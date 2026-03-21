# Standard library imports
import os
import re
import json
import logging
import jwt
from datetime import datetime, timedelta, timezone

# Third-party imports
from flask import (
    Flask, jsonify, render_template, send_from_directory, request, 
    redirect, url_for, flash, session, abort, make_response
)
from flask_cors import CORS
from flask_wtf.csrf import CSRFProtect
from functools import wraps
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun
from pymongo import MongoClient
from werkzeug.utils import safe_join
from bson.objectid import ObjectId
from waitress import serve

# Local application imports
from traffic import traffic_bp
from merge import run_merge

################################################################################
# Configuration
################################################################################

TITAN_ENV = os.getenv("TITAN_ENV", "dev").strip().lower()
IS_PROD = TITAN_ENV in {"prod", "production"}
DEV_MODE = not IS_PROD
CODING = os.getenv("CODING", "false").strip().lower() in {"1", "true", "yes"}
PORT = 5008 if DEV_MODE else 4008
logging.basicConfig(level=logging.INFO if DEV_MODE else logging.WARNING)
logger = logging.getLogger(__name__)

DEV_URL = f"http://safe.lemans.org:{PORT}"
PROD_URL = "https://safe.lemans.org"

BASE_URL = DEV_URL if DEV_MODE else PROD_URL

################################################################################
# Initialisation Flask
################################################################################

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'HacB6vFEPpU3M04zMIIcuNtebrAvRME9T2vyqcYjGrQ')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=not DEV_MODE,
    SESSION_COOKIE_SAMESITE="Lax",
)

JWT_SECRET = os.getenv('JWT_SECRET', 'qXyKGSrVz2wVNhOep4tALcRzCzbkgaVFVfNqtKJk0YY')
JWT_ALGORITHM = 'HS256'

if IS_PROD and CODING:
    raise ValueError("CODING must be disabled in production!")

UPLOAD_FOLDER = './uploads'
ALLOWED_EXTENSIONS = {'json', 'geojson', 'csv', 'bson'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['TEMPLATES_AUTO_RELOAD'] = True
csrf = CSRFProtect(app)

# Validation stricte pour la clé secrète en production
if not DEV_MODE and app.config['SECRET_KEY'] == 'HacB6vFEPpU3M04zMIIcuNtebrAvRME9T2vyqcYjGrQ':
    raise ValueError("SECRET_KEY must be set securely in production!")
if not DEV_MODE and JWT_SECRET == 'qXyKGSrVz2wVNhOep4tALcRzCzbkgaVFVfNqtKJk0YY':
    raise ValueError("JWT_SECRET must be set securely in production!")

# Connexion à MongoDB
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)

# Sélection dynamique de la base de données
db_name = 'titan' if DEV_MODE else 'titan'
db = client[db_name]

CORS(app)  # Activer CORS pour toutes les routes

# Collections cockpit groupes/user-groups
COL_GROUPS = db['cockpit_groups']
COL_USER_GROUPS = db['cockpit_user_groups']
COL_GROUPS.create_index("name", unique=True)
COL_USER_GROUPS.create_index("user_id", unique=True)

# Seed fake users en mode CODING pour tester la gestion des groupes
if CODING:
    _fake_cockpit_users = [
        {"prenom": "Bruce", "nom": "WAYNE", "email": "bruce@wayneenterprise.com",
         "titre": "CEO", "service": "DIRECTION",
         "applications": ["Cockpit"], "roles_by_app": {"cockpit": "admin"}},
        {"prenom": "Clark", "nom": "KENT", "email": "clark@dailyplanet.com",
         "titre": "Reporter", "service": "COMMUNICATION",
         "applications": ["Cockpit"], "roles_by_app": {"cockpit": "manager"}},
        {"prenom": "Diana", "nom": "PRINCE", "email": "diana@themyscira.org",
         "titre": "Ambassadrice", "service": "RELATIONS INTERNATIONALES",
         "applications": ["Cockpit"], "roles_by_app": {"cockpit": "user"}},
        {"prenom": "Barry", "nom": "ALLEN", "email": "barry@starlabs.com",
         "titre": "Ingenieur", "service": "TECHNIQUE",
         "applications": ["Cockpit"], "roles_by_app": {"cockpit": "user"}},
    ]
    for _u in _fake_cockpit_users:
        db['users'].update_one(
            {"email": _u["email"]},
            {"$set": _u, "$setOnInsert": {"domain_user": False}},
            upsert=True
        )

################################################################################
# Contrôle d'accès
################################################################################

ROLE_HIERARCHY = {
    "user": 1,
    "manager": 2,
    "admin": 3,
}
ROLE_ORDER = ["user", "manager", "admin"]
APP_KEY = "cockpit"
SUPER_ADMIN_ROLE = "super_admin"

