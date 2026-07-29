"""
Generation du rapport HTML depuis les documents `historique_controle`.

Adaptateur entre le nouveau document `type: "complet"` (issu de l'import
Excel) et le contrat d'entree du gabarit HTML existant. Les fonctions
`serialize_zone` / `serialize_porte` de generate_parking_report.py sont
appelees telles quelles : le gabarit (2 140 lignes) et toutes les fonctions
d'analyse restent intacts.

Deux sources possibles, dans cet ordre :
  1. `historique_controle{type: "complet"}` — nouvelle chaine d'import
  2. `parking_scans` / `porte_scans` — ancienne chaine

Le repli garantit que le rapport historique reste reproductible a l'identique
tant qu'aucun `complet` n'a ete importe pour ce couple.

Les effectifs ne viennent d'aucune de ces deux sources : ils sont derives du
calendrier en fin de generation par `scan_staffing.attach_to_payload`, pour
les deux chemins.
"""

import logging
import os
import re
import unicodedata
from datetime import datetime

import generate_parking_report as gpr

logger = logging.getLogger(__name__)

REPORT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_SUBDIR = 'reports'


def slugify_event(name):
    """'24H MOTOS' -> '24h_motos' : nom de fichier stable et lisible."""
    txt = unicodedata.normalize('NFKD', str(name or ''))
    txt = txt.encode('ascii', 'ignore').decode('ascii').lower()
    txt = re.sub(r'[^a-z0-9]+', '_', txt).strip('_')
    return txt or 'inconnu'


def report_filename(event, year):
    return 'parking_report_%s_%s.html' % (slugify_event(event), year)


def report_path(event, year):
    return os.path.join(REPORT_DIR, REPORTS_SUBDIR, report_filename(event, year))


# ---------------------------------------------------------------------------
# Adaptation complet -> contrat du gabarit
# ---------------------------------------------------------------------------

def _intervals(unit):
    """`data_15min` -> `intervals` attendu par serialize_zone.

    Le gabarit ne connait que deux series (entree / sortie) : les scans sans
    direction n'y ont pas de place et sont laisses de cote ici. Ils restent
    dans le document `complet` et sont comptabilises a part par l'appelant.
    """
    out = []
    for rec in unit.get('data_15min') or []:
        raw = rec.get('date')
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        out.append({
            'ts': ts,
            'entree': int(rec.get('entree') or 0),
            'sortie': int(rec.get('sortie') or 0),
        })
    out.sort(key=lambda x: x['ts'])
    return out


def _legacy_enrichment(db, event, year):
    """Effectifs et renforts de l'ancienne chaine, indexes par nom.

    Un import Excel seul ne peut pas les produire : ils viennent des scripts
    d'enrichissement. On les greffe quand ils existent pour ce couple plutot
    que de faire disparaitre les panneaux du rapport.

    ⚠ `parking_scans` / `porte_scans` sont indexees sur le SLUG de l'ancienne
    chaine (`24h_du_mans`), pas sur le nom cockpit (`24H AUTOS`). Interroger
    avec le nom cockpit ne remonte rien et fait disparaitre silencieusement
    tous les effectifs du rapport.
    """
    by_name = {}
    slug = _legacy_slug(event)
    events = {event, slug}
    try:
        for coll, key in (('parking_scans', 'zone'), ('porte_scans', 'porte')):
            for doc in db[coll].find({'event': {'$in': sorted(events)},
                                      'year': int(year)}):
                name = (doc.get(key) or '').strip().upper()
                if not name:
                    continue
                by_name[name] = {
                    'staffing': doc.get('staffing'),
                    'uam_help': doc.get('uam_help'),
                    'pda_renfort': doc.get('pda_renfort'),
                }
    except Exception:
        logger.warning('Lecture des effectifs heritee impossible', exc_info=True)
    return by_name


