"""momentus_lieux.py - Ce que Momentus prevoit dans un lieu de la carte.

Module pur (ni Flask ni connexion Mongo propre : `db` en argument).
⚠️ COPIE IDENTIQUE dans ../groundmaster/momentus_lieux.py : les deux apps sont
des depots separes. Toute modification se reporte dans les deux fichiers.

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

Information seulement : aucune notion de conflit ici.
"""

from datetime import date, datetime, timedelta

COL_MAPPING = "momentus_lieux_mapping"

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


def _geometry_point(geom):
    """(lat, lng) representatif d'une geometrie GeoJSON, polygone ou point."""
    if not isinstance(geom, dict):
        return None, None
    t, c = geom.get("type"), geom.get("coordinates")
    try:
        if t == "Point":
            return c[1], c[0]
        if t == "MultiPoint":
            return c[0][1], c[0][0]
        ring = c[0] if t == "Polygon" else c[0][0] if t == "MultiPolygon" else None
        if ring:
            pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
            return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))
    except (TypeError, IndexError, ZeroDivisionError):
        pass
    return None, None


def _features_geo(db, wanted):
    """{feature_id: {name, lat, lng, geometry}} pour les (collection -> {ids}) demandes."""
    out = {}
    for col, ids in wanted.items():
        if not col:
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
