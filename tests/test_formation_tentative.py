"""Regles du passage unique (formation/tentative.py) contre un MongoDB local jetable."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from formation import correction, store, tentative

T0 = datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_formation_tent_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    store.ensure_indexes(d)
    yield d
    client.drop_database(name)


def _candidat(db, amenagement=False):
    email = "c%d@b.fr" % db["candidats"].count_documents({})
    res = db["candidats"].insert_one({"campagne": store.CAMPAGNE, "email": email,
                                      "amenagement": amenagement})
    return db["candidats"].find_one({"_id": res.inserted_id})


def _t(db, cand):
    return db["tentatives"].find_one({"candidat_id": str(cand["_id"]),
                                      "statut": {"$in": store.STATUTS_TENTATIVE_ACTIFS}})


def _etats(etat):
    return {e["id"]: e["etat"] for e in etat["epreuves"]}


def _demarre(db, amenagement=False):
    cand = _candidat(db, amenagement)
    tentative.connexion(db, cand, "s1", "10.0.0.1", T0)
    tentative.commencer(db, cand, "s1", 1.33, T0)
    return cand


def test_sans_tentative_non_commence(db):
    cand = _candidat(db)
    assert tentative.connexion(db, cand, "s1", "10.0.0.1", T0) == {"statut": "non_commence"}


def test_commencer_irreversible(db):
    cand = _candidat(db)
    e1 = tentative.commencer(db, cand, "s1", 1.33, T0)
    e2 = tentative.commencer(db, cand, "s1", 1.33, T0 + timedelta(minutes=1))
    assert db["tentatives"].count_documents({}) == 1
    assert e1 == e2
    assert e1["statut"] == "en_cours"
    assert e1["epreuve_courante"] == "radio"
    assert [e["id"] for e in e1["epreuves"]] == store.EPREUVES
    assert set(_etats(e1).values()) == {"a_faire"}
    t = _t(db, cand)
    assert t["debut"].replace(tzinfo=timezone.utc) == T0
    assert t["campagne"] == store.CAMPAGNE and isinstance(t["seed"], int)


def test_ordre_des_epreuves_impose(db):
    cand = _demarre(db)
    with pytest.raises(ValueError, match="ordre"):
        tentative.debut_epreuve(db, cand, "s1", "appel", T0)
    enonce = tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    assert "messages" in enonce
    # l'enonce envoye est conserve dans la tentative
    assert _t(db, cand)["epreuves"]["radio"]["enonce"] == enonce
    with pytest.raises(ValueError, match="ordre"):
        tentative.debut_epreuve(db, cand, "s1", "appel", T0)
    with pytest.raises(ValueError, match="ordre"):
        tentative.debut_epreuve(db, cand, "s1", "inconnue", T0)


def test_debut_rejoue_meme_session_sans_action(db):
    """Reponse de /debut perdue : le meme onglet redemande l'epreuve et recoit le
    meme enonce, sans seconde transition ; des qu'une action est recue, ou depuis
    une autre session, c'est un refus."""
    cand = _demarre(db)
    enonce = tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    t1 = _t(db, cand)
    t5 = T0 + timedelta(seconds=5)
    assert tentative.debut_epreuve(db, cand, "s1", "radio", t5) == enonce
    t2 = _t(db, cand)
    assert t2["epreuves"]["radio"]["debut"] == t1["epreuves"]["radio"]["debut"]
    assert t2["epreuves"]["radio"]["etat"] == "en_cours"
    assert tentative.ecoule_ms(t2, "radio", t5) == 5000
    assert tentative.ecoule_ms(t2, "appel", t5) == 0
    tentative.action(db, cand, "s1", "radio", {"type": "prise", "msg": "r01", "t_ms": 6000},
                     T0 + timedelta(seconds=6))
    with pytest.raises(ValueError, match="ordre"):
        tentative.debut_epreuve(db, cand, "s1", "radio", T0 + timedelta(seconds=7))
    # autre session : jamais l'enonce de l'epreuve en cours
    autre = _demarre(db)
    tentative.debut_epreuve(db, autre, "s1", "radio", T0)
    with pytest.raises(PermissionError):
        tentative.debut_epreuve(db, autre, "s9", "radio", t5)


def test_enonce_deterministe_par_seed(db):
    cand = _demarre(db)
    t = _t(db, cand)
    fin_radio = T0 + timedelta(milliseconds=correction.duree_min_ms("radio", 1.0))
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    tentative.fin_epreuve(db, cand, "s1", "radio", fin_radio)
    enonce = tentative.debut_epreuve(db, cand, "s1", "appel", fin_radio)
    import random
    from formation import banque
    assert enonce == banque.enonce("appel", random.Random(t["seed"] + 1))


