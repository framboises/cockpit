"""Cloture d'une fiche creee par la tablette (/field/my-fiches/<id>/close)
contre un MongoDB local jetable : issue et bilan comme le Cockpit."""
import os
from datetime import datetime, timezone

import pytest
from flask import Flask
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import field
import pcorg_history as PH


@pytest.fixture
def env(monkeypatch):
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_fclose_%d" % os.getpid()
    client.drop_database(name)
    db = client[name]
    now = datetime.now(timezone.utc)
    db["field_devices"].insert_one({"_id": "dev-1", "name": "Secu 1", "event": "SAISON", "year": "2026",
                                    "revoked": False, "last_seen": now, "status": "intervention",
                                    "active_fiche_id": "f1", "token_hash": field._hash_token("tok-1")})
    for fid, created in (("f1", True), ("f2", True), ("f3", False)):
        db["pcorg"].insert_one({"_id": fid, "status_code": 1, "category": "PCO.Securite", "comment_history": [],
                                "content_category": {"patrouille": "Secu 1", "field_created": created}})
    monkeypatch.setattr(field, "_get_mongo_db", lambda: db)
    monkeypatch.setattr(field, "_sweep_event_if_ended", lambda *a: False)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(field.field_bp)
    c = app.test_client()
    c.set_cookie(field.FIELD_COOKIE_NAME, "tok-1", path=field.FIELD_COOKIE_PATH)
    yield c, db
    client.drop_database(name)


def test_issue_et_bilan(env):
    c, db = env
    rep = c.post("/field/my-fiches/f1/close", json={"issue": "fausse_alerte", "comment": "Personne sur place"})
    assert rep.status_code == 200, rep.get_json()
    f = db["pcorg"].find_one({"_id": "f1"})
    assert f["status_code"] == 10
    assert f["cloture"] == {"issue": "fausse_alerte", "bilan": "Personne sur place"}
    assert f["comment_history"][-1]["text"] == PH.close_text("fausse_alerte", "Personne sur place")
    assert "Issue : Fausse alerte" in f["comment_history"][-1]["text"]
    assert db["field_devices"].find_one({"_id": "dev-1"})["status"] == "patrouille"


def test_sans_corps_et_issue_inconnue(env):
    c, db = env
    assert c.post("/field/my-fiches/f2/close", json={"issue": "nimporte"}).status_code == 200
    f = db["pcorg"].find_one({"_id": "f2"})
    assert "cloture" not in f
    assert f["comment_history"][-1]["text"] == "Statut: En cours -> Terminé"


def test_fiche_du_cockpit_refusee(env):
    c, _ = env
    assert c.post("/field/my-fiches/f3/close", json={}).status_code == 403
