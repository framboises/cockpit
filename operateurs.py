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
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import event_courant as EC
import pcorg_history as PH

PARIS = ZoneInfo("Europe/Paris")
SETTINGS_ID = "operateurs"
COL_POSTS = "cockpit_operator_posts"
# Fiches de poste : PCO.Information, sous-classification dediee (filtrable
# dans les regles Friday et les vues comme toute sous-classification)
FICHE_CATEGORY = "PCO.Information"
FICHE_SOUS_CLASSIFICATION = "Service"
FICHE_TAG = "poste_operateur"
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


# ---------------------------------------------------------------------------
# Postes
# ---------------------------------------------------------------------------
# Un poste est rattache au COMPTE (pas au navigateur) : tant qu'il est ouvert
# (`open: true`, statut active ou pause), toute ouverture de Cockpit le
# reprend sans nouvelle prise de poste ni nouvelle fiche. Index unique
# partiel sur {user_id} quand open = true : un seul poste ouvert par compte.

def ensure_indexes(db):
    col = db[COL_POSTS]
    col.create_index([("user_id", 1)], unique=True, name="uniq_open_post",
                     partialFilterExpression={"open": True})
    col.create_index([("open", 1), ("status", 1)], name="open_status")
    col.create_index([("started_at", -1)], name="started_desc")


def ensure_sous_classification(db):
    """Cree la sous-classification "Service" de PCO.Information si absente."""
    cfg = db["pcorg_config"]
    doc = cfg.find_one({"_id": "pcorg_lists"}, {"sous_classifications": 1}) or {}
    items = ((doc.get("sous_classifications") or {}).get(FICHE_CATEGORY)) or []
    if any(str((i or {}).get("label") or "").strip().lower() == FICHE_SOUS_CLASSIFICATION.lower()
           for i in items if isinstance(i, dict)):
        return
    cfg.update_one({"_id": "pcorg_lists"},
                   {"$push": {f"sous_classifications.{FICHE_CATEGORY}":
                              {"id": uuid.uuid4().hex[:8], "label": FICHE_SOUS_CLASSIFICATION}}},
                   upsert=True)


def user_id_for(db, email):
    if not email:
        return None
    u = db["users"].find_one({"email": email}, {"_id": 1})
    return u["_id"] if u else None


def open_post(db, user_id):
    return db[COL_POSTS].find_one({"user_id": user_id, "open": True}) if user_id else None


def public_post(post):
    if not post:
        return None
    return {
        "id": str(post["_id"]),
        "event": post.get("event"), "year": post.get("year"),
        "zones": post.get("zones") or [], "zones_labels": post.get("zones_labels") or [],
        "metier_id": post.get("metier_id"), "metier_label": post.get("metier_label"),
        "status": post.get("status"),
        "started_at": _iso(post.get("started_at")),
        "pause_since": _iso(post.get("pause_since")),
    }


def _iso(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PARIS).isoformat()


def options(db):
    """Choix proposes a la prise de poste."""
    evs = [{"event": a["event"], "year": int(a["year"]), "kind": a["kind"], "phase": a.get("phase")}
           for a in EC.active_events(db)]
    zs = zones(db)
    return {"events": evs, "zones": zs, "zones_configured": bool(get_settings(db)["zones_collection"]),
            "metiers": metiers(db)}


def validate_choice(db, event, year, zone_ids, metier_id):
    """Retourne ({event, year, zones, zones_labels, metier_id, metier_label}, erreur)."""
    try:
        year = int(year)
    except (TypeError, ValueError):
        return None, "annee_invalide"
    if not EC.is_active(db, event, year):
        return None, "evenement_non_en_cours"
    zone_ids = [str(z) for z in (zone_ids or []) if z]
    zone_ids = list(dict.fromkeys(zone_ids))
    by_id = {z["id"]: z for z in zones(db)}
    if by_id:
        if not zone_ids:
            return None, "zone_requise"
        unknown = [z for z in zone_ids if z not in by_id]
        if unknown:
            return None, "zone_inconnue"
    else:
        zone_ids = []
    mets = {m["id"]: m for m in metiers(db)}
    if mets:
        if metier_id not in mets:
            return None, "metier_requis"
    else:
        metier_id = None
    return {"event": event, "year": year, "zones": zone_ids,
            "zones_labels": [by_id[z]["label"] for z in zone_ids],
            "metier_id": metier_id,
            "metier_label": mets[metier_id]["label"] if metier_id else None}, None


