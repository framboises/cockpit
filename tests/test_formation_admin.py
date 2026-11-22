"""Administration du module Formation (formation/routes_admin.py) contre un
MongoDB local jetable : droits, candidats, detail, amenagement, reinitialisation,
statistiques, exports, reglages, import. smtplib.SMTP est remplace par un faux :
aucun e-mail reel. Saute sans MongoDB local."""
import csv
import io
import os
import sys
import time
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from flask import Flask, request
from flask_wtf.csrf import CSRFProtect, generate_csrf
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from formation import store

EN_TETE = (["forms_id", "nom", "prenom", "email", "statut", "debut", "duree_s"]
           + store.EPREUVES + ["global", "amenagement", "reprises", "drapeaux"])


@pytest.fixture(autouse=True)
def faux_smtp(monkeypatch):
    import smtplib

    class Refus:
        def __init__(self, *a, **k):
            raise AssertionError("aucun e-mail reel dans les tests")
    monkeypatch.setattr(smtplib, "SMTP", Refus)


def _mongo():
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    return client


@pytest.fixture
def db():
    client = _mongo()
    name = "titan_test_formation_admin_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    store.ensure_indexes(d)
    store.set_db_for_tests(d)
    yield d
    store.set_db_for_tests(None)
    client.drop_database(name)


@pytest.fixture
def env(db, monkeypatch):
    """Faux module `app` (modele test_declarations.py) : role_required refuse en
    403 si le role du test est insuffisant, sinon pose l'utilisateur courant."""
    who = {"role": "admin"}
    niveaux = {"user": 1, "manager": 2, "admin": 3}
    fake = types.ModuleType("app")

    def role_required(role):
        def deco(f):
            def w(*a, **k):
                if niveaux.get(who["role"], 0) < niveaux[role]:
                    return "interdit", 403
                request.user_payload = {"email": "doa@aco.fr"}
                return f(*a, **k)
            return w
        return deco
    fake.role_required = role_required
    monkeypatch.setitem(sys.modules, "app", fake)
    from formation import formation_admin_bp
    a = Flask("formation_admin_test")
    a.config.update(SECRET_KEY="t", WTF_CSRF_ENABLED=True, TESTING=True)
    CSRFProtect(a)
    a.register_blueprint(formation_admin_bp)

    @a.get("/jeton")
    def jeton():
        return generate_csrf()
    c = a.test_client()
    c.jeton = c.get("/jeton").get_data(as_text=True)
    return types.SimpleNamespace(client=c, db=db, who=who, app=a)


def _post(c, chemin, data=None, methode="post"):
    return getattr(c, methode)(chemin, json=data or {}, headers={"X-CSRFToken": c.jeton})


def _cand(db, email, nom="Martin", prenom="Alice", forms_id=1, fictif=False, amenagement=False):
    r = db["candidats"].insert_one({
        "campagne": store.CAMPAGNE, "email": email, "nom": nom, "prenom": prenom,
        "forms_id": forms_id, "tel": "0612345678", "tel4": "5678",
        "fictif": fictif, "amenagement": amenagement})
    return str(r.inserted_id)


def _tentative(db, cid, statut="terminee", scores=None, debut=None, duree_s=1200, reprises=0,
               drapeaux=None):
    debut = debut or datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)
    epreuves = {}
    for e in store.EPREUVES:
        if scores and e in scores:
            epreuves[e] = {"etat": "terminee", "score": scores[e], "actions": [],
                           "drapeaux": list((drapeaux or {}).get(e, [])), "detail": {},
                           "enonce": {"questions": []}}
        else:
            epreuves[e] = {"etat": "a_faire", "actions": [], "drapeaux": []}
    doc = {"campagne": store.CAMPAGNE, "candidat_id": cid, "statut": statut, "debut": debut,
           "fin": debut + timedelta(seconds=duree_s) if statut != "en_cours" else None,
           "coef": 1.0, "seed": 1, "sid": "secret-sid", "reprises": reprises,
           "reprises_trace": [], "epreuves": epreuves}
    return db["tentatives"].insert_one(doc).inserted_id


TOUS = {e: 50 for e in store.EPREUVES}


# --- Droits -----------------------------------------------------------------

def test_admin_requiert_role(env):
    env.who["role"] = "manager"
    regles = [r for r in env.app.url_map.iter_rules() if r.endpoint.startswith("formation_admin.")]
    assert len(regles) >= 20
    for r in regles:
        chemin = r.rule.replace("<candidat_id>", str(ObjectId()))
        for m in r.methods - {"HEAD", "OPTIONS"}:
            rep = _post(env.client, chemin, methode=m.lower()) if m != "GET" else env.client.get(chemin)
            assert rep.status_code == 403, (m, chemin, rep.status_code)


