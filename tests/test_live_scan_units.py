"""Unites du rapport de scans construites depuis l'archive du controle d'acces live.

Couvre : porte / zone et categorie depuis l'Area HSH, creneaux 15 min
etiquetes a leur fin sans decalage de fuseau (piege tranche), convention
vehicules par categorie, scans refuses exclus (statuts 107/133 comptes comme
passages), aide UAM et renforts PDA, priorite des corrections manuelles,
priorite des sources (live, import, ancienne chaine) et edition du mapping
d'une edition live (overrides seulement, aucun document reecrit).
"""

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from conftest import FakeDb  # noqa: E402

import live_scan_units as lsu  # noqa: E402
import scan_mapping  # noqa: E402
import scan_report_build as srb  # noqa: E402

TAG = "TEST_EV_2026"


@pytest.fixture(autouse=True)
def _no_cache():
    lsu.clear_cache()
    yield
    lsu.clear_cache()


def _tx(tranche, cp, name, gate, e=0, s=0, ve=0, vs=0, err=0):
    return {"tranche": tranche, "checkpoint_id": cp, "checkpoint_name": name,
            "gate_name": gate, "entrees": e, "sorties": s,
            "entrees_vehicules": ve, "sorties_vehicules": vs, "erreurs": err}


def _err(ts, cp, direction, status="117", type_scan="personne"):
    return {"date_utc": ts, "checkpoint": {"ID": cp}, "direction": direction,
            "status": status, "type_scan": type_scan}


STRUCTURE = [
    {"location_type": "Area", "location_id": "628", "location_name": "ENCEINTE GENERALE"},
    {"location_type": "Area", "location_id": "926", "location_name": "P OUEST"},
    {"location_type": "Area", "location_id": "1085", "location_name": "TRIBUNE 24"},
    {"location_type": "Area", "location_id": "1227", "location_name": "PARKING M1"},
    {"location_type": "Gate", "location_name": "PORTE A", "parent_area": {"id": "628", "name": "ENCEINTE GENERALE"}},
    {"location_type": "Gate", "location_name": "UAM", "parent_area": {"id": "628", "name": "ENCEINTE GENERALE"}},
    {"location_type": "Gate", "location_name": "P OUEST", "parent_area": {"id": "926", "name": "P OUEST"}},
    # Gate sans parent_area : l'Area vient des checkpoints qui y sont rattaches.
    {"location_type": "Gate", "location_name": "ACCES TRIBUNE 24"},
    {"location_type": "Checkpoint", "location_id": "c9", "location_name": "PDA.9",
     "parent_area": {"id": "1085", "name": "TRIBUNE 24"}, "parent_gate": {"name": "ACCES TRIBUNE 24"}},
    {"location_type": "Gate", "location_name": "ACCES M1", "parent_area": {"id": "1227", "name": "PARKING M1"}},
]


def _db(tx=None, errs=None, extra=None):
    cols = {
        "evenement": [{"nom": "TEST EV", "short": "TEV"}],
        "parametrages": [{"event": "TEST EV", "year": "2026", "data": {"globalHoraires": {
            "race": "2026-09-26T13:00:00.000Z",
            "dates": [{"date": "2026-09-26"}, {"date": "2026-09-27"}]}}}],
        "data_access": [{"_id": "___GLOBAL___", "evenement": "AUTRE"}],
        "hsh_archive_structure_" + TAG: list(STRUCTURE),
        "hsh_archive_tx_" + TAG: tx if tx is not None else [
            _tx(datetime(2026, 9, 26, 19, 45), "c1", "TRI-A-1", "PORTE A", e=10, s=1, err=0),
            _tx(datetime(2026, 9, 26, 19, 50), "c1", "TRI-A-1", "PORTE A", e=20, s=2, err=3),
            _tx(datetime(2026, 9, 26, 19, 55), "c2", "PDA.2", "PORTE A", e=5, ve=2),
            _tx(datetime(2026, 9, 26, 20, 0), "c2", "PDA.2", "PORTE A", e=7),
            _tx(datetime(2026, 9, 26, 20, 0), "u1", "PDA.3.31", "UAM", s=4),
            _tx(datetime(2026, 9, 26, 20, 5), "u1", "PDA.3.31", "PORTE A", e=6),
            _tx(datetime(2026, 9, 26, 10, 0), "c3", "PDA.5", "P OUEST", e=30, ve=12, err=0),
            _tx(datetime(2026, 9, 26, 10, 0), "c9", "PDA.9", "ACCES TRIBUNE 24", e=8, ve=3),
            _tx(datetime(2026, 9, 19, 20, 0), "m1", "PDA.3.34", "ACCES M1", e=40),
        ],
        "hsh_archive_erreurs_" + TAG: errs if errs is not None else [
            _err("2026-09-26T19:51:10", "c1", "Entree"),
            _err("2026-09-26T19:52:00", "c1", "Entree", status="133"),   # compte comme passage
            _err("2026-09-26T19:53:00", "c1", "Sortie"),
        ],
    }
    cols.update(extra or {})
    return FakeDb(**cols)


