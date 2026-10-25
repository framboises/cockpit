"""
portes_etat.py -- Etat et analyse des portes du controle d'acces (page
operateur /doors, mur TV /portes-mur, bilan RETEX). Module pur : `db` en
argument, aucun import Flask, comme presents_etat.py.

CE QUE DISENT LES MESURES (archives 2026, 24H MOTOS / GPF / SUPERBIKE) :

  - En live, 100 % des passages ont un SENS (Entree / Sortie, lu par HSH sur
    l'attribut Entry/Exit de l'Area traversee). Le sens << Autre >> des
    imports Excel 2024 n'existe plus.
  - Aucune porte de l'enceinte n'a de mode FIXE : sur une edition, toutes
    font ~50/50 entrees/sorties. Le mode est une affaire d'HEURE et il bascule
    par reaffectation des appareils (Porte Nord pietons, GPF dimanche :
    10 appareils en entree a 9 h, 11 en sortie a 14 h). Seuls parkings,
    musee et quelques portes vehicules sont a sens unique.
  - Donc : le mode se calcule sur une fenetre glissante (MODE_FENETRE_MIN)
    a partir de la part de sorties, et chaque APPAREIL recoit son propre
    sens, ce qui donne une capacite PAR SENS.

CAPACITE, PAR SENS (docs/claude/alertes.md) :

  - Entree : le goulet est la PALPATION, pas le scan. Capacite de securite =
    agents prevus x 350/h (door_security.py, bible + planning). A la fiche
    << renfort filtrage >>, la porte est a 101 % de cette capacite et a 15 %
    de sa capacite de scan. La capacite de scan des appareils en entree reste
    affichee, en second.
  - Sortie : aucune palpation ; capacite = appareils en sortie x debit
    appareil. A l'exode le controle est souvent lache : les sorties mesurees
    sont une BORNE BASSE.

PASSAGES VALIDES : `entrees` / `sorties` des tranches tx comptent AUSSI les
scans refuses (3 a 7 % du flux, 15 % a la Porte Nord bis). Le collecteur
ecrit desormais `entrees_valides` / `sorties_valides` ; pour les tranches
plus anciennes, les refus par sens viennent de l'archive des erreurs
(live_scan_units.valid_counts), au prorata a defaut.

FUSEAU : `tranche` est en HEURE DE PARIS etiquetee UTC (presents_etat).
Tout ici est en heure de Paris naive ; seules les bornes UTC passent par
presents_etat.to_tranche_label. Les creneaux 15 min de ce module sont
etiquetes a leur DEBUT (a la difference de l'import Excel, etiquete a sa fin).
"""

from __future__ import annotations

import logging
import threading
import time
import unicodedata
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date

import door_security
import live_frequentation as lf
import live_scan_units as lsu
import presents_etat as pe

logger = logging.getLogger(__name__)

# Services mobiles qui ne sont pas des lieux de passage (portent un nom de
# gate dans HSH, comme les portes).
SERVICES = ("HELPDESK", "UAM", "LITIGE", "SERI", "PUNISHER")
UAM_GATE = "UAM"

# Mode d'une porte : part de sorties sur la fenetre glissante.
MODE_FENETRE_MIN = 15
SEUIL_ENTREE = 0.20      # moins de 20 % de sorties : porte en ENTREE
SEUIL_SORTIE = 0.80      # plus de 80 % de sorties : porte en SORTIE
MIN_PASSAGES_MODE = 10   # en dessous sur la fenetre, la part ne dit rien : << calme >>
FERMEE_APRES_MIN = 30    # aucun passage depuis 30 min : << fermee >>

# Debit d'un appareil (/h) : memes valeurs que le detecteur de saturation
# (alert_engine.DOOR_SAT_DEFAULTS, p99 mesure 2026).
DEVICE_CAP = {"tripode": 900, "pda": 650, "autre": 650}
AGENT_RATE_H = door_security.AGENT_RATE_H

# Charge : vigilance a 80 %, saturation a 100 %.
CHARGE_VIGILANCE = 0.80
CHARGE_SATURATION = 1.00
MIN_RATE_ALERTE = 300    # /h : en dessous, jamais d'alerte (nuit, portes calmes)

# Refus : rafale signalee au-dela de 15 % sur 15 min, avec au moins 20 scans.
REFUS_ALERTE_PCT = 15.0
REFUS_MIN_SCANS = 20

# Prevision : memes reglages que le detecteur (profil N-1 de la meme porte,
# aligne sur le jour de course, croissance bornee).
HORIZON_MIN = 60
MAX_GROWTH = 3.0
MIN_N1_RATE = 120

CACHE_LIVE_S = 30
CACHE_ARCHIVE_S = 600
_CACHE = {}
_LOCK = threading.Lock()

CATEGORIES_FICHES = ("PCO.Flux", "PCO.Securite")


# ---------------------------------------------------------------------------
# Petits outils
# ---------------------------------------------------------------------------

