"""Controle de documents des tablettes Field (controle.py, /field/controle/search),
contre un MongoDB local jetable. Le serveur de consultation est simule."""
import json
import os

import pytest
from flask import Flask
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import controle
import field

SECRET = "s" * 40


@pytest.fixture
def env(monkeypatch):
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_fctl_%d" % os.getpid()
    client.drop_database(name)
    db = client[name]
    db["anoloc_config"].insert_one({"_id": "global", "beacon_groups": [
        {"id": "g-ctl", "controle": True}, {"id": "g-autre"}]})
    db["field_devices"].insert_one({"_id": "dev-1", "name": "Secu 1", "event": "SAISON", "year": "2026",
                                    "revoked": False, "beacon_group_id": "g-ctl",
                                    "last_position": {"lat": 47.95, "lng": 0.21},
                                    "token_hash": field._hash_token("tok-1")})
    monkeypatch.setattr(field, "_get_mongo_db", lambda: db)
    monkeypatch.setattr(field, "_sweep_event_if_ended", lambda *a: False)
    monkeypatch.delenv("CONTROLE_API_URL", raising=False)
    monkeypatch.delenv("CONTROLE_API_SECRET", raising=False)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(field.field_bp)
    c = app.test_client()
    c.set_cookie(field.FIELD_COOKIE_NAME, "tok-1", path=field.FIELD_COOKIE_PATH)
    yield c, db
    client.drop_database(name)


PERSONNE = {"query": {"type": "personne", "nom": "Dupont", "prenom": "Jean"}, "source": "manuel", "motif": "acces"}


def _api(monkeypatch, reponse=None, erreur=None):
    monkeypatch.setenv("CONTROLE_API_URL", "https://consultation.test/v1/consultations")
    monkeypatch.setenv("CONTROLE_API_SECRET", SECRET)
    vu = {}

    class R:
        status_code = 200

        def json(self):
            return reponse

    def post(url, data=None, timeout=None, headers=None):
        vu.update(url=url, data=data, headers=headers, timeout=timeout)
        if erreur:
            raise erreur
        return R()

    monkeypatch.setattr(controle.requests, "post", post)
    return vu


def test_groupe_non_autorise_refuse(env):
    c, db = env
    db["field_devices"].update_one({"_id": "dev-1"}, {"$set": {"beacon_group_id": "g-autre"}})
    r = c.post("/field/controle/search", json=PERSONNE)
    assert r.status_code == 403 and r.get_json()["error"] == "controle_non_autorise"
    assert db[controle.COLLECTION].count_documents({}) == 0


def test_sans_serveur_503_et_trace(env):
    c, db = env
    r = c.post("/field/controle/search", json=PERSONNE)
    assert r.status_code == 503 and r.get_json()["error"] == "consultation_non_configuree"
    log = db[controle.COLLECTION].find_one({})
    assert log["query"]["nom"] == "Dupont" and log["provider"] is None and log["device_id"] == "dev-1"
    assert log["position"] == {"lat": 47.95, "lng": 0.21} and log["expire_at"] > log["at"]


def test_mode_test_resultats_fictifs(env):
    c, db = env
    db["field_devices"].update_one({"_id": "dev-1"}, {"$set": {"test_mode": True}})
    q = {"query": {"type": "personne", "nom": "Test-Alerte"}}
    d = c.post("/field/controle/search", json=q).get_json()
    assert d["ok"] and d["demo"] and d["statut"] == "alerte" and d["resultats"][0]["consignes"]
    d = c.post("/field/controle/search", json=PERSONNE).get_json()
    assert d["statut"] == "aucun" and d["resultats"] == []


def test_appel_signe_et_reponse_assainie(env, monkeypatch):
    c, db = env
    vu = _api(monkeypatch, {"resultats": [
        {"niveau": "alerte", "titre": "X" * 500, "champs": [{"label": "Nom", "valeur": "Dupont"}, {"pas": "de label"}]},
        {"niveau": "inconnu", "titre": "Autre"},
    ], "sources": [{"nom": "Accreditations", "etat": "bizarre"}], "messages": ["ok", 12]})
    d = c.post("/field/controle/search", json=PERSONNE).get_json()
    assert d["statut"] == "alerte"
    assert len(d["resultats"][0]["titre"]) == 120 and d["resultats"][0]["champs"] == [{"label": "Nom", "valeur": "Dupont"}]
    assert d["resultats"][1]["niveau"] == "info"
    assert d["sources"] == [{"nom": "Accreditations", "etat": "ok"}] and d["messages"] == ["ok"]
    h = vu["headers"]
    assert h["X-Cockpit-Signature"] == controle.signature(SECRET, h["X-Cockpit-Timestamp"], vu["data"])
    envoye = json.loads(vu["data"])
    assert envoye["query"]["nom"] == "Dupont" and envoye["origine"]["tablette_id"] == "dev-1"
    assert envoye["consultation_id"] == d["consultation_id"] == h["X-Cockpit-Consultation"]
    assert db[controle.COLLECTION].find_one({"_id": d["consultation_id"]})["statut"] == "alerte"


