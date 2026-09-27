"""Presents sur site : UN seul calcul pour le cockpit et la montre.

Fonctions pures et lectures Mongo, `db` toujours passe en argument. Aucun
import Flask ni `app` : la montre (watch_state, watch_peaks, watch_pages) doit
produire le meme chiffre que le widget Controle d'acces de l'accueil et que
general-stats (TV), sans importer app.py -- meme raison d'etre que
meteo_etat.py et trafic_etat.py.

LA REGLE, appliquee partout :

    presents = current - correction - vehicules_presents

  - `current` : dernier releve du compteur Area HSH dans data_access (solde
    entrees - sorties, cumule depuis la DERNIERE REMISE A ZERO du compteur,
    pas depuis minuit).
  - `correction` : corrections_compteurs du document ___GLOBAL___.
  - `vehicules_presents` : solde entrees_vehicules - sorties_vehicules de
    hsh_transactions_agg, cumule depuis LE MEME INSTANT que `current` (voir
    counter_baseline), moins corrections_vehicules. Jamais de remise a zero
    quotidienne : un vehicule gare la veille est toujours sur site. Sur
    24H CAMIONS 2026, la remise a zero de minuit n'en retirait que ~740
    pour 1 297 reellement presents.

ATTENTION FUSEAUX : hsh_transactions_agg.tranche est en HEURE DE PARIS
etiquetee UTC (le champ Handshake `date_utc` porte en realite l'heure locale),
alors que data_access.timestamp et activation_timestamp sont du vrai UTC
naif. Toute comparaison passe par to_tranche_label().
"""

import bisect
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ_PARIS = ZoneInfo("Europe/Paris")
HSH_GLOBAL_ID = "___GLOBAL___"

_BASELINE_CACHE = {}
BASELINE_TTL = timedelta(minutes=2)

CATEGORIES = ("veh", "enf", "acc")
_CHAMPS_AGG = {
    "veh": ("entrees_vehicules", "sorties_vehicules"),
    "enf": ("entrees_enfants", "sorties_enfants"),
    "acc": ("entrees_accredites", "sorties_accredites"),
}


def _int(valeur, defaut=0):
    """`current`/`entries` sont des chaines, parfois "" ou "N/A"."""
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return defaut


