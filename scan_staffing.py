"""
Effectifs (Accueil / Securite) rattaches aux unites de scan.

Deux temps, parce que le rapprochement poste <-> unite de scan ne peut pas
etre entierement automatise :

  1. `build_audit_csv` produit le tableau de rapprochement (unite de scan ->
     numeros de poste -> bible -> calendrier) que l'utilisateur valide a la
     main, colonne « Validation » : OK, NON, ou PADDOCKS pour reaffecter.
  2. `apply_validated_csv` relit ce tableau annote, calcule les agents-heures,
     le pic simultane et la courbe horaire, puis pose le champ `staffing` sur
     les unites du document `complet`.

Difference avec import_staffing_to_scans.py, dont la logique de calcul est
reprise telle quelle : les validations sont persistees en base plutot que
relues depuis un fichier du poste de travail. Un serveur n'a pas le CSV de
l'utilisateur sous la main, et le `.numbers` d'origine n'est pas lisible
sans dependance supplementaire.
"""

import csv
import io
import logging
from collections import defaultdict
from datetime import datetime

import audit_staffing_mapping as audit

logger = logging.getLogger(__name__)

VALIDATIONS_COLLECTION = 'scan_staffing_validations'

CSV_HEADER = [
    'Unite (scan)', 'Categorie', 'Numero affectation', 'Nom affectation',
    'Validation (OK = valide / NON = pas de scan/tripode)',
]

StaffingSourcesMissing = audit.StaffingSourcesMissing


def categorize_role(categorie):
    """Libelle de categorie -> role court. Repris de import_staffing_to_scans."""
    c = (categorie or '').lower()
    if 'accueil' in c and 'chef' in c:
        return 'A_chef'
    if 'accueil' in c:
        return 'A_op'
    if ('securite' in c or 'sécurité' in c) and 'chef' in c:
        return 'S_chef'
    if 'securite' in c or 'sécurité' in c:
        return 'S_op'
    return None


# ---------------------------------------------------------------------------
# Etape 1 : produire le tableau a valider
# ---------------------------------------------------------------------------

def load_saved_validations(db, event, year):
    """Validations deja saisies : {(unite, num_str): 'OK'|'NON'|'PADDOCKS'}."""
    out = {}
    try:
        for d in db[VALIDATIONS_COLLECTION].find(
                {'event': event, 'year': int(year)}):
            out[(d.get('unite'), str(d.get('post_num')))] = d.get('validation')
    except Exception:
        logger.warning('Lecture des validations impossible', exc_info=True)
    return out


def save_validations(db, event, year, rows, saved_by=None):
    """Persiste les validations saisies, pour les repropser au prochain audit."""
    n = 0
    for unite, num, validation in rows:
        if not validation:
            continue
        db[VALIDATIONS_COLLECTION].update_one(
            {'event': event, 'year': int(year), 'unite': unite,
             'post_num': str(num)},
            {'$set': {'validation': validation, 'updated_at': datetime.now(),
                      'updated_by': saved_by}},
            upsert=True)
        n += 1
    return n


def build_audit_csv(db, event, year):
    """Genere le CSV d'audit, pre-rempli des validations deja connues.

    Retourne (contenu_csv, stats). Leve StaffingSourcesMissing si la bible ou
    le calendrier n'existent pas pour ce couple — c'est le cas de la plupart
    des editions anciennes, et il vaut mieux le dire que produire un tableau
    vide.
    """
    import tempfile, os as _os
    tmp = tempfile.NamedTemporaryFile(suffix='.csv', delete=False)
    tmp.close()
    try:
        audit.main(event, year, csv_output=tmp.name, db=db)
        with open(tmp.name, encoding='utf-8') as f:
            rows = list(csv.reader(f))
    finally:
        try:
            _os.remove(tmp.name)
        except OSError:
            pass

    if not rows:
        raise StaffingSourcesMissing('Audit vide pour %s %s' % (event, year))

    saved = load_saved_validations(db, event, year)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_HEADER)
    filled = 0
    for row in rows[1:]:
        if len(row) < 4:
            continue
        key = (row[0], str(row[2]).strip())
        val = saved.get(key) or (row[4] if len(row) > 4 else '')
        if val:
            filled += 1
        w.writerow([row[0], row[1], row[2], row[3], val])
    return out.getvalue(), {'rows': len(rows) - 1, 'prefilled': filled}


# ---------------------------------------------------------------------------
# Etape 2 : appliquer le tableau valide
# ---------------------------------------------------------------------------

def _parse_csv(content):
    """CSV annote -> [{unite, categorie, num, nom, validation}]."""
    if isinstance(content, bytes):
        for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
            try:
                content = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        raise ValueError('csv_vide')
    out = []
    for row in rows[1:]:
        if len(row) < 4 or not row[0].strip():
            continue
        try:
            num = int(str(row[2]).strip())
        except (TypeError, ValueError):
            continue
        out.append({
            'unite': row[0].strip(),
            'categorie': row[1].strip(),
            'num': num,
            'nom': row[3].strip(),
            'validation': (row[4].strip().upper() if len(row) > 4 else ''),
        })
    if not out:
        raise ValueError('csv_sans_ligne_exploitable')
    return out


