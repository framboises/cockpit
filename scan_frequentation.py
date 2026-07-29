"""
Fréquentation journaliere de l'enceinte generale, comparee aux editions passees.

Alimente la vue « Frequentation » du rapport de scans : agregats par jour,
alignement au jour de course pour comparer N / N-1 / N-2, et croisement avec la
meteo.

Trois choix structurants, tous dictes par les donnees :

1. **Alignement au jour de course, pas au calendrier.** Les editions tombent a
   des dates differentes (24H MOTOS 2024 = 15-21 avril, 2023 = 10-16 avril) mais
   produisent un squelette d'offsets identique (J-5 ... J+1). Comparer le 20
   avril au 20 avril opposerait un jour de course a un jour d'essais.

2. **Le pic de presents est l'indicateur principal, pas les entrees.** Le nombre
   de portes instrumentees a grossi d'une edition a l'autre (24H AUTOS : 21 -> 22
   -> 26 -> 29). Le total d'entrees 2022 -> 2023 bondit de +68 % : c'est la
   mesure qui a change, pas la foule. Le pic de presents, lui, ne depend
   quasiment pas des portes instrumentees en marge.

3. **Un jour a zero en debut de periode est une absence de mesure**, pas une
   frequentation nulle : les capteurs n'etaient pas encore actifs (24H MOTOS
   2023 J-5, LMC 2022 J-4). L'afficher comme zero ferait croire a un effondrement.

La meteo vient de `donnees_meteo` (quotidien, 1990 -> 2026, 100 % des editions
couvertes). Le nom `historique_meteo` designe une route Flask, pas une
collection. Cette collection ne porte que quatre grandeurs : temperatures
extremes, precipitations, ensoleillement. Ni vent ni conditions — le vent
n'existe que dans `meteo_previsions`, a partir d'octobre 2024, et ce sont des
previsions, pas des observations.
"""

import logging
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

import pcorg_summary

logger = logging.getLogger(__name__)

WEATHER_COLLECTION = 'donnees_meteo'

# Collections geo, pour classer une unite de scan (porte / tribune / ...).
GEO_COLLECTIONS = ('portes', 'tribunes', 'hospitalites', 'terrains')

# Detection des pertes de controle d'acces. Une plage est retenue quand la
# presence reste significative ET que le volume de scans s'effondre sous une
# fraction de ce qu'on observe habituellement A LA MEME HEURE DU JOUR.
# Calibrer sur une mediane globale ferait remonter toutes les nuits, ou
# l'absence de scan est normale (les spectateurs dorment sur place).
ACCESS_PRESENCE_FLOOR = 0.25    # presence >= 25 % du pic de l'edition
ACCESS_COLLAPSE_RATIO = 0.20    # scans < 20 % de l'attendu pour cette heure
ACCESS_MIN_HOURS = 2            # une heure isolee n'est pas un evenement

# Editions dont les donnees sont inexploitables, verifiees une par une :
#   GPE 2022 : le cumul d'entrees finit a 0
#   GPE 2023 : `race` = 2023-09-09 alors que les donnees sont en octobre,
#              l'alignement serait decale de 28 jours
EXCLUDED_EDITIONS = {('GPE', 2022), ('GPE', 2023)}

# Sentinelle : `0` est une valeur meteo legitime, donc `.get(k) or defaut`
# effacerait les journees sans pluie. Meme precaution que analyse_ops.py:1054.
_MISSING = object()

# Cles Mongo accentuees, avec leurs variantes observees.
WEATHER_FIELDS = {
    'tmax': ['Température max (°C)', 'Temperature max (°C)', 'Temperature max (C)'],
    'tmin': ['Température min (°C)', 'Temperature min (°C)', 'Temperature min (C)'],
    'rain': ['Précipitations (mm)', 'Precipitations (mm)'],
    'sun': ['Ensoleillement (h)', 'Ensoleillement (h)'],
}