def test_coupure_abandonne_et_continue(db):
    cand = _demarre(db)
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    tentative.action(db, cand, "s1", "radio", {"type": "prise", "msg": "r01", "t_ms": 1500},
                     T0 + timedelta(seconds=2))
    etat = tentative.connexion(db, cand, "s2", "10.0.0.2", T0 + timedelta(minutes=5))
    assert etat["statut"] == "en_cours"
    assert etat["epreuve_courante"] == "appel"
    assert _etats(etat)["radio"] == "abandonnee"
    t = _t(db, cand)
    radio = t["epreuves"]["radio"]
    assert isinstance(radio["score"], int)
    assert "abandon" in radio["drapeaux"]
    assert t["reprises"] == 1
    trace = t["reprises_trace"][0]
    assert trace["epreuve"] == "radio" and trace["ip"] == "10.0.0.2"
    assert trace["date"].replace(tzinfo=timezone.utc) == T0 + timedelta(minutes=5)
    assert t["sid"] == "s2"
    assert "abandon" not in etat and "score" not in str(etat)


def test_second_onglet_ne_rejoue_pas(db):
    cand = _demarre(db)
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    etat = tentative.connexion(db, cand, "s2", "10.0.0.1", T0 + timedelta(seconds=30))
    assert _etats(etat)["radio"] == "abandonnee"
    with pytest.raises(ValueError, match="ordre"):
        tentative.debut_epreuve(db, cand, "s2", "radio", T0 + timedelta(seconds=31))
    with pytest.raises(PermissionError):
        tentative.debut_epreuve(db, cand, "s1", "radio", T0 + timedelta(seconds=31))
    # une troisieme connexion ne rouvre rien
    etat = tentative.connexion(db, cand, "s3", "10.0.0.1", T0 + timedelta(seconds=40))
    assert _etats(etat)["radio"] == "abandonnee"
    assert etat["epreuve_courante"] == "appel"
    assert _t(db, cand)["reprises"] == 1


def test_deuxieme_coupure_interrompt(db):
    cand = _demarre(db)
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    tentative.connexion(db, cand, "s2", "10.0.0.1", T0 + timedelta(minutes=2))
    tentative.debut_epreuve(db, cand, "s2", "appel", T0 + timedelta(minutes=3))
    etat = tentative.connexion(db, cand, "s3", "10.0.0.1", T0 + timedelta(minutes=4))
    assert etat["statut"] == "interrompue"
    assert _etats(etat)["appel"] == "abandonnee"
    assert _etats(etat)["mc"] == "a_faire"
    t = _t(db, cand)
    assert t["reprises"] == 1
    assert t["fin"].replace(tzinfo=timezone.utc) == T0 + timedelta(minutes=4)
    with pytest.raises(ValueError):
        tentative.debut_epreuve(db, cand, "s3", "mc", T0 + timedelta(minutes=5))
    # pas de nouvelle tentative possible
    tentative.commencer(db, cand, "s3", 1.33, T0 + timedelta(minutes=6))
    assert db["tentatives"].count_documents({}) == 1


def test_reconnexion_apres_une_heure_interrompt(db):
    cand = _demarre(db)
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    etat = tentative.connexion(db, cand, "s2", "10.0.0.1", T0 + timedelta(minutes=61))
    assert etat["statut"] == "interrompue"
    assert _etats(etat)["radio"] == "abandonnee"
    assert _t(db, cand)["reprises"] == 0


def test_reconnexion_apres_une_heure_hors_epreuve_interrompt(db):
    cand = _demarre(db)
    etat = tentative.connexion(db, cand, "s2", "10.0.0.1", T0 + timedelta(minutes=61))
    assert etat["statut"] == "interrompue"


def test_ancienne_session_refusee(db):
    cand = _demarre(db)
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    tentative.connexion(db, cand, "s2", "10.0.0.1", T0 + timedelta(seconds=10))
    tentative.debut_epreuve(db, cand, "s2", "appel", T0 + timedelta(seconds=20))
    with pytest.raises(PermissionError, match="session"):
        tentative.action(db, cand, "s1", "appel", {"type": "reponse", "question": "a1s1",
                                                   "choix": "a1s1c1", "t_ms": 100},
                         T0 + timedelta(seconds=21))
    with pytest.raises(PermissionError, match="session"):
        tentative.fin_epreuve(db, cand, "s1", "appel", T0 + timedelta(seconds=22))
    assert _t(db, cand)["epreuves"]["appel"]["actions"] == []


