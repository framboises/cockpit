"""RETEX automatique de fin d'edition.

`build(db, event, year)` assemble TOUT ce que Cockpit sait d'une edition,
en reutilisant les calculs existants plutot qu'en les re-ecrivant :

  - main courante : pcorg_summary.compute_kpis sur toute l'edition, plus un
    detail par jour / heure / duree (calcul pur, `pcorg_stats`) ;
  - comparaison avec les 2 editions precedentes du meme evenement, alignees
    sur le jour de course ;
  - frequentation : scan_frequentation.build_frequentation_block avec
    source_priority=LIVE_FIRST -- archive du controle d'acces live d'abord
    (live_frequentation : rejeu exact du dashboard), import Excel en repli,
    edition par edition ; deja aligne sur le jour de course, deja conscient
    des editions non comparables, reduit par
    scan_analysis.build_frequentation_prompt_payload ;
  - centre d'alertes (cockpit_alert_history, TTL 7 jours : couverture
    signalee), SOS Field, meteo (donnees_meteo), billetterie vs pic.

Chaque bloc est construit independamment (situation_briefing._run_source) :
un bloc en echec est marque indisponible, jamais bloquant.

Puis un appel Claude redige un rapport long en francais (contrat JSON strict
SECTION_KEYS), persiste VERSIONNE dans `edition_retex` (jamais ecrase), et
rendu en page HTML autonome imprimable (`render_html`).

Pas de Flask ni d'`app` : `db` toujours passe en argument.
"""

import html
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

from situation_briefing import (TZ_PARIS, as_utc, jsonable, llm_call, paris_str,
                                record_usage, _run_source, _trunc)

logger = logging.getLogger(__name__)

COLLECTION = "edition_retex"
RETEX_MAX_TOKENS = int(os.getenv("RETEX_MAX_TOKENS", "24000"))
MAX_PREV_EDITIONS = 2
MAX_MAJORS = 40
STATUT_CLOS = 10
MAJOR_URGENCIES = ("EU", "UA")

SECTION_KEYS = (
    "synthese",
    "chronologie",
    "secours",
    "securite",
    "flux_et_acces",
    "frequentation",
    "technique",
    "alertes_et_reactivite",
    "comparaison_editions",
    "points_forts",
    "points_amelioration",
    "recommandations_prochaine_edition",
)
SECTION_LABELS = {
    "synthese": "Synthese",
    "chronologie": "Chronologie",
    "secours": "Secours",
    "securite": "Securite et surete",
    "flux_et_acces": "Flux et acces",
    "frequentation": "Frequentation",
    "technique": "Technique",
    "alertes_et_reactivite": "Alertes et reactivite",
    "comparaison_editions": "Comparaison avec les editions precedentes",
    "points_forts": "Points forts",
    "points_amelioration": "Points d'amelioration",
    "recommandations_prochaine_edition": "Recommandations pour la prochaine edition",
}
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def _ps():
    import pcorg_summary
    return pcorg_summary


# ---------------------------------------------------------------------------
# Edition : dates, fenetre
# ---------------------------------------------------------------------------

def _param_doc(db, event, year):
    return (db["parametrages"].find_one({"event": event, "year": str(year)})
            or db["parametrages"].find_one({"event": event, "year": int(year)}) or {})


def _parse_dt(raw):
    fn = getattr(_ps(), "_parse_iso_dt", None)
    if fn is not None:
        return fn(raw)
    if not raw:
        return None
    try:
        dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(
            str(raw).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_PARIS)
    return dt.astimezone(timezone.utc)


def race_date(db, event, year, param=None):
    """Date (Paris) de la course : chaine de repli de pcorg_summary._load_race_dt."""
    fn = getattr(_ps(), "_load_race_dt", None)
    if fn is not None:
        try:
            dt = fn(db, event, int(year))
            if dt:
                return dt.astimezone(TZ_PARIS).date()
        except Exception as exc:
            logger.warning("retex : _load_race_dt(%s, %s) : %s", event, year, exc)
    data = (param if param is not None else _param_doc(db, event, year)).get("data") or {}
    dt = _parse_dt(data.get("race") or (data.get("globalHoraires") or {}).get("race"))
    return dt.astimezone(TZ_PARIS).date() if dt else None


