"""musee_borne.py - Dialogue LECTURE SEULE avec la borne Handshake pour le musee.

Partage par scripts/musee_collect.py (collecte toutes les 5 min) et
musee_api.py (structure et bouton "Tester" de l'onglet Musee de
/live-controle). Les fonctions reseau et les parseurs viennent de
live_controle.py, inchanges. La borne n'a ni session ni jeton : chaque
connexion TCP est independante (meme TELEGRAM_ID que le live-controle), on
ouvre donc toujours la sienne. Inquiry Counter et Inquiry Transactions
uniquement : rien n'est jamais ecrit sur la borne.

live_controle cree un client Mongo a l'import : il est importe a l'appel,
jamais au chargement du module (cote Flask, seule une action admin le charge).
"""

from __future__ import annotations

import datetime as dt
import logging
import socket
import time

import musee as M

MAX_PAGES_TX = 100            # 100 x 100 transactions par passage, au plus

_log = logging.getLogger(__name__)


def _lc():
    import live_controle
    return live_controle


def adresse():
    lc = _lc()
    return "%s:%s" % (lc.HSH_IP, lc.HSH_PORT)


def connect(essais=2, pause=3):
    """Connexion propre a la borne ; un second essai si elle est occupee."""
    lc = _lc()
    last = None
    for attempt in range(essais):
        try:
            sock = socket.create_connection((lc.HSH_IP, lc.HSH_PORT), timeout=lc.CONNECT_TIMEOUT)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return sock
        except OSError as exc:
            last = exc
            if attempt + 1 < essais:
                time.sleep(pause)
    raise last


