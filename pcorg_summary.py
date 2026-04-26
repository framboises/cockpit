"""Resume de periode des fiches PC Organisation via l'API Claude.

Module helpers pur (pas de blueprint Flask) : les routes vivent dans app.py
a cote des autres routes /api/pcorg/* pour rester coherent.

Pattern d'appel HTTP externe calque sur traffic.py (Waze) et routing.py (Valhalla).
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from pymongo import ASCENDING, DESCENDING

TZ_PARIS = ZoneInfo("Europe/Paris")


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Configuration (variables d'environnement)
# ----------------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6").strip()
CLAUDE_TIMEOUT_SECONDS = int(os.getenv("CLAUDE_TIMEOUT_SECONDS", "120"))
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "8192"))


SUMMARIES_COLLECTION = "pcorg_summaries"
PCORG_COLLECTION = "pcorg"
N1_RETROS_COLLECTION = "pcorg_n1_retros"
MORNING_REPORT_SETTINGS_ID = "morning_report"
COCKPIT_SETTINGS_COLLECTION = "cockpit_settings"

# Plafond du nombre de fiches transmises a Claude (apres priorisation).
DEFAULT_MAX_FICHES = 80
TEXT_TRUNCATE_CHARS = 800
COMMENTS_KEEP_LAST = 3

# Cles de sortie attendues du modele.
SECTION_KEYS = (
    "synthese",
    "faits_marquants",
    "secours",
    "securite",
    "technique",
    "flux",
    "fourriere",
    "recommandations",
    "prochaines_24h",
)


_indexes_ensured = False


def _ensure_indexes(db):
    """Cree les index lazy au premier appel."""
    global _indexes_ensured
    if _indexes_ensured:
        return
    try:
        db[SUMMARIES_COLLECTION].create_index(
            [("event", ASCENDING), ("year", ASCENDING), ("period_start", DESCENDING)],
            name="event_year_period",
        )
        _indexes_ensured = True
    except Exception as e:
        logger.warning("Impossible de creer l'index sur %s: %s", SUMMARIES_COLLECTION, e)


# ----------------------------------------------------------------------------
# Helpers : alignement par date de course (logique du bloc affluence)
# ----------------------------------------------------------------------------

def _parse_race_dt(raw):
    """Parse une date de course (str ISO ou datetime) en datetime aware UTC.

    Retourne None si non parsable.
    """
    if raw is None:
        return None
    try:
        if isinstance(raw, datetime):
            dt = raw
        else:
            s = str(raw).strip()
            if not s:
                return None
            s = s.replace("Z", "+00:00")
            # Date pure -> midi UTC pour eviter les bords de jour
            if "T" not in s and " " not in s and len(s) <= 10:
                dt = datetime.fromisoformat(s).replace(hour=12)
            else:
                dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_race_dt(db, event, year):
    """Resout la date de course pour (event, year) avec chaine de fallback.

    Priorite (du plus fiable au plus permissif) :
      1. parametrages.data.globalHoraires.race  (source officielle moderne)
      2. parametrages.data.race                  (vieux champ, parfois errone)
      3. historique_controle{type:portes, event, year}.race    (audit terrain)
      4. historique_controle{type:frequentation, event, year}.race

    `year` peut etre stocke en string ou int : on tente les deux dans
    parametrages (la prod stocke en string).

    Retourne un datetime UTC ou None.
    """
    if not event or year is None:
        return None
    try:
        year_int = int(year)
    except (TypeError, ValueError):
        return None

    proj = {"data.race": 1, "data.globalHoraires.race": 1}
    doc = None
    try:
        doc = db["parametrages"].find_one({"event": event, "year": str(year_int)}, proj)
        if not doc:
            doc = db["parametrages"].find_one({"event": event, "year": year_int}, proj)
    except Exception:
        doc = None

    if doc:
        data = doc.get("data") or {}
        gh = data.get("globalHoraires") or {}
        # 1. globalHoraires.race en priorite
        for raw in (gh.get("race"), data.get("race")):
            if raw:
                dt = _parse_race_dt(raw)
                if dt:
                    return dt

    # 3-4. Fallback historique_controle (souvent plus fiable post-event)
    for hc_type in ("portes", "frequentation"):
        try:
            for y in (year_int, str(year_int)):
                hc = db["historique_controle"].find_one(
                    {"type": hc_type, "event": event, "year": y},
                    {"race": 1},
                )
                if hc and hc.get("race"):
                    dt = _parse_race_dt(hc["race"])
                    if dt:
                        return dt
        except Exception:
            continue
    return None


def _aligned_prev_year_window(ts_start, ts_end, race_dt_n, race_dt_prev):
    """Retourne (start_prev, end_prev) alignes sur la date de course N-1.

    offset = ts - race_n ; ts_prev = race_prev + offset (en secondes pour
    une precision horaire identique au bloc hsh_get_counters_context).
    """
    if not race_dt_n or not race_dt_prev:
        return None, None
    off_start = (ts_start - race_dt_n).total_seconds()
    off_end = (ts_end - race_dt_n).total_seconds()
    return (
        race_dt_prev + timedelta(seconds=off_start),
        race_dt_prev + timedelta(seconds=off_end),
    )


# ----------------------------------------------------------------------------
# Helpers : billetterie & frequentation
# ----------------------------------------------------------------------------

def _parse_yyyy_mm_dd(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _day_ventes(ticketing_config, products_data, day_str):
    """Somme des billets vendus dont le produit donne acces a `day_str`.

    Reproduit la logique de app.py:get_affluence pour day_ventes : un billet
    multi-jours compte une fois par jour d'acces (presence attendue).
    Couvre uniquement les billets references dans gh.ticketing (donc enceinte
    publique), pas les parking/camping.
    """
    total = 0
    for tc in ticketing_config or []:
        days_scope = tc.get("days")
        prods = tc.get("products") or []
        applies = (days_scope == "all") or (isinstance(days_scope, list) and day_str in days_scope)
        if not applies:
            continue
        for pname in prods:
            pdata = (products_data or {}).get(pname) or {}
            total += int(pdata.get("ventes") or 0)
    return total


def _index_freq_by_day(hist_doc):
    """Indexe historique_controle.data par jour 'YYYY-MM-DD' -> [records]."""
    out = {}
    if not hist_doc:
        return out
    for rec in hist_doc.get("data") or []:
        rd = rec.get("date")
        if isinstance(rd, str):
            key = rd[:10]
        elif hasattr(rd, "strftime"):
            key = rd.strftime("%Y-%m-%d")
        else:
            continue
        out.setdefault(key, []).append(rec)
    return out


def _max_present(records):
    if not records:
        return None
    vals = [int(r.get("present") or 0) for r in records if r.get("present") is not None]
    return max(vals) if vals else None


def _find_prev_param(db, event, year_int):
    """Charge le parametrages N-1 (annee la plus recente < N) avec tickets."""
    candidates = list(db["parametrages"].find(
        {"event": event, "tickets": {"$exists": True}},
        {"year": 1, "data.globalHoraires": 1, "data.race": 1, "tickets": 1, "_id": 0},
    ))
    for cand in sorted(candidates, key=lambda c: str(c.get("year", "")), reverse=True):
        try:
            if int(cand.get("year", "")) < year_int:
                return cand
        except (ValueError, TypeError):
            continue
    return None


def _find_hist_freq(db, event, year_int):
    """Charge historique_controle (type=frequentation, event, year=N).
    Pour year_int = annee precedente, on cherche le doc <= year_int (le plus
    recent qui soit anterieur ou egal).
    """
    docs = list(db["historique_controle"].find(
        {"type": "frequentation", "event": event},
        sort=[("year", -1)],
    ))
    for d in docs:
        try:
            if int(d.get("year", -1)) == year_int:
                return d
        except (ValueError, TypeError):
            continue
    return None


def _archive_tag(event, year_int):
    """Reconstitue le suffixe utilise par hsh_archive_and_purge (app.py:4615)."""
    import re as _re_local
    suffix = _re_local.sub(r'[^a-zA-Z0-9_-]', '_', str(event).strip())
    return suffix + "_" + str(int(year_int))


def _get_main_counter_id(db):
    """Retourne l'id du compteur principal (par defaut 'ENCEINTE GENERALE')
    depuis data_access._id='___GLOBAL___'.compteur_principal_id.
    Fallback : 1ere location selectionnee.
    """
    g = db["data_access"].find_one({"_id": "___GLOBAL___"})
    if not g:
        return None
    cid = g.get("compteur_principal_id")
    if cid:
        return str(cid)
    locs = g.get("locations_selectionnees") or []
    if locs and locs[0].get("id") is not None:
        return str(locs[0]["id"])
    return None


def _max_current_in_snapshots(db, coll_name, target_date_paris, location_id, event=None):
    """Max(current) sur les snapshots d'une journee Paris pour un compteur donne.

    Sert pour data_access (live) ou hsh_archive_compteurs_<tag> (archive).
    Le champ `current` est stocke en string -> caste en int defensivement.
    """
    day_start_paris = datetime.combine(target_date_paris, datetime.min.time(), tzinfo=TZ_PARIS)
    day_end_paris = day_start_paris + timedelta(days=1)
    day_start_utc = day_start_paris.astimezone(timezone.utc)
    day_end_utc = day_end_paris.astimezone(timezone.utc)
    q = {
        "timestamp": {"$gte": day_start_utc, "$lt": day_end_utc},
        "_id": {"$ne": "___GLOBAL___"},
    }
    if location_id:
        q["requested_location_id"] = str(location_id)
    if event:
        # Filtre par event si disponible (les vieux snapshots peuvent ne pas
        # avoir le champ; on tolere via $or).
        q["$or"] = [
            {"requested_event": event},
            {"requested_event": {"$exists": False}},
        ]
    max_v = None
    try:
        for s in db[coll_name].find(q, {"current": 1}):
            v = s.get("current")
            try:
                vi = int(v) if v not in (None, "") else None
            except (ValueError, TypeError):
                continue
            if vi is None:
                continue
            if max_v is None or vi > max_v:
                max_v = vi
    except Exception as e:
        logger.warning("Lecture %s a echoue : %s", coll_name, e)
    return max_v


def _get_pic_observed_for_day(db, event, year_int, target_date):
    """Chaine de fallback pour retrouver le pic constate d'un jour donne :

    1. historique_controle.type=frequentation -> max(present) du jour
       (post-archivage consolide via tools/controle/enbase_freq.py)
    2. data_access live snapshots -> max(current) du jour sur le compteur
       principal (event en cours OU termine mais pas encore archive)
    3. hsh_archive_compteurs_<archive_tag> -> idem (archivage admin fait,
       cf app.py:hsh_archive_and_purge)

    Retourne (pic_int, source_str) ou (None, None).
    """
    # Tier 1 : historique_controle
    hist = _find_hist_freq(db, event, year_int)
    if hist:
        freq = _index_freq_by_day(hist)
        pic = _max_present(freq.get(target_date.strftime("%Y-%m-%d")))
        if pic is not None and pic > 0:
            return pic, "historique_controle"

    main_loc_id = _get_main_counter_id(db)

    # Tier 2 : data_access live
    pic = _max_current_in_snapshots(db, "data_access", target_date, main_loc_id, event=event)
    if pic is not None and pic > 0:
        return pic, "data_access"

    # Tier 3 : archive
    archive_coll = "hsh_archive_compteurs_" + _archive_tag(event, year_int)
    try:
        existing = db.list_collection_names(filter={"name": archive_coll})
    except Exception:
        existing = []
    if archive_coll in existing:
        pic = _max_current_in_snapshots(db, archive_coll, target_date, main_loc_id, event=event)
        if pic is not None and pic > 0:
            return pic, "hsh_archive"

    return None, None


def _find_hist_freq_prev(db, event, year_int):
    """Pour comparaison N-1 : doc historique_controle frequentation < year_int
    le plus recent. On retourne aussi sa race_date (depuis lui-meme ou portes).
    """
    docs = list(db["historique_controle"].find(
        {"type": "frequentation", "event": event},
        sort=[("year", -1)],
    ))
    for d in docs:
        cy = d.get("year")
        try:
            if isinstance(cy, (int, float)) and int(cy) < year_int:
                race_raw = d.get("race")
                if not race_raw:
                    portes = db["historique_controle"].find_one(
                        {"type": "portes", "event": event, "year": cy},
                        {"_id": 0, "race": 1},
                    )
                    if portes:
                        race_raw = portes.get("race")
                return d, _parse_yyyy_mm_dd(race_raw)
        except (ValueError, TypeError):
            continue
    return None, None


def compute_attendance_block(db, event, year, now_utc=None):
    """Calcule le bloc Billetterie & Frequentation pour les 3 jours
    centres sur aujourd'hui : hier / aujourd'hui / demain.

    now_utc (optionnel, mode simulation) : datetime UTC aware. Defaut now().
    Retourne None si l'event n'a pas de parametrages billetterie publique.
    """
    if not event or year is None:
        return None
    try:
        year_int = int(year)
    except (TypeError, ValueError):
        return None

    doc = db["parametrages"].find_one({"event": event, "year": str(year)}, {"_id": 0}) \
        or db["parametrages"].find_one({"event": event, "year": year_int}, {"_id": 0})
    if not doc or "data" not in doc:
        return None
    gh = (doc.get("data") or {}).get("globalHoraires") or {}
    public_days_raw = gh.get("dates") or []
    ticketing_config = gh.get("ticketing") or []
    if not public_days_raw or not ticketing_config:
        return None
    public_dates = set()
    for d in public_days_raw:
        ds = d.get("date") if isinstance(d, dict) else d
        pd = _parse_yyyy_mm_dd(ds)
        if pd:
            public_dates.add(pd)
    if not public_dates:
        return None

    products_data = (doc.get("tickets") or {}).get("products") or {}
    race_date = _parse_yyyy_mm_dd((doc.get("data") or {}).get("race") or gh.get("race"))

    # N-1 parametrages + historique
    prev_param = _find_prev_param(db, event, year_int)
    prev_year_int = None
    prev_ticketing_config = []
    prev_products_data = {}
    prev_param_race_date = None
    if prev_param:
        try:
            prev_year_int = int(prev_param.get("year", ""))
        except (TypeError, ValueError):
            prev_year_int = None
        pgh = (prev_param.get("data") or {}).get("globalHoraires") or {}
        prev_ticketing_config = pgh.get("ticketing") or []
        prev_products_data = (prev_param.get("tickets") or {}).get("products") or {}
        prev_param_race_date = _parse_yyyy_mm_dd((prev_param.get("data") or {}).get("race") or pgh.get("race"))

    hist_n = _find_hist_freq(db, event, year_int)
    freq_n_by_day = _index_freq_by_day(hist_n)

    hist_prev_doc, hist_prev_race_date = (None, None)
    if prev_year_int:
        hist_prev_doc, hist_prev_race_date = _find_hist_freq_prev(db, event, year_int)
    freq_prev_by_day = _index_freq_by_day(hist_prev_doc)
    prev_race_ref = hist_prev_race_date or prev_param_race_date

    if now_utc is None:
        today = datetime.now(TZ_PARIS).date()
    else:
        today = now_utc.astimezone(TZ_PARIS).date()
    slots = []
    for offset, key, label_fr in (
        (-1, "yesterday", "Hier"),
        (0, "today", "Aujourd'hui"),
        (1, "tomorrow", "Demain"),
    ):
        d = today + timedelta(days=offset)
        d_str = d.strftime("%Y-%m-%d")
        is_public = d in public_dates

        slot = {
            "slot": key,
            "label": label_fr,
            "date": d_str,
            "is_public": is_public,
            "billets_vendus": None,
            "pic_observed": None,
            "pic_prev": None,
            "pic_projection": None,
            "delta_pct_vs_prev": None,
            "prev_year": prev_year_int,
            "prev_date": None,
        }
        if not is_public:
            slots.append(slot)
            continue

        # Billets vendus N pour ce jour (somme produits qui appliquent a d)
        slot["billets_vendus"] = _day_ventes(ticketing_config, products_data, d_str)

        # Pic observe N : chaine de fallback historique -> data_access -> archive.
        # Pour le jour d'aujourd'hui ou de demain on ne cherche pas (pas encore
        # de pic constate). Pour hier et avant, on tente de retrouver le pic.
        if offset <= 0:
            pic_val, pic_src = _get_pic_observed_for_day(db, event, year_int, d)
            slot["pic_observed"] = pic_val
            slot["pic_observed_source"] = pic_src  # debug : "historique_controle" / "data_access" / "hsh_archive"
        else:
            slot["pic_observed"] = None
            slot["pic_observed_source"] = None

        # Alignement N-1 sur date de course
        prev_aligned = None
        if race_date and prev_race_ref:
            prev_aligned = prev_race_ref + timedelta(days=(d - race_date).days)
        if prev_aligned:
            slot["prev_date"] = prev_aligned.strftime("%Y-%m-%d")

        # Pic N-1 jour-equivalent
        if prev_aligned:
            slot["pic_prev"] = _max_present(freq_prev_by_day.get(prev_aligned.strftime("%Y-%m-%d")))

        # Ventes N-1 jour-equivalent (utile pour la projection)
        ventes_prev = None
        if prev_aligned and prev_ticketing_config:
            ventes_prev = _day_ventes(prev_ticketing_config, prev_products_data,
                                      prev_aligned.strftime("%Y-%m-%d"))
            if ventes_prev == 0:
                ventes_prev = None

        # Pic projete = pic_prev * (billets_vendus_N / ventes_prev)
        if slot["pic_prev"] and ventes_prev and slot["billets_vendus"]:
            ratio = slot["billets_vendus"] / float(ventes_prev)
            slot["pic_projection"] = int(round(slot["pic_prev"] * ratio))

        # Delta % (pic principal vs pic_prev)
        ref = slot["pic_observed"] if slot["pic_observed"] else slot["pic_projection"]
        if ref and slot["pic_prev"]:
            slot["delta_pct_vs_prev"] = int(round(100 * (ref - slot["pic_prev"]) / slot["pic_prev"]))

        slots.append(slot)

    has_any_value = any(
        s.get("billets_vendus") or s.get("pic_observed") or s.get("pic_prev") or s.get("pic_projection")
        for s in slots
    )
    if not has_any_value:
        return None

    return {
        "event": event,
        "year": year_int,
        "prev_year": prev_year_int,
        "race_date": race_date.isoformat() if race_date else None,
        "prev_race_date": prev_race_ref.isoformat() if prev_race_ref else None,
        "slots": slots,
    }


# ----------------------------------------------------------------------------
# Helpers : prochaines vignettes timetable
# ----------------------------------------------------------------------------

def _combine_timetable_dt(date_str, time_str):
    """Combine date YYYY-MM-DD + heure HH:MM en datetime Europe/Paris aware.

    Retourne None si time_str est vide, "TBC" ou non parsable.
    """
    if not date_str or not time_str:
        return None
    s = str(time_str).strip()
    if not s or s.upper() == "TBC":
        return None
    try:
        dt = datetime.strptime(str(date_str) + " " + s, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return dt.replace(tzinfo=TZ_PARIS)


def _serialize_upcoming(item, dt_paris):
    """Forme compacte d'une vignette pour le prompt + l'UI."""
    activity = (item.get("activity") or "").strip()
    place = (item.get("place") or "").strip()
    return {
        "event": item.get("__event"),
        "year": item.get("__year"),
        "datetime": dt_paris.isoformat(),
        "date": dt_paris.strftime("%Y-%m-%d"),
        "time": dt_paris.strftime("%H:%M"),
        "activity": activity,
        "place": place,
        "category": item.get("category") or "",
        "department": item.get("department") or "",
        "preparation_checked": item.get("preparation_checked") or "",
        "duration": item.get("duration") or "",
        "remark": (item.get("remark") or "").strip()[:240],
    }


def _scan_timetable_doc(doc, now_paris, end_paris):
    """Extrait les vignettes d'un doc timetable dans la fenetre [now, end]."""
    out = []
    data = (doc or {}).get("data") or {}
    if not isinstance(data, dict):
        return out
    event = doc.get("event")
    year = doc.get("year")
    today_str = now_paris.strftime("%Y-%m-%d")
    end_str = end_paris.strftime("%Y-%m-%d")
    for date_str, items in data.items():
        if not isinstance(items, list):
            continue
        # Court-circuit : on saute les jours hors fenetre [today, end]
        if date_str < today_str or date_str > end_str:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            dt = _combine_timetable_dt(date_str, item.get("start"))
            if dt is None or dt < now_paris or dt > end_paris:
                continue
            tagged = dict(item)
            tagged["__event"] = event
            tagged["__year"] = year
            out.append(_serialize_upcoming(tagged, dt))
    return out