def role_required(required_role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if CODING:
                # En mode développement, on simule un utilisateur avec des permissions maximales
                logger.info(f"[DEV_MODE] Bypassing authentication for role '{required_role}'")
                request.user_payload = {
                    "apps": ["looker", "shiftsolver", "tagger"],
                    "roles_by_app": {"cockpit": "admin"},
                    "global_roles": [],
                    "roles": ["user", "manager", "admin"],
                    "firstname": "Bruce",
                    "lastname": "WAYNE",
                    "email": "bruce@wayneenterprise.com"
                }
                return f(*args, **kwargs)

            token = request.cookies.get("access_token")
            if not token:
                logger.info("Access token manquant. Redirection vers le portail.")
                redirect_url = f"{BASE_URL}/home?message=Authentification requise pour accéder à l'application&category=error"
                return redirect(redirect_url)

            try:
                payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            except jwt.ExpiredSignatureError:
                redirect_url = f"{BASE_URL}/home?message=Votre session a expiré. Veuillez vous reconnecter.&category=warning"
                return redirect(redirect_url)
            except jwt.InvalidTokenError:
                redirect_url = f"{BASE_URL}/home?message=Authentification invalide. Veuillez vous reconnecter.&category=error"
                return redirect(redirect_url)

            global_roles = payload.get("global_roles", []) or []
            is_super_admin = SUPER_ADMIN_ROLE in global_roles
            roles_by_app = payload.get("roles_by_app", {}) or {}
            if not isinstance(roles_by_app, dict):
                roles_by_app = {}
            app_role = roles_by_app.get(APP_KEY)

            if not is_super_admin and not app_role:
                logger.warning("Accès refusé à Cockpit pour cet utilisateur.")
                redirect_url = f"{BASE_URL}/home?message=Vous n'avez pas les droits nécessaires pour accéder à cette application.&category=error"
                return redirect(redirect_url)

            effective_role = "admin" if is_super_admin else app_role
            max_user_role_level = ROLE_HIERARCHY.get(effective_role, 0)

            if max_user_role_level < ROLE_HIERARCHY.get(required_role, 0):
                flash(f"Accès interdit : cette fonctionnalité requiert un rôle '{required_role}'.", "error")
                return redirect(request.referrer or "/")

            if effective_role in ROLE_HIERARCHY:
                payload["roles"] = [
                    role for role in ROLE_ORDER
                    if ROLE_HIERARCHY[role] <= ROLE_HIERARCHY[effective_role]
                ]
            else:
                payload["roles"] = []
            payload["app_role"] = effective_role
            payload["is_super_admin"] = is_super_admin
            request.user_payload = payload
            return f(*args, **kwargs)
        return decorated_function
    return decorator

################################################################################
# GENERAL
################################################################################

def clean_collection_name(name):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', name)

@app.route("/logout_redirect")
def logout_redirect():
    # Ici, le cookie SSO a déjà été supprimé par le portail.
    target_url = f"{BASE_URL}/logout"
    return redirect(target_url)

@app.route("/")
@role_required("user")
def index():
    # Récupérer l'info stockée dans request.user_payload si besoin
    payload = getattr(request, 'user_payload', {})
    user_roles = payload.get("roles", [])
    user_apps = payload.get("apps", [])
    user_firstname = payload.get("firstname", "")
    user_lastname = payload.get("lastname", "")
    user_email = payload.get("email", "")
    return render_template("index.html", user_roles=user_roles, user_apps=user_apps,
                           user_firstname=user_firstname, user_lastname=user_lastname,
                           user_email=user_email)

@app.errorhandler(404)
def page_not_found(e):
    flash("La page demandée est introuvable. Veuillez contacter un administrateur.", "error")
    # Redirection vers l'index
    return redirect(url_for("index"))

@app.route('/get_events', methods=['GET'])
@role_required("user")
def get_events():
    events = list(db['evenement'].find({}, {'_id': 0, 'nom': 1}))
    return jsonify(events)

# Route pour servir les tuiles locales
@app.route('/tiles/<z>/<x>/<y>.png')
@role_required("user")
def serve_tiles(z, x, y):
    tile_directories = [
        r'E:\TITAN\shared\satellite', # Windows serveur
        r'C:\Users\l.arnault\satellite', # Windows PCA
        '/Users/ludovic/Dropbox/ACO/TITAN/archives/looker/static/img/sat', # MAC OS laptop
        '/Users/ludovicarnault/Dropbox/ACO/TITAN/looker/static/img/sat' # MAC OS maison
    ]

    for tile_directory in tile_directories:
        tile_path = safe_join(tile_directory, z, x)
        image_path = safe_join(tile_path, f'{y}.png')
              
        if os.path.exists(image_path):
            return send_from_directory(tile_path, f'{y}.png')

    return abort(404, description="Image non trouvée dans les répertoires spécifiés.")

################################################################################
# TIMETABLE
################################################################################

@app.route('/timetable', methods=['GET'])
@role_required("user")
def get_timetable():
    # Récupère les paramètres d'URL pour l'événement et l'année
    event = request.args.get('event')
    year = request.args.get('year')
    
    if not event or not year:
        return jsonify({"error": "Les paramètres 'event' et 'year' sont requis."}), 400

    # Recherche dans la collection "timetable"
    timetable_doc = db.timetable.find_one({"event": event, "year": year}, {"_id": 0})
    
    if not timetable_doc:
        return jsonify({"error": "Aucune donnée trouvée pour cet événement et cette année."}), 404

    return jsonify(timetable_doc)

@app.route('/get_parametrage', methods=['GET'])
@role_required("user")
def get_parametrage():
    event = request.args.get('event')
    year = request.args.get('year')
    parametrage = db['parametrages'].find_one({'event': event, 'year': year}, {'_id': 0, 'data': 1})
    if parametrage:
        return jsonify(parametrage['data'])
    else:
        return jsonify({})

# -------------------------------------------------------------------------------
# Routes pour la carte (event view)
# -------------------------------------------------------------------------------
@app.route('/get_gm_categories', methods=['GET'])
@role_required("user")
def get_gm_categories():
    """Return enabled groundmaster categories with their config."""
    cats = list(db['groundmaster_categories'].find(
        {'enabled': True},
        {'_id': 1, 'label': 1, 'icon': 1, 'dataKey': 1, 'collection': 1,
         'mode': 1, 'scheduleConfig': 1, 'mapping': 1, 'sourceFormat': 1,
         'storageType': 1, 'source': 1, 'cardFields': 1}
    ))
    for c in cats:
        c['_id'] = str(c['_id'])
    return jsonify(cats)


@app.route('/gm_collection_data/<collection_name>', methods=['GET'])
@role_required("user")
def gm_collection_data(collection_name):
    """Return GeoJSON features from a groundmaster collection."""
    valid = db['groundmaster_categories'].find_one(
        {'collection': collection_name, 'enabled': True}, {'_id': 1}
    )
    if not valid:
        return jsonify({"error": "Collection not found"}), 404

    doc = db[collection_name].find_one(
        {'type': 'FeatureCollection'} if collection_name == 'terrains' else {},
        {'features': 1, '_id': 0}
    )
    if doc and 'features' in doc:
        features = doc['features']
    else:
        features = []

    if not features:
        docs = list(db[collection_name].find(
            {'type': 'FeatureCollection'}, {'features': 1, '_id': 0}
        ))
        for d in docs:
            features.extend(d.get('features', []))

    return jsonify(features)


@app.route('/get_parking_color', methods=['GET'])
@role_required("user")
def get_parking_color():
    color_name = request.args.get("color")
    if not color_name:
        return jsonify({"error": "Le parametre 'color' est requis"}), 400
    settings = db['signmanager_settings'].find_one({}, {'_id': 0, 'itineraire.couleurs': 1})
    if not settings or 'itineraire' not in settings or 'couleurs' not in settings['itineraire']:
        return jsonify({"color": "#808080"})
    couleurs = settings['itineraire']['couleurs']
    matching = next((c for c in couleurs if c.get("nom", "").lower() == color_name.lower()), None)
    if matching:
        return jsonify({"color": matching.get("hexa", "#808080")})
    return jsonify({"color": "#808080"})


# -------------------------------------------------------------------------------
# Route pour récupérer les catégories existantes dans la collection timetable
# -------------------------------------------------------------------------------
@app.route('/get_timetable_categories', methods=['GET'])
@role_required("user")
def get_timetable_categories():
    try:
        event = request.args.get('event')
        year = request.args.get('year')
        if not event or not year:
            return jsonify({"categories": []}), 400

        # S'assurer que year est une chaîne de caractères
        year = str(year)

        pipeline = [
            {"$match": {"event": event, "year": year}},
            {"$project": {"data": 1}},
            {"$project": {"events": {"$objectToArray": "$data"}}},
            {"$unwind": "$events"},
            {"$unwind": "$events.v"},
            {"$group": {"_id": "$events.v.category"}}
        ]
        result = list(db.timetable.aggregate(pipeline))
        categories = [doc["_id"] for doc in result if doc["_id"]]
        return jsonify({"categories": categories})
    except Exception as e:
        logger.error("Error getting categories: " + str(e))
        return jsonify({"categories": []}), 500

# -------------------------------------------------------------------------------
# Route pour ajouter un événement dans la collection timetable
# -------------------------------------------------------------------------------
@app.route('/add_timetable_event', methods=['POST'])
@role_required("user")
def add_timetable_event():
    try:
        data = request.get_json()
        # Récupérer les valeurs envoyées
        event_name = data.get('event')
        year = data.get('year')
        date = data.get('date')
        event_details = {
            "start": data.get('start', "TBC"),
            "end": data.get('end', "TBC"),
            "duration": data.get('duration', ""),
            "category": data.get('category'),
            "activity": data.get('activity'),
            "place": data.get('place'),
            "department": data.get('department'),
            "type": data.get('type', "Timetable"),
            "origin": data.get('origin', "manual"),
            "remark": data.get('remark', ""),
            "todo": data.get('todo', ""),  # texte multi-lignes (une tâche par ligne)
            "preparation_checked": (data.get('preparation_checked') or "").lower()  # "", "progress", "true"
        }
        # Générer un identifiant unique pour l'événement
        event_details["_id"] = str(ObjectId())

        # Vérifier si un document pour cet event et cette année existe déjà
        timetable_doc = db.timetable.find_one({"event": event_name, "year": year})
        if timetable_doc:
            # Si la date existe déjà, on ajoute l'événement à la liste
            if date in timetable_doc.get('data', {}):
                db.timetable.update_one(
                    {"_id": timetable_doc["_id"]},
                    {"$push": {f"data.{date}": event_details}}
                )
            else:
                # Sinon, on crée la clé pour cette date avec une liste contenant l'événement
                db.timetable.update_one(
                    {"_id": timetable_doc["_id"]},
                    {"$set": {f"data.{date}": [event_details]}}
                )
        else:
            # Création d'un nouveau document pour cet événement et cette année
            new_doc = {
                "event": event_name,
                "year": str(year),
                "data": {
                    date: [event_details]
                }
            }
            db.timetable.insert_one(new_doc)
        return jsonify({"success": True, "message": "Événement ajouté avec succès."})
    except Exception as e:
        logger.error("Erreur lors de l'ajout de l'événement dans la timetable: " + str(e))
        return jsonify({"success": False, "message": "Erreur lors de l'ajout de l'événement."}), 500
    
# -------------------------------------------------------------------------------
# Mettre à jour un événement (édition dans la liste imbriquée par date + _id)
# payload attendu: { event, year, date, _id, start, end, duration, category, activity, place, department, remark }
# -------------------------------------------------------------------------------
@app.route('/update_timetable_event', methods=['POST'])
@role_required("user")
def update_timetable_event():
    try:
        data = request.get_json() or {}
        event_name = data.get('event')
        year = str(data.get('year'))
        target_date = data.get('date')  # peut être une nouvelle date
        ev_id = str(data.get('_id') or '')

        if not all([event_name, year, target_date, ev_id]):
            return jsonify({"success": False, "message": "Paramètres manquants (event/year/date/_id)."}), 400

        doc = db.timetable.find_one({"event": event_name, "year": year})
        if not doc:
            return jsonify({"success": False, "message": "Document timetable introuvable."}), 404

        data_map = doc.get('data') or {}

        # 1) Tente sous la date cible
        events_list = data_map.get(target_date, [])
        idx = next((i for i, ev in enumerate(events_list) if str(ev.get('_id')) == ev_id), None)

        # 2) Si pas trouvé, on cherche dans toutes les dates
        found_date = target_date if idx is not None else None
        if idx is None:
            for d, lst in data_map.items():
                j = next((i for i, ev in enumerate(lst) if str(ev.get('_id')) == ev_id), None)
                if j is not None:
                    found_date, idx = d, j
                    break

        if idx is None or found_date is None:
            return jsonify({"success": False, "message": "Événement introuvable."}), 404

        # Prépare les nouvelles valeurs
        updated_fields = {
            "start":               data.get("start", "TBC"),
            "end":                 data.get("end", "TBC"),
            "duration":            data.get("duration", ""),
            "category":            data.get("category"),
            "activity":            data.get("activity"),
            "place":               data.get("place"),
            "department":          data.get("department"),
            "remark":              data.get("remark"),
            "todo":                data.get("todo", ""),  # texte multi-lignes
            "preparation_checked": (data.get("preparation_checked") or "").lower(),
            "origin":              "manual-edit"
        }

        # Si la date d'origine ≠ la date cible -> on déplace l'objet
        if found_date != target_date:
            # on prend l'objet source, on le met à jour, puis on le push dans la target_date
            src_event = data_map[found_date][idx]
            for k, v in updated_fields.items():
                if v is not None:
                    src_event[k] = v

            # supprime dans found_date
            db.timetable.update_one(
                {"_id": doc["_id"]},
                {"$pull": {f"data.{found_date}": {"_id": ev_id}}}
            )
            # push dans target_date (créé si absent)
            db.timetable.update_one(
                {"_id": doc["_id"]},
                {"$push": {f"data.{target_date}": src_event}}
            )
            return jsonify({"success": True, "message": "Événement déplacé et mis à jour."})

        # Sinon même date -> simple $set par index
        set_ops = {}
        for k, v in updated_fields.items():
            if v is not None:
                set_ops[f"data.{found_date}.{idx}.{k}"] = v

        if not set_ops:
            return jsonify({"success": False, "message": "Aucune donnée à mettre à jour."}), 400

        db.timetable.update_one({"_id": doc["_id"]}, {"$set": set_ops})
        return jsonify({"success": True, "message": "Événement mis à jour."})

    except Exception as e:
        logger.error("Erreur update_timetable_event: %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Erreur serveur lors de la mise à jour."}), 500

