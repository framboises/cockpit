"""
Edition du mapping apres import, sans reprendre le classeur.

Le rattachement geographique, la categorie et l'exclusion d'une unite se
decidaient jusqu'ici uniquement pendant l'import : les corriger imposait de
reteleverser le xlsx. Or le document `complet` porte deja tout ce qu'il faut
— `_id_feature`, `feature_collection`, `category`, et la serie 15 min de
chaque unite. Les trois documents sont donc reconstructibles depuis lui.

Equivalence verifiee sur 24H MOTOS 2024, chiffre par chiffre :
  - `frequentation` recalcule depuis `complet` : 151 enregistrements,
    0 different, cumul final 131 975 / 107 259 identique
  - `portes` reconstruit : 13 portes, memes noms, memes `doors_id`,
    280 516 scans de part et d'autre

⚠ Ne fonctionne que pour les couples ayant un document `complet`. Ceux qui
tombent encore sur l'ancienne chaine (`parking_scans`) doivent etre importes
une fois. C'est le cas de 24H AUTOS 2025.

Editions suivies par le controle d'acces live : les unites viennent de
l'archive HSH (pseudo `complet` en memoire, live_scan_units). Une correction
n'y reecrit AUCUN document : elle est seulement memorisee dans
scan_feature_overrides (apply_mapping_live), puis le rapport est regenere.
"""

import logging
import uuid
from collections import defaultdict
from datetime import datetime

import scan_import

logger = logging.getLogger(__name__)


