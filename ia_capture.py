# ia_capture.py -- Page IA : cameras en Capture IA (trafic) et galerie
# d'entrainement. Routes sur le blueprint de la page Detection / IA (memes
# droits). Les captures sont prises par la tache planifiee "Cockpit - Capture
# IA" (capture_ia.py), JAMAIS par Cockpit : ici on lit, on configure et on
# recueille les avis des operateurs.
#
#   cockpit_cameras.channels[].roles[type=capture]  reglages (rythme, ecart,
#                         preset, alertes) : source unique, comme les autres usages
#   ia_capture_samples    une mesure = une paire de photos analysee
#   ia_capture_state      etat courant par canal ("<camera_id>:<canal>")
#   ia_capture_zones      zones dessinees sur l'image (polygones 0-1)
from datetime import datetime, timedelta, timezone

from bson.objectid import ObjectId
from flask import jsonify, request
from pymongo import DESCENDING

import detection_arming as arming
from detection import detection_bp, _ensure_db, _iso, _utc, _oid, _img, _who, _pub_ai

STATES = ("fluide", "dense", "ralenti", "bouchon", "normal", "charge", "trop", "rien", "sol", "immobile")
VOTES = ("up", "down")
PURPOSES = ("traffic", "crowd", "secours")
TRAFFIC_Q = {"purpose": {"$nin": ["crowd", "secours"]}}  # anciennes mesures sans purpose = trafic


def _role_spec():
    import cameras
    return cameras.ROLE_INDEX["capture"]


def _key(cid, ch):
    return "%s:%s" % (cid, ch)


def _capture_role(ch):
    return next((r for r in ch.get("roles") or [] if r.get("type") == "capture"), None)


def _pub_sample(s, full=False):
    zones = s.get("zones") or {}
    va = s.get("vehicles_a") or []
    out = {
        "id": str(s["_id"]), "at": _iso(s.get("at")), "state": s.get("state"),
        "camera_id": str(s.get("camera_id")), "channel": s.get("channel"),
        "camera_name": s.get("camera_name", ""), "channel_name": s.get("channel_name", ""),
        "n": len(va),
        "arretes": sum(1 for v in va if v.get("status") == "arrete"),
        "deplaces": sum(1 for v in va if v.get("status") in ("deplace", "lent")),
        "partis": sum(1 for v in va if v.get("status") in ("parti", "roule", "bref")),
        "brefs": sum(1 for v in va if v.get("status") == "bref"),
        "method": s.get("method"),
        "thumb": _img(((s.get("photos") or [{}])[0]).get("thumb")),
        "vote": _pub_vote(s.get("vote")),
        "purpose": s.get("purpose") or "traffic",
        "total": s.get("total"),
    }
    if full:
        out["grid"], out["grid_max"] = s.get("grid"), s.get("grid_max")
        out["persons_yolo"] = s.get("persons_yolo")
        out["gap_s"] = s.get("gap_s")
        out["pair_s"], out["source"] = s.get("pair_s"), s.get("source")
        out["photos"] = [{"photo": _img(p.get("photo")), "thumb": _img(p.get("thumb")), "at": _iso(p.get("at"))}
                         for p in s.get("photos") or []]
        out["vehicles_a"], out["vehicles_b"] = va, s.get("vehicles_b") or []
        # 2e image de chaque paire (deux paires) : cadres a la couleur du jumeau
        out["vehicles_a2"], out["vehicles_b2"] = s.get("vehicles_a2") or [], s.get("vehicles_b2") or []
        out["zones"] = [{"id": k, **v} for k, v in zones.items()]
        out["preset"] = s.get("preset")
        out["waze"] = s.get("waze")
        out["zone_votes"] = {k: {"state": v.get("state"), "by": v.get("by", "")} for k, v in (s.get("zone_votes") or {}).items()}
        # Secours : squelette et posture de chaque personne
        out["persons_pose"] = [{k: q.get(k) for k in ("box", "conf", "kpts", "posture", "angle", "zone", "counted")}
                               | {"since": _iso(q.get("since"))} for q in s.get("persons_pose") or []]
    if out["purpose"] == "secours":
        out["lying"], out["lying_min"] = s.get("lying", 0), s.get("lying_min", 0)
    return out


def _pub_vote(v):
    if not v:
        return None
    return {"vote": v.get("vote"), "state": v.get("state"), "by": v.get("by", ""), "at": _iso(v.get("at"))}


# ---------------------------------------------------------------------------
# Cameras en capture
# ---------------------------------------------------------------------------
@detection_bp.route("/api/detection/capture/cameras")
def capture_cameras():
    import cameras
    db = _ensure_db()
    now = datetime.now(timezone.utc)
    groups = list(db["detection_groups"].find())
    # Regles actives de la centrale d'alertes qui surveillent ces cameras
    # (trafic, stationnement, foule, secours)
    rules = list(db["cockpit_alert_definitions"].find(
        {"detection_type": {"$in": ["traffic_camera", "parking_camera", "crowd_camera", "secours_camera"]}, "enabled": True},
        {"name": 1, "slug": 1, "params": 1}))
    out, candidates = [], []
    for d in db["cockpit_cameras"].find({}, {"name": 1, "channels": 1, "brand": 1, "ip": 1, "enabled": 1,
                                             "group_path": 1, "model": 1, "site": 1}).sort("name", 1):
        piste = cameras.is_piste_device(d)
        pilotable = d.get("brand") in cameras.CONTROLLABLE_BRANDS and not piste and d.get("enabled", True)
        chans = d.get("channels") or []
        if pilotable:
            candidates.append({"camera_id": str(d["_id"]), "name": d.get("name", ""), "cellular": cameras.is_cellular(d),
                               "channels": [{"no": c.get("no"), "name": c.get("name", "")} for c in chans
                                            if not _capture_role(c)]})
        for ch in chans:
            role = _capture_role(ch)
            if not role:
                continue
            key = _key(d["_id"], ch.get("no"))
            st = db["ia_capture_state"].find_one({"_id": key}) or {}
            last = db["ia_capture_samples"].find_one({"camera_id": d["_id"], "channel": ch.get("no")}, sort=[("at", -1)])
            gs = arming.channel_groups(groups, d["_id"], ch.get("no"))
            out.append({
                "key": key, "camera_id": str(d["_id"]), "channel": ch.get("no"),
                "camera_name": d.get("name", ""), "channel_name": ch.get("name", ""),
                "brand": d.get("brand"), "model": d.get("model", ""), "site": d.get("site", ""),
                "cellular": cameras.is_cellular(d), "usable": pilotable,
                "role": {k: role.get(k) for k in ("purpose", "interval_min", "gap_s", "preset")},
                "alert_rules": [r.get("name") or r.get("slug") for r in rules
                                if key in ((r.get("params") or {}).get("channels") or [])],
                "groups": [str(g["_id"]) for g in gs],
                "planning_on": (not gs) or any(arming.group_state(g, now, db)[0] for g in gs),
                "state": st.get("state"), "streak": st.get("streak", 0),
                "last_try": _iso(st.get("last_try")), "last_error": st.get("last_error"),
                "error_at": _iso(st.get("error_at")),
                "last": _pub_sample(last, full=True) if last else None,
            })
    return jsonify({"cameras": out, "candidates": candidates,
                    "params": _role_spec()["params"], "states": STATES, "switch": _pub_switch(db)})


# Interrupteur general par fonction (trafic, foule, secours) : en arret, la
# tache planifiee ne prend plus aucune photo pour cette fonction ; `cell`
# faux : seulement les cameras filaires (data des cameras 4G preservee).
# capture_ia.capture_enabled lit le meme document.
def _pub_switch(db):
    d = db["ia_capture_state"].find_one({"_id": "_switch"}) or {}
    return {p: {"on": (d.get(p) or {}).get("on", True) is not False,
                "cell": (d.get(p) or {}).get("cell", True) is not False,
                "by": (d.get(p) or {}).get("by", ""), "at": _iso((d.get(p) or {}).get("at"))} for p in PURPOSES}


@detection_bp.route("/api/detection/capture/switch", methods=["PUT"])
def capture_switch():
    """{purpose: traffic|crowd|secours, on: bool, cell: bool} (on et / ou cell)"""
    db = _ensure_db()
    data = request.get_json(silent=True) or {}
    p = data.get("purpose")
    upd = {k: data[k] for k in ("on", "cell") if isinstance(data.get(k), bool)}
    if p not in PURPOSES or not upd:
        return jsonify({"error": "Parametre invalide"}), 400
    upd.update({"by": _who(), "at": datetime.now(timezone.utc)})
    db["ia_capture_state"].update_one({"_id": "_switch"}, {"$set": {"%s.%s" % (p, k): v for k, v in upd.items()}}, upsert=True)
    return jsonify({"ok": True, "switch": _pub_switch(db)})


def _find_channel(db, cid, ch):
    d = db["cockpit_cameras"].find_one({"_id": _oid(cid)}) if _oid(cid) else None
    if not d:
        return None, None
    for i, c in enumerate(d.get("channels") or []):
        if c.get("no") == ch:
            return d, i
    return d, None


def _write_role(db, d, idx, role):
    """Remplace l'usage capture du canal (role None = retire), avec la meme
    validation que la page Cameras."""
    import cameras
    chans = d.get("channels") or []
    roles = [r for r in chans[idx].get("roles") or [] if r.get("type") != "capture"]
    if role is not None:
        roles.append(dict(role, type="capture"))
    roles = cameras._clean_roles(roles)
    db["cockpit_cameras"].update_one({"_id": d["_id"]}, {"$set": {"channels.%d.roles" % idx: roles,
                                                                  "updatedAt": datetime.now(timezone.utc)}})
    return next((r for r in roles if r["type"] == "capture"), None)


@detection_bp.route("/api/detection/capture/cameras", methods=["POST"])
def capture_add():
    import cameras
    db = _ensure_db()
    data = request.get_json(silent=True) or {}
    try:
        ch = int(data.get("channel") or 1)
    except (TypeError, ValueError):
        return jsonify({"error": "Canal invalide"}), 400
    d, idx = _find_channel(db, data.get("camera_id"), ch)
    if d is None or idx is None:
        return jsonify({"error": "Camera ou canal introuvable"}), 404
    if cameras.is_piste_device(d):
        return jsonify({"error": "Camera de piste : aucune capture depuis Cockpit (securite piste)"}), 403
    if d.get("brand") not in cameras.CONTROLLABLE_BRANDS:
        return jsonify({"error": "Marque non pilotable : capture impossible"}), 400
    defaults = {p["key"]: p["default"] for p in _role_spec()["params"]}
    if data.get("purpose") in ("crowd", "secours"):
        # foule / secours : une photo toutes les 5 min
        defaults.update({"purpose": data["purpose"], "interval_min": "5"})
    role = _write_role(db, d, idx, defaults)
    return jsonify({"ok": True, "role": role}), 201


@detection_bp.route("/api/detection/capture/cameras/<cid>/<int:ch>", methods=["PUT", "DELETE"])
def capture_update(cid, ch):
    db = _ensure_db()
    d, idx = _find_channel(db, cid, ch)
    if d is None or idx is None or not _capture_role(d["channels"][idx]):
        return jsonify({"error": "Camera en capture introuvable"}), 404
    if request.method == "DELETE":
        _write_role(db, d, idx, None)
        db["ia_capture_state"].delete_one({"_id": _key(d["_id"], ch)})
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    role = dict(_capture_role(d["channels"][idx]))
    for p in _role_spec()["params"]:
        if p["key"] in data:
            role[p["key"]] = data[p["key"]]
    role = _write_role(db, d, idx, role)
    return jsonify({"ok": True, "role": role})