# -------------------------------------------------------------------------------
# Supprimer un événement (par date + _id)
# payload attendu: { event, year, date, _id }
# -------------------------------------------------------------------------------
@app.route('/delete_timetable_event', methods=['POST'])
@role_required("user")
def delete_timetable_event():
    try:
        data = request.get_json() or {}
        event_name = data.get('event')
        year = str(data.get('year'))
        date = data.get('date')
        ev_id = str(data.get('_id') or '')

        if not all([event_name, year, date, ev_id]):
            return jsonify({"success": False, "message": "Paramètres manquants (event/year/date/_id)."}), 400

        res = db.timetable.update_one(
            {"event": event_name, "year": year},
            {"$pull": {f"data.{date}": {"_id": ev_id}}}
        )
        if res.modified_count == 0:
            return jsonify({"success": False, "message": "Aucune suppression effectuée (événement introuvable)."}), 404

        return jsonify({"success": True, "message": "Événement supprimé."})
    except Exception as e:
        logger.error("Erreur delete_timetable_event: %s", e)
        return jsonify({"success": False, "message": "Erreur serveur lors de la suppression."}), 500

# -------------------------------------------------------------------------------
# Dupliquer un événement (copie le même jour avec un nouvel _id, ou autre date si fournie)
# payload attendu: { event, year, date, _id, target_date? }
# -------------------------------------------------------------------------------
@app.route('/duplicate_timetable_event', methods=['POST'])
@role_required("user")
def duplicate_timetable_event():
    try:
        data = request.get_json() or {}
        event_name = data.get('event')
        year = str(data.get('year'))
        date = data.get('date')
        ev_id = str(data.get('_id') or '')
        target_date = data.get('target_date') or date

        if not all([event_name, year, date, ev_id, target_date]):
            return jsonify({"success": False, "message": "Paramètres manquants (event/year/date/_id/target_date)."}), 400

        doc = db.timetable.find_one({"event": event_name, "year": year})
        if not doc:
            return jsonify({"success": False, "message": "Document timetable introuvable."}), 404

        src_list = (doc.get('data') or {}).get(date, [])
        src = next((ev for ev in src_list if str(ev.get('_id')) == ev_id), None)
        if not src:
            return jsonify({"success": False, "message": "Événement source introuvable."}), 404

        new_ev = dict(src)
        new_ev["_id"] = str(ObjectId())
        new_ev["origin"] = "duplicate"

        db.timetable.update_one(
            {"_id": doc["_id"]},
            {"$push": {f"data.{target_date}": new_ev}}
        )
        return jsonify({"success": True, "message": "Événement dupliqué.", "new_id": new_ev["_id"]})
    except Exception as e:
        logger.error("Erreur duplicate_timetable_event: %s", e)
        return jsonify({"success": False, "message": "Erreur serveur lors de la duplication."}), 500
    
    # -------------------------------------------------------------------------------
# Passer en "progress" (préparation en cours)
# payload: { event, year, date, id }
# -------------------------------------------------------------------------------
@app.route('/set_preparation_progress', methods=['POST'])
@role_required("user")
def set_preparation_progress():
    try:
        data = request.get_json() or {}
        event_name = data.get('event')
        year = str(data.get('year'))
        date = data.get('date')
        ev_id = str(data.get('id') or '')

        if not all([event_name, year, date, ev_id]):
            return jsonify({"success": False, "message": "Paramètres manquants (event/year/date/id)."}), 400

        doc = db.timetable.find_one({"event": event_name, "year": year})
        if not doc:
            return jsonify({"success": False, "message": "Document timetable introuvable."}), 404

        events = (doc.get('data') or {}).get(date, [])
        idx = next((i for i, ev in enumerate(events) if str(ev.get('_id')) == ev_id), None)
        if idx is None:
            return jsonify({"success": False, "message": "Événement introuvable pour cette date."}), 404

        db.timetable.update_one(
            {"_id": doc["_id"]},
            {"$set": {f"data.{date}.{idx}.preparation_checked": "progress"}}
        )
        return jsonify({"success": True})
    except Exception as e:
        logger.error("Erreur set_preparation_progress: %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Erreur serveur"}), 500


