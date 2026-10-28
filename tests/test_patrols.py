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

    def test_fusion(self, db):
        sc = _scenario(db)                          # P1, P2, P3
        p1, p2, p3 = sc["points"]
        db["patrol_points"].update_one({"_id": ObjectId(p3)}, {"$set": {"aliases": ["Porte B"], "instruction": "cadenas"}})
        other, _ = P.save_scenario(db, {"name": "Autre", "points": [p3, p2]}, "t")
        out, err = P.merge_points(db, p1, p3, "t")
        assert not err and out["scenarios"] == 2
        keep = db["patrol_points"].find_one({"_id": ObjectId(p1)})
        assert keep["name"] == "P1" and keep["aliases"] == ["P3", "Porte B"] and keep["instruction"] == "cadenas"
        drop = db["patrol_points"].find_one({"_id": ObjectId(p3)})
        assert drop["merged_into"] == p1 and drop["active"] is False
        # Doublon retire, trajet recalcule, version incrementee
        s1 = db["patrol_scenarios"].find_one({"_id": sc["_id"]})
        assert s1["points"] == [p1, p2] and s1["estimate"]["distance_m"] == 1000 and s1["version"] == 2
        assert db["patrol_scenarios"].find_one({"_id": other["_id"]})["points"] == [p1, p2]
        assert P.merge_points(db, p1, p3, "t")[1] == "not_found"      # deja fusionne
        assert P.merge_points(db, p1, p1, "t")[1] == "meme_point"

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
        # Toujours Main courante, sans position (pas de pin), sans unite engagee
        assert f["category"] == "PCO.MainCourante" and f["content_category"]["patrol_run"] == run["_id"]
        assert f["content_category"]["patrol_device"] == "A" and "patrouille" not in f["content_category"]
        assert f["gps"] is None and f["status_code"] == 0
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

    def test_non_prise_alerte_et_fiche(self, db):
        a = _dev(db, "A", status="pause")
        run, code, _ = P.create_run(db, str(_scenario(db)["_id"]), {"kind": "device", "device_id": str(a["_id"])}, "op")
        assert code == "no_device"
        al = db["cockpit_active_alerts"].find_one({"definition_slug": "patrol_not_taken"})
        assert al and al["actionData"]["run_id"] == run["_id"]
        f = db["pcorg"].find_one({"_id": al["actionData"]["pcorg_id"]})
        assert f["category"] == "PCO.MainCourante" and f["status_code"] == 10
        P.on_not_taken(db, run["_id"], NOW)          # une seule fois
        assert db["cockpit_active_alerts"].count_documents({}) == 1

    def test_vue_tablette(self, db):
        a = _dev(db, "A")
        run, _, _ = P.create_run(db, str(_scenario(db)["_id"]), {"kind": "device", "device_id": str(a["_id"])}, "op")
        active, prop = P.tablet_view(db, db["field_devices"].find_one({"_id": a["_id"]}))
        assert active is None and prop["run_id"] == run["_id"] and prop["points"] == ["P1", "P2", "P3"]
        P.accept(db, run["_id"], a)
        active, prop = P.tablet_view(db, db["field_devices"].find_one({"_id": a["_id"]}))
        assert prop is None and active["state"] == "running" and len(active["steps"]) == 3


# ---------------------------------------------------------------------------
# Planifications (livraison 2)
# ---------------------------------------------------------------------------
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
# Lundi 2 novembre 2026 (heure d'hiver)
def _paris(h, m=0, day=2):
    return datetime(2026, 11, day, h, m, tzinfo=PARIS).astimezone(timezone.utc)


def _week(fr, to):
    d = "".join("1" if fr * 4 <= q < to * 4 else "0" for q in range(96))
    return [d] * 7


