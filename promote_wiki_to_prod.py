# -*- coding: utf-8 -*-
"""
Promotion du Wiki des procédures : copie les collections validées
de titan_dev vers titan (même instance Mongo).

Ne touche QUE :
  - cockpit_wiki_categories
  - cockpit_wiki_procedures

Upsert par clé métier (key / code) : ré-exécutable sans créer de doublon.
N'écrase pas created_at / created_by / version / status des fiches déjà
présentes en prod (préserve un éventuel travail d'édition côté prod).

Usage :
  python promote_wiki_to_prod.py            # dry-run : affiche ce qui serait fait
  python promote_wiki_to_prod.py --apply    # exécute réellement
  python promote_wiki_to_prod.py --apply --overwrite   # écrase tout (statut/version compris)
"""
import os
import argparse
from datetime import datetime, timezone
from pymongo import MongoClient, ASCENDING

SRC_DB = "titan_dev"
DST_DB = "titan"
COLLECTIONS = ["cockpit_wiki_categories", "cockpit_wiki_procedures"]
KEY = {"cockpit_wiki_categories": "key", "cockpit_wiki_procedures": "code"}
# champs de prod à ne pas écraser lors d'une mise à jour (hors --overwrite)
PRESERVE = {"cockpit_wiki_procedures": ("created_at", "created_by", "version", "status")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mongo", default=os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
    ap.add_argument("--apply", action="store_true", help="exécute réellement (sinon dry-run)")
    ap.add_argument("--overwrite", action="store_true",
                    help="écrase aussi statut/version/created_* en prod")
    args = ap.parse_args()

    client = MongoClient(args.mongo)
    src = client[SRC_DB]
    dst = client[DST_DB]
    now = datetime.now(timezone.utc)

    print(f"Source : {SRC_DB}   ->   Destination : {DST_DB}")
    print("Mode   :", "APPLY" if args.apply else "DRY-RUN (aucune écriture)",
          "+ OVERWRITE" if args.overwrite else "")
    print("-" * 60)

    for coll in COLLECTIONS:
        key = KEY[coll]
        docs = list(src[coll].find())
        if args.apply:
            dst[coll].create_index(key, unique=True)
            if coll == "cockpit_wiki_procedures":
                dst[coll].create_index([("dom", ASCENDING)])
                dst[coll].create_index([("status", ASCENDING)])
        n_new = n_upd = 0
        for d in docs:
            d.pop("_id", None)
            kval = d.get(key)
            existing = dst[coll].find_one({key: kval})
            if existing:
                patch = dict(d)
                if not args.overwrite:
                    for f in PRESERVE.get(coll, ()):
                        patch.pop(f, None)
                patch["updated_at"] = now
                if args.apply:
                    dst[coll].update_one({key: kval}, {"$set": patch})
                n_upd += 1
            else:
                if args.apply:
                    dst[coll].insert_one(d)
                n_new += 1
        total = dst[coll].count_documents({}) if args.apply else "?"
        print(f"{coll:32s} : {n_new} créés, {n_upd} mis à jour  (total prod : {total})")

    if not args.apply:
        print("-" * 60)
        print("DRY-RUN terminé. Relance avec --apply pour écrire dans titan.")


if __name__ == "__main__":
    main()
