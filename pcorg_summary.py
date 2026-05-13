"""Resume de periode des fiches PC Organisation via l'API Claude.

Module helpers pur (pas de blueprint Flask) : les routes vivent dans app.py
a cote des autres routes /api/pcorg/* pour rester coherent.

Pattern d'appel HTTP externe calque sur traffic.py (Waze) et routing.py (Valhalla).
"""

import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "16384"))
CLAUDE_MAX_TOKENS_RETRY = int(os.getenv("CLAUDE_MAX_TOKENS_RETRY", "32000"))

# Retry HTTP : 3 essais sur erreurs reseau et codes 429/503/529 (overloaded).
RETRY_MAX_ATTEMPTS = int(os.getenv("CLAUDE_RETRY_MAX_ATTEMPTS", "3"))
RETRY_BACKOFF_BASE_S = float(os.getenv("CLAUDE_RETRY_BACKOFF_BASE_S", "1.0"))
RETRYABLE_HTTP_CODES = {429, 503, 529}

# Whitelist des modeles autorises en override par requete. La valeur par defaut
# vient de CLAUDE_MODEL ; cette whitelist permet juste les A/B tests sans
# changer la conf serveur.
ALLOWED_MODELS = {
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-haiku-4-5",
}

# Tarif approximatif (USD par 1M tokens) pour estimation de cout.
# Source : claude.com/pricing. A reverifier si Anthropic revise.
# Valeurs cache : creation = +25% du prix input, lecture cache = -90% du prix input.
MODEL_PRICING_USD_PER_MTOK = {
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0},
    "claude-sonnet-4-5": {"input": 3.0,  "output": 15.0},
    "claude-opus-4-7":   {"input": 15.0, "output": 75.0},
    "claude-opus-4-6":   {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5":  {"input": 1.0,  "output": 5.0},
}


SUMMARIES_COLLECTION = "pcorg_summaries"
PCORG_COLLECTION = "pcorg"
N1_RETROS_COLLECTION = "pcorg_n1_retros"
MORNING_REPORT_SETTINGS_ID = "morning_report"
COCKPIT_SETTINGS_COLLECTION = "cockpit_settings"

# Version du system prompt de base (incrementer manuellement quand on refond
# le prompt). Permet de filtrer le dataset d'apprentissage par generation de
# prompt -- utile si on veut exclure les vieux samples post-refonte.
PROMPT_VERSION = 1

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


def _record_hour_str(rec):
    """Extrait l'heure 'HHhMM' (Paris) d'un record historique_controle.

    Cherche dans 'hour' (str 'HH:MM'), puis dans 'date' (str ISO ou datetime).
    Les records frequentation sont stockes en heure locale Paris dans le doc
    historique_controle, donc pas de conversion fuseau a faire.
    """
    h = rec.get("hour")
    if not h:
        rd = rec.get("date")
        if isinstance(rd, str) and len(rd) >= 16:
            h = rd[11:16]
        elif hasattr(rd, "strftime"):
            h = rd.strftime("%H:%M")
    if not h or ":" not in str(h):
        return None
    parts = str(h).split(":")
    return parts[0].zfill(2) + "h" + parts[1].zfill(2)[:2]


def _max_present_with_hour(records):
    """Variante de _max_present qui retourne aussi l'heure du record max.

    Retourne (val_int, hour_str_HHhMM) ou (None, None).
    En cas d'egalite, garde le PREMIER record vu (heure la plus precoce).
    """
    if not records:
        return None, None
    best_p = None
    best_hour = None
    for r in records:
        p = r.get("present")
        if p is None:
            continue
        try:
            pi = int(p)
        except (ValueError, TypeError):
            continue
        if best_p is None or pi > best_p:
            best_p = pi
            best_hour = _record_hour_str(r)
    return best_p, best_hour


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

    Retourne (max_int, hour_str_HHhMM) avec l'heure (Europe/Paris) du snapshot
    qui a fourni le max. (None, None) si rien.
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
    max_ts = None
    try:
        for s in db[coll_name].find(q, {"current": 1, "timestamp": 1}):
            v = s.get("current")
            try:
                vi = int(v) if v not in (None, "") else None
            except (ValueError, TypeError):
                continue
            if vi is None:
                continue
            if max_v is None or vi > max_v:
                max_v = vi
                max_ts = s.get("timestamp")
    except Exception as e:
        logger.warning("Lecture %s a echoue : %s", coll_name, e)

    hour_str = None
    if max_ts is not None:
        if isinstance(max_ts, datetime):
            local = max_ts if max_ts.tzinfo else max_ts.replace(tzinfo=timezone.utc)
            hour_str = local.astimezone(TZ_PARIS).strftime("%Hh%M")
    return max_v, hour_str


