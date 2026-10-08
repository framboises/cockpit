"""Outil Alfred `cockpit_frequentation` : presents en direct et historique des
editions, comparaisons entre annees.

AUCUN CALCUL NOUVEAU. Les chiffres sont ceux que l'exploitation voit deja :

- en direct : `presents_etat` (accueil, TV general-stats, montre) -- presents
  = compteur principal - correction - vehicules, pic = plus haut RELEVE ;
- N-1 a la meme heure : `presents_etat.historique_n1`, la serie de la TV ;
- historique : `scan_frequentation.load_editions(..., LIVE_FIRST)`, celle du
  RETEX : archive du controle d'acces live d'abord (rejeu exact du
  dashboard), import Excel des scans en repli, edition par edition. On lit la
  base (direct ou archives), jamais le serveur de controle d'acces.

Conventions reprises de docs/claude/scans.md et frequentation-live.md :

- les editions se comparent au DECALAGE AU JOUR DE COURSE (J-1, J0...), jamais
  a la date calendaire ;
- seul le PIC DE PRESENTS se compare d'une edition a l'autre : le nombre de
  portes et la source de mesure changent, les entrees ne se comparent donc pas ;
- un jour sans mesure n'est pas une frequentation nulle ;
- un compteur non remis a zero avant l'edition majore tous ses presents.

Le resultat porte un `resume` en francais a recopier tel quel : le modele local
n'a ni a additionner, ni a aligner des jours, ni a calculer un pourcentage.
"""

import logging
import re
import threading
import time as _time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import alfred_evenements as AE

logger = logging.getLogger(__name__)

TZ_PARIS = ZoneInfo("Europe/Paris")
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]
EDITIONS_MAX = 8          # profondeur d'historique chargee
COMPARAISON_DEFAUT = 3    # editions montrees quand l'operateur dit juste << compare >>
CACHE_TTL_S = 600
RESUME_MAX = 4200
SOURCES = {"live_controle": "contrôle d'accès live",
           "scan_import": "import des scans",
           "collecte_temps_reel": "ancienne collecte temps réel"}

_cache = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Mise en forme
# ---------------------------------------------------------------------------

def nombre(n):
    return "{:,}".format(int(n)).replace(",", " ") if n is not None else "?"


def pct(a, b):
    if not a or not b:
        return None
    v = (a - b) * 100.0 / b
    return ("+" if v >= 0 else "") + ("%.1f" % v).replace(".", ",") + " %"


def jour_fr(d, annee=True):
    s = "%s %d %s" % (JOURS[d.weekday()], d.day, MOIS[d.month - 1])
    return s + (" %d" % d.year if annee else "")


def libelle_offset(o):
    if o == 0:
        return "J0 (jour de course)"
    if o == -1:
        return "J-1 (veille)"
    if o == 1:
        return "J+1 (lendemain)"
    return "J%+d" % o


def _date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Donnees
# ---------------------------------------------------------------------------

def _compacte(ed):
    """Edition de load_editions reduite a ce que l'outil lit (cache leger)."""
    ci = ed.get("counter_initial") or {}
    jours = []
    for d in ed.get("days") or []:
        jours.append({"date": d["date"], "offset": d.get("offset"),
                      "pic": d.get("peak_present"), "heure": d.get("peak_hour"),
                      "entrees": d.get("entrees"), "mesure": bool(d.get("measured")),
                      "motif": d.get("unmeasured_reason")})
    return {"annee": int(ed["year"]), "course": ed.get("race_date"),
            "source": ed.get("source") or "collecte_temps_reel",
            "portes": ed.get("doors"), "jours": jours,
            "solde_initial": ci.get("current"),
            "remise_a_zero": bool(ci.get("reset_in_window"))}


