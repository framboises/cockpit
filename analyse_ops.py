# analyse_ops.py -- Blueprint Analyse Ops (Laboratoire post-evenement PCOrg)
import os
import re
import json
import threading
import logging
import hashlib
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from dateutil import tz
from flask import Blueprint, jsonify, request, render_template
from pymongo import MongoClient
from bson import json_util
import html
import unicodedata

analyse_ops_bp = Blueprint("analyse_ops", __name__)
logger = logging.getLogger(__name__)

PARIS = tz.gettz("Europe/Paris")

# ---------------------------------------------------------------------------
# MongoDB (lazy init -- set by _ensure_db)
# ---------------------------------------------------------------------------
_db = None
_col_pcorg = None
_col_cache = None
_col_grid_ref = None

def _ensure_db():
    global _db, _col_pcorg, _col_cache, _col_grid_ref
    if _db is not None:
        return
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    client = MongoClient(uri)
    _db = client["titan"]
    _col_pcorg = _db["pcorg"]
    _col_grid_ref = _db["grid_ref"]
    _col_cache = _db["analyse_ops_cache"]
    _col_cache.create_index([("event", 1), ("year", 1), ("module", 1)], unique=True)
    _col_cache.create_index("computed_at", expireAfterSeconds=30 * 24 * 3600)

# ---------------------------------------------------------------------------
# Compute state (in-memory, per-process)
# ---------------------------------------------------------------------------
_compute_state = {
    "status": "idle",        # idle | computing | done | error
    "progress": 0,           # 0-100
    "event": None,
    "year": None,
    "error": None,
}
_compute_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Helpers (ported from pcorg_report_reportcss.py)
# ---------------------------------------------------------------------------
def _minutes_between(a, b):
    if a is None or b is None:
        return None
    try:
        return (b - a).total_seconds() / 60.0
    except Exception:
        return None

def _pct(n, d):
    return round(100.0 * n / d, 1) if d and d > 0 else 0.0

def _sanitize(s):
    return None if s is None else str(s).strip()

def _safe_len(s):
    return len(s) if isinstance(s, str) else 0

def _human_td_minutes(m):
    if m is None or (isinstance(m, float) and np.isnan(m)):
        return "N/A"
    if m < 60:
        return f"{int(round(m))} min"
    h = int(m // 60)
    r = int(round(m - 60 * h))
    return f"{h}h{r:02d}"

def clean_operator_name(x):
    if not x:
        return "Inconnu"
    s = re.sub(r"\s*\[.*\]\s*$", "", str(x)).strip()
    if not s:
        return "Inconnu"
    parts = s.split()
    if len(parts) == 1:
        return parts[0].upper()
    prenom = parts[-1].capitalize()
    nom = " ".join(parts[:-1]).upper()
    return f"{nom} {prenom}"

def normalize_services(s):
    if not s:
        return []
    s = str(s)
    s = html.unescape(s)
    s = re.sub(r"(?:&amp;)?\\x3B", ";", s, flags=re.IGNORECASE)
    s = re.sub(r"(?:&amp;)?\bx3B\b", ";", s, flags=re.IGNORECASE)
    s = s.replace("\x3B", ";")
    s = (s.replace("\\", ";").replace("/", ";").replace("|", ";").replace(",", ";"))
    parts = [p.strip(" .\t\r\n") for p in s.split(";") if p and p.strip(" .\t\r\n")]
    parts = [" ".join(p.split()) for p in parts]
    return parts

def clean_service_label(lbl):
    if not lbl:
        return "Inconnu"
    s = str(lbl).strip().rstrip(".")
    s = " ".join(s.split())
    return s

def _strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")

STOPWORDS_FR = {
    "a", "ai", "ainsi", "apres", "attn", "au", "aucun", "aucune", "aura", "auront",
    "aussi", "autre", "aux", "avec", "avoir", "avons", "demande", "bon", "car", "cela",
    "ces", "cet", "cette", "ceci", "ce", "comme", "comment", "contre", "dans", "de",
    "des", "du", "donc", "dos", "deja", "elle", "elles", "en", "encore", "entre", "est",
    "et", "etaient", "etait", "etant", "etais", "ete", "etre", "fait", "faut", "fois",
    "font", "grand", "grande", "grandes", "grands", "hors", "hui", "ici", "il", "ils",
    "je", "jusqu", "jusque", "la", "le", "les", "leur", "leurs", "ma", "mais", "me",
    "mes", "moi", "mon", "ne", "ni", "non", "nos", "notre", "nous", "on", "ou", "par",
    "parce", "pas", "peu", "peut", "peuvent", "plus", "pour", "pourra", "pourrait",
    "pres", "pris", "prend", "prendrait", "prendre", "qu", "quand", "que", "quel",
    "quelle", "quelles", "quels", "qui", "sa", "sans", "se", "ses", "si", "sont",
    "sous", "sur", "ta", "te", "tes", "toi", "ton", "tous", "tout", "toute", "toutes",
    "tres", "tu", "un", "une", "vers", "vos", "votre", "vous",
    "pc", "org", "pcorg", "incident", "incidents", "appel", "appels", "radio",
    "telephone", "etc", "svp", "mr", "mme", "km", "kmh", "mn", "min",
    "statut", "termine", "terminee", "cours", "classe", "vu", "anciennete",
}

# Appelant aliases (from pcorg_report)
APPELANT_ALIASES = {
    "PCORG": ["PCO", "PC ORG", "PC ORGANISATION", "PC ORG A CD HYPER CENTRE",
              "PC ORG > RESP PANORAMA", "PC ORG S BATTEUX", "PC ORG BENOIT COULBAUT",
              "CGO", "OSE", "GROUPE WHATSAPP PCORG"],
    "OPV": ["VIDEO PC ORG", "CAMERA PCORG", "CAMERA PC ORG",
            "PCORG OPERATEUR VIDEOAK", "OPV", "OPERATEUR VIDEO", "VIDEO PC"],
    "DIRECTION DE COURSE": ["DIRECTION COURSE", "PC COURSE", "DIRECTION DE COURSE LOIC"],
    "HELP DESK": ["HELP DESCK"],
    "PCA": ["PCA", "GUY CAMERA PCA", "PC FLUX", "DIRECTRICE DE CABINET PREFECTURE"],
}

_re_status_line = re.compile(r"^\s*statut\s*:", re.I)
_re_stamp_line = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s*,", re.I)

def _clean_text_blob(s):
    if not s:
        return ""
    lines = []
    for ln in str(s).splitlines():
        if _re_status_line.search(ln):
            continue
        if _re_stamp_line.search(ln):
            continue
        lines.append(ln)
    s = "\n".join(lines)
    s = re.sub(r"\[[^\]]+\]", " ", s)
    return s

def _tokenize_basic(s):
    s = s.lower()
    s = _strip_accents(s)
    s = re.sub(r"https?://\S+|www\.\S+|\S+@\S+", " ", s)
    s = re.sub(r"[^a-z' -]", " ", s)
    s = re.sub(r"[-_'']", " ", s)
    return [t for t in s.split() if len(t) >= 3]

# ---------------------------------------------------------------------------
# Flatten a pcorg document into a flat dict (from pcorg_report_reportcss.py)
# ---------------------------------------------------------------------------
def _flatten_doc(doc):
    ts = doc.get("ts")
    close_ts = doc.get("close_ts")
    xml = doc.get("xml_struct") or {}
    caller = xml.get("caller") or {}
    flags = xml.get("flags") or caller.get("flags") or {}
    cl = xml.get("classification") or {}
    res = xml.get("resource") or {}
    cc = doc.get("content_category") or {}
    delay_min = _minutes_between(ts, close_ts)
    gps = doc.get("gps")
    lat = lon = None
    if gps and isinstance(gps, dict) and gps.get("coordinates"):
        coords = gps["coordinates"]
        if len(coords) >= 2:
            lon, lat = float(coords[0]), float(coords[1])
    return {
        "_id": doc.get("_id"),
        "ts": ts,
        "close_ts": close_ts,
        "delay_min": delay_min,
        "date_local": doc.get("date_local"),
        "time_local": doc.get("time_local"),
        "source": _sanitize(doc.get("source")),
        "category": _sanitize(doc.get("category")),
        "area_id": _sanitize((doc.get("area") or {}).get("id")),
        "area_desc": _sanitize((doc.get("area") or {}).get("desc")),
        "group_names": _sanitize((doc.get("group") or {}).get("names")),
        "status_code": doc.get("status_code"),
        "severity": doc.get("severity"),
        "is_incident": bool(doc.get("is_incident")) if doc.get("is_incident") is not None else None,
        "operator_create": _sanitize(doc.get("operator")),
        "operator_close": _sanitize(doc.get("operator_close")),
        "text_len": _safe_len(doc.get("text")),
        "text_full_len": _safe_len(doc.get("text_full")),
        "comment_len": _safe_len(doc.get("comment")),
        "sous_classification": _sanitize(cl.get("sous") or cc.get("sous_classification")),
        "appelant": _sanitize(caller.get("appelant") or cc.get("appelant")),
        "flag_tel": bool(flags.get("telephone")) if "telephone" in flags else False,
        "flag_radio": bool(flags.get("radio")) if "radio" in flags else False,
        "carroye": _sanitize(res.get("carroye") or cc.get("carroye")),
        "text": _sanitize(doc.get("text")),
        "text_full": _sanitize(doc.get("text_full")),
        "comment_text": _sanitize(doc.get("comment")),
        "services_contactes_raw": _sanitize(xml.get("service_contacte") or cc.get("service_contacte")),
        "lat": lat,
        "lon": lon,
        "extracted_plates": (doc.get("extracted") or {}).get("plates") or [],
    }

# ---------------------------------------------------------------------------
# Source hash for cache invalidation
# ---------------------------------------------------------------------------
def _source_hash(event, year):
    _ensure_db()
    count = _col_pcorg.count_documents({"event": event, "year": year})
    last = _col_pcorg.find_one({"event": event, "year": year}, sort=[("ts", -1)], projection={"ts": 1})
    last_ts = str(last["ts"]) if last and last.get("ts") else "none"
    return f"{count}_{last_ts}"

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def _cache_get(event, year, module):
    _ensure_db()
    doc = _col_cache.find_one({"event": event, "year": year, "module": module})
    if not doc:
        return None
    current_hash = _source_hash(event, year)
    if doc.get("source_hash") != current_hash:
        return None
    return doc.get("data")

def _cache_set(event, year, module, data):
    _ensure_db()
    _col_cache.update_one(
        {"event": event, "year": year, "module": module},
        {"$set": {
            "data": data,
            "computed_at": datetime.now(timezone.utc),
            "source_hash": _source_hash(event, year),
        }},
        upsert=True,
    )

# ---------------------------------------------------------------------------
# Auth helper (import from app at request time to avoid circular import)
# ---------------------------------------------------------------------------
def _check_admin():
    from app import role_required, CODING, JWT_SECRET, JWT_ALGORITHM, ROLE_HIERARCHY, ROLE_ORDER, APP_KEY, SUPER_ADMIN_ROLE, BASE_URL
    import jwt as pyjwt
    if CODING:
        sim_role = request.args.get("as", "admin")
        if sim_role not in ROLE_HIERARCHY:
            sim_role = "admin"
        sim_level = ROLE_HIERARCHY[sim_role]
        sim_roles = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= sim_level]
        request.user_payload = {
            "apps": ["cockpit"],
            "roles_by_app": {"cockpit": sim_role},
            "global_roles": [],
            "roles": sim_roles,
            "app_role": sim_role,
            "is_super_admin": False,
            "firstname": "Bruce",
            "lastname": "WAYNE",
            "email": "bruce@wayneenterprise.com",
        }
        return None
    token = request.cookies.get("access_token")
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    roles = payload.get("roles_by_app", {}).get(APP_KEY, "")
    if isinstance(roles, str):
        roles = [roles]
    max_level = max((ROLE_HIERARCHY.get(r, 0) for r in roles), default=0)
    if max_level < ROLE_HIERARCHY.get("admin", 3):
        return jsonify({"error": "Admin required"}), 403
    request.user_payload = payload
    return None

