# crise_auth.py - Blueprint Flask d'authentification PIN pour les exercices
# de gestion de crise (sous-arbre cockpit/crise/<exercise_id>/).
#
# Protege l'acces aux ressources "animateur" :
#   - master.html (la console maitre, anciennement index.html du dashboard)
#   - files/<*>   (les 11 fiches d'animation)
#   - input/<*>   (les medias d'inject : photos, videos, PDF)
#
# Reste public :
#   - /crise/                          (hub des exercices)
#   - /crise/<exercise>/               (landing 3 stations)
#   - /crise/<exercise>/player.html    (vue participant)
#   - /crise/<exercise>/livefeed.html  (mur d'images)
#   - /crise/assets/<*>                (logo)
#
# Modele :
#   - 1 PIN 8 chiffres par exercice (stocke hashe en MongoDB)
#   - Hash : werkzeug pbkdf2:sha256:600000 (~ 100 ms / essai cote serveur)
#   - JWT HS256 en cookie httpOnly + Secure (prod) + Path=/crise/<exercise>/
#   - Anti-bruteforce :
#       * 0-2 echecs / 15 min  : autorise immediatement
#       * 3+ echecs / 15 min   : delai exponentiel cote serveur (1, 2, 4, 8 s)
#       * 6+ echecs / 15 min   : lockout 1 h sur l'IP pour cet exercice
#   - Toutes les tentatives loggees (audit RETEX) avec TTL 1 h
#
# Variables d'environnement :
#   - CRISE_JWT_SECRET (obligatoire en prod) : cle HS256 (>= 32 bytes hex)
#   - CRISE_JWT_TTL_HOURS (default 8) : duree du token
#   - CRISE_PIN_LOCKOUT_THRESHOLD (default 6)
#   - CRISE_PIN_LOCKOUT_WINDOW_MIN (default 15)
#   - CRISE_PIN_LOCKOUT_DURATION_MIN (default 60)
#
# Init du PIN d'un exercice : `python scripts/init_crise_pin.py`
#
# Le blueprint conserve la CSRF protection de Cockpit pour le POST /auth
# (template injecte {{ csrf_token() }} et le JS l'envoie via header X-CSRFToken).

from flask import (
    Blueprint, request, jsonify, redirect, send_from_directory,
    make_response, render_template, url_for
)
from werkzeug.security import check_password_hash
from werkzeug.utils import safe_join
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import os
import re
import json
import time
import logging
import uuid

import jwt as pyjwt
from flask_wtf.csrf import generate_csrf


logger = logging.getLogger("crise_auth")


# ---------------------------------------------------------------------------
# Constantes / configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CRISE_ROOT = os.path.join(SCRIPT_DIR, "crise")

DEV_MODE = os.getenv("TITAN_ENV", "dev") != "prod"

# Fallback dev (cohérent avec le pattern Cockpit pour JWT_SECRET / SECRET_KEY).
# En prod, le RuntimeError plus bas refuse le démarrage si la var est absente.
JWT_SECRET = os.getenv(
    "CRISE_JWT_SECRET",
    "CHANGE_ME_IN_DEV_CRISE_JWT" if DEV_MODE else "",
)
JWT_ALGO = "HS256"
JWT_ISSUER = "cockpit-crise"
JWT_TTL_HOURS = int(os.getenv("CRISE_JWT_TTL_HOURS", "8"))

LOCKOUT_THRESHOLD = int(os.getenv("CRISE_PIN_LOCKOUT_THRESHOLD", "6"))
LOCKOUT_WINDOW_MIN = int(os.getenv("CRISE_PIN_LOCKOUT_WINDOW_MIN", "15"))
LOCKOUT_DURATION_MIN = int(os.getenv("CRISE_PIN_LOCKOUT_DURATION_MIN", "60"))

EXERCISE_ID_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")
PIN_RE = re.compile(r"^\d{8}$")

COOKIE_NAME = "crise_session"


