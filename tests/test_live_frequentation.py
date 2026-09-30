"""Frequentation rejouee depuis les archives du controle d'acces live.

Couvre : nommage des archives, remises a zero, rejeu du calcul du dashboard
(vehicules cumules depuis la remise a zero, jamais depuis minuit ; releves
fantomes d'avant remise a zero ecartes ; piege tranche Paris / UTC), pic =
plus haut releve, creneaux 15 min etiquetes a leur fin, perimetre des portes,
priorite des sources (defaut inchange) et RETEX.
"""

import os
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from conftest import FakeDb  # noqa: E402

import edition_retex as er  # noqa: E402
import live_frequentation as lf  # noqa: E402
import presents_etat as pe  # noqa: E402
import scan_frequentation as sf  # noqa: E402

TAG = "TEST_EV_2026"


def _cpt(ts, current, entries, exits=0, loc="628"):
    return {"timestamp": ts, "requested_location_id": loc, "requested_location_type": "Area",
            "current": str(current), "entries": str(entries), "exits": str(exits)}


def _tx(tranche, cp, gate, e=0, s=0, ve=0, vs=0, err=0):
    return {"tranche": tranche, "checkpoint_id": cp, "gate_name": gate, "entrees": e,
            "sorties": s, "entrees_vehicules": ve, "sorties_vehicules": vs, "erreurs": err}


def _db(extra=None, global_doc=None):
    cols = {
        "evenement": [{"nom": "TEST EV", "short": "TEV"}],
        "parametrages": [{"event": "TEST EV", "year": "2026", "data": {"globalHoraires": {
            "race": "2026-09-26T13:00:00.000Z",
            "dates": [{"date": "2026-09-26"}, {"date": "2026-09-27"}]}}}],
        "data_access": [global_doc or {"_id": "___GLOBAL___", "evenement": "AUTRE"}],
        "hsh_archive_structure_" + TAG: [
            {"location_type": "Checkpoint", "location_id": "c1", "parent_area": {"id": "628"}},
            {"location_type": "Checkpoint", "location_id": "c2", "parent_area": {"id": "628"}},
            {"location_type": "Checkpoint", "location_id": "c3", "parent_area": {"id": "926"}},
            {"location_type": "Gate", "location_name": "PORTE A", "parent_area": {"id": "628"}},
            {"location_type": "Gate", "location_name": "P OUEST", "parent_area": {"id": "926"}},
        ],
        "hsh_archive_compteurs_" + TAG: [
            # Edition d'une autre annee rangee dans la meme archive : hors fenetre.
            _cpt(datetime(2025, 9, 20, 12, 0, 5), 99999, 999999),
            # Compteur pas remis a zero : fantomes.
            _cpt(datetime(2026, 9, 22, 10, 0, 5), 20000, 300000, 280000),
            _cpt(datetime(2026, 9, 22, 10, 3, 5), 20010, 300010, 280000),
            # Remise a zero le 23/09 04h33 UTC = 06h33 Paris.
            _cpt(datetime(2026, 9, 23, 4, 33, 14), 1, 1, 0),
            _cpt(datetime(2026, 9, 23, 4, 36, 14), 3, 5, 2),
            # Jour de course : 12h02 UTC = 14h02 Paris.
            _cpt(datetime(2026, 9, 26, 12, 2, 9), 900, 1000, 100),
            _cpt(datetime(2026, 9, 26, 12, 5, 9), 950, 1100, 150),
            _cpt(datetime(2026, 9, 26, 12, 8, 9), 940, 1120, 180),
            # Lendemain : plus aucun passage, compteur fige.
            _cpt(datetime(2026, 9, 27, 8, 0, 9), 940, 1120, 180),
        ],
        "hsh_archive_tx_" + TAG: [
            _tx(datetime(2026, 9, 22, 12, 0), "c1", "PORTE A", ve=50),     # avant la remise a zero
            _tx(datetime(2026, 9, 23, 6, 30), "c1", "PORTE A", ve=10),     # apres (tranche 06:30 >= 06:30)
            _tx(datetime(2026, 9, 26, 14, 0), "c1", "PORTE A", e=100, s=10, ve=20, vs=5, err=3),
            _tx(datetime(2026, 9, 26, 14, 5), "c2", "PORTE A", ve=7),
            _tx(datetime(2026, 9, 26, 14, 0), "c3", "P OUEST", e=40),      # hors enceinte
        ],
    }
    cols.update(extra or {})
    return FakeDb(**cols)


