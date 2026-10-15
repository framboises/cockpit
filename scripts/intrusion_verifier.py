"""Verification locale (GPU) des intrusions avant l'alerte.

Service NSSM "IntrusionVerifier", 127.0.0.1:4011 uniquement, venv titan_prod
(celui de Tagger : torch CUDA + ultralytics + transformers). Les modeles sont
charges UNE fois au demarrage (~30 s) et restent en memoire.

Le recepteur Hik (PCA/SCRIPTS/intrusions.py) envoie le chemin de la photo et le
cadre de la cible donne par la camera ; on analyse un zoom autour de ce cadre :
  - YOLO11m COCO (Tagger) : DECIDE. Personne / vehicule vus ou non, avec leurs
    cadres et leur confiance (15-20 ms). Fiable sur les personnes, y compris
    derriere un grillage.
  - SigLIP2 so400m (Tagger), classification zero-shot du zoom : EXPLIQUE ce que
    c'est (oiseau 95 %, panneau 84 %...), ~160 ms. Ne decide jamais : il voit
    parfois "grillage" sur une personne derriere une cloture.
Le recepteur decide (seuil, alerte ou non) et laisse partir l'alerte si ce
service ne repond pas.

  POST /verify  {"photo": "intrusions/AAAA/MM/JJ/<cam>/<fichier>.jpg", "rect": {x, y, w, h}}
    -> {real_conf, label, conf, detections: [{class, category, conf, box}],
        guess: [{label, p}], ms, model}
       box = cadre normalise (0-1) sur la PHOTO ENTIERE, comme rect.
  POST /embed    empreintes SigLIP du cadrage serre (entrainement de nuit)
  POST /detect   vehicules et personnes sur l'image entiere (Trafic)
  POST /density  personnes par zone, carte de densite ZIP-B (Foule)
  POST /pose     squelette de chaque personne, yolo11m-pose (Secours)
  GET  /health
"""
import logging
import os
import threading
import time

import torch
from flask import Flask, jsonify, request
from PIL import Image
from ultralytics import YOLO
from waitress import serve

HOST = "127.0.0.1"
PORT = int(os.getenv("INTRUSION_VERIFIER_PORT", "4011"))
IMAGES_ROOT = os.path.abspath(os.getenv("HIK_IMAGES_ROOT", r"E:\TITAN\production\hik_images"))
MODELS = r"E:\TITAN\production\tagger\models\analytics"
MODEL_PATH = os.getenv("INTRUSION_VERIFIER_MODEL", os.path.join(MODELS, "ground", "yolo11m.pt"))
SIGLIP_PATH = os.getenv("INTRUSION_VERIFIER_SIGLIP", os.path.join(MODELS, "photo_embedder_siglip"))
# Deux cadrages autour de la cible, on garde le meilleur (mesure du 14/10/2026
# sur 284 intrusions filtrees, ~220 etaient de vraies personnes / vehicules :
# un carre de 160 px agrandi a 640 devient flou et YOLO ne voit plus rien) :
#   (zoom, cote min. en px, imgsz) : large avec contexte, et petit sans agrandir
FRAMINGS = ((8.0, 320, 640), (4.0, 160, 320))
MIN_CONF = 0.1  # detections renvoyees ; le seuil d'alerte est cote recepteur
# SigLIP seul ne decide jamais, mais s'il voit une personne / un vehicule
# (>= SIG_REAL) et que YOLO en devine un (>= SIG_YOLO_MIN), on retient l'avis
# SigLIP comme confiance (vehicules eblouis par leurs phares, silhouettes
# de 30 px). 0 fausse alerte validee par l'operateur reclassee au test.
SIG_REAL, SIG_YOLO_MIN = 0.6, 0.1

