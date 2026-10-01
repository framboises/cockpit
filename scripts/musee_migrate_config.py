"""Migration de cockpit_settings {_id: "musee"} du schema 1 au schema 2.

Schema 1 : ouverture / fermeture / jours_fermes / area_id / locations a plat.
Schema 2 : horaires {ouverture, fermeture, semaine, exceptions}, area,
gates, checkpoints (inclus, mobile, libelle), releve_perime_min,
statuts_passage (cf. musee.py).

IDEMPOTENTE : un document deja au schema 2 n'est pas touche. Aucune valeur
perdue : les champs v1 sont recopies sous migration.v1. Ne cree RIEN si le
document n'existe pas (il se cree depuis l'onglet Musee de /live-controle).

Usage :
    python scripts/musee_migrate_config.py            # base selon TITAN_ENV
    python scripts/musee_migrate_config.py --dry-run  # affiche, n'ecrit rien
    python scripts/musee_migrate_config.py --db titan
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pymongo import MongoClient  # noqa: E402

import musee as M  # noqa: E402

V1_KEYS = ("ouverture", "fermeture", "jours_fermes", "area_id", "locations")


def migrate(db, dry_run=False, out=print):
    col = db["cockpit_settings"]
    doc = col.find_one({"_id": M.SETTINGS_ID})
    if doc is None:
        out("Aucun document cockpit_settings.musee : rien a migrer (a creer depuis l'onglet Musee).")
        return "absent"
    out("AVANT :\n" + json.dumps(doc, default=str, indent=1, ensure_ascii=False))
    if doc.get("schema") == M.SCHEMA:
        out("Deja au schema %d : rien a faire." % M.SCHEMA)
        return "deja"
    new = M.migrate_legacy(doc)
    new.pop("_id", None)
    new["migration"] = {"depuis": 1, "date": M.now_utc(),
                        "v1": {k: doc[k] for k in V1_KEYS if k in doc}}
    unset = {k: "" for k in V1_KEYS if k in doc}
    if dry_run:
        out("APRES (dry-run, non ecrit) :\n" + json.dumps(new, default=str, indent=1, ensure_ascii=False))
        return "dry-run"
    upd = {"$set": new}
    if unset:
        upd["$unset"] = unset
    # Filtre sur l'absence de schema : deux lancements concurrents ne
    # migrent qu'une fois.
    res = col.update_one({"_id": M.SETTINGS_ID, "schema": {"$exists": False}}, upd)
    after = col.find_one({"_id": M.SETTINGS_ID})
    out("APRES :\n" + json.dumps(after, default=str, indent=1, ensure_ascii=False))
    cfg = M.normalize_config(after)
    out("Config de travail : configure=%s, Area %s, %d location(s) relevee(s)"
        % (cfg["configure"], cfg["area_id"], len(cfg["locations"])))
    return "migre" if res.modified_count else "concurrent"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default="")
    args = ap.parse_args(argv)
    name = args.db.strip()
    if not name:
        env = os.getenv("TITAN_ENV", "dev").strip().lower()
        name = "titan" if env in {"prod", "production"} else "titan_dev"
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"),
                         serverSelectionTimeoutMS=10000)
    try:
        print("Base : %s" % name)
        print("Resultat : %s" % migrate(client[name], dry_run=args.dry_run))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
