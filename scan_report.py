"""
Blueprint Cockpit : chaine scans, de l'import Excel a l'analyse redigee.

Transport HTTP uniquement — la logique metier vit dans les modules dedies :
    scan_import.py        parsing, resolution des features, ecriture Mongo
    scan_report_build.py  generation du rapport HTML
    scan_staffing.py      effectifs derives du calendrier (aucune saisie)
    scan_analysis.py      analyse redigee par Claude

Groupes de routes :
- page et service du rapport  : /scan-report, /static, /available
- import                      : /template.xlsx, /features, /import/*
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
    from app import (CODING, decoder_jeton_portail, ROLE_HIERARCHY,
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
        payload = decoder_jeton_portail(token)
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    roles = payload.get("roles_by_app", {}).get(APP_KEY, "")
    if isinstance(roles, str):
        roles = [roles]
    max_level = max((ROLE_HIERARCHY.get(r, 0) for r in roles), default=0)
    # super_admin global = admin partout (meme regle que role_required)
    from app import SUPER_ADMIN_ROLE, request_admin_grant
    if SUPER_ADMIN_ROLE in (payload.get("global_roles") or []):
        max_level = ROLE_HIERARCHY.get("admin", 3)
    # Page d'administration accordee par un groupe (app.ADMIN_PAGE_REGISTRY)
    if max_level < ROLE_HIERARCHY.get("admin", 3) and not request_admin_grant(payload):
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
    report_available = bool(file_path and os.path.isfile(file_path))
    live_available = False
    if not report_available:
        # Ecran vide : dire qu'une archive live suffit, sans import.
        try:
            from app import db as app_db
            import live_frequentation
            live_available = live_frequentation.has_live_archive(app_db, event, int(year))
        except Exception:
            logger.warning('Detection de l\'archive live impossible', exc_info=True)
    return render_template(
        'scan_report.html',
        event=event, year=year,
        report_available=report_available,
        live_available=live_available,
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
    """Couples (event, year) dont le fichier HTML existe reellement sur disque,
    plus les editions ayant une archive du controle d'acces live (rapport
    generable sans import ; `generated_at` null tant qu'il n'est pas genere).

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
    # Editions suivies par le controle d'acces live : leur rapport se construit
    # depuis l'archive HSH, sans aucun import. On les liste meme sans rapport
    # genere (`generated_at` null) pour qu'on sache qu'il suffit de regenerer.
    live = set()
    try:
        import live_scan_units
        live = {(e, str(y)) for e, y in live_scan_units.list_live_editions(app_db)}
    except Exception:
        logger.warning('Inventaire des editions live indisponible', exc_info=True)
    for key, row in best.items():
        row['source'] = 'live_controle' if key in live else None
    for ev, yr in live:
        if (ev, yr) not in best:
            best[(ev, yr)] = {'event': ev, 'year': yr, 'generated_at': None,
                              'size_kb': None, 'source': 'live_controle'}
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

# Une matrice Sirius pese ~700 Ko, mais un journal (une ligne par scan) pese
# bien plus : 63 Mo pour le GP Explorer 2025 (746 516 lignes). Le fichier est
# ecrit sur disque puis lu en flux, jamais charge en memoire d'un bloc.
# ⚠ IIS plafonne aussi le corps des requetes (`maxAllowedContentLength` du
# web.config, 30 Mo par defaut) : les deux limites doivent rester alignees.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
ALLOWED_UPLOAD_EXT = {'.xlsx', '.xlsm', '.csv', '.txt'}

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
    """Recoit le fichier et lance son analyse en tache de fond.

    Asynchrone depuis l'arrivee des journaux de scans : lire 746 000 lignes
    prend ~35 s, trop pres du delai du proxy IIS (ARR, 2 min) pour tenir dans
    la requete. Retourne {token, job_id} ; l'apercu arrive dans le `result`
    du job (GET /scan-report/generate/status), au meme format qu'avant.
    """
    upload = request.files.get('file')
    if upload is None or not upload.filename:
        return _err('fichier_manquant')
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        return _err('extension_invalide')

    tmp_dir = os.path.join(REPORT_DIR, 'uploads', 'scan_imports')
    os.makedirs(tmp_dir, exist_ok=True)
    token = uuid.uuid4().hex
    # Le fichier source est conserve : tracabilite de ce qui a ete importe.
    # Ecrit en flux : un journal de plusieurs dizaines de Mo ne transite
    # jamais en entier par la memoire du process.
    tmp_path = os.path.join(tmp_dir, token + ext)
    upload.save(tmp_path)
    size = os.path.getsize(tmp_path)
    if not size:
        os.remove(tmp_path)
        return _err('fichier_vide')
    if size > MAX_UPLOAD_BYTES:
        os.remove(tmp_path)
        return _err('fichier_trop_volumineux', 413)

    event = (request.form.get('event') or DEFAULT_EVENT).strip()
    year = request.form.get('year') or None
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            os.remove(tmp_path)
            return _err('annee_invalide')

    with _STAGING_LOCK:
        _sweep_staging()
        _STAGING[token] = {
            'created_at': time.time(), 'path': tmp_path, 'parsed': None,
            'event': event, 'year': year, 'user': _current_user_email(),
            # Le fichier est stocke sous un jeton, mais c'est le nom d'origine
            # qui doit apparaitre dans le document : sans lui on ne sait plus
            # quel export a produit les donnees.
            'filename': upload.filename,
        }
    job_id = _start_import_job(token, None, None)
    return jsonify({'ok': True, 'token': token, 'job_id': job_id})


