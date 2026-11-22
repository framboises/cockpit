"""Erreur CSRF de l'API candidat Formation sur la vraie application : JSON 400
(handle_csrf_error d'app.py), comme pour /api/. Test saute sans MongoDB local."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import app as A
from formation import store


@pytest.fixture
def client(monkeypatch):
    try:
        mc = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        mc.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_formation_csrf_%d" % os.getpid()
    mc.drop_database(name)
    db = mc[name]
    store.ensure_indexes(db)
    now = datetime.now(timezone.utc)
    db["reglages"].insert_one({"_id": store.CAMPAGNE, "ouverture": now - timedelta(hours=1),
                               "fermeture": now + timedelta(hours=1),
                               "reply_to": "a@b.fr", "contact_rgpd": "dpo@b.fr"})
    store.set_db_for_tests(db)
    monkeypatch.setitem(A.app.config, "WTF_CSRF_ENABLED", True)
    monkeypatch.setitem(A.app.config, "TESTING", True)
    yield A.app.test_client()
    store.set_db_for_tests(None)
    mc.drop_database(name)


@pytest.mark.parametrize("chemin", ["/test-pc-organisation/api/commencer",
                                    "/test-pc-organisation/api/epreuve/radio/action",
                                    "/test-pc-organisation/api/connexion"])
def test_post_sans_csrf_json_400(client, chemin):
    r = client.post(chemin, json={"accepte": True, "t_ms": 1, "type": "x"})
    assert r.status_code == 400
    assert r.is_json
    assert r.get_json()["code"] == "csrf"
