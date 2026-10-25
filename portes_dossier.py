# portes_dossier.py -- Analyse complete d'UNE porte (page /doors, mode focus)
#
# Tout ce que les archives du controle d'acces HSH disent d'une porte sur une
# periode (un jour jusqu'a une heure, ou toute l'edition) :
#   hsh_archive_tx_*       tranches 5 min par appareil : passages par sens,
#                          enfants, accredites, vehicules (ou le direct
#                          hsh_transactions_agg)
#   hsh_archive_erreurs_*  CHAQUE transaction en statut non nul (ou le direct
#                          hsh_erreurs) : motif, sens, appareil, titre (utid),
#                          support (coding), validation hors ligne...
# Pur : `db` en argument, comme portes_etat.py.
#
# Les statuts non nuls ne sont pas tous des refus (live_scan_units) : 133
# (entree sans sortie prealable) et 107 (checkpoint hors ligne) sont des
# passages acceptes ; 61 / 175 / 62 / 63 ouvrent la porte sans titre valide.
# Ils sont ranges en familles lisibles par un operateur (FAMILLES).
import logging
from collections import defaultdict
from datetime import datetime, timedelta

import live_frequentation as lf
import live_scan_units as lsu
import presents_etat as pe
from live_controle import STATUS_LABELS
from portes_etat import compter, device_kind, norm_porte

logger = logging.getLogger(__name__)

# Familles de statuts (cle, libelle, explication), dans l'ordre d'affichage
FAMILLES = [
    ("double_usage", "Doubles scans refusés",
     "billet re-présenté avant la fin du délai anti-double usage (re-scan, billet passé à un autre)"),
    ("reentree", "Entrées sans sortie préalable",
     "billet accepté alors qu'il était déjà entré sans être ressorti : passage accepté, mais billet passé par-dessus la clôture ou partagé"),
    ("titre", "Titres refusés",
     "billet inconnu, non autorisé, expiré, bloqué ou illisible"),
    ("ouverture", "Ouvertures manuelles",
     "porte ouverte par un agent (bouton, carte de contrôle, mode libre) ou passage forcé"),
    ("annulation", "Libérations annulées",
     "porte libérée puis refermée sans passage"),
    ("hors_ligne", "Checkpoint hors ligne",
     "passage accepté par le lecteur sans réponse du serveur HSH"),
    ("autre", "Autres statuts", "statuts informatifs ou rares"),
]
FAMILLE_LIBELLE = {k: lib for k, lib, _ in FAMILLES}

_DOUBLE = {"111", "112", "113", "114", "116", "117", "118", "119", "121", "122", "123", "124"}
_OUVERTURE = {"51", "61", "62", "63", "64", "65", "66", "175"}
_AUTRE = {"140"}
FORCE = "63"

TITRES_RECURRENTS_MIN = 3
TITRES_RECURRENTS_MAX = 12

_ERR_PROJ = {"_id": 0, "checkpoint": 1, "gate": 1, "direction": 1, "date_utc": 1, "type_scan": 1,
             "status": 1, "status_label": 1, "utid": 1, "coding_label": 1, "validated": 1}


def famille(status):
    s = str(status)
    if s == "133":
        return "reentree"
    if s == "107":
        return "hors_ligne"
    if s == "134":
        return "annulation"
    if s in _DOUBLE:
        return "double_usage"
    if s in _OUVERTURE:
        return "ouverture"
    if s in _AUTRE:
        return "autre"
    return "titre"


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def _hm(dt):
    return dt.strftime("%H:%M") if dt else None


def _lire_tx(db, sources, l0, l1, cle):
    """Tranches tx de la porte (nom normalise `cle`) et noms bruts vus."""
    proj = dict(lsu._TX_PROJ)
    proj.update({"ok": 1, "entrees_valides": 1, "sorties_valides": 1, "entrees_enfants": 1,
                 "sorties_enfants": 1, "entrees_accredites": 1, "sorties_accredites": 1})
    out, bruts = {}, set()
    for coll, filtre in sources:
        q = dict(filtre)
        q["tranche"] = {"$gte": l0, "$lt": l1}
        try:
            for d in db[coll].find(q, proj):
                if norm_porte(d.get("gate_name")) != cle:
                    continue
                tr = pe._utc_naif(d.get("tranche"))
                if tr is None:
                    continue
                d["tranche"] = tr
                bruts.add(d.get("gate_name"))
                out[(str(d.get("checkpoint_id")), tr)] = d
        except Exception:
            logger.warning("portes_dossier : lecture de %s impossible", coll, exc_info=True)
    return list(out.values()), bruts


