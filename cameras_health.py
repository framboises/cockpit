"""Etat des cameras : releve reseau historise (alertes "camera hors ligne").

Lance toutes les 2 minutes par la tache planifiee "Cockpit - Etat cameras"
(scripts/cameras_health.bat, installee par scripts/install_cameras_health_task.ps1).
JAMAIS dans le process Flask (pas de tache cachee dans Cockpit).

- Connexion TCP au port web de chaque appareil ACTIF, sans identifiant ni
  requete (comme l'etat reseau de la page Cameras). Cameras de PISTE
  exclues : rien ne leur est envoye, pas meme une connexion.
- Hysteresis : hors ligne apres KO_CONFIRM echecs consecutifs
  (KO_CONFIRM_CELL pour une camera 4G, dont le modem s'endort), de nouveau
  en ligne au premier succes. Une coupure d'une seule releve ne compte pas.
- cameras_health (un document par appareil, _id = camera_id) : etat confirme
  `ok`, `since` (debut de cet etat), `fails`, `last_ok_at`, `last_check_at`,
  `lapi_last_at` (derniere lecture de plaque si un canal a l'usage LAPI).
- cameras_health_log : chaque changement d'etat confirme (TTL LOG_TTL_DAYS),
  sert a la disponibilite sur 24 h / 7 j de la fiche.
- Les alertes sont creees par la centrale (alert_engine.py, type
  camera_offline de camera_alerts.py), qui lit cameras_health.

Aucun identifiant : la tache peut tourner sous n'importe quel compte ayant
TITAN_ENV / MONGO_URI.

    python cameras_health.py              # une releve (TITAN_ENV=prod)
    python cameras_health.py --dry-run    # teste, affiche, n'ecrit rien
"""
import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from pymongo import MongoClient  # noqa: E402

import cameras  # noqa: E402

log = logging.getLogger("cameras_health")

KO_CONFIRM = 2          # releves en echec avant "hors ligne" (2 x 2 min)
KO_CONFIRM_CELL = 3     # cameras 4G : modem endormi, reveil lent
CELL_PROBE_TIMEOUT_S = 8.0
PROBE_WORKERS = 48
LOG_TTL_DAYS = 400
LOG_DIR = os.path.join(ROOT, "logs")
LOCK_PATH = os.path.join(LOG_DIR, "cameras_health.lock")


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "cameras_health-%s.log" % datetime.now().strftime("%Y%m%d"))
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
    except ImportError:
        pass  # hors Windows (tests, poste de dev)
    except OSError:
        os.close(fd)
        return None
    return fd


def _get_db():
    env = os.getenv("TITAN_ENV", "dev").strip().lower()
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=10000)
    return client["titan" if env in ("prod", "production") else "titan_dev"]


def _utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def monitored(db):
    """Appareils releves : actifs, jamais de piste."""
    for d in db["cockpit_cameras"].find({"enabled": {"$ne": False}},
                                        {"name": 1, "ip": 1, "port": 1, "site": 1, "group_path": 1, "model": 1,
                                         "camera_path": 1, "channels": 1}):
        if d.get("ip") and not cameras.is_piste_device(d):
            yield d


def probe(d):
    """True si le port web accepte la connexion. 4G : delai plus long et une
    seconde tentative (la premiere reveille le modem)."""
    ip, port = d["ip"], d.get("port", 80)
    if not cameras.is_cellular(d):
        return cameras._probe(ip, port)
    import socket
    for _ in range(2):
        try:
            with socket.create_connection((ip, port), timeout=CELL_PROBE_TIMEOUT_S):
                return True
        except OSError:
            continue
    return False


def next_state(prev, raw_ok, now, cellular=False):
    """Etat confirme apres une releve. prev : document cameras_health (ou {}).
    -> (champs a ecrire, changement) ; changement = True / False (nouvel etat
    confirme) ou None. Premiere releve : l'etat mesure est pris tel quel, sans
    changement (activer la releve ne doit pas alerter)."""
    need = KO_CONFIRM_CELL if cellular else KO_CONFIRM
    fails = 0 if raw_ok else int(prev.get("fails") or 0) + 1
    out = {"raw_ok": raw_ok, "fails": fails, "last_check_at": now, "cellular": cellular}
    if raw_ok:
        out["last_ok_at"] = now
    if "ok" not in prev:
        out.update({"ok": raw_ok, "since": now, "first_seen": now})
        return out, None
    was = bool(prev["ok"])
    if was and not raw_ok and fails >= need:
        # Hors ligne depuis le premier echec de la serie (ou la derniere reussite)
        out.update({"ok": False, "since": _utc(prev.get("last_ok_at")) or now})
        return out, False
    if not was and raw_ok:
        out.update({"ok": True, "since": now})
        return out, True
    return out, None