def compute_staffing(db, event, year, validations):
    """Effectifs par unite, a partir du tableau valide.

    Calcul repris de import_staffing_to_scans.py :
      - un creneau de presence vaut 30 min, d'ou agents-h = somme(nb) * 0.5
      - le pic simultane est le maximum d'agents presents sur un meme creneau
      - la courbe horaire retient le MAXIMUM des deux demi-heures, pas la
        somme : c'est un effectif present, pas un volume.
    """
    cal_name = audit.calendar_collection_name(event, year)
    cal_by_sc = {}
    if cal_name in db.list_collection_names():
        for d in db[cal_name].find({}, {'shiftcode': 1, 'accueil_surete': 1,
                                        'donnees_presences': 1}):
            sc = d.get('shiftcode')
            if sc is not None:
                cal_by_sc[int(sc)] = d
    else:
        raise StaffingSourcesMissing(
            'Collection calendrier %s absente' % cal_name)

    bible_by_num = {}
    for d in db['bible'].find({'event': event, 'year': int(year)},
                              {'post.number': 1, 'post.metier': 1}):
        post = d.get('post') or {}
        if post.get('number'):
            bible_by_num[post['number']] = {'metier': post.get('metier')}

    # Une validation vide vaut NON ; PADDOCKS reaffecte le poste a l'unite
    # PADDOCKS (des postes y sont rattaches sans y etre nommes).
    by_unit = defaultdict(list)
    for v in validations:
        val = v['validation'] or 'NON'
        if val == 'PADDOCKS':
            target, valid = 'PADDOCKS', True
        elif val == 'OK':
            target, valid = v['unite'], True
        else:
            target, valid = v['unite'], False
        role = categorize_role(v['categorie'])
        if not role:
            continue
        by_unit[target].append({'num': v['num'], 'role': role,
                                'nom': v['nom'], 'valid': valid})

    unit_staffing = {}
    for unit, posts in by_unit.items():
        valid_posts = [p for p in posts if p['valid']]
        a_op = [p for p in valid_posts if p['role'] == 'A_op']
        a_chef = [p for p in posts if p['role'] == 'A_chef']
        s_op = [p for p in valid_posts if p['role'] == 'S_op']
        s_chef = [p for p in posts if p['role'] == 'S_chef']

        ah_total = 0.0
        slot_sums = defaultdict(int)
        for p in a_op:
            cal = cal_by_sc.get(p['num'])
            if not cal or cal.get('accueil_surete') != 'A':
                continue
            for jour in cal.get('donnees_presences') or []:
                if not isinstance(jour, dict):
                    continue
                date = jour.get('date')
                for slot in jour.get('plages_horaires', []) or []:
                    nb = slot.get('nombre_personnes') or 0
                    if nb:
                        slot_sums[(date, slot.get('heure_debut'))] += nb
                        ah_total += nb * 0.5

        peak, peak_ts = 0, None
        hourly = defaultdict(lambda: defaultdict(int))
        for (date, hd), val in slot_sums.items():
            if val > peak:
                peak, peak_ts = val, '%s %s' % (date, hd)
            hour = (hd or '').split(':')[0]
            if hour:
                hourly[date][hour] = max(hourly[date][hour], val)

        unit_staffing[unit] = {
            'posts': [{'num': p['num'], 'role': p['role'],
                       'affectation': p['nom'],
                       'metier': (bible_by_num.get(p['num']) or {}).get('metier'),
                       'valid_scan': p['valid']} for p in posts],
            'accueil': {
                'count_op': len(a_op), 'count_chef': len(a_chef),
                'agents_h_total': round(ah_total, 1),
                'peak_simu': peak, 'peak_simu_ts': peak_ts,
                'hourly': {d: dict(h) for d, h in hourly.items()},
            },
            'securite': {
                'count_op': len(s_op), 'count_chef': len(s_chef),
                'affectations': [p['nom'] for p in s_op],
            },
            'generated_at': datetime.now(),
        }
    return unit_staffing


def build_validation_rows(db, event, year):
    """Lignes de validation reconstituees depuis l'audit + les choix memorises.

    Permet de rejouer les effectifs sans redemander le CSV a l'utilisateur,
    des lors qu'il a valide ce couple au moins une fois.
    """
    content, _ = build_audit_csv(db, event, year)
    return _parse_csv(content)


def attach_staffing(db, event, year, staffing, saved_by=None):
    """Pose le `staffing` calcule sur les unites du document `complet`.

    Retourne la liste des unites effectivement rattachees.
    """
    doc = db['historique_controle'].find_one(
        {'event': event, 'year': int(year), 'type': 'complet'})
    if not doc:
        raise StaffingSourcesMissing(
            'Aucun document complet pour %s %s : importer le classeur d\'abord'
            % (event, year))

    by_upper = {k.strip().upper(): v for k, v in staffing.items()}
    applied = []
    for unit in doc.get('complet') or []:
        st = by_upper.get((unit.get('name') or '').strip().upper())
        if st:
            unit['staffing'] = st
            applied.append(unit['name'])

    db['historique_controle'].update_one(
        {'_id': doc['_id']},
        {'$set': {'complet': doc['complet'],
                  'staffing_applied_at': datetime.now(),
                  'staffing_applied_by': saved_by}})
    return applied


def apply_validated_csv(db, event, year, csv_content, saved_by=None):
    """Applique le CSV valide : calcule puis pose `staffing` sur `complet`.

    Retourne un descriptif de ce qui a ete rattache et de ce qui ne l'a pas
    ete — les unites du tableau qui n'existent pas dans le document sont
    nommees plutot que silencieusement ignorees.
    """
    year = int(year)
    validations = _parse_csv(csv_content)
    save_validations(db, event, year,
                     [(v['unite'], v['num'], v['validation']) for v in validations],
                     saved_by=saved_by)

    staffing = compute_staffing(db, event, year, validations)
    applied = attach_staffing(db, event, year, staffing, saved_by=saved_by)

    applied_upper = {a.strip().upper() for a in applied}
    unmatched = sorted(u for u in staffing
                       if u.strip().upper() not in applied_upper)
    return {
        'rows': len(validations),
        'validated_ok': sum(1 for v in validations if v['validation'] == 'OK'),
        'units_computed': len(staffing),
        'units_applied': applied,
        'units_unmatched': unmatched,
    }
