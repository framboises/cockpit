# routing.py - Blueprint Flask pour le calcul d'itineraires (Valhalla auto-heberge).
#
# Architecture :
#   - Service Valhalla externe (Docker, image gisops/valhalla) sur VALHALLA_URL.
#   - Tuiles construites pour la zone du circuit des 24h du Mans + 5 km
#     (voir scripts/build_valhalla_tiles.sh).
#   - Penalites Waze : on lit la collection waze_alerts (alimentee par traffic.py)
#     et on convertit les alertes recentes en avoid_locations / exclude_polygons.
#   - Mode god (gyrophare / intervention prioritaire) : ignore les bouchons et
#     emprunte les sens interdits via un costing alternatif (par defaut bicycle,
#     en attendant un profil emergency custom).
#   - Fallback stub : si Valhalla est injoignable, retourne une polyline droite
#     entre les points avec un ETA estime par haversine (utile en dev avant
#     deploiement de Valhalla, et comme garde-fou en prod).
#
# Routes :
#   - POST /field/api/route        (tablette Field, auth field_token)
#   - POST /api/route              (Cockpit operateur, auth admin)
#   - POST /api/route/forward      (Cockpit -> push itineraire vers tablette)

from flask import Blueprint, jsonify, request
from datetime import datetime, timezone, timedelta
import os
import math
import logging
import requests
from bson.objectid import ObjectId

# Reutilise les helpers generiques de field.py (auth, mongo, message dispatch).
from field import (
    field_token_required,
    admin_required,
    _get_mongo_db,
    _now,
    _resolve_targets,
    INBOX_MESSAGE_TTL_SECONDS,
)


routing_bp = Blueprint("routing", __name__)
logger = logging.getLogger("routing")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VALHALLA_URL = os.getenv("VALHALLA_URL", "http://localhost:8002").rstrip("/")
VALHALLA_TIMEOUT = int(os.getenv("VALHALLA_TIMEOUT_SECONDS", "5"))

# Costing utilise en mode god. "bicycle" en MVP : ignore beaucoup de oneways,
# accepte les living_streets et paths. Pour aller plus loin : compiler un
# profil "emergency" custom et passer ROUTING_GOD_COSTING=emergency.
ROUTING_GOD_COSTING = os.getenv("ROUTING_GOD_COSTING", "bicycle")

# Anciennete max d'une alerte Waze pour qu'elle soit prise en compte
WAZE_MAX_AGE_MINUTES = int(os.getenv("ROUTING_WAZE_MAX_AGE_MIN", "30"))

# Penalites de cout (en secondes) ajoutees par alerte Waze. La carte est
# (type, subtype) -> penalite. Le subtype None est un fallback generique.
# Mapping cale sur les types Waze observes dans waze_alerts.data.
WAZE_PENALTIES = {
    ("JAM", "STAND_STILL_TRAFFIC"): 600,
    ("JAM", "HEAVY_TRAFFIC"):       300,
    ("JAM", "MODERATE_TRAFFIC"):    150,
    ("JAM", None):                  300,
    ("ACCIDENT", None):             600,
    ("ROAD_CLOSED", None):         1800,
    ("CONSTRUCTION", None):         900,
    ("HAZARD", None):                60,
    ("WEATHERHAZARD", None):         60,
}

# Au-dela de ce seuil de penalite, on bascule de exclude_polygons (cercle ~80m
# qui evite mais reste contournable) vers avoid_locations (Valhalla evite
# fortement le point) - typiquement pour ROAD_CLOSED.
HARD_AVOID_PENALTY_THRESHOLD = 1500

# Vitesses moyennes utilisees par le fallback stub (km/h)
STUB_SPEED_NORMAL_KMH = 35
STUB_SPEED_GOD_KMH = 60


# ---------------------------------------------------------------------------
# Helpers geometriques
# ---------------------------------------------------------------------------

def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _parse_latlng(value):
    """Accepte [lat, lng], (lat, lng), {lat, lng/lon}, ou None. Renvoie (lat, lon) ou None."""
    if value is None:
        return None
    if isinstance(value, dict):
        lat = value.get("lat", value.get("latitude", value.get("y")))
        lng = value.get("lng", value.get("lon", value.get("longitude", value.get("x"))))
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        lat, lng = value[0], value[1]
    else:
        return None
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    return (lat, lng)


def _circle_polygon(lat, lon, radius_m=80, n=8):
    """Approxime un cercle par un polygone (liste de [lon, lat]) ferme."""
    R = 6371000.0
    coords = []
    cos_lat = math.cos(math.radians(lat)) or 1e-9
    for i in range(n):
        angle = 2 * math.pi * i / n
        dlat = (radius_m * math.cos(angle)) / R * (180.0 / math.pi)
        dlon = (radius_m * math.sin(angle)) / (R * cos_lat) * (180.0 / math.pi)
        coords.append([lon + dlon, lat + dlat])
    coords.append(coords[0])
    return coords


