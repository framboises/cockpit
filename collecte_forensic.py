#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collecte_forensic.py — Collecte toutes les transactions HSH depuis une date donnee
et les stocke dans la collection handshake_forensic.

Usage:
    python collecte_forensic.py [--from "2026-04-13 06:00"] [--to "2026-04-13 23:59"]

Par defaut : depuis lundi 13 avril 2026 06:00 heure de Paris jusqu'a maintenant.
"""

import socket
import datetime
import argparse
import zoneinfo

from pymongo import MongoClient, UpdateOne
from live_controle import (
    HSH_IP, HSH_PORT, CONNECT_TIMEOUT, READ_TIMEOUT_TRANSACTIONS,
    TZ_PARIS, MAX_TX, OPTION_TRANSACTIONS,
    build_transactions_xml, parse_transactions,
    envoyer_et_recevoir, encapsuler_transactions,
    lire_global,
    MONGO_URI, DB_NAME,
)

mongo_client = MongoClient(MONGO_URI)
db = mongo_client[DB_NAME]
col_forensic = db["handshake_forensic"]


def paris_to_utc(dt_str):
    """Convertit une date string 'YYYY-MM-DD HH:MM' heure Paris en datetime UTC."""
    dt_paris = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    dt_paris = dt_paris.replace(tzinfo=TZ_PARIS)
    return dt_paris.astimezone(datetime.timezone.utc)


def collecter_et_stocker(sock, from_utc, to_utc):
    """Collecte toutes les transactions entre from et to, stocke dans handshake_forensic."""
    from_dt = from_utc.strftime("%Y-%m-%dT%H:%M:%S")
    to_dt = to_utc.strftime("%Y-%m-%dT%H:%M:%S")

    from_paris = from_utc.astimezone(TZ_PARIS).strftime("%d/%m %H:%M")
    to_paris = to_utc.astimezone(TZ_PARIS).strftime("%d/%m %H:%M")
    print(f"Fenetre: {from_paris} -> {to_paris} (heure Paris)")

    sock.settimeout(READ_TIMEOUT_TRANSACTIONS)

    page = 0
    total = 0
    cursor = None

    while True:
        page += 1
        xml_req = build_transactions_xml(
            from_dt=from_dt,
            to_dt=to_dt,
            last_tx_id=cursor,
        )
        frame = encapsuler_transactions(xml_req)
        resp = envoyer_et_recevoir(sock, frame)
        if not resp:
            print(f"  Page {page}: pas de reponse.")
            break

        not_complete, txs, max_txid = parse_transactions(resp)
        nb = len(txs)
        total += nb

        # Stocker dans MongoDB
        if txs:
            ops = []
            for tx in txs:
                tx_id = tx.get("transaction_id")
                if tx_id is None:
                    continue
                ops.append(UpdateOne(
                    {"_id": tx_id},
                    {"$set": tx},
                    upsert=True,
                ))
            if ops:
                col_forensic.bulk_write(ops, ordered=False)

        print(f"  Page {page:03d}: {nb} transactions | Total: {total} | NotComplete={int(not_complete)}")

        if not_complete and max_txid is not None:
            cursor = str(max_txid)
            from_dt = None
            to_dt = None
            import time
            time.sleep(0.1)
        else:
            break

    return total


def collecter_depuis_curseur(sock, last_tx_id):
    """Collecte toutes les transactions apres last_tx_id, stocke dans handshake_forensic."""
    print(f"Reprise depuis LastTransactionId={last_tx_id}")

    sock.settimeout(READ_TIMEOUT_TRANSACTIONS)

    page = 0
    total = 0
    cursor = str(last_tx_id)

    while True:
        page += 1
        xml_req = build_transactions_xml(last_tx_id=cursor)
        frame = encapsuler_transactions(xml_req)
        resp = envoyer_et_recevoir(sock, frame)
        if not resp:
            print(f"  Page {page}: pas de reponse.")
            break

        not_complete, txs, max_txid = parse_transactions(resp)
        nb = len(txs)
        total += nb

        if txs:
            ops = []
            for tx in txs:
                tx_id = tx.get("transaction_id")
                if tx_id is None:
                    continue
                ops.append(UpdateOne(
                    {"_id": tx_id},
                    {"$set": tx},
                    upsert=True,
                ))
            if ops:
                col_forensic.bulk_write(ops, ordered=False)

        print(f"  Page {page:03d}: {nb} transactions | Total: {total} | NotComplete={int(not_complete)}")

        if not_complete and max_txid is not None:
            cursor = str(max_txid)
            import time
            time.sleep(0.1)
        else:
            break

    return total


def main():
    parser = argparse.ArgumentParser(description="Collecte forensic des transactions HSH")
    parser.add_argument("--from", dest="from_dt", type=str, default="2026-04-13 06:00",
                        help="Debut heure Paris (defaut: 2026-04-13 06:00)")
    parser.add_argument("--to", dest="to_dt", type=str, default=None,
                        help="Fin heure Paris (defaut: maintenant)")
    parser.add_argument("--continue", dest="resume", action="store_true",
                        help="Reprendre depuis le dernier transaction_id en base")
    args = parser.parse_args()

    doc = lire_global()
    evenement = doc.get("evenement", "")
    if not evenement:
        print("Aucun evenement configure dans ___GLOBAL___.")
        return

    print(f"Evenement: {evenement}")
    print(f"Connexion HSH: {HSH_IP}:{HSH_PORT}")
    print(f"Collection cible: handshake_forensic")
    print()

    if args.resume:
        # Trouver le max _id dans la collection
        last = col_forensic.find_one(sort=[("_id", -1)], projection={"_id": 1})
        if not last:
            print("Collection vide, impossible de reprendre. Utilisez --from a la place.")
            return
        last_tx_id = last["_id"]
        print(f"Dernier transaction_id en base: {last_tx_id}")
        print()

        try:
            with socket.create_connection((HSH_IP, HSH_PORT), timeout=CONNECT_TIMEOUT) as sock:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                total = collecter_depuis_curseur(sock, last_tx_id)
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        except ConnectionRefusedError:
            print(f"Connexion refusee sur {HSH_IP}:{HSH_PORT}.")
            return
        except socket.timeout:
            print(f"Timeout de connexion vers {HSH_IP}:{HSH_PORT}.")
            return
    else:
        from_utc = paris_to_utc(args.from_dt)
        if args.to_dt:
            to_utc = paris_to_utc(args.to_dt)
        else:
            to_utc = datetime.datetime.now(datetime.timezone.utc)

        try:
            with socket.create_connection((HSH_IP, HSH_PORT), timeout=CONNECT_TIMEOUT) as sock:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                total = collecter_et_stocker(sock, from_utc, to_utc)
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        except ConnectionRefusedError:
            print(f"Connexion refusee sur {HSH_IP}:{HSH_PORT}.")
            return
        except socket.timeout:
            print(f"Timeout de connexion vers {HSH_IP}:{HSH_PORT}.")
            return

    print(f"\n{'='*60}")
    print(f"{total} transactions stockees dans handshake_forensic")
    print(f"Collection: {col_forensic.count_documents({})} documents au total")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
