#!/usr/bin/env python3
"""
momentus_details.py - Detail operationnel des evenements Momentus -> MongoDB.

Complete momentus_sync.py (evenements, fonctions, espaces, referentiels) avec ce
que l'Open API expose evenement par evenement ou fonction par fonction :

    momentus_item_allocations   articles commandes sur chaque fonction : materiel,
                                mobilier, son, eclairage, nettoyage, restauration
                                ET personnel (categoryType "labor")
    momentus_personnel          le personnel commande (agents de securite, SSIAP,
                                regisseurs...) au format du planning de Bibler :
                                donnees_presences = par jour, 48 plages de 30 min
                                portant nombre_personnes
    momentus_booked_spaces      espaces reserves en detail, pilote de montage /
                                regie compris (spaceUsageName)
    momentus_booked_spaces_removed  espaces retires (quand, par qui)
    momentus_package_allocations    packages poses sur les fonctions
    momentus_documents          documents des dossiers autorises SEULEMENT
                                (Plans, Etat des lieux), fichier telecharge
    momentus_items / momentus_packages  catalogue de l'inventaire
    momentus_details_state      etat par evenement + verrou (_id "__lock__")

REGLE NON NEGOCIABLE : RIEN QUI TOUCHE A L'ARGENT. Pas de devis, pas de facture,
pas de paiement, pas de tarif, pas de cout, pas de code comptable. Les montants
rendus par l'API sont retires AVANT toute ecriture (`_sans_argent`). Les
dossiers documentaires hors liste blanche (Devis prestataires...) ne sont ni
lus en detail, ni stockes, ni telecharges. Pas de comptes ni de contacts.

LECTURE SEULE cote Momentus : uniquement des GET (client de momentus_sync).

Ordonnancement : appele par momentus_sync.py a la fin de chaque synchro reussie
(tache "Sync Momentus", toutes les heures), verrou propre ; lancable seul :
  1. catalogue (articles, packages) ;
  2. evenements prioritaires : modifies depuis leur derniere lecture
     (lastModifiedOn), ou ayant une fonction / un espace dans la fenetre
     J-2 / J+45 (relus a chaque passage : rien ne garantit qu'ajouter un
     article fasse bouger lastModifiedOn) ;
  3. puis les plus anciennement lus de l'horizon (fin >= J-365) jusqu'au budget
     d'appels du passage. L'horizon complet est ainsi relu en quelques heures.
  `--all` etend l'horizon a tout l'historique (import initial), `--event` relit
  un evenement, `--dry-run` n'ecrit rien et affiche ce qui serait fait.

Rien n'est jamais supprime : un objet disparu recoit `_sync.deleted_at`
(fichiers telecharges conserves).
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
import unicodedata
import uuid
from datetime import date, datetime, timedelta

from pymongo import MongoClient, ReplaceOne, UpdateOne

import momentus_sync as ms

TASK_NAME = "Details Momentus"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")

DOCS_DIR = os.getenv("MOMENTUS_DOCS_DIR", "").strip() or os.path.join(SCRIPT_DIR, "uploads", "momentus_documents")
DOC_MAX_BYTES = 50 * 1024 * 1024
# Liste blanche des dossiers documentaires (noms normalises : minuscules, sans
# accents). Tout autre dossier, en particulier "Devis prestataires", est ignore.
DOC_FOLDERS = {"plans", "etat des lieux"}

WINDOW = (-2, 45)            # fenetre relue a chaque passage (jours)
HORIZON_DAYS = 365           # horizon de la relecture tournante (fin d'evenement >= J-365)
CALL_BUDGET = int(os.getenv("MOMENTUS_DETAILS_BUDGET", "3000"))
LOCK_TTL_MINUTES = 90
PRESENCE_MAX_DAYS = 31       # au-dela, un article sans horaires propres n'est pas etale

log = logging.getLogger("momentus_details")


# ---------------------------------------------------------------------------
# Sans argent
# ---------------------------------------------------------------------------

# Toute cle dont le nom evoque un montant, un tarif, la facturation ou la
# comptabilite est retiree, a toute profondeur.
_MONEY_KEY = re.compile(
    r"cost|price|pricing|total|balance|revenue|gl_?code|invoic|billing|tax|discount|"
    r"servicecharge|deposit|unbillable|perperson|itemcode|payment|credit|refund|rate$|rates$|"
    r"priceschedule|amount|budget",
    re.I,
)
# Exceptions : unite et type de tarif ne sont pas des montants (ex. "Jour",
# "15 a 35 personnes") et servent a lire les quantites.
_MONEY_KEEP = {"ratetype", "unitname", "rates", "itemrate"}
# Montants ecrits en clair dans un texte libre (note, description) : "12 EUR",
# "45,50 €", "€ 30", "300 euros HT".
_MONEY_TEXT = re.compile(
    r"(?:€\s?\d[\d\s.,]*|\d[\d\s.,]*\s?(?:€|eur\b|euros?\b))(?:\s?(?:ht|ttc)\b)?", re.I)


# Articles de l'inventaire qui sont des lignes commerciales et non du materiel
# ou du personnel ("Devis global pour partenaires", "Frais d'ouverture") : ni
# stockes dans le catalogue, ni dans les articles commandes.
_MONEY_ITEM = re.compile(r"devis|frais d.?ouverture|factur|acompte|caution|remise commerciale|forfait global", re.I)


def _article_commercial(name):
    return bool(_MONEY_ITEM.search(_norm(name)))


def _sans_argent(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = k.lower()
            if kl not in _MONEY_KEEP and _MONEY_KEY.search(kl):
                continue
            out[k] = _sans_argent(v)
        return out
    if isinstance(obj, list):
        return [_sans_argent(v) for v in obj]
    if isinstance(obj, str) and _MONEY_TEXT.search(obj):
        return _MONEY_TEXT.sub("[montant retire]", obj)
    return obj


def _norm(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip().lower()


# ---------------------------------------------------------------------------
# Presences au format Bibler
# ---------------------------------------------------------------------------

def _slots():
    return [(f"{m // 60:02d}:{m % 60:02d}", f"{(m + 30) // 60 % 24:02d}:{(m + 30) % 60:02d}")
            for m in range(0, 1440, 30)]


SLOTS = _slots()


def _dt(day, hhmm):
    try:
        d = date.fromisoformat(str(day)[:10])
    except (TypeError, ValueError):
        return None
    if not hhmm:
        return datetime(d.year, d.month, d.day)
    try:
        h, m = (int(x) for x in str(hhmm)[:5].split(":"))
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, h % 24, m)


def _interval(alloc, fn):
    """(debut, fin, source) en heure locale, ou (None, None, raison).

    L'article porte ses propres dates / heures quand il est borne dans le temps
    (agent 20:00-01:00) ; sinon on prend le creneau de la fonction. Une fin
    inferieure ou egale au debut le meme jour passe minuit (07:00-00:00).
    """
    for src, sd, st, ed, et in (
        ("article", alloc.get("startDate"), alloc.get("startTime"), alloc.get("endDate"), alloc.get("endTime")),
        ("fonction", fn.get("startDate"), fn.get("startTime"), fn.get("endDate"), fn.get("endTime")),
    ):
        if not sd or not st or not et:
            continue
        a, b = _dt(sd, st), _dt(ed or sd, et)
        if a is None or b is None:
            continue
        if b <= a:
            b += timedelta(days=1)
        if (b - a).days > PRESENCE_MAX_DAYS:
            return None, None, "trop_long"
        return a, b, src
    return None, None, "inconnu"


def _days(alloc, fn):
    """Jours couverts quand les heures sont inconnues (borne PRESENCE_MAX_DAYS)."""
    sd = alloc.get("startDate") or fn.get("startDate")
    ed = alloc.get("endDate") or fn.get("endDate") or sd
    try:
        a, b = date.fromisoformat(str(sd)[:10]), date.fromisoformat(str(ed)[:10])
    except (TypeError, ValueError):
        return []
    if b < a or (b - a).days > PRESENCE_MAX_DAYS:
        return [a.isoformat()]
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def presences(debut, fin, nombre):
    """Grille Bibler : [{date, plages_horaires: 48 x {heure_debut, heure_fin, nombre_personnes}}]."""
    out = []
    day = debut.date()
    while datetime(day.year, day.month, day.day) < fin:
        base = datetime(day.year, day.month, day.day)
        plages = []
        for i, (hd, hf) in enumerate(SLOTS):
            s0, s1 = base + timedelta(minutes=30 * i), base + timedelta(minutes=30 * (i + 1))
            plages.append({"heure_debut": hd, "heure_fin": hf,
                           "nombre_personnes": nombre if (s0 < fin and s1 > debut) else 0})
        out.append({"date": day.isoformat(), "plages_horaires": plages})
        day += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Transformations
# ---------------------------------------------------------------------------

def _quantities(alloc):
    cost = alloc.get("cost") or {}
    q, tq = cost.get("quantity"), cost.get("timeQuantity")
    return (q if isinstance(q, (int, float)) else None, tq if isinstance(tq, (int, float)) else None)


def allocation_doc(alloc, fn, event, items, rooms):
    """Ligne d'article sans aucun montant, enrichie pour l'exploitation."""
    q, tq = _quantities(alloc)
    doc = _sans_argent(alloc)
    item = items.get(alloc.get("itemId")) or {}
    room_id = alloc.get("roomId") or fn.get("roomId")
    doc.update({
        "_id": alloc["id"],
        "eventId": fn.get("eventId"),
        "eventName": (event or {}).get("name") or fn.get("eventName"),
        "quantite": q,
        "duree": tq,
        "categoryName": (item.get("categoryName") or "").strip() or None,
        "departmentName": item.get("departmentName"),
        "lieuId": room_id,
        "lieu": (rooms.get(room_id) or {}).get("name") or fn.get("roomName"),
        "venueName": fn.get("venueName"),
        "functionRoomName": fn.get("roomName"),
        "functionStatus": fn.get("functionStatus"),
        "jours": _days(alloc, fn),
    })
    return doc


def personnel_doc(adoc, alloc, fn):
    """Personnel commande au format du planning de Bibler (calendrier_<annee>_<evt>)."""
    debut, fin, source = _interval(alloc, fn)
    nombre = adoc.get("quantite")
    nombre = int(nombre) if isinstance(nombre, (int, float)) and float(nombre).is_integer() else nombre
    doc = {
        "_id": adoc["_id"],
        "source": "momentus",
        "eventId": adoc.get("eventId"),
        "eventName": adoc.get("eventName"),
        "functionId": adoc.get("functionId"),
        "functionName": adoc.get("functionName"),
        "itemId": adoc.get("itemId"),
        "metier": adoc.get("name"),
        "categorie": adoc.get("categoryName"),
        "departement": adoc.get("departmentName"),
        "package": adoc.get("packageName"),
        "lieuId": adoc.get("lieuId"),
        "lieu": adoc.get("lieu"),
        "nombre_personnes": nombre,
        "unite": adoc.get("unitName"),
        "duree": adoc.get("duree"),
        "note": adoc.get("note"),
        "horaires": source,  # article | fonction | inconnu | trop_long
        "debut": debut.isoformat(timespec="minutes") if debut else None,
        "fin": fin.isoformat(timespec="minutes") if fin else None,
        "jours": sorted({p["date"] for p in presences(debut, fin, 1)}) if debut else adoc.get("jours", []),
        "donnees_presences": presences(debut, fin, nombre or 0) if debut else [],
    }
    return doc


def booked_space_doc(bs, event):
    doc = _sans_argent(bs)
    doc.update({"_id": bs["id"], "eventId": event["_id"], "eventName": event.get("name")})
    return doc


def removed_space_doc(rs, event):
    space = _sans_argent(rs.get("space") or {})
    by = rs.get("removedBy") or {}
    return {**space, "_id": space.get("id"), "eventId": event["_id"], "eventName": event.get("name"),
            "removedOn": rs.get("removedOn"), "removedByName": by.get("name")}


def package_allocation_doc(pa, event):
    doc = _sans_argent({k: v for k, v in pa.items() if k != "items"})
    doc.update({"_id": pa["id"], "eventId": event["_id"], "eventName": event.get("name"),
                "itemAllocationIds": [i.get("id") for i in pa.get("items") or [] if i.get("id")]})
    return doc


def item_catalog_doc(it):
    doc = _sans_argent(it)
    doc["_id"] = it["id"]
    return doc


# ---------------------------------------------------------------------------
# Ecriture
# ---------------------------------------------------------------------------

class Writer:
    """Upserts avec `_sync` (comme momentus_sync) ; en dry-run, compte seulement."""

    def __init__(self, db, now, dry_run):
        self.db, self.now, self.dry = db, now, dry_run
        self.counts = {}

    def upsert(self, name, docs):
        docs = [d for d in docs if d.get("_id")]
        self.counts.setdefault(name, 0)
        self.counts[name] += len(docs)
        if self.dry or not docs:
            return
        coll = self.db[name]
        ids = [d["_id"] for d in docs]
        first = {d["_id"]: (d.get("_sync") or {}).get("first_seen")
                 for d in coll.find({"_id": {"$in": ids}}, {"_sync.first_seen": 1})}
        ops = []
        for d in docs:
            d = dict(d)
            d["_sync"] = {"first_seen": first.get(d["_id"]) or self.now, "last_seen": self.now, "deleted_at": None}
            ops.append(ReplaceOne({"_id": d["_id"]}, d, upsert=True))
        for i in range(0, len(ops), 1000):
            coll.bulk_write(ops[i:i + 1000], ordered=False)

    def mark_missing(self, name, flt, seen):
        if self.dry:
            return 0
        q = dict(flt)
        q["_id"] = {"$nin": list(seen)}
        q["_sync.deleted_at"] = None
        return self.db[name].update_many(q, {"$set": {"_sync.deleted_at": self.now}}).modified_count


def _ensure_indexes(db):
    for name in ("momentus_item_allocations", "momentus_personnel", "momentus_booked_spaces",
                 "momentus_booked_spaces_removed", "momentus_package_allocations", "momentus_documents"):
        c = db[name]
        c.create_index("eventId")
        c.create_index("_sync.deleted_at")
    for name in ("momentus_item_allocations", "momentus_personnel"):
        db[name].create_index("functionId")
        db[name].create_index("jours")
        db[name].create_index("itemId")
    db["momentus_item_allocations"].create_index("categoryType")
    db["momentus_personnel"].create_index("donnees_presences.date")
    db["momentus_booked_spaces"].create_index([("startDate", 1), ("endDate", 1)])
    db["momentus_booked_spaces"].create_index("roomId")
    db["momentus_details_state"].create_index("synced_at")


# ---------------------------------------------------------------------------
# Lecture Momentus
# ---------------------------------------------------------------------------

def sync_catalog(api, w, stats):
    items = [i for i in api.get("/v1/items", includeInactive="true") or []
             if i.get("id") and not _article_commercial(i.get("name"))]
    w.upsert("momentus_items", [item_catalog_doc(i) for i in items])
    w.mark_missing("momentus_items", {}, {i["id"] for i in items})
    packages = [p for p in api.get("/v1/packages", includeInactive="true") or []
                if p.get("id") and not _article_commercial(p.get("name"))]
    w.upsert("momentus_packages", [item_catalog_doc(p) for p in packages])
    w.mark_missing("momentus_packages", {}, {p["id"] for p in packages})
    stats["items"], stats["packages"] = len(items), len(packages)
    return {i["id"]: i for i in items}


def sync_function_items(api, w, fn, event, items, rooms):
    allocs = [a for a in api.get("/v1/functions/item-allocations", functionId=fn["_id"]) or []
              if a.get("id") and not _article_commercial(a.get("name"))]
    adocs = [allocation_doc(a, fn, event, items, rooms) for a in allocs]
    pdocs = [personnel_doc(d, a, fn) for d, a in zip(adocs, allocs) if a.get("categoryType") == "labor"]
    w.upsert("momentus_item_allocations", adocs)
    w.upsert("momentus_personnel", pdocs)
    w.mark_missing("momentus_item_allocations", {"functionId": fn["_id"]}, {d["_id"] for d in adocs})
    w.mark_missing("momentus_personnel", {"functionId": fn["_id"]}, {d["_id"] for d in pdocs})
    return len(adocs), len(pdocs)


def _download(api, event_id, doc, dry):
    """Telecharge un document autorise. Rend le bloc `fichier` ou None."""
    safe = re.sub(r"[^\w.\- ]+", "_", doc.get("name") or doc["id"])[:120]
    rel = os.path.join(event_id, f"{doc['id']}_{safe}")
    if dry:
        return {"chemin": rel.replace("\\", "/"), "dry_run": True}
    if api.token is None:
        api._authenticate()
    url = f"{ms.API_URL}/v1/events/{event_id}/documents/{doc['id']}/download"
    for attempt in range(2):
        resp = api.session.get(url, headers={"Authorization": f"Bearer {api.token}"},
                               timeout=ms.HTTP_TIMEOUT, stream=True, allow_redirects=False)
        api.calls += 1
        if resp.status_code == 401 and attempt == 0:
            resp.close()
            api._authenticate()
            continue
        break
    if resp.status_code != 200:
        resp.close()
        raise ms.MomentusError(f"download_http_{resp.status_code}")
    path = os.path.join(DOCS_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    h, size, tmp = hashlib.sha256(), 0, path + ".part"
    try:
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(64 * 1024):
                size += len(chunk)
                if size > DOC_MAX_BYTES:
                    raise ms.MomentusError("document_trop_gros")
                h.update(chunk)
                fh.write(chunk)
        os.replace(tmp, path)
    finally:
        resp.close()
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"chemin": rel.replace("\\", "/"), "taille": size, "sha256": h.hexdigest(),
            "content_type": resp.headers.get("content-type"), "telecharge_le": ms._utcnow()}


def sync_documents(api, w, db, event, stats):
    docs = api.get(f"/v1/events/{event['_id']}/documents") or []
    keep = [d for d in docs if d.get("id") and _norm(d.get("folderName")) in DOC_FOLDERS]
    stats["documents_ignores"] = stats.get("documents_ignores", 0) + (len(docs) - len(keep))
    known = {d["_id"]: d for d in db["momentus_documents"].find({"_id": {"$in": [d["id"] for d in keep]}})}
    out = []
    for d in keep:
        meta = {k: d.get(k) for k in ("id", "name", "description", "folderId", "folderName", "isUploaded",
                                      "fileUrl", "dateUploaded", "uploadedByName")}
        meta.update({"_id": d["id"], "eventId": event["_id"], "eventName": event.get("name")})
        prev = (known.get(d["id"]) or {}).get("fichier")
        if d.get("isUploaded"):
            if prev and prev.get("sha256") and (known[d["id"]].get("dateUploaded") == d.get("dateUploaded")):
                meta["fichier"] = prev
            else:
                try:
                    meta["fichier"] = _download(api, event["_id"], d, w.dry)
                    stats["documents_telecharges"] = stats.get("documents_telecharges", 0) + 1
                except Exception as exc:  # un fichier en echec ne bloque pas l'evenement
                    meta["fichier"] = prev
                    meta["fichier_erreur"] = str(exc)[:200]
                    log.warning("Document %s de %s non telecharge : %s", d["id"], event["_id"], exc)
        out.append(meta)
    w.upsert("momentus_documents", out)
    w.mark_missing("momentus_documents", {"eventId": event["_id"]}, {d["_id"] for d in out})
    return len(out)


def sync_event(api, w, db, event, functions, items, rooms, stats):
    eid = event["_id"]
    spaces = [s for s in api.get(f"/v1/booked-spaces/{eid}") or [] if s.get("id")]
    sdocs = [booked_space_doc(s, event) for s in spaces]
    w.upsert("momentus_booked_spaces", sdocs)
    w.mark_missing("momentus_booked_spaces", {"eventId": eid}, {d["_id"] for d in sdocs})

    removed = [removed_space_doc(r, event) for r in api.get(f"/v1/booked-spaces/{eid}/removed-spaces") or []]
    removed = [r for r in removed if r.get("_id")]
    w.upsert("momentus_booked_spaces_removed", removed)

    pas = [package_allocation_doc(p, event) for p in api.get(f"/v1/package-allocations/{eid}") or [] if p.get("id")]
    w.upsert("momentus_package_allocations", pas)
    w.mark_missing("momentus_package_allocations", {"eventId": eid}, {p["_id"] for p in pas})

    ndocs = sync_documents(api, w, db, event, stats)

    n_items = n_staff = 0
    for fn in functions:
        a, p = sync_function_items(api, w, fn, event, items, rooms)
        n_items += a
        n_staff += p
    # Fonctions disparues de l'evenement : leurs articles aussi.
    fids = {f["_id"] for f in functions}
    for name in ("momentus_item_allocations", "momentus_personnel"):
        w.mark_missing(name, {"eventId": eid, "functionId": {"$nin": list(fids)}}, set())
    return {"spaces": len(sdocs), "removed": len(removed), "packages": len(pas), "documents": ndocs,
            "functions": len(functions), "items": n_items, "personnel": n_staff}


def mark_event_deleted(w, eid):
    for name in ("momentus_item_allocations", "momentus_personnel", "momentus_booked_spaces",
                 "momentus_package_allocations", "momentus_documents"):
        w.mark_missing(name, {"eventId": eid}, set())


# ---------------------------------------------------------------------------
# Selection des evenements
# ---------------------------------------------------------------------------

def select_events(db, args, today):
    """Liste ordonnee d'(event, raison) : prioritaires d'abord, puis tournante."""
    ev = db["momentus_events"]
    st = {s["_id"]: s for s in db["momentus_details_state"].find({"_id": {"$ne": "__lock__"}})}
    if args.event:
        e = ev.find_one({"_id": args.event})
        return [(e, "demande")] if e else []

    lo, hi = (today + timedelta(days=WINDOW[0])).isoformat(), (today + timedelta(days=WINDOW[1])).isoformat()
    in_window = set(db["momentus_functions"].distinct(
        "eventId", {"startDate": {"$lte": hi}, "endDate": {"$gte": lo}, "_sync.deleted_at": None}))
    in_window |= set(ev.distinct("_id", {"bookedSpaces": {"$elemMatch": {"startDate": {"$lte": hi},
                                                                         "endDate": {"$gte": lo}}},
                                         "_sync.deleted_at": None}))
    flt = {"_sync.deleted_at": None}
    if not args.all:
        flt["end"] = {"$gte": (today - timedelta(days=HORIZON_DAYS)).isoformat()}
    proj = {"name": 1, "lastModifiedOn": 1, "start": 1, "end": 1}
    events = list(ev.find(flt, proj))

    prio, rest = [], []
    for e in events:
        s = st.get(e["_id"])
        if s is None:
            rest.append((e, "jamais_lu", datetime.min))
        elif s.get("lastModifiedOn") != e.get("lastModifiedOn"):
            prio.append((e, "modifie"))
        elif e["_id"] in in_window:
            prio.append((e, "fenetre"))
        else:
            rest.append((e, "tournante", s.get("synced_at") or datetime.min))
    # Jamais lus d'abord, du plus proche d'aujourd'hui au plus lointain ; puis
    # la relecture tournante, du plus anciennement lu au plus recent.
    def _proximity(e):
        try:
            return abs((date.fromisoformat(str(e.get("start"))[:10]) - today).days)
        except ValueError:
            return 10 ** 6
    rest.sort(key=lambda x: (x[1] != "jamais_lu", _proximity(x[0]) if x[1] == "jamais_lu" else 0, x[2]))
    prio.sort(key=lambda x: (x[1] != "modifie", str(x[0].get("start") or "")))
    return prio + [(e, r) for e, r, _ in rest]


# ---------------------------------------------------------------------------
# Verrou, journal, main
# ---------------------------------------------------------------------------

def _acquire_lock(coll, owner, now):
    exp = now + timedelta(minutes=LOCK_TTL_MINUTES)
    res = coll.update_one({"_id": "__lock__", "$or": [{"expires": {"$lt": now}}, {"owner": None}]},
                          {"$set": {"owner": owner, "expires": exp}})
    if res.matched_count:
        return True
    try:
        coll.insert_one({"_id": "__lock__", "owner": owner, "expires": exp})
        return True
    except Exception:
        return False


def _release_lock(coll, owner):
    coll.update_one({"_id": "__lock__", "owner": owner}, {"$set": {"owner": None}})


def _cron(status, message):
    """cron_status.json, entree propre a ce script (fonction de momentus_sync)."""
    previous = ms.TASK_NAME
    ms.TASK_NAME = TASK_NAME
    try:
        ms._update_cron_status(status, message)
    finally:
        ms.TASK_NAME = previous


def _setup_logging(verbose):
    os.makedirs(LOG_DIR, exist_ok=True)
    logfile = os.path.join(LOG_DIR, f"momentus_details-{datetime.now():%Y%m%d}.log")
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s [MomentusDetails] %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(logfile, encoding="utf-8")])


def run(args):
    client_id, client_secret = ms._env("MOMENTUS_CLIENT_ID"), ms._env("MOMENTUS_CLIENT_SECRET")
    if not client_id or not client_secret:
        log.error("MOMENTUS_CLIENT_ID / MOMENTUS_CLIENT_SECRET absents")
        return 2
    mongo = MongoClient(ms.MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo[args.db]
    now = ms._utcnow()
    state = db["momentus_details_state"]
    owner = uuid.uuid4().hex
    if not args.dry_run:
        _ensure_indexes(db)
        if not _acquire_lock(state, owner, now):
            log.warning("Details Momentus deja en cours (verrou pose), abandon")
            return 0
    api = ms.MomentusClient(client_id, client_secret)
    w = Writer(db, now, args.dry_run)
    stats = {"mode": "dry-run" if args.dry_run else "ecriture", "budget": args.budget}
    run_doc = {"_id": owner, "kind": "details", "started_at": now, "status": "running", "db": args.db}
    t0 = time.monotonic()
    try:
        if not args.dry_run:
            db["momentus_sync_runs"].insert_one(run_doc)
        items = sync_catalog(api, w, stats)
        rooms = {r["_id"]: r for r in db["momentus_rooms"].find({}, {"name": 1})}
        today = date.today()

        # Evenements supprimes dans Momentus : leurs details aussi.
        deleted = [e["_id"] for e in db["momentus_events"].find({"_sync.deleted_at": {"$ne": None}}, {"_id": 1})]
        gone = [s["_id"] for s in state.find({"_id": {"$in": deleted}, "deleted": {"$ne": True}}, {"_id": 1})]
        for eid in gone:
            mark_event_deleted(w, eid)
            if not args.dry_run:
                state.update_one({"_id": eid}, {"$set": {"deleted": True, "synced_at": now}})
        stats["events_supprimes"] = len(gone)

        queue = select_events(db, args, today)
        stats["file"] = {"total": len(queue), "modifie": sum(1 for _, r in queue if r == "modifie"),
                         "fenetre": sum(1 for _, r in queue if r == "fenetre"),
                         "jamais_lu": sum(1 for _, r in queue if r == "jamais_lu")}
        done = {"events": 0, "functions": 0, "items": 0, "personnel": 0, "spaces": 0, "documents": 0}
        for event, reason in queue:
            if reason not in ("modifie", "fenetre", "demande") and api.calls >= args.budget:
                break
            fns = list(db["momentus_functions"].find({"eventId": event["_id"], "_sync.deleted_at": None}))
            try:
                res = sync_event(api, w, db, event, fns, items, rooms, stats)
            except ms.MomentusError as exc:
                log.warning("Evenement %s (%s) non lu : %s", event["_id"], event.get("name"), exc)
                stats.setdefault("erreurs", []).append(f"{event['_id']}: {str(exc)[:120]}")
                continue
            done["events"] += 1
            for k in ("functions", "items", "personnel", "spaces", "documents"):
                done[k] += res[k]
            log.debug("%s %s (%s) : %s", reason, event["_id"], event.get("name"), res)
            if not args.dry_run:
                state.update_one({"_id": event["_id"]}, {"$set": {
                    "synced_at": now, "lastModifiedOn": event.get("lastModifiedOn"), "raison": reason,
                    "deleted": False, **{k: res[k] for k in res}}}, upsert=True)
        stats["traites"] = done
        stats["ecrits"] = w.counts
        stats["api_calls"] = api.calls
        duration = round(time.monotonic() - t0, 1)
        msg = (f"{done['events']} evenements, {done['items']} articles dont {done['personnel']} personnel, "
               f"{done['documents']} documents, {api.calls} appels, {duration} s")
        log.info("OK %s | %s", msg, json.dumps(stats, default=str, ensure_ascii=False))
        if not args.dry_run:
            db["momentus_sync_runs"].update_one({"_id": owner}, {"$set": {
                "status": "ok", "ended_at": ms._utcnow(), "duration_s": duration, "api_calls": api.calls,
                "stats": stats}})
            _cron("ok", msg)
        return 0
    except Exception as exc:
        log.exception("Echec details Momentus")
        if not args.dry_run:
            db["momentus_sync_runs"].update_one({"_id": owner}, {"$set": {
                "status": "error", "ended_at": ms._utcnow(), "error": str(exc)[:500], "api_calls": api.calls,
                "stats": stats}})
            _cron("error", str(exc)[:200])
        return 1
    finally:
        if not args.dry_run:
            _release_lock(state, owner)
        mongo.close()


def main(argv=None):
    p = argparse.ArgumentParser(description="Detail operationnel Momentus -> MongoDB (lecture seule, sans argent)")
    p.add_argument("--db", default=ms.DB_NAME)
    p.add_argument("--dry-run", action="store_true", help="Lit Momentus, n'ecrit rien, ne telecharge rien")
    p.add_argument("--all", action="store_true", help="Tout l'historique (import initial)")
    p.add_argument("--event", help="Relire un seul evenement (id Momentus)")
    p.add_argument("--budget", type=int, default=CALL_BUDGET, help="Appels max pour la relecture tournante")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
