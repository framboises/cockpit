"""
detection_calib.py - Position estimee d'une cible d'intrusion par calage
image -> sol (homographie), par canal de camera fixe.

L'operateur clique 4 reperes AU SOL ou plus (pied de poteau, angle bas d'un
batiment, marquage) sur une photo du canal et les memes sur la carte. Une
homographie relie alors l'image au plan du sol. Elle contient la hauteur, l'inclinaison et la focale
de la camera : rien a saisir. Le point au sol de la cible = milieu du BAS du
cadre de la camera (les pieds, les roues), projete sur la carte.

Limites : sol suppose plat (talus, tribunes : erreur), pieds caches par une
cloture ou une haie -> point place trop loin ; camera motorisee, zoom change
ou camera deplacee : calage a refaire. Au-dela de MAX_DIST_M ou au-dessus de
l'horizon : pas d'estimation (on garde la position de la camera).

Stockage `detection_calib` : _id "<camera_id>:<canal>", points
[{img: {x, y} (0-1), map: {lat, lng}}], origin {lat, lng}, H (3x3, image ->
metres est / nord autour de origin), w_sign, err_m (erreur moyenne aux
reperes, laissant chaque repere de cote a partir de 5), errors[], photo, by, at.
"""
import math
from datetime import datetime, timezone

MIN_POINTS = 4
MAX_POINTS = 12
MAX_DIST_M = 500
M_PER_DEG_LAT = 111320.0


def _enu(origin, lat, lng):
    k = M_PER_DEG_LAT * math.cos(math.radians(origin["lat"]))
    return (lng - origin["lng"]) * k, (lat - origin["lat"]) * M_PER_DEG_LAT


def _geo(origin, x, y):
    k = M_PER_DEG_LAT * math.cos(math.radians(origin["lat"]))
    return origin["lat"] + y / M_PER_DEG_LAT, origin["lng"] + x / k


def _solve(src, dst):
    """Homographie src -> dst par DLT normalise (moindres carres des 4 points et plus)."""
    import numpy as np

    def norm(pts):
        p = np.asarray(pts, dtype=float)
        c = p.mean(axis=0)
        d = np.sqrt(((p - c) ** 2).sum(axis=1)).mean() or 1.0
        s = math.sqrt(2) / d
        return np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1]])

    ts, td = norm(src), norm(dst)
    rows = []
    for (u, v), (x, y) in zip(src, dst):
        a = ts @ [u, v, 1]
        b = td @ [x, y, 1]
        u, v, x, y = a[0] / a[2], a[1] / a[2], b[0] / b[2], b[1] / b[2]
        rows.append([-u, -v, -1, 0, 0, 0, x * u, x * v, x])
        rows.append([0, 0, 0, -u, -v, -1, y * u, y * v, y])
    _, s, vt = np.linalg.svd(np.asarray(rows))
    h = vt[-1].reshape(3, 3)
    h = np.linalg.inv(td) @ h @ ts
    if abs(h[2, 2]) > 1e-12:
        h = h / h[2, 2]
    return h


def _apply(h, u, v):
    w = h[2][0] * u + h[2][1] * v + h[2][2]
    if abs(w) < 1e-12:
        return None
    return (h[0][0] * u + h[0][1] * v + h[0][2]) / w, (h[1][0] * u + h[1][1] * v + h[1][2]) / w, w


def clean_points(raw):
    out = []
    for p in raw or []:
        try:
            u, v = float(p["img"]["x"]), float(p["img"]["y"])
            lat, lng = float(p["map"]["lat"]), float(p["map"]["lng"])
        except (KeyError, TypeError, ValueError):
            return None, "repere_invalide"
        if not (0 <= u <= 1 and 0 <= v <= 1 and -90 <= lat <= 90 and -180 <= lng <= 180):
            return None, "repere_invalide"
        out.append({"img": {"x": u, "y": v}, "map": {"lat": lat, "lng": lng}})
    if len(out) < MIN_POINTS:
        return None, "quatre_reperes_minimum"
    if len(out) > MAX_POINTS:
        return None, "trop_de_reperes"
    return out, None


