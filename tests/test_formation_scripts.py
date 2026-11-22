"""Scripts du module Formation : candidats fictifs et purge. Saute sans MongoDB local."""
import importlib.util
import os
from datetime import datetime, timezone

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from formation import banque, store

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")


def _charger(nom):
    spec = importlib.util.spec_from_file_location(nom, os.path.join(_SCRIPTS, nom + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


fictifs = _charger("formation_fictifs")
purge = _charger("formation_purge")


@pytest.fixture
def db():
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_formation_scripts_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    store.ensure_indexes(d)
    yield d
    client.drop_database(name)


def _reel(db, email="reel@x.fr"):
    return db["candidats"].insert_one({
        "campagne": store.CAMPAGNE, "email": email, "nom": "R", "prenom": "P", "tel": "0612345678",
        "tel4": "5678", "fictif": False, "amenagement": False}).inserted_id


def test_creer_fictifs_comme_des_importes(db):
    assert fictifs.creer(db) == 5
    assert fictifs.creer(db) == 0  # relancable
    docs = list(db["candidats"].find({"campagne": store.CAMPAGNE}).sort("email", 1))
    assert [d["email"] for d in docs] == ["candidat%d@test.invalid" % n for n in range(1, 6)]
    assert [d["tel"] for d in docs] == ["060000000%d" % n for n in range(1, 6)]
    assert all(d["fictif"] is True and d["tel4"] == d["tel"][-4:] for d in docs)
    assert [d["amenagement"] for d in docs] == [False, False, False, False, True]
    attendus = {"campagne", "forms_id", "nom", "prenom", "email", "tel", "tel4", "forms_repondu_le",
                "maj_le", "amenagement", "fictif", "importe_le", "_id"}
    ref = db["candidats"].find_one({"email": "candidat1@test.invalid"})
    assert set(ref) == attendus


def test_supprimer_fictifs_ne_touche_que_fictifs(db):
    fictifs.creer(db)
    reel = str(_reel(db))
    fid = str(db["candidats"].find_one({"email": "candidat2@test.invalid"})["_id"])
    for cid in (fid, reel):
        db["tentatives"].insert_one({"campagne": store.CAMPAGNE, "candidat_id": cid, "statut": "terminee"})
        db["envois"].insert_one({"campagne": store.CAMPAGNE, "candidat_id": cid, "type": "invitation"})
    comptes = fictifs.supprimer(db)
    assert comptes == {"tentatives": 1, "envois": 1, "candidats": 5}
    assert db["candidats"].count_documents({}) == 1
    assert db["tentatives"].find_one()["candidat_id"] == reel
    assert db["envois"].find_one()["candidat_id"] == reel


def _remplir(db, campagne=store.CAMPAGNE):
    cid = db["candidats"].insert_one({"campagne": campagne, "email": "a@x.fr", "fictif": False}).inserted_id
    for c in ("tentatives", "envois", "imports", "essais_connexion", "journal_admin"):
        db[c].insert_one({"campagne": campagne, "candidat_id": str(cid), "type": "invitation",
                          "t": datetime.now(timezone.utc)})
    db["reglages"].insert_one({"_id": campagne, "coef_amenagement": 1.5})


def test_purge_sans_confirmer_ne_supprime_rien(db):
    _remplir(db)
    comptes = purge.purger(db, store.CAMPAGNE, confirmer=False)
    assert all(n == 1 for n in comptes.values()) and len(comptes) == 7
    for c in comptes:
        assert db[c].count_documents({}) == 1


def test_purge_campagne(db):
    _remplir(db)
    _remplir(db, "autre-campagne")
    purge.purger(db, store.CAMPAGNE, confirmer=True)
    for c in ("candidats", "tentatives", "envois", "imports", "essais_connexion", "journal_admin"):
        assert db[c].count_documents({"campagne": store.CAMPAGNE}) == 0
        assert db[c].count_documents({"campagne": "autre-campagne"}) == 1
    assert db["reglages"].count_documents({"_id": store.CAMPAGNE}) == 0
    assert db["reglages"].count_documents({"_id": "autre-campagne"}) == 1


def test_scripts_utilisent_la_base_dediee(monkeypatch):
    monkeypatch.setenv("TITAN_ENV", "dev")
    assert store.nom_base() == "cockpit_formation_dev"
    assert fictifs.store is store and purge.store is store


def test_secrets_par_id_public():
    s = banque.secrets_par_id("logique")
    assert s and all(isinstance(v, dict) and v for v in s.values())
    s["x"] = 1
    assert "x" not in banque.secrets_par_id("logique")
    assert banque.secrets_par_id("inconnue") == {}


def test_maj_reglages_ne_journalise_que_les_changements(db):
    store.maj_reglages(db, {"coef_amenagement": 1.5, "reply_to": "a@x.fr"}, "alice")
    store.maj_reglages(db, {"coef_amenagement": 1.5, "reply_to": "b@x.fr"}, "alice")
    store.maj_reglages(db, {"coef_amenagement": 1.5}, "alice")
    js = list(db["journal_admin"].find({"action": "reglages"}).sort("t", 1))
    assert len(js) == 2
    assert js[1]["details"]["champs"] == ["reply_to"]
    assert js[1]["details"]["avant"] == {"reply_to": "a@x.fr"}
    assert js[1]["details"]["apres"] == {"reply_to": "b@x.fr"}
