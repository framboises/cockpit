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
from pymongo import MongoClient, ReturnDocument
from bson.objectid import ObjectId
from io import BytesIO
import os
import hashlib
import secrets
import logging
import re
import uuid as _uuid
from functools import wraps

try:
    from PIL import Image, ImageOps
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

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

# ---------------------------------------------------------------------------
# Web Push (VAPID)
# ---------------------------------------------------------------------------
# En dev, on genere les cles automatiquement dans vapid_private.pem.
# En prod, definir VAPID_PRIVATE_KEY (contenu PEM) et VAPID_CONTACT_EMAIL.

VAPID_CONTACT_EMAIL = os.getenv("VAPID_CONTACT_EMAIL", "dev@cockpit.local")
_VAPID_PRIVATE_KEY = None
_VAPID_PUBLIC_KEY_B64 = None

# Photos
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FIELD_PHOTOS_DIR = os.path.join(SCRIPT_DIR, "uploads", "field_photos")
FIELD_PHOTO_MAX_SIZE = 10 * 1024 * 1024   # 10 Mo par photo en upload brut
FIELD_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
FIELD_PHOTO_MAX_DIM = 1920                # downscale plus grand cote
FIELD_PHOTO_JPEG_QUALITY = 85
FIELD_PHOTO_THUMB_DIM = 256
FIELD_PHOTO_THUMB_QUALITY = 78
FIELD_PHOTO_FILE_TTL_DAYS = 30            # purge fichiers physiques apres 30 jours
FIELD_PHOTO_MAX_PER_BATCH = 5             # limite multi-photos par envoi

# Codes (QR / barcode) scannes depuis la tablette : longueur unitaire + lot
FIELD_SCAN_MAX_PER_BATCH = 20             # limite codes par envoi
FIELD_SCAN_VALUE_MAX_LEN = 512            # taille brute d'un code (ascii / utf-8)
FIELD_SCAN_FORMATS = {
    "qr_code", "data_matrix", "aztec", "pdf417",
    "code_128", "code_39", "code_93", "codabar", "itf",
    "ean_13", "ean_8", "upc_a", "upc_e",
    "manual",  # saisie clavier (fallback)
}

# -----------------------------------------------------------------------------
# Streaming live tablette -> PC org -> VMS Qonify (via mediamtx)
# -----------------------------------------------------------------------------
# Architecture : pool de N slots fixes (paths mediamtx field-1, field-2, ...).
# Qonify est configure UNE FOIS avec les N URLs RTSP. Cockpit alloue dynamiquement
# un slot a chaque demande de stream, evitant toute reconfiguration VMS.
#
# RGPD : pas d'enregistrement, pas d'audio, auto-stop a 5 min, consentement
# explicite cote tablette a chaque demande.
FIELD_STREAM_SLOTS = int(os.getenv("FIELD_STREAM_SLOTS", "3"))
FIELD_STREAM_MAX_DURATION_S = int(os.getenv("FIELD_STREAM_MAX_DURATION_S", "300"))
FIELD_STREAM_REQUEST_TTL_S = int(os.getenv("FIELD_STREAM_REQUEST_TTL_S", "60"))
FIELD_STREAM_STALE_GRACE_S = 30           # libere un slot dont le stream
                                          # n'a pas ping mediamtx depuis 30s

# View tokens stables (1 par slot, jamais changes en cours d'evenement). Ils
# sont integres dans les URLs RTSP/WHEP configurees une fois pour toutes dans
# Qonify et le PC org. En prod : definir FIELD_STREAM_VIEW_TOKENS=tok1,tok2,tok3
# (taille recommandee : 32 hex chars chacun). En dev, fallback derive secret.
def _load_field_stream_view_tokens():
    raw = os.getenv("FIELD_STREAM_VIEW_TOKENS", "").strip()
    if raw:
        toks = [t.strip() for t in raw.split(",") if t.strip()]
        if len(toks) >= FIELD_STREAM_SLOTS:
            return toks[:FIELD_STREAM_SLOTS]
    # Dev fallback : tokens deterministes derives du SECRET_KEY si dispo,
    # sinon tokens fixes ("dev-view-token-N") pour faciliter le test.
    base = os.getenv("SECRET_KEY", "cockpit-dev-secret")
    return [
        hashlib.sha256(("vstream:" + base + ":slot:" + str(i)).encode()).hexdigest()[:32]
        for i in range(FIELD_STREAM_SLOTS)
    ]


FIELD_STREAM_VIEW_TOKENS = _load_field_stream_view_tokens()
# Defauts cales sur l'Option A (path-based proxy sous cockpit.lemans.org).
# En prod : MEDIAMTX_BASE_URL doit pointer vers le sous-path proxifie qui
# pointe vers mediamtx (ex: https://cockpit.lemans.org/webrtc).
# RTSP n'utilise pas HTTPS donc reste sur le DNS Cockpit, port 8554 direct.
MEDIAMTX_BASE_URL = os.getenv("MEDIAMTX_BASE_URL", "https://cockpit.lemans.org/webrtc").rstrip("/")
MEDIAMTX_RTSP_BASE = os.getenv("MEDIAMTX_RTSP_BASE", "rtsp://cockpit.lemans.org:8554").rstrip("/")
MEDIAMTX_AUTH_HMAC_KEY = os.getenv("MEDIAMTX_AUTH_HMAC_KEY", "dev-mediamtx-shared-key")

# Photos 3P (portes/portails) partagees avec l'app looker
LOOKER_3P_MEDIA_DIR = os.path.join(SCRIPT_DIR, "..", "looker", "static", "img", "media")


def _now():
    return datetime.now(timezone.utc)


def _now_local():
    return datetime.now(TZ_LOCAL)


def _init_vapid():
    global _VAPID_PRIVATE_KEY, _VAPID_PUBLIC_KEY_B64
    if _VAPID_PRIVATE_KEY is not None:
        return
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    import base64

    env_key = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    pem_path = os.path.join(SCRIPT_DIR, "vapid_private.pem")

    if env_key:
        pem_bytes = env_key.encode("utf-8")
    elif os.path.exists(pem_path):
        with open(pem_path, "rb") as f:
            pem_bytes = f.read()
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        pem_bytes = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        with open(pem_path, "wb") as f:
            f.write(pem_bytes)
        logger.info("field: generated new VAPID key pair -> %s", pem_path)

    priv = serialization.load_pem_private_key(pem_bytes, password=None)
    _VAPID_PRIVATE_KEY = pem_bytes.decode("utf-8")
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    _VAPID_PUBLIC_KEY_B64 = base64.urlsafe_b64encode(pub_raw).decode("ascii").rstrip("=")


def get_vapid_public_key():
    _init_vapid()
    return _VAPID_PUBLIC_KEY_B64


def send_push_notification(subscription_info, title, body, url=None, tag=None, push_type=None):
    """Envoie une notification push a une souscription. Silencieux en cas d'erreur.
    push_type="sos" declenche un rendu renforce cote SW (vibration longue, persistance)."""
    _init_vapid()
    if not _VAPID_PRIVATE_KEY or not subscription_info:
        return False
    try:
        from pywebpush import webpush
        import json as _json
        payload = {"title": title, "body": body}
        if url:
            payload["url"] = url
        if tag:
            payload["tag"] = tag
        if push_type:
            payload["type"] = push_type
        webpush(
            subscription_info=subscription_info,
            data=_json.dumps(payload),
            vapid_private_key=_VAPID_PRIVATE_KEY,
            vapid_claims={"sub": "mailto:" + VAPID_CONTACT_EMAIL},
            timeout=5,
        )
        return True
    except Exception as e:
        logger.debug("field: push failed: %s", e)
        return False


def send_push_to_device(db, device_id, title, body, url=None, tag=None, push_type=None):
    """Envoie un push a toutes les souscriptions d'un device."""
    subs = list(db["field_push_subs"].find({"device_id": device_id}))
    for sub in subs:
        info = sub.get("subscription")
        if info:
            send_push_notification(info, title, body, url=url, tag=tag, push_type=push_type)


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
        # Filtres console admin (vue "Photos terrain", recherches event/type)
        db["field_messages"].create_index([("type", 1), ("event", 1), ("year", 1), ("createdAt", -1)])
        db["field_messages"].create_index([("event", 1), ("year", 1), ("createdAt", -1)])

        # field_push_subs : souscriptions Web Push par device
        db["field_push_subs"].create_index("device_id")
        db["field_push_subs"].create_index("endpoint", unique=True)

        # field_stream_slots : 1 doc par slot fixe, allocate via findOneAndUpdate
        db["field_stream_slots"].create_index("slot_index", unique=True)
        db["field_stream_slots"].create_index("current_stream_id")

        # field_streams : audit + TTL Mongo via expires_at (auto-delete apres 90j)
        db["field_streams"].create_index("device_id")
        db["field_streams"].create_index("status")
        db["field_streams"].create_index("expires_at", expireAfterSeconds=0)

        # Seed les N slots si la collection est vide (idempotent)
        _seed_stream_slots(db)
    except Exception as e:
        logger.warning("field: index creation failed: %s", e)
    _INDEXES_READY = True