# -------------------------------------------------------------------------------
# Passer en "true" (préparation prête)
# payload: { event, year, date, id }
# -------------------------------------------------------------------------------
@app.route('/set_preparation_ready', methods=['POST'])
@role_required("user")
def set_preparation_ready():
    try:
        data = request.get_json() or {}
        event_name = data.get('event')
        year = str(data.get('year'))
        date = data.get('date')
        ev_id = str(data.get('id') or '')

        if not all([event_name, year, date, ev_id]):
            return jsonify({"success": False, "message": "Paramètres manquants (event/year/date/id)."}), 400

        doc = db.timetable.find_one({"event": event_name, "year": year})
        if not doc:
            return jsonify({"success": False, "message": "Document timetable introuvable."}), 404

        events = (doc.get('data') or {}).get(date, [])
        idx = next((i for i, ev in enumerate(events) if str(ev.get('_id')) == ev_id), None)
        if idx is None:
            return jsonify({"success": False, "message": "Événement introuvable pour cette date."}), 404

        db.timetable.update_one(
            {"_id": doc["_id"]},
            {"$set": {f"data.{date}.{idx}.preparation_checked": "true"}}
        )
        return jsonify({"success": True})
    except Exception as e:
        logger.error("Erreur set_preparation_ready: %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Erreur serveur"}), 500

################################################################################
# METEO ET SOLEIL
################################################################################

