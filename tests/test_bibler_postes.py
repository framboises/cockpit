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
    fake._user_can_publish_consigne = lambda p: who.get("publier", True)
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
    assert r.headers["Content-Security-Policy"] == "sandbox; default-src 'none'"
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


# --- Ecriture ---------------------------------------------------------------------------------------------------

BASE = {"titre": "T", "texte": "X", "priorite": "information", "event": "SAISON", "year": 2026,
        "cible": {"departements": ["D"], "zones": [], "secteurs": [], "postes": [1], "tout_evenement": False},
        "dans_briefing": True}


def _form(duree):
    return dict(BASE, duree=duree)


def test_duree_permanente():
    c = BP.consigne_depuis_formulaire(_form({"mode": "permanente"}))
    assert "debut" not in c and "fin" not in c
    assert c["cible"]["formats"] == [] and c["statut"] == "publiee"
    assert c["titre"] == "T" and c["event"] == "SAISON" and c["year"] == 2026
    assert c["cible"]["postes"] == [1] and c["dans_briefing"] is True


def test_duree_shift():
    d = {"mode": "shift", "debut": "2026-06-13T10:00:00+02:00", "fin": "2026-06-13T18:00:00+02:00",
         "format": "10:00-18:00"}
    c = BP.consigne_depuis_formulaire(_form(d))
    assert c["debut"] == d["debut"] and c["fin"] == d["fin"] and c["cible"]["formats"] == ["10:00-18:00"]


def test_duree_format():
    c = BP.consigne_depuis_formulaire(_form({"mode": "format", "format": "10:00-18:00"}))
    assert "debut" not in c and "fin" not in c
    assert c["cible"]["formats"] == ["10:00-18:00"]


def test_duree_shift_passe_minuit():
    d = {"mode": "shift", "debut": "2026-06-13T18:00:00+02:00", "fin": "2026-06-14T02:00:00+02:00",
         "format": "18:00-02:00"}
    assert BP.consigne_depuis_formulaire(_form(d))["fin"] == d["fin"]


@pytest.mark.parametrize("debut,fin", [
    ("2026-06-13T10:00:00+02:00", "2026-06-13T10:00:00+02:00"),
    ("2026-06-13T10:00:00+02:00", "2026-06-13T09:00:00+02:00"),
    ("2026-06-13T10:00:00+02:00", "2026-06-14T16:00:00+02:00"),
    ("2026-06-13T10:00:00", "2026-06-13T18:00:00"),
    ("nimporte", "2026-06-13T18:00:00+02:00"),
])
def test_duree_shift_invalide(debut, fin):
    with pytest.raises(ValueError, match="shift_invalide"):
        BP.consigne_depuis_formulaire(_form({"mode": "shift", "debut": debut, "fin": fin, "format": "f"}))


def test_duree_invalide():
    with pytest.raises(ValueError, match="duree_invalide"):
        BP.consigne_depuis_formulaire(_form({"mode": "x"}))
    with pytest.raises(ValueError, match="duree_invalide"):
        BP.consigne_depuis_formulaire(_form({"mode": "format"}))


@pytest.fixture
def ecr(env):
    envois = []
    env.mp.setattr(bibler_pont, "envoyer",
                   lambda m, c, corps, capacites=("consignes:ecrire",), idempotence=None:
                   envois.append((m, c, corps, idempotence)) or ({"ok": True, "consigne": {"id": "a1" * 12}}, 201))
    env.envois = envois
    env.who["payload"] = {"email": "op@aco.fr", "firstname": "Ada", "lastname": "Lovelace"}
    return env


def test_ecriture_sans_droit_403(ecr):
    ecr.who["publier"] = False
    r = ecr.client.post("/api/bibler/consignes", json=_form({"mode": "permanente"}),
                        headers={"X-Idempotency-Key": "k"})
    assert r.status_code == 403 and ecr.envois == []


def test_ecriture_compte_borne_403(ecr):
    ecr.who["payload"] = {"email": "tv@aco.fr", "type_compte": "borne"}
    for r in (ecr.client.post("/api/bibler/consignes", json=_form({"mode": "permanente"}),
                              headers={"X-Idempotency-Key": "k"}),
              ecr.client.post("/api/bibler/consignes/" + "a1" * 12 + "/lever", json={})):
        assert r.status_code == 403
    assert ecr.envois == []