def _seed_stream_slots(db):
    """Cree les N docs field_stream_slots si la collection est vide.
    Utilise upsert + $setOnInsert : idempotent et safe sous concurrence
    (plusieurs workers Waitress / gunicorn appellent cette fonction au boot
    en parallele -- sans upsert on aurait des E11000 duplicate key)."""
    for i in range(FIELD_STREAM_SLOTS):
        try:
            db["field_stream_slots"].update_one(
                {"slot_index": i},
                {"$setOnInsert": {
                    "slot_index": i,
                    "path": "field-{}".format(i + 1),
                    "view_token": FIELD_STREAM_VIEW_TOKENS[i],
                    "current_stream_id": None,
                    "last_assigned_at": None,
                }},
                upsert=True,
            )
        except Exception as e:
            # Course tres serree entre 2 upserts en parallele : on ignore
            # le E11000 (le doc existe deja, c'est ce qu'on voulait).
            msg = str(e)
            if "E11000" in msg or "duplicate key" in msg:
                continue
            logger.warning("field: stream slots seed failed (slot=%d): %s", i, e)


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
# Helper partage avec vision_admin.py : date de fin d'evenement (parametrages)
# ---------------------------------------------------------------------------

def _event_end_datetime(db, event, year):
    """Retourne la date/heure de fin d'evenement (apres demontage) en UTC,
    ou None si non defini. Utilise par vision_admin.py pour calculer l'exp du JWT."""
    if not event or not year:
        return None
    try:
        doc = db["parametrages"].find_one(
            {"event": event, "year": str(year)},
            {"_id": 0, "data.globalHoraires.demontage": 1},
        )
        if not doc:
            doc = db["parametrages"].find_one(
                {"event": event, "year": year},
                {"_id": 0, "data.globalHoraires.demontage": 1},
            )
        gh = ((doc or {}).get("data") or {}).get("globalHoraires") or {}
        end_str = (gh.get("demontage") or {}).get("end") or ""
        end_str = end_str.strip()
    except Exception:
        end_str = ""
    if not end_str:
        return None
    try:
        if "T" in end_str:
            dt = datetime.fromisoformat(end_str)
        else:
            dt = datetime.fromisoformat(end_str[:10] + "T23:59:59")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_LOCAL)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Auto-revocation : quand un evenement est termine (date de fin de demontage
# depassee), on revoke automatiquement toutes ses tablettes. Check paresseux
# avec cache 5 min pour eviter les reads inutiles a chaque poll tablette.
# ---------------------------------------------------------------------------

_EVENT_ENDED_TTL = 300  # secondes
_event_ended_cache = {}  # (event, year_str) -> (checked_at_ts, is_ended, swept)


def _event_demontage_ended(db, event, year):
    """True si l'evenement a une date de fin de demontage passee.
    False si pas de demontage defini (evenement reste actif indefiniment)."""
    if not event or not year:
        return False
    key = (str(event), str(year))
    now_ts = _now().timestamp()
    cached = _event_ended_cache.get(key)
    if cached and now_ts - cached[0] < _EVENT_ENDED_TTL:
        return cached[1]
    try:
        doc = db["parametrages"].find_one(
            {"event": event, "year": str(year)}, {"_id": 0, "data.globalHoraires.demontage": 1}
        )
        if not doc:
            doc = db["parametrages"].find_one(
                {"event": event, "year": year}, {"_id": 0, "data.globalHoraires.demontage": 1}
            )
        gh = ((doc or {}).get("data") or {}).get("globalHoraires") or {}
        demontage = gh.get("demontage") or {}
        end_str = (demontage.get("end") or "").strip()
    except Exception:
        end_str = ""

    is_ended = False
    if end_str:
        # Format attendu : ISO YYYY-MM-DD (ou avec heure). On prend la date locale.
        try:
            end_date = end_str[:10]  # YYYY-MM-DD
            today = _now_local().strftime("%Y-%m-%d")
            is_ended = today > end_date
        except Exception:
            is_ended = False

    _event_ended_cache[key] = (now_ts, is_ended, cached[2] if cached else False)
    return is_ended


def _sweep_event_if_ended(db, event, year):
    """Si l'evenement est termine, revoke en bloc toutes ses tablettes
    encore actives et retourne True. Idempotent via un flag cache."""
    if not _event_demontage_ended(db, event, year):
        return False
    key = (str(event), str(year))
    cached = _event_ended_cache.get(key)
    # Deja sweep pour cet (event, year) ? On saute le update_many.
    if cached and cached[2]:
        return True
    try:
        db["field_devices"].update_many(
            {"event": event, "year": str(year), "revoked": {"$ne": True}},
            {"$set": {
                "revoked": True,
                "revoke_reason": "event_ended",
                "revoke_ts": _now(),
            }},
        )
    except Exception as e:
        logger.debug("field: sweep_event_if_ended failed: %s", e)
        return True
    _event_ended_cache[key] = (cached[0] if cached else _now().timestamp(), True, True)
    return True


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

        # Auto-revocation si l'evenement est termine (demontage passe).
        # Le sweep se fait une seule fois par (event, year) grace au cache.
        if _sweep_event_if_ended(db, device.get("event"), device.get("year")):
            if _wants_json():
                return jsonify({"error": "event_ended"}), 401
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
        "revoke_reason": device.get("revoke_reason"),
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
        "direction": msg.get("direction", "cockpit_to_field"),
        "thread_id": str(msg["thread_id"]) if msg.get("thread_id") else None,
        "reply_count": msg.get("reply_count", 0),
        "created_at": _iso(msg.get("createdAt")),
        "ack_at": _iso(msg.get("ack_at")),
    }


def _iso(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return None


class PhotoUploadError(Exception):
    """Levee par _process_and_save_photo en cas d'upload invalide.
    `code` est l'identifiant retourne au front, `status` le code HTTP."""
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code = code
        self.status = status


def _process_and_save_photo(photo_file, sub_dir):
    """Lit une photo uploadee, valide son contenu (Pillow), strippe l'EXIF,
    applique l'auto-rotation, downscale a FIELD_PHOTO_MAX_DIM, sauve l'original
    JPEG q=85 + une miniature 256px. Retourne (photo_url, thumb_url).

    `sub_dir` est relatif a FIELD_PHOTOS_DIR (ex. "{event}/{year}").

    Leve PhotoUploadError(code) en cas d'invalidite (extension, taille, contenu).
    """
    if not _PIL_AVAILABLE:
        raise PhotoUploadError("server_no_pillow", 500)
    if not photo_file or not photo_file.filename:
        raise PhotoUploadError("missing_photo")

    ext = (photo_file.filename.rsplit(".", 1)[-1] if "." in photo_file.filename else "").lower()
    if ext not in FIELD_PHOTO_EXTENSIONS:
        raise PhotoUploadError("invalid_photo_format")

    photo_data = photo_file.read()
    if not photo_data:
        raise PhotoUploadError("empty_photo")
    if len(photo_data) > FIELD_PHOTO_MAX_SIZE:
        raise PhotoUploadError("photo_too_large")

    try:
        Image.open(BytesIO(photo_data)).verify()
    except Exception:
        raise PhotoUploadError("invalid_photo_content")

    try:
        img = Image.open(BytesIO(photo_data))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if max(img.size) > FIELD_PHOTO_MAX_DIM:
            img.thumbnail((FIELD_PHOTO_MAX_DIM, FIELD_PHOTO_MAX_DIM), Image.LANCZOS)
    except Exception as e:
        logger.warning("field: photo decode failed: %s", e)
        raise PhotoUploadError("invalid_photo_content")

    photo_id = str(_uuid.uuid4())
    base_safe = secure_filename(photo_file.filename) or "photo.jpg"
    stem = (base_safe.rsplit(".", 1)[0][:20] or "photo")
    filename = "{}_{}.jpg".format(photo_id[:8], stem)

    full_dir = os.path.join(FIELD_PHOTOS_DIR, sub_dir)
    thumb_dir = os.path.join(full_dir, "thumbs")
    os.makedirs(full_dir, exist_ok=True)
    os.makedirs(thumb_dir, exist_ok=True)

    filepath = os.path.join(full_dir, filename)
    img.save(filepath, format="JPEG", quality=FIELD_PHOTO_JPEG_QUALITY, optimize=True)

    thumb = img.copy()
    thumb.thumbnail((FIELD_PHOTO_THUMB_DIM, FIELD_PHOTO_THUMB_DIM), Image.LANCZOS)
    thumb_path = os.path.join(thumb_dir, filename)
    thumb.save(thumb_path, format="JPEG", quality=FIELD_PHOTO_THUMB_QUALITY, optimize=True)

    sub = sub_dir.replace(os.sep, "/")
    photo_url = "/field/photos/{}/{}".format(sub, filename)
    thumb_url = "/field/photos/{}/thumbs/{}".format(sub, filename)
    return photo_url, thumb_url


def _load_thread_root(db, msg_oid):
    """Retourne (root, thread_id) pour un msg_id donne (racine ou reponse).
    Retourne (None, None) si introuvable."""
    original = db["field_messages"].find_one({"_id": msg_oid})
    if not original:
        return None, None
    thread_id = original.get("thread_id") or msg_oid
    if thread_id == msg_oid:
        root = original
    else:
        root = db["field_messages"].find_one({"_id": thread_id})
        if not root:
            return None, None
    return root, thread_id


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


# ---------------------------------------------------------------------------
# Web Push : souscription / cle publique
# ---------------------------------------------------------------------------

@field_bp.route("/field/push/vapid-public-key", methods=["GET"])
@field_token_required
def field_push_vapid_key():
    """Retourne la cle publique VAPID pour le frontend."""
    return jsonify({"ok": True, "key": get_vapid_public_key()})


@field_bp.route("/field/push/subscribe", methods=["POST"])
@field_token_required
def field_push_subscribe():
    """Enregistre (ou met a jour) une souscription push pour cette tablette."""
    data = request.get_json(silent=True) or {}
    sub = data.get("subscription")
    if not sub or not sub.get("endpoint"):
        return jsonify({"ok": False, "error": "missing_subscription"}), 400

    db = _get_mongo_db()
    device = request.device
    endpoint = sub["endpoint"]

    db["field_push_subs"].update_one(
        {"endpoint": endpoint},
        {"$set": {
            "device_id": device["_id"],
            "device_name": device.get("name", ""),
            "subscription": sub,
            "updatedAt": _now(),
        },
        "$setOnInsert": {"createdAt": _now()}},
        upsert=True,
    )
    return jsonify({"ok": True})


@field_bp.route("/field/push/unsubscribe", methods=["POST"])
@field_token_required
def field_push_unsubscribe():
    """Supprime une souscription push."""
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"ok": False, "error": "missing_endpoint"}), 400

    db = _get_mongo_db()
    db["field_push_subs"].delete_one({"endpoint": endpoint})
    return jsonify({"ok": True})


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