def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def norm_porte(name):
    """Nom de porte comparable d'une edition a l'autre (casse, accents,
    PORTAIL/PORTE, pluriels) -- meme regle que alert_engine._door_norm."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Z0-9]+", " ", s.upper()).strip()
    return " ".join({"PORTAIL": "PORTE", "PIETONS": "PIETON", "VEHICULES": "VEHICULE"}.get(w, w)
                    for w in s.split())


def device_kind(cp_name):
    n = str(cp_name or "").upper()
    if n.startswith("TRI"):
        return "tripode"
    if "PDA" in n:
        return "pda"
    return "autre"


def floor5(dt):
    return dt.replace(minute=dt.minute - dt.minute % 5, second=0, microsecond=0)


def floor15(dt):
    return dt.replace(minute=dt.minute - dt.minute % 15, second=0, microsecond=0)


def compter(doc, err=None):
    """Passages VALIDES d'une tranche tx :
    (pers_e, pers_s, veh_e, veh_s, refus_e, refus_s).

    `entrees_valides` / `sorties_valides` (collecteur depuis octobre 2026)
    font foi ; sinon les refus par sens viennent de l'archive des erreurs
    (`err`, live_scan_units.error_buckets), au prorata a defaut.
    """
    if "entrees_valides" in doc or "sorties_valides" in doc:
        e_all, s_all = _int(doc.get("entrees")), _int(doc.get("sorties"))
        e, s = min(_int(doc.get("entrees_valides")), e_all), min(_int(doc.get("sorties_valides")), s_all)
        ve = min(_int(doc.get("entrees_vehicules")), e)
        vs = min(_int(doc.get("sorties_vehicules")), s)
        return e - ve, s - vs, ve, vs, e_all - e, s_all - s
    return lsu.valid_counts(doc, err)


def mode_de(e, s, devices_e=0, devices_s=0):
    """Mode d'une porte d'apres ses passages (et ses appareils si le flux est
    trop faible pour que la part de sorties veuille dire quelque chose)."""
    total = e + s
    if total >= MIN_PASSAGES_MODE:
        part = s / float(total)
        if part < SEUIL_ENTREE:
            return "entree"
        if part > SEUIL_SORTIE:
            return "sortie"
        return "mixte"
    if devices_e and devices_s:
        return "mixte"
    if devices_s:
        return "sortie"
    if devices_e or total:
        return "entree"
    return None


def niveau_charge(charge):
    if charge is None:
        return "normal"
    if charge >= CHARGE_SATURATION:
        return "saturation"
    if charge >= CHARGE_VIGILANCE:
        return "vigilance"
    return "normal"


# ---------------------------------------------------------------------------
# Lecture d'une edition (live ou archivee)
# ---------------------------------------------------------------------------

def _sources(db, event, year):
    return lf.find_sources(db, event, year)


def _read_tx(db, sources, l0, l1):
    proj = dict(lsu._TX_PROJ)
    proj.update({"ok": 1, "entrees_valides": 1, "sorties_valides": 1})
    out = {}
    for coll, filtre in sources:
        q = dict(filtre)
        q["tranche"] = {"$gte": l0, "$lt": l1}
        try:
            for d in db[coll].find(q, proj):
                tr = pe._utc_naif(d.get("tranche"))
                if tr is None:
                    continue
                d["tranche"] = tr
                out[(str(d.get("checkpoint_id")), tr)] = d
        except Exception:
            logger.warning("portes_etat : lecture de %s impossible", coll, exc_info=True)
    return list(out.values())


def charger(db, event, year, l0=None, l1=None, use_cache=True):
    """Tranches 5 min par porte d'une edition, sur [l0, l1) (heure de Paris).

    Rend None si l'edition n'a ni archive ni direct. Sinon :
      {event, year, direct, race_dt (Paris naif), window [l0, l1],
       portes: {nom: {kind, area, buckets: {t: [pe, ps, ve, vs, re, rs]},
                      devices: {t: {cp: (e, s, kind)}}}},
       uam_devices: set, gate_names: [...]}
    """
    src = _sources(db, event, year)
    if src is None:
        return None
    w0, w1 = src["window"]
    l0 = l0 or pe.to_tranche_label(w0)
    l1 = l1 or pe.to_tranche_label(w1)
    key = (str(event), int(year), l0, l1)
    ttl = CACHE_LIVE_S if src.get("direct") else CACHE_ARCHIVE_S
    now = time.time()
    if use_cache:
        with _LOCK:
            hit = _CACHE.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]

    tx = _read_tx(db, src["tx"], l0, l1)
    # Refus par sens : seulement pour les tranches ecrites avant que le
    # collecteur ne les distingue lui-meme.
    err_by_bucket = {}
    if any("entrees_valides" not in d for d in tx):
        try:
            existing = set(db.list_collection_names())
        except Exception:
            existing = set()
        err_docs, _ = lsu._read_errors(db, src["tx"], l0, l1, existing)
        err_by_bucket = lsu.error_buckets(err_docs)
    maps = lsu.structure_maps(lsu._read_structure(db, src["structure"]))
    enclosure = lsu._enclosure_id(src)

    portes, uam = {}, set()
    for d in tx:
        gate = lsu._norm(d.get("gate_name"))
        cp_id = str(d.get("checkpoint_id"))
        cp = d.get("checkpoint_name") or cp_id
        t = d["tranche"]
        if gate == UAM_GATE:
            uam.add(cp)
        if not gate or gate in SERVICES:
            continue
        kind, unit, area, _aid = lsu.classify_gate(gate, cp_id, maps, enclosure)
        p_e, p_s, v_e, v_s, r_e, r_s = compter(d, err_by_bucket.get((cp_id, floor5(t))))
        p = portes.setdefault(gate, {"kind": kind, "area": area, "buckets": {}, "devices": {}})
        b = p["buckets"].setdefault(t, [0, 0, 0, 0, 0, 0])
        for i, v in enumerate((p_e, p_s, v_e, v_s, r_e, r_s)):
            b[i] += v
        e_all, s_all = p_e + v_e, p_s + v_s
        if e_all or s_all or r_e or r_s:
            p["devices"].setdefault(t, {})[cp] = (e_all, s_all, device_kind(cp))

    race = src.get("race_dt")
    race_label = race.astimezone(pe.TZ_PARIS).replace(tzinfo=None) if race else None
    raw = {
        "event": event, "year": int(year), "direct": bool(src.get("direct")),
        "race_dt": race_label, "window": [l0, l1],
        "portes": portes, "uam_devices": uam, "gate_names": sorted(portes),
        "derniere_tranche": max((d["tranche"] for d in tx), default=None),
    }
    if use_cache:
        with _LOCK:
            _CACHE[key] = (now, raw)
    return raw


def clear_cache():
    with _LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# Securite (bible + planning)
# ---------------------------------------------------------------------------

_SECU_CACHE = {}


def staffing_securite(db, event, year, gate_names):
    """{porte: {creneau 30 min: agents}} ; {} sans bible ou sans planning."""
    key = (event, int(year), tuple(sorted(gate_names)))
    hit = _SECU_CACHE.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        posts, _unmatched = door_security.load_door_posts(db, event, year, gate_names)
        staffing = door_security.load_security_staffing(db, event, year, posts)
    except Exception:
        logger.warning("portes_etat : effectifs securite illisibles", exc_info=True)
        staffing = {}
    _SECU_CACHE[key] = (time.time(), staffing)
    return staffing


# ---------------------------------------------------------------------------
# N-1 : la meme porte, l'edition precedente, alignee sur le jour de course
# ---------------------------------------------------------------------------

def _race_label(db, event, year):
    """Instant de course N (Paris naif) ou None."""
    for name in lf.event_names(db, event):
        dt, _src = lf.race_moment(db, name, year)
        if dt is not None:
            return dt.astimezone(pe.TZ_PARIS).replace(tzinfo=None)
    return None


def decalage_n1(race_n, race_n1):
    """Decalage N -> N-1 en jours, arrondi a la semaine entiere : la course
    revient chaque annee le meme jour de semaine, ce qui neutralise le champ
    `race` qui porte tantot le depart tantot l'arrivee."""
    if not race_n or not race_n1:
        return None
    return int(round((race_n.date() - race_n1.date()).days / 7.0)) * 7


