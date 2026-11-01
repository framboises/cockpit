"""Droits de groupe Bibler (fonctions pures) et relais de lecture /api/bibler/*."""
import sys
import types

import pytest
from flask import Flask, request

import bibler_pont
import bibler_postes as BP


@pytest.fixture
def env(monkeypatch):
    who = {"payload": {"email": "op@aco.fr"}, "voir": True}
    fake = types.ModuleType("app")

    def role_required(role):
        def deco(f):
            def w(*a, **k):
                request.user_payload = who["payload"]
                return f(*a, **k)
            w.__name__ = f.__name__
            return w
        return deco
    fake.role_required = role_required
    fake._user_can_view_bibler = lambda p: who["voir"]
    monkeypatch.setitem(sys.modules, "app", fake)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(BP.bibler_bp)
    appels = []
    monkeypatch.setattr(bibler_pont, "lire",
                        lambda chemin, params=None, capacites=("postes:lire",), ttl=0:
                        appels.append((chemin, params, capacites, ttl)) or {"ok": True})
    return types.SimpleNamespace(client=app.test_client(), who=who, appels=appels, mp=monkeypatch)


def test_lecture_sans_droit_403(env):
    env.who["voir"] = False
    r = env.client.get("/api/bibler/postes?event=SAISON&year=2026")
    assert r.status_code == 403 and r.get_json()["code"] == "droit_insuffisant"
    assert env.appels == []


def test_lecture_relayee(env):
    r = env.client.get("/api/bibler/postes?event=SAISON&year=2026&q=nord")
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    chemin, params, caps, ttl = env.appels[0]
    assert chemin == "/api/v1/postes"
    assert params == {"event": "SAISON", "year": "2026", "q": "nord"}
    assert ttl == 30
    env.client.get("/api/bibler/postes?event=SAISON&year=2026&a=2026-06-13T10:00")
    assert env.appels[1][1]["a"] == "2026-06-13T10:00" and env.appels[1][3] == 0


def test_relais_autres_routes(env):
    env.client.get("/api/bibler/postes/12?event=E&year=2026")
    env.client.get("/api/bibler/formats?event=E&year=2026&tout=1&postes=1,2&junk=x")
    env.client.get("/api/bibler/consignes?event=E&year=2026&junk=x")
    env.client.get("/api/bibler/consignes/cibles?event=E&year=2026&q=n&junk=x")
    env.client.get("/api/bibler/consignes/apercu?event=E&zones=z&debut=d&fin=f&junk=x")
    env.client.get("/api/bibler/consignes/" + "a1" * 12)
    chemins = [a[0] for a in env.appels]
    assert chemins == ["/api/v1/postes/12", "/api/v1/vacations/formats", "/api/v1/consignes",
                       "/api/v1/consignes/cibles", "/api/v1/consignes/apercu",
                       "/api/v1/consignes/" + "a1" * 12]
    assert env.appels[1][3] == 300
    assert env.appels[1][1] == {"event": "E", "year": "2026", "tout": "1", "postes": "1,2"}
    assert env.appels[2][1] == {"event": "E", "year": "2026"}
    assert env.appels[3][1] == {"event": "E", "year": "2026", "q": "n"}
    assert env.appels[4][1] == {"event": "E", "zones": "z", "debut": "d", "fin": "f"}
    assert env.appels[5][1] == {}
    assert env.appels[2][2] == ("consignes:lire",)


def test_id_consigne_invalide_404(env):
    assert env.client.get("/api/bibler/consignes/..%2Fx").status_code == 404
    assert env.client.get("/api/bibler/consignes/zz").status_code == 404
    assert env.appels == []


def test_visuel(env):
    vus = []
    env.mp.setattr(bibler_pont, "lire_binaire",
                   lambda chemin, ttl=86400: vus.append(chemin) or (b"IMG", "image/png"))
    r = env.client.get("/api/bibler/visuel/poste-1.png")
    assert r.status_code == 200 and r.data == b"IMG"
    assert r.headers["Content-Type"] == "image/png"
    assert r.headers["Cache-Control"] == "private, max-age=86400"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    env.mp.setattr(bibler_pont, "lire_binaire", lambda c, ttl=86400: (b"<svg/>", "image/svg+xml"))
    assert env.client.get("/api/bibler/visuel/x.svg").status_code == 404
    env.mp.setattr(bibler_pont, "lire_binaire", lambda c, ttl=86400: (b"IMG", "image/png"))
    assert vus == ["/api/v1/specimens/visuel/poste-1.png"]
    assert env.client.get("/api/bibler/visuel/.cache").status_code == 404
    assert env.client.get("/api/bibler/visuel/a%25b").status_code == 404


def test_relais_bibler_indisponible(env):
    def boom(*a, **k):
        raise bibler_pont.BiblerIndisponible("x")
    env.mp.setattr(bibler_pont, "lire", boom)
    r = env.client.get("/api/bibler/postes?event=E&year=2026")
    assert r.status_code == 502 and r.get_json()["code"] == "bibler_indisponible"


def test_non_configure_503(env):
    def boom(*a, **k):
        raise bibler_pont.BiblerNonConfigure("x")
    env.mp.setattr(bibler_pont, "lire", boom)
    r = env.client.get("/api/bibler/formats")
    assert r.status_code == 503 and r.get_json()["code"] == "bibler_non_configure"


def test_erreur_bibler_relayee(env):
    def boom(*a, **k):
        raise bibler_pont.BiblerErreur(404, {"code": "evenement_inconnu"})
    env.mp.setattr(bibler_pont, "lire", boom)
    r = env.client.get("/api/bibler/postes?event=X&year=1")
    assert r.status_code == 404 and r.get_json() == {"code": "evenement_inconnu"}


def test_erreur_bibler_corps_vide(env):
    def boom(*a, **k):
        raise bibler_pont.BiblerErreur(400, None)
    env.mp.setattr(bibler_pont, "lire", boom)
    r = env.client.get("/api/bibler/formats")
    assert r.status_code == 400 and r.get_json()["code"] == "bibler_erreur"


def test_droits_groupe():
    u = {"email": "a@b.fr"}
    assert BP.peut_voir(u, []) is False
    assert BP.peut_voir(u, [{"bibler_postes": True}]) is True
    assert BP.peut_publier(u, [{"can_publish_consigne": True}]) is False
    assert BP.peut_publier(u, [{"bibler_postes": True}]) is False
    assert BP.peut_publier(u, [{"bibler_postes": True, "can_publish_consigne": True}]) is True
    assert BP.peut_publier(u, [{"bibler_postes": True}, {"can_publish_consigne": True}]) is True
    assert BP.peut_voir({"app_role": "admin"}, []) is True
    assert BP.peut_publier({"is_super_admin": True}, []) is True
    borne = {"type_compte": "borne", "app_role": "admin"}
    assert BP.peut_voir(borne, [{"bibler_postes": True}]) is False
    assert BP.peut_publier(borne, [{"bibler_postes": True, "can_publish_consigne": True}]) is False