def build_payload_from_complet(db, event, year, progress_cb=None,
                               legacy_event=None, legacy_year=None):
    """Construit le payload du gabarit depuis le document `complet`.

    Retourne (payload, info) ou `info` porte ce qui a ete ecarte, pour que
    l'UI puisse le dire plutot que de laisser un trou silencieux.
    """
    def _p(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)

    _p(8, 'Lecture du document complet')
    doc = db['historique_controle'].find_one(
        {'event': event, 'year': int(year), 'type': 'complet'})
    if not doc:
        raise gpr.ReportGenerationError(
            'Aucun document complet pour %s/%s' % (event, year))

    units = doc.get('complet') or []
    if not units:
        raise gpr.ReportGenerationError(
            'Le document complet de %s/%s ne contient aucune unite' % (event, year))

    enrichment = _legacy_enrichment(db, legacy_event or event,
                                    legacy_year or int(year))
    tripodes_flag = gpr.load_tripodes_flag(db)

    zones, portes = [], []
    omitted = []
    # Le gabarit n'affiche que les entrees et les sorties : AUCUN scan sans
    # direction n'y apparait, qu'il vienne d'une unite retenue ou ecartee.
    # Compter seulement ceux des unites ecartees sous-estimerait fortement ce
    # qui manque au rapport (21 893 au lieu de 82 203 sur 24H MOTOS 2024).
    autre_not_shown = sum(int(u.get('total_autre') or 0) for u in units)

    total = len(units) or 1
    for i, unit in enumerate(units):
        pct = 12 + int(70.0 * i / total)
        _p(pct, 'Analyse de %s' % unit.get('name'))

        intervals = _intervals(unit)
        flow = sum(it['entree'] + it['sortie'] for it in intervals)
        if not flow:
            # Sans aucun flux dirige, l'unite ne produirait qu'un onglet vide.
            omitted.append(unit.get('name'))
            continue

        name = unit.get('name')
        extra = enrichment.get((name or '').strip().upper(), {})
        pseudo = {
            'zone': name,
            'total_entree': int(unit.get('total_entree') or 0),
            'total_sortie': int(unit.get('total_sortie') or 0),
            'intervals': intervals,
            # Pas de staffing ici : il est calcule en fin de generation, par
            # `scan_staffing.attach_to_payload`. Reprendre celui du document
            # ferait survivre le resultat de l'ancienne chaine a validation
            # manuelle, dont les compteurs a zero se lisent comme « personne
            # n'etait en poste ».
        }

        if unit.get('kind') == 'porte':
            pseudo['porte'] = name
            pseudo['zone'] = unit.get('zone') or name
            pseudo['device_count'] = int(unit.get('device_count') or 0)
            pseudo['pda_count'] = int(unit.get('pda_count') or 0)
            pseudo['tripode_count'] = int(unit.get('tripode_count') or 0)
            # L'aide UAM et les renforts sont calcules a l'import et portes
            # par le document complet ; le repli sur l'ancienne chaine ne sert
            # qu'aux couples importes avant cette mise en place.
            pseudo['uam_help'] = unit.get('uam_help') or extra.get('uam_help')
            pseudo['pda_renfort'] = unit.get('pda_renfort') or extra.get('pda_renfort')
            out = gpr.serialize_porte(
                pseudo,
                tripodes_mode=tripodes_flag.get((name or '').upper(), False))
            portes.append(out)
        else:
            out = gpr.serialize_zone(pseudo)
            zones.append(out)
        # Rattachement geographique conserve jusqu'au payload : c'est par lui
        # que les effectifs sont calcules, sans repasser par le nom.
        out['_id_feature'] = unit.get('_id_feature')
        # Categorie choisie a l'import. `category` est deja pris dans le
        # contrat du gabarit ('zone' / 'porte'), d'ou le nom distinct.
        out['zone_category'] = unit.get('category')

    if not zones and not portes:
        raise gpr.ReportGenerationError(
            'Aucune unite exploitable pour %s/%s (aucun flux dirige)' % (event, year))

    zones.sort(key=lambda z: z.get('name') or '')
    portes.sort(key=lambda p: p.get('name') or '')

    _p(88, 'Assemblage du rapport')
    payload = {
        'event': event,
        'year': int(year),
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'zones': zones,
        'portes': portes,
    }
    info = {
        'source': 'complet',
        'zones': len(zones),
        'portes': len(portes),
        'omitted_units': omitted,
        'autre_scans_not_shown': autre_not_shown,
    }
    return payload, info