def _operator_name(user):
    return f"{user.get('firstname', '')} {user.get('lastname', '')}".strip() or user.get("email", "")


def create_fiche(db, post, kind, text, user, now=None):
    """Fiche Information "Service" de la main courante, cloturee des sa
    creation (simple trace). `_id` deterministe (poste + type + horodatage
    a la seconde) : un double envoi ne cree pas deux fiches. Pas de dispatch
    automatique."""
    now = now or datetime.now(PARIS)
    operator = _operator_name(user)
    email = user.get("email", "")
    doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                            f"poste-operateur|{post['_id']}|{kind}|{now.strftime('%Y%m%d%H%M%S')}"))
    history = [PH.make_entry(operator, text, ts=now),
               PH.make_entry(operator, "Statut: En cours -> Terminé", ts=now)]
    doc = {
        "_id": doc_id,
        "event": post["event"],
        "year": int(post["year"]),
        "ts": now,
        "timestamp_iso": now.isoformat(),
        "created_at": now,
        "close_ts": now,
        "close_iso": now.isoformat(),
        "category": FICHE_CATEGORY,
        "source": FICHE_CATEGORY,
        "text": text,
        "text_full": text,
        "comment": PH.render_comment(history),
        "comment_history": history,
        "operator": operator,
        "operator_id_create": email,
        "operator_close": operator,
        "operator_id_close": email,
        "status_code": 10,
        "severity": 0,
        "niveau_urgence": None,
        "is_incident": False,
        "area": {"id": None, "desc": ", ".join(post.get("zones_labels") or [])} if post.get("zones_labels") else None,
        "gps": None,
        "group": None,
        "content_category": {"sous_classification": FICHE_SOUS_CLASSIFICATION},
        "extracted": {"phones": None, "plates": None},
        "tags": [FICHE_TAG],
        "operator_post_id": str(post["_id"]),
        "operator_post_kind": kind,
        "synced_at": None,
        "sql_id": None,
        "guid": None,
        "server": "COCKPIT",
        "bounce_rev": 1,
    }
    try:
        db["pcorg"].insert_one(doc)
    except Exception as exc:  # DuplicateKeyError : double envoi, fiche deja la
        if "duplicate key" not in str(exc).lower():
            raise
    return doc_id


def _describe(choice):
    parts = []
    if choice.get("zones_labels"):
        parts.append("Zones : " + ", ".join(choice["zones_labels"]))
    if choice.get("metier_label"):
        parts.append("Metier : " + choice["metier_label"])
    return " - ".join(parts)


def take_post(db, user, user_id, choice):
    """Prise de poste. Retourne (post, created). Un poste deja ouvert est
    renvoye tel quel (reprise, aucune fiche)."""
    existing = open_post(db, user_id)
    if existing:
        return existing, False
    now = datetime.now(PARIS)
    post = {
        "user_id": user_id,
        "email": user.get("email", ""),
        "name": _operator_name(user),
        **choice,
        "status": "active",
        "open": True,
        "started_at": now,
        "ended_at": None,
        "end_reason": None,
        "pause_since": None,
        "history": [],
    }
    try:
        ins = db[COL_POSTS].insert_one(post)
    except Exception as exc:  # deux onglets en meme temps : l'autre a gagne
        if "duplicate key" not in str(exc).lower():
            raise
        return open_post(db, user_id), False
    post["_id"] = ins.inserted_id
    text = "Prise de poste - " + post["name"]
    desc = _describe(choice)
    if desc:
        text += " - " + desc
    fiche_id = create_fiche(db, post, "prise", text, user, now=now)
    db[COL_POSTS].update_one({"_id": post["_id"]},
                             {"$push": {"history": {"at": now, "type": "prise", "fiche_id": fiche_id}}})
    return post, True


def validate_zones_metier(db, zone_ids, metier_id):
    """Comme validate_choice sans l'evenement (changement en cours de poste :
    l'evenement du poste reste celui de la prise de poste)."""
    zone_ids = list(dict.fromkeys(str(z) for z in (zone_ids or []) if z))
    by_id = {z["id"]: z for z in zones(db)}
    if by_id:
        if not zone_ids:
            return None, "zone_requise"
        if any(z not in by_id for z in zone_ids):
            return None, "zone_inconnue"
    else:
        zone_ids = []
    mets = {m["id"]: m for m in metiers(db)}
    if mets:
        if metier_id not in mets:
            return None, "metier_requis"
    else:
        metier_id = None
    return {"zones": zone_ids, "zones_labels": [by_id[z]["label"] for z in zone_ids],
            "metier_id": metier_id,
            "metier_label": mets[metier_id]["label"] if metier_id else None}, None


