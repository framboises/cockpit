"""momentus_lieux.py - Ce que Momentus prevoit dans un lieu de la carte.

Module pur (ni Flask ni connexion Mongo propre : `db` en argument).
⚠️ COPIE IDENTIQUE dans cockpit, groundmaster et looker (momentus_lieux.py) :
depots separes. Toute modification se reporte dans les trois fichiers.

Donnees :
  - momentus_events / momentus_rooms : alimentees par cockpit/momentus_sync.py
  - momentus_lieux_mapping : correspondance espace Momentus -> lieu carto,
    editee dans Groundmaster (/momentus/lieux). Un doc par espace Momentus :
      {_id: <roomId Momentus>, room_name, venue_name,
       status: "valide" | "sans_lieu",
       collection, feature_id (= properties._id_feature), feature_name,
       updated_at, updated_by}
    Seuls les rattachements `valide` sont exploites : une proposition
    automatique n'est jamais consideree comme vraie tant qu'un humain ne l'a
    pas confirmee (ex. "Hunaudieres" du PEC, une salle, rapproche a tort du
    parking HUNAUDIERES).
  - momentus_lieux_sources : couches carto hors categories Groundmaster dont
    les lieux peuvent etre rattaches, editees par les super_admin dans
    Groundmaster. Un doc par collection, qui decrit son format :
      {_id: <collection>, label,
       liste: "features" (chemin d'une liste de lieux dans chaque doc) | "" (un doc = un lieu),
       id: "properties._id_feature", nom: "properties.Name" (chemins dans un lieu),
       nom_modele: "Box {}" (optionnel : {} = valeur du champ nom),
       position: {mode: "geojson", champ}            geometrie GeoJSON
               | {mode: "latlng", lat, lng}           deux champs numeriques
               | {mode: "point", champ, ordre}        "47.9, 0.2", [a, b] ou {lat, lng}
                                                      ordre "latlng" | "lnglat" (texte et liste)
       updated_at, updated_by}
    Une collection absente de cette liste est lue au format historique
    (features[].properties._id_feature + geometry).

Information seulement : aucune notion de conflit ici.
"""

import re
from datetime import date, datetime, timedelta

COL_MAPPING = "momentus_lieux_mapping"
COL_SOURCES = "momentus_lieux_sources"

# Identifiant de lieu utilisable dans une URL (/api/momentus/lieu/<feature_id>).
FEATURE_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")

# ⚠️ "moveIn" est le mode de reservation PAR DEFAUT dans Momentus (79 % des
# espaces des seminaires, 91 % des travaux, mesure en septembre 2026), pas un
# montage : le seminaire CPAM de 434 personnes y est saisi en moveIn de 8h30 a
# 17h. On l'affiche donc "reserve", libelle neutre. "event", "moveOut" et
# "dark" sont des choix volontaires de la personne qui saisit.
PHASES = {
    "moveIn": "reserve",
    "event": "exploitation",
    "moveOut": "demontage",
    "dark": "bloque",
}
PHASE_ORDER = {"reserve": 0, "exploitation": 1, "demontage": 2, "bloque": 3}

MAX_RANGE_DAYS = 400


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def event_status(ev):
    """Statut commercial lisible d'un evenement Momentus."""
    if ev.get("isDefinite"):
        return "confirme"
    if ev.get("isTentative"):
        return "option"
    if ev.get("isProspect"):
        return "prospect"
    return "autre"


def mapped_features(db):
    """{feature_id: [roomId, ...]} des rattachements valides."""
    out = {}
    for m in db[COL_MAPPING].find({"status": "valide", "feature_id": {"$nin": [None, ""]}},
                                  {"feature_id": 1}):
        out.setdefault(m["feature_id"], []).append(m["_id"])
    return out


def rooms_for_feature(db, feature_id):
    return [m["_id"] for m in db[COL_MAPPING].find(
        {"status": "valide", "feature_id": feature_id}, {"_id": 1})]