def test_relais_injecte_auteur(ecr):
    r = ecr.client.post("/api/bibler/consignes", json=_form({"mode": "permanente"}),
                        headers={"X-Idempotency-Key": "k-1"})
    assert r.status_code == 201
    m, chemin, corps, idem = ecr.envois[0]
    assert (m, chemin) == ("POST", "/api/v1/consignes")
    assert corps["source"]["auteur"] == {"email": "op@aco.fr", "nom": "Ada Lovelace"}
    assert "duree" not in corps


def test_relais_transmet_cle_idempotence(ecr):
    ecr.client.post("/api/bibler/consignes", json=_form({"mode": "permanente"}),
                    headers={"X-Idempotency-Key": "k-1"})
    assert ecr.envois[0][3] == "k-1"
    r = ecr.client.post("/api/bibler/consignes", json=_form({"mode": "permanente"}))
    assert r.status_code == 400 and r.get_json()["erreur"] == "idempotence_requise"
    assert len(ecr.envois) == 1


def test_creation_duree_invalide_422(ecr):
    r = ecr.client.post("/api/bibler/consignes", json=_form({"mode": "x"}), headers={"X-Idempotency-Key": "k"})
    assert r.status_code == 422 and r.get_json()["erreur"] == "duree_invalide"


def test_modifier_et_lever(ecr):
    cid = "a1" * 12
    r = ecr.client.patch("/api/bibler/consignes/" + cid, json={"titre": "N", "version": 2, "junk": 1})
    assert r.status_code == 201
    m, chemin, corps, _ = ecr.envois[0]
    assert (m, chemin) == ("PATCH", "/api/v1/consignes/" + cid)
    assert corps == {"titre": "N", "version": 2, "source": {"auteur": {"email": "op@aco.fr", "nom": "Ada Lovelace"}}}
    ecr.client.post("/api/bibler/consignes/%s/lever" % cid)
    assert ecr.envois[1][:2] == ("POST", "/api/v1/consignes/%s/lever" % cid)
    assert ecr.client.patch("/api/bibler/consignes/zz", json={}).status_code == 404
    assert ecr.client.post("/api/bibler/consignes/zz/lever").status_code == 404


def test_erreur_bibler_ecriture_relayee(ecr):
    def boom(*a, **k):
        raise bibler_pont.BiblerErreur(409, {"erreur": "version_perimee"})
    ecr.mp.setattr(bibler_pont, "envoyer", boom)
    r = ecr.client.patch("/api/bibler/consignes/" + "a1" * 12, json={"version": 1})
    assert r.status_code == 409 and r.get_json() == {"erreur": "version_perimee"}


def test_ecriture_journalisee(ecr, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="bibler_postes"):
        ecr.client.post("/api/bibler/consignes/%s/lever" % ("a1" * 12))
    assert any("Consigne Bibler levee " + "a1" * 12 + " par op@aco.fr" in m for m in caplog.messages)


def test_rejeu_meme_cle_corps_identique(ecr):
    import json
    import time
    form = _form({"mode": "permanente"})
    ecr.client.post("/api/bibler/consignes", json=form, headers={"X-Idempotency-Key": "k"})
    time.sleep(0.01)
    ecr.client.post("/api/bibler/consignes", json=form, headers={"X-Idempotency-Key": "k"})
    a, b = ecr.envois[0][2], ecr.envois[1][2]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_cible_non_liste_ignoree():
    f = _form({"mode": "permanente"})
    f["cible"] = {"departements": "ABC", "zones": 5, "secteurs": None, "postes": [1]}
    c = BP.consigne_depuis_formulaire(f)["cible"]
    assert c["departements"] == [] and c["zones"] == [] and c["secteurs"] == [] and c["postes"] == [1]


def test_page_sans_droit_403(env):
    env.who["voir"] = False
    r = env.client.get("/consignes-bibler")
    assert r.status_code == 403
    assert env.appels == []


def test_ecriture_reussie_vide_le_cache(ecr):
    vidages = []
    ecr.mp.setattr(bibler_pont, "vider_cache", lambda: vidages.append(1), raising=False)
    cid = "a1" * 12
    ecr.client.post("/api/bibler/consignes", json=_form({"mode": "permanente"}),
                    headers={"X-Idempotency-Key": "k-1"})
    ecr.client.patch("/api/bibler/consignes/" + cid, json={"titre": "N", "version": 2})
    ecr.client.post("/api/bibler/consignes/%s/lever" % cid, json={})
    assert len(vidages) == 3


