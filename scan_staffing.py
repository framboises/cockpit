"""
Effectifs (Accueil / Securite) rattaches aux unites de scan.

Entierement derives, sans aucune saisie : la chaine de rattachement existe
deja en base et ne demande aucun arbitrage.

    unite de scan
      -> `_id_feature`            (pose a l'import, ou resolu par le nom)
      -> feature geo              (portes / tribunes / hospitalites / terrains)
      -> `post_numbers`           (numeros de poste travaillant sur ce lieu)
      -> `shiftcode`              (meme numero cote calendrier)
      -> calendrier_<annee>_<evenement>

Le calendrier porte `accueil_surete` ('A' ou 'S') et `donnees_presences`, une
liste de journees decoupees en creneaux de 30 min avec `nombre_personnes`.
Accueil et securite se calculent donc exactement de la meme facon : la seule
difference est la valeur de `accueil_surete`.

Remplace l'ancien parcours en deux temps (audit CSV -> validation a la main ->
application) qui passait par `bible` et un rapprochement par libelle. Ce
parcours ne comptait un poste que si la colonne Validation valait `OK` : sans
CSV valide, tous les effectifs tombaient a zero alors que la donnee etait la.

⚠ Les `post_numbers` poses sur les features ne sont PAS filtres par edition :
c'est une liste unique par lieu. Le filtrage se fait naturellement a la
jointure — un poste absent du calendrier de l'annee ne compte pas. Ne pas
« corriger » ce comportement, c'est lui qui rend la liste reutilisable d'une
edition a l'autre.
"""

import logging
import re
import unicodedata
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)

# Collections geographiques portant `_id_feature` et `post_numbers`.
GEO_COLLECTIONS = ('portes', 'tribunes', 'hospitalites', 'terrains')

# Un creneau de presence vaut 30 min.
SLOT_HOURS = 0.5

# Valeurs de `accueil_surete` cote calendrier.
ROLE_BY_FLAG = {'A': 'accueil', 'S': 'securite'}

# Drapeaux de `post_config`, qui dit qui fait quoi sur le lieu.
POST_CONFIG_FLAGS = ('access_control', 'palpation', 'placier', 'controle_tripode')


class StaffingSourcesMissing(Exception):
    """Calendrier absent, vide, ou dans un format non exploitable."""


def calendar_collection_name(event, year):
    """Nom de la collection calendrier pour un couple (evenement, annee).

    Convention observee en base : `calendrier_<annee>_<evenement colle et
    sans accent>` — '24H AUTOS' 2025 -> calendrier_2025_24hautos,
    'LE MANS CLASSIC' -> calendrier_2025_lemansclassic.
    """
    slug = unicodedata.normalize('NFKD', str(event or ''))
    slug = slug.encode('ascii', 'ignore').decode('ascii').lower()
    slug = re.sub(r'[^a-z0-9]+', '', slug)
    return 'calendrier_%s_%s' % (year, slug)


# ---------------------------------------------------------------------------
# Chargement des deux sources
# ---------------------------------------------------------------------------

def load_feature_posts(db):
    """`_id_feature` -> {name, collection, post_numbers, post_config, tripodes}.

    Balaye les quatre collections geo. Une feature sans `post_numbers` est
    conservee : elle permet de distinguer « lieu inconnu » de « lieu connu mais
    aucun poste rattache », deux situations qui ne se corrigent pas pareil.
    """
    out = {}
    for coll in GEO_COLLECTIONS:
        try:
            doc = db[coll].find_one() or {}
        except Exception:
            logger.warning('Collection geo %s illisible', coll, exc_info=True)
            continue
        for feat in doc.get('features') or []:
            props = feat.get('properties') or {}
            fid = props.get('_id_feature')
            if not fid:
                continue
            nums = []
            for n in props.get('post_numbers') or []:
                try:
                    nums.append(int(n))
                except (TypeError, ValueError):
                    continue
            place = props.get('place_config') or {}
            out[str(fid)] = {
                'name': props.get('Name'),
                'collection': coll,
                'post_numbers': nums,
                'post_config': props.get('post_config') or {},
                'tripodes': bool(place.get('tripodes')),
            }
    return out


