"""
Capture IA : paires de photos prises a intervalle regulier sur les cameras
ayant l'usage "Capture IA" (page IA), analysees par l'IA locale (service
IntrusionVerifier, GPU). Premiere analyse : le trafic (bouchons).

Lance par la tache planifiee Windows "Cockpit - Capture IA" TOUTES LES MINUTES
(scripts/capture_ia.bat, installee par scripts/install_capture_ia_task.ps1),
JAMAIS par Cockpit (pas de tache cachee dans l'application). Chaque camera a
son rythme (usage : interval_min) : le script ne prend que celles qui sont
dues et dont le planning (groupes de la page IA) est actif.

Pour une camera (trafic) :
  1. rappel du preset choisi (facultatif), 8 s pour que le dome arrive ;
  2. DEUX PAIRES d'images : 0 s / 1 s puis `gap_s` / `gap_s` + 1 s (8 par
     defaut). Flux video RTSP quand le port 554 repond (ecarts exacts), sinon
     photos enchainees (Antares, 4G : ~2 s par photo, ecarts reels gardes).
     Images pleine taille : on ne touche pas aux reglages des cameras ;
  3. detection des vehicules sur les 4 images (service IA, /detect) ;
  4. chaque vehicule de la 1re image de chaque paire : sa VITESSE en
     longueurs de vehicule par seconde, mesuree sur la paire (meme vehicule
     a 1 s : il doit RESSEMBLER, couleur), puis verifiee sur l'autre paire.
     ARRETE (immobile et toujours la), LENT, ARRET BREF (immobile puis
     reparti : cede le passage, compte comme fluide) ou ROULE ;
  5. par zone (page IA ; sans zone : l'image entiere) : arretes et lents sur
     les deux paires -> etat fluide / ralenti / bouchon.
Les mesures d'avant le 10/10/2026 (method absente) sont des paires A/B a
`gap_s` d'ecart : arrete / deplace / parti, arrive / suivi sur B.
Historique : ia_capture_samples (photos sous hik_images/captures, gardees
CAPTURE_RETENTION_DAYS jours sauf celles annotees, qui servent a
l'entrainement). Etat courant : ia_capture_state.

    python capture_ia.py              # un passage (TITAN_ENV=prod)
    python capture_ia.py --force      # toutes les cameras, meme non dues
    python capture_ia.py --dry-run    # capture et analyse, n'ecrit rien en base
    python capture_ia.py --stats-backfill   # rattrapage des statistiques (ia_capture_stats)
"""
import argparse
import json
import logging
import math
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
# Flux RTSP (OpenCV / FFMPEG) : TCP, sans bavardage (l'URL porte le mot de passe)
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")

from PIL import Image  # noqa: E402
from pymongo import MongoClient  # noqa: E402

import camera_secrets  # noqa: E402
import cameras  # noqa: E402
import detection_arming  # noqa: E402

log = logging.getLogger("capture_ia")

HIK_IMAGE_DIR = os.getenv("HIK_IMAGE_DIR", "E:/TITAN/production/hik_images")
CAPTURE_DIR = os.path.join(HIK_IMAGE_DIR, "captures")
LOG_DIR = os.path.join(ROOT, "logs")
LOCK_PATH = os.path.join(LOG_DIR, "capture_ia.lock")
DETECT_URL = os.getenv("CAPTURE_IA_DETECT_URL", "http://127.0.0.1:4011/detect")
CAPTURE_RETENTION_DAYS = int(os.getenv("CAPTURE_RETENTION_DAYS", "30"))
PRESET_WAIT_S = 8
MAX_WORKERS = 8
PHOTO_MAX_W, PHOTO_QUALITY = 1920, 80
THUMB_W = 480
DUE_MARGIN_S = 20          # la tache tourne chaque minute : tolerance d'avance
VEHICLES = {"car", "truck", "bus", "motorcycle"}

# Etat d'une zone a partir des vehicules de la photo A
MIN_VEHICLES = 3           # en dessous : fluide quoi qu'il arrive
STOPPED_JAM = 0.6          # part d'arretes -> bouchon
SLOW_RATIO = 0.6           # part d'arretes + deplaces -> ralenti
DENSE_RATIO = 0.3          # part d'arretes + lents -> dense (beaucoup de monde, ca roule encore)
DENSE_MIN_VEH = 6          # ... et au moins 6 vehicules par image (sinon : 1 lent sur 3 = "dense")
STATE_ORDER = {"inconnu": -1, "fluide": 0, "dense": 1, "ralenti": 2, "bouchon": 3}
LEVELS = ("dense", "ralenti", "bouchon")
LEVEL_MIN_VOTES = 2        # avis de chaque cote d'un seuil pour l'apprendre

# Deux paires (essai du 10/10/2026 sur 22 mesures Antares + Technoparc).
# Vitesse en longueurs de vehicule par seconde : une voiture qui circule fait
# 0,7 a 2, une garee 0,00. Une voiture au loin qui arrive de face parait lente
# (0,4-0,6) : seuil "lent" a 0,3 ; un cede-le-passage est un arret bref.
PAIR_S = 1.0               # ecart dans une paire
STOP_V, SLOW_V = 0.15, 0.3
RTSP_PORT = 554
STOPPED, SLOW = {"arrete"}, {"deplace", "lent"}   # le reste roule (parti, roule, bref)


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "capture_ia-%s.log" % datetime.now().strftime("%Y%m%d"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S",
                        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)])


def _acquire_lock():
    """Verrou exclusif non bloquant (libere par l'OS si le process meurt)."""
    os.makedirs(LOG_DIR, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT)
    try:
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        os.close(fd)
        return None
    return fd


def _get_db():
    env = os.getenv("TITAN_ENV", "dev").strip().lower()
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=10000)
    return client["titan" if env in ("prod", "production") else "titan_dev"]


def ensure_indexes(db):
    db["ia_capture_samples"].create_index([("camera_id", 1), ("channel", 1), ("at", -1)])
    db["ia_capture_samples"].create_index([("at", -1)])
    db["ia_capture_samples"].create_index([("review.score", -1), ("at", -1)])  # galerie "A revoir"
    db["ia_capture_zones"].create_index([("camera_id", 1), ("channel", 1)])
    db["ia_capture_stats"].create_index([("camera_id", 1), ("channel", 1), ("zone", 1), ("slot", 1)])
    db["ia_capture_stats"].create_index([("camera_id", 1), ("channel", 1), ("dow", 1), ("hm", 1)])


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def capture_channels(db):
    """(appareil, canal, usage) des canaux en Capture IA, actifs et pilotables."""
    out = []
    for d in db["cockpit_cameras"].find({"channels.roles.type": "capture", "enabled": {"$ne": False}}):
        if cameras.is_piste_device(d) or d.get("brand") not in cameras.CONTROLLABLE_BRANDS:
            continue
        for ch in d.get("channels") or []:
            if ch.get("enabled") is False:
                continue
            for r in ch.get("roles") or []:
                if r.get("type") == "capture":
                    out.append((d, ch, r))
    return out


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Analyse
# ---------------------------------------------------------------------------
def _iou(a, b):
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1, y1 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return inter / (a["w"] * a["h"] + b["w"] * b["h"] - inter + 1e-9)


def _center(b):
    return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2


# Ressemblance de couleur exigee pour dire "meme vehicule" entre A et B.
# Sans elle, l'appariement par la seule position reliait deux voitures
# DIFFERENTES (giratoire Antares, 10/10/2026 : une rouge a une grise, une
# blanche a une noire) et inventait des "ralenti". Mesure sur 16 paires :
# vraies voitures garees 0,76-0,97 ; voitures differentes 0,13-0,80 (deux
# noires : 0,78). Meme place : 0,6 suffit ; deplace : 0,85.
SAME_SPOT_COLOR = 0.6
MOVED_COLOR = 0.85


def _color_sig(im, box):
    """Histogramme couleur 4x4x4 du coeur du cadre (60 % central, moins de
    route et de fond)."""
    import numpy as np
    W, H = im.size
    x0, y0 = box["x"] * W, box["y"] * H
    w, h = box["w"] * W, box["h"] * H
    l, t = int(x0 + 0.2 * w), int(y0 + 0.2 * h)
    c = im.crop((l, t, max(int(x0 + 0.8 * w), l + 1), max(int(y0 + 0.8 * h), t + 1))).resize((24, 24))
    q = np.asarray(c, dtype=np.int32) // 64
    hist = np.bincount((q[..., 0] * 16 + q[..., 1] * 4 + q[..., 2]).ravel(), minlength=64).astype(np.float32)
    return hist / hist.sum()


def _pair_images(shots):
    """Photos A et B (PIL) d'une paire, None si l'une manque (position seule)."""
    try:
        return tuple(Image.open(os.path.join(HIK_IMAGE_DIR, s["photo"])).convert("RGB") for s in shots[:2])
    except Exception as e:
        log.warning("Photos de la paire illisibles (%s) : appariement sur la position seule", e)
        return None


def match_vehicles(va, vb, ims=None):
    """Statut de chaque vehicule de A : arrete / deplace / parti ; ceux de B
    sans correspondant : arrive. ims = (photo A, photo B) en PIL : exige en
    plus que les deux cadres se ressemblent (couleur) ; sans photos,
    position seule (ancien comportement)."""
    import numpy as np
    sig_b = [_color_sig(ims[1], b["box"]) for b in vb] if ims else None

    def sim(sa, j):
        return 1.0 if sa is None else float(np.minimum(sa, sig_b[j]).sum())

    used = set()
    for a in va:
        sa = _color_sig(ims[0], a["box"]) if ims else None
        a.pop("sim", None)
        best, best_iou = None, 0.0
        for j, b in enumerate(vb):
            if j in used or sim(sa, j) < SAME_SPOT_COLOR:
                continue
            i = _iou(a["box"], b["box"])
            if i > best_iou:
                best, best_iou = j, i
        if best is not None and best_iou >= 0.5:
            a["status"] = "arrete"
            used.add(best)
            if ims:
                a["sim"] = round(sim(sa, best), 2)
            continue
        # Cadre voisin dans B (moins de 1,5 fois sa taille). Le cadre d'un
        # petit vehicule gare "tremble" de quelques pixels d'une photo a
        # l'autre : moins d'un tiers de sa taille = toujours arrete.
        ax, ay = _center(a["box"])
        size = max(a["box"]["w"], a["box"]["h"])
        near = []
        for j, b in enumerate(vb):
            if j in used:
                continue
            dist = ((ax - _center(b["box"])[0]) ** 2 + (ay - _center(b["box"])[1]) ** 2) ** 0.5
            s = sim(sa, j)
            if dist < 0.35 * size and s >= SAME_SPOT_COLOR or dist <= 1.5 * size and s >= MOVED_COLOR:
                near.append((j, dist, s))
        if near:
            j, dist, s = min(near, key=lambda x: x[1])
            a["status"] = "arrete" if dist < 0.35 * size else "deplace"
            used.add(j)
            if ims:
                a["sim"] = round(s, 2)
        else:
            a["status"] = "parti"
    for j, b in enumerate(vb):
        b["status"] = "suivi" if j in used else "arrive"
    return va, vb


# "Meme place" n'est cru que si l'IMAGE des deux cadres est quasi identique
# (correlation des pixels, chacun son cadre). Sans cela, une voiture qui roule
# remplacee par une autre au meme endroit passait pour immobile (Antares
# 10/10/2026 16:31 : voiture aux velos -> C3 grise, "arret bref"). Mesure :
# voitures garees 0,88-0,99 ; faux immobiles d'Antares -0,26-0,70.
STILL_NCC = 0.8


