"""Blueprint Cockpit : rapports IA de situation et de fin d'edition.

  - Briefing de situation (situation_briefing.py), manager :
      POST /api/ai/briefing                {llm: bool, event?, year?, model?}
            llm=false -> contexte brut, synchrone, gratuit
            llm=true  -> job en arriere-plan (contexte puis redaction Claude)
      GET  /api/ai/briefing/status?job=
      GET  /api/ai/briefing/list?limit=
      GET  /api/ai/briefing/<id>
      GET|PUT /api/ai/briefing/settings    (admin) releve automatique
  - RETEX de fin d'edition (edition_retex.py), admin :
      GET  /api/ai/retex/editions
      POST /api/ai/retex                   {event, year, dry_run?, model?}
      GET  /api/ai/retex/status?job=
      GET  /api/ai/retex/list?event=&year=
      GET  /api/ai/retex/<id>
      GET  /api/ai/retex/<id>/html         page autonome imprimable

Auth : meme motif que pmv.py (JSON 401/403, jamais de redirection sur une
API appelee en fetch). CSRF : blueprint NON exempte, les POST/PUT portent
X-CSRFToken. Erreurs : {"ok": false, "error": "<code>"}, jamais abort(404)
(le handler 404 global redirige vers /, ce qui casserait un XHR).

Les jobs (registre memoire) supposent un seul process, comme scan_report.py
et pmv.py (vrai sous waitress).
"""

import logging
import threading
import time
import uuid
from functools import wraps

from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger(__name__)

ai_reports_bp = Blueprint("ai_reports", __name__)


# ---------------------------------------------------------------------------
# Registre de jobs (pur, testable sans Flask ni Mongo)
# ---------------------------------------------------------------------------

class Job:
    """Vue d'un job passee a la fonction de travail."""

    def __init__(self, registry, job_id):
        self._reg = registry
        self.id = job_id

    def progress(self, pct, step):
        self._reg._update(self.id, progress=max(0, min(100, int(pct))), step=step)

    def set(self, **fields):
        self._reg._update(self.id, **fields)


class JobRegistry:
    """Jobs en thread, une cible a la fois (409 sinon), purges apres TTL.

    La fonction de travail recoit un `Job` et rend le `result`. Toute
    exception -- BaseException comprise, sinon une SystemExit laisserait le
    job << en cours >> a jamais (cf. scan_report.py) -- passe le job en erreur
    et LIBERE la cible dans le finally.
    """

    PUBLIC = ("id", "kind", "status", "progress", "step", "error", "detail",
              "result", "context", "started_at", "finished_at")

    def __init__(self, ttl_s=3600):
        self._jobs = {}
        self._by_target = {}
        self._threads = {}
        self._lock = threading.Lock()
        self.ttl_s = ttl_s

    def _sweep(self):
        now = time.time()
        for jid in [j for j, v in self._jobs.items()
                    if v.get("finished_at") and now - v["finished_at"] > self.ttl_s]:
            self._jobs.pop(jid, None)
            self._threads.pop(jid, None)

    def _update(self, job_id, **fields):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(fields)

    def start(self, kind, target, fn, *args):
        """Retourne (job_id, deja_en_cours)."""
        with self._lock:
            self._sweep()
            running = self._by_target.get(target)
            if running and self._jobs.get(running, {}).get("status") in ("queued", "running"):
                return running, True
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = {
                "id": job_id, "kind": kind, "target": target, "status": "queued",
                "progress": 0, "step": "En attente", "error": None, "detail": None,
                "result": None, "context": None,
                "started_at": time.time(), "finished_at": None,
            }
            self._by_target[target] = job_id
        th = threading.Thread(target=self._run, args=(job_id, target, fn, args), daemon=True)
        self._threads[job_id] = th
        th.start()
        return job_id, False

    def _run(self, job_id, target, fn, args):
        try:
            self._update(job_id, status="running")
            result = fn(Job(self, job_id), *args)
            self._update(job_id, status="done", progress=100, step="Termine", result=result)
        except BaseException as exc:
            code = exc.code if isinstance(exc, JobError) else "generation_impossible"
            logger.warning("job %s en echec : %s", job_id, exc, exc_info=True)
            self._update(job_id, status="error", error=str(code), detail=str(exc)[:500],
                         step="Echec")
        finally:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["finished_at"] = time.time()
                if self._by_target.get(target) == job_id:
                    self._by_target.pop(target, None)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return {k: job.get(k) for k in self.PUBLIC}

    def wait(self, job_id, timeout=10):
        """Tests : attend la fin du thread."""
        th = self._threads.get(job_id)
        if th is not None:
            th.join(timeout)
        return self.get(job_id)


JOBS = JobRegistry()


class JobError(Exception):
    """Erreur de job portant un code d'erreur stable pour l'UI."""

    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


# ---------------------------------------------------------------------------
# Auth (motif pmv.py)
# ---------------------------------------------------------------------------

def _err(code, status=400, **extra):
    body = {"ok": False, "error": code}
    body.update(extra)
    return jsonify(body), status


def _db():
    from app import db
    return db


