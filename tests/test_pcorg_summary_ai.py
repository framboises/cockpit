"""Tests de l'Assistant IA (pcorg_summary / pcorg_ai_memory / mail).

Aucun appel reseau : les appels Claude sont remplaces par des doubles.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import pcorg_ai_memory
import pcorg_summary as ps
import pcorg_summary_mail
from conftest import FakeCollection, FakeDb


# ----------------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------------

class _Col(FakeCollection):
    def insert_one(self, doc):
        self.docs.append(doc)

        class _R:
            inserted_id = doc.get("_id")
        return _R()


class _Db(FakeDb):
    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _Col([])
        return self._cols[name]


def _get_path(doc, path):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _matches(doc, query):
    """Evaluateur minimal des filtres produits par build_scope_query."""
    for key, cond in query.items():
        val = _get_path(doc, key)
        if isinstance(cond, dict):
            if "$in" in cond and val not in cond["$in"]:
                return False
        elif val != cond:
            return False
    return True


# ----------------------------------------------------------------------------
# Cout / journal / budget
# ----------------------------------------------------------------------------

def test_cost_formula_input_excludes_cache_tokens():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
             "cache_creation_input_tokens": 1_000_000,
             "cache_read_input_tokens": 1_000_000}
    # Sonnet 5.5 : 2 + 10 + 2,50 + 0,20
    assert ps.compute_cost_usd("claude-sonnet-5-5", usage) == pytest.approx(14.70)
    # Opus 5.5 : lecture cache a 0,05 x input
    assert ps.compute_cost_usd("claude-opus-5-5", {"cache_read_input_tokens": 1_000_000}) \
        == pytest.approx(0.20)
    # Les caches ne sont PAS soustraits de input_tokens (ancienne erreur).
    u = {"input_tokens": 1000, "cache_read_input_tokens": 5000, "output_tokens": 0}
    assert ps.compute_cost_usd("claude-sonnet-4-6", u) == pytest.approx(
        (1000 * 3.0 + 5000 * 0.30) / 1e6)
    assert ps.compute_cost_usd("modele-inconnu", usage) is None


def test_pricing_covers_allowed_models():
    for m in ps.ALLOWED_MODELS | {"claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5"}:
        p = ps.MODEL_PRICING_USD_PER_MTOK[m]
        assert {"input", "output", "cache_write", "cache_read"} <= set(p)


def test_record_ai_usage_inserts_with_cost():
    db = _Db()
    doc = ps.record_ai_usage(db, "suggest_rule", "claude-haiku-4-5",
                             {"input_tokens": 1000, "output_tokens": 100},
                             meta={"event": "24H AUTOS"})
    assert doc is not None
    stored = db["ai_usage_log"].docs
    assert len(stored) == 1
    assert stored[0]["feature"] == "suggest_rule"
    assert stored[0]["usage"]["cache_read_input_tokens"] == 0
    assert stored[0]["cost_usd"] == pytest.approx((1000 * 1.0 + 100 * 5.0) / 1e6)
    assert stored[0]["meta"]["event"] == "24H AUTOS"


def test_record_ai_usage_never_raises():
    class Boom:
        def __getitem__(self, name):
            raise RuntimeError("mongo down")
    assert ps.record_ai_usage(Boom(), "x", "claude-haiku-4-5", {}) is None
    assert ps.record_ai_usage(None, "x", "claude-haiku-4-5", {}) is None


def _db_with_month_spend(usd_output_tokens):
    now = datetime.now(timezone.utc)
    db = _Db()
    db["pcorg_summaries"].docs.append({
        "created_at": now.replace(tzinfo=None), "model": "claude-sonnet-5-5",
        "created_by": "x@y", "usage": {"output_tokens": usd_output_tokens},
    })
    return db


def test_budget_check_blocks_only_when_enabled():
    db = _db_with_month_spend(2_000_000)  # 20 USD de sortie Sonnet 5.5
    # Pas de budget : aucun blocage.
    assert ps.check_ai_budget(db) is None
    # Budget depasse mais blocage desactive : informatif seulement.
    ps.set_ai_budget(db, 10, block_when_exceeded=False)
    assert ps.check_ai_budget(db) is None
    st = ps.ai_budget_status(db)
    assert st["exceeded"] is True and st["spent_usd"] == pytest.approx(20.0)
    # Blocage active : refus.
    ps.set_ai_budget(db, 10, block_when_exceeded=True)
    with pytest.raises(ps.ClaudeError) as exc:
        ps.check_ai_budget(db)
    assert str(exc.value) == "budget_exceeded"
    # Budget releve : de nouveau autorise.
    ps.set_ai_budget(db, 50, block_when_exceeded=True)
    assert ps.check_ai_budget(db)["exceeded"] is False


def test_stream_request_checks_budget_before_http(monkeypatch):
    db = _db_with_month_spend(2_000_000)
    ps.set_ai_budget(db, 1, block_when_exceeded=True)
    monkeypatch.setattr(ps, "ANTHROPIC_API_KEY", "sk-test")

    def _no_http(*a, **k):
        raise AssertionError("aucun appel HTTP ne doit partir")
    monkeypatch.setattr(ps.requests, "post", _no_http)
    with pytest.raises(ps.ClaudeError, match="budget_exceeded"):
        ps._claude_stream_request("s", "u", 100, db=db)


def test_aggregate_by_feature():
    now = datetime.now(timezone.utc)
    naive = now.replace(tzinfo=None)
    db = _Db()
    db["pcorg_summaries"].docs += [
        {"created_at": naive, "model": "claude-sonnet-5-5", "created_by": "morning-report@cockpit.lemans.org",
         "usage": {"input_tokens": 1000, "output_tokens": 1000}},
        {"created_at": naive, "model": "claude-sonnet-5-5", "created_by": "a@b",
         "usage": {"input_tokens": 0, "output_tokens": 0}},  # court-circuit : ignore
    ]
    db["pcorg_n1_retros"].docs.append(
        {"created_at": naive, "model": "claude-haiku-4-5", "usage": {"output_tokens": 10}})
    db["scan_analyses"].docs.append(
        {"created_at": ps._to_naive_paris(now), "model": "claude-sonnet-5", "kind": "frequentation",
         "usage": {"output_tokens": 10}})
    ps.record_ai_usage(db, "suggest_rule", "claude-haiku-4-5", {"output_tokens": 5})
    agg = ps.aggregate_ai_usage(db, now - timedelta(hours=1), now + timedelta(hours=1))
    assert set(agg["by_feature"]) == {"rapport_matinal", "retro_n1", "analyse_frequentation", "suggest_rule"}
    assert agg["counts"] == {"summaries": 1, "retros": 1, "scan_analyses": 1, "log": 1}
    assert agg["total_estimated_cost_usd"] > 0


# ----------------------------------------------------------------------------
# Corps de requete (structured outputs, effort, cache)
# ----------------------------------------------------------------------------

def test_request_body_structured_output_and_effort():
    schema = ps.build_output_schema(("a", "b"))
    assert schema == {"type": "object",
                      "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                      "required": ["a", "b"], "additionalProperties": False}
    body = ps.build_request_body("SYS", "USER", 1000, model="claude-sonnet-5-5",
                                 memory_block="MEM", output_schema=schema, effort="medium")
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": schema},
                                     "effort": "medium"}
    assert body["stream"] is True
    assert [b["text"] for b in body["system"]] == ["SYS", "MEM"]
    assert all(b["cache_control"] == {"type": "ephemeral"} for b in body["system"])


def test_request_body_skips_effort_on_unsupported_models():
    schema = ps.build_output_schema()
    body = ps.build_request_body("S", "U", 10, model="claude-haiku-4-5",
                                 output_schema=schema, effort="medium")
    assert "effort" not in body["output_config"]
    assert body["output_config"]["format"]["schema"]["required"] == list(ps.SECTION_KEYS)
    body = ps.build_request_body("S", "U", 10, model="claude-sonnet-5-5", system_cache=False)
    assert "output_config" not in body
    assert body["system"] == "S"


def test_call_claude_retry_goes_to_user_turn(monkeypatch):
    calls = []

    def fake(system_prompt, user_prompt, max_tokens, **kw):
        calls.append((system_prompt, user_prompt, max_tokens, kw))
        if len(calls) == 1:
            return '{"synthese": "tronq', {"output_tokens": 5}, "max_tokens"
        keys = {k: "ok" for k in ps.SECTION_KEYS}
        import json
        return json.dumps(keys), {"output_tokens": 7}, "end_turn"

    monkeypatch.setattr(ps, "_claude_stream_request", fake)
    sections, raw, usage = ps.call_claude("SYSTEM", "USER")
    assert sections["synthese"] == "ok"
    assert usage["output_tokens"] == 12 and usage["retried_for_truncation"]
    # System identique (cache preserve), consigne ajoutee au tour user.
    assert calls[1][0] == "SYSTEM"
    assert calls[1][1].startswith("USER") and "troncature" in calls[1][1]
    assert calls[0][3]["output_schema"]["required"] == list(ps.SECTION_KEYS)
    assert calls[0][3]["effort"] == ps.CLAUDE_EFFORT


def test_call_claude_falls_back_without_schema_on_400(monkeypatch):
    seen = []

    def fake(system_prompt, user_prompt, max_tokens, **kw):
        seen.append(kw.get("output_schema"))
        if kw.get("output_schema"):
            raise ps.ClaudeError("claude_http_400")
        return '{"a": "x"}', {}, "end_turn"

    monkeypatch.setattr(ps, "_claude_stream_request", fake)
    sections, _raw, _u = ps.call_claude("S", "U", section_keys=("a",))
    assert sections == {"a": "x"}
    assert seen[0] is not None and seen[1] is None


# ----------------------------------------------------------------------------
# Corrections utilisateur
# ----------------------------------------------------------------------------

def test_add_feedback_sets_sections_corrected(monkeypatch):
    captured = {}

    class Col:
        def find_one_and_update(self, filtre, update, return_document=None):
            captured["update"] = update
            return {"_id": "s1"}

    class Db:
        def __getitem__(self, name):
            return Col()

    ps.add_feedback(Db(), "s1", "synthese", "correction",
                    corrected_text="Nouveau texte", by_name="Alice")
    upd = captured["update"]
    assert upd["$push"]["feedback"]["corrected_text"] == "Nouveau texte"
    assert upd["$set"]["sections_corrected.synthese"]["text"] == "Nouveau texte"
    assert upd["$set"]["sections_corrected.synthese"]["by_name"] == "Alice"

    ps.add_feedback(Db(), "s1", "synthese", "comment", comment="rien")
    assert "$set" not in captured["update"]

    with pytest.raises(ValueError):
        ps.validate_feedback("synthese", "correction", corrected_text="")


def test_corrected_text_used_by_serializer_and_mail():
    now = datetime.now(timezone.utc)
    doc = {
        "_id": "s1", "event": "24H AUTOS", "year": 2026,
        "period_start": now, "period_end": now, "created_at": now,
        "sections": {k: "genere " + k for k in ps.SECTION_KEYS},
        "sections_corrected": {"synthese": {"text": "corrige", "by_name": "Bob", "ts": now}},
        "n1_retro": {"text": "NOTE INTERNE SECRETE"},
    }
    ser = ps._serialize_summary(doc, light=False)
    assert ser["sections"]["synthese"] == "genere synthese"  # original conserve
    assert ser["sections_corrected"]["synthese"]["text"] == "corrige"
    assert ps.effective_sections(ser)["synthese"] == "corrige"
    txt = pcorg_summary_mail.render_summary_text(ser)
    html = pcorg_summary_mail.render_summary_html(ser)
    assert "corrige" in txt and "genere synthese" not in txt
    assert "corrige" in html and "genere synthese" not in html
    # D2 : la note retro interne n'est jamais envoyee.
    assert "NOTE INTERNE" not in txt and "NOTE INTERNE" not in html


def test_serializer_replays_legacy_feedback_corrections():
    now = datetime.now(timezone.utc)
    doc = {"_id": "s", "sections": {"flux": "a"}, "feedback": [
        {"kind": "correction", "section": "flux", "target": "section", "corrected_text": "b1", "ts": now},
        {"kind": "correction", "section": "flux", "target": "section", "corrected_text": "b2", "ts": now},
        {"kind": "correction", "section": "flux", "target": "bullet:0", "corrected_text": "zz", "ts": now},
    ]}
    ser = ps._serialize_summary(doc, light=False)
    assert ser["sections_corrected"]["flux"]["text"] == "b2"


# ----------------------------------------------------------------------------
# Selection stratifiee
# ----------------------------------------------------------------------------

def test_stratified_selection_covers_whole_window():
    t0 = datetime(2026, 6, 13, 5, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=24)
    docs = [{"_id": i, "ts": (t0 + timedelta(minutes=10 * i)).replace(tzinfo=None),
             "status_code": 10, "comment_history": []} for i in range(144)]
    picked, n = ps._stratified_pick(docs, 24, t0, t1)
    assert len(picked) == 24 and n == 12
    hours = sorted({int((ps._as_aware_utc(d["ts"]) - t0).total_seconds() // 7200) for d in picked})
    assert hours == list(range(12))  # chaque tranche de 2 h representee
    # L'ancienne methode (plus recentes) n'aurait couvert que les 4 dernieres heures.
    assert min(d["ts"] for d in picked) < (t0 + timedelta(hours=4)).replace(tzinfo=None)


def test_stratified_selection_prefers_open_and_commented():
    t0 = datetime(2026, 6, 13, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=1)
    docs = [
        {"_id": "closed", "ts": t0 + timedelta(minutes=1), "status_code": 10, "comment_history": []},
        {"_id": "open", "ts": t0 + timedelta(minutes=2), "status_code": 1, "comment_history": []},
        {"_id": "busy", "ts": t0 + timedelta(minutes=3), "status_code": 10,
         "comment_history": [{}, {}, {}]},
    ]
    picked, _ = ps._stratified_pick(docs, 1, t0, t1, max_buckets=1)
    assert [d["_id"] for d in picked] == ["open"]
    picked, _ = ps._stratified_pick(docs, 2, t0, t1, max_buckets=1)
    assert {d["_id"] for d in picked} == {"open", "busy"}


# ----------------------------------------------------------------------------
# Memoire : filtrage par scope et regroupement par section
# ----------------------------------------------------------------------------

DIRECTIVES = [
    {"_id": "g", "active": True, "scope": {"event": None, "section": None, "phase": None, "year": None}, "content": "global"},
    {"_id": "ev", "active": True, "scope": {"event": "24H AUTOS", "section": None, "phase": None, "year": None}, "content": "event"},
    {"_id": "other", "active": True, "scope": {"event": "GPF", "section": None, "phase": None, "year": None}, "content": "autre event"},
    {"_id": "y26", "active": True, "scope": {"event": None, "section": None, "phase": None, "year": 2026}, "content": "2026"},
    {"_id": "y25", "active": True, "scope": {"event": None, "section": None, "phase": None, "year": 2025}, "content": "2025"},
    {"_id": "course", "active": True, "scope": {"event": None, "section": None, "phase": "course", "year": None}, "content": "course"},
    {"_id": "montage", "active": True, "scope": {"event": None, "section": None, "phase": "montage", "year": None}, "content": "montage"},
    {"_id": "flux", "active": True, "scope": {"event": None, "section": "flux", "phase": None, "year": None}, "content": "flux"},
    {"_id": "off", "active": False, "scope": {}, "content": "inactive"},
]


def _select(**ctx):
    q = pcorg_ai_memory.build_scope_query(**ctx)
    return {d["_id"] for d in DIRECTIVES if _matches(d, q)}


def test_memory_scope_filtering():
    assert _select(event="24H AUTOS", year=2026, phase="course") == {"g", "ev", "y26", "course", "flux"}
    # Phase inconnue : aucune directive scopee par phase.
    assert _select(event="24H AUTOS", year=2026, phase=None) == {"g", "ev", "y26", "flux"}
    # Rapport tous evenements / toutes annees : directives globales seulement.
    assert _select(event=None, year=None, phase=None) == {"g", "flux"}


def test_memory_block_groups_sections_after_globals():
    block = pcorg_ai_memory.format_directives_block([DIRECTIVES[7], DIRECTIVES[0]])
    assert block.index("Directives generales") < block.index("Pour la section flux")
    assert block.index("global") < block.index("Pour la section flux") < block.rindex("flux")


# ----------------------------------------------------------------------------
# Job de generation en arriere-plan
# ----------------------------------------------------------------------------

def _wait_job(job_id, timeout=5.0):
    t = time.time()
    while time.time() - t < timeout:
        j = ps.get_summary_job(job_id)
        if j and j["status"] in ("done", "error"):
            return j
        time.sleep(0.02)
    raise AssertionError("job non termine")


def test_summary_job_success_and_progress():
    gate = threading.Event()
    seen = {}

    def runner(db, on_step=None, on_progress=None, on_thinking=None, **params):
        seen.update(params)
        on_step("writing", "Redaction du rapport")
        on_progress("x" * 4500, 900)
        gate.wait(2)
        now = datetime.now(timezone.utc)
        return {"_id": "abc", "sections": {"synthese": "ok"}, "period_start": now,
                "period_end": now, "created_at": now}

    job_id, already = ps.start_summary_job(None, {"event": "E"}, owner="u1@x", runner=runner)
    assert already is False
    # Un second lancement du meme utilisateur rattache le job en cours.
    job2, already2 = ps.start_summary_job(None, {"event": "E"}, owner="u1@x", runner=runner)
    assert job2 == job_id and already2 is True
    time.sleep(0.1)
    mid = ps.get_summary_job(job_id)
    assert mid["status"] == "running" and mid["step_key"] == "writing"
    assert mid["chars"] == 4500 and 25 < mid["progress"] < 95
    # Un autre utilisateur ne voit pas le job.
    assert ps.get_summary_job(job_id, owner="autre@x") is None
    gate.set()
    done = _wait_job(job_id)
    assert done["status"] == "done" and done["result_id"] == "abc"
    assert done["summary"]["sections"]["synthese"] == "ok"
    assert seen == {"event": "E"}


def test_summary_job_error_is_reported():
    def runner(db, **kw):
        raise ps.ClaudeError("budget_exceeded")
    job_id, _ = ps.start_summary_job(None, {}, owner="u2@x", runner=runner)
    j = _wait_job(job_id)
    assert j["status"] == "error" and j["error"] == "budget_exceeded"
    # Le verrou utilisateur est libere : un nouveau job peut partir.
    job2, already = ps.start_summary_job(None, {}, owner="u2@x", runner=runner)
    assert already is False and job2 != job_id
    _wait_job(job2)


# ----------------------------------------------------------------------------
# Retro N-1 : cle de cache arrondie a l'heure
# ----------------------------------------------------------------------------

def test_retro_cache_key_rounded_to_hour():
    a = datetime(2025, 6, 14, 7, 3, 12, tzinfo=timezone.utc)
    b = datetime(2025, 6, 14, 7, 41, 59, tzinfo=timezone.utc)
    ka = ps._retro_cache_key("E", 2025, a, a + timedelta(days=1))
    kb = ps._retro_cache_key("E", 2025, b, b + timedelta(days=1))
    assert ka == kb
    assert ka["period_start"] == "2025-06-14T07:00:00+00:00"