@detection_bp.route("/api/detection/capture/cameras/<cid>/<int:ch>/now", methods=["POST"])
def capture_now(cid, ch):
    """Demande une mesure au prochain passage de la tache (moins d'une minute) :
    la capture n'est jamais faite par Cockpit."""
    db = _ensure_db()
    d, idx = _find_channel(db, cid, ch)
    if d is not None and idx is not None:
        role = next((r for r in (d["channels"][idx].get("roles") or []) if r.get("type") == "capture"), {})
        p = role.get("purpose") if role.get("purpose") in ("crowd", "secours") else "traffic"
        sw = _pub_switch(db)[p]
        name = {"crowd": "Foule", "secours": "Secours"}.get(p, "Trafic")
        if not sw["on"]:
            return jsonify({"error": "Fonction %s a l'arret : la remettre en marche d'abord" % name}), 409
        import cameras
        if not sw["cell"] and cameras.is_cellular(d):
            return jsonify({"error": "Fonction %s : cameras 4G exclues (data preservee), les reactiver d'abord" % name}), 409
    db["ia_capture_state"].update_one({"_id": _key(cid, ch)}, {"$unset": {"last_try": ""}}, upsert=True)
    return jsonify({"ok": True})


@detection_bp.route("/api/detection/capture/cameras/<cid>/<int:ch>/presets")
def capture_presets(cid, ch):
    """Presets de vue de la camera (lecture), pour choisir celui a rappeler."""
    import cameras
    db = _ensure_db()
    d, idx = _find_channel(db, cid, ch)
    if d is None:
        return jsonify({"error": "Camera introuvable"}), 404
    if cameras.is_piste_device(d):
        return jsonify({"error": "Camera de piste"}), 403
    try:
        cam = cameras.make_hik_camera(d, ch)
        presets = cameras.call_camera(d, lambda: cameras._view_presets(d, cam))
        return jsonify({"presets": presets})
    except Exception as e:
        return jsonify({"error": cameras.camera_error(d, e, "Presets")}), 502


@detection_bp.route("/api/detection/groups/<gid>/member", methods=["POST"])
def group_member(gid):
    """Ajoute / retire UN canal d'un groupe (planning) sans reecrire le reste du
    groupe (forcage en cours conserve). {camera_id, channel, member: bool}."""
    db = _ensure_db()
    data = request.get_json(silent=True) or {}
    cid = _oid(data.get("camera_id"))
    try:
        ch = int(data.get("channel"))
    except (TypeError, ValueError):
        return jsonify({"error": "Canal invalide"}), 400
    if not cid or not _oid(gid):
        return jsonify({"error": "Parametre invalide"}), 400
    m = {"camera_id": cid, "channel": ch}
    upd = {"$addToSet": {"members": m}} if data.get("member") else {"$pull": {"members": m}}
    res = db["detection_groups"].update_one({"_id": _oid(gid)}, upd)
    if not res.matched_count:
        return jsonify({"error": "Groupe introuvable"}), 404
    arming.invalidate()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Mesures
# ---------------------------------------------------------------------------
@detection_bp.route("/api/detection/capture/samples")
def capture_samples():
    db = _ensure_db()
    cid, _, ch = (request.args.get("key") or "").partition(":")
    if not _oid(cid):
        return jsonify({"error": "Camera invalide"}), 400
    try:
        hours = max(1, min(24 * 14, int(request.args.get("hours", 24))))
        ch = int(ch)
    except ValueError:
        return jsonify({"error": "Parametre invalide"}), 400
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    docs = db["ia_capture_samples"].find({"camera_id": _oid(cid), "channel": ch, "at": {"$gte": since}},
                                         {"vehicles_b": 0, "vehicles_a2": 0, "vehicles_b2": 0, "zones": 0}).sort("at", 1).limit(5000)
    return jsonify({"items": [_pub_sample(s) for s in docs]})


@detection_bp.route("/api/detection/capture/samples/<sid>")
def capture_sample(sid):
    db = _ensure_db()
    s = db["ia_capture_samples"].find_one({"_id": _oid(sid)}) if _oid(sid) else None
    if not s:
        return jsonify({"error": "Mesure introuvable"}), 404
    out = _pub_sample(s, full=True)
    # Trace de chaque vehicule (zones actuelles) : la photo montre a quel
    # sens de circulation l'IA rattache chaque voiture
    traces = list(db["ia_capture_zones"].find({"camera_id": s.get("camera_id"), "channel": s.get("channel"), "kind": "itineraire"}))
    if traces:
        import capture_ia
        for k in ("vehicles_a", "vehicles_b", "vehicles_a2", "vehicles_b2"):
            own = capture_ia.assign_traces(traces, out[k])
            for v in out[k]:
                if id(v) in own:
                    v["trace"] = own[id(v)]
    return jsonify(out)


@detection_bp.route("/api/detection/capture/samples/<sid>/feedback", methods=["POST"])
def capture_feedback(sid):
    """Avis de l'operateur sur l'etat calcule : {vote: up|down|null, state:
    fluide|dense|ralenti|bouchon (le vrai etat, si l'IA s'est trompee)}. Garde la
    mesure au-dela de la duree de conservation (jeu d'entrainement)."""
    db = _ensure_db()
    data = request.get_json(silent=True) or {}
    vote, state = data.get("vote"), data.get("state")
    if vote is not None and vote not in VOTES or state is not None and state not in STATES:
        return jsonify({"error": "Avis invalide"}), 400
    oid = _oid(sid)
    upd = ({"$set": {"vote": {"vote": vote, "state": state, "by": _who(), "at": datetime.now(timezone.utc)}}}
           if vote else {"$unset": {"vote": ""}})
    res = db["ia_capture_samples"].update_one({"_id": oid}, upd) if oid else None
    if not res or not res.matched_count:
        return jsonify({"error": "Mesure introuvable"}), 404
    return jsonify({"ok": True, "vote": _pub_vote((db["ia_capture_samples"].find_one({"_id": oid}, {"vote": 1}) or {}).get("vote"))})


