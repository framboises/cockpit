# anpr.py -- Blueprint LAPI / ANPR (Lecture Automatique de Plaques)
import os
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request, render_template, Response, send_file, abort
from pymongo import MongoClient, DESCENDING, ASCENDING
from bson.objectid import ObjectId
import gridfs

anpr_bp = Blueprint("anpr", __name__)
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


@anpr_bp.before_request
def _before():
    err = _check_admin()
    if err:
        return err

# ---------------------------------------------------------------------------
# Hikvision vehicle_logo -> marque
# ---------------------------------------------------------------------------
# Source: Hikvision ISAPI ANPR Main Vehicle Brand Reference
# https://tpp.hikvision.com/wiki/isapi/anpr/GUID-552878C8-F295-4F1C-87F0-5467E9C9160A.html
BRAND_MAP = {
    # --- Confirmed from Hikvision official docs ---
    1026: "Alfa Romeo",   1027: "Aston Martin", 1028: "Audi",
    1030: "Porsche",      1031: "Buick",        1036: "Mercedes",
    1037: "BMW",          1038: "Baojun",       1043: "Changan",
    1044: "Chevrolet",    1045: "Changfeng",    1048: "Dongfeng",
    1050: "Dongnan",      1051: "Dazhong",      1053: "Ford",
    1056: "GAC Trumpchi", 1060: "Honda",        1063: "Haima",
    1064: "Haval",        1067: "Huanghai",     1071: "Jianghuai (JAC)",
    1078: "Karry",        1081: "Lamborghini",  1083: "Leopaard",
    1084: "Lexus",        1085: "Lifan",        1088: "Maserati",
    1089: "Mazda",        1093: "MG",           1094: "MG",
    1096: "Mini",
    1100: "Lotus",        1101: "Land Rover",   1102: "Suzuki",
    1103: "Lufeng",       1104: "Luxgen",       1105: "Renault",
    1107: "Mini",         1108: "Maserati",     1112: "Mazda",
    1114: "Luxgen",       1116: "Opel",         1117: "Acura",
    1119: "Venucia",      1120: "Chery",        1121: "Kia",
    1123: "Nissan",       1125: "Roewe",        1127: "Smart",
    1128: "Mitsubishi",   1130: "ShuangHuan",   1131: "ShuangLong",
    1132: "SsangYong",    1133: "Subaru",       1134: "Skoda",
    1135: "Saab",         1139: "Tesla",        1141: "Denza",
    1144: "Volvo",        1149: "Hyundai",      1150: "Seat",
    1151: "Chevrolet",    1152: "Citroen",      1156: "Infiniti",
    1159: "Yujie",        1160: "Ferrari",      1161: "Fiat",
    1163: "Geely",        1169: "Peugeot",

    # --- Marques chinoises (codes > 1500) ---
    1552: "BYD",          1559: "Baic Senova",  1561: "Bestune",
    1566: "Borgward",     1571: "Changan",      1576: "Cowin",
    1579: "DS",           1581: "Foton",        1584: "GAC",
    1588: "Geely",        1599: "Great Wall",   1614: "Haval",
    1621: "JAC",          1629: "JMC",          1631: "Jetour",
    1633: "Jinbei",       1639: "Kaiyi",
    1691: "Lynk & Co",    1709: "NIO",          1715: "ORA",
    1737: "Qoros",        1745: "Roewe",        1747: "SAIC Maxus",
    1763: "SWM",
    1806: "Trumpchi",     1807: "VGV",          1808: "Voyah",
    1834: "Wuling",       1843: "XPeng",        1849: "Yudo",
    1855: "Zeekr",        1857: "Zhidou",       1869: "Zotye",
    1870: "Wey",          1877: "Dongfeng",
    1887: "Lancia",       1890: "Cupra",
    1938: "Dacia",        1944: "Toyota",       1948: "Volkswagen",
    1951: "Jeep",
}