def test_admin_delegue_par_la_page_formation():
    """Vraie application : un compte cockpit `user` passe sur /api/formation/admin
    quand un de ses groupes lui accorde la page d'administration `formation`,
    et reste refuse sans cette page."""
    import jwt
    from pymongo.collection import Collection
    from titan_shared.revocation import cache_par_defaut
    import app as A
    client = _mongo()
    name = "titan_test_formation_admin_app_%d" % os.getpid()
    client.drop_database(name)
    mdb = client[name]
    fdb = client[name + "_f"]
    store.ensure_indexes(fdb)
    store.set_db_for_tests(fdb)
    sauve = {}
    try:
        for attr, val in list(vars(A).items()):
            if isinstance(val, Collection) and val.database.name != name:
                sauve[attr] = val
                setattr(A, attr, mdb[val.name])
        sauve["db"], sauve["client"] = A.db, A.client
        sauve["CODING"], sauve["JWT_SECRET"] = A.CODING, A.JWT_SECRET
        A.db, A.client, A.CODING = mdb, None, False
        A.JWT_SECRET = "secret-hs256-de-test-" + "x" * 20
        A._user_name_cache.clear()
        cache_par_defaut.vider()
        uid = ObjectId()
        email = "%s@test" % uid
        mdb["users"].insert_one({"_id": uid, "email": email, "actif": True})
        gid = mdb["cockpit_groups"].insert_one({"name": "selection", "admin_pages": ["formation"]}).inserted_id
        maintenant = int(time.time())
        jeton = jwt.encode({
            "user_id": str(uid), "email": email, "roles_by_app": {"cockpit": "user"},
            "global_roles": [], "iss": "titan-home", "jti": str(uuid.uuid4()), "sid": "b" * 32,
            "iat": maintenant - 60, "exp": maintenant + 3600, "type_compte": "domaine",
            "firstname": "Sel", "lastname": "Ection"}, A.JWT_SECRET, algorithm="HS256")
        c = A.app.test_client()
        c.set_cookie("access_token", jeton)
        assert c.get("/api/formation/admin/candidats").status_code == 302
        mdb["cockpit_user_groups"].insert_one({"user_id": uid, "groups": [gid]})
        rep = c.get("/api/formation/admin/candidats")
        assert rep.status_code == 200 and rep.get_json()["ok"] is True
        page = c.get("/admin/formation")
        assert page.status_code == 200 and b"formation/admin.js" in page.data
    finally:
        for attr, val in sauve.items():
            setattr(A, attr, val)
        cache_par_defaut.vider()
        store.set_db_for_tests(None)
        client.drop_database(name)
        client.drop_database(name + "_f")


# --- Candidats et detail -------------------------------------------------------

def test_statut_non_commence_sans_tentative(env):
    cid = _cand(env.db, "a@x.fr")
    _cand(env.db, "fictif@x.fr", fictif=True, forms_id=9)
    lignes = env.client.get("/api/formation/admin/candidats").get_json()["candidats"]
    assert [l["email"] for l in lignes] == ["a@x.fr"]
    l = lignes[0]
    assert l["id"] == cid and l["statut"] == "non_commence" and l["global"] is None
    assert l["scores"] == {e: None for e in store.EPREUVES} and l["reprises"] == 0
    assert l["debut"] is None and l["duree_s"] is None and l["dernier_envoi"] is None
    tous = env.client.get("/api/formation/admin/candidats?fictifs=1").get_json()["candidats"]
    assert len(tous) == 2