def _plan(db, rule="span_start", every=60, fr=10, to=12, target=None, **kw):
    sc = _scenario(db)
    data = {"name": "Ronde du matin", "scenario_id": str(sc["_id"]),
            "target": target or {"kind": "group", "beacon_group_id": "g1"},
            "launch": {"rule": rule, "every_min": every},
            "arming": {"mode": "schedule", "weeks": [{"id": "w1", "name": "S", "grid": _week(fr, to)}],
                       "default_week": "w1", "exceptions": [], "holidays": "normal"}}
    data.update(kw)
    plan, err = P.save_plan(db, data, "test")
    assert not err, err
    # Tablettes "vues" a la date simulee des lancements (2 novembre)
    db["field_devices"].update_many({}, {"$set": {"last_seen": _paris(23)}})
    return plan


class TestPlanifications:
    def test_lancement_au_debut_de_plage_une_seule_fois(self, db):
        _dev(db, "A")
        plan = _plan(db)
        assert P.launch_plans(db, _paris(9, 50)) == 0
        assert P.launch_plans(db, _paris(10, 2)) == 1
        assert P.launch_plans(db, _paris(10, 5)) == 0          # meme creneau
        runs = list(db["patrol_runs"].find({"plan_id": str(plan["_id"])}))
        assert len(runs) == 1 and runs[0]["slot_key"].endswith("T09:00")   # 10:00 Paris = 09:00 UTC
        assert db["patrol_plans"].find_one({"_id": plan["_id"]})["last_launch"]["result"] == "proposee"

    def test_creneau_rate_pas_rattrape(self, db):
        _dev(db, "A")
        _plan(db)
        assert P.launch_plans(db, _paris(10, 40)) == 0

    def test_toutes_les_heures_et_precedente_en_cours(self, db):
        a = _dev(db, "A")
        plan = _plan(db, rule="every", every=60, fr=10, to=13)
        assert P.launch_plans(db, _paris(10, 1)) == 1
        run = db["patrol_runs"].find_one({"plan_id": str(plan["_id"])})
        P.accept(db, run["_id"], a)
        assert P.launch_plans(db, _paris(11, 1)) == 0          # precedente en cours : sautee
        assert "sautee" in db["patrol_plans"].find_one({"_id": plan["_id"]})["last_launch"]["result"]
        P.finish(db, run["_id"], a, reason="fin", abandon=True)
        assert P.launch_plans(db, _paris(12, 3)) == 1

    def test_validite_et_desactivation(self, db):
        _dev(db, "A")
        plan = _plan(db, valid_from="2026-11-03")
        assert P.launch_plans(db, _paris(10, 2)) == 0          # avant la periode
        db["patrol_plans"].update_one({"_id": plan["_id"]}, {"$set": {"valid_from": None, "enabled": False}})
        assert P.launch_plans(db, _paris(10, 2)) == 0

    def test_exception_annuelle_en_pause(self, db):
        _dev(db, "A")
        _plan(db, arming={"mode": "schedule", "weeks": [{"id": "w1", "name": "S", "grid": _week(10, 12)}],
                          "default_week": "w1", "holidays": "normal",
                          "exceptions": [{"id": "x", "label": "Toussaint", "kind": "dates", "start": "2025-11-02",
                                          "end": "2025-11-02", "yearly": True, "action": "off"}]})
        assert P.launch_plans(db, _paris(10, 2)) == 0          # 2 novembre, chaque annee
        assert P.launch_plans(db, _paris(10, 2, day=9)) == 1

    def test_prochains_lancements(self, db):
        plan = _plan(db, rule="every", every=60, fr=10, to=12)
        nxt = P.next_launches(db, plan, _paris(8), limit=4)
        assert [t.astimezone(PARIS).strftime("%d %H:%M") for t in nxt] == ["02 10:00", "02 11:00", "03 10:00", "03 11:00"]

    def test_forcage(self, db):
        _dev(db, "A")
        plan = _plan(db)
        assert P.set_plan_override(db, str(plan["_id"]), "off", 4, "op")
        assert not P.plan_active(db, db["patrol_plans"].find_one({"_id": plan["_id"]}), P._now())


