# gravite_fiches.py -- suivi et controle de la gravite evaluee des fiches
#
# Page /admin/gravite-fiches (page d'administration "gravite_fiches" de
# ADMIN_PAGE_REGISTRY : admins, accordable explicitement a un groupe).
# Lit ce que produit pcorg_digest.py (lance hors de Cockpit) : avancement du
# traitement en cours, lots de 50, et controle par un admin fiche par fiche
# (juste / trop haut / trop bas, resume inexact). Une gravite validee prime
# partout et fige la fiche (jamais reevaluee). Voir docs/claude/patrouilles.md.
import logging
import os
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, render_template, request

import pcorg_digest as PD
from field import admin_required

gravite_fiches_bp = Blueprint("gravite_fiches", __name__)
logger = logging.getLogger("gravite_fiches")

VERDICTS = ("ok", "trop_haut", "trop_bas")   # jugement porte sur la gravite du modele
# Champs que l'admin peut signaler faux (controle par sondage des faits extraits)
CHAMPS = ("lieu", "position", "heures", "equipement", "moyens", "services", "causes", "issue", "resume",
          "nature", "ronde", "alarme", "dysfonctionnement", "jalon", "secours", "prise_service",
          "sous_classification")
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
                                  "event", "year", "category", "has_gps", "lieu", "etapes", "delais",
                                  "equipement", "services_exterieurs", "causes", "issue_code",
                                  "donnees_personnelles", "geo", "zone", "completude",
                                  "doublon_probable", "nature", "activite", "ronde", "alarme",
                                  "dysfonctionnement", "jalon", "secours", "prise_service",
                                  "sous_classification_proposee", "taxo", "area", "relances",
                                  "doublon_declare", "alarme_non_remise", "a_controler")}
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
    # Fiches jumelles (doublon probable / declare) : lues avec la fiche pour
    # confirmer le doublon sans quitter le controle
    twins = {(d.get(k) or {}).get("de") for d in digests for k in ("doublon_probable", "doublon_declare")} - {None}
    fiches = {f["_id"]: f for f in _db()["pcorg"].find({"_id": {"$in": ids + list(twins)}},
                                                      dict(FICHE_FIELDS, sql_id=1))}
    out = []
    for d in digests:
        o = _pub_digest(d, fiches.get(d["_id"], {}))
        for k in ("doublon_probable", "doublon_declare"):
            t = fiches.get((d.get(k) or {}).get("de"))
            if t:
                o[k] = dict(o[k], fiche=dict({x: t.get(x) for x in FICHE_FIELDS}, sql_id=t.get("sql_id")))
        out.append(o)
    return out


def _asset_version():
    """Version des JS / CSS de la page (date de modification) : Safari iOS
    garde longtemps les fichiers statiques, l'URL change a chaque deploiement."""
    from flask import current_app
    v = 0
    for parts in (("js", "gravite_fiches.js"), ("css", "gravite_fiches.css")):
        try:
            v = max(v, int(os.path.getmtime(os.path.join(current_app.static_folder, *parts))))
        except OSError:
            pass
    return v