def _clean_number(value):
    """NaN et infinis -> None : un NaN BSON casse la serialisation JSON."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 2)


def event_aliases(db, event):
    """Noms sous lesquels un evenement peut vivre dans historique_controle.

    Reprend la logique de app.py:_event_hist_aliases, mais avec `db` en argument
    plutot que le global du module app : `SBK` et `SUPERBIKE` designent la meme
    edition 2024, et `race` n'existe que sur l'une des deux.
    """
    aliases = [event] if event else []
    try:
        doc = db['evenement'].find_one({'nom': event}, {'_id': 0, 'short': 1})
        short = (doc or {}).get('short')
        if short and short not in aliases:
            aliases.append(short)
    except Exception:
        pass
    return aliases


def _find_frequentation(db, event, year):
    """Document `frequentation` d'une edition, en essayant les alias."""
    aliases = event_aliases(db, event)
    for y in (int(year), str(year)):
        doc = db['historique_controle'].find_one(
            {'type': 'frequentation', 'event': {'$in': aliases}, 'year': y})
        if doc:
            return doc
    return None


def _race_date(db, event, year):
    """Date de course (date seule), via la chaine de repli de pcorg_summary.

    Indispensable : le champ `race` manque sur TOUS les documents
    `frequentation` de 2025, mais existe sur les documents `portes`
    correspondants — ce que _load_race_dt sait deja gerer.
    """
    dt = pcorg_summary._load_race_dt(db, event, year)
    return dt.date() if dt else None


# ---------------------------------------------------------------------------
# Agregation journaliere
# ---------------------------------------------------------------------------

def daily_aggregate(doc, race_date):
    """Serie journaliere d'une edition, indexee par offset au jour de course.

    `entree` et `sortie` sont des cumuls : les entrees du jour sont la somme des
    deltas positifs. `present` est instantane, donc son maximum est direct.
    """
    by_day = pcorg_summary._index_freq_by_day(doc)
    if not by_day:
        return []

    # Deltas du cumul, sur la serie complete et ordonnee (les bornes de journee
    # ne doivent pas casser la continuite du cumul).
    records = sorted((r for recs in by_day.values() for r in recs),
                     key=lambda r: str(r.get('date')))
    deltas = {}
    # Le cumul demarre a 0 avant la premiere mesure : partir de None ferait
    # perdre la valeur initiale du compteur, et le total de l'edition
    # n'egalerait plus le cumul final du document.
    prev_e = prev_s = 0
    for rec in records:
        key = str(rec.get('date'))[:10]
        cur_e = int(rec.get('entree') or 0)
        cur_s = int(rec.get('sortie') or 0)
        d = deltas.setdefault(key, {'e': 0, 's': 0})
        d['e'] += max(0, cur_e - prev_e)
        d['s'] += max(0, cur_s - prev_s)
        prev_e, prev_s = cur_e, cur_s

    days = []
    for date_key in sorted(by_day):
        recs = by_day[date_key]
        peak = pcorg_summary._max_present(recs)
        peak_hour = None
        if peak is not None:
            for rec in recs:
                if int(rec.get('present') or 0) == peak:
                    peak_hour = str(rec.get('date'))[11:16]
                    break
        try:
            d = datetime.strptime(date_key, '%Y-%m-%d').date()
        except ValueError:
            continue
        offset = (d - race_date).days if race_date else None
        flow = deltas.get(date_key, {'e': 0, 's': 0})

        # Capteurs pas encore actifs : aucune entree ET aucun present sur toute
        # la journee. C'est une absence de mesure, pas une frequentation nulle.
        measured = bool(flow['e'] or flow['s'] or peak)
        days.append({
            'date': date_key,
            'offset': offset,
            'entrees': flow['e'] if measured else None,
            'sorties': flow['s'] if measured else None,
            'peak_present': peak if measured else None,
            'peak_hour': peak_hour if measured else None,
            'hours': len(recs),
            'measured': measured,
        })
    return days


def hourly_series(doc, race_date):
    """Courbe horaire alignee : [{slot, offset, hour, present}].

    `slot` = offset_jours * 24 + heure. Cet axe continu permet de superposer
    plusieurs editions dont les dates calendaires different.
    """
    out = []
    for rec in doc.get('data') or []:
        raw = rec.get('date')
        key = raw[:10] if isinstance(raw, str) else (
            raw.strftime('%Y-%m-%d') if hasattr(raw, 'strftime') else None)
        if not key:
            continue
        try:
            d = datetime.strptime(key, '%Y-%m-%d').date()
        except ValueError:
            continue
        hour_txt = raw[11:13] if isinstance(raw, str) and len(raw) >= 13 else (
            raw.strftime('%H') if hasattr(raw, 'strftime') else None)
        try:
            hour = int(hour_txt)
        except (TypeError, ValueError):
            continue
        offset = (d - race_date).days if race_date else 0
        out.append({
            'slot': offset * 24 + hour,
            'offset': offset,
            'hour': hour,
            'present': int(rec.get('present') or 0),
        })
    out.sort(key=lambda x: x['slot'])
    return out


