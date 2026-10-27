# gravite_fiches.py -- suivi et controle de la gravite evaluee des fiches
#
# Page /admin/gravite-fiches (page d'administration "gravite_fiches" de
# ADMIN_PAGE_REGISTRY : admins, accordable explicitement a un groupe).
# Lit ce que produit pcorg_digest.py (lance hors de Cockpit) : avancement du
# traitement en cours, lots de 50, et controle par un admin fiche par fiche
# (juste / trop haut / trop bas, resume inexact). Une gravite validee prime
# partout et fige la fiche (jamais reevaluee). Voir docs/claude/patrouilles.md.
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, render_template, request

import pcorg_digest as PD
from field import admin_required

gravite_fiches_bp = Blueprint("gravite_fiches", __name__)
logger = logging.getLogger("gravite_fiches")

VERDICTS = ("ok", "trop_haut", "trop_bas")   # jugement porte sur la gravite du modele
FICHE_FIELDS = {"text": 1, "comment": 1, "category": 1, "niveau_urgence": 1,
                "event": 1, "year": 1, "date_local": 1, "time_local": 1}


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


def _iso(v):
    if not isinstance(v, datetime):
        return v
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.isoformat()


def _pub_digest(d, fiche=None):
    out = {k: d.get(k) for k in ("gravite", "justification", "type", "victimes", "moyens_engages",
                                  "issue", "resume", "error", "attempts", "batch", "prompt_version",
                                  "event", "year", "category", "has_gps")}
    out["id"] = d["_id"]
    out["at"] = _iso(d.get("at"))
    out["ms"] = (d.get("meta") or {}).get("ms")
    rv = d.get("review")
    if rv:
        rv = dict(rv, at=_iso(rv.get("at")))
    out["review"] = rv
    if fiche is not None:
        out["fiche"] = {k: fiche.get(k) for k in FICHE_FIELDS}
    return out


def _with_fiches(digests):
    ids = [d["_id"] for d in digests]
    fiches = {f["_id"]: f for f in _db()["pcorg"].find({"_id": {"$in": ids}}, FICHE_FIELDS)}
    return [_pub_digest(d, fiches.get(d["_id"], {})) for d in digests]


@gravite_fiches_bp.route("/admin/gravite-fiches")
@_role_required("admin")
def gravite_fiches_page():
    payload = getattr(request, "user_payload", {}) or {}
    return render_template("gravite_fiches.html",
                           user_roles=payload.get("roles", []),
                           user_firstname=payload.get("firstname", ""),
                           user_lastname=payload.get("lastname", ""),
                           user_email=payload.get("email", ""),
                           prompt_version=PD.PROMPT_VERSION, model=PD.OLLAMA_MODEL,
                           batch_size=PD.BATCH_SIZE)