@analyse_ops_bp.before_request
def _before():
    err = _check_admin()
    if err:
        return err

# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------
@analyse_ops_bp.route("/analyse-ops")
def analyse_ops_page():
    from app import role_required
    payload = getattr(request, "user_payload", {})
    user_roles = payload.get("roles", [])
    user_firstname = payload.get("firstname", "")
    user_lastname = payload.get("lastname", "")
    user_email = payload.get("email", "")
    return render_template("analyse_ops.html",
                           user_roles=user_roles,
                           user_firstname=user_firstname,
                           user_lastname=user_lastname,
                           user_email=user_email)

# ---------------------------------------------------------------------------
# Fiches search (live query, not cached)
# ---------------------------------------------------------------------------
@analyse_ops_bp.route("/api/analyse-ops/fiches")
def search_fiches():
    _ensure_db()
    event = request.args.get("event")
    year = request.args.get("year")
    q = request.args.get("q", "").strip()
    page = int(request.args.get("page", 0))
    per_page = 50
    if not event or not year:
        return jsonify({"error": "event and year required"}), 400
    try:
        year = int(year)
    except (ValueError, TypeError):
        pass
    query = {"event": event, "year": year}
    if q:
        # Search across multiple text fields with regex (case insensitive)
        regex = {"$regex": q, "$options": "i"}
        query["$or"] = [
            {"text": regex},
            {"text_full": regex},
            {"comment": regex},
            {"operator": regex},
            {"operator_close": regex},
            {"category": regex},
            {"source": regex},
        ]
        # Also try area.desc and content_category fields
        query["$or"].append({"area.desc": regex})
        query["$or"].append({"content_category.sous_classification": regex})
        query["$or"].append({"content_category.appelant": regex})
    total = _col_pcorg.count_documents(query)
    docs = list(_col_pcorg.find(query, sort=[("ts", -1)]).skip(page * per_page).limit(per_page))
    fiches = []
    for d in docs:
        ts = d.get("ts")
        close_ts = d.get("close_ts")
        delay = _minutes_between(ts, close_ts)
        gps = d.get("gps")
        lat = lon = None
        if gps and isinstance(gps, dict) and gps.get("coordinates"):
            coords = gps["coordinates"]
            if len(coords) >= 2:
                lon, lat = float(coords[0]), float(coords[1])
        cc = d.get("content_category") or {}
        xml_s = d.get("xml_struct") or {}
        cl = xml_s.get("classification") or {}
        # Intervenants
        intervs = []
        for i in range(1, 6):
            v = cc.get(f"intervenant{i}") or xml_s.get(f"intervenant{i}")
            if v and str(v).strip():
                intervs.append(str(v).strip())
        fiches.append({
            "id": str(d.get("_id", "")),
            "sql_id": d.get("sql_id"),
            "ts": str(ts) if ts else "",
            "close_ts": str(close_ts) if close_ts else "",
            "date_local": d.get("date_local", ""),
            "time_local": d.get("time_local", ""),
            "category": d.get("category", ""),
            "source": d.get("source", ""),
            "text": d.get("text", "") or "",
            "text_full": d.get("text_full", "") or "",
            "comment": d.get("comment", "") or "",
            "operator": d.get("operator", ""),
            "operator_close": d.get("operator_close", ""),
            "area_id": (d.get("area") or {}).get("id"),
            "area": (d.get("area") or {}).get("desc", ""),
            "group": (d.get("group") or {}).get("desc", ""),
            "severity": d.get("severity", 0),
            "status_code": d.get("status_code"),
            "is_incident": d.get("is_incident"),
            "sous_class": cl.get("sous") or cc.get("sous_classification", ""),
            "classification": cl.get("principale") or cc.get("classification", ""),
            "motif": cl.get("motif") or cc.get("motif_intervention", ""),
            "appelant": (xml_s.get("caller") or {}).get("appelant") or cc.get("appelant", ""),
            "telephone": bool(cc.get("telephone") or (xml_s.get("flags") or {}).get("telephone")),
            "radio": bool(cc.get("radio") or (xml_s.get("flags") or {}).get("radio")),
            "carroye": cc.get("carroye") or (xml_s.get("resource") or {}).get("carroye", ""),
            "service_contacte": xml_s.get("service_contacte") or cc.get("service_contacte", ""),
            "intervenants": intervs,
            "date_heure_xml": cc.get("date_heure", ""),
            "delay_min": round(delay, 1) if delay is not None else None,
            "lat": lat,
            "lon": lon,
            "extracted_phones": (d.get("extracted") or {}).get("phones") or [],
            "extracted_plates": (d.get("extracted") or {}).get("plates") or [],
        })
    return jsonify({"total": total, "page": page, "per_page": per_page, "fiches": fiches})

