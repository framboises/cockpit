"""Controles d'acces de l'app tablette : detail de fiche et photos terrain."""
import re
import sys
import types

import jwt as pyjwt
import pytest
from flask import Flask

import field

SECRET = "test-secret"
env_fake_app = {}
JSON = {"Accept": "application/json"}


def _get(doc, path):
    for part in path.split("."):
        if not isinstance(doc, dict):
            return None
        doc = doc.get(part)
    return doc


def _match(doc, flt):
    for key, cond in flt.items():
        if key == "$or":
            if not any(_match(doc, sub) for sub in cond):
                return False
            continue
        val = _get(doc, key)
        if isinstance(cond, dict):
            for op, arg in cond.items():
                if op == "$ne" and val == arg:
                    return False
                if op == "$regex" and not re.search(arg, str(val or "")):
                    return False
        elif val != cond:
            return False
    return True


class _Cursor(list):
    def sort(self, *a, **k):
        return self

    def limit(self, n):
        return _Cursor(self[:n])


class _Col:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find_one(self, flt, projection=None):
        for d in self.docs:
            if _match(d, flt):
                return d
        return None

    def find(self, flt=None, projection=None):
        return _Cursor(d for d in self.docs if _match(d, flt or {}))

    def update_one(self, flt, update, **k):
        for d in self.docs:
            if _match(d, flt):
                d.update(update.get("$set", {}))
                return types.SimpleNamespace(matched_count=1)
        return types.SimpleNamespace(matched_count=0)


class _Db(dict):
    def __missing__(self, key):
        self[key] = _Col()
        return self[key]


@pytest.fixture
def env(monkeypatch, tmp_path):
    fake_app = types.ModuleType("app")
    fake_app.JWT_SECRET = SECRET
    fake_app.JWT_ALGORITHM = "HS256"
    fake_app.CODING = False
    fake_app.APP_KEY = "cockpit"
    fake_app.SUPER_ADMIN_ROLE = "super_admin"
    fake_app.ROLE_HIERARCHY = {}
    monkeypatch.setitem(sys.modules, "app", fake_app)
    env_fake_app["mod"] = fake_app

    db = _Db()
    db["anoloc_config"] = _Col([{"_id": "global", "beacon_groups": [
        {"id": "grp-surete", "pco_category": "PCO.Securite"},
        {"id": "grp-tangos", "pco_category": "PCO.Technique"},
        {"id": "grp-medias"},
    ]}])
    base = {"event": "E", "year": "2026", "revoked": False}
    db["field_devices"] = _Col([
        # categorie explicite
        dict(base, _id="dev1", name="Tech 1", category="PCO.Technique",
             beacon_group_id="grp-medias", token_hash=field._hash_token("tok1")),
        # sans categorie propre : herite de son groupe (Securite)
        dict(base, _id="dev2", name="Tech 2", beacon_group_id="grp-surete",
             token_hash=field._hash_token("tok2")),
        dict(base, _id="dev3", name="Old", category="PCO.Securite", revoked=True,
             token_hash=field._hash_token("tok3")),
        dict(base, _id="dev4", name="Elec", beacon_group_id="grp-tangos",
             token_hash=field._hash_token("tok4")),
        dict(base, _id="dev5", name="Flux 1", category="PCO.Flux",
             token_hash=field._hash_token("tok5")),
        # ni categorie ni groupe categorise : aucune restriction
        dict(base, _id="dev6", name="Libre", beacon_group_id="grp-medias",
             token_hash=field._hash_token("tok6")),
    ])
    fiche = {"event": "E", "year": 2026, "status_code": 1}
    db["pcorg"] = _Col([
        dict(fiche, _id="f1", category="PCO.Technique", text="fuite",
             content_category={"patrouille": "Tech 1"}),
        dict(fiche, _id="f2", category="PCO.Securite", text="rixe",
             content_category={"patrouille": "Tech 1"}),
        dict(fiche, _id="f3", category="PCO.Secours", text="SOS",
             content_category={"patrouille": "Tech 1", "field_sos": True}),
    ])
    monkeypatch.setattr(field, "_get_mongo_db", lambda: db)
    env_fake_app["db"] = db
    monkeypatch.setattr(field, "_sweep_event_if_ended", lambda *a: False)

    photos = tmp_path / "E" / "2026"
    photos.mkdir(parents=True)
    (photos / "p.jpg").write_bytes(b"jpg")
    monkeypatch.setattr(field, "FIELD_PHOTOS_DIR", str(tmp_path))

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(field.field_bp)
    return app.test_client()


def _as_tablet(client, token):
    client.set_cookie(field.FIELD_COOKIE_NAME, token, path=field.FIELD_COOKIE_PATH)


