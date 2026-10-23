# detection.py -- Blueprint Detection : poste de controle des intrusions
#
# Donnees produites par le recepteur (E:\TITAN\production\PCA\SCRIPTS\
# intrusions.py, appele par ecoutehik2.py) :
#   hik_intrusions        episodes (un par passage dans le champ d'un canal) :
#                         camera_path, channel, camera_id/name, started_at,
#                         last_at, target_types, regions, targets, photos,
#                         cover, status (new / seen / closed)
#   hik_intrusion_events  detail de chaque message (TTL = duree des photos)
# Photos : hik_images/intrusions/AAAA/MM/JJ/<chemin>/ (+ _t/ vignette cible),
# conservees 1 an (purge par le recepteur).
# Propre a cette page :
#   detection_zones       zones dessinees sur la carte, liees a des canaux
#                         (et eventuellement a des zones de la camera)
#   cockpit_cameras.channels[].map   position du canal sur la carte
# Les canaux visibles ici sont ceux qui ont l'usage "fielddetection" (page Cameras).
import os
import re
from datetime import datetime, date, timezone, timedelta
from flask import Blueprint, jsonify, request, render_template, send_file, abort
from pymongo import MongoClient, DESCENDING
from bson.objectid import ObjectId

import detection_arming as arming

detection_bp = Blueprint("detection", __name__)

HIK_IMAGE_DIR = "E:/TITAN/production/hik_images"
# Un episode est "en cours" tant qu'un message est arrive il y a moins de
# EPISODE_GAP_S (meme valeur que HIK_INTRUSION_GAP_S du recepteur)
EPISODE_GAP_S = 30
STATUSES = ("new", "seen", "closed")
TARGETS = ("human", "vehicle")


@detection_bp.before_request
def _before():
    from cameras import _check_admin
    err = _check_admin()
    if err:
        return err


_db = None