def lapi_last(db, d):
    """Derniere lecture de plaque d'un appareil en usage LAPI (index
    camera_path + event_dt de hik_anpr), ou None."""
    role = next((r for ch in d.get("channels") or [] for r in ch.get("roles") or [] if r.get("type") == "anpr"), None)
    if not role:
        return None
    path = cameras.anpr_lpv_path(d) if role.get("source") == "axis_lpv" else d.get("camera_path")
    if not path:
        return None
    last = db["hik_anpr"].find_one({"camera_path": path}, {"event_dt": 1}, sort=[("event_dt", -1)])
    return _utc((last or {}).get("event_dt"))


def run(db, dry_run=False, now=None, prober=probe):
    now = now or datetime.now(timezone.utc)
    devices = list(monitored(db))
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        results = list(pool.map(prober, devices))
    prev = {h["_id"]: h for h in db["cameras_health"].find()}
    ops, logs, changes = [], [], []
    for d, raw_ok in zip(devices, results):
        p = prev.get(d["_id"]) or {}
        fields, change = next_state(p, raw_ok, now, cameras.is_cellular(d))
        fields.update({"name": d.get("name", ""), "ip": d["ip"], "site": d.get("site", "")})
        lapi = lapi_last(db, d)
        if lapi:
            fields["lapi_last_at"] = lapi
        ops.append((d["_id"], fields))
        if change is not None:
            changes.append((d, change, fields["since"]))
            logs.append({"camera_id": d["_id"], "name": d.get("name", ""), "ip": d["ip"], "ok": change,
                         "at": fields["since"]})
    down = sum(1 for d, ok in zip(devices, results) if not ok)
    for d, ok, since in changes:
        log.info("%s (%s) : %s depuis %s", d.get("name"), d["ip"], "DE NOUVEAU EN LIGNE" if ok else "HORS LIGNE",
                 since.strftime("%H:%M:%S"))
    if not dry_run:
        for cid, fields in ops:  # ~330 appareils toutes les 2 min : pas besoin de bulk
            db["cameras_health"].update_one({"_id": cid}, {"$set": fields}, upsert=True)
        for entry in logs:
            db["cameras_health_log"].insert_one(entry)
        # Appareils plus releves (supprimes, desactives, reclasses piste)
        gone = [k for k in prev if k not in {d["_id"] for d in devices}]
        if gone:
            db["cameras_health"].delete_many({"_id": {"$in": gone}})
        db["cameras_health_log"].create_index([("camera_id", 1), ("at", -1)])
        db["cameras_health_log"].create_index("at", expireAfterSeconds=LOG_TTL_DAYS * 86400)
    log.info("Releve : %d appareil(s), %d sans reponse, %d changement(s) d'etat%s",
             len(devices), down, len(changes), " (a blanc)" if dry_run else "")
    return {"devices": len(devices), "down": down, "changes": len(changes)}


def availability(db, camera_id, hours, now=None):
    """Part du temps en ligne (0-1) sur les `hours` dernieres heures, d'apres
    cameras_health_log ; None si l'appareil n'est pas releve. La fenetre
    commence au plus tot a la premiere releve de l'appareil (first_seen)."""
    now = now or datetime.now(timezone.utc)
    cur = db["cameras_health"].find_one({"_id": camera_id}, {"ok": 1, "first_seen": 1, "since": 1})
    if not cur or "ok" not in cur:
        return None
    start = max(now - timedelta(hours=hours), _utc(cur.get("first_seen") or cur.get("since")) or now)
    if start >= now:
        return None
    before = db["cameras_health_log"].find_one({"camera_id": camera_id, "at": {"$lte": start}}, sort=[("at", -1)])
    events = list(db["cameras_health_log"].find({"camera_id": camera_id, "at": {"$gt": start}}).sort("at", 1))
    if before:
        state = bool(before["ok"])
    elif events:
        state = not bool(events[0]["ok"])
    else:
        state = bool(cur["ok"])
    up, t = 0.0, start
    for e in events:
        at = min(_utc(e["at"]), now)
        if state:
            up += (at - t).total_seconds()
        t, state = at, bool(e["ok"])
    if state:
        up += (now - t).total_seconds()
    return round(up / (now - start).total_seconds(), 4)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Releve de l'etat reseau des cameras")
    ap.add_argument("--dry-run", action="store_true", help="teste les cameras, n'ecrit rien")
    args = ap.parse_args(argv)
    _setup_logging()
    lock = _acquire_lock()
    if lock is None:
        log.warning("Releve precedente encore en cours : sortie.")
        return 0
    try:
        run(_get_db(), dry_run=args.dry_run)
    except Exception as e:
        log.error("Releve impossible : %s", e, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
