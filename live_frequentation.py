"""Frequentation d'une edition depuis les ARCHIVES du controle d'acces live.

Source PRIMAIRE de la frequentation pour le RETEX et la vue Frequentation du
rapport de scans (option `source_priority` de scan_frequentation) ; l'import
Excel (historique_controle) reste le repli des editions sans archive live
(toutes celles <= 2025).

Fonctions pures et lectures Mongo, `db` toujours passe en argument, aucun
import Flask ni `app`.

CE QUE CE MODULE REJOUE, et pourquoi c'est le bon chiffre :

  presents = current - correction - vehicules_presents

exactement le calcul du dashboard (presents_etat, /api/live-controle/dashboard),
releve par releve (toutes les ~3 min), sur hsh_archive_compteurs_* au lieu de
data_access et hsh_archive_tx_* au lieu de hsh_transactions_agg :

  - vehicules cumules depuis la derniere REMISE A ZERO du compteur precedant
    chaque releve (sinon depuis l'activation du live-controle), jamais depuis
    minuit : presents_etat.detect_resets / vehicle_prefixes / solde_vehicules ;
  - releves anterieurs a la derniere remise a zero survenue avant la fin du
    jour de course ecartes : leur `current` est un fantome (LMC 2026 : 98 258
    le 28/06 avant la remise a zero de 17h56 ; 24H CAMIONS 2026 : 21 952 le
    22/09 avant celle du 23/09 04h33 UTC). Meme regle que presents_etat.pic_presents ;
  - un pic = le plus haut RELEVE du jour, jamais le max de la courbe 15 min.

Ce que les archives NE conservent PAS : le document ___GLOBAL___ (compteur
principal, corrections_compteurs / corrections_vehicules, activation). Pour
une edition archivee on prend donc l'Area 628 ENCEINTE GENERALE, aucune
correction, et l'activation estimee au debut de la serie continue de releves
(ecart < 2 h). Le ___GLOBAL___ courant n'est utilise que s'il designe
l'edition (meme evenement ET activation dans la fenetre).

PIEGES :
  - `tranche` (tx) est en HEURE DE PARIS etiquetee UTC, `timestamp` (compteurs)
    en vrai UTC naif : tout passe par presents_etat.to_tranche_label.
  - Le suffixe d'annee d'une archive est l'annee du clic << Archiver >>, pas
    celle de l'edition, et une archive peut contenir une autre edition
    (hsh_archive_compteurs_24H_MOTOS_2026 porte 2025 ; hsh_archive_tx_LE_MANS_CLASSIC_2026
    commence pendant les 24H AUTOS). Rien n'est lu hors de la FENETRE de
    l'edition (course - 10 j, course + 3 j), qui seule fait foi.
  - Les tx `entrees`/`sorties` comptent aussi les passages REFUSES (erreurs) :
    les totaux par porte en sont legerement majores. Les entrees de l'enceinte
    viennent donc du compteur (passages valides), les portes des tx.
  - Convention des creneaux 15 min : etiquetes a leur FIN, comme l'import
    Excel (<< 20:00 >> = etat a 20:00, passages 19:45-20:00).

Vehicules : exclus partout (convention du dashboard). Enfants et accredites
sont des PERSONNES : ils restent comptes.
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone

import presents_etat as pe

logger = logging.getLogger(__name__)

SOURCE = "live_controle"
PRESENTS_COMPTEUR = "compteur_moins_vehicules"
PRESENTS_SOLDE = "solde_scans"

DEFAULT_LOCATION = {"id": "628", "type": "Area", "name": "ENCEINTE GENERALE"}

WINDOW_BEFORE = timedelta(days=10)   # memes bornes que watch_peaks
WINDOW_AFTER = timedelta(days=3)
ACTIVATION_LOOKBACK = timedelta(days=7)
ACTIVATION_GAP = timedelta(hours=2)
QUARTER = timedelta(minutes=15)

TX_PREFIX = "hsh_archive_tx_"
CPT_PREFIX = "hsh_archive_compteurs_"
ST_PREFIX = "hsh_archive_structure_"
# Instantane du ___GLOBAL___ pris par hsh_archive_and_purge depuis le 29/09/2026
# (doc unique _id "global"). Les archives anterieures n'en ont pas.
GLOBAL_PREFIX = "hsh_archive_global_"

_TX_PROJ = {"_id": 0, "checkpoint_id": 1, "gate_name": 1, "tranche": 1,
            "entrees": 1, "sorties": 1, "entrees_vehicules": 1,
            "sorties_vehicules": 1, "erreurs": 1}


# ---------------------------------------------------------------------------
# Localisation des archives
# ---------------------------------------------------------------------------

def archive_tag(event, year):
    """Suffixe de app.py:hsh_archive_and_purge (`LE MANS CLASSIC` -> `LE_MANS_CLASSIC_2026`)."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(event).strip()) + "_" + str(int(year))


