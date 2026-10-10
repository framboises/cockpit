"""LAPI par consultation : cameras Axis License Plate Verifier (LPV).

Ces cameras n'envoient rien au recepteur (pas de chemin d'envoi) : la LAPI
Porte Nord (Axis P1455-LE) commande la barriere, on ne modifie JAMAIS sa
configuration. On lit son journal avec les memes appels que l'interface LPV :

- premier passage (aucune lecture connue pour la camera) : base de depart =
  les 10 dernieres lectures (events.cgi?limit=10), pas de reprise d'historique ;
- passages suivants : TOUTES les lectures plus recentes que la derniere connue
  (search.cgi?timestampfrom=..., par pages de 100).

Lance toutes les 5 minutes par la tache planifiee "Cockpit - LAPI Axis"
(scripts/install_anpr_lpv_task.ps1), sous le compte de Cockpit (cle
COCKPIT_CAMERA_KEY). Rien ne tourne dans le process Flask.

Activation : usage LAPI d'un canal avec la source "axis_lpv" (page Cameras).
Les passages vont dans hik_anpr comme ceux du recepteur Hik, sous un chemin
interne (cameras.anpr_lpv_path, "/lpv-10-34-0-192"), et passent par le meme
dispatch (liste de surveillance, console Hik) que ecoutehik2.py.

    python anpr_lpv.py              # consultation (TITAN_ENV=prod)
    python anpr_lpv.py --dry-run    # interroge les cameras, n'ecrit rien
"""
import argparse
import importlib.util
import logging
import os
import random
import string
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import requests  # noqa: E402
from defusedxml import ElementTree as ET  # noqa: E402
from pymongo import MongoClient  # noqa: E402
from requests.auth import HTTPDigestAuth  # noqa: E402

import camera_secrets  # noqa: E402
import cameras  # noqa: E402

log = logging.getLogger("anpr_lpv")

FIRST_RUN_LIMIT = 10      # base de depart d'une camera jamais consultee
PAGE_SIZE = 100           # pagination de search.cgi (LPV garde 1000 lectures)
MAX_PAGES = 20
HTTP_TIMEOUT_S = 10
SOURCE = "axis_lpv"
DISPATCH_FILE = os.getenv("ANPR_DISPATCH_FILE", r"E:\TITAN\production\PCA\SCRIPTS\cockpit_dispatch.py")
HIK_IMAGE_DIR = os.getenv("HIK_IMAGE_DIR", "E:/TITAN/production/hik_images")  # = anpr.HIK_IMAGE_DIR
LOG_DIR = os.path.join(ROOT, "logs")
LOCK_PATH = os.path.join(LOG_DIR, "anpr_lpv.lock")

# Codes LPV (interface fflprapp, main.js : array_direction / array_granted)
LPV_DIRECTION = {"2": "forward", "3": "reverse"}  # 2 = IN, 3 = OUT, 0/1 = inconnu
LPV_GRANTED = {1, 2, 4, 101, 102, 201, 202}


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "anpr_lpv-%s.log" % datetime.now().strftime("%Y%m%d"))
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