def _gray(im, box):
    import numpy as np
    W, H = im.size
    l, t = int(box["x"] * W), int(box["y"] * H)
    r = (l, t, max(int((box["x"] + box["w"]) * W), l + 2), max(int((box["y"] + box["h"]) * H), t + 2))
    a = np.asarray(im.crop(r).convert("L").resize((32, 32)), dtype=np.float32).ravel()
    return a - a.mean()


def _ncc(ga, gb):
    """Correlation des pixels de deux cadres (niveaux de gris, 32x32)."""
    import numpy as np
    return float((ga * gb).sum() / (np.sqrt((ga * ga).sum() * (gb * gb).sum()) + 1e-6))


def _track(v0, v1, im0, im1, dt):
    """Pour chaque vehicule de v0 : (indice dans v1, vitesse en longueurs/s,
    ressemblance) du meme vehicule dans v1, ou None. Meme vehicule = meme
    place (couleur >= SAME_SPOT_COLOR ET image du cadre inchangee,
    STILL_NCC) ou cadre voisin qui ressemble (>= MOVED_COLOR), a moins de 3
    longueurs par seconde ; le plus proche. Vitesse = deplacement du centre."""
    import numpy as np
    s1 = [_color_sig(im1, b["box"]) for b in v1]
    g1 = {}
    used, out = set(), []
    for a in v0:
        sa = _color_sig(im0, a["box"])
        ga = None
        ax, ay = _center(a["box"])
        size = max(a["box"]["w"], a["box"]["h"])
        best = None
        for j, b in enumerate(v1):
            if j in used:
                continue
            bx, by = _center(b["box"])
            dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
            if dist > 3.0 * size * dt:
                continue
            sim = float(np.minimum(sa, s1[j]).sum())
            if _iou(a["box"], b["box"]) >= 0.5 or dist < 0.35 * size:
                # Meme place : il faut en plus que ce soit la meme image
                if sim < SAME_SPOT_COLOR:
                    continue
                # (meme rectangle sur les deux images : vehicule immobile dont
                # le cadre tremble ; ou chacun son cadre : il avance au pas)
                if ga is None:
                    ga, ga_in1 = _gray(im0, a["box"]), _gray(im1, a["box"])
                    same_rect = _ncc(ga, ga_in1)
                if j not in g1:
                    g1[j] = _gray(im1, b["box"])
                if max(same_rect, _ncc(ga, g1[j])) < STILL_NCC:
                    continue
            elif sim < MOVED_COLOR:
                continue
            if best is None or dist < best[1]:
                best = (j, dist, sim)
        if best:
            used.add(best[0])
            out.append((best[0], best[1] / size / max(dt, 0.1), best[2]))
        else:
            out.append(None)
    return out


def classify_pair(vs, ims, t, i, j, k):
    """Vehicules de l'image i : vitesse sur la paire (i, j), verifiee sur
    l'image k de l'autre paire. Immobile sur la paire : ARRETE s'il est encore
    la (quasi immobile) en k, LENT s'il n'a que peu avance (file qui
    avance au pas), sinon ARRET BREF (cede le passage). Pas retrouve sur la
    paire : il roulait trop vite pour rester dans le voisinage -> ROULE.
    Retourne aussi les vehicules de l'image j : le statut de leur jumeau de
    l'image i, "autre" s'ils n'y sont relies a aucun."""
    short = _track(vs[i], vs[j], ims[i], ims[j], t[j] - t[i])
    cross = _track(vs[i], vs[k], ims[i], ims[k], abs(t[k] - t[i]))
    out = []
    second = [{key: val for key, val in b.items() if key not in ("status", "sim", "speed", "move")} for b in vs[j]]
    for b in second:
        b["status"] = "autre"
    dt = max(t[j] - t[i], 0.1)
    for a, s, c in zip(vs[i], short, cross):
        v = {key: val for key, val in a.items() if key not in ("status", "sim", "speed", "move")}
        if s is None:
            v["status"], v["speed"] = "roule", None
        else:
            sp = s[1]
            if sp < STOP_V:
                cs = c[1] if c else None
                v["status"] = "arrete" if cs is not None and cs < STOP_V else \
                    "lent" if cs is not None and cs < SLOW_V else "bref"
            else:
                v["status"] = "lent" if sp < SLOW_V else "roule"
            v["speed"], v["sim"] = round(sp, 2), round(s[2], 2)
            # Deplacement du centre (fractions de l'image par seconde) : le
            # SENS de circulation, pour le rattacher a un itineraire trace
            (ax, ay), (bx, by) = _center(a["box"]), _center(vs[j][s[0]]["box"])
            v["move"] = [round((bx - ax) / dt, 4), round((by - ay) / dt, 4)]
            second[s[0]].update(status=v["status"], speed=v["speed"], move=v["move"])
        out.append(v)
    return out, second


def _in_polygon(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0 + 1e-12) + x0:
            inside = not inside
    return inside


# ITINERAIRES TRACES sur la photo (zones kind "itineraire" : `line` = points
# du depart a l'arrivee, dans le sens de circulation ; `waze_route` facultatif).
# Un vehicule appartient au trace le plus proche s'il est a moins d'environ
# sa taille de la ligne ET, quand il bouge, s'il va dans le sens du trace
# (deplacement mesure sur la paire). Deux sens colles sont ainsi separes.
ASPECT = 16 / 9            # images 16:9 : distances sans deformation
TRACE_DIST = 0.7           # x la taille du vehicule (+ TRACE_MARGIN)
TRACE_MARGIN = 0.01
TRACE_COS = -0.2           # en dessous : il roule en sens inverse


def _trace_fit(v, line):
    """(distance, sens_ok) du vehicule au trace, en unites corrigees de
    l'aspect ; None si la ligne est invalide."""
    if not line or len(line) < 2:
        return None
    cx, cy = _center(v["box"])
    px, py = cx * ASPECT, cy
    best = None
    for (x0, y0), (x1, y1) in zip(line[:-1], line[1:]):
        ax, ay, bx, by = x0 * ASPECT, y0, x1 * ASPECT, y1
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        u = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        d = ((px - ax - u * dx) ** 2 + (py - ay - u * dy) ** 2) ** 0.5
        if best is None or d < best[0]:
            best = (d, dx, dy)
    d, dx, dy = best
    ok = True
    mv = v.get("move")
    if mv and (v.get("speed") or 0) >= STOP_V:
        mx, my = mv[0] * ASPECT, mv[1]
        n = ((mx * mx + my * my) * (dx * dx + dy * dy)) ** 0.5
        ok = n == 0 or (mx * dx + my * dy) / n >= TRACE_COS
    return d, ok


def assign_traces(traces, vehicles):
    """{id(vehicule): id de zone du trace} pour les vehicules proches d'un trace."""
    out = {}
    for v in vehicles:
        size = max(v["box"]["w"] * ASPECT, v["box"]["h"])
        best = None
        for z in traces:
            f = _trace_fit(v, z.get("line") or [])
            if f and f[1] and f[0] <= TRACE_DIST * size + TRACE_MARGIN and (best is None or f[0] < best[0]):
                best = (f[0], str(z["_id"]))
        if best:
            out[id(v)] = best[1]
    return out


def measure_targets(zones, vehicles):
    """[(cle, nom, kind, appartient(v), seuils appris)] des zones qui donnent
    l'etat du trafic : zones "trafic" (polygone) et itineraires traces ; sans
    aucune : l'image entiere."""
    out = []
    for z in zones:
        if (z.get("kind") or "trafic") == "trafic":
            poly = z.get("polygon") or []
            out.append((str(z["_id"]), z.get("name", ""), "trafic",
                        lambda v, poly=poly: _in_polygon(_center(v["box"]), poly), z.get("levels")))
    traces = [z for z in zones if z.get("kind") == "itineraire"]
    if traces:
        own = assign_traces(traces, vehicles)
        for z in traces:
            zid = str(z["_id"])
            out.append((zid, z.get("name", ""), "itineraire", lambda v, zid=zid: own.get(id(v)) == zid, z.get("levels")))
    return out or [("image", "Image entiere", "trafic", lambda v: True, None)]


def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo = int(k)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (k - lo)


def learn_levels(db, zone_id):
    """Seuils de VEHICULES (moyenne par image dans la zone) de chaque niveau,
    appris des avis des operateurs sur CETTE zone / ce trace : le seuil d'un
    niveau est a mi-chemin entre les avis en dessous (80e centile) et les
    avis a ce niveau ou au-dessus (20e centile), avec au moins
    LEVEL_MIN_VOTES avis de chaque cote. Garde sur la zone (`levels`)."""
    zid = str(zone_id)
    votes = []
    for s in db["ia_capture_samples"].find({"zone_votes." + zid: {"$exists": True}}, {"zone_votes." + zid: 1}):
        v = (s.get("zone_votes") or {}).get(zid) or {}
        if v.get("state") in STATE_ORDER and v.get("veh") is not None:
            votes.append((STATE_ORDER[v["state"]], float(v["veh"]), v))
    levels = {"votes": len(votes), "at": datetime.now(timezone.utc),
              "by_state": {k: sum(1 for r, _x, _v in votes if r == STATE_ORDER[k]) for k in ("fluide",) + LEVELS}}
    prev = 0.0
    for lv in LEVELS:
        r = STATE_ORDER[lv]
        below = [x for rr, x, _v in votes if rr < r]
        above = [x for rr, x, _v in votes if rr >= r]
        if len(below) >= LEVEL_MIN_VOTES and len(above) >= LEVEL_MIN_VOTES:
            t = round(max(prev, (_pct(below, 0.8) + _pct(above, 0.2)) / 2), 1)
            levels[lv] = prev = t
    # Avis retrouves avec ces seuils (etat recalcule de la mesure votee)
    hit = 0
    for _r, x, v in votes:
        st = _apply_levels(v.get("state_motion") or "fluide", x, levels)
        hit += st == v["state"]
    levels["agree"] = hit
    db["ia_capture_zones"].update_one({"_id": zone_id}, {"$set": {"levels": levels}})
    return levels


def _count_state(veh, levels):
    st = "fluide"
    for lv in LEVELS:
        if levels and levels.get(lv) is not None and veh >= levels[lv]:
            st = lv
    return st


def _apply_levels(state_motion, veh, levels):
    """Etat final : le pire du mouvement (arretes / lents) et du nombre de
    vehicules (seuils appris)."""
    cs = _count_state(veh, levels)
    return max(state_motion, cs, key=lambda s: STATE_ORDER.get(s, -1))


def zone_metrics(zone_poly, va, vb, pred=None, levels=None):
    def inz(v):
        if pred is not None:
            return pred(v)
        return zone_poly is None or _in_polygon(_center(v["box"]), zone_poly)
    a = [v for v in va if inz(v)]
    b = [v for v in vb if inz(v)]
    m = {
        "n": len(a), "n_b": len(b),
        "arretes": sum(1 for v in a if v["status"] in STOPPED),
        "deplaces": sum(1 for v in a if v["status"] in SLOW),
        "partis": sum(1 for v in a if v["status"] not in STOPPED | SLOW),
        "brefs": sum(1 for v in a if v["status"] == "bref"),
        "arrives": sum(1 for v in b if v["status"] == "arrive"),
    }
    # Deux paires : les vehicules de la 2e paire sont mesures aussi (l'etat
    # porte sur les deux) ; ancienne paire A/B : B ne sert qu'aux arrivees
    measured = a + [v for v in b if v.get("status") not in ("arrive", "suivi")]
    n = len(measured)
    pairs = 2 if any("speed" in v for v in va + vb) else 1
    stopped = sum(1 for v in measured if v["status"] in STOPPED)
    slow = sum(1 for v in measured if v["status"] in SLOW)
    if n < MIN_VEHICLES * pairs:  # moins de 3 vehicules par image en moyenne
        state = "fluide"
    elif stopped / n >= STOPPED_JAM:
        state = "bouchon"
    elif (stopped + slow) / n >= SLOW_RATIO:
        state = "ralenti"
    elif (stopped + slow) / n >= DENSE_RATIO and n / pairs >= DENSE_MIN_VEH:
        state = "dense"
    else:
        state = "fluide"
    # Nombre de vehicules par image (deux paires : moyenne) et seuils appris
    m["veh"] = round(n / pairs, 1)
    m["state_motion"] = state
    m["state_count"] = _count_state(m["veh"], levels)
    m["state"] = _apply_levels(state, m["veh"], levels)
    return m


