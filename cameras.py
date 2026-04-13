# cameras.py -- Blueprint Camera HIK (Administration cameras de surveillance)
import os
import re
import io
import time
import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, render_template, Response, send_file
from pymongo import MongoClient
from bson.objectid import ObjectId

cameras_bp = Blueprint("cameras", __name__)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth helper (import from app at request time to avoid circular import)
# ---------------------------------------------------------------------------
def _check_admin():
    from app import (CODING, JWT_SECRET, JWT_ALGORITHM,
                     ROLE_HIERARCHY, ROLE_ORDER, APP_KEY)
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
    effective_role = "admin" if max_level >= ROLE_HIERARCHY.get("admin", 3) else roles[0] if roles else "user"
    payload["roles"] = [r for r in ROLE_ORDER if ROLE_HIERARCHY.get(r, 0) <= ROLE_HIERARCHY.get(effective_role, 0)]
    payload["app_role"] = effective_role
    request.user_payload = payload
    return None


@cameras_bp.before_request
def _before():
    err = _check_admin()
    if err:
        return err


# ---------------------------------------------------------------------------
# MongoDB (lazy init)
# ---------------------------------------------------------------------------
_db = None
_col_cameras = None

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads", "camera_snapshots")


def _ensure_db():
    global _db, _col_cameras
    if _db is not None:
        return
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    client = MongoClient(uri)
    dev_mode = os.getenv("TITAN_ENV", "dev") != "prod"
    _db = client["titan_dev" if dev_mode else "titan"]
    _col_cameras = _db["cockpit_cameras"]

    # Index
    _col_cameras.create_index([("ip", 1), ("port", 1)], unique=True)
    _col_cameras.create_index("enabled")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
VALID_BRANDS = {"hikvision", "dahua", "bosch", "hanwha", "axis", "uniview"}
VALID_PROTOCOLS = {"http", "https"}
IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

ALLOWED_ACTIONS = {
    "wiper", "light_on", "light_off", "autofocus", "reboot",
    "goto_home", "set_home", "lens_init",
    "start_patrol", "stop_patrol",
    "daynight",
}


def _pub(doc):
    """Convert MongoDB document to JSON-serializable dict."""
    if not doc:
        return None
    d = dict(doc)
    for k, v in d.items():
        if isinstance(v, ObjectId):
            d[k] = str(v)
        elif isinstance(v, list):
            d[k] = [str(x) if isinstance(x, ObjectId) else x for x in v]
        elif hasattr(v, "isoformat"):
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            d[k] = v.isoformat()
    return d


def _pub_safe(doc):
    """Serialize document, stripping password."""
    d = _pub(doc)
    if d:
        d.pop("password", None)
    return d


def _make_cam(doc):
    """Instantiate a HikCamera from a MongoDB document."""
    from hik.hik_control import HikCamera
    return HikCamera(
        name=doc["name"],
        ip=doc["ip"],
        port=doc.get("port", 80),
        user=doc.get("user", "admin"),
        password=doc.get("password", ""),
        channel=doc.get("channel", 1),
        protocol=doc.get("protocol", "http"),
        brand=doc.get("brand", "hikvision"),
    )


def _get_cam_doc(cam_id):
    """Fetch camera document by id, return (doc, error_response)."""
    _ensure_db()
    try:
        oid = ObjectId(cam_id)
    except Exception:
        return None, (jsonify({"error": "Invalid id"}), 400)
    doc = _col_cameras.find_one({"_id": oid})
    if not doc:
        return None, (jsonify({"error": "Camera not found"}), 404)
    return doc, None


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------
@cameras_bp.route("/cameras")
def cameras_page():
    payload = getattr(request, "user_payload", {})
    return render_template(
        "cameras.html",
        user_roles=payload.get("roles", []),
        user_firstname=payload.get("firstname", ""),
        user_lastname=payload.get("lastname", ""),
        user_email=payload.get("email", ""),
    )


# ---------------------------------------------------------------------------
# CRUD API
# ---------------------------------------------------------------------------
@cameras_bp.route("/api/cameras", methods=["GET"])
def list_cameras():
    _ensure_db()
    docs = list(_col_cameras.find().sort([("name", 1)]))
    return jsonify([_pub_safe(d) for d in docs])


