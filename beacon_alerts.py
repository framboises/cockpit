"""beacon_alerts.py - Alertes des groupes de balises GPS (centrale d'alerte).

Detecteurs appeles par alert_engine.py (tache planifiee, 30 s), quelle que
soit la source de la balise (Anoloc, Teltonika) : ils ne lisent que les
collections communes (anoloc_latest, anoloc_positions) et l'etat
balises_core.device_state. Les tablettes Field ne sont pas concernees.
Regles et parametres : docs/claude/balises.md.

  beacon_speed     vitesse > max_kmh sur `consecutive` points
  beacon_zone      entree / sortie de zones d'une couche Looker (hysteresis)
  beacon_stopped   immobile (rayon radius_m) depuis plus de max_min minutes,
                   optionnellement seulement dans des zones choisies
  beacon_silent    balise passee de en ligne a muette
  beacon_battery   batterie <= threshold_pct (hors charge)
  beacon_movement  passage de l'arret au mouvement

Parametres communs : groups ([] = tous les groupes actifs), cooldown_min.
Etat par definition : cockpit_alert_engine_state._id = "beacon:<slug>"
(rafraichi a chaque cycle, donc jamais purge par le menage des 2 jours).

Uniquement le TEMPS REEL : un point date de plus de LIVE_WINDOW_MIN (un
rattrapage Teltonika apres une coupure reseau) ne declenche rien.
"""

import logging
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import balises_core as B

log = logging.getLogger("alert_engine")
PARIS = ZoneInfo("Europe/Paris")
LIVE_WINDOW_MIN = 10
ALERT_TTL_MIN = 30

DETECTION_TYPES = ("beacon_speed", "beacon_zone", "beacon_stopped",
                   "beacon_silent", "beacon_battery", "beacon_movement")


def _utc(dt):
    return B._utc(dt)


