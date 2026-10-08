"""Constats terrain (declarations.py) contre un MongoDB local jetable.

Tablette declarante : depot (idempotent), liste, complement ; tablette
normale refusee. Cockpit : droits, suivi, classement, rattachement d'une
fiche creee par l'operateur. Test saute sans MongoDB local.
"""
import io
import os
import sys
import types
from datetime import datetime, timezone

import pytest
from flask import Flask, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import field
import declarations as D


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 50, 50)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def env(monkeypatch, tmp_path):
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_decl_%d" % os.getpid()
    client.drop_database(name)
    db = client[name]
    db["anoloc_config"].insert_one({"_id": "global", "beacon_groups": [
        {"id": "grp-cap", "label": "Cellule appui", "pco_category": "PCO.Technique", "declarant": True},
        {"id": "grp-tech", "label": "Tech", "pco_category": "PCO.Technique"},
    ]})
    now = datetime.now(timezone.utc)
    base = {"event": "SAISON", "year": "2026", "revoked": False, "last_seen": now}
    db["field_devices"].insert_many([
        dict(base, _id="dev-cap", name="Presta 1", beacon_group_id="grp-cap",
             token_hash=field._hash_token("tok-cap")),
        dict(base, _id="dev-tech", name="Tech 1", beacon_group_id="grp-tech",
             token_hash=field._hash_token("tok-tech")),
    ])
    monkeypatch.setattr(field, "_get_mongo_db", lambda: db)
    monkeypatch.setattr(field, "_sweep_event_if_ended", lambda *a: False)
    monkeypatch.setattr(field, "fiche_target_event", lambda db, dev, now=None: ("SAISON", 2026))
    monkeypatch.setattr(field, "FIELD_PHOTOS_DIR", str(tmp_path))
    D._indexes_done = False

    # Faux module `app` : role_required pose l'utilisateur courant, droits
    # pilotes par le test.
    who = {"payload": {"email": "op@aco.fr", "firstname": "Op", "lastname": "Pc"}, "treat": True}
    fake = types.ModuleType("app")

    def role_required(role):
        def deco(f):
            def w(*a, **k):
                request.user_payload = who["payload"]
                return f(*a, **k)
            return w
        return deco
    fake.role_required = role_required
    fake._user_can_treat_declaration = lambda p: who["treat"]
    fake._user_can_create_fiche = lambda p: True
    monkeypatch.setitem(sys.modules, "app", fake)

    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(D.__file__), "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(field.field_bp)
    app.register_blueprint(D.declarations_bp)
    c = app.test_client()
    yield types.SimpleNamespace(client=c, db=db, who=who)
    client.drop_database(name)


def _as(c, tok):
    c.set_cookie(field.FIELD_COOKIE_NAME, tok, path=field.FIELD_COOKIE_PATH)


def _depose(env, **extra):
    _as(env.client, "tok-cap")
    data = dict({"text": "Barriere arrachee porte nord", "priority": "haute",
                 "lat": "47.95", "lng": "0.21", "carroye": "AH14",
                 "client_token": "tok-12345678"}, **extra)
    data["photos"] = (io.BytesIO(_png()), "p.jpg")
    return env.client.post("/field/declarations", data=data, content_type="multipart/form-data")


