"""Analyse complete d'une porte (portes_dossier.py) : familles de statuts HSH
et, si l'archive GPF 2026 est presente en local, coherence du dossier."""
from datetime import date, datetime

import pytest

import portes_dossier as P


def test_familles_des_statuts():
    assert P.famille("117") == P.famille("119") == P.famille(124) == "double_usage"
    assert P.famille("133") == "reentree"          # passage accepte
    assert P.famille("107") == "hors_ligne"        # passage accepte
    assert P.famille("61") == P.famille("175") == P.famille("63") == "ouverture"
    assert P.famille("134") == "annulation"
    assert P.famille("106") == P.famille("35") == P.famille("129") == "titre"
    assert P.famille("140") == "autre"
    assert [k for k, _l, _a in P.FAMILLES][0] == "double_usage"


@pytest.fixture
def db_dev():
    try:
        from pymongo import MongoClient
        db = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)["titan_dev"]
        if not db["hsh_archive_tx_GPF_2026"].estimated_document_count():
            raise RuntimeError
    except Exception:
        pytest.skip("archive GPF 2026 absente")
    return db


def test_dossier_d_une_porte(db_dev):
    d = P.dossier(db_dev, "GPF", 2026, "PORTE NORD PIETONS", jour=date(2026, 5, 10))
    assert d["disponible"]
    t = d["totaux"]
    assert t["scans"] == t["entrees"] + t["sorties"] + t["refus"]
    assert t["refus"] == t["double_usage"] + t["titres_refuses"]
    assert sum(a["e"] for a in d["appareils"]) == t["entrees"]
    assert sum(s["n"] for s in d["statuts"]) == sum(f["n"] for f in d["familles"])
    assert all(r["n"] >= P.TITRES_RECURRENTS_MIN for r in d["titres_recurrents"])
    # Arrete a une heure : jamais plus que la journee
    d2 = P.dossier(db_dev, "GPF", 2026, "PORTE NORD PIETONS", jour=date(2026, 5, 10), at=datetime(2026, 5, 10, 11, 30))
    assert d2["totaux"]["entrees"] < t["entrees"]
    assert d2["serie"][-1]["iso"] <= "2026-05-10T11:00"
    # Toute l'edition
    assert P.dossier(db_dev, "GPF", 2026, "PORTE NORD PIETONS")["totaux"]["entrees"] > t["entrees"]
    assert not P.dossier(db_dev, "GPF", 2026, "PORTE QUI N EXISTE PAS")["disponible"]
