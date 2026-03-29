# anoloc.py - Blueprint Flask pour l'integration Anoloc GPS
# Lecture MongoDB uniquement (le collecteur anoloc_collector.py alimente les collections)
# Seules exceptions: test-login et anoloc-devices appellent l'API Anoloc directement

from flask import Blueprint, jsonify, request
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    TZ_LOCAL = ZoneInfo("Europe/Paris")
except ImportError:
    import dateutil.tz
    TZ_LOCAL = dateutil.tz.gettz("Europe/Paris")


def _now():
    return datetime.now(TZ_LOCAL)
from pymongo import MongoClient
from bson.objectid import ObjectId
import logging
import os
import requests

anoloc_bp = Blueprint("anoloc", __name__)

ANOLOC_API_BASE_DEFAULT = "https://app.lemans.anoloc.io/api/v3"
USER_AGENT = "COCKPIT-TITAN/1.0"

# Map route -> block_id pour la protection par bloc
_ROUTE_BLOCK_MAP = {
    "anoloc_live": "widget-right-4",
    "anoloc_status": "widget-right-4",
}

_TITAN_ENV = os.getenv("TITAN_ENV", "dev")
_CODING = os.getenv("CODING", "").lower() == "true"
_DEBUG_LOG = _TITAN_ENV == "dev" or _CODING
logger = logging.getLogger("anoloc")

# --- MongoDB connexion (lazy) ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_mongo_client = None
_mongo_db = None


def _get_mongo_db():
    global _mongo_client, _mongo_db
    if _mongo_db is None:
        _mongo_client = MongoClient(MONGO_URI)
        _mongo_db = _mongo_client["titan"]
    return _mongo_db


# ---------------------------------------------------------------------------
# Auth & permissions (before_request) - meme pattern que traffic.py
# ---------------------------------------------------------------------------

@anoloc_bp.before_request
def _check_block_permission():
    """Verifie l'auth et les permissions de bloc pour toutes les routes anoloc."""
    from app import get_user_allowed_blocks
    import jwt as pyjwt
    from app import (
        JWT_SECRET, JWT_ALGORITHM, CODING, ROLE_HIERARCHY,
        ROLE_ORDER, APP_KEY, SUPER_ADMIN_ROLE,
    )

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
    else:
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
            payload["roles"] = [
                r for r in ROLE_ORDER
                if ROLE_HIERARCHY[r] <= ROLE_HIERARCHY[effective_role]
            ]
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


def _get_api_base():
    """Retourne l'URL de base de l'API Anoloc depuis la config, ou le defaut."""
    db = _get_mongo_db()
    config = db["anoloc_config"].find_one({"_id": "global"}) or {}
    base = (config.get("api_base") or "").rstrip("/")
    return base or ANOLOC_API_BASE_DEFAULT


def _require_admin():
    """Retourne une erreur 403 si l'utilisateur n'est pas admin."""
    payload = getattr(request, "user_payload", {})
    if payload.get("app_role") != "admin" and not payload.get("is_super_admin"):
        return jsonify({"error": "Acces reserve aux administrateurs"}), 403
    return None


# ---------------------------------------------------------------------------
# Helper: visibilite des beacon_groups pour l'utilisateur courant
# ---------------------------------------------------------------------------

def _get_user_visible_groups(config):
    """Retourne la liste des beacon_group IDs visibles pour l'user courant, ou None (tous)."""
    payload = getattr(request, "user_payload", {})
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return None  # admin voit tout

    visibility = config.get("group_visibility") or {}
    if not visibility:
        return None  # pas de restrictions configurees

    # Trouver les groupes cockpit de l'utilisateur
    from app import COL_USER_GROUPS, COL_GROUPS
    email = payload.get("email", "")
    db = _get_mongo_db()
    user_doc = db["users"].find_one({"email": email}, {"_id": 1})
    if not user_doc:
        return None

    ug = COL_USER_GROUPS.find_one({"user_id": user_doc["_id"]})
    group_ids = (ug.get("groups") or []) if ug else []
    if not group_ids:
        return None

    # Union des beacon_groups visibles pour tous les groupes cockpit de l'user
    visible = set()
    for gid in group_ids:
        gid_str = str(gid)
        grp_vis = visibility.get(gid_str)
        if grp_vis is None:
            return None  # un groupe sans restriction = tout visible
        visible.update(grp_vis)
    return visible if visible else set()


