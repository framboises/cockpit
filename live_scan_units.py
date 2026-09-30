"""Unites de scan du rapport depuis l'ARCHIVE du controle d'acces live.

Pour une edition suivie par le live-controle (24H AUTOS, GPF, 24H CAMIONS,
LE MANS CLASSIC, SUPERBIKE, 24H MOTOS 2026...), le rapport de scans est
construit depuis les archives HSH (hsh_archive_tx_* / _erreurs_* /
_structure_*) plutot que depuis l'import Excel, qui reste le repli des
editions sans archive (toutes celles <= 2025).

Le resultat est un PSEUDO document `complet` EN MEMOIRE, au meme contrat que
`scan_import.build_complet_doc` (units -> name, kind, zone, porte,
_id_feature, category, ignored, devices, data_15min, uam_help, pda_renfort...)
pour que scan_report_build, scan_mapping, scan_staffing et scan_analysis le
consomment sans chemin special. Il n'est JAMAIS ecrit dans
historique_controle : ce document est la reference N-1 de la TV, de la montre
et des projections d'affluence.

Correspondance avec l'export Excel (meme systeme HSH) :
  - PORTE = gate HSH rattachee a l'Area de l'enceinte (628 ENCEINTE GENERALE) :
    une unite par gate, nommee comme la gate (PORTE NORD PIETONS...) ;
  - ZONE = Area HSH qui n'est pas l'enceinte : une unite par AREA, toutes ses
    gates agregees, nommee comme l'Area (AA BEAUSEJOUR, P OUEST, TRIBUNE 16,
    VISITES MUSEE dont la gate s'appelle ENTREE MUSEE). C'est exactement la
    colonne << zone >> de l'export.

Conventions de comptage (verifiees contre l'import Excel de 24H MOTOS 2026,
seule edition ayant les deux sources) :
  - les scans REFUSES sont exclus partout (choix utilisateur). `entrees` /
    `sorties` des tx comptent toutes les transactions, refusees comprises ;
    le detail des refus par sens vient de hsh_archive_erreurs_* (une ligne
    par transaction en statut non nul, rapprochee de sa tranche par
    checkpoint + 5 min), repli proportionnel si l'archive des erreurs est
    incomplete pour une tranche (24H AUTOS 2026 : 167 039 lignes pour
    182 025 refus). Les statuts 107 et 133 sont des passages (le compteur
    HSH les compte), pas des refus ;
  - PORTES : passages de PERSONNES seulement (vehicules exclus, comme le
    dashboard et la frequentation live). Contre l'Excel, ecart absolu cumule
    sur les portes : 18 691 (personnes valides) contre 26 982 (toutes
    personnes), 44 377 (valides vehicules compris), 55 079 (tout). PORTE SUD
    porte 12 869 scans vehicules que l'Excel ne compte pas ;
  - ZONES : vehicules INCLUS quand la categorie est un flux vehicules
    (aire_accueil, parking, autre : UNIT_CATEGORIES[*].flow) -- c'est le flux
    utile d'un parking, et la capacite retenue par le rapport y est de 250
    vehicules/h ; exclus pour tribune, paddock, hospitalite. En pratique la
    categorisation live ne reconnait pas les titres de parking comme
    vehicules (0 sur toutes les zones 2026) : sans effet aujourd'hui.
    ⚠ L'Excel des zones semble compter AUSSI les refus (P OUEST 3 105 contre
    2 473 valides, 3 148 en tout) : les zones live sont donc plus basses ;
  - pas de sens << Autre >> en live : total_autre = 0 ;
  - Areas du stade (parkings foot M1/M2/M3) ecartees par ID.

PIEGE : `tranche` (tx) et `date_utc` (erreurs) sont en HEURE DE PARIS
etiquetee UTC. Aucune conversion : ce sont deja les datetimes naifs Paris de
la chaine scans. Seules les bornes de la fenetre (UTC) passent par
presents_etat.to_tranche_label.

Creneaux 15 min etiquetes a leur FIN (convention de l'import) : les tranches
19:45, 19:50, 19:55 vont dans << 20:00 >>.
"""