VEHICLE_TYPE_LABELS = {
    "vehicle": "Voiture",
    "SUVMPV": "SUV/Monospace",
    "truck": "Camion",
    "bus": "Bus",
    "van": "Utilitaire",
    "pickupTruck": "Pick-up",
    "buggy": "Buggy",
}

COLOR_HEX = {
    "white": "#f0f0f0", "black": "#1a1a2e", "gray": "#6b7280",
    "blue": "#3b82f6", "red": "#ef4444", "green": "#22c55e",
    "yellow": "#eab308", "brown": "#92400e", "pink": "#ec4899",
    "cyan": "#06b6d4",
}

# ---------------------------------------------------------------------------
# MongoDB (lazy init)
# ---------------------------------------------------------------------------
_db = None
_col_anpr = None
_fs = None
_col_camera_config = None
_col_site_counter = None


def _ensure_db():
    global _db, _col_anpr, _fs, _col_camera_config, _col_site_counter
    if _db is not None:
        return
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    titan_env = os.getenv("TITAN_ENV", "dev").strip().lower()
    db_name = "titan" if titan_env in {"prod", "production"} else "titan_dev"
    client = MongoClient(uri)
    _db = client[db_name]
    _col_anpr = _db["hik_anpr"]
    _fs = gridfs.GridFS(_db, collection="hik_images")
    _col_camera_config = _db["anpr_camera_config"]
    _col_site_counter = _db["anpr_site_counter"]

    # Index
    _col_anpr.create_index([("event_dt", DESCENDING)])
    _col_anpr.create_index([("license_plate", 1)])
    _col_anpr.create_index([("camera_path", 1), ("event_dt", DESCENDING)])
    _col_camera_config.create_index("camera_path", unique=True)


def _brand(logo_id):
    return BRAND_MAP.get(logo_id, "Autre")


def _get_cam_configs():
    """Return {camera_path: config_doc} dict, cached per-request."""
    return {c["camera_path"]: c for c in _col_camera_config.find()}


def _resolve_direction(raw_direction, camera_path, cam_configs):
    """Resolve raw forward/reverse into entry/exit using camera config."""
    cfg = cam_configs.get(camera_path, {})
    fwd_role = cfg.get("forward_role", "entry")  # default: forward = entry
    if raw_direction == "forward":
        return "entry" if fwd_role == "entry" else "exit"
    elif raw_direction == "reverse":
        return "exit" if fwd_role == "entry" else "entry"
    return "unknown"