def event_names(db, event):
    """Libelles possibles d'un evenement : nom cockpit et sigle (LMC, SBK...),
    dans les deux sens (on peut recevoir le sigle)."""
    names = [event] if event else []
    try:
        ev = db["evenement"].find_one({"nom": event}, {"_id": 0, "nom": 1, "short": 1})
        if ev is None:
            ev = db["evenement"].find_one({"short": event}, {"_id": 0, "nom": 1, "short": 1})
        for cand in ((ev or {}).get("nom"), (ev or {}).get("short")):
            if cand and cand not in names:
                names.append(cand)
    except Exception:
        logger.warning("live_frequentation : lecture de `evenement` impossible", exc_info=True)
    return names


def archive_collections(existing, names, year):
    """{'tx': [...], 'compteurs': [...], 'structure': [...]} existants pour ces
    libelles. L'annee du suffixe est celle du clic : on essaie year et year+1."""
    out = {"tx": [], "compteurs": [], "structure": [], "global": []}
    for name in names:
        for y in (int(year), int(year) + 1):
            tag = archive_tag(name, y)
            for key, prefix in (("tx", TX_PREFIX), ("compteurs", CPT_PREFIX),
                                ("structure", ST_PREFIX), ("global", GLOBAL_PREFIX)):
                coll = prefix + tag
                if coll in existing and coll not in out[key]:
                    out[key].append(coll)
    return out


def _public_days(db, event, year):
    doc = (db["parametrages"].find_one({"event": event, "year": str(year)})
           or db["parametrages"].find_one({"event": event, "year": int(year)}) or {})
    gh = (doc.get("data") or {}).get("globalHoraires") or {}
    days = []
    for d in gh.get("dates") or []:
        raw = d.get("date") if isinstance(d, dict) else d
        if raw:
            days.append(str(raw)[:10])
    return sorted(set(days))


def race_moment(db, event, year):
    """(instant de course UTC conscient, origine) ou (None, None).

    watch_peaks.resolve_race_dt (garde sur l'annee + alias), sinon midi du
    premier jour public (SUPERBIKE 2026 n'a aucune date de course saisie)."""
    try:
        import watch_peaks
        dt = watch_peaks.resolve_race_dt(db, event, year)
        if dt is not None:
            return dt, "course"
    except Exception:
        logger.warning("live_frequentation : date de course %s %s illisible",
                       event, year, exc_info=True)
    days = _public_days(db, event, year)
    if days:
        d = date.fromisoformat(days[0])
        moment = datetime(d.year, d.month, d.day, 12, tzinfo=pe.TZ_PARIS)
        return moment.astimezone(timezone.utc), "premier_jour_public"
    return None, None