def event_window(db, event, year):
    """(debut montage, fin demontage) du parametrage, en dates. None si absent."""
    if not event or not year:
        return None
    if str(event).strip().upper() == "SAISON":
        # SAISON n'a ni montage ni demontage ; ses jours publics sont des jours
        # de visites libres epars sur l'annee (02/10/2026) : le repli sur les
        # jours publics ferait une "fenetre" de janvier a decembre. None =
        # periode par defaut (aujourd'hui + 60 j), comme avant.
        return None
    try:
        years = [int(year), str(year)]
    except (TypeError, ValueError):
        years = [year]
    p = db["parametrages"].find_one(
        {"event": event, "year": {"$in": years}},
        {"data.globalHoraires.montage": 1, "data.globalHoraires.demontage": 1,
         "data.globalHoraires.dates": 1})
    gh = ((p or {}).get("data") or {}).get("globalHoraires") or {}
    start = _day((gh.get("montage") or {}).get("start"))
    end = _day((gh.get("demontage") or {}).get("end"))
    if not start or not end:
        # Repli : jours publics seuls
        days = sorted(d for d in (_day(x.get("date")) for x in gh.get("dates") or []
                                  if isinstance(x, dict)) if d)
        if days:
            start, end = start or days[0], end or days[-1]
    if not start or not end or end < start:
        return None
    return start, end


def _merge_segments(segments):
    """Fusionne les segments contigus d'une meme phase (jours consecutifs).

    segments : [(phase, start, end, start_time, end_time, room_name)]
    Deux segments ne fusionnent que s'ils n'ont pas d'horaires propres.
    """
    segments = sorted(segments, key=lambda s: (s[5], s[1], PHASE_ORDER.get(s[0], 9)))
    merged = []
    for seg in segments:
        if merged:
            last = merged[-1]
            if (last["phase"] == seg[0] and last["room"] == seg[5]
                    and not last["start_time"] and not seg[3]
                    and _day(last["end"]) + timedelta(days=1) >= seg[1]):
                if seg[2] > _day(last["end"]):
                    last["end"] = seg[2].isoformat()
                continue
        merged.append({"phase": seg[0], "start": seg[1].isoformat(), "end": seg[2].isoformat(),
                       "start_time": seg[3], "end_time": seg[4], "room": seg[5]})
    merged.sort(key=lambda s: (s["start"], PHASE_ORDER.get(s["phase"], 9), s["room"]))
    return merged


def bookings_for_rooms(db, room_ids, date_from, date_to, with_functions=True):
    """Reservations Momentus actives des espaces `room_ids` sur [date_from, date_to].

    Rend une liste d'evenements (un par client/reservation), tries par date :
      {event_id, name, account, type, status, attendance, start, end, rooms,
       phases: [{phase, start, end, start_time, end_time, room}],
       functions: [{date, start_time, end_time, name, room, type, attendance}]}
    Ni contacts, ni montants : c'est de l'information d'exploitation.
    """
    if not room_ids:
        return []
    if (date_to - date_from).days > MAX_RANGE_DAYS:
        date_to = date_from + timedelta(days=MAX_RANGE_DAYS)
    lo, hi = date_from.isoformat(), date_to.isoformat()
    room_set = set(room_ids)
    q = {
        "_sync.deleted_at": None, "isCanceled": {"$ne": True}, "isLost": {"$ne": True},
        "bookedSpaces": {"$elemMatch": {"roomId": {"$in": list(room_set)},
                                        "startDate": {"$lte": hi}, "endDate": {"$gte": lo}}},
    }
    proj = {"name": 1, "accountName": 1, "eventTypeName": 1, "estimatedTotalAttendance": 1,
            "estimatedAttendance": 1, "isDefinite": 1, "isTentative": 1, "isProspect": 1,
            "bookedSpaces": 1, "isInternal": 1}
    out = []
    for ev in db["momentus_events"].find(q, proj):
        segs = []
        for bs in ev.get("bookedSpaces") or []:
            if bs.get("roomId") not in room_set:
                continue
            a, b = _day(bs.get("startDate")), _day(bs.get("endDate"))
            if not a or not b or b < date_from or a > date_to:
                continue
            segs.append((PHASES.get(bs.get("usageType"), bs.get("usageType") or "autre"),
                         max(a, date_from), min(b, date_to),
                         None if bs.get("isAllDay") else bs.get("startTime"),
                         None if bs.get("isAllDay") else bs.get("endTime"),
                         (bs.get("roomName") or "").strip()))
        if not segs:
            continue
        phases = _merge_segments(segs)
        out.append({
            "event_id": ev["_id"],
            "name": (ev.get("name") or "").strip(),
            "account": (ev.get("accountName") or "").strip(),
            "type": (ev.get("eventTypeName") or "").strip(),
            "internal": bool(ev.get("isInternal")),
            "status": event_status(ev),
            "attendance": ev.get("estimatedTotalAttendance") or ev.get("estimatedAttendance") or None,
            "start": min(p["start"] for p in phases),
            "end": max(p["end"] for p in phases),
            "rooms": sorted({p["room"] for p in phases if p["room"]}),
            "phases": phases,
            "functions": [],
        })
    if with_functions and out:
        by_event = {e["event_id"]: e for e in out}
        fq = {"_sync.deleted_at": None, "eventId": {"$in": list(by_event)},
              "roomId": {"$in": list(room_set)}, "startDate": {"$lte": hi},
              "endDate": {"$gte": lo}}
        for fn in db["momentus_functions"].find(fq).sort([("startDate", 1), ("startTime", 1)]):
            by_event[fn["eventId"]]["functions"].append({
                "date": fn.get("startDate"),
                "end_date": fn.get("endDate"),
                "start_time": None if fn.get("isAllDay") else fn.get("startTime"),
                "end_time": None if fn.get("isAllDay") else fn.get("endTime"),
                "name": (fn.get("name") or "").strip(),
                "room": (fn.get("roomName") or "").strip(),
                "type": (fn.get("functionTypeName") or "").strip(),
                "status": fn.get("functionStatus"),
                "attendance": fn.get("expectedAttendance") or fn.get("agreedAttendance") or None,
            })
    out.sort(key=lambda e: (e["start"], e["name"]))
    return out


