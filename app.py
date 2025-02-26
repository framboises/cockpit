# Standard library imports
import os
import re
import json
import csv
import bson
import logging
import chardet
import jwt
import math
import uuid
from datetime import datetime, timedelta, timezone
from math import atan2, degrees, sqrt

# Third-party imports
from flask import (
    Flask, jsonify, render_template, send_from_directory, request, 
    redirect, url_for, flash, session, abort, make_response
)
from flask_cors import CORS
from flask_talisman import Talisman
from flask_wtf.csrf import CSRFProtect
from functools import wraps
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun
from PIL import Image
from pymongo import MongoClient
from werkzeug.utils import secure_filename, safe_join
from shapely.geometry import Point, shape
from bson.objectid import ObjectId
from waitress import serve

# Local application imports
from traffic import traffic_bp

################################################################################
# Configuration
################################################################################

DEV_MODE = True
CODING = True
PORT = 5008 if DEV_MODE else 4008
logging.basicConfig(level=logging.INFO if DEV_MODE else logging.WARNING)
logger = logging.getLogger(__name__)

DEV_URL = "http://dev.safe.lemans.org"
PROD_URL = "http://safe.lemans.org"

BASE_URL = DEV_URL if DEV_MODE else PROD_URL

################################################################################
# Initialisation Flask
################################################################################

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'HacB6vFEPpU3M04zMIIcuNtebrAvRME9T2vyqcYjGrQ')

JWT_SECRET = os.getenv('JWT_SECRET', 'qXyKGSrVz2wVNhOep4tALcRzCzbkgaVFVfNqtKJk0YY')
JWT_ALGORITHM = 'HS256'

UPLOAD_FOLDER = './uploads'
ALLOWED_EXTENSIONS = {'json', 'geojson', 'csv', 'bson'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['TEMPLATES_AUTO_RELOAD'] = True
csrf = CSRFProtect(app)

# Validation stricte pour la clé secrète en production
if not DEV_MODE and app.config['SECRET_KEY'] == 'HacB6vFEPpU3M04zMIIcuNtebrAvRME9T2vyqcYjGrQ':
    raise ValueError("SECRET_KEY must be set securely in production!")

# Connexion à MongoDB
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)

# Sélection dynamique de la base de données
db_name = 'titan_dev' if DEV_MODE else 'titan'
db = client[db_name]

CORS(app)  # Activer CORS pour toutes les routes

################################################################################
# Contrôle d'accès
################################################################################

ROLE_HIERARCHY = {
    "user": 1,
    "manager": 2,
    "admin": 3,
}

def role_required(required_role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if CODING:
                # En mode développement, on simule un utilisateur avec des permissions maximales
                logger.info(f"[DEV_MODE] Bypassing authentication for role '{required_role}'")
                request.user_payload = {
                    "apps": ["looker", "shiftsolver", "tagger"],
                    "roles": ["admin"]
                }
                return f(*args, **kwargs)

            token = request.cookies.get("access_token")
            if not token:
                logger.info("Access token manquant. Redirection vers le portail.")
                redirect_url = f"{BASE_URL}/home?message=Authentification requise pour accéder à l'application&category=error"
                return redirect(redirect_url)

            try:
                payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
                logger.info(f"Utilisateur authentifié : {payload}")
            except jwt.ExpiredSignatureError:
                redirect_url = f"{BASE_URL}/home?message=Votre session a expiré. Veuillez vous reconnecter.&category=warning"
                return redirect(redirect_url)
            except jwt.InvalidTokenError:
                redirect_url = f"{BASE_URL}/home?message=Authentification invalide. Veuillez vous reconnecter.&category=error"
                return redirect(redirect_url)

            # Vérifier si l'utilisateur a accès à l'application
            user_apps = payload.get("apps", [])
            normalized_user_apps = [
                re.sub(r'[^a-z0-9_-]', '', app.lower().replace(" ", ""))
                for app in user_apps
            ]  # Normaliser les noms d'applications

            if "looker" not in normalized_user_apps:
                logger.warning("Accès refusé à Tagger pour cet utilisateur.")
                redirect_url = f"{BASE_URL}/home?message=Vous n'avez pas les droits nécessaires pour accéder à cette application.&category=error"
                return redirect(redirect_url)

            user_roles = payload.get("roles", [])
            max_user_role_level = max([ROLE_HIERARCHY.get(role, 0) for role in user_roles])

            if max_user_role_level < ROLE_HIERARCHY.get(required_role, 0):
                flash(f"Accès interdit : cette fonctionnalité requiert un rôle '{required_role}'.", "error")
                return redirect(request.referrer or "/")

            request.user_payload = payload
            return f(*args, **kwargs)
        return decorated_function
    return decorator

################################################################################
# Routes Flask
################################################################################

def clean_collection_name(name):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', name)

@app.route("/logout_redirect")
def logout_redirect():
    logger.info("Tentative de suppression du cookie access_token.")
    
    # Utilisation de BASE_URL pour l'URL cible
    target_url = f"{BASE_URL}/login"
    
    response = make_response(redirect(target_url))
    response.set_cookie(
        "access_token",
        "",
        expires=0,
        domain=".safe.lemans.org",
        path="/"
    )
    logger.info(f"Cookie access_token supprimé. Redirection vers {target_url}.")
    return response

@app.route("/")
@role_required("user")
def index():
    # Récupérer l'info stockée dans request.user_payload si besoin
    payload = getattr(request, 'user_payload', {})
    user_roles = payload.get("roles", [])
    user_apps = payload.get("apps", [])
    return render_template("index.html", user_roles=user_roles, user_apps=user_apps)

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
# ROUTES FLASK
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