def _get_pic_observed_for_day(db, event, year_int, target_date):
    """Chaine de fallback pour retrouver le pic constate d'un jour donne :

    1. historique_controle.type=frequentation -> max(present) du jour
       (post-archivage consolide via tools/controle/enbase_freq.py)
    2. data_access live snapshots -> max(current) du jour sur le compteur
       principal (event en cours OU termine mais pas encore archive)
    3. hsh_archive_compteurs_<archive_tag> -> idem (archivage admin fait,
       cf app.py:hsh_archive_and_purge)

    Retourne (pic_int, source_str, hour_str_HHhMM) ou (None, None, None).
    """
    # Tier 1 : historique_controle
    hist = _find_hist_freq(db, event, year_int)
    if hist:
        freq = _index_freq_by_day(hist)
        pic, hour = _max_present_with_hour(freq.get(target_date.strftime("%Y-%m-%d")))
        if pic is not None and pic > 0:
            return pic, "historique_controle", hour

    main_loc_id = _get_main_counter_id(db)

    # Tier 2 : data_access live
    pic, hour = _max_current_in_snapshots(db, "data_access", target_date, main_loc_id, event=event)
    if pic is not None and pic > 0:
        return pic, "data_access", hour

    # Tier 3 : archive
    archive_coll = "hsh_archive_compteurs_" + _archive_tag(event, year_int)
    try:
        existing = db.list_collection_names(filter={"name": archive_coll})
    except Exception:
        existing = []
    if archive_coll in existing:
        pic, hour = _max_current_in_snapshots(db, archive_coll, target_date, main_loc_id, event=event)
        if pic is not None and pic > 0:
            return pic, "hsh_archive", hour

    return None, None, None


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
        logger.info("attendance_block : skip (event=%s year=%s)", event, year)
        return None
    try:
        year_int = int(year)
    except (TypeError, ValueError):
        logger.info("attendance_block : skip (year non castable : %s)", year)
        return None

    doc = db["parametrages"].find_one({"event": event, "year": str(year)}, {"_id": 0}) \
        or db["parametrages"].find_one({"event": event, "year": year_int}, {"_id": 0})
    if not doc or "data" not in doc:
        logger.info("attendance_block : skip (parametrages introuvable pour %s %s)", event, year)
        return None
    gh = (doc.get("data") or {}).get("globalHoraires") or {}
    public_days_raw = gh.get("dates") or []
    ticketing_config = gh.get("ticketing") or []
    if not public_days_raw:
        logger.info("attendance_block : skip (%s %s -> aucune date publique configuree)", event, year)
        return None
    has_ticketing = bool(ticketing_config)
    public_dates = set()
    for d in public_days_raw:
        ds = d.get("date") if isinstance(d, dict) else d
        pd = _parse_yyyy_mm_dd(ds)
        if pd:
            public_dates.add(pd)
    if not public_dates:
        logger.info("attendance_block : skip (%s %s -> aucune date publique parsable)", event, year)
        return None
    if not has_ticketing:
        # Ticketing config absent : on continue mais billets_vendus et
        # pic_projection seront None. Pic_observed (data_access / archive)
        # et pic_prev (historique_controle N-1) restent accessibles, ce qui
        # est l'essentiel pour le rapport matinal.
        logger.info(
            "attendance_block : %s %s -> pas de ticketing config, "
            "calcul pics seulement (billets/projection a None)",
            event, year,
        )

    products_data = (doc.get("tickets") or {}).get("products") or {}
    race_date = _parse_yyyy_mm_dd((doc.get("data") or {}).get("race") or gh.get("race"))

    # N-1 parametrages (utile pour billets_vendus_prev / pic_projection).
    # Optionnel : si l'event ne porte pas le champ tickets (ex. GPF), on
    # continuera quand meme avec les pics N-1 issus de historique_controle.
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

    # historique_controle.frequentation N-1 : tente toujours, meme sans
    # prev_param (cas events non ticketises). C'est la source la plus
    # fiable pour la frequentation et la date de course de l'edition
    # precedente (fallback sur historique_controle.portes pour la race).
    hist_prev_doc, hist_prev_race_date = _find_hist_freq_prev(db, event, year_int)
    if hist_prev_doc and prev_year_int is None:
        try:
            prev_year_int = int(hist_prev_doc.get("year"))
        except (TypeError, ValueError):
            pass
    freq_prev_by_day = _index_freq_by_day(hist_prev_doc)
    prev_race_ref = hist_prev_race_date or prev_param_race_date

    if now_utc is None:
        now_paris = datetime.now(TZ_PARIS)
    else:
        now_paris = now_utc.astimezone(TZ_PARIS)
    today = now_paris.date()
    # En-dessous de cette heure (Europe/Paris), pic_observed du jour J n'est
    # pas significatif (pic typique evenement sport entre 14h et 17h). Sert a
    # eviter qu'a 7h du matin le rapport annonce un faux pic minuscule.
    TODAY_PIC_CUTOFF_HOUR = 18
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
            "pic_observed_hour": None,    # heure 'HHhMM' Paris du pic constate
            "pic_prev": None,
            "pic_prev_hour": None,        # heure 'HHhMM' Paris du pic N-1 (~ heure attendue du pic du jour)
            "pic_projection": None,
            "delta_pct_vs_prev": None,
            "prev_year": prev_year_int,
            "prev_date": None,
        }
        if not is_public:
            slots.append(slot)
            continue

        # Billets vendus N pour ce jour (somme produits qui appliquent a d).
        # Reste a None si pas de ticketing_config (event non ticketise via
        # parametrages, ex. GPF) -> distingue 'pas de donnee' vs '0 vente'.
        if has_ticketing:
            slot["billets_vendus"] = _day_ventes(ticketing_config, products_data, d_str)

        # Pic observe N : chaine de fallback historique -> data_access -> archive.
        # Hier (offset=-1) : toujours calcule. Aujourd'hui (offset=0) : seulement
        # si on a depasse l'heure typique du pic (sinon faux pic minuscule).
        # Demain (offset=+1) : jamais (futur).
        skip_today_pic = (offset == 0 and now_paris.hour < TODAY_PIC_CUTOFF_HOUR)
        if offset <= 0 and not skip_today_pic:
            pic_val, pic_src, pic_hour = _get_pic_observed_for_day(db, event, year_int, d)
            slot["pic_observed"] = pic_val
            slot["pic_observed_hour"] = pic_hour
            slot["pic_observed_source"] = pic_src  # debug : "historique_controle" / "data_access" / "hsh_archive"
        else:
            slot["pic_observed"] = None
            slot["pic_observed_source"] = "skipped_too_early" if skip_today_pic else None

        # Alignement N-1 sur date de course
        prev_aligned = None
        if race_date and prev_race_ref:
            prev_aligned = prev_race_ref + timedelta(days=(d - race_date).days)
        if prev_aligned:
            slot["prev_date"] = prev_aligned.strftime("%Y-%m-%d")

        # Pic N-1 jour-equivalent + heure (= heure attendue du pic du jour N).
        if prev_aligned:
            pp_val, pp_hour = _max_present_with_hour(
                freq_prev_by_day.get(prev_aligned.strftime("%Y-%m-%d"))
            )
            slot["pic_prev"] = pp_val
            slot["pic_prev_hour"] = pp_hour

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
        logger.info(
            "attendance_block : %s %s -> 3 slots calcules mais TOUS vides "
            "(public_dates=%d, prev_year=%s, race_date=%s, prev_race_ref=%s, "
            "freq_prev_doc=%s) -> bloc non emis",
            event, year, len(public_dates), prev_year_int, race_date, prev_race_ref,
            "trouve" if hist_prev_doc else "absent",
        )
        return None

    # Log de diagnostic : quel slot a quoi (utile pour comprendre pourquoi
    # le rapport matinal ne mentionne pas certains pics).
    diag = []
    for s in slots:
        diag.append("%s(public=%s billets=%s pic=%s@%s pic_prev=%s@%s proj=%s d=%s%%)" % (
            s.get("slot"),
            s.get("is_public"),
            s.get("billets_vendus"),
            s.get("pic_observed"),
            s.get("pic_observed_hour") or "-",
            s.get("pic_prev"),
            s.get("pic_prev_hour") or "-",
            s.get("pic_projection"),
            s.get("delta_pct_vs_prev") if s.get("delta_pct_vs_prev") is not None else "-",
        ))
    logger.info("attendance_block : %s %s -> %s", event, year, " | ".join(diag))

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


def _iso_paris(v):
    """Convertit datetime/string ISO en ISO localise sur Europe/Paris.

    Pour les valeurs envoyees a Claude : on veut que tous les datetimes
    apparaissent dans le fuseau Paris pour eviter que Claude prenne 05h UTC
    et le rende litteralement comme '05h' au lieu de '07h' Paris.
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        dt = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ_PARIS).isoformat()
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return s
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(TZ_PARIS).isoformat()
        except (ValueError, TypeError):
            return s
    return str(v)


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
            "ts": _iso_paris(h.get("ts")),
            "operator": h.get("operator"),
            "text": _truncate(h.get("text"), 300),
        })
    return {
        "id": str(doc.get("_id", "")),
        "event": doc.get("event"),
        "year": doc.get("year"),
        "ts": _iso_paris(doc.get("ts")),
        "close_ts": _iso_paris(doc.get("close_ts")),
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
        "ts": _iso_paris(doc.get("ts")),
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
    """Retourne (fiches_serialized, total_in_period, truncated_bool, detail).

    Priorise les fiches majeures (urgence EU/UA ou is_incident) qui sont
    toujours incluses ; complete avec les autres dans la limite max_fiches.
    Si event/year sont None, la selection porte sur tous les evenements.

    detail : dict {total, majors, others, cut, majors_capped, by_urgency}
    pour debug / telemetrie (logue + expose dans la reponse API).
    'majors_capped' = True si > max_fiches majeures (= aucune fiche normale
    n'a pu etre incluse, signal d'alerte volumetrique).
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

    # Si trop de majeures, on tronque a max_fiches et on perd le contexte normal.
    selected = (majors + others)[:max_fiches]
    truncated = total > len(selected)

    by_urg = {}
    for d in selected:
        u = d.get("niveau_urgence") or "_none"
        by_urg[str(u)] = by_urg.get(str(u), 0) + 1

    detail = {
        "total": total,
        "majors": len(majors),
        "others": len(others),
        "selected": len(selected),
        "cut": max(0, total - len(selected)),
        "majors_capped": len(majors) > max_fiches,
        "max_fiches": max_fiches,
        "selected_by_urgency": by_urg,
    }
    logger.info(
        "Selection fiches : total=%d majors=%d others=%d selected=%d cut=%d capped=%s",
        detail["total"], detail["majors"], detail["others"],
        detail["selected"], detail["cut"], detail["majors_capped"],
    )
    if detail["majors_capped"]:
        logger.warning(
            "Fiches majeures (%d) > max_fiches (%d) : "
            "aucune fiche normale incluse, contexte degrade",
            len(majors), max_fiches,
        )

    return [_serialize_fiche(d) for d in selected], total, truncated, detail


# ----------------------------------------------------------------------------
# Construction du prompt
# ----------------------------------------------------------------------------

