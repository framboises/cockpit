# routing_overrides.py - Blueprint Flask pour les overrides de routing edites
# par l'admin Cockpit (portails fermes mal taggues dans OSM, routes barrees,
# zones a forcer passable...).
#
# Architecture :
#   - Collection MongoDB "routing_overrides" : 1 doc par correction terrain.
#   - Trois types :
#       block_point   : point unique (lat, lon). Envoye a Valhalla en
#                       avoid_locations : Valhalla snape sur l'arete de route
#                       la plus proche et l'interdit. Pas de rayon necessaire,
#                       plus precis qu'un mini-polygone.
#       block_polygon : polygone (coords [[lon, lat], ...]) envoye en
#                       exclude_polygons. Pour les zones larges.
#       force_open    : marqueur "ce passage est en realite ouvert" - non
#                       applique au runtime (Valhalla ne sait pas inclure),
#                       sert d'inventaire pour le futur patch OSM (Phase 3).
#   - Trois scopes : "all" (tous les itineraires), "normal_only" (uniquement
#     mode auto), "god_only" (uniquement mode intervention).
#   - expires_at optionnel : blocage temporaire auto-desactive apres echeance.
#   - Cache module-level 30 s pour eviter les hits Mongo a chaque calcul.
#     Invalidation sur write via _bump_cache().
#
# Routes :
#   - GET    /api/admin/routing-overrides              (admin, liste)
#   - POST   /api/admin/routing-overrides              (admin, create)
#   - PATCH  /api/admin/routing-overrides/<id>         (admin, update partiel)
#   - DELETE /api/admin/routing-overrides/<id>         (admin, delete)
#   - GET    /api/routing-overrides/active             (admin, lecture seule
#                                                       pour visualisation modale)
#
# Consomme par routing.py :
#   get_active_overrides_for_compute(god) -> (avoid_locations, exclude_polygons)

from flask import Blueprint, jsonify, request
from datetime import datetime, timezone
from bson.objectid import ObjectId
import logging
import time

from field import admin_required, _get_mongo_db, _now


routing_overrides_bp = Blueprint("routing_overrides", __name__)
logger = logging.getLogger("routing_overrides")


# ---------------------------------------------------------------------------
# Constantes / validation
# ---------------------------------------------------------------------------

VALID_TYPES = {"block_point", "block_polygon", "force_open"}
VALID_SCOPES = {"all", "normal_only", "god_only"}

MIN_POLY_VERTICES = 3
MAX_POLY_VERTICES = 200
MAX_LABEL_LEN = 200
MAX_NOTES_LEN = 2000

CACHE_TTL_S = 30


# ---------------------------------------------------------------------------
# Cache module-level
# ---------------------------------------------------------------------------

_cache = {"data": None, "ts": 0.0}


def _bump_cache():
    _cache["ts"] = 0.0
    _cache["data"] = None


# ---------------------------------------------------------------------------
# Index Mongo (lazy)
# ---------------------------------------------------------------------------

_INDEXES_READY = False