def _geo_index(db):
    """`_id_feature` -> collection geo, pour classer une unite de scan."""
    out = {}
    for coll in GEO_COLLECTIONS:
        try:
            doc = db[coll].find_one() or {}
        except Exception:
            logger.warning('Collection geo %s illisible', coll, exc_info=True)
            continue
        for feat in doc.get('features') or []:
            props = feat.get('properties') or {}
            if props.get('_id_feature'):
                out[str(props['_id_feature'])] = coll
    return out


def _portes_doc(db, event, year):
    """Document `historique_controle{type: portes}` d'une edition, ou None."""
    aliases = event_aliases(db, event)
    for y in (int(year), str(year)):
        doc = db['historique_controle'].find_one(
            {'type': 'portes', 'event': {'$in': aliases}, 'year': y})
        if doc:
            return doc
    return None


def door_inventory(doc, geo_index):
    """Unites de scan d'une edition : [{name, category}].

    ⚠ Ce document est le SEUL inventaire par edition disponible en base, et il
    ne contient que des portes. Les zones, tribunes et hospitalites n'existent
    que pour l'edition courante (`parking_scans` 2025, `complet` 24H MOTOS
    2024) : aucune comparaison pluriannuelle n'est possible sur ces categories.
    """
    out = []
    for door in (doc or {}).get('doors') or []:
        name = (door.get('name') or '').strip()
        if not name:
            continue
        fid = door.get('doors_id')
        out.append({
            'name': name,
            # Sans rattachement geographique, l'unite est un service mobile
            # (UAM, HELPDESK, LITIGE) et pas un lieu de passage.
            'category': geo_index.get(str(fid)) if fid else 'sans_lieu',
        })
    return sorted(out, key=lambda d: d['name'])


def _door_hourly_scans(doc):
    """'YYYY-MM-DDTHH' -> total de scans, toutes portes confondues."""
    per_hour = defaultdict(int)
    for door in (doc or {}).get('doors') or []:
        for scan in door.get('scans') or []:
            ts = scan.get('timestamp')
            key = (ts.strftime('%Y-%m-%dT%H') if hasattr(ts, 'strftime')
                   else str(ts)[:13])
            if len(key) == 13:
                per_hour[key] += int(scan.get('scan_count') or 0)
    return dict(per_hour)


def _presence_by_hour(doc):
    """'YYYY-MM-DDTHH' -> presents, depuis le document `frequentation`."""
    out = {}
    for rec in (doc or {}).get('data') or []:
        key = str(rec.get('date'))[:13]
        if len(key) == 13:
            out[key] = int(rec.get('present') or 0)
    return out