# Patterns de regroupement pour la factorisation des prochaines 24h.
# Chaque tuple : (regex sur activity, cle de groupement, libelle synthetique)
import re as _re

_UPCOMING_GROUP_PATTERNS = [
    (_re.compile(r"^Ouverture\s+Parking\s+", _re.IGNORECASE), "parking", "Ouverture parkings"),
    (_re.compile(r"^Ouverture\s+Porte\s+", _re.IGNORECASE), "porte", "Ouverture portes"),
    (_re.compile(r"^Ouverture\s+Tribune\s+", _re.IGNORECASE), "tribune", "Ouverture tribunes"),
    (_re.compile(r"^Ouverture\s+(Aire|AA)\s+", _re.IGNORECASE), "aire", "Ouverture aires d'accueil"),
    (_re.compile(r"^Ouverture\s+Camping\s+", _re.IGNORECASE), "camping", "Ouverture campings"),
    (_re.compile(r"^Fermeture\s+Parking\s+", _re.IGNORECASE), "fermeture_parking", "Fermeture parkings"),
    (_re.compile(r"^Fermeture\s+Porte\s+", _re.IGNORECASE), "fermeture_porte", "Fermeture portes"),
]


def _upcoming_group_key(activity):
    """Retourne (group_key, group_label) ou (None, None) si l'item ne se
    groupe pas (ouverture publique, briefings, depart course, etc.)."""
    if not activity:
        return None, None
    for rgx, key, label in _UPCOMING_GROUP_PATTERNS:
        if rgx.match(activity):
            return key, label
    return None, None


