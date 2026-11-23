"""Acces public et API candidat du test PC Organisation (formation/acces.py,
formation/routes_public.py), contre un MongoDB local jetable et une app Flask
de test avec CSRFProtect actif, comme Cockpit. Test saute sans MongoDB local."""
import os
import re
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask
from flask_wtf.csrf import CSRFProtect
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from formation import acces, banque, store, tentative
from formation.normalise import normaliser_email, normaliser_tel

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "/test-pc-organisation"
GENERIQUE = ("Ces informations ne correspondent à aucune candidature. "
             "Vérifiez l'adresse utilisée dans le questionnaire.")


@pytest.fixture
def db():
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_formation_acces_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    store.ensure_indexes(d)
    store.set_db_for_tests(d)
    _fenetre(d, ouverte=True)
    yield d
    store.set_db_for_tests(None)
    client.drop_database(name)


def _fenetre(db, ouverte=True, reply_to="selection@lemans.org", contact="dpo@lemans.org"):
    now = datetime.now(timezone.utc)
    if ouverte:
        ouv, fer = now - timedelta(hours=1), now + timedelta(hours=1)
    else:
        ouv, fer = now - timedelta(hours=3), now - timedelta(hours=2)
    db["reglages"].update_one({"_id": store.CAMPAGNE}, {"$set": {
        "ouverture": ouv, "fermeture": fer, "reply_to": reply_to, "contact_rgpd": contact}},
        upsert=True)


@pytest.fixture
def app(db):
    from formation import formation_bp
    a = Flask("formation_test", template_folder=os.path.join(RACINE, "templates"),
              static_folder=os.path.join(RACINE, "static"))
    a.config.update(SECRET_KEY="test-secret-formation", WTF_CSRF_ENABLED=True, TESTING=True)
    CSRFProtect(a)
    a.register_blueprint(formation_bp)
    return a


def _candidat(db, email="f.arbia@gmail.com", tel="+33 6 12 34 31 79", amenagement=False):
    tel = normaliser_tel(tel)
    res = db["candidats"].insert_one({"campagne": store.CAMPAGNE, "email": normaliser_email(email),
                                      "tel": tel, "tel4": tel[-4:], "amenagement": amenagement})
    return db["candidats"].find_one({"_id": res.inserted_id})


class Client:
    """Client de test qui lit le jeton CSRF dans la page d'accueil."""

    def __init__(self, app, ip="10.0.0.1"):
        self.c = app.test_client()
        self.ip = ip
        page = self.c.get(BASE, environ_base={"REMOTE_ADDR": ip}).get_data(as_text=True)
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
        assert m, page
        self.jeton = m.group(1)

    def get(self, chemin, **kw):
        return self.c.get(BASE + chemin, environ_base={"REMOTE_ADDR": self.ip}, **kw)

    def post(self, chemin, json=None, csrf=True, **kw):
        h = {"X-CSRFToken": self.jeton} if csrf else {}
        return self.c.post(BASE + chemin, json=json, headers=h,
                           environ_base={"REMOTE_ADDR": self.ip}, **kw)

    def connexion(self, email="f.arbia@gmail.com", tel4="3179"):
        return self.post("/api/connexion", {"email": email, "tel4": tel4})


def _demarre(app, db):
    _candidat(db)
    c = Client(app)
    assert c.connexion().status_code == 200
    r = c.post("/api/commencer", {"accepte": True})
    assert r.status_code == 200, r.get_json()
    return c


# ---------------------------------------------------------------- identification

def test_message_generique_identique(app, db):
    _candidat(db)
    c = Client(app)
    r1 = c.connexion(email="inconnu@exemple.fr", tel4="3179")
    r2 = c.connexion(email="f.arbia@gmail.com", tel4="0000")
    assert r1.status_code == r2.status_code == 401
    assert r1.get_data() == r2.get_data()
    assert r1.get_json() == {"ok": False, "message": GENERIQUE}
    assert "formation_session" not in (r1.headers.get("Set-Cookie") or "")


