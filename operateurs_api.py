"""operateurs_api.py - Routes de la prise de poste operateur.

  GET /api/operator/settings  (admin) reglages + couches Looker eligibles
  PUT /api/operator/settings  (admin) couche de zones + propriete de nom (CSRF ACTIF)
  GET /api/operator/zones     (user)  zones de la couche choisie (GeoJSON)
  GET /api/operator/metiers   (user)  metiers de Bibler
  GET /api/operator/post      (user)  {required, post} : poste ouvert du compte
  GET /api/operator/options   (user)  evenements en cours, zones, metiers
  POST /api/operator/post     (user)  prise de poste + fiche (CSRF ACTIF)

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


def _payload():
    return getattr(request, "user_payload", {}) or {}


@operateurs_bp.route("/api/operator/post", methods=["GET"])
@_role_required("user")
def api_post_get():
    from app import user_is_operator
    db = _db()
    payload = _payload()
    post = OP.open_post(db, OP.user_id_for(db, payload.get("email")))
    return jsonify({"ok": True, "required": user_is_operator(payload),
                    "post": OP.public_post(post)})


@operateurs_bp.route("/api/operator/options", methods=["GET"])
@_role_required("user")
def api_options():
    return jsonify({"ok": True, **OP.options(_db())})


@operateurs_bp.route("/api/operator/post", methods=["POST"])
@_role_required("user")
def api_post_take():
    from app import user_is_operator
    db = _db()
    payload = _payload()
    if not user_is_operator(payload):
        return _err("pas_operateur", 403)
    user_id = OP.user_id_for(db, payload.get("email"))
    if not user_id:
        return _err("utilisateur_inconnu", 403)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err("json_attendu")
    choice, err = OP.validate_choice(db, body.get("event"), body.get("year"),
                                     body.get("zones"), body.get("metier_id"))
    if err:
        return _err(err)
    post, created = OP.take_post(db, payload, user_id, choice)
    if created:
        logger.info("Prise de poste %s : %s %s, zones %s, metier %s", payload.get("email"),
                    choice["event"], choice["year"], choice["zones_labels"], choice["metier_label"])
    return jsonify({"ok": True, "created": created, "post": OP.public_post(post)})