def _serialize(doc, cam_configs=None):
    """Serialize a single ANPR document for JSON."""
    if cam_configs is None:
        cam_configs = _get_cam_configs()
    raw_dir = doc.get("direction", "")
    camera = doc.get("camera_path", "")
    return {
        "id": str(doc["_id"]),
        "plate": doc.get("license_plate", ""),
        "original_plate": doc.get("original_plate", ""),
        "confidence": doc.get("confidence", 0),
        "color": doc.get("vehicle_color", ""),
        "color_hex": COLOR_HEX.get(doc.get("vehicle_color", ""), "#888"),
        "type": doc.get("vehicle_type", ""),
        "type_label": VEHICLE_TYPE_LABELS.get(doc.get("vehicle_type", ""), doc.get("vehicle_type", "")),
        "brand": _brand(doc.get("vehicle_logo", 0)),
        "brand_id": doc.get("vehicle_logo", 0),
        "camera": camera,
        "direction": raw_dir,
        "resolved_dir": _resolve_direction(raw_dir, camera, cam_configs),
        "event_dt": (doc["event_dt"].replace(tzinfo=timezone.utc).isoformat() if doc["event_dt"].tzinfo is None else doc["event_dt"].isoformat()) if isinstance(doc.get("event_dt"), datetime) else str(doc.get("event_dt", "")),
        "plate_image_id": str(doc["plate_image_id"]) if doc.get("plate_image_id") else None,
        "vehicle_image_id": str(doc["vehicle_image_id"]) if doc.get("vehicle_image_id") else None,
        "plate_image_path": doc.get("plate_image_path"),
        "vehicle_image_path": doc.get("vehicle_image_path"),
        "list_name": doc.get("vehicle_list_name", ""),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@anpr_bp.route("/anpr")
def anpr_page():
    payload = getattr(request, "user_payload", {})
    user_roles = payload.get("roles", [])
    return render_template(
        "anpr.html",
        user_roles=user_roles,
        user_firstname=payload.get("firstname", ""),
        user_lastname=payload.get("lastname", ""),
        user_email=payload.get("email", ""),
    )


@anpr_bp.route("/api/anpr/search")
def anpr_search():
    """Search ANPR records with filters, pagination."""
    _ensure_db()
    cam_cfgs = _get_cam_configs()
    query = {}

    # Plate search (partial match)
    plate = request.args.get("plate", "").strip().upper()
    if plate:
        query["license_plate"] = {"$regex": plate, "$options": "i"}

    # Exclude UNKNOWN plates unless explicitly searching
    if not plate:
        query["license_plate"] = {"$ne": "UNKNOWN"}

    # Color filter
    color = request.args.get("color", "").strip()
    if color:
        query["vehicle_color"] = color

    # Type filter
    vtype = request.args.get("type", "").strip()
    if vtype:
        query["vehicle_type"] = vtype

    # Brand filter
    brand = request.args.get("brand", "").strip()
    if brand:
        # Reverse lookup brand -> logo IDs
        logo_ids = [k for k, v in BRAND_MAP.items() if v == brand]
        if logo_ids:
            query["vehicle_logo"] = {"$in": logo_ids}

    # Camera filter
    camera = request.args.get("camera", "").strip()
    if camera:
        query["camera_path"] = camera

    # Direction filter (resolved with camera config)
    direction = request.args.get("direction", "").strip()
    if direction in ("entry", "exit"):
        # Get camera configs
        configs = {c["camera_path"]: c for c in _col_camera_config.find()}
        entry_cameras = []
        exit_cameras = []
        for path, cfg in configs.items():
            fwd = cfg.get("forward_role", "entry")
            if fwd == "entry":
                entry_cameras.append(path)
                # backward = exit (implicit)
            else:
                exit_cameras.append(path)

        if direction == "entry":
            # forward on entry cameras OR reverse on exit cameras
            dir_conds = []
            if entry_cameras:
                dir_conds.append({"camera_path": {"$in": entry_cameras}, "direction": "forward"})
                dir_conds.append({"camera_path": {"$in": entry_cameras}, "direction": {"$ne": "forward"}})
            if exit_cameras:
                dir_conds.append({"camera_path": {"$in": exit_cameras}, "direction": {"$ne": "forward"}})
            # Simplify: entry = forward on entry_cams + reverse on exit_cams
            dir_conds = []
            if entry_cameras:
                dir_conds.append({"camera_path": {"$in": entry_cameras}, "direction": "forward"})
            if exit_cameras:
                dir_conds.append({"camera_path": {"$in": exit_cameras}, "direction": "reverse"})
            if dir_conds:
                query["$or"] = dir_conds
        elif direction == "exit":
            dir_conds = []
            if entry_cameras:
                dir_conds.append({"camera_path": {"$in": entry_cameras}, "direction": "reverse"})
            if exit_cameras:
                dir_conds.append({"camera_path": {"$in": exit_cameras}, "direction": "forward"})
            if dir_conds:
                query["$or"] = dir_conds

    # Date range
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    if date_from or date_to:
        dt_filter = {}
        if date_from:
            try:
                dt_filter["$gte"] = datetime.fromisoformat(date_from)
            except ValueError:
                pass
        if date_to:
            try:
                dt_filter["$lte"] = datetime.fromisoformat(date_to)
            except ValueError:
                pass
        if dt_filter:
            query["event_dt"] = dt_filter

    # Confidence min
    conf_min = request.args.get("conf_min", "").strip()
    if conf_min:
        try:
            query["confidence"] = {"$gte": int(conf_min)}
        except ValueError:
            pass

    # Pagination
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, int(request.args.get("per_page", 50)))
    skip = (page - 1) * per_page

    total = _col_anpr.count_documents(query)
    docs = list(_col_anpr.find(query).sort("event_dt", DESCENDING).skip(skip).limit(per_page))

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "results": [_serialize(d, cam_cfgs) for d in docs],
    })