def access_control(freq_doc, portes_doc):
    """Perte de controle d'acces : plages ou l'enceinte n'est plus comptee.

    Deux constats distincts, qu'il ne faut pas confondre :

    - `solde_non_resorbe` : a la derniere mesure, N personnes sont encore
      comptees a l'interieur. Leurs sorties n'ont jamais ete enregistrees.
      C'est structurel — 70 a 94 % du pic selon les editions.
    - `events` : plages ou la presence reste forte alors que les scans
      s'effondrent. `controle_non_tenu` quand les portes scannent encore un
      peu (typiquement l'evacuation apres l'arrivee, portes ouvertes en
      grand), `mesure_absente` quand aucune donnee n'existe sur la plage.
    """
    pres = _presence_by_hour(freq_doc)
    if not pres:
        return None
    per_hour = _door_hourly_scans(portes_doc)

    peak = max(pres.values()) if pres else 0
    last_key = max(pres)
    out = {
        'peak_present': peak,
        'final_present': pres[last_key],
        'final_at': last_key,
        'final_pct': round(100.0 * pres[last_key] / peak, 1) if peak else None,
        'events': [],
    }
    if not per_hour or not peak:
        return out

    # Attendu par heure du jour : a 03h on n'attend pas le volume de 11h.
    by_hod = defaultdict(list)
    for key, val in per_hour.items():
        by_hod[int(key[11:13])].append(val)
    expected = {h: statistics.median(v) for h, v in by_hod.items()}

    floor = peak * ACCESS_PRESENCE_FLOOR
    hours = sorted(set(per_hour) | set(pres))
    carried, run, runs = 0, [], []
    for key in hours:
        # La serie de presence s'arrete souvent avant celle des portes : on
        # reporte la derniere valeur connue plutot que de perdre la plage qui
        # nous interesse le plus, celle d'apres l'arrivee.
        if key in pres:
            carried = pres[key]
        exp = expected.get(int(key[11:13]), 0)
        scans = per_hour.get(key)
        gap = scans is None
        scans = scans or 0
        collapsed = exp > 0 and scans < exp * ACCESS_COLLAPSE_RATIO
        if carried >= floor and (collapsed or gap):
            run.append((key, carried, scans, exp, gap))
        else:
            if len(run) >= ACCESS_MIN_HOURS:
                runs.append(run)
            run = []
    if len(run) >= ACCESS_MIN_HOURS:
        runs.append(run)

    for r in runs:
        out['events'].append({
            'start': r[0][0], 'end': r[-1][0], 'hours': len(r),
            'present_max': max(x[1] for x in r),
            'scans': sum(x[2] for x in r),
            'scans_expected': int(sum(x[3] for x in r)),
            'kind': 'mesure_absente' if all(x[4] for x in r) else 'controle_non_tenu',
        })
    return out


def load_editions(db, event, year, back=2):
    """Edition courante + les `back` precedentes reellement exploitables.

    Retourne une liste [plus recente -> plus ancienne]. Une edition sans
    document `frequentation`, sans date de course, ou explicitement exclue est
    ignoree — sans faire echouer les autres.
    """
    raw = []
    y = int(year)
    candidate = y
    # On remonte plus loin que `back` : certaines editions manquent (LMC est
    # biennal, 24H CAMIONS n'a pas 2025).
    while len(raw) <= back and candidate > y - (back + 4):
        if (event, candidate) not in EXCLUDED_EDITIONS:
            doc = _find_frequentation(db, event, candidate)
            race = _race_date(db, event, candidate)
            if doc and race:
                raw.append({'year': candidate, 'doc': doc, 'race': race})
            elif doc and not race:
                logger.warning('Edition %s %s ignoree : date de course introuvable',
                               event, candidate)
        candidate -= 1

    races = _normalize_race_dates(event, raw)
    geo = _geo_index(db)

    editions = []
    for item in raw:
        race = races[item['year']]
        portes_doc = _portes_doc(db, event, item['year'])
        units = door_inventory(portes_doc, geo)
        editions.append({
            'year': item['year'],
            'race_date': race.isoformat(),
            'is_current': item['year'] == y,
            'days': daily_aggregate(item['doc'], race),
            'hourly': hourly_series(item['doc'], race),
            'source': item['doc'].get('source') or 'collecte_temps_reel',
            'doors': len(units) or None,
            'units': units,
            'access_control': access_control(item['doc'], portes_doc),
        })
    return editions


def compare_units(editions):
    """Portes actives d'une edition a l'autre : communes, apparues, disparues.

    Repond a « quelles portes etaient ouvertes cette annee et pas l'an dernier »,
    la seule comparaison d'inventaire que les donnees permettent.
    """
    current = next((e for e in editions if e['is_current']), None)
    if not current or not current.get('units'):
        return None
    cur_names = {u['name'] for u in current['units']}
    by_cat = defaultdict(list)
    for u in current['units']:
        by_cat[u['category'] or 'sans_lieu'].append(u['name'])

    versus = []
    for ed in editions:
        if ed['is_current'] or not ed.get('units'):
            continue
        prev = {u['name'] for u in ed['units']}
        versus.append({
            'year': ed['year'],
            'total': len(prev),
            'added': sorted(cur_names - prev),
            'removed': sorted(prev - cur_names),
            'common': len(cur_names & prev),
        })
    return {
        'year': current['year'],
        'total': len(cur_names),
        'by_category': {k: sorted(v) for k, v in sorted(by_cat.items())},
        'versus': versus,
        # Les zones, tribunes et hospitalites n'ont pas d'inventaire par
        # edition en base : le dire plutot que laisser croire a un perimetre
        # complet.
        'categories_covered': ['portes'],
    }