@scan_report_bp.route('/scan-report/import/reanalyze', methods=['POST'])
def scan_report_import_reanalyze():
    """Relit le fichier en attente avec des colonnes / options corrigees.

    Body : {token, mapping: {role: index|null}, options: {day_first,
    strip_tags, dedup, sens_map, result_map}}. Rien n'est reteleverse.
    """
    body = request.get_json(silent=True) or {}
    token = body.get('token')
    with _STAGING_LOCK:
        _sweep_staging()
        staged = _STAGING.get(token)
        if staged:
            staged['created_at'] = time.time()  # l'utilisateur travaille encore
    if not staged:
        return _err('import_expire', 410)
    mapping = body.get('mapping') if isinstance(body.get('mapping'), dict) else None
    options = body.get('options') if isinstance(body.get('options'), dict) else None
    job_id = _start_import_job(token, mapping, options)
    return jsonify({'ok': True, 'token': token, 'job_id': job_id})


def _start_import_job(token, mapping, options):
    with _JOBS_LOCK:
        _sweep_jobs()
        job_id = uuid.uuid4().hex
        _JOBS[job_id] = {
            'id': job_id, 'event': None, 'year': None,
            'status': 'queued', 'progress': 0, 'step': 'En attente',
            'error': None, 'detail': None, 'result': None,
            'started_at': time.time(), 'finished_at': None,
        }
    threading.Thread(target=_run_import_analysis,
                     args=(job_id, token, mapping, options),
                     daemon=True).start()
    return job_id


def _run_import_analysis(job_id, token, mapping, options):
    """Thread d'analyse d'un fichier en attente. Meme protection BaseException
    que la generation : un job ne doit jamais rester bloque « en cours »."""
    from app import db
    import scan_formats
    import scan_import

    def progress(pct, msg):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job['progress'] = max(0, min(100, int(pct)))
                job['step'] = msg

    def fail(code, detail=None, result=None):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job.update({'status': 'error', 'error': code, 'detail': detail,
                            'step': 'Echec', 'result': result})

    try:
        with _JOBS_LOCK:
            _JOBS[job_id]['status'] = 'running'
        with _STAGING_LOCK:
            staged = _STAGING.get(token)
        if not staged:
            return fail('import_expire')
        # Familles de titre : a defaut d'un choix dans la modale, celui de la
        # derniere edition importee de l'evenement (IMPORT [ACO] = vehicules
        # ne se devine pas au libelle, il ne faut le dire qu'une fois).
        options = dict(options or {})
        if 'famille_map' not in options:
            remembered = _last_title_families(db, staged['event'])
            if remembered:
                options['famille_map'] = remembered
        try:
            analysis = scan_formats.analyze_file(staged['path'], mapping, options, progress)
        except scan_import.ScanImportError as exc:
            # Colonnes non reconnues : on renvoie ce qui a ete detecte pour
            # que l'utilisateur complete le mapping, sans reteleverser.
            det = (exc.extra or {}).get('detection')
            result = {'token': token, 'format': _format_block(det, None)} if det else None
            return fail(exc.code, exc.detail, result)

        progress(96, 'Rattachement aux entites cartographiques')
        event = staged['event']
        if analysis['format'] == 'journal':
            journal = analysis['journal']
            # Annee : celle de la page, sinon celle majoritaire du fichier.
            year = staged['year']
            if year is None:
                tmp = scan_formats.build_parsed(journal, {})
                year = scan_import.guess_year(tmp)
            parsed, resolved, _ = scan_formats.resolve_journal(
                db, journal, event, year, source_file=staged['filename'])
        else:
            journal = None
            parsed = analysis['parsed']
            year = staged['year'] or scan_import.guess_year(parsed)
            resolved = scan_import.resolve_features(db, parsed['units'], event, year)
        parsed['source_file'] = staged['filename']

        with _STAGING_LOCK:
            cur = _STAGING.get(token)
            if not cur:
                return fail('import_expire')
            cur.update({'parsed': parsed, 'journal': journal, 'year': year,
                        'format': analysis['format'],
                        'detection': analysis['detection'],
                        'created_at': time.time()})

        payload = _preview_payload(db, token, staged['filename'], event, year,
                                   parsed, resolved)
        payload['format'] = _format_block(analysis['detection'],
                                          journal['profile'] if journal else None)
        with _JOBS_LOCK:
            _JOBS[job_id].update({'status': 'done', 'progress': 100,
                                  'step': 'Termine', 'result': payload})
    except BaseException as exc:
        logger.exception('Analyse du fichier de scans impossible')
        fail('parsing_impossible', str(exc))
    finally:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job['finished_at'] = time.time()