def test_ecriture_en_echec_ne_vide_pas_le_cache(ecr):
    vidages = []
    ecr.mp.setattr(bibler_pont, "vider_cache", lambda: vidages.append(1), raising=False)
    ecr.mp.setattr(bibler_pont, "envoyer",
                   lambda *a, **k: ({"ok": False, "erreur": "version_perimee"}, 409))
    r = ecr.client.patch("/api/bibler/consignes/" + "a1" * 12, json={"titre": "N", "version": 1})
    assert r.status_code == 409 and vidages == []


def test_vider_cache_garde_les_visuels():
    bibler_pont._cache_set(("bin", "/x"), (b"1", "image/png"), 60)
    bibler_pont._cache_set(("/api/v1/consignes", (), ("consignes:lire",)), {"a": 1}, 60)
    bibler_pont.vider_cache()
    assert bibler_pont._cache_get(("bin", "/x")) is not None
    assert bibler_pont._cache_get(("/api/v1/consignes", (), ("consignes:lire",))) is None


# --- Refus du jeton de service par Bibler : jamais relaye en 401/403 (session_portail.js) ----------------------------

@pytest.mark.parametrize("statut,code", [(401, "auth_invalide"), (401, "auth_requise"),
                                         (403, "ip_refusee"), (403, "capacite_manquante")])
def test_lecture_refus_service_traduit(env, caplog, statut, code):
    import logging

    def boom(*a, **k):
        raise bibler_pont.BiblerErreur(statut, {"ok": False, "erreur": code})
    env.mp.setattr(bibler_pont, "lire", boom)
    with caplog.at_level(logging.WARNING, logger="bibler_postes"):
        r = env.client.get("/api/bibler/postes?event=E&year=2026")
    assert r.status_code == 502
    assert r.status_code not in (401, 403)
    assert r.get_json() == {"ok": False, "erreur": "bibler_refus_service",
                            "message": "Bibler refuse l'acces de Cockpit (configuration du pont)."}
    assert any(code in m and "/api/bibler/postes" in m for m in caplog.messages)


@pytest.mark.parametrize("statut,code", [(401, "auth_invalide"), (403, "ip_refusee")])
def test_ecriture_refus_service_traduit(ecr, caplog, statut, code):
    import logging

    def boom(*a, **k):
        raise bibler_pont.BiblerErreur(statut, {"ok": False, "erreur": code})
    ecr.mp.setattr(bibler_pont, "envoyer", boom)
    with caplog.at_level(logging.WARNING, logger="bibler_postes"):
        r = ecr.client.post("/api/bibler/consignes/%s/lever" % ("a1" * 12))
    assert r.status_code == 502
    assert r.get_json()["erreur"] == "bibler_refus_service"
    assert any(code in m and "/lever" in m for m in caplog.messages)


def test_refus_service_corps_vide(env):
    def boom(*a, **k):
        raise bibler_pont.BiblerErreur(401, None)
    env.mp.setattr(bibler_pont, "lire", boom)
    r = env.client.get("/api/bibler/formats")
    assert r.status_code == 502 and r.get_json()["erreur"] == "bibler_refus_service"


def test_droit_insuffisant_cockpit_reste_403(env, ecr):
    ecr.who["publier"] = False
    r = ecr.client.post("/api/bibler/consignes/%s/lever" % ("a1" * 12))
    assert r.status_code == 403 and r.get_json()["erreur"] == "droit_insuffisant"
    ecr.who["voir"] = False
    r = ecr.client.get("/api/bibler/postes")
    assert r.status_code == 403 and r.get_json()["code"] == "droit_insuffisant"


def test_4xx_bibler_journalise(env, ecr, caplog):
    import logging

    def boom_l(*a, **k):
        raise bibler_pont.BiblerErreur(404, {"code": "evenement_inconnu", "detail": "secret-texte"})

    def boom_e(*a, **k):
        raise bibler_pont.BiblerErreur(422, {"ok": False, "erreur": "donnees_personnelles",
                                             "message": "06 12 34 56 78"})
    ecr.mp.setattr(bibler_pont, "lire", boom_l)
    ecr.mp.setattr(bibler_pont, "envoyer", boom_e)
    with caplog.at_level(logging.WARNING, logger="bibler_postes"):
        assert ecr.client.get("/api/bibler/postes?event=X").status_code == 404
        assert ecr.client.patch("/api/bibler/consignes/" + "a1" * 12, json={"version": 1}).status_code == 422
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("evenement_inconnu" in m and "/api/bibler/postes" in m for m in msgs)
    assert any("donnees_personnelles" in m and "/api/bibler/consignes/" in m for m in msgs)
    assert not any("secret-texte" in m or "06 12" in m for m in msgs)
