#!/usr/bin/env python3
"""
flespi_poller.py - Adaptateur flespi des balises Teltonika (TRANSITOIRE).

Le temps que le port 5027 soit ouvert vers le recepteur maison, les balises
emettent vers le canal flespi ; ce script relit les messages du canal par
l'API REST (sortant HTTPS uniquement, aucun port entrant) et les passe a la
MEME ingestion que le recepteur (balises_core.ingest). Une balise vue par
les deux chemins pendant la bascule n'est jamais dupliquee (cle imei + ts).

Lance par tache planifiee toutes les minutes ("Cockpit - Balises flespi"),
2 releves espaces de 30 s, comme anoloc_collector.py.

Prerequis :
  - FLESPI_TOKEN (variable d'environnement du compte de la tache)
  - balises_config {_id: "flespi", enabled: true, channel_id: <id>}
    (Configuration > Balises GPS > Teltonika)
Curseur : server.timestamp du dernier message lu (le canal range ses
messages par heure de reception flespi), relu inclusivement.
"""

import logging
import os
import signal
import sys
import time
import json
from datetime import datetime, timezone

import requests
from pymongo import MongoClient

import balises_core as B

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_TITAN_ENV = os.getenv("TITAN_ENV", "dev").strip().lower()
DB_NAME = "titan" if _TITAN_ENV in {"prod", "production"} else "titan_dev"
API = "https://flespi.io"
SOURCE = "flespi"
CONFIG_ID = "flespi"

NB_CYCLES = 2
CYCLE_INTERVAL = 30
PAGE = 1000
FIRST_LOOKBACK_S = 24 * 3600   # premier releve : le buffer du canal (1 jour)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [flespi] %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("flespi_poller")
_stop = False


def _handle_signal(signum, frame):
    global _stop
    _stop = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def fetch(token, channel_id, since):
    """Messages du canal recus par flespi depuis `since` (inclus)."""
    resp = requests.get(
        "%s/gw/channels/%s/messages" % (API, channel_id),
        params={"data": json.dumps({"from": since, "count": PAGE})},
        headers={"Authorization": "FlespiToken %s" % token},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("result") or []


def poll_once(db, token, cfg):
    since = cfg.get("cursor") or (time.time() - FIRST_LOOKBACK_S)
    total = {"accepted": 0, "duplicates": 0, "unknown": 0, "invalid": 0, "bad_ts": 0}
    for _ in range(20):  # au plus 20 pages par releve
        msgs = fetch(token, cfg["channel_id"], since)
        if not msgs:
            break
        st = B.ingest(db, msgs, SOURCE)
        for k in total:
            total[k] += st.get(k, 0)
        prev = since
        since = max([since] + [float(m.get("server.timestamp") or 0) for m in msgs])
        db[B.COL_CONFIG].update_one({"_id": CONFIG_ID}, {"$set": {"cursor": since}})
        # Page incomplete, ou page pleine sans progression (1000 messages a
        # la meme seconde) : on s'arrete plutot que de boucler
        if len(msgs) < PAGE or since <= prev:
            break
    return total


def set_status(db, **fields):
    fields["last_run"] = datetime.now(timezone.utc)
    db[B.COL_CONFIG].update_one({"_id": CONFIG_ID}, {"$set": fields}, upsert=True)


def main():
    db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)[DB_NAME]
    cfg = db[B.COL_CONFIG].find_one({"_id": CONFIG_ID}) or {}
    if not cfg.get("enabled"):
        sys.exit(0)
    if not cfg.get("channel_id"):
        set_status(db, last_error="Canal flespi non renseigne")
        sys.exit(0)
    token = os.getenv("FLESPI_TOKEN", "").strip()
    if not token:
        set_status(db, last_error="FLESPI_TOKEN absent de l'environnement")
        sys.exit(0)
    B.ensure_indexes(db)
    for cycle in range(NB_CYCLES):
        if _stop:
            break
        try:
            cfg = db[B.COL_CONFIG].find_one({"_id": CONFIG_ID}) or cfg
            st = poll_once(db, token, cfg)
            set_status(db, last_error=None, last_stats=st)
            if st["accepted"] or st["unknown"]:
                log.info("releve %d : %s", cycle + 1, st)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            set_status(db, last_error="HTTP %s flespi" % code)
            log.error("flespi HTTP %s", code)
            break
        except Exception as e:
            set_status(db, last_error=str(e)[:200])
            log.error("releve flespi : %s", e)
        if cycle < NB_CYCLES - 1:
            end = time.time() + CYCLE_INTERVAL
            while not _stop and time.time() < end:
                time.sleep(1)


if __name__ == "__main__":
    main()
