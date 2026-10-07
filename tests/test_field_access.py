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
        if key == "$and":
            if not all(_match(doc, sub) for sub in cond):
                return False
            continue
        val = _get(doc, key)
        if isinstance(cond, dict):
            for op, arg in cond.items():
                if op == "$ne" and val == arg:
                    return False
                if op == "$in" and val not in arg:
                    return False
                if op == "$exists" and (val is not None) != bool(arg):
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

    def insert_one(self, doc):
        self.docs.append(doc)
        return types.SimpleNamespace(inserted_id=doc.get("_id"))

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

    def test_sos_entre_saison_et_epreuve(self, env):
        """Les tablettes non revoquees dont les evenements vus croisent ceux de
        l'emetteur sont prevenues : une tablette SAISON recoit le SOS d'une
        tablette d'epreuve. Une tablette d'epreuve terminee (pas encore
        balayee) ne le recoit plus."""
        import event_courant as EC
        EC.invalidate()
        db = env_fake_app["db"]
        db["field_devices"].docs.extend([
            {"_id": "dev7", "name": "Saison Secu", "event": "SAISON", "year": "2025",
             "category": "PCO.Securite", "revoked": False},
            {"_id": "dev8", "name": "Ancienne", "event": "X", "year": "2025",
             "category": "PCO.Securite", "revoked": False},
        ])
        db["parametrages"].docs.append({"event": "X", "year": "2025", "data": {"globalHoraires": {
            "demontage": {"end": "2025-06-15"}}}})
        sender = db["field_devices"].find_one({"_id": "dev1"})
        names = sorted(d["name"] for d in field._sos_recipients(db, sender))
        assert names == ["Elec", "Libre", "Saison Secu", "Tech 2"]
        # Et dans l'autre sens : la tablette SAISON previent l'epreuve
        saison = db["field_devices"].find_one({"_id": "dev7"})
        assert "Tech 2" in [d["name"] for d in field._sos_recipients(db, saison)]

    def test_admin_change_categorie(self, env):
        env_fake_app["mod"].CODING = True
        rep = env.post("/field/admin/devices/%s/category" % ("0" * 24),
                       json={"category": "PCO.Inconnue"})
        assert rep.status_code == 400
        assert rep.get_json()["error"] == "invalid_category"


