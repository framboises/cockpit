#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyse_scans_pda67.py — Collecte un echantillon de transactions du checkpoint PDA 67
pour analyser les champs UTID/coding et distinguer vehicules vs personnes.

Usage:
    python analyse_scans_pda67.py [--heures N] [--max N] [--checkpoint NOM]

Options:
    --heures N       Nombre d'heures a remonter (defaut: 6)
    --max N          Nombre max de transactions a collecter (defaut: 200)
    --checkpoint NOM Filtre sur le nom du checkpoint (defaut: PDA 67)

Sortie: scans_pda67.json
"""

import socket
import json
import sys
import datetime
import argparse

# Reutiliser les fonctions de live_controle.py
from live_controle import (
    HSH_IP, HSH_PORT, CONNECT_TIMEOUT, READ_TIMEOUT_TRANSACTIONS,
    TZ_PARIS, MAX_TX, OPTION_TRANSACTIONS,
    build_transactions_xml, parse_transactions,
    envoyer_et_recevoir, encapsuler_transactions,
    lire_global,
)


def collecter_transactions(sock, heures, max_tx, filtre_cp):
    """Collecte les transactions des N dernieres heures, filtre par checkpoint."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    from_utc = now_utc - datetime.timedelta(hours=heures)
    from_dt = from_utc.strftime("%Y-%m-%dT%H:%M:%S")
    to_dt = now_utc.strftime("%Y-%m-%dT%H:%M:%S")

    from_paris = from_utc.astimezone(TZ_PARIS).strftime("%H:%M")
    to_paris = now_utc.astimezone(TZ_PARIS).strftime("%H:%M")
    print(f"Fenetre: {from_paris} -> {to_paris} (heure Paris), soit {heures}h")
    if filtre_cp:
        print(f"Filtre checkpoint: nom contient '{filtre_cp}'")

    sock.settimeout(READ_TIMEOUT_TRANSACTIONS)

    toutes = []
    page = 0
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

        # Filtrer par checkpoint si demande
        if filtre_cp:
            filtrees = []
            for tx in txs:
                cp = tx.get("checkpoint")
                if cp:
                    cp_name = cp.get("Name") or cp.get("name") or ""
                    if filtre_cp.lower() in cp_name.lower():
                        filtrees.append(tx)
            print(f"  Page {page:03d}: {nb} transactions, {len(filtrees)} matchent '{filtre_cp}' | NotComplete={int(not_complete)}")
            toutes.extend(filtrees)
        else:
            print(f"  Page {page:03d}: {nb} transactions | NotComplete={int(not_complete)}")
            toutes.extend(txs)

        if len(toutes) >= max_tx:
            print(f"  Atteint {len(toutes)} transactions, arret.")
            break

        if not_complete and max_txid is not None:
            cursor = str(max_txid)
            from_dt = None
            to_dt = None
            import time
            time.sleep(0.1)
        else:
            break

    return toutes[:max_tx]


def main():
    parser = argparse.ArgumentParser(description="Collecte de transactions HSH pour analyse")
    parser.add_argument("--heures", type=int, default=6, help="Heures a remonter (defaut: 6)")
    parser.add_argument("--max", type=int, default=200, help="Nombre max de transactions (defaut: 200)")
    parser.add_argument("--checkpoint", type=str, default="PDA.67", help="Filtre nom checkpoint (defaut: PDA.67)")
    args = parser.parse_args()

    # Verifier que le live controle est configure
    doc = lire_global()
    evenement = doc.get("evenement", "")
    if not evenement:
        print("Aucun evenement configure dans ___GLOBAL___. Configurez le live controle d'abord.")
        return

    print(f"Evenement: {evenement}")
    print(f"Connexion HSH: {HSH_IP}:{HSH_PORT}")

    try:
        with socket.create_connection((HSH_IP, HSH_PORT), timeout=CONNECT_TIMEOUT) as sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            txs = collecter_transactions(sock, args.heures, args.max, args.checkpoint)

            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    except ConnectionRefusedError:
        print(f"Connexion refusee sur {HSH_IP}:{HSH_PORT}. Le serveur HSH est-il accessible ?")
        return
    except socket.timeout:
        print(f"Timeout de connexion vers {HSH_IP}:{HSH_PORT}.")
        return

    if not txs:
        print("\nAucune transaction trouvee. Essayez d'augmenter --heures ou de verifier le nom du checkpoint.")
        return

    # Sauvegarder en JSON
    output_file = "scans_pda67.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(txs, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n{'='*60}")
    print(f"{len(txs)} transactions sauvegardees dans {output_file}")
    print(f"{'='*60}")

    # Resume rapide
    utids = [tx.get("utid") or "N/A" for tx in txs]
    codings = set(tx.get("coding") or "N/A" for tx in txs)
    directions = {}
    for tx in txs:
        d = tx.get("direction", "Inconnu")
        directions[d] = directions.get(d, 0) + 1

    # Detecter les patterns UTID
    prefixes = {}
    for u in utids:
        if u == "N/A":
            p = "N/A"
        elif len(u) > 10:
            p = u[:10] + "..."
        else:
            p = u
        prefixes[p] = prefixes.get(p, 0) + 1

    print(f"\nCodings distincts: {codings}")
    print(f"Directions: {directions}")
    print(f"\nTop 20 prefixes UTID:")
    for prefix, count in sorted(prefixes.items(), key=lambda x: -x[1])[:20]:
        print(f"  {prefix:30s} x{count}")

    # Compter les ACO_YYYY_
    import re
    vehicules = sum(1 for u in utids if re.match(r"^ACO_\d{4}_", u))
    personnes = len(utids) - vehicules
    print(f"\nResume: {personnes} personnes, {vehicules} vehicules (pattern ACO_YYYY_)")


if __name__ == "__main__":
    main()