# Classes COCO -> categorie
CATEGORY = {
    "person": "personne",
    "car": "vehicule", "truck": "vehicule", "bus": "vehicule", "motorcycle": "vehicule",
    "bicycle": "velo", "train": "vehicule",
    "bird": "oiseau",
    "cat": "animal", "dog": "animal", "horse": "animal", "sheep": "animal", "cow": "animal",
    "bear": "animal", "elephant": "animal", "zebra": "animal", "giraffe": "animal",
    "stop sign": "panneau", "traffic light": "panneau",
}
REAL = {"personne", "vehicule", "velo"}
# Hypotheses de SigLIP (libelle affiche -> texte compare a l'image)
GUESSES = {
    "personne": "a photo of a person",
    "vehicule": "a photo of a car",
    "oiseau": "a photo of a bird",
    "animal": "a photo of a cat, a dog or a fox",
    "panneau": "a photo of a road sign",
    "branche": "a photo of tree branches and leaves",
    "reflet": "a photo of a light reflection or glare",
    "sol": "a photo of empty ground or road",
    "grillage": "a photo of a fence or a gate",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("intrusion_verifier")

DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"
_t0 = time.time()
MODEL = YOLO(MODEL_PATH)
MODEL.predict(Image.new("RGB", (640, 640)), device=DEVICE, verbose=False)  # chauffe CUDA
log.info("YOLO %s charge sur %s en %.1f s", os.path.basename(MODEL_PATH), DEVICE, time.time() - _t0)

SIG = SIG_PROC = SIG_TEXT = None


def _feat(out):
    # transformers 5 peut renvoyer un objet de sortie au lieu du tenseur
    t = out if torch.is_tensor(out) else getattr(out, "pooler_output", None)
    return t / t.norm(dim=-1, keepdim=True)


try:  # facultatif : sans SigLIP, la decision reste possible
    from transformers import AutoModel, AutoProcessor
    _t0 = time.time()
    SIG_PROC = AutoProcessor.from_pretrained(SIGLIP_PATH)
    SIG = AutoModel.from_pretrained(SIGLIP_PATH).to(DEVICE).eval()
    _keys = list(GUESSES)
    with torch.no_grad():  # textes encodes une fois pour toutes
        _ti = SIG_PROC(text=[GUESSES[k] for k in _keys], padding="max_length", max_length=64,
                       return_tensors="pt").to(DEVICE)
        SIG_TEXT = _feat(SIG.get_text_features(**_ti))
    log.info("SigLIP %s charge en %.1f s", os.path.basename(SIGLIP_PATH), time.time() - _t0)
except Exception as e:
    SIG = None
    log.warning("SigLIP indisponible (%s) : pas d'explication, decision YOLO seule", e)

# Densite de foule : ZIP-B (NWPU-Crowd, MIT, Yiming-M/EBC-ZIP), poids ranges
# dans le dossier de modeles de Tagger (un seul exemplaire pour Cockpit et
# Tagger). Fenetres glissantes de 672 px, normalisation CLIP. Facultatif.
ZIP_DIR = os.getenv("INTRUSION_VERIFIER_ZIP", os.path.join(MODELS, "crowd", "zip"))
ZIP_WEIGHTS = os.path.join(ZIP_DIR, "weights_nwpu_b", "ebc_b_best", "best_mae.pth")
ZIP_WIN = 672
DENSITY = None
try:
    import sys as _sys
    import importlib.util as _ilu
    _code = os.path.join(ZIP_DIR, "code", "ZIP-main")
    if _code not in _sys.path:
        _sys.path.insert(0, _code)
    from models import get_model as _zip_get_model
    _spec = _ilu.spec_from_file_location("zip_eval_utils", os.path.join(_code, "utils", "eval_utils.py"))
    _zev = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_zev)
    _t0 = time.time()
    DENSITY = _zip_get_model(model_info_path=ZIP_WEIGHTS).to(DEVICE).eval()
    _ZIP_MEAN = torch.tensor((0.48145466, 0.4578275, 0.40821073), device=DEVICE).view(1, 3, 1, 1)
    _ZIP_STD = torch.tensor((0.26862954, 0.26130258, 0.27577711), device=DEVICE).view(1, 3, 1, 1)
    log.info("Densite ZIP-B charge en %.1f s", time.time() - _t0)
except Exception as e:
    DENSITY = None
    log.warning("Modele de densite indisponible (%s) : analyse Foule desactivee", e)