def build_prompts(event, year, ts_start, ts_end, kpis, fiches, truncated,
                  comparisons=None, upcoming=None, n1_retro=None, extra_focus_note=None,
                  door_reinforcement=None, attendance=None, memory_block=None):
    """Retourne (system_prompt, user_prompt) en francais.

    comparisons (optionnel) : dict produit par compute_comparisons.
    upcoming (optionnel) : liste produite par get_upcoming_timetable.
    n1_retro (optionnel) : dict produit par get_or_build_n1_retrospective.
    extra_focus_note (optionnel) : consigne supplementaire (par ex. focus nuit
       pour le rapport matinal) ajoutee au user prompt.
    door_reinforcement (optionnel) : dict produit par
       pcorg_doors_analysis.compute_door_reinforcement.
    attendance (optionnel) : dict produit par compute_attendance_block.
       Apporte le pic constate de la veille (avec heure), le pic projete
       du jour, et l'heure attendue du pic via les pics N-1 jour-equivalent.
    memory_block (optionnel) : bloc texte des directives accumulees (mode
       reference uniquement -- ce parametre N'EST PAS injecte dans le system
       retourne ; il est envoye en bloc cache_control separe par
       _claude_stream_request. On le mentionne ici pour que la docstring
       reste a jour, mais build_prompts continue de retourner uniquement le
       system de base + user pour l'audit et le dataset).
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
        "precedente). Si un bloc 'Billetterie & Frequentation' t'est fourni, "
        "INCLUS OBLIGATOIREMENT dans la synthese : (a) le pic de frequentation "
        "de la VEILLE avec son heure (ex. **48 200 personnes** vers **15h30**) "
        "et la comparaison vs edition precedente en pourcentage, (b) le pic "
        "PROJETE du JOUR avec l'heure approximative attendue (= heure du pic "
        "de l'edition precedente jour-equivalent ; ex. 'pic projete a "
        "**52 000 vers 16h**, en hausse de **+8 %** par rapport a l'an "
        "passe'). Ces deux phrases sont obligatoires si la donnee est dispo. "
        "Quelques mots-cles peuvent etre en **gras** pour les chiffres ou "
        "tendances importantes. PAS de liste a puces ici, c'est un "
        "paragraphe synthetique.\n"
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
        "constat puis de l'action.\n"
        "ATTENTION : la 'Note retrospective' est un contexte INTERNE qui "
        "ne sera pas affiche au lecteur. NE FAIS JAMAIS reference a la "
        "note ('en coherence avec la note retrospective', 'voir note "
        "retrospective', 'cf note retrospective', 'comme indique dans la "
        "note', etc.). Integre directement le constat de l'an passe dans "
        "ta phrase comme un fait connu (par ex. : 'L'an passe a la meme "
        "heure, plusieurs altercations ont eu lieu : prevoir une patrouille "
        "dediee').\n"
        "NE redetaille PAS les renforts portes (le tableau dedie suffit), "
        "tu peux juste y faire reference d'une phrase generale. Pas de "
        "numerotation '1.', '2.' ; uniquement des puces.\n"
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
        "Bloc Billetterie & Frequentation (si fourni) :\n"
        "- 3 slots : yesterday / today / tomorrow. Pour chacun : "
        "'billets_vendus' (titres N), 'pic_observed' (pic constate, "
        "passe seulement) avec 'pic_observed_hour' (heure 'HHhMM' du pic), "
        "'pic_prev' avec 'pic_prev_hour' (pic et heure de l'edition "
        "precedente jour-equivalent), 'pic_projection' (pic projete = "
        "pic_prev * billets_vendus_N / billets_vendus_prev), "
        "'delta_pct_vs_prev' (en pourcentage).\n"
        "- VEILLE (slot=yesterday) : utilise pic_observed + "
        "pic_observed_hour pour annoncer le pic constate, et "
        "delta_pct_vs_prev pour la comparaison annee precedente. Ex: 'pic "
        "constate hier a **48 200** vers **15h30**, en hausse de **+12 %** "
        "vs l'an passe'.\n"
        "- AUJOURD'HUI (slot=today) : utilise pic_projection pour annoncer "
        "le pic attendu, et pic_prev_hour comme heure approximative du pic "
        "(en absence d'autre signal). Ex: 'pic projete a **52 000** vers "
        "**16h**'. Si pic_projection absent mais pic_prev present, donne "
        "le pic_prev en valeur de reference 'autour de **50 000** comme l'an "
        "passe a la meme heure'. L'heure pic_prev_hour est la **meilleure "
        "estimation** du moment ou interviendra le pic du jour.\n"
        "- Ne mentionne pas un slot dont aucune donnee n'est dispo "
        "(billets_vendus / pic_observed / pic_prev / pic_projection tous "
        "null) -- silence vaut mieux qu'invention.\n"
        "- N'invente pas un pic_observed si la donnee est null (cas "
        "today/tomorrow ou jour passe sans archive). Utilise pic_projection.\n"
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
        "\n"
        "Fuseau horaire :\n"
        "- TOUTES les heures et dates fournies dans les donnees (periode "
        "analysee, fenetres comparatives, ts des fiches, ts des "
        "commentaires, vignettes timetable) sont DEJA exprimees en heure "
        "locale **Europe/Paris** (UTC+01:00 en hiver, UTC+02:00 en ete). "
        "Quand tu cites une heure dans tes reponses, utilise toujours "
        "cette heure locale au format compact 'HHhMM' (ex: 14h30, 22h00, "
        "07h00). N'utilise JAMAIS le suffixe 'UTC', 'Z', '+00:00' ni "
        "ne mentionne le decalage horaire dans les phrases.\n"
    )

    # Toutes les heures envoyees a Claude doivent etre en local Paris pour
    # eviter qu'il rende les ISO UTC litteralement (05h UTC -> '05h' au lieu
    # de '07h' Paris).
    period_iso_start = _iso_paris(ts_start)
    period_iso_end = _iso_paris(ts_end)
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
        "- Periode (heures locales Europe/Paris) : "
        + period_iso_start + " --> " + period_iso_end + "\n"
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
                "- Fenetre (Europe/Paris) : "
                + _iso_paris(prev["period_start"]) + " --> "
                + _iso_paris(prev["period_end"]) + "\n"
                + json.dumps(prev["kpis"], ensure_ascii=False, indent=2, default=_json_default)
            )
        prev_year = comparisons.get("prev_year_aligned")
        if prev_year and prev_year.get("kpis", {}).get("total", 0) > 0:
            parts.append(
                "\n\nKPIs comparatifs - " + prev_year["label"] + " :\n"
                "- Fenetre annee precedente (Europe/Paris) : "
                + _iso_paris(prev_year["period_start"]) + " --> "
                + _iso_paris(prev_year["period_end"]) + "\n"
                "- Date course annee courante (Europe/Paris) : "
                + _iso_paris(prev_year["race_dt_n"]) + "\n"
                "- Date course annee precedente (Europe/Paris) : "
                + _iso_paris(prev_year["race_dt_prev"]) + "\n"
                + json.dumps(prev_year["kpis"], ensure_ascii=False, indent=2, default=_json_default)
            )

    if attendance and attendance.get("slots"):
        # Forme compacte des slots pour le prompt (on enleve les champs debug
        # type pic_observed_source qui n'apportent rien a Claude).
        compact_slots = []
        for s in attendance["slots"]:
            cs = {k: v for k, v in s.items() if k not in ("pic_observed_source",)}
            compact_slots.append(cs)
        att_payload = {
            "event": attendance.get("event"),
            "year": attendance.get("year"),
            "prev_year": attendance.get("prev_year"),
            "race_date": attendance.get("race_date"),
            "prev_race_date": attendance.get("prev_race_date"),
            "slots": compact_slots,
        }
        parts.append(
            "\n\nBilletterie & Frequentation (3 jours centres sur "
            "aujourd'hui ; pic_observed_hour = heure constatee Europe/Paris ; "
            "pic_prev_hour = heure du pic edition precedente, sert "
            "d'estimation pour le pic du jour) :\n"
            + json.dumps(att_payload, ensure_ascii=False, indent=2, default=_json_default)
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
        "- TOUTES les heures et dates fournies dans les donnees (fenetre, "
        "ts des fiches, ts des commentaires) sont DEJA exprimees en heure "
        "locale Europe/Paris. Quand tu cites une heure, utilise le format "
        "compact 'HHhMM' (ex: 14h30, 22h00). N'utilise JAMAIS 'UTC', 'Z', "
        "'+00:00' ni le decalage horaire dans tes phrases.\n"
    )
    user = (
        "Contexte :\n"
        "- Evenement : " + str(event) + "\n"
        "- Annee analysee : " + str(year_prev) + "\n"
        "- Fenetre alignee (heures locales Europe/Paris) : "
        + _iso_paris(ts_start) + " --> " + _iso_paris(ts_end) + "\n"
        "\n"
        "KPIs (edition precedente, periode alignee) :\n"
        + json.dumps(kpis, ensure_ascii=False, indent=2, default=_json_default)
        + "\n\nEchantillon de fiches (" + str(len(fiches)) + " sur "
        + str(kpis.get("total", 0)) + " au total) :\n"
        + json.dumps(fiches, ensure_ascii=False, indent=2, default=_json_default)
        + "\n\nProduis la note retrospective demandee."
    )
    return system, user


def _call_claude_text(system_prompt, user_prompt, on_progress=None, model=None):
    """Variante streaming de call_claude qui retourne juste le texte brut + usage.

    Sert pour la retro N-1 (note synthetique courte, max_tokens=1024).
    """
    raw_text, usage, _stop_reason = _claude_stream_request(
        system_prompt, user_prompt, max_tokens=1024,
        on_progress=on_progress, model=model,
    )
    return raw_text.strip(), usage


def _retro_cache_key(event, year_prev, ts_start, ts_end):
    return {
        "event": event,
        "year_prev": int(year_prev),
        "period_start": ts_start.isoformat() if hasattr(ts_start, "isoformat") else str(ts_start),
        "period_end": ts_end.isoformat() if hasattr(ts_end, "isoformat") else str(ts_end),
    }


def get_or_build_n1_retrospective(db, event, year_prev, ts_start_prev, ts_end_prev,
                                   on_progress=None, model=None):
    """Retourne la note retrospective N-1 (texte court) pour une fenetre alignee.

    1. Cherche un cache dans `pcorg_n1_retros` sur (event, year_prev, fenetre).
    2. Sinon, calcule KPIs + selectionne un echantillon de ~80 fiches N-1, fait
       un appel Claude dedie, sauve en cache et retourne.

    model (optionnel) : override CLAUDE_MODEL pour cet appel (whitelist).
    Le cache est globalement le meme quel que soit le modele : un changement
    de modele ne re-genere pas automatiquement la note. Pour forcer regeneration
    apres changement de modele, supprimer manuellement la collection
    pcorg_n1_retros pour la fenetre concernee.

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
    fiches, _, _, _ = select_fiches_for_prompt(
        db, event, int(year_prev), ts_start_prev, ts_end_prev, max_fiches=80,
    )
    if not fiches:
        return None

    system, user = _build_retro_prompts(event, int(year_prev), ts_start_prev, ts_end_prev, kpis, fiches)
    use_model = _validate_model(model) or CLAUDE_MODEL
    try:
        text, usage = _call_claude_text(system, user, on_progress=on_progress, model=use_model)
    except ClaudeError as e:
        logger.warning("Retro N-1 echouee : %s", e)
        return None
    if not text:
        return None

    doc = dict(key)
    doc["text"] = text
    doc["kpis"] = kpis
    doc["fiches_count"] = len(fiches)
    doc["model"] = use_model
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
        "model": use_model,
        "usage": usage,
    }


