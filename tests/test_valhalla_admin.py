"""Moteur d'itineraire : modes de calcul, relais valhalla-admin, acces piste
des tablettes Field."""
import sys
import types

import pytest
from flask import Flask

import field
import routing
import valhalla_admin as VA
from conftest import FakeDb


# ---------------------------------------------------------------------------
# Modes et recettes
# ---------------------------------------------------------------------------

class TestModes:
    def test_resolution_depuis_booleens(self):
        assert VA.resolve_mode({}) == "normal"
        assert VA.resolve_mode({"piste": True}) == "normal_piste"
        assert VA.resolve_mode({"god": True}) == "god"
        assert VA.resolve_mode({"god": True, "piste": True}) == "god_piste"

    def test_mode_explicite_prioritaire_et_tolerant(self):
        assert VA.resolve_mode({"mode": "god + piste"}) == "god_piste"
        assert VA.resolve_mode({"mode": "Normal-Piste", "god": True}) == "normal_piste"
        # Mode inconnu : retour aux booleens, jamais une recette inventee
        assert VA.resolve_mode({"mode": "ambulance", "god": True}) == "god"

    def test_recettes_du_cahier_sans_ignore_access(self, monkeypatch):
        monkeypatch.setattr(VA, "VALHALLA_ADMIN_API_KEY", "")
        monkeypatch.setattr(VA, "_profiles", {"data": None, "ts": 0.0, "ok": False})
        assert VA.route_recipe("normal") == {"costing": "auto"}
        assert VA.route_recipe("normal_piste") == {"costing": "taxi"}
        god = VA.route_recipe("god")
        assert god["costing"] == "bus"
        assert god["costing_options"]["bus"] == {"ignore_oneways": True, "ignore_restrictions": True}
        assert VA.route_recipe("god_piste")["costing"] == "motorcycle"
        for m in VA.ROUTE_MODES:
            assert "ignore_access" not in str(VA.route_recipe(m))

    def test_recette_est_une_copie(self, monkeypatch):
        monkeypatch.setattr(VA, "VALHALLA_ADMIN_API_KEY", "")
        r = VA.route_recipe("god")
        r["costing_options"]["bus"]["ignore_oneways"] = False
        assert VA.route_recipe("god")["costing_options"]["bus"]["ignore_oneways"] is True

    def test_lecture_des_profils_du_service(self):
        parsed = VA.parse_profiles({"profiles": {
            "normal": {"costing": "auto"},
            "god+piste": {"label": "x", "recipe": {"costing": "motorcycle", "costing_options": {"motorcycle": {"a": 1}}}},
            "inconnu": {"costing": "truck"},
            "god": {"label": "sans recette"},
        }})
        assert parsed == {"normal": {"costing": "auto"},
                          "god_piste": {"costing": "motorcycle", "costing_options": {"motorcycle": {"a": 1}}}}
        assert VA.parse_profiles([{"id": "normal_piste", "costing": "taxi"}]) == {"normal_piste": {"costing": "taxi"}}


# ---------------------------------------------------------------------------
# Relais HTTP
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.content = b"" if body is None else b"x"
        self.text = ""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise VA.requests.HTTPError("HTTP %d" % self.status_code)


@pytest.fixture
def relay(monkeypatch):
    fake_app = types.ModuleType("app")
    fake_app.CODING = True      # admin_required : utilisateur simule
    fake_app.JWT_SECRET = "s"
    fake_app.JWT_ALGORITHM = "HS256"
    fake_app.APP_KEY = "cockpit"
    fake_app.SUPER_ADMIN_ROLE = "super_admin"
    fake_app.ROLE_HIERARCHY = {}
    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setattr(VA, "VALHALLA_ADMIN_API_KEY", "cle-test")
    monkeypatch.setattr(VA, "VALHALLA_ADMIN_URL", "http://valhalla:8003")
    calls = []
    replies = []

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        calls.append({"method": method, "url": url, "params": params, "json": json, "headers": headers})
        return replies.pop(0) if replies else _Resp(200, {})

    monkeypatch.setattr(VA.requests, "request", fake_request)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(VA.valhalla_admin_bp)
    return types.SimpleNamespace(client=app.test_client(), calls=calls, replies=replies)


