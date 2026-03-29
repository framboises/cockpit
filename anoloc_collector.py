#!/usr/bin/env python3
"""
anoloc_collector.py - Collecteur autonome de positions GPS Anoloc.

Lance par tache planifiee Windows toutes les minutes.
Fait 2 collectes espacees de 30 secondes puis s'arrete.

Prerequis:
  - Document anoloc_config {_id: "global", enabled: true, ...} dans MongoDB
  - Document anoloc_live_control {_id: "live-control", collecting: true} dans MongoDB

Usage:
    python anoloc_collector.py
"""

import os
import sys
import time
import logging
import signal
from datetime import datetime, timezone

import requests
from pymongo import MongoClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "titan"
ANOLOC_API_BASE_DEFAULT = "https://app.lemans.anoloc.io/api/v3"
USER_AGENT = "COCKPIT-TITAN/1.0"

COLLECT_INTERVAL = 30       # secondes entre les 2 collectes
DEVICES_CACHE_TTL = 300     # cache devices local 5 min
NB_CYCLES = 2               # nombre de collectes par execution

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Anoloc] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("anoloc_collector")

# Graceful shutdown
_stop = False

def _handle_signal(signum, frame):
    global _stop
    log.info("Signal %s recu, arret en cours...", signum)
    _stop = True

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------

_client = None
_db = None

def get_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(MONGO_URI)
        _db = _client[DB_NAME]
    return _db


def ensure_indexes(db):
    db["anoloc_positions"].create_index(
        [("device_id", 1), ("collected_at", -1)]
    )
    db["anoloc_positions"].create_index("collected_at")


# ---------------------------------------------------------------------------
# Live control (activation/desactivation depuis l'admin)
# ---------------------------------------------------------------------------

LIVE_CONTROL_ID = "live-control"

def is_collecting_enabled(db):
    """Verifie si la collecte est activee via le document live-control."""
    doc = db["anoloc_config"].find_one({"_id": LIVE_CONTROL_ID})
    if not doc:
        return False
    return bool(doc.get("collecting", False))


def set_collecting_status(db, running, error=None):
    """Met a jour le statut du collecteur dans live-control."""
    update = {
        "running": running,
        "last_run": datetime.now(timezone.utc),
    }
    if error:
        update["last_error"] = error
    elif running:
        update["last_error"] = None
    db["anoloc_config"].update_one(
        {"_id": LIVE_CONTROL_ID},
        {"$set": update},
        upsert=True,
    )


# ---------------------------------------------------------------------------
# Anoloc API
# ---------------------------------------------------------------------------

_token = None
_devices_cache = None
_devices_cache_ts = 0
api_base = ANOLOC_API_BASE_DEFAULT


