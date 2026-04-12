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

from flask import Blueprint, jsonify, request, render_template, make_response, redirect, send_from_directory, abort
from werkzeug.utils import safe_join, secure_filename
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

# Photos
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FIELD_PHOTOS_DIR = os.path.join(SCRIPT_DIR, "uploads", "field_photos")
FIELD_PHOTO_MAX_SIZE = 10 * 1024 * 1024   # 10 Mo par photo
FIELD_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic"}


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
        if not device:
            # Token inconnu : on purge et on renvoie sur le pairing
            if _wants_json():
                return jsonify({"error": "device_revoked"}), 401
            resp = make_response(redirect("/field/pair"))
            resp.delete_cookie(FIELD_COOKIE_NAME, path=FIELD_COOKIE_PATH)
            return resp
        if device.get("revoked"):
            # Tablette explicitement revoquee : on garde le cookie pour
            # qu'elle puisse etre re-autorisee sans nouveau code de pairing.
            if _wants_json():
                return jsonify({"error": "device_revoked"}), 401
            return redirect("/field/denied")

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
        "status": device.get("status") or "patrouille",
        "status_since": _iso(device.get("status_since")),
        "active_fiche_id": device.get("active_fiche_id"),
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

@field_bp.route("/field/manifest.webmanifest", methods=["GET"])
def field_manifest():
    """PWA manifest. Pas d'auth : le navigateur le charge avant le login."""
    manifest = {
        "name": "COCKPIT Field",
        "short_name": "Field",
        "description": "Application terrain pour tablettes patrouille",
        "start_url": "/field",
        "scope": "/field",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "lang": "fr-FR",
        "icons": [
            {
                "src": "/static/img/field-icon.svg",
                "sizes": "192x192 512x512 any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            },
        ],
    }
    resp = jsonify(manifest)
    resp.headers["Content-Type"] = "application/manifest+json"
    return resp


@field_bp.route("/field/sw.js", methods=["GET"])
def field_service_worker():
    """Service worker : sert le fichier statique avec le bon scope. On pourrait
    pointer /static/js/field-sw.js directement, mais pour garder Service-Worker-Allowed
    = /field on passe par la route (sinon le navigateur restreint le scope au path
    du fichier statique)."""
    from flask import current_app, send_from_directory
    static_dir = os.path.join(current_app.root_path, "static", "js")
    resp = make_response(send_from_directory(static_dir, "field-sw.js"))
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/field"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@field_bp.route("/field", methods=["GET"])
def field_index():
    """Page principale de l'app terrain. Redirige vers /field/pair si pas de token."""
    token = request.cookies.get(FIELD_COOKIE_NAME)
    if not token:
        return redirect("/field/pair")
    db = _get_mongo_db()
    device = db["field_devices"].find_one({"token_hash": _hash_token(token)})
    if not device:
        # Token inconnu : on purge et on renvoie au pairing
        resp = make_response(redirect("/field/pair"))
        resp.delete_cookie(FIELD_COOKIE_NAME, path=FIELD_COOKIE_PATH)
        return resp
    if device.get("revoked"):
        return redirect("/field/denied")
    return render_template("field.html", device=_pub_device(device))


@field_bp.route("/field/denied", methods=["GET"])
def field_denied_view():
    """Page affichee quand la tablette a ete revoquee. Le cookie est conserve
    afin qu'une re-autorisation cote admin permette de recuperer la session
    sans nouveau pairing. La page se rafraichit periodiquement pour detecter
    automatiquement la restauration."""
    token = request.cookies.get(FIELD_COOKIE_NAME)
    if not token:
        return redirect("/field/pair")
    db = _get_mongo_db()
    device = db["field_devices"].find_one({"token_hash": _hash_token(token)})
    if not device:
        resp = make_response(redirect("/field/pair"))
        resp.delete_cookie(FIELD_COOKIE_NAME, path=FIELD_COOKIE_PATH)
        return resp
    if not device.get("revoked"):
        # Plus revoquee, retour direct
        return redirect("/field")
    return render_template("field_denied.html", device=_pub_device(device))


@field_bp.route("/field/denied/check", methods=["GET"])
def field_denied_check():
    """Endpoint JSON utilise par la page denied pour detecter une restauration."""
    token = request.cookies.get(FIELD_COOKIE_NAME)
    if not token:
        return jsonify({"status": "no_token"}), 401
    db = _get_mongo_db()
    device = db["field_devices"].find_one({"token_hash": _hash_token(token)})
    if not device:
        return jsonify({"status": "unknown"}), 401
    if device.get("revoked"):
        return jsonify({"status": "revoked"})
    return jsonify({"status": "active"})


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

    # -- Historique des positions (meme collection que les balises anoloc)
    try:
        db["anoloc_positions"].insert_one({
            "device_id": "field:" + str(request.device["_id"]),
            "beacon_group": request.device.get("beacon_group_id", ""),
            "label": request.device.get("name", "?"),
            "lat": lat,
            "lng": lng,
            "speed": speed or 0,
            "heading": heading or 0,
            "status": "running",
            "battery_pct": int(round(battery)) if battery is not None else None,
            "gps_fix": 1,
            "collected_at": _now(),
        })
    except Exception:
        pass  # non-bloquant
    # -- Auto-detection de proximite : si la tablette a une fiche assignee
    #    avec GPS et qu'on est a <10m, passer le statut a "sur_place"
    try:
        device_fresh = db["field_devices"].find_one({"_id": request.device["_id"]})
        cur_status = (device_fresh or {}).get("status") or "patrouille"
        fiche_id = (device_fresh or {}).get("active_fiche_id")
        if fiche_id and cur_status in ("intervention", "patrouille"):
            fiche = db["pcorg"].find_one({"_id": fiche_id}, {"gps": 1, "status_code": 1})
            if fiche and fiche.get("status_code") != 10:
                fiche_gps = fiche.get("gps") or {}
                fcoords = fiche_gps.get("coordinates") or []
                if len(fcoords) >= 2:
                    from math import radians, sin, cos, asin, sqrt
                    def _hav(a_lat, a_lng, b_lat, b_lng):
                        R = 6371000
                        dl = radians(b_lat - a_lat)
                        do = radians(b_lng - a_lng)
                        s = sin(dl / 2) ** 2 + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(do / 2) ** 2
                        return 2 * R * asin(min(1, sqrt(s)))
                    dist = _hav(lat, lng, fcoords[1], fcoords[0])
                    if dist <= 10:
                        db["field_devices"].update_one(
                            {"_id": request.device["_id"], "status": {"$in": ["intervention", "patrouille"]}},
                            {
                                "$set": {"status": "sur_place", "status_since": _now()},
                                "$push": {"status_history": {
                                    "status": "sur_place", "ts": _now(),
                                    "trigger": "auto_proximity",
                                    "fiche_id": fiche_id,
                                }},
                            },
                        )
    except Exception:
        pass  # non-bloquant

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Statut de la patrouille
# ---------------------------------------------------------------------------

VALID_STATUSES = {"patrouille", "intervention", "sur_place", "pause"}


@field_bp.route("/field/status", methods=["GET"])
@field_token_required
def field_status_get():
    """Retourne le statut courant de la tablette."""
    db = _get_mongo_db()
    device = db["field_devices"].find_one({"_id": request.device["_id"]})
    return jsonify({
        "ok": True,
        "status": (device or {}).get("status") or "patrouille",
        "status_since": _iso((device or {}).get("status_since")),
        "active_fiche_id": (device or {}).get("active_fiche_id"),
    })


@field_bp.route("/field/status", methods=["POST"])
@field_token_required
def field_status_set():
    """Met a jour le statut de la tablette.
    Statuts : patrouille | intervention | sur_place | pause
    Si statut == 'patrouille' et qu'il y avait une active_fiche_id auto-creee,
    on peut optionnellement la cloturer via close_active_fiche=true."""
    data = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip().lower()
    if new_status not in VALID_STATUSES:
        return jsonify({"ok": False, "error": "invalid_status"}), 400

    device = request.device
    db = _get_mongo_db()
    now = _now()

    update = {
        "$set": {
            "status": new_status,
            "status_since": now,
        },
        "$push": {
            "status_history": {
                "status": new_status,
                "ts": now,
                "trigger": "manual",
            },
        },
    }

    # Si retour a patrouille, on desassocie la fiche active
    if new_status == "patrouille":
        update["$set"]["active_fiche_id"] = None

    db["field_devices"].update_one({"_id": device["_id"]}, update)
    return jsonify({"ok": True, "status": new_status})


@field_bp.route("/field/create-fiche", methods=["POST"])
@field_token_required
def field_create_fiche():
    """Cree une fiche d'intervention PCO depuis la tablette.
    La tablette passe automatiquement en statut 'intervention'.
    Champs : category (obligatoire), text (obligatoire), niveau_urgence (optionnel)."""
    import uuid as _uuid

    data = request.get_json(silent=True) or {}
    category = (data.get("category") or "").strip()
    text = (data.get("text") or "").strip()
    niveau_urgence = (data.get("niveau_urgence") or "").strip() or None

    if not category or not category.startswith("PCO."):
        return jsonify({"ok": False, "error": "invalid_category"}), 400
    if not text:
        return jsonify({"ok": False, "error": "empty_text"}), 400
    if niveau_urgence and niveau_urgence not in {"EU", "UA", "UR", "IMP"}:
        return jsonify({"ok": False, "error": "invalid_urgency"}), 400

    device = request.device
    name = device.get("name") or "?"
    event = device.get("event")
    year = device.get("year")

    db = _get_mongo_db()
    now = _now()
    now_local = _now_local()
    ts_str = now_local.isoformat()

    # GPS courant
    lat = data.get("lat")
    lng = data.get("lng")
    gps = None
    if lat is not None and lng is not None:
        try:
            gps = {"type": "Point", "coordinates": [float(lng), float(lat)]}
        except (ValueError, TypeError):
            pass

    # Carroyage optionnel
    carroye = (data.get("carroye") or "").strip()

    try:
        year_int = int(year) if year is not None else None
    except (TypeError, ValueError):
        year_int = year

    seed = "{}|{}|{}|{}|{}|field:{}".format(
        event or "", year or "", ts_str, category, text, str(device.get("_id"))
    )
    fiche_id = str(_uuid.uuid5(_uuid.NAMESPACE_URL, seed))

    content_category = {"patrouille": name, "field_created": True}
    if carroye:
        content_category["carroye"] = carroye

    fiche_doc = {
        "_id": fiche_id,
        "event": event,
        "year": year_int,
        "ts": now,
        "timestamp_iso": ts_str,
        "close_ts": None,
        "close_iso": None,
        "category": category,
        "source": category,
        "text": text,
        "text_full": text,
        "comment": "",
        "comment_history": [],
        "operator": "Tablette " + name,
        "operator_id_create": "field:" + str(device.get("_id")),
        "operator_close": None,
        "operator_id_close": None,
        "status_code": 0,
        "severity": 0,
        "niveau_urgence": niveau_urgence,
        "is_incident": False,
        "area": None,
        "gps": gps,
        "group": None,
        "content_category": content_category,
        "extracted": {"phones": None, "plates": None},
        "tags": ["field-created"],
        "synced_at": None,
        "sql_id": None,
        "guid": None,
        "server": "COCKPIT",
        "bounce_rev": 1,
    }

    db["pcorg"].insert_one(fiche_doc)

    # Passer la tablette en statut 'intervention' et lier la fiche
    db["field_devices"].update_one(
        {"_id": device["_id"]},
        {
            "$set": {
                "status": "intervention",
                "status_since": now,
                "active_fiche_id": fiche_id,
            },
            "$push": {
                "status_history": {
                    "status": "intervention",
                    "ts": now,
                    "trigger": "create_fiche",
                    "fiche_id": fiche_id,
                },
            },
        },
    )

    return jsonify({"ok": True, "id": fiche_id})


@field_bp.route("/field/my-fiches/<fiche_id>/close", methods=["POST"])
@field_token_required
def field_my_fiche_close(fiche_id):
    """Cloture une fiche creee par la tablette (field_created=true).
    Les fiches creees par cockpit ne peuvent PAS etre cloturees depuis field."""
    device = request.device
    name = device.get("name")
    if not name:
        return jsonify({"ok": False, "error": "unnamed_device"}), 400

    db = _get_mongo_db()
    fiche = db["pcorg"].find_one({"_id": fiche_id})
    if not fiche:
        return jsonify({"ok": False, "error": "not_found"}), 404

    cc = fiche.get("content_category") or {}
    if (cc.get("patrouille") or "") != name:
        return jsonify({"ok": False, "error": "not_assigned"}), 403
    if not cc.get("field_created") and not cc.get("field_sos"):
        return jsonify({"ok": False, "error": "cannot_close_cockpit_fiche"}), 403
    if fiche.get("status_code") == 10:
        return jsonify({"ok": False, "error": "already_closed"}), 400

    now = _now()
    now_local = _now_local()
    ts_fmt = now_local.strftime("%d/%m/%Y %H:%M:%S")
    operator = "field:" + name

    comment_line = "{} , {}\n Statut: En cours -> Termine\n".format(ts_fmt, operator)
    history_entry = {
        "ts": now.isoformat(),
        "operator": operator,
        "text": "Statut: En cours -> Termine",
    }

    old_comment = fiche.get("comment") or ""
    new_comment = old_comment + comment_line if old_comment else comment_line

    db["pcorg"].update_one(
        {"_id": fiche_id},
        {
            "$set": {
                "status_code": 10,
                "close_ts": now,
                "close_iso": now_local.isoformat(),
                "operator_close": operator,
                "operator_id_close": "field:" + str(device.get("_id")),
                "comment": new_comment,
            },
            "$push": {"comment_history": history_entry},
            "$inc": {"bounce_rev": 1},
        },
    )

    # Retour en patrouille si c'etait la fiche active
    cur = db["field_devices"].find_one({"_id": device["_id"]})
    if cur and cur.get("active_fiche_id") == fiche_id:
        db["field_devices"].update_one(
            {"_id": device["_id"]},
            {
                "$set": {
                    "status": "patrouille",
                    "status_since": now,
                    "active_fiche_id": None,
                },
                "$push": {
                    "status_history": {
                        "status": "patrouille",
                        "ts": now,
                        "trigger": "close_fiche",
                        "fiche_id": fiche_id,
                    },
                },
            },
        )

    return jsonify({"ok": True})


@field_bp.route("/field/pco-categories", methods=["GET"])
@field_token_required
def field_pco_categories():
    """Liste les categories PCO disponibles pour la creation de fiches terrain."""
    return jsonify({
        "categories": [
            {"id": "PCO.Secours", "label": "Secours", "icon": "medical_services"},
            {"id": "PCO.Securite", "label": "Securite", "icon": "security"},
            {"id": "PCO.Technique", "label": "Technique", "icon": "build"},
            {"id": "PCO.Flux", "label": "Flux", "icon": "directions_car"},
        ]
    })


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


# =============================================================================
# PARTIE ADMIN : routes /field/admin/*
# =============================================================================
#
# Ces routes sont consommees depuis le panneau admin cockpit (edit.html).
# Elles utilisent l'auth cockpit (JWT) et exigent le role admin.
# Les routes field tablette (au-dessus) utilisent l'auth field_token.


def admin_required(f):
    """Decorateur : exige un JWT cockpit avec role admin."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        from app import (
            JWT_SECRET, JWT_ALGORITHM, CODING, APP_KEY,
            SUPER_ADMIN_ROLE, ROLE_HIERARCHY,
        )
        import jwt as pyjwt

        if CODING:
            # Simulation : role admin par defaut
            request.admin_user = {
                "email": "dev@cockpit",
                "app_role": "admin",
                "is_super_admin": False,
            }
            return f(*args, **kwargs)

        token = request.cookies.get("access_token")
        if not token:
            return jsonify({"error": "auth_required"}), 401
        try:
            payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
            return jsonify({"error": "invalid_token"}), 401

        is_super_admin = SUPER_ADMIN_ROLE in (payload.get("global_roles") or [])
        app_role = (payload.get("roles_by_app") or {}).get(APP_KEY)
        if not is_super_admin and app_role != "admin":
            return jsonify({"error": "admin_required"}), 403

        payload["app_role"] = "admin" if is_super_admin else app_role
        payload["is_super_admin"] = is_super_admin
        request.admin_user = payload
        return f(*args, **kwargs)
    return wrapper


def _pub_pairing(p):
    if not p:
        return None
    return {
        "code": p.get("code"),
        "name": p.get("name"),
        "event": p.get("event"),
        "year": p.get("year"),
        "beacon_group_id": p.get("beacon_group_id"),
        "notes": p.get("notes", ""),
        "createdAt": _iso(p.get("createdAt")),
        "expiresAt": _iso(p.get("expiresAt")),
        "created_by": p.get("created_by"),
    }


def _pub_device_admin(d):
    """Version admin : inclut last_position et derniers meta."""
    if not d:
        return None
    base = _pub_device(d)
    base.update({
        "last_position": _pub_position(d.get("last_position")),
        "last_ip": d.get("last_ip"),
        "last_ua": d.get("last_ua"),
        "notes": d.get("notes", ""),
    })
    return base


def _pub_position(pos):
    if not pos:
        return None
    return {
        "lat": pos.get("lat"),
        "lng": pos.get("lng"),
        "accuracy": pos.get("accuracy"),
        "speed": pos.get("speed"),
        "heading": pos.get("heading"),
        "battery": pos.get("battery"),
        "ts": _iso(pos.get("ts")),
    }


def _label_conflict(db, name, event, year, exclude_device_id=None):
    """True si le nom collisionne avec une balise anoloc (device_labels) ou une tablette
    existante dans le meme event/year. Requis : les fiches PCORG matchent sur cc.patrouille
    (label) et l'unicite evite les ambiguites."""
    if not name:
        return False
    # Collision avec une balise Anoloc (device_labels.*)
    config = db["anoloc_config"].find_one({"_id": "global"}) or {}
    for grp in config.get("beacon_groups", []) or []:
        dev_labels = grp.get("device_labels") or {}
        for v in dev_labels.values():
            if str(v).strip() == name.strip():
                return True
    # Collision avec une autre tablette
    query = {"name": name, "event": event, "year": year, "revoked": {"$ne": True}}
    if exclude_device_id is not None:
        query["_id"] = {"$ne": exclude_device_id}
    if db["field_devices"].count_documents(query) > 0:
        return True
    return False


# ---------------------------------------------------------------------------
# Admin : beacon groups (helper pour dropdown)
# ---------------------------------------------------------------------------

@field_bp.route("/field/admin/beacon-groups", methods=["GET"])
@admin_required
def field_admin_beacon_groups():
    """Retourne TOUS les beacon_groups (meme inactifs) pour remplir un dropdown
    admin. Le drapeau `disabled` permet a l'UI de marquer visuellement les
    groupes desactives, sans les masquer (sinon l'admin ne comprend pas
    pourquoi son groupe n'apparait pas)."""
    db = _get_mongo_db()
    config = db["anoloc_config"].find_one({"_id": "global"}) or {}
    groups = []
    for g in config.get("beacon_groups", []) or []:
        gid = g.get("id")
        if not gid:
            continue
        groups.append({
            "id": gid,
            "label": g.get("label") or gid,
            "color": g.get("color") or "#6366f1",
            "icon": g.get("icon") or "location_on",
            "pco_category": g.get("pco_category"),
            "disabled": g.get("enabled") is False,
        })
    resp = jsonify({"groups": groups})
    # Eviter le cache HTTP cote navigateur (sinon une mise a jour des groupes
    # n'apparait pas tant qu'on n'a pas hard-refresh).
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# ---------------------------------------------------------------------------
# Admin : pairings (codes en cours)
# ---------------------------------------------------------------------------

@field_bp.route("/field/admin/pairings", methods=["GET"])
@admin_required
def field_admin_pairings_list():
    """Liste les codes de pairing actifs (filtre optionnel par event/year)."""
    db = _get_mongo_db()
    query = {}
    event = request.args.get("event")
    year = request.args.get("year")
    if event:
        query["event"] = event
    if year:
        query["year"] = str(year)
    pairings = [_pub_pairing(p) for p in db["field_pairings"].find(query).sort("createdAt", -1)]
    return jsonify({"pairings": pairings})


@field_bp.route("/field/admin/pairings", methods=["POST"])
@admin_required
def field_admin_pairings_create():
    """Cree un nouveau code de pairing (unique). Payload : name, event, year,
    beacon_group_id, notes?"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    event = (data.get("event") or "").strip()
    year = str(data.get("year") or "").strip()
    beacon_group_id = (data.get("beacon_group_id") or "").strip()
    notes = (data.get("notes") or "").strip()

    if not name:
        return jsonify({"ok": False, "error": "missing_name"}), 400
    if not event or not year:
        return jsonify({"ok": False, "error": "missing_event_year"}), 400
    if not beacon_group_id:
        return jsonify({"ok": False, "error": "missing_beacon_group"}), 400

    db = _get_mongo_db()

    # Le beacon_group doit exister (l'admin peut associer une tablette a un
    # groupe meme inactif - le drapeau "enabled" cote anoloc concerne
    # l'affichage carte/widgets, pas l'eligibilite des tablettes).
    config = db["anoloc_config"].find_one({"_id": "global"}) or {}
    grp = next((g for g in config.get("beacon_groups", []) or [] if g.get("id") == beacon_group_id), None)
    if not grp:
        return jsonify({"ok": False, "error": "unknown_beacon_group"}), 400

    # Le nom ne doit pas collisionner avec une balise anoloc ou une autre tablette
    if _label_conflict(db, name, event, year):
        return jsonify({"ok": False, "error": "name_conflict"}), 409

    # Generer un code unique (retry jusqu'a 5 fois)
    code = None
    for _ in range(5):
        candidate = _generate_pairing_code()
        if db["field_pairings"].find_one({"code": candidate}) is None:
            code = candidate
            break
    if code is None:
        return jsonify({"ok": False, "error": "code_generation_failed"}), 500

    doc = {
        "code": code,
        "name": name,
        "event": event,
        "year": year,
        "beacon_group_id": beacon_group_id,
        "notes": notes,
        "createdAt": _now(),
        "expiresAt": _now() + timedelta(seconds=PAIRING_CODE_TTL_SECONDS),
        "created_by": (request.admin_user or {}).get("email", "?"),
    }
    db["field_pairings"].insert_one(doc)
    return jsonify({"ok": True, "pairing": _pub_pairing(doc)}), 201


@field_bp.route("/field/admin/pairings/<code>", methods=["DELETE"])
@admin_required
def field_admin_pairings_delete(code):
    """Supprime un code de pairing (non utilise)."""
    db = _get_mongo_db()
    res = db["field_pairings"].delete_one({"code": code})
    if res.deleted_count == 0:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Admin : devices (tablettes enrolees)
# ---------------------------------------------------------------------------

@field_bp.route("/field/admin/devices", methods=["GET"])
@admin_required
def field_admin_devices_list():
    """Liste les tablettes enrolees (filtre event/year/beacon_group)."""
    db = _get_mongo_db()
    query = {}
    event = request.args.get("event")
    year = request.args.get("year")
    group = request.args.get("beacon_group_id")
    # Par defaut on inclut les tablettes revoquees pour pouvoir les
    # re-autoriser. Le client peut filtrer avec include_revoked=false.
    include_revoked = request.args.get("include_revoked", "true").lower() in ("1", "true", "yes")
    if event:
        query["event"] = event
    if year:
        query["year"] = str(year)
    if group:
        query["beacon_group_id"] = group
    if not include_revoked:
        query["revoked"] = {"$ne": True}
    devices = [
        _pub_device_admin(d)
        for d in db["field_devices"].find(query).sort("createdAt", -1)
    ]
    return jsonify({"devices": devices})


@field_bp.route("/field/admin/devices/<device_id>/revoke", methods=["POST"])
@admin_required
def field_admin_device_revoke(device_id):
    """Revoque une tablette (le cookie deviendra invalide au prochain appel)."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    res = db["field_devices"].update_one(
        {"_id": oid},
        {"$set": {"revoked": True, "revokedAt": _now()}},
    )
    if res.matched_count == 0:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


