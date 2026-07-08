#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, datetime
from collections import Counter, defaultdict
from pymongo import MongoClient
db = MongoClient(os.getenv("MONGO_URI","mongodb://localhost:27017/"))["titan"]
DEBUT = "2026-06-01"

cache = {}
for d in db["access_barcodes_utids"].find({}, {"utid":1,"title":1}):
    if d.get("utid"): cache[d["utid"]] = d.get("title","")

def cat(u, up=None):
    if up and up.endswith("-ACCRED"): return "accredite"
    if u and u.startswith("EWC"): return "accredite"
    if u and u in cache: return "enfant" if "Bracelet Enfant" in cache[u] else "vehicule"
    return "personne"
def fmt(u):
    if not u: return "vide"
    if u.startswith("ACO_"): return "ACO_"
    if u.startswith("http"): return "URL/QR"
    if u.isdigit() and len(u)==28: return "barcode28"
    if u.isdigit() and len(u)==16: return "RFID16"
    if u.isdigit(): return f"num{len(u)}"
    return "autre"

docs = list(db["handshake_forensic"].find({"date_paris":{"$gte":DEBUT}}))
print(f"Transactions 24A (date_paris>={DEBUT}) : {len(docs)}")
if docs:
    print(f"Plage : {min(d.get('date_paris','') for d in docs)} -> {max(d.get('date_paris','') for d in docs)}")

st = Counter(str(d.get("status")) for d in docs)
print("\nStatus :", dict(st.most_common(8)))
ok = [d for d in docs if str(d.get("status"))=="0"]
print(f"Valides (status=0) : {len(ok)} / {len(docs)}")

print("\nFormat des VALIDES :", dict(Counter(fmt(d.get("utid")) for d in ok).most_common()))
cv = Counter(cat(d.get("utid"), d.get("upid")) for d in ok)
print("Categorie des VALIDES :", dict(cv.most_common()))

# Presents par categorie (E-S) sur les valides, toutes zones
comp = defaultdict(lambda:[0,0])
for d in ok:
    c=cat(d.get("utid"), d.get("upid")); dr=d.get("direction","")
    if dr=="Entree": comp[c][0]+=1
    elif dr=="Sortie": comp[c][1]+=1
print("\nPresents calcules (valides, E-S) :")
for c,(e,s) in sorted(comp.items(), key=lambda x:-(x[1][0])):
    print(f"   {c:<11} E={e:<6} S={s:<6} present={e-s}")

# Top titres vehicules
print("\nTop titres VEHICULE (valides) :")
tv = Counter()
for d in ok:
    u=d.get("utid")
    if u in cache and "Bracelet Enfant" not in cache[u]:
        tv[cache[u]]+=1
for t,n in tv.most_common(20): print(f"   {n:>5} | {t!r}")

# Vehicules par porte
print("\nVehicules (valides) par porte :")
vg = Counter()
for d in ok:
    if cat(d.get("utid"), d.get("upid"))=="vehicule":
        vg[(d.get("gate") or {}).get("Name","?")]+=1
for g,n in vg.most_common(): print(f"   {n:>5} | {g}")

# Gap : valides NON dans le cache (sous-estimation), par porte
print("\nScans VALIDES non trouves dans le cache (classes 'personne' a tort possible) :")
unk = [d for d in ok if (d.get("utid") or "") and not (d.get("utid") or "").startswith(("ACO_","EWC")) and (d.get("utid") not in cache) and not (d.get("upid") or "").endswith("-ACCRED")]
print(f"   total : {len(unk)}")
ug = Counter((d.get("gate") or {}).get("Name","?") for d in unk)
print("   par porte :", dict(ug.most_common()))
print("   echantillon UTID :", [d.get("utid") for d in unk[:8]])