def test_blocage_apres_5_echecs_email(app, db):
    _candidat(db)
    for i in range(5):
        r = Client(app, ip="10.0.1.%d" % i).connexion(tel4="0000")
        assert r.status_code == 401
    r = Client(app, ip="10.0.2.1").connexion(tel4="1111")  # mauvaise paire, IP neuve
    assert r.status_code == 429
    assert r.get_json() == {"ok": False, "message": GENERIQUE + " Réessayez dans 15 minutes."}
    assert "formation_session" not in (r.headers.get("Set-Cookie") or "")
    # une IP qui a deja echoue reste bloquee, meme avec la bonne paire
    assert Client(app, ip="10.0.1.0").connexion().status_code == 429


def test_email_bloque_par_un_tiers_candidat_passe_depuis_son_ip(app, db):
    """Un tiers qui connait l'e-mail ne peut pas empecher le candidat d'entrer :
    la bonne paire passe depuis une IP sans echec dans la fenetre."""
    _candidat(db)
    for i in range(8):
        Client(app, ip="10.0.4.%d" % i).connexion(tel4="0000")
    r = Client(app, ip="10.0.5.1").connexion()
    assert r.status_code == 200
    assert "formation_session" in (r.headers.get("Set-Cookie") or "")


def test_blocage_apres_5_echecs_ip(app, db):
    _candidat(db)
    c = Client(app, ip="10.0.3.3")
    for i in range(5):
        assert c.connexion(email="x%d@exemple.fr" % i).status_code == 401
    assert c.connexion().status_code == 429
    assert Client(app, ip="10.0.3.4").connexion().status_code == 200


def test_echecs_anciens_ne_bloquent_pas(db):
    now = datetime.now(timezone.utc)
    vieux = now - timedelta(minutes=16)
    for _ in range(5):
        acces.identifier(db, "a@b.fr", "0000", "10.9.9.9", vieux)
    assert acces.est_bloque(db, "a@b.fr", "10.9.9.9", vieux)
    assert not acces.est_bloque(db, "a@b.fr", "10.9.9.9", now)


def test_connexion_email_majuscules_espaces(app, db):
    _candidat(db, email="f.arbia@gmail.com", tel="+33 6 12 34 31 79")
    r = Client(app).connexion(email=" F.Arbia@GMAIL.com ", tel4="3179")
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"ok": True, "etat": {"statut": "non_commence"}}