def _identifier():
    import jwt as pyjwt
    from app import (APP_KEY, CODING, JWT_ALGORITHM, JWT_SECRET, ROLE_HIERARCHY,
                     ROLE_ORDER, SUPER_ADMIN_ROLE)
    if CODING:
        role = request.args.get("as", "admin")
        if role not in ROLE_HIERARCHY:
            role = "admin"
        request.user_payload = {
            "roles": [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= ROLE_HIERARCHY[role]],
            "app_role": role, "is_super_admin": False,
            "firstname": "Bruce", "lastname": "WAYNE", "email": "bruce@wayneenterprise.com",
        }
        return None
    token = request.cookies.get("access_token")
    if not token:
        return _err("non_authentifie", 401)
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return _err("session_invalide", 401)
    super_admin = SUPER_ADMIN_ROLE in (payload.get("global_roles") or [])
    roles_by_app = payload.get("roles_by_app") or {}
    app_role = roles_by_app.get(APP_KEY) if isinstance(roles_by_app, dict) else None
    role = "admin" if super_admin else app_role
    if role not in ROLE_HIERARCHY:
        return _err("acces_refuse", 403)
    payload["roles"] = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= ROLE_HIERARCHY[role]]
    payload["app_role"] = role
    request.user_payload = payload
    return None


def _has_role(role):
    return role in (getattr(request, "user_payload", {}) or {}).get("roles", [])


@ai_reports_bp.before_request
def _before():
    err = _identifier()
    if err:
        return err
    if not _has_role("manager"):
        return _err("role_manager_requis", 403)
    return None


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _has_role("admin"):
            return _err("role_admin_requis", 403)
        return f(*args, **kwargs)
    return wrapper


def _user():
    p = getattr(request, "user_payload", {}) or {}
    name = ("%s %s" % (p.get("firstname", ""), p.get("lastname", ""))).strip()
    return p.get("email", ""), name or p.get("email", "")


def _event_year(body):
    event = (body.get("event") or "").strip() or None
    year = body.get("year")
    if year in (None, ""):
        return event, None
    try:
        return event, int(year)
    except (TypeError, ValueError):
        raise ValueError("annee_invalide")


def _job_status(kind):
    job = JOBS.get(request.args.get("job") or "")
    if not job or job.get("kind") != kind:
        return _err("job_inconnu", 404)
    return jsonify({"ok": True, **job})


# ---------------------------------------------------------------------------
# Briefing de situation
# ---------------------------------------------------------------------------

@ai_reports_bp.route("/api/ai/briefing", methods=["POST"])
def briefing_run():
    import situation_briefing as sb
    body = request.get_json(silent=True) or {}
    try:
        event, year = _event_year(body)
    except ValueError:
        return _err("annee_invalide")
    if event and year is None:
        return _err("annee_invalide")

    if not body.get("llm"):
        try:
            ctx = sb.build_context(_db(), event=event, year=year)
        except Exception as exc:
            logger.exception("briefing brut impossible")
            return _err("contexte_impossible", 500, detail=str(exc)[:300])
        return jsonify({"ok": True, "llm": False, "context": ctx})

    if not sb.api_key_present():
        return _err("cle_api_absente", 503, detail="COCKPIT_ANTHROPIC_API_KEY non configuree sur le serveur")
    email, name = _user()
    job_id, already = JOBS.start("briefing", ("briefing", event, year), _briefing_job,
                                 event, year, body.get("model"), email or name)
    if already:
        return _err("briefing_en_cours", 409, job_id=job_id)
    return jsonify({"ok": True, "llm": True, "job_id": job_id})


