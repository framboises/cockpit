"""Mur TV de suivi GPS (balises_mur.etat_mur) contre un MongoDB local jetable :
groupes demandes dans l'ordre (3 au plus), groupes inconnus signales, visibilite,
en ligne / en mouvement par balises_core.device_state, trainee des dernieres
minutes ; liste publiee pour le portail (tv_choix)."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import balises_mur as M

MAINTENANT = datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_balises_mur_%d" % os.getpid()
    client.drop_database(name)
    M._PUBLIE.update(empreinte=None, a=0.0)
    yield client[name]
    client.drop_database(name)


def _naif(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _config(db, enabled=True):
    db.anoloc_config.insert_one({"_id": "global", "enabled": enabled, "beacon_groups": [
        {"id": "med", "label": "Medical", "color": "#f00", "icon": "e911_emergency",
         "anoloc_device_ids": ["a1", "a2", "a3"], "device_labels": {"a3": "AMBU 3"}},
        {"id": "sur", "label": "Surete", "color": "#00f", "icon": "local_police", "anoloc_device_ids": ["s1"]},
        {"id": "off", "label": "Coupe", "enabled": False, "anoloc_device_ids": []},
        {"id": "dep", "label": "Depannage", "anoloc_device_ids": []},
        {"id": "fou", "label": "Fourriere", "anoloc_device_ids": []},
    ]})


def _latest(db, did, label, age_s, speed=0, lat=47.9, lng=0.2, status="running"):
    db.anoloc_latest.insert_one({"_id": did, "device_id": did, "label": label, "lat": lat, "lng": lng,
                                 "speed": speed, "heading": 90, "status": status,
                                 "last_real_at": _naif(MAINTENANT - timedelta(seconds=age_s))})


def test_groupes_dans_l_ordre_et_etat_des_balises(db):
    _config(db)
    _latest(db, "a1", "AMBU 1", 20, speed=42)
    _latest(db, "a2", "AMBU 2", 7200)                      # muette depuis 2 h
    _latest(db, "s1", "VL 1", 30, speed=0, status="stopped")
    for k in range(3):
        db.anoloc_positions.insert_one({"device_id": "a1", "lat": 47.8 + k / 100, "lng": 0.2,
                                        "collected_at": _naif(MAINTENANT - timedelta(minutes=3 - k))})
    db.anoloc_positions.insert_one({"device_id": "a1", "lat": 1, "lng": 1,
                                    "collected_at": _naif(MAINTENANT - timedelta(minutes=30))})
    d = M.etat_mur(db, ["sur", "med", "inconnu", "off"], now=MAINTENANT)
    assert d["actif"] is True
    assert [g["id"] for g in d["groupes"]] == ["sur", "med"]
    assert d["ignores"] == ["inconnu", "off"]               # inconnu ou desactive
    assert [o["valeur"] for o in d["dispo"]] == ["med", "sur", "dep", "fou"]
    sur, med = d["groupes"]
    assert (sur["total"], sur["en_ligne"], sur["en_mouvement"]) == (1, 1, 0)
    assert (med["total"], med["en_ligne"], med["en_mouvement"]) == (3, 1, 1)
    a1, a2, a3 = med["balises"]
    assert a1["mouvement"] and a1["vitesse"] == 42 and len(a1["trace"]) == 3   # 10 dernieres minutes
    assert not a2["en_ligne"] and a2["age_s"] == 7200 and a2["trace"] == []
    assert a3["label"] == "AMBU 3" and a3["lat"] is None     # jamais collectee


def test_trois_groupes_au_plus_et_visibilite(db):
    _config(db)
    assert M.lire_groupes("med, sur,med,,dep,fou") == ["med", "sur", "dep"]
    d = M.etat_mur(db, ["med", "sur"], visibles={"sur"}, now=MAINTENANT)
    assert [g["id"] for g in d["groupes"]] == ["sur"] and d["ignores"] == ["med"]
    assert [o["valeur"] for o in d["dispo"]] == ["sur"]


def test_suivi_desactive(db):
    _config(db, enabled=False)
    d = M.etat_mur(db, ["med"], now=MAINTENANT)
    assert d["actif"] is False and d["groupes"] == []


def test_liste_publiee_pour_le_portail(db):
    _config(db)
    assert M.publier_choix_tv(db, force=True)
    doc = db[M.TV_CHOIX_COL].find_one({"_id": M.TV_CHOIX_ID})
    assert (doc["app_key"], doc["variable"], doc["min"], doc["max"], doc["separateur"]) == ("cockpit", "groupes", 1, 3, ",")
    assert [o["valeur"] for o in doc["options"]] == ["med", "sur", "dep", "fou"]
    assert doc["options"][0] == {"valeur": "med", "libelle": "Medical", "couleur": "#f00",
                                 "icone": "e911_emergency", "nb": 3}
    fond = db[M.TV_CHOIX_COL].find_one({"_id": M.TV_CHOIX_FOND_ID})
    assert (fond["variable"], fond["min"], fond["max"], fond["defaut"]) == ("fond", 1, 1, ["plan"])
    assert [o["valeur"] for o in fond["options"]] == ["plan", "ortho"]
    # Sans changement ni force : rien n'est reecrit
    M._PUBLIE["a"] = 0.0
    assert not M.publier_choix_tv(db)


def test_activite_libre_engagee_sur_place(db):
    _config(db)
    _latest(db, "a1", "AMBU 1", 20)                        # fiche ouverte, arrivee horodatee
    _latest(db, "a2", "AMBU 2", 7200)                      # muette, engagee quand meme
    _latest(db, "s1", "VL 1", 30, status="stopped")        # aucune fiche : libre
    base = {"event": "24H AUTOS", "year": 2026, "close_ts": None}
    db.pcorg.insert_many([
        {**base, "_id": "f1", "ts": _naif(MAINTENANT - timedelta(minutes=30)), "text": "Malaise tribune",
         "content_category": {"patrouille": "ambu  1"}, "intervention": {"arrived_at": _naif(MAINTENANT)}},
        {**base, "_id": "f2", "ts": _naif(MAINTENANT - timedelta(minutes=5)), "text": "Chute",
         "content_category": {"patrouille": "AMBU 2"}},
        {**base, "_id": "f3", "ts": _naif(MAINTENANT - timedelta(days=30)), "text": "Jamais close",
         "content_category": {"patrouille": "VL 1"}},
        {**base, "_id": "f4", "ts": _naif(MAINTENANT), "close_ts": _naif(MAINTENANT), "text": "Close",
         "content_category": {"patrouille": "VL 1"}},
        {"_id": "f5", "event": "AUTRE", "year": 2026, "close_ts": None, "ts": _naif(MAINTENANT),
         "content_category": {"patrouille": "VL 1"}},
    ])
    tab = M.tablette({"_id": "t1", "name": "Secu 1", "last_seen": MAINTENANT - timedelta(seconds=10),
                      "last_position": {"lat": 47.9, "lng": 0.2}, "status": "intervention",
                      "status_since": MAINTENANT, "active_fiche_id": "f2"}, now=MAINTENANT)
    d = M.etat_mur(db, ["med", "sur"], now=MAINTENANT, paires=[("24H AUTOS", 2026)],
                   tablettes={"sur": [tab]})
    med = {b["id"]: b for b in d["groupes"][0]["balises"]}
    assert (med["a1"]["activite"], med["a1"]["fiche_texte"]) == ("sur_place", "Malaise tribune")
    assert med["a2"]["activite"] == "engagee" and not med["a2"]["en_ligne"]
    assert med["a3"]["activite"] is None                   # jamais vue, pas de fiche
    sur = {b["id"]: b for b in d["groupes"][1]["balises"]}
    assert sur["s1"]["activite"] == "libre"
    assert (sur["field:t1"]["activite"], sur["field:t1"]["fiche_texte"]) == ("engagee", "Chute")
    assert "statut_field" not in sur["field:t1"] and "fiche_id" not in sur["field:t1"]
    assert (d["groupes"][0]["engagees"], d["groupes"][1]["engagees"], d["groupes"][1]["libres"]) == (2, 1, 1)


def test_suivi_operateur_tous_les_groupes_visibles(db):
    _config(db)
    # demandes=None : tous les groupes actifs visibles, dans l'ordre de la configuration
    d = M.etat_mur(db, None, now=MAINTENANT, maxi=M.SUIVI_MAX_GROUPES)
    assert [g["id"] for g in d["groupes"]] == ["med", "sur", "dep", "fou"]
    d = M.etat_mur(db, None, visibles={"sur", "fou"}, now=MAINTENANT, maxi=M.SUIVI_MAX_GROUPES)
    assert [g["id"] for g in d["groupes"]] == ["sur", "fou"]
    # Au-dela de 3 groupes demandes : servis par la page operateur, pas par le mur
    ids = M.lire_groupes("med,sur,dep,fou", M.SUIVI_MAX_GROUPES)
    assert len(M.etat_mur(db, ids, now=MAINTENANT, maxi=M.SUIVI_MAX_GROUPES)["groupes"]) == 4
    assert len(M.etat_mur(db, M.lire_groupes("med,sur,dep,fou"), now=MAINTENANT)["groupes"]) == 3