def _utc_naif(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def to_tranche_label(dt_utc):
    """UTC (naif ou conscient) -> heure de Paris naive, l'echelle de `tranche`."""
    dt_utc = _utc_naif(dt_utc)
    if dt_utc is None:
        return None
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(TZ_PARIS).replace(tzinfo=None)


def read_global(db):
    return db["data_access"].find_one({"_id": HSH_GLOBAL_ID}) or {}


def principal_location(global_doc):
    """Location du compteur principal (dict {id, type, name}), repli sur la 1re."""
    locations = global_doc.get("locations_selectionnees") or []
    pid = global_doc.get("compteur_principal_id")
    if pid:
        for loc in locations:
            if str(loc.get("id")) == str(pid):
                return loc
    return locations[0] if locations else None


# ----------------------------------------------------------------------
# Debut de cumul d'un compteur
# ----------------------------------------------------------------------

def counter_baseline(db, location_id, location_type, activation_ts):
    """Instant (UTC naif) depuis lequel le compteur cumule : derniere remise a
    zero (chute de `entries`) depuis l'activation du live-controle, sinon
    l'activation elle-meme. None si rien de connu.

    Une lecture fautive isolee (0 puis retour a la valeur d'avant) n'est pas
    une remise a zero. Cache 2 min : le balayage couvre tous les releves
    depuis l'activation (~2 000 par zone sur une edition).
    """
    cle = (str(location_id), location_type, activation_ts)
    maintenant = datetime.now()
    hit = _BASELINE_CACHE.get(cle)
    if hit and maintenant - hit[0] < BASELINE_TTL:
        return hit[1]

    baseline = activation_ts
    filtre = {"requested_location_id": str(location_id)}
    if location_type:
        filtre["requested_location_type"] = location_type
    if activation_ts:
        filtre["timestamp"] = {"$gte": activation_ts}
    prev = None
    pending = None  # (timestamp, entries avant la chute) : chute a confirmer
    for s in db["data_access"].find(filtre, {"_id": 0, "timestamp": 1, "entries": 1}).sort("timestamp", 1):
        e = _int(s.get("entries"), None)
        if e is None:
            continue
        if pending is not None:
            if e < pending[1]:
                baseline = pending[0]
            pending = None
        if prev is not None and e < prev:
            pending = (s["timestamp"], prev)
        prev = e
    if pending is not None:
        baseline = pending[0]

    _BASELINE_CACHE[cle] = (maintenant, baseline)
    return baseline


def zone_baselines(db, locations, activation_ts):
    """{location_id str: debut de cumul en echelle `tranche`, arrondi 5 min, ou None}."""
    out = {}
    for loc in locations or []:
        lid = loc.get("id")
        if not lid:
            continue
        b = to_tranche_label(counter_baseline(db, lid, loc.get("type"), activation_ts))
        if b is not None:
            b = b.replace(minute=(b.minute // 5) * 5, second=0, microsecond=0)
        out[str(lid)] = b
    return out


def fallback_start(now_utc=None):
    """Repli quand un compteur n'a aucun debut connu : minuit (Paris) du jour."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    return to_tranche_label(now_utc).replace(hour=0, minute=0, second=0, microsecond=0)


def checkpoint_parents(db):
    """{checkpoint_id: {id de zone (area / venue) en str}}."""
    out = {}
    for cp in db["hsh_structure"].find(
            {"location_type": "Checkpoint"},
            {"_id": 0, "location_id": 1, "parent_area": 1, "parent_venue": 1}):
        pids = set()
        for cle in ("parent_area", "parent_venue"):
            parent = cp.get(cle) or {}
            if parent.get("id"):
                pids.add(str(parent.get("id")))
        if pids:
            out[cp.get("location_id")] = pids
    return out


# ----------------------------------------------------------------------
# Soldes courants (widget accueil, montre)
# ----------------------------------------------------------------------

def soldes_categories(db, locations, activation_ts, now_utc=None):
    """{location_id str: {entrees_veh, sorties_veh, entrees_enf, ...}} cumules
    depuis le debut de cumul de CHAQUE compteur, sans borne haute."""
    repli = fallback_start(now_utc)
    baselines = {lid: (b or repli) for lid, b in zone_baselines(db, locations, activation_ts).items()}
    parents = checkpoint_parents(db)

    group = {"_id": "$checkpoint_id"}
    for cat, (ch_e, ch_s) in _CHAMPS_AGG.items():
        group["entrees_" + cat] = {"$sum": {"$ifNull": ["$" + ch_e, 0]}}
        group["sorties_" + cat] = {"$sum": {"$ifNull": ["$" + ch_s, 0]}}

    out = {}
    # Une agregation par debut de cumul distinct (en pratique un ou deux).
    for debut in set(baselines.values()):
        lids = {lid for lid, b in baselines.items() if b == debut}
        pipeline = [{"$match": {"tranche": {"$gte": debut.replace(tzinfo=timezone.utc)}}},
                    {"$group": group}]
        for cp in db["hsh_transactions_agg"].aggregate(pipeline):
            for pid in parents.get(cp["_id"], ()):
                if pid not in lids:
                    continue
                tot = out.setdefault(pid, {k: 0 for k in group if k != "_id"})
                for k in tot:
                    tot[k] += cp.get(k, 0)
    return out


def presents_from_snapshot(snapshot, global_doc, soldes):
    """Presents d'une zone a partir de son dernier releve et de ses soldes.

    Meme formule que controle_access.js (widget accueil) :
    max(current - correction - max(veh - corr_veh, 0), 0).
    """
    if snapshot is None:
        return None
    lid = str(snapshot.get("requested_location_id"))
    current = _int(snapshot.get("current"), None)
    if current is None:
        return None
    correction = _int((global_doc.get("corrections_compteurs") or {}).get(lid))
    corr_veh = _int((global_doc.get("corrections_vehicules") or {}).get(lid))
    s = soldes.get(lid) or {}
    veh = max(s.get("entrees_veh", 0) - s.get("sorties_veh", 0) - corr_veh, 0)
    return max(current - correction - veh, 0)


def presents_principal(db, snapshot, global_doc=None, now_utc=None):
    """Presents du compteur principal pour un releve deja lu (montre, page 1)."""
    if snapshot is None:
        return None
    if global_doc is None:
        global_doc = read_global(db)
    lid = str(snapshot.get("requested_location_id"))
    loc = None
    for cand in global_doc.get("locations_selectionnees") or []:
        if str(cand.get("id")) == lid:
            loc = cand
            break
    if loc is None:
        loc = {"id": lid, "type": snapshot.get("requested_location_type")}
    soldes = soldes_categories(db, [loc], global_doc.get("activation_timestamp"), now_utc)
    return presents_from_snapshot(snapshot, global_doc, soldes)


# ----------------------------------------------------------------------
# Series (courbe TV, pics)
# ----------------------------------------------------------------------

class Vehicules:
    """Soldes vehicules cumules par zone, interrogeables a n'importe quel releve."""

    def __init__(self, db, locations, activation_ts, depuis_label=None, now_utc=None):
        self.repli = fallback_start(now_utc)
        if depuis_label is not None:
            self.repli = min(self.repli, depuis_label)
        self.baselines = {lid: (b or self.repli)
                          for lid, b in zone_baselines(db, locations, activation_ts).items()}
        debut = min([self.repli] + list(self.baselines.values()))
        parents = checkpoint_parents(db)
        evenements = {}
        for agg in db["hsh_transactions_agg"].find(
                {"tranche": {"$gte": debut.replace(tzinfo=timezone.utc)}},
                {"_id": 0, "checkpoint_id": 1, "tranche": 1,
                 "entrees_vehicules": 1, "sorties_vehicules": 1}):
            zones = parents.get(agg.get("checkpoint_id"))
            tr = _utc_naif(agg.get("tranche"))
            if not zones or tr is None:
                continue
            delta = (agg.get("entrees_vehicules") or 0) - (agg.get("sorties_vehicules") or 0)
            for z in zones:
                evenements.setdefault(z, []).append((tr, delta))
        self.prefix = {}
        for z, evs in evenements.items():
            evs.sort(key=lambda x: x[0])
            cumul, run = [], 0
            for _, d in evs:
                run += d
                cumul.append(run)
            self.prefix[z] = ([e[0] for e in evs], cumul)

    def presents_at(self, zone_id, at_dt, corr_veh=0, until_end=False):
        """Vehicules presents dans la zone au releve at_dt (UTC naif).
        until_end=True : toutes les tranches, sans borne haute (valeur
        courante, identique au widget accueil)."""
        entry = self.prefix.get(str(zone_id))
        if not entry or at_dt is None:
            return 0
        times, cumul = entry
        debut = self.baselines.get(str(zone_id)) or self.repli
        hi = len(times) if until_end else bisect.bisect_right(times, to_tranche_label(at_dt))
        lo = bisect.bisect_left(times, debut)
        if hi <= lo:
            return 0
        total = cumul[hi - 1] - (cumul[lo - 1] if lo > 0 else 0)
        return max(total - (corr_veh or 0), 0)


def serie_presents(db, location, debut_utc, fin_utc, global_doc=None, vehicules=None,
                   bucket_minutes=15):
    """[(bucket UTC naif, presents)] sur [debut, fin), dernier releve de chaque
    tranche -- le meme bucketing que la courbe et le pic du jour de la TV."""
    if global_doc is None:
        global_doc = read_global(db)
    lid = str(location.get("id"))
    ltype = location.get("type")
    correction = _int((global_doc.get("corrections_compteurs") or {}).get(lid))
    corr_veh = _int((global_doc.get("corrections_vehicules") or {}).get(lid))
    if vehicules is None:
        vehicules = Vehicules(db, global_doc.get("locations_selectionnees") or [location],
                              global_doc.get("activation_timestamp"),
                              depuis_label=to_tranche_label(debut_utc))
    filtre = {"requested_location_id": lid,
              "timestamp": {"$gte": _utc_naif(debut_utc), "$lt": _utc_naif(fin_utc)}}
    if ltype:
        filtre["requested_location_type"] = ltype
    serie = []
    for s in db["data_access"].find(filtre, {"_id": 0, "timestamp": 1, "current": 1}).sort("timestamp", 1):
        ts = s["timestamp"]
        # bucket_minutes=None : chaque releve garde son propre instant (pics).
        bucket = ts if not bucket_minutes else ts.replace(
            minute=(ts.minute // bucket_minutes) * bucket_minutes, second=0, microsecond=0)
        present = max(_int(s.get("current")) - correction
                      - vehicules.presents_at(lid, ts, corr_veh), 0)
        if serie and serie[-1][0] == bucket:
            serie[-1] = (bucket, present)
        else:
            serie.append((bucket, present))
    return serie


def pic_presents(db, location, debut_utc, fin_utc, global_doc=None):
    """(pic, instant du releve UTC naif) des presents sur [debut, fin), ou (None, None).

    Le debut est remonte au debut de cumul du compteur : un releve anterieur
    a la derniere remise a zero porte un `current` fantome (22 447 le 22/09
    sur 24H CAMIONS 2026, pour quelques centaines de personnes reelles)."""
    if global_doc is None:
        global_doc = read_global(db)
    baseline = counter_baseline(db, location.get("id"), location.get("type"),
                                global_doc.get("activation_timestamp"))
    debut = _utc_naif(debut_utc)
    if baseline is not None and baseline > debut:
        debut = baseline
    fin = _utc_naif(fin_utc)
    if debut >= fin:
        return None, None
    # Plus haut RELEVE, pas le dernier releve de chaque quart d'heure (la
    # courbe) : sinon un pic peut baisser en cours de quart d'heure et
    # afficher moins qu'un chiffre << presents >> deja vu a l'ecran.
    serie = serie_presents(db, location, debut, fin, global_doc, bucket_minutes=None)
    if not serie:
        return None, None
    bucket, pic = max(serie, key=lambda x: x[1])
    return pic, bucket


def paris_day_bounds_utc(jour):
    """[minuit, minuit + 1 j) heure de Paris, en UTC naif."""
    debut = datetime(jour.year, jour.month, jour.day, tzinfo=TZ_PARIS)
    fin = debut + timedelta(days=1)
    return _utc_naif(debut), _utc_naif(fin)


def edition_en_direct(global_doc, event):
    """Les releves ET les soldes vehicules de cette edition sont-ils en base ?

    Vrai tant que le live-controle est configure sur cette edition, qu'il
    soit encore actif ou deja coupe : couper le live-controle le dimanche
    soir ne purge rien, et le rapport du lundi 7h doit encore pouvoir
    calculer le pic de la veille comme le dashboard. Apres archivage, les
    releves ne sont plus dans data_access : pic_presents rend None et les
    appelants retombent sur leur ancienne chaine."""
    return bool(event) and global_doc.get("evenement") == event


# ----------------------------------------------------------------------
# N-1 : historique_controle, comme general-stats
# ----------------------------------------------------------------------

def _parse_date(raw):
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        return raw.date() if hasattr(raw, "date") else None
    except Exception:
        return None


def _aliases(db, event):
    aliases = [event]
    ev = db["evenement"].find_one({"nom": event}, {"_id": 0, "short": 1}) or {}
    if ev.get("short") and ev["short"] not in aliases:
        aliases.append(ev["short"])
    return aliases


def _serie_n1_instants(db, prev):
    """[(instant naif Paris, present)] de l'edition N-1, au pas le plus fin.

    ⚠️ Un creneau 15 min scan_import est etiquete a sa FIN : << 20:00 >>
    porte les passages de 19:45 a 20:00, donc l'etat a 20:00. Mesure sur
    24H MOTOS 2026, seule edition ayant a la fois l'import et les releves
    temps reel du compteur : correlation 0,996 avec les entrees live de
    [HH:MM-15, HH:MM], contre 0,971 pour [HH:MM, HH:MM+15]. La serie 15 min
    se lit donc telle quelle. La serie HORAIRE, elle, somme les creneaux
    H:00 a H:45 : son point << 20:00 >> est l'etat a 20:45. Lue telle quelle,
    la courbe N-1 avait 45 min d'avance sur la courbe N.

    15 min via scan_frequentation.enclosure_series (document `complet`,
    portes non ignorees) quand il existe : un point horaire faisait bouger
    la comparaison N-1 par paliers d'une heure, a cote d'un compteur N
    rafraichi toutes les trois minutes. Les creneaux sans passage sont
    omis a l'import ; le cumul est reporte pour avoir un point tous les
    quarts d'heure.
    """
    import scan_frequentation  # import local : pcorg_summary a l'import

    recs, granularite = [], "horaire"
    try:
        recs, granularite = scan_frequentation.enclosure_series(db, prev.get("event"), prev.get("year"))
    except Exception as exc:
        logger.warning("presents_etat : serie 15 min N-1 indisponible (%s)", exc)
    if granularite == "horaire":
        recs = prev.get("data") or []
        # Seules les series importees suivent cette convention ; on ne
        # decale pas une collecte temps reel.
        decalage = timedelta(minutes=45) if prev.get("source") == "scan_import" else timedelta(0)
    else:
        decalage = timedelta(0)
    pas = timedelta(minutes=15)

    points = []
    for rec in recs:
        rd = rec.get("date")
        try:
            dt = datetime.fromisoformat(rd) if isinstance(rd, str) else rd
        except ValueError:
            continue
        if not hasattr(dt, "hour"):
            continue
        v = rec.get("present")
        if not isinstance(v, (int, float)):
            continue
        points.append((dt.replace(tzinfo=None) + decalage, int(v)))
    points.sort(key=lambda p: p[0])

    if granularite != "horaire" and points:
        plein, i = [], 0
        t, fin = points[0][0], points[-1][0]
        courant = points[0][1]
        while t <= fin:
            while i < len(points) and points[i][0] <= t:
                courant = points[i][1]
                i += 1
            plein.append((t, courant))
            t += pas
        points = plein
    return points


def historique_n1(db, event, year, aliases=None):
    """Edition N-1 de reference pour les comparaisons live, ou None.

    {year, race (date), par_jour: {'YYYY-MM-DD': [{'hour': 'HH:MM', 'present'}]}}

    Edition anterieure la plus recente ayant un historique_controle
    {frequentation}, `race` de ce document (repli : document portes). Source
    commune a la TV (/api/live-controle/dashboard) et a la montre.
    """
    try:
        cy = int(year)
    except (TypeError, ValueError):
        return None
    if aliases is None:
        aliases = _aliases(db, event)
    prev = None
    for cand in db["historique_controle"].find(
            {"type": "frequentation", "event": {"$in": aliases}}).sort("year", -1):
        cyn = cand.get("year")
        if isinstance(cyn, (int, float)) and int(cyn) < cy:
            prev = cand
            break
    if prev is None:
        return None
    race_prev = _parse_date(prev.get("race"))
    if race_prev is None:
        portes = db["historique_controle"].find_one(
            {"type": "portes", "event": {"$in": aliases}, "year": int(prev.get("year"))},
            {"_id": 0, "race": 1})
        race_prev = _parse_date((portes or {}).get("race"))
    par_jour = {}
    for dt, present in _serie_n1_instants(db, prev):
        par_jour.setdefault(dt.strftime("%Y-%m-%d"), []).append(
            {"hour": dt.strftime("%H:%M"), "present": present})
    return {"year": int(prev.get("year")), "race": race_prev, "par_jour": par_jour}


def pic_n1_historique(db, event, year, jour, aliases=None):
    """Pic de presents N-1 au jour equivalent (decalage au jour de course),
    sur la serie de historique_n1 -- celle de la TV general-stats."""
    try:
        cy = int(year)
    except (TypeError, ValueError):
        return None
    doc_n = (db["parametrages"].find_one({"event": event, "year": str(cy)})
             or db["parametrages"].find_one({"event": event, "year": cy}) or {})
    data = doc_n.get("data") or {}
    race_n = _parse_date(data.get("race") or (data.get("globalHoraires") or {}).get("race"))
    if race_n is None:
        return None
    n1 = historique_n1(db, event, cy, aliases)
    if n1 is None or n1["race"] is None:
        return None
    recs = n1["par_jour"].get((n1["race"] + (jour - race_n)).strftime("%Y-%m-%d"))
    return max((r["present"] for r in recs), default=None) if recs else None
