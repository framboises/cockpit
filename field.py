# field.py - Blueprint Flask pour l'application terrain des patrouilles (tablettes)
#
# Systeme completement isole de l'auth TITAN/JWT :
#   - Les tablettes sont des "appareils", pas des utilisateurs
#   - Auth par token opaque (256 bits random, stocke SHA-256 en base)
#   - Cookie dedie "field_token" avec Path=/field (ne fuit pas ailleurs)
#   - Pairing par code a 6 chiffres genere dans l'admin cockpit
#   - Une tablette appartient a un beacon_group + event/year donnes
#
# Collections MongoDB :
#   - field_pairings  : codes de pairing valides (TTL 15 min)
#   - field_devices   : tablettes enrolees (une ligne par tablette active)
#   - field_messages  : messages/instructions envoyes aux tablettes (inbox)

from flask import Blueprint, jsonify, request, render_template, make_response, redirect
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient
from bson.objectid import ObjectId
import os
import hashlib
import secrets
import logging
import re
from functools import wraps

try:
    from zoneinfo import ZoneInfo
    TZ_LOCAL = ZoneInfo("Europe/Paris")
except ImportError:
    import dateutil.tz
    TZ_LOCAL = dateutil.tz.gettz("Europe/Paris")


field_bp = Blueprint("field", __name__)
logger = logging.getLogger("field")

# ---------------------------------------------------------------------------
# Config / constantes
# ---------------------------------------------------------------------------

PAIRING_CODE_TTL_SECONDS = 15 * 60          # code valable 15 min
DEVICE_TOKEN_TTL_SECONDS = 30 * 24 * 3600    # cookie valable 30 jours
INBOX_MESSAGE_TTL_SECONDS = 7 * 24 * 3600    # 7 jours dans l'inbox puis purge
PAIR_RATE_LIMIT_WINDOW = 60                  # fenetre rate-limit en secondes
PAIR_RATE_LIMIT_MAX = 10                     # max 10 tentatives / ip / fenetre

FIELD_COOKIE_NAME = "field_token"
FIELD_COOKIE_PATH = "/field"


def _now():
    return datetime.now(timezone.utc)


def _now_local():
    return datetime.now(TZ_LOCAL)


# ---------------------------------------------------------------------------
# MongoDB (lazy)
# ---------------------------------------------------------------------------

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_mongo_client = None
_mongo_db = None


def _get_mongo_db():
    global _mongo_client, _mongo_db
    if _mongo_db is None:
        _mongo_client = MongoClient(MONGO_URI)
        _TITAN_ENV = os.getenv("TITAN_ENV", "dev").strip().lower()
        _IS_PROD = _TITAN_ENV in {"prod", "production"}
        _mongo_db = _mongo_client["titan" if _IS_PROD else "titan_dev"]
        _ensure_indexes(_mongo_db)
    return _mongo_db


_INDEXES_READY = False


def _ensure_indexes(db):
    global _INDEXES_READY
    if _INDEXES_READY:
        return
    try:
        # field_pairings : code unique + TTL via expiresAt
        db["field_pairings"].create_index("code", unique=True)
        db["field_pairings"].create_index("expiresAt", expireAfterSeconds=0)

        # field_devices : token_hash unique, event/year/beacon_group pour filtrage
        db["field_devices"].create_index("token_hash", unique=True)
        db["field_devices"].create_index([("event", 1), ("year", 1)])
        db["field_devices"].create_index([("event", 1), ("year", 1), ("beacon_group_id", 1)])
        db["field_devices"].create_index([("event", 1), ("year", 1), ("name", 1)])

        # field_messages : inbox par device_id + TTL 7 jours
        db["field_messages"].create_index([("device_id", 1), ("createdAt", -1)])
        db["field_messages"].create_index("expiresAt", expireAfterSeconds=0)
    except Exception as e:
        logger.warning("field: index creation failed: %s", e)
    _INDEXES_READY = True


# ---------------------------------------------------------------------------
# Helpers token
# ---------------------------------------------------------------------------

def _generate_token():
    """Token opaque 256 bits (hex, 64 chars)."""
    return secrets.token_hex(32)


