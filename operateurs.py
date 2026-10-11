"""operateurs.py - Prise de poste des operateurs : referentiel zones / metiers.

Plan complet : docs/claude/operateurs.md.

- Zones : une couche polygones de Looker (`categories_config.categories`,
  dataType geojson). AUCUNE copie : les features sont lues en direct dans la
  collection de la couche (meme base que Looker), designees par
  `properties._id_feature` (stable, jamais le nom).
- Metiers : lus en direct dans `bible_structure` (Bibler), niveau metier.
- Reglages : `cockpit_settings._id = "operateurs"`.
"""

import threading
import time
from datetime import datetime, timezone

SETTINGS_ID = "operateurs"
POLYGON_TYPES = ("Polygon", "MultiPolygon")
_LAYER_GEOMETRY_TYPES = ("polygon", "multipolygon")
_ZONES_TTL_S = 60

_cache_lock = threading.Lock()
_zones_cache = {}  # collection -> (expires, label_field, zones)


# ---------------------------------------------------------------------------
# Couches Looker
# ---------------------------------------------------------------------------

def _looker_categories(db):
    doc = db["categories_config"].find_one({}, {"_id": 0, "categories": 1}) or {}
    return [c for c in (doc.get("categories") or []) if isinstance(c, dict)]


def _strip_prop(field):
    field = str(field or "")
    return field[len("properties."):] if field.startswith("properties.") else field


def layer_features(db, collection):
    """Features GeoJSON d'une collection Looker (un ou plusieurs documents
    FeatureCollection, comme /gm_collection_data)."""
    features = []
    for d in db[collection].find({"type": "FeatureCollection"}, {"features": 1, "_id": 0}):
        features.extend(f for f in (d.get("features") or []) if isinstance(f, dict))
    return features


def eligible_layers(db):
    """Couches polygones de Looker proposables comme couche de zones."""
    out = []
    for c in _looker_categories(db):
        coll = c.get("collection_name")
        if not coll or c.get("dataType") != "geojson":
            continue
        if str(c.get("geometryType") or "").lower() not in _LAYER_GEOMETRY_TYPES:
            continue
        feats = layer_features(db, coll)
        polys = [f for f in feats if ((f.get("geometry") or {}).get("type")) in POLYGON_TYPES]
        props = []
        for f in polys[:50]:
            for k in (f.get("properties") or {}).keys():
                if k not in props and k != "_id_feature":
                    props.append(k)
        out.append({
            "id": c.get("id"),
            "label": c.get("label") or coll,
            "collection": coll,
            "search_field": _strip_prop(c.get("searchField")),
            "properties": props,
            "n_zones": len(polys),
            "n_sans_id": sum(1 for f in polys if not (f.get("properties") or {}).get("_id_feature")),
        })
    out.sort(key=lambda x: str(x["label"]).lower())
    return out


# ---------------------------------------------------------------------------
# Reglages
# ---------------------------------------------------------------------------

def get_settings(db):
    doc = db["cockpit_settings"].find_one({"_id": SETTINGS_ID}) or {}
    return {
        "zones_collection": doc.get("zones_collection") or None,
        "zones_label_field": doc.get("zones_label_field") or None,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


def save_settings(db, body, email):
    """Valide et enregistre. Retourne (settings, erreur)."""
    coll = body.get("zones_collection") or None
    label_field = body.get("zones_label_field") or None
    if coll is not None:
        layer = next((l for l in eligible_layers(db) if l["collection"] == coll), None)
        if not layer:
            return None, "couche_inconnue"
        if label_field is None:
            label_field = layer["search_field"] or None
        if label_field is not None and label_field not in layer["properties"]:
            return None, "propriete_inconnue"
    else:
        label_field = None
    db["cockpit_settings"].update_one(
        {"_id": SETTINGS_ID},
        {"$set": {"zones_collection": coll, "zones_label_field": label_field,
                  "updated_at": datetime.now(timezone.utc), "updated_by": email}},
        upsert=True,
    )
    with _cache_lock:
        _zones_cache.clear()
    return get_settings(db), None


# ---------------------------------------------------------------------------
# Zones et metiers
# ---------------------------------------------------------------------------

def zones(db, fresh=False, collection=None, label_field=None):
    """Zones de la couche choisie : [{id, label, geometry}], triees par nom.
    Seules les features polygones portant un `_id_feature` comptent (sans
    identifiant stable, une zone ne peut pas etre designee). Cache 60 s.
    `collection` / `label_field` : apercu d'une autre couche (admin), deja
    validee par l'appelant."""
    if collection:
        coll = collection
    else:
        st = get_settings(db)
        coll = st["zones_collection"]
        label_field = st["zones_label_field"]
    if not coll:
        return []
    now = time.monotonic()
    with _cache_lock:
        hit = _zones_cache.get(coll)
        if hit and not fresh and hit[0] > now and hit[1] == label_field:
            return hit[2]
    out = []
    for f in layer_features(db, coll):
        geom = f.get("geometry") or {}
        props = f.get("properties") or {}
        fid = props.get("_id_feature")
        if geom.get("type") not in POLYGON_TYPES or not fid:
            continue
        label = props.get(label_field) if label_field else None
        out.append({"id": str(fid), "label": str(label).strip() if label not in (None, "") else str(fid),
                    "geometry": geom})
    out.sort(key=lambda z: z["label"].lower())
    with _cache_lock:
        _zones_cache[coll] = (now + _ZONES_TTL_S, label_field, out)
    return out


def metiers(db):
    """Metiers de Bibler (`bible_structure`) : [{id, label}]."""
    return [{"id": str(d["_id"]), "label": str(d.get("metier") or d["_id"])}
            for d in db["bible_structure"].find({}, {"_id": 1, "metier": 1}).sort("metier", 1)]