def test_candidats_filtres_scores_drapeaux(env):
    a = _cand(env.db, "a@x.fr", nom="Dupont", forms_id=1)
    b = _cand(env.db, "b@x.fr", nom="Leroy", forms_id=2)
    _tentative(env.db, a, "terminee", scores=TOUS, drapeaux={"radio": ["incoherente"]})
    _tentative(env.db, b, "interrompue", scores={"radio": 80}, reprises=1)
    env.db["envois"].insert_one({"campagne": store.CAMPAGNE, "candidat_id": a, "type": "invitation",
                                 "statut": "envoye", "cree_le": datetime.now(timezone.utc)})
    lignes = {l["email"]: l for l in env.client.get("/api/formation/admin/candidats").get_json()["candidats"]}
    assert lignes["a@x.fr"]["global"] == 50 and lignes["a@x.fr"]["duree_s"] == 1200
    assert lignes["a@x.fr"]["drapeaux"] == ["radio:incoherente"]
    assert lignes["a@x.fr"]["dernier_envoi"]["type"] == "invitation"
    # interrompue : epreuves non jouees comptees 0 (80 / 7)
    assert lignes["b@x.fr"]["global"] == pytest.approx(80 / 7, abs=0.1)
    assert lignes["b@x.fr"]["reprises"] == 1 and lignes["b@x.fr"]["scores"]["appel"] is None
    r = env.client.get("/api/formation/admin/candidats?statut=interrompue").get_json()["candidats"]
    assert [l["email"] for l in r] == ["b@x.fr"]
    r = env.client.get("/api/formation/admin/candidats?q=dupo").get_json()["candidats"]
    assert [l["email"] for l in r] == ["a@x.fr"]
    assert env.client.get("/api/formation/admin/candidats?statut=x").status_code == 400


def test_detail_tentative_et_historique(env):
    a = _cand(env.db, "a@x.fr")
    _tentative(env.db, a, "reinitialisee", scores={"radio": 10})
    _tentative(env.db, a, "terminee", scores=TOUS)
    d = env.client.get("/api/formation/admin/tentative/" + a).get_json()
    assert d["ok"] and d["candidat"]["email"] == "a@x.fr"
    assert d["tentative"]["statut"] == "terminee" and "sid" not in d["tentative"]
    assert d["tentative"]["epreuves"]["radio"]["enonce"] == {"questions": []}
    assert [h["statut"] for h in d["historique"]] == ["reinitialisee"]
    assert d["global"] == 50
    assert "a1s1c1" in d["corrige"]["appel"]
    assert env.client.get("/api/formation/admin/tentative/" + str(ObjectId())).status_code == 404
    assert env.client.get("/api/formation/admin/tentative/pasunid").status_code == 404


# --- Amenagement et reinitialisation ---------------------------------------------

def test_amenagement_journalise(env):
    a = _cand(env.db, "a@x.fr")
    url = "/api/formation/admin/candidat/%s/amenagement" % a
    r = _post(env.client, url, {"actif": True})
    assert r.status_code == 200 and r.get_json()["amenagement"] is True
    assert r.get_json()["tentative_commencee"] is False
    assert env.db["candidats"].find_one({"_id": ObjectId(a)})["amenagement"] is True
    j = env.db["journal_admin"].find_one({"action": "amenagement"})
    assert j["auteur"] == "doa@aco.fr" and j["details"] == {"candidat_id": a, "avant": False, "apres": True}
    assert _post(env.client, url, {"actif": "oui"}).status_code == 400
    _tentative(env.db, a, "en_cours", scores={"radio": 1})
    r = _post(env.client, url, {"actif": False}).get_json()
    assert r["tentative_commencee"] is True
    assert _post(env.client, "/api/formation/admin/candidat/%s/amenagement" % ObjectId(),
                 {"actif": True}).status_code == 404
    r = env.client.post(url, json={"actif": True})  # sans CSRF
    assert r.status_code == 400


def test_reinitialiser_sans_motif_400(env):
    a = _cand(env.db, "a@x.fr")
    _tentative(env.db, a, "interrompue", scores={"radio": 1})
    url = "/api/formation/admin/candidat/%s/reinitialiser" % a
    for motif in (None, "", "   ", 12):
        r = _post(env.client, url, {"motif": motif})
        assert r.status_code == 400 and r.get_json()["error"] == "motif_requis"
    assert env.db["tentatives"].find_one()["statut"] == "interrompue"
    r = _post(env.client, url, {"motif": "coupure reseau signalee par e-mail"})
    assert r.status_code == 200
    t = env.db["tentatives"].find_one()
    assert t["statut"] == "reinitialisee" and t["reinitialisation"]["auteur"] == "doa@aco.fr"
    assert _post(env.client, url, {"motif": "encore"}).status_code == 404
    l = env.client.get("/api/formation/admin/candidats").get_json()["candidats"][0]
    assert l["statut"] == "non_commence"


# --- Statistiques et exports --------------------------------------------------------

