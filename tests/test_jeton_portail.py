"""Decodeur unique du jeton du portail TITAN Home v2 (app.decoder_jeton_portail)
contre un MongoDB local jetable : choix de l'algorithme par l'en-tete (EdDSA par
le JWKS, HS256 par JWT_SECRET, rien d'autre), pas de confusion d'algorithme,
JWKS injoignable, puis revocation partagee (titan_shared.revocation, guide 05 §3)."""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import jwt
import pytest
from bson import ObjectId
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask
from jwt.algorithms import OKPAlgorithm
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from titan_shared.revocation import cache_par_defaut

import app as A

KID = "ed-20261025-test0001"
NAVIGATEUR = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def _connexion(url_retour):
    return "%s/login?next=%s" % (A.BASE_URL, quote(url_retour, safe=""))


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _forge(header, payload, signature=b""):
    """Jeton fabrique a la main : PyJWT refuse de signer certains cas d'attaque."""
    return ".".join([_b64(json.dumps(header).encode()), _b64(json.dumps(payload).encode()),
                     _b64(signature)])


def _forge_hmac(header, payload, secret):
    entete = _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(payload).encode())
    sig = hmac.new(secret, entete.encode(), hashlib.sha256).digest()
    return entete + "." + _b64(sig)


class _SpyDb:
    """Base reelle dont on note les collections reellement interrogees."""

    def __init__(self, db):
        self._db = db
        self.name = db.name
        self.lues = []

    def __getitem__(self, nom):
        return _SpyCollection(self, nom, self._db[nom])


class _SpyCollection:
    def __init__(self, spy, nom, col):
        self._spy, self._nom, self._col = spy, nom, col

    def find_one(self, *a, **kw):
        self._spy.lues.append(self._nom)
        return self._col.find_one(*a, **kw)

    def find(self, *a, **kw):
        self._spy.lues.append(self._nom)
        return self._col.find(*a, **kw)


@pytest.fixture
def env(monkeypatch):
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_jeton_%d" % os.getpid()
    assert name.startswith("titan_test_")
    client.drop_database(name)
    db = client[name]
    monkeypatch.setattr(A, "db", db)
    monkeypatch.setattr(A, "CODING", False)
    monkeypatch.setattr(A, "JWT_SECRET", "secret-hs256-de-test-" + "x" * 20)

    privee = Ed25519PrivateKey.generate()
    jwk = OKPAlgorithm.to_jwk(privee.public_key(), as_dict=True)
    jwk.update(kid=KID, alg="EdDSA", use="sig")
    client_jwks = jwt.PyJWKClient("http://jwks.test/.well-known/jwks.json",
                                  cache_keys=True, lifespan=3600)
    lectures = []

    def fetch_data():
        lectures.append(1)
        return {"keys": [jwk]}

    monkeypatch.setattr(client_jwks, "fetch_data", fetch_data)
    monkeypatch.setattr(A, "_jwks_client", client_jwks)

    uid = ObjectId()
    db["users"].insert_one({"_id": uid, "email": "op@test", "actif": True})
    cache_par_defaut.vider()
    maintenant = int(time.time())
    yield {
        "db": db, "uid": uid, "privee": privee, "jwk": jwk, "lectures": lectures,
        "claims": {"user_id": str(uid), "email": "op@test", "roles_by_app": {"cockpit": "user"},
                   "global_roles": [], "iss": "titan-home", "jti": str(uuid.uuid4()),
                   "sid": "a" * 32, "iat": maintenant - 60, "exp": maintenant + 3600},
    }
    cache_par_defaut.vider()
    client.drop_database(name)


def _hs256(claims):
    return jwt.encode(claims, A.JWT_SECRET, algorithm="HS256", headers={"kid": "k1"})


def _eddsa(env, claims=None, kid=KID, cle=None):
    return jwt.encode(claims or env["claims"], cle or env["privee"], algorithm="EdDSA",
                      headers={"kid": kid})


