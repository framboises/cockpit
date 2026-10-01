"""Briefing de situation : l'etat du site << maintenant >>, pour la releve.

Deux usages :
  - a la demande (bouton << Briefing >> de la sidebar, routes ai_reports.py) ;
  - a chaque releve (scripts/briefing_releve.py, tache planifiee), envoye
    par mail aux destinataires choisis par un admin.

Fonctions pures et lectures Mongo, `db` toujours passe en argument. Aucun
import Flask ni `app` : le script de releve tourne hors du process web.

PRINCIPE : chaque source est lue INDEPENDAMMENT (`_run_source`). Une source
qui leve est marquee `statut: "indisponible"` avec son erreur, et le
briefing continue. Un briefing partiel qui DIT ce qu'il ne sait pas vaut
mieux qu'un briefing absent -- et bien mieux qu'un briefing qui se tait sur
une source morte (une page calme parce qu'elle a cesse de savoir).

Le mode << sans IA >> rend le contexte structure tel quel, instantanement
et gratuitement ; le mode IA y ajoute une redaction Claude contrainte par un
contrat JSON strict (SECTION_KEYS) et un ancrage strict sur ce contexte.
"""

import html
import inspect
import json
import logging
import math
import os
import re
import smtplib
import time
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ_PARIS = ZoneInfo("Europe/Paris")

COLLECTION = "situation_briefings"
SETTINGS_COLLECTION = "cockpit_settings"
SETTINGS_ID = "briefing_releve"
DEFAULT_TIMES = ["06:45", "14:45", "22:45"]
MAX_TIMES = 8

# Contrat JSON du briefing. call_claude filtre les sections sur section_keys :
# sans ce parametre, les cinq sections seraient remplacees par les neuf
# sections pcorg vides (piege documente dans CLAUDE.md).
SECTION_KEYS = (
    "situation",
    "points_chauds",
    "a_surveiller_6h",
    "ressources",
    "consignes_releve",
)
SECTION_LABELS = {
    "situation": "Situation",
    "points_chauds": "Points chauds",
    "a_surveiller_6h": "A surveiller (6 h)",
    "ressources": "Ressources",
    "consignes_releve": "Consignes de releve",
}

RECENT_HOURS = 3
UPCOMING_HOURS = 6
MAJOR_URGENCIES = ("EU", "UA")
STATUT_CLOS = 10
FIELD_ONLINE_MAX_S = 10 * 60
PRESENTS_MAX_AGE_MIN = 15
VERDICT_LABELS = {0: "FLUIDE", 1: "VIGILANCE", 2: "TENSION", 3: "CRITIQUE"}
FIELD_STATUS_LABELS = {
    "patrouille": "disponible (patrouille)",
    "intervention": "engage (intervention)",
    "sur_place": "engage (sur place)",
    "fin_intervention": "fin d'intervention",
    "pause": "en pause",
}


# ---------------------------------------------------------------------------
# Helpers generiques (reutilises par edition_retex)
# ---------------------------------------------------------------------------