@gravite_fiches_bp.route("/admin/gravite-fiches")
@_role_required("admin")
def gravite_fiches_page():
    payload = getattr(request, "user_payload", {}) or {}
    return render_template("gravite_fiches.html", asset_v=_asset_version(),
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
                    "marked": {"$sum": {"$cond": [{"$ifNull": ["$a_controler", False]}, 1, 0]}},
                    "marked_done": {"$sum": {"$cond": [{"$and": [{"$ifNull": ["$a_controler", False]},
                                                                  {"$ifNull": ["$review", False]}]}, 1, 0]}},
                    "first": {"$min": "$at"}, "last": {"$max": "$at"},
                    "prompt_version": {"$first": "$prompt_version"}}},
        {"$sort": {"_id": -1}}, {"$limit": 40}]))
    for b in batches:
        b["batch"] = b.pop("_id")
        b["first"], b["last"] = _iso(b["first"]), _iso(b["last"])

    # Controles : justesse et matrice gravite JUGEE (au moment du controle) x validee
    reviews = list(col.find({"review": {"$ne": None}},
                            {"gravite": 1, "review": 1, "prompt_version": 1, "a_controler": 1}))
    # Justesse sur les seules fiches tirees AU HASARD : les autres sont choisies
    # parce qu'elles sont delicates, leur justesse sous-estime le modele.
    rand = [d for d in reviews if "aleatoire" in ((d.get("a_controler") or {}).get("raisons") or [])]
    rand_ok = sum(1 for d in rand if (d.get("review") or {}).get("verdict") == "ok")
    matrix = {}
    verdicts = {v: 0 for v in VERDICTS}
    inexact = 0
    champs_faux = {c: 0 for c in CHAMPS}
    doublons_rev = {"oui": 0, "non": 0}
    by_version = {}                 # justesse de chaque version sur les fiches validees
    for d in reviews:
        rv = d.get("review") or {}
        verdicts[rv.get("verdict")] = verdicts.get(rv.get("verdict"), 0) + 1
        inexact += 1 if rv.get("resume_inexact") else 0
        for c in rv.get("champs_faux") or []:
            if c in champs_faux:
                champs_faux[c] += 1
        if rv.get("doublon") in doublons_rev:
            doublons_rev[rv["doublon"]] += 1
        judged = rv.get("gravite_modele") or d.get("gravite")
        valid = rv.get("gravite_validee") or judged
        key = "%s>%s" % (judged, valid)
        matrix[key] = matrix.get(key, 0) + 1
        # Version jugee par l'admin, puis version actuelle (retraitee depuis)
        pairs = {(rv.get("prompt_version") or "v1", judged)}
        if d.get("prompt_version") and d.get("gravite"):
            pairs.add((d["prompt_version"], d["gravite"]))
        for ver, g in pairs:
            bv = by_version.setdefault(ver, {"n": 0, "ok": 0, "ecart": 0})
            bv["n"] += 1
            bv["ok"] += 1 if g == valid else 0
            bv["ecart"] += abs((g or 0) - (valid or 0))
    quality = _quality(col, cur)

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
                    "matrix": matrix, "champs_faux": champs_faux, "doublons": doublons_rev,
                    "by_version": by_version, "aleatoire": {"n": len(rand), "ok": rand_ok}},
        "quality": quality,
    })