# Refus de demarrage en prod si JWT_SECRET absent
if not DEV_MODE and not JWT_SECRET:
    raise RuntimeError(
        "CRISE_JWT_SECRET must be set in production. "
        "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )


crise_auth_bp = Blueprint("crise_auth", __name__, url_prefix="/crise")


# ---------------------------------------------------------------------------
# Indexes MongoDB (lazy, premier acces)
# ---------------------------------------------------------------------------

_INDEXES_READY = False


def _get_db():
    # Reutilise la connexion centrale de app.py
    from app import db
    return db


def _ensure_indexes():
    global _INDEXES_READY
    if _INDEXES_READY:
        return
    try:
        db = _get_db()
        db["crise_config"].create_index("exercise_id", unique=True)
        db["crise_auth_attempts"].create_index([("exercise_id", 1), ("ip", 1), ("ts", -1)])
        # Auto-purge des tentatives apres 1 h
        db["crise_auth_attempts"].create_index("ts", expireAfterSeconds=3600)
        # Live feed regie : state singleton par exercice + audit append-only TTL 7j
        db["crise_livefeed_state"].create_index("exercise_id", unique=True)
        db["crise_livefeed_audit"].create_index([("exercise_id", 1), ("ts", -1)])
        db["crise_livefeed_audit"].create_index("ts", expireAfterSeconds=7 * 24 * 3600)
    except Exception as exc:
        logger.warning("crise_auth: index creation failed: %s", exc)
    _INDEXES_READY = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "?"


@lru_cache(maxsize=64)
def _exercise_dir(exercise_id):
    """Chemin absolu du dossier de l'exercice (cache)."""
    return os.path.join(CRISE_ROOT, exercise_id)


def _validate_exercise_id(exercise_id):
    """Retourne True si exercise_id matche le regex ET correspond a un dossier existant."""
    if not isinstance(exercise_id, str) or not EXERCISE_ID_RE.match(exercise_id):
        return False
    folder = _exercise_dir(exercise_id)
    return os.path.isdir(folder)


def _count_recent_failures(exercise_id, ip, window_min):
    db = _get_db()
    since = _now() - timedelta(minutes=window_min)
    return db["crise_auth_attempts"].count_documents({
        "exercise_id": exercise_id,
        "ip": ip,
        "success": False,
        "ts": {"$gte": since},
    })


def _is_locked_out(exercise_id, ip):
    """
    Retourne (locked, secs_left).
    Lockout = au moins LOCKOUT_THRESHOLD echecs dans la fenetre LOCKOUT_WINDOW_MIN.
    Le lockout dure LOCKOUT_DURATION_MIN a partir du dernier echec qui a declenche le seuil.
    """
    db = _get_db()
    since = _now() - timedelta(minutes=LOCKOUT_DURATION_MIN)
    recent_failures = list(
        db["crise_auth_attempts"]
        .find({
            "exercise_id": exercise_id,
            "ip": ip,
            "success": False,
            "ts": {"$gte": since},
        })
        .sort("ts", -1)
        .limit(LOCKOUT_THRESHOLD * 2)
    )
    if len(recent_failures) < LOCKOUT_THRESHOLD:
        return False, 0

    # On verifie qu'il y a bien LOCKOUT_THRESHOLD echecs dans la fenetre LOCKOUT_WINDOW_MIN
    window_start = _now() - timedelta(minutes=LOCKOUT_WINDOW_MIN)
    in_window = [a for a in recent_failures if a["ts"] >= window_start]
    if len(in_window) < LOCKOUT_THRESHOLD:
        return False, 0

    # Le lockout commence au LOCKOUT_THRESHOLD-eme echec dans la fenetre
    threshold_failure = in_window[LOCKOUT_THRESHOLD - 1]  # le plus recent est en [0]
    unlock_at = threshold_failure["ts"] + timedelta(minutes=LOCKOUT_DURATION_MIN)
    if _now() >= unlock_at:
        return False, 0
    return True, int((unlock_at - _now()).total_seconds())


def _log_attempt(exercise_id, ip, success):
    try:
        _get_db()["crise_auth_attempts"].insert_one({
            "exercise_id": exercise_id,
            "ip": ip,
            "ts": _now(),
            "success": bool(success),
            "ua": (request.headers.get("User-Agent") or "")[:200],
        })
    except Exception as exc:
        logger.warning("crise_auth: attempt log failed: %s", exc)


def _make_jwt(exercise_id):
    if not JWT_SECRET:
        return None
    now = _now()
    payload = {
        "iss": JWT_ISSUER,
        "sub": "crise-master",
        "exercise": exercise_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_TTL_HOURS)).timestamp()),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def _verify_jwt(token, exercise_id):
    if not token or not JWT_SECRET:
        return False
    try:
        payload = pyjwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGO], issuer=JWT_ISSUER
        )
        if payload.get("sub") != "crise-master":
            return False
        if payload.get("exercise") != exercise_id:
            return False
        return True
    except Exception:
        return False


