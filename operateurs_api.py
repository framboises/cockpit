"""operateurs_api.py - Routes de la prise de poste operateur.

  GET /api/operator/settings  (admin) reglages + couches Looker eligibles
  PUT /api/operator/settings  (admin) couche de zones + propriete de nom (CSRF ACTIF)
  GET /api/operator/zones     (user)  zones de la couche choisie (GeoJSON)
  GET /api/operator/metiers   (user)  metiers de Bibler

Logique dans operateurs.py ; plan : docs/claude/operateurs.md.
"""

import logging
from functools import wraps

from flask import Blueprint, jsonify, request

import operateurs as OP

logger = logging.getLogger(__name__)

operateurs_bp = Blueprint("operateurs", __name__)


def _db():
    from app import db
    return db


def _role_required(role):
    """role_required d'app.py, resolu a l'appel (cf. momentus_api.py)."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


def _err(code, status=400, **extra):
    return jsonify({"ok": False, "error": code, **extra}), status


@operateurs_bp.route("/api/operator/settings", methods=["GET"])
@_role_required("admin")
def api_settings_get():
    db = _db()
    return jsonify({"ok": True, "settings": OP.get_settings(db),
                    "layers": OP.eligible_layers(db)})


@operateurs_bp.route("/api/operator/settings", methods=["PUT"])
@_role_required("admin")
def api_settings_put():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err("json_attendu")
    email = (getattr(request, "user_payload", {}) or {}).get("email")
    settings, err = OP.save_settings(_db(), body, email)
    if err:
        return _err(err)
    logger.info("Operateurs : couche de zones %s (nom %s) par %s",
                settings["zones_collection"], settings["zones_label_field"], email)
    return jsonify({"ok": True, "settings": settings})


@operateurs_bp.route("/api/operator/zones", methods=["GET"])
@_role_required("user")
def api_zones():
    """Zones de la couche enregistree. Admin : `?collection=&label=` = apercu
    d'une couche eligible avant enregistrement."""
    db = _db()
    fresh = request.args.get("fresh") == "1"
    preview = request.args.get("collection")
    if preview:
        payload = getattr(request, "user_payload", {}) or {}
        if not (payload.get("is_super_admin") or payload.get("app_role") == "admin"):
            return _err("admin_requis", 403)
        layer = next((l for l in OP.eligible_layers(db) if l["collection"] == preview), None)
        if not layer:
            return _err("couche_inconnue")
        label = request.args.get("label") or layer["search_field"]
        if label not in layer["properties"]:
            label = None
        return jsonify({"ok": True, "collection": preview,
                        "zones": OP.zones(db, fresh=fresh, collection=preview, label_field=label)})
    st = OP.get_settings(db)
    return jsonify({"ok": True, "collection": st["zones_collection"],
                    "zones": OP.zones(db, fresh=fresh)})


@operateurs_bp.route("/api/operator/metiers", methods=["GET"])
@_role_required("user")
def api_metiers():
    return jsonify({"ok": True, "metiers": OP.metiers(_db())})