def find_sources(db, event, year):
    """Sources live d'une edition, ou None si elle n'a pas d'archive live.

    Une edition << a une archive live >> si une collection tx a son nom
    (ou le hsh_transactions_agg courant, si le live-controle compte encore
    cette edition) contient au moins une tranche dans SA fenetre.
    """
    try:
        year = int(year)
    except (TypeError, ValueError):
        return None
    names = event_names(db, event)
    if not names:
        return None
    try:
        existing = set(db.list_collection_names())
    except Exception:
        logger.warning("live_frequentation : inventaire des collections impossible", exc_info=True)
        return None
    colls = archive_collections(existing, names, year)
    global_doc = pe.read_global(db)
    live_ev = global_doc.get("evenement")
    if not colls["tx"] and live_ev not in names:
        return None   # chemin rapide : ni archive ni direct

    race_dt = race_src = None
    for name in names:   # parametrages est indexe sur le nom long, pas le sigle
        race_dt, race_src = race_moment(db, name, year)
        if race_dt is not None:
            break
    if race_dt is None:
        return None
    d0 = (race_dt - WINDOW_BEFORE).astimezone(timezone.utc).replace(tzinfo=None)
    d1 = (race_dt + WINDOW_AFTER).astimezone(timezone.utc).replace(tzinfo=None)

    tx = [(c, {}) for c in colls["tx"]]
    structure = [(c, {}) for c in colls["structure"]]
    compteurs = [(c, {}) for c in colls["compteurs"]] + [("data_access", {})]
    direct = False
    act = pe._utc_naif(global_doc.get("activation_timestamp"))
    if live_ev in names and act is not None and d0 <= act < d1:
        direct = True
        tx.append(("hsh_transactions_agg", {"evenement": live_ev}))
        structure.append(("hsh_structure", {"evenement": live_ev}))

    l0, l1 = pe.to_tranche_label(d0), pe.to_tranche_label(d1)
    found = []
    for coll, filtre in tx:
        q = dict(filtre)
        q["tranche"] = {"$gte": l0, "$lt": l1}
        try:
            if db[coll].find_one(q) is not None:
                found.append((coll, filtre))
        except Exception:
            logger.warning("live_frequentation : lecture de %s impossible", coll, exc_info=True)
    if not found:
        return None
    # Edition archivee : le ___GLOBAL___ archive (s'il existe) donne compteur
    # principal, corrections et activation exacts. Garde : son activation doit
    # tomber dans la fenetre de l'edition (le nom d'archive ment, cf. docstring).
    global_src = "direct" if direct else None
    if not direct:
        for coll in colls.get("global") or []:
            try:
                snap = db[coll].find_one({"_id": "global"})
            except Exception:
                logger.warning("live_frequentation : lecture de %s impossible", coll, exc_info=True)
                continue
            sact = pe._utc_naif((snap or {}).get("activation_timestamp"))
            if snap and sact is not None and d0 - ACTIVATION_LOOKBACK <= sact < d1:
                global_doc, global_src = snap, "archive"
                break
    return {
        "event": event, "year": year, "names": names,
        "race_dt": race_dt, "race_source": race_src,
        "race_date": race_dt.astimezone(pe.TZ_PARIS).date(),
        "window": (d0, d1), "tx": found, "compteurs": compteurs,
        "structure": structure, "direct": direct,
        "global": global_doc if global_src else None,
        "global_source": global_src,
    }