def charger_n1(db, event, year, l0, l1, race_n):
    """{porte normalisee: {debut 15 min (echelle N): [entrees, sorties]}} et
    sa source, sur la fenetre [l0, l1) de N.

    1. archive live de N-1 (5 min, sens, passages valides) -- 2026 et apres ;
    2. historique_controle `complet` (import 15 min, entree/sortie par porte) ;
    3. historique_controle `portes` (horaire, sens confondus) : rendu dans
       la colonne << entrees >> avec la source `portes_total`, pour la courbe
       seulement (pas de prevision par sens).
    """
    y1 = int(year) - 1
    race_n1 = _race_label(db, event, y1)
    off = decalage_n1(race_n, race_n1)
    if off is None:
        return {}, None, None
    shift = timedelta(days=off)
    a, b = l0 - shift, l1 - shift
    out = defaultdict(lambda: defaultdict(lambda: [0, 0]))

    raw = charger(db, event, y1, a, b)
    if raw and raw["portes"]:
        for gate, p in raw["portes"].items():
            k = norm_porte(gate)
            for t, v in p["buckets"].items():
                c = out[k][floor15(t) + shift]
                c[0] += v[0] + v[2]
                c[1] += v[1] + v[3]
        return out, "archive_live", y1

    aliases = lf.event_names(db, event)
    doc = db["historique_controle"].find_one(
        {"type": "complet", "event": {"$in": aliases}, "year": y1})
    if doc:
        for u in doc.get("complet") or []:
            if u.get("kind") != "porte" or u.get("ignored"):
                continue
            k = norm_porte(u.get("porte") or u.get("name"))
            for r in u.get("data_15min") or []:
                try:
                    # Import : creneau etiquete a sa FIN -> debut = fin - 15 min
                    t = datetime.fromisoformat(str(r.get("date"))[:19]) - timedelta(minutes=15)
                except ValueError:
                    continue
                if not (a <= t < b):
                    continue
                c = out[k][t + shift]
                c[0] += _int(r.get("entree"))
                c[1] += _int(r.get("sortie"))
        if out:
            return out, "import_15min", y1

    doc = db["historique_controle"].find_one(
        {"type": "portes", "event": {"$in": aliases}, "year": y1})
    for door in (doc or {}).get("doors") or []:
        k = norm_porte(door.get("name"))
        for s in door.get("scans") or []:
            ts = s.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    continue
            if not isinstance(ts, datetime):
                continue
            ts = ts.replace(tzinfo=None, minute=0, second=0, microsecond=0)
            if not (a <= ts < b):
                continue
            # Horaire reparti sur ses 4 quarts d'heure
            n = _int(s.get("scan_count"))
            for q in range(4):
                out[k][ts + shift + timedelta(minutes=15 * q)][0] += n / 4.0
    return out, ("portes_total" if out else None), (y1 if out else None)


def _n1_rate(serie, t, sens):
    """Debit N-1 (/h) du quart d'heure contenant t, interpole entre milieux
    de creneaux (lue en marches, la serie faisait bondir la prevision a
    chaque changement de quart d'heure)."""
    if not serie:
        return None
    m = t - timedelta(minutes=7.5)
    q0 = floor15(m)
    q1 = q0 + timedelta(minutes=15)
    if q0 not in serie and q1 not in serie:
        return None
    r0 = (serie.get(q0) or [0, 0])[sens] * 4.0
    r1 = (serie.get(q1) or [0, 0])[sens] * 4.0
    f = (m - q0).total_seconds() / 900.0
    return r0 + (r1 - r0) * f


def prevoir(cur_rate, serie_n1, end, sens, horizon=HORIZON_MIN):
    """[(minutes devant, debit prevu)] par pas de 15 min, ou [] : debit
    courant x rapport N-1 (futur / maintenant), borne a MAX_GROWTH. Meme
    methode que le detecteur de saturation (profil_n1)."""
    if not cur_rate or cur_rate <= 0 or not serie_n1:
        return []
    n1_now = _n1_rate(serie_n1, end - timedelta(minutes=MODE_FENETRE_MIN / 2.0), sens)
    if n1_now is None or n1_now < MIN_N1_RATE:
        return []
    out = []
    for h in range(15, horizon + 1, 15):
        fut = _n1_rate(serie_n1, end + timedelta(minutes=h), sens)
        if fut is None:
            continue
        out.append((h, cur_rate * min(max(fut / n1_now, 0.0), MAX_GROWTH)))
    return out


