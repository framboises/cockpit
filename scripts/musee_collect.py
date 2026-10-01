"""Collecte de la frequentation du Musee des 24 Heures du Mans.

Lancee toutes les 5 minutes par la tache planifiee "Cockpit - Musee"
(scripts/install_musee_task.ps1), 24 h/24 : un releve avant 10h sert de base
du jour. AUTONOME du live-controle : ne lit ni n'ecrit data_access,
hsh_structure, hsh_transactions_agg ; n'ecrit que musee_*. La configuration
(cockpit_settings._id="musee", onglet Musee de /live-controle) n'est jamais
creee ici : sans elle, "musee non configure" et sortie propre.

Cote borne Handshake : LECTURE SEULE (Inquiry Counter + Inquiry Transactions),
sur une connexion TCP qui lui est propre (la borne n'a ni session ni jeton :
chaque connexion est independante, comme scan_import_hsh.py qui tourne deja
en parallele du live-controle). Dialogue dans musee_borne.py (fonctions
reseau de live_controle.py, inchangees).

Usage :
    python scripts/musee_collect.py              # production
    python scripts/musee_collect.py --dry-run    # interroge la borne, n'ecrit rien
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pymongo import MongoClient  # noqa: E402

import musee as M  # noqa: E402
import musee_borne as B  # noqa: E402

LOG_DIR = os.path.join(ROOT, "logs")
LOCK_PATH = os.path.join(LOG_DIR, "musee_collect.lock")


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "musee_collect-%s.log" % dt.datetime.now().strftime("%Y%m%d"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S",
                        handlers=[logging.FileHandler(path, encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    return logging.getLogger("musee_collect")


def _acquire_lock():
    """Verrou exclusif non bloquant (libere par l'OS si le process meurt)."""
    os.makedirs(LOG_DIR, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _db():
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    name = os.getenv("MONGO_DB", "").strip()
    if not name:
        env = os.getenv("TITAN_ENV", "dev").strip().lower()
        name = "titan" if env in {"prod", "production"} else "titan_dev"
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    return client, client[name]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Collecte frequentation musee")
    ap.add_argument("--dry-run", action="store_true", help="interroge la borne, n'ecrit rien")
    args = ap.parse_args(argv)

    log = _setup_logging()
    lock = _acquire_lock()
    if lock is None:
        log.warning("Collecte precedente encore en cours : sortie.")
        return 0

    client, db = _db()
    try:
        cfg = M.get_config(db)
        if cfg is None or not cfg["configure"]:
            log.info("musee non configure (cockpit_settings.musee : Area et horaires a "
                     "choisir dans /live-controle, onglet Musee) : sortie.")
            return 0
        if not cfg["enabled"]:
            log.info("Collecte musee desactivee (cockpit_settings.musee.enabled=false).")
            return 0
        if not args.dry_run:
            M.ensure_indexes(db)

        now = M.now_utc()
        now_p = now.astimezone(M.TZ_PARIS)
        today = now_p.strftime("%Y-%m-%d")
        jour = db[M.COL_JOURS].find_one({"_id": today}, {"tx_depuis": 1, "tx_jusqu_a": 1}) or {}

        # Fenetre transactions : depuis le dernier passage (chevauchement de
        # 10 min, dedoublonne par _id = transaction_id), sinon depuis minuit.
        debut_jour = today + "T00:00:00"
        from_str = debut_jour
        if jour.get("tx_jusqu_a"):
            try:
                prev = dt.datetime.strptime(jour["tx_jusqu_a"], "%Y-%m-%dT%H:%M:%S")
                from_str = max(debut_jour, (prev - dt.timedelta(minutes=M.TX_RECOUVREMENT_MIN))
                               .strftime("%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                pass
        to_str = now_p.strftime("%Y-%m-%dT%H:%M:%S")

        erreur = None
        compteurs, erreurs, passages = {}, {}, None
        max_date, plafonne = None, False
        t0 = time.time()
        try:
            sock = B.connect()
            try:
                log.info("Borne %s - compteurs du musee (Area %s %s)", B.adresse(),
                         cfg["area_id"], cfg["area_nom"])
                compteurs, erreurs = B.lire_compteurs(sock, cfg, log)
                if cfg.get("transactions"):
                    try:
                        passages, max_date, plafonne, _lues = B.lire_passages(
                            sock, cfg, from_str, to_str, log)
                    except Exception as exc:
                        erreur = "transactions : %s" % exc
                        log.warning("Transactions en echec : %s", exc)
            finally:
                B.close(sock)
        except Exception as exc:
            erreur = "borne : %s" % exc
            log.error("Borne injoignable : %s", exc)
        log.info("Dialogue borne : %.1f s", time.time() - t0)

        if args.dry_run:
            log.info("[dry-run] releve %s ; %s passages musee", compteurs,
                     len(passages) if passages is not None else "-")
            return 0

        if compteurs:
            db[M.COL_RELEVES].insert_one(M.build_releve(now, compteurs, erreurs))
        if passages:
            for d in passages:
                db[M.COL_PASSAGES].update_one({"_id": d["_id"]}, {"$set": d}, upsert=True)

        etat = {"derniere_collecte": now, "derniere_erreur": erreur}
        if passages is not None:
            if from_str == debut_jour and not jour.get("tx_depuis"):
                etat["tx_depuis"] = "00:00:00"
            elif not jour.get("tx_depuis"):
                etat["tx_depuis"] = from_str[11:]
            # Plafond : on ne repart que du dernier passage vu, pas de trou.
            etat["tx_jusqu_a"] = (max_date.replace(" ", "T") if plafonne and max_date else to_str)
            etat["tx_retard"] = bool(plafonne)
        db[M.COL_JOURS].update_one({"_id": today}, {"$set": etat}, upsert=True)

        day = M.save_day(db, today, cfg)
        # Premier passage apres minuit : fige l'agregat de la veille.
        if now_p.hour == 0 and now_p.minute < 15:
            M.save_day(db, M.shift_date(today, days=-1), cfg)
        log.info("Visiteurs du %s : %s (source %s, compteur %s, transactions %s)",
                 today, day["visiteurs"], day["source"], day["visiteurs_compteur"],
                 day["visiteurs_tx"])
        return 1 if erreur and not compteurs else 0
    except Exception as exc:
        log.exception("Erreur collecte musee : %s", exc)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    rc = 2
    try:
        rc = main()
    finally:
        # Comme vision_sync : aucun thread residuel (moniteurs pymongo, dont le
        # client cree a l'import de live_controle) ne doit garder la tache
        # planifiee en vie.
        logging.shutdown()
        sys.stdout.flush()
        os._exit(rc)