@field_bp.route("/field/admin/devices/<device_id>/restore", methods=["POST"])
@admin_required
def field_admin_device_restore(device_id):
    """Re-autorise une tablette precedemment revoquee. Le token existant
    redevient valide et la tablette retrouvera sa session sans avoir besoin
    d'un nouveau code de pairing."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    res = db["field_devices"].update_one(
        {"_id": oid},
        {
            "$set": {"revoked": False, "restoredAt": _now()},
            "$unset": {"revokedAt": ""},
        },
    )
    if res.matched_count == 0:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


@field_bp.route("/field/admin/devices/<device_id>/rename", methods=["POST"])
@admin_required
def field_admin_device_rename(device_id):
    """Renomme une tablette (verifie la non-collision)."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"ok": False, "error": "missing_name"}), 400

    db = _get_mongo_db()
    device = db["field_devices"].find_one({"_id": oid})
    if not device:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if _label_conflict(db, new_name, device.get("event"), device.get("year"), exclude_device_id=oid):
        return jsonify({"ok": False, "error": "name_conflict"}), 409

    db["field_devices"].update_one({"_id": oid}, {"$set": {"name": new_name}})
    return jsonify({"ok": True})


@field_bp.route("/field/admin/devices/<device_id>", methods=["DELETE"])
@admin_required
def field_admin_device_delete(device_id):
    """Supprime definitivement une tablette enrolee."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    res = db["field_devices"].delete_one({"_id": oid})
    if res.deleted_count == 0:
        return jsonify({"ok": False, "error": "not_found"}), 404
    # On purge aussi les messages associes
    db["field_messages"].delete_many({"device_id": oid})
    return jsonify({"ok": True})


@field_bp.route("/field/admin/device/<device_id>/tracking", methods=["POST"])
@admin_required
def field_admin_device_tracking(device_id):
    """Active/desactive le mode tracking haute frequence sur une tablette.
    Body : {"mode": "high_freq"} ou {"mode": "normal"}.
    Chaque operateur cockpit qui suit un device peut agir independamment.
    On stocke un set de watcher_id + un TTL pour auto-expiration si
    l'operateur ferme son navigateur sans deverrouiller."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "normal")
    watcher_id = data.get("watcher_id", "")
    if mode not in ("normal", "high_freq"):
        return jsonify({"ok": False, "error": "invalid_mode"}), 400
    if not watcher_id:
        return jsonify({"ok": False, "error": "missing_watcher_id"}), 400

    db = _get_mongo_db()
    dev = db["field_devices"].find_one({"_id": oid})
    if not dev:
        return jsonify({"ok": False, "error": "not_found"}), 404

    # Watchers = dict {watcher_id: expiry_iso}
    watchers = dev.get("tracking_watchers") or {}
    if not isinstance(watchers, dict):
        watchers = {}
    now = _now()
    # Purge expired watchers (TTL 90s)
    watchers = {k: v for k, v in watchers.items()
                if isinstance(v, str) and v > now.isoformat()}

    if mode == "high_freq":
        # Set expiry 90s from now — cockpit must ping to keep alive
        expiry = (now + timedelta(seconds=90)).isoformat()
        watchers[watcher_id] = expiry
    else:
        watchers.pop(watcher_id, None)

    effective_mode = "high_freq" if len(watchers) > 0 else "normal"
    db["field_devices"].update_one(
        {"_id": oid},
        {"$set": {"tracking_mode": effective_mode, "tracking_watchers": watchers}},
    )
    return jsonify({"ok": True, "mode": effective_mode, "watchers": len(watchers)})