@cameras_bp.route("/api/cameras", methods=["POST"])
def create_camera():
    _ensure_db()
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    ip = (data.get("ip") or "").strip()
    if not name or not ip:
        return jsonify({"error": "name et ip sont requis"}), 400
    if not IP_RE.match(ip):
        return jsonify({"error": "Adresse IP invalide"}), 400
    port = int(data.get("port", 80))
    if port < 1 or port > 65535:
        return jsonify({"error": "Port invalide (1-65535)"}), 400
    protocol = data.get("protocol", "http")
    if protocol not in VALID_PROTOCOLS:
        protocol = "http"
    brand = data.get("brand", "hikvision")
    if brand not in VALID_BRANDS:
        brand = "hikvision"

    tags_raw = data.get("tags", [])
    if isinstance(tags_raw, str):
        tags_raw = [t.strip() for t in tags_raw.split(",") if t.strip()]

    now = datetime.now(timezone.utc)
    doc = {
        "name": name,
        "ip": ip,
        "port": port,
        "user": (data.get("user") or "admin").strip(),
        "password": data.get("password", ""),
        "channel": int(data.get("channel", 1)),
        "protocol": protocol,
        "brand": brand,
        "location": (data.get("location") or "").strip(),
        "tags": tags_raw,
        "enabled": data.get("enabled", True),
        "createdAt": now,
        "updatedAt": now,
    }
    try:
        result = _col_cameras.insert_one(doc)
        doc["_id"] = result.inserted_id
    except Exception as e:
        if "duplicate key" in str(e).lower():
            return jsonify({"error": "Une camera avec cette IP et ce port existe deja"}), 409
        return jsonify({"error": str(e)}), 500
    return jsonify(_pub_safe(doc)), 201