# ---------------------------------------------------------------------------
# Signature et choix de l'algorithme
# ---------------------------------------------------------------------------

class TestAlgorithme:
    def test_hs256_valide_accepte(self, env):
        assert A.decoder_jeton_portail(_hs256(env["claims"]))["email"] == "op@test"

    def test_eddsa_valide_accepte(self, env):
        assert A.decoder_jeton_portail(_eddsa(env))["email"] == "op@test"

    def test_eddsa_cle_en_cache(self, env):
        A.decoder_jeton_portail(_eddsa(env))
        A.decoder_jeton_portail(_eddsa(env))
        assert len(env["lectures"]) == 1

    def test_alg_none_refuse(self, env):
        jeton = _forge({"alg": "none", "typ": "JWT"}, env["claims"])
        with pytest.raises(jwt.InvalidAlgorithmError):
            A.decoder_jeton_portail(jeton)

    def test_alg_inconnu_refuse_meme_signe_avec_le_secret(self, env):
        jeton = jwt.encode(env["claims"], A.JWT_SECRET, algorithm="HS512")
        with pytest.raises(jwt.InvalidAlgorithmError):
            A.decoder_jeton_portail(jeton)

    def test_hs256_signe_avec_la_cle_publique_refuse(self, env):
        publique = env["privee"].public_key()
        pem = publique.public_bytes(serialization.Encoding.PEM,
                                    serialization.PublicFormat.SubjectPublicKeyInfo)
        brute = publique.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        for secret in (pem, brute, env["jwk"]["x"].encode()):
            for kid in (KID, "k1"):
                jeton = _forge_hmac({"alg": "HS256", "typ": "JWT", "kid": kid}, env["claims"], secret)
                with pytest.raises(jwt.InvalidSignatureError):
                    A.decoder_jeton_portail(jeton)

    def test_en_tete_eddsa_signe_en_hmac_refuse(self, env):
        jeton = _forge_hmac({"alg": "EdDSA", "typ": "JWT", "kid": KID}, env["claims"],
                            A.JWT_SECRET.encode())
        with pytest.raises(jwt.InvalidTokenError):
            A.decoder_jeton_portail(jeton)

    def test_eddsa_kid_inconnu_refuse(self, env):
        with pytest.raises(jwt.InvalidTokenError):
            A.decoder_jeton_portail(_eddsa(env, kid="ed-inconnu"))
        # kid inconnu : le JWKS a ete relu (rotation), sans trouver la cle
        assert len(env["lectures"]) >= 1

    def test_eddsa_signe_par_une_autre_cle_refuse(self, env):
        with pytest.raises(jwt.InvalidSignatureError):
            A.decoder_jeton_portail(_eddsa(env, cle=Ed25519PrivateKey.generate()))

    def test_eddsa_sans_iss_refuse(self, env):
        claims = {k: v for k, v in env["claims"].items() if k != "iss"}
        with pytest.raises(jwt.MissingRequiredClaimError):
            A.decoder_jeton_portail(_eddsa(env, claims))

    def test_eddsa_mauvais_iss_refuse(self, env):
        with pytest.raises(jwt.InvalidIssuerError):
            A.decoder_jeton_portail(_eddsa(env, dict(env["claims"], iss="autre")))

    def test_expire_refuse(self, env):
        claims = dict(env["claims"], exp=int(time.time()) - 5)
        for jeton in (_hs256(claims), _eddsa(env, claims)):
            with pytest.raises(jwt.ExpiredSignatureError):
                A.decoder_jeton_portail(jeton)

    def test_jeton_illisible_refuse(self, env):
        with pytest.raises(jwt.InvalidTokenError):
            A.decoder_jeton_portail("pas-un-jeton")

    def test_jwks_injoignable_eddsa_refuse_hs256_accepte(self, env, monkeypatch):
        # Port 9 (discard) ferme : connexion refusee, comme un portail arrete
        monkeypatch.setattr(A, "_jwks_client", jwt.PyJWKClient(
            "http://127.0.0.1:9/.well-known/jwks.json", timeout=2))
        with pytest.raises(jwt.InvalidTokenError):
            A.decoder_jeton_portail(_eddsa(env))
        assert A.decoder_jeton_portail(_hs256(env["claims"]))["email"] == "op@test"