# ---------------------------------------------------------------------------
# Nommage des archives
# ---------------------------------------------------------------------------

def test_archive_tag_matches_archive_route():
    assert lf.archive_tag("LE MANS CLASSIC", 2026) == "LE_MANS_CLASSIC_2026"
    assert lf.archive_tag(" 24H AUTOS ", "2026") == "24H_AUTOS_2026"


def test_archive_collections_try_short_name_and_next_year():
    existing = {"hsh_archive_tx_LMC_2027", "hsh_archive_compteurs_LE_MANS_CLASSIC_2026",
                "hsh_archive_tx_LE_MANS_CLASSIC_2026", "hsh_archive_tx_GPF_2026"}
    out = lf.archive_collections(existing, ["LE MANS CLASSIC", "LMC"], 2026)
    assert out["tx"] == ["hsh_archive_tx_LE_MANS_CLASSIC_2026", "hsh_archive_tx_LMC_2027"]
    assert out["compteurs"] == ["hsh_archive_compteurs_LE_MANS_CLASSIC_2026"]


def test_event_names_both_directions():
    db = _db()
    assert lf.event_names(db, "TEST EV") == ["TEST EV", "TEV"]
    assert lf.event_names(db, "TEV") == ["TEV", "TEST EV"]


def test_has_live_archive_uses_window_not_name():
    db = _db()
    assert lf.has_live_archive(db, "TEST EV", 2026) is True
    assert lf.has_live_archive(db, "TEV", 2026) is True          # sigle accepte
    # L'archive << 2026 >> ne contient que 2026 : l'edition 2025 n'y est pas.
    db2 = _db(extra={"parametrages": [
        {"event": "TEST EV", "year": "2025",
         "data": {"globalHoraires": {"race": "2025-09-20T13:00:00.000Z"}}}]})
    assert lf.has_live_archive(db2, "TEST EV", 2025) is False
    assert lf.has_live_archive(_db(), "INCONNU", 2026) is False


# ---------------------------------------------------------------------------
# Remises a zero : fonction partagee avec le direct
# ---------------------------------------------------------------------------

def test_detect_resets_ignores_isolated_glitch():
    t = [datetime(2026, 9, 26, 10, i) for i in range(6)]
    assert pe.detect_resets([(t[0], "100"), (t[1], "99"), (t[2], "105")]) == []
    assert pe.detect_resets([(t[0], "100"), (t[1], "1"), (t[2], "4")]) == [t[1]]
    assert pe.detect_resets([(t[0], "100"), (t[1], "1")]) == [t[1]]      # derniere chute retenue
    assert pe.detect_resets([(t[0], "N/A"), (t[1], ""), (t[2], "7")]) == []


def test_counter_baseline_unchanged_on_live_collection():
    pe._BASELINE_CACHE.clear()
    act = datetime(2026, 9, 21, 20, 0)
    db = FakeDb(data_access=[
        _cpt(datetime(2026, 9, 21, 20, 15), 21952, 300485),
        _cpt(datetime(2026, 9, 23, 4, 33), 1, 1),
        _cpt(datetime(2026, 9, 23, 4, 36), 3, 5),
    ])
    assert pe.counter_baseline(db, "628", "Area", act) == datetime(2026, 9, 23, 4, 33)
    pe._BASELINE_CACHE.clear()
    db2 = FakeDb(data_access=[_cpt(datetime(2026, 9, 21, 20, 15), 10, 10)])
    assert pe.counter_baseline(db2, "628", "Area", act) == act


# ---------------------------------------------------------------------------
# Rejeu
# ---------------------------------------------------------------------------