class TestModeDeclarant:
    """Groupe coche "mode declarant" : la tablette ne cree jamais de fiche
    (elle depose des constats, cf. test_declarations.py), ne recoit pas les
    SOS des autres et ne voit que son propre SOS dans ses fiches."""

    @pytest.fixture
    def decl(self, env, monkeypatch):
        db = env_fake_app["db"]
        db["anoloc_config"].docs[0]["beacon_groups"].append(
            {"id": "grp-cap", "pco_category": "PCO.Technique", "declarant": True})
        db["field_devices"].docs.append(dict(
            {"event": "E", "year": "2026", "revoked": False},
            _id="dev9", name="Presta 1", beacon_group_id="grp-cap",
            status="patrouille", token_hash=field._hash_token("tok9")))
        db["pcorg"].docs.extend([
            {"_id": "d1", "event": "E", "year": 2026, "status_code": 1, "category": "PCO.Technique",
             "text": "barriere", "operator_id_create": "field:dev9",
             "content_category": {"declarant": "Presta 1", "field_created": True}},
            # fiche d'un autre, meme categorie : jamais visible du declarant
            {"_id": "d2", "event": "E", "year": 2026, "status_code": 1, "category": "PCO.Technique",
             "text": "autre", "content_category": {}},
        ])
        started = []
        monkeypatch.setattr(field.DA, "maybe_auto_start", lambda db, fid, **k: started.append(fid))
        monkeypatch.setattr(field, "_claim_client_request", lambda *a: (True, None))
        monkeypatch.setattr(field, "_finish_client_request", lambda *a, **k: None)
        return env, db, started

    def test_drapeau_lu_sur_le_groupe(self, decl):
        _, db, _ = decl
        devs = {d["_id"]: d for d in db["field_devices"].docs}
        assert field._device_declarant(db, devs["dev9"]) is True
        assert field._device_declarant(db, devs["dev1"]) is False

    def test_ne_recoit_pas_les_sos_des_autres(self, decl):
        _, db, _ = decl
        sender = db["field_devices"].find_one({"_id": "dev1"})
        names = [d["name"] for d in field._sos_recipients(db, sender)]
        assert "Presta 1" not in names
        # mais son propre SOS part bien vers les equipes
        me = db["field_devices"].find_one({"_id": "dev9"})
        assert "Tech 2" in [d["name"] for d in field._sos_recipients(db, me)]

    def test_pas_de_fiches_sauf_son_sos(self, decl):
        client, db, _ = decl
        db["pcorg"].docs.append({"_id": "s9", "event": "E", "year": 2026, "status_code": 1,
                                 "category": "PCO.Secours", "text": "SOS",
                                 "content_category": {"patrouille": "Presta 1", "field_sos": True}})
        db["pcorg"].docs.append({"_id": "x9", "event": "E", "year": 2026, "status_code": 1,
                                 "category": "PCO.Technique", "text": "x",
                                 "content_category": {"patrouille": "Presta 1"}})
        _as_tablet(client, "tok9")
        data = client.get("/field/my-fiches", headers=JSON).get_json()
        assert data["declarant"] is True
        assert data["proposal"] is None
        assert [f["id"] for f in data["open"]] == ["s9"]

    def test_creation_de_fiche_refusee(self, decl):
        client, db, started = decl
        _as_tablet(client, "tok9")
        rep = client.post("/field/create-fiche", headers=JSON,
                          json={"category": "PCO.Technique", "text": "eclairage HS"})
        assert rep.status_code == 403
        assert rep.get_json()["error"] == "declarant"
        assert started == []

    def test_photos_rangees_a_part(self, decl):
        _, db, _ = decl
        dev = db["field_devices"].find_one({"_id": "dev9"})
        import os
        assert field._device_photo_sub_dir(db, dev) == os.path.join("_declarant", "E", "2026")

    def test_purge_garde_les_photos_declarant(self, env, tmp_path):
        import os
        import time
        old = time.time() - 60 * 86400        # 60 jours
        keep = tmp_path / "_declarant" / "E" / "2026"
        keep.mkdir(parents=True)
        (keep / "a.jpg").write_bytes(b"x")
        gone = tmp_path / "E" / "2026" / "b.jpg"
        gone.write_bytes(b"x")
        for p in (keep / "a.jpg", gone):
            os.utime(p, (old, old))
        field.purge_old_photo_files()
        assert (keep / "a.jpg").exists()
        assert not gone.exists()


class TestPushVapid:
    def test_cle_pkcs8_lue_par_pywebpush(self, monkeypatch):
        pytest.importorskip("py_vapid")
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        monkeypatch.setattr(field, "_VAPID_PRIVATE_KEY", pem.decode("utf-8"))
        monkeypatch.setattr(field, "_VAPID_SIGNER", None)
        signer = field._vapid_signer()
        # Le texte PEM passe tel quel echouait ("Could not deserialize key data")
        assert signer.sign({"aud": "https://web.push.apple.com", "sub": "https://cockpit.lemans.org",
                            "exp": 9999999999})

    def test_contact_valide_pour_apple(self, monkeypatch):
        monkeypatch.setattr(field, "VAPID_CONTACT_EMAIL", "dev@cockpit.local")
        assert field._vapid_subject() == "https://cockpit.lemans.org"
        monkeypatch.setattr(field, "VAPID_CONTACT_EMAIL", "pcorg@lemans.org")
        assert field._vapid_subject() == "mailto:pcorg@lemans.org"


class TestReactivationApresFinEvenement:
    def test_tablette_reactivee_apres_la_fin_reste_active(self, env, monkeypatch):
        from datetime import datetime, timedelta, timezone
        fin = datetime(2026, 9, 30, 18, tzinfo=timezone.utc)
        monkeypatch.setattr(field, "_event_end_datetime", lambda *a: fin)
        dev = {"event": "E", "year": "2026"}
        # reactivee apres la fin : choix explicite de l'admin, pas de revocation
        dev["restoredAt"] = (fin + timedelta(hours=10)).replace(tzinfo=None)
        assert field._restored_after_event_end(None, dev)
        # reactivee avant la fin (ou jamais) : l'expiration automatique s'applique
        dev["restoredAt"] = (fin - timedelta(days=1)).replace(tzinfo=None)
        assert not field._restored_after_event_end(None, dev)
        assert not field._restored_after_event_end(None, {"event": "E", "year": "2026"})