@app.route('/meteo_previsions/<date>', methods=['GET'])
@role_required("user")
def get_meteo_details(date):
    try:
        day_data = db.meteo_previsions.find_one({'Date': date})
        if not day_data:
            return jsonify({'error': 'No data found for the given date'}), 404
        day_data['_id'] = str(day_data['_id'])
        return jsonify(day_data)

    except Exception as e:
        print(f"Erreur lors de la récupération des détails météo: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/meteo_previsions', methods=['GET'])
@role_required("user")
def get_meteo_previsions():
    today = datetime.now().strftime('%Y-%m-%d')
    three_days_from_now = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')

    previsions = db.meteo_previsions.find({
        'Date': {'$gte': today, '$lte': three_days_from_now}
    }).sort('Date', 1)

    results = []

    for day in previsions:
        if 'Heures' not in day or not isinstance(day['Heures'], list):
            continue
        temperatures = [int(heure['Température (°C)']) for heure in day['Heures']]
        pluviometries = [float(heure['Pluviométrie (mm)']) for heure in day['Heures']]

        max_temp = max(temperatures)
        min_temp = min(temperatures)
        somme_pluie = sum(pluviometries)

        variations_temperature = []
        variations_pluie = []

        for heure in day['Heures']:
            if 'historique_temperature' in heure and heure['historique_temperature'] != 0:
                variations_temperature.append(heure['historique_temperature'])
            if 'historique_pluie' in heure and heure['historique_pluie'] != 0:
                variations_pluie.append(heure['historique_pluie'])

        variation_temp = sum(variations_temperature) if variations_temperature else 0
        variation_pluie = sum(variations_pluie) if variations_pluie else 0

        results.append({
            'Date': day['Date'],
            'Température Max (°C)': max_temp,
            'Température Min (°C)': min_temp,
            'Somme Pluviométrie (mm)': round(somme_pluie, 1),
            'Variation Température (°C)': round(variation_temp, 1),
            'Variation Pluviométrie (mm)': round(variation_pluie, 1),
            'Heures': day['Heures']
        })

    return jsonify(results)

@app.route('/historique_meteo/<date>', methods=['GET'])
@role_required("user")
def get_historique_meteo(date):
    try:
        selected_date = datetime.strptime(date, '%Y-%m-%d')
        years = [selected_date.year, selected_date.year - 1, selected_date.year - 2, selected_date.year - 3, selected_date.year - 4, selected_date.year - 5]
        month = selected_date.month
        day = selected_date.day

        result = {}

        for year in years:
            start_of_month = datetime(year, month, 1)
            end_of_month = (start_of_month + timedelta(days=32)).replace(day=1) - timedelta(days=1)

            if year == selected_date.year:
                end_of_month = selected_date

            monthly_data = list(db.donnees_meteo.find({
                'Date': {
                    '$gte': start_of_month,
                    '$lte': end_of_month
                }
            }))

            if monthly_data:
                total_precipitations = sum([entry.get('Précipitations (mm)', 0) for entry in monthly_data if entry.get('Précipitations (mm)', 0) is not None])
                max_temperature = max([entry.get('Température max (°C)', 0) for entry in monthly_data if entry.get('Température max (°C)', 0) is not None])
                min_temperature = min([entry.get('Température min (°C)', 0) for entry in monthly_data if entry.get('Température min (°C)', 0) is not None])
                
                if len([entry.get('Température max (°C)', 0) for entry in monthly_data]) > 0:
                    avg_temperature = sum([entry.get('Température max (°C)', 0) for entry in monthly_data]) / len(monthly_data)
                    avg_temperature = round(avg_temperature, 1)
                else:
                    avg_temperature = 0

                if year == selected_date.year:
                    result[year] = {
                        'Précipitations Totales Mois (mm)': round(total_precipitations, 1),
                        'Température Max Mois (°C)': round(max_temperature, 1),
                        'Température Min Mois (°C)': round(min_temperature, 1),
                        'Température Moyenne Mois (°C)': avg_temperature,
                        'message': 'Données mensuelles seulement pour le mois en cours'
                    }
                else:
                    daily_data = db.donnees_meteo.find_one({'Date': datetime(year, month, day)})
                    if daily_data:
                        result[year] = {
                            'Précipitations Totales Mois (mm)': round(total_precipitations, 1),
                            'Température Max Mois (°C)': round(max_temperature, 1),
                            'Température Min Mois (°C)': round(min_temperature, 1),
                            'Température Moyenne Mois (°C)': avg_temperature,
                            'Température Jour (°C)': {
                                'max': round(daily_data.get('Température max (°C)', 0), 1),
                                'min': round(daily_data.get('Température min (°C)', 0), 1)
                            },
                            'Précipitations Jour (mm)': round(daily_data.get('Précipitations (mm)', 0), 1)
                        }
                    else:
                        result[year] = {
                            'Précipitations Totales Mois (mm)': round(total_precipitations, 1),
                            'Température Max Mois (°C)': round(max_temperature, 1),
                            'Température Min Mois (°C)': round(min_temperature, 1),
                            'Température Moyenne Mois (°C)': avg_temperature,
                            'message': f'Pas de données pour le jour {day}/{month}/{year}'
                        }
            else:
                result[year] = {
                    'message': f'Aucune donnée disponible pour {month}/{year}'
                }

        result_cleaned = json.loads(json.dumps(result).replace('NaN', '0'))
        return jsonify(result_cleaned)

    except Exception as e:
        print(f"Erreur lors de la récupération des données historiques: {e}")
        return jsonify({'error': 'Server error'}), 500
    
@app.route('/meteo_previsions_6h', methods=['GET'])
@role_required("user")
def get_meteo_previsions_6h():
    now = datetime.now()
    six_hours_from_now = now + timedelta(hours=6)

    # Rechercher les prévisions pour la journée actuelle
    previsions_today = db.meteo_previsions.find_one({
        'Date': now.strftime('%Y-%m-%d')
    })

    # Rechercher les prévisions pour le jour suivant si nécessaire
    previsions_tomorrow = None
    if six_hours_from_now.day != now.day:
        previsions_tomorrow = db.meteo_previsions.find_one({
            'Date': six_hours_from_now.strftime('%Y-%m-%d')
        })

    results = []

    # Filtrer les heures de la journée actuelle en respectant la limite des 6 heures
    if previsions_today and 'Heures' in previsions_today:
        for heure in previsions_today['Heures']:
            heure_str = heure['Heure']
            heure_obj = datetime.strptime(f"{previsions_today['Date']} {heure_str}", '%Y-%m-%d %H:%M')

            # Inclure les heures entre now et six_hours_from_now
            if now <= heure_obj < six_hours_from_now:
                results.append({
                    'Date': previsions_today['Date'],
                    'Heure': heure_str,
                    'Température (°C)': int(heure['Température (°C)']),
                    'Pluviométrie (mm)': float(heure['Pluviométrie (mm)']),
                    'Vent rafale (km/h)': int(heure['Vent rafale (km/h)'])
                })

    # Ajouter les heures du jour suivant si nécessaire
    if previsions_tomorrow and 'Heures' in previsions_tomorrow:
        for heure in previsions_tomorrow['Heures']:
            heure_str = heure['Heure']
            heure_obj = datetime.strptime(f"{previsions_tomorrow['Date']} {heure_str}", '%Y-%m-%d %H:%M')

            # Inclure les heures jusqu'à six_hours_from_now
            if heure_obj <= six_hours_from_now:
                results.append({
                    'Date': previsions_tomorrow['Date'],
                    'Heure': heure_str,
                    'Température (°C)': int(heure['Température (°C)']),
                    'Pluviométrie (mm)': float(heure['Pluviométrie (mm)']),
                    'Vent rafale (km/h)': int(heure['Vent rafale (km/h)'])
                })

    return jsonify(results)

@app.route('/sun_times', methods=['GET'])
@role_required("user")  # 🔥 Accessible à tous les utilisateurs authentifiés
def get_sun_times():
    """
    Retourne les heures de lever et de coucher du soleil en fonction de l'heure actuelle.
    La réponse est au format JSON.
    """
    # Définir les coordonnées GPS (Le Mans)
    latitude = 47.94904215730735
    longitude = 0.21130481133172585
    timezone_local = ZoneInfo("Europe/Paris")  # Fuseau horaire local

    # Récupérer la date et l'heure actuelles en UTC
    now_utc = datetime.now(timezone.utc)  # ✅ Utilisation correcte
    now_local = now_utc.astimezone(timezone_local)  # ✅ Conversion en heure locale

    # Obtenir les heures de lever et coucher du soleil pour aujourd'hui et demain
    today = now_local.date()
    tomorrow = today + timedelta(days=1)

    location = LocationInfo(latitude=latitude, longitude=longitude)
    
    # Calcul des heures de lever et coucher du soleil pour aujourd'hui et demain
    sun_today = sun(location.observer, date=today)
    sun_tomorrow = sun(location.observer, date=tomorrow)

    # Convertir les heures en fuseau horaire local
    sunrise_today = sun_today["sunrise"].astimezone(timezone_local)
    sunset_today = sun_today["sunset"].astimezone(timezone_local)

    sunrise_tomorrow = sun_tomorrow["sunrise"].astimezone(timezone_local)
    sunset_tomorrow = sun_tomorrow["sunset"].astimezone(timezone_local)

    # Déterminer quelles valeurs renvoyer
    if now_local < sunrise_today:
        # Avant le lever du soleil du jour -> On renvoie le lever et coucher du jour
        sunrise_next = sunrise_today
        sunset_next = sunset_today
    elif now_local < sunset_today:
        # Après le lever du soleil mais avant le coucher -> On renvoie coucher du jour et lever de demain
        sunrise_next = sunrise_tomorrow
        sunset_next = sunset_today
    else:
        # Après le coucher du soleil -> On renvoie lever et coucher de demain
        sunrise_next = sunrise_tomorrow
        sunset_next = sunset_tomorrow

    # Retourner le résultat en JSON
    return jsonify({
        "lever": sunrise_next.strftime("%Y-%m-%d %H:%M:%S"),
        "coucher": sunset_next.strftime("%Y-%m-%d %H:%M:%S")
    })

################################################################################
# TRAFFIC
################################################################################

app.register_blueprint(traffic_bp)
# app.register_blueprint(meteo_bp)

################################################################################
# DATA BILLETTERIE
################################################################################

@app.route('/get_counter', methods=['GET'])
@role_required("user")
def get_counter():
    event = request.args.get('event')
    year = request.args.get('year')  # Ex. "2025"
    
    if not event:
        return jsonify({"current": "N/A", "error": "Event parameter missing"}), 400
    if not year:
        return jsonify({"current": "N/A", "error": "Year parameter missing"}), 400

    # Chercher l'événement dans la collection "evenement"
    event_doc = db.evenement.find_one({"nom": event})
    if not event_doc:
        return jsonify({"current": "N/A", "error": "Event not found"}), 404

    # Extraire la clé skidata depuis le document de l'événement
    skidata = event_doc.get("skidata")
    if not skidata:
        return jsonify({"current": "N/A", "error": "Skidata not found for this event"}), 404

    # Rechercher le document le plus récent dans data_access en fonction de skidata et de l'année transmise
    counter_doc = db.data_access.find_one(
        {"counter_id": str(skidata), "year": str(year)},
        sort=[("timestamp", -1)]
    )
    if counter_doc:
        current_value = counter_doc.get("current", "N/A")
        return jsonify({"current": current_value})
    else:
        return jsonify({"current": "N/A"})
    
@app.route('/get_counter_max', methods=['GET'])
@role_required("user")
def get_counter_max():
    event = request.args.get('event')
    year = request.args.get('year')  # Ex. "2025"
    
    if not event:
        return jsonify({"current": "N/A", "error": "Event parameter missing"}), 400
    if not year:
        return jsonify({"current": "N/A", "error": "Year parameter missing"}), 400

    # Chercher l'événement dans la collection "evenement"
    event_doc = db.evenement.find_one({"nom": event})
    if not event_doc:
        return jsonify({"current": "N/A", "error": "Event not found"}), 404

    # Extraire la clé skidata depuis le document de l'événement
    skidata = event_doc.get("skidata")
    if not skidata:
        return jsonify({"current": "N/A", "error": "Skidata not found for this event"}), 404

    # Rechercher le document avec la valeur "current" la plus élevée dans data_access
    counter_doc = db.data_access.find_one(
        {"counter_id": str(skidata), "year": str(year)},
        sort=[("current", -1)]
    )
    
    if counter_doc:
        current_value = counter_doc.get("current", "N/A")
        return jsonify({"current": current_value})
    else:
        return jsonify({"current": "N/A"})


################################################################################
# AFFLUENCE PREVISIONNELLE
################################################################################

@app.route('/get_affluence', methods=['GET'])
@role_required("user")
def get_affluence():
    event = request.args.get("event")
    year = request.args.get("year")
    if not event or not year:
        return jsonify({"error": "Missing event or year"}), 400

    # Charger parametrages complet (data + tickets a la racine)
    doc = db['parametrages'].find_one({'event': event, 'year': year}, {'_id': 0})
    if not doc or 'data' not in doc:
        return jsonify({"days": [], "total_ventes": None})

    gh = doc['data'].get('globalHoraires', {})
    public_days = gh.get('dates', [])
    ticketing_config = gh.get('ticketing', [])
    race_raw = doc['data'].get('race') or gh.get('race')
    tickets = doc.get('tickets', {})
    products_data = tickets.get('products', {})
    last_update = tickets.get('lastUpdate')

    if not public_days or not ticketing_config:
        return jsonify({"days": [], "total_ventes": None, "last_update": last_update})

    # Parser la date de course courante
    race_date = None
    if race_raw:
        try:
            if isinstance(race_raw, str):
                race_date = datetime.fromisoformat(race_raw.replace('Z', '+00:00')).date()
            else:
                race_date = race_raw.date() if hasattr(race_raw, 'date') else None
        except Exception:
            race_date = None

    # ── Charger parametrages N-1 (ventes precedentes) ──
    prev_year_str = None
    prev_param = None
    prev_race_date = None
    prev_products_data = {}
    prev_ticketing_config = []
    prev_public_days = []
    current_year_int = int(year) if year.isdigit() else None

    if current_year_int:
        # Chercher le parametrage de l'annee precedente la plus recente
        prev_candidates = list(db['parametrages'].find(
            {'event': event, 'tickets': {'$exists': True}},
            {'year': 1, 'data.globalHoraires': 1, 'data.race': 1, 'tickets': 1, '_id': 0}
        ))
        for cand in sorted(prev_candidates, key=lambda c: str(c.get('year', '')), reverse=True):
            cand_year = cand.get('year', '')
            try:
                if int(cand_year) < current_year_int:
                    prev_param = cand
                    prev_year_str = cand_year
                    break
            except (ValueError, TypeError):
                continue

    if prev_param:
        prev_gh = prev_param.get('data', {}).get('globalHoraires', {})
        prev_ticketing_config = prev_gh.get('ticketing', [])
        prev_public_days = prev_gh.get('dates', [])
        prev_products_data = prev_param.get('tickets', {}).get('products', {})
        prev_race_raw = prev_param.get('data', {}).get('race') or prev_gh.get('race')
        if prev_race_raw:
            try:
                if isinstance(prev_race_raw, str):
                    prev_race_date = datetime.fromisoformat(prev_race_raw.replace('Z', '+00:00')).date()
                else:
                    prev_race_date = prev_race_raw.date() if hasattr(prev_race_raw, 'date') else None
            except Exception:
                prev_race_date = None

    # ── Charger historique_controle N-1 (pic presents) ──
    prev_hist_doc = None
    prev_hist_race_date = None
    prev_data_by_day = {}
    if race_date:
        prev_hist_candidates = list(db['historique_controle'].find(
            {'type': 'frequentation', 'event': event},
            sort=[('year', -1)]
        ))
        for cand in prev_hist_candidates:
            cand_year = cand.get('year')
            if current_year_int and isinstance(cand_year, (int, float)) and int(cand_year) < current_year_int:
                prev_hist_doc = cand
                break

        if prev_hist_doc:
            prev_hist_year = prev_hist_doc.get('year')
            prev_hist_race_raw = prev_hist_doc.get('race')
            if not prev_hist_race_raw:
                portes_doc = db['historique_controle'].find_one(
                    {'type': 'portes', 'event': event, 'year': prev_hist_year},
                    {'_id': 0, 'race': 1}
                )
                if portes_doc:
                    prev_hist_race_raw = portes_doc.get('race')
            try:
                if isinstance(prev_hist_race_raw, str):
                    prev_hist_race_date = datetime.fromisoformat(prev_hist_race_raw.replace('Z', '+00:00')).date()
                else:
                    prev_hist_race_date = prev_hist_race_raw.date() if hasattr(prev_hist_race_raw, 'date') else None
            except Exception:
                prev_hist_race_date = None

            if prev_hist_race_date and prev_hist_doc.get('data'):
                from collections import defaultdict
                day_records = defaultdict(list)
                for rec in prev_hist_doc['data']:
                    rec_date = rec.get('date')
                    if isinstance(rec_date, str):
                        day_key = rec_date[:10]
                    elif hasattr(rec_date, 'strftime'):
                        day_key = rec_date.strftime('%Y-%m-%d')
                    else:
                        continue
                    day_records[day_key].append(rec)
                prev_data_by_day = dict(day_records)

    # ── Construire la reponse par jour ──
    result_days = []
    total_ventes = 0
    total_delta = 0
    total_ventes_prev = 0
    JOURS_FR = {0: 'Lun', 1: 'Mar', 2: 'Mer', 3: 'Jeu', 4: 'Ven', 5: 'Sam', 6: 'Dim'}

    for day_info in public_days:
        day_str = day_info.get('date', '')
        try:
            day_date = datetime.strptime(day_str, '%Y-%m-%d').date()
        except Exception:
            continue

        label = JOURS_FR.get(day_date.weekday(), '') + ' ' + day_date.strftime('%d/%m')

        # Ventes N pour ce jour
        day_ventes = 0
        day_delta = 0
        for tc in ticketing_config:
            days_scope = tc.get('days', [])
            prods = tc.get('products', [])
            applies = (days_scope == 'all') or (day_str in days_scope)
            if not applies:
                continue
            for pname in prods:
                pdata = products_data.get(pname)
                if not pdata:
                    continue
                day_ventes += pdata.get('ventes', 0)
                hist = pdata.get('history', [])
                if len(hist) >= 2:
                    day_delta += pdata.get('ventes', 0) - hist[-2].get('ventes', 0)

        total_ventes += day_ventes
        total_delta += day_delta

        # Ventes N-1 pour le jour equivalent (meme offset depuis la course)
        ventes_prev = None
        if race_date and prev_race_date and prev_ticketing_config:
            offset_days = (day_date - race_date).days
            target_prev_date = prev_race_date + timedelta(days=offset_days)
            target_prev_str = target_prev_date.strftime('%Y-%m-%d')

            day_ventes_prev = 0
            found_prev = False
            for tc in prev_ticketing_config:
                days_scope = tc.get('days', [])
                prods = tc.get('products', [])
                applies = (days_scope == 'all') or (target_prev_str in days_scope)
                if not applies:
                    continue
                for pname in prods:
                    pdata = prev_products_data.get(pname)
                    if not pdata:
                        continue
                    day_ventes_prev += pdata.get('ventes', 0)
                    found_prev = True

            if found_prev:
                ventes_prev = day_ventes_prev
                total_ventes_prev += day_ventes_prev

        # Pic N-1 depuis historique_controle
        pic_prev = None
        if race_date and prev_hist_race_date:
            offset_days = (day_date - race_date).days
            target_prev_date = prev_hist_race_date + timedelta(days=offset_days)
            target_key = target_prev_date.strftime('%Y-%m-%d')
            if target_key in prev_data_by_day:
                records = prev_data_by_day[target_key]
                pic_prev = max((r.get('present', 0) for r in records), default=0)

        result_days.append({
            "date": day_str,
            "label": label,
            "ventes": day_ventes,
            "delta": day_delta,
            "ventes_prev": ventes_prev,
            "pic_prev": pic_prev,
            "prev_year": prev_year_str
        })

    return jsonify({
        "days": result_days,
        "total_ventes": total_ventes,
        "total_delta": total_delta,
        "total_ventes_prev": total_ventes_prev if total_ventes_prev else None,
        "last_update": last_update,
        "prev_year": prev_year_str
    })


################################################################################
# MONITOR TV
################################################################################

@app.route('/general_stat', methods=['GET'])
@role_required("user")
def general_stat():
    event = request.args.get("event")
    year = request.args.get("year")
    
    if not event or not year:
        return "Missing event or year parameter", 400

    # Préparation de la structure des statistiques avec des placeholders
    stats = {
        "current_present": "N/A",              # Nombre de présents actuels
        "current_present_gauge": "N/A",          # Jauge par rapport à l'affluence possible
        "max_present_day": "N/A",                # Maximum présent de la journée
        "max_present_event": "N/A",              # Maximum présent à l'événement
        "total_entries": "N/A",                  # Nombre d'entrées depuis le début de l'événement
        "unique_visitors": "N/A",                # Nombre de visiteurs uniques depuis le début
        "previous_year_max": "N/A",              # Maximum de l'année précédente
        "previous_year_current": "N/A"           # Chiffre de l'année précédente au même moment
    }
    
    return render_template("general-stats.html", stats=stats, event=event, year=year)

@app.route('/update_general_stat', methods=['GET'])
@role_required("user")
def update_general_stat():
    event = request.args.get("event")
    year = request.args.get("year")
    
    if not event or not year:
        return jsonify({"error": "Missing event or year parameter"}), 400

    # Préparation des statistiques actualisées (pour l'instant des placeholders "N/A")
    stats = {
        "current_present": "N/A",
        "current_present_gauge": "N/A",
        "max_present_day": "N/A",
        "max_present_event": "N/A",
        "total_entries": "N/A",
        "unique_visitors": "N/A",
        "previous_year_max": "N/A",
        "previous_year_current": "N/A"
    }
    
    return jsonify(stats)

@app.route('/terrains', methods=['GET'])
@role_required("user")
def parkings():
    event = request.args.get('event')
    year = request.args.get('year')
    if not event or not year:
        return "Missing event or year parameter", 400

    # Exemple de structure de données avec des placeholders pour chaque parking/aire d'accueil
    terrains_data = [
        {"id": "parking-a", "name": "Parking A", "scans": "N/A", "tickets": "N/A", "gauge": "N/A"},
        {"id": "parking-b", "name": "Parking B", "scans": "N/A", "tickets": "N/A", "gauge": "N/A"},
        {"id": "aire-accueil", "name": "Aire d'Accueil", "scans": "N/A", "tickets": "N/A", "gauge": "N/A"}
    ]
    
    return render_template("terrains.html", terrains=terrains_data, event=event, year=year)

@app.route('/update_parkings', methods=['GET'])
@role_required("user")
def update_parkings():
    event = request.args.get('event')
    year = request.args.get('year')
    if not event or not year:
        return jsonify({"error": "Missing event or year parameter"}), 400

    # Remplacez ces valeurs par vos requêtes sur la base de données
    terrains_data = [
        {"id": "parking-a", "scans": "N/A", "tickets": "N/A", "gauge": "N/A"},
        {"id": "parking-b", "scans": "N/A", "tickets": "N/A", "gauge": "N/A"},
        {"id": "aire-accueil", "scans": "N/A", "tickets": "N/A", "gauge": "N/A"}
    ]
    return jsonify({"parkings": terrains_data})

@app.route('/doors', methods=['GET'])
@role_required("user")
def doors():
    event = request.args.get('event')
    year = request.args.get('year')
    if not event or not year:
        return "Missing event or year parameter", 400

    # Exemple de structure pour les portes avec des placeholders
    doors_data = [
        {"id": "door-1", "name": "Porte 1", "ranking": "N/A", "total_entries": "N/A", "rate": "N/A", "color": "N/A"},
        {"id": "door-2", "name": "Porte 2", "ranking": "N/A", "total_entries": "N/A", "rate": "N/A", "color": "N/A"},
        {"id": "door-3", "name": "Porte 3", "ranking": "N/A", "total_entries": "N/A", "rate": "N/A", "color": "N/A"}
    ]
    
    return render_template("doors.html", doors=doors_data, event=event, year=year)

@app.route('/update_doors', methods=['GET'])
@role_required("user")
def update_doors():
    event = request.args.get('event')
    year = request.args.get('year')
    if not event or not year:
        return jsonify({"error": "Missing event or year parameter"}), 400

    # Remplacez ces valeurs par vos requêtes sur la base de données
    doors_data = [
        {"id": "door-1", "ranking": "N/A", "total_entries": "N/A", "rate": "N/A", "color": "N/A"},
        {"id": "door-2", "ranking": "N/A", "total_entries": "N/A", "rate": "N/A", "color": "N/A"},
        {"id": "door-3", "ranking": "N/A", "total_entries": "N/A", "rate": "N/A", "color": "N/A"}
    ]
    return jsonify({"doors": doors_data})

################################################################################
# Gestion de la configuration des tâches automatiques cockpit
################################################################################

COL_TODOS = db['todos']  # schema: { type:str, todos:[str], createdAt, updatedAt }

# Helpers

def _pub(doc):
    if not doc: return None
    d = dict(doc)
    d['_id'] = str(d['_id'])
    return d

@app.route('/api/todo-sets', methods=['GET'])
@role_required("manager")

def list_todo_sets():
    q = {}
    t = request.args.get('type')
    if t: q['type'] = t
    items = list(COL_TODOS.find(q).sort([('type', 1)]))
    return jsonify([_pub(x) for x in items])

@app.route('/api/todo-sets/<id>', methods=['GET'])
@role_required("manager")

def get_todo_set(id):
    doc = COL_TODOS.find_one({'_id': ObjectId(id)})
    if not doc: return jsonify({'error':'Not found'}), 404
    return jsonify(_pub(doc))

@app.route('/api/todo-sets', methods=['POST'])
@role_required("manager")
@csrf.exempt

def create_todo_set():
    data = request.get_json(force=True) or {}
    typ = (data.get('type') or '').strip()
    todos = data.get('todos') or []
    if not typ:
        return jsonify({'error':'type is required'}), 400
    # ensure list of strings
    todos = [str(x).strip() for x in todos if str(x).strip()]
    doc = {
        'type': typ,
        'todos': todos,
        'createdAt': datetime.now(timezone.utc),
        'updatedAt': datetime.now(timezone.utc),
    }
    ins = COL_TODOS.insert_one(doc)
    doc['_id'] = str(ins.inserted_id)
    return jsonify(doc), 201

@app.route('/api/todo-sets/<id>', methods=['PUT'])
@role_required("manager")
@csrf.exempt

def update_todo_set(id):
    data = request.get_json(force=True) or {}
    patch = {}
    if 'type' in data:
        patch['type'] = (data.get('type') or '').strip()
    if 'todos' in data:
        todos = data.get('todos') or []
        patch['todos'] = [str(x).strip() for x in todos if str(x).strip()]
    if not patch: return jsonify({'error':'Empty update'}), 400
    patch['updatedAt'] = datetime.now(timezone.utc)
    res = COL_TODOS.find_one_and_update({'_id': ObjectId(id)}, {'$set': patch}, return_document=True)
    if not res: return jsonify({'error':'Not found'}), 404
    return jsonify(_pub(res))

@app.route('/api/todo-sets/<id>', methods=['DELETE'])
@role_required("manager")
@csrf.exempt

def delete_todo_set(id):
    r = COL_TODOS.delete_one({'_id': ObjectId(id)})
    if r.deleted_count == 0: return jsonify({'error':'Not found'}), 404
    return jsonify({'ok': True})

# (optionnel) supprimer en masse
@app.route('/api/todo-sets/bulk-delete', methods=['POST'])
@role_required("admin")
@csrf.exempt

def bulk_delete_todo_sets():
    data = request.get_json(force=True) or {}
    ids = [ObjectId(x) for x in (data.get('ids') or []) if x]
    if not ids: return jsonify({'error':'No ids'}), 400
    r = COL_TODOS.delete_many({'_id': {'$in': ids}})
    return jsonify({'deleted': r.deleted_count})

# (optionnel) index d’un item dans le tableau
@app.route('/api/todo-sets/<id>/item/<int:idx>', methods=['DELETE'])
@csrf.exempt
@role_required("admin")

def delete_todo_item(id, idx):
    doc = COL_TODOS.find_one({'_id': ObjectId(id)})
    if not doc: return jsonify({'error':'Not found'}), 404
    arr = list(doc.get('todos') or [])
    if not (0 <= idx < len(arr)):
        return jsonify({'error':'Index out of range'}), 400
    arr.pop(idx)
    res = COL_TODOS.find_one_and_update(
        {'_id': ObjectId(id)}, {'$set': {'todos': arr, 'updatedAt': datetime.now(timezone.utc)}}, return_document=True
    )
    return jsonify(_pub(res))

@app.route('/config/todos')
@role_required("admin")
def edit_todo_sets_page():
    payload = getattr(request, 'user_payload', {})
    user_roles = payload.get("roles", [])
    user_firstname = payload.get("firstname", "")
    user_lastname = payload.get("lastname", "")
    user_email = payload.get("email", "")
    return render_template('edit.html', user_roles=user_roles,
                           user_firstname=user_firstname, user_lastname=user_lastname,
                           user_email=user_email)

################################################################################
# Gestion des groupes et utilisateurs cockpit
################################################################################

@app.route('/api/cockpit-users', methods=['GET'])
@role_required("admin")
def list_cockpit_users():
    users = list(db['users'].find(
        {"applications": "Cockpit"},
        {"prenom": 1, "nom": 1, "email": 1, "titre": 1, "service": 1, "roles_by_app.cockpit": 1}
    ))
    # Joindre les groupes
    user_groups_map = {}
    for ug in COL_USER_GROUPS.find():
        user_groups_map[str(ug['user_id'])] = [str(g) for g in (ug.get('groups') or [])]
    result = []
    for u in users:
        uid = str(u['_id'])
        result.append({
            '_id': uid,
            'prenom': u.get('prenom', ''),
            'nom': u.get('nom', ''),
            'email': u.get('email', ''),
            'titre': u.get('titre', ''),
            'service': u.get('service', ''),
            'cockpit_role': (u.get('roles_by_app') or {}).get('cockpit', 'user'),
            'groups': user_groups_map.get(uid, [])
        })
    return jsonify(result)

@app.route('/api/cockpit-users/<uid>/groups', methods=['PUT'])
@role_required("admin")
@csrf.exempt
def set_user_groups(uid):
    data = request.get_json(force=True) or {}
    group_ids = data.get('groups') or []
    # Valider que les groupes existent
    oids = [ObjectId(g) for g in group_ids]
    existing = COL_GROUPS.count_documents({"_id": {"$in": oids}})
    if existing != len(oids):
        return jsonify({"error": "Un ou plusieurs groupes invalides"}), 400
    COL_USER_GROUPS.update_one(
        {"user_id": ObjectId(uid)},
        {"$set": {"user_id": ObjectId(uid), "groups": oids}},
        upsert=True
    )
    return jsonify({"ok": True})

@app.route('/api/groups', methods=['GET'])
@role_required("admin")
def list_groups():
    groups = list(COL_GROUPS.find().sort([('name', 1)]))
    # Compter les membres par groupe
    all_ug = list(COL_USER_GROUPS.find())
    for g in groups:
        gid = g['_id']
        count = sum(1 for ug in all_ug if gid in (ug.get('groups') or []))
        g['member_count'] = count
    return jsonify([_pub(g) for g in groups])

@app.route('/api/groups', methods=['POST'])
@role_required("admin")
@csrf.exempt
def create_group():
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"error": "Le nom est requis"}), 400
    # Verifier unicite
    if COL_GROUPS.find_one({"name": name}):
        return jsonify({"error": "Un groupe avec ce nom existe deja"}), 409
    doc = {
        'name': name,
        'description': (data.get('description') or '').strip(),
        'color': (data.get('color') or '#6366f1').strip(),
        'createdAt': datetime.now(timezone.utc),
        'updatedAt': datetime.now(timezone.utc),
    }
    ins = COL_GROUPS.insert_one(doc)
    doc['_id'] = str(ins.inserted_id)
    return jsonify(doc), 201

