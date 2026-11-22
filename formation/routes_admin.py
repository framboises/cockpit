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


# --- Campagne d'e-mails -----------------------------------------------------

def _json():
    return request.get_json(silent=True) or {}


@formation_admin_bp.post("/api/formation/admin/campagne/essai")
@_role_required("admin")
def api_campagne_essai():
    from formation import mailer, store
    from pcorg_summary_mail import SmtpError
    try:
        mailer.envoyer_essai(store.get_db(), _json().get("adresse"), _auteur())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except SmtpError as exc:
        return jsonify({"ok": False, "error": "smtp", "detail": str(exc)[:200]}), 502
    return jsonify({"ok": True})


@formation_admin_bp.post("/api/formation/admin/campagne/compter")
@_role_required("admin")
def api_campagne_compter():
    from formation import mailer, store
    try:
        n = mailer.compter(store.get_db(), _json().get("cible"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "n": n})


@formation_admin_bp.post("/api/formation/admin/campagne/lancer")
@_role_required("admin")
def api_campagne_lancer():
    from datetime import datetime, timezone
    from formation import mailer, store
    data = _json()
    db = store.get_db()
    try:
        res = mailer.lancer(db, data.get("cible"), data.get("confirmation_n"), _auteur(),
                            datetime.now(timezone.utc))
    except ValueError as exc:
        if exc.args and exc.args[0] == "confirmation_differente":
            return jsonify({"ok": False, "error": "confirmation_differente", "n": exc.args[1]}), 409
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "planifies": res["planifies"], "invalides": res["invalides"],
                    "etat": mailer.fil.etat()})


@formation_admin_bp.post("/api/formation/admin/campagne/arreter")
@_role_required("admin")
def api_campagne_arreter():
    from formation import mailer, store
    mailer.arreter(store.get_db(), _auteur())
    return jsonify({"ok": True, "etat": mailer.fil.etat()})


@formation_admin_bp.post("/api/formation/admin/campagne/reprendre")
@_role_required("admin")
def api_campagne_reprendre():
    from formation import mailer, store
    mailer.reprendre(store.get_db(), _auteur())
    return jsonify({"ok": True, "etat": mailer.fil.etat()})


@formation_admin_bp.post("/api/formation/admin/campagne/renvoyer-echecs")
@_role_required("admin")
def api_campagne_renvoyer_echecs():
    from formation import mailer, store
    db = store.get_db()
    n = mailer.renvoyer_echecs(db, _auteur())
    if n:
        mailer.fil.demarrer(db)
    return jsonify({"ok": True, "n": n, "etat": mailer.fil.etat()})


@formation_admin_bp.get("/api/formation/admin/campagne/etat")
@_role_required("admin")
def api_campagne_etat():
    from formation import mailer
    return jsonify({"ok": True, "etat": mailer.fil.etat()})


@formation_admin_bp.put("/api/formation/admin/gabarit")
@_role_required("admin")
def api_gabarit():
    from formation import store
    data = _json()
    objet, corps = data.get("objet"), data.get("corps")
    if not isinstance(objet, str) or not isinstance(corps, str) or not objet.strip() or not corps.strip():
        return jsonify({"ok": False, "error": "gabarit_invalide"}), 400
    reg = store.maj_reglages(store.get_db(), {"gabarit": {"objet": objet, "corps": corps}}, _auteur())
    return jsonify({"ok": True, "gabarit": reg["gabarit"]})