def lieu_payload(db, feature_id, date_from=None, date_to=None, event=None, year=None):
    """Reponse complete pour un lieu : periode retenue, espaces rattaches, reservations.

    Periode : [date_from, date_to] si fournis, sinon montage -> demontage de
    (event, year), sinon aujourd'hui -> +60 j.
    """
    window_source = "dates"
    if not (date_from and date_to):
        win = event_window(db, event, year)
        if win:
            date_from, date_to = win
            window_source = "evenement"
        else:
            today = date.today()
            date_from, date_to = today, today + timedelta(days=60)
            window_source = "defaut"
    room_ids = rooms_for_feature(db, feature_id)
    rooms = list(db["momentus_rooms"].find({"_id": {"$in": room_ids}}, {"name": 1, "venueName": 1}))
    state = db["momentus_sync_state"].find_one({"_id": "state"}, {"last_success_at": 1}) or {}
    last = state.get("last_success_at")
    return {
        "ok": True,
        "feature_id": feature_id,
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "window_source": window_source,
        "rooms": [{"id": r["_id"], "name": (r.get("name") or "").strip(), "venue": r.get("venueName")}
                  for r in rooms],
        "bookings": bookings_for_rooms(db, room_ids, date_from, date_to),
        "synced_at": last.isoformat() + "Z" if isinstance(last, datetime) else None,
    }


def _traversees(rings, lat):
    """Longitudes ou la parallele `lat` coupe les anneaux, triees.

    Regle pair-impair : entre la 1re et la 2e, la 3e et la 4e... on est dans la
    forme (hors des trous).
    """
    xs = []
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 <= lat < y2) or (y2 <= lat < y1):
                xs.append(x1 + (lat - y1) * (x2 - x1) / (y2 - y1))
    return sorted(xs)


def _point_sur_forme(rings, lat, lng):
    """(lat, lng) dans la forme : le point donne s'il y est, sinon le milieu de
    la traversee la plus proche sur la meme parallele (ruban d'une piste dont le
    centre tombe dans le trou, forme en croissant)."""
    xs = _traversees(rings, lat)
    if sum(1 for x in xs if x > lng) % 2:
        return lat, lng
    milieux = [(xs[i] + xs[i + 1]) / 2 for i in range(0, len(xs) - 1, 2)]
    if not milieux:
        return lat, lng
    return lat, min(milieux, key=lambda x: abs(x - lng))