def test_stats_compte_les_statuts(env):
    ids = [_cand(env.db, "c%d@x.fr" % i, forms_id=i) for i in range(5)]
    f = _cand(env.db, "fictif@x.fr", fictif=True, forms_id=99)
    _tentative(env.db, ids[0], "terminee", scores=dict(TOUS, radio=100), duree_s=1000)
    _tentative(env.db, ids[1], "terminee", scores=dict(TOUS, radio=5), duree_s=2000)
    _tentative(env.db, ids[2], "interrompue", scores={"radio": 42})
    _tentative(env.db, ids[3], "en_cours", scores={"radio": 42})
    _tentative(env.db, f, "terminee", scores=TOUS)
    env.db["envois"].insert_many([
        {"campagne": store.CAMPAGNE, "candidat_id": ids[0], "type": "invitation", "statut": "envoye"},
        {"campagne": store.CAMPAGNE, "candidat_id": ids[1], "type": "invitation", "statut": "echec"},
        {"campagne": store.CAMPAGNE, "candidat_id": f, "type": "invitation", "statut": "envoye"}])
    s = env.client.get("/api/formation/admin/stats").get_json()
    assert (s["inscrits"], s["invites"], s["commences"], s["termines"], s["interrompus"], s["en_cours"]) \
        == (5, 1, 4, 2, 1, 1)
    assert s["temps_moyen_s"] == 1500
    d = s["distribution"]["radio"]
    assert len(d) == 10 and d[9] == 1 and d[0] == 1 and d[4] == 2 and sum(d) == 4
    assert sum(s["distribution"]["appel"]) == 2