# Pose (squelette, 17 points COCO) : personnes au sol, mode Secours.
# yolo11m-pose (Ultralytics, telecharge le 11/10/2026). Facultatif.
POSE_PATH = os.getenv("INTRUSION_VERIFIER_POSE", os.path.join(MODELS, "ground", "yolo11m-pose.pt"))
POSE = None
try:
    _t0 = time.time()
    POSE = YOLO(POSE_PATH)
    POSE.predict(Image.new("RGB", (640, 640)), device=DEVICE, verbose=False)
    log.info("Pose %s charge en %.1f s", os.path.basename(POSE_PATH), time.time() - _t0)
except Exception as e:
    POSE = None
    log.warning("Modele de pose indisponible (%s) : analyse Secours desactivee", e)

_LOCK = threading.Lock()  # un seul passage GPU a la fois
_STATS = {"checks": 0, "errors": 0, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

app = Flask(__name__)


def _photo_path(rel):
    full = os.path.abspath(os.path.join(IMAGES_ROOT, rel or ""))
    if os.path.commonpath([full, IMAGES_ROOT]) != IMAGES_ROOT or not os.path.isfile(full):
        return None
    return full


def _zoom_box(im, rect, zoom, min_px):
    W, H = im.size
    if not rect or not rect.get("w") or not rect.get("h"):
        return (0, 0, W, H)
    cx, cy = (rect["x"] + rect["w"] / 2) * W, (rect["y"] + rect["h"] / 2) * H
    half = max(min_px, rect["w"] * W * zoom, rect["h"] * H * zoom) / 2
    return (max(0, int(cx - half)), max(0, int(cy - half)), min(W, int(cx + half)), min(H, int(cy + half)))


# Avis appris sur les reponses des operateurs (scripts/intrusion_training.py,
# niveau 2) : remplace les phrases toutes faites quand il existe. Relu a
# chaud (date du fichier, controlee toutes les 30 s) : pas de redemarrage.
HEAD_PATH = os.getenv("INTRUSION_VERIFIER_HEAD", os.path.join(MODELS, "intrusion_head", "head.json"))
_HEAD = {"checked": 0.0, "mtime": None, "W": None, "b": None, "version": None}


def _head():
    now = time.time()
    if now - _HEAD["checked"] < 30:
        return _HEAD if _HEAD["W"] is not None else None
    _HEAD["checked"] = now
    try:
        mt = os.path.getmtime(HEAD_PATH)
    except OSError:
        _HEAD.update(mtime=None, W=None, b=None, version=None)
        return None
    if mt != _HEAD["mtime"]:
        _HEAD["mtime"] = mt
        try:
            import json
            with open(HEAD_PATH, encoding="utf-8") as f:
                h = json.load(f)
            if h.get("keys") != list(GUESSES):
                raise ValueError("categories differentes de GUESSES")
            _HEAD.update(W=torch.tensor(h["W"], device=DEVICE), b=torch.tensor(h["b"], device=DEVICE),
                         version=h.get("version"))
            log.info("Avis appris charge : version %s (%s exemples)", h.get("version"), h.get("n"))
        except Exception as e:
            _HEAD.update(W=None, b=None, version=None)
            log.warning("Avis appris illisible (%s) : phrases d'origine", e)
    return _HEAD if _HEAD["W"] is not None else None


def _sig_feature(crop):
    with torch.no_grad():
        ii = SIG_PROC(images=crop, return_tensors="pt").to(DEVICE)
        return _feat(SIG.get_image_features(**ii))


def _guess(crop):
    if SIG is None:
        return []
    f = _sig_feature(crop)
    h = _head()
    with torch.no_grad():
        if h:
            logits = (f @ h["W"].T)[0] * SIG.logit_scale.exp() + h["b"]
        else:
            logits = (f @ SIG_TEXT.T)[0] * SIG.logit_scale.exp()
        p = torch.softmax(logits, -1).cpu().tolist()
    keys = list(GUESSES)
    return [{"label": k, "p": round(v, 3)} for k, v in sorted(zip(keys, p), key=lambda x: -x[1])[:4]]


@app.get("/health")
def health():
    return jsonify({"ok": True, "device": DEVICE, "model": os.path.basename(MODEL_PATH),
                    "siglip": os.path.basename(SIGLIP_PATH) if SIG is not None else None,
                    "density": "ZIP-B NWPU" if DENSITY is not None else None,
                    "pose": os.path.basename(POSE_PATH) if POSE is not None else None,
                    "head": (_head() or {}).get("version"), **_STATS})


@app.post("/embed")
def embed():
    """Empreintes SigLIP du cadrage serre (le meme que l'avis de /verify),
    pour l'entrainement de nuit. {"items": [{photo, rect}] (32 max)} ->
    {"features": [[...] | null], "keys", "text": empreintes des phrases,
    "scale"}. Ne change rien au service."""
    if SIG is None:
        return jsonify({"error": "SigLIP indisponible"}), 503
    items = ((request.get_json(silent=True) or {}).get("items") or [])[:32]
    zoom, min_px, _ = FRAMINGS[-1]
    out = []
    for it in items:
        path = _photo_path(it.get("photo"))
        if not path:
            out.append(None)
            continue
        try:
            im = Image.open(path).convert("RGB")
            with _LOCK:
                f = _sig_feature(im.crop(_zoom_box(im, it.get("rect"), zoom, min_px)))
            out.append([round(v, 5) for v in f[0].cpu().tolist()])
        except Exception as e:
            log.warning("Empreinte impossible (%s) : %s", it.get("photo"), e)
            out.append(None)
    return jsonify({"features": out, "keys": list(GUESSES), "text": SIG_TEXT.cpu().tolist(),
                    "scale": float(SIG.logit_scale.exp())})


@app.post("/verify")
def verify():
    data = request.get_json(silent=True) or {}
    path = _photo_path(data.get("photo"))
    if not path:
        return jsonify({"error": "photo introuvable"}), 404
    t = time.time()
    dets = []
    try:
        im = Image.open(path).convert("RGB")
        W, H = im.size
        with _LOCK:
            for i, (zoom, min_px, size) in enumerate(FRAMINGS):
                x0, y0, x1, y1 = _zoom_box(im, data.get("rect"), zoom, min_px)
                crop = im.crop((x0, y0, x1, y1))
                res = MODEL.predict(crop, imgsz=size, device=DEVICE, conf=MIN_CONF, classes=None, verbose=False)[0]
                for b in res.boxes:
                    bx0, by0, bx1, by1 = [float(v) for v in b.xyxy[0]]
                    name = res.names[int(b.cls)]
                    dets.append({"class": name, "category": CATEGORY.get(name, "objet"), "conf": round(float(b.conf), 3),
                                 "box": {"x": round((x0 + bx0) / W, 4), "y": round((y0 + by0) / H, 4),
                                         "w": round((bx1 - bx0) / W, 4), "h": round((by1 - by0) / H, 4)}})
                if i == len(FRAMINGS) - 1:
                    guess = _guess(crop)  # SigLIP sur le cadrage serre (zoom x4)
    except Exception as e:
        _STATS["errors"] += 1
        log.warning("Verification impossible (%s) : %s", data.get("photo"), e)
        return jsonify({"error": str(e)}), 500
    dets = _dedupe(sorted(dets, key=lambda d: -d["conf"]))
    real = [d for d in dets if d["category"] in REAL]
    best = real[0] if real else (dets[0] if dets else None)
    yolo_real = real[0]["conf"] if real else 0.0
    real_conf, by = yolo_real, "detecteur"
    sig = [g for g in guess if g["label"] in ("personne", "vehicule")]
    sig_p = sum(g["p"] for g in sig)
    if yolo_real >= SIG_YOLO_MIN and sig_p >= SIG_REAL and sig_p > yolo_real:
        real_conf, by = round(sig_p, 3), "detecteur+avis"
    label = best["category"] if best else "rien"
    if by != "detecteur" and sig:
        label = max(sig, key=lambda g: g["p"])["label"]
    _STATS["checks"] += 1
    out = {
        "real_conf": real_conf,
        "yolo_conf": yolo_real,
        "by": by,
        "label": label,
        "conf": real_conf if real_conf else (best["conf"] if best else 0.0),
        "detections": dets[:6],
        "guess": guess,
        "ms": round((time.time() - t) * 1000),
        "model": os.path.basename(MODEL_PATH),
        "head": _HEAD["version"] if _HEAD["W"] is not None else None,
    }
    log.info("%s -> %s %.2f (personne/vehicule %.2f, %s) avis %s %d ms", data.get("photo"), out["label"], out["conf"],
             out["real_conf"], by, ", ".join("%s %d%%" % (g["label"], g["p"] * 100) for g in guess[:2]), out["ms"])
    return jsonify(out)


def _iou(a, b):
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    inter = ix * iy
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _dedupe(dets):
    """Les deux cadrages voient souvent le meme objet : garde la meilleure
    confiance par objet (meme categorie, cadres qui se recouvrent)."""
    keep = []
    for d in dets:
        if not any(k["category"] == d["category"] and _iou(k["box"], d["box"]) > 0.5 for k in keep):
            keep.append(d)
    return keep


VEHICLES = {"car", "truck", "bus", "motorcycle", "bicycle"}


@app.post("/detect")
def detect():
    """Detection sur l'IMAGE ENTIERE (captures trafic) : vehicules et
    personnes. {"photos": ["captures/...jpg", ...]} -> {"results": [[{class,
    category, conf, box}], ...]} (une liste par photo, cadres normalises)."""
    data = request.get_json(silent=True) or {}
    rels = (data.get("photos") or [])[:4]
    paths = [_photo_path(r) for r in rels]
    if not rels or not all(paths):
        return jsonify({"error": "photo introuvable"}), 404
    t = time.time()
    try:
        ims = [Image.open(p).convert("RGB") for p in paths]
        with _LOCK:
            res = MODEL.predict(ims, imgsz=1280, device=DEVICE, conf=0.2, classes=None, verbose=False)
    except Exception as e:
        _STATS["errors"] += 1
        log.warning("Detection impossible (%s) : %s", rels, e)
        return jsonify({"error": str(e)}), 500
    out = []
    for im, r in zip(ims, res):
        W, H = im.size
        dets = []
        for b in r.boxes:
            name = r.names[int(b.cls)]
            if name not in VEHICLES and name != "person":
                continue
            x0, y0, x1, y1 = [float(v) for v in b.xyxy[0]]
            dets.append({"class": name, "category": CATEGORY.get(name, "objet"), "conf": round(float(b.conf), 3),
                         "box": {"x": round(x0 / W, 4), "y": round(y0 / H, 4),
                                 "w": round((x1 - x0) / W, 4), "h": round((y1 - y0) / H, 4)}})
        out.append(dets)
    _STATS["checks"] += 1
    log.info("detect %s -> %s vehicules/personnes, %d ms", rels[0], [len(d) for d in out], (time.time() - t) * 1000)
    return jsonify({"results": out, "ms": round((time.time() - t) * 1000), "model": os.path.basename(MODEL_PATH)})


def _poly_mask(poly, w, h):
    """Masque (h, w) d'un polygone en coordonnees 0-1."""
    from PIL import ImageDraw
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).polygon([(x * w, y * h) for x, y in poly], fill=1)
    return torch.from_numpy(__import__("numpy").asarray(m, dtype="float32"))


