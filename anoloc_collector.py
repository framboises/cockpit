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
from datetime import datetime, timezone, timedelta

# Fuseau horaire Paris (UTC+1 / UTC+2 selon DST)
try:
    from zoneinfo import ZoneInfo
    TZ_LOCAL = ZoneInfo("Europe/Paris")
except ImportError:
    # Python < 3.9 fallback
    import dateutil.tz
    TZ_LOCAL = dateutil.tz.gettz("Europe/Paris")


def now_local():
    return datetime.now(TZ_LOCAL)

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
    db["anoloc_logs"].create_index("ts", expireAfterSeconds=7 * 24 * 3600)  # TTL 7 jours


# ---------------------------------------------------------------------------
# Live control (activation/desactivation depuis l'admin)
# ---------------------------------------------------------------------------

LIVE_CONTROL_ID = "live-control"

def get_live_control(db):
    """Retourne le document live-control."""
    return db["anoloc_config"].find_one({"_id": LIVE_CONTROL_ID}) or {}


def is_collecting_enabled(db):
    """Verifie si la collecte est activee via le document live-control."""
    doc = get_live_control(db)
    return bool(doc.get("collecting", False))


def is_logging_enabled(db):
    """Verifie si le logging detaille est active."""
    doc = get_live_control(db)
    return bool(doc.get("logging", False))


def set_collecting_status(db, running, error=None):
    """Met a jour le statut du collecteur dans live-control."""
    now = now_local()
    update = {
        "running": running,
        "last_run": now,
        "last_run_display": now.strftime("%d/%m/%Y %H:%M:%S"),
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


def fmt_local(dt):
    """Formate une datetime en heure Paris lisible."""
    if not dt:
        return "-"
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_LOCAL)
        return dt.astimezone(TZ_LOCAL).strftime("%d/%m %H:%M:%S")
    except Exception:
        return str(dt)


def db_log(db, level, message, details=None):
    """Insere un log dans la collection anoloc_logs."""
    now = now_local()
    doc = {
        "ts": now,
        "ts_display": now.strftime("%d/%m/%Y %H:%M:%S"),
        "level": level,
        "message": message,
    }
    if details:
        doc["details"] = details
    try:
        db["anoloc_logs"].insert_one(doc)
    except Exception:
        pass


def collect_once(token, device_group_map, devices_info, db, logging_enabled=False):
    """Une iteration de collecte: GET /live, insert positions, upsert latest."""
    now = now_local()
    live_data = get_live(token)

    inserted = 0
    device_details = []

    for device_id, frame in live_data.items():
        dev_info = devices_info.get(device_id, {})
        dev_label = dev_info.get("label", device_id)
        in_group = device_id in device_group_map

        if not isinstance(frame, dict):
            if logging_enabled and in_group:
                device_details.append({
                    "id": device_id,
                    "label": dev_label,
                    "status": "offline",
                    "reason": "pas de frame (liste vide)",
                })
            continue
        grp = device_group_map.get(device_id)
        if not grp:
            continue

        lat = frame.get("latitude")
        lng = frame.get("longitude")
        if lat is None or lng is None:
            if logging_enabled:
                device_details.append({
                    "id": device_id,
                    "label": dev_label,
                    "status": frame.get("status", "?"),
                    "reason": "pas de coordonnees GPS",
                })
            continue

        sent_at_str = frame.get("sent_at")
        sent_at = None
        if sent_at_str:
            try:
                sent_at = datetime.fromisoformat(sent_at_str.replace("Z", "+00:00")).astimezone(TZ_LOCAL)
            except (ValueError, TypeError):
                sent_at = now

        power = frame.get("power_supply") or {}
        battery_pct = power.get("battery_percentage")

        # last_real_frame_sent_at = derniere vraie frame (pas heartbeat)
        last_real_str = frame.get("last_real_frame_sent_at")
        last_real = None
        if last_real_str:
            try:
                last_real = datetime.fromisoformat(last_real_str.replace("Z", "+00:00")).astimezone(TZ_LOCAL)
            except (ValueError, TypeError):
                pass

        gps_fix = frame.get("gps_fix", 0)

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
            "gps_fix": gps_fix,
            "sent_at": sent_at or now,
            "last_real_at": last_real,
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

        # Determiner le vrai statut
        anoloc_status = frame.get("status", "offline")
        has_gps = gps_fix > 0
        last_real_age = None
        if last_real:
            try:
                last_real_age = abs((now - last_real).total_seconds())
            except Exception:
                pass
        really_online = anoloc_status != "offline" and (last_real_age is None or last_real_age < 1800)

        if logging_enabled:
            status_label = anoloc_status
            if not has_gps:
                status_label += " (sans GPS)"
            if last_real_age is not None and last_real_age > 1800:
                status_label += f" (derniere frame il y a {int(last_real_age/60)}min)"

            device_details.append({
                "id": device_id,
                "label": dev_label,
                "status": status_label,
                "gps": "OK" if has_gps else "NON",
                "speed": frame.get("speed", 0) if has_gps else "-",
                "battery": battery_pct,
                "collected": True,
                "online": really_online,
                "last_real": fmt_local(last_real) if last_real else "-",
                "sent_at": fmt_local(sent_at) if sent_at else "-",
            })

    # Log du cycle
    if logging_enabled:
        online = len([d for d in device_details if d.get("online")])
        offline = len([d for d in device_details if not d.get("online")])
        gps_ok = len([d for d in device_details if d.get("gps") == "OK"])
        db_log(db, "info",
               f"Collecte: {online} en ligne, {offline} hors ligne, {gps_ok} avec GPS, {inserted} positions",
               {"devices": device_details})

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
        sys.exit(0)

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
        sys.exit(0)

    # Signaler le demarrage
    set_collecting_status(db, True)
    _logging = is_logging_enabled(db)
    if _logging:
        db_log(db, "info", f"Demarrage collecteur: {len(device_group_map)} devices, {len([g for g in config.get('beacon_groups', []) if g.get('enabled', True)])} groupes")

    # --- 2 collectes espacees de 30 secondes ---
    for cycle in range(1, NB_CYCLES + 1):
        if _stop:
            break

        try:
            devices_info = get_devices(_token)
            count = collect_once(_token, device_group_map, devices_info, db, logging_enabled=_logging)
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