def _ensure_indexes(db):
    global _INDEXES_READY
    if _INDEXES_READY:
        return
    try:
        db["routing_overrides"].create_index([("active", 1), ("scope", 1)])
        db["routing_overrides"].create_index("expires_at")
        db["routing_overrides"].create_index("type")
        _INDEXES_READY = True
    except Exception as exc:
        logger.warning("routing_overrides: index creation failed: %s", exc)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_payload(data, partial=False):
    """Valide un payload de create (partial=False) ou update (partial=True).
    Retourne (cleaned_dict, error_message)."""
    if not isinstance(data, dict):
        return None, "payload_not_object"

    cleaned = {}

    if not partial or "type" in data:
        t = (data.get("type") or "").strip()
        if t not in VALID_TYPES:
            return None, "invalid_type"
        cleaned["type"] = t

    if not partial or "label" in data:
        label = (data.get("label") or "").strip()
        if not label:
            return None, "missing_label"
        if len(label) > MAX_LABEL_LEN:
            return None, "label_too_long"
        cleaned["label"] = label

    if not partial or "scope" in data:
        scope = (data.get("scope") or "all").strip()
        if scope not in VALID_SCOPES:
            return None, "invalid_scope"
        cleaned["scope"] = scope

    typ = cleaned.get("type") or (data.get("type") if partial else None)

    if typ in ("block_point", "force_open"):
        if not partial or any(k in data for k in ("lat", "lon")):
            try:
                lat = float(data.get("lat"))
                lon = float(data.get("lon"))
            except (TypeError, ValueError):
                return None, "invalid_coordinates"
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return None, "invalid_coordinates"
            cleaned["lat"] = lat
            cleaned["lon"] = lon
    elif typ == "block_polygon":
        if not partial or "coords" in data:
            coords = data.get("coords")
            if not isinstance(coords, list):
                return None, "invalid_coords"
            if not (MIN_POLY_VERTICES <= len(coords) <= MAX_POLY_VERTICES):
                return None, "invalid_coords"
            clean_coords = []
            for pt in coords:
                if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                    return None, "invalid_coords"
                try:
                    px = float(pt[0])
                    py = float(pt[1])
                except (TypeError, ValueError):
                    return None, "invalid_coords"
                if not (-180 <= px <= 180 and -90 <= py <= 90):
                    return None, "invalid_coords"
                clean_coords.append([px, py])
            cleaned["coords"] = clean_coords

    if "active" in data:
        cleaned["active"] = bool(data.get("active"))

    if "expires_at" in data:
        exp = data.get("expires_at")
        if exp is None or exp == "":
            cleaned["expires_at"] = None
        else:
            try:
                dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                cleaned["expires_at"] = dt
            except (TypeError, ValueError):
                return None, "invalid_expires_at"

    if "notes" in data:
        notes = (data.get("notes") or "").strip()
        if len(notes) > MAX_NOTES_LEN:
            return None, "notes_too_long"
        cleaned["notes"] = notes

    if "osm_ref" in data:
        osm = (data.get("osm_ref") or "").strip()[:120]
        cleaned["osm_ref"] = osm

    return cleaned, None


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _serialize(doc):
    if not doc:
        return None
    out = {
        "id": str(doc["_id"]),
        "type": doc.get("type"),
        "label": doc.get("label", ""),
        "scope": doc.get("scope", "all"),
        "active": bool(doc.get("active", True)),
        "notes": doc.get("notes", ""),
        "osm_ref": doc.get("osm_ref", ""),
        "created_by": doc.get("created_by"),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
        "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
        "expires_at": doc.get("expires_at").isoformat() if doc.get("expires_at") else None,
    }
    if doc.get("type") in ("block_point", "force_open"):
        out["lat"] = doc.get("lat")
        out["lon"] = doc.get("lon")
    elif doc.get("type") == "block_polygon":
        out["coords"] = doc.get("coords", [])
    return out


# ---------------------------------------------------------------------------
# Lecture cached pour _compute()
# ---------------------------------------------------------------------------

def _is_expired(doc, now):
    exp = doc.get("expires_at")
    if not exp:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp < now


def _get_all_active():
    """Retourne la liste des overrides actifs non expires, avec cache 30 s."""
    now_ts = time.time()
    if _cache["data"] is not None and (now_ts - _cache["ts"]) < CACHE_TTL_S:
        return _cache["data"]
    try:
        db = _get_mongo_db()
        _ensure_indexes(db)
        cursor = db["routing_overrides"].find({"active": True})
        now_dt = datetime.now(timezone.utc)
        data = [d for d in cursor if not _is_expired(d, now_dt)]
    except Exception as exc:
        logger.warning("routing_overrides: lecture echouee, fallback vide: %s", exc)
        data = []
    _cache["data"] = data
    _cache["ts"] = now_ts
    return data


