"""Cameras en projet : priorite, support de pose, remplacements, deplacement,
et dossier de planification des travaux (cameras_dossier.py). Aucun appel
reseau : les tuiles IGN sont remplacees par une image unie."""
from datetime import datetime, timezone

import pytest
from bson.objectid import ObjectId
from PIL import Image

from conftest import FakeDb

import cameras as C
import cameras_dossier as D

A, B, M = ObjectId(), ObjectId(), ObjectId()
NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDb(cockpit_cameras=[
        {"_id": A, "name": "048 - Ouest", "ip": "10.34.1.72", "map": {"lat": 47.9470, "lng": 0.2090}, "channels": []},
        {"_id": B, "name": "086 - Beausejour", "ip": "10.34.1.80", "map": {"lat": 47.9480, "lng": 0.2100}, "channels": []},
        {"_id": M, "name": "041 - Hall", "ip": "10.34.1.105", "model": "DS-2DP7D836IW-AE", "map": {"lat": 47.9290, "lng": 0.1924},
         "channels": [{"no": 1, "optics": "mobile", "view": {"az": 10, "fov": 59, "range": 48}}]},
    ], cameras_planned=[])
    monkeypatch.setattr(C, "_db", fake)
    monkeypatch.setattr(C, "_col_cameras", fake["cockpit_cameras"])
    monkeypatch.setattr(C, "_who", lambda: "Test")
    return fake


# --- champs du projet ---------------------------------------------------------
def test_priorite_support_hauteur(db):
    f, unset, err = C._planned_fields({"priority": "3", "mount": "angle_mur", "mount_height": "4,5"}, [{"no": 1}])
    assert err is None and f == {"priority": 3, "mount": "angle_mur", "mount_height": 4.5}
    f, unset, err = C._planned_fields({"priority": "", "mount": "", "mount_height": None}, [{"no": 1}])
    assert err is None and f == {} and set(unset) == {"priority", "mount", "mount_height"}


@pytest.mark.parametrize("data", [{"priority": "abc"}, {"priority": "0"}, {"priority": "1000"},
                                  {"mount": "toit"}, {"mount_height": "200"}, {"mount_height": "x"}])
def test_champs_invalides(db, data):
    assert C._planned_fields(data, [{"no": 1}])[2]


def test_remplacements_verifies(db):
    f, _u, err = C._planned_fields({"replaces": [str(A), str(A), str(B)]}, [{"no": 1}])
    assert err is None and f["replaces"] == [A, B]  # doublon retire
    assert C._planned_fields({"replaces": [str(ObjectId())]}, [{"no": 1}])[2] == "Camera a remplacer introuvable"


def test_conflits(db):
    assert C._planned_conflict(None, {"moves_camera_id": M, "replaces": [M]})
    db["cameras_planned"].insert_one({"_id": ObjectId(), "name": "P1", "replaces": [A]})
    assert "deja reprise par le projet P1" in C._planned_conflict(None, {"replaces": [A]})
    assert "deja reprise" in C._planned_conflict(None, {"moves_camera_id": A})
    assert C._planned_conflict(None, {"replaces": [B]}) is None


# --- donnees du dossier -------------------------------------------------------
def _planned(db):
    db["cameras_planned"].docs = [
        {"_id": ObjectId(), "name": "Sans priorite", "status": "souhaitee", "lat": 47.95, "lng": 0.21,
         "channels": [{"no": 1, "optics": "fixe"}]},
        {"_id": ObjectId(), "name": "Parking P4", "status": "validee", "priority": 1, "mount": "mat", "mount_height": 6,
         "lat": 47.9472, "lng": 0.2093, "replaces": [A, B],
         "channels": [{"no": 1, "optics": "mobile", "model": "2dp-ptz", "view": {"az": 40, "fov": 59, "range": 48}}] +
                     [{"no": i, "optics": "fixe", "model": "2dp-pano", "view": {"az": 90 * i, "fov": 106, "range": 30}} for i in range(2, 6)]},
        {"_id": ObjectId(), "name": "Deplacement - 041", "status": "souhaitee", "priority": 2, "lat": 47.9293, "lng": 0.1924,
         "moves_camera_id": M, "channels": [{"no": 1, "optics": "mobile", "view": {"az": 10, "fov": 59, "range": 48}}]},
        {"_id": ObjectId(), "name": "Commandee", "status": "commandee", "priority": 1, "lat": 47.96, "lng": 0.22,
         "channels": [{"no": 1}]},
    ]


def test_tri_par_priorite_et_reperes(db):
    _planned(db)
    items, _existing = D.collect(db, statuses=["souhaitee", "validee"])
    assert [(it["rep"], it["name"]) for it in items] == [("C1", "Parking P4"), ("C2", "Deplacement - 041"), ("C3", "Sans priorite")]
    assert [it["kind"] for it in items] == ["remplacement", "deplacement", "nouvelle"]
    assert [r["name"] for r in items[0]["replaces"]] == ["048 - Ouest", "086 - Beausejour"]
    assert items[1]["moves"]["lat"] == 47.9290


def test_materiel(db):
    _planned(db)
    items, _e = D.collect(db, statuses=["souhaitee", "validee"])
    # Multi-capteurs : UN appareil pour les 5 canaux ; deplacement : rien a commander
    assert D.devices_of(items[0]) == ["Hikvision DS-2DP7D836 (1 PTZ + 4 objectifs fixes)"]
    assert D.devices_of(items[1]) == []
    assert D.devices_of(items[2]) == ["Hikvision iDS-2CD7A87G0 – 2,8 mm"]
    # Objectif de la camera deplacee deduit de son modele reel
    assert items[1]["channels"][0]["lens"] == "2dp-ptz"


def test_geodesie():
    x, y = D.lambert93(47.95248, 0.21907)
    assert abs(x - 492456.3) < 0.5 and abs(y - 6764998.7) < 1.0  # point de reference du dossier Porte du Houx
    assert D.cardinal(0) == "N" and D.cardinal(200) == "SSO" and D.cardinal(359) == "N"
    assert D.dms(47.5, "N", "S") == "47°30'00.0\" N"
    lat, lng = D.dest_point(47.95, 0.21, 90, 100)
    assert abs(D.distance_m(47.95, 0.21, lat, lng) - 100) < 0.5


def test_pdf(db, monkeypatch):
    _planned(db)
    monkeypatch.setattr(D, "_tile", lambda z, x, y: Image.new("RGB", (256, 256), (120, 140, 110)))
    pdf, n = D.build_pdf(db, {"statuses": ["souhaitee", "validee"], "author": "L. Arnault", "indice": "b"})
    assert n == 3 and pdf.startswith(b"%PDF")
    # couverture + plan d'ensemble + 3 fiches + recapitulatif
    assert b"/Count 6" in pdf
    assert D.build_pdf(db, {"statuses": ["commandee"], "ids": [ObjectId()]})[0] is None
