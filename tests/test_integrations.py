"""Connecteur applications externes (integrations.py) contre un MongoDB local
jetable : regles d'envoi, file signee et ordonnee, evenements entrants
(signature, rejeu, perimetre), jamais de cloture, pas d'echo."""
import base64
import io
import json
import os
import time
from datetime import datetime, timezone, timedelta

import pytest
from flask import Flask
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import integrations as I

SECRET = "s" * 40


@pytest.fixture
def env(monkeypatch, tmp_path):
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_integ_%d" % os.getpid()
    client.drop_database(name)
    db = client[name]
    monkeypatch.setattr(I, "_app_db", lambda: db)
    monkeypatch.setenv("COCKPIT_INTEG_FRIDAY_SECRET", SECRET)
    monkeypatch.setenv("COCKPIT_INTEG_ALLOWED_HOSTS", "friday.test")
    # friday.test : adresse privee (Friday est interne), admise car hote autorise
    monkeypatch.setattr(I, "_resolve", lambda host: ["10.20.30.40"])
    import field
    monkeypatch.setattr(field, "FIELD_PHOTOS_DIR", str(tmp_path))
    db["integrations"].insert_one({
        "_id": "friday", "label": "Friday", "enabled": True,
        "outbound_url": "https://friday.test/api/integrations/cockpit/events",
        "rules": [{"category": "PCO.Technique", "sous_classification": "Informatique"},
                  {"category": "PCO.Information", "sous_classification": None}],
        "enabled_at": datetime.now(timezone.utc) - timedelta(days=1),
    })
    I.ensure_indexes(db)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(I.integrations_bp)
    yield db, app.test_client()
    client.drop_database(name)


def _fiche(db, fid, cat="PCO.Technique", sc="Informatique", status=0, history=None):
    db["pcorg"].insert_one({
        "_id": fid, "event": "SAISON", "year": 2026, "category": cat, "text": "Wifi HS tribune",
        "content_category": {"sous_classification": sc}, "status_code": status,
        "ts": datetime.now(timezone.utc), "comment_history": history or [], "comment": "",
        "bounce_rev": 1, "niveau_urgence": "UR",
    })


def _cfg(db):
    return I.get_config(db, "friday")


def _post(client, ev, ev_id="e1", secret=SECRET, ts=None):
    body = json.dumps(ev).encode("utf-8")
    ts = ts or int(time.time())
    return client.post("/api/integrations/friday/events", data=body, headers={
        "Content-Type": "application/json", "X-Event-Id": ev_id,
        "X-Signature-Timestamp": str(ts), "X-Signature": I.sign(secret, ts, body)})


class TestRegles:
    def test_categorie_ou_sous_classification(self, env):
        db, _ = env
        cfg = _cfg(db)
        assert I.fiche_matches(cfg, {"category": "PCO.Technique", "content_category": {"sous_classification": "informatique"}})
        assert not I.fiche_matches(cfg, {"category": "PCO.Technique", "content_category": {"sous_classification": "Electricite"}})
        assert I.fiche_matches(cfg, {"category": "PCO.Information", "content_category": {}})
        assert not I.fiche_matches(cfg, {"category": "PCO.Secours", "content_category": {}})

    def test_nettoyage_des_regles(self):
        rules = I.clean_rules([{"category": "PCO.X"}, {"category": "PCO.X"}, {"category": "pas une cat"},
                               {"category": "PCO.Y", "sous_classification": "  A "}])
        assert rules == [{"category": "PCO.X", "sous_classification": None},
                         {"category": "PCO.Y", "sous_classification": "A"}]


