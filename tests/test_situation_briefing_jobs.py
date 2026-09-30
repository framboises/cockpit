"""Registre de jobs et routes du blueprint ai_reports (sans app.py)."""

import threading

import pytest
from flask import Flask

import ai_reports
import situation_briefing as sb


# ---------------------------------------------------------------------------
# Registre de jobs
# ---------------------------------------------------------------------------

def test_job_runs_and_reports_progress():
    reg = ai_reports.JobRegistry()

    def work(job, x):
        job.progress(50, "moitie")
        job.set(context={"c": 1})
        return {"double": x * 2}

    jid, already = reg.start("k", ("k", 1), work, 21)
    assert not already
    job = reg.wait(jid)
    assert job["status"] == "done" and job["result"] == {"double": 42}
    assert job["context"] == {"c": 1} and job["progress"] == 100


def test_same_target_is_refused_while_running_then_freed():
    reg = ai_reports.JobRegistry()
    gate = threading.Event()
    jid, _ = reg.start("k", ("t",), lambda job: gate.wait(5))
    jid2, already = reg.start("k", ("t",), lambda job: None)
    assert already and jid2 == jid
    other, already_other = reg.start("k", ("autre",), lambda job: None)
    assert not already_other
    gate.set()
    reg.wait(jid)
    reg.wait(other)
    jid3, already3 = reg.start("k", ("t",), lambda job: None)
    assert not already3 and jid3 != jid


@pytest.mark.parametrize("exc", [RuntimeError("boom"), SystemExit(3),
                                 ai_reports.JobError("budget_ia_depasse", "budget")])
def test_failure_marks_error_and_releases_target(exc):
    reg = ai_reports.JobRegistry()

    def work(job):
        raise exc

    jid, _ = reg.start("k", ("t",), work)
    job = reg.wait(jid)
    assert job["status"] == "error" and job["finished_at"]
    if isinstance(exc, ai_reports.JobError):
        assert job["error"] == "budget_ia_depasse"
    _, already = reg.start("k", ("t",), lambda job: None)
    assert not already


# ---------------------------------------------------------------------------
# Routes (auth et base remplacees)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    role = {"value": "manager"}

    def ident():
        from flask import request
        order = ["user", "manager", "admin"]
        request.user_payload = {"roles": order[:order.index(role["value"]) + 1],
                                "email": "m@test", "firstname": "M", "lastname": "T"}
        return None

    monkeypatch.setattr(ai_reports, "_identifier", ident)
    monkeypatch.setattr(ai_reports, "_db", lambda: object())
    app = Flask(__name__)
    app.register_blueprint(ai_reports.ai_reports_bp)
    c = app.test_client()
    c.role = role
    return c


def test_raw_briefing_is_synchronous_and_free(client, monkeypatch):
    monkeypatch.setattr(sb, "build_context", lambda db, event=None, year=None: {
        "event": event or "AUTO", "year": year, "sources": {}})
    res = client.post("/api/ai/briefing", json={"llm": False})
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "llm": False,
                              "context": {"event": "AUTO", "year": None, "sources": {}}}
    res = client.post("/api/ai/briefing", json={"llm": False, "event": "E", "year": "2026"})
    assert res.get_json()["context"]["year"] == 2026


def test_llm_briefing_without_key_is_503(client, monkeypatch):
    monkeypatch.setattr(sb, "api_key_present", lambda: False)
    res = client.post("/api/ai/briefing", json={"llm": True})
    assert res.status_code == 503 and res.get_json()["error"] == "cle_api_absente"


def test_bad_year_and_unknown_job(client):
    assert client.post("/api/ai/briefing", json={"event": "E", "year": "xx"}).get_json() == {
        "ok": False, "error": "annee_invalide"}
    res = client.get("/api/ai/briefing/status?job=nope")
    assert res.status_code == 404 and res.get_json()["error"] == "job_inconnu"


def test_retex_is_admin_only(client):
    res = client.get("/api/ai/retex/editions")
    assert res.status_code == 403 and res.get_json()["error"] == "role_admin_requis"
    res = client.get("/api/ai/briefing/settings")
    assert res.status_code == 403


def test_user_role_is_refused(client):
    client.role["value"] = "user"
    res = client.post("/api/ai/briefing", json={})
    assert res.status_code == 403 and res.get_json()["error"] == "role_manager_requis"


def test_retex_dry_run_route(client, monkeypatch):
    import edition_retex as er
    client.role["value"] = "admin"
    monkeypatch.setattr(er, "dry_run", lambda db, e, y: {
        "dry_run": True, "prompt_chars": 10, "dataset": {"gros": 1}, "blocs": {}})
    res = client.post("/api/ai/retex", json={"event": "E", "year": 2026, "dry_run": True})
    body = res.get_json()
    assert res.status_code == 200 and body["prompt_chars"] == 10 and "dataset" not in body
    assert client.post("/api/ai/retex", json={"year": 2026}).get_json()["error"] == "edition_requise"
