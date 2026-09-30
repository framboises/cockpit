"""momentus_api.py - Reservations Momentus par lieu de la carte (lecture seule).

Routes (user) consommees par static/js/momentus_lieu.js (bouton dans les
popups des lieux de la carte d'accueil) :
  GET /api/momentus/mapped              lieux carto ayant un espace Momentus valide
  GET /api/momentus/carte               calque carte : lieux rattaches ayant des reservations
  GET /api/momentus/lieu/<feature_id>   reservations du lieu (?event=&year= ou ?from=&to=)

Memes routes et meme module de calcul (momentus_lieux.py) que Groundmaster, ou
se fait la correspondance espaces Momentus <-> lieux (/momentus/lieux).
Donnees : collections momentus_* (synchro horaire momentus_sync.py).
Ni contacts ni montants dans les reponses.
"""

import logging
import re
from functools import wraps

from flask import Blueprint, jsonify, request

import momentus_lieux as ML

logger = logging.getLogger(__name__)

momentus_bp = Blueprint("momentus", __name__)


def _db():
    from app import db
    return db


def _role_required(role):
    """role_required d'app.py, resolu a l'appel (cf. pcorg_assist.py)."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


def _err(code, status=400):
    return jsonify({"ok": False, "error": code}), status


@momentus_bp.route("/api/momentus/mapped", methods=["GET"])
@_role_required("user")
def api_mapped():
    try:
        feats = ML.mapped_features(_db())
    except Exception as e:  # la carte ne doit jamais casser pour Momentus
        logger.warning("momentus mapped : %s", e)
        feats = {}
    return jsonify({"ok": True, "features": {k: len(v) for k, v in feats.items()}})


@momentus_bp.route("/api/momentus/carte", methods=["GET"])
@_role_required("user")
def api_carte():
    """Calque Momentus de la carte (?event=&year= ou ?from=&to=)."""
    a = ML.parse_day_arg(request.args.get("from"))
    b = ML.parse_day_arg(request.args.get("to"))
    if a and b and b < a:
        return _err("periode_invalide")
    return jsonify(ML.carte_payload(_db(), a, b, request.args.get("event"), request.args.get("year")))


@momentus_bp.route("/api/momentus/lieu/<feature_id>", methods=["GET"])
@_role_required("user")
def api_lieu(feature_id):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", feature_id or ""):
        return _err("lieu_invalide")
    a = ML.parse_day_arg(request.args.get("from"))
    b = ML.parse_day_arg(request.args.get("to"))
    if a and b and b < a:
        return _err("periode_invalide")
    return jsonify(ML.lieu_payload(_db(), feature_id, a, b,
                                   request.args.get("event"), request.args.get("year")))