import logging
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

import live_frequentation as lf
import presents_etat as pe
import scan_import

logger = logging.getLogger(__name__)

SOURCE = 'live_controle'
SOURCE_LABEL = "controle d'acces live (archive HSH)"
ENCLOSURE_ID = '628'
ENCLOSURE_NAME = 'ENCEINTE GENERALE'
UAM_GATE = 'UAM'

ERR_PREFIX = 'hsh_archive_erreurs_'
LIVE_ERR_COLLECTION = 'hsh_erreurs'

_TX_PROJ = {'_id': 0, 'checkpoint_id': 1, 'checkpoint_name': 1, 'gate_name': 1,
            'tranche': 1, 'entrees': 1, 'sorties': 1, 'entrees_vehicules': 1,
            'sorties_vehicules': 1, 'erreurs': 1}
_ERR_PROJ = {'_id': 0, 'checkpoint': 1, 'direction': 1, 'date_utc': 1,
             'type_scan': 1, 'status': 1}

# Statuts non nuls que le COMPTEUR HSH compte pourtant comme des passages
# (reconciliation compteur <-> transactions verifiee a l'unite sur les
# parkings foot : entrees = status 0 + 107 checkpoint hors ligne + 133 entree
# sans sortie prealable). Le collecteur les range dans `erreurs` ; ici ce sont
# des passages valides. Tous les autres statuts (106, 117, 119, 130, 35...)
# sont des refus.
ACCEPTED_ERROR_STATUSES = {'107', '133'}

# Areas etrangeres aux evenements du circuit : parkings des matchs du club de
# foot (stade MMArena, arborescence HSH dediee depuis septembre 2026). La
# fenetre de 24H CAMIONS 2026 contient le match du 19/09 : sans ce filtre,
# PARKING M1/M2/M3 apparaissaient comme des zones de l'edition. Filtre par ID
# d'Area, jamais par nom (un << P M1 >> circuit existe, Area 634).
EXCLUDED_AREA_IDS = {'1227', '1229', '1231'}
_ST_PROJ = {'_id': 0, 'location_id': 1, 'location_type': 1, 'location_name': 1,
            'parent_area': 1, 'parent_gate': 1}

VEHICLE_FLOW_CATEGORIES = {c['id'] for c in scan_import.UNIT_CATEGORIES
                           if c.get('flow') == 'vehicules'}

CONVENTIONS = {
    'refus': 'exclus (hsh_archive_erreurs par sens, repli proportionnel) ; '
             'statuts 107 et 133 comptes comme passages, comme le compteur HSH',
    'portes': 'passages de personnes valides, vehicules exclus',
    'zones': 'vehicules inclus pour les categories a flux vehicules '
             '(aire_accueil, parking, autre), exclus sinon',
    'autre': 'aucun sens << Autre >> en live (total_autre = 0)',
    'creneaux': '15 min etiquetes a leur fin, heure de Paris',
}

# Donnees brutes (avant rattachement), par edition : la lecture des archives
# coute une a deux secondes, le rattachement quelques millisecondes. L'editeur
# de mapping puis la regeneration relisent la meme edition coup sur coup.
RAW_TTL_SECONDS = 120
_RAW_CACHE = {}
_RAW_LOCK = threading.Lock()

EDITIONS_TTL_SECONDS = 600
_EDITIONS_CACHE = {'at': 0.0, 'rows': None}


def _norm(name):
    return re.sub(r'\s+', ' ', str(name or '')).strip().upper()


def floor5(dt):
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Scans refuses
# ---------------------------------------------------------------------------

def errors_collection(tx_coll):
    """Collection des transactions refusees correspondant a une collection tx."""
    if tx_coll == 'hsh_transactions_agg':
        return LIVE_ERR_COLLECTION
    if tx_coll.startswith(lf.TX_PREFIX):
        return ERR_PREFIX + tx_coll[len(lf.TX_PREFIX):]
    return None