def parking_metrics(zone, va, at, history, interval_min):
    """Zone de stationnement interdit : vehicules ARRETES dans la zone (meme
    cadre sur les 2 photos) et depuis quand ils sont a cette place, en
    remontant les mesures precedentes tant qu'un cadre quasi identique y est
    (IoU >= 0,5). Un trou de plus de 3 rythmes (hors planning, panne) arrete
    la remontee. Precision : le rythme des mesures."""
    poly = zone.get("polygon") or []
    max_min = int(zone.get("max_min") or 10)
    gap_max = timedelta(minutes=max(10, 3 * interval_min))
    at = at.replace(tzinfo=None) if at.tzinfo else at
    parked = []
    inside = [v for v in va if _in_polygon(_center(v["box"]), poly)]
    for v in inside:
        if v.get("status") != "arrete":
            continue
        since, last = at, at
        for s in history:
            s_at = s["at"].replace(tzinfo=None) if s["at"].tzinfo else s["at"]
            if s_at >= at:
                continue
            if last - s_at > gap_max:
                break
            if not any(_iou(v["box"], w["box"]) >= 0.5 for w in s.get("vehicles_a") or []):
                break
            since, last = s_at, s_at
        minutes = round((at - since).total_seconds() / 60)
        parked.append({"box": v["box"], "class": v.get("class"), "since": since.replace(tzinfo=timezone.utc),
                       "minutes": minutes, "over": minutes >= max_min})
        v["parked_min"] = minutes
    return {"name": zone.get("name", ""), "kind": "stationnement", "max_min": max_min,
            "n": len(inside), "parked": parked, "over": sum(1 for p in parked if p["over"]),
            "state": "fluide"}


def compute_zones(zones, va, vb, at, history, interval_min):
    """Mesures par zone et etat du trafic d'une paire. Zones "trafic" (sans
    zone trafic : l'image entiere) et "stationnement". history = mesures
    precedentes (les plus recentes d'abord), pour la duree de stationnement."""
    zm = {}
    for key, name, kind, pred, levels in measure_targets(zones, list(va) + list(vb)):
        zm[key] = {"name": name, "kind": kind, **zone_metrics(None, va, vb, pred, levels)}
    for z in zones:
        if z.get("kind") == "stationnement":
            zm[str(z["_id"])] = parking_metrics(z, va, at, history, interval_min)
    state = max((m["state"] for m in zm.values() if m.get("kind") in ("trafic", "itineraire")), key=lambda s: STATE_ORDER[s])
    return zm, state


def recompute_zones(db, camera_id, channel, days=14):
    """Recalcule les mesures deja prises d'un canal avec ses zones actuelles
    (appele quand une zone est dessinee, modifiee ou supprimee) : les
    vehicules et leurs statuts sont gardes, seuls les comptes par zone et
    l'etat changent. Sans cela, l'historique restait calcule sur l'image
    entiere (Technoparc : rouge malgre la zone qui exclut le trottoir)."""
    zones = list(db["ia_capture_zones"].find({"camera_id": camera_id, "channel": channel}))
    interval = 3
    d = db["cockpit_cameras"].find_one({"_id": camera_id}, {"channels": 1}) or {}
    for ch in d.get("channels") or []:
        if ch.get("no") == channel:
            interval = _int(next((r.get("interval_min") for r in ch.get("roles") or [] if r.get("type") == "capture"), 3), 3)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    done, prev, state = 0, [], None
    for s in db["ia_capture_samples"].find({"camera_id": camera_id, "channel": channel, "at": {"$gte": since}},
                                           {"at": 1, "vehicles_a": 1, "vehicles_b": 1, "purpose": 1,
                                            "grid": 1, "grid_max": 1, "total": 1}).sort("at", 1):
        if s.get("purpose") == "secours":
            # Secours : la duree au sol s'enchaine d'une photo a l'autre ; une
            # zone redessinee vaut pour les photos suivantes seulement
            continue
        if s.get("purpose") == "crowd":
            # Foule : comptes par zone repris de la carte de densite reduite
            fz = [z for z in zones if z.get("kind") == "foule"]
            zm, state = crowd_zones(zones, _grid_counts(s.get("grid"), s.get("grid_max") or 0, fz), s.get("total") or 0)
            db["ia_capture_samples"].update_one({"_id": s["_id"]}, {"$set": {"zones": zm, "state": state}})
            done += 1
            continue
        va, vb = s.get("vehicles_a") or [], s.get("vehicles_b") or []
        zm, state = compute_zones(zones, va, vb, s["at"], list(reversed(prev[-200:])), interval)
        db["ia_capture_samples"].update_one({"_id": s["_id"]}, {"$set": {"zones": zm, "state": state, "vehicles_a": va}})
        prev.append({"at": s["at"], "vehicles_a": va})
        done += 1
    if state:
        db["ia_capture_state"].update_one({"_id": "%s:%s" % (camera_id, channel)}, {"$set": {"state": state}})
    return done


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
def _save(src, dst_dir, name, max_w=PHOTO_MAX_W, quality=PHOTO_QUALITY):
    """Photo recompressee (1920 px, q80 ; foule : pleine taille) + vignette ;
    chemins relatifs a HIK_IMAGE_DIR."""
    os.makedirs(os.path.join(dst_dir, "_t"), exist_ok=True)
    im = (src if isinstance(src, Image.Image) else Image.open(src)).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    full = os.path.join(dst_dir, name)
    im.save(full, "JPEG", quality=quality, optimize=True)
    th = im.copy()
    th.thumbnail((THUMB_W, THUMB_W))
    th.save(os.path.join(dst_dir, "_t", name), "JPEG", quality=70)
    rel = os.path.relpath(full, HIK_IMAGE_DIR).replace("\\", "/")
    return rel, os.path.relpath(os.path.join(dst_dir, "_t", name), HIK_IMAGE_DIR).replace("\\", "/")


def _rtsp_url(d, no):
    from urllib.parse import quote
    user, pw = cameras.credentials_for(d)
    auth = "%s:%s@" % (quote(user or "", safe=""), quote(pw or "", safe=""))
    if d.get("brand") == "dahua":
        return "rtsp://%s%s:%d/cam/realmonitor?channel=%d&subtype=0" % (auth, d["ip"], RTSP_PORT, no)
    return "rtsp://%s%s:%d/Streaming/Channels/%d01" % (auth, d["ip"], RTSP_PORT, no)


def _rtsp_reachable(d):
    try:
        socket.create_connection((d["ip"], RTSP_PORT), timeout=2).close()
        return True
    except OSError:
        return False


def _grab_rtsp(d, no, offs):
    """Images du flux principal aux decalages offs (horodatage du flux : ecarts
    exacts). Seules les images retenues sont decodees."""
    import cv2
    cap = cv2.VideoCapture(_rtsp_url(d, no), cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise RuntimeError("flux RTSP injoignable")
    try:
        frames, t0, k = [], None, 0
        deadline = time.time() + offs[-1] + 15
        while k < len(offs) and time.time() < deadline:
            if not cap.grab():
                continue
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if t0 is None:
                t0 = t
            if t - t0 >= offs[k] - 0.02:
                ok, fr = cap.retrieve()
                if ok:
                    frames.append((t - t0, Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))))
                    k += 1
        if k < len(offs):
            raise RuntimeError("flux RTSP trop lent (%d image(s) sur %d)" % (k, len(offs)))
        return frames
    finally:
        cap.release()


def _grab_snapshots(d, cam, offs, tmp):
    """Repli sans flux : photos enchainees, ecarts reels (Antares ~2 s par photo)."""
    out, t0 = [], time.time()
    for k, off in enumerate(offs):
        time.sleep(max(0.0, off - (time.time() - t0)))
        p = "%s%d.jpg" % (tmp, k)
        cameras.call_camera(d, lambda: cam.capture_image(p))
        t = time.time() - t0  # fin de la photo (comme l'essai ; meme biais pour toutes)
        im = Image.open(p)
        im.load()
        os.remove(p)
        out.append((t, im))
    return [(t - out[0][0], im) for t, im in out]


def grab_frames(d, no, cam, offs, tmp):
    """(secondes depuis la 1re image, image PIL) x len(offs), et la source."""
    if _rtsp_reachable(d):
        try:
            return _grab_rtsp(d, no, offs), "rtsp"
        except Exception as e:
            log.warning("%s canal %s : %s, repli sur les photos", d.get("name"), no, e)
    return _grab_snapshots(d, cam, offs, tmp), "photos"