class MappingError(Exception):
    """Erreur exploitable par la route, avec un code stable."""

    def __init__(self, code, status=400, detail=None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


SOURCE_LIVE = 'live_controle'


def _units_doc(db, event, year):
    """(document d'unites, source) : archive live d'abord, `complet` sinon.

    Une edition suivie par le controle d'acces live n'a pas besoin d'import :
    ses unites sont construites en memoire depuis l'archive HSH
    (scan_report_build.resolve_units_doc). Seules les editions sans archive
    ni import levent `complet_absent`.
    """
    import scan_report_build
    doc, source = scan_report_build.resolve_units_doc(db, event, year)
    if not doc:
        raise MappingError(
            'complet_absent', 404,
            'Aucun document complet pour %s %s : importer le classeur une '
            'fois pour pouvoir editer le mapping.' % (event, year))
    return doc, source


def _complet_doc(db, event, year):
    doc = db['historique_controle'].find_one(
        {'event': event, 'year': int(year), 'type': 'complet'})
    if not doc:
        raise MappingError(
            'complet_absent', 404,
            'Aucun document complet pour %s %s : importer le classeur une '
            'fois pour pouvoir editer le mapping.' % (event, year))
    return doc


def _as_datetime(raw):
    """Datetime naif depuis la valeur stockee (str ISO ou datetime BSON)."""
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

def load_mapping(db, event, year, doc=None, source=None):
    """Unites du document `complet`, avec candidats et suggestions.

    Meme forme que la reponse d'`import/analyze`, pour que l'UI puisse
    reutiliser la table de mapping telle quelle. Pour une edition live, les
    unites viennent de l'archive du controle d'acces (`live: true`).
    """
    if doc is None:
        doc, source = _units_doc(db, event, year)
    geo = scan_import._load_geo_index(db)

    units = []
    for unit in doc.get('complet') or []:
        pseudo = {'name': unit.get('name'), 'kind': unit.get('kind')}
        candidates = scan_import._candidates_for(pseudo, geo)
        entry = {
            'name': unit.get('name'),
            'kind': unit.get('kind'),
            'zone': unit.get('zone'),
            'total_entree': int(unit.get('total_entree') or 0),
            'total_sortie': int(unit.get('total_sortie') or 0),
            'total_autre': int(unit.get('total_autre') or 0),
            '_id_feature': unit.get('_id_feature'),
            'feature_collection': unit.get('feature_collection'),
            'feature_source': unit.get('feature_source') or 'aucun',
            'no_location': unit.get('feature_source') == 'sans_lieu',
            'feature_label': None,
            'category': unit.get('category') or scan_import.guess_category(
                unit.get('name'), unit.get('kind'),
                unit.get('feature_collection')),
            'category_source': unit.get('category_source') or 'auto',
            # Les unites ecartees restent dans `complet` avec leurs series :
            # l'exclusion est donc reversible depuis cette meme table.
            'ignored': bool(unit.get('ignored')),
            'candidates': [{'collection': c, '_id_feature': f, 'label': l}
                           for c, f, l in candidates],
        }
        for coll, fid, label in candidates:
            if fid == entry['_id_feature']:
                entry['feature_label'] = label
                break
        entry['suggestions'] = ([] if entry['_id_feature'] else
                                scan_import._suggest_for(
                                    pseudo, geo,
                                    exclude_ids={c['_id_feature']
                                                 for c in entry['candidates']}))
        units.append(entry)

    return {
        'event': event,
        'year': int(year),
        'units': units,
        'categories': scan_import.UNIT_CATEGORIES,
        'source_file': doc.get('source_file'),
        'imported_at': str(doc.get('imported_at') or ''),
        'race': doc.get('race'),
        'source': source or 'complet',
        'live': source == SOURCE_LIVE,
        'source_label': doc.get('source_label'),
    }


# ---------------------------------------------------------------------------
# Reconstruction des documents derives
# ---------------------------------------------------------------------------

def _hourly_from_complet(unit):
    """Serie 15 min d'une unite -> creneaux horaires {e, s, a}."""
    buckets = defaultdict(lambda: {'e': 0, 's': 0, 'a': 0})
    for rec in unit.get('data_15min') or []:
        ts = _as_datetime(rec.get('date'))
        if ts is None:
            continue
        hour = ts.replace(minute=0, second=0, microsecond=0)
        buckets[hour]['e'] += int(rec.get('entree') or 0)
        buckets[hour]['s'] += int(rec.get('sortie') or 0)
        buckets[hour]['a'] += int(rec.get('autre') or 0)
    return sorted(buckets.items())


def retained(units):
    """Unites reellement prises en compte : tout sauf les ecartees."""
    return [u for u in units if not u.get('ignored')]


def rebuild_portes_doc(units, event, year, race_iso=None):
    """Document `portes` reconstruit depuis les unites `complet` retenues.

    `scan_count` compte TOUS les scans de l'heure, sens confondus — meme
    semantique que `scan_import.build_portes_doc`.
    """
    doors = []
    for unit in retained(units):
        if unit.get('kind') != 'porte':
            continue
        scans = []
        for hour, counts in _hourly_from_complet(unit):
            total = counts['e'] + counts['s'] + counts['a']
            if not total:
                continue
            scans.append({'id': str(uuid.uuid4()), 'timestamp': hour,
                          'scan_count': total})
        entry = {'name': unit.get('name'), 'scans': scans}
        if unit.get('_id_feature'):
            entry['doors_id'] = unit['_id_feature']
        doors.append(entry)
    return {
        'event': event, 'year': int(year), 'type': 'portes',
        'race': race_iso, 'doors': doors,
    }


def rebuild_frequentation_doc(units, event, year, race_iso=None,
                              ignored_doors=None):
    """Document `frequentation` reconstruit depuis les unites `complet`.

    `entree`/`sortie` sont CUMULES et `present = entree - sortie`, comme
    l'original : les lecteurs (pcorg_summary, app.py) sont cables dessus.
    `autre` reste exclu, sans direction il fausserait le solde.
    """
    hourly = defaultdict(lambda: {'e': 0, 's': 0})
    excluded_autre = 0
    doors_without_direction = []
    for unit in retained(units):
        if unit.get('kind') != 'porte':
            continue
        autre = int(unit.get('total_autre') or 0)
        excluded_autre += autre
        total = (int(unit.get('total_entree') or 0)
                 + int(unit.get('total_sortie') or 0) + autre)
        if total and autre / float(total) > 0.9:
            doors_without_direction.append(unit.get('name'))
        for hour, counts in _hourly_from_complet(unit):
            hourly[hour]['e'] += counts['e']
            hourly[hour]['s'] += counts['s']

    data = []
    cum_e = cum_s = 0
    for hour in sorted(hourly):
        cum_e += hourly[hour]['e']
        cum_s += hourly[hour]['s']
        data.append({
            'id': str(uuid.uuid4()),
            'date': hour.isoformat(),
            'entree': cum_e,
            'sortie': cum_s,
            'present': cum_e - cum_s,
        })

    return {
        'event': event, 'year': int(year), 'type': 'frequentation',
        'race': race_iso, 'data': data, 'source': 'scan_import',
        'excluded_autre': excluded_autre,
        'doors_without_direction': doors_without_direction,
        'ignored_doors': sorted(ignored_doors or []),
    }


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

def apply_mapping(db, event, year, changes, applied_by=None,
                  save_overrides=False):
    """Applique les changements et reecrit les trois documents.

    `changes` : {"<kind>|<name>": {_id_feature, feature_collection, category,
    ignored}}. Seules les cles presentes sont modifiees.

    Les documents remplaces sont archives, comme a l'import : une correction
    de mapping reste annulable.
    """
    year = int(year)
    doc, source = _units_doc(db, event, year)
    if source == SOURCE_LIVE:
        return apply_mapping_live(db, event, year, changes, doc,
                                  applied_by=applied_by,
                                  save_overrides=save_overrides)
    units = doc.get('complet') or []
    touched, now_ignored, now_restored, overrides = _apply_changes(
        units, changes)
    return _rewrite_documents(db, event, year, doc, units, touched,
                              now_ignored, now_restored, overrides,
                              applied_by, save_overrides)


def _apply_changes(units, changes):
    """Applique `changes` aux unites (en place).

    Retourne (touched, now_ignored, now_restored, overrides) ; `overrides`
    porte, par (kind, name), les seuls champs modifies.
    """
    changes = changes or {}
    touched = 0
    now_ignored, now_restored = [], []
    overrides = {}
    for unit in units:
        key = '%s|%s' % (unit.get('kind'), unit.get('name'))
        ch = changes.get(key)
        if not ch:
            continue

        changed = False

        # « Aucune collection » : cette unite n'est pas un lieu. Decision
        # explicite, distincte d'un rattachement encore a faire.
        want_nolocation = bool(ch.get('no_location'))
        if want_nolocation != (unit.get('feature_source') == 'sans_lieu'):
            if want_nolocation:
                unit['_id_feature'] = None
                unit['feature_collection'] = None
                unit['feature_source'] = 'sans_lieu'
            else:
                unit['feature_source'] = 'aucun'
            overrides.setdefault((unit.get('kind'), unit.get('name')), {})[
                'no_location'] = want_nolocation
            changed = True

        want_ignored = bool(ch.get('ignored'))
        if want_ignored != bool(unit.get('ignored')):
            unit['ignored'] = want_ignored
            (now_ignored if want_ignored else now_restored).append(
                unit.get('name'))
            overrides.setdefault((unit.get('kind'), unit.get('name')), {})[
                'ignored'] = want_ignored
            changed = True

        if (not want_nolocation and ch.get('_id_feature')
                and ch['_id_feature'] != unit.get('_id_feature')):
            unit['_id_feature'] = ch['_id_feature']
            unit['feature_collection'] = ch.get('feature_collection')
            unit['feature_source'] = 'manuel'
            ov = overrides.setdefault((unit.get('kind'), unit.get('name')), {})
            ov['_id_feature'] = unit['_id_feature']
            ov['feature_collection'] = unit.get('feature_collection')
            changed = True

        cat = ch.get('category')
        if cat in scan_import.CATEGORY_IDS and cat != unit.get('category'):
            unit['category'] = cat
            unit['category_source'] = 'manuel'
            overrides.setdefault((unit.get('kind'), unit.get('name')), {})[
                'category'] = cat
            changed = True

        if changed:
            touched += 1
    return touched, now_ignored, now_restored, overrides


def _rewrite_documents(db, event, year, doc, units, touched, now_ignored,
                       now_restored, overrides, applied_by, save_overrides):
    """Reecrit complet / portes / frequentation (editions importees)."""
    if not touched:
        return {'changed': 0, 'units': len(retained(units)),
                'ignored': [], 'restored': [], 'rewritten': []}
    if not retained(units):
        raise MappingError(
            'toutes_les_unites_ignorees', 400,
            'Toutes les unites seraient ecartees : le rapport serait vide.')

    for val in overrides.values():
        val['save'] = save_overrides

    race = doc.get('race')
    ignored_doors = [u.get('name') for u in units
                     if u.get('ignored') and u.get('kind') == 'porte']

    new_complet = dict(doc)
    new_complet.pop('_id', None)
    new_complet['complet'] = units
    new_complet['mapping_edited_at'] = datetime.now()
    new_complet['mapping_edited_by'] = applied_by

    docs = [
        new_complet,
        rebuild_portes_doc(units, event, year, race),
        rebuild_frequentation_doc(units, event, year, race,
                                  ignored_doors=ignored_doors),
    ]

    run_id = uuid.uuid4().hex
    rewritten = []
    for d in docs:
        scan_import.archive_and_replace(db, d, archived_by=applied_by,
                                        import_id=run_id,
                                        reason='edition_mapping')
        rewritten.append(d['type'])

    saved = 0
    if save_overrides and overrides:
        try:
            saved = scan_import.save_overrides(db, overrides,
                                               created_by=applied_by)
        except Exception:
            logger.warning('Persistance des choix de mapping impossible',
                           exc_info=True)

    freq = docs[2]['data']
    return {
        'changed': touched,
        'units': len(retained(units)),
        'ignored': sorted(now_ignored),
        'restored': sorted(now_restored),
        'rewritten': rewritten,
        'overrides_saved': saved,
        'enceinte_entree': freq[-1]['entree'] if freq else 0,
    }


def _full_state_override(unit):
    """Etat complet d'une unite, au format de scan_import.save_overrides.

    Un override CIBLE (event, year) remplace entierement l'override global
    du meme nom a la resolution (scan_import._load_overrides) : n'y mettre
    que le champ modifie effacerait, pour cette edition, un rattachement ou
    une categorie memorises globalement. On y ecrit donc tout ce qui a ete
    decide a la main.
    """
    out = {'ignored': bool(unit.get('ignored')),
           'no_location': unit.get('feature_source') == 'sans_lieu'}
    if unit.get('feature_source') == 'manuel' and unit.get('_id_feature'):
        out['_id_feature'] = unit['_id_feature']
        out['feature_collection'] = unit.get('feature_collection')
    if unit.get('category_source') == 'manuel':
        out['category'] = unit.get('category')
    return out


def apply_mapping_live(db, event, year, changes, doc, applied_by=None,
                       save_overrides=False):
    """Edition live : les choix ne vont QUE dans scan_feature_overrides.

    Les unites d'une edition live sont reconstruites en memoire depuis
    l'archive du controle d'acces a chaque generation : il n'y a aucun
    document a reecrire, et surtout rien a ecrire dans historique_controle
    (reference N-1 de la TV, de la montre et des projections). Le choix
    << memoriser pour les prochains imports >> decide de la portee :
    coche, override global (tous evenements et imports) ; decoche, override
    cible sur ce couple (event, year).
    """
    year = int(year)
    units = doc.get('complet') or []
    touched, now_ignored, now_restored, _ = _apply_changes(units, changes)
    if not touched:
        return {'changed': 0, 'units': len(retained(units)), 'ignored': [],
                'restored': [], 'rewritten': [], 'live': True}
    if not retained(units):
        raise MappingError(
            'toutes_les_unites_ignorees', 400,
            'Toutes les unites seraient ecartees : le rapport serait vide.')

    touched_keys = {k for k in (changes or {})}
    overrides = {}
    for unit in units:
        key = '%s|%s' % (unit.get('kind'), unit.get('name'))
        if key not in touched_keys:
            continue
        val = _full_state_override(unit)
        val['save'] = True
        if not save_overrides:
            val['event'] = event
            val['year'] = year
        overrides[(unit.get('kind'), unit.get('name'))] = val
    try:
        scan_import.ensure_indexes(db)
    except Exception:
        logger.warning('Creation des index scan_import impossible', exc_info=True)
    saved = scan_import.save_overrides(db, overrides, created_by=applied_by)

    enceinte = sum(int(u.get('total_entree') or 0) for u in retained(units)
                   if u.get('kind') == 'porte')
    return {
        'changed': touched,
        'units': len(retained(units)),
        'ignored': sorted(now_ignored),
        'restored': sorted(now_restored),
        'rewritten': [],
        'overrides_saved': saved,
        'overrides_scope': 'global' if save_overrides else 'edition',
        'live': True,
        'enceinte_entree': enceinte,
    }