def close(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


def lire_compteurs(sock, cfg, log=None):
    """Inquiry Counter de chaque location de cfg["locations"]."""
    lc = _lc()
    log = log or _log
    sock.settimeout(lc.READ_TIMEOUT_COUNTER)
    compteurs, erreurs = {}, {}
    for loc in cfg["locations"]:
        try:
            resp = lc.envoyer_et_recevoir(sock, lc.encapsuler_counter(
                lc.build_counter_location_xml(loc["id"], loc["type"])))
            data = lc.parse_counter_single(resp) if resp else None
            if not data:
                erreurs[loc["id"]] = "pas_de_reponse"
                continue
            compteurs[loc["id"]] = {
                "type": loc["type"],
                "nom": data.get("location_name") or loc["nom"],
                "entries": M.to_int(data.get("entries")),
                "exits": M.to_int(data.get("exits")),
                "current": M.to_int(data.get("current")),
            }
            log.info("  %s %-12s E=%s S=%s", loc["id"], compteurs[loc["id"]]["nom"],
                     compteurs[loc["id"]]["entries"], compteurs[loc["id"]]["exits"])
        except Exception as exc:  # une location en echec ne bloque pas les autres
            erreurs[loc["id"]] = str(exc)[:200]
            log.warning("  %s : %s", loc["id"], exc)
    return compteurs, erreurs


def lire_passages(sock, cfg, from_str, to_str, log=None, max_pages=MAX_PAGES_TX):
    """Transactions de toute la borne sur [from, to], filtrees sur l'Area du
    musee. Retourne (docs, dernier_horodatage_vu, plafonne, nb_lues)."""
    res, max_date, plafonne, lues = lire_transactions(
        sock, from_str, to_str, {"musee": lambda tx: M.passage_doc(tx, cfg["area_id"])},
        log=log, max_pages=max_pages)
    return res["musee"], max_date, plafonne, lues


def lire_transactions(sock, from_str, to_str, filtres, log=None, max_pages=MAX_PAGES_TX):
    """Transactions de toute la borne sur [from, to] (heure LOCALE Paris, cf.
    scan_import_hsh.collecter : From/To gardes a chaque page), une seule
    lecture pour plusieurs perimetres. filtres = {nom: f(tx) -> doc | None}.
    Retourne ({nom: [docs]}, dernier_horodatage_vu, plafonne, nb_lues)."""
    lc = _lc()
    log = log or _log
    sock.settimeout(lc.READ_TIMEOUT_TRANSACTIONS)
    vus = set()
    res = {k: [] for k in filtres}
    cursor, page, plafonne = None, 0, False
    max_date = None
    while True:
        page += 1
        resp = lc.envoyer_et_recevoir(sock, lc.encapsuler_transactions(lc.build_transactions_xml(
            from_dt=from_str, to_dt=to_str, last_tx_id=cursor)))
        if not resp:
            break
        not_complete, txs, max_txid = lc.parse_transactions(resp)
        nouveaux = 0
        for tx in txs:
            tid = tx.get("transaction_id")
            if tid in vus:
                continue
            vus.add(tid)
            nouveaux += 1
            dp = tx.get("date_paris")
            if dp and (max_date is None or dp > max_date):
                max_date = dp
            for nom, f in filtres.items():
                d = f(tx)
                if d:
                    res[nom].append(d)
        if not txs or not nouveaux or not not_complete or max_txid is None:
            break
        if page >= max_pages:
            plafonne = True
            break
        cursor = str(max_txid)
    log.info("  transactions %s -> %s : %d lues en %d page(s), %s%s",
             from_str[11:], to_str[11:], len(vus), page,
             ", ".join("%d %s" % (len(v), k) for k, v in res.items()),
             " (PLAFOND atteint)" if plafonne else "")
    return res, max_date, plafonne, len(vus)


def lire_inventaire(timeout=20):
    """Inquiry Counter global (meme requete que live_controle.executer_inventaire,
    SANS son ecriture dans hsh_structure) : liste a plat des locations."""
    lc = _lc()
    sock = connect(essais=1)
    try:
        sock.settimeout(timeout)
        resp = lc.envoyer_et_recevoir(sock, lc.encapsuler_counter(lc.build_counter_global_xml()))
    finally:
        close(sock)
    if not resp:
        raise RuntimeError("pas de reponse a l'inventaire")
    return lc.parse_counter_global(resp)


def tester(cfg, db=None, minutes=60, max_pages=5):
    """Equivalent du --dry-run du collecteur pour une config (pas forcement
    enregistree) : compteurs + passages de la derniere heure. Rien n'est ecrit.
    Avec db : apercu du jour recalcule avec ces horaires (lecture seule)."""
    t0 = time.time()
    now_p = M.now_utc().astimezone(M.TZ_PARIS)
    to_str = now_p.strftime("%Y-%m-%dT%H:%M:%S")
    from_str = max(now_p.strftime("%Y-%m-%dT00:00:00"),
                   (now_p - dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S"))
    res = {"ok": True, "borne": adresse(), "compteurs": {}, "erreurs": {}, "transactions": None,
           "erreur": None}
    try:
        sock = connect(essais=1)
        try:
            res["compteurs"], res["erreurs"] = lire_compteurs(sock, cfg)
            site = cfg.get("site") or {}
            if site.get("locations"):
                c, e = lire_compteurs(sock, {"locations": site["locations"]})
                res["site_compteurs"], res["site_erreurs"] = c, e
            if cfg.get("transactions"):
                docs, _max, plafonne, lues = lire_passages(sock, cfg, from_str, to_str,
                                                           max_pages=max_pages)
                statuts = set(cfg["statuts_passage"])
                par_cp, refus = {}, 0
                for d in docs:
                    if d["status"] not in statuts:
                        refus += 1
                        continue
                    slot = par_cp.setdefault(d["checkpoint_id"], {"nom": d["checkpoint_nom"], "n": 0})
                    slot["n"] += 1
                res["transactions"] = {"depuis": from_str[11:16], "jusqu_a": to_str[11:16],
                                       "lues": lues, "au_musee": len(docs),
                                       "comptees": len(docs) - refus, "refus": refus,
                                       "par_checkpoint": par_cp, "plafonne": plafonne}
        finally:
            close(sock)
    except Exception as exc:
        res["ok"] = False
        res["erreur"] = "borne : %s" % exc
    res["duree_s"] = round(time.time() - t0, 1)
    if db is not None:
        today = now_p.strftime("%Y-%m-%d")
        try:
            day = M.compute_day(db, today, cfg)
            res["apercu_jour"] = {"date": today, "visiteurs": day["visiteurs"],
                                  "source": day["source"],
                                  "horaires": M.horaires_du_jour(cfg, today),
                                  "statut": M.statut_ouverture(cfg, now_p)[1]}
        except Exception as exc:
            res["apercu_jour"] = {"erreur": str(exc)[:200]}
        if (cfg.get("site") or {}).get("enabled"):
            try:
                g = M.site_gate_db(db, cfg)
                res["site_gate"] = {"libelle": g["libelle"], "fenetre": g["fenetre"],
                                    "collecte": g["collecte"]}
            except Exception as exc:
                res["site_gate"] = {"libelle": "erreur : %s" % str(exc)[:200]}
    return res
