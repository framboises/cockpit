"""Intrusions confirmees envoyees aux tablettes (intrusion_dispatch.py)
contre un MongoDB local jetable (meme principe que test_patrols). Position
de la camera et copie de la photo simulees ; jamais de push reel."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import dispatch_auto as DA
import event_courant
import field
import intrusion_dispatch as ID
import patrols as P

NOW = datetime.now(timezone.utc).replace(microsecond=0)
LAT, LNG = 47.95, 0.21
SQUARE = {"type": "Polygon", "coordinates": [[[0.20, 47.94], [0.22, 47.94], [0.22, 47.96], [0.20, 47.96], [0.20, 47.94]]]}
CAM, CAM2 = ObjectId(), ObjectId()


@pytest.fixture
def db(monkeypatch):
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_intrusions_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    monkeypatch.setattr(field, "send_push_to_device", lambda *a, **k: None)
    monkeypatch.setattr(event_courant, "current_event", lambda db, now=None: ("SAISON", 2026))
    monkeypatch.setattr(P, "_zones", lambda db: [{"id": "z1", "label": "Zone 1", "geometry": SQUARE}])
    monkeypatch.setattr(ID, "_camera_point", lambda db, ep: (LAT, LNG))
    monkeypatch.setattr(ID, "_copy_photo", lambda rel, ev, yr: ("/field/photos/p.jpg", "/field/photos/t.jpg"))
    d["anoloc_config"].insert_one({"_id": "global", "beacon_groups": [
        {"id": "g1", "label": "Secu", "pco_category": "PCO.Securite"}]})
    yield d
    client.drop_database(name)


def _dev(db, name, meters_north=0, zones=None):
    doc = {"_id": ObjectId(), "name": name, "event": "SAISON", "year": "2026", "revoked": False,
           "beacon_group_id": "g1", "status": "patrouille", "last_seen": NOW - timedelta(seconds=10),
           "last_position": {"lat": LAT + meters_north / 111_000.0, "lng": LNG, "ts": NOW},
           "competence_zones": zones or []}
    db["field_devices"].insert_one(doc)
    return doc


def _group(db, mode="ai", **kw):
    fd = dict({"mode": mode}, **kw)
    g = {"_id": ObjectId(), "name": "Campus", "members": [{"camera_id": CAM, "channel": 1},
                                                         {"camera_id": CAM2, "channel": 1}],
         "field_dispatch": fd}
    db["detection_groups"].insert_one(g)
    return g


def _ep(db, cam=CAM, label="personne", status="confirmed", age_s=20, **kw):
    ep = dict({"_id": ObjectId(), "camera_id": cam, "channel": 1, "camera_name": "Porte Houx", "channel_name": "Porte Houx",
               "started_at": NOW - timedelta(seconds=age_s), "last_at": NOW - timedelta(seconds=age_s - 5),
               "ai": {"status": status, "label": label, "conf": 0.93, "photo": "intrusions/x.jpg"},
               "cover": {"photo": "intrusions/x.jpg"}, "target_types": ["human"]}, **kw)
    db["hik_intrusions"].insert_one(ep)
    return ep


def test_reglage_par_defaut_et_bornes():
    st = ID.clean_settings({"mode": "ai", "labels": ["vehicule", "x"], "burst_min": 999, "max_per_hour": 0})
    assert st == {"mode": "ai", "labels": ["vehicule"], "burst_min": 120, "max_per_hour": 1, "category": "PCO.Securite"}
    assert ID.clean_settings(None)["mode"] == "off"


def test_mode_ia_fiche_et_proposition(db):
    dev = _dev(db, "A")
    _group(db)
    ep = _ep(db)
    assert ID.tick(db, now=NOW) == 1
    f = db["pcorg"].find_one({"content_category.intrusion_episode": str(ep["_id"])})
    assert f["category"] == "PCO.Securite" and f["gps"]["coordinates"] == [LNG, LAT]
    assert f["text"] == "Intrusion : personne detectee - Porte Houx"
    assert f["content_category"]["intrusion_photo"] == "/field/photos/p.jpg"
    d = f["dispatch"]
    assert d["state"] == "proposing" and d["current"]["device_id"] == str(dev["_id"])
    # Proposition courte : 60 s
    assert (d["current"]["expires_at"] - d["current"]["proposed_at"]).total_seconds() == 60
    prop = DA.proposal_for_device(db, db["field_devices"].find_one({"_id": dev["_id"]}), now=NOW)
    assert prop["intrusion"]["label"] == "personne" and prop["intrusion"]["photo"] == "/field/photos/p.jpg"
    # Un seul envoi par episode
    assert ID.tick(db, now=NOW) == 0
    assert db["hik_intrusions"].find_one({"_id": ep["_id"]})["field_dispatch"]["state"] == "sent"


def test_filtres(db):
    _dev(db, "A")
    _group(db)
    _ep(db, label="vehicule")                         # cible non retenue (personne seule)
    _ep(db, cam=CAM2, status="filtered")              # filtree par l'IA
    _ep(db, cam=CAM2, age_s=3600)                     # perimee
    _ep(db, cam=CAM2, ai_vote={"vote": "down", "truth": "oiseau"})   # ecartee par l'operateur
    _ep(db, cam=CAM2, armed=False)                    # hors planning
    assert ID.tick(db, now=NOW) == 0 and db["pcorg"].count_documents({}) == 0


def test_anti_rafale_et_plafond(db):
    _dev(db, "A")
    g = _group(db, burst_min=10, max_per_hour=1)
    e1, e2 = _ep(db, age_s=60), _ep(db, age_s=20)
    assert ID.tick(db, now=NOW) == 2
    fiches = list(db["pcorg"].find())
    assert len(fiches) == 1                            # la 2e intrusion rejoint la fiche en cours
    assert db["hik_intrusions"].find_one({"_id": e2["_id"]})["field_dispatch"]["state"] == "grouped"
    assert sum("Intrusion detectee" in e["text"] for e in fiches[0]["comment_history"]) == 2
    # Autre camera du groupe, plafond de 1 par heure atteint : PC Org seulement
    e3 = _ep(db, cam=CAM2)
    ok, code, fid = ID.dispatch_episode(db, e3, now=NOW, settings=ID.group_settings(g), group=g)
    assert code == "pc_org" and not db["pcorg"].find_one({"_id": fid}).get("dispatch", {}).get("state")


def test_zone_de_competence(db):
    _dev(db, "Ailleurs", zones=["z9"])
    ok_dev = _dev(db, "Ici", meters_north=500, zones=["z1"])
    _group(db)
    _ep(db)
    ID.tick(db, now=NOW)
    f = db["pcorg"].find_one()
    assert f["content_category"]["intrusion_zone_ids"] == ["z1"]
    assert f["dispatch"]["current"]["device_id"] == str(ok_dev["_id"])


def test_mode_operateur(db):
    _dev(db, "A")
    _group(db, mode="operator", labels=["personne", "vehicule"])
    ep = _ep(db, label="vehicule", status="filtered")
    assert ID.tick(db, now=NOW) == 0                  # rien sans l'avis de l'operateur
    db["hik_intrusions"].update_one({"_id": ep["_id"]}, {"$set": {"ai_vote": {"vote": "down", "truth": "vehicule"}}})
    assert ID.on_operator_truth(db, ep["_id"], "vehicule", "Ludo") == "sent"
    f = db["pcorg"].find_one()
    assert f["text"].startswith("Intrusion : vehicule detecte") and "Envoyee par Ludo" in f["comment_history"][0]["text"]
    assert ID.on_operator_truth(db, ep["_id"], "vehicule", "Ludo") is None


def test_position_estimee_si_canal_cale(db):
    import detection_calib as C
    _dev(db, "A")
    _group(db)
    # Calage simple : u -> 100 m d'est en ouest, v -> 100 m vers le nord
    db["detection_calib"].insert_one({"_id": "%s:1" % CAM, "origin": {"lat": LAT, "lng": LNG}, "w_sign": 1,
                                      "H": [[100, 0, -50], [0, 100, 0], [0, 0, 1]], "err_m": 2})
    _ep(db, cover={"photo": "intrusions/x.jpg", "rect": {"x": 0.55, "y": 0.3, "w": 0.1, "h": 0.2}})
    ID.tick(db, now=NOW)
    f = db["pcorg"].find_one()
    x, y = C._enu({"lat": LAT, "lng": LNG}, f["gps"]["coordinates"][1], f["gps"]["coordinates"][0])
    assert round(x) == 10 and round(y) == 50
    assert f["content_category"]["intrusion_position"] == {"source": "estimee", "dist_m": 51, "err_m": 5}
    assert any("a 51 m de la camera (+/- 5 m)" in e["text"] for e in f["comment_history"])


def test_acceptation_met_la_patrouille_en_pause(db):
    dev = _dev(db, "A")
    _group(db)
    _ep(db)
    ID.tick(db, now=NOW)
    f = db["pcorg"].find_one()
    assert DA.accept(db, f["_id"], db["field_devices"].find_one({"_id": dev["_id"]}), now=NOW) == (True, "ok")
    d = db["field_devices"].find_one({"_id": dev["_id"]})
    assert d["status"] == "intervention" and d["active_fiche_id"] == f["_id"]
