"""
Blueprint Cockpit : chaine scans, de l'import Excel a l'analyse redigee.

Transport HTTP uniquement — la logique metier vit dans les modules dedies :
    scan_import.py        parsing, resolution des features, ecriture Mongo
    scan_report_build.py  generation du rapport HTML
    scan_staffing.py      effectifs (audit CSV + application)
    scan_analysis.py      analyse redigee par Claude

Groupes de routes :
- page et service du rapport  : /scan-report, /static, /available
- import                      : /template.xlsx, /features, /import/*
- effectifs                   : /staffing/audit.csv, /staffing/apply
- generation                  : /generate, /generate/status
- analyse                     : /analysis/*

Toutes heritent de l'authentification admin via `before_request` et restent
protegees par CSRF (ce blueprint n'est PAS exempte, contrairement a field_bp
ou vision_admin_bp).

Deux registres module-globaux supposent UN SEUL PROCESS, meme hypothese que
analyse_ops.py:49 (vrai sous waitress) : `_STAGING` pour les classeurs en
attente entre analyze et commit, `_JOBS` pour les taches asynchrones.

Documentation complete : CLAUDE.md, section « Chaine scans ».
"""

import io
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime

from flask import Blueprint, request, render_template, send_file, abort, jsonify

logger = logging.getLogger(__name__)

scan_report_bp = Blueprint("scan_report", __name__)

REPORT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPORT_FILE = os.path.join(REPORT_DIR, 'parking_report.html')

# Dossier des rapports generes, un fichier par couple (evenement, annee).
# Le dossier fait foi : un rapport fraichement genere devient visible sans
# redemarrage ni edition de code (l'ancienne whitelist en dur imposait les deux).
REPORTS_DIR = os.path.join(REPORT_DIR, 'reports')

# Rapports produits avant la mise en place du dossier `reports/`, servis depuis
# la racine. A conserver tant que 24H AUTOS 2025 n'a pas ete regenere.
LEGACY_REPORTS = {
    ('24h_du_mans', '2025'): DEFAULT_REPORT_FILE,
}

# Nom d'evenement cockpit (collection `evenement`, expose par /get_events) ->
# slug de l'ancien pipeline d'import. Les scripts import_*.py ecrivent
# event='24h_du_mans' alors que la sidebar cockpit parle de '24H AUTOS'.
# Ne sert plus qu'au repli sur les rapports historiques.
EVENT_ALIASES = {
    '24H AUTOS': '24h_du_mans',
}

# Selection par defaut quand la page est ouverte sans query param (le JS de la
# sidebar redirige ensuite vers la selection memorisee en localStorage).
DEFAULT_EVENT = '24H AUTOS'
DEFAULT_YEAR = '2025'


def _canonical_event(event):
    """Nom d'evenement cockpit -> slug de l'ancien pipeline scans."""
    return EVENT_ALIASES.get((event or '').strip().upper(), event)


def _report_path(event, year):
    """Chemin du rapport pour (event, year), ou None s'il n'existe pas.

    Cherche d'abord le fichier genere par la nouvelle chaine, puis retombe sur
    la table des rapports historiques.
    """
    import scan_report_build
    path = scan_report_build.report_path(event, year)
    if os.path.isfile(path):
        return path
    legacy = LEGACY_REPORTS.get((_canonical_event(event), str(year)))
    if legacy and os.path.isfile(legacy):
        return legacy
    return None


def _list_reports():
    """Inventaire des rapports presents sur disque.

    Retourne [{event_slug, year, path, generated_at, size_kb}], le dossier
    `reports/` faisant autorite, complete par les rapports historiques.
    """
    import scan_report_build
    out = []
    seen = set()
    if os.path.isdir(REPORTS_DIR):
        for name in sorted(os.listdir(REPORTS_DIR)):
            m = re.match(r'^parking_report_(?P<slug>[a-z0-9_]+)_(?P<year>\d{4})\.html$',
                         name)
            if not m:
                continue
            path = os.path.join(REPORTS_DIR, name)
            out.append({
                'slug': m.group('slug'), 'year': m.group('year'), 'path': path,
                'generated_at': datetime.fromtimestamp(
                    os.path.getmtime(path)).isoformat(timespec='seconds'),
                'size_kb': round(os.path.getsize(path) / 1024.0, 1),
            })
            seen.add((m.group('slug'), m.group('year')))
    for (slug, year), path in LEGACY_REPORTS.items():
        if (slug, year) in seen or not os.path.isfile(path):
            continue
        out.append({
            'slug': slug, 'year': year, 'path': path,
            'generated_at': datetime.fromtimestamp(
                os.path.getmtime(path)).isoformat(timespec='seconds'),
            'size_kb': round(os.path.getsize(path) / 1024.0, 1),
        })
    return out