def editions_archivees(db, event, annee_ref):
    """Editions mesurees de l'evenement, de la plus recente a la plus ancienne.
    Cache 10 min : une edition close ne bouge plus."""
    cle = (id(db), event, int(annee_ref))
    with _cache_lock:
        hit = _cache.get(cle)
        if hit and _time.monotonic() - hit[0] < CACHE_TTL_S:
            return hit[1]
    import scan_frequentation as sf
    eds = sf.load_editions(db, event, int(annee_ref), back=EDITIONS_MAX - 1,
                           source_priority=sf.LIVE_FIRST)
    # EXCLUDED_EDITIONS est cle sur le nom de historique_controle (GPE) :
    # load_editions, appele avec le nom Cockpit (GP EXPLORER), ne l'applique
    # pas -- GPE 2023 et sa date de course fausse (J+30) passaient.
    noms = set(sf.event_aliases(db, event))
    out = [_compacte(e) for e in eds
           if not any((n, int(e["year"])) in sf.EXCLUDED_EDITIONS for n in noms)]
    with _cache_lock:
        _cache[cle] = (_time.monotonic(), out)
    return out


def _course(db, event, year):
    try:
        import watch_peaks
        dt = watch_peaks.resolve_race_dt(db, event, int(year))
        return dt.astimezone(TZ_PARIS).date() if dt else None
    except Exception:
        return None


def edition_en_base(db, event, year):
    """Edition encore dans le direct (data_access, pas encore archivee), au
    format _compacte, ou None. Pic de chaque jour = plus haut releve, comme le
    resume par jour de la TV."""
    import presents_etat as pe
    g = pe.read_global(db)
    if not pe.edition_en_direct(g, event):
        return None
    act = g.get("activation_timestamp")
    if not act or int(year) != pe.to_tranche_label(act).year:
        return None
    loc = pe.principal_location(g)
    if loc is None:
        return None
    race = _course(db, event, year)
    debut = pe.to_tranche_label(act).date()
    auj = datetime.now(TZ_PARIS).date()
    jours = []
    d = debut
    while d <= auj and len(jours) < 21:
        lo, hi = pe.paris_day_bounds_utc(d)
        pic, ts = pe.pic_presents(db, loc, lo, hi, g)
        h = pe.to_tranche_label(ts) if ts is not None else None
        jours.append({"date": d.isoformat(), "offset": (d - race).days if race else None,
                      "pic": pic, "heure": h.strftime("%H:%M") if h else None,
                      "entrees": None, "mesure": pic is not None,
                      "motif": None if pic is not None else "aucune_mesure"})
        d += timedelta(days=1)
    if not any(j["mesure"] for j in jours):
        return None   # direct deja purge : l'archive prendra le relais
    return {"annee": int(year), "course": race.isoformat() if race else None,
            "source": "live_controle", "portes": None, "jours": jours,
            "solde_initial": None, "remise_a_zero": True, "en_cours": True}


def toutes_editions(db, event, annee_ref):
    eds = list(editions_archivees(db, event, annee_ref))
    if not any(e["annee"] == int(annee_ref) for e in eds):
        try:
            live = edition_en_base(db, event, annee_ref)
        except Exception:
            logger.warning("frequentation : direct illisible pour %s %s", event, annee_ref,
                           exc_info=True)
            live = None
        if live:
            eds.insert(0, live)
    return sorted(eds, key=lambda e: -e["annee"])


def direct(db, event, year, now_utc=None):
    """Presents a l'instant, pic du jour, N-1 a la meme heure. None si le
    controle d'acces live ne compte pas cette edition maintenant."""
    import presents_etat as pe
    import watch_state
    now_utc = now_utc or datetime.now(timezone.utc)
    g = pe.read_global(db)
    if not (watch_state.read_live_active(db) and pe.edition_en_direct(g, event)):
        return None
    snap = watch_state.read_counter(db, watch_state.read_principal_id(db),
                                    max_age=watch_state.COUNTER_MAX_AGE, now_utc=now_utc)
    if not snap:
        return {"collecteur_arrete": True}
    n = pe.presents_principal(db, snap, now_utc=now_utc)
    releve = pe.to_tranche_label(snap.get("timestamp"))
    jour = now_utc.astimezone(TZ_PARIS).date()
    loc = pe.principal_location(g)
    pic, pic_ts = (None, None)
    if loc is not None:
        lo, hi = pe.paris_day_bounds_utc(jour)
        pic, pic_ts = pe.pic_presents(db, loc, lo, hi, g)
    out = {"presents": n, "releve": releve.strftime("%H:%M") if releve else None,
           "pic_jour": pic,
           "pic_jour_heure": pe.to_tranche_label(pic_ts).strftime("%H:%M") if pic_ts else None}
    # N-1 : meme decalage au jour de course, meme heure (serie de la TV)
    race = _course(db, event, year)
    n1 = pe.historique_n1(db, event, year)
    if race and n1 and n1.get("race"):
        j1 = n1["race"] + (jour - race)
        recs = n1["par_jour"].get(j1.isoformat()) or []
        hhmm = releve.strftime("%H:%M") if releve else None
        avant = [r for r in recs if hhmm and r["hour"] <= hhmm]
        out["n1"] = {"annee": n1["year"], "date": j1.isoformat(),
                     "meme_heure": avant[-1]["present"] if avant else None,
                     "pic_jour": max((r["present"] for r in recs), default=None)}
    return out