def as_utc(dt):
    """datetime naif (convention pymongo : UTC) ou conscient -> conscient UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def paris_str(dt, fmt="%Y-%m-%dT%H:%M"):
    """Horodatage Mongo (naif UTC) ou conscient -> chaine heure de Paris naive."""
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return str(dt)
    return as_utc(dt).astimezone(TZ_PARIS).strftime(fmt)


def jsonable(value):
    """Rend une structure serialisable JSON (et stockable telle quelle).

    datetime -> ISO heure de Paris naive (convention Cockpit), date -> ISO,
    ObjectId et inconnus -> str, NaN -> None.
    """
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, datetime):
        return paris_str(value, "%Y-%m-%dT%H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _pcorg_summary():
    import pcorg_summary
    return pcorg_summary


def record_usage(db, feature, model, usage, meta=None):
    """Journalise l'usage IA via pcorg_summary.record_ai_usage s'il existe.

    Import defensif : la fonction est ajoutee en parallele par un autre lot.
    Ne leve jamais -- un echec de journalisation ne doit pas perdre le
    rapport qui vient d'etre paye.
    """
    try:
        fn = getattr(_pcorg_summary(), "record_ai_usage", None)
    except Exception:
        fn = None
    if fn is None:
        return None
    try:
        return fn(db, feature, model, usage or {}, meta=meta)
    except Exception as exc:
        logger.warning("record_ai_usage(%s) : %s", feature, exc)
        return None


def api_key_present():
    try:
        return bool(getattr(_pcorg_summary(), "ANTHROPIC_API_KEY", ""))
    except Exception:
        return False


def default_model():
    try:
        return _pcorg_summary().CLAUDE_MODEL
    except Exception:
        return None


def _accepts(fn, name):
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def llm_call(db, system_prompt, user_prompt, section_keys, model=None,
             on_progress=None, max_tokens=None):
    """Appel Claude tolerant aux evolutions de pcorg_summary.

    Retourne (sections|None, raw_text, usage, meta) ou meta porte
    {model, stop_reason, truncated}.

    - budget : pcorg_summary.check_ai_budget(db) s'il existe (leve
      ClaudeError('budget_exceeded') quand le blocage est actif) ;
    - max_tokens fourni : appel direct de _claude_stream_request + parsing
      _parse_sections (le RETEX a besoin d'un budget plus large que
      CLAUDE_MAX_TOKENS) ; sinon call_claude (retry sur troncature compris) ;
    - section_keys TOUJOURS passe : c'est ce qui empeche call_claude de
      remplacer nos sections par les sections pcorg vides.
    """
    ps = _pcorg_summary()
    use_model = None
    validate = getattr(ps, "_validate_model", None)
    if validate is not None:
        use_model = validate(model)
    use_model = use_model or ps.CLAUDE_MODEL

    stream = getattr(ps, "_claude_stream_request", None)
    parse = getattr(ps, "_parse_sections", None)
    direct = bool(max_tokens and stream is not None and parse is not None)
    target = stream if direct else ps.call_claude

    # Budget : les versions recentes de pcorg_summary le controlent elles-memes
    # quand on leur passe `db` ; sinon on le fait ici (s'il existe).
    check = getattr(ps, "check_ai_budget", None)
    if check is not None and not _accepts(target, "db"):
        check(db)

    if direct:
        kwargs = {"on_progress": on_progress, "model": use_model}
        if _accepts(stream, "db"):
            kwargs["db"] = db
        schema_fn = getattr(ps, "build_output_schema", None)
        if schema_fn is not None and _accepts(stream, "output_schema"):
            # Structured outputs comme call_claude ; effort au defaut du module.
            kwargs["output_schema"] = schema_fn(tuple(section_keys))
            if _accepts(stream, "effort"):
                kwargs["effort"] = getattr(ps, "CLAUDE_EFFORT", None)
        try:
            raw_text, usage, stop_reason = stream(system_prompt, user_prompt,
                                                  int(max_tokens), **kwargs)
        except ps.ClaudeError as exc:
            if str(exc) != "claude_http_400" or "output_schema" not in kwargs:
                raise
            # Meme repli que call_claude : schema refuse -> sans output_config.
            kwargs.pop("output_schema", None)
            kwargs.pop("effort", None)
            raw_text, usage, stop_reason = stream(system_prompt, user_prompt,
                                                  int(max_tokens), **kwargs)
        sections = parse(raw_text, section_keys)
        return sections, raw_text, usage, {
            "model": use_model, "stop_reason": stop_reason,
            "truncated": stop_reason == "max_tokens"}

    kwargs = {"on_progress": on_progress, "model": use_model,
              "section_keys": tuple(section_keys)}
    if _accepts(ps.call_claude, "db"):
        kwargs["db"] = db
    sections, raw_text, usage = ps.call_claude(system_prompt, user_prompt, **kwargs)
    return sections, raw_text, usage, {
        "model": use_model, "stop_reason": None,
        "truncated": bool((usage or {}).get("retried_for_truncation"))}


def _run_source(name, fn, *args):
    """Execute une source ; une exception la marque indisponible, sans plus."""
    t0 = time.perf_counter()
    try:
        data = fn(*args)
        out = {"statut": "ok", "data": jsonable(data)}
    except Exception as exc:
        # ValueError = absence de donnee attendue (pas un bug) : pas de pile.
        logger.warning("source %s indisponible (%s)", name, exc,
                       exc_info=not isinstance(exc, ValueError))
        out = {"statut": "indisponible", "erreur": str(exc)[:300] or exc.__class__.__name__}
    out["ms"] = int(round((time.perf_counter() - t0) * 1000))
    return out


def _trunc(text, n):
    s = (str(text) if text is not None else "").strip()
    return s if len(s) <= n else s[:n].rstrip() + " [...]"


def _age_min(dt, now_utc):
    if dt is None:
        return None
    return int((now_utc - as_utc(dt)).total_seconds() // 60)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def _is_major(f):
    return (f.get("niveau_urgence") in MAJOR_URGENCIES) or bool(f.get("is_incident"))


def _fiche_compact(f, now_utc, detailed=False):
    cc = f.get("content_category") or {}
    out = {
        "id": str(f.get("_id")),
        "sql_id": f.get("sql_id"),
        "heure": paris_str(f.get("ts"), "%d/%m %H:%M"),
        "categorie": f.get("category"),
        "urgence": f.get("niveau_urgence"),
        "incident": bool(f.get("is_incident")),
        "zone": ((f.get("area") or {}).get("desc")) or None,
        "texte": _trunc(f.get("text") or f.get("text_full"), 400 if detailed else 140),
        "ouverte": f.get("status_code") != STATUT_CLOS,
        "age_min": _age_min(f.get("ts"), now_utc),
    }
    if cc.get("sous_classification"):
        out["sous_classification"] = cc.get("sous_classification")
    if cc.get("field_sos"):
        out["sos_tablette"] = True
    if detailed:
        hist = f.get("comment_history") or []
        last = [h for h in hist if isinstance(h, dict) and (h.get("text") or "").strip()][-2:]
        if last:
            out["derniers_commentaires"] = [_trunc(h.get("text"), 200) for h in last]
    return out


def _count(items, key):
    out = {}
    for it in items:
        k = it.get(key) or "_aucun"
        out[str(k)] = out.get(str(k), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


_FICHE_PROJ = {"_id": 1, "ts": 1, "category": 1, "niveau_urgence": 1,
               "is_incident": 1, "text": 1, "text_full": 1, "area": 1,
               "status_code": 1, "content_category": 1, "sql_id": 1,
               "comment_history": 1}


def src_main_courante(db, event, year, now_utc):
    """Fiches ouvertes (par categorie / urgence), dernieres 3 h, majeures anciennes."""
    if not event or year is None:
        raise ValueError("aucun evenement actif")
    col = db["pcorg"]
    base = {"event": event, "year": int(year)}
    # SAISON : les fiches ouvertes / recentes de SAISON/<y-1> restent visibles
    # (nuit du Nouvel An, fiche ouverte en decembre). Le total reste l'annee.
    live = dict(base)
    if str(event).strip().upper() == "SAISON":
        live["year"] = {"$in": [int(year) - 1, int(year)]}
    open_docs = list(col.find({**live, "status_code": {"$ne": STATUT_CLOS}}, _FICHE_PROJ))
    since = now_utc - timedelta(hours=RECENT_HOURS)
    recent = list(col.find({**live, "ts": {"$gte": since, "$lte": now_utc}}, _FICHE_PROJ))
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    recent.sort(key=lambda f: as_utc(f.get("ts")) or epoch, reverse=True)
    open_majors = sorted([f for f in open_docs if _is_major(f)],
                         key=lambda f: as_utc(f.get("ts")) or epoch)
    recent_majors = [f for f in recent if _is_major(f)]
    recent_others = [f for f in recent if not _is_major(f)]
    old_limit = now_utc - timedelta(hours=12)
    return {
        "total_edition": col.count_documents(base),
        "ouvertes": {
            "total": len(open_docs),
            "par_categorie": _count(open_docs, "category"),
            "par_urgence": _count(open_docs, "niveau_urgence"),
            "ouvertes_depuis_plus_de_12h": sum(
                1 for f in open_docs if f.get("ts") and as_utc(f["ts"]) < old_limit),
            "majeures_les_plus_anciennes": [_fiche_compact(f, now_utc, True)
                                            for f in open_majors[:10]],
        },
        "dernieres_%dh" % RECENT_HOURS: {
            "total": len(recent),
            "closes": sum(1 for f in recent if f.get("status_code") == STATUT_CLOS),
            "par_categorie": _count(recent, "category"),
            "par_urgence": _count(recent, "niveau_urgence"),
            "majeures": [_fiche_compact(f, now_utc, True) for f in recent_majors[:12]],
            "autres": [_fiche_compact(f, now_utc) for f in recent_others[:25]],
            "autres_non_listees": max(0, len(recent_others) - 25),
        },
    }


def src_alertes(db, now_utc):
    """Alertes actives (prises en charge ou non) et historique des 3 dernieres heures.

    Pas de filtre event/year : les alertes trafic, meteo et main courante
    tournent toute l'annee et portent souvent un event vide (cf. CLAUDE.md,
    << Les alertes de la montre ne filtrent pas sur l'evenement >>).
    """
    names = {}
    for d in db["cockpit_alert_definitions"].find({}, {"slug": 1, "name": 1}):
        if d.get("slug"):
            names[d["slug"]] = d.get("name") or d["slug"]
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    active = list(db["cockpit_active_alerts"].find({"expiresAt": {"$gt": now_utc}}))
    active.sort(key=lambda a: as_utc(a.get("triggeredAt")) or epoch, reverse=True)
    actives = []
    for a in active[:30]:
        slug = a.get("definition_slug") or ""
        actives.append({
            "type": names.get(slug, slug or "?"),
            "slug": slug,
            "titre": _trunc(a.get("title"), 120),
            "message": _trunc(a.get("message"), 200),
            "declenchee": paris_str(a.get("triggeredAt"), "%H:%M"),
            "age_min": _age_min(a.get("triggeredAt"), now_utc),
            "prise_en_charge": ({"par": a.get("taken_by_name") or a.get("taken_by"),
                                 "a": paris_str(a.get("taken_at"), "%H:%M")}
                                if a.get("taken_at") else None),
        })
    since = now_utc - timedelta(hours=RECENT_HOURS)
    hist = list(db["cockpit_alert_history"].find({"createdAt": {"$gte": since, "$lte": now_utc}}))
    hist.sort(key=lambda h: as_utc(h.get("createdAt")) or epoch, reverse=True)
    par_type = {}
    for h in hist:
        label = names.get(h.get("type") or "", h.get("type") or "?")
        par_type[label] = par_type.get(label, 0) + 1
    return {
        "actives": {
            "total": len(active),
            "non_prises_en_charge": sum(1 for a in active if not a.get("taken_at")),
            "liste": actives,
        },
        "historique_%dh" % RECENT_HOURS: {
            "total": len(hist),
            "par_type": dict(sorted(par_type.items(), key=lambda kv: -kv[1])),
            "dernieres": [{
                "heure": paris_str(h.get("createdAt"), "%H:%M"),
                "type": names.get(h.get("type") or "", h.get("type") or "?"),
                "titre": _trunc(h.get("title"), 100),
                "message": _trunc(h.get("message"), 160),
            } for h in hist[:15]],
        },
    }


def src_presents(db, event, year, now_utc):
    """Presents actuels, pic du jour, pic N-1 equivalent et pic projete.

    Meme calcul que le dashboard (/api/live-controle/dashboard) et la montre :
    presents_etat. La projection vient de pcorg_summary.compute_attendance_block
    (celle du rapport matinal) : pas de troisieme methode.
    """
    import presents_etat

    out = {}
    global_doc = presents_etat.read_global(db)
    loc = presents_etat.principal_location(global_doc)
    en_direct = presents_etat.edition_en_direct(global_doc, event)
    out["live_controle_edition"] = global_doc.get("evenement")
    out["live_controle_compte_cette_edition"] = en_direct
    if loc is not None and en_direct:
        out["compteur"] = loc.get("name")
        snap = db["data_access"].find_one(
            {"requested_location_id": str(loc.get("id")),
             "requested_location_type": loc.get("type")},
            sort=[("timestamp", -1)])
        if snap is not None:
            out["presents_actuels"] = presents_etat.presents_principal(db, snap, global_doc, now_utc)
            out["dernier_releve"] = paris_str(snap.get("timestamp"), "%H:%M")
            out["age_releve_min"] = _age_min(snap.get("timestamp"), now_utc)
            if (out["age_releve_min"] or 0) > PRESENTS_MAX_AGE_MIN:
                # Un compteur fige n'est pas un site vide : on le dit plutot
                # que de laisser lire << 0 present >> comme un fait.
                out["releve_perime"] = True
                out["note"] = ("dernier releve du compteur vieux de plus de %d min : "
                               "presents actuels non fiables" % PRESENTS_MAX_AGE_MIN)
        jour = now_utc.astimezone(TZ_PARIS).date()
        d0, d1 = presents_etat.paris_day_bounds_utc(jour)
        pic, instant = presents_etat.pic_presents(db, loc, d0, d1, global_doc)
        out["pic_du_jour"] = pic
        if instant is not None:
            out["pic_du_jour_heure"] = presents_etat.to_tranche_label(instant).strftime("%Hh%M")
    elif loc is None:
        out["note"] = "aucun compteur principal configure dans le live-controle"
    else:
        out["note"] = ("le live-controle ne compte pas cette edition : presents "
                       "actuels non disponibles")

    if event and year is not None:
        try:
            jour = now_utc.astimezone(TZ_PARIS).date()
            out["pic_n1_jour_equivalent"] = presents_etat.pic_n1_historique(db, event, year, jour)
        except Exception as exc:
            out["pic_n1_erreur"] = str(exc)[:200]
        try:
            att = _pcorg_summary().compute_attendance_block(db, event, year, now_utc=now_utc)
        except Exception as exc:
            att = None
            out["projection_erreur"] = str(exc)[:200]
        if att:
            slots = {s.get("slot"): s for s in att.get("slots") or []}
            today = slots.get("today") or {}
            yesterday = slots.get("yesterday") or {}
            out["billetterie_aujourdhui"] = {
                "jour_public": today.get("is_public"),
                "billets_vendus": today.get("billets_vendus"),
                "pic_projete": today.get("pic_projection"),
                "heure_pic_attendue": today.get("pic_prev_hour"),
                "pic_n1": today.get("pic_prev"),
                "delta_pct_vs_n1": today.get("delta_pct_vs_prev"),
            }
            out["hier"] = {
                "pic_constate": yesterday.get("pic_observed"),
                "heure": yesterday.get("pic_observed_hour"),
                "pic_n1": yesterday.get("pic_prev"),
            }
            out["annee_n1"] = att.get("prev_year")
    return out


def src_trafic(db, now_utc):
    """Verdict global et pires axes, meme calcul que le mur et la montre."""
    import trafic_etat

    routes = trafic_etat.lire_routes(db)
    alertes = trafic_etat.lire_alertes(db)
    if not routes and not alertes:
        raise ValueError("aucun releve Waze en base")
    axes = trafic_etat.axes_mur(routes)
    pire = max([a["severity"] for a in axes] or [0])
    t_routes = trafic_etat.fraicheur(db)
    t_alertes = trafic_etat.fraicheur_alertes(db)
    age_alertes = _age_min(t_alertes, now_utc)
    fraiches = age_alertes is not None and age_alertes <= 5
    out = {
        "age_releve_routes_min": _age_min(t_routes, now_utc),
        "age_releve_alertes_min": age_alertes,
    }
    if fraiches:
        comptes = trafic_etat.compter_alertes(alertes)
        vd = trafic_etat.verdict_global(comptes, pire)
        out["verdict"] = VERDICT_LABELS.get(vd, str(vd))
        out["alertes_waze_en_zone"] = {"accidents": comptes.get("ACCIDENT", 0),
                                       "bouchons": comptes.get("JAM", 0),
                                       "dangers": comptes.get("HAZARD", 0)}
        trafic_etat.rattacher_alertes(axes, alertes)
    else:
        # Meme regle que la montre : un verdict qui depend des accidents ne
        # s'affirme pas quand on ignore les accidents.
        out["verdict"] = None
        out["note"] = "alertes Waze perimees : verdict global non affirme"
    ordre = sorted(axes, key=lambda a: (a["severity"], a.get("deltaSeconds") or 0), reverse=True)
    # Le tag Waze `#P` designe les AUTOROUTES (A28, A11), pas les parkings
    # (CLAUDE.md, montre) ; axes_mur l'expose pourtant sous le nom `parking`.
    out["pires_axes"] = [{
        "axe": a["nom"],
        "sens": "autoroute" if a["parking"] else {"in": "entree", "out": "sortie"}.get(
            a["direction"], "sans sens"),
        "temps_s": a["currentTime"],
        "retard_s": a.get("deltaSeconds"),
        "severite_0_4": a["severity"],
        "alertes": {k: v for k, v in (a.get("alertes") or {}).items() if v} or None,
    } for a in ordre[:6]]
    out["axes_surveilles"] = len(axes)
    return out


def src_meteo(db, now_utc):
    """Etat du mur meteo (meteo_etat.etat_mur) : consignes ET contraintes."""
    import meteo_etat

    now_paris = now_utc.astimezone(TZ_PARIS).replace(tzinfo=None)
    etat = meteo_etat.etat_mur(db, now_paris)
    if not etat:
        raise ValueError("etat meteo vide")
    actuel = etat.get("actuel") or {}
    pluie = etat.get("prochaine_pluie") or {}
    vig = etat.get("vigilance") or {}
    return {
        "actuel": {k: actuel.get(k) for k in ("heure", "temperature_c", "pluie_mm",
                                              "vent_moyen_kmh", "vent_rafale_kmh",
                                              "wbgt_c", "wbgt_niveau")},
        "prochaines_6h": [{k: (b or {}).get(k) for k in ("heure", "temperature_c", "pluie_mm",
                                                         "vent_rafale_kmh")}
                          for b in (etat.get("prochaines") or [])[:UPCOMING_HOURS]],
        "prochaine_pluie": {k: pluie.get(k) for k in ("attendue", "dans_min", "intensite_mmh",
                                                      "pic_mmh", "horizon_min")} if pluie else None,
        "consignes": [{"niveau": c.get("niveau"), "heure": c.get("heure"), "texte": c.get("texte")}
                      for c in etat.get("consignes") or []],
        "contraintes_non_normales": [{
            "libelle": c.get("libelle"), "niveau": c.get("niveau"), "pic": c.get("pic"),
            "unite": c.get("unite"), "heure_pic": c.get("pic_heure"),
            "consigne": c.get("consigne"),
        } for c in etat.get("contraintes") or [] if c.get("niveau") not in (None, "normal")],
        "verdict": etat.get("verdict"),
        "vigilance_couleur_jour": vig.get("couleur_jour"),
    }


def src_field(db, event, year, now_utc):
    """Tablettes Field : engagees / disponibles / hors ligne, et SOS."""
    if not event or year is None:
        raise ValueError("aucun evenement actif")
    devs = list(db["field_devices"].find(
        {"event": event, "year": str(year), "revoked": {"$ne": True}}))
    par_statut = {}
    engagees, hors_ligne, disponibles = [], [], 0
    fiche_ids = [d.get("active_fiche_id") for d in devs if d.get("active_fiche_id")]
    fiches = {}
    if fiche_ids:
        for f in db["pcorg"].find({"_id": {"$in": fiche_ids}},
                                  {"_id": 1, "category": 1, "text": 1, "niveau_urgence": 1}):
            fiches[str(f["_id"])] = f
    for d in devs:
        statut = d.get("status") or "patrouille"
        age_s = None
        if d.get("last_seen"):
            age_s = (now_utc - as_utc(d["last_seen"])).total_seconds()
        en_ligne = age_s is not None and age_s <= FIELD_ONLINE_MAX_S
        par_statut[statut] = par_statut.get(statut, 0) + 1
        if not en_ligne:
            hors_ligne.append({"nom": d.get("name"),
                               "vu_il_y_a_min": int(age_s // 60) if age_s is not None else None})
        if statut in ("intervention", "sur_place", "fin_intervention"):
            f = fiches.get(str(d.get("active_fiche_id"))) or {}
            engagees.append({
                "nom": d.get("name"), "statut": FIELD_STATUS_LABELS.get(statut, statut),
                "depuis": paris_str(d.get("status_since"), "%H:%M"),
                "depuis_min": _age_min(d.get("status_since"), now_utc),
                "fiche": ({"categorie": f.get("category"), "urgence": f.get("niveau_urgence"),
                           "texte": _trunc(f.get("text"), 120)} if f else None),
            })
        elif statut == "patrouille" and en_ligne:
            disponibles += 1
    sos_actifs = [a for a in db["cockpit_active_alerts"].find(
        {"definition_slug": "field_sos", "expiresAt": {"$gt": now_utc}})]
    since = now_utc - timedelta(hours=RECENT_HOURS)
    sos_fiches = list(db["pcorg"].find(
        {"event": event, "year": int(year), "content_category.field_sos": True},
        {"_id": 1, "ts": 1, "status_code": 1, "text": 1}))
    return {
        "tablettes_enrolees": len(devs),
        "par_statut": {FIELD_STATUS_LABELS.get(k, k): v for k, v in par_statut.items()},
        "disponibles_en_ligne": disponibles,
        "engagees": engagees,
        "hors_ligne": hors_ligne,
        "sos": {
            "alertes_sos_actives": [{
                "tablette": (a.get("actionData") or {}).get("device_name"),
                "declenche": paris_str(a.get("triggeredAt"), "%H:%M"),
                "pris_en_charge_par": a.get("taken_by_name"),
            } for a in sos_actifs],
            "fiches_sos_ouvertes": sum(1 for f in sos_fiches if f.get("status_code") != STATUT_CLOS),
            "sos_dernieres_%dh" % RECENT_HOURS: sum(
                1 for f in sos_fiches if f.get("ts") and as_utc(f["ts"]) >= since),
            "sos_edition": len(sos_fiches),
        },
    }


def src_timetable(db, event, year, now_utc):
    """Vignettes timetable des 6 prochaines heures (factorisees)."""
    items = _pcorg_summary().get_upcoming_timetable(db, event, year, hours=UPCOMING_HOURS,
                                                    now_utc=now_utc)
    return {"vignettes": [{
        "heure": it.get("time"), "date": it.get("date"),
        "activite": _trunc(it.get("activity"), 140),
        "lieu": _trunc(it.get("place"), 80) or None,
        "categorie": it.get("category") or None,
        "remarque": _trunc(it.get("remark"), 120) or None,
    } for it in items[:25]]}


SOURCE_LABELS = {
    "main_courante": "Main courante",
    "alertes": "Centre d'alertes",
    "presents": "Presents / affluence",
    "trafic": "Trafic",
    "meteo": "Meteo",
    "field": "Tablettes Field",
    "timetable": "Timetable",
}


def build_context(db, now=None, event=None, year=None, sources=None):
    """Contexte structure du briefing. Ne leve jamais pour une source.

    now : datetime conscient (defaut maintenant) ; un naif est lu en heure
    de Paris. event/year : defaut = evenement actif (detect_active_event,
    meme logique que le rapport matinal, repli SAISON).
    sources : surcharge {nom: callable} (tests).
    """
    t0 = time.perf_counter()
    if now is None:
        now_utc = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now_utc = now.replace(tzinfo=TZ_PARIS).astimezone(timezone.utc)
    else:
        now_utc = now.astimezone(timezone.utc)

    detect_err = None
    if not event:
        try:
            event, year = _pcorg_summary().detect_active_event(db, now_utc=now_utc)
        except Exception as exc:
            detect_err = str(exc)[:200]
            event, year = None, None
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            pass

    registry = {
        "main_courante": (src_main_courante, (db, event, year, now_utc)),
        "alertes": (src_alertes, (db, now_utc)),
        "presents": (src_presents, (db, event, year, now_utc)),
        "trafic": (src_trafic, (db, now_utc)),
        "meteo": (src_meteo, (db, now_utc)),
        "field": (src_field, (db, event, year, now_utc)),
        "timetable": (src_timetable, (db, event, year, now_utc)),
    }
    if sources:
        for name, fn in sources.items():
            registry[name] = (fn, registry.get(name, (None, (db, now_utc)))[1])

    out_sources = {}
    for name, (fn, args) in registry.items():
        out_sources[name] = _run_source(name, fn, *args)

    ctx = {
        "genere_a": now_utc.astimezone(TZ_PARIS).strftime("%Y-%m-%dT%H:%M"),
        "event": event,
        "year": year,
        "sources": out_sources,
        "sources_indisponibles": [n for n, s in out_sources.items() if s["statut"] != "ok"],
        "duree_ms": int(round((time.perf_counter() - t0) * 1000)),
    }
    if detect_err:
        ctx["detection_evenement_erreur"] = detect_err
    return ctx


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Tu es l'officier de liaison du PC Organisation du circuit du Mans (Automobile Club de l'Ouest). Tu rediges le BRIEFING DE SITUATION remis au superviseur qui prend son poste : l'etat du site MAINTENANT, en francais, pour qu'il sache en deux minutes ou regarder.

REGLES D'ANCRAGE (non negociables) :
- Tu n'utilises QUE les faits du contexte JSON fourni. Aucun chiffre, lieu, nom, horaire ou evenement qui n'y figure pas.
- Une source dont "statut" vaut "indisponible" : ecris "donnee indisponible" en nommant la source (ex. "Trafic : donnee indisponible") la ou elle serait utile. Ne deduis RIEN a sa place, ne dis jamais que tout est calme faute de donnee.
- Une source "ok" mais vide se dit explicitement ("aucune alerte active", "aucune fiche majeure ouverte").
- Un champ null ou absent n'est pas un zero.
- Les heures sont en heure de Paris, au format 14h35. Les ages sont en minutes dans le contexte : convertis-les en duree lisible.
- Urgences : EU = detresse vitale, UA = urgence absolue, UR = urgence relative, IMP = implique.
- Le pic de presents projete est une estimation : dis-le.
- Style telegraphique, operationnel, phrases courtes. **Gras** pour les chiffres cles. Pas de formule de politesse.

FORMAT DE SORTIE : un objet JSON strict, sans texte autour, avec exactement ces 5 cles, chaque valeur etant une chaine :
- "situation" : 3 a 5 lignes. Etat general (activite main courante, alertes, presents, trafic, meteo) et tendance.
- "points_chauds" : puces "- " : ce qui demande une action ou une attention maintenant (fiches majeures ouvertes et anciennes, alertes non prises en charge, SOS, axe critique, consigne meteo). Si rien : "- Aucun point chaud dans les donnees fournies."
- "a_surveiller_6h" : puces "- " : jalons timetable, meteo, pic de frequentation attendu, dans les 6 prochaines heures, avec l'heure.
- "ressources" : puces "- " : tablettes engagees / disponibles / hors ligne, sur quoi elles sont engagees.
- "consignes_releve" : puces "- " : ce que l'equipe sortante doit transmettre explicitement (dossiers en cours, fiches a suivre, engagements en cours, points non resolus).

Pour les listes, separe les puces par des retours a la ligne commencant par "- "."""