def _check_admin():
    """Meme logique que analyse_ops : reservation admin (avec bypass CODING)."""
    from app import (CODING, JWT_SECRET, JWT_ALGORITHM, ROLE_HIERARCHY,
                     ROLE_ORDER, APP_KEY)
    import jwt as pyjwt
    if CODING:
        sim_role = request.args.get("as", "admin")
        if sim_role not in ROLE_HIERARCHY:
            sim_role = "admin"
        sim_level = ROLE_HIERARCHY[sim_role]
        sim_roles = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= sim_level]
        request.user_payload = {
            "apps": ["cockpit"], "roles_by_app": {"cockpit": sim_role},
            "global_roles": [], "roles": sim_roles, "app_role": sim_role,
            "is_super_admin": False,
            "firstname": "Bruce", "lastname": "WAYNE",
            "email": "bruce@wayneenterprise.com",
        }
        return None
    token = request.cookies.get("access_token")
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    roles = payload.get("roles_by_app", {}).get(APP_KEY, "")
    if isinstance(roles, str):
        roles = [roles]
    max_level = max((ROLE_HIERARCHY.get(r, 0) for r in roles), default=0)
    if max_level < ROLE_HIERARCHY.get("admin", 3):
        return jsonify({"error": "Admin required"}), 403
    effective_role = "admin" if max_level >= ROLE_HIERARCHY.get("admin", 3) else (roles[0] if roles else "user")
    payload["roles"] = [r for r in ROLE_ORDER if ROLE_HIERARCHY.get(r, 0) <= ROLE_HIERARCHY.get(effective_role, 0)]
    payload["app_role"] = effective_role
    request.user_payload = payload
    return None


@scan_report_bp.before_request
def _before():
    err = _check_admin()
    if err:
        return err


def _resolve_event_year():
    """Retourne (event, year, file_path) ; file_path None si non supporte."""
    event = request.args.get('event') or DEFAULT_EVENT
    year = request.args.get('year') or DEFAULT_YEAR
    return event, year, _report_path(event, year)


@scan_report_bp.route('/scan-report')
def scan_report_page():
    payload = getattr(request, 'user_payload', {})
    event, year, file_path = _resolve_event_year()
    return render_template(
        'scan_report.html',
        event=event, year=year,
        report_available=bool(file_path and os.path.isfile(file_path)),
        user_roles=payload.get('roles', []),
        user_firstname=payload.get('firstname', ''),
        user_lastname=payload.get('lastname', ''),
        user_email=payload.get('email', ''),
    )


@scan_report_bp.route('/scan-report/static')
def scan_report_static():
    event, year, file_path = _resolve_event_year()
    if not file_path or not os.path.isfile(file_path):
        abort(404, description=f'Rapport non disponible pour {event}/{year}')
    return send_file(file_path, mimetype='text/html')


@scan_report_bp.route('/scan-report/available')
def scan_report_available():
    """Couples (event, year) dont le fichier HTML existe reellement sur disque.

    Renvoie les noms d'evenement tels qu'affiches dans la sidebar cockpit (via
    EVENT_ALIASES) pour que le JS puisse marquer les options correspondantes,
    plus le slug brut au cas ou aucun alias ne soit defini.
    """
    from app import db as app_db
    import scan_report_build

    # Le nom de fichier ne porte qu'un slug : on le rapproche des evenements
    # cockpit pour que le selecteur de la sidebar sache quoi cocher.
    by_slug = {}
    try:
        for doc in app_db['evenement'].find({}, {'_id': 0, 'nom': 1}):
            nom = doc.get('nom')
            if nom:
                by_slug.setdefault(scan_report_build.slugify_event(nom), []).append(nom)
    except Exception:
        logger.warning('Liste des evenements indisponible', exc_info=True)
    for name, slug in EVENT_ALIASES.items():
        by_slug.setdefault(slug, []).append(name)

    # Dedoublonnage sur le nom d'evenement resolu, pas sur le slug : un meme
    # couple peut exister sous deux slugs differents (`24h_autos` cote nouvelle
    # chaine, `24h_du_mans` cote historique). Le plus recent l'emporte, ce qui
    # correspond a ce que sert _report_path.
    best = {}
    for rep in _list_reports():
        for name in by_slug.get(rep['slug'], []) or [rep['slug']]:
            key = (name, rep['year'])
            if key not in best or rep['generated_at'] > best[key]['generated_at']:
                best[key] = {'event': name, 'year': rep['year'],
                             'generated_at': rep['generated_at'],
                             'size_kb': rep['size_kb']}
    out = sorted(best.values(), key=lambda r: (r['event'], r['year']))
    return jsonify(out)


