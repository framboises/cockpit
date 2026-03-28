# traffic.py — collecte trafic avec fallback MongoDB -> Waze API
from flask import Blueprint, jsonify, request
from datetime import datetime, timezone
from pymongo import MongoClient
from functools import wraps
import logging
import os
import re
import threading
import time
import requests

traffic_bp = Blueprint('traffic', __name__)

# Map route -> block_id pour la protection par bloc
_ROUTE_BLOCK_MAP = {
    'get_trafic_data': 'widget-traffic',
    'get_all_routes': 'widget-traffic',
    'alerts': 'widget-traffic',
    'get_trafic_data_parking_structured': 'widget-parkings',
}

@traffic_bp.before_request
def _check_block_permission():
    """Verifie l'auth et les permissions de bloc pour toutes les routes traffic."""
    from app import role_required, get_user_allowed_blocks
    # L'auth est geree par role_required applique via before_app_request ou directement
    # On fait un import tardif pour eviter le circulaire
    import jwt as pyjwt
    from app import JWT_SECRET, JWT_ALGORITHM, CODING, ROLE_HIERARCHY, ROLE_ORDER, APP_KEY, SUPER_ADMIN_ROLE, BASE_URL

    if CODING:
        from app import ROLE_HIERARCHY, ROLE_ORDER
        sim_role = request.args.get("as", "admin")
        if sim_role not in ROLE_HIERARCHY:
            sim_role = "admin"
        sim_level = ROLE_HIERARCHY[sim_role]
        sim_roles = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= sim_level]
        request.user_payload = {
            "apps": ["looker", "shiftsolver", "tagger"],
            "roles_by_app": {"cockpit": sim_role},
            "global_roles": [],
            "roles": sim_roles,
            "app_role": sim_role,
            "is_super_admin": False,
            "firstname": "Bruce",
            "lastname": "WAYNE",
            "email": "bruce@wayneenterprise.com",
        }
    else:
        from flask import redirect
        token = request.cookies.get("access_token")
        if not token:
            return jsonify({"error": "Authentification requise"}), 401
        try:
            payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
            return jsonify({"error": "Token invalide ou expire"}), 401

        global_roles = payload.get("global_roles", []) or []
        is_super_admin = SUPER_ADMIN_ROLE in global_roles
        roles_by_app = payload.get("roles_by_app", {}) or {}
        if not isinstance(roles_by_app, dict):
            roles_by_app = {}
        app_role = roles_by_app.get(APP_KEY)
        if not is_super_admin and not app_role:
            return jsonify({"error": "Acces non autorise"}), 403
        effective_role = "admin" if is_super_admin else app_role
        if effective_role in ROLE_HIERARCHY:
            payload["roles"] = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= ROLE_HIERARCHY[effective_role]]
        else:
            payload["roles"] = []
        payload["app_role"] = effective_role
        payload["is_super_admin"] = is_super_admin
        request.user_payload = payload

    # Verifier la permission de bloc
    block_id = _ROUTE_BLOCK_MAP.get(request.endpoint)
    if block_id:
        allowed = get_user_allowed_blocks(request.user_payload)
        if allowed is not None and block_id not in allowed:
            return jsonify({"error": "Acces non autorise a ce widget"}), 403

# --- Logging (actif en dev/coding uniquement) ---
_TITAN_ENV = os.getenv("TITAN_ENV", "dev")
_CODING = os.getenv("CODING", "").lower() == "true"
_DEBUG_LOG = _TITAN_ENV == "dev" or _CODING
logger = logging.getLogger("traffic")

def _log_source(endpoint, status):
    if not _DEBUG_LOG:
        return
    labels = {
        "HIT": "\033[36m[CACHE]\033[0m",
        "MONGO": "\033[33m[MONGODB]\033[0m",
        "MISS": "\033[32m[WAZE API]\033[0m",
        "STALE": "\033[31m[STALE CACHE]\033[0m",
        "BYPASS": "\033[90m[BYPASS]\033[0m",
    }
    label = labels.get(status, status)
    print(f"  \033[1m[Traffic]\033[0m {endpoint} -> {label}")

# --- Configuration ---
WAZE_CACHE_TTL_SECONDS = int(os.getenv("WAZE_CACHE_TTL_SECONDS", "60"))
WAZE_TIMEOUT_SECONDS   = int(os.getenv("WAZE_TIMEOUT_SECONDS", "10"))
MONGO_MAX_AGE_SECONDS  = int(os.getenv("MONGO_MAX_AGE_SECONDS", "300"))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")

