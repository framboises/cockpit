"""
Audit complet du mapping unite_scan -> features GeoJSON -> bible + calendrier.

Source d'autorite : `post_numbers` dans les features GeoJSON (portes, tribunes,
terrains, hospitalites). Pour chaque unite scan, on construit la liste des
features matchees puis on regroupe les post_numbers en Accueil (1000-5999) et
Securite (>=8000).

Enrichissements :
- Accueil : bible.post.number == post_num -> affectation + calendrier.shiftcode
            == post_num -> donnees_presences (presences 30 min)
- Securite : bible.post.number == post_num -> affectation (compte statique)
             (le calendrier securite ayant des numeros refaits, pas de lien
             temporel)

Fallback pour unites scan sans match geojson : regex sur bible.post.affectation.

Sortie : tableau d'audit en console + CSV.
"""

import os
import re
import csv
from collections import defaultdict, Counter
from pymongo import MongoClient

MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
DB_NAME = 'titan_dev'
BIBLE_EVENT = '24H AUTOS'
BIBLE_YEAR = 2025
PARAM_EVENT = '24H AUTOS'
PARAM_YEAR = '2025'
CSV_OUTPUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'staffing_mapping_audit.csv',
)


# Aliases manuels (geojson feature name -> scan unit name) pour les portes
PORTE_ALIASES = {
    'PORTAIL CONCIERGERIE': 'PORTE CONCIERGERIE',
    'PORTE KARTING VL': 'PORTE KARTING VEHICULES',
    'PORTE NORD VL': 'PORTE NORD VEHICULES',
    'PORTE MAISON-BLANCHE PIETONS': 'PORTE MAISON BLANCHE PIETONS',
    'PORTE MAISON-BLANCHE VL': 'PORTE MAISON BLANCHE VEHICULES',
    'PORTE PORSCHE': 'VIRAGE PORSCHE',
    'PORTE TERTRE ROUGE PIETONS': 'PORTE TERTRE ROUGE',
    'PORTE TERTRE ROUGE VL': 'PORTE TERTRE ROUGE',
    # Identites
    'PORTE ANNEXE': 'PORTE ANNEXE',
    'PORTE CHATEAU': 'PORTE CHATEAU',
    'PORTE CIK': 'PORTE CIK',
    'PORTE EST': 'PORTE EST',
    'PORTE GARAGE VERT': 'PORTE GARAGE VERT',
    'PORTE HUNAUDIERES': 'PORTE HUNAUDIERES',
    'PORTE KARTING PIETONS': 'PORTE KARTING PIETONS',
    'PORTE NORD BIS': 'PORTE NORD BIS',
    'PORTE NORD PIETONS': 'PORTE NORD PIETONS',
    'PORTE PANORAMA': 'PORTE PANORAMA',
    'PORTE RACCORDEMENT': 'PORTE RACCORDEMENT',
    'PORTE SUD': 'PORTE SUD',
}

# Mapping P<NOM> scan -> liste de patterns name (parametrages parkingsHoraires)
PARKING_MAPPING = {
    'P EXPO':     ['EXPO AUTOS'],
    'P MULSANNE': ['MULSANNE 1', 'MULSANNE 3'],
    'P ARNAGE':   ['ARNAGE', 'ARNAGE 3'],
    'P PANORAMA': ['PANORAMA', 'PANORAMIC'],
    'P OUEST':    ['OUEST'],
    'P M1':       ['M1'],
}

# Mapping AA<NOM> scan -> liste de patterns name (parametrages campingsHoraires)
AA_MAPPING = {
    'AA ARNAGE':     ['ARNAGE'],
    'AA BEAUSEJOUR': ['BEAUSEJOUR'],
    'AA CLOS FLEURI':['CLOS FLEURI'],
    'AA EPINETTES':  ['EPINETTES'],
    'AA HIPPODROME': ['HIPPODROME'],
    'AA MULSANNE':   ['MULSANNE 2'],  # camping uniquement
    'AA PRAIRIE':    ['PRAIRIE'],
}

# Regex pour filtrer les chefs de poste (non productifs cote scans)
CHEF_REGEX = re.compile(
    r'\bChef\s*(?:de|d[\'\u2019])\b|\bChef\s*Adjoint\b|\bResponsable\b|'
    r'\bChef\s*de\s*Secteur\b|\bChef\s*de\s*Zone\b|\bChef\s*de\s*Poste\b',
    re.IGNORECASE,
)


def is_chef(affectation):
    return bool(affectation and CHEF_REGEX.search(affectation))