def fit(points, camera=None):
    """Calage a partir des reperes nettoyes. Origine : la camera (sinon le
    centre des reperes). Retourne ({origin, H, w_sign, err_m, errors}, err)."""
    origin = dict(camera) if camera and camera.get("lat") is not None else {
        "lat": sum(p["map"]["lat"] for p in points) / len(points),
        "lng": sum(p["map"]["lng"] for p in points) / len(points)}
    src = [(p["img"]["x"], p["img"]["y"]) for p in points]
    dst = [_enu(origin, p["map"]["lat"], p["map"]["lng"]) for p in points]
    try:
        h = _solve(src, dst)
    except Exception:
        return None, "calage_impossible"
    ws = [(_apply(h.tolist(), u, v) or (0, 0, 0))[2] for u, v in src]
    if not all(ws) or len({w > 0 for w in ws}) != 1:
        return None, "reperes_incoherents"     # reperes de part et d'autre de l'horizon
    errors = []
    for i, ((u, v), (x, y)) in enumerate(zip(src, dst)):
        hh = h
        if len(points) > MIN_POINTS:            # controle : calage sans ce repere
            try:
                hh = _solve(src[:i] + src[i + 1:], dst[:i] + dst[i + 1:])
            except Exception:
                hh = h
        r = _apply(hh.tolist(), u, v)
        errors.append(round(math.hypot(r[0] - x, r[1] - y), 1) if r else None)
    good = [e for e in errors if e is not None]
    return {"origin": {"lat": origin["lat"], "lng": origin["lng"]}, "H": h.tolist(), "w_sign": 1 if ws[0] > 0 else -1,
            "err_m": round(sum(good) / len(good), 1) if good and len(points) > MIN_POINTS else None,
            "errors": errors if len(points) > MIN_POINTS else [None] * len(points)}, None


def project(cal, u, v):
    """Point image (0-1) -> {lat, lng, dist_m} sur le sol, ou None (au-dessus
    de l'horizon, ou trop loin pour etre credible)."""
    if not cal or not cal.get("H"):
        return None
    r = _apply(cal["H"], u, v)
    if not r or (r[2] > 0) != (cal.get("w_sign", 1) > 0):
        return None
    dist = math.hypot(r[0], r[1])
    if dist > MAX_DIST_M:
        return None
    lat, lng = _geo(cal["origin"], r[0], r[1])
    return {"lat": lat, "lng": lng, "dist_m": round(dist)}


def foot_point(rect):
    """Point au sol d'un cadre {x, y, w, h} (0-1) : milieu du bas."""
    try:
        u = float(rect["x"]) + float(rect["w"]) / 2
        v = float(rect["y"]) + float(rect["h"])
    except (KeyError, TypeError, ValueError):
        return None
    return max(0.0, min(1.0, u)), max(0.0, min(1.0, v))


def episode_rect(ep):
    """Cadre de la cible sur la photo qui a decide (sinon la couverture)."""
    dp = (ep.get("ai") or {}).get("photo")
    for p in ep.get("photos") or []:
        if dp and p.get("photo") == dp and p.get("rect"):
            return p["rect"]
    return (ep.get("cover") or {}).get("rect")


def get(db, camera_id, channel):
    return db["detection_calib"].find_one({"_id": "%s:%s" % (camera_id, channel)})


def estimate(db, ep, cal=None):
    """Position estimee de la cible d'un episode : {lat, lng, dist_m, err_m}
    ou None (canal non cale, pas de cadre, hors du sol cale)."""
    cal = cal or get(db, ep.get("camera_id"), ep.get("channel"))
    if not cal:
        return None
    fp = foot_point(episode_rect(ep) or {})
    if not fp:
        return None
    pos = project(cal, fp[0], fp[1])
    if not pos:
        return None
    # Incertitude : erreur aux reperes (5 m sans controle), plus 5 % de la distance
    pos["err_m"] = int(round((cal.get("err_m") or 5) + 0.05 * pos["dist_m"]))
    return pos


def save(db, camera_id, channel, points, result, photo, by):
    doc = dict(result, _id="%s:%s" % (camera_id, channel), camera_id=str(camera_id), channel=int(channel),
               points=points, photo=photo, by=by, at=datetime.now(timezone.utc))
    db["detection_calib"].replace_one({"_id": doc["_id"]}, doc, upsert=True)
    return doc
