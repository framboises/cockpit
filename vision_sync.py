#!/usr/bin/env python3
"""
vision_sync.py - Collecteur Vision ACO (Firestore) -> MongoDB cockpit.

Synchronise les collections Firestore du projet Vision (immatriculations,
blacklist, config) vers la base MongoDB titan pour croisement LAPI.

Lance par tache planifiee toutes les 5 minutes.

Chaque lecture Firestore est facturee : relire tout l'evenement a chaque
passage (10 700 docs x 288 passages/jour) coutait ~1,60 EUR/jour. D'ou :

- immatriculations : incremental sur le champ `date` (ISO UTC, triable en
  chaine) avec un recouvrement de OVERLAP_MIN, puis controle par count()
  (~1 lecture / 1000 docs). Un count Firestore superieur au nombre en base
  (tablette hors ligne qui remonte des scans dates dans le passe) declenche
  une resync complete de l'evenement. Resync complete aussi toutes les
  FULL_RESYNC_HOURS (filet pour les docs modifies sans changement de date)
  et a chaque changement d'evenement.
- blacklist : relue au plus toutes les BLACKLIST_INTERVAL_MIN.
- sortie : l'app Firebase est fermee et le process termine par os._exit,
  sinon les threads gRPC le gardaient vivant et la tache planifiee
  (IgnoreNew) restait bloquee jusqu'a 72 h.

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
OVERLAP_MIN = 15
FULL_RESYNC_HOURS = 24
BLACKLIST_INTERVAL_MIN = 60
STATE_ID = "sync_state"

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


def _close_firestore():
    global _fs_db
    if _fs_db is None:
        return
    try:
        import firebase_admin
        firebase_admin.delete_app(firebase_admin.get_app())
    except Exception as exc:
        log.warning("Fermeture Firebase: %s", exc)
    _fs_db = None


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


def _event_query(fs_db, evenement, annee):
    return (fs_db.collection("immatriculations")
            .where("evenement", "==", evenement)
            .where("annee", "==", int(annee)))


def _firestore_count(query):
    """count() facture ~1 lecture par tranche de 1000 docs. None si echec."""
    try:
        return int(query.count().get()[0][0].value)
    except Exception as exc:
        log.warning("count() Firestore indisponible: %s", exc)
        return None


def _cursor_minus_overlap(cursor):
    """'2026-05-10T09:03:41.924Z' -> meme format, OVERLAP_MIN plus tot."""
    try:
        dt = datetime.strptime(cursor[:19], "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None
    return (dt - timedelta(minutes=OVERLAP_MIN)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def sync_immatriculations(fs_db, mongo_db, evenement=None, annee=None, full=False,
                          since=None):
    """Sync les immatriculations de Vision vers MongoDB.

    full=True : toute la collection. since : docs dont `date` > since (tous
    evenements, index mono-champ automatique, pas d'index composite requis).
    Sinon : tout l'evenement. Retourne (nb upsert ou None si erreur, max date).
    """
    col = mongo_db["vision_immatriculations"]

    # Index
    col.create_index("plaque_norm")
    col.create_index([("evenement", 1), ("annee", 1)])
    col.create_index("lieu")
    col.create_index("device_id")

    query = fs_db.collection("immatriculations")
    if since:
        query = query.where("date", ">", since)
    elif not full and evenement and annee:
        query = _event_query(fs_db, evenement, annee)

    count_upsert = 0
    max_date = None
    try:
        for doc in query.stream():
            data = doc.to_dict()
            date_str = data.get("date", "")
            if isinstance(date_str, str) and date_str and (max_date is None or date_str > max_date):
                max_date = date_str
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
                "device_id": data.get("device_id", ""),
                "device_name": data.get("device_name", ""),
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
        return None, max_date

    mode = "depuis %s" % since if since else ("complet" if full else "evenement")
    log.info("Immatriculations (%s): %d upsert", mode, count_upsert)
    return count_upsert, max_date


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
        state_col = mongo_db["vision_config"]
        state = state_col.find_one({"_id": STATE_ID}) or {}
        now = datetime.utcnow()
        state_set = {}

        # 1. Sync config
        evenement, annee = sync_config(fs_db, mongo_db)
        imm_col = mongo_db["vision_immatriculations"]

        # 2. Sync immatriculations
        # Auto-full si premiere execution (collection vide)
        if not full_mode and imm_col.estimated_document_count() == 0:
            log.info("Collection vision_immatriculations vide, bascule en mode COMPLET")
            full_mode = True

        cursor = state.get("date_cursor")
        max_date = None
        n_imm = 0
        if full_mode:
            n_imm, max_date = sync_immatriculations(fs_db, mongo_db, full=True)
        elif evenement and annee:
            event_key = f"{evenement}|{int(annee)}"
            last_full = state.get("last_full_at")
            full_due = (
                state.get("last_full_key") != event_key
                or last_full is None
                or now - last_full > timedelta(hours=FULL_RESYNC_HOURS)
            )
            if not cursor:
                last = imm_col.find_one({"date": {"$type": "string"}},
                                        {"date": 1}, sort=[("date", -1)])
                cursor = last.get("date") if last else None
            since = _cursor_minus_overlap(cursor) if cursor else None

            if not full_due and since:
                n_imm, max_date = sync_immatriculations(fs_db, mongo_db, since=since)
                if n_imm is not None:
                    fs_n = _firestore_count(_event_query(fs_db, evenement, annee))
                    mongo_n = imm_col.count_documents(
                        {"evenement": evenement, "annee": int(annee)})
                    if fs_n is not None and fs_n > mongo_n:
                        log.info("count Firestore %d > base %d : resync complete de %s",
                                 fs_n, mongo_n, event_key)
                        full_due = True
            else:
                full_due = True

            if full_due:
                n_full, full_max = sync_immatriculations(fs_db, mongo_db, evenement, annee)
                if n_full is not None:
                    state_set["last_full_at"] = now
                    state_set["last_full_key"] = event_key
                    n_imm = (n_imm or 0) + n_full
                    if full_max and (max_date is None or full_max > max_date):
                        max_date = full_max
                else:
                    n_imm = None
        else:
            log.warning("Pas d'evenement actif dans Vision, sync immatriculations ignoree")

        # Le curseur n'avance que sur un passage sans erreur : le flux n'est pas
        # trie par date, un passage interrompu laisserait des trous.
        if n_imm is not None:
            new_cursor = max(c for c in (cursor, max_date) if c) if (cursor or max_date) else None
            if new_cursor:
                state_set["date_cursor"] = new_cursor

        # 3. Sync blacklist (au plus toutes les BLACKLIST_INTERVAL_MIN)
        last_bl = state.get("last_blacklist_at")
        if full_mode or last_bl is None or now - last_bl >= timedelta(minutes=BLACKLIST_INTERVAL_MIN):
            n_bl = sync_blacklist(fs_db, mongo_db)
            state_set["last_blacklist_at"] = now
        else:
            n_bl = "skip"

        if state_set:
            state_col.update_one({"_id": STATE_ID}, {"$set": state_set}, upsert=True)

        summary = f"{n_imm if n_imm is not None else 'erreur'} immat, {n_bl} blacklist"
        log.info("Sync terminee: %s", summary)
        _update_cron_status("ok" if n_imm is not None else "down", summary)

    except Exception as exc:
        log.error("Erreur sync: %s", exc)
        _update_cron_status("down", str(exc)[:200])
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        # Sans ca, les threads gRPC de firebase_admin gardent le process en vie
        # apres "Sync terminee" et la tache planifiee (IgnoreNew) reste bloquee.
        _close_firestore()
        logging.shutdown()
        sys.stdout.flush()
        os._exit(0)