def test_quarter_labels_are_end_of_slot():
    assert lf.quarter_end(datetime(2026, 9, 26, 20, 7, 3)) == datetime(2026, 9, 26, 20, 15)
    assert lf.quarter_end(datetime(2026, 9, 26, 20, 0)) == datetime(2026, 9, 26, 20, 0)
    assert lf.tranche_quarter(datetime(2026, 9, 26, 19, 55)) == datetime(2026, 9, 26, 20, 0)
    assert lf.tranche_quarter(datetime(2026, 9, 26, 20, 0)) == datetime(2026, 9, 26, 20, 15)


def test_replay_matches_dashboard_formula():
    db = _db()
    raw = lf.load_live_edition(db, "TEST EV", 2026)
    assert raw["source"] == "live_controle"
    assert raw["presents_method"] == lf.PRESENTS_COMPTEUR
    day = raw["days_raw"]["2026-09-26"]
    # 12:05:09 UTC = 14:05:09 Paris : tranches 14:00 et 14:05 comptees, plus
    # celle du 23/09 06:30 (cumul depuis la remise a zero, pas depuis minuit),
    # pas celle du 22/09 (avant la remise a zero).
    # vehicules = 10 + (20 - 5) + 7 = 32 ; present = 950 - 32.
    assert day["peak"] == 918
    assert day["peak_at"] == datetime(2026, 9, 26, 14, 5, 9)
    assert raw["valid_from"] == "2026-09-23T06:33:14"
    assert raw["phantom_days"] == ["2026-09-22"]


def test_tranche_utc_trap_excludes_future_tranche():
    """Releve de 12:02 UTC = 14:02 Paris : la tranche 14:05 (etiquetee UTC mais
    en heure de Paris) n'est PAS encore comptee. Comparer en UTC brut
    l'aurait comptee (12:02 < 14:05 faux) ou ignoree a tort."""
    db = _db()
    src = lf.find_sources(db, "TEST EV", 2026)
    parents, _ = lf._read_structure(db, src["structure"])
    tx = lf._read_tx(db, src["tx"], datetime(2026, 9, 1), datetime(2026, 10, 1))
    readings = lf._read_counter(db, src["compteurs"], lf.DEFAULT_LOCATION,
                                datetime(2026, 9, 1), datetime(2026, 10, 1))
    rp = lf.replay_presents(readings, pe.vehicle_prefixes(tx, parents), "628",
                            src["window"], datetime(2026, 9, 26, 22, 0))
    at_1402 = next(p for p in rp["points"] if p[0] == datetime(2026, 9, 26, 12, 2, 9))
    assert at_1402[3] == 25 and at_1402[1] == 875


def test_peak_is_highest_reading_not_curve_value():
    db = _db()
    raw = lf.load_live_edition(db, "TEST EV", 2026)
    rec = {r["date"]: r for r in raw["records"]}
    # Quart d'heure 14:15 : dernier releve 14:08 (908), mais le pic du jour
    # est le releve de 14:05 (918).
    assert rec["2026-09-26T14:15:00"]["present"] == 908
    assert raw["days_raw"]["2026-09-26"]["peak"] == 918


def test_door_perimeter_and_vehicles_excluded():
    db = _db()
    raw = lf.load_live_edition(db, "TEST EV", 2026, with_door_series=True)
    names = [g["name"] for g in raw["gates"]]
    assert names == ["PORTE A"]                                  # P OUEST : autre Area
    porte = raw["gates"][0]
    assert porte["entrees"] == 80 and porte["sorties"] == 5      # vehicules exclus
    assert porte["entrees_vehicules"] == 87 and porte["erreurs"] == 3
    serie = {r["date"]: r for r in raw["door_series"]["PORTE A"]}
    assert serie["2026-09-26T14:15:00"]["entree"] == 80


