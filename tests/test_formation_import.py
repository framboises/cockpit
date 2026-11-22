"""Import de l'export Forms (formation/importer.py) contre un MongoDB local jetable."""
import os
from datetime import datetime, timezone

import openpyxl
import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from formation import store
from formation.importer import importer
from formation.normalise import normaliser_email, normaliser_tel

ENTETE = ["Id", "Heure de début", "Heure de fin", "Adresse de messagerie", "Nom", "Nom1",
          "Prénom", "Téléphone mobile", "Adresse e-mail", "Code postal de résidence"]


@pytest.fixture
def db():
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_formation_imp_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    store.ensure_indexes(d)
    yield d
    client.drop_database(name)


def _xlsx(tmp_path, lignes):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(ENTETE)
    for i, (fid, fin, nom, prenom, tel, email) in enumerate(lignes):
        ws.append([fid, fin, fin, "anonymous", None, nom, prenom, tel, email, "72000"])
    p = tmp_path / "export.xlsx"
    wb.save(p)
    return p.read_bytes()


def D(h, mn=0):
    return datetime(2026, 11, 19, h, mn, 0)


def test_normaliser_tel():
    assert normaliser_tel("+33 6 17 96 31 79") == "0617963179"
    assert normaliser_tel("0617963179") == "0617963179"
    assert normaliser_tel("617963179") is None
    assert normaliser_tel(None) is None


def test_normaliser_email():
    assert normaliser_email(" F.Arbia@Gmail.com ") == "f.arbia@gmail.com"
    assert normaliser_email("pas-un-email") is None
    assert normaliser_email("") is None


def test_import_basique_heure_utc(db, tmp_path):
    r = importer(db, _xlsx(tmp_path, [(1, D(15, 19), "Arbia", "Fatima", "0617963179", "F.Arbia@x.fr")]), "a@x.fr")
    assert (r["lignes"], r["crees"]) == (1, 1)
    c = db["candidats"].find_one({"email": "f.arbia@x.fr"})
    assert c["tel4"] == "3179" and c["amenagement"] is False and c["fictif"] is False
    # 15:19 Paris (UTC+1 en novembre) = 14:19 UTC
    assert c["forms_repondu_le"].replace(tzinfo=timezone.utc) == datetime(2026, 11, 19, 14, 19, tzinfo=timezone.utc)
    assert db["imports"].count_documents({}) == 1


def test_doublon_garde_le_plus_recent(db, tmp_path):
    r = importer(db, _xlsx(tmp_path, [
        (1, D(10), "Ancien", "A", "0600000001", "dup@x.fr"),
        (2, D(12), "Recent", "A", "0600000002", "DUP@x.fr")]), "a@x.fr")
    assert r["crees"] == 1
    assert r["doublons"] == [{"email": "dup@x.fr", "ids_forms": [1, 2], "garde": 2}]
    c = db["candidats"].find_one({"email": "dup@x.fr"})
    assert c["forms_id"] == 2 and c["nom"] == "Recent"
    assert db["candidats"].count_documents({}) == 1


def test_inexploitables(db, tmp_path):
    r = importer(db, _xlsx(tmp_path, [
        (1, D(10), "A", "A", "0600000001", None),
        (2, D(10), "B", "B", "123", "b@x.fr"),
        (3, D(10), "C", "C", "+33 6 00 00 00 03", "c@x.fr")]), "a@x.fr")
    assert r["crees"] == 1
    assert {i["forms_id"] for i in r["inexploitables"]} == {1, 2}
    assert all(i["motif"] for i in r["inexploitables"])
    assert db["candidats"].count_documents({}) == 1


def test_relance_ne_touche_pas_tentatives(db, tmp_path):
    importer(db, _xlsx(tmp_path, [(1, D(10), "Ancien", "A", "0600000001", "a@x.fr")]), "x")
    c = db["candidats"].find_one({"email": "a@x.fr"})
    db["candidats"].update_one({"_id": c["_id"]}, {"$set": {"amenagement": True}})
    db["tentatives"].insert_one({"campagne": store.CAMPAGNE, "candidat_id": str(c["_id"]), "statut": "en_cours"})
    db["envois"].insert_one({"campagne": store.CAMPAGNE, "candidat_id": str(c["_id"]), "type": "invitation"})
    r = importer(db, _xlsx(tmp_path, [(1, D(10), "Nouveau", "A", "0600000001", "a@x.fr")]), "x")
    assert (r["crees"], r["mis_a_jour"]) == (0, 1)
    c2 = db["candidats"].find_one({"_id": c["_id"]})
    assert c2["nom"] == "Nouveau" and c2["amenagement"] is True
    assert db["tentatives"].count_documents({}) == 1 and db["envois"].count_documents({}) == 1
    r = importer(db, _xlsx(tmp_path, [(1, D(10), "Nouveau", "A", "0600000001", "a@x.fr")]), "x")
    assert r["inchanges"] == 1 and r["mis_a_jour"] == 0


def test_absents_du_fichier(db, tmp_path):
    importer(db, _xlsx(tmp_path, [(1, D(10), "A", "A", "0600000001", "a@x.fr"),
                                  (2, D(10), "B", "B", "0600000002", "b@x.fr")]), "x")
    r = importer(db, _xlsx(tmp_path, [(1, D(10), "A", "A", "0600000001", "a@x.fr")]), "x")
    assert r["absents_du_fichier"] == ["b@x.fr"]


def test_aucune_colonne_superflue(db, tmp_path):
    importer(db, _xlsx(tmp_path, [(1, D(10), "A", "A", "0600000001", "a@x.fr")]), "x")
    c = db["candidats"].find_one({"email": "a@x.fr"})
    assert set(c) - {"_id"} == {"campagne", "forms_id", "nom", "prenom", "email", "tel", "tel4",
                                "amenagement", "fictif", "forms_repondu_le", "importe_le", "maj_le"}