def _factorize_upcoming(items, min_to_factorize=2):
    """Regroupe les vignettes du meme creneau (datetime exact) qui matchent
    un meme pattern (parkings, portes, tribunes...). Quand au moins
    `min_to_factorize` items sont regroupables, on les remplace par un item
    synthetique avec le decompte et la liste compactee des lieux. Sinon on
    conserve les items individuels.
    """
    if not items:
        return items
    # Indexe les items par (datetime, group_key) pour reperer les groupes
    indexed = []
    for it in items:
        gk, gl = _upcoming_group_key(it.get("activity"))
        indexed.append({"item": it, "gk": gk, "gl": gl, "consumed": False})

    out = []
    for i, entry in enumerate(indexed):
        if entry["consumed"]:
            continue
        if entry["gk"] is None:
            out.append(entry["item"])
            entry["consumed"] = True
            continue
        # Cherche les frères (meme datetime + meme group_key)
        siblings = [entry]
        for j in range(i + 1, len(indexed)):
            other = indexed[j]
            if other["consumed"] or other["gk"] != entry["gk"]:
                continue
            if other["item"].get("datetime") == entry["item"].get("datetime"):
                siblings.append(other)
        if len(siblings) < min_to_factorize:
            out.append(entry["item"])
            entry["consumed"] = True
            continue
        # Factorisation
        for s in siblings:
            s["consumed"] = True
        first = entry["item"]
        # Recupere les noms de lieux (place ou suffix de l'activity)
        places = []
        for s in siblings:
            it = s["item"]
            place = it.get("place") or ""
            if not place:
                # extrait le suffixe apres "Ouverture Parking " etc.
                act = it.get("activity") or ""
                rgx = _UPCOMING_GROUP_PATTERNS[
                    next(idx for idx, (_, key, _l) in enumerate(_UPCOMING_GROUP_PATTERNS) if key == entry["gk"])
                ][0]
                place = rgx.sub("", act).strip()
            if place:
                places.append(place)
        # Dedupe en preservant ordre
        seen = set()
        unique_places = []
        for p in places:
            k = p.lower()
            if k not in seen:
                seen.add(k)
                unique_places.append(p)
        place_str = ", ".join(unique_places[:6])
        if len(unique_places) > 6:
            place_str += " (+" + str(len(unique_places) - 6) + ")"
        synth = dict(first)
        synth["activity"] = entry["gl"] + " (×" + str(len(siblings)) + ")"
        synth["place"] = place_str
        synth["is_factorized"] = True
        synth["factor_count"] = len(siblings)
        out.append(synth)
    return out


def get_upcoming_timetable(db, event, year, hours=24, now_utc=None):
    """Retourne la liste des vignettes timetable dans les prochaines `hours`,
    avec factorisation des ouvertures/fermetures simultanees (parkings,
    portes, tribunes...).

    Si event/year sont fournis, filtre sur ce doc unique. Sinon, scanne
    tous les docs timetable (mode "Tous evenements").
    Trie par datetime croissant.
    now_utc (optionnel, mode simulation) : datetime UTC aware. Defaut now().
    """
    if now_utc is None:
        now_paris = datetime.now(TZ_PARIS)
    else:
        now_paris = now_utc.astimezone(TZ_PARIS)
    end_paris = now_paris + timedelta(hours=hours)
    upcoming = []
    col = db["timetable"]
    if event and year is not None:
        # Le champ year est stocke en string dans timetable.
        doc = col.find_one({"event": event, "year": str(year)})
        if not doc:
            doc = col.find_one({"event": event, "year": int(year)})
        if doc:
            upcoming.extend(_scan_timetable_doc(doc, now_paris, end_paris))
    else:
        for doc in col.find({}):
            upcoming.extend(_scan_timetable_doc(doc, now_paris, end_paris))
    upcoming.sort(key=lambda e: e.get("datetime") or "")
    upcoming = _factorize_upcoming(upcoming)
    # Plafond defensif : on borne a 50 vignettes pour limiter le prompt.
    return upcoming[:50]


# ----------------------------------------------------------------------------
# Calcul des KPIs
# ----------------------------------------------------------------------------