@app.post("/density")
def density():
    """Estimation du nombre de personnes (carte de densite ZIP-B).
    {"photo": "captures/...jpg", "zones": [{"id", "polygon": [[x, y], ...]}]}
    -> {"total", "zones": {id: personnes}, "persons_yolo": n, "grid": carte 0-1
    reduite (48 x 27 max, pour l'affichage), "ms"}"""
    if DENSITY is None:
        return jsonify({"error": "modele de densite indisponible"}), 503
    import numpy as np
    data = request.get_json(silent=True) or {}
    path = _photo_path(data.get("photo"))
    if not path:
        return jsonify({"error": "photo introuvable"}), 404
    t = time.time()
    try:
        im = Image.open(path).convert("RGB")
        x = torch.from_numpy(np.asarray(im).copy()).permute(2, 0, 1).float().unsqueeze(0).to(DEVICE) / 255.0
        x = (x - _ZIP_MEAN) / _ZIP_STD
        H, W = x.shape[-2:]
        if H < ZIP_WIN or W < ZIP_WIN:  # petite image : agrandie a la fenetre
            s = ZIP_WIN / min(H, W)
            x = torch.nn.functional.interpolate(x, size=(int(H * s + 0.5), int(W * s + 0.5)), mode="bicubic", align_corners=False)
        with _LOCK, torch.no_grad(), torch.autocast(device_type="cuda", enabled=DEVICE.startswith("cuda")):
            den = _zev.sliding_window_predict(DENSITY, x, ZIP_WIN, ZIP_WIN, 8)[0, 0].float()
            # ultralytics GARDE classes d'un appel a l'autre : les autres appels
            # passent classes=None, sinon /verify et /detect ne voient plus que des personnes
            yolo = MODEL.predict(im, imgsz=1280, device=DEVICE, conf=0.3, classes=[0], verbose=False)[0]
    except Exception as e:
        _STATS["errors"] += 1
        log.warning("Densite impossible (%s) : %s", data.get("photo"), e)
        return jsonify({"error": str(e)}), 500
    den = den.clamp(min=0).cpu()
    gh, gw = den.shape
    zones = {}
    for z in data.get("zones") or []:
        poly = z.get("polygon") or []
        if len(poly) >= 3:
            zones[str(z.get("id"))] = round(float((den * _poly_mask(poly, gw, gh)).sum()), 1)
    # Carte reduite pour l'affichage (somme par cellule, puis 0-1)
    cell = max(1, int(np.ceil(max(gh / 27, gw / 48))))
    g = torch.nn.functional.avg_pool2d(den[None, None], cell, cell, ceil_mode=True)[0, 0] * cell * cell
    gmax = float(g.max()) or 1.0
    persons = [[round(float(v) / im.width, 4) for v in b.xyxy[0]] for b in yolo.boxes]
    _STATS["checks"] += 1
    out = {"total": round(float(den.sum()), 1), "zones": zones, "persons_yolo": len(persons),
           "grid": [[round(float(v) / gmax, 2) for v in row] for row in g.tolist()], "grid_max": round(gmax, 2),
           "ms": round((time.time() - t) * 1000), "model": "ZIP-B NWPU"}
    log.info("densite %s -> %.1f personnes (YOLO %d), %d ms", data.get("photo"), out["total"], len(persons), out["ms"])
    return jsonify(out)


