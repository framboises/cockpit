"""Prediction de renforts par porte d'acces (Cockpit Assistant IA).

Pour chaque porte d'acces de l'evenement courant, compare le pic de trafic
N-1 jour-equivalent (offset par rapport a la date de course) avec les fiches
d'incident PCO N-1 mentionnant cette porte, et propose des recommandations
de renfort sur la fenetre [now, now+24h].

Particularites :
- Les portes physiques sont regroupees en FAMILLES (ex. Famille NORD = PORTE
  NORD PIETONS + PORTE NORD VEHICULES + PORTE NORD BIS) car les fiches du PC
  org parlent rarement de la sous-porte exacte. La reco est emise au niveau
  famille.
- Certaines entrees de historique_controle ne sont pas des portes physiques
  (LITIGE, RENFORT BAF, HELPDESK, SERI, PUNISHER, UAM) : exclues.
- Le matching fiche -> famille se fait sur le full text (area.desc + text +
  text_full + comment + comment_history[].text), strategie "tokens" :
  intersection non vide entre les tokens significatifs (>3 chars) du nom de
  famille et ceux du blob.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from pymongo import ASCENDING

import pcorg_summary


logger = logging.getLogger(__name__)


# ============================================================================
# Constantes de configuration
# ============================================================================

CATEGORIES = ["PCO.Flux", "PCO.Securite", "PCO.Information", "PCO.MainCourante"]

# Tokens a exclure de la signature de famille (prefixes / suffixes generiques
# qui ne sont pas discriminants entre portes).
FAMILY_EXCLUDE_TOKENS = {
    "porte", "portail", "virage", "acces",
    "pieton", "pietons", "vehicule", "vehicules",
    "bis", "annexe",
}

# Tokens a exclure du matching dans les blobs de fiche (mots trop generiques).
MATCH_STOPWORDS = {
    "porte", "portail", "acces", "pieton", "pietons", "vehicule", "vehicules",
    "tribune", "zone", "secteur", "entree", "sortie", "site", "circuit",
    "le", "la", "les", "du", "de", "des", "un", "une", "et", "ou",
    "au", "aux", "vers", "sur", "dans", "pour", "avec",
    "pco", "pcs",
}

# Noms du historique_controle qui ne sont pas des portes physiques.
EXCLUDED_PSEUDO_DOORS = {
    "litige", "renfort baf", "helpdesk", "seri", "punisher", "uam",
}

MIN_TOKEN_LEN = 4
TOP_K_PEAKS = 3
MIN_FICHES_FOR_RECO = 2
MAX_BLOB_CHARS = 10000
WINDOW_AHEAD_HOURS = 24
SLOT_HOURS = 2  # creneaux de 2h, ancres sur les heures paires


# ============================================================================
# Helpers de normalisation / tokens
# ============================================================================

def _strip_accents(s: str) -> str:
    if not s:
        return ""
    nf = unicodedata.normalize("NFD", s)
    return "".join(c for c in nf if unicodedata.category(c) != "Mn")


def _normalize(s: str) -> str:
    if not s:
        return ""
    out = _strip_accents(s).lower()
    out = re.sub(r"[^a-z0-9]+", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def _tokens(s: str, stopwords: set | None = None) -> set:
    n = _normalize(s)
    if not n:
        return set()
    sw = stopwords or set()
    return {t for t in n.split() if len(t) >= MIN_TOKEN_LEN and t not in sw}


def _door_family_tokens(door_name: str) -> frozenset:
    """Retourne les tokens significatifs identifiant la famille d'une porte.

    Ex: "PORTE NORD VEHICULES" -> {"nord"}
        "PORTE TERTRE ROUGE" -> {"tertre", "rouge"}
        "VIRAGE TERTRE ROUGE" -> {"tertre", "rouge"}  (meme famille)
        "PORTAIL HOUX 5" -> {"houx"}
    """
    toks = _tokens(door_name) - FAMILY_EXCLUDE_TOKENS
    if not toks:
        # Fallback : on garde le nom normalise complet
        toks = {_normalize(door_name)}
    return frozenset(toks)


def _is_excluded_pseudo_door(door_name: str) -> bool:
    return _normalize(door_name) in EXCLUDED_PSEUDO_DOORS


# ============================================================================
# Construction des familles
# ============================================================================

def build_families(door_names: list[str]) -> dict:
    """Regroupe les noms de portes par signature de famille (set de tokens).

    Retourne dict family_key -> {
      'label': 'NORD' (libelle humain compact),
      'tokens': frozenset(...),
      'doors': [door_name, ...]
    }
    """
    families = defaultdict(lambda: {"label": "", "tokens": frozenset(), "doors": []})
    for name in door_names:
        if _is_excluded_pseudo_door(name):
            continue
        sig = _door_family_tokens(name)
        if not sig:
            continue
        key = "_".join(sorted(sig))
        if not families[key]["tokens"]:
            families[key]["tokens"] = sig
            families[key]["label"] = " ".join(sorted(sig)).upper()
        families[key]["doors"].append(name)
    return dict(families)


# ============================================================================
# Construction du blob d'une fiche
# ============================================================================

def _fiche_blob(doc) -> str:
    pieces = []
    area = doc.get("area") or {}
    if area.get("desc"):
        pieces.append(str(area["desc"]))
    for k in ("text", "text_full", "comment"):
        v = doc.get(k)
        if v:
            pieces.append(str(v))
    history = doc.get("comment_history") or []
    if isinstance(history, list):
        for h in history:
            if isinstance(h, dict) and h.get("text"):
                pieces.append(str(h["text"]))
    blob = "\n".join(pieces)
    if len(blob) > MAX_BLOB_CHARS:
        blob = blob[:MAX_BLOB_CHARS]
    return blob


def _fiche_matches_family(blob_tokens: set, family_tokens: frozenset) -> bool:
    return bool(blob_tokens & family_tokens)


# ============================================================================
# Bucketing par creneau de 2h
# ============================================================================

def _slot_index(dt: datetime) -> int:
    """Retourne l'index du creneau 2h pour un datetime (en jour relatif).

    On divise les heures (0..23) par 2, ancrees sur les heures paires.
    Combine avec date pour avoir un id unique par jour+slot.
    """
    return dt.hour // SLOT_HOURS


def _slot_start_dt(dt_paris: datetime) -> datetime:
    """Aligne dt sur le debut du creneau 2h le plus proche en arriere."""
    h = (dt_paris.hour // SLOT_HOURS) * SLOT_HOURS
    return dt_paris.replace(hour=h, minute=0, second=0, microsecond=0)


def _slot_label(dt_paris: datetime) -> str:
    """Ex: 14h-16h ou 'J+1 14h-16h' si pas le meme jour. Format compact :
    'DD/MM HHh-HHh'.
    """
    start = _slot_start_dt(dt_paris)
    end = start + timedelta(hours=SLOT_HOURS)
    return start.strftime("%d/%m") + " " + start.strftime("%Hh") + "-" + end.strftime("%Hh")


# ============================================================================
# Helpers Mongo
# ============================================================================

def _load_doors_doc(db, event, year_int):
    """Charge le doc historique_controle type=portes pour event/year."""
    doc = db["historique_controle"].find_one({"type": "portes", "event": event, "year": year_int})
    if not doc:
        doc = db["historique_controle"].find_one({"type": "portes", "event": event, "year": str(year_int)})
    return doc


def _load_fiches_n1(db, event, year_prev_int, ts_start_prev, ts_end_prev):
    """Charge les fiches PCO ciblees N-1 dans la fenetre alignee."""
    base_q = {
        "event": event,
        "year": year_prev_int,
        "category": {"$in": CATEGORIES},
        "ts": {"$gte": ts_start_prev, "$lte": ts_end_prev},
    }
    proj = {"_id": 1, "ts": 1, "category": 1, "area": 1, "text": 1,
            "text_full": 1, "comment": 1, "comment_history": 1}
    docs = list(db["pcorg"].find(base_q, proj).sort("ts", ASCENDING))
    if not docs:
        base_q["year"] = str(year_prev_int)
        docs = list(db["pcorg"].find(base_q, proj).sort("ts", ASCENDING))
    return docs


# ============================================================================
# Calcul principal
# ============================================================================

def compute_door_reinforcement(db, event, year, now_utc=None):
    """Retourne le bloc de recommandations de renfort pour les 24h a venir.

    Retourne None si :
    - event/year manquants
    - pas de date de course pour N ou N-1
    - pas de doc historique_controle type=portes pour N-1
    """
    if not event or year is None:
        return None
    try:
        year_int = int(year)
    except (TypeError, ValueError):
        return None

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    race_dt_n = pcorg_summary._load_race_dt(db, event, year_int)
    race_dt_prev = pcorg_summary._load_race_dt(db, event, year_int - 1)
    if not race_dt_n or not race_dt_prev:
        return None

    # Fenetre N : [now, now+24h]
    ts_start_n = now_utc
    ts_end_n = now_utc + timedelta(hours=WINDOW_AHEAD_HOURS)

    # Fenetre N-1 alignee
    ts_start_prev, ts_end_prev = pcorg_summary._aligned_prev_year_window(
        ts_start_n, ts_end_n, race_dt_n, race_dt_prev,
    )
    if not ts_start_prev or not ts_end_prev:
        return None

    # Charge doors N-1
    doors_doc = _load_doors_doc(db, event, year_int - 1)
    if not doors_doc or not doors_doc.get("doors"):
        return None

    door_names = [d.get("name", "").strip() for d in doors_doc["doors"] if d.get("name")]
    families = build_families(door_names)
    if not families:
        return None

    # Pour chaque famille : agrege scans par creneau de 2h dans la fenetre N-1.
    # Cle bucket = datetime debut de creneau en Paris (aligne 2h).
    # scans_by_family[fam_key][bucket_paris] = total_scans
    scans_by_family = {fk: defaultdict(int) for fk in families}
    door_to_family = {}
    for fk, fam in families.items():
        for d in fam["doors"]:
            door_to_family[d] = fk

    for d in doors_doc["doors"]:
        name = (d.get("name") or "").strip()
        if not name or name not in door_to_family:
            continue
        fk = door_to_family[name]
        for s in d.get("scans") or []:
            ts_raw = s.get("timestamp")
            count = int(s.get("scan_count") or 0)
            if not ts_raw or count <= 0:
                continue
            if isinstance(ts_raw, datetime):
                ts = ts_raw
            else:
                try:
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=pcorg_summary.TZ_PARIS)
            ts_utc = ts.astimezone(timezone.utc)
            if not (ts_start_prev <= ts_utc <= ts_end_prev):
                continue
            ts_paris = ts_utc.astimezone(pcorg_summary.TZ_PARIS)
            bucket = _slot_start_dt(ts_paris)
            scans_by_family[fk][bucket] += count

    # Top 3 creneaux par famille
    top_buckets_by_family = {}
    for fk, buckets in scans_by_family.items():
        if not buckets:
            top_buckets_by_family[fk] = set()
            continue
        sorted_b = sorted(buckets.items(), key=lambda kv: -kv[1])
        top_buckets_by_family[fk] = {b for b, _ in sorted_b[:TOP_K_PEAKS]}

    # Charge fiches N-1 dans la fenetre alignee
    fiches = _load_fiches_n1(db, event, year_int - 1, ts_start_prev, ts_end_prev)

    # Pour chaque fiche : tokens du blob, familles matchees, et bucket
    # Compte fiches par (family_key, bucket) et par categorie.
    # fiches_by_family_bucket[fk][bucket] = {category: count, "_total": int}
    fiches_by_family_bucket = {fk: defaultdict(lambda: defaultdict(int)) for fk in families}
    for f in fiches:
        ts_raw = f.get("ts")
        if not isinstance(ts_raw, datetime):
            try:
                ts_raw = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
        ts_utc = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=timezone.utc)
        ts_paris = ts_utc.astimezone(pcorg_summary.TZ_PARIS)
        bucket = _slot_start_dt(ts_paris)
        blob = _fiche_blob(f)
        btoks = _tokens(blob, MATCH_STOPWORDS)
        cat = f.get("category") or "?"
        for fk, fam in families.items():
            if _fiche_matches_family(btoks, fam["tokens"]):
                fiches_by_family_bucket[fk][bucket][cat] += 1
                fiches_by_family_bucket[fk][bucket]["_total"] += 1

    # Construction des recommandations
    # Pour chaque (famille, bucket present dans top OU dans incidents),
    # decide criticite. Mappe le bucket N-1 -> bucket equivalent N.
    offset_seconds = (race_dt_n - race_dt_prev).total_seconds()

    recos = []
    for fk, fam in families.items():
        top_buckets = top_buckets_by_family.get(fk, set())
        all_buckets = set(scans_by_family[fk].keys()) | set(fiches_by_family_bucket[fk].keys())
        for bucket_prev in all_buckets:
            scan_count = scans_by_family[fk].get(bucket_prev, 0)
            fcounts = fiches_by_family_bucket[fk].get(bucket_prev, {})
            n_fiches = int(fcounts.get("_total", 0))
            is_pic = bucket_prev in top_buckets
            has_incidents = n_fiches >= MIN_FICHES_FOR_RECO

            if not is_pic and not has_incidents:
                continue
            if is_pic and has_incidents:
                criticite = "forte"
            else:
                criticite = "moderee"

            # Translate bucket_prev (Paris) -> bucket_n (Paris) via offset secondes
            # On s'aligne ensuite sur le creneau 2h.
            bucket_prev_utc = bucket_prev.astimezone(timezone.utc)
            bucket_n_utc = bucket_prev_utc + timedelta(seconds=offset_seconds)
            bucket_n_paris = _slot_start_dt(bucket_n_utc.astimezone(pcorg_summary.TZ_PARIS))

            # Categories serialisables (sans la cle _total)
            by_cat = {k: v for k, v in fcounts.items() if k != "_total"}

            # Phrase de raison synthetique
            reason_bits = []
            if is_pic:
                rank = sorted(scans_by_family[fk].values(), reverse=True).index(scan_count) + 1
                reason_bits.append("pic N-1 (top " + str(rank) + ", " + str(scan_count) + " scans)")
            if has_incidents:
                reason_bits.append(str(n_fiches) + " incident(s) N-1")
            reason = " + ".join(reason_bits) if reason_bits else ""

            recos.append({
                "family_key": fk,
                "family_label": fam["label"],
                "doors": fam["doors"],
                "slot_n_start": bucket_n_paris.isoformat(),
                "slot_n_end": (bucket_n_paris + timedelta(hours=SLOT_HOURS)).isoformat(),
                "slot_label_n": _slot_label(bucket_n_paris),
                "slot_label_prev": _slot_label(bucket_prev),
                "n1_scan_count": scan_count,
                "is_top3_pic": is_pic,
                "n1_fiches_count": n_fiches,
                "n1_fiches_by_category": by_cat,
                "criticite": criticite,
                "reason": reason,
            })

    # Tri : criticite (forte d'abord), puis par slot N croissant
    recos.sort(key=lambda r: (0 if r["criticite"] == "forte" else 1, r["slot_n_start"]))

    if not recos:
        return None

    return {
        "race_n": race_dt_n.isoformat(),
        "race_n_minus_1": race_dt_prev.isoformat(),
        "window_n": [ts_start_n.isoformat(), ts_end_n.isoformat()],
        "window_n_minus_1": [ts_start_prev.isoformat(), ts_end_prev.isoformat()],
        "year_prev": year_int - 1,
        "matching_strategy": "B_full_text_S2_tokens_familles",
        "families": [
            {"key": fk, "label": fam["label"], "doors": fam["doors"]}
            for fk, fam in families.items()
        ],
        "recommendations": recos,
    }
