"""balises_api.py - Points d'entree machine des balises Teltonika.

Appeles par le recepteur TCP (recepteur_balises/recepteur.py, compte
svc-balises) sur la MEME machine, jamais par un navigateur :

  POST /api/balises/ingest     {"source": "...", "messages": [...], "unknown": [...]}
  GET  /api/balises/whitelist  -> {"imeis": [...]}

Securite (meme format que integrations.py) : HMAC-SHA256 du secret
BALISES_INGEST_SECRET (32 caracteres min, environnement uniquement) sur
"<timestamp>.<corps brut>", en-tetes X-Signature-Timestamp (+/- 300 s) et
X-Signature ("sha256=<hex>"). IP source : request.remote_addr dans
BALISES_INGEST_ALLOWED_IPS (defaut 127.0.0.1 et ::1). Derriere IIS,
remote_addr est l'IP reelle du client (waitress trusted_proxy) : une requete
venue de l'exterieur n'est jamais 127.0.0.1. Secret absent = tout refuse.
"""

import ipaddress
import json
import logging
import os

from flask import Blueprint, jsonify, request

import balises_core as B
from integrations import sign, verify, _parse_ip  # noqa: F401  (sign : tests)

logger = logging.getLogger("balises")
balises_bp = Blueprint("balises", __name__)

SECRET_ENV = "BALISES_INGEST_SECRET"
ALLOWED_IPS_ENV = "BALISES_INGEST_ALLOWED_IPS"
SECRET_MIN_LEN = 32
MAX_BODY = 5 * 1024 * 1024
MAX_MESSAGES = 2000


def _db():
    from app import db
    return db


def _secret():
    s = os.getenv(SECRET_ENV, "") or ""
    return s if len(s) >= SECRET_MIN_LEN else ""


def _allowed(ip):
    raw = os.getenv(ALLOWED_IPS_ENV, "") or "127.0.0.1,::1"
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            net = ipaddress.ip_network(part, strict=False)
        except ValueError:
            continue
        if ip is not None and ip.version == net.version and ip in net:
            return True
    return False


def _check(body):
    """None si la requete est admise, sinon (reponse, code)."""
    secret = _secret()
    if not secret:
        return jsonify({"error": "non_configure"}), 503
    ip = _parse_ip(request.remote_addr)
    if not _allowed(ip):
        logger.warning("balises : requete refusee (ip) depuis %s", request.remote_addr)
        return jsonify({"error": "ip_refusee"}), 403
    ok, code = verify(secret, request.headers.get("X-Signature-Timestamp"),
                      request.headers.get("X-Signature"), body)
    if not ok:
        logger.warning("balises : requete refusee (%s) depuis %s", code, request.remote_addr)
        return jsonify({"error": code}), 401
    return None


@balises_bp.route("/api/balises/ingest", methods=["POST"])
def ingest():
    if (request.content_length or 0) > MAX_BODY:
        return jsonify({"error": "trop_gros"}), 413
    body = request.get_data(cache=False) or b""
    refused = _check(body)
    if refused:
        return refused
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return jsonify({"error": "json_invalide"}), 400
    messages = payload.get("messages") or []
    if not isinstance(messages, list) or len(messages) > MAX_MESSAGES:
        return jsonify({"error": "messages_invalides"}), 400
    source = str(payload.get("source") or "teltonika-tcp")[:40]
    db = _db()
    for u in (payload.get("unknown") or [])[:200]:
        if isinstance(u, dict):
            B.note_unknown(db, u.get("imei"), source, u.get("peer"))
    stats = B.ingest(db, messages, source)
    db[B.COL_CONFIG].update_one({"_id": "ingest:" + source}, {"$set": {
        "last_post_at": B._now(), "last_stats": stats}, "$inc": {"posts": 1}}, upsert=True)
    return jsonify({"ok": True, **stats})


@balises_bp.route("/api/balises/whitelist", methods=["GET"])
def whitelist():
    refused = _check(b"")
    if refused:
        return refused
    return jsonify({"imeis": B.whitelist(_db())})