@app.post("/pose")
def pose():
    """Squelette de chaque personne sur l'IMAGE ENTIERE (mode Secours).
    {"photo": "captures/...jpg"} -> {"persons": [{conf, box, kpts: [[x, y,
    c] x 17]}], "size": [W, H], "ms"} ; box et points normalises (0-1),
    ordre COCO (0 nez ... 5-6 epaules, 11-12 hanches, 15-16 chevilles).
    La posture (debout / assis / allonge) est decidee par capture_ia."""
    if POSE is None:
        return jsonify({"error": "modele de pose indisponible"}), 503
    data = request.get_json(silent=True) or {}
    path = _photo_path(data.get("photo"))
    if not path:
        return jsonify({"error": "photo introuvable"}), 404
    t = time.time()
    try:
        im = Image.open(path).convert("RGB")
        W, H = im.size
        with _LOCK:
            # 1920 : personnes de ~100 px vues (1280 en manquait la moitie, essai du 11/10/2026)
            r = POSE.predict(im, imgsz=1920, device=DEVICE, conf=0.2, classes=None, verbose=False)[0]
    except Exception as e:
        _STATS["errors"] += 1
        log.warning("Pose impossible (%s) : %s", data.get("photo"), e)
        return jsonify({"error": str(e)}), 500
    persons = []
    kp = r.keypoints.data.cpu().tolist() if r.keypoints is not None else []
    for i, b in enumerate(r.boxes):
        x0, y0, x1, y1 = [float(v) for v in b.xyxy[0]]
        pts = kp[i] if i < len(kp) else []
        persons.append({"conf": round(float(b.conf), 3),
                        "box": {"x": round(x0 / W, 4), "y": round(y0 / H, 4), "w": round((x1 - x0) / W, 4), "h": round((y1 - y0) / H, 4)},
                        "kpts": [[round(p[0] / W, 4), round(p[1] / H, 4), round(p[2], 2)] for p in pts]})
    _STATS["checks"] += 1
    out = {"persons": persons, "size": [W, H], "ms": round((time.time() - t) * 1000), "model": os.path.basename(POSE_PATH)}
    log.info("pose %s -> %d personne(s), %d ms", data.get("photo"), len(persons), out["ms"])
    return jsonify(out)


if __name__ == "__main__":
    serve(app, host=HOST, port=PORT, threads=4)
