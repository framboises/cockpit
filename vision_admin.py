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
SESSION_INACTIVE_TIMEOUT_HOURS = 4        # auto-logout si pas de heartbeat depuis 4h
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
        db["vision_devices"].create_index("tablet_uid")
        db["vision_sessions"].create_index("tablet_uid")
        db["vision_sessions"].create_index("device_id")
        db["vision_sessions"].create_index([("started_at", -1)])
        db["vision_sessions"].create_index([("ended_at", 1), ("last_seen", 1)])
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


def _cors_preflight():
    """Reponse standard pour les requetes OPTIONS des endpoints publics
    avec Authorization (identify, logout, heartbeat)."""
    resp = make_response("", 204)
    origin = _cors_origin()
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Max-Age"] = "3600"
        resp.headers["Vary"] = "Origin"
    return resp


# ---------------------------------------------------------------------------
# Authentification JWT (commune a /heartbeat, /identify, /logout)
# ---------------------------------------------------------------------------

def _verify_request_jwt():
    """Decode et valide le JWT Bearer. Retourne (payload, None) si OK,
    (None, error_response) sinon. L'appelant doit checker error_response
    et retourner directement si non-None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, _cors_response({"ok": False, "error": "no_jwt"}, 401)
    token = auth[7:].strip()

    pub_pem = _get_pub_key()
    if pub_pem is None:
        return None, _cors_response({"ok": False, "error": "server_error"}, 500)

    try:
        import jwt as _pyjwt
        payload = _pyjwt.decode(token, pub_pem, algorithms=["RS256"], issuer=VISION_JWT_ISSUER)
    except Exception as exc:
        logger.debug("vision: JWT invalide: %s", exc)
        return None, _cors_response({"ok": False, "error": "invalid_jwt"}, 401)
    return payload, None


def _check_device_active(db, payload):
    """Verifie que le device existe et n'est pas revoque. Retourne (oid, device, None)
    ou (None, None, error_response)."""
    device_id = payload.get("device_id")
    if not device_id:
        return None, None, _cors_response({"ok": False, "error": "no_device_id"}, 400)
    try:
        oid = ObjectId(device_id)
    except Exception:
        return None, None, _cors_response({"ok": False, "error": "invalid_device_id"}, 400)
    device = db["vision_devices"].find_one({"_id": oid})
    if device is None or device.get("revoked"):
        return None, None, _cors_response({"ok": False, "error": "revoked"}, 403)
    return oid, device, None


# ---------------------------------------------------------------------------
# Sweep auto-logout : ferme les sessions sans heartbeat depuis N heures
# ---------------------------------------------------------------------------

def _sweep_inactive_sessions(db):
    """Cloture les sessions Vision orphelines (sans heartbeat depuis 4 h).
    Appele opportunistiquement par /heartbeat, /identify, /logout, /sessions."""
    cutoff = _now() - timedelta(hours=SESSION_INACTIVE_TIMEOUT_HOURS)
    try:
        db["vision_sessions"].update_many(
            {"ended_at": None, "last_seen": {"$lt": cutoff}},
            {"$set": {"ended_at": _now(), "ended_reason": "timeout"}},
        )
    except Exception as exc:
        logger.warning("vision: sweep sessions echoue: %s", exc)


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
    cu = d.get("current_user") or {}
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
        "tablet_uid": d.get("tablet_uid"),
        "current_user": ({
            "employee_number": cu.get("employee_number"),
            "person_id_external": cu.get("person_id_external"),
            "id_source": cu.get("id_source"),
            "firstname": cu.get("firstname"),
            "lastname": cu.get("lastname"),
            "started_at": _iso(cu.get("started_at")),
            "session_id": str(cu.get("session_id")) if cu.get("session_id") else None,
        } if cu.get("employee_number") else None),
    }


def _pub_session(s):
    if not s:
        return None
    return {
        "id": str(s.get("_id")),
        "tablet_uid": s.get("tablet_uid"),
        "device_id": str(s.get("device_id")) if s.get("device_id") else None,
        "device_name": s.get("device_name"),
        "event": s.get("event"),
        "year": s.get("year"),
        "lieu": s.get("lieu"),
        "employee_number": s.get("employee_number"),
        "person_id_external": s.get("person_id_external"),
        "scanned_code": s.get("scanned_code"),
        "id_source": s.get("id_source"),
        "firstname": s.get("firstname"),
        "lastname": s.get("lastname"),
        "started_at": _iso(s.get("started_at")),
        "ended_at": _iso(s.get("ended_at")),
        "ended_reason": s.get("ended_reason"),
        "last_seen": _iso(s.get("last_seen")),
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
    tablet_uid = str(data.get("tablet_uid") or "").strip() or None
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

    # Creer le device Vision (l'_id de pairing sert de device_id pour ce JWT).
    # tablet_uid (genere cote tablette) reste stable a travers les ré-enrôlements.
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
        "tablet_uid": tablet_uid,
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
    """Recoit position GPS + batterie d'une tablette Vision et materialise
    la revocation. Met aussi a jour la session operateur active si elle existe."""
    if request.method == "OPTIONS":
        return _cors_preflight()

    payload, err = _verify_request_jwt()
    if err is not None:
        return err

    db = _db()
    _sweep_inactive_sessions(db)

    oid, device, err = _check_device_active(db, payload)
    if err is not None:
        return err

    data = request.get_json(silent=True) or {}
    tablet_uid = (data.get("tablet_uid") or "").strip() or None

    update = {"last_seen": _now(), "last_ip": _client_ip()}
    if tablet_uid and not device.get("tablet_uid"):
        update["tablet_uid"] = tablet_uid

    session_update = {"last_seen": _now()}

    battery = data.get("battery")
    if battery is not None:
        try:
            bat_val = float(battery)
            update["last_battery"] = bat_val
            update["last_charging"] = bool(data.get("charging"))
            session_update["last_battery"] = bat_val
            session_update["last_charging"] = bool(data.get("charging"))
        except (TypeError, ValueError):
            pass

    lat = data.get("lat")
    lng = data.get("lng")
    if lat is not None and lng is not None:
        try:
            lat_val = float(lat)
            lng_val = float(lng)
            acc = data.get("accuracy")
            acc_val = float(acc) if acc is not None else None
            update["last_lat"] = lat_val
            update["last_lng"] = lng_val
            update["last_accuracy"] = acc_val
            update["last_position_ts"] = _now()
            session_update["last_lat"] = lat_val
            session_update["last_lng"] = lng_val
            session_update["last_accuracy"] = acc_val
        except (TypeError, ValueError):
            pass

    try:
        db["vision_devices"].update_one({"_id": oid}, {"$set": update})
    except Exception as exc:
        logger.warning("vision heartbeat: update device mongo echoue: %s", exc)
        return _cors_response({"ok": False, "error": "db_error"}, 500)

    # MAJ de la session operateur active s'il y en a une (ne crashe pas si aucune)
    try:
        db["vision_sessions"].update_one(
            {"device_id": oid, "ended_at": None},
            {"$set": session_update},
        )
    except Exception as exc:
        logger.warning("vision heartbeat: update session mongo echoue: %s", exc)

    return _cors_response({"ok": True})


# ---------------------------------------------------------------------------
# Endpoint public : identify (scan QR badge -> session operateur)
# ---------------------------------------------------------------------------

@vision_admin_bp.route("/field/api/vision/identify", methods=["POST", "OPTIONS"])
def vision_api_identify():
    """Lie un employee_number (scanne via QR badge) au device JWT-authentifie.
    Lookup planbition_people pour valider, cree une vision_sessions, met a
    jour vision_devices.current_user. Renvoie firstname/lastname."""
    if request.method == "OPTIONS":
        return _cors_preflight()

    payload, err = _verify_request_jwt()
    if err is not None:
        return err

    db = _db()
    _sweep_inactive_sessions(db)

    oid, device, err = _check_device_active(db, payload)
    if err is not None:
        return err

    data = request.get_json(silent=True) or {}
    # `employee_number` est le nom du champ historique cote API, mais le scan
    # peut tres bien remonter un PersonID Adecco. On essaie donc dans l'ordre :
    #   1) person_id_external (PersonID Adecco, index unique sparse)
    #   2) employee_number (fallback historique)
    scanned_code = str(data.get("employee_number") or "").strip()
    tablet_uid = (data.get("tablet_uid") or "").strip() or None
    if not scanned_code:
        return _cors_response({"ok": False, "error": "missing_employee_number"}, 400)

    person = db["planbition_people"].find_one({"person_id_external": scanned_code})
    id_source = "person_id_external" if person else None
    if not person:
        person = db["planbition_people"].find_one({"employee_number": scanned_code})
        id_source = "employee_number" if person else None
    if not person:
        return _cors_response({"ok": False, "error": "unknown_employee"}, 404)

    # Champ canonique : employee_number reel de la personne (peut differer du
    # code scanne si match via PersonID, et fallback sur le code scanne si
    # absent en base — rare mais possible).
    employee_number = (person.get("employee_number") or "").strip() or scanned_code
    person_id_external = (person.get("person_id_external") or "").strip() or None
    firstname = (person.get("firstname") or "").strip()
    lastname = (person.get("lastname") or "").strip()

    now = _now()
    # Cloture toute session active sur ce device (changement d'operateur)
    try:
        db["vision_sessions"].update_many(
            {"device_id": oid, "ended_at": None},
            {"$set": {"ended_at": now, "ended_reason": "switched_user"}},
        )
    except Exception as exc:
        logger.warning("vision identify: cloture sessions echoue: %s", exc)

    session_doc = {
        "tablet_uid": tablet_uid or device.get("tablet_uid"),
        "device_id": oid,
        "device_name": device.get("name") or "",
        "event": device.get("event") or "",
        "year": device.get("year") or "",
        "lieu": device.get("lieu") or "",
        "employee_number": employee_number,
        "person_id_external": person_id_external,
        "scanned_code": scanned_code,
        "id_source": id_source,
        "firstname": firstname,
        "lastname": lastname,
        "started_at": now,
        "ended_at": None,
        "last_seen": now,
        "ip": _client_ip(),
    }
    try:
        ins = db["vision_sessions"].insert_one(session_doc)
        session_id = ins.inserted_id
    except Exception as exc:
        logger.error("vision identify: insert session echoue: %s", exc)
        return _cors_response({"ok": False, "error": "db_error"}, 500)

    device_update = {
        "last_seen": now,
        "current_user": {
            "employee_number": employee_number,
            "person_id_external": person_id_external,
            "id_source": id_source,
            "firstname": firstname,
            "lastname": lastname,
            "started_at": now,
            "session_id": session_id,
        },
    }
    if tablet_uid and not device.get("tablet_uid"):
        device_update["tablet_uid"] = tablet_uid
    try:
        db["vision_devices"].update_one({"_id": oid}, {"$set": device_update})
    except Exception as exc:
        logger.warning("vision identify: update device echoue: %s", exc)

    return _cors_response({
        "ok": True,
        "employee_number": employee_number,
        "person_id_external": person_id_external,
        "id_source": id_source,
        "firstname": firstname,
        "lastname": lastname,
        "started_at": _iso(now),
        "session_id": str(session_id),
    })


# ---------------------------------------------------------------------------
# Endpoint public : logout (cloture session, garde le pairing)
# ---------------------------------------------------------------------------

@vision_admin_bp.route("/field/api/vision/logout", methods=["POST", "OPTIONS"])
def vision_api_logout():
    """Cloture la session operateur active sans desenroler la tablette.
    Le JWT reste valide ; la tablette retombe sur l'ecran d'identification
    au reload cote client."""
    if request.method == "OPTIONS":
        return _cors_preflight()

    payload, err = _verify_request_jwt()
    if err is not None:
        return err

    db = _db()
    _sweep_inactive_sessions(db)

    device_id = payload.get("device_id")
    if not device_id:
        return _cors_response({"ok": False, "error": "no_device_id"}, 400)
    try:
        oid = ObjectId(device_id)
    except Exception:
        return _cors_response({"ok": False, "error": "invalid_device_id"}, 400)

    now = _now()
    try:
        db["vision_sessions"].update_many(
            {"device_id": oid, "ended_at": None},
            {"$set": {"ended_at": now, "ended_reason": "logout"}},
        )
        db["vision_devices"].update_one(
            {"_id": oid},
            {"$set": {"last_seen": now}, "$unset": {"current_user": ""}},
        )
    except Exception as exc:
        logger.warning("vision logout: mongo echoue: %s", exc)
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


# ---------------------------------------------------------------------------
# Admin : sessions operateur (historique tablette)
# ---------------------------------------------------------------------------

@vision_admin_bp.route("/field/admin/vision/sessions", methods=["GET"])
@admin_required
def vision_admin_sessions_list():
    """Liste les sessions operateur Vision. Filtres : tablet_uid (historique
    d'une tablette), event/year, employee_number. Tri par started_at desc."""
    db = _db()
    _sweep_inactive_sessions(db)

    query = {}
    tablet_uid = request.args.get("tablet_uid")
    event = request.args.get("event")
    year = request.args.get("year")
    emp = request.args.get("employee_number")
    if tablet_uid:
        query["tablet_uid"] = tablet_uid
    if event:
        query["event"] = event
    if year:
        query["year"] = str(year)
    if emp:
        query["employee_number"] = emp

    try:
        limit = max(1, min(int(request.args.get("limit", "200")), 500))
    except (TypeError, ValueError):
        limit = 200

    sessions = [
        _pub_session(s)
        for s in db["vision_sessions"].find(query).sort("started_at", -1).limit(limit)
    ]
    return jsonify({"sessions": sessions})