def anoloc_login(login, password):
    """Authentification Anoloc, retourne le Bearer token."""
    resp = requests.post(
        f"{api_base}/login",
        json={"login": login, "password": password, "remember_me": True},
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", {}).get("token") or data.get("token")


def anoloc_get(endpoint, token):
    """GET generique vers l'API Anoloc."""
    resp = requests.get(
        f"{api_base}{endpoint}",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_devices(token):
    """Retourne les devices Anoloc avec cache local."""
    global _devices_cache, _devices_cache_ts
    now = time.time()
    if _devices_cache and (now - _devices_cache_ts) < DEVICES_CACHE_TTL:
        return _devices_cache

    result = anoloc_get("/devices", token)
    devices = result.get("data", [])
    _devices_cache = {d["id"]: d for d in devices}
    _devices_cache_ts = now
    return _devices_cache


def get_live(token):
    """Retourne les positions live de tous les devices sous forme de dict {device_id: frame}."""
    result = anoloc_get("/live", token)
    data = result.get("data") or result
    # L'API peut retourner un dict {id: frame} ou une liste [frame, ...]
    if isinstance(data, list):
        out = {}
        for frame in data:
            did = frame.get("device_id") or frame.get("id") or frame.get("imei")
            if did:
                out[str(did)] = frame
        return out
    return data

# ---------------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------------

def build_device_to_group_map(config):
    """Construit un mapping device_id -> beacon_group config."""
    mapping = {}
    for grp in config.get("beacon_groups", []):
        if not grp.get("enabled", True):
            continue
        for dev_id in grp.get("anoloc_device_ids", []):
            mapping[dev_id] = grp
    return mapping


def collect_once(token, device_group_map, devices_info, db):
    """Une iteration de collecte: GET /live, insert positions, upsert latest."""
    now = datetime.now(timezone.utc)
    live_data = get_live(token)

    inserted = 0
    for device_id, frame in live_data.items():
        if not isinstance(frame, dict):
            continue
        grp = device_group_map.get(device_id)
        if not grp:
            continue

        dev_info = devices_info.get(device_id, {})
        lat = frame.get("latitude")
        lng = frame.get("longitude")
        if lat is None or lng is None:
            continue

        sent_at_str = frame.get("sent_at")
        sent_at = None
        if sent_at_str:
            try:
                sent_at = datetime.fromisoformat(sent_at_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                sent_at = now

        power = frame.get("power_supply") or {}
        battery_pct = power.get("battery_percentage")

        doc = {
            "device_id": device_id,
            "beacon_group": grp["id"],
            "label": dev_info.get("label", device_id),
            "lat": lat,
            "lng": lng,
            "speed": frame.get("speed", 0),
            "heading": frame.get("heading", 0),
            "status": frame.get("status", "offline"),
            "battery_pct": battery_pct,
            "sent_at": sent_at or now,
            "collected_at": now,
        }

        # Historique complet
        db["anoloc_positions"].insert_one(doc.copy())

        # Derniere position (upsert)
        latest = dict(doc)
        latest["imei"] = dev_info.get("imei", "")
        latest["icon"] = grp.get("icon", "location_on")
        latest["color"] = grp.get("color", "#6366f1")
        latest["group_label"] = grp.get("label", "")

        db["anoloc_latest"].replace_one(
            {"_id": device_id},
            {**latest, "_id": device_id},
            upsert=True,
        )
        inserted += 1

    return inserted

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _token, api_base

    db = get_db()
    ensure_indexes(db)

    # --- Verifier le live-control ---
    if not is_collecting_enabled(db):
        log.info("Collecte desactivee (live-control.collecting = false)")
        sys.exit(0)

    # --- Lire la config ---
    config = db["anoloc_config"].find_one({"_id": "global"})
    if not config:
        log.warning("Pas de configuration Anoloc trouvee (anoloc_config.global)")
        set_collecting_status(db, False, error="Config absente")
        sys.exit(0)

    if not config.get("enabled", False):
        log.info("Collecteur Anoloc desactive dans la config globale")
        set_collecting_status(db, False, error="Config desactivee")
        sys.exit(0)

    # URL de base
    api_base = config.get("api_base", "").rstrip("/") or ANOLOC_API_BASE_DEFAULT

    login = config.get("login", "")
    password = config.get("password", "")
    if not login or not password:
        log.error("Credentials Anoloc manquants dans la config")
        set_collecting_status(db, False, error="Credentials manquants")
        sys.exit(1)

    # Construire le mapping device -> groupe
    device_group_map = build_device_to_group_map(config)
    if not device_group_map:
        log.warning("Aucun device configure dans les beacon_groups actifs")
        set_collecting_status(db, False, error="Aucun device configure")
        sys.exit(0)

    log.info(
        "Demarrage: %d devices dans %d groupes actifs",
        len(device_group_map),
        len([g for g in config.get("beacon_groups", []) if g.get("enabled", True)]),
    )

    # Authentification
    try:
        _token = anoloc_login(login, password)
        log.info("Authentification Anoloc reussie")
    except Exception as e:
        log.error("Echec authentification Anoloc: %s", e)
        set_collecting_status(db, False, error=f"Auth echouee: {e}")
        sys.exit(1)

    # Signaler le demarrage
    set_collecting_status(db, True)

    # --- 2 collectes espacees de 30 secondes ---
    for cycle in range(1, NB_CYCLES + 1):
        if _stop:
            break

        try:
            devices_info = get_devices(_token)
            count = collect_once(_token, device_group_map, devices_info, db)
            log.info("Cycle %d/%d: %d positions collectees", cycle, NB_CYCLES, count)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                log.warning("Token expire, re-authentification...")
                try:
                    _token = anoloc_login(login, password)
                    log.info("Re-authentification reussie")
                except Exception as e2:
                    log.error("Re-auth echouee: %s", e2)
                    set_collecting_status(db, False, error=f"Re-auth echouee: {e2}")
                    break
            else:
                log.error("Erreur API Anoloc: %s", e)
        except Exception as e:
            log.error("Erreur collecte: %s", e)

        # Attendre 30s avant le prochain cycle (sauf apres le dernier)
        if cycle < NB_CYCLES and not _stop:
            wait_end = time.time() + COLLECT_INTERVAL
            while not _stop and time.time() < wait_end:
                time.sleep(1)

    # Mettre a jour le statut
    set_collecting_status(db, False)
    log.info("Collecteur termine")

    if _client:
        _client.close()


if __name__ == "__main__":
    main()