def parse_err_ts(raw):
    """`date_utc` d'une transaction : heure de PARIS etiquetee UTC, naive."""
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None) if raw.tzinfo else raw
    try:
        return datetime.strptime(str(raw)[:19], '%Y-%m-%dT%H:%M:%S')
    except (TypeError, ValueError):
        return None


def error_buckets(err_docs):
    """Refus par (checkpoint, tranche 5 min) : {'e', 's', 've', 'vs', 'n'}.

    Meme cle que l'agregation du collecteur (live_controle.agreger_transactions),
    donc rapprochable tranche par tranche des documents tx. `n` compte TOUTES
    les transactions en statut non nul (ce que le collecteur a range dans
    `erreurs`) ; e/s/ve/vs seulement les vrais refus (hors
    ACCEPTED_ERROR_STATUSES).
    """
    out = defaultdict(lambda: {'e': 0, 's': 0, 've': 0, 'vs': 0, 'n': 0})
    for d in err_docs:
        ts = parse_err_ts(d.get('date_utc'))
        cp = (d.get('checkpoint') or {}).get('ID')
        if ts is None or cp is None:
            continue
        b = out[(str(cp), floor5(ts))]
        b['n'] += 1
        if str(d.get('status')) in ACCEPTED_ERROR_STATUSES:
            continue
        direction = d.get('direction')
        veh = d.get('type_scan') == 'vehicule'
        if direction == 'Entree':
            b['e'] += 1
            if veh:
                b['ve'] += 1
        elif direction == 'Sortie':
            b['s'] += 1
            if veh:
                b['vs'] += 1
    return out


def valid_counts(tx, err=None):
    """Passages VALIDES d'une tranche tx : (pers_e, pers_s, veh_e, veh_s, ref_e, ref_s).

    `entrees` / `sorties` / `*_vehicules` des tx comptent aussi les refus.
    `err` (error_buckets) donne les refus exacts par sens ; les refus que
    l'archive des erreurs n'a pas (tranche incomplete) sont repartis au
    prorata des passages restants, comme des refus de personnes.
    """
    e, s = _int(tx.get('entrees')), _int(tx.get('sorties'))
    ve, vs = _int(tx.get('entrees_vehicules')), _int(tx.get('sorties_vehicules'))
    total_err = _int(tx.get('erreurs'))
    err = err or {'e': 0, 's': 0, 've': 0, 'vs': 0, 'n': 0}
    ref_e, ref_s = min(e, err['e']), min(s, err['s'])
    rv_e, rv_s = min(ve, err['ve']), min(vs, err['vs'])
    missing = total_err - err['n']
    rest_e, rest_s = e - ref_e, s - ref_s
    if missing > 0 and rest_e + rest_s > 0:
        extra_e = int(round(missing * rest_e / float(rest_e + rest_s)))
        extra_e = min(extra_e, rest_e)
        extra_s = min(missing - extra_e, rest_s)
        ref_e += extra_e
        ref_s += extra_s
    veh_e, veh_s = max(ve - rv_e, 0), max(vs - rv_s, 0)
    pers_e = max(e - ref_e - veh_e, 0)
    pers_s = max(s - ref_s - veh_s, 0)
    return pers_e, pers_s, veh_e, veh_s, ref_e, ref_s


# ---------------------------------------------------------------------------
# Structure : gate -> Area, porte ou zone
# ---------------------------------------------------------------------------