# ===========================================================================
# Import de scans Excel
# ===========================================================================
#
# Deux temps : `analyze` parse le fichier et rend un apercu (unites, mapping,
# conflits) sans rien ecrire ; `commit` rejoue le mapping corrige par
# l'utilisateur et ecrit les trois documents.
#
# Le classeur parse est conserve en memoire entre les deux appels plutot que
# re-televerse : l'analyse coute ~3 s sur 1482 colonnes, et le resultat pese
# moins de 1 Mo. Ce registre module-global suppose UN SEUL PROCESS — c'est le
# cas sous waitress (app.py:27), meme hypothese que analyse_ops.py:49.
# Si l'app passait a gunicorn multi-workers, il faudrait basculer le staging
# dans une collection Mongo avec index TTL.

_STAGING = {}
_STAGING_LOCK = threading.Lock()
STAGING_TTL_SECONDS = 30 * 60
STAGING_MAX_ENTRIES = 3

# Un xlsx de scans complet pese ~700 Ko ; 25 Mo laisse de la marge sans
# permettre de saturer la memoire du process.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_UPLOAD_EXT = {'.xlsx', '.xlsm'}

# Ecart relatif au-dela duquel on exige une confirmation explicite : reecrire
# un document `frequentation` change les comparaisons N-1 de tout le cockpit
# (pcorg_summary._find_hist_freq, app.py:2067).
DELTA_CONFIRM_THRESHOLD = 0.10


def _err(code, status=400, **extra):
    payload = {'ok': False, 'error': code}
    payload.update(extra)
    return jsonify(payload), status


def _current_user_email():
    return (getattr(request, 'user_payload', {}) or {}).get('email')


def _drop_staged(token):
    """Retire une entree en attente ET son fichier ; _STAGING_LOCK deja pris.

    Sans la suppression du fichier, chaque import abandonne ou expire laissait
    un classeur de plusieurs centaines de Ko sur le disque indefiniment.
    """
    staged = _STAGING.pop(token, None)
    if staged and staged.get('path') and os.path.isfile(staged['path']):
        try:
            os.remove(staged['path'])
        except OSError:
            logger.warning('Fichier de staging non supprime : %s', staged['path'])
    return staged


def _sweep_staging_dir():
    """Supprime les classeurs en attente plus vieux que le TTL.

    Le registre `_STAGING` est en memoire : un redemarrage du process laisse
    ses fichiers orphelins, sans entree pour les balayer. Ce nettoyage se base
    sur la date du fichier et reste donc valable apres un redemarrage.
    """
    tmp_dir = os.path.join(REPORT_DIR, 'uploads', 'scan_imports')
    if not os.path.isdir(tmp_dir):
        return
    now = time.time()
    for name in os.listdir(tmp_dir):
        path = os.path.join(tmp_dir, name)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > STAGING_TTL_SECONDS:
                os.remove(path)
        except OSError:
            pass


def _sweep_staging():
    """Purge les entrees expirees ; suppose _STAGING_LOCK deja pris."""
    now = time.time()
    for token in [t for t, v in _STAGING.items()
                  if now - v['created_at'] > STAGING_TTL_SECONDS]:
        _drop_staged(token)
    while len(_STAGING) > STAGING_MAX_ENTRIES:
        oldest = min(_STAGING.items(), key=lambda kv: kv[1]['created_at'])[0]
        _drop_staged(oldest)
    _sweep_staging_dir()


def _existing_summary(db, event, year):
    """Ce qui existe deja en base pour ce couple, et l'ecart attendu.

    Sert a prevenir l'utilisateur AVANT l'ecriture : le document
    `frequentation` de 24H MOTOS 2024 par exemple provient d'un autre export
    et totalise 166 328 entrees la ou le xlsx en donne 131 975. L'ecraser sans
    le dire fausserait silencieusement toutes les comparaisons N-1.
    """
    out = {'types': [], 'frequentation_total_entree': None}
    for doc in db['historique_controle'].find(
            {'event': event, 'year': int(year),
             'type': {'$in': ['complet', 'frequentation', 'portes']}},
            {'type': 1, 'data': 1, 'source': 1, 'imported_at': 1}):
        entry = {'type': doc['type'], 'source': doc.get('source')}
        if doc.get('imported_at'):
            entry['imported_at'] = doc['imported_at'].isoformat(timespec='seconds')
        out['types'].append(entry)
        if doc['type'] == 'frequentation' and doc.get('data'):
            out['frequentation_total_entree'] = doc['data'][-1].get('entree')
    return out