def _last_title_families(db, event):
    """Choix par famille de titre de la derniere edition importee, ou {}."""
    doc = db['historique_controle'].find_one(
        {'event': event, 'type': 'complet', 'title_families': {'$exists': True}},
        {'title_families': 1}, sort=[('year', -1)])
    return dict((doc or {}).get('title_families') or {})


def _format_block(detection, profile):
    """Description du format lu, des colonnes et des options, pour l'UI."""
    import scan_formats
    if not detection:
        return None
    out = {
        'id': detection['format'],
        'label': scan_formats.FORMAT_LABELS.get(detection['format'], detection['format']),
        'columns': detection.get('columns') or [],
        'mapping': detection.get('mapping') or {},
        'options': {k: v for k, v in (detection.get('options') or {}).items()
                    if k in ('day_first', 'strip_tags', 'dedup')},
        'roles': scan_formats.ROLES if detection['format'] == 'journal' else [],
        'warnings': detection.get('warnings') or [],
    }
    if profile:
        out.update({'stats': profile['stats'], 'sens_values': profile['sens_values'],
                    'result_values': profile['result_values'],
                    'famille_values': profile.get('famille_values') or [],
                    'dedup_active': profile['dedup']})
    return out


def _preview_payload(db, token, filename, event, year, parsed, resolved):
    """Apercu de l'import : unites, rattachements, ecart avec l'existant."""
    import scan_import
    race = scan_import.resolve_race(db, event, year)
    existing = _existing_summary(db, event, year)

    units = []
    for unit in parsed['units']:
        res = resolved[unit['key']]
        units.append({
            # Cle stable de l'unite pour toute la session d'import : dans un
            # journal, `kind` suit la categorie et peut changer dans la modale,
            # la cle des choix de l'utilisateur ne doit pas bouger avec lui.
            'key': unit['kind'] + '|' + unit['name'],
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
            'category': res.get('category'),
            'category_source': res.get('category_source'),
            'ignored': bool(res.get('ignored')),
            'no_location': bool(res.get('no_location')),
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

    return {
        'ok': True,
        'token': token,
        'event': event,
        'year': year,
        'race': race,
        'summary': {
            'source_file': filename,
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
        'categories': scan_import.UNIT_CATEGORIES,
        'unresolved_count': sum(1 for u in units if not u['_id_feature']),
        'existing': existing,
        'delta_pct': delta_pct,
        'needs_confirm': delta_pct is not None
                         and abs(delta_pct) > DELTA_CONFIRM_THRESHOLD * 100,
        # Journal : la categorie « Porte d'enceinte » decide qu'une unite
        # compte dans la serie de l'enceinte generale (cf. resolve_journal).
        'kind_from_category': bool(parsed.get('kind_from_category')),
        # Remise a zero nocturne des presents : reprise de la derniere edition
        # importee de l'evenement, jamais deduite (un 24 h dort sur place).
        'presence_reset_hour': _last_reset_hour(db, event, year),
    }


def _last_reset_hour(db, event, year):
    """Reglage de remise a zero de l'edition, sinon de la plus recente."""
    import scan_import
    cur = db['historique_controle'].find(
        {'event': event, 'type': 'complet'},
        {'year': 1, 'presence_reset_hour': 1}).sort('year', -1)
    docs = list(cur)
    for d in docs:
        if d.get('year') == int(year):
            return scan_import.normalize_reset_hour(d.get('presence_reset_hour'))
    for d in docs:
        if 'presence_reset_hour' in d:
            return scan_import.normalize_reset_hour(d.get('presence_reset_hour'))
    return None


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
    # La date saisie a la main passe par le meme filtre que les sources : le
    # champ accepte ce que l'utilisateur colle, y compris une valeur UTC
    # copiee depuis parametrages.
    race = (scan_import.to_naive_paris_iso(body.get('race'))
            or scan_import.resolve_race(db, event, year))

    # Choix manuels, par unite :
    # {"porte|PORTE NORD": {_id_feature, feature_collection, category, save}}
    # Le rattachement geographique et la categorie sont independants : on peut
    # classer une unite sans savoir ou elle est, et l'inverse.
    overrides = {}
    for key, val in (body.get('overrides') or {}).items():
        if '|' not in key or not isinstance(val, dict):
            continue
        kind, name = key.split('|', 1)
        cat = val.get('category')
        ign = val.get('ignored')
        nol = val.get('no_location')
        overrides[(kind, name)] = {
            '_id_feature': val.get('_id_feature') or None,
            'feature_collection': val.get('feature_collection'),
            'category': cat if cat in scan_import.CATEGORY_IDS else None,
            'ignored': bool(ign) if ign is not None else None,
            'no_location': bool(nol) if nol is not None else None,
            'save': bool(val.get('save')),
        }

    if staged.get('parsed') is None:
        return _err('analyse_fichier_en_cours', 409)

    if staged.get('format') == 'journal':
        # La nature porte / zone suit la categorie choisie : les unites sont
        # reconstruites depuis le journal agrege (sans relire le fichier), et
        # les choix re-cles sous leur nature finale avant d'etre memorises.
        import scan_formats
        by_name = {name: val for (_kind, name), val in overrides.items()}
        parsed, resolved, overrides = scan_formats.resolve_journal(
            db, staged['journal'], event, year, overrides_by_name=by_name,
            source_file=staged.get('filename'))
    else:
        parsed = staged['parsed']
        resolved = scan_import.resolve_features(db, parsed['units'], event, year,
                                                overrides=overrides)
    parsed['source_file'] = staged.get('filename') or parsed.get('source_file')
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

    # Remise a zero nocturne des presents : choix explicite de la modale
    # (null = solde continu sur toute la periode).
    reset_hour = scan_import.normalize_reset_hour(body.get('presence_reset_hour'))

    docs = [
        scan_import.build_complet_doc(parsed, resolved, event, year, race,
                                      imported_by=user, imported_at=now,
                                      uam_by_porte=uam_by_porte,
                                      presence_reset_hour=reset_hour),
        scan_import.build_portes_doc(parsed, resolved, event, year, race),
        scan_import.build_frequentation_doc(parsed, event, year, race,
                                            resolved=resolved,
                                            presence_reset_hour=reset_hour),
    ]
    if staged.get('format') == 'journal':
        profile = (staged.get('journal') or {}).get('profile') or {}
        fams = profile.get('famille_values') or []
        if fams:
            # Memorise pour les imports suivants de l'evenement, et trace de
            # ce qui compte (ou non) dans les presents de cette edition.
            # Cles = libelles normalises (sans ponctuation, donc sans '.' ni
            # '$', interdits dans une cle Mongo) ; la valeur vide est omise.
            docs[0]['title_families'] = {f['value']: f['map'] for f in fams if f['value']}
            docs[0]['title_families_counts'] = {f['value']: f['count'] for f in fams if f['value']}
        docs[0]['import_stats'] = profile.get('stats')

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

    ignored = scan_import.ignored_keys(resolved)
    # Une unite explicitement declaree sans lieu n'est PAS « a localiser » :
    # la question a ete tranchee, la signaler serait du bruit.
    unresolved = [{'kind': k[0], 'name': k[1]}
                  for k, v in resolved.items()
                  if not v['_id_feature'] and not v.get('no_location')
                  and k not in ignored]

    # Le rapport est un fichier fige : sans regeneration il montrerait encore
    # l'etat d'avant. On l'enchaine plutot que de compter sur la memoire de
    # l'utilisateur. L'analyse redigee n'appelle le modele que si les donnees
    # qui l'alimentent ont bouge (empreinte), donc la laisser active ne coute
    # rien quand l'import ne change pas la frequentation.
    regen_job, _ = start_generate_job(event, year, True, user)

    return jsonify({
        'ok': True, 'run_id': run_id, 'event': event, 'year': year,
        'written': written, 'archived': archived,
        'overrides_saved': saved, 'unresolved': unresolved,
        'ignored': sorted(name for _, name in ignored),
        'ignored_doors': sorted(name for kind, name in ignored
                                if kind == 'porte'),
        'regen_job': regen_job,
    })


@scan_report_bp.route('/scan-report/import/<token>', methods=['DELETE'])
def scan_report_import_abort(token):
    """Abandon : purge l'entree en attente et son fichier source."""
    with _STAGING_LOCK:
        _drop_staged(token)
    return jsonify({'ok': True})


# ===========================================================================
# Edition du mapping apres import
# ===========================================================================
#
# Corriger un rattachement n'impose plus de reteleverser le classeur : les
# trois documents sont reconstruits depuis `complet`, qui porte deja les
# series par unite. Voir scan_mapping.py pour l'equivalence verifiee.

@scan_report_bp.route('/scan-report/mapping')
def scan_report_mapping_get():
    """Mapping courant d'un couple, au format de la table d'import."""
    from app import db
    import scan_mapping

    event = (request.args.get('event') or DEFAULT_EVENT).strip()
    try:
        year = int(request.args.get('year') or DEFAULT_YEAR)
    except (TypeError, ValueError):
        return _err('annee_invalide')

    try:
        data = scan_mapping.load_mapping(db, event, year)
    except scan_mapping.MappingError as exc:
        return _err(exc.code, exc.status, detail=exc.detail)
    except Exception as exc:
        logger.exception('Lecture du mapping impossible')
        return _err('mapping_illisible', 500, detail=str(exc))

    data['ok'] = True
    return jsonify(data)


@scan_report_bp.route('/scan-report/mapping', methods=['POST'])
def scan_report_mapping_post():
    """Applique des corrections de mapping et reecrit les trois documents."""
    from app import db
    import scan_import
    import scan_mapping

    body = request.get_json(silent=True) or {}
    event = (body.get('event') or DEFAULT_EVENT).strip()
    try:
        year = int(body.get('year') or DEFAULT_YEAR)
    except (TypeError, ValueError):
        return _err('annee_invalide')

    changes = {}
    for key, val in (body.get('changes') or {}).items():
        if '|' not in key or not isinstance(val, dict):
            continue
        cat = val.get('category')
        changes[key] = {
            '_id_feature': val.get('_id_feature') or None,
            'feature_collection': val.get('feature_collection'),
            'category': cat if cat in scan_import.CATEGORY_IDS else None,
            'ignored': bool(val.get('ignored')),
            'no_location': bool(val.get('no_location')),
        }

    try:
        info = scan_mapping.apply_mapping(
            db, event, year, changes,
            applied_by=_current_user_email(),
            save_overrides=bool(body.get('save_overrides')))
    except scan_mapping.MappingError as exc:
        return _err(exc.code, exc.status, detail=exc.detail)
    except Exception as exc:
        logger.exception('Application du mapping impossible')
        return _err('mapping_impossible', 500, detail=str(exc))

    # Meme raison qu'a l'import : sans regeneration, la correction reste
    # invisible dans le rapport. Rien a enchainer si rien n'a change.
    if info.get('changed'):
        info['regen_job'], _ = start_generate_job(
            event, year, True, _current_user_email())

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


def start_generate_job(event, year, with_analysis=True, user=None):
    """Met une generation en file et retourne (job_id, job_deja_en_cours).

    Extrait de la route pour que l'import et l'edition de mapping puissent
    enchainer la regeneration sans que l'utilisateur ait a y penser : c'est
    l'oubli de regenerer, pas la generation elle-meme, qui coute du temps.
    """
    target = ('report', event, year)
    with _JOBS_LOCK:
        _sweep_jobs()
        running = _JOBS_BY_TARGET.get(target)
        if running and _JOBS.get(running, {}).get('status') in ('queued', 'running'):
            return running, True
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
    return job_id, False


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
    job_id, already = start_generate_job(
        event, year, with_analysis, _current_user_email())
    if already:
        return _err('generation_en_cours', 409, job_id=job_id)
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
                    detail='COCKPIT_ANTHROPIC_API_KEY non configuree sur le serveur')

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
