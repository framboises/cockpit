#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ecoute_pda.py -- Affiche en (quasi) direct les titres lus par un PDA Skidata.

Le HSH (HSHIF25, 192.168.2.10:5205) ne pousse rien : on l'interroge en
boucle avec un curseur LastTransactionId (lecture seule, comme live_controle)
et on filtre sur le nom du checkpoint ("PDA 217", "PDA_217"...).

Usage:
    python scripts/ecoute_pda.py 217 [--depuis 30] [--intervalle 2] [--tous]

    --depuis N      minutes d'historique affichees au demarrage (defaut 10)
    --intervalle S  secondes entre deux interrogations (defaut 2)
    --tous          affiche aussi les checkpoints des autres lecteurs (diagnostic du nom)
    --duree N       arret apres N secondes (defaut 0 = jamais)
"""

import argparse
import datetime
import os
import re
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_controle import (  # noqa: E402
    HSH_IP, HSH_PORT, CONNECT_TIMEOUT, READ_TIMEOUT_TRANSACTIONS, DEFAULT_TX_ISSUER,
    encapsuler_transactions, envoyer_et_recevoir,
    parse_transactions, _label_status, _label_validated,
)

# HSHIF25 ne sait PAS filtrer les Transactions par checkpoint (le filtre
# <Location> n'existe que pour les Counter, spec v2.34 SS4.27/4.35) : on recoit
# tout le flux et on filtre ici. Pour alleger chaque reponse, on supprime ce
# qui ne sert pas a l'ecoute (spec SS4.27.4) :
#   1 RawData, 4 Gate, 16 Venue, 32 TSProperty  (suppression)
#   128 nouveau layout, 256 erreurs incluses, 1024 autres billetteries
# On garde Checkpoint (le filtre PDA), Area (sens Entree/Sortie), Permission (UPID).
OPTION_ECOUTE = 1 + 4 + 16 + 32 + 128 + 256 + 1024  # 1461
MAX_TX_PAGE = 1000


def build_xml(from_dt=None, to_dt=None, last_tx_id=None):
    attrs = f'Type="Transactions" MaxTransactions="{MAX_TX_PAGE}" Option="{OPTION_ECOUTE}"'
    if from_dt:
        attrs += f' From="{from_dt}"'
    if to_dt:
        attrs += f' To="{to_dt}"'
    if last_tx_id is not None:
        attrs += f' LastTransactionId="{last_tx_id}"'
    return f"""<TSData>
    <Header>
        <Version>HSHIF25</Version>
        <Issuer>{DEFAULT_TX_ISSUER}</Issuer>
        <Receiver>1</Receiver>
        <ID>EcoutePDA_{int(time.time())}</ID>
    </Header>
    <Inquiry {attrs}/>
</TSData>"""


def _cp_name(tx):
    cp = tx.get("checkpoint") or {}
    return cp.get("Name") or cp.get("name") or ""


def _afficher(tx):
    statut = tx.get("status")
    ok = "OK " if statut == "0" else "ERR"
    ligne = (f"{tx.get('date_paris')}  {ok}  {tx.get('direction'):7s}  "
             f"UTID={tx.get('utid') or '-'}  coding={tx.get('coding') or '-'}  "
             f"UPID={tx.get('upid') or '-'}  tx={tx.get('transaction_id')}")
    if statut != "0":
        ligne += f"  [{_label_status(statut)}]"
    if tx.get("validated") == "1":
        ligne += f"  [{_label_validated('1')}]"
    print(ligne, flush=True)


def interroger(sock, from_dt, to_dt, cursor):
    """Toutes les pages disponibles depuis le curseur. Retourne (txs, curseur)."""
    toutes = []
    while True:
        resp = envoyer_et_recevoir(sock, encapsuler_transactions(
            build_xml(from_dt=from_dt, to_dt=to_dt, last_tx_id=cursor)))
        if not resp:
            return toutes, cursor
        not_complete, txs, max_txid = parse_transactions(resp)
        toutes.extend(txs)
        if max_txid is not None:
            cursor = str(max_txid)
        if not (not_complete and max_txid is not None):
            return toutes, cursor
        from_dt = to_dt = None


def main():
    p = argparse.ArgumentParser(description="Ecoute en direct d'un PDA Skidata")
    p.add_argument("pda", help="numero du PDA, ex. 217")
    p.add_argument("--depuis", type=int, default=10)
    p.add_argument("--intervalle", type=float, default=2.0)
    p.add_argument("--tous", action="store_true")
    p.add_argument("--duree", type=int, default=0, help="arret apres N secondes (0 = jamais)")
    args = p.parse_args()
    debut = time.monotonic()

    # "PDA 217", "PDA_217", "PDA-0217"... mais pas "PDA 2170"
    motif = re.compile(rf"PDA\D*0*{re.escape(args.pda)}(?!\d)", re.IGNORECASE)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    from_dt = (now_utc - datetime.timedelta(minutes=args.depuis)).strftime("%Y-%m-%dT%H:%M:%S")
    to_dt = now_utc.strftime("%Y-%m-%dT%H:%M:%S")
    cursor = None
    vus_autres = set()

    print(f"Ecoute du PDA {args.pda} via {HSH_IP}:{HSH_PORT} "
          f"(historique {args.depuis} min, toutes les {args.intervalle}s). Ctrl+C pour arreter.")

    while True:
        try:
            with socket.create_connection((HSH_IP, HSH_PORT), timeout=CONNECT_TIMEOUT) as sock:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(READ_TIMEOUT_TRANSACTIONS)
                while True:
                    txs, cursor = interroger(sock, from_dt, to_dt, cursor)
                    from_dt = to_dt = None
                    for tx in sorted(txs, key=lambda t: t["transaction_id"]
                                     if isinstance(t.get("transaction_id"), int) else 0):
                        nom = _cp_name(tx)
                        if motif.search(nom):
                            _afficher(tx)
                        elif args.tous and nom and nom not in vus_autres:
                            vus_autres.add(nom)
                            print(f"   (autre lecteur vu : {nom})", flush=True)
                    if args.duree and time.monotonic() - debut > args.duree:
                        return
                    time.sleep(args.intervalle)
        except KeyboardInterrupt:
            print("\nArret.")
            return
        except (OSError, ValueError, ConnectionError) as e:
            # Coupure HSH : on reprend au meme curseur, sans perdre de lecture
            print(f"!! connexion HSH perdue ({e}), nouvel essai dans 5 s", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