def _units(doc):
    return {(u["kind"], u["name"]): u for u in doc["complet"]}


# ---------------------------------------------------------------------------
# Porte / zone et categorie
# ---------------------------------------------------------------------------

def test_kind_from_gate_area_and_checkpoint_votes():
    maps = lsu.structure_maps(STRUCTURE)
    assert lsu.classify_gate("PORTE A", "c1", maps)[:2] == ("porte", "PORTE A")
    assert lsu.classify_gate("P OUEST", "c3", maps)[:2] == ("zone", "P OUEST")
    # Zone = l'AREA, pas la gate (comme la colonne << zone >> de l'export).
    assert lsu.classify_gate("ACCES TRIBUNE 24", "zz", maps)[:2] == ("zone", "TRIBUNE 24")
    # Un PDA mobile : la gate du passage prime sur l'Area du checkpoint.
    assert lsu.classify_gate("PORTE A", "c9", maps)[:2] == ("porte", "PORTE A")
    # Aucune structure : repli sur le nom.
    empty = lsu.structure_maps([])
    assert lsu.classify_gate("PORTE X", "q", empty)[0] == "porte"
    assert lsu.classify_gate("AA TRUC", "q", empty)[:2] == ("zone", "AA TRUC")


def test_categories_derived_from_hsh_area():
    doc = lsu.build_live_complet(_db(), "TEST EV", 2026, tripode_portes=set())
    u = _units(doc)
    assert u[("porte", "PORTE A")]["category"] == "porte"
    assert u[("zone", "P OUEST")]["category"] == "parking"
    assert u[("zone", "TRIBUNE 24")]["category"] == "tribune"
    assert {x["category_source"] for x in doc["complet"]} == {"hsh_area"}
    assert doc["source"] == "live_controle" and doc["type"] == "complet"


def test_football_parkings_excluded_by_area_id():
    doc = lsu.build_live_complet(_db(), "TEST EV", 2026, tripode_portes=set())
    assert ("zone", "PARKING M1") not in _units(doc)
    assert doc["live"]["excluded_areas"] == {"PARKING M1": 40}


# ---------------------------------------------------------------------------
# Creneaux, fuseau, refus, vehicules
# ---------------------------------------------------------------------------

def test_quarters_end_labelled_without_timezone_shift():
    doc = lsu.build_live_complet(_db(), "TEST EV", 2026, tripode_portes=set())
    porte = _units(doc)[("porte", "PORTE A")]
    dates = [r["date"] for r in porte["data_15min"]]
    # 19:45 / 19:50 / 19:55 -> << 20:00 >> ; 20:00 et 20:05 -> << 20:15 >>.
    # Aucun +2 h : `tranche` est deja l'heure de Paris.
    assert dates == ["2026-09-26T20:00:00", "2026-09-26T20:15:00"]


def test_refused_scans_excluded_and_133_counted():
    doc = lsu.build_live_complet(_db(), "TEST EV", 2026, tripode_portes=set())
    porte = _units(doc)[("porte", "PORTE A")]
    first = porte["data_15min"][0]
    # Entrees : 10 + (20 - 1 refus 117 ; le 133 est un passage) + (5 - 2 vehicules)
    assert first["entree"] == 10 + 19 + 3
    assert first["sortie"] == 1 + 1          # 2 sorties dont 1 refusee
    assert porte["refused_entree"] == 1 and porte["refused_sortie"] == 1
    assert first["autre"] == 0 and porte["total_autre"] == 0


def test_valid_counts_proportional_fallback_when_errors_missing():
    # 4 refus dans la tranche, aucun detail dans l'archive des erreurs.
    pe_, ps_, ve, vs, re_, rs = lsu.valid_counts(
        {"entrees": 30, "sorties": 10, "erreurs": 4}, None)
    assert (re_, rs) == (3, 1)
    assert (pe_, ps_) == (27, 9)


