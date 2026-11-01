"""Postes Bibler sur la carte et consignes : droits de groupe et relais de lecture.

Droits (fonctions pures, testables sans app.py) : `bibler_postes` (voir les
postes), `can_publish_consigne` (donner des consignes, exige de voir). Admin :
toujours ; compte borne (afficheur TV) : jamais.

Relais : Cockpit lit Bibler avec le jeton de service (bibler_pont.py), le
navigateur n'appelle jamais Bibler directement. Tout segment d'URL transmis
est valide avant l'appel (404 sinon).
"""
import re
from functools import wraps

from flask import Blueprint, Response, jsonify, request

import bibler_pont

bibler_bp = Blueprint("bibler", __name__)

_RE_ID = re.compile(r"^[0-9a-fA-F]{24}$")
_RE_VISUEL = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}$")


def _est_admin(payload):
    return bool(payload.get("is_super_admin") or payload.get("app_role") == "admin")


def peut_voir(payload, groupes):
    payload = payload or {}
    if payload.get("type_compte") == "borne":
        return False
    if _est_admin(payload):
        return True
    return any(g.get("bibler_postes") for g in (groupes or []))


def peut_publier(payload, groupes):
    payload = payload or {}
    if not peut_voir(payload, groupes):
        return False
    if _est_admin(payload):
        return True
    return any(g.get("can_publish_consigne") for g in (groupes or []))


def _role_required(role):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


def _lecture(f):
    """role_required('user') puis droit `bibler_postes`, erreurs du pont mappees."""
    @_role_required("user")
    @wraps(f)
    def wrapper(*args, **kwargs):
        from app import _user_can_view_bibler
        payload = getattr(request, "user_payload", None) or {}
        if not _user_can_view_bibler(payload):
            return jsonify({"error": "Droit insuffisant", "code": "droit_insuffisant"}), 403
        try:
            return f(*args, **kwargs)
        except bibler_pont.BiblerNonConfigure:
            return jsonify({"error": "Bibler non configure", "code": "bibler_non_configure"}), 503
        except bibler_pont.BiblerIndisponible:
            return jsonify({"error": "Bibler indisponible", "code": "bibler_indisponible"}), 502
        except bibler_pont.BiblerErreur as e:
            corps = e.corps if isinstance(e.corps, (dict, list)) and e.corps else \
                {"error": "Erreur Bibler", "code": "bibler_erreur"}
            return jsonify(corps), e.statut
    return wrapper


def _non_trouve():
    return jsonify({"error": "Introuvable", "code": "introuvable"}), 404


def _params(*noms):
    return {n: request.args[n] for n in noms if request.args.get(n) not in (None, "")}


@bibler_bp.route("/api/bibler/postes")
@_lecture
def postes():
    params = _params("event", "year", "q", "a")
    ttl = 0 if "a" in params else 30
    return jsonify(bibler_pont.lire("/api/v1/postes", params=params, ttl=ttl))


@bibler_bp.route("/api/bibler/postes/<int:numero>")
@_lecture
def poste(numero):
    return jsonify(bibler_pont.lire("/api/v1/postes/%d" % numero, params=_params("event", "year", "a")))


@bibler_bp.route("/api/bibler/visuel/<nom>")
@_lecture
def visuel(nom):
    if not _RE_VISUEL.match(nom):
        return _non_trouve()
    octets, ctype = bibler_pont.lire_binaire("/api/v1/specimens/visuel/" + nom, ttl=86400)
    ctype = (ctype or "").split(";")[0].strip().lower()
    if ctype not in ("image/jpeg", "image/png"):
        return _non_trouve()
    rep = Response(octets, mimetype=ctype)
    rep.headers["Content-Type"] = ctype
    rep.headers["X-Content-Type-Options"] = "nosniff"
    rep.headers["Cache-Control"] = "private, max-age=86400"
    return rep


@bibler_bp.route("/api/bibler/formats")
@_lecture
def formats():
    params = _params("event", "year", "tout", "postes", "secteurs", "zones", "departements")
    return jsonify(bibler_pont.lire("/api/v1/vacations/formats", params=params, ttl=300))


def _consignes(chemin, params):
    return jsonify(bibler_pont.lire(chemin, params=params, capacites=("consignes:lire",)))


@bibler_bp.route("/api/bibler/consignes")
@_lecture
def consignes():
    return _consignes("/api/v1/consignes", _params("event", "year"))


@bibler_bp.route("/api/bibler/consignes/cibles")
@_lecture
def consignes_cibles():
    return _consignes("/api/v1/consignes/cibles", _params("event", "year", "q"))


@bibler_bp.route("/api/bibler/consignes/apercu")
@_lecture
def consignes_apercu():
    return _consignes("/api/v1/consignes/apercu", _params(
        "event", "year", "tout", "postes", "secteurs", "zones", "departements",
        "formats", "debut", "fin"))


@bibler_bp.route("/api/bibler/consignes/<cid>")
@_lecture
def consigne(cid):
    if not _RE_ID.match(cid):
        return _non_trouve()
    return _consignes("/api/v1/consignes/" + cid, {})