class TestTablette:
    def test_depot_avec_photo(self, env):
        rep = _depose(env)
        assert rep.status_code == 200, rep.get_json()
        body = rep.get_json()
        assert body["ref"] == "C-2026-0001"
        d = env.db["declarations"].find_one({"_id": body["id"]})
        assert d["status"] == "nouvelle" and d["priority"] == "haute"
        assert d["group_label"] == "Cellule appui"
        assert d["photos"][0]["photo"].startswith("/field/photos/_declarant/SAISON/2026/")
        # Aucune fiche creee
        assert env.db["pcorg"].count_documents({}) == 0

    def test_depot_idempotent(self, env):
        a = _depose(env).get_json()
        b = _depose(env).get_json()
        assert b["duplicate"] is True and b["id"] == a["id"]
        assert env.db["declarations"].count_documents({}) == 1

    def test_evenement_de_la_tablette_et_filtre(self, env):
        # Tablette appairee sur une epreuve : le constat y est rattache
        env.db["field_devices"].update_one({"_id": "dev-cap"}, {"$set": {"event": "24H MOTOS", "year": "2027"}})
        did = _depose(env, client_token="tok-ev-123456").get_json()["id"]
        d = env.db["declarations"].find_one({"_id": did})
        assert (d["event"], d["year"]) == ("24H MOTOS", 2027)
        # Tablette SAISON d'une annee passee -> SAISON courant
        env.db["field_devices"].update_one({"_id": "dev-cap"}, {"$set": {"event": "SAISON", "year": "2020"}})
        did2 = _depose(env, client_token="tok-ev-654321").get_json()["id"]
        d2 = env.db["declarations"].find_one({"_id": did2})
        assert d2["event"] == "SAISON" and d2["year"] == datetime.now(timezone.utc).year
        lst = env.client.get("/api/declarations?event=24H%20MOTOS&year=2027").get_json()
        assert [x["id"] for x in lst["declarations"]] == [did]
        assert lst["counts"]["nouvelle"] == 1
        assert {(e["event"], e["year"]) for e in lst["events"]} == {("24H MOTOS", 2027), ("SAISON", d2["year"])}
        html = env.client.get("/api/declarations/%s/report/preview" % did).get_data(as_text=True)
        assert "24H MOTOS 2027" in html

    def test_tablette_normale_refusee(self, env):
        _as(env.client, "tok-tech")
        rep = env.client.post("/field/declarations", json={"text": "x"})
        assert rep.status_code == 403
        assert rep.get_json()["error"] == "not_declarant"

    def test_liste_et_complement(self, env):
        did = _depose(env).get_json()["id"]
        lst = env.client.get("/field/declarations").get_json()["declarations"]
        assert [x["id"] for x in lst] == [did]
        assert lst[0]["field_state"] == "envoyee"
        rep = env.client.post("/field/declarations/%s/comment" % did, json={"comment": "Toujours au sol"})
        assert rep.status_code == 200
        det = env.client.get("/field/declarations/%s" % did).get_json()
        assert [h["kind"] for h in det["history"]] == ["declaration", "complement"]


