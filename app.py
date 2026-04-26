# Standard library imports
import os
import re
import json
import logging
import uuid
import subprocess
import jwt
from datetime import datetime, timedelta, timezone

# Third-party imports
from flask import (
    Flask, jsonify, render_template, send_from_directory, request, 
    redirect, url_for, flash, session, abort, make_response
)
from flask_cors import CORS
from flask_wtf.csrf import CSRFProtect, CSRFError
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
from analyse_ops import analyse_ops_bp
from anoloc import anoloc_bp
from anpr import anpr_bp
from field import field_bp
from vision_admin import vision_admin_bp
from routing import routing_bp
from cameras import cameras_bp
import pcorg_summary

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
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'CHANGE_ME_IN_DEV')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=not DEV_MODE,
    SESSION_COOKIE_SAMESITE="Lax",
)

JWT_SECRET = os.getenv('JWT_SECRET', 'CHANGE_ME_IN_DEV')
JWT_ALGORITHM = 'HS256'

if IS_PROD and CODING:
    raise ValueError("CODING must be disabled in production!")

UPLOAD_FOLDER = './uploads'
ALLOWED_EXTENSIONS = {'json', 'geojson', 'csv', 'bson'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['TEMPLATES_AUTO_RELOAD'] = True
csrf = CSRFProtect(app)

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    if request.path.startswith('/api/'):
        return jsonify({"error": "CSRF token manquant ou invalide"}), 400
    return e.get_body(), 400

# Validation stricte pour la clé secrète en production
if not DEV_MODE and not os.getenv('SECRET_KEY'):
    raise ValueError("SECRET_KEY must be set via environment variable in production!")
if not DEV_MODE and not os.getenv('JWT_SECRET'):
    raise ValueError("JWT_SECRET must be set via environment variable in production!")

# Connexion à MongoDB
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)

# Sélection dynamique de la base de données
db_name = 'titan_dev' if DEV_MODE else 'titan'
db = client[db_name]

CORS(app)  # Activer CORS pour toutes les routes

# Cache noms utilisateurs (email -> {firstname, lastname}), evite un find par requete
_user_name_cache = {}

# Collections cockpit groupes/user-groups
COL_GROUPS = db['cockpit_groups']
COL_USER_GROUPS = db['cockpit_user_groups']
COL_ALERT_HISTORY = db['cockpit_alert_history']
COL_ALERT_DEFS = db['cockpit_alert_definitions']
COL_ACTIVE_ALERTS = db['cockpit_active_alerts']
COL_ANPR_WATCHLIST = db['cockpit_anpr_watchlist']
COL_GROUPS.create_index("name", unique=True)
COL_USER_GROUPS.create_index("user_id", unique=True)
COL_ALERT_HISTORY.create_index("createdAt", expireAfterSeconds=7*24*3600)  # TTL 7 jours
COL_ALERT_DEFS.create_index("slug", unique=True)
COL_ACTIVE_ALERTS.create_index("expiresAt", expireAfterSeconds=0)  # TTL
COL_ACTIVE_ALERTS.create_index("dedup_key", unique=True, sparse=True)
COL_ACTIVE_ALERTS.create_index("definition_slug")
COL_ANPR_WATCHLIST.create_index("plate", unique=True)

# Collections WhatsApp (WAHA)
COL_WA_GROUPS = db['cockpit_wa_groups']
COL_WA_CONTACTS = db['cockpit_wa_contacts']
COL_WA_HISTORY = db['cockpit_wa_send_history']
COL_WA_CONFIG = db['cockpit_wa_config']
COL_WA_GROUPS.create_index("group_id", unique=True)
COL_WA_CONTACTS.create_index("phone", unique=True)
COL_WA_HISTORY.create_index("createdAt", expireAfterSeconds=30*24*3600)
COL_WA_HISTORY.create_index("alert_dedup_key")
COL_WA_HISTORY.create_index("sentAt", background=True)

# Groupes systeme (non supprimables)
DEFAULT_GROUP_NAME = "__default__"
ADMIN_GROUP_NAME = "__admin__"
SYSTEM_GROUP_NAMES = {DEFAULT_GROUP_NAME, ADMIN_GROUP_NAME}

COL_GROUPS.update_one(
    {"name": DEFAULT_GROUP_NAME},
    {"$setOnInsert": {
        "name": DEFAULT_GROUP_NAME,
        "description": "Blocs visibles par defaut pour les utilisateurs sans groupe",
        "color": "#94a3b8",
        "allowed_blocks": None,
        "is_default": True,
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }},
    upsert=True
)
COL_GROUPS.update_one(
    {"name": ADMIN_GROUP_NAME},
    {"$setOnInsert": {
        "name": ADMIN_GROUP_NAME,
        "description": "Apparence de la pillule Admin dans le header",
        "color": "#ef4444",
        "allowed_blocks": None,
        "is_default": True,
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }},
    upsert=True
)

# Seed des definitions d'alertes
_ALERT_SEEDS = [
    {
        "slug": "opening",
        "name": "Ouverture imminente",
        "description": "Alerte 30 min avant l'ouverture au public",
        "icon": "door_open",
        "color": "#f59e0b",
        "detection_type": "schedule_proximity",
        "params": {"minutes_before": 30, "schedule_event": "open"},
        "enabled": True,
        "groups": [],
        "priority": 1,
    },
    {
        "slug": "opened",
        "name": "Site ouvert",
        "description": "Le site est ouvert au public",
        "icon": "door_front",
        "color": "#22c55e",
        "detection_type": "schedule_transition",
        "params": {"transition": "open"},
        "enabled": True,
        "groups": [],
        "priority": 2,
    },
    {
        "slug": "closing",
        "name": "Fermeture imminente",
        "description": "Alerte 30 min avant la fermeture au public",
        "icon": "door_back",
        "color": "#f59e0b",
        "detection_type": "schedule_proximity",
        "params": {"minutes_before": 30, "schedule_event": "close"},
        "enabled": True,
        "groups": [],
        "priority": 3,
    },
    {
        "slug": "closed",
        "name": "Site ferme",
        "description": "Le site est ferme au public",
        "icon": "door_sliding",
        "color": "#ef4444",
        "detection_type": "schedule_transition",
        "params": {"transition": "close"},
        "enabled": True,
        "groups": [],
        "priority": 4,
    },
    {
        "slug": "traffic-cluster",
        "name": "Zone critique trafic",
        "description": "Cluster d'incidents trafic dans un rayon restreint",
        "icon": "traffic",
        "color": "#f97316",
        "detection_type": "traffic_cluster",
        "params": {"radius_m": 500, "threshold": 12},
        "enabled": True,
        "groups": [],
        "priority": 5,
    },
    {
        "slug": "anpr-watchlist",
        "name": "Plaque surveillee detectee",
        "description": "Une plaque d'immatriculation surveillee a ete detectee par le systeme LAPI",
        "icon": "local_police",
        "color": "#dc2626",
        "detection_type": "anpr_watchlist",
        "params": {},
        "enabled": True,
        "groups": [],
        "priority": 6,
    },
    {
        "slug": "meteo-vent",
        "name": "Alerte vent fort",
        "description": "Rafales de vent depassant le seuil d'alerte",
        "icon": "air",
        "color": "#f97316",
        "detection_type": "meteo_threshold",
        "params": {"field": "vent_rafale", "warn": 40, "alert": 60, "unit": "km/h"},
        "enabled": True,
        "groups": [],
        "priority": 7,
    },
    {
        "slug": "meteo-pluie",
        "name": "Alerte pluie forte",
        "description": "Precipitations depassant le seuil d'alerte",
        "icon": "umbrella",
        "color": "#42a5f5",
        "detection_type": "meteo_threshold",
        "params": {"field": "pluviometrie", "warn": 5, "alert": 15, "unit": "mm"},
        "enabled": True,
        "groups": [],
        "priority": 8,
    },
    {
        "slug": "checkpoint-reassign",
        "name": "Changement d'affectation checkpoint",
        "description": "Un checkpoint a change de gate entre deux cycles de detection",
        "icon": "swap_horiz",
        "color": "#8b5cf6",
        "detection_type": "checkpoint_reassign",
        "params": {},
        "enabled": True,
        "groups": [],
        "priority": 9,
    },
    {
        "slug": "pcorg-securite-ua",
        "name": "Main courante Securite (UA+)",
        "description": "Fiche securite avec urgence absolue ou detresse vitale",
        "icon": "shield",
        "color": "#dc2626",
        "detection_type": "pcorg_urgency",
        "params": {"category": "PCO.Securite", "min_level": "UA"},
        "enabled": False,
        "groups": [],
        "priority": 10,
    },
    {
        "slug": "pcorg-secours-ua",
        "name": "Main courante Secours (UA+)",
        "description": "Fiche secours avec urgence absolue ou detresse vitale",
        "icon": "local_hospital",
        "color": "#dc2626",
        "detection_type": "pcorg_urgency",
        "params": {"category": "PCO.Secours", "min_level": "UA"},
        "enabled": False,
        "groups": [],
        "priority": 11,
    },
    {
        "slug": "field_sos",
        "name": "SOS Tablette terrain",
        "description": "Alerte SOS declenchee par une tablette de patrouille terrain",
        "icon": "sos",
        "color": "#dc2626",
        "detection_type": "field_sos",
        "params": {},
        "enabled": True,
        "groups": [],
        "priority": 0,
    },
]
for _seed in _ALERT_SEEDS:
    COL_ALERT_DEFS.update_one(
        {"slug": _seed["slug"]},
        {"$setOnInsert": {
            **_seed,
            "createdAt": datetime.now(timezone.utc),
            "updatedAt": datetime.now(timezone.utc),
        }},
        upsert=True
    )

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
                # En mode développement, on simule un utilisateur
                # ?as=user ou ?as=manager pour simuler un role non-admin
                sim_role = request.args.get("as", "admin")
                if sim_role not in ROLE_HIERARCHY:
                    sim_role = "admin"
                sim_level = ROLE_HIERARCHY[sim_role]
                sim_roles = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= sim_level]
                logger.info(f"[DEV_MODE] Bypassing authentication for role '{required_role}' (simulated: {sim_role})")
                request.user_payload = {
                    "apps": ["looker", "shiftsolver", "tagger"],
                    "roles_by_app": {"cockpit": sim_role},
                    "global_roles": [],
                    "roles": sim_roles,
                    "app_role": sim_role,
                    "is_super_admin": False,
                    "firstname": "Bruce",
                    "lastname": "WAYNE",
                    "email": "bruce@wayneenterprise.com"
                }
                if sim_level < ROLE_HIERARCHY.get(required_role, 0):
                    flash(f"Acces interdit : cette fonctionnalite requiert un role '{required_role}'.", "error")
                    return redirect(request.referrer or "/")
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

            # Enrichir avec prenom/nom depuis MongoDB si absents du JWT
            if not payload.get("firstname"):
                email = payload.get("email", "")
                if email:
                    if email in _user_name_cache:
                        payload["firstname"] = _user_name_cache[email]["firstname"]
                        payload["lastname"] = _user_name_cache[email]["lastname"]
                    else:
                        user_doc = db["users"].find_one(
                            {"email": email},
                            {"prenom": 1, "nom": 1},
                        )
                        if user_doc:
                            payload["firstname"] = user_doc.get("prenom", "")
                            payload["lastname"] = user_doc.get("nom", "")
                            _user_name_cache[email] = {
                                "firstname": payload["firstname"],
                                "lastname": payload["lastname"],
                            }

            request.user_payload = payload
            return f(*args, **kwargs)
        return decorated_function
    return decorator

################################################################################
# BLOCK PERMISSIONS (visibilite des widgets par groupe)
################################################################################

BLOCK_REGISTRY = {
    "widget-traffic":   {"label": "Trafic",            "default_column": "left"},
    "widget-comms":     {"label": "Communications",    "default_column": "left"},
    "widget-parkings":  {"label": "Temps d'acces",     "default_column": "left"},
    "status-card":      {"label": "Statut evenement",  "default_column": None},
    "widget-counters":  {"label": "Compteurs",         "default_column": "right"},
    "widget-right-1":   {"label": "Meteo detail",      "default_column": "right"},
    "widget-right-2":   {"label": "Affluence",         "default_column": "right"},
    "widget-right-3":   {"label": "Alertes",           "default_column": "right"},
    "widget-right-4":   {"label": "Ressources",        "default_column": "right"},
    "meteo-previsions": {"label": "Meteo bandeau",     "default_column": None},
    "timeline-main":    {"label": "Timeline",          "default_column": None},
    "map-main":         {"label": "Carte",             "default_column": None},
}
ALL_BLOCK_IDS = list(BLOCK_REGISTRY.keys())
MOVABLE_BLOCK_IDS = [bid for bid, info in BLOCK_REGISTRY.items() if info.get("default_column")]

DEFAULT_LAYOUT = {
    "left":  [bid for bid, info in BLOCK_REGISTRY.items() if info.get("default_column") == "left"],
    "right": [bid for bid, info in BLOCK_REGISTRY.items() if info.get("default_column") == "right"],
}

def get_user_allowed_blocks(payload):
    """Retourne set() de block IDs autorises, ou None si aucune restriction."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return None
    email = payload.get("email", "")
    user_doc = db['users'].find_one({"email": email}, {"_id": 1})
    if not user_doc:
        return _get_default_blocks()
    uid = user_doc["_id"]
    ug = COL_USER_GROUPS.find_one({"user_id": uid})
    group_ids = (ug.get("groups") or []) if ug else []
    if not group_ids:
        return _get_default_blocks()
    groups = list(COL_GROUPS.find({"_id": {"$in": group_ids}}))
    allowed = set()
    for g in groups:
        ab = g.get("allowed_blocks")
        if ab is None:
            return None
        allowed.update(ab)
    return allowed

def _get_default_blocks():
    """Retourne les blocs du groupe __default__, ou None si pas de restriction."""
    default_group = COL_GROUPS.find_one({"name": DEFAULT_GROUP_NAME})
    if not default_group:
        return None
    ab = default_group.get("allowed_blocks")
    if ab is None:
        return None
    return set(ab)

def get_user_block_layout(payload):
    """Retourne dict {left: [...], right: [...]} ou None (= layout par defaut)."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return None
    email = payload.get("email", "")
    user_doc = db['users'].find_one({"email": email}, {"_id": 1})
    if not user_doc:
        return _get_default_layout()
    uid = user_doc["_id"]
    ug = COL_USER_GROUPS.find_one({"user_id": uid})
    group_ids = (ug.get("groups") or []) if ug else []
    if not group_ids:
        return _get_default_layout()
    groups = list(COL_GROUPS.find({"_id": {"$in": group_ids}}))
    for g in groups:
        bl = g.get("block_layout")
        if bl and isinstance(bl, dict) and ("left" in bl or "right" in bl):
            return bl
    return None

def _get_default_layout():
    """Retourne le block_layout du groupe __default__, ou None."""
    default_group = COL_GROUPS.find_one({"name": DEFAULT_GROUP_NAME})
    if not default_group:
        return None
    bl = default_group.get("block_layout")
    if bl and isinstance(bl, dict) and ("left" in bl or "right" in bl):
        return bl
    return None

def _user_can_fiche_simplifiee(payload):
    """Verifie si l'utilisateur a le droit fiche_simplifiee via ses groupes."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return True
    email = payload.get("email", "")
    user_doc = db['users'].find_one({"email": email}, {"_id": 1})
    if not user_doc:
        return False
    ug = COL_USER_GROUPS.find_one({"user_id": user_doc["_id"]})
    group_ids = (ug.get("groups") or []) if ug else []
    if not group_ids:
        return False
    return COL_GROUPS.count_documents({"_id": {"$in": group_ids}, "fiche_simplifiee": True}) > 0

def _user_can_close_fiche(payload):
    """Verifie si l'utilisateur a le droit de cloturer des fiches via ses groupes."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return True
    email = payload.get("email", "")
    user_doc = db['users'].find_one({"email": email}, {"_id": 1})
    if not user_doc:
        return False
    ug = COL_USER_GROUPS.find_one({"user_id": user_doc["_id"]})
    group_ids = (ug.get("groups") or []) if ug else []
    if not group_ids:
        return False
    return COL_GROUPS.count_documents({"_id": {"$in": group_ids}, "can_close_fiche": True}) > 0

def _parse_allowed_categories(raw):
    if not isinstance(raw, list):
        return None
    filtered = [c for c in raw if c in ALL_PCO_CATEGORIES]
    return filtered if filtered else None

ALL_PCO_CATEGORIES = [
    "PCO.Secours", "PCO.Securite", "PCO.Technique",
    "PCO.Flux", "PCO.Information", "PCO.MainCourante", "PCO.Fourriere"
]

def get_user_allowed_categories(payload):
    """Retourne liste de categories PCO autorisees, ou None si aucune restriction."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return None
    email = payload.get("email", "")
    user_doc = db['users'].find_one({"email": email}, {"_id": 1})
    if not user_doc:
        return _get_default_categories()
    uid = user_doc["_id"]
    ug = COL_USER_GROUPS.find_one({"user_id": uid})
    group_ids = (ug.get("groups") or []) if ug else []
    if not group_ids:
        return _get_default_categories()
    groups = list(COL_GROUPS.find({"_id": {"$in": group_ids}}))
    allowed = set()
    for g in groups:
        ac = g.get("allowed_categories")
        if ac is None:
            return None  # pas de restriction
        allowed.update(ac)
    return list(allowed) if allowed else None

def _get_default_categories():
    """Retourne les categories du groupe __default__, ou None si pas de restriction."""
    default_group = COL_GROUPS.find_one({"name": DEFAULT_GROUP_NAME})
    if not default_group:
        return None
    ac = default_group.get("allowed_categories")
    if ac is None:
        return None
    return list(ac)

def block_required(block_id):
    """Decorateur: retourne 403 si le user n'a pas acces a ce bloc."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            payload = getattr(request, 'user_payload', {})
            allowed = get_user_allowed_blocks(payload)
            if allowed is not None and block_id not in allowed:
                return jsonify({"error": "Acces non autorise a ce widget"}), 403
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
    allowed = get_user_allowed_blocks(payload)
    allowed_blocks_json = json.dumps(list(allowed) if allowed is not None else None)
    # Recuperer les groupes (nom + couleur) de l'utilisateur pour les pillules header
    user_group_pills = []
    effective_role = payload.get("app_role", "user")
    if effective_role == "admin" or payload.get("is_super_admin"):
        admin_grp = COL_GROUPS.find_one({"name": ADMIN_GROUP_NAME})
        admin_color = admin_grp["color"] if admin_grp else "#ef4444"
        user_group_pills = [{"name": "Admin", "color": admin_color}]
    else:
        user_doc = db['users'].find_one({"email": user_email}, {"_id": 1})
        if user_doc:
            ug = COL_USER_GROUPS.find_one({"user_id": user_doc["_id"]})
            gids = (ug.get("groups") or []) if ug else []
            if gids:
                groups = list(COL_GROUPS.find({"_id": {"$in": gids}, "name": {"$nin": list(SYSTEM_GROUP_NAMES)}}))
                user_group_pills = [{"name": g["name"], "color": g.get("color", "#6366f1")} for g in groups]
    user_groups_json = json.dumps(user_group_pills)
    user_fiche_simplifiee = _user_can_fiche_simplifiee(payload)
    block_layout = get_user_block_layout(payload)
    block_layout_json = json.dumps(block_layout)
    return render_template("index.html", user_roles=user_roles, user_apps=user_apps,
                           user_firstname=user_firstname, user_lastname=user_lastname,
                           user_email=user_email,
                           allowed_blocks_json=allowed_blocks_json,
                           block_layout_json=block_layout_json,
                           user_groups_json=user_groups_json,
                           user_fiche_simplifiee_json=json.dumps(user_fiche_simplifiee),
                           user_allowed_categories_json=json.dumps(get_user_allowed_categories(payload)),
                           user_can_close_fiche_json=json.dumps(_user_can_close_fiche(payload)))

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
@block_required("timeline-main")
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