def test_vehicle_convention_per_category():
    doc = lsu.build_live_complet(_db(), "TEST EV", 2026, tripode_portes=set())
    u = _units(doc)
    # Parking : les vehicules sont le flux utile.
    assert u[("zone", "P OUEST")]["total_entree"] == 30
    assert u[("zone", "P OUEST")]["vehicles_counted"] is True
    # Tribune (flux personnes) : vehicules exclus.
    assert u[("zone", "TRIBUNE 24")]["total_entree"] == 5
    # Porte : personnes seulement.
    assert u[("porte", "PORTE A")]["vehicles_entree"] == 2
    assert u[("porte", "PORTE A")]["vehicles_counted"] is False
    assert lsu.counts_vehicles("zone", "aire_accueil")
    assert not lsu.counts_vehicles("porte", "parking")


# ---------------------------------------------------------------------------
# Aide UAM, renforts PDA, corrections manuelles
# ---------------------------------------------------------------------------

def test_uam_help_and_pda_reinforcement_on_tripod_gate():
    doc = lsu.build_live_complet(_db(), "TEST EV", 2026, tripode_portes={"PORTE A"})
    porte = _units(doc)[("porte", "PORTE A")]
    assert doc["live"]["uam_devices"] == ["PDA.3.31"]
    assert porte["uam_help"]["total_scans"] == 6          # PDA UAM venu en renfort
    assert porte["pda_renfort"]["total_scans"] == 3 + 7   # PDA.2 sur une porte a tripodes
    assert porte["tripode_count"] == 1 and porte["pda_count"] == 2


def test_manual_overrides_take_precedence():
    ov = [
        {"scan_name": "P OUEST", "kind": "zone", "event": None, "year": None,
         "category": "aire_accueil", "_id_feature": "f-ouest", "feature_collection": "terrains"},
        {"scan_name": "UAM", "kind": "porte", "event": None, "year": None,
         "category": "autre", "no_location": True},
        # Cible sur l'edition : prioritaire sur le global.
        {"scan_name": "TRIBUNE 24", "kind": "zone", "event": "TEST EV", "year": 2026,
         "ignored": True},
    ]
    doc = lsu.build_live_complet(_db(extra={"scan_feature_overrides": ov}), "TEST EV", 2026,
                                 tripode_portes=set())
    u = _units(doc)
    assert u[("zone", "P OUEST")]["category"] == "aire_accueil"
    assert u[("zone", "P OUEST")]["category_source"] == "manuel"
    assert u[("zone", "P OUEST")]["_id_feature"] == "f-ouest"
    assert u[("porte", "UAM")]["feature_source"] == "sans_lieu"
    assert u[("zone", "TRIBUNE 24")]["ignored"] is True


def test_no_archive_gives_none():
    assert lsu.build_live_complet(_db(), "INCONNU", 2026) is None
    assert lsu.build_live_complet(_db(), "TEST EV", 2024) is None


# ---------------------------------------------------------------------------
# Priorite des sources
# ---------------------------------------------------------------------------

IMPORT_DOC = {"event": "TEST EV", "year": 2026, "type": "complet", "complet": [
    {"name": "PORTE A", "kind": "porte", "total_entree": 1, "total_sortie": 0,
     "data_15min": [{"date": "2026-09-26T20:00:00", "entree": 1, "sortie": 0, "present": 1}]}]}


def test_source_priority_live_then_import(monkeypatch):
    db = _db(extra={"historique_controle": [dict(IMPORT_DOC)]})
    doc, source = srb.resolve_units_doc(db, "TEST EV", 2026)
    assert source == "live_controle" and doc.get("in_memory") is True
    monkeypatch.setattr(srb, "live_units_doc", lambda *a, **k: None)
    doc, source = srb.resolve_units_doc(db, "TEST EV", 2026)
    assert source == "complet" and doc["complet"][0]["total_entree"] == 1
    assert srb.resolve_units_doc(FakeDb(), "TEST EV", 2026) == (None, None)