def _normalize_race_dates(event, raw):
    """Recale les dates de course sur un meme jour de semaine.

    Le champ `race` ne designe pas la meme chose selon le millesime : jusqu'a
    2024 il porte le DEPART (24H AUTOS 2024 : samedi 16h), en 2025 il porte
    l'ARRIVEE (dimanche 14h). Aligner tel quel compare le samedi d'une edition
    au dimanche d'une autre — un decalage d'un jour entier sur toute la vue,
    invisible parce que les courbes restent plausibles.

    On ne corrige pas `pcorg_summary._load_race_dt`, qui sert aux resumes
    quotidiens en production. On normalise ici, sur le seul invariant qui ne
    depende pas du format de course : un evenement annuel revient chaque annee
    le meme jour de semaine. Le jour dominant sur les editions chargees fait
    reference, les autres sont recalees dessus (au plus 3 jours d'ecart).

    Retourne {annee: date}.
    """
    out = {item['year']: item['race'] for item in raw}
    if len(raw) < 2:
        return out

    counts = {}
    for item in raw:
        wd = item['race'].weekday()
        counts[wd] = counts.get(wd, 0) + 1
    # Egalite : le jour de l'edition la plus ancienne l'emporte. Les vieilles
    # editions portent le champ `race` d'origine, celui du depart.
    top = max(counts.values())
    tied = [wd for wd, n in counts.items() if n == top]
    if len(tied) > 1:
        target = raw[-1]['race'].weekday()
    else:
        target = tied[0]

    for item in raw:
        race = item['race']
        delta = (race.weekday() - target) % 7
        if delta == 0:
            continue
        shift = delta if delta <= 3 else delta - 7
        out[item['year']] = race - timedelta(days=shift)
        logger.info('%s %s : date de course recalee de %s a %s '
                    '(alignement sur le jour de semaine dominant)',
                    event, item['year'], race, out[item['year']])
    return out


# ---------------------------------------------------------------------------
# Meteo
# ---------------------------------------------------------------------------

def load_weather(db, dates):
    """Meteo quotidienne pour une liste de dates 'YYYY-MM-DD'.

    Retourne {date: {tmax, tmin, rain, sun}}. Les cles Mongo sont accentuees et
    `0` est une valeur legitime, d'ou la lecture par sentinelle.
    """
    if not dates:
        return {}
    wanted = []
    for d in dates:
        try:
            wanted.append(datetime.strptime(d, '%Y-%m-%d'))
        except (TypeError, ValueError):
            continue
    if not wanted:
        return {}

    out = {}
    try:
        cur = db[WEATHER_COLLECTION].find({'Date': {'$in': wanted}})
    except Exception:
        logger.warning('Lecture meteo impossible', exc_info=True)
        return {}

    for doc in cur:
        raw = doc.get('Date')
        key = raw.strftime('%Y-%m-%d') if hasattr(raw, 'strftime') else str(raw)[:10]
        entry = {}
        for name, candidates in WEATHER_FIELDS.items():
            value = _MISSING
            for cand in candidates:
                value = doc.get(cand, _MISSING)
                if value is not _MISSING:
                    break
            entry[name] = _clean_number(None if value is _MISSING else value)
        out[key] = entry
    return out


# ---------------------------------------------------------------------------
# Insights calcules (aucun cout, purs calculs)
# ---------------------------------------------------------------------------