@cameras_bp.route("/api/cameras/<cam_id>", methods=["PUT"])
def update_camera(cam_id):
    _ensure_db()
    try:
        oid = ObjectId(cam_id)
    except Exception:
        return jsonify({"error": "Invalid id"}), 400
    data = request.get_json(force=True) or {}
    patch = {}
    if "name" in data:
        patch["name"] = (data["name"] or "").strip()
    if "ip" in data:
        ip = (data["ip"] or "").strip()
        if ip and not IP_RE.match(ip):
            return jsonify({"error": "Adresse IP invalide"}), 400
        patch["ip"] = ip
    if "port" in data:
        port = int(data["port"])
        if port < 1 or port > 65535:
            return jsonify({"error": "Port invalide"}), 400
        patch["port"] = port
    if "user" in data:
        patch["user"] = (data["user"] or "admin").strip()
    if "password" in data and data["password"]:
        patch["password"] = data["password"]
    if "channel" in data:
        patch["channel"] = int(data["channel"])
    if "protocol" in data:
        patch["protocol"] = data["protocol"] if data["protocol"] in VALID_PROTOCOLS else "http"
    if "brand" in data:
        patch["brand"] = data["brand"] if data["brand"] in VALID_BRANDS else "hikvision"
    if "location" in data:
        patch["location"] = (data["location"] or "").strip()
    if "tags" in data:
        tags = data["tags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        patch["tags"] = tags
    if "enabled" in data:
        patch["enabled"] = bool(data["enabled"])

    patch["updatedAt"] = datetime.now(timezone.utc)
    try:
        res = _col_cameras.find_one_and_update(
            {"_id": oid}, {"$set": patch}, return_document=True
        )
    except Exception as e:
        if "duplicate key" in str(e).lower():
            return jsonify({"error": "Une camera avec cette IP et ce port existe deja"}), 409
        return jsonify({"error": str(e)}), 500
    if not res:
        return jsonify({"error": "Camera not found"}), 404
    return jsonify(_pub_safe(res))


@cameras_bp.route("/api/cameras/<cam_id>", methods=["DELETE"])
def delete_camera(cam_id):
    _ensure_db()
    try:
        oid = ObjectId(cam_id)
    except Exception:
        return jsonify({"error": "Invalid id"}), 400
    result = _col_cameras.delete_one({"_id": oid})
    return jsonify({"ok": result.deleted_count > 0})


# ---------------------------------------------------------------------------
# Camera operations
# ---------------------------------------------------------------------------
@cameras_bp.route("/api/cameras/<cam_id>/status", methods=["GET"])
def camera_status(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    cam = _make_cam(doc)
    try:
        info = cam.get_device_info()
        ptz = cam.get_ptz_status()
        online = "_error" not in info
    except Exception as e:
        info = {"_error": str(e)}
        ptz = None
        online = False
    return jsonify({"device_info": info, "ptz": ptz, "online": online})


@cameras_bp.route("/api/cameras/<cam_id>/capture", methods=["POST"])
def capture_snapshot(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    cam = _make_cam(doc)
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        save_path = os.path.join(UPLOAD_DIR, f"{cam_id}_{ts}.jpg")
        cam.capture_image(save_path)
        with open(save_path, "rb") as f:
            img_bytes = f.read()
        return Response(img_bytes, mimetype="image/jpeg")
    except Exception as e:
        logger.exception("Capture failed for camera %s", cam_id)
        return jsonify({"error": str(e)}), 500


@cameras_bp.route("/api/cameras/<cam_id>/presets", methods=["GET"])
def list_presets(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    cam = _make_cam(doc)
    try:
        presets = cam.list_presets()
    except Exception:
        presets = []
    return jsonify(presets)


@cameras_bp.route("/api/cameras/<cam_id>/preset", methods=["POST"])
def goto_preset(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    data = request.get_json(force=True) or {}
    preset_id = data.get("preset_id")
    if preset_id is None:
        return jsonify({"error": "preset_id requis"}), 400
    cam = _make_cam(doc)
    try:
        result = cam.goto_preset(int(preset_id))
        return jsonify({"ok": bool(result)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@cameras_bp.route("/api/cameras/<cam_id>/ptz", methods=["POST"])
def ptz_control(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    data = request.get_json(force=True) or {}
    action = data.get("action", "stop")
    cam = _make_cam(doc)
    try:
        if action == "move":
            pan = int(data.get("pan", 0))
            tilt = int(data.get("tilt", 0))
            zoom = int(data.get("zoom", 0))
            cam.move(pan, tilt, zoom)
        elif action == "goto":
            azimuth = int(data.get("azimuth", 0))
            elevation = int(data.get("elevation", 0))
            zoom = int(data.get("zoom", 10))
            cam.goto_position(azimuth, elevation, zoom)
        else:
            cam.stop_move()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@cameras_bp.route("/api/cameras/<cam_id>/action", methods=["POST"])
def camera_action(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    data = request.get_json(force=True) or {}
    action = data.get("action", "")
    params = data.get("params", {})

    if action not in ALLOWED_ACTIONS:
        return jsonify({"error": f"Action inconnue: {action}"}), 400

    if action == "reboot" and not data.get("confirm"):
        return jsonify({"error": "Confirmation requise pour reboot"}), 400

    cam = _make_cam(doc)
    try:
        if action == "wiper":
            result = cam.wiper()
        elif action == "light_on":
            result = cam.light_on()
        elif action == "light_off":
            result = cam.light_off()
        elif action == "autofocus":
            result = cam.autofocus()
        elif action == "lens_init":
            result = cam.lens_init()
        elif action == "reboot":
            result = cam.reboot()
        elif action == "goto_home":
            result = cam.goto_home()
        elif action == "set_home":
            result = cam.set_home_position()
        elif action == "daynight":
            mode = params.get("mode", "auto")
            if mode not in ("day", "night", "auto"):
                return jsonify({"error": "Mode invalide (day/night/auto)"}), 400
            result = cam.set_daynight_mode(mode)
        elif action == "start_patrol":
            patrol_id = params.get("id", 1)
            result = cam.start_patrol(int(patrol_id))
        elif action == "stop_patrol":
            patrol_id = params.get("id", 1)
            result = cam.stop_patrol(int(patrol_id))
        else:
            result = False
        return jsonify({"ok": bool(result)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@cameras_bp.route("/api/cameras/<cam_id>/test", methods=["POST"])
def test_connection(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    cam = _make_cam(doc)
    try:
        info = cam.get_device_info()
        if "_error" in info:
            return jsonify({"ok": False, "error": info["_error"]})
        return jsonify({"ok": True, "info": info})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@cameras_bp.route("/api/cameras/test-connection", methods=["POST"])
def test_new_connection():
    """Test connection for a camera not yet saved (from the add form)."""
    data = request.get_json(force=True) or {}
    ip = (data.get("ip") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "IP requise"}), 400
    from hik.hik_control import HikCamera
    cam = HikCamera(
        name="test",
        ip=ip,
        port=int(data.get("port", 80)),
        user=(data.get("user") or "admin").strip(),
        password=data.get("password", ""),
        channel=int(data.get("channel", 1)),
        protocol=data.get("protocol", "http"),
        brand=data.get("brand", "hikvision"),
    )
    try:
        info = cam.get_device_info()
        if "_error" in info:
            return jsonify({"ok": False, "error": info["_error"]})
        return jsonify({"ok": True, "info": info})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
