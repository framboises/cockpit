# vision_admin.py - Blueprint Flask pour la gestion autonome des tablettes Vision
# (app externe https://vision-a0f55.web.app, scan billets vehicule).
#
# Totalement dissocie de Field :
#   - Collections MongoDB dediees : vision_pairings, vision_devices
#   - Routes URL sous /field/... (pour beneficier de la whitelist d'auth Cockpit
#     existante, /field/* etant deja public sans portail) mais code 100% separe
#   - Aucune lecture/ecriture des collections field_*
#
# Routes :
#   - POST   /field/api/vision/pair                   (public + CORS, app Vision)
#   - GET    /field/admin/vision/pairings             (admin Cockpit)
#   - POST   /field/admin/vision/pairings             (admin)
#   - DELETE /field/admin/vision/pairings/<code>      (admin)
#   - GET    /field/admin/vision/devices              (admin)
#   - POST   /field/admin/vision/devices/<id>/lieu    (admin)
#   - POST   /field/admin/vision/devices/<id>/revoke  (admin)
#   - DELETE /field/admin/vision/devices/<id>         (admin)

from flask import Blueprint, jsonify, request, make_response
from datetime import datetime, timezone, timedelta
from bson.objectid import ObjectId
import os
import secrets
import logging
import re

# Reutilise les helpers generiques de field.py (mongo, admin_required, rate-limit).
# Les schemas et collections restent strictement separes.
from field import (
    admin_required,
    _get_mongo_db,
    _client_ip,
    _wants_json,
    _rate_limit_pair,
    _generate_pairing_code,
    _now,
    _iso,
    _event_end_datetime,
)


vision_admin_bp = Blueprint("vision_admin", __name__)
logger = logging.getLogger("vision_admin")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

PAIRING_CODE_TTL_SECONDS = 15 * 60       # code valable 15 min
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

VISION_LIEUX = ["Ouest", "Panorama", "Houx"]
VISION_APP_URL = os.getenv("VISION_APP_URL", "https://vision-a0f55.web.app/associer.html")
VISION_JWT_ISSUER = "cockpit-vision"
VISION_JWT_KEY_PATH = os.getenv(
    "VISION_JWT_PRIVATE_KEY",
    os.path.join(SCRIPT_DIR, "keys", "vision_jwt_private.pem"),
)
VISION_JWT_END_MARGIN_DAYS = 1            # marge apres date de demontage

VISION_ALLOWED_ORIGINS = [
    "https://vision-a0f55.web.app",
    "https://vision-a0f55.firebaseapp.com",
]


# ---------------------------------------------------------------------------
# Cle privee JWT + indexes Mongo
# ---------------------------------------------------------------------------

_priv_key_pem = None
_pub_key_pem = None
_INDEXES_READY = False


def _load_priv_key():
    global _priv_key_pem
    if _priv_key_pem is not None:
        return _priv_key_pem
    if not os.path.exists(VISION_JWT_KEY_PATH):
        logger.error("vision: cle privee JWT introuvable: %s", VISION_JWT_KEY_PATH)
        return None
    try:
        with open(VISION_JWT_KEY_PATH, "rb") as f:
            _priv_key_pem = f.read()
        return _priv_key_pem
    except Exception as exc:
        logger.error("vision: lecture cle privee JWT echouee: %s", exc)
        return None


def _get_pub_key():
    """Derive et met en cache la cle publique PEM depuis la cle privee."""
    global _pub_key_pem
    if _pub_key_pem is not None:
        return _pub_key_pem
    priv_pem = _load_priv_key()
    if priv_pem is None:
        return None
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        priv_obj = load_pem_private_key(priv_pem, password=None)
        _pub_key_pem = priv_obj.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        return _pub_key_pem
    except Exception as exc:
        logger.error("vision: derivation cle publique echouee: %s", exc)
        return None


def _ensure_vision_indexes(db):
    global _INDEXES_READY
    if _INDEXES_READY:
        return
    try:
        db["vision_pairings"].create_index("code", unique=True)
        db["vision_pairings"].create_index("expiresAt", expireAfterSeconds=0)
        db["vision_devices"].create_index([("event", 1), ("year", 1)])
        db["vision_devices"].create_index([("event", 1), ("year", 1), ("name", 1)])
    except Exception as e:
        logger.warning("vision: index creation failed: %s", e)
    _INDEXES_READY = True