# ---------------------------------------------------------------------------
# Timeline et main courante
# ---------------------------------------------------------------------------

_PROG_MOTS = ("COURSE", "DEPART", "ARRIVEE", "CONCERT", "FEU", "PARADE",
              "WARM", "QUALIF", "ESSAI")


def vignettes_jour(db, event, year, jour, gate_names):
    """Vignettes de la timeline du jour : celles qui nomment une porte, et le
    programme general (ouverture au public, depart, arrivee, concerts...).
    [{debut, fin, libelle, porte}] ; porte = nom de gate ou None."""
    doc = db["timetable"].find_one({"event": event, "year": str(year)}, {"data." + jour: 1})
    items = ((doc or {}).get("data") or {}).get(jour) or []
    out = []
    for it in items:
        lib = it.get("activity") or it.get("title") or ""
        if not lib:
            continue
        lieu = it.get("place") or ""
        porte = door_security.match_gate(lieu, gate_names) or door_security.match_gate(lib, gate_names)
        mots = set(door_security.norm(lib).split())
        general = it.get("category") == "General" or any(m in mots for m in _PROG_MOTS)
        if not porte and not general:
            continue
        out.append({"debut": (it.get("start") or "")[:5], "fin": (it.get("end") or "")[:5],
                    "libelle": str(lib)[:90], "porte": porte})
    out.sort(key=lambda v: v["debut"] or "99")
    return out


def fiches_jour(db, event, year, jour, gate_names):
    """{porte: [{heure, texte, categorie, urgence, ouverte}]} : fiches de flux
    et de securite du jour qui citent la porte (tous ses mots distinctifs dans
    le titre, le texte ou la zone)."""
    jetons = {g: door_security._tokens(g) for g in gate_names}
    jetons = {g: t for g, t in jetons.items() if t}
    out = defaultdict(list)
    q = {"event": {"$in": lf.event_names(db, event)}, "year": {"$in": [str(year), int(year)]},
         "date_local": jour, "category": {"$in": list(CATEGORIES_FICHES)}}
    proj = {"text": 1, "text_full": 1, "area": 1, "category": 1, "time_local": 1,
            "niveau_urgence": 1, "close_iso": 1, "status_code": 1}
    try:
        docs = list(db["pcorg"].find(q, proj).limit(2000))
    except Exception:
        logger.warning("portes_etat : lecture des fiches impossible", exc_info=True)
        return {}
    for d in docs:
        area = d.get("area") if isinstance(d.get("area"), dict) else {}
        blob = set(door_security.norm(" ".join(str(x or "") for x in (
            d.get("text"), d.get("text_full"), area.get("desc")))).split())
        cible = [g for g, t in jetons.items() if t <= blob]
        if not cible:
            continue
        # La porte la plus specifique seulement (NORD BIS plutot que NORD)
        best = max(len(jetons[g]) for g in cible)
        for g in cible:
            if len(jetons[g]) == best:
                out[g].append({
                    "heure": (d.get("time_local") or "")[:5],
                    "texte": str(d.get("text") or "")[:140],
                    "categorie": d.get("category"),
                    "urgence": d.get("niveau_urgence"),
                    "ouverte": not d.get("close_iso"),
                })
    for g in out:
        out[g].sort(key=lambda f: f["heure"])
    return dict(out)


# ---------------------------------------------------------------------------
# Analyse
# ---------------------------------------------------------------------------

def _fenetre(p, end, minutes):
    """Somme des tranches de [end - minutes, end) et appareils par sens."""
    a = end - timedelta(minutes=minutes)
    tot = [0, 0, 0, 0, 0, 0]
    devs = {}
    for t, v in p["buckets"].items():
        if a <= t < end:
            for i in range(6):
                tot[i] += v[i]
    for t, dd in p["devices"].items():
        if a <= t < end:
            for cp, (e, s, kind) in dd.items():
                c = devs.setdefault(cp, [0, 0, kind])
                c[0] += e
                c[1] += s
    return tot, devs


def _capacites_appareils(devs):
    """(capacite entree, capacite sortie, nb appareils E, S, ES) : chaque
    appareil compte dans un sens au prorata de son flux sur la fenetre."""
    cap_e = cap_s = 0.0
    n_e = n_s = n_es = 0
    for e, s, kind in devs.values():
        tot = e + s
        if not tot:
            continue
        c = DEVICE_CAP.get(kind, DEVICE_CAP["autre"])
        cap_e += c * e / tot
        cap_s += c * s / tot
        if e and s:
            n_es += 1
        elif e:
            n_e += 1
        else:
            n_s += 1
    return cap_e, cap_s, n_e, n_s, n_es


def _ratio(a, b):
    return (a / b) if (a is not None and b) else None


def _arrondi(v, n=0):
    return None if v is None else round(v, n)


