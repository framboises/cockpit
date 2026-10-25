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
    ], cameras_planned=[], cameras_models=[], cameras_meta=[])
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
    db["cameras_planned"].insert_one({"_id": ObjectId(), "name": "P1", "replaces": [A], "moves_camera_id": M})
    assert "deja remplacee par le projet P1" in C._planned_conflict(None, {"replaces": [A]})
    assert "deja reinstallee par le projet P1" in C._planned_conflict(None, {"moves_camera_id": M})
    assert C._planned_conflict(None, {"replaces": [B]}) is None
    # Reemploi : remplacee par P1, reinstallee par un autre projet
    assert C._planned_conflict(None, {"moves_camera_id": A}) is None


def test_reemploi_dans_les_references(db):
    p1, p2 = ObjectId(), ObjectId()
    db["cameras_planned"].docs = [{"_id": p1, "name": "P1", "replaces": [A]}, {"_id": p2, "name": "P2", "moves_camera_id": A}]
    refs = C._planned_refs(db["cameras_planned"].docs)
    assert refs[A]["removed_by"] == {"_id": str(p1), "name": "P1"}
    assert refs[A]["reused_by"] == {"_id": str(p2), "name": "P2"}


# --- catalogue des modeles ----------------------------------------------------
def _model(db, ref):
    import cameras_models
    return next(m for m in cameras_models.catalogue(db) if m["ref"] == ref)


def test_catalogue_initial_une_seule_fois(db):
    import cameras_models
    assert {m["ref"] for m in cameras_models.catalogue(db)} >= {"DS-2DP7D836", "iDS-2CD7A87G0"}
    db["cameras_models"].docs = []
    assert cameras_models.catalogue(db) == []  # vide par choix : pas de nouveau semis


def test_modele_multi_pose_la_disposition(db):
    m = _model(db, "DS-2DP7D836")
    chans = [{"no": 1, "optics": "fixe", "view": {"az": 12, "fov": 50, "range": 40}}]
    f, _u, err = C._planned_fields({"model_id": str(m["_id"])}, chans)
    assert err is None and f["model_id"] == m["_id"]
    assert [(c["no"], c["model"], c["optics"]) for c in f["channels"]] == [(1, "2dp-ptz", "mobile")] + [(i, "2dp-pano", "fixe") for i in range(2, 6)]
    assert f["channels"][0]["view"]["az"] == 12  # angle garde


def test_modele_simple_garde_les_canaux(db):
    m = _model(db, "iDS-2CD7A87G0")
    chans = [{"no": 1, "optics": "fixe"}, {"no": 2, "optics": "fixe", "model": "bullet-tele"}]
    f, _u, err = C._planned_fields({"model_id": str(m["_id"])}, chans)
    assert err is None and [c["model"] for c in f["channels"]] == ["bullet-wide", "bullet-tele"]
    assert C._planned_fields({"model_id": str(ObjectId())}, chans)[2] == "Modele inconnu"
    assert C._planned_fields({"ch": 1, "model": "inconnu"}, chans)[2] == "Modele inconnu"


def test_modele_saisi(db):
    import cameras_models
    f, err = cameras_models.clean({"brand": "Axis", "ref": "Q6135-LE", "multi": False,
                                   "lenses": [{"label": "PTZ", "optics": "mobile", "fov": "61,8", "range": 120}]}, None, {"ptz"})
    assert err is None and f["lenses"][0]["fov"] == 61.8 and f["lenses"][0]["k"].startswith("m")
    assert cameras_models.clean({"ref": "X", "lenses": []})[1] == "Au moins un objectif"
    assert "mobile ou fixe" in cameras_models.clean({"ref": "X", "lenses": [{"label": "a", "optics": "x", "fov": 1, "range": 1}]})[1]
    assert "8 canaux" in cameras_models.clean({"ref": "X", "multi": True,
                                               "lenses": [{"label": "a", "optics": "fixe", "fov": 90, "range": 9, "count": 5}] * 2})[1]


def test_motif_le_plus_precis(db):
    import cameras_models
    models = [{"_id": 1, "match": "2CD7", "lenses": []}, {"_id": 2, "match": "2CD7A87G0", "lenses": []}]
    assert cameras_models.model_for_device(models, "iDS-2CD7A87G0-XZHSY")["_id"] == 2
    assert cameras_models.model_for_device(models, "") is None


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
    assert D.devices_of(items[2]) == ["Hikvision iDS-2CD7A87G0 – fixe 2,8 mm"]
    # 10 canaux d'un multi-capteurs 1 + 4 : deux appareils
    items[0]["channels"] += [dict(c) for c in items[0]["channels"]]
    assert D.devices_of(items[0]) == ["Hikvision DS-2DP7D836 (1 PTZ + 4 objectifs fixes)"] * 2


