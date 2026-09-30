"""Moteur de dispatch (dispatch_auto.py) contre un MongoDB local jetable.

Les transitions reposent sur des mises a jour atomiques conditionnelles et
sur les pipelines d'update de pcorg_history : une base simulee ne prouverait
rien. Base `titan_test_dispatch_<pid>`, supprimee en fin de test ; test
saute si aucun MongoDB n'ecoute sur localhost.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import dispatch_auto as DA
import field

NOW = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)
FICHE_LAT, FICHE_LNG = 47.95, 0.21


@pytest.fixture
def db(monkeypatch):
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_dispatch_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    monkeypatch.setattr(field, "send_push_to_device", lambda *a, **k: None)
    d["anoloc_config"].insert_one({"_id": "global", "beacon_groups": []})
    yield d
    client.drop_database(name)


def _dev(db, name, meters_north=0, category="PCO.Technique", metiers=None,
         status="patrouille", pos_age_s=30, seen_age_s=10):
    lat = FICHE_LAT + meters_north / 111_000.0
    doc = {
        "_id": ObjectId(), "name": name, "event": "E", "year": "2026", "revoked": False,
        "category": category, "metiers": metiers or [], "status": status,
        "last_seen": NOW - timedelta(seconds=seen_age_s),
        "last_position": {"lat": lat, "lng": FICHE_LNG, "ts": NOW - timedelta(seconds=pos_age_s)},
    }
    db["field_devices"].insert_one(doc)
    return doc


def _fiche(db, fid="f1", urgence="UA", metier="Electricite", category="PCO.Technique"):
    db["pcorg"].insert_one({
        "_id": fid, "event": "E", "year": 2026, "category": category,
        "text": "panne", "niveau_urgence": urgence, "status_code": 0,
        "gps": {"type": "Point", "coordinates": [FICHE_LNG, FICHE_LAT]},
        "content_category": {"sous_classification": metier},
        "comment": "", "comment_history": [],
    })
    return fid


def _set_cfg(db, **tech):
    DA.save_config(db, {"categories": {"PCO.Technique": tech}}, "test")


def _state(db, fid="f1"):
    return (db["pcorg"].find_one({"_id": fid}) or {}).get("dispatch") or {}


def _texts(db, fid="f1"):
    return [e["text"] for e in db["pcorg"].find_one({"_id": fid})["comment_history"]]


class TestConfig:
    def test_defauts(self, db):
        cfg = DA.get_config(db)["categories"]
        assert cfg["PCO.Technique"]["mode"] == "urgency"
        assert cfg["PCO.Technique"]["levels"] == ["UA", "EU"]
        assert cfg["PCO.Technique"]["self_close"] is True
        assert cfg["PCO.Securite"]["mode"] == "always"
        assert cfg["PCO.Secours"]["mode"] == "never"

    def test_declenchement(self, db):
        cfg = DA.get_config(db)
        f = {"category": "PCO.Technique", "niveau_urgence": "UA", "status_code": 0, "content_category": {}}
        assert DA.wants_auto(cfg, f)
        assert not DA.wants_auto(cfg, dict(f, niveau_urgence="UR"))
        assert DA.wants_auto(cfg, dict(f, category="PCO.Securite", niveau_urgence=None))
        assert not DA.wants_auto(cfg, dict(f, category="PCO.Secours", niveau_urgence="EU"))
        assert not DA.wants_auto(cfg, dict(f, content_category={"patrouille": "X"}))

    def test_nettoyage(self, db):
        cfg = DA.save_config(db, {"categories": {"PCO.Technique": {
            "mode": "n_importe", "levels": ["EU", "ZZ"], "timeout_s": 1,
            "managers": ["A@B.fr", "a@b.fr", "pas-un-mail"]}}}, "t")["categories"]["PCO.Technique"]
        assert cfg["mode"] == "urgency"          # valeur invalide ignoree
        assert cfg["levels"] == ["EU"]
        assert cfg["timeout_s"] == 10            # borne basse
        assert cfg["managers"] == ["a@b.fr"]


class TestCandidats:
    def test_plus_proche_disponible(self, db):
        _dev(db, "Loin", 800)
        near = _dev(db, "Pres", 100)
        _dev(db, "Occupe", 10, status="intervention")
        _dev(db, "Secu", 5, category="PCO.Securite")
        _dev(db, "Muet", 5, seen_age_s=3600)
        _fiche(db)
        names = [c["device"]["name"] for c in DA.find_candidates(db, db["pcorg"].find_one({"_id": "f1"}), now=NOW)]
        assert names == ["Pres", "Loin"]
        assert near["name"] == names[0]

    def test_metier_sans_accent(self, db):
        _dev(db, "Plombier", 10, metiers=["Sanitaire"])
        _dev(db, "Elec", 50, metiers=["Electricité"])
        _dev(db, "Polyvalent", 90)
        _fiche(db, metier="Electricite")
        names = [c["device"]["name"] for c in DA.find_candidates(db, db["pcorg"].find_one({"_id": "f1"}), now=NOW)]
        assert names == ["Elec", "Polyvalent"]

    def test_position_fraiche_avant_position_perimee(self, db):
        _dev(db, "PresMaisVieux", 20, pos_age_s=1200)
        _dev(db, "LoinFrais", 600, pos_age_s=20)
        _fiche(db)
        names = [c["device"]["name"] for c in DA.find_candidates(db, db["pcorg"].find_one({"_id": "f1"}), now=NOW)]
        assert names == ["LoinFrais", "PresMaisVieux"]


class TestTournee:
    def test_proposition_puis_acceptation(self, db):
        a = _dev(db, "A", 100)
        _fiche(db)
        assert DA.start(db, "f1", now=NOW) == (True, "ok")
        st = _state(db)
        assert st["state"] == "proposing" and st["current"]["device_name"] == "A"
        dev = db["field_devices"].find_one({"_id": a["_id"]})
        assert dev["pending_proposal"]["fiche_id"] == "f1"
        prop = DA.proposal_for_device(db, dev, now=NOW + timedelta(seconds=5))
        assert prop["fiche_id"] == "f1" and prop["remaining_s"] == 25

        assert DA.accept(db, "f1", dev, now=NOW + timedelta(seconds=5)) == (True, "ok")
        fiche = db["pcorg"].find_one({"_id": "f1"})
        assert fiche["content_category"]["patrouille"] == "A"
        assert fiche["dispatch"]["state"] == "assigned"
        assert fiche["intervention"]["engaged_at"]
        assert "content_category.patrouille" in fiche["cockpit_owned"]
        dev = db["field_devices"].find_one({"_id": a["_id"]})
        assert dev["status"] == "intervention" and dev["active_fiche_id"] == "f1"
        assert dev["pending_proposal"] is None
        assert any(t.startswith("Proposee a A") for t in _texts(db))

    def test_refus_puis_suivant_puis_file(self, db):
        _set_cfg(db, max_attempts=2)
        a = _dev(db, "A", 100)
        b = _dev(db, "B", 300)
        _dev(db, "C", 500)
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        assert DA.refuse(db, "f1", a, now=NOW + timedelta(seconds=3))
        assert _state(db)["current"]["device_name"] == "B"
        # A est liberee de sa proposition
        assert db["field_devices"].find_one({"_id": a["_id"]})["pending_proposal"] is None
        assert DA.refuse(db, "f1", b, now=NOW + timedelta(seconds=6))
        st = _state(db)
        assert st["state"] == "queued" and st["current"] is None
        assert "2 proposition(s)" in st["queue_reason"]
        assert [x["answer"] for x in st["attempts"]] == ["refused", "refused"]

    def test_sans_reponse_via_tick(self, db):
        _dev(db, "A", 100)
        _dev(db, "B", 300)
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        assert DA.tick(db, now=NOW + timedelta(seconds=31)) == 0   # delai de grace
        assert DA.tick(db, now=NOW + timedelta(seconds=40)) == 1
        st = _state(db)
        assert st["current"]["device_name"] == "B"
        assert st["attempts"][0]["answer"] == "timeout"
        assert any(t == "Sans reponse de A" for t in _texts(db))

    def test_acceptation_trop_tardive_ou_mauvaise_unite(self, db):
        a = _dev(db, "A", 100)
        b = _dev(db, "B", 300)
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        assert DA.accept(db, "f1", b, now=NOW) == (False, "proposition_expiree")
        assert DA.accept(db, "f1", a, now=NOW + timedelta(seconds=60)) == (False, "proposition_expiree")

    def test_aucune_unite(self, db):
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        st = _state(db)
        assert st["state"] == "queued" and st["queue_reason"] == "aucune unite disponible"

    def test_une_seule_proposition_par_unite(self, db):
        _dev(db, "A", 100)
        _fiche(db, "f1")
        _fiche(db, "f2")
        DA.start(db, "f1", now=NOW)
        DA.start(db, "f2", now=NOW)
        assert _state(db, "f1")["current"]["device_name"] == "A"
        assert _state(db, "f2")["state"] == "queued"

    def test_engagement_direct_annule_la_proposition(self, db):
        a = _dev(db, "A", 100)
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        DA.on_manual_assign(db, "f1", by="PC Org", now=NOW)
        assert _state(db)["state"] == "manual"
        assert db["field_devices"].find_one({"_id": a["_id"]})["pending_proposal"] is None

    def test_maybe_auto_start_respecte_la_config(self, db):
        _dev(db, "A", 100)
        _fiche(db, "f1", urgence="UR")
        assert not DA.maybe_auto_start(db, "f1")
        _fiche(db, "f2", urgence="EU")
        assert DA.maybe_auto_start(db, "f2")


class TestFileEtFin:
    def test_engagement_par_responsable_sans_ecraser(self, db):
        busy = _dev(db, "Occupe", 100, status="intervention")
        db["field_devices"].update_one({"_id": busy["_id"]}, {"$set": {"active_fiche_id": "autre"}})
        _fiche(db)
        dev = db["field_devices"].find_one({"_id": busy["_id"]})
        assert DA.manual_assign(db, "f1", dev, "Chef", now=NOW) == (True, "ajoutee_aux_missions")
        assert db["field_devices"].find_one({"_id": busy["_id"]})["active_fiche_id"] == "autre"
        fiche = db["pcorg"].find_one({"_id": "f1"})
        assert fiche["content_category"]["patrouille"] == "Occupe"
        assert fiche["dispatch"]["assigned_by"] == "Chef"

    def test_responsable_refuse_categorie_differente(self, db):
        secu = _dev(db, "Secu", 100, category="PCO.Securite")
        _fiche(db)
        assert DA.manual_assign(db, "f1", secu, "Chef") == (False, "categorie_differente")

    def _engaged(self, db):
        a = _dev(db, "A", 100)
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        DA.accept(db, "f1", db["field_devices"].find_one({"_id": a["_id"]}), now=NOW)
        return db["field_devices"].find_one({"_id": a["_id"]})

    def test_fin_resolue_clot_la_fiche(self, db):
        dev = self._engaged(db)
        assert DA.finish(db, "f1", dev, "resolu", "ok") == (False, "compte_rendu_obligatoire")
        assert DA.finish(db, "f1", dev, "resolu", "Disjoncteur rearme", now=NOW) == (True, "ok")
        fiche = db["pcorg"].find_one({"_id": "f1"})
        assert fiche["status_code"] == 10
        assert fiche["intervention"]["outcome"] == "resolu"
        assert fiche["dispatch"]["state"] == "done"
        dev = db["field_devices"].find_one({"_id": dev["_id"]})
        assert dev["status"] == "patrouille" and dev["active_fiche_id"] is None
        # Renvoi par la file hors ligne : sans effet, sans erreur
        assert DA.finish(db, "f1", dev, "resolu", "Disjoncteur rearme") == (True, "deja_termine")

    def test_fin_non_resolue_retour_en_file(self, db):
        dev = self._engaged(db)
        assert DA.finish(db, "f1", dev, "materiel", "Il faut un tableau neuf", now=NOW) == (True, "ok")
        fiche = db["pcorg"].find_one({"_id": "f1"})
        assert fiche["status_code"] == 0
        assert fiche["content_category"]["patrouille"] == ""
        assert fiche["dispatch"]["state"] == "queued"
        assert "materiel" in fiche["dispatch"]["queue_reason"]
        assert db["field_devices"].find_one({"_id": dev["_id"]})["status"] == "patrouille"

    def test_cloture_terrain_desactivee(self, db):
        _set_cfg(db, self_close=False)
        dev = self._engaged(db)
        assert DA.finish(db, "f1", dev, "resolu", "Disjoncteur rearme") == (False, "cloture_terrain_desactivee")

    def test_etapes_horodatees_une_seule_fois(self, db):
        _fiche(db)
        DA.mark_step(db, "f1", "arrived_at", now=NOW)
        DA.mark_step(db, "f1", "arrived_at", now=NOW + timedelta(minutes=5))
        at = db["pcorg"].find_one({"_id": "f1"})["intervention"]["arrived_at"]
        assert at.replace(tzinfo=timezone.utc) == NOW


class TestRoutesTablette:
    def test_bout_en_bout(self, db, monkeypatch):
        from flask import Flask
        monkeypatch.setattr(field, "_get_mongo_db", lambda: db)
        monkeypatch.setattr(field, "_sweep_event_if_ended", lambda *a: False)
        app = Flask(__name__)
        app.register_blueprint(field.field_bp)
        client = app.test_client()
        a = _dev(db, "A", 100)
        db["field_devices"].update_one({"_id": a["_id"]}, {"$set": {"token_hash": field._hash_token("tokA")}})
        client.set_cookie(field.FIELD_COOKIE_NAME, "tokA", path=field.FIELD_COOKIE_PATH)
        js = {"Accept": "application/json"}
        _fiche(db)
        DA.start(db, "f1")

        data = client.get("/field/my-fiches", headers=js).get_json()
        assert data["proposal"]["fiche_id"] == "f1"
        assert data["self_close"] is True
        assert data["open"] == []                 # pas encore affectee

        rep = client.post("/field/proposal/f1/accept", headers=js, json={})
        assert rep.status_code == 200
        data = client.get("/field/my-fiches", headers=js).get_json()
        assert data["proposal"] is None
        assert [f["id"] for f in data["open"]] == ["f1"]
        assert data["device_status"] == "intervention"

        rep = client.post("/field/my-fiches/f1/finish", headers=js, json={"outcome": "resolu", "report": ""})
        assert rep.status_code == 400 and rep.get_json()["error"] == "compte_rendu_obligatoire"
        rep = client.post("/field/my-fiches/f1/finish", headers=js,
                          json={"outcome": "resolu", "report": "Prise remplacee"})
        assert rep.status_code == 200
        assert db["pcorg"].find_one({"_id": "f1"})["status_code"] == 10

    def test_pause_vaut_refus(self, db, monkeypatch):
        from flask import Flask
        monkeypatch.setattr(field, "_get_mongo_db", lambda: db)
        monkeypatch.setattr(field, "_sweep_event_if_ended", lambda *a: False)
        app = Flask(__name__)
        app.register_blueprint(field.field_bp)
        client = app.test_client()
        a = _dev(db, "A", 100)
        _dev(db, "B", 300)
        db["field_devices"].update_one({"_id": a["_id"]}, {"$set": {"token_hash": field._hash_token("tokA")}})
        client.set_cookie(field.FIELD_COOKIE_NAME, "tokA", path=field.FIELD_COOKIE_PATH)
        _fiche(db)
        DA.start(db, "f1")
        rep = client.post("/field/status", headers={"Accept": "application/json"}, json={"status": "pause"})
        assert rep.status_code == 200
        assert _state(db)["current"]["device_name"] == "B"


def test_reengagement_libere_l_ancienne_unite(db):
    a = _dev(db, "A", 100)
    b = _dev(db, "B", 300)
    _fiche(db)
    DA.start(db, "f1", now=NOW)
    DA.accept(db, "f1", db["field_devices"].find_one({"_id": a["_id"]}), now=NOW)
    ok, _ = DA.manual_assign(db, "f1", db["field_devices"].find_one({"_id": b["_id"]}), "Chef", now=NOW)
    assert ok
    old = db["field_devices"].find_one({"_id": a["_id"]})
    assert old["status"] == "patrouille" and old["active_fiche_id"] is None
    assert db["field_devices"].find_one({"_id": b["_id"]})["active_fiche_id"] == "f1"