@scan_report_bp.route('/scan-report/template.xlsx')
def scan_report_template():
    """Modele xlsx d'exemple, genere a la volee."""
    from app import db
    import scan_import
    try:
        buf = scan_import.build_template_workbook(db)
    except Exception as exc:
        logger.exception('Generation du modele xlsx impossible')
        return _err('template_indisponible', 500, detail=str(exc))
    return send_file(
        buf, as_attachment=True, download_name='modele_import_scans.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@scan_report_bp.route('/scan-report/features')
def scan_report_features():
    """Inventaire d'une collection geographique, pour le mapping manuel.

    Les collections font au plus 156 features : on renvoie tout d'un coup et
    le front filtre localement, ce qui evite une requete par frappe.
    """
    from app import db
    import scan_import
    coll = request.args.get('collection', 'portes')
    if coll not in scan_import.FEATURE_COLLECTIONS:
        return _err('collection_inconnue', 400)
    field = scan_import.FEATURE_NAME_FIELD[coll]
    doc = db[coll].find_one() or {}
    items = []
    for feat in doc.get('features') or []:
        props = feat.get('properties') or {}
        fid = props.get('_id_feature')
        if not fid:
            continue
        items.append({
            '_id_feature': fid,
            'label': str(props.get(field) or '').strip() or '(sans nom)',
            'detail': str(props.get('description') or props.get('TYPE') or '').strip(),
        })
    items.sort(key=lambda x: x['label'])
    return jsonify({'ok': True, 'collection': coll, 'items': items})


@scan_report_bp.route('/scan-report/import/analyze', methods=['POST'])
def scan_report_import_analyze():
    """Parse le classeur televerse et rend l'apercu, sans rien ecrire."""
    from app import db
    import scan_import

    upload = request.files.get('file')
    if upload is None or not upload.filename:
        return _err('fichier_manquant')
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        return _err('extension_invalide')

    raw = upload.read()
    if not raw:
        return _err('fichier_vide')
    if len(raw) > MAX_UPLOAD_BYTES:
        return _err('fichier_trop_volumineux', 413)

    tmp_dir = os.path.join(REPORT_DIR, 'uploads', 'scan_imports')
    os.makedirs(tmp_dir, exist_ok=True)
    token = uuid.uuid4().hex
    # Le fichier source est conserve : tracabilite de ce qui a ete importe.
    tmp_path = os.path.join(tmp_dir, token + ext)
    with open(tmp_path, 'wb') as fh:
        fh.write(raw)

    try:
        parsed = scan_import.parse_scan_xlsx(tmp_path)
    except scan_import.ScanImportError as exc:
        os.remove(tmp_path)
        return _err(exc.code, exc.status, detail=exc.detail)
    except Exception as exc:
        os.remove(tmp_path)
        logger.exception('Parsing du classeur de scans impossible')
        return _err('parsing_impossible', 400, detail=str(exc))

    event = (request.form.get('event') or DEFAULT_EVENT).strip()
    year = request.form.get('year') or scan_import.guess_year(parsed)
    try:
        year = int(year)
    except (TypeError, ValueError):
        os.remove(tmp_path)
        return _err('annee_invalide')

    resolved = scan_import.resolve_features(db, parsed['units'], event, year)
    race = scan_import.resolve_race(db, event, year)
    existing = _existing_summary(db, event, year)

    with _STAGING_LOCK:
        _sweep_staging()
        _STAGING[token] = {
            'created_at': time.time(), 'path': tmp_path, 'parsed': parsed,
            'event': event, 'year': year, 'user': _current_user_email(),
            # Le fichier est stocke sous un jeton, mais c'est le nom d'origine
            # qui doit apparaitre dans le document : sans lui on ne sait plus
            # quel export a produit les donnees.
            'filename': upload.filename,
        }
    parsed['source_file'] = upload.filename

    units = []
    for unit in parsed['units']:
        res = resolved[unit['key']]
        units.append({
            'name': unit['name'], 'kind': unit['kind'], 'zone': unit['zone'],
            'device_count': unit['device_count'],
            'total_entree': unit['total_entree'],
            'total_sortie': unit['total_sortie'],
            'total_autre': unit['total_autre'],
            '_id_feature': res['_id_feature'],
            'feature_collection': res['feature_collection'],
            'feature_source': res['feature_source'],
            'feature_label': res['feature_label'],
            'candidates': res['candidates'],
            'suggestions': res.get('suggestions', []),
        })

    totals = {
        'entree': sum(u['total_entree'] for u in parsed['units']),
        'sortie': sum(u['total_sortie'] for u in parsed['units']),
        'autre': sum(u['total_autre'] for u in parsed['units']),
    }
    eg_entree = sum(u['total_entree'] for u in parsed['units']
                    if u['kind'] == 'porte')

    delta_pct = None
    prev = existing.get('frequentation_total_entree')
    if prev:
        delta_pct = round((eg_entree - prev) * 100.0 / prev, 1)

    return jsonify({
        'ok': True,
        'token': token,
        'event': event,
        'year': year,
        'race': race,
        'summary': {
            'source_file': upload.filename,
            'period_start': parsed['period_start'].isoformat(timespec='minutes'),
            'period_end': parsed['period_end'].isoformat(timespec='minutes'),
            'slot_count': parsed['slot_count'],
            'zone_count': parsed['zone_count'],
            'porte_count': parsed['porte_count'],
            'has_autre': parsed['has_autre'],
            'totals': totals,
            'enceinte_entree': eg_entree,
        },
        'units': units,
        'unresolved_count': sum(1 for u in units if not u['_id_feature']),
        'existing': existing,
        'delta_pct': delta_pct,
        'needs_confirm': delta_pct is not None
                         and abs(delta_pct) > DELTA_CONFIRM_THRESHOLD * 100,
    })


@scan_report_bp.route('/scan-report/import/commit', methods=['POST'])
def scan_report_import_commit():
    """Ecrit les trois documents, en archivant l'existant au prealable."""
    from app import db
    import scan_import

    body = request.get_json(silent=True) or {}
    token = body.get('token')
    with _STAGING_LOCK:
        _sweep_staging()
        staged = _STAGING.get(token)
    if not staged:
        return _err('import_expire', 410)

    event = (body.get('event') or staged['event']).strip()
    try:
        year = int(body.get('year') or staged['year'])
    except (TypeError, ValueError):
        return _err('annee_invalide')
    race = body.get('race') or scan_import.resolve_race(db, event, year)

    # Mapping manuel : {"porte|PORTE NORD": {_id_feature, feature_collection, save}}
    overrides = {}
    for key, val in (body.get('overrides') or {}).items():
        if '|' not in key or not isinstance(val, dict):
            continue
        kind, name = key.split('|', 1)
        overrides[(kind, name)] = {
            '_id_feature': val.get('_id_feature') or None,
            'feature_collection': val.get('feature_collection'),
            'save': bool(val.get('save')),
        }

    parsed = staged['parsed']
    parsed['source_file'] = staged.get('filename') or parsed.get('source_file')
    resolved = scan_import.resolve_features(db, parsed['units'], event, year,
                                            overrides=overrides)
    user = _current_user_email()
    run_id = uuid.uuid4().hex
    now = datetime.now()

    # Aide UAM et renforts PDA : deduits du seul classeur, donc calculables
    # automatiquement a l'import (contrairement aux effectifs, qui demandent
    # une validation humaine).
    try:
        uam_by_porte = scan_import.compute_uam_help(
            parsed, scan_import.load_tripode_portes(db))
    except Exception:
        logger.warning('Calcul UAM/renforts impossible', exc_info=True)
        uam_by_porte = {}

    docs = [
        scan_import.build_complet_doc(parsed, resolved, event, year, race,
                                      imported_by=user, imported_at=now,
                                      uam_by_porte=uam_by_porte),
        scan_import.build_portes_doc(parsed, resolved, event, year, race),
        scan_import.build_frequentation_doc(parsed, event, year, race),
    ]

    try:
        scan_import.ensure_indexes(db)
    except Exception:
        logger.warning('Creation des index scan_import impossible', exc_info=True)

    written, archived = [], []
    try:
        for doc in docs:
            res = scan_import.archive_and_replace(db, doc, archived_by=user,
                                                  import_id=run_id)
            written.append(doc['type'])
            if res['archived']:
                archived.append({'type': doc['type'],
                                 'archive_id': str(res['archive_id'])})
    except scan_import.ScanImportError as exc:
        return _err(exc.code, exc.status, detail=exc.detail,
                    written=written, archived=archived)
    except Exception as exc:
        logger.exception('Ecriture des documents de scans impossible')
        # Les documents deja ecrits le restent : on les nomme pour que l'etat
        # soit reconstituable a la main plutot que devine.
        return _err('ecriture_partielle', 500, detail=str(exc),
                    written=written, archived=archived)

    saved = 0
    if body.get('save_overrides'):
        try:
            saved = scan_import.save_overrides(db, overrides, created_by=user)
        except Exception:
            logger.warning('Persistance des mappings impossible', exc_info=True)

    with _STAGING_LOCK:
        _drop_staged(token)

    # Effectifs : si des validations ont deja ete saisies pour ce couple, on
    # les rejoue tout de suite. L'utilisateur s'attend a ce que l'import
    # enrichisse tout ; le passage par l'etape manuelle n'est necessaire que
    # la premiere fois, quand aucune validation n'existe encore.
    staffing_info = _apply_saved_staffing(db, event, year, user)

    unresolved = [{'kind': k[0], 'name': k[1]}
                  for k, v in resolved.items() if not v['_id_feature']]
    return jsonify({
        'ok': True, 'run_id': run_id, 'event': event, 'year': year,
        'written': written, 'archived': archived,
        'overrides_saved': saved, 'unresolved': unresolved,
        'staffing': staffing_info,
    })


def _apply_saved_staffing(db, event, year, user):
    """Rejoue les validations d'effectifs deja memorisees, si elles existent.

    Retourne un descriptif pour l'UI : `status` vaut 'applique',
    'aucune_validation' (premier import de ce couple) ou 'sources_absentes'
    (edition sans bible ni calendrier).
    """
    import scan_staffing
    try:
        saved = scan_staffing.load_saved_validations(db, event, year)
        if not saved:
            return {'status': 'aucune_validation'}
        rows = scan_staffing.build_validation_rows(db, event, year)
        if not rows:
            return {'status': 'aucune_validation'}
        staffing = scan_staffing.compute_staffing(db, event, year, rows)
        applied = scan_staffing.attach_staffing(db, event, year, staffing, user)
        return {'status': 'applique', 'units': len(applied),
                'validations': len(saved)}
    except scan_staffing.StaffingSourcesMissing as exc:
        return {'status': 'sources_absentes', 'detail': str(exc)}
    except Exception as exc:
        logger.warning('Application automatique des effectifs impossible',
                       exc_info=True)
        return {'status': 'echec', 'detail': str(exc)}


@scan_report_bp.route('/scan-report/import/<token>', methods=['DELETE'])
def scan_report_import_abort(token):
    """Abandon : purge l'entree en attente et son fichier source."""
    with _STAGING_LOCK:
        _drop_staged(token)
    return jsonify({'ok': True})


# ===========================================================================
# Effectifs (2e temps de l'import)
# ===========================================================================
#
# Le rapprochement poste <-> unite de scan ne peut pas etre automatise : on
# produit un tableau a valider, l'utilisateur l'annote, on le rejoue. Les
# validations sont conservees en base pour ne pas refaire le travail a chaque
# import.

@scan_report_bp.route('/scan-report/staffing/audit.csv')
def scan_report_staffing_audit():
    """Tableau de rapprochement des effectifs, a valider a la main."""
    from app import db
    import scan_staffing

    event = (request.args.get('event') or DEFAULT_EVENT).strip()
    try:
        year = int(request.args.get('year') or DEFAULT_YEAR)
    except (TypeError, ValueError):
        return _err('annee_invalide')

    try:
        content, stats = scan_staffing.build_audit_csv(db, event, year)
    except scan_staffing.StaffingSourcesMissing as exc:
        # Cas frequent sur les editions anciennes : ni bible ni calendrier.
        return _err('sources_effectifs_absentes', 404, detail=str(exc))
    except Exception as exc:
        logger.exception('Audit des effectifs impossible')
        return _err('audit_impossible', 500, detail=str(exc))

    buf = io.BytesIO(content.encode('utf-8-sig'))  # BOM : Excel lit l'UTF-8
    name = 'effectifs_%s_%s.csv' % (
        re.sub(r'[^A-Za-z0-9]+', '_', event).strip('_').lower(), year)
    resp = send_file(buf, as_attachment=True, download_name=name,
                     mimetype='text/csv')
    resp.headers['X-Audit-Rows'] = str(stats['rows'])
    resp.headers['X-Audit-Prefilled'] = str(stats['prefilled'])
    return resp


@scan_report_bp.route('/scan-report/staffing/apply', methods=['POST'])
def scan_report_staffing_apply():
    """Applique le tableau valide sur les unites du document complet."""
    from app import db
    import scan_staffing

    upload = request.files.get('file')
    if upload is None or not upload.filename:
        return _err('fichier_manquant')
    if not upload.filename.lower().endswith('.csv'):
        # Volontairement CSV seul : le .numbers est un format de poste de
        # travail, illisible sans dependance supplementaire cote serveur.
        return _err('extension_invalide', 400,
                    detail='Exporter la feuille au format CSV')
    raw = upload.read()
    if not raw:
        return _err('fichier_vide')
    if len(raw) > MAX_UPLOAD_BYTES:
        return _err('fichier_trop_volumineux', 413)

    event = (request.form.get('event') or DEFAULT_EVENT).strip()
    try:
        year = int(request.form.get('year') or DEFAULT_YEAR)
    except (TypeError, ValueError):
        return _err('annee_invalide')

    try:
        info = scan_staffing.apply_validated_csv(
            db, event, year, raw, saved_by=_current_user_email())
    except scan_staffing.StaffingSourcesMissing as exc:
        return _err('sources_effectifs_absentes', 404, detail=str(exc))
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception('Application des effectifs impossible')
        return _err('effectifs_impossible', 500, detail=str(exc))

    info['ok'] = True
    return jsonify(info)


# ===========================================================================
# Generation du rapport (job asynchrone)
# ===========================================================================
#
# Meme motif que analyse_ops.py:49-56, avec une difference : l'etat est
# indexe par (event, year) au lieu d'etre un global unique. Deux rapports
# d'evenements differents peuvent donc etre generes en parallele, la ou
# analyse_ops n'en tolere qu'un a la fois.

_JOBS = {}
_JOBS_BY_TARGET = {}
_JOBS_LOCK = threading.Lock()
JOB_TTL_SECONDS = 60 * 60


def _sweep_jobs():
    """Purge les jobs termines depuis plus d'une heure ; verrou deja pris."""
    now = time.time()
    for job_id in [j for j, v in _JOBS.items()
                   if v.get('finished_at') and now - v['finished_at'] > JOB_TTL_SECONDS]:
        job = _JOBS.pop(job_id, None)
        if job:
            _JOBS_BY_TARGET.pop((job['event'], job['year']), None)


@scan_report_bp.route('/scan-report/generate', methods=['POST'])
def scan_report_generate():
    """Lance la generation du rapport pour (event, year)."""
    body = request.get_json(silent=True) or {}
    event = (body.get('event') or DEFAULT_EVENT).strip()
    try:
        year = int(body.get('year') or DEFAULT_YEAR)
    except (TypeError, ValueError):
        return _err('annee_invalide')

    # L'analyse redigee de la frequentation est embarquee dans le rapport. Elle
    # n'appelle Claude que si les donnees ont change depuis la derniere, mais
    # on laisse la possibilite de la couper franchement.
    with_analysis = body.get('analysis') is not False
    user = _current_user_email()

    target = ('report', event, year)
    with _JOBS_LOCK:
        _sweep_jobs()
        running = _JOBS_BY_TARGET.get(target)
        if running and _JOBS.get(running, {}).get('status') in ('queued', 'running'):
            return _err('generation_en_cours', 409, job_id=running)
        job_id = uuid.uuid4().hex
        _JOBS[job_id] = {
            'id': job_id, 'event': event, 'year': year,
            'status': 'queued', 'progress': 0, 'step': 'En attente',
            'error': None, 'detail': None, 'result': None,
            'started_at': time.time(), 'finished_at': None,
        }
        _JOBS_BY_TARGET[target] = job_id

    threading.Thread(target=_run_generate,
                     args=(job_id, event, year, with_analysis, user),
                     daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})


