"""Alertes des groupes de balises (beacon_alerts.py) contre un MongoDB local
jetable : memes regles pour une balise Anoloc et une balise Teltonika."""
import os
from datetime import datetime, timezone, timedelta

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import balises_core as B
import beacon_alerts as BA

T0 = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)
TLT = "tlt:866107086018948"
ANO = "12345"
# Carre ~ 220 m x 150 m autour de (47.95, 0.21)
ZONE = {"type": "Polygon", "coordinates": [[[0.209, 47.949], [0.211, 47.949], [0.211, 47.951],
                                            [0.209, 47.951], [0.209, 47.949]]]}
IN, OUT = (47.950, 0.210), (47.960, 0.230)


@pytest.fixture
def db():
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_beacon_alerts_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    B.ensure_indexes(d)
    d["anoloc_config"].insert_one({"_id": "global", "enabled": True, "beacon_groups": [
        {"id": "grp-a", "label": "Medical", "enabled": True,
         "anoloc_device_ids": [TLT, ANO], "device_labels": {}},
    ]})
    d["balises_devices"].insert_one({"_id": TLT, "imei": "866107086018948", "label": "M12",
                                     "enabled": True})
    d["zones_test"].insert_one({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": ZONE, "properties": {"_id_feature": "z1", "nom": "Parking A"}}]})
    yield d
    client.drop_database(name)


def latest(db, did, t, lat, lng, **kw):
    doc = {"_id": did, "device_id": did, "label": "M12" if did == TLT else "A1",
           "lat": lat, "lng": lng, "speed": kw.pop("speed", 0)}
    if B.is_teltonika_id(did):
        doc.update({"last_msg_at": t, "last_pos_at": t, "gps_valid_last": True,
                    "movement": kw.pop("movement", False)})
    else:
        doc.update({"status": kw.pop("status", "stopped"), "last_real_at": t, "sent_at": t})
    doc.update(kw)
    db["anoloc_latest"].replace_one({"_id": did}, doc, upsert=True)


def point(db, did, t, lat, lng, speed=0):
    db["anoloc_positions"].insert_one({"device_id": did, "lat": lat, "lng": lng, "speed": speed,
                                       "sent_at": t, "collected_at": t})


def run(db, dt, params, now, slug="test"):
    h = BA.make_handler(dt, lambda: db)
    return h({"slug": slug, "detection_type": dt, "params": params}, {"now": now}) or []


@pytest.mark.parametrize("did", [TLT, ANO])
def test_vitesse_points_consecutifs(db, did):
    p = {"max_kmh": 50, "consecutive": 2}
    point(db, did, T0, *IN, speed=80)
    latest(db, did, T0, *IN, speed=80)
    assert run(db, "beacon_speed", p, T0 + timedelta(seconds=5)) == []
    point(db, did, T0 + timedelta(seconds=30), *IN, speed=90)
    out = run(db, "beacon_speed", p, T0 + timedelta(seconds=35))
    assert len(out) == 1 and "90 km/h" in out[0]["message"]
    assert out[0]["actionData"]["lat"] == IN[0]
    # Episode en cours : pas de nouvelle alerte
    point(db, did, T0 + timedelta(seconds=60), *IN, speed=95)
    assert run(db, "beacon_speed", p, T0 + timedelta(seconds=65)) == []


def test_vitesse_rattrapage_ignore(db):
    old = T0 - timedelta(hours=2)
    point(db, TLT, old, *IN, speed=120)
    point(db, TLT, old + timedelta(seconds=30), *IN, speed=120)
    latest(db, TLT, T0, *IN)
    assert run(db, "beacon_speed", {"max_kmh": 50, "consecutive": 2}, T0) == []


def test_zone_hysteresis_entree_sortie(db):
    p = {"zones_collection": "zones_test", "zone_ids": ["z1"], "zone_labels": {"z1": "Parking A"},
         "event": "both", "consecutive": 2}
    latest(db, TLT, T0, *OUT)
    point(db, TLT, T0, *OUT)
    assert run(db, "beacon_zone", p, T0 + timedelta(seconds=1)) == []  # etat initial
    # Un seul point dedans (bruit GPS) : rien
    point(db, TLT, T0 + timedelta(seconds=30), *IN)
    point(db, TLT, T0 + timedelta(seconds=60), *OUT)
    assert run(db, "beacon_zone", p, T0 + timedelta(seconds=61)) == []
    # Deux points dedans : entree
    point(db, TLT, T0 + timedelta(seconds=90), *IN)
    point(db, TLT, T0 + timedelta(seconds=120), *IN)
    out = run(db, "beacon_zone", p, T0 + timedelta(seconds=121))
    assert len(out) == 1 and "entre dans Parking A" in out[0]["message"]
    # Sortie
    point(db, TLT, T0 + timedelta(seconds=150), *OUT)
    point(db, TLT, T0 + timedelta(seconds=180), *OUT)
    out = run(db, "beacon_zone", p, T0 + timedelta(seconds=181))
    assert len(out) == 1 and out[0]["title"] == "BALISE - SORTIE DE ZONE"
    # Entree seule demandee (regle neuve, rejoue les 10 dernieres min) :
    # l'entree sonne, jamais la sortie
    p2 = dict(p, event="enter")
    out = run(db, "beacon_zone", p2, T0 + timedelta(seconds=182), slug="s2")
    assert [a["title"] for a in out] == ["BALISE - ENTREE DE ZONE"]


def test_arret_prolonge(db):
    p = {"max_min": 20, "radius_m": 50}
    for i in range(5):
        point(db, TLT, T0 + timedelta(minutes=i * 5), IN[0] + 0.0001 * (i % 2), IN[1])
        latest(db, TLT, T0 + timedelta(minutes=i * 5), *IN)
        out = run(db, "beacon_stopped", p, T0 + timedelta(minutes=i * 5, seconds=10))
    assert len(out) == 1 and "immobile depuis 20 min" in out[0]["message"]
    point(db, TLT, T0 + timedelta(minutes=25), *IN)
    latest(db, TLT, T0 + timedelta(minutes=25), *IN)
    assert run(db, "beacon_stopped", p, T0 + timedelta(minutes=25, seconds=10)) == []


def test_arret_seulement_dans_les_zones(db):
    p = {"max_min": 5, "radius_m": 50, "zones_collection": "zones_test", "zone_ids": ["z1"]}
    point(db, TLT, T0, *OUT)
    latest(db, TLT, T0, *OUT)
    run(db, "beacon_stopped", p, T0)
    latest(db, TLT, T0 + timedelta(minutes=9), *OUT)
    assert run(db, "beacon_stopped", p, T0 + timedelta(minutes=9)) == []


def test_muette_sur_transition(db):
    latest(db, TLT, T0, *IN)
    assert run(db, "beacon_silent", {}, T0 + timedelta(minutes=1)) == []
    out = run(db, "beacon_silent", {}, T0 + timedelta(minutes=50))  # 3 x 15 min + 2
    assert len(out) == 1 and out[0]["title"] == "BALISE MUETTE"
    assert run(db, "beacon_silent", {}, T0 + timedelta(minutes=51)) == []


def test_muette_jamais_a_l_activation(db):
    latest(db, ANO, T0 - timedelta(days=60), *IN, status="offline")
    assert run(db, "beacon_silent", {}, T0) == []


def test_batterie(db):
    latest(db, TLT, T0, *IN, battery_pct=18)
    out = run(db, "beacon_battery", {"threshold_pct": 20}, T0)
    assert len(out) == 1 and "18 %" in out[0]["message"]
    assert run(db, "beacon_battery", {"threshold_pct": 20}, T0 + timedelta(minutes=1)) == []
    latest(db, TLT, T0, *IN, battery_pct=18, charging=True)
    run(db, "beacon_battery", {"threshold_pct": 20}, T0 + timedelta(minutes=2))
    latest(db, TLT, T0, *IN, battery_pct=17)
    assert len(run(db, "beacon_battery", {"threshold_pct": 20}, T0 + timedelta(minutes=3))) == 1


@pytest.mark.parametrize("did,kw_stop,kw_move", [
    (TLT, {"movement": False}, {"movement": True}),
    (ANO, {"status": "stopped"}, {"status": "running"}),
])
def test_mise_en_mouvement(db, did, kw_stop, kw_move):
    latest(db, did, T0, *IN, **kw_stop)
    assert run(db, "beacon_movement", {}, T0) == []
    latest(db, did, T0 + timedelta(seconds=30), *IN, **kw_move)
    assert len(run(db, "beacon_movement", {}, T0 + timedelta(seconds=31))) == 1


def test_filtre_par_groupe_et_interrupteur_general(db):
    latest(db, TLT, T0, *IN, battery_pct=5)
    assert run(db, "beacon_battery", {"groups": ["grp-autre"]}, T0) == []
    db["anoloc_config"].update_one({"_id": "global"}, {"$set": {"enabled": False}})
    assert run(db, "beacon_battery", {}, T0, slug="s3") == []