# ---------------------------------------------------------------------------
# Revocation partagee (guide 05 §3)
# ---------------------------------------------------------------------------

class TestRevocation:
    def _refuse(self, env):
        for jeton in (_hs256(env["claims"]), _eddsa(env)):
            cache_par_defaut.vider()
            with pytest.raises(A.JetonRevoque):
                A.decoder_jeton_portail(jeton)

    def test_jeton_revoque_est_un_jeton_invalide(self):
        assert issubclass(A.JetonRevoque, jwt.InvalidTokenError)

    def test_compte_desactive(self, env):
        env["db"]["users"].update_one({"_id": env["uid"]}, {"$set": {"actif": False}})
        self._refuse(env)

    def test_compte_disabled(self, env):
        env["db"]["users"].update_one({"_id": env["uid"]}, {"$set": {"disabled": True}})
        self._refuse(env)

    def test_compte_absent(self, env):
        env["db"]["users"].delete_one({"_id": env["uid"]})
        self._refuse(env)

    def test_sessions_revoquees_avant(self, env):
        seuil = datetime.fromtimestamp(env["claims"]["iat"] + 10, timezone.utc)
        env["db"]["users"].update_one({"_id": env["uid"]},
                                      {"$set": {"sessions_revoquees_avant": seuil}})
        self._refuse(env)

    def test_sessions_revoquees_avant_l_emission_n_affecte_pas(self, env):
        seuil = datetime.fromtimestamp(env["claims"]["iat"] - 3600, timezone.utc)
        env["db"]["users"].update_one({"_id": env["uid"]},
                                      {"$set": {"sessions_revoquees_avant": seuil}})
        assert A.decoder_jeton_portail(_hs256(env["claims"]))

    def test_jti_revoque(self, env):
        env["db"]["home_jetons_revoques"].insert_one({"jti": env["claims"]["jti"]})
        self._refuse(env)

    def test_sid_revoque(self, env):
        env["db"]["home_jetons_revoques"].insert_one(
            {"jti": "sid:" + env["claims"]["sid"], "type": "sid"})
        self._refuse(env)

    def test_autre_jti_n_affecte_pas(self, env):
        env["db"]["home_jetons_revoques"].insert_one({"jti": str(uuid.uuid4())})
        assert A.decoder_jeton_portail(_hs256(env["claims"]))

    def test_jeton_v1_sans_jti_ni_sid(self, env, monkeypatch):
        claims = {k: v for k, v in env["claims"].items() if k not in ("jti", "sid", "iss")}
        env["db"]["home_jetons_revoques"].insert_many([{"jti": "sid:"}, {"jti": "sid:None"}])
        spy = _SpyDb(env["db"])
        monkeypatch.setattr(A, "db", spy)
        assert A.decoder_jeton_portail(_hs256(claims))["email"] == "op@test"
        # Seule la partie users s'applique : home_jetons_revoques n'est pas lue
        assert spy.lues == ["users"]

        cache_par_defaut.vider()
        env["db"]["users"].update_one({"_id": env["uid"]}, {"$set": {"disabled": True}})
        with pytest.raises(A.JetonRevoque):
            A.decoder_jeton_portail(_hs256(claims))


# ---------------------------------------------------------------------------
# Appelants : role_required et _request_payload_peek
# ---------------------------------------------------------------------------