def analyser_porte(nom, p, end, staffing, n1_serie, tripode_jour, uam_devices,
                   debut_jour=None, avec_series=True, n1_tous_sens=False):
    """Etat d'une porte a `end` (fin de la derniere tranche complete) et,
    si demande, ses series du jour par quart d'heure."""
    tot, devs = _fenetre(p, end, MODE_FENETRE_MIN)
    pe_, ps_, ve_, vs_, re_, rs_ = tot
    e, s = pe_ + ve_, ps_ + vs_
    k = 60.0 / MODE_FENETRE_MIN
    rate_e, rate_s, rate_pe = e * k, s * k, pe_ * k
    cap_e, cap_s, n_e, n_s, n_es = _capacites_appareils(devs)

    derniere = max((t for t, v in p["buckets"].items() if t < end and sum(v[:4])), default=None)
    fermee = derniere is None or (end - derniere) > timedelta(minutes=FERMEE_APRES_MIN)
    mode = None if fermee else mode_de(e, s, n_e + n_es, n_s + n_es)
    if mode is None and not fermee:
        # Rien sur la fenetre mais une activite recente : le sens de la
        # derniere heure.
        tot60, devs60 = _fenetre(p, end, 60)
        _c1, _c2, m_e, m_s, m_es = _capacites_appareils(devs60)
        mode = mode_de(tot60[0] + tot60[2], tot60[1] + tot60[3], m_e + m_es, m_s + m_es)
    calme = (not fermee) and (e + s) < MIN_PASSAGES_MODE

    agents = door_security.agents_at(staffing, nom, end) if staffing else 0
    cap_secu = agents * AGENT_RATE_H if agents else None
    charge_secu = _ratio(rate_pe, cap_secu)
    charge_scan_e = _ratio(rate_e, cap_e)
    charge_s = _ratio(rate_s, cap_s)
    # La charge d'entree qui compte est celle de la securite quand la porte
    # en a ; sinon celle des appareils en entree.
    charge_e = charge_secu if charge_secu is not None else charge_scan_e

    # Un N-1 tous sens confondus (import horaire) ne prevoit rien par sens :
    # sa vague de sortie a l'exode passerait pour une ruee a l'entree.
    peut_prevoir = not fermee and not n1_tous_sens
    prev_e = prevoir(rate_e, n1_serie, end, 0) if peut_prevoir else []
    prev_s = prevoir(rate_s, n1_serie, end, 1) if peut_prevoir else []

    # Pire charge a venir (60 min), a la capacite PREVUE (une fin de vacation
    # fait baisser la capacite alors que le flux monte).
    pire_prev = None
    for h, r in prev_e:
        t = end + timedelta(minutes=h)
        ag = door_security.agents_at(staffing, nom, t) if staffing else 0
        cap = ag * AGENT_RATE_H if ag else (cap_e or None)
        part_pers = (pe_ / float(e)) if e else 1.0
        c = _ratio(r * (part_pers if ag else 1.0), cap)
        if c is not None and (pire_prev is None or c > pire_prev["charge"]):
            pire_prev = {"sens": "entree", "dans_min": h, "debit": round(r), "charge": c,
                         "capacite": round(cap) if cap else None, "agents": ag or None}
    for h, r in prev_s:
        c = _ratio(r, cap_s)
        if c is not None and (pire_prev is None or c > pire_prev["charge"]):
            pire_prev = {"sens": "sortie", "dans_min": h, "debit": round(r), "charge": c,
                         "capacite": round(cap_s) if cap_s else None, "agents": None}

    scans = e + s + re_ + rs_
    refus_pct = round(100.0 * (re_ + rs_) / scans, 1) if scans else None
    actifs = set(devs)
    # Renfort PDA : sur une porte equipee de tripodes ce jour-la, un PDA (hors
    # equipe mobile UAM) scanne -> debordement des tourniquets (scans.md).
    pda_renfort = bool(tripode_jour) and any(
        devs[cp][2] == "pda" and cp not in uam_devices for cp in actifs)
    uam_now = sorted(cp for cp in actifs if cp in uam_devices)

    # Niveau : saturation constatee, puis saturation prevue, puis vigilance.
    # Seuls comptent les sens que le mode autorise ET qui ont un debit
    # significatif : 3 sorties sur un appareil d'entree donneraient un ratio
    # absurde face a la toute petite part de sa capacite affectee a la sortie.
    candidats = []
    if mode in ("entree", "mixte") and charge_e is not None and rate_e >= MIN_RATE_ALERTE:
        candidats.append(("entree", charge_e))
    if mode in ("sortie", "mixte") and charge_s is not None and rate_s >= MIN_RATE_ALERTE:
        candidats.append(("sortie", charge_s))
    sens_critique, charge_now = max(candidats, key=lambda x: x[1]) if candidats else (None, None)
    niveau = niveau_charge(charge_now)
    if niveau != "saturation" and pire_prev and pire_prev["charge"] >= CHARGE_SATURATION \
            and pire_prev["debit"] >= MIN_RATE_ALERTE:
        niveau = "saturation_prevue"

    etat = {
        "porte": nom,
        "kind": p.get("kind"),
        "mode": mode, "calme": calme, "fermee": fermee,
        "derniere_activite": derniere.strftime("%H:%M") if derniere else None,
        "debit": {"entree": round(rate_e), "sortie": round(rate_s),
                  "entree_personnes": round(rate_pe), "sortie_personnes": round(ps_ * k),
                  "part_sorties": _arrondi(100.0 * s / (e + s), 0) if (e + s) else None,
                  "part_vehicules": _arrondi(100.0 * (ve_ + vs_) / (e + s), 0) if (e + s) else None},
        "appareils": {"entree": n_e, "sortie": n_s, "deux_sens": n_es,
                      "tripodes": sum(1 for cp in actifs if devs[cp][2] == "tripode"),
                      "pda": sum(1 for cp in actifs if devs[cp][2] == "pda"),
                      "liste": sorted(({"nom": cp, "type": devs[cp][2],
                                        "sens": ("ES" if devs[cp][0] and devs[cp][1]
                                                 else ("E" if devs[cp][0] else "S")),
                                        "debit": round((devs[cp][0] + devs[cp][1]) * k)}
                                       for cp in actifs if devs[cp][0] + devs[cp][1]),
                                      key=lambda x: x["nom"])},
        "capacite": {"securite": cap_secu, "agents": agents or None,
                     "scan_entree": round(cap_e) or None, "scan_sortie": round(cap_s) or None},
        "charge": {"entree": _arrondi(charge_e, 2), "securite": _arrondi(charge_secu, 2),
                   "scan_entree": _arrondi(charge_scan_e, 2), "sortie": _arrondi(charge_s, 2),
                   "source_entree": "securite" if charge_secu is not None else
                   ("appareils" if charge_scan_e is not None else None),
                   "sens_critique": sens_critique},
        "prevision": {"entree": [(h, round(r)) for h, r in prev_e],
                      "sortie": [(h, round(r)) for h, r in prev_s],
                      "pire": dict(pire_prev, charge=round(pire_prev["charge"], 2)) if pire_prev else None},
        "refus_pct": refus_pct,
        "pda_renfort": pda_renfort,
        "uam": uam_now,
        "niveau": niveau,
    }
    if avec_series and debut_jour is not None:
        etat["series"] = series_porte(nom, p, debut_jour, end, staffing, n1_serie, n1_tous_sens)
    return etat