# ---------------------------------------------------------------------------
# Routes: lecture MongoDB
# ---------------------------------------------------------------------------

@anoloc_bp.route("/anoloc/live")
def anoloc_live():
    """Positions live de tous les devices, filtrees par visibilite."""
    db = _get_mongo_db()
    config = db["anoloc_config"].find_one({"_id": "global"})
    if not config or not config.get("enabled"):
        return jsonify({"groups": {}, "enabled": False})

    visible_groups = _get_user_visible_groups(config)

    # Lire anoloc_latest (positions connues)
    latest_docs = {doc["_id"]: doc for doc in db["anoloc_latest"].find()}

    # Construire les groupes a partir de la config (pas seulement anoloc_latest)
    beacon_groups_map = {
        g["id"]: g for g in config.get("beacon_groups", []) if g.get("enabled")
    }

    groups = {}

    for grp_id, grp_cfg in beacon_groups_map.items():
        if visible_groups is not None and grp_id not in visible_groups:
            continue

        groups[grp_id] = {
            "label": grp_cfg.get("label", grp_id),
            "icon": grp_cfg.get("icon", "location_on"),
            "color": grp_cfg.get("color", "#6366f1"),
            "devices": [],
        }

        for dev_id in grp_cfg.get("anoloc_device_ids", []):
            doc = latest_docs.get(dev_id)
            if doc:
                # Device avec position connue
                collected_at = doc.get("collected_at")
                online = False
                # Online = status Anoloc != offline ET derniere vraie frame < 30 min
                status = doc.get("status", "offline")
                online = status != "offline"
                last_real = doc.get("last_real_at")
                if online and last_real:
                    if last_real.tzinfo is None:
                        last_real = last_real.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - last_real).total_seconds() > 1800:
                        online = False  # pas de vraie frame depuis 30 min

                groups[grp_id]["devices"].append({
                    "id": dev_id,
                    "label": doc.get("label", dev_id),
                    "lat": doc.get("lat"),
                    "lng": doc.get("lng"),
                    "speed": doc.get("speed", 0),
                    "heading": doc.get("heading", 0),
                    "status": doc.get("status", "offline"),
                    "battery_pct": doc.get("battery_pct"),
                    "gps_fix": doc.get("gps_fix", 0),
                    "sent_at": doc.get("sent_at", "").isoformat() if isinstance(doc.get("sent_at"), datetime) else str(doc.get("sent_at", "")),
                    "last_real_at": doc.get("last_real_at", "").isoformat() if isinstance(doc.get("last_real_at"), datetime) else "",
                    "collected_at": doc.get("collected_at", "").isoformat() if isinstance(doc.get("collected_at"), datetime) else "",
                    "online": online,
                })
            else:
                # Device configure mais jamais collecte — utiliser le label sauvegarde
                dev_labels = grp_cfg.get("device_labels") or {}
                groups[grp_id]["devices"].append({
                    "id": dev_id,
                    "label": dev_labels.get(dev_id, dev_id),
                    "lat": None,
                    "lng": None,
                    "speed": 0,
                    "heading": 0,
                    "status": "offline",
                    "battery_pct": None,
                    "sent_at": "",
                    "collected_at": "",
                    "online": False,
                })

    return jsonify({"groups": groups, "enabled": True})