def _briefing_job(job, event, year, model, by):
    import situation_briefing as sb
    db = _db()
    job.progress(5, "Collecte des sources")
    ctx = sb.build_context(db, event=event, year=year)
    job.set(context=ctx)
    job.progress(30, "Redaction IA")

    def on_progress(text, out_tokens):
        job.progress(min(95, 30 + out_tokens // 25), "Redaction IA (%d tokens)" % out_tokens)

    try:
        doc = sb.generate_briefing(db, ctx, by=by, model=model, on_progress=on_progress)
    except Exception as exc:
        raise JobError(_claude_code(exc), str(exc))
    return {"id": str(doc.get("_id")), "sections": doc.get("sections"),
            "raw_text": doc.get("raw_text"), "model": doc.get("model"),
            "usage": doc.get("usage")}


def _claude_code(exc):
    msg = str(exc)
    if msg == "budget_exceeded":
        return "budget_ia_depasse"
    if msg.startswith("claude_") or "ANTHROPIC" in msg:
        return "ia_indisponible"
    return "generation_impossible"


@ai_reports_bp.route("/api/ai/briefing/status")
def briefing_status():
    return _job_status("briefing")


@ai_reports_bp.route("/api/ai/briefing/list")
def briefing_list():
    import situation_briefing as sb
    try:
        limit = max(1, min(100, int(request.args.get("limit") or 30)))
    except ValueError:
        limit = 30
    return jsonify({"ok": True, "items": sb.list_briefings(_db(), limit)})


@ai_reports_bp.route("/api/ai/briefing/settings", methods=["GET"])
@admin_required
def briefing_settings_get():
    import situation_briefing as sb
    db = _db()
    users = []
    for u in db["users"].find({"roles_by_app.cockpit": {"$exists": True},
                               "email": {"$exists": True, "$ne": ""}},
                              {"prenom": 1, "nom": 1, "email": 1}):
        users.append({"id": str(u["_id"]),
                      "name": ("%s %s" % (u.get("prenom") or "", u.get("nom") or "")).strip()
                      or u.get("email"),
                      "email": u.get("email")})
    users.sort(key=lambda u: (u["name"] or "").lower())
    return jsonify({"ok": True, "settings": sb.get_settings(db), "users": users,
                    "api_key": sb.api_key_present()})


@ai_reports_bp.route("/api/ai/briefing/settings", methods=["PUT"])
@admin_required
def briefing_settings_put():
    import situation_briefing as sb
    body = request.get_json(silent=True) or {}
    email, _ = _user()
    try:
        settings = sb.set_settings(
            _db(),
            enabled=body.get("enabled") if "enabled" in body else None,
            times=body.get("times") if "times" in body else None,
            recipients=body.get("recipients") if "recipients" in body else None,
            by=email)
    except ValueError as exc:
        return _err("reglage_invalide", 400, detail=str(exc))
    return jsonify({"ok": True, "settings": settings})


@ai_reports_bp.route("/api/ai/briefing/<briefing_id>")
def briefing_get(briefing_id):
    import situation_briefing as sb
    doc = sb.get_briefing(_db(), briefing_id)
    if not doc:
        return _err("briefing_inconnu", 404)
    return jsonify({"ok": True, "briefing": sb.serialize(doc, light=False)})


# ---------------------------------------------------------------------------
# RETEX de fin d'edition
# ---------------------------------------------------------------------------

@ai_reports_bp.route("/api/ai/retex/editions")
@admin_required
def retex_editions():
    import edition_retex as er
    return jsonify({"ok": True, "items": er.list_editions(_db())})


@ai_reports_bp.route("/api/ai/retex", methods=["POST"])
@admin_required
def retex_run():
    import edition_retex as er
    import situation_briefing as sb
    body = request.get_json(silent=True) or {}
    try:
        event, year = _event_year(body)
    except ValueError:
        return _err("annee_invalide")
    if not event or year is None:
        return _err("edition_requise")

    if body.get("dry_run"):
        try:
            out = er.dry_run(_db(), event, year)
        except Exception as exc:
            logger.exception("retex dry-run %s %s", event, year)
            return _err("dry_run_impossible", 500, detail=str(exc)[:300])
        out.pop("dataset", None)  # volumineux, deja resume dans `blocs` et `user`
        return jsonify({"ok": True, **out})

    if not sb.api_key_present():
        return _err("cle_api_absente", 503, detail="COCKPIT_ANTHROPIC_API_KEY non configuree sur le serveur")
    email, name = _user()
    job_id, already = JOBS.start("retex", ("retex", event, year), _retex_job,
                                 event, year, body.get("model"), email or name)
    if already:
        return _err("retex_en_cours", 409, job_id=job_id)
    return jsonify({"ok": True, "job_id": job_id})


def _retex_job(job, event, year, model, by):
    import edition_retex as er
    db = _db()
    job.progress(5, "Assemblage des donnees de l'edition")
    dataset = er.build(db, event, year)
    job.progress(25, "Redaction IA (rapport long, 1 a 3 min)")

    def on_progress(text, out_tokens):
        job.progress(min(95, 25 + out_tokens // 150), "Redaction IA (%d tokens)" % out_tokens)

    try:
        doc = er.generate(db, event, year, by=by, model=model, on_progress=on_progress,
                          dataset=dataset)
    except Exception as exc:
        raise JobError(_claude_code(exc), str(exc))
    return {"id": str(doc["_id"]), "version": doc.get("version"),
            "parsed": bool(doc.get("sections")), "truncated": doc.get("truncated"),
            "model": doc.get("model"), "usage": doc.get("usage")}


@ai_reports_bp.route("/api/ai/retex/status")
@admin_required
def retex_status():
    return _job_status("retex")


@ai_reports_bp.route("/api/ai/retex/list")
@admin_required
def retex_list():
    import edition_retex as er
    return jsonify({"ok": True, "items": er.list_retex(
        _db(), request.args.get("event"), request.args.get("year"))})


@ai_reports_bp.route("/api/ai/retex/<retex_id>")
@admin_required
def retex_get(retex_id):
    import edition_retex as er
    doc = er.get_retex(_db(), retex_id)
    if not doc:
        return _err("retex_inconnu", 404)
    return jsonify({"ok": True, "retex": er.serialize(doc, light=False)})


@ai_reports_bp.route("/api/ai/retex/<retex_id>/html")
@admin_required
def retex_html(retex_id):
    import edition_retex as er
    doc = er.get_retex(_db(), retex_id)
    if not doc:
        return _err("retex_inconnu", 404)
    resp = Response(er.render_html(doc), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp
