#!/usr/bin/env python3
"""
vision_sync.py - Collecteur Vision ACO (Firestore) -> MongoDB cockpit.

Synchronise les collections Firestore du projet Vision (immatriculations,
blacklist, config) vers la base MongoDB titan pour croisement LAPI.

Lance par tache planifiee toutes les 5 minutes.

Usage:
    python vision_sync.py            # sync incremental (evenement actif)
    python vision_sync.py --full     # sync tous les evenements
"""

import os
import sys
import json
import logging
import re
from datetime import datetime, timedelta

from pymongo import MongoClient

TASK_NAME = "Sync Vision ACO"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "vision_sync.log")
LOG_RETENTION_DAYS = 3
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_dev_mode = os.getenv("TITAN_ENV", "dev") != "prod"
DB_NAME = "titan_dev" if _dev_mode else "titan"
FIREBASE_CREDENTIALS = os.getenv(
    "FIREBASE_CREDENTIALS",
    os.path.join(SCRIPT_DIR, "firebase-service-account.json"),
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [VisionSync] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("vision_sync")


# ---------------------------------------------------------------------------
# Cron status (meme pattern que pcorg_sync.py)
# ---------------------------------------------------------------------------

def _status_path():
    path = os.getenv("CRON_STATUS_FILE", "").strip()
    if path:
        return path
    return os.path.join(SCRIPT_DIR, "cron_status.json")


def _update_cron_status(status, message=""):
    path = _status_path()
    tasks = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            tasks = data if isinstance(data, list) else data.get("tasks", [])
        except Exception:
            tasks = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    updated = False
    for task in tasks:
        if task.get("name") == TASK_NAME:
            task["status"] = status
            task["last_run"] = now
            if message:
                task["message"] = message
            else:
                task.pop("message", None)
            updated = True
            break
    if not updated:
        entry = {"name": TASK_NAME, "status": status, "last_run": now}
        if message:
            entry["message"] = message
        tasks.append(entry)
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(tasks, handle, indent=2)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Log rotation
# ---------------------------------------------------------------------------

def _purge_old_logs():
    if not os.path.exists(LOG_FILE):
        return
    cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
    kept = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    ts_str = line[:19]
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    if ts >= cutoff:
                        kept.append(line)
                except ValueError:
                    kept.append(line)
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(kept)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Firebase init
# ---------------------------------------------------------------------------

_fs_db = None


def _get_firestore():
    global _fs_db
    if _fs_db is not None:
        return _fs_db
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError:
        log.error("firebase-admin non installe. pip install firebase-admin")
        return None
    if not os.path.exists(FIREBASE_CREDENTIALS):
        log.error("Fichier credentials Firebase introuvable: %s", FIREBASE_CREDENTIALS)
        return None
    try:
        cred = credentials.Certificate(FIREBASE_CREDENTIALS)
        firebase_admin.initialize_app(cred)
        _fs_db = firestore.client()
        return _fs_db
    except Exception as exc:
        log.error("Erreur init Firebase: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def _normalize_plate(plate):
    """Normalise une plaque : uppercase, alphanum only."""
    return re.sub(r"[^A-Z0-9]", "", (plate or "").strip().upper())


def sync_config(fs_db, mongo_db):
    """Sync le document config/current de Vision."""
    col = mongo_db["vision_config"]
    try:
        doc = fs_db.collection("config").document("current").get()
        if doc.exists:
            data = doc.to_dict()
            col.update_one(
                {"_id": "current"},
                {"$set": {
                    "evenement": data.get("evenement", ""),
                    "annee": data.get("annee", 0),
                    "synced_at": datetime.utcnow(),
                }},
                upsert=True,
            )
            log.info("Config syncee: %s %s", data.get("evenement"), data.get("annee"))
            return data.get("evenement", ""), data.get("annee", 0)
        else:
            log.warning("Pas de document config/current dans Vision")
            return None, None
    except Exception as exc:
        log.error("Erreur sync config: %s", exc)
        return None, None


def sync_immatriculations(fs_db, mongo_db, evenement=None, annee=None, full=False):
    """Sync les immatriculations de Vision vers MongoDB."""
    col = mongo_db["vision_immatriculations"]

    # Index
    col.create_index("plaque_norm")
    col.create_index([("evenement", 1), ("annee", 1)])
    col.create_index("lieu")

    query = fs_db.collection("immatriculations")
    if not full and evenement and annee:
        query = query.where("evenement", "==", evenement).where("annee", "==", int(annee))

    count_upsert = 0
    count_skip = 0
    try:
        for doc in query.stream():
            data = doc.to_dict()
            plaque = data.get("plaque", "")
            plaque_norm = _normalize_plate(plaque)
            if not plaque_norm:
                continue

            record = {
                "plaque": plaque,
                "plaque_norm": plaque_norm,
                "lieu": data.get("lieu", ""),
                "commentaire": data.get("commentaire", ""),
                "billets": data.get("billets", []),
                "date": data.get("date", ""),
                "evenement": data.get("evenement", ""),
                "annee": data.get("annee", 0),
                "photo_vehicule": data.get("photoVehicule", ""),
                "photo_plaque": data.get("photoPlaque", ""),
                "couleur": data.get("couleur", ""),
                "marque": data.get("marque", ""),
                "modele": data.get("modele", ""),
                "firestore_doc_id": doc.id,
                "synced_at": datetime.utcnow(),
            }

            col.update_one(
                {"firestore_doc_id": doc.id},
                {"$set": record},
                upsert=True,
            )
            count_upsert += 1

    except Exception as exc:
        log.error("Erreur sync immatriculations: %s", exc)
        return 0

    log.info("Immatriculations: %d upsert", count_upsert)
    return count_upsert


def sync_blacklist(fs_db, mongo_db):
    """Sync la blacklist Vision vers MongoDB."""
    col = mongo_db["vision_blacklist"]
    col.create_index("plaque_norm", unique=True)

    count = 0
    seen_norms = set()
    try:
        for doc in fs_db.collection("blacklist").stream():
            data = doc.to_dict()
            plaque = data.get("plaque", "")
            plaque_norm = _normalize_plate(plaque)
            if not plaque_norm:
                continue
            seen_norms.add(plaque_norm)

            col.update_one(
                {"plaque_norm": plaque_norm},
                {"$set": {
                    "plaque": plaque,
                    "plaque_norm": plaque_norm,
                    "raison": data.get("raison", ""),
                    "date_ajout": data.get("dateAjout", ""),
                    "synced_at": datetime.utcnow(),
                }},
                upsert=True,
            )
            count += 1

        # Supprimer les entrees qui ne sont plus dans Firestore
        if seen_norms:
            result = col.delete_many({"plaque_norm": {"$nin": list(seen_norms)}})
            if result.deleted_count:
                log.info("Blacklist: %d entrees supprimees (plus dans Vision)", result.deleted_count)

    except Exception as exc:
        log.error("Erreur sync blacklist: %s", exc)
        return 0

    log.info("Blacklist: %d entrees syncees", count)
    return count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    _purge_old_logs()
    full_mode = "--full" in sys.argv

    log.info("=== Demarrage sync Vision %s ===", "COMPLET" if full_mode else "incremental")

    # Init Firestore
    fs_db = _get_firestore()
    if fs_db is None:
        _update_cron_status("down", "Firebase non disponible")
        return

    # Init MongoDB
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.server_info()
        mongo_db = client[DB_NAME]
    except Exception as exc:
        log.error("Connexion MongoDB echouee: %s", exc)
        _update_cron_status("down", "MongoDB non disponible")
        return

    try:
        # 1. Sync config
        evenement, annee = sync_config(fs_db, mongo_db)

        # 2. Sync immatriculations
        # Auto-full si premiere execution (collection vide)
        if not full_mode and mongo_db["vision_immatriculations"].estimated_document_count() == 0:
            log.info("Collection vision_immatriculations vide, bascule en mode COMPLET")
            full_mode = True

        if full_mode:
            n_imm = sync_immatriculations(fs_db, mongo_db, full=True)
        elif evenement and annee:
            n_imm = sync_immatriculations(fs_db, mongo_db, evenement, annee)
        else:
            log.warning("Pas d'evenement actif dans Vision, sync immatriculations ignoree")
            n_imm = 0

        # 3. Sync blacklist
        n_bl = sync_blacklist(fs_db, mongo_db)

        summary = f"{n_imm} immat, {n_bl} blacklist"
        log.info("Sync terminee: %s", summary)
        _update_cron_status("ok", summary)

    except Exception as exc:
        log.error("Erreur sync: %s", exc)
        _update_cron_status("down", str(exc)[:200])
    finally:
        client.close()


if __name__ == "__main__":
    main()