# --- MongoDB connexion (lazy) ---
_mongo_client = None
_mongo_db = None

def _get_mongo_db():
    global _mongo_client, _mongo_db
    if _mongo_db is None:
        _mongo_client = MongoClient(MONGO_URI)
        _mongo_db = _mongo_client["titan"]
    return _mongo_db

# --- In-memory cache (tier 1) ---
_WAZE_CACHE = {
    "trafic": {"data": None, "ts": 0.0},
    "alerts": {"data": None, "ts": 0.0},
}
_WAZE_CACHE_LOCK = threading.Lock()

def _cache_get(key):
    if WAZE_CACHE_TTL_SECONDS <= 0:
        return None, "BYPASS"
    now = time.time()
    with _WAZE_CACHE_LOCK:
        entry = _WAZE_CACHE.get(key)
        if entry and entry["data"] is not None and (now - entry["ts"]) < WAZE_CACHE_TTL_SECONDS:
            return entry["data"], "HIT"
    return None, "MISS"

def _cache_set(key, data):
    if WAZE_CACHE_TTL_SECONDS <= 0:
        return
    with _WAZE_CACHE_LOCK:
        _WAZE_CACHE[key] = {"data": data, "ts": time.time()}

def _cache_get_stale(key):
    with _WAZE_CACHE_LOCK:
        entry = _WAZE_CACHE.get(key)
        if entry and entry["data"] is not None:
            return entry["data"]
    return None

# --- MongoDB read (tier 2) ---
def _utc_age_seconds(fetched_at):
    """Calcule l'age en secondes, que fetched_at soit aware ou naive (UTC)."""
    now_utc = datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        # PyMongo retourne souvent des naive datetimes, toujours en UTC
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return (now_utc - fetched_at).total_seconds()

def _mongo_get(collection_name):
    """Read data from MongoDB if it's less than MONGO_MAX_AGE_SECONDS old."""
    try:
        db = _get_mongo_db()
        doc = db[collection_name].find_one({"_id": "latest"})
        if doc and "data" in doc and "fetched_at" in doc:
            age = _utc_age_seconds(doc["fetched_at"])
            if _DEBUG_LOG:
                print(f"  \033[90m[Traffic] MongoDB {collection_name}: age={age:.0f}s (max={MONGO_MAX_AGE_SECONDS}s)\033[0m")
            if age < MONGO_MAX_AGE_SECONDS:
                return doc["data"]
    except Exception as e:
        if _DEBUG_LOG:
            print(f"  \033[31m[Traffic] MongoDB {collection_name} read error: {e}\033[0m")
    return None

def _jsonify_with_cache(payload, status):
    response = jsonify(payload)
    response.headers["X-Waze-Cache"] = status
    return response

# --- Traffic payload: cache -> mongo -> waze api ---
def _get_waze_trafic_payload():
    url = 'https://www.waze.com/row-partnerhub-api/feeds-tvt/?id=1709107524427'

    # Tier 1: in-memory cache
    cached, cache_status = _cache_get("trafic")
    if cached is not None:
        _log_source("/trafic/data", cache_status)
        return cached, cache_status

    # Tier 2: MongoDB (from external collector)
    mongo_data = _mongo_get("waze_trafic")
    if mongo_data is not None:
        _cache_set("trafic", mongo_data)
        _log_source("/trafic/data", "MONGO")
        return mongo_data, "MONGO"

    # Tier 3: direct Waze API
    try:
        response = requests.get(url, timeout=WAZE_TIMEOUT_SECONDS)
        response.raise_for_status()
        trafic_data = response.json()

        if not isinstance(trafic_data, dict) or "routes" not in trafic_data:
            raise ValueError("Format inattendu, cle 'routes' manquante")

        _cache_set("trafic", trafic_data)
        _log_source("/trafic/data", "MISS")
        return trafic_data, "MISS"
    except (requests.exceptions.RequestException, ValueError) as e:
        stale = _cache_get_stale("trafic")
        if stale is not None:
            _log_source("/trafic/data", "STALE")
            return stale, "STALE"
        raise e