VALID_STATUSES = {"patrouille", "intervention", "sur_place", "pause", "fin_intervention"}


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
    Statuts : patrouille | intervention | sur_place | pause | fin_intervention
    Pour fin_intervention : on accepte un 'comment' optionnel (stocke sur le device).
    Transition directe intervention/sur_place -> patrouille interdite (doit passer par fin_intervention)."""
    data = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip().lower()
    if new_status not in VALID_STATUSES:
        return jsonify({"ok": False, "error": "invalid_status"}), 400

    device = request.device
    db = _get_mongo_db()
    now = _now()

    cur_status = device.get("status") or "patrouille"

    # Interdire passage direct intervention/sur_place -> patrouille
    if new_status == "patrouille" and cur_status in ("intervention", "sur_place"):
        return jsonify({"ok": False, "error": "transition_interdite",
                        "message": "Utilisez 'Fin d intervention' avant de revenir en disponible"}), 400

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

    # Fin d'intervention : stocker le commentaire optionnel
    if new_status == "fin_intervention":
        fin_comment = (data.get("comment") or "").strip()
        update["$set"]["fin_comment"] = fin_comment or None

    # Si retour a patrouille, on desassocie la fiche active
    if new_status == "patrouille":
        update["$set"]["active_fiche_id"] = None

    db["field_devices"].update_one({"_id": device["_id"]}, update)

    # Ajouter une entree dans la chronologie de la fiche active pour ASL et engagement
    fiche_id = device.get("active_fiche_id")
    if fiche_id and new_status in ("sur_place", "intervention"):
        status_labels = {
            "sur_place": "Arrivee sur les lieux (ASL)",
            "intervention": "Engagement confirme",
        }
        chrono_entry = {
            "ts": now,
            "text": "Statut: " + status_labels.get(new_status, new_status),
            "operator": "field:" + (device.get("name") or "?"),
        }
        db["pcorg"].update_one(
            {"_id": fiche_id},
            {"$push": {"comment_history": chrono_entry}},
        )

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


@field_bp.route("/field/thread/<msg_id>", methods=["GET"])
@field_token_required
def field_thread(msg_id):
    """Retourne tous les messages d'un thread (message initial + reponses)."""
    try:
        oid = ObjectId(msg_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400

    db = _get_mongo_db()
    device_id = request.device["_id"]

    root, thread_id = _load_thread_root(db, oid)
    if root is None:
        return jsonify({"ok": False, "error": "not_found"}), 404
    # Seul le device destinataire de la racine du thread peut le consulter
    if root.get("device_id") != device_id:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    cursor = db["field_messages"].find({
        "$or": [
            {"_id": thread_id},
            {"thread_id": thread_id},
        ],
    }).sort("createdAt", 1)

    messages = [_pub_message(m) for m in cursor]
    return jsonify({"ok": True, "thread_id": str(thread_id), "messages": messages})


@field_bp.route("/field/reply/<msg_id>", methods=["POST"])
@field_token_required
def field_reply(msg_id):
    """Reponse de la tablette a un message cockpit. Cree un message dans le thread.
    Accept multipart/form-data (body + photo) ou JSON (body)."""
    try:
        oid = ObjectId(msg_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400

    db = _get_mongo_db()
    device = request.device
    device_id = device["_id"]

    root, thread_id = _load_thread_root(db, oid)
    if root is None:
        return jsonify({"ok": False, "error": "not_found"}), 404
    # Seul le device destinataire de la racine peut repondre dans ce thread
    if root.get("device_id") != device_id:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    original = root

    # Parse body
    is_multipart = request.content_type and "multipart" in request.content_type
    if is_multipart:
        body_text = (request.form.get("body") or "").strip()
        photo_file = request.files.get("photo")
    else:
        data = request.get_json(silent=True) or {}
        body_text = (data.get("body") or "").strip()
        photo_file = None

    if not body_text and not photo_file:
        return jsonify({"ok": False, "error": "empty_reply"}), 400
    if body_text and len(body_text) > 4000:
        return jsonify({"ok": False, "error": "body_too_long"}), 400

    # Handle photo
    photo_url = None
    thumb_url = None
    if photo_file and photo_file.filename:
        event = device.get("event") or "unknown"
        year = device.get("year") or "unknown"
        sub_dir = os.path.join(str(event), str(year))
        try:
            photo_url, thumb_url = _process_and_save_photo(photo_file, sub_dir)
        except PhotoUploadError as e:
            return jsonify({"ok": False, "error": e.code}), e.status

    payload = {}
    if photo_url:
        payload["photo"] = photo_url
        payload["thumb"] = thumb_url

    now = _now()
    reply_doc = {
        "device_id": device_id,
        "device_name": device.get("name"),
        "event": device.get("event"),
        "year": device.get("year"),
        "type": original.get("type", "info"),
        "title": "",
        "body": body_text,
        "payload": payload,
        "priority": "normal",
        "from": "field:" + (device.get("name") or "?"),
        "direction": "field_to_cockpit",
        "thread_id": thread_id,
        "createdAt": now,
        "expiresAt": now + timedelta(seconds=INBOX_MESSAGE_TTL_SECONDS),
        "ack_at": None,
    }
    db["field_messages"].insert_one(reply_doc)

    # Incrementer le reply_count sur le message racine du thread
    db["field_messages"].update_one(
        {"_id": thread_id},
        {"$inc": {"reply_count": 1}},
    )

    return jsonify({"ok": True, "id": str(reply_doc["_id"])})


@field_bp.route("/field/photo/send", methods=["POST"])
@field_token_required
def field_photo_send():
    """Envoi d'une photo spontanee depuis la tablette vers les operateurs PC Org.
    Cree un message field_messages direction=field_to_cockpit, type=photo_report.
    Visible dans la console Field dispatch (historique des messages)."""
    device = request.device

    if not request.content_type or "multipart" not in request.content_type:
        return jsonify({"ok": False, "error": "multipart_required"}), 400

    comment = (request.form.get("comment") or "").strip()
    if len(comment) > 1000:
        return jsonify({"ok": False, "error": "comment_too_long"}), 400

    # Categorie + urgence (photo spontanee uniquement). Categorie obligatoire,
    # urgence optionnelle (defaut UR cote front).
    category = (request.form.get("category") or "").strip() or None
    urgency = (request.form.get("niveau_urgence") or "").strip() or None
    if category and not category.startswith("PCO."):
        return jsonify({"ok": False, "error": "invalid_category"}), 400
    if urgency and urgency not in {"IMP", "UR", "UA", "EU"}:
        return jsonify({"ok": False, "error": "invalid_urgency"}), 400

    # Multi-photos : champ "photos" (getlist) prioritaire, fallback "photo" (compat).
    photo_files = [pf for pf in request.files.getlist("photos") if pf and pf.filename]
    if not photo_files:
        single = request.files.get("photo")
        if single and single.filename:
            photo_files = [single]
    if not photo_files:
        return jsonify({"ok": False, "error": "missing_photo"}), 400
    if len(photo_files) > FIELD_PHOTO_MAX_PER_BATCH:
        return jsonify({"ok": False, "error": "too_many_photos"}), 400

    event = device.get("event") or "unknown"
    year = device.get("year") or "unknown"
    sub_dir = os.path.join(str(event), str(year))

    photos_meta = []
    for pf in photo_files:
        try:
            url, thumb = _process_and_save_photo(pf, sub_dir)
        except PhotoUploadError as e:
            return jsonify({"ok": False, "error": e.code, "uploaded": photos_meta}), e.status
        photos_meta.append({"photo": url, "thumb": thumb})

    # Metadata optionnelles
    try:
        lat = float(request.form.get("lat")) if request.form.get("lat") else None
    except (TypeError, ValueError):
        lat = None
    try:
        lng = float(request.form.get("lng")) if request.form.get("lng") else None
    except (TypeError, ValueError):
        lng = None
    try:
        battery = int(request.form.get("battery")) if request.form.get("battery") else None
    except (TypeError, ValueError):
        battery = None

    db = _get_mongo_db()
    now = _now()
    now_local = _now_local()
    name = device.get("name") or "?"
    # Titre : "Photo HH:MM" + nombre si multiple
    hh_mm = now_local.strftime("%H:%M")
    title = "Photo " + hh_mm if len(photos_meta) == 1 else "Photos {} (x{})".format(hh_mm, len(photos_meta))
    payload = {
        "photos": photos_meta,
        # Champs back-compat (1ere photo) pour les vieux clients
        "photo": photos_meta[0]["photo"],
        "thumb": photos_meta[0]["thumb"],
        "lat": lat,
        "lng": lng,
        "battery": battery,
    }
    if category:
        payload["category"] = category
    if urgency:
        payload["niveau_urgence"] = urgency

    doc = {
        "device_id": device["_id"],
        "device_name": name,
        "event": device.get("event"),
        "year": device.get("year"),
        "type": "photo_report",
        "title": title,
        "body": comment,
        "payload": payload,
        "priority": "normal",
        "from": "field:" + name,
        "direction": "field_to_cockpit",
        "createdAt": now,
        "expiresAt": now + timedelta(seconds=INBOX_MESSAGE_TTL_SECONDS),
        "ack_at": None,
    }
    res = db["field_messages"].insert_one(doc)
    return jsonify({
        "ok": True,
        "id": str(res.inserted_id),
        "photos": photos_meta,
        "photo_url": photos_meta[0]["photo"],
        "thumb_url": photos_meta[0]["thumb"],
    })


def _normalize_scan_codes(raw):
    """Valide et normalise une liste de codes scannes.
    Accepte une liste de chaines (back-compat) OU une liste de dicts
    {value, format}. Renvoie une liste de dicts {value, format} prets a
    persister, ou leve ValueError(code_erreur) en cas de probleme.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("missing_codes")
    if len(raw) > FIELD_SCAN_MAX_PER_BATCH:
        raise ValueError("too_many_codes")
    out = []
    for item in raw:
        if isinstance(item, str):
            value = item.strip()
            fmt = "manual"
        elif isinstance(item, dict):
            value = (item.get("value") or "").strip() if isinstance(item.get("value"), str) else ""
            fmt = (item.get("format") or "manual").strip().lower()
        else:
            raise ValueError("invalid_code")
        if not value:
            raise ValueError("empty_code")
        if len(value) > FIELD_SCAN_VALUE_MAX_LEN:
            raise ValueError("code_too_long")
        if fmt not in FIELD_SCAN_FORMATS:
            fmt = "manual"
        out.append({"value": value, "format": fmt})
    return out


@field_bp.route("/field/scan/send", methods=["POST"])
@field_token_required
def field_scan_send():
    """Envoi d'un lot de codes (QR/barcode) spontane vers le PC Org.
    Cree un field_messages direction=field_to_cockpit, type=scan_report.
    Body JSON : {codes: [{value, format}, ...], category, niveau_urgence,
    comment, lat, lng, battery}."""
    device = request.device
    data = request.get_json(silent=True) or {}

    try:
        codes = _normalize_scan_codes(data.get("codes"))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    comment = (data.get("comment") or "").strip()
    if len(comment) > 1000:
        return jsonify({"ok": False, "error": "comment_too_long"}), 400

    category = (data.get("category") or "").strip() or None
    urgency = (data.get("niveau_urgence") or "").strip() or None
    if category and not category.startswith("PCO."):
        return jsonify({"ok": False, "error": "invalid_category"}), 400
    if urgency and urgency not in {"IMP", "UR", "UA", "EU"}:
        return jsonify({"ok": False, "error": "invalid_urgency"}), 400
    # En envoi spontane (pas dans une fiche), categorie obligatoire pour
    # router le message cote PC org.
    if not category:
        return jsonify({"ok": False, "error": "missing_category"}), 400

    def _opt_float(k):
        try:
            v = data.get(k)
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    lat = _opt_float("lat")
    lng = _opt_float("lng")
    try:
        battery = int(data.get("battery")) if data.get("battery") is not None else None
    except (TypeError, ValueError):
        battery = None

    db = _get_mongo_db()
    now = _now()
    now_local = _now_local()
    name = device.get("name") or "?"
    hh_mm = now_local.strftime("%H:%M")
    title = "Scan {} (x{})".format(hh_mm, len(codes)) if len(codes) > 1 else "Scan " + hh_mm

    payload = {
        "codes": codes,
        "lat": lat,
        "lng": lng,
        "battery": battery,
        "category": category,
    }
    if urgency:
        payload["niveau_urgence"] = urgency

    doc = {
        "device_id": device["_id"],
        "device_name": name,
        "event": device.get("event"),
        "year": device.get("year"),
        "type": "scan_report",
        "title": title,
        "body": comment,
        "payload": payload,
        "priority": "normal",
        "from": "field:" + name,
        "direction": "field_to_cockpit",
        "createdAt": now,
        "expiresAt": now + timedelta(seconds=INBOX_MESSAGE_TTL_SECONDS),
        "ack_at": None,
    }
    res = db["field_messages"].insert_one(doc)
    return jsonify({"ok": True, "id": str(res.inserted_id), "codes": codes})


# =============================================================================
# STREAMING : flux video tablette -> mediamtx -> PC org / VMS Qonify
# =============================================================================
# Pool de N slots fixes : Qonify est configure une seule fois avec N URLs RTSP
# stables, Cockpit alloue dynamiquement un slot a chaque demande.
#
# Flux : PC org demande -> tablette accepte -> publie WHIP -> PC org consomme
# WHEP, Qonify consomme RTSP, le tout sur le meme path field-N.

import hmac as _hmac


def _slot_label(slot_index):
    """Label humain affiche dans l'UI : 'Camera terrain N'."""
    return "Camera terrain {}".format(int(slot_index) + 1)


def _stream_status_active(status):
    return status in ("requested", "accepted")


def _release_stream_slot(db, stream_id):
    """Libere le slot occupe par stream_id (idempotent). Retire aussi
    pending_stream_request du device si pose."""
    try:
        sid = stream_id if isinstance(stream_id, ObjectId) else ObjectId(stream_id)
    except Exception:
        return
    db["field_stream_slots"].update_many(
        {"current_stream_id": sid},
        {"$set": {"current_stream_id": None}},
    )
    s = db["field_streams"].find_one({"_id": sid}, {"device_id": 1})
    if s and s.get("device_id"):
        db["field_devices"].update_one(
            {"_id": s["device_id"], "pending_stream_request": sid},
            {"$set": {"pending_stream_request": None}},
        )


def _gc_stale_slots(db):
    """Libere les slots dont le stream est dans un etat stale (expired,
    fini, ou plus de last_publish_at depuis FIELD_STREAM_STALE_GRACE_S).
    Appele de facon lazy a chaque allocation."""
    now = _now()
    stale_publish_before = now - timedelta(seconds=FIELD_STREAM_STALE_GRACE_S)
    occupied = list(db["field_stream_slots"].find(
        {"current_stream_id": {"$ne": None}}
    ))
    for slot in occupied:
        sid = slot.get("current_stream_id")
        if not sid:
            continue
        s = db["field_streams"].find_one({"_id": sid})
        if not s:
            # Stream supprime (TTL Mongo) -> liberation
            db["field_stream_slots"].update_one(
                {"_id": slot["_id"]}, {"$set": {"current_stream_id": None}}
            )
            continue
        status = s.get("status")
        accepted_at = s.get("accepted_at")
        last_pub = s.get("last_publish_at")
        max_dur = s.get("max_duration_s") or FIELD_STREAM_MAX_DURATION_S
        is_stale = False
        if status in ("ended", "expired", "declined"):
            is_stale = True
        elif status == "requested":
            # Demande non acceptee dans les FIELD_STREAM_REQUEST_TTL_S
            if s.get("expires_at") and s["expires_at"] < now:
                is_stale = True
                db["field_streams"].update_one(
                    {"_id": s["_id"]},
                    {"$set": {"status": "expired", "ended_at": now}},
                )
        elif status == "accepted":
            # Depasse la duree max -> expired
            if accepted_at and accepted_at + timedelta(seconds=max_dur) < now:
                is_stale = True
                db["field_streams"].update_one(
                    {"_id": s["_id"]},
                    {"$set": {"status": "expired", "ended_at": now}},
                )
            # Pas de publish vu depuis grace period -> tablette deconnectee
            elif last_pub and last_pub < stale_publish_before:
                is_stale = True
                db["field_streams"].update_one(
                    {"_id": s["_id"]},
                    {"$set": {"status": "ended", "ended_at": now}},
                )
        if is_stale:
            db["field_stream_slots"].update_one(
                {"_id": slot["_id"]}, {"$set": {"current_stream_id": None}}
            )
            if s.get("device_id"):
                db["field_devices"].update_one(
                    {"_id": s["device_id"], "pending_stream_request": s["_id"]},
                    {"$set": {"pending_stream_request": None}},
                )


def _allocate_stream_slot(db, stream_id):
    """Trouve un slot libre et l'attribue atomiquement au stream_id.
    Renvoie le slot doc apres update, ou None si pool plein."""
    _gc_stale_slots(db)
    return db["field_stream_slots"].find_one_and_update(
        {"current_stream_id": None},
        {"$set": {"current_stream_id": stream_id, "last_assigned_at": _now()}},
        sort=[("slot_index", 1)],
        return_document=ReturnDocument.AFTER,
    )


def _verify_mediamtx_signature(req):
    """Verifie le header X-Mediamtx-Auth contre MEDIAMTX_AUTH_HMAC_KEY.
    En dev simple : comparaison directe (cle partagee). En prod, on peut
    upgrader vers HMAC sur le body."""
    sent = req.headers.get("X-Mediamtx-Auth", "")
    expected = MEDIAMTX_AUTH_HMAC_KEY or ""
    if not sent or not expected:
        return False
    return _hmac.compare_digest(sent, expected)


def _stream_view_urls(slot):
    """Construit les URLs publiques pour un slot (vue PC org + VMS)."""
    path = slot["path"]
    view_token = slot["view_token"]
    base_https = MEDIAMTX_BASE_URL.rstrip("/")
    base_rtsp = MEDIAMTX_RTSP_BASE.rstrip("/")
    return {
        "whep_url": "{}/{}/whep?token={}".format(base_https, path, view_token),
        "hls_url": "{}/{}/index.m3u8?token={}".format(base_https.replace(":8889", ":8888"), path, view_token),
        "rtsp_url": "{}/{}?token={}".format(base_rtsp, path, view_token),
    }


@field_bp.route("/field/stream/<stream_id>/accept", methods=["POST"])
@field_token_required
def field_stream_accept(stream_id):
    """La tablette accepte une demande de flux. On retourne au client le
    whip_url (ou pousser le flux WebRTC) et un publish_token jetable."""
    device = request.device
    try:
        sid = ObjectId(stream_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    s = db["field_streams"].find_one({"_id": sid})
    if not s:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if s.get("device_id") != device["_id"]:
        return jsonify({"ok": False, "error": "not_yours"}), 403
    now = _now()
    if s.get("status") != "requested":
        return jsonify({"ok": False, "error": "invalid_state", "status": s.get("status")}), 409
    if s.get("expires_at") and s["expires_at"] < now:
        # Expire en silence : libere le slot
        db["field_streams"].update_one({"_id": sid}, {"$set": {"status": "expired", "ended_at": now}})
        _release_stream_slot(db, sid)
        return jsonify({"ok": False, "error": "expired"}), 410

    slot = db["field_stream_slots"].find_one({"current_stream_id": sid})
    if not slot:
        return jsonify({"ok": False, "error": "slot_lost"}), 410

    db["field_streams"].update_one(
        {"_id": sid},
        {"$set": {"status": "accepted", "accepted_at": now, "last_publish_at": None}},
    )
    whip_url = "{}/{}/whip?token={}".format(
        MEDIAMTX_BASE_URL.rstrip("/"), slot["path"], s["publish_token"]
    )
    return jsonify({
        "ok": True,
        "whip_url": whip_url,
        "publish_token": s["publish_token"],
        "max_duration_s": s.get("max_duration_s") or FIELD_STREAM_MAX_DURATION_S,
        "slot_index": slot["slot_index"],
        "slot_label": _slot_label(slot["slot_index"]),
    })


@field_bp.route("/field/stream/<stream_id>/decline", methods=["POST"])
@field_token_required
def field_stream_decline(stream_id):
    """La tablette refuse la demande de flux."""
    device = request.device
    try:
        sid = ObjectId(stream_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    s = db["field_streams"].find_one({"_id": sid})
    if not s:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if s.get("device_id") != device["_id"]:
        return jsonify({"ok": False, "error": "not_yours"}), 403
    db["field_streams"].update_one(
        {"_id": sid},
        {"$set": {"status": "declined", "ended_at": _now()}},
    )
    _release_stream_slot(db, sid)
    return jsonify({"ok": True})


@field_bp.route("/field/stream/<stream_id>/end", methods=["POST"])
@field_token_required
def field_stream_end_tablet(stream_id):
    """La tablette met fin au stream (bouton Arreter ou auto-stop)."""
    device = request.device
    try:
        sid = ObjectId(stream_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    s = db["field_streams"].find_one({"_id": sid})
    if not s:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if s.get("device_id") != device["_id"]:
        return jsonify({"ok": False, "error": "not_yours"}), 403
    if s.get("status") in ("ended", "expired", "declined"):
        _release_stream_slot(db, sid)
        return jsonify({"ok": True, "already_ended": True})
    db["field_streams"].update_one(
        {"_id": sid},
        {"$set": {"status": "ended", "ended_at": _now()}},
    )
    _release_stream_slot(db, sid)
    return jsonify({"ok": True})


@field_bp.route("/field/api/stream/auth", methods=["POST"])
def field_stream_auth_webhook():
    """Webhook appele par mediamtx (runOnPublish/Read/Unpublish).
    Valide les tokens et met a jour l'etat des slots/streams.
    Auth : header X-Mediamtx-Auth = MEDIAMTX_AUTH_HMAC_KEY (cle partagee)."""
    if not _verify_mediamtx_signature(request):
        return jsonify({"ok": False, "error": "bad_signature"}), 403

    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").lower()
    path = (data.get("path") or "").strip()
    query = (data.get("query") or "").strip()

    if not path or not action:
        return jsonify({"ok": False, "error": "missing_fields"}), 400

    # Extraire le token de la query string. mediamtx passe la query sous forme
    # "token=xxx" (sans le "?"). On parse a la main pour eviter une dep.
    token = None
    for pair in query.split("&"):
        if pair.startswith("token="):
            token = pair[6:]
            break

    db = _get_mongo_db()
    slot = db["field_stream_slots"].find_one({"path": path})
    if not slot:
        return jsonify({"ok": False, "error": "unknown_path"}), 404

    now = _now()

    if action == "publish":
        if not token:
            return jsonify({"ok": False, "error": "missing_token"}), 403
        sid = slot.get("current_stream_id")
        if not sid:
            return jsonify({"ok": False, "error": "slot_idle"}), 403
        s = db["field_streams"].find_one({"_id": sid})
        if not s or s.get("status") != "accepted":
            return jsonify({"ok": False, "error": "stream_not_active"}), 403
        if not _hmac.compare_digest(s.get("publish_token") or "", token):
            return jsonify({"ok": False, "error": "bad_publish_token"}), 403
        db["field_streams"].update_one(
            {"_id": sid}, {"$set": {"last_publish_at": now}}
        )
        return jsonify({"ok": True})

    if action == "read":
        if not token:
            return jsonify({"ok": False, "error": "missing_token"}), 403
        # View token est stable pour le slot
        if not _hmac.compare_digest(slot.get("view_token") or "", token):
            return jsonify({"ok": False, "error": "bad_view_token"}), 403
        return jsonify({"ok": True})

    if action == "unpublish":
        # Liberation du slot quand la tablette ferme la peer connection.
        sid = slot.get("current_stream_id")
        if sid:
            db["field_streams"].update_one(
                {"_id": sid, "status": {"$in": ["accepted", "requested"]}},
                {"$set": {"status": "ended", "ended_at": now}},
            )
            _release_stream_slot(db, sid)
        return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "unknown_action"}), 400


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

@field_bp.route("/field/admin/sweep-ended-events", methods=["POST"])
@admin_required
def field_admin_sweep_ended_events():
    """Parcourt tous les (event, year) des tablettes encore actives et
    revoque celles dont l'evenement a une date de fin de demontage passee.
    Utile pour declencher le nettoyage hors du check paresseux en cas de
    doute ou en dehors des heures de connexion des tablettes."""
    db = _get_mongo_db()
    pairs = db["field_devices"].distinct("event", {"revoked": {"$ne": True}})
    total_swept = 0
    scanned = 0
    for ev in pairs:
        if not ev:
            continue
        years = db["field_devices"].distinct(
            "year", {"event": ev, "revoked": {"$ne": True}}
        )
        for yr in years:
            scanned += 1
            # Vide le cache de la paire pour forcer un re-check frais
            key = (str(ev), str(yr))
            _event_ended_cache.pop(key, None)
            before = db["field_devices"].count_documents(
                {"event": ev, "year": yr, "revoked": {"$ne": True}}
            )
            if _sweep_event_if_ended(db, ev, yr):
                after = db["field_devices"].count_documents(
                    {"event": ev, "year": yr, "revoked": {"$ne": True}}
                )
                total_swept += max(0, before - after)
    return jsonify({"ok": True, "scanned_pairs": scanned, "devices_revoked": total_swept})


def purge_old_photo_files(ttl_days=None):
    """Supprime les fichiers photos plus vieux que `ttl_days` jours
    (defaut FIELD_PHOTO_FILE_TTL_DAYS). Retourne un dict de stats.
    Walk-only sur le disque, ne requiert pas Mongo : appelable depuis un cron
    standalone (field_purge_photos.bat)."""
    if ttl_days is None:
        ttl_days = FIELD_PHOTO_FILE_TTL_DAYS
    if not os.path.isdir(FIELD_PHOTOS_DIR):
        return {"scanned": 0, "deleted": 0, "bytes_freed": 0, "ttl_days": ttl_days}

    cutoff = _now().timestamp() - (ttl_days * 86400)
    scanned = 0
    deleted = 0
    bytes_freed = 0
    for root, dirs, files in os.walk(FIELD_PHOTOS_DIR):
        for fname in files:
            scanned += 1
            full = os.path.join(root, fname)
            try:
                mtime = os.path.getmtime(full)
                if mtime < cutoff:
                    size = os.path.getsize(full)
                    os.remove(full)
                    deleted += 1
                    bytes_freed += size
            except OSError as e:
                logger.debug("field: purge skip %s: %s", full, e)

    # Nettoyage des dossiers vides residuels (thumbs/, year/, event/)
    for root, dirs, files in os.walk(FIELD_PHOTOS_DIR, topdown=False):
        if root == FIELD_PHOTOS_DIR:
            continue
        try:
            if not os.listdir(root):
                os.rmdir(root)
        except OSError:
            pass

    return {
        "scanned": scanned,
        "deleted": deleted,
        "bytes_freed": bytes_freed,
        "ttl_days": ttl_days,
    }


@field_bp.route("/field/admin/photos/purge-orphans", methods=["POST"])
@admin_required
def field_admin_photos_purge_orphans():
    """Supprime les fichiers photos plus vieux que FIELD_PHOTO_FILE_TTL_DAYS jours.
    Les documents Mongo (field_messages, comment_history) ont leur propre TTL plus
    court (7 j) ; on garde les fichiers physiques 30 j pour le debrief."""
    return jsonify({"ok": True, **purge_old_photo_files()})


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
            "$unset": {"revokedAt": "", "revoke_reason": "", "revoke_ts": ""},
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
# Admin : streaming live (allocation slots, viewer URLs, etat du pool)
# ---------------------------------------------------------------------------

@field_bp.route("/field/admin/device/<device_id>/stream-request", methods=["POST"])
@admin_required
def field_admin_stream_request(device_id):
    """Cree une demande de flux video pour la tablette device_id.
    Allocation atomique d'un slot dans le pool. Si pool plein -> 409 + liste
    des streams actifs (pour permettre a l'admin de couper)."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    dev = db["field_devices"].find_one({"_id": oid})
    if not dev:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if dev.get("revoked"):
        return jsonify({"ok": False, "error": "device_revoked"}), 403

    # Si une demande en cours, refuser plutot que doubler.
    existing_id = dev.get("pending_stream_request")
    if existing_id:
        existing = db["field_streams"].find_one({"_id": existing_id})
        if existing and _stream_status_active(existing.get("status")):
            return jsonify({
                "ok": False, "error": "device_busy",
                "stream_id": str(existing["_id"]),
                "status": existing.get("status"),
            }), 409

    operator = (getattr(request, "admin_user", None) or {}).get("email") or "operator"
    now = _now()
    expires_at = now + timedelta(seconds=FIELD_STREAM_REQUEST_TTL_S)
    publish_token = secrets.token_urlsafe(24)

    # On insert d'abord le stream pour obtenir son _id, puis on alloue le slot.
    stream_doc = {
        "device_id": dev["_id"],
        "device_name": dev.get("name") or "?",
        "event": dev.get("event"),
        "year": dev.get("year"),
        "slot_id": None,
        "slot_path": None,
        "publish_token": publish_token,
        "status": "requested",
        "requested_by": operator,
        "requested_at": now,
        "accepted_at": None,
        "ended_at": None,
        "expires_at": expires_at,
        "max_duration_s": FIELD_STREAM_MAX_DURATION_S,
        "last_publish_at": None,
    }
    res = db["field_streams"].insert_one(stream_doc)
    sid = res.inserted_id

    slot = _allocate_stream_slot(db, sid)
    if not slot:
        # Pool plein : on supprime le stream qu'on vient de creer + on
        # renvoie la liste des slots actifs.
        db["field_streams"].delete_one({"_id": sid})
        active = []
        for s in db["field_stream_slots"].find().sort("slot_index", 1):
            cur = s.get("current_stream_id")
            if not cur:
                continue
            cs = db["field_streams"].find_one({"_id": cur})
            if not cs:
                continue
            active.append({
                "slot_index": s["slot_index"],
                "slot_label": _slot_label(s["slot_index"]),
                "stream_id": str(cur),
                "device_id": str(cs.get("device_id")) if cs.get("device_id") else None,
                "device_name": cs.get("device_name"),
                "status": cs.get("status"),
                "accepted_at": _iso(cs.get("accepted_at")) if cs.get("accepted_at") else None,
            })
        return jsonify({"ok": False, "error": "pool_full", "active_slots": active}), 409

    db["field_streams"].update_one(
        {"_id": sid},
        {"$set": {"slot_id": slot["_id"], "slot_path": slot["path"]}},
    )
    db["field_devices"].update_one(
        {"_id": oid},
        {"$set": {"pending_stream_request": sid}},
    )
    return jsonify({
        "ok": True,
        "stream_id": str(sid),
        "slot_index": slot["slot_index"],
        "slot_label": _slot_label(slot["slot_index"]),
        "slot_path": slot["path"],
        "expires_at": _iso(expires_at),
        "status": "requested",
    })


@field_bp.route("/field/admin/device/<device_id>/stream-request", methods=["DELETE"])
@admin_required
def field_admin_stream_request_cancel(device_id):
    """Annule une demande de flux en cours (avant ou apres acceptation)."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    dev = db["field_devices"].find_one({"_id": oid}, {"pending_stream_request": 1})
    sid = dev.get("pending_stream_request") if dev else None
    if not sid:
        return jsonify({"ok": True, "no_active": True})
    db["field_streams"].update_one(
        {"_id": sid, "status": {"$in": ["requested", "accepted"]}},
        {"$set": {"status": "ended", "ended_at": _now()}},
    )
    _release_stream_slot(db, sid)
    return jsonify({"ok": True, "stream_id": str(sid)})


@field_bp.route("/field/admin/stream/<stream_id>/view", methods=["GET"])
@admin_required
def field_admin_stream_view(stream_id):
    """Retourne les URLs WHEP/RTSP/HLS + etat du stream pour la modale viewer."""
    try:
        sid = ObjectId(stream_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    s = db["field_streams"].find_one({"_id": sid})
    if not s:
        return jsonify({"ok": False, "error": "not_found"}), 404

    slot = None
    if s.get("slot_id"):
        slot = db["field_stream_slots"].find_one({"_id": s["slot_id"]})
    if not slot and s.get("slot_path"):
        slot = db["field_stream_slots"].find_one({"path": s["slot_path"]})

    body = {
        "ok": True,
        "stream_id": str(sid),
        "status": s.get("status"),
        "device_id": str(s.get("device_id")) if s.get("device_id") else None,
        "device_name": s.get("device_name"),
        "requested_by": s.get("requested_by"),
        "requested_at": _iso(s.get("requested_at")) if s.get("requested_at") else None,
        "accepted_at": _iso(s.get("accepted_at")) if s.get("accepted_at") else None,
        "ended_at": _iso(s.get("ended_at")) if s.get("ended_at") else None,
        "expires_at": _iso(s.get("expires_at")) if s.get("expires_at") else None,
        "max_duration_s": s.get("max_duration_s") or FIELD_STREAM_MAX_DURATION_S,
        "slot_index": slot["slot_index"] if slot else None,
        "slot_label": _slot_label(slot["slot_index"]) if slot else None,
    }
    if slot:
        body.update(_stream_view_urls(slot))
    return jsonify(body)


@field_bp.route("/field/admin/streams/active", methods=["GET"])
@admin_required
def field_admin_streams_active():
    """Liste l'etat des N slots du pool (pour la barre d'info Field Dispatch)."""
    db = _get_mongo_db()
    _gc_stale_slots(db)
    out = []
    for slot in db["field_stream_slots"].find().sort("slot_index", 1):
        entry = {
            "slot_index": slot["slot_index"],
            "slot_label": _slot_label(slot["slot_index"]),
            "path": slot["path"],
            "free": slot.get("current_stream_id") is None,
            "stream": None,
        }
        sid = slot.get("current_stream_id")
        if sid:
            s = db["field_streams"].find_one({"_id": sid})
            if s:
                entry["stream"] = {
                    "stream_id": str(s["_id"]),
                    "device_id": str(s.get("device_id")) if s.get("device_id") else None,
                    "device_name": s.get("device_name"),
                    "status": s.get("status"),
                    "requested_by": s.get("requested_by"),
                    "accepted_at": _iso(s.get("accepted_at")) if s.get("accepted_at") else None,
                    "expires_at": _iso(s.get("expires_at")) if s.get("expires_at") else None,
                    "max_duration_s": s.get("max_duration_s") or FIELD_STREAM_MAX_DURATION_S,
                }
        out.append(entry)
    return jsonify({"ok": True, "slots": out})


@field_bp.route("/field/admin/stream/<stream_id>/end", methods=["POST"])
@admin_required
def field_admin_stream_end(stream_id):
    """Force l'arret d'un stream cote PC org (bouton Couper dans la modale
    ou dans la liste des slots actifs)."""
    try:
        sid = ObjectId(stream_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    s = db["field_streams"].find_one({"_id": sid})
    if not s:
        return jsonify({"ok": False, "error": "not_found"}), 404
    db["field_streams"].update_one(
        {"_id": sid, "status": {"$in": ["requested", "accepted"]}},
        {"$set": {"status": "ended", "ended_at": _now()}},
    )
    _release_stream_slot(db, sid)
    return jsonify({"ok": True})


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


@field_bp.route("/field/admin/send-with-photo", methods=["POST"])
@admin_required
def field_admin_send_with_photo():
    """Envoie un message avec photo optionnelle vers une ou plusieurs tablettes.
    Accept multipart/form-data : champs texte + fichier 'photo' optionnel.
    Les champs JSON sont passes individuellement dans le form."""
    event = (request.form.get("event") or "").strip()
    year = (request.form.get("year") or "").strip()
    if not event or not year:
        return jsonify({"ok": False, "error": "missing_event_year"}), 400

    mtype = (request.form.get("type") or "info").strip()
    if mtype not in ALLOWED_MESSAGE_TYPES:
        return jsonify({"ok": False, "error": "invalid_type"}), 400

    priority = (request.form.get("priority") or "normal").strip()
    if priority not in ALLOWED_PRIORITIES:
        priority = "normal"

    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    if not title and not body:
        return jsonify({"ok": False, "error": "empty_message"}), 400
    if len(title) > 120:
        return jsonify({"ok": False, "error": "title_too_long"}), 400
    if len(body) > 4000:
        return jsonify({"ok": False, "error": "body_too_long"}), 400

    # Resolve target
    import json as _json
    target_raw = request.form.get("target") or "{}"
    try:
        target = _json.loads(target_raw)
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "invalid_target"}), 400

    db = _get_mongo_db()
    targets, err = _resolve_targets(db, event, year, target)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if not targets:
        return jsonify({"ok": False, "error": "no_target_matched"}), 404

    # Handle photo upload
    photo_url = None
    thumb_url = None
    photo_file = request.files.get("photo")
    if photo_file and photo_file.filename:
        sub_dir = os.path.join(str(event), str(year))
        try:
            photo_url, thumb_url = _process_and_save_photo(photo_file, sub_dir)
        except PhotoUploadError as e:
            return jsonify({"ok": False, "error": e.code}), e.status

    payload = {}
    if photo_url:
        payload["photo"] = photo_url
        payload["thumb"] = thumb_url

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


@field_bp.route("/field/admin/threads/<device_id>", methods=["GET"])
@admin_required
def field_admin_threads(device_id):
    """Liste des fils de conversation pour une tablette. Un fil = un message
    racine (thread_id = null) + toutes ses reponses. On renvoie les racines
    avec un resume : dernier message, compteur non lus admin, photo si
    presente dans le fil."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_device_id"}), 400
    db = _get_mongo_db()

    # Toutes les racines (pas de thread_id) pour ce device
    roots_cursor = db["field_messages"].find({
        "device_id": oid,
        "$or": [{"thread_id": None}, {"thread_id": {"$exists": False}}],
    }).sort("createdAt", -1).limit(200)
    roots = list(roots_cursor)

    threads = []
    for root in roots:
        root_id = root["_id"]
        # Tous les messages du fil (racine + replies)
        msgs = list(db["field_messages"].find({
            "$or": [{"_id": root_id}, {"thread_id": root_id}],
        }).sort("createdAt", 1))
        if not msgs:
            continue
        last = msgs[-1]
        unread = sum(
            1 for m in msgs
            if m.get("direction") == "field_to_cockpit"
            and not m.get("admin_read_at")
        )
        # Premiere photo trouvee dans le fil (pour thumbnail)
        photo = None
        for m in msgs:
            p = (m.get("payload") or {}).get("photo")
            if p:
                photo = p
                break
        threads.append({
            "root_id": str(root_id),
            "title": root.get("title") or "(sans titre)",
            "type": root.get("type"),
            "direction": root.get("direction", "cockpit_to_field"),
            "priority": root.get("priority", "normal"),
            "created_at": _iso(root.get("createdAt")),
            "last_at": _iso(last.get("createdAt")),
            "last_preview": (last.get("body") or last.get("title") or "")[:140],
            "last_direction": last.get("direction", "cockpit_to_field"),
            "reply_count": max(0, len(msgs) - 1),
            "unread": unread,
            "photo": photo,
        })
    # Tri : non-lus d'abord, puis par dernier message (plus recent en haut)
    threads.sort(key=lambda t: (0 if t["unread"] else 1, t["last_at"] or ""), reverse=False)
    # reverse sur last_at : on veut recent en haut, donc on trie avec une cle custom
    threads.sort(key=lambda t: t["last_at"] or "", reverse=True)
    threads.sort(key=lambda t: 0 if t["unread"] else 1)
    return jsonify({"ok": True, "threads": threads})


@field_bp.route("/field/admin/conversation/<device_id>", methods=["GET"])
@admin_required
def field_admin_conversation(device_id):
    """Deprecated : conserve pour compat (fil unique tous messages confondus).
    La console dispatch utilise maintenant /field/admin/threads."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_device_id"}), 400
    db = _get_mongo_db()
    cursor = db["field_messages"].find({"device_id": oid}).sort("createdAt", 1).limit(500)
    messages = [_pub_message_admin(m) for m in cursor]
    unread = db["field_messages"].count_documents({
        "device_id": oid,
        "direction": "field_to_cockpit",
        "$or": [{"admin_read_at": None}, {"admin_read_at": {"$exists": False}}],
    })
    return jsonify({"ok": True, "messages": messages, "unread_inbound": unread})


@field_bp.route("/field/admin/thread/<msg_id>/mark-read", methods=["POST"])
@admin_required
def field_admin_thread_mark_read(msg_id):
    """Marque les messages inbound d'un fil comme lus par l'admin."""
    try:
        oid = ObjectId(msg_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    original = db["field_messages"].find_one({"_id": oid})
    if not original:
        return jsonify({"ok": False, "error": "not_found"}), 404
    thread_id = original.get("thread_id") or oid
    res = db["field_messages"].update_many(
        {
            "$and": [
                {"$or": [{"_id": thread_id}, {"thread_id": thread_id}]},
                {"direction": "field_to_cockpit"},
                {"$or": [{"admin_read_at": None}, {"admin_read_at": {"$exists": False}}]},
            ],
        },
        {"$set": {"admin_read_at": _now()}},
    )
    return jsonify({"ok": True, "updated": res.modified_count})


@field_bp.route("/field/admin/conversation/<device_id>/mark-read", methods=["POST"])
@admin_required
def field_admin_conversation_mark_read(device_id):
    """Marque tous les messages entrants (field->cockpit) comme lus par un admin.
    N'affecte pas le champ ack_at cote tablette (qui sert au ack cote device)."""
    try:
        oid = ObjectId(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_device_id"}), 400
    db = _get_mongo_db()
    res = db["field_messages"].update_many(
        {
            "device_id": oid,
            "direction": "field_to_cockpit",
            "$or": [{"admin_read_at": None}, {"admin_read_at": {"$exists": False}}],
        },
        {"$set": {"admin_read_at": _now()}},
    )
    return jsonify({"ok": True, "updated": res.modified_count})


@field_bp.route("/field/admin/unread-by-device", methods=["GET"])
@admin_required
def field_admin_unread_by_device():
    """Retourne le nombre de messages entrants non lus par admin, groupe par
    device_id. Utilise pour afficher les badges dans la table des tablettes."""
    db = _get_mongo_db()
    pipeline = [
        {"$match": {
            "direction": "field_to_cockpit",
            "$or": [{"admin_read_at": None}, {"admin_read_at": {"$exists": False}}],
        }},
        {"$group": {"_id": "$device_id", "count": {"$sum": 1}}},
    ]
    out = {}
    for row in db["field_messages"].aggregate(pipeline):
        if row.get("_id"):
            out[str(row["_id"])] = row.get("count", 0)
    return jsonify({"ok": True, "unread": out})


@field_bp.route("/field/admin/thread/<msg_id>", methods=["GET"])
@admin_required
def field_admin_thread(msg_id):
    """Retourne tous les messages d'un thread pour le cockpit."""
    try:
        oid = ObjectId(msg_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400

    db = _get_mongo_db()
    original = db["field_messages"].find_one({"_id": oid})
    if not original:
        return jsonify({"ok": False, "error": "not_found"}), 404

    thread_id = original.get("thread_id") or oid
    cursor = db["field_messages"].find({
        "$or": [
            {"_id": thread_id},
            {"thread_id": thread_id},
        ],
    }).sort("createdAt", 1)

    messages = [_pub_message_admin(m) for m in cursor]
    return jsonify({"ok": True, "thread_id": str(thread_id), "messages": messages})


@field_bp.route("/field/admin/reply/<msg_id>", methods=["POST"])
@admin_required
def field_admin_reply(msg_id):
    """Reponse du cockpit dans un thread existant. Multipart (body + photo)."""
    try:
        oid = ObjectId(msg_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400

    db = _get_mongo_db()
    original = db["field_messages"].find_one({"_id": oid})
    if not original:
        return jsonify({"ok": False, "error": "not_found"}), 404

    thread_id = original.get("thread_id") or oid

    # Le device cible est celui du message original
    device_id = original.get("device_id")
    device_name = original.get("device_name")
    event = original.get("event", "")
    year = original.get("year", "")
    if not device_id:
        return jsonify({"ok": False, "error": "no_target"}), 400

    is_multipart = request.content_type and "multipart" in request.content_type
    if is_multipart:
        body_text = (request.form.get("body") or "").strip()
        photo_file = request.files.get("photo")
    else:
        data = request.get_json(silent=True) or {}
        body_text = (data.get("body") or "").strip()
        photo_file = None

    if not body_text and not (photo_file and photo_file.filename):
        return jsonify({"ok": False, "error": "empty_reply"}), 400
    if body_text and len(body_text) > 4000:
        return jsonify({"ok": False, "error": "body_too_long"}), 400

    # Handle photo
    photo_url = None
    thumb_url = None
    if photo_file and photo_file.filename:
        sub_dir = os.path.join(str(event), str(year))
        try:
            photo_url, thumb_url = _process_and_save_photo(photo_file, sub_dir)
        except PhotoUploadError as e:
            return jsonify({"ok": False, "error": e.code}), e.status

    payload = {}
    if photo_url:
        payload["photo"] = photo_url
        payload["thumb"] = thumb_url

    now = _now()
    sender_email = (getattr(request, "admin_user", None) or {}).get("email", "?")
    reply_doc = {
        "device_id": device_id,
        "device_name": device_name,
        "event": event,
        "year": year,
        "type": original.get("type", "info"),
        "title": "",
        "body": body_text,
        "payload": payload,
        "priority": "normal",
        "from": sender_email,
        "direction": "cockpit_to_field",
        "thread_id": thread_id,
        "createdAt": now,
        "expiresAt": now + timedelta(seconds=INBOX_MESSAGE_TTL_SECONDS),
        "ack_at": None,
    }
    db["field_messages"].insert_one(reply_doc)

    # Incrementer le reply_count sur le message racine
    db["field_messages"].update_one(
        {"_id": thread_id},
        {"$inc": {"reply_count": 1}},
    )

    return jsonify({"ok": True, "id": str(reply_doc["_id"])})


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


@field_bp.route("/field/resources/3p/photo/thumb/<filename>", methods=["GET"])
@field_token_required
def field_3p_photo_thumb(filename):
    """Miniature d'une photo 3P. Miroir de /api/3p/photo/thumb cote tablette."""
    safe = os.path.basename(filename)
    thumb_dir = os.path.join(LOOKER_3P_MEDIA_DIR, "thumbnails")
    if not os.path.isfile(os.path.join(thumb_dir, safe)):
        abort(404)
    resp = send_from_directory(thumb_dir, safe)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@field_bp.route("/field/resources/3p/photo/original/<filename>", methods=["GET"])
@field_token_required
def field_3p_photo_original(filename):
    """Photo originale 3P avec fallback sur miniature si absente."""
    safe = os.path.basename(filename)
    orig_dir = os.path.join(LOOKER_3P_MEDIA_DIR, "original")
    thumb_dir = os.path.join(LOOKER_3P_MEDIA_DIR, "thumbnails")
    if os.path.isfile(os.path.join(orig_dir, safe)):
        resp = send_from_directory(orig_dir, safe)
    elif os.path.isfile(os.path.join(thumb_dir, safe)):
        resp = send_from_directory(thumb_dir, safe)
    else:
        abort(404)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


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
        "door_front": "#60a5fa",
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

    # Demande de flux video en attente : si un operateur PC org a demande
    # un stream, on remonte l'info pour que la tablette affiche la modale
    # de consentement. Meme pattern que tracking_mode.
    pending_stream = None
    psr_id = dev_fresh.get("pending_stream_request")
    if psr_id:
        s = db["field_streams"].find_one({"_id": psr_id})
        if s and _stream_status_active(s.get("status")):
            slot = None
            if s.get("slot_id"):
                slot = db["field_stream_slots"].find_one({"_id": s["slot_id"]})
            pending_stream = {
                "stream_id": str(s["_id"]),
                "status": s.get("status"),
                "requested_by": s.get("requested_by"),
                "requested_at": _iso(s.get("requested_at")) if s.get("requested_at") else None,
                "expires_at": _iso(s.get("expires_at")) if s.get("expires_at") else None,
                "max_duration_s": s.get("max_duration_s") or FIELD_STREAM_MAX_DURATION_S,
                "slot_label": _slot_label(slot["slot_index"]) if slot else None,
            }
        else:
            # Stream stale -> nettoyer le champ device
            db["field_devices"].update_one(
                {"_id": device["_id"]}, {"$set": {"pending_stream_request": None}}
            )

    return jsonify({
        "open": open_list,
        "closed": closed_list,
        "device_name": name,
        "device_status": dev_fresh.get("status") or "patrouille",
        "active_fiche_id": dev_fresh.get("active_fiche_id"),
        "tracking_mode": tracking_mode,
        "pending_stream_request": pending_stream,
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
    raw_history = fiche.get("comment_history") or []
    comment_history = []
    for h in raw_history:
        entry = dict(h)
        if isinstance(entry.get("ts"), datetime):
            entry["ts"] = _iso(entry["ts"])
        comment_history.append(entry)

    return jsonify({
        "ok": True,
        "id": str(fiche["_id"]),
        "category": fiche.get("category"),
        "text": fiche.get("text") or "",
        "text_full": fiche.get("text_full") or "",
        "comment": fiche.get("comment") or "",
        "comment_history": comment_history,
        "niveau_urgence": fiche.get("niveau_urgence"),
        "ts": _iso(ts),
        "close_ts": _iso(close_ts),
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
    codes = []
    if is_multipart:
        comment = (request.form.get("comment") or "").strip()
        # Multi-photos : "photos" en getlist, fallback "photo" pour compat
        photo_files = [pf for pf in request.files.getlist("photos") if pf and pf.filename]
        if not photo_files:
            single = request.files.get("photo")
            if single and single.filename:
                photo_files = [single]
    else:
        data = request.get_json(silent=True) or {}
        comment = (data.get("comment") or "").strip()
        photo_files = []
        # Codes scannes optionnels : meme structure que /field/scan/send.
        if data.get("codes"):
            try:
                codes = _normalize_scan_codes(data.get("codes"))
            except ValueError as e:
                return jsonify({"ok": False, "error": str(e)}), 400

    if not comment and not photo_files and not codes:
        return jsonify({"ok": False, "error": "empty_comment"}), 400
    if comment and len(comment) > 2000:
        return jsonify({"ok": False, "error": "comment_too_long"}), 400
    if len(photo_files) > FIELD_PHOTO_MAX_PER_BATCH:
        return jsonify({"ok": False, "error": "too_many_photos"}), 400

    db = _get_mongo_db()
    fiche = db["pcorg"].find_one({"_id": fiche_id})
    if not fiche:
        return jsonify({"ok": False, "error": "not_found"}), 404

    cc = fiche.get("content_category") or {}
    if (cc.get("patrouille") or "") != name:
        return jsonify({"ok": False, "error": "not_assigned"}), 403

    # Handle photo uploads (multi)
    photos_meta = []
    if photo_files:
        event = device.get("event") or "unknown"
        year = device.get("year") or "unknown"
        sub_dir = os.path.join(str(event), str(year))
        for pf in photo_files:
            try:
                url, thumb = _process_and_save_photo(pf, sub_dir)
            except PhotoUploadError as e:
                return jsonify({"ok": False, "error": e.code, "uploaded": photos_meta}), e.status
            photos_meta.append({"photo": url, "thumb": thumb})

    entry = {
        "ts": _now(),
        "text": comment or "",
        "operator": "field:" + name,
    }
    if photos_meta:
        entry["photos"] = photos_meta
        # Back-compat : 1ere photo aussi en champs plats
        entry["photo"] = photos_meta[0]["photo"]
        entry["thumb"] = photos_meta[0]["thumb"]
    if codes:
        entry["codes"] = codes

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
    return jsonify({
        "ok": True,
        "photos": photos_meta,
        "photo": photos_meta[0]["photo"] if photos_meta else None,
        "thumb": photos_meta[0]["thumb"] if photos_meta else None,
        "codes": codes,
    })


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
    """Declenche une alerte SOS.
    - Cree une fiche PCO Secours/UA
    - Insere une alerte cockpit (cockpit_active_alerts) avec dedup unique par SOS
    - Broadcast un message SOS a toutes les autres tablettes connectees
    Pas de note : le SOS doit etre le plus rapide possible (un tap + confirm)."""
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

    now = _now()
    now_local = _now_local()
    # Expiration longue : 24h (l'alerte reste jusqu'a acquittement ou expiry)
    expires = now + timedelta(hours=24)

    db = _get_mongo_db()

    # 1) Creer une fiche PCO automatique (categorie PCO.Secours, niveau UA)
    fiche_id = None
    try:
        import uuid as _uuid
        ts_str = now_local.isoformat()
        text_lines = ["SOS tablette : " + name]
        if lat is not None and lng is not None:
            text_lines.append("Position : {:.5f}, {:.5f}".format(lat, lng))
        text = " \u2014 ".join(text_lines)
        # ID unique par SOS (inclut le timestamp pour ne pas dedup)
        seed = "field-sos|{}|{}|{}|{}".format(
            str(device.get("_id")), ts_str, lat or 0, lng or 0
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

    # 2) Dedup unique par SOS (pas par device) pour permettre plusieurs SOS
    dedup_key = "field-sos-{}-{}".format(str(device.get("_id")), now.strftime("%Y%m%d%H%M%S%f"))

    alert = {
        "definition_slug": "field_sos",
        "event": event,
        "year": str(year) if year is not None else None,
        "title": "SOS - " + name,
        "message": "Demande d assistance immediate",
        "timeStr": now.strftime("%H:%M"),
        "dedup_key": dedup_key,
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
            "pcorg_id": fiche_id,
        },
    }

    db["cockpit_active_alerts"].insert_one(alert)

    # 3) Confirmer a la tablette emettrice
    db["field_messages"].insert_one({
        "device_id": device["_id"],
        "device_name": name,
        "event": event,
        "year": str(year) if year is not None else None,
        "type": "alert",
        "title": "SOS envoye",
        "body": "Le cockpit a ete prevenu.",
        "priority": "high",
        "from": "field",
        "createdAt": now,
        "expiresAt": now + timedelta(seconds=INBOX_MESSAGE_TTL_SECONDS),
        "ack_at": None,
    })

    # 4) Broadcast SOS a toutes les autres tablettes du meme event/year
    other_devices = db["field_devices"].find({
        "event": event,
        "year": year,
        "_id": {"$ne": device["_id"]},
    }, {"_id": 1, "name": 1})
    sos_messages = []
    for other in other_devices:
        sos_messages.append({
            "device_id": other["_id"],
            "device_name": other.get("name") or "?",
            "event": event,
            "year": str(year) if year is not None else None,
            "type": "sos_broadcast",
            "title": "SOS - " + name,
            "body": "Demande d assistance immediate",
            "priority": "critical",
            "from": "field",
            "payload": {
                "source_device_id": str(device.get("_id")),
                "source_device_name": name,
                "lat": lat,
                "lng": lng,
                "battery": battery,
                "pcorg_id": fiche_id,
            },
            "createdAt": now,
            "expiresAt": now + timedelta(hours=1),
            "ack_at": None,
        })
    if sos_messages:
        db["field_messages"].insert_many(sos_messages)
        # Push urgent a toutes les autres tablettes (son systeme + vibration renforcee cote SW)
        for m in sos_messages:
            try:
                send_push_to_device(
                    db,
                    m["device_id"],
                    m["title"],
                    m["body"],
                    url="/field",
                    tag="field-sos-" + str(fiche_id),
                    push_type="sos",
                )
            except Exception as e:
                logger.debug("field: sos push failed for device %s: %s", m["device_id"], e)

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