def _is_authenticated(exercise_id):
    return _verify_jwt(request.cookies.get(COOKIE_NAME), exercise_id)


def _backoff_delay(exercise_id, ip):
    """Delai exponentiel cote serveur a partir de 3 echecs : 1, 2, 4, 8 s (cap)."""
    n = _count_recent_failures(exercise_id, ip, LOCKOUT_WINDOW_MIN)
    if n < 3:
        return
    delay = min(2 ** (n - 2), 8)
    time.sleep(delay)


def _require_auth(exercise_id):
    """Retourne une Response (redirect) si non authentifie, None si OK."""
    if not _validate_exercise_id(exercise_id):
        return ("Not found", 404)
    if not _is_authenticated(exercise_id):
        return redirect(f"/crise/{exercise_id}/auth")
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@crise_auth_bp.route("/<exercise_id>/auth", methods=["GET"])
def auth_page(exercise_id):
    if not _validate_exercise_id(exercise_id):
        return ("Not found", 404)
    _ensure_indexes()
    ip = _client_ip()
    locked, secs_left = _is_locked_out(exercise_id, ip)
    # Si deja authentifie -> redirige vers master directement
    if not locked and _is_authenticated(exercise_id):
        return redirect(f"/crise/{exercise_id}/master.html")
    return render_template(
        "crise_auth.html",
        exercise_id=exercise_id,
        locked=locked,
        secs_left=secs_left,
    )


@crise_auth_bp.route("/<exercise_id>/auth", methods=["POST"])
def auth_submit(exercise_id):
    if not _validate_exercise_id(exercise_id):
        return jsonify({"ok": False, "error": "not_found"}), 404
    _ensure_indexes()
    ip = _client_ip()

    locked, secs_left = _is_locked_out(exercise_id, ip)
    if locked:
        # On ne logge pas une tentative supplementaire ici pour ne pas allonger
        # indefiniment le lockout en bouclant : la tentative de submit pendant
        # lockout ne compte pas. On retourne juste 429.
        return jsonify({"ok": False, "error": "locked_out", "retry_after": secs_left}), 429

    # Lecture du PIN (form ou JSON, peu importe)
    pin = ""
    if request.is_json:
        body = request.get_json(silent=True) or {}
        pin = str(body.get("pin", ""))
    else:
        pin = request.form.get("pin", "")

    if not isinstance(pin, str) or not PIN_RE.match(pin):
        _log_attempt(exercise_id, ip, False)
        _backoff_delay(exercise_id, ip)
        return jsonify({"ok": False, "error": "invalid_pin"}), 401

    db = _get_db()
    config = db["crise_config"].find_one({"exercise_id": exercise_id})
    if not config or not config.get("pin_hash"):
        # Pas de PIN configure -> erreur 503 explicite (jamais d'acces sans PIN)
        return jsonify({"ok": False, "error": "not_configured"}), 503

    is_valid = False
    try:
        is_valid = check_password_hash(config["pin_hash"], pin)
    except Exception as exc:
        logger.warning("crise_auth: hash check error: %s", exc)
        is_valid = False

    _log_attempt(exercise_id, ip, is_valid)

    if not is_valid:
        _backoff_delay(exercise_id, ip)
        # Le client peut maintenant etre lockout : on re-check
        new_locked, new_secs = _is_locked_out(exercise_id, ip)
        if new_locked:
            return jsonify({"ok": False, "error": "locked_out", "retry_after": new_secs}), 429
        return jsonify({"ok": False, "error": "invalid_pin"}), 401

    token = _make_jwt(exercise_id)
    if not token:
        return jsonify({"ok": False, "error": "server_error"}), 500

    resp = make_response(jsonify({
        "ok": True,
        "redirect": f"/crise/{exercise_id}/master.html",
    }))
    resp.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=not DEV_MODE,
        samesite="Lax",
        path=f"/crise/{exercise_id}/",
        max_age=JWT_TTL_HOURS * 3600,
    )
    return resp