# --- Alerts payload: cache -> mongo -> waze api ---
def _get_waze_alerts_payload():
    url = "https://www.waze.com/row-partnerhub-api/partners/19308574489/waze-feeds/fa96cebf-1625-4b4f-91a0-a5af6db60e49?format=1"

    # Tier 1: in-memory cache
    cached, cache_status = _cache_get("alerts")
    if cached is not None:
        _log_source("/alerts", cache_status)
        return cached, cache_status

    # Tier 2: MongoDB (from external collector)
    mongo_data = _mongo_get("waze_alerts")
    if mongo_data is not None:
        _cache_set("alerts", mongo_data)
        _log_source("/alerts", "MONGO")
        return mongo_data, "MONGO"

    # Tier 3: direct Waze API
    try:
        response = requests.get(url, timeout=WAZE_TIMEOUT_SECONDS)
        response.raise_for_status()
        if not response.text.strip():
            raise ValueError("Reponse vide de l'API Waze")
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError as e:
            raise ValueError("Donnees JSON invalides") from e

        alerts = data.get('alerts', [])
        _cache_set("alerts", alerts)
        _log_source("/alerts", "MISS")
        return alerts, "MISS"
    except (requests.exceptions.RequestException, ValueError) as e:
        stale = _cache_get_stale("alerts")
        if stale is not None:
            _log_source("/alerts", "STALE")
            return stale, "STALE"
        raise e