def structure_maps(docs):
    """Documents de structure HSH -> tables de rattachement.

    Retourne {'gate_area': {gate: (area_id, area_name)},
              'cp_area': {checkpoint_id: (area_id, area_name)},
              'cp_gate': {checkpoint_id: gate},
              'area_name': {area_id: area_name}}.
    La gate est la cle de l'export Excel ; son Area vient du document Gate,
    a defaut de la majorite des checkpoints qui y sont rattaches.
    """
    area_name, gate_area, cp_area, cp_gate = {}, {}, {}, {}
    gate_votes = defaultdict(lambda: defaultdict(int))
    for d in docs:
        t = d.get('location_type')
        if t == 'Area' and d.get('location_id') is not None:
            area_name[str(d['location_id'])] = _norm(d.get('location_name'))
    for d in docs:
        t = d.get('location_type')
        pa = d.get('parent_area') or {}
        aid = str(pa['id']) if pa.get('id') is not None else None
        aname = _norm(pa.get('name')) or area_name.get(aid)
        if t == 'Gate':
            if aid or aname:
                gate_area[_norm(d.get('location_name'))] = (aid, aname)
        elif t == 'Checkpoint':
            cid = d.get('location_id')
            if cid is None:
                continue
            cid = str(cid)
            if aid or aname:
                cp_area[cid] = (aid, aname)
            g = _norm((d.get('parent_gate') or {}).get('name'))
            if g:
                cp_gate[cid] = g
                if aid or aname:
                    gate_votes[g][(aid, aname)] += 1
    for g, votes in gate_votes.items():
        if g not in gate_area:
            gate_area[g] = max(votes.items(), key=lambda kv: kv[1])[0]
    return {'gate_area': gate_area, 'cp_area': cp_area, 'cp_gate': cp_gate,
            'area_name': area_name}


def classify_gate(gate, cp_id, maps, enclosure_id=ENCLOSURE_ID):
    """(kind, nom d'unite, nom d'Area, id d'Area) d'un passage.

    Porte : gate de l'Area enceinte (id, ou nom ENCEINTE GENERALE en repli).
    Zone : l'Area elle-meme. L'Area de la GATE prime sur celle du checkpoint :
    un PDA mobile scanne sur plusieurs gates (PDA.226 : PORTE MAISON BLANCHE
    PIETONS puis P OUEST) et `gate_name` dit ou le passage a eu lieu.
    """
    gate = _norm(gate)
    area = maps['gate_area'].get(gate) or maps['cp_area'].get(str(cp_id))
    if area is None:
        # Aucune structure : une gate << PORTE ... >> est un acces d'enceinte,
        # le reste une zone a son propre nom.
        if gate.startswith('PORTE ') or gate == UAM_GATE:
            return 'porte', gate, ENCLOSURE_NAME, None
        return 'zone', gate, None, None
    aid, aname = area
    aname = aname or maps['area_name'].get(aid)
    if (aid is not None and str(aid) == str(enclosure_id)) or aname == ENCLOSURE_NAME:
        return 'porte', gate, aname or ENCLOSURE_NAME, aid
    return 'zone', aname or gate, aname, aid


def counts_vehicles(kind, category):
    """Les vehicules comptent-ils dans les passages de cette unite ?"""
    return kind == 'zone' and category in VEHICLE_FLOW_CATEGORIES


# ---------------------------------------------------------------------------
# Agregation (pur)
# ---------------------------------------------------------------------------