class TestAdresseEnvoi:
    """Anti-SSRF : https, hote autorise, jamais le serveur ni une adresse speciale."""

    def test_regles(self, env, monkeypatch):
        assert I.check_outbound_url("https://friday.test/api/x") == (True, "ok")
        assert I.check_outbound_url("http://friday.test/api/x")[1] == "https_obligatoire"
        assert I.check_outbound_url("https://autre.test/api/x")[1] == "hote_non_autorise"
        assert I.check_outbound_url("https://user:pw@friday.test/")[1] == "url_invalide"
        assert I.check_outbound_url("https://127.0.0.1/")[1] == "hote_non_autorise"
        for bad in (["127.0.0.1"], ["::1"], ["169.254.169.254"], ["10.0.0.1", "127.0.0.2"],
                    ["0.0.0.0"], ["::ffff:127.0.0.1"]):
            monkeypatch.setattr(I, "_resolve", lambda host, b=bad: b)
            assert I.check_outbound_url("https://friday.test/")[1] == "adresse_interdite", bad

    def test_verifie_a_chaque_envoi(self, env, monkeypatch):
        db, _ = env
        _fiche(db, "f1")
        I.scan_integration(db, _cfg(db), "https://cockpit.test")
        import requests
        sent = []
        monkeypatch.setattr(requests, "post", lambda *a, **k: sent.append(1))
        # Le DNS de l'hote autorise pointe desormais sur le serveur lui-meme
        monkeypatch.setattr(I, "_resolve", lambda host: ["127.0.0.1"])
        I.process_outbox(db)
        assert not sent
        assert "adresse_interdite" in db["integration_outbox"].find_one({})["last_error"]

    def test_enregistrement_refuse(self, env, monkeypatch):
        import sys
        import types
        from flask import request as freq
        db, client = env
        fake = types.ModuleType("app")

        def role_required(role):
            def deco(f):
                def w(*a, **k):
                    freq.user_payload = {"app_role": "admin", "email": "admin@aco.fr"}
                    return f(*a, **k)
                return w
            return deco
        fake.role_required = role_required
        monkeypatch.setitem(sys.modules, "app", fake)
        r = client.post("/api/integrations-admin", json={"id": "friday", "outbound_url": "http://friday.test/x"})
        assert r.status_code == 400 and r.get_json()["error"] == "https_obligatoire"
        r = client.post("/api/integrations-admin", json={"id": "friday", "outbound_url": "https://intranet.lan/x"})
        assert r.get_json()["error"] == "hote_non_autorise" and r.get_json()["allowed_hosts"] == ["friday.test"]
        r = client.post("/api/integrations-admin", json={"id": "friday", "outbound_url": "https://friday.test/x"})
        assert r.status_code == 200


class TestSortant:
    def test_creation_puis_commentaire_puis_cloture(self, env):
        db, _ = env
        _fiche(db, "f1", history=[{"ts": datetime.now(timezone.utc), "operator": "Op", "text": "Signale", "origin": "cockpit"}])
        _fiche(db, "f2", sc="Electricite")
        assert I.scan_integration(db, _cfg(db), "https://cockpit.test") == 1
        ev = db["integration_outbox"].find_one({"fiche_id": "f1"})
        assert ev["type"] == "fiche.created"
        assert ev["payload"]["fiche"]["sous_classification"] == "Informatique"
        assert [e["text"] for e in ev["payload"]["new_entries"]] == ["Signale"]
        # rien de neuf : pas d'evenement
        assert I.scan_integration(db, _cfg(db), "https://cockpit.test") == 0
        db["pcorg"].update_one({"_id": "f1"}, {"$push": {"comment_history": {
            "ts": datetime.now(timezone.utc), "operator": "Op", "text": "Relance", "origin": "cockpit"}}})
        I.scan_integration(db, _cfg(db), "https://cockpit.test")
        ev2 = db["integration_outbox"].find_one({"fiche_id": "f1", "type": "fiche.comment"})
        assert [e["text"] for e in ev2["payload"]["new_entries"]] == ["Relance"]
        db["pcorg"].update_one({"_id": "f1"}, {"$set": {"status_code": 10}})
        I.scan_integration(db, _cfg(db), "https://cockpit.test")
        assert db["integration_outbox"].find_one({"fiche_id": "f1", "type": "fiche.closed"})

    def test_sortie_du_perimetre(self, env):
        db, _ = env
        _fiche(db, "f1")
        I.scan_integration(db, _cfg(db), "https://cockpit.test")
        db["pcorg"].update_one({"_id": "f1"}, {"$set": {"content_category.sous_classification": "Sanitaire"}})
        I.scan_integration(db, _cfg(db), "https://cockpit.test")
        assert db["integration_outbox"].find_one({"fiche_id": "f1", "type": "fiche.unassigned"})

    def test_envoi_signe_et_ordonne(self, env, monkeypatch):
        db, _ = env
        _fiche(db, "f1")
        _fiche(db, "f2")
        I.scan_integration(db, _cfg(db), "https://cockpit.test")
        calls = []

        class R:
            def __init__(self, code):
                self.status_code, self.text = code, ""

        answers = [503, 200, 200]

        def post(url, data, headers, timeout, allow_redirects=True):
            assert allow_redirects is False          # jamais de redirection suivie
            calls.append((headers["X-Event-Id"], data, headers))
            return R(answers.pop(0))
        import requests
        monkeypatch.setattr(requests, "post", post)
        # 1er passage : le premier echoue -> le second attend (ordre)
        assert I.process_outbox(db) == 0
        assert len(calls) == 1
        # seul le code HTTP est garde, jamais le contenu de la reponse
        assert db["integration_outbox"].find_one({"_id": calls[0][0]})["last_error"] == "HTTP 503"
        ev_id, body, headers = calls[0]
        ok, _ = I.verify(SECRET, headers["X-Signature-Timestamp"], headers["X-Signature"], body)
        assert ok
        # nouvel essai apres le delai : les deux partent dans l'ordre
        db["integration_outbox"].update_many({}, {"$set": {"next_at": datetime.now(timezone.utc)}})
        assert I.process_outbox(db) == 2
        assert calls[1][0] == ev_id

    def test_abandon_apres_le_delai(self, env, monkeypatch):
        db, _ = env
        _fiche(db, "f1")
        I.scan_integration(db, _cfg(db), "https://cockpit.test")
        db["integration_outbox"].update_many({}, {"$set": {"deadline": datetime.now(timezone.utc) - timedelta(minutes=1)}})
        import requests
        monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
        I.process_outbox(db)
        assert db["integration_outbox"].find_one({})["state"] == "failed"