@app.route('/api/groups/<gid>', methods=['PUT'])
@role_required("admin")
@csrf.exempt
def update_group(gid):
    data = request.get_json(force=True) or {}
    patch = {}
    if 'name' in data:
        patch['name'] = (data['name'] or '').strip()
    if 'description' in data:
        patch['description'] = (data['description'] or '').strip()
    if 'color' in data:
        patch['color'] = (data['color'] or '').strip()
    if not patch:
        return jsonify({"error": "Rien a modifier"}), 400
    patch['updatedAt'] = datetime.now(timezone.utc)
    res = COL_GROUPS.find_one_and_update(
        {"_id": ObjectId(gid)}, {"$set": patch}, return_document=True
    )
    if not res:
        return jsonify({"error": "Groupe introuvable"}), 404
    return jsonify(_pub(res))

@app.route('/api/groups/<gid>', methods=['DELETE'])
@role_required("admin")
@csrf.exempt
def delete_group(gid):
    oid = ObjectId(gid)
    r = COL_GROUPS.delete_one({"_id": oid})
    if r.deleted_count == 0:
        return jsonify({"error": "Groupe introuvable"}), 404
    # Retirer ce groupe de tous les user_groups
    COL_USER_GROUPS.update_many({}, {"$pull": {"groups": oid}})
    return jsonify({"ok": True})