def _zone_vote_doc(s, zid, zm, state):
    """Avis de zone + mesures du moment (apprentissage, Waze plus tard)."""
    wz = [r.get("ratio") for r in ((s.get("waze") or {}).get("routes") or {}).values()
          if r.get("zone") == zid and r.get("ratio") is not None]
    return {"state": state, "by": _who(), "at": datetime.now(timezone.utc),
            "veh": zm.get("veh"), "state_motion": zm.get("state_motion"), "state_ai": zm.get("state"),
            "n": zm.get("n"), "arretes": zm.get("arretes"), "lents": zm.get("deplaces"),
            "waze_ratio": wz[0] if wz else None}


@detection_bp.route("/api/detection/capture/samples/<sid>/zone-vote", methods=["POST"])
def capture_zone_vote(sid):
    """Etat VRAI d'une zone / d'un itineraire trace sur une mesure, selon
    l'operateur : {zone: id, state: fluide|dense|ralenti|bouchon|null}. Garde
    les mesures du moment (vehicules par image, etat du mouvement, Waze) et
    apprend les seuils de vehicules de chaque niveau pour cette zone
    (capture_ia.learn_levels) ; l'historique recent est recalcule."""
    import capture_ia
    db = _ensure_db()
    data = request.get_json(silent=True) or {}
    zid, state = str(data.get("zone") or ""), data.get("state")
    if state is not None and state not in ("fluide", "dense", "ralenti", "bouchon"):
        return jsonify({"error": "Etat invalide"}), 400
    oid = _oid(sid)
    s = db["ia_capture_samples"].find_one({"_id": oid}, {"zones": 1, "waze": 1, "camera_id": 1, "channel": 1}) if oid else None
    zone = db["ia_capture_zones"].find_one({"_id": _oid(zid)}) if _oid(zid) else None
    if not s or not zone or zone.get("camera_id") != s.get("camera_id"):
        return jsonify({"error": "Mesure ou zone introuvable"}), 404
    zm = (s.get("zones") or {}).get(zid)
    if not zm:
        return jsonify({"error": "Zone absente de cette mesure (dessinee apres ?)"}), 400
    if state is None:
        db["ia_capture_samples"].update_one({"_id": oid}, {"$unset": {"zone_votes." + zid: ""}})
    else:
        db["ia_capture_samples"].update_one({"_id": oid}, {"$set": {"zone_votes." + zid: _zone_vote_doc(s, zid, zm, state)}})
    levels = capture_ia.learn_levels(db, zone["_id"])
    try:
        capture_ia.recompute_zones(db, s["camera_id"], s["channel"], days=1)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Recalcul apres avis de zone impossible")
    return jsonify({"ok": True, "levels": _pub_levels(levels)})


def _pub_levels(lv):
    if not lv:
        return None
    return {k: lv.get(k) for k in ("dense", "ralenti", "bouchon", "votes", "agree", "by_state")}


# ---------------------------------------------------------------------------
# Zones de mesure (sur l'image)
# ---------------------------------------------------------------------------
ZONE_KINDS = ("trafic", "stationnement", "foule", "itineraire", "secours")


def _pub_zone(z):
    return {"id": str(z["_id"]), "name": z.get("name", ""), "polygon": z.get("polygon") or [],
            "color": z.get("color", "#7c3aed"), "kind": z.get("kind") or "trafic",
            "max_min": z.get("max_min"), "surface_m2": z.get("surface_m2"),
            "seuil_charge": z.get("seuil_charge"), "seuil_trop": z.get("seuil_trop"),
            "geo": z.get("geo") or [], "line": z.get("line") or [],
            "waze_route": z.get("waze_route"), "waze_name": z.get("waze_name"),
            "levels": _pub_levels(z.get("levels"))}


def _num(v, lo, hi):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def _zone_fields(data):
    """Zone "trafic" (etat fluide / ralenti / bouchon) ou "stationnement"
    (stationnement interdit : alerte au-dela de max_min minutes)."""
    name = str(data.get("name") or "").strip()[:60]
    kind = data.get("kind") if data.get("kind") in ZONE_KINDS else "trafic"
    if kind == "itineraire":
        # Itineraire trace : ligne du depart a l'arrivee (sens de circulation),
        # itineraire Waze facultatif
        try:
            line = [[round(min(1.0, max(0.0, float(x))), 4), round(min(1.0, max(0.0, float(y))), 4)]
                    for x, y in data.get("line") or []][:40]
        except (TypeError, ValueError):
            return None, "Trace invalide"
        if not name or len(line) < 2:
            return None, "Nom et au moins 2 points (depart puis arrivee) requis"
        wr = str(data.get("waze_route") or "").strip()[:40] or None
        return {"name": name, "kind": kind, "line": line, "polygon": [], "waze_route": wr,
                "waze_name": str(data.get("waze_name") or "")[:120] if wr else None,
                "color": str(data.get("color") or "#0891b2")[:9]}, None
    poly = data.get("polygon") or []
    try:
        poly = [[round(min(1.0, max(0.0, float(x))), 4), round(min(1.0, max(0.0, float(y))), 4)] for x, y in poly][:40]
    except (TypeError, ValueError):
        return None, "Polygone invalide"
    if not name or len(poly) < 3:
        return None, "Nom et au moins 3 points requis"
    f = {"name": name, "polygon": poly, "color": str(data.get("color") or "#7c3aed")[:9], "kind": kind}
    if kind == "stationnement":
        try:
            f["max_min"] = max(1, min(1440, int(data.get("max_min") or 10)))
        except (TypeError, ValueError):
            return None, "Duree maximale invalide"
    if kind == "foule":
        # Surface reelle (m2, saisie ou mesuree sur la carte) et seuils en
        # personnes / m2 : capacite = surface x seuil
        surf = data.get("surface_m2")
        f["surface_m2"] = _num(surf, 1, 1000000) if surf not in (None, "") else None
        if surf not in (None, "") and f["surface_m2"] is None:
            return None, "Surface invalide (m2)"
        f["seuil_charge"] = _num(data.get("seuil_charge") or 2, 0.1, 10) or 2.0
        f["seuil_trop"] = _num(data.get("seuil_trop") or 4, 0.1, 10) or 4.0
        if f["seuil_trop"] <= f["seuil_charge"]:
            return None, "Le seuil \"trop de monde\" doit depasser le seuil \"charge\""
        geo = data.get("geo") or []
        try:
            f["geo"] = [[round(float(a), 7), round(float(b), 7)] for a, b in geo][:60] if len(geo) >= 3 else []
        except (TypeError, ValueError):
            f["geo"] = []
    return f, None