@traffic_bp.route('/trafic/data')
def get_trafic_data():
    try:
        trafic_data, cache_status = _get_waze_trafic_payload()

        # Filtrage des routes dont le champ "name" commence par "#"
        routes = trafic_data.get("routes", [])
        if not isinstance(routes, list):
            routes = []
        filtered_routes = [
            route for route in routes
            if isinstance(route, dict) and not route.get("name", "").startswith("#")
        ]
        payload = dict(trafic_data)
        payload["routes"] = filtered_routes

        return _jsonify_with_cache(payload, cache_status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Balises acceptees : ##, #I#, #O#, #P#, #I1#, #O2#, #P1#, ...
TAG_RE = re.compile(r'^\s*(#([IOP])(\d+)?#|##)\s*(.+?)\s*$')
SECURITY_RE = re.compile(r'^\s*\*\*\s*(.+?)\s*$')

def parse_route_name(name: str):
    """
    Retourne (direction, terrain, tag, variant).
    Le variant (chiffre du tag) est utilise pour distinguer les itineraires P.
    Pour I/O, les variants sont des troncons du meme itineraire (on les fusionne).
    Exemples:
      "## Ouest"      -> (None, "Ouest", "neutral", None)
      "#I# Ouest"     -> ("in",  "Ouest", "I", None)
      "#I2# Ouest"    -> ("in",  "Ouest", "I", None)    # troncon, meme terrain
      "#O1# Panorama" -> ("out", "Panorama", "O", None) # troncon, meme terrain
      "#P# A11"       -> (None,  "A11", "P", None)
      "#P1# A28"      -> (None,  "A28", "P", "1")       # itineraire distinct
      "#P2# A28"      -> (None,  "A28", "P", "2")       # itineraire distinct
      "** SDIS -> X"  -> (None,  "SDIS -> X", "security", None)
      "Route libre"   -> (None,  "Route libre", "free", None)
    """
    m = TAG_RE.match(name or "")
    if m:
        io = m.group(2)          # 'I', 'O', 'P' ou None
        num = m.group(3)         # chiffre optionnel
        terrain = m.group(4).strip()
        if io == 'I':
            return "in", terrain, "I", None
        if io == 'O':
            return "out", terrain, "O", None
        if io == 'P':
            return None, terrain, "P", num  # num distingue les itineraires P
        return None, terrain, "neutral", None  # cas "##"

    ms = SECURITY_RE.match(name or "")
    if ms:
        return None, ms.group(1).strip(), "security", None

    return None, (name or "").strip(), "free", None

def classify_congestion(current_time, historic_time):
    # (ta logique d’origine)
    if not historic_time or historic_time <= 0:
        t = current_time or 0
        if t < 15:   return ("normal",    1)
        if t < 30:   return ("chargé",    2)
        if t < 60:   return ("saturé",    3)
        if t >= 60:  return ("bouchon",   4)
        return ("normal", 1)

    ratio = (current_time or 0) / float(historic_time)
    if ratio < 0.9:    return ("plus fluide", 0)
    if ratio < 1.2:    return ("normal",      1)
    if ratio < 1.6:    return ("chargé",      2)
    if ratio < 2.5:    return ("saturé",      3)
    return ("bouchon", 4)

@traffic_bp.route('/trafic/waiting_data_structured')
def get_trafic_data_parking_structured():
    try:
        trafic_data, cache_status = _get_waze_trafic_payload()

        # Agrégateur: clé = (terrain, direction)
        agg = {}

        for route in trafic_data["routes"]:
            if not isinstance(route, dict):
                continue

            raw_name = route.get("name", "")
            # On ne garde que ##, #I#, #O#, #I1#, #O2#, etc.
            if not (raw_name.startswith("##") or raw_name.startswith("#I") or raw_name.startswith("#O")):
                continue

            direction, terrain, _tag, _variant = parse_route_name(raw_name)
            cur  = int(route.get("time", 0) or 0)
            hist = int(route.get("historicTime", 0) or 0)

            key = (terrain, direction)
            if key not in agg:
                agg[key] = {
                    "terrain": terrain,
                    "direction": direction,    # "in" | "out" | None
                    "sumCurrent": 0,
                    "sumHistoric": 0,
                    "routesCount": 0,
                }
            agg[key]["sumCurrent"]  += max(0, cur)
            agg[key]["sumHistoric"] += max(0, hist)
            agg[key]["routesCount"] += 1

        terrains = []
        for (_terrain, _direction), rec in agg.items():
            sum_cur  = rec["sumCurrent"]
            sum_hist = rec["sumHistoric"]

            # Ratio/delta sur les SOMMES
            ratio_val = (sum_cur / sum_hist) if sum_hist > 0 else None
            ratio_round = round(ratio_val, 2) if ratio_val is not None else None
            delta_s   = max(0, sum_cur - sum_hist) if sum_hist > 0 else None
            delta_pct = round((ratio_val - 1) * 100) if ratio_val is not None else None

            status, severity = classify_congestion(sum_cur, sum_hist)

            terrains.append({
                "terrain": rec["terrain"],
                "direction": rec["direction"],
                "currentTime": sum_cur,
                "historicTime": sum_hist,
                "ratio": ratio_round,        # ex: 1.27
                "deltaSeconds": delta_s,     # ≥ 0 si hist > 0, sinon None
                "deltaPercent": delta_pct,   # ex: 27
                "status": status,
                "severity": severity,
                "routesCount": rec["routesCount"],
            })

        # Tri par ratio décroissant (None en fin)
        terrains.sort(key=lambda t: (-1 if t["ratio"] is None else t["ratio"]), reverse=True)

        return _jsonify_with_cache({
            "terrains": terrains,
            "updateTime": trafic_data.get("updateTime")
        }, cache_status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@traffic_bp.route('/trafic/all_routes')
def get_all_routes():
    """Toutes les routes individuelles, sans fusion."""
    try:
        trafic_data, cache_status = _get_waze_trafic_payload()
        routes = trafic_data.get("routes", [])
        result = []

        for route in routes:
            if not isinstance(route, dict):
                continue
            raw_name = route.get("name", "")
            direction, terrain, tag, _variant = parse_route_name(raw_name)
            cur = int(route.get("time", 0) or 0)
            hist = int(route.get("historicTime", 0) or 0)
            line = route.get("line", [])

            ratio_val = (cur / hist) if hist > 0 else None
            status, severity = classify_congestion(cur, hist)
            category = "pkg_aa" if tag in ("I", "O", "neutral", "P") else tag

            result.append({
                "terrain": terrain,
                "rawName": raw_name,
                "direction": direction,
                "tag": tag,
                "category": category,
                "currentTime": cur,
                "historicTime": hist,
                "ratio": round(ratio_val, 2) if ratio_val else None,
                "deltaSeconds": max(0, cur - hist) if hist > 0 else None,
                "status": status,
                "severity": severity,
                "line": line,
            })

        result.sort(key=lambda t: (-1 if t["ratio"] is None else t["ratio"]), reverse=True)

        return _jsonify_with_cache({
            "routes": result,
            "updateTime": trafic_data.get("updateTime")
        }, cache_status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@traffic_bp.route('/alerts')
def alerts():
    try:
        alerts_payload, cache_status = _get_waze_alerts_payload()
        return _jsonify_with_cache(alerts_payload, cache_status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