def aggregate(tx_docs, err_by_bucket, maps, enclosure_id=ENCLOSURE_ID):
    """Tranches tx -> unites brutes, sans rattachement ni convention vehicules.

    Retourne {'units': {(kind, name): unit}, 'uam_devices': set,
    'excluded_areas': {nom: passages}}. Une unite :
    {kind, name, zone, porte, gates, devices {nom: passages},
     series {fin de quart d'heure: [pers_e, pers_s, veh_e, veh_s]},
     refused [e, s], device_hours {(porte, device): {heure: n}}}.
    """
    units = {}
    uam_devices = set()
    excluded = defaultdict(int)
    for d in tx_docs:
        tr = d.get('tranche')
        if not isinstance(tr, datetime):
            continue
        tr = tr.replace(tzinfo=None) if tr.tzinfo else tr
        cp = str(d.get('checkpoint_id'))
        dev = str(d.get('checkpoint_name') or '').strip() or cp
        gate = _norm(d.get('gate_name'))
        if not gate:
            gate = maps['cp_gate'].get(cp) or ''
        if not gate:
            continue
        kind, name, area, area_id = classify_gate(gate, cp, maps, enclosure_id)
        if area_id is not None and str(area_id) in EXCLUDED_AREA_IDS:
            excluded[name] += _int(d.get('entrees')) + _int(d.get('sorties'))
            continue
        pers_e, pers_s, veh_e, veh_s, ref_e, ref_s = valid_counts(
            d, err_by_bucket.get((cp, floor5(tr))))
        if gate == UAM_GATE and scan_import._classify_device(dev) == 'pda' and (
                _int(d.get('entrees')) + _int(d.get('sorties')) + _int(d.get('erreurs'))):
            uam_devices.add(dev)
        u = units.get((kind, name))
        if u is None:
            u = units[(kind, name)] = {
                'kind': kind, 'name': name,
                'zone': ENCLOSURE_NAME if kind == 'porte' else name,
                'porte': name if kind == 'porte' else None,
                'area': area, 'gates': set(), 'devices': defaultdict(int),
                'series': defaultdict(lambda: [0, 0, 0, 0]),
                'refused': [0, 0], 'device_hours': defaultdict(lambda: defaultdict(int)),
            }
        u['gates'].add(gate)
        u['refused'][0] += ref_e
        u['refused'][1] += ref_s
        moved = pers_e + pers_s + veh_e + veh_s
        if not moved:
            continue
        u['devices'][dev] += moved
        q = lf.tranche_quarter(tr)
        slot = u['series'][q]
        slot[0] += pers_e
        slot[1] += pers_s
        slot[2] += veh_e
        slot[3] += veh_s
        # Detail par boitier des portes d'enceinte (hors ligne UAM) : grain de
        # l'aide UAM et des renforts PDA, comme scan_import.parse_scan_xlsx.
        # Heure du creneau etiquete a sa fin, comme l'import.
        if (kind == 'porte' and name != UAM_GATE and pers_e + pers_s
                and scan_import._classify_device(dev) == 'pda'):
            hour = q.replace(minute=0, second=0, microsecond=0)
            u['device_hours'][(name, dev)][hour] += pers_e + pers_s
    return {'units': units, 'uam_devices': uam_devices,
            'excluded_areas': dict(excluded)}


def build_unit(raw_unit, resolved_entry, renfort=None):
    """Unite brute + rattachement -> unite au contrat de `complet`."""
    feat = resolved_entry or {}
    kind, name = raw_unit['kind'], raw_unit['name']
    category = feat.get('category') or scan_import.guess_category(
        name, kind, feat.get('feature_collection'))
    with_veh = counts_vehicles(kind, category)
    series, present = [], 0
    tot_e = tot_s = veh_e_tot = veh_s_tot = 0
    for q in sorted(raw_unit['series']):
        pe_, ps_, ve_, vs_ = raw_unit['series'][q]
        e = pe_ + (ve_ if with_veh else 0)
        s = ps_ + (vs_ if with_veh else 0)
        veh_e_tot += ve_
        veh_s_tot += vs_
        if not (e or s):
            continue   # creneaux vides omis, comme l'export
        present += e - s
        tot_e += e
        tot_s += s
        series.append({'date': scan_import._iso(q), 'entree': e, 'sortie': s,
                       'autre': 0, 'present': present})
    devices = sorted(d for d, n in raw_unit['devices'].items() if n)
    classes = [scan_import._classify_device(d) for d in devices]
    renfort = renfort or {}
    manual_cat = feat.get('category_source') == 'manuel'
    return {
        'uam_help': renfort.get('uam_help'),
        'pda_renfort': renfort.get('pda_renfort'),
        'name': name,
        'kind': kind,
        'zone': raw_unit['zone'],
        'porte': raw_unit['porte'],
        '_id_feature': feat.get('_id_feature'),
        'feature_collection': feat.get('feature_collection'),
        'feature_source': feat.get('feature_source', 'aucun'),
        'category': category,
        # Categorie deduite de l'Area HSH (son nom porte le prefixe AA / P /
        # TRIBUNE / HOSPITALITE), sauf choix manuel memorise.
        'category_source': 'manuel' if manual_cat else 'hsh_area',
        'ignored': bool(feat.get('ignored')),
        'devices': devices,
        'device_count': len(devices),
        'pda_count': classes.count('pda'),
        'tripode_count': classes.count('tripode'),
        'other_count': classes.count('autre'),
        'total_entree': tot_e,
        'total_sortie': tot_s,
        'total_autre': 0,
        'data_15min': series,
        # Tracabilite propre au live (hors contrat du gabarit).
        'hsh_area': raw_unit.get('area'),
        'gates': sorted(raw_unit['gates']),
        'vehicles_counted': with_veh,
        'vehicles_entree': veh_e_tot,
        'vehicles_sortie': veh_s_tot,
        'refused_entree': raw_unit['refused'][0],
        'refused_sortie': raw_unit['refused'][1],
    }


