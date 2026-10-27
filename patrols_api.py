# patrols_api.py -- routes des patrouilles programmees (patrols.py)
#
# - patrols_bp : page /patrouilles et /api/patrols/* (page d'administration
#   "patrouilles" de ADMIN_PAGE_REGISTRY : admins, accordable a un groupe),
#   CSRF conserve.
# - patrols_field_bp : routes tablette /field/patrol/* (cookie field_token),
#   exemptees de CSRF comme le blueprint field.
# Rien ne tourne en fond ici : delais et pauses sont traites par
# patrol_engine.py (tache planifiee). Voir docs/claude/patrouilles.md.
import logging
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, render_template, request

import patrols as P
from field import (PhotoUploadError, _device_photo_sub_dir, _process_and_save_photo, admin_required,
                   field_token_required)

logger = logging.getLogger("patrols_api")

patrols_bp = Blueprint("patrols", __name__)
patrols_field_bp = Blueprint("patrols_field", __name__)


def _db():
    from app import db
    return db


def _role_required(role):
    """role_required d'app.py, importe a l'appel (import circulaire)."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


def _who():
    u = getattr(request, "admin_user", None) or {}
    name = " ".join(x for x in (u.get("firstname"), u.get("lastname")) if x).strip()
    return name or u.get("email") or "?"


def _err(code, status=400, **extra):
    out = {"ok": False, "error": code}
    out.update(extra)
    return jsonify(out), status


def _asset_version():
    from flask import current_app
    v = 0
    for parts in (("js", "patrouilles.js"), ("css", "patrouilles.css"),
                  ("js", "schedule_grid.js"), ("css", "schedule_grid.css")):
        try:
            v = max(v, int(os.path.getmtime(os.path.join(current_app.static_folder, *parts))))
        except OSError:
            pass
    return v


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@patrols_bp.route("/patrouilles")
@_role_required("admin")
def patrouilles_page():
    payload = getattr(request, "user_payload", {}) or {}
    return render_template("patrouilles.html", asset_v=_asset_version(),
                           user_roles=payload.get("roles", []),
                           user_firstname=payload.get("firstname", ""),
                           user_lastname=payload.get("lastname", ""),
                           user_email=payload.get("email", ""))


def _event_names(db):
    import detection_arming as DAR
    try:
        return sorted({str(w.get("event")) for w in DAR._event_windows(db)})
    except Exception:
        return []


def _zone_labels(zones):
    return {z["id"]: z["label"] for z in zones}


def _pub_device(d, groups, now, settings):
    import field as F
    seen = P._aware(d.get("last_seen"))
    pos = d.get("last_position") or {}
    g = groups.get(d.get("beacon_group_id")) or {}
    return {
        "id": str(d["_id"]), "name": d.get("name"), "event": d.get("event"), "year": d.get("year"),
        "beacon_group_id": d.get("beacon_group_id"), "group_label": g.get("label") or d.get("beacon_group_id"),
        "status": d.get("status") or "patrouille", "declarant": bool(g.get("declarant")),
        "category": F._device_category(db=_db(), device=d),
        "recent": bool(isinstance(seen, datetime) and (now - seen).total_seconds() <= settings["seen_window_min"] * 60),
        "last_seen": P._iso(seen),
        "position": {"lat": pos.get("lat"), "lng": pos.get("lng"), "ts": P._iso(pos.get("ts"))}
        if pos.get("lat") is not None else None,
        "competence_zones": d.get("competence_zones") or [],
        "active_patrol_run": d.get("active_patrol_run"),
        "test_mode": bool(d.get("test_mode")),
        "proposal": bool(P._busy(d, now) == "proposition_en_cours"),
    }


@patrols_bp.route("/api/patrols/bootstrap")
@admin_required
def patrols_bootstrap():
    """Tout ce qu'il faut a la page : points, scenarios, zones, groupes,
    tablettes, reglages."""
    import field as F
    db = _db()
    now = P._now()
    st = P.get_settings(db)
    zones = P._zones(db)
    labels = _zone_labels(zones)
    groups = F._beacon_groups(db)
    points = [P.pub_point(p, labels) for p in db["patrol_points"].find().sort("name", 1)]
    scenarios = [P.pub_scenario(db, s) for s in db["patrol_scenarios"].find().sort("name", 1)]
    devices = [_pub_device(d, groups, now, st)
               for d in db["field_devices"].find({"revoked": {"$ne": True}}).sort("name", 1)]
    return jsonify({
        "ok": True, "settings": st, "points": points, "scenarios": scenarios, "devices": devices,
        "dominantes": [{"id": k, "label": v} for k, v in P.DOMINANTES.items()],
        # Epreuves connues (exceptions de planning calees sur une epreuve)
        "events": _event_names(db),
        "zones": [{"id": z["id"], "label": z["label"], "geometry": z["geometry"]} for z in zones],
        "groups": [{"id": gid, "label": g.get("label") or gid, "color": g.get("color") or "#6366f1",
                    "declarant": bool(g.get("declarant")), "disabled": g.get("enabled") is False}
                   for gid, g in groups.items()],
    })


@patrols_bp.route("/api/patrols/settings", methods=["PUT"])
@admin_required
def patrols_settings():
    data = request.get_json(silent=True) or {}
    db = _db()
    upd = {k: data[k] for k in P.DEFAULTS if k in data}
    upd.update(updated_at=P._now(), updated_by=_who())
    db["cockpit_settings"].update_one({"_id": P.SETTINGS_ID}, {"$set": upd}, upsert=True)
    return jsonify({"ok": True, "settings": P.get_settings(db)})


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------

@patrols_bp.route("/api/patrols/points", methods=["POST"])
@admin_required
def patrols_point_create():
    db = _db()
    p, err = P.save_point(db, request.get_json(silent=True) or {}, _who())
    if err:
        return _err(err)
    return jsonify({"ok": True, "point": P.pub_point(p, _zone_labels(P._zones(db)))})


@patrols_bp.route("/api/patrols/points/<point_id>", methods=["PUT"])
@admin_required
def patrols_point_update(point_id):
    db = _db()
    p, err = P.save_point(db, request.get_json(silent=True) or {}, _who(), point_id=point_id)
    if err:
        return _err(err, 404 if err == "not_found" else 400)
    return jsonify({"ok": True, "point": P.pub_point(p, _zone_labels(P._zones(db)))})


@patrols_bp.route("/api/patrols/points/<point_id>/active", methods=["POST"])
@admin_required
def patrols_point_active(point_id):
    active = bool((request.get_json(silent=True) or {}).get("active"))
    if not P.set_point_active(_db(), point_id, active):
        return _err("not_found", 404)
    return jsonify({"ok": True, "active": active})


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

@patrols_bp.route("/api/patrols/scenarios/<scenario_id>")
@admin_required
def patrols_scenario_get(scenario_id):
    db = _db()
    sc = db["patrol_scenarios"].find_one({"_id": P._oid(scenario_id)})
    if not sc:
        return _err("not_found", 404)
    return jsonify({"ok": True, "scenario": P.pub_scenario(db, sc, with_points=True,
                                                            zone_labels=_zone_labels(P._zones(db)))})


@patrols_bp.route("/api/patrols/scenarios", methods=["POST"])
@admin_required
def patrols_scenario_create():
    db = _db()
    sc, err = P.save_scenario(db, request.get_json(silent=True) or {}, _who())
    if err:
        return _err(err)
    return jsonify({"ok": True, "scenario": P.pub_scenario(db, sc)})


@patrols_bp.route("/api/patrols/scenarios/<scenario_id>", methods=["PUT"])
@admin_required
def patrols_scenario_update(scenario_id):
    db = _db()
    sc, err = P.save_scenario(db, request.get_json(silent=True) or {}, _who(), scenario_id=scenario_id)
    if err:
        return _err(err, 404 if err == "not_found" else 400)
    return jsonify({"ok": True, "scenario": P.pub_scenario(db, sc)})


@patrols_bp.route("/api/patrols/scenarios/<scenario_id>/archive", methods=["POST"])
@admin_required
def patrols_scenario_archive(scenario_id):
    archived = bool((request.get_json(silent=True) or {}).get("archived", True))
    r = _db()["patrol_scenarios"].update_one({"_id": P._oid(scenario_id)}, {"$set": {"archived": archived}})
    if not r.matched_count:
        return _err("not_found", 404)
    return jsonify({"ok": True, "archived": archived})


@patrols_bp.route("/api/patrols/preview", methods=["POST"])
@admin_required
def patrols_preview():
    """Trace et estimation d'un parcours pas encore enregistre (editeur)."""
    db = _db()
    data = request.get_json(silent=True) or {}
    clean, err = P._clean_scenario(db, dict(data, name=data.get("name") or "apercu"))
    if err:
        return _err(err)
    if len(clean["points"]) < 2:
        return jsonify({"ok": True, "estimate": None})
    est = P.estimate(db, clean)
    if est.get("error"):
        return _err(est["error"], 502)
    return jsonify({"ok": True, "estimate": {k: est.get(k) for k in ("distance_m", "duration_s", "polyline", "legs")}})