def build_prompts(context):
    """(system, user) du briefing. Le contexte est transmis tel quel (compact)."""
    payload = {
        "genere_a": context.get("genere_a"),
        "evenement": context.get("event"),
        "annee": context.get("year"),
        "sources": {name: ({"statut": s["statut"], "donnees": s.get("data")}
                           if s["statut"] == "ok"
                           else {"statut": "indisponible", "raison": s.get("erreur")})
                    for name, s in (context.get("sources") or {}).items()},
    }
    user = ("Contexte du site au " + str(context.get("genere_a")) + " (heure de Paris).\n\n"
            "```json\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) +
            "\n```\n\nRedige le briefing au format JSON demande.")
    return SYSTEM_PROMPT, user


# ---------------------------------------------------------------------------
# Generation et persistance
# ---------------------------------------------------------------------------

def generate_briefing(db, context, by=None, model=None, on_progress=None,
                      trigger="manuel", persist=True):
    """Redaction Claude d'un contexte deja construit, puis persistance.

    Leve pcorg_summary.ClaudeError si l'appel echoue (le job la rapporte).
    """
    system, user = build_prompts(context)
    sections, raw_text, usage, meta = llm_call(
        db, system, user, SECTION_KEYS, model=model, on_progress=on_progress)
    doc = {
        "ts": datetime.now(timezone.utc),
        "event": context.get("event"),
        "year": context.get("year"),
        "by": by or "",
        "trigger": trigger,
        "context": context,
        "sections": sections,
        "raw_text": None if sections else raw_text,
        "model": meta["model"],
        "usage": usage,
        "prompt_chars": len(system) + len(user),
    }
    if persist:
        res = db[COLLECTION].insert_one(doc)
        doc["_id"] = res.inserted_id
        _ensure_indexes(db)
    record_usage(db, "situation_briefing", meta["model"], usage,
                 meta={"event": context.get("event"), "year": context.get("year"),
                       "trigger": trigger,
                       "briefing_id": str(doc.get("_id")) if doc.get("_id") else None})
    return doc