def has_live_archive(db, event, year):
    """L'edition a-t-elle une archive du controle d'acces live exploitable ?"""
    try:
        return find_sources(db, event, year) is not None
    except Exception:
        logger.warning("live_frequentation : has_live_archive(%s, %s)", event, year, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Lectures
# ---------------------------------------------------------------------------

def _read_counter(db, sources, location, lo, hi):
    """[(ts UTC naif, current, entries, exits)] tries, dedoublonnes par instant."""
    by_ts = {}
    q = {"requested_location_id": str(location["id"]),
         "timestamp": {"$gte": lo, "$lt": hi}}
    if location.get("type"):
        q["requested_location_type"] = location["type"]
    for coll, filtre in sources:
        qq = dict(filtre)
        qq.update(q)
        try:
            for s in db[coll].find(qq, {"_id": 0, "timestamp": 1, "current": 1,
                                        "entries": 1, "exits": 1}):
                ts = pe._utc_naif(s.get("timestamp"))
                if ts is None:
                    continue
                by_ts[ts] = (ts, pe._int(s.get("current"), None),
                             pe._int(s.get("entries"), None), pe._int(s.get("exits"), None))
        except Exception:
            logger.warning("live_frequentation : lecture de %s impossible", coll, exc_info=True)
    return [by_ts[k] for k in sorted(by_ts)]


def _read_tx(db, sources, l0, l1):
    """Tranches tx [l0, l1) (echelle Paris), dedoublonnees par (checkpoint, tranche)."""
    out = {}
    for coll, filtre in sources:
        q = dict(filtre)
        q["tranche"] = {"$gte": l0, "$lt": l1}
        try:
            for d in db[coll].find(q, _TX_PROJ):
                tr = pe._utc_naif(d.get("tranche"))
                if tr is None:
                    continue
                out[(d.get("checkpoint_id"), tr)] = d
        except Exception:
            logger.warning("live_frequentation : lecture de %s impossible", coll, exc_info=True)
    return list(out.values())


def _read_structure(db, sources):
    """(parents des checkpoints, {nom de gate normalise: id de son Area})."""
    docs = {}
    for coll, filtre in sources:
        try:
            for d in db[coll].find(dict(filtre), {"_id": 0, "location_id": 1, "location_type": 1,
                                                  "location_name": 1, "parent_area": 1,
                                                  "parent_venue": 1}):
                docs[(d.get("location_type"), d.get("location_id"))] = d
        except Exception:
            logger.warning("live_frequentation : lecture de %s impossible", coll, exc_info=True)
    parents = pe.parents_from_structure(d for (t, _), d in docs.items() if t == "Checkpoint")
    gates = {}
    for (t, _), d in docs.items():
        if t == "Gate" and (d.get("parent_area") or {}).get("id"):
            gates[_norm(d.get("location_name"))] = str(d["parent_area"]["id"])
    return parents, gates


def _norm(name):
    return re.sub(r"\s+", " ", str(name or "")).strip().upper()


# ---------------------------------------------------------------------------
# Calculs purs
# ---------------------------------------------------------------------------

def quarter_end(label):
    """Instant naif -> fin de son quart d'heure (convention de l'import :
    un creneau est etiquete a sa FIN). 20:07 -> 20:15, 20:00:00 -> 20:00."""
    base = label.replace(minute=(label.minute // 15) * 15, second=0, microsecond=0)
    return base if base == label else base + QUARTER


def tranche_quarter(tranche):
    """Tranche tx de 5 min [T, T+5) -> fin du quart d'heure qui la contient.
    19:45, 19:50, 19:55 -> 20:00 ; 20:00 -> 20:15."""
    return tranche.replace(minute=(tranche.minute // 15) * 15, second=0,
                           microsecond=0) + QUARTER


def _floor5(label):
    return label.replace(minute=(label.minute // 5) * 5, second=0, microsecond=0)


def estimate_activation(readings, first_in_window_idx, gap=ACTIVATION_GAP):
    """Debut de la serie continue de releves (ecart < gap) contenant le
    premier releve de la fenetre : l'activation du live-controle."""
    i = first_in_window_idx
    while i > 0 and readings[i][0] - readings[i - 1][0] <= gap:
        i -= 1
    return readings[i][0]


def replay_presents(readings, prefix, zone_id, window, cutoff_limit,
                    activation=None, correction=0, corr_veh=0):
    """Rejoue le calcul du dashboard sur des releves archives.

    readings : [(ts UTC naif, current, entries, exits)] tries, pouvant
    commencer AVANT la fenetre (pour dater l'activation et les remises a zero).
    prefix : presents_etat.vehicle_prefixes(...) ; cutoff_limit : UTC naif,
    fin du jour de course -- une remise a zero avant cette borne rend les
    releves anterieurs fantomes.

    Retourne dict(points=[(ts, present, current, veh, entries, exits, reset)],
    resets, valid_from, activation) ou None.
    """
    lo, hi = window
    idx = [i for i, r in enumerate(readings) if lo <= r[0] < hi]
    if not idx:
        return None
    resets = pe.detect_resets((r[0], r[2]) for r in readings)
    act_source = "global"
    if activation is None:
        activation = estimate_activation(readings, idx[0])
        act_source = "estimee"
    cut = [r for r in resets if lo <= r < cutoff_limit]
    valid_from = max(cut) if cut else None
    reset_set = set(resets)
    entry = prefix.get(str(zone_id))
    points = []
    for i in idx:
        ts, current, entries, exits = readings[i]
        if current is None:
            continue
        if valid_from is not None and ts < valid_from:
            continue
        seg = max([activation] + [r for r in resets if r <= ts])
        debut = _floor5(pe.to_tranche_label(seg))
        veh = pe.solde_vehicules(entry, debut, pe.to_tranche_label(ts), corr_veh)
        present = max(current - correction - veh, 0)
        points.append((ts, present, current, veh, entries, exits, ts in reset_set))
    return {"points": points, "resets": resets, "valid_from": valid_from,
            "activation": activation, "activation_source": act_source}


def aggregate_points(points, veh_flows):
    """Points rejoues -> (records 15 min, jours).

    records : [{date 'YYYY-MM-DDTHH:MM:00' (fin de quart d'heure, Paris),
    entree, sortie (cumuls hors vehicules), present (dernier releve)}].
    jours : {'YYYY-MM-DD': {peak, peak_at (Paris), e, s, n}} ; le pic est le
    plus haut RELEVE (strictement superieur : premier atteint, comme le
    dashboard). veh_flows : {fin de quart d'heure: (entrees_veh, sorties_veh)}.
    """
    quarters = {}
    days = {}
    prev_e = prev_s = None
    for ts, present, _cur, _veh, entries, exits, reset in points:
        label = pe.to_tranche_label(ts)
        q = quarter_end(label)
        slot = quarters.setdefault(q, {"e": 0, "s": 0, "present": None})
        # Deltas du compteur (passages valides), repartis a zero sur remise a zero.
        de = ds = 0
        if entries is not None:
            if reset or prev_e is None:
                de = entries if (reset and prev_e is not None) else 0
            else:
                de = max(entries - prev_e, 0)
            prev_e = entries
        if exits is not None:
            if reset or prev_s is None:
                ds = exits if (reset and prev_s is not None) else 0
            else:
                ds = max(exits - prev_s, 0)
            prev_s = exits
        slot["e"] += de
        slot["s"] += ds
        slot["present"] = present
        dkey = label.strftime("%Y-%m-%d")
        day = days.setdefault(dkey, {"peak": None, "peak_at": None, "e": 0, "s": 0, "n": 0})
        if day["peak"] is None or present > day["peak"]:
            day["peak"], day["peak_at"] = present, label
    records = []
    cum_e = cum_s = 0
    for q in sorted(quarters):
        ve, vs = veh_flows.get(q, (0, 0))
        e = max(quarters[q]["e"] - ve, 0)
        s = max(quarters[q]["s"] - vs, 0)
        cum_e += e
        cum_s += s
        dkey = q.strftime("%Y-%m-%d") if not (q.hour == 0 and q.minute == 0) else \
            (q - QUARTER).strftime("%Y-%m-%d")
        day = days.get(dkey)
        if day is not None:
            day["e"] += e
            day["s"] += s
            day["n"] += 1
        records.append({"date": q.isoformat(), "entree": cum_e, "sortie": cum_s,
                        "present": quarters[q]["present"]})
    return records, days


def solde_from_tx(enclosure_tx):
    """Repli sans releves de compteur (SUPERBIKE 2026) : solde cumule des
    passages personnes (entrees - sorties, hors vehicules) des portes de
    l'enceinte, comme l'import Excel. -> (records 15 min, jours)."""
    by_tr = {}
    for d in enclosure_tx:
        tr = pe._utc_naif(d.get("tranche"))
        e = (d.get("entrees") or 0) - (d.get("entrees_vehicules") or 0)
        s = (d.get("sorties") or 0) - (d.get("sorties_vehicules") or 0)
        agg = by_tr.setdefault(tr, [0, 0])
        agg[0] += max(e, 0)
        agg[1] += max(s, 0)
    quarters, days = {}, {}
    cum_e = cum_s = 0
    for tr in sorted(by_tr):
        e, s = by_tr[tr]
        cum_e += e
        cum_s += s
        at = tr + timedelta(minutes=5)          # etat a la fin de la tranche
        present = cum_e - cum_s
        quarters[quarter_end(at)] = (cum_e, cum_s, present)
        dkey = tr.strftime("%Y-%m-%d")
        day = days.setdefault(dkey, {"peak": None, "peak_at": None, "e": 0, "s": 0, "n": 0})
        day["e"] += e
        day["s"] += s
        if day["peak"] is None or present > day["peak"]:
            day["peak"], day["peak_at"] = present, at
    records = []
    for q in sorted(quarters):
        ce, cs, p = quarters[q]
        records.append({"date": q.isoformat(), "entree": ce, "sortie": cs, "present": p})
    for r in records:
        dkey = r["date"][:10]
        if dkey in days:
            days[dkey]["n"] += 1
    return records, days


# ---------------------------------------------------------------------------
# Edition
# ---------------------------------------------------------------------------

def load_live_edition(db, event, year, sources=None, with_door_series=False):
    """Donnees brutes d'une edition live, ou None si pas d'archive.

    Retourne un dict (voir to_edition pour la forme livree aux consommateurs).
    """
    src = sources or find_sources(db, event, year)
    if src is None:
        return None
    d0, d1 = src["window"]
    g = src.get("global") or {}
    location = DEFAULT_LOCATION
    correction = corr_veh = 0
    activation = None
    if g:   # ___GLOBAL___ courant (edition en direct) ou archive
        loc = pe.principal_location(g)
        if loc is not None:
            location = loc
        lid = str(location.get("id"))
        correction = pe._int((g.get("corrections_compteurs") or {}).get(lid))
        corr_veh = pe._int((g.get("corrections_vehicules") or {}).get(lid))
        activation = pe._utc_naif(g.get("activation_timestamp"))
    lid = str(location.get("id"))

    parents, gate_area = _read_structure(db, src["structure"])
    readings = _read_counter(db, src["compteurs"], location, d0 - ACTIVATION_LOOKBACK, d1)
    race_day_end = datetime.combine(src["race_date"] + timedelta(days=1), datetime.min.time(),
                                    tzinfo=pe.TZ_PARIS).astimezone(timezone.utc).replace(tzinfo=None)
    replay = None
    if readings:
        # tx a partir du plus ancien debut de cumul possible (activation estimee)
        idx = next((i for i, r in enumerate(readings) if d0 <= r[0] < d1), None)
        if idx is not None:
            act0 = activation or estimate_activation(readings, idx)
            tx_lo = _floor5(pe.to_tranche_label(min(act0, d0)))
        else:
            tx_lo = pe.to_tranche_label(d0)
    else:
        tx_lo = pe.to_tranche_label(d0)
    l0, l1 = pe.to_tranche_label(d0), pe.to_tranche_label(d1)
    tx_all = _read_tx(db, src["tx"], min(tx_lo, l0), l1)

    prefix = pe.vehicle_prefixes(tx_all, parents)
    if readings:
        replay = replay_presents(readings, prefix, lid, (d0, d1), race_day_end,
                                 activation=activation, correction=correction,
                                 corr_veh=corr_veh)

    # Portes de l'enceinte (tx de la fenetre) et vehicules par quart d'heure.
    tx_win = [d for d in tx_all if l0 <= pe._utc_naif(d.get("tranche")) < l1]
    gates, door_series, hourly_scans, veh_flows = {}, {}, {}, {}
    enclosure_tx = []
    for d in tx_win:
        tr = pe._utc_naif(d.get("tranche"))
        q = tranche_quarter(tr)
        cp_zones = parents.get(d.get("checkpoint_id")) or set()
        if lid in cp_zones:
            ve, vs = veh_flows.get(q, (0, 0))
            veh_flows[q] = (ve + (d.get("entrees_vehicules") or 0),
                            vs + (d.get("sorties_vehicules") or 0))
        name = _norm(d.get("gate_name"))
        area = gate_area.get(name)
        in_enclosure = (area == lid) if area is not None else (lid in cp_zones)
        if not in_enclosure or not name:
            continue
        enclosure_tx.append(d)
        ev, sv = d.get("entrees_vehicules") or 0, d.get("sorties_vehicules") or 0
        e = max((d.get("entrees") or 0) - ev, 0)
        s = max((d.get("sorties") or 0) - sv, 0)
        gt = gates.setdefault(name, {"name": name, "entrees": 0, "sorties": 0,
                                     "entrees_vehicules": 0, "sorties_vehicules": 0,
                                     "erreurs": 0})
        gt["entrees"] += e
        gt["sorties"] += s
        gt["entrees_vehicules"] += ev
        gt["sorties_vehicules"] += sv
        gt["erreurs"] += d.get("erreurs") or 0
        hk = tr.replace(minute=0)
        hourly_scans[hk] = hourly_scans.get(hk, 0) + (d.get("entrees") or 0) + (d.get("sorties") or 0)
        if with_door_series:
            ser = door_series.setdefault(name, {})
            cur = ser.setdefault(q, [0, 0])
            cur[0] += e
            cur[1] += s

    # Des releves hors de la periode ou les portes scannent ne decrivent pas
    # cette edition (SUPERBIKE 2026 : releves du 29 au 31/03, scans les 4 et
    # 5/04) : on retombe alors sur le solde des scans.
    if replay and replay["points"] and enclosure_tx:
        t_min = min(pe._utc_naif(d.get("tranche")) for d in enclosure_tx)
        t_max = max(pe._utc_naif(d.get("tranche")) for d in enclosure_tx) + timedelta(minutes=5)
        if not any(t_min <= pe.to_tranche_label(p[0]) <= t_max for p in replay["points"]):
            logger.info("live_frequentation : %s %s, releves du compteur hors de la periode "
                        "des scans, repli sur le solde des scans", event, year)
            replay = None

    if replay and replay["points"]:
        method = PRESENTS_COMPTEUR
        records, days = aggregate_points(replay["points"], veh_flows)
    elif enclosure_tx:
        method = PRESENTS_SOLDE
        records, days = solde_from_tx(enclosure_tx)
    else:
        return None

    # Jours de la fenetre ou le compteur relevait mais n'etait pas exploitable
    # (avant la remise a zero) : non mesures, jamais zero.
    phantom_days = set()
    if replay and replay["valid_from"] is not None:
        for r in readings:
            if d0 <= r[0] < replay["valid_from"]:
                phantom_days.add(pe.to_tranche_label(r[0]).strftime("%Y-%m-%d"))

    hourly_presence = {}
    for rec in records:
        # Etat a la fin de l'heure H = dernier point de l'heure H (cle 'YYYY-MM-DDTHH').
        q = datetime.fromisoformat(rec["date"])
        hk = (q - timedelta(seconds=1)).strftime("%Y-%m-%dT%H")
        hourly_presence[hk] = rec["present"]

    # Solde deja affiche au premier releve exploitable : sans remise a zero
    # observee, c'est un reliquat des evenements precedents qui majore TOUS
    # les presents de l'edition (24H MOTOS 2026 : 8 916 au premier releve du
    # 13/04, ~7 300 de plus que l'import Excel sur chaque pic journalier).
    # Le dashboard l'affichait tel quel ; le rejeu aussi, mais le signale.
    initial = None
    if method == PRESENTS_COMPTEUR:
        p0 = replay["points"][0]
        initial = {"at": pe.to_tranche_label(p0[0]).isoformat(), "current": p0[2],
                   "entries": p0[4], "reset_in_window": replay["valid_from"] is not None}

    out = {
        "event": event,
        "year": int(year),
        "source": SOURCE,
        "presents_method": method,
        "initial_counter": initial,
        "race_date": src["race_date"],
        "race_source": src["race_source"],
        "location": {"id": lid, "type": location.get("type"), "name": location.get("name")},
        "records": records,
        "days_raw": days,
        "phantom_days": sorted(phantom_days - set(k for k, v in days.items() if v["peak"] is not None)),
        "hourly_presence": hourly_presence,
        "hourly_scans": {k.strftime("%Y-%m-%dT%H"): v for k, v in hourly_scans.items()},
        "gates": sorted(gates.values(), key=lambda x: -x["entrees"]),
        "resets": [pe.to_tranche_label(r).isoformat() for r in (replay or {}).get("resets", [])
                   if d0 - ACTIVATION_LOOKBACK <= r < d1],
        "valid_from": (pe.to_tranche_label(replay["valid_from"]).isoformat()
                       if replay and replay.get("valid_from") else None),
        "activation": (pe.to_tranche_label(replay["activation"]).isoformat()
                       if replay and replay.get("activation") else None),
        "activation_source": (replay or {}).get("activation_source"),
        "corrections": {"compteur": correction, "vehicules": corr_veh,
                        "appliquees": bool(src.get("direct"))},
        "collections": sorted({c for c, _ in src["tx"]} | {c for c, _ in src["compteurs"]}),
        "readings": len((replay or {}).get("points") or []),
        "tx_docs": len(tx_win),
        "window": [pe.to_tranche_label(d0).isoformat(), pe.to_tranche_label(d1).isoformat()],
    }
    if with_door_series:
        out["door_series"] = {
            name: [{"date": q.isoformat(), "entree": v[0], "sortie": v[1]}
                   for q, v in sorted(ser.items())]
            for name, ser in door_series.items()}
    return out


def enclosure_records(raw):
    """(records, granularite) au format de scan_frequentation.enclosure_series."""
    return (raw or {}).get("records") or [], "15min"


def _units(db, raw, geo_index):
    """Inventaire des portes (gates) de l'enceinte, rattache aux features geo
    par le resolveur de l'import (variantes orthographiques, corrections)."""
    names = [g["name"] for g in raw.get("gates") or [] if g["entrees"] or g["sorties"]]
    fids = {}
    if names:
        try:
            import scan_staffing
            fids = scan_staffing.resolve_units_for_names(
                db, names, kinds={n: "porte" for n in names},
                event=raw.get("event"), year=raw.get("year"))
        except Exception:
            logger.warning("live_frequentation : rattachement des portes impossible", exc_info=True)
    out = []
    for n in names:
        fid = fids.get(n)
        out.append({"name": n, "_id_feature": str(fid) if fid else None,
                    "category": geo_index.get(str(fid)) if fid else "sans_lieu"})
    return sorted(out, key=lambda u: u["name"])


def to_edition(db, raw, race, is_current, geo_index=None):
    """Edition au format de scan_frequentation.load_editions."""
    import scan_frequentation as sf
    if geo_index is None:
        geo_index = sf._geo_index(db)
    days = []
    all_days = set(raw["days_raw"]) | set(raw.get("phantom_days") or [])
    for dkey in sorted(all_days):
        d = date.fromisoformat(dkey)
        info = raw["days_raw"].get(dkey)
        peak = info["peak"] if info else None
        moved = bool(info and (info["e"] or info["s"]))
        # Aucun passage de la journee : le compteur est fige (collecte coupee
        # apres l'evenement), son solde n'est qu'un report de la veille --
        # 24H AUTOS 2026 le 15/06 : 91 077 << presents >> sans un seul passage.
        # Une absence de mesure, pas une frequentation.
        measured = moved
        reason = None
        if info and not moved and peak:
            reason = "compteur_fige"
        elif not info or not moved:
            reason = ("compteur_non_remis_a_zero"
                      if dkey in (raw.get("phantom_days") or []) else "aucune_mesure")
        days.append({
            "date": dkey,
            "offset": (d - race).days if race else None,
            "entrees": info["e"] if measured else None,
            "sorties": info["s"] if measured else None,
            "peak_present": peak if measured else None,
            "peak_hour": info["peak_at"].strftime("%H:%M") if measured and info["peak_at"] else None,
            "hours": info["n"] if info else 0,
            "measured": measured,
        })
        if not measured:
            days[-1]["unmeasured_reason"] = reason
    units = _units(db, raw, geo_index)
    freq_doc = {"data": [{"date": k + ":00:00", "present": v}
                         for k, v in sorted(raw["hourly_presence"].items())]}
    portes_doc = {"doors": [{"name": "enceinte", "scans": [
        {"timestamp": datetime.strptime(k, "%Y-%m-%dT%H"), "scan_count": v}
        for k, v in sorted(raw["hourly_scans"].items())]}]}
    return {
        "year": raw["year"],
        "race_date": race.isoformat(),
        "is_current": is_current,
        "days": days,
        "hourly": sf.presence_series(raw["records"], race),
        "granularity": "15min",
        "source": SOURCE,
        "doors": len(units) or None,
        "units": units,
        "access_control": sf.access_control(freq_doc, portes_doc),
        # Specifique live : methode, base du pic, perimetre, tracabilite.
        "presents_method": raw["presents_method"],
        "peak_basis": "releve" if raw["presents_method"] == PRESENTS_COMPTEUR else "tranche_5min",
        "perimeter": {
            "compteur": raw["location"],
            "portes": "gates HSH rattachees a cette Area (hsh_archive_structure)",
            "corrections_appliquees": raw["corrections"]["appliquees"],
        },
        "counter_resets": raw.get("resets"),
        "counter_initial": raw.get("initial_counter"),
        "valid_from": raw.get("valid_from"),
        "activation": raw.get("activation"),
        "race_source": raw.get("race_source"),
    }