################################################################################
# Webhook & Merge Config
################################################################################

WEBHOOK_TOKEN = os.getenv('WEBHOOK_TOKEN', 'dev-webhook-token-change-me')
if IS_PROD and WEBHOOK_TOKEN == 'dev-webhook-token-change-me':
    logger.warning("WEBHOOK_TOKEN non configure en production!")


@app.route('/webhook/parametrage-updated', methods=['POST'])
@csrf.exempt
def webhook_parametrage_updated():
    """Webhook appele par groundmaster quand un parametrage est modifie."""
    token = request.headers.get('X-Webhook-Token', '')
    if token != WEBHOOK_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    event = data.get('event')
    year = data.get('year')
    if not event or not year:
        return jsonify({"error": "event et year requis"}), 400

    try:
        result = run_merge(db, event, str(year))
        return jsonify(result)
    except Exception as e:
        logger.error(f"Erreur webhook merge: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/merge-config', methods=['GET'])
@role_required("admin")
def list_merge_configs():
    """Liste toutes les configs de merge + detection des categories non configurees."""
    configs = list(db.merge_config.find({}, {"_id": 0}))
    configured_keys = {c["data_key"] for c in configs}

    # Detecter les categories groundmaster non configurees
    gm_cats = list(db.groundmaster_categories.find({}, {"_id": 0}))
    unconfigured = []
    for cat in gm_cats:
        dk = cat.get("dataKey")
        if dk and dk not in configured_keys:
            unconfigured.append({
                "data_key": dk,
                "label": cat.get("label", dk),
                "mode": cat.get("mode", "schedule"),
                "configured": False,
            })

    return jsonify({"configs": configs, "unconfigured": unconfigured})


@app.route('/api/merge-config/<data_key>', methods=['PUT'])
@role_required("admin")
@csrf.exempt
def update_merge_config(data_key):
    """Cree ou met a jour la config de merge pour une categorie."""
    data = request.get_json(silent=True) or {}
    data["data_key"] = data_key

    allowed_fields = {
        "data_key", "label", "enabled", "mode",
        "activity_label", "timeline_category", "timeline_type",
        "department", "access_types", "todos_type", "vignette_fields",
    }
    clean = {k: v for k, v in data.items() if k in allowed_fields}

    db.merge_config.update_one(
        {"data_key": data_key},
        {"$set": clean},
        upsert=True
    )
    return jsonify({"ok": True})


@app.route('/api/merge-config/<data_key>', methods=['DELETE'])
@role_required("admin")
@csrf.exempt
def delete_merge_config(data_key):
    """Supprime la config de merge pour une categorie."""
    db.merge_config.delete_one({"data_key": data_key})
    return jsonify({"ok": True})


@app.route('/api/run-merge', methods=['POST'])
@role_required("admin")
def run_merge_manual():
    """Lance le merge manuellement depuis l'UI admin."""
    data = request.get_json(silent=True) or {}
    event = data.get('event')
    year = data.get('year')
    if not event or not year:
        return jsonify({"error": "event et year requis"}), 400

    try:
        result = run_merge(db, event, str(year))
        return jsonify(result)
    except Exception as e:
        logger.error(f"Erreur run_merge: {e}")
        return jsonify({"error": str(e)}), 500


################################################################################
# Exécution
################################################################################

if __name__ == "__main__":
    # Validation pour éviter debug=True en production
    if not DEV_MODE and app.debug:
        raise RuntimeError("L'application ne doit pas tourner en mode debug en production.")

    # Lancement de l'application
    if DEV_MODE:
        logger.info(f"[DEV] Running TITAN Home in development mode on port {PORT}")
        app.run(debug=True, use_reloader=True, host="0.0.0.0", port=PORT)
    else:
        logger.warning(f"[PROD] Running TITAN Home on port {PORT}")
        serve(app, host="0.0.0.0", port=PORT)