_indexes_ok = False


def _ensure_indexes(db):
    global _indexes_ok
    if _indexes_ok:
        return
    try:
        db[COLLECTION].create_index([("ts", -1)], name="ts_desc")
        db[COLLECTION].create_index([("event", 1), ("year", 1), ("ts", -1)], name="event_year_ts")
        _indexes_ok = True
    except Exception as exc:
        logger.warning("index %s : %s", COLLECTION, exc)


def serialize(doc, light=True):
    if not doc:
        return None
    out = {
        "id": str(doc.get("_id")),
        "ts": paris_str(doc.get("ts"), "%Y-%m-%dT%H:%M"),
        "event": doc.get("event"),
        "year": doc.get("year"),
        "by": doc.get("by"),
        "trigger": doc.get("trigger"),
        "model": doc.get("model"),
        "usage": doc.get("usage"),
        "parsed": bool(doc.get("sections")),
        "sources_indisponibles": (doc.get("context") or {}).get("sources_indisponibles"),
    }
    if not light:
        out["sections"] = doc.get("sections")
        out["raw_text"] = doc.get("raw_text")
        out["context"] = doc.get("context")
    return out


def list_briefings(db, limit=30):
    cur = db[COLLECTION].find({}, {"context": 0, "raw_text": 0}).sort("ts", -1).limit(int(limit))
    return [serialize(d) for d in cur]


