"""Page et API d'administration du module Formation. Squelette : complete par les taches suivantes."""
from functools import wraps

from flask import Blueprint, jsonify, request

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


def _auteur():
    u = getattr(request, "user_payload", None) or {}
    return u.get("email") or "?"


@formation_admin_bp.post("/api/formation/admin/import")
@_role_required("admin")
def api_import():
    from formation import store
    from formation.importer import importer
    f = request.files.get("fichier")
    if f is None:
        return jsonify({"ok": False, "error": "fichier_manquant"}), 400
    try:
        rapport = importer(store.get_db(), f.read(), _auteur())
    except Exception as exc:
        return jsonify({"ok": False, "error": "fichier_illisible", "detail": str(exc)[:200]}), 400
    return jsonify({"ok": True, "rapport": rapport})
