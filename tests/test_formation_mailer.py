"""Campagne d'e-mails du module Formation (formation/mailer.py). smtplib.SMTP est
toujours remplace par un faux : aucun message reel n'est envoye. Saute sans MongoDB local."""
import email
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header

import pytest
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from formation import mailer, store, tentative


class FauxSMTP:
    envoyes = []
    echec_pour = set()

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, message, from_addr=None, to_addrs=None):
        import smtplib
        if message["To"] in FauxSMTP.echec_pour:
            raise smtplib.SMTPException("refuse")
        FauxSMTP.envoyes.append((message, from_addr, list(to_addrs)))


@pytest.fixture(autouse=True)
def faux_smtp(monkeypatch):
    import smtplib
    FauxSMTP.envoyes = []
    FauxSMTP.echec_pour = set()
    monkeypatch.setattr(smtplib, "SMTP", FauxSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.invalide.test")
    monkeypatch.setenv("SMTP_FROM", "safe@lemans.org")
    monkeypatch.delenv("FORMATION_PUBLIC_URL", raising=False)


@pytest.fixture
def db():
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_formation_mailer_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    store.ensure_indexes(d)
    store.set_db_for_tests(d)
    d["reglages"].update_one({"_id": store.CAMPAGNE}, {"$set": {
        "reply_to": "selection@lemans.org", "contact_rgpd": "dpo@lemans.org",
        "debit_envoi_s": 0}}, upsert=True)
    yield d
    store.set_db_for_tests(None)
    client.drop_database(name)


def _cand(db, email_, prenom="Alice", fictif=False):
    r = db["candidats"].insert_one({"campagne": store.CAMPAGNE, "email": email_, "prenom": prenom,
                                    "fictif": fictif})
    return str(r.inserted_id)


def _now():
    return datetime.now(timezone.utc)


def _fil():
    return mailer.FilEnvoi(sleep=lambda s: None)


def test_rendre_variables(db):
    reg = store.reglages(db)
    objet, texte, html = mailer.rendre(reg["gabarit"], {"prenom": "Alice"}, reg)
    assert "mercredi 2 décembre à 23 h 59" in objet
    assert "Bonjour Alice," in texte
    assert "https://cockpit.lemans.org/test-pc-organisation" in texte
    assert "{" not in texte and "{" not in objet
    assert '<a href="https://cockpit.lemans.org/test-pc-organisation">' in html
    assert "<img" not in html
    o2, t2, _ = mailer.rendre({"objet": "o", "corps": "{contact} <b>{prenom}</b>"},
                              {"prenom": "A&B"}, reg)
    assert t2 == "selection@lemans.org <b>A&B</b>"
    _, _, h2 = mailer.rendre({"objet": "o", "corps": "<b>{prenom}</b>"}, {"prenom": "A&B"}, reg)
    assert "&lt;b&gt;A&amp;B&lt;/b&gt;" in h2


def test_lien_depuis_env(db, monkeypatch):
    monkeypatch.setenv("FORMATION_PUBLIC_URL", "https://exemple.test/")
    reg = store.reglages(db)
    assert "https://exemple.test/test-pc-organisation" in mailer.rendre(reg["gabarit"], {"prenom": "A"}, reg)[1]


def test_lien_ignore_les_autres_url_publiques(db, monkeypatch):
    # COCKPIT_PUBLIC_URL / PUBLIC_BASE_URL servent a d'autres usages (safe.lemans.org...)
    monkeypatch.setenv("COCKPIT_PUBLIC_URL", "https://autre.test")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://safe.lemans.org")
    reg = store.reglages(db)
    texte = mailer.rendre(reg["gabarit"], {"prenom": "A"}, reg)[1]
    assert "https://cockpit.lemans.org/test-pc-organisation" in texte
    assert "autre.test" not in texte and "safe.lemans.org" not in texte


def test_un_message_par_destinataire(db):
    for i in range(3):
        _cand(db, "c%d@x.fr" % i)
    assert mailer.planifier(db, "non_invites", "adm", _now())["planifies"] == 3
    fil = _fil()
    fil.traiter_tout(db)
    assert len(FauxSMTP.envoyes) == 3
    for msg, expediteur, dest in FauxSMTP.envoyes:
        assert len(dest) == 1 and "," not in msg["To"] and msg["To"] == dest[0]
        assert msg["Cc"] is None and msg["Bcc"] is None
        assert msg["Reply-To"] == "selection@lemans.org"
        assert msg["From"] == "PC Organisation - ACO <safe@lemans.org>"
        assert msg["Date"] and msg["Message-ID"]
        assert str(make_header(decode_header(msg["Subject"]))).startswith("PC Organisation : votre test")
        types_ = [p.get_content_type() for p in msg.get_payload()]
        assert types_ == ["text/plain", "text/html"]
    assert fil.etat()["envoyes"] == 3


def test_fictifs_exclus(db):
    _cand(db, "vrai@x.fr")
    _cand(db, "faux@x.fr", fictif=True)
    assert mailer.compter(db, "non_invites") == 1
    assert mailer.planifier(db, "non_invites", "adm", _now())["planifies"] == 1
    assert db["envois"].count_documents({"email": "faux@x.fr"}) == 0


def test_planifier_deux_fois_ne_double_pas(db):
    _cand(db, "a@x.fr")
    _cand(db, "b@x.fr")
    assert mailer.planifier(db, "non_invites", "adm", _now())["planifies"] == 2
    assert mailer.planifier(db, "non_invites", "adm", _now())["planifies"] == 0
    _fil().traiter_tout(db)
    assert mailer.planifier(db, "non_invites", "adm", _now())["planifies"] == 0
    assert len(FauxSMTP.envoyes) == 2
    assert db["envois"].count_documents({"type": "invitation"}) == 2


def test_index_unique_bloque_le_doublon(db):
    from pymongo.errors import DuplicateKeyError
    cid = _cand(db, "a@x.fr")
    doc = {"campagne": store.CAMPAGNE, "candidat_id": cid, "type": "invitation", "statut": "en_file"}
    db["envois"].insert_one(dict(doc))
    with pytest.raises(DuplicateKeyError):
        db["envois"].insert_one(dict(doc))


def test_relance_seulement_non_commences(db):
    a = _cand(db, "a@x.fr")
    b = _cand(db, "b@x.fr")
    _cand(db, "c@x.fr")  # jamais invite : pas de relance
    for cid in (a, b):
        db["envois"].insert_one({"campagne": store.CAMPAGNE, "candidat_id": cid, "type": "invitation",
                                 "statut": "envoye", "email": "x"})
    db["tentatives"].insert_one({"campagne": store.CAMPAGNE, "candidat_id": b, "statut": "en_cours"})
    assert mailer.compter(db, "relance_non_commences") == 1
    assert mailer.planifier(db, "relance_non_commences", "adm", _now())["planifies"] == 1
    assert mailer.planifier(db, "relance_non_commences", "adm", _now())["planifies"] == 0
    r = db["envois"].find_one({"type": "relance"})
    assert r["candidat_id"] == a


def test_reply_to_obligatoire(db):
    _cand(db, "a@x.fr")
    db["reglages"].update_one({"_id": store.CAMPAGNE}, {"$set": {"reply_to": ""}})
    with pytest.raises(ValueError):
        mailer.planifier(db, "non_invites", "adm", _now())
    assert db["envois"].count_documents({}) == 0


def test_contact_rgpd_obligatoire(db, client, monkeypatch):
    monkeypatch.setattr(mailer.fil, "demarrer", lambda d: None)
    _cand(db, "a@x.fr")
    db["reglages"].update_one({"_id": store.CAMPAGNE}, {"$set": {"contact_rgpd": "  "}})
    with pytest.raises(ValueError) as exc:
        mailer.planifier(db, "non_invites", "adm", _now())
    assert exc.value.args[0] == "contact_rgpd_vide"
    r = _post(client, "/api/formation/admin/campagne/lancer", {"cible": "non_invites", "confirmation_n": 1})
    assert r.status_code == 400 and r.get_json()["error"] == "contact_rgpd_vide"
    assert db["envois"].count_documents({}) == 0


def test_echec_smtp_marque_echec_et_continue(db):
    _cand(db, "a@x.fr")
    _cand(db, "b@x.fr")
    _cand(db, "c@x.fr")
    mailer.planifier(db, "non_invites", "adm", _now())
    FauxSMTP.echec_pour = {"b@x.fr"}
    fil = _fil()
    fil.traiter_tout(db)
    assert sorted(m["To"] for m, _, _ in FauxSMTP.envoyes) == ["a@x.fr", "c@x.fr"]
    e = db["envois"].find_one({"email": "b@x.fr"})
    assert e["statut"] == "echec" and "refuse" in e["erreur"]
    assert fil.etat()["echecs"] == 1
    FauxSMTP.echec_pour = set()
    assert mailer.renvoyer_echecs(db, "adm") == 1
    fil.traiter_tout(db)
    assert db["envois"].find_one({"email": "b@x.fr"})["statut"] == "envoye"


def test_reprise_apres_redemarrage(db, monkeypatch):
    _cand(db, "a@x.fr")
    mailer.planifier(db, "non_invites", "adm", _now())
    db["envois"].update_many({}, {"$set": {"statut": "en_cours", "debut": _now() - timedelta(minutes=10)}})
    lances = []
    monkeypatch.setattr(mailer.fil, "demarrer", lambda d: lances.append(d))
    mailer.reprendre_au_demarrage(db)
    assert db["envois"].find_one()["statut"] == "en_file" and lances == [db]
    fil = _fil()
    fil.traiter_tout(db)
    assert len(FauxSMTP.envoyes) == 1
    lances.clear()
    mailer.reprendre_au_demarrage(db)
    assert lances == []  # plus rien en file : pas de fil


def test_arret_stoppe_la_boucle(db):
    for i in range(3):
        _cand(db, "c%d@x.fr" % i)
    mailer.planifier(db, "non_invites", "adm", _now())
    fil = mailer.FilEnvoi()
    fil._sleep = lambda s: fil._stop.set()
    fil.traiter_tout(db)
    assert len(FauxSMTP.envoyes) == 1
    assert fil.etat()["en_file"] == 2


def test_envoyer_essai(db):
    mailer.envoyer_essai(db, "moi@gmail.com", "adm")
    msg, _, dest = FauxSMTP.envoyes[0]
    assert dest == ["moi@gmail.com"] and "Bonjour Camille," in msg.get_payload()[0].get_payload(decode=True).decode()
    assert db["journal_admin"].find_one({"action": "campagne_essai"})
    with pytest.raises(ValueError):
        mailer.envoyer_essai(db, "pas une adresse", "adm")


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setitem(sys.modules, "app", types.SimpleNamespace(role_required=lambda r: (lambda f: f)))
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
    return c


def _post(c, chemin, data, csrf=True):
    return c.post(chemin, json=data, headers={"X-CSRFToken": c.jeton} if csrf else {})


def test_lancer_refuse_si_confirmation_differente(db, client, monkeypatch):
    demarres = []
    monkeypatch.setattr(mailer.fil, "demarrer", lambda d: demarres.append(1))
    _cand(db, "a@x.fr")
    _cand(db, "b@x.fr")
    base = "/api/formation/admin/campagne/"
    assert _post(client, base + "compter", {"cible": "non_invites"}).get_json()["n"] == 2
    r = _post(client, base + "lancer", {"cible": "non_invites", "confirmation_n": 3})
    assert r.status_code == 409 and r.get_json()["n"] == 2
    assert db["envois"].count_documents({}) == 0 and demarres == []
    r = _post(client, base + "lancer", {"cible": "non_invites", "confirmation_n": 2})
    assert r.status_code == 200 and r.get_json()["planifies"] == 2 and demarres == [1]
    assert _post(client, base + "lancer", {"cible": "x", "confirmation_n": 0}).status_code == 400
    assert _post(client, base + "compter", {"cible": "non_invites"}, csrf=False).status_code == 400


def test_gabarit_put(db, client):
    r = client.put("/api/formation/admin/gabarit", json={"objet": "O {prenom}", "corps": "C"},
                   headers={"X-CSRFToken": client.jeton})
    assert r.status_code == 200 and store.reglages(db)["gabarit"] == {"objet": "O {prenom}", "corps": "C"}
    r = client.put("/api/formation/admin/gabarit", json={"objet": "", "corps": "C"},
                   headers={"X-CSRFToken": client.jeton})
    assert r.status_code == 400


def test_rapport_pcorg_inchange(monkeypatch):
    import pcorg_summary_mail as m
    monkeypatch.setattr(m, "email_subject", lambda s: "Sujet")
    monkeypatch.setattr(m, "render_summary_text", lambda s: "txt")
    monkeypatch.setattr(m, "render_summary_html", lambda s: "<p>h</p>")
    r = m.send_summary_email(["a@x.fr"], {}, cc_emails=["c@x.fr"], bcc_emails=["b@x.fr"])
    msg, exp, dest = FauxSMTP.envoyes[0]
    assert dest == ["a@x.fr", "c@x.fr", "b@x.fr"] and msg["Cc"] == "c@x.fr" and exp == "safe@lemans.org"
    assert r == {"ok": True, "sent_count": 3, "smtp_host": "smtp.invalide.test", "from": "safe@lemans.org"}
    with pytest.raises(m.SmtpError):
        m.send_summary_email([], {})


def test_en_cours_recent_non_remis_en_file(db, monkeypatch):
    _cand(db, "a@x.fr")
    mailer.planifier(db, "non_invites", "adm", _now())
    lances = []
    monkeypatch.setattr(mailer.fil, "demarrer", lambda d: lances.append(1))
    db["envois"].update_many({}, {"$set": {"statut": "en_cours", "debut": _now() - timedelta(minutes=1)}})
    mailer.reprendre_au_demarrage(db)
    assert db["envois"].find_one()["statut"] == "en_cours" and lances == []
    db["envois"].update_many({}, {"$set": {"debut": _now() - timedelta(minutes=6)}})
    mailer.reprendre_au_demarrage(db)
    assert db["envois"].find_one()["statut"] == "en_file" and lances == [1]


def test_import_app_ne_demarre_pas_le_fil():
    import subprocess
    code = ("import app, formation.mailer as m; "
            "assert m.fil._thread is None; print('ok')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), timeout=120)
    assert "ok" in r.stdout, r.stderr[-500:]


def test_arret_persistant(db, client, monkeypatch):
    lances = []
    monkeypatch.setattr(mailer.fil, "demarrer", lambda d: lances.append(1))
    _cand(db, "a@x.fr")
    mailer.planifier(db, "non_invites", "adm", _now())
    mailer.arreter(db, "adm")
    assert store.reglages(db)["campagne_arretee"] is True
    assert db["envois"].find_one()["statut"] == "en_file"
    assert db["journal_admin"].find_one({"action": "campagne_arreter"})
    mailer.reprendre_au_demarrage(db)
    assert lances == []
    fil = _fil()
    fil.traiter_tout(db)
    assert FauxSMTP.envoyes == []
    r = _post(client, "/api/formation/admin/campagne/reprendre", {})
    assert r.status_code == 200 and lances == [1]
    assert store.reglages(db)["campagne_arretee"] is False
    _fil().traiter_tout(db)
    assert len(FauxSMTP.envoyes) == 1


def test_lancer_leve_l_arret(db, monkeypatch):
    monkeypatch.setattr(mailer.fil, "demarrer", lambda d: None)
    _cand(db, "a@x.fr")
    mailer.arreter(db, "adm")
    mailer.lancer(db, "non_invites", 1, "adm", _now())
    assert store.reglages(db)["campagne_arretee"] is False


def test_planifier_confirmation_et_adresses_invalides(db):
    _cand(db, "a@x.fr")
    _cand(db, "pas-une-adresse")
    with pytest.raises(ValueError) as e:
        mailer.planifier(db, "non_invites", "adm", _now(), confirmation_n=5)
    assert e.value.args == ("confirmation_differente", 2)
    assert db["envois"].count_documents({}) == 0
    assert mailer.planifier(db, "non_invites", "adm", _now(), confirmation_n=2) == {"planifies": 1, "invalides": 1}


def test_controles_a_l_envoi(db):
    a = _cand(db, "a@x.fr")
    _cand(db, "b@x.fr")
    mailer.planifier(db, "non_invites", "adm", _now())
    db["candidats"].update_one({"email": "a@x.fr"}, {"$set": {"fictif": True}})
    fil = _fil()
    assert fil.traiter_un(db)
    assert db["envois"].find_one({"email": "a@x.fr"})["statut"] == "annule"
    db["reglages"].update_one({"_id": store.CAMPAGNE}, {"$set": {"reply_to": ""}})
    assert fil.traiter_un(db)
    assert FauxSMTP.envoyes == []
    assert db["envois"].find_one({"email": "b@x.fr"})["statut"] == "en_file"
    assert store.reglages(db)["campagne_arretee"] is True


def test_erreur_mongo_ne_tue_pas_silencieusement(db, caplog):
    fil = _fil()
    fil.traiter_un = lambda d: (_ for _ in ()).throw(RuntimeError("mongo"))
    fil.traiter_tout(db)
    assert "erreur inattendue" in caplog.text


def test_lancer_sans_confirmation_400(db, client, monkeypatch):
    monkeypatch.setattr(mailer.fil, "demarrer", lambda d: None)
    _cand(db, "a@x.fr")
    base = "/api/formation/admin/campagne/lancer"
    for corps in ({"cible": "non_invites"}, {"cible": "non_invites", "confirmation_n": None},
                  {"cible": "non_invites", "confirmation_n": True},
                  {"cible": "non_invites", "confirmation_n": "1"}):
        r = _post(client, base, corps)
        assert r.status_code == 400 and r.get_json()["error"] == "confirmation_requise"
    assert db["envois"].count_documents({}) == 0


def test_orphelin_recent_repris_par_la_boucle(db):
    _cand(db, "a@x.fr")
    mailer.planifier(db, "non_invites", "adm", _now())
    db["envois"].update_many({}, {"$set": {"statut": "en_cours", "debut": _now() - timedelta(minutes=6)}})
    _fil().traiter_tout(db)
    assert len(FauxSMTP.envoyes) == 1
    assert db["envois"].find_one()["statut"] == "envoye"


def test_demarrer_attend_l_ancien_fil(db):
    import threading
    fil = mailer.FilEnvoi(sleep=lambda s: None)
    fin = threading.Event()
    fil._stop = threading.Event()
    fil._thread = threading.Thread(target=fin.wait, daemon=True)
    fil._thread.start()
    fil._stop.set()
    threading.Timer(0.2, fin.set).start()
    assert fil.demarrer(db) is True
    fil._thread.join(timeout=5)