def get_briefing(db, briefing_id):
    from bson.objectid import ObjectId
    try:
        oid = ObjectId(str(briefing_id))
    except Exception:
        return None
    return db[COLLECTION].find_one({"_id": oid})


# ---------------------------------------------------------------------------
# Reglages << a chaque releve >>
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def get_settings(db):
    doc = db[SETTINGS_COLLECTION].find_one({"_id": SETTINGS_ID}) or {}
    return {
        "enabled": bool(doc.get("enabled", False)),
        "times": list(doc.get("times") or DEFAULT_TIMES),
        "recipients": [str(x) for x in doc.get("recipients") or []],
        "last_slots": list(doc.get("last_slots") or []),
        "updated_at": paris_str(doc.get("updated_at")) if doc.get("updated_at") else None,
        "updated_by": doc.get("updated_by"),
    }


def validate_times(times):
    if not isinstance(times, (list, tuple)):
        raise ValueError("times doit etre une liste")
    clean = sorted({str(t).strip() for t in times if str(t).strip()})
    if not clean:
        raise ValueError("au moins une heure de releve")
    if len(clean) > MAX_TIMES:
        raise ValueError("au plus %d heures de releve" % MAX_TIMES)
    for t in clean:
        if not _TIME_RE.match(t):
            raise ValueError("heure invalide : %s (format HH:MM)" % t)
    return clean