def test_export_csv_colonnes(env):
    a = _cand(env.db, "a@x.fr", nom="=HYPERLINK(1)", forms_id=17)
    _cand(env.db, "fictif@x.fr", fictif=True, forms_id=99)
    _tentative(env.db, a, "terminee", scores=TOUS, drapeaux={"radio": ["incoherente"]})
    r = env.client.get("/api/formation/admin/export.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["Content-Type"]
    assert "attachment" in r.headers["Content-Disposition"]
    brut = r.get_data()
    assert brut.startswith(b"\xef\xbb\xbf")
    lignes = list(csv.reader(io.StringIO(brut.decode("utf-8-sig")), delimiter=";"))
    assert lignes[0] == EN_TETE and len(lignes) == 2
    ligne = dict(zip(EN_TETE, lignes[1]))
    assert ligne["forms_id"] == "17" and ligne["email"] == "a@x.fr" and ligne["statut"] == "terminee"
    assert ligne["nom"] == "'=HYPERLINK(1)"  # pas de formule a l'ouverture dans Excel
    assert ligne["radio"] == "50" and ligne["global"] == "50" and "radio:incoherente" in ligne["drapeaux"]
    assert ligne["debut"] == "2026-12-01 11:00"  # heure de Paris
    b = _cand(env.db, "b@x.fr", nom="Zola", forms_id=18)
    _tentative(env.db, b, "interrompue", scores={"radio": 80})
    lignes = list(csv.reader(io.StringIO(env.client.get("/api/formation/admin/export.csv")
                                         .get_data().decode("utf-8-sig")), delimiter=";"))
    assert dict(zip(EN_TETE, lignes[2]))["global"] == "11,4"  # virgule decimale (Excel francais)


def test_export_xlsx_lisible(env):
    import openpyxl
    a = _cand(env.db, "a@x.fr", nom="=1+1", forms_id=17)
    _cand(env.db, "b@x.fr", forms_id=18)
    _cand(env.db, "fictif@x.fr", fictif=True, forms_id=99)
    _tentative(env.db, a, "terminee", scores=TOUS)
    r = env.client.get("/api/formation/admin/export.xlsx")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["Content-Type"]
    ws = openpyxl.load_workbook(io.BytesIO(r.get_data())).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    assert list(rows[0]) == EN_TETE and len(rows) == 3
    par_id = {row[0]: dict(zip(EN_TETE, row)) for row in rows[1:]}
    assert set(par_id) == {17, 18}
    assert par_id[17]["nom"] == "=1+1" and par_id[17]["radio"] == 50
    assert ws.cell(row=2 if rows[1][0] == 17 else 3, column=2).data_type == "s"
    assert par_id[18]["statut"] == "non_commence" and par_id[18]["global"] is None


# --- Reglages ----------------------------------------------------------------------

def test_reglages_get_put_valide_et_journalise(env):
    url = "/api/formation/admin/reglages"
    g = env.client.get(url).get_json()
    assert g["reglages"]["coef_amenagement"] == 1.33 and "gabarit" not in g["reglages"]
    assert g["reglages"]["ouverture_paris"] == "2026-11-30T08:00"
    r = _post(env.client, url, {"ouverture": "2026-11-29T09:00", "fermeture": "2026-12-03T20:00",
                                "coef_amenagement": 1.5, "reply_to": "selection@lemans.org",
                                "debit_envoi_s": 3}, "put")
    assert r.status_code == 200, r.get_json()
    reg = store.reglages(env.db)
    assert reg["ouverture"] == datetime(2026, 11, 29, 8, 0, tzinfo=timezone.utc)
    assert reg["coef_amenagement"] == 1.5 and reg["debit_envoi_s"] == 3.0
    j = env.db["journal_admin"].find_one({"action": "reglages"})
    assert j["details"]["avant"]["coef_amenagement"] == 1.33
    assert j["details"]["apres"]["coef_amenagement"] == 1.5
    assert j["details"]["avant"]["reply_to"] == "" and j["details"]["apres"]["reply_to"] == "selection@lemans.org"
    mauvais = [
        {"ouverture": "pas une date"},
        {"fermeture": "2026-11-28T10:00"},  # avant l'ouverture enregistree
        {"coef_amenagement": 0.9}, {"coef_amenagement": 3.5}, {"coef_amenagement": True},
        {"coef_amenagement": "1.2"},
        {"ponderations": dict({e: 1 for e in store.EPREUVES}, radio=-1)},
        {"ponderations": {"radio": 1}},
        {"ponderations": dict({e: 1 for e in store.EPREUVES}, radio="2")},
        {"ponderations": {e: 0 for e in store.EPREUVES}},
        {"reply_to": "pas une adresse"}, {"contact_rgpd": "a@b"},
        {"debit_envoi_s": 0.2}, {"expediteur_nom": "a\nBcc: x@y.fr"},
        {"gabarit": {"objet": "x", "corps": "y"}}, {"campagne_arretee": True},
    ]
    for m in mauvais:
        r = _post(env.client, url, m, "put")
        assert r.status_code == 400, (m, r.get_json())
    assert store.reglages(env.db)["coef_amenagement"] == 1.5
    r = _post(env.client, url, {"ponderations": dict({e: 1 for e in store.EPREUVES}, radio=2.5),
                                "reply_to": "", "contact_rgpd": "dpo@lemans.org"}, "put")
    assert r.status_code == 200 and store.reglages(env.db)["ponderations"]["radio"] == 2.5


def test_rappel_de_purge(env, monkeypatch):
    from formation import routes_admin
    monkeypatch.setattr(routes_admin, "_maintenant", lambda: datetime(2027, 1, 22, 22, 0, tzinfo=timezone.utc))
    p = env.client.get("/api/formation/admin/etat").get_json()["purge"]
    assert p == {"date": "2027-01-30", "rappel": False}
    monkeypatch.setattr(routes_admin, "_maintenant", lambda: datetime(2027, 1, 22, 23, 30, tzinfo=timezone.utc))
    assert env.client.get("/api/formation/admin/etat").get_json()["purge"]["rappel"] is True


# --- Import, apercu du gabarit -----------------------------------------------------

def test_import_fichier_illisible_400(env):
    r = env.client.post("/api/formation/admin/import", data={"fichier": (io.BytesIO(b"pas un xlsx"), "x.xlsx")},
                        headers={"X-CSRFToken": env.client.jeton}, content_type="multipart/form-data")
    assert r.status_code == 400 and r.get_json()["error"] == "fichier_illisible"


def test_import_erreur_inattendue_500(env, monkeypatch):
    import formation.importer as imp

    def boum(*a, **k):
        raise RuntimeError("panne base")
    monkeypatch.setattr(imp, "importer", boum)
    r = env.client.post("/api/formation/admin/import", data={"fichier": (io.BytesIO(b"x"), "x.xlsx")},
                        headers={"X-CSRFToken": env.client.jeton}, content_type="multipart/form-data")
    assert r.status_code == 500 and "panne" not in r.get_data(as_text=True)


def test_apercu_gabarit(env):
    r = _post(env.client, "/api/formation/admin/gabarit/apercu",
              {"objet": "Test {prenom}", "corps": "Bonjour {prenom}, {lien}"})
    d = r.get_json()
    assert r.status_code == 200 and d["objet"] == "Test Camille"
    assert d["texte"].startswith("Bonjour Camille, http") and "<a href=" in d["html"]
    assert _post(env.client, "/api/formation/admin/gabarit/apercu", {"objet": 1}).status_code == 400