# ---------------------------------------------------------------------------
# Resolution des parametres
# ---------------------------------------------------------------------------

RELATIF = [
    (r"\b(il y a (deux|2) ans|n 2|avant derniere|avant dernier)\b", -2),
    (r"\b(an (dernier|passe)|annee (derniere|passee)|edition (precedente|derniere)|"
     r"precedente|n 1|l an d avant)\b", -1),
    (r"\b(cette annee|en cours|actuelle|cette edition)\b", 0),
]


def annees_demandees(raw, ref):
    """Annees citees (4 chiffres) ou relatives a `ref`, dans l'ordre."""
    s = AE.norm(raw)
    if not s:
        return []
    out = [int(y) for y in re.findall(r"\b(20\d\d)\b", s)]
    for rx, delta in RELATIF:
        if re.search(rx, s):
            out.append(ref + delta)
    vus = []
    for y in out:
        if y not in vus:
            vus.append(y)
    return vus


def nombre_editions(raw):
    """<< 3 dernieres editions >>, << toutes >> -> nombre, ou None."""
    s = AE.norm(raw)
    if re.search(r"\b(toutes|tout|historique complet)\b", s):
        return EDITIONS_MAX
    m = re.search(r"\b(\d{1,2}) (dernieres?|editions?|ans|annees)\b", s) or \
        re.search(r"\b(deux|trois|quatre|cinq) (dernieres?|editions?|ans|annees)\b", s)
    if m:
        v = {"deux": 2, "trois": 3, "quatre": 4, "cinq": 5}.get(m.group(1), m.group(1))
        return max(2, min(int(v), EDITIONS_MAX))
    if s in ("oui", "true", "1") or re.search(r"\bprecedentes\b", s):
        return COMPARAISON_DEFAUT
    return None


def offset_demande(raw, edition):
    """Decalage au jour de course vise par `jour`, ou None (toute l'edition)."""
    s = AE.norm(raw)
    if not s:
        return None
    if re.search(r"\b(jour de (la )?course|course|j ?0)\b", s):
        return 0
    if re.search(r"\bveille\b", s):
        return -1
    if re.search(r"\blendemain\b", s):
        return 1
    m = re.search(r"\bj ?([+-]) ?(\d{1,2})\b", str(raw or "").lower())
    if m:
        return int(m.group(2)) * (1 if m.group(1) == "+" else -1)
    jours = [j for j in edition["jours"]]
    if re.search(r"\b(aujourd ?hui|ce jour)\b", s):
        d = datetime.now(TZ_PARIS).date()
    elif s == "hier":
        d = datetime.now(TZ_PARIS).date() - timedelta(days=1)
    else:
        d = _date(raw)
    if d:
        hit = next((j for j in jours if j["date"] == d.isoformat()), None)
        return hit["offset"] if hit else "hors"
    for i, nom in enumerate(JOURS):
        if s.startswith(nom) or (" " + nom) in (" " + s):
            hits = [j["offset"] for j in jours if _date(j["date"]).weekday() == i]
            if hits:
                # Le plus proche de la course (un samedi = celui de la course)
                return min(hits, key=lambda o: (abs(o), o))
            return "hors"
    return None