def test_cookie_httponly_samesite(app, db):
    _candidat(db)
    r = Client(app).connexion()
    cookie = r.headers["Set-Cookie"]
    assert cookie.startswith("formation_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Path=/test-pc-organisation" in cookie
    assert "Max-Age=3600" in cookie
    assert "Secure" not in cookie  # dev


def test_cookie_secure_hors_dev(app, db, monkeypatch):
    _candidat(db)
    monkeypatch.setenv("TITAN_ENV", "prod")
    monkeypatch.setenv("FORMATION_JWT_SECRET", "x" * 40)
    assert "Secure" in Client(app).connexion().headers["Set-Cookie"]


def test_staging_sans_secret_503_sans_jeton(app, db, monkeypatch):
    _candidat(db)
    c = Client(app)
    monkeypatch.setenv("TITAN_ENV", "staging")
    monkeypatch.delenv("FORMATION_JWT_SECRET", raising=False)
    r = c.connexion()
    assert r.status_code == 503
    assert r.get_json() == {"ok": False, "error": "reessayez"}
    assert "formation_session" not in (r.headers.get("Set-Cookie") or "")
    assert db["tentatives"].count_documents({}) == 0


def test_staging_avec_secret_cookie_secure(app, db, monkeypatch):
    _candidat(db)
    c = Client(app)
    monkeypatch.setenv("TITAN_ENV", "staging")
    monkeypatch.setenv("FORMATION_JWT_SECRET", "z" * 40)
    r = c.connexion()
    assert r.status_code == 200
    assert "Secure" in r.headers["Set-Cookie"]


def test_jeton(monkeypatch):
    now = datetime.now(timezone.utc)
    j = acces.emettre_jeton("abc", "sid1", now)
    p = acces.lire_jeton(j)
    assert p["sub"] == "abc" and p["sid"] == "sid1" and p["aud"] == "formation"
    assert acces.lire_jeton(acces.emettre_jeton("abc", "s", now - timedelta(hours=2))) is None
    assert acces.lire_jeton("pas.un.jeton") is None
    assert acces.lire_jeton(None) is None
    monkeypatch.setenv("FORMATION_JWT_SECRET", "y" * 40)
    assert acces.lire_jeton(j) is None  # autre secret


def test_jeton_prod_sans_secret_refuse(monkeypatch):
    monkeypatch.setenv("TITAN_ENV", "prod")
    monkeypatch.delenv("FORMATION_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        acces.emettre_jeton("abc", "s", datetime.now(timezone.utc))


def test_post_sans_csrf_refuse(app, db):
    _candidat(db)
    c = Client(app)
    r = c.post("/api/connexion", {"email": "f.arbia@gmail.com", "tel4": "3179"}, csrf=False)
    assert r.status_code == 400
    assert "formation_session" not in (r.headers.get("Set-Cookie") or "")


def test_api_csrf_jeton_neuf_utilisable(app, db):
    """GET /api/csrf sans session : jeton neuf, no-store, accepte par un POST."""
    _candidat(db)
    c = Client(app)
    r = c.get("/api/csrf")
    assert r.status_code == 200
    assert r.headers.get("Cache-Control") == "no-store"
    jeton = r.get_json()["csrf_token"]
    assert isinstance(jeton, str) and jeton
    c.jeton = "invalide"
    assert c.connexion().status_code == 400
    c.jeton = jeton
    assert c.connexion().status_code == 200


# ---------------------------------------------------------------- fenetre

def test_etat_fenetre():
    now = datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc)
    r = {"reply_to": "a@b.fr", "contact_rgpd": "dpo@b.fr",
         "ouverture": now - timedelta(hours=1), "fermeture": now + timedelta(hours=1)}
    assert acces.etat_fenetre(r, now) == "ouvert"
    assert acces.etat_fenetre(r, now - timedelta(hours=2)) == "avant"
    assert acces.etat_fenetre(r, now + timedelta(hours=2)) == "apres"
    assert acces.etat_fenetre(dict(r, reply_to=" "), now) == "non_configure"
    assert acces.etat_fenetre(dict(r, contact_rgpd=""), now) == "non_configure"


def test_hors_fenetre_refuse_commencer(app, db):
    _candidat(db)
    c = Client(app)
    assert c.connexion().status_code == 200
    _fenetre(db, ouverte=False)
    r = c.post("/api/commencer", {"accepte": True})
    assert r.status_code == 403
    assert r.get_json() == {"ok": False, "fenetre": "apres"}
    # sans tentative, la connexion elle-meme est refusee hors fenetre
    r = Client(app, ip="10.0.0.2").connexion()
    assert r.status_code == 403
    assert r.get_json() == {"ok": False, "fenetre": "apres"}


def test_commencer_exige_accepte(app, db):
    _candidat(db)
    c = Client(app)
    c.connexion()
    for corps in ({}, {"accepte": "true"}, {"accepte": 1}, [True]):
        assert c.post("/api/commencer", corps).status_code == 400
    assert db["tentatives"].count_documents({}) == 0


def test_fenetre_fermee_tentative_en_cours_continue(app, db):
    c = _demarre(app, db)
    assert c.post("/api/epreuve/radio/debut").status_code == 200
    _fenetre(db, ouverte=False)
    assert c.post("/api/epreuve/radio/action", {"t_ms": 100, "type": "prise"}).status_code == 200
    r = c.post("/api/epreuve/radio/fin")
    assert r.status_code == 200
    assert r.get_json()["etat"]["epreuve_courante"] == "appel"
    # reconnexion apres la fermeture : admise, la tentative continue
    c2 = Client(app, ip="10.0.0.9")
    r = c2.connexion()
    assert r.status_code == 200
    assert r.get_json()["etat"]["statut"] == "en_cours"
    assert c2.post("/api/epreuve/appel/debut").status_code == 200


def _illimite(db):
    db["candidats"].update_many({}, {"$set": {"fictif": True, "essais_illimites": True}})


def _demarre_compte_test(app, db):
    _candidat(db)
    _illimite(db)
    c = Client(app)
    c.connexion()
    r = c.post("/api/commencer", {"accepte": True})
    assert r.status_code == 200 and r.get_json()["etat"]["peut_passer"] is True
    return c


def test_compte_test_passe_des_epreuves(app, db):
    c = _demarre_compte_test(app, db)
    # on ne garde que les patrouilles : tout le reste est passe, dans l'ordre
    for e in store.EPREUVES:
        if e == "patrouilles":
            assert c.post("/api/epreuve/%s/debut" % e).status_code == 200
            assert c.post("/api/epreuve/%s/fin" % e).status_code == 200
            continue
        r = c.post("/api/epreuve/%s/passer" % e)
        assert r.status_code == 200, r.get_json()
    t = db["tentatives"].find_one({})
    assert t["statut"] == "terminee"
    assert [t["epreuves"][e]["etat"] for e in store.EPREUVES] == [
        "terminee" if e == "patrouilles" else "passee" for e in store.EPREUVES]
    assert t["epreuves"]["radio"]["drapeaux"] == ["passee"] and "score" not in t["epreuves"]["radio"]
    # epreuves passees hors du score global : seules les patrouilles comptent
    assert tentative.score_global(t, {e: 1 for e in store.EPREUVES}) == t["epreuves"]["patrouilles"]["score"]


def test_compte_test_passer_dans_l_ordre(app, db):
    c = _demarre_compte_test(app, db)
    assert c.post("/api/epreuve/appel/passer").status_code == 409  # pas la suivante
    assert c.post("/api/epreuve/radio/debut").status_code == 200
    assert c.post("/api/epreuve/radio/passer").status_code == 409  # deja commencee
    assert db["tentatives"].find_one({})["epreuves"]["radio"]["etat"] == "en_cours"


def test_compte_test_aller_a_une_epreuve(app, db):
    c = _demarre_compte_test(app, db)
    # droit aux patrouilles : radio, appel, mc passees
    r = c.post("/api/test/aller/patrouilles")
    assert r.status_code == 200, r.get_json()
    etat = r.get_json()["etat"]
    assert etat["epreuve_courante"] == "patrouilles"
    assert [x["etat"] for x in etat["epreuves"][:3]] == ["passee"] * 3
    assert c.post("/api/epreuve/patrouilles/debut").status_code == 200
    assert c.post("/api/epreuve/patrouilles/fin").status_code == 200
    # on y retourne : elle repart de zero, journal vide, meme pendant une epreuve en cours
    assert c.post("/api/epreuve/acces/debut").status_code == 200
    r = c.post("/api/test/aller/patrouilles")
    assert r.get_json()["etat"]["epreuve_courante"] == "patrouilles"
    t = db["tentatives"].find_one({})
    assert t["epreuves"]["patrouilles"] == {"etat": "a_faire", "actions": [], "drapeaux": []}
    assert t["epreuves"]["acces"]["etat"] == "passee"
    # tentative terminee : elle repart aussi
    for e in store.EPREUVES[store.EPREUVES.index("patrouilles"):]:
        if db["tentatives"].find_one({})["epreuves"][e]["etat"] == "a_faire":
            c.post("/api/epreuve/%s/passer" % e)
    assert db["tentatives"].find_one({})["statut"] == "terminee"
    r = c.post("/api/test/aller/radio")
    assert r.status_code == 200 and r.get_json()["etat"]["statut"] == "en_cours"
    assert r.get_json()["etat"]["epreuve_courante"] == "radio"
    assert c.post("/api/test/aller/inconnue").status_code == 409


def test_compte_test_remise_a_zero(app, db):
    c = _demarre_compte_test(app, db)
    c.post("/api/epreuve/radio/passer")
    r = c.post("/api/test/remise-a-zero")
    assert r.status_code == 200, r.get_json()
    etat = r.get_json()["etat"]
    assert etat["statut"] == "en_cours" and etat["epreuve_courante"] == "radio" and etat["peut_passer"] is True
    assert [t["statut"] for t in db["tentatives"].find({}).sort("debut", 1)] == ["reinitialisee", "en_cours"]
    # la meme session continue : on joue la nouvelle tentative sans se reconnecter
    assert c.post("/api/epreuve/radio/debut").status_code == 200


def test_vrai_candidat_ni_aller_ni_remise_a_zero(app, db):
    c = _demarre(app, db)
    for chemin in ("/api/test/aller/patrouilles", "/api/test/remise-a-zero"):
        r = c.post(chemin)
        assert r.status_code == 403 and r.get_json()["error"] == "interdit"
    t = db["tentatives"].find_one({})
    assert t["statut"] == "en_cours" and t["epreuves"]["radio"]["etat"] == "a_faire"
    assert db["tentatives"].count_documents({}) == 1


def test_vrai_candidat_ne_passe_jamais(app, db):
    c = _demarre(app, db)
    assert db["tentatives"].find_one({}).get("sauts") is None
    r = c.post("/api/epreuve/radio/passer")
    assert r.status_code == 403 and r.get_json()["error"] == "interdit"
    # devenu fictif apres avoir commence : la tentative n'a pas le droit, toujours refuse
    _illimite(db)
    assert c.post("/api/epreuve/radio/passer").status_code == 403
    # redevenu vrai candidat en cours de route : refuse aussi
    db["candidats"].update_many({}, {"$set": {"fictif": False}})
    db["tentatives"].update_many({}, {"$set": {"sauts": True}})
    assert c.post("/api/epreuve/radio/passer").status_code == 403
    assert db["tentatives"].find_one({})["epreuves"]["radio"]["etat"] == "a_faire"


def test_fictif_illimite_repart_apres_tentative_terminee(app, db):
    c = _demarre(app, db)
    _illimite(db)
    for e in store.EPREUVES:
        assert c.post("/api/epreuve/%s/debut" % e).status_code == 200
        assert c.post("/api/epreuve/%s/fin" % e).status_code == 200
    r = Client(app, ip="10.0.0.7").connexion()
    assert r.status_code == 200 and r.get_json()["etat"]["statut"] == "non_commence"
    assert [t["statut"] for t in db["tentatives"].find()] == ["reinitialisee"]


def test_fictif_illimite_repart_apres_plus_d_une_heure(app, db):
    # la connexion clot elle-meme la tentative (plus d'une heure) : remise a zero dans la foulee
    _demarre(app, db)
    _illimite(db)
    db["tentatives"].update_many({}, {"$set": {"debut": datetime.now(timezone.utc) - timedelta(hours=2)}})
    r = Client(app, ip="10.0.0.6").connexion()
    assert r.status_code == 200 and r.get_json()["etat"]["statut"] == "non_commence"
    assert [t["statut"] for t in db["tentatives"].find()] == ["reinitialisee"]


def test_vrai_candidat_jamais_illimite(app, db):
    _demarre(app, db)
    db["candidats"].update_many({}, {"$set": {"essais_illimites": True}})  # sans fictif : ignore
    db["tentatives"].update_many({}, {"$set": {"debut": datetime.now(timezone.utc) - timedelta(hours=2)}})
    r = Client(app, ip="10.0.0.5").connexion()
    assert r.get_json()["etat"]["statut"] == "interrompue"
    assert [t["statut"] for t in db["tentatives"].find()] == ["interrompue"]


def test_tentative_terminee_connexion_hors_fenetre(app, db):
    c = _demarre(app, db)
    for e in store.EPREUVES:
        assert c.post("/api/epreuve/%s/debut" % e).status_code == 200
        assert c.post("/api/epreuve/%s/fin" % e).status_code == 200
    _fenetre(db, ouverte=False)
    r = Client(app, ip="10.0.0.8").connexion()
    assert r.status_code == 200
    assert r.get_json()["etat"]["statut"] == "terminee"


def test_non_configure_tant_que_reply_to_vide(app, db):
    _candidat(db)
    _fenetre(db, ouverte=True, reply_to="")
    c = Client(app)
    page = c.get("").get_data(as_text=True)
    assert 'data-fenetre="non_configure"' in page
    r = c.connexion()
    assert r.status_code == 403
    assert r.get_json() == {"ok": False, "fenetre": "non_configure"}


def test_accueil_mention_rgpd(app, db):
    page = Client(app).get("").get_data(as_text=True)
    assert 'data-fenetre="ouvert"' in page
    assert "dpo@lemans.org" in page
    assert "30/01/2027" in page


# ---------------------------------------------------------------- API du test

def _cles_secretes(obj, chemin=""):
    """Chemins des cles de reponse (points, ans, a, ok) presentes dans obj."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("points", "ans", "a", "ok"):
                out.append(chemin + "." + k)
            out += _cles_secretes(v, chemin + "." + k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _cles_secretes(v, "%s[%d]" % (chemin, i))
    return out


def test_enonce_api_sans_reponses(app, db):
    c = _demarre(app, db)
    enonces = {}
    for e in store.EPREUVES:
        r = c.post("/api/epreuve/%s/debut" % e)
        assert r.status_code == 200
        enonces[e] = r.get_json()["enonce"]
        assert c.post("/api/epreuve/%s/fin" % e).status_code == 200
    assert enonces["appel"]["appels"] and enonces["mc"]["fiches"]
    for e in ("logique", "situations"):
        assert enonces[e]["questions"]
    for e in ("appel", "mc", "logique", "situations"):
        assert _cles_secretes(enonces[e]) == [], e


def test_ordre_409(app, db):
    c = _demarre(app, db)
    r = c.post("/api/epreuve/appel/debut")
    assert r.status_code == 409
    assert r.get_json() == {"ok": False, "error": "ordre"}


def test_debut_rejoue_apres_reponse_perdue(app, db):
    """Second /debut du meme onglet sans action : meme enonce et temps ecoule
    serveur ; apres une action : 409 ordre."""
    c = _demarre(app, db)
    r1 = c.post("/api/epreuve/radio/debut")
    assert r1.status_code == 200 and r1.get_json()["ecoule_ms"] == 0
    r2 = c.post("/api/epreuve/radio/debut")
    assert r2.status_code == 200
    assert r2.get_json()["enonce"] == r1.get_json()["enonce"]
    assert r2.get_json()["ecoule_ms"] >= 0
    assert c.post("/api/epreuve/radio/action", {"type": "prise", "msg": "r01", "t_ms": 10}).status_code == 200
    r3 = c.post("/api/epreuve/radio/debut")
    assert r3.status_code == 409 and r3.get_json()["error"] == "ordre"


def _sid_du_cookie(reponse):
    m = re.search(r"formation_session=([^;]+)", reponse.headers.get("Set-Cookie", ""))
    assert m, reponse.headers
    return acces.lire_jeton(m.group(1))


def test_cookie_glissant_commencer_et_debut(app, db):
    """/commencer et chaque /debut reemettent le cookie (meme sid, 1 h neuve) :
    un candidat lent ne perd pas sa session en plein test."""
    _candidat(db)
    c = Client(app)
    p0 = _sid_du_cookie(c.connexion())
    r = c.post("/api/commencer", {"accepte": True})
    p1 = _sid_du_cookie(r)
    assert p1["sid"] == p0["sid"] and p1["exp"] >= p0["exp"]
    assert "Max-Age=3600" in r.headers["Set-Cookie"]
    r = c.post("/api/epreuve/radio/debut")
    assert _sid_du_cookie(r)["sid"] == p0["sid"]
    assert c.get("/api/etat").status_code == 200


def test_action_trop_grande_413_et_non_objet_400(app, db):
    c = _demarre(app, db)
    c.post("/api/epreuve/radio/debut")
    r = c.post("/api/epreuve/radio/action", {"t_ms": 1, "type": "x", "pad": "a" * 17000})
    assert r.status_code == 413
    assert c.post("/api/epreuve/radio/action", [1, 2]).status_code == 400
    r = c.c.post(BASE + "/api/epreuve/radio/action", data="pas du json",
                 headers={"X-CSRFToken": c.jeton, "Content-Type": "application/json"})
    assert r.status_code == 400
    t = db["tentatives"].find_one({})
    assert t["epreuves"]["radio"]["actions"] == []


def test_session_remplacee_401(app, db):
    a = _demarre(app, db)
    assert a.get("/api/etat").status_code == 200
    b = Client(app, ip="10.0.0.5")
    assert b.connexion().status_code == 200
    for r in (a.get("/api/etat"), a.post("/api/epreuve/radio/debut")):
        assert r.status_code == 401
        assert r.get_json() == {"ok": False, "error": "session"}
    assert b.get("/api/etat").status_code == 200


def test_rechargement_page_test_nouvelle_session(app, db):
    a = _demarre(app, db)
    a.post("/api/epreuve/radio/debut")
    vieux = a.c.get_cookie("formation_session", path=BASE).value
    r = a.get("/test")
    assert r.status_code == 200
    assert a.c.get_cookie("formation_session", path=BASE).value != vieux
    t = db["tentatives"].find_one({})
    assert t["epreuves"]["radio"]["etat"] == "abandonnee" and t["reprises"] == 1
    etat = a.get("/api/etat").get_json()["etat"]
    assert etat["epreuve_courante"] == "appel"
    # un autre onglet garde l'ancien jeton : 401
    autre = app.test_client()
    autre.set_cookie("formation_session", vieux, path=BASE)
    r = autre.get(BASE + "/api/etat")
    assert r.status_code == 401


def test_page_test_venue_d_un_autre_site_ne_change_pas_la_session(app, db):
    """Un lien d'un site tiers vers /test ne clot pas l'epreuve en cours."""
    a = _demarre(app, db)
    a.post("/api/epreuve/radio/debut")
    vieux = a.c.get_cookie("formation_session", path=BASE).value
    r = a.get("/test", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 302 and r.headers["Location"].endswith(BASE)
    assert a.c.get_cookie("formation_session", path=BASE).value == vieux
    t = db["tentatives"].find_one({})
    assert t["epreuves"]["radio"]["etat"] == "en_cours" and t.get("reprises", 0) == 0
    # navigation normale (meme site, ou adresse tapee) : comportement inchange
    assert a.get("/test", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_etat_n_appelle_pas_connexion(app, db):
    a = _demarre(app, db)
    a.post("/api/epreuve/radio/debut")
    a.get("/api/etat")
    a.get("/api/etat")
    t = db["tentatives"].find_one({})
    assert t["epreuves"]["radio"]["etat"] == "en_cours" and t["reprises"] == 0


@pytest.mark.parametrize("entete", [("Sec-Purpose", "prefetch"), ("Sec-Purpose", "prefetch;prerender"),
                                    ("Purpose", "prefetch")])
def test_prechargement_ne_touche_pas_la_session(app, db, entete):
    a = _demarre(app, db)
    a.post("/api/epreuve/radio/debut")
    vieux = a.c.get_cookie("formation_session", path=BASE).value
    r = a.get("/test", headers={entete[0]: entete[1]})
    assert r.status_code == 204
    assert r.headers.get("Cache-Control") == "no-store"
    assert "formation_session" not in (r.headers.get("Set-Cookie") or "")
    assert a.c.get_cookie("formation_session", path=BASE).value == vieux
    t = db["tentatives"].find_one({})
    assert t["epreuves"]["radio"]["etat"] == "en_cours" and t["reprises"] == 0
    assert a.get("/api/etat").status_code == 200


def test_valueerror_inattendue_409_requete(app, db, monkeypatch):
    from formation import tentative

    def boum(*a, **k):
        raise ValueError("epreuve inconnue : details internes")
    c = _demarre(app, db)
    monkeypatch.setattr(tentative, "debut_epreuve", boum)
    r = c.post("/api/epreuve/radio/debut")
    assert r.status_code == 409
    assert r.get_json() == {"ok": False, "error": "requete"}


def test_plafond_journalier_email_sans_contournement(db):
    """Au-dela de 20 echecs sur 24 h pour un e-mail, plus rien ne passe, meme la
    bonne paire depuis une IP neuve (essais etales pour eviter le seuil 15 min)."""
    _candidat(db)
    t0 = datetime.now(timezone.utc) - timedelta(hours=20)
    for i in range(acces.PLAFOND_EMAIL_JOUR):
        t = t0 + timedelta(minutes=16 * (i // 5))
        acces.essayer(db, "f.arbia@gmail.com", "0000", "10.8.%d.1" % i, t)
    now = datetime.now(timezone.utc)
    assert acces.essayer(db, "f.arbia@gmail.com", "3179", "10.9.0.1", now) == ("bloque", None)
    # 24 h plus tard, la bonne paire passe a nouveau
    plus_tard = t0 + timedelta(hours=25)
    assert acces.essayer(db, "f.arbia@gmail.com", "3179", "10.9.0.2", plus_tard)[0] == "ok"


def test_ipv6_regroupees_par_64(db):
    """Les adresses IPv6 d'un meme /64 comptent comme une seule IP."""
    _candidat(db)
    now = datetime.now(timezone.utc)
    for i in range(5):
        acces.essayer(db, "x%d@exemple.fr" % i, "0000", "2001:db8:1:2::%x" % (i + 1), now)
    assert acces.essayer(db, "f.arbia@gmail.com", "3179", "2001:db8:1:2::ffff", now) == ("bloque", None)
    assert acces.essayer(db, "f.arbia@gmail.com", "3179", "2001:db8:1:3::1", now)[0] == "ok"


def test_ttl_essais_passe_a_24h(db):
    """Une base creee avec l'ancien TTL de 15 min est mise a 24 h."""
    db["essais_connexion"].drop_indexes()
    db["essais_connexion"].create_index("t", expireAfterSeconds=900, name="ttl_essais")
    store.ensure_indexes(db)
    info = db["essais_connexion"].index_information()
    ttl = [v for v in info.values() if "expireAfterSeconds" in v]
    assert len(ttl) == 1 and ttl[0]["expireAfterSeconds"] == 86400


def test_essais_simultanes_comptes_avant_verification(db):
    """L'essai est enregistre avant le comptage : un 6e essai depuis la meme IP
    est bloque meme avec la bonne paire, une mauvaise paire depuis une IP neuve
    aussi ; la bonne paire depuis une IP neuve passe. Un succes ne laisse pas
    d'echec derriere lui."""
    _candidat(db)
    now = datetime.now(timezone.utc)
    assert acces.essayer(db, "f.arbia@gmail.com", "3179", "10.7.7.7", now)[0] == "ok"
    assert db["essais_connexion"].count_documents({}) == 0
    for _ in range(5):
        assert acces.essayer(db, "f.arbia@gmail.com", "0000", "10.7.7.7", now)[0] == "refuse"
    assert acces.essayer(db, "f.arbia@gmail.com", "3179", "10.7.7.7", now) == ("bloque", None)
    assert acces.essayer(db, "f.arbia@gmail.com", "0000", "10.7.7.8", now) == ("bloque", None)
    assert acces.essayer(db, "f.arbia@gmail.com", "3179", "10.7.7.9", now)[0] == "ok"


def test_page_test_sans_session_redirige(app, db):
    r = Client(app).get("/test")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(BASE)


def test_api_sans_session_401(app, db):
    c = Client(app)
    r = c.get("/api/etat")
    assert r.status_code == 401
    assert r.get_json() == {"ok": False, "error": "session"}


def test_entrainement(app, db):
    _candidat(db)
    c = Client(app)
    assert c.get("/api/entrainement/radio").status_code == 401
    c.connexion()
    r = c.get("/api/entrainement/radio")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert r.get_json()["contenu"]
    r = c.get("/api/entrainement/patrouilles")
    assert r.status_code == 200
    ent = r.get_json()["contenu"]
    # plan et moyens de l'epreuve, mais seulement l'incident d'entrainement
    for k in ("RW", "CL", "PASS", "TRAV", "MAPZONES", "UNITS"):
        assert ent[k] == banque.charger()["patrouilles"][k]
    assert [i["id"] for i in ent["INCIDENTS"]] == ["E1"]
    # regle d'acces lue avant l'epreuve : couleurs et heure, jamais les badges (ni `ok`)
    r = c.get("/api/entrainement/acces")
    assert r.status_code == 200
    a = banque.charger()["acces"]
    assert r.get_json()["contenu"] == {"couleurs": a["couleurs"], "heure": a["heure"]}
    assert "badges" not in r.get_data(as_text=True) and a["badges"][0]["num"] not in r.get_data(as_text=True)
    assert c.get("/api/entrainement/logique").status_code == 404
    assert db["tentatives"].count_documents({}) == 0


def test_entetes_no_store(app, db):
    _candidat(db)
    c = Client(app)
    reponses = [c.get(""), c.get("/api/etat"), c.connexion(tel4="0000"), c.get("/test")]
    c.connexion()
    reponses += [c.get("/api/etat"), c.get("/test")]
    for r in reponses:
        assert r.headers.get("Cache-Control") == "no-store", r.status_code
        assert r.headers.get("Referrer-Policy") == "no-referrer"