class TestCockpit:
    def test_droit_requis(self, env):
        did = _depose(env).get_json()["id"]
        env.who["treat"] = False
        rep = env.client.post("/api/declarations/%s/status" % did, json={"status": "en_suivi"})
        assert rep.status_code == 403

    def test_suivi_puis_classement(self, env):
        did = _depose(env).get_json()["id"]
        assert env.client.post("/api/declarations/%s/status" % did, json={"status": "en_suivi"}).status_code == 200
        rep = env.client.post("/api/declarations/%s/status" % did, json={"status": "classee"})
        assert rep.get_json()["error"] == "motif_requis"
        rep = env.client.post("/api/declarations/%s/status" % did,
                              json={"status": "classee", "reason": "Doublon du C-2026-0000"})
        assert rep.status_code == 200
        lst = env.client.get("/api/declarations?status=classee").get_json()
        assert lst["counts"]["classee"] == 1
        _as(env.client, "tok-cap")
        assert env.client.get("/field/declarations").get_json()["declarations"][0]["field_state"] == "classee"

    def test_rattachement_fiche(self, env):
        did = _depose(env).get_json()["id"]
        det = env.client.get("/api/declarations/%s" % did).get_json()
        assert det["prefill"]["urgency"] == "UA" and det["prefill"]["lat"] == 47.95
        # Source pre-remplie : externe / groupe declarant / canal application
        assert (det["prefill"]["source"], det["prefill"]["appelant"], det["prefill"]["canal"]) == \
            ("externe", "Cellule appui", "application")
        # Choix d'evenement : SAISON toujours propose, defaut = evenement du constat
        assert any(c["event"] == "SAISON" for c in det["event_choices"])
        assert det["event_default"] == {"event": "SAISON", "year": 2026}
        # fiche creee par un autre operateur : refusee
        env.db["pcorg"].insert_one({"_id": "f-autre", "operator_id_create": "autre@aco.fr",
                                    "comment_history": [], "comment": "", "status_code": 0})
        rep = env.client.post("/api/declarations/%s/link" % did, json={"fiche_id": "f-autre"})
        assert rep.status_code == 403
        env.db["pcorg"].insert_one({"_id": "f1", "operator_id_create": "op@aco.fr", "category": "PCO.Technique",
                                    "comment_history": [], "comment": "", "status_code": 0})
        rep = env.client.post("/api/declarations/%s/link" % did, json={"fiche_id": "f1"})
        assert rep.status_code == 200, rep.get_json()
        d = env.db["declarations"].find_one({"_id": did})
        assert d["status"] == "transformee" and d["fiche_id"] == "f1"
        f = env.db["pcorg"].find_one({"_id": "f1"})
        assert f["declaration_ref"] == "C-2026-0001"
        assert f["comment_history"][-1]["photos"]
        # Etat cote tablette : pris en charge, puis traite a la cloture
        _as(env.client, "tok-cap")
        assert env.client.get("/field/declarations").get_json()["declarations"][0]["field_state"] == "prise_en_charge"
        env.db["pcorg"].update_one({"_id": "f1"}, {"$set": {"status_code": 10}})
        assert env.client.get("/field/declarations").get_json()["declarations"][0]["field_state"] == "traitee"
        # Deja transforme : un second rattachement est refuse
        rep = env.client.post("/api/declarations/%s/link" % did, json={"fiche_id": "f1"})
        assert rep.status_code == 409

    def test_discussion_avec_le_declarant(self, env, monkeypatch):
        pushes = []
        monkeypatch.setattr(field, "send_push_to_device",
                            lambda db, dev_id, **k: pushes.append((dev_id, k)) or 1)
        did = _depose(env).get_json()["id"]
        # Droit requis
        env.who["treat"] = False
        assert env.client.post("/api/declarations/%s/message" % did, json={"text": "?"}).status_code == 403
        env.who["treat"] = True
        rep = env.client.post("/api/declarations/%s/message" % did,
                              json={"text": "La barriere est-elle encore au sol ?"})
        assert rep.status_code == 200 and rep.get_json()["pushed"] == 1
        # Ecrire au declarant vaut accuse de reception
        assert env.db["declarations"].find_one({"_id": did})["status"] == "en_suivi"
        assert pushes[0][0] == "dev-cap"
        assert pushes[0][1]["url"] == "/field?constat=" + did
        # Cote tablette : non lu, visible dans le detail (pas les notes internes)
        env.client.post("/api/declarations/%s/note" % did, json={"text": "note PC"})
        _as(env.client, "tok-cap")
        lst = env.client.get("/field/declarations").get_json()["declarations"]
        assert lst[0]["field_unread"] == 1
        det = env.client.get("/field/declarations/%s" % did).get_json()
        kinds = [h["kind"] for h in det["history"]]
        assert "message" in kinds and "note" not in kinds
        assert env.client.get("/field/declarations").get_json()["declarations"][0]["field_unread"] == 0
        # Reponse du declarant -> signalee au PC, lue a l'ouverture
        env.client.post("/field/declarations/%s/comment" % did, json={"comment": "Oui, toujours"})
        lst = env.client.get("/api/declarations?status=a_traiter").get_json()
        assert lst["counts"]["reponses"] == 1 and lst["declarations"][0]["cockpit_unread"] == 1
        env.client.get("/api/declarations/%s" % did)
        assert env.client.get("/api/declarations").get_json()["counts"]["reponses"] == 0
        # Constat classe : plus de message
        env.client.post("/api/declarations/%s/status" % did, json={"status": "classee", "reason": "regle"})
        rep = env.client.post("/api/declarations/%s/message" % did, json={"text": "x"})
        assert rep.status_code == 409 and rep.get_json()["error"] == "classee"

    def test_apercu_constat_et_envoi_a_finaliser(self, env):
        did = _depose(env).get_json()["id"]
        rep = env.client.get("/api/declarations/%s/report/preview" % did)
        assert rep.status_code == 200
        html = rep.get_data(as_text=True)
        assert "C-2026-0001" in html and "Barriere arrachee" in html
        assert env.client.post("/api/declarations/%s/report/send" % did).status_code == 501