def derniere_mesuree(db, now, exclure=None):
    """(event, annee) de la derniere epreuve commencee ayant des mesures."""
    try:
        import event_courant
        wins = sorted([w for w in event_courant.windows(db)
                       if w["start"] <= now and AE.norm(w["event"]) != "saison"
                       and w["event"] != exclure],
                      # Par FIN : un match d'un soir commence apres le montage
                      # des 24H CAMIONS mais se termine bien avant.
                      key=lambda w: min(w.get("end") or w["start"], now), reverse=True)
    except Exception:
        return None
    for w in wins[:8]:
        eds = toutes_editions(db, w["event"], max(int(w["year"]), now.year))
        if any(e["annee"] == int(w["year"]) for e in eds):
            return w["event"], int(w["year"])
    return None


def resoudre_evenement(db, args, ctx, now):
    """(event, annee_ref, note, candidats)."""
    ev_arg = str(args.get("evenement") or "").strip()
    note = None
    if ev_arg:
        ev, cands = AE.resoudre_nom(db, ev_arg)
        if not ev:
            return None, None, None, cands or []
        return ev, None, None, []
    ce = str((ctx or {}).get("event") or "")
    if ce and AE.norm(ce) != "saison":
        try:
            return ce, int((ctx or {}).get("year")), None, []
        except (TypeError, ValueError):
            return ce, None, None, []
    try:
        import event_courant
        ev, yr = event_courant.current_event(db, now)
        if AE.norm(ev) != "saison":
            return ev, int(yr), None, []
        # SAISON : l'epreuve la plus recente deja commencee (celle dont on a
        # des chiffres), sinon la prochaine.
        wins = event_courant.windows(db)
        passees = sorted([w for w in wins if w["start"] <= now and AE.norm(w["event"]) != "saison"],
                         key=lambda w: min(w.get("end") or w["start"], now))
        futures = sorted([w for w in wins if w["start"] > now and AE.norm(w["event"]) != "saison"],
                         key=lambda w: w["start"])
        w = (passees[::-1] or futures or [None])[0]
        if w:
            note = ("Aucune épreuve en cours : chiffres de la dernière épreuve, %s %s."
                    % (w["event"], w["year"]))
            return w["event"], int(w["year"]), note, []
    except Exception:
        logger.warning("frequentation : evenement courant illisible", exc_info=True)
    return None, None, None, []


# ---------------------------------------------------------------------------
# Textes
# ---------------------------------------------------------------------------

def _pic_edition(ed):
    js = [j for j in ed["jours"] if j["mesure"] and j["pic"] is not None]
    return max(js, key=lambda j: j["pic"]) if js else None


def _majoration(ed):
    """Solde laisse par les evenements precedents (compteur non remis a zero) :
    meme seuil que les reserves du RETEX (> 2 % du pic)."""
    p = _pic_edition(ed)
    s0 = ed.get("solde_initial")
    if ed["source"] == "live_controle" and s0 and p and not ed.get("remise_a_zero") \
            and s0 > 0.02 * p["pic"]:
        return s0
    return None


def _ligne_jour(j, avec_entrees=True, avec_annee=False):
    d = _date(j["date"])
    tete = "%s, %s" % (jour_fr(d, avec_annee), libelle_offset(j["offset"])) \
        if j["offset"] is not None else jour_fr(d, avec_annee)
    if not j["mesure"]:
        return "%s : pas de mesure" % tete
    s = "%s : pic %s présents à %s" % (tete, nombre(j["pic"]), j["heure"] or "?")
    if avec_entrees and j.get("entrees") is not None:
        s += ", %s entrées" % nombre(j["entrees"])
    return s