def set_settings(db, enabled=None, times=None, recipients=None, by=None):
    from bson.objectid import ObjectId
    upd = {"updated_at": datetime.now(timezone.utc), "updated_by": by or ""}
    if enabled is not None:
        upd["enabled"] = bool(enabled)
    if times is not None:
        upd["times"] = validate_times(times)
    if recipients is not None:
        oids = []
        for r in recipients:
            try:
                oids.append(ObjectId(str(r)))
            except Exception:
                raise ValueError("destinataire invalide : %s" % r)
        upd["recipients"] = oids
    db[SETTINGS_COLLECTION].update_one({"_id": SETTINGS_ID}, {"$set": upd}, upsert=True)
    return get_settings(db)


def recipient_emails(db, settings=None):
    """user_ids -> emails uniques (utilisateurs cockpit ayant un email)."""
    from bson.objectid import ObjectId
    settings = settings or get_settings(db)
    oids = []
    for uid in settings.get("recipients") or []:
        try:
            oids.append(ObjectId(uid))
        except Exception:
            continue
    if not oids:
        return []
    out, seen = [], set()
    for u in db["users"].find({"_id": {"$in": oids},
                               "roles_by_app.cockpit": {"$exists": True},
                               "email": {"$exists": True, "$ne": ""}}, {"email": 1}):
        e = (u.get("email") or "").strip()
        if e and e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out