@crise_auth_bp.route("/<exercise_id>/auth/logout", methods=["GET", "POST"])
def auth_logout(exercise_id):
    if not _validate_exercise_id(exercise_id):
        return ("Not found", 404)
    resp = make_response(redirect(f"/crise/{exercise_id}/auth"))
    resp.set_cookie(
        COOKIE_NAME, "",
        expires=0, max_age=0,
        path=f"/crise/{exercise_id}/",
        httponly=True, secure=not DEV_MODE, samesite="Lax",
    )
    return resp


# --- Ressources protegees -----------------------------------------------------

@crise_auth_bp.route("/<exercise_id>/master.html", methods=["GET"])
def serve_master(exercise_id):
    blocked = _require_auth(exercise_id)
    if blocked is not None:
        return blocked
    folder = _exercise_dir(exercise_id)
    if not os.path.isfile(os.path.join(folder, "master.html")):
        return ("Not found", 404)
    resp = make_response(send_from_directory(folder, "master.html"))
    # No-cache pour eviter qu'un proxy garde la page apres expiration du JWT
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


# Allowlist : fiches accessibles SANS PIN (vues par les participants en mode
# player.html). Toutes les autres fiches restent gated par le JWT animateur.
PUBLIC_FILES_ALLOWLIST = frozenset({
    "00_consignes_cellule.html",
    "00_consignes_pcorg.html",
    "00_consignes_pca.html",
})


@crise_auth_bp.route("/<exercise_id>/files/<path:filename>", methods=["GET"])
def serve_files(exercise_id, filename):
    if any(part.startswith(".") for part in filename.split("/") if part):
        return ("Not found", 404)
    # Allowlist publique pour les consignes (mode participant)
    if filename in PUBLIC_FILES_ALLOWLIST:
        if not _validate_exercise_id(exercise_id):
            return ("Not found", 404)
    else:
        blocked = _require_auth(exercise_id)
        if blocked is not None:
            return blocked
    folder = os.path.join(_exercise_dir(exercise_id), "files")
    candidate = safe_join(folder, filename)
    if not candidate or not os.path.isfile(candidate):
        return ("Not found", 404)
    resp = make_response(send_from_directory(folder, filename))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@crise_auth_bp.route("/<exercise_id>/input/<path:filename>", methods=["GET"])
def serve_input(exercise_id, filename):
    # Public : livefeed.html (sans PIN) doit pouvoir afficher les medias.
    # Le livefeed est de toute facon visible dans la salle et le manifeste
    # public expose deja la liste des fichiers. La regie reste gated par PIN.
    if not _validate_exercise_id(exercise_id):
        return ("Not found", 404)
    if any(part.startswith(".") for part in filename.split("/") if part):
        return ("Not found", 404)
    folder = os.path.join(_exercise_dir(exercise_id), "input")
    candidate = safe_join(folder, filename)
    if not candidate or not os.path.isfile(candidate):
        return ("Not found", 404)
    resp = make_response(send_from_directory(folder, filename))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