@patrols_bp.route("/api/patrols/optimize", methods=["POST"])
@admin_required
def patrols_optimize():
    out, err = P.optimize_order(_db(), request.get_json(silent=True) or {})
    if err:
        return _err(err, 502)
    return jsonify(dict(out, ok=True))


# ---------------------------------------------------------------------------
# Envoi et suivi
# ---------------------------------------------------------------------------

REASONS = {
    "deja_sollicitee": "deja sollicitee", "declarante": "tablette declarante",
    "epreuve_terminee": "epreuve terminee", "pas_vue_recemment": "pas vue recemment",
    "proposition_en_cours": "proposition en cours", "patrouille_en_cours": "patrouille en cours",
    "hors_zone": "zone de competence ne couvrant pas tous les points",
}


def _reason_label(code):
    if code.startswith("statut_"):
        return "statut " + code[7:].replace("_", " ")
    return REASONS.get(code, code)


@patrols_bp.route("/api/patrols/scenarios/<scenario_id>/send", methods=["POST"])
@admin_required
def patrols_send(scenario_id):
    """Envoyer maintenant a une tablette ou au premier disponible d'un groupe."""
    db = _db()
    target = (request.get_json(silent=True) or {}).get("target") or {}
    run, code, skipped = P.create_run(db, scenario_id, target, _who())
    if not run:
        return _err(code, 404 if code == "scenario_introuvable" else 400)
    out = {"ok": code == "proposed", "code": code, "run": P.pub_run(run),
           "skipped": [dict(s, label=_reason_label(s["reason"])) for s in skipped]}
    return jsonify(out), (200 if code == "proposed" else 409)


