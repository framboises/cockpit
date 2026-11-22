"""Socle du module Formation (formation/store.py) contre un MongoDB local jetable."""
import os
from datetime import datetime, timezone

import pytest
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from formation import store


@pytest.fixture
def db():
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_formation_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    store.ensure_indexes(d)
    yield d
    client.drop_database(name)


def test_nom_de_base_dev_et_prod(monkeypatch):
    monkeypatch.setenv("TITAN_ENV", "dev")
    assert store.nom_base() == "cockpit_formation_dev"
    monkeypatch.setenv("TITAN_ENV", "prod")
    assert store.nom_base() == "cockpit_formation"


def test_reglages_par_defaut(db):
    r = store.reglages(db)
    assert r["coef_amenagement"] == 1.33
    assert r["ponderations"] == {e: 1 for e in store.EPREUVES}
    assert r["debit_envoi_s"] == 2.0
    assert r["expediteur_nom"] == "PC Organisation - ACO"
    assert r["reply_to"] == "" and r["contact_rgpd"] == ""
    assert r["conservation_jusqu_au"] == "2027-01-30"
    # 08:00 Paris (UTC+1 en novembre) = 07:00 UTC
    assert r["ouverture"] == datetime(2026, 11, 30, 7, 0, tzinfo=timezone.utc)
    assert r["fermeture"] == datetime(2026, 12, 2, 22, 59, tzinfo=timezone.utc)


def test_maj_reglages_journalise(db):
    r = store.maj_reglages(db, {"coef_amenagement": 1.5}, "alice@x.fr")
    assert r["coef_amenagement"] == 1.5
    assert store.reglages(db)["coef_amenagement"] == 1.5
    j = db["journal_admin"].find_one({"action": "reglages"})
    assert j and j["auteur"] == "alice@x.fr"


def test_index_tentative_unique(db):
    t = db["tentatives"]
    t.insert_one({"campagne": store.CAMPAGNE, "candidat_id": "c1", "statut": "reinitialisee"})
    t.insert_one({"campagne": store.CAMPAGNE, "candidat_id": "c1", "statut": "en_cours"})
    with pytest.raises(DuplicateKeyError):
        t.insert_one({"campagne": store.CAMPAGNE, "candidat_id": "c1", "statut": "terminee"})


def test_index_envois_invitation_et_relance(db):
    e = db["envois"]
    base = {"campagne": store.CAMPAGNE, "candidat_id": "c1"}
    e.insert_one(dict(base, type="invitation"))
    e.insert_one(dict(base, type="relance"))
    with pytest.raises(DuplicateKeyError):
        e.insert_one(dict(base, type="invitation"))
    with pytest.raises(DuplicateKeyError):
        e.insert_one(dict(base, type="relance"))


def test_index_tentative_interrompue_compte(db):
    t = db["tentatives"]
    t.insert_one({"campagne": store.CAMPAGNE, "candidat_id": "c2", "statut": "interrompue"})
    with pytest.raises(DuplicateKeyError):
        t.insert_one({"campagne": store.CAMPAGNE, "candidat_id": "c2", "statut": "en_cours"})
