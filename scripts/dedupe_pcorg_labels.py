#!/usr/bin/env python3
"""
dedupe_pcorg_labels.py - Une seule orthographe par libelle de la main courante.

Prysm a les deux orthographes de plusieurs libelles ("Signaletique" /
"Signalétique", "Electricite" / "Electricité"...) et la synchro les recopiait
toutes dans pcorg_config.pcorg_lists : doublons dans les menus, statistiques
coupees en deux. La synchro ramene desormais chaque valeur au libelle de la
liste (pcorg_labels.Canon) ; ce script fait le rattrapage, une fois :

  1. fusionne les doublons de pcorg_lists (sous-classifications par categorie,
     intervenants, services) : libelle le plus accentue, puis le plus utilise ;
  2. corrige les fiches deja en base : content_category (sous-classification,
     intervenants, moyens engages, service contacte) et xml_struct
     (classification.sous, intervenants, service_contacte).

Ne touche ni a la chronologie, ni a cockpit_owned, ni au XML brut
(content_category_xml). Ecriture gardee par `cockpit_rev`.

Usage :
    python scripts/dedupe_pcorg_labels.py            # simulation (defaut)
    python scripts/dedupe_pcorg_labels.py --apply    # ecriture
    python scripts/dedupe_pcorg_labels.py --db titan
"""
import argparse
import collections
import os
import sys

from pymongo import MongoClient, UpdateOne

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pcorg_labels as PL  # noqa: E402

_ENV = os.getenv("TITAN_ENV", "dev").strip().lower()
DEFAULT_DB = "titan" if _ENV in {"prod", "production"} else "titan_dev"


def _usage(col):
    """Nombre de fiches par libelle : {cat: Counter} pour les sous-classifications,
    Counter commun intervenants / moyens, Counter services."""
    sc = collections.defaultdict(collections.Counter)
    people, services = collections.Counter(), collections.Counter()
    proj = {"category": 1, "content_category": 1}
    for d in col.find({"content_category": {"$type": "object"}}, proj):
        cc = d.get("content_category") or {}
        v = cc.get(PL.SC_FIELD)
        if isinstance(v, str) and v:
            sc[d.get("category")][v] += 1
        for f in PL.PERSON_FIELDS:
            if isinstance(cc.get(f), str) and cc[f]:
                people[cc[f]] += 1
        for f in PL.SERVICE_FIELDS:
            if isinstance(cc.get(f), str) and cc[f]:
                services[cc[f]] += 1
    return sc, people, services


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ecrire (sinon simulation)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--mongo", default=os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
    args = ap.parse_args()

    db = MongoClient(args.mongo)[args.db]
    col, cfg_col = db["pcorg"], db["pcorg_config"]
    print(f"Base {args.db} - mode {'ECRITURE' if args.apply else 'SIMULATION'}")

    cfg = cfg_col.find_one({"_id": "pcorg_lists"}) or {}
    sc_use, people_use, svc_use = _usage(col)

    # 1. Listes
    new_sc, n_merged = {}, 0
    for cat, items in (cfg.get("sous_classifications") or {}).items():
        new_sc[cat], renamed = PL.dedupe_items(items, sc_use.get(cat))
        for old, keep in renamed.items():
            print(f"  [{cat}] {old!r} -> {keep!r}")
        n_merged += len(renamed)
    lists = {"sous_classifications": new_sc}
    for name, use in (("intervenants", people_use), ("services", svc_use)):
        lists[name], renamed = PL.dedupe_items(cfg.get(name) or [], use)
        for old, keep in renamed.items():
            print(f"  [{name}] {old!r} -> {keep!r}")
        n_merged += len(renamed)
    print(f"Listes : {n_merged} doublon(s) fusionne(s)")
    if args.apply and n_merged:
        cfg_col.update_one({"_id": "pcorg_lists"}, {"$set": lists})

    # 2. Fiches
    canon = PL.Canon(dict(cfg, **lists))
    ops, per_value = [], collections.Counter()
    proj = {"category": 1, "content_category": 1, "xml_struct": 1, "cockpit_rev": 1}
    for d in col.find({"content_category": {"$type": "object"}}, proj):
        cc = dict(d.get("content_category") or {})
        before = dict(cc)
        changed = canon.apply_cc(d.get("category"), cc)
        if not changed:
            continue
        sets = {}
        for f in changed:
            sets[f"content_category.{f}"] = cc[f]
            per_value[(f if f == PL.SC_FIELD else "autres", str(before[f]), str(cc[f]))] += 1
        xs = d.get("xml_struct") or {}
        cl = xs.get("classification") or {}
        if PL.SC_FIELD in changed and isinstance(cl.get("sous"), str):
            sets["xml_struct.classification.sous"] = cc[PL.SC_FIELD]
        if isinstance(xs.get("intervenants"), list):
            fixed = [canon.people.get(PL.key(v), v) if isinstance(v, str) else v
                     for v in xs["intervenants"]]
            if fixed != xs["intervenants"]:
                sets["xml_struct.intervenants"] = fixed
        if isinstance(xs.get("service_contacte"), str) and "service_contacte" in changed:
            sets["xml_struct.service_contacte"] = cc["service_contacte"]
        ops.append(UpdateOne({"_id": d["_id"], "cockpit_rev": d.get("cockpit_rev")}, {"$set": sets}))

    for (f, old, new), n in sorted(per_value.items()):
        print(f"  {f:20} {old!r} -> {new!r} : {n} fiche(s)")
    print(f"Fiches a corriger : {len(ops)}")
    if args.apply and ops:
        done = 0
        for i in range(0, len(ops), 500):
            done += col.bulk_write(ops[i:i + 500], ordered=False).modified_count
        print(f"Fiches corrigees : {done}")


if __name__ == "__main__":
    main()