def test_coupure_hors_epreuve_sans_reprise(db):
    cand = _demarre(db)
    fin_radio = T0 + timedelta(milliseconds=correction.duree_min_ms("radio", 1.0))
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    tentative.fin_epreuve(db, cand, "s1", "radio", fin_radio)
    etat = tentative.connexion(db, cand, "s2", "10.0.0.1", fin_radio + timedelta(seconds=30))
    assert etat["statut"] == "en_cours"
    assert etat["epreuve_courante"] == "appel"
    assert _etats(etat)["radio"] == "terminee"
    t = _t(db, cand)
    assert t["reprises"] == 0 and t["sid"] == "s2"


def test_coef_fige(db):
    cand = _candidat(db, amenagement=True)
    tentative.commencer(db, cand, "s1", 1.33, T0)
    db["candidats"].update_one({"_id": cand["_id"]}, {"$set": {"amenagement": False}})
    db["reglages"].update_one({"_id": store.CAMPAGNE}, {"$set": {"coef_amenagement": 2.0}},
                              upsert=True)
    cand = db["candidats"].find_one({"_id": cand["_id"]})
    etat = tentative.commencer(db, cand, "s1", 2.0, T0 + timedelta(minutes=1))
    assert etat["coef"] == 1.33
    autre = _candidat(db, amenagement=False)
    assert tentative.commencer(db, autre, "s9", 1.33, T0)["coef"] == 1.0
    # puis l'amenagement active apres le depart ne change rien
    autre["amenagement"] = True
    assert tentative.commencer(db, autre, "s9", 1.33, T0)["coef"] == 1.0


def test_duree_incoherente_drapeau(db):
    cand = _demarre(db)
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    etat = tentative.fin_epreuve(db, cand, "s1", "radio", T0 + timedelta(seconds=5))
    assert _etats(etat)["radio"] == "terminee"
    radio = _t(db, cand)["epreuves"]["radio"]
    assert "incoherente" in radio["drapeaux"]
    assert radio["fin"].replace(tzinfo=timezone.utc) == T0 + timedelta(seconds=5)
    # duree normale : pas de drapeau
    autre = _candidat(db)
    tentative.commencer(db, autre, "z1", 1.33, T0)
    tentative.debut_epreuve(db, autre, "z1", "radio", T0)
    tentative.fin_epreuve(db, autre, "z1", "radio",
                          T0 + timedelta(milliseconds=correction.duree_min_ms("radio", 1.0)))
    assert "incoherente" not in _t(db, autre)["epreuves"]["radio"]["drapeaux"]


def test_horodatage_incoherent_ramene(db):
    cand = _demarre(db)
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    tentative.action(db, cand, "s1", "radio", {"type": "prise", "msg": "r01", "t_ms": 50000},
                     T0 + timedelta(seconds=3))
    radio = _t(db, cand)["epreuves"]["radio"]
    assert radio["actions"][0]["t_ms"] == 3000
    assert radio["actions"][0]["recu_ms"] == 3000
    assert "horodatage_incoherent" in radio["drapeaux"]


def test_action_apres_duree_max_ignoree(db):
    cand = _demarre(db)
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    tard = T0 + timedelta(milliseconds=correction.duree_max_ms("radio", 1.0) + 1)
    tentative.action(db, cand, "s1", "radio", {"type": "prise", "msg": "r14", "t_ms": 1000}, tard)
    assert _t(db, cand)["epreuves"]["radio"]["actions"] == []


def test_action_hors_epreuve_en_cours_refusee(db):
    cand = _demarre(db)
    with pytest.raises(ValueError):
        tentative.action(db, cand, "s1", "radio", {"type": "prise", "msg": "r01", "t_ms": 1}, T0)


def test_derniere_epreuve_termine_la_tentative(db):
    cand = _demarre(db)
    now = T0
    for e in store.EPREUVES:
        tentative.debut_epreuve(db, cand, "s1", e, now)
        now += timedelta(minutes=3)
        etat = tentative.fin_epreuve(db, cand, "s1", e, now)
    assert etat["statut"] == "terminee"
    assert etat["epreuve_courante"] is None
    t = _t(db, cand)
    assert t["fin"].replace(tzinfo=timezone.utc) == now
    assert all(isinstance(t["epreuves"][e]["score"], int) for e in store.EPREUVES)


def test_expirer_onglet_laisse_ouvert(db):
    cand = _demarre(db)
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    t = _t(db, cand)
    max_ms = correction.duree_max_ms("radio", 1.0)
    t = tentative.expirer_si_besoin(db, t, T0 + timedelta(milliseconds=max_ms + 59000))
    assert t["epreuves"]["radio"]["etat"] == "en_cours"
    t = tentative.expirer_si_besoin(db, t, T0 + timedelta(milliseconds=max_ms + 61000))
    assert t["epreuves"]["radio"]["etat"] == "abandonnee"
    assert t["reprises"] == 0 and t["statut"] == "en_cours"
    etat = tentative.connexion(db, cand, "s1", "10.0.0.1", T0 + timedelta(minutes=10))
    assert etat["epreuve_courante"] == "appel"