# =========================================================================
# Live feed regie -- pilotage en temps reel d'un mur d'images TV depuis
# master.html. Ecritures gated par JWT animateur + CSRF, lectures publiques.
# =========================================================================

VALID_LEVELS = {"info", "warning", "alert", "critical"}
VALID_TYPES = {"input", "message", "idle"}
VALID_ANNOUNCE = {"alert", "notification", "none"}
MAX_TITLE_LEN = 120
MAX_BODY_LEN = 1500
MAX_DURATION_S = 1800
TV_CLIENT_ACTIVE_WINDOW_S = 30  # un client est "actif" si vu < 30s


def _normalize_announce(value):
    """Accepte l'enum nouveau ('alert'|'notification'|'none') ET les anciens
    booleens (rentrocompat). Retourne (string, None) ou (None, error)."""
    if isinstance(value, bool):
        return ("alert" if value else "none"), None
    if isinstance(value, str) and value in VALID_ANNOUNCE:
        return value, None
    return None, "invalid announce (expected 'alert'|'notification'|'none')"


def _require_auth_api(exercise_id):
    """Variante de _require_auth() qui retourne 401/404 JSON au lieu d'un
    redirect HTML. Utilisee par les routes API de la regie."""
    if not _validate_exercise_id(exercise_id):
        return jsonify({"ok": False, "error": "not_found"}), 404
    if not _is_authenticated(exercise_id):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


@lru_cache(maxsize=8)
def _load_inputs_manifest(exercise_id):
    """Charge livefeed_inputs.json. Cache LRU.
    Retourne un dict {by_id: {id: input_dict}, csv_data: {...}, raw: {...}}
    ou None si absent/invalide."""
    path = os.path.join(_exercise_dir(exercise_id), "livefeed_inputs.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as exc:
        logger.warning("livefeed: manifest load failed for %s: %s", exercise_id, exc)
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("inputs"), list):
        logger.warning("livefeed: manifest invalid structure for %s", exercise_id)
        return None
    by_id = {}
    for entry in raw["inputs"]:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("id")
        if isinstance(eid, int):
            by_id[eid] = entry
    return {"by_id": by_id, "raw": raw}


def _validate_livefeed_payload(payload, manifest):
    """Retourne (sanitized_payload, None) ou (None, error_message)."""
    if not isinstance(payload, dict):
        return None, "payload must be an object"
    ptype = payload.get("type")
    if ptype not in VALID_TYPES:
        return None, "invalid type"

    if ptype == "idle":
        return {"type": "idle"}, None

    announce, err = _normalize_announce(payload.get("announce", "alert"))
    if err:
        return None, err
    duration_s = payload.get("duration_s")
    if duration_s is not None:
        if not isinstance(duration_s, int) or duration_s < 1 or duration_s > MAX_DURATION_S:
            return None, f"duration_s must be int in [1, {MAX_DURATION_S}] or null"

    if ptype == "input":
        input_id = payload.get("input_id")
        if not isinstance(input_id, int):
            return None, "input_id must be int"
        if not manifest or input_id not in manifest["by_id"]:
            return None, "input_id not in manifest"
        entry = manifest["by_id"][input_id]
        media_type = entry.get("type")
        # Les videos bouclent cote TV (loop=true) pendant duration_s, puis
        # auto-clear comme tout autre type. duration_s=None reste possible si
        # l'animateur veut un clear strictement manuel.
        sanitized = {
            "type": "input",
            "input_id": input_id,
            "num": entry.get("num", input_id),
            "file": entry.get("file"),
            "media_type": media_type,  # photo|video|pdf|data
            "label": entry.get("label", ""),
            "announce": announce,
            "duration_s": duration_s,
        }
        return sanitized, None

    if ptype == "message":
        title = payload.get("title", "")
        body = payload.get("body", "")
        level = payload.get("level", "info")
        if not isinstance(title, str) or not title.strip():
            return None, "title must be non-empty string"
        if not isinstance(body, str):
            return None, "body must be string"
        if len(title) > MAX_TITLE_LEN:
            return None, f"title too long (max {MAX_TITLE_LEN})"
        if len(body) > MAX_BODY_LEN:
            return None, f"body too long (max {MAX_BODY_LEN})"
        if level not in VALID_LEVELS:
            return None, "invalid level"
        sanitized = {
            "type": "message",
            "title": title.strip(),
            "body": body,
            "level": level,
            "announce": announce,
            "duration_s": duration_s,
        }
        return sanitized, None

    return None, "unreachable"  # defensif