class TestRelais:
    def test_ecriture_signee_et_verrouillee(self, relay):
        relay.replies.append(_Resp(200, {"id": "saison-2026", "version": 8}, {"ETag": "8"}))
        rep = relay.client.put("/api/admin/itineraire/seasons/saison-2026/rules/node/251820935",
                               json={"access": "fermer"}, headers={"If-Match": "7"})
        assert rep.status_code == 200
        assert rep.headers["ETag"] == "8"
        c = relay.calls[0]
        assert c["method"] == "PUT"
        assert c["url"] == "http://valhalla:8003/seasons/saison-2026/rules/node/251820935"
        assert c["headers"]["X-Api-Key"] == "cle-test"
        assert c["headers"]["If-Match"] == "7"
        assert c["headers"]["X-Author"] == "dev@cockpit"
        assert c["json"] == {"access": "fermer"}

    def test_conflit_rendu_tel_quel(self, relay):
        relay.replies.append(_Resp(409, {"detail": {"message": "version obsolete", "current_version": 9}}))
        rep = relay.client.patch("/api/admin/itineraire/events/24h-moto-2026", json={"name": "x"},
                                 headers={"If-Match": "3"})
        assert rep.status_code == 409
        assert rep.get_json()["detail"]["current_version"] == 9

    def test_suppression_204(self, relay):
        relay.replies.append(_Resp(204))
        rep = relay.client.delete("/api/admin/itineraire/events/vieux", headers={"If-Match": "2"})
        assert rep.status_code == 204

    def test_couche_et_identifiants_filtres(self, relay):
        assert relay.client.get("/api/admin/itineraire/users/x").status_code == 404
        assert relay.client.get("/api/admin/itineraire/seasons/..%2Fstatus").status_code == 404
        assert relay.client.put("/api/admin/itineraire/seasons/s/rules/relation/12", json={}).status_code == 404
        assert relay.calls == []

    def test_voies_sans_bbox_refusees_sauf_regles(self, relay):
        assert relay.client.get("/api/admin/itineraire/features?kind=highways").status_code == 422
        assert relay.calls == []
        relay.client.get("/api/admin/itineraire/features?kind=highways&only_rules=true&season=s&event=none&evil=1")
        assert relay.calls[0]["params"] == {"kind": "highways", "only_rules": "true", "season": "s", "event": "none"}

    def test_chargement(self, relay):
        relay.replies.append(_Resp(202, {"last_load": {"state": "running"}}))
        rep = relay.client.post("/api/admin/itineraire/load", json={"season_id": "saison-2026", "event_id": ""})
        assert rep.status_code == 202
        assert relay.calls[0]["json"] == {"season_id": "saison-2026", "event_id": None}
        assert relay.client.post("/api/admin/itineraire/load", json={}).status_code == 422

    def test_noeud_par_coordonnees_et_non_par_rang(self, relay, monkeypatch):
        # OSM en ligne a gagne un noeud (99) depuis le PBF du moteur : le rang
        # ne correspond plus, les coordonnees si
        osm = {"elements": [
            {"type": "node", "id": 11, "lon": 0.21, "lat": 47.94},
            {"type": "node", "id": 99, "lon": 0.2105, "lat": 47.9405},
            {"type": "node", "id": 12, "lon": 0.211, "lat": 47.941},
            {"type": "way", "id": 5, "nodes": [11, 99, 12]},
        ]}
        monkeypatch.setattr(VA, "_way_nodes", {})
        monkeypatch.setattr(VA.requests, "get", lambda *a, **k: _Resp(200, osm))
        rep = relay.client.get("/api/admin/itineraire/node-at?way=5&lon=0.211&lat=47.941")
        assert rep.get_json()["node"] == "node/12"
        # Voie retouchee dans OSM depuis le PBF du moteur : point deplace de ~2 m
        rep = relay.client.get("/api/admin/itineraire/node-at?way=5&lon=0.211&lat=47.94102")
        assert rep.get_json()["node"] == "node/12"
        rep = relay.client.get("/api/admin/itineraire/node-at?way=5&lon=0.3&lat=47.9")
        assert rep.status_code == 404

    def test_cle_absente(self, relay, monkeypatch):
        monkeypatch.setattr(VA, "VALHALLA_ADMIN_API_KEY", "")
        rep = relay.client.get("/api/admin/itineraire/status")
        assert rep.status_code == 503
        assert relay.calls == []


