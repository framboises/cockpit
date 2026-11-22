"""Page et API d'administration du module Formation. Squelette : complete par les taches suivantes."""
from functools import wraps

from flask import Blueprint, jsonify

formation_admin_bp = Blueprint("formation_admin", __name__)


def _role_required(role):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


@formation_admin_bp.get("/admin/formation")
@_role_required("admin")
def page_admin():
    return jsonify({"ok": True, "module": "formation"})


@formation_admin_bp.get("/api/formation/admin/etat")
@_role_required("admin")
def api_etat():
    return jsonify({"ok": True})