# ----------------------------------------------------------------------------
# Appel API Claude
# ----------------------------------------------------------------------------

class ClaudeError(Exception):
    """Erreur lors de l'appel a l'API Anthropic."""


def _validate_model(model):
    """Retourne le modele si valide (whitelist), sinon None pour fallback defaut."""
    if not model:
        return None
    m = str(model).strip()
    if m and m in ALLOWED_MODELS:
        return m
    if m:
        logger.warning("Modele '%s' non autorise, fallback sur defaut '%s'", m, CLAUDE_MODEL)
    return None


def _claude_stream_request(system_prompt, user_prompt, max_tokens, on_progress=None,
                           model=None, system_cache=True, memory_block=None):
    """Effectue un appel streaming a l'API Anthropic et retourne (text, usage, stop_reason).

    Le streaming evite les timeouts sur les reponses longues : tant que Claude
    envoie des chunks, la connexion reste vivante. Le timeout
    CLAUDE_TIMEOUT_SECONDS s'applique alors uniquement entre 2 chunks.

    Retry automatique sur erreurs reseau et HTTP 429/503/529 avec exponential
    backoff (3 essais, 1s/2s/4s).

    Prompt caching : par defaut le system prompt est marque cache_control
    ephemeral (cache 5 min) -> les appels rapproches ne re-paient pas le system.

    Si memory_block est fourni, il est ajoute comme bloc system separe avec
    son propre cache_control. Ainsi une modification de la memoire (ajout
    de directive) n'invalide que le cache 'memoire', pas le cache du gros
    system de base.

    Parametres :
    - on_progress (optionnel) : callable(text_so_far, output_tokens_so_far)
      appele a intervalles reguliers pour permettre un affichage en temps reel.
    - model (optionnel) : override CLAUDE_MODEL pour cet appel (whitelist appliquee).
    - system_cache (defaut True) : passer False pour desactiver le cache (debug).
    - memory_block (optionnel) : texte du bloc 'Connaissance accumulee', ajoute
      en bloc system separe avec cache_control distinct.

    Le usage retourne contient input_tokens / output_tokens
    + cache_creation_input_tokens / cache_read_input_tokens (telemetrie cache).
    Le stop_reason est extrait du dernier message_delta SSE ('end_turn',
    'max_tokens', 'stop_sequence', 'tool_use'...).
    """
    if not ANTHROPIC_API_KEY:
        raise ClaudeError("ANTHROPIC_API_KEY non configuree")

    use_model = _validate_model(model) or CLAUDE_MODEL

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }
    # System en blocs avec cache_control pour profiter du prompt caching.
    # Si une memoire constitutionnelle est fournie, on la met en bloc separe
    # pour que sa modification n'invalide que son propre cache.
    if system_cache:
        system_payload = [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }]
        if memory_block:
            system_payload.append({
                "type": "text",
                "text": memory_block,
                "cache_control": {"type": "ephemeral"},
            })
    else:
        if memory_block:
            system_payload = system_prompt + "\n\n" + memory_block
        else:
            system_payload = system_prompt
    body = {
        "model": use_model,
        "max_tokens": int(max_tokens),
        "system": system_payload,
        "messages": [{"role": "user", "content": user_prompt}],
        "stream": True,
    }

    last_err = None
    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            return _claude_stream_attempt(headers, body, on_progress)
        except ClaudeError as e:
            msg = str(e)
            retryable = (
                msg == "claude_unreachable"
                or msg == "claude_stream_interrupted"
                or any(msg.startswith("claude_http_" + str(c)) for c in RETRYABLE_HTTP_CODES)
            )
            if not retryable or attempt >= RETRY_MAX_ATTEMPTS - 1:
                raise
            backoff = RETRY_BACKOFF_BASE_S * (2 ** attempt)
            logger.warning("Claude '%s' (essai %d/%d) -> retry dans %.1fs",
                           msg, attempt + 1, RETRY_MAX_ATTEMPTS, backoff)
            time.sleep(backoff)
            last_err = e
    if last_err:
        raise last_err
    raise ClaudeError("retry_exhausted")


def _claude_stream_attempt(headers, body, on_progress=None):
    """Un seul essai d'appel streaming Anthropic. Voir _claude_stream_request."""
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
    cache_creation = 0
    cache_read = 0
    stop_reason = None
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
                cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
                cache_read = int(usage.get("cache_read_input_tokens") or 0)
            elif etype == "message_delta":
                delta = evt.get("delta") or {}
                if delta.get("stop_reason"):
                    stop_reason = delta["stop_reason"]
                usage = evt.get("usage") or {}
                if usage.get("output_tokens"):
                    usage_out = int(usage["output_tokens"])
                if usage.get("cache_creation_input_tokens"):
                    cache_creation = int(usage["cache_creation_input_tokens"])
                if usage.get("cache_read_input_tokens"):
                    cache_read = int(usage["cache_read_input_tokens"])
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

    return raw_text, {
        "input_tokens": usage_in,
        "output_tokens": usage_out,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
    }, stop_reason


def _merge_usage(*usages):
    """Somme cumulative de plusieurs dicts usage (input/output/cache tokens)."""
    out = {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
    }
    for u in usages:
        if not u:
            continue
        for k in out:
            out[k] += int(u.get(k) or 0)
    return out