# ---------------------------------------------------------------------------
# Lectures
# ---------------------------------------------------------------------------

def _enclosure_id(src):
    g = src.get('global') or {}
    loc = pe.principal_location(g) if g else None
    if loc and (loc.get('type') in (None, 'Area')) and loc.get('id') is not None:
        return str(loc['id'])
    return ENCLOSURE_ID


def _read_tx(db, sources, l0, l1):
    out = {}
    for coll, filtre in sources:
        q = dict(filtre)
        q['tranche'] = {'$gte': l0, '$lt': l1}
        try:
            for d in db[coll].find(q, _TX_PROJ):
                tr = pe._utc_naif(d.get('tranche'))
                if tr is None:
                    continue
                out[(str(d.get('checkpoint_id')), tr)] = d
        except Exception:
            logger.warning('live_scan_units : lecture de %s impossible', coll, exc_info=True)
    return list(out.values())


def _read_errors(db, sources, l0, l1, existing):
    """Transactions refusees de la fenetre (bornes en heure de Paris)."""
    docs = []
    seen = set()
    lo, hi = l0.strftime('%Y-%m-%dT%H:%M:%S'), l1.strftime('%Y-%m-%dT%H:%M:%S')
    for coll, filtre in sources:
        ecoll = errors_collection(coll)
        if not ecoll or ecoll in seen or ecoll not in existing:
            continue
        seen.add(ecoll)
        q = dict(filtre)
        q['date_utc'] = {'$gte': lo, '$lt': hi}
        try:
            docs.extend(db[ecoll].find(q, _ERR_PROJ))
        except Exception:
            logger.warning('live_scan_units : lecture de %s impossible', ecoll, exc_info=True)
    return docs, sorted(seen)


def _read_structure(db, sources):
    docs = {}
    for coll, filtre in sources:
        try:
            for d in db[coll].find(dict(filtre), _ST_PROJ):
                docs[(d.get('location_type'), d.get('location_id'))] = d
        except Exception:
            logger.warning('live_scan_units : lecture de %s impossible', coll, exc_info=True)
    return list(docs.values())