def _hash_token(token):
    """SHA-256 du token en hex (pas de sel, on compare a egalite)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _generate_pairing_code():
    """Code a 6 chiffres, pas de ambiguite visuelle."""
    # secrets.randbelow pour eviter le biais
    return "".join(str(secrets.randbelow(10)) for _ in range(6))


# ---------------------------------------------------------------------------
# Rate limit en memoire pour /field/pair
# ---------------------------------------------------------------------------

_pair_rate_log = {}  # ip -> [timestamp, ...]


def _rate_limit_pair(ip):
    """Retourne True si la requete est autorisee, False si bloquee."""
    now = _now().timestamp()
    window_start = now - PAIR_RATE_LIMIT_WINDOW
    hist = [t for t in _pair_rate_log.get(ip, []) if t > window_start]
    if len(hist) >= PAIR_RATE_LIMIT_MAX:
        _pair_rate_log[ip] = hist
        return False
    hist.append(now)
    _pair_rate_log[ip] = hist
    # nettoyage opportuniste
    if len(_pair_rate_log) > 1000:
        to_del = [k for k, v in _pair_rate_log.items() if not any(t > window_start for t in v)]
        for k in to_del:
            _pair_rate_log.pop(k, None)
    return True


def _client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "0.0.0.0").split(",")[0].strip()


# ---------------------------------------------------------------------------
# Decorateur : auth tablette par cookie field_token
# ---------------------------------------------------------------------------

def _wants_json():
    """True si le client prefere JSON a HTML (XHR/fetch) ou si on est en POST."""
    if request.method != "GET":
        return True
    accept = request.accept_mimetypes
    # Si le client ne demande pas explicitement HTML, on considere que c'est XHR
    if not accept or accept.best == "*/*":
        return False
    return accept.quality("application/json") > accept.quality("text/html")


def field_token_required(f):
    """Decorateur : exige un cookie field_token valide pointant sur une tablette active."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.cookies.get(FIELD_COOKIE_NAME)
        if not token:
            if _wants_json():
                return jsonify({"error": "not_paired"}), 401
            return redirect("/field/pair")

        db = _get_mongo_db()
        device = db["field_devices"].find_one({"token_hash": _hash_token(token)})
        if not device or device.get("revoked"):
            if _wants_json():
                return jsonify({"error": "device_revoked"}), 401
            resp = make_response(redirect("/field/pair"))
            resp.delete_cookie(FIELD_COOKIE_NAME, path=FIELD_COOKIE_PATH)
            return resp

        # Mettre a jour last_seen (best-effort)
        try:
            db["field_devices"].update_one(
                {"_id": device["_id"]},
                {"$set": {
                    "last_seen": _now(),
                    "last_ip": _client_ip(),
                    "last_ua": (request.headers.get("User-Agent") or "")[:200],
                }},
            )
        except Exception:
            pass

        request.device = device
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Helpers de publication : serialiser les documents pour JSON
# ---------------------------------------------------------------------------

def _pub_device(device):
    if not device:
        return None
    return {
        "id": str(device.get("_id")),
        "name": device.get("name"),
        "event": device.get("event"),
        "year": device.get("year"),
        "beacon_group_id": device.get("beacon_group_id"),
        "created_at": _iso(device.get("createdAt")),
        "last_seen": _iso(device.get("last_seen")),
        "revoked": bool(device.get("revoked")),
    }


def _pub_message(msg):
    if not msg:
        return None
    return {
        "id": str(msg.get("_id")),
        "type": msg.get("type"),
        "title": msg.get("title"),
        "body": msg.get("body"),
        "payload": msg.get("payload") or {},
        "priority": msg.get("priority", "normal"),
        "from": msg.get("from"),
        "created_at": _iso(msg.get("createdAt")),
        "ack_at": _iso(msg.get("ack_at")),
    }