def test_reinitialiser_motif_obligatoire(db):
    cand = _demarre(db)
    with pytest.raises(ValueError, match="motif"):
        tentative.reinitialiser(db, str(cand["_id"]), "   ", "doa@x.fr", T0)
    assert _t(db, cand)["statut"] == "en_cours"
    assert db["journal_admin"].count_documents({}) == 0


def test_reinitialiser_conserve_et_libere(db):
    cand = _demarre(db)
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    tentative.connexion(db, cand, "s2", "10.0.0.1", T0 + timedelta(minutes=61))
    tentative.reinitialiser(db, str(cand["_id"]), "coupure electrique", "doa@x.fr",
                            T0 + timedelta(hours=2))
    ancienne = db["tentatives"].find_one({"statut": "reinitialisee"})
    assert ancienne and ancienne["reinitialisation"]["motif"] == "coupure electrique"
    j = db["journal_admin"].find_one({"action": "reinitialisation"})
    assert j["auteur"] == "doa@x.fr"
    assert j["details"] == {"candidat_id": str(cand["_id"]), "motif": "coupure electrique"}
    assert tentative.connexion(db, cand, "s3", "10.0.0.1", T0 + timedelta(hours=3)) == \
        {"statut": "non_commence"}
    etat = tentative.commencer(db, cand, "s3", 1.33, T0 + timedelta(hours=3))
    assert etat["statut"] == "en_cours" and etat["epreuve_courante"] == "radio"
    assert db["tentatives"].count_documents({}) == 2


def test_score_global_pondere():
    t = {"statut": "en_cours", "epreuves": {
        "radio": {"etat": "terminee", "score": 80},
        "appel": {"etat": "abandonnee", "score": 20},
        "mc": {"etat": "a_faire"},
        "patrouilles": {"etat": "en_cours"},
    }}
    pond = {"radio": 3, "appel": 1, "mc": 5, "patrouilles": 5}
    assert tentative.score_global(t, pond) == pytest.approx((80 * 3 + 20) / 4)
    assert tentative.score_global({"epreuves": {}}, pond) is None
    assert tentative.score_global(t, {"radio": 0, "appel": 0}) is None


def test_score_global_interrompue_compte_zero():
    epreuves = {
        "radio": {"etat": "terminee", "score": 90},
        "appel": {"etat": "abandonnee", "score": 30},
        "mc": {"etat": "a_faire"},
        "patrouilles": {"etat": "a_faire"},
    }
    pond = {"radio": 2, "appel": 1, "mc": 1, "patrouilles": 2}
    attendu = (90 * 2 + 30) / 6
    for statut in ("interrompue", "terminee"):
        t = {"statut": statut, "epreuves": epreuves}
        assert tentative.score_global(t, pond) == pytest.approx(attendu)
    # en cours : epreuves jouees seules
    assert tentative.score_global({"statut": "en_cours", "epreuves": epreuves}, pond) == \
        pytest.approx((90 * 2 + 30) / 3)
    # interrompu avant toute epreuve : 0
    vide = {"statut": "interrompue", "epreuves": {"radio": {"etat": "a_faire"}}}
    assert tentative.score_global(vide, {"radio": 1}) == 0


def test_transition_concurrente_une_seule(db):
    """Deux requetes ayant lu le meme etat : une seule transition passe."""
    cand = _demarre(db)
    tentative.debut_epreuve(db, cand, "s1", "radio", T0)
    lu = _t(db, cand)
    now = T0 + timedelta(seconds=30)
    assert tentative._clore(db, lu, "radio", "abandonnee", now) is not None
    assert tentative._clore(db, lu, "radio", "terminee", now) is None
    # action arrivee apres la lecture : le score ne porte pas sur un journal perime
    autre = _candidat(db)
    tentative.commencer(db, autre, "z1", 1.33, T0)
    tentative.debut_epreuve(db, autre, "z1", "radio", T0)
    lu = _t(db, autre)
    tentative.action(db, autre, "z1", "radio", {"type": "prise", "msg": "r01", "t_ms": 1000},
                     T0 + timedelta(seconds=1))
    assert tentative._clore(db, lu, "radio", "terminee", now) is None
    tentative.fin_epreuve(db, autre, "z1", "radio", now)
    assert len(_t(db, autre)["epreuves"]["radio"]["actions"]) == 1