def _state_doc_to_response(doc, include_clients_for_admin=False):
    """Serialize un doc Mongo state en JSON propre."""
    if doc is None:
        return {
            "version": 0,
            "server_ts": _iso_utc(_now()),
            "payload": {"type": "idle"},
            "tv_clients": [],
        }
    payload = doc.get("payload") or {"type": "idle"}
    # On normalise started_at en ISO si present
    if isinstance(payload.get("started_at"), datetime):
        payload = dict(payload)
        payload["started_at"] = _iso_utc(payload["started_at"])
    clients = []
    if include_clients_for_admin:
        cutoff = _now() - timedelta(seconds=TV_CLIENT_ACTIVE_WINDOW_S)
        for c in (doc.get("tv_clients") or []):
            ls = c.get("last_seen")
            if not isinstance(ls, datetime):
                continue
            # pymongo retourne des datetimes naive (UTC) par defaut. On force
            # tz-aware pour comparer sans TypeError.
            if ls.tzinfo is None:
                ls = ls.replace(tzinfo=timezone.utc)
            if ls < cutoff:
                continue
            cid = c.get("client_id") or ""
            # Ignore les clients regie (master-*) dans le compteur "TV en ligne"
            if cid.startswith("master-"):
                continue
            clients.append({
                "client_id": cid,
                "last_seen": _iso_utc(ls),
                "ua": (c.get("ua") or "")[:80],
            })
    return {
        "version": int(doc.get("version", 0)),
        "server_ts": _iso_utc(doc.get("server_ts") or _now()),  # instant de la derniere modif
        "now_ts": _iso_utc(_now()),                              # instant de la requete (calibration client)
        "payload": payload,
        "tv_clients": clients,
    }


def _iso_utc(dt):
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _audit_livefeed(exercise_id, action, payload, ip):
    try:
        _get_db()["crise_livefeed_audit"].insert_one({
            "exercise_id": exercise_id,
            "ts": _now(),
            "action": action,
            "payload": payload,
            "set_by_ip": ip,
            "ua": (request.headers.get("User-Agent") or "")[:200],
        })
    except Exception as exc:
        logger.warning("livefeed: audit insert failed: %s", exc)


# --- Routes Live feed ----------------------------------------------------

