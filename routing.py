# routing.py -- Proxy Valhalla pour navigation interne
from flask import Blueprint, jsonify, request
from functools import wraps
import logging
import os
import requests

routing_bp = Blueprint('routing', __name__)
logger = logging.getLogger(__name__)

# URL du serveur Valhalla (Docker local par defaut)
VALHALLA_URL = os.getenv("VALHALLA_URL", "http://localhost:8002")

# Profils vehicule -> costing Valhalla
VEHICLE_PROFILES = {
    "auto": "auto",
    "ambulance": "auto",       # auto avec des options emergency
    "vl": "auto",              # vehicule leger
    "pedestrian": "pedestrian",
    "bicycle": "bicycle",
}

# Options de costing specifiques par profil
COSTING_OPTIONS = {
    "auto": {},
    "ambulance": {
        "top_speed": 90,
        "use_highways": 0.2,
        "use_tolls": 1.0,
        "shortest": False,
    },
    "vl": {
        "top_speed": 50,
        "use_highways": 0.1,
        "shortest": False,
    },
    "pedestrian": {
        "walking_speed": 5.1,
        "walkway_factor": 0.9,
    },
    "bicycle": {
        "cycling_speed": 18.0,
    },
}


def _check_auth():
    """Verifie l'auth pour les routes routing."""
    from app import CODING, ROLE_HIERARCHY, ROLE_ORDER
    import jwt as pyjwt
    from app import JWT_SECRET, JWT_ALGORITHM, BASE_URL

    if CODING:
        sim_role = request.args.get("as", "admin")
        if sim_role not in ROLE_HIERARCHY:
            sim_role = "admin"
        sim_level = ROLE_HIERARCHY[sim_role]
        sim_roles = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= sim_level]
        request.user_payload = {
            "apps": ["looker", "shiftsolver", "tagger"],
            "roles_by_app": {"cockpit": sim_role},
            "global_roles": [],
            "roles": sim_roles,
            "app_role": sim_role,
            "is_super_admin": False,
            "firstname": "Bruce",
            "lastname": "WAYNE",
            "email": "bruce@wayneenterprise.com",
        }
        return None

    token = request.cookies.get("access_token")
    if not token:
        return jsonify({"error": "Authentification requise"}), 401
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
        return jsonify({"error": "Token invalide ou expire"}), 401

    from app import APP_KEY, SUPER_ADMIN_ROLE
    apps = payload.get("apps", []) or []
    if APP_KEY not in apps:
        return jsonify({"error": "Application non autorisee"}), 403

    global_roles = payload.get("global_roles", []) or []
    is_super = SUPER_ADMIN_ROLE in global_roles
    roles_map = payload.get("roles_by_app", {}) or {}
    app_role = roles_map.get(APP_KEY, "user")
    from app import ROLE_HIERARCHY, ROLE_ORDER
    level = ROLE_HIERARCHY.get(app_role, 0)
    if is_super:
        level = max(ROLE_HIERARCHY.values())
    roles_list = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= level]

    request.user_payload = {
        **payload,
        "roles": roles_list,
        "app_role": app_role,
        "is_super_admin": is_super,
    }
    return None


@routing_bp.before_request
def _before():
    resp = _check_auth()
    if resp is not None:
        return resp