@patrols_bp.route("/api/patrols/runs")
@admin_required
def patrols_runs():
    """?scope=live : en cours et proposees + terminees du jour ; ?scope=history
    &days=N&scenario=<id> : historique avec taux de realisation."""
    db = _db()
    scope = request.args.get("scope") or "live"
    now = P._now()
    if scope == "history":
        try:
            days = max(1, min(366, int(request.args.get("days") or 30)))
        except ValueError:
            days = 30
        q = {"created_at": {"$gte": now - timedelta(days=days)}}
        if request.args.get("scenario"):
            q["scenario.id"] = request.args["scenario"]
        runs = list(db["patrol_runs"].find(q).sort("created_at", -1).limit(500))
    else:
        start = now - timedelta(hours=18)
        runs = list(db["patrol_runs"].find({"$or": [{"state": {"$in": list(P.RUN_OPEN)}},
                                                     {"created_at": {"$gte": start}}]})
                    .sort("created_at", -1).limit(200))
    devs = {str(d["_id"]): d for d in db["field_devices"].find(
        {"_id": {"$in": [P._oid(r["device_id"]) for r in runs if r.get("device_id")]}},
        {"name": 1, "last_position": 1, "status": 1})}
    out = [P.pub_run(r, devs.get(r.get("device_id"))) for r in runs]
    stats = {}
    for r in runs:
        sid = (r.get("scenario") or {}).get("id")
        s = stats.setdefault(sid, {"scenario_id": sid, "name": (r.get("scenario") or {}).get("name"),
                                   "runs": 0, "taken": 0, "points": 0, "done": 0, "not_taken": 0})
        s["runs"] += 1
        if r.get("started_at"):
            s["taken"] += 1
            c = P._counts(r.get("steps") or [])
            s["points"] += len(r.get("steps") or [])
            s["done"] += c["done"]
        elif r.get("state") in ("refused", "expired"):
            s["not_taken"] += 1
    return jsonify({"ok": True, "runs": out, "stats": sorted(stats.values(), key=lambda x: -x["runs"])})


