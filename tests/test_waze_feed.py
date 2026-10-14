"""Fermetures publiees vers Waze (flux CIFS) : validation, heures de Paris,
cycle de vie, synchronisation avec les regles, flux public."""
import base64
import sys
import types
import defusedxml.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

import waze_feed as W
from conftest import FakeDb

# ~640 m le long de la D338 (Hunaudieres)
POLY = [[47.9375, 0.2243], [47.9349, 0.2251], [47.9319, 0.2262]]


def _closure(**kw):
    base = {"layer": "events", "doc_id": "24h-2027", "doc_name": "24h 2027",
            "rule_keys": ["way/1", "way/2"], "polyline": POLY, "street": "D338",
            "description": "24 Heures : route fermee", "direction": "BOTH_DIRECTIONS",
            "slots": [{"start": "2027-06-09T14:00", "end": "2027-06-09T20:00"}]}
    base.update(kw)
    return base


class TestValidation:
    def test_fermeture_valide(self):
        out, err = W.clean_closure(_closure())
        assert err is None
        assert out["length_m"] > 500
        assert out["slots"][0]["sid"] == "s1"
        assert out["slots"][0]["start"] == datetime(2027, 6, 9, 12, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize("kw, msg", [
        ({"polyline": [[47.9375, 0.2243], [47.93751, 0.22431]]}, "trop courte"),
        ({"polyline": POLY + [POLY[0]]}, "boucle"),
        ({"polyline": [[47.0, 0.2], [47.3, 0.2]]}, "trop longue"),
        ({"description": "x" * 41}, "40"),
        ({"street": ""}, "rue"),
        ({"slots": []}, "plage"),
        ({"slots": [{"start": "2027-06-09T20:00", "end": "2027-06-09T14:00"}]}, "avant"),
        ({"rule_keys": ["node/5"]}, "troncon"),
    ])
    def test_refus(self, kw, msg):
        out, err = W.clean_closure(_closure(**kw))
        assert out is None and msg in err

    def test_regle_waze(self):
        assert W.rule_closes_for_waze({"access": "fermer"})
        assert W.rule_closes_for_waze({"access": "interdire", "interdire_pour": ["vl", "pietons"]})
        assert not W.rule_closes_for_waze({"access": "interdire", "interdire_pour": ["pietons"]})
        assert W.rule_closes_for_waze({"piste": True, "oneway": "yes"})
        assert not W.rule_closes_for_waze({"oneway": "yes"})
        assert not W.rule_closes_for_waze(None)


class TestHeuresEtFlux:
    def test_decalage_ete_hiver(self):
        assert W.fmt_local(W.parse_local("2027-06-09T14:00")) == "2027-06-09T14:00:00+02:00"
        assert W.fmt_local(W.parse_local("2027-01-15T14:00")) == "2027-01-15T14:00:00+01:00"
        # Plage a cheval sur le passage a l'heure d'hiver
        assert W.fmt_local(W.parse_local("2027-10-31T04:00")) == "2027-10-31T04:00:00+01:00"

    def test_flux_cifs(self):
        fields, _ = W.clean_closure(_closure(slots=[
            {"start": "2027-06-09T14:00", "end": "2027-06-09T20:00"},
            {"start": "2027-06-10T08:00", "end": "2027-06-10T12:00"}]))
        c = dict(fields, uid="aco-wz-abcd1234", publish=True, sync_ok=True)
        now = datetime(2027, 6, 1, tzinfo=timezone.utc)
        root = ET.fromstring(W.build_feed_xml([c], now=now))
        incs = root.findall("incident")
        assert [i.get("id") for i in incs] == ["aco-wz-abcd1234-s1", "aco-wz-abcd1234-s2"]
        i = incs[0]
        assert i.find("type").text == "ROAD_CLOSED"
        assert i.find("subtype").text == "ROAD_CLOSED_EVENT"
        assert i.find("polyline").text.split()[:2] == ["47.937500", "0.224300"]   # lat lon
        assert i.find("starttime").text == "2027-06-09T14:00:00+02:00"
        assert i.find("endtime").text == "2027-06-09T20:00:00+02:00"
        assert i.find("parent_event").get("id") == "aco-24h-2027"

    def test_exclusions(self):
        fields, _ = W.clean_closure(_closure())
        now = datetime(2027, 6, 1, tzinfo=timezone.utc)
        brouillon = dict(fields, uid="aco-wz-1", publish=False)
        desync = dict(fields, uid="aco-wz-2", publish=True, sync_ok=False)
        vieille = dict(fields, uid="aco-wz-3", publish=True)
        xml = W.build_feed_xml([brouillon, desync], now=now)
        assert "<incident " not in xml
        # Terminee depuis plus de 24 h : retiree
        assert "<incident " not in W.build_feed_xml([vieille], now=datetime(2027, 6, 11, tzinfo=timezone.utc))
        assert "<incident " in W.build_feed_xml([vieille], now=datetime(2027, 6, 10, tzinfo=timezone.utc))
        # Interrupteur coupe : flux vide volontaire
        assert "<incident " not in W.build_feed_xml([vieille], enabled=False, now=now)

    def test_echappement(self):
        fields, _ = W.clean_closure(_closure(street="Rue <A & B>"))
        xml = W.build_feed_xml([dict(fields, uid="aco-wz-x", publish=True)],
                               now=datetime(2027, 6, 1, tzinfo=timezone.utc))
        assert ET.fromstring(xml).find("incident/street").text == "Rue <A & B>"


class TestCycleDeVie:
    def test_debut_verrouille_une_fois_commence(self, monkeypatch):
        fields, _ = W.clean_closure(_closure())
        monkeypatch.setattr(W, "_now", lambda: datetime(2027, 6, 9, 13, 0, tzinfo=timezone.utc))
        _, err = W.clean_closure({"slots": [{"sid": "s1", "start": "2027-06-09T15:00", "end": "2027-06-09T21:00"}]},
                                 existing=fields)
        assert "deja commencee" in err
        out, err = W.clean_closure({"slots": [{"sid": "s1", "start": "2027-06-09T14:00", "end": "2027-06-09T22:00"}]},
                                   existing=fields)
        assert err is None and out["slots"][0]["end"].hour == 20      # 22:00 Paris

    def test_plage_en_cours_non_supprimable(self, monkeypatch):
        fields, _ = W.clean_closure(_closure())
        monkeypatch.setattr(W, "_now", lambda: datetime(2027, 6, 9, 13, 0, tzinfo=timezone.utc))
        _, err = W.clean_closure({"slots": [{"start": "2027-06-12T08:00", "end": "2027-06-12T09:00"}]},
                                 existing=fields)
        assert "Rouvrir maintenant" in err

    def test_statuts(self):
        fields, _ = W.clean_closure(_closure())
        c = dict(fields, publish=True)
        assert W.closure_status(c, datetime(2027, 6, 1, tzinfo=timezone.utc)) == "programmee"
        assert W.closure_status(c, datetime(2027, 6, 9, 13, tzinfo=timezone.utc)) == "en_cours"
        assert W.closure_status(c, datetime(2027, 6, 10, tzinfo=timezone.utc)) == "terminee"
        c["slots"][0]["ended_early_at"] = datetime(2027, 6, 9, 13, tzinfo=timezone.utc)
        assert W.closure_status(c, datetime(2027, 6, 9, 14, tzinfo=timezone.utc)) == "terminee"
        assert W.closure_status(dict(c, sync_ok=False)) == "desynchronisee"
        assert W.closure_status(dict(c, publish=False)) == "brouillon"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    fake_app = types.ModuleType("app")
    fake_app.CODING = True
    for k, v in (("JWT_SECRET", "s"), ("JWT_ALGORITHM", "HS256"), ("APP_KEY", "cockpit"),
                 ("SUPER_ADMIN_ROLE", "super_admin"), ("ROLE_HIERARCHY", {})):
        setattr(fake_app, k, v)
    monkeypatch.setitem(sys.modules, "app", fake_app)
    db = FakeDb(waze_feed_config=[{"_id": "global", "enabled": True}])
    monkeypatch.setattr(W, "_get_mongo_db", lambda: db)
    monkeypatch.setattr(W, "WAZE_FEED_USER", "waze")
    monkeypatch.setattr(W, "WAZE_FEED_PASSWORD", "secret-tres-long")
    monkeypatch.setattr(W, "_now", lambda: datetime(2027, 6, 1, tzinfo=timezone.utc))
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(W.waze_feed_bp)
    return types.SimpleNamespace(client=app.test_client(), db=db)


def _auth(user="waze", pwd="secret-tres-long"):
    return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (user, pwd)).encode()).decode()}