def test_to_edition_flags_unmeasured_days():
    db = _db()
    raw = lf.load_live_edition(db, "TEST EV", 2026)
    ed = lf.to_edition(db, raw, date(2026, 9, 26), True, geo_index={})
    days = {d["date"]: d for d in ed["days"]}
    assert days["2026-09-22"]["measured"] is False
    assert days["2026-09-22"]["unmeasured_reason"] == "compteur_non_remis_a_zero"
    assert days["2026-09-27"]["measured"] is False
    assert days["2026-09-27"]["unmeasured_reason"] == "compteur_fige"
    d0 = days["2026-09-26"]
    assert d0["measured"] and d0["offset"] == 0 and d0["peak_present"] == 918
    assert d0["peak_hour"] == "14:05"
    assert ed["source"] == "live_controle" and ed["granularity"] == "15min"
    assert ed["doors"] == 1 and ed["peak_basis"] == "releve"
    assert ed["hourly"] and all("slot" in h for h in ed["hourly"])


def test_corrections_only_from_global_of_this_edition():
    g = {"_id": "___GLOBAL___", "evenement": "TEST EV",
         "activation_timestamp": datetime(2026, 9, 21, 20, 13),
         "locations_selectionnees": [{"id": "628", "type": "Area", "name": "ENCEINTE GENERALE"}],
         "compteur_principal_id": "628", "corrections_compteurs": {"628": 100}}
    raw = lf.load_live_edition(_db(global_doc=g), "TEST EV", 2026)
    assert raw["corrections"]["appliquees"] is True
    assert raw["days_raw"]["2026-09-26"]["peak"] == 818


def test_readings_outside_scan_period_fall_back_on_scan_balance():
    """SUPERBIKE 2026 : releves du compteur du 29 au 31/03, scans les 4-5/04."""
    db = _db(extra={
        "hsh_archive_compteurs_" + TAG: [_cpt(datetime(2026, 9, 17, 10, 0), 3367, 5000)],
    })
    raw = lf.load_live_edition(db, "TEST EV", 2026)
    assert raw["presents_method"] == lf.PRESENTS_SOLDE
    # solde des personnes des portes de l'enceinte : 80 entrees - 5 sorties
    assert raw["days_raw"]["2026-09-26"]["peak"] == 75


# ---------------------------------------------------------------------------
# Priorite des sources
# ---------------------------------------------------------------------------