def load_calendar(db, event, year):
    """`shiftcode` -> document calendrier, pour un couple (evenement, annee).

    Leve StaffingSourcesMissing si la collection n'existe pas, ou si elle est
    dans l'ancien format (colonnes Excel brutes `'10h - 10h30'`, pas de
    `shiftcode` ni de `donnees_presences`) : mieux vaut le dire que rendre des
    effectifs vides qui se liraient comme « personne n'etait la ».
    """
    name = calendar_collection_name(event, year)
    try:
        exists = name in db.list_collection_names()
    except Exception:
        logger.warning('Liste des collections illisible', exc_info=True)
        exists = True
    if not exists:
        raise StaffingSourcesMissing(
            'Aucun calendrier %s pour %s %s' % (name, event, year))

    out = {}
    for d in db[name].find({}, {'shiftcode': 1, 'accueil_surete': 1,
                                'donnees_presences': 1, 'poste': 1, 'lot': 1,
                                'secteur': 1, 'zone': 1, 'departement': 1}):
        sc = d.get('shiftcode')
        if sc is None:
            continue
        try:
            out[int(sc)] = d
        except (TypeError, ValueError):
            continue
    if not out:
        raise StaffingSourcesMissing(
            'Le calendrier %s ne porte aucun shiftcode exploitable '
            '(ancien format Excel ?)' % name)
    return out


# ---------------------------------------------------------------------------
# Calcul
# ---------------------------------------------------------------------------

def _presence_curve(cal_docs):
    """Docs calendrier d'une meme categorie -> bloc d'effectif.

    - un creneau vaut 30 min, d'ou agents-h = somme(nombre_personnes) * 0.5
    - le pic est le maximum d'agents presents sur un meme creneau
    - la courbe horaire retient le MAXIMUM des deux demi-heures, pas leur
      somme : c'est un effectif present, pas un volume.
    """
    slots = defaultdict(int)
    for d in cal_docs:
        for jour in d.get('donnees_presences') or []:
            if not isinstance(jour, dict):
                continue
            date = jour.get('date')
            for slot in jour.get('plages_horaires') or []:
                nb = slot.get('nombre_personnes') or 0
                if nb:
                    slots[(date, slot.get('heure_debut'))] += nb

    peak, peak_ts = 0, None
    hourly = defaultdict(lambda: defaultdict(int))
    for (date, hd), val in slots.items():
        if val > peak:
            peak, peak_ts = val, '%s %s' % (date, hd)
        hour = (hd or '').split(':')[0]
        if hour:
            hourly[date][hour] = max(hourly[date][hour], val)

    return {
        'count_op': len(cal_docs),
        'agents_h_total': round(sum(slots.values()) * SLOT_HOURS, 1),
        'peak_simu': peak,
        'peak_simu_ts': peak_ts,
        'hourly': {d: dict(h) for d, h in hourly.items()},
    }


def _post_detail(num, cal_doc, post_config):
    """Descriptif d'un poste, cote calendrier et cote configuration du lieu."""
    cfg = post_config.get(str(num)) or post_config.get(num) or {}
    return {
        'num': num,
        'role': ROLE_BY_FLAG.get(cal_doc.get('accueil_surete')),
        'affectation': cal_doc.get('poste') or cal_doc.get('secteur'),
        'lot': cal_doc.get('lot'),
        'secteur': cal_doc.get('secteur'),
        'zone': cal_doc.get('zone'),
        'departement': cal_doc.get('departement'),
        'missions': [f for f in POST_CONFIG_FLAGS if cfg.get(f)],
    }


def compute_for_feature(feature, calendar):
    """Effectifs d'un lieu, ou None si rien n'est rattachable.

    Retourne (staffing, raison) — `raison` nomme le trou quand `staffing` est
    None, pour que l'appelant puisse le remonter au lieu d'afficher un zero
    qui se lirait comme une absence d'agents.
    """
    nums = feature.get('post_numbers') or []
    if not nums:
        return None, 'aucun_post_number'

    matched = [(n, calendar[n]) for n in nums if n in calendar]
    if not matched:
        return None, 'aucun_poste_au_calendrier'

    by_role = defaultdict(list)
    for _, doc in matched:
        role = ROLE_BY_FLAG.get(doc.get('accueil_surete'))
        if role:
            by_role[role].append(doc)

    post_config = feature.get('post_config') or {}
    staffing = {
        'accueil': _presence_curve(by_role.get('accueil') or []),
        'securite': _presence_curve(by_role.get('securite') or []),
        'posts': [_post_detail(n, d, post_config) for n, d in sorted(matched)],
        'posts_total': len(nums),
        'posts_matched': len(matched),
        'feature_name': feature.get('name'),
        'generated_at': datetime.now(),
    }
    return staffing, None


