"""Application Android de Field : jeton FCM, envoi par FCM et reponse de
/field/position, contre un MongoDB local jetable."""
import os

import pytest
from flask import Flask
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import field


@pytest.fixture
def env(monkeypatch):
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_ffcm_%d" % os.getpid()
    client.drop_database(name)
    db = client[name]
    db["field_push_subs"].create_index("endpoint", unique=True)
    db["field_devices"].insert_one({"_id": "dev-1", "name": "Secu 1", "event": "SAISON", "year": "2026",
                                    "revoked": False, "status": "intervention",
                                    "token_hash": field._hash_token("tok-1")})
    monkeypatch.setattr(field, "_get_mongo_db", lambda: db)
    monkeypatch.setattr(field, "_sweep_event_if_ended", lambda *a: False)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(field.field_bp)
    c = app.test_client()
    c.set_cookie(field.FIELD_COOKIE_NAME, "tok-1", path=field.FIELD_COOKIE_PATH)
    yield c, db
    client.drop_database(name)


def test_enregistrement_du_jeton(env):
    c, db = env
    assert c.post("/field/push/fcm-register", json={"token": "abc", "app_version": "1.0"}).status_code == 200
    sub = db["field_push_subs"].find_one({"device_id": "dev-1"})
    assert sub["kind"] == "fcm" and sub["fcm_token"] == "abc" and sub["endpoint"] == "fcm:abc"
    assert db["field_devices"].find_one({"_id": "dev-1"})["native_app_version"] == "1.0"


def test_nouveau_jeton_remplace_l_ancien_sans_toucher_au_web_push(env):
    c, db = env
    db["field_push_subs"].insert_one({"device_id": "dev-1", "endpoint": "https://push/1", "subscription": {"endpoint": "https://push/1"}})
    c.post("/field/push/fcm-register", json={"token": "ancien"})
    c.post("/field/push/fcm-register", json={"token": "nouveau"})
    endpoints = sorted(s["endpoint"] for s in db["field_push_subs"].find({"device_id": "dev-1"}))
    assert endpoints == ["fcm:nouveau", "https://push/1"]


def test_jeton_absent_refuse(env):
    c, _db = env
    assert c.post("/field/push/fcm-register", json={}).status_code == 400
    assert c.post("/field/push/fcm-register", json={"token": 12}).status_code == 400


def test_envoi_aiguille_selon_le_type_et_retire_le_jeton_expire(env, monkeypatch):
    _c, db = env
    db["field_push_subs"].insert_many([
        {"device_id": "dev-1", "endpoint": "fcm:ok", "kind": "fcm", "fcm_token": "ok"},
        {"device_id": "dev-1", "endpoint": "fcm:mort", "kind": "fcm", "fcm_token": "mort"},
        {"device_id": "dev-1", "endpoint": "https://push/1", "subscription": {"endpoint": "https://push/1"}},
    ])
    vus = {"fcm": [], "web": []}

    def fake_fcm(token, title, body, url=None, tag=None, push_type=None):
        vus["fcm"].append((token, push_type))
        return "expired" if token == "mort" else True

    def fake_web(info, title, body, url=None, tag=None, push_type=None):
        vus["web"].append(info["endpoint"])
        return True

    monkeypatch.setattr(field, "send_fcm_notification", fake_fcm)
    monkeypatch.setattr(field, "send_push_notification", fake_web)
    assert field.send_push_to_device(db, "dev-1", "T", "B", push_type="sos") == 2
    assert sorted(vus["fcm"]) == [("mort", "sos"), ("ok", "sos")]
    assert vus["web"] == ["https://push/1"]
    assert db["field_push_subs"].find_one({"endpoint": "fcm:mort"}) is None


def test_fcm_non_configure_ne_bloque_rien(monkeypatch):
    monkeypatch.setattr(field, "FIELD_FCM_CREDENTIALS", "")
    monkeypatch.setattr(field, "_FCM_APP", None)
    assert field.send_fcm_notification("abc", "T", "B") is False


def test_position_web_inchangee(env):
    c, _db = env
    rep = c.post("/field/position", json={"lat": 47.9, "lng": 0.2})
    assert rep.get_json() == {"ok": True}


def test_position_native_renvoie_statut_et_suivi(env):
    c, db = env
    rep = c.post("/field/position", json={"lat": 47.9, "lng": 0.2, "native": True}).get_json()
    assert rep == {"ok": True, "status": "intervention", "tracking_mode": "normal"}
    db["field_devices"].update_one({"_id": "dev-1"}, {"$set": {
        "tracking_mode": "high_freq", "tracking_watchers": {"op": "2999-01-01T00:00:00+00:00"}}})
    rep = c.post("/field/position", json={"lat": 47.9, "lng": 0.2, "native": True}).get_json()
    assert rep["tracking_mode"] == "high_freq"


def test_position_native_purge_les_suivis_expires(env):
    c, db = env
    db["field_devices"].update_one({"_id": "dev-1"}, {"$set": {
        "tracking_mode": "high_freq", "tracking_watchers": {"op": "2000-01-01T00:00:00+00:00"}}})
    rep = c.post("/field/position", json={"lat": 47.9, "lng": 0.2, "native": True}).get_json()
    assert rep["tracking_mode"] == "normal"
    assert db["field_devices"].find_one({"_id": "dev-1"})["tracking_watchers"] == {}


def _capture_push(monkeypatch):
    envois = []

    def fake(db, device_id, title, body, url=None, tag=None, push_type=None):
        envois.append({"id": device_id, "title": title, "body": body, "tag": tag, "type": push_type})
        return 1

    monkeypatch.setattr(field, "send_push_to_device", fake)
    return envois


def test_message_normal_notifie_sans_alarme(monkeypatch):
    envois = _capture_push(monkeypatch)
    field._push_admin_message(None, [{"_id": "a"}, {"_id": "b"}], "", "Bonjour", "normal")
    assert [e["id"] for e in envois] == ["a", "b"]
    assert envois[0]["title"] == "Message du PC Organisation" and envois[0]["body"] == "Bonjour"
    assert envois[0]["type"] is None and envois[0]["tag"] == envois[1]["tag"]


def test_message_prioritaire_declenche_l_alarme(monkeypatch):
    envois = _capture_push(monkeypatch)
    field._push_admin_message(None, [{"_id": "a"}], "Urgent", "", "high")
    assert envois[0]["type"] == "mission" and envois[0]["body"] == "Urgent"


def test_un_envoi_en_echec_ne_bloque_pas_les_suivants(monkeypatch):
    vus = []

    def fake(db, device_id, title, body, url=None, tag=None, push_type=None):
        vus.append(device_id)
        if device_id == "a":
            raise RuntimeError("panne")
        return 1

    monkeypatch.setattr(field, "send_push_to_device", fake)
    field._push_admin_message(None, [{"_id": "a"}, {"_id": "b"}], "T", "B", "normal")
    assert vus == ["a", "b"]