def _build_frequentation(db, event, year, info, with_analysis=True,
                         created_by=None, progress_cb=None):
    """Bloc frequentation, ou None. Un echec ici ne doit pas perdre le rapport."""
    import scan_frequentation
    try:
        block = scan_frequentation.build_frequentation_block(db, event, year)
    except Exception:
        logger.warning('Bloc frequentation indisponible pour %s %s',
                       event, year, exc_info=True)
        return None
    if not block:
        info['frequentation_editions'] = []
        return None

    info['frequentation_editions'] = [e['year'] for e in block['editions']]
    info['entries_comparable'] = block.get('entries_comparable')

    # L'analyse est EMBARQUEE ici et pas appelee a l'ouverture : le rapport est
    # un fichier autonome, sans aucun appel reseau. Un appel par regeneration,
    # jamais un par lecture — et zero appel si les donnees n'ont pas bouge.
    if with_analysis:
        import scan_analysis
        # Seule etape qui peut durer : tout le reste tient sous la seconde.
        # Le dire dans le libelle evite de lire l'attente comme un blocage.
        if progress_cb:
            progress_cb(88, 'Analyse redigee (appel au modele, 30 a 90 s)')
        try:
            doc = scan_analysis.generate_frequentation_analysis(
                db, event, year, block, created_by=created_by)
        except Exception:
            logger.warning('Analyse frequentation echouee pour %s %s',
                           event, year, exc_info=True)
            doc = None
        if doc and doc.get('sections'):
            block['analysis'] = {
                'sections': doc['sections'],
                'model': doc.get('model'),
                'created_at': str(doc.get('created_at') or ''),
            }
            info['frequentation_analysis'] = (
                'reutilisee' if doc.get('reused') else 'generee')
        else:
            info['frequentation_analysis'] = 'absente'
    return block


def generate(db, event, year, progress_cb=None, with_analysis=True,
             created_by=None):
    """Genere le rapport pour (event, year) et retourne un descriptif.

    Utilise le document `complet` s'il existe, sinon retombe sur l'ancienne
    chaine `parking_scans` / `porte_scans` via l'alias d'evenement — c'est ce
    qui garde 24H AUTOS 2025 reproductible.
    """
    def _p(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)

    year = int(year)
    try:
        payload, info = build_payload_from_complet(db, event, year, progress_cb)
    except gpr.ReportGenerationError:
        _p(10, 'Pas de document complet, repli sur l\'ancienne chaine')
        slug = _legacy_slug(event)
        payload = gpr.build_payload_legacy(db, slug, year, progress_cb)
        payload['event'] = event
        info = {'source': 'legacy', 'zones': len(payload['zones']),
                'portes': len(payload['portes']), 'omitted_units': [],
                'autre_scans_not_shown': 0}

    # Effectifs : calcules ici, pour les deux chemins de generation. Derives du
    # calendrier via `_id_feature`, sans aucune saisie — un rapport regenere
    # reflete donc toujours le dernier planning en base.
    _p(84, 'Effectifs depuis le calendrier')
    import scan_staffing
    try:
        info['staffing'] = scan_staffing.attach_to_payload(db, event, year, payload)
    except Exception:
        logger.warning('Calcul des effectifs echoue pour %s %s',
                       event, year, exc_info=True)
        info['staffing'] = {'error': 'effectifs_indisponibles'}

    # Bloc frequentation : ajoute ici et pas dans les deux constructeurs, parce
    # que c'est le seul endroit ou le NOM COCKPIT de l'evenement est connu a
    # coup sur. Le chemin de repli travaille sur le slug historique
    # (`24h_du_mans`), avec lequel historique_controle ne trouverait rien.
    _p(86, 'Frequentation et comparaison aux editions passees')
    payload['frequentation'] = _build_frequentation(
        db, event, year, info, with_analysis=with_analysis,
        created_by=created_by, progress_cb=progress_cb)

    _p(92, 'Rendu HTML')
    html = gpr.render_html(payload)

    out_path = report_path(event, year)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp_path = out_path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(html)
    os.replace(tmp_path, out_path)  # atomique : jamais de fichier a moitie ecrit

    info['path'] = out_path
    info['size_kb'] = round(os.path.getsize(out_path) / 1024.0, 1)
    _p(100, 'Rapport genere (%.0f Ko)' % info['size_kb'])
    return info


def _legacy_slug(event):
    """Nom d'evenement -> slug de l'ancienne chaine (parking_scans)."""
    try:
        from scan_report import EVENT_ALIASES
        return EVENT_ALIASES.get((event or '').strip().upper(), event)
    except Exception:
        return event