@routing_bp.route('/api/routing/route', methods=['POST'])
def get_route():
    """Calcule un itineraire via Valhalla.
    Body JSON: {
        "from": {"lat": ..., "lng": ...},
        "to":   {"lat": ..., "lng": ...},
        "vehicule": "auto"|"ambulance"|"vl"|"pedestrian"|"bicycle",
        "avoid": [{"lat": ..., "lng": ..., "radius": 100}]  // optionnel
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body requis"}), 400

    origin = data.get("from")
    dest = data.get("to")
    if not origin or not dest:
        return jsonify({"error": "Champs 'from' et 'to' requis"}), 400

    profile = data.get("vehicule", "auto")
    if profile not in VEHICLE_PROFILES:
        return jsonify({"error": "Profil vehicule inconnu: " + profile}), 400

    costing = VEHICLE_PROFILES[profile]
    costing_opts = COSTING_OPTIONS.get(profile, {})

    # Construction de la requete Valhalla
    valhalla_req = {
        "locations": [
            {"lat": origin["lat"], "lon": origin["lng"]},
            {"lat": dest["lat"], "lon": dest["lng"]},
        ],
        "costing": costing,
        "costing_options": {costing: costing_opts},
        "directions_options": {
            "units": "kilometers",
            "language": "fr-FR",
        },
        "alternates": 0,
    }

    # Zones a eviter (routes bloquees)
    avoid_locs = data.get("avoid", [])
    if avoid_locs:
        valhalla_req["exclude_locations"] = [
            {"lat": a["lat"], "lon": a["lng"]}
            for a in avoid_locs
        ]

    try:
        resp = requests.post(
            VALHALLA_URL + "/route",
            json=valhalla_req,
            timeout=10,
        )
    except requests.RequestException as e:
        logger.error("Valhalla unreachable: %s", e)
        return jsonify({"error": "Serveur de routage indisponible"}), 503

    if resp.status_code != 200:
        logger.warning("Valhalla error %s: %s", resp.status_code, resp.text[:500])
        return jsonify({
            "error": "Erreur de routage",
            "detail": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:200],
        }), 502

    valhalla_data = resp.json()

    # Extraire le shape (polyline encode) et les maneuvers
    trip = valhalla_data.get("trip", {})
    legs = trip.get("legs", [])
    summary = trip.get("summary", {})

    result = {
        "distance_km": round(summary.get("length", 0), 2),
        "duration_s": round(summary.get("time", 0)),
        "legs": [],
    }

    for leg in legs:
        shape = leg.get("shape", "")
        maneuvers = []
        for m in leg.get("maneuvers", []):
            maneuvers.append({
                "instruction": m.get("instruction", ""),
                "type": m.get("type", 0),
                "distance_km": round(m.get("length", 0), 3),
                "duration_s": round(m.get("time", 0)),
                "begin_shape_index": m.get("begin_shape_index", 0),
                "end_shape_index": m.get("end_shape_index", 0),
                "street_names": m.get("street_names", []),
            })
        result["legs"].append({
            "shape": shape,
            "maneuvers": maneuvers,
        })

    return jsonify(result)


@routing_bp.route('/api/routing/isochrone', methods=['POST'])
def get_isochrone():
    """Calcule une isochrone (zone atteignable en N minutes).
    Body JSON: {
        "center": {"lat": ..., "lng": ...},
        "minutes": [3, 5, 10],
        "vehicule": "ambulance"
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body requis"}), 400

    center = data.get("center")
    if not center:
        return jsonify({"error": "Champ 'center' requis"}), 400

    minutes = data.get("minutes", [3, 5, 10])
    profile = data.get("vehicule", "auto")
    costing = VEHICLE_PROFILES.get(profile, "auto")
    costing_opts = COSTING_OPTIONS.get(profile, {})

    contours = [{"time": m} for m in minutes]

    valhalla_req = {
        "locations": [{"lat": center["lat"], "lon": center["lng"]}],
        "costing": costing,
        "costing_options": {costing: costing_opts},
        "contours": contours,
        "polygons": True,
    }

    try:
        resp = requests.post(
            VALHALLA_URL + "/isochrone",
            json=valhalla_req,
            timeout=15,
        )
    except requests.RequestException as e:
        logger.error("Valhalla isochrone unreachable: %s", e)
        return jsonify({"error": "Serveur de routage indisponible"}), 503

    if resp.status_code != 200:
        return jsonify({"error": "Erreur isochrone", "detail": resp.text[:200]}), 502

    # Valhalla retourne du GeoJSON directement
    return jsonify(resp.json())


@routing_bp.route('/api/routing/health', methods=['GET'])
def routing_health():
    """Verifie que Valhalla est accessible."""
    try:
        resp = requests.get(VALHALLA_URL + "/status", timeout=3)
        ok = resp.status_code == 200
    except requests.RequestException:
        ok = False

    return jsonify({
        "valhalla_url": VALHALLA_URL,
        "available": ok,
    })