# ---------------------------------------------------------------------------
# Timeline data (lightweight for all fiches)
# ---------------------------------------------------------------------------
@analyse_ops_bp.route("/api/analyse-ops/timeline-data")
def timeline_data():
    _ensure_db()
    event = request.args.get("event")
    year = request.args.get("year")
    if not event or not year:
        return jsonify({"error": "event and year required"}), 400
    try:
        year = int(year)
    except (ValueError, TypeError):
        pass
    docs = list(_col_pcorg.find(
        {"event": event, "year": year},
        {"ts": 1, "close_ts": 1, "category": 1, "source": 1, "text": 1, "severity": 1,
         "area": 1, "operator": 1, "status_code": 1, "date_local": 1, "time_local": 1,
         "content_category.sous_classification": 1, "xml_struct.classification.sous": 1,
         "gps": 1},
        sort=[("ts", 1)],
    ))
    items = []
    for d in docs:
        ts = d.get("ts")
        if not ts:
            continue
        close_ts = d.get("close_ts")
        cc = d.get("content_category") or {}
        xml_s = d.get("xml_struct") or {}
        cl = xml_s.get("classification") or {}
        gps = d.get("gps")
        lat = lon = None
        if gps and isinstance(gps, dict) and gps.get("coordinates"):
            coords = gps["coordinates"]
            if len(coords) >= 2:
                lon, lat = float(coords[0]), float(coords[1])
        items.append({
            "id": str(d.get("_id", "")),
            "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "close_ts": close_ts.isoformat() if close_ts and hasattr(close_ts, "isoformat") else None,
            "cat": d.get("category", ""),
            "text": (d.get("text", "") or "")[:120],
            "sev": d.get("severity", 0),
            "area": (d.get("area") or {}).get("desc", ""),
            "op": (d.get("operator") or "").split("[")[0].strip(),
            "sc": cl.get("sous") or cc.get("sous_classification", ""),
            "lat": lat,
            "lon": lon,
        })
    return jsonify({"items": items, "total": len(items)})

# ---------------------------------------------------------------------------
# Compute endpoints
# ---------------------------------------------------------------------------
@analyse_ops_bp.route("/api/analyse-ops/compute", methods=["POST"])
def compute():
    global _compute_state
    data = request.get_json(silent=True) or {}
    event = data.get("event") or request.args.get("event")
    year = data.get("year") or request.args.get("year")
    if not event or not year:
        return jsonify({"error": "event and year required"}), 400
    # Convert year to int if possible
    try:
        year = int(year)
    except (ValueError, TypeError):
        pass
    with _compute_lock:
        if _compute_state["status"] == "computing":
            return jsonify({"error": "Compute already in progress"}), 409
        _compute_state = {"status": "computing", "progress": 0, "event": event, "year": year, "error": None}
    t = threading.Thread(target=_run_compute, args=(event, year), daemon=True)
    t.start()
    return jsonify({"status": "computing", "event": event, "year": year})

@analyse_ops_bp.route("/api/analyse-ops/status")
def compute_status():
    return jsonify(_compute_state)

# ---------------------------------------------------------------------------
# Module GET endpoints (serve from cache)
# ---------------------------------------------------------------------------
_MODULES = [
    "kpis", "temporal", "geographic", "performance", "categories",
    "operators", "services", "intervenants", "text", "appelants",
    "meteo-cross", "affluence-cross", "waze-cross", "convergence",
    "comparative", "escalation", "zones-vulnerability", "effectifs-cross",
    "anpr-cross", "network", "quality",
]

def _make_module_route(module_name):
    def handler():
        event = request.args.get("event")
        year = request.args.get("year")
        if not event or not year:
            return jsonify({"error": "event and year required"}), 400
        try:
            year = int(year)
        except (ValueError, TypeError):
            pass
        cached = _cache_get(event, year, module_name)
        if cached is not None:
            return jsonify({"status": "ok", "data": cached})
        return jsonify({"status": "stale", "data": None})
    handler.__name__ = f"get_{module_name.replace('-', '_')}"
    return handler

for _mod in _MODULES:
    analyse_ops_bp.add_url_rule(
        f"/api/analyse-ops/{_mod}",
        endpoint=f"get_{_mod.replace('-', '_')}",
        view_func=_make_module_route(_mod),
    )

