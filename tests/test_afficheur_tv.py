"""Afficheurs TV du portail (guide 05 §10) sur la vraie application et ses
hooks, contre un MongoDB local jetable : un afficheur (borne + afficheur_id +
role cockpit) lit les seules pages de PAGES_AFFICHEUR au-dessus de user, un
humain "user" reste refuse, un compte borne n'ecrit jamais ; mode cadre."""
import os
import time
import uuid

import jwt
import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError
from titan_shared.revocation import cache_par_defaut

import app as A


@pytest.fixture
def env(monkeypatch):
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_afficheur_%d" % os.getpid()
    assert name.startswith("titan_test_")
    client.drop_database(name)
    db = client[name]
    monkeypatch.setattr(A, "db", db)
    # Collections liees a la base de dev a l'import d'app.py : redirigees
    for attr, val in list(vars(A).items()):
        if isinstance(val, Collection) and val.database.name != name:
            monkeypatch.setattr(A, attr, db[val.name])
    monkeypatch.setattr(A, "client", None)
    monkeypatch.setattr(A, "CODING", False)
    monkeypatch.setattr(A, "JWT_SECRET", "secret-hs256-de-test-" + "x" * 20)
    monkeypatch.setitem(A.app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setitem(A.app.config, "TESTING", True)
    A._user_name_cache.clear()
    cache_par_defaut.vider()
    yield db
    cache_par_defaut.vider()
    client.drop_database(name)


def _jeton(db, role="user", borne=True, afficheur=True, global_roles=None):
    uid = ObjectId()
    db["users"].insert_one({"_id": uid, "email": "%s@test" % uid, "actif": True})
    maintenant = int(time.time())
    claims = {"user_id": str(uid), "email": "%s@test" % uid, "roles_by_app": {"cockpit": role},
              "global_roles": global_roles or [], "iss": "titan-home", "jti": str(uuid.uuid4()),
              "sid": "b" * 32, "iat": maintenant - 60, "exp": maintenant + 3600,
              "firstname": "Ecran", "lastname": "TV"}
    if borne:
        claims.update(type_compte="borne", lecture_seule=True, active_par="admin@test")
    else:
        claims["type_compte"] = "domaine"
    if borne and afficheur:
        claims["afficheur_id"] = "pi-pco-01"
    return jwt.encode(claims, A.JWT_SECRET, algorithm="HS256")


@pytest.fixture
def client(env):
    return A.app.test_client()


def _avec(client, jeton):
    client.set_cookie("access_token", jeton)
    return client


def _refuse(rep):
    """role_required refuse par un retour (referrer ou accueil), jamais un 200."""
    assert rep.status_code == 302, rep.status_code
    assert "/home?" not in rep.location      # pas un probleme de jeton : un refus de role


class TestLectureAfficheur:
    def test_afficheur_accepte_sur_meteo_mur(self, env, client):
        rep = _avec(client, _jeton(env)).get("/meteo-mur")
        assert rep.status_code == 200
        assert b"/api/meteo/mur" in rep.data

    def test_afficheur_accepte_sur_les_donnees_du_mur(self, env, client):
        rep = _avec(client, _jeton(env)).get("/api/meteo/mur")
        assert rep.status_code == 200
        assert rep.is_json

    def test_afficheur_accepte_sur_circulation(self, env, client):
        assert _avec(client, _jeton(env)).get("/circulation").status_code == 200

    def test_mur_des_portes_sur_l_edition_demandee_par_le_portail(self, env, client):
        # Meme contrat que /terrains et /general_stat : ?event=&year= de l'URL
        c = _avec(client, _jeton(env))
        rep = c.get("/portes-mur?event=24H%20MOTOS&year=2026")
        assert rep.status_code == 200
        assert b'var EVENT = "24H MOTOS";' in rep.data and b'var YEAR  = "2026";' in rep.data
        assert rep.headers.get("Content-Security-Policy") == TestModeCadre.CSP
        rep = c.get("/api/portes/etat?event=24H%20MOTOS&year=2026&vue=tv")
        assert rep.status_code == 200
        assert rep.get_json()["event"] == "24H MOTOS" and rep.get_json()["year"] == 2026

    def test_mur_des_portes_sans_edition_refuse(self, env, client):
        c = _avec(client, _jeton(env))
        assert c.get("/portes-mur").status_code == 400
        assert c.get("/portes-mur?event=24H%20MOTOS").status_code == 400

    def test_humain_user_toujours_refuse(self, env, client):
        for chemin in ("/meteo-mur", "/api/meteo/mur", "/circulation"):
            _refuse(_avec(client, _jeton(env, borne=False)).get(chemin))

    def test_humain_manager_toujours_accepte(self, env, client):
        assert _avec(client, _jeton(env, role="manager", borne=False)).get("/meteo-mur").status_code == 200

    def test_borne_sans_afficheur_id_refusee(self, env, client):
        _refuse(_avec(client, _jeton(env, afficheur=False)).get("/meteo-mur"))

    def test_afficheur_sans_role_cockpit_refuse(self, env, client):
        rep = _avec(client, _jeton(env, role=None)).get("/meteo-mur")
        assert rep.status_code == 302
        assert "/home?" in rep.location

    def test_afficheur_refuse_sur_page_non_listee(self, env, client):
        # /api/todo-sets exige manager et n'est pas dans PAGES_AFFICHEUR
        _refuse(_avec(client, _jeton(env)).get("/api/todo-sets"))

    def test_borne_plafonnee_a_user_meme_avec_un_role_superieur(self, env, client):
        for jeton in (_jeton(env, role="admin"),
                      _jeton(env, role="user", global_roles=[A.SUPER_ADMIN_ROLE])):
            _refuse(_avec(client, jeton).get("/api/todo-sets"))

    def test_afficheur_ne_recoit_aucune_alerte(self, env, client):
        A.COL_ALERT_DEFS.insert_one({"slug": "x", "enabled": True})
        env["cockpit_active_alerts"].insert_one({"definition_slug": "x", "message": "test",
                                         "expiresAt": A.datetime.now(A.timezone.utc)
                                         + A.timedelta(hours=1)})
        rep = _avec(client, _jeton(env)).get("/api/active-alerts")
        assert rep.status_code == 200 and rep.get_json() == []
        humain = _avec(A.app.test_client(), _jeton(env, borne=False)).get("/api/active-alerts")
        assert len(humain.get_json()) == 1


class TestEcritureBorne:
    @pytest.mark.parametrize("methode,chemin", [
        ("POST", "/api/todo-sets"),
        ("PUT", "/api/todo-sets/abc"),
        ("DELETE", "/api/timetable-categories"),
        ("PATCH", "/api/todo-sets/abc"),
        ("POST", "/meteo-mur"),                 # meme une page listee
        ("PUT", "/api/meteo/config"),           # blueprint a garde propre
        ("POST", "/api/field/inconnue"),        # route inexistante : refus avant routage
    ])
    def test_ecriture_refusee(self, env, client, methode, chemin):
        for jeton in (_jeton(env), _jeton(env, role="admin", afficheur=False)):
            rep = _avec(client, jeton).open(chemin, method=methode, json={})
            assert rep.status_code == 403
            assert rep.get_json() == {"ok": False, "error": "lecture_seule"}

    def test_humain_non_bloque_par_le_garde(self, env, client):
        rep = _avec(client, _jeton(env, role="manager", borne=False)).post("/api/todo-sets", json={})
        assert rep.status_code != 403 or rep.get_json() != {"ok": False, "error": "lecture_seule"}


class TestModeCadre:
    CSP = "frame-ancestors 'self' https://safe.lemans.org"

    def test_pages_tv_encadrables_par_le_portail(self, env, client):
        for chemin in ("/meteo-mur", "/circulation"):
            rep = _avec(client, _jeton(env)).get(chemin)
            assert rep.headers.get("Content-Security-Policy") == self.CSP
            assert "X-Frame-Options" not in rep.headers

    def test_accueil_seulement_en_mode_tv(self):
        with A.app.test_request_context("/?tv=1"):
            assert A._cadre_portail_autorise()
        with A.app.test_request_context("/"):
            assert not A._cadre_portail_autorise()

    def test_ni_api_ni_page_non_listee(self, env, client):
        with A.app.test_request_context("/api/meteo/mur"):
            assert not A._cadre_portail_autorise()
        rep = _avec(client, _jeton(env, role="manager", borne=False)).get("/api/todo-sets")
        assert "Content-Security-Policy" not in rep.headers