def test_generate_falls_back_to_import_then_legacy(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(srb, "report_path", lambda e, y: str(tmp_path / "r.html"))
    monkeypatch.setattr(srb.gpr, "render_html", lambda p: "<html></html>")
    monkeypatch.setattr(srb, "_build_frequentation", lambda *a, **k: None)
    import scan_staffing
    monkeypatch.setattr(scan_staffing, "attach_to_payload", lambda *a, **k: {})

    def fake_build(db, event, year, progress_cb=None, doc=None, source="complet", **k):
        calls.append(source)
        if source == "live_controle":
            return {"zones": [], "portes": [1], "source": source}, {"source": source}
        raise srb.gpr.ReportGenerationError("pas de complet")

    def fake_legacy(db, slug, year, progress_cb=None):
        calls.append("legacy")
        return {"zones": [], "portes": []}

    monkeypatch.setattr(srb, "build_payload_from_complet", fake_build)
    monkeypatch.setattr(srb.gpr, "build_payload_legacy", fake_legacy)

    monkeypatch.setattr(srb, "live_units_doc", lambda *a, **k: {"complet": [1]})
    assert srb.generate(FakeDb(), "TEST EV", 2026, with_analysis=False)["source"] == "live_controle"
    assert calls == ["live_controle"]

    calls.clear()
    monkeypatch.setattr(srb, "live_units_doc", lambda *a, **k: None)
    info = srb.generate(FakeDb(), "TEST EV", 2026, with_analysis=False)
    assert calls == ["complet", "legacy"] and info["source"] == "legacy"


def test_payload_carries_source_label():
    db = _db()
    doc = lsu.build_live_complet(db, "TEST EV", 2026, tripode_portes=set())
    payload, info = srb.build_payload_from_complet(db, "TEST EV", 2026, doc=doc,
                                                   source="live_controle")
    assert payload["source"] == "live_controle"
    assert "live" in payload["source_label"]
    assert payload["source_note"].startswith("Portes : ")
    assert info["live"]["excluded_areas"] == {"PARKING M1": 40}


# ---------------------------------------------------------------------------
# Edition du mapping d'une edition live
# ---------------------------------------------------------------------------

def test_mapping_on_live_edition_writes_only_overrides():
    db = _db(extra={"historique_controle": [dict(IMPORT_DOC)]})
    before = [dict(d) for d in db["historique_controle"].docs]
    data = scan_mapping.load_mapping(db, "TEST EV", 2026)
    assert data["live"] is True and data["source"] == "live_controle"

    changes = {"zone|P OUEST": {"category": "aire_accueil", "ignored": False,
                                "no_location": False, "_id_feature": None}}
    res = scan_mapping.apply_mapping(db, "TEST EV", 2026, changes, save_overrides=False)
    assert res["live"] is True and res["rewritten"] == [] and res["changed"] == 1
    assert res["overrides_scope"] == "edition"
    # Aucune ecriture dans historique_controle (reference N-1), ni archive.
    assert db["historique_controle"].docs == before
    assert db["historique_controle_archive"].docs == []
    ov = db["scan_feature_overrides"].docs
    assert len(ov) == 1
    assert (ov[0]["scan_name"], ov[0]["event"], ov[0]["year"]) == ("P OUEST", "TEST EV", 2026)
    assert ov[0]["category"] == "aire_accueil"

    # La regeneration relit l'override : la categorie est appliquee.
    lsu.clear_cache()
    doc = lsu.build_live_complet(db, "TEST EV", 2026, tripode_portes=set())
    assert _units(doc)[("zone", "P OUEST")]["category"] == "aire_accueil"

    # << Memoriser >> coche : override global.
    res = scan_mapping.apply_mapping(
        db, "TEST EV", 2026, {"porte|UAM": {"no_location": True, "ignored": False}},
        save_overrides=True)
    assert res["overrides_scope"] == "global"
    glob = [d for d in db["scan_feature_overrides"].docs if d["scan_name"] == "UAM"]
    assert glob and glob[0]["event"] is None and glob[0]["no_location"] is True
    assert db["historique_controle"].docs == before


def test_enclosure_hourly_matches_import_semantics():
    units = [
        {"kind": "porte", "name": "A", "data_15min": [
            {"date": "2026-09-26T20:00:00", "entree": 5, "sortie": 1},
            {"date": "2026-09-26T20:15:00", "entree": 3, "sortie": 0}]},
        {"kind": "porte", "name": "B", "ignored": True, "data_15min": [
            {"date": "2026-09-26T20:00:00", "entree": 100, "sortie": 0}]},
        {"kind": "zone", "name": "Z", "data_15min": [
            {"date": "2026-09-26T20:00:00", "entree": 100, "sortie": 0}]},
    ]
    assert lsu.enclosure_hourly(units) == [
        {"date": "2026-09-26T20:00:00", "entree": 8, "sortie": 1, "present": 7}]