class TestDetailFiche:
    def test_tablette_affectee_lit_sa_fiche(self, env):
        _as_tablet(env, "tok1")
        rep = env.get("/field/my-fiches/f1/detail", headers={"Accept": "application/json"})
        assert rep.status_code == 200
        assert rep.get_json()["id"] == "f1"

    def test_autre_tablette_refusee(self, env):
        _as_tablet(env, "tok2")
        rep = env.get("/field/my-fiches/f1/detail", headers={"Accept": "application/json"})
        assert rep.status_code == 403
        assert rep.get_json()["error"] == "not_assigned"


class TestPhotos:
    URL = "/field/photos/E/2026/p.jpg"

    def test_anonyme_refuse(self, env):
        assert env.get(self.URL).status_code == 401

    def test_tablette_active_autorisee(self, env):
        _as_tablet(env, "tok1")
        rep = env.get(self.URL)
        assert rep.status_code == 200
        assert "private" in rep.headers["Cache-Control"]

    def test_tablette_revoquee_refusee(self, env):
        _as_tablet(env, "tok3")
        assert env.get(self.URL).status_code == 401

    def test_utilisateur_cockpit_autorise(self, env):
        tok = pyjwt.encode({"roles_by_app": {"cockpit": "user"}}, SECRET, algorithm="HS256")
        env.set_cookie("access_token", tok)
        assert env.get(self.URL).status_code == 200

    def test_jwt_sans_droit_cockpit_refuse(self, env):
        tok = pyjwt.encode({"roles_by_app": {"autre_app": "admin"}}, SECRET, algorithm="HS256")
        env.set_cookie("access_token", tok)
        assert env.get(self.URL).status_code == 401

    def test_jwt_falsifie_refuse(self, env):
        tok = pyjwt.encode({"roles_by_app": {"cockpit": "admin"}}, "mauvaise-cle", algorithm="HS256")
        env.set_cookie("access_token", tok)
        assert env.get(self.URL).status_code == 401


class TestCategorieTablette:
    def test_herite_du_groupe_sinon_explicite(self, env):
        db = env_fake_app["db"]
        devs = {d["_id"]: d for d in db["field_devices"].docs}
        assert field._device_category(db, devs["dev1"]) == "PCO.Technique"
        assert field._device_category(db, devs["dev2"]) == "PCO.Securite"
        assert field._device_category(db, devs["dev6"]) is None

    def test_missions_filtrees_sur_la_categorie(self, env):
        _as_tablet(env, "tok1")
        data = env.get("/field/my-fiches", headers=JSON).get_json()
        assert data["device_category"] == "PCO.Technique"
        # f2 (Securite) masquee ; f3 = son propre SOS, toujours visible
        assert sorted(f["id"] for f in data["open"]) == ["f1", "f3"]

    def test_fiche_active_visible_meme_hors_categorie(self, env):
        env_fake_app["db"]["field_devices"].update_one(
            {"_id": "dev1"}, {"$set": {"active_fiche_id": "f2"}})
        _as_tablet(env, "tok1")
        data = env.get("/field/my-fiches", headers=JSON).get_json()
        assert sorted(f["id"] for f in data["open"]) == ["f1", "f2", "f3"]

    def test_creation_refusee_hors_categorie(self, env):
        _as_tablet(env, "tok1")
        rep = env.post("/field/create-fiche", headers=JSON,
                       json={"category": "PCO.Securite", "text": "x"})
        assert rep.status_code == 403
        assert rep.get_json() == {"ok": False, "error": "category_not_allowed",
                                  "allowed": "PCO.Technique"}

    def test_categories_proposees(self, env):
        _as_tablet(env, "tok1")
        cats = env.get("/field/pco-categories", headers=JSON).get_json()["categories"]
        assert [c["id"] for c in cats] == ["PCO.Technique"]
        _as_tablet(env, "tok6")
        cats = env.get("/field/pco-categories", headers=JSON).get_json()["categories"]
        assert [c["id"] for c in cats] == ["PCO.Secours", "PCO.Securite", "PCO.Technique", "PCO.Flux"]

    def test_sos_cible(self, env):
        db = env_fake_app["db"]
        sender = db["field_devices"].find_one({"_id": "dev1"})
        names = sorted(d["name"] for d in field._sos_recipients(db, sender, "E", "2026"))
        # Securite (Tech 2), meme equipe (Elec), sans categorie (Libre) ;
        # ni Flux 1, ni la tablette revoquee, ni l'emetteur
        assert names == ["Elec", "Libre", "Tech 2"]

    def test_admin_change_categorie(self, env):
        env_fake_app["mod"].CODING = True
        rep = env.post("/field/admin/devices/%s/category" % ("0" * 24),
                       json={"category": "PCO.Inconnue"})
        assert rep.status_code == 400
        assert rep.get_json()["error"] == "invalid_category"