def change_zones(db, post, user, choice):
    """Changement de zones et/ou de metier sans fin de poste. Fiche
    "Changement de zone" cloturee. Retourne le poste a jour (None si le poste
    a ete ferme entre-temps)."""
    if choice["zones"] == (post.get("zones") or []) and choice["metier_id"] == post.get("metier_id"):
        return post
    now = datetime.now(PARIS)
    updated = db[COL_POSTS].find_one_and_update(
        {"_id": post["_id"], "open": True}, {"$set": choice}, return_document=True)
    if not updated:
        return None
    parts = []
    old_z, new_z = post.get("zones_labels") or [], choice["zones_labels"]
    if old_z != new_z:
        parts.append("Zones : " + (", ".join(old_z) or "aucune") + " -> " + (", ".join(new_z) or "aucune"))
    if post.get("metier_id") != choice["metier_id"]:
        parts.append("Metier : " + (post.get("metier_label") or "aucun") + " -> " + (choice["metier_label"] or "aucun"))
    text = "Changement de zone - " + updated["name"] + (" - " + " - ".join(parts) if parts else "")
    fiche_id = create_fiche(db, updated, "zones", text, user, now=now)
    db[COL_POSTS].update_one({"_id": post["_id"]}, {"$push": {"history": {
        "at": now, "type": "zones", "fiche_id": fiche_id,
        "from": {"zones": post.get("zones") or [], "metier_id": post.get("metier_id")},
        "to": {"zones": choice["zones"], "metier_id": choice["metier_id"]}}}})
    return updated


def set_pause(db, post, pause):
    """Pause DECLAREE (jamais deduite) : ses zones sortent du routage des
    alertes. Pas de fiche. Retourne le poste a jour (None si ferme)."""
    now = datetime.now(PARIS)
    want, other = ("pause", "active") if pause else ("active", "pause")
    updated = db[COL_POSTS].find_one_and_update(
        {"_id": post["_id"], "open": True, "status": other},
        {"$set": {"status": want, "pause_since": now if pause else None},
         "$push": {"history": {"at": now, "type": "pause" if pause else "retour"}}},
        return_document=True)
    if updated:
        return updated
    return db[COL_POSTS].find_one({"_id": post["_id"], "open": True})


def _duration(start, end):
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    mins = max(0, int((end - start).total_seconds() // 60))
    h, m = divmod(mins, 60)
    return f"{h} h {m:02d}" if h else f"{m} min"


def end_post(db, post, user, reason, by_admin=False):
    """Fin de poste : fiche "Fin de poste" cloturee, poste ferme. `user` =
    celui qui ferme (l'operateur, ou l'admin pour une cloture admin).
    Retourne True si c'est cet appel qui a ferme le poste."""
    now = datetime.now(PARIS)
    closed = db[COL_POSTS].find_one_and_update(
        {"_id": post["_id"], "open": True},
        {"$set": {"status": "ended", "ended_at": now, "end_reason": reason, "pause_since": None,
                  "ended_by": user.get("email", "")},
         "$unset": {"open": ""}},
        return_document=True)
    if not closed:
        return False
    text = "Fin de poste - " + closed["name"]
    desc = _describe(closed)
    if desc:
        text += " - " + desc
    text += " - Duree : " + _duration(closed["started_at"], now)
    if by_admin:
        text += " - Cloture par " + _operator_name(user)
    elif reason == "logout":
        text += " - Deconnexion"
    fiche_id = create_fiche(db, closed, "fin", text, user, now=now)
    db[COL_POSTS].update_one({"_id": closed["_id"]}, {"$push": {"history": {
        "at": now, "type": "fin", "fiche_id": fiche_id, "reason": reason}}})
    return True


def list_open_posts(db):
    out = []
    for p in db[COL_POSTS].find({"open": True}).sort("started_at", 1):
        pub = public_post(p)
        pub.update({"name": p.get("name"), "email": p.get("email")})
        out.append(pub)
    return out