@patrols_bp.route("/api/patrols/runs/<run_id>/cancel", methods=["POST"])
@admin_required
def patrols_run_cancel(run_id):
    reason = ((request.get_json(silent=True) or {}).get("reason") or "").strip()
    ok, code = P.finish(_db(), run_id, reason=reason or "annulee par le PC Org", by=_who())
    if not ok:
        return _err(code, 404 if code == "not_found" else 400)
    return jsonify({"ok": True, "code": code})


# ---------------------------------------------------------------------------
# Planifications
# ---------------------------------------------------------------------------

PLAN_ERRORS = {"nom_obligatoire", "scenario_introuvable", "cible_invalide", "date_invalide"}


@patrols_bp.route("/api/patrols/plans")
@admin_required
def patrols_plans():
    db = _db()
    scs = {str(s["_id"]): s for s in db["patrol_scenarios"].find({}, {"name": 1, "color": 1})}
    now = P._now()
    plans = [P.pub_plan(db, p, scs, now) for p in db["patrol_plans"].find().sort("name", 1)]
    return jsonify({"ok": True, "plans": plans})


@patrols_bp.route("/api/patrols/plans", methods=["POST"])
@admin_required
def patrols_plan_create():
    db = _db()
    plan, err = P.save_plan(db, request.get_json(silent=True) or {}, _who())
    if err:
        return _err(err)
    return jsonify({"ok": True, "plan": P.pub_plan(db, plan)})


@patrols_bp.route("/api/patrols/plans/<plan_id>", methods=["PUT"])
@admin_required
def patrols_plan_update(plan_id):
    db = _db()
    plan, err = P.save_plan(db, request.get_json(silent=True) or {}, _who(), plan_id=plan_id)
    if err:
        return _err(err, 404 if err == "not_found" else 400)
    return jsonify({"ok": True, "plan": P.pub_plan(db, plan)})


@patrols_bp.route("/api/patrols/plans/<plan_id>", methods=["DELETE"])
@admin_required
def patrols_plan_delete(plan_id):
    r = _db()["patrol_plans"].delete_one({"_id": P._oid(plan_id)})
    if not r.deleted_count:
        return _err("not_found", 404)
    logger.info("Patrouilles : planification %s supprimee par %s", plan_id, _who())
    return jsonify({"ok": True})