def _ensure_db():
    global _db
    if _db is not None:
        return _db
    titan_env = os.getenv("TITAN_ENV", "dev").strip().lower()
    db = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))[
        "titan" if titan_env in {"prod", "production"} else "titan_dev"]
    db["detection_zones"].create_index("name")
    db["hik_intrusions"].create_index([("status", 1), ("started_at", -1)])
    _db = db
    return db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utc(dt):
    if not isinstance(dt, datetime):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso(dt):
    dt = _utc(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _oid(v):
    try:
        return ObjectId(v)
    except Exception:
        return None


def _img(rel):
    return "/api/detection/image/" + rel if rel else None


# Positions GPS : couche cartographique "cameras" de la base titan (un seul
# document FeatureCollection, un point par source video, proprietes IP et
# "SEETEC NAME" = nom du canal). Lue a chaque appel (cache court) : une
# correction faite dans la couche apparait sans rien ressaisir. Une position
# placee a la main dans la page Detection (channels[].map) reste prioritaire.
_GEO_TTL_S = 60
_geo_cache = {"at": 0.0, "by_ip": None}


def _norm_name(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _geo_by_ip(db):
    import time as _time
    now = _time.monotonic()
    if _geo_cache["by_ip"] is None or now - _geo_cache["at"] > _GEO_TTL_S:
        by_ip = {}
        for doc in db["cameras"].find({}, {"features": 1}):
            for f in doc.get("features") or []:
                props = f.get("properties") or {}
                geom = f.get("geometry") or {}
                coords = geom.get("coordinates") or []
                ip = str(props.get("IP") or "").strip()
                if geom.get("type") != "Point" or len(coords) < 2 or not ip:
                    continue
                by_ip.setdefault(ip, []).append({
                    "lat": float(coords[1]), "lng": float(coords[0]),
                    "seetec": props.get("SEETEC NAME") or "", "name": props.get("NEW NAME") or "",
                    "type": props.get("TYPE") or "",
                })
        _geo_cache.update({"at": now, "by_ip": by_ip})
    return _geo_cache["by_ip"]


def _geo_point(db, ip, channel_name):
    """Point GPS d'un canal : meme IP, puis meme nom de canal (SEETEC NAME)."""
    pts = _geo_by_ip(db).get(ip) or []
    if not pts:
        return None
    want = _norm_name(channel_name)
    for p in pts:
        if want and _norm_name(p["seetec"]) == want:
            return p
    for p in pts:  # nom de la couche sans le numero (ex. "[F] - Porte Houx")
        if want and _norm_name(p["name"]) and want.endswith(_norm_name(p["name"])):
            return p
    return pts[0] if len(pts) == 1 else None


def _device_position(db, d):
    """UNE IP = UN LIEU : tous les canaux d'un appareil (multi-capteurs) partagent
    la position. Manuelle (`cockpit_cameras.map`, ou ancienne position d'un
    canal) prioritaire, sinon couche GPS par IP puis par nom d'un des canaux."""
    manual = d.get("map") or next((c["map"] for c in d.get("channels") or [] if c.get("map")), None)
    if manual:
        return manual, "manual", None
    geo = None
    for ch in [{"name": d.get("name")}] + list(d.get("channels") or []):
        geo = _geo_point(db, d.get("ip", ""), ch.get("name"))
        if geo:
            break
    return ({"lat": geo["lat"], "lng": geo["lng"]}, "geo", geo) if geo else (None, "", None)


def _detection_channels(db):
    """Canaux surveilles par la page IA : detection d'intrusion (alertes de la
    camera) et Capture IA (photos prises par la tache planifiee). Les deux
    peuvent entrer dans un groupe (planning). modes : ["alerte", "capture"]."""
    out = []
    for d in db["cockpit_cameras"].find({"channels.roles.type": {"$in": ["fielddetection", "capture"]}},
                                        {"name": 1, "site": 1, "camera_path": 1, "channels": 1, "enabled": 1, "ip": 1, "map": 1}):
        pos, source, geo = _device_position(db, d)
        for ch in d.get("channels") or []:
            types = {r.get("type") for r in ch.get("roles") or []}
            modes = (["alerte"] if "fielddetection" in types else []) + (["capture"] if "capture" in types else [])
            if not modes:
                continue
            out.append({
                "modes": modes,
                "key": "%s:%s" % (d["_id"], ch.get("no")),
                "camera_id": str(d["_id"]), "camera_name": d.get("name", ""),
                "channel": ch.get("no"), "channel_name": ch.get("name") or "Canal %s" % ch.get("no"),
                "camera_path": d.get("camera_path", ""), "site": d.get("site", ""),
                "enabled": d.get("enabled", True) and ch.get("enabled", True),
                "map": pos, "map_source": source,
                "geo_name": geo["seetec"] if geo else "",
            })
    return out


def _zones(db):
    return list(db["detection_zones"].find().sort("name", 1))


DAY_ABBR = ["", "lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def _local_label(dt_utc, now_utc):
    """'14:30' aujourd'hui, sinon 'lun. 22:00'."""
    loc = dt_utc.astimezone(arming.PARIS)
    if loc.date() == now_utc.astimezone(arming.PARIS).date():
        return loc.strftime("%H:%M")
    return "%s %s" % (DAY_ABBR[loc.isoweekday()], loc.strftime("%H:%M"))


def _pub_group(g, now, dropped=None, db=None):
    db = db if db is not None else _ensure_db()
    on, source = arming.group_state(g, now, db)
    nxt = arming.next_change(g, now, db=db)
    a = arming.normalize(g.get("arming"))
    ov = (g.get("arming") or {}).get("override") or None
    rule = None
    if source == "override":
        label = ("Force active" if on else "Force desactive") + (" jusqu'a " + _local_label(_utc(ov["until"]), now))
    elif source == "always":
        label = "Toujours active"
    elif source == "off":
        label = "En pause"
    else:
        _grid, rule = arming.day_grid(g, now.astimezone(arming.PARIS).date(), db)
        label = ("Active jusqu'a " if on else "Inactive, reprend ") + (_local_label(nxt, now) if nxt else "--")
    return {
        "_id": str(g["_id"]), "name": g.get("name", ""), "color": g.get("color", "#2563eb"),
        "notes": g.get("notes", ""),
        "members": [{"camera_id": str(m.get("camera_id")), "channel": m.get("channel"),
                     "key": "%s:%s" % (m.get("camera_id"), m.get("channel"))} for m in g.get("members") or []],
        "arming": {
            "mode": a["mode"], "weeks": a["weeks"], "default_week": a["default_week"],
            "exceptions": a["exceptions"], "holidays": a["holidays"],
            "override": {"state": ov.get("state"), "until": _iso(ov.get("until"))} if source == "override" else None,
        },
        "armed": on, "source": source, "next_change": _iso(nxt), "state_label": label,
        "today_rule": {"source": rule["source"], "label": rule["label"]} if rule else None,
        "dropped_today": (dropped or {}).get(str(g["_id"]), 0),
    }


def _dropped_today(db, groups):
    """Messages jetes aujourd'hui (hors activation), par groupe et par canal."""
    now = datetime.now(timezone.utc)
    day = now.astimezone(arming.PARIS).strftime("%Y-%m-%d")
    by_key = {}
    for d in db["detection_dropped"].find({"day": day}):
        k = "%s:%s" % (d.get("camera_id"), d.get("channel"))
        by_key[k] = by_key.get(k, 0) + d.get("n", 0)
    by_group = {}
    for g in groups:
        by_group[str(g["_id"])] = sum(by_key.get("%s:%s" % (m.get("camera_id"), m.get("channel")), 0)
                                      for m in g.get("members") or [])
    return by_key, by_group


def _pub_zone(z):
    return {
        "_id": str(z["_id"]), "name": z.get("name", ""), "color": z.get("color", "#d97706"),
        "polygon": z.get("polygon") or [], "notes": z.get("notes", ""),
        "links": [{"camera_id": str(l.get("camera_id")), "channel": l.get("channel"),
                   "regions": l.get("regions") or []} for l in z.get("links") or []],
    }


def _zones_of(ep, zones):
    """Zones de la carte concernees par un episode."""
    out = []
    regions = set(ep.get("regions") or [])
    for z in zones:
        for l in z.get("links") or []:
            if l.get("camera_id") == ep.get("camera_id") and l.get("channel") == ep.get("channel") \
                    and (not l.get("regions") or regions & set(l["regions"])):
                out.append(z)
                break
    return out


_REAL_CATS = ("personne", "vehicule")


def _iou(a, b):
    iw = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    ih = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _match_score(b, r):
    """Le cadre b (detecteur) est-il LA cible signalee par la camera (cadre
    r) ? Score = IoU (position ET taille : un grand vehicule qui deborde sur
    un petit cadre de personne n'est pas cette cible). Candidat si IoU >=
    0.1 ou si le centre de b tombe dans r elargi de 25 % (cadres camera
    approximatifs). 0 sinon."""
    iou = _iou(b, r)
    cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    mx, my = r["w"] * 0.25, r["h"] * 0.25
    inside = r["x"] - mx <= cx <= r["x"] + r["w"] + mx and r["y"] - my <= cy <= r["y"] + r["h"] + my
    return max(iou, 0.01) if iou >= 0.1 or inside else 0.0


def _target_det(pai, rect):
    """Index du cadre du detecteur qui EST la cible de la camera, ou None.
    Un seul : le meilleur recouvrement (pas la meilleure confiance)."""
    best_i, best_s = None, 0.0
    for i, d in enumerate((pai or {}).get("detections") or []):
        if d.get("category") not in _REAL_CATS or not d.get("box"):
            continue
        s = _match_score(d["box"], rect) if rect else 0.0
        if s > best_s:
            best_i, best_s = i, s
    return best_i


def _ai_answer(pai, rect):
    """LA reponse de l'IA sur la cible signalee (cadre orange de la camera),
    une seule, toujours rattachee a ce que l'operateur voit :
    - un cadre du detecteur coincide avec le cadre orange (_target_det) :
      sa classe et sa confiance, k = son index (pastille cerclee d'orange,
      jugee par la reponse "c'est quoi ?" de la photo) ;
    - sinon le 1er de l'avis SigLIP (calcule sur le cadrage du cadre
      orange), sans k.
    Jamais de melange : la classe affichee est celle du cadre k.
    -> {label, p, src, k?} ou None."""
    if not pai:
        return None
    k = _target_det(pai, rect)
    if k is not None:
        d = pai["detections"][k]
        return {"label": d["category"], "p": round(d.get("conf") or 0, 3), "src": "detecteur", "k": k}
    guess = pai.get("guess") or []
    if guess:
        return {"label": guess[0].get("label"), "p": guess[0].get("p"), "src": "avis"}
    return None


def _pub_ai(ep):
    """Verdict de la verification IA locale (recepteur + service
    IntrusionVerifier) et avis de l'operateur (pouce). None si non verifiee."""
    ai, vote = ep.get("ai") or {}, ep.get("ai_vote") or {}
    if not ai and not vote:
        return None
    # Reponse de l'IA sur la photo qui a decide (sa cible)
    dp = next((p for p in ep.get("photos") or [] if ai.get("photo") and p.get("photo") == ai.get("photo")), None)
    answer = _ai_answer(dp.get("ai"), dp.get("rect")) if dp else None
    return {"answer": answer, "photo": _img(ai.get("photo")) if ai.get("photo") else None, "status": ai.get("status"), "label": ai.get("label"), "conf": ai.get("conf"),
            "real_conf": ai.get("real_conf"), "reason": ai.get("reason"), "guess": ai.get("guess") or [],
            "by": ai.get("by"),  # "detecteur" ou "detecteur+avis" (verificateur du 14/10/2026)
            "vote": vote.get("vote"), "truth": vote.get("truth"), "note": vote.get("note", ""),
            "vote_by": vote.get("by", ""), "vote_at": _iso(vote.get("at"))}


def _pub_label(lb):
    if not lb:
        return None
    return {"boxes": lb.get("boxes") or [], "by": lb.get("by", ""), "at": _iso(lb.get("at"))}


def _pub_episode(ep, zones, now, full=False):
    zs = _zones_of(ep, zones)
    last, start = _utc(ep.get("last_at")), _utc(ep.get("started_at"))
    cover = ep.get("cover") or {}
    item = {
        "id": str(ep["_id"]),
        "camera_id": str(ep.get("camera_id") or ""), "camera_name": ep.get("camera_name", ""),
        "channel": ep.get("channel"), "channel_name": ep.get("channel_name", ""),
        "camera_path": ep.get("camera_path", ""),
        "started_at": _iso(start), "last_at": _iso(last),
        "duration_s": int((last - start).total_seconds()) if last and start else 0,
        "active": bool(last and (now - last).total_seconds() < EPISODE_GAP_S),
        "types": ep.get("target_types") or [],
        "regions": [r for r in ep.get("regions") or [] if r is not None],
        "targets": len(ep.get("targets") or []),
        "events": ep.get("events", 0),
        "photos_n": len(ep.get("photos") or []),
        "thumb": _img(cover.get("thumb")), "photo": _img(cover.get("photo")), "rect": cover.get("rect"),
        "zones": [{"id": str(z["_id"]), "name": z.get("name", ""), "color": z.get("color", "#d97706")} for z in zs],
        "status": ep.get("status", "new"),
        "status_by": ep.get("status_by", ""), "status_at": _iso(ep.get("status_at")),
        "note": ep.get("note", ""),
        "ai": _pub_ai(ep),
        # Hors planning : gardee sans alerte (champ absent = dans le planning)
        "armed": ep.get("armed") is not False,
    }
    if full:
        item["photos"] = [{"photo": _img(p.get("photo")), "thumb": _img(p.get("thumb")), "at": _iso(p.get("at")),
                           "type": p.get("type"), "rect": p.get("rect"), "target_id": p.get("target_id"),
                           "ai": p.get("ai"), "label": _pub_label(p.get("label")),
                           "truth": _pub_truth(p.get("truth")),
                           "answer": _ai_answer(p.get("ai"), p.get("rect")),
                           # Retours sur les cadres du detecteur {index: {verdict, by, at}}
                           "det_fb": {str(kk): _pub_det_fb(v) for kk, v in (p.get("det_fb") or {}).items()},
                           # Capture de Cockpit (camera sans photo, detection_snapshot.py)
                           "snapshot": bool(p.get("snapshot")), "snap_delay_s": p.get("snap_delay_s")}
                          for p in ep.get("photos") or []]
        item["region_polygon"] = ep.get("region_polygon") or []
        item["resolution"] = ep.get("resolution")
    return item


def _episode_query(args, zones):
    from anpr import _period
    q = {}
    code, start, end, step, bars = _period(args.get("period", "today"), args.get("date"))
    q["started_at"] = {"$gte": start, "$lt": end} if code == "day" else {"$gte": start}
    t = args.get("type", "")
    if t in TARGETS:
        q["target_types"] = t
    st = args.get("status", "")
    if st == "open":
        q["status"] = {"$ne": "closed"}
    elif st in STATUSES:
        q["status"] = st
    # Planning : "" = intrusions du planning seulement (defaut, les hors
    # planning peuvent etre nombreuses en journee), "off" = hors planning,
    # "all" = toutes
    pl = args.get("planning", "")
    if pl == "off":
        q["armed"] = False
    elif pl != "all":
        q["armed"] = {"$ne": False}
    ai = args.get("ai", "")
    if ai == "filtered":
        q["ai.status"] = "filtered"
    elif ai == "alerted":
        q["ai.status"] = {"$ne": "filtered"}
    elif ai == "voted":
        q["ai_vote.vote"] = {"$in": AI_VOTES}
    cam = args.get("camera", "")
    if cam and ":" in cam:
        cid, ch = cam.split(":", 1)
        if _oid(cid):
            q["camera_id"] = _oid(cid)
            if ch != "*":  # "<id>:*" = tous les canaux de l'appareil
                try:
                    q["channel"] = int(ch)
                except ValueError:
                    pass
    group = _oid(args.get("group", ""))
    if group:
        g = _ensure_db()["detection_groups"].find_one({"_id": group}) or {}
        conds = [{"camera_id": m.get("camera_id"), "channel": m.get("channel")} for m in g.get("members") or []]
        q.setdefault("$and", []).append({"$or": conds or [{"_id": None}]})
    zone = _oid(args.get("zone", ""))
    if zone:
        z = next((z for z in zones if z["_id"] == zone), None)
        ors = []
        for l in (z or {}).get("links") or []:
            cond = {"camera_id": l.get("camera_id"), "channel": l.get("channel")}
            if l.get("regions"):
                cond["regions"] = {"$in": l["regions"]}
            ors.append(cond)
        q["$or"] = ors or [{"_id": None}]
    return q, (code, start, end, step, bars)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
@detection_bp.route("/detection")
def detection_page():
    payload = getattr(request, "user_payload", {})
    return render_template("detection.html", user_roles=payload.get("roles", []),
                           user_firstname=payload.get("firstname", ""),
                           user_lastname=payload.get("lastname", ""),
                           user_email=payload.get("email", ""))


# ---------------------------------------------------------------------------
# Configuration : canaux places sur la carte, zones
# ---------------------------------------------------------------------------
@detection_bp.route("/api/detection/config")
def detection_config():
    db = _ensure_db()
    now = datetime.now(timezone.utc)
    groups = list(db["detection_groups"].find().sort("name", 1))
    drop_by_key, drop_by_group = _dropped_today(db, groups)
    channels = _detection_channels(db)
    for ch in channels:
        gs = arming.channel_groups(groups, ch["camera_id"], ch["channel"])
        ch["groups"] = [str(g["_id"]) for g in gs]
        ch["armed"] = (not gs) or any(arming.group_state(g, now, db)[0] for g in gs)
        ch["dropped_today"] = drop_by_key.get(ch["key"], 0)
    return jsonify({"channels": channels, "zones": [_pub_zone(z) for z in _zones(db)],
                    "groups": [_pub_group(g, now, drop_by_group, db) for g in groups],
                    "events": sorted({str(w.get("event")) for w in arming._event_windows(db)}),
                    "gap_s": EPISODE_GAP_S})


# ---------------------------------------------------------------------------
# Groupes de cameras et activation (programme horaire, forcage)
# ---------------------------------------------------------------------------
HM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _group_fields(data):
    name = (data.get("name") or "").strip()[:60]
    if not name:
        return None, "Nom du groupe requis"
    color = data.get("color") or "#2563eb"
    if not re.match(r"^#[0-9a-fA-F]{6}$", color):
        color = "#2563eb"
    members = []
    seen = set()
    for m in data.get("members") or []:
        cid = _oid(m.get("camera_id"))
        try:
            ch = int(m.get("channel"))
        except (TypeError, ValueError):
            continue
        if cid and (cid, ch) not in seen:
            seen.add((cid, ch))
            members.append({"camera_id": cid, "channel": ch})
    a = data.get("arming") or {}
    mode = a.get("mode") if a.get("mode") in arming.MODES else "always"
    weeks = []
    for i, w in enumerate((a.get("weeks") or [])[:10]):
        wid = re.sub(r"[^a-z0-9]", "", str(w.get("id") or "")) or "w%d" % (i + 1)
        grid = w.get("grid") or []
        if len(grid) != 7 or any(not re.match(r"^[01]{96}$", str(r)) for r in grid):
            return None, "Semaine type invalide (7 jours x 96 quarts d'heure)"
        weeks.append({"id": wid, "name": (str(w.get("name") or "") or "Semaine %d" % (i + 1))[:40], "grid": [str(r) for r in grid]})
    if not weeks and a.get("slots"):  # ancien format (creneaux) : conversion
        weeks = [{"id": "w1", "name": "Semaine normale", "grid": arming.slots_to_grid(a.get("slots"))}]
    if not weeks:
        weeks = [{"id": "w1", "name": "Semaine normale", "grid": [arming.EMPTY_DAY] * 7}]
    if len({w["id"] for w in weeks}) != len(weeks):
        return None, "Deux semaines types ont le meme identifiant"
    ids = [w["id"] for w in weeks]
    default = a.get("default_week") if a.get("default_week") in ids else ids[0]
    if mode == "schedule" and not any("1" in r for r in next(w for w in weeks if w["id"] == default)["grid"]):
        return None, "La semaine type par defaut n'a aucune plage active : peignez au moins une plage"
    exceptions = []
    for i, e in enumerate((a.get("exceptions") or [])[:60]):
        kind = "event" if e.get("kind") == "event" else "dates"
        action = e.get("action") if e.get("action") in ("week", "on", "off") else "on"
        x = {"id": re.sub(r"[^a-z0-9]", "", str(e.get("id") or "")) or "x%d" % (i + 1),
             "label": (str(e.get("label") or "") or "Exception")[:60], "kind": kind, "action": action}
        if action == "week":
            if e.get("week_id") not in ids:
                return None, "Exception %s : semaine type inconnue" % x["label"]
            x["week_id"] = e["week_id"]
        if kind == "event":
            if not e.get("event"):
                return None, "Exception %s : choisissez l'epreuve" % x["label"]
            x["event"] = str(e["event"])[:60]
            x["span"] = "public" if e.get("span") == "public" else "window"
        else:
            try:
                start = date.fromisoformat(str(e.get("start")))
                end = date.fromisoformat(str(e.get("end") or e.get("start")))
            except ValueError:
                return None, "Exception %s : dates invalides" % x["label"]
            if end < start and not e.get("yearly"):
                return None, "Exception %s : la fin precede le debut" % x["label"]
            x.update({"start": start.isoformat(), "end": end.isoformat(), "yearly": bool(e.get("yearly"))})
        exceptions.append(x)
    hol = a.get("holidays") if a.get("holidays") in arming.HOLIDAY_RULES else "normal"
    return {"name": name, "color": color, "members": members,
            "arming.mode": mode, "arming.weeks": weeks, "arming.default_week": default,
            "arming.exceptions": exceptions, "arming.holidays": hol,
            "notes": (data.get("notes") or "").strip()[:300]}, None


@detection_bp.route("/api/detection/groups", methods=["POST"])
def create_group():
    db = _ensure_db()
    f, err = _group_fields(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    now = datetime.now(timezone.utc)
    doc = {"name": f["name"], "color": f["color"], "members": f["members"], "notes": f["notes"],
           "arming": {"mode": f["arming.mode"], "weeks": f["arming.weeks"], "default_week": f["arming.default_week"],
                      "exceptions": f["arming.exceptions"], "holidays": f["arming.holidays"], "override": None},
           "createdAt": now, "updatedAt": now}
    gid = db["detection_groups"].insert_one(doc).inserted_id
    arming.invalidate()
    return jsonify(_pub_group(db["detection_groups"].find_one({"_id": gid}), now)), 201


@detection_bp.route("/api/detection/groups/<gid>", methods=["PUT"])
def update_group(gid):
    db = _ensure_db()
    f, err = _group_fields(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    f["updatedAt"] = datetime.now(timezone.utc)
    g = db["detection_groups"].find_one_and_update({"_id": _oid(gid)}, {"$set": f, "$unset": {"arming.slots": ""}},
                                                   return_document=True)
    if not g:
        return jsonify({"error": "Groupe introuvable"}), 404
    arming.invalidate()
    return jsonify(_pub_group(g, datetime.now(timezone.utc)))


@detection_bp.route("/api/detection/groups/<gid>/calendar")
def group_calendar(gid):
    """Calendrier du mois (?month=AAAA-MM) : regle et plages actives par jour,
    plus les prochains changements. Accepte aussi un brouillon non enregistre
    (POST du planning en cours d'edition) pour previsualiser."""
    db = _ensure_db()
    g = db["detection_groups"].find_one({"_id": _oid(gid)}) if gid != "draft" else {"_id": "draft", "arming": {}}
    if not g:
        return jsonify({"error": "Groupe introuvable"}), 404
    return _calendar_response(db, g)


@detection_bp.route("/api/detection/groups/preview", methods=["POST"])
def group_preview():
    """Previsualisation du calendrier d'un planning en cours d'edition."""
    db = _ensure_db()
    data = request.get_json(silent=True) or {}
    f, err = _group_fields(dict(data, name=data.get("name") or "apercu"))
    if err:
        return jsonify({"error": err}), 400
    g = {"_id": "draft", "arming": {"mode": f["arming.mode"], "weeks": f["arming.weeks"],
                                    "default_week": f["arming.default_week"], "exceptions": f["arming.exceptions"],
                                    "holidays": f["arming.holidays"]}}
    return _calendar_response(db, g)


def _calendar_response(db, g):
    try:
        y, m = [int(x) for x in (request.args.get("month") or "").split("-")]
        date(y, m, 1)
    except (ValueError, TypeError):
        today = datetime.now(arming.PARIS).date()
        y, m = today.year, today.month
    now = datetime.now(timezone.utc)
    return jsonify({
        "month": "%04d-%02d" % (y, m),
        "days": arming.month_view(g, y, m, db),
        "changes": [{"at": _iso(t), "active": a} for t, a in arming.changes(g, now, days=14, db=db)],
    })


@detection_bp.route("/api/detection/groups/<gid>", methods=["DELETE"])
def delete_group(gid):
    db = _ensure_db()
    db["detection_groups"].delete_one({"_id": _oid(gid)})
    arming.invalidate()
    return jsonify({"ok": True})


@detection_bp.route("/api/detection/groups/<gid>/override", methods=["POST"])
def override_group(gid):
    """Forcage temporaire : {state: "on" | "off", hours} ; {state: null} annule."""
    db = _ensure_db()
    data = request.get_json(silent=True) or {}
    state = data.get("state")
    if state in ("on", "off"):
        try:
            hours = max(0.25, min(72.0, float(data.get("hours", 1))))
        except (TypeError, ValueError):
            return jsonify({"error": "Duree invalide"}), 400
        ov = {"state": state, "until": datetime.now(timezone.utc) + timedelta(hours=hours),
              "by": _who(), "at": datetime.now(timezone.utc)}
    elif state is None:
        ov = None
    else:
        return jsonify({"error": "Etat invalide"}), 400
    g = db["detection_groups"].find_one_and_update({"_id": _oid(gid)}, {"$set": {"arming.override": ov}},
                                                   return_document=True)
    if not g:
        return jsonify({"error": "Groupe introuvable"}), 404
    arming.invalidate()
    return jsonify(_pub_group(g, datetime.now(timezone.utc)))


@detection_bp.route("/api/detection/cameras/<camera_id>/position", methods=["PUT"])
@detection_bp.route("/api/detection/channels/<camera_id>/<int:channel>/position", methods=["PUT"])
def set_position(camera_id, channel=None):
    """Position de l'APPAREIL (une IP = un lieu), partagee par tous ses canaux.
    L'ancienne route par canal reste acceptee (le canal est ignore)."""
    db = _ensure_db()
    oid = _oid(camera_id)
    data = request.get_json(silent=True) or {}
    if data.get("lat") is None:
        pos = None
    else:
        try:
            pos = {"lat": float(data["lat"]), "lng": float(data["lng"])}
            if data.get("heading") is not None:
                pos["heading"] = float(data["heading"]) % 360
        except (TypeError, ValueError, KeyError):
            return jsonify({"error": "Position invalide"}), 400
    doc = db["cockpit_cameras"].find_one({"_id": oid}) if oid else None
    if not doc:
        return jsonify({"error": "Camera introuvable"}), 404
    if pos:
        # Meme regle que la carte de la page Cameras : ecrit dans la couche GPS
        # titan.cameras (points crees si l'appareil n'y figure pas)
        import cameras
        r = cameras.place_device(doc, pos["lat"], pos["lng"])
        return jsonify({"ok": True, "map": {"lat": r["lat"], "lng": r["lng"]}, "map_source": r["map_source"]})
    unset = {"map": ""}
    if isinstance(doc.get("channels"), list) and doc["channels"]:
        unset["channels.$[].map"] = ""
    db["cockpit_cameras"].update_one({"_id": oid}, {"$unset": unset})
    return jsonify({"ok": True, "map": None})


def _zone_fields(data):
    name = (data.get("name") or "").strip()[:60]
    if not name:
        return None, "Nom de zone requis"
    color = data.get("color") or "#d97706"
    if not re.match(r"^#[0-9a-fA-F]{6}$", color):
        color = "#d97706"
    poly = []
    for p in data.get("polygon") or []:
        try:
            poly.append([round(float(p[0]), 7), round(float(p[1]), 7)])
        except (TypeError, ValueError, IndexError):
            return None, "Contour invalide"
    if len(poly) < 3:
        return None, "Une zone a au moins 3 points"
    links = []
    for l in data.get("links") or []:
        cid = _oid(l.get("camera_id"))
        try:
            ch = int(l.get("channel"))
        except (TypeError, ValueError):
            continue
        if cid:
            regs = [int(r) for r in l.get("regions") or [] if str(r).isdigit()]
            links.append({"camera_id": cid, "channel": ch, "regions": regs})
    return {"name": name, "color": color, "polygon": poly, "links": links,
            "notes": (data.get("notes") or "").strip()[:300]}, None


@detection_bp.route("/api/detection/zones", methods=["POST"])
def create_zone():
    db = _ensure_db()
    f, err = _zone_fields(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    now = datetime.now(timezone.utc)
    f.update({"createdAt": now, "updatedAt": now})
    zid = db["detection_zones"].insert_one(f).inserted_id
    return jsonify(_pub_zone(db["detection_zones"].find_one({"_id": zid}))), 201


@detection_bp.route("/api/detection/zones/<zid>", methods=["PUT"])
def update_zone(zid):
    db = _ensure_db()
    f, err = _zone_fields(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    f["updatedAt"] = datetime.now(timezone.utc)
    z = db["detection_zones"].find_one_and_update({"_id": _oid(zid)}, {"$set": f}, return_document=True)
    if not z:
        return jsonify({"error": "Zone introuvable"}), 404
    return jsonify(_pub_zone(z))


@detection_bp.route("/api/detection/zones/<zid>", methods=["DELETE"])
def delete_zone(zid):
    db = _ensure_db()
    db["detection_zones"].delete_one({"_id": _oid(zid)})
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Vue d'ensemble et intrusions
# ---------------------------------------------------------------------------
@detection_bp.route("/api/detection/overview")
def overview():
    db = _ensure_db()
    zones = _zones(db)
    q, (code, start, end, step, bars) = _episode_query(request.args, zones)
    now = datetime.now(timezone.utc)
    eps = list(db["hik_intrusions"].find(q, {"photos": 0, "targets": 0}).sort("started_at", DESCENDING).limit(5000))
    totals = {"episodes": len(eps), "human": 0, "vehicle": 0, "other": 0, "active": 0, "new": 0, "seen": 0, "closed": 0}
    flow = {"human": [0] * bars, "vehicle": [0] * bars, "other": [0] * bars}
    by_zone, by_channel = {}, {}
    for ep in eps:
        types = ep.get("target_types") or ["other"]
        kind = "human" if "human" in types else "vehicle" if "vehicle" in types else "other"
        totals[kind] += 1
        # Hors planning : ce n'est pas une intrusion au sens propre (personne
        # n'est cense etre absent), jamais "en cours" ni "a traiter" dans les
        # compteurs ; l'episode reste listable pour l'entrainement
        armed = ep.get("armed") is not False
        status = ep.get("status", "new")
        if armed or status != "new":
            totals[status] = totals.get(status, 0) + 1
        last = _utc(ep.get("last_at"))
        active = armed and bool(last and (now - last).total_seconds() < EPISODE_GAP_S)
        totals["active"] += active
        i = min(bars - 1, max(0, int((_utc(ep["started_at"]) - start) // step)))
        flow[kind][i] += 1
        key = "%s:%s" % (ep.get("camera_id"), ep.get("channel"))
        c = by_channel.setdefault(key, {"n": 0, "active": False, "new": 0, "last": None})
        c["n"] += 1
        c["active"] = c["active"] or active
        c["new"] += armed and status == "new"
        c["last"] = max(filter(None, [c["last"], _iso(last)]), default=None)
        for z in _zones_of(ep, zones):
            zz = by_zone.setdefault(str(z["_id"]), {"n": 0, "active": False, "new": 0, "human": 0, "vehicle": 0})
            zz["n"] += 1
            zz["active"] = zz["active"] or active
            zz["new"] += armed and status == "new"
            if kind in ("human", "vehicle"):
                zz[kind] += 1
    return jsonify({
        "now": _iso(now),
        "period": {"code": code, "from": _iso(start), "to": _iso(end), "step_min": int(step.total_seconds() // 60), "bars": bars},
        "totals": totals, "flow": flow, "by_zone": by_zone, "by_channel": by_channel,
    })


@detection_bp.route("/api/detection/episodes")
def episodes():
    db = _ensure_db()
    zones = _zones(db)
    q, _p = _episode_query(request.args, zones)
    before = request.args.get("before", "")
    if before:
        b_dt, _, b_id = before.partition("|")
        try:
            b_dt = datetime.fromisoformat(b_dt.replace("Z", "+00:00"))
            q = {"$and": [q, {"$or": [{"started_at": {"$lt": b_dt}},
                                      {"started_at": b_dt, "_id": {"$lt": ObjectId(b_id)}}]}]}
        except Exception:
            pass
    try:
        limit = max(1, min(200, int(request.args.get("limit", 60))))
    except ValueError:
        limit = 60
    docs = list(db["hik_intrusions"].find(q, {"targets": 0}).sort([("started_at", DESCENDING), ("_id", DESCENDING)]).limit(limit + 1))
    more = len(docs) > limit
    docs = docs[:limit]
    now = datetime.now(timezone.utc)
    nxt = "%s|%s" % (_iso(docs[-1]["started_at"]), docs[-1]["_id"]) if more and docs else None
    return jsonify({"items": [_pub_episode(d, zones, now) for d in docs], "next": nxt})


@detection_bp.route("/api/detection/episodes/<eid>")
def episode_detail(eid):
    db = _ensure_db()
    ep = db["hik_intrusions"].find_one({"_id": _oid(eid)})
    if not ep:
        return jsonify({"error": "Intrusion introuvable"}), 404
    item = _pub_episode(ep, _zones(db), datetime.now(timezone.utc), full=True)
    item["messages"] = [{"at": _iso(e.get("event_dt")), "type": e.get("event_type"),
                         "target": (e.get("regions") or [{}])[0].get("target"),
                         "region": (e.get("regions") or [{}])[0].get("region_id"),
                         "target_id": e.get("target_id")}
                        for e in db["hik_intrusion_events"].find({"episode_id": ep["_id"]}).sort("event_dt", 1).limit(200)]
    return jsonify(item)


def _who():
    p = getattr(request, "user_payload", {}) or {}
    return ("%s %s" % (p.get("firstname", ""), p.get("lastname", ""))).strip() or p.get("email", "")


@detection_bp.route("/api/detection/episodes/status", methods=["POST"])
def set_status():
    """{ids: [...], status: new|seen|closed, note?}"""
    db = _ensure_db()
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in STATUSES:
        return jsonify({"error": "Statut invalide"}), 400
    ids = [o for o in (_oid(i) for i in data.get("ids") or []) if o]
    if not ids:
        return jsonify({"error": "Aucune intrusion"}), 400
    patch = {"status": status, "status_by": _who(), "status_at": datetime.now(timezone.utc)}
    if "note" in data:
        patch["note"] = (data.get("note") or "").strip()[:500]
    res = db["hik_intrusions"].update_many({"_id": {"$in": ids}}, {"$set": patch})
    return jsonify({"ok": True, "modified": res.modified_count})


AI_VOTES = ["up", "down"]
# Ce qu'est vraiment la cible signalee par la camera (pilules de l'IA
# cliquables) ; les trois premieres sont de vraies intrusions
AI_TRUTHS = ["personne", "vehicule", "velo", "animal", "oiseau", "panneau", "branche", "reflet", "sol",
             "grillage", "rien"]
AI_TRUTH_REAL = {"personne", "vehicule", "velo"}


@detection_bp.route("/api/detection/episodes/<eid>/ai-vote", methods=["POST"])
def set_ai_vote(eid):
    """Avis de l'operateur sur la cible signalee par la camera.
    {truth: personne|vehicule|...|rien, note?} : ce que c'est vraiment
    (pilule cliquee) ; le pouce `vote` en est deduit (up = l'IA a eu raison,
    filtrer ou alerter). {vote: up|down} reste accepte (anciens avis),
    {vote: null} ou {truth: null} retire l'avis. Sert a mesurer le filtre et
    a constituer le jeu d'entrainement (photo + cadre + verite)."""
    db = _ensure_db()
    data = request.get_json(silent=True) or {}
    oid = _oid(eid)
    ep = db["hik_intrusions"].find_one({"_id": oid}, {"ai.status": 1, "ai.photo": 1}) if oid else None
    if not ep:
        return jsonify({"error": "Intrusion introuvable"}), 404
    truth, vote = data.get("truth"), data.get("vote")
    if truth in AI_TRUTHS and (ep.get("ai") or {}).get("photo"):
        # La photo qui a decide du verdict recoit la meme reponse (si elle n'en a pas)
        db["hik_intrusions"].update_one(
            {"_id": oid, "photos": {"$elemMatch": {"photo": ep["ai"]["photo"], "truth": {"$exists": False}}}},
            {"$set": {"photos.$.truth": {"truth": truth, "by": _who(), "at": datetime.now(timezone.utc), "auto": "episode"}}})
    if "truth" in data:
        if truth is not None and truth not in AI_TRUTHS:
            return jsonify({"error": "Avis invalide"}), 400
        if truth:
            status = (ep.get("ai") or {}).get("status")
            alerted = status != "filtered"
            vote = "up" if (truth in AI_TRUTH_REAL) == alerted else "down"
    elif vote is not None and vote not in AI_VOTES:
        return jsonify({"error": "Avis invalide"}), 400
    if vote:
        doc = {"vote": vote, "by": _who(), "at": datetime.now(timezone.utc)}
        if truth:
            doc["truth"] = truth
            note = (data.get("note") or "").strip()[:300]
            if note:
                doc["note"] = note
        upd = {"$set": {"ai_vote": doc}}
    else:
        upd = {"$unset": {"ai_vote": ""}}
    db["hik_intrusions"].update_one({"_id": oid}, upd)
    ep = db["hik_intrusions"].find_one({"_id": oid}, {"ai": 1, "ai_vote": 1})
    return jsonify({"ok": True, "ai": _pub_ai(ep)})


def _photo_at(ep, idx, url):
    """Photo idx de l'episode, controlee par son url (la liste peut avoir
    grandi depuis l'ouverture de la fiche). None si elle ne correspond pas."""
    photos = ep.get("photos") or []
    if idx < 0 or idx >= len(photos) or _img(photos[idx].get("photo")) != url:
        return None
    return photos[idx]


@detection_bp.route("/api/detection/episodes/<eid>/photos/<int:idx>/truth", methods=["POST"])
def set_photo_truth(eid, idx):
    """Ce qu'est vraiment la cible signalee sur UNE photo (une photo = une
    cible de la camera). {photo: <url, controle>, truth: personne|...|rien|null,
    note?}. Le pouce de la photo est deduit de la decision IA de la photo
    (s'il y en a une). Si c'est la photo qui a decide du verdict de
    l'episode et que l'episode n'a pas d'avis, l'avis d'episode suit."""
    db = _ensure_db()
    oid = _oid(eid)
    ep = db["hik_intrusions"].find_one({"_id": oid}, {"photos": 1, "ai": 1, "ai_vote": 1}) if oid else None
    if not ep:
        return jsonify({"error": "Intrusion introuvable"}), 404
    data = request.get_json(silent=True) or {}
    p = _photo_at(ep, idx, data.get("photo"))
    if not p:
        return jsonify({"error": "Photo introuvable (la liste a change, rouvrez la fiche)"}), 409
    truth = data.get("truth")
    if truth is not None and truth not in AI_TRUTHS:
        return jsonify({"error": "Avis invalide"}), 400
    now = datetime.now(timezone.utc)
    upd, doc = {}, None
    if truth:
        doc = {"truth": truth, "by": _who(), "at": now}
        note = (data.get("note") or "").strip()[:300]
        if note:
            doc["note"] = note
        dec = (p.get("ai") or {}).get("decision")
        if dec in ("confirmed", "filtered"):
            doc["vote"] = "up" if (truth in AI_TRUTH_REAL) == (dec == "confirmed") else "down"
        upd["$set"] = {"photos.%d.truth" % idx: doc}
    else:
        upd["$unset"] = {"photos.%d.truth" % idx: ""}
    # Le cadre du detecteur qui EST la cible de la camera recoit le retour
    # deduit de la reponse (pas de ligne a part), sauf retour donne a la main
    ans = _ai_answer(p.get("ai"), p.get("rect"))
    k = ans.get("k") if ans else None
    dets = (p.get("ai") or {}).get("detections") or []
    if k is not None and k < len(dets):
        cur = (p.get("det_fb") or {}).get(str(k))
        key = "photos.%d.det_fb.%d" % (idx, k)
        if not cur or cur.get("auto"):
            if truth:
                d = dets[k]
                cls = "personne" if truth == "velo" else truth
                verdict = "juste" if cls == d.get("category") else cls if cls in _REAL_CATS else "faux"
                upd.setdefault("$set", {})[key] = {
                    "verdict": verdict, "ai_category": d.get("category"), "ai_class": d.get("class"),
                    "conf": d.get("conf"), "box": d.get("box"), "by": _who(), "at": now, "auto": "photo"}
            elif cur:
                upd.setdefault("$unset", {})[key] = ""
    ai, ev = ep.get("ai") or {}, ep.get("ai_vote") or {}
    if ai.get("photo") == p.get("photo") and ai.get("status") in ("confirmed", "filtered") \
            and (not ev or ev.get("auto") == "photo"):
        if truth:
            alerted = ai.get("status") != "filtered"
            upd.setdefault("$set", {})["ai_vote"] = {
                "vote": "up" if (truth in AI_TRUTH_REAL) == alerted else "down", "truth": truth,
                "by": _who(), "at": now, "auto": "photo"}
        else:
            upd.setdefault("$unset", {})["ai_vote"] = ""
    db["hik_intrusions"].update_one({"_id": oid}, upd)
    ep = db["hik_intrusions"].find_one({"_id": oid}, {"ai": 1, "ai_vote": 1})
    return jsonify({"ok": True, "truth": _pub_truth(doc), "ai": _pub_ai(ep)})


def _pub_truth(t):
    if not t:
        return None
    return {"truth": t.get("truth"), "note": t.get("note", ""), "vote": t.get("vote"),
            "by": t.get("by", ""), "at": _iso(t.get("at"))}


DET_VERDICTS = ("juste", "faux", "personne", "vehicule")


@detection_bp.route("/api/detection/episodes/<eid>/photos/<int:idx>/detections/<int:k>", methods=["POST"])
def set_detection_feedback(eid, idx, k):
    """Retour de l'operateur sur UN cadre du detecteur (pointilles) d'une
    photo : {photo, verdict: juste|faux|personne|vehicule|null}. Stocke dans
    photos.N.det_fb.<k> avec une copie du cadre et de la classe de l'IA
    (exemples pour reentrainer le detecteur, niveau 3). Distinct de `label`
    (cadres dessines = verite COMPLETE de la photo) : un retour par cadre ne
    dit rien des cibles que l'IA a ratees."""
    db = _ensure_db()
    oid = _oid(eid)
    ep = db["hik_intrusions"].find_one({"_id": oid}, {"photos": 1}) if oid else None
    if not ep:
        return jsonify({"error": "Intrusion introuvable"}), 404
    data = request.get_json(silent=True) or {}
    p = _photo_at(ep, idx, data.get("photo"))
    if not p:
        return jsonify({"error": "Photo introuvable (la liste a change, rouvrez la fiche)"}), 409
    dets = (p.get("ai") or {}).get("detections") or []
    if k < 0 or k >= len(dets) or not dets[k].get("box"):
        return jsonify({"error": "Cadre de l'IA introuvable"}), 409
    verdict = data.get("verdict")
    if verdict is not None and verdict not in DET_VERDICTS:
        return jsonify({"error": "Retour invalide"}), 400
    d = dets[k]
    key = "photos.%d.det_fb.%d" % (idx, k)
    if verdict:
        # "personne" / "vehicule" sur un cadre deja de cette classe = juste
        if verdict == d.get("category"):
            verdict = "juste"
        doc = {"verdict": verdict, "ai_category": d.get("category"), "ai_class": d.get("class"),
               "conf": d.get("conf"), "box": d["box"], "by": _who(), "at": datetime.now(timezone.utc)}
        db["hik_intrusions"].update_one({"_id": oid, "photos.%d.photo" % idx: p.get("photo")}, {"$set": {key: doc}})
    else:
        doc = None
        db["hik_intrusions"].update_one({"_id": oid, "photos.%d.photo" % idx: p.get("photo")}, {"$unset": {key: ""}})
    return jsonify({"ok": True, "k": k, "fb": _pub_det_fb(doc)})


def _pub_det_fb(f):
    if not f:
        return None
    return {"verdict": f.get("verdict"), "by": f.get("by", ""), "at": _iso(f.get("at"))}


VERIFIER_URL = os.getenv("INTRUSION_VERIFIER_URL", "http://127.0.0.1:4011/verify")


@detection_bp.route("/api/detection/episodes/<eid>/photos/<int:idx>/verify", methods=["POST"])
def verify_photo(eid, idx):
    """Passe UNE photo au verificateur IA local (IntrusionVerifier) quand le
    recepteur ne l'a pas fait : il s'arrete a la premiere photo confirmee.
    Donne a l'operateur l'avis de l'IA sur chaque cible ; ne change ni le
    verdict de l'episode ni les alertes. {photo: <url, controle>}."""
    import json
    import urllib.request
    db = _ensure_db()
    oid = _oid(eid)
    ep = db["hik_intrusions"].find_one({"_id": oid}, {"photos": 1}) if oid else None
    if not ep:
        return jsonify({"error": "Intrusion introuvable"}), 404
    p = _photo_at(ep, idx, (request.get_json(silent=True) or {}).get("photo"))
    if not p:
        return jsonify({"error": "Photo introuvable (la liste a change, rouvrez la fiche)"}), 409
    if p.get("ai"):
        return jsonify({"ok": True, "ai": p["ai"], "answer": _ai_answer(p["ai"], p.get("rect"))})
    try:
        req = urllib.request.Request(VERIFIER_URL, data=json.dumps({"photo": p.get("photo"), "rect": p.get("rect")}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.load(r)
    except Exception as e:
        return jsonify({"error": "Verification IA indisponible (%s)" % e}), 503
    min_conf = float((db["cockpit_settings"].find_one({"_id": "detection_ai_filter"}) or {}).get("min_conf") or 0.3)
    real = float(res.get("real_conf") or 0)
    ai = {"decision": "confirmed" if real >= min_conf else "filtered", "real_conf": real,
          "detections": res.get("detections") or [], "guess": res.get("guess") or [], "on_demand": True}
    db["hik_intrusions"].update_one({"_id": oid, "photos.%d.photo" % idx: p.get("photo")},
                                    {"$set": {"photos.%d.ai" % idx: ai}})
    return jsonify({"ok": True, "ai": ai, "answer": _ai_answer(ai, p.get("rect"))})


LABEL_CATEGORIES = ["personne", "vehicule"]


def _clip01(v):
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return None


@detection_bp.route("/api/detection/episodes/<eid>/photos/<int:idx>/label", methods=["POST"])
def set_photo_label(eid, idx):
    """Annotation de l'operateur sur UNE photo : ou est la personne / le
    vehicule. {photo: <url de la photo, controle>, boxes: [{x, y, w, h,
    category: personne|vehicule}]} ; boxes vide = "rien sur cette photo".
    Cadres normalises 0-1 sur la photo entiere (comme rect). Sert au
    reentrainement (verite terrain) ; ne change pas le statut de l'intrusion.
    Si l'IA avait filtre l'intrusion et qu'on indique une personne ou un
    vehicule, l'avis "Erreur" est pose (s'il n'y a pas deja d'avis)."""
    db = _ensure_db()
    oid = _oid(eid)
    ep = db["hik_intrusions"].find_one({"_id": oid}, {"photos.photo": 1, "ai.status": 1, "ai_vote": 1}) if oid else None
    if not ep:
        return jsonify({"error": "Intrusion introuvable"}), 404
    photos = ep.get("photos") or []
    data = request.get_json(silent=True) or {}
    if idx < 0 or idx >= len(photos) or _img(photos[idx].get("photo")) != data.get("photo"):
        return jsonify({"error": "Photo introuvable (la liste a change, rouvrez la fiche)"}), 409
    boxes = []
    for b in (data.get("boxes") or [])[:20]:
        x, y, w, h = (_clip01(b.get(k)) for k in ("x", "y", "w", "h"))
        if None in (x, y, w, h) or w < 0.002 or h < 0.002 or b.get("category") not in LABEL_CATEGORIES:
            return jsonify({"error": "Cadre invalide"}), 400
        boxes.append({"x": round(x, 4), "y": round(y, 4), "w": round(min(w, 1 - x), 4),
                      "h": round(min(h, 1 - y), 4), "category": b["category"]})
    now = datetime.now(timezone.utc)
    label = {"boxes": boxes, "by": _who(), "at": now}
    upd = {"$set": {"photos.%d.label" % idx: label}}
    if boxes and (ep.get("ai") or {}).get("status") == "filtered" and not (ep.get("ai_vote") or {}).get("vote"):
        upd["$set"]["ai_vote"] = {"vote": "down", "by": _who(), "at": now, "auto": "annotation"}
    db["hik_intrusions"].update_one({"_id": oid}, upd)
    ep = db["hik_intrusions"].find_one({"_id": oid}, {"ai": 1, "ai_vote": 1})
    return jsonify({"ok": True, "label": {**label, "at": _iso(now)}, "ai": _pub_ai(ep)})


@detection_bp.route("/api/detection/image/<path:rel>")
def image(rel):
    safe = os.path.normpath(rel)
    # intrusions/ (recepteur) et captures/ (Capture IA, capture_ia.py)
    if ".." in safe or os.path.isabs(safe) or not safe.replace("\\", "/").startswith(("intrusions/", "captures/")):
        abort(400)
    full = os.path.abspath(os.path.join(HIK_IMAGE_DIR, safe))
    if not full.startswith(os.path.abspath(HIK_IMAGE_DIR) + os.sep) or not os.path.isfile(full):
        abort(404)
    return send_file(full, mimetype="image/jpeg", max_age=7 * 86400)


# Capture IA (trafic) et galerie d'entrainement : routes dans ia_capture.py,
# sur ce meme blueprint (memes droits que la page)
import ia_capture  # noqa: E402,F401