def due_slot(times, now_paris, window_min=15, already=()):
    """Creneau de releve a traiter maintenant, ou None.

    Un creneau HH:MM est du si HH:MM <= maintenant < HH:MM + fenetre (la tache
    planifiee passe toutes les 15 min). `already` : creneaux deja envoyes
    (cle 'YYYY-MM-DD HH:MM'), pour qu'un double passage n'envoie pas deux fois.
    Gere le passage de minuit (creneau 23:55, passage a 00:05).
    """
    if now_paris.tzinfo is not None:
        now_paris = now_paris.astimezone(TZ_PARIS).replace(tzinfo=None)
    for day_shift in (0, -1):
        d = (now_paris + timedelta(days=day_shift)).date()
        for t in sorted(times or []):
            if not _TIME_RE.match(str(t)):
                continue
            hh, mm = int(t[:2]), int(t[3:])
            slot_dt = datetime(d.year, d.month, d.day, hh, mm)
            if slot_dt <= now_paris < slot_dt + timedelta(minutes=window_min):
                key = slot_dt.strftime("%Y-%m-%d %H:%M")
                if key not in (already or ()):
                    return key
    return None


def mark_slot_sent(db, slot_key):
    db[SETTINGS_COLLECTION].update_one(
        {"_id": SETTINGS_ID},
        {"$push": {"last_slots": {"$each": [slot_key], "$slice": -30}}},
        upsert=True)


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------

def _md_to_html(text):
    try:
        import pcorg_summary_mail
        return pcorg_summary_mail._render_md(text)
    except Exception:
        return "<p>" + html.escape(text or "").replace("\n", "<br>") + "</p>"