@patrols_bp.route("/api/patrols/plans/<plan_id>/enabled", methods=["POST"])
@admin_required
def patrols_plan_enabled(plan_id):
    db = _db()
    enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
    r = db["patrol_plans"].update_one({"_id": P._oid(plan_id)},
                                      {"$set": {"enabled": enabled, "updated_at": P._now(), "updated_by": _who()}})
    if not r.matched_count:
        return _err("not_found", 404)
    return jsonify({"ok": True, "plan": P.pub_plan(db, db["patrol_plans"].find_one({"_id": P._oid(plan_id)}))})


@patrols_bp.route("/api/patrols/plans/<plan_id>/override", methods=["POST"])
@admin_required
def patrols_plan_override(plan_id):
    db = _db()
    data = request.get_json(silent=True) or {}
    if not P.set_plan_override(db, plan_id, data.get("state"), data.get("hours"), _who()):
        return _err("not_found", 404)
    return jsonify({"ok": True, "plan": P.pub_plan(db, db["patrol_plans"].find_one({"_id": P._oid(plan_id)}))})


@patrols_bp.route("/api/patrols/plans/preview", methods=["POST"])
@admin_required
def patrols_plan_preview():
    """Calendrier du mois et prochains lancements d'une planification en
    cours d'edition (rien n'est enregistre)."""
    import detection_arming as DAR
    db = _db()
    data = request.get_json(silent=True) or {}
    clean, err = P._clean_plan(db, dict(data, name=data.get("name") or "apercu"))
    if err and err not in ("scenario_introuvable", "cible_invalide"):
        return _err(err)
    if err:     # apercu possible avant d'avoir choisi scenario et cible
        clean = {"arming": DAR.normalize(data.get("arming")), "launch": data.get("launch") or {},
                 "valid_from": data.get("valid_from") or None, "valid_until": data.get("valid_until") or None}
    clean["enabled"] = True
    now = P._now()
    month = str(request.args.get("month") or now.astimezone(P.PARIS).strftime("%Y-%m"))
    try:
        y, m = int(month[:4]), int(month[5:7])
    except ValueError:
        return _err("mois_invalide")
    days = DAR.month_view({"arming": clean["arming"]}, y, m, db)
    for d in days:
        if not P._in_validity(clean, datetime.fromisoformat(d["date"] + "T12:00:00").replace(tzinfo=P.PARIS)):
            d.update(spans=[], active_quarters=0, label="Hors periode de validite", source="validity", kind="off")
    return jsonify({"ok": True, "month": "%04d-%02d" % (y, m), "days": days,
                    "next": [P._iso(t) for t in P.next_launches(db, clean, now, limit=10)]})


@patrols_bp.route("/api/patrols/devices/<device_id>/zones", methods=["POST"])
@admin_required
def patrols_device_zones(device_id):
    """Zones de competence d'une tablette (ids de la couche de zones ; vide = partout)."""
    db = _db()
    wanted = [str(z) for z in (request.get_json(silent=True) or {}).get("zones") or []]
    known = {z["id"] for z in P._zones(db)}
    zones = [z for z in wanted if z in known]
    r = db["field_devices"].update_one({"_id": P._oid(device_id)}, {"$set": {"competence_zones": zones}})
    if not r.matched_count:
        return _err("not_found", 404)
    logger.info("Patrouilles : zones de %s = %s par %s", device_id, zones, _who())
    return jsonify({"ok": True, "zones": zones})


# ---------------------------------------------------------------------------
# Tablette
# ---------------------------------------------------------------------------

@patrols_field_bp.route("/field/patrol/<run_id>/accept", methods=["POST"])
@field_token_required
def field_patrol_accept(run_id):
    ok, code = P.accept(_db(), run_id, request.device)
    return jsonify({"ok": ok, "error": None if ok else code, "code": code}), (200 if ok else 409)


@patrols_field_bp.route("/field/patrol/<run_id>/refuse", methods=["POST"])
@field_token_required
def field_patrol_refuse(run_id):
    reason = (request.get_json(silent=True) or {}).get("reason")
    P.refuse(_db(), run_id, request.device["_id"], reason=reason)
    return jsonify({"ok": True})


