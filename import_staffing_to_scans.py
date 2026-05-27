"""
Importe les effectifs (Accueil + Securite) dans les documents scan a partir du
fichier de validation `staffing_mapping_audit.numbers` (ou .csv).

Pour chaque unite scan (zone ou porte) :
- Recupere les affectations validees OK (filtre les NON + vides)
- Applique les reassignations PADDOCKS (depuis WELCOME)
- Pour chaque affectation Accueil : lookup calendrier.shiftcode -> donnees_presences
- Calcule agents-h total + pic simultane + courbe horaire
- Compte les postes Securite (statique, pas de temporal)
- Upsert le champ `staffing` dans parking_scans / porte_scans
"""

import os
import csv
from collections import defaultdict
from datetime import datetime
from pymongo import MongoClient

NUMBERS_FILE = '/Users/framboises/Dropbox/ACO/TITAN/cockpit/staffing_mapping_audit.numbers'
CSV_FILE = '/Users/framboises/Dropbox/ACO/TITAN/cockpit/staffing_mapping_audit.csv'

MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
DB_NAME = 'titan_dev'
SCAN_EVENT = '24h_du_mans'
SCAN_YEAR = 2025
BIBLE_EVENT = '24H AUTOS'
BIBLE_YEAR = 2025


def load_validations():
    """Retourne list de dicts {unite, categorie, num, nom, validation}."""
    rows = []
    if os.path.isfile(NUMBERS_FILE):
        try:
            from numbers_parser import Document
            doc = Document(NUMBERS_FILE)
            t = doc.sheets[0].tables[0]
            for ri in range(1, t.num_rows):
                cell = lambda c: (t.cell(ri, c).value if t.cell(ri, c) else None)
                rows.append({
                    'unite':       cell(0) or '',
                    'categorie':   cell(1) or '',
                    'num':         int(cell(2)) if cell(2) else None,
                    'nom':         cell(3) or '',
                    'validation':  (cell(4) or '').strip().upper(),
                })
            print(f'Lecture .numbers : {len(rows)} lignes')
            return rows
        except Exception as e:
            print(f'Echec lecture .numbers ({e}), fallback CSV')

    with open(CSV_FILE, 'r', encoding='utf-8') as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append({
                'unite':      row.get('Unite (scan)', ''),
                'categorie':  row.get('Categorie', ''),
                'num':        int(row.get('Numero affectation') or 0) or None,
                'nom':        row.get('Nom affectation', ''),
                'validation': (next((v for k, v in row.items() if 'Validation' in k), '') or '').strip().upper(),
            })
    print(f'Lecture CSV : {len(rows)} lignes')
    return rows


def categorize_role(categorie):
    c = (categorie or '').lower()
    if 'accueil' in c and 'chef' in c:    return 'A_chef'
    if 'accueil' in c:                    return 'A_op'
    if ('securite' in c or 'sécurité' in c) and 'chef' in c: return 'S_chef'
    if 'securite' in c or 'sécurité' in c: return 'S_op'
    return None