@gravite_fiches_bp.route("/api/gravite-fiches/summary")
@admin_required
def gravite_fiches_summary():
    db = _db()
    col = db[PD.COLL]
    cur = {"prompt_version": PD.PROMPT_VERSION, "error": None}
    total = db["pcorg"].estimated_document_count()
    total_gps = db["pcorg"].count_documents({"gps": {"$ne": None}})
    structured = col.count_documents(cur)
    structured_gps = col.count_documents(dict(cur, has_gps=True))
    errors = col.count_documents({"error": {"$ne": None}})
    abandoned = col.count_documents({"error": {"$ne": None}, "attempts": {"$gte": PD.MAX_ATTEMPTS}})

    by_grav = {str(r["_id"]): r["n"] for r in col.aggregate([
        {"$match": cur}, {"$group": {"_id": "$gravite", "n": {"$sum": 1}}}])}

    batches = list(col.aggregate([
        {"$match": {"batch": {"$ne": None}}},
        {"$group": {"_id": "$batch", "n": {"$sum": 1},
                    "errors": {"$sum": {"$cond": [{"$ne": ["$error", None]}, 1, 0]}},
                    "reviewed": {"$sum": {"$cond": [{"$ifNull": ["$review", False]}, 1, 0]}},
                    "ok": {"$sum": {"$cond": [{"$eq": ["$review.verdict", "ok"]}, 1, 0]}},
                    "first": {"$min": "$at"}, "last": {"$max": "$at"},
                    "prompt_version": {"$first": "$prompt_version"}}},
        {"$sort": {"_id": -1}}, {"$limit": 40}]))
    for b in batches:
        b["batch"] = b.pop("_id")
        b["first"], b["last"] = _iso(b["first"]), _iso(b["last"])

    # Controles : justesse et matrice gravite evaluee x validee
    reviews = list(col.find({"review": {"$ne": None}},
                            {"gravite": 1, "review": 1, "prompt_version": 1}))
    matrix = {}
    verdicts = {v: 0 for v in VERDICTS}
    inexact = 0
    for d in reviews:
        rv = d.get("review") or {}
        verdicts[rv.get("verdict")] = verdicts.get(rv.get("verdict"), 0) + 1
        inexact += 1 if rv.get("resume_inexact") else 0
        key = "%s>%s" % (d.get("gravite"), rv.get("gravite_validee") or d.get("gravite"))
        matrix[key] = matrix.get(key, 0) + 1

    lock = db["cockpit_settings"].find_one({"_id": PD.LOCK_ID}) or {}
    now = datetime.now(timezone.utc)
    until = lock.get("until")
    if isinstance(until, datetime) and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    alive = bool(lock.get("running")) and isinstance(until, datetime) and until > now
    rate = eta = None
    started = lock.get("started_at")
    if isinstance(started, datetime):
        started = started.replace(tzinfo=timezone.utc) if started.tzinfo is None else started
        last = lock.get("last_at")
        last = last.replace(tzinfo=timezone.utc) if isinstance(last, datetime) and last.tzinfo is None else last
        span = ((last if isinstance(last, datetime) else now) - started).total_seconds()
        n = (lock.get("done") or 0) + (lock.get("errors") or 0)
        if span > 0 and n:
            rate = round(n * 60.0 / span, 1)
            left = max(0, (lock.get("todo_at_start") or 0) - n)
            eta = int(left * span / n) if alive else None
    run = {k: lock.get(k) for k in ("run_id", "batch", "done", "errors", "todo_at_start", "target",
                                    "max_batches", "end_at", "requested_by", "model",
                                    "prompt_version", "stop_reason", "stop_requested", "batch_size")}
    if alive and rate:
        # Fin estimee sur l'objectif de CE traitement (lots demandes), pas sur tout le stock
        n = (lock.get("done") or 0) + (lock.get("errors") or 0)
        left = max(0, (lock.get("target") or lock.get("todo_at_start") or 0) - n)
        eta = int(left * 60 / rate)
    run.update({"alive": alive, "started_at": _iso(lock.get("started_at")),
                "last_at": _iso(lock.get("last_at")), "ended_at": _iso(lock.get("ended_at")),
                "rate_per_min": rate, "eta_s": eta})

    req = db["cockpit_settings"].find_one({"_id": PD.REQUEST_ID}) or {}
    request_info = {k: req.get(k) for k in ("state", "batches", "batch_size", "requested_by", "result")}
    request_info.update({"requested_at": _iso(req.get("requested_at")),
                         "started_at": _iso(req.get("started_at")), "ended_at": _iso(req.get("ended_at"))})
    settings = db["cockpit_settings"].find_one({"_id": PD.SETTINGS_ID}) or {}

    return jsonify({
        "prompt_version": PD.PROMPT_VERSION, "model": PD.OLLAMA_MODEL,
        "total": total, "total_gps": total_gps, "structured": structured,
        "structured_gps": structured_gps, "errors": errors, "abandoned": abandoned,
        "by_gravite": by_grav, "batches": batches, "run": run, "request": request_info,
        "night_enabled": bool(settings.get("night_enabled")), "batch_sizes": list(PD.BATCH_SIZES),
        "max_request_batches": PD.MAX_REQUEST_BATCHES,
        "reviews": {"count": len(reviews), "verdicts": verdicts, "resume_inexact": inexact,
                    "matrix": matrix},
    })


@gravite_fiches_bp.route("/api/gravite-fiches/batch/<batch>")
@admin_required
def gravite_fiches_batch(batch):
    digests = list(_db()[PD.COLL].find({"batch": batch}).sort("at", 1))
    return jsonify({"batch": batch, "items": _with_fiches(digests)})