# ---------------------------------------------------------------------------
# Admin : messages (envoi + historique)
# ---------------------------------------------------------------------------

# Types de messages autorises. "info" et "instruction" : contenu libre.
# "route" : payload = {"waypoints": [[lat, lng], ...]} (commit 7).
# "alert" : niveau eleve (rouge).
ALLOWED_MESSAGE_TYPES = {"info", "instruction", "alert", "route"}
ALLOWED_PRIORITIES = {"normal", "high"}


def _resolve_targets(db, event, year, target):
    """Resout une specification de cible vers une liste de field_devices.
    target = {"device_ids": [...] } OR {"beacon_group_id": "..."} OR
             {"name": "..."} OR {"all": true}."""
    query = {"event": event, "year": str(year), "revoked": {"$ne": True}}

    if not isinstance(target, dict):
        return [], "invalid_target"

    if target.get("device_ids"):
        ids = []
        for s in target.get("device_ids") or []:
            try:
                ids.append(ObjectId(s))
            except Exception:
                return [], "invalid_device_id"
        if not ids:
            return [], "empty_device_ids"
        query["_id"] = {"$in": ids}
    elif target.get("beacon_group_id"):
        query["beacon_group_id"] = target.get("beacon_group_id")
    elif target.get("name"):
        query["name"] = target.get("name")
    elif target.get("all"):
        pass  # tout event/year
    else:
        return [], "missing_target"

    return list(db["field_devices"].find(query)), None