def _pearson(xs, ys):
    """Correlation de Pearson sur les paires completes, ou None."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    n = len(pairs)
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    num = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    dx = math.sqrt(sum((p[0] - mx) ** 2 for p in pairs))
    dy = math.sqrt(sum((p[1] - my) ** 2 for p in pairs))
    if not dx or not dy:
        return None
    return round(num / (dx * dy), 3)


def compute_insights(editions, weather):
    """Constats chiffres tires des seules donnees, sans appel modele."""
    insights = {}
    current = next((e for e in editions if e['is_current']), None)
    if not current:
        return insights

    measured = [d for d in current['days'] if d['measured']]
    if not measured:
        return insights

    busiest = max(measured, key=lambda d: d['peak_present'] or 0)
    insights['busiest_day'] = {
        'date': busiest['date'], 'offset': busiest['offset'],
        'peak_present': busiest['peak_present'], 'peak_hour': busiest['peak_hour'],
    }

    # Stabilite de l'heure de pic entre editions : elle est remarquablement
    # constante (16h AUTOS, 15h MOTOS), donc tout ecart merite d'etre vu.
    race_hours = []
    for ed in editions:
        day = next((d for d in ed['days'] if d['offset'] == 0 and d['measured']), None)
        if day and day['peak_hour']:
            race_hours.append({'year': ed['year'], 'hour': day['peak_hour'],
                               'peak_present': day['peak_present']})
    insights['race_day_peak_by_year'] = race_hours

    # Comparaison au meme offset : c'est la seule qui ait un sens.
    comparisons = []
    for ed in editions:
        if ed['is_current']:
            continue
        rows = []
        for day in measured:
            prev = next((d for d in ed['days']
                         if d['offset'] == day['offset'] and d['measured']), None)
            if not prev:
                continue
            rows.append({
                'offset': day['offset'],
                'peak_present': day['peak_present'],
                'peak_present_prev': prev['peak_present'],
                'delta_pct': (round((day['peak_present'] - prev['peak_present'])
                                    * 100.0 / prev['peak_present'], 1)
                              if prev['peak_present'] else None),
            })
        comparisons.append({'year': ed['year'], 'doors': ed['doors'], 'days': rows})
    insights['comparisons'] = comparisons

    # Correlations meteo, sur toutes les editions chargees pour avoir assez de
    # points (3 jours minimum, sinon None).
    peaks, rains, temps, suns = [], [], [], []
    for ed in editions:
        for day in ed['days']:
            if not day['measured']:
                continue
            w = weather.get(day['date']) or {}
            peaks.append(day['peak_present'])
            rains.append(w.get('rain'))
            temps.append(w.get('tmax'))
            suns.append(w.get('sun'))
    insights['weather_correlation'] = {
        'rain': _pearson(rains, peaks),
        'tmax': _pearson(temps, peaks),
        'sun': _pearson(suns, peaks),
        'sample_days': sum(1 for p in peaks if p is not None),
    }

    # Comparabilite : si le nombre de portes differe, les entrees ne sont pas
    # comparables d'une edition a l'autre.
    doors = {e['year']: e['doors'] for e in editions if e['doors']}
    insights['doors_by_year'] = doors
    insights['entries_comparable'] = len(set(doors.values())) <= 1 if doors else None

    # Controle d'acces : le solde jamais resorbe et les plages non tenues sont
    # des faits d'exploitation, pas des artefacts de mesure a masquer.
    insights['access_control'] = current.get('access_control')
    insights['access_control_by_year'] = [
        {'year': e['year'],
         'final_present': (e.get('access_control') or {}).get('final_present'),
         'final_pct': (e.get('access_control') or {}).get('final_pct'),
         'events': len((e.get('access_control') or {}).get('events') or [])}
        for e in editions if e.get('access_control')
    ]

    return insights


# ---------------------------------------------------------------------------
# Bloc injecte dans le payload du rapport
# ---------------------------------------------------------------------------

def build_frequentation_block(db, event, year, back=2):
    """Bloc `DATA.frequentation`, ou None si l'edition courante est absente."""
    editions = load_editions(db, event, year, back=back)
    if not editions or not any(e['is_current'] for e in editions):
        return None

    dates = [d['date'] for e in editions for d in e['days']]
    weather = load_weather(db, dates)
    insights = compute_insights(editions, weather)

    current = next(e for e in editions if e['is_current'])
    measured = [d for d in current['days'] if d['measured']]
    return {
        'event': event,
        'year': int(year),
        'editions': editions,
        'weather': weather,
        'insights': insights,
        'units_comparison': compare_units(editions),
        'totals': {
            'peak_present': max((d['peak_present'] or 0) for d in measured) if measured else 0,
            'entrees': sum((d['entrees'] or 0) for d in measured),
            'days_measured': len(measured),
            'days_total': len(current['days']),
        },
        # Le rapport ne doit jamais laisser croire que les entrees sont
        # comparables quand le perimetre de mesure a bouge.
        'entries_comparable': insights.get('entries_comparable'),
        'analysis': None,  # rempli a la generation si la cle API est presente
    }
