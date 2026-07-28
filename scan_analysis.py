"""
Analyse redigee des scans par Claude.

Complete l'analyse par regles du rapport HTML (`generate_parking_report.
build_analysis`, qui produit des phrases a partir de seuils) par un compte
rendu operationnel redige : ce qui a sature, ou, quand, et ce qu'il faudrait
changer.

L'appel passe par `pcorg_summary.call_claude` plutot que par le SDK anthropic :
cette fonction porte deja le retry exponentiel sur 429/503/529, le retry sur
troncature, le prompt caching et la telemetrie d'usage. Ajouter le SDK
dupliquerait une integration eprouvee et une dependance de plus.

On n'envoie JAMAIS les creneaux bruts (45 000 enregistrements pour une
edition) : uniquement des KPI agreges, ce qui tient dans quelques milliers de
tokens et garde la reponse focalisee.
"""

import logging
from collections import defaultdict
from datetime import datetime

import pcorg_summary

logger = logging.getLogger(__name__)

ANALYSES_COLLECTION = 'scan_analyses'
DEFAULT_MODEL = 'claude-sonnet-5'

# Nombre d'unites detaillees dans le prompt. Au-dela, le modele se disperse et
# le cout grimpe sans gain : les unites sont classees par volume et seules les
# plus significatives sont detaillees, les autres restent dans les totaux.
MAX_UNITS_DETAILED = 25

ClaudeError = pcorg_summary.ClaudeError


# ---------------------------------------------------------------------------
# Agregation des KPI
# ---------------------------------------------------------------------------

def _unit_kpis(unit):
    """Pics et fenetres de saturation d'une unite, depuis ses creneaux 15 min."""
    series = unit.get('data_15min') or []
    if not series:
        return None

    by_hour = defaultdict(lambda: {'e': 0, 's': 0})
    by_day = defaultdict(lambda: {'e': 0, 's': 0})
    peak_present, peak_present_ts = 0, None
    peak_flow, peak_flow_ts = 0, None

    for rec in series:
        date = rec.get('date') or ''
        hour, day = date[:13], date[:10]
        e = int(rec.get('entree') or 0)
        s = int(rec.get('sortie') or 0)
        by_hour[hour]['e'] += e
        by_hour[hour]['s'] += s
        by_day[day]['e'] += e
        by_day[day]['s'] += s
        present = int(rec.get('present') or 0)
        if present > peak_present:
            peak_present, peak_present_ts = present, date
        if e + s > peak_flow:
            peak_flow, peak_flow_ts = e + s, date

    hourly_flow = {h: v['e'] + v['s'] for h, v in by_hour.items()}
    peak_hour = max(hourly_flow.items(), key=lambda kv: kv[1]) if hourly_flow else (None, 0)
    busiest_day = max(by_day.items(), key=lambda kv: kv[1]['e']) if by_day else (None, {'e': 0})

    return {
        'nom': unit.get('name'),
        'type': 'porte' if unit.get('kind') == 'porte' else 'zone',
        'localisee': bool(unit.get('_id_feature')),
        'entrees': int(unit.get('total_entree') or 0),
        'sorties': int(unit.get('total_sortie') or 0),
        'sans_direction': int(unit.get('total_autre') or 0),
        'devices': int(unit.get('device_count') or 0),
        'pic_presents': peak_present,
        'pic_presents_a': peak_present_ts,
        'pic_flux_15min': peak_flow,
        'pic_flux_15min_a': peak_flow_ts,
        'pic_horaire': peak_hour[1],
        'pic_horaire_a': peak_hour[0],
        'jour_le_plus_charge': busiest_day[0],
        'entrees_ce_jour': busiest_day[1]['e'],
        'renfort_pda': (unit.get('pda_renfort') or {}).get('total_scans'),
        'aide_uam': (unit.get('uam_help') or {}).get('total_scans'),
        'effectif_pic': ((unit.get('staffing') or {}).get('accueil') or {}).get('peak_simu'),
        'agents_h': ((unit.get('staffing') or {}).get('accueil') or {}).get('agents_h_total'),
    }