def call_claude(system_prompt, user_prompt, on_progress=None, model=None,
                memory_block=None):
    """Appelle l'API Claude en streaming, retourne (sections_dict, raw_text, usage).

    Si le retour n'est pas du JSON parsable, sections_dict est None et
    raw_text contient la reponse brute. Leve ClaudeError pour les erreurs.

    Si la reponse est tronquee (stop_reason='max_tokens'), un retry est tente
    avec CLAUDE_MAX_TOKENS_RETRY et une consigne de concision. Les usages sont
    cumules.

    memory_block (optionnel) : bloc 'Connaissance accumulee' ajoute en
    bloc system separe (cache_control distinct).
    """
    raw_text, usage, stop_reason = _claude_stream_request(
        system_prompt, user_prompt, CLAUDE_MAX_TOKENS,
        on_progress=on_progress, model=model, memory_block=memory_block,
    )
    sections = _parse_sections(raw_text)

    if stop_reason == "max_tokens":
        logger.warning("Reponse Claude tronquee a max_tokens (%d) -> retry avec %d",
                       CLAUDE_MAX_TOKENS, CLAUDE_MAX_TOKENS_RETRY)
        retry_system = system_prompt + (
            "\n\nIMPORTANT (retry apres troncature) : la reponse precedente a "
            "depasse le budget de tokens. Sois plus concis : maximum 4 phrases "
            "courtes par section, maximum 6 puces par liste. Garde l'essentiel "
            "operationnel."
        )
        try:
            raw_text2, usage2, stop_reason2 = _claude_stream_request(
                retry_system, user_prompt, CLAUDE_MAX_TOKENS_RETRY,
                on_progress=on_progress, model=model, memory_block=memory_block,
            )
            sections2 = _parse_sections(raw_text2)
            if sections2 is not None:
                merged = _merge_usage(usage, usage2)
                merged["retried_for_truncation"] = True
                return sections2, raw_text2, merged
            logger.warning("Retry truncation : reponse 2 toujours non parsable, "
                           "on garde la version partielle initiale")
        except ClaudeError as e:
            logger.warning("Retry truncation echec : %s, on garde la version partielle", e)

    return sections, raw_text, usage


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
    chaque valeur etant une chaine non vide ou ''.

    Tolerance format : si Claude renvoie une LISTE pour une section (au lieu
    d'une chaine avec puces \\n- ), on la reconstitue proprement plutot que
    de str()-ifier la liste Python brute (qui donnerait "['rec1', 'rec2']").
    Idem pour un dict (rare) : on aplatit en lignes 'cle : valeur'.
    """
    out = {}
    for key in SECTION_KEYS:
        v = data.get(key)
        if v is None:
            out[key] = ""
            continue
        if isinstance(v, list):
            items = [str(x).strip() for x in v if x is not None and str(x).strip()]
            if not items:
                out[key] = ""
            elif all(it.startswith("- ") or it.startswith("* ") for it in items):
                out[key] = "\n".join(items)
            else:
                out[key] = "\n".join("- " + it for it in items)
            continue
        if isinstance(v, dict):
            out[key] = "\n".join(
                str(k) + " : " + str(val) for k, val in v.items() if val
            )
            continue
        out[key] = str(v).strip()
    return out


# ----------------------------------------------------------------------------
# Persistance MongoDB
# ----------------------------------------------------------------------------

def _compute_prompt_hash(system_prompt, user_prompt):
    """Hash court (12 chars hex) du system + user prompt envoyes a Claude.

    Sert a filtrer le dataset d'apprentissage par generation de prompt :
    apres une refonte du system, on peut exclure les vieux samples du dataset
    sans relire 1000 documents.
    """
    import hashlib
    h = hashlib.sha256()
    h.update((system_prompt or "").encode("utf-8", errors="replace"))
    h.update(b"\x00")
    h.update((user_prompt or "").encode("utf-8", errors="replace"))
    return h.hexdigest()[:12]


def save_summary(db, event, year, ts_start, ts_end, created_by_email, created_by_name,
                 kpis, fiches_count, truncated, sections, raw_text, usage,
                 comparisons=None, upcoming=None, attendance=None, n1_retro=None,
                 door_reinforcement=None, selection_detail=None, model=None,
                 system_prompt=None, user_prompt=None,
                 memory_directive_ids=None, memory_block_text=None):
    """Insere un document de resume et retourne le doc complet.

    event/year peuvent etre None (resume "tous evenements").
    model (optionnel) : si fourni et valide, persiste a la place de CLAUDE_MODEL.

    system_prompt / user_prompt : prompts complets envoyes a Claude. Persistes
    pour preparer le dataset d'apprentissage (fine-tuning futur). Le hash et
    la version du prompt sont aussi stockes pour permettre le filtrage.

    memory_directive_ids : liste d'ids de directives injectees dans le prompt
    courant (pour audit + increment used_count).
    """
    _ensure_indexes(db)
    use_model = _validate_model(model) or CLAUDE_MODEL
    prompt_hash = _compute_prompt_hash(system_prompt or "", user_prompt or "") \
        if (system_prompt or user_prompt) else None
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
        "selection_detail": selection_detail or None,
        "kpis": kpis,
        "comparisons": comparisons or {},
        "upcoming": upcoming or [],
        "attendance": attendance or None,
        "n1_retro": n1_retro or None,
        "door_reinforcement": door_reinforcement or None,
        "sections": sections,
        "raw_text": raw_text,
        "model": use_model,
        "usage": usage or {},
        # Persistance prompts pour dataset d'apprentissage. Snapshots
        # immutables : ne JAMAIS re-ecrire ces champs.
        "system_prompt": system_prompt or "",
        "user_prompt": user_prompt or "",
        "system_prompt_hash": prompt_hash,
        "prompt_version": PROMPT_VERSION,
        "memory_directive_ids": list(memory_directive_ids or []),
        "memory_block_text": memory_block_text or "",
        # Sous-arrays append-only pour le feedback utilisateur.
        "feedback": [],
        "recommendations_status": [],
        "quality_label_per_section": {},
        "quality_label_global": None,
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
            # MongoDB BSON ne stocke pas la tzinfo : les datetime relus sont
            # naifs en UTC. Sans suffixe explicite, le client JS
            # (new Date(...)) interprete l'ISO comme heure locale du
            # navigateur -> rapport matinal stocke 07h Paris (= 05h UTC) qui
            # apparait '05:00' au lieu de '07:00'. On force +00:00.
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
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
        out["selection_detail"] = doc.get("selection_detail") or None
        out["sections"] = doc.get("sections") or {}
        out["raw_text"] = doc.get("raw_text") or ""
        out["usage"] = doc.get("usage") or {}
        # Feedback structure (immutable append-only sous-arrays).
        out["feedback"] = doc.get("feedback") or []
        out["recommendations_status"] = doc.get("recommendations_status") or []
        out["quality_label_per_section"] = doc.get("quality_label_per_section") or {}
        out["quality_label_global"] = doc.get("quality_label_global")
        # Versioning prompt -- utile cote UI pour signaler les anciens samples.
        out["prompt_version"] = doc.get("prompt_version")
        out["system_prompt_hash"] = doc.get("system_prompt_hash")
        # Les prompts complets ne sont pas envoyes par defaut (verbeux). La
        # route /api/pcorg/summary/<id>/prompts en mode admin les expose.
    return out


# ----------------------------------------------------------------------------
# Orchestration principale
# ----------------------------------------------------------------------------

# ----------------------------------------------------------------------------
# Preferences "Rapport matinal" (opt-in par utilisateur)
# ----------------------------------------------------------------------------

def get_morning_report_prefs(db):
    """Retourne {enabled, enabled_user_ids: [str], updated_at, updated_by}.

    `enabled` est l'interrupteur global : si False, la tache planifiee quitte
    sans rien faire (zero appel Claude, zero mail). Defaut False (opt-in).
    """
    doc = db[COCKPIT_SETTINGS_COLLECTION].find_one({"_id": MORNING_REPORT_SETTINGS_ID}) or {}
    raw_ids = doc.get("enabled_user_ids") or []
    ids = [str(x) for x in raw_ids]
    return {
        "enabled": bool(doc.get("enabled", False)),
        "enabled_user_ids": ids,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


def set_morning_report_enabled(db, enabled, updated_by_email=None):
    """Active ou desactive globalement le rapport matinal automatique."""
    db[COCKPIT_SETTINGS_COLLECTION].update_one(
        {"_id": MORNING_REPORT_SETTINGS_ID},
        {
            "$set": {
                "enabled": bool(enabled),
                "updated_at": datetime.now(timezone.utc),
                "updated_by": updated_by_email or "",
            },
        },
        upsert=True,
    )


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
                             extra_focus_note=None, as_of_utc=None, on_progress=None,
                             model=None, dry_run=False):
    """Calcule KPIs + comparaisons + prochaines 24h + billetterie + retro N-1, appelle Claude.

    Le pipeline DB et l'appel retro N-1 (Claude) sont parallelises via un
    ThreadPoolExecutor : tous les calculs autres que le compute_comparisons
    courent en parallele, et le retro N-1 (qui depend de comparisons) est
    soumis des que comparisons est dispo. Gain typique 5-10s.

    extra_focus_note : consigne supplementaire a injecter dans le user prompt
    (par ex. focus nuit pour le rapport matinal).
    as_of_utc (optionnel, mode test) : datetime UTC aware qui simule le 'now'
    pour les blocs upcoming / attendance / door_reinforcement. Permet de
    tester le rapport hors periode d'evenement en se placant virtuellement
    pendant une edition passee.
    on_progress (optionnel) : callback(text_so_far, output_tokens_so_far)
    appele a intervalles reguliers pendant le streaming Claude. Utile pour
    afficher la progression cote CLI ou pour streamer vers une UI.
    model (optionnel) : override du modele pour CET appel (whitelist appliquee).
    dry_run (optionnel) : si True, retourne le prompt assemble sans appeler
    Claude ni persister. Utile pour iterer sur le prompt sans cramer du token.
    """
    use_model = _validate_model(model) or CLAUDE_MODEL

    # Charge la memoire constitutionnelle pour ce scope (event uniquement,
    # section=None car le rapport produit toutes les sections d'un coup).
    # Le bloc est passe en bloc cache_control separe a Claude.
    memory_directives = []
    memory_block_text = ""
    try:
        import pcorg_ai_memory
        memory_directives, _overflow = pcorg_ai_memory.load_active_directives(
            db, event=event, section=None,
        )
        memory_block_text = pcorg_ai_memory.format_directives_block(memory_directives)
    except Exception as e:
        logger.warning("ai_memory: chargement directives a echoue (%s)", e)
        memory_directives = []
        memory_block_text = ""

    def _safe_doors():
        try:
            import pcorg_doors_analysis
            return pcorg_doors_analysis.compute_door_reinforcement(
                db, event, year, now_utc=as_of_utc,
            )
        except Exception as e:
            logger.warning("Renforts portes : echec calcul (%s)", e)
            return None

    # Phase 1 : tous les calculs en parallele. Les threads relachent le GIL
    # pendant les I/O Mongo et l'appel Claude, donc le ThreadPoolExecutor
    # parallelise effectivement.
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="pcorg_sum") as pool:
        f_kpis = pool.submit(compute_kpis, db, event, year, ts_start, ts_end)
        f_cmp = pool.submit(compute_comparisons, db, event, year, ts_start, ts_end)
        f_up = pool.submit(get_upcoming_timetable, db, event, year, 24, as_of_utc)
        f_att = pool.submit(compute_attendance_block, db, event, year, as_of_utc)
        f_doors = pool.submit(_safe_doors)
        f_fiches = pool.submit(select_fiches_for_prompt, db, event, year, ts_start, ts_end)

        # Kicker la retro N-1 des que comparisons est pret : elle declenche
        # un appel Claude (~5-10s) qui peut tourner en parallele du reste.
        # Pas de on_progress sur la retro pour ne pas melanger les streams CLI.
        comparisons = f_cmp.result()
        f_retro = None
        py = (comparisons or {}).get("prev_year_aligned")
        if py and py.get("kpis", {}).get("total", 0) > 0:
            try:
                ts_prev_start = datetime.fromisoformat(py["period_start"])
                ts_prev_end = datetime.fromisoformat(py["period_end"])
                f_retro = pool.submit(
                    get_or_build_n1_retrospective,
                    db, event, py.get("year_prev"), ts_prev_start, ts_prev_end,
                    None, use_model,
                )
            except Exception as e:
                logger.warning("Retro N-1 : preparation echouee : %s", e)

        kpis = f_kpis.result()
        upcoming = f_up.result()
        attendance = f_att.result()
        door_reinforcement = f_doors.result()
        fiches, total_fiches, truncated, selection_detail = f_fiches.result()
        n1_retro = None
        if f_retro:
            try:
                n1_retro = f_retro.result()
            except Exception as e:
                logger.warning("Retro N-1 : echec runtime : %s", e)
                n1_retro = None

    memory_ids = [d.get("_id") for d in (memory_directives or []) if d.get("_id")]

    def _increment_memory_usage():
        if not memory_ids:
            return
        try:
            import pcorg_ai_memory
            pcorg_ai_memory.increment_usage(db, memory_ids)
        except Exception as e:
            logger.warning("ai_memory: increment_usage a echoue (%s)", e)

    # Cas "aucune fiche" : court-circuit ou appel Claude minimal.
    if kpis["total"] == 0:
        if not upcoming:
            sections = {k: "RAS" for k in SECTION_KEYS}
            sections["prochaines_24h"] = "Aucun jalon planifie dans les 24 prochaines heures."
            if dry_run:
                return _dry_run_payload(
                    event, year, ts_start, ts_end, kpis, [], False,
                    comparisons, upcoming, attendance, n1_retro,
                    door_reinforcement, extra_focus_note, selection_detail,
                    use_model, sections, "[shortcut: aucune fiche, aucun jalon]",
                    memory_block_text=memory_block_text,
                    memory_directive_ids=memory_ids,
                )
            return save_summary(
                db, event, year, ts_start, ts_end, created_by_email, created_by_name,
                kpis, 0, False, sections, "",
                {"input_tokens": 0, "output_tokens": 0,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                comparisons=comparisons, upcoming=upcoming, attendance=attendance,
                n1_retro=n1_retro, door_reinforcement=door_reinforcement,
                selection_detail=selection_detail, model=use_model,
                system_prompt="", user_prompt="",
                memory_directive_ids=memory_ids,
                memory_block_text=memory_block_text,
            )
        system, user = build_prompts(
            event, year, ts_start, ts_end, kpis, [], False,
            comparisons=comparisons, upcoming=upcoming, n1_retro=n1_retro,
            extra_focus_note=extra_focus_note,
            door_reinforcement=door_reinforcement,
            attendance=attendance,
        )
        if dry_run:
            return _dry_run_payload(
                event, year, ts_start, ts_end, kpis, [], False,
                comparisons, upcoming, attendance, n1_retro,
                door_reinforcement, extra_focus_note, selection_detail,
                use_model, system_prompt=system, user_prompt=user,
                memory_block_text=memory_block_text,
                memory_directive_ids=memory_ids,
            )
        sections, raw_text, usage = call_claude(
            system, user, on_progress=on_progress, model=use_model,
            memory_block=memory_block_text or None,
        )
        _increment_memory_usage()
        if sections is None:
            sections = {k: "RAS" for k in SECTION_KEYS}
            sections["prochaines_24h"] = "Reponse Claude non parsable."
        for k in SECTION_KEYS:
            if k != "prochaines_24h" and not sections.get(k):
                sections[k] = "RAS"
        return save_summary(
            db, event, year, ts_start, ts_end, created_by_email, created_by_name,
            kpis, 0, False, sections, raw_text, usage,
            comparisons=comparisons, upcoming=upcoming, attendance=attendance,
            n1_retro=n1_retro, door_reinforcement=door_reinforcement,
            selection_detail=selection_detail, model=use_model,
            system_prompt=system, user_prompt=user,
            memory_directive_ids=memory_ids,
            memory_block_text=memory_block_text,
        )

    system, user = build_prompts(
        event, year, ts_start, ts_end, kpis, fiches, truncated,
        comparisons=comparisons, upcoming=upcoming, n1_retro=n1_retro,
        extra_focus_note=extra_focus_note,
        door_reinforcement=door_reinforcement,
        attendance=attendance,
    )
    if dry_run:
        return _dry_run_payload(
            event, year, ts_start, ts_end, kpis, fiches, truncated,
            comparisons, upcoming, attendance, n1_retro,
            door_reinforcement, extra_focus_note, selection_detail,
            use_model, system_prompt=system, user_prompt=user,
            memory_block_text=memory_block_text,
            memory_directive_ids=memory_ids,
        )
    sections, raw_text, usage = call_claude(
        system, user, on_progress=on_progress, model=use_model,
        memory_block=memory_block_text or None,
    )
    _increment_memory_usage()
    if sections is None:
        sections = {k: "" for k in SECTION_KEYS}
        sections["faits_marquants"] = raw_text or "Reponse Claude non parsable."

    return save_summary(
        db, event, year, ts_start, ts_end, created_by_email, created_by_name,
        kpis, len(fiches), truncated, sections, raw_text, usage,
        comparisons=comparisons, upcoming=upcoming, attendance=attendance,
        n1_retro=n1_retro, door_reinforcement=door_reinforcement,
        selection_detail=selection_detail, model=use_model,
        system_prompt=system, user_prompt=user,
        memory_directive_ids=memory_ids,
        memory_block_text=memory_block_text,
    )


def _dry_run_payload(event, year, ts_start, ts_end, kpis, fiches, truncated,
                     comparisons, upcoming, attendance, n1_retro,
                     door_reinforcement, extra_focus_note, selection_detail,
                     model, sections=None, raw_text=None,
                     system_prompt=None, user_prompt=None,
                     memory_block_text=None, memory_directive_ids=None):
    """Payload retourne par generate_period_summary en mode dry_run.

    Le shape est compatible avec _serialize_summary (light=False) cote frontend
    pour pouvoir reutiliser le meme rendu, plus les champs system_prompt /
    user_prompt qui contiennent les prompts assembles tels qu'ils auraient ete
    envoyes a Claude.
    """
    return {
        "_id": "dry-run",
        "id": "dry-run",
        "dry_run": True,
        "event": event,
        "year": int(year) if year is not None else None,
        "period_start": ts_start.isoformat() if hasattr(ts_start, "isoformat") else ts_start,
        "period_end": ts_end.isoformat() if hasattr(ts_end, "isoformat") else ts_end,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fiches_count": len(fiches),
        "truncated": bool(truncated),
        "selection_detail": selection_detail,
        "kpis": kpis,
        "comparisons": comparisons or {},
        "upcoming": upcoming or [],
        "attendance": attendance,
        "n1_retro": n1_retro,
        "door_reinforcement": door_reinforcement,
        "model": model,
        "sections": sections or {},
        "raw_text": raw_text or "",
        "usage": {"input_tokens": 0, "output_tokens": 0,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                  "dry_run": True},
        "extra_focus_note": extra_focus_note or None,
        "system_prompt": system_prompt or "",
        "user_prompt": user_prompt or "",
        "system_prompt_chars": len(system_prompt or ""),
        "user_prompt_chars": len(user_prompt or ""),
        "memory_block_text": memory_block_text or "",
        "memory_directive_ids": list(memory_directive_ids or []),
    }


# ----------------------------------------------------------------------------
# Feedback utilisateur sur un rapport (append-only, immutable)
# ----------------------------------------------------------------------------

FEEDBACK_KINDS = ("validation", "correction", "rule", "comment")
QUALITY_LABELS = ("good", "neutral", "bad")
RECOMMENDATION_STATUSES = ("applied", "partial", "ignored", "not_relevant")


def add_feedback(db, summary_id, section, kind,
                 original_text=None, corrected_text=None,
                 rule_text=None, comment=None, target=None,
                 rating=None, by_email=None, by_name=None,
                 promoted_memory_id=None):
    """Ajoute une entry de feedback (append-only) sur un rapport.

    section : une des SECTION_KEYS.
    kind : 'validation' | 'correction' | 'rule' | 'comment'.
    target : 'section' (defaut) ou 'bullet:N' pour cibler une puce d'une
             section en liste (recommandations, faits_marquants, prochaines_24h).
    rating : 'good' | 'bad' optionnel (pour 'validation' / 'comment').

    Leve ValueError pour parametres invalides. Retourne le doc apres
    insertion ou None si rapport introuvable.
    """
    if section not in SECTION_KEYS:
        raise ValueError("section invalide : " + str(section))
    if kind not in FEEDBACK_KINDS:
        raise ValueError("kind invalide : " + str(kind))
    if rating is not None and rating not in QUALITY_LABELS:
        raise ValueError("rating invalide : " + str(rating))
    if kind == "correction" and not corrected_text:
        raise ValueError("correction requiert corrected_text non vide")
    if kind == "rule" and not rule_text:
        raise ValueError("rule requiert rule_text non vide")
    entry = {
        "section": section,
        "kind": kind,
        "target": target or "section",
        "original_text": original_text,
        "corrected_text": corrected_text,
        "rule_text": rule_text,
        "comment": comment,
        "rating": rating,
        "promoted_memory_id": promoted_memory_id,
        "ts": datetime.now(timezone.utc),
        "by_email": by_email or "",
        "by_name": by_name or "",
    }
    res = db[SUMMARIES_COLLECTION].find_one_and_update(
        {"_id": summary_id},
        {"$push": {"feedback": entry}},
        return_document=True,
    )
    return res


def set_quality_label(db, summary_id, label, section=None, by_email=None):
    """Pose un label qualite global (section=None) ou par section.

    label : 'good' | 'neutral' | 'bad' | None (pour effacer).
    Stocke aussi un audit dans quality_label_history (append-only).
    """
    if label is not None and label not in QUALITY_LABELS:
        raise ValueError("label invalide : " + str(label))
    set_op = {}
    if section is None:
        set_op["quality_label_global"] = label
    else:
        if section not in SECTION_KEYS:
            raise ValueError("section invalide : " + str(section))
        set_op["quality_label_per_section." + section] = label
    push_op = {
        "quality_label_history": {
            "ts": datetime.now(timezone.utc),
            "section": section,
            "label": label,
            "by_email": by_email or "",
        },
    }
    return db[SUMMARIES_COLLECTION].find_one_and_update(
        {"_id": summary_id},
        {"$set": set_op, "$push": push_op},
        return_document=True,
    )


def set_recommendation_status(db, summary_id, bullet_index, status,
                              by_email=None):
    """Marque une recommandation comme appliquee/partielle/ignoree/non pertinente.

    bullet_index : index 0-based de la puce dans la section 'recommandations'
    rendue. status : un des RECOMMENDATION_STATUSES ou None pour reset.
    """
    if status is not None and status not in RECOMMENDATION_STATUSES:
        raise ValueError("status invalide : " + str(status))
    try:
        idx = int(bullet_index)
    except (TypeError, ValueError):
        raise ValueError("bullet_index invalide")
    entry = {
        "bullet_index": idx,
        "status": status,
        "ts": datetime.now(timezone.utc),
        "by_email": by_email or "",
    }
    return db[SUMMARIES_COLLECTION].find_one_and_update(
        {"_id": summary_id},
        {"$push": {"recommendations_status": entry}},
        return_document=True,
    )


def get_prompts(db, summary_id):
    """Retourne system_prompt + user_prompt persistes pour audit/debug.
    Reserve admin (verbeux).
    """
    doc = db[SUMMARIES_COLLECTION].find_one(
        {"_id": summary_id},
        {"system_prompt": 1, "user_prompt": 1, "memory_block_text": 1,
         "system_prompt_hash": 1, "prompt_version": 1},
    )
    if not doc:
        return None
    return {
        "system_prompt": doc.get("system_prompt") or "",
        "user_prompt": doc.get("user_prompt") or "",
        "memory_block_text": doc.get("memory_block_text") or "",
        "system_prompt_hash": doc.get("system_prompt_hash"),
        "prompt_version": doc.get("prompt_version"),
    }


# ----------------------------------------------------------------------------
# Export dataset pour fine-tuning futur
# ----------------------------------------------------------------------------

def _section_label_fr(key):
    """Libelle FR d'une section pour les samples (utile en assistant content)."""
    return {
        "synthese": "Synthese",
        "faits_marquants": "Faits marquants",
        "secours": "Secours",
        "securite": "Securite",
        "technique": "Technique",
        "flux": "Flux",
        "fourriere": "Fourriere",
        "recommandations": "Recommandations",
        "prochaines_24h": "Prochaines 24 heures",
    }.get(key, key)


def _assistant_content_from_sections(sections):
    """Reconstruit la reponse au format JSON attendu par Claude depuis le
    dict sections. Pour le dataset, on retourne le JSON serialise comme
    Claude le produisait (compatible re-entrainement sur meme format de sortie).
    """
    if not sections:
        return ""
    payload = {k: sections.get(k, "") for k in SECTION_KEYS}
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _apply_corrections(sections, feedback_entries):
    """Applique les corrections de feedback au dict sections pour produire la
    'ground truth' utilisateur. Une correction au niveau section remplace la
    valeur. Les corrections au niveau bullet ne sont pas appliquees ici
    (necessitent parsing de la liste -- on les laisse a un re-export plus
    riche si besoin).
    """
    out = dict(sections or {})
    for f in (feedback_entries or []):
        if f.get("kind") != "correction":
            continue
        section = f.get("section")
        target = f.get("target") or "section"
        if section in SECTION_KEYS and target == "section" and f.get("corrected_text"):
            out[section] = f["corrected_text"]
    return out


def export_training_dataset(db, format_="sft", ts_from=None, ts_to=None,
                            min_quality=None, include_memory=False,
                            prompt_version=None):
    """Genere des samples pour fine-tuning au format JSONL.

    format_ : 'sft' (supervised) ou 'dpo' (preference paire chosen/rejected).
    ts_from / ts_to : filtre sur created_at.
    min_quality : 'good' (n'inclut que les rapports labelles bons) ou None.
    include_memory : si True, concatene memory_block_text au system_prompt.
    prompt_version : filtre sur prompt_version (ex: 1 pour la generation
                     actuelle, evite les samples post-refonte du prompt).

    Yields des dicts (a serialiser en JSONL par l'appelant).

    SFT format :
      { "messages": [
          {"role":"system","content":"..."},
          {"role":"user","content":"..."},
          {"role":"assistant","content":"<JSON sections corrigees>"}
      ]}

    DPO format (uniquement pour rapports avec >=1 correction) :
      { "prompt":"...","chosen":"<JSON corrige>","rejected":"<JSON original>"}
    """
    q = {}
    if ts_from is not None:
        q.setdefault("created_at", {})["$gte"] = ts_from
    if ts_to is not None:
        q.setdefault("created_at", {})["$lte"] = ts_to
    if prompt_version is not None:
        try:
            q["prompt_version"] = int(prompt_version)
        except (TypeError, ValueError):
            pass

    cur = db[SUMMARIES_COLLECTION].find(q)
    for doc in cur:
        system_prompt = doc.get("system_prompt") or ""
        user_prompt = doc.get("user_prompt") or ""
        if not system_prompt or not user_prompt:
            # Pas exploitable : doc anterieur a l'introduction de la
            # persistance prompts.
            continue
        sections_original = doc.get("sections") or {}
        feedback = doc.get("feedback") or []
        sections_corrected = _apply_corrections(sections_original, feedback)

        # Filtre qualite : on n'inclut que les rapports juges 'bons' (global)
        # ou ayant au moins une correction (= ground truth utilisateur).
        global_label = doc.get("quality_label_global")
        has_correction = any(f.get("kind") == "correction" for f in feedback)
        if min_quality == "good":
            if global_label != "good" and not has_correction:
                continue

        if include_memory and doc.get("memory_block_text"):
            full_system = system_prompt + "\n\n" + doc["memory_block_text"]
        else:
            full_system = system_prompt

        if format_ == "dpo":
            if not has_correction:
                continue
            yield {
                "prompt": full_system + "\n\n---\n\n" + user_prompt,
                "chosen": _assistant_content_from_sections(sections_corrected),
                "rejected": _assistant_content_from_sections(sections_original),
                "_meta": {
                    "summary_id": str(doc.get("_id")),
                    "event": doc.get("event"),
                    "year": doc.get("year"),
                    "prompt_version": doc.get("prompt_version"),
                    "model": doc.get("model"),
                },
            }
        else:
            # SFT : utilise les sections corrigees comme reponse cible.
            yield {
                "messages": [
                    {"role": "system", "content": full_system},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": _assistant_content_from_sections(sections_corrected)},
                ],
                "_meta": {
                    "summary_id": str(doc.get("_id")),
                    "event": doc.get("event"),
                    "year": doc.get("year"),
                    "prompt_version": doc.get("prompt_version"),
                    "model": doc.get("model"),
                    "quality_label": global_label,
                    "has_correction": has_correction,
                },
            }


def export_stats(db):
    """Compteurs pour la page admin (volume du dataset, samples utilisables).

    Distingue le label qualite GLOBAL (au niveau rapport entier) de la
    validation PAR SECTION (clic 👍 sur une section). Avant ce fix, seul
    quality_label_global etait compte, ce qui faisait apparaitre 0 alors
    que l'utilisateur cumulait des validations de section.
    """
    col = db[SUMMARIES_COLLECTION]
    total = col.count_documents({})
    with_prompts = col.count_documents({
        "system_prompt": {"$exists": True, "$ne": ""},
        "user_prompt": {"$exists": True, "$ne": ""},
    })
    with_feedback = col.count_documents({"feedback.0": {"$exists": True}})
    with_corrections = col.count_documents({"feedback.kind": "correction"})
    with_quality_good_global = col.count_documents({"quality_label_global": "good"})

    # ----- Validations par section : on agrege via quality_label_history qui
    # est append-only (capture chaque clic 👍 / 👎 / retour neutre).
    # On compte chaque entry distincte (rapport, section, label) -- ainsi un
    # toggle 👍 puis retour neutre puis re-👍 ne compte qu'une fois la version
    # finale (latest par (summary, section)).
    validations_good = 0
    validations_bad = 0
    summaries_with_section_validation = set()
    # Approche simple : on lit le champ persistant quality_label_per_section
    # sur chaque doc -- plus rapide qu'un agg sur l'historique et suffisant
    # car c'est ce qui est effectivement injecte dans les samples du dataset.
    for d in col.find(
        {"quality_label_per_section": {"$exists": True}},
        {"_id": 1, "quality_label_per_section": 1},
    ):
        labels = d.get("quality_label_per_section") or {}
        if not isinstance(labels, dict):
            continue
        had_any = False
        for sec, lab in labels.items():
            if lab == "good":
                validations_good += 1
                had_any = True
            elif lab == "bad":
                validations_bad += 1
                had_any = True
        if had_any:
            summaries_with_section_validation.add(str(d.get("_id")))

    # Total entries feedback (correction + comment + rule + validation).
    feedback_entries_total = 0
    rules_promoted = 0
    for r in col.aggregate([
        {"$match": {"feedback.0": {"$exists": True}}},
        {"$project": {"n": {"$size": {"$ifNull": ["$feedback", []]}},
                      "n_rules": {
                          "$size": {
                              "$filter": {
                                  "input": {"$ifNull": ["$feedback", []]},
                                  "as": "f",
                                  "cond": {"$eq": ["$$f.kind", "rule"]},
                              },
                          },
                      }}},
        {"$group": {"_id": None,
                    "tot": {"$sum": "$n"},
                    "tot_rules": {"$sum": "$n_rules"}}},
    ]):
        feedback_entries_total = int(r.get("tot") or 0)
        rules_promoted = int(r.get("tot_rules") or 0)

    by_prompt_version = {}
    for r in col.aggregate([
        {"$group": {"_id": "$prompt_version", "n": {"$sum": 1}}},
    ]):
        by_prompt_version[str(r.get("_id"))] = int(r.get("n") or 0)

    return {
        "total_summaries": total,
        "with_prompts": with_prompts,
        "with_feedback": with_feedback,
        "with_corrections": with_corrections,
        # Validations globales (label pose sur tout le rapport)
        "with_quality_good": with_quality_good_global,
        # Validations par section (cumul + nb distinct de rapports)
        "validations_good": validations_good,
        "validations_bad": validations_bad,
        "summaries_with_section_validation": len(summaries_with_section_validation),
        "feedback_entries_total": feedback_entries_total,
        "rules_promoted": rules_promoted,
        "by_prompt_version": by_prompt_version,
        # Volume samples potentiels (estimes) : 1 SFT par rapport with_prompts,
        # 1 DPO par rapport with_corrections.
        "estimated_sft_samples": with_prompts,
        "estimated_dpo_samples": with_corrections,
        "current_prompt_version": PROMPT_VERSION,
    }