def load_raw(db, event, year, sources=None, use_cache=True):
    """Unites brutes d'une edition live, ou None si pas d'archive."""
    key = (str(event), int(year))
    now = time.time()
    if use_cache and sources is None:
        with _RAW_LOCK:
            hit = _RAW_CACHE.get(key)
            if hit and now - hit[0] < RAW_TTL_SECONDS:
                return hit[1]
    src = sources or lf.find_sources(db, event, year)
    if src is None:
        return None
    d0, d1 = src['window']
    l0, l1 = pe.to_tranche_label(d0), pe.to_tranche_label(d1)
    try:
        existing = set(db.list_collection_names())
    except Exception:
        existing = set()
    tx_docs = _read_tx(db, src['tx'], l0, l1)
    if not tx_docs:
        return None
    err_docs, err_colls = _read_errors(db, src['tx'], l0, l1, existing)
    st_docs = _read_structure(db, src['structure'])
    maps = structure_maps(st_docs)
    enclosure_id = _enclosure_id(src)
    agg = aggregate(tx_docs, error_buckets(err_docs), maps, enclosure_id)
    total_err = sum(_int(d.get('erreurs')) for d in tx_docs)
    raw = {
        'event': event, 'year': int(year),
        'race': scan_import.to_naive_paris_iso(src['race_dt']),
        'race_source': src.get('race_source'),
        'window': [l0.isoformat(), l1.isoformat()],
        'enclosure_id': enclosure_id,
        'direct': bool(src.get('direct')),
        'collections': sorted({c for c, _ in src['tx']} | set(err_colls)
                              | {c for c, _ in src['structure']}),
        'tx_docs': len(tx_docs),
        'refused_total': total_err,
        'refused_detailed': len(err_docs),
        'units': agg['units'],
        'uam_devices': sorted(agg['uam_devices']),
        'excluded_areas': agg['excluded_areas'],
    }
    if sources is None:
        with _RAW_LOCK:
            _RAW_CACHE[key] = (now, raw)
    return raw


def clear_cache():
    with _RAW_LOCK:
        _RAW_CACHE.clear()
    _EDITIONS_CACHE['at'] = 0.0
    _EDITIONS_CACHE['rows'] = None


# ---------------------------------------------------------------------------
# Pseudo document `complet`
# ---------------------------------------------------------------------------

def build_live_complet(db, event, year, sources=None, use_cache=True,
                       tripode_portes=None):
    """Pseudo document `complet` d'une edition live, EN MEMOIRE, ou None.

    Rattachement geographique et categorie par le resolveur de l'import
    (corrections manuelles de scan_feature_overrides, recolte des documents
    `portes`, rapprochement normalise), aide UAM et renforts PDA par la
    logique de l'import. Rien n'est ecrit.
    """
    raw = load_raw(db, event, year, sources=sources, use_cache=use_cache)
    if raw is None or not raw['units']:
        return None
    keys = sorted(raw['units'])
    resolved = scan_import.resolve_features(
        db, [{'kind': k, 'name': n} for k, n in keys], event, int(year))

    device_hours = {}
    for key in keys:
        for dk, hours in raw['units'][key]['device_hours'].items():
            device_hours[dk] = dict(hours)
    try:
        tri = tripode_portes if tripode_portes is not None else \
            scan_import.load_tripode_portes(db)
        uam_by_porte = scan_import.compute_uam_help(
            {'device_hours': device_hours, 'uam_devices': raw['uam_devices']}, tri)
    except Exception:
        logger.warning('live_scan_units : aide UAM / renforts impossibles', exc_info=True)
        uam_by_porte = {}

    units = [build_unit(raw['units'][k], resolved.get(k), uam_by_porte.get(k[1]))
             for k in keys]
    units = [u for u in units if u['data_15min'] or u['refused_entree'] or u['refused_sortie']]
    units.sort(key=lambda u: (u['kind'], u['name']))
    all_ts = sorted({r['date'] for u in units for r in u['data_15min']})
    return {
        'event': event,
        'year': int(year),
        'type': 'complet',
        'source': SOURCE,
        'source_label': SOURCE_LABEL,
        'in_memory': True,
        'race': raw['race'],
        'granularity': '15min',
        'source_file': None,
        'imported_at': None,
        'period_start': all_ts[0] if all_ts else None,
        'period_end': all_ts[-1] if all_ts else None,
        'slot_count': len(all_ts),
        'complet': units,
        'live': {
            'window': raw['window'],
            'direct': raw['direct'],
            'collections': raw['collections'],
            'tx_docs': raw['tx_docs'],
            'refused_total': raw['refused_total'],
            'refused_detailed': raw['refused_detailed'],
            'enclosure_id': raw['enclosure_id'],
            'uam_devices': raw['uam_devices'],
            'excluded_areas': raw['excluded_areas'],
            'conventions': CONVENTIONS,
        },
    }


