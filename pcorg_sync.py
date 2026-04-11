#!/usr/bin/env python3
"""
pcorg_sync.py - Wrapper cron pour sync PC Organisation SQL Server -> MongoDB.

Lance par tache planifiee Windows toutes les 5 minutes.
Peut aussi etre declenche manuellement via l'API /pcorg/force-sync.

Usage:
    python pcorg_sync.py            # sync incremental
    python pcorg_sync.py --full     # resync complet
"""

import os
import sys
import json
import logging
import subprocess
from datetime import datetime, timedelta

from pymongo import MongoClient

TASK_NAME = "Sync PC Organisation"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "pcorg_sync.log")
LOG_RETENTION_DAYS = 3
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_TITAN_ENV = os.getenv("TITAN_ENV", "dev").strip().lower()
DB_NAME = "titan" if _TITAN_ENV in {"prod", "production"} else "titan_dev"
CONTROL_ID = "pcorg_sync_control"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PcorgSync] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("pcorg_sync")
SYNC_SCRIPT = os.path.join(SCRIPT_DIR, "uploads", "pcorg", "sync_pcorg_sql.py")
PYTHON_EXE = sys.executable


# ---------------------------------------------------------------------------
# Cron status (meme pattern que live_controle.py)
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
    """Supprime les lignes du log de plus de LOG_RETENTION_DAYS jours."""
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
# Main
# ---------------------------------------------------------------------------

def _get_control(db):
    """Lit le document de controle pcorg_sync."""
    return db["pcorg_sync_config"].find_one({"_id": CONTROL_ID}) or {}


def _set_control(db, update):
    """Met a jour le document de controle pcorg_sync."""
    db["pcorg_sync_config"].update_one(
        {"_id": CONTROL_ID},
        {"$set": update},
        upsert=True,
    )


def main():
    _purge_old_logs()
    full_mode = "--full" in sys.argv
    force_mode = "--force" in sys.argv

    # Connexion MongoDB pour lire le flag d'activation
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]

    control = _get_control(db)

    # Verifier si la sync est activee (sauf si --force)
    if not force_mode and not control.get("actif", False):
        log.info("Sync PC Organisation desactivee. Sortie.")
        client.close()
        return

    # Verifier les credentials SQL
    if not os.getenv("MSSQL_USER") or not os.getenv("MSSQL_PASSWORD"):
        log.error("MSSQL_USER ou MSSQL_PASSWORD non defini")
        _update_cron_status("down", "Credentials SQL manquants")
        _set_control(db, {"last_run": datetime.now(), "last_error": "Credentials SQL manquants", "running": False})
        client.close()
        return

    if not os.path.exists(SYNC_SCRIPT):
        log.error("Script sync introuvable: %s", SYNC_SCRIPT)
        _update_cron_status("down", "Script introuvable")
        client.close()
        return

    # Lancer le script de sync comme sous-processus
    cmd = [PYTHON_EXE, "-X", "utf8", SYNC_SCRIPT]
    if full_mode:
        cmd.append("--full")

    log.info("Lancement sync %s", "COMPLET" if full_mode else "incremental")
    _set_control(db, {"running": True, "last_run": datetime.now(), "last_error": None})

    try:
        result = subprocess.run(
            cmd,
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min max
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if stdout:
            for line in stdout.split("\n")[-10:]:
                log.info("  %s", line)

        if result.returncode != 0:
            error_msg = stderr[-200:] if stderr else f"exit code {result.returncode}"
            log.error("Sync echouee: %s", error_msg)
            _update_cron_status("down", error_msg[:200])
            _set_control(db, {"running": False, "last_error": error_msg[:200]})
        else:
            summary = ""
            for line in stdout.split("\n"):
                if "upsert" in line.lower() or "termin" in line.lower():
                    summary = line.strip()
            _update_cron_status("ok", summary[:200] if summary else "")
            _set_control(db, {"running": False, "last_error": None, "last_success": datetime.now(), "last_summary": summary[:200]})
            log.info("Sync terminee avec succes")

    except subprocess.TimeoutExpired:
        log.error("Sync timeout (5 min)")
        _update_cron_status("down", "Timeout apres 5 min")
        _set_control(db, {"running": False, "last_error": "Timeout apres 5 min"})
    except Exception as e:
        log.error("Erreur: %s", e)
        _update_cron_status("down", str(e)[:200])
        _set_control(db, {"running": False, "last_error": str(e)[:200]})
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Erreur: {e}")
    sys.exit(0)