class TestHttp:
    def test_creation_publication_flux(self, env):
        rep = env.client.post("/api/admin/itineraire/waze/closures", json=dict(_closure(), publish=False))
        assert rep.status_code == 201
        uid = rep.get_json()["uid"]
        assert rep.get_json()["status"] == "brouillon"
        assert "<incident " not in env.client.get(W.FEED_PATH, headers=_auth()).get_data(as_text=True)
        rep = env.client.patch("/api/admin/itineraire/waze/closures/" + uid, json={"action": "publish"})
        assert rep.get_json()["status"] == "programmee"
        feed = env.client.get(W.FEED_PATH, headers=_auth())
        assert feed.status_code == 200
        assert feed.headers["Cache-Control"] == "no-store"
        assert ('id="%s-s1"' % uid) in feed.get_data(as_text=True)

    def test_authentification(self, env, monkeypatch):
        assert env.client.get(W.FEED_PATH).status_code == 401
        assert env.client.get(W.FEED_PATH, headers=_auth(pwd="faux")).status_code == 401
        monkeypatch.setattr(W, "WAZE_FEED_PASSWORD", "")
        assert env.client.get(W.FEED_PATH, headers=_auth()).status_code == 503

    def test_panne_mongo_jamais_flux_vide(self, env, monkeypatch):
        def boom():
            raise RuntimeError("mongo")
        monkeypatch.setattr(W, "_get_mongo_db", boom)
        assert env.client.get(W.FEED_PATH, headers=_auth()).status_code == 503

    def test_desynchronisation(self, env):
        rep = env.client.post("/api/admin/itineraire/waze/closures", json=dict(_closure(), publish=True))
        uid = rep.get_json()["uid"]
        W.on_doc_written("events", "24h-2027", {"rules": {"way/1": {"access": "fermer"}, "way/2": {"oneway": "yes"}}})
        c = env.db[W.COL].find_one({"uid": uid})
        assert c["sync_ok"] is False
        assert "<incident " not in env.client.get(W.FEED_PATH, headers=_auth()).get_data(as_text=True)
        W.on_doc_written("events", "24h-2027", {"rules": {"way/1": {"access": "fermer"}, "way/2": {"piste": True}}})
        assert env.db[W.COL].find_one({"uid": uid})["sync_ok"] is True

    def test_suppression_protegee(self, env):
        rep = env.client.post("/api/admin/itineraire/waze/closures", json=dict(_closure(), publish=True))
        uid = rep.get_json()["uid"]
        assert env.client.delete("/api/admin/itineraire/waze/closures/" + uid).status_code == 409
        env.client.patch("/api/admin/itineraire/waze/closures/" + uid, json={"action": "unpublish"})
        assert env.client.delete("/api/admin/itineraire/waze/closures/" + uid).status_code == 204