def series_porte(nom, p, debut, end, staffing, n1_serie, n1_tous_sens=False):
    """Quarts d'heure du jour (etiquetes a leur debut) jusqu'a `end` pour la
    mesure, toute la journee pour le N-1 et la capacite prevue."""
    q = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    for t, v in p["buckets"].items():
        if debut <= t < debut + timedelta(days=1):
            c = q[floor15(t)]
            for i in range(6):
                c[i] += v[i]
    qdev = defaultdict(dict)
    for t, dd in p["devices"].items():
        if debut <= t < debut + timedelta(days=1):
            slot = qdev[floor15(t)]
            for cp, (e, s, kind) in dd.items():
                c = slot.setdefault(cp, [0, 0, kind])
                c[0] += e
                c[1] += s
    rows = []
    t = debut
    while t < debut + timedelta(days=1):
        mesure = t < end
        v = q.get(t)
        cap_e, cap_s, n_e, n_s, n_es = _capacites_appareils(qdev.get(t, {}))
        ag = door_security.agents_at(staffing, nom, t) if staffing else 0
        n1 = (n1_serie or {}).get(t)
        row = {"t": t.strftime("%H:%M"),
               "agents": ag or None,
               "cap_secu": ag * AGENT_RATE_H if ag else None,
               "n1_e": round(n1[0] * 4) if n1 and not n1_tous_sens else None,
               "n1_s": round(n1[1] * 4) if n1 and not n1_tous_sens else None,
               "n1_total": round(n1[0] * 4) if n1 and n1_tous_sens else None}
        if mesure and v is not None:
            e, s = v[0] + v[2], v[1] + v[3]
            row.update({"e": e * 4, "s": s * 4, "pe": v[0] * 4, "ps": v[1] * 4,
                        "refus": v[4] + v[5],
                        "mode": mode_de(e, s, n_e + n_es, n_s + n_es),
                        "dev_e": n_e + n_es, "dev_s": n_s + n_es,
                        "cap_e": round(cap_e) or None, "cap_s": round(cap_s) or None})
        elif mesure:
            row.update({"e": 0, "s": 0, "pe": 0, "ps": 0, "refus": 0, "mode": None})
        rows.append(row)
        t += timedelta(minutes=15)
    return rows


def _fin_mesure(raw, at):
    """Fin de la derniere tranche 5 min complete a `at` (Paris naif).

    Collecteur en retard ou edition terminee : on s'arrete a la fin de la
    derniere tranche reellement ecrite, sans compter des zeros inventes."""
    end = floor5(at)
    last = raw.get("derniere_tranche")
    if last is not None and at > last:
        end = min(end, last + timedelta(minutes=5))
    return end