@field_bp.route("/field/admin/send", methods=["POST"])
@admin_required
def field_admin_send():
    """Envoie un message vers une ou plusieurs tablettes.

    Body JSON :
      {
        "event": "...", "year": "...",
        "target": {"device_ids": [...]} | {"beacon_group_id": "..."}
                  | {"name": "..."} | {"all": true},
        "type": "info"|"instruction"|"alert"|"route",
        "title": "...", "body": "...",
        "priority": "normal"|"high",
        "payload": { ... }   # optionnel (route waypoints, coordonnees, etc.)
      }
    """
    data = request.get_json(silent=True) or {}
    event = (data.get("event") or "").strip()
    year = str(data.get("year") or "").strip()
    if not event or not year:
        return jsonify({"ok": False, "error": "missing_event_year"}), 400

    mtype = (data.get("type") or "info").strip()
    if mtype not in ALLOWED_MESSAGE_TYPES:
        return jsonify({"ok": False, "error": "invalid_type"}), 400

    priority = (data.get("priority") or "normal").strip()
    if priority not in ALLOWED_PRIORITIES:
        priority = "normal"

    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    if not title and not body:
        return jsonify({"ok": False, "error": "empty_message"}), 400
    if len(title) > 120:
        return jsonify({"ok": False, "error": "title_too_long"}), 400
    if len(body) > 4000:
        return jsonify({"ok": False, "error": "body_too_long"}), 400

    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "invalid_payload"}), 400

    db = _get_mongo_db()
    targets, err = _resolve_targets(db, event, year, data.get("target") or {})
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if not targets:
        return jsonify({"ok": False, "error": "no_target_matched"}), 404

    now = _now()
    expires = now + timedelta(seconds=INBOX_MESSAGE_TTL_SECONDS)
    sender_email = (getattr(request, "admin_user", None) or {}).get("email", "?")

    docs = []
    for d in targets:
        docs.append({
            "device_id": d["_id"],
            "device_name": d.get("name"),
            "event": event,
            "year": year,
            "type": mtype,
            "title": title,
            "body": body,
            "payload": payload,
            "priority": priority,
            "from": sender_email,
            "createdAt": now,
            "expiresAt": expires,
            "ack_at": None,
        })
    if docs:
        db["field_messages"].insert_many(docs)

    return jsonify({
        "ok": True,
        "sent_count": len(docs),
        "targets": [
            {"id": str(d["_id"]), "name": d.get("name")} for d in targets
        ],
    })