@pytest.fixture
def client(env):
    flask_app = Flask(__name__)
    flask_app.config.update(TESTING=True, SECRET_KEY="test")

    @flask_app.route("/protege")
    @A.role_required("user")
    def protege():
        return "ok"

    @flask_app.route("/api/protege")
    @A.role_required("user")
    def api_protege():
        return "ok"

    @flask_app.route("/peek")
    def peek():
        p = A._request_payload_peek()
        return (p or {}).get("app_role") or "aucun"

    return flask_app.test_client()


class TestAppelants:
    def test_role_required_jeton_valide(self, env, client):
        client.set_cookie("access_token", _eddsa(env))
        assert client.get("/protege").data == b"ok"

    def test_role_required_jeton_revoque_renvoie_au_portail(self, env, client):
        env["db"]["home_jetons_revoques"].insert_one({"jti": env["claims"]["jti"]})
        client.set_cookie("access_token", _hs256(env["claims"]))
        rep = client.get("/protege", headers=NAVIGATEUR)
        assert rep.status_code == 302 and rep.location == _connexion("http://localhost/protege")
        rep = client.get("/api/protege")
        assert rep.status_code == 401 and rep.get_json()["erreur"] == "session_revoquee"

    def test_role_required_alg_none_renvoie_au_portail(self, env, client):
        client.set_cookie("access_token", _forge({"alg": "none"}, env["claims"]))
        rep = client.get("/protege", headers=NAVIGATEUR)
        assert rep.status_code == 302 and rep.location == _connexion("http://localhost/protege")
        rep = client.get("/api/protege")
        assert rep.status_code == 401 and rep.get_json()["erreur"] == "auth_invalide"

    def test_role_required_expire(self, env, client):
        claims = dict(env["claims"], exp=int(time.time()) - 10)
        client.set_cookie("access_token", _hs256(claims))
        assert client.get("/protege", headers=NAVIGATEUR).location == _connexion("http://localhost/protege")
        rep = client.get("/api/protege")
        assert rep.status_code == 401 and rep.get_json()["erreur"] == "session_expiree"

    def test_sans_cookie_page_retour_avec_parametres(self, env, client):
        rep = client.get("/protege?event=24H%20AUTOS&year=2026", headers=NAVIGATEUR)
        assert rep.status_code == 302
        assert rep.location == _connexion("http://localhost/protege?event=24H%20AUTOS&year=2026")
        assert "/home" not in rep.location
        rep = client.get("/protege", headers=NAVIGATEUR)
        assert rep.location == _connexion("http://localhost/protege")  # sans "?" final

    def test_sans_cookie_api_401_json_sans_redirection(self, env, client):
        rep = client.get("/api/protege")
        assert rep.status_code == 401 and "Location" not in rep.headers
        corps = rep.get_json()
        assert corps["ok"] is False and corps["erreur"] == "auth_requise"
        assert corps["login"] == _connexion("http://localhost/api/protege")

    def test_sans_cookie_appel_js_hors_api_401(self, env, client):
        # session_portail.js ajoute X-Requested-With a tous les appels vers Cockpit
        for entetes in ({"X-Requested-With": "XMLHttpRequest"}, {"Accept": "application/json"}):
            rep = client.get("/protege", headers=entetes)
            assert rep.status_code == 401 and rep.get_json()["erreur"] == "auth_requise"

    def test_retour_vers_l_origine_publique_en_production(self, env, client, monkeypatch):
        # Sans ProxyFix, request.url serait en 127.0.0.1 : le portail le refuserait
        monkeypatch.setattr(A, "IS_PROD", True)
        rep = client.get("/protege?x=1", headers=NAVIGATEUR)
        assert rep.location == _connexion("https://cockpit.lemans.org/protege?x=1")

    def test_peek_jeton_revoque_ignore(self, env, client):
        client.set_cookie("access_token", _hs256(env["claims"]))
        assert client.get("/peek").data == b"user"
        env["db"]["users"].update_one({"_id": env["uid"]}, {"$set": {"actif": False}})
        cache_par_defaut.vider()
        assert client.get("/peek").data == b"aucun"