def _db():
    db = _get_mongo_db()
    _ensure_vision_indexes(db)
    return db


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def _generate_jwt(device, end_dt):
    """Genere un JWT RS256 pour autoriser un device Vision.
    end_dt : datetime UTC (fin d'evenement) ou None (=> 24 h par defaut)."""
    import jwt as _pyjwt
    priv_pem = _load_priv_key()
    if priv_pem is None:
        raise RuntimeError("vision_jwt_key_missing")

    now = _now()
    exp = (end_dt + timedelta(days=VISION_JWT_END_MARGIN_DAYS)) if end_dt else (now + timedelta(hours=24))
    year_val = device.get("year")
    annee = int(year_val) if str(year_val or "").isdigit() else year_val

    payload = {
        "iss": VISION_JWT_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "device_id": str(device.get("_id")),
        "device_name": device.get("name") or "",
        "evenement": device.get("event") or "",
        "annee": annee,
        "lieu": device.get("lieu") or "",
    }
    return _pyjwt.encode(payload, priv_pem, algorithm="RS256")


# ---------------------------------------------------------------------------
# CORS pour l'endpoint public
# ---------------------------------------------------------------------------

def _cors_origin():
    origin = request.headers.get("Origin", "").strip()
    return origin if origin in VISION_ALLOWED_ORIGINS else None


def _cors_response(payload, status=200):
    resp = make_response(jsonify(payload), status)
    origin = _cors_origin()
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    return resp


# ---------------------------------------------------------------------------
# Helpers de publication
# ---------------------------------------------------------------------------

def _pub_pairing(p):
    if not p:
        return None
    return {
        "code": p.get("code"),
        "name": p.get("name"),
        "event": p.get("event"),
        "year": p.get("year"),
        "lieu": p.get("lieu"),
        "notes": p.get("notes", ""),
        "createdAt": _iso(p.get("createdAt")),
        "expiresAt": _iso(p.get("expiresAt")),
        "created_by": p.get("created_by"),
    }


def _pub_device(d):
    if not d:
        return None
    return {
        "id": str(d.get("_id")),
        "name": d.get("name"),
        "event": d.get("event"),
        "year": d.get("year"),
        "lieu": d.get("lieu"),
        "notes": d.get("notes", ""),
        "created_at": _iso(d.get("createdAt")),
        "paired_at": _iso(d.get("paired_at")),
        "last_seen": _iso(d.get("last_seen")),
        "last_ip": d.get("last_ip"),
        "last_ua": d.get("last_ua"),
        "revoked": bool(d.get("revoked")),
        "revoked_at": _iso(d.get("revokedAt")),
        "last_battery": d.get("last_battery"),
        "last_charging": d.get("last_charging"),
        "last_lat": d.get("last_lat"),
        "last_lng": d.get("last_lng"),
        "last_accuracy": d.get("last_accuracy"),
        "last_position_ts": _iso(d.get("last_position_ts")),
    }


def _label_conflict(db, name, event, year, exclude_device_id=None):
    """True si le nom collisionne avec une autre tablette Vision dans le meme evenement."""
    if not name:
        return False
    query = {"name": name, "event": event, "year": str(year), "revoked": {"$ne": True}}
    if exclude_device_id is not None:
        query["_id"] = {"$ne": exclude_device_id}
    return db["vision_devices"].count_documents(query) > 0


# ---------------------------------------------------------------------------
# Endpoint public : pairing depuis l'app Vision
# ---------------------------------------------------------------------------