def texte_edition(event, ed, offset=None):
    lignes = []
    src = SOURCES.get(ed["source"], ed["source"])
    p = _pic_edition(ed)
    titre = "Fréquentation %s %d (source : %s%s)" % (
        event, ed["annee"], src, ", édition en cours" if ed.get("en_cours") else "")
    if offset is not None:
        j = next((x for x in ed["jours"] if x["offset"] == offset), None)
        if not j:
            return titre + " : aucun jour %s dans cette édition." % libelle_offset(offset)
        lignes.append(titre + " :")
        lignes.append("- " + _ligne_jour(j, avec_annee=True))
    else:
        if p:
            lignes.append("%s. Pic de l'édition : %s présents le %s à %s, %s." % (
                titre, nombre(p["pic"]), jour_fr(_date(p["date"])), p["heure"] or "?",
                libelle_offset(p["offset"])))
        else:
            lignes.append(titre + " : aucune mesure.")
        mesures = [j for j in ed["jours"] if j["mesure"]]
        if mesures:
            lignes.append("Par jour (pic de présents, heure du pic, entrées du jour) :")
            lignes += ["- " + _ligne_jour(j) for j in mesures]
        sans = [jour_fr(_date(j["date"]), False) for j in ed["jours"] if not j["mesure"]]
        if sans:
            lignes.append("Jours sans mesure (ce n'est pas une fréquentation nulle) : %s."
                          % ", ".join(sans))
    m = _majoration(ed)
    if m:
        lignes.append("Réserve : le compteur n'avait pas été remis à zéro avant l'édition "
                      "(solde initial %s) : les présents de %d sont majorés d'environ autant."
                      % (nombre(m), ed["annee"]))
    return "\n".join(lignes)


def texte_comparaison(event, eds, offset=None):
    ref = eds[0]
    lignes = ["Comparaison %s : %s. Pic de présents, éditions alignées sur le jour de course."
              % (event, ", ".join(str(e["annee"]) for e in eds))]
    lignes.append("Pic de l'édition :")
    p_ref = _pic_edition(ref)
    for e in eds:
        p = _pic_edition(e)
        if not p:
            lignes.append("- %d : aucune mesure" % e["annee"])
            continue
        s = "- %d : %s présents, %s à %s" % (e["annee"], nombre(p["pic"]),
                                              jour_fr(_date(p["date"]), False), p["heure"] or "?")
        if e is not ref and p_ref:
            s += " (%d : %s par rapport à %d)" % (ref["annee"], pct(p_ref["pic"], p["pic"]),
                                                  e["annee"])
        lignes.append(s)
    offsets = sorted({j["offset"] for e in eds for j in e["jours"]
                      if j["mesure"] and j["offset"] is not None})
    if offset is not None:
        offsets = [o for o in offsets if o == offset]
    if offsets:
        lignes.append("Par jour (pic de présents et heure) :")
    for o in offsets:
        du_jour = {}
        for e in eds:
            j = next((x for x in e["jours"] if x["offset"] == o and x["mesure"]
                      and x["pic"] is not None), None)
            if j:
                du_jour[e["annee"]] = j
        parts, partiels = [], set()
        for e in eds:
            j = du_jour.get(e["annee"])
            if not j:
                parts.append("%d pas de mesure" % e["annee"])
                continue
            # Quelques presents la ou les autres editions en comptent des
            # milliers : debut ou fin de collecte, pas une frequentation
            # (SUPERBIKE 2026 J0 : 3 presents). Aucun pourcentage dessus.
            autres = [x["pic"] for y, x in du_jour.items() if y != e["annee"]]
            if autres and j["pic"] < 0.05 * max(autres):
                partiels.add(e["annee"])
                parts.append("%d %s à %s (mesure très partielle)" % (
                    e["annee"], nombre(j["pic"]), j["heure"] or "?"))
            else:
                parts.append("%d %s à %s" % (e["annee"], nombre(j["pic"]), j["heure"] or "?"))
        # Jour de semaine : le meme pour toutes les editions (alignement au jour
        # de course), pris sur la premiere qui a ce jour.
        jr = next((x for e in eds for x in e["jours"] if x["offset"] == o), None)
        nom = JOURS[_date(jr["date"]).weekday()] if jr else None
        base = du_jour.get(ref["annee"])
        ecarts = []
        for e in eds[1:]:
            j = du_jour.get(e["annee"])
            if base and j and not ({ref["annee"], e["annee"]} & partiels):
                ecarts.append("%s vs %d" % (pct(base["pic"], j["pic"]), e["annee"]))
        lignes.append("- %s : %s%s" % ("%s, %s" % (nom, libelle_offset(o)) if nom
                                       else libelle_offset(o),
                                       " | ".join(parts),
                                       (" ; %d : %s" % (ref["annee"], ", ".join(ecarts)))
                                       if ecarts else ""))
    sources = {e["annee"]: e["source"] for e in eds}
    if len(set(sources.values())) > 1:
        lignes.append("Attention : sources de mesure différentes (%s) : un écart peut venir de "
                      "la mesure, pas de la foule." % ", ".join(
                          "%d %s" % (y, SOURCES.get(s, s)) for y, s in sorted(sources.items())))
    portes = {e["annee"]: e["portes"] for e in eds if e.get("portes")}
    if len(set(portes.values())) > 1:
        lignes.append("Les entrées ne se comparent pas d'une édition à l'autre (portes mesurées : "
                      "%s) : seul le pic de présents se compare." % ", ".join(
                          "%d %d" % (y, n) for y, n in sorted(portes.items())))
    for e in eds:
        m = _majoration(e)
        if m:
            lignes.append("Réserve %d : compteur non remis à zéro avant l'édition (solde initial "
                          "%s) : ses présents sont majorés d'environ autant." % (e["annee"], nombre(m)))
    return "\n".join(lignes)