def etat(db, event, year, jour=None, at=None, vue="operateur", maintenant=None):
    """Etat de toutes les portes actives d'un jour, a l'instant `at`.

    jour : date (Paris) ; defaut = jour de `at`, ou aujourd'hui en direct,
           ou le jour de course pour une edition archivee.
    at   : datetime Paris naif ; defaut = maintenant (direct) ou fin du jour.
    vue  : "operateur" (series par porte, vignettes, fiches) ou "tv" (etat
           courant et alertes seulement).
    """
    now = maintenant or pe.to_tranche_label(datetime.now(timezone.utc))
    src = _sources(db, event, year)
    if src is None:
        return {"event": event, "year": int(year), "disponible": False,
                "motif": "aucune donnee de controle d'acces live pour cette edition"}
    race = src["race_dt"].astimezone(pe.TZ_PARIS).replace(tzinfo=None) if src.get("race_dt") else None
    direct = bool(src.get("direct"))
    if jour is None:
        if at is not None:
            jour = at.date()
        elif direct:
            jour = now.date()
        else:
            jour = race.date() if race else now.date()
    debut = datetime(jour.year, jour.month, jour.day)
    fin_jour = debut + timedelta(days=1)
    if at is None:
        at = now if (debut <= now < fin_jour) else fin_jour
    at = min(max(at, debut), fin_jour)

    raw = charger(db, event, year, debut - timedelta(hours=1), fin_jour)
    if raw is None:
        return {"event": event, "year": int(year), "disponible": False,
                "motif": "aucune donnee de controle d'acces live pour cette edition"}
    end = _fin_mesure(raw, at)

    gate_names = raw["gate_names"]
    staffing = staffing_securite(db, event, year, gate_names)
    n1, n1_src, n1_year = charger_n1(db, event, year, debut, fin_jour + timedelta(hours=2),
                                     race or _race_label(db, event, year))

    portes = []
    for nom, p in raw["portes"].items():
        if p.get("kind") != "porte":
            continue
        if not any(debut <= t < fin_jour for t in p["buckets"]):
            continue
        tripode_jour = any(kind == "tripode" for t, dd in p["devices"].items()
                           if debut <= t < fin_jour for (_e, _s, kind) in dd.values())
        portes.append(analyser_porte(nom, p, end, staffing, n1.get(norm_porte(nom)),
                                     tripode_jour, raw["uam_devices"], debut,
                                     avec_series=(vue != "tv"),
                                     n1_tous_sens=(n1_src == "portes_total")))

    ordre_niveau = {"saturation": 0, "saturation_prevue": 1, "vigilance": 2, "normal": 3}
    portes.sort(key=lambda x: (x["fermee"], ordre_niveau.get(x["niveau"], 9),
                               -(x["debit"]["entree"] + x["debit"]["sortie"])))

    out = {
        "event": event, "year": int(year), "disponible": True,
        "direct": direct and debut <= now < fin_jour,
        "jour": jour.isoformat(), "a": end.strftime("%H:%M"),
        "a_iso": end.isoformat(timespec="minutes"),
        "derniere_tranche": raw["derniere_tranche"].isoformat(timespec="minutes")
        if raw["derniere_tranche"] else None,
        "race": race.isoformat(timespec="minutes") if race else None,
        "jour_course": (jour - race.date()).days if race else None,
        "n1": {"annee": n1_year, "source": n1_src,
               "decalage_jours": decalage_n1(race, _race_label(db, event, n1_year)) if n1_year else None},
        "securite": bool(staffing),
        "totaux": _totaux(portes),
        "alertes": alertes(portes),
        "portes": portes,
        "seuils": {"entree": SEUIL_ENTREE, "sortie": SEUIL_SORTIE, "fenetre_min": MODE_FENETRE_MIN,
                   "vigilance": CHARGE_VIGILANCE, "saturation": CHARGE_SATURATION,
                   "agent_h": AGENT_RATE_H, "appareil_h": DEVICE_CAP},
        "fenetre": _fenetre_edition(src),
    }
    if vue != "tv":
        out["vignettes"] = vignettes_jour(db, event, year, jour.isoformat(), gate_names)
        fiches = fiches_jour(db, event, year, jour.isoformat(), [p["porte"] for p in portes])
        for p in portes:
            p["fiches"] = fiches.get(p["porte"], [])
    out["presents"] = _presents(db, event) if out["direct"] else None
    return out


def _fenetre_edition(src):
    """Premier et dernier jour de la fenetre de l'edition (selecteur de jour)."""
    w0, w1 = src["window"]
    l0, l1 = pe.to_tranche_label(w0), pe.to_tranche_label(w1)
    return [l0.date().isoformat(), l1.date().isoformat()]


def _totaux(portes):
    actives = [p for p in portes if not p["fermee"]]
    par_mode = defaultdict(int)
    for p in actives:
        par_mode[p["mode"] or "inconnu"] += 1
    return {
        "debit_entree": sum(p["debit"]["entree"] for p in actives),
        "debit_sortie": sum(p["debit"]["sortie"] for p in actives),
        "portes_actives": len(actives),
        "portes_fermees": len(portes) - len(actives),
        "par_mode": dict(par_mode),
        "appareils": sum(len(p["appareils"]["liste"]) for p in actives),
    }


SENS_MOT = {"entree": "entrée", "sortie": "sortie"}


