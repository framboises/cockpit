"""saison_indicateurs_api.py - Routes des indicateurs de la timeline SAISON.

  GET /api/saison/indicateurs?from=&to=   (user)  jours -> indicateurs (62 j max)
  GET /api/saison/indicateurs/config      (user)  configuration globale (lecture)
  PUT /api/saison/indicateurs/config      (admin) enregistre la configuration (CSRF ACTIF)
  GET /api/saison/indicateurs/rooms       (admin) espaces Momentus groupes par site
  GET /api/saison/indicateurs/event-types (admin) types d'evenement Momentus (bloc seminaires)
  GET /api/saison/client?client=&from=&to= (user) programme d'un client (180 j max,
                                          defaut J-7 -> J+90) ; `event=<id Momentus>`
                                          a la place de `client` : client de l'evenement
  GET /api/saison/search?q=&from=&to=     (user) recherche client / lieu / epreuve /
                                          visites (400 j max, defaut J-30 -> J+365)

Calcul dans saison_indicateurs.py. Ni contacts ni montants dans les reponses.
"""

import logging
from datetime import timedelta
from functools import wraps

from flask import Blueprint, jsonify, request

import saison_indicateurs as SI

logger = logging.getLogger(__name__)

saison_indicateurs_bp = Blueprint("saison_indicateurs", __name__)


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


@saison_indicateurs_bp.route("/api/saison/indicateurs", methods=["GET"])
@_role_required("user")
def api_indicateurs():
    a = SI.parse_day(request.args.get("from"))
    b = SI.parse_day(request.args.get("to"))
    if not a or not b:
        return _err("periode_requise")
    if b < a:
        return _err("periode_invalide")
    if b - a > timedelta(days=SI.MAX_WINDOW_DAYS - 1):
        return _err("periode_trop_longue")
    try:
        return jsonify(SI.payload(_db(), a, b))
    except Exception as e:  # la timeline ne doit jamais casser pour un indicateur
        logger.warning("saison indicateurs : %s", e)
        return _err("indisponible", 503)


@saison_indicateurs_bp.route("/api/saison/client", methods=["GET"])
@_role_required("user")
def api_client():
    """Programme d'un client Momentus (cle `client` des seminaires), ou du
    client d'un evenement (`event=<momentus_event_id>`, bouton planning des
    vignettes de la timeline)."""
    if request.args.get("event") and not request.args.get("client"):
        if not SI.parse_event_id(request.args.get("event")):
            return _err("evenement_invalide")
        try:
            client = SI.client_for_event(_db(), request.args.get("event"))
        except Exception as e:
            logger.warning("saison programme client (evenement) : %s", e)
            return _err("indisponible", 503)
        if not client:
            return _err("evenement_inconnu", 404)
    else:
        client = SI.parse_client(request.args.get("client"))
    if not client:
        return _err("client_invalide")
    from datetime import date
    a, b, err = SI.client_window(request.args.get("from"), request.args.get("to"), date.today())
    if err:
        return _err(err)
    try:
        return jsonify({"ok": True, **SI.client_programme(_db(), client, a, b)})
    except Exception as e:
        logger.warning("saison programme client : %s", e)
        return _err("indisponible", 503)


@saison_indicateurs_bp.route("/api/saison/search", methods=["GET"])
@_role_required("user")
def api_search():
    """Jours de la saison ou un client, un lieu, une epreuve ou des visites
    correspondent a `q` (calendrier d'occupation, barre de recherche)."""
    from datetime import date
    q = SI.norm_query(request.args.get("q"))
    if not q:
        return _err("requete_trop_courte")
    a, b, err = SI.search_window(request.args.get("from"), request.args.get("to"), date.today())
    if err:
        return _err(err)
    try:
        return jsonify({"ok": True, **SI.search_cached(_db(), q, a, b)})
    except Exception as e:
        logger.warning("saison recherche : %s", e)
        return _err("indisponible", 503)


@saison_indicateurs_bp.route("/api/saison/indicateurs/config", methods=["GET"])
@_role_required("user")
def api_config_get():
    db = _db()
    return jsonify({"ok": True, "indicators": SI.get_config(db),
                    "defaults": SI.DEFAULT_INDICATORS,
                    "seminaires": SI.get_seminaires(db),
                    "seminaires_defaults": SI.DEFAULT_SEMINAIRES,
                    "limits": {"indicators": SI.MAX_INDICATORS, "rooms": SI.MAX_ROOMS,
                               "sem_types": SI.MAX_SEM_TYPES, "sem_list": SI.MAX_SEM_LIST}})


@saison_indicateurs_bp.route("/api/saison/indicateurs/config", methods=["PUT"])
@_role_required("admin")
def api_config_put():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err("json_attendu")
    payload = getattr(request, "user_payload", {}) or {}
    db = _db()
    clean, errors = SI.save_config(db, body.get("indicators") if "indicators" in body else None,
                                   payload.get("email"), seminaires=body.get("seminaires"))
    if errors:
        return _err("configuration_invalide", 400, details=errors[:20])
    logger.info("Indicateurs SAISON mis a jour par %s", payload.get("email"))
    return jsonify({"ok": True, "indicators": clean if clean is not None else SI.get_config(db),
                    "seminaires": SI.get_seminaires(db)})


@saison_indicateurs_bp.route("/api/saison/indicateurs/event-types", methods=["GET"])
@_role_required("admin")
def api_event_types():
    return jsonify({"ok": True, "types": SI.event_types(_db())})


@saison_indicateurs_bp.route("/api/saison/indicateurs/rooms", methods=["GET"])
@_role_required("admin")
def api_rooms():
    return jsonify({"ok": True, "venues": SI.rooms_by_venue(_db())})