@app.route('/api/grid-ref', methods=['GET'])
@role_required("user")
def get_grid_ref():
    """Retourne le carroyage tactique (lignes depuis QGIS)."""
    lines_doc = db.grid_ref_qgis.find_one({"type": "grid_lines"}, {"_id": 0})
    lines_25 = db.grid_ref_qgis.find_one({"type": "grid_lines_25"}, {"_id": 0})
    if not lines_doc:
        return jsonify({"lines": None}), 200
    return jsonify({"lines": lines_doc, "lines_25": lines_25})


@app.route('/api/3p', methods=['GET'])
@role_required("user")
def get_3p():
    """Retourne les portes/portails/portillons (collection 3p) dans le viewport."""
    doc = db["3p"].find_one({}, {"_id": 0})
    if not doc or "features" not in doc:
        return jsonify({"features": []})

    south = request.args.get("south", type=float)
    west = request.args.get("west", type=float)
    north = request.args.get("north", type=float)
    east = request.args.get("east", type=float)

    features = doc["features"]
    if south is not None and west is not None and north is not None and east is not None:
        filtered = []
        for f in features:
            coords = f.get("geometry", {}).get("coordinates", [])
            if len(coords) >= 2:
                lng, lat = coords[0], coords[1]
                if south <= lat <= north and west <= lng <= east:
                    filtered.append(f)
        features = filtered

    return jsonify({"features": features})


# Photos 3P (servies depuis le dossier looker/static/img/media)
LOOKER_MEDIA = os.path.join(os.path.dirname(__file__), '..', 'looker', 'static', 'img', 'media')


@app.route('/api/3p/photo/thumb/<filename>')
@role_required("user")
def get_3p_thumb(filename):
    """Sert la miniature d'une photo 3P."""
    safe = os.path.basename(filename)
    return send_from_directory(os.path.join(LOOKER_MEDIA, 'thumbnails'), safe)


@app.route('/api/3p/photo/original/<filename>')
@role_required("user")
def get_3p_original(filename):
    """Sert la photo originale 3P (fallback sur thumbnail si pas d'original)."""
    safe = os.path.basename(filename)
    orig_path = os.path.join(LOOKER_MEDIA, 'original', safe)
    if os.path.isfile(orig_path):
        return send_from_directory(os.path.join(LOOKER_MEDIA, 'original'), safe)
    return send_from_directory(os.path.join(LOOKER_MEDIA, 'thumbnails'), safe)


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
@block_required("widget-right-1")
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
@block_required("meteo-previsions")
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
@block_required("widget-right-1")
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
@block_required("meteo-previsions")
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

    results.sort(key=lambda r: (r['Date'], r['Heure']))
    return jsonify(results)


# ── Seuils operationnels meteo ──
METEO_THRESHOLDS = {
    "wind_warn": 40,   # km/h vigilance
    "wind_alert": 60,  # km/h action
    "rain_warn": 5,    # mm vigilance
    "rain_alert": 15,  # mm alerte
    "temp_hot": 35,    # C canicule
    "temp_cold": 2,    # C gel
}


@app.route('/meteo_widget_summary', methods=['GET'])
@role_required("user")
@block_required("widget-right-1")
def get_meteo_widget_summary():
    """Retourne un resume meteo operationnel : conditions actuelles, risque, alertes."""
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    current_hour = now.strftime('%H:00')

    previsions = db.meteo_previsions.find_one({'Date': today_str})
    if not previsions or 'Heures' not in previsions:
        return jsonify({'error': 'Aucune donnee meteo disponible'}), 404

    heures = previsions['Heures']
    th = METEO_THRESHOLDS

    # ── Conditions actuelles (heure la plus proche) ──
    current = None
    for h in heures:
        if h['Heure'] >= current_hour:
            current = h
            break
    if not current:
        current = heures[-1] if heures else {}

    current_data = {
        'temp': int(current.get('Temperature (°C)', current.get('Temp\u00e9rature (\u00b0C)', 0))),
        'gust': int(current.get('Vent rafale (km/h)', 0)),
        'rain': float(current.get('Pluviometrie (mm)', current.get('Pluviom\u00e9trie (mm)', 0))),
        'wind_avg': int(current.get('Vent moyen (km/h)', 0)),
        'hour': current.get('Heure', current_hour)
    }

    # ── Filtrer les heures restantes de la journee ──
    upcoming = [h for h in heures if h['Heure'] >= current_hour]

    # ── Calcul du risque et des alertes ──
    alerts = []
    max_severity = 'green'

    for h in upcoming:
        heure = h['Heure']
        gust = int(h.get('Vent rafale (km/h)', 0))
        rain = float(h.get('Pluviometrie (mm)', h.get('Pluviom\u00e9trie (mm)', 0)))
        temp = int(h.get('Temperature (°C)', h.get('Temp\u00e9rature (\u00b0C)', 0)))

        if gust >= th['wind_alert']:
            alerts.append({'type': 'wind', 'icon': 'air', 'severity': 'red',
                           'message': f'Rafales {gust} km/h a {heure}'})
            max_severity = 'red'
        elif gust >= th['wind_warn']:
            alerts.append({'type': 'wind', 'icon': 'air', 'severity': 'orange',
                           'message': f'Rafales {gust} km/h a {heure}'})
            if max_severity != 'red':
                max_severity = 'orange'

        if rain >= th['rain_alert']:
            alerts.append({'type': 'rain', 'icon': 'umbrella', 'severity': 'red',
                           'message': f'Pluie forte {rain} mm a {heure}'})
            max_severity = 'red'
        elif rain >= th['rain_warn']:
            alerts.append({'type': 'rain', 'icon': 'umbrella', 'severity': 'orange',
                           'message': f'Pluie {rain} mm a {heure}'})
            if max_severity != 'red':
                max_severity = 'orange'

        if temp >= th['temp_hot']:
            alerts.append({'type': 'heat', 'icon': 'thermostat', 'severity': 'orange',
                           'message': f'Canicule {temp}C a {heure}'})
            if max_severity == 'green':
                max_severity = 'orange'
        elif temp <= th['temp_cold']:
            alerts.append({'type': 'cold', 'icon': 'ac_unit', 'severity': 'orange',
                           'message': f'Gel {temp}C a {heure}'})
            if max_severity == 'green':
                max_severity = 'orange'

    # Deduplication : garder la pire alerte par type
    seen_types = {}
    deduped_alerts = []
    for a in alerts:
        key = a['type']
        if key not in seen_types or a['severity'] == 'red':
            seen_types[key] = a
    deduped_alerts = list(seen_types.values())

    risk_labels = {'green': 'RAS', 'orange': 'Vigilance', 'red': 'Alerte'}

    return jsonify({
        'current': current_data,
        'alerts': deduped_alerts,
        'risk_level': max_severity,
        'risk_label': risk_labels[max_severity]
    })


@app.route('/sun_times', methods=['GET'])
@role_required("user")
@block_required("meteo-previsions")
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
app.register_blueprint(analyse_ops_bp)
app.register_blueprint(anoloc_bp)
app.register_blueprint(anpr_bp)
app.register_blueprint(field_bp)
# Le blueprint field utilise son propre systeme d'auth (cookie field_token) et
# doit etre exempte de CSRF puisque les tablettes n'ont pas de token CSRF cockpit.
csrf.exempt(field_bp)
# Vision admin : autonome, partage uniquement la whitelist d'auth /field/* via
# les URL /field/api/vision/pair (public CORS) et /field/admin/vision/* (admin).
app.register_blueprint(vision_admin_bp)
csrf.exempt(vision_admin_bp)
# Routing : Valhalla auto-heberge, calcul d'itineraires Field + Cockpit. Seule
# la route tablette /field/api/route est exemptee de CSRF (la tablette n'a pas
# de token CSRF cockpit) ; les routes admin (/api/route, /api/route/forward)
# conservent leur CSRF.
app.register_blueprint(routing_bp)
csrf.exempt(app.view_functions["routing.field_route"])
app.register_blueprint(cameras_bp)
# app.register_blueprint(meteo_bp)

################################################################################
# DATA BILLETTERIE
################################################################################

@app.route('/get_counter', methods=['GET'])
@role_required("user")
@block_required("widget-counters")
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
@block_required("widget-counters")
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


def _parse_race_date(raw):
    """Parse une date de course (string ISO ou datetime) en date."""
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            return datetime.fromisoformat(raw.replace('Z', '+00:00')).date()
        return raw.date() if hasattr(raw, 'date') else None
    except Exception:
        return None


def _parse_race_datetime(raw):
    """Parse une date de course en datetime complet (avec heure si disponible)."""
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            return datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if hasattr(raw, 'hour'):
            return raw
        if hasattr(raw, 'date'):
            # datetime sans tz
            return raw
        return None
    except Exception:
        return None


def _fill_curve(param_doc, race_date_override=None):
    """Retourne [(days_before_race, total_ventes)] tries descending, final, race_date.
    Filtre la fenetre saisonniere autour de la course pour eviter de melanger les
    historiques de plusieurs editions. Applique un forward-fill par produit avant
    aggregation pour combler les trous de snapshots non synchronises.
    race_date_override permet de forcer la date de reference (ex: portes plus fiable
    que parametrages) pour un calcul des days_before coherent.
    """
    rd = race_date_override or _parse_race_date(param_doc.get('data', {}).get('race'))
    if not rd:
        return [], 0, None
    prods = param_doc.get('tickets', {}).get('products', {})

    season_start = rd - timedelta(days=300)
    season_end = rd + timedelta(days=30)

    product_series = {}
    all_dates = set()
    for pname, p in prods.items():
        series = {}
        for h in p.get('history', []):
            try:
                d = datetime.strptime(h['date'], '%Y-%m-%d').date()
            except Exception:
                continue
            if season_start <= d <= season_end:
                series[h['date']] = h.get('ventes', 0)
                all_dates.add(h['date'])
        if series:
            product_series[pname] = series

    if not all_dates:
        return [], 0, None

    sorted_dates = sorted(all_dates)
    sorted_series = {pn: sorted(s.items()) for pn, s in product_series.items()}

    snaps = {}
    for date_key in sorted_dates:
        total = 0
        for items in sorted_series.values():
            last_val = 0
            for sd, sv in items:
                if sd <= date_key:
                    last_val = sv
                else:
                    break
            total += last_val
        snaps[date_key] = total

    points = []
    for d, v in snaps.items():
        dt = datetime.strptime(d, '%Y-%m-%d').date()
        points.append(((rd - dt).days, v))
    points.sort(key=lambda x: x[0], reverse=True)

    final_from_curve = snaps[sorted_dates[-1]]
    final_actual = sum(p.get('ventes', 0) for p in prods.values())
    final = max(final_from_curve, final_actual)

    return points, final, rd


def _interpolate_pct(points, final, target_days_before):
    """Interpole le % atteint a target_days_before jours de la course."""
    if not points or final <= 0:
        return None
    for i in range(len(points) - 1):
        d1, v1 = points[i]
        d2, v2 = points[i + 1]
        if d1 >= target_days_before >= d2:
            ratio = (d1 - target_days_before) / (d1 - d2) if d1 != d2 else 0
            pct = (v1 + ratio * (v2 - v1)) / final * 100
            return pct
    if target_days_before >= points[0][0]:
        return points[0][1] / final * 100
    return points[-1][1] / final * 100