# ---------------------------------------------------------------------------
# Compute engine (background thread)
# ---------------------------------------------------------------------------
def _run_compute(event, year):
    global _compute_state
    try:
        _ensure_db()
        total_modules = 21
        completed = 0

        def _progress():
            nonlocal completed
            completed += 1
            _compute_state["progress"] = int(100 * completed / total_modules)

        # =====================================================================
        # 1) Load all pcorg documents for this event/year
        # =====================================================================
        docs = list(_col_pcorg.find({"event": event, "year": year}))
        if not docs:
            _compute_state = {"status": "error", "progress": 0, "event": event, "year": year,
                              "error": f"Aucun document pour {event} {year}"}
            return

        df = pd.DataFrame([_flatten_doc(d) for d in docs])

        # Clean operators
        df["operator_create"] = df["operator_create"].apply(clean_operator_name)
        df["operator_close"] = df["operator_close"].apply(clean_operator_name)

        # Normalize carroyes
        df["carroye"] = df["carroye"].astype(str).str.upper().str.replace(" ", "", regex=False).replace("NONE", np.nan)

        # Parse timestamps
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)
        df["close_ts"] = pd.to_datetime(df["close_ts"], errors="coerce", utc=True)
        df["delay_min"] = pd.to_numeric(df["delay_min"], errors="coerce")

        # Temporal derivatives
        dft = df.dropna(subset=["ts"]).copy()
        ts_paris = dft["ts"].dt.tz_convert("Europe/Paris")
        dft["date"] = ts_paris.dt.date
        dft["hour"] = ts_paris.dt.hour
        try:
            dft["dow_name"] = ts_paris.dt.day_name(locale="fr_FR")
        except Exception:
            dft["dow_name"] = ts_paris.dt.day_name()

        # Services
        df["services_list"] = df["services_contactes_raw"].apply(normalize_services)

        N = len(df)
        N_closed = int(pd.notna(df["delay_min"]).sum())

        # =====================================================================
        # 2) KPIs
        # =====================================================================
        same_op = df["operator_create"].fillna("Inconnu") == df["operator_close"].fillna("Inconnu")
        fast_close = df["delay_min"].le(30)
        fcr_mask = same_op & fast_close
        fcr_count = int(fcr_mask.sum())

        median_delay = float(np.nanmedian(df["delay_min"])) if N_closed else None
        p90_delay = float(np.nanpercentile(df["delay_min"].dropna(), 90)) if N_closed else None

        def sla_share(thr):
            if N_closed == 0:
                return 0.0
            s = df["delay_min"].dropna()
            return _pct(int((s <= thr).sum()), int(len(s)))

        tel_share = _pct(int(df["flag_tel"].sum()), N)
        radio_share = _pct(int(df["flag_radio"].sum()), N)

        # Quality score
        quality_fields = ["sous_classification", "appelant", "carroye", "services_contactes_raw"]
        filled_counts = {f: int(df[f].notna().sum()) for f in quality_fields if f in df.columns}
        n_perfect = int(df[quality_fields].notna().all(axis=1).sum()) if all(f in df.columns for f in quality_fields) else 0

        # Date range
        date_min = str(dft["date"].min()) if not dft.empty else None
        date_max = str(dft["date"].max()) if not dft.empty else None

        kpis_data = {
            "total": N,
            "total_closed": N_closed,
            "fcr_count": fcr_count,
            "fcr_rate": _pct(fcr_count, N),
            "median_delay_min": round(median_delay, 1) if median_delay else None,
            "p90_delay_min": round(p90_delay, 1) if p90_delay else None,
            "sla10": sla_share(10),
            "sla30": sla_share(30),
            "sla60": sla_share(60),
            "tel_share": tel_share,
            "radio_share": radio_share,
            "quality_fields": filled_counts,
            "n_perfect": n_perfect,
            "pct_perfect": _pct(n_perfect, N),
            "date_range": {"start": date_min, "end": date_max},
        }
        _cache_set(event, year, "kpis", kpis_data)
        _progress()

        # =====================================================================
        # 2b) Quality (detailed)
        # =====================================================================
        quality_data = {
            "total": N,
            "n_perfect": n_perfect,
            "pct_perfect": _pct(n_perfect, N),
            "fields": {},
            "by_operator": [],
            "by_day": [],
            "worst_fields_by_operator": [],
        }
        # Per-field fill rate
        field_labels = {
            "sous_classification": "Sous-classification",
            "appelant": "Appelant",
            "carroye": "Carroye",
            "services_contactes_raw": "Service contacte",
        }
        for f in quality_fields:
            if f in df.columns:
                filled = int(df[f].notna().sum())
                quality_data["fields"][field_labels.get(f, f)] = {
                    "filled": filled,
                    "missing": N - filled,
                    "pct": _pct(filled, N),
                }

        # Quality per operator (top 20 creators)
        op_top_names = df["operator_create"].value_counts().head(20).index.tolist()
        op_quality = []
        for op in op_top_names:
            op_df = df[df["operator_create"] == op]
            op_n = len(op_df)
            if op_n == 0:
                continue
            op_perfect = int(op_df[quality_fields].notna().all(axis=1).sum()) if all(f in op_df.columns for f in quality_fields) else 0
            field_pcts = {}
            for f in quality_fields:
                if f in op_df.columns:
                    field_pcts[field_labels.get(f, f)] = _pct(int(op_df[f].notna().sum()), op_n)
            op_quality.append({
                "operator": op,
                "total": op_n,
                "perfect": op_perfect,
                "pct_perfect": _pct(op_perfect, op_n),
                "fields": field_pcts,
            })
        quality_data["by_operator"] = sorted(op_quality, key=lambda x: x["pct_perfect"])

        # Quality progression by day
        if not dft.empty:
            day_quality = []
            for day, group in dft.groupby("date"):
                day_n = len(group)
                day_ids = group.index
                day_df = df.loc[day_ids]
                day_perfect = int(day_df[quality_fields].notna().all(axis=1).sum()) if all(f in day_df.columns for f in quality_fields) else 0
                day_quality.append({
                    "date": str(day),
                    "total": day_n,
                    "pct_perfect": _pct(day_perfect, day_n),
                })
            quality_data["by_day"] = day_quality

        _cache_set(event, year, "quality", quality_data)
        _progress()

        # =====================================================================
        # 3) Temporal
        # =====================================================================
        hourly = []
        heatmap_data = {"days": [], "hours": list(range(24)), "values": []}
        backlog = []

        if not dft.empty:
            by_hour = dft.groupby(["date", "hour"]).size().reset_index(name="count")
            by_hour["dt"] = pd.to_datetime(by_hour["date"]) + pd.to_timedelta(by_hour["hour"], unit="h")
            hourly = [{"dt": str(r["dt"]), "count": int(r["count"])} for _, r in by_hour.iterrows()]

            # Heatmap day x hour
            hm = dft.groupby(["dow_name", "hour"]).size().reset_index(name="n")
            days_order = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
            hm["dow_name"] = hm["dow_name"].str.lower()
            hm["dow_name"] = pd.Categorical(hm["dow_name"], categories=days_order, ordered=True)
            pivot = hm.pivot(index="dow_name", columns="hour", values="n").fillna(0)
            heatmap_data = {
                "days": pivot.index.tolist(),
                "hours": list(range(24)),
                "values": pivot.values.tolist(),
            }

            # Backlog
            events_open = dft[["ts"]].copy()
            events_open["delta"] = 1
            events_open = events_open.rename(columns={"ts": "t"})
            events_close = df[pd.notna(df["close_ts"])][["close_ts"]].copy()
            events_close["delta"] = -1
            events_close = events_close.rename(columns={"close_ts": "t"})
            flow = pd.concat([events_open[["t", "delta"]], events_close[["t", "delta"]]], ignore_index=True)
            flow = flow.sort_values("t")
            flow["backlog"] = flow["delta"].cumsum()
            # Downsample to max 500 points
            step = max(1, len(flow) // 500)
            flow_sampled = flow.iloc[::step]
            backlog = [{"t": str(r["t"]), "backlog": int(r["backlog"])} for _, r in flow_sampled.iterrows()]

            # Peak detection (z-score > 2)
            hour_counts = by_hour["count"]
            mean_c = hour_counts.mean()
            std_c = hour_counts.std()
            peaks = []
            if std_c > 0:
                for _, r in by_hour.iterrows():
                    z = (r["count"] - mean_c) / std_c
                    if z > 2:
                        peaks.append({"dt": str(r["dt"]), "count": int(r["count"]), "zscore": round(z, 2)})

        temporal_data = {"hourly": hourly, "heatmap": heatmap_data, "backlog": backlog, "peaks": peaks if not dft.empty else []}
        _cache_set(event, year, "temporal", temporal_data)
        _progress()

        # =====================================================================
        # 4) Geographic
        # =====================================================================
        # Areas
        area_counts = df["area_desc"].value_counts().head(15)
        areas = [{"area_desc": str(k), "n": int(v)} for k, v in area_counts.items()]

        # Carroyes with GPS
        car_series = df["carroye"].dropna()
        car_counts = car_series.value_counts()
        grid_docs = list(_col_grid_ref.find({}, {"_id": 0, "grid_ref": 1, "latitude": 1, "longitude": 1}))
        grid_df = pd.DataFrame(grid_docs) if grid_docs else pd.DataFrame(columns=["grid_ref", "latitude", "longitude"])
        if not grid_df.empty:
            grid_df["grid_ref"] = grid_df["grid_ref"].astype(str).str.upper().str.replace(" ", "", regex=False)

        car_points = []
        if not car_counts.empty and not grid_df.empty:
            car_df = car_counts.reset_index()
            car_df.columns = ["carroye", "n"]
            # Descriptions
            desc_df = (
                df.loc[pd.notna(df["carroye"]) & pd.notna(df["text"]), ["carroye", "text"]]
                .groupby("carroye")["text"].apply(list).reset_index(name="descs")
            )
            desc_df["descs"] = desc_df["descs"].apply(lambda L: [str(t)[:240] for t in L[:10]])
            car_map = car_df.merge(grid_df, left_on="carroye", right_on="grid_ref", how="left")
            car_map = car_map.merge(desc_df, on="carroye", how="left")
            car_map = car_map.dropna(subset=["latitude", "longitude"])
            car_points = [{
                "ref": str(r["carroye"]),
                "n": int(r["n"]),
                "lat": float(r["latitude"]),
                "lon": float(r["longitude"]),
                "descs": r["descs"] if isinstance(r["descs"], list) else [],
            } for _, r in car_map.iterrows()]

        # GPS points (real coordinates from gps field)
        gps_df = df.dropna(subset=["lat", "lon"])[["_id", "lat", "lon", "text", "category", "severity", "ts", "sous_classification"]].copy()
        gps_points = [{
            "id": str(r["_id"]),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "text": str(r["text"])[:200] if r["text"] else "",
            "category": str(r["category"]) if r["category"] else "",
            "severity": int(r["severity"]) if pd.notna(r["severity"]) else 0,
            "ts": str(r["ts"]) if pd.notna(r["ts"]) else "",
            "sous_class": str(r["sous_classification"]) if r["sous_classification"] else "",
        } for _, r in gps_df.iterrows()]

        # Hotspots (risk score)
        hotspots = []
        df_hot = pd.DataFrame({"carroye": df["carroye"], "delay_min": df["delay_min"]})
        df_hot = df_hot.dropna(subset=["carroye"])
        if not df_hot.empty:
            def _norm_col(s):
                s = s.astype(float)
                if s.max() == s.min():
                    return pd.Series([0.0] * len(s), index=s.index)
                return (s - s.min()) / (s.max() - s.min())
            agg_hot = df_hot.groupby("carroye").agg(
                volume=("carroye", "count"),
                p90_delay=("delay_min", lambda s: float(np.nanpercentile(s.dropna(), 90)) if s.dropna().any() else 0),
            ).reset_index()
            if len(agg_hot) > 1:
                agg_hot["score"] = round(100 * (_norm_col(agg_hot["volume"]) + _norm_col(agg_hot["p90_delay"].fillna(0))) / 2.0, 1)
            else:
                agg_hot["score"] = 50.0
            top_hot = agg_hot.sort_values("score", ascending=False).head(20)
            # Merge with coords
            if not grid_df.empty:
                top_hot = top_hot.merge(grid_df, left_on="carroye", right_on="grid_ref", how="left")
                top_hot = top_hot.dropna(subset=["latitude", "longitude"])
                hotspots = [{
                    "ref": str(r["carroye"]), "score": float(r["score"]),
                    "volume": int(r["volume"]), "p90": round(float(r["p90_delay"]), 1),
                    "lat": float(r["latitude"]), "lon": float(r["longitude"]),
                } for _, r in top_hot.iterrows()]

        geo_data = {
            "areas": areas,
            "car_points": car_points,
            "gps_points": gps_points,
            "hotspots": hotspots,
            "gps_count": len(gps_points),
        }
        _cache_set(event, year, "geographic", geo_data)
        _progress()

        # =====================================================================
        # 5) Performance
        # =====================================================================
        delay_hist = []
        if N_closed:
            delays = df["delay_min"].dropna()
            bins = [0, 10, 30, 60, 120, 240, 480, float("inf")]
            labels = ["0-10", "10-30", "30-60", "1-2h", "2-4h", "4-8h", "8h+"]
            cuts = pd.cut(delays, bins=bins, labels=labels, right=True)
            delay_hist = [{"range": str(k), "count": int(v)} for k, v in cuts.value_counts().sort_index().items()]

        perf_data = {
            "fcr": {"fcr": fcr_count, "non_fcr": max(0, N - fcr_count)},
            "sla": {"sla10": sla_share(10), "sla30": sla_share(30), "sla60": sla_share(60)},
            "delay_distribution": delay_hist,
            "median_delay": round(median_delay, 1) if median_delay else None,
            "p90_delay": round(p90_delay, 1) if p90_delay else None,
        }
        _cache_set(event, year, "performance", perf_data)
        _progress()

        # =====================================================================
        # 6) Categories
        # =====================================================================
        source_series = df["source"].dropna().astype(str)
        pco_sources = source_series[source_series.str.startswith("PCO")]
        source_top = pco_sources.value_counts().head(15)
        sources = [{"label": str(k), "n": int(v)} for k, v in source_top.items()]

        sc_series = df["sous_classification"].dropna().astype(str)
        sc_series = sc_series[sc_series.str.strip() != ""]
        sc_series = sc_series[sc_series != "Inconnu"]
        sc_counts = sc_series.value_counts().sort_values(ascending=True)
        sous_class = [{"label": str(k), "n": int(v)} for k, v in sc_counts.items()]

        # Channels
        channels = {}
        channels["Telephone"] = int(df["flag_tel"].sum())
        channels["Radio"] = int(df["flag_radio"].sum())
        channels["Autre"] = N - channels["Telephone"] - channels["Radio"]

        cat_data = {"sources": sources, "sous_classifications": sous_class, "channels": channels}
        _cache_set(event, year, "categories", cat_data)
        _progress()

        # =====================================================================
        # 7) Operators
        # =====================================================================
        op_create = df["operator_create"].value_counts().head(20)
        op_create_list = [{"label": str(k), "n": int(v)} for k, v in op_create.items()]

        # Delays by operator (top 15 creators)
        top_ops = op_create.head(15).index.tolist()
        op_delays = []
        for op in top_ops:
            subset = df[df["operator_create"] == op]["delay_min"].dropna()
            if len(subset):
                op_delays.append({
                    "label": op,
                    "median": round(float(subset.median()), 1),
                    "p90": round(float(np.percentile(subset, 90)), 1),
                    "n": int(len(subset)),
                })

        ops_data = {"creators": op_create_list, "delays_by_op": op_delays}
        _cache_set(event, year, "operators", ops_data)
        _progress()

        # =====================================================================
        # 8) Services
        # =====================================================================
        svc_exploded = df[["services_list", "delay_min"]].copy()
        svc_exploded["services_list_filled"] = svc_exploded["services_list"].apply(lambda L: L if L else ["Inconnu"])
        svc_df = svc_exploded.explode("services_list_filled").rename(columns={"services_list_filled": "service"})
        svc_df["service"] = svc_df["service"].apply(clean_service_label)
        svc_known = svc_df[svc_df["service"] != "Inconnu"]

        svc_split = svc_known["service"].value_counts().head(15)
        svc_split_list = [{"label": str(k), "n": int(v)} for k, v in svc_split.items()]

        svc_closed = svc_df[pd.notna(svc_df["delay_min"])].copy()
        svc_p90 = []
        if not svc_closed.empty:
            svc_agg = svc_closed.groupby("service")["delay_min"].agg(
                n="count", mediane="median", moyenne="mean",
                p90=lambda s: float(np.percentile(s.dropna(), 90)) if s.dropna().any() else 0,
            ).reset_index().sort_values("p90", ascending=True)
            svc_p90 = [{
                "label": str(r["service"]), "n": int(r["n"]),
                "median": round(float(r["mediane"]), 1),
                "mean": round(float(r["moyenne"]), 1),
                "p90": round(float(r["p90"]), 1),
            } for _, r in svc_agg.iterrows()]

        svc_data = {"split": svc_split_list, "p90": svc_p90}
        _cache_set(event, year, "services", svc_data)
        _progress()

        # =====================================================================
        # 9) Intervenants
        # =====================================================================
        intervs = []
        for d in docs:
            cc = d.get("content_category") or {}
            xml_s = (d.get("xml_struct") or {})
            for i in range(1, 6):
                val = cc.get(f"intervenant{i}") or xml_s.get(f"intervenant{i}")
                if val and str(val).strip():
                    intervs.append({"fiche_id": d.get("_id"), "niveau": f"Niveau {i}", "intervenant": str(val).strip()})
        df_interv = pd.DataFrame(intervs) if intervs else pd.DataFrame(columns=["fiche_id", "niveau", "intervenant"])

        interv_top = []
        interv_levels = []
        avg_per_fiche = 0
        if not df_interv.empty:
            avg_per_fiche = round(df_interv.groupby("fiche_id").size().mean(), 2)
            top_g = df_interv["intervenant"].value_counts().head(20).reset_index()
            top_g.columns = ["intervenant", "n"]
            interv_top = [{"label": str(r["intervenant"]), "n": int(r["n"])} for _, r in top_g.iterrows()]
            # By level
            repart = df_interv.groupby(["intervenant", "niveau"]).size().reset_index(name="n")
            repart_top = repart[repart["intervenant"].isin(top_g["intervenant"])]
            for _, r in repart_top.iterrows():
                interv_levels.append({"intervenant": str(r["intervenant"]), "niveau": str(r["niveau"]), "n": int(r["n"])})

        interv_data = {"top": interv_top, "levels": interv_levels, "avg_per_fiche": avg_per_fiche}
        _cache_set(event, year, "intervenants", interv_data)
        _progress()

        # =====================================================================
        # 10) Text / Wordcloud
        # =====================================================================
        op_name_tokens = set()
        for coln in ["operator_create", "operator_close"]:
            if coln in df.columns:
                for name in df[coln].dropna().unique():
                    op_name_tokens.update(_tokenize_basic(str(name)))

        text_sources = []
        for col in ["text_full", "text", "comment_text"]:
            if col in df.columns:
                text_sources += [_clean_text_blob(t) for t in df[col].dropna().tolist()]

        raw_tokens = _tokenize_basic(" ".join(text_sources))
        tokens = [t for t in raw_tokens if t not in STOPWORDS_FR and t not in op_name_tokens]

        wordcloud_words = []
        if tokens:
            freq = pd.Series(tokens).value_counts().head(120)
            wordcloud_words = [{"t": str(w), "n": int(c)} for w, c in freq.items()]

        # Treemap: sous-classifications
        treemap = []
        if not sc_counts.empty:
            for k, v in sc_counts.items():
                treemap.append({"label": str(k), "n": int(v)})

        text_data = {"wordcloud": wordcloud_words, "treemap": treemap}
        _cache_set(event, year, "text", text_data)
        _progress()

        # =====================================================================
        # 11) Appelants
        # =====================================================================
        # Build reverse alias map
        alias_map = {}
        for canonical, variants in APPELANT_ALIASES.items():
            for v in variants:
                alias_map[v.upper().strip()] = canonical
            alias_map[canonical.upper().strip()] = canonical

        appelant_series = df["appelant"].dropna().astype(str).str.upper().str.strip()
        appelant_norm = appelant_series.map(lambda x: alias_map.get(x, x))
        app_counts = appelant_norm.value_counts().head(20)
        appelants = [{"label": str(k), "n": int(v)} for k, v in app_counts.items()]

        _cache_set(event, year, "appelants", {"top": appelants})
        _progress()

        # =====================================================================
        # 12) Meteo cross
        # =====================================================================
        meteo_cross = {"days": [], "pearson_temp": None, "pearson_rain": None}
        try:
            if not dft.empty:
                dates = sorted(dft["date"].unique())
                inc_by_day = dft.groupby("date").size().to_dict()
                meteo_days = []
                for d in dates:
                    date_str = str(d)
                    # Date in donnees_meteo can be ISODate, string "YYYY-MM-DD" or "YYYY-MM-DDT00:00:00.000Z"
                    meteo_doc = _db["donnees_meteo"].find_one({"Date": datetime(d.year, d.month, d.day)})
                    if not meteo_doc:
                        meteo_doc = _db["donnees_meteo"].find_one({"Date": date_str})
                    if not meteo_doc:
                        meteo_doc = _db["donnees_meteo"].find_one({"Date": date_str + "T00:00:00.000Z"})
                    tmax = tmin = rain = None
                    if meteo_doc:
                        # Use get with sentinel to handle 0 values correctly (0 is valid, not missing)
                        _miss = object()
                        # Keys in MongoDB have accents: Température, Précipitations
                        tmax = meteo_doc.get("Temp\u00e9rature max (\u00b0C)", _miss)
                        if tmax is _miss:
                            tmax = meteo_doc.get("Temperature max (\u00b0C)", _miss)
                        if tmax is _miss:
                            tmax = meteo_doc.get("Temperature max (C)")
                        tmin = meteo_doc.get("Temp\u00e9rature min (\u00b0C)", _miss)
                        if tmin is _miss:
                            tmin = meteo_doc.get("Temperature min (\u00b0C)", _miss)
                        if tmin is _miss:
                            tmin = meteo_doc.get("Temperature min (C)")
                        rain = meteo_doc.get("Pr\u00e9cipitations (mm)", _miss)
                        if rain is _miss:
                            rain = meteo_doc.get("Precipitations (mm)")
                    def _safe_float(v):
                        if v is None:
                            return None
                        try:
                            f = float(v)
                            if np.isnan(f) or np.isinf(f):
                                return None
                            return round(f, 2)
                        except (ValueError, TypeError):
                            return None
                    meteo_days.append({
                        "date": date_str,
                        "incidents": int(inc_by_day.get(d, 0)),
                        "tmax": _safe_float(tmax),
                        "tmin": _safe_float(tmin),
                        "rain": _safe_float(rain),
                    })
                meteo_cross["days"] = meteo_days
                # Pearson
                inc_vals = [m["incidents"] for m in meteo_days if m["tmax"] is not None]
                temp_vals = [m["tmax"] for m in meteo_days if m["tmax"] is not None]
                rain_vals = [m["rain"] for m in meteo_days if m["rain"] is not None]
                inc_rain = [m["incidents"] for m in meteo_days if m["rain"] is not None]
                if len(inc_vals) >= 3 and len(temp_vals) >= 3:
                    r = float(np.corrcoef(inc_vals[:len(temp_vals)], temp_vals)[0, 1])
                    if not np.isnan(r):
                        meteo_cross["pearson_temp"] = round(r, 3)
                if len(inc_rain) >= 3 and len(rain_vals) >= 3:
                    r = float(np.corrcoef(inc_rain[:len(rain_vals)], rain_vals)[0, 1])
                    if not np.isnan(r):
                        meteo_cross["pearson_rain"] = round(r, 3)
        except Exception as e:
            logger.warning(f"Meteo cross failed: {e}")
        _cache_set(event, year, "meteo-cross", meteo_cross)
        _progress()

        # =====================================================================
        # 13) Affluence cross
        # =====================================================================
        affluence_cross = {"hourly": []}
        try:
            evt_doc = _db["evenement"].find_one({"nom": event})
            if evt_doc and evt_doc.get("skidata"):
                skidata_id = evt_doc["skidata"]
                # year can be int or str in data_access, try both
                access_docs = list(_db["data_access"].find(
                    {"counter_id": skidata_id, "year": {"$in": [year, str(year), int(year) if isinstance(year, str) and year.isdigit() else year]}},
                    sort=[("timestamp", 1)],
                ).limit(2000))
                if not access_docs:
                    # Fallback: search by requested_event (no spaces, no "H ")
                    slug = event.replace(" ", "").upper()
                    access_docs = list(_db["data_access"].find(
                        {"requested_event": slug, "year": {"$in": [year, str(year), int(year) if isinstance(year, str) and year.isdigit() else year]}},
                        sort=[("timestamp", 1)],
                    ).limit(2000))
                if access_docs:
                    for adoc in access_docs:
                        ts_a = adoc.get("timestamp")
                        if ts_a:
                            current_val = adoc.get("current", 0)
                            try:
                                current_val = int(current_val)
                            except (ValueError, TypeError):
                                current_val = 0
                            affluence_cross["hourly"].append({
                                "dt": str(ts_a),
                                "presents": current_val,
                            })
        except Exception as e:
            logger.warning(f"Affluence cross failed: {e}")
        _cache_set(event, year, "affluence-cross", affluence_cross)
        _progress()

        # =====================================================================
        # 14) Waze cross
        # =====================================================================
        waze_cross = {"alerts": []}
        try:
            if not dft.empty:
                date_min = dft["ts"].min()
                date_max = dft["ts"].max()
                waze_alerts = list(_db["waze_feed_events"].find({
                    "observed_at": {"$gte": date_min, "$lte": date_max},
                    "entity_type": "alert",
                }).limit(500))
                for wa in waze_alerts:
                    geo = wa.get("geometry") or {}
                    coords = geo.get("coordinates", [])
                    if len(coords) >= 2:
                        waze_cross["alerts"].append({
                            "lat": float(coords[1]),
                            "lon": float(coords[0]),
                            "type": (wa.get("kind") or {}).get("type", ""),
                            "subtype": (wa.get("kind") or {}).get("subtype", ""),
                            "dt": str(wa.get("observed_at", "")),
                            "street": (wa.get("road") or {}).get("street", ""),
                        })
        except Exception as e:
            logger.warning(f"Waze cross failed: {e}")
        _cache_set(event, year, "waze-cross", waze_cross)
        _progress()

        # =====================================================================
        # 15) Convergence (data for client-side jDBSCAN)
        # =====================================================================
        convergence_data = {"gps_incidents": []}
        gps_for_cluster = df.dropna(subset=["lat", "lon"])[["_id", "lat", "lon", "ts", "category", "severity", "sous_classification", "area_desc"]].copy()
        if not gps_for_cluster.empty:
            convergence_data["gps_incidents"] = [{
                "id": str(r["_id"]),
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "ts": str(r["ts"]) if pd.notna(r["ts"]) else "",
                "category": str(r["category"]) if r["category"] else "",
                "severity": int(r["severity"]) if pd.notna(r["severity"]) else 0,
                "sous_class": str(r["sous_classification"]) if r["sous_classification"] else "",
                "area": str(r["area_desc"]) if r["area_desc"] else "",
            } for _, r in gps_for_cluster.iterrows()]
        # Also add carrroye-based points for events without GPS
        if len(convergence_data["gps_incidents"]) < 50 and car_points:
            for cp in car_points:
                convergence_data["gps_incidents"].append({
                    "id": f"car_{cp['ref']}",
                    "lat": cp["lat"],
                    "lon": cp["lon"],
                    "ts": "",
                    "category": "",
                    "severity": 0,
                    "sous_class": "",
                    "area": cp["ref"],
                    "is_carroye": True,
                    "n": cp["n"],
                })
        _cache_set(event, year, "convergence", convergence_data)
        _progress()

        # =====================================================================
        # 16) Comparative
        # =====================================================================
        comparative = {"years": [], "kpis_by_year": {}}
        try:
            other_year = year - 1 if isinstance(year, int) else None
            if other_year:
                other_count = _col_pcorg.count_documents({"event": event, "year": other_year})
                if other_count > 0:
                    other_docs = list(_col_pcorg.find({"event": event, "year": other_year}))
                    odf = pd.DataFrame([_flatten_doc(d) for d in other_docs])
                    odf["delay_min"] = pd.to_numeric(odf["delay_min"], errors="coerce")
                    on_closed = int(pd.notna(odf["delay_min"]).sum())
                    comparative["years"] = [other_year, year]
                    comparative["kpis_by_year"][str(other_year)] = {
                        "total": len(odf),
                        "median_delay": round(float(np.nanmedian(odf["delay_min"])), 1) if on_closed else None,
                        "sla30": _pct(int((odf["delay_min"].dropna() <= 30).sum()), on_closed) if on_closed else 0,
                    }
                    comparative["kpis_by_year"][str(year)] = {
                        "total": N,
                        "median_delay": round(median_delay, 1) if median_delay else None,
                        "sla30": sla_share(30),
                    }
                    # Category evolution
                    cat_curr = df["source"].value_counts().head(10).to_dict()
                    cat_prev = odf["source"].value_counts().head(10).to_dict()
                    all_cats = sorted(set(list(cat_curr.keys()) + list(cat_prev.keys())))[:15]
                    comparative["category_evolution"] = {
                        c: {str(other_year): int(cat_prev.get(c, 0)), str(year): int(cat_curr.get(c, 0))}
                        for c in all_cats
                    }
        except Exception as e:
            logger.warning(f"Comparative failed: {e}")
        _cache_set(event, year, "comparative", comparative)
        _progress()

        # =====================================================================
        # 17) Escalation (Sankey flows)
        # =====================================================================
        escalation = {"flows": [], "levels_count": {}}
        if not df_interv.empty:
            # Count fiches with each level
            for i in range(1, 6):
                lvl_name = f"Niveau {i}"
                escalation["levels_count"][lvl_name] = int((df_interv["niveau"] == lvl_name).sum())
            # Build sankey flows: category -> sous_class -> service
            cat_to_sous = df.dropna(subset=["source", "sous_classification"]).groupby(["source", "sous_classification"]).size().reset_index(name="n")
            for _, r in cat_to_sous.head(30).iterrows():
                escalation["flows"].append({"from": str(r["source"]), "to": str(r["sous_classification"]), "flow": int(r["n"])})
            sous_to_svc = df.dropna(subset=["sous_classification"]).copy()
            sous_to_svc["svc"] = sous_to_svc["services_list"].apply(lambda L: L[0] if L else None)
            sous_to_svc = sous_to_svc.dropna(subset=["svc"])
            svc_flows = sous_to_svc.groupby(["sous_classification", "svc"]).size().reset_index(name="n")
            for _, r in svc_flows.head(30).iterrows():
                escalation["flows"].append({"from": str(r["sous_classification"]), "to": str(r["svc"]), "flow": int(r["n"])})

        _cache_set(event, year, "escalation", escalation)
        _progress()

        # =====================================================================
        # 18) Zones vulnerability
        # =====================================================================
        zones_vuln = {"zones": []}
        area_df = df.dropna(subset=["area_desc"]).copy()
        if not area_df.empty:
            agg = area_df.groupby("area_desc").agg(
                volume=("area_desc", "count"),
                sev_mean=("severity", lambda s: float(s.dropna().mean()) if s.dropna().any() else 0),
                p90_delay=("delay_min", lambda s: float(np.nanpercentile(s.dropna(), 90)) if s.dropna().any() else 0),
            ).reset_index()
            if len(agg) > 1:
                agg["score"] = round(100 * (
                    _norm_col(agg["volume"]) * 0.4 +
                    _norm_col(agg["sev_mean"].fillna(0)) * 0.3 +
                    _norm_col(agg["p90_delay"].fillna(0)) * 0.3
                ), 1)
            else:
                agg["score"] = 50.0
            top_zones = agg.sort_values("score", ascending=False).head(15)
            zones_vuln["zones"] = [{
                "zone": str(r["area_desc"]),
                "score": float(r["score"]),
                "volume": int(r["volume"]),
                "sev_mean": round(float(r["sev_mean"]), 2),
                "p90_delay": round(float(r["p90_delay"]), 1),
            } for _, r in top_zones.iterrows()]

        _cache_set(event, year, "zones-vulnerability", zones_vuln)
        _progress()

        # =====================================================================
        # 19) Effectifs cross
        # =====================================================================
        effectifs_cross = {"zones": [], "alerts": [], "available": False}
        try:
            event_slug = event.lower().replace(" ", "").replace("24h", "24h")
            # Try different slug patterns
            slug_map = {
                "24H AUTOS": "24hautos", "24H MOTOS": "24hmotos", "24H CAMIONS": "24hcamions",
                "GPF": "gpf", "GP EXPLORER": "gpexplorer", "LE MANS CLASSIC": "lemansclassic",
                "SUPERBIKE": "superbike", "CONGRES SDIS": "congressdis",
            }
            slug = slug_map.get(event, event.lower().replace(" ", ""))
            cal_name = f"calendrier_{year}_{slug}"
            cal_col = _db[cal_name]
            cal_count = cal_col.count_documents({})
            if cal_count > 0:
                effectifs_cross["available"] = True
                cal_docs = list(cal_col.find({}))
                # Aggregate by zone and half-hour slot
                zone_slots = {}
                for cdoc in cal_docs:
                    zone = cdoc.get("zone") or "Inconnu"
                    stype = cdoc.get("accueil_surete", "S")
                    for dp in (cdoc.get("donnees_presences") or []):
                        date = dp.get("date", "")
                        for ph in (dp.get("plages_horaires") or []):
                            nb = ph.get("nombre_personnes", 0)
                            if nb > 0:
                                key = (zone, date, ph.get("heure_debut", ""))
                                if key not in zone_slots:
                                    zone_slots[key] = {"agents_secu": 0, "agents_accueil": 0}
                                if stype == "S":
                                    zone_slots[key]["agents_secu"] += nb
                                else:
                                    zone_slots[key]["agents_accueil"] += nb

                # Cross with incidents
                zone_mapping = {}
                for z in set(k[0] for k in zone_slots):
                    zu = z.upper()
                    zone_mapping[zu] = zu

                zone_results = []
                for (zone, date, heure), agents in zone_slots.items():
                    total_agents = agents["agents_secu"] + agents["agents_accueil"]
                    if total_agents > 0:
                        zone_results.append({
                            "zone": zone, "date": date, "heure": heure,
                            "agents_secu": agents["agents_secu"],
                            "agents_accueil": agents["agents_accueil"],
                            "total_agents": total_agents,
                        })
                effectifs_cross["zones"] = zone_results[:500]
        except Exception as e:
            logger.warning(f"Effectifs cross failed: {e}")
        _cache_set(event, year, "effectifs-cross", effectifs_cross)
        _progress()

        # =====================================================================
        # ANPR cross (conditional)
        # =====================================================================
        anpr_cross = {"available": False, "matches": []}
        try:
            plates_in_pcorg = set()
            for pl_list in df["extracted_plates"].dropna():
                if isinstance(pl_list, list):
                    plates_in_pcorg.update(pl_list)
            if plates_in_pcorg and _db["hik_anpr"].count_documents({}) > 0:
                anpr_cross["available"] = True
                matched = list(_db["hik_anpr"].find(
                    {"license_plate": {"$in": list(plates_in_pcorg)}},
                    {"license_plate": 1, "event_dt": 1, "camera_path": 1, "_id": 0},
                ).limit(200))
                anpr_cross["matches"] = [{
                    "plate": m.get("license_plate", ""),
                    "dt": str(m.get("event_dt", "")),
                    "camera": m.get("camera_path", ""),
                } for m in matched]
        except Exception as e:
            logger.warning(f"ANPR cross failed: {e}")
        _cache_set(event, year, "anpr-cross", anpr_cross)
        _progress()

        # =====================================================================
        # 20) Network graph (operator-zone-service relationships)
        # =====================================================================
        network = {"nodes": [], "links": []}
        try:
            node_map = {}
            link_map = {}

            def _add_node(name, ntype):
                if name and name != "Inconnu" and name not in node_map:
                    node_map[name] = {"id": name, "type": ntype, "weight": 0}

            def _add_link(src, tgt, w=1):
                if src and tgt and src != "Inconnu" and tgt != "Inconnu" and src != tgt:
                    key = (src, tgt) if src < tgt else (tgt, src)
                    link_map[key] = link_map.get(key, 0) + w

            # Top operators (creators)
            top_op_names = df["operator_create"].value_counts().head(12).index.tolist()
            for op in top_op_names:
                _add_node(op, "operator")

            # Top zones
            top_zone_names = df["area_desc"].value_counts().head(12).index.tolist()
            for z in top_zone_names:
                _add_node(z.split("/")[-1].strip() if "/" in z else z, "zone")

            # Top services
            top_svc_names = svc_known["service"].value_counts().head(10).index.tolist() if not svc_known.empty else []
            for s in top_svc_names:
                _add_node(s, "service")

            # Build links: operator -> zone
            for _, row in df.iterrows():
                op = row.get("operator_create")
                zone = row.get("area_desc")
                if op in top_op_names and zone in top_zone_names:
                    zone_short = zone.split("/")[-1].strip() if "/" in zone else zone
                    _add_link(op, zone_short)
                    if op in node_map:
                        node_map[op]["weight"] += 1

            # Build links: zone -> service
            for _, row in df.iterrows():
                zone = row.get("area_desc")
                svcs = row.get("services_list") or []
                if zone in top_zone_names and svcs:
                    zone_short = zone.split("/")[-1].strip() if "/" in zone else zone
                    for svc in svcs:
                        svc = clean_service_label(svc)
                        if svc in top_svc_names:
                            _add_link(zone_short, svc)

            network["nodes"] = list(node_map.values())
            network["links"] = [{"source": k[0], "target": k[1], "value": v}
                                for k, v in sorted(link_map.items(), key=lambda x: -x[1])[:80]]
        except Exception as e:
            logger.warning(f"Network graph failed: {e}")
        _cache_set(event, year, "network", network)

        _compute_state = {"status": "done", "progress": 100, "event": event, "year": year, "error": None}

    except Exception as e:
        logger.exception(f"Compute failed for {event}/{year}")
        _compute_state = {"status": "error", "progress": 0, "event": event, "year": year, "error": str(e)}


def _norm_col(s):
    s = s.astype(float)
    if s.max() == s.min():
        return pd.Series([0.0] * len(s), index=s.index)
    return (s - s.min()) / (s.max() - s.min())