def test_reemploi_dans_le_dossier(db):
    _planned(db)
    db["cameras_planned"].docs.append({"_id": ObjectId(), "name": "Reemploi 048", "status": "validee", "priority": 3,
                                        "lat": 47.95, "lng": 0.215, "moves_camera_id": A, "channels": [{"no": 1}]})
    items, _e = D.collect(db, statuses=["souhaitee", "validee"])
    p4, reuse = items[0], next(it for it in items if it["name"] == "Reemploi 048")
    assert p4["replaces"][0]["reused_by"]["rep"] == reuse["rep"]
    assert reuse["moves"]["removed_by"]["rep"] == "C1"
    assert D.kind_label(reuse) == "Réemploi d'une caméra existante déposée par C1"
    # Objectif de la camera deplacee deduit de son modele reel
    assert items[1]["channels"][0]["lens"] == "2dp-ptz"


def test_geodesie():
    x, y = D.lambert93(47.95248, 0.21907)
    assert abs(x - 492456.3) < 0.5 and abs(y - 6764998.7) < 1.0  # point de reference du dossier Porte du Houx
    assert D.cardinal(0) == "N" and D.cardinal(200) == "SSO" and D.cardinal(359) == "N"
    assert D.dms(47.5, "N", "S") == "47°30'00.0\" N"
    lat, lng = D.dest_point(47.95, 0.21, 90, 100)
    assert abs(D.distance_m(47.95, 0.21, lat, lng) - 100) < 0.5


def test_pdf(db, monkeypatch, tmp_path):
    _planned(db)
    monkeypatch.setattr(D, "_tile", lambda z, x, y: Image.new("RGB", (256, 256), (120, 140, 110)))
    pdf, n = D.build_pdf(db, {"statuses": ["souhaitee", "validee"], "author": "L. Arnault", "indice": "b"})
    assert n == 3 and pdf.startswith(b"%PDF")
    # couverture + plan d'ensemble + 3 fiches + recapitulatif
    assert b"/Count 6" in pdf
    # Fiche technique du modele en annexe : intercalaire + ses pages
    import cameras_models
    from reportlab.pdfgen import canvas as rl
    m = _model(db, "DS-2DP7D836")
    monkeypatch.setattr(cameras_models, "DATASHEET_DIR", str(tmp_path))
    sheet = rl.Canvas(cameras_models.datasheet_path(m["_id"]))
    sheet.drawString(50, 50, "Fiche"); sheet.showPage(); sheet.drawString(50, 50, "Fiche 2"); sheet.save()
    m["datasheet"] = {"name": "DS-2DP7D836.pdf", "size": 1}
    pdf, _n = D.build_pdf(db, {"statuses": ["souhaitee", "validee"], "datasheets": True})
    from pypdf import PdfReader
    import io
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 6 + 1 + 2
    assert D.build_pdf(db, {"statuses": ["commandee"], "ids": [ObjectId()]})[0] is None


def test_deplacement_impose_en_chaine(db):
    """A -> emplacement de B, B -> emplacement de M : chaine admise ; une camera
    deja reinstallee ailleurs n'est reprise qu'avec take_move."""
    import flask
    p1, p2 = ObjectId(), ObjectId()
    db["cameras_planned"].docs = [
        {"_id": p1, "name": "A chez B", "lat": 47.948, "lng": 0.21, "moves_camera_id": A, "replaces": [B], "channels": [{"no": 1}]},
        {"_id": p2, "name": "B chez M", "lat": 47.929, "lng": 0.1924, "moves_camera_id": B, "replaces": [M], "channels": [{"no": 1}]},
    ]
    assert C._planned_conflict(p1, db["cameras_planned"].docs[0]) is None
    assert C._planned_conflict(p2, db["cameras_planned"].docs[1]) is None
    app = flask.Flask(__name__)
    p3 = db["cameras_planned"].insert_one({"_id": ObjectId(), "name": "Nouveau", "lat": 47.95, "lng": 0.22, "channels": [{"no": 1}]}).inserted_id
    doc = db["cameras_planned"].find_one({"_id": p3})
    with app.test_request_context():
        r = C._planned_save(doc, {"moves_camera_id": A})
        assert r[1] == 409
        r = C._planned_save(doc, {"moves_camera_id": A}, take_move=True)
        assert r.get_json()["released"] == ["A chez B"]
    assert "moves_camera_id" not in db["cameras_planned"].find_one({"_id": p1})
    assert db["cameras_planned"].find_one({"_id": p3})["moves_camera_id"] == A