class TestModeTest:
    def test_jamais_candidate_au_dispatch_auto(self, db):
        a = _dev(db, "Essai")
        db["field_devices"].update_one({"_id": a["_id"]}, {"$set": {"test_mode": True, "category": "PCO.Securite"}})
        db["pcorg"].insert_one({"_id": "f1", "event": "SAISON", "year": 2026, "category": "PCO.Securite",
                                "status_code": 0, "content_category": {},
                                "gps": {"type": "Point", "coordinates": [LNG, LAT]}})
        monkey = db["field_devices"].find_one({"_id": a["_id"]})
        assert monkey["test_mode"] is True
        assert DA.find_candidates(db, db["pcorg"].find_one({"_id": "f1"}), now=NOW) == []

    def test_patrouille_marquee_test(self, db):
        a = _dev(db, "Essai")
        db["field_devices"].update_one({"_id": a["_id"]}, {"$set": {"test_mode": True}})
        a = db["field_devices"].find_one({"_id": a["_id"]})
        run, _, _ = P.create_run(db, str(_scenario(db)["_id"]), {"kind": "device", "device_id": str(a["_id"])}, "op")
        assert P.accept(db, run["_id"], a)[0]
        f = db["pcorg"].find_one({"_id": db["patrol_runs"].find_one({"_id": run["_id"]})["fiche_id"]})
        assert f["text"].startswith("[TEST] Patrouille programmee") and "test" in f["tags"]


# ---------------------------------------------------------------------------
# Routes tablette du mode test (Flask + MongoDB local)
# ---------------------------------------------------------------------------
@pytest.fixture
def tablet(db, monkeypatch):
    import sys, types
    from flask import Flask
    import patrols_api as PA
    fake_app = types.ModuleType("app")
    fake_app.db = db
    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setattr(field, "_get_mongo_db", lambda: db)
    monkeypatch.setattr(field, "_sweep_event_if_ended", lambda *a: False)
    dev = _dev(db, "Essai")
    db["field_devices"].update_one({"_id": dev["_id"]}, {"$set": {"token_hash": field._hash_token("tokT")}})
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(field.field_bp)
    app.register_blueprint(PA.patrols_field_bp)
    c = app.test_client()
    c.set_cookie(field.FIELD_COOKIE_NAME, "tokT", path=field.FIELD_COOKIE_PATH)
    return c, dev


def _test_on(db, dev, on=True):
    db["field_devices"].update_one({"_id": dev["_id"]}, {"$set": {"test_mode": on}})


class TestOutilsTablette:
    def test_position_simulee_reservee_au_mode_test(self, tablet, db):
        c, dev = tablet
        body = {"lat": 47.95, "lng": 0.21, "simulated": True}
        assert c.post("/field/position", json=body).status_code == 403
        _test_on(db, dev)
        assert c.post("/field/position", json=body).status_code == 200
        d = db["field_devices"].find_one({"_id": dev["_id"]})
        assert d["last_position"]["simulated"] is True
        assert db["anoloc_positions"].count_documents({}) == 0      # jamais dans les traces

    def test_outils_refuses_hors_mode_test(self, tablet):
        c, _ = tablet
        for url in ("/field/test/state", "/field/test/scenarios"):
            assert c.get(url).status_code == 403
        for url in ("/field/test/send", "/field/test/expire", "/field/test/engine", "/field/test/cancel", "/field/test/report"):
            assert c.post(url, json={}).status_code == 403

    def test_se_proposer_puis_expirer(self, tablet, db):
        c, dev = tablet
        _test_on(db, dev)
        sc = _scenario(db)
        r = c.post("/field/test/send", json={"scenario_id": str(sc["_id"])})
        assert r.status_code == 200 and r.get_json()["code"] == "proposed"
        st = c.get("/field/test/state").get_json()
        assert st["device"]["patrol_proposal"]["run_id"] == r.get_json()["run_id"]
        assert st["engine"]["alive"] is False                      # aucune tache dans le test
        assert c.post("/field/test/expire", json={}).get_json()["ok"]
        run = db["patrol_runs"].find_one({"_id": r.get_json()["run_id"]})
        assert run["state"] == "refused" and run["attempts"][0]["answer"] == "timeout"
        al = db["cockpit_active_alerts"].find_one({"definition_slug": "patrol_not_taken"})
        assert al["title"].startswith("[TEST]")

    def test_rapport(self, tablet, db):
        c, dev = tablet
        _test_on(db, dev)
        r = c.post("/field/test/report", json={"note": "point 2 pas valide", "log": [{"k": "gps", "m": "x"}],
                                               "client": {"queue": 0}})
        assert r.get_json()["ok"]
        rep = db["field_test_reports"].find_one()
        assert rep["note"] == "point 2 pas valide" and rep["log"][0]["k"] == "gps" and rep["server"]["device"]["name"] == "Essai"