def edition_info(db, event, year, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    param = _param_doc(db, event, year)
    gh = (param.get("data") or {}).get("globalHoraires") or {}
    public_days = sorted({(d.get("date") if isinstance(d, dict) else str(d))[:10]
                          for d in gh.get("dates") or [] if d})
    start = _parse_dt((gh.get("montage") or {}).get("start"))
    end = _parse_dt((gh.get("demontage") or {}).get("end"))
    if start is None or end is None:
        col = db["pcorg"]
        base = {"event": event, "year": int(year)}
        first = col.find_one(base, sort=[("ts", 1)])
        last = col.find_one(base, sort=[("ts", -1)])
        start = start or (as_utc(first["ts"]) if first else None)
        end = end or (as_utc(last["ts"]) if last else None)
    if end is not None and end > now_utc:
        end = now_utc
    rd = race_date(db, event, year, param)
    return {
        "event": event,
        "year": int(year),
        "race_date": rd.isoformat() if rd else None,
        "public_days": public_days,
        "window_start": start,
        "window_end": end,
        "has_param": bool(param),
    }


# ---------------------------------------------------------------------------
# Main courante (calcul pur)
# ---------------------------------------------------------------------------

_FICHE_PROJ = {"_id": 1, "ts": 1, "close_ts": 1, "status_code": 1, "category": 1,
               "niveau_urgence": 1, "is_incident": 1, "area": 1, "text": 1,
               "content_category": 1, "operator": 1, "sql_id": 1}


def load_fiches(db, event, year):
    return list(db["pcorg"].find({"event": event, "year": int(year)}, _FICHE_PROJ))


def _duration_min(f):
    ts, cl = f.get("ts"), f.get("close_ts")
    if f.get("status_code") != STATUT_CLOS or not isinstance(ts, datetime) or not isinstance(cl, datetime):
        return None
    # close_ts vaut 9000-01-01 sur les fiches Prysm ouvertes : sentinelle.
    if cl.year >= 9000:
        return None
    d = (as_utc(cl) - as_utc(ts)).total_seconds() / 60.0
    if d < 0 or d > 7 * 24 * 60:
        return None
    return d


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return round(s[k], 1)


def _counter(items):
    out = {}
    for k in items:
        k = str(k) if k not in (None, "") else "_aucun"
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def pcorg_stats(fiches, race_day=None, detail=True):
    """Statistiques d'une edition a partir de ses fiches (pur, testable).

    race_day : date de course (Paris) pour l'offset J-x / J+x de chaque jour.
    detail=False : version reduite pour les editions de comparaison.
    """
    by_day = {}
    hours = [0] * 24
    durations_by_cat = {}
    majors = []
    for f in fiches:
        ts = f.get("ts")
        if not isinstance(ts, datetime):
            continue
        tp = as_utc(ts).astimezone(TZ_PARIS)
        dkey = tp.strftime("%Y-%m-%d")
        day = by_day.setdefault(dkey, {"total": 0, "majeures": 0, "par_categorie": {}})
        day["total"] += 1
        cat = f.get("category") or "_aucun"
        day["par_categorie"][cat] = day["par_categorie"].get(cat, 0) + 1
        major = (f.get("niveau_urgence") in MAJOR_URGENCIES) or bool(f.get("is_incident"))
        if major:
            day["majeures"] += 1
            majors.append(f)
        hours[tp.hour] += 1
        d = _duration_min(f)
        if d is not None:
            durations_by_cat.setdefault(cat, []).append(d)

    days = []
    for dkey in sorted(by_day):
        dd = datetime.strptime(dkey, "%Y-%m-%d").date()
        row = {"date": dkey, "jour": JOURS[dd.weekday()],
               "offset": (dd - race_day).days if race_day else None, **by_day[dkey]}
        if not detail:
            row.pop("par_categorie", None)
        days.append(row)

    all_d = [d for v in durations_by_cat.values() for d in v]
    out = {
        "total": sum(1 for f in fiches if isinstance(f.get("ts"), datetime)),
        "ouvertes_restantes": sum(1 for f in fiches if f.get("status_code") != STATUT_CLOS),
        "par_categorie": _counter(f.get("category") for f in fiches),
        "par_urgence": _counter(f.get("niveau_urgence") for f in fiches),
        "majeures": len(majors),
        "sos_tablette": sum(1 for f in fiches if (f.get("content_category") or {}).get("field_sos")),
        "duree_min": {"mediane": _pct(all_d, 0.5), "p90": _pct(all_d, 0.9), "n": len(all_d)},
        "par_jour": days,
    }
    if detail:
        out["par_heure"] = {"%02dh" % h: n for h, n in enumerate(hours) if n}
        out["duree_min_par_categorie"] = {
            cat: {"mediane": _pct(v, 0.5), "p90": _pct(v, 0.9), "n": len(v)}
            for cat, v in sorted(durations_by_cat.items(), key=lambda kv: -len(kv[1]))}
        out["top_zones"] = list(_counter(((f.get("area") or {}).get("desc")) for f in fiches
                                         if (f.get("area") or {}).get("desc")).items())[:12]
        out["top_sous_classifications"] = list(_counter(
            (f.get("content_category") or {}).get("sous_classification") for f in fiches
            if (f.get("content_category") or {}).get("sous_classification")).items())[:12]
        majors.sort(key=lambda f: as_utc(f["ts"]))
        out["fiches_majeures"] = [{
            "quand": paris_str(f.get("ts"), "%d/%m %H:%M"),
            "offset": ((as_utc(f["ts"]).astimezone(TZ_PARIS).date() - race_day).days
                       if race_day else None),
            "categorie": f.get("category"),
            "urgence": f.get("niveau_urgence"),
            "incident": bool(f.get("is_incident")),
            "zone": (f.get("area") or {}).get("desc"),
            "sous_classification": (f.get("content_category") or {}).get("sous_classification"),
            "texte": _trunc(f.get("text"), 300),
            "duree_min": (round(_duration_min(f)) if _duration_min(f) is not None else None),
            "close": f.get("status_code") == STATUT_CLOS,
        } for f in majors[:MAX_MAJORS]]
        out["fiches_majeures_non_listees"] = max(0, len(majors) - MAX_MAJORS)
    return out


def block_main_courante(db, event, year, race_day):
    fiches = load_fiches(db, event, year)
    if not fiches:
        raise ValueError("aucune fiche main courante pour cette edition")
    stats = pcorg_stats(fiches, race_day, detail=True)
    try:
        kpis = _ps().compute_kpis(db, event, year,
                                  datetime(2000, 1, 1, tzinfo=timezone.utc),
                                  datetime(2100, 1, 1, tzinfo=timezone.utc))
        stats["kpis_cockpit"] = {k: kpis.get(k) for k in (
            "total", "open", "closed", "avg_duration_min", "top_operators")}
    except Exception as exc:
        stats["kpis_cockpit_erreur"] = str(exc)[:200]
    return stats


def previous_years(db, event, year, limit=MAX_PREV_EDITIONS):
    years = []
    for y in db["pcorg"].distinct("year", {"event": event}):
        try:
            y = int(y)
        except (TypeError, ValueError):
            continue
        if y < int(year):
            years.append(y)
    return sorted(set(years), reverse=True)[:limit]


def block_comparaison(db, event, year):
    out = []
    for y in previous_years(db, event, year):
        rd = race_date(db, event, y)
        st = pcorg_stats(load_fiches(db, event, y), rd, detail=False)
        out.append({"annee": y, "date_course": rd.isoformat() if rd else None,
                    "total": st["total"], "majeures": st["majeures"],
                    "par_categorie": st["par_categorie"], "par_urgence": st["par_urgence"],
                    "duree_min": st["duree_min"], "sos_tablette": st["sos_tablette"],
                    "par_jour": [{"offset": d["offset"], "jour": d["jour"], "total": d["total"],
                                  "majeures": d["majeures"]} for d in st["par_jour"]]})
    if not out:
        raise ValueError("aucune edition precedente de cet evenement dans la main courante")
    return {"editions": out}


# ---------------------------------------------------------------------------
# Frequentation, presents, meteo, billetterie
# ---------------------------------------------------------------------------

SOURCE_LABELS = {
    "live_controle": "controle d'acces live (compteur Area, presents = solde - vehicules)",
    "scan_import": "import Excel des scans (somme des portes du classeur)",
    "collecte_temps_reel": "ancienne collecte temps reel (somme des portes)",
}


def block_frequentation(db, event, year):
    """Frequentation de l'edition et des 2 precedentes, SOURCE PAR SOURCE :
    archive du controle d'acces live d'abord (live_frequentation, rejeu exact
    du dashboard), import Excel (historique_controle) en repli -- chaque
    edition prend la premiere source disponible."""
    import scan_analysis
    import scan_frequentation
    block = scan_frequentation.build_frequentation_block(
        db, event, int(year), source_priority=scan_frequentation.LIVE_FIRST)
    if not block:
        raise ValueError("aucune serie de frequentation (ni archive du controle d'acces live, "
                         "ni import historique_controle) pour cette edition")
    payload = scan_analysis.build_frequentation_prompt_payload(block)
    payload["pic_edition"] = (block.get("totals") or {}).get("peak_present")
    payload["jours_mesures"] = (block.get("totals") or {}).get("days_measured")
    payload["sources_par_edition"] = {str(e.get("year")): e.get("source")
                                      for e in block.get("editions") or []}
    return payload


def block_presents_live(db, event, year, public_days):
    """Pics de presents par jour quand la frequentation est indisponible :
    presents du dashboard (presents_etat) tant que le live-controle compte
    l'edition. PLUS de repli sur le max brut du compteur archive
    (pcorg_summary._get_pic_observed_for_day) : il ne retire ni les vehicules
    ni les valeurs fantomes d'avant remise a zero (24H AUTOS 2026 : 148 919
    brut pour 141 777 presents). Une archive live est deja lue, rejouee, par
    block_frequentation."""
    import presents_etat
    g = presents_etat.read_global(db)
    if not presents_etat.edition_en_direct(g, event):
        raise ValueError("aucun releve de presents exploitable (edition ni comptee en direct, "
                         "ni archivee par le controle d'acces live)")
    loc = presents_etat.principal_location(g)
    rows = []
    for d in public_days:
        jd = datetime.strptime(d, "%Y-%m-%d").date()
        pic, heure = None, None
        if loc is not None:
            d0, d1 = presents_etat.paris_day_bounds_utc(jd)
            pic, inst = presents_etat.pic_presents(db, loc, d0, d1, g)
            if pic is not None:
                heure = presents_etat.to_tranche_label(inst).strftime("%Hh%M")
        rows.append({"date": d, "pic_presents": pic, "heure": heure,
                     "source": "presents_dashboard" if pic is not None else None})
    if not any(r["pic_presents"] for r in rows):
        raise ValueError("aucun releve de presents pour les jours publics")
    return {"par_jour": rows,
            "note": "presents du dashboard (compteur principal moins vehicules), "
                    "plus haut releve du jour"}


def block_meteo(db, public_days, race_day):
    import scan_frequentation
    dates = set(public_days or [])
    if race_day:
        for k in (-1, 0, 1):
            dates.add((race_day + timedelta(days=k)).isoformat())
    if not dates:
        raise ValueError("aucune date d'edition connue")
    w = scan_frequentation.load_weather(db, sorted(dates))
    if not w:
        raise ValueError("aucune donnee donnees_meteo pour ces dates")
    return {"par_jour": [{"date": d, "t_max": (w.get(d) or {}).get("tmax"),
                          "t_min": (w.get(d) or {}).get("tmin"),
                          "pluie_mm": (w.get(d) or {}).get("rain"),
                          "soleil_h": (w.get(d) or {}).get("sun")} for d in sorted(dates)]}


def _basket_total(param):
    gh = (param.get("data") or {}).get("globalHoraires") or {}
    names = {p for tc in gh.get("ticketing") or [] for p in (tc.get("products") or []) if p}
    prods = (param.get("tickets") or {}).get("products") or {}
    return sum(int((prods.get(n) or {}).get("ventes") or 0) for n in names), len(names)


def block_billetterie(db, event, year, public_days):
    """Billets du panier `globalHoraires.ticketing` (seul perimetre comparable,
    cf. CLAUDE.md << Affluence >>), total et par jour public."""
    param = _param_doc(db, event, year)
    if not param:
        raise ValueError("aucun parametrages pour cette edition")
    gh = (param.get("data") or {}).get("globalHoraires") or {}
    ticketing = gh.get("ticketing") or []
    if not ticketing:
        raise ValueError("aucun panier billetterie (globalHoraires.ticketing vide)")
    prods = (param.get("tickets") or {}).get("products") or {}
    day_fn = getattr(_ps(), "_day_ventes", None)
    total, n_products = _basket_total(param)
    par_jour = []
    for d in public_days:
        v = day_fn(ticketing, prods, d) if day_fn else None
        par_jour.append({"date": d, "billets_donnant_acces": v})
    prev = None
    for y in range(int(year) - 1, int(year) - 4, -1):
        p = _param_doc(db, event, y)
        if p and ((p.get("data") or {}).get("globalHoraires") or {}).get("ticketing"):
            t, n = _basket_total(p)
            prev = {"annee": y, "billets_panier": t, "produits_panier": n}
            break
    return {
        "billets_panier": total,
        "produits_panier": n_products,
        "derniere_maj_billetterie": (param.get("tickets") or {}).get("lastUpdate"),
        "par_jour_public": par_jour,
        "edition_precedente": prev,
        "note": "un billet multi-jours compte sur chaque jour d'acces ; les noms de "
                "produits changent d'une edition a l'autre, seules les sommes se comparent",
    }


# ---------------------------------------------------------------------------
# Alertes et Field
# ---------------------------------------------------------------------------

def block_alertes(db, start, end):
    if start is None or end is None:
        raise ValueError("fenetre de l'edition inconnue")
    names = {d.get("slug"): d.get("name") or d.get("slug")
             for d in db["cockpit_alert_definitions"].find({}, {"slug": 1, "name": 1})
             if d.get("slug")}
    hist = list(db["cockpit_alert_history"].find({"createdAt": {"$gte": start, "$lte": end}}))
    oldest = db["cockpit_alert_history"].find_one({}, sort=[("createdAt", 1)])
    oldest_at = as_utc(oldest["createdAt"]) if oldest and oldest.get("createdAt") else None
    if not hist and (oldest_at is None or oldest_at > end):
        # Historique conserve 7 jours (TTL) : une edition plus ancienne n'y a
        # plus rien. Zero alerte serait un mensonge, pas un constat.
        raise ValueError("historique des alertes expire (conserve 7 jours) pour cette edition")
    par_type, par_jour = {}, {}
    for h in hist:
        label = names.get(h.get("type") or "", h.get("type") or "?")
        par_type[label] = par_type.get(label, 0) + 1
        dkey = paris_str(h.get("createdAt"), "%Y-%m-%d")
        par_jour[dkey] = par_jour.get(dkey, 0) + 1
    # Prise en charge : seul cockpit_active_alerts porte taken_at, et ces
    # documents expirent (TTL). Rarement disponible apres coup.
    reactivite = []
    for a in db["cockpit_active_alerts"].find(
            {"triggeredAt": {"$gte": start, "$lte": end}, "taken_at": {"$exists": True}}):
        if isinstance(a.get("taken_at"), datetime) and isinstance(a.get("triggeredAt"), datetime):
            reactivite.append({
                "type": names.get(a.get("definition_slug"), a.get("definition_slug")),
                "delai_s": int((as_utc(a["taken_at"]) - as_utc(a["triggeredAt"])).total_seconds()),
            })
    out = {
        "total": len(hist),
        "par_type": dict(sorted(par_type.items(), key=lambda kv: -kv[1])),
        "par_jour": dict(sorted(par_jour.items())),
        "couverture_partielle": bool(oldest_at and oldest_at > start),
        "historique_disponible_depuis": paris_str(oldest_at) if oldest_at else None,
    }
    if reactivite:
        delais = [r["delai_s"] for r in reactivite]
        out["prise_en_charge"] = {"n": len(delais), "mediane_s": _pct(delais, 0.5),
                                  "max_s": max(delais), "detail": reactivite[:30]}
    else:
        out["prise_en_charge"] = ("indisponible : la prise en charge n'est portee que par les "
                                  "alertes actives, qui expirent")
    return out


def block_field(db, event, year, race_day):
    sos = list(db["pcorg"].find({"event": event, "year": int(year),
                                 "content_category.field_sos": True}, _FICHE_PROJ))
    devices = list(db["field_devices"].find({"event": event, "year": str(year)},
                                            {"name": 1, "revoked": 1}))
    return {
        "tablettes_enrolees": len(devices),
        "sos": len(sos),
        "sos_detail": [{
            "quand": paris_str(f.get("ts"), "%d/%m %H:%M"),
            "zone": (f.get("area") or {}).get("desc"),
            "texte": _trunc(f.get("text"), 160),
            "duree_min": round(_duration_min(f)) if _duration_min(f) is not None else None,
        } for f in sorted(sos, key=lambda f: as_utc(f["ts"]) if isinstance(f.get("ts"), datetime)
                          else datetime.min.replace(tzinfo=timezone.utc))[:30]],
    }


# ---------------------------------------------------------------------------
# Assemblage
# ---------------------------------------------------------------------------

BLOCK_LABELS = {
    "main_courante": "Main courante",
    "comparaison_editions": "Comparaison editions",
    "frequentation": "Frequentation",
    "presents_live": "Presents (dashboard live)",
    "alertes": "Centre d'alertes",
    "field": "Tablettes Field / SOS",
    "meteo": "Meteo",
    "billetterie": "Billetterie",
}


def _caveats(dataset):
    notes = [
        "Le pic de presents est le KPI de reference de la frequentation ; les totaux "
        "d'entrees mesurent le dispositif (nombre de portes), pas la foule.",
        "Le volume de fiches main courante depend aussi des pratiques de saisie "
        "(Prysm SQL, Cockpit, tablettes) : un ecart entre editions n'est pas forcement "
        "un ecart d'activite.",
    ]
    fr = dataset["blocs"].get("frequentation") or {}
    if fr.get("statut") == "ok":
        d = fr["data"]
        if d.get("perimetre_comparable") is False:
            notes.append("Entrees NON comparables entre editions : nombre de portes en service "
                         "different (%s)." % json.dumps(d.get("portes_en_service_par_edition"),
                                                        ensure_ascii=False))
        eds = d.get("editions") or []
        sources = {e.get("source") for e in eds if e.get("source")}
        if len(sources) > 1:
            detail = ", ".join("%s : %s" % (e.get("annee"), e.get("source")) if e.get("annee")
                               else str(e.get("source")) for e in eds if e.get("source"))
            notes.append("Les editions comparees n'ont pas la meme source de mesure (%s) : "
                         "perimetres et methodes differents, tout ecart peut venir du "
                         "changement de mesure." % detail)
        if "live_controle" in sources:
            notes.append("Frequentation << live_controle >> : rejeu des releves du compteur Area "
                         "ENCEINTE GENERALE (toutes les ~3 min) comme le dashboard -- presents = "
                         "solde du compteur moins les vehicules (enfants et accredites comptes), "
                         "pic = plus haut releve du jour. Les sorties etant sous-scannees, le solde "
                         "derive vers le haut en fin d'evenement.")
        for e in eds:
            s0 = e.get("solde_initial_compteur")
            pic = max([j.get("presents_pic") or 0 for j in e.get("jours") or []] or [0])
            if (e.get("source") == "live_controle" and s0 and pic
                    and not e.get("remise_a_zero_observee") and s0 > 0.02 * pic):
                notes.append("Edition %s : le compteur affichait deja %s presents au premier "
                             "releve (%s), sans remise a zero observee -- reliquat probable des "
                             "evenements precedents, qui majore d'autant tous les presents de "
                             "l'edition (pic %s)." % (e.get("annee"), s0,
                                                       e.get("premier_releve"), pic))
    al = dataset["blocs"].get("alertes") or {}
    if al.get("statut") == "ok" and al["data"].get("couverture_partielle"):
        notes.append("Historique des alertes partiel : conserve 7 jours, disponible depuis le %s."
                     % al["data"].get("historique_disponible_depuis"))
    bi = dataset["blocs"].get("billetterie") or {}
    if bi.get("statut") == "ok":
        notes.append("Billetterie : seul le panier globalHoraires.ticketing est compte ; "
                     "les noms de produits changent entre editions, seules les sommes se comparent.")
    return notes


def build(db, event, year, now_utc=None):
    """Jeu de donnees complet de l'edition. Ne leve pas pour un bloc."""
    t0 = time.perf_counter()
    year = int(year)
    info = edition_info(db, event, year, now_utc)
    rd = date.fromisoformat(info["race_date"]) if info["race_date"] else None
    blocs = {
        "main_courante": _run_source("main_courante", block_main_courante, db, event, year, rd),
        "comparaison_editions": _run_source("comparaison_editions", block_comparaison,
                                            db, event, year),
        "frequentation": _run_source("frequentation", block_frequentation, db, event, year),
        "alertes": _run_source("alertes", block_alertes, db, info["window_start"],
                               info["window_end"]),
        "field": _run_source("field", block_field, db, event, year, rd),
        "meteo": _run_source("meteo", block_meteo, db, info["public_days"], rd),
        "billetterie": _run_source("billetterie", block_billetterie, db, event, year,
                                   info["public_days"]),
    }
    if blocs["frequentation"]["statut"] != "ok" and info["public_days"]:
        blocs["presents_live"] = _run_source("presents_live", block_presents_live,
                                             db, event, year, info["public_days"])

    # Rapprochement billets / pic par jour : le pic vient de la frequentation
    # (ou du live), les billets du panier. Un ratio, pas une projection.
    try:
        if blocs["billetterie"]["statut"] == "ok":
            pics = {}
            if blocs["frequentation"]["statut"] == "ok":
                for ed in blocs["frequentation"]["data"].get("editions") or []:
                    if ed.get("edition_analysee"):
                        for j in ed.get("jours") or []:
                            if j.get("mesure"):
                                pics[j.get("date")] = j.get("presents_pic")
            elif (blocs.get("presents_live") or {}).get("statut") == "ok":
                for r in blocs["presents_live"]["data"]["par_jour"]:
                    pics[r["date"]] = r["pic_presents"]
            for row in blocs["billetterie"]["data"]["par_jour_public"]:
                pic = pics.get(row["date"])
                row["pic_presents"] = pic
                b = row.get("billets_donnant_acces")
                row["ratio_pic_sur_billets"] = round(pic / b, 3) if pic and b else None
    except Exception as exc:
        logger.warning("retex : rapprochement billets/pic : %s", exc)

    dataset = {
        "edition": jsonable({k: v for k, v in info.items()}),
        "blocs": blocs,
    }
    dataset["avertissements"] = _caveats(dataset)
    dataset["blocs_indisponibles"] = [n for n, b in blocs.items() if b["statut"] != "ok"]
    dataset["duree_ms"] = int(round((time.perf_counter() - t0) * 1000))
    return dataset


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Tu es charge du RETEX (retour d'experience) de fin d'edition pour la direction des operations du circuit du Mans (Automobile Club de l'Ouest). Tu rediges, en francais, un rapport complet et structure, destine a etre imprime et discute en reunion de debriefing.

REGLES D'ANCRAGE (non negociables) :
- Tu n'utilises QUE les donnees JSON fournies. Aucun chiffre, lieu, nom, horaire ou fait qui n'y figure pas. Pas de generalites sur l'evenementiel.
- Un bloc dont "statut" vaut "indisponible" : dis-le explicitement dans la section concernee ("donnee indisponible : <raison>") et n'en tire aucune conclusion.
- Respecte les "avertissements" fournis : ils disent ce qui n'est PAS comparable. Le pic de presents est le KPI de reference de la frequentation ; ne commente pas les ecarts de totaux d'entrees entre editions quand le perimetre de mesure (nombre de portes) a change. Un ecart entre editions de sources de mesure differentes est a presenter comme potentiellement lie au changement de mesure.
- Les jours sont reperes par leur decalage au jour de course (offset : J-2, J, J+1) ET leur jour de semaine ; les editions se comparent au meme offset, jamais a la meme date calendaire.
- Urgences : EU = detresse vitale, UA = urgence absolue, UR = urgence relative, IMP = implique. Les fiches "majeures" sont EU, UA ou marquees incident.
- Chiffre ce que tu affirmes. Distingue constat (donnee) et interpretation (hypothese, a formuler comme telle).
- **Gras** pour les chiffres cles. Pas de formule de politesse.

FORMAT DE SORTIE : un objet JSON strict, sans texte autour, avec exactement ces 12 cles, chaque valeur etant une chaine (markdown leger : paragraphes, puces "- ", **gras**) :
- "synthese" : 8 a 12 lignes, l'edition en un coup d'oeil (volume d'activite, faits majeurs, pic de presents, meteo, comparaison).
- "chronologie" : puces "- " par jour (offset + jour de semaine) : volume, faits majeurs.
- "secours" : activite secours (PCO.Secours), urgences EU/UA, SOS tablettes, durees.
- "securite" : securite et surete (PCO.Securite, PCS.*).
- "flux_et_acces" : flux, fourriere, acces, controle d'acces.
- "frequentation" : pics par jour et heure, comparaison au meme offset, lien billetterie / pic, perte de controle d'acces si fournie. Nomme la source de mesure de chaque edition (champ "source" : live_controle = compteur du controle d'acces live, scan_import = import Excel des scans) ; un jour "mesure": false n'est jamais une frequentation nulle.
- "technique" : PCO.Technique et main courante technique.
- "alertes_et_reactivite" : alertes par type, reactivite si disponible, limites de couverture.
- "comparaison_editions" : ecarts avec les editions precedentes, avec les reserves de comparabilite. Si aucune edition comparable : "Aucune edition precedente comparable dans les donnees fournies."
- "points_forts" : puces "- ".
- "points_amelioration" : puces "- ".
- "recommandations_prochaine_edition" : puces "- " concretes et priorisees (dimensionnement, horaires, zones), chacune reliee a un constat chiffre."""


def build_prompts(dataset):
    payload = {
        "edition": dataset.get("edition"),
        "avertissements": dataset.get("avertissements"),
        "blocs": {n: ({"statut": "ok", "donnees": b.get("data")} if b["statut"] == "ok"
                      else {"statut": "indisponible", "raison": b.get("erreur")})
                  for n, b in (dataset.get("blocs") or {}).items()},
    }
    ed = dataset.get("edition") or {}
    user = ("Donnees de l'edition %s %s (date de course : %s).\n\n```json\n%s\n```\n\n"
            "Redige le RETEX au format JSON demande."
            % (ed.get("event"), ed.get("year"), ed.get("race_date"),
               json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
    return SYSTEM_PROMPT, user


def dry_run(db, event, year):
    t0 = time.perf_counter()
    dataset = build(db, event, year)
    system, user = build_prompts(dataset)
    chars = len(system) + len(user)
    return {
        "dry_run": True,
        "event": event,
        "year": int(year),
        "build_ms": int(round((time.perf_counter() - t0) * 1000)),
        "blocs": {n: {"statut": b["statut"], "ms": b.get("ms"), "erreur": b.get("erreur"),
                      "chars": len(json.dumps(b.get("data"), ensure_ascii=False))
                      if b["statut"] == "ok" else 0}
                  for n, b in dataset["blocs"].items()},
        "avertissements": dataset["avertissements"],
        "prompt_chars": chars,
        # ~3,5 caracteres par token en francais JSON : ordre de grandeur, pas une facture.
        "tokens_estimes": int(chars / 3.5),
        "max_tokens_sortie": RETEX_MAX_TOKENS,
        "system": system,
        "user": user,
        "dataset": dataset,
    }


def generate(db, event, year, by=None, model=None, on_progress=None, dataset=None):
    """Construit (si besoin), appelle Claude, persiste une NOUVELLE version."""
    dataset = dataset or build(db, event, year)
    system, user = build_prompts(dataset)
    sections, raw_text, usage, meta = llm_call(
        db, system, user, SECTION_KEYS, model=model, on_progress=on_progress,
        max_tokens=RETEX_MAX_TOKENS)
    year = int(year)
    version = db[COLLECTION].count_documents({"event": event, "year": year}) + 1
    doc = {
        "event": event,
        "year": year,
        "version": version,
        "created_at": datetime.now(timezone.utc),
        "created_by": by or "",
        "model": meta["model"],
        "sections": sections,
        "raw_text": None if sections else raw_text,
        "truncated": bool(meta.get("truncated")),
        "usage": usage,
        "prompt_chars": len(system) + len(user),
        "dataset": dataset,
    }
    res = db[COLLECTION].insert_one(doc)
    doc["_id"] = res.inserted_id
    _ensure_indexes(db)
    record_usage(db, "edition_retex", meta["model"], usage,
                 meta={"event": event, "year": year, "version": version,
                       "retex_id": str(res.inserted_id)})
    return doc


_indexes_ok = False


def _ensure_indexes(db):
    global _indexes_ok
    if _indexes_ok:
        return
    try:
        db[COLLECTION].create_index([("event", 1), ("year", 1), ("version", -1)],
                                    name="event_year_version")
        db[COLLECTION].create_index([("created_at", -1)], name="created_desc")
        _indexes_ok = True
    except Exception as exc:
        logger.warning("index %s : %s", COLLECTION, exc)


def serialize(doc, light=True):
    if not doc:
        return None
    out = {
        "id": str(doc.get("_id")),
        "event": doc.get("event"),
        "year": doc.get("year"),
        "version": doc.get("version"),
        "created_at": paris_str(doc.get("created_at")),
        "created_by": doc.get("created_by"),
        "model": doc.get("model"),
        "usage": doc.get("usage"),
        "truncated": doc.get("truncated"),
        "parsed": bool(doc.get("sections")),
    }
    if not light:
        out["sections"] = doc.get("sections")
        out["raw_text"] = doc.get("raw_text")
        out["avertissements"] = (doc.get("dataset") or {}).get("avertissements")
        out["blocs_indisponibles"] = (doc.get("dataset") or {}).get("blocs_indisponibles")
    return out


def list_retex(db, event=None, year=None, limit=50):
    q = {}
    if event:
        q["event"] = event
    if year:
        try:
            q["year"] = int(year)
        except (TypeError, ValueError):
            pass
    cur = db[COLLECTION].find(q, {"dataset": 0, "raw_text": 0, "sections": 0}).sort(
        "created_at", -1).limit(int(limit))
    return [serialize(d) for d in cur]


def get_retex(db, retex_id):
    from bson.objectid import ObjectId
    try:
        oid = ObjectId(str(retex_id))
    except Exception:
        return None
    return db[COLLECTION].find_one({"_id": oid})


def list_editions(db):
    """(event, year) disponibles dans parametrages, plus recentes d'abord."""
    out = []
    for p in db["parametrages"].find({}, {"event": 1, "year": 1}):
        ev, yr = p.get("event"), p.get("year")
        if not ev or str(ev).startswith("__"):
            continue
        try:
            out.append({"event": ev, "year": int(yr)})
        except (TypeError, ValueError):
            continue
    uniq = {(e["event"], e["year"]): e for e in out}
    return sorted(uniq.values(), key=lambda e: (-e["year"], e["event"]))


# ---------------------------------------------------------------------------
# Page HTML autonome (impression / PDF depuis le navigateur)
# ---------------------------------------------------------------------------

def _e(v):
    return html.escape("-" if v is None else str(v))


def _md(text):
    """Markdown leger -> HTML, texte ECHAPPE d'abord (aucune injection)."""
    import re
    if not text:
        return '<p class="muted">Non renseigne.</p>'
    out, items = [], []

    def inline(s):
        return re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", html.escape(s))

    def flush():
        if items:
            out.append("<ul>" + "".join("<li>" + inline(i) + "</li>" for i in items) + "</ul>")
            items.clear()

    para = []
    for line in str(text).replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith("- ") or s.startswith("* "):
            if para:
                out.append("<p>" + "<br>".join(inline(p) for p in para) + "</p>")
                para = []
            items.append(s[2:].strip())
        elif not s:
            flush()
            if para:
                out.append("<p>" + "<br>".join(inline(p) for p in para) + "</p>")
                para = []
        else:
            flush()
            para.append(s)
    flush()
    if para:
        out.append("<p>" + "<br>".join(inline(p) for p in para) + "</p>")
    return "".join(out)


def _table(headers, rows):
    if not rows:
        return '<p class="muted">Aucune donnee.</p>'
    return ("<table><thead><tr>" + "".join("<th>" + _e(h) + "</th>" for h in headers)
            + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join("<td>" + _e(c) + "</td>" for c in r) + "</tr>" for r in rows)
            + "</tbody></table>")


def _offset_label(o):
    if o is None:
        return "-"
    return "J" if o == 0 else ("J%+d" % o)


def _annexes(dataset):
    parts = []
    blocs = dataset.get("blocs") or {}
    mc = blocs.get("main_courante") or {}
    if mc.get("statut") == "ok":
        d = mc["data"]
        parts.append("<h3>Main courante par jour</h3>")
        parts.append(_table(["Date", "Jour", "Offset", "Fiches", "Majeures"],
                            [[r["date"], r["jour"], _offset_label(r.get("offset")), r["total"],
                              r["majeures"]] for r in d.get("par_jour") or []]))
        parts.append("<h3>Par categorie</h3>")
        dur = d.get("duree_min_par_categorie") or {}
        parts.append(_table(["Categorie", "Fiches", "Duree mediane (min)", "Duree p90 (min)"],
                            [[c, n, (dur.get(c) or {}).get("mediane"), (dur.get(c) or {}).get("p90")]
                             for c, n in (d.get("par_categorie") or {}).items()]))
        parts.append("<h3>Par niveau d'urgence</h3>")
        parts.append(_table(["Urgence", "Fiches"], list((d.get("par_urgence") or {}).items())))
    cmp_ = blocs.get("comparaison_editions") or {}
    if cmp_.get("statut") == "ok" and mc.get("statut") == "ok":
        cur = mc["data"]
        rows = [[(dataset.get("edition") or {}).get("year"), cur.get("total"), cur.get("majeures"),
                 cur.get("sos_tablette"), (cur.get("duree_min") or {}).get("mediane")]]
        for e in cmp_["data"]["editions"]:
            rows.append([e["annee"], e["total"], e["majeures"], e["sos_tablette"],
                         (e.get("duree_min") or {}).get("mediane")])
        parts.append("<h3>Comparaison main courante</h3>")
        parts.append(_table(["Edition", "Fiches", "Majeures", "SOS tablette", "Duree mediane (min)"],
                            rows))
    fr = blocs.get("frequentation") or {}
    if fr.get("statut") == "ok":
        parts.append("<h3>Frequentation (pic de presents par jour)</h3>")
        eds = fr["data"].get("editions") or []
        parts.append(_table(["Edition", "Source de mesure"],
                            [[ed.get("annee"), SOURCE_LABELS.get(ed.get("source"),
                                                                 ed.get("source") or "inconnue")]
                             for ed in eds]))
        rows = []
        for ed in eds:
            for j in ed.get("jours") or []:
                rows.append([ed.get("annee"), ed.get("source"), _offset_label(j.get("jour")),
                             j.get("date"),
                             j.get("presents_pic") if j.get("mesure") else
                             "non mesure" + (" (%s)" % j["raison_non_mesure"].replace("_", " ")
                                             if j.get("raison_non_mesure") else ""),
                             j.get("heure_pic"), j.get("entrees")])
        parts.append(_table(["Edition", "Source", "Offset", "Date", "Pic presents", "Heure",
                             "Entrees"], rows))
    bi = blocs.get("billetterie") or {}
    if bi.get("statut") == "ok":
        parts.append("<h3>Billetterie et pic</h3>")
        parts.append(_table(["Jour public", "Billets donnant acces", "Pic presents", "Pic / billets"],
                            [[r["date"], r.get("billets_donnant_acces"), r.get("pic_presents"),
                              r.get("ratio_pic_sur_billets")]
                             for r in bi["data"].get("par_jour_public") or []]))
    me = blocs.get("meteo") or {}
    if me.get("statut") == "ok":
        parts.append("<h3>Meteo</h3>")
        parts.append(_table(["Date", "T max", "T min", "Pluie (mm)", "Soleil (h)"],
                            [[r["date"], r["t_max"], r["t_min"], r["pluie_mm"], r["soleil_h"]]
                             for r in me["data"]["par_jour"]]))
    al = blocs.get("alertes") or {}
    if al.get("statut") == "ok":
        parts.append("<h3>Alertes par type</h3>")
        parts.append(_table(["Type", "Nombre"], list((al["data"].get("par_type") or {}).items())))
    indispo = dataset.get("blocs_indisponibles") or []
    if indispo:
        parts.append("<h3>Blocs indisponibles</h3>")
        parts.append(_table(["Bloc", "Raison"],
                            [[BLOCK_LABELS.get(n, n), (blocs.get(n) or {}).get("erreur")]
                             for n in indispo]))
    return "".join(parts)


_CSS = """
:root{--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--accent:#1d4ed8;--bg:#ffffff;--soft:#f1f5f9}
*{box-sizing:border-box}
body{margin:0;background:var(--soft);color:var(--ink);font:14px/1.55 Arial,Helvetica,sans-serif}
.page{max-width:900px;margin:24px auto;background:var(--bg);padding:36px 44px;border:1px solid var(--line)}
header{border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:18px}
.kicker{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
h1{font-size:26px;margin:4px 0}
h2{font-size:17px;margin:26px 0 8px;padding-left:10px;border-left:4px solid var(--accent);break-after:avoid}
h3{font-size:14px;margin:18px 0 6px;color:#334155;break-after:avoid}
.meta{font-size:12px;color:var(--muted)}
.muted{color:var(--muted);font-style:italic}
.warn{background:#fff7ed;border:1px solid #fed7aa;padding:10px 14px;margin:12px 0;font-size:13px}
.warn li{margin:2px 0}
table{border-collapse:collapse;width:100%;margin:6px 0 12px;font-size:12.5px;break-inside:auto}
th,td{border:1px solid var(--line);padding:4px 7px;text-align:left;vertical-align:top}
th{background:var(--soft)}
tr{break-inside:avoid}
ul{margin:6px 0 10px;padding-left:22px}
.toolbar{max-width:900px;margin:12px auto 0;text-align:right}
.toolbar button{font:inherit;padding:6px 14px;border:1px solid var(--accent);background:var(--accent);color:#fff;border-radius:4px;cursor:pointer}
@media print{body{background:#fff}.page{margin:0;border:0;padding:0;max-width:none}.toolbar{display:none}}
"""


def render_html(doc):
    ds = doc.get("dataset") or {}
    ed = ds.get("edition") or {}
    sections = doc.get("sections") or {}
    title = "RETEX %s %s" % (doc.get("event") or "", doc.get("year") or "")
    parts = [
        "<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>" + _e(title) + "</title><style>" + _CSS + "</style></head><body>",
        "<div class=\"toolbar\"><button type=\"button\" onclick=\"window.print()\">"
        "Imprimer / PDF</button></div>",
        "<div class=\"page\"><header>",
        "<div class=\"kicker\">Cockpit - Retour d'experience de fin d'edition</div>",
        "<h1>" + _e(title) + "</h1>",
        "<div class=\"meta\">Course : " + _e(ed.get("race_date")) + " &middot; Version "
        + _e(doc.get("version")) + " &middot; Generee le " + _e(paris_str(doc.get("created_at")))
        + " par " + _e(doc.get("created_by")) + " &middot; Modele " + _e(doc.get("model")) + "</div>",
        "</header>",
    ]
    notes = ds.get("avertissements") or []
    if notes or doc.get("truncated"):
        parts.append("<div class=\"warn\"><strong>Reserves de lecture</strong><ul>")
        for n in notes:
            parts.append("<li>" + _e(n) + "</li>")
        if doc.get("truncated"):
            parts.append("<li>Reponse du modele tronquee : la fin du rapport peut manquer.</li>")
        parts.append("</ul></div>")
    if sections:
        for key in SECTION_KEYS:
            parts.append("<h2>" + _e(SECTION_LABELS[key]) + "</h2>" + _md(sections.get(key)))
    else:
        parts.append("<h2>Texte brut du modele</h2><pre style=\"white-space:pre-wrap\">"
                     + _e(doc.get("raw_text")) + "</pre>")
    parts.append("<h2>Annexes chiffrees</h2>")
    parts.append(_annexes(ds))
    parts.append("<p class=\"meta\" style=\"margin-top:28px\">Texte redige par IA a partir des "
                 "seules donnees Cockpit listees en annexe ; les blocs indisponibles sont signales "
                 "comme tels.</p></div></body></html>")
    return "".join(parts)