@detection_bp.route("/api/detection/capture/zones")
def capture_zones():
    db = _ensure_db()
    cid, _, ch = (request.args.get("key") or "").partition(":")
    try:
        q = {"camera_id": _oid(cid), "channel": int(ch)}
    except ValueError:
        return jsonify({"error": "Camera invalide"}), 400
    return jsonify({"zones": [_pub_zone(z) for z in db["ia_capture_zones"].find(q).sort("name", 1)]})


@detection_bp.route("/api/detection/capture/zones", methods=["POST"])
def capture_zone_create():
    db = _ensure_db()
    data = request.get_json(silent=True) or {}
    f, err = _zone_fields(data)
    if err:
        return jsonify({"error": err}), 400
    try:
        f["camera_id"], f["channel"] = _oid(data.get("camera_id")), int(data.get("channel"))
    except (TypeError, ValueError):
        return jsonify({"error": "Camera invalide"}), 400
    if not f["camera_id"]:
        return jsonify({"error": "Camera invalide"}), 400
    zid = db["ia_capture_zones"].insert_one(f).inserted_id
    n = _recompute(db, f["camera_id"], f["channel"])
    return jsonify(dict(_pub_zone(db["ia_capture_zones"].find_one({"_id": zid})), recomputed=n)), 201


@detection_bp.route("/api/detection/capture/zones/<zid>", methods=["PUT", "DELETE"])
def capture_zone_update(zid):
    db = _ensure_db()
    oid = _oid(zid)
    old = db["ia_capture_zones"].find_one({"_id": oid}, {"camera_id": 1, "channel": 1})
    if not old:
        return jsonify({"error": "Zone introuvable"}), 404
    if request.method == "DELETE":
        db["ia_capture_zones"].delete_one({"_id": oid})
        return jsonify({"ok": True, "recomputed": _recompute(db, old["camera_id"], old["channel"])})
    f, err = _zone_fields(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    z = db["ia_capture_zones"].find_one_and_update({"_id": oid}, {"$set": f}, return_document=True)
    return jsonify(dict(_pub_zone(z), recomputed=_recompute(db, old["camera_id"], old["channel"])))


def _recompute(db, camera_id, channel):
    """Historique recalcule avec les zones actuelles (capture_ia.recompute_zones)."""
    try:
        import capture_ia
        # Statistiques (vehicules par zone, Waze des itineraires traces) :
        # refaites par la tache planifiee
        db["ia_capture_links"].update_one({"_id": _key(camera_id, channel)}, {"$set": {
            "camera_id": camera_id, "channel": channel, "rebuild": True}}, upsert=True)
        return capture_ia.recompute_zones(db, camera_id, channel)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Recalcul des zones impossible (%s canal %s)", camera_id, channel)
        return 0


# ---------------------------------------------------------------------------
# Itineraires Waze lies a un canal trafic (ia_capture_links "<camera_id>:<canal>"
# {waze_routes: [id]}) : choisis ici, ou etiquetes [cam:N] / [cam:N.canal]
# dans leur nom Waze. Les statistiques sont refaites par la tache planifiee.
# ---------------------------------------------------------------------------
@detection_bp.route("/api/detection/capture/cameras/<cid>/<int:ch>/waze")
def capture_waze(cid, ch):
    import capture_ia
    db = _ensure_db()
    d, idx = _find_channel(db, cid, ch)
    if d is None or idx is None:
        return jsonify({"error": "Camera introuvable"}), 404
    lt = db["waze_trafic"].find_one({"_id": "latest"}) or {}
    routes = (lt.get("data") or {}).get("routes") or []
    link = db["ia_capture_links"].find_one({"_id": _key(d["_id"], ch)}) or {}
    chosen = {str(x) for x in link.get("waze_routes") or []}
    rz = link.get("route_zones") or {}
    tzones = [{"id": str(z["_id"]), "name": z.get("name", "")} for z in
              db["ia_capture_zones"].find({"camera_id": d["_id"], "channel": ch}).sort("name", 1)
              if (z.get("kind") or "trafic") == "trafic"]
    from detection import _device_position
    pos = _device_position(db, d)[0]  # pas le cache de la tache : position a jour
    num = capture_ia.cam_number(d.get("name"))
    out = []
    for r in routes:
        rid = str(r.get("id") or "")
        if not rid:
            continue
        dist = capture_ia.route_distance_m(r, pos["lat"], pos["lng"]) if pos else None
        t, h = r.get("time"), r.get("historicTime")
        out.append({"id": rid, "name": r.get("name", ""), "from": r.get("fromName", ""), "to": r.get("toName", ""),
                    "length": r.get("length"), "time": t, "historic": h, "jam": r.get("jamLevel"),
                    "ratio": round(t / h, 2) if t and h else None,
                    "distance_m": round(dist) if dist is not None else None,
                    "tagged": capture_ia.waze_tagged(r.get("name"), num, ch), "chosen": rid in chosen,
                    "zone": rz.get(rid)})
    out.sort(key=lambda x: (not (x["tagged"] or x["chosen"]), x["distance_m"] if x["distance_m"] is not None else 1e9))
    return jsonify({"routes": out, "zones": tzones, "tag": "[cam:%s]" % num if num else None, "tag_channel": "[cam:%s.%s]" % (num, ch) if num else None,
                    "positioned": bool(pos), "fetched_at": _iso(lt.get("fetched_at"))})


@detection_bp.route("/api/detection/capture/cameras/<cid>/<int:ch>/waze", methods=["PUT"])
def capture_waze_save(cid, ch):
    db = _ensure_db()
    d, idx = _find_channel(db, cid, ch)
    if d is None or idx is None:
        return jsonify({"error": "Camera introuvable"}), 404
    data = request.get_json(silent=True) or {}
    ids = sorted({str(x)[:40] for x in data.get("routes") or [] if str(x).strip()})[:30]
    # Zone (sens de circulation) de chaque itineraire : coche ou etiquete
    valid = {str(z["_id"]) for z in db["ia_capture_zones"].find({"camera_id": d["_id"], "channel": ch}, {"_id": 1})}
    rz = {str(k)[:40]: str(v) for k, v in (data.get("route_zones") or {}).items() if str(v) in valid}
    db["ia_capture_links"].update_one({"_id": _key(d["_id"], ch)}, {"$set": {
        "camera_id": d["_id"], "channel": ch, "waze_routes": ids, "route_zones": rz, "rebuild": True,
        "by": _who(), "at": datetime.now(timezone.utc)}}, upsert=True)
    return jsonify({"ok": True, "routes": ids})


# ---------------------------------------------------------------------------
# Carte globale (ecran d'ouverture de la page IA) : toutes les cameras placees
# ---------------------------------------------------------------------------
@detection_bp.route("/api/detection/global-map")
def global_map():
    """Toutes les cameras ayant une position (une IP = un lieu), avec leurs
    usages (intrusion, trafic, LAPI), l'etat du trafic et les intrusions du jour."""
    import cameras
    from detection import _device_position
    db = _ensure_db()
    now = datetime.now(timezone.utc)
    day0 = now.astimezone(arming.PARIS).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    intr = {}
    for r in db["hik_intrusions"].aggregate([
            {"$match": {"started_at": {"$gte": day0.replace(tzinfo=None)}, "armed": {"$ne": False}}},
            {"$group": {"_id": "$camera_id", "n": {"$sum": 1},
                        "open": {"$sum": {"$cond": [{"$ne": ["$status", "closed"]}, 1, 0]}},
                        "real": {"$sum": {"$cond": [{"$ne": ["$ai.status", "filtered"]}, 1, 0]}}}}]):
        intr[str(r["_id"])] = r
    states = {s["_id"]: s for s in db["ia_capture_state"].find({"_id": {"$regex": ":"}})}
    order = {"inconnu": -1, "fluide": 0, "dense": 1, "ralenti": 2, "bouchon": 3}
    out, unplaced = [], 0
    for d in db["cockpit_cameras"].find({}, {"name": 1, "ip": 1, "site": 1, "channels": 1, "map": 1, "brand": 1,
                                             "model": 1, "group_path": 1, "enabled": 1}):
        pos, _src, _geo = _device_position(db, d)
        if not pos:
            unplaced += 1
            continue
        types, traffic = set(), []
        for ch in d.get("channels") or []:
            for r in ch.get("roles") or []:
                types.add(r.get("type"))
            role = _capture_role(ch)
            if role:
                st = states.get(_key(d["_id"], ch.get("no"))) or {}
                traffic.append({"channel": ch.get("no"), "state": st.get("state"), "last_at": _iso(st.get("last_at")),
                                "key": _key(d["_id"], ch.get("no")), "purpose": role.get("purpose") or "traffic"})
        crowd = [t for t in traffic if t["purpose"] == "crowd"]
        rescue = [t for t in traffic if t["purpose"] == "secours"]
        traffic = [t for t in traffic if t["purpose"] not in ("crowd", "secours")]
        worst = max((t["state"] or "inconnu" for t in traffic), key=lambda s: order.get(s, -1)) if traffic else None
        corder = {"inconnu": -1, "normal": 0, "charge": 1, "trop": 2}
        cworst = max((t["state"] or "inconnu" for t in crowd), key=lambda s: corder.get(s, -1)) if crowd else None
        sorder = {"inconnu": -1, "rien": 0, "sol": 1, "immobile": 2}
        sworst = max((t["state"] or "inconnu" for t in rescue), key=lambda s: sorder.get(s, -1)) if rescue else None
        it = intr.get(str(d["_id"])) or {}
        out.append({
            "id": str(d["_id"]), "name": d.get("name", ""), "site": d.get("site", ""),
            "lat": pos["lat"], "lng": pos["lng"], "enabled": d.get("enabled", True),
            "cellular": cameras.is_cellular(d), "piste": cameras.is_piste_device(d),
            "intrusion": "fielddetection" in types, "traffic": bool(traffic), "anpr": "anpr" in types,
            "traffic_state": worst, "traffic_channels": traffic,
            "crowd": bool(crowd), "crowd_state": cworst, "crowd_channels": crowd,
            "secours": bool(rescue), "secours_state": sworst, "secours_channels": rescue,
            "intrusions_today": it.get("n", 0), "intrusions_open": it.get("open", 0), "intrusions_real": it.get("real", 0),
            "channels": len(d.get("channels") or []),
        })
    return jsonify({"cameras": out, "unplaced": unplaced})


@detection_bp.route("/api/detection/global-map/<cid>")
def global_map_detail(cid):
    """Volet de detail d'une camera de la carte globale : identite, canaux et
    usages, derniere image, intrusions recentes, trafic de chaque canal."""
    import cameras
    from detection import _pub_episode, _zones
    db = _ensure_db()
    oid = _oid(cid)
    d = db["cockpit_cameras"].find_one({"_id": oid}) if oid else None
    if not d:
        return jsonify({"error": "Camera introuvable"}), 404
    now = datetime.now(timezone.utc)
    payload = getattr(request, "user_payload", {}) or {}
    is_admin = "admin" in (payload.get("roles") or [])
    chans = []
    for ch in d.get("channels") or []:
        types = [r.get("type") for r in ch.get("roles") or []]
        item = {"no": ch.get("no"), "name": ch.get("name", ""), "roles": types}
        role = _capture_role(ch)
        if role:
            key = _key(d["_id"], ch.get("no"))
            st = db["ia_capture_state"].find_one({"_id": key}) or {}
            last = db["ia_capture_samples"].find_one({"camera_id": d["_id"], "channel": ch.get("no")}, sort=[("at", -1)])
            item["traffic"] = {"key": key, "state": st.get("state"), "last_error": st.get("last_error"),
                               "purpose": role.get("purpose") or "traffic",
                               "role": {k: role.get(k) for k in ("interval_min", "gap_s", "preset")},
                               "last": _pub_sample(last, full=True) if last else None}
        chans.append(item)
    zones = _zones(db)
    eps = [_pub_episode(e, zones, now) for e in db["hik_intrusions"].find(
        {"camera_id": d["_id"]}, {"targets": 0}).sort("started_at", DESCENDING).limit(6)]
    snap = cameras.latest_snapshot_path(str(d["_id"]))
    out = {
        "id": str(d["_id"]), "name": d.get("name", ""), "site": d.get("site", ""), "location": d.get("location", ""),
        "brand": d.get("brand", ""), "model": d.get("model", ""), "enabled": d.get("enabled", True),
        "cellular": cameras.is_cellular(d), "piste": cameras.is_piste_device(d),
        "pilotable": d.get("brand") in cameras.CONTROLLABLE_BRANDS and not cameras.is_piste_device(d),
        "camera_path": d.get("camera_path", ""), "channels": chans, "episodes": eps,
        "snapshot": ("/api/pcorg/cameras/%s/snapshot" % d["_id"]) if snap else None,
        "is_admin": is_admin,
    }
    if is_admin:
        out["ip"] = d.get("ip", "")
    return jsonify(out)


# ---------------------------------------------------------------------------
# Galerie d'entrainement : intrusions et captures, les plus recentes d'abord
# ---------------------------------------------------------------------------
TRAIN_TARGET_PHOTOS = 300  # photos annotees (cadres) avant de re-entrainer le detecteur


@detection_bp.route("/api/detection/gallery/stats")
def gallery_stats():
    """Compteur du jeu d'entrainement : avis par source, photos d'intrusion
    annotees (cadres poses, ou "rien" = exemple negatif), avis par trace."""
    db = _ensure_db()
    S, I = db["ia_capture_samples"], db["hik_intrusions"]

    def votes(coll, field, q=None):
        out = {"up": 0, "down": 0}
        for r in coll.aggregate([{"$match": dict(q or {}, **{field: {"$in": ["up", "down"]}})},
                                 {"$group": {"_id": "$" + field, "n": {"$sum": 1}}}]):
            out[r["_id"]] = r["n"]
        return out
    lab = {"photos": 0, "with_boxes": 0, "empty": 0, "boxes": {}}
    for r in I.aggregate([{"$match": {"photos.label": {"$exists": True}}}, {"$unwind": "$photos"},
                          {"$match": {"photos.label": {"$exists": True}}},
                          {"$project": {"b": "$photos.label.boxes"}}]):
        b = r.get("b") or []
        lab["photos"] += 1
        lab["with_boxes" if b else "empty"] += 1
        for x in b:
            lab["boxes"][x.get("category")] = lab["boxes"].get(x.get("category"), 0) + 1
    names = {str(z["_id"]): z.get("name", "") for z in db["ia_capture_zones"].find({"kind": {"$nin": ["stationnement", "foule"]}}, {"name": 1})}
    traces = {}
    for r in S.aggregate([{"$match": {"zone_votes": {"$exists": True, "$ne": {}}}},
                          {"$project": {"z": {"$objectToArray": "$zone_votes"}}}, {"$unwind": "$z"},
                          {"$group": {"_id": "$z.k", "n": {"$sum": 1}}}]):
        if r["_id"] in names:
            traces[names[r["_id"]]] = r["n"]
    return jsonify({
        "intrusions": dict(votes(I, "ai_vote.vote"), total=I.count_documents({})),
        "traffic": dict(votes(S, "vote.vote", TRAFFIC_Q), total=S.count_documents(TRAFFIC_Q)),
        "crowd": dict(votes(S, "vote.vote", {"purpose": "crowd"}), total=S.count_documents({"purpose": "crowd"})),
        "secours": dict(votes(S, "vote.vote", {"purpose": "secours"}), total=S.count_documents({"purpose": "secours"})),
        "labels": lab, "target_photos": TRAIN_TARGET_PHOTOS,
        "traces": traces,
    })


@detection_bp.route("/api/detection/gallery")
def _intrusion_review(e):
    """Interet d'une intrusion pour l'apprentissage (calcule a la lecture :
    elles sont peu nombreuses). Toutes celles verifiees par l'IA comptent ;
    les cas douteux passent devant."""
    ai = e.get("ai") or {}
    if not ai.get("status") or ai.get("status") == "unverified":
        return {"score": 0, "reasons": []}
    score, why = 1, []
    rc = ai.get("real_conf") or 0
    if ai.get("status") == "filtered" and rc >= 0.15:
        score += 3
        why.append("filtree, mais personne ou vehicule possible (%d %%)" % round(rc * 100))
    elif ai.get("status") == "confirmed" and (ai.get("conf") or 0) < 0.5:
        score += 2
        why.append("IA peu sure (%d %%)" % round((ai.get("conf") or 0) * 100))
    g = (ai.get("guess") or [{}])[0].get("label")
    if g and ai.get("label") and g != ai.get("label") and ai.get("label") != "rien":
        score += 1
        why.append("detecteur et avis IA en desaccord")
    return {"score": score, "reasons": why or ["intrusion verifiee par l'IA"]}


def _gallery_capture_q(source, cam):
    q = {}
    if source in ("crowd", "secours"):
        q["purpose"] = source
    elif source == "traffic":
        q.update(TRAFFIC_Q)  # anciennes mesures sans purpose = trafic
    if cam:
        q["camera_id"] = cam
    return q


# Sans avis : ni sur l'etat global, ni sur une zone / un trace
_NO_VOTE = {"vote": {"$exists": False}, "$or": [{"zone_votes": {"$exists": False}}, {"zone_votes": {}}]}


@detection_bp.route("/api/detection/gallery")
def gallery():
    """?source=all|intrusions|traffic|crowd|secours (captures = les trois)
    &mode=revoir|todo|all (todo=1 : ancien nom de "todo") &camera=<id>
    &before=<iso> (todo / all) &offset=<n> (revoir) &limit.
    "revoir" : sans avis ET utiles a l'apprentissage (`review.score`,
    capture_ia.review_of), les plus utiles d'abord ; "todo" : tout ce qui
    est sans avis ; "all" : tout. Renvoie aussi `counts` {revoir, todo}."""
    db = _ensure_db()
    source = request.args.get("source", "all")
    mode = request.args.get("mode") or ("todo" if request.args.get("todo") == "1" else "all")
    if mode not in ("revoir", "todo", "all"):
        mode = "all"
    try:
        limit = max(1, min(120, int(request.args.get("limit", 60))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        limit, offset = 60, 0
    before = request.args.get("before")
    try:
        before = datetime.fromisoformat(before.replace("Z", "+00:00")) if before else None
    except ValueError:
        before = None
    cam = _oid(request.args.get("camera", ""))
    want = offset + limit if mode == "revoir" else limit
    items = []
    counts = {"revoir": 0, "todo": 0}
    if source in ("all", "intrusions"):
        q = {"photos.0": {"$exists": True}}
        if cam:
            q["camera_id"] = cam
        if before and mode != "revoir":
            q["started_at"] = {"$lt": before}
        if mode != "all":
            q["ai_vote"] = {"$exists": False}
        cur = db["hik_intrusions"].find(q, {"targets": 0}).sort("started_at", DESCENDING).limit(500 if mode == "revoir" else want)
        pq = {"photos.0": {"$exists": True}, "ai_vote": {"$exists": False}}
        if cam:
            pq["camera_id"] = cam
        pend = db["hik_intrusions"].find(pq, {"ai": 1}).limit(2000)
        rv_n = 0
        for e in pend:
            counts["todo"] += 1
            rv_n += _intrusion_review(e)["score"] > 0
        counts["revoir"] += rv_n
        for e in cur:
            rv = _intrusion_review(e)
            if mode == "revoir" and not rv["score"]:
                continue
            ph = e.get("photos") or []
            cover = ph[-1]
            items.append({
                "kind": "intrusion", "id": str(e["_id"]), "at": _iso(e.get("started_at")),
                "camera_name": e.get("camera_name", ""), "channel_name": e.get("channel_name", ""),
                "thumb": _img(cover.get("thumb")), "photo": _img(cover.get("photo")), "photos_n": len(ph),
                "rect": cover.get("rect"), "ai": _pub_ai(e), "armed": e.get("armed") is not False,
                "labeled": any(p.get("label") for p in ph), "review": rv,
            })
    if source in ("all", "captures", "traffic", "crowd", "secours"):
        base = _gallery_capture_q(source, cam)
        counts["todo"] += db["ia_capture_samples"].count_documents(dict(base, **_NO_VOTE))
        counts["revoir"] += db["ia_capture_samples"].count_documents(dict(base, **_NO_VOTE, **{"review.score": {"$gte": 1}}))
        q = dict(base)
        if mode != "all":
            q.update(_NO_VOTE)
        if mode == "revoir":
            q["review.score"] = {"$gte": 1}
            cur = db["ia_capture_samples"].find(q, {"vehicles_b": 0, "vehicles_a2": 0, "vehicles_b2": 0, "grid": 0}) \
                .sort([("review.score", DESCENDING), ("at", DESCENDING)]).limit(want + 1)  # +1 : y a-t-il une page apres ?
        else:
            if before:
                q["at"] = {"$lt": before}
            cur = db["ia_capture_samples"].find(q, {"vehicles_b": 0, "vehicles_a2": 0, "vehicles_b2": 0, "grid": 0}) \
                .sort("at", DESCENDING).limit(want)
        for s in cur:
            it = _pub_sample(s)
            it.update({"kind": "capture", "photo": _img(((s.get("photos") or [{}])[0]).get("photo")),
                       "review": s.get("review") or {"score": 0, "reasons": []}})
            if it["purpose"] == "traffic":  # avis par trace : nombre donne / a donner
                it["zones"] = [{"id": k, "name": v.get("name", ""), "kind": v.get("kind") or "trafic", "state": v.get("state"),
                                "veh": v.get("veh"), "n": v.get("n")}
                               for k, v in (s.get("zones") or {}).items() if k != "image" and v.get("kind") in (None, "trafic", "itineraire")]
                it["zone_votes"] = {k: {"state": v.get("state")} for k, v in (s.get("zone_votes") or {}).items()}
            items.append(it)
    if mode == "revoir":
        # Les plus utiles d'abord, puis les plus recentes ; page suivante par offset
        items.sort(key=lambda x: (x["review"]["score"], x["at"] or ""), reverse=True)
        page = items[offset:offset + limit]
        nxt = offset + limit if len(items) > offset + limit else None
        return jsonify({"items": page, "next": nxt, "counts": counts, "mode": mode})
    items.sort(key=lambda x: x["at"] or "", reverse=True)
    items = items[:limit]
    nxt = items[-1]["at"] if len(items) == limit else None
    return jsonify({"items": items, "next": nxt, "counts": counts, "mode": mode})