# ---------------------------------------------------------------------------
# Calcul d'itineraire : piste des tablettes, aucun chemin
# ---------------------------------------------------------------------------

@pytest.fixture
def tablets(monkeypatch):
    db = FakeDb(
        anoloc_config=[{"_id": "global", "beacon_groups": [
            {"id": "grp-piste", "route_piste": True},
            {"id": "grp-ville"},
        ]}],
        field_devices=[
            {"_id": "d1", "name": "Piste 1", "event": "E", "year": "2026",
             "beacon_group_id": "grp-piste", "token_hash": field._hash_token("tok-piste")},
            {"_id": "d2", "name": "Ville 1", "event": "E", "year": "2026",
             "beacon_group_id": "grp-ville", "token_hash": field._hash_token("tok-ville")},
        ])
    monkeypatch.setattr(field, "_get_mongo_db", lambda: db)
    monkeypatch.setattr(routing, "_get_mongo_db", lambda: db)
    monkeypatch.setattr(field, "_sweep_event_if_ended", lambda *a: False)
    monkeypatch.setattr(routing, "_get_recent_waze_alerts", lambda *a: [])
    monkeypatch.setattr(VA, "VALHALLA_ADMIN_API_KEY", "")
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append(json)
        return _Resp(200, {"trip": {"summary": {"length": 1.5, "time": 120, "has_toll": True},
                                    "legs": [{"shape": "_p~iF~ps|U_ulLnnqC"}]}})

    monkeypatch.setattr(routing.requests, "post", fake_post)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(routing.routing_bp)
    return types.SimpleNamespace(client=app.test_client(), sent=sent)


def _route(env, token, body):
    env.client.set_cookie(field.FIELD_COOKIE_NAME, token, path=field.FIELD_COOKIE_PATH)
    return env.client.post("/field/api/route", json=dict({"from": [47.95, 0.21], "to": [47.94, 0.22]}, **body),
                           headers={"Accept": "application/json"})


class TestPisteTablette:
    def test_groupe_sans_piste_refuse(self, tablets):
        rep = _route(tablets, "tok-ville", {"god": True, "piste": True})
        assert rep.status_code == 403
        assert rep.get_json()["error"] == "piste_not_allowed"
        assert tablets.sent == []

    def test_groupe_avec_piste(self, tablets):
        rep = _route(tablets, "tok-piste", {"god": True, "piste": True})
        assert rep.status_code == 200
        j = rep.get_json()
        assert j["mode"] == "god_piste" and j["god"] is True and j["piste"] is True
        assert j["uses_raceway"] is True
        assert tablets.sent[0]["costing"] == "motorcycle"

    def test_mode_normal_inchange(self, tablets):
        rep = _route(tablets, "tok-ville", {})
        assert rep.status_code == 200
        assert tablets.sent[0]["costing"] == "auto"
        assert "costing_options" not in tablets.sent[0]

    def test_drapeau_lu_sur_le_groupe(self, tablets):
        db = field._get_mongo_db()
        assert field._device_route_piste(db, {"beacon_group_id": "grp-piste"}) is True
        assert field._device_route_piste(db, {"beacon_group_id": "grp-ville"}) is False
        assert field._device_route_piste(db, {}) is False


class TestAucunChemin:
    def test_400_valhalla_pas_de_trait_droit(self, tablets, monkeypatch):
        monkeypatch.setattr(routing.requests, "post",
                            lambda url, json=None, timeout=None: _Resp(400, {"error_code": 442}))
        rep = _route(tablets, "tok-ville", {})
        assert rep.status_code == 502
        assert rep.get_json()["error"] == "no_route"

    def test_injoignable_garde_le_repli(self, tablets, monkeypatch):
        def boom(*a, **k):
            raise routing.requests.exceptions.ConnectionError("down")
        monkeypatch.setattr(routing.requests, "post", boom)
        rep = _route(tablets, "tok-ville", {"god": True})
        assert rep.status_code == 200
        assert rep.get_json()["engine"] == "stub"
        assert rep.get_json()["mode"] == "god"
