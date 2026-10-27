"""Patrouilles programmees (patrols.py) contre un MongoDB local jetable.

Memes raisons que test_dispatch_auto : transitions atomiques conditionnelles
et pipelines de pcorg_history. Base `titan_test_patrols_<pid>`, supprimee en
fin de test ; test saute si aucun MongoDB n'ecoute sur localhost. Valhalla
n'est jamais appele (estimation simulee).
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import dispatch_auto as DA
import field
import patrols as P

NOW = datetime.now(timezone.utc).replace(microsecond=0)
LAT, LNG = 47.95, 0.21
SQUARE = {"type": "Polygon", "coordinates": [[[0.20, 47.94], [0.22, 47.94], [0.22, 47.96], [0.20, 47.96], [0.20, 47.94]]]}


@pytest.fixture
def db(monkeypatch):
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_patrols_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    monkeypatch.setattr(field, "send_push_to_device", lambda *a, **k: None)
    monkeypatch.setattr(P, "_zones", lambda db: [{"id": "z1", "label": "Zone 1", "geometry": SQUARE}])
    monkeypatch.setattr(P, "route_through", lambda coords, travel, mode="normal": (
        {"distance_m": 1000 * (len(coords) - 1), "duration_s": 120 * (len(coords) - 1), "polyline": "", "legs": []},
        None))
    monkeypatch.setattr(field.EC, "current_event", lambda db, now=None: ("SAISON", 2026))
    d["anoloc_config"].insert_one({"_id": "global", "beacon_groups": [
        {"id": "g1", "label": "Secu", "pco_category": "PCO.Securite"},
        {"id": "gd", "label": "Presta", "declarant": True}]})
    P.ensure_indexes(d)
    yield d
    client.drop_database(name)


def _dev(db, name, meters_north=0, group="g1", status="patrouille", seen_age_s=10, zones=None):
    doc = {"_id": ObjectId(), "name": name, "event": "SAISON", "year": "2026", "revoked": False,
           "beacon_group_id": group, "status": status, "last_seen": NOW - timedelta(seconds=seen_age_s),
           "last_position": {"lat": LAT + meters_north / 111_000.0, "lng": LNG, "ts": NOW},
           "competence_zones": zones or []}
    db["field_devices"].insert_one(doc)
    return doc


def _scenario(db, n=3, inside=True, photo_on=None):
    ids = []
    for i in range(n):
        lat = LAT + (0.001 * i if inside else 0.5)
        p, err = P.save_point(db, {"name": "P%d" % (i + 1), "lat": lat, "lng": LNG,
                                   "photo_required": i == photo_on}, "test")
        assert not err
        ids.append(str(p["_id"]))
    sc, err = P.save_scenario(db, {"name": "Ronde", "travel": "pedestrian", "points": ids}, "test")
    assert not err
    return sc


def _run(db, run_id):
    return db["patrol_runs"].find_one({"_id": run_id})


def _texts(db, fid):
    return [e["text"] for e in db["pcorg"].find_one({"_id": fid})["comment_history"]]


class TestPointsEtScenarios:
    def test_zones_calculees(self, db):
        p, _ = P.save_point(db, {"name": "Dedans", "lat": LAT, "lng": LNG}, "t")
        q, _ = P.save_point(db, {"name": "Dehors", "lat": 48.5, "lng": LNG}, "t")
        assert p["zone_ids"] == ["z1"] and q["zone_ids"] == []
        assert p["radius_m"] == 25

    def test_validation(self, db):
        assert P.save_point(db, {"name": "", "lat": 1, "lng": 1}, "t")[1] == "nom_obligatoire"
        assert P.save_point(db, {"name": "x", "lat": "a", "lng": 1}, "t")[1] == "position_invalide"
        assert P.save_scenario(db, {"name": "s", "points": [str(ObjectId())]}, "t")[1] == "point_inconnu"

    def test_estimation_et_version(self, db):
        sc = _scenario(db)
        assert sc["estimate"]["distance_m"] == 2000
        sc2, _ = P.save_scenario(db, {"name": "Ronde 2", "points": sc["points"], "loop": True}, "t",
                                 scenario_id=str(sc["_id"]))
        assert sc2["version"] == 2 and sc2["estimate"]["distance_m"] == 3000

    def test_ordre_optimal(self):
        # 0 -> 2 -> 1 -> 3 est le plus court (ligne : 0, 2, 1, 3 places a 0, 2, 1, 3)
        pos = [0, 2, 1, 3]
        m = [[abs(pos[i] - pos[j]) for j in range(4)] for i in range(4)]
        assert P.best_order(m, loop=False) == [0, 2, 1, 3]
        pos = list(range(14))[::-1]
        m = [[abs(pos[i] - pos[j]) for j in range(14)] for i in range(14)]
        order = P.best_order(m, loop=False)
        assert order[0] == 0 and sorted(order) == list(range(14))


class TestProposition:
    def test_plus_proche_du_groupe(self, db):
        _dev(db, "Loin", meters_north=900)
        near = _dev(db, "Pres", meters_north=50)
        _dev(db, "Muette", seen_age_s=3600)
        _dev(db, "Presta", group="gd")
        run, code, skipped = P.create_run(db, str(_scenario(db)["_id"]), {"kind": "group", "beacon_group_id": "g1"}, "op")
        assert code == "proposed"
        assert run["current"]["device_id"] == str(near["_id"])
        assert {s["name"]: s["reason"] for s in skipped} == {"Muette": "pas_vue_recemment"}
        dev = db["field_devices"].find_one({"_id": near["_id"]})
        assert dev["patrol_proposal"]["run_id"] == run["_id"]
        # Une proposition a la fois : le dispatch ne la sollicite plus
        assert DA._proposal_pending(dev, NOW)

    def test_hors_zone(self, db):
        _dev(db, "Z2", zones=["z2"])
        ok = _dev(db, "Z1", meters_north=800, zones=["z1"])
        run, code, skipped = P.create_run(db, str(_scenario(db)["_id"]), {"kind": "group", "beacon_group_id": "g1"}, "op")
        assert run["current"]["device_id"] == str(ok["_id"])
        assert skipped == [{"device_id": skipped[0]["device_id"], "name": "Z2", "reason": "hors_zone"}]

    def test_refus_puis_suivante_puis_personne(self, db):
        a = _dev(db, "A", meters_north=10)
        b = _dev(db, "B", meters_north=500)
        run, _, _ = P.create_run(db, str(_scenario(db)["_id"]), {"kind": "group", "beacon_group_id": "g1"}, "op")
        assert P.refuse(db, run["_id"], a["_id"], reason="pas dispo")
        r = _run(db, run["_id"])
        assert r["current"]["device_id"] == str(b["_id"])
        assert db["field_devices"].find_one({"_id": a["_id"]})["patrol_proposal"] is None
        # Silence de B : le moteur passe au suivant ; plus personne -> refused
        db["patrol_runs"].update_one({"_id": run["_id"]}, {"$set": {"current.expires_at": NOW - timedelta(seconds=60)}})
        assert P.tick(db) >= 1
        r = _run(db, run["_id"])
        assert r["state"] == "refused" and [x["answer"] for x in r["attempts"]] == ["refused", "timeout"]

    def test_tablette_imposee_occupee(self, db):
        a = _dev(db, "A", status="intervention")
        run, code, skipped = P.create_run(db, str(_scenario(db)["_id"]), {"kind": "device", "device_id": str(a["_id"])}, "op")
        assert code == "no_device" and run["state"] == "refused"
        assert skipped[0]["reason"] == "statut_intervention"


class TestExecution:
    def _accepted(self, db, photo_on=None):
        a = _dev(db, "A")
        run, _, _ = P.create_run(db, str(_scenario(db, photo_on=photo_on)["_id"]),
                                 {"kind": "device", "device_id": str(a["_id"])}, "op")
        ok, code = P.accept(db, run["_id"], a)
        assert ok, code
        return a, _run(db, run["_id"])

    def test_acceptation_ouvre_la_fiche(self, db):
        a, run = self._accepted(db)
        assert run["state"] == "running" and run["fiche_id"]
        f = db["pcorg"].find_one({"_id": run["fiche_id"]})
        assert f["category"] == "PCO.Securite" and f["content_category"]["patrol_run"] == run["_id"]
        assert f["content_category"]["patrouille"] == "A" and f["status_code"] == 0
        assert not DA.wants_auto(DA.get_config(db), f)        # jamais proposee au dispatch
        dev = db["field_devices"].find_one({"_id": a["_id"]})
        assert dev["active_patrol_run"] == run["_id"] and dev["patrol_proposal"] is None
        assert P.accept(db, run["_id"], a) == (True, "deja_acceptee")

    def test_points_idempotents_et_fin(self, db):
        a, run = self._accepted(db)
        ids = [s["point_id"] for s in run["steps"]]
        assert P.step(db, run["_id"], a, ids[0], "done", method="gps")[1] == "ok"
        assert P.step(db, run["_id"], a, ids[0], "done", method="gps")[1] == "deja_fait"
        assert P.step(db, run["_id"], a, ids[1], "skipped")[1] == "motif_obligatoire"
        assert P.step(db, run["_id"], a, ids[1], "skipped", comment="acces ferme")[1] == "ok"
        ok, code, r = P.step(db, run["_id"], a, ids[2], "done")
        assert r["state"] == "partial"            # dernier point : fin automatique
        f = db["pcorg"].find_one({"_id": run["fiche_id"]})
        assert f["status_code"] == 10
        texts = _texts(db, run["fiche_id"])
        assert any("Point 1/3 fait : P1 (passage GPS)" in t for t in texts)
        assert any("Point 2/3 passe : P2\nMotif : acces ferme" in t for t in texts)
        assert any("2/3 points faits, 1 passe" in t for t in texts)
        assert db["field_devices"].find_one({"_id": a["_id"]})["active_patrol_run"] is None

    def test_photo_exigee(self, db):
        a, run = self._accepted(db, photo_on=0)
        pid = run["steps"][0]["point_id"]
        assert P.step(db, run["_id"], a, pid, "done")[1] == "photo_obligatoire"
        assert P.step(db, run["_id"], a, pid, "done", photo={"photo": "/p.jpg", "thumb": "/t.jpg"})[1] == "ok"

    def test_abandon(self, db):
        a, run = self._accepted(db)
        assert P.finish(db, run["_id"], a, reason="malaise collegue", abandon=True) == (True, "ok")
        r = _run(db, run["_id"])
        assert r["state"] == "partial" and all(s["state"] == "missed" for s in r["steps"])
        assert any("Patrouille abandonnee : 0/3 points faits, 3 non faits" in t for t in _texts(db, run["fiche_id"]))

    def test_pause_pendant_une_intervention(self, db):
        a, run = self._accepted(db)
        db["field_devices"].update_one({"_id": a["_id"]}, {"$set": {"status": "intervention"}})
        P.tick(db)
        assert _run(db, run["_id"])["state"] == "paused"
        db["field_devices"].update_one({"_id": a["_id"]}, {"$set": {"status": "patrouille"}})
        P.tick(db)
        r = _run(db, run["_id"])
        assert r["state"] == "running" and r["pauses"][0]["end"]
        texts = _texts(db, run["fiche_id"])
        assert any("Patrouille en pause (agent engage" in t for t in texts)
        assert any("Patrouille reprise au point : P1" in t for t in texts)

    def test_fiche_close_par_le_pc(self, db):
        a, run = self._accepted(db)
        db["pcorg"].update_one({"_id": run["fiche_id"]}, {"$set": {"status_code": 10, "operator_close": "Op"}})
        P.tick(db)
        r = _run(db, run["_id"])
        assert r["state"] == "cancelled" and r["cancelled_by"] == "Op"

    def test_vue_tablette(self, db):
        a = _dev(db, "A")
        run, _, _ = P.create_run(db, str(_scenario(db)["_id"]), {"kind": "device", "device_id": str(a["_id"])}, "op")
        active, prop = P.tablet_view(db, db["field_devices"].find_one({"_id": a["_id"]}))
        assert active is None and prop["run_id"] == run["_id"] and prop["points"] == ["P1", "P2", "P3"]
        P.accept(db, run["_id"], a)
        active, prop = P.tablet_view(db, db["field_devices"].find_one({"_id": a["_id"]}))
        assert prop is None and active["state"] == "running" and len(active["steps"]) == 3