def _pub_message_admin(msg):
    """Variante admin : inclut device_id, device_name, event/year et status."""
    if not msg:
        return None
    base = _pub_message(msg)
    base.update({
        "device_id": str(msg.get("device_id")) if msg.get("device_id") else None,
        "device_name": msg.get("device_name"),
        "event": msg.get("event"),
        "year": msg.get("year"),
        "status": "read" if msg.get("ack_at") else "sent",
    })
    return base


@field_bp.route("/field/admin/messages", methods=["GET"])
@admin_required
def field_admin_messages_list():
    """Liste les messages envoyes (filtre event/year/device_id/limit)."""
    db = _get_mongo_db()
    query = {}
    event = request.args.get("event")
    year = request.args.get("year")
    device_id = request.args.get("device_id")
    if event:
        query["event"] = event
    if year:
        query["year"] = str(year)
    if device_id:
        try:
            query["device_id"] = ObjectId(device_id)
        except Exception:
            return jsonify({"ok": False, "error": "invalid_device_id"}), 400
    try:
        limit = max(1, min(500, int(request.args.get("limit", 100))))
    except (TypeError, ValueError):
        limit = 100
    cursor = db["field_messages"].find(query).sort("createdAt", -1).limit(limit)
    messages = [_pub_message_admin(m) for m in cursor]
    return jsonify({"ok": True, "messages": messages})


