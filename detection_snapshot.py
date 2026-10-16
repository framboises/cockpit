"""Capture de secours des intrusions arrivees SANS photo.

Certaines cameras (ex. 131 Porte Est) envoient l'intrusion sans image
(`pictures: 0` : envoi de la photo non coche dans leurs reglages, qu'on ne
touche pas). Cockpit prend alors lui-meme une capture du canal (GET ISAPI
`/Streaming/channels/<n>01/picture`, lecture seule), quelques secondes apres
l'alerte, et la range dans l'episode comme une photo de la camera, marquee
`snapshot: true` (la cible a pu bouger depuis : le cadre de la camera est
celui de la detection).

Tourne DANS Cockpit (thread de fond) : seul son compte lit les mots de passe
chiffres des cameras (COCKPIT_CAMERA_KEY). Une capture par episode, reservee
dans Mongo (`snap`) : deux instances ne capturent pas deux fois. Jamais de
camera de piste (cameras.make_hik_camera refuse).
"""
import io
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

IMAGE_ROOT = "E:/TITAN/production/hik_images"
POLL_S = 2
MAX_AGE_S = 120          # au-dela, la capture ne montrerait plus la scene
PHOTO_MAX_W, PHOTO_QUALITY, THUMB_SIZE = 1600, 55, (480, 360)  # comme le recepteur

_started = False
_lock = threading.Lock()


def _safe_dir(camera_path):
    return (camera_path or "").strip("/").replace("/", "_") or "root"


def _first_rect(db, ep):
    """Cadre de la cible donne par la camera (meme sans photo)."""
    ev = db["hik_intrusion_events"].find_one({"episode_id": ep["_id"]}, {"regions": 1}, sort=[("event_dt", 1)])
    for r in (ev or {}).get("regions") or []:
        if r.get("rect"):
            return r["rect"]
    return None


def _store(jpeg, ep, rect):
    """Meme rangement que le recepteur (store_photo) : dossier du jour de
    l'episode, photo recompressee + vignette centree sur la cible."""
    from PIL import Image
    start = ep.get("started_at") or datetime.utcnow()
    day = start.strftime("%Y/%m/%d")
    sub = _safe_dir(ep.get("camera_path"))
    folder = os.path.join(IMAGE_ROOT, "intrusions", day, sub)
    os.makedirs(os.path.join(folder, "_t"), exist_ok=True)
    name = "ch%d_%s_snap_%s.jpg" % (ep.get("channel") or 1, start.strftime("%H%M%S"), os.urandom(3).hex())
    with Image.open(io.BytesIO(jpeg)) as im:
        im = im.convert("RGB")
        if rect:
            cx, cy = (rect["x"] + rect["w"] / 2) * im.width, (rect["y"] + rect["h"] / 2) * im.height
            half_w = max(rect["w"] * im.width * 1.5, im.width / 8, rect["h"] * im.height * 1.5 * 4 / 3)
            half_h = half_w * 3 / 4
            left = min(max(0, cx - half_w), max(0, im.width - 2 * half_w))
            top = min(max(0, cy - half_h), max(0, im.height - 2 * half_h))
            crop = im.crop((int(left), int(top), int(left + 2 * half_w), int(top + 2 * half_h)))
        else:
            crop = im.copy()
        crop.thumbnail(THUMB_SIZE)
        crop.save(os.path.join(folder, "_t", name), "JPEG", quality=80, optimize=True)
        if im.width > PHOTO_MAX_W:
            im = im.resize((PHOTO_MAX_W, round(im.height * PHOTO_MAX_W / im.width)), Image.LANCZOS)
        im.save(os.path.join(folder, name), "JPEG", quality=PHOTO_QUALITY, optimize=True)
    base = "intrusions/%s/%s/" % (day, sub)
    return base + name, base + "_t/" + name


def capture_episode(db, ep):
    """Capture du canal de l'episode et rangement. -> message (journal)."""
    import cameras
    doc = db["cockpit_cameras"].find_one({"_id": ep.get("camera_id")})
    if not doc:
        return "camera inconnue de Cockpit"
    if cameras.is_piste_device(doc):
        return "camera de piste : jamais de capture"
    if doc.get("brand") not in cameras.CONTROLLABLE_BRANDS or not doc.get("credential_profile_id"):
        return "camera non pilotable (marque ou profil d'identifiants)"
    cam = cameras.make_hik_camera(doc, ep.get("channel"))
    tmp = os.path.join(IMAGE_ROOT, "intrusions", "_snap_%s.jpg" % os.urandom(4).hex())
    try:
        cameras.call_camera(doc, lambda: cam.capture_image(tmp))
        with open(tmp, "rb") as f:
            jpeg = f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    now = datetime.now(timezone.utc)
    rect = _first_rect(db, ep)
    photo, thumb = _store(jpeg, ep, rect)
    p = {"photo": photo, "thumb": thumb, "at": ep.get("started_at"), "type": (ep.get("target_types") or ["other"])[0],
         "rect": rect, "target_id": "", "snapshot": True, "snap_delay_s": round((now - ep["_created"]).total_seconds(), 1)}
    db["hik_intrusions"].update_one({"_id": ep["_id"], "photos.0": {"$exists": False}}, {
        "$push": {"photos": p},
        "$set": {"cover": {"photo": photo, "thumb": thumb, "rect": rect}, "snap.state": "ok", "snap.photo": photo}})
    return "capture ok (%.1f s apres l'alerte)" % p["snap_delay_s"]


def scan(db):
    now = datetime.now(timezone.utc)
    q = {"created_at": {"$gte": now.replace(tzinfo=None) - timedelta(seconds=MAX_AGE_S)},
         "photos.0": {"$exists": False}, "snap": {"$exists": False}, "camera_id": {"$ne": None}}
    while True:
        ep = db["hik_intrusions"].find_one_and_update(q, {"$set": {"snap": {"state": "pending", "at": now}}},
                                                      sort=[("created_at", 1)])
        if not ep:
            return
        ep["_created"] = (ep.get("created_at") or now.replace(tzinfo=None)).replace(tzinfo=timezone.utc)
        try:
            msg = capture_episode(db, ep)
        except Exception as e:
            msg = "echec : %s" % (str(e)[:200] or e.__class__.__name__)
            db["hik_intrusions"].update_one({"_id": ep["_id"]}, {"$set": {"snap.state": "error", "snap.error": msg}})
        else:
            if not msg.startswith("capture ok"):
                db["hik_intrusions"].update_one({"_id": ep["_id"]}, {"$set": {"snap.state": "skipped", "snap.error": msg}})
        logger.info("Intrusion sans photo %s (%s canal %s) : %s", ep["_id"], ep.get("camera_name"), ep.get("channel"), msg)


def start_worker(db_getter):
    """Thread de fond (idempotent)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True

    def _loop():
        db = db_getter()
        db["hik_intrusions"].create_index([("created_at", -1)])
        while True:
            try:
                scan(db)
            except Exception as e:
                logger.warning("Capture des intrusions sans photo : %s", e)
            time.sleep(POLL_S)

    threading.Thread(target=_loop, name="intrusion-snapshots", daemon=True).start()
    logger.info("Capture des intrusions sans photo demarree")