class TestCouchesLooker:
    def _layer(self, db, lat=47.951):
        db["categories_config"].delete_many({})
        db["categories_config"].insert_one({"categories": [
            {"label": "Tribunes", "collection_name": "tribunes", "searchField": "properties.NUMERO 2026", "dataType": "geojson"}]})
        db["tribunes"].delete_many({})
        db["tribunes"].insert_one({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.21, lat]},
             "properties": {"_id_feature": "f1", "NUMERO 2026": "01", "NOM": "TERTRE ROUGE", "NB ACCES": "3", "POLYGONE": ""}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.22, 47.952]},
             "properties": {"_id_feature": "f2", "NUMERO 2026": "02", "NOM": "VIRAGE"}}]})

    def test_import_et_suivi(self, db):
        self._layer(db)
        items = P.layer_items(db, "tribunes")
        assert [i["name"] for i in items] == ["Tribune 01 - TERTRE ROUGE", "Tribune 02 - VIRAGE"]
        assert items[0]["info"] == "NB ACCES : 3"
        ids, err = P.import_points(db, "tribunes", ["f1", "f2"], "t")
        assert not err and len(ids) == 2
        p = db["patrol_points"].find_one({"_id": ObjectId(ids[0])})
        assert p["layer_ref"] == {"collection": "tribunes", "fid": "f1"} and p["zone_ids"] == ["z1"]
        # Re-import : pas de doublon
        assert P.import_points(db, "tribunes", ["f1"], "t")[0] == [ids[0]]
        # L'objet bouge dans Looker : le point suit ; nom et position non modifiables a la main
        self._layer(db, lat=47.9555)
        assert P.sync_layer_points(db) == 1
        assert abs(db["patrol_points"].find_one({"_id": ObjectId(ids[0])})["lat"] - 47.9555) < 1e-9
        P.save_point(db, {"name": "Autre", "lat": 1, "lng": 1, "radius_m": 40}, "t", point_id=ids[0])
        p = db["patrol_points"].find_one({"_id": ObjectId(ids[0])})
        assert p["name"] == "Tribune 01 - TERTRE ROUGE" and p["radius_m"] == 40 and abs(p["lat"] - 47.9555) < 1e-9
        # Objet supprime de la couche : marque absent
        db["tribunes"].update_one({}, {"$pull": {"features": {"properties._id_feature": "f1"}}})
        P.sync_layer_points(db)
        assert db["patrol_points"].find_one({"_id": ObjectId(ids[0])})["layer_missing"] is True


class TestPropositions:
    def test_ronde_dans_la_duree(self, db, monkeypatch):
        import patrols_propose as PP
        monkeypatch.setattr(P, "_matrix", lambda coords, travel: (None, "skip"))
        monkeypatch.setattr(PP, "_gazetteer", lambda db: [])
        # 3 foyers de surete (dont un sur 2 editions), 1 fiche technique ignoree
        def fiche(i, lat, year, cat="PCO.Securite", urg="UA"):
            db["pcorg"].insert_one({"_id": "p%d" % i, "event": "24H", "year": year, "category": cat, "niveau_urgence": urg,
                                    "text": "intrusion", "gps": {"type": "Point", "coordinates": [0.21, lat]},
                                    "ts": NOW, "content_category": {"sous_classification": "Intrusion"}})
        for i in range(4):
            fiche(i, 47.950, 2025 if i % 2 else 2026)
        for i in range(4, 6):
            fiche(i, 47.955, 2026)
        fiche(6, 47.960, 2026, urg="IMP")
        fiche(7, 47.950, 2026, cat="PCO.Technique")
        foyers, st = PP.build_foyers(db, "24H", "surete", "pedestrian", source="pco")
        assert st["fiches"] == 7 and st["placees"] == 7
        assert len(foyers) == 3 and foyers[0]["n"] == 4 and foyers[0]["editions"] == [2025, 2026]
        r = PP.propose(db, "surete", 30, "pedestrian", "24H", source="pco")
        assert r["stops"] and r["stops"][0]["n"] == 4
        assert r["estimate"]["total_s"] <= 30 * 60 * 1.1
        # Creation : points (foyers) + scenario avec la dominante
        sc, err = PP.create_from_proposal(db, {"stops": r["stops"], "dominante": "surete", "travel": "pedestrian",
                                               "event": "24H", "name": "Ronde test"}, "t")
        assert not err and sc["dominante"] == "surete" and len(sc["points"]) == len(r["stops"])

    def test_masquage(self):
        import patrols_propose as PP
        assert PP.mask("M. Dupont au 06 12 34 56 78, AB-123-CD, x@y.fr") == "[personne] au [tel], [plaque], [email]"