def test_serveur_injoignable_502_trace(env, monkeypatch):
    c, db = env
    _api(monkeypatch, erreur=RuntimeError("timeout"))
    r = c.post("/field/controle/search", json=PERSONNE)
    assert r.status_code == 502 and r.get_json()["error"] == "consultation_indisponible"
    assert db[controle.COLLECTION].find_one({})["error"] == "timeout"


def test_limite_de_consultations(env, monkeypatch):
    c, db = env
    monkeypatch.setenv("CONTROLE_RATE_10MIN", "2")
    for _ in range(2):
        c.post("/field/controle/search", json=PERSONNE)
    assert c.post("/field/controle/search", json=PERSONNE).status_code == 429
    # Le refus est trace aussi, et compte : pas de contournement en insistant
    assert db[controle.COLLECTION].count_documents({"statut": "refuse_limite"}) == 1


def test_trace_avant_consultation(env, monkeypatch):
    c, db = env
    vu = {}

    def post(url, data=None, timeout=None, headers=None):
        vu["log"] = db[controle.COLLECTION].find_one({})
        raise RuntimeError("coupure")

    monkeypatch.setenv("CONTROLE_API_URL", "https://consultation.test/v1")
    monkeypatch.setenv("CONTROLE_API_SECRET", SECRET)
    monkeypatch.setattr(controle.requests, "post", post)
    c.post("/field/controle/search", json=PERSONNE)
    assert vu["log"]["statut"] == "en_cours" and vu["log"]["query"]["nom"] == "Dupont"
    assert db[controle.COLLECTION].find_one({})["statut"] == "erreur"


@pytest.mark.parametrize("query,erreur", [
    ({"type": "rien"}, "type_invalide"),
    ({"type": "personne", "nom": "D"}, "nom_requis"),
    ({"type": "personne", "nom": "Dupont<script>"}, "nom_invalide"),
    ({"type": "personne", "nom": "Dupont", "date_naissance": "1990-02-30"}, "date_invalide"),
    ({"type": "entreprise", "siret": "12345678901234"}, "siret_invalide"),
    ({"type": "entreprise"}, "entreprise_requise"),
    ({"type": "code"}, "code_requis"),
])
def test_demandes_invalides(env, query, erreur):
    c, db = env
    r = c.post("/field/controle/search", json={"query": query})
    assert r.status_code == 400 and r.get_json()["error"] == erreur
    assert db[controle.COLLECTION].count_documents({}) == 0


def test_normalisation():
    q, err = controle.normalize_query({"query": {"type": "personne", "numero_document": "x4rtbpfw4 ",
                                                 "nationalite": "fra", "type_document": "cni"},
                                       "source": "mrz", "motif": "nimporte"})
    assert err is None and q["source"] == "mrz" and q["motif"] == "acces"
    assert q["query"]["numero_document"] == "X4RTBPFW4" and q["query"]["nationalite"] == "FRA"


def test_badge_avec_titulaire():
    q, err = controle.normalize_query({"query": {"type": "code", "valeur": "9000914400001003", "nom": "LECLAIR", "prenom": "Florian"}})
    assert err is None and q["query"]["nom"] == "LECLAIR" and q["query"]["prenom"] == "Florian"
    _, err = controle.normalize_query({"query": {"type": "code", "valeur": "1", "nom": "<b>"}})
    assert err == "nom_invalide"
    d = controle.demo_response(q["query"])
    assert d["resultats"][0]["champs"][0] == {"label": "Titulaire", "valeur": "LECLAIR Florian"}


def test_luhn():
    assert controle.luhn_ok("73282932000074")      # SIRET valide (exemple INSEE)
    assert controle.luhn_ok("732829320")           # SIREN
    assert not controle.luhn_ok("73282932000075")
    assert controle.luhn_ok("35600000000048")      # La Poste, siege (Luhn)
    assert controle.luhn_ok("35600000000015")      # La Poste fictif : somme 20, hors Luhn