# Regex bible.affectation fallback pour les unites sans match geojson
FALLBACK_PATTERNS = {
    'HELPDESK':          [r'\bHelpdesk\b'],
    'LITIGE':            [r'\bLitige\b'],
    'UAM':               [r'\bUAM\b', r"\bUnit[ée]\s*d['\u2019]?Appui\s*Mobile\b"],
    'PORTE GLAMPING':    [r'\bGlamping\b'],
    'PORTE HOUX 5':      [r'\bHoux\s*5\b'],
    'PORTE MUSEE':       [r'\bPorte\s*Mus[ée]e\b'],
    'PORTE NAVETTES VIP':[r'\bNavettes?\s*VIP\b'],
    'VIRAGE TERTRE ROUGE':[r'\bVirage\s*Tertre\b'],
    'PADDOCKS':          [r'\bAcc[èe]s\s*Working\s*Paddock\b', r'\bAcc[èe]s\s*Paddock\b',
                          r'\bGrand\s*Paddock\b'],
    'MAISON DES HUNAUDIERES': [r'\bMaison\s*des\s*Hunaudi[èe]res\b'],
    'VISITES MUSEE':     [r'\bVisites\s*Mus[ée]e\b'],
    'WELCOME':           [r'\bWelcome\b'],
}


def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]

    # --- 1. Charger les sources ---
    bible_by_num = {}
    for d in db['bible'].find({'event': BIBLE_EVENT, 'year': BIBLE_YEAR},
                               {'post.number':1, 'post.metier':1, 'post.affectation':1, 'post.zone':1}):
        post = d.get('post', {})
        num = post.get('number')
        if num:
            bible_by_num[num] = {'metier': post.get('metier'), 'affect': post.get('affectation'),
                                 'zone': post.get('zone')}
    print(f'Bible : {len(bible_by_num)} postes')

    cal_by_sc = {}
    for d in db['calendrier_2025_24hautos'].find({}, {'shiftcode':1,'accueil_surete':1,
                                                       'donnees_presences':1, 'zone':1,
                                                       'secteur':1, 'poste':1}):
        sc = d.get('shiftcode')
        if sc is not None:
            cal_by_sc[int(sc)] = d
    print(f'Calendrier : {len(cal_by_sc)} docs')

    # GeoJSON
    geos = {}
    for col_name in ['portes', 'tribunes', 'terrains', 'hospitalites']:
        doc = db[col_name].find_one() or {}
        geos[col_name] = {f.get('properties', {}).get('_id_feature'):
                          f.get('properties', {}) for f in doc.get('features', [])}
    print(f'Geo : portes={len(geos["portes"])} tribunes={len(geos["tribunes"])} '
          f'terrains={len(geos["terrains"])} hospitalites={len(geos["hospitalites"])}')

    # Parametrages : event/year
    param_doc = db['parametrages'].find_one({'event': PARAM_EVENT, 'year': PARAM_YEAR}) or {}
    pdata = param_doc.get('data', {}) or {}
    # parkingsHoraires et campingsHoraires : list de dicts avec id (=feature_id) + name
    parking_by_name = {}
    for p in pdata.get('parkingsHoraires', []) or []:
        nm = (p.get('name') or '').strip()
        fid = p.get('id')
        if nm and fid:
            parking_by_name.setdefault(nm.upper(), []).append(fid)
    camping_by_name = {}
    for c in pdata.get('campingsHoraires', []) or []:
        nm = (c.get('name') or '').strip()
        fid = c.get('id')
        if nm and fid:
            camping_by_name.setdefault(nm.upper(), []).append(fid)
    print(f'Parametrages : {len(parking_by_name)} parkings activated, {len(camping_by_name)} campings')

    # Listes scan units
    scan_portes = sorted(d['porte'] for d in db['porte_scans'].find(
        {'event':'24h_du_mans','year':2025}, {'porte':1,'_id':0}))
    scan_zones = sorted(d['zone'] for d in db['parking_scans'].find(
        {'event':'24h_du_mans','year':2025}, {'zone':1,'_id':0}))
    print(f'Scan units : {len(scan_portes)} portes + {len(scan_zones)} zones')

    # --- 2. Mapping unite -> liste post_numbers (via geojson principalement) ---
    unit_posts = defaultdict(set)         # unit -> {post_number, ...}
    unit_sources = defaultdict(list)      # unit -> [(geo_coll, feat_name)]

    # 2a. Portes : match par Name (avec aliases)
    portes_doc = db['portes'].find_one() or {}
    for f in portes_doc.get('features', []) or []:
        p = f.get('properties', {})
        name = (p.get('Name') or '').strip().upper()
        scan_name = PORTE_ALIASES.get(name)
        if not scan_name:
            continue
        for pn in p.get('post_numbers') or []:
            unit_posts[scan_name].add(pn)
        unit_sources[scan_name].append(('portes', name))

    # 2b. Tribunes : match ANCIEN 2025 -> TRIBUNE NN
    trib_doc = db['tribunes'].find_one() or {}
    for f in trib_doc.get('features', []) or []:
        p = f.get('properties', {})
        anc = p.get('ANCIEN 2025') or p.get('ANCIEN_2025')
        if not anc:
            continue
        try:
            num = int(str(anc).strip())
        except (ValueError, TypeError):
            continue
        scan_name = f'TRIBUNE {num:02d}'
        # cas speciaux : "03 BIS/TER" si NUMERO 2026 le mentionne
        nom = (p.get('NOM') or '').upper()
        if 'BIS' in nom or 'TER' in nom:
            if num == 3:
                scan_name = 'TRIBUNE 03 BIS/TER'
            elif num == 29:
                scan_name = 'TRIBUNE 29 BIS'
        for pn in p.get('post_numbers') or []:
            unit_posts[scan_name].add(pn)
        unit_sources[scan_name].append(('tribunes', f"{anc} {nom}"))

    # 2c. Parkings : via parametrages (parking name -> feature_id) -> terrains
    for scan_name, patterns in PARKING_MAPPING.items():
        for pat in patterns:
            for cal_name, fids in parking_by_name.items():
                if pat.upper() == cal_name:
                    for fid in fids:
                        feat = geos['terrains'].get(fid)
                        if not feat:
                            continue
                        for pn in feat.get('post_numbers') or []:
                            unit_posts[scan_name].add(pn)
                        unit_sources[scan_name].append(('terrains via parking', feat.get('Name')))

    # 2d. AA : via parametrages campings
    for scan_name, patterns in AA_MAPPING.items():
        for pat in patterns:
            for cal_name, fids in camping_by_name.items():
                if pat.upper() == cal_name:
                    for fid in fids:
                        feat = geos['terrains'].get(fid)
                        if not feat:
                            continue
                        for pn in feat.get('post_numbers') or []:
                            unit_posts[scan_name].add(pn)
                        unit_sources[scan_name].append(('terrains via camping', feat.get('Name')))

    # --- 3. Fallback regex bible.affectation pour unites sans match ---
    all_scan_units = set(scan_portes) | set(scan_zones)
    no_match_units = [u for u in all_scan_units if u not in unit_posts]
    fallback_compiled = {u: [re.compile(p, re.IGNORECASE) for p in pats]
                          for u, pats in FALLBACK_PATTERNS.items()}
    for unit in no_match_units:
        pats = fallback_compiled.get(unit)
        if not pats:
            continue
        for num, info in bible_by_num.items():
            aff = info.get('affect') or ''
            for p in pats:
                if p.search(aff):
                    unit_posts[unit].add(num)
                    unit_sources[unit].append(('bible-fallback', aff))
                    break

    # --- 4. Pour chaque unite : split par range + enrichir avec calendrier ---
    rows = []
    posts_csv = []  # detail post par post pour validation
    for unit in sorted(all_scan_units):
        posts = unit_posts.get(unit, set())
        a_nums = sorted(n for n in posts if n < 8000)
        s_nums = sorted(n for n in posts if n >= 8000)
        ah_a_op = 0      # agents-h operationnels (sans chef)
        ah_a_chef = 0    # agents-h chef
        peak_by_slot = defaultdict(int)
        cal_hits_a = 0
        a_op_posts = 0
        a_chef_posts = 0
        for n in a_nums:
            bi = bible_by_num.get(n)
            affect = bi.get('affect') if bi else None
            chef = is_chef(affect)
            metier = bi.get('metier') if bi else None
            cal = cal_by_sc.get(n)
            ah_for_this = 0
            if cal and cal.get('accueil_surete') == 'A':
                cal_hits_a += 1
                dp = cal.get('donnees_presences') or []
                for jour in dp:
                    if not isinstance(jour, dict): continue
                    date = jour.get('date')
                    for slot in jour.get('plages_horaires', []):
                        nb = slot.get('nombre_personnes', 0) or 0
                        if nb:
                            ah_for_this += nb
                            if not chef:
                                key = (date, slot.get('heure_debut'))
                                peak_by_slot[key] += nb
            if chef:
                ah_a_chef += ah_for_this
                a_chef_posts += 1
            else:
                ah_a_op += ah_for_this
                a_op_posts += 1
            posts_csv.append({
                'unite': unit,
                'role': 'A_chef' if chef else 'A_op',
                'post_num': n,
                'metier': metier or '?',
                'affectation': affect or '(absent bible)',
                'agents_h': ah_for_this,
            })
        peak_a = max(peak_by_slot.values()) if peak_by_slot else 0
        s_bible_hits = 0
        s_chef = 0
        s_op = 0
        for n in s_nums:
            bi = bible_by_num.get(n)
            affect = bi.get('affect') if bi else None
            metier = bi.get('metier') if bi else None
            chef = is_chef(affect)
            if bi and metier == 'SECURITE':
                s_bible_hits += 1
            if chef:
                s_chef += 1
            else:
                s_op += 1
            posts_csv.append({
                'unite': unit,
                'role': 'S_chef' if chef else 'S_op',
                'post_num': n,
                'metier': metier or '?',
                'affectation': affect or '(absent bible)',
                'agents_h': '',
            })
        rows.append({
            'unite': unit,
            'category': 'porte' if unit in scan_portes else 'zone',
            'sources': '; '.join(s for src, s in unit_sources.get(unit, []))[:120] or '-',
            'a_op': a_op_posts, 'a_chef': a_chef_posts,
            's_op': s_op, 's_chef': s_chef,
            'cal_hits_a': cal_hits_a, 'bible_hits_s': s_bible_hits,
            'agents_h_a_op': ah_a_op, 'agents_h_a_chef': ah_a_chef,
            'pic_simu_a_op': peak_a,
        })

    rows.sort(key=lambda r: -(r['agents_h_a_op']))

    # === TABLEAU SYNTHETIQUE ===
    print(f"\n{'Unite':<32} {'cat':<5} {'Aop':>3} {'Ach':>3} {'Sop':>3} {'Sch':>3} {'AHop':>7} {'AHch':>5} {'PicOp':>5}  sources")
    print('-' * 140)
    for r in rows:
        print(f"  {r['unite']:<30} {r['category']:<5} "
              f"{r['a_op']:>3} {r['a_chef']:>3} {r['s_op']:>3} {r['s_chef']:>3} "
              f"{r['agents_h_a_op']:>7} {r['agents_h_a_chef']:>5} {r['pic_simu_a_op']:>5}  "
              f"{r['sources'][:50]}")

    print(f"\nTotaux : Aop={sum(r['a_op'] for r in rows)} Achef={sum(r['a_chef'] for r in rows)} "
          f"Sop={sum(r['s_op'] for r in rows)} Schef={sum(r['s_chef'] for r in rows)}")
    print(f"Agents-h Accueil operationnels : {sum(r['agents_h_a_op'] for r in rows)} (chefs exclus)")
    print(f"Agents-h Accueil chefs (info)  : {sum(r['agents_h_a_chef'] for r in rows)}")

    # === TABLEAU DETAILLE : un poste par ligne ===
    print('\n\n' + '=' * 140)
    print('TABLEAU DETAILLE : un poste par ligne (validation)')
    print('=' * 140)
    posts_csv_sorted = sorted(posts_csv, key=lambda p: (p['unite'], p['role'], p['post_num']))
    cur_unit = None
    for p in posts_csv_sorted:
        if p['unite'] != cur_unit:
            print(f"\n--- {p['unite']} ---")
            cur_unit = p['unite']
        ah = f"AH={p['agents_h']}" if p['agents_h'] != '' else ''
        print(f"  [{p['role']:<6}] #{p['post_num']:<5} {p['metier']:<10} | {p['affectation']:<60} {ah}")

    # CSV simple pour validation (4 colonnes seulement)
    ROLE_LABEL = {
        'A_chef': 'Accueil - Chef',
        'A_op':   'Accueil - Operationnel',
        'S_chef': 'Securite - Chef',
        'S_op':   'Securite - Operationnel',
    }
    # Recharger les validations deja saisies (key = unite|num)
    existing_validations = {}
    if os.path.isfile(CSV_OUTPUT):
        try:
            with open(CSV_OUTPUT, 'r', encoding='utf-8') as f:
                r = csv.reader(f)
                header = next(r, None)
                # On suppose colonnes : unite, categorie, num, nom, validation
                for row in r:
                    if len(row) >= 5 and row[4].strip():
                        existing_validations[(row[0], str(row[2]).strip())] = row[4]
        except Exception:
            existing_validations = {}

    with open(CSV_OUTPUT, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([
            'Unite (scan)', 'Categorie', 'Numero affectation', 'Nom affectation',
            'Validation (OK = valide / NON = pas de scan/tripode)',
        ])
        for p in posts_csv_sorted:
            key = (p['unite'], str(p['post_num']))
            w.writerow([
                p['unite'],
                ROLE_LABEL.get(p['role'], p['role']),
                p['post_num'],
                p['affectation'],
                existing_validations.get(key, ''),
            ])
    print(f'\nCSV validation : {CSV_OUTPUT}')


if __name__ == '__main__':
    main()
