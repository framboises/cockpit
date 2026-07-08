#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
from collections import Counter
from pymongo import MongoClient
from live_controle import _label_coding, _label_status
db = MongoClient(os.getenv("MONGO_URI","mongodb://localhost:27017/"))["titan"]
DEBUT = "2026-06-01"
cache = {}
for d in db["access_barcodes_utids"].find({}, {"utid":1,"title":1}):
    if d.get("utid"): cache[d["utid"]] = d.get("title","")

def matched(u):
    return bool(u) and (u in cache)

for gate in ["PORTE SUD", "PORTE NORD VEHICULES"]:
    docs = list(db["handshake_forensic"].find(
        {"date_paris":{"$gte":DEBUT}, "gate.Name":gate, "status":"0"}))
    inc = [d for d in docs if matched(d.get("utid"))]
    unk = [d for d in docs if not matched(d.get("utid")) and not (d.get("utid") or "").startswith(("ACO_","EWC"))]
    print("="*100)
    print(f"{gate} : {len(docs)} scans valides  |  matches cache={len(inc)}  non-matches={len(unk)}")

    # repartition coding sur les non-matches
    cod = Counter((d.get("coding"), _label_coding(d.get("coding"))) for d in unk)
    print(f"  Types de code-barres (coding) des NON-matches :")
    for (c,lbl),n in cod.most_common(8):
        print(f"     {n:>5}  coding={c} -> {lbl}")

    print(f"\n  -- Echantillon NON-matches (code-barres physiques, classes 'personne') --")
    print(f"  {'date':<17} {'dir':<7} {'coding':<10} {'upid':<22} utid")
    for d in unk[:18]:
        print(f"  {(d.get('date_paris') or '')[:17]:<17} {d.get('direction',''):<7} {str(d.get('coding')):<10} {str(d.get('upid'))[:20]:<22} {d.get('utid')!r}")

    print(f"\n  -- Echantillon MATCHES (ACO_) pour comparaison --")
    for d in inc[:6]:
        u=d.get("utid")
        print(f"  {(d.get('date_paris') or '')[:17]:<17} {d.get('direction',''):<7} {str(d.get('coding')):<10} {u!r} -> {cache.get(u)!r}")
    print()