def _run_generate(job_id, event, year, with_analysis=True, created_by=None):
    """Corps du thread de generation.

    Attrape BaseException et non Exception : une SystemExit ou une
    MemoryError laisserait sinon le job bloque sur « en cours » a jamais,
    sans moyen de le debloquer autrement qu'en redemarrant le serveur.
    """
    from app import db
    import scan_report_build

    def progress(pct, msg):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job['progress'] = max(0, min(100, int(pct)))
                job['step'] = msg

    try:
        with _JOBS_LOCK:
            _JOBS[job_id]['status'] = 'running'
        info = scan_report_build.generate(
            db, event, year, progress_cb=progress,
            with_analysis=with_analysis, created_by=created_by)
        with _JOBS_LOCK:
            job = _JOBS[job_id]
            job.update({'status': 'done', 'progress': 100,
                        'step': 'Termine', 'result': info})
    except BaseException as exc:
        logger.exception('Generation du rapport %s/%s impossible', event, year)
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job.update({'status': 'error', 'error': 'generation_impossible',
                            'detail': str(exc), 'step': 'Echec'})
    finally:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job['finished_at'] = time.time()
            # Libere la cible meme en cas d'echec, sinon toute nouvelle
            # tentative se heurterait a un 409 fantome.
            if _JOBS_BY_TARGET.get(('report', event, year)) == job_id:
                _JOBS_BY_TARGET.pop(('report', event, year), None)