def _geometry_point(geom):
    """(lat, lng) representatif d'une geometrie GeoJSON : le point, ou pour un
    polygone un point a l'interieur de la forme (pin pose sur le lieu, pas dans
    le trou d'une piste)."""
    if not isinstance(geom, dict):
        return None, None
    t, c = geom.get("type"), geom.get("coordinates")
    try:
        if t == "Point":
            return c[1], c[0]
        if t == "MultiPoint":
            return c[0][1], c[0][0]
        polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else None
        ring = polys[0][0] if polys else None
        if ring:
            pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
            lat, lng = sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)
            return _point_sur_forme([[(p[0], p[1]) for p in r] for poly in polys for r in poly], lat, lng)
    except (TypeError, IndexError, ZeroDivisionError):
        pass
    return None, None


def get_path(obj, path):
    """Valeur au chemin pointe `a.b.c` (None si absente) ; chemin vide = l'objet."""
    if not path:
        return obj
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip().replace(",", "."))
        except ValueError:
            return None
    return None


def _pair(a, b, ordre):
    """(lat, lng) d'un couple de nombres dans l'ordre annonce, None si hors bornes."""
    a, b = _num(a), _num(b)
    if a is None or b is None:
        return None
    lat, lng = (a, b) if ordre == "latlng" else (b, a)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        return None
    return lat, lng


def point_value(v, ordre="latlng"):
    """(lat, lng) d'une position libre : texte "47.9, 0.2", liste [a, b], {lat, lng} ou GeoJSON."""
    if isinstance(v, str):
        parts = [p for p in re.split(r"[,;\s]+", v.strip()) if p]
        return _pair(parts[0], parts[1], ordre) if len(parts) == 2 else None
    if isinstance(v, (list, tuple)):
        return _pair(v[0], v[1], ordre) if len(v) == 2 else None
    if isinstance(v, dict):
        if "coordinates" in v:
            lat, lng = _geometry_point(v)
            return (lat, lng) if lat is not None else None
        low = {str(k).lower(): x for k, x in v.items()}
        lat = next((low[k] for k in ("lat", "latitude") if k in low), None)
        lng = next((low[k] for k in ("lng", "lon", "long", "longitude") if k in low), None)
        return _pair(lat, lng, "latlng")
    return None


def source_position(item, src):
    """Geometrie GeoJSON d'un lieu selon le format de sa couche (None si absente)."""
    pos = src.get("position") or {}
    mode = pos.get("mode")
    if mode == "geojson":
        geom = get_path(item, pos.get("champ") or "geometry")
        lat, _ = _geometry_point(geom)
        return geom if lat is not None else None
    if mode == "latlng":
        ll = _pair(get_path(item, pos.get("lat")), get_path(item, pos.get("lng")), "latlng")
    elif mode == "point":
        ll = point_value(get_path(item, pos.get("champ")), pos.get("ordre") or "latlng")
    else:
        return None
    return {"type": "Point", "coordinates": [ll[1], ll[0]]} if ll else None


def source_projection(src):
    """Projection Mongo : seuls les champs de premier niveau utiles a la couche."""
    pos = src.get("position") or {}
    paths = [src.get("liste")] if src.get("liste") else [
        src.get("id"), src.get("nom"), pos.get("champ"), pos.get("lat"), pos.get("lng")]
    return {p.split(".")[0]: 1 for p in paths if p} or None


def iter_source(db, src, limit=None):
    """Lieux bruts d'une couche configuree : (item, id_brut, nom_brut, geometrie).

    `id_brut` est rendu en texte (ObjectId compris), None si absent. `limit`
    borne le nombre de lieux parcourus (une collection de positions GPS peut
    en contenir des millions).
    """
    n = 0
    for doc in db[src["_id"]].find({}, source_projection(src)):
        items = get_path(doc, src.get("liste")) if src.get("liste") else [doc]
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            if limit is not None and n >= limit:
                return
            n += 1
            fid = get_path(it, src.get("id"))
            fid = None if fid is None or isinstance(fid, (dict, list)) else str(fid).strip()
            name = get_path(it, src.get("nom"))
            if isinstance(name, float) and name.is_integer():
                name = int(name)
            name = str(name).strip() if isinstance(name, (str, int, float)) and not isinstance(name, bool) else ""
            if name and src.get("nom_modele"):
                name = src["nom_modele"].replace("{}", name).strip()
            yield it, fid or None, name, source_position(it, src)