def test_default_priority_never_touches_live(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("le mode par defaut ne doit pas lire le live")
    monkeypatch.setattr(sf, "_load_live", boom)
    db = FakeDb(historique_controle=[{"type": "complet", "event": "E", "year": 2026, "complet": [
        {"kind": "porte", "data_15min": [{"date": "2026-09-26T14:15:00", "entree": 5, "sortie": 1}]}]}])
    recs, gran = sf.enclosure_series(db, "E", 2026)
    assert gran == "15min" and recs[0]["present"] == 4


def test_live_first_enclosure_series(monkeypatch):
    monkeypatch.setattr(sf, "_load_live", lambda db, e, y: {"records": [{"date": "x", "present": 9}]})
    recs, gran = sf.enclosure_series(FakeDb(), "E", 2026, source_priority=sf.LIVE_FIRST)
    assert recs == [{"date": "x", "present": 9}] and gran == "15min"
    monkeypatch.setattr(sf, "_load_live", lambda db, e, y: None)
    assert sf.enclosure_series(FakeDb(), "E", 2026, source_priority=(sf.SOURCE_LIVE,)) == ([], "horaire")


def test_load_editions_live_first_then_import_per_edition(monkeypatch):
    live_years = {2026}
    import_years = {2026, 2025, 2024}

    def fake_live(db, e, y):
        return {"race_date": date(y, 9, 26)} if y in live_years else None

    def fake_import(db, e, y, guard_year=False):
        if y not in import_years:
            return None
        return {"year": y, "race": date(y, 9, 26) if y != 2025 else date(2025, 9, 20),
                "doc": {"source": "scan_import", "data": []}, "records": [], "granularity": "15min"}

    monkeypatch.setattr(sf, "_load_live", fake_live)
    monkeypatch.setattr(sf, "_import_item", fake_import)
    monkeypatch.setattr(lf, "to_edition", lambda db, raw, race, cur, geo=None: {
        "year": 2026, "source": "live_controle", "is_current": cur, "race_date": race.isoformat()})
    eds = sf.load_editions(FakeDb(), "E", 2026, source_priority=sf.LIVE_FIRST)
    assert [(e["year"], e["source"]) for e in eds] == [
        (2026, "live_controle"), (2025, "scan_import"), (2024, "scan_import")]
    # Mode par defaut : import partout, live jamais consulte.
    monkeypatch.setattr(sf, "_load_live", lambda *a: pytest.fail("live consulte"))
    eds = sf.load_editions(FakeDb(), "E", 2026)
    assert [e["source"] for e in eds] == ["scan_import"] * 3


def test_block_marks_mixed_sources_not_comparable(monkeypatch):
    monkeypatch.setattr(sf, "load_editions", lambda db, e, y, back=2, source_priority=None: [
        {"year": 2026, "is_current": True, "source": "live_controle", "doors": 20, "units": [],
         "days": [{"date": "2026-09-26", "offset": 0, "measured": True, "peak_present": 10,
                   "peak_hour": "15:00", "entrees": 5}]},
        {"year": 2025, "is_current": False, "source": "scan_import", "doors": 20, "units": [],
         "days": [{"date": "2025-09-20", "offset": 0, "measured": True, "peak_present": 9,
                   "peak_hour": "15:00", "entrees": 5}]}])
    b = sf.build_frequentation_block(FakeDb(), "E", 2026, source_priority=sf.LIVE_FIRST)
    assert b["entries_comparable"] is False
    assert b["sources_by_year"] == {"2026": "live_controle", "2025": "scan_import"}
    b = sf.build_frequentation_block(FakeDb(), "E", 2026)
    assert "sources_by_year" not in b and b["entries_comparable"] is True


def test_end_to_end_block_live_first_on_fake_db():
    b = sf.build_frequentation_block(_db(), "TEST EV", 2026, source_priority=sf.LIVE_FIRST)
    cur = next(e for e in b["editions"] if e["is_current"])
    assert cur["source"] == "live_controle"
    assert b["totals"]["peak_present"] == 918
    assert sf.build_frequentation_block(_db(), "TEST EV", 2026) is None   # pas d'import


def test_prompt_payload_unchanged_for_import_editions():
    import scan_analysis
    block = {"event": "E", "year": 2026, "editions": [
        {"year": 2026, "is_current": True, "source": "scan_import", "race_date": "2026-09-26",
         "days": [{"date": "2026-09-26", "offset": 0, "measured": True, "peak_present": 1,
                   "peak_hour": "15:00", "entrees": 2}]}], "insights": {}}
    p = scan_analysis.build_frequentation_prompt_payload(block)
    assert set(p["editions"][0]) == {"annee", "edition_analysee", "source",
                                     "date_jour_de_course", "jours"}
    assert "sources_par_edition" not in p


# ---------------------------------------------------------------------------
# RETEX
# ---------------------------------------------------------------------------

def test_retex_presents_live_has_no_raw_archive_fallback():
    db = FakeDb(data_access=[{"_id": "___GLOBAL___", "evenement": "AUTRE"}])
    with pytest.raises(ValueError):
        er.block_presents_live(db, "TEST EV", 2026, ["2026-09-26"])


def test_retex_caveats_name_source_per_edition_and_initial_balance():
    ds = {"blocs": {"frequentation": {"statut": "ok", "data": {"editions": [
        {"annee": 2026, "source": "live_controle", "solde_initial_compteur": 8916,
         "premier_releve": "2026-04-13T10:56:39", "remise_a_zero_observee": False,
         "jours": [{"presents_pic": 48322}]},
        {"annee": 2025, "source": "collecte_temps_reel", "jours": [{"presents_pic": 35885}]}]}}}}
    notes = er._caveats(ds)
    assert any("2026 : live_controle, 2025 : collecte_temps_reel" in n for n in notes)
    assert any("8916 presents au premier releve" in n for n in notes)
    assert any("live_controle" in n and "vehicules" in n for n in notes)