def compute_kpis(db, event, year):
    """KPI agreges du document `complet`, prets a etre mis dans le prompt."""
    doc = db['historique_controle'].find_one(
        {'event': event, 'year': int(year), 'type': 'complet'})
    if not doc:
        raise ClaudeError('Aucun document complet pour %s %s' % (event, year))

    units = [u for u in (doc.get('complet') or [])]
    kpis = [k for k in (_unit_kpis(u) for u in units) if k]
    kpis.sort(key=lambda k: -(k['entrees'] + k['sorties']))

    portes = [k for k in kpis if k['type'] == 'porte']
    zones = [k for k in kpis if k['type'] == 'zone']

    total_e = sum(k['entrees'] for k in kpis)
    total_s = sum(k['sorties'] for k in kpis)
    total_a = sum(k['sans_direction'] for k in kpis)

    # Presents de l'enceinte : la serie globale du document frequentation.
    freq = db['historique_controle'].find_one(
        {'event': event, 'year': int(year), 'type': 'frequentation'})
    enceinte = None
    if freq and freq.get('data'):
        best = max(freq['data'], key=lambda r: int(r.get('present') or 0))
        enceinte = {
            'pic_presents': int(best.get('present') or 0),
            'pic_presents_a': best.get('date'),
            'entrees_cumul': int(freq['data'][-1].get('entree') or 0),
            'sorties_cumul': int(freq['data'][-1].get('sortie') or 0),
            'scans_sans_direction_exclus': freq.get('excluded_autre'),
            'portes_sans_direction': freq.get('doors_without_direction') or [],
        }

    return {
        'event': event,
        'year': int(year),
        'source': 'scan_import',
        'race': doc.get('race'),
        'periode': {'debut': doc.get('period_start'), 'fin': doc.get('period_end')},
        'creneaux': doc.get('slot_count'),
        'totaux': {'entrees': total_e, 'sorties': total_s,
                   'sans_direction': total_a},
        'enceinte': enceinte,
        'nb_zones': len(zones), 'nb_portes': len(portes),
        'portes': portes[:MAX_UNITS_DETAILED],
        'zones': zones[:MAX_UNITS_DETAILED],
        'non_localisees': [k['nom'] for k in kpis if not k['localisee']],
        'tronque': len(portes) > MAX_UNITS_DETAILED or len(zones) > MAX_UNITS_DETAILED,
    }


def compute_comparison(db, event, year):
    """Comparaison a l'edition precedente disponible, ou None."""
    year = int(year)
    for prev in range(year - 1, year - 4, -1):
        doc = db['historique_controle'].find_one(
            {'event': event, 'year': prev, 'type': 'frequentation'})
        if not doc or not doc.get('data'):
            continue
        best = max(doc['data'], key=lambda r: int(r.get('present') or 0))
        return {
            'annee': prev,
            'pic_presents': int(best.get('present') or 0),
            'pic_presents_a': best.get('date'),
            'entrees_cumul': int(doc['data'][-1].get('entree') or 0),
            # Provenance : un document issu de l'import Excel et un document
            # issu de la collecte temps reel ne couvrent pas le meme perimetre
            # de portes. Les comparer sans le dire produirait une « baisse de
            # frequentation » qui n'est qu'un artefact de mesure.
            'source': doc.get('source') or 'collecte_temps_reel',
        }
    return None


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Tu es analyste d'exploitation pour l'Automobile Club de l'Ouest.
Tu rediges le compte rendu des flux de spectateurs d'une edition, a partir des
scans de billets releves aux portes et dans les zones du circuit.

Tu ecris pour des responsables d'exploitation qui prepareront l'edition
suivante. Ils connaissent le site : pas besoin d'expliquer ce qu'est une porte
ou une tribune. Ce qu'ils veulent savoir : ou ca a sature, quand, pourquoi, et
ce qu'il faut changer.

Regles de fond :
- Chiffre tes affirmations. « PORTE NORD a sature » ne vaut rien sans le pic et
  l'heure. Reprends les valeurs fournies telles quelles, ne les recalcule pas.
- Les horodatages sont en heure locale de Paris.
- Un scan « sans direction » vient d'un boitier non configure en entree/sortie.
  Il compte dans le volume de passage d'une porte mais ne permet aucun calcul
  de presents. Ne traite jamais une porte qui n'a que ces scans comme une porte
  sans frequentation.
- Les unites listees comme non localisees n'ont pas d'entite cartographique.
  Beaucoup sont des services (helpdesk, litige, UAM), pas des lieux : ne les
  commente pas comme des acces.
- Un « renfort PDA » sur une porte a tripodes signale un debordement des
  tourniquets. C'est un signal d'exploitation fort, releve-le.
- N'invente aucune donnee absente. Si un element manque pour conclure, dis-le
  plutot que de supposer.
- ATTENTION aux comparaisons entre editions : le champ "source" indique d'ou
  vient chaque jeu. "scan_import" vient d'un export Excel de billetterie,
  "collecte_temps_reel" de la remontee terrain. Ces deux sources ne couvrent
  pas le meme perimetre de portes. Si les sources different entre l'edition
  analysee et la precedente, tu DOIS le dire explicitement et presenter tout
  ecart comme potentiellement lie au changement de mesure, jamais comme une
  variation de frequentation averee.

