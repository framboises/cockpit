"""camera_alerts.py - Alerte "camera hors ligne" de la centrale d'alerte.

Detecteur appele par alert_engine.py (tache planifiee, 30 s). Il ne sonde
RIEN : il lit cameras_health, ecrit par la releve planifiee cameras_health.py
(toutes les 2 min, TCP seul, cameras de piste exclues). Regles :
docs/claude/cameras.md, section "Etat des cameras".

  camera_offline   appareil hors ligne (etat confirme par la releve) depuis
                   plus de after_min minutes ; facultatif : retour en ligne
                   (notify_back), LAPI sans lecture de plaque depuis
                   lapi_silence_min minutes alors que la camera repond.

Parametres : after_min (10), sites ([] = tous), camera_ids ([] = toutes),
include_cellular (true), notify_back (false), lapi_silence_min (0 = jamais),
stale_min (10 : releve plus vieille = tache arretee, on n'alerte pas sur un
etat perime).
Etat par definition : cockpit_alert_engine_state._id = "camera:<slug>"
(rafraichi a chaque cycle, donc jamais purge par le menage des 2 jours).
UNE alerte par episode : une coupure ne re-sonne pas tant que la camera n'est
pas revenue en ligne.
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("alert_engine")
PARIS = ZoneInfo("Europe/Paris")
ALERT_TTL_MIN = 30

DETECTION_TYPES = ("camera_offline",)
DEFAULTS = {"after_min": 10, "sites": [], "camera_ids": [], "include_cellular": True,
            "notify_back": False, "lapi_silence_min": 0, "stale_min": 10}


def _utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hm(dt):
    return _utc(dt).astimezone(PARIS).strftime("%H:%M")


def _duration(minutes):
    m = int(round(minutes))
    if m < 60:
        return "%d min" % m
    h, m = divmod(m, 60)
    return "%d h %02d" % (h, m) if h < 24 else "%d j %d h" % (h // 24, h % 24)


def _positions(db):
    """{ip: (lat, lng)} depuis la couche GPS titan.cameras (un point par canal,
    une IP = un lieu)."""
    layer = db["cameras"].find_one({"type": "FeatureCollection"}, {"features.geometry": 1, "features.properties.IP": 1}) or {}
    out = {}
    for f in layer.get("features") or []:
        ip = str((f.get("properties") or {}).get("IP") or "").strip()
        c = (f.get("geometry") or {}).get("coordinates") or []
        if ip and len(c) >= 2:
            out.setdefault(ip, (float(c[1]), float(c[0])))
    return out


def _alert(definition, context, h, title, message, dedup, pos, extra=None):
    now = context["now"]
    ad = {"camera_id": str(h["_id"]), "label": h.get("name", ""), "ip": h.get("ip", ""), "site": h.get("site", ""),
          "cellular": bool(h.get("cellular"))}
    if pos:
        ad["lat"], ad["lng"] = pos
    ad.update(extra or {})
    return {
        "definition_slug": definition["slug"],
        "event": context.get("event", ""), "year": context.get("year", ""),
        "title": title, "message": message,
        "timeStr": _hm(now),
        "actionData": ad,
        "dedup_key": dedup,
        "triggeredAt": now,
        "expiresAt": now + timedelta(minutes=ALERT_TTL_MIN),
    }


def evaluate(definition, context, db):
    """-> liste d'alertes ou None. Etat lu / ecrit dans cockpit_alert_engine_state."""
    now = context["now"]
    p = dict(DEFAULTS, **(definition.get("params") or {}))
    after = float(p["after_min"] or 0)
    lapi_silence = float(p["lapi_silence_min"] or 0)
    stale = float(p["stale_min"] or 10)
    sites = set(p["sites"] or [])
    ids = set(str(i) for i in p["camera_ids"] or [])
    key = "camera:%s" % definition["slug"]
    states = (db["cockpit_alert_engine_state"].find_one({"_id": key}) or {}).get("devices") or {}
    pos = None
    results, new_states = [], {}
    for h in db["cameras_health"].find():
        cid = str(h["_id"])
        if (sites and h.get("site") not in sites) or (ids and cid not in ids):
            continue
        if h.get("cellular") and not p["include_cellular"]:
            continue
        st = dict(states.get(cid) or {})
        new_states[cid] = st
        checked = _utc(h.get("last_check_at"))
        if checked is None or (now - checked).total_seconds() > stale * 60 or "ok" not in h:
            continue  # releve arretee ou jamais faite : etat perime, on ne decide rien
        since = _utc(h.get("since")) or now
        if not h["ok"]:
            down_min = (now - since).total_seconds() / 60.0
            if down_min >= after and st.get("down") != since.isoformat():
                st["down"] = since.isoformat()
                pos = _positions(db) if pos is None else pos
                results.append(_alert(definition, context, h, "CAMERA HORS LIGNE",
                                      "%s (%s) ne repond plus depuis %s (%s)%s" % (
                                          h.get("name", ""), h.get("ip", ""), _hm(since), _duration(down_min),
                                          " - camera 4G" if h.get("cellular") else ""),
                                      "camera-offline-%s-%s" % (cid, since.strftime("%Y%m%d%H%M%S")),
                                      pos.get(h.get("ip")), {"down_since": since.isoformat()}))
            continue
        # En ligne
        if st.get("down"):
            was = st.pop("down")
            if p["notify_back"]:
                pos = _positions(db) if pos is None else pos
                results.append(_alert(definition, context, h, "CAMERA DE NOUVEAU EN LIGNE",
                                      "%s (%s) repond de nouveau depuis %s" % (h.get("name", ""), h.get("ip", ""), _hm(since)),
                                      "camera-back-%s-%s" % (cid, since.strftime("%Y%m%d%H%M%S")),
                                      pos.get(h.get("ip")), {"down_since": was}))
        last = _utc(h.get("lapi_last_at"))
        if lapi_silence and last is not None:
            quiet = (now - last).total_seconds() / 60.0
            if quiet < lapi_silence:
                st.pop("lapi", None)
            elif st.get("lapi") != last.isoformat():
                st["lapi"] = last.isoformat()
                pos = _positions(db) if pos is None else pos
                results.append(_alert(definition, context, h, "LAPI SANS LECTURE",
                                      "%s (%s) repond mais n'a lu aucune plaque depuis %s (%s)" % (
                                          h.get("name", ""), h.get("ip", ""), _hm(last), _duration(quiet)),
                                      "camera-lapi-%s-%s" % (cid, last.strftime("%Y%m%d%H%M%S")),
                                      pos.get(h.get("ip")), {"lapi_last_at": last.isoformat()}))
    db["cockpit_alert_engine_state"].update_one({"_id": key}, {"$set": {"devices": new_states, "at": now}}, upsert=True)
    for r in results:
        log.info("  camera_offline: %s", r["message"])
    return results or None


def make_handler(detection_type, get_db):
    def handler(definition, context):
        return evaluate(definition, context, get_db())
    handler.__name__ = "detect_" + detection_type
    return handler