def _lire_erreurs(db, sources, l0, l1, cle, bruts):
    try:
        existing = set(db.list_collection_names())
    except Exception:
        existing = set()
    lo, hi = l0.strftime("%Y-%m-%dT%H:%M:%S"), l1.strftime("%Y-%m-%dT%H:%M:%S")
    docs, vues = [], set()
    for coll, filtre in sources:
        ecoll = lsu.errors_collection(coll)
        if not ecoll or ecoll in vues or ecoll not in existing:
            continue
        vues.add(ecoll)
        q = dict(filtre)
        q["date_utc"] = {"$gte": lo, "$lt": hi}
        if bruts:
            q["gate.Name"] = {"$in": sorted(n for n in bruts if n)}
        try:
            for d in db[ecoll].find(q, _ERR_PROJ):
                if norm_porte((d.get("gate") or {}).get("Name")) == cle:
                    docs.append(d)
        except Exception:
            logger.warning("portes_dossier : lecture de %s impossible", ecoll, exc_info=True)
    return docs


def dossier(db, event, year, porte, jour=None, at=None):
    """Analyse complete de `porte` sur un jour (jusqu'a `at`, heure de Paris
    naive) ou, sans `jour`, sur toute la fenetre de l'edition."""
    src = lf.find_sources(db, event, year)
    base = {"event": event, "year": int(year), "porte": porte}
    if src is None:
        return dict(base, disponible=False, motif="aucune donnee de controle d'acces live pour cette edition")
    w0, w1 = src["window"]
    if jour is not None:
        l0 = datetime(jour.year, jour.month, jour.day)
        l1 = l0 + timedelta(days=1)
        if at is not None:
            l1 = min(l1, max(l0, at))
    else:
        l0, l1 = pe.to_tranche_label(w0), pe.to_tranche_label(w1)
    cle = norm_porte(porte)
    tx, bruts = _lire_tx(db, src["tx"], l0, l1, cle)
    errs = _lire_erreurs(db, src["tx"], l0, l1, cle, bruts)
    if not tx and not errs:
        return dict(base, disponible=False, motif="aucun passage ni scan pour cette porte sur la periode")

    edition = jour is None
    err_buckets = lsu.error_buckets(errs)

    # ---- Passages (tx) ----
    tot = defaultdict(int)
    heures = defaultdict(lambda: defaultdict(int))
    apps = defaultdict(lambda: defaultdict(int))
    app_bornes = {}
    for d in tx:
        cp_id = str(d.get("checkpoint_id"))
        nom = d.get("checkpoint_name") or cp_id
        t = d["tranche"]
        p_e, p_s, v_e, v_s, r_e, r_s = compter(d, err_buckets.get((cp_id, lsu.floor5(t))))
        for k, v in (("pers_e", p_e), ("pers_s", p_s), ("veh_e", v_e), ("veh_s", v_s)):
            tot[k] += v
        for k in ("entrees_enfants", "sorties_enfants", "entrees_accredites", "sorties_accredites"):
            tot[k] += lsu._int(d.get(k))
        h = t.replace(minute=0, second=0, microsecond=0)
        heures[h]["e"] += p_e + v_e
        heures[h]["s"] += p_s + v_s
        a = apps[nom]
        a["e"] += p_e + v_e
        a["s"] += p_s + v_s
        if p_e + v_e + p_s + v_s:
            b = app_bornes.setdefault(nom, [t, t])
            b[0], b[1] = min(b[0], t), max(b[1], t + timedelta(minutes=5))

    # ---- Transactions en statut non nul (erreurs) ----
    fam = defaultdict(int)
    statuts = {}
    supports = defaultdict(int)
    types = defaultdict(int)
    titres = {}
    forces = hors_ligne_lecteur = 0
    for d in errs:
        st = str(d.get("status"))
        f = famille(st)
        fam[f] += 1
        ts = lsu.parse_err_ts(d.get("date_utc"))
        sens = "e" if d.get("direction") == "Entree" else "s" if d.get("direction") == "Sortie" else "?"
        row = statuts.setdefault(st, {"status": st, "libelle": d.get("status_label") or STATUS_LABELS.get(st, st),
                                      "famille": f, "n": 0, "e": 0, "s": 0})
        row["n"] += 1
        if sens in ("e", "s"):
            row[sens] += 1
        if st == FORCE:
            forces += 1
        if str(d.get("validated")) == "1":
            hors_ligne_lecteur += 1
        cp = (d.get("checkpoint") or {}).get("Name") or str((d.get("checkpoint") or {}).get("ID"))
        a = apps[cp]
        a[f] += 1
        if f in ("double_usage", "titre"):
            a["refus"] += 1
        if str(d.get("validated")) == "1":
            a["hors_ligne"] += 1
        if ts:
            heures[ts.replace(minute=0, second=0, microsecond=0)][f] += 1
        if f in ("double_usage", "titre"):
            supports[d.get("coding_label") or "inconnu"] += 1
            types[d.get("type_scan") or "inconnu"] += 1
            u = d.get("utid")
            if u:
                tt = titres.setdefault(u, {"titre": u, "n": 0, "motifs": defaultdict(int),
                                           "appareils": set(), "premier": ts, "dernier": ts})
                tt["n"] += 1
                tt["motifs"][row["libelle"]] += 1
                tt["appareils"].add(cp)
                if ts:
                    tt["premier"] = min(filter(None, [tt["premier"], ts]))
                    tt["dernier"] = max(filter(None, [tt["dernier"], ts]))

    entrees = tot["pers_e"] + tot["veh_e"]
    sorties = tot["pers_s"] + tot["veh_s"]
    refus = fam["double_usage"] + fam["titre"]
    scans = entrees + sorties + refus

    familles = [{"cle": k, "libelle": lib, "aide": aide, "n": fam[k], "pct_scans": _pct(fam[k], scans)}
                for k, lib, aide in FAMILLES if fam[k]]

    # Par heure, de la premiere a la derniere activite (une edition entiere
    # garde ses nuits creuses : l'axe reste continu)
    serie = []
    if heures:
        h, fin = min(heures), max(heures)
        while h <= fin:
            x = heures.get(h, {})
            r = sum(x.get(k, 0) for k in ("double_usage", "titre"))
            serie.append({"t": h.strftime("%d/%m %Hh") if edition else h.strftime("%Hh"), "iso": h.isoformat(timespec="minutes"),
                          "e": x.get("e", 0), "s": x.get("s", 0), "refus": r,
                          "refus_pct": _pct(r, x.get("e", 0) + x.get("s", 0) + r),
                          **{k: x.get(k, 0) for k, _l, _a in FAMILLES}})
            h += timedelta(hours=1)

    appareils = []
    for nom, a in apps.items():
        sc = a["e"] + a["s"] + a["refus"]
        bornes = app_bornes.get(nom)
        appareils.append({
            "nom": nom, "type": device_kind(nom), "e": a["e"], "s": a["s"],
            "refus": a["refus"], "refus_pct": _pct(a["refus"], sc),
            "double_usage": a["double_usage"], "titre": a["titre"], "reentree": a["reentree"],
            "ouverture": a["ouverture"], "annulation": a["annulation"], "hors_ligne": a["hors_ligne"],
            "debut": _hm(bornes[0]) if bornes else None, "fin": _hm(bornes[1]) if bornes else None,
        })
    appareils.sort(key=lambda x: -(x["e"] + x["s"] + x["refus"]))

    recurrents = sorted((t for t in titres.values() if t["n"] >= TITRES_RECURRENTS_MIN),
                        key=lambda t: -t["n"])[:TITRES_RECURRENTS_MAX]

    return dict(base, **{
        "disponible": True,
        "periode": {"jour": jour.isoformat() if jour else None, "debut": l0.isoformat(timespec="minutes"),
                    "fin": l1.isoformat(timespec="minutes"), "edition": edition},
        "direct": bool(src.get("direct")),
        "totaux": {
            "entrees": entrees, "sorties": sorties,
            "entrees_personnes": tot["pers_e"], "sorties_personnes": tot["pers_s"],
            "entrees_vehicules": tot["veh_e"], "sorties_vehicules": tot["veh_s"],
            "entrees_enfants": tot["entrees_enfants"], "sorties_enfants": tot["sorties_enfants"],
            "entrees_accredites": tot["entrees_accredites"], "sorties_accredites": tot["sorties_accredites"],
            "scans": scans, "refus": refus, "refus_pct": _pct(refus, scans),
            "double_usage": fam["double_usage"], "titres_refuses": fam["titre"],
            "reentrees": fam["reentree"], "reentrees_pct": _pct(fam["reentree"], entrees),
            "ouvertures": fam["ouverture"], "forces": forces, "annulations": fam["annulation"],
            "checkpoint_hors_ligne": fam["hors_ligne"], "valides_hors_ligne": hors_ligne_lecteur,
            "appareils": len([a for a in appareils if a["e"] + a["s"]]),
        },
        "familles": familles,
        "statuts": sorted(statuts.values(), key=lambda r: -r["n"]),
        "serie": serie,
        "appareils": appareils,
        "supports": [{"libelle": k, "n": v} for k, v in sorted(supports.items(), key=lambda kv: -kv[1])],
        "types": [{"libelle": k, "n": v} for k, v in sorted(types.items(), key=lambda kv: -kv[1])],
        "titres_recurrents": [{
            "titre": t["titre"], "n": t["n"],
            "motif": max(t["motifs"].items(), key=lambda kv: kv[1])[0],
            "appareils": sorted(t["appareils"]),
            "premier": t["premier"].isoformat(timespec="minutes") if t["premier"] else None,
            "dernier": t["dernier"].isoformat(timespec="minutes") if t["dernier"] else None,
        } for t in recurrents],
    })