# ---------------------------------------------------------------------------
# Encodage / decodage polyline6 (precision 1e-6, format natif Valhalla)
# ---------------------------------------------------------------------------

def _encode_polyline6(points):
    """Encode [(lat, lon), ...] en polyline6 (Valhalla / Google polyline avec precision 1e-6)."""
    out = []
    prev_lat = 0
    prev_lon = 0
    for lat, lon in points:
        ilat = int(round(lat * 1e6))
        ilon = int(round(lon * 1e6))
        for delta in (ilat - prev_lat, ilon - prev_lon):
            d = delta << 1
            if delta < 0:
                d = ~d
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1f)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        prev_lat = ilat
        prev_lon = ilon
    return "".join(out)


def _decode_polyline6(encoded):
    """Decode polyline6 en [(lat, lon), ...]."""
    pts = []
    idx = 0
    lat = 0
    lon = 0
    n = len(encoded)
    while idx < n:
        for axis in (0, 1):
            shift = 0
            result = 0
            while True:
                if idx >= n:
                    return pts
                b = ord(encoded[idx]) - 63
                idx += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if (result & 1) else (result >> 1)
            if axis == 0:
                lat += d
            else:
                lon += d
        pts.append((lat / 1e6, lon / 1e6))
    return pts


# ---------------------------------------------------------------------------
# Lecture Waze
# ---------------------------------------------------------------------------

def _get_recent_waze_alerts(max_age_minutes=None):
    """Lit le doc waze_alerts.latest et filtre les alertes par anciennete."""
    if max_age_minutes is None:
        max_age_minutes = WAZE_MAX_AGE_MINUTES
    try:
        db = _get_mongo_db()
        doc = db["waze_alerts"].find_one({"_id": "latest"})
    except Exception as e:
        logger.warning("routing: lecture waze_alerts impossible: %s", e)
        return []
    if not doc or not isinstance(doc.get("data"), list):
        return []
    cutoff_ms = int((datetime.now(timezone.utc).timestamp() - max_age_minutes * 60) * 1000)
    fresh = []
    for a in doc["data"]:
        try:
            ts = int(a.get("pubMillis") or 0)
        except (TypeError, ValueError):
            continue
        if ts >= cutoff_ms:
            fresh.append(a)
    return fresh


def _build_avoid_locations(alerts):
    """Convertit les alertes en exclusions ponderees Valhalla.
    Renvoie une liste [{lat, lon, penalty_s, type, subtype}, ...] que
    _call_valhalla() distribuera entre avoid_locations / exclude_polygons."""
    out = []
    for a in alerts:
        loc = a.get("location") or {}
        try:
            lon = float(loc.get("x"))
            lat = float(loc.get("y"))
        except (TypeError, ValueError):
            continue
        atype = (a.get("type") or "").upper()
        subtype = (a.get("subtype") or "").upper() or None
        penalty = WAZE_PENALTIES.get((atype, subtype))
        if penalty is None:
            penalty = WAZE_PENALTIES.get((atype, None), 0)
        if penalty <= 0:
            continue
        out.append({
            "lat": lat, "lon": lon,
            "penalty_s": penalty,
            "type": atype, "subtype": subtype,
        })
    return out


# ---------------------------------------------------------------------------
# Appel Valhalla
# ---------------------------------------------------------------------------

def _call_valhalla(from_pt, to_pt, waypoints, god, avoids):
    locations = [{"lat": from_pt[0], "lon": from_pt[1], "type": "break"}]
    for wp in (waypoints or []):
        ll = _parse_latlng(wp)
        if ll:
            locations.append({"lat": ll[0], "lon": ll[1], "type": "via"})
    locations.append({"lat": to_pt[0], "lon": to_pt[1], "type": "break"})

    costing = ROUTING_GOD_COSTING if god else "auto"
    payload = {
        "locations": locations,
        "costing": costing,
        "directions_options": {"units": "kilometers"},
        "id": "cockpit-routing",
    }

    if not god and avoids:
        avoid_locations = []
        exclude_polygons = []
        for a in avoids:
            if a["penalty_s"] >= HARD_AVOID_PENALTY_THRESHOLD:
                avoid_locations.append({"lat": a["lat"], "lon": a["lon"]})
            else:
                exclude_polygons.append(_circle_polygon(a["lat"], a["lon"], radius_m=80))
        if avoid_locations:
            payload["avoid_locations"] = avoid_locations
        if exclude_polygons:
            payload["exclude_polygons"] = exclude_polygons

    try:
        url = VALHALLA_URL + "/route"
        r = requests.post(url, json=payload, timeout=VALHALLA_TIMEOUT)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.RequestException as e:
        logger.info("routing: Valhalla injoignable, fallback stub: %s", e)
        return None, "valhalla_unreachable"