def _at_from_ms(v):
    try:
        return datetime.fromtimestamp(float(v) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


@patrols_field_bp.route("/field/patrol/<run_id>/step", methods=["POST"])
@field_token_required
def field_patrol_step(run_id):
    """Point fait / passe. JSON, ou multipart avec `photo` (point a photo
    exigee). Idempotent par (patrouille, point) : rejouable par la file hors
    ligne."""
    db = _db()
    device = request.device
    if request.content_type and "multipart" in request.content_type:
        data = request.form.to_dict()
        pf = request.files.get("photo")
    else:
        data = request.get_json(silent=True) or {}
        pf = None
    photo = None
    if pf and pf.filename:
        run = db["patrol_runs"].find_one({"_id": run_id}, {"device_id": 1, "steps": 1})
        st = next((s for s in (run or {}).get("steps") or [] if s.get("point_id") == data.get("point_id")), None)
        if run and run.get("device_id") == str(device["_id"]) and st and st.get("state") == "pending":
            try:
                url, thumb = _process_and_save_photo(pf, _device_photo_sub_dir(db, device))
            except PhotoUploadError as e:
                return _err(e.code, e.status)
            photo = {"photo": url, "thumb": thumb}
    method = data.get("method") if data.get("method") in ("gps", "manual") else "manual"
    ok, code, run = P.step(db, run_id, device, data.get("point_id"), data.get("action") or "done",
                           method=method, comment=data.get("comment"), photo=photo,
                           at=_at_from_ms(data.get("at")))
    if not ok:
        return _err(code, 403 if code == "not_assigned" else 409)
    return jsonify({"ok": True, "code": code, "state": (run or {}).get("state")})


@patrols_field_bp.route("/field/patrol/<run_id>/finish", methods=["POST"])
@field_token_required
def field_patrol_finish(run_id):
    data = request.get_json(silent=True) or {}
    abandon = bool(data.get("abandon"))
    reason = (data.get("reason") or "").strip()
    if abandon and len(reason) < 3:
        return _err("motif_obligatoire")
    ok, code = P.finish(_db(), run_id, request.device, reason=reason, abandon=abandon)
    if not ok:
        return _err(code, 403 if code == "not_assigned" else 409)
    return jsonify({"ok": True, "code": code})


@patrols_field_bp.route("/field/patrol/<run_id>/route", methods=["POST"])
@field_token_required
def field_patrol_route(run_id):
    """Itineraire de la position de la tablette au prochain point non fait
    (ou au point demande), dans le mode du scenario : a pied, ou vehicule
    avec la piste si le groupe de la tablette y a droit."""
    import field as F
    db = _db()
    device = request.device
    data = request.get_json(silent=True) or {}
    run = db["patrol_runs"].find_one({"_id": run_id})
    if not run or run.get("device_id") != str(device["_id"]):
        return _err("not_assigned", 403)
    try:
        src = (float(data["from"][0]), float(data["from"][1]))
    except (KeyError, TypeError, ValueError, IndexError):
        return _err("invalid_coordinates")
    pts = {p["point_id"]: p for p in (run.get("scenario") or {}).get("points") or []}
    pid = data.get("point_id") or next(
        (s["point_id"] for s in run.get("steps") or [] if s.get("state") == "pending"), None)
    pt = pts.get(pid)
    if not pt:
        return _err("aucun_point")
    travel = (run.get("scenario") or {}).get("travel") or "vehicle"
    mode = "normal_piste" if (travel == "vehicle" and F._device_route_piste(db, device)) else "normal"
    out, err = P.route_through([src, (pt["lat"], pt["lng"])], travel, mode=mode)
    if err:
        return _err(err, 502)
    return jsonify({"ok": True, "point_id": pid, "travel": travel, "distance_m": out["distance_m"],
                    "duration_s": out["duration_s"], "polyline": out["polyline"]})
