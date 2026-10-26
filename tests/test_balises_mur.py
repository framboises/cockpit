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
    # Sans changement ni force : rien n'est reecrit
    M._PUBLIE["a"] = 0.0
    assert not M.publier_choix_tv(db)