def _load_dispatch():
    """cockpit_dispatch du recepteur (hors depot), charge sans toucher sys.path."""
    try:
        spec = importlib.util.spec_from_file_location("cockpit_dispatch", DISPATCH_FILE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        log.warning("cockpit_dispatch indisponible (%s) : pas d'alerte liste de surveillance", e)
        return None


def _lpv_devices(db):
    for d in db["cockpit_cameras"].find({"enabled": {"$ne": False},
                                          "channels.roles": {"$elemMatch": {"type": "anpr", "source": SOURCE}}}):
        if cameras.is_piste_device(d):
            log.warning("%s est une camera de piste : ignoree", d.get("ip"))
            continue
        if d.get("brand") != "axis":
            log.warning("%s n'est pas une Axis : source consultation ignoree", d.get("ip"))
            continue
        yield d


def _fetch_new(base, auth, last_ts):
    """Lectures LPV a inserer. last_ts (us) None = premiere consultation."""
    if last_ts is None:
        r = requests.get(base + "events.cgi", params={"limit": FIRST_RUN_LIMIT}, auth=auth, timeout=HTTP_TIMEOUT_S)
        r.raise_for_status()
        return ET.fromstring(r.content).findall("event")
    out = []
    for page in range(MAX_PAGES):
        r = requests.get(base + "search.cgi", auth=auth, timeout=HTTP_TIMEOUT_S, params={
            "timestampfrom": last_ts + 1, "timestampto": 9999999999999999, "text": "",
            "limit": PAGE_SIZE, "offset": page * PAGE_SIZE})
        r.raise_for_status()
        batch = ET.fromstring(r.content).findall("event")
        out.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
    return out


def _save_image(base, auth, rel_url, path_slug, kind, event_dt):
    """Telecharge une image LPV (tools.cgi?action=getImage) dans HIK_IMAGE_DIR."""
    if not rel_url:
        return None
    try:
        r = requests.get(base + rel_url, auth=auth, timeout=HTTP_TIMEOUT_S)
        if r.status_code != 200 or not r.headers.get("Content-Type", "").startswith("image/"):
            return None
        rel = "%s/%s/%s_%s_%s.jpg" % (event_dt.strftime("%Y/%m/%d"), path_slug, kind, event_dt.strftime("%H%M%S"),
                                      "".join(random.choices(string.ascii_lowercase + string.digits, k=8)))
        full = os.path.join(HIK_IMAGE_DIR, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(r.content)
        return rel
    except Exception as e:
        log.info("image %s non recuperee (%s)", kind, e)
        return None


def _int(text, default):
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


def poll_device(db, d, dispatch, dry_run=False):
    """Une consultation de la camera. -> nombre de lectures inserees."""
    user, password = cameras.credentials_for(d)
    auth = HTTPDigestAuth(user, password)
    base = "%s://%s:%s/local/fflprapp/" % (d.get("protocol", "http"), d["ip"], d.get("port", 80))
    path = cameras.anpr_lpv_path(d)
    col = db["hik_anpr"]
    last = col.find_one({"camera_path": path, "lpv_ts": {"$exists": True}}, {"lpv_ts": 1}, sort=[("lpv_ts", -1)])
    events = _fetch_new(base, auth, last["lpv_ts"] if last else None)
    if dry_run:
        log.info("%s : %d lecture(s) a inserer (a blanc, rien d'ecrit)", d["ip"], len(events))
        return 0
    inserted = 0
    for e in sorted(events, key=lambda x: _int(x.findtext("TS"), 0)):
        car_id = _int(e.findtext("CAR_ID"), 0)
        ts = _int(e.findtext("TS"), 0)
        uuid = "axislpv-%s-%d" % (d["ip"], car_id)
        if not car_id or not ts or col.find_one({"uuid": uuid}, {"_id": 1}):
            continue
        event_dt = datetime.fromtimestamp(ts / 1e6, tz=timezone.utc).replace(tzinfo=None)
        plate = (e.findtext("LPR_UTF8") or e.findtext("LPR") or "").strip().upper().replace(" ", "")
        try:
            confidence = int(round(float(e.findtext("THRESHOLD") or 0) * 100))
        except ValueError:
            confidence = 0
        action = _int(e.findtext("ACTION"), -1)
        slug = path.strip("/")
        plate_img = _save_image(base, auth, e.findtext("LP_BMP"), slug, "plate", event_dt)
        vehicle_img = _save_image(base, auth, e.findtext("ROI_BMP"), slug, "vehicle", event_dt)
        doc = {
            "uuid": uuid, "lpv_car_id": car_id, "lpv_ts": ts, "lpv_action": action, "source": SOURCE,
            "camera_path": path, "channel_id": 1, "ip_address": d["ip"],
            "event_dt": event_dt, "received_at": datetime.now(timezone.utc).replace(tzinfo=None),
            "license_plate": plate or "UNKNOWN", "original_plate": plate,
            "confidence": confidence, "country": e.findtext("COUNTRY") or "",
            "direction": LPV_DIRECTION.get((e.findtext("DIRECTION") or "").strip(), "unknown"),
            "vehicle_list_name": "allowList" if action in LPV_GRANTED else "otherList",
            "vehicle_type": "vehicle",
        }
        if plate_img:
            doc["plate_image_path"] = plate_img
        if vehicle_img:
            doc["vehicle_image_path"] = vehicle_img
        res = col.update_one({"uuid": uuid}, {"$setOnInsert": doc}, upsert=True)
        if not res.upserted_id:
            continue
        inserted += 1
        if dispatch and plate:
            try:
                dispatch.record_event_stream(db, "anpr_watchlist", path, event_dt,
                                             snapshot_path=vehicle_img or plate_img,
                                             payload_summary="Plaque %s - vehicle" % plate)
                dispatch.dispatch_anpr_watchlist(db, plate, path, event_dt=event_dt,
                                                 vehicle_image_path=vehicle_img, plate_image_path=plate_img,
                                                 confidence=confidence)
            except Exception as ex:
                log.warning("%s : dispatch en erreur (%s)", d["ip"], ex)
    return inserted


def main(argv=None):
    ap = argparse.ArgumentParser(description="LAPI par consultation (Axis License Plate Verifier)")
    ap.add_argument("--dry-run", action="store_true", help="interroge les cameras, n'ecrit rien")
    args = ap.parse_args(argv)
    _setup_logging()
    lock = _acquire_lock()
    if lock is None:
        log.warning("Consultation precedente encore en cours : sortie.")
        return 0
    if not camera_secrets.available():
        log.error("COCKPIT_CAMERA_KEY absente pour ce compte : la tache doit tourner sous le compte de Cockpit.")
        return 2
    db = _get_db()
    devices = list(_lpv_devices(db))
    if not devices:
        log.info("Aucune camera LAPI en consultation (usage LAPI, source axis_lpv) : sortie.")
        return 0
    dispatch = None if args.dry_run else _load_dispatch()
    rc = 0
    for d in devices:
        try:
            n = poll_device(db, d, dispatch, dry_run=args.dry_run)
            log.info("%s (%s) : %d nouvelle(s) lecture(s)", d["ip"], d.get("name"), n)
        except Exception as e:
            rc = 1
            log.warning("%s (%s) : consultation impossible (%s)", d.get("ip"), d.get("name"), e)
    return rc


if __name__ == "__main__":
    sys.exit(main())