Reponds UNIQUEMENT par un objet JSON valide, sans texte autour, avec
exactement ces sept cles, toutes en francais :
- "synthese" : 4 a 6 phrases. Volume total, pic de presents avec sa date et son
  heure, tendance vs edition precedente si fournie.
- "pics_et_saturation" : les moments de tension, dates et heures a l'appui.
- "portes_critiques" : les acces qui ont concentre la charge ou montre un
  debordement. Une puce par porte, avec les chiffres.
- "zones_critiques" : idem pour les zones (tribunes, parkings, aires d'accueil).
- "comparaison_n1" : evolution vs l'edition precedente. Si aucune donnee
  comparable n'est fournie, ecris exactement "Aucune edition precedente
  comparable dans les donnees fournies."
- "anomalies" : trous de mesure, portes sans direction, unites non localisees,
  ecarts suspects. C'est la section ou l'on dit ce en quoi il ne faut pas avoir
  confiance.
- "recommandations" : actions concretes pour l'edition suivante, classees par
  priorite. Pas de generalites.

Chaque valeur est une chaine. Pour les listes, utilise des puces separees par
des retours a la ligne commencant par "- "."""


def build_prompts(kpis, comparison=None):
    """Assemble (system, user). Le system est stable donc mis en cache."""
    import json
    payload = dict(kpis)
    payload['edition_precedente'] = comparison
    user = (
        "Voici les donnees de scans a analyser.\n\n"
        "```json\n" + json.dumps(payload, ensure_ascii=False, indent=1) + "\n```\n\n"
        "Redige le compte rendu au format JSON demande."
    )
    return SYSTEM_PROMPT, user


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def generate_scan_analysis(db, event, year, model=None, created_by=None,
                           dry_run=False, on_progress=None):
    """Genere l'analyse et la persiste. `dry_run` retourne le prompt sans appel.

    Retourne le document enregistre (ou le prompt assemble en dry run).
    """
    year = int(year)
    if on_progress:
        on_progress(10, 'Agregation des indicateurs')
    kpis = compute_kpis(db, event, year)
    comparison = compute_comparison(db, event, year)
    system, user = build_prompts(kpis, comparison)

    if dry_run:
        return {'dry_run': True, 'system': system, 'user': user,
                'kpis': kpis, 'comparison': comparison,
                'chars': len(system) + len(user)}

    if on_progress:
        on_progress(30, 'Appel du modele')
    use_model = pcorg_summary._validate_model(model) or DEFAULT_MODEL
    sections, raw_text, usage = pcorg_summary.call_claude(
        system, user, model=use_model,
        on_progress=(lambda pct, msg: on_progress(30 + int(pct * 0.6), msg))
        if on_progress else None,
    )

    if on_progress:
        on_progress(95, 'Enregistrement')
    doc = {
        'event': event, 'year': year,
        'created_at': datetime.now(), 'created_by': created_by,
        'model': use_model,
        'sections': sections,
        'raw_text': None if sections else raw_text,
        'usage': usage,
        'kpis': kpis,
        'comparison': comparison,
    }
    res = db[ANALYSES_COLLECTION].insert_one(doc)
    doc['_id'] = res.inserted_id
    ensure_indexes(db)
    return doc


def ensure_indexes(db):
    try:
        db[ANALYSES_COLLECTION].create_index(
            [('event', 1), ('year', -1), ('created_at', -1)])
    except Exception:
        logger.warning('Index scan_analyses non cree', exc_info=True)


def list_analyses(db, event=None, year=None, limit=25):
    """Liste allegee (sans kpis ni sections) pour l'historique."""
    q = {}
    if event:
        q['event'] = event
    if year:
        q['year'] = int(year)
    out = []
    for d in db[ANALYSES_COLLECTION].find(
            q, {'kpis': 0, 'sections': 0, 'raw_text': 0}).sort(
            'created_at', -1).limit(int(limit)):
        d['_id'] = str(d['_id'])
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = d['created_at'].isoformat(timespec='seconds')
        out.append(d)
    return out


def get_analysis(db, analysis_id):
    from bson import ObjectId
    try:
        oid = ObjectId(analysis_id)
    except Exception:
        return None
    d = db[ANALYSES_COLLECTION].find_one({'_id': oid})
    if not d:
        return None
    d['_id'] = str(d['_id'])
    if isinstance(d.get('created_at'), datetime):
        d['created_at'] = d['created_at'].isoformat(timespec='seconds')
    return d
