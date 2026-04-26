"""Resume de periode des fiches PC Organisation via l'API Claude.

Module helpers pur (pas de blueprint Flask) : les routes vivent dans app.py
a cote des autres routes /api/pcorg/* pour rester coherent.

Pattern d'appel HTTP externe calque sur traffic.py (Waze) et routing.py (Valhalla).
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

import requests
from pymongo import ASCENDING, DESCENDING


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Configuration (variables d'environnement)
# ----------------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6").strip()
CLAUDE_TIMEOUT_SECONDS = int(os.getenv("CLAUDE_TIMEOUT_SECONDS", "60"))
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "2048"))


SUMMARIES_COLLECTION = "pcorg_summaries"
PCORG_COLLECTION = "pcorg"

# Plafond du nombre de fiches transmises a Claude (apres priorisation).
DEFAULT_MAX_FICHES = 80
TEXT_TRUNCATE_CHARS = 800
COMMENTS_KEEP_LAST = 3

# Cles de sortie attendues du modele.
SECTION_KEYS = (
    "faits_marquants",
    "secours",
    "securite",
    "technique",
    "recommandations",
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

def build_prompts(event, year, ts_start, ts_end, kpis, fiches, truncated):
    """Retourne (system_prompt, user_prompt) en francais."""
    system = (
        "Tu es un analyste operationnel pour un PC Organisation d'evenement "
        "(festival, course automobile). On te fournit des KPIs agreges et un "
        "echantillon de fiches d'intervention. Tu produis un compte-rendu "
        "concis, factuel, en francais, destine aux managers en debrief.\n"
        "\n"
        "Contraintes strictes :\n"
        "- Reponds UNIQUEMENT par un objet JSON valide, sans texte avant ou "
        "apres, sans bloc markdown.\n"
        "- L'objet contient EXACTEMENT ces 5 cles : faits_marquants, secours, "
        "securite, technique, recommandations.\n"
        "- Chaque valeur est une chaine en texte clair (pas de markdown), "
        "2 a 6 phrases courtes maximum, ou 'RAS' si rien a signaler.\n"
        "- N'invente jamais de chiffres : appuie-toi uniquement sur les "
        "donnees fournies.\n"
        "- Mentionne les fiches majeures (urgence EU/UA ou incidents) dans "
        "faits_marquants.\n"
        "- N'utilise pas de guillemets typographiques courbes : uniquement "
        "des apostrophes droites et guillemets droits.\n"
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
    user = (
        "Contexte :\n"
        "- Perimetre : " + scope_label + "\n"
        "- Periode : " + period_iso_start + " --> " + period_iso_end + "\n"
        "- Echantillon tronque : " + ("oui" if truncated else "non") + "\n"
        "\n"
        "KPIs :\n"
        + json.dumps(kpis, ensure_ascii=False, indent=2, default=_json_default)
        + "\n\nFiches (" + str(len(fiches)) + " sur " + str(kpis.get("total", 0)) + " au total) :\n"
        + json.dumps(fiches, ensure_ascii=False, indent=2, default=_json_default)
        + "\n\nProduis le JSON demande."
    )
    return system, user


# ----------------------------------------------------------------------------
# Appel API Claude
# ----------------------------------------------------------------------------

class ClaudeError(Exception):
    """Erreur lors de l'appel a l'API Anthropic."""


def call_claude(system_prompt, user_prompt):
    """Appelle l'API Claude et retourne (sections_dict, raw_text, usage).

    Si le retour n'est pas du JSON parsable, sections_dict est None et
    raw_text contient la reponse brute (l'appelant decide comment l'afficher).
    Leve ClaudeError pour les erreurs reseau / HTTP / config.
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
        "max_tokens": CLAUDE_MAX_TOKENS,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_prompt},
        ],
    }
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers=headers,
            json=body,
            timeout=CLAUDE_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        logger.warning("Claude API injoignable: %s", e)
        raise ClaudeError("claude_unreachable")

    if resp.status_code >= 400:
        snippet = (resp.text or "")[:500]
        logger.warning("Claude API HTTP %s : %s", resp.status_code, snippet)
        raise ClaudeError("claude_http_" + str(resp.status_code))

    try:
        payload = resp.json()
    except ValueError as e:
        logger.warning("Claude API : reponse non JSON : %s", e)
        raise ClaudeError("claude_invalid_response")

    parts = payload.get("content") or []
    raw_text = ""
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            raw_text += p.get("text") or ""

    usage = payload.get("usage") or {}
    usage_clean = {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }

    sections = _parse_sections(raw_text)
    return sections, raw_text, usage_clean


def _parse_sections(raw_text):
    """Tente de parser un JSON dans raw_text et de retourner un dict de sections.

    Retourne None si le parsing echoue.
    """
    if not raw_text:
        return None
    txt = raw_text.strip()
    # Tolere un eventuel bloc markdown ```json ... ```
    if txt.startswith("```"):
        # supprime la premiere ligne
        nl = txt.find("\n")
        if nl != -1:
            txt = txt[nl + 1:]
        if txt.endswith("```"):
            txt = txt[:-3]
        txt = txt.strip()
    try:
        data = json.loads(txt)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    out = {}
    for key in SECTION_KEYS:
        v = data.get(key)
        out[key] = str(v).strip() if v is not None else ""
    return out


# ----------------------------------------------------------------------------
# Persistance MongoDB
# ----------------------------------------------------------------------------

def save_summary(db, event, year, ts_start, ts_end, created_by_email, created_by_name,
                 kpis, fiches_count, truncated, sections, raw_text, usage):
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
        out["sections"] = doc.get("sections") or {}
        out["raw_text"] = doc.get("raw_text") or ""
        out["usage"] = doc.get("usage") or {}
    return out


# ----------------------------------------------------------------------------
# Orchestration principale
# ----------------------------------------------------------------------------

def generate_period_summary(db, event, year, ts_start, ts_end, created_by_email, created_by_name):
    """Calcule KPIs, appelle Claude (si fiches > 0), sauve, retourne le doc."""
    kpis = compute_kpis(db, event, year, ts_start, ts_end)
    if kpis["total"] == 0:
        # RAS : pas d'appel Claude, sections vides.
        sections = {k: "RAS" for k in SECTION_KEYS}
        return save_summary(
            db, event, year, ts_start, ts_end, created_by_email, created_by_name,
            kpis, 0, False, sections, "", {"input_tokens": 0, "output_tokens": 0},
        )

    fiches, total, truncated = select_fiches_for_prompt(db, event, year, ts_start, ts_end)
    system, user = build_prompts(event, year, ts_start, ts_end, kpis, fiches, truncated)
    sections, raw_text, usage = call_claude(system, user)
    if sections is None:
        # JSON non parsable : on conserve le texte brut dans faits_marquants
        # et on remplit les autres en RAS pour ne rien perdre.
        sections = {k: "" for k in SECTION_KEYS}
        sections["faits_marquants"] = raw_text or "Reponse Claude non parsable."

    return save_summary(
        db, event, year, ts_start, ts_end, created_by_email, created_by_name,
        kpis, len(fiches), truncated, sections, raw_text, usage,
    )