def enclosure_hourly(units):
    """Serie horaire cumulee des portes retenues (forme du document
    `frequentation` de l'import : entree / sortie cumules, present)."""
    hourly = defaultdict(lambda: [0, 0])
    for u in units:
        if u.get('kind') != 'porte' or u.get('ignored'):
            continue
        for r in u.get('data_15min') or []:
            try:
                h = datetime.fromisoformat(r['date']).replace(minute=0, second=0)
            except (TypeError, ValueError, KeyError):
                continue
            hourly[h][0] += int(r.get('entree') or 0)
            hourly[h][1] += int(r.get('sortie') or 0)
    data, ce, cs = [], 0, 0
    for h in sorted(hourly):
        ce += hourly[h][0]
        cs += hourly[h][1]
        data.append({'date': h.strftime('%Y-%m-%dT%H:%M:%S'), 'entree': ce,
                     'sortie': cs, 'present': ce - cs})
    return data


# ---------------------------------------------------------------------------
# Inventaire des editions live
# ---------------------------------------------------------------------------

def list_live_editions(db, use_cache=True):
    """[(event, year)] des editions ayant une archive live exploitable.

    Candidats : les collections hsh_archive_tx_<TAG> (TAG = nom ou sigle de
    l'evenement + annee du clic, donc year ou year+1) et l'edition en direct.
    Chaque candidat passe par live_frequentation.has_live_archive, qui seul
    fait foi (fenetre de course).
    """
    now = time.time()
    if use_cache and _EDITIONS_CACHE['rows'] is not None and \
            now - _EDITIONS_CACHE['at'] < EDITIONS_TTL_SECONDS:
        return list(_EDITIONS_CACHE['rows'])
    try:
        existing = set(db.list_collection_names())
    except Exception:
        logger.warning('live_scan_units : inventaire des collections impossible', exc_info=True)
        return []
    tags = {c[len(lf.TX_PREFIX):] for c in existing if c.startswith(lf.TX_PREFIX)}
    candidates = set()
    try:
        events = list(db['evenement'].find({}, {'_id': 0, 'nom': 1, 'short': 1}))
    except Exception:
        events = []
    for ev in events:
        nom = ev.get('nom')
        if not nom:
            continue
        for label in (nom, ev.get('short')):
            if not label:
                continue
            prefix = lf.archive_tag(label, 2000)[:-len('_2000')] + '_'
            for tag in tags:
                m = re.match(re.escape(prefix) + r'(\d{4})$', tag)
                if m:
                    y = int(m.group(1))
                    candidates.add((nom, y))
                    # Suffixe = annee du clic : l'edition y-1 n'est candidate
                    # que si l'archive contient des tranches de cette annee-la
                    # (evite une resolution de course par evenement connu).
                    try:
                        if db[lf.TX_PREFIX + tag].find_one(
                                {'tranche': {'$lt': datetime(y, 1, 1)}},
                                {'_id': 1}) is not None:
                            candidates.add((nom, y - 1))
                    except Exception:
                        candidates.add((nom, y - 1))
    live_ev = pe.read_global(db).get('evenement')
    act = pe._utc_naif(pe.read_global(db).get('activation_timestamp'))
    if live_ev and act is not None:
        nom = live_ev
        for ev in events:
            if live_ev in (ev.get('nom'), ev.get('short')):
                nom = ev.get('nom') or live_ev
        candidates.add((nom, act.year))
    rows = sorted((e, y) for e, y in candidates if lf.has_live_archive(db, e, y))
    _EDITIONS_CACHE.update({'at': now, 'rows': rows})
    return list(rows)