def texte_direct(event, year, d):
    if d.get("collecteur_arrete"):
        return ("%s %s : contrôle d'accès live actif mais aucun relevé récent du compteur "
                "principal (collecteur probablement arrêté) : le signaler, ne rien estimer."
                % (event, year))
    s = "%s %s, en direct (relevé de %s) : %s personnes présentes sur site." % (
        event, year, d.get("releve") or "?", nombre(d.get("presents")))
    if d.get("pic_jour") is not None:
        s += " Pic du jour : %s à %s." % (nombre(d["pic_jour"]), d.get("pic_jour_heure") or "?")
    n1 = d.get("n1")
    if n1:
        j1 = _date(n1["date"])
        if n1.get("meme_heure") is not None:
            s += " Même jour de course en %d (%s), à la même heure : %s présents" % (
                n1["annee"], jour_fr(j1, False), nombre(n1["meme_heure"]))
            ec = pct(d.get("presents"), n1["meme_heure"])
            s += (" (écart %s)" % ec) if ec else ""
            s += "."
        if n1.get("pic_jour") is not None:
            s += " Pic de cette journée en %d : %s." % (n1["annee"], nombre(n1["pic_jour"]))
    return s


# ---------------------------------------------------------------------------
# Outil
# ---------------------------------------------------------------------------

def _donnees(eds):
    out = []
    for e in eds:
        p = _pic_edition(e)
        out.append({"annee": e["annee"], "source": e["source"],
                    "pic": p["pic"] if p else None,
                    "pic_le": ("%s %s" % (p["date"], p["heure"] or "")).strip() if p else None})
    return out


