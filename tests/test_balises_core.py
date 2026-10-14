"""Ingestion Teltonika (balises_core.py) contre un MongoDB local jetable :
idempotence (imei, ts), horodatages aberrants, records sans fix, ordre de
l'etat courant, parc et groupes, TTL des positions, regle en ligne/GPS."""
import os
from datetime import datetime, timezone, timedelta

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import balises_core as B

IMEI = "866107086018948"
DID = "tlt:" + IMEI
NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_balises_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    d["anoloc_positions"].create_index("collected_at")  # index historique sans TTL
    B.ensure_indexes(d)
    d["anoloc_config"].insert_one({"_id": "global", "enabled": True, "beacon_groups": [
        {"id": "grp-test", "label": "Test", "enabled": True, "icon": "location_on",
         "color": "#ff0000", "anoloc_device_ids": [DID, "anoloc-1"], "device_labels": {}},
    ]})
    B.save_device(d, {"imei": IMEI, "label": "Balise 1"}, now=NOW)
    yield d
    client.drop_database(name)


def msg(ts, lat=47.9466, lng=0.212266, valid=True, **extra):
    m = {"ident": IMEI, "timestamp": ts.timestamp(), "position.latitude": lat,
         "position.longitude": lng, "position.valid": valid, "position.speed": 0,
         "position.direction": 174, "battery.level": 95, "movement.status": False}
    m.update(extra)
    return m


def test_ttl_positions_pose_par_collmod(db):
    info = {i["name"]: i for i in db["anoloc_positions"].list_indexes()}
    ttl = [i for i in info.values() if dict(i["key"]) == {"collected_at": 1}]
    assert ttl and ttl[0]["expireAfterSeconds"] == B.POSITIONS_TTL_S
    B.ensure_indexes(db)  # idempotent


def test_libelle_recopie_dans_le_groupe(db):
    grp = db["anoloc_config"].find_one({"_id": "global"})["beacon_groups"][0]
    assert grp["device_labels"][DID] == "Balise 1"


def test_idempotence_et_rattrapage(db):
    t1, t2 = NOW - timedelta(minutes=30), NOW - timedelta(minutes=15)
    st = B.ingest(db, [msg(t2), msg(t1)], "teltonika-tcp", now=NOW)
    assert st["accepted"] == 2
    st = B.ingest(db, [msg(t1), msg(t2)], "flespi", now=NOW)
    assert st == {"accepted": 0, "duplicates": 2, "unknown": 0, "invalid": 0, "bad_ts": 0}
    assert db["anoloc_positions"].count_documents({"device_id": DID}) == 2
    # Le rattrapage (t1, arrive apres t2) ne recule pas l'etat courant
    latest = db["anoloc_latest"].find_one({"_id": DID})
    assert latest["last_msg_at"].replace(tzinfo=timezone.utc) == t2
    # Traces : collected_at = horodatage balise
    pos = sorted(p["collected_at"] for p in db["anoloc_positions"].find({"device_id": DID}))
    assert [p.replace(tzinfo=timezone.utc) for p in pos] == [t1, t2]


def test_horodatage_aberrant(db):
    st = B.ingest(db, [msg(datetime(2021, 12, 31, tzinfo=timezone.utc)),
                       msg(NOW + timedelta(hours=1))], "flespi", now=NOW)
    assert st["accepted"] == 2 and st["bad_ts"] == 2
    assert db["anoloc_positions"].count_documents({"device_id": DID}) == 0
    assert db["anoloc_latest"].find_one({"_id": DID}) is None
    assert db["balises_messages"].count_documents({"ts_valid": False}) == 2


def test_sans_fix_met_a_jour_la_telemetrie_seulement(db):
    t1, t2 = NOW - timedelta(minutes=20), NOW - timedelta(minutes=5)
    B.ingest(db, [msg(t1)], "flespi", now=NOW)
    B.ingest(db, [msg(t2, lat=0, lng=0, valid=False, **{"battery.level": 100,
                  "external.powersource.voltage": 4.963})], "flespi", now=NOW)
    latest = db["anoloc_latest"].find_one({"_id": DID})
    assert latest["battery_pct"] == 100 and latest["charging"] is True
    assert latest["lat"] == 47.9466
    assert latest["last_pos_at"].replace(tzinfo=timezone.utc) == t1
    assert latest["gps_valid_last"] is False
    assert db["anoloc_positions"].count_documents({"device_id": DID}) == 1


def test_imei_inconnu(db):
    st = B.ingest(db, [dict(msg(NOW), ident="123456789012345")], "flespi", now=NOW)
    assert st["unknown"] == 1
    assert db["balises_unknown"].find_one({"_id": "123456789012345"})["count"] == 1
    assert IMEI in B.whitelist(db) and "123456789012345" not in B.whitelist(db)


def test_etat_en_ligne_par_periode(db):
    B.ingest(db, [msg(NOW - timedelta(minutes=20))], "flespi", now=NOW)
    doc = db["anoloc_latest"].find_one({"_id": DID})
    s = B.device_state(doc, now=NOW, settings=B.get_settings(db))
    assert s["online"] is True and s["gps_fix"] == 1 and s["expected_s"] == 900
    # En mouvement, periode courte : muette bien plus vite
    db["anoloc_latest"].update_one({"_id": DID}, {"$set": {"movement": True}})
    doc = db["anoloc_latest"].find_one({"_id": DID})
    assert B.device_state(doc, now=NOW, settings=B.get_settings(db))["online"] is False
    # 3 periodes a l'arret depassees
    s = B.device_state(dict(doc, movement=False), now=NOW + timedelta(minutes=40),
                       settings=B.get_settings(db))
    assert s["online"] is False


def test_etat_anoloc_inchange():
    doc = {"_id": "anoloc-1", "status": "running", "last_real_at": NOW - timedelta(minutes=2)}
    assert B.device_state(doc, now=NOW) ["gps_fix"] == 1
    doc["last_real_at"] = NOW - timedelta(minutes=40)
    assert B.device_state(doc, now=NOW)["online"] is False


def test_suppression_retire_des_groupes(db):
    B.ingest(db, [msg(NOW)], "flespi", now=NOW)
    B.delete_device(db, IMEI)
    grp = db["anoloc_config"].find_one({"_id": "global"})["beacon_groups"][0]
    assert DID not in grp["anoloc_device_ids"] and DID not in grp["device_labels"]
    assert db["anoloc_latest"].find_one({"_id": DID}) is None
    assert B.ingest(db, [msg(NOW + timedelta(seconds=1))], "flespi", now=NOW)["unknown"] == 1
