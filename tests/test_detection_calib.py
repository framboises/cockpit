"""Calage image -> sol (detection_calib) sur une camera synthetique : 6 m de
haut, regard vers le nord incline de 15 degres, champ de 60 degres."""
import math

import detection_calib as C

CAM = {"lat": 47.95, "lng": 0.21}


def _image_of(x, y, h=6.0, tilt=math.radians(15), fov=math.radians(60)):
    """Projection d'un point du sol (metres est / nord de la camera) -> image 0-1."""
    # repere camera : axe optique vers le nord, incline vers le bas
    yc = y * math.cos(tilt) + h * math.sin(tilt)          # profondeur
    zc = -y * math.sin(tilt) + h * math.cos(tilt)         # vers le bas de l'image
    f = 0.5 / math.tan(fov / 2)
    return 0.5 + f * x / yc, 0.5 + f * zc / yc * (16 / 9)


def _pt(x, y):
    u, v = _image_of(x, y)
    lat, lng = C._geo(CAM, x, y)
    return {"img": {"x": u, "y": v}, "map": {"lat": lat, "lng": lng}}


def test_calage_et_projection():
    pts, err = C.clean_points([_pt(-10, 20), _pt(10, 20), _pt(-20, 60), _pt(20, 60), _pt(0, 40)])
    assert not err
    cal, err = C.fit(pts, CAM)
    assert not err and cal["err_m"] < 0.5
    u, v = _image_of(5, 35)
    pos = C.project(cal, u, v)
    x, y = C._enu(CAM, pos["lat"], pos["lng"])
    assert abs(x - 5) < 0.5 and abs(y - 35) < 0.5 and pos["dist_m"] == 35
    # Au-dessus de l'horizon : pas d'estimation
    assert C.project(cal, 0.5, 0.0) is None


def test_reperes_imprecis_et_erreur_de_controle():
    pts = [_pt(-10, 20), _pt(10, 20), _pt(-20, 60), _pt(20, 60), _pt(0, 40)]
    pts[4]["map"]["lat"] += 8 / C.M_PER_DEG_LAT          # repere mal clique de 8 m
    cal, _ = C.fit(C.clean_points(pts)[0], CAM)
    assert cal["errors"][4] > 4 and cal["err_m"] > 1


def test_validation_et_pied_du_cadre():
    assert C.clean_points([_pt(0, 20)] * 3)[1] == "quatre_reperes_minimum"
    assert C.clean_points([{"img": {"x": 2, "y": 0}, "map": {"lat": 1, "lng": 1}}] * 4)[1] == "repere_invalide"
    assert C.foot_point({"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.5}) == (0.5, 0.7)
    ep = {"ai": {"photo": "a.jpg"}, "photos": [{"photo": "a.jpg", "rect": {"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}}],
          "cover": {"rect": {"x": 0.9, "y": 0.9, "w": 0, "h": 0}}}
    assert C.episode_rect(ep)["x"] == 0.1