@app.route('/get_affluence', methods=['GET'])
@role_required("user")
@block_required("widget-right-2")
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
    race_date = _parse_race_date(race_raw)

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
        prev_race_date = _parse_race_date(prev_race_raw)

    # ── Charger historique_controle N-1 (pic presents) ──
    prev_hist_race_date = None
    prev_data_by_day = {}
    if race_date and current_year_int:
        prev_hist_candidates = list(db['historique_controle'].find(
            {'type': 'frequentation', 'event': event},
            sort=[('year', -1)]
        ))
        for cand in prev_hist_candidates:
            cand_year = cand.get('year')
            if isinstance(cand_year, (int, float)) and int(cand_year) < current_year_int:
                prev_hist_year = cand_year
                prev_hist_race_raw = cand.get('race')
                if not prev_hist_race_raw:
                    portes_doc = db['historique_controle'].find_one(
                        {'type': 'portes', 'event': event, 'year': prev_hist_year},
                        {'_id': 0, 'race': 1}
                    )
                    if portes_doc:
                        prev_hist_race_raw = portes_doc.get('race')
                prev_hist_race_date = _parse_race_date(prev_hist_race_raw)
                if prev_hist_race_date and cand.get('data'):
                    from collections import defaultdict
                    day_records = defaultdict(list)
                    for rec in cand['data']:
                        rec_date = rec.get('date')
                        if isinstance(rec_date, str):
                            day_key = rec_date[:10]
                        elif hasattr(rec_date, 'strftime'):
                            day_key = rec_date.strftime('%Y-%m-%d')
                        else:
                            continue
                        day_records[day_key].append(rec)
                    prev_data_by_day = dict(day_records)
                break

    # Reference unifiee de la date de course N-1 : privilegier historique_controle
    # (via doc portes, fiable) sur parametrages.data.race (parfois errone).
    prev_race_ref = prev_hist_race_date or prev_race_date

    # ── Calculer la projection basee sur les courbes N-1 et N-2 ──
    # Calculer le ratio de projection
    projection_ratio = None  # ventes_actuelles / projection = ce ratio
    if race_date and last_update:
        last_dt = datetime.strptime(last_update, '%Y-%m-%d').date()
        days_before = (race_date - last_dt).days

        fill_pcts = []
        # Courbe N-1
        if prev_param:
            pts, final, _ = _fill_curve(prev_param, race_date_override=prev_race_ref)
            pct = _interpolate_pct(pts, final, days_before)
            if pct:
                fill_pcts.append(pct)

        # Courbe N-2
        if current_year_int:
            for cand in sorted(prev_candidates, key=lambda c: str(c.get('year', '')), reverse=True):
                try:
                    cy = int(cand.get('year', ''))
                except (ValueError, TypeError):
                    continue
                if cy < current_year_int and cand.get('year') != prev_year_str:
                    pts2, final2, _ = _fill_curve(cand)
                    pct2 = _interpolate_pct(pts2, final2, days_before)
                    if pct2:
                        fill_pcts.append(pct2)
                    break

        if fill_pcts:
            avg_pct = sum(fill_pcts) / len(fill_pcts)
            if avg_pct > 0:
                projection_ratio = avg_pct / 100  # ex: 0.55 = on est a 55% du final

    # ── Totaux sur l'ensemble unique des produits references ──
    # day_ventes est multi-compte (billet 4J compte 4 fois) pour refleter la
    # presence attendue par jour ; les totaux ne doivent PAS agreger cela.
    referenced_products = set()
    for tc in ticketing_config:
        for pname in tc.get('products', []):
            referenced_products.add(pname)
    total_ventes = sum(
        products_data.get(p, {}).get('ventes', 0) for p in referenced_products
    )
    total_delta = 0
    for pname in referenced_products:
        pdata = products_data.get(pname, {})
        hist = pdata.get('history', [])
        if len(hist) >= 2:
            total_delta += pdata.get('ventes', 0) - hist[-2].get('ventes', 0)

    prev_referenced = set()
    for tc in prev_ticketing_config:
        for pname in tc.get('products', []):
            prev_referenced.add(pname)
    total_ventes_prev = sum(
        prev_products_data.get(p, {}).get('ventes', 0) for p in prev_referenced
    )

    # Delta de croissance N vs N-1 : sert de borne basse a la projection.
    # La projection "courbe" peut s'emballer si la saison N a pris son avance tot
    # et aplatit en fin ; la projection "delta" applique simplement la croissance
    # observee au final N-1 et borne le haut.
    delta_n_vs_n1 = None
    if total_ventes_prev and total_ventes_prev > 0:
        delta_n_vs_n1 = total_ventes / total_ventes_prev

    # ── Construire la reponse par jour ──
    result_days = []
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

        # Ventes N-1 pour le jour equivalent (meme offset depuis la course)
        ventes_prev = None
        if race_date and prev_race_ref and prev_ticketing_config:
            offset_days = (day_date - race_date).days
            target_prev_date = prev_race_ref + timedelta(days=offset_days)
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

        # Projection ventes pour ce jour : fourchette low (delta) -> high (courbe)
        day_projection = None       # borne haute (courbe N-1)
        day_projection_low = None   # borne basse (delta de croissance actuel)
        if projection_ratio and projection_ratio > 0:
            day_projection = round(day_ventes / projection_ratio)
        if ventes_prev and ventes_prev > 0 and delta_n_vs_n1:
            # borne basse = ventes finales N-1 du meme jour * croissance globale N/N-1 observee
            # plafonnee a au moins les ventes actuelles N (on ne redescend pas)
            day_projection_low = max(day_ventes, round(ventes_prev * delta_n_vs_n1))

        # Pic N-1 et Pic projete depuis historique_controle (fourchette aussi)
        pic_prev = None
        pic_projection = None
        pic_projection_low = None
        if race_date and prev_race_ref:
            offset_days = (day_date - race_date).days
            target_prev_date = prev_race_ref + timedelta(days=offset_days)
            target_key = target_prev_date.strftime('%Y-%m-%d')
            if target_key in prev_data_by_day:
                records = prev_data_by_day[target_key]
                pic_prev = max((r.get('present', 0) for r in records), default=0)
                # Pic projete = projection_ventes * (pic_prev / ventes_prev)
                # Le ratio pic/ventes de N-1 capture les enfants gratuits + accredites
                if pic_prev and ventes_prev and ventes_prev > 0:
                    pic_ratio = pic_prev / ventes_prev
                    if day_projection:
                        pic_projection = round(day_projection * pic_ratio)
                    if day_projection_low:
                        pic_projection_low = round(day_projection_low * pic_ratio)

        result_days.append({
            "date": day_str,
            "label": label,
            "ventes": day_ventes,
            "delta": day_delta,
            "ventes_prev": ventes_prev,
            "projection": day_projection,
            "projection_low": day_projection_low,
            "pic_prev": pic_prev,
            "pic_projection": pic_projection,
            "pic_projection_low": pic_projection_low,
            "prev_year": prev_year_str
        })

    total_projection = round(total_ventes / projection_ratio) if projection_ratio else None
    total_projection_low = None
    if total_ventes_prev and delta_n_vs_n1:
        total_projection_low = max(total_ventes, round(total_ventes_prev * delta_n_vs_n1))

    # ── Sites (parkings + campings avec ticketing) ──
    # Charger aussi les sites N-1 pour comparer par nom de site
    prev_sites_by_name = {}
    if prev_param:
        prev_data = prev_param.get('data', {})
        prev_prods = prev_param.get('tickets', {}).get('products', {})
        for sk in ('parkingsHoraires', 'campingsHoraires'):
            for ps in prev_data.get(sk, []):
                ptk = ps.get('ticketing', [])
                if not ptk:
                    continue
                sv = sum(prev_prods.get(t.get('product', ''), {}).get('ventes', 0) for t in ptk)
                prev_sites_by_name[ps.get('name', '')] = sv

    sites = []
    for source_key in ('parkingsHoraires', 'campingsHoraires'):
        for site in doc['data'].get(source_key, []):
            tk = site.get('ticketing', [])
            if not tk:
                continue
            site_ventes = 0
            for t in tk:
                pname = t.get('product', '')
                pdata = products_data.get(pname)
                if pdata:
                    site_ventes += pdata.get('ventes', 0)
            site_name = site.get('name', '?')
            site_ventes_prev = prev_sites_by_name.get(site_name)
            site_projection = round(site_ventes / projection_ratio) if projection_ratio else None
            sites.append({
                'name': site_name,
                'capacite': site.get('capacite') or site.get('capacite_theorique') or 0,
                'ventes': site_ventes,
                'ventes_prev': site_ventes_prev,
                'projection': site_projection,
            })

    return jsonify({
        "days": result_days,
        "total_ventes": total_ventes,
        "total_delta": total_delta,
        "total_ventes_prev": total_ventes_prev if total_ventes_prev else None,
        "total_projection": total_projection,
        "total_projection_low": total_projection_low,
        "last_update": last_update,
        "prev_year": prev_year_str,
        "sites": sites
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

COL_TODOS = db['todos']  # schema: { type:str, todos:[{text:str, phase:str}], createdAt, updatedAt }

# Helpers

_VALID_PHASES = {"open", "close", "both"}

def _normalize_todos(raw_todos):
    """Normalise les todos en [{text, phase}]. Accepte l'ancien format [str]."""
    result = []
    for item in (raw_todos or []):
        if isinstance(item, str):
            text = item.strip()
            if text:
                result.append({"text": text, "phase": "open"})
        elif isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            phase = item.get("phase", "both")
            if phase not in _VALID_PHASES:
                phase = "both"
            if text:
                result.append({"text": text, "phase": phase})
    return result

def _pub(doc):
    if not doc: return None
    d = dict(doc)
    for k, v in d.items():
        if isinstance(v, ObjectId):
            d[k] = str(v)
        elif isinstance(v, list):
            d[k] = [str(x) if isinstance(x, ObjectId) else x for x in v]
        elif hasattr(v, 'isoformat'):
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            d[k] = v.isoformat()
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
    if not typ:
        return jsonify({'error':'type is required'}), 400
    todos = _normalize_todos(data.get('todos'))
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
        patch['todos'] = _normalize_todos(data.get('todos'))
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


@app.route('/field-dispatch')
@role_required("admin")
def field_dispatch_page():
    """Console operationnelle PCO pour piloter les tablettes terrain :
    appairage, envoi de messages, suivi/revocation. Consomme les endpoints
    /field/admin/* exposes par le blueprint field."""
    payload = getattr(request, 'user_payload', {})
    return render_template(
        'field_dispatch.html',
        user_roles=payload.get("roles", []),
        user_firstname=payload.get("firstname", ""),
        user_lastname=payload.get("lastname", ""),
        user_email=payload.get("email", ""),
    )

################################################################################
# Block permissions API
################################################################################

@app.route('/api/my-permissions', methods=['GET'])
@role_required("user")
def get_my_permissions():
    allowed = get_user_allowed_blocks(request.user_payload)
    return jsonify({
        "allowed_blocks": list(allowed) if allowed is not None else None,
        "all_blocks": ALL_BLOCK_IDS
    })

@app.route('/api/block-registry', methods=['GET'])
@role_required("admin")
def get_block_registry():
    return jsonify([
        {"id": bid, "label": info["label"], "default_column": info.get("default_column")}
        for bid, info in BLOCK_REGISTRY.items()
    ])

@app.route('/api/pco-category-registry', methods=['GET'])
@role_required("admin")
def get_pco_category_registry():
    labels = {
        "PCO.Secours": "Secours", "PCO.Securite": "Securite", "PCO.Technique": "Technique",
        "PCO.Flux": "Flux", "PCO.Fourriere": "Fourriere", "PCO.Information": "Information",
        "PCO.MainCourante": "Main courante"
    }
    return jsonify([{"id": c, "label": labels.get(c, c)} for c in ALL_PCO_CATEGORIES])

################################################################################
################################################################################
# Historique des alertes cockpit
################################################################################

@app.route('/api/alert-history', methods=['GET'])
@role_required("user")
def get_alert_history():
    limit = int(request.args.get('limit', 50))
    payload = getattr(request, 'user_payload', {})
    allowed_slugs = _get_user_alert_slugs(payload)
    query = {}
    if allowed_slugs is not None:
        query["type"] = {"$in": allowed_slugs}
    alerts = list(COL_ALERT_HISTORY.find(query).sort('createdAt', -1).limit(limit))
    return jsonify([_pub(a) for a in alerts])

@app.route('/api/alert-history', methods=['POST'])
@role_required("user")
@csrf.exempt
def post_alert_history():
    data = request.get_json(force=True) or {}
    alert_type = data.get('type', '')
    message = data.get('message', '')

    # Verifier que l'utilisateur a le droit de voir ce type d'alerte
    payload = getattr(request, 'user_payload', {})
    allowed_slugs = _get_user_alert_slugs(payload)
    if allowed_slugs is not None and alert_type not in allowed_slugs:
        return jsonify({"ok": True, "filtered": True}), 200
    now = datetime.now(timezone.utc)

    # Deduplication adaptative selon le type d'alerte
    # traffic-cluster : meme zone pendant 30 min (match sur type + lieu extrait du message)
    # autres : meme type + message exact dans les 60 dernieres secondes
    if alert_type == 'traffic-cluster':
        dedup_query = {'type': alert_type, 'createdAt': {'$gte': now - timedelta(minutes=30)}}
        # Affiner par lieu si present (texte apres " — " dans le message)
        if '\u2014' in message:
            zone = message.split('\u2014')[-1].strip()
            if zone:
                dedup_query['message'] = {'$regex': zone.replace('(', '\\(').replace(')', '\\)')}
    else:
        dedup_query = {
            'type': alert_type,
            'message': message,
            'createdAt': {'$gte': now - timedelta(hours=1)}
        }
    existing = COL_ALERT_HISTORY.find_one(dedup_query)
    if existing:
        return jsonify(_pub(existing)), 200

    doc = {
        'type': alert_type,
        'title': data.get('title', ''),
        'timeStr': data.get('timeStr', ''),
        'message': message,
        'hasAction': bool(data.get('hasAction')),
        'actionData': data.get('actionData'),
        'createdAt': now,
    }
    ins = COL_ALERT_HISTORY.insert_one(doc)
    doc['_id'] = str(ins.inserted_id)
    return jsonify(doc), 201

# Gestion des groupes et utilisateurs cockpit
################################################################################

@app.route('/api/cockpit-users', methods=['GET'])
@role_required("admin")
def list_cockpit_users():
    users = list(db['users'].find(
        {"roles_by_app.cockpit": {"$exists": True}},
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

@app.route('/api/groups/sql-default', methods=['GET'])
@role_required("admin")
def get_sql_default_group():
    doc = db["cockpit_settings"].find_one({"_id": "sql_default_group"})
    return jsonify({"group_id": doc.get("group_id", "") if doc else ""})

@app.route('/api/groups/sql-default', methods=['PUT'])
@role_required("admin")
@csrf.exempt
def set_sql_default_group():
    data = request.get_json(force=True)
    group_id = (data.get("group_id") or "").strip()
    db["cockpit_settings"].update_one(
        {"_id": "sql_default_group"},
        {"$set": {"group_id": group_id}},
        upsert=True
    )
    return jsonify({"ok": True})

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
    raw_blocks = data.get('allowed_blocks')
    allowed_blocks = None
    if isinstance(raw_blocks, list):
        allowed_blocks = [b for b in raw_blocks if b in BLOCK_REGISTRY]
        if not allowed_blocks:
            allowed_blocks = None
    raw_alerts = data.get('traffic_alerts')
    traffic_alerts = None
    if isinstance(raw_alerts, list):
        traffic_alerts = [a for a in raw_alerts if isinstance(a, str)]
        if not traffic_alerts:
            traffic_alerts = None
    raw_layout = data.get('block_layout')
    block_layout = None
    if isinstance(raw_layout, dict):
        bl = {
            "left":  [b for b in (raw_layout.get("left") or []) if b in MOVABLE_BLOCK_IDS],
            "right": [b for b in (raw_layout.get("right") or []) if b in MOVABLE_BLOCK_IDS],
        }
        if bl["left"] or bl["right"]:
            block_layout = bl
    doc = {
        'name': name,
        'description': (data.get('description') or '').strip(),
        'color': (data.get('color') or '#6366f1').strip(),
        'allowed_blocks': allowed_blocks,
        'traffic_alerts': traffic_alerts,
        'block_layout': block_layout,
        'fiche_simplifiee': bool(data.get('fiche_simplifiee', False)),
        'can_close_fiche': bool(data.get('can_close_fiche', False)),
        'allowed_categories': _parse_allowed_categories(data.get('allowed_categories')),
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
    # Verifier si c'est un groupe systeme
    existing = COL_GROUPS.find_one({"_id": ObjectId(gid)})
    group_name = existing.get("name") if existing else None
    is_system = group_name in SYSTEM_GROUP_NAMES
    is_admin_grp = group_name == ADMIN_GROUP_NAME
    patch = {}
    if 'name' in data and not is_system:
        patch['name'] = (data['name'] or '').strip()
    if 'description' in data and not is_system:
        patch['description'] = (data['description'] or '').strip()
    if 'color' in data:
        patch['color'] = (data['color'] or '').strip()
    if 'allowed_blocks' in data and not is_admin_grp:
        raw_blocks = data['allowed_blocks']
        if isinstance(raw_blocks, list):
            filtered = [b for b in raw_blocks if b in BLOCK_REGISTRY]
            patch['allowed_blocks'] = filtered if filtered else None
        else:
            patch['allowed_blocks'] = None
    if 'traffic_alerts' in data:
        raw_alerts = data['traffic_alerts']
        if isinstance(raw_alerts, list):
            patch['traffic_alerts'] = [a for a in raw_alerts if isinstance(a, str)] or None
        else:
            patch['traffic_alerts'] = None
    if 'block_layout' in data and not is_admin_grp:
        raw_layout = data['block_layout']
        if isinstance(raw_layout, dict):
            bl = {
                "left":  [b for b in (raw_layout.get("left") or []) if b in MOVABLE_BLOCK_IDS],
                "right": [b for b in (raw_layout.get("right") or []) if b in MOVABLE_BLOCK_IDS],
            }
            patch['block_layout'] = bl if (bl["left"] or bl["right"]) else None
        else:
            patch['block_layout'] = None
    if 'fiche_simplifiee' in data:
        patch['fiche_simplifiee'] = bool(data.get('fiche_simplifiee', False))
    if 'allowed_categories' in data:
        patch['allowed_categories'] = _parse_allowed_categories(data.get('allowed_categories'))
    if 'can_close_fiche' in data:
        patch['can_close_fiche'] = bool(data.get('can_close_fiche', False))
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
    # Interdire la suppression du groupe par defaut
    g = COL_GROUPS.find_one({"_id": oid})
    if g and g.get("name") in SYSTEM_GROUP_NAMES:
        return jsonify({"error": "Les groupes systeme ne peuvent pas etre supprimes"}), 400
    r = COL_GROUPS.delete_one({"_id": oid})
    if r.deleted_count == 0:
        return jsonify({"error": "Groupe introuvable"}), 404
    # Retirer ce groupe de tous les user_groups
    COL_USER_GROUPS.update_many({}, {"$pull": {"groups": oid}})
    return jsonify({"ok": True})

################################################################################
# Centrale d'Alerte - Definitions & Watchlist ANPR
################################################################################

# Types de detection disponibles (pour validation)
DETECTION_TYPES = {
    "schedule_proximity", "schedule_transition",
    "traffic_cluster", "anpr_watchlist", "meteo_threshold",
    "checkpoint_reassign", "checkpoint_error_burst",
    "meteo_rain_onset", "pcorg_urgency",
}

@app.route('/admin/alertes')
@role_required("admin")
def alertes_admin():
    payload = getattr(request, 'user_payload', {})
    return render_template('alertes_admin.html',
                           user_roles=payload.get("roles", []),
                           user_firstname=payload.get("firstname", ""),
                           user_lastname=payload.get("lastname", ""),
                           user_email=payload.get("email", ""),
                           app_role=payload.get("app_role", "user"))

# --- CRUD definitions d'alertes ---

@app.route('/api/alert-definitions', methods=['GET'])
@role_required("admin")
def list_alert_definitions():
    docs = list(COL_ALERT_DEFS.find().sort([('priority', 1)]))
    return jsonify([_pub(d) for d in docs])

@app.route('/api/alert-definitions', methods=['POST'])
@role_required("admin")
def create_alert_definition():
    data = request.get_json(force=True) or {}
    slug = (data.get('slug') or '').strip()
    name = (data.get('name') or '').strip()
    if not slug or not name:
        return jsonify({"error": "slug et name sont requis"}), 400
    if COL_ALERT_DEFS.find_one({"slug": slug}):
        return jsonify({"error": "Une alerte avec ce slug existe deja"}), 409
    detection_type = data.get('detection_type', '')
    if detection_type not in DETECTION_TYPES:
        return jsonify({"error": "Type de detection invalide"}), 400
    raw_groups = data.get('groups') or []
    group_oids = [ObjectId(g) for g in raw_groups if g]
    doc = {
        'slug': slug,
        'name': name,
        'description': (data.get('description') or '').strip(),
        'icon': (data.get('icon') or 'notifications').strip(),
        'color': (data.get('color') or '#6366f1').strip(),
        'detection_type': detection_type,
        'params': data.get('params') or {},
        'enabled': bool(data.get('enabled', True)),
        'groups': group_oids,
        'priority': int(data.get('priority', 99)),
        'createdAt': datetime.now(timezone.utc),
        'updatedAt': datetime.now(timezone.utc),
    }
    ins = COL_ALERT_DEFS.insert_one(doc)
    doc['_id'] = ins.inserted_id
    return jsonify(_pub(doc)), 201

@app.route('/api/alert-definitions/<did>', methods=['PUT'])
@role_required("admin")
def update_alert_definition(did):
    try:
        oid = ObjectId(did)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    data = request.get_json(force=True) or {}
    patch = {}
    if 'name' in data:
        patch['name'] = (data['name'] or '').strip()
    if 'description' in data:
        patch['description'] = (data['description'] or '').strip()
    if 'icon' in data:
        patch['icon'] = (data['icon'] or '').strip()
    if 'color' in data:
        patch['color'] = (data['color'] or '').strip()
    if 'enabled' in data:
        patch['enabled'] = bool(data['enabled'])
    if 'params' in data:
        patch['params'] = data['params'] or {}
    if 'priority' in data:
        patch['priority'] = int(data.get('priority', 99))
    if 'groups' in data:
        raw_groups = data['groups'] or []
        try:
            patch['groups'] = [ObjectId(g) for g in raw_groups if g]
        except Exception:
            return jsonify({"error": "ID de groupe invalide"}), 400
    if 'whatsapp' in data:
        wa = data['whatsapp'] or {}
        patch['whatsapp'] = {
            'enabled': bool(wa.get('enabled', False)),
            'groups': [str(g) for g in (wa.get('groups') or [])],
            'dm_on_critical': bool(wa.get('dm_on_critical', False)),
            'dm_recipients': [str(p) for p in (wa.get('dm_recipients') or [])],
            'cooldown_minutes': int(wa.get('cooldown_minutes', 15)),
        }
    if not patch:
        return jsonify({"error": "Rien a modifier"}), 400
    patch['updatedAt'] = datetime.now(timezone.utc)
    res = COL_ALERT_DEFS.find_one_and_update(
        {"_id": oid}, {"$set": patch}, return_document=True
    )
    if not res:
        return jsonify({"error": "Definition introuvable"}), 404
    return jsonify(_pub(res))

@app.route('/api/alert-definitions/<did>', methods=['DELETE'])
@role_required("admin")
def delete_alert_definition(did):
    try:
        oid = ObjectId(did)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    r = COL_ALERT_DEFS.delete_one({"_id": oid})
    if r.deleted_count == 0:
        return jsonify({"error": "Definition introuvable"}), 404
    return jsonify({"ok": True})

# --- Watchlist ANPR ---

@app.route('/api/anpr-watchlist', methods=['GET'])
@role_required("admin")
def list_anpr_watchlist():
    docs = list(COL_ANPR_WATCHLIST.find().sort([('createdAt', -1)]))
    return jsonify([_pub(d) for d in docs])

@app.route('/api/anpr-watchlist', methods=['POST'])
@role_required("admin")
def add_anpr_watchlist():
    data = request.get_json(force=True) or {}
    plate = (data.get('plate') or '').strip().upper().replace(' ', '-')
    if not plate:
        return jsonify({"error": "La plaque est requise"}), 400
    if COL_ANPR_WATCHLIST.find_one({"plate": plate}):
        return jsonify({"error": "Cette plaque est deja dans la watchlist"}), 409
    # Trouver l'ID de la definition anpr-watchlist
    anpr_def = COL_ALERT_DEFS.find_one({"slug": "anpr-watchlist"})
    doc = {
        'plate': plate,
        'label': (data.get('label') or '').strip(),
        'alert_definition_id': anpr_def['_id'] if anpr_def else None,
        'enabled': bool(data.get('enabled', True)),
        'createdAt': datetime.now(timezone.utc),
        'updatedAt': datetime.now(timezone.utc),
    }
    ins = COL_ANPR_WATCHLIST.insert_one(doc)
    doc['_id'] = ins.inserted_id
    return jsonify(_pub(doc)), 201

@app.route('/api/anpr-watchlist/<wid>', methods=['PUT'])
@role_required("admin")
def update_anpr_watchlist(wid):
    try:
        oid = ObjectId(wid)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    data = request.get_json(force=True) or {}
    patch = {}
    if 'label' in data:
        patch['label'] = (data['label'] or '').strip()
    if 'enabled' in data:
        patch['enabled'] = bool(data['enabled'])
    if not patch:
        return jsonify({"error": "Rien a modifier"}), 400
    patch['updatedAt'] = datetime.now(timezone.utc)
    res = COL_ANPR_WATCHLIST.find_one_and_update(
        {"_id": oid}, {"$set": patch}, return_document=True
    )
    if not res:
        return jsonify({"error": "Plaque introuvable"}), 404
    return jsonify(_pub(res))

@app.route('/api/anpr-watchlist/<wid>', methods=['DELETE'])
@role_required("admin")
def delete_anpr_watchlist(wid):
    try:
        oid = ObjectId(wid)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    r = COL_ANPR_WATCHLIST.delete_one({"_id": oid})
    if r.deleted_count == 0:
        return jsonify({"error": "Plaque introuvable"}), 404
    return jsonify({"ok": True})

# --- Alertes actives (polling par le client) ---

def _get_user_alert_slugs(payload):
    """Retourne les slugs d'alertes autorisees pour l'utilisateur, ou None si tout est autorise."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return None  # admin voit tout
    email = payload.get("email", "")
    user_doc = db['users'].find_one({"email": email}, {"_id": 1})
    if not user_doc:
        user_group_ids = []
    else:
        ug = COL_USER_GROUPS.find_one({"user_id": user_doc["_id"]})
        user_group_ids = (ug.get("groups") or []) if ug else []
    # Utilisateurs sans groupe explicite -> inclure le groupe __default__
    if not user_group_ids:
        default_grp = db['cockpit_groups'].find_one({"is_default": True, "name": "__default__"})
        if default_grp:
            user_group_ids = [default_grp["_id"]]
    # Charger toutes les definitions activees
    all_defs = list(COL_ALERT_DEFS.find({"enabled": True}))
    allowed_slugs = []
    for d in all_defs:
        def_groups = d.get("groups") or []
        if not def_groups:
            # Pas de restriction de groupe -> tout le monde la recoit
            allowed_slugs.append(d["slug"])
        elif any(gid in def_groups for gid in user_group_ids):
            allowed_slugs.append(d["slug"])
    return allowed_slugs

@app.route('/api/active-alerts', methods=['GET'])
@role_required("user")
def get_active_alerts():
    payload = getattr(request, 'user_payload', {})
    allowed_slugs = _get_user_alert_slugs(payload)
    query = {"expiresAt": {"$gt": datetime.now(timezone.utc)}}
    if allowed_slugs is not None:
        query["definition_slug"] = {"$in": allowed_slugs}
    docs = list(COL_ACTIVE_ALERTS.find(query).sort([('triggeredAt', -1)]).limit(50))
    result = []
    for d in docs:
        d['_id'] = str(d['_id'])
        for k, v in d.items():
            if hasattr(v, 'isoformat'):
                # MongoDB stocke en UTC ; forcer le suffixe +00:00
                # pour que le navigateur interprete correctement
                if v.tzinfo is None:
                    v = v.replace(tzinfo=timezone.utc)
                d[k] = v.isoformat()
            elif isinstance(v, ObjectId):
                d[k] = str(v)
        result.append(d)
    return jsonify(result)

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


@app.route('/api/cluster-config', methods=['GET'])
@role_required("user")
def get_cluster_config():
    """Retourne la config de clustering pour la timeline (accessible a tous)."""
    configs = list(db.merge_config.find(
        {"cluster_enabled": True},
        {"_id": 0, "data_key": 1, "label": 1, "cluster_icon": 1,
         "timeline_category": 1, "activity_label": 1}
    ))
    return jsonify(configs)


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
        "department", "access_types", "merge_access_types",
        "todos_type", "vignette_fields",
        "cluster_enabled", "cluster_icon",
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


################################################################################
# Carte — Defauts globaux & preferences utilisateur
################################################################################

@app.route('/api/map-defaults', methods=['GET'])
@role_required("user")
def get_map_defaults():
    """Retourne les defauts globaux d'affichage de la carte."""
    doc = db.merge_config.find_one({"data_key": "__map_defaults__"}, {"_id": 0})
    if not doc:
        return jsonify({"hidden_categories": [], "default_tile": "osm", "hidden_route_colors": {}})
    return jsonify({
        "hidden_categories": doc.get("hidden_categories", []),
        "default_tile": doc.get("default_tile", "osm"),
        "hidden_route_colors": doc.get("hidden_route_colors", {})
    })


@app.route('/api/map-defaults', methods=['PUT'])
@role_required("admin")
@csrf.exempt
def set_map_defaults():
    """Sauvegarde les defauts globaux d'affichage carte (admin)."""
    data = request.get_json(force=True) or {}
    hidden = data.get("hidden_categories", [])
    tile = data.get("default_tile", "osm")
    if tile not in ("osm", "sat-egis", "sat-aco"):
        tile = "osm"
    hidden_colors = data.get("hidden_route_colors", {})
    db.merge_config.update_one(
        {"data_key": "__map_defaults__"},
        {"$set": {
            "data_key": "__map_defaults__",
            "hidden_categories": hidden,
            "default_tile": tile,
            "hidden_route_colors": hidden_colors
        }},
        upsert=True
    )
    return jsonify({"ok": True})


@app.route('/api/map-preferences', methods=['GET'])
@role_required("user")
def get_map_preferences():
    """Retourne les preferences carte de l'utilisateur courant."""
    payload = getattr(request, 'user_payload', {})
    email = payload.get("email", "")
    if not email:
        return jsonify({}), 200
    user = db.users.find_one({"email": email}, {"_id": 1})
    if not user:
        return jsonify({}), 200
    ug = COL_USER_GROUPS.find_one({"user_id": user["_id"]}, {"map_prefs": 1, "_id": 0})
    if not ug or "map_prefs" not in ug:
        return jsonify({}), 200
    return jsonify(ug["map_prefs"])


@app.route('/api/map-preferences', methods=['PUT'])
@role_required("user")
@csrf.exempt
def set_map_preferences():
    """Sauvegarde les preferences carte de l'utilisateur courant."""
    payload = getattr(request, 'user_payload', {})
    email = payload.get("email", "")
    if not email:
        return jsonify({"error": "Utilisateur non identifie"}), 400
    user = db.users.find_one({"email": email}, {"_id": 1})
    if not user:
        return jsonify({"error": "Utilisateur introuvable"}), 404
    data = request.get_json(force=True) or {}
    prefs = {}
    if "hidden_categories" in data:
        prefs["hidden_categories"] = data["hidden_categories"]
    if "default_tile" in data:
        tile = data["default_tile"]
        prefs["default_tile"] = tile if tile in ("osm", "sat-egis", "sat-aco") else "osm"
    if "hidden_route_colors" in data:
        prefs["hidden_route_colors"] = data["hidden_route_colors"]
    COL_USER_GROUPS.update_one(
        {"user_id": user["_id"]},
        {"$set": {"user_id": user["_id"], "map_prefs": prefs}},
        upsert=True
    )
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
# API Main courante (pcorg) — interventions PCO uniquement
################################################################################

_COMMENT_RE = re.compile(
    r'(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})\s*,\s*(.+?)\s*\n(.*?)(?=\d{2}/\d{2}/\d{4}|\Z)',
    re.DOTALL,
)

def _parse_comment_history(comment):
    if not comment:
        return []
    entries = []
    for m in _COMMENT_RE.finditer(comment):
        ts_raw, operator, text = m.group(1), m.group(2).strip(), m.group(3).strip()
        try:
            dt = datetime.strptime(ts_raw, "%d/%m/%Y %H:%M:%S")
            dt = dt.replace(tzinfo=ZoneInfo("Europe/Paris"))
            ts_iso = dt.isoformat()
        except ValueError:
            ts_iso = ts_raw
        entries.append({"ts": ts_iso, "operator": operator, "text": text})
    return entries


VALID_URGENCY_LEVELS = {"EU", "UA", "UR", "IMP"}

URGENCY_LABELS = {
    "SECOURS": {
        "EU": "D\u00e9tresse vitale", "UA": "Urgence absolue",
        "UR": "Urgence relative", "IMP": "Impliqu\u00e9 m\u00e9dical"
    },
    "SECURITE": {
        "EU": "Danger imm\u00e9diat", "UA": "Incident grave",
        "UR": "Incident en cours", "IMP": "T\u00e9moin / impliqu\u00e9"
    },
    "MIXTE": {
        "EU": "Urgence extr\u00eame", "UA": "Urgence prioritaire",
        "UR": "Situation stable", "IMP": "Impliqu\u00e9"
    }
}

def _urgency_type(category):
    if category == "PCO.Secours":
        return "SECOURS"
    if category == "PCO.Securite":
        return "SECURITE"
    return "MIXTE"

PCO_PROJECTION = {
    "_id": 1, "ts": 1, "close_ts": 1, "category": 1, "text": 1,
    "area": 1, "operator": 1, "severity": 1, "is_incident": 1,
    "gps": 1, "status_code": 1, "niveau_urgence": 1, "bounce_rev": 1,
    "content_category.sous_classification": 1,
    "content_category.patrouille": 1,
}

def _clean_operator(name):
    if not name:
        return ""
    return re.sub(r'\s*\[.*?\]\s*$', '', name).strip()

def _pcorg_serialise(doc):
    """Aplatit un document pcorg pour le JSON frontend."""
    gps = doc.get("gps")
    coords = gps.get("coordinates") if gps and isinstance(gps, dict) else None
    cc = doc.get("content_category") or {}
    area = doc.get("area") or {}
    ts = doc.get("ts")
    close_ts = doc.get("close_ts")
    return {
        "id": str(doc["_id"]),
        "ts": ts.isoformat() if isinstance(ts, datetime) else ts,
        "close_ts": close_ts.isoformat() if isinstance(close_ts, datetime) else close_ts,
        "category": doc.get("category"),
        "text": doc.get("text") or "",
        "area_id": area.get("id"),
        "area_desc": area.get("desc") or "",
        "operator": _clean_operator(doc.get("operator")),
        "severity": doc.get("severity", 0),
        "is_incident": doc.get("is_incident", False),
        "status_code": doc.get("status_code", 0),
        "sous_classification": cc.get("sous_classification") or "",
        "patrouille": cc.get("patrouille") or "",
        "lat": coords[1] if coords and len(coords) >= 2 else None,
        "lon": coords[0] if coords and len(coords) >= 2 else None,
        "server": doc.get("server"),
        "niveau_urgence": doc.get("niveau_urgence"),
        "bounce_rev": doc.get("bounce_rev", 0),
    }


@app.route('/api/pcorg/live', methods=['GET'])
@role_required("user")
def pcorg_live():
    event = request.args.get("event", "")
    year = request.args.get("year", "")
    if not event or not year:
        return jsonify({"error": "event et year requis"}), 400
    try:
        year = int(year)
    except ValueError:
        return jsonify({"error": "year invalide"}), 400

    base = {"event": event, "year": year, "category": {"$regex": "^PCO"}}
    col = db["pcorg"]

    open_docs = list(col.find(
        {**base, "status_code": {"$nin": [10]}},
        PCO_PROJECTION
    ).sort("ts", -1))

    closed_docs = list(col.find(
        {**base, "status_code": 10},
        PCO_PROJECTION
    ).sort("close_ts", -1).limit(100))

    return jsonify({
        "open": [_pcorg_serialise(d) for d in open_docs],
        "closed": [_pcorg_serialise(d) for d in closed_docs],
        "counts": {"open": len(open_docs), "closed": len(closed_docs)},
    })


@app.route('/api/pcorg/detail/<doc_id>', methods=['GET'])
@role_required("user")
def pcorg_detail(doc_id):
    doc = db["pcorg"].find_one({"_id": doc_id})
    if not doc:
        return jsonify({"error": "introuvable"}), 404
    gps = doc.get("gps")
    coords = gps.get("coordinates") if gps and isinstance(gps, dict) else None
    cc = doc.get("content_category") or {}
    area = doc.get("area") or {}
    ts = doc.get("ts")
    close_ts = doc.get("close_ts")
    # comment_history : utiliser le champ stocke, sinon parser a la volee
    comment_history = doc.get("comment_history")
    if comment_history is None:
        comment_history = _parse_comment_history(doc.get("comment"))

    # Resoudre le groupe cockpit de l'operateur
    operator_group = ""
    server = doc.get("server") or ""
    op_email = doc.get("operator_id_create") or ""
    if op_email and server != "SQL":
        op_user = db['users'].find_one({"email": op_email}, {"_id": 1})
        if op_user:
            op_ug = COL_USER_GROUPS.find_one({"user_id": op_user["_id"]})
            op_gids = (op_ug.get("groups") or []) if op_ug else []
            if op_gids:
                op_groups = list(COL_GROUPS.find(
                    {"_id": {"$in": op_gids}, "name": {"$nin": list(SYSTEM_GROUP_NAMES)}},
                    {"name": 1}
                ))
                operator_group = ", ".join(g["name"] for g in op_groups)
    # Fiches SQL : utiliser le groupe par defaut configure
    if not operator_group:
        sql_setting = db["cockpit_settings"].find_one({"_id": "sql_default_group"})
        if sql_setting and sql_setting.get("group_id"):
            try:
                sql_grp = COL_GROUPS.find_one({"_id": ObjectId(sql_setting["group_id"])}, {"name": 1})
                if sql_grp and sql_grp.get("name") not in SYSTEM_GROUP_NAMES:
                    operator_group = sql_grp["name"]
            except Exception:
                pass

    return jsonify({
        "id": str(doc["_id"]),
        "sql_id": doc.get("sql_id"),
        "ts": ts.isoformat() if isinstance(ts, datetime) else ts,
        "close_ts": close_ts.isoformat() if isinstance(close_ts, datetime) else close_ts,
        "category": doc.get("category"),
        "text": doc.get("text") or "",
        "text_full": doc.get("text_full") or "",
        "comment": doc.get("comment") or "",
        "comment_history": comment_history or [],
        "area_id": area.get("id"),
        "area_desc": area.get("desc") or "",
        "operator": _clean_operator(doc.get("operator")),
        "operator_group": operator_group,
        "operator_close": _clean_operator(doc.get("operator_close")),
        "severity": doc.get("severity", 0),
        "is_incident": doc.get("is_incident", False),
        "status_code": doc.get("status_code", 0),
        "content_category": cc,
        "group_desc": (doc.get("group") or {}).get("desc") or "",
        "phones": (doc.get("extracted") or {}).get("phones"),
        "plates": (doc.get("extracted") or {}).get("plates"),
        "lat": coords[1] if coords and len(coords) >= 2 else None,
        "lon": coords[0] if coords and len(coords) >= 2 else None,
        "server": doc.get("server"),
        "niveau_urgence": doc.get("niveau_urgence"),
        "bounce_rev": doc.get("bounce_rev", 0),
    })


def _engage_field_device(patrouille_name, fiche_id, event, year, category="", text=""):
    """Assigne une fiche a une tablette terrain (pose active_fiche_id)
    sans changer le statut : c'est l'operateur tablette qui confirmera
    son engagement via le bouton 'Engagement'."""
    if not patrouille_name or not fiche_id:
        return
    device = db["field_devices"].find_one({
        "name": patrouille_name,
        "event": str(event),
        "year": str(year),
    })
    if not device:
        return
    now = datetime.now(timezone.utc)
    db["field_devices"].update_one(
        {"_id": device["_id"]},
        {
            "$set": {
                "active_fiche_id": fiche_id,
            },
            "$push": {
                "status_history": {
                    "status": "dispatch",
                    "ts": now,
                    "trigger": "cockpit_dispatch",
                    "fiche_id": fiche_id,
                },
            },
        },
    )
    # Notification push vers la tablette
    try:
        from field import send_push_to_device
        cat_short = (category or "Intervention").replace("PCO.", "")
        body = (text or "Nouvelle intervention")[:120]
        send_push_to_device(
            db, device["_id"],
            title="Dispatch : " + cat_short,
            body=body,
            url="/field",
            tag="dispatch-" + str(fiche_id),
        )
    except Exception:
        pass  # non-bloquant


def _disengage_field_device(doc, fiche_id):
    """Quand une fiche est cloturee, remet la tablette associee en patrouille."""
    try:
        cc = (doc or {}).get("content_category") or {}
        patrouille_name = cc.get("patrouille")
        if not patrouille_name:
            return
        event = doc.get("event") or ""
        year = doc.get("year") or ""
        device = db["field_devices"].find_one({
            "name": patrouille_name,
            "event": str(event),
            "year": str(year),
            "active_fiche_id": fiche_id,
        })
        if not device:
            return
        now = datetime.now(timezone.utc)
        db["field_devices"].update_one(
            {"_id": device["_id"]},
            {
                "$set": {
                    "status": "patrouille",
                    "status_since": now,
                    "active_fiche_id": None,
                },
                "$push": {
                    "status_history": {
                        "status": "patrouille",
                        "ts": now,
                        "trigger": "cockpit_close",
                        "fiche_id": fiche_id,
                    },
                },
            },
        )
    except Exception:
        pass  # non-bloquant


def _pcorg_mk_uuid(event, year, ts_str, category, text, area_id, user_id):
    seed = (
        f"{event}|{year}|{ts_str.strip()}"
        f"|{(category or '').strip()}|{(text or '').strip()}"
        f"|{str(area_id or '').strip()}|{str(user_id or '').strip()}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


@app.route('/api/pcorg/create', methods=['POST'])
@role_required("user")
def pcorg_create():
    data = request.get_json(force=True)
    event = data.get("event", "")
    year = data.get("year", "")
    category = data.get("category", "")
    text = data.get("text", "").strip()
    if not event or not year or not category or not text:
        return jsonify({"error": "event, year, category et text requis"}), 400
    if not category.startswith("PCO."):
        return jsonify({"error": "categorie invalide (doit commencer par PCO.)"}), 400
    try:
        year = int(year)
    except ValueError:
        return jsonify({"error": "year invalide"}), 400

    now = datetime.now(ZoneInfo("Europe/Paris"))
    ts_str = now.isoformat()
    user = request.user_payload
    operator_name = f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()

    lat = data.get("lat")
    lon = data.get("lon")
    gps = None
    if lat is not None and lon is not None:
        try:
            gps = {"type": "Point", "coordinates": [float(lon), float(lat)]}
        except (ValueError, TypeError):
            pass

    niveau_urgence = data.get("niveau_urgence")
    if niveau_urgence and niveau_urgence not in VALID_URGENCY_LEVELS:
        return jsonify({"error": "niveau_urgence invalide"}), 400

    area_desc = data.get("area_desc", "")
    content_cat = data.get("content_category") or {}
    initial_comment = (data.get("comment") or "").strip()

    doc_id = _pcorg_mk_uuid(event, year, ts_str, category, text, "", str(user.get("email", "")))

    # Build initial comment / comment_history
    comment_raw = ""
    comment_history = []
    if initial_comment:
        ts_fmt = now.strftime("%d/%m/%Y %H:%M:%S")
        comment_raw = f"{ts_fmt} , {operator_name}\n {initial_comment}\n"
        comment_history.append({
            "ts": now.isoformat(),
            "operator": operator_name,
            "text": initial_comment,
        })

    doc = {
        "_id": doc_id,
        "event": event,
        "year": year,
        "ts": now,
        "timestamp_iso": ts_str,
        "close_ts": None,
        "close_iso": None,
        "category": category,
        "source": category,
        "text": text,
        "text_full": text,
        "comment": comment_raw,
        "comment_history": comment_history,
        "operator": operator_name,
        "operator_id_create": user.get("email", ""),
        "operator_close": None,
        "operator_id_close": None,
        "status_code": 0,
        "severity": 0,
        "niveau_urgence": niveau_urgence,
        "is_incident": False,
        "area": {"id": None, "desc": area_desc} if area_desc else None,
        "gps": gps,
        "group": None,
        "content_category": content_cat,
        "extracted": {"phones": None, "plates": None},
        "tags": [],
        "synced_at": None,
        "sql_id": None,
        "guid": None,
        "server": "COCKPIT",
        "bounce_rev": 1,
    }

    db["pcorg"].insert_one(doc)

    # Engager la tablette terrain si patrouille correspond
    patr = content_cat.get("patrouille", "")
    if patr:
        _engage_field_device(patr, doc_id, event, year, category=category, text=text)

    return jsonify({"ok": True, "id": doc_id})


@app.route('/api/pcorg/quick-create', methods=['POST'])
@role_required("user")
def pcorg_quick_create():
    """Creation rapide d'une fiche simplifiee (clic droit carte)."""
    data = request.get_json(force=True)
    event = data.get("event", "")
    year = data.get("year", "")
    category = data.get("category", "")
    niveau_urgence = data.get("niveau_urgence", "")
    if not event or not year or not category or not niveau_urgence:
        return jsonify({"error": "event, year, category et niveau_urgence requis"}), 400
    if not category.startswith("PCO."):
        return jsonify({"error": "categorie invalide"}), 400
    if niveau_urgence not in VALID_URGENCY_LEVELS:
        return jsonify({"error": "niveau_urgence invalide"}), 400
    try:
        year = int(year)
    except ValueError:
        return jsonify({"error": "year invalide"}), 400

    # Verifier permissions : categorie + groupe
    config = COL_PCORG_CONFIG.find_one({"_id": "pcorg_lists"})
    cat_fs = (config or {}).get("fiche_simplifiee", {})
    if not cat_fs.get(category):
        return jsonify({"error": "Fiche simplifiee non activee pour cette categorie"}), 403
    if not _user_can_fiche_simplifiee(request.user_payload):
        return jsonify({"error": "Votre groupe n'autorise pas les fiches simplifiees"}), 403

    now = datetime.now(ZoneInfo("Europe/Paris"))
    ts_str = now.isoformat()
    user = request.user_payload
    operator_name = f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()

    lat = data.get("lat")
    lon = data.get("lon")
    carroye = (data.get("carroye") or "").strip()
    area_desc = (data.get("area_desc") or "").strip()
    gps = None
    if lat is not None and lon is not None:
        try:
            gps = {"type": "Point", "coordinates": [float(lon), float(lat)]}
        except (ValueError, TypeError):
            pass

    # Recuperer le(s) nom(s) de groupe de l'utilisateur
    group_name = ""
    email = user.get("email", "")
    user_doc = db['users'].find_one({"email": email}, {"_id": 1})
    if user_doc:
        ug = COL_USER_GROUPS.find_one({"user_id": user_doc["_id"]})
        gids = (ug.get("groups") or []) if ug else []
        if gids:
            groups = list(COL_GROUPS.find(
                {"_id": {"$in": gids}, "name": {"$nin": list(SYSTEM_GROUP_NAMES)}},
                {"name": 1}
            ))
            group_name = ", ".join(g["name"] for g in groups)
    if not group_name:
        app_role = user.get("app_role", "user")
        if app_role == "admin" or user.get("is_super_admin"):
            group_name = "Admin"

    # Generer la description automatique
    utype = _urgency_type(category)
    label = URGENCY_LABELS.get(utype, URGENCY_LABELS["MIXTE"]).get(niveau_urgence, niveau_urgence)
    text = f"Cette fiche a ete generee en procedure d'urgence par {operator_name}"
    if group_name:
        text += f" du {group_name}"

    doc_id = _pcorg_mk_uuid(event, year, ts_str, category, text, "", str(user.get("email", "")))

    patrouille = (data.get("patrouille") or "").strip()
    content_category = {}
    if carroye:
        content_category["carroye"] = carroye
    if patrouille:
        content_category["patrouille"] = patrouille

    doc = {
        "_id": doc_id,
        "event": event,
        "year": year,
        "ts": now,
        "timestamp_iso": ts_str,
        "close_ts": None,
        "close_iso": None,
        "category": category,
        "source": category,
        "text": text,
        "text_full": text,
        "comment": "",
        "comment_history": [],
        "operator": operator_name,
        "operator_id_create": user.get("email", ""),
        "operator_close": None,
        "operator_id_close": None,
        "status_code": 0,
        "severity": 0,
        "niveau_urgence": niveau_urgence,
        "is_incident": False,
        "area": {"id": None, "desc": area_desc} if area_desc else None,
        "gps": gps,
        "group": None,
        "content_category": content_category,
        "extracted": {"phones": None, "plates": None},
        "tags": [],
        "synced_at": None,
        "sql_id": None,
        "guid": None,
        "server": "COCKPIT",
        "bounce_rev": 1,
    }

    db["pcorg"].insert_one(doc)

    # Engager la tablette terrain si patrouille correspond
    if patrouille:
        _engage_field_device(patrouille, doc_id, event, year, category=category, text=text)

    return jsonify({"ok": True, "id": doc_id})


@app.route('/api/pcorg/update/<doc_id>', methods=['PUT'])
@role_required("user")
def pcorg_update(doc_id):
    """Met a jour les champs d'une intervention (SQL ou COCKPIT)."""
    doc = db["pcorg"].find_one({"_id": doc_id}, {"status_code": 1, "event": 1, "year": 1, "category": 1, "text": 1})
    if not doc:
        return jsonify({"error": "introuvable"}), 404
    if doc.get("status_code") == 10:
        return jsonify({"error": "intervention close, non editable"}), 403

    data = request.get_json(force=True)

    sets = {}
    # Champs de base
    if "text" in data:
        txt = (data["text"] or "").strip()
        if txt:
            sets["text"] = txt
            sets["text_full"] = txt
    if "category" in data and data["category"]:
        sets["category"] = data["category"]
        sets["source"] = data["category"]
    if "area_desc" in data:
        sets["area.desc"] = data["area_desc"]
    if "niveau_urgence" in data:
        nu = data["niveau_urgence"]
        if nu and nu not in VALID_URGENCY_LEVELS:
            return jsonify({"error": "niveau_urgence invalide"}), 400
        sets["niveau_urgence"] = nu or None

    # GPS
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is not None and lon is not None:
        try:
            sets["gps"] = {"type": "Point", "coordinates": [float(lon), float(lat)]}
        except (ValueError, TypeError):
            pass

    # content_category : merge
    cc_update = data.get("content_category")
    if cc_update and isinstance(cc_update, dict):
        for k, v in cc_update.items():
            sets[f"content_category.{k}"] = v

    if not sets:
        return jsonify({"error": "rien a mettre a jour"}), 400

    db["pcorg"].update_one({"_id": doc_id}, {"$set": sets})

    # Si patrouille a ete modifie, engager la tablette terrain
    new_patr = (cc_update or {}).get("patrouille", "")
    if new_patr:
        cat = sets.get("category") or doc.get("category", "")
        txt = sets.get("text") or doc.get("text", "")
        _engage_field_device(new_patr, doc_id, doc.get("event", ""), doc.get("year", ""),
                             category=cat, text=txt)

    return jsonify({"ok": True})


@app.route('/api/pcorg/comment/<doc_id>', methods=['POST'])
@role_required("user")
def pcorg_add_comment(doc_id):
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text requis"}), 400

    user = request.user_payload
    operator_name = f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()
    now = datetime.now(ZoneInfo("Europe/Paris"))
    ts_fmt = now.strftime("%d/%m/%Y %H:%M:%S")

    photo_url = (data.get("photo") or "").strip() or None

    comment_line = f"{ts_fmt} , {operator_name}\n {text}\n"
    history_entry = {
        "ts": now.isoformat(),
        "operator": operator_name,
        "text": text,
    }
    if photo_url:
        history_entry["photo"] = photo_url

    doc = db["pcorg"].find_one({"_id": doc_id}, {"comment": 1})
    if not doc:
        return jsonify({"error": "introuvable"}), 404

    old_comment = doc.get("comment") or ""
    new_comment = old_comment + comment_line if old_comment else comment_line

    db["pcorg"].update_one(
        {"_id": doc_id},
        {
            "$set": {"comment": new_comment},
            "$push": {"comment_history": history_entry},
            "$inc": {"bounce_rev": 1},
        }
    )
    return jsonify({"ok": True, "entry": history_entry})


@app.route('/api/pcorg/camera-capture', methods=['POST'])
@role_required("user")
def pcorg_camera_capture():
    """Capture une image depuis une camera HIK et l'attache a une fiche."""
    import shutil
    data = request.get_json(force=True)
    cam_id = (data.get("cam_id") or "").strip()
    fiche_id = (data.get("fiche_id") or "").strip()
    if not cam_id:
        return jsonify({"error": "cam_id requis"}), 400

    # Charger la camera depuis MongoDB
    from bson.objectid import ObjectId
    from cameras import HIK_PASSWORD
    try:
        oid = ObjectId(cam_id)
    except Exception:
        return jsonify({"error": "cam_id invalide"}), 400
    cam_doc = db["cockpit_cameras"].find_one({"_id": oid})
    if not cam_doc:
        return jsonify({"error": "Camera introuvable"}), 404

    # Instancier et capturer
    from hik.hik_control import HikCamera
    password = cam_doc.get("password", "") or HIK_PASSWORD
    cam = HikCamera(
        name=cam_doc["name"],
        ip=cam_doc["ip"],
        port=cam_doc.get("port", 80),
        user=cam_doc.get("user", "admin"),
        password=password,
        channel=cam_doc.get("channel", 1),
        protocol=cam_doc.get("protocol", "http"),
        brand=cam_doc.get("brand", "hikvision"),
    )

    import uuid as _uuid
    photo_id = str(_uuid.uuid4())[:8]
    ts = datetime.now(ZoneInfo("Europe/Paris"))
    ts_fmt = ts.strftime("%d/%m/%Y %H:%M:%S")
    ts_file = ts.strftime("%Y%m%d_%H%M%S")

    # Si fiche fournie, on stocke avec event/year dans field_photos
    # Sinon on stocke dans un dossier generique
    fiche_doc = None
    if fiche_id:
        fiche_doc = db["pcorg"].find_one({"_id": fiche_id}, {"event": 1, "year": 1})
    event_name = (fiche_doc or {}).get("event", "cockpit")
    year_val = str((fiche_doc or {}).get("year", ts.year))
    sub_dir = f"{event_name}/{year_val}"

    photos_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "uploads", "field_photos", event_name, year_val)
    os.makedirs(photos_dir, exist_ok=True)

    safe_cam_name = cam_doc["name"].replace(" ", "_")[:20]
    filename = f"{photo_id}_cam_{safe_cam_name}_{ts_file}.jpg"
    save_path = os.path.join(photos_dir, filename)

    try:
        cam.capture_image(save_path)
    except Exception as e:
        logger.exception("Camera capture failed for fiche %s, cam %s", fiche_id, cam_id)
        return jsonify({"error": f"Capture echouee: {e}"}), 500

    photo_url = f"/field/photos/{sub_dir}/{filename}"

    # Mettre a jour aussi le latest de la camera (pour les vignettes)
    cam_snap_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "uploads", "camera_snapshots")
    os.makedirs(cam_snap_dir, exist_ok=True)
    latest_path = os.path.join(cam_snap_dir, f"{cam_id}_latest.jpg")
    shutil.copy2(save_path, latest_path)

    # Si fiche_id fourni, ajouter en commentaire
    result = {"ok": True, "photo": photo_url, "cam_name": cam_doc["name"]}
    if fiche_id and fiche_doc:
        user = request.user_payload
        operator_name = f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()
        comment_text = f"Capture camera {cam_doc['name']}"
        comment_line = f"{ts_fmt} , {operator_name}\n {comment_text}\n"
        history_entry = {
            "ts": ts.isoformat(),
            "operator": operator_name,
            "text": comment_text,
            "photo": photo_url,
        }
        old = (db["pcorg"].find_one({"_id": fiche_id}, {"comment": 1}) or {}).get("comment", "")
        new_comment = (old + comment_line) if old else comment_line
        db["pcorg"].update_one(
            {"_id": fiche_id},
            {
                "$set": {"comment": new_comment},
                "$push": {"comment_history": history_entry},
                "$inc": {"bounce_rev": 1},
            }
        )
        result["entry"] = history_entry

    return jsonify(result)


@app.route('/api/pcorg/update-gps/<doc_id>', methods=['POST'])
@role_required("user")
def pcorg_update_gps(doc_id):
    data = request.get_json(force=True)
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is None or lon is None:
        return jsonify({"error": "lat et lon requis"}), 400
    try:
        lat = float(lat)
        lon = float(lon)
    except (ValueError, TypeError):
        return jsonify({"error": "lat/lon invalides"}), 400

    result = db["pcorg"].update_one(
        {"_id": doc_id},
        {"$set": {"gps": {"type": "Point", "coordinates": [lon, lat]}}}
    )
    if result.matched_count == 0:
        return jsonify({"error": "introuvable"}), 404
    return jsonify({"ok": True})


@app.route('/api/pcorg/set-urgency/<doc_id>', methods=['POST'])
@role_required("user")
def pcorg_set_urgency(doc_id):
    """Change le niveau d'urgence et consigne l'action dans la chronologie."""
    data = request.get_json(force=True)
    niveau = data.get("niveau_urgence")
    if niveau and niveau not in VALID_URGENCY_LEVELS:
        return jsonify({"error": "niveau_urgence invalide"}), 400

    doc = db["pcorg"].find_one({"_id": doc_id}, {"niveau_urgence": 1, "category": 1})
    if not doc:
        return jsonify({"error": "introuvable"}), 404

    old_niveau = doc.get("niveau_urgence")
    if old_niveau == niveau:
        return jsonify({"ok": True, "unchanged": True})

    user = request.user_payload
    operator_name = f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()
    now = datetime.now(ZoneInfo("Europe/Paris"))
    ts_fmt = now.strftime("%d/%m/%Y %H:%M:%S")

    cat = doc.get("category", "")
    utype = _urgency_type(cat)
    labels = URGENCY_LABELS.get(utype, URGENCY_LABELS["MIXTE"])
    old_label = labels.get(old_niveau, old_niveau or "Aucun")
    new_label = labels.get(niveau, niveau or "Aucun") if niveau else "Aucun"
    action_text = f"Niveau d'urgence : {old_label} \u2192 {new_label}"

    comment_line = f"{ts_fmt} , {operator_name}\n {action_text}\n"
    history_entry = {
        "ts": now.isoformat(),
        "operator": operator_name,
        "text": action_text,
    }

    old_comment = (db["pcorg"].find_one({"_id": doc_id}, {"comment": 1}) or {}).get("comment") or ""
    new_comment = old_comment + comment_line if old_comment else comment_line

    db["pcorg"].update_one(
        {"_id": doc_id},
        {
            "$set": {"niveau_urgence": niveau, "comment": new_comment},
            "$push": {"comment_history": history_entry},
            "$inc": {"bounce_rev": 1},
        }
    )
    return jsonify({"ok": True, "entry": history_entry})


@app.route('/api/pcorg/close/<doc_id>', methods=['POST'])
@role_required("user")
def pcorg_close(doc_id):
    if not _user_can_close_fiche(request.user_payload):
        return jsonify({"error": "Votre groupe n'autorise pas la cloture de fiches"}), 403
    user = request.user_payload
    operator_name = f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()
    now = datetime.now(ZoneInfo("Europe/Paris"))
    ts_str = now.strftime("%d/%m/%Y %H:%M:%S")

    comment_line = f"{ts_str} , {operator_name} \n Statut: En cours -> Termine\n"
    history_entry = {
        "ts": now.isoformat(),
        "operator": operator_name,
        "text": "Statut: En cours -> Termine",
    }

    # Lire le comment existant pour le concatener
    doc = db["pcorg"].find_one(
        {"_id": doc_id, "status_code": {"$ne": 10}},
        {"comment": 1, "content_category": 1, "event": 1, "year": 1}
    )
    if not doc:
        return jsonify({"error": "introuvable ou deja clos"}), 404

    old_comment = doc.get("comment") or ""
    new_comment = old_comment + comment_line if old_comment else comment_line

    db["pcorg"].update_one(
        {"_id": doc_id},
        {
            "$set": {
                "close_ts": now,
                "close_iso": now.isoformat(),
                "status_code": 10,
                "operator_close": operator_name,
                "operator_id_close": user.get("email", ""),
                "comment": new_comment,
            },
            "$push": {"comment_history": history_entry},
        }
    )

    # Auto-disengage: reset tablet to "patrouille" when cockpit closes fiche
    _disengage_field_device(doc, doc_id)

    return jsonify({"ok": True})


@app.route('/api/field-device/release', methods=['POST'])
@role_required("user")
def field_device_release():
    """Libere un vehicule/tablette en fin d'intervention.
    Passe le device en patrouille, vide active_fiche_id, ajoute l'historique.
    Si le device n'a pas laisse de commentaire de fin, le cockpit doit en fournir un."""
    data = request.get_json(force=True)
    device_name = (data.get("device_name") or "").strip()
    event = data.get("event", "")
    year = data.get("year", "")
    cockpit_comment = (data.get("comment") or "").strip()

    if not device_name or not event:
        return jsonify({"error": "device_name et event requis"}), 400

    device = db["field_devices"].find_one({
        "name": device_name,
        "event": str(event),
        "year": str(year),
    })
    if not device:
        return jsonify({"error": "device introuvable"}), 404

    if device.get("status") != "fin_intervention":
        return jsonify({"error": "Le device n'est pas en fin d'intervention"}), 400

    # Si pas de commentaire tablette, le cockpit doit en fournir un
    fin_comment = device.get("fin_comment") or ""
    if not fin_comment and not cockpit_comment:
        return jsonify({"error": "comment_required",
                        "message": "L'operateur n'a pas laisse de commentaire, vous devez en saisir un"}), 400

    user = request.user_payload
    operator_name = f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()
    now = datetime.now(timezone.utc)
    now_local = datetime.now(ZoneInfo("Europe/Paris"))

    # Remettre le device en patrouille
    db["field_devices"].update_one(
        {"_id": device["_id"]},
        {
            "$set": {
                "status": "patrouille",
                "status_since": now,
                "active_fiche_id": None,
                "fin_comment": None,
            },
            "$push": {
                "status_history": {
                    "status": "patrouille",
                    "ts": now,
                    "trigger": "cockpit_release",
                    "operator": operator_name,
                },
            },
        },
    )

    # Ajouter un commentaire dans la fiche si active
    fiche_id = device.get("active_fiche_id")
    if fiche_id:
        ts_fmt = now_local.strftime("%d/%m/%Y %H:%M:%S")
        release_text = f"Liberation de {device_name} par {operator_name}"
        if cockpit_comment:
            release_text += f" : {cockpit_comment}"
        comment_line = f"{ts_fmt} , {operator_name}\n {release_text}\n"
        history_entry = {
            "ts": now_local.isoformat(),
            "operator": operator_name,
            "text": release_text,
        }
        old_doc = db["pcorg"].find_one({"_id": fiche_id}, {"comment": 1})
        if old_doc:
            old_comment = old_doc.get("comment") or ""
            new_comment = old_comment + comment_line if old_comment else comment_line
            db["pcorg"].update_one(
                {"_id": fiche_id},
                {
                    "$set": {"comment": new_comment, "content_category.patrouille": ""},
                    "$push": {"comment_history": history_entry},
                },
            )

    return jsonify({"ok": True, "device_name": device_name})


@app.route('/api/pcorg/delete/<doc_id>', methods=['DELETE'])
@role_required("admin")
def pcorg_delete(doc_id):
    """Supprime une fiche d'intervention (admin uniquement)."""
    result = db["pcorg"].delete_one({"_id": doc_id})
    if result.deleted_count == 0:
        return jsonify({"error": "introuvable"}), 404
    return jsonify({"ok": True})


################################################################################
# Configuration Main courante (listes de reference PCO)
################################################################################

COL_PCORG_CONFIG = db['pcorg_config']


def _make_item(label):
    """Cree un item {id, label} avec un id court unique."""
    return {"id": uuid.uuid4().hex[:8], "label": label}


def _migrate_pcorg_config():
    """Migre les anciennes listes de strings vers des objets {id, label}."""
    doc = COL_PCORG_CONFIG.find_one({"_id": "pcorg_lists"})
    if not doc:
        return False
    changed = False
    # Migrer sous_classifications
    sc = doc.get("sous_classifications") or {}
    for cat, items in sc.items():
        if items and isinstance(items[0], str):
            sc[cat] = [_make_item(s) for s in items]
            changed = True
    # Migrer intervenants
    interv = doc.get("intervenants") or []
    if interv and isinstance(interv[0], str):
        doc["intervenants"] = [_make_item(s) for s in interv]
        changed = True
    # Migrer services
    svcs = doc.get("services") or []
    if svcs and isinstance(svcs[0], str):
        doc["services"] = [_make_item(s) for s in svcs]
        changed = True
    if changed:
        COL_PCORG_CONFIG.replace_one({"_id": "pcorg_lists"}, doc)
    return changed


# Seed initial si collection vide
if COL_PCORG_CONFIG.count_documents({}) == 0:
    _seed = {
        "sous_classifications": {
            "PCO.Secours": [_make_item(s) for s in [
                "Secours a victime", "Accident de circulation", "Depart de feux", "Incendie", "Malaise"]],
            "PCO.Securite": [_make_item(s) for s in [
                "Intrusion", "Altercation-Rixe", "Vol", "Gene a la circulation",
                "Acte de malveillance", "Stationnement genant", "Colis ou objet suspect",
                "Enfant perdu", "Degradation", "Agression", "Fraude accreditation-billet",
                "Nuisances sonores", "Drone non autorise", "Ivresse manifeste", "Stupefiants"]],
            "PCO.Technique": [_make_item(s) for s in [
                "Logistique", "Electricite", "Sanitaire", "Informatique",
                "Barrierage", "Signaletique", "Cloture", "Fluide", "Controle Acces",
                "Serrurerie", "Portail - Portillon"]],
            "PCO.Flux": [_make_item(s) for s in [
                "Congestion vehicules", "Congestion pietons", "Renfort controle acces",
                "Passage pieton a securiser", "Voie secours encombree",
                "Balisage-Barrierage a poser", "Regulation manuelle demandee",
                "Parking complet-Sorties saturees", "Evacuation de foule"]],
        },
        "intervenants": [_make_item(s) for s in [
            "Appui Flux Moto", "Equipe securite", "Equipe technique",
            "CMS", "SDIS", "Gendarmerie", "Police municipale",
            "Ambulance", "SAMU", "DPS", "PC Securite"]],
        "services": [_make_item(s) for s in [
            "CMS", "SDIS 72", "SAMU 72", "Gendarmerie", "Police municipale",
            "DPS", "PC Securite", "PC Course", "Direction technique",
            "Direction securite", "Accueil", "Billetterie"]],
    }
    COL_PCORG_CONFIG.insert_one({"_id": "pcorg_lists", **_seed})
else:
    _migrate_pcorg_config()


@app.route('/api/pcorg-config', methods=['GET'])
@role_required("user")
def get_pcorg_config():
    doc = COL_PCORG_CONFIG.find_one({"_id": "pcorg_lists"}, {"_id": 0})
    return jsonify(doc or {})


@app.route('/api/pcorg-config', methods=['PUT'])
@role_required("admin")
def update_pcorg_config():
    data = request.get_json(force=True)
    allowed = {"sous_classifications", "intervenants", "services", "fiche_simplifiee", "urgence_categories"}
    update = {k: v for k, v in data.items() if k in allowed}
    if not update:
        return jsonify({"error": "rien a mettre a jour"}), 400
    COL_PCORG_CONFIG.update_one(
        {"_id": "pcorg_lists"},
        {"$set": update},
        upsert=True,
    )
    return jsonify({"ok": True})


PCORG_SYNC_CONTROL_ID = "pcorg_sync_control"

@app.route('/api/pcorg/sync-control', methods=['GET'])
@role_required("user")
def pcorg_sync_control_get():
    """Retourne l'etat du controle de sync PC Organisation."""
    doc = db["pcorg_sync_config"].find_one({"_id": PCORG_SYNC_CONTROL_ID}) or {}
    doc.pop("_id", None)
    for k in ("last_run", "last_success"):
        if hasattr(doc.get(k), "isoformat"):
            doc[k] = doc[k].isoformat()
    return jsonify(doc)


@app.route('/api/pcorg/sync-control', methods=['PUT'])
@role_required("admin")
def pcorg_sync_control_set():
    """Active ou desactive la sync automatique PC Organisation."""
    data = request.get_json(force=True)
    update = {}
    if "actif" in data:
        update["actif"] = bool(data["actif"])
    if not update:
        return jsonify({"error": "rien a mettre a jour"}), 400
    db["pcorg_sync_config"].update_one(
        {"_id": PCORG_SYNC_CONTROL_ID},
        {"$set": update},
        upsert=True,
    )
    return jsonify({"ok": True})


@app.route('/api/pcorg/force-sync', methods=['POST'])
@role_required("admin")
def pcorg_force_sync():
    """Lance une synchronisation PC Organisation SQL -> MongoDB a la demande."""
    script = os.path.join(os.path.dirname(__file__), "pcorg_sync.py")
    python_exe = "E:\\TITAN\\production\\titan_prod\\Scripts\\python.exe"
    data = request.get_json(force=True) if request.is_json else {}
    cmd = [python_exe, "-X", "utf8", script, "--force"]
    if data.get("full"):
        cmd.append("--full")
    try:
        subprocess.Popen(
            cmd,
            cwd=os.path.dirname(__file__),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return jsonify({"ok": True, "message": "Sync lancee en arriere-plan"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


################################################################################
# Assistant IA : resume de periode des fiches PC Organisation
################################################################################

def _parse_period_dt(raw):
    """Parse une date ISO 8601 (avec ou sans tz) en datetime aware UTC.

    Accepte aussi le format datetime-local HTML (YYYY-MM-DDTHH:MM) interprete
    en Europe/Paris.
    """
    if not raw:
        return None
    try:
        s = str(raw).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("Europe/Paris"))
    return dt.astimezone(timezone.utc)


@app.route('/api/pcorg/summary/generate', methods=['POST'])
@role_required("manager")
def pcorg_summary_generate():
    """Genere un resume de periode (KPIs + appel Claude) et le persiste."""
    data = request.get_json(silent=True) or {}
    event = (data.get("event") or "").strip()
    year = data.get("year")
    ts_start = _parse_period_dt(data.get("period_start"))
    ts_end = _parse_period_dt(data.get("period_end"))
    if not event or year in (None, ""):
        return jsonify({"ok": False, "error": "event et year requis"}), 400
    try:
        year = int(year)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "year invalide"}), 400
    if not ts_start or not ts_end:
        return jsonify({"ok": False, "error": "period_start et period_end requis (ISO 8601)"}), 400
    if ts_end <= ts_start:
        return jsonify({"ok": False, "error": "period_end doit etre apres period_start"}), 400

    user = request.user_payload or {}
    created_by_email = user.get("email", "") or ""
    created_by_name = (str(user.get("firstname", "") or "") + " " + str(user.get("lastname", "") or "")).strip()

    try:
        doc = pcorg_summary.generate_period_summary(
            db, event, year, ts_start, ts_end, created_by_email, created_by_name,
        )
    except pcorg_summary.ClaudeError as e:
        msg = str(e)
        if msg == "ANTHROPIC_API_KEY non configuree":
            return jsonify({"ok": False, "error": msg}), 503
        if msg == "claude_unreachable":
            return jsonify({"ok": False, "error": msg}), 502
        return jsonify({"ok": False, "error": msg}), 502
    except Exception as e:
        logger.exception("pcorg_summary_generate: erreur inattendue")
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "summary": pcorg_summary._serialize_summary(doc, light=False)})


@app.route('/api/pcorg/summary/list', methods=['GET'])
@role_required("manager")
def pcorg_summary_list():
    event = request.args.get("event") or None
    year = request.args.get("year") or None
    items = pcorg_summary.list_summaries(db, event=event, year=year, limit=50)
    return jsonify({"ok": True, "items": items})


@app.route('/api/pcorg/summary/<summary_id>', methods=['GET'])
@role_required("manager")
def pcorg_summary_get(summary_id):
    doc = pcorg_summary.get_summary(db, summary_id)
    if not doc:
        return jsonify({"ok": False, "error": "introuvable"}), 404
    return jsonify({"ok": True, "summary": doc})


@app.route('/api/pcorg/summary/<summary_id>', methods=['DELETE'])
@role_required("admin")
def pcorg_summary_delete(summary_id):
    deleted = pcorg_summary.delete_summary(db, summary_id)
    if not deleted:
        return jsonify({"ok": False, "error": "introuvable"}), 404
    return jsonify({"ok": True})


################################################################################
# CONTROLE D'ACCES (HANDSHAKE)
################################################################################

COL_HSH_STRUCTURE = db['hsh_structure']
COL_HSH_ERREURS = db['hsh_erreurs']
COL_HSH_TX_AGG = db['hsh_transactions_agg']
COL_HSH_AGG_TITRES = db['hsh_agg_titres']
HSH_GLOBAL_ID = "___GLOBAL___"


@app.route('/live-controle')
@role_required("admin")
def live_controle_page():
    payload = getattr(request, 'user_payload', {})
    user_roles = payload.get("roles", [])
    return render_template('live_controle.html',
                           user_roles=user_roles,
                           user_firstname=payload.get("firstname", ""),
                           user_lastname=payload.get("lastname", ""),
                           user_email=payload.get("email", ""))


def _hsh_read_global():
    doc = db.data_access.find_one({"_id": HSH_GLOBAL_ID})
    if doc is None:
        defaults = {
            "_id": HSH_GLOBAL_ID,
            "live_controle_actif": False,
            "activation_timestamp": None,
            "evenement": "",
            "evenement_clean": "",
            "locations_selectionnees": [],
            "dernier_inventaire": None,
            "dernier_transaction_id": None,
            "dernier_cycle": None,
        }
        db.data_access.insert_one(defaults)
        doc = defaults
    return doc


@app.route('/api/live-controle/config', methods=['GET'])
@role_required("user")
def hsh_get_config():
    doc = _hsh_read_global()
    doc.pop("_id", None)
    # Serialiser les datetimes
    for k in ("activation_timestamp", "dernier_inventaire", "dernier_cycle"):
        v = doc.get(k)
        if hasattr(v, "isoformat"):
            doc[k] = v.isoformat()
    return jsonify(doc)


@app.route('/api/live-controle/config', methods=['PUT'])
@role_required("admin")
def hsh_update_config():
    data = request.get_json(force=True)
    allowed = {
        "live_controle_actif", "evenement", "evenement_clean",
        "locations_selectionnees", "corrections_compteurs",
        "corrections_enfants", "corrections_vehicules", "corrections_accredites",
        "compteur_principal_id",
    }
    update = {k: v for k, v in data.items() if k in allowed}
    if not update:
        return jsonify({"error": "rien a mettre a jour"}), 400
    # Si on active, enregistrer le timestamp
    if update.get("live_controle_actif") is True:
        update["activation_timestamp"] = datetime.now(timezone.utc)
    db.data_access.update_one(
        {"_id": HSH_GLOBAL_ID},
        {"$set": update},
        upsert=True,
    )
    return jsonify({"ok": True})


@app.route('/api/live-controle/force-inventory', methods=['POST'])
@role_required("admin")
def hsh_force_inventory():
    db.data_access.update_one(
        {"_id": HSH_GLOBAL_ID},
        {"$set": {"dernier_inventaire": None}},
        upsert=True,
    )
    return jsonify({"ok": True})


@app.route('/api/live-controle/force-transactions', methods=['POST'])
@role_required("admin")
def hsh_force_transactions():
    data = request.get_json(force=True)
    jours = data.get("jours", 1)
    try:
        jours = int(jours)
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Valeur invalide"}), 400
    if jours < 1 or jours > 3:
        return jsonify({"ok": False, "error": "1 a 3 jours maximum"}), 400
    db.data_access.update_one(
        {"_id": HSH_GLOBAL_ID},
        {"$set": {
            "force_collecte_jours": jours,
            "dernier_transaction_id": None,
        }},
        upsert=True,
    )
    return jsonify({"ok": True, "jours": jours})


@app.route('/api/live-controle/archive', methods=['POST'])
@role_required("admin")
def hsh_archive_and_purge():
    """Archive les donnees HSH dans des collections dediees puis purge les collections de travail."""
    data = request.get_json(force=True)
    evenement = data.get("evenement", "").strip()
    if not evenement:
        return jsonify({"ok": False, "error": "Evenement requis"}), 400

    import re
    suffix = re.sub(r'[^a-zA-Z0-9_-]', '_', evenement)
    year = datetime.now().year
    archive_tag = f"{suffix}_{year}"

    counts = {}
    # 1. Archiver hsh_transactions_agg
    src_col = COL_HSH_TX_AGG
    docs = list(src_col.find({"evenement": evenement}))
    if docs:
        dest = db[f"hsh_archive_tx_{archive_tag}"]
        dest.insert_many(docs)
        counts["transactions_agg"] = len(docs)
        src_col.delete_many({"evenement": evenement})

    # 1b. Archiver hsh_agg_titres
    src_titres = db["hsh_agg_titres"]
    docs = list(src_titres.find({"evenement": evenement}))
    if docs:
        dest = db[f"hsh_archive_titres_{archive_tag}"]
        dest.insert_many(docs)
        counts["titres_agg"] = len(docs)
        src_titres.delete_many({"evenement": evenement})

    # 2. Archiver hsh_erreurs
    docs = list(COL_HSH_ERREURS.find({"evenement": evenement}))
    if docs:
        dest = db[f"hsh_archive_erreurs_{archive_tag}"]
        dest.insert_many(docs)
        counts["erreurs"] = len(docs)
        COL_HSH_ERREURS.delete_many({"evenement": evenement})

    # 3. Archiver hsh_structure
    docs = list(COL_HSH_STRUCTURE.find({"evenement": evenement}))
    if docs:
        dest = db[f"hsh_archive_structure_{archive_tag}"]
        dest.insert_many(docs)
        counts["structure"] = len(docs)
        COL_HSH_STRUCTURE.delete_many({"evenement": evenement})

    # 4. Archiver et purger data_access (compteurs) de cet evenement
    docs = list(db.data_access.find({
        "requested_event": evenement,
        "_id": {"$ne": HSH_GLOBAL_ID},
    }))
    if docs:
        dest = db[f"hsh_archive_compteurs_{archive_tag}"]
        dest.insert_many(docs)
        counts["compteurs"] = len(docs)
        db.data_access.delete_many({
            "requested_event": evenement,
            "_id": {"$ne": HSH_GLOBAL_ID},
        })

    return jsonify({"ok": True, "archive": archive_tag, "counts": counts})


@app.route('/api/live-controle/structure', methods=['GET'])
@role_required("user")
def hsh_get_structure():
    filtre = {}
    evenement = request.args.get("evenement")
    if evenement:
        filtre["evenement"] = evenement
    docs = list(COL_HSH_STRUCTURE.find(filtre))

    # Agreger les compteurs du jour par checkpoint depuis hsh_transactions_agg
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    agg_filtre = {"tranche": {"$gte": today_start}}
    if evenement:
        agg_filtre["evenement"] = evenement
    pipeline = [
        {"$match": agg_filtre},
        {"$group": {
            "_id": "$checkpoint_id",
            "entrees": {"$sum": "$entrees"},
            "sorties": {"$sum": "$sorties"},
            "entrees_vehicules": {"$sum": {"$ifNull": ["$entrees_vehicules", 0]}},
            "sorties_vehicules": {"$sum": {"$ifNull": ["$sorties_vehicules", 0]}},
            "entrees_enfants": {"$sum": {"$ifNull": ["$entrees_enfants", 0]}},
            "sorties_enfants": {"$sum": {"$ifNull": ["$sorties_enfants", 0]}},
        }},
    ]
    counts_by_cp = {}
    for agg in COL_HSH_TX_AGG.aggregate(pipeline):
        counts_by_cp[agg["_id"]] = agg

    for d in docs:
        d["_id"] = str(d["_id"])
        for k, v in d.items():
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    if hasattr(vv, "isoformat"):
                        v[kk] = vv.isoformat()
        # Attacher les compteurs du jour aux checkpoints
        if d.get("location_type") == "Checkpoint":
            c = counts_by_cp.get(d.get("location_id"), {})
            d["counts_jour"] = {
                "entrees": c.get("entrees", 0),
                "sorties": c.get("sorties", 0),
                "entrees_veh": c.get("entrees_vehicules", 0),
                "sorties_veh": c.get("sorties_vehicules", 0),
                "entrees_enf": c.get("entrees_enfants", 0),
                "sorties_enf": c.get("sorties_enfants", 0),
            }
    return jsonify(docs)


@app.route('/api/live-controle/structure/assign', methods=['POST'])
@role_required("admin")
def hsh_assign_parent():
    """Reassigner un noeud a un parent dans hsh_structure.
    Body: {node_id, node_type, parent_id, parent_type, parent_name}
    parent_id=null pour detacher."""
    data = request.get_json(force=True)
    node_id = data.get("node_id")
    node_type = data.get("node_type")
    parent_id = data.get("parent_id")
    parent_type = data.get("parent_type")
    parent_name = data.get("parent_name", "")

    if not node_id or not node_type:
        return jsonify({"error": "node_id et node_type requis"}), 400

    doc_id = f"{node_type}_{node_id}"

    # Determiner le champ parent a mettre a jour
    parent_field_map = {
        "Gate": "parent_area",
        "Checkpoint": "parent_gate",
        "Area": "parent_venue",
    }
    parent_field = parent_field_map.get(node_type)
    if not parent_field:
        return jsonify({"error": "Type non reassignable: " + node_type}), 400

    if parent_id:
        COL_HSH_STRUCTURE.update_one(
            {"_id": doc_id},
            {"$set": {parent_field: {"id": str(parent_id), "name": parent_name}}},
        )
        # Ajouter aussi l'enfant dans le parent
        parent_doc_id = f"{parent_type}_{parent_id}"
        COL_HSH_STRUCTURE.update_one(
            {"_id": parent_doc_id},
            {"$addToSet": {"enfants": {"id": str(node_id), "type": node_type, "name": data.get("node_name", "")}}},
        )
    else:
        COL_HSH_STRUCTURE.update_one(
            {"_id": doc_id},
            {"$unset": {parent_field: ""}},
        )

    return jsonify({"ok": True})


@app.route('/api/live-controle/counters-context', methods=['GET'])
@role_required("user")
def hsh_get_counters_context():
    """Contexte de projection pour le widget compteurs :
    pic projete du jour + presents N-1 projetes au meme offset horaire."""
    event = request.args.get("event")
    year = request.args.get("year")
    if not event or not year:
        return jsonify({})

    # --- Charger parametrages N ---
    doc = db['parametrages'].find_one({'event': event, 'year': year}, {'_id': 0})
    if not doc or 'data' not in doc:
        return jsonify({})

    gh = doc['data'].get('globalHoraires', {})
    public_days = gh.get('dates', [])
    ticketing_config = gh.get('ticketing', [])
    race_raw = doc['data'].get('race') or gh.get('race')
    tickets = doc.get('tickets', {})
    products_data = tickets.get('products', {})
    last_update = tickets.get('lastUpdate')

    race_date = _parse_race_date(race_raw)
    race_dt = _parse_race_datetime(race_raw)
    if not race_date:
        return jsonify({})

    current_year_int = int(year) if str(year).isdigit() else None
    if not current_year_int:
        return jsonify({})

    # --- Charger parametrages N-1 ---
    prev_year_str = None
    prev_param = None
    prev_race_date = None
    prev_products_data = {}
    prev_ticketing_config = []
    prev_candidates = list(db['parametrages'].find(
        {'event': event, 'tickets': {'$exists': True}},
        {'year': 1, 'data.globalHoraires': 1, 'data.race': 1, 'tickets': 1, '_id': 0}
    ))
    for cand in sorted(prev_candidates, key=lambda c: str(c.get('year', '')), reverse=True):
        try:
            if int(cand.get('year', '')) < current_year_int:
                prev_param = cand
                prev_year_str = cand.get('year')
                break
        except (ValueError, TypeError):
            continue

    if prev_param:
        prev_gh = prev_param.get('data', {}).get('globalHoraires', {})
        prev_ticketing_config = prev_gh.get('ticketing', [])
        prev_products_data = prev_param.get('tickets', {}).get('products', {})
        prev_race_raw = prev_param.get('data', {}).get('race') or prev_gh.get('race')
        prev_race_date = _parse_race_date(prev_race_raw)

    # --- Charger historique_controle N-1 ---
    prev_hist_race_date = None
    prev_hist_race_dt = None
    prev_data_by_day = {}
    prev_hist_candidates = list(db['historique_controle'].find(
        {'type': 'frequentation', 'event': event},
        sort=[('year', -1)]
    ))
    for cand in prev_hist_candidates:
        cand_year = cand.get('year')
        if isinstance(cand_year, (int, float)) and int(cand_year) < current_year_int:
            prev_hist_race_raw = cand.get('race')
            if not prev_hist_race_raw:
                portes_doc = db['historique_controle'].find_one(
                    {'type': 'portes', 'event': event, 'year': cand_year},
                    {'_id': 0, 'race': 1}
                )
                if portes_doc:
                    prev_hist_race_raw = portes_doc.get('race')
            prev_hist_race_date = _parse_race_date(prev_hist_race_raw)
            prev_hist_race_dt = _parse_race_datetime(prev_hist_race_raw)
            if prev_hist_race_date and cand.get('data'):
                from collections import defaultdict
                day_records = defaultdict(list)
                for rec in cand['data']:
                    rec_date = rec.get('date')
                    if isinstance(rec_date, str):
                        day_key = rec_date[:10]
                    elif hasattr(rec_date, 'strftime'):
                        day_key = rec_date.strftime('%Y-%m-%d')
                    else:
                        continue
                    day_records[day_key].append(rec)
                prev_data_by_day = dict(day_records)
            break

    if not prev_hist_race_date:
        return jsonify({})

    # --- Calculer projection_ratio ---
    projection_ratio = None
    if last_update:
        last_dt = datetime.strptime(last_update, '%Y-%m-%d').date()
        days_before = (race_date - last_dt).days
        fill_pcts = []
        if prev_param:
            pts, final, _ = _fill_curve(prev_param)
            pct = _interpolate_pct(pts, final, days_before)
            if pct:
                fill_pcts.append(pct)
        for cand in sorted(prev_candidates, key=lambda c: str(c.get('year', '')), reverse=True):
            try:
                cy = int(cand.get('year', ''))
            except (ValueError, TypeError):
                continue
            if cy < current_year_int and str(cand.get('year')) != str(prev_year_str):
                pts2, final2, _ = _fill_curve(cand)
                pct2 = _interpolate_pct(pts2, final2, days_before)
                if pct2:
                    fill_pcts.append(pct2)
                break
        if fill_pcts:
            avg_pct = sum(fill_pcts) / len(fill_pcts)
            if avg_pct > 0:
                projection_ratio = avg_pct / 100

    # --- Identifier le jour courant par offset depuis la course ---
    today = datetime.now(timezone.utc).date()
    offset_days = (today - race_date).days
    target_prev_date = prev_hist_race_date + timedelta(days=offset_days)
    target_key = target_prev_date.strftime('%Y-%m-%d')

    # Pic N-1 du jour equivalent
    records = prev_data_by_day.get(target_key, [])
    if not records:
        # Pas de donnees historiques pour cette date (hors periode evenement)
        hist_days = sorted(prev_data_by_day.keys())
        return jsonify({
            "no_data": True,
            "message": "Pas de donnees N-1 pour cette date (J" + ("%+d" % offset_days) + " vs course)",
            "hint": "Historique N-1 disponible du " + hist_days[0] + " au " + hist_days[-1] if hist_days else "",
            "prev_year": prev_year_str,
        })
    pic_prev = max((r.get('present', 0) for r in records), default=0)
    if not pic_prev:
        return jsonify({"no_data": True, "message": "Pic N-1 = 0 pour cette date"})

    # Ventes N du jour courant
    today_str = today.strftime('%Y-%m-%d')
    day_ventes = 0
    for tc in ticketing_config:
        days_scope = tc.get('days', [])
        applies = (days_scope == 'all') or (today_str in days_scope)
        if not applies:
            continue
        for pname in tc.get('products', []):
            pdata = products_data.get(pname)
            if pdata:
                day_ventes += pdata.get('ventes', 0)

    # Ventes N-1 du jour equivalent
    ventes_prev = 0
    if prev_race_date and prev_ticketing_config:
        target_prev_str = (prev_race_date + timedelta(days=offset_days)).strftime('%Y-%m-%d')
        for tc in prev_ticketing_config:
            days_scope = tc.get('days', [])
            applies = (days_scope == 'all') or (target_prev_str in days_scope)
            if not applies:
                continue
            for pname in tc.get('products', []):
                pdata = prev_products_data.get(pname)
                if pdata:
                    ventes_prev += pdata.get('ventes', 0)

    # Ratio de projection (ventes N / ventes N-1)
    has_sales = ventes_prev and ventes_prev > 0 and day_ventes > 0
    if has_sales:
        day_projection = round(day_ventes / projection_ratio) if projection_ratio and projection_ratio > 0 else day_ventes
        sales_ratio = day_projection / ventes_prev
        pic_projection = round(pic_prev * sales_ratio)
        mode = "projected"
    else:
        # Fallback : pas de donnees de ventes, utiliser le pic N-1 brut
        sales_ratio = None
        pic_projection = pic_prev
        mode = "raw_n1"

    # --- N-1 meme heure ---
    present_n1 = None
    if race_dt and prev_hist_race_dt:
        now_utc = datetime.now(timezone.utc)
        if race_dt.tzinfo is None:
            race_dt = race_dt.replace(tzinfo=timezone.utc)
        if prev_hist_race_dt.tzinfo is None:
            prev_hist_race_dt = prev_hist_race_dt.replace(tzinfo=timezone.utc)
        offset_seconds = (now_utc - race_dt).total_seconds()
        target_dt = prev_hist_race_dt + timedelta(seconds=offset_seconds)
        target_day_key = target_dt.strftime('%Y-%m-%d')
        day_records = prev_data_by_day.get(target_day_key, [])
        if day_records:
            best = None
            best_diff = None
            for rec in day_records:
                rec_date = rec.get('date')
                if isinstance(rec_date, str):
                    try:
                        rec_dt = datetime.fromisoformat(rec_date.replace('Z', '+00:00'))
                    except Exception:
                        continue
                elif hasattr(rec_date, 'timestamp'):
                    rec_dt = rec_date
                else:
                    continue
                if rec_dt.tzinfo is None:
                    rec_dt = rec_dt.replace(tzinfo=timezone.utc)
                diff = abs((rec_dt - target_dt).total_seconds())
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best = rec
            if best:
                raw_present = best.get('present', 0)
                if sales_ratio:
                    present_n1 = round(raw_present * sales_ratio)
                else:
                    present_n1 = raw_present

    return jsonify({
        "pic_projection": pic_projection,
        "present_n1": present_n1,
        "prev_year": prev_year_str,
        "mode": mode,
    })


@app.route('/api/live-controle/counters', methods=['GET'])
@role_required("user")
@block_required("widget-counters")
def hsh_get_counters():
    doc = _hsh_read_global()
    locations = doc.get("locations_selectionnees", [])
    corrections = doc.get("corrections_compteurs", {})
    corrections_enf = doc.get("corrections_enfants", {})
    corrections_veh = doc.get("corrections_vehicules", {})
    corrections_acc = doc.get("corrections_accredites", {})
    principal_id = doc.get("compteur_principal_id")
    principal_id_str = str(principal_id) if principal_id else None

    # Agreger vehicules/enfants du jour depuis hsh_transactions_agg
    # Les compteurs live sont par location (Area, Venue...), les transactions sont par checkpoint.
    # On doit remonter : checkpoint -> parent_area/parent_venue via hsh_structure.
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    pipeline = [
        {"$match": {"tranche": {"$gte": today_start}}},
        {"$group": {
            "_id": "$checkpoint_id",
            "entrees_veh": {"$sum": {"$ifNull": ["$entrees_vehicules", 0]}},
            "sorties_veh": {"$sum": {"$ifNull": ["$sorties_vehicules", 0]}},
            "entrees_enf": {"$sum": {"$ifNull": ["$entrees_enfants", 0]}},
            "sorties_enf": {"$sum": {"$ifNull": ["$sorties_enfants", 0]}},
            "entrees_acc": {"$sum": {"$ifNull": ["$entrees_accredites", 0]}},
            "sorties_acc": {"$sum": {"$ifNull": ["$sorties_accredites", 0]}},
        }},
    ]
    veh_enf_by_cp = {}
    for agg in COL_HSH_TX_AGG.aggregate(pipeline):
        veh_enf_by_cp[agg["_id"]] = agg

    # Construire le mapping location_id -> totaux vehicules/enfants/accredites en remontant la hierarchie
    # Un checkpoint appartient a une area (parent_area.id) et une venue (parent_venue.id)
    veh_enf_by_loc = {}
    for cp_doc in COL_HSH_STRUCTURE.find({"location_type": "Checkpoint"}):
        cp_id = cp_doc.get("location_id")
        cp_counts = veh_enf_by_cp.get(cp_id)
        if not cp_counts:
            continue
        # Remonter vers les parents
        for parent_key in ("parent_area", "parent_venue"):
            parent = cp_doc.get(parent_key, {})
            pid = parent.get("id") if parent else None
            if pid:
                if pid not in veh_enf_by_loc:
                    veh_enf_by_loc[pid] = {"entrees_veh": 0, "sorties_veh": 0, "entrees_enf": 0, "sorties_enf": 0, "entrees_acc": 0, "sorties_acc": 0}
                veh_enf_by_loc[pid]["entrees_veh"] += cp_counts.get("entrees_veh", 0)
                veh_enf_by_loc[pid]["sorties_veh"] += cp_counts.get("sorties_veh", 0)
                veh_enf_by_loc[pid]["entrees_enf"] += cp_counts.get("entrees_enf", 0)
                veh_enf_by_loc[pid]["sorties_enf"] += cp_counts.get("sorties_enf", 0)
                veh_enf_by_loc[pid]["entrees_acc"] += cp_counts.get("entrees_acc", 0)
                veh_enf_by_loc[pid]["sorties_acc"] += cp_counts.get("sorties_acc", 0)

    result = []
    for loc in locations:
        loc_id = loc.get("id")
        loc_type = loc.get("type")
        if not loc_id:
            continue
        counter = db.data_access.find_one(
            {"requested_location_id": str(loc_id), "requested_location_type": loc_type},
            sort=[("timestamp", -1)],
        )
        if counter:
            ve = veh_enf_by_loc.get(str(loc_id), {})
            result.append({
                "location_id": loc_id,
                "location_type": loc_type,
                "location_name": loc.get("name", counter.get("location_name", "")),
                "counter_name": counter.get("counter_name", ""),
                "entries": counter.get("entries"),
                "exits": counter.get("exits"),
                "current": counter.get("current"),
                "upper_limit": counter.get("upper_limit"),
                "lower_limit": counter.get("lower_limit"),
                "locked": counter.get("locked"),
                "locked_status": counter.get("locked_status"),
                "first_entries": counter.get("first_entries"),
                "first_entries_day": counter.get("first_entries_day"),
                "timestamp": counter["timestamp"].isoformat() if hasattr(counter.get("timestamp"), "isoformat") else counter.get("timestamp"),
                "entrees_veh": ve.get("entrees_veh", 0),
                "sorties_veh": ve.get("sorties_veh", 0),
                "entrees_enf": ve.get("entrees_enf", 0),
                "sorties_enf": ve.get("sorties_enf", 0),
                "entrees_acc": ve.get("entrees_acc", 0),
                "sorties_acc": ve.get("sorties_acc", 0),
                "correction": corrections.get(str(loc_id), 0),
                "correction_enf": corrections_enf.get(str(loc_id), 0),
                "correction_veh": corrections_veh.get(str(loc_id), 0),
                "correction_acc": corrections_acc.get(str(loc_id), 0),
                "is_principal": (principal_id_str is not None and str(loc_id) == principal_id_str),
            })
    return jsonify(result)


@app.route('/api/live-controle/dashboard', methods=['GET'])
@role_required("user")
@block_required("widget-counters")
def hsh_get_dashboard():
    """Dashboard contexte pour le plein-ecran : series temporelles par zone,
    pics jour, comparaison N-1. Param optionnel ?date=YYYY-MM-DD pour choisir
    le jour de reference du graphique principal (defaut: aujourd'hui)."""
    event = request.args.get('event')
    year = request.args.get('year')
    target_date_param = request.args.get('date')

    global_doc = _hsh_read_global()
    locations = global_doc.get('locations_selectionnees', []) or []
    corrections = global_doc.get('corrections_compteurs', {}) or {}
    principal_id = global_doc.get('compteur_principal_id')
    principal_id_str = str(principal_id) if principal_id else None

    # Reference N-1 via parametrages + fallback portes
    race_n = None
    prev_race_ref = None
    prev_year_str = None
    event_days = []
    doc_n = None
    if event and year:
        doc_n = db['parametrages'].find_one({'event': event, 'year': year}) or {}
        gh = (doc_n.get('data') or {}).get('globalHoraires', {})
        event_days = [d.get('date') for d in gh.get('dates', []) if d.get('date')]
        race_n = _parse_race_date((doc_n.get('data') or {}).get('race') or gh.get('race'))
        try:
            cy = int(year)
            for cand in db['parametrages'].find(
                {'event': event, 'tickets': {'$exists': True}},
                {'year': 1, 'data.race': 1, '_id': 0}
            ).sort('year', -1):
                try:
                    cyn = int(cand.get('year', ''))
                except (ValueError, TypeError):
                    continue
                if cyn < cy:
                    prev_year_str = str(cyn)
                    portes = db['historique_controle'].find_one(
                        {'type': 'portes', 'event': event, 'year': cyn},
                        {'_id': 0, 'race': 1}
                    )
                    prev_race_ref = _parse_race_date((portes or {}).get('race')) or \
                                    _parse_race_date((cand.get('data') or {}).get('race'))
                    break
        except (ValueError, TypeError):
            pass

    # Historique N-1 par jour
    prev_hist_by_day = {}
    if prev_year_str:
        try:
            hist = db['historique_controle'].find_one(
                {'type': 'frequentation', 'event': event, 'year': int(prev_year_str)}
            )
            if hist:
                for rec in hist.get('data', []):
                    rd = rec.get('date')
                    if isinstance(rd, str):
                        day_key = rd[:10]
                        hour = rd[11:16] if len(rd) >= 16 else None
                    elif hasattr(rd, 'strftime'):
                        day_key = rd.strftime('%Y-%m-%d')
                        hour = rd.strftime('%H:%M')
                    else:
                        continue
                    prev_hist_by_day.setdefault(day_key, []).append({
                        'hour': hour,
                        'present': rec.get('present', 0),
                    })
        except (ValueError, TypeError):
            pass

    # data_access.timestamp est stocke en datetime naif (en UTC).
    today_local = datetime.now().date()
    # Jour de reference du graphique principal (par defaut aujourd'hui)
    try:
        target_date = datetime.strptime(target_date_param, '%Y-%m-%d').date() if target_date_param else today_local
    except Exception:
        target_date = today_local
    target_day_start = datetime(target_date.year, target_date.month, target_date.day)
    target_day_end = target_day_start + timedelta(days=1)

    # Plage "totale collecte" = depuis le premier jour public (ou 4j avant today) jusqu'a maintenant
    if event_days:
        try:
            first_event_day = min(datetime.strptime(d, '%Y-%m-%d').date() for d in event_days)
            full_start = datetime(first_event_day.year, first_event_day.month, first_event_day.day)
        except Exception:
            full_start = target_day_start - timedelta(days=3)
    else:
        full_start = target_day_start - timedelta(days=3)
    full_end = datetime.now() + timedelta(hours=1)

    # historique_controle ne contient qu'une serie globale (zone principale d'enceinte).
    # On ne l'expose donc que pour la zone principale ou, a defaut, la premiere zone.
    pic_n1_principal = None
    max_n1_principal = None
    if prev_race_ref and race_n:
        offset = (target_date - race_n).days
        n1_day = (prev_race_ref + timedelta(days=offset)).strftime('%Y-%m-%d')
        recs = prev_hist_by_day.get(n1_day)
        if recs:
            pic_n1_principal = max(r['present'] for r in recs)
    if prev_hist_by_day:
        max_n1_principal = max(
            (max(r['present'] for r in recs) for recs in prev_hist_by_day.values()),
            default=None
        )

    # Series + stats par zone
    zones = []
    has_principal = any(str(l.get('id')) == principal_id_str for l in locations) if principal_id_str else False
    for idx, loc in enumerate(locations):
        lid = str(loc.get('id'))
        ltype = loc.get('type')
        name = loc.get('name', f'Loc {lid}')
        correction = int(corrections.get(lid, 0) or 0)

        is_principal = (principal_id_str == lid) if principal_id_str else (idx == 0 and not has_principal)

        # Pour la zone principale : serie detaillee (15 min) du jour CIBLE (pour le gros chart).
        # Pour les autres zones : serie longue (bucket 30 min) sur toute la periode event_days.
        if is_principal:
            query_start, query_end, bucket_minutes = target_day_start, target_day_end, 15
        else:
            query_start, query_end, bucket_minutes = full_start, full_end, 30

        snaps = list(db['data_access'].find(
            {'requested_location_id': lid, 'requested_location_type': ltype,
             'timestamp': {'$gte': query_start, '$lt': query_end}},
            {'_id': 0, 'timestamp': 1, 'current': 1}
        ).sort('timestamp', 1))

        series = []
        last_bucket_key = None
        for s in snaps:
            ts = s['timestamp']
            bucket = ts.replace(minute=(ts.minute // bucket_minutes) * bucket_minutes,
                                second=0, microsecond=0)
            key = bucket.isoformat() + 'Z'
            present = max(int(s.get('current', 0) or 0) - correction, 0)
            if key != last_bucket_key:
                series.append({'ts': key, 'present': present})
                last_bucket_key = key
            else:
                series[-1]['present'] = present

        pic_today = max((p['present'] for p in series), default=0)
        # "current" = valeur la plus recente tout court (pas specifique au jour cible)
        latest = db['data_access'].find_one(
            {'requested_location_id': lid, 'requested_location_type': ltype},
            sort=[('timestamp', -1)],
            projection={'_id': 0, 'current': 1}
        )
        current = max(int((latest or {}).get('current', 0) or 0) - correction, 0) if latest else 0

        zones.append({
            'location_id': lid,
            'location_type': ltype,
            'name': name,
            'is_principal': is_principal,
            'correction': correction,
            'current': current,
            'pic_today': pic_today,
            'pic_n1_same_day': pic_n1_principal if is_principal else None,
            'max_n1_season': max_n1_principal if is_principal else None,
            'series': series,
        })

    # Resume par jour public (base sur la zone principale effective)
    principal_zone = next((z for z in zones if z.get('is_principal')), None)
    principal_effective_id = principal_zone['location_id'] if principal_zone else None
    days_summary = []
    for dstr in event_days:
        try:
            dd = datetime.strptime(dstr, '%Y-%m-%d').date()
        except Exception:
            continue
        pic_n = None
        if principal_effective_id:
            day_start = datetime(dd.year, dd.month, dd.day)
            day_end = day_start + timedelta(days=1)
            if dd <= today_local:
                p_corr = int(corrections.get(principal_effective_id, 0) or 0)
                top = db['data_access'].find_one(
                    {'requested_location_id': principal_effective_id,
                     'timestamp': {'$gte': day_start, '$lt': day_end}},
                    sort=[('current', -1)]
                )
                if top:
                    pic_n = max(int(top.get('current', 0) or 0) - p_corr, 0)
        pic_n1 = None
        if prev_race_ref and race_n:
            offset = (dd - race_n).days
            n1_key = (prev_race_ref + timedelta(days=offset)).strftime('%Y-%m-%d')
            recs = prev_hist_by_day.get(n1_key)
            if recs:
                pic_n1 = max(r['present'] for r in recs)
        JOURS = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim']
        days_summary.append({
            'date': dstr,
            'label': JOURS[dd.weekday()] + ' ' + dd.strftime('%d/%m'),
            'pic_n': pic_n,
            'pic_n1': pic_n1,
            'is_today': dd == today_local,
            'is_past': dd < today_local,
        })

    # Serie N-1 du jour equivalent au jour CIBLE selectionne, pour comparaison
    n1_series_today = []
    if prev_race_ref and race_n:
        offset_target = (target_date - race_n).days
        n1_day_equiv = (prev_race_ref + timedelta(days=offset_target)).strftime('%Y-%m-%d')
        recs = prev_hist_by_day.get(n1_day_equiv)
        if recs:
            for r in sorted(recs, key=lambda x: x.get('hour') or ''):
                n1_series_today.append({'hour': r.get('hour'), 'present': r.get('present')})

    return jsonify({
        'zones': zones,
        'days_summary': days_summary,
        'prev_year': prev_year_str,
        'n1_series_today': n1_series_today,
        'target_date': target_date.strftime('%Y-%m-%d'),
    })


@app.route('/api/live-controle/titres-live', methods=['GET'])
@role_required("admin")
def hsh_get_titres_live():
    """Repartition des entrees/sorties/presents par titre de billet (jour en cours)."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    pipeline = [
        {"$match": {"tranche": {"$gte": today_start}}},
        {"$group": {
            "_id": "$titre",
            "entrees": {"$sum": "$entrees"},
            "sorties": {"$sum": "$sorties"},
        }},
        {"$sort": {"_id": 1}},
    ]
    result = []
    for agg in COL_HSH_AGG_TITRES.aggregate(pipeline):
        e = agg.get("entrees", 0)
        s = agg.get("sorties", 0)
        result.append({
            "titre": agg["_id"],
            "entrees": e,
            "sorties": s,
            "presents": e - s,
        })
    return jsonify(result)


@app.route('/api/live-controle/debit-gates', methods=['GET'])
@role_required("admin")
def hsh_get_debit_gates():
    """Debit par gate sur la derniere heure glissante."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    pipeline = [
        {"$match": {"tranche": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$gate_name",
            "entrees": {"$sum": "$entrees"},
            "sorties": {"$sum": "$sorties"},
        }},
        {"$sort": {"entrees": -1}},
    ]
    result = []
    for agg in COL_HSH_TX_AGG.aggregate(pipeline):
        gate = agg["_id"] or "Inconnu"
        e = agg.get("entrees", 0)
        s = agg.get("sorties", 0)
        result.append({
            "gate": gate,
            "entrees_h": e,
            "sorties_h": s,
            "total_h": e + s,
        })
    return jsonify(result)


@app.route('/api/live-controle/active-checkpoints', methods=['GET'])
@role_required("user")
def hsh_get_active_checkpoints():
    """Checkpoints ayant scanne au moins 1 transaction dans les 10 dernieres minutes."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    docs = list(COL_HSH_STRUCTURE.find({
        "location_type": "Checkpoint",
        "derniere_transaction": {"$gte": cutoff},
    }))

    # Agreger les compteurs du jour par checkpoint
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    cp_ids = [d.get("location_id") for d in docs if d.get("location_id")]
    counts_by_cp = {}
    if cp_ids:
        pipeline = [
            {"$match": {"checkpoint_id": {"$in": cp_ids}, "tranche": {"$gte": today_start}}},
            {"$group": {
                "_id": "$checkpoint_id",
                "entrees": {"$sum": "$entrees"},
                "sorties": {"$sum": "$sorties"},
                "entrees_vehicules": {"$sum": {"$ifNull": ["$entrees_vehicules", 0]}},
                "sorties_vehicules": {"$sum": {"$ifNull": ["$sorties_vehicules", 0]}},
                "entrees_enfants": {"$sum": {"$ifNull": ["$entrees_enfants", 0]}},
                "sorties_enfants": {"$sum": {"$ifNull": ["$sorties_enfants", 0]}},
                "entrees_accredites": {"$sum": {"$ifNull": ["$entrees_accredites", 0]}},
                "sorties_accredites": {"$sum": {"$ifNull": ["$sorties_accredites", 0]}},
            }},
        ]
        for agg in COL_HSH_TX_AGG.aggregate(pipeline):
            counts_by_cp[agg["_id"]] = agg

    result = []
    for d in docs:
        dt = d.get("derniere_transaction")
        loc_id = d.get("location_id")
        c = counts_by_cp.get(loc_id, {})
        entrees = c.get("entrees", 0)
        entrees_veh = c.get("entrees_vehicules", 0)
        entrees_enf = c.get("entrees_enfants", 0)
        entrees_acc = c.get("entrees_accredites", 0)
        entrees_pers = entrees - entrees_veh - entrees_enf - entrees_acc
        result.append({
            "location_id": loc_id,
            "location_name": d.get("location_name", ""),
            "parent_gate": d.get("parent_gate", {}).get("name", ""),
            "derniere_transaction": dt.isoformat() if hasattr(dt, "isoformat") else dt,
            "entrees": entrees,
            "entrees_pers": entrees_pers,
            "entrees_veh": entrees_veh,
            "entrees_enf": entrees_enf,
            "entrees_acc": entrees_acc,
        })
    result.sort(key=lambda x: x.get("derniere_transaction", ""), reverse=True)
    return jsonify(result)


@app.route('/api/live-controle/errors', methods=['GET'])
@role_required("user")
def hsh_get_errors():
    filtre = {}
    evenement = request.args.get("evenement")
    if evenement:
        filtre["evenement"] = evenement
    docs = list(COL_HSH_ERREURS.find(filtre).sort("date_paris", -1).limit(50))
    for d in docs:
        d["_id"] = str(d["_id"])
        for k, v in d.items():
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
            elif isinstance(v, ObjectId):
                d[k] = str(v)
    return jsonify(docs)


@app.route('/api/live-controle/status', methods=['GET'])
@role_required("user")
def hsh_get_status():
    doc = _hsh_read_global()
    dernier_cycle = doc.get("dernier_cycle")
    health = "unknown"
    age_seconds = None
    if dernier_cycle and hasattr(dernier_cycle, "year"):
        # Gerer les datetimes naives (sans tz) stockes par live_controle.py
        if dernier_cycle.tzinfo is None:
            dernier_cycle = dernier_cycle.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - dernier_cycle).total_seconds()
        health = "ok" if age_seconds < 300 else "warning"
    elif doc.get("live_controle_actif"):
        health = "waiting"
    return jsonify({
        "live_controle_actif": doc.get("live_controle_actif", False),
        "evenement": doc.get("evenement", ""),
        "dernier_cycle": dernier_cycle.isoformat() if hasattr(dernier_cycle, "isoformat") else dernier_cycle,
        "dernier_inventaire": doc.get("dernier_inventaire").isoformat() if hasattr(doc.get("dernier_inventaire"), "isoformat") else doc.get("dernier_inventaire"),
        "dernier_transaction_id": doc.get("dernier_transaction_id"),
        "nb_locations": len(doc.get("locations_selectionnees", [])),
        "health": health,
        "age_seconds": int(age_seconds) if age_seconds is not None else None,
    })


################################################################################
# WhatsApp (WAHA) - API admin
################################################################################

from whatsapp import WhatsAppService
_wa_service = WhatsAppService(db)

@app.route('/api/whatsapp/config', methods=['GET'])
@role_required("admin")
def wa_get_config():
    cfg = _wa_service.get_config()
    out = dict(cfg)
    out.pop('_id', None)
    return jsonify(out)

@app.route('/api/whatsapp/config', methods=['PUT'])
@role_required("admin")
def wa_update_config():
    data = request.get_json(force=True) or {}
    patch = {}
    if 'enabled' in data:
        patch['enabled'] = bool(data['enabled'])
    if 'waha_url' in data:
        patch['waha_url'] = (data['waha_url'] or '').strip()
    if 'session_name' in data:
        patch['session_name'] = (data['session_name'] or '').strip()
    if 'rate_limit_per_hour' in data:
        patch['rate_limit_per_hour'] = max(1, int(data['rate_limit_per_hour']))
    if 'rate_limit_per_day' in data:
        patch['rate_limit_per_day'] = max(1, int(data['rate_limit_per_day']))
    if 'global_cooldown_minutes' in data:
        patch['global_cooldown_minutes'] = max(1, int(data['global_cooldown_minutes']))
    if 'type_cooldown_minutes' in data:
        patch['type_cooldown_minutes'] = max(1, int(data['type_cooldown_minutes']))
    if 'quiet_hours' in data:
        qh = data['quiet_hours'] or {}
        patch['quiet_hours'] = {
            'enabled': bool(qh.get('enabled', False)),
            'start': str(qh.get('start', '23:00')),
            'end': str(qh.get('end', '06:00')),
        }
    if 'api_key' in data:
        patch['api_key'] = (data['api_key'] or '').strip()
    if not patch:
        return jsonify({"error": "Rien a modifier"}), 400
    patch['updatedAt'] = datetime.now(timezone.utc)
    COL_WA_CONFIG.update_one(
        {"_id": "wa_config"},
        {"$set": patch},
        upsert=True,
    )
    _wa_service._config_cache = None
    return jsonify({"ok": True})

@app.route('/api/whatsapp/status', methods=['GET'])
@role_required("admin")
def wa_status():
    session = _wa_service.check_session()
    stats = _wa_service.get_stats()
    return jsonify({"session": session, "stats": stats})

@app.route('/api/whatsapp/test', methods=['POST'])
@role_required("admin")
def wa_send_test():
    data = request.get_json(force=True) or {}
    chat_id = (data.get('chat_id') or '').strip()
    if not chat_id:
        return jsonify({"error": "chat_id requis"}), 400
    ok, detail = _wa_service.send_test(chat_id)
    return jsonify({"ok": ok, "detail": detail}), 200 if ok else 500

# --- Groupes WhatsApp ---

@app.route('/api/whatsapp/groups', methods=['GET'])
@role_required("admin")
def wa_list_groups():
    groups = list(COL_WA_GROUPS.find().sort("name", 1))
    for g in groups:
        g['_id'] = str(g['_id'])
    return jsonify(groups)

@app.route('/api/whatsapp/groups/sync', methods=['POST'])
@role_required("admin")
def wa_sync_groups():
    remote = _wa_service.get_groups()
    synced = 0
    for g in remote:
        gid = g.get("_chat_id", "")
        if not gid:
            continue
        meta = g.get("groupMetadata") or {}
        participant_count = len(meta.get("participants") or [])
        COL_WA_GROUPS.update_one(
            {"group_id": gid},
            {"$set": {
                "group_id": gid,
                "name": g.get("name") or meta.get("subject") or gid,
                "participants_count": participant_count,
                "last_synced": datetime.now(timezone.utc),
            }, "$setOnInsert": {
                "enabled": False,
                "createdAt": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
        synced += 1
    return jsonify({"synced": synced})

@app.route('/api/whatsapp/groups/clear', methods=['DELETE'])
@role_required("admin")
def wa_clear_groups():
    result = COL_WA_GROUPS.delete_many({})
    return jsonify({"ok": True, "deleted": result.deleted_count})

@app.route('/api/whatsapp/groups/<gid>', methods=['PUT'])
@role_required("admin")
def wa_update_group(gid):
    try:
        oid = ObjectId(gid)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    data = request.get_json(force=True) or {}
    patch = {}
    if 'enabled' in data:
        patch['enabled'] = bool(data['enabled'])
    if 'description' in data:
        patch['description'] = (data['description'] or '').strip()
    if not patch:
        return jsonify({"error": "Rien a modifier"}), 400
    COL_WA_GROUPS.update_one({"_id": oid}, {"$set": patch})
    return jsonify({"ok": True})

@app.route('/api/whatsapp/groups/<gid>', methods=['DELETE'])
@role_required("admin")
def wa_delete_group(gid):
    try:
        oid = ObjectId(gid)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    COL_WA_GROUPS.delete_one({"_id": oid})
    return jsonify({"ok": True})

# --- Contacts DM ---

@app.route('/api/whatsapp/contacts', methods=['GET'])
@role_required("admin")
def wa_list_contacts():
    contacts = list(COL_WA_CONTACTS.find().sort("name", 1))
    for c in contacts:
        c['_id'] = str(c['_id'])
    return jsonify(contacts)

@app.route('/api/whatsapp/contacts', methods=['POST'])
@role_required("admin")
def wa_create_contact():
    data = request.get_json(force=True) or {}
    phone = (data.get('phone') or '').strip()
    name = (data.get('name') or '').strip()
    if not phone or not name:
        return jsonify({"error": "phone et name requis"}), 400
    # Nettoyer le numero : garder uniquement les chiffres
    phone = ''.join(c for c in phone if c.isdigit())
    doc = {
        "phone": phone,
        "name": name,
        "role": (data.get('role') or '').strip(),
        "enabled": True,
        "createdAt": datetime.now(timezone.utc),
    }
    try:
        COL_WA_CONTACTS.insert_one(doc)
    except Exception:
        return jsonify({"error": "Ce numero existe deja"}), 409
    doc['_id'] = str(doc['_id'])
    return jsonify(doc), 201

@app.route('/api/whatsapp/contacts/<cid>', methods=['PUT'])
@role_required("admin")
def wa_update_contact(cid):
    try:
        oid = ObjectId(cid)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    data = request.get_json(force=True) or {}
    patch = {}
    if 'name' in data:
        patch['name'] = (data['name'] or '').strip()
    if 'role' in data:
        patch['role'] = (data['role'] or '').strip()
    if 'enabled' in data:
        patch['enabled'] = bool(data['enabled'])
    if 'phone' in data:
        phone = (data['phone'] or '').strip()
        patch['phone'] = ''.join(c for c in phone if c.isdigit())
    if not patch:
        return jsonify({"error": "Rien a modifier"}), 400
    COL_WA_CONTACTS.update_one({"_id": oid}, {"$set": patch})
    return jsonify({"ok": True})

@app.route('/api/whatsapp/contacts/<cid>', methods=['DELETE'])
@role_required("admin")
def wa_delete_contact(cid):
    try:
        oid = ObjectId(cid)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    COL_WA_CONTACTS.delete_one({"_id": oid})
    return jsonify({"ok": True})

# --- Historique envoi ---

@app.route('/api/whatsapp/history', methods=['GET'])
@role_required("admin")
def wa_history():
    page = max(1, int(request.args.get('page', 1)))
    limit = min(100, max(1, int(request.args.get('limit', 20))))
    slug = request.args.get('slug', '').strip()
    query = {}
    if slug:
        query['alert_slug'] = {"$regex": slug}
    total = COL_WA_HISTORY.count_documents(query)
    docs = list(
        COL_WA_HISTORY.find(query)
        .sort("sentAt", -1)
        .skip((page - 1) * limit)
        .limit(limit)
    )
    for d in docs:
        d['_id'] = str(d['_id'])
        if d.get('sentAt'):
            d['sentAt'] = d['sentAt'].isoformat()
        if d.get('createdAt'):
            d['createdAt'] = d['createdAt'].isoformat()
    return jsonify({
        "items": docs,
        "total": total,
        "page": page,
        "limit": limit,
    })

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
        app.run(debug=True, use_reloader=True, host="127.0.0.1", port=PORT)
    else:
        logger.warning(f"[PROD] Running TITAN Home on port {PORT}")
        serve(app, host="0.0.0.0", port=PORT)