@scan_report_bp.route('/scan-report/analysis/generate', methods=['POST'])
def scan_report_analysis_generate():
    """Lance l'analyse redigee. `dry_run` retourne le prompt sans appel API."""
    from app import db
    import scan_analysis

    body = request.get_json(silent=True) or {}
    event = (body.get('event') or DEFAULT_EVENT).strip()
    try:
        year = int(body.get('year') or DEFAULT_YEAR)
    except (TypeError, ValueError):
        return _err('annee_invalide')

    if body.get('dry_run'):
        # Permet d'iterer sur le prompt sans consommer de tokens.
        try:
            out = scan_analysis.generate_scan_analysis(
                db, event, year, dry_run=True)
        except scan_analysis.ClaudeError as exc:
            return _err('analyse_impossible', 400, detail=str(exc))
        return jsonify({'ok': True, 'dry_run': True, 'chars': out['chars'],
                        'system': out['system'], 'user': out['user']})

    if not pcorg_api_key_present():
        return _err('cle_api_absente', 503,
                    detail='ANTHROPIC_API_KEY non configuree sur le serveur')

    target = ('analysis', event, year)
    with _JOBS_LOCK:
        _sweep_jobs()
        running = _JOBS_BY_TARGET.get(target)
        if running and _JOBS.get(running, {}).get('status') in ('queued', 'running'):
            return _err('analyse_en_cours', 409, job_id=running)
        job_id = uuid.uuid4().hex
        _JOBS[job_id] = {
            'id': job_id, 'event': event, 'year': year,
            'status': 'queued', 'progress': 0, 'step': 'En attente',
            'error': None, 'detail': None, 'result': None,
            'started_at': time.time(), 'finished_at': None,
        }
        _JOBS_BY_TARGET[target] = job_id

    threading.Thread(target=_run_analysis,
                     args=(job_id, event, year, body.get('model'),
                           _current_user_email()),
                     daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})