def compute_staffing(db, event, year, units):
    """Effectifs par unite de scan.

    `units` : iterable de dicts portant au moins `name` et `_id_feature`.
    Retourne (staffing_par_nom, diagnostic).
    """
    calendar = load_calendar(db, event, year)
    features = load_feature_posts(db)

    out, reasons = {}, {}
    for unit in units:
        name = (unit.get('name') or '').strip()
        fid = unit.get('_id_feature')
        if not name:
            continue
        if not fid:
            reasons[name] = 'aucune_feature'
            continue
        feature = features.get(str(fid))
        if not feature:
            reasons[name] = 'feature_introuvable'
            continue
        staffing, reason = compute_for_feature(feature, calendar)
        if staffing:
            out[name] = staffing
        else:
            reasons[name] = reason

    diagnostic = {
        'calendar': calendar_collection_name(event, year),
        'calendar_shiftcodes': len(calendar),
        'units_with_staffing': len(out),
        'units_without': reasons,
        'agents_h_accueil': round(
            sum(s['accueil']['agents_h_total'] for s in out.values()), 1),
        'agents_h_securite': round(
            sum(s['securite']['agents_h_total'] for s in out.values()), 1),
    }
    return out, diagnostic


# ---------------------------------------------------------------------------
# Rattachement aux unites d'un rapport
# ---------------------------------------------------------------------------

def resolve_units_for_names(db, names, kinds=None, event=None, year=None):
    """Noms d'unites -> `_id_feature`, pour les rapports sans document complet.

    L'ancienne chaine (`parking_scans` / `porte_scans`) ne porte pas de
    `_id_feature` : on rejoue le resolveur de l'import, qui connait deja les
    variantes orthographiques et les corrections manuelles persistees.
    """
    import scan_import
    kinds = kinds or {}
    units = [{'name': n, 'kind': kinds.get(n, 'zone')} for n in names]
    try:
        resolved = scan_import.resolve_features(db, units, event, year)
    except Exception:
        logger.warning('Resolution des features impossible', exc_info=True)
        return {}
    out = {}
    for unit in units:
        entry = resolved.get((unit['kind'], unit['name'])) or {}
        if entry.get('_id_feature'):
            out[unit['name']] = entry['_id_feature']
    return out


def attach_to_payload(db, event, year, payload):
    """Pose `staffing` sur les zones et portes d'un payload de rapport.

    Fonctionne pour les deux chemins de generation : les unites issues du
    document `complet` portent deja leur `_id_feature`, les autres passent par
    la resolution par nom. Retourne le diagnostic, ou None si les sources
    manquent — un rapport sans effectifs vaut mieux qu'un rapport perdu.
    """
    entries = []
    for cat, key in (('zones', 'zone'), ('portes', 'porte')):
        for item in payload.get(cat) or []:
            # Cette fonction est seule maitresse du champ : on efface d'abord
            # tout staffing herite (ancienne chaine `parking_scans`, ou reste
            # de l'etape de validation manuelle). Sans cela, une unite non
            # calculable garderait des compteurs a zero qui se lisent comme
            # une absence d'agents, alors que c'est le rattachement qui manque.
            item['staffing'] = None
            entries.append((item, 'porte' if key == 'porte' else 'zone'))
    if not entries:
        return None

    missing = [(item, kind) for item, kind in entries
               if not item.get('_id_feature')]
    if missing:
        kinds = {(item.get('name') or ''): kind for item, kind in missing}
        by_name = resolve_units_for_names(
            db, list(kinds), kinds=kinds, event=event, year=year)
        for item, _ in missing:
            fid = by_name.get(item.get('name'))
            if fid:
                item['_id_feature'] = fid

    units = [{'name': item.get('name'), '_id_feature': item.get('_id_feature')}
             for item, _ in entries]
    try:
        staffing, diagnostic = compute_staffing(db, event, year, units)
    except StaffingSourcesMissing as exc:
        logger.warning('Effectifs indisponibles pour %s %s : %s', event, year, exc)
        return {'error': str(exc)}

    import generate_parking_report as gpr
    applied = 0
    for item, _ in entries:
        st = staffing.get((item.get('name') or '').strip())
        if st:
            item['staffing'] = gpr._serialize_staffing(st)
            applied += 1
    diagnostic['units_applied'] = applied
    return diagnostic