@gravite_fiches_bp.route("/api/gravite-fiches/sample")
@admin_required
def gravite_fiches_sample():
    """Sondage : fiches structurees non controlees tirees au hasard, filtrables
    par gravite evaluee ; les gravites 4-5 sont sur-representees par defaut."""
    try:
        n = max(1, min(50, int(request.args.get("n", 20))))
    except ValueError:
        n = 20
    match = {"prompt_version": PD.PROMPT_VERSION, "error": None, "review": None}
    grav = request.args.get("gravite")
    col = _db()[PD.COLL]
    if grav and grav.isdigit():
        docs = list(col.aggregate([{"$match": dict(match, gravite=int(grav))}, {"$sample": {"size": n}}]))
    else:
        high = list(col.aggregate([{"$match": dict(match, gravite={"$gte": 4})},
                                   {"$sample": {"size": n // 2}}]))
        rest = list(col.aggregate([{"$match": dict(match, gravite={"$lte": 3})},
                                   {"$sample": {"size": n - len(high)}}]))
        docs = high + rest
    return jsonify({"items": _with_fiches(docs)})


@gravite_fiches_bp.route("/api/gravite-fiches/<fiche_id>/review", methods=["POST"])
@admin_required
def gravite_fiches_review(fiche_id):
    data = request.get_json(silent=True) or {}
    verdict = data.get("verdict")
    col = _db()[PD.COLL]
    digest = col.find_one({"_id": fiche_id}, {"gravite": 1})
    if not digest:
        return jsonify({"error": "not_found"}), 404
    if verdict is None:                                  # annuler le controle
        col.update_one({"_id": fiche_id}, {"$set": {"review": None}})
        return jsonify({"ok": True, "review": None})
    if verdict not in VERDICTS:
        return jsonify({"error": "invalid_verdict"}), 400
    grav = digest.get("gravite")
    if verdict == "ok":
        valid = grav
    else:
        try:
            valid = int(data.get("gravite_validee"))
        except (TypeError, ValueError):
            return jsonify({"error": "gravite_validee_requise"}), 400
        # trop haut : la bonne gravite est en dessous ; trop bas : au-dessus
        if not 1 <= valid <= 5 or (verdict == "trop_haut" and valid >= (grav or 6)) \
                or (verdict == "trop_bas" and valid <= (grav or 0)):
            return jsonify({"error": "gravite_validee_incoherente"}), 400
    review = {"verdict": verdict, "gravite_validee": valid,
              "resume_inexact": bool(data.get("resume_inexact")),
              "note": str(data.get("note") or "").strip()[:500],
              "by": (getattr(request, "admin_user", None) or {}).get("email", "?"),
              "at": datetime.now(timezone.utc)}
    col.update_one({"_id": fiche_id}, {"$set": {"review": review}})
    review["at"] = _iso(review["at"])
    return jsonify({"ok": True, "review": review})


@gravite_fiches_bp.route("/api/gravite-fiches/stop", methods=["POST"])
@admin_required
def gravite_fiches_stop():
    """Demande l'arret du traitement en cours (pris en compte apres la fiche
    courante) et annule une demande pas encore prise par la tache."""
    db = _db()
    res = db["cockpit_settings"].update_one({"_id": PD.LOCK_ID, "running": True},
                                            {"$set": {"stop_requested": True}})
    cancelled = db["cockpit_settings"].update_one({"_id": PD.REQUEST_ID, "state": "pending"},
                                                  {"$set": {"state": "cancelled", "ended_at": datetime.now(timezone.utc)}})
    return jsonify({"ok": True, "running": bool(res.matched_count), "cancelled": bool(cancelled.modified_count)})


@gravite_fiches_bp.route("/api/gravite-fiches/launch", methods=["POST"])
@admin_required
def gravite_fiches_launch():
    """Depose une demande de lots. Rien ne tourne dans Cockpit : la tache
    planifiee (pcorg_digest.py --poll, chaque minute) la prend en charge."""
    data = request.get_json(silent=True) or {}
    try:
        size = int(data.get("batch_size") or PD.BATCH_SIZE)
        batches = int(data.get("batches") or 1)
    except (TypeError, ValueError):
        return jsonify({"error": "parametres_invalides"}), 400
    if size not in PD.BATCH_SIZES or not 1 <= batches <= PD.MAX_REQUEST_BATCHES:
        return jsonify({"error": "parametres_invalides"}), 400
    db = _db()
    if PD.lock_alive(db):
        return jsonify({"error": "traitement_en_cours"}), 409
    doc = {"state": "pending", "batch_size": size, "batches": batches,
           "requested_by": (getattr(request, "admin_user", None) or {}).get("email", "?"),
           "requested_at": datetime.now(timezone.utc), "started_at": None, "ended_at": None,
           "result": None, "run_id": None}
    # Une seule demande a la fois : refusee si une autre attend ou tourne
    try:
        res = db["cockpit_settings"].update_one(
            {"_id": PD.REQUEST_ID, "state": {"$nin": ["pending", "running"]}}, {"$set": doc}, upsert=True)
    except Exception:
        return jsonify({"error": "demande_deja_en_attente"}), 409
    if not (res.modified_count or res.upserted_id):
        return jsonify({"error": "demande_deja_en_attente"}), 409
    return jsonify({"ok": True, "batch_size": size, "batches": batches})


@gravite_fiches_bp.route("/api/gravite-fiches/night", methods=["POST"])
@admin_required
def gravite_fiches_night():
    """Active / coupe le traitement automatique de nuit (tache planifiee)."""
    enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
    _db()["cockpit_settings"].update_one(
        {"_id": PD.SETTINGS_ID},
        {"$set": {"night_enabled": enabled, "updated_at": datetime.now(timezone.utc),
                  "updated_by": (getattr(request, "admin_user", None) or {}).get("email", "?")}},
        upsert=True)
    return jsonify({"ok": True, "night_enabled": enabled})