def _iso(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return None


# ---------------------------------------------------------------------------
# Routes tablette : UI
# ---------------------------------------------------------------------------

@field_bp.route("/field", methods=["GET"])
def field_index():
    """Page principale de l'app terrain. Redirige vers /field/pair si pas de token."""
    token = request.cookies.get(FIELD_COOKIE_NAME)
    if not token:
        return redirect("/field/pair")
    db = _get_mongo_db()
    device = db["field_devices"].find_one({"token_hash": _hash_token(token)})
    if not device or device.get("revoked"):
        resp = make_response(redirect("/field/pair"))
        resp.delete_cookie(FIELD_COOKIE_NAME, path=FIELD_COOKIE_PATH)
        return resp
    # Template sera cree dans le commit 2
    try:
        return render_template("field.html", device=_pub_device(device))
    except Exception:
        # Fallback temporaire : template pas encore cree
        return (
            "<!doctype html><meta charset='utf-8'><title>Field</title>"
            "<h1>Field patrol</h1>"
            f"<p>Tablette : <b>{device.get('name', '?')}</b></p>"
            f"<p>Evenement : {device.get('event', '?')} / {device.get('year', '?')}</p>"
            "<p>Template field.html pas encore cree (commit 2).</p>"
        )


@field_bp.route("/field/pair", methods=["GET"])
def field_pair_view():
    """Ecran de saisie du code de pairing."""
    # Si deja paire, renvoyer vers /field
    token = request.cookies.get(FIELD_COOKIE_NAME)
    if token:
        db = _get_mongo_db()
        device = db["field_devices"].find_one({"token_hash": _hash_token(token)})
        if device and not device.get("revoked"):
            return redirect("/field")
    return render_template("field_pair.html")


@field_bp.route("/field/pair", methods=["POST"])
def field_pair_submit():
    """Valider un code de pairing et poser le cookie field_token."""
    ip = _client_ip()
    if not _rate_limit_pair(ip):
        return jsonify({"ok": False, "error": "rate_limited"}), 429

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    code = str(data.get("code", "")).strip()
    if not re.fullmatch(r"\d{6}", code):
        return jsonify({"ok": False, "error": "invalid_code_format"}), 400

    db = _get_mongo_db()
    pairing = db["field_pairings"].find_one({"code": code})
    if not pairing:
        return jsonify({"ok": False, "error": "unknown_code"}), 404

    exp = pairing.get("expiresAt")
    if isinstance(exp, datetime):
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < _now():
            db["field_pairings"].delete_one({"_id": pairing["_id"]})
            return jsonify({"ok": False, "error": "expired_code"}), 410

    if pairing.get("used"):
        return jsonify({"ok": False, "error": "code_already_used"}), 409

    # Generer le token tablette
    token = _generate_token()
    token_hash = _hash_token(token)

    device_doc = {
        "name": pairing.get("name"),
        "event": pairing.get("event"),
        "year": pairing.get("year"),
        "beacon_group_id": pairing.get("beacon_group_id"),
        "token_hash": token_hash,
        "createdAt": _now(),
        "paired_at": _now(),
        "paired_ip": ip,
        "paired_ua": (request.headers.get("User-Agent") or "")[:200],
        "last_seen": _now(),
        "last_ip": ip,
        "revoked": False,
        "notes": pairing.get("notes", ""),
    }
    ins = db["field_devices"].insert_one(device_doc)
    device_doc["_id"] = ins.inserted_id

    # Marquer le code consomme (et le supprimer immediatement, usage unique)
    db["field_pairings"].delete_one({"_id": pairing["_id"]})

    resp = make_response(jsonify({
        "ok": True,
        "device": _pub_device(device_doc),
        "redirect": "/field",
    }))
    # Cookie HttpOnly, SameSite=Lax, Secure en prod
    is_prod = os.getenv("TITAN_ENV", "dev").strip().lower() in {"prod", "production"}
    resp.set_cookie(
        FIELD_COOKIE_NAME,
        token,
        max_age=DEVICE_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=is_prod,
        samesite="Lax",
        path=FIELD_COOKIE_PATH,
    )
    return resp


@field_bp.route("/field/logout", methods=["GET", "POST"])
def field_logout():
    """Effacer le cookie. N'invalide pas le device en base (l'admin doit revoquer)."""
    resp = make_response(redirect("/field/pair"))
    resp.delete_cookie(FIELD_COOKIE_NAME, path=FIELD_COOKIE_PATH)
    return resp


# ---------------------------------------------------------------------------
# Routes tablette : API (cookie field_token obligatoire)
# ---------------------------------------------------------------------------

@field_bp.route("/field/me", methods=["GET"])
@field_token_required
def field_me():
    """Infos sur la tablette courante."""
    return jsonify({
        "ok": True,
        "device": _pub_device(request.device),
    })


@field_bp.route("/field/position", methods=["POST"])
@field_token_required
def field_position():
    """Recevoir une position GPS de la tablette (ecrit dans field_devices.last_position)."""
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("lat"))
        lng = float(data.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_coords"}), 400

    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({"ok": False, "error": "out_of_range"}), 400

    accuracy = data.get("accuracy")
    try:
        accuracy = float(accuracy) if accuracy is not None else None
    except (TypeError, ValueError):
        accuracy = None

    speed = data.get("speed")
    try:
        speed = float(speed) if speed is not None else None
    except (TypeError, ValueError):
        speed = None

    heading = data.get("heading")
    try:
        heading = float(heading) if heading is not None else None
    except (TypeError, ValueError):
        heading = None

    battery = data.get("battery")
    try:
        battery = float(battery) if battery is not None else None
    except (TypeError, ValueError):
        battery = None

    pos_doc = {
        "lat": lat,
        "lng": lng,
        "accuracy": accuracy,
        "speed": speed,
        "heading": heading,
        "battery": battery,
        "ts": _now(),
    }

    db = _get_mongo_db()
    db["field_devices"].update_one(
        {"_id": request.device["_id"]},
        {"$set": {
            "last_position": pos_doc,
            "last_seen": _now(),
        }},
    )
    return jsonify({"ok": True})


@field_bp.route("/field/inbox", methods=["GET"])
@field_token_required
def field_inbox():
    """Retourne les messages non acquittes pour la tablette courante."""
    db = _get_mongo_db()
    device_id = request.device["_id"]

    # On retourne aussi les messages acquittes des dernieres 24h pour affichage historique optionnel
    since = request.args.get("since")
    query = {"device_id": device_id}
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            query["createdAt"] = {"$gt": since_dt}
        except Exception:
            pass

    cursor = db["field_messages"].find(query).sort("createdAt", 1).limit(200)
    messages = [_pub_message(m) for m in cursor]
    return jsonify({"ok": True, "messages": messages, "now": _iso(_now())})


@field_bp.route("/field/ack/<msg_id>", methods=["POST"])
@field_token_required
def field_ack(msg_id):
    """Marquer un message comme lu (ack)."""
    try:
        oid = ObjectId(msg_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400

    db = _get_mongo_db()
    res = db["field_messages"].update_one(
        {"_id": oid, "device_id": request.device["_id"]},
        {"$set": {"ack_at": _now()}},
    )
    if res.matched_count == 0:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})