def pcorg_api_key_present():
    import pcorg_summary
    return bool(pcorg_summary.ANTHROPIC_API_KEY)


def _run_analysis(job_id, event, year, model, user):
    """Thread d'analyse. Meme protection BaseException que la generation."""
    from app import db
    import scan_analysis

    def progress(pct, msg):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job['progress'] = max(0, min(100, int(pct)))
                job['step'] = msg

    try:
        with _JOBS_LOCK:
            _JOBS[job_id]['status'] = 'running'
        doc = scan_analysis.generate_scan_analysis(
            db, event, year, model=model, created_by=user, on_progress=progress)
        with _JOBS_LOCK:
            _JOBS[job_id].update({
                'status': 'done', 'progress': 100, 'step': 'Termine',
                'result': {'id': str(doc['_id']), 'model': doc['model'],
                           'usage': doc.get('usage'),
                           'parsed': bool(doc.get('sections'))},
            })
    except BaseException as exc:
        logger.exception('Analyse %s/%s impossible', event, year)
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job.update({'status': 'error', 'error': 'analyse_impossible',
                            'detail': str(exc), 'step': 'Echec'})
    finally:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job['finished_at'] = time.time()
            if _JOBS_BY_TARGET.get(('analysis', event, year)) == job_id:
                _JOBS_BY_TARGET.pop(('analysis', event, year), None)


@scan_report_bp.route('/scan-report/analysis/list')
def scan_report_analysis_list():
    from app import db
    import scan_analysis
    return jsonify({'ok': True, 'items': scan_analysis.list_analyses(
        db, request.args.get('event'), request.args.get('year'))})


@scan_report_bp.route('/scan-report/analysis/<analysis_id>')
def scan_report_analysis_get(analysis_id):
    from app import db
    import scan_analysis
    doc = scan_analysis.get_analysis(db, analysis_id)
    if not doc:
        return _err('analyse_inconnue', 404)
    doc.pop('kpis', None)  # volumineux et deja resume dans les sections
    return jsonify({'ok': True, 'analysis': doc})


@scan_report_bp.route('/scan-report/generate/status')
def scan_report_generate_status():
    """Etat d'un job de generation."""
    job_id = request.args.get('job')
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return _err('job_inconnu', 404)
        return jsonify({
            'ok': True, 'status': job['status'], 'progress': job['progress'],
            'step': job['step'], 'error': job['error'], 'detail': job['detail'],
            'result': job['result'],
        })