class TestPropositionsPCS:
    def test_lieux_cites_et_autres_noms(self, db, monkeypatch):
        import patrols_propose as PP
        monkeypatch.setattr(P, "_matrix", lambda coords, travel: (None, "skip"))
        monkeypatch.setattr(PP, "_gazetteer", lambda db: [])
        def fiche(i, text, area="PCS/Stade 1/PEC"):
            db["pcorg"].insert_one({"_id": "s%d" % i, "event": "SAISON", "year": 2026, "category": "PCS.Surete",
                                    "text": text, "area": {"desc": area}, "ts": NOW, "content_category": {}})
        for i in range(5):
            fiche(i, "Intrusion constatee")                      # zone de saisie PEC
        fiche(5, "Prise de service PS Nord")                     # jamais comptee
        fiche(6, "Ronde de verification pole europeen")          # autre nom du point
        _, st = PP.build_foyers(db, "SAISON", "surete", "pedestrian", source="pcs")
        assert st["fiches"] == 6 and st["placees"] == 0
        assert st["non_placees"][0] == {"lieu": "PEC", "n": 6}     # la ronde a aussi la zone PEC
        # Le lieu est place comme point, avec un autre nom : tout se place
        P.save_point(db, {"name": "PEC", "lat": LAT, "lng": LNG, "aliases": "pole europeen"}, "t")
        foyers, st = PP.build_foyers(db, "SAISON", "surete", "pedestrian", source="pcs")
        assert st["placees"] == 6 and foyers[0]["lieu"] == "PEC" and foyers[0]["kinds"] == {"incident": 5, "ronde": 1}


class TestAvisIA:
    def test_avis_garde_et_rattache_par_position(self, db, monkeypatch):
        import patrols_propose as PP
        monkeypatch.setattr(P, "_matrix", lambda coords, travel: (None, "skip"))
        foyers = [{"id": "f0", "lat": LAT, "lng": LNG, "lieu": "A"}, {"id": "f1", "lat": LAT + 0.01, "lng": LNG, "lieu": "B"}]
        PP.save_review(db, "pcs", "SAISON", "surete", "pedestrian", foyers, {"f0": 90, "f1": 10}, {"f0": "grave"}, "ok", "t", "m")
        rv = PP.get_review(db, "pcs", "SAISON", "surete", "pedestrian")
        assert rv["comment"] == "ok" and len(rv["foyers"]) == 2
        # Numeros de foyers changes, positions identiques (+ un nouveau foyer non relu)
        new = [{"id": "f5", "lat": LAT + 0.01, "lng": LNG, "score": 40}, {"id": "f7", "lat": LAT + 0.0001, "lng": LNG, "score": 2},
               {"id": "f9", "lat": LAT + 0.05, "lng": LNG, "score": 20}]
        PP._apply_review(new, rv["foyers"], "pedestrian")
        assert [f["id"] for f in new] == ["f7", "f9", "f1"[:0] + "f5"]
        assert new[0]["score"] == 90 and new[0]["ai_reason"] == "grave" and new[1]["score"] == 25.0