def _format_valhalla_response(resp, mode):
    trip = (resp or {}).get("trip") or {}
    legs = trip.get("legs") or []
    if not legs:
        return None
    summary = trip.get("summary") or {}
    distance_km = float(summary.get("length") or 0)
    duration_s = int(summary.get("time") or 0)

    if len(legs) == 1:
        polyline = legs[0].get("shape", "")
    else:
        merged = []
        for leg in legs:
            pts = _decode_polyline6(leg.get("shape", ""))
            if merged and pts and merged[-1] == pts[0]:
                pts = pts[1:]
            merged.extend(pts)
        polyline = _encode_polyline6(merged)

    return {
        "polyline": polyline,
        "distance_m": int(distance_km * 1000),
        "duration_s": duration_s,
        "eta_iso": (datetime.now(timezone.utc) + timedelta(seconds=duration_s)).isoformat(),
        "mode": mode,
        "engine": "valhalla",
    }


def _stub_route(from_pt, to_pt, waypoints, god):
    """Fallback : trace un trait droit + ETA estime par haversine."""
    pts = [from_pt]
    for wp in (waypoints or []):
        ll = _parse_latlng(wp)
        if ll:
            pts.append(ll)
    pts.append(to_pt)
    distance = 0.0
    for i in range(len(pts) - 1):
        distance += _haversine_m(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
    speed_kmh = STUB_SPEED_GOD_KMH if god else STUB_SPEED_NORMAL_KMH
    speed_mps = speed_kmh * 1000.0 / 3600.0
    duration = distance / speed_mps if speed_mps > 0 else 0
    return {
        "polyline": _encode_polyline6(pts),
        "distance_m": int(distance),
        "duration_s": int(duration),
        "eta_iso": (datetime.now(timezone.utc) + timedelta(seconds=duration)).isoformat(),
        "mode": "god" if god else "auto",
        "engine": "stub",
    }


def _compute(from_pt, to_pt, waypoints, god):
    avoids = [] if god else _build_avoid_locations(_get_recent_waze_alerts())
    resp, err = _call_valhalla(from_pt, to_pt, waypoints, god, avoids)
    if resp is None:
        out = _stub_route(from_pt, to_pt, waypoints, god)
        out["waze_avoided"] = len(avoids)
        return out, None
    out = _format_valhalla_response(resp, mode=("god" if god else "auto"))
    if out is None:
        return None, "no_route"
    out["waze_avoided"] = len(avoids)
    return out, None


# ---------------------------------------------------------------------------
# Resolution fiche / vehicule (Cockpit)
# ---------------------------------------------------------------------------

def _resolve_fiche_endpoints(db, fiche_id):
    """Pour une fiche pcorg, retourne (from_pt, to_pt, device_doc) ou (None, None, None).
    from = derniere position du vehicule engage ; to = GPS de la fiche.
    Note : pcorg._id est un string UUID5, pas un ObjectId."""
    fiche = db["pcorg"].find_one({"_id": fiche_id})
    if not fiche:
        return None, None, None

    to_pt = None
    gps = fiche.get("gps") or {}
    coords = gps.get("coordinates")
    if coords and len(coords) >= 2:
        # GeoJSON : [lng, lat]
        try:
            to_pt = (float(coords[1]), float(coords[0]))
        except (TypeError, ValueError):
            to_pt = None

    from_pt = None
    device = None
    patrouille = (fiche.get("content_category") or {}).get("patrouille")
    if patrouille:
        device = db["field_devices"].find_one({
            "name": patrouille,
            "event": fiche.get("event"),
            "year": str(fiche.get("year") or ""),
            "revoked": {"$ne": True},
        })
        if device:
            last = device.get("last_position") or {}
            try:
                from_pt = (float(last.get("lat")), float(last.get("lng")))
            except (TypeError, ValueError):
                from_pt = None

    return from_pt, to_pt, device


# ---------------------------------------------------------------------------
# Routes HTTP
# ---------------------------------------------------------------------------

@routing_bp.route("/field/api/route", methods=["POST"])
@field_token_required
def field_route():
    """Calcul d'itineraire pour la tablette Field.

    Body JSON : {from: [lat,lng], to: [lat,lng], waypoints?: [...], god?: bool}
    """
    data = request.get_json(silent=True) or {}
    from_pt = _parse_latlng(data.get("from"))
    to_pt = _parse_latlng(data.get("to"))
    if not from_pt or not to_pt:
        return jsonify({"ok": False, "error": "invalid_coordinates"}), 400
    waypoints = data.get("waypoints") or []
    god = bool(data.get("god"))
    out, err = _compute(from_pt, to_pt, waypoints, god)
    if err:
        return jsonify({"ok": False, "error": err}), 502
    out["ok"] = True
    return jsonify(out)


@routing_bp.route("/api/route", methods=["POST"])
@admin_required
def cockpit_route():
    """Calcul d'itineraire pour Cockpit.

    Body JSON :
      - Forme directe : {from: [lat,lng], to: [lat,lng], waypoints?, god?}
      - Forme fiche   : {fiche_id: "...", waypoints?, god?}
        => from = field_devices.last_position de la patrouille engagee
        => to   = pcorg.gps de la fiche
    """
    data = request.get_json(silent=True) or {}
    from_pt = _parse_latlng(data.get("from"))
    to_pt = _parse_latlng(data.get("to"))
    fiche_id = (data.get("fiche_id") or "").strip()

    device_id = None
    device_name = None
    event = None
    year = None
    if fiche_id:
        db = _get_mongo_db()
        f_from, f_to, device = _resolve_fiche_endpoints(db, fiche_id)
        if not from_pt:
            from_pt = f_from
        if not to_pt:
            to_pt = f_to
        if device:
            device_id = str(device["_id"])
            device_name = device.get("name")
            event = device.get("event")
            year = device.get("year")

    if not from_pt or not to_pt:
        return jsonify({"ok": False, "error": "invalid_coordinates"}), 400
    waypoints = data.get("waypoints") or []
    god = bool(data.get("god"))
    out, err = _compute(from_pt, to_pt, waypoints, god)
    if err:
        return jsonify({"ok": False, "error": err}), 502
    out["ok"] = True
    out["from"] = list(from_pt)
    out["to"] = list(to_pt)
    if device_id:
        out["device_id"] = device_id
        out["device_name"] = device_name
        out["event"] = event
        out["year"] = year
    return jsonify(out)


@routing_bp.route("/api/route/forward", methods=["POST"])
@admin_required
def forward_route():
    """Pousse un itineraire calcule vers la tablette du vehicule engage.

    Body JSON :
      {device_id, event, year, polyline, from?, to, waypoints?,
       distance_m?, duration_s?, title?, body?}
    Cree un message field_messages type=route avec payload.polyline +
    payload.waypoints (compat ascendante avec l'ancien handler tablette).
    """
    data = request.get_json(silent=True) or {}
    device_id = (data.get("device_id") or "").strip()
    event = (data.get("event") or "").strip()
    year = str(data.get("year") or "").strip()
    polyline = (data.get("polyline") or "").strip()

    if not device_id or not event or not year:
        return jsonify({"ok": False, "error": "missing_target"}), 400
    if not polyline:
        return jsonify({"ok": False, "error": "missing_polyline"}), 400

    db = _get_mongo_db()
    targets, err = _resolve_targets(db, event, year, {"device_ids": [device_id]})
    if err or not targets:
        return jsonify({"ok": False, "error": err or "no_target"}), 404

    to_ll = _parse_latlng(data.get("to"))
    waypoints_compat = []
    if to_ll:
        waypoints_compat.append([to_ll[0], to_ll[1]])

    payload = {
        "polyline": polyline,
        "waypoints": waypoints_compat,
        "distance_m": int(data.get("distance_m") or 0),
        "duration_s": int(data.get("duration_s") or 0),
        "from": list(data.get("from") or []),
        "to": list(data.get("to") or []),
        "via": data.get("waypoints") or [],
        "forced": True,
    }

    title = (data.get("title") or "Itineraire").strip()[:120]
    body = (data.get("body") or "Itineraire envoye depuis le PC org.").strip()[:4000]

    now = _now()
    expires = now + timedelta(seconds=INBOX_MESSAGE_TTL_SECONDS)
    sender = (getattr(request, "admin_user", None) or {}).get("email", "?")

    docs = []
    for d in targets:
        docs.append({
            "device_id": d["_id"],
            "device_name": d.get("name"),
            "event": event,
            "year": year,
            "type": "route",
            "title": title,
            "body": body,
            "payload": payload,
            "priority": "high",
            "from": sender,
            "createdAt": now,
            "expiresAt": expires,
            "ack_at": None,
        })
    if docs:
        db["field_messages"].insert_many(docs)

    return jsonify({"ok": True, "sent_count": len(docs), "device_id": device_id})
