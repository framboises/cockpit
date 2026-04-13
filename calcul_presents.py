#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calcul_presents.py — Calcule les presents sur site a un instant donne
en rejouant toutes les transactions depuis le debut de la journee.

Utilise la collection handshake_forensic (ou une archive) pour
comptabiliser entrees - sorties dans ENCEINTE GENERALE, ventile
par categorie (personne, vehicule, enfant, accredite).

Usage:
    python calcul_presents.py

Le script demande la date/heure en prompt (heure de Paris).
"""

import datetime
import zoneinfo
import os
import sys

from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_TITAN_ENV = os.getenv("TITAN_ENV", "dev").strip().lower()
DB_NAME = "titan" if _TITAN_ENV in {"prod", "production"} else "titan_dev"

TZ_PARIS = zoneinfo.ZoneInfo("Europe/Paris")

mongo_client = MongoClient(MONGO_URI)
db = mongo_client[DB_NAME]


def charger_cache_titres():
    cache = {}
    for doc in db["access_barcodes_utids"].find({}, {"utid": 1, "title": 1}):
        utid = doc.get("utid")
        if utid:
            cache[utid] = doc.get("title", "")
    return cache


def categoriser_scan(utid, cache_titres, upid=None):
    if upid and upid.endswith("-ACCRED"):
        return "accredite"
    if utid and utid.startswith("EWC"):
        return "accredite"
    if utid and utid in cache_titres:
        title = cache_titres[utid]
        if "Bracelet Enfant" in title:
            return "enfant"
        return "vehicule"
    return "personne"


def lister_collections_forensic():
    """Liste les collections disponibles contenant des transactions."""
    cols = []
    for name in db.list_collection_names():
        if name == "handshake_forensic" or name.startswith("hsh_archive_tx_"):
            count = db[name].estimated_document_count()
            if count > 0:
                cols.append((name, count))
    cols.sort()
    return cols


def main():
    print("=" * 60)
    print("  CALCUL DES PRESENTS SUR SITE")
    print("=" * 60)
    print()

    # Source : collections MongoDB ou fichier JSON
    json_source = None
    cols = lister_collections_forensic()

    # Chercher aussi un fichier JSON forensic
    json_path = os.path.join(os.path.dirname(__file__), "uploads", "titan.handshake_forensic.json")
    has_json = os.path.exists(json_path)

    sources = []
    for name, count in cols:
        sources.append(("mongo", name, count))
    if has_json:
        sources.append(("json", json_path, None))

    if not sources:
        print("Aucune source de transactions trouvee.")
        return

    print("Sources disponibles :")
    for i, (stype, name, count) in enumerate(sources, 1):
        if stype == "mongo":
            print(f"  {i}. [MongoDB] {name} ({count:,} documents)")
        else:
            print(f"  {i}. [Fichier] {os.path.basename(name)}")
    print()

    if len(sources) == 1:
        selected = sources[0]
        label = selected[1] if selected[0] == "mongo" else os.path.basename(selected[1])
        print(f"Source selectionnee : {label}")
    else:
        choix = input("Numero de la source [1] : ").strip()
        if not choix:
            choix = "1"
        try:
            idx = int(choix) - 1
            selected = sources[idx]
        except (ValueError, IndexError):
            print("Choix invalide.")
            return

    col = None
    if selected[0] == "mongo":
        col = db[selected[1]]
    else:
        import json as jsonlib
        print(f"Chargement de {os.path.basename(selected[1])}...")
        with open(selected[1], "r", encoding="utf-8") as f:
            json_source = jsonlib.load(f)
        print(f"  {len(json_source):,} transactions chargees.")
    print()

    # Demander la date/heure de debut
    now_paris = datetime.datetime.now(TZ_PARIS)
    print("Depuis quelle date/heure compter les transactions ?")
    print("Format : YYYY-MM-DD HH:MM (heure de Paris)")
    print("Exemple : 2026-04-13 06:00")
    print(f"Vide    = debut de la journee ({now_paris.strftime('%Y-%m-%d')} 00:00)")
    print()
    dt_input = input("Depuis : ").strip()

    if not dt_input:
        debut_paris = now_paris.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        try:
            debut_paris = datetime.datetime.strptime(dt_input, "%Y-%m-%d %H:%M")
            debut_paris = debut_paris.replace(tzinfo=TZ_PARIS)
        except ValueError:
            print("Format invalide. Utilisez YYYY-MM-DD HH:MM")
            return

    debut_str = debut_paris.strftime("%Y-%m-%d %H:%M:%S")
    cible_str = now_paris.strftime("%Y-%m-%d %H:%M:%S")

    print()
    print(f"Calcul des presents dans ENCEINTE GENERALE")
    print(f"  Du    : {debut_str} (Paris)")
    print(f"  Au    : {cible_str} (Paris) — maintenant")
    print()

    # Charger le cache des titres
    cache_titres = charger_cache_titres()
    print(f"Cache titres : {len(cache_titres)} UTID charges")
    print()

    # Requeter les transactions OK (status=0) dans ENCEINTE GENERALE
    # jusqu'a l'heure cible
    total_tx = 0
    compteurs = {
        "entrees_personne": 0, "sorties_personne": 0,
        "entrees_vehicule": 0, "sorties_vehicule": 0,
        "entrees_enfant": 0, "sorties_enfant": 0,
        "entrees_accredite": 0, "sorties_accredite": 0,
    }
    par_gate = {}
    par_titre_veh = {}

    if col is not None:
        # Source MongoDB
        filtre = {
            "status": "0",
            "area.Name": "ENCEINTE GENERALE",
            "date_paris": {"$gte": debut_str, "$lte": cible_str},
        }
        source_iter = col.find(filtre).sort("date_paris", 1)
    else:
        # Source JSON — filtrer en Python
        def json_filter():
            for tx in json_source:
                if tx.get("status") != "0":
                    continue
                area = tx.get("area") or {}
                if area.get("Name") != "ENCEINTE GENERALE":
                    continue
                dp = tx.get("date_paris", "")
                if dp < debut_str or dp > cible_str:
                    continue
                yield tx
        source_iter = json_filter()

    for tx in source_iter:
        total_tx += 1
        direction = tx.get("direction", "")
        utid = tx.get("utid")
        upid = tx.get("upid")
        cat = categoriser_scan(utid, cache_titres, upid)

        if direction == "Entree":
            compteurs[f"entrees_{cat}"] += 1
        elif direction == "Sortie":
            compteurs[f"sorties_{cat}"] += 1

        # Par gate
        gate_name = (tx.get("gate") or {}).get("Name", "Inconnu")
        if gate_name not in par_gate:
            par_gate[gate_name] = {"entrees": 0, "sorties": 0}
        if direction == "Entree":
            par_gate[gate_name]["entrees"] += 1
        elif direction == "Sortie":
            par_gate[gate_name]["sorties"] += 1

        # Detail par titre vehicule
        if cat == "vehicule" and utid and utid in cache_titres:
            titre = cache_titres[utid]
            if titre not in par_titre_veh:
                par_titre_veh[titre] = {"entrees": 0, "sorties": 0}
            if direction == "Entree":
                par_titre_veh[titre]["entrees"] += 1
            elif direction == "Sortie":
                par_titre_veh[titre]["sorties"] += 1

    # Calculs
    p_pers = compteurs["entrees_personne"] - compteurs["sorties_personne"]
    p_veh = compteurs["entrees_vehicule"] - compteurs["sorties_vehicule"]
    p_enf = compteurs["entrees_enfant"] - compteurs["sorties_enfant"]
    p_acc = compteurs["entrees_accredite"] - compteurs["sorties_accredite"]
    p_total_skidata = p_pers + p_veh + p_enf + p_acc
    p_personnes = p_pers + p_enf + p_acc  # humains = tout sauf vehicules

    # Affichage
    print(f"{'=' * 60}")
    print(f"  RESULTATS AU {cible_str} (Paris)")
    print(f"{'=' * 60}")
    print(f"  Transactions OK analysees : {total_tx:,}")
    print()
    print(f"  --- PRESENTS SUR SITE (ENCEINTE GENERALE) ---")
    print()
    print(f"  Total compteur (comme Skidata)  : {p_total_skidata:>8,}")
    print(f"    - Vehicules presents          : {p_veh:>8,}")
    print(f"  = Personnes sur site            : {p_personnes:>8,}")
    print(f"      dont accredites             : {p_acc:>8,}")
    print(f"      dont enfants                : {p_enf:>8,}")
    print()
    print(f"  CORRECTION SUGGEREE = {p_total_skidata} - {p_personnes} = {p_veh} (vehicules a deduire)")
    print()
    print(f"  --- DETAIL ENTREES / SORTIES ---")
    print(f"  {'Categorie':<14} {'Entrees':>8} {'Sorties':>8} {'Presents':>8}")
    print(f"  {'-'*14} {'-'*8} {'-'*8} {'-'*8}")
    for cat_label, cat_key in [("Personnes", "personne"), ("Vehicules", "vehicule"),
                                ("Enfants", "enfant"), ("Accredites", "accredite")]:
        e = compteurs[f"entrees_{cat_key}"]
        s = compteurs[f"sorties_{cat_key}"]
        print(f"  {cat_label:<14} {e:>8,} {s:>8,} {e - s:>8,}")
    print()

    print(f"  --- DEBIT PAR PORTE ---")
    print(f"  {'Porte':<25} {'Entrees':>8} {'Sorties':>8} {'Presents':>8}")
    print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8}")
    for gate_name, g in sorted(par_gate.items(), key=lambda x: x[1]["entrees"], reverse=True):
        p = g["entrees"] - g["sorties"]
        print(f"  {gate_name:<25} {g['entrees']:>8,} {g['sorties']:>8,} {p:>8,}")
    print()

    if par_titre_veh:
        print(f"  --- DETAIL VEHICULES PAR TITRE ---")
        print(f"  {'Titre':<25} {'Entrees':>8} {'Sorties':>8} {'Presents':>8}")
        print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8}")
        for titre, v in sorted(par_titre_veh.items(), key=lambda x: x[1]["entrees"], reverse=True):
            p = v["entrees"] - v["sorties"]
            print(f"  {titre:<25} {v['entrees']:>8,} {v['sorties']:>8,} {p:>8,}")
        print()

    print(f"{'=' * 60}")


if __name__ == "__main__":
    try:
        main()
    finally:
        mongo_client.close()