def haversine(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Donnees communes
# ---------------------------------------------------------------------------

def target_devices(db, params):
    """{device_id: groupe} des balises visees (hors tablettes)."""
    wanted = set(params.get("groups") or [])
    cfg = db["anoloc_config"].find_one({"_id": "global"}, {"beacon_groups": 1, "enabled": 1}) or {}
    if not cfg.get("enabled"):
        return {}
    out = {}
    for g in cfg.get("beacon_groups") or []:
        if g.get("enabled") is False or (wanted and g.get("id") not in wanted):
            continue
        for did in g.get("anoloc_device_ids") or []:
            if not str(did).startswith("field:"):
                out.setdefault(did, g)
    return out


def new_points(db, did, since, now):
    """Points valides de la balise plus recents que `since` et que la
    fenetre temps reel, tries. Heure du point = sent_at (Anoloc : la meme
    trame est relue a chaque collecte, collected_at change mais pas sent_at)
    ou collected_at (Teltonika : horodatage balise)."""
    floor = now - timedelta(minutes=LIVE_WINDOW_MIN)
    since = max(_utc(since) or floor, floor)
    pts = {}
    for p in db[B.COL_POSITIONS].find(
            {"device_id": did, "collected_at": {"$gt": since - timedelta(minutes=2)}},
            {"lat": 1, "lng": 1, "speed": 1, "sent_at": 1, "collected_at": 1, "_id": 0}):
        if p.get("lat") is None or p.get("lng") is None:
            continue
        t = _utc(p.get("sent_at")) or _utc(p.get("collected_at"))
        if t is None or t <= since or t > now + timedelta(minutes=5):
            continue
        pts[t] = p
    return [(t, pts[t]) for t in sorted(pts)]


def _paris_hm(dt):
    return dt.astimezone(PARIS).strftime("%H:%M")


def _alert(definition, context, did, grp, latest, title, message, dedup, extra=None, at=None):
    now = context["now"]
    ad = {
        "device_id": did, "label": (latest or {}).get("label") or did,
        "group": grp.get("label") or grp.get("id"), "source": B.source_of(did),
    }
    lat, lng = (latest or {}).get("lat"), (latest or {}).get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        ad["lat"], ad["lng"] = float(lat), float(lng)
    ad.update(extra or {})
    return {
        "definition_slug": definition["slug"],
        "event": context.get("event", ""), "year": context.get("year", ""),
        "title": title, "message": message,
        "timeStr": _paris_hm(at or now),
        "actionData": ad,
        "dedup_key": dedup,
        "triggeredAt": now,
        "expiresAt": now + timedelta(minutes=ALERT_TTL_MIN),
    }


def _label(latest, did, grp):
    lbl = (latest or {}).get("label") or did
    g = grp.get("label") or ""
    return "%s (%s)" % (lbl, g) if g else lbl


def _cooldown_ok(st, now, cooldown_min):
    last = _utc(st.get("last_alert_at"))
    return last is None or (now - last).total_seconds() >= cooldown_min * 60


# ---------------------------------------------------------------------------
# Zones (couche polygones Looker, comme la prise de poste operateur)
# ---------------------------------------------------------------------------

_ZONE_CACHE = {}
_ZONE_TTL_S = 300


def load_zones(db, collection, zone_ids, now):
    """[(id, shape preparee)] des zones choisies. Cache 5 min."""
    from shapely.geometry import shape
    from shapely.prepared import prep
    import operateurs as OP
    key = (collection, tuple(sorted(zone_ids or [])))
    hit = _ZONE_CACHE.get(key)
    if hit and (now - hit[0]).total_seconds() < _ZONE_TTL_S:
        return hit[1]
    wanted = set(str(z) for z in zone_ids or [])
    out = []
    for f in OP.layer_features(db, collection):
        geom = f.get("geometry") or {}
        fid = str((f.get("properties") or {}).get("_id_feature") or "")
        if not fid or (wanted and fid not in wanted):
            continue
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            out.append((fid, prep(shape(geom))))
        except Exception:
            continue
    _ZONE_CACHE[key] = (now, out)
    return out


def zones_containing(zones, lat, lng):
    from shapely.geometry import Point
    pt = Point(lng, lat)
    return {fid for fid, shp in zones if shp.contains(pt)}


# ---------------------------------------------------------------------------
# Detecteurs
# ---------------------------------------------------------------------------

def _run(definition, context, step):
    """Squelette commun : charge l'etat, appelle step(did, grp, latest,
    dev_state, now) pour chaque balise visee, sauve l'etat."""
    db = context["db"]
    now = context["now"]
    params = definition.get("params") or {}
    key = "beacon:%s" % definition["slug"]
    state_doc = db["cockpit_alert_engine_state"].find_one({"_id": key}) or {}
    states = state_doc.get("devices") or {}
    targets = target_devices(db, params)
    if not targets:
        return None
    latest = {d["_id"]: d for d in db[B.COL_LATEST].find({"_id": {"$in": list(targets)}})}
    tlt = B.devices_by_id(db)
    settings = B.get_settings(db)
    results = []
    new_states = {}
    for did, grp in targets.items():
        doc = latest.get(did)
        if not doc:
            continue
        st = dict(states.get(did) or {})
        dev_state = B.device_state(doc, now=now, device=tlt.get(did), settings=settings)
        try:
            res = step(db, did, grp, doc, dev_state, st, now, params)
        except Exception as e:
            log.warning("  %s %s : %s", definition.get("detection_type"), did, e)
            res = None
        new_states[did] = st
        for r in res or []:
            title, message, dedup, extra = r
            st["last_alert_at"] = now
            results.append(_alert(definition, context, did, grp, doc, title, message, dedup, extra))
    db["cockpit_alert_engine_state"].update_one(
        {"_id": key}, {"$set": {"devices": new_states, "at": now}}, upsert=True)
    for r in results:
        log.info("  %s: %s", definition.get("detection_type"), r["message"])
    return results or None


def _speed_step(db, did, grp, doc, ds, st, now, p):
    max_kmh = float(p.get("max_kmh") or 50)
    need = max(1, int(p.get("consecutive") or 2))
    cooldown = float(p.get("cooldown_min") if p.get("cooldown_min") is not None else 10)
    out = []
    for t, pt in new_points(db, did, st.get("last_ts"), now):
        st["last_ts"] = t
        speed = float(pt.get("speed") or 0)
        if speed > max_kmh:
            st["count"] = int(st.get("count") or 0) + 1
            st["vmax"] = max(float(st.get("vmax") or 0), speed)
        else:
            st["count"] = 0
            st["vmax"] = 0
            st["episode"] = False
        if st.get("count", 0) >= need and not st.get("episode") and _cooldown_ok(st, now, cooldown):
            st["episode"] = True
            out.append(("BALISE - EXCES DE VITESSE",
                        "%s : %d km/h (seuil %d km/h) a %s" % (_label(doc, did, grp), round(st["vmax"]),
                                                              round(max_kmh), _paris_hm(t)),
                        "beacon-speed-%s-%s" % (did, t.strftime("%Y%m%d%H%M%S")),
                        {"speed": round(st["vmax"]), "max_kmh": max_kmh}))
    return out


def _zone_step(db, did, grp, doc, ds, st, now, p):
    coll = p.get("zones_collection")
    if not coll:
        return []
    zones = load_zones(db, coll, p.get("zone_ids") or [], now)
    if not zones:
        return []
    labels = p.get("zone_labels") or {}
    event = p.get("event") or "both"
    need = max(1, int(p.get("consecutive") or 2))
    inside = st.get("inside")              # zones confirmees (liste) ; None = jamais observe
    pending = st.get("pending") or {}      # zone -> nb de points consecutifs en desaccord
    out = []
    for t, pt in new_points(db, did, st.get("last_ts"), now):
        st["last_ts"] = t
        raw = zones_containing(zones, float(pt["lat"]), float(pt["lng"]))
        if inside is None:
            # Premiere observation : on prend l'etat tel quel, sans alerte
            # (activer la regle ne doit pas alerter sur toutes les balises)
            inside = sorted(raw)
            continue
        conf = set(inside)
        for fid in raw.symmetric_difference(conf) | set(pending):
            disagree = (fid in raw) != (fid in conf)
            pending[fid] = int(pending.get(fid) or 0) + 1 if disagree else 0
            if pending[fid] >= need:
                pending[fid] = 0
                entering = fid in raw
                if entering:
                    conf.add(fid)
                else:
                    conf.discard(fid)
                if event == "both" or (event == "enter") == entering:
                    zl = labels.get(fid) or fid
                    out.append(("BALISE - %s DE ZONE" % ("ENTREE" if entering else "SORTIE"),
                                "%s %s %s a %s" % (_label(doc, did, grp),
                                                   "entre dans" if entering else "sort de", zl, _paris_hm(t)),
                                "beacon-zone-%s-%s-%s-%s" % (did, fid, "in" if entering else "out",
                                                             t.strftime("%Y%m%d%H%M%S")),
                                {"zone_id": fid, "zone": zl, "direction": "entree" if entering else "sortie"}))
        pending = {k: v for k, v in pending.items() if v}
        inside = sorted(conf)
    st["inside"] = inside
    st["pending"] = pending
    return out


def _stopped_step(db, did, grp, doc, ds, st, now, p):
    max_min = float(p.get("max_min") or 20)
    radius = float(p.get("radius_m") or 50)
    for t, pt in new_points(db, did, st.get("last_ts"), now):
        st["last_ts"] = t
        lat, lng = float(pt["lat"]), float(pt["lng"])
        a = st.get("anchor")
        if not a or haversine(a["lat"], a["lng"], lat, lng) > radius:
            st["anchor"] = {"lat": lat, "lng": lng, "since": t}
            st["alerted"] = False
    a = st.get("anchor")
    if not a or st.get("alerted") or not ds["online"]:
        return []
    since = _utc(a["since"])
    minutes = (now - since).total_seconds() / 60.0
    if minutes < max_min:
        return []
    zl = None
    if p.get("zones_collection"):
        zones = load_zones(db, p["zones_collection"], p.get("zone_ids") or [], now)
        hit = zones_containing(zones, a["lat"], a["lng"])
        if not hit:
            return []
        zl = ", ".join((p.get("zone_labels") or {}).get(z) or z for z in sorted(hit))
    st["alerted"] = True
    msg = "%s immobile depuis %d min%s (depuis %s)" % (
        _label(doc, did, grp), round(minutes), (" dans " + zl) if zl else "", _paris_hm(since))
    return [("BALISE - ARRET PROLONGE", msg,
             "beacon-stop-%s-%s" % (did, since.strftime("%Y%m%d%H%M%S")),
             {"stopped_min": round(minutes), "zone": zl})]


def _silent_step(db, did, grp, doc, ds, st, now, p):
    after = p.get("after_min")
    if after:
        last = ds["last_at"]
        online = last is not None and (now - last).total_seconds() < float(after) * 60
    else:
        online = ds["online"]
    was = st.get("online")
    st["online"] = online
    if was is True and not online:
        last = ds["last_at"]
        since = ("depuis %s" % _paris_hm(last)) if last else ""
        return [("BALISE MUETTE", "%s ne repond plus %s" % (_label(doc, did, grp), since),
                 "beacon-silent-%s-%s" % (did, (last or now).strftime("%Y%m%d%H%M%S")),
                 {"last_at": last.isoformat() if last else None})]
    return []


def _battery_step(db, did, grp, doc, ds, st, now, p):
    thr = float(p.get("threshold_pct") or 20)
    pct = doc.get("battery_pct")
    if pct is None:
        return []
    pct = float(pct)
    if doc.get("charging") or pct > thr + 5:
        st["low"] = False
        return []
    if pct <= thr and not st.get("low"):
        st["low"] = True
        return [("BALISE - BATTERIE FAIBLE", "%s : batterie %d %% (seuil %d %%)" % (
                    _label(doc, did, grp), round(pct), round(thr)),
                 "beacon-battery-%s-%d-%s" % (did, round(thr), now.strftime("%Y%m%d%H")),
                 {"battery_pct": round(pct)})]
    return []


def _movement_step(db, did, grp, doc, ds, st, now, p):
    cooldown = float(p.get("cooldown_min") if p.get("cooldown_min") is not None else 10)
    min_speed = float(p.get("min_speed_kmh") or B.MOVING_SPEED_KMH)
    if not ds["online"]:
        return []
    if B.is_teltonika_id(did):
        moving = bool(doc.get("movement"))
    else:
        moving = doc.get("status") == "running" or float(doc.get("speed") or 0) >= min_speed
    was = st.get("moving")
    st["moving"] = moving
    if was is False and moving and _cooldown_ok(st, now, cooldown):
        return [("BALISE - MISE EN MOUVEMENT", "%s s'est mise en mouvement a %s" % (
                    _label(doc, did, grp), _paris_hm(now)),
                 "beacon-move-%s-%s" % (did, now.strftime("%Y%m%d%H%M")), {})]
    return []


_STEPS = {
    "beacon_speed": _speed_step,
    "beacon_zone": _zone_step,
    "beacon_stopped": _stopped_step,
    "beacon_silent": _silent_step,
    "beacon_battery": _battery_step,
    "beacon_movement": _movement_step,
}


def make_handler(detection_type, get_db):
    step = _STEPS[detection_type]

    def handler(definition, context):
        ctx = dict(context)
        ctx["db"] = get_db()
        return _run(definition, ctx, step)
    handler.__name__ = "detect_" + detection_type
    return handler
