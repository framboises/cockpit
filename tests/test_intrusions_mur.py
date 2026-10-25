"""Mur TV des intrusions (detection.etat_mur) contre un MongoDB local jetable :
une intrusion du mur est dans le planning ET non filtree par l'IA ; en cours =
message il y a moins de 30 s ; etat des cameras et des zones sur la carte."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import detection as D

MAINTENANT = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)  # 21:00 a Paris


@pytest.fixture
def db():
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_intrusions_mur_%d" % os.getpid()
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


def _naif(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _camera(db, nom, lat=47.94, lng=0.22):
    return db.cockpit_cameras.insert_one({
        "name": nom, "ip": "10.0.0.%d" % (db.cockpit_cameras.count_documents({}) + 1), "enabled": True,
        "map": {"lat": lat, "lng": lng},
        "channels": [{"no": 1, "name": nom, "enabled": True, "roles": [{"type": "fielddetection"}]}]}).inserted_id


def _intrusion(db, cam, nom, debut_min, dernier_s, **extra):
    doc = {"camera_id": cam, "channel": 1, "camera_name": nom, "channel_name": nom,
           "started_at": _naif(MAINTENANT - timedelta(minutes=debut_min)),
           "last_at": _naif(MAINTENANT - timedelta(seconds=dernier_s)),
           "target_types": ["human"], "status": "new",
           "cover": {"photo": "intrusions/a.jpg", "thumb": "intrusions/a_t.jpg", "rect": {"x": 0.1, "y": 0.2, "w": 0.05, "h": 0.1}},
           "photos": [{"photo": "intrusions/a.jpg"}, {"photo": "intrusions/b.jpg", "rect": {"x": 0.3, "y": 0.2, "w": 0.05, "h": 0.1}}]}
    doc.update(extra)
    return db.hik_intrusions.insert_one(doc).inserted_id


def test_en_cours_a_traiter_et_comptes(db):
    houx, bleu, nord = _camera(db, "Porte Houx"), _camera(db, "Parking Bleu"), _camera(db, "Tribune Nord")
    _intrusion(db, houx, "Porte Houx", 2, 5, ai={"status": "confirmed", "label": "personne", "real_conf": 0.9})
    _intrusion(db, bleu, "Parking Bleu", 40, 2000, target_types=["vehicle"])
    _intrusion(db, bleu, "Parking Bleu", 90, 5000, status="closed")
    # Filtree par l'IA et hors planning : comptees, jamais montrees
    _intrusion(db, nord, "Tribune Nord", 1, 3, ai={"status": "filtered", "label": "oiseau"})
    _intrusion(db, nord, "Tribune Nord", 1, 3, armed=False)
    d = D.etat_mur(db, MAINTENANT)

    assert [e["camera"] for e in d["en_cours"]] == ["Porte Houx"]
    e = d["en_cours"][0]
    assert e["photo_der"] == "/api/intrusions/photo?p=intrusions/b.jpg" and e["rect_der"]["x"] == 0.3
    assert e["ia"]["label"] == "personne"
    assert [e["camera"] for e in d["a_traiter"]] == ["Parking Bleu"]
    assert d["a_traiter"][0]["type"] == "vehicle"
    assert d["derniere"]["camera"] == "Parking Bleu"
    assert d["totaux"] == {"intrusions": 3, "human": 2, "vehicle": 1, "a_traiter": 2,
                           "filtrees": 1, "hors_planning": 1}
    assert d["par_heure"][20] == 2 and d["par_heure"][19] == 1  # heures de Paris (20:58, 20:20, 19:30)
    cams = {c["nom"]: c for c in d["cameras"]}
    assert cams["Porte Houx"]["actif"] and not cams["Tribune Nord"]["actif"]
    assert cams["Parking Bleu"]["a_traiter"] == 1


def test_planning_et_zones(db):
    houx, village = _camera(db, "Porte Houx"), _camera(db, "Village")
    _camera(db, "Sans position")
    db.cockpit_cameras.update_one({"name": "Sans position"}, {"$unset": {"map": 1}})
    db.detection_groups.insert_one({"name": "Village", "members": [{"camera_id": village, "channel": 1}],
                                    "arming": {"mode": "off"}})
    z = db.detection_zones.insert_one({"name": "Cloture", "color": "#d97706",
                                       "polygon": [[47.9, 0.2], [47.91, 0.2], [47.91, 0.21]],
                                       "links": [{"camera_id": houx, "channel": 1, "regions": []}]}).inserted_id
    assert D.etat_mur(db, MAINTENANT)["zones"][0]["actif"] is False
    _intrusion(db, houx, "Porte Houx", 1, 2)
    d = D.etat_mur(db, MAINTENANT)
    assert d["zones"][0]["actif"] is True
    assert d["en_cours"][0]["zones"][0]["id"] == str(z)
    cams = {c["nom"]: c for c in d["cameras"]}
    # Canal dans aucun groupe : toujours actif ; groupe en pause : hors planning
    assert cams["Porte Houx"]["arme"] and not cams["Village"]["arme"]
    assert "Sans position" not in cams
    assert d["groupes"] == [{"nom": "Village", "couleur": "#2563eb", "arme": False, "libelle": "En pause"}]


def test_portee_des_intrusions():
    assert D.portee({}) == "alerte"
    assert D.portee({"ai": {"status": "confirmed"}}) == "alerte"
    assert D.portee({"ai": {"status": "unverified"}}) == "alerte"
    oiseau = {"ai": {"status": "filtered", "guess": [{"label": "oiseau", "p": 0.8}, {"label": "personne", "p": 0.2}]}}
    assert D.portee(oiseau) == "ecartee"
    assert D.portee({"ai": {"status": "filtered", "guess": [{"label": "personne", "p": 0.6}]}}) == "doute"
    # L'avis de l'operateur l'emporte
    assert D.portee(dict(oiseau, ai_vote={"vote": "up"})) == "alerte"
    assert D.portee({"ai": {"status": "confirmed"}, "ai_vote": {"vote": "down"}}) == "ecartee"


def test_douteuse_a_traiter_jamais_en_cours(db):
    cam = _camera(db, "Porte Houx")
    _intrusion(db, cam, "Porte Houx", 1, 2, ai={"status": "filtered", "guess": [{"label": "personne", "p": 0.7}]})
    d = D.etat_mur(db, MAINTENANT)
    assert d["en_cours"] == [] and [e["camera"] for e in d["a_traiter"]] == ["Porte Houx"]
    assert d["totaux"]["filtrees"] == 0 and d["totaux"]["a_traiter"] == 1


def test_overview_sans_les_ecartees(db, monkeypatch):
    # Compteurs et pastilles de la page Detection : un oiseau n'allume rien
    import app as A
    monkeypatch.setattr(D, "_db", db)
    monkeypatch.setattr(A, "CODING", True)
    cam = _camera(db, "Porte Houx")
    maintenant = datetime.now(timezone.utc)
    for ai in ({"status": "filtered", "guess": [{"label": "oiseau", "p": 0.9}]},
               {"status": "filtered", "guess": [{"label": "personne", "p": 0.7}]},
               {"status": "confirmed"}):
        db.hik_intrusions.insert_one({"camera_id": cam, "channel": 1, "camera_name": "Porte Houx",
                                      "started_at": _naif(maintenant - timedelta(seconds=20)),
                                      "last_at": _naif(maintenant - timedelta(seconds=5)),
                                      "target_types": ["human"], "status": "new", "ai": ai})
    rep = A.app.test_client().get("/api/detection/overview?period=today")
    t, c = rep.get_json()["totals"], rep.get_json()["by_channel"]["%s:1" % cam]
    assert (t["active"], t["new"], t["ecartees"], t["doutes"]) == (1, 2, 1, 1)
    assert c["new"] == 2 and c["n"] == 3
