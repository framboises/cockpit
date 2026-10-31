"""Cameras le long d'un axe Waze pour Looker (cameras.py, routes /api/cameras/le-long et /derniere-capture).

- Lecture seule de la derniere capture, memes cameras que la main courante : jamais la piste, jamais d'IP.
- Trace recu valide strictement (format, bornes, nombre de points).
"""
import pytest

import cameras as C


@pytest.mark.parametrize("texte", [None, "", "47.9,0.2", "47.9,0.2;abc", "47.9,0.2;95,0.2",
                                   "47.9,0.2;47.9,0.2,1", ";".join(["47.9,0.2"] * (C.LOOKER_POINTS_MAX + 1))])
def test_trace_refusee(texte):
    assert C.trace_looker(texte) is None


def test_trace_acceptee():
    assert C.trace_looker("47.9,0.2;47.91,0.21") == [(47.9, 0.2), (47.91, 0.21)]


def test_angle_de_vue():
    # camera a l'origine, vue vers l'est (90 deg), ouverture 60, portee 200 m
    vue = {"az": 90, "fov": 60, "range": 200}
    assert C._voit(vue, [(150.0, 0.0)])          # a l'est, a portee
    assert not C._voit(vue, [(0.0, 150.0)])      # au nord
    assert not C._voit(vue, [(300.0, 0.0)])      # trop loin
    assert not C._voit({"az": None}, [(150.0, 0.0)])


def test_cameras_le_long_sans_ip_et_tri(monkeypatch):
    liste = [
        {"_id": "a" * 24, "name": "Loin", "ip": "10.0.0.1", "location": "", "lat": 47.95, "lon": 0.25, "views": []},
        {"_id": "b" * 24, "name": "Proche", "ip": "10.0.0.2", "location": "Parking", "lat": 47.9001, "lon": 0.205,
         "views": [{"no": 1, "name": "Canal 1", "az": 0, "fov": 10, "range": 50}]},
        {"_id": "c" * 24, "name": "Voit", "ip": "10.0.0.3", "location": "", "lat": 47.898, "lon": 0.205,
         "views": [{"no": 2, "name": "Route", "az": 0, "fov": 40, "range": 400}]},
        {"_id": "d" * 24, "name": "Sans position", "ip": "10.0.0.4", "location": "", "lat": None, "lon": None, "views": []},
    ]
    monkeypatch.setattr(C, "capture_camera_list", lambda: liste)
    monkeypatch.setattr(C, "_capture_le", lambda cam_id, no: None)
    out = C.cameras_le_long([(47.9, 0.2), (47.9, 0.21)])
    assert [c["nom"] for c in out] == ["Voit", "Proche"]
    assert out[0]["voit"] is True and out[0]["canal"] == 2
    assert all("ip" not in c for c in out) and "10.0.0" not in str(out)


def test_derniere_capture_jamais_la_piste(monkeypatch):
    # latest_snapshot_path passe par capturable_doc (filtre de la main courante, piste exclue)
    monkeypatch.setattr(C, "capturable_doc", lambda cam_id: None)
    assert C.latest_snapshot_path("e" * 24, 1) is None