@field_bp.route("/field/admin/messages/<msg_id>", methods=["DELETE"])
@admin_required
def field_admin_message_delete(msg_id):
    """Supprime un message (rappel cote cockpit, mais la tablette l'a peut-etre deja vu)."""
    try:
        oid = ObjectId(msg_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    res = db["field_messages"].delete_one({"_id": oid})
    if res.deleted_count == 0:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


# =============================================================================
# RESSOURCES CARTE : routes /field/resources/*
# =============================================================================
#
# Ces routes servent les memes donnees geographiques que les routes /api/*
# utilisees par la carte cockpit, mais sans l'auth TITAN :
# elles exigent simplement un field_token valide (la tablette appartient a
# un event/year connu).

# Repertoires ou peuvent se trouver les tuiles satellites ACO
TILE_DIRECTORIES = [
    r"E:\TITAN\shared\satellite",
    r"C:\Users\l.arnault\satellite",
    "/Users/ludovic/Dropbox/ACO/TITAN/archives/looker/static/img/sat",
    "/Users/ludovicarnault/Dropbox/ACO/TITAN/looker/static/img/sat",
]


@field_bp.route("/field/resources/grid-ref", methods=["GET"])
@field_token_required
def field_resources_grid_ref():
    """Retourne le carroyage tactique (lignes depuis QGIS). Memes donnees que
    /api/grid-ref cote cockpit."""
    db = _get_mongo_db()
    lines_doc = db["grid_ref_qgis"].find_one({"type": "grid_lines"}, {"_id": 0})
    lines_25 = db["grid_ref_qgis"].find_one({"type": "grid_lines_25"}, {"_id": 0})
    if not lines_doc:
        return jsonify({"lines": None})
    return jsonify({"lines": lines_doc, "lines_25": lines_25})


@field_bp.route("/field/resources/3p", methods=["GET"])
@field_token_required
def field_resources_3p():
    """Retourne les portes/portails/portillons (collection 3p). Filtrage bbox
    optionnel via south/west/north/east."""
    db = _get_mongo_db()
    doc = db["3p"].find_one({}, {"_id": 0})
    if not doc or "features" not in doc:
        return jsonify({"features": []})

    try:
        south = request.args.get("south", type=float)
        west = request.args.get("west", type=float)
        north = request.args.get("north", type=float)
        east = request.args.get("east", type=float)
    except Exception:
        south = west = north = east = None

    features = doc["features"]
    if south is not None and west is not None and north is not None and east is not None:
        filtered = []
        for f in features:
            coords = (f.get("geometry") or {}).get("coordinates") or []
            if len(coords) >= 2:
                lng, lat = coords[0], coords[1]
                if south <= lat <= north and west <= lng <= east:
                    filtered.append(f)
        features = filtered

    return jsonify({"features": features})


@field_bp.route("/field/resources/gm-categories", methods=["GET"])
@field_token_required
def field_resources_gm_categories():
    """Liste des categories groundmaster actives (parkings, POI, etc.)."""
    db = _get_mongo_db()
    cats = list(db["groundmaster_categories"].find(
        {"enabled": True},
        {"label": 1, "icon": 1, "dataKey": 1, "collection": 1,
         "mode": 1, "sourceFormat": 1, "storageType": 1, "source": 1,
         "scheduleConfig": 1, "mapping": 1, "cardFields": 1},
    ))
    # On retire les categories qui sont explicitement "hors carte" (mode != map)
    # ainsi que celles masquees par defaut dans map_defaults
    defaults = db["merge_config"].find_one({"data_key": "__map_defaults__"}, {"_id": 0}) or {}
    hidden = set(defaults.get("hidden_categories") or [])
    out = []
    for c in cats:
        if c.get("dataKey") in hidden:
            continue
        c["_id"] = str(c["_id"])
        out.append(c)
    return jsonify({"categories": out})


@field_bp.route("/field/resources/map-bundle", methods=["GET"])
@field_token_required
def field_resources_map_bundle():
    """Bundle complet pour la carte field : categories + parametrage de l'event
    + couleurs d'itineraire (parking_colors) + couleurs par defaut par icone.
    Permet a la tablette de rendre les POI exactement comme la carte cockpit
    en un seul aller-retour reseau."""
    device = request.device
    event = device.get("event")
    year = device.get("year")
    db = _get_mongo_db()

    # 1) Categories groundmaster avec config complete
    cats = list(db["groundmaster_categories"].find(
        {"enabled": True},
        {"label": 1, "icon": 1, "dataKey": 1, "collection": 1,
         "mode": 1, "sourceFormat": 1, "storageType": 1, "source": 1,
         "scheduleConfig": 1, "mapping": 1, "cardFields": 1},
    ))
    defaults = db["merge_config"].find_one({"data_key": "__map_defaults__"}, {"_id": 0}) or {}
    hidden = set(defaults.get("hidden_categories") or [])
    categories = []
    for c in cats:
        if c.get("dataKey") in hidden:
            continue
        c["_id"] = str(c["_id"])
        categories.append(c)

    # 2) Parametrage event/year (ce qui est actif)
    parametrage = {}
    if event and year:
        param_doc = db["parametrages"].find_one(
            {"event": event, "year": str(year)}, {"_id": 0, "data": 1}
        )
        if not param_doc:
            param_doc = db["parametrages"].find_one(
                {"event": event, "year": year}, {"_id": 0, "data": 1}
            )
        if param_doc and isinstance(param_doc.get("data"), dict):
            parametrage = param_doc["data"]

    # 3) Couleurs d'itineraire (signmanager)
    parking_colors = {}
    sm = db["signmanager_settings"].find_one({}, {"_id": 0, "itineraire.couleurs": 1})
    if sm and isinstance(sm.get("itineraire"), dict):
        for col in (sm["itineraire"].get("couleurs") or []):
            nom = (col.get("nom") or "").strip()
            hexa = (col.get("hexa") or "").strip()
            if nom and hexa:
                parking_colors[nom.lower()] = hexa

    # 4) Couleurs par defaut par icone (mirror du looker)
    default_colors = {
        "door_front": "#132646",
        "local_parking": "#3B82F6",
        "camping": "#2E7D32",
        "hotel": "#FFD700",
        "event_seat": "#FA8072",
        "wc": "#b47272",
        "campground": "#B46300",
        "badge": "#FF00FF",
        "build": "#FF8C00",
        "rv_hookup": "#0D9488",
        "restaurant": "#E11D48",
        "medical_services": "#DC2626",
        "security": "#7C3AED",
        "directions_car": "#2563EB",
    }

    return jsonify({
        "categories": categories,
        "parametrage": parametrage,
        "parking_colors": parking_colors,
        "default_colors": default_colors,
        "event": event,
        "year": year,
    })


@field_bp.route("/field/resources/gm-collection/<collection_name>", methods=["GET"])
@field_token_required
def field_resources_gm_collection(collection_name):
    """Retourne les features GeoJSON d'une collection groundmaster."""
    # Validation : la collection doit etre referencee dans groundmaster_categories
    if not re.fullmatch(r"[a-zA-Z0-9_\-]{1,64}", collection_name or ""):
        return jsonify({"error": "invalid_collection"}), 400

    db = _get_mongo_db()
    valid = db["groundmaster_categories"].find_one(
        {"collection": collection_name, "enabled": True}, {"_id": 1}
    )
    if not valid:
        return jsonify({"features": []}), 200  # collection desactivee ou inconnue

    doc = db[collection_name].find_one(
        {"type": "FeatureCollection"} if collection_name == "terrains" else {},
        {"features": 1, "_id": 0},
    )
    features = []
    if doc and "features" in doc:
        features = doc["features"]
    if not features:
        docs = list(db[collection_name].find(
            {"type": "FeatureCollection"}, {"features": 1, "_id": 0}
        ))
        for d in docs:
            features.extend(d.get("features") or [])
    return jsonify({"features": features})


@field_bp.route("/field/my-fiches", methods=["GET"])
@field_token_required
def field_my_fiches():
    """Retourne les fiches PCORG assignees a la tablette courante.
    Match : event + year + content_category.patrouille == device.name."""
    device = request.device
    event = device.get("event")
    year = device.get("year")
    name = device.get("name")
    if not event or not year or not name:
        return jsonify({"open": [], "closed": []})

    db = _get_mongo_db()

    # Le champ year en base est un int dans la collection pcorg
    try:
        year_val = int(year)
    except (TypeError, ValueError):
        year_val = year

    base_query = {
        "event": event,
        "year": year_val,
        "content_category.patrouille": name,
        "category": {"$regex": "^PCO"},
    }

    # Ouvertes : status_code != 10 et (close_ts null ou absent)
    open_query = dict(base_query)
    open_query["$or"] = [
        {"status_code": {"$ne": 10}},
        {"close_ts": None},
        {"close_ts": {"$exists": False}},
    ]
    closed_query = dict(base_query)
    closed_query["status_code"] = 10

    def pub(f):
        gps = f.get("gps") or {}
        coords = gps.get("coordinates") if isinstance(gps, dict) else None
        lng = lat = None
        if isinstance(coords, list) and len(coords) >= 2:
            try:
                lng = float(coords[0])
                lat = float(coords[1])
            except (TypeError, ValueError):
                lng = lat = None
        return {
            "id": str(f.get("_id")),
            "category": f.get("category"),
            "text": f.get("text") or f.get("text_full"),
            "comment": f.get("comment") or "",
            "niveau_urgence": f.get("niveau_urgence"),
            "ts": _iso(f.get("ts")),
            "close_ts": _iso(f.get("close_ts")),
            "operator": f.get("operator"),
            "area": (f.get("area") or {}).get("desc") if isinstance(f.get("area"), dict) else None,
            "content_category": f.get("content_category") or {},
            "status_code": f.get("status_code"),
            "lat": lat,
            "lng": lng,
        }

    open_list = [pub(f) for f in db["pcorg"].find(open_query).sort("ts", -1).limit(200)]
    closed_list = [pub(f) for f in db["pcorg"].find(closed_query).sort("close_ts", -1).limit(50)]
    # Relire le device pour avoir le statut frais
    dev_fresh = db["field_devices"].find_one({"_id": device["_id"]}) or device

    # Determiner le tracking_mode effectif (purge watchers expires)
    tracking_mode = "normal"
    watchers = dev_fresh.get("tracking_watchers")
    if isinstance(watchers, dict) and watchers:
        now_iso = _now().isoformat()
        active = {k: v for k, v in watchers.items()
                  if isinstance(v, str) and v > now_iso}
        if active:
            tracking_mode = "high_freq"
        elif dev_fresh.get("tracking_mode") == "high_freq":
            # Tous les watchers ont expire, remettre a normal
            db["field_devices"].update_one(
                {"_id": device["_id"]},
                {"$set": {"tracking_mode": "normal", "tracking_watchers": {}}},
            )
    else:
        tracking_mode = dev_fresh.get("tracking_mode") or "normal"

    return jsonify({
        "open": open_list,
        "closed": closed_list,
        "device_name": name,
        "device_status": dev_fresh.get("status") or "patrouille",
        "active_fiche_id": dev_fresh.get("active_fiche_id"),
        "tracking_mode": tracking_mode,
        "now": _iso(_now()),
    })


@field_bp.route("/field/my-fiches/<fiche_id>/detail", methods=["GET"])
@field_token_required
def field_my_fiche_detail(fiche_id):
    """Retourne le detail complet d'une fiche (comme pcorg_detail cote cockpit)
    incluant la chronologie, les champs de contenu, et les coordonnees."""
    device = request.device
    name = device.get("name")
    db = _get_mongo_db()

    fiche = db["pcorg"].find_one({"_id": fiche_id})
    if not fiche:
        return jsonify({"ok": False, "error": "not_found"}), 404

    cc = fiche.get("content_category") or {}
    gps = fiche.get("gps") or {}
    coords = gps.get("coordinates") if isinstance(gps, dict) else None
    lat = lng = None
    if isinstance(coords, list) and len(coords) >= 2:
        try:
            lat = float(coords[1])
            lng = float(coords[0])
        except (TypeError, ValueError):
            pass

    ts = fiche.get("ts")
    close_ts = fiche.get("close_ts")
    comment_history = fiche.get("comment_history") or []

    return jsonify({
        "ok": True,
        "id": str(fiche["_id"]),
        "category": fiche.get("category"),
        "text": fiche.get("text") or "",
        "text_full": fiche.get("text_full") or "",
        "comment": fiche.get("comment") or "",
        "comment_history": comment_history,
        "niveau_urgence": fiche.get("niveau_urgence"),
        "ts": ts.isoformat() if isinstance(ts, datetime) else ts,
        "close_ts": close_ts.isoformat() if isinstance(close_ts, datetime) else close_ts,
        "operator": fiche.get("operator"),
        "operator_close": fiche.get("operator_close"),
        "area": (fiche.get("area") or {}).get("desc") if isinstance(fiche.get("area"), dict) else None,
        "content_category": cc,
        "status_code": fiche.get("status_code"),
        "lat": lat,
        "lng": lng,
        "server": fiche.get("server"),
    })


@field_bp.route("/field/my-fiches/<fiche_id>/comment", methods=["POST"])
@field_token_required
def field_my_fiche_comment_with_photo(fiche_id):
    """Ajoute un commentaire avec photo optionnelle sur une fiche.
    Accept multipart/form-data ou application/json.
    multipart : champ 'comment' + fichier 'photo' optionnel.
    json : champ 'comment'."""
    device = request.device
    name = device.get("name")
    if not name:
        return jsonify({"ok": False, "error": "unnamed_device"}), 400

    # Detect content type
    is_multipart = request.content_type and "multipart" in request.content_type
    if is_multipart:
        comment = (request.form.get("comment") or "").strip()
        photo_file = request.files.get("photo")
    else:
        data = request.get_json(silent=True) or {}
        comment = (data.get("comment") or "").strip()
        photo_file = None

    if not comment and not photo_file:
        return jsonify({"ok": False, "error": "empty_comment"}), 400
    if comment and len(comment) > 2000:
        return jsonify({"ok": False, "error": "comment_too_long"}), 400

    db = _get_mongo_db()
    fiche = db["pcorg"].find_one({"_id": fiche_id})
    if not fiche:
        return jsonify({"ok": False, "error": "not_found"}), 404

    cc = fiche.get("content_category") or {}
    if (cc.get("patrouille") or "") != name:
        return jsonify({"ok": False, "error": "not_assigned"}), 403

    # Handle photo upload
    photo_url = None
    if photo_file and photo_file.filename:
        ext = (photo_file.filename.rsplit(".", 1)[-1] if "." in photo_file.filename else "").lower()
        if ext not in FIELD_PHOTO_EXTENSIONS:
            return jsonify({"ok": False, "error": "invalid_photo_format"}), 400

        # Read and check size
        photo_data = photo_file.read()
        if len(photo_data) > FIELD_PHOTO_MAX_SIZE:
            return jsonify({"ok": False, "error": "photo_too_large"}), 400

        import uuid as _uuid
        photo_id = str(_uuid.uuid4())
        event = device.get("event") or "unknown"
        year = device.get("year") or "unknown"
        sub_dir = os.path.join(event, str(year))
        full_dir = os.path.join(FIELD_PHOTOS_DIR, sub_dir)
        os.makedirs(full_dir, exist_ok=True)

        safe_name = secure_filename(photo_file.filename) or "photo.jpg"
        filename = "{}_{}.{}".format(photo_id[:8], safe_name.rsplit(".", 1)[0][:20], ext)
        filepath = os.path.join(full_dir, filename)
        with open(filepath, "wb") as fp:
            fp.write(photo_data)

        photo_url = "/field/photos/{}/{}".format(sub_dir, filename)

    entry = {
        "ts": _now(),
        "text": comment or "",
        "operator": "field:" + name,
    }
    if photo_url:
        entry["photo"] = photo_url

    update_sets = {}
    if comment:
        update_sets["comment"] = comment

    db["pcorg"].update_one(
        {"_id": fiche_id},
        {
            "$set": update_sets,
            "$push": {"comment_history": entry},
        },
    )
    return jsonify({"ok": True, "photo": photo_url})


@field_bp.route("/field/photos/<path:photo_path>", methods=["GET"])
def field_photo_serve(photo_path):
    """Sert une photo uploadee depuis une tablette.
    Pas d'auth field_token : les photos sont visibles par les operateurs cockpit."""
    safe_path = os.path.normpath(photo_path)
    if ".." in safe_path or safe_path.startswith("/"):
        abort(404)
    full = os.path.join(FIELD_PHOTOS_DIR, safe_path)
    if not os.path.isfile(full):
        abort(404)
    directory = os.path.dirname(full)
    filename = os.path.basename(full)
    resp = send_from_directory(directory, filename)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@field_bp.route("/field/sos", methods=["POST"])
@field_token_required
def field_sos():
    """Declenche une alerte SOS dans cockpit_active_alerts. Le poller cockpit
    (alert_poller.js) affichera l'alerte en plein ecran sur les postes
    operateurs. Cree egalement automatiquement une fiche PCO de type Secours
    en niveau d urgence absolue (UA) pour suivi operationnel."""
    device = request.device
    name = device.get("name") or "?"
    event = device.get("event")
    year = device.get("year")

    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("lat")) if data.get("lat") is not None else None
    except (TypeError, ValueError):
        lat = None
    try:
        lng = float(data.get("lng")) if data.get("lng") is not None else None
    except (TypeError, ValueError):
        lng = None
    battery = data.get("battery")
    note = (data.get("note") or "").strip()

    now = _now()
    now_local = _now_local()
    expires = now + timedelta(minutes=30)

    db = _get_mongo_db()

    # 1) Creer une fiche PCO automatique (categorie PCO.Secours, niveau UA)
    fiche_id = None
    try:
        import uuid as _uuid
        ts_str = now_local.isoformat()
        text_lines = ["SOS tablette : " + name]
        if note:
            text_lines.append(note)
        if lat is not None and lng is not None:
            text_lines.append("Position : {:.5f}, {:.5f}".format(lat, lng))
        text = " \u2014 ".join(text_lines)
        seed = "{}|{}|{}|PCO.Secours|{}|{}|field-sos:{}".format(
            event or "", year or "", ts_str, text, "", str(device.get("_id"))
        )
        fiche_id = str(_uuid.uuid5(_uuid.NAMESPACE_URL, seed))
        gps = None
        if lat is not None and lng is not None:
            gps = {"type": "Point", "coordinates": [lng, lat]}
        try:
            year_int = int(year) if year is not None else None
        except (TypeError, ValueError):
            year_int = year
        fiche_doc = {
            "_id": fiche_id,
            "event": event,
            "year": year_int,
            "ts": now,
            "timestamp_iso": ts_str,
            "close_ts": None,
            "close_iso": None,
            "category": "PCO.Secours",
            "source": "PCO.Secours",
            "text": text,
            "text_full": text,
            "comment": "",
            "comment_history": [],
            "operator": "Tablette " + name,
            "operator_id_create": "field:" + str(device.get("_id")),
            "operator_close": None,
            "operator_id_close": None,
            "status_code": 0,
            "severity": 0,
            "niveau_urgence": "UA",
            "is_incident": False,
            "area": None,
            "gps": gps,
            "group": None,
            "content_category": {
                "patrouille": name,
                "field_sos": True,
            },
            "extracted": {"phones": None, "plates": None},
            "tags": ["field-sos"],
            "synced_at": None,
            "sql_id": None,
            "guid": None,
            "server": "COCKPIT",
            "bounce_rev": 1,
        }
        db["pcorg"].insert_one(fiche_doc)
    except Exception as exc:
        logger.warning("[field_sos] auto-fiche failed: %s", exc)
        fiche_id = None

    alert = {
        "definition_slug": "field_sos",
        "event": event,
        "year": str(year) if year is not None else None,
        "title": "SOS - " + name,
        "message": note or "Demande d assistance immediate",
        "timeStr": now.strftime("%H:%M"),
        "dedup_key": "field-sos-" + str(device.get("_id")),
        "triggeredAt": now,
        "expiresAt": expires,
        "status": "active",
        "actionData": {
            "device_id": str(device.get("_id")),
            "device_name": name,
            "beacon_group_id": device.get("beacon_group_id"),
            "lat": lat,
            "lng": lng,
            "battery": battery,
            "note": note,
            "pcorg_id": fiche_id,
        },
    }

    # Upsert par dedup_key pour eviter le spam si la tablette rappuie
    db["cockpit_active_alerts"].update_one(
        {"dedup_key": alert["dedup_key"]},
        {"$set": alert},
        upsert=True,
    )
    # Historiser egalement dans field_messages pour avoir trace cote tablette
    db["field_messages"].insert_one({
        "device_id": device["_id"],
        "device_name": name,
        "event": event,
        "year": str(year) if year is not None else None,
        "type": "alert",
        "title": "SOS envoye",
        "body": "Le cockpit a ete prevenu." + (" (" + note + ")" if note else ""),
        "priority": "high",
        "from": "field",
        "createdAt": now,
        "expiresAt": now + timedelta(seconds=INBOX_MESSAGE_TTL_SECONDS),
        "ack_at": None,
    })
    return jsonify({"ok": True, "pcorg_id": fiche_id})


@field_bp.route("/field/resources/tiles/<z>/<x>/<y>.png", methods=["GET"])
@field_token_required
def field_resources_tiles(z, x, y):
    """Proxy vers les tuiles satellite ACO (mirror de /tiles/<z>/<x>/<y>.png
    mais sans l'auth cockpit)."""
    # Validation numerique stricte
    if not (z.isdigit() and x.isdigit() and y.isdigit()):
        return abort(400)
    for tile_directory in TILE_DIRECTORIES:
        try:
            tile_path = safe_join(tile_directory, z, x)
            if not tile_path:
                continue
            image_path = safe_join(tile_path, f"{y}.png")
            if image_path and os.path.exists(image_path):
                return send_from_directory(tile_path, f"{y}.png")
        except Exception:
            continue
    return abort(404)
