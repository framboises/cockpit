#!/usr/bin/env python3
"""
migrate_pcorg_history.py - Rattrapage de la chronologie des fiches SQL (pcorg).

La synchro SQL fusionne desormais la chronologie Prysm avec les entrees
Cockpit / tablette (pcorg_history.merge_sync_doc), mais seulement pour les
lignes que Prysm reecrit. Ce script applique le meme traitement, une fois, a
toutes les fiches SQL deja en base :

  - re-parse le champ `comment` avec le parseur corrige (l'ancien perdait
    l'entree finale sans saut de ligne : ~2 900 fiches) ;
  - marque chaque entree de son origine (sql / cockpit / field), ce qui
    permet a la synchro de conserver les entrees locales ;
  - ne touche NI au champ `comment`, NI au statut, NI aux autres champs.

Ecriture gardee par `cockpit_rev` (une fiche modifiee pendant le passage est
laissee pour le passage suivant).

Usage :
    python scripts/migrate_pcorg_history.py            # simulation (defaut)
    python scripts/migrate_pcorg_history.py --apply    # ecriture
    python scripts/migrate_pcorg_history.py --db titan_dev
"""
import argparse
import os
import sys

from pymongo import MongoClient, UpdateOne

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pcorg_history as H  # noqa: E402

_ENV = os.getenv("TITAN_ENV", "dev").strip().lower()
DEFAULT_DB = "titan" if _ENV in {"prod", "production"} else "titan_dev"


def _norm(h):
    return [(H.entry_key(e), (e.get("text") or "").strip(), e.get("origin")) for e in h or []]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ecrire (sinon simulation)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--mongo", default=os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
    args = ap.parse_args()

    col = MongoClient(args.mongo)[args.db]["pcorg"]
    print(f"Base {args.db} - mode {'ECRITURE' if args.apply else 'SIMULATION'}")

    scanned = changed = gained = local_kept = 0
    ops = []
    cursor = col.find({"sql_id": {"$ne": None}},
                      {"comment": 1, "comment_history": 1, "cockpit_rev": 1})
    for d in cursor:
        scanned += 1
        existing = d.get("comment_history") or []
        merged = H.merge_history(existing, H.parse_comment(d.get("comment"), origin="sql"))
        if _norm(merged) == _norm(existing):
            continue
        changed += 1
        gained += max(0, len(merged) - len(existing))
        local_kept += sum(1 for e in merged if e.get("origin") != "sql")
        ops.append(UpdateOne({"_id": d["_id"], "cockpit_rev": d.get("cockpit_rev")},
                             {"$set": {"comment_history": merged}}))
        if args.apply and len(ops) >= 500:
            col.bulk_write(ops, ordered=False)
            ops = []
    if args.apply and ops:
        col.bulk_write(ops, ordered=False)

    print(f"Fiches SQL examinees : {scanned}")
    print(f"Fiches a mettre a jour : {changed}")
    print(f"Entrees recuperees (parseur corrige) : {gained}")
    print(f"Entrees Cockpit/tablette conservees dans ces fiches : {local_kept}")
    if not args.apply:
        print("Simulation : rien n'a ete ecrit. Relancer avec --apply.")


if __name__ == "__main__":
    main()
