"""Banc de rejeu de l'alerte door_saturation_forecast (alert_engine.py).

LECTURE SEULE. Rejoue le detecteur toutes les 5 min sur les archives 5 min du
controle d'acces (hsh_archive_tx_<EVENT>_<ANNEE>), exactement comme en live
(meme fonction, `door_tx_source`), et confronte ses alertes a trois
references independantes :

  L1 debit     : le debit 15 min de la porte depasse le seuil de capacite
                 (ce que mesurait le premier rejeu ; auto-reference : depend
                 de la capacite supposee).
  L2 renfort   : un PDA se met a scanner sur une porte a tripodes ou aucun PDA
                 n'avait scanne depuis 90 min, porte chargee. C'est la trace
                 objective d'un renfort envoye parce que les tripodes ne
                 suffisaient plus.
  L3 fiches    : fiche main courante (pcorg) citant la porte avec un mot de
                 flux (renfort, filtrage, attente, saturation...).

Mesures : precision (part des alertes suivies d'une reference sur la porte),
rappel (part des references precedees d'une alerte) et avance (minutes entre
la premiere alerte et la reference). Ventilation par pluie (meteo_previsions,
historique horaire Meteo-France) pour juger d'une regle meteo.

--capacity apprise : facteur de capacite par porte appris en validation
croisee (edition exclue de son propre apprentissage) = p98 du rapport
debit / capacite theorique des appareils actifs, borne [0.5, 1.5].
--print-factors : facteurs appris sur TOUTES les editions, au format du
parametre `capacity_factors` de la definition d'alerte.

Usage :
    python scripts/replay_door_saturation.py
    python scripts/replay_door_saturation.py --capacity apprise --consec 2
    python scripts/replay_door_saturation.py --events "24H AUTOS" --json out.json
    python scripts/replay_door_saturation.py --print-factors

Base : titan si TITAN_ENV=prod, titan_dev sinon (--db pour forcer). Les
archives n'existent qu'en base de production.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient  # noqa: E402

import alert_engine as AE  # noqa: E402
import door_security as DS  # noqa: E402

SECU_THRESHOLDS = (0.6, 0.7, 0.8, 0.9, 1.0, 1.2)

PARIS = ZoneInfo("Europe/Paris")
YEAR = 2026
STEP = timedelta(minutes=5)

# Fiche de flux REACTIVE, lue sur le seul titre (text / text_full) : le champ
# comment porte la chronologie, ou n'importe quel mot finit par apparaitre.
# Verifie a la main sur les 6 editions 2026 : sans ces exclusions, bagarres,
# "prise de service filtrage" (planning, pas une reaction) et nuisances
# sonores passaient pour des saturations.
FICHE_FLUX_RE = re.compile(
    r"RENFORT\w* (?:\w+ ){0,3}(?:FILTRAGE|SCAN|FLUX|PORTE|ENTREE|ACCES|PALPATION)"
    r"|(?:FILTRAGE|SCAN|FLUX|PALPATION) (?:\w+ ){0,2}RENFORT"
    r"|AFFLUENCE|AFFLUX|CONGESTION|ENGORG|SATUR|EN ATTENTE|FILE D ATTENTE|BOUCHON|DESENGORG")
FICHE_EXCL_RE = re.compile(
    r"PRISE (?:DE|ET) |FIN DE SERVICE|PREPOSITION|PAUSE|NACELLE|TECHNIQUE|ELECTRI|RELEVE"
    r"|BAGARRE|VOL\b|MALAISE|AGRESS|NUISANCE|HERAS|BARRIERE|SORTIE")
# Mots qui ne distinguent pas une porte d'une autre
DOOR_GENERIC = {"PORTE", "ACCES", "PIETON", "VEHICULE", "AA", "P", "ENTREE", "BIS"}


# ---------------------------------------------------------------------------
# Acces base : archive en memoire, alertes actives neutralisees
# ---------------------------------------------------------------------------

class MemColl:
    """Collection d'archive en memoire, triee par tranche : le detecteur ne
    l'interroge que par intervalle de `tranche` (pas d'index en base)."""

    def __init__(self, docs):
        self.docs = sorted(docs, key=lambda d: d["tranche"])
        self.keys = [d["tranche"] for d in self.docs]

    def find(self, q=None, projection=None):
        r = (q or {}).get("tranche") or {}
        i = bisect.bisect_left(self.keys, r["$gte"]) if "$gte" in r else 0
        j = bisect.bisect_left(self.keys, r["$lt"]) if "$lt" in r else len(self.keys)
        return self.docs[i:j]

    def distinct(self, field, q=None):
        return sorted({d.get(field) for d in self.docs if d.get(field)})


class NoAlerts:
    """Le dedoublonnage est fait par le banc : le detecteur ne voit jamais
    d'alerte deja levee (sinon il faudrait simuler l'upsert)."""

    def find_one(self, *a, **k):
        return None


class ReplayDB:
    def __init__(self, real, archive_name, archive):
        self.real = real
        self.archive_name = archive_name
        self.archive = archive
        self._names = None

    def __getitem__(self, name):
        if name == self.archive_name:
            return self.archive
        if name == "cockpit_active_alerts":
            return NoAlerts()
        return self.real[name]

    def list_collection_names(self):
        if self._names is None:
            self._names = self.real.list_collection_names()
        return self._names


# ---------------------------------------------------------------------------
# Etat des portes reconstruit depuis l'archive (pour les references)
# ---------------------------------------------------------------------------

def door_state(docs, exclude, sens):
    """{porte: {"name", "buckets": {tranche: n}, "active": {tranche: {cp: kind}}}}"""
    out = {}
    for d in docs:
        name = d.get("gate_name") or ""
        key = AE._door_norm(name)
        if not name or key in exclude:
            continue
        t = d["tranche"]
        e = out.setdefault(key, {"name": name, "buckets": {}, "active": defaultdict(dict)})
        v = int(d.get("entrees") or 0)
        if sens == "total":
            v += int(d.get("sorties") or 0)
        e["buckets"][t] = e["buckets"].get(t, 0) + v
        if d.get("ok") or d.get("erreurs") or d.get("entrees"):
            cp = d.get("checkpoint_name") or "?"
            e["active"][t][cp] = AE._device_kind(cp)
    return out


def theo_capacity(e, end, dev_cap):
    devs = {}
    t = end - timedelta(minutes=60)
    while t < end:
        devs.update(e["active"].get(t, {}))
        t += STEP
    return float(sum(dev_cap.get(k, dev_cap["autre"]) for k in devs.values())), devs


def grid(docs):
    first = AE._floor5(min(d["tranche"] for d in docs)) + timedelta(minutes=60)
    last = AE._floor5(max(d["tranche"] for d in docs)) + STEP
    t = first
    while t <= last:
        yield t
        t += STEP


def learn_ratios(state, times, dev_cap, min_rate):
    """{porte: [debit/capacite theorique]} sur les fenetres chargees."""
    out = defaultdict(list)
    for key, e in state.items():
        for t in times:
            cur = AE.door_rate(e["buckets"], t, 15)
            if cur < min_rate:
                continue
            cap, _ = theo_capacity(e, t, dev_cap)
            if cap > 0:
                out[key].append(cur / cap)
    return out


def factor_from(samples, lo=0.5, hi=1.5, min_n=24):
    if len(samples) < min_n:
        return None
    s = sorted(samples)
    p98 = s[min(len(s) - 1, int(round(0.98 * (len(s) - 1))))]
    return round(min(max(p98, lo), hi), 3)


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

def label_debit(state, times, capfn, thr_pct, min_rate):
    """{porte: [instants au-dessus du seuil]} et debuts d'episodes.
    capfn(porte, t) -> capacite (/h) ou None (porte non evaluee)."""
    over, onsets = defaultdict(list), defaultdict(list)
    for key, e in state.items():
        prev = None
        for t in times:
            cap = capfn(key, t)
            if not cap:
                continue
            cur = AE.door_rate(e["buckets"], t, 15)
            if cur >= cap * thr_pct / 100.0 and cur >= min_rate:
                over[key].append(t)
                if prev is None or t - prev > timedelta(minutes=30):
                    onsets[key].append(t)
                prev = t
    return over, onsets


def label_pda_renfort(state, times, min_rate):
    """Debuts de renfort PDA sur porte a tripodes."""
    onsets = defaultdict(list)
    for key, e in state.items():
        last = None
        for t in times:
            tr = t - STEP  # derniere tranche complete
            now_devs = e["active"].get(tr, {})
            pdas = {cp for cp, k in now_devs.items() if k == "pda"}
            if not pdas:
                continue
            _, devs60 = theo_capacity(e, t, {"tripode": 1, "pda": 1, "autre": 1})
            if not any(k == "tripode" for k in devs60.values()):
                continue
            seen = set()
            u = tr - timedelta(minutes=90)
            while u < tr:
                seen.update(cp for cp, k in e["active"].get(u, {}).items() if k == "pda")
                u += STEP
            if pdas - seen and AE.door_rate(e["buckets"], t, 15) >= min_rate:
                if last is None or t - last > timedelta(minutes=60):
                    onsets[key].append(t)
                last = t
    return onsets


def door_tokens(key):
    toks = set(key.split()) - DOOR_GENERIC
    return toks


def label_fiches(db, event, t0, t1, door_keys):
    """[(instant Paris naif, {portes citees}, texte)]"""
    utc0 = t0.replace(tzinfo=PARIS).astimezone(timezone.utc).replace(tzinfo=None)
    utc1 = t1.replace(tzinfo=PARIS).astimezone(timezone.utc).replace(tzinfo=None)
    toks = {k: door_tokens(k) for k in door_keys}
    out = []
    for f in db["pcorg"].find({"event": event, "year": YEAR, "ts": {"$gte": utc0, "$lt": utc1}},
                              {"ts": 1, "text": 1, "text_full": 1}):
        blob = AE._door_norm(" ".join(str(f.get(k) or "") for k in ("text", "text_full")))
        if not FICHE_FLUX_RE.search(blob) or FICHE_EXCL_RE.search(blob):
            continue
        words = set(blob.split())
        doors = {k for k, tk in toks.items() if tk and tk <= words}
        if not doors:
            continue
        ts = f["ts"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append((ts.astimezone(PARIS).replace(tzinfo=None), doors, (f.get("text") or "")[:80]))
    return out


# ---------------------------------------------------------------------------
# Meteo : pluie horaire (meteo_previsions, un document par jour)
# ---------------------------------------------------------------------------

class Rain:
    def __init__(self, db):
        self.db = db
        self.cache = {}

    def mm(self, t):
        day = t.strftime("%Y-%m-%d")
        if day not in self.cache:
            doc = self.db["meteo_previsions"].find_one({"Date": day}) or {}
            hours = {}
            for h in doc.get("Heures") or []:
                try:
                    hours[int(str(h.get("Heure"))[:2])] = float(h.get("Pluviométrie (mm)") or h.get("Pluviometrie (mm)") or 0)
                except (TypeError, ValueError):
                    pass
            self.cache[day] = hours
        return self.cache[day].get(t.hour)

    def wet(self, t):
        """Pluie dans l'heure ou l'heure precedente ; None si inconnu."""
        a, b = self.mm(t), self.mm(t - timedelta(hours=1))
        if a is None and b is None:
            return None
        return (a or 0) > 0 or (b or 0) > 0


# ---------------------------------------------------------------------------
# Rejeu
# ---------------------------------------------------------------------------

def archive_list(db, events):
    out = []
    for c in sorted(db.list_collection_names()):
        if not c.startswith("hsh_archive_tx_") or not c.endswith("_%d" % YEAR):
            continue
        d = db[c].find_one({}, {"evenement": 1})
        ev = (d or {}).get("evenement")
        if ev and (not events or ev in events):
            out.append((c, ev))
    return out


def run_event(db, coll, event, params, factors, args, rain):
    proj = {"gate_name": 1, "checkpoint_name": 1, "tranche": 1, "entrees": 1,
            "sorties": 1, "ok": 1, "erreurs": 1}
    docs = [d for d in db[coll].find({}, proj) if isinstance(d.get("tranche"), datetime)]
    for d in docs:
        d["tranche"] = d["tranche"].replace(tzinfo=None)
    if not docs:
        return None
    mem = MemColl(docs)
    rdb = ReplayDB(db, coll, mem)
    dev_cap = dict(AE.DOOR_SAT_DEFAULTS["device_capacity_h"])
    exclude = {AE._door_norm(x) for x in AE.DOOR_SAT_SERVICES}
    sens = params.get("sens", "entrees")
    thr = float(params["threshold_pct"])
    min_rate = float(params["min_rate"])
    horizon = int(params["horizon_min"])

    definition = {"slug": "replay", "params": dict(params, capacity_factors=factors)}
    times = list(grid(docs))

    # 1. Detecteur, cycle par cycle
    raw = defaultdict(dict)   # porte -> {t: hit}
    for t in times:
        i = bisect.bisect_left(mem.keys, t - timedelta(minutes=20))
        if i >= len(mem.keys) or mem.keys[i] >= t:
            continue  # rien de collecte : le detecteur rendrait None
        now = t.replace(tzinfo=PARIS).astimezone(timezone.utc)
        res = AE.detect_door_saturation_forecast(definition, {
            "now": now, "db": rdb, "event": event, "year": YEAR,
            "door_tx_source": {"collection": coll, "event": event, "year": YEAR}})
        for a in res or []:
            ad = a["actionData"]
            raw[ad["door_key"]][t] = {
                "t": t, "door": ad["door_key"], "ahead": ad["minutes_ahead"],
                "method": ad["method"], "cur": ad["current_rate"],
                "pred": ad["predicted_rate"], "cap": ad["capacity"],
                "need": ad.get("agents_needed") or 0, "agents": ad.get("security_agents")}

    # 2. Confirmation sur N cycles consecutifs + dedoublonnage par porte,
    #    meme regle que le detecteur en production (cockpit_active_alerts) :
    #    mode securite = une alerte par episode (renotify_min), sauf
    #    aggravation du besoin en agents.
    secu_mode = params.get("capacity_mode") == "securite"
    renotify = timedelta(minutes=int(params.get("renotify_min") or args.dedup)) if secu_mode else None
    alerts = []
    for key, hits in raw.items():
        last = None
        for t in sorted(hits):
            ok = all((t - STEP * k) in hits for k in range(1, args.consec))
            if not ok:
                continue
            if last is not None:
                if t - last["t"] < timedelta(minutes=args.dedup):
                    continue
                worse_ok = params.get("renotify_on_worse") and hits[t]["need"] > last["need"]
                if secu_mode and t - last["t"] < renotify and not worse_ok:
                    continue
            alerts.append(hits[t])
            last = hits[t]

    # 3. References
    state = door_state(docs, exclude, sens)
    gate_names = sorted({d.get("gate_name") for d in docs if d.get("gate_name")})
    posts, _unmatched = DS.load_door_posts(db, event, YEAR, gate_names)
    staffing = {AE._door_norm(g): v for g, v in
                DS.load_security_staffing(db, event, YEAR, posts).items()}
    agent_rate = float(params.get("agent_rate_h") or DS.AGENT_RATE_H)

    if params.get("capacity_mode") == "securite":
        def capfn(key, t):
            n = DS.agents_at(staffing, key, t)
            return n * agent_rate if n else None
    else:
        def capfn(key, t):
            cap, _ = theo_capacity(state[key], t, dev_cap)
            return cap * factors.get(key, 1.0) if cap > 0 else None
    over, deb_on = label_debit(state, times, capfn, thr, min_rate)
    pda_on = label_pda_renfort(state, times, min_rate)
    fiches = label_fiches(db, event, times[0], times[-1], list(state))

    def within(ts_list, lo, hi):
        j = bisect.bisect_left(ts_list, lo)
        return j < len(ts_list) and ts_list[j] <= hi

    fiche_by_door = defaultdict(list)
    for ts, doors, _ in fiches:
        for k in doors:
            fiche_by_door[k].append(ts)
    for k in fiche_by_door:
        fiche_by_door[k].sort()

    for a in alerts:
        t, k = a["t"], a["door"]
        a["kind"] = "constate" if a["ahead"] == 0 else "prevu"
        a["L1"] = within(over.get(k, []), t, t + timedelta(minutes=horizon + 10))
        a["L2"] = within(pda_on.get(k, []), t - STEP, t + timedelta(minutes=60))
        a["L3"] = within(fiche_by_door.get(k, []), t - STEP, t + timedelta(minutes=90))
        a["wet"] = rain.wet(t)

    alerts_by_door = defaultdict(list)
    for a in alerts:
        alerts_by_door[a["door"]].append(a["t"])
    for k in alerts_by_door:
        alerts_by_door[k].sort()

    def recall(onsets_by_door, before_min, after_min=5):
        found, leads, total, wet_hits = 0, [], 0, []
        for k, ons in onsets_by_door.items():
            for o in ons:
                total += 1
                ts = alerts_by_door.get(k, [])
                j = bisect.bisect_left(ts, o - timedelta(minutes=before_min))
                if j < len(ts) and ts[j] <= o + timedelta(minutes=after_min):
                    found += 1
                    leads.append((o - ts[j]).total_seconds() / 60.0)
                wet_hits.append(rain.wet(o))
        return {"total": total, "found": found, "leads": leads, "wet": wet_hits}

    # Rappel des fiches compte PAR FICHE : "Porte Nord" cite 4 portes, une
    # alerte sur l'une d'elles suffit.
    rec3 = {"total": 0, "found": 0, "leads": [], "wet": []}
    for ts, doors, _ in fiches:
        rec3["total"] += 1
        best = None
        for k in doors:
            tl = alerts_by_door.get(k, [])
            j = bisect.bisect_left(tl, ts - timedelta(minutes=90))
            if j < len(tl) and tl[j] <= ts + STEP:
                best = tl[j] if best is None else min(best, tl[j])
        if best is not None:
            rec3["found"] += 1
            rec3["leads"].append((ts - best).total_seconds() / 60.0)
        rec3["wet"].append(rain.wet(ts))

    # Occupation de la porte (debit 15 min / capacite theorique) au moment
    # des renforts PDA et des fiches, et 30 min avant : a quel niveau de
    # charge le terrain reagit-il reellement ?
    def util(key, t):
        e = state.get(key)
        if not e:
            return None
        cap, _ = theo_capacity(e, t, dev_cap)
        return AE.door_rate(e["buckets"], t, 15) / cap if cap > 0 else None

    occ = {"L2": [], "L2_m30": [], "L3": [], "L3_m30": []}
    for k, ons in pda_on.items():
        for o in ons:
            for lab, dt in (("L2", 0), ("L2_m30", 30)):
                u = util(k, AE._floor5(o) - timedelta(minutes=dt))
                if u is not None:
                    occ[lab].append(u)
    for ts, doors, _ in fiches:
        for lab, dt in (("L3", 0), ("L3_m30", 30)):
            us = [u for u in (util(k, AE._floor5(ts) - timedelta(minutes=dt)) for k in doors) if u is not None]
            if us:
                occ[lab].append(max(us))

    # --- Capacite de SECURITE (agents prevus au planning x 350/h) ---------
    # Postes SECURITE pietons de la bible -> portes, agents du planning
    fam_buckets ={k: state[k]["buckets"] for k in staffing if k in state}

    def usec(fam, t):
        cap = DS.security_capacity(staffing, fam, t)
        if not cap or fam not in fam_buckets:
            return None
        return AE.door_rate(fam_buckets[fam], t, 15) / cap

    secu = {"loaded": [], "fiche": [], "fiche_m30": [], "rules": {}}
    series = defaultdict(dict)  # fam -> {t: u}
    for fam in fam_buckets:
        for t in times:
            u = usec(fam, t)
            if u is None:
                continue
            series[fam][t] = u
            if AE.door_rate(fam_buckets[fam], t, 15) >= min_rate:
                secu["loaded"].append(u)
                secu.setdefault("by_fam", defaultdict(list))[fam].append(u)
    fiche_fams = []
    for ts, doors, txt in fiches:
        fams = set(doors) & set(fam_buckets)
        if not fams:
            continue
        fiche_fams.append((ts, fams))
        for lab, dt in (("fiche", 0), ("fiche_m30", 30)):
            us = [u for u in (usec(f, AE._floor5(ts) - timedelta(minutes=dt)) for f in fams) if u is not None]
            if us:
                secu[lab].append(max(us))
    # Regle "debit >= seuil x capacite securite", dedoublonnee 30 min
    for thr_s in SECU_THRESHOLDS:
        al = []
        for fam, s in series.items():
            last = None
            for t in sorted(s):
                if s[t] >= thr_s and AE.door_rate(fam_buckets[fam], t, 15) >= min_rate:
                    if last is None or t - last >= timedelta(minutes=args.dedup):
                        al.append((fam, t))
                    last = t
        tp = sum(1 for fam, t in al if any(
            fam in fs and t - STEP <= ts <= t + timedelta(minutes=90) for ts, fs in fiche_fams))
        rec = sum(1 for ts, fs in fiche_fams if any(
            fam in fs and ts - timedelta(minutes=90) <= t <= ts + STEP for fam, t in al))
        secu["rules"][thr_s] = {"alerts": len(al), "tp": tp, "fiches": len(fiche_fams), "found": rec}
    secu["families_staffed"] = sorted(staffing)
    secu["fiche_doors"] = [sorted(fs) for _, fs in fiche_fams]

    return {
        "event": event, "alerts": alerts, "doors": len(state), "secu": secu,
        "rec_L1": recall(deb_on, 45, 0),
        "rec_L2": recall(pda_on, 60),
        "rec_L3": rec3,
        "occ": occ,
        "pda_onsets": [(k, o) for k, ons in pda_on.items() for o in ons],
        "fiches": fiches,
        "active_wet": [rain.wet(t) for t in times if any(
            AE.door_rate(e["buckets"], t, 15) >= min_rate for e in state.values())],
    }


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------

def pct(a, b):
    return "%5.1f %%" % (100.0 * a / b) if b else "   - "


def med(xs):
    return "%4.0f min" % statistics.median(xs) if xs else "    -   "


def report(results, title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    tot = defaultdict(int)
    agg_rec = {k: {"total": 0, "found": 0, "leads": []} for k in ("rec_L1", "rec_L2", "rec_L3")}
    print("%-18s %6s %6s | %-15s %-15s %-15s" % ("edition", "alertes", "prevu",
          "L1 debit", "L2 renfort PDA", "L3 fiche"))
    for r in results:
        al = r["alerts"]
        pv = [a for a in al if a["kind"] == "prevu"]
        tot["al"] += len(al)
        tot["pv"] += len(pv)
        for L in ("L1", "L2", "L3"):
            tot[L] += sum(1 for a in al if a[L])
            tot[L + "pv"] += sum(1 for a in pv if a[L])
        tot["ext"] += sum(1 for a in al if a["L2"] or a["L3"])
        for k in agg_rec:
            for f in ("total", "found"):
                agg_rec[k][f] += r[k][f]
            agg_rec[k]["leads"] += r[k]["leads"]
        print("%-18s %6d %6d | %-15s %-15s %-15s" % (
            r["event"][:18], len(al), len(pv),
            pct(sum(1 for a in pv if a["L1"]), len(pv)),
            pct(sum(1 for a in al if a["L2"]), len(al)),
            pct(sum(1 for a in al if a["L3"]), len(al))))
    print("-" * 78)
    print("PRECISION (alertes suivies d'une reference sur la meme porte)")
    print("  prevues confirmees par le debit (L1) : %s  (%d/%d)" % (pct(tot["L1pv"], tot["pv"]), tot["L1pv"], tot["pv"]))
    print("  toutes, renfort PDA (L2)            : %s  (%d/%d)" % (pct(tot["L2"], tot["al"]), tot["L2"], tot["al"]))
    print("  toutes, fiche main courante (L3)    : %s  (%d/%d)" % (pct(tot["L3"], tot["al"]), tot["L3"], tot["al"]))
    print("  toutes, L2 ou L3                    : %s  (%d/%d)" % (pct(tot["ext"], tot["al"]), tot["ext"], tot["al"]))
    print("RAPPEL (references precedees d'une alerte) et AVANCE mediane")
    labels = {"rec_L1": "episodes de debit (anticipes)", "rec_L2": "renforts PDA",
              "rec_L3": "fiches de flux"}
    for k, lab in labels.items():
        a = agg_rec[k]
        print("  %-30s : %s  (%d/%d)  avance %s" % (lab, pct(a["found"], a["total"]),
              a["found"], a["total"], med(a["leads"])))

    print("OCCUPATION DE LA PORTE (debit 15 min / capacite theorique) : q25 / mediane / q75")
    for lab, txt in (("L2", "au renfort PDA"), ("L2_m30", "30 min avant le renfort PDA"),
                     ("L3", "a la fiche de flux"), ("L3_m30", "30 min avant la fiche")):
        xs = sorted(u for r in results for u in r["occ"][lab])
        if len(xs) >= 4:
            q = statistics.quantiles(xs, n=4)
            print("  %-28s : %3.0f %% / %3.0f %% / %3.0f %%  (n=%d)" % (txt, q[0] * 100, q[1] * 100, q[2] * 100, len(xs)))
        else:
            print("  %-28s : trop peu de cas (n=%d)" % (txt, len(xs)))

    print("CAPACITE DE SECURITE (debit porte / agents prevus x %d/h)" % DS.AGENT_RATE_H)
    for r in results:
        print("  %-18s familles avec agents au planning : %s" % (r["event"][:18], ", ".join(r["secu"]["families_staffed"]) or "aucune"))
    for lab, txt in (("loaded", "tout le temps charge"), ("fiche", "a la fiche de flux"),
                     ("fiche_m30", "30 min avant la fiche")):
        xs = sorted(u for r in results for u in r["secu"][lab])
        if len(xs) >= 4:
            q = statistics.quantiles(xs, n=4)
            print("  %-28s : %3.0f %% / %3.0f %% / %3.0f %%  (n=%d)" % (txt, q[0] * 100, q[1] * 100, q[2] * 100, len(xs)))
        else:
            print("  %-28s : trop peu de cas (n=%d)" % (txt, len(xs)))
    byf = defaultdict(list)
    fic = defaultdict(int)
    for r in results:
        for f, us in (r["secu"].get("by_fam") or {}).items():
            byf[f] += us
        for fs in r["secu"]["fiche_doors"]:
            for f in fs:
                fic[f] += 1
    print("  par porte (temps charge) : mediane, part > 100 %, fiches de flux")
    for f in sorted(byf, key=lambda k: -statistics.median(byf[k])):
        us = byf[f]
        print("    %-15s %4.0f %%   %5.1f %%   %3d fiches   (n=%d)" % (
            f, statistics.median(us) * 100, 100.0 * sum(1 for u in us if u > 1) / len(us), fic[f], len(us)))
    print("  regle 'debit >= seuil x capacite securite' (alerte suivie d'une fiche <= 90 min) :")
    for thr_s in SECU_THRESHOLDS:
        a = sum(r["secu"]["rules"][thr_s]["alerts"] for r in results)
        tp = sum(r["secu"]["rules"][thr_s]["tp"] for r in results)
        fi = sum(r["secu"]["rules"][thr_s]["fiches"] for r in results)
        fo = sum(r["secu"]["rules"][thr_s]["found"] for r in results)
        print("    seuil %3.0f %% : %4d alertes, precision %s, rappel fiches %s (%d/%d)" % (
            thr_s * 100, a, pct(tp, a), pct(fo, fi), fo, fi))

    # Meteo
    wet_al = [a for r in results for a in r["alerts"] if a["wet"] is not None]
    act = [w for r in results for w in r["active_wet"] if w is not None]
    pda_w = [w for r in results for w in r["rec_L2"]["wet"] if w is not None]
    print("METEO (pluie dans l'heure ou l'heure precedente)")
    print("  part du temps charge sous la pluie  : %s" % pct(sum(act), len(act)))
    print("  part des renforts PDA sous la pluie : %s  (%d/%d)" % (pct(sum(pda_w), len(pda_w)), sum(pda_w), len(pda_w)))
    for wet in (True, False):
        sub = [a for a in wet_al if a["wet"] is wet]
        print("  precision L2|L3 %-5s             : %s  (%d alertes)" % (
            "pluie" if wet else "sec", pct(sum(1 for a in sub if a["L2"] or a["L3"]), len(sub)), len(sub)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--events", nargs="*", default=None)
    ap.add_argument("--capacity", choices=("theorique", "apprise", "securite"), default="theorique",
                    help="securite = agents prevus (bible + planning) x --agent-rate")
    ap.add_argument("--agent-rate", type=float, default=None, help="personnes/h/agent (defaut 350)")
    ap.add_argument("--no-escalate", action="store_true", help="(defaut) pas de re-alerte sur aggravation")
    ap.add_argument("--escalate", action="store_true", help="re-alerte si le besoin en agents grandit")
    ap.add_argument("--renotify", type=int, default=None, help="minutes entre deux episodes (securite)")
    ap.add_argument("--consec", type=int, default=1, help="cycles consecutifs exiges")
    ap.add_argument("--dedup", type=int, default=30, help="minutes entre deux alertes d'une porte")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--horizon", type=int, default=None)
    ap.add_argument("--min-rate", type=float, default=None)
    ap.add_argument("--trend", action="store_true", help="active trend_fallback")
    ap.add_argument("--print-factors", action="store_true")
    ap.add_argument("--json", default=None, help="exporte alertes et references")
    args = ap.parse_args()

    AE.log.setLevel("WARNING")  # une ligne par alerte simulee sinon
    env = os.getenv("TITAN_ENV", "dev").strip().lower()
    dbname = args.db or ("titan" if env in {"prod", "production"} else "titan_dev")
    db = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))[dbname]

    params = dict(AE.DOOR_SAT_DEFAULTS)
    if args.threshold is not None:
        params["threshold_pct"] = args.threshold
    if args.horizon is not None:
        params["horizon_min"] = args.horizon
    if args.min_rate is not None:
        params["min_rate"] = args.min_rate
    if args.trend:
        params["trend_fallback"] = True
    if args.capacity == "securite":
        params["capacity_mode"] = "securite"
    if args.agent_rate is not None:
        params["agent_rate_h"] = args.agent_rate
    if args.escalate:
        params["renotify_on_worse"] = True
    if args.renotify is not None:
        params["renotify_min"] = args.renotify

    archives = archive_list(db, args.events)
    if not archives:
        print("Aucune archive hsh_archive_tx_*_%d dans %s" % (YEAR, dbname))
        return 1

    dev_cap = dict(AE.DOOR_SAT_DEFAULTS["device_capacity_h"])
    exclude = {AE._door_norm(x) for x in AE.DOOR_SAT_SERVICES}
    ratios = {}
    if args.capacity == "apprise" or args.print_factors:
        proj = {"gate_name": 1, "checkpoint_name": 1, "tranche": 1, "entrees": 1,
                "sorties": 1, "ok": 1, "erreurs": 1}
        for coll, ev in archives:
            docs = [d for d in db[coll].find({}, proj) if isinstance(d.get("tranche"), datetime)]
            for d in docs:
                d["tranche"] = d["tranche"].replace(tzinfo=None)
            st = door_state(docs, exclude, params.get("sens", "entrees"))
            ratios[ev] = learn_ratios(st, list(grid(docs)), dev_cap, float(params["min_rate"]))

    if args.print_factors:
        allr = defaultdict(list)
        for ev in ratios:
            for k, v in ratios[ev].items():
                allr[k] += v
        facs = {k: factor_from(v) for k, v in sorted(allr.items())}
        print(json.dumps({k: v for k, v in facs.items() if v is not None}, indent=1, ensure_ascii=False))
        print("# portes sans assez de fenetres chargees (capacite theorique conservee) : %s"
              % ", ".join(k for k, v in facs.items() if v is None))
        return 0

    rain = Rain(db)
    results = []
    for coll, ev in archives:
        factors = {}
        if args.capacity == "apprise":
            loo = defaultdict(list)
            for other, rs in ratios.items():
                if other == ev:
                    continue
                for k, v in rs.items():
                    loo[k] += v
            factors = {k: f for k, f in ((k, factor_from(v)) for k, v in loo.items()) if f is not None}
        print("rejeu %-18s (%d facteurs de capacite)..." % (ev, len(factors)), flush=True)
        r = run_event(db, coll, ev, params, factors, args, rain)
        if r:
            results.append(r)

    title = "capacite=%s  consec=%d  seuil=%s %%  horizon=%s min  tendance=%s" % (
        args.capacity, args.consec, params["threshold_pct"], params["horizon_min"],
        "oui" if params["trend_fallback"] else "non")
    report(results, title)

    if args.json:
        def ser(x):
            return x.isoformat() if isinstance(x, datetime) else str(x)
        dump = [{"event": r["event"], "alerts": r["alerts"],
                 "pda_onsets": r["pda_onsets"],
                 "fiches": [(ts, sorted(d), txt) for ts, d, txt in r["fiches"]]} for r in results]
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(dump, fh, default=ser, ensure_ascii=False, indent=1)
        print("export : %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