@anoloc_bp.route("/anoloc/status")
def anoloc_status():
    """Resume: nombre de devices online/offline par groupe."""
    db = _get_mongo_db()
    config = db["anoloc_config"].find_one({"_id": "global"})
    if not config or not config.get("enabled"):
        return jsonify({"enabled": False, "groups": {}})

    visible_groups = _get_user_visible_groups(config)
    docs = list(db["anoloc_latest"].find())
    beacon_groups_map = {
        g["id"]: g for g in config.get("beacon_groups", []) if g.get("enabled")
    }

    groups = {}
    for doc in docs:
        grp_id = doc.get("beacon_group")
        if not grp_id or grp_id not in beacon_groups_map:
            continue
        if visible_groups is not None and grp_id not in visible_groups:
            continue

        if grp_id not in groups:
            grp_cfg = beacon_groups_map[grp_id]
            groups[grp_id] = {
                "label": grp_cfg.get("label", grp_id),
                "icon": grp_cfg.get("icon", "location_on"),
                "color": grp_cfg.get("color", "#6366f1"),
                "online": 0,
                "offline": 0,
                "total": 0,
            }

        status = doc.get("status", "offline")
        is_online = status != "offline"
        last_real = doc.get("last_real_at")
        if is_online and last_real:
            if last_real.tzinfo is None:
                last_real = last_real.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - last_real).total_seconds() > 1800:
                is_online = False
        if is_online:
            groups[grp_id]["online"] += 1
        else:
            groups[grp_id]["offline"] += 1
        groups[grp_id]["total"] += 1

    return jsonify({"enabled": True, "groups": groups})


# ---------------------------------------------------------------------------
# Routes: live-control (activation collecte)
# ---------------------------------------------------------------------------

LIVE_CONTROL_ID = "live-control"

@anoloc_bp.route("/anoloc/live-control", methods=["GET"])
def anoloc_live_control_get():
    """Retourne l'etat du live-control."""
    err = _require_admin()
    if err:
        return err
    db = _get_mongo_db()
    doc = db["anoloc_config"].find_one({"_id": LIVE_CONTROL_ID}) or {}
    doc.pop("_id", None)
    # Convertir les dates en string
    for k in ("last_run",):
        if isinstance(doc.get(k), datetime):
            doc[k] = doc[k].isoformat()
    return jsonify(doc)


@anoloc_bp.route("/anoloc/live-control", methods=["POST"])
def anoloc_live_control_set():
    """Active ou desactive la collecte et/ou le logging."""
    err = _require_admin()
    if err:
        return err
    db = _get_mongo_db()
    data = request.get_json(force=True)
    update = {"updatedAt": _now()}
    if "collecting" in data:
        update["collecting"] = bool(data["collecting"])
    if "logging" in data:
        update["logging"] = bool(data["logging"])
    db["anoloc_config"].update_one(
        {"_id": LIVE_CONTROL_ID},
        {"$set": update},
        upsert=True,
    )
    return jsonify({"ok": True})


@anoloc_bp.route("/anoloc/logs", methods=["GET"])
def anoloc_logs_get():
    """Retourne les derniers logs du collecteur."""
    err = _require_admin()
    if err:
        return err
    db = _get_mongo_db()
    limit = min(int(request.args.get("limit", 50)), 200)
    docs = list(db["anoloc_logs"].find().sort("ts", -1).limit(limit))
    logs = []
    for doc in docs:
        doc.pop("_id", None)
        if isinstance(doc.get("ts"), datetime):
            doc["ts"] = doc["ts"].isoformat()
        logs.append(doc)
    return jsonify({"logs": logs})


@anoloc_bp.route("/anoloc/logs", methods=["DELETE"])
def anoloc_logs_clear():
    """Vide les logs."""
    err = _require_admin()
    if err:
        return err
    db = _get_mongo_db()
    db["anoloc_logs"].delete_many({})
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Routes: config admin
# ---------------------------------------------------------------------------

@anoloc_bp.route("/anoloc/config", methods=["GET"])
def anoloc_config_get():
    """Retourne la configuration Anoloc (password masque)."""
    err = _require_admin()
    if err:
        return err
    db = _get_mongo_db()
    config = db["anoloc_config"].find_one({"_id": "global"}) or {}
    config.pop("_id", None)
    # Masquer le password
    if config.get("password"):
        config["password"] = "********"
    return jsonify(config)


