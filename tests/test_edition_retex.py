"""RETEX de fin d'edition : calcul pur, tolerance des blocs, contrat, rendu."""

import json
from datetime import date, datetime, timezone

import pytest

import edition_retex as er
import pcorg_summary
from test_situation_briefing import ExplodingDb, MemDb


def _f(i, ts, cat="PCO.Secours", urg=None, close=None, status=10, **extra):
    d = {"_id": "f%d" % i, "ts": ts, "category": cat, "niveau_urgence": urg,
         "status_code": status, "close_ts": close, "text": "texte %d" % i}
    d.update(extra)
    return d


RACE = date(2026, 9, 26)


def test_pcorg_stats_days_offsets_durations_and_majors():
    fiches = [
        # 25/09 22:30 UTC = 26/09 00:30 Paris -> jour de course (J)
        _f(1, datetime(2026, 9, 25, 22, 30), urg="UA", close=datetime(2026, 9, 25, 23, 0)),
        _f(2, datetime(2026, 9, 26, 10, 0), cat="PCO.Flux", close=datetime(2026, 9, 26, 10, 20)),
        # fiche Prysm ouverte : close_ts sentinelle 9000, pas une duree
        _f(3, datetime(2026, 9, 27, 8, 0), cat="PCO.Flux", status=1,
           close=datetime(9000, 1, 1), is_incident=True),
        _f(4, datetime(2026, 9, 24, 8, 0), content_category={"field_sos": True}),
    ]
    st = er.pcorg_stats(fiches, RACE)
    assert st["total"] == 4
    assert st["ouvertes_restantes"] == 1
    assert st["majeures"] == 2 and st["sos_tablette"] == 1
    days = {d["date"]: d for d in st["par_jour"]}
    assert days["2026-09-26"]["offset"] == 0 and days["2026-09-26"]["total"] == 2
    assert days["2026-09-24"]["offset"] == -2 and days["2026-09-24"]["jour"] == "jeu."
    assert days["2026-09-27"]["majeures"] == 1
    assert st["duree_min"]["n"] == 2            # la sentinelle est ecartee
    assert st["duree_min_par_categorie"]["PCO.Flux"]["mediane"] == 20.0
    assert [m["texte"] for m in st["fiches_majeures"]] == ["texte 1", "texte 3"]
    assert st["fiches_majeures"][0]["offset"] == 0
    assert st["par_heure"]["00h"] == 1


def test_pcorg_stats_light_has_no_detail():
    st = er.pcorg_stats([_f(1, datetime(2026, 9, 26, 10, 0))], RACE, detail=False)
    assert "fiches_majeures" not in st and "par_categorie" not in st["par_jour"][0]


def test_build_is_tolerant_to_failing_blocks(monkeypatch):
    monkeypatch.setattr(er, "edition_info", lambda db, e, y, now=None: {
        "event": e, "year": y, "race_date": "2026-09-26", "public_days": ["2026-09-26"],
        "window_start": None, "window_end": None, "has_param": True})

    def boom(*a):
        raise RuntimeError("mongo KO")

    for name in ("block_main_courante", "block_comparaison", "block_field", "block_meteo",
                 "block_billetterie", "block_presents_live"):
        monkeypatch.setattr(er, name, boom)
    monkeypatch.setattr(er, "block_frequentation", lambda db, e, y: {
        "perimetre_comparable": False, "portes_en_service_par_edition": {"2026": 19, "2025": 18},
        "editions": [{"source": "scan_import"}, {"source": "collecte_temps_reel"}]})
    ds = er.build(ExplodingDb(), "24H MOTOS", 2026)
    assert ds["blocs"]["frequentation"]["statut"] == "ok"
    assert ds["blocs"]["alertes"]["statut"] == "indisponible"   # fenetre inconnue
    assert "main_courante" in ds["blocs_indisponibles"]
    assert any("NON comparables" in a for a in ds["avertissements"])
    assert any("source de mesure" in a for a in ds["avertissements"])
    json.dumps(ds)


def test_alertes_block_refuses_expired_history():
    start = datetime(2025, 6, 1, tzinfo=timezone.utc)
    end = datetime(2025, 6, 20, tzinfo=timezone.utc)

    class Col:
        def __init__(self, docs):
            self.docs = docs

        def find(self, q=None, p=None):
            return iter([])

        def find_one(self, q=None, sort=None):
            return self.docs[0] if self.docs else None

    class Db:
        cols = {"cockpit_alert_history": Col([{"createdAt": datetime(2026, 9, 23)}]),
                "cockpit_alert_definitions": Col([]), "cockpit_active_alerts": Col([])}

        def __getitem__(self, n):
            return self.cols[n]

    with pytest.raises(ValueError):
        er.block_alertes(Db(), start, end)