@anpr_bp.route("/api/anpr/stats")
def anpr_stats():
    """Aggregate statistics for the dashboard."""
    _ensure_db()

    # Base query (exclude unknowns for stats)
    base_match = {"license_plate": {"$ne": "UNKNOWN"}}

    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    if date_from or date_to:
        dt_filter = {}
        if date_from:
            try:
                dt_filter["$gte"] = datetime.fromisoformat(date_from)
            except ValueError:
                pass
        if date_to:
            try:
                dt_filter["$lte"] = datetime.fromisoformat(date_to)
            except ValueError:
                pass
        if dt_filter:
            base_match["event_dt"] = dt_filter

    pipeline_total = [{"$match": base_match}, {"$count": "n"}]
    total_res = list(_col_anpr.aggregate(pipeline_total))
    total = total_res[0]["n"] if total_res else 0

    pipeline_unique = [
        {"$match": base_match},
        {"$group": {"_id": "$license_plate"}},
        {"$count": "n"},
    ]
    unique_res = list(_col_anpr.aggregate(pipeline_unique))
    unique_plates = unique_res[0]["n"] if unique_res else 0

    # By color
    pipeline_color = [
        {"$match": base_match},
        {"$group": {"_id": "$vehicle_color", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    by_color = {d["_id"]: d["count"] for d in _col_anpr.aggregate(pipeline_color) if d["_id"]}

    # By brand (top 15)
    pipeline_brand = [
        {"$match": base_match},
        {"$group": {"_id": "$vehicle_logo", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15},
    ]
    by_brand = [{"brand": _brand(d["_id"]), "count": d["count"]}
                for d in _col_anpr.aggregate(pipeline_brand)]

    # By type
    pipeline_type = [
        {"$match": base_match},
        {"$group": {"_id": "$vehicle_type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    by_type = [{"type": d["_id"], "label": VEHICLE_TYPE_LABELS.get(d["_id"], d["_id"]), "count": d["count"]}
               for d in _col_anpr.aggregate(pipeline_type) if d["_id"]]

    # By camera
    pipeline_camera = [
        {"$match": base_match},
        {"$group": {"_id": "$camera_path", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    by_camera = {d["_id"]: d["count"] for d in _col_anpr.aggregate(pipeline_camera) if d["_id"]}

    # Hourly distribution
    pipeline_hourly = [
        {"$match": base_match},
        {"$group": {"_id": {"$hour": "$event_dt"}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    by_hour = {d["_id"]: d["count"] for d in _col_anpr.aggregate(pipeline_hourly)}

    # Allowlist count
    pipeline_allow = [
        {"$match": {**base_match, "vehicle_list_name": "allowList"}},
        {"$count": "n"},
    ]
    allow_res = list(_col_anpr.aggregate(pipeline_allow))
    allowlist_count = allow_res[0]["n"] if allow_res else 0

    # Avg confidence
    pipeline_conf = [
        {"$match": {**base_match, "confidence": {"$gt": 0}}},
        {"$group": {"_id": None, "avg": {"$avg": "$confidence"}}},
    ]
    conf_res = list(_col_anpr.aggregate(pipeline_conf))
    avg_confidence = round(conf_res[0]["avg"], 1) if conf_res else 0

    return jsonify({
        "total": total,
        "unique_plates": unique_plates,
        "allowlist_count": allowlist_count,
        "avg_confidence": avg_confidence,
        "by_color": by_color,
        "by_brand": by_brand,
        "by_type": by_type,
        "by_camera": by_camera,
        "by_hour": by_hour,
        "color_hex": COLOR_HEX,
    })


@anpr_bp.route("/api/anpr/live")
def anpr_live():
    """Last N detections for live feed."""
    _ensure_db()
    cam_cfgs = _get_cam_configs()
    n = min(25, int(request.args.get("n", 10)))
    docs = list(_col_anpr.find({"license_plate": {"$ne": "UNKNOWN"}}).sort("event_dt", DESCENDING).limit(n))
    return jsonify([_serialize(d, cam_cfgs) for d in docs])


@anpr_bp.route("/api/anpr/plate/<plate>")
def anpr_plate_history(plate):
    """All detections for a given plate."""
    _ensure_db()
    cam_cfgs = _get_cam_configs()
    plate = plate.strip().upper()
    docs = list(_col_anpr.find({"license_plate": plate}).sort("event_dt", DESCENDING).limit(200))
    return jsonify({
        "plate": plate,
        "count": len(docs),
        "records": [_serialize(d, cam_cfgs) for d in docs],
    })


HIK_IMAGE_DIR = "E:/TITAN/production/hik_images"


@anpr_bp.route("/api/anpr/image/<path:image_ref>")
def anpr_image(image_ref):
    """Sert une image : chemin disque (nouveau) ou ObjectId GridFS (ancien)."""
    _ensure_db()

    # Nouveau format : chemin relatif sur disque
    if "/" in image_ref:
        safe = os.path.normpath(image_ref)
        if ".." in safe:
            abort(400)
        full_path = os.path.join(HIK_IMAGE_DIR, safe)
        if not os.path.abspath(full_path).startswith(os.path.abspath(HIK_IMAGE_DIR)):
            abort(400)
        if os.path.isfile(full_path):
            resp = send_file(full_path, mimetype="image/jpeg")
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp

    # Ancien format : ObjectId GridFS
    else:
        try:
            oid = ObjectId(image_ref)
            grid_file = _fs.get(oid)
            return Response(
                grid_file.read(),
                mimetype="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400"},
            )
        except Exception:
            pass

    # Fallback : pixel transparent 1x1
    return Response(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
        mimetype="image/png",
        status=404,
    )


# ---------------------------------------------------------------------------
# Camera config
# ---------------------------------------------------------------------------

@anpr_bp.route("/api/anpr/cameras")
def anpr_cameras():
    """Get camera list with config."""
    _ensure_db()
    # Get distinct cameras from data
    cameras = _col_anpr.distinct("camera_path")
    configs = {c["camera_path"]: c for c in _col_camera_config.find()}

    result = []
    for cam in sorted(cameras):
        cfg = configs.get(cam, {})
        result.append({
            "camera_path": cam,
            "label": cfg.get("label", cam.replace("/", "").replace("lapi", "LAPI ")),
            "forward_role": cfg.get("forward_role", "entry"),
            "enabled": cfg.get("enabled", True),
        })
    return jsonify(result)


@anpr_bp.route("/api/anpr/cameras/config", methods=["POST"])
def anpr_cameras_config():
    """Update camera configuration."""
    _ensure_db()
    data = request.get_json()
    if not data or "camera_path" not in data:
        return jsonify({"error": "camera_path required"}), 400

    _col_camera_config.update_one(
        {"camera_path": data["camera_path"]},
        {"$set": {
            "camera_path": data["camera_path"],
            "label": data.get("label", ""),
            "forward_role": data.get("forward_role", "entry"),
            "enabled": data.get("enabled", True),
        }},
        upsert=True,
    )
    return jsonify({"ok": True})


@anpr_bp.route("/api/anpr/flow")
def anpr_flow():
    """Entries vs exits over time (15-min buckets)."""
    _ensure_db()

    base_match = {"license_plate": {"$ne": "UNKNOWN"}}
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    if date_from:
        try:
            base_match.setdefault("event_dt", {})["$gte"] = datetime.fromisoformat(date_from)
        except ValueError:
            pass
    if date_to:
        try:
            base_match.setdefault("event_dt", {})["$lte"] = datetime.fromisoformat(date_to)
        except ValueError:
            pass

    # Get camera configs for entry/exit resolution
    configs = {c["camera_path"]: c for c in _col_camera_config.find()}

    # Build per-camera direction classification
    entry_match = []
    exit_match = []
    for cam in _col_anpr.distinct("camera_path"):
        cfg = configs.get(cam, {})
        fwd_role = cfg.get("forward_role", "entry")
        if fwd_role == "entry":
            entry_match.append({"camera_path": cam, "direction": "forward"})
            exit_match.append({"camera_path": cam, "direction": "reverse"})
        else:
            exit_match.append({"camera_path": cam, "direction": "forward"})
            entry_match.append({"camera_path": cam, "direction": "reverse"})

    def bucket_pipeline(dir_conditions):
        if not dir_conditions:
            return []
        return list(_col_anpr.aggregate([
            {"$match": {**base_match, "$or": dir_conditions}},
            {"$group": {
                "_id": {
                    "$dateTrunc": {"date": "$event_dt", "unit": "minute", "binSize": 15}
                },
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]))

    entries = bucket_pipeline(entry_match)
    exits = bucket_pipeline(exit_match)

    return jsonify({
        "entries": [{"t": d["_id"].isoformat(), "n": d["count"]} for d in entries],
        "exits": [{"t": d["_id"].isoformat(), "n": d["count"]} for d in exits],
    })


# ---------------------------------------------------------------------------
# On-site vehicle counter (entries - exits since last reset)
# ---------------------------------------------------------------------------

def _count_direction(cam_configs, since, target_dir):
    """Count detections resolved as 'entry' or 'exit' since a given datetime."""
    match_conds = []
    for cam_path, cfg in cam_configs.items():
        fwd_role = cfg.get("forward_role", "entry")
        if target_dir == "entry":
            raw = "forward" if fwd_role == "entry" else "reverse"
        else:
            raw = "reverse" if fwd_role == "entry" else "forward"
        match_conds.append({"camera_path": cam_path, "direction": raw})

    # Also include cameras with no config (default forward=entry)
    all_cams = _col_anpr.distinct("camera_path")
    for cam in all_cams:
        if cam not in cam_configs:
            raw = "forward" if target_dir == "entry" else "reverse"
            match_conds.append({"camera_path": cam, "direction": raw})

    if not match_conds:
        return 0

    base = {"license_plate": {"$ne": "UNKNOWN"}, "event_dt": {"$gte": since}}
    pipeline = [
        {"$match": {**base, "$or": match_conds}},
        {"$count": "n"},
    ]
    res = list(_col_anpr.aggregate(pipeline))
    return res[0]["n"] if res else 0


@anpr_bp.route("/api/anpr/onsite")
def anpr_onsite():
    """Vehicles currently on site = entries - exits since last reset."""
    _ensure_db()
    cam_configs = _get_cam_configs()

    # Get reset timestamp (or epoch if never reset)
    doc = _col_site_counter.find_one({"_id": "reset"})
    reset_at = doc["reset_at"] if doc else datetime(2000, 1, 1)

    entries = _count_direction(cam_configs, reset_at, "entry")
    exits = _count_direction(cam_configs, reset_at, "exit")
    on_site = max(0, entries - exits)

    return jsonify({
        "on_site": on_site,
        "entries": entries,
        "exits": exits,
        "reset_at": reset_at.isoformat() if isinstance(reset_at, datetime) else str(reset_at),
    })


@anpr_bp.route("/api/anpr/onsite/reset", methods=["POST"])
def anpr_onsite_reset():
    """Reset the on-site counter to 0 (stores current timestamp)."""
    _ensure_db()
    now = datetime.now(timezone.utc)
    _col_site_counter.update_one(
        {"_id": "reset"},
        {"$set": {"reset_at": now}},
        upsert=True,
    )
    return jsonify({"ok": True, "reset_at": now.isoformat()})