@vision_admin_bp.route("/field/api/vision/pair", methods=["POST", "OPTIONS"])
def vision_api_pair():
    """Echange un code de pairing Vision contre un JWT RS256.
    Appele en CORS depuis https://vision-a0f55.web.app par l'app Vision."""
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        origin = _cors_origin()
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Access-Control-Max-Age"] = "3600"
            resp.headers["Vary"] = "Origin"
        return resp

    ip = _client_ip()
    if not _rate_limit_pair(ip):
        return _cors_response({"ok": False, "error": "rate_limited"}, 429)

    data = request.get_json(silent=True) or {}
    code = str(data.get("code") or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return _cors_response({"ok": False, "error": "invalid_code_format"}, 400)

    db = _db()
    pairing = db["vision_pairings"].find_one({"code": code})
    if not pairing:
        return _cors_response({"ok": False, "error": "unknown_code"}, 404)

    exp_at = pairing.get("expiresAt")
    if isinstance(exp_at, datetime):
        if exp_at.tzinfo is None:
            exp_at = exp_at.replace(tzinfo=timezone.utc)
        if exp_at < _now():
            db["vision_pairings"].delete_one({"_id": pairing["_id"]})
            return _cors_response({"ok": False, "error": "expired_code"}, 410)

    if pairing.get("used"):
        return _cors_response({"ok": False, "error": "code_already_used"}, 409)

    # Creer le device Vision (l'_id sert de device_id stable pour la tracabilite)
    device_doc = {
        "_id": pairing["_id"],
        "name": pairing.get("name") or "",
        "event": pairing.get("event") or "",
        "year": pairing.get("year") or "",
        "lieu": pairing.get("lieu") or "",
        "notes": pairing.get("notes", ""),
        "createdAt": _now(),
        "paired_at": _now(),
        "paired_ip": ip,
        "paired_ua": (request.headers.get("User-Agent") or "")[:200],
        "last_seen": _now(),
        "last_ip": ip,
        "revoked": False,
    }

    end_dt = _event_end_datetime(db, device_doc["event"], device_doc["year"])
    try:
        token = _generate_jwt(device_doc, end_dt)
    except RuntimeError as exc:
        logger.error("vision: generation JWT impossible: %s", exc)
        return _cors_response({"ok": False, "error": "vision_jwt_unavailable"}, 500)

    # Persister le device puis consommer le code
    try:
        db["vision_devices"].insert_one(device_doc)
    except Exception as exc:
        logger.warning("vision: insert device echoue: %s", exc)
    db["vision_pairings"].delete_one({"_id": pairing["_id"]})

    return _cors_response({
        "ok": True,
        "jwt": token,
        "exp": int(end_dt.timestamp()) if end_dt else None,
        "device_name": device_doc["name"],
        "evenement": device_doc["event"],
        "annee": device_doc["year"],
        "lieu": device_doc["lieu"],
    })


# ---------------------------------------------------------------------------
# Endpoint public : heartbeat depuis l'app Vision (batterie + GPS)
# ---------------------------------------------------------------------------

@vision_admin_bp.route("/field/api/vision/heartbeat", methods=["POST", "OPTIONS"])
def vision_api_heartbeat():
    """Recoit la position GPS et le niveau de batterie d'une tablette Vision.
    Authentifie via JWT Bearer (meme token que l'app utilise pour Firestore)."""
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        origin = _cors_origin()
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            resp.headers["Access-Control-Max-Age"] = "3600"
            resp.headers["Vary"] = "Origin"
        return resp

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return _cors_response({"ok": False, "error": "no_jwt"}, 401)
    token = auth[7:].strip()

    pub_pem = _get_pub_key()
    if pub_pem is None:
        return _cors_response({"ok": False, "error": "server_error"}, 500)

    try:
        import jwt as _pyjwt
        payload = _pyjwt.decode(token, pub_pem, algorithms=["RS256"], issuer=VISION_JWT_ISSUER)
    except Exception as exc:
        logger.debug("vision heartbeat: JWT invalide: %s", exc)
        return _cors_response({"ok": False, "error": "invalid_jwt"}, 401)

    device_id = payload.get("device_id")
    if not device_id:
        return _cors_response({"ok": False, "error": "no_device_id"}, 400)

    data = request.get_json(silent=True) or {}
    update = {"last_seen": _now(), "last_ip": _client_ip()}

    battery = data.get("battery")
    if battery is not None:
        try:
            update["last_battery"] = float(battery)
            update["last_charging"] = bool(data.get("charging"))
        except (TypeError, ValueError):
            pass

    lat = data.get("lat")
    lng = data.get("lng")
    if lat is not None and lng is not None:
        try:
            update["last_lat"] = float(lat)
            update["last_lng"] = float(lng)
            acc = data.get("accuracy")
            update["last_accuracy"] = float(acc) if acc is not None else None
            update["last_position_ts"] = _now()
        except (TypeError, ValueError):
            pass

    db = _db()
    try:
        oid = ObjectId(device_id)
        db["vision_devices"].update_one({"_id": oid}, {"$set": update})
    except Exception as exc:
        logger.warning("vision heartbeat: update mongo echoue: %s", exc)
        return _cors_response({"ok": False, "error": "db_error"}, 500)

    return _cors_response({"ok": True})


# ---------------------------------------------------------------------------
# Admin : pairings
# ---------------------------------------------------------------------------

@vision_admin_bp.route("/field/admin/vision/pairings", methods=["GET"])
@admin_required
def vision_admin_pairings_list():
    """Liste les codes de pairing Vision actifs (filtre event/year optionnel)."""
    db = _db()
    query = {}
    event = request.args.get("event")
    year = request.args.get("year")
    if event:
        query["event"] = event
    if year:
        query["year"] = str(year)
    pairings = [_pub_pairing(p) for p in db["vision_pairings"].find(query).sort("createdAt", -1)]
    return jsonify({"pairings": pairings})


@vision_admin_bp.route("/field/admin/vision/pairings", methods=["POST"])
@admin_required
def vision_admin_pairings_create():
    """Cree un code de pairing Vision. Payload : name, event, year, lieu, notes?"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    event = (data.get("event") or "").strip()
    year = str(data.get("year") or "").strip()
    lieu = (data.get("lieu") or "").strip()
    notes = (data.get("notes") or "").strip()

    if not name:
        return jsonify({"ok": False, "error": "missing_name"}), 400
    if not event or not year:
        return jsonify({"ok": False, "error": "missing_event_year"}), 400
    if lieu not in VISION_LIEUX:
        return jsonify({"ok": False, "error": "invalid_lieu"}), 400

    db = _db()
    if _label_conflict(db, name, event, year):
        return jsonify({"ok": False, "error": "name_conflict"}), 409

    code = None
    for _ in range(5):
        candidate = _generate_pairing_code()
        if db["vision_pairings"].find_one({"code": candidate}) is None:
            code = candidate
            break
    if code is None:
        return jsonify({"ok": False, "error": "code_generation_failed"}), 500

    doc = {
        "code": code,
        "name": name,
        "event": event,
        "year": year,
        "lieu": lieu,
        "notes": notes,
        "createdAt": _now(),
        "expiresAt": _now() + timedelta(seconds=PAIRING_CODE_TTL_SECONDS),
        "created_by": (getattr(request, "admin_user", None) or {}).get("email", "?"),
    }
    db["vision_pairings"].insert_one(doc)
    return jsonify({"ok": True, "pairing": _pub_pairing(doc)}), 201


@vision_admin_bp.route("/field/admin/vision/pairings/<code>", methods=["DELETE"])
@admin_required
def vision_admin_pairings_delete(code):
    """Supprime un code de pairing Vision non utilise."""
    db = _db()
    res = db["vision_pairings"].delete_one({"code": code})
    if res.deleted_count == 0:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Admin : devices
# ---------------------------------------------------------------------------

@vision_admin_bp.route("/field/admin/vision/devices", methods=["GET"])
@admin_required
def vision_admin_devices_list():
    """Liste les tablettes Vision enrolees (filtre event/year/lieu)."""
    db = _db()
    query = {}
    event = request.args.get("event")
    year = request.args.get("year")
    lieu = request.args.get("lieu")
    include_revoked = request.args.get("include_revoked", "true").lower() in ("1", "true", "yes")
    if event:
        query["event"] = event
    if year:
        query["year"] = str(year)
    if lieu:
        query["lieu"] = lieu
    if not include_revoked:
        query["revoked"] = {"$ne": True}
    devices = [_pub_device(d) for d in db["vision_devices"].find(query).sort("createdAt", -1)]
    return jsonify({"devices": devices})


@vision_admin_bp.route("/field/admin/vision/devices/<device_id>/lieu", methods=["POST"])
@admin_required
def vision_admin_device_lieu(device_id):
    """Modifie le lieu d'une tablette Vision. Le JWT existant garde son ancien lieu
    jusqu'a expiration ; un nouveau pairing prendra le nouveau lieu."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    data = request.get_json(silent=True) or {}
    lieu = (data.get("lieu") or "").strip()
    if lieu not in VISION_LIEUX:
        return jsonify({"ok": False, "error": "invalid_lieu"}), 400
    db = _db()
    res = db["vision_devices"].update_one(
        {"_id": oid},
        {"$set": {"lieu": lieu, "lieu_updated_at": _now()}},
    )
    if res.matched_count == 0:
        return jsonify({"ok": False, "error": "not_found"}), 404
    device = db["vision_devices"].find_one({"_id": oid})
    return jsonify({"ok": True, "device": _pub_device(device)})


@vision_admin_bp.route("/field/admin/vision/devices/<device_id>/revoke", methods=["POST"])
@admin_required
def vision_admin_device_revoke(device_id):
    """Revoque une tablette Vision. Le JWT existant reste valide jusqu'a son exp
    (limitation inherente au JWT stateless) - cf. CLAUDE.md."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _db()
    res = db["vision_devices"].update_one(
        {"_id": oid},
        {"$set": {"revoked": True, "revokedAt": _now()}},
    )
    if res.matched_count == 0:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


@vision_admin_bp.route("/field/admin/vision/devices/<device_id>", methods=["DELETE"])
@admin_required
def vision_admin_device_delete(device_id):
    """Supprime definitivement une tablette Vision (purge de l'inventaire)."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _db()
    res = db["vision_devices"].delete_one({"_id": oid})
    if res.deleted_count == 0:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})