def get_active_overrides_for_compute(god):
    """Retourne (avoid_locations, exclude_polygons) au format Valhalla,
    filtres selon le mode (god ou normal).

    - block_point : envoye en avoid_locations (Valhalla snape sur l'arete
      de route la plus proche et l'interdit). Plus precis qu'un mini-polygone.
    - block_polygon : ajoute en exclude_polygons direct.
    - force_open : ignore (non applicable au runtime, sert pour Phase 3).
    - scope "all" : applique aux deux modes.
    - scope "normal_only" : applique uniquement si god=False.
    - scope "god_only" : applique uniquement si god=True.
    """
    avoid_locations = []
    exclude_polygons = []
    for doc in _get_all_active():
        scope = doc.get("scope", "all")
        if scope == "normal_only" and god:
            continue
        if scope == "god_only" and not god:
            continue
        t = doc.get("type")
        if t == "block_point":
            try:
                lat = float(doc["lat"]); lon = float(doc["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            avoid_locations.append({"lat": lat, "lon": lon})
        elif t == "block_polygon":
            coords = doc.get("coords") or []
            if len(coords) >= 3:
                ring = [list(p) for p in coords]
                if ring[0] != ring[-1]:
                    ring.append(list(ring[0]))
                exclude_polygons.append(ring)
        # force_open : pas d'effet runtime
    return avoid_locations, exclude_polygons


def get_active_overrides_for_display():
    """Pour la visualisation cote frontend (modale routing, page admin).
    Retourne la liste serialisee des overrides actifs non expires."""
    return [_serialize(d) for d in _get_all_active()]


# ---------------------------------------------------------------------------
# Routes HTTP
# ---------------------------------------------------------------------------

@routing_overrides_bp.route("/api/admin/routing-overrides", methods=["GET"])
@admin_required
def list_overrides():
    db = _get_mongo_db()
    _ensure_indexes(db)
    q = {}
    typ = (request.args.get("type") or "").strip()
    if typ in VALID_TYPES:
        q["type"] = typ
    scope = (request.args.get("scope") or "").strip()
    if scope in VALID_SCOPES:
        q["scope"] = scope
    if (request.args.get("active_only") or "").lower() in {"1", "true", "yes"}:
        q["active"] = True
    cursor = db["routing_overrides"].find(q).sort("created_at", -1)
    return jsonify({"ok": True, "items": [_serialize(d) for d in cursor]})


@routing_overrides_bp.route("/api/admin/routing-overrides", methods=["POST"])
@admin_required
def create_override():
    data = request.get_json(silent=True) or {}
    cleaned, err = _validate_payload(data, partial=False)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    sender = (getattr(request, "admin_user", None) or {}).get("email") or "?"
    now_dt = _now()
    doc = dict(cleaned)
    doc.setdefault("active", True)
    doc.setdefault("notes", "")
    doc.setdefault("osm_ref", "")
    doc.setdefault("expires_at", None)
    doc["created_by"] = sender
    doc["created_at"] = now_dt
    doc["updated_at"] = now_dt

    db = _get_mongo_db()
    _ensure_indexes(db)
    res = db["routing_overrides"].insert_one(doc)
    doc["_id"] = res.inserted_id
    _bump_cache()
    return jsonify({"ok": True, "item": _serialize(doc)})


@routing_overrides_bp.route("/api/admin/routing-overrides/<oid>", methods=["PATCH"])
@admin_required
def update_override(oid):
    try:
        obj_id = ObjectId(oid)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400

    data = request.get_json(silent=True) or {}
    cleaned, err = _validate_payload(data, partial=True)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    if not cleaned:
        return jsonify({"ok": False, "error": "empty_update"}), 400

    cleaned["updated_at"] = _now()
    db = _get_mongo_db()
    res = db["routing_overrides"].find_one_and_update(
        {"_id": obj_id},
        {"$set": cleaned},
        return_document=True,
    )
    if not res:
        return jsonify({"ok": False, "error": "not_found"}), 404
    _bump_cache()
    return jsonify({"ok": True, "item": _serialize(res)})


@routing_overrides_bp.route("/api/admin/routing-overrides/<oid>", methods=["DELETE"])
@admin_required
def delete_override(oid):
    try:
        obj_id = ObjectId(oid)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    db = _get_mongo_db()
    res = db["routing_overrides"].delete_one({"_id": obj_id})
    if res.deleted_count == 0:
        return jsonify({"ok": False, "error": "not_found"}), 404
    _bump_cache()
    return jsonify({"ok": True})


@routing_overrides_bp.route("/api/routing-overrides/active", methods=["GET"])
@admin_required
def list_active_for_display():
    """Endpoint lecture seule pour les visualisations cote front (modale
    routing notamment). Pas de filtre, juste les overrides actifs non expires."""
    return jsonify({"ok": True, "items": get_active_overrides_for_display()})
