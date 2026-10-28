"""
patrol_engine.py - Moteur des patrouilles programmees (tache planifiee).

Lance chaque minute par la tache planifiee Windows "Cockpit - Patrouilles"
(patrol_engine.bat) : JAMAIS de thread ni de planificateur dans le processus
Cockpit. Chaque execution boucle ~55 s, un passage toutes les 3 s, puis
s'arrete :

- propositions de patrouille expirees -> tablette suivante du groupe ;
- pauses / reprises selon le statut de l'agent, fiche close par le PC Org ;
- (livraison 2) lancements des planifications ; (livraison 3) intrusions
  confirmees envoyees aux tablettes (intrusion_dispatch.tick).

Verrou en base (`cockpit_settings._id = "patrol_engine_lock"`, bail de 70 s
pose par find_one_and_update) : une execution qui chevauche la precedente
sort aussitot. Journal : logs/patrol_engine-AAAAMMJJ.log.

    python patrol_engine.py            # boucle ~55 s (tache planifiee)
    python patrol_engine.py --once     # un seul passage (essai)
"""
import argparse
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

import intrusion_dispatch as ID
import patrols as P

LOCK_ID = "patrol_engine_lock"
LEASE_S = 70
RUN_FOR_S = 55
TICK_S = 3

log = logging.getLogger("patrol_engine")


def _db():
    env = os.getenv("TITAN_ENV", "dev").strip().lower()
    name = "titan" if env in {"prod", "production"} else "titan_dev"
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=8000)
    return client[name]


def _setup_logging():
    here = os.path.dirname(os.path.abspath(__file__))
    logs = os.path.join(here, "logs")
    os.makedirs(logs, exist_ok=True)
    path = os.path.join(logs, "patrol_engine-%s.log" % datetime.now().strftime("%Y%m%d"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()])


def take_lock(db, owner, now=None):
    """Bail du verrou : pris si libre ou expire, ou deja a nous (renouvellement)."""
    now = now or datetime.now(timezone.utc)
    until = now + timedelta(seconds=LEASE_S)
    try:
        doc = db["cockpit_settings"].find_one_and_update(
            {"_id": LOCK_ID, "$or": [{"until": {"$lt": now}}, {"owner": owner}]},
            {"$set": {"owner": owner, "until": until, "at": now}},
            upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        return False   # verrou tenu par une autre execution (l'upsert a echoue)
    return bool(doc and doc.get("owner") == owner)


def release_lock(db, owner):
    db["cockpit_settings"].update_one({"_id": LOCK_ID, "owner": owner},
                                      {"$set": {"until": datetime.now(timezone.utc) - timedelta(seconds=1)}})


def run(db, once=False):
    owner = str(uuid.uuid4())
    if not take_lock(db, owner):
        log.info("verrou tenu par une autre execution : sortie")
        return 0
    P.ensure_indexes(db)
    start = time.monotonic()
    actions = 0
    try:
        while True:
            try:
                actions += P.tick(db)
            except Exception:
                log.exception("passage du moteur en echec")
            try:        # livraison 3 : intrusions confirmees -> tablettes (groupes en mode ai)
                actions += ID.tick(db)
            except Exception:
                log.exception("passage des intrusions en echec")
            if once or time.monotonic() - start >= RUN_FOR_S:
                break
            time.sleep(TICK_S)
            if not take_lock(db, owner):
                log.warning("verrou perdu : sortie")
                break
    finally:
        release_lock(db, owner)
    if actions:
        log.info("%d action(s)", actions)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Moteur des patrouilles programmees")
    p.add_argument("--once", action="store_true", help="un seul passage")
    args = p.parse_args(argv)
    _setup_logging()
    return run(_db(), once=args.once)


if __name__ == "__main__":
    sys.exit(main())
