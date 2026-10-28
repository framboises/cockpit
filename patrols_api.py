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
                  ("js", "schedule_grid.js"), ("css", "schedule_grid.css"), ("js", "patrol_fiche_map.js")):
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
    try:        # points issus des couches Looker : ils suivent Looker
        P.sync_layer_points(db)
    except Exception as e:
        logger.warning("Patrouilles : synchro Looker en echec (%s)", e)
    zones = P._zones(db)
    labels = _zone_labels(zones)
    groups = F._beacon_groups(db)
    points = [P.pub_point(p, labels) for p in db["patrol_points"].find({"merged_into": {"$exists": False}}).sort("name", 1)]
    scenarios = [P.pub_scenario(db, s) for s in db["patrol_scenarios"].find().sort("name", 1)]
    devices = [_pub_device(d, groups, now, st)
               for d in db["field_devices"].find({"revoked": {"$ne": True}}).sort("name", 1)]
    return jsonify({
        "ok": True, "settings": st, "points": points, "scenarios": scenarios, "devices": devices,
        "dominantes": [{"id": k, "label": v} for k, v in P.DOMINANTES.items()],
        # Epreuves connues (exceptions de planning calees sur une epreuve)
        "events": _event_names(db),
        # Couches Looker (import de points), celles retenues dans les reglages
        "layers": P.available_layers(db),
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
    if isinstance(data.get("layers"), list):
        known = {d["collection"] for d in P.available_layers(db)}
        upd["layers"] = [str(x) for x in data["layers"] if str(x) in known]
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


@patrols_bp.route("/api/patrols/layers/<collection>/items")
@admin_required
def patrols_layer_items(collection):
    """Objets d'une couche Looker, et le point deja cree pour chacun."""
    db = _db()
    items = P.layer_items(db, collection)
    if items is None:
        return _err("couche_inconnue", 404)
    # Point fusionne : c'est le point garde qui le represente
    done = {(p.get("layer_ref") or {}).get("fid"): {"id": p.get("merged_into") or str(p["_id"]),
                                                     "active": bool(p.get("merged_into")) or p.get("active", True) is not False}
            for p in db["patrol_points"].find({"layer_ref.collection": collection},
                                              {"layer_ref": 1, "active": 1, "merged_into": 1})}
    for it in items:
        it["point"] = done.get(it["fid"])
    return jsonify({"ok": True, "items": items})


@patrols_bp.route("/api/patrols/points/import", methods=["POST"])
@admin_required
def patrols_points_import():
    """Cree un point par objet choisi d'une couche Looker (ou reactive celui
    qui existe). Retourne les points dans l'ordre demande."""
    db = _db()
    data = request.get_json(silent=True) or {}
    ids, err = P.import_points(db, data.get("collection"), data.get("fids"), _who())
    if err:
        return _err(err)
    labels = _zone_labels(P._zones(db))
    pts = {str(p["_id"]): p for p in db["patrol_points"].find({"_id": {"$in": [P._oid(i) for i in ids]}})}
    return jsonify({"ok": True, "ids": ids, "points": [P.pub_point(pts[i], labels) for i in ids if i in pts]})


@patrols_bp.route("/api/patrols/parcours", methods=["POST"])
@patrols_bp.route("/api/patrols/parcours/<point_id>", methods=["PUT"])
@admin_required
def patrols_parcours_save(point_id=None):
    """Parcours impose (tour de piste) : passages dans l'ordre de roulage,
    sens, tour complet ; trace recalcule en mode piste force."""
    db = _db()
    p, err = P.save_parcours(db, request.get_json(silent=True) or {}, _who(), point_id=point_id)
    if err:
        return _err(err, 404 if err == "not_found" else 400)
    scenarios = [P.pub_scenario(db, s) for s in db["patrol_scenarios"].find().sort("name", 1)]
    return jsonify({"ok": True, "point": P.pub_point(p, _zone_labels(P._zones(db))), "scenarios": scenarios})


@patrols_bp.route("/api/patrols/parcours/preview", methods=["POST"])
@admin_required
def patrols_parcours_preview():
    """Trace d'un parcours en cours de saisie (rien n'est enregistre)."""
    data = request.get_json(silent=True) or {}
    clean, err = P._clean_parcours(dict(data, name=data.get("name") or "apercu"), P.get_settings(_db()))
    if err:
        return _err(err)
    tr = P.parcours_track(clean)
    out = {k: tr.get(k) for k in ("distance_m", "duration_s", "polyline", "uses_raceway", "error", "mode")}
    out["checks"] = len(tr.get("checks") or [])
    return jsonify({"ok": True, "track": out})


@patrols_bp.route("/api/patrols/points/<point_id>/merge", methods=["POST"])
@admin_required
def patrols_point_merge(point_id):
    """Fusionne le point `other` dans `point_id` (qui reste affiche) ; le nom
    de l'autre devient un de ses autres noms, les scenarios suivent."""
    db = _db()
    out, err = P.merge_points(db, point_id, (request.get_json(silent=True) or {}).get("other"), _who())
    if err:
        return _err(err, 404 if err == "not_found" else 400)
    labels = _zone_labels(P._zones(db))
    scenarios = [P.pub_scenario(db, s) for s in db["patrol_scenarios"].find().sort("name", 1)]
    return jsonify({"ok": True, "point": P.pub_point(out["point"], labels), "scenarios_updated": out["scenarios"],
                    "scenarios": scenarios})


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
    "sans_acces_piste": "groupe sans acces piste (le scenario contient un tour de piste)",
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


# ---------------------------------------------------------------------------
# Propositions d'apres l'historique (patrols_propose.py)
# ---------------------------------------------------------------------------

def _is_real_admin():
    """Admin Cockpit ou super admin : une page accordee a un groupe ne suffit
    pas pour l'IA (couts, donnees envoyees a Anthropic)."""
    u = getattr(request, "admin_user", None) or {}
    return bool(u.get("is_super_admin") or u.get("app_role") == "admin")


@patrols_bp.route("/api/patrols/propose/events")
@admin_required
def patrols_propose_events():
    import patrols_propose as PP
    source = request.args.get("source") if request.args.get("source") in PP.SOURCE_PREFIX else "pcs"
    return jsonify({"ok": True, "events": PP.events_with_history(_db(), source), "ai": _is_real_admin()})


@patrols_bp.route("/api/patrols/propose", methods=["POST"])
@admin_required
def patrols_propose():
    """Proposition d'une ronde : dominante, duree, deplacement, evenement,
    tranche horaire facultative ; `ai: true` = scores revus par Claude
    (admins seulement)."""
    import patrols_propose as PP
    db = _db()
    d = request.get_json(silent=True) or {}
    event = str(d.get("event") or "").strip()
    if not event:
        return _err("evenement_obligatoire")
    hour = None
    if d.get("hour") not in (None, ""):
        try:
            hh, mm = str(d["hour"]).split(":")[:2]
            hour = int(hh) + int(mm) / 60.0
        except (ValueError, TypeError):
            hour = None
    source = d.get("source") if d.get("source") in PP.SOURCE_PREFIX else "pcs"
    dominante = d.get("dominante") or "generale"
    travel = d.get("travel") if d.get("travel") in PP.TRAVEL else "pedestrian"
    # Avis de l'IA : paye une fois (`ai: true`), garde en base et reutilise
    # gratuitement (`use_ai: true`) pour la meme source / evenement /
    # dominante / deplacement, meme apres un changement de duree ou de lieux.
    review = None
    if d.get("ai"):
        if not _is_real_admin():
            return _err("admin_requis", 403)
        try:
            import pcorg_summary as PS
            scores, reasons, comment, top = PP.ai_rescore(db, event, dominante, travel, hour, source=source)
            review = PP.save_review(db, source, event, dominante, travel, top, scores, reasons, comment, _who(),
                                    PS.CLAUDE_MODEL)
        except Exception as e:
            logger.warning("Patrouilles : affinage IA en echec (%s)", e)
            return _err("ia_indisponible", 502, detail=str(e)[:200])
    else:
        stored = PP.get_review(db, source, event, dominante, travel)
        if stored and d.get("use_ai"):
            review = stored
    out = PP.propose(db, dominante, d.get("duration_min"), travel, event, loop=bool(d.get("loop")), hour=hour,
                     ai_review=(review or {}).get("foyers"), source=source)
    shown = review or PP.get_review(db, source, event, dominante, travel)
    out["ai_review"] = {"at": P._iso(shown["at"]), "by": shown.get("by"), "n": len(shown.get("foyers") or []),
                        "used": bool(review)} if shown else None
    out["ai_comment"] = (review or {}).get("comment")
    out["ok"] = True
    return jsonify(out)


@patrols_bp.route("/api/patrols/propose/create", methods=["POST"])
@admin_required
def patrols_propose_create():
    import patrols_propose as PP
    db = _db()
    sc, err = PP.create_from_proposal(db, request.get_json(silent=True) or {}, _who())
    if err:
        return _err(err)
    return jsonify({"ok": True, "scenario": P.pub_scenario(db, sc)})


@patrols_bp.route("/api/pcorg-patrol/<run_id>")
@_role_required("user")
def pcorg_patrol_view(run_id):
    """Patrouille d'une fiche de main courante (lecture, tout operateur) :
    points, etat, trajet estime, position de la tablette. Sert a la carte de
    la fiche (pcorg.js) et a la fenetre de fiche de la page Patrouilles."""
    db = _db()
    run = db["patrol_runs"].find_one({"_id": run_id})
    if not run:
        return _err("not_found", 404)
    dev = db["field_devices"].find_one({"_id": P._oid(run.get("device_id"))}, {"name": 1, "last_position": 1, "status": 1}) \
        if run.get("device_id") else None
    return jsonify({"ok": True, "run": P.pub_run(run, dev)})


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
    gates = data.get("gates") if isinstance(data.get("gates"), list) else None
    vias = data.get("vias")
    if isinstance(vias, str):           # multipart (point a photo) : "0,2"
        vias = [x for x in vias.split(",") if x.strip()]
    ok, code, run = P.step(db, run_id, device, data.get("point_id"), data.get("action") or "done",
                           method=method, comment=data.get("comment"), photo=photo,
                           at=_at_from_ms(data.get("at")), gates=gates,
                           vias=vias if isinstance(vias, list) else None)
    if not ok:
        return _err(code, 403 if code == "not_assigned" else 409)
    return jsonify({"ok": True, "code": code, "state": (run or {}).get("state")})


@patrols_field_bp.route("/field/patrol/<run_id>/speeding", methods=["POST"])
@field_token_required
def field_patrol_speeding(run_id):
    """Depassement de la vitesse maximale (episode termine, start / end en ms).
    Idempotent par `id` : rejouable par la file hors ligne."""
    data = request.get_json(silent=True) or {}
    data = dict(data, start=_at_from_ms(data.get("start")), end=_at_from_ms(data.get("end")))
    ok, code = P.record_speeding(_db(), run_id, request.device, data)
    if not ok:
        return _err(code, 403 if code == "not_assigned" else 400)
    return jsonify({"ok": True, "code": code})


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


# ---------------------------------------------------------------------------
# Outils de test de la tablette (mode test de Field dispatch uniquement)
# ---------------------------------------------------------------------------
# Panneau "Outils de test" de field.js : etat vu par le serveur, raccourcis
# (se proposer une patrouille, faire expirer, passer le moteur pour SA
# patrouille, annuler) et rapports de bug (`field_test_reports`, 30 jours),
# lus ensuite par les developpeurs. Tout est refuse hors mode test.

TEST_REPORTS_TTL_DAYS = 30


def _test_device():
    d = request.device
    fresh = _db()["field_devices"].find_one({"_id": d["_id"]}) or d
    return fresh if fresh.get("test_mode") else None


def _plain(v):
    """Document Mongo -> JSON (dates ISO, ObjectId en texte)."""
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, datetime):
        return P._iso(v)
    if type(v).__name__ == "ObjectId":
        return str(v)
    return v


@patrols_field_bp.route("/field/test/state", methods=["GET"])
@field_token_required
def field_test_state():
    """Ce que voit le serveur : tablette, patrouille, propositions, moteur."""
    d = _test_device()
    if not d:
        return _err("mode_test_requis", 403)
    db = _db()
    now = P._now()
    run = db["patrol_runs"].find_one({"_id": d.get("active_patrol_run")}) if d.get("active_patrol_run") else None
    prop_run = None
    pp = d.get("patrol_proposal") or {}
    if pp.get("run_id"):
        prop_run = db["patrol_runs"].find_one({"_id": pp["run_id"]}, {"state": 1, "current": 1, "attempts": 1, "scenario.name": 1})
    lock = db["cockpit_settings"].find_one({"_id": "patrol_engine_lock"}) or {}
    last = P._aware(lock.get("at"))
    import field as F
    return jsonify({
        "ok": True, "now": P._iso(now),
        "device": _plain({k: d.get(k) for k in (
            "_id", "name", "event", "year", "beacon_group_id", "category", "status", "status_since",
            "active_fiche_id", "active_patrol_run", "patrol_proposal", "pending_proposal", "last_seen",
            "last_position", "competence_zones", "test_mode", "metiers")}),
        "category_effective": F._device_category(db, d),
        "route_piste": F._device_route_piste(db, d),
        "run": _plain({k: (run or {}).get(k) for k in ("_id", "state", "fiche_id", "started_at", "pauses",
                                                     "attempts", "current", "end_reason")}) if run else None,
        "run_steps": [{"name": s.get("name"), "state": s.get("state"), "method": s.get("method"),
                       "at": P._iso(s.get("at"))} for s in (run or {}).get("steps") or []],
        "proposal_run": _plain(prop_run) if prop_run else None,
        "engine": {"last_at": P._iso(last),
                   "age_s": round((now - last).total_seconds()) if isinstance(last, datetime) else None,
                   "alive": bool(isinstance(last, datetime) and (now - last).total_seconds() < 90)},
        "settings": P.get_settings(db),
    })


@patrols_field_bp.route("/field/test/scenarios", methods=["GET"])
@field_token_required
def field_test_scenarios():
    if not _test_device():
        return _err("mode_test_requis", 403)
    db = _db()
    out = [{"id": str(s["_id"]), "name": s.get("name"), "points": len(s.get("points") or []),
            "travel": s.get("travel")} for s in db["patrol_scenarios"].find({"archived": {"$ne": True}}).sort("name", 1)]
    return jsonify({"ok": True, "scenarios": out})


_PLACES_CACHE = {"at": 0.0, "items": []}
_PLACES_TTL_S = 300


def _places_index(db):
    """Lieux cherchables par les outils de test : objets de toutes les
    couches Looker et points de patrouille (cache 5 min)."""
    import time
    now = time.monotonic()
    if _PLACES_CACHE["items"] and now - _PLACES_CACHE["at"] < _PLACES_TTL_S:
        return _PLACES_CACHE["items"]
    out = []
    for layer in P.available_layers(db):
        # Riverains / cadastre : noms de particuliers, jamais sur une tablette
        if layer["collection"].lower() in ("riverains", "cadastre"):
            continue
        try:
            for it in P.layer_items(db, layer["collection"]) or []:
                out.append({"name": it["name"], "where": layer["label"], "lat": it["lat"], "lng": it["lng"]})
        except Exception as e:
            logger.debug("lieux %s : %s", layer["collection"], e)
    for p in db["patrol_points"].find({"active": {"$ne": False}}, {"name": 1, "lat": 1, "lng": 1}):
        out.append({"name": p.get("name") or "?", "where": "Point de patrouille", "lat": p["lat"], "lng": p["lng"]})
    _PLACES_CACHE.update(at=now, items=out)
    return out


@patrols_field_bp.route("/field/test/places", methods=["GET"])
@field_token_required
def field_test_places():
    """Recherche de lieux pour se placer (position simulee) : couches
    Looker, points de patrouille et case du carroyage (ex. AJ17)."""
    if not _test_device():
        return _err("mode_test_requis", 403)
    import unicodedata

    def norm(s):
        s = unicodedata.normalize("NFKD", str(s or ""))
        return "".join(c for c in s if not unicodedata.combining(c)).lower()

    q = norm(request.args.get("q")).strip()
    if len(q) < 2:
        return jsonify({"ok": True, "places": []})
    db = _db()
    out = []
    g = db["grid_ref"].find_one({"grid_ref": q.upper().replace(" ", "")}, {"latitude": 1, "longitude": 1, "grid_ref": 1})
    if g and g.get("latitude") is not None:
        out.append({"name": g["grid_ref"], "where": "Carroyage", "lat": float(g["latitude"]), "lng": float(g["longitude"])})
    words = q.split()
    for it in _places_index(db):
        hay = norm(it["name"] + " " + it["where"])
        if all(w in hay for w in words):
            out.append(it)
            if len(out) >= 40:
                break
    return jsonify({"ok": True, "places": out})


@patrols_field_bp.route("/field/test/send", methods=["POST"])
@field_token_required
def field_test_send():
    """Se proposer une patrouille a soi-meme (cible = cette tablette)."""
    d = _test_device()
    if not d:
        return _err("mode_test_requis", 403)
    sid = (request.get_json(silent=True) or {}).get("scenario_id")
    run, code, skipped = P.create_run(_db(), sid, {"kind": "device", "device_id": str(d["_id"])},
                                      "Test tablette " + (d.get("name") or "?"))
    if not run:
        return _err(code)
    return jsonify({"ok": code == "proposed", "code": code, "run_id": run["_id"],
                    "skipped": [dict(s, label=_reason_label(s["reason"])) for s in skipped]})


@patrols_field_bp.route("/field/test/expire", methods=["POST"])
@field_token_required
def field_test_expire():
    """Fait expirer tout de suite la proposition de patrouille en attente
    (comme un silence de l'agent : tablette suivante ou non prise)."""
    d = _test_device()
    if not d:
        return _err("mode_test_requis", 403)
    rid = (d.get("patrol_proposal") or {}).get("run_id")
    if not rid:
        return _err("aucune_proposition")
    P.refuse(_db(), rid, d["_id"], answer="timeout")
    return jsonify({"ok": True, "run_id": rid})


@patrols_field_bp.route("/field/test/engine", methods=["POST"])
@field_token_required
def field_test_engine():
    """Un passage du moteur limite a CETTE tablette (proposition expiree,
    pause / reprise de sa patrouille) : utile tant que la tache planifiee
    n'est pas creee. Rien d'autre n'est traite (ni planifications, ni autres
    tablettes)."""
    d = _test_device()
    if not d:
        return _err("mode_test_requis", 403)
    db = _db()
    now = P._now()
    done = []
    pp = d.get("patrol_proposal") or {}
    exp = P._aware(pp.get("expires_at"))
    if pp.get("run_id") and isinstance(exp, datetime) and exp + timedelta(seconds=P.ANSWER_GRACE_S) < now:
        P.refuse(db, pp["run_id"], d["_id"], now=now, answer="timeout")
        done.append("proposition expiree traitee")
    if d.get("active_patrol_run"):
        run = db["patrol_runs"].find_one({"_id": d["active_patrol_run"]})
        before = (run or {}).get("state")
        after = (P.sync_pause(db, run, now=now) or {}).get("state")
        done.append("patrouille %s -> %s" % (before, after))
    return jsonify({"ok": True, "done": done or ["rien a faire"]})


@patrols_field_bp.route("/field/test/cancel", methods=["POST"])
@field_token_required
def field_test_cancel():
    d = _test_device()
    if not d:
        return _err("mode_test_requis", 403)
    db = _db()
    rid = d.get("active_patrol_run") or (d.get("patrol_proposal") or {}).get("run_id")
    if not rid:
        return _err("aucune_patrouille")
    ok, code = P.finish(db, rid, reason="annulee depuis les outils de test", by="Test " + (d.get("name") or "?"))
    return jsonify({"ok": ok, "code": code})


@patrols_field_bp.route("/field/test/report", methods=["POST"])
@field_token_required
def field_test_report():
    """Rapport de bug : note, journal de la tablette et etat complet."""
    d = _test_device()
    if not d:
        return _err("mode_test_requis", 403)
    data = request.get_json(silent=True) or {}
    db = _db()
    now = P._now()
    log = data.get("log") if isinstance(data.get("log"), list) else []
    doc = {
        "device_id": str(d["_id"]), "device_name": d.get("name"), "at": now,
        "purge_at": now + timedelta(days=TEST_REPORTS_TTL_DAYS),
        "note": str(data.get("note") or "")[:2000], "log": log[-400:],
        "client": data.get("client") if isinstance(data.get("client"), dict) else {},
        "server": {"device": _plain(d), "run": _plain(db["patrol_runs"].find_one({"_id": d.get("active_patrol_run")}))
                   if d.get("active_patrol_run") else None},
        "user_agent": request.headers.get("User-Agent", "")[:300],
    }
    try:
        db["field_test_reports"].create_index("purge_at", expireAfterSeconds=0, name="purge_ttl")
    except Exception:
        pass
    res = db["field_test_reports"].insert_one(doc)
    logger.info("Rapport de test %s de %s : %s", res.inserted_id, d.get("name"), doc["note"][:120])
    return jsonify({"ok": True, "id": str(res.inserted_id)})


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
    # Points de passage du troncon pas encore franchis (liste de la tablette)
    left = data.get("vias")
    vias = [(v["lat"], v["lng"]) for v in pt.get("vias") or []
            if not isinstance(left, list) or v.get("i") in left]
    target = (pt["lat"], pt["lng"])
    back = pt.get("back_to_vehicle")
    if pt.get("on_foot"):
        out, err = P.route_through([src] + vias + [target], "pedestrian",
                                   kinds=["break"] + ["through"] * len(vias) + ["break"])
        travel = "pedestrian"
    elif back and travel == "vehicle" and P.haversine_m(src[0], src[1], back["lat"], back["lng"]) > 60:
        # Fin d'un passage a pied : retour au vehicule, puis en route
        walk, err = P.route_through([src, (back["lat"], back["lng"])], "pedestrian")
        drive, err2 = (P.route_through([(back["lat"], back["lng"])] + vias + [target], travel, mode=mode,
                                       kinds=["break"] + ["through"] * len(vias) + ["break"])
                       if not err else (None, None))
        err = err or err2
        if not err:
            import routing
            line = routing._decode_polyline6(walk["polyline"]) + routing._decode_polyline6(drive["polyline"])[1:]
            out = {"distance_m": walk["distance_m"] + drive["distance_m"],
                   "duration_s": walk["duration_s"] + drive["duration_s"], "polyline": routing._encode_polyline6(line)}
    else:
        out, err = P.route_through([src] + vias + [target], travel, mode=mode,
                                   kinds=["break"] + ["through"] * len(vias) + ["break"])
    if err:
        return _err(err, 502)
    return jsonify({"ok": True, "point_id": pid, "travel": travel, "distance_m": out["distance_m"],
                    "duration_s": out["duration_s"], "polyline": out["polyline"]})