def t_frequentation(db, args, ctx, now=None):
    args = args or {}
    now = now or datetime.now(TZ_PARIS)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ_PARIS)
    event, annee_ctx, note, cands = resoudre_evenement(db, args, ctx, now)
    if not event:
        if cands:
            return {"disponible": True, "trouve": False, "candidats": cands,
                    "resume": "Plusieurs événements correspondent à « %s » : %s. Demander lequel."
                              % (args.get("evenement"), ", ".join(cands))}
        return {"disponible": False, "trouve": False,
                "resume": "Événement « %s » inconnu de Cockpit." % (args.get("evenement") or "")}

    # << l'an dernier >> se lit par rapport a l'edition de l'annee (passee ou a
    # venir) : en octobre 2026 comme en janvier 2027, c'est l'edition N-1.
    ref = annee_ctx or now.year
    eds = toutes_editions(db, event, max(ref, now.year))
    annees_dispo = [e["annee"] for e in eds]
    base = {"disponible": True, "evenement": event, "annees_disponibles": annees_dispo}
    if note:
        base["note"] = note

    demandees = annees_demandees(args.get("annee"), ref)
    if not eds and not args.get("evenement"):
        # Evenement du contexte ou en cours sans aucune mesure (IAME, karting...) :
        # la derniere epreuve mesuree, en le disant.
        rep = derniere_mesuree(db, now, exclure=event)
        if rep:
            ev2, y2 = rep
            motif = ("%s n'a aucune mesure de fréquentation : chiffres de la dernière épreuve "
                     "mesurée, %s %d." % (event, ev2, y2))
            r = t_frequentation(db, dict(args, evenement=ev2, annee=str(y2)), {}, now)
            r["resume"] = motif + "\n" + r.get("resume", "")
            r["note"] = motif
            return r
    if not eds:
        return dict(base, trouve=False,
                    resume="Aucune donnée de fréquentation pour %s (ni archive du contrôle "
                           "d'accès, ni import des scans) : le dire, ne rien estimer." % event)

    # Edition de reference : annee citee > annee du contexte > la plus recente mesuree
    if demandees:
        cible = demandees[0]
    elif annee_ctx and annee_ctx in annees_dispo:
        cible = annee_ctx
    else:
        cible = annees_dispo[0]

    # Direct : edition comptee a l'instant par le controle d'acces live
    vue_direct = None
    if not args.get("comparer_avec") and cible == (annee_ctx or now.year) and not args.get("jour"):
        try:
            vue_direct = direct(db, event, cible)
        except Exception:
            logger.warning("frequentation : direct illisible", exc_info=True)

    ed = next((e for e in eds if e["annee"] == cible), None)
    resolu = {"evenement": event, "annee": cible}

    # Comparaison : annees citees en plus, ou nombre d'editions
    autres = annees_demandees(args.get("comparer_avec"), cible) + demandees[1:]
    n_ed = nombre_editions(args.get("comparer_avec"))
    if args.get("comparer_avec") and not autres and not n_ed:
        n_ed = COMPARAISON_DEFAUT
    if autres or n_ed:
        if ed is None:
            ed_ref = next((e for e in eds if e["annee"] < cible), None) or eds[0]
        else:
            ed_ref = ed
        choix = [ed_ref]
        manquantes = []
        for y in autres:
            e = next((x for x in eds if x["annee"] == y), None)
            if e and e not in choix:
                choix.append(e)
            elif not e:
                manquantes.append(y)
        if n_ed:
            for e in eds:
                if len(choix) >= n_ed:
                    break
                if e not in choix and e["annee"] < ed_ref["annee"]:
                    choix.append(e)
        choix.sort(key=lambda e: -e["annee"])
        ref_ed = ed_ref
        choix.remove(ref_ed)
        choix.insert(0, ref_ed)
        off = offset_demande(args.get("jour"), ref_ed) if args.get("jour") else None
        resume = texte_comparaison(event, choix, off if isinstance(off, int) else None)
        if manquantes:
            resume += "\nPas de données pour %s." % ", ".join(str(y) for y in manquantes)
        if ed is None:
            resume = ("Pas de données pour %s %d : comparaison à partir de %d.\n"
                      % (event, cible, ref_ed["annee"])) + resume
        resolu.update(vue="comparaison", annees=[e["annee"] for e in choix])
        return dict(base, vue="comparaison", resolu=resolu, donnees=_donnees(choix),
                    resume=_borne(resume))

    if vue_direct is not None:
        resume = texte_direct(event, cible, vue_direct)
        if ed:
            precedents = [j for j in ed["jours"] if j["mesure"]
                          and j["date"] < now.date().isoformat()]
            if precedents:
                resume += "\nJours précédents de l'édition : " + " ; ".join(
                    _ligne_jour(j, avec_entrees=False) for j in precedents) + "."
        resolu.update(vue="direct")
        return dict(base, vue="direct", resolu=resolu, direct=vue_direct, resume=_borne(resume))

    if ed is None:
        proches = ", ".join(str(y) for y in annees_dispo)
        return dict(base, trouve=False, resolu=resolu,
                    resume="Pas de données de fréquentation pour %s %d. Éditions disponibles : %s."
                           % (event, cible, proches))
    off = offset_demande(args.get("jour"), ed) if args.get("jour") else None
    if off == "hors":
        resume = "Le jour « %s » ne fait pas partie de %s %d.\n" % (args.get("jour"), event, cible) \
            + texte_edition(event, ed)
    else:
        resume = texte_edition(event, ed, off)
    if note:
        resume = note + "\n" + resume
    resolu.update(vue="edition", jour=off)
    return dict(base, vue="edition", resolu=resolu, donnees=_donnees([ed]), resume=_borne(resume))


def _borne(resume):
    if len(resume) <= RESUME_MAX:
        return resume
    return resume[:RESUME_MAX].rsplit("\n", 1)[0] + "\n[… préciser un jour ou moins d'éditions]"
