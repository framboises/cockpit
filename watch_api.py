"""Blueprint de l'API montre.

Lecture seule, authentifiee par jeton Bearer revocable. Aucun acces direct a
Mongo depuis l'exterieur : cette couche est la seule surface exposee.
"""

import hashlib
import logging
import secrets
import time
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, request

import watch_state

logger = logging.getLogger(__name__)

watch_bp = Blueprint("watch", __name__, url_prefix="/api/v1/watch")

# Cache du payload. Meme principe que traffic.py : la charge Mongo est bornee
# quel que soit le rythme de polling, sans process supplementaire.
CACHE_TTL_S = 20

# Fenetre glissante par jeton. La montre consomme 5 requetes (app a 1 min) plus
# 1 (background) : la marge absorbe les reprises reseau sans jamais couvrir une
# boucle folle.
RATE_LIMIT_WINDOW_S = 300
RATE_LIMIT_MAX = 60

# Bride d'ecriture de la telemetrie : sans elle, une montre a 1 min de polling
# produirait 1 440 ecritures par jour pour un seul champ d'horodatage.
LAST_USED_THROTTLE_S = 60

_cache = {"at": 0.0, "payload": None}
_rate_log = {}
_indexes_ready = False


def _db():
    """Base de l'environnement courant. Jamais de nom code en dur."""
    from app import db
    return db


def reset_cache():
    _cache["at"] = 0.0
    _cache["payload"] = None


def reset_rate_limit():
    _rate_log.clear()


def _ensure_indexes(db):
    global _indexes_ready
    if _indexes_ready:
        return
    db["watch_tokens"].create_index("token_sha256", unique=True)
    _indexes_ready = True


def _client_ip():
    return request.headers.get(
        "X-Forwarded-For", request.remote_addr or "0.0.0.0"
    ).split(",")[0].strip()


def _maintenant():
    """Naif UTC, coherent avec ce que pymongo rend en lecture sur un client
    non tz_aware (celui de app.py). Comparer ce naif a un datetime conscient
    du fuseau leverait un TypeError des la deuxieme requete dans la fenetre
    de throttle : c'est exactement le bug trouve en verification bout en
    bout. Meme forme que meteo.py (datetime.now(timezone.utc).replace(
    tzinfo=None)), pas datetime.utcnow() qui est deprecie."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(db, label, created_by):
    """Emet un jeton. Le clair est renvoye une seule fois et jamais stocke."""
    _ensure_indexes(db)
    token = secrets.token_urlsafe(32)
    db["watch_tokens"].insert_one({
        "label": label,
        "token_sha256": hash_token(token),
        "created_at": _maintenant(),
        "created_by": created_by,
        "revoked": False,
        "revoked_at": None,
        "last_used_at": None,
        "last_ip": None,
        "use_count": 0,
    })
    return token


def verify_token(db, token):
    """Lookup indexe sur le hash : aucune comparaison de secrets."""
    _ensure_indexes(db)
    if not token:
        return None
    doc = db["watch_tokens"].find_one({"token_sha256": hash_token(token)})
    if not doc or doc.get("revoked"):
        return None
    return doc


def revoke_token(db, token_id):
    from bson import ObjectId
    try:
        oid = ObjectId(token_id)
    except Exception:
        return False
    res = db["watch_tokens"].update_one(
        {"_id": oid},
        {"$set": {"revoked": True,
                  "revoked_at": _maintenant()}},
    )
    return bool(getattr(res, "matched_count", 1))


def _touch_token(db, doc):
    """Telemetrie d'usage, bridee a une ecriture par minute."""
    dernier = doc.get("last_used_at")
    maintenant = _maintenant()
    if dernier is not None:
        if (maintenant - dernier).total_seconds() < LAST_USED_THROTTLE_S:
            return
    db["watch_tokens"].update_one(
        {"_id": doc["_id"]},
        {"$set": {"last_used_at": maintenant, "last_ip": _client_ip()},
         "$inc": {"use_count": 1}},
    )


def _rate_limited(cle):
    maintenant = time.time()
    debut = maintenant - RATE_LIMIT_WINDOW_S
    hist = [t for t in _rate_log.get(cle, []) if t > debut]
    if len(hist) >= RATE_LIMIT_MAX:
        _rate_log[cle] = hist
        return True
    hist.append(maintenant)
    _rate_log[cle] = hist
    if len(_rate_log) > 1000:
        mortes = [k for k, v in _rate_log.items()
                  if not any(t > debut for t in v)]
        for k in mortes:
            _rate_log.pop(k, None)
    return False


def bearer_required(f):
    """Auth par jeton. Repond 401 en JSON, jamais 302.

    @role_required redirige vers le portail, ce qui casserait un appel machine.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        entete = request.headers.get("Authorization", "")
        if not entete.startswith("Bearer "):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        token = entete[7:].strip()

        db = _db()
        doc = verify_token(db, token)
        if doc is None:
            logger.info("Jeton montre refuse depuis %s", _client_ip())
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        if _rate_limited(doc["token_sha256"]):
            reponse = jsonify({"ok": False, "error": "rate_limited"})
            reponse.headers["Retry-After"] = str(RATE_LIMIT_WINDOW_S)
            return reponse, 429

        _touch_token(db, doc)
        request.watch_token = doc
        return f(*args, **kwargs)

    return wrapper


@watch_bp.route("/state", methods=["GET"])
@bearer_required
def state():
    maintenant = time.time()
    if _cache["payload"] is not None and maintenant - _cache["at"] < CACHE_TTL_S:
        return jsonify(_cache["payload"])

    payload = watch_state.build_state(_db(), datetime.now())
    _cache["payload"] = payload
    _cache["at"] = maintenant
    return jsonify(payload)
