"""momentus_timeline.py - Programme du site (Momentus Elite) dans la timeline SAISON.

Module pur (ni Flask ni connexion Mongo propre : `db` en argument). Appele a la
fin de chaque synchro reussie de momentus_sync.py (toutes les heures) ;
lancable seul :
    python momentus_timeline.py --dry-run      # liste sans rien ecrire
    python momentus_timeline.py                # ecrit

Hors grands evenements, le programme du jour du site est ce qui est reserve
dans Momentus (seminaires, roulages, receptifs). On l'ecrit dans le document
`timetable` {event: "SAISON", year: "<annee Paris>"} au format des vignettes de
la timeline, avec `origin: "momentus"` :

- fenetre glissante J-1 -> J+14 (heure de Paris). Dans la fenetre, seules les
  vignettes origin=momentus sont remplacees ; les vignettes manuelles ne sont
  jamais touchees. Les vignettes momentus plus anciennes que J-KEEP_PAST_DAYS
  sont purgees (le detail reste dans momentus_*) pour garder le document petit.
- une fonction (creneau horaire) -> une vignette : debut/fin, lieu = espace
  Momentus (+ `feature_id` si un rattachement `valide` existe dans
  momentus_lieux_mapping) ;
- un espace reserve sans fonction ce jour-la pour cet evenement -> une vignette
  par (evenement, jour, phase, horaires), lieux regroupes, sans heure si la
  reservation est a la journee.
- Titre = nom de l'evenement Momentus SEUL : jamais de contact client
  (contactRoles), ni montant, ni nom de pilote.
- Ecartes : objets supprimes (_sync.deleted_at), evenements annules, perdus,
  prospects (espace non reserve) et blackouts.
- Changement d'annee : les vignettes de janvier vont dans SAISON/<annee+1>.
- Une vignette dont l'operateur a modifie la remarque, les taches ou le statut
  de preparation (drawer, scope operateur) conserve ces champs. Une vignette
  editee en entier dans la modale devient `manual-edit` : elle sort de la
  synchro (jamais ecrasee, jamais recreee en double).
- Ecriture atomique par date ($pull origin momentus puis $push) : pas de
  lecture-reecriture du document, donc pas de perte d'un ajout manuel
  concurrent. `version` n'est incrementee que si quelque chose change.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo("Europe/Paris")
SAISON = "SAISON"
ORIGIN = "momentus"
CATEGORY = "Momentus"
DAYS_BEFORE = 1
DAYS_AFTER = 14
KEEP_PAST_DAYS = 7
PHASES = {"moveIn": "reserve", "event": "exploitation", "moveOut": "demontage", "dark": "bloque"}
# Champs operateur preserves d'une synchro a l'autre
OPERATOR_FIELDS = ("remark", "todo", "preparation_checked", "remark_manual")

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[<>\"`]")
_MAIL = re.compile(r"\S+@\S+")
_PHONE = re.compile(r"(?:\+|\b0)\d(?:[\s.\-]?\d{2}){4}\b")


def _clean(text, limit=200):
    """Texte affichable : la timeline injecte activity/place en innerHTML, et
    coupe titre et lieu au premier '/'. Les libelles de fonctions sont du
    texte libre : mails et numeros de telephone eventuels sont retires."""
    s = _UNSAFE.sub("", str(text or "")).replace("/", "-")
    s = _PHONE.sub("[tel]", _MAIL.sub("[mail]", s))
    return " ".join(s.split())[:limit]


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _hhmm(value):
    s = str(value or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})", s)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def _duration(start, end):
    if not start or not end:
        return ""
    a = int(start[:2]) * 60 + int(start[3:5])
    b = int(end[:2]) * 60 + int(end[3:5])
    d = b - a if b > a else b + 1440 - a
    return f"{d // 60:02d}:{d % 60:02d}"


def _mk_id(seed):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "momentus-timeline|" + seed))


def _status(ev):
    if ev.get("isDefinite"):
        return "confirme"
    if ev.get("isTentative"):
        return "option"
    if ev.get("isProspect"):
        return "prospect"
    return ""


def paris_today(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(TZ_PARIS).date()


def window(now=None):
    t = paris_today(now)
    return t - timedelta(days=DAYS_BEFORE), t + timedelta(days=DAYS_AFTER)


def build_items(db, d_from, d_to):
    """{'YYYY-MM-DD': [vignette, ...]} des activites Momentus sur [d_from, d_to]."""
    lo, hi = d_from.isoformat(), d_to.isoformat()
    mapping = {m["_id"]: m.get("feature_id") for m in db["momentus_lieux_mapping"].find(
        {"status": "valide", "feature_id": {"$nin": [None, ""]}}, {"feature_id": 1})}
    q = {"_sync.deleted_at": None, "isCanceled": {"$ne": True}, "isLost": {"$ne": True},
         "isProspect": {"$ne": True}, "isBlackout": {"$ne": True},
         "bookedSpaces": {"$elemMatch": {"startDate": {"$lte": hi}, "endDate": {"$gte": lo}}}}
    proj = {"name": 1, "eventTypeName": 1, "bookedSpaces": 1, "isDefinite": 1, "isTentative": 1,
            "isProspect": 1, "estimatedTotalAttendance": 1, "estimatedAttendance": 1}
    events = {e["_id"]: e for e in db["momentus_events"].find(q, proj)}
    out = defaultdict(list)
    if not events:
        return out

    # --- Fonctions (creneaux) ------------------------------------------------
    covered = set()   # (event_id, room_id, day) couverts par une fonction
    fq = {"_sync.deleted_at": None, "eventId": {"$in": list(events)},
          "startDate": {"$lte": hi}, "endDate": {"$gte": lo}}
    fproj = {"eventId": 1, "name": 1, "startDate": 1, "endDate": 1, "startTime": 1, "endTime": 1,
             "isAllDay": 1, "roomId": 1, "roomName": 1, "venueName": 1, "functionTypeName": 1,
             "expectedAttendance": 1, "agreedAttendance": 1, "guaranteedAttendance": 1}
    fn_items = {}   # (jour, evenement, debut, fin, lieu) -> vignette (creneaux identiques fusionnes)
    for fn in db["momentus_functions"].find(fq, fproj).sort("_id", 1):
        ev = events.get(fn.get("eventId"))
        a, b = _day(fn.get("startDate")), _day(fn.get("endDate")) or _day(fn.get("startDate"))
        if not ev or not a:
            continue
        b = max(a, b)
        all_day = bool(fn.get("isAllDay"))
        if all_day and not fn.get("roomId"):
            # Notes sans lieu ni horaire ("Details de l'evenement global",
            # "Details traiteur"...) : texte libre, pas un creneau.
            continue
        f_start = "" if all_day else _hhmm(fn.get("startTime"))
        f_end = "" if all_day else _hhmm(fn.get("endTime"))
        place = _clean(fn.get("roomName") or fn.get("venueName"))
        detail = []
        label = _clean(fn.get("name"), 120)
        ftype = _clean(fn.get("functionTypeName"), 60)
        if label and label.lower() != _clean(ev.get("name")).lower():
            detail.append(label)
        if ftype:
            detail.append(ftype)
        att = fn.get("guaranteedAttendance") or fn.get("expectedAttendance") or fn.get("agreedAttendance")
        if att:
            detail.append(f"{att} pers.")
        if a != b:
            detail.append(f"du {a.strftime('%d/%m')} au {b.strftime('%d/%m')}")
        x = max(a, d_from)
        while x <= min(b, d_to):
            ds = x.isoformat()
            covered.add((ev["_id"], fn.get("roomId"), ds))
            start = f_start if x == a else ""
            end = f_end if x == b else ""
            k = (ds, ev["_id"], start, end, place)
            item = fn_items.get(k)
            if item is None:
                item = fn_items[k] = {
                    "_id": _mk_id(f"fn|{fn['_id']}|{ds}"),
                    "date": ds, "start": start, "end": end, "duration": _duration(start, end),
                    "category": CATEGORY, "activity": _clean(ev.get("name")) or "Reservation Momentus",
                    "place": place, "department": _clean(ev.get("eventTypeName"), 120),
                    "type": "Timetable", "origin": ORIGIN, "_detail": [],
                    "preparation_checked": "", "momentus_event_id": ev["_id"],
                    "momentus_status": _status(ev),
                }
                fid = mapping.get(fn.get("roomId"))
                if fid:
                    item["feature_id"] = fid
            d = ", ".join(detail)
            if d and d not in item["_detail"]:
                item["_detail"].append(d)
            x += timedelta(days=1)
    for item in fn_items.values():
        bits = ["Momentus", item["momentus_status"]] + item.pop("_detail")
        item["remark"] = " | ".join(p for p in bits if p)[:600]
        out[item["date"]].append(item)

    # --- Espaces reserves sans fonction ce jour-la ---------------------------
    for ev in events.values():
        groups = defaultdict(list)   # (day, phase, start, end) -> [(room_name, room_id)]
        for bs in ev.get("bookedSpaces") or []:
            a, b = _day(bs.get("startDate")), _day(bs.get("endDate"))
            if not a or not b or b < d_from or a > d_to:
                continue
            phase = PHASES.get(bs.get("usageType"), bs.get("usageType") or "autre")
            all_day = bool(bs.get("isAllDay"))
            s = "" if all_day else _hhmm(bs.get("startTime"))
            e = "" if all_day else _hhmm(bs.get("endTime"))
            x = max(a, d_from)
            while x <= min(b, d_to):
                ds = x.isoformat()
                if (ev["_id"], bs.get("roomId"), ds) not in covered:
                    groups[(ds, phase, s if x == a else "", e if x == b else "")].append(
                        (_clean(bs.get("roomName")), bs.get("roomId")))
                x += timedelta(days=1)
        for (ds, phase, s, e), rooms in groups.items():
            names = sorted({n for n, _ in rooms if n})
            fids = sorted({mapping[r] for _, r in rooms if mapping.get(r)})
            bits = ["Momentus", _status(ev), f"espace {phase}"]
            if not s and not e:
                bits.append("a la journee")
            att = ev.get("estimatedTotalAttendance") or ev.get("estimatedAttendance")
            if att:
                bits.append(f"{att} pers. (estime)")
            item = {
                "_id": _mk_id(f"bs|{ev['_id']}|{ds}|{phase}|{s}|{e}"),
                "date": ds, "start": s, "end": e, "duration": _duration(s, e),
                "category": CATEGORY, "activity": _clean(ev.get("name")) or "Reservation Momentus",
                "place": _clean(", ".join(names), 200), "department": _clean(ev.get("eventTypeName"), 120),
                "type": "Timetable", "origin": ORIGIN, "remark": " | ".join(p for p in bits if p),
                "preparation_checked": "", "momentus_event_id": ev["_id"], "momentus_phase": phase,
            }
            if len(fids) == 1:
                item["feature_id"] = fids[0]
            elif fids:
                item["feature_ids"] = fids
            out[ds].append(item)

    for ds in out:
        out[ds].sort(key=lambda it: (it["start"] or "99", it["activity"], it["place"]))
    return out


def _comparable(items):
    return sorted((sorted((k, str(v)) for k, v in it.items() if k not in OPERATOR_FIELDS) for it in items))


def _sync_year_doc(db, year, days, items_by_day, prune_before, dry_run):
    """Remplace les vignettes momentus des `days` (de l'annee `year`) du doc
    SAISON/<year>. Rend des statistiques."""
    col = db["timetable"]
    key = {"event": SAISON, "year": str(year)}
    doc = col.find_one(key, {"data": 1}) or {}
    data = doc.get("data") or {}

    foreign_ids, operator = set(), {}
    for items in data.values():
        for it in items or []:
            if it.get("origin") != ORIGIN:
                foreign_ids.add(str(it.get("_id")))
            elif it.get("remark_manual") or it.get("todo") or it.get("preparation_checked"):
                operator[str(it.get("_id"))] = {k: it[k] for k in OPERATOR_FIELDS if k in it}

    pulls, pushes = [], {}
    stats = {"year": str(year), "items": 0, "days_changed": 0, "pruned_days": 0}
    for ds in days:
        new = []
        for it in items_by_day.get(ds, []):
            if it["_id"] in foreign_ids:
                continue  # editee en entier par un operateur : elle lui appartient
            it = dict(it)
            it.update(operator.get(it["_id"], {}))
            new.append(it)
        old = [it for it in data.get(ds) or [] if it.get("origin") == ORIGIN]
        stats["items"] += len(new)
        if _comparable(old) == _comparable(new) and all(
                {k: o.get(k) for k in OPERATOR_FIELDS} == {k: n.get(k) for k in OPERATOR_FIELDS}
                for o, n in zip(sorted(old, key=lambda i: i["_id"]), sorted(new, key=lambda i: i["_id"]))):
            continue
        stats["days_changed"] += 1
        if old:
            pulls.append(ds)
        if new:
            pushes[ds] = new
    for ds, items in data.items():
        if ds < prune_before and any(it.get("origin") == ORIGIN for it in items or []):
            pulls.append(ds)
            stats["pruned_days"] += 1

    if dry_run or (not pulls and not pushes):
        return stats
    if pulls:
        col.update_one(key, {"$pull": {f"data.{ds}": {"origin": ORIGIN} for ds in pulls}})
    upd = {"$inc": {"version": 1}}
    if pushes:
        upd["$push"] = {f"data.{ds}": {"$each": items} for ds, items in pushes.items()}
    col.update_one(key, upd, upsert=True)
    for ds in pulls:
        if ds not in pushes:
            # Une date videe ne doit pas laisser de section vide dans la timeline
            col.update_one({**key, f"data.{ds}": {"$size": 0}}, {"$unset": {f"data.{ds}": ""}})
    return stats


def sync_saison_timeline(db, now=None, dry_run=False):
    """Ecrit la fenetre J-1 -> J+14 dans SAISON/<annee>. Rend un resume."""
    d_from, d_to = window(now)
    items = build_items(db, d_from, d_to)
    prune_before = (paris_today(now) - timedelta(days=KEEP_PAST_DAYS)).isoformat()
    days_by_year = defaultdict(list)
    x = d_from
    while x <= d_to:
        days_by_year[x.year].append(x.isoformat())
        x += timedelta(days=1)
    per_year = [_sync_year_doc(db, y, days, items, prune_before, dry_run)
                for y, days in sorted(days_by_year.items())]
    return {
        "from": d_from.isoformat(), "to": d_to.isoformat(), "dry_run": dry_run,
        "per_day": {d: len(items.get(d, [])) for ys in days_by_year.values() for d in ys},
        "docs": per_year,
        "items": items,
    }


def main(argv=None):
    from pymongo import MongoClient
    env = os.getenv("TITAN_ENV", "dev").strip().lower()
    ap = argparse.ArgumentParser(description="Timeline SAISON depuis Momentus")
    ap.add_argument("--db", default="titan" if env in {"prod", "production"} else "titan_dev")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true", help="Affiche les vignettes")
    args = ap.parse_args(argv)
    db = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=5000)[args.db]
    res = sync_saison_timeline(db, dry_run=args.dry_run)
    print(f"Base {args.db} | fenetre {res['from']} -> {res['to']} | dry_run={res['dry_run']}")
    for d, n in res["per_day"].items():
        print(f"  {d} : {n} vignettes")
        if args.list:
            for it in res["items"].get(d, []):
                t = (it["start"] or "--:--") + "-" + (it["end"] or "--:--")
                print(f"      {t}  {it['activity'][:40]:40}  {it['place'][:40]:40}  {it.get('feature_id', '')}")
    for s in res["docs"]:
        print("  doc SAISON/%s : %d vignettes, %d jours modifies, %d jours purges" % (
            s["year"], s["items"], s["days_changed"], s["pruned_days"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
