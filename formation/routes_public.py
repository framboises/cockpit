"""Pages et API candidat (/test-pc-organisation). Squelette : complete par les taches suivantes."""
from flask import Blueprint, jsonify

formation_bp = Blueprint("formation", __name__, url_prefix="/test-pc-organisation")


@formation_bp.get("/")
def accueil():
    return jsonify({"ok": True, "module": "formation"})