def _features_geo(db, wanted):
    """{feature_id: {name, lat, lng, geometry}} pour les (collection -> {ids}) demandes."""
    out = {}
    sources = {s["_id"]: s for s in db[COL_SOURCES].find({"_id": {"$in": [c for c in wanted if c]}})}
    for col, ids in wanted.items():
        if not col:
            continue
        src = sources.get(col)
        if src:
            for _, fid, name, geom in iter_source(db, src):
                if fid not in ids or not geom:
                    continue
                lat, lng = _geometry_point(geom)
                out[fid] = {"name": name, "lat": lat, "lng": lng,
                            "geometry": geom if geom.get("type") in ("Polygon", "MultiPolygon") else None}
            continue
        for doc in db[col].find({}, {"features": 1, "properties": 1, "geometry": 1}):
            feats = doc.get("features") if isinstance(doc.get("features"), list) else [doc]
            for f in feats:
                p = (f or {}).get("properties") or {}
                fid = p.get("_id_feature")
                if fid not in ids:
                    continue
                lat, lng = _geometry_point(f.get("geometry"))
                if lat is None:
                    continue
                geom = f.get("geometry") or {}
                out[fid] = {
                    "name": p.get("NOM") or p.get("Name") or p.get("Nom") or p.get("nom") or p.get("name"),
                    "lat": lat, "lng": lng,
                    "geometry": geom if geom.get("type") in ("Polygon", "MultiPolygon") else None,
                }
    return out


def carte_payload(db, date_from=None, date_to=None, event=None, year=None):
    """Calque carte : lieux rattaches ayant au moins une reservation sur la periode.

    Independant des lieux actives dans Groundmaster pour l'evenement : c'est
    justement l'interet de voir un lieu occupe que l'evenement n'utilise pas.
    """
    window_source = "dates"
    if not (date_from and date_to):
        win = event_window(db, event, year)
        if win:
            date_from, date_to = win
            window_source = "evenement"
        else:
            today = date.today()
            date_from, date_to = today, today + timedelta(days=60)
            window_source = "defaut"
    maps = list(db[COL_MAPPING].find({"status": "valide", "feature_id": {"$nin": [None, ""]}},
                                     {"feature_id": 1, "collection": 1, "feature_name": 1, "category": 1}))
    by_feature, wanted = {}, {}
    for m in maps:
        f = by_feature.setdefault(m["feature_id"], {"rooms": [], "collection": m.get("collection"),
                                                    "name": m.get("feature_name"), "category": m.get("category")})
        f["rooms"].append(m["_id"])
        wanted.setdefault(m.get("collection"), set()).add(m["feature_id"])
    geo = _features_geo(db, wanted)
    today = date.today().isoformat()
    lieux = []
    for fid, f in by_feature.items():
        g = geo.get(fid)
        if not g:
            continue
        bookings = bookings_for_rooms(db, f["rooms"], date_from, date_to, with_functions=False)
        if not bookings:
            continue
        now = [b for b in bookings if any(p["start"] <= today <= p["end"] for p in b["phases"])]
        lieux.append({
            "feature_id": fid,
            "name": g["name"] or f["name"],
            "category": f["category"],
            "lat": g["lat"], "lng": g["lng"], "geometry": g["geometry"],
            "count": len(bookings),
            "today": len(now),
            "bookings": [{"name": b["name"], "status": b["status"], "start": b["start"], "end": b["end"],
                          "phases": sorted({p["phase"] for p in b["phases"]},
                                           key=lambda ph: PHASE_ORDER.get(ph, 9))}
                         for b in bookings[:8]],
        })
    lieux.sort(key=lambda x: (-x["count"], x["name"] or ""))
    return {"ok": True, "from": date_from.isoformat(), "to": date_to.isoformat(),
            "window_source": window_source, "lieux": lieux}


def parse_day_arg(value):
    """Date ISO d'un parametre de requete, None si absente ou invalide."""
    return _day(value) if value else None