def _auto_expire_state(db, exercise_id):
    """Si un payload non-idle est expire (started_at + duration_s < now), le
    serveur passe atomiquement en idle. Garantit que TV et regie voient le
    clear meme si personne n'a appuye sur Stopper. Pas applicable aux videos
    (duration_s force a None cote validation)."""
    doc = db["crise_livefeed_state"].find_one({"exercise_id": exercise_id})
    if not doc:
        return None
    payload = doc.get("payload") or {}
    if payload.get("type") in (None, "idle"):
        return doc
    duration_s = payload.get("duration_s")
    started = payload.get("started_at")
    if not isinstance(duration_s, int) or duration_s <= 0:
        return doc  # pas d'auto-clear configure (manuel)
    if not isinstance(started, datetime):
        return doc
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    expires_at = started + timedelta(seconds=duration_s)
    if _now() < expires_at:
        return doc
    # Expire : update atomique vers idle. Conditionne sur la version courante
    # pour eviter d'ecraser un set tout frais arrive en parallele.
    new_doc = db["crise_livefeed_state"].find_one_and_update(
        {"exercise_id": exercise_id, "version": doc.get("version", 0)},
        {
            "$set": {
                "payload": {"type": "idle"},
                "server_ts": _now(),
                "set_by_ip": "auto-expire",
                "set_at": _now(),
            },
            "$inc": {"version": 1},
        },
        return_document=True,
    )
    if new_doc:
        try:
            _get_db()["crise_livefeed_audit"].insert_one({
                "exercise_id": exercise_id,
                "ts": _now(),
                "action": "auto_expire",
                "payload": {"type": "idle"},
                "set_by_ip": "auto",
                "ua": "server",
            })
        except Exception:
            pass
        logger.info("livefeed: auto-expire %s (duration_s=%s)", exercise_id, duration_s)
        return new_doc
    return doc


@crise_auth_bp.route("/<exercise_id>/livefeed/state", methods=["GET"])
def livefeed_state_get(exercise_id):
    """Lecture du state courant. PUBLIC (la TV n'a pas de cookie). Met aussi
    a jour l'entree tv_clients[] si ?client=<uuid> est passe (heartbeat
    implicite, gratuit). Auto-clear si payload expire."""
    if not _validate_exercise_id(exercise_id):
        return jsonify({"ok": False, "error": "not_found"}), 404
    _ensure_indexes()
    db = _get_db()

    # Heartbeat TV implicite
    client_id = (request.args.get("client") or "").strip()
    if client_id and re.match(r"^[a-zA-Z0-9_\-]{1,64}$", client_id):
        ua = (request.headers.get("User-Agent") or "")[:80]
        now = _now()
        # Tente de mettre a jour l'entree existante
        upd = db["crise_livefeed_state"].update_one(
            {"exercise_id": exercise_id, "tv_clients.client_id": client_id},
            {"$set": {"tv_clients.$.last_seen": now, "tv_clients.$.ua": ua}},
        )
        if upd.matched_count == 0:
            # Pas trouve -> push (upsert le doc si besoin avec version 0)
            db["crise_livefeed_state"].update_one(
                {"exercise_id": exercise_id},
                {
                    "$setOnInsert": {
                        "exercise_id": exercise_id,
                        "version": 0,
                        "server_ts": now,
                        "payload": {"type": "idle"},
                    },
                    "$push": {"tv_clients": {
                        "client_id": client_id,
                        "last_seen": now,
                        "ua": ua,
                    }},
                },
                upsert=True,
            )

    # Auto-expire si payload expire (atomique, incrementera version si applique)
    doc = _auto_expire_state(db, exercise_id)
    if doc is None:
        doc = db["crise_livefeed_state"].find_one({"exercise_id": exercise_id})
    is_admin = _is_authenticated(exercise_id)
    body = _state_doc_to_response(doc, include_clients_for_admin=is_admin)
    return jsonify({"ok": True, **body})


