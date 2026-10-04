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

Perimetre "Site - visites libres" (cle `site`, 02/10/2026) : amorce une
seule fois depuis la ligne de commande (aucun identifiant dans le code) :
    python scripts/musee_migrate_config.py --site-checkpoints 749,750,751 [--site-marge 30]
Les noms viennent des archives hsh_archive_structure_* ; un id inconnu est
refuse. IDEMPOTENT : un document qui porte deja `site` n'est pas touche
(la suite se regle dans l'onglet Musee).
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


def _noms_archives(db, ids):
    """{id: (type, nom)} depuis toutes les archives hsh_archive_structure_*."""
    out = {}
    variantes = list(ids) + [int(i) for i in ids if i.isdigit()]
    for name in db.list_collection_names():
        if not name.startswith("hsh_archive_structure_"):
            continue
        for d in db[name].find({"location_id": {"$in": variantes}}):
            out[str(d.get("location_id"))] = (d.get("location_type"), d.get("location_name") or "")
    return out


def seed_site(db, checkpoint_ids, marge=M.DEFAUT_SITE_MARGE_MIN, dry_run=False, out=print):
    """Ajoute la cle `site` (visites libres) au document musee. Idempotent."""
    col = db["cockpit_settings"]
    doc = col.find_one({"_id": M.SETTINGS_ID})
    if doc is None:
        out("Aucun document cockpit_settings.musee : configurer d'abord le musee.")
        return "absent"
    if "site" in doc:
        out("Cle site deja presente : rien a faire.\n" +
            json.dumps(doc["site"], default=str, indent=1, ensure_ascii=False))
        return "deja"
    ids = [i.strip() for i in checkpoint_ids if i.strip()]
    if not ids or not all(i.isdigit() for i in ids):
        out("Identifiants de checkpoints numeriques attendus : %r" % checkpoint_ids)
        return "invalide"
    noms = _noms_archives(db, ids)
    inconnus = [i for i in ids if noms.get(i, (None,))[0] != "Checkpoint"]
    if inconnus:
        out("Checkpoint(s) absent(s) des archives Handshake : %s" % ", ".join(inconnus))
        return "invalide"
    site = {"enabled": True, "marge_min": int(marge),
            "checkpoints": [{"id": i, "nom": noms[i][1] or i, "libelle": "", "inclus": True,
                             "sens": "mixte"} for i in ids]}
    if not 0 <= int(marge) <= M.SITE_MARGE_MAX:
        out("Marge de 0 a %d minutes attendue : %r" % (M.SITE_MARGE_MAX, marge))
        return "invalide"
    out("AVANT : cle site absente (document schema %s)" % doc.get("schema"))
    out("APRES%s :\n" % (" (dry-run, non ecrit)" if dry_run else "") +
        json.dumps({"site": site}, indent=1, ensure_ascii=False))
    if dry_run:
        return "dry-run"
    col.update_one({"_id": M.SETTINGS_ID},
                   {"$set": {"site": site, "migration_site": {"date": M.now_utc()}}})
    stored = col.find_one({"_id": M.SETTINGS_ID})
    out("APRES (relu) :\n" + json.dumps(stored.get("site"), default=str, indent=1, ensure_ascii=False))
    after = M.normalize_config(stored)
    out("Config de travail : site enabled=%s configure=%s, %d checkpoint(s), marge %d min"
        % (after["site"]["enabled"], after["site"]["configure"],
           len(after["site"]["locations"]), after["site"]["marge_min"]))
    return "migre"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default="")
    ap.add_argument("--site-checkpoints", default="",
                    help="ids des checkpoints du perimetre visites libres (amorce, une fois)")
    ap.add_argument("--site-marge", type=int, default=M.DEFAUT_SITE_MARGE_MIN)
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
        if args.site_checkpoints:
            print("Site visites libres : %s" % seed_site(
                client[name], args.site_checkpoints.split(","), marge=args.site_marge,
                dry_run=args.dry_run))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