def _detect(rels):
    req = urllib.request.Request(DETECT_URL, data=json.dumps({"photos": rels}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


# ---------------------------------------------------------------------------
# Foule : une photo, nombre de personnes par zone (carte de densite ZIP-B du
# service IA), densite en personnes / m2, niveau normal / charge / trop.
# ---------------------------------------------------------------------------
DENSITY_URL = os.getenv("CAPTURE_IA_DENSITY_URL", "http://127.0.0.1:4011/density")
CROWD_ORDER = {"inconnu": -1, "normal": 0, "charge": 1, "trop": 2}
CROWD_CHARGE_PM2, CROWD_TROP_PM2 = 2.0, 4.0  # seuils par defaut (securite des foules)


def crowd_level(count, zone):
    """Niveau d'une zone : densite = personnes / surface. Sans surface : inconnu."""
    surf = float(zone.get("surface_m2") or 0)
    if surf <= 0:
        return None, "inconnu"
    dens = count / surf
    charge = float(zone.get("seuil_charge") or CROWD_CHARGE_PM2)
    trop = float(zone.get("seuil_trop") or CROWD_TROP_PM2)
    return round(dens, 2), ("trop" if dens >= trop else "charge" if dens >= charge else "normal")


def crowd_zones(zones, counts, total):
    """Mesures par zone "foule" (sans zone : l'image entiere, niveau inconnu)."""
    zm = {}
    fz = [z for z in zones if z.get("kind") == "foule"]
    for z in fz:
        n = float(counts.get(str(z["_id"]), 0))
        dens, level = crowd_level(n, z)
        surf = float(z.get("surface_m2") or 0)
        zm[str(z["_id"])] = {
            "name": z.get("name", ""), "kind": "foule", "count": round(n, 1), "surface_m2": surf or None,
            "density": dens, "state": level,
            "cap_charge": round(surf * float(z.get("seuil_charge") or CROWD_CHARGE_PM2)) if surf else None,
            "cap_trop": round(surf * float(z.get("seuil_trop") or CROWD_TROP_PM2)) if surf else None,
        }
    if not fz:
        zm["image"] = {"name": "Image entiere", "kind": "foule", "count": round(float(total), 1),
                       "surface_m2": None, "density": None, "state": "inconnu"}
    state = max((m["state"] for m in zm.values()), key=lambda s: CROWD_ORDER[s])
    return zm, state


def _grid_counts(grid, gmax, zones):
    """Recalcul approche des personnes par zone depuis la carte reduite gardee
    avec la mesure (cellules dont le centre est dans le polygone)."""
    out = {}
    if not grid:
        return out
    gh, gw = len(grid), len(grid[0])
    for z in zones:
        poly = z.get("polygon") or []
        if len(poly) < 3:
            continue
        s = 0.0
        for i, row in enumerate(grid):
            for j, v in enumerate(row):
                if v and _in_polygon(((j + 0.5) / gw, (i + 0.5) / gh), poly):
                    s += v * gmax
        out[str(z["_id"])] = s
    return out


def capture_crowd(db, d, ch, role, dry_run=False):
    cam_id, no = str(d["_id"]), ch.get("no", 1)
    label = "%s canal %s" % (d.get("name"), no)
    cam = cameras.make_hik_camera(d, no)
    preset = str(role.get("preset") or "").strip()
    if preset:
        cameras.call_camera(d, lambda: cam.goto_preset(preset, raise_errors=True))
        time.sleep(PRESET_WAIT_S)
    now = datetime.now(timezone.utc)
    day_dir = os.path.join(CAPTURE_DIR, now.strftime("%Y"), now.strftime("%m"), now.strftime("%d"), "%s_ch%s" % (cam_id, no))
    tmp = os.path.join(LOG_DIR, "capture_%s_%s_f.jpg" % (cam_id, no))
    cameras.call_camera(d, lambda: cam.capture_image(tmp))
    at = datetime.now(timezone.utc)
    rel, thumb = _save(tmp, day_dir, "%s_f.jpg" % at.strftime("%H%M%S"), max_w=4096, quality=85)
    os.remove(tmp)
    zones = list(db["ia_capture_zones"].find({"camera_id": d["_id"], "channel": no}))
    req = urllib.request.Request(DENSITY_URL, data=json.dumps({
        "photo": rel, "zones": [{"id": str(z["_id"]), "polygon": z.get("polygon") or []}
                                for z in zones if z.get("kind") == "foule"]}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.load(r)
    zm, state = crowd_zones(zones, res.get("zones") or {}, res.get("total") or 0)
    sample = {
        "camera_id": d["_id"], "channel": no, "camera_name": d.get("name", ""),
        "channel_name": ch.get("name", ""), "purpose": "crowd", "at": at, "preset": preset or None,
        "photos": [{"photo": rel, "thumb": thumb, "at": at}], "vehicles_a": [], "vehicles_b": [],
        "total": res.get("total"), "persons_yolo": res.get("persons_yolo"),
        "grid": res.get("grid"), "grid_max": res.get("grid_max"),
        "zones": zm, "state": state, "model": res.get("model"),
    }
    log.info("%s : foule ~%.0f personne(s) (detecteur %s), niveau %s", label, res.get("total") or 0,
             res.get("persons_yolo"), state)
    if dry_run:
        return sample
    sid = db["ia_capture_samples"].insert_one(sample).inserted_id
    key = "%s:%s" % (cam_id, no)
    prev = db["ia_capture_state"].find_one({"_id": key}) or {}
    streak = (prev.get("streak", 0) + 1) if prev.get("state") == state else 1
    db["ia_capture_state"].update_one({"_id": key}, {"$set": {
        "camera_id": d["_id"], "channel": no, "last_at": at, "last_sample": sid,
        "state": state, "streak": streak, "last_error": None, "purpose": "crowd"}}, upsert=True)
    sample["_id"] = sid
    try:
        set_review(db, sample)
    except Exception as e:
        log.warning("%s : interet pour la galerie non calcule (%s)", label, e)
    try:
        dispatch_crowd(db, d, ch, sample)
    except Exception as e:
        log.warning("%s : alerte foule non evaluee (%s)", label, e)
    return sample


def dispatch_crowd(db, d, ch, sample):
    """Regles "crowd_camera" : niveau >= min_level sur `consecutive` mesures
    (au moins une zone avec surface). Une alerte par episode et par regle,
    fermee a la premiere mesure sous le niveau ; cooldown_min entre deux."""
    key = "%s:%s" % (d["_id"], ch.get("no"))
    defs = [x for x in db["cockpit_alert_definitions"].find({"detection_type": "crowd_camera", "enabled": True})
            if key in ((x.get("params") or {}).get("channels") or [])]
    if not defs:
        return 0
    now = datetime.now(timezone.utc)
    st = db["ia_capture_state"].find_one({"_id": key}) or {}
    episodes = dict(st.get("crowd_episodes") or {})
    rank = CROWD_ORDER.get(sample["state"], -1)
    wa_batch = []
    for df in defs:
        p = df.get("params") or {}
        slug = df.get("slug")
        min_level = p.get("min_level") if p.get("min_level") in ("charge", "trop") else "trop"
        need = max(1, min(10, _int(p.get("consecutive"), 1)))
        ep = episodes.get(slug) or {}
        if rank < CROWD_ORDER[min_level]:
            if ep.get("open"):
                episodes[slug] = dict(ep, open=False, closed_at=now)
            continue
        if ep.get("open"):
            continue
        last = list(db["ia_capture_samples"].find({"camera_id": d["_id"], "channel": ch.get("no"), "purpose": "crowd"},
                                                  {"state": 1}).sort("at", -1).limit(need))
        if len(last) < need or any(CROWD_ORDER.get(s.get("state"), -1) < CROWD_ORDER[min_level] for s in last):
            continue
        cool = _int(p.get("cooldown_min"), 30)
        if ep.get("alerted_at") and now - ep["alerted_at"].replace(tzinfo=timezone.utc) < timedelta(minutes=cool):
            episodes[slug] = dict(ep, open=True)
            continue
        bad = [z for z in sample["zones"].values() if CROWD_ORDER.get(z.get("state"), -1) >= CROWD_ORDER[min_level]]
        lbl = "Trop de monde" if sample["state"] == "trop" else "Foule chargee"
        msg = "%s sur %s : %s" % (lbl, d.get("name", ""), ", ".join(
            "%s ~%d pers. (%.1f p/m2)" % (z["name"], z["count"], z["density"] or 0) for z in bad))
        a = _create_alert(db, df, d, ch, sample, key, "crowd", lbl, msg, {
            "crowd_state": sample["state"], "zones": [z["name"] for z in bad],
            "persons": sample.get("total")})
        if a:
            wa_batch.append((a, df))
        episodes[slug] = {"open": True, "alerted_at": now, "state": sample["state"]}
    db["ia_capture_state"].update_one({"_id": key}, {"$set": {"crowd_episodes": episodes}}, upsert=True)
    if wa_batch and _whatsapp():
        try:
            _whatsapp()(db).notify_batch(wa_batch)
        except Exception as e:
            log.warning("WhatsApp foule : envoi impossible (%s)", e)
    return len(wa_batch)


# ---------------------------------------------------------------------------
# Secours : une photo, squelette de chaque personne (service IA, /pose),
# posture (allonge ou non) et personnes AU SOL qui y restent d'une photo a
# l'autre. Observation : aucune alerte pour l'instant.
# ---------------------------------------------------------------------------
POSE_URL = os.getenv("CAPTURE_IA_POSE_URL", "http://127.0.0.1:4011/pose")
SECOURS_ORDER = {"inconnu": -1, "rien": 0, "sol": 1, "immobile": 2}
KP_MIN = 0.35          # confiance minimale d'un point du squelette
LYING_DEG = 60         # axe du corps a plus de 60 degres de la verticale = allonge
UPRIGHT_DEG = 35       # a moins de 35 degres : debout (ou assis)
LYING_CONF = 0.4       # personnes moins sures : ignorees pour l'etat
SAME_SPOT = 1.0        # meme personne au sol : centres a moins de 1 taille de cadre


def posture(p, size):
    """(posture, angle) : "allonge", "incline", "vertical" ou "inconnu". Axe
    du corps = epaules (ou tete) -> chevilles (ou genoux, ou hanches), en
    pixels ; sans assez de points, la forme du cadre (bien plus large que
    haut = allonge)."""
    W, H = size
    k = p.get("kpts") or []

    def pt(i):
        return (k[i][0] * W, k[i][1] * H) if i < len(k) and k[i][2] >= KP_MIN else None

    def mid(a, b):
        pa, pb = pt(a), pt(b)
        if pa and pb:
            return ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
        return pa or pb
    top = mid(5, 6) or mid(3, 4) or pt(0)
    bottom = mid(15, 16) or mid(13, 14) or mid(11, 12)
    bw, bh = p["box"]["w"] * W, p["box"]["h"] * H
    if top and bottom:
        dx, dy = bottom[0] - top[0], bottom[1] - top[1]
        if (dx * dx + dy * dy) ** 0.5 >= 0.3 * max(bw, bh):
            ang = round(math.degrees(math.atan2(abs(dx), abs(dy))))
            return ("allonge" if ang >= LYING_DEG else "vertical" if ang <= UPRIGHT_DEG else "incline"), ang
    if bh and bw >= 1.6 * bh:
        return "allonge", None
    return ("vertical" if bh >= 1.3 * bw else "inconnu"), None


def secours_persons(res, zones, prev):
    """Personnes de la photo avec posture, zone et duree au sol (`since` :
    premiere photo ou la meme personne etait deja allongee a cet endroit)."""
    size = res.get("size") or [1920, 1080]
    sz = [z for z in zones if z.get("kind") == "secours" and len(z.get("polygon") or []) >= 3]
    prev_lying = [q for q in (prev or {}).get("persons_pose") or [] if q.get("posture") == "allonge" and q.get("counted")]
    out = []
    for p in res.get("persons") or []:
        po, ang = posture(p, size)
        b = p["box"]
        c = _center(b)
        zone = next((str(z["_id"]) for z in sz if _in_polygon(c, z["polygon"])), None)
        q = {"box": b, "conf": p.get("conf"), "kpts": p.get("kpts"), "posture": po, "angle": ang, "zone": zone,
             "counted": po == "allonge" and (p.get("conf") or 0) >= LYING_CONF and (zone is not None or not sz)}
        if q["counted"]:
            size_n = max(b["w"] * ASPECT, b["h"])
            twin = next((x for x in prev_lying if ((_center(x["box"])[0] - c[0]) * ASPECT) ** 2 +
                         (_center(x["box"])[1] - c[1]) ** 2 <= (SAME_SPOT * max(size_n, max(x["box"]["w"] * ASPECT, x["box"]["h"]))) ** 2), None)
            q["since"] = twin.get("since") if twin else None
        out.append(q)
    return out


def secours_state(persons, at):
    """("rien" | "sol" | "immobile", minutes) : "sol" = quelqu'un allonge sur
    cette photo, "immobile" = deja allonge au meme endroit sur la precedente
    (`since` repris) ; minutes = la plus longue duree au sol."""
    lying = [q for q in persons if q.get("counted")]
    if not lying:
        return "rien", 0
    seen = [q for q in lying if q.get("since")]
    minutes = max([int((at - q["since"].replace(tzinfo=timezone.utc)).total_seconds() // 60) for q in seen] or [0])
    return ("immobile" if seen else "sol"), minutes


def capture_secours(db, d, ch, role, dry_run=False):
    cam_id, no = str(d["_id"]), ch.get("no", 1)
    label = "%s canal %s" % (d.get("name"), no)
    cam = cameras.make_hik_camera(d, no)
    preset = str(role.get("preset") or "").strip()
    if preset:
        cameras.call_camera(d, lambda: cam.goto_preset(preset, raise_errors=True))
        time.sleep(PRESET_WAIT_S)
    now = datetime.now(timezone.utc)
    day_dir = os.path.join(CAPTURE_DIR, now.strftime("%Y"), now.strftime("%m"), now.strftime("%d"), "%s_ch%s" % (cam_id, no))
    tmp = os.path.join(LOG_DIR, "capture_%s_%s_s.jpg" % (cam_id, no))
    cameras.call_camera(d, lambda: cam.capture_image(tmp))
    at = datetime.now(timezone.utc)
    rel, thumb = _save(tmp, day_dir, "%s_s.jpg" % at.strftime("%H%M%S"), max_w=4096, quality=85)
    os.remove(tmp)
    req = urllib.request.Request(POSE_URL, data=json.dumps({"photo": rel}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.load(r)
    zones = list(db["ia_capture_zones"].find({"camera_id": d["_id"], "channel": no}))
    # Photo precedente : la meme personne encore au sol (moins de 3 intervalles)
    prev = db["ia_capture_samples"].find_one(
        {"camera_id": d["_id"], "channel": no, "purpose": "secours",
         "at": {"$gte": at - timedelta(minutes=3 * _int(role.get("interval_min"), 3) + 1)}},
        {"persons_pose": 1, "at": 1}, sort=[("at", -1)])
    persons = secours_persons(res, zones, prev)
    state, minutes = secours_state(persons, at)
    # Nouvelle personne au sol : au sol depuis cette photo ("immobile" si
    # elle y est encore a la suivante)
    for q in persons:
        if q.get("counted") and q.get("since") is None:
            q["since"] = at
    sample = {
        "camera_id": d["_id"], "channel": no, "camera_name": d.get("name", ""),
        "channel_name": ch.get("name", ""), "purpose": "secours", "at": at, "preset": preset or None,
        "photos": [{"photo": rel, "thumb": thumb, "at": at}], "vehicles_a": [], "vehicles_b": [],
        "persons_pose": persons, "size": res.get("size"), "total": len(persons),
        "lying": sum(1 for q in persons if q.get("counted")), "lying_min": minutes,
        "state": state, "model": res.get("model"),
    }
    log.info("%s : secours %d personne(s), %d au sol, etat %s%s", label, len(persons), sample["lying"], state,
             " (%d min)" % minutes if state == "immobile" else "")
    if dry_run:
        return sample
    sid = db["ia_capture_samples"].insert_one(sample).inserted_id
    key = "%s:%s" % (cam_id, no)
    prevst = db["ia_capture_state"].find_one({"_id": key}) or {}
    streak = (prevst.get("streak", 0) + 1) if prevst.get("state") == state else 1
    db["ia_capture_state"].update_one({"_id": key}, {"$set": {
        "camera_id": d["_id"], "channel": no, "last_at": at, "last_sample": sid,
        "state": state, "streak": streak, "last_error": None, "purpose": "secours"}}, upsert=True)
    sample["_id"] = sid
    try:
        set_review(db, sample)
    except Exception as e:
        log.warning("%s : interet pour la galerie non calcule (%s)", label, e)
    try:
        dispatch_secours(db, d, ch, sample)
    except Exception as e:
        log.warning("%s : alerte secours non evaluee (%s)", label, e)
    return sample


def dispatch_secours(db, d, ch, sample):
    """Regles "secours_camera" : etat >= min_state ("sol" ou "immobile", par
    defaut immobile), au sol depuis >= min_minutes, sur `consecutive`
    mesures. Une alerte par episode et par regle (photo + cadre de la
    personne), fermee a la premiere photo sans personne au sol ; cooldown_min
    entre deux."""
    key = "%s:%s" % (d["_id"], ch.get("no"))
    defs = [x for x in db["cockpit_alert_definitions"].find({"detection_type": "secours_camera", "enabled": True})
            if key in ((x.get("params") or {}).get("channels") or [])]
    if not defs:
        return 0
    now = datetime.now(timezone.utc)
    st = db["ia_capture_state"].find_one({"_id": key}) or {}
    episodes = dict(st.get("secours_episodes") or {})
    rank = SECOURS_ORDER.get(sample["state"], -1)
    wa_batch = []
    for df in defs:
        p = df.get("params") or {}
        slug = df.get("slug")
        min_state = p.get("min_state") if p.get("min_state") in ("sol", "immobile") else "immobile"
        min_min = max(0, min(120, _int(p.get("min_minutes"), 0)))
        need = max(1, min(10, _int(p.get("consecutive"), 1)))
        ep = episodes.get(slug) or {}
        ok = rank >= SECOURS_ORDER[min_state] and (sample.get("lying_min") or 0) >= min_min
        if not ok:
            if ep.get("open") and rank < SECOURS_ORDER["sol"]:
                episodes[slug] = dict(ep, open=False, closed_at=now)
            continue
        if ep.get("open"):
            continue
        last = list(db["ia_capture_samples"].find({"camera_id": d["_id"], "channel": ch.get("no"), "purpose": "secours"},
                                                  {"state": 1}).sort("at", -1).limit(need))
        if len(last) < need or any(SECOURS_ORDER.get(s.get("state"), -1) < SECOURS_ORDER[min_state] for s in last):
            continue
        cool = _int(p.get("cooldown_min"), 30)
        if ep.get("alerted_at") and now - ep["alerted_at"].replace(tzinfo=timezone.utc) < timedelta(minutes=cool):
            episodes[slug] = dict(ep, open=True)
            continue
        lying = [q for q in sample.get("persons_pose") or [] if q.get("counted")]
        lbl = "Personne au sol"
        msg = "%s sur %s : %d personne(s) allongee(s)%s" % (
            lbl, d.get("name", ""), len(lying),
            ", immobile(s) depuis %d min" % sample.get("lying_min", 0) if sample["state"] == "immobile" else "")
        a = _create_alert(db, df, d, ch, sample, key, "secours", lbl, msg, {
            "secours_state": sample["state"], "lying": len(lying), "lying_min": sample.get("lying_min", 0),
            "box": lying[0]["box"] if lying else None})
        if a:
            wa_batch.append((a, df))
        episodes[slug] = {"open": True, "alerted_at": now, "state": sample["state"]}
    db["ia_capture_state"].update_one({"_id": key}, {"$set": {"secours_episodes": episodes}}, upsert=True)
    if wa_batch and _whatsapp():
        try:
            _whatsapp()(db).notify_batch(wa_batch)
        except Exception as e:
            log.warning("WhatsApp secours : envoi impossible (%s)", e)
    return len(wa_batch)


# ---------------------------------------------------------------------------
# Galerie : interet d'une mesure pour l'apprentissage. Si tout est actif,
# des milliers de photos par jour, presque toutes identiques a la precedente
# et loin des seuils : on ne propose a l'operateur que celles qui apprennent
# quelque chose (changement d'etat, cas rare, desaccord entre indices, proche
# d'un seuil, personne allongee), plus un tirage au hasard de controle
# (1 sur REVIEW_AUDIT) pour mesurer la justesse du reste sans biais.
# ---------------------------------------------------------------------------
REVIEW_AUDIT = 40
CROWD_NEAR = 0.2          # a +-20 % d'un seuil de densite : cas limite


def review_of(s, prev):
    """{score, reasons} d'une mesure (prev = mesure precedente du canal, meme
    fonction). Score 0 = rien a apprendre (sauf tirage de controle)."""
    score, why = 0, []

    def add(n, txt):
        nonlocal score
        score += n
        why.append(txt)
    purpose, st = s.get("purpose") or "traffic", s.get("state")
    pst = (prev or {}).get("state")
    if prev and pst and st and pst != st:
        add(3, "changement d'etat (%s -> %s)" % (pst, st))
    if purpose == "secours":
        ps = s.get("persons_pose") or []
        if any(q.get("posture") == "allonge" for q in ps):
            add(4, "personne allongee")
        elif any(q.get("posture") == "incline" and (q.get("conf") or 0) >= LYING_CONF for q in ps):
            add(1, "personne penchee")
        if st == "immobile":
            add(2, "au sol, immobile")
    elif purpose == "crowd":
        if st == "trop":
            add(2, "trop de monde (rare)")
        for z in (s.get("zones") or {}).values():
            dens, surf = z.get("density"), z.get("surface_m2")
            if dens is None or not surf:
                continue
            for lim in (z.get("cap_charge"), z.get("cap_trop")):
                if lim and abs(z.get("count", 0) - lim) <= CROWD_NEAR * lim:
                    add(2, "proche d'un seuil (%s)" % z.get("name", ""))
                    break
    else:
        if st in ("ralenti", "bouchon"):
            add(2, "%s (rare)" % st)
        elif st == "dense":
            add(1, "dense")
        for z in (s.get("zones") or {}).values():
            m, c = z.get("state_motion"), z.get("state_count")
            if m and c and m != c and "inconnu" not in (m, c):
                add(2, "mouvement et nombre en desaccord (%s)" % z.get("name", ""))
        for r in ((s.get("waze") or {}).get("routes") or {}).values():
            ratio = r.get("ratio")
            if ratio is None:
                continue
            if ratio >= 1.5 and st == "fluide":
                add(3, "Waze ralenti (x%.1f), IA fluide" % ratio)
            elif ratio <= 1.1 and st in ("ralenti", "bouchon"):
                add(3, "Waze fluide, IA %s" % st)
    if not score:
        at = s.get("at")
        if at and int(at.timestamp() * 1000) % REVIEW_AUDIT == 0:
            add(1, "controle au hasard (1 sur %d)" % REVIEW_AUDIT)
    return {"score": score, "reasons": why[:4]}


def set_review(db, s):
    """Calcule et enregistre l'interet de la mesure (apres son insertion)."""
    prev = db["ia_capture_samples"].find_one(
        {"camera_id": s["camera_id"], "channel": s["channel"], "at": {"$lt": s["at"]},
         "purpose": s.get("purpose") if s.get("purpose") in ("crowd", "secours") else {"$nin": ["crowd", "secours"]}},
        {"state": 1}, sort=[("at", -1)])
    rv = review_of(s, prev)
    db["ia_capture_samples"].update_one({"_id": s["_id"]}, {"$set": {"review": rv}})
    return rv


def backfill_review(db):
    """Interet de toutes les mesures en base (canal par canal, dans l'ordre)."""
    n, prev = 0, {}
    for s in db["ia_capture_samples"].find({}, {"photos": 0, "vehicles_a": 0, "vehicles_b": 0, "vehicles_a2": 0,
                                                "vehicles_b2": 0, "grid": 0}).sort("at", 1):
        k = (s["camera_id"], s["channel"], s.get("purpose") or "traffic")
        db["ia_capture_samples"].update_one({"_id": s["_id"]}, {"$set": {"review": review_of(s, prev.get(k))}})
        prev[k] = s
        n += 1
    return n


def capture_one(db, d, ch, role, dry_run=False):
    if role.get("purpose") == "crowd":
        return capture_crowd(db, d, ch, role, dry_run=dry_run)
    if role.get("purpose") == "secours":
        return capture_secours(db, d, ch, role, dry_run=dry_run)
    cam_id, no = str(d["_id"]), ch.get("no", 1)
    label = "%s canal %s" % (d.get("name"), no)
    cam = cameras.make_hik_camera(d, no)
    preset = str(role.get("preset") or "").strip()
    if preset:
        cameras.call_camera(d, lambda: cam.goto_preset(preset, raise_errors=True))
        time.sleep(PRESET_WAIT_S)
    gap = _int(role.get("gap_s"), 8)
    offs = (0.0, PAIR_S, float(gap), gap + PAIR_S)
    now = datetime.now(timezone.utc)
    day_dir = os.path.join(CAPTURE_DIR, now.strftime("%Y"), now.strftime("%m"), now.strftime("%d"), "%s_ch%s" % (cam_id, no))
    tmp = os.path.join(LOG_DIR, "capture_%s_%s_" % (cam_id, no))
    t_start = datetime.now(timezone.utc)
    frames, source = grab_frames(d, no, cam, offs, tmp)
    shots = []
    for k, (t, im) in enumerate(frames):
        at = t_start + timedelta(seconds=t)
        rel, thumb = _save(im, day_dir, "%s_%s.jpg" % (t_start.strftime("%H%M%S"), "abcd"[k]))
        shots.append({"photo": rel, "thumb": thumb, "at": at, "t": round(t, 2)})
    res = _detect([s["photo"] for s in shots])
    vs = [[v for v in r if v["class"] in VEHICLES] for r in res["results"]]
    ims = [im.convert("RGB") for _, im in frames]
    ts = [t for t, _ in frames]
    va, va2 = classify_pair(vs, ims, ts, 0, 1, 2)   # 1re paire, verifiee 8 s plus tard
    vb, vb2 = classify_pair(vs, ims, ts, 2, 3, 0)   # 2e paire, verifiee 8 s plus tot
    zones = list(db["ia_capture_zones"].find({"camera_id": d["_id"], "channel": no}))
    history = []
    if any(z.get("kind") == "stationnement" for z in zones):
        history = list(db["ia_capture_samples"].find(
            {"camera_id": d["_id"], "channel": no, "at": {"$gte": shots[0]["at"] - timedelta(hours=6)}},
            {"at": 1, "vehicles_a": 1}).sort("at", -1).limit(200))
    zm, state = compute_zones(zones, va, vb, shots[0]["at"], history, _int(role.get("interval_min"), 3))
    real_gap = ts[2] - ts[0]
    sample = {
        "camera_id": d["_id"], "channel": no, "camera_name": d.get("name", ""),
        "channel_name": ch.get("name", ""), "purpose": role.get("purpose", "traffic"),
        "at": shots[0]["at"], "gap_s": round(real_gap, 1), "preset": preset or None,
        "method": "flux", "source": source, "pair_s": [round(ts[1] - ts[0], 2), round(ts[3] - ts[2], 2)],
        "photos": shots, "vehicles_a": va, "vehicles_b": vb, "vehicles_a2": va2, "vehicles_b2": vb2, "persons": [len([v for v in r if v["class"] == "person"]) for r in res["results"]],
        "zones": zm, "state": state, "model": res.get("model"),
    }
    cnt = {k: sum(v["status"] == k for v in va + vb) for k in ("arrete", "lent", "bref", "roule")}
    log.info("%s : %d + %d vehicules (%d arretes, %d lents, %d arrets brefs, %d roulent), etat %s, ecarts %s s (%s)",
             label, len(va), len(vb), cnt["arrete"], cnt["lent"], cnt["bref"], cnt["roule"], state,
             [round(t, 1) for t in ts], source)
    if dry_run:
        return sample
    sid = db["ia_capture_samples"].insert_one(sample).inserted_id
    prev = db["ia_capture_state"].find_one({"_id": "%s:%s" % (cam_id, no)}) or {}
    streak = (prev.get("streak", 0) + 1) if prev.get("state") == state else 1
    db["ia_capture_state"].update_one({"_id": "%s:%s" % (cam_id, no)}, {"$set": {
        "camera_id": d["_id"], "channel": no, "last_at": shots[0]["at"], "last_sample": sid,
        "state": state, "streak": streak, "last_error": None}}, upsert=True)
    sample["_id"] = sid
    try:
        add_stats(db, sample, zones)
    except Exception as e:
        log.warning("%s : statistiques non enregistrees (%s)", label, e)
    try:
        set_review(db, sample)
    except Exception as e:
        log.warning("%s : interet pour la galerie non calcule (%s)", label, e)
    try:
        dispatch_traffic(db, d, ch, sample)
    except Exception as e:
        log.warning("%s : alerte trafic non evaluee (%s)", label, e)
    try:
        dispatch_parking(db, d, ch, sample)
    except Exception as e:
        log.warning("%s : alerte stationnement non evaluee (%s)", label, e)
    return sample


# ---------------------------------------------------------------------------
# Statistiques du trafic : une ligne par camera, zone et quart d'heure, dans
# ia_capture_stats, GARDEE SANS LIMITE (les mesures et leurs photos sont
# purgees a CAPTURE_RETENTION_DAYS). Sert aux profils de reference (jour de
# semaine, heure) et aux comparaisons (jours d'evenement, editions).
# Nombre de vehicules = REMPLISSAGE de la zone sur une image, pas un debit.
# ---------------------------------------------------------------------------
STATS_SLOT_MIN = 15

# Waze (flux partenaire, traffic.py) croise avec les cameras : itineraires
# lies a un canal (choisis dans la page IA : ia_capture_links, ou nommes avec
# l'etiquette [cam:133] / [cam:133.2] dans Waze) et signalements proches.
WAZE_TAG = re.compile(r"\[\s*cam\s*:?\s*(\d+)(?:\s*[.\-/]\s*(\d+))?\s*\]", re.I)
WAZE_MAX_AGE_MIN = 20       # releve Waze plus vieux : ignore
WAZE_ALERT_RADIUS_M = 400   # signalements comptes autour de la camera


def cam_number(name):
    m = re.match(r"\s*(\d+)", name or "")
    return m.group(1) if m else None


def waze_tagged(route_name, num, channel):
    """L'itineraire porte-t-il l'etiquette de cette camera ([cam:N] ou [cam:N.canal]) ?"""
    if not num:
        return False
    for m in WAZE_TAG.finditer(route_name or ""):
        if m.group(1) == num and (m.group(2) is None or int(m.group(2)) == int(channel)):
            return True
    return False


def waze_linked(db, camera_id, channel, cam_name, routes):
    """[(itineraire, "choix"|"nom", zone_id|None)] lies au canal parmi les
    itineraires d'un releve. zone = zone trafic de l'image qui correspond a
    l'itineraire (une zone par sens de circulation) ; None = tout le canal."""
    link = db["ia_capture_links"].find_one({"_id": "%s:%s" % (camera_id, channel)}) or {}
    ids = {str(x) for x in link.get("waze_routes") or []}
    rz = dict(link.get("route_zones") or {})
    # Itineraire trace sur la photo avec son itineraire Waze : lie, et sa zone
    traced = set()
    for z in db["ia_capture_zones"].find({"camera_id": camera_id, "channel": channel, "kind": "itineraire",
                                          "waze_route": {"$nin": [None, ""]}}, {"waze_route": 1}):
        rz[str(z["waze_route"])] = str(z["_id"])
        traced.add(str(z["waze_route"]))
    num = cam_number(cam_name)
    out = []
    for r in routes or []:
        rid = str(r.get("id"))
        if rid in traced:
            out.append((r, "trace", rz.get(rid)))
        elif waze_tagged(r.get("name"), num, channel):
            out.append((r, "nom", rz.get(rid)))
        elif rid in ids:
            out.append((r, "choix", rz.get(rid)))
    return out


def waze_snapshot(db, at):
    """(itineraires, signalements) du releve Waze le plus proche de `at` (UTC) :
    le dernier releve s'il est frais, sinon l'historique (garde 1 an)."""
    at = at.replace(tzinfo=None) if at.tzinfo else at
    win = timedelta(minutes=WAZE_MAX_AGE_MIN)
    lt = db["waze_trafic"].find_one({"_id": "latest"}) or {}
    if lt.get("fetched_at") and abs(lt["fetched_at"] - at) <= win and lt["fetched_at"] <= at + timedelta(minutes=2):
        la = db["waze_alerts"].find_one({"_id": "latest"}) or {}
        return (lt.get("data") or {}).get("routes") or [], la.get("data") or []
    near = None
    for d in (db["waze_trafic_history"].find_one({"fetched_at": {"$lte": at, "$gte": at - win}}, sort=[("fetched_at", -1)]),
              db["waze_trafic_history"].find_one({"fetched_at": {"$gt": at, "$lte": at + win}}, sort=[("fetched_at", 1)])):
        if d and (near is None or abs(d["fetched_at"] - at) < abs(near["fetched_at"] - at)):
            near = d
    if not near:
        return None, None
    al = db["waze_alerts_history"].find_one({"fetched_at": near["fetched_at"]}) or {}
    return (near.get("data") or {}).get("routes") or [], al.get("data") or []


def _dist_m(lat1, lng1, lat2, lng2):
    import math
    k = 111320.0
    return math.hypot((lat2 - lat1) * k, (lng2 - lng1) * k * math.cos(math.radians(lat1)))


def route_distance_m(route, lat, lng):
    """Distance de la camera au trace de l'itineraire (sommets, sous-itineraires compris)."""
    pts = list(route.get("line") or [])
    for sr in route.get("subRoutes") or []:
        pts.extend(sr.get("line") or [])
    return min((_dist_m(lat, lng, p["y"], p["x"]) for p in pts if "x" in p and "y" in p), default=None)


_POS = {}


def camera_position(db, camera_id):
    """Position de la camera (carte de la page IA), gardee le temps du passage."""
    if camera_id not in _POS:
        try:
            from detection import _device_position
            d = db["cockpit_cameras"].find_one({"_id": camera_id})
            _POS[camera_id] = _device_position(db, d)[0] if d else None
        except Exception:
            _POS[camera_id] = None
    return _POS[camera_id]


def waze_for_sample(db, s):
    """Waze au moment d'une mesure : {routes: {id: {...}}, alerts: {type: n}}, ou None."""
    routes, alerts = waze_snapshot(db, s["at"])
    if routes is None:
        return None
    out = {"routes": {}, "alerts": {}}
    pos = camera_position(db, s["camera_id"])
    for r, how, zone in waze_linked(db, s["camera_id"], s["channel"], s.get("camera_name"), routes):
        t, h = r.get("time"), r.get("historicTime")
        item = {"name": r.get("name", ""), "time": t, "historic": h,
                "ratio": round(t / h, 3) if t and h else None,
                "jam": r.get("jamLevel"), "length": r.get("length"), "how": how, "zone": zone}
        # Troncon (sous-itineraire Waze) le plus proche de la camera : un
        # itineraire de plusieurs km ne dit pas ce qui se passe devant elle
        if pos:
            best = None
            for sr in r.get("subRoutes") or []:
                dd = route_distance_m({"line": sr.get("line")}, pos["lat"], pos["lng"])
                if dd is not None and (best is None or dd < best[0]):
                    best = (dd, sr)
            if best and best[0] <= 150:
                sr = best[1]
                st, sh = sr.get("time"), sr.get("historicTime")
                item["sub"] = {"from": sr.get("fromName", ""), "to": sr.get("toName", ""), "length": sr.get("length"),
                               "time": st, "historic": sh, "ratio": round(st / sh, 3) if st and sh else None,
                               "jam": sr.get("jamLevel"), "dist_m": round(best[0])}
        out["routes"][str(r.get("id"))] = item
    if pos:
        for a in alerts or []:
            loc = a.get("location") or {}
            if "x" in loc and _dist_m(pos["lat"], pos["lng"], loc["y"], loc["x"]) <= WAZE_ALERT_RADIUS_M:
                k = a.get("type") or "AUTRE"
                out["alerts"][k] = out["alerts"].get(k, 0) + 1
    return out


def _sample_images(s):
    """Vehicules de chaque image d'une mesure (4 en deux paires, 2 avant)."""
    if s.get("method") == "flux":
        return [s.get("vehicles_a") or [], s.get("vehicles_a2") or [], s.get("vehicles_b") or [], s.get("vehicles_b2") or []]
    return [s.get("vehicles_a") or [], s.get("vehicles_b") or []]


def _waze_fields(wz, zone_key, valid, winc, wmax, wset):
    """Champs Waze d'une ligne de statistiques : itineraires attribues a la
    zone (ou a tout le canal, ou a une zone supprimee depuis), signalements
    proches."""
    for rid, r in (wz.get("routes") or {}).items():
        if r.get("zone") in valid and r["zone"] != zone_key:
            continue
        p = "waze.%s." % rid
        winc[p + "n"] = 1
        for f, v in (("time_sum", r.get("time")), ("hist_sum", r.get("historic")), ("ratio_sum", r.get("ratio"))):
            if v is not None:
                winc[p + f] = v
        if r.get("ratio") is not None:
            wmax[p + "ratio_max"] = r["ratio"]
        if r.get("jam") is not None:
            wmax[p + "jam_max"] = r["jam"]
        wset[p + "name"] = r.get("name", "")
        sub = r.get("sub") or {}
        if sub.get("ratio") is not None:
            winc[p + "sub_n"] = 1
            winc[p + "sub_ratio_sum"] = sub["ratio"]
            wmax[p + "sub_ratio_max"] = sub["ratio"]
            wset[p + "sub_name"] = "%s -> %s" % (sub.get("from", ""), sub.get("to", ""))
    for k, n in (wz.get("alerts") or {}).items():
        wmax["waze_alerts." + k] = n


def add_stats(db, s, zones):
    """Ajoute une mesure trafic a son quart d'heure (une fois : `in_stats`).
    Par zone trafic (sans zone : l'image entiere) : nombre de mesures,
    vehicules (max sur une image, somme des moyennes par image), statuts
    des vehicules mesures (arretes / lents / roulants), vitesse moyenne
    (deux paires), etats."""
    if s.get("purpose") in ("crowd", "secours") or s.get("in_stats") or not s.get("_id"):
        return 0
    at = s["at"].replace(tzinfo=timezone.utc) if s["at"].tzinfo is None else s["at"]
    slot = at.replace(minute=at.minute - at.minute % STATS_SLOT_MIN, second=0, microsecond=0)
    loc = slot.astimezone(detection_arming.PARIS)
    images = _sample_images(s)
    targets = [(k, n, p) for k, n, _kind, p, _lv in measure_targets(zones, [v for im in images for v in im])]
    flux = s.get("method") == "flux"
    # Vehicules mesures : 1re image de chaque paire (deux paires), A seule avant
    measured = (s.get("vehicles_a") or []) + ((s.get("vehicles_b") or []) if flux else [])
    zstate = s.get("zones") or {}
    # Waze au meme moment (itineraires lies au canal, signalements proches)
    wz = s.get("waze")
    if wz is None:
        try:
            wz = waze_for_sample(db, s)
        except Exception as e:
            log.warning("Waze non lu pour la mesure %s (%s)", s.get("_id"), e)
            wz = None
    # Par zone : les itineraires attribues a cette zone, ou a tout le canal
    per_zone = {}
    for key, _n, _p in targets:
        winc, wmax, wset = {}, {}, {}
        if wz:
            winc["waze_n"] = 1
            _waze_fields(wz, key, {k for k, _n2, _p2 in targets}, winc, wmax, wset)
        per_zone[key] = (winc, wmax, wset)
    for key, name, inz in targets:
        winc, wmax, wset = per_zone[key]
        counts = [sum(1 for v in im if inz(v)) for im in images]
        mv = [v for v in measured if inz(v)]
        speeds = [v["speed"] for v in mv if v.get("speed") is not None]
        state = (zstate.get(key) or {}).get("state") or s.get("state") or "inconnu"
        inc = {
            "samples": 1, "images": len(counts), "veh_sum": sum(counts) / max(len(counts), 1),
            "measured": len(mv),
            "stopped": sum(1 for v in mv if v.get("status") in STOPPED),
            "slow": sum(1 for v in mv if v.get("status") in SLOW),
            "speed_sum": sum(speeds), "speed_n": len(speeds),
            "states." + state: 1,
        }
        inc.update(winc)
        db["ia_capture_stats"].update_one(
            {"_id": "%s:%s:%s:%s" % (s["camera_id"], s["channel"], key, slot.strftime("%Y%m%d%H%M"))},
            {"$inc": inc, "$max": dict(wmax, veh_max=max(counts) if counts else 0),
             "$setOnInsert": {"camera_id": s["camera_id"], "channel": s["channel"], "camera_name": s.get("camera_name", ""),
                              "zone": key, "slot": slot, "slot_min": STATS_SLOT_MIN,
                              "day": loc.strftime("%Y-%m-%d"), "dow": loc.weekday(), "hm": loc.strftime("%H:%M")},
             "$set": dict(wset, zone_name=name)},
            upsert=True)
    upd = {"in_stats": True}
    if wz is not None and "waze" not in s:
        upd["waze"] = wz
        s["waze"] = wz  # relu par review_of
    db["ia_capture_samples"].update_one({"_id": s["_id"]}, {"$set": upd})
    return len(targets)


def rebuild_stats(db, camera_id, channel):
    """Refait les quarts d'heure d'un canal encore couverts par des mesures en
    base (apres un changement d'itineraires Waze lies) ; les plus anciens,
    dont les mesures sont purgees, restent tels quels. Le 1er quart d'heure,
    peut-etre deja ecorne par la purge, n'est pas refait."""
    first = db["ia_capture_samples"].find_one({"camera_id": camera_id, "channel": channel, "purpose": {"$nin": ["crowd", "secours"]}},
                                              {"at": 1}, sort=[("at", 1)])
    if not first:
        return 0
    at = first["at"]
    start = at.replace(minute=at.minute - at.minute % STATS_SLOT_MIN, second=0, microsecond=0) + timedelta(minutes=STATS_SLOT_MIN)
    db["ia_capture_stats"].delete_many({"camera_id": camera_id, "channel": channel, "slot": {"$gte": start}})
    q = {"camera_id": camera_id, "channel": channel, "purpose": {"$nin": ["crowd", "secours"]}, "at": {"$gte": start}}
    db["ia_capture_samples"].update_many(q, {"$unset": {"in_stats": "", "waze": ""}})
    zones = list(db["ia_capture_zones"].find({"camera_id": camera_id, "channel": channel}))
    n = 0
    for s in db["ia_capture_samples"].find(q, {"photos": 0}).sort("at", 1):
        add_stats(db, s, zones)
        n += 1
    return n


def backfill_stats(db):
    """Ajoute aux statistiques les mesures encore en base qui n'y sont pas
    (rattrapage ; les plus recentes, en cours de traitement, attendent)."""
    zones_of, n = {}, 0
    q = {"purpose": {"$nin": ["crowd", "secours"]}, "in_stats": {"$ne": True},
         "at": {"$lt": datetime.now(timezone.utc) - timedelta(minutes=5)}}
    for s in db["ia_capture_samples"].find(q, {"photos": 0}).sort("at", 1):
        k = (s["camera_id"], s["channel"])
        if k not in zones_of:
            zones_of[k] = list(db["ia_capture_zones"].find({"camera_id": k[0], "channel": k[1]}))
        add_stats(db, s, zones_of[k])
        n += 1
    return n


# ---------------------------------------------------------------------------
# Alertes : regles "traffic_camera" de la centrale d'alertes
# ---------------------------------------------------------------------------
_WA = None


def _whatsapp():
    global _WA
    if _WA is None:
        try:
            from whatsapp import WhatsAppService
            _WA = WhatsAppService
        except Exception as e:
            log.warning("WhatsApp indisponible (%s)", e)
            _WA = False
    return _WA or None


def dispatch_traffic(db, d, ch, sample):
    """Une alerte par episode de ralentissement / bouchon et par regle : quand
    les `consecutive` dernieres mesures sont toutes au moins a `min_state`.
    L'episode se ferme a la premiere mesure en dessous ; `cooldown_min` evite
    de re-alerter aussitot apres. Meme format d'alerte que le recepteur
    (cockpit_dispatch) : photo, WhatsApp de la regle."""
    key = "%s:%s" % (d["_id"], ch.get("no"))
    defs = [x for x in db["cockpit_alert_definitions"].find({"detection_type": "traffic_camera", "enabled": True})
            if key in ((x.get("params") or {}).get("channels") or [])]
    if not defs:
        return 0
    if "image" in (sample.get("zones") or {}):
        # Sans zone, les vehicules gares (parking, trottoir) comptent comme
        # arretes : 151 Giratoire Technoparc donnait "bouchon" a vide
        log.info("%s : pas de zone de mesure, aucune alerte trafic (zones a dessiner dans la page IA)", d.get("name"))
        return 0
    now = datetime.now(timezone.utc)
    st = db["ia_capture_state"].find_one({"_id": key}) or {}
    episodes = dict(st.get("alert_episodes") or {})
    rank = STATE_ORDER.get(sample["state"], -1)
    created, wa_batch = 0, []
    for df in defs:
        p = df.get("params") or {}
        slug = df.get("slug")
        min_state = p.get("min_state") if p.get("min_state") in ("ralenti", "bouchon") else "bouchon"
        need = max(1, min(10, _int(p.get("consecutive"), 2)))
        ep = episodes.get(slug) or {}
        if rank < STATE_ORDER[min_state]:
            if ep.get("open"):
                episodes[slug] = dict(ep, open=False, closed_at=now)
            continue
        if ep.get("open"):
            continue  # deja alerte pour cet episode
        last = list(db["ia_capture_samples"].find({"camera_id": d["_id"], "channel": ch.get("no")}, {"state": 1})
                    .sort("at", -1).limit(need))
        if len(last) < need or any(STATE_ORDER.get(s.get("state"), -1) < STATE_ORDER[min_state] for s in last):
            continue
        cool = _int(p.get("cooldown_min"), 30)
        if ep.get("alerted_at") and now - ep["alerted_at"].replace(tzinfo=timezone.utc) < timedelta(minutes=cool):
            episodes[slug] = dict(ep, open=True)
            continue
        bad_zones = [z.get("name") for z in (sample.get("zones") or {}).values()
                     if STATE_ORDER.get(z.get("state"), -1) >= STATE_ORDER[min_state]]
        va = sample.get("vehicles_a") or []
        label = "Bouchon" if sample["state"] == "bouchon" else "Ralentissement"
        msg = "%s sur %s%s : %d vehicule(s), %d arrete(s)" % (
            label, d.get("name", ""), " (%s)" % ", ".join(bad_zones) if bad_zones and bad_zones != ["Image entiere"] else "",
            len(va), sum(1 for v in va if v.get("status") == "arrete"))
        loc = now.astimezone(detection_arming.PARIS)
        snap = os.path.abspath(os.path.join(HIK_IMAGE_DIR, sample["photos"][0]["photo"]))
        alert = {
            "definition_slug": slug, "event": "", "year": "",
            "title": "Trafic - " + label.lower(), "message": msg, "timeStr": loc.strftime("%H:%M:%S"),
            "actionData": {
                "event_type": "traffic_" + sample["state"], "event_label": label,
                "camera_path": "capture:" + key, "camera_label": d.get("name", ""),
                "camera_location": d.get("site", ""), "camera_id": str(d["_id"]), "channel": ch.get("no"),
                "icon": df.get("icon") or "traffic", "color": df.get("color") or "#dc2626",
                "snapshot_path": snap, "has_snapshot": os.path.isfile(snap),
                "traffic_state": sample["state"], "sample_id": str(sample["_id"]),
                "vehicles": len(va), "stopped": sum(1 for v in va if v.get("status") == "arrete"),
                "zones": bad_zones,
            },
            "dedup_key": "trafic-%s-%s-%s" % (slug, key, now.strftime("%Y%m%d%H%M")),
            "triggeredAt": now, "expiresAt": now + timedelta(minutes=10),
        }
        res = db["cockpit_active_alerts"].update_one({"dedup_key": alert["dedup_key"]}, {"$setOnInsert": alert}, upsert=True)
        if res.upserted_id is not None:
            alert["_id"] = res.upserted_id
            created += 1
            wa_batch.append((alert, df))
            log.info("Alerte trafic %s : %s", slug, msg)
        episodes[slug] = {"open": True, "alerted_at": now, "state": sample["state"]}
    db["ia_capture_state"].update_one({"_id": key}, {"$set": {"alert_episodes": episodes}}, upsert=True)
    if wa_batch and _whatsapp():
        try:
            _whatsapp()(db).notify_batch(wa_batch)
        except Exception as e:
            log.warning("WhatsApp trafic : envoi impossible (%s)", e)
    return created


def _create_alert(db, df, d, ch, sample, key, kind, label, msg, extra):
    """Alerte au format du recepteur (cockpit_dispatch) : photo A, WhatsApp de la regle."""
    now = datetime.now(timezone.utc)
    loc = now.astimezone(detection_arming.PARIS)
    snap = os.path.abspath(os.path.join(HIK_IMAGE_DIR, sample["photos"][0]["photo"]))
    alert = {
        "definition_slug": df.get("slug"), "event": "", "year": "",
        "title": label, "message": msg, "timeStr": loc.strftime("%H:%M:%S"),
        "actionData": dict({
            "event_type": kind, "event_label": label,
            "camera_path": "capture:" + key, "camera_label": d.get("name", ""),
            "camera_location": d.get("site", ""), "camera_id": str(d["_id"]), "channel": ch.get("no"),
            "icon": df.get("icon") or "traffic", "color": df.get("color") or "#dc2626",
            "snapshot_path": snap, "has_snapshot": os.path.isfile(snap), "sample_id": str(sample["_id"]),
        }, **extra),
        "dedup_key": "%s-%s-%s-%s" % (kind, df.get("slug"), key, extra.get("dedup") or now.strftime("%Y%m%d%H%M")),
        "triggeredAt": now, "expiresAt": now + timedelta(minutes=10),
    }
    res = db["cockpit_active_alerts"].update_one({"dedup_key": alert["dedup_key"]}, {"$setOnInsert": alert}, upsert=True)
    if res.upserted_id is None:
        return None
    alert["_id"] = res.upserted_id
    log.info("Alerte %s %s : %s", kind, df.get("slug"), msg)
    return alert


def dispatch_parking(db, d, ch, sample):
    """Regles "parking_camera" : un vehicule arrete dans une zone de
    stationnement interdit depuis plus que la duree de la zone. Une alerte par
    vehicule stationne (zone + heure d'arrivee) et par regle."""
    key = "%s:%s" % (d["_id"], ch.get("no"))
    over = [(zid, z, p) for zid, z in (sample.get("zones") or {}).items() if z.get("kind") == "stationnement"
            for p in z.get("parked") or [] if p.get("over")]
    if not over:
        return 0
    defs = [x for x in db["cockpit_alert_definitions"].find({"detection_type": "parking_camera", "enabled": True})
            if key in ((x.get("params") or {}).get("channels") or [])]
    if not defs:
        return 0
    st = db["ia_capture_state"].find_one({"_id": key}) or {}
    done = dict(st.get("parking_alerted") or {})
    wa_batch = []
    for df in defs:
        seen = list(done.get(df["slug"]) or [])
        for zid, z, p in over:
            ident = "%s@%s" % (zid, p["since"].strftime("%Y%m%d%H%M"))
            if ident in seen:
                continue  # deja alerte pour ce vehicule a cette place
            msg = "Stationnement interdit sur %s (zone %s) : vehicule arrete depuis %d min (max %d min)" % (
                d.get("name", ""), z.get("name", ""), p["minutes"], z.get("max_min", 0))
            a = _create_alert(db, df, d, ch, sample, key, "parking", "Stationnement interdit", msg, {
                "zones": [z.get("name", "")], "parked_min": p["minutes"], "max_min": z.get("max_min"),
                "since": p["since"].isoformat(), "box": p["box"], "dedup": ident.replace("@", "-")})
            if a:
                wa_batch.append((a, df))
            seen.append(ident)
        done[df["slug"]] = seen[-50:]
    db["ia_capture_state"].update_one({"_id": key}, {"$set": {"parking_alerted": done}}, upsert=True)
    if wa_batch and _whatsapp():
        try:
            _whatsapp()(db).notify_batch(wa_batch)
        except Exception as e:
            log.warning("WhatsApp stationnement : envoi impossible (%s)", e)
    return len(wa_batch)


LOW_INTEREST_RETENTION_DAYS = int(os.getenv("CAPTURE_LOW_INTEREST_DAYS", "7"))


def _purge_samples(db, q):
    n = 0
    for s in db["ia_capture_samples"].find(q, {"photos": 1}).limit(5000):
        for p in s.get("photos") or []:
            for rel in (p.get("photo"), p.get("thumb")):
                try:
                    os.remove(os.path.join(HIK_IMAGE_DIR, rel))
                except OSError:
                    pass
        db["ia_capture_samples"].delete_one({"_id": s["_id"]})
        n += 1
    return n


def purge(db):
    """Captures sans avis : SANS INTERET pour l'apprentissage (`review.score`
    0, hors tirage de controle) apres LOW_INTEREST_RETENTION_DAYS (7 j),
    les autres apres CAPTURE_RETENTION_DAYS (30 j). Jamais celles annotees
    (entrainement). Trafic : seulement une fois comptee dans les
    statistiques (`in_stats`), gardees, elles, sans limite."""
    st = db["ia_capture_state"].find_one({"_id": "_purge"}) or {}
    now = datetime.now(timezone.utc)
    if st.get("at") and now - st["at"].replace(tzinfo=timezone.utc) < timedelta(hours=6):
        return
    keep = {"label": {"$exists": False}, "vote": {"$exists": False}, "zone_votes": {"$exists": False}}
    n_old = _purge_samples(db, dict(keep, at={"$lt": now - timedelta(days=CAPTURE_RETENTION_DAYS)}))
    n_low = _purge_samples(db, dict(keep, **{
        "at": {"$lt": now - timedelta(days=LOW_INTEREST_RETENTION_DAYS)}, "review.score": 0,
        "$or": [{"in_stats": True}, {"purpose": {"$in": ["crowd", "secours"]}}]}))
    db["ia_capture_state"].update_one({"_id": "_purge"}, {"$set": {"at": now, "removed": n_old + n_low, "removed_low": n_low}}, upsert=True)
    if n_old or n_low:
        log.info("Purge : %d capture(s) de plus de %d jours, %d sans interet de plus de %d jours supprimee(s)",
                 n_old, CAPTURE_RETENTION_DAYS, n_low, LOW_INTEREST_RETENTION_DAYS)


def capture_enabled(db):
    """{traffic, crowd, secours: {on, cell}} : interrupteur general de la
    page IA (en arret, aucune photo prise pour cette fonction, meme avec
    --force ; cell faux : cameras 4G sautees, data preservee)."""
    d = db["ia_capture_state"].find_one({"_id": "_switch"}) or {}
    return {p: {"on": (d.get(p) or {}).get("on", True) is not False,
                "cell": (d.get(p) or {}).get("cell", True) is not False} for p in ("traffic", "crowd", "secours")}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Capture IA (paires de photos analysees par l'IA locale)")
    ap.add_argument("--dry-run", action="store_true", help="capture et analyse, n'ecrit rien en base")
    ap.add_argument("--force", action="store_true", help="toutes les cameras, meme non dues ou hors planning")
    ap.add_argument("--stats-backfill", action="store_true", help="ajoute aux statistiques les mesures qui n'y sont pas, puis sort")
    ap.add_argument("--review-backfill", action="store_true", help="calcule l'interet (galerie) de toutes les mesures, puis sort")
    args = ap.parse_args(argv)
    _setup_logging()
    if args.review_backfill:
        log.info("Galerie : interet de %d mesure(s) calcule", backfill_review(_get_db()))
        return 0
    if args.stats_backfill:
        db = _get_db()
        ensure_indexes(db)
        log.info("Statistiques : %d mesure(s) ajoutee(s)", backfill_stats(db))
        return 0
    lock = _acquire_lock()
    if lock is None:
        log.info("Passage precedent encore en cours : sortie.")
        return 0
    if not camera_secrets.available():
        log.error("COCKPIT_CAMERA_KEY absente pour ce compte : la tache doit tourner sous le compte de Cockpit.")
        return 2
    db = _get_db()
    ensure_indexes(db)
    now = datetime.now(timezone.utc)
    if not args.dry_run:
        # Etiquettes [cam:N] apparues / retirees dans les noms Waze : a refaire
        routes = ((db["waze_trafic"].find_one({"_id": "latest"}) or {}).get("data") or {}).get("routes") or []
        if routes:
            for d, ch, role in capture_channels(db):
                if role.get("purpose") in ("crowd", "secours"):
                    continue
                key = "%s:%s" % (d["_id"], ch.get("no"))
                tagged = sorted(str(r.get("id")) for r in routes
                                if waze_tagged(r.get("name"), cam_number(d.get("name")), ch.get("no")))
                lk = db["ia_capture_links"].find_one({"_id": key}) or {}
                if tagged != (lk.get("tagged") or []):
                    db["ia_capture_links"].update_one({"_id": key}, {"$set": {
                        "camera_id": d["_id"], "channel": ch.get("no"), "tagged": tagged, "rebuild": True}}, upsert=True)
        # Itineraires Waze lies changes : statistiques refaites ici (jamais
        # dans Cockpit)
        for lk in db["ia_capture_links"].find({"rebuild": True}):
            db["ia_capture_links"].update_one({"_id": lk["_id"]}, {"$unset": {"rebuild": ""}})
            try:
                n = rebuild_stats(db, lk["camera_id"], lk["channel"])
                log.info("Statistiques refaites pour %s (itineraires Waze) : %d mesure(s)", lk["_id"], n)
            except Exception as e:
                log.warning("Statistiques non refaites pour %s (%s)", lk["_id"], e)
    todo = []
    switch = capture_enabled(db)
    for d, ch, role in capture_channels(db):
        sw = switch[role.get("purpose") if role.get("purpose") in ("crowd", "secours") else "traffic"]
        if not sw["on"] or (not sw["cell"] and cameras.is_cellular(d)):
            continue
        key = "%s:%s" % (d["_id"], ch.get("no"))
        st = db["ia_capture_state"].find_one({"_id": key}) or {}
        last = st.get("last_try")
        due = args.force or not last or (now - last.replace(tzinfo=timezone.utc)).total_seconds() >= \
            _int(role.get("interval_min"), 3) * 60 - DUE_MARGIN_S
        if not due:
            continue
        armed, _g = detection_arming.channel_armed(db, d["_id"], ch.get("no"))
        if not armed and not args.force:
            db["ia_capture_state"].update_one({"_id": key}, {"$set": {"planning": "off", "checked_at": now}}, upsert=True)
            continue
        if not args.dry_run:
            db["ia_capture_state"].update_one({"_id": key}, {"$set": {"last_try": now, "planning": "on"}}, upsert=True)
        todo.append((d, ch, role))
    rc = 0
    if todo:
        def run(item):
            d, ch, role = item
            try:
                capture_one(db, d, ch, role, dry_run=args.dry_run)
                return 0
            except Exception as e:
                msg = cameras.camera_error(d, e, "Capture IA") if not isinstance(e, (urllib.error.URLError, KeyError)) \
                    else "Service IA injoignable ou reponse invalide (%s)" % e
                log.warning("%s canal %s : capture IA impossible (%s)", d.get("name"), ch.get("no"), msg)
                if not args.dry_run:
                    db["ia_capture_state"].update_one({"_id": "%s:%s" % (d["_id"], ch.get("no"))},
                                                      {"$set": {"last_error": msg, "error_at": datetime.now(timezone.utc)}},
                                                      upsert=True)
                return 1
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(todo))) as ex:
            rc = max(ex.map(run, todo))
    if not args.dry_run:
        purge(db)
    return rc


if __name__ == "__main__":
    sys.exit(main())