def _top(col, match, field, n=12, unwind=False):
    pipe = [{"$match": match}]
    if unwind:
        pipe.append({"$unwind": "$" + field})
    pipe += [{"$group": {"_id": "$" + field, "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": n}]
    return [{"v": r["_id"], "n": r["n"]} for r in col.aggregate(pipe) if r["_id"] not in (None, "")]


def _quality(col, cur):
    """Indicateurs de qualite des donnees sur les fiches de la version courante."""
    base = dict(cur, gravite={"$ne": None})
    total = col.count_documents(base)
    pos = {r["_id"] or "aucune": r["n"] for r in col.aggregate([
        {"$match": base}, {"$group": {"_id": "$geo.source", "n": {"$sum": 1}}}])}
    delais = {}
    for k in ("engagement", "sur_place", "fin"):
        vals = sorted(d["delais"][k] for d in col.find(dict(base, **{"delais." + k: {"$ne": None}}),
                                                       {"delais." + k: 1}))
        delais[k] = {"n": len(vals), "mediane": vals[len(vals) // 2] if vals else None}
    equip = _top(col, dict(base, equipement={"$nin": ["aucun", "non precise", None, ""]}), "equipement")
    comp = list(col.aggregate([{"$match": dict(base, **{"completude.score": {"$ne": None}})},
                               {"$group": {"_id": None, "moy": {"$avg": "$completude.score"}}}]))
    out = {
        "total": total, "positions": pos,
        "delais": delais,
        "equipements": equip,
        "services": _top(col, base, "services_exterieurs", unwind=True),
        "causes": _top(col, base, "causes", unwind=True),
        "issues": _top(col, base, "issue_code", n=6),
        "donnees_perso": _top(col, base, "donnees_personnelles", unwind=True),
        "doublons": col.count_documents(dict(base, doublon_probable={"$ne": None})),
        "completude_moy": round(comp[0]["moy"], 2) if comp else None,
    }
    out.update(_quality_v3(col, base))
    return out


def _median(vals):
    vals = sorted(v for v in vals if v is not None)
    return vals[len(vals) // 2] if vals else None


def _minutes(a, b):
    """Duree HH:MM -> HH:MM en minutes (passage de minuit gere), ou None."""
    try:
        ha, ma = (int(x) for x in a.split(":"))
        hb, mb = (int(x) for x in b.split(":"))
    except (AttributeError, ValueError):
        return None
    d = (hb * 60 + mb) - (ha * 60 + ma)
    d = d + 1440 if d < 0 else d
    return d if d <= 720 else None


def _when(d):
    ts = d.get("ts")
    return "%s %s %s" % (d.get("event") or "", d.get("year") or "",
                         ts.strftime("%d/%m %H:%M") if isinstance(ts, datetime) else "")


def _quality_v3(col, base):
    """Indicateurs des blocs v3 : nature, rondes, alarmes, RETEX, jalons, secours,
    presences, reactivite des services, taxonomie, doublons declares."""
    out = {"natures": _top(col, base, "nature", n=4), "activites": _top(col, base, "activite", n=8)}

    rondes = list(col.find(dict(base, ronde={"$ne": None}), {"ronde": 1}))
    out["rondes"] = {
        "n": len(rondes),
        "ras": sum(1 for d in rondes if (d["ronde"] or {}).get("resultat") == "ras"),
        "anomalies": sum(1 for d in rondes if (d["ronde"] or {}).get("resultat") == "anomalie"),
        "duree_mediane": _median(_minutes((d["ronde"] or {}).get("debut"), (d["ronde"] or {}).get("fin"))
                                 for d in rondes),
        "points": _top(col, dict(base, ronde={"$ne": None}), "ronde.points", unwind=True),
        "anomalies_top": _top(col, dict(base, ronde={"$ne": None}), "ronde.anomalies", n=8, unwind=True),
    }

    alarm = dict(base, alarme={"$ne": None})
    out["alarmes"] = {
        "hors_service": col.count_documents(dict(alarm, **{"alarme.action": "mise_hors_service"})),
        "remises": col.count_documents(dict(alarm, **{"alarme.action": {"$in": ["remise_en_service", "les_deux"]}})),
        "non_remises": col.count_documents(dict(base, alarme_non_remise=True)),
        "derniers_non_remis": [{"quand": _when(d), "batiment": (d.get("alarme") or {}).get("batiment"),
                                "id": d["_id"]}
                               for d in col.find(dict(base, alarme_non_remise=True),
                                                 {"alarme": 1, "ts": 1, "event": 1, "year": 1}).sort("ts", -1).limit(10)],
    }

    dysf = dict(base, dysfonctionnement={"$ne": None})
    out["retex"] = {
        "par_type": _top(col, dysf, "dysfonctionnement.type", n=6),
        "derniers": [{"quand": _when(d), "type": (d.get("dysfonctionnement") or {}).get("type"),
                      "description": (d.get("dysfonctionnement") or {}).get("description"), "id": d["_id"]}
                     for d in col.find(dysf, {"dysfonctionnement": 1, "ts": 1, "event": 1, "year": 1})
                     .sort("ts", -1).limit(12)],
    }

    jal = dict(base, jalon={"$ne": None})
    out["jalons"] = {
        "par_type": _top(col, jal, "jalon.type", n=8),
        "derniers": [{"quand": _when(d), "type": (d.get("jalon") or {}).get("type"),
                      "heure": (d.get("jalon") or {}).get("heure"),
                      "libelle": (d.get("jalon") or {}).get("libelle"), "id": d["_id"]}
                     for d in col.find(jal, {"jalon": 1, "ts": 1, "event": 1, "year": 1}).sort("ts", -1).limit(12)],
    }

    sec = dict(base, secours={"$ne": None})
    out["secours"] = {"n": col.count_documents(sec)}
    for k in ("nature", "tranche_age", "sexe", "prise_en_charge"):
        out["secours"][k] = _top(col, sec, "secours." + k, n=8)

    ps = dict(base, prise_service={"$ne": None})
    out["presences"] = {"n": col.count_documents(ps), "postes": _top(col, ps, "prise_service.poste", n=10),
                        "types": _top(col, ps, "prise_service.type", n=3)}

    # Reactivite par service contacte : delai de fin median et relances moyennes
    reac = {}
    for d in col.find(dict(base, **{"taxo.service": {"$nin": [None, ""]}}),
                      {"taxo.service": 1, "delais.fin": 1, "relances": 1}):
        r = reac.setdefault(d["taxo"]["service"], {"n": 0, "fins": [], "relances": []})
        r["n"] += 1
        r["fins"].append((d.get("delais") or {}).get("fin"))
        if d.get("relances") is not None:
            r["relances"].append(d["relances"])
    out["reactivite"] = sorted(
        [{"service": k, "n": v["n"], "fin_mediane": _median(v["fins"]),
          "relances_moy": round(sum(v["relances"]) / len(v["relances"]), 2) if v["relances"] else None}
         for k, v in reac.items()], key=lambda x: -x["n"])[:12]

    out["taxonomie"] = {
        "sous_classifications": _top(col, base, "taxo.sous_classification", n=12),
        "hors_liste": col.count_documents(dict(base, **{"taxo.sous_classification": {"$ne": None},
                                                         "taxo.sous_classification_officielle": False})),
        "proposees": col.count_documents(dict(base, sous_classification_proposee={"$ne": None},
                                              **{"taxo.sous_classification": None})),
        "motifs": _top(col, base, "taxo.motif", n=10),
    }

    # Doublons declares par les operateurs : part retrouvee par le detecteur
    decl = list(col.find(dict(base, doublon_declare={"$ne": None}), {"doublon_declare": 1, "doublon_probable": 1}))
    found = sum(1 for d in decl if (d.get("doublon_probable") or {}).get("de") == d["doublon_declare"]["de"])
    out["doublons_declares"] = {"n": len(decl), "retrouves": found}
    return out


@gravite_fiches_bp.route("/api/gravite-fiches/batch/<batch>")
@admin_required
def gravite_fiches_batch(batch):
    """Fiches A CONTROLER du lot (marquees a la generation) ; ?tout=1 = lot entier."""
    col = _db()[PD.COLL]
    total = col.count_documents({"batch": batch})
    q = {"batch": batch}
    marked = col.count_documents(dict(q, a_controler={"$ne": None}))
    only_marked = request.args.get("tout") != "1" and marked > 0
    if only_marked:
        q["a_controler"] = {"$ne": None}
    digests = list(col.find(q))
    digests.sort(key=lambda d: ((d.get("a_controler") or {}).get("rang") or 999, str(d.get("at"))))
    return jsonify({"batch": batch, "items": _with_fiches(digests), "total": total,
                    "marked": marked, "only_marked": only_marked})


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
    # Les fiches marquees « a controler » a la generation passent d'abord
    if not grav and col.count_documents(dict(match, a_controler={"$ne": None})):
        docs = list(col.find(dict(match, a_controler={"$ne": None})).sort([("batch", -1)]).limit(n))
        docs.sort(key=lambda d: (str(d.get("batch")), (d.get("a_controler") or {}).get("rang") or 999))
        return jsonify({"items": _with_fiches(docs), "marked": True})
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
    digest = col.find_one({"_id": fiche_id}, {"gravite": 1, "prompt_version": 1, "doublon_probable": 1})
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
    champs = [c for c in (data.get("champs_faux") or []) if c in CHAMPS]
    if data.get("resume_inexact") and "resume" not in champs:
        champs.append("resume")
    doublon = data.get("doublon") if data.get("doublon") in ("oui", "non") else None
    review = {"verdict": verdict, "gravite_validee": valid,
              # gravite et version JUGEES : la fiche peut etre retraitee par une
              # version suivante, le controle reste interpretable
              "gravite_modele": grav, "prompt_version": digest.get("prompt_version"),
              "resume_inexact": "resume" in champs, "champs_faux": champs, "doublon": doublon,
              "note": str(data.get("note") or "").strip()[:500],
              "by": (getattr(request, "admin_user", None) or {}).get("email", "?"),
              "at": datetime.now(timezone.utc)}
    sets = {"review": review}
    if doublon == "non":
        sets["doublon_probable"] = None     # jamais repose (pcorg_enrich.link_duplicates)
    col.update_one({"_id": fiche_id}, {"$set": sets})
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
