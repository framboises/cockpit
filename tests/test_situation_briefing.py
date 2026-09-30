"""Briefing de situation : tolerance aux sources, contrat JSON, releve."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import pcorg_summary
import situation_briefing as sb
from conftest import FakeDb


class ExplodingDb:
    """Toute lecture leve : simule une base injoignable."""

    def __getitem__(self, name):
        raise RuntimeError("mongo injoignable (%s)" % name)


class _Inserted:
    def __init__(self, i):
        self.inserted_id = i


class MemCol:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        doc = dict(doc)
        doc["_id"] = "id%d" % (len(self.docs) + 1)
        self.docs.append(doc)
        return _Inserted(doc["_id"])

    def create_index(self, *a, **k):
        return None

    def count_documents(self, q):
        return sum(1 for d in self.docs if all(d.get(k) == v for k, v in q.items()))


class MemDb:
    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        return self.cols.setdefault(name, MemCol())


NOW = datetime(2026, 9, 27, 13, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Tolerance
# ---------------------------------------------------------------------------

def test_all_sources_fail_but_briefing_is_built():
    ctx = sb.build_context(ExplodingDb(), now=NOW, event="24H CAMIONS", year=2026)
    assert set(ctx["sources"]) == set(sb.SOURCE_LABELS)
    assert all(s["statut"] == "indisponible" for s in ctx["sources"].values())
    assert sorted(ctx["sources_indisponibles"]) == sorted(sb.SOURCE_LABELS)
    assert all(s["erreur"] for s in ctx["sources"].values())
    json.dumps(ctx)  # stockable et transmissible tel quel


def test_event_detection_failure_is_reported_not_raised():
    ctx = sb.build_context(ExplodingDb(), now=NOW)
    assert ctx["event"] is None
    assert "detection_evenement_erreur" in ctx


def test_one_failing_source_does_not_affect_others():
    def boom(*a):
        raise KeyError("waze")

    def ok(*a):
        return {"quand": datetime(2026, 9, 27, 12, 0), "n": float("nan")}

    fakes = {name: ok for name in sb.SOURCE_LABELS}
    fakes["trafic"] = boom
    ctx = sb.build_context(ExplodingDb(), now=NOW, event="E", year=2026, sources=fakes)
    assert ctx["sources"]["trafic"]["statut"] == "indisponible"
    assert ctx["sources_indisponibles"] == ["trafic"]
    data = ctx["sources"]["meteo"]["data"]
    # datetime Mongo (naif UTC) -> heure de Paris naive ; NaN -> None
    assert data == {"quand": "2026-09-27T14:00:00", "n": None}


def test_naive_now_is_paris_time():
    fakes = {name: (lambda *a: {}) for name in sb.SOURCE_LABELS}
    ctx = sb.build_context(ExplodingDb(), now=datetime(2026, 9, 27, 15, 0), event="E", year=1,
                           sources=fakes)
    assert ctx["genere_a"] == "2026-09-27T15:00"


# ---------------------------------------------------------------------------
# Sources sur double Mongo
# ---------------------------------------------------------------------------

def _fiche(i, minutes_ago, cat="PCO.Secours", urg=None, closed=False, incident=False):
    return {"_id": "f%d" % i, "event": "E", "year": 2026, "category": cat,
            "niveau_urgence": urg, "is_incident": incident,
            "ts": (NOW - timedelta(minutes=minutes_ago)).replace(tzinfo=None),
            "status_code": 10 if closed else 1, "text": "fiche %d" % i}


def test_main_courante_counts_and_oldest_majors_first():
    db = FakeDb(pcorg=[
        _fiche(1, 30, urg="UA"),
        _fiche(2, 60 * 20, urg="EU"),            # majeure ouverte ancienne
        _fiche(3, 10, cat="PCO.Flux"),
        _fiche(4, 100, closed=True, urg="UA"),   # majeure close, recente
        _fiche(5, 60 * 30, cat="PCO.Flux"),      # hors fenetre 3 h, ouverte
        _fiche(6, -30),                          # dans le futur (simulation) : exclue des 3 h
    ])
    d = sb.src_main_courante(db, "E", 2026, NOW)
    assert d["ouvertes"]["total"] == 5
    assert d["ouvertes"]["ouvertes_depuis_plus_de_12h"] == 2
    assert [f["id"] for f in d["ouvertes"]["majeures_les_plus_anciennes"]] == ["f2", "f1"]
    recent = d["dernieres_3h"]
    assert recent["total"] == 3 and recent["closes"] == 1
    assert [f["id"] for f in recent["majeures"]] == ["f1", "f4"]
    assert [f["id"] for f in recent["autres"]] == ["f3"]


def test_main_courante_requires_event():
    with pytest.raises(ValueError):
        sb.src_main_courante(FakeDb(), None, None, NOW)


# ---------------------------------------------------------------------------
# Prompt et contrat JSON
# ---------------------------------------------------------------------------

def _ctx():
    fakes = {name: (lambda *a: {"x": 1}) for name in sb.SOURCE_LABELS}
    fakes["meteo"] = lambda *a: (_ for _ in ()).throw(ValueError("meteo morte"))
    return sb.build_context(ExplodingDb(), now=NOW, event="E", year=2026, sources=fakes)


def test_prompt_marks_unavailable_sources_and_lists_contract():
    system, user = sb.build_prompts(_ctx())
    for key in sb.SECTION_KEYS:
        assert '"%s"' % key in system
    payload = json.loads(user.split("```json\n")[1].split("\n```")[0])
    assert payload["sources"]["meteo"] == {"statut": "indisponible", "raison": "meteo morte"}
    assert payload["sources"]["trafic"] == {"statut": "ok", "donnees": {"x": 1}}
    assert "donnee indisponible" in system


def test_contract_parsing_keeps_only_briefing_keys():
    raw = json.dumps({"situation": "Calme.", "points_chauds": ["a", "b"],
                      "synthese": "cle pcorg parasite", "consignes_releve": "- x"})
    parsed = pcorg_summary._parse_sections(raw, sb.SECTION_KEYS)
    assert set(parsed) == set(sb.SECTION_KEYS)
    assert parsed["points_chauds"] == "- a\n- b"
    assert parsed["a_surveiller_6h"] == ""


def test_generate_passes_section_keys_persists_and_records_usage(monkeypatch):
    seen = {}

    def fake_call(system, user, **kw):
        seen.update(kw)
        return {k: "ok " + k for k in kw["section_keys"]}, "{}", {"input_tokens": 10, "output_tokens": 5}

    recorded = []
    monkeypatch.setattr(pcorg_summary, "call_claude", fake_call)
    monkeypatch.setattr(pcorg_summary, "check_ai_budget", lambda db: None, raising=False)
    monkeypatch.setattr(pcorg_summary, "record_ai_usage",
                        lambda db, feature, model, usage, meta=None: recorded.append((feature, usage)),
                        raising=False)
    db = MemDb()
    doc = sb.generate_briefing(db, _ctx(), by="moi@test")
    assert tuple(seen["section_keys"]) == sb.SECTION_KEYS
    assert doc["sections"]["situation"] == "ok situation"
    assert db[sb.COLLECTION].docs[0]["event"] == "E"
    assert db[sb.COLLECTION].docs[0]["context"]["sources"]["meteo"]["statut"] == "indisponible"
    assert recorded == [("situation_briefing", {"input_tokens": 10, "output_tokens": 5})]


def test_budget_checked_here_when_call_claude_ignores_db(monkeypatch):
    called = []

    def blocked(db):
        raise pcorg_summary.ClaudeError("budget_exceeded")

    monkeypatch.setattr(pcorg_summary, "check_ai_budget", blocked, raising=False)
    monkeypatch.setattr(pcorg_summary, "call_claude",
                        lambda s, u, on_progress=None, model=None, section_keys=None: called.append(1))
    with pytest.raises(pcorg_summary.ClaudeError):
        sb.generate_briefing(MemDb(), _ctx())
    assert called == []


def test_db_is_forwarded_when_call_claude_accepts_it(monkeypatch):
    seen = {}

    def fake(s, u, on_progress=None, model=None, section_keys=None, db=None):
        seen["db"] = db
        return None, "texte libre", {}

    monkeypatch.setattr(pcorg_summary, "check_ai_budget",
                        lambda db: pytest.fail("double controle de budget"), raising=False)
    monkeypatch.setattr(pcorg_summary, "call_claude", fake)
    db = MemDb()
    doc = sb.generate_briefing(db, _ctx())
    assert seen["db"] is db
    assert doc["sections"] is None and doc["raw_text"] == "texte libre"


# ---------------------------------------------------------------------------
# Releve
# ---------------------------------------------------------------------------

def test_due_slot_window_and_dedup():
    times = ["06:45", "14:45", "22:45"]
    assert sb.due_slot(times, datetime(2026, 9, 29, 6, 50)) == "2026-09-29 06:45"
    assert sb.due_slot(times, datetime(2026, 9, 29, 7, 0)) is None          # fenetre depassee
    assert sb.due_slot(times, datetime(2026, 9, 29, 6, 44)) is None         # trop tot
    assert sb.due_slot(times, datetime(2026, 9, 29, 6, 50),
                       already=["2026-09-29 06:45"]) is None                # deja envoye


def test_due_slot_crosses_midnight():
    assert sb.due_slot(["23:55"], datetime(2026, 9, 30, 0, 5)) == "2026-09-29 23:55"


def test_validate_times():
    assert sb.validate_times([" 14:45", "06:45", "06:45"]) == ["06:45", "14:45"]
    for bad in (["25:00"], ["6:45"], [], "06:45"):
        with pytest.raises(ValueError):
            sb.validate_times(bad)


def test_mail_html_escapes_and_flags_stale_presents():
    ctx = _ctx()
    ctx["event"] = "<b>E</b>"
    ctx["sources"]["presents"] = {"statut": "ok", "data": {"presents_actuels": 0,
                                                           "releve_perime": True,
                                                           "dernier_releve": "21:11"}}
    doc = {"context": ctx, "sections": {"situation": "<script>x</script> **12**"}}
    out = sb.render_briefing_html(doc)
    assert "<script>x" not in out and "&lt;script&gt;" in out
    assert "&lt;b&gt;E&lt;/b&gt;" in out
    assert "non fiable" in sb.render_briefing_text(doc)