def compute_kpis(db, event, year, ts_start, ts_end):
    """Aggrege les fiches pcorg pour la periode et retourne un dict de KPIs.

    ts_start / ts_end : datetime aware (UTC).
    Si event/year sont None, l'agregation porte sur tous les evenements.
    """
    col = db[PCORG_COLLECTION]
    base = {"ts": {"$gte": ts_start, "$lte": ts_end}}
    if event:
        base["event"] = event
    if year is not None:
        base["year"] = int(year)
    total = col.count_documents(base)
    closed = col.count_documents({**base, "status_code": 10})
    open_ = total - closed

    def _counts(field):
        pipe = [
            {"$match": base},
            {"$group": {"_id": "$" + field, "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
        ]
        return list(col.aggregate(pipe))

    by_category = {}
    for r in _counts("category"):
        key = r.get("_id") or "_none"
        by_category[str(key)] = int(r["n"])

    by_event = []
    pipe_event = [
        {"$match": base},
        {"$group": {"_id": {"event": "$event", "year": "$year"}, "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]
    for r in col.aggregate(pipe_event):
        eid = r.get("_id") or {}
        ev = eid.get("event") or "_none"
        yr = eid.get("year")
        by_event.append({"event": str(ev), "year": yr, "count": int(r["n"])})

    by_urgency = {}
    for r in _counts("niveau_urgence"):
        key = r.get("_id") or "_none"
        by_urgency[str(key)] = int(r["n"])

    by_operator = []
    for r in _counts("operator")[:5]:
        if r.get("_id"):
            by_operator.append({"name": str(r["_id"]), "count": int(r["n"])})

    top_zones = []
    pipe_zones = [
        {"$match": base},
        {"$group": {"_id": "$area.desc", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 10},
    ]
    for r in col.aggregate(pipe_zones):
        if r.get("_id"):
            top_zones.append({"desc": str(r["_id"]), "count": int(r["n"])})

    top_sous = []
    pipe_sous = [
        {"$match": base},
        {"$group": {"_id": "$content_category.sous_classification", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 10},
    ]
    for r in col.aggregate(pipe_sous):
        if r.get("_id"):
            top_sous.append({"label": str(r["_id"]), "count": int(r["n"])})

    # Duree moyenne d'intervention (en minutes) sur fiches cloturees.
    avg_duration_min = None
    pipe_dur = [
        {"$match": {**base, "status_code": 10, "close_ts": {"$ne": None}}},
        {"$project": {
            "dur_ms": {"$subtract": ["$close_ts", "$ts"]},
        }},
        {"$group": {"_id": None, "avg_ms": {"$avg": "$dur_ms"}, "n": {"$sum": 1}}},
    ]
    dur_res = list(col.aggregate(pipe_dur))
    if dur_res and dur_res[0].get("avg_ms") is not None:
        avg_duration_min = round(float(dur_res[0]["avg_ms"]) / 60000.0, 1)

    return {
        "total": total,
        "open": open_,
        "closed": closed,
        "by_category": by_category,
        "by_urgency": by_urgency,
        "by_event": by_event,
        "top_zones": top_zones,
        "top_sous_classifications": top_sous,
        "top_operators": by_operator,
        "avg_duration_min": avg_duration_min,
    }


def compute_compact_kpis(db, event, year, ts_start, ts_end):
    """Version reduite des KPIs pour les fenetres comparatives.

    Retourne juste {total, closed, by_category, by_urgency} pour limiter
    la verbosite du prompt et le temps de calcul.
    """
    col = db[PCORG_COLLECTION]
    base = {"ts": {"$gte": ts_start, "$lte": ts_end}}
    if event:
        base["event"] = event
    if year is not None:
        base["year"] = int(year)
    total = col.count_documents(base)
    if total == 0:
        return {"total": 0, "closed": 0, "by_category": {}, "by_urgency": {}}
    closed = col.count_documents({**base, "status_code": 10})

    def _counts(field):
        return list(col.aggregate([
            {"$match": base},
            {"$group": {"_id": "$" + field, "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
        ]))

    by_category = {}
    for r in _counts("category"):
        key = r.get("_id") or "_none"
        by_category[str(key)] = int(r["n"])
    by_urgency = {}
    for r in _counts("niveau_urgence"):
        key = r.get("_id") or "_none"
        by_urgency[str(key)] = int(r["n"])
    return {"total": total, "closed": closed, "by_category": by_category, "by_urgency": by_urgency}


def compute_comparisons(db, event, year, ts_start, ts_end):
    """Calcule les KPIs comparatifs pour 2 fenetres de reference.

    Retourne un dict :
    {
      "prev_period": {
        "label": "Periode precedente (24h juste avant)",
        "period_start": iso, "period_end": iso, "kpis": {...}
      } ou None,
      "prev_year_aligned": {
        "label": "Annee N-1 jour-equivalent (course 4 mai 2025)",
        "period_start": iso, "period_end": iso, "kpis": {...},
        "race_dt_n": iso, "race_dt_prev": iso, "year_prev": int
      } ou None
    }
    """
    out = {"prev_period": None, "prev_year_aligned": None}

    # Fenetre period precedente : decalage = duree de la fenetre.
    duration = ts_end - ts_start
    if duration.total_seconds() > 0:
        prev_start = ts_start - duration
        prev_end = ts_end - duration
        kpis = compute_compact_kpis(db, event, year, prev_start, prev_end)
        out["prev_period"] = {
            "label": "Periode precedente (meme duree juste avant)",
            "period_start": prev_start.isoformat(),
            "period_end": prev_end.isoformat(),
            "kpis": kpis,
        }

    # N-1 aligne sur date de course : necessite event + year + parametrages OK
    # pour les deux annees.
    if event and year is not None:
        race_dt_n = _load_race_dt(db, event, int(year))
        race_dt_prev = _load_race_dt(db, event, int(year) - 1)
        if race_dt_n and race_dt_prev:
            prev_start, prev_end = _aligned_prev_year_window(
                ts_start, ts_end, race_dt_n, race_dt_prev,
            )
            if prev_start and prev_end:
                kpis = compute_compact_kpis(db, event, int(year) - 1, prev_start, prev_end)
                out["prev_year_aligned"] = {
                    "label": "Annee precedente, meme position par rapport a la course",
                    "period_start": prev_start.isoformat(),
                    "period_end": prev_end.isoformat(),
                    "kpis": kpis,
                    "race_dt_n": race_dt_n.isoformat(),
                    "race_dt_prev": race_dt_prev.isoformat(),
                    "year_prev": int(year) - 1,
                }
    return out


# ----------------------------------------------------------------------------
# Selection des fiches a envoyer a Claude
# ----------------------------------------------------------------------------

def _truncate(text, n=TEXT_TRUNCATE_CHARS):
    if not text:
        return ""
    s = str(text)
    if len(s) <= n:
        return s
    return s[:n] + " [...]"


def _iso(v):
    """Convertit datetime/date en ISO string ; passe through les autres types."""
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    return v


def _json_default(o):
    """Fallback de serialisation JSON pour les types non standards."""
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "isoformat"):
        try:
            return o.isoformat()
        except Exception:
            return str(o)
    return str(o)


def _serialize_fiche(doc):
    """Forme compacte d'une fiche pour le prompt."""
    cc = doc.get("content_category") or {}
    area = doc.get("area") or {}
    history = doc.get("comment_history") or []
    if isinstance(history, list) and len(history) > COMMENTS_KEEP_LAST:
        history = history[-COMMENTS_KEEP_LAST:]
    history_short = []
    for h in history:
        if not isinstance(h, dict):
            continue
        history_short.append({
            "ts": _iso(h.get("ts")),
            "operator": h.get("operator"),
            "text": _truncate(h.get("text"), 300),
        })
    return {
        "id": str(doc.get("_id", "")),
        "event": doc.get("event"),
        "year": doc.get("year"),
        "ts": _iso(doc.get("ts")),
        "close_ts": _iso(doc.get("close_ts")),
        "category": doc.get("category"),
        "sous_classification": cc.get("sous_classification"),
        "urgence": doc.get("niveau_urgence"),
        "is_incident": bool(doc.get("is_incident")),
        "status": "ferme" if doc.get("status_code") == 10 else "ouvert",
        "operator": doc.get("operator"),
        "zone": area.get("desc"),
        "text": _truncate(doc.get("text_full") or doc.get("text") or ""),
        "comments": history_short,
    }


def _serialize_fiche_compact(doc):
    """Forme tres compacte d'une fiche pour les comparaisons N-1.

    Pas de comments, pas de operator, texte tronque court : on veut
    essentiellement les categories, urgences, zones et nature des incidents
    pour permettre a Claude de detecter des recurrences.
    """
    cc = doc.get("content_category") or {}
    area = doc.get("area") or {}
    return {
        "ts": _iso(doc.get("ts")),
        "category": doc.get("category"),
        "sous_classification": cc.get("sous_classification"),
        "urgence": doc.get("niveau_urgence"),
        "is_incident": bool(doc.get("is_incident")),
        "zone": area.get("desc"),
        "text": _truncate(doc.get("text_full") or doc.get("text") or "", 280),
    }


def select_fiches_n_minus_1(db, event, year_prev, ts_start, ts_end, max_fiches=25):
    """Echantillon compact des fiches N-1 (jour-equivalent) pour les
    recommandations preventives.

    Priorise les fiches majeures (urgence EU/UA, is_incident) puis complete
    avec un echantillon des autres categories pour rester representatif.
    Forme tres compacte (texte court, sans commentaires).
    """
    if not event or year_prev is None:
        return []
    col = db[PCORG_COLLECTION]
    base = {
        "event": event,
        "year": int(year_prev),
        "ts": {"$gte": ts_start, "$lte": ts_end},
    }
    majors = list(col.find({
        **base,
        "$or": [
            {"niveau_urgence": {"$in": ["EU", "UA"]}},
            {"is_incident": True},
        ],
    }).sort("ts", ASCENDING))
    major_ids = {d["_id"] for d in majors}
    quota = max(0, max_fiches - len(majors))
    others = []
    if quota > 0:
        others = list(
            col.find({**base, "_id": {"$nin": list(major_ids)}})
               .sort("ts", DESCENDING)
               .limit(quota)
        )
    selected = (majors + others)[:max_fiches]
    return [_serialize_fiche_compact(d) for d in selected]


def select_fiches_for_prompt(db, event, year, ts_start, ts_end, max_fiches=DEFAULT_MAX_FICHES):
    """Retourne (fiches_serialized, total_in_period, truncated_bool).

    Priorise les fiches majeures (urgence EU/UA ou is_incident) qui sont
    toujours incluses ; complete avec les autres dans la limite max_fiches.
    Si event/year sont None, la selection porte sur tous les evenements.
    """
    col = db[PCORG_COLLECTION]
    base = {"ts": {"$gte": ts_start, "$lte": ts_end}}
    if event:
        base["event"] = event
    if year is not None:
        base["year"] = int(year)
    total = col.count_documents(base)

    major_filter = {
        **base,
        "$or": [
            {"niveau_urgence": {"$in": ["EU", "UA"]}},
            {"is_incident": True},
        ],
    }
    majors = list(col.find(major_filter).sort("ts", ASCENDING))
    major_ids = {d["_id"] for d in majors}

    remaining_quota = max(0, max_fiches - len(majors))
    others = []
    if remaining_quota > 0:
        others = list(
            col.find({**base, "_id": {"$nin": list(major_ids)}})
               .sort("ts", DESCENDING)
               .limit(remaining_quota)
        )

    selected = majors + others
    truncated = total > len(selected)
    return [_serialize_fiche(d) for d in selected], total, truncated


# ----------------------------------------------------------------------------
# Construction du prompt
# ----------------------------------------------------------------------------

def build_prompts(event, year, ts_start, ts_end, kpis, fiches, truncated,
                  comparisons=None, upcoming=None, n1_retro=None, extra_focus_note=None,
                  door_reinforcement=None):
    """Retourne (system_prompt, user_prompt) en francais.

    comparisons (optionnel) : dict produit par compute_comparisons.
    upcoming (optionnel) : liste produite par get_upcoming_timetable.
    n1_retro (optionnel) : dict produit par get_or_build_n1_retrospective.
    extra_focus_note (optionnel) : consigne supplementaire (par ex. focus nuit
       pour le rapport matinal) ajoutee au user prompt.
    door_reinforcement (optionnel) : dict produit par
       pcorg_doors_analysis.compute_door_reinforcement.
    """
    system = (
        "Tu es un analyste operationnel pour un PC Organisation d'evenement "
        "(festival, course automobile). On te fournit des KPIs agreges et un "
        "echantillon de fiches d'intervention. Tu produis un compte-rendu "
        "factuel, en francais, destine aux managers en debrief.\n"
        "\n"
        "Contraintes strictes :\n"
        "- Reponds UNIQUEMENT par un objet JSON valide, sans texte avant ou "
        "apres, sans bloc markdown encadrant le JSON.\n"
        "- L'objet contient EXACTEMENT ces 9 cles : synthese, faits_marquants, "
        "secours, securite, technique, flux, fourriere, recommandations, "
        "prochaines_24h.\n"
        "\n"
        "Mise en forme du contenu de chaque cle (markdown leger autorise) :\n"
        "- Utilise des SAUTS DE LIGNE pour aerer : separe les paragraphes "
        "avec une ligne vide (\\n\\n).\n"
        "- Utilise des LISTES A PUCES pour les enumerations : chaque puce "
        "commence par '- ' (tiret + espace) en debut de ligne. Une puce "
        "par ligne, separees par \\n.\n"
        "- Utilise du GRAS sur les elements cles : entoure-les de **double "
        "asterisques**. Par exemple **Porte Nord** ou **renfort recommande**.\n"
        "- Garde les phrases courtes et l'expression operationnelle.\n"
        "- Pas de titres markdown (#, ##), pas de tableaux, pas de blocs "
        "code, pas d'images.\n"
        "\n"
        "Contenu attendu par cle :\n"
        "- synthese : vision macro de la periode analysee en 3 a 5 phrases "
        "courtes (1 paragraphe). Doit imperativement situer le volume "
        "d'activite, la tonalite generale (calme / dense / critique) et "
        "exploiter les KPIs comparatifs s'ils sont fournis (variation par "
        "rapport a la veille meme creneau, et par rapport a l'edition "
        "precedente). Quelques mots-cles peuvent etre en **gras** pour les "
        "chiffres ou tendances importantes. PAS de liste a puces ici, "
        "c'est un paragraphe synthetique.\n"
        "- faits_marquants : 3 a 6 puces qui mettent en avant les "
        "evenements vraiment notables uniquement (incident reel, fait "
        "inhabituel, situation critique, anomalie). PAS un resume general. "
        "'RAS' si rien de notable.\n"
        "- secours : synthese des fiches PCO.Secours, en 2 a 4 paragraphes "
        "courts ou en puces si volume important.\n"
        "- securite : synthese des fiches PCO.Securite, PCS.Surete et "
        "PCS.Information.\n"
        "- technique : synthese des fiches PCO.Technique.\n"
        "- flux : synthese des fiches PCO.Flux (gestion des flux pietons "
        "et vehicules, regulation). Si une analyse de renforts portes "
        "t'est fournie, NE LES REDETAILLE PAS ici (le tableau dedie les "
        "affiche deja). Tu peux mentionner brievement les zones de "
        "vigilance attendues.\n"
        "- fourriere : synthese des fiches PCO.Fourriere.\n"
        "- recommandations : OBLIGATOIREMENT en LISTE A PUCES (une "
        "recommandation par puce, debutant par '- '). Chaque puce : action "
        "concrete + court rationnel. Si une 'Note retrospective de "
        "l'edition precedente' t'est fournie, EXPLOITE-LA pour rappeler "
        "EXPLICITEMENT les erreurs a ne pas reproduire dans les 24h qui "
        "arrivent : commence ces puces par par exemple **A ne pas "
        "reproduire** ou **L'an passe sur ce meme creneau** suivi du "
        "constat puis de l'action. NE redetaille PAS les renforts portes "
        "(le tableau dedie suffit), tu peux juste y faire reference d'une "
        "phrase. Pas de numerotation '1.', '2.' ; uniquement des puces.\n"
        "- prochaines_24h : mini-briefing en 5 a 8 puces qui FACTORISE et "
        "MET EN AVANT les jalons strategiques pour un circuit automobile. "
        "Priorise dans l'ordre :\n"
        "  1. Depart et arrivee de course, warm-up, qualifs (=> moments "
        "operationnels critiques).\n"
        "  2. Ouverture au public, ouvertures massives portes/parkings/"
        "tribunes (=> regrouper sur la meme heure : '8 portes', "
        "'7 parkings', etc., et nommer les 3-4 plus strategiques entre "
        "parentheses ; eviter d'enumerer toute la liste).\n"
        "  3. Pics de frequentation attendus (si signales par l'analyse "
        "renforts portes).\n"
        "  4. Briefings importants, inspections piste, mise en place "
        "dispositifs (VRI, ambulances, safety car).\n"
        "  5. Reunions de direction de course, jury.\n"
        "Ignore les micro-jalons techniques (panneaux 1', 2', 3' decompte, "
        "klaxons, ouvertures ponctuelles de portail piste durant les "
        "courses) sauf s'ils impactent le dispositif sol. Donne les "
        "heures (format 14h30). 'Aucun jalon planifie dans les 24 "
        "prochaines heures.' si liste vide.\n"
        "\n"
        "Comparaisons :\n"
        "- Si des KPIs comparatifs te sont fournis (periode precedente ou "
        "edition precedente alignee sur la date de course), exploite-les "
        "dans 'faits_marquants' et les sections thematiques pour situer le "
        "volume (par ex. 'volume comparable a la veille meme creneau', "
        "'forte hausse par rapport a l'edition precedente').\n"
        "- Pour la comparaison annee precedente, utilise 'edition "
        "precedente' ou 'annee precedente' (jamais 'N-1', 'jour-equivalent' "
        "ou autre jargon).\n"
        "- Si la difference est < 10%, parle de 'volume comparable'.\n"
        "\n"
        "Style et vocabulaire :\n"
        "- Phrases redigees pour des operationnels : ne JAMAIS citer de "
        "nom de champ ou variable (is_incident, status_code, "
        "niveau_urgence, content_category). Utilise 'incident', 'fiche "
        "cloturee', 'urgence absolue', etc.\n"
        "- Pas d'identifiants techniques (uuid, sql_id) dans les phrases.\n"
        "- N'invente jamais de chiffres : appuie-toi uniquement sur les "
        "donnees fournies.\n"
        "- Pas de guillemets typographiques courbes : uniquement des "
        "apostrophes et guillemets droits.\n"
    )

    period_iso_start = ts_start.isoformat()
    period_iso_end = ts_end.isoformat()
    scope_label = "tous evenements confondus"
    if event and year is not None:
        scope_label = str(event) + " " + str(year)
    elif event:
        scope_label = str(event) + " (toutes annees)"
    elif year is not None:
        scope_label = "annee " + str(year) + " (tous evenements)"
    parts = [
        "Contexte :\n"
        "- Perimetre : " + scope_label + "\n"
        "- Periode : " + period_iso_start + " --> " + period_iso_end + "\n"
        "- Echantillon tronque : " + ("oui" if truncated else "non") + "\n"
        "\n"
        "KPIs (periode courante) :\n"
        + json.dumps(kpis, ensure_ascii=False, indent=2, default=_json_default)
    ]

    if comparisons:
        prev = comparisons.get("prev_period")
        if prev and prev.get("kpis", {}).get("total", 0) > 0:
            parts.append(
                "\n\nKPIs comparatifs - " + prev["label"] + " :\n"
                "- Fenetre : " + prev["period_start"] + " --> " + prev["period_end"] + "\n"
                + json.dumps(prev["kpis"], ensure_ascii=False, indent=2, default=_json_default)
            )
        prev_year = comparisons.get("prev_year_aligned")
        if prev_year and prev_year.get("kpis", {}).get("total", 0) > 0:
            parts.append(
                "\n\nKPIs comparatifs - " + prev_year["label"] + " :\n"
                "- Fenetre annee precedente : " + prev_year["period_start"] + " --> " + prev_year["period_end"] + "\n"
                "- Date course annee courante : " + prev_year["race_dt_n"] + "\n"
                "- Date course annee precedente : " + prev_year["race_dt_prev"] + "\n"
                + json.dumps(prev_year["kpis"], ensure_ascii=False, indent=2, default=_json_default)
            )
    if n1_retro and n1_retro.get("text"):
        parts.append(
            "\n\nNote retrospective de l'edition precedente (analyse "
            "synthetique deja produite a partir des fiches N-1 du jour-"
            "equivalent ; sert uniquement a alimenter la section "
            "'recommandations') :\n---\n"
            + n1_retro["text"]
            + "\n---"
        )

    if door_reinforcement and door_reinforcement.get("recommendations"):
        # Forme compacte pour le prompt : on garde l'essentiel.
        compact = [
            {
                "porte": r["family_label"],
                "creneau": r["slot_label_n"],
                "criticite": r["criticite"],
                "pic_n1_scans": r["n1_scan_count"],
                "is_pic_top3": r["is_top3_pic"],
                "incidents_n1": r["n1_fiches_count"],
                "incidents_par_categorie": r["n1_fiches_by_category"],
                "raison": r["reason"],
            }
            for r in door_reinforcement["recommendations"]
        ]
        parts.append(
            "\n\nAnalyse 'Renforts conseilles sur les portes' (basee sur "
            "le pic N-1 jour-equivalent et les fiches d'incident N-1 "
            "mentionnant la porte). A exploiter dans 'flux' et "
            "'recommandations' uniquement, sans inventer de portes :\n"
            + json.dumps(compact, ensure_ascii=False, indent=2, default=_json_default)
        )

    if upcoming is not None:
        if upcoming:
            parts.append(
                "\n\nVignettes timetable des prochaines 24 heures ("
                + str(len(upcoming)) + " jalon(s)) :\n"
                + json.dumps(upcoming, ensure_ascii=False, indent=2, default=_json_default)
            )
        else:
            parts.append("\n\nVignettes timetable des prochaines 24 heures : aucune.")

    parts.append(
        "\n\nFiches (" + str(len(fiches)) + " sur " + str(kpis.get("total", 0)) + " au total) :\n"
        + json.dumps(fiches, ensure_ascii=False, indent=2, default=_json_default)
    )

    if extra_focus_note:
        parts.append(
            "\n\nConsigne complementaire (a respecter en priorite) :\n"
            + str(extra_focus_note).strip()
        )

    parts.append("\n\nProduis le JSON demande.")
    return system, "".join(parts)


# ----------------------------------------------------------------------------
# Retrospective N-1 (premier appel Claude, mis en cache)
# ----------------------------------------------------------------------------

def _build_retro_prompts(event, year_prev, ts_start, ts_end, kpis, fiches):
    """Prompts dedies a la retrospective N-1.

    Sortie demandee : texte clair court (pas de JSON), 4 paragraphes
    titres, exploitable comme contexte par le prompt principal.
    """
    system = (
        "Tu es un analyste retrospectif d'evenements (festival, course "
        "automobile). On te fournit les KPIs et un echantillon de fiches "
        "d'incidents de l'edition precedente sur le meme creneau "
        "operationnel (alignement par rapport a la date de course). "
        "Ton objectif unique : produire une note retrospective qui aide "
        "les operateurs de l'edition courante a NE PAS REPETER les "
        "memes erreurs.\n"
        "\n"
        "Contraintes :\n"
        "- Reponse en francais, texte clair (pas de markdown, pas de "
        "JSON, pas de listes a puces).\n"
        "- 4 paragraphes courts, dans l'ordre, chacun introduit par un "
        "titre suivi de deux points :\n"
        "  Volume et tonalite : (volume d'activite ressenti, repartition "
        "globale)\n"
        "  Incidents marquants : (les vrais evenements notables, pas "
        "une liste exhaustive)\n"
        "  Zones et recurrences : (zones et types d'incidents qui "
        "ressortent, situations qui ont surpris l'equipe)\n"
        "  Lecons a retenir : (3 a 5 points concrets a anticiper ou "
        "verifier sur l'edition courante)\n"
        "- Chaque paragraphe : 1 a 4 phrases courtes maximum.\n"
        "- N'invente jamais de chiffres, ne cite pas de noms de variables "
        "(is_incident, status_code, niveau_urgence, content_category, "
        "uuid). Utilise un vocabulaire naturel : 'incident', 'urgence "
        "absolue', 'fiche cloturee'.\n"
        "- Pas de guillemets typographiques courbes : uniquement des "
        "apostrophes et guillemets droits.\n"
        "- Si le volume est faible ou les fiches peu parlantes, dis-le "
        "honnetement, ne sur-interprete pas.\n"
    )
    user = (
        "Contexte :\n"
        "- Evenement : " + str(event) + "\n"
        "- Annee analysee : " + str(year_prev) + "\n"
        "- Fenetre alignee : " + ts_start.isoformat() + " --> " + ts_end.isoformat() + "\n"
        "\n"
        "KPIs (edition precedente, periode alignee) :\n"
        + json.dumps(kpis, ensure_ascii=False, indent=2, default=_json_default)
        + "\n\nEchantillon de fiches (" + str(len(fiches)) + " sur "
        + str(kpis.get("total", 0)) + " au total) :\n"
        + json.dumps(fiches, ensure_ascii=False, indent=2, default=_json_default)
        + "\n\nProduis la note retrospective demandee."
    )
    return system, user


def _call_claude_text(system_prompt, user_prompt, on_progress=None):
    """Variante streaming de call_claude qui retourne juste le texte brut + usage.

    Sert pour la retro N-1 (note synthetique courte, max_tokens=1024).
    """
    raw_text, usage = _claude_stream_request(
        system_prompt, user_prompt, max_tokens=1024, on_progress=on_progress,
    )
    return raw_text.strip(), usage


def _retro_cache_key(event, year_prev, ts_start, ts_end):
    return {
        "event": event,
        "year_prev": int(year_prev),
        "period_start": ts_start.isoformat() if hasattr(ts_start, "isoformat") else str(ts_start),
        "period_end": ts_end.isoformat() if hasattr(ts_end, "isoformat") else str(ts_end),
    }


def get_or_build_n1_retrospective(db, event, year_prev, ts_start_prev, ts_end_prev, on_progress=None):
    """Retourne la note retrospective N-1 (texte court) pour une fenetre alignee.

    1. Cherche un cache dans `pcorg_n1_retros` sur (event, year_prev, fenetre).
    2. Sinon, calcule KPIs + selectionne un echantillon de ~80 fiches N-1, fait
       un appel Claude dedie, sauve en cache et retourne.

    Retourne None en cas d'echec silencieux (pas de fiches, ou echec API) :
    le resume principal continue sans contexte N-1.
    """
    if not event or year_prev is None or not ts_start_prev or not ts_end_prev:
        return None
    key = _retro_cache_key(event, year_prev, ts_start_prev, ts_end_prev)

    try:
        db[N1_RETROS_COLLECTION].create_index(
            [("event", ASCENDING), ("year_prev", ASCENDING), ("period_start", ASCENDING), ("period_end", ASCENDING)],
            name="cache_key",
            unique=True,
        )
    except Exception:
        pass

    cached = db[N1_RETROS_COLLECTION].find_one(key)
    if cached and cached.get("text"):
        return {
            "text": cached["text"],
            "kpis": cached.get("kpis") or {},
            "fiches_count": cached.get("fiches_count") or 0,
            "from_cache": True,
            "cached_at": cached.get("created_at").isoformat() if hasattr(cached.get("created_at"), "isoformat") else None,
            "model": cached.get("model"),
            "usage": cached.get("usage") or {},
        }

    kpis = compute_compact_kpis(db, event, int(year_prev), ts_start_prev, ts_end_prev)
    if kpis.get("total", 0) == 0:
        return None
    fiches, _, _ = select_fiches_for_prompt(
        db, event, int(year_prev), ts_start_prev, ts_end_prev, max_fiches=80,
    )
    if not fiches:
        return None

    system, user = _build_retro_prompts(event, int(year_prev), ts_start_prev, ts_end_prev, kpis, fiches)
    try:
        text, usage = _call_claude_text(system, user, on_progress=on_progress)
    except ClaudeError as e:
        logger.warning("Retro N-1 echouee : %s", e)
        return None
    if not text:
        return None

    doc = dict(key)
    doc["text"] = text
    doc["kpis"] = kpis
    doc["fiches_count"] = len(fiches)
    doc["model"] = CLAUDE_MODEL
    doc["usage"] = usage
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db[N1_RETROS_COLLECTION].insert_one(doc)
    except Exception as e:
        logger.warning("Cache retro N-1 : insert_one a echoue : %s", e)
    return {
        "text": text,
        "kpis": kpis,
        "fiches_count": len(fiches),
        "from_cache": False,
        "cached_at": doc["created_at"].isoformat(),
        "model": CLAUDE_MODEL,
        "usage": usage,
    }


# ----------------------------------------------------------------------------
# Appel API Claude
# ----------------------------------------------------------------------------

class ClaudeError(Exception):
    """Erreur lors de l'appel a l'API Anthropic."""


def _claude_stream_request(system_prompt, user_prompt, max_tokens, on_progress=None):
    """Effectue un appel streaming a l'API Anthropic et retourne (text, usage).

    Le streaming evite les timeouts sur les reponses longues : tant que Claude
    envoie des chunks, la connexion reste vivante. Le timeout
    CLAUDE_TIMEOUT_SECONDS s'applique alors uniquement entre 2 chunks.

    on_progress (optionnel) : callable(text_so_far, output_tokens_so_far)
    appele a intervalles reguliers pour permettre un affichage en temps reel.
    """
    if not ANTHROPIC_API_KEY:
        raise ClaudeError("ANTHROPIC_API_KEY non configuree")

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": int(max_tokens),
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "stream": True,
    }

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers=headers,
            json=body,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            stream=True,
        )
    except requests.exceptions.RequestException as e:
        logger.warning("Claude API injoignable: %s", e)
        raise ClaudeError("claude_unreachable")

    if resp.status_code >= 400:
        try:
            snippet = (resp.text or "")[:500]
        except Exception:
            snippet = ""
        logger.warning("Claude API HTTP %s : %s", resp.status_code, snippet)
        raise ClaudeError("claude_http_" + str(resp.status_code))

    raw_text = ""
    usage_in = 0
    usage_out = 0
    last_progress_len = 0
    try:
        # chunk_size=None pour ne pas bufferiser les chunks SSE plus que
        # necessaire (chaque event SSE = quelques bytes a quelques centaines).
        for line in resp.iter_lines(decode_unicode=False, chunk_size=None):
            if not line:
                continue
            try:
                line_s = line.decode("utf-8", errors="replace")
            except Exception:
                continue
            if not line_s.startswith("data:"):
                continue
            data_str = line_s[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue
            try:
                evt = json.loads(data_str)
            except (json.JSONDecodeError, ValueError):
                continue
            etype = evt.get("type")
            if etype == "content_block_delta":
                delta = evt.get("delta") or {}
                if delta.get("type") == "text_delta":
                    raw_text += delta.get("text") or ""
                    if on_progress and (len(raw_text) - last_progress_len >= 400):
                        last_progress_len = len(raw_text)
                        try:
                            on_progress(raw_text, usage_out)
                        except Exception:
                            pass
            elif etype == "message_start":
                msg = evt.get("message") or {}
                usage = msg.get("usage") or {}
                usage_in = int(usage.get("input_tokens") or 0)
                usage_out = int(usage.get("output_tokens") or 0)
            elif etype == "message_delta":
                usage = evt.get("usage") or {}
                if usage.get("output_tokens"):
                    usage_out = int(usage["output_tokens"])
            elif etype == "error":
                err = evt.get("error") or {}
                raise ClaudeError(
                    "claude_stream_error: " + str(err.get("type", "unknown"))
                    + ": " + str(err.get("message", ""))[:200]
                )
    except requests.exceptions.RequestException as e:
        logger.warning("Claude stream interrompu : %s", e)
        if not raw_text:
            raise ClaudeError("claude_stream_interrupted")
        # Sinon, on garde le texte partiel et on continue.

    if on_progress and last_progress_len < len(raw_text):
        try:
            on_progress(raw_text, usage_out)
        except Exception:
            pass

    return raw_text, {"input_tokens": usage_in, "output_tokens": usage_out}


def call_claude(system_prompt, user_prompt, on_progress=None):
    """Appelle l'API Claude en streaming, retourne (sections_dict, raw_text, usage).

    Si le retour n'est pas du JSON parsable, sections_dict est None et
    raw_text contient la reponse brute. Leve ClaudeError pour les erreurs.
    """
    raw_text, usage_clean = _claude_stream_request(
        system_prompt, user_prompt, CLAUDE_MAX_TOKENS, on_progress=on_progress,
    )
    sections = _parse_sections(raw_text)
    return sections, raw_text, usage_clean


def _parse_sections(raw_text):
    """Tente de parser un JSON dans raw_text et de retourner un dict de sections.

    Robustesse :
    - Tolere les blocs markdown encadrants ```json ... ```
    - Si json.loads echoue (typiquement reponse Claude tronquee a max_tokens),
      tente une recuperation par regex des paires "cle": "valeur" completes
      + recuperation de la cle finale tronquee si possible.

    Retourne un dict (potentiellement partiel) ou None si rien d'exploitable.
    """
    if not raw_text:
        return None
    txt = raw_text.strip()
    # Tolere un eventuel bloc markdown ```json ... ```
    if txt.startswith("```"):
        nl = txt.find("\n")
        if nl != -1:
            txt = txt[nl + 1:]
        if txt.endswith("```"):
            txt = txt[:-3]
        txt = txt.strip()

    # Premier essai : JSON propre
    try:
        data = json.loads(txt)
        if isinstance(data, dict):
            return _normalize_sections(data)
    except (ValueError, TypeError):
        pass

    # Fallback : extraction par regex pour les reponses tronquees.
    # Match "key": "value..." avec valeur complete (chaine fermee).
    # NB: [a-z_0-9] (et non [a-z_]) pour matcher 'prochaines_24h'.
    import re as _re_local
    pattern = r'"([a-z_0-9]+)"\s*:\s*"((?:[^"\\]|\\.)*)"'
    pairs = _re_local.findall(pattern, txt, _re_local.DOTALL)
    recovered = {}
    for k, v in pairs:
        if k in SECTION_KEYS:
            # Decode les escapes JSON dans la valeur.
            try:
                recovered[k] = json.loads('"' + v + '"')
            except (ValueError, TypeError):
                recovered[k] = v

    # Tentative de recuperation de la cle finale tronquee :
    # 1. Trouver TOUTES les positions de '"<key>": "' dans le texte
    # 2. Prendre la derniere occurence
    # 3. Lire la valeur a partir de la fin du match jusqu'a un " non
    #    echappe ou la fin du texte
    pat_key_open = r'"([a-z_0-9]+)"\s*:\s*"'
    key_matches = list(_re_local.finditer(pat_key_open, txt))
    if key_matches:
        last_match = key_matches[-1]
        last_key = last_match.group(1)
        if last_key in SECTION_KEYS and last_key not in recovered:
            start = last_match.end()
            val_chars = []
            i = start
            closed = False
            while i < len(txt):
                c = txt[i]
                if c == "\\" and i + 1 < len(txt):
                    val_chars.append(c)
                    val_chars.append(txt[i + 1])
                    i += 2
                    continue
                if c == '"':
                    closed = True
                    break
                val_chars.append(c)
                i += 1
            if not closed:
                raw_val = "".join(val_chars)
                try:
                    clean_val = json.loads('"' + raw_val + '"')
                except (ValueError, TypeError):
                    clean_val = raw_val
                recovered[last_key] = (clean_val.rstrip() + " [reponse tronquee]").strip()

    if not recovered:
        return None
    return _normalize_sections(recovered)


def _normalize_sections(data):
    """Convertit un dict brut en dict ne contenant que les SECTION_KEYS,
    chaque valeur etant une chaine non vide ou ''."""
    out = {}
    for key in SECTION_KEYS:
        v = data.get(key)
        out[key] = str(v).strip() if v is not None else ""
    return out


# ----------------------------------------------------------------------------
# Persistance MongoDB
# ----------------------------------------------------------------------------

def save_summary(db, event, year, ts_start, ts_end, created_by_email, created_by_name,
                 kpis, fiches_count, truncated, sections, raw_text, usage,
                 comparisons=None, upcoming=None, attendance=None, n1_retro=None,
                 door_reinforcement=None):
    """Insere un document de resume et retourne le doc complet.

    event/year peuvent etre None (resume "tous evenements").
    """
    _ensure_indexes(db)
    doc = {
        "_id": uuid.uuid4().hex,
        "event": event,
        "year": int(year) if year is not None else None,
        "period_start": ts_start,
        "period_end": ts_end,
        "created_at": datetime.now(timezone.utc),
        "created_by": created_by_email or "",
        "created_by_name": created_by_name or "",
        "fiches_count": int(fiches_count),
        "truncated": bool(truncated),
        "kpis": kpis,
        "comparisons": comparisons or {},
        "upcoming": upcoming or [],
        "attendance": attendance or None,
        "n1_retro": n1_retro or None,
        "door_reinforcement": door_reinforcement or None,
        "sections": sections,
        "raw_text": raw_text,
        "model": CLAUDE_MODEL,
        "usage": usage or {},
    }
    db[SUMMARIES_COLLECTION].insert_one(doc)
    return doc


def list_summaries(db, event=None, year=None, limit=50):
    _ensure_indexes(db)
    q = {}
    if event:
        q["event"] = event
    if year is not None:
        try:
            q["year"] = int(year)
        except (TypeError, ValueError):
            pass
    proj = {
        "_id": 1, "event": 1, "year": 1, "period_start": 1, "period_end": 1,
        "created_at": 1, "created_by": 1, "created_by_name": 1,
        "fiches_count": 1, "truncated": 1, "model": 1,
    }
    cur = db[SUMMARIES_COLLECTION].find(q, proj).sort("created_at", DESCENDING).limit(int(limit))
    return [_serialize_summary(d, light=True) for d in cur]


def get_summary(db, summary_id):
    _ensure_indexes(db)
    doc = db[SUMMARIES_COLLECTION].find_one({"_id": summary_id})
    if not doc:
        return None
    return _serialize_summary(doc, light=False)


def delete_summary(db, summary_id):
    res = db[SUMMARIES_COLLECTION].delete_one({"_id": summary_id})
    return res.deleted_count > 0


def _serialize_summary(doc, light=True):
    def _iso(v):
        if isinstance(v, datetime):
            return v.isoformat()
        return v
    out = {
        "id": str(doc.get("_id", "")),
        "event": doc.get("event"),
        "year": doc.get("year"),
        "period_start": _iso(doc.get("period_start")),
        "period_end": _iso(doc.get("period_end")),
        "created_at": _iso(doc.get("created_at")),
        "created_by": doc.get("created_by"),
        "created_by_name": doc.get("created_by_name"),
        "fiches_count": doc.get("fiches_count"),
        "truncated": doc.get("truncated"),
        "model": doc.get("model"),
    }
    if not light:
        out["kpis"] = doc.get("kpis") or {}
        out["comparisons"] = doc.get("comparisons") or {}
        out["upcoming"] = doc.get("upcoming") or []
        out["attendance"] = doc.get("attendance") or None
        out["n1_retro"] = doc.get("n1_retro") or None
        out["door_reinforcement"] = doc.get("door_reinforcement") or None
        out["sections"] = doc.get("sections") or {}
        out["raw_text"] = doc.get("raw_text") or ""
        out["usage"] = doc.get("usage") or {}
    return out


# ----------------------------------------------------------------------------
# Orchestration principale
# ----------------------------------------------------------------------------

# ----------------------------------------------------------------------------
# Preferences "Rapport matinal" (opt-in par utilisateur)
# ----------------------------------------------------------------------------

def get_morning_report_prefs(db):
    """Retourne {enabled_user_ids: [str], updated_at, updated_by}."""
    doc = db[COCKPIT_SETTINGS_COLLECTION].find_one({"_id": MORNING_REPORT_SETTINGS_ID}) or {}
    raw_ids = doc.get("enabled_user_ids") or []
    ids = [str(x) for x in raw_ids]
    return {
        "enabled_user_ids": ids,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


def set_morning_report_recipient(db, user_id, enabled, updated_by_email=None):
    """Active ou desactive l'inscription d'un utilisateur au rapport matinal."""
    from bson.objectid import ObjectId
    try:
        oid = ObjectId(str(user_id))
    except Exception:
        raise ValueError("user_id invalide")
    op = "$addToSet" if enabled else "$pull"
    db[COCKPIT_SETTINGS_COLLECTION].update_one(
        {"_id": MORNING_REPORT_SETTINGS_ID},
        {
            op: {"enabled_user_ids": oid},
            "$set": {
                "updated_at": datetime.now(timezone.utc),
                "updated_by": updated_by_email or "",
            },
        },
        upsert=True,
    )


def get_morning_report_emails(db):
    """Resout les user_ids inscrits en liste d'emails uniques (cockpit-only)."""
    from bson.objectid import ObjectId
    prefs = get_morning_report_prefs(db)
    if not prefs["enabled_user_ids"]:
        return []
    oids = []
    for uid in prefs["enabled_user_ids"]:
        try:
            oids.append(ObjectId(uid))
        except Exception:
            continue
    if not oids:
        return []
    cur = db["users"].find(
        {
            "_id": {"$in": oids},
            "roles_by_app.cockpit": {"$exists": True},
            "email": {"$exists": True, "$ne": ""},
        },
        {"email": 1},
    )
    out = []
    seen = set()
    for u in cur:
        e = (u.get("email") or "").strip()
        if e and e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out


# ----------------------------------------------------------------------------
# Detection automatique de l'event/year actif (pour le rapport matinal)
# ----------------------------------------------------------------------------

def _parse_iso_dt(raw):
    if not raw:
        return None
    try:
        if isinstance(raw, datetime):
            dt = raw
        else:
            s = str(raw).strip().replace("Z", "+00:00")
            if "T" not in s and " " not in s and len(s) <= 10:
                dt = datetime.fromisoformat(s)
            else:
                dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_PARIS)
    return dt.astimezone(timezone.utc)


DEFAULT_FALLBACK_EVENT = "SAISON"


def _saison_fallback(db, now_utc):
    """Retourne (event='SAISON', year) avec l'annee courante si parametrages
    existent, sinon le SAISON le plus recent dispo, sinon ('SAISON', annee
    courante) sans verification.
    """
    current_year = now_utc.astimezone(TZ_PARIS).year
    doc = db["parametrages"].find_one(
        {"event": DEFAULT_FALLBACK_EVENT, "year": str(current_year)},
        {"_id": 1},
    )
    if not doc:
        doc = db["parametrages"].find_one(
            {"event": DEFAULT_FALLBACK_EVENT, "year": current_year},
            {"_id": 1},
        )
    if doc:
        return (DEFAULT_FALLBACK_EVENT, current_year)
    # Cherche le SAISON le plus recent dispo
    candidates = list(db["parametrages"].find(
        {"event": DEFAULT_FALLBACK_EVENT}, {"year": 1},
    ))
    years = []
    for c in candidates:
        try:
            years.append(int(c.get("year")))
        except (TypeError, ValueError):
            continue
    if years:
        return (DEFAULT_FALLBACK_EVENT, max(years))
    return (DEFAULT_FALLBACK_EVENT, current_year)


def detect_active_event(db, now_utc=None):
    """Devine l'evenement actuellement actif d'apres parametrages.

    Logique alignee sur le bloc "live status" (static/js/main.js) :
    un evenement est actif entre globalHoraires.montage.start et
    globalHoraires.demontage.end.

    - 1 seul candidat actif -> on le prend.
    - Plusieurs candidats actifs (chevauchement) -> on prend celui dont
      la date de course (globalHoraires.race) est la plus proche de now
      (la course la plus "chaude").
    - Aucun candidat actif -> fallback SAISON annee courante.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    candidates = []
    for doc in db["parametrages"].find(
        {},
        {"event": 1, "year": 1,
         "data.globalHoraires.montage": 1,
         "data.globalHoraires.demontage": 1,
         "data.globalHoraires.race": 1,
         "data.race": 1},
    ):
        gh = (doc.get("data") or {}).get("globalHoraires") or {}
        m_dt = _parse_iso_dt((gh.get("montage") or {}).get("start"))
        d_dt = _parse_iso_dt((gh.get("demontage") or {}).get("end"))
        if not m_dt or not d_dt:
            continue
        if not (m_dt <= now_utc <= d_dt):
            continue
        try:
            yr = int(doc.get("year"))
        except (TypeError, ValueError):
            continue
        ev = doc.get("event")
        if not ev:
            continue
        race_dt = _parse_iso_dt((doc.get("data") or {}).get("race") or gh.get("race"))
        candidates.append((ev, yr, race_dt))

    if not candidates:
        return _saison_fallback(db, now_utc)

    if len(candidates) == 1:
        ev, yr, _ = candidates[0]
        return (ev, yr)

    # Plusieurs candidats : on prend celui dont la course est la plus proche
    # de now (en valeur absolue). Les candidats sans race connue sont penalises.
    def _score(c):
        _, _, race_dt = c
        if race_dt is None:
            return float("inf")
        return abs((race_dt - now_utc).total_seconds())

    candidates.sort(key=_score)
    ev, yr, _ = candidates[0]
    return (ev, yr)


def generate_period_summary(db, event, year, ts_start, ts_end, created_by_email, created_by_name,
                             extra_focus_note=None, as_of_utc=None, on_progress=None):
    """Calcule KPIs + comparaisons + prochaines 24h + billetterie + retro N-1, appelle Claude.

    extra_focus_note : consigne supplementaire a injecter dans le user prompt
    (par ex. focus nuit pour le rapport matinal).
    as_of_utc (optionnel, mode test) : datetime UTC aware qui simule le 'now'
    pour les blocs upcoming / attendance / door_reinforcement. Permet de
    tester le rapport hors periode d'evenement en se placant virtuellement
    pendant une edition passee.
    on_progress (optionnel) : callback(text_so_far, output_tokens_so_far)
    appele a intervalles reguliers pendant le streaming Claude. Utile pour
    afficher la progression cote CLI ou pour streamer vers une UI.
    """
    kpis = compute_kpis(db, event, year, ts_start, ts_end)
    comparisons = compute_comparisons(db, event, year, ts_start, ts_end)
    upcoming = get_upcoming_timetable(db, event, year, hours=24, now_utc=as_of_utc)
    attendance = compute_attendance_block(db, event, year, now_utc=as_of_utc)
    # Renforts portes : import lazy pour eviter dependance circulaire au boot.
    door_reinforcement = None
    try:
        import pcorg_doors_analysis
        door_reinforcement = pcorg_doors_analysis.compute_door_reinforcement(
            db, event, year, now_utc=as_of_utc,
        )
    except Exception as e:
        logger.warning("Renforts portes : echec calcul (%s)", e)

    # Premier appel Claude : retrospective N-1 sur la fenetre alignee.
    # Cache en collection pcorg_n1_retros, echec silencieux.
    n1_retro = None
    py = (comparisons or {}).get("prev_year_aligned")
    if py and py.get("kpis", {}).get("total", 0) > 0:
        try:
            ts_prev_start = datetime.fromisoformat(py["period_start"])
            ts_prev_end = datetime.fromisoformat(py["period_end"])
            n1_retro = get_or_build_n1_retrospective(
                db, event, py.get("year_prev"), ts_prev_start, ts_prev_end,
                on_progress=on_progress,
            )
        except Exception as e:
            logger.warning("Retro N-1 : preparation echouee : %s", e)
            n1_retro = None

    # Cas "aucune fiche" : on appelle quand meme Claude UNIQUEMENT pour
    # produire le mini-briefing prochaines_24h s'il y a des jalons.
    # Sinon on court-circuite tout.
    if kpis["total"] == 0:
        if not upcoming:
            sections = {k: "RAS" for k in SECTION_KEYS}
            sections["prochaines_24h"] = "Aucun jalon planifie dans les 24 prochaines heures."
            return save_summary(
                db, event, year, ts_start, ts_end, created_by_email, created_by_name,
                kpis, 0, False, sections, "", {"input_tokens": 0, "output_tokens": 0},
                comparisons=comparisons, upcoming=upcoming, attendance=attendance, n1_retro=n1_retro,
                door_reinforcement=door_reinforcement,
            )
        # Sinon, appel Claude minimal pour la section prochaines_24h.
        system, user = build_prompts(
            event, year, ts_start, ts_end, kpis, [], False,
            comparisons=comparisons, upcoming=upcoming, n1_retro=n1_retro,
            extra_focus_note=extra_focus_note,
            door_reinforcement=door_reinforcement,
        )
        sections, raw_text, usage = call_claude(system, user, on_progress=on_progress)
        if sections is None:
            sections = {k: "RAS" for k in SECTION_KEYS}
            sections["prochaines_24h"] = "Reponse Claude non parsable."
        # Force RAS pour les sections sans fiches.
        for k in SECTION_KEYS:
            if k != "prochaines_24h" and not sections.get(k):
                sections[k] = "RAS"
        return save_summary(
            db, event, year, ts_start, ts_end, created_by_email, created_by_name,
            kpis, 0, False, sections, raw_text, usage,
            comparisons=comparisons, upcoming=upcoming, attendance=attendance, n1_retro=n1_retro,
            door_reinforcement=door_reinforcement,
        )

    fiches, total, truncated = select_fiches_for_prompt(db, event, year, ts_start, ts_end)
    system, user = build_prompts(
        event, year, ts_start, ts_end, kpis, fiches, truncated,
        comparisons=comparisons, upcoming=upcoming, n1_retro=n1_retro,
        extra_focus_note=extra_focus_note,
        door_reinforcement=door_reinforcement,
    )
    sections, raw_text, usage = call_claude(system, user, on_progress=on_progress)
    if sections is None:
        # JSON non parsable : on conserve le texte brut dans faits_marquants
        # et on remplit les autres en RAS pour ne rien perdre.
        sections = {k: "" for k in SECTION_KEYS}
        sections["faits_marquants"] = raw_text or "Reponse Claude non parsable."

    return save_summary(
        db, event, year, ts_start, ts_end, created_by_email, created_by_name,
        kpis, len(fiches), truncated, sections, raw_text, usage,
        comparisons=comparisons, upcoming=upcoming, attendance=attendance, n1_retro=n1_retro,
        door_reinforcement=door_reinforcement,
    )
