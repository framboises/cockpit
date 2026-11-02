"""momentus_api.py - Reservations Momentus par lieu de la carte (lecture seule).

Routes (user) consommees par static/js/momentus_lieu.js (bouton dans les
popups des lieux de la carte d'accueil) :
  GET /api/momentus/mapped              lieux carto ayant un espace Momentus valide
  GET /api/momentus/carte               calque carte : lieux rattaches ayant des reservations
  GET /api/momentus/lieu/<feature_id>   reservations du lieu (?event=&year= ou ?from=&to=)
  GET /api/momentus/evenement/<event_id>  fiche d'exploitation d'un evenement (momentus_fiche.py,
                                        copie identique dans Looker) : personnel, materiel,
                                        espaces + pilote, programme, plans, alertes
  GET /api/momentus/document/<doc_id>   plan / etat des lieux telecharge par momentus_details.py

Memes routes et meme module de calcul (momentus_lieux.py) que Groundmaster, ou
se fait la correspondance espaces Momentus <-> lieux (/momentus/lieux).
Donnees : collections momentus_* (synchro horaire momentus_sync.py).
Ni contacts ni montants dans les reponses.
"""

import logging
import mimetypes
import os
import re
from functools import wraps

from flask import Blueprint, jsonify, request, send_file

import momentus_fiche as MF
import momentus_lieux as ML

DOCS_DIR = os.path.realpath(os.getenv("MOMENTUS_DOCS_DIR", "").strip() or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "uploads", "momentus_documents"))

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


@momentus_bp.route("/api/momentus/evenement/<event_id>", methods=["GET"])
@_role_required("user")
def api_evenement(event_id):
    if not MF.EVENT_ID_RE.fullmatch(event_id or ""):
        return _err("evenement_invalide")
    try:
        data = MF.fiche_payload(_db(), event_id)
    except Exception as e:  # la page ne casse jamais pour Momentus
        logger.warning("momentus fiche %s : %s", event_id, e)
        return _err("momentus_indisponible", 503)
    if data is None:
        return _err("evenement_inconnu", 404)
    return jsonify(data)


_INLINE = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}


@momentus_bp.route("/api/momentus/document/<doc_id>", methods=["GET"])
@_role_required("user")
def api_document(doc_id):
    """Plan ou etat des lieux (seuls dossiers telecharges, cf. momentus_details.DOC_FOLDERS)."""
    if not MF.DOC_ID_RE.fullmatch(doc_id or ""):
        return _err("document_invalide")
    doc, rel = MF.document_meta(_db(), doc_id)
    if not doc:
        return _err("document_inconnu", 404)
    path = os.path.realpath(os.path.join(DOCS_DIR, rel))
    if not path.startswith(DOCS_DIR + os.sep) or not os.path.isfile(path):
        return _err("document_absent", 404)
    name = doc.get("name") or os.path.basename(path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    resp = send_file(path, mimetype=mime, as_attachment=mime not in _INLINE, download_name=name,
                     conditional=True, max_age=300)
    # Type deduit de l'extension et nosniff : un fichier client ne s'execute jamais
    # comme une page. Pas de CSP "sandbox" : Chrome refuse alors d'afficher un PDF.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