@crise_auth_bp.route("/<exercise_id>/livefeed/state", methods=["POST"])
def livefeed_state_set(exercise_id):
    """Ecrit le state courant. JWT animateur requis. CSRF active (Flask-WTF)."""
    blocked = _require_auth_api(exercise_id)
    if blocked is not None:
        return blocked
    _ensure_indexes()
    payload_in = request.get_json(silent=True) or {}
    manifest = _load_inputs_manifest(exercise_id)
    sanitized, err = _validate_livefeed_payload(payload_in, manifest)
    if err:
        logger.warning("livefeed: invalid payload from %s for %s: %s",
                       _client_ip(), exercise_id, err)
        return jsonify({"ok": False, "error": "invalid_payload", "detail": err}), 422

    # On marque le started_at cote serveur (heure de mise a l'antenne).
    if sanitized["type"] != "idle":
        sanitized["started_at"] = _now()

    db = _get_db()
    now = _now()
    doc = db["crise_livefeed_state"].find_one_and_update(
        {"exercise_id": exercise_id},
        {
            "$set": {
                "payload": sanitized,
                "server_ts": now,
                "set_by_ip": _client_ip(),
                "set_at": now,
            },
            "$inc": {"version": 1},
            "$setOnInsert": {"exercise_id": exercise_id, "tv_clients": []},
        },
        upsert=True,
        return_document=True,  # = ReturnDocument.AFTER en pymongo
    )

    # Audit
    _audit_livefeed(exercise_id, "set", sanitized, _client_ip())
    logger.info("livefeed: set %s by %s [%s]",
                sanitized.get("type"),
                _client_ip(),
                sanitized.get("input_id") or sanitized.get("title") or "-")

    body = _state_doc_to_response(doc, include_clients_for_admin=True)
    return jsonify({"ok": True, **body})


@crise_auth_bp.route("/<exercise_id>/livefeed/clear", methods=["POST"])
def livefeed_clear(exercise_id):
    """Equivalent a POST /state avec {type:'idle'}. Plus explicite cote regie."""
    blocked = _require_auth_api(exercise_id)
    if blocked is not None:
        return blocked
    _ensure_indexes()

    db = _get_db()
    now = _now()
    sanitized = {"type": "idle"}
    doc = db["crise_livefeed_state"].find_one_and_update(
        {"exercise_id": exercise_id},
        {
            "$set": {
                "payload": sanitized,
                "server_ts": now,
                "set_by_ip": _client_ip(),
                "set_at": now,
            },
            "$inc": {"version": 1},
            "$setOnInsert": {"exercise_id": exercise_id, "tv_clients": []},
        },
        upsert=True,
        return_document=True,
    )
    _audit_livefeed(exercise_id, "clear", sanitized, _client_ip())
    logger.info("livefeed: clear by %s", _client_ip())
    body = _state_doc_to_response(doc, include_clients_for_admin=True)
    return jsonify({"ok": True, **body})


@crise_auth_bp.route("/<exercise_id>/livefeed/csrf", methods=["GET"])
def livefeed_csrf(exercise_id):
    """Retourne un token CSRF utilisable sur les POST. JWT animateur requis."""
    blocked = _require_auth_api(exercise_id)
    if blocked is not None:
        return blocked
    return jsonify({"ok": True, "csrf_token": generate_csrf()})


@crise_auth_bp.route("/<exercise_id>/livefeed/inputs.json", methods=["GET"])
def livefeed_inputs_json(exercise_id):
    """Sert le manifeste des inputs. JWT animateur requis (utile cote regie pour
    eviter de dupliquer la liste cote master.html)."""
    blocked = _require_auth_api(exercise_id)
    if blocked is not None:
        return blocked
    manifest = _load_inputs_manifest(exercise_id)
    if not manifest:
        return jsonify({"ok": False, "error": "manifest_missing"}), 404
    return jsonify({"ok": True, **manifest["raw"]})


@crise_auth_bp.route("/<exercise_id>/regie.js", methods=["GET"])
def serve_regie_js(exercise_id):
    """Sert regie.js avec auth animateur. Pattern equivalent a serve_master."""
    blocked = _require_auth(exercise_id)  # redirect HTML acceptable ici
    if blocked is not None:
        return blocked
    folder = _exercise_dir(exercise_id)
    if not os.path.isfile(os.path.join(folder, "regie.js")):
        return ("Not found", 404)
    resp = make_response(send_from_directory(folder, "regie.js"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    return resp


# ---------------------------------------------------------------------------
# Hardening : pas de strip de cookie ici (contrairement a crise_bp).
# Cet after_request est specifique au blueprint et ajoute juste des headers
# defensifs sur les pages auth et ressources protegees.
# ---------------------------------------------------------------------------

@crise_auth_bp.after_request
def _crise_auth_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response