def alertes(portes):
    """Alertes du mur TV, les plus graves d'abord. Chaque alerte dit QUOI,
    OU et QUAND, en texte court."""
    out = []
    for p in portes:
        if p["fermee"]:
            continue
        nom = p["porte"]
        if p["niveau"] == "saturation":
            sens = p["charge"]["sens_critique"]
            c = p["charge"][sens] or 0
            if sens == "entree" and p["charge"]["source_entree"] == "securite":
                besoin = max(1, int(-(-p["debit"]["entree_personnes"] // AGENT_RATE_H)) - (p["capacite"]["agents"] or 0))
                txt = "entrée à %d %% de la capacité sécurité — prévoir +%d agent%s" % (
                    round(c * 100), besoin, "s" if besoin > 1 else "")
            else:
                txt = "%s à %d %% de la capacité des appareils" % (SENS_MOT[sens], round(c * 100))
            out.append({"niveau": "critique", "porte": nom, "type": "saturation", "texte": txt, "quand": "maintenant"})
        elif p["niveau"] == "saturation_prevue":
            pv = p["prevision"]["pire"]
            out.append({"niveau": "danger", "porte": nom, "type": "saturation_prevue",
                        "texte": "%s : ~%d/h attendus (%d %% de la capacité)" % (
                            SENS_MOT[pv["sens"]], pv["debit"], round(pv["charge"] * 100)),
                        "quand": "dans %d min" % pv["dans_min"]})
        elif p["niveau"] == "vigilance":
            sens = p["charge"]["sens_critique"]
            out.append({"niveau": "vigilance", "porte": nom, "type": "charge",
                        "texte": "%s à %d %% de la capacité" % (SENS_MOT[sens], round((p["charge"][sens] or 0) * 100)),
                        "quand": "maintenant"})
        if p["refus_pct"] is not None and p["refus_pct"] >= REFUS_ALERTE_PCT:
            out.append({"niveau": "vigilance", "porte": nom, "type": "refus",
                        "texte": "%d %% de scans refusés" % round(p["refus_pct"]), "quand": "15 dernières min"})
        if p["pda_renfort"]:
            out.append({"niveau": "info", "porte": nom, "type": "pda_renfort",
                        "texte": "PDA en renfort des tripodes", "quand": "maintenant"})
    rang = {"critique": 0, "danger": 1, "vigilance": 2, "info": 3}
    out.sort(key=lambda a: rang.get(a["niveau"], 9))
    return out


def _presents(db, event):
    """Presents sur site (compteur principal), pendant le direct seulement."""
    try:
        g = pe.read_global(db)
        if not pe.edition_en_direct(g, event):
            return None
        loc = pe.principal_location(g)
        if not loc:
            return None
        snap = db["data_access"].find_one(
            {"requested_location_id": str(loc.get("id")),
             "requested_location_type": loc.get("type") or "Area"},
            sort=[("timestamp", -1)])
        return pe.presents_principal(db, snap, g)
    except Exception:
        logger.warning("portes_etat : presents illisibles", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Bilan RETEX d'une edition
# ---------------------------------------------------------------------------

def bilan(db, event, year):
    """Par porte et par jour : volumes, pics, modes, charge de securite.

    Lecture de l'edition entiere (archive ou direct), en une passe.
    """
    raw = charger(db, event, year)
    if raw is None:
        return {"event": event, "year": int(year), "disponible": False,
                "motif": "aucune donnee de controle d'acces live pour cette edition"}
    staffing = staffing_securite(db, event, year, raw["gate_names"])
    race = raw.get("race_dt")
    jours = defaultdict(lambda: defaultdict(lambda: {
        "e": 0, "s": 0, "ve": 0, "vs": 0, "refus": 0,
        "pic_e": 0, "pic_e_t": None, "pic_s": 0, "pic_s_t": None,
        "modes": defaultdict(int), "charge_max": None, "charge_max_t": None,
        "min_sature": 0, "pda_renfort_q": 0, "agents_max": 0}))
    for nom, p in raw["portes"].items():
        if p.get("kind") != "porte":
            continue
        q = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
        for t, v in p["buckets"].items():
            c = q[floor15(t)]
            for i in range(6):
                c[i] += v[i]
        qdev = defaultdict(dict)
        tripode = set()
        for t, dd in p["devices"].items():
            for cp, (e, s, kind) in dd.items():
                c = qdev[floor15(t)].setdefault(cp, [0, 0, kind])
                c[0] += e
                c[1] += s
                if kind == "tripode":
                    tripode.add(t.date())
        for t, v in q.items():
            j = jours[t.date().isoformat()][nom]
            e, s = v[0] + v[2], v[1] + v[3]
            j["e"] += v[0]
            j["s"] += v[1]
            j["ve"] += v[2]
            j["vs"] += v[3]
            j["refus"] += v[4] + v[5]
            if e * 4 > j["pic_e"]:
                j["pic_e"], j["pic_e_t"] = e * 4, t.strftime("%H:%M")
            if s * 4 > j["pic_s"]:
                j["pic_s"], j["pic_s_t"] = s * 4, t.strftime("%H:%M")
            devs = qdev.get(t, {})
            _ce, _cs, n_e, n_s, n_es = _capacites_appareils(devs)
            m = mode_de(e, s, n_e + n_es, n_s + n_es)
            if m:
                j["modes"][m] += 15
            ag = door_security.agents_at(staffing, nom, t) if staffing else 0
            if ag:
                j["agents_max"] = max(j["agents_max"], ag)
                c = v[0] * 4 / float(ag * AGENT_RATE_H)
                # Sous MIN_RATE_ALERTE (nuit, 1 agent de garde), un ratio eleve
                # ne dit rien : ni pic de charge, ni temps de saturation.
                if v[0] * 4 >= MIN_RATE_ALERTE:
                    if j["charge_max"] is None or c > j["charge_max"]:
                        j["charge_max"], j["charge_max_t"] = c, t.strftime("%H:%M")
                    if c >= CHARGE_SATURATION:
                        j["min_sature"] += 15
            if t.date() in tripode and any(k == "pda" for _e, _s, k in devs.values()):
                j["pda_renfort_q"] += 1
    sortie = []
    for jour in sorted(jours):
        rows = []
        for nom, j in jours[jour].items():
            scans = j["e"] + j["s"] + j["ve"] + j["vs"] + j["refus"]
            if not (j["e"] + j["s"] + j["ve"] + j["vs"]):
                continue
            rows.append({
                "porte": nom, "entrees": j["e"], "sorties": j["s"],
                "vehicules": j["ve"] + j["vs"],
                "refus_pct": round(100.0 * j["refus"] / scans, 1) if scans else None,
                "pic_entree": j["pic_e"], "pic_entree_a": j["pic_e_t"],
                "pic_sortie": j["pic_s"], "pic_sortie_a": j["pic_s_t"],
                "minutes_mode": dict(j["modes"]),
                "charge_secu_max": round(j["charge_max"], 2) if j["charge_max"] is not None else None,
                "charge_secu_max_a": j["charge_max_t"],
                "minutes_saturation": j["min_sature"],
                "agents_max": j["agents_max"] or None,
                "pda_renfort_min": j["pda_renfort_q"] * 15,
            })
        if not rows:
            continue
        rows.sort(key=lambda r: -(r["entrees"] + r["sorties"]))
        d = date.fromisoformat(jour)
        sortie.append({"jour": jour, "jour_course": (d - race.date()).days if race else None,
                       "entrees": sum(r["entrees"] for r in rows),
                       "sorties": sum(r["sorties"] for r in rows),
                       "portes": rows})
    return {"event": event, "year": int(year), "disponible": True, "direct": raw["direct"],
            "race": race.isoformat(timespec="minutes") if race else None,
            "securite": bool(staffing), "jours": sortie,
            "seuils": {"agent_h": AGENT_RATE_H, "saturation": CHARGE_SATURATION}}


def editions(db):
    """[{event, year}] des editions ayant une archive live (RETEX)."""
    return [{"event": e, "year": y} for e, y in sorted(lsu.list_live_editions(db),
                                                       key=lambda r: (-r[1], r[0]))]