def test_prompt_contract_and_caveats():
    ds = {"edition": {"event": "E", "year": 2026, "race_date": "2026-09-26"},
          "avertissements": ["reserve 1"],
          "blocs": {"meteo": {"statut": "indisponible", "erreur": "rien"},
                    "field": {"statut": "ok", "data": {"sos": 2}}}}
    system, user = er.build_prompts(ds)
    for k in er.SECTION_KEYS:
        assert '"%s"' % k in system
    payload = json.loads(user.split("```json\n")[1].split("\n```")[0])
    assert payload["avertissements"] == ["reserve 1"]
    assert payload["blocs"]["meteo"] == {"statut": "indisponible", "raison": "rien"}
    assert payload["blocs"]["field"]["donnees"] == {"sos": 2}


def test_generate_uses_large_budget_contract_and_versions(monkeypatch):
    calls = []

    def fake_stream(system, user, max_tokens, **kw):
        calls.append(max_tokens)
        body = {k: "- constat " + k for k in er.SECTION_KEYS}
        body["synthese"] = "Edition calme."
        body["intrus"] = "ignore"
        return json.dumps(body), {"input_tokens": 100, "output_tokens": 50}, "end_turn"

    monkeypatch.setattr(pcorg_summary, "_claude_stream_request", fake_stream)
    monkeypatch.setattr(pcorg_summary, "check_ai_budget", lambda db: None, raising=False)
    monkeypatch.setattr(pcorg_summary, "record_ai_usage", lambda *a, **k: None, raising=False)
    db = MemDb()
    ds = {"edition": {"event": "E", "year": 2026}, "avertissements": [], "blocs": {}}
    d1 = er.generate(db, "E", 2026, by="admin", dataset=ds)
    d2 = er.generate(db, "E", 2026, by="admin", dataset=ds)
    assert calls == [er.RETEX_MAX_TOKENS, er.RETEX_MAX_TOKENS]
    assert set(d1["sections"]) == set(er.SECTION_KEYS)
    assert d1["sections"]["synthese"] == "Edition calme."
    assert (d1["version"], d2["version"]) == (1, 2)
    assert len(db[er.COLLECTION].docs) == 2             # jamais ecrase


def test_structured_output_refused_falls_back_without_schema(monkeypatch):
    calls = []

    def fake_stream(system, user, max_tokens, on_progress=None, model=None, db=None,
                    output_schema=None, effort=None):
        calls.append(output_schema is not None)
        if output_schema is not None:
            raise pcorg_summary.ClaudeError("claude_http_400")
        return json.dumps({"synthese": "ok"}), {}, "max_tokens"

    monkeypatch.setattr(pcorg_summary, "_claude_stream_request", fake_stream)
    monkeypatch.setattr(pcorg_summary, "record_ai_usage", lambda *a, **k: None, raising=False)
    doc = er.generate(MemDb(), "E", 2026, dataset={"edition": {}, "avertissements": [], "blocs": {}})
    assert calls == [True, False]
    assert doc["truncated"] is True and doc["sections"]["synthese"] == "ok"


def test_render_html_escapes_everything():
    doc = {"event": "E<script>", "year": 2026, "version": 1, "model": "m",
           "created_at": datetime(2026, 9, 29, 10, 0), "created_by": "a@b",
           "sections": {"synthese": "<img src=x onerror=alert(1)> **42**\n- puce"},
           "dataset": {"edition": {"race_date": "2026-09-26"}, "avertissements": ["<b>r</b>"],
                       "blocs": {"main_courante": {"statut": "ok", "data": {
                           "par_jour": [{"date": "2026-09-26", "jour": "sam.", "offset": 0,
                                         "total": 3, "majeures": 1}],
                           "par_categorie": {"<x>": 3}, "par_urgence": {"UA": 1}}}},
                       "blocs_indisponibles": []}}
    out = er.render_html(doc)
    assert "<img" not in out and "<script>" not in out and "<b>r</b>" not in out
    assert "<strong>42</strong>" in out and "<li>puce</li>" in out
    assert "&lt;x&gt;" in out
