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
from datetime import datetime, timedelta

import pcorg_summary

logger = logging.getLogger(__name__)

WEATHER_COLLECTION = 'donnees_meteo'

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


def _door_count(db, event, year):
    """Nombre de portes instrumentees — la mesure de comparabilite d'une edition."""
    aliases = event_aliases(db, event)
    for y in (int(year), str(year)):
        doc = db['historique_controle'].find_one(
            {'type': 'portes', 'event': {'$in': aliases}, 'year': y},
            {'doors.name': 1})
        if doc:
            return len(doc.get('doors') or [])
    return None


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

    editions = []
    for item in raw:
        race = races[item['year']]
        editions.append({
            'year': item['year'],
            'race_date': race.isoformat(),
            'is_current': item['year'] == y,
            'days': daily_aggregate(item['doc'], race),
            'hourly': hourly_series(item['doc'], race),
            'source': item['doc'].get('source') or 'collecte_temps_reel',
            'doors': _door_count(db, event, item['year']),
        })
    return editions


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