def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]

    validations = load_validations()

    # Application des regles :
    # - vide -> NON
    # - PADDOCKS -> reassigne a PADDOCKS, validation = OK
    # - OK / NON tels quels
    by_unit = defaultdict(list)  # unit -> [post dict]
    for v in validations:
        val = v['validation'] or 'NON'  # vide = NON
        if val == 'PADDOCKS':
            target_unit = 'PADDOCKS'
            valid = True
        elif val == 'OK':
            target_unit = v['unite']
            valid = True
        else:
            target_unit = v['unite']
            valid = False
        role = categorize_role(v['categorie'])
        if not role:
            continue
        by_unit[target_unit].append({
            'num': v['num'],
            'role': role,
            'nom': v['nom'],
            'valid': valid,
        })

    print(f'Unites avec postes : {len(by_unit)}')
    print(f'  Postes valides   : {sum(1 for posts in by_unit.values() for p in posts if p["valid"])}')
    print(f'  Postes ecartes   : {sum(1 for posts in by_unit.values() for p in posts if not p["valid"])}')

    # Calendrier par shiftcode
    cal_by_sc = {}
    for d in db['calendrier_2025_24hautos'].find({}, {'shiftcode':1,'accueil_surete':1,'donnees_presences':1}):
        sc = d.get('shiftcode')
        if sc is not None:
            cal_by_sc[int(sc)] = d

    # Bible pour info
    bible_by_num = {}
    for d in db['bible'].find({'event': BIBLE_EVENT, 'year': BIBLE_YEAR},
                               {'post.number':1, 'post.metier':1, 'post.affectation':1}):
        post = d.get('post', {})
        num = post.get('number')
        if num:
            bible_by_num[num] = {
                'metier': post.get('metier'),
                'affect': post.get('affectation'),
            }

    # Construit le staffing par unite
    unit_staffing = {}
    for unit, posts in by_unit.items():
        valid_posts = [p for p in posts if p['valid']]
        a_op_posts = [p for p in valid_posts if p['role'] == 'A_op']
        a_chef_posts = [p for p in posts if p['role'] == 'A_chef']  # informatif
        s_op_posts = [p for p in valid_posts if p['role'] == 'S_op']
        s_chef_posts = [p for p in posts if p['role'] == 'S_chef']

        # Agents-h Accueil + pic simultane via donnees_presences
        ah_total = 0
        peak_simu = 0
        peak_simu_ts = None
        # hourly aggregation: (date) -> {hour: agents_max_simultane (max over 2 demi-heures)}
        hourly = defaultdict(lambda: defaultdict(int))  # date -> hour -> agents (sum cumulatif sur 30min puis max)
        slot_sums = defaultdict(int)  # (date, heure_debut) -> nb_agents_simultanes
        for p in a_op_posts:
            cal = cal_by_sc.get(p['num'])
            if not cal or cal.get('accueil_surete') != 'A':
                continue
            dp = cal.get('donnees_presences') or []
            for jour in dp:
                if not isinstance(jour, dict):
                    continue
                date = jour.get('date')
                for slot in jour.get('plages_horaires', []):
                    nb = slot.get('nombre_personnes', 0) or 0
                    if nb:
                        key = (date, slot.get('heure_debut'))
                        slot_sums[key] += nb
                        ah_total += nb * 0.5  # 30 min = 0.5 h

        for (date, hd), val in slot_sums.items():
            if val > peak_simu:
                peak_simu = val
                peak_simu_ts = f'{date} {hd}'
        # Pour le hourly aggregate : sum E+S sur l'heure ? Non, MAX (effectif simu max dans l'heure)
        for (date, hd), val in slot_sums.items():
            hour = hd.split(':')[0] if hd else ''
            if hour:
                hourly[date][hour] = max(hourly[date][hour], val)

        unit_staffing[unit] = {
            'posts': [
                {
                    'num': p['num'], 'role': p['role'], 'affectation': p['nom'],
                    'metier': (bible_by_num.get(p['num']) or {}).get('metier'),
                    'valid_scan': p['valid'],
                }
                for p in posts
            ],
            'accueil': {
                'count_op': len(a_op_posts),
                'count_chef': len(a_chef_posts),
                'agents_h_total': round(ah_total, 1),
                'peak_simu': peak_simu,
                'peak_simu_ts': peak_simu_ts,
                'hourly': {date: dict(hours) for date, hours in hourly.items()},
            },
            'securite': {
                'count_op': len(s_op_posts),
                'count_chef': len(s_chef_posts),
                'affectations': [p['nom'] for p in s_op_posts],
            },
            'generated_at': datetime.utcnow(),
        }

    # Upsert dans parking_scans + porte_scans
    updates_parking = updates_porte = 0
    for unit, st in unit_staffing.items():
        # Tentative dans parking_scans
        res = db['parking_scans'].update_one(
            {'event': SCAN_EVENT, 'year': SCAN_YEAR, 'zone': unit},
            {'$set': {'staffing': st}},
        )
        if res.matched_count:
            updates_parking += 1
            continue
        res = db['porte_scans'].update_one(
            {'event': SCAN_EVENT, 'year': SCAN_YEAR, 'porte': unit},
            {'$set': {'staffing': st}},
        )
        if res.matched_count:
            updates_porte += 1
        else:
            print(f'  !! unite "{unit}" introuvable dans parking_scans ni porte_scans')

    print(f'\nStaffing injecte : {updates_parking} zones + {updates_porte} portes')

    # Verification
    sample = db['parking_scans'].find_one({'event': SCAN_EVENT, 'year': SCAN_YEAR,
                                            'staffing.accueil.peak_simu': {'$gt': 0}})
    if sample:
        s = sample['staffing']
        print(f'\nExemple ({sample["zone"]}):')
        print(f'  Accueil : {s["accueil"]["count_op"]} postes operationnels '
              f'(chef={s["accueil"]["count_chef"]}) | {s["accueil"]["agents_h_total"]} agents.h '
              f'| pic simu {s["accueil"]["peak_simu"]} le {s["accueil"]["peak_simu_ts"]}')
        print(f'  Securite : {s["securite"]["count_op"]} postes')


if __name__ == '__main__':
    main()
