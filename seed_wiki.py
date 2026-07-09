# -*- coding: utf-8 -*-
"""
Seed du Wiki des procédures PC Orga dans Cockpit.

Importe les catégories et les 29 fiches procédures dans :
  - cockpit_wiki_categories : { key, label, color, order }
  - cockpit_wiki_procedures : { code, titre, dom, situation, questions[], acteurs,
        conduite[], consigner, pieges, souscas[], details[], flow[],
        status, version, created_at, updated_at, created_by, updated_by }

Source : le JSON embarqué dans le wiki statique déjà produit
(wiki_procedures_pcorg.html, tableau `const P=[...]`), qui contient déjà
la donnée finale validée (situation, questions, cheminement, réflexes terrain...).

Usage :
  CODING=true python seed_wiki.py                # -> titan_dev (défaut dev)
  TITAN_ENV=prod python seed_wiki.py             # -> titan
  python seed_wiki.py --source /chemin/wiki.html # source explicite

Idempotent : upsert par `code` (procédures) et `key` (catégories). Ne touche
qu'à ces deux collections.
"""
import os, re, json, argparse
from datetime import datetime, timezone
from pymongo import MongoClient, ASCENDING

DEFAULT_SOURCE = "/Users/framboises/Dropbox/ACO/Formation/PCORG/wiki_procedures_pcorg.html"
CAT_ORDER = ["secours", "securite", "technique", "flux", "acces"]


def load_procedures(path):
    html = open(path, encoding="utf-8").read()
    m = re.search(r"const P=(\[.*?\]);", html, re.S)
    if not m:
        raise SystemExit("Impossible de trouver le tableau `const P=[...]` dans " + path)
    return json.loads(m.group(1))


def build_categories(procs):
    seen = {}
    for p in procs:
        seen.setdefault(p["dom"], {"key": p["dom"], "label": p["domlabel"], "color": p["color"]})
    cats = list(seen.values())
    cats.sort(key=lambda c: (CAT_ORDER.index(c["key"]) if c["key"] in CAT_ORDER else 99, c["key"]))
    for i, c in enumerate(cats):
        c["order"] = i
    return cats


def build_doc(p, now):
    return {
        "code": p["code"],
        "titre": p["titre"],
        "dom": p["dom"],
        "situation": p.get("situation", ""),
        "questions": p.get("questions", []),
        "acteurs": p.get("acteurs", ""),
        "conduite": p.get("conduite", []),
        "consigner": p.get("consigner", ""),
        "pieges": p.get("pieges", ""),
        "souscas": p.get("souscas", []),
        "details": p.get("details", []),
        "flow": p.get("flow", []),  # noeuds {k,t,y?,n?}
        "status": "published",
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "created_by": "seed",
        "updated_by": "seed",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--mongo", default=os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
    args = ap.parse_args()

    is_prod = os.getenv("TITAN_ENV", "dev").strip().lower() in {"prod", "production"}
    db_name = "titan" if is_prod else "titan_dev"
    db = MongoClient(args.mongo)[db_name]
    col_cat = db["cockpit_wiki_categories"]
    col_pro = db["cockpit_wiki_procedures"]

    procs = load_procedures(args.source)
    cats = build_categories(procs)
    now = datetime.now(timezone.utc)

    # index
    col_pro.create_index("code", unique=True)
    col_pro.create_index([("dom", ASCENDING)])
    col_pro.create_index([("status", ASCENDING)])
    col_cat.create_index("key", unique=True)

    # catégories
    for c in cats:
        col_cat.update_one({"key": c["key"]},
                           {"$set": {"label": c["label"], "color": c["color"], "order": c["order"]}},
                           upsert=True)

    # procédures (upsert par code, sans écraser les métadonnées d'édition existantes)
    n_new = n_upd = 0
    for p in procs:
        doc = build_doc(p, now)
        existing = col_pro.find_one({"code": doc["code"]})
        if existing:
            patch = {k: v for k, v in doc.items()
                     if k not in ("created_at", "created_by", "version", "status")}
            patch["updated_at"] = now
            col_pro.update_one({"code": doc["code"]}, {"$set": patch})
            n_upd += 1
        else:
            col_pro.insert_one(doc)
            n_new += 1

    print(f"DB={db_name}")
    print(f"catégories : {len(cats)} ({', '.join(c['key'] for c in cats)})")
    print(f"procédures : {n_new} créées, {n_upd} mises à jour  (total en base : {col_pro.count_documents({})})")


if __name__ == "__main__":
    main()