def _kpi_lines(context):
    """Chiffres cles tires du contexte brut (utilises par le mail et l'UI)."""
    s = context.get("sources") or {}
    lines = []

    def data(name):
        src = s.get(name) or {}
        return src.get("data") if src.get("statut") == "ok" else None

    mc = data("main_courante")
    lines.append(("Fiches ouvertes",
                  ((mc.get("ouvertes") or {}).get("total")) if mc is not None else "indisponible"))
    al = data("alertes")
    if al is None:
        alertes = "indisponible"
    else:
        act = al.get("actives") or {}
        alertes = "%s (%s non prises en charge)" % (act.get("total"), act.get("non_prises_en_charge"))
    lines.append(("Alertes actives", alertes))
    pr = data("presents")
    if pr is None:
        presents = "indisponible"
    elif pr.get("releve_perime"):
        presents = "non fiable (dernier releve %s)" % pr.get("dernier_releve")
    else:
        presents = pr.get("presents_actuels")
    lines.append(("Presents", presents))
    tr = data("trafic")
    lines.append(("Trafic", (tr.get("verdict") or "non affirme") if tr is not None else "indisponible"))
    fi = data("field")
    lines.append(("Tablettes engagees",
                  len(fi.get("engagees") or []) if fi is not None else "indisponible"))
    return lines


def render_briefing_html(doc):
    ctx = doc.get("context") or {}
    sections = doc.get("sections") or {}
    title = "Briefing de situation - %s %s" % (ctx.get("event") or "", ctx.get("year") or "")
    rows = "".join(
        '<tr><td style="padding:4px 10px;color:#475569;">%s</td>'
        '<td style="padding:4px 10px;font-weight:700;">%s</td></tr>'
        % (html.escape(str(k)), html.escape("-" if v is None else str(v)))
        for k, v in _kpi_lines(ctx))
    parts = [
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;max-width:720px;margin:auto;">',
        '<div style="background:#1e3a8a;color:#fff;padding:18px 22px;border-radius:8px 8px 0 0;">',
        '<div style="font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#bfdbfe;">'
        'Cockpit - Briefing de releve</div>',
        '<div style="font-size:20px;font-weight:700;margin-top:4px;">%s</div>' % html.escape(title),
        '<div style="font-size:13px;color:#dbeafe;margin-top:4px;">Etat au %s (heure de Paris)</div>'
        % html.escape(str(ctx.get("genere_a") or "")),
        '</div>',
        '<table style="border-collapse:collapse;margin:12px 0;font-size:14px;">%s</table>' % rows,
    ]
    if ctx.get("sources_indisponibles"):
        parts.append('<p style="color:#b45309;font-size:13px;">Sources indisponibles : %s</p>'
                     % html.escape(", ".join(SOURCE_LABELS.get(n, n)
                                             for n in ctx["sources_indisponibles"])))
    if sections:
        for key in SECTION_KEYS:
            parts.append('<h3 style="font-size:14px;text-transform:uppercase;letter-spacing:.05em;'
                         'border-left:4px solid #2563eb;padding-left:8px;margin:18px 0 6px;">%s</h3>'
                         % html.escape(SECTION_LABELS[key]))
            parts.append(_md_to_html(sections.get(key) or "RAS"))
    else:
        parts.append('<p style="color:#64748b;font-style:italic;">Redaction IA indisponible : '
                     'seuls les chiffres bruts ci-dessus sont fournis.</p>')
    parts.append('<p style="color:#94a3b8;font-size:11px;margin-top:24px;">Genere automatiquement '
                 'par Cockpit. Les textes IA ne portent que sur les donnees listees ; une source '
                 'indisponible est signalee comme telle.</p></div>')
    body = "".join(parts)
    try:
        import pcorg_summary_mail
        return pcorg_summary_mail._wrap('<tr><td style="padding:16px;">' + body + '</td></tr>')
    except Exception:
        return "<!doctype html><html><body>" + body + "</body></html>"


def render_briefing_text(doc):
    ctx = doc.get("context") or {}
    lines = ["Briefing de situation - %s %s - %s" % (ctx.get("event"), ctx.get("year"),
                                                     ctx.get("genere_a")), ""]
    for k, v in _kpi_lines(ctx):
        lines.append("%s : %s" % (k, v))
    for key in SECTION_KEYS:
        txt = (doc.get("sections") or {}).get(key)
        if txt:
            lines += ["", SECTION_LABELS[key].upper(), txt]
    return "\n".join(lines)


class SmtpError(Exception):
    pass


def send_briefing_email(to_emails, doc):
    """Envoi SMTP, meme configuration que pcorg_summary_mail (variables SMTP_*)."""
    host = (os.getenv("SMTP_HOST", "192.168.254.2") or "").strip()
    if not host:
        raise SmtpError("SMTP non configure")
    if not to_emails:
        raise SmtpError("Aucun destinataire")
    port = int(os.getenv("SMTP_PORT", "25"))
    user = (os.getenv("SMTP_USER", "") or "").strip()
    password = (os.getenv("SMTP_PASSWORD", "") or "").strip()
    sender = (os.getenv("SMTP_FROM", "safe@lemans.org") or "").strip()
    sender_name = (os.getenv("SMTP_FROM_NAME", "TITAN ACO") or "").strip()
    use_tls = os.getenv("SMTP_USE_TLS", "false").strip().lower() in {"1", "true", "yes", "on"}
    timeout = int(os.getenv("SMTP_TIMEOUT", "10"))
    ctx = doc.get("context") or {}
    msg = MIMEMultipart("alternative")
    msg["From"] = "%s <%s>" % (sender_name, sender)
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = "[TITAN Cockpit] Briefing de releve - %s %s - %s" % (
        ctx.get("event") or "", ctx.get("year") or "", ctx.get("genere_a") or "")
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] if "@" in sender else "lemans.org")
    msg["X-Mailer"] = "TITAN Cockpit Briefing"
    msg.attach(MIMEText(render_briefing_text(doc), "plain", "utf-8"))
    msg.attach(MIMEText(render_briefing_html(doc), "html", "utf-8"))
    try:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg, from_addr=sender, to_addrs=list(to_emails))
    except (smtplib.SMTPException, OSError) as exc:
        raise SmtpError("smtp_send_failed: " + str(exc))
    return {"ok": True, "sent_count": len(to_emails), "smtp_host": host}
