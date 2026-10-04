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

        # Site - visites libres : jour public SAISON et aucune epreuve active
        # (sinon les checkpoints servent au delestage : spectateurs).
        site = cfg["site"]
        gate = M.site_gate_db(db, cfg, now)
        site_actif = gate["collecte"]
        if site["enabled"]:
            log.info("Site visites libres : %s%s", gate["libelle"],
                     (" (fenetre %s-%s)" % (gate["fenetre"]["debut"], gate["fenetre"]["fin"])
                      if gate["fenetre"] else ""))
            if not site_actif:
                log.info("Site visites libres : pas de collecte (%s).", gate["raison"])
        site_jour = (db[M.COL_SITE_JOURS].find_one({"_id": today}, {"tx_depuis": 1, "tx_jusqu_a": 1})
                     or {}) if site_actif else {}

        # Fenetre transactions : depuis le dernier passage (chevauchement de
        # 10 min, dedoublonne par _id = transaction_id), sinon depuis minuit.
        debut_jour = today + "T00:00:00"

        def _depuis(j):
            if j.get("tx_jusqu_a"):
                try:
                    prev = dt.datetime.strptime(j["tx_jusqu_a"], "%Y-%m-%dT%H:%M:%S")
                    return max(debut_jour, (prev - dt.timedelta(minutes=M.TX_RECOUVREMENT_MIN))
                               .strftime("%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    pass
            return debut_jour

        from_m = _depuis(jour)
        from_s = _depuis(site_jour) if site_actif else None
        filtres = {}
        if cfg.get("transactions"):
            filtres["musee"] = lambda tx: M.passage_doc(tx, cfg["area_id"])
        if site_actif:
            site_locs = {loc["id"]: loc for loc in site["locations"]}
            filtres["site"] = lambda tx: M.site_passage_doc(tx, site_locs)
        from_str = min(x for x in (from_m if "musee" in filtres else None, from_s) if x) \
            if filtres else None
        to_str = now_p.strftime("%Y-%m-%dT%H:%M:%S")

        erreur = None
        compteurs, erreurs, passages = {}, {}, None
        s_compteurs, s_erreurs, s_passages = {}, {}, None
        max_date, plafonne = None, False
        t0 = time.time()
        try:
            sock = B.connect()
            try:
                log.info("Borne %s - compteurs du musee (Area %s %s)", B.adresse(),
                         cfg["area_id"], cfg["area_nom"])
                compteurs, erreurs = B.lire_compteurs(sock, cfg, log)
                # En dry-run, les compteurs du site sont lus meme hors jour de
                # visites libres (verification), jamais ecrits.
                if site["configure"] and (site_actif or args.dry_run):
                    log.info("Compteurs du site (visites libres)%s",
                             "" if site_actif else " - lecture de controle, non collectes")
                    s_compteurs, s_erreurs = B.lire_compteurs(
                        sock, {"locations": site["locations"]}, log)
                if filtres:
                    try:
                        res, max_date, plafonne, _lues = B.lire_transactions(
                            sock, from_str, to_str, filtres, log)
                        passages = res.get("musee")
                        s_passages = res.get("site")
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
            if site["configure"]:
                log.info("[dry-run] site : collecte=%s (%s) ; compteurs %s ; %s passages site",
                         site_actif, gate["raison"], s_compteurs,
                         len(s_passages) if s_passages is not None else "-")
            return 0

        if compteurs:
            db[M.COL_RELEVES].insert_one(M.build_releve(now, compteurs, erreurs))
        if passages:
            for d in passages:
                db[M.COL_PASSAGES].update_one({"_id": d["_id"]}, {"$set": d}, upsert=True)

        def _etat_tx(j, debut):
            """tx_depuis / tx_jusqu_a / tx_retard apres une lecture reussie."""
            e = {}
            if debut == debut_jour and not j.get("tx_depuis"):
                e["tx_depuis"] = "00:00:00"
            elif not j.get("tx_depuis"):
                # La lecture est partie de from_str (<= debut) : couverture reelle.
                e["tx_depuis"] = from_str[11:]
            # Plafond : on ne repart que du dernier passage vu, pas de trou.
            e["tx_jusqu_a"] = (max_date.replace(" ", "T") if plafonne and max_date else to_str)
            e["tx_retard"] = bool(plafonne)
            return e

        etat = {"derniere_collecte": now, "derniere_erreur": erreur}
        if passages is not None:
            etat.update(_etat_tx(jour, from_str))
        db[M.COL_JOURS].update_one({"_id": today}, {"$set": etat}, upsert=True)

        day = M.save_day(db, today, cfg)
        # Premier passage apres minuit : fige l'agregat de la veille.
        if now_p.hour == 0 and now_p.minute < 15:
            M.save_day(db, M.shift_date(today, days=-1), cfg)
            veille = M.shift_date(today, days=-1)
            if site["configure"] and db[M.COL_SITE_JOURS].find_one({"_id": veille}, {"_id": 1}):
                M.save_site_day(db, veille, cfg)
        log.info("Visiteurs du %s : %s (source %s, compteur %s, transactions %s)",
                 today, day["visiteurs"], day["source"], day["visiteurs_compteur"],
                 day["visiteurs_tx"])

        if site_actif:
            if s_compteurs:
                db[M.COL_SITE_RELEVES].insert_one(M.build_releve(now, s_compteurs, s_erreurs))
            for d in s_passages or []:
                db[M.COL_SITE_PASSAGES].update_one({"_id": d["_id"]}, {"$set": d}, upsert=True)
            s_etat = {"derniere_collecte": now, "fenetre": gate["fenetre"],
                      "horaires": gate["horaires"],
                      "derniere_erreur": erreur if (erreur or not s_compteurs) else None}
            if not s_compteurs and not erreur:
                s_etat["derniere_erreur"] = "compteurs du site : %s" % (s_erreurs or "aucune reponse")
            if s_passages is not None:
                s_etat.update(_etat_tx(site_jour, from_str))
            db[M.COL_SITE_JOURS].update_one({"_id": today}, {"$set": s_etat}, upsert=True)
            sday = M.save_site_day(db, today, cfg, gate["fenetre"])
            log.info("Site visites libres du %s : entrees %s, sorties %s, presents %s "
                     "(pic %s, source %s)", today, sday["entrees"], sday["sorties"],
                     sday["presents_now"], sday["presents_max"], sday["source"])
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
