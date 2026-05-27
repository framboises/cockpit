"""
Genere un rapport HTML autonome a partir de la collection parking_scans.

Lecture titan_dev.parking_scans (24h_du_mans/2025), export d'un JSON embarque
dans un fichier HTML unique avec onglets par zone, courbes par jour (precision
15 min) et KPIs (pic, jour le plus charge, plages de stress, ...).

Usage : python3 generate_parking_report.py
Sortie : parking_report.html (a la racine du repo).
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta

from pymongo import MongoClient

EVENT = '24h_du_mans'
YEAR = 2025
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
DB_NAME = 'titan_dev'
OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'parking_report.html')


def fmt_fr(n):
    return f'{int(n):,}'.replace(',', ' ')


def compute_peak_hour(intervals_sorted, value_fn):
    """Pic sur tranches horaires alignees (00h-01h, 01h-02h, ..., 23h-24h)."""
    if not intervals_sorted:
        return {'val': 0, 'start': None, 'end': None}
    buckets = defaultdict(int)
    for it in intervals_sorted:
        hour_start = it['ts'].replace(minute=0)
        buckets[hour_start] += value_fn(it)
    if not buckets:
        return {'val': 0, 'start': None, 'end': None}
    hour, val = max(buckets.items(), key=lambda x: x[1])
    return {
        'val': val,
        'start': hour.strftime('%Y-%m-%d %H:%M'),
        'end': (hour + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M'),
    }


_ENTREE = lambda it: int(it.get('entree') or 0)
_SORTIE = lambda it: int(it.get('sortie') or 0)
_FLOW = lambda it: int(it.get('entree') or 0) + int(it.get('sortie') or 0)


def _hour_text(w):
    """Rend 'YYYY-MM-DD entre 14h et 15h' (la date est reformatee cote JS)."""
    if not w or not w.get('start'):
        return '-'
    parts = w['start'].split(' ')
    date = parts[0]
    h = int(parts[1].split(':')[0])
    next_h = h + 1
    return f"{date} entre {h}h et {next_h}h"


def compute_stress_windows(intervals_sorted, min_flow_threshold=5):
    """Identifie les fenetres ou le flux (E+S) /15min depasse le p90."""
    flows = [(it['ts'], (it.get('entree') or 0) + (it.get('sortie') or 0)) for it in intervals_sorted]
    positive = sorted(f for _, f in flows if f > 0)
    if len(positive) < 8:
        return []
    p90 = positive[int(len(positive) * 0.9)]
    if p90 < min_flow_threshold:
        return []

    above = [(ts, f) for ts, f in flows if f >= p90]
    if not above:
        return []

    windows = []
    cur_start = above[0][0]
    cur_end = above[0][0]
    cur_total = above[0][1]
    cur_count = 1
    for ts, f in above[1:]:
        gap_min = (ts - cur_end).total_seconds() / 60
        if gap_min <= 30:  # tolerer 1 slot vide
            cur_end = ts
            cur_total += f
            cur_count += 1
        else:
            if cur_count >= 2:
                windows.append({
                    'start': cur_start.strftime('%Y-%m-%d %H:%M'),
                    'end_label': (cur_end + timedelta(minutes=15)).strftime('%H:%M'),
                    'total_flow': cur_total,
                    'duration_min': int((cur_end - cur_start).total_seconds() / 60) + 15,
                })
            cur_start = ts
            cur_end = ts
            cur_total = f
            cur_count = 1
    if cur_count >= 2:
        windows.append({
            'start': cur_start.strftime('%Y-%m-%d %H:%M'),
            'end_label': (cur_end + timedelta(minutes=15)).strftime('%H:%M'),
            'total_flow': cur_total,
            'duration_min': int((cur_end - cur_start).total_seconds() / 60) + 15,
        })
    windows.sort(key=lambda x: x['total_flow'], reverse=True)
    return windows[:5]


def compute_interruptions(intervals_sorted, min_gap_minutes=60, min_surrounding_flow=15):
    """Trous suspects : encadres par une activite reelle avant ET apres."""
    by_day = defaultdict(list)
    for it in intervals_sorted:
        d = it['ts'].strftime('%Y-%m-%d')
        e = int(it.get('entree') or 0)
        s = int(it.get('sortie') or 0)
        by_day[d].append((it['ts'], e + s))

    out = []
    for d, points in by_day.items():
        points.sort(key=lambda x: x[0])
        for i in range(len(points) - 1):
            cur_ts, _ = points[i]
            nxt_ts, _ = points[i + 1]
            gap_min = (nxt_ts - cur_ts).total_seconds() / 60
            if gap_min < min_gap_minutes:
                continue
            win_before_start = cur_ts - timedelta(hours=1)
            win_after_end = nxt_ts + timedelta(hours=1)
            flow_before = sum(f for ts, f in points if win_before_start <= ts <= cur_ts)
            flow_after = sum(f for ts, f in points if nxt_ts <= ts <= win_after_end)
            if flow_before < min_surrounding_flow or flow_after < min_surrounding_flow:
                continue
            missing = int(gap_min / 15) - 1
            out.append({
                'date': d,
                'start': (cur_ts + timedelta(minutes=15)).strftime('%H:%M'),
                'end': nxt_ts.strftime('%H:%M'),
                'duration_min': missing * 15,
                'gap_slots': missing,
                'flow_before': flow_before,
                'flow_after': flow_after,
            })
    out.sort(key=lambda x: x['duration_min'], reverse=True)
    return out[:5]


def build_analysis(intervals_sorted, day_totals, days_sorted, kpis, total_e, total_s,
                   zone_name=None, is_porte=False):
    if not intervals_sorted or not days_sorted:
        return None

    first_d, last_d = days_sorted[0], days_sorted[-1]
    if is_porte:
        overview = (
            f"Scans actifs sur {len(days_sorted)} jour"
            + ('s' if len(days_sorted) > 1 else '')
            + f", du {first_d} au {last_d}. Cumul periode : "
            f"{fmt_fr(total_e + total_s)} passages "
            f"({fmt_fr(total_e)} entrees + {fmt_fr(total_s)} sorties)."
        )
    else:
        overview = (
            f"Scans actifs sur {len(days_sorted)} jour"
            + ('s' if len(days_sorted) > 1 else '')
            + f", du {first_d} au {last_d}. Cumul periode : "
            f"{fmt_fr(total_e)} entrees, {fmt_fr(total_s)} sorties "
            f"(solde {fmt_fr(total_e - total_s)} presents en fin de periode)."
        )

    warnings = []
    if zone_name in ('AA ARNAGE', 'AA MULSANNE'):
        warnings.append(
            f"ATTENTION : {zone_name} cumule aire d'accueil + zone d'enceinte "
            "generale (passage commun de plusieurs flux). Les volumes observes "
            "sont nettement plus eleves qu'une aire d'accueil seule, ce qui "
            "explique le dimensionnement de capacite different (traitee comme "
            "tribune dans le calcul de staffing)."
        )

    highlights = []
    pp = kpis['peak_presents']
    if pp['val'] > 0 and not is_porte:
        highlights.append(
            f"Pic d'occupation : {fmt_fr(pp['val'])} presents le {pp['ts']}."
        )
    pe = kpis['peak_entree']
    ps = kpis['peak_sortie']
    if pe['val'] > 0 or ps['val'] > 0:
        highlights.append(
            f"Pic d'entrees /15 min : {fmt_fr(pe['val'])} le {pe['ts']}. "
            f"Pic de sorties /15 min : {fmt_fr(ps['val'])} le {ps['ts']}."
        )
    phe = kpis.get('peak_hour_e')
    phs = kpis.get('peak_hour_s')
    if phe and phe['val'] > 0:
        highlights.append(
            f"Pic d'entrees sur 1 h : {fmt_fr(phe['val'])} le {_hour_text(phe)}."
        )
    if phs and phs['val'] > 0:
        highlights.append(
            f"Pic de sorties sur 1 h : {fmt_fr(phs['val'])} le {_hour_text(phs)}."
        )
    pf = kpis['peak_flow']
    if pf['val'] > max(pe['val'], ps['val']) * 1.15:
        highlights.append(
            f"Croisement entrees/sorties maximal : {fmt_fr(pf['val'])} mouvements /15 min le {pf['ts']}."
        )
    phf = kpis.get('peak_hour_flow')
    if phf and phf['val'] > 0:
        highlights.append(
            f"Pic du flux total (E+S) sur 1 h : {fmt_fr(phf['val'])} mouvements le {_hour_text(phf)}."
        )
    bd = kpis['busiest_day_p']
    if bd.get('date') and not is_porte:
        highlights.append(
            f"Journee de plus forte occupation : {bd['date']} "
            f"({fmt_fr(bd['peak_p'])} presents au plus, vers {bd.get('peak_p_hm') or '-'})."
        )
    bde = kpis['busiest_day_e']
    if bde.get('date') and (is_porte or bde['date'] != (bd.get('date') if bd else None)):
        highlights.append(
            f"Journee de plus forte affluence en entrees : {bde['date']} "
            f"({fmt_fr(bde['e'])} entrees)."
        )

    # Variations jour a jour notables (entrees)
    swings = []
    days = list(days_sorted)
    for prev, cur in zip(days, days[1:]):
        e_prev = day_totals[prev]['e']
        e_cur = day_totals[cur]['e']
        if e_prev > 50 and e_cur > 50:
            delta = (e_cur - e_prev) * 100.0 / e_prev
            if abs(delta) >= 40:
                swings.append((cur, delta, e_prev, e_cur))
    swings.sort(key=lambda x: abs(x[1]), reverse=True)
    for cur, delta, e_prev, e_cur in swings[:2]:
        direction = 'hausse' if delta > 0 else 'baisse'
        if abs(delta) > 400 and e_prev > 0:
            ratio = e_cur / e_prev
            descr = f"x{ratio:.1f}"
        else:
            descr = f"{'+' if delta > 0 else ''}{round(delta)} %"
        highlights.append(
            f"Forte {direction} le {cur} : {fmt_fr(e_cur)} entrees "
            f"({descr} vs veille a {fmt_fr(e_prev)})."
        )

    # Solde net journee (entrees - sorties) le plus dechargeant
    biggest_outflow = None
    biggest_inflow = None
    for d in days_sorted:
        net = day_totals[d]['e'] - day_totals[d]['s']
        if biggest_outflow is None or net < biggest_outflow[1]:
            biggest_outflow = (d, net)
        if biggest_inflow is None or net > biggest_inflow[1]:
            biggest_inflow = (d, net)
    if biggest_outflow and biggest_outflow[1] < -100 and not is_porte:
        highlights.append(
            f"Vidage marque : {biggest_outflow[0]} avec un solde net de "
            f"{fmt_fr(biggest_outflow[1])} (sorties > entrees)."
        )

    stress_windows = compute_stress_windows(intervals_sorted)
    interruptions = compute_interruptions(intervals_sorted)

    return {
        'overview': overview,
        'warnings': warnings,
        'highlights': highlights,
        'stress_windows': stress_windows,
        'interruptions': interruptions,
    }


def serialize_zone(doc):
    by_day = defaultdict(list)
    peak_e = {'val': 0, 'ts': None}
    peak_s = {'val': 0, 'ts': None}
    peak_flow = {'val': 0, 'ts': None}
    peak_presents = {'val': 0, 'ts': None}
    day_totals = defaultdict(lambda: {'e': 0, 's': 0,
                                      'peak_p': 0, 'peak_p_hm': None,
                                      'peak_e': 0, 'peak_e_hm': None,
                                      'peak_s': 0, 'peak_s_hm': None})
    hour_totals = defaultdict(lambda: {'e': 0, 's': 0})

    # Tri chrono pour cumul "presents" coherent (entree - sortie depuis debut periode)
    intervals_sorted = sorted(doc['intervals'], key=lambda x: x['ts'])
    presents = 0
    for it in intervals_sorted:
        ts = it['ts']
        d = ts.strftime('%Y-%m-%d')
        hm = ts.strftime('%H:%M')
        e = int(it.get('entree') or 0)
        s = int(it.get('sortie') or 0)
        presents += e - s
        by_day[d].append({'hm': hm, 'e': e, 's': s, 'p': presents})
        day_totals[d]['e'] += e
        day_totals[d]['s'] += s
        if presents > day_totals[d]['peak_p']:
            day_totals[d]['peak_p'] = presents
            day_totals[d]['peak_p_hm'] = hm
        if e > day_totals[d]['peak_e']:
            day_totals[d]['peak_e'] = e
            day_totals[d]['peak_e_hm'] = hm
        if s > day_totals[d]['peak_s']:
            day_totals[d]['peak_s'] = s
            day_totals[d]['peak_s_hm'] = hm
        hour_totals[hm]['e'] += e
        hour_totals[hm]['s'] += s
        if e > peak_e['val']:
            peak_e = {'val': e, 'ts': ts.strftime('%Y-%m-%d %H:%M')}
        if s > peak_s['val']:
            peak_s = {'val': s, 'ts': ts.strftime('%Y-%m-%d %H:%M')}
        flow = e + s
        if flow > peak_flow['val']:
            peak_flow = {'val': flow, 'ts': ts.strftime('%Y-%m-%d %H:%M')}
        if presents > peak_presents['val']:
            peak_presents = {'val': presents, 'ts': ts.strftime('%Y-%m-%d %H:%M')}

    # Ne garder que les jours avec au moins un mouvement (E ou S > 0).
    days_sorted = sorted(d for d in by_day.keys() if day_totals[d]['e'] + day_totals[d]['s'] > 0)
    active_totals = {d: day_totals[d] for d in days_sorted}

    # Pics horaires sur tranches alignees (HHh - HH+1h) global + par jour
    peak_hour_e = compute_peak_hour(intervals_sorted, _ENTREE)
    peak_hour_s = compute_peak_hour(intervals_sorted, _SORTIE)
    peak_hour_flow = compute_peak_hour(intervals_sorted, _FLOW)
    day_intervals = defaultdict(list)
    for it in intervals_sorted:
        day_intervals[it['ts'].strftime('%Y-%m-%d')].append(it)
    for d in days_sorted:
        active_totals[d]['peak_hour_e'] = compute_peak_hour(day_intervals[d], _ENTREE)
        active_totals[d]['peak_hour_s'] = compute_peak_hour(day_intervals[d], _SORTIE)
        active_totals[d]['peak_hour_flow'] = compute_peak_hour(day_intervals[d], _FLOW)

    busiest_day_e = max(active_totals.items(), key=lambda x: x[1]['e']) if active_totals else (None, {'e': 0, 's': 0})
    busiest_day_p = max(active_totals.items(), key=lambda x: x[1]['peak_p']) if active_totals else (None, {'peak_p': 0, 'peak_p_hm': None})

    busiest_hour = max(hour_totals.items(), key=lambda x: x[1]['e'] + x[1]['s']) if hour_totals else (None, {'e': 0, 's': 0})

    # Top stress = max flux brut (E+S)
    stress = [{
        'ts': it['ts'].strftime('%Y-%m-%d %H:%M'),
        'e': int(it.get('entree') or 0),
        's': int(it.get('sortie') or 0),
        'flow': int(it.get('entree') or 0) + int(it.get('sortie') or 0),
    } for it in intervals_sorted]
    stress.sort(key=lambda x: x['flow'], reverse=True)
    top_stress = stress[:5]

    progression = []
    prev = None
    for d in days_sorted:
        tot = active_totals[d]['e']
        delta_pct = None
        if prev is not None and prev > 0:
            delta_pct = round((tot - prev) * 100.0 / prev, 1)
        progression.append({
            'date': d, 'e': tot, 's': active_totals[d]['s'],
            'peak_p': active_totals[d]['peak_p'], 'peak_p_hm': active_totals[d]['peak_p_hm'],
            'peak_e': active_totals[d]['peak_e'], 'peak_e_hm': active_totals[d]['peak_e_hm'],
            'peak_s': active_totals[d]['peak_s'], 'peak_s_hm': active_totals[d]['peak_s_hm'],
            'peak_hour_e': active_totals[d]['peak_hour_e'],
            'peak_hour_s': active_totals[d]['peak_hour_s'],
            'peak_hour_flow': active_totals[d]['peak_hour_flow'],
            'delta_pct': delta_pct,
        })
        prev = tot

    kpis = {
        'peak_entree': peak_e,
        'peak_sortie': peak_s,
        'peak_flow': peak_flow,
        'peak_presents': peak_presents,
        'peak_hour_e': peak_hour_e,
        'peak_hour_s': peak_hour_s,
        'peak_hour_flow': peak_hour_flow,
        'busiest_day_e': {'date': busiest_day_e[0], 'e': busiest_day_e[1]['e'], 's': busiest_day_e[1]['s']},
        'busiest_day_p': {'date': busiest_day_p[0], 'peak_p': busiest_day_p[1]['peak_p'], 'peak_p_hm': busiest_day_p[1].get('peak_p_hm')},
        'busiest_hour': {'hm': busiest_hour[0], 'e': busiest_hour[1]['e'], 's': busiest_hour[1]['s']},
        'top_stress': top_stress,
        'progression': progression,
    }

    analysis = build_analysis(
        intervals_sorted, active_totals, days_sorted, kpis,
        doc['total_entree'], doc['total_sortie'],
        zone_name=doc['zone'],
    )

    staffing = _serialize_staffing(doc.get('staffing'))
    return {
        'category': 'zone',
        'name': doc['zone'],
        'zone': doc['zone'],
        'total_entree': doc['total_entree'],
        'total_sortie': doc['total_sortie'],
        'days': days_sorted,
        'by_day': dict(by_day),
        'kpis': kpis,
        'analysis': analysis,
        'staffing': staffing,
    }


def _serialize_staffing(st):
    """Rend le staffing JSON-serializable (datetime -> str)."""
    if not st or not isinstance(st, dict):
        return None
    out = dict(st)
    if isinstance(out.get('generated_at'), datetime):
        out['generated_at'] = out['generated_at'].isoformat(timespec='seconds')
    return out


def serialize_porte(doc, tripodes_mode=False):
    """Adapte serialize_zone pour un doc porte (zone parente + porte + devices)."""
    pseudo = dict(doc)
    pseudo['zone'] = doc['porte']
    out = serialize_zone(pseudo)
    out['category'] = 'porte'
    out['name'] = doc['porte']
    out['porte'] = doc['porte']
    out['zone_parent'] = doc['zone']
    out['device_count'] = doc.get('device_count', 0)
    out['pda_count'] = doc.get('pda_count', 0)
    out['tripode_count'] = doc.get('tripode_count', 0)
    out['tripodes_mode'] = bool(tripodes_mode)
    out['zone'] = doc['zone']
    out['staffing'] = _serialize_staffing(doc.get('staffing'))
    out['uam_help'] = doc.get('uam_help')
    out['pda_renfort'] = doc.get('pda_renfort')
    # Re-genere l'analyse avec le flag is_porte (sans le concept de presents)
    intervals_sorted = sorted(doc['intervals'], key=lambda x: x['ts'])
    day_totals_redo = defaultdict(lambda: {'e': 0, 's': 0})
    for it in intervals_sorted:
        d = it['ts'].strftime('%Y-%m-%d')
        day_totals_redo[d]['e'] += int(it.get('entree') or 0)
        day_totals_redo[d]['s'] += int(it.get('sortie') or 0)
    days_sorted = sorted(d for d in day_totals_redo if day_totals_redo[d]['e'] + day_totals_redo[d]['s'] > 0)
    out['analysis'] = build_analysis(
        intervals_sorted, day_totals_redo, days_sorted, out['kpis'],
        doc['total_entree'], doc['total_sortie'],
        zone_name=doc['porte'], is_porte=True,
    )
    return out


def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    cur = db['parking_scans'].find({'event': EVENT, 'year': YEAR}).sort('zone', 1)
    zones = [serialize_zone(d) for d in cur]
    if not zones:
        raise SystemExit('Aucune donnee dans parking_scans pour ' + EVENT + '/' + str(YEAR))
    print(f'Zones serialisees : {len(zones)}')

    # Charge le flag place_config.tripodes depuis la collection portes geojson
    tripodes_flag = {}
    portes_geo = db['portes'].find_one() or {}
    for f in portes_geo.get('features', []) or []:
        p = f.get('properties') or {}
        nm = (p.get('Name') or '').strip().upper()
        pc = p.get('place_config') or {}
        if nm and pc.get('tripodes'):
            tripodes_flag[nm] = True
    print(f'Portes geojson avec tripodes=True : {len(tripodes_flag)} ({sorted(tripodes_flag)})')

    cur_p = db['porte_scans'].find({'event': EVENT, 'year': YEAR}).sort('porte', 1)
    portes = [serialize_porte(d, tripodes_mode=tripodes_flag.get(d['porte'].upper(), False))
              for d in cur_p]
    print(f'Portes serialisees : {len(portes)}')

    payload = {
        'event': EVENT,
        'year': YEAR,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'zones': zones,
        'portes': portes,
    }

    data_json = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))

    html = HTML_TEMPLATE.replace('__DATA__', data_json)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f'Ecrit : {OUTPUT} ({size_kb:.1f} Ko)')


HTML_TEMPLATE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f1620">
<title>Scans parkings - 24h du Mans 2025</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js"></script>
<style>
  :root {
    --bg: #0f1620; --panel: #1a2231; --panel-2: #232d3f;
    --border: #2a3548; --text: #e6ecf5; --muted: #8aa0bd;
    --accent: #4f9eff; --green: #4ade80; --red: #f87171; --amber: #fbbf24;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; }
  aside { width: 280px; background: var(--panel); border-right: 1px solid var(--border);
    padding: 60px 0 14px; flex-shrink: 0; display: flex; flex-direction: column;
    overflow: hidden;
    transition: width .18s ease, padding .18s ease, border-color .18s ease; }
  aside h1, aside .nav-overview, aside .search { flex-shrink: 0; }
  .zone-list { flex: 1; overflow-y: auto; }
  .sidebar-group-header { padding: 12px 14px 4px; font-size: 10px;
    color: var(--muted); text-transform: uppercase; letter-spacing: 0.6px;
    font-weight: 600; cursor: default; pointer-events: none;
    border-top: 1px solid var(--border); margin-top: 4px; }
  .sidebar-group-header:first-child { border-top: none; margin-top: 0; }
  body.menu-collapsed aside { width: 0; padding: 0; border-right-color: transparent;
    overflow: hidden; }
  .menu-toggle, .home-btn { position: fixed; top: 12px; z-index: 50; width: 38px; height: 38px;
    border-radius: 6px; border: 1px solid var(--border); background: var(--panel-2);
    color: var(--text); cursor: pointer; display: flex; align-items: center; justify-content: center;
    padding: 0; transition: background .12s, border-color .12s; }
  .menu-toggle { left: 12px; }
  .home-btn { left: 60px; }
  .menu-toggle:hover, .home-btn:hover { background: var(--border); border-color: var(--accent); color: var(--accent); }
  .home-btn.active { background: var(--accent); border-color: var(--accent); color: #0f1620; }
  .menu-toggle svg, .home-btn svg { width: 18px; height: 18px; }
  body.menu-collapsed main { padding-left: 64px; }
  aside h1 { font-size: 14px; margin: 0 14px 10px; color: var(--muted); text-transform: uppercase;
    letter-spacing: 0.5px; }
  .zone-list { list-style: none; padding: 0; margin: 0; }
  .zone-list li { padding: 9px 14px; cursor: pointer; border-left: 3px solid transparent;
    font-size: 13px; transition: background .12s; }
  .zone-list li:hover { background: var(--panel-2); }
  .zone-list li.active { background: var(--panel-2); border-left-color: var(--accent); color: var(--accent); }
  .zone-list .zone-meta { display: block; font-size: 11px; color: var(--muted); margin-top: 2px; }
  main { flex: 1; overflow-y: auto; padding: 0 28px 24px; }
  h2 { margin: 24px 0 4px; font-size: 24px; }
  .subtitle { color: var(--muted); font-size: 13px; margin-bottom: 20px; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px;
    background: var(--bg); margin: 0; }
  .sticky-header { position: sticky; top: 0; z-index: 10; background: var(--bg);
    padding: 16px 0 14px; margin-bottom: 18px;
    border-bottom: 1px solid var(--border); }
  .sticky-header h2 { margin: 0 0 4px; }
  .sticky-header .subtitle { margin-bottom: 14px; }
  .kpi { background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 14px 16px; }
  .kpi-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .kpi-value { font-size: 22px; font-weight: 600; margin-top: 4px; }
  .kpi-sub { font-size: 12px; color: var(--muted); margin-top: 3px; }
  .kpi-value.green { color: var(--green); }
  .kpi-value.red { color: var(--red); }
  .kpi-value.amber { color: var(--amber); }
  .kpi-value.blue { color: var(--accent); }
  .charts { display: grid; grid-template-columns: 1fr; gap: 20px; margin-bottom: 24px; }
  .chart-card { background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px 18px 12px; }
  .chart-card { position: relative; }
  .chart-card h3 { margin: 0 0 12px; font-size: 14px; color: var(--muted); font-weight: 500;
    padding-right: 40px; }
  .chart-wrap { position: relative; height: 280px; }
  .zoom-hint { position: absolute; top: 8px; left: 8px; z-index: 5;
    background: rgba(15, 22, 32, 0.85); color: var(--muted); font-size: 11px;
    padding: 4px 8px; border-radius: 4px; pointer-events: none;
    opacity: 0; transition: opacity .15s; border: 1px solid var(--border); }
  .zoom-hint.visible { opacity: 1; }
  .zoom-hint.ready { color: var(--green); border-color: var(--green); }
  .chart-expand { position: absolute; top: 10px; right: 10px; width: 28px; height: 28px;
    background: var(--panel-2); border: 1px solid var(--border); color: var(--muted);
    border-radius: 5px; cursor: pointer; display: flex; align-items: center; justify-content: center;
    padding: 0; transition: background .12s, color .12s, border-color .12s; }
  .chart-expand:hover { color: var(--accent); border-color: var(--accent); background: var(--border); }
  .chart-expand svg { width: 14px; height: 14px; }
  .chart-fullscreen { position: fixed; inset: 0; background: rgba(15, 22, 32, 0.97);
    z-index: 100; display: flex; flex-direction: column; padding: 60px 24px 24px; }
  .chart-fullscreen[hidden] { display: none; }
  .toolbar[hidden], .peaks-toolbar[hidden], .back-btn[hidden] { display: none; }
  .chart-fullscreen-close { position: absolute; top: 16px; right: 20px; width: 40px; height: 40px;
    background: var(--panel-2); border: 1px solid var(--border); color: var(--text);
    border-radius: 6px; font-size: 18px; cursor: pointer; font-family: inherit; }
  .chart-fullscreen-close:hover { color: var(--red); border-color: var(--red); }
  .chart-fullscreen-hint { color: var(--muted); font-size: 12px; text-align: center;
    margin-bottom: 16px; }
  .chart-fullscreen-wrap { flex: 1; position: relative; }
  .chart-fullscreen-wrap canvas { max-height: 100%; }
  .tables { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .table-card { background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 14px 16px; }
  .table-card h3 { margin: 0 0 10px; font-size: 13px; color: var(--muted); font-weight: 500;
    text-transform: uppercase; letter-spacing: 0.5px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 500; font-size: 11px; text-transform: uppercase; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .delta-up { color: var(--green); }
  .delta-down { color: var(--red); }
  .delta-flat { color: var(--muted); }
  .search { padding: 8px 14px 10px; }
  .search input { width: 100%; padding: 7px 10px; background: var(--panel-2); border: 1px solid var(--border);
    color: var(--text); border-radius: 5px; font-size: 12px; }
  .search input:focus { outline: none; border-color: var(--accent); }
  @media (max-width: 1000px) { .tables { grid-template-columns: 1fr; } }

  /* Onglets categorie (Zones / Portes) */
  .cat-tabs { display: flex; padding: 0 14px 8px; gap: 6px; flex-shrink: 0; }
  .cat-tab { flex: 1; background: transparent; border: 1px solid var(--border);
    color: var(--muted); padding: 7px 10px; border-radius: 6px;
    font: inherit; font-size: 12px; cursor: pointer;
    text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;
    transition: background .12s, border-color .12s, color .12s; }
  .cat-tab:hover { color: var(--text); border-color: var(--accent); }
  .cat-tab.active { background: var(--accent); border-color: var(--accent);
    color: #0f1620; }

  /* Vue d'ensemble pics */
  .nav-overview { padding: 10px 14px 10px; }
  .nav-overview button { width: 100%; text-align: left; padding: 9px 12px;
    background: var(--panel-2); border: 1px solid var(--border); color: var(--text);
    border-radius: 6px; font: inherit; font-size: 13px; cursor: pointer;
    display: flex; align-items: center; gap: 8px; transition: background .12s, border-color .12s; }
  .nav-overview button:hover { background: var(--border); border-color: var(--accent); }
  .nav-overview button.active { background: var(--border); border-color: var(--accent); color: var(--accent); }
  .nav-overview button svg { width: 14px; height: 14px; flex-shrink: 0; }
  .peaks-toolbar { display: flex; gap: 12px; flex-wrap: wrap; align-items: center;
    margin-bottom: 18px; }
  .peaks-toolbar input, .peaks-toolbar select { background: var(--panel-2); border: 1px solid var(--border);
    color: var(--text); padding: 7px 10px; border-radius: 5px; font-size: 13px; font-family: inherit; }
  .peaks-toolbar input:focus, .peaks-toolbar select:focus { outline: none; border-color: var(--accent); }
  .peaks-toolbar input { flex: 1; min-width: 200px; max-width: 320px; }
  .peaks-toolbar label { font-size: 12px; color: var(--muted); }
  .back-btn { background: var(--panel-2); border: 1px solid var(--border); color: var(--text);
    cursor: pointer; padding: 6px 12px; border-radius: 5px; font-size: 12px; font-family: inherit;
    margin: 24px 0 12px; transition: background .12s, border-color .12s; }
  .back-btn:hover { background: var(--border); border-color: var(--accent); color: var(--accent); }
  .day-nav { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0 20px; }
  .day-nav-btn { background: var(--panel-2); border: 1px solid var(--border); color: var(--text);
    cursor: pointer; padding: 6px 12px; border-radius: 5px; font-size: 12px; font-family: inherit;
    transition: background .12s, border-color .12s, color .12s; }
  .day-nav-btn:hover { background: var(--border); border-color: var(--accent); color: var(--accent); }
  .day-nav-btn.active { background: var(--accent); border-color: var(--accent); color: #0f1620; font-weight: 600; }
  .peaks-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 14px; }
  .peak-card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 18px; display: flex; flex-direction: column; gap: 6px; position: relative;
    transition: border-color .12s, transform .08s; }
  .peak-card.clickable { cursor: pointer; }
  .peak-card.clickable:hover { border-color: var(--accent); transform: translateY(-1px); }
  /* Heatmap d'intensite (>=30%, >=50%, >=75% du pic periode) */
  .peak-card.intensity-hot  { border-left: 4px solid #ef4444; }
  .peak-card.intensity-warm { border-left: 4px solid #f97316; }
  .peak-card.intensity-mild { border-left: 4px solid #facc15; }
  .peak-card.intensity-cool { border-left: 4px solid transparent; }
  .heat-badge { display: inline-block; font-size: 9px;
    font-weight: 700; letter-spacing: 0.5px; padding: 2px 7px; border-radius: 4px;
    text-transform: uppercase; margin-bottom: 6px; }
  .heat-badge.hot  { background: #ef444433; color: #ef4444; }
  .heat-badge.warm { background: #f9731633; color: #f97316; }
  .heat-badge.mild { background: #facc1533; color: #facc15; }
  .heat-badge.uam  { background: #4f9eff33; color: #4f9eff; }
  .heat-badge.renfort { background: #c084fc33; color: #c084fc; }
  .pc-uam, .pc-renfort { margin-top: 6px; font-size: 11px; }
  .pc-uam { color: #4f9eff; }
  .pc-renfort { color: #c084fc; }
  .pc-uam strong, .pc-renfort strong { font-weight: 600; }
  .peak-card { position: relative; }
  .peak-card .rank { position: absolute; top: 10px; right: 12px; font-size: 11px;
    color: var(--muted); font-variant-numeric: tabular-nums; }
  .peak-card .pc-zone { font-size: 14px; font-weight: 600; color: var(--text);
    text-transform: uppercase; letter-spacing: 0.4px; }
  .peak-card .pc-value { font-size: 32px; font-weight: 600; color: var(--accent);
    font-variant-numeric: tabular-nums; line-height: 1; margin: 4px 0 2px; }
  .peak-card .pc-when { font-size: 12px; color: var(--muted); }
  .peak-card .pc-extras { display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
    margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); }
  .peak-card .pc-extras .pc-extra-label { font-size: 10px; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.4px; }
  .peak-card .pc-extras .pc-extra-val { font-size: 15px; font-weight: 600;
    font-variant-numeric: tabular-nums; margin-top: 2px; }
  .peak-card .pc-extras .pc-extra-val.green { color: var(--green); }
  .peak-card .pc-extras .pc-extra-val.red { color: var(--red); }
  .peak-card .pc-extras .pc-extra-when { font-size: 11px; color: var(--muted); margin-top: 1px; }
  .peak-card .pc-footer { display: flex; gap: 10px; margin-top: 10px; font-size: 11px;
    color: var(--muted); border-top: 1px solid var(--border); padding-top: 8px; }
  .peak-card .pc-footer span { display: flex; gap: 4px; }
  .peak-card .pc-footer strong { color: var(--text); font-variant-numeric: tabular-nums; }
  .empty { padding: 24px 0; color: var(--muted); }
  .days-section { margin-top: 28px; margin-bottom: 28px; }
  .days-section > h3 { margin: 0 0 12px; font-size: 13px; color: var(--muted);
    font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }
  .pc-staff { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border);
    font-size: 12px; color: var(--muted); line-height: 1.45; }
  .pc-staff strong { color: var(--amber); font-weight: 600;
    font-variant-numeric: tabular-nums; }
  .pc-staff .pc-extra-label { margin-bottom: 2px; }
  .pc-extra-val.amber { color: var(--amber); }

  /* Bloc d'analyse RETEX par zone */
  .analysis { background: var(--panel); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 20px 18px; margin-bottom: 22px;
    border-left: 3px solid var(--accent); }
  .analysis .a-overview { font-size: 14px; line-height: 1.55; margin: 0 0 6px; }
  .analysis h4 { font-size: 11px; color: var(--muted); text-transform: uppercase;
    letter-spacing: 0.6px; margin: 14px 0 6px; font-weight: 600; }
  .analysis ul { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.55; }
  .analysis ul li { margin-bottom: 3px; }
  .analysis .a-section.warn { color: var(--amber); }
  .analysis .a-section.warn h4 { color: var(--amber); }
  .analysis .a-section.alert h4 { color: var(--red); }
  .analysis .a-empty { color: var(--muted); font-size: 12px; font-style: italic; }
  .a-warning { background: rgba(251, 191, 36, 0.10); border-left: 3px solid var(--amber);
    padding: 10px 14px; border-radius: 6px; margin-top: 12px;
    font-size: 13px; line-height: 1.5; color: var(--amber); }

  /* Titres alignes a gauche comme les cartes ; les boutons fixed
     (hamburger + home en haut a gauche) flottent au-dessus. */
  main > h2 { margin-top: 16px; }
  .back-btn { margin: 16px 0 12px; }

  /* Dashboard accueil */
  .home-title { margin-top: 16px; }
  .home-section { margin: 28px 0; }
  .home-section > h3 { margin: 0 0 14px; font-size: 13px; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;
    border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  .home-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; }
  .home-stats-big { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
  .home-stat { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 18px 20px; }
  .home-stat-label { font-size: 11px; color: var(--muted); text-transform: uppercase;
    letter-spacing: 0.5px; }
  .home-stat-val { font-size: 32px; font-weight: 600; margin-top: 6px;
    font-variant-numeric: tabular-nums; line-height: 1.1; }
  .home-stat-val.blue { color: var(--accent); }
  .home-stat-val.green { color: var(--green); }
  .home-stat-val.red { color: var(--red); }
  .home-stat-val.amber { color: var(--amber); }
  .home-stat-sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .home-cats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
  .home-cat-card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px; }
  .home-cat-name { font-size: 13px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.4px; color: var(--accent); }
  .home-cat-count { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .home-cat-stats { margin-top: 10px; display: grid; grid-template-columns: 1fr 1fr 1fr;
    gap: 6px; font-size: 11px; color: var(--muted); }
  .home-cat-stats strong { color: var(--text); font-variant-numeric: tabular-nums;
    display: block; font-size: 14px; }
  .home-cat-stats .lab { display: block; font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.4px; }
  .home-cat-peak { margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--border);
    font-size: 11px; color: var(--muted); font-style: italic; }
  .home-top-list { display: flex; flex-direction: column; gap: 4px; }
  .home-top-row { display: grid;
    grid-template-columns: 36px 1.5fr 1fr 1.5fr;
    align-items: center; gap: 10px;
    padding: 9px 14px; background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; cursor: pointer; transition: background .12s, border-color .12s; }
  .home-top-row:hover { background: var(--panel-2); border-color: var(--accent); }
  .home-top-rank { color: var(--muted); font-variant-numeric: tabular-nums;
    font-size: 12px; font-weight: 600; }
  .home-top-name { font-weight: 500; font-size: 13px; }
  .home-top-stat { font-variant-numeric: tabular-nums; color: var(--accent); font-size: 13px;
    font-weight: 600; }
  .home-top-sub { font-size: 11px; color: var(--muted); text-align: right; }

  /* Responsive iPhone / petits ecrans (portrait) */
  @media (max-width: 768px) {
    main { padding: 0 14px 24px; }
    h2 { font-size: 20px; margin-top: 16px; }
    .subtitle { font-size: 12px; margin-bottom: 14px; }

    /* Sidebar en drawer overlay */
    aside {
      position: fixed; top: 0; left: 0; bottom: 0;
      width: 270px; z-index: 40;
      transform: translateX(0);
      transition: transform .22s ease;
      box-shadow: 4px 0 18px rgba(0, 0, 0, 0.5);
    }
    body.menu-collapsed aside {
      transform: translateX(-100%);
      box-shadow: none;
      width: 270px;
      padding: 60px 0 14px;
      border-right: 1px solid var(--border);
    }
    body.menu-collapsed main, main { padding-left: 14px; }

    /* Backdrop tap-to-close quand drawer ouvert */
    body:not(.menu-collapsed)::after {
      content: '';
      position: fixed; top: 0; right: 0; bottom: 0; left: 270px;
      background: rgba(0, 0, 0, 0.5);
      z-index: 35;
    }

    /* KPIs : 2 colonnes au lieu d'auto-fit */
    .kpis { grid-template-columns: repeat(2, 1fr); gap: 8px;
      padding: 10px 0 12px; margin-bottom: 14px; }
    .kpi { padding: 10px 12px; }
    .kpi-label { font-size: 10px; }
    .kpi-value { font-size: 16px; }
    .kpi-sub { font-size: 11px; }

    /* Charts plus compacts */
    .chart-wrap { height: 220px; }
    .chart-card { padding: 12px 12px 8px; }
    .chart-card h3 { font-size: 12px; margin-bottom: 8px; }
    .charts { gap: 14px; margin-bottom: 18px; }

    /* Grilles cartes : 1 colonne */
    .peaks-grid { grid-template-columns: 1fr; gap: 10px; }
    .peak-card { padding: 14px 16px; }
    .peak-card .pc-value { font-size: 28px; }
    .peak-card .pc-zone { font-size: 13px; }
    .pc-extras { grid-template-columns: 1fr 1fr; gap: 6px; }

    /* Tables empilees */
    .tables { grid-template-columns: 1fr; gap: 12px; }
    .table-card { padding: 12px 14px; }
    table { font-size: 11px; }
    th, td { padding: 5px 6px; }

    /* Section "Detail par jour" plus aeree */
    .days-section { margin-top: 22px; margin-bottom: 22px; }
    .days-section > h3 { font-size: 11px; }

    /* Analyse RETEX */
    .analysis { padding: 12px 14px 14px; }
    .analysis .a-overview { font-size: 13px; }
    .analysis ul { font-size: 12px; padding-left: 16px; }
    .analysis h4 { font-size: 10px; }

    /* Navigation jours */
    .day-nav { gap: 5px; margin: 8px 0 16px; }
    .day-nav-btn { padding: 5px 9px; font-size: 11px; }

    /* Toolbar pics overview */
    .peaks-toolbar input, .peaks-toolbar select { font-size: 12px; padding: 6px 8px; }

    /* Bouton retour */
    .back-btn { margin: 16px 0 10px; }

    /* Hamburger plus visible */
    .menu-toggle { width: 42px; height: 42px; }
    .menu-toggle svg { width: 20px; height: 20px; }

    /* Filtre sidebar */
    .search input { font-size: 14px; padding: 9px 12px; }
    .zone-list li { padding: 11px 14px; font-size: 14px; }
    .nav-overview button { padding: 11px 14px; font-size: 14px; }
  }

  /* Scrollbars dans le ton du theme */
  * { scrollbar-width: thin; scrollbar-color: #3a4a66 transparent; }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #2f3b52; border-radius: 8px;
    border: 2px solid var(--bg); }
  ::-webkit-scrollbar-thumb:hover { background: #46587a; }
  aside::-webkit-scrollbar-thumb { border-color: var(--panel); }
  aside::-webkit-scrollbar-corner, ::-webkit-scrollbar-corner { background: transparent; }
</style>
</head>
<body>
<button class="menu-toggle" id="menu-toggle" aria-label="Replier le menu" title="Replier / deplier le menu">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
       stroke-linecap="round" stroke-linejoin="round">
    <line x1="3" y1="6" x2="21" y2="6"></line>
    <line x1="3" y1="12" x2="21" y2="12"></line>
    <line x1="3" y1="18" x2="21" y2="18"></line>
  </svg>
</button>
<button class="home-btn" id="home-btn" aria-label="Accueil" title="Tableau de bord">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
       stroke-linecap="round" stroke-linejoin="round">
    <path d="M3 12l9-9 9 9"></path>
    <path d="M5 10v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V10"></path>
    <path d="M10 21V14h4v7"></path>
  </svg>
</button>
<div class="chart-fullscreen" id="chart-fullscreen" hidden>
  <button class="chart-fullscreen-close" id="chart-fullscreen-close" aria-label="Fermer">x</button>
  <div class="chart-fullscreen-hint">Pinch / Wheel pour zoomer - drag pour deplacer - double clic pour reset</div>
  <div class="chart-fullscreen-wrap"><canvas id="chart-fullscreen-canvas"></canvas></div>
</div>
<aside>
  <div class="cat-tabs">
    <button class="cat-tab active" data-cat="zone">Zones</button>
    <button class="cat-tab" data-cat="porte">Portes</button>
  </div>
  <div class="nav-overview">
    <button id="nav-peaks" title="Voir le pic de presents par unite">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round">
        <polyline points="3 17 9 11 13 15 21 7"></polyline>
        <polyline points="14 7 21 7 21 14"></polyline>
      </svg>
      Vue d'ensemble - Pics
    </button>
  </div>
  <div class="search"><input id="zone-search" placeholder="Filtrer..."></div>
  <ul class="zone-list" id="zone-list"></ul>
</aside>
<main id="main"></main>

<template id="tpl-zone">
  <div class="sticky-header">
    <h2 data-bind="zone"></h2>
    <div class="subtitle" data-bind="subtitle"></div>
    <div class="kpis" data-bind="kpis"></div>
  </div>
  <div class="analysis" data-bind="analysis"></div>
  <div data-bind="days-section"></div>
  <div class="charts" data-bind="day-charts"></div>
  <div class="tables">
    <div class="table-card">
      <h3>Progression jour a jour</h3>
      <table>
        <thead><tr><th>Jour</th><th class="num">Entrees</th><th class="num">Sorties</th><th class="num">Pic presents</th><th class="num">Var % (E)</th></tr></thead>
        <tbody data-bind="progression"></tbody>
      </table>
    </div>
    <div class="table-card">
      <h3>Top 5 plages de stress (flux entrees + sorties)</h3>
      <table>
        <thead><tr><th>Creneau</th><th class="num">Entrees</th><th class="num">Sorties</th><th class="num">Flux</th></tr></thead>
        <tbody data-bind="stress"></tbody>
      </table>
    </div>
  </div>
</template>

<script>
const DATA = __DATA__;
const FR = new Intl.NumberFormat('fr-FR');
const fmt = n => FR.format(n);
const WEEKDAYS_FR = ['dimanche','lundi','mardi','mercredi','jeudi','vendredi','samedi'];
const weekdayFr = (y, m, d) => WEEKDAYS_FR[new Date(Date.UTC(+y, +m - 1, +d)).getUTCDay()];
const fmtDateFr = iso => {
  if (!iso) return '-';
  const [y, m, d] = iso.split('-');
  return weekdayFr(y, m, d) + ' ' + d + '/' + m;
};
const fmtTsFr = ts => {
  if (!ts) return '-';
  // ts attendu: 'YYYY-MM-DD HH:MM'
  const [datePart, timePart] = ts.split(' ');
  if (!datePart) return ts;
  const [y, m, d] = datePart.split('-');
  return weekdayFr(y, m, d) + ' ' + d + '/' + m + (timePart ? ' ' + timePart : '');
};
const hourPart = ts => (ts && ts.includes(' ')) ? ts.split(' ')[1] : '';
const _windowHours = w => {
  const hStart = parseInt(hourPart(w.start).split(':')[0], 10);
  let hEnd = parseInt(hourPart(w.end).split(':')[0], 10);
  if (hEnd === 0 && hStart === 23) hEnd = 24;
  return [hStart, hEnd];
};
const fmtHourWindowFull = w => {
  if (!w || !w.start) return '-';
  const [dPart] = w.start.split(' ');
  const [y, m, d] = dPart.split('-');
  const [hStart, hEnd] = _windowHours(w);
  return weekdayFr(y, m, d) + ' ' + d + '/' + m + ' entre ' + hStart + 'h et ' + hEnd + 'h';
};
const fmtHourWindowTime = w => {
  if (!w || !w.start) return '-';
  const [hStart, hEnd] = _windowHours(w);
  return 'entre ' + hStart + 'h et ' + hEnd + 'h';
};
const colorForDay = (i, total) => {
  const h = Math.round(200 + (i / Math.max(total - 1, 1)) * 130);
  return 'hsl(' + h + ', 75%, 60%)';
};

let charts = [];
let activeZone = null;
let activeDay = null;
let viewMode = 'zone'; // 'zone' | 'peaks-overview' | 'peaks-detail' | 'zone-day'
let peaksDetailZone = null;
let peaksFilter = '';
let peaksSort = 'peak_desc';
let peaksDaySort = 'date_asc';
let currentCategory = 'zone'; // 'zone' | 'porte'

function currentList() {
  return (currentCategory === 'porte') ? (DATA.portes || []) : (DATA.zones || []);
}
function findUnit(name) {
  return currentList().find(x => x.name === name);
}
function unitLabel() {
  return currentCategory === 'porte' ? 'portes' : 'zones';
}

function intensityClass(ratio) {
  if (!isFinite(ratio) || ratio <= 0) return 'intensity-cool';
  if (ratio >= 0.75) return 'intensity-hot';
  if (ratio >= 0.50) return 'intensity-warm';
  if (ratio >= 0.30) return 'intensity-mild';
  return 'intensity-cool';
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const k in attrs) {
      if (k === 'class') node.className = attrs[k];
      else if (k === 'dataset') Object.assign(node.dataset, attrs[k]);
      else node.setAttribute(k, attrs[k]);
    }
  }
  for (const c of children) {
    if (c == null) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}

function kpiCard(label, value, sub, valueClass) {
  return el('div', { class: 'kpi' },
    el('div', { class: 'kpi-label' }, label),
    el('div', { class: 'kpi-value' + (valueClass ? ' ' + valueClass : '') }, value),
    el('div', { class: 'kpi-sub' }, sub || ''),
  );
}

// Capacite d'une porte = nb agents simultanes au pic x debit/agent.
// Le nb de PDA/tripodes n'est PAS la capacite (PDA souvent en recharge,
// renforts mobiles UAM, etc.).
const PORTE_CAP_PER_AGENT = 600; // scans/h par agent au pic, base terrain

// Portes equipees de tripodes (detecte via geojson place_config.tripodes) :
// la capacite est determinee par les tripodes, pas par le nb d'agents en simu.
// 1 agent peut gerer jusqu'a 3 tripodes.
const TRIPODES_PER_AGENT = 3;
const isTripodesPorte = z => !!(z && z.tripodes_mode);

function porteAgentsSimuAtPeak(z) {
  const st = z.staffing || {};
  const a = (st.accueil && st.accueil.peak_simu) || 0;
  const s = (st.securite && st.securite.count_op) || 0;
  return { accueil: a, securite: s, total: a + s };
}

function porteHourlyAgents(z, date, hour) {
  // hour = '00' a '23' (string)
  const st = z.staffing || {};
  const a = (((st.accueil || {}).hourly || {})[date] || {})[hour] || 0;
  const s = (st.securite && st.securite.count_op) || 0; // statique, on suppose presents
  return { accueil: a, securite: s, total: a + s };
}

function porteCapacityPeak(z) {
  // Capacite theorique max + nb agents requis. Differe pour les portes a tripodes.
  if (isTripodesPorte(z)) {
    const nbTri = z.tripode_count || 0;
    const capacity = nbTri * PORTE_CAP_PER_AGENT;
    const agentsMin = Math.ceil(nbTri / TRIPODES_PER_AGENT);
    return { mode: 'tripodes', nbTripodes: nbTri, capacity,
             agentsMin, agentsTotal: agentsMin, agentsAccueil: agentsMin,
             agentsSecurite: 0 };
  }
  const agents = porteAgentsSimuAtPeak(z);
  return { mode: 'agents', capacity: agents.total * PORTE_CAP_PER_AGENT,
           agentsTotal: agents.total,
           agentsAccueil: agents.accueil, agentsSecurite: agents.securite };
}

function porteCapacityAtHour(z, date, hour) {
  if (isTripodesPorte(z)) {
    const nbTri = z.tripode_count || 0;
    const ha = porteHourlyAgents(z, date, hour);
    const capacity = nbTri * PORTE_CAP_PER_AGENT;
    const agentsMin = Math.ceil(nbTri / TRIPODES_PER_AGENT);
    return { mode: 'tripodes', nbTripodes: nbTri, capacity, agentsMin,
             agentsAccueil: ha.accueil, agentsSecurite: ha.securite,
             agentsTotal: ha.total,
             sousStaffed: ha.total > 0 && ha.total < agentsMin };
  }
  const ha = porteHourlyAgents(z, date, hour);
  return { mode: 'agents', capacity: ha.total * PORTE_CAP_PER_AGENT,
           agentsTotal: ha.total,
           agentsAccueil: ha.accueil, agentsSecurite: ha.securite };
}

function appendStaffingKpis(host, staffing) {
  if (!staffing) return;
  const a = staffing.accueil || {};
  const s = staffing.securite || {};
  // KPIs cote bleu pour les distinguer du staffing recommande (ambre)
  host.appendChild(kpiCard(
    'Personnel Accueil planifie',
    fmt(a.count_op || 0) + ' postes',
    'pic simu ' + fmt(a.peak_simu || 0) + ' simultanes' +
      (a.peak_simu_ts ? ' (' + fmtTsFr(a.peak_simu_ts) + ')' : ''),
    'green',
  ));
  host.appendChild(kpiCard(
    'Personnel Securite planifie',
    fmt(s.count_op || 0) + ' postes',
    (s.count_op || 0) > 0 ? 'compte statique (numeros sec. refaits)' : 'aucun renseigne',
    'red',
  ));
  if (a.agents_h_total) {
    host.appendChild(kpiCard(
      'Agents.h Accueil periode',
      fmt(Math.round(a.agents_h_total)) + ' h',
      'somme heures-personnes scan (chefs exclus)',
      'green',
    ));
  }
}

function reformatDatesInText(text) {
  return text
    .replace(/(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})/g,
      (_, y, m, d, t) => weekdayFr(y, m, d) + ' ' + d + '/' + m + ' a ' + t)
    .replace(/(\d{4})-(\d{2})-(\d{2})/g,
      (_, y, m, d) => weekdayFr(y, m, d) + ' ' + d + '/' + m);
}

function renderAnalysis(a, host, z) {
  if (!a) {
    host.replaceChildren(el('div', { class: 'a-empty' }, 'Pas assez de donnees pour generer une analyse.'));
    return;
  }
  host.replaceChildren();
  host.appendChild(el('p', { class: 'a-overview' }, reformatDatesInText(a.overview)));

  if (a.warnings && a.warnings.length) {
    a.warnings.forEach(w => {
      host.appendChild(el('div', { class: 'a-warning' }, w));
    });
  }

  if (z && z.category !== 'porte') {
    const ps = periodStaffing(z);
    const capInfo = zoneCapacity(z);
    const sec = el('div', { class: 'a-section' });
    sec.appendChild(el('h4', null, "Recommandation d'effectif"));
    const ul = el('ul');
    ul.appendChild(el('li', null,
      "Effectif soutenu au pic : " + ps.maxSustained + ' agents simultanes (capacite ' +
      capInfo.cap + ' ' + capInfo.unit + '/h, 1 agent par flux au-dela de ' +
      Math.round(STAFF_LOW_VOLUME_RATIO * 100) + ' % de la capacite, alternance autorisee en dessous).'));
    if (ps.maxPeak > ps.maxSustained) {
      ul.appendChild(el('li', null,
        "Pic instantane (1 h isolee) observe a " + ps.maxPeak +
        ' agents : absorbe par la tolerance de 15 min d\'attente, pas d\'embauche supplementaire.'));
    }
    ul.appendChild(el('li', null,
      'Total des services 8 h sur la periode : ' + ps.totalServices +
      ' (matin ' + ps.shiftTotals.matin +
      ' \u00b7 soir ' + ps.shiftTotals.soir +
      ' \u00b7 nuit ' + ps.shiftTotals.nuit + ').'));
    ul.appendChild(el('li', null, STAFFING_NOTE + '.'));
    sec.appendChild(ul);
    host.appendChild(sec);
  }

  if (z && z.category === 'porte') {
    const cap = porteCapacityPeak(z);
    const theoCap = cap.capacity;
    const peakFlow = z.kpis.peak_hour_flow.val || 0;
    const utilization = theoCap > 0 ? Math.round((peakFlow / theoCap) * 100) : null;
    const sec = el('div', { class: 'a-section' });
    sec.appendChild(el('h4', null, 'Dispositif sur la porte'));
    const ul = el('ul');
    if (cap.mode === 'tripodes') {
      ul.appendChild(el('li', null,
        'Porte equipee de ' + cap.nbTripodes + ' tripodes. La capacite est determinee ' +
        'par les tripodes (pas par le nombre de PDA).'));
      ul.appendChild(el('li', null,
        'Personnel minimum pour gerer : ' + cap.agentsMin + ' agent' +
        (cap.agentsMin > 1 ? 's' : '') + ' (1 agent peut gerer jusqu\'a 3 tripodes).'));
      ul.appendChild(el('li', null,
        'Capacite theorique : ~' + fmt(theoCap) + ' scans/h (' + cap.nbTripodes +
        ' tripodes \u00d7 ' + PORTE_CAP_PER_AGENT + ' scans/h base terrain).'));
      if (utilization !== null) {
        ul.appendChild(el('li', null,
          'Utilisation au pic horaire : ' + utilization + ' % de la capacite (' +
          fmt(peakFlow) + ' scans cumules entrees+sorties).'));
      }
    } else if (cap.agentsTotal > 0) {
      ul.appendChild(el('li', null,
        'Personnel au pic simultane : ' + cap.agentsTotal + ' agent' +
        (cap.agentsTotal > 1 ? 's' : '') +
        ' (' + cap.agentsAccueil + ' Accueil + ' + cap.agentsSecurite + ' Securite).'));
      ul.appendChild(el('li', null,
        'Capacite theorique : ~' + fmt(theoCap) + ' scans/h (' + cap.agentsTotal +
        ' agents \u00d7 ' + PORTE_CAP_PER_AGENT + ' scans/h/agent base terrain).'));
      if (utilization !== null) {
        ul.appendChild(el('li', null,
          'Utilisation au pic horaire : ' + utilization + ' % de la capacite (' +
          fmt(peakFlow) + ' scans cumules entrees+sorties).'));
      }
    } else {
      ul.appendChild(el('li', null,
        'Pic de personnel simultane non renseigne pour cette porte. Pas de capacite theorique calculable.'));
    }
    ul.appendChild(el('li', null,
      'Devices physiquement deployes : ' + z.device_count + ' (' +
      (z.pda_count || 0) + ' PDA + ' + (z.tripode_count || 0) + ' tripode' +
      (((z.tripode_count || 0) > 1) ? 's' : '') +
      '). Ce nombre inclut les unites en recharge, les renforts mobiles UAM, ' +
      'etc. et ne represente pas le nombre d\'agents en poste simultane.'));
    sec.appendChild(ul);
    host.appendChild(sec);
  }

  if (z && z.staffing) {
    const st = z.staffing;
    const sec = el('div', { class: 'a-section' });
    sec.appendChild(el('h4', null, 'Personnel planifie (Accueil + Securite)'));
    const ul = el('ul');
    const a = st.accueil || {};
    const s = st.securite || {};
    if (a.count_op || a.count_chef) {
      ul.appendChild(el('li', null,
        'Accueil : ' + (a.count_op || 0) + ' poste' + ((a.count_op || 0) > 1 ? 's' : '') +
        ' operationnel(s) + ' + (a.count_chef || 0) + ' chef(s) - ' +
        Math.round(a.agents_h_total || 0) + ' agents.h cumules, pic simu ' +
        (a.peak_simu || 0) + ' agents' +
        (a.peak_simu_ts ? ' le ' + reformatDatesInText(a.peak_simu_ts) : '')));
    }
    if (s.count_op || s.count_chef) {
      const aff = (s.affectations || []).slice(0, 3).join(', ');
      ul.appendChild(el('li', null,
        'Securite : ' + (s.count_op || 0) + ' poste' + ((s.count_op || 0) > 1 ? 's' : '') +
        ' (compte statique - numeros sec. refaits cote calendrier 2025)' +
        (aff ? ' - ex : ' + aff : '')));
    }
    if (!(a.count_op || s.count_op)) {
      ul.appendChild(el('li', { class: 'a-empty' },
        'Aucun poste valide pour cette unite (uniquement non-scan / surveillance).'));
    }
    sec.appendChild(ul);
    host.appendChild(sec);
  }

  if (a.highlights && a.highlights.length) {
    const sec = el('div', { class: 'a-section' });
    sec.appendChild(el('h4', null, 'Faits marquants'));
    const ul = el('ul');
    a.highlights.forEach(t => ul.appendChild(el('li', null, reformatDatesInText(t))));
    sec.appendChild(ul);
    host.appendChild(sec);
  }

  if (a.stress_windows && a.stress_windows.length) {
    const sec = el('div', { class: 'a-section warn' });
    sec.appendChild(el('h4', null, 'Moments de tension'));
    const ul = el('ul');
    a.stress_windows.forEach(w => {
      const startTxt = reformatDatesInText(w.start);
      ul.appendChild(el('li', null,
        startTxt + ' -> ' + w.end_label +
        ' (' + w.duration_min + ' min, ' + fmt(w.total_flow) + ' mouvements cumules)'));
    });
    sec.appendChild(ul);
    host.appendChild(sec);
  }

  if (a.interruptions && a.interruptions.length) {
    const sec = el('div', { class: 'a-section alert' });
    sec.appendChild(el('h4', null, 'Interruptions de scan detectees'));
    const ul = el('ul');
    a.interruptions.forEach(it => {
      const dTxt = reformatDatesInText(it.date);
      const hours = Math.floor(it.duration_min / 60);
      const mins = it.duration_min % 60;
      const dur = (hours ? hours + ' h ' : '') + (mins ? mins + ' min' : '').trim();
      ul.appendChild(el('li', null,
        dTxt + ' de ' + it.start + ' a ' + it.end + ' - ' + (dur || it.duration_min + ' min') +
        ' sans aucun scan (' + it.gap_slots + ' creneaux manquants)'));
    });
    sec.appendChild(ul);
    host.appendChild(sec);
  }
}

const isMobile = () => window.matchMedia('(max-width: 768px)').matches;
const closeMenuOnMobile = () => { if (isMobile()) document.body.classList.add('menu-collapsed'); };

const ZONE_GROUPS = [
  { id: 'aire_accueil', label: "Aires d'accueil",
    match: n => n.startsWith('AA ') },
  { id: 'hospitalite', label: 'Hospitalite',
    match: n => ['MAISON DES HUNAUDIERES', 'WELCOME', 'VISITES MUSEE'].indexOf(n) >= 0 },
  { id: 'paddock', label: 'Paddocks',
    match: n => n === 'PADDOCKS' },
  { id: 'parking', label: 'Parkings',
    match: n => n.startsWith('P ') },
  { id: 'tribune', label: 'Tribunes',
    match: n => n.startsWith('TRIBUNE') },
];

function groupZones(list) {
  const grouped = ZONE_GROUPS.map(g => ({ ...g, items: [] }));
  const other = [];
  list.forEach(z => {
    const g = grouped.find(g => g.match(z.name));
    if (g) g.items.push(z);
    else other.push(z);
  });
  if (other.length) grouped.push({ id: 'other', label: 'Autres', items: other });
  return grouped.filter(g => g.items.length > 0);
}

function renderSidebar(filter) {
  const ul = document.getElementById('zone-list');
  const f = (filter || '').toLowerCase();
  ul.replaceChildren();
  const filteredList = currentList().filter(z => z.name.toLowerCase().includes(f));
  if (currentCategory === 'porte') {
    // Pas de sous-groupes pour les portes (elles sont toutes "Enceinte Generale")
    filteredList.forEach(z => ul.appendChild(buildSidebarItem(z)));
    return;
  }
  groupZones(filteredList).forEach(g => {
    const header = el('li', { class: 'sidebar-group-header' }, g.label + ' (' + g.items.length + ')');
    ul.appendChild(header);
    g.items.forEach(z => ul.appendChild(buildSidebarItem(z)));
  });
}

function buildSidebarItem(z) {
  const li = el('li', { dataset: { zone: z.name } },
    el('strong', null, z.name),
    el('span', { class: 'zone-meta' }, fmt(z.total_entree) + ' E - ' + fmt(z.total_sortie) + ' S'),
  );
  if (z.name === activeZone) li.classList.add('active');
  li.addEventListener('click', () => { selectZone(z.name); closeMenuOnMobile(); });
  return li;
}

function selectZone(zoneName) {
  viewMode = 'zone';
  activeZone = zoneName;
  document.getElementById('nav-peaks').classList.remove('active');
  document.getElementById('home-btn').classList.remove('active');
  document.querySelectorAll('.zone-list li').forEach(li => {
    li.classList.toggle('active', li.dataset.zone === zoneName);
  });
  const z = findUnit(zoneName);
  if (z) render(z);
}

function showHome() {
  viewMode = 'home';
  activeZone = null;
  activeDay = null;
  document.getElementById('home-btn').classList.add('active');
  document.getElementById('nav-peaks').classList.remove('active');
  document.querySelectorAll('.zone-list li').forEach(li => li.classList.remove('active'));
  charts.forEach(c => c.destroy());
  charts = [];
  renderHome();
}

function aggregatePortesEnceinte() {
  // Aggrege les portes (zone parente = ENCEINTE GENERALE) pour la freq globale
  const portes = (DATA.portes || []).filter(p => (p.zone_parent || p.zone) === 'ENCEINTE GENERALE');
  if (!portes.length) return null;
  // Somme par creneau 15 min (date + hm)
  const byTs = new Map(); // 'YYYY-MM-DD HH:MM' -> {e, s}
  portes.forEach(p => {
    Object.entries(p.by_day).forEach(([d, slots]) => {
      slots.forEach(it => {
        const key = d + ' ' + it.hm;
        const v = byTs.get(key) || { e: 0, s: 0 };
        v.e += it.e; v.s += it.s;
        byTs.set(key, v);
      });
    });
  });
  const sorted = [...byTs.keys()].sort();
  let cumul = 0;
  let peak = { val: 0, ts: null };
  let totalE = 0, totalS = 0;
  let dailyPeakP = new Map(); // 'YYYY-MM-DD' -> {peak, hm}
  let hourlyFlow = new Map(); // 'YYYY-MM-DD HH:00' -> {e, s, flow}
  sorted.forEach(k => {
    const v = byTs.get(k);
    totalE += v.e; totalS += v.s;
    cumul += v.e - v.s;
    if (cumul > peak.val) peak = { val: cumul, ts: k };
    const d = k.slice(0, 10);
    const hm = k.slice(11);
    const dp = dailyPeakP.get(d) || { peak: 0, hm: null };
    if (cumul > dp.peak) { dp.peak = cumul; dp.hm = hm; dailyPeakP.set(d, dp); }
    else dailyPeakP.set(d, dp);
    const hourKey = d + ' ' + hm.slice(0, 2) + ':00';
    const hf = hourlyFlow.get(hourKey) || { e: 0, s: 0, flow: 0 };
    hf.e += v.e; hf.s += v.s; hf.flow = hf.e + hf.s;
    hourlyFlow.set(hourKey, hf);
  });
  let peakHourFlow = { val: 0, ts: null };
  hourlyFlow.forEach((v, k) => { if (v.flow > peakHourFlow.val) peakHourFlow = { val: v.flow, ts: k }; });
  return {
    total_entree: totalE, total_sortie: totalS,
    peak_presents: peak, peak_hour_flow: peakHourFlow,
    dailyPeakP, portes_count: portes.length,
    devices: portes.reduce((s, p) => s + (p.device_count || 0), 0),
    pdas: portes.reduce((s, p) => s + (p.pda_count || 0), 0),
    tripodes: portes.reduce((s, p) => s + (p.tripode_count || 0), 0),
  };
}

function homeSummaryCard(label, value, sub, cls) {
  return el('div', { class: 'home-stat' },
    el('div', { class: 'home-stat-label' }, label),
    el('div', { class: 'home-stat-val ' + (cls || '') }, value),
    el('div', { class: 'home-stat-sub' }, sub || ''),
  );
}

function renderHome() {
  const main = document.getElementById('main');
  main.replaceChildren();
  const eventPretty = DATA.event.replace(/_/g, ' ').toUpperCase();

  main.appendChild(el('h2', { class: 'home-title' },
    'Tableau de bord - ' + eventPretty + ' ' + DATA.year));
  const allDays = [...new Set([
    ...(DATA.zones || []).flatMap(z => z.days),
    ...(DATA.portes || []).flatMap(z => z.days),
  ])].sort();
  const periodLbl = allDays.length
    ? 'Du ' + fmtDateFr(allDays[0]) + ' au ' + fmtDateFr(allDays[allDays.length - 1]) +
      ' \u00b7 ' + allDays.length + ' jours d\'activite'
    : '-';
  main.appendChild(el('div', { class: 'subtitle' }, periodLbl));

  // ============== Section 1 : Fréquentation enceinte ==============
  const agg = aggregatePortesEnceinte();
  const sec1 = el('div', { class: 'home-section' });
  sec1.appendChild(el('h3', null, 'Frequentation generale - Enceinte generale'));
  const stats1 = el('div', { class: 'home-stats home-stats-big' });
  if (agg) {
    stats1.append(
      homeSummaryCard('Pic de presents enceinte',
        fmt(agg.peak_presents.val),
        agg.peak_presents.ts ? fmtTsFr(agg.peak_presents.ts.replace(' ', ' ')) : '-',
        'blue'),
      homeSummaryCard('Total entrees',
        fmt(agg.total_entree),
        agg.portes_count + ' portes \u00b7 ' + agg.devices + ' devices', 'green'),
      homeSummaryCard('Total sorties',
        fmt(agg.total_sortie),
        'solde final ' + fmt(agg.total_entree - agg.total_sortie), 'red'),
      homeSummaryCard('Pic flux total /heure',
        fmt(agg.peak_hour_flow.val),
        agg.peak_hour_flow.ts ? fmtTsFr(agg.peak_hour_flow.ts) : '-', 'amber'),
    );
  } else {
    stats1.appendChild(el('div', { class: 'empty' }, 'Aucune donnee porte importee.'));
  }
  sec1.appendChild(stats1);
  main.appendChild(sec1);

  // ============== Section 2 : Activite par categorie ==============
  const sec2 = el('div', { class: 'home-section' });
  sec2.appendChild(el('h3', null, 'Vue par categorie'));
  const catBox = el('div', { class: 'home-cats' });
  const cats = [
    { id: 'tribune', label: 'Tribunes', match: n => n.startsWith('TRIBUNE'), src: DATA.zones || [] },
    { id: 'aire_accueil', label: "Aires d'accueil", match: n => n.startsWith('AA '), src: DATA.zones || [] },
    { id: 'parking', label: 'Parkings', match: n => n.startsWith('P '), src: DATA.zones || [] },
    { id: 'paddock', label: 'Paddocks', match: n => n === 'PADDOCKS', src: DATA.zones || [] },
    { id: 'hospitalite', label: 'Hospitalite',
      match: n => ['MAISON DES HUNAUDIERES','WELCOME','VISITES MUSEE'].indexOf(n) >= 0,
      src: DATA.zones || [] },
    { id: 'porte', label: 'Portes enceinte', match: () => true, src: DATA.portes || [] },
  ];
  cats.forEach(c => {
    const items = c.src.filter(z => c.match(z.name));
    if (!items.length) return;
    const totalE = items.reduce((s, z) => s + z.total_entree, 0);
    const totalS = items.reduce((s, z) => s + z.total_sortie, 0);
    const maxP = items.reduce((m, z) =>
      z.kpis.peak_presents.val > m.val ? { val: z.kpis.peak_presents.val, ts: z.kpis.peak_presents.ts, name: z.name } : m,
      { val: 0, ts: null, name: '-' });
    const card = el('div', { class: 'home-cat-card' });
    card.append(
      el('div', { class: 'home-cat-name' }, c.label),
      el('div', { class: 'home-cat-count' }, items.length + ' ' + (items.length > 1 ? 'unites' : 'unite')),
      el('div', { class: 'home-cat-stats' },
        el('div', null, el('span', { class: 'lab' }, 'Entrees '),
          el('strong', null, fmt(totalE))),
        el('div', null, el('span', { class: 'lab' }, 'Sorties '),
          el('strong', null, fmt(totalS))),
        el('div', null, el('span', { class: 'lab' }, 'Pic presents '),
          el('strong', null, fmt(maxP.val))),
      ),
      el('div', { class: 'home-cat-peak' },
        '"' + maxP.name + '" - ' + (maxP.ts ? fmtTsFr(maxP.ts) : '-')),
    );
    catBox.appendChild(card);
  });
  sec2.appendChild(catBox);
  main.appendChild(sec2);

  // ============== Section 3 : Top portes ==============
  const sec3 = el('div', { class: 'home-section' });
  sec3.appendChild(el('h3', null, 'Top 8 portes par scans cumules (entrees + sorties)'));
  const portesTop = (DATA.portes || [])
    .map(p => ({ p, scans: p.total_entree + p.total_sortie }))
    .sort((a, b) => b.scans - a.scans)
    .slice(0, 8);
  const list3 = el('div', { class: 'home-top-list' });
  portesTop.forEach((row, i) => {
    const r = el('div', { class: 'home-top-row' });
    r.addEventListener('click', () => { currentCategory = 'porte';
      document.querySelectorAll('.cat-tab').forEach(b => b.classList.toggle('active', b.dataset.cat === 'porte'));
      renderSidebar(''); selectZone(row.p.name); });
    r.append(
      el('span', { class: 'home-top-rank' }, '#' + (i + 1)),
      el('span', { class: 'home-top-name' }, row.p.name),
      el('span', { class: 'home-top-stat' }, fmt(row.scans) + ' scans'),
      el('span', { class: 'home-top-sub' },
        row.p.device_count + ' devices \u00b7 pic ' + fmt(row.p.kpis.peak_hour_flow.val) + '/h'),
    );
    list3.appendChild(r);
  });
  sec3.appendChild(list3);
  main.appendChild(sec3);

  // ============== Section 4 : Top zones par pic présents ==============
  const sec4 = el('div', { class: 'home-section' });
  sec4.appendChild(el('h3', null, 'Top 8 zones par pic de presents'));
  const zonesTop = (DATA.zones || [])
    .slice()
    .sort((a, b) => b.kpis.peak_presents.val - a.kpis.peak_presents.val)
    .slice(0, 8);
  const list4 = el('div', { class: 'home-top-list' });
  zonesTop.forEach((z, i) => {
    const r = el('div', { class: 'home-top-row' });
    r.addEventListener('click', () => { currentCategory = 'zone';
      document.querySelectorAll('.cat-tab').forEach(b => b.classList.toggle('active', b.dataset.cat === 'zone'));
      renderSidebar(''); selectZone(z.name); });
    r.append(
      el('span', { class: 'home-top-rank' }, '#' + (i + 1)),
      el('span', { class: 'home-top-name' }, z.name),
      el('span', { class: 'home-top-stat' }, fmt(z.kpis.peak_presents.val) + ' presents'),
      el('span', { class: 'home-top-sub' },
        z.kpis.peak_presents.ts ? fmtTsFr(z.kpis.peak_presents.ts) : '-'),
    );
    list4.appendChild(r);
  });
  sec4.appendChild(list4);
  main.appendChild(sec4);

  main.scrollTop = 0;
}

function showPeaksOverview() {
  viewMode = 'peaks-overview';
  activeZone = null;
  peaksDetailZone = null;
  document.getElementById('nav-peaks').classList.add('active');
  document.getElementById('home-btn').classList.remove('active');
  document.querySelectorAll('.zone-list li').forEach(li => li.classList.remove('active'));
  charts.forEach(c => c.destroy());
  charts = [];
  renderPeaksOverview();
}

function showPeaksDetail(zoneName) {
  viewMode = 'peaks-detail';
  peaksDetailZone = zoneName;
  document.getElementById('nav-peaks').classList.add('active');
  document.getElementById('home-btn').classList.remove('active');
  charts.forEach(c => c.destroy());
  charts = [];
  renderPeaksDetail();
}

function showZoneDay(zoneName, date) {
  const z = findUnit(zoneName);
  if (!z) return;
  viewMode = 'zone-day';
  activeZone = zoneName;
  activeDay = date;
  document.getElementById('nav-peaks').classList.remove('active');
  document.getElementById('home-btn').classList.remove('active');
  document.querySelectorAll('.zone-list li').forEach(li => {
    li.classList.toggle('active', li.dataset.zone === zoneName);
  });
  charts.forEach(c => c.destroy());
  charts = [];
  renderZoneDay(z, date);
}

function render(z) {
  const k = z.kpis;
  const tpl = document.getElementById('tpl-zone').content.cloneNode(true);
  const titleEl = tpl.querySelector('[data-bind=zone]');
  titleEl.textContent = z.name;
  if (z.category === 'porte') {
    titleEl.appendChild(el('span', { class: 'subtitle',
      style: 'display:block; margin-top:2px; font-size:12px; font-weight:400;' },
      z.zone_parent || z.zone));
  }
  const eventPretty = DATA.event.replace(/_/g, ' ').toUpperCase();
  tpl.querySelector('[data-bind=subtitle]').textContent =
    eventPretty + ' ' + DATA.year + ' - ' + z.days.length + ' jours - donnees a 15 min' +
    (z.category === 'porte' ? ' - ' + z.device_count + ' device' + (z.device_count > 1 ? 's' : '') : '');

  renderAnalysis(z.analysis, tpl.querySelector('[data-bind=analysis]'), z);

  const kpiHost = tpl.querySelector('[data-bind=kpis]');
  const isPorte = z.category === 'porte';
  if (isPorte) {
    kpiHost.append(
      kpiCard('Total entrees', fmt(z.total_entree), 'sur ' + z.days.length + ' jours', 'green'),
      kpiCard('Total sorties', fmt(z.total_sortie),
        'total passages : ' + fmt(z.total_entree + z.total_sortie), 'red'),
      kpiCard('Pic entrees / 15 min', fmt(k.peak_entree.val), fmtTsFr(k.peak_entree.ts), 'green'),
      kpiCard('Pic entrees / heure', fmt(k.peak_hour_e.val), fmtHourWindowFull(k.peak_hour_e), 'green'),
      kpiCard('Pic sorties / 15 min', fmt(k.peak_sortie.val), fmtTsFr(k.peak_sortie.ts), 'red'),
      kpiCard('Pic sorties / heure', fmt(k.peak_hour_s.val), fmtHourWindowFull(k.peak_hour_s), 'red'),
      kpiCard('Pic flux total / 15 min', fmt(k.peak_flow.val), fmtTsFr(k.peak_flow.ts), 'amber'),
      kpiCard('Pic flux total / heure', fmt(k.peak_hour_flow.val), fmtHourWindowFull(k.peak_hour_flow), 'amber'),
      kpiCard('Jour le plus charge (E)', fmtDateFr(k.busiest_day_e.date), fmt(k.busiest_day_e.e) + ' entrees'),
      kpiCard('Creneau type le plus dense', k.busiest_hour.hm || '-',
        fmt(k.busiest_hour.e) + ' E - ' + fmt(k.busiest_hour.s) + ' S (cumule periode)'),
    );
  } else {
    kpiHost.append(
      kpiCard('Total entrees', fmt(z.total_entree), 'sur ' + z.days.length + ' jours', 'green'),
      kpiCard('Total sorties', fmt(z.total_sortie), 'solde final : ' + fmt(z.total_entree - z.total_sortie), 'red'),
      kpiCard('Pic de presents', fmt(k.peak_presents.val), fmtTsFr(k.peak_presents.ts), 'blue'),
      kpiCard('Pic entrees / 15 min', fmt(k.peak_entree.val), fmtTsFr(k.peak_entree.ts), 'green'),
      kpiCard('Pic entrees / heure', fmt(k.peak_hour_e.val), fmtHourWindowFull(k.peak_hour_e), 'green'),
      kpiCard('Pic sorties / 15 min', fmt(k.peak_sortie.val), fmtTsFr(k.peak_sortie.ts), 'red'),
      kpiCard('Pic sorties / heure', fmt(k.peak_hour_s.val), fmtHourWindowFull(k.peak_hour_s), 'red'),
      kpiCard('Pic flux total / 15 min', fmt(k.peak_flow.val), fmtTsFr(k.peak_flow.ts), 'amber'),
      kpiCard('Pic flux total / heure', fmt(k.peak_hour_flow.val), fmtHourWindowFull(k.peak_hour_flow), 'amber'),
      kpiCard('Jour pic de presents',
        fmtDateFr(k.busiest_day_p.date),
        fmt(k.busiest_day_p.peak_p) + ' presents' + (k.busiest_day_p.peak_p_hm ? ' a ' + k.busiest_day_p.peak_p_hm : ''),
        'blue'),
      kpiCard('Jour le plus charge (E)', fmtDateFr(k.busiest_day_e.date), fmt(k.busiest_day_e.e) + ' entrees'),
      kpiCard('Creneau type le plus dense', k.busiest_hour.hm || '-',
        fmt(k.busiest_hour.e) + ' E - ' + fmt(k.busiest_hour.s) + ' S (cumule periode)'),
    );
  }

  if (z.category === 'porte') {
    const cap = porteCapacityPeak(z);
    const theoCap = cap.capacity;
    const utilization = theoCap > 0 ? Math.round((k.peak_hour_flow.val / theoCap) * 100) : null;
    let personnelLabel, personnelSub, capSub;
    if (cap.mode === 'tripodes') {
      personnelLabel = fmt(cap.nbTripodes) + ' tripodes';
      personnelSub = '>= ' + cap.agentsMin + ' agents requis (1 agent / 3 tripodes)';
      capSub = cap.nbTripodes + ' tripodes \u00d7 ' + PORTE_CAP_PER_AGENT + ' scans/h';
    } else {
      personnelLabel = fmt(cap.agentsTotal) + ' agents';
      personnelSub = cap.agentsAccueil + ' Accueil + ' + cap.agentsSecurite + ' Securite';
      capSub = theoCap > 0
        ? cap.agentsTotal + ' agents \u00d7 ' + PORTE_CAP_PER_AGENT + ' scans/h'
        : 'staffing non renseigne pour cette porte';
    }
    kpiHost.append(
      kpiCard('Personnel / dispositif au pic', personnelLabel, personnelSub, 'amber'),
      kpiCard('Capacite theorique /h',
        theoCap > 0 ? fmt(theoCap) + ' scans' : 'n/a', capSub, 'amber'),
      kpiCard('Utilisation au pic',
        utilization !== null ? utilization + ' %' : 'n/a',
        fmt(k.peak_hour_flow.val) + ' scans cumules (' + fmtHourWindowFull(k.peak_hour_flow) + ')',
        'amber'),
      kpiCard('Devices presents (info)', fmt(z.device_count),
        (z.pda_count || 0) + ' PDA + ' + (z.tripode_count || 0) +
        ' tripode' + (((z.tripode_count || 0) > 1) ? 's' : '') +
        ' \u2014 recharges et renforts mobiles inclus', 'amber'),
    );
  } else {
    const _ps = periodStaffing(z);
    const _capInfo = zoneCapacity(z);
    kpiHost.append(
      kpiCard('Effectif soutenu pic', fmt(_ps.maxSustained) + ' agents',
        'pic instantane absorbe par la file (capacite ' + _capInfo.cap + ' ' + _capInfo.unit + '/h)', 'amber'),
      kpiCard('Services 8 h cumules', fmt(_ps.totalServices),
        'matin ' + _ps.shiftTotals.matin +
        ' \u00b7 soir ' + _ps.shiftTotals.soir +
        ' \u00b7 nuit ' + _ps.shiftTotals.nuit, 'amber'),
    );
  }

  const progBody = tpl.querySelector('[data-bind=progression]');
  k.progression.forEach(p => {
    const cls = p.delta_pct == null ? 'delta-flat' : (p.delta_pct >= 0 ? 'delta-up' : 'delta-down');
    const sign = p.delta_pct == null ? '-' : (p.delta_pct >= 0 ? '+' : '') + p.delta_pct + '%';
    const peakP = fmt(p.peak_p) + (p.peak_p_hm ? ' (' + p.peak_p_hm + ')' : '');
    progBody.appendChild(el('tr', null,
      el('td', null, fmtDateFr(p.date)),
      el('td', { class: 'num' }, fmt(p.e)),
      el('td', { class: 'num' }, fmt(p.s)),
      el('td', { class: 'num' }, peakP),
      el('td', { class: 'num ' + cls }, sign),
    ));
  });

  const stressBody = tpl.querySelector('[data-bind=stress]');
  k.top_stress.forEach(s => {
    const flowCell = el('td', { class: 'num' }, el('strong', null, fmt(s.flow)));
    stressBody.appendChild(el('tr', null,
      el('td', null, fmtTsFr(s.ts)),
      el('td', { class: 'num' }, fmt(s.e)),
      el('td', { class: 'num' }, fmt(s.s)),
      flowCell,
    ));
  });

  // Section detail par jour (placee juste apres les KPIs)
  const daysHost = tpl.querySelector('[data-bind=days-section]');
  const daysSection = el('div', { class: 'days-section' });
  daysSection.appendChild(el('h3', null,
    'Detail par jour - cliquer pour la vue heure par heure'));
  const grid = el('div', { class: 'peaks-grid' });
  z.kpis.progression.forEach((p, i) => grid.appendChild(buildDayCard(z, p, i + 1, showZoneDay)));
  daysSection.appendChild(grid);
  daysHost.replaceWith(daysSection);

  const main = document.getElementById('main');
  main.replaceChildren();
  main.appendChild(tpl);
  const chartHost = document.querySelector('[data-bind=day-charts]');
  renderCharts(z, chartHost);

  main.scrollTop = 0;
}

function buildSlotLabels() {
  const out = [];
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m += 15) {
      out.push(String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0'));
    }
  }
  return out;
}

function renderCharts(z, host) {
  charts.forEach(c => c.destroy());
  charts = [];

  const labels = buildSlotLabels();
  const labelIndex = Object.fromEntries(labels.map((l, i) => [l, i]));

  const optsLeft = {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { labels: { color: '#e6ecf5', boxWidth: 14, font: { size: 11 } } },
      tooltip: { backgroundColor: '#0f1620', borderColor: '#2a3548', borderWidth: 1 },
      zoom: {
        pan: { enabled: true, mode: 'x', modifierKey: null },
        zoom: {
          // wheel desactive par defaut : evite de zoomer en scrollant la page
          // Active apres 2.5s de survol (cf. mouseenter handler en bas)
          wheel: { enabled: false },
          pinch: { enabled: true },
          drag: { enabled: false },
          mode: 'x',
        },
        limits: { x: { minRange: 4 } },
      },
    },
    scales: {
      x: { ticks: { color: '#8aa0bd', maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
           grid: { color: 'rgba(255,255,255,0.04)' } },
      y: { beginAtZero: true, ticks: { color: '#8aa0bd' },
           grid: { color: 'rgba(255,255,255,0.06)' }, title: { display: true, text: 'Entrees / Sorties', color: '#8aa0bd' } },
      y1: { position: 'right', beginAtZero: true, ticks: { color: '#4f9eff' },
            grid: { drawOnChartArea: false }, title: { display: true, text: 'Presents', color: '#4f9eff' } },
    },
  };

  z.days.forEach(d => {
    const arrE = new Array(labels.length).fill(null);
    const arrS = new Array(labels.length).fill(null);
    const arrP = new Array(labels.length).fill(null);
    (z.by_day[d] || []).forEach(it => {
      const idx = labelIndex[it.hm];
      if (idx == null) return;
      arrE[idx] = it.e;
      arrS[idx] = it.s;
      arrP[idx] = it.p;
    });

    const card = el('div', { class: 'chart-card' });
    const title = el('h3', null, 'Journee ' + fmtDateFr(d) + ' - 00h00 -> 23h45 (pas 15 min, scroll = zoom)');
    const expandBtn = el('button', { class: 'chart-expand', 'aria-label': 'Ouvrir en grand', title: 'Ouvrir en grand' });
    expandBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"' +
      ' stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"></polyline>' +
      '<polyline points="9 21 3 21 3 15"></polyline><line x1="21" y1="3" x2="14" y2="10"></line>' +
      '<line x1="3" y1="21" x2="10" y2="14"></line></svg>';
    const wrap = el('div', { class: 'chart-wrap' });
    const canvas = document.createElement('canvas');
    wrap.appendChild(canvas);
    card.appendChild(title);
    card.appendChild(expandBtn);
    card.appendChild(wrap);
    host.appendChild(card);

    const datasets = [
      { label: 'Entrees', data: arrE, borderColor: '#4ade80', backgroundColor: '#4ade8022',
        borderWidth: 1.6, pointRadius: 0, pointHoverRadius: 4, tension: 0.25, spanGaps: true, yAxisID: 'y' },
      { label: 'Sorties', data: arrS, borderColor: '#f87171', backgroundColor: '#f8717122',
        borderWidth: 1.6, pointRadius: 0, pointHoverRadius: 4, tension: 0.25, spanGaps: true, yAxisID: 'y' },
    ];
    if (z.category !== 'porte') {
      datasets.push({ label: 'Presents', data: arrP, borderColor: '#4f9eff', backgroundColor: '#4f9eff22',
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.25, spanGaps: true, yAxisID: 'y1', fill: true });
    }
    const chartOpts = z.category === 'porte'
      ? Object.assign({}, optsLeft, { scales: Object.assign({}, optsLeft.scales, { y1: { display: false } }) })
      : optsLeft;
    const ch = new Chart(canvas, {
      type: 'line',
      data: { labels, datasets },
      options: chartOpts,
    });
    charts.push(ch);
    expandBtn.addEventListener('click', () => openChartFullscreen(ch, title.textContent));
    attachWheelZoomDelay(wrap, ch);
  });
}

const ZOOM_HOVER_DELAY_MS = 2500;

function attachWheelZoomDelay(wrap, chart) {
  // Empeche le zoom wheel accidentel quand on scroll la page : il faut rester
  // sur le graphique pendant ZOOM_HOVER_DELAY_MS pour activer wheel zoom.
  const hint = el('div', { class: 'zoom-hint' }, 'Survol... wheel zoom dans 2.5s');
  wrap.appendChild(hint);
  let timer = null;
  let ready = false;
  const setReady = (v) => {
    ready = v;
    chart.options.plugins.zoom.zoom.wheel.enabled = v;
    chart.update('none');
    hint.classList.toggle('ready', v);
    hint.textContent = v ? 'Zoom actif (wheel)' : 'Survol... wheel zoom dans 2.5s';
  };
  wrap.addEventListener('mouseenter', () => {
    hint.classList.add('visible');
    if (ready) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => setReady(true), ZOOM_HOVER_DELAY_MS);
  });
  wrap.addEventListener('mouseleave', () => {
    if (timer) { clearTimeout(timer); timer = null; }
    hint.classList.remove('visible');
    if (ready) setReady(false);
  });
}

// Plugin chartjs-plugin-zoom enregistrement (charge via CDN)
if (typeof Chart !== 'undefined' && typeof ChartZoom !== 'undefined') {
  Chart.register(ChartZoom);
}

let fullscreenChart = null;
function openChartFullscreen(srcChart, titleText) {
  const overlay = document.getElementById('chart-fullscreen');
  const canvas = document.getElementById('chart-fullscreen-canvas');
  overlay.hidden = false;
  if (fullscreenChart) fullscreenChart.destroy();
  // Clone des datasets pour ne pas alterer le chart source
  const data = {
    labels: srcChart.data.labels.slice(),
    datasets: srcChart.data.datasets.map(ds => Object.assign({}, ds, { data: ds.data.slice() })),
  };
  const opts = JSON.parse(JSON.stringify(srcChart.config.options));
  opts.plugins.title = { display: true, text: titleText, color: '#e6ecf5',
    font: { size: 14 }, padding: { bottom: 12 } };
  // En fullscreen, wheel zoom directement actif (pas de delai d'inhibition)
  if (opts.plugins.zoom && opts.plugins.zoom.zoom && opts.plugins.zoom.zoom.wheel) {
    opts.plugins.zoom.zoom.wheel.enabled = true;
  }
  fullscreenChart = new Chart(canvas, { type: srcChart.config.type, data, options: opts });
}
function closeChartFullscreen() {
  document.getElementById('chart-fullscreen').hidden = true;
  if (fullscreenChart) { fullscreenChart.destroy(); fullscreenChart = null; }
}

function renderPeaksOverview() {
  const main = document.getElementById('main');
  main.replaceChildren();

  const eventPretty = DATA.event.replace(/_/g, ' ').toUpperCase();
  const ulabel = unitLabel();
  const list = currentList();
  main.appendChild(el('h2', null, 'Pics de presents par ' + (ulabel === 'portes' ? 'porte' : 'zone')));
  main.appendChild(el('div', { class: 'subtitle' },
    list.length + ' ' + ulabel + ' - ' + eventPretty + ' ' + DATA.year +
    ' - clique sur une carte pour voir le pic par jour'));

  const toolbar = el('div', { class: 'peaks-toolbar' });
  const inp = el('input', { id: 'peaks-filter', placeholder: 'Filtrer une ' + (ulabel === 'portes' ? 'porte' : 'zone') + '...' });
  inp.value = peaksFilter;
  inp.addEventListener('input', () => { peaksFilter = inp.value; renderPeaksOverview(); });
  const lbl = el('label', { for: 'peaks-sort' }, 'Tri :');
  const sel = el('select', { id: 'peaks-sort' });
  [
    ['peak_desc', 'Pic decroissant'],
    ['peak_asc', 'Pic croissant'],
    ['zone_asc', 'Nom (A-Z)'],
    ['when_asc', 'Date du pic (chrono)'],
  ].forEach(([v, t]) => {
    const o = el('option', { value: v }, t);
    if (v === peaksSort) o.selected = true;
    sel.appendChild(o);
  });
  sel.addEventListener('change', () => { peaksSort = sel.value; renderPeaksOverview(); });
  toolbar.append(inp, lbl, sel);
  main.appendChild(toolbar);

  const f = peaksFilter.toLowerCase();
  const rows = list
    .map(z => ({
      name: z.name,
      category: z.category,
      peak: z.kpis.peak_presents.val,
      ts: z.kpis.peak_presents.ts,
      peak_hour_e: z.kpis.peak_hour_e,
      peak_hour_s: z.kpis.peak_hour_s,
      total_e: z.total_entree,
      total_s: z.total_sortie,
      ps: z.category === 'porte' ? null : periodStaffing(z),
      cap_info: zoneCapacity(z),
      device_count: z.device_count,
      pda_count: z.pda_count,
      tripode_count: z.tripode_count,
    }))
    .filter(r => r.name.toLowerCase().includes(f));
  const cmp = {
    peak_desc: (a, b) => b.peak - a.peak,
    peak_asc: (a, b) => a.peak - b.peak,
    zone_asc: (a, b) => a.name.localeCompare(b.name),
    when_asc: (a, b) => (a.ts || '').localeCompare(b.ts || ''),
  }[peaksSort];
  rows.sort(cmp);

  // Pic global pour calibrer la heatmap
  const periodPeakP = rows.reduce((m, r) => Math.max(m, r.peak), 0) || 1;

  const grid = el('div', { class: 'peaks-grid' });
  if (!rows.length) {
    grid.appendChild(el('div', { class: 'empty' }, 'Aucune entree ne correspond a ce filtre.'));
  }
  rows.forEach((r, i) => {
    const isPorte = r.category === 'porte';
    const refMetric = isPorte ? (r.total_e + r.total_s) : r.peak;
    const refMax = isPorte
      ? rows.reduce((m, x) => Math.max(m, x.total_e + x.total_s), 0) || 1
      : periodPeakP;
    const intensity = refMetric / refMax;
    const card = el('div', { class: 'peak-card clickable ' + intensityClass(intensity) });
    card.addEventListener('click', () => showPeaksDetail(r.name));
    if (intensity >= 0.75) {
      card.appendChild(el('span', { class: 'heat-badge hot' }, 'PIC'));
    } else if (intensity >= 0.50) {
      card.appendChild(el('span', { class: 'heat-badge warm' }, 'CHARGE'));
    } else if (intensity >= 0.30) {
      card.appendChild(el('span', { class: 'heat-badge mild' }, 'ACTIF'));
    }
    card.append(
      el('span', { class: 'rank' }, '#' + (i + 1)),
      el('div', { class: 'pc-zone' }, r.name),
      el('div', { class: 'pc-value' }, isPorte ? fmt(r.total_e + r.total_s) : fmt(r.peak)),
      el('div', { class: 'pc-when' },
        isPorte ? 'passages totaux' :
        (r.ts ? 'pic le ' + fmtTsFr(r.ts) : 'pas de pic')),
      el('div', { class: 'pc-extras' },
        el('div', null,
          el('div', { class: 'pc-extra-label' }, 'Pic entrees /h'),
          el('div', { class: 'pc-extra-val green' }, fmt(r.peak_hour_e.val)),
          el('div', { class: 'pc-extra-when' }, fmtHourWindowFull(r.peak_hour_e)),
        ),
        el('div', null,
          el('div', { class: 'pc-extra-label' }, 'Pic sorties /h'),
          el('div', { class: 'pc-extra-val red' }, fmt(r.peak_hour_s.val)),
          el('div', { class: 'pc-extra-when' }, fmtHourWindowFull(r.peak_hour_s)),
        ),
      ),
      el('div', { class: 'pc-footer' },
        el('span', null, 'Total E ', el('strong', null, fmt(r.total_e))),
        el('span', null, 'Total S ', el('strong', null, fmt(r.total_s))),
      ),
    );
    if (r.category === 'porte') {
      const theoCap = (r.pda_count || 0) * 500 + (r.tripode_count || 0) * 600;
      const peakFlow = (r.peak_hour_e.val || 0) + (r.peak_hour_s.val || 0);
      // peak_hour_e et peak_hour_s sont des fenetres distinctes, on prend la somme comme proxy
      card.appendChild(el('div', { class: 'pc-staff' },
        'Dispositif : ', el('strong', null, fmt(r.device_count)),
        ' devices \u00b7 capa ~', el('strong', null, fmt(theoCap)), '/h',
        el('div', { class: 'pc-extra-when' },
          (r.pda_count || 0) + ' PDA + ' + (r.tripode_count || 0) + ' tripode' +
          ((r.tripode_count || 0) > 1 ? 's' : '')),
      ));
    } else if (r.ps) {
      card.appendChild(el('div', { class: 'pc-staff' },
        'Effectif soutenu : ', el('strong', null, fmt(r.ps.maxSustained)),
        ' agents \u00b7 Services 8 h periode : ', el('strong', null, fmt(r.ps.totalServices)),
        el('div', { class: 'pc-extra-when' },
          'matin ' + r.ps.shiftTotals.matin +
          ' \u00b7 soir ' + r.ps.shiftTotals.soir +
          ' \u00b7 nuit ' + r.ps.shiftTotals.nuit),
        el('div', { class: 'pc-extra-when' },
          'capacite ' + r.cap_info.cap + ' ' + r.cap_info.unit + '/h'),
      ));
    }
    grid.appendChild(card);
  });
  main.appendChild(grid);
  main.scrollTop = 0;
}

const STAFF_LOW_VOLUME_RATIO = 0.3;
const SERVICE_HOURS = 8;
const PAUSE_MINUTES = 20;
const EFFECTIVE_HOURS_PER_SERVICE = SERVICE_HOURS - PAUSE_MINUTES / 60; // ~7.67h
const SHIFTS = [
  { id: 'matin', label: '6h - 14h', short: 'matin', start: 6, end: 14 },
  { id: 'soir',  label: '14h - 22h', short: 'soir',  start: 14, end: 22 },
  { id: 'nuit',  label: '22h - 6h',  short: 'nuit',  start: 22, end: 6 },
];
const STAFFING_NOTE = 'shifts 6h-14h / 14h-22h / 22h-6h, pause 20 min, pic d\'1 h absorbe par la file (15 min d\'attente toleree)';

function hoursInShift(shift) {
  const out = [];
  if (shift.start < shift.end) {
    for (let h = shift.start; h < shift.end; h++) out.push(h);
  } else {
    for (let h = shift.start; h < 24; h++) out.push(h);
    for (let h = 0; h < shift.end; h++) out.push(h);
  }
  return out;
}

function sustainedPeakInHours(staff, hours) {
  let peak = 0;
  for (let i = 0; i < hours.length - 1; i++) {
    peak = Math.max(peak, Math.min(staff[hours[i]], staff[hours[i + 1]]));
  }
  if (peak === 0) {
    // Fallback : tout est isole sur 1 h dans ce shift -> on prend le pic instantane
    peak = hours.reduce((m, h) => Math.max(m, staff[h]), 0);
  }
  return peak;
}

const ZONES_AS_TRIBUNE = new Set(['AA ARNAGE', 'AA MULSANNE']);

function zoneCapacity(unitOrName) {
  // Accepte un nom (string) ou l'objet unite complet
  if (unitOrName && typeof unitOrName === 'object' && unitOrName.category === 'porte') {
    // Sur une porte, les devices sont deja deployes, pas de calcul de staffing.
    return { cap: 0, unit: 'scans', isPorte: true,
             pda_count: unitOrName.pda_count || 0,
             tripode_count: unitOrName.tripode_count || 0,
             device_count: unitOrName.device_count || 0 };
  }
  const name = (typeof unitOrName === 'string') ? unitOrName : (unitOrName ? unitOrName.name : '');
  const n = (name || '').toUpperCase();
  if (n.startsWith('TRIBUNE') || n === 'PADDOCKS' ||
      n === 'MAISON DES HUNAUDIERES' || n === 'VISITES MUSEE' || n === 'WELCOME' ||
      ZONES_AS_TRIBUNE.has(n)) {
    return { cap: 650, unit: 'personnes' };
  }
  return { cap: 250, unit: 'vehicules' };
}

const STAFF_NEGLIGIBLE_FLOW_RATIO = 0.1; // < 10 % cap -> absorbe sans agent dedie

function staffNeededFor(e, s, cap) {
  if (e === 0 && s === 0) return 0;
  // Volume total tres faible -> un seul agent alterne sans risque
  if ((e + s) <= cap * STAFF_LOW_VOLUME_RATIO) return 1;
  const negligible = cap * STAFF_NEGLIGIBLE_FLOW_RATIO;
  // Sens marginal -> absorbe par l'agent en charge du sens principal
  if (s < negligible) return Math.max(1, Math.ceil(e / cap));
  if (e < negligible) return Math.max(1, Math.ceil(s / cap));
  // Sinon un agent par flux pour eviter les erreurs (sens separes)
  return Math.max(1, Math.ceil(e / cap) + Math.ceil(s / cap));
}

function dayStaffing(z, date) {
  const intervals = z.by_day[date] || [];
  const { cap } = zoneCapacity(z);
  if (!cap) return null; // portes : pas de recommandation staffing
  const hourly = [];
  for (let h = 0; h < 24; h++) hourly.push({ e: 0, s: 0, has: false });
  intervals.forEach(it => {
    const h = parseInt(it.hm.split(':')[0], 10);
    hourly[h].e += it.e;
    hourly[h].s += it.s;
    hourly[h].has = true;
  });
  const staff = hourly.map(h => staffNeededFor(h.e, h.s, cap));
  let first = -1, last = -1;
  for (let h = 0; h < 24; h++) if (hourly[h].has) { if (first < 0) first = h; last = h; }
  if (first < 0) return null;
  const windowHours = last - first + 1;
  const peak = Math.max.apply(null, staff);
  const peakHour = staff.indexOf(peak);
  const totalAH = staff.reduce((a, b) => a + b, 0);

  // Decoupage sur shifts FIXES (6-14, 14-22, 22-6).
  // Pour chaque shift actif, on commande l'effectif soutenu pendant ce shift
  // (pic atteint sur 2 h consecutives au moins ; un creneau d'1 h isole est
  // absorbe par la tolerance de 15 min d'attente).
  const shiftBreakdown = SHIFTS.map(shift => {
    const hours = hoursInShift(shift);
    const activeCount = hours.reduce((n, h) => n + (hourly[h].has ? 1 : 0), 0);
    const sustained = activeCount ? sustainedPeakInHours(staff, hours) : 0;
    const instPeak = activeCount ? hours.reduce((m, h) => Math.max(m, staff[h]), 0) : 0;
    return {
      id: shift.id, label: shift.label, short: shift.short,
      active: activeCount > 0, sustained, instPeak,
    };
  });

  const sustainedPeak = shiftBreakdown.reduce((m, s) => Math.max(m, s.sustained), 0);
  const services = shiftBreakdown.reduce((sum, s) => sum + (s.active ? s.sustained : 0), 0);

  return {
    peak, sustainedPeak, peakHour, windowHours, totalAH, services, cap,
    shiftBreakdown,
  };
}

function shiftBreakdownLabel(breakdown) {
  return breakdown
    .filter(s => s.active && s.sustained > 0)
    .map(s => s.short + ' ' + s.sustained)
    .join(' \u00b7 ');
}

function periodStaffing(z) {
  let totalServices = 0, maxPeak = 0, maxSustained = 0, totalAH = 0;
  const shiftTotals = { matin: 0, soir: 0, nuit: 0 };
  z.kpis.progression.forEach(p => {
    const d = dayStaffing(z, p.date);
    if (!d) return;
    totalServices += d.services;
    maxPeak = Math.max(maxPeak, d.peak);
    maxSustained = Math.max(maxSustained, d.sustainedPeak);
    totalAH += d.totalAH;
    d.shiftBreakdown.forEach(s => {
      if (s.active) shiftTotals[s.id] += s.sustained;
    });
  });
  return { totalServices, maxPeak, maxSustained, totalAH, shiftTotals };
}

function buildDayCard(z, p, rank, onClick) {
  const isPorte = z.category === 'porte';
  // Heatmap : intensite vs pic horaire de la periode (flux pour portes / presents pour zones)
  const refPeak = isPorte ? (z.kpis.peak_hour_flow.val || 1) : (z.kpis.peak_presents.val || 1);
  const dayMetric = isPorte ? (p.peak_hour_flow ? p.peak_hour_flow.val : (p.e + p.s)) : p.peak_p;
  const intensity = dayMetric / refPeak;
  const card = el('div', { class: 'peak-card clickable ' + intensityClass(intensity) });
  card.addEventListener('click', () => onClick(z.name, p.date));
  const ds = dayStaffing(z, p.date);
  if (intensity >= 0.75) {
    card.appendChild(el('span', { class: 'heat-badge hot' }, 'PIC'));
  } else if (intensity >= 0.50) {
    card.appendChild(el('span', { class: 'heat-badge warm' }, 'CHARGE'));
  } else if (intensity >= 0.30) {
    card.appendChild(el('span', { class: 'heat-badge mild' }, 'ACTIF'));
  }
  // Pill UAM si la porte a eu de l'aide UAM ce jour
  if (isPorte && z.uam_help && z.uam_help.per_day && z.uam_help.per_day[p.date]) {
    const u = z.uam_help.per_day[p.date];
    if (u.pda_count > 0) {
      card.appendChild(el('span', { class: 'heat-badge uam',
        title: u.scan_count + ' scans UAM sur ' + u.active_hours + 'h' },
        'UAM ' + u.pda_count));
    }
  }
  card.append(
    el('span', { class: 'rank' }, '#' + rank),
    el('div', { class: 'pc-zone' }, fmtDateFr(p.date)),
    el('div', { class: 'pc-value' }, isPorte ? fmt(p.e + p.s) : fmt(p.peak_p)),
    el('div', { class: 'pc-when' },
      isPorte ? fmt(p.e) + ' E + ' + fmt(p.s) + ' S' :
      (p.peak_p_hm ? 'pic a ' + p.peak_p_hm : 'pas de pic')),
    el('div', { class: 'pc-extras' },
      el('div', null,
        el('div', { class: 'pc-extra-label' }, 'Pic entrees /h'),
        el('div', { class: 'pc-extra-val green' }, fmt(p.peak_hour_e.val)),
        el('div', { class: 'pc-extra-when' }, fmtHourWindowTime(p.peak_hour_e)),
      ),
      el('div', null,
        el('div', { class: 'pc-extra-label' }, 'Pic sorties /h'),
        el('div', { class: 'pc-extra-val red' }, fmt(p.peak_hour_s.val)),
        el('div', { class: 'pc-extra-when' }, fmtHourWindowTime(p.peak_hour_s)),
      ),
    ),
    el('div', { class: 'pc-footer' },
      el('span', null, 'Total E ', el('strong', null, fmt(p.e))),
      el('span', null, 'Total S ', el('strong', null, fmt(p.s))),
    ),
  );
  if (ds) {
    const staffLine = el('div', { class: 'pc-staff' },
      'Effectif soutenu : ', el('strong', null, fmt(ds.sustainedPeak)),
      ' agents \u00b7 Services 8 h : ', el('strong', null, fmt(ds.services)),
    );
    const breakdown = shiftBreakdownLabel(ds.shiftBreakdown);
    if (breakdown) {
      staffLine.appendChild(el('div', { class: 'pc-extra-when' }, breakdown));
    }
    if (ds.peak > ds.sustainedPeak) {
      staffLine.appendChild(el('div', { class: 'pc-extra-when' },
        'pic instantane : ' + ds.peak + ' agents (absorbe par la file)'));
    }
    card.appendChild(staffLine);
  }
  return card;
}

function renderPeaksDetail() {
  const z = findUnit(peaksDetailZone);
  if (!z) { showPeaksOverview(); return; }
  const main = document.getElementById('main');
  main.replaceChildren();

  const back = el('button', { class: 'back-btn' }, '<- toutes les ' + unitLabel());
  back.addEventListener('click', showPeaksOverview);
  main.appendChild(back);

  main.appendChild(el('h2', null, z.name));
  const eventPretty = DATA.event.replace(/_/g, ' ').toUpperCase();
  main.appendChild(el('div', { class: 'subtitle' },
    'Pic de presents par jour - ' + z.days.length + ' jours - ' + eventPretty + ' ' + DATA.year));

  const toolbar = el('div', { class: 'peaks-toolbar' });
  const lbl = el('label', { for: 'peaks-day-sort' }, 'Tri :');
  const sel = el('select', { id: 'peaks-day-sort' });
  [
    ['date_asc', 'Chronologique'],
    ['peak_desc', 'Pic decroissant'],
    ['peak_asc', 'Pic croissant'],
  ].forEach(([v, t]) => {
    const o = el('option', { value: v }, t);
    if (v === peaksDaySort) o.selected = true;
    sel.appendChild(o);
  });
  sel.addEventListener('change', () => { peaksDaySort = sel.value; renderPeaksDetail(); });
  toolbar.append(lbl, sel);
  main.appendChild(toolbar);

  const rows = [...z.kpis.progression];
  const cmp = {
    date_asc: (a, b) => (a.date || '').localeCompare(b.date || ''),
    peak_desc: (a, b) => b.peak_p - a.peak_p,
    peak_asc: (a, b) => a.peak_p - b.peak_p,
  }[peaksDaySort];
  rows.sort(cmp);

  const grid = el('div', { class: 'peaks-grid' });
  if (!rows.length) {
    grid.appendChild(el('div', { class: 'empty' }, 'Aucune donnee.'));
  }
  rows.forEach((p, i) => grid.appendChild(buildDayCard(z, p, i + 1, showZoneDay)));
  main.appendChild(grid);
  main.scrollTop = 0;
}

function renderZoneDay(z, date) {
  const main = document.getElementById('main');
  main.replaceChildren();

  const back = el('button', { class: 'back-btn' }, '<- retour ' + z.name);
  back.addEventListener('click', () => selectZone(z.name));
  main.appendChild(back);

  main.appendChild(el('h2', null, z.name));
  const eventPretty = DATA.event.replace(/_/g, ' ').toUpperCase();
  main.appendChild(el('div', { class: 'subtitle' },
    'Detail heure par heure - ' + fmtDateFr(date) + ' - ' + eventPretty + ' ' + DATA.year));

  const nav = el('div', { class: 'day-nav' });
  z.days.forEach(d => {
    const btn = el('button', { class: 'day-nav-btn' + (d === date ? ' active' : '') }, fmtDateFr(d));
    btn.addEventListener('click', () => showZoneDay(z.name, d));
    nav.appendChild(btn);
  });
  main.appendChild(nav);

  const intervals = z.by_day[date] || [];
  const hourly = [];
  for (let h = 0; h < 24; h++) {
    hourly.push({ h, e: 0, s: 0, max_p: 0, max_p_hm: null, slots: 0 });
  }
  intervals.forEach(it => {
    const h = parseInt(it.hm.split(':')[0], 10);
    const slot = hourly[h];
    slot.e += it.e;
    slot.s += it.s;
    slot.slots += 1;
    if (it.p > slot.max_p) {
      slot.max_p = it.p;
      slot.max_p_hm = it.hm;
    }
  });

  const capInfo = zoneCapacity(z);
  const isPorte = capInfo.isPorte;
  // Heatmap : intensite vs pic horaire de la periode (flux E+S)
  const periodPeakFlow = z.kpis.peak_hour_flow.val || 1;
  const grid = el('div', { class: 'peaks-grid' });
  let visible = 0;
  hourly.forEach(d => {
    if (d.slots === 0) return;
    visible++;
    const flow = d.e + d.s;
    const intensity = flow / periodPeakFlow;
    const net = d.e - d.s;
    const sign = net > 0 ? '+' : '';
    const card = el('div', { class: 'peak-card ' + intensityClass(intensity) });
    if (intensity >= 0.75) {
      card.appendChild(el('span', { class: 'heat-badge hot' }, 'PIC'));
    } else if (intensity >= 0.50) {
      card.appendChild(el('span', { class: 'heat-badge warm' }, 'CHARGE'));
    } else if (intensity >= 0.30) {
      card.appendChild(el('span', { class: 'heat-badge mild' }, 'ACTIF'));
    }
    const children = [
      el('div', { class: 'pc-zone' }, d.h + 'h - ' + (d.h + 1) + 'h'),
      el('div', { class: 'pc-value' }, isPorte ? fmt(flow) : fmt(d.max_p)),
      el('div', { class: 'pc-when' },
        isPorte ? 'passages cumules' :
        (d.max_p_hm ? 'pic a ' + d.max_p_hm : 'pas de pic')),
      el('div', { class: 'pc-extras' },
        el('div', null,
          el('div', { class: 'pc-extra-label' }, 'Entrees'),
          el('div', { class: 'pc-extra-val green' }, fmt(d.e)),
        ),
        el('div', null,
          el('div', { class: 'pc-extra-label' }, 'Sorties'),
          el('div', { class: 'pc-extra-val red' }, fmt(d.s)),
        ),
      ),
    ];
    if (!isPorte) {
      const agents = staffNeededFor(d.e, d.s, capInfo.cap);
      children.push(el('div', { class: 'pc-staff' },
        el('div', { class: 'pc-extra-label' }, 'Agents recommandes'),
        el('div', { class: 'pc-extra-val amber' }, fmt(agents)),
        el('div', { class: 'pc-extra-when' }, 'capacite ' + capInfo.cap + ' ' + capInfo.unit + '/h'),
      ));
    } else {
      const hourStr = String(d.h).padStart(2, '0');
      const cap = porteCapacityAtHour(z, date, hourStr);
      const util = cap.capacity > 0 ? Math.round((flow / cap.capacity) * 100) : null;
      let val, sub;
      if (cap.mode === 'tripodes') {
        const sousStaff = cap.sousStaffed ? ' \u26a0' : '';
        val = fmt(cap.agentsAccueil) + ' A \u00b7 ' + fmt(cap.agentsSecurite) + ' S' + sousStaff;
        const minNote = ' / min ' + cap.agentsMin + ' pour ' + cap.nbTripodes + ' trip.';
        sub = 'capa ' + fmt(cap.capacity) + '/h \u2192 utilisation ' +
              (util !== null ? util + ' %' : 'n/a') + minNote;
      } else {
        val = fmt(cap.agentsAccueil) + ' A \u00b7 ' + fmt(cap.agentsSecurite) + ' S';
        sub = cap.agentsTotal > 0
          ? 'capa ~' + fmt(cap.capacity) + '/h \u2192 utilisation ' +
            (util !== null ? util + ' %' : 'n/a')
          : 'staffing non renseigne';
      }
      children.push(el('div', { class: 'pc-staff' },
        el('div', { class: 'pc-extra-label' }, 'Personnel planifie'),
        el('div', { class: 'pc-extra-val amber' }, val),
        el('div', { class: 'pc-extra-when' }, sub),
      ));
      // Aide UAM (et renfort PDA pour portes tripodes) sur ce creneau
      const hourKey = date + 'T' + hourStr + ':00';
      const uamSlot = ((z.uam_help || {}).per_hour || {})[hourKey];
      if (uamSlot && uamSlot.pda_count > 0) {
        children.push(el('div', { class: 'pc-uam' },
          'Aide UAM : ', el('strong', null, uamSlot.pda_count + ' PDA'),
          ' (', el('strong', null, fmt(uamSlot.scan_count)), ' scans)'));
      }
      if (isTripodesPorte(z)) {
        const rSlot = ((z.pda_renfort || {}).per_hour || {})[hourKey];
        if (rSlot && rSlot.pda_count > 0) {
          children.push(el('div', { class: 'pc-renfort' },
            'PDA renfort tripodes : ', el('strong', null, rSlot.pda_count + ' PDA'),
            ' (', el('strong', null, fmt(rSlot.scan_count)), ' scans hors tripodes)'));
        }
      }
    }
    children.push(el('div', { class: 'pc-footer' },
      el('span', null, 'Solde ', el('strong', null, sign + fmt(net))),
    ));
    children.forEach(c => card.appendChild(c));
    grid.appendChild(card);
  });
  if (!visible) {
    grid.appendChild(el('div', { class: 'empty' }, 'Aucune donnee pour ce jour.'));
  }
  main.appendChild(grid);
  main.scrollTop = 0;
}

// Bouton fermeture fullscreen + double-clic reset zoom
document.getElementById('chart-fullscreen-close').addEventListener('click', closeChartFullscreen);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !document.getElementById('chart-fullscreen').hidden) closeChartFullscreen();
});
document.getElementById('chart-fullscreen-canvas').addEventListener('dblclick', () => {
  if (fullscreenChart && fullscreenChart.resetZoom) fullscreenChart.resetZoom();
});

document.getElementById('zone-search').addEventListener('input', e => renderSidebar(e.target.value));
document.getElementById('menu-toggle').addEventListener('click', () => {
  document.body.classList.toggle('menu-collapsed');
  // Recalcul des dimensions des charts apres l'animation de la sidebar
  setTimeout(() => charts.forEach(c => c.resize()), 220);
});
document.getElementById('nav-peaks').addEventListener('click', () => {
  showPeaksOverview();
  closeMenuOnMobile();
});
document.getElementById('home-btn').addEventListener('click', () => {
  showHome();
  closeMenuOnMobile();
});

document.addEventListener('keydown', e => {
  const tag = (document.activeElement && document.activeElement.tagName) || '';
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

  if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && viewMode === 'zone') {
    const visible = Array.from(document.querySelectorAll('.zone-list li'))
      .map(li => li.dataset.zone);
    if (!visible.length) return;
    let idx = visible.indexOf(activeZone);
    if (idx === -1) idx = 0;
    else idx = e.key === 'ArrowDown'
      ? Math.min(idx + 1, visible.length - 1)
      : Math.max(idx - 1, 0);
    e.preventDefault();
    selectZone(visible[idx]);
    const li = document.querySelector('.zone-list li.active');
    if (li) li.scrollIntoView({ block: 'nearest' });
    return;
  }

  if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && viewMode === 'zone-day') {
    const z = findUnit(activeZone);
    if (!z || !z.days.length) return;
    let idx = z.days.indexOf(activeDay);
    if (idx === -1) idx = 0;
    else idx = e.key === 'ArrowRight'
      ? Math.min(idx + 1, z.days.length - 1)
      : Math.max(idx - 1, 0);
    e.preventDefault();
    showZoneDay(z.name, z.days[idx]);
  }
});

// Onglets categorie (zones / portes)
document.querySelectorAll('.cat-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const cat = btn.dataset.cat;
    if (cat === currentCategory) return;
    currentCategory = cat;
    document.querySelectorAll('.cat-tab').forEach(b => {
      b.classList.toggle('active', b.dataset.cat === cat);
    });
    document.getElementById('zone-search').placeholder =
      'Filtrer ' + (cat === 'porte' ? 'une porte' : 'une zone') + '...';
    document.getElementById('zone-search').value = '';
    peaksFilter = '';
    activeZone = null;
    activeDay = null;
    renderSidebar('');
    const list = currentList();
    if (list.length) selectZone(list[0].name);
  });
});

// Drawer ferme par defaut sur mobile
if (isMobile()) document.body.classList.add('menu-collapsed');

// Tap sur le backdrop sombre (cote droit du drawer) ferme le menu
document.addEventListener('click', e => {
  if (!isMobile() || document.body.classList.contains('menu-collapsed')) return;
  const aside = document.querySelector('aside');
  const toggle = document.getElementById('menu-toggle');
  if (aside.contains(e.target) || toggle.contains(e.target)) return;
  document.body.classList.add('menu-collapsed');
});

renderSidebar('');
showHome();
</script>
</body>
</html>
"""




if __name__ == '__main__':
    main()