class TestEntrant:
    def _shared(self, db):
        _fiche(db, "f1")
        I.scan_integration(db, _cfg(db), "https://cockpit.test")

    def test_signature_horodatage_rejeu(self, env):
        db, client = env
        self._shared(db)
        ev = {"type": "ticket.linked", "fiche_id": "f1", "ref": "INC-2026-0042", "status": "open"}
        assert _post(client, ev, secret="x" * 40).status_code == 401
        assert _post(client, ev, ts=int(time.time()) - 3600).status_code == 401
        assert _post(client, ev).status_code == 200
        r = _post(client, ev)          # meme X-Event-Id
        assert r.status_code == 200 and r.get_json()["duplicate"] is True

    def test_fiche_non_partagee_refusee(self, env):
        db, client = env
        _fiche(db, "autre", cat="PCO.Secours", sc=None)
        r = _post(client, {"type": "ticket.linked", "fiche_id": "autre", "ref": "INC-1"})
        assert r.status_code == 404 and r.get_json()["error"] == "fiche_non_partagee"

    def test_suivi_complet_sans_cloture_ni_echo(self, env):
        db, client = env
        self._shared(db)
        assert _post(client, {"type": "ticket.note", "fiche_id": "f1", "text": "x"}, "e0").status_code == 409
        _post(client, {"type": "ticket.linked", "fiche_id": "f1", "ref": "INC-2026-0042",
                       "url": "https://friday.test/inc/42", "status": "open", "agent": "Jean"}, "e1")
        _post(client, {"type": "ticket.updated", "fiche_id": "f1", "status": "in_progress", "agent": "Paul"}, "e2")
        _post(client, {"type": "ticket.note", "fiche_id": "f1", "text": "Borne redemarree", "author": "Paul"}, "e3")
        png = io.BytesIO()
        from PIL import Image
        Image.new("RGB", (30, 20), (0, 0, 200)).save(png, format="PNG")
        r = _post(client, {"type": "ticket.photo", "fiche_id": "f1", "author": "Paul", "photo": {
            "filename": "borne.png", "data_base64": base64.b64encode(png.getvalue()).decode(), "caption": "Apres"}}, "e4")
        assert r.status_code == 200, r.get_json()
        _post(client, {"type": "ticket.resolved", "fiche_id": "f1", "resolution": "Switch remplace",
                       "resolved_by": "Paul"}, "e5")
        f = db["pcorg"].find_one({"_id": "f1"})
        link = f["integrations"]["friday"]
        assert link["ref"] == "INC-2026-0042" and link["agent"] == "Paul"
        assert link["status"] == "resolved" and link["attention"] == "resolved"
        # JAMAIS de cloture depuis l'application
        assert f["status_code"] == 0
        texts = [e["text"] for e in f["comment_history"]]
        assert any("En cours" in t for t in texts) and "Borne redemarree" in texts
        assert any(e.get("photos") for e in f["comment_history"])
        assert all(e["origin"] == "integration:friday" for e in f["comment_history"])
        # Pas d'echo : ces entrees ne repartent pas vers Friday (aucun evenement)
        before = db["integration_outbox"].count_documents({"fiche_id": "f1"})
        I.scan_integration(db, _cfg(db), "https://cockpit.test")
        assert db["integration_outbox"].count_documents({"fiche_id": "f1"}) == before

    def test_photo_signee(self, env, tmp_path):
        db, client = env
        (tmp_path / "SAISON" / "2026").mkdir(parents=True)
        (tmp_path / "SAISON" / "2026" / "a.jpg").write_bytes(b"jpg")
        url = I.signed_photo_url(_cfg(db), "https://cockpit.test", "/field/photos/SAISON/2026/a.jpg")
        path = url.split("https://cockpit.test", 1)[1]
        assert client.get(path).status_code == 200
        assert client.get(path.replace("sig=", "sig=0")).status_code == 404
        assert client.get("/api/integrations/friday/photo?p=SAISON/2026/a.jpg&exp=9999999999&sig=abc").status_code == 404