@anoloc_bp.route("/anoloc/config", methods=["POST"])
def anoloc_config_save():
    """Sauvegarder la configuration Anoloc."""
    err = _require_admin()
    if err:
        return err
    db = _get_mongo_db()
    data = request.get_json(force=True)

    existing = db["anoloc_config"].find_one({"_id": "global"}) or {}

    update = {
        "api_base": data.get("api_base", existing.get("api_base", "")),
        "login": data.get("login", existing.get("login", "")),
        "enabled": bool(data.get("enabled", False)),
        "beacon_groups": data.get("beacon_groups", existing.get("beacon_groups", [])),
        "group_visibility": data.get("group_visibility", existing.get("group_visibility", {})),
        "updatedAt": _now(),
    }

    # Ne pas ecraser le password si "********" ou absent
    pwd = data.get("password", "")
    if pwd and pwd != "********":
        update["password"] = pwd
    else:
        update["password"] = existing.get("password", "")

    db["anoloc_config"].replace_one(
        {"_id": "global"},
        {**update, "_id": "global"},
        upsert=True,
    )
    return jsonify({"ok": True})


@anoloc_bp.route("/anoloc/visibility", methods=["POST"])
def anoloc_visibility_save():
    """Sauvegarder la visibilite des beacon_groups par groupe cockpit."""
    err = _require_admin()
    if err:
        return err
    db = _get_mongo_db()
    data = request.get_json(force=True)
    # data = { "<cockpit_group_id>": ["grp-id1", "grp-id2"] | null, ... }
    db["anoloc_config"].update_one(
        {"_id": "global"},
        {"$set": {"group_visibility": data, "updatedAt": _now()}},
        upsert=True,
    )
    return jsonify({"ok": True})


@anoloc_bp.route("/anoloc/test-login", methods=["POST"])
def anoloc_test_login():
    """Tester les credentials Anoloc (appel direct API)."""
    err = _require_admin()
    if err:
        return err
    data = request.get_json(force=True)
    login = data.get("login", "")
    password = data.get("password", "")

    # Si password masque, relire depuis la config
    if password == "********":
        db = _get_mongo_db()
        config = db["anoloc_config"].find_one({"_id": "global"}) or {}
        password = config.get("password", "")

    if not login or not password:
        return jsonify({"ok": False, "error": "Login et password requis"}), 400

    try:
        resp = requests.post(
            f"{_get_api_base()}/login",
            json={"login": login, "password": password, "remember_me": True},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        return jsonify({"ok": True, "user": result.get("data", {}).get("user", {})})
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 0
        return jsonify({"ok": False, "error": f"HTTP {status}"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@anoloc_bp.route("/anoloc/anoloc-devices", methods=["GET"])
def anoloc_remote_devices():
    """Liste les devices depuis l'API Anoloc (appel direct, admin only)."""
    err = _require_admin()
    if err:
        return err
    db = _get_mongo_db()
    config = db["anoloc_config"].find_one({"_id": "global"}) or {}
    login = config.get("login", "")
    password = config.get("password", "")

    if not login or not password:
        return jsonify({"ok": False, "error": "Credentials Anoloc non configures"}), 400

    try:
        # Login
        api_base = _get_api_base()
        resp = requests.post(
            f"{api_base}/login",
            json={"login": login, "password": password, "remember_me": True},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        login_data = resp.json()
        token = (login_data.get("data") or {}).get("token") or login_data.get("token")
        if not token:
            return jsonify({"ok": False, "error": "Token non trouve dans la reponse login", "raw": str(login_data)[:500]}), 400

        # Get devices
        resp2 = requests.get(
            f"{api_base}/devices",
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            timeout=15,
        )
        resp2.raise_for_status()
        devices_data = resp2.json()
        devices = devices_data.get("data", [])
        if not isinstance(devices, list):
            devices = []
        return jsonify({"ok": True, "devices": devices})
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 0
        body = ""
        try:
            body = e.response.text[:500] if e.response is not None else ""
        except Exception:
            pass
        return jsonify({"ok": False, "error": f"HTTP {status}", "detail": body}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
