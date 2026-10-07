# Standard library imports
import os
import re
import unicodedata
import json
import hmac
import logging
import uuid
import subprocess
import hashlib
import time
import jwt
from datetime import datetime, timedelta, timezone

# Third-party imports
from flask import (
    Flask, Blueprint, jsonify, render_template, send_from_directory, request,
    redirect, url_for, flash, session, abort, make_response, g
)
from flask_cors import CORS
from flask_wtf.csrf import CSRFProtect, CSRFError
from functools import wraps
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun
from pymongo import MongoClient, ReturnDocument
from werkzeug.utils import safe_join
from bson.objectid import ObjectId
from waitress import serve

# Local application imports
from traffic import traffic_bp
from merge import run_merge
from analyse_ops import analyse_ops_bp
from scan_report import scan_report_bp
from anoloc import anoloc_bp
from anpr import anpr_bp
from field import field_bp
from vision_admin import vision_admin_bp
from crise_auth import crise_auth_bp
from routing import routing_bp
from routing_overrides import routing_overrides_bp
from cameras import cameras_bp
from meteo import meteo_bp
from pmv import pmv_bp
from pcorg_assist import pcorg_assist_bp
import dispatch_auto as DA
import event_courant as EC
from momentus_api import momentus_bp
from saison_indicateurs_api import saison_indicateurs_bp
from musee_api import musee_bp
from ai_reports import ai_reports_bp
from alert_ai import alert_ai_bp
import pcorg_summary
import pcorg_summary_mail
import pcorg_ai_memory
import alfred
from alfred import alfred_bp

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
        # code "csrf" : le front renouvelle son jeton (/api/csrf-token) et rejoue
        return jsonify({"error": "CSRF token manquant ou invalide", "code": "csrf"}), 400
    return e.get_body(), 400

# Validation stricte pour la clé secrète en production
if not DEV_MODE and not os.getenv('SECRET_KEY'):
    raise ValueError("SECRET_KEY must be set via environment variable in production!")
if not DEV_MODE and not os.getenv('JWT_SECRET'):
    raise ValueError("JWT_SECRET must be set via environment variable in production!")
if not DEV_MODE and not os.getenv('SNAPSHOT_PUBLIC_SECRET'):
    logger.warning(
        "SNAPSHOT_PUBLIC_SECRET non configure en prod ! "
        "Les URLs publiques de snapshot WhatsApp sont signees avec JWT_SECRET en fallback "
        "et doivent etre regenerees apres rotation. Definir cette variable sur Cockpit ET sur la VM PCA "
        "(ecoutehik2.py / cockpit_dispatch.py) pour cloisonner."
    )

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
# Une entree d'historique par alerte active (ecrite par alert_engine.sync_alert_history)
COL_ALERT_HISTORY.create_index("alert_id", unique=True, sparse=True)
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
# Droit "Creer des fiches" (can_create_fiche) introduit le 30/09/2026 : tous
# les groupes existants creaient deja, ils le recoivent une fois pour ne rien
# retirer a personne. Un groupe cree ensuite part sans (droit explicite).
COL_GROUPS.update_many({"can_create_fiche": {"$exists": False}},
                       {"$set": {"can_create_fiche": True}})

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
                if sim_level < ROLE_HIERARCHY.get(required_role, 0) and not (
                        required_role == "admin" and request_admin_grant(request.user_payload)):
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

            # Page d'administration accordee par un groupe (ADMIN_PAGE_REGISTRY)
            granted = (max_user_role_level < ROLE_HIERARCHY.get(required_role, 0)
                       and required_role == "admin"
                       and request_admin_grant(dict(payload, app_role=effective_role,
                                                    is_super_admin=is_super_admin)))
            if max_user_role_level < ROLE_HIERARCHY.get(required_role, 0) and not granted:
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
    # Libelles = titres affiches sur la page d'accueil (index.html), pour que
    # la fiche d'un groupe parle le meme langage que l'ecran.
    "widget-comms":     {"label": "Main courante",     "default_column": "left"},
    "widget-parkings":  {"label": "Temps d'acces",     "default_column": "left"},
    "status-card":      {"label": "Statut evenement",  "default_column": None},
    "widget-counters":  {"label": "Controle d'acces",  "default_column": "right"},
    "widget-musee":     {"label": "Musee",             "default_column": "right"},
    "widget-right-1":   {"label": "Meteo",             "default_column": "right"},
    "widget-right-2":   {"label": "Affluence",         "default_column": "right"},
    "widget-right-3":   {"label": "Alertes",           "default_column": "right"},
    "widget-right-4":   {"label": "Suivi GPS",         "default_column": "right"},
    "meteo-previsions": {"label": "Meteo bandeau",     "default_column": None},
    "timeline-main":    {"label": "Timeline",          "default_column": None},
    "map-main":         {"label": "Carte",             "default_column": None},
}
ALL_BLOCK_IDS = list(BLOCK_REGISTRY.keys())

# Pages de la barre laterale qu'un groupe peut autoriser une par une
# (cockpit_groups.allowed_pages : None = toutes). Le role reste exige EN PLUS :
# une page manager n'est jamais ouverte a un simple user par un groupe. Les
# pages admin n'y figurent pas : le role admin voit tout, les groupes ne le
# restreignent pas. `paths` : prefixes controles cote serveur (avant_requete) ;
# "/" est exact. Sans chemin (Assistant IA) : seul le bouton est masque.
PAGE_REGISTRY = [
    {"id": "cockpit",      "label": "Cockpit (accueil)",  "icon": "dashboard",       "role": "user",    "paths": ["/"]},
    {"id": "portes",       "label": "Portes",             "icon": "door_front",      "role": "user",    "paths": ["/doors"]},
    {"id": "parkings",     "label": "Parkings",           "icon": "local_parking",   "role": "user",    "paths": ["/terrains"]},
    {"id": "statistiques", "label": "Statistiques",       "icon": "insert_chart",    "role": "user",    "paths": ["/general_stat"]},
    {"id": "wiki",         "label": "Wiki procedures",    "icon": "menu_book",       "role": "user",    "paths": ["/wiki"]},
    {"id": "dispatch",     "label": "File du service",    "icon": "assignment_ind",  "role": "user",    "paths": ["/dispatch-service", "/api/dispatch/board"]},
    {"id": "declarations", "label": "Constats terrain",   "icon": "report",          "role": "user",    "paths": ["/declarations", "/api/declarations"]},
    {"id": "assistant_ia", "label": "Assistant IA",       "icon": "smart_toy",       "role": "manager", "paths": []},
    {"id": "circulation",  "label": "Circulation",        "icon": "moving",          "role": "manager", "paths": ["/circulation"]},
    {"id": "meteo_mur",    "label": "Mur meteo",          "icon": "radar",           "role": "manager", "paths": ["/meteo-mur"]},
    {"id": "pmv",          "label": "PMV",                "icon": "signpost",        "role": "manager", "paths": ["/pmv", "/api/pmv"]},
]
ALL_PAGE_IDS = [p["id"] for p in PAGE_REGISTRY]

# Pages d'ADMINISTRATION accordables a un groupe (cockpit_groups.admin_pages),
# TOUJOURS explicitement : un groupe sans restriction de pages n'en recoit
# aucune. Chaque page embarque les routes admin qu'elle appelle, sinon elle
# s'ouvrirait et tous ses appels echoueraient en 403. `(methode, prefixe)` =
# prefixe accorde pour cette seule methode (lecture de la liste des groupes).
# ⚠️ Configuration (/config/todos : groupes, utilisateurs, fusion...) n'y est
# PAS : l'accorder permettrait a n'importe qui de se donner tous les droits.
ADMIN_PAGE_REGISTRY = [
    {"id": "live_controle",  "label": "Controle acces",  "icon": "sensors",
     "paths": ["/live-controle", "/api/live-controle"]},
    {"id": "field_dispatch", "label": "Field dispatch",  "icon": "tablet_android",
     "paths": ["/field-dispatch", "/field/admin", "/api/alfred", "/api/whatsapp",
               "/api/dispatch/config", "/api/admin/routing-overrides"]},
    {"id": "scan_report",    "label": "Rapport scans",   "icon": "qr_code_scanner",
     "paths": ["/scan-report"]},
    {"id": "analyse_ops",    "label": "Analyse Ops",     "icon": "biotech",
     "paths": ["/analyse-ops", "/api/analyse-ops"]},
    {"id": "lapi",           "label": "LAPI",            "icon": "directions_car",
     "paths": ["/anpr", "/api/anpr", "/api/anpr-watchlist"]},
    {"id": "alertes",        "label": "Alertes",         "icon": "notifications_active",
     "paths": ["/admin/alertes", "/api/alert-definitions", "/api/camera-event-types",
               "/api/cameras-list", "/api/anpr-watchlist", "/api/hik-events-stream",
               ("GET", "/api/groups")]},
    {"id": "wiki_admin",     "label": "Wiki (admin)",    "icon": "edit_note",
     "paths": ["/admin/wiki", "/api/wiki/admin"]},
    {"id": "cameras",        "label": "Cameras",         "icon": "videocam",
     "paths": ["/cameras", "/api/cameras"]},
    {"id": "montre",         "label": "Montre",          "icon": "watch",
     "paths": ["/watch-admin", "/api/v1/watch/admin"]},
]
ALL_ADMIN_PAGE_IDS = [p["id"] for p in ADMIN_PAGE_REGISTRY]


def _path_matches(path, pref):
    return path == pref or path.startswith(pref + "/")


def _admin_pages_for_path(path, method):
    """Pages d'administration dont releve ce chemin (plusieurs possibles :
    la liste de surveillance LAPI sert a LAPI et a Alertes)."""
    out = []
    for p in ADMIN_PAGE_REGISTRY:
        for spec in p["paths"]:
            if isinstance(spec, tuple):
                meth, pref = spec
                if method != meth:
                    continue
            else:
                pref = spec
            if _path_matches(path, pref):
                out.append(p["id"])
                break
    return out


def _parse_admin_pages(raw):
    if not isinstance(raw, list):
        return []
    return [p for p in ALL_ADMIN_PAGE_IDS if p in raw]


def _page_for_path(path):
    for p in PAGE_REGISTRY:
        for pref in p["paths"]:
            if pref == "/":
                if path == "/":
                    return p
            elif path == pref or path.startswith(pref + "/"):
                return p
    return None


def _parse_allowed_pages(raw):
    """Liste de pages autorisees ; None si non-liste ou toutes cochees."""
    if not isinstance(raw, list):
        return None
    pages = [p for p in ALL_PAGE_IDS if p in raw]
    return None if len(pages) == len(ALL_PAGE_IDS) else pages
MOVABLE_BLOCK_IDS = [bid for bid, info in BLOCK_REGISTRY.items() if info.get("default_column")]

DEFAULT_LAYOUT = {
    "left":  [bid for bid, info in BLOCK_REGISTRY.items() if info.get("default_column") == "left"],
    "right": [bid for bid, info in BLOCK_REGISTRY.items() if info.get("default_column") == "right"],
}

def _admin_display_group():
    return COL_GROUPS.find_one({"name": ADMIN_GROUP_NAME}) or {}


def get_user_display_blocks(payload):
    """Blocs AFFICHES sur l'accueil (set ou None = tous). Pour un admin : les
    preferences d'affichage du groupe __admin__ (01/10/2026 ; avant, un admin
    voyait toujours tout). Ce n'est PAS un droit : get_user_allowed_blocks reste
    None pour un admin, qui garde l'acces a toutes les API de blocs."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        ab = _admin_display_group().get("allowed_blocks")
        return set(ab) if ab else None
    return get_user_allowed_blocks(payload)


def _parse_saison_only_blocks(raw):
    """Blocs 'seulement en SAISON' d'un groupe : liste d'IDs connus."""
    if not isinstance(raw, list):
        return []
    return [b for b in raw if isinstance(b, str) and b in BLOCK_REGISTRY]


def get_user_saison_only_blocks(payload):
    """Blocs de l'accueil masques quand le poste est sur une epreuve (affiches
    seulement sur SAISON). Reglage de la fiche groupe (bascule SAISON de chaque
    bloc) ; admin : groupe __admin__. Plusieurs groupes : un bloc est reserve a
    SAISON des qu'un de ses groupes le demande. Affichage seulement, pas un droit."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return list(_admin_display_group().get("saison_only_blocks") or [])
    user_doc = db['users'].find_one({"email": payload.get("email", "")}, {"_id": 1})
    groups = []
    if user_doc:
        ug = COL_USER_GROUPS.find_one({"user_id": user_doc["_id"]})
        gids = (ug.get("groups") or []) if ug else []
        if gids:
            groups = list(COL_GROUPS.find({"_id": {"$in": gids}}, {"saison_only_blocks": 1}))
    if not groups:
        dg = COL_GROUPS.find_one({"name": DEFAULT_GROUP_NAME}, {"saison_only_blocks": 1})
        groups = [dg] if dg else []
    out = []
    for g in groups:
        for b in g.get("saison_only_blocks") or []:
            if b not in out:
                out.append(b)
    return out


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
    """Retourne dict {left: [...], right: [...]} ou None (= layout par defaut).
    Admin : disposition du groupe __admin__ (reglable depuis /edit)."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        bl = _admin_display_group().get("block_layout")
        if bl and isinstance(bl, dict) and ("left" in bl or "right" in bl):
            return bl
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

def _user_group_docs(payload):
    """Groupes cockpit de l'utilisateur (liste vide s'il n'en a aucun)."""
    email = payload.get("email", "")
    user_doc = db['users'].find_one({"email": email}, {"_id": 1}) if email else None
    if not user_doc:
        return []
    ug = COL_USER_GROUPS.find_one({"user_id": user_doc["_id"]})
    group_ids = (ug.get("groups") or []) if ug else []
    if not group_ids:
        return []
    return list(COL_GROUPS.find({"_id": {"$in": group_ids}}))


def _user_allowed_pages(payload):
    """Pages de la barre laterale ouvertes a l'utilisateur (None = toutes).
    Union des groupes ; un groupe sans restriction ouvre tout ; sans groupe,
    celles du groupe par defaut. Admin : toutes."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return None
    groups = _user_group_docs(payload)
    if not groups:
        dg = COL_GROUPS.find_one({"name": DEFAULT_GROUP_NAME}, {"allowed_pages": 1}) or {}
        return dg.get("allowed_pages")
    allowed = set()
    for g in groups:
        ap = g.get("allowed_pages")
        if ap is None:
            return None
        allowed.update(ap)
    return [p for p in ALL_PAGE_IDS if p in allowed]


def _user_admin_pages(payload):
    """Pages d'administration accordees a l'utilisateur par ses groupes
    (sans groupe : celles du groupe par defaut). Toutes pour un admin."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return list(ALL_ADMIN_PAGE_IDS)
    groups = _user_group_docs(payload)
    if not groups:
        dg = COL_GROUPS.find_one({"name": DEFAULT_GROUP_NAME}, {"admin_pages": 1}) or {}
        groups = [dg]
    granted = set()
    for grp in groups:
        granted.update(grp.get("admin_pages") or [])
    return [p for p in ALL_ADMIN_PAGE_IDS if p in granted]


def request_admin_grant(payload):
    """Vrai si la requete courante vise une page d'administration accordee a
    l'utilisateur par un de ses groupes. Appelee la ou le role admin est exige
    (role_required, field.admin_required, _check_admin des blueprints, montre)
    pour laisser passer un non-admin sur SES pages accordees seulement.
    Exige un role cockpit : un compte sans acces a l'app ne passe jamais."""
    if not payload or not payload.get("email"):
        return False
    role = (payload.get("roles_by_app") or {}).get(APP_KEY) or payload.get("app_role")
    if not role and not payload.get("is_super_admin"):
        return False
    pages = _admin_pages_for_path(request.path, request.method)
    if not pages:
        return False
    try:
        granted = _user_admin_pages(payload)
    except Exception:
        return False
    return any(p in granted for p in pages)


def _request_payload_peek():
    """Payload JWT de la requete courante sans rediriger (None si absent ou
    invalide : la route elle-meme gerera l'authentification). Meme calcul de
    role que role_required. Memorise dans g pour la requete."""
    if hasattr(g, "_payload_peek"):
        return g._payload_peek
    payload = None
    if CODING:
        sim = request.args.get("as", "admin")
        payload = {"email": "bruce@wayneenterprise.com",
                   "app_role": sim if sim in ROLE_HIERARCHY else "admin", "is_super_admin": False}
    else:
        token = request.cookies.get("access_token")
        if token:
            try:
                p = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
                is_sa = SUPER_ADMIN_ROLE in (p.get("global_roles") or [])
                p["is_super_admin"] = is_sa
                p["app_role"] = "admin" if is_sa else (p.get("roles_by_app") or {}).get(APP_KEY)
                payload = p
            except Exception:
                payload = None
    g._payload_peek = payload
    return payload


def _page_allowed_for_request(page_id):
    payload = _request_payload_peek()
    if payload is None:
        return True
    if not hasattr(g, "_allowed_pages"):
        try:
            g._allowed_pages = _user_allowed_pages(payload)
        except Exception:
            g._allowed_pages = None
    allowed = g._allowed_pages
    return allowed is None or page_id in allowed


@app.before_request
def _enforce_page_access():
    """Pages autorisees par groupe (Configuration > Groupes > Pages). Ne
    s'applique qu'aux chemins du registre PAGE_REGISTRY ; l'authentification
    et le role restent l'affaire de role_required."""
    page = _page_for_path(request.path)
    if not page or _page_allowed_for_request(page["id"]):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "page_non_autorisee"}), 403
    allowed = g._allowed_pages or []
    for p in PAGE_REGISTRY:
        if p["id"] in allowed and p["paths"] and not p["paths"][0].startswith("/api/"):
            if p["paths"][0] != request.path:
                return redirect(p["paths"][0])
    return ("<p style='font-family:sans-serif;padding:24px'>Aucune page Cockpit n'est "
            "ouverte a votre groupe. Contactez un administrateur.</p>"), 403


def _admin_page_allowed_for_request(page_id):
    payload = _request_payload_peek()
    if payload is None:
        return False
    if not hasattr(g, "_admin_pages"):
        try:
            g._admin_pages = _user_admin_pages(payload)
        except Exception:
            g._admin_pages = []
    return page_id in g._admin_pages


@app.context_processor
def _inject_page_access():
    """page_allowed('<id>') / admin_page_allowed('<id>') dans les gabarits
    (barre laterale)."""
    return {"page_allowed": _page_allowed_for_request,
            "admin_page_allowed": _admin_page_allowed_for_request,
            "alfred_chat_allowed": _alfred_chat_allowed_for_request}


def _user_dispatch_categories(payload):
    """Categories dont l'utilisateur est responsable de service (page
    /dispatch-service) : celles des groupes coches "Responsable de service",
    toutes si le groupe ne restreint pas ses categories. Admin : toutes."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return list(ALL_PCO_CATEGORIES)
    cats = []
    for g in _user_group_docs(payload):
        if not g.get("dispatch_manager"):
            continue
        allowed = g.get("allowed_categories")
        for c in (allowed if allowed else ALL_PCO_CATEGORIES):
            if c not in cats:
                cats.append(c)
    return cats


def user_can_alfred_chat(payload):
    """Chat Alfred (widget flottant, alfred_chat.py) : option EXPLICITE d'un
    groupe (`alfred_chat`), coupee par defaut. Sans groupe : celle du groupe
    par defaut. Admin : toujours."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return True
    groups = _user_group_docs(payload)
    if not groups:
        dg = COL_GROUPS.find_one({"name": DEFAULT_GROUP_NAME}, {"alfred_chat": 1}) or {}
        groups = [dg]
    return any(g.get("alfred_chat") for g in groups)


def _alfred_chat_allowed_for_request():
    payload = _request_payload_peek()
    if not payload:
        return False
    if not hasattr(g, "_alfred_chat"):
        try:
            g._alfred_chat = user_can_alfred_chat(payload)
        except Exception:
            g._alfred_chat = False
    return g._alfred_chat


def _pcorg_created_by(payload, doc):
    """Vrai si la fiche a ete creee dans Cockpit par cet utilisateur."""
    email = str(payload.get("email") or "").strip().lower()
    return bool(email) and str((doc or {}).get("operator_id_create") or "").strip().lower() == email


def _user_can_edit_fiche(payload, doc=None):
    """Modifier les elements d'une fiche (description, categorie, urgence,
    position...). Un groupe "Fiches en lecture seule" ne modifie que les
    fiches creees par l'utilisateur lui-meme. Les droits des groupes
    s'additionnent : un seul groupe sans lecture seule suffit.
    Sans `doc` : droit general (affichage), sans l'exception "mes fiches"."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return True
    groups = _user_group_docs(payload)
    if not groups or any(not g.get("fiche_lecture_seule") for g in groups):
        return True
    return doc is not None and _pcorg_created_by(payload, doc)


def _user_can_create_fiche(payload):
    """Creer une fiche (assistant complet, creation rapide, File du service).
    Droit de groupe `can_create_fiche` ; sans groupe, celui du groupe par
    defaut. Les groupes anterieurs au droit l'ont recu a la migration."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return True
    groups = _user_group_docs(payload)
    if not groups:
        default_group = COL_GROUPS.find_one({"name": DEFAULT_GROUP_NAME}, {"can_create_fiche": 1})
        return bool((default_group or {}).get("can_create_fiche", True))
    return any(g.get("can_create_fiche") for g in groups)


def _user_can_treat_declaration(payload):
    """Constats terrain des tablettes declarantes (/declarations) : suivre,
    annoter, classer, transformer en fiche. Droit de groupe explicite
    `can_convert_declaration` (aucun groupe ne l'a par defaut) ; admin
    toujours. Transformer exige EN PLUS de pouvoir creer la fiche."""
    if payload.get("is_super_admin") or payload.get("app_role") == "admin":
        return True
    return any(g.get("can_convert_declaration") for g in _user_group_docs(payload))


_PCORG_CREATE_ERROR = ({"error": "Votre groupe ne permet pas de creer des fiches",
                        "code": "creation_interdite"}, 403)


def _user_can_close_fiche_cat(payload, category):
    """Cloture : droit "Cloturer des fiches" de groupe, ou responsable de
    service de la categorie de la fiche."""
    return _user_can_close_fiche(payload) or category in _user_dispatch_categories(payload)


_PCORG_READONLY_ERROR = ({"error": "Votre groupe ne permet de modifier que les fiches que "
                                   "vous avez creees (sur les autres : action et cloture uniquement)",
                          "code": "lecture_seule"}, 403)


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
    allowed = get_user_display_blocks(payload)
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
                           saison_only_blocks_json=json.dumps(get_user_saison_only_blocks(payload)),
                           user_groups_json=user_groups_json,
                           user_fiche_simplifiee_json=json.dumps(user_fiche_simplifiee),
                           user_allowed_categories_json=json.dumps(get_user_allowed_categories(payload)),
                           user_can_close_fiche_json=json.dumps(_user_can_close_fiche(payload)),
                           user_can_edit_fiche_json=json.dumps(_user_can_edit_fiche(payload)),
                           user_dispatch_categories_json=json.dumps(_user_dispatch_categories(payload)),
                           user_can_create_fiche_json=json.dumps(_user_can_create_fiche(payload)))

@app.route('/api/csrf-token', methods=['GET'])
@role_required("user")
def api_csrf_token():
    """Jeton CSRF frais pour une page restee ouverte (static/js/csrf_refresh.js).

    Le jeton de la balise <meta> expire apres WTF_CSRF_TIME_LIMIT (1 h par
    defaut) : sans renouvellement, la premiere ecriture d'un onglet ancien
    echouait, souvent apres un formulaire entierement rempli. generate_csrf()
    re-signe le jeton de session avec un horodatage neuf.
    """
    from flask_wtf.csrf import generate_csrf
    resp = jsonify({"csrf_token": generate_csrf(),
                    "time_limit": app.config.get("WTF_CSRF_TIME_LIMIT", 3600)})
    resp.headers["Cache-Control"] = "no-store"
    return resp


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


@app.route('/api/event/current', methods=['GET'])
@role_required("user")
def api_event_current():
    """Evenement du jour (epreuve active sinon SAISON) et liste des actifs.
    Source unique : event_courant.py."""
    return jsonify(EC.payload(db))


@app.route('/api/event/priority', methods=['PUT'])
@role_required("admin")
def api_event_priority():
    """Choix GLOBAL de l'epreuve prioritaire quand plusieurs sont actives.
    Body {event, year}. Ne deplace aucune fiche : seules les nouvelles fiches,
    la selection par defaut des postes et les rapports suivent ce choix."""
    data = request.get_json(silent=True) or {}
    event = (data.get("event") or "").strip()
    try:
        year = int(data.get("year"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "year_invalide"}), 400
    if not event or EC.is_saison(event):
        return jsonify({"ok": False, "error": "evenement_invalide"}), 400
    if not any(w["event"] == event and int(w["year"]) == year for w in EC.windows(db)):
        return jsonify({"ok": False, "error": "epreuve_inconnue"}), 404
    user = getattr(request, "user_payload", None) or {}
    who = " ".join(x for x in (user.get("firstname"), user.get("lastname")) if x) or user.get("email")
    EC.set_priority(db, event, year, user=who)
    return jsonify({"ok": True, **EC.payload(db)})

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

    # SAISON : le document porte tout le futur Momentus (momentus_timeline.py) ;
    # la timeline n'affiche que la veille -> J+14 (choix exploitation 01/10/2026).
    # Rapports et montre lisent la base directement, sans ce filtre.
    if EC.is_saison(event) and isinstance(timetable_doc.get("data"), dict):
        today = datetime.now(ZoneInfo("Europe/Paris")).date()
        lo = (today - timedelta(days=1)).isoformat()
        hi = (today + timedelta(days=14)).isoformat()
        timetable_doc["data"] = {d: v for d, v in timetable_doc["data"].items()
                                 if not re.match(r"^\d{4}-\d{2}-\d{2}$", d) or lo <= d <= hi}
        timetable_doc["window"] = {"from": lo, "to": hi}

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
        used = [doc["_id"] for doc in result if doc["_id"]]

        # Fusion avec la liste persistante (categories ajoutees manuellement,
        # potentiellement sans vignette) puis tri alphabetique insensible a la casse.
        custom_doc = COL_TT_CATEGORIES.find_one({"event": event, "year": year}) or {}
        custom = custom_doc.get("categories", []) or []
        merged = sorted(set(used) | set(custom), key=lambda s: (s or "").lower())
        return jsonify({"categories": merged})
    except Exception as e:
        logger.error("Error getting categories: " + str(e))
        return jsonify({"categories": []}), 500


# Collection des categories Timetable curees manuellement (par event/year).
# Permet d'ajouter une categorie avant qu'une vignette ne l'utilise, et de
# supprimer les "orphelines" (categories sans aucune vignette) sans risque.
COL_TT_CATEGORIES = db['timetable_custom_categories']


def _used_category_counts(event, year):
    """Retourne {categorie: nombre_de_vignettes} pour event/year."""
    pipeline = [
        {"$match": {"event": event, "year": str(year)}},
        {"$project": {"events": {"$objectToArray": "$data"}}},
        {"$unwind": "$events"},
        {"$unwind": "$events.v"},
        {"$group": {"_id": "$events.v.category", "count": {"$sum": 1}}},
    ]
    out = {}
    for doc in db.timetable.aggregate(pipeline):
        if doc["_id"]:
            out[doc["_id"]] = doc["count"]
    return out


@app.route('/api/timetable-categories', methods=['GET'])
@role_required("user")
def list_timetable_categories():
    """Liste enrichie pour le gestionnaire de categories de la modale.

    Retourne [{name, count, custom, orphan}] triee, count = nombre de vignettes,
    orphan = count 0 (supprimable).
    """
    event = request.args.get('event')
    year = str(request.args.get('year') or '')
    if not event or not year:
        return jsonify({"categories": []}), 400
    counts = _used_category_counts(event, year)
    custom_doc = COL_TT_CATEGORIES.find_one({"event": event, "year": year}) or {}
    custom = set(custom_doc.get("categories", []) or [])
    names = set(counts) | custom
    out = []
    for n in sorted(names, key=lambda s: (s or "").lower()):
        cnt = counts.get(n, 0)
        out.append({"name": n, "count": cnt, "custom": n in custom, "orphan": cnt == 0})
    return jsonify({"categories": out})


@app.route('/api/timetable-categories', methods=['POST'])
@role_required("user")
def add_timetable_category():
    """Ajoute une categorie a la liste persistante (event/year)."""
    data = request.get_json(force=True) or {}
    event = data.get('event')
    year = str(data.get('year') or '')
    cat = (data.get('category') or '').strip()
    if not event or not year or not cat:
        return jsonify({"ok": False, "error": "missing_params"}), 400
    if len(cat) > 120:
        return jsonify({"ok": False, "error": "too_long"}), 400
    COL_TT_CATEGORIES.update_one(
        {"event": event, "year": year},
        {"$addToSet": {"categories": cat}},
        upsert=True,
    )
    return jsonify({"ok": True, "category": cat})


@app.route('/api/timetable-categories', methods=['DELETE'])
@role_required("manager")
def delete_timetable_category():
    """Supprime une categorie SI elle est orpheline (0 vignette)."""
    data = request.get_json(force=True) or {}
    event = data.get('event')
    year = str(data.get('year') or '')
    cat = (data.get('category') or '').strip()
    if not event or not year or not cat:
        return jsonify({"ok": False, "error": "missing_params"}), 400
    counts = _used_category_counts(event, year)
    if counts.get(cat, 0) > 0:
        return jsonify({"ok": False, "error": "not_orphan",
                        "count": counts[cat]}), 409
    COL_TT_CATEGORIES.update_one(
        {"event": event, "year": year},
        {"$pull": {"categories": cat}},
    )
    return jsonify({"ok": True})

# -------------------------------------------------------------------------------
# Validation commune ajout / edition d'une vignette timetable
# -------------------------------------------------------------------------------
_TT_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_TT_TIME_RE = re.compile(r'^(\d{1,2})\s*[:hH.]\s*(\d{2})$')
_TT_HOUR_RE = re.compile(r'^(\d{1,2})\s*[hH]$')
_TT_TEXT_LIMITS = {
    "activity": 200, "category": 120, "place": 200, "department": 120,
    "duration": 20, "remark": 4000, "todo": 20000,
}
_TT_PREP_VALUES = {"", "non", "progress", "true"}

# Champs ecrases par la synchronisation du parametrage (merge.PARAM_FIELDS) :
# les modifier depuis la modale serait perdu au prochain merge.
_TT_PARAM_MANAGED_FIELDS = (
    "date", "start", "end", "duration", "activity",
    "category", "place", "department",
)


def _tt_norm_time(raw):
    """Normalise une heure : '' | 'TBC' | 'HH:MM'. Leve ValueError si illisible."""
    s = str(raw or '').strip()
    if not s:
        return ''
    if s.upper() == 'TBC':
        return 'TBC'
    m = _TT_TIME_RE.match(s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
    else:
        m = _TT_HOUR_RE.match(s)
        if not m:
            raise ValueError(s)
        h, mi = int(m.group(1)), 0
    if mi > 59 or h > 24 or (h == 24 and mi != 0):
        raise ValueError(s)
    return f"{h:02d}:{mi:02d}"


def _tt_is_param_managed(ev):
    """Vignette generee par le parametrage (et donc reecrite par le merge)."""
    return bool(ev.get("param_id")) and ev.get("origin") in ("parametrage", "manual-edit")


def _tt_clean_payload(data):
    """Valide et normalise les champs d'une vignette.

    Retourne (fields, None) ou (None, message_erreur).
    """
    date = str(data.get('date') or '').strip()
    if not _TT_DATE_RE.match(date):
        return None, "Date invalide (format attendu AAAA-MM-JJ)."
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None, "Date invalide."

    try:
        start = _tt_norm_time(data.get('start'))
    except ValueError:
        return None, "Heure de debut invalide (format HH:MM ou TBC)."
    try:
        end = _tt_norm_time(data.get('end'))
    except ValueError:
        return None, "Heure de fin invalide (format HH:MM ou TBC)."
    if not start and not end:
        return None, "Renseigner au moins une heure (debut ou fin)."

    fields = {"date": date, "start": start, "end": end}
    for key, limit in _TT_TEXT_LIMITS.items():
        val = data.get(key)
        val = '' if val is None else str(val)
        val = val.strip() if key not in ("remark", "todo") else val.rstrip()
        if len(val) > limit:
            return None, f"Champ '{key}' trop long ({limit} caracteres max)."
        fields[key] = val
    if not fields["activity"]:
        return None, "L'activite est obligatoire."
    if not fields["category"]:
        return None, "La categorie est obligatoire."

    prep = str(data.get('preparation_checked') or '').strip().lower()
    fields["preparation_checked"] = prep if prep in _TT_PREP_VALUES else ''
    return fields, None


def _tt_event_year(data):
    event_name = str(data.get('event') or '').strip()
    year = str(data.get('year') or '').strip()
    return event_name, year


# -------------------------------------------------------------------------------
# Route pour ajouter un événement dans la collection timetable
# -------------------------------------------------------------------------------
@app.route('/add_timetable_event', methods=['POST'])
@role_required("user")
def add_timetable_event():
    try:
        data = request.get_json(silent=True) or {}
        event_name, year = _tt_event_year(data)
        if not event_name or not year:
            return jsonify({"success": False, "message": "Aucun evenement selectionne."}), 400

        fields, err = _tt_clean_payload(data)
        if err:
            return jsonify({"success": False, "message": err}), 400

        date = fields.pop("date")
        event_details = dict(fields)
        event_details.update({
            "_id": str(ObjectId()),
            "type": "Timetable",
            "origin": "manual",
        })

        # upsert : cree le document event/year s'il n'existe pas encore.
        # $push cree la cle de date si elle est absente.
        db.timetable.update_one(
            {"event": event_name, "year": year},
            {"$push": {f"data.{date}": event_details}, "$inc": {"version": 1}},
            upsert=True,
        )
        return jsonify({"success": True, "message": "Événement ajouté avec succès.",
                        "_id": event_details["_id"]})
    except Exception as e:
        logger.error("Erreur lors de l'ajout de l'événement dans la timetable: %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Erreur lors de l'ajout de l'événement."}), 500

# -------------------------------------------------------------------------------
# Mettre à jour un événement (édition dans la liste imbriquée par date + _id)
# payload attendu: { event, year, date, _id, start, end, duration, category, activity, place, department, remark }
# Vignette issue du parametrage : seuls remark / todo / preparation_checked sont
# modifiables (le merge reecrit les autres champs a chaque synchronisation).
# -------------------------------------------------------------------------------
@app.route('/update_timetable_event', methods=['POST'])
@role_required("user")
def update_timetable_event():
    try:
        data = request.get_json(silent=True) or {}
        event_name, year = _tt_event_year(data)
        ev_id = str(data.get('_id') or '')

        if not all([event_name, year, ev_id]):
            return jsonify({"success": False, "message": "Paramètres manquants (event/year/_id)."}), 400

        doc = db.timetable.find_one({"event": event_name, "year": year})
        if not doc:
            return jsonify({"success": False, "message": "Document timetable introuvable."}), 404

        data_map = doc.get('data') or {}

        # Recherche par _id : d'abord sous la date annoncee, puis partout
        hint_date = str(data.get('date') or '')
        found_date, idx = None, None
        search_order = ([hint_date] if hint_date in data_map else []) + \
                       [d for d in data_map if d != hint_date]
        for d in search_order:
            j = next((i for i, ev in enumerate(data_map[d] or []) if str(ev.get('_id')) == ev_id), None)
            if j is not None:
                found_date, idx = d, j
                break

        if idx is None:
            return jsonify({"success": False, "message": "Événement introuvable."}), 404

        existing = data_map[found_date][idx]

        # scope="operator" : mise a jour partielle envoyee par le drawer (taches,
        # statut de preparation) sans revalider les horaires d'une vignette ancienne.
        if _tt_is_param_managed(existing) or data.get("scope") == "operator":
            # Champs operateur uniquement. L'origine reste inchangee.
            remark = data.get("remark")
            remark = existing.get("remark", "") if remark is None else str(remark).rstrip()
            todo = data.get("todo")
            todo = existing.get("todo", "") if todo is None else str(todo).rstrip()
            if len(remark) > _TT_TEXT_LIMITS["remark"] or len(todo) > _TT_TEXT_LIMITS["todo"]:
                return jsonify({"success": False, "message": "Remarque ou taches trop longues."}), 400
            prep = str(data.get("preparation_checked", existing.get("preparation_checked", "")) or "").lower()
            set_ops = {
                f"data.{found_date}.{idx}.remark": remark,
                f"data.{found_date}.{idx}.todo": todo,
                f"data.{found_date}.{idx}.preparation_checked": prep if prep in _TT_PREP_VALUES else "",
            }
            if remark != (existing.get("remark") or ""):
                # merge._patch_vignette conserve alors la remarque de l'operateur
                set_ops[f"data.{found_date}.{idx}.remark_manual"] = True
            db.timetable.update_one({"_id": doc["_id"]}, {"$set": set_ops, "$inc": {"version": 1}})
            return jsonify({"success": True, "message": "Événement mis à jour.",
                            "param_managed": _tt_is_param_managed(existing)})

        fields, err = _tt_clean_payload(data)
        if err:
            return jsonify({"success": False, "message": err}), 400

        target_date = fields.pop("date")
        fields["origin"] = "manual-edit"

        # Date changee -> on deplace l'objet mis a jour
        if found_date != target_date:
            moved = dict(existing)
            moved.update(fields)
            db.timetable.update_one(
                {"_id": doc["_id"]},
                {"$pull": {f"data.{found_date}": {"_id": existing.get("_id")}}}
            )
            db.timetable.update_one(
                {"_id": doc["_id"]},
                {"$push": {f"data.{target_date}": moved}, "$inc": {"version": 1}}
            )
            # Une date videe ne doit pas laisser de section vide dans la timeline
            db.timetable.update_one(
                {"_id": doc["_id"], f"data.{found_date}": {"$size": 0}},
                {"$unset": {f"data.{found_date}": ""}}
            )
            return jsonify({"success": True, "message": "Événement déplacé et mis à jour."})

        set_ops = {f"data.{found_date}.{idx}.{k}": v for k, v in fields.items()}
        db.timetable.update_one({"_id": doc["_id"]}, {"$set": set_ops, "$inc": {"version": 1}})
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

        db.timetable.update_one({"event": event_name, "year": year}, {"$inc": {"version": 1}})
        db.timetable.update_one(
            {"event": event_name, "year": year, f"data.{date}": {"$size": 0}},
            {"$unset": {f"data.{date}": ""}}
        )
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
        if not _TT_DATE_RE.match(str(target_date)):
            return jsonify({"success": False, "message": "Date cible invalide (AAAA-MM-JJ)."}), 400

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
        new_ev["preparation_checked"] = ""
        new_ev["todo"] = re.sub(r'\[[xX]\]', '[ ]', str(src.get("todo") or ""))
        # La copie est une vignette manuelle : elle ne doit plus etre rattachee
        # au parametrage (sinon le merge pourrait la prendre pour l'originale).
        for k in ("param_id", "phase", "todos_type", "remark_manual"):
            new_ev.pop(k, None)
        if target_date == date:
            new_ev["activity"] = f"{src.get('activity') or ''} (copie)".strip()

        db.timetable.update_one(
            {"_id": doc["_id"]},
            {"$push": {f"data.{target_date}": new_ev}, "$inc": {"version": 1}}
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

        # WBGT par creneau, calcule ici et pas cote client : la formule vit dans
        # meteo_thermique et sert deja au mur et a la montre. La recoder en
        # JavaScript creerait une seconde verite qui divergerait un jour.
        import meteo_thermique as thermique
        for entree in (day_data.get('Heures') or []):
            temperature = entree.get('Température (°C)')
            if temperature is None:
                temperature = entree.get('Temperature (C)')
            humidite = entree.get('Humidité (%)')
            if humidite is None:
                humidite = entree.get('Humidite (%)')
            # 0 est une valeur legitime : tester `is None`, jamais la verite.
            if temperature is None or humidite is None:
                entree['wbgt_c'] = None
                continue
            try:
                valeur = thermique.wbgt_approche(float(temperature), float(humidite))
            except (TypeError, ValueError):
                valeur = None
            entree['wbgt_c'] = round(valeur, 1) if valeur is not None else None

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

def _meteo_val(doc, key):
    """Valeur numerique d'un releve donnees_meteo, None si absente/illisible.

    La collection contient des null (releve non publie par la station) et,
    pour les imports anterieurs a aout 2026, des NaN. Les deux doivent etre
    traites comme "pas de mesure" et non comme un zero.
    """
    value = doc.get(key)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if value != value else value  # NaN != NaN


def _meteo_round(doc, key, digits=1):
    value = _meteo_val(doc, key)
    return None if value is None else round(value, digits)


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
                # donnees_meteo peut contenir des valeurs manquantes (null pour un
                # releve non publie par la station, NaN pour les vieux imports) :
                # on les ecarte au lieu de contaminer les agregats.
                precipitations = [v for v in (_meteo_val(e, 'Précipitations (mm)') for e in monthly_data) if v is not None]
                temps_max = [v for v in (_meteo_val(e, 'Température max (°C)') for e in monthly_data) if v is not None]
                temps_min = [v for v in (_meteo_val(e, 'Température min (°C)') for e in monthly_data) if v is not None]

                total_precipitations = sum(precipitations)
                max_temperature = max(temps_max) if temps_max else 0
                min_temperature = min(temps_min) if temps_min else 0

                if temps_max:
                    avg_temperature = round(sum(temps_max) / len(temps_max), 1)
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
                                'max': _meteo_round(daily_data, 'Température max (°C)'),
                                'min': _meteo_round(daily_data, 'Température min (°C)')
                            },
                            'Précipitations Jour (mm)': _meteo_round(daily_data, 'Précipitations (mm)')
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

        # Plus besoin de rustine anti-NaN : _meteo_val ecarte les valeurs
        # manquantes en amont, le JSON produit est donc valide.
        return jsonify(result)

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

    # ── La vigilance Meteo-France prime sur les seuils internes ──
    #
    # Les seuils ci-dessus sont une appreciation locale, calculee sur la
    # prevision d'un point. La vigilance est un produit de securite publique,
    # opposable, et c'est elle que regardera la prefecture. Un orange officiel
    # doit donc allumer la jauge meme si aucun seuil interne n'est franchi --
    # l'inverse laisserait lire "RAS" un jour de vigilance orange orages.
    #
    # On ne prend que le MAXIMUM : la vigilance ne peut qu'elever le niveau,
    # jamais l'abaisser. Un vert officiel n'efface pas des rafales a 90 km/h
    # mesurees sur le circuit.
    vigilance_info = None
    try:
        bulletin = db.meteo_vigilance.find_one({'departement': '72'},
                                               sort=[('update_time', -1)])
    except Exception:
        bulletin = None

    if bulletin:
        # Correspondance des echelles : jaune -> orange chez nous (notre
        # echelle n'a que trois crans), orange et rouge -> red.
        couleur_vers_severite = {'vert': 'green', 'jaune': 'orange',
                                 'orange': 'red', 'rouge': 'red'}
        ordre = {'green': 0, 'orange': 1, 'red': 2}

        # Niveau et peremption viennent tous deux de meteo.py : une seule regle,
        # un seul endroit. Le calcul qui figurait ici ne lisait que l'echeance J
        # et les phenomenes, la ou le panneau developpe prenait le maximum de
        # toutes les echeances via couleur_max. Un jaune annonce pour DEMAIN
        # donnait donc "RAS — vigilance vert" dans la jauge et "vigilance jaune"
        # dans le panneau, au meme instant. Constate en production.
        #
        # La jauge dit l'etat COURANT : elle prend couleur_jour. Ce qui est
        # annonce pour demain se lit dans le panneau, a sa place, date.
        from meteo import etat_vigilance, niveau_vigilance
        fraicheur = etat_vigilance(bulletin)
        niveau = niveau_vigilance(bulletin)
        perime = bool(fraicheur['perime'])
        pire_couleur = niveau['couleur_jour']
        phenomenes = niveau['phenomenes_jour']

        severite_vigilance = couleur_vers_severite.get(pire_couleur, 'green')
        if not perime and ordre.get(severite_vigilance, 0) > ordre.get(max_severity, 0):
            max_severity = severite_vigilance

        # Nota : un bulletin encore valide mais non rafraichi eleve quand meme
        # le niveau. Il parle bien de maintenant ; c'est sa confirmation qui
        # manque, et sur un produit de securite l'exces de prudence est le bon
        # sens d'erreur. Seule la validite depassee cesse d'elever.
        vigilance_info = {
            'couleur': pire_couleur,
            'phenomenes': phenomenes,
            # Ce qui est annonce au-dela d'aujourd'hui, expose separement pour
            # que la jauge puisse le mentionner sans jamais le confondre avec
            # l'etat courant.
            'couleur_max': niveau['couleur_max'],
            'phenomenes_max': niveau['phenomenes_max'],
            'echeances': niveau['echeances'],
            'update_time': bulletin.get('update_time'),
            'age_h': fraicheur['age_h'],
            'perime': perime,
            'retard_collecte': fraicheur['retard_collecte'],
            'valide_jusqua': fraicheur['valide_jusqua'],
            'motif': fraicheur['motif'],
        }

    label = risk_labels[max_severity]
    if vigilance_info and vigilance_info['perime']:
        label += ' — bulletin perime'
    elif vigilance_info and vigilance_info['phenomenes'] and max_severity != 'green':
        label = ', '.join(vigilance_info['phenomenes'])

    return jsonify({
        'current': current_data,
        'alerts': deduped_alerts,
        'risk_level': max_severity,
        'risk_label': label,
        'vigilance': vigilance_info
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
app.register_blueprint(scan_report_bp)
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
# Routing overrides : corrections admin pour la carte (portails fermes,
# routes barrees...). Fusionne avec les penalites Waze dans routing._compute().
# Toutes routes admin, CSRF conserve.
app.register_blueprint(routing_overrides_bp)
app.register_blueprint(cameras_bp)
# PMV : pilotage des remorques a panneau a message variable (TCP 9520 vers les
# routeurs 4G, cf. pmv.py). Page manager, CSRF ACTIF sur toutes les ecritures.
app.register_blueprint(pmv_bp)
# Aide a la saisie des fiches et precedents (pcorg_assist.py). Routes user,
# CSRF ACTIF sur les POST (pcorg.js envoie X-CSRFToken via apiCall).
app.register_blueprint(pcorg_assist_bp)
# Dispatch des fiches vers les unites terrain (dispatch_auto.py) : proposition
# automatique a l'unite la plus proche, file du service (/dispatch-service).
# Routes user/admin, CSRF ACTIF sur les ecritures.
app.register_blueprint(DA.dispatch_bp)
# Constats terrain des tablettes Field en mode declarant (declarations.py) :
# depot depuis la tablette (cookie field_token -> CSRF exemptee sur ces deux
# POST seulement), suivi et transformation en fiche sur /declarations
# (routes user, CSRF ACTIF).
from declarations import declarations_bp, field_declaration_create, field_declaration_comment
app.register_blueprint(declarations_bp)
csrf.exempt(field_declaration_create)
csrf.exempt(field_declaration_comment)
# Reservations Momentus par lieu de la carte (momentus_api.py). GET user,
# lecture seule des collections momentus_* (synchro momentus_sync.py).
app.register_blueprint(momentus_bp)
# Indicateurs de la barre des jours de la timeline SAISON (saison_indicateurs*.py) :
# visites libres/guidees et pistes utilisees. GET user, PUT config admin
# (CSRF ACTIF), configuration globale dans cockpit_settings.
app.register_blueprint(saison_indicateurs_bp)
# Bloc Musee de l'accueil (musee_api.py) : GET user + bloc widget-musee,
# lecture seule des collections musee_* (collecte scripts/musee_collect.py,
# autonome du live-controle).
app.register_blueprint(musee_bp)
# Briefing de situation (manager) et RETEX de fin d'edition (admin), cf.
# ai_reports.py. CSRF ACTIF sur les POST (ai_reports.js envoie X-CSRFToken).
app.register_blueprint(ai_reports_bp)
# Explication IA d'une alerte (alert_ai.py). Route user, CSRF ACTIF.
app.register_blueprint(alert_ai_bp)
# Alfred (agent IA WhatsApp via VM Linux + WAHA webhook). Le webhook POST
# /api/wa/webhook est exempt de CSRF : WAHA ne sait pas envoyer un token CSRF,
# l'authentification se fait par HMAC (header X-Webhook-Hmac, secret partage
# WAHA_WEBHOOK_SECRET cote env). Les routes admin /api/alfred/* gardent leur
# CSRF (decorees @role_required("admin") dans les sections ad hoc).
app.register_blueprint(alfred_bp)
csrf.exempt(app.view_functions["alfred.wa_webhook"])
# Chat Alfred des operateurs (alfred_chat.py) : widget flottant, option de
# groupe `alfred_chat`. Routes /api/alfred-chat/* : CSRF ACTIF
# (alfred_chat.js envoie X-CSRFToken). Routes /api/alfred-tools/* : appelees
# par la VM, sans session ni jeton CSRF, authentifiees par HMAC
# (ALFRED_TOOLS_SECRET) -> exemptees, comme le webhook WAHA.
from alfred_chat import alfred_chat_bp
app.register_blueprint(alfred_chat_bp)
csrf.exempt(app.view_functions["alfred_chat.tools_manifest"])
csrf.exempt(app.view_functions["alfred_chat.tools_call"])
# Meteo : configuration de l'emprise de veille et lecture des grilles radar.
# Les collecteurs vivent dans tools/Meteo/ et ne sont pas pilotes d'ici ; ce
# blueprint expose la configuration qu'ils lisent, et l'etat de fraicheur des
# flux.
#
# CSRF ACTIVE, comme scan_report_bp : le seul verbe mutateur est PUT
# /api/meteo/config, appele depuis meteo_bbox.js qui envoie X-CSRFToken.
# Rien ici n'est consomme par un client externe qui ne saurait pas le faire.
app.register_blueprint(meteo_bp)

from watch_api import watch_bp
app.register_blueprint(watch_bp)
# Pas de csrf.exempt(watch_bp) : /state est un GET, que Flask-WTF ne protege
# pas, et les routes admin qui ecrivent doivent garder la protection.

# Espace exercices de crise : sous-arbre statique servi sous /crise.
#
# Architecture en deux blueprints :
#   - crise_auth_bp (importe depuis crise_auth.py) : routes specifiques avec
#     authentification PIN (master.html + files/* + input/* d'un exercice).
#     Conserve la CSRF protection. Set des cookies JWT de session animateur.
#     Doit etre enregistre AVANT crise_bp pour que ses routes specifiques aient
#     priorite sur le catch-all statique.
#   - crise_bp (defini ci-dessous) : catch-all statique pour le reste (hub,
#     landing, player.html, livefeed.html, assets/). Pas d'auth, pas de cookie.
#
# Garanties d'isolation (audit) sur crise_bp :
#   - Pas de @role_required, aucune lecture de cookie cockpit (JWT ou session).
#   - csrf.exempt(crise_bp) : Flask-WTF n'intercepte pas.
#   - 404 capturees localement (return direct) pour court-circuiter l'errorhandler
#     global qui redirige sinon vers le home cockpit avec un flash message.
#   - Fichiers/dossiers caches (commencant par '.', ex: .DS_Store, .git) bloques.
#   - Path traversal stoppe par safe_join (werkzeug) + send_from_directory.
#   - after_request : strip defensif des Set-Cookie au cas ou un middleware
#     externe en injecterait. crise_auth_bp est un blueprint distinct dont
#     les set_cookie ne sont PAS impactes par cet after_request.
#
# Le contenu vit dans cockpit/crise/ ; chaque exercice est un sous-dossier
# (ex: gpmotos2026/). cockpit/crise/index.html liste les exercices disponibles.

# Enregistrement de l'auth PIN : DOIT etre avant crise_bp.
app.register_blueprint(crise_auth_bp)
# Pas de csrf.exempt(crise_auth_bp) : la CSRF reste active sur le POST d'auth
# (le template injecte csrf_token() et le JS l'envoie via X-CSRFToken).

CRISE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crise")


# /crise (sans slash final) -> redirect vers /crise/ avec Location *relatif*.
# On ne laisse PAS Werkzeug strict_slashes generer un 308 absolu (qui exposerait
# 127.0.0.1:4008 derriere le reverse proxy). Avec un Location relatif "/crise/",
# le navigateur compose au host courant (cockpit.lemans.org).
@app.route("/crise")
def crise_root_no_slash():
    return redirect("/crise/", code=308)


crise_bp = Blueprint("crise", __name__, url_prefix="/crise")

# Patterns proteges par crise_auth_bp (PIN). Filet defensif au cas ou la priorite
# de routing Werkzeug devierait : ces chemins ne sont JAMAIS servis en clair par
# le catch-all statique. <exercise> = sous-dossier de cockpit/crise/.
_CRISE_PROTECTED_RE = re.compile(
    r"^[a-z0-9_\-]{1,64}/(master\.html|auth(/.*)?$|files/.+|regie\.js)$"
)

def _crise_path_has_hidden(filename: str) -> bool:
    return any(part.startswith(".") for part in filename.split("/") if part)

def _crise_path_is_protected(filename: str) -> bool:
    return bool(_CRISE_PROTECTED_RE.match(filename))

@crise_bp.route("/")
def crise_home():
    # Pas de strict_slashes=False : on laisse Flask rediriger /crise -> /crise/
    # (308) pour que les liens relatifs (ex: href="gpmotos2026/") se resolvent
    # bien sous /crise/.
    return send_from_directory(CRISE_ROOT, "index.html")

@crise_bp.route("/<path:filename>")
def crise_asset(filename):
    if _crise_path_has_hidden(filename):
        return ("Not found", 404)
    # Filet defensif : refuse de servir en clair les chemins proteges par
    # crise_auth_bp. Cette branche ne devrait JAMAIS etre atteinte si Werkzeug
    # route correctement (les routes specifiques de crise_auth_bp ont priorite),
    # mais on garde une protection au cas ou.
    if _crise_path_is_protected(filename):
        return ("Not found", 404)
    candidate = safe_join(CRISE_ROOT, filename)
    if not candidate or not os.path.exists(candidate):
        return ("Not found", 404)
    if os.path.isdir(candidate):
        if not os.path.isfile(os.path.join(candidate, "index.html")):
            return ("Not found", 404)
        return send_from_directory(candidate, "index.html")
    return send_from_directory(CRISE_ROOT, filename)

@crise_bp.after_request
def _crise_strip_cookies(response):
    if "Set-Cookie" in response.headers:
        del response.headers["Set-Cookie"]
    return response

app.register_blueprint(crise_bp)
csrf.exempt(crise_bp)

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


def _event_hist_aliases(event):
    """Noms sous lesquels un evenement peut etre stocke dans historique_controle.

    La collection historique_controle est incoherente : certains events y sont
    ecrits sous leur nom complet (ex: '24H AUTOS'), d'autres sous leur code court
    (ex: 'LMC' pour 'LE MANS CLASSIC', 'GPE', 'SBK'), parfois les deux. Le code
    court vit dans evenement.short. On renvoie donc [nom, short] (dedup, ordre
    preserve) pour requeter l'historique avec {'event': {'$in': aliases}}.
    """
    aliases = []
    if event:
        aliases.append(event)
        try:
            ev_doc = db.evenement.find_one({'nom': event}, {'_id': 0, 'short': 1})
            short = (ev_doc or {}).get('short')
            if short and short not in aliases:
                aliases.append(short)
        except Exception:
            pass
    return aliases


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


def _ticketing_product_names(param_doc):
    """Produits references dans globalHoraires.ticketing = le panier 'enceinte
    generale'.

    C'est le seul perimetre comparable d'une edition a l'autre : il ne contient
    que les titres d'entree affectes aux jours publics (mono-jour ou multi-jours),
    a l'exclusion des campings, parkings et des agregats synthetiques que le
    referentiel billetterie ajoute parfois (24C TOTAL_ENTREES, TOTAL_AA...) et
    qui double-comptent le reste.
    """
    gh = (param_doc.get('data') or {}).get('globalHoraires') or {}
    names = set()
    for tc in gh.get('ticketing', []) or []:
        for pname in tc.get('products', []) or []:
            if pname:
                names.add(pname)
    return names


def _param_race_date(param_doc):
    """Date de course d'un doc parametrages, avec repli.

    data.race -> globalHoraires.race -> 1er jour public. Le repli compte : sur
    plusieurs editions (24H CAMIONS 2024/2025/2026) data.race est absent et seul
    globalHoraires.race est renseigne ; sans repli toutes les courbes sortent
    vides.
    """
    data = param_doc.get('data') or {}
    gh = data.get('globalHoraires') or {}
    rd = _parse_race_date(data.get('race') or gh.get('race'))
    if rd:
        return rd
    for d in gh.get('dates', []) or []:
        try:
            return datetime.strptime(d.get('date', ''), '%Y-%m-%d').date()
        except Exception:
            continue
    return None


def _curve_points(products, race_date):
    """[(days_before_race, total_ventes)] tries descending pour un sous-ensemble
    de produits.

    Filtre la fenetre saisonniere autour de la course pour eviter de melanger les
    historiques de plusieurs editions. Applique un forward-fill par produit avant
    aggregation pour combler les trous de snapshots non synchronises.
    """
    if not products or not race_date:
        return []

    season_start = race_date - timedelta(days=300)
    season_end = race_date + timedelta(days=30)

    product_series = {}
    all_dates = set()
    for pname, p in products.items():
        series = {}
        for h in (p or {}).get('history', []):
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
        return []

    sorted_dates = sorted(all_dates)
    sorted_series = {pn: sorted(s.items()) for pn, s in product_series.items()}

    points = []
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
        dt = datetime.strptime(date_key, '%Y-%m-%d').date()
        points.append(((race_date - dt).days, total))

    points.sort(key=lambda x: x[0], reverse=True)
    return points


def _select_products(param_doc, product_names=None):
    """Produits d'un doc parametrages, restreints au panier demande.

    `None` = pas de filtre. Un ensemble **vide** n'est pas la meme chose : il
    signifie que l'edition n'a aucune config ticketing (cas de 24H AUTOS 2024,
    0 entree pour 106 produits) et donc aucun panier comparable. Retomber sur
    tous les produits reintroduirait campings et agregats TOTAL_*, c'est-a-dire
    exactement la courbe faussee qu'on cherche a eviter — mieux vaut ecarter
    l'edition des references que lui faire dire n'importe quoi.
    """
    prods = (param_doc.get('tickets') or {}).get('products', {}) or {}
    if product_names is None:
        return prods
    return {k: v for k, v in prods.items() if k in product_names}


def _fill_curve(param_doc, race_date_override=None, product_names=None):
    """Retourne [(days_before_race, total_ventes)] tries descending, final, race_date.

    race_date_override permet de forcer la date de reference (ex: portes plus
    fiable que parametrages) pour un calcul des days_before coherent.
    product_names restreint le panier : indispensable pour que le taux de
    remplissage interpole soit celui des entrees et pas celui d'un melange
    entrees + campings (qui saturent des janvier) + agregats double-comptes.
    """
    rd = race_date_override or _param_race_date(param_doc)
    if not rd:
        return [], 0, None
    prods = _select_products(param_doc, product_names)
    points = _curve_points(prods, rd)
    if not points:
        return [], 0, None

    final_from_curve = points[-1][1]  # points tries descending sur days_before
    final_actual = sum((p or {}).get('ventes', 0) for p in prods.values())
    final = max(final_from_curve, final_actual)

    return points, final, rd


def _interpolate_value(points, target_days_before):
    """Valeur absolue de la courbe a target_days_before jours de la course.

    Les snapshots billetterie sont hebdomadaires avec des trous d'un mois : on
    interpole lineairement entre les deux snapshots encadrants, et on borne aux
    extremites de la serie.
    """
    if not points:
        return None
    for i in range(len(points) - 1):
        d1, v1 = points[i]
        d2, v2 = points[i + 1]
        if d1 >= target_days_before >= d2:
            ratio = (d1 - target_days_before) / (d1 - d2) if d1 != d2 else 0
            return v1 + ratio * (v2 - v1)
    if target_days_before >= points[0][0]:
        return points[0][1]
    return points[-1][1]


def _interpolate_pct(points, final, target_days_before):
    """Interpole le % atteint a target_days_before jours de la course."""
    if not points or final <= 0:
        return None
    v = _interpolate_value(points, target_days_before)
    return None if v is None else v / final * 100


def _ticketing_day_groups(param_doc, race_date):
    """Regroupe les produits ticketing par portee de jours, exprimee en offsets
    au jour de course : {'all'} ou {(0,)} ou {(0, 1)}...

    La signature est **stable d'une edition a l'autre** alors que les noms de
    produits changent completement ('24C WEEK_END' -> '24C Entree Week-end  -
    Course'). C'est elle qui permet de projeter chaque type de billet sur son
    propre rythme de vente : a J-50 un Pack VIP est ecoule aux deux tiers quand
    un billet Samedi en est au cinquieme. Un taux unique pour tout le panier
    suppose que le mix produits de N est celui de N-1 — vrai sur le total,
    faux jour par jour.
    """
    gh = (param_doc.get('data') or {}).get('globalHoraires') or {}
    groups = {}
    for tc in gh.get('ticketing', []) or []:
        prods = [p for p in (tc.get('products') or []) if p]
        if not prods:
            continue
        scope = tc.get('days', [])
        if scope == 'all':
            sig = 'all'
        elif race_date:
            offsets = set()
            for ds in scope or []:
                try:
                    offsets.add((datetime.strptime(ds, '%Y-%m-%d').date() - race_date).days)
                except Exception:
                    continue
            if not offsets:
                continue
            sig = tuple(sorted(offsets))
        else:
            continue
        groups.setdefault(sig, set()).update(prods)
    return groups


def _group_fill_rates(param_doc, race_ref, target_days_before):
    """{signature: part du final deja vendue a target_days_before} pour une
    edition de reference."""
    rates = {}
    for sig, names in _ticketing_day_groups(param_doc, race_ref).items():
        pts, final, _ = _fill_curve(param_doc, race_date_override=race_ref, product_names=names)
        pct = _interpolate_pct(pts, final, target_days_before)
        if pct and pct > 0:
            rates[sig] = pct / 100
    return rates


def _reference_weights(n):
    """Poids geometriques decroissants : N-1 pese le double de N-2, etc.

    Rien ne prouve que l'edition la plus recente soit la plus predictive, mais
    elle partage davantage de contexte (grille tarifaire, calendrier de mise en
    vente) avec la saison en cours.
    """
    if n <= 0:
        return []
    raw = [0.5 ** i for i in range(n)]
    total = sum(raw)
    return [r / total for r in raw]


@app.route('/get_affluence', methods=['GET'])
@role_required("user")
@block_required("widget-right-2")
def get_affluence():
    event = request.args.get("event")
    year = request.args.get("year")
    if not event or not year:
        return jsonify({"error": "Missing event or year"}), 400
    # SAISON : ses jours publics sont des jours de visites libres (billet
    # musee), sans billetterie ni course : aucune affluence previsionnelle.
    # Reponse vide = widget masque (affluence.js).
    if EC.is_saison(event):
        return jsonify({"days": [], "total_ventes": None, "saison": True})

    # Charger parametrages complet (data + tickets a la racine)
    doc = db['parametrages'].find_one({'event': event, 'year': year}, {'_id': 0})
    if not doc or 'data' not in doc:
        return jsonify({"days": [], "total_ventes": None})

    gh = doc['data'].get('globalHoraires', {})
    public_days = gh.get('dates', [])
    ticketing_config = gh.get('ticketing', [])
    tickets = doc.get('tickets', {})
    products_data = tickets.get('products', {})
    last_update = tickets.get('lastUpdate')

    if not public_days or not ticketing_config:
        return jsonify({"days": [], "total_ventes": None, "last_update": last_update})

    # Parser la date de course courante
    race_date = _param_race_date(doc)

    # ── Charger parametrages N-1 (ventes precedentes) ──
    prev_year_str = None
    prev_param = None
    prev_race_date = None
    prev_products_data = {}
    prev_ticketing_config = []
    prev_public_days = []
    current_year_int = int(year) if year.isdigit() else None

    if current_year_int:
        # Chercher le parametrage de l'annee precedente la plus recente.
        # parkings/campings sont projetes : sans eux le bloc Sites n'a aucun N-1.
        prev_candidates = list(db['parametrages'].find(
            {'event': event, 'tickets': {'$exists': True}},
            {'year': 1, 'data.globalHoraires': 1, 'data.race': 1,
             'data.parkingsHoraires': 1, 'data.campingsHoraires': 1,
             'tickets': 1, '_id': 0}
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
        prev_race_date = _param_race_date(prev_param)

    # ── Charger historique_controle N-1 (pic presents) ──
    prev_hist_race_date = None
    prev_data_by_day = {}
    if race_date and current_year_int:
        hist_aliases = _event_hist_aliases(event)
        prev_hist_candidates = list(db['historique_controle'].find(
            {'type': 'frequentation', 'event': {'$in': hist_aliases}},
            sort=[('year', -1)]
        ))
        for cand in prev_hist_candidates:
            cand_year = cand.get('year')
            if isinstance(cand_year, (int, float)) and int(cand_year) < current_year_int:
                prev_hist_year = cand_year
                prev_hist_race_raw = cand.get('race')
                if not prev_hist_race_raw:
                    portes_doc = db['historique_controle'].find_one(
                        {'type': 'portes', 'event': {'$in': hist_aliases}, 'year': prev_hist_year},
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
        # Pic N-1 : serie au quart d'heure (presents_etat.historique_n1), la meme
        # que la case << Meme jour N-1 >> de general-stats et le rapport matinal.
        # La serie horaire ratait le vrai pic (51 889 contre 52 520 sur
        # 24H CAMIONS 2025, samedi).
        if prev_hist_race_date:
            n1 = presents_etat.historique_n1(db, event, current_year_int, hist_aliases)
            if n1 and n1.get('par_jour'):
                prev_data_by_day = n1['par_jour']

    # Reference unifiee de la date de course N-1 : privilegier historique_controle
    # (via doc portes, fiable) sur parametrages.data.race (parfois errone).
    prev_race_ref = prev_hist_race_date or prev_race_date

    # Dates de course fiables de toutes les editions passees, pour caler les
    # courbes de reference au-dela de N-1.
    hist_race_by_year = {}
    if current_year_int:
        for h in db['historique_controle'].find(
            {'type': {'$in': ['portes', 'frequentation']},
             'event': {'$in': _event_hist_aliases(event)}},
            {'_id': 0, 'year': 1, 'race': 1}
        ):
            hy, hr = h.get('year'), h.get('race')
            if isinstance(hy, (int, float)) and hr and int(hy) not in hist_race_by_year:
                rd = _parse_race_date(hr)
                if rd:
                    hist_race_by_year[int(hy)] = rd

    # ── Paniers "enceinte generale" (produits references dans ticketing) ──
    # day_ventes est multi-compte (un billet week-end compte sur chaque jour)
    # pour refleter la presence attendue par jour ; les totaux ne doivent PAS
    # agreger cela, d'ou l'ensemble unique.
    referenced_products = _ticketing_product_names(doc)
    prev_referenced = _ticketing_product_names(prev_param) if prev_param else set()

    days_before = None
    if race_date and last_update:
        try:
            last_dt = datetime.strptime(last_update, '%Y-%m-%d').date()
        except ValueError:
            last_dt = None
        if last_dt:
            days_before = (race_date - last_dt).days

    # ── Taux de remplissage de chaque edition de reference ──
    # Une edition de reference fournit, a days_before, la part de son total final
    # deja vendue — globalement et par groupe de jours. Les courbes sont
    # restreintes au panier ticketing : y laisser les campings (satures des
    # janvier) ou les agregats TOTAL_* du referentiel fausserait le taux.
    MAX_REFERENCE_EDITIONS = 3
    reference_rates = []
    if days_before is not None and current_year_int:
        for cand in sorted(prev_candidates, key=lambda c: str(c.get('year', '')), reverse=True):
            try:
                cy = int(cand.get('year', ''))
            except (ValueError, TypeError):
                continue
            if cy >= current_year_int:
                continue
            race_ref = hist_race_by_year.get(cy) or _param_race_date(cand)
            pts, final, _ = _fill_curve(cand, race_date_override=race_ref,
                                        product_names=_ticketing_product_names(cand))
            g_pct = _interpolate_pct(pts, final, days_before)
            g_rate = g_pct / 100 if g_pct and g_pct > 0 else None
            groups = _group_fill_rates(cand, race_ref, days_before)
            if g_rate or groups:
                reference_rates.append({'year': cy, 'global': g_rate, 'groups': groups})
                if len(reference_rates) >= MAX_REFERENCE_EDITIONS:
                    break

    total_ventes = sum(
        products_data.get(p, {}).get('ventes', 0) for p in referenced_products
    )
    total_delta = 0
    for pname in referenced_products:
        pdata = products_data.get(pname, {})
        hist = pdata.get('history', [])
        if len(hist) >= 2:
            total_delta += pdata.get('ventes', 0) - hist[-2].get('ventes', 0)

    # Final N-1 : le panier au soir de la course precedente. Sert de socle aux
    # projections (une projection est un total final, elle se compare a un final).
    total_ventes_prev_final = sum(
        prev_products_data.get(p, {}).get('ventes', 0) for p in prev_referenced
    )

    # ── N-1 au meme avancement ──
    # C'est LA reference de comparaison : le meme panier, lu au meme nombre de
    # jours avant la course. Comparer les ventes N en cours au final N-1 ferait
    # lire un effondrement de -78 % en pleine saison alors qu'on est a parite.
    def _prev_curve(product_names):
        """(points, final) de la courbe N-1 restreinte a un panier de produits."""
        if not product_names or not prev_race_ref:
            return [], 0
        prods = {p: prev_products_data[p] for p in product_names if p in prev_products_data}
        points = _curve_points(prods, prev_race_ref)
        if not points:
            return [], 0
        final = max(points[-1][1], sum((prods[p] or {}).get('ventes', 0) for p in prods))
        return points, final

    def _prev_at_same_stage(product_names):
        if days_before is None:
            return None
        points, _ = _prev_curve(product_names)
        v = _interpolate_value(points, days_before)
        return None if v is None else int(round(v))

    def _prev_fill_ratio(product_names):
        """Part du final N-1 deja atteinte a days_before, pour ce panier."""
        if days_before is None:
            return None
        points, final = _prev_curve(product_names)
        pct = _interpolate_pct(points, final, days_before)
        return pct / 100 if pct and pct > 0 else None

    total_ventes_prev = _prev_at_same_stage(prev_referenced)
    prev_reference_date = None
    if prev_race_ref and days_before is not None:
        prev_reference_date = (prev_race_ref - timedelta(days=days_before)).strftime('%Y-%m-%d')

    # Croissance reelle N/N-1 a avancement egal (ex: 0,97 = 3 % sous la saison
    # precedente au meme stade). Appliquee au final N-1, elle donne une
    # projection independante de la forme des courbes.
    delta_n_vs_n1 = None
    if total_ventes_prev and total_ventes_prev > 0:
        delta_n_vs_n1 = total_ventes / total_ventes_prev

    # ── Projection ──
    # Une projection par edition de reference, chacune ventilee par groupe de
    # jours, plus une projection "croissance". La valeur centrale est la moyenne
    # ponderee des editions (N-1 pese le double de N-2) ; la fourchette est le
    # min/max de TOUTES les projections, y compris celle par croissance.
    #
    # Moyenner les editions en une seule fourchette etroite masquait leur
    # desaccord : sur 24H CAMIONS 2026 a J-50, 2025 projette 52 637 et 2024
    # 67 539 pour des finals quasi identiques (2024 vendait simplement plus
    # tard). L'ecart entre editions EST l'incertitude, il doit se voir.
    groups_n = _ticketing_day_groups(doc, race_date)
    ventes_by_sig = {}
    for sig, names in groups_n.items():
        ventes_by_sig[sig] = sum(products_data.get(p, {}).get('ventes', 0) for p in names)

    def _project_on(ventes_map, ref):
        """Projection d'un panier ventile par signature sur une edition de ref.

        Un groupe absent de l'edition de reference (billet cree cette annee)
        retombe sur le taux global de cette edition.
        """
        total = 0.0
        for sig, v in ventes_map.items():
            rate = ref['groups'].get(sig) or ref['global']
            if not rate:
                return None
            total += v / rate
        return int(round(total)) if total else None

    def _growth_projection(ventes_now, prev_final):
        if not prev_final or not delta_n_vs_n1:
            return None
        return max(ventes_now, int(round(prev_final * delta_n_vs_n1)))

    def _assemble(ventes_map, ventes_now, prev_final):
        """(central, low, high, [{source, value}]) pour un panier donne."""
        per_edition = []
        for ref in reference_rates:
            v = _project_on(ventes_map, ref)
            if v:
                per_edition.append({'source': str(ref['year']), 'value': v})
        detail = list(per_edition)
        growth = _growth_projection(ventes_now, prev_final)
        if growth:
            detail.append({'source': 'croissance', 'value': growth})
        if not detail:
            return None, None, None, []
        if per_edition:
            weights = _reference_weights(len(per_edition))
            central = int(round(sum(w * p['value'] for w, p in zip(weights, per_edition))))
        else:
            central = growth
        values = [p['value'] for p in detail]
        return central, min(values), max(values), detail

    total_projection, total_projection_low, total_projection_high, projection_refs = _assemble(
        ventes_by_sig, total_ventes, total_ventes_prev_final
    )

    # Dispersion entre methodes : dit si la projection est solide (editions
    # d'accord) ou fragile. Sans elle, une fourchette large se lit comme une
    # precision, pas comme un doute.
    projection_spread_pct = None
    if total_projection and total_projection_low and total_projection_high:
        projection_spread_pct = int(round(
            100 * (total_projection_high - total_projection_low) / total_projection
        ))

    # Ratio implicite, encore utilise comme repli pour les sites sans historique.
    projection_ratio = (total_ventes / total_projection) if total_projection else None

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

        # Produits N-1 du jour equivalent (meme offset depuis la course).
        # Un jour public N est rapproche du jour public N-1 de meme rang, pas de
        # la meme date calendaire : les editions ne tombent pas au meme jour.
        prev_day_products = set()
        if race_date and prev_race_ref and prev_ticketing_config:
            offset_days = (day_date - race_date).days
            target_prev_str = (prev_race_ref + timedelta(days=offset_days)).strftime('%Y-%m-%d')
            for tc in prev_ticketing_config:
                days_scope = tc.get('days', [])
                applies = (days_scope == 'all') or (target_prev_str in days_scope)
                if not applies:
                    continue
                for pname in tc.get('products', []):
                    if pname in prev_products_data:
                        prev_day_products.add(pname)

        # Final N-1 de ce jour (socle des projections) et N-1 au meme avancement
        # (reference de comparaison affichee).
        ventes_prev_final = None
        ventes_prev = None
        if prev_day_products:
            ventes_prev_final = sum(
                prev_products_data.get(p, {}).get('ventes', 0) for p in prev_day_products
            )
            ventes_prev = _prev_at_same_stage(prev_day_products)

        # Projection ventes du jour : meme assemblage que le total, mais sur les
        # seuls groupes de billets valables ce jour-la. C'est ce qui distingue le
        # samedi du dimanche : les billets a la journee se vendent nettement plus
        # tard que les week-end, un taux global unique les confondrait.
        day_offset = (day_date - race_date).days if race_date else None
        day_by_sig = {}
        if day_offset is not None:
            for sig, v in ventes_by_sig.items():
                if sig == 'all' or day_offset in sig:
                    day_by_sig[sig] = v
        day_projection, day_projection_low, day_projection_high, day_projection_refs = _assemble(
            day_by_sig, day_ventes, ventes_prev_final
        )

        # Pic N-1 et Pic projete depuis historique_controle (fourchette aussi)
        pic_prev = None
        pic_projection = None
        pic_projection_low = None
        pic_projection_high = None
        if race_date and prev_race_ref:
            offset_days = (day_date - race_date).days
            target_prev_date = prev_race_ref + timedelta(days=offset_days)
            target_key = target_prev_date.strftime('%Y-%m-%d')
            if target_key in prev_data_by_day:
                records = prev_data_by_day[target_key]
                pic_prev = max((r.get('present', 0) for r in records), default=0)
                # Pic projete = projection_ventes * (pic_prev / ventes_prev_final)
                # Le ratio pic/ventes de N-1 capture les enfants gratuits + accredites.
                # Il se calcule sur le FINAL N-1 : une projection est un total de
                # fin de saison, la rapporter au N-1 a mi-saison la ferait exploser.
                if pic_prev and ventes_prev_final and ventes_prev_final > 0:
                    pic_ratio = pic_prev / ventes_prev_final
                    if day_projection:
                        pic_projection = round(day_projection * pic_ratio)
                    if day_projection_low:
                        pic_projection_low = round(day_projection_low * pic_ratio)
                    if day_projection_high:
                        pic_projection_high = round(day_projection_high * pic_ratio)

        result_days.append({
            "date": day_str,
            "label": label,
            "ventes": day_ventes,
            "delta": day_delta,
            "ventes_prev": ventes_prev,
            "ventes_prev_final": ventes_prev_final,
            "projection": day_projection,
            "projection_low": day_projection_low,
            "projection_high": day_projection_high,
            "projection_refs": day_projection_refs,
            "pic_prev": pic_prev,
            "pic_projection": pic_projection,
            "pic_projection_low": pic_projection_low,
            "pic_projection_high": pic_projection_high,
            "prev_year": prev_year_str
        })

    # ── Sites (parkings + campings avec ticketing) ──
    # Les sites vivent hors du panier enceinte generale (ticketing propre a chaque
    # parking/camping). On garde le rapprochement par nom, avec les deux N-1 :
    # final (comparable a une projection) et meme avancement (comparable aux
    # ventes en cours).
    prev_site_products = {}
    if prev_param:
        prev_data = prev_param.get('data', {})
        for sk in ('parkingsHoraires', 'campingsHoraires'):
            for ps in prev_data.get(sk, []):
                ptk = ps.get('ticketing', [])
                if not ptk:
                    continue
                names = {t.get('product', '') for t in ptk if t.get('product')}
                prev_site_products[ps.get('name', '')] = names

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
            prev_names = prev_site_products.get(site_name)
            site_ventes_prev_final = None
            site_ventes_prev = None
            site_ratio = None
            if prev_names:
                site_ventes_prev_final = sum(
                    prev_products_data.get(p, {}).get('ventes', 0) for p in prev_names
                )
                site_ventes_prev = _prev_at_same_stage(prev_names)
                site_ratio = _prev_fill_ratio(prev_names)
            # Chaque site est projete sur SA courbe N-1 : les campings sont
            # quasi pleins des le printemps, leur appliquer le taux de
            # remplissage des entrees (encore a un quart en juillet) quadruplerait
            # une jauge deja atteinte. Repli sur le ratio global si pas d'N-1.
            ratio = site_ratio or projection_ratio
            site_projection = round(site_ventes / ratio) if ratio else None
            sites.append({
                'name': site_name,
                'capacite': site.get('capacite') or site.get('capacite_theorique') or 0,
                'ventes': site_ventes,
                'ventes_prev': site_ventes_prev,
                'ventes_prev_final': site_ventes_prev_final,
                'projection': site_projection,
            })

    return jsonify({
        "days": result_days,
        "total_ventes": total_ventes,
        "total_delta": total_delta,
        "total_ventes_prev": total_ventes_prev if total_ventes_prev else None,
        "total_ventes_prev_final": total_ventes_prev_final if total_ventes_prev_final else None,
        "total_projection": total_projection,
        "total_projection_low": total_projection_low,
        "total_projection_high": total_projection_high,
        "projection_refs": projection_refs,
        "projection_spread_pct": projection_spread_pct,
        "last_update": last_update,
        "prev_year": prev_year_str,
        "prev_reference_date": prev_reference_date,
        "days_before": days_before,
        "sites": sites
    })


@app.route('/get_affluence_hourly', methods=['GET'])
@role_required("user")
@block_required("widget-right-2")
def get_affluence_hourly():
    """Courbe horaire des presents pour un jour donne :
    - n  : presents annee en cours (historique_controle{year=N} si dispo,
           sinon data_access bucketise par tranches de 15 min).
    - n1 : presents annee precedente (jour-equivalent aligne sur la course).
    Sert au grand panneau Affluence (section "Vue du jour selectionne").
    """
    event = request.args.get("event")
    year = request.args.get("year")
    date_str = request.args.get("date")
    if not event or not year or not date_str:
        return jsonify({"error": "Missing event, year or date"}), 400
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        year_int = int(year)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid date or year"}), 400

    # Date de course N (priorite parametrages.data.race -> globalHoraires.race)
    doc = db['parametrages'].find_one({'event': event, 'year': year}, {'_id': 0})
    race_date = None
    if doc:
        race_raw = (doc.get('data') or {}).get('race') or \
                   ((doc.get('data') or {}).get('globalHoraires') or {}).get('race')
        race_date = _parse_race_date(race_raw)

    # N-1 : doc frequentation + race date alignee
    hist_prev_doc, hist_prev_race_date = pcorg_summary._find_hist_freq_prev(db, event, year_int)
    prev_race_date = hist_prev_race_date
    if not prev_race_date:
        # Fallback : parametrages N-1
        prev_param = pcorg_summary._find_prev_param(db, event, year_int)
        if prev_param:
            prev_race_raw = (prev_param.get('data') or {}).get('race') or \
                            ((prev_param.get('data') or {}).get('globalHoraires') or {}).get('race')
            prev_race_date = _parse_race_date(prev_race_raw)

    prev_aligned = None
    if race_date and prev_race_date:
        offset_days = (target_date - race_date).days
        prev_aligned = prev_race_date + timedelta(days=offset_days)

    # N-1 hourly : trie par heure
    n1_series = []
    pic_prev = None
    pic_prev_hour = None
    if hist_prev_doc and prev_aligned:
        freq_by_day = pcorg_summary._index_freq_by_day(hist_prev_doc)
        records = freq_by_day.get(prev_aligned.strftime('%Y-%m-%d'), [])
        tmp = []
        for r in records:
            try:
                p = int(r.get('present') or 0)
            except (ValueError, TypeError):
                continue
            h = pcorg_summary._record_hour_str(r)  # 'HHhMM' ou None
            if not h:
                continue
            hh_mm = h.replace('h', ':')
            tmp.append((hh_mm, p))
        tmp.sort(key=lambda x: x[0])
        n1_series = [{"hour": hm, "present": p} for hm, p in tmp]
        if tmp:
            best = max(tmp, key=lambda x: x[1])
            pic_prev = best[1]
            pic_prev_hour = best[0].replace(':', 'h')

    # N hourly : historique_controle{year=N} en priorite, sinon data_access bucketise
    n_series = []
    hist_n = pcorg_summary._find_hist_freq(db, event, year_int)
    if hist_n:
        freq_n = pcorg_summary._index_freq_by_day(hist_n)
        records = freq_n.get(target_date.strftime('%Y-%m-%d'), [])
        tmp = []
        for r in records:
            try:
                p = int(r.get('present') or 0)
            except (ValueError, TypeError):
                continue
            rd = r.get('date')
            ts = None
            if isinstance(rd, str) and len(rd) >= 16:
                ts = rd
            elif hasattr(rd, 'isoformat'):
                ts = rd.isoformat()
            elif r.get('hour'):
                ts = target_date.strftime('%Y-%m-%d') + 'T' + str(r.get('hour'))
            if ts:
                tmp.append((ts, p))
        tmp.sort(key=lambda x: x[0])
        n_series = [{"ts": ts, "present": p} for ts, p in tmp]

    if not n_series and target_date <= datetime.now(ZoneInfo("Europe/Paris")).date():
        # Fallback data_access pour le jour en cours / la veille recente
        main_loc_id = pcorg_summary._get_main_counter_id(db)
        if main_loc_id:
            tz_paris = ZoneInfo("Europe/Paris")
            day_start_paris = datetime.combine(target_date, datetime.min.time(), tzinfo=tz_paris)
            day_end_paris = day_start_paris + timedelta(days=1)
            day_start_utc = day_start_paris.astimezone(timezone.utc)
            day_end_utc = day_end_paris.astimezone(timezone.utc)
            q = {
                "timestamp": {"$gte": day_start_utc, "$lt": day_end_utc},
                "_id": {"$ne": "___GLOBAL___"},
                "requested_location_id": str(main_loc_id),
                "$or": [
                    {"requested_event": event},
                    {"requested_event": {"$exists": False}},
                ],
            }
            # Bucket par tranches de 15 minutes : on garde le max(current) du bucket.
            buckets = {}
            try:
                for s in db['data_access'].find(q, {"current": 1, "timestamp": 1}):
                    v = s.get('current')
                    try:
                        vi = int(v) if v not in (None, "") else None
                    except (ValueError, TypeError):
                        continue
                    if vi is None:
                        continue
                    ts = s.get('timestamp')
                    if not isinstance(ts, datetime):
                        continue
                    local = (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(tz_paris)
                    bucket_min = (local.minute // 15) * 15
                    bucket = local.replace(minute=bucket_min, second=0, microsecond=0)
                    key = bucket.isoformat()
                    if key not in buckets or vi > buckets[key]:
                        buckets[key] = vi
            except Exception as e:
                logging.warning("get_affluence_hourly data_access fallback failed: %s", e)
            n_series = [{"ts": k, "present": v} for k, v in sorted(buckets.items())]

    return jsonify({
        "date": date_str,
        "race_date": race_date.isoformat() if race_date else None,
        "prev_date": prev_aligned.isoformat() if prev_aligned else None,
        "prev_race_date": prev_race_date.isoformat() if prev_race_date else None,
        "n": n_series,
        "n1": n1_series,
        "pic_prev": pic_prev,
        "pic_prev_hour": pic_prev_hour,
    })


@app.route('/get_affluence_curves', methods=['GET'])
@role_required("user")
@block_required("widget-right-2")
def get_affluence_curves():
    """Courbes de remplissage (ventes cumulees) par 'jours avant course'
    pour N, N-1 et N-2. Sert au grand panneau Affluence pour visualiser
    si la saison N est en avance/retard de remplissage par rapport aux
    editions precedentes.
    """
    event = request.args.get("event")
    year = request.args.get("year")
    if not event or not year:
        return jsonify({"error": "Missing event or year"}), 400
    try:
        year_int = int(year)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid year"}), 400

    # N : parametrages courants
    doc_n = db['parametrages'].find_one({'event': event, 'year': year}, {'_id': 0})
    points_n, final_n, race_n = ([], 0, None)
    if doc_n:
        points_n, final_n, race_n = _fill_curve(doc_n, product_names=_ticketing_product_names(doc_n))

    # N-1 et N-2 : on cherche les 2 plus recentes editions < year_int avec tickets
    candidates = list(db['parametrages'].find(
        {'event': event, 'tickets': {'$exists': True}},
        {'year': 1, 'data.globalHoraires': 1, 'data.race': 1, 'tickets': 1, '_id': 0}
    ))
    prev_sorted = []
    for cand in sorted(candidates, key=lambda c: str(c.get('year', '')), reverse=True):
        try:
            cy = int(cand.get('year', ''))
        except (ValueError, TypeError):
            continue
        if cy < year_int:
            prev_sorted.append((cy, cand))

    prev_n_minus_1 = prev_sorted[0] if len(prev_sorted) >= 1 else None
    prev_n_minus_2 = prev_sorted[1] if len(prev_sorted) >= 2 else None

    def _serialize(curve_points):
        # Trie ascendant sur d_before (du plus eloigne vers J-0)
        return [{"d_before": d, "ventes": v}
                for d, v in sorted(curve_points, key=lambda x: -x[0])]

    out_n = _serialize(points_n)
    out_n1, final_n1, year_n1 = [], None, None
    if prev_n_minus_1:
        year_n1, cand1 = prev_n_minus_1
        pts1, final1, _ = _fill_curve(cand1, product_names=_ticketing_product_names(cand1))
        out_n1 = _serialize(pts1)
        final_n1 = final1
    out_n2, final_n2, year_n2 = [], None, None
    if prev_n_minus_2:
        year_n2, cand2 = prev_n_minus_2
        pts2, final2, _ = _fill_curve(cand2, product_names=_ticketing_product_names(cand2))
        out_n2 = _serialize(pts2)
        final_n2 = final2

    # Position de N au jour de la derniere maj (J - last_update)
    last_update = (doc_n or {}).get('tickets', {}).get('lastUpdate') if doc_n else None
    days_before_now = None
    if race_n and last_update:
        try:
            last_dt = datetime.strptime(last_update, '%Y-%m-%d').date()
            days_before_now = (race_n - last_dt).days
        except ValueError:
            pass

    return jsonify({
        "year_n": year_int,
        "year_n1": year_n1,
        "year_n2": year_n2,
        "race_n": race_n.isoformat() if race_n else None,
        "n": out_n,
        "n_minus_1": out_n1,
        "n_minus_2": out_n2,
        "final_n_minus_1": final_n1,
        "final_n_minus_2": final_n2,
        "days_before_now": days_before_now,
        "last_update": last_update,
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

# ---------------------------------------------------------------------------
# Terrains (parkings / campings) — taux de remplissage vs ventes + jauge live
# ---------------------------------------------------------------------------
# Association terrain (parametrages) <-> compteur live-access (locations_selectionnees).
# Auto-match par nom normalise, override manuel persiste dans terrain_location_mapping.
COL_TERRAIN_MAP = db['terrain_location_mapping']  # 1 doc / (event, year), champ mappings

_TERRAIN_STOPWORDS = {"PARKING", "PARK", "CAMPING", "AA", "AIRE", "ACCUEIL", "P", "PK", "CP"}

def _norm_terrain_name(s):
    """Normalise un nom de terrain/location pour le matching (sans accents/casse/bruit)."""
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode('ascii').upper()
    s = re.sub(r'[^A-Z0-9]+', ' ', s)
    toks = [t for t in s.split() if t and t not in _TERRAIN_STOPWORDS]
    return ' '.join(toks)

def _terrain_base_name(name):
    """Nom de base d'un terrain en retirant le numero de parcelle final.
    'PRAIRIE 1' -> 'PRAIRIE', 'MULSANNE 12' -> 'MULSANNE', 'EXPO AUTOS' inchange."""
    s = (name or '').strip()
    s2 = re.sub(r'\s*\d+\s*$', '', s).strip()
    return s2 or s

def _auto_match_location(terrain_name, locations_norm):
    """locations_norm: liste de (loc, normname). Retourne la meilleure loc ou None."""
    tn = _norm_terrain_name(terrain_name)
    if not tn:
        return None
    tset = set(tn.split())
    best, best_score = None, 0.0
    for loc, ln in locations_norm:
        if not ln:
            continue
        if ln == tn:
            return loc  # match exact
        lset = set(ln.split())
        if not lset:
            continue
        inter = tset & lset
        union = tset | lset
        jacc = len(inter) / len(union) if union else 0.0
        contained = (tn in ln) or (ln in tn)
        score = jacc + (0.5 if contained else 0.0)
        if score > best_score:
            best_score, best = score, loc
    return best if best_score >= 0.5 else None


@app.route('/terrains', methods=['GET'])
@role_required("user")
def parkings():
    event = request.args.get('event')
    year = request.args.get('year')
    if not event or not year:
        return "Missing event or year parameter", 400
    payload = getattr(request, 'user_payload', {})
    is_admin = (payload.get("app_role") == "admin") or payload.get("is_super_admin") \
               or ("admin" in (payload.get("roles", []) or []))
    return render_template("terrains.html", event=event, year=year,
                           is_admin=bool(is_admin),
                           user_firstname=payload.get("firstname", ""),
                           user_lastname=payload.get("lastname", ""),
                           user_email=payload.get("email", ""))


@app.route('/api/terrains/fill', methods=['GET'])
@role_required("user")
def api_terrains_fill():
    """Taux de remplissage par terrain : presents live (jauge live-access) vs
    billets vendus (primaire) et vs capacite (secondaire)."""
    event = request.args.get('event')
    year = request.args.get('year')
    if not event or not year:
        return jsonify({"error": "Missing event or year"}), 400

    param = db['parametrages'].find_one({'event': event, 'year': year}, {'_id': 0}) or {}
    data = param.get('data', {}) or {}
    products = (param.get('tickets', {}) or {}).get('products', {}) or {}
    last_update = (param.get('tickets', {}) or {}).get('lastUpdate')

    global_doc = _hsh_read_global()
    locations = global_doc.get('locations_selectionnees', []) or []
    corrections = global_doc.get('corrections_compteurs', {}) or {}

    # Dernier compteur live par location activee
    counters = {}
    for loc in locations:
        lid = str(loc.get('id'))
        c = db['data_access'].find_one(
            {'requested_location_id': lid, 'requested_location_type': loc.get('type')},
            sort=[('timestamp', -1)],
            projection={'_id': 0, 'current': 1, 'upper_limit': 1, 'timestamp': 1}
        )
        counters[lid] = c

    locations_norm = [(loc, _norm_terrain_name(loc.get('name', ''))) for loc in locations]
    map_doc = COL_TERRAIN_MAP.find_one({'event': event, 'year': str(year)}) or {}
    mappings = map_doc.get('mappings', {}) or {}

    def _present_for_loc(loc):
        """(present, upper_limit, ts) du compteur d'une location, ou (None,None,None)."""
        if not loc:
            return None, None, None
        c = counters.get(str(loc.get('id')))
        if not c:
            return None, None, None
        corr = int(corrections.get(str(loc.get('id')), 0) or 0)
        present = max(int(c.get('current', 0) or 0) - corr, 0)
        cts = c.get('timestamp')
        ts = (cts.isoformat() + 'Z') if hasattr(cts, 'isoformat') else None
        return present, c.get('upper_limit'), ts

    terrains_out = []   # par parcelle -> alimente la modale d'association
    groups = {}         # cle compteur (ou nom de base) -> agregat -> alimente le board

    for kind, key in (("parking", "parkingsHoraires"), ("camping", "campingsHoraires")):
        for t in data.get(key, []):
            tk = t.get('ticketing', []) or []
            if not tk:
                continue
            tid = str(t.get('id') or t.get('name'))
            name = t.get('name', '?')
            prod_names = [x.get('product') for x in tk if x.get('product')]
            ventes = sum(products.get(p, {}).get('ventes', 0) for p in prod_names)
            capacite = t.get('capacite') or t.get('capacite_theorique') or 0

            # Association : manuel d'abord, sinon auto (sauf si explicitement vide)
            loc = None
            source = None
            manual = mappings.get(tid)
            if manual and manual.get('location_id'):
                loc = next((l for l in locations if str(l.get('id')) == str(manual['location_id'])), None)
                if loc:
                    source = 'manual'
            if manual and manual.get('cleared'):
                source = 'none'                              # association volontairement vide
            elif loc is None:
                loc = _auto_match_location(name, locations_norm)
                if loc:
                    source = 'auto'

            present_t, _u, _ts = _present_for_loc(loc)
            terrains_out.append({
                'id': tid, 'name': name, 'type': kind,
                'capacite': capacite or None, 'products': prod_names, 'ventes': ventes,
                'location': ({'id': str(loc.get('id')), 'type': loc.get('type'),
                              'name': loc.get('name')} if loc else None),
                'match_source': source,
                'present': present_t,
            })

            # Agregation : par compteur si associe (fusionne PRAIRIE 1/2/3 -> compteur
            # PRAIRIE), sinon par nom de base (numero de parcelle retire).
            if loc:
                gkey = 'loc::' + str(loc.get('id'))
                gname = loc.get('name')
            else:
                gkey = 'na::' + kind + '::' + _terrain_base_name(name).upper()
                gname = _terrain_base_name(name)
            g = groups.get(gkey)
            if not g:
                g = {'key': gkey, 'name': gname, 'type': kind,
                     'location': ({'id': str(loc.get('id')), 'type': loc.get('type'),
                                   'name': loc.get('name')} if loc else None),
                     'members': [], 'ventes': 0, 'capacite': 0, 'sources': set()}
                groups[gkey] = g
            g['members'].append({'name': name, 'products': prod_names, 'ventes': ventes})
            g['ventes'] += ventes
            g['capacite'] += (capacite or 0)
            if source:
                g['sources'].add(source)

    groups_out = []
    for g in groups.values():
        loc = g['location']
        present, upper, ts = _present_for_loc(loc) if loc else (None, None, None)
        cap = g['capacite'] or None
        if loc:
            gsrc = 'manual' if 'manual' in g['sources'] else 'auto'
        else:
            gsrc = 'none' if (g['sources'] and g['sources'] <= {'none'}) else None
        taux_vendus = round(present / g['ventes'] * 100) if (present is not None and g['ventes']) else None
        taux_cap = round(present / cap * 100) if (present is not None and cap) else None
        groups_out.append({
            'key': g['key'], 'name': g['name'], 'type': g['type'],
            'location': loc, 'match_source': gsrc,
            'members': g['members'], 'members_count': len(g['members']),
            'ventes': g['ventes'], 'capacite': cap,
            'present': present, 'upper_limit': upper,
            'taux_vendus': taux_vendus, 'taux_capacite': taux_cap,
            'timestamp': ts,
        })
    # associes (avec taux) en premier, du plus rempli au moins, puis non associes
    groups_out.sort(key=lambda x: (x['taux_vendus'] is None, -(x['taux_vendus'] or 0), x['name']))

    return jsonify({
        'event': event, 'year': year,
        'groups': groups_out,
        'terrains': terrains_out,
        'locations': [{'id': str(l.get('id')), 'type': l.get('type'),
                       'name': l.get('name')} for l in locations],
        'last_update_tickets': last_update,
    })


@app.route('/api/terrains/mapping', methods=['POST'])
@role_required("admin")
def api_terrains_mapping():
    """Override manuel de l'association terrain <-> compteur (admin).
    Body: {event, year, terrain_id, mode: 'auto'|'none'|'manual', location_id?, location_type?}."""
    body = request.get_json(silent=True) or {}
    event = body.get('event')
    year = body.get('year')
    terrain_id = body.get('terrain_id')
    mode = body.get('mode', 'manual')
    if not event or not year or not terrain_id:
        return jsonify({"error": "Missing event, year or terrain_id"}), 400

    doc = COL_TERRAIN_MAP.find_one({'event': event, 'year': str(year)}) \
        or {'event': event, 'year': str(year), 'mappings': {}}
    mappings = doc.get('mappings', {}) or {}

    if mode == 'auto':
        mappings.pop(terrain_id, None)                       # revient a l'auto-match
    elif mode == 'none':
        mappings[terrain_id] = {'cleared': True}             # aucune association
    else:
        location_id = body.get('location_id')
        if not location_id:
            return jsonify({"error": "Missing location_id"}), 400
        mappings[terrain_id] = {
            'location_id': str(location_id),
            'location_type': body.get('location_type'),
        }

    COL_TERRAIN_MAP.update_one(
        {'event': event, 'year': str(year)},
        {'$set': {'mappings': mappings, 'updated_at': datetime.now(timezone.utc)}},
        upsert=True
    )
    return jsonify({"ok": True, "mode": mode, "terrain_id": terrain_id})

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

_VALID_PHASES = {"open", "close", "both", "switch_control", "switch_free"}
_VALID_CONTROLS = {"controle", "libre", "both"}

def _normalize_todos(raw_todos):
    """Normalise les todos en [{text, phase, control}]. Accepte l'ancien format [str].

    'control' ('controle'|'libre'|'both') cible une tache selon le mode d'acces
    de l'item a l'ouverture/fermeture. Defaut 'both' (s'applique aux deux modes).
    """
    result = []
    for item in (raw_todos or []):
        if isinstance(item, str):
            text = item.strip()
            if text:
                result.append({"text": text, "phase": "open", "control": "both"})
        elif isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            phase = item.get("phase", "both")
            if phase not in _VALID_PHASES:
                phase = "both"
            control = item.get("control", "both")
            if control not in _VALID_CONTROLS:
                control = "both"
            if text:
                result.append({"text": text, "phase": phase, "control": control})
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
    allowed = get_user_display_blocks(request.user_payload)
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

@app.route('/api/page-registry', methods=['GET'])
@role_required("admin")
def get_page_registry():
    """Pages de la barre laterale autorisables par groupe (fiche groupe) :
    pages courantes (role user/manager) puis pages d'administration (role
    "admin", accord explicite)."""
    pages = [{k: p[k] for k in ("id", "label", "icon", "role")} for p in PAGE_REGISTRY]
    pages += [{"id": p["id"], "label": p["label"], "icon": p["icon"], "role": "admin"}
              for p in ADMIN_PAGE_REGISTRY]
    return jsonify(pages)

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
    metas = _alert_def_metas()
    out = []
    for a in alerts:
        a = _pub(a)
        a['meta'] = metas.get(a.get('type'))
        out.append(a)
    return jsonify(out)

@app.route('/api/alert-history', methods=['POST'])
@role_required("user")
@csrf.exempt
def post_alert_history():
    # L'historique est ecrit par le moteur (alert_engine.sync_alert_history),
    # une entree par alerte, qu'un poste soit ouvert ou non. Avant, chaque
    # poste postait sa copie et le serveur dedoublonnait sur le texte : aucun
    # poste ouvert = aucun historique, un message reformule = un doublon.
    # Route conservee en no-op pour un onglet reste sur l'ancien JS.
    return jsonify({"ok": True, "ignored": True}), 200

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

@app.route('/api/cockpit-users/names', methods=['GET'])
@role_required("user")
def list_cockpit_users_names():
    """Liste minimaliste des utilisateurs Cockpit (prenom + nom).
    Sert d'autocomplete pour la source 'Operateur' / 'Hierarchie' des fiches PCO.
    """
    users = db['users'].find(
        {"roles_by_app.cockpit": {"$exists": True}},
        {"prenom": 1, "nom": 1}
    )
    seen = set()
    result = []
    for u in users:
        prenom = (u.get("prenom") or "").strip()
        nom = (u.get("nom") or "").strip()
        if not prenom and not nom:
            continue
        full = (prenom + " " + nom).strip()
        if full in seen:
            continue
        seen.add(full)
        result.append({"name": full})
    result.sort(key=lambda x: x["name"].lower())
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
        'saison_only_blocks': _parse_saison_only_blocks(data.get('saison_only_blocks')),
        'fiche_simplifiee': bool(data.get('fiche_simplifiee', False)),
        'can_close_fiche': bool(data.get('can_close_fiche', False)),
        'dispatch_manager': bool(data.get('dispatch_manager', False)),
        'fiche_lecture_seule': bool(data.get('fiche_lecture_seule', False)),
        'can_create_fiche': bool(data.get('can_create_fiche', False)),
        'can_convert_declaration': bool(data.get('can_convert_declaration', False)),
        'alfred_chat': bool(data.get('alfred_chat', False)),
        'allowed_pages': _parse_allowed_pages(data.get('allowed_pages')),
        'admin_pages': _parse_admin_pages(data.get('admin_pages')),
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
    # Groupe __admin__ : blocs et disposition = preferences d'AFFICHAGE des
    # admins (get_user_display_blocks), sans effet sur leurs droits.
    if 'allowed_blocks' in data:
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
    if 'saison_only_blocks' in data:
        patch['saison_only_blocks'] = _parse_saison_only_blocks(data['saison_only_blocks'])
    if 'block_layout' in data:
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
    if 'dispatch_manager' in data:
        patch['dispatch_manager'] = bool(data.get('dispatch_manager', False))
    if 'fiche_lecture_seule' in data:
        patch['fiche_lecture_seule'] = bool(data.get('fiche_lecture_seule', False))
    if 'can_create_fiche' in data:
        patch['can_create_fiche'] = bool(data.get('can_create_fiche', False))
    if 'can_convert_declaration' in data:
        patch['can_convert_declaration'] = bool(data.get('can_convert_declaration', False))
    if 'alfred_chat' in data and not is_admin_grp:
        patch['alfred_chat'] = bool(data.get('alfred_chat', False))
    if 'allowed_pages' in data and not is_admin_grp:
        patch['allowed_pages'] = _parse_allowed_pages(data.get('allowed_pages'))
    if 'admin_pages' in data and not is_admin_grp:
        patch['admin_pages'] = _parse_admin_pages(data.get('admin_pages'))
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
    "camera_event", "door_saturation_forecast",
}

# Catalogue des Smart Events Hikvision exposes a la modale de creation d'alerte
# camera_event. Chaque entree decrit un type d'evenement remonte par les cameras
# via ecoutehik2.py (cf. PCA/SCRIPTS/cockpit_dispatch.py).
CAMERA_EVENT_TYPES = [
    {"id": "fieldDetection",       "label": "Intrusion de zone",            "icon": "shield",            "color": "#dc2626", "desc": "Personne ou vehicule entre dans une zone interdite."},
    {"id": "lineDetection",        "label": "Franchissement de ligne",      "icon": "linear_scale",      "color": "#dc2626", "desc": "Franchissement d'une ligne virtuelle (acces controle)."},
    {"id": "regionEntrance",       "label": "Entree de zone",               "icon": "login",             "color": "#f97316", "desc": "Mouvement entrant dans une zone surveillee."},
    {"id": "regionExiting",        "label": "Sortie de zone",               "icon": "logout",            "color": "#f97316", "desc": "Sortie anormale d'une zone (parking VIP hors horaire, etc.)."},
    {"id": "unattendedBaggage",    "label": "Objet abandonne",              "icon": "luggage",           "color": "#dc2626", "desc": "Sac, colis ou objet non reclame > N secondes."},
    {"id": "objectRemoval",        "label": "Retrait d'objet",              "icon": "inventory_2",       "color": "#f97316", "desc": "Objet retire de son emplacement (materiel, equipement)."},
    {"id": "loiterDetection",      "label": "Flanerie",                     "icon": "accessibility",     "color": "#eab308", "desc": "Personne stationnaire trop longtemps."},
    {"id": "peopleGathering",      "label": "Attroupement",                 "icon": "groups",            "color": "#f97316", "desc": "Densite de personnes superieure a un seuil."},
    {"id": "fastMoving",           "label": "Mouvement rapide",             "icon": "directions_run",    "color": "#f97316", "desc": "Vitesse anormale (course, fuite, vehicule rapide)."},
    {"id": "parkingDetection",     "label": "Stationnement interdit",       "icon": "no_crash",          "color": "#dc2626", "desc": "Vehicule en zone evacuation, acces secours, voie pompiers."},
    {"id": "audioDetection",       "label": "Bruit anormal",                "icon": "volume_up",         "color": "#eab308", "desc": "Cri, klaxon, alarme, casse (cameras audio uniquement)."},
    {"id": "sceneChangeDetection", "label": "Camera masquee ou deplacee",   "icon": "visibility_off",    "color": "#dc2626", "desc": "Sabotage potentiel : peinture, repositionnement, voile."},
    {"id": "vibrationDetection",   "label": "Vibration / choc",             "icon": "vibration",         "color": "#eab308", "desc": "Impact sur une camera ou son support."},
    {"id": "anpr_watchlist",       "label": "Plaque surveillee (LAPI)",     "icon": "local_police",      "color": "#dc2626", "desc": "Plaque presente dans la watchlist LAPI."},
]
CAMERA_EVENT_TYPE_IDS = {e["id"] for e in CAMERA_EVENT_TYPES}

# Mode d'affichage d'une alerte sur les postes :
#   banner     : notification discrete (toast) + historique, jamais de plein ecran
#   fullscreen : plein ecran a acquitter sur chaque poste (comportement historique)
#   critical   : plein ecran + signal sonore + prise en compte partagee (le
#                premier operateur qui la prend la retire des autres postes)
ALERT_DISPLAY_MODES = ("banner", "fullscreen", "critical")
DEFAULT_ALERT_DISPLAY_MODE = "fullscreen"


def _alert_def_meta(d):
    """Ce dont un poste a besoin pour afficher une alerte de cette definition.

    Le rendu (couleur, icone, titre, mise en forme) est pilote par la
    definition et non plus par des tables codees en dur dans le JS : une
    alerte creee depuis l'admin avec un slug libre (ex. main-courante-flux)
    s'affichait sans en-tete ni bouton visible."""
    slug = d.get("slug") or ""
    mode = d.get("display_mode")
    if mode not in ALERT_DISPLAY_MODES:
        mode = DEFAULT_ALERT_DISPLAY_MODE
    if slug in ("field_sos", "field-sos"):
        mode = "critical"
    return {
        "slug": slug,
        "name": d.get("name") or slug,
        "icon": d.get("icon") or "notifications",
        "color": d.get("color") or "#6366f1",
        "detection_type": d.get("detection_type") or "",
        "display_mode": mode,
        "category": (d.get("params") or {}).get("category") or "",
    }


def _alert_def_metas():
    """{slug: meta} de toutes les definitions (activees ou non : une alerte
    active peut survivre quelques minutes a la desactivation de sa definition)."""
    return {d.get("slug"): _alert_def_meta(d) for d in COL_ALERT_DEFS.find(
        {}, {"slug": 1, "name": 1, "icon": 1, "color": 1, "detection_type": 1,
             "display_mode": 1, "params.category": 1})}

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
        'display_mode': data.get('display_mode') if data.get('display_mode') in ALERT_DISPLAY_MODES else DEFAULT_ALERT_DISPLAY_MODE,
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
    if 'display_mode' in data:
        if data['display_mode'] not in ALERT_DISPLAY_MODES:
            return jsonify({"error": "Mode d'affichage invalide"}), 400
        patch['display_mode'] = data['display_mode']
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

# --- Alfred (agent IA WhatsApp) ---
# Config par groupe : lue/ecrite depuis Field Dispatch.

@app.route('/api/alfred/config', methods=['GET'])
@role_required("admin")
def alfred_config_list():
    return jsonify({"ok": True, "groups": alfred.list_configs()})

@app.route('/api/alfred/config/<path:chat_id>', methods=['POST'])
@role_required("admin")
def alfred_config_upsert(chat_id):
    data = request.get_json(force=True) or {}
    payload = getattr(request, 'user_payload', {})
    user_email = payload.get("email", "?")
    try:
        doc = alfred.upsert_config(chat_id, data, updated_by=user_email)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    last = doc.get("last_summary_at")
    if hasattr(last, "isoformat"):
        doc["last_summary_at"] = last.isoformat()
    updated_at = doc.get("updated_at")
    if hasattr(updated_at, "isoformat"):
        doc["updated_at"] = updated_at.isoformat()
    return jsonify({"ok": True, "group": doc})

@app.route('/api/alfred/summary/trigger/<path:chat_id>', methods=['POST'])
@role_required("admin")
def alfred_summary_trigger(chat_id):
    # trigger_summary_now rend False si un resume de ce groupe tourne deja
    # (garde anti-chevauchement d'alfred.py).
    started = alfred.trigger_summary_now(chat_id)
    return jsonify({"ok": True, "triggered": bool(started), "already_running": not started})


@app.route('/api/alfred/history/<path:chat_id>', methods=['DELETE'])
@role_required("admin")
def alfred_history_clear(chat_id):
    """Vide l'historique des messages WhatsApp ingeres pour un groupe.

    Supprime aussi les reponses Alfred persistees (source=alfred_response).
    Les resumes generes ne sont PAS affectes (ils vivent dans une autre collection).
    """
    user_email = (getattr(request, 'user_payload', {}) or {}).get("email", "?")
    deleted = alfred.clear_group_history(chat_id, deleted_by=user_email)
    return jsonify({"ok": True, "deleted": deleted})

@app.route('/api/alfred/summaries', methods=['GET'])
@role_required("manager")
def alfred_summary_list():
    chat_id = request.args.get("chat_id") or None
    try:
        limit = int(request.args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    return jsonify({"ok": True, "summaries": alfred.list_summaries(chat_id=chat_id, limit=limit)})

@app.route('/api/alfred/summaries/<sid>', methods=['GET'])
@role_required("manager")
def alfred_summary_get(sid):
    doc = alfred.get_summary(sid)
    if not doc:
        return jsonify({"ok": False, "error": "introuvable"}), 404
    return jsonify({"ok": True, "summary": doc})

@app.route('/api/alfred/summaries/<sid>', methods=['DELETE'])
@role_required("admin")
def alfred_summary_delete(sid):
    if not alfred.delete_summary(sid):
        return jsonify({"ok": False, "error": "introuvable"}), 404
    return jsonify({"ok": True})

@app.route('/api/alfred/dm-whitelist', methods=['GET'])
@role_required("admin")
def alfred_dm_list():
    return jsonify({"ok": True, "entries": alfred.list_dm_whitelist()})

@app.route('/api/alfred/dm-whitelist', methods=['POST'])
@role_required("admin")
def alfred_dm_add():
    data = request.get_json(force=True) or {}
    payload = getattr(request, 'user_payload', {})
    user_email = payload.get("email", "?")
    try:
        entry = alfred.add_dm_whitelist(
            data.get("chat_id"),
            label=data.get("label") or "",
            added_by=user_email,
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if hasattr(entry.get("added_at"), "isoformat"):
        entry["added_at"] = entry["added_at"].isoformat()
    return jsonify({"ok": True, "entry": entry})

@app.route('/api/alfred/dm-whitelist/<path:chat_id>', methods=['DELETE'])
@role_required("admin")
def alfred_dm_remove(chat_id):
    if not alfred.remove_dm_whitelist(chat_id):
        return jsonify({"ok": False, "error": "introuvable"}), 404
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


@app.route('/api/alert-definitions/mine', methods=['GET'])
@role_required("user")
def my_alert_definitions():
    """Definitions activees que l'utilisateur recoit : alimente les
    preferences locales (couper une alerte sur ce poste) et le rendu."""
    payload = getattr(request, 'user_payload', {})
    allowed = _get_user_alert_slugs(payload)
    docs = COL_ALERT_DEFS.find({"enabled": True}).sort([('priority', 1), ('name', 1)])
    return jsonify([_alert_def_meta(d) for d in docs
                    if allowed is None or d.get("slug") in allowed])


@app.route('/api/active-alerts', methods=['GET'])
@role_required("user")
def get_active_alerts():
    payload = getattr(request, 'user_payload', {})
    allowed_slugs = _get_user_alert_slugs(payload)
    query = {"expiresAt": {"$gt": datetime.now(timezone.utc)}}
    if allowed_slugs is not None:
        query["definition_slug"] = {"$in": allowed_slugs}
    docs = list(COL_ACTIVE_ALERTS.find(query).sort([('triggeredAt', -1)]).limit(50))
    metas = _alert_def_metas() if docs else {}
    result = []
    for d in docs:
        d['meta'] = metas.get(d.get('definition_slug'))
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


@app.route('/api/active-alerts/<alert_id>/take', methods=['POST'])
@role_required("user")
def take_active_alert(alert_id):
    """Prise en compte partagee d'une alerte critique (SOS tablette compris).

    Le premier operateur qui clique "Je prends en charge / en compte" est
    enregistre sur l'alerte (atomique : un seul gagnant). Les autres postes,
    au poll suivant, ferment leur alerte et affichent qui l'a prise. Pour un
    SOS, la tablette emettrice est prevenue ; pour toute alerte liee a une
    fiche main courante, la fiche recoit une entree de chronologie.

    Eligible : le SOS tablette et les alertes dont la definition est en mode
    d'affichage "critical".
    """
    try:
        oid = ObjectId(alert_id)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    user = request.user_payload
    cur = COL_ACTIVE_ALERTS.find_one({"_id": oid}, {"definition_slug": 1})
    if not cur:
        return jsonify({"ok": False, "error": "not_found"}), 404
    slug = cur.get("definition_slug") or ""
    allowed = _get_user_alert_slugs(user)
    if allowed is not None and slug not in allowed:
        return jsonify({"ok": False, "error": "not_found"}), 404
    is_sos = slug in ("field_sos", "field-sos")
    def_doc = COL_ALERT_DEFS.find_one({"slug": slug}) or {"slug": slug}
    meta = _alert_def_meta(def_doc)
    if not is_sos and meta["display_mode"] != "critical":
        return jsonify({"ok": False, "error": "not_takeable"}), 400

    name = _pcorg_operator(user) or user.get("email", "?")
    now = datetime.now(timezone.utc)
    doc = COL_ACTIVE_ALERTS.find_one_and_update(
        {"_id": oid, "taken_at": {"$exists": False}},
        {"$set": {"taken_at": now, "taken_by": user.get("email", ""), "taken_by_name": name}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        cur = COL_ACTIVE_ALERTS.find_one({"_id": oid}, {"taken_at": 1, "taken_by_name": 1})
        if not cur:
            return jsonify({"ok": False, "error": "not_found"}), 404
        taken = cur.get("taken_at")
        if isinstance(taken, datetime) and taken.tzinfo is None:
            taken = taken.replace(tzinfo=timezone.utc)
        # Deja pris par quelqu'un d'autre (ou par soi sur un autre poste)
        return jsonify({"ok": False, "error": "already_taken",
                        "taken_by_name": cur.get("taken_by_name"),
                        "taken_at": taken.isoformat() if isinstance(taken, datetime) else None}), 409

    ad = doc.get("actionData") or {}
    heure = now.astimezone(ZoneInfo("Europe/Paris")).strftime("%H:%M")
    if ad.get("pcorg_id"):
        texte = ("SOS pris en charge par %s" % name) if is_sos else \
            ("Alerte \"%s\" prise en compte par %s" % (meta["name"], name))
        try:
            PH.append_entry(db["pcorg"], ad["pcorg_id"],
                            PH.make_entry(name, texte),
                            inc_bounce=True)
        except Exception:
            logger.exception("Chronologie prise en compte alerte %s", alert_id)
    if is_sos and ad.get("device_id"):
        try:
            db["field_messages"].insert_one({
                "device_id": ObjectId(ad["device_id"]),
                "device_name": ad.get("device_name"),
                "event": doc.get("event"),
                "year": doc.get("year"),
                "type": "alert",
                "title": "SOS pris en charge",
                "body": "%s (PC org) a pris en charge votre SOS a %s." % (name, heure),
                "priority": "high",
                "from": user.get("email", ""),
                "createdAt": now,
                "expiresAt": now + timedelta(days=7),
                "ack_at": None,
            })
            from field import send_push_to_device
            send_push_to_device(db, ObjectId(ad["device_id"]), "SOS pris en charge",
                                "%s (PC org) a pris en charge votre SOS." % name,
                                url="/field", tag="field-sos-taken-" + str(alert_id))
        except Exception:
            logger.exception("Notification tablette prise en charge SOS %s", alert_id)
    return jsonify({"ok": True, "taken_by_name": name, "taken_at": now.isoformat()})


# ---------------------------------------------------------------------------
# Camera Smart Events : catalogue, liste cameras, snapshot, console temps reel
# ---------------------------------------------------------------------------

HIK_IMAGES_ROOT = os.path.abspath(os.getenv("HIK_IMAGES_ROOT", r"E:\TITAN\production\hik_images"))


@app.route('/api/camera-event-types', methods=['GET'])
@role_required("admin")
def list_camera_event_types():
    """Retourne le catalogue des Smart Events Hik exposes a la modale."""
    return jsonify(CAMERA_EVENT_TYPES)


@app.route('/api/cameras-list', methods=['GET'])
@role_required("admin")
def list_cameras_for_alert_def():
    """Liste des cameras enabled pour la checklist de la modale alerte.
    Fusionne cockpit_cameras (admin Cameras) + anpr_camera_config (LAPI).
    L'identifiant utilise pour selectionner et matcher est camera_path
    (ex: /lapisud3) - c'est ce que ecoutehik2.py emet.
    """
    result = []
    seen_paths = set()

    # 1) cockpit_cameras (champ camera_path si explicitement renseigne)
    for d in db['cockpit_cameras'].find(
        {"enabled": True},
        {"name": 1, "location": 1, "ip": 1, "tags": 1, "camera_path": 1}
    ).sort([("location", 1), ("name", 1)]):
        cp = d.get("camera_path") or ""
        if not cp:
            # Fallback : si pas de camera_path, on utilise /<name>
            cp = "/" + (d.get("name") or str(d["_id"])).strip("/")
        if cp in seen_paths:
            continue
        seen_paths.add(cp)
        result.append({
            "_id": str(d["_id"]),
            "name": d.get("name", ""),
            "location": d.get("location", ""),
            "ip": d.get("ip", ""),
            "tags": d.get("tags", []) or [],
            "camera_path": cp,
            "source": "cockpit_cameras",
        })

    # 2) anpr_camera_config (entrees LAPI specifiques)
    try:
        for d in db['anpr_camera_config'].find(
            {},
            {"camera_path": 1, "label": 1, "location": 1}
        ).sort([("location", 1), ("label", 1)]):
            cp = d.get("camera_path") or ""
            if not cp or cp in seen_paths:
                continue
            seen_paths.add(cp)
            result.append({
                "_id": str(d["_id"]),
                "name": d.get("label", cp),
                "location": d.get("location", "") or "LAPI",
                "ip": "",
                "tags": ["lapi"],
                "camera_path": cp,
                "source": "anpr_camera_config",
            })
    except Exception as e:
        logger.warning("merge anpr_camera_config in /api/cameras-list: %s", e)

    return jsonify(result)


@app.route('/api/hik-snapshot/<alert_id>', methods=['GET'])
@role_required("user")
def get_hik_snapshot(alert_id):
    """Sert le JPEG snapshot lie a une alerte camera depuis le disque local.
    Filtre anti path-traversal : le chemin doit etre sous HIK_IMAGES_ROOT.
    """
    try:
        oid = ObjectId(alert_id)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    doc = COL_ACTIVE_ALERTS.find_one({"_id": oid}, {"actionData": 1})
    if not doc:
        # Tentative aussi sur l'historique au cas ou l'alerte a expire
        doc = db['cockpit_active_alerts_archive'].find_one({"_id": oid}, {"actionData": 1})
    if not doc:
        return jsonify({"error": "Alerte introuvable"}), 404
    snap = (doc.get("actionData") or {}).get("snapshot_path") or ""
    if not snap:
        return jsonify({"error": "Pas de snapshot"}), 404
    # Resolution + securisation du chemin
    full_path = os.path.abspath(snap)
    try:
        common = os.path.commonpath([full_path, HIK_IMAGES_ROOT])
    except ValueError:
        return jsonify({"error": "Chemin invalide"}), 403
    if common != HIK_IMAGES_ROOT:
        return jsonify({"error": "Chemin hors zone autorisee"}), 403
    if not os.path.isfile(full_path):
        return jsonify({"error": "Fichier introuvable"}), 404
    from flask import send_file
    resp = send_file(full_path, mimetype="image/jpeg", max_age=300)
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


@app.route('/public/snapshot/<alert_id>', methods=['GET'])
@csrf.exempt
def public_snapshot(alert_id):
    """Route publique (pas d'auth Cockpit) servant un snapshot camera signe HMAC.
    Utilisee dans les messages WhatsApp ; les destinataires n'ont pas a se
    connecter a Cockpit. URL : /public/snapshot/<alert_id>?exp=<unix>&sig=<hmac32>.
    TTL 7 jours par defaut (configurable cote signature dans whatsapp.py).
    Variable d'env SNAPSHOT_PUBLIC_SECRET partagee Cockpit <-> ecoutehik2 (PCA).
    """
    from whatsapp import verify_snapshot_signature
    exp = request.args.get("exp", "")
    sig = request.args.get("sig", "")
    if not verify_snapshot_signature(alert_id, exp, sig):
        return jsonify({"error": "Lien invalide ou expire"}), 403
    try:
        oid = ObjectId(alert_id)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    doc = COL_ACTIVE_ALERTS.find_one({"_id": oid}, {"actionData": 1})
    if not doc:
        doc = db['cockpit_active_alerts_archive'].find_one({"_id": oid}, {"actionData": 1})
    if not doc:
        return jsonify({"error": "Alerte introuvable"}), 404
    snap = (doc.get("actionData") or {}).get("snapshot_path") or ""
    if not snap:
        return jsonify({"error": "Pas de snapshot"}), 404
    full_path = os.path.abspath(snap)
    try:
        common = os.path.commonpath([full_path, HIK_IMAGES_ROOT])
    except ValueError:
        return jsonify({"error": "Chemin invalide"}), 403
    if common != HIK_IMAGES_ROOT:
        return jsonify({"error": "Chemin hors zone autorisee"}), 403
    if not os.path.isfile(full_path):
        return jsonify({"error": "Fichier introuvable"}), 404
    from flask import send_file
    resp = send_file(full_path, mimetype="image/jpeg", max_age=3600)
    resp.headers["Cache-Control"] = "public, max-age=3600"
    # X-Robots-Tag : ne pas indexer
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.route('/api/hik-events-stream', methods=['GET'])
@role_required("admin")
def hik_events_stream():
    """Console Hik temps reel.
    Aggrege la collection hik_event_stream (alimentee par ecoutehik2.py) par
    (camera_path, event_type) sur la fenetre [since_seconds] (defaut 900s = 15 min).

    Retour : liste triee par last_dt desc :
      [{camera_path, camera_name, camera_location, event_type, event_label,
        event_icon, event_color, count, last_dt, last_snapshot_path,
        last_alert_id}]
    """
    try:
        since_s = int(request.args.get("since", "900"))
    except ValueError:
        since_s = 900
    since_s = max(60, min(86400, since_s))
    since_dt = datetime.now(timezone.utc) - timedelta(seconds=since_s)

    col = db['hik_event_stream']
    pipeline = [
        {"$match": {"ts": {"$gte": since_dt}}},
        {"$sort": {"ts": -1}},
        {"$group": {
            "_id": {"camera_path": "$camera_path", "event_type": "$event_type"},
            "camera_name": {"$first": "$camera_name"},
            "camera_location": {"$first": "$camera_location"},
            "count": {"$sum": 1},
            "last_dt": {"$first": "$ts"},
            "last_snapshot_path": {"$first": "$snapshot_path"},
            "last_id": {"$first": "$_id"},
        }},
        {"$sort": {"last_dt": -1}},
        {"$limit": 200},
    ]
    try:
        agg = list(col.aggregate(pipeline))
    except Exception as e:
        logger.warning("hik_events_stream aggregation failed: %s", e)
        agg = []

    # Map event_type -> meta
    meta_by_id = {e["id"]: e for e in CAMERA_EVENT_TYPES}
    result = []
    for row in agg:
        et = row["_id"].get("event_type", "")
        meta = meta_by_id.get(et, {})
        last_dt = row.get("last_dt")
        if last_dt and last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        result.append({
            "camera_path": row["_id"].get("camera_path", ""),
            "camera_name": row.get("camera_name", "") or row["_id"].get("camera_path", ""),
            "camera_location": row.get("camera_location", ""),
            "event_type": et,
            "event_label": meta.get("label", et),
            "event_icon": meta.get("icon", "notifications"),
            "event_color": meta.get("color", "#6b7280"),
            "count": row.get("count", 0),
            "last_dt": last_dt.isoformat() if last_dt else None,
            "last_snapshot_path": row.get("last_snapshot_path") or "",
            "last_id": str(row.get("last_id")) if row.get("last_id") else "",
        })
    return jsonify({"window_seconds": since_s, "rows": result})


@app.route('/api/hik-events-stream/snapshot/<stream_id>', methods=['GET'])
@role_required("admin")
def hik_event_stream_snapshot(stream_id):
    """Sert un snapshot lie a un event brut hik_event_stream (pour les thumbnails de la console).
    Idem regles de securite que /api/hik-snapshot.
    """
    try:
        oid = ObjectId(stream_id)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    doc = db['hik_event_stream'].find_one({"_id": oid}, {"snapshot_path": 1})
    if not doc:
        return jsonify({"error": "Event introuvable"}), 404
    snap = doc.get("snapshot_path") or ""
    if not snap:
        return jsonify({"error": "Pas de snapshot"}), 404
    full_path = os.path.abspath(snap)
    try:
        common = os.path.commonpath([full_path, HIK_IMAGES_ROOT])
    except ValueError:
        return jsonify({"error": "Chemin invalide"}), 403
    if common != HIK_IMAGES_ROOT:
        return jsonify({"error": "Chemin hors zone autorisee"}), 403
    if not os.path.isfile(full_path):
        return jsonify({"error": "Fichier introuvable"}), 404
    from flask import send_file
    resp = send_file(full_path, mimetype="image/jpeg", max_age=300)
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp

################################################################################
# Webhook & Merge Config
################################################################################

_WEBHOOK_TOKEN_DEFAULT = 'dev-webhook-token-change-me'
WEBHOOK_TOKEN = os.getenv('WEBHOOK_TOKEN', _WEBHOOK_TOKEN_DEFAULT)
if IS_PROD and WEBHOOK_TOKEN == _WEBHOOK_TOKEN_DEFAULT:
    raise ValueError("WEBHOOK_TOKEN must be set via environment variable in production!")


@app.route('/webhook/parametrage-updated', methods=['POST'])
@csrf.exempt
def webhook_parametrage_updated():
    """Webhook appele par groundmaster quand un parametrage est modifie."""
    token = request.headers.get('X-Webhook-Token', '')
    if not hmac.compare_digest(token, WEBHOOK_TOKEN):
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


@app.route('/api/timetable/version', methods=['GET'])
@role_required("user")
def get_timetable_version():
    """Endpoint leger pour le polling : retourne uniquement la version du timetable.
    Utilise par le navigateur pour declencher un refetch si le merge a tourne."""
    event = request.args.get('event')
    year = request.args.get('year')
    if not event or not year:
        return jsonify({"error": "event et year requis"}), 400
    doc = db.timetable.find_one(
        {"event": event, "year": year},
        {"_id": 0, "version": 1}
    )
    return jsonify({"version": (doc or {}).get("version", 0)})


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
    if tile not in ("osm", "sat-egis", "sat-aco", "sat-ign"):
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
        prefs["default_tile"] = tile if tile in ("osm", "sat-egis", "sat-aco", "sat-ign") else "osm"
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

import pcorg_history as PH  # noqa: E402  parsing, fusion SQL/Cockpit, chronologie
import pcorg_assist as PCA  # noqa: E402  search_terms / ensure_text_index (recherche $text)
from pymongo.errors import DuplicateKeyError  # noqa: E402


def _parse_comment_history(comment):
    return PH.parse_comment(comment, origin="sql")


def _pcorg_operator(user):
    return f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()


# Fiches PC Securite (Prysm, PCS.*) : la main courante SAISON les affiche aussi
# (01/10/2026) - hors epreuve ce sont la quasi-totalite des fiches. Un groupe
# restreint les voit selon la categorie PCO equivalente.
PCS_EQUIVALENT = {"PCS.Surete": "PCO.Securite", "PCS.Information": "PCO.Information"}


def _pcorg_cat_query(payload, event=None):
    """Filtre Mongo sur la categorie selon les droits du groupe.

    Les categories autorisees n'etaient filtrees que dans le widget cote
    navigateur : le panneau elargi, la recherche, le detail et toutes les
    ecritures les ignoraient. En SAISON, les fiches PCS.* sont incluses.
    """
    with_pcs = EC.is_saison(event)
    allowed = get_user_allowed_categories(payload)
    if allowed is None:
        return {"$regex": "^PC[OS]\\." if with_pcs else "^PCO"}
    cats = [c for c in allowed if c.startswith("PCO.")]
    if with_pcs:
        cats += [pcs for pcs, pco in PCS_EQUIVALENT.items() if pco in allowed]
    return {"$in": cats}


def _pcorg_cat_allowed(payload, category):
    allowed = get_user_allowed_categories(payload)
    return (allowed is None or category in allowed
            or PCS_EQUIVALENT.get(category) in allowed)


def _pcorg_gps(lat, lon):
    """Point GeoJSON valide, None si absent, ValueError si invalide."""
    if lat is None or lon is None or lat == "" or lon == "":
        return None
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or lat != lat or lon != lon:
        raise ValueError("coordonnees hors bornes")
    return {"type": "Point", "coordinates": [lon, lat]}


# Cles de content_category qu'un client Cockpit ne peut pas poser : elles
# ouvrent des droits cote tablette (cloture depuis le terrain).
_PCORG_CC_RESERVED = {"field_created", "field_sos", "photo_message_id"}
PCORG_TEXT_MAX = 5000


def _pcorg_clean_cc(cc):
    if not isinstance(cc, dict):
        return {}
    out = {}
    for k, v in cc.items():
        k = str(k)
        if not k or "." in k or k.startswith("$") or k in _PCORG_CC_RESERVED:
            continue
        if isinstance(v, str):
            v = v.strip()[:PCORG_TEXT_MAX]
        elif not isinstance(v, (bool, int, float, type(None))):
            continue
        out[k] = v
    return out


def _pcorg_is_closed(doc):
    return (doc or {}).get("status_code") == 10


_pcorg_indexes_ready = False


def _pcorg_ensure_indexes():
    """Index de la collection pcorg (crees jusque-la par la seule synchro SQL :
    une base sans synchro, en dev, n'en avait aucun)."""
    global _pcorg_indexes_ready
    if _pcorg_indexes_ready:
        return
    try:
        col = db["pcorg"]
        col.create_index([("event", 1), ("year", 1), ("ts", 1)])
        col.create_index([("event", 1), ("year", 1), ("category", 1)])
        col.create_index([("event", 1), ("year", 1), ("status_code", 1), ("close_ts", -1)])
        col.create_index([("event", 1), ("year", 1), ("sql_id", 1)])
        # SAISON (main courante permanente, une annee de fiches) : fiches
        # ouvertes recentes (/live), empreinte bornee a 7 jours (/sig).
        col.create_index([("event", 1), ("year", 1), ("status_code", 1), ("ts", 1)])
        col.create_index([("ts", 1)])
        col.create_index([("synced_at", 1)])
        _pcorg_indexes_ready = True
    except Exception as e:
        logger.warning("Index pcorg : %s", e)


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
    "_id": 1, "ts": 1, "close_ts": 1, "created_at": 1, "category": 1, "text": 1,
    "area": 1, "operator": 1, "severity": 1, "is_incident": 1,
    "gps": 1, "status_code": 1, "niveau_urgence": 1, "bounce_rev": 1,
    "operator_close": 1, "server": 1,
    "content_category.sous_classification": 1,
    "content_category.patrouille": 1,
    "content_category.source_type": 1,
    "dispatch.state": 1, "dispatch.current": 1, "dispatch.queue_reason": 1,
    "operator_id_create": 1,
}


def _pcorg_dispatch_view(doc):
    """Etat de dispatch lisible par le front (dispatch_auto) : proposition en
    cours avec son echeance, mise en file et motif. None si jamais dispatchee."""
    d = doc.get("dispatch") or {}
    if not d.get("state"):
        return None
    cur = d.get("current") or {}
    return {
        "state": d.get("state"),
        "current_device": cur.get("device_name"),
        "expires_at": _dt_to_iso_utc(cur.get("expires_at")),
        "queue_reason": d.get("queue_reason"),
    }


def _clean_operator(name):
    if not name:
        return ""
    return re.sub(r'\s*\[.*?\]\s*$', '', name).strip()

def _dt_to_iso_utc(val):
    """Serialise un datetime en ISO avec offset.

    pymongo stocke les datetimes en BSON UTC sans tzinfo. A la relecture, .isoformat()
    sans offset est interprete par le frontend (new Date) comme heure locale au lieu
    de UTC -> decalage du fuseau a l'affichage. On force donc tzinfo=UTC sur les naifs.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.isoformat()
    return val


def _pcorg_close_iso(doc):
    """Date de cloture, None si la fiche n'est pas close. Prysm pose une date
    sentinelle (01/01/9000) sur les fiches ouvertes, affichee "01/01"."""
    ct = doc.get("close_ts")
    if doc.get("status_code") != 10 or (isinstance(ct, datetime) and ct.year >= 9000):
        return None
    return _dt_to_iso_utc(ct)


def _pcorg_serialise(doc):
    """Aplatit un document pcorg pour le JSON frontend."""
    gps = doc.get("gps")
    coords = gps.get("coordinates") if gps and isinstance(gps, dict) else None
    cc = doc.get("content_category") or {}
    area = doc.get("area") or {}
    return {
        "id": str(doc["_id"]),
        "ts": _dt_to_iso_utc(doc.get("ts")),
        "close_ts": _pcorg_close_iso(doc),
        "created_at": _dt_to_iso_utc(doc.get("created_at")),
        "category": doc.get("category"),
        "text": doc.get("text") or "",
        "area_id": area.get("id"),
        "area_desc": area.get("desc") or "",
        "operator": _clean_operator(doc.get("operator")),
        "operator_close": _clean_operator(doc.get("operator_close")),
        "severity": doc.get("severity", 0),
        "is_incident": doc.get("is_incident", False),
        "status_code": doc.get("status_code", 0),
        "sous_classification": cc.get("sous_classification") or "",
        "patrouille": cc.get("patrouille") or "",
        "source_type": cc.get("source_type") or "",
        "lat": coords[1] if coords and len(coords) >= 2 else None,
        "lon": coords[0] if coords and len(coords) >= 2 else None,
        "server": doc.get("server"),
        "niveau_urgence": doc.get("niveau_urgence"),
        "bounce_rev": doc.get("bounce_rev", 0),
        "dispatch": _pcorg_dispatch_view(doc),
        # Createur Cockpit (e-mail) : "lecture seule" permet d'editer ses fiches
        "operator_id_create": doc.get("operator_id_create") or "",
    }


PCORG_CLOSED_PAGE_SIZE = 100

# SAISON = main courante permanente (une annee civile de fiches) : /live ne
# rend que les fiches ouvertes des N derniers jours (+ le nombre des plus
# anciennes) ; `all_open=1` les rend toutes, plafonnees.
PCORG_SAISON_OPEN_DAYS = int(os.getenv("PCORG_SAISON_OPEN_DAYS", "30"))
PCORG_ALL_OPEN_CAP = 1000
# /sig ne lit que les fiches ouvertes ou touchees depuis N jours
PCORG_SIG_WINDOW_DAYS = 7
PCORG_STATS_PERIODS = ("today", "24h", "7d", "all")


def _pcorg_open_years(event, year):
    """Annees couvertes pour les fiches OUVERTES. SAISON de l'annee courante
    inclut SAISON/<annee-1> : une fiche ouverte le 31/12 ne disparait pas au
    changement d'annee."""
    if EC.is_saison(event) and year == EC.saison_year():
        return {"$in": [year, year - 1]}
    return year


def _pcorg_stats_period(event):
    """(cle, since_utc|None, libelle) depuis ?since=ISO ou ?period=.
    Defaut : aujourd'hui (jour de Paris) pour SAISON, tout pour une epreuve."""
    now = datetime.now(timezone.utc)
    since_raw = (request.args.get("since") or "").strip()
    if since_raw:
        dt = PH.to_aware(since_raw)
        if dt is not None:
            loc = dt.astimezone(PH.PARIS)
            return "since", dt.astimezone(timezone.utc), "depuis le " + loc.strftime("%d/%m %H:%M")
    period = (request.args.get("period") or "").strip().lower()
    if period not in PCORG_STATS_PERIODS:
        period = "today" if EC.is_saison(event) else "all"
    if period == "today":
        day0 = now.astimezone(PH.PARIS).replace(hour=0, minute=0, second=0, microsecond=0)
        return period, day0.astimezone(timezone.utc), "aujourd'hui"
    if period == "24h":
        return period, now - timedelta(hours=24), "24 dernieres heures"
    if period == "7d":
        return period, now - timedelta(days=7), "7 derniers jours"
    return "all", None, ("toute l'annee" if EC.is_saison(event) else "toute l'edition")


_PCORG_SIG_CACHE = {}   # (event, year) -> (monotonic, sig)
_PCORG_SIG_TTL = 2.0


@app.route('/api/pcorg/sig', methods=['GET'])
@role_required("user")
def pcorg_signature():
    """Empreinte legere de la main courante d'un evenement : les postes la
    lisent toutes les 5 s et ne rechargent /live que si elle change. Sans elle,
    une fiche close depuis une tablette restait affichee jusqu'a 60 s.
    Couvre creation, suppression, cloture, commentaires (bounce_rev /
    cockpit_rev incrementes a chaque ecriture Cockpit ou tablette) et la
    synchro Prysm (synced_at). Cache 2 s partage entre les postes."""
    event = request.args.get("event", "")
    try:
        year = int(request.args.get("year", ""))
    except ValueError:
        return jsonify({"error": "year invalide"}), 400
    if not event:
        return jsonify({"error": "event requis"}), 400
    key = (event, year)
    now_m = time.monotonic()
    hit = _PCORG_SIG_CACHE.get(key)
    if hit and now_m - hit[0] < _PCORG_SIG_TTL:
        return jsonify({"sig": hit[1]})
    _pcorg_ensure_indexes()
    # Borne : fiches ouvertes, ou creees / resynchronisees depuis 7 jours.
    # Sans elle, SAISON (une annee entiere) etait relue toutes les 2 s. Les
    # ecritures Cockpit/tablette sont refusees sur fiche close (sauf
    # reouverture, qui la remet dans "ouvertes") : rien n'echappe a la borne.
    # Chaque branche du $or porte event/year pour rester indexee.
    since = datetime.now(timezone.utc) - timedelta(days=PCORG_SIG_WINDOW_DAYS)
    ey = {"event": event, "year": _pcorg_open_years(event, year)}
    agg = list(db["pcorg"].aggregate([
        {"$match": {"$or": [
            {**ey, "status_code": {"$ne": 10}},
            {**ey, "ts": {"$gte": since}},
            {**ey, "synced_at": {"$gte": since}},
        ]}},
        {"$group": {
            "_id": None,
            "n": {"$sum": 1},
            "closed": {"$sum": {"$cond": [{"$eq": ["$status_code", 10]}, 1, 0]}},
            "br": {"$sum": {"$ifNull": ["$bounce_rev", 0]}},
            "cr": {"$sum": {"$ifNull": ["$cockpit_rev", 0]}},
            "sync": {"$max": "$synced_at"},
        }},
    ]))
    a = agg[0] if agg else {}
    raw = "%s|%s|%s|%s|%s" % (a.get("n", 0), a.get("closed", 0), a.get("br", 0), a.get("cr", 0), a.get("sync"))
    sig = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    _PCORG_SIG_CACHE[key] = (now_m, sig)
    resp = jsonify({"sig": sig})
    resp.headers["Cache-Control"] = "no-store"
    return resp


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

    _pcorg_ensure_indexes()
    base = {"event": event, "year": year, "category": _pcorg_cat_query(request.user_payload, event)}
    col = db["pcorg"]

    saison = EC.is_saison(event)
    all_open = (request.args.get("all_open") or "").strip().lower() in ("1", "true", "yes")
    older_open = 0
    if saison:
        # Main courante permanente : fiches ouvertes recentes seulement (les
        # oubliees de l'annee s'accumulent), + SAISON/<annee-1> au changement
        # d'annee. all_open=1 : toutes, plafonnees.
        open_q = {**base, "year": _pcorg_open_years(event, year), "status_code": {"$nin": [10]}}
        if all_open:
            open_docs = list(col.find(open_q, PCO_PROJECTION).sort("ts", -1).limit(PCORG_ALL_OPEN_CAP))
        else:
            since = datetime.now(timezone.utc) - timedelta(days=PCORG_SAISON_OPEN_DAYS)
            open_docs = list(col.find({**open_q, "ts": {"$gte": since}}, PCO_PROJECTION)
                             .sort("ts", -1).limit(PCORG_ALL_OPEN_CAP))
            older_open = col.count_documents({**open_q, "ts": {"$not": {"$gte": since}}})
    else:
        open_docs = list(col.find(
            {**base, "status_code": {"$nin": [10]}},
            PCO_PROJECTION
        ).sort("ts", -1))

    closed_query = {**base, "status_code": 10}
    closed_docs = list(col.find(
        closed_query,
        PCO_PROJECTION
    ).sort("close_ts", -1).limit(PCORG_CLOSED_PAGE_SIZE))
    closed_total = col.count_documents(closed_query)

    return jsonify({
        "open": [_pcorg_serialise(d) for d in open_docs],
        "closed": [_pcorg_serialise(d) for d in closed_docs],
        "counts": {
            "open": len(open_docs),
            "closed": len(closed_docs),
            "closed_total": closed_total,
        },
        "closed_page_size": PCORG_CLOSED_PAGE_SIZE,
        # SAISON : fiches ouvertes plus anciennes que la fenetre, non rendues
        "older_open": older_open,
        "all_open": bool(saison and all_open),
        "open_window_days": PCORG_SAISON_OPEN_DAYS if saison else None,
    })


@app.route('/api/pcorg/stats', methods=['GET'])
@role_required("user")
def pcorg_stats():
    event = request.args.get("event", "")
    year = request.args.get("year", "")
    if not event or not year:
        return jsonify({"error": "event et year requis"}), 400
    try:
        year = int(year)
    except ValueError:
        return jsonify({"error": "year invalide"}), 400

    # Periode : ouvertes = toutes celles ouvertes maintenant ; closes = closes
    # pendant la periode (close_ts). SAISON : aujourd'hui par defaut (un
    # compteur annuel n'a pas de sens en exploitation courante).
    period_key, since, period_label = _pcorg_stats_period(event)
    cat_q = _pcorg_cat_query(request.user_payload, event)
    open_clause = {"event": event, "year": _pcorg_open_years(event, year),
                   "category": cat_q, "status_code": {"$ne": 10}}
    closed_clause = {"event": event, "year": year, "category": cat_q, "status_code": 10}
    if since is not None:
        closed_clause["year"] = _pcorg_open_years(event, year)
        closed_clause["close_ts"] = {"$gte": since}
    pipeline = [
        {"$match": {"$or": [open_clause, closed_clause]}},
        {"$group": {
            "_id": {
                "cat": "$category",
                "closed": {"$eq": ["$status_code", 10]},
            },
            "n": {"$sum": 1},
        }},
    ]
    counts = {}
    total_open = 0
    total_closed = 0
    for row in db["pcorg"].aggregate(pipeline):
        cat = row["_id"].get("cat") or ""
        is_closed = bool(row["_id"].get("closed"))
        n = int(row.get("n", 0))
        bucket = counts.setdefault(cat, {"open": 0, "closed": 0})
        if is_closed:
            bucket["closed"] = n
            total_closed += n
        else:
            bucket["open"] = n
            total_open += n

    return jsonify({
        "counts": counts,
        "totals": {
            "open": total_open,
            "closed": total_closed,
            "all": total_open + total_closed,
        },
        "period": {
            "key": period_key,
            "since": since.isoformat() if since else None,
            "label": period_label,
            "basis": "close_ts",
        },
    })


@app.route('/api/pcorg/closed', methods=['GET'])
@role_required("user")
def pcorg_closed_page():
    event = request.args.get("event", "")
    year = request.args.get("year", "")
    if not event or not year:
        return jsonify({"error": "event et year requis"}), 400
    try:
        year = int(year)
    except ValueError:
        return jsonify({"error": "year invalide"}), 400

    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0
    try:
        limit = int(request.args.get("limit", PCORG_CLOSED_PAGE_SIZE))
    except ValueError:
        limit = PCORG_CLOSED_PAGE_SIZE
    limit = max(1, min(limit, 200))

    base = {
        "event": event, "year": year,
        "category": _pcorg_cat_query(request.user_payload, event), "status_code": 10,
    }
    col = db["pcorg"]
    # Pagination par curseur (close_ts, _id) : l'offset glissait quand une
    # fiche etait cloturee entre deux pages (doublons ou trous).
    before_ts = PH.to_aware(request.args.get("before_ts"))
    # Total : premiere page seulement (SAISON = une annee de fiches closes,
    # recompter a chaque page de defilement ne sert a rien ; le client garde
    # le total precedent quand il recoit null).
    total = col.count_documents(base) if before_ts is None else None

    before_id = request.args.get("before_id") or ""
    query = dict(base)
    if before_ts is not None:
        bt = before_ts.astimezone(timezone.utc).replace(tzinfo=None)
        query["$or"] = [
            {"close_ts": {"$lt": bt}},
            {"close_ts": bt, "_id": {"$lt": before_id}},
        ]
        cursor = col.find(query, PCO_PROJECTION).sort([("close_ts", -1), ("_id", -1)]).limit(limit + 1)
    else:
        cursor = col.find(query, PCO_PROJECTION).sort([("close_ts", -1), ("_id", -1)]).skip(offset).limit(limit + 1)
    docs = list(cursor)
    has_more = len(docs) > limit
    items = [_pcorg_serialise(d) for d in docs[:limit]]
    return jsonify({
        "items": items,
        "offset": offset,
        "limit": limit,
        "total": total,
        "has_more": has_more,
    })


@app.route('/api/pcorg/search', methods=['GET'])
@role_required("user")
def pcorg_search():
    event = request.args.get("event", "")
    year = request.args.get("year", "")
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").lower()
    if not event or not year:
        return jsonify({"error": "event et year requis"}), 400
    try:
        year = int(year)
    except ValueError:
        return jsonify({"error": "year invalide"}), 400
    if len(q) < 2:
        return jsonify({"error": "query trop courte"}), 400

    try:
        limit = int(request.args.get("limit", 200))
    except ValueError:
        limit = 200
    limit = max(1, min(limit, 500))

    base = {"event": event, "year": year, "category": _pcorg_cat_query(request.user_payload, event)}
    col = db["pcorg"]
    open_years = _pcorg_open_years(event, year)

    # 1) Index texte (pca_text : text, sous-classification, zone ; cree par
    # pcorg_assist) : SAISON porte une annee de fiches, un $regex sur
    # `comment` les relit toutes. Le texte ignore la chronologie, l'operateur
    # et le carroyage : sans resultat, ou pour un code (chiffres : n° SQL,
    # carroyage), on retombe sur le $regex historique.
    terms = [] if any(ch.isdigit() for ch in q) else PCA.search_terms(q)
    if terms and PCA.ensure_text_index(col):
        tq = {"$text": {"$search": " ".join(terms)}}
        proj = dict(PCO_PROJECTION)
        proj["score"] = {"$meta": "textScore"}
        t_open, t_closed = [], []
        try:
            if status in ("all", "open"):
                cur = col.find({**base, **tq, "year": open_years, "status_code": {"$nin": [10]}}, proj)
                t_open = list(cur.sort([("score", {"$meta": "textScore"})]).limit(limit))
            if status in ("all", "closed"):
                cur = col.find({**base, **tq, "status_code": 10}, proj)
                t_closed = list(cur.sort([("score", {"$meta": "textScore"})]).limit(limit))
        except Exception as e:  # index texte supprime entre-temps, etc.
            logger.warning("pcorg search $text : %s", e)
            t_open, t_closed = [], []
        if t_open or t_closed:
            return jsonify({
                "open": [_pcorg_serialise(d) for d in t_open],
                "closed": [_pcorg_serialise(d) for d in t_closed],
                "counts": {"open": len(t_open), "closed": len(t_closed)},
                "q": q,
                "status": status,
                "limit": limit,
                "method": "text",
            })

    # 2) Repli $regex (sous-chaine, tous champs y compris la chronologie)
    rx = {"$regex": re.escape(q), "$options": "i"}
    or_clauses = [
        {"text": rx},
        {"category": rx},
        {"area.desc": rx},
        {"operator": rx},
        {"content_category.sous_classification": rx},
        {"content_category.patrouille": rx},
        {"content_category.carroye": rx},
        {"comment": rx},
    ]
    if q.isdigit():
        or_clauses.append({"sql_id": int(q)})
    base["$or"] = or_clauses

    open_items = []
    closed_items = []
    if status in ("all", "open"):
        open_q = dict(base)
        open_q["year"] = open_years
        open_q["status_code"] = {"$nin": [10]}
        open_cur = col.find(open_q, PCO_PROJECTION).sort("ts", -1).limit(limit)
        open_items = [_pcorg_serialise(d) for d in open_cur]
    if status in ("all", "closed"):
        closed_q = dict(base)
        closed_q["status_code"] = 10
        closed_cur = col.find(closed_q, PCO_PROJECTION).sort("close_ts", -1).limit(limit)
        closed_items = [_pcorg_serialise(d) for d in closed_cur]

    return jsonify({
        "open": open_items,
        "closed": closed_items,
        "counts": {"open": len(open_items), "closed": len(closed_items)},
        "q": q,
        "status": status,
        "limit": limit,
        "method": "regex",
    })


@app.route('/api/pcorg/detail/<doc_id>', methods=['GET'])
@role_required("user")
def pcorg_detail(doc_id):
    doc = db["pcorg"].find_one({"_id": doc_id})
    if not doc:
        return jsonify({"error": "introuvable"}), 404
    if not _pcorg_cat_allowed(request.user_payload, doc.get("category")):
        return jsonify({"error": "categorie non autorisee"}), 403
    gps = doc.get("gps")
    coords = gps.get("coordinates") if gps and isinstance(gps, dict) else None
    cc = doc.get("content_category") or {}
    area = doc.get("area") or {}
    # comment_history : utiliser le champ stocke, sinon parser a la volee.
    # Decoree pour l'affichage : statut, modifications Prysm et commentaire
    # separes, entrees vides signalees, cloture synthetique si absente.
    comment_history = doc.get("comment_history")
    if comment_history is None:
        comment_history = _parse_comment_history(doc.get("comment"))
    comment_history = PH.decorate_history(comment_history, doc)

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
        "ts": _dt_to_iso_utc(doc.get("ts")),
        "close_ts": _pcorg_close_iso(doc),
        "created_at": _dt_to_iso_utc(doc.get("created_at")),
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
        "cockpit_owned": doc.get("cockpit_owned") or [],
        "operator_id_create": doc.get("operator_id_create") or "",
        "dispatch": _pcorg_dispatch_view(doc),
        "intervention": {k: (_dt_to_iso_utc(v) if isinstance(v, datetime) else v)
                         for k, v in (doc.get("intervention") or {}).items()},
    })


def _engage_field_device(patrouille_name, fiche_id, event, year, category="", text=""):
    """Assigne une fiche a une tablette terrain (pose active_fiche_id)
    sans changer le statut : c'est l'operateur tablette qui confirmera
    son engagement via le bouton 'Engagement'."""
    if not patrouille_name or not fiche_id:
        return
    # Tablette appairee sur l'evenement de la fiche d'abord, sinon une
    # tablette d'un autre evenement qui le voit (SAISON pendant une epreuve
    # active) : field.find_device_for_fiche / device_pairs.
    try:
        from field import find_device_for_fiche
        device = find_device_for_fiche(db, patrouille_name, event, year)
    except Exception:
        device = None
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
        # active_fiche_id designe la tablette sans ambiguite, quel que soit
        # son evenement d'appairage (tablette SAISON engagee sur une epreuve).
        device = db["field_devices"].find_one({
            "name": patrouille_name,
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


# Bornes d'antidatage / programmation pour les fiches PCO
PCORG_TS_PAST_MAX_DAYS = 60     # antidatage max
PCORG_TS_FUTURE_MAX_DAYS = 30   # programmation max


def _parse_intervention_ts(raw):
    """Parse un timestamp d'intervention (ISO 8601 ou datetime-local navigateur)
    en datetime aware Europe/Paris.
    Retourne (datetime|None, error_str|None). raw vide/None -> (None, None).
    """
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, "intervention_ts invalide"
    s = raw.strip()
    if not s:
        return None, None
    # datetime-local navigateur: '2026-05-11T14:32' (sans seconde, sans tz)
    if "T" in s and len(s) == 16:
        s = s + ":00"
    # 'Z' -> '+00:00' pour fromisoformat (Python <3.11 compat safety)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None, "intervention_ts invalide (ISO 8601 attendu)"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("Europe/Paris"))
    now = datetime.now(ZoneInfo("Europe/Paris"))
    if dt < now - timedelta(days=PCORG_TS_PAST_MAX_DAYS):
        return None, f"date trop ancienne (max {PCORG_TS_PAST_MAX_DAYS} jours)"
    if dt > now + timedelta(days=PCORG_TS_FUTURE_MAX_DAYS):
        return None, f"date trop lointaine (max {PCORG_TS_FUTURE_MAX_DAYS} jours)"
    return dt, None


@app.route('/api/pcorg/create', methods=['POST'])
@role_required("user")
def pcorg_create():
    if not _user_can_create_fiche(request.user_payload):
        return jsonify(_PCORG_CREATE_ERROR[0]), _PCORG_CREATE_ERROR[1]
    data = request.get_json(force=True)
    event = data.get("event", "")
    year = data.get("year", "")
    category = data.get("category", "")
    text = data.get("text", "").strip()
    if not event or not year or not category or not text:
        return jsonify({"error": "event, year, category et text requis"}), 400
    if not category.startswith("PCO."):
        return jsonify({"error": "categorie invalide (doit commencer par PCO.)"}), 400
    if not _pcorg_cat_allowed(request.user_payload, category):
        return jsonify({"error": "categorie non autorisee pour votre groupe"}), 403
    if len(text) > PCORG_TEXT_MAX:
        return jsonify({"error": f"description trop longue (max {PCORG_TEXT_MAX} caracteres)"}), 400
    try:
        year = int(year)
    except ValueError:
        return jsonify({"error": "year invalide"}), 400

    now = datetime.now(ZoneInfo("Europe/Paris"))
    user = request.user_payload
    operator_name = _pcorg_operator(user)

    # Heure d'intervention (= ts) : par defaut 'maintenant', sinon antidatage/programmation
    intervention_ts, ts_err = _parse_intervention_ts(data.get("intervention_ts"))
    if ts_err:
        return jsonify({"error": ts_err}), 400
    ts_dt = intervention_ts or now
    ts_str = ts_dt.isoformat()
    created_at_str = now.isoformat()

    try:
        gps = _pcorg_gps(data.get("lat"), data.get("lon"))
    except (ValueError, TypeError):
        return jsonify({"error": "position invalide"}), 400

    niveau_urgence = data.get("niveau_urgence")
    if niveau_urgence and niveau_urgence not in VALID_URGENCY_LEVELS:
        return jsonify({"error": "niveau_urgence invalide"}), 400

    area_desc = (data.get("area_desc") or "").strip()
    content_cat = _pcorg_clean_cc(data.get("content_category"))
    initial_comment = (data.get("comment") or "").strip()[:PCORG_TEXT_MAX]

    # Jeton client (un par ouverture de l'assistant) : un double clic sur
    # "Creer" rejoue la meme requete et retombe sur le meme _id au lieu de
    # creer une seconde fiche. Sans jeton : UUID base sur created_at
    # (immuable) -> 2 fiches antidatees identiques restent uniques.
    client_token = str(data.get("client_token") or "").strip()[:64]
    if client_token:
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pcorg-client|{user.get('email', '')}|{client_token}"))
        if db["pcorg"].find_one({"_id": doc_id}, {"_id": 1}):
            return jsonify({"ok": True, "id": doc_id, "duplicate": True})
    else:
        doc_id = _pcorg_mk_uuid(event, year, created_at_str, category, text, "", str(user.get("email", "")))

    # Build initial comment / comment_history (horodate sur ts d'intervention pour coherence chrono)
    comment_raw = ""
    comment_history = []
    if initial_comment:
        entry = PH.make_entry(operator_name, initial_comment, ts=ts_dt)
        comment_history.append(entry)
        comment_raw = PH.render_comment(comment_history)

    doc = {
        "_id": doc_id,
        "event": event,
        "year": year,
        "ts": ts_dt,
        "timestamp_iso": ts_str,
        "created_at": now,
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

    try:
        db["pcorg"].insert_one(doc)
    except DuplicateKeyError:
        return jsonify({"ok": True, "id": doc_id, "duplicate": True})

    # Engager la tablette terrain si patrouille correspond, sinon proposition
    # automatique si la categorie le demande (dispatch_auto)
    patr = content_cat.get("patrouille", "")
    if patr:
        _engage_field_device(patr, doc_id, event, year, category=category, text=text)
    else:
        DA.maybe_auto_start(db, doc_id)

    return jsonify({"ok": True, "id": doc_id})


@app.route('/api/pcorg/quick-create', methods=['POST'])
@role_required("user")
def pcorg_quick_create():
    """Creation rapide d'une fiche simplifiee (clic droit carte)."""
    if not _user_can_create_fiche(request.user_payload):
        return jsonify(_PCORG_CREATE_ERROR[0]), _PCORG_CREATE_ERROR[1]
    data = request.get_json(force=True)
    event = data.get("event", "")
    year = data.get("year", "")
    category = data.get("category", "")
    niveau_urgence = data.get("niveau_urgence", "")
    if not event or not year or not category or not niveau_urgence:
        return jsonify({"error": "event, year, category et niveau_urgence requis"}), 400
    if not category.startswith("PCO."):
        return jsonify({"error": "categorie invalide"}), 400
    if not _pcorg_cat_allowed(request.user_payload, category):
        return jsonify({"error": "categorie non autorisee pour votre groupe"}), 403
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
    operator_name = _pcorg_operator(user)

    carroye = (data.get("carroye") or "").strip()
    area_desc = (data.get("area_desc") or "").strip()
    try:
        gps = _pcorg_gps(data.get("lat"), data.get("lon"))
    except (ValueError, TypeError):
        return jsonify({"error": "position invalide"}), 400

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
    text = f"Cette fiche a ete generee en procedure d'urgence par {operator_name}"
    if group_name:
        text += f" du {group_name}"

    client_token = str(data.get("client_token") or "").strip()[:64]
    if client_token:
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pcorg-client|{user.get('email', '')}|{client_token}"))
        if db["pcorg"].find_one({"_id": doc_id}, {"_id": 1}):
            return jsonify({"ok": True, "id": doc_id, "duplicate": True})
    else:
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
        "created_at": now,
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

    try:
        db["pcorg"].insert_one(doc)
    except DuplicateKeyError:
        return jsonify({"ok": True, "id": doc_id, "duplicate": True})

    # Engager la tablette terrain si patrouille correspond, sinon proposition
    # automatique si la categorie le demande (dispatch_auto)
    if patrouille:
        _engage_field_device(patrouille, doc_id, event, year, category=category, text=text)
    else:
        DA.maybe_auto_start(db, doc_id)

    return jsonify({"ok": True, "id": doc_id})


_PCORG_FIELD_LABELS = {
    "text": "Description", "category": "Categorie", "area_desc": "Zone",
    "niveau_urgence": "Urgence", "gps": "Position", "ts": "Heure d'intervention",
}
_PCORG_CC_LABELS = {
    "sous_classification": "Sous-classification", "patrouille": "Vehicule engage",
    "appelant": "Appelant", "carroye": "Carroye", "intervenant1": "Intervenant 1",
    "intervenant2": "Intervenant 2", "service_contacte": "Service contacte",
    "moyens_engages_niveau_1": "Moyens niv. 1", "moyens_engages_niveau_2": "Moyens niv. 2",
    "source_type": "Source", "canal": "Canal", "canal_detail": "Detail canal",
    "emetteur_interne": "Emetteur", "donneur_ordre": "Donneur d'ordre",
    "telephone": "Telephone", "radio": "Radio", "texte": "Texte", "alerte": "Alerte",
    "typedemande": "Type de demande", "decision": "Decision", "lieu": "Lieu",
    "immat": "Immatriculation", "detailsvl": "Vehicule", "source_origine": "A la suite de",
    "radio_canal": "Canal radio", "presentiel": "Presentiel", "mail": "Mail",
}


def _pcorg_fmt_val(v):
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "oui" if v else "non"
    if isinstance(v, dict) and v.get("type") == "Point":
        c = v.get("coordinates") or [None, None]
        try:
            return f"{float(c[1]):.5f}, {float(c[0]):.5f}"
        except (TypeError, ValueError, IndexError):
            return ""
    return str(v)[:300]


def _pcorg_urgency_label(category, niveau):
    if not niveau:
        return "Aucun"
    labels = URGENCY_LABELS.get(_urgency_type(category), URGENCY_LABELS["MIXTE"])
    return labels.get(niveau, niveau)


def _pcorg_load_for_write(doc_id, need_open=True, projection=None):
    """Charge une fiche pour ecriture et verifie les droits.
    Retourne (doc, None) ou (None, reponse_erreur)."""
    doc = db["pcorg"].find_one({"_id": doc_id}, projection)
    if not doc:
        return None, (jsonify({"error": "introuvable"}), 404)
    if not _pcorg_cat_allowed(request.user_payload, doc.get("category")):
        return None, (jsonify({"error": "categorie non autorisee pour votre groupe"}), 403)
    if need_open and _pcorg_is_closed(doc):
        return None, (jsonify({"error": "intervention close, non editable"}), 403)
    return doc, None


_PCORG_OPEN_FILTER = {"status_code": {"$ne": 10}}


@app.route('/api/pcorg/update/<doc_id>', methods=['PUT'])
@role_required("user")
def pcorg_update(doc_id):
    """Met a jour les champs d'une intervention (SQL ou COCKPIT).

    Chaque modification est tracee dans la chronologie (entree systeme avec
    la liste des champs modifies, ancienne -> nouvelle valeur) et, pour une
    fiche SQL, marquee comme possedee par Cockpit : la synchro ne l'ecrasera
    plus. Seuls les champs reellement modifies sont ecrits.
    """
    doc, err = _pcorg_load_for_write(doc_id)
    if err:
        return err
    if not _user_can_edit_fiche(request.user_payload, doc):
        return jsonify(_PCORG_READONLY_ERROR[0]), _PCORG_READONLY_ERROR[1]
    data = request.get_json(force=True) or {}
    user = request.user_payload
    operator_name = _pcorg_operator(user)

    sets, owned, changes = {}, set(), []

    def change(key, old, new, label=None):
        changes.append({
            "field": label or _PCORG_FIELD_LABELS.get(key, key),
            "old": _pcorg_fmt_val(old), "new": _pcorg_fmt_val(new),
        })

    if "text" in data:
        txt = (data["text"] or "").strip()
        if txt and txt != (doc.get("text") or ""):
            if len(txt) > PCORG_TEXT_MAX:
                return jsonify({"error": f"description trop longue (max {PCORG_TEXT_MAX})"}), 400
            sets["text"] = txt
            sets["text_full"] = txt
            owned.update(["text", "text_full"])
            change("text", doc.get("text"), txt)

    new_cat = doc.get("category")
    if data.get("category") and data["category"] != doc.get("category"):
        new_cat = data["category"]
        if not str(new_cat).startswith("PCO.") or new_cat not in ALL_PCO_CATEGORIES:
            return jsonify({"error": "categorie invalide"}), 400
        if not _pcorg_cat_allowed(user, new_cat):
            return jsonify({"error": "categorie non autorisee pour votre groupe"}), 403
        sets["category"] = new_cat
        sets["source"] = new_cat
        owned.update(["category", "source"])
        change("category", (doc.get("category") or "").replace("PCO.", ""), new_cat.replace("PCO.", ""))

    if "area_desc" in data:
        new_area = (data.get("area_desc") or "").strip()
        old_area = ((doc.get("area") or {}).get("desc")) or ""
        if new_area != old_area:
            sets["area.desc"] = new_area
            owned.add("area.desc")
            change("area_desc", old_area, new_area)

    if "niveau_urgence" in data:
        nu = data.get("niveau_urgence") or None
        if nu and nu not in VALID_URGENCY_LEVELS:
            return jsonify({"error": "niveau_urgence invalide"}), 400
        if nu != (doc.get("niveau_urgence") or None):
            sets["niveau_urgence"] = nu
            owned.add("niveau_urgence")
            change("niveau_urgence", _pcorg_urgency_label(new_cat, doc.get("niveau_urgence")),
                   _pcorg_urgency_label(new_cat, nu))

    if "lat" in data and "lon" in data:
        try:
            gps = _pcorg_gps(data.get("lat"), data.get("lon"))
        except (ValueError, TypeError):
            return jsonify({"error": "position invalide"}), 400
        if gps and gps != doc.get("gps"):
            sets["gps"] = gps
            owned.add("gps")
            change("gps", doc.get("gps"), gps)

    old_cc = doc.get("content_category") or {}
    cc_update = _pcorg_clean_cc(data.get("content_category"))
    for k, v in cc_update.items():
        old_v = old_cc.get(k)
        if (old_v in (None, "", False) and v in (None, "", False)) or old_v == v:
            continue
        sets[f"content_category.{k}"] = v
        owned.add(f"content_category.{k}")
        # Drapeaux de compatibilite (derives du canal) : ecrits, pas traces
        if k not in ("telephone", "radio", "presentiel", "mail", "radio_canal"):
            change(k, old_v, v, label=_PCORG_CC_LABELS.get(k, k))
        elif k == "radio_canal" and v:
            change(k, old_v, v, label=_PCORG_CC_LABELS.get(k, k))
    # Champs propres a l'ancienne categorie, a retirer lors d'un changement
    removed = []
    for k in data.get("content_category_remove") or []:
        k = str(k)
        if k in old_cc and "." not in k and not k.startswith("$") and k not in _PCORG_CC_RESERVED \
                and k not in cc_update:
            removed.append(k)
            owned.add(f"content_category.{k}")
            if old_cc.get(k) not in (None, "", False):
                change(k, old_cc.get(k), "", label=_PCORG_CC_LABELS.get(k, k))

    # Heure d'intervention (ts) : antidatage / reprogrammation a posteriori
    if "intervention_ts" in data:
        new_ts, ts_err = _parse_intervention_ts(data.get("intervention_ts"))
        if ts_err:
            return jsonify({"error": ts_err}), 400
        if new_ts is None:
            return jsonify({"error": "intervention_ts requis"}), 400
        old_ts = PH.to_aware(doc.get("ts"))
        if old_ts is None or abs((new_ts - old_ts).total_seconds()) >= 60:
            sets["ts"] = new_ts
            sets["timestamp_iso"] = new_ts.isoformat()
            owned.update(["ts", "timestamp_iso"])
            old_fmt = old_ts.astimezone(ZoneInfo("Europe/Paris")).strftime("%d/%m %Hh%M") if old_ts else ""
            change("ts", old_fmt, new_ts.strftime("%d/%m %Hh%M"))

    comment = (data.get("comment") or "").strip()[:PCORG_TEXT_MAX]
    if not sets and not removed and not comment:
        return jsonify({"ok": True, "unchanged": True})

    entries = []
    if changes:
        labels = ", ".join(dict.fromkeys(c["field"] for c in changes))
        entries.append(PH.make_entry(operator_name, f"Fiche modifiee : {labels}",
                                     system=True, changes=changes))
    if comment:
        entries.append(PH.make_entry(operator_name, comment))

    for k in removed:
        # Vide plutot que supprime : la valeur reste possedee par Cockpit,
        # la synchro ne la fera pas reapparaitre
        sets[f"content_category.{k}"] = None
    n = PH.append_entry(db["pcorg"], doc_id, entries, set_fields=sets, owned=owned,
                        inc_bounce=True, extra_filter=_PCORG_OPEN_FILTER)
    if not n:
        return jsonify({"error": "fiche close ou supprimee entre-temps"}), 409

    # Vehicule : desengager l'ancien, engager le nouveau
    old_p = old_cc.get("patrouille") or ""
    new_p = cc_update.get("patrouille", old_p) or ""
    if new_p != old_p:
        if old_p:
            _disengage_field_device(doc, doc_id)
        if new_p:
            _engage_field_device(new_p, doc_id, doc.get("event", ""), doc.get("year", ""),
                                 category=new_cat, text=sets.get("text") or doc.get("text", ""))
            # Engagement direct : annule une proposition automatique en cours
            DA.on_manual_assign(db, doc_id, by=operator_name)
    if not new_p:
        # Urgence ou categorie changee sur une fiche sans unite
        DA.maybe_auto_start(db, doc_id, trigger="modification")

    return jsonify({"ok": True, "changes": changes})


@app.route('/api/pcorg/comment/<doc_id>', methods=['POST'])
@role_required("user")
def pcorg_add_comment(doc_id):
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text requis"}), 400
    if len(text) > PCORG_TEXT_MAX:
        return jsonify({"error": f"commentaire trop long (max {PCORG_TEXT_MAX})"}), 400
    _doc, err = _pcorg_load_for_write(doc_id, projection={"status_code": 1, "category": 1})
    if err:
        return err

    photo_url = (data.get("photo") or "").strip() or None
    entry = PH.make_entry(_pcorg_operator(request.user_payload), text, photo=photo_url)
    n = PH.append_entry(db["pcorg"], doc_id, entry, inc_bounce=True, extra_filter=_PCORG_OPEN_FILTER)
    if not n:
        return jsonify({"error": "fiche close ou supprimee entre-temps"}), 409
    return jsonify({"ok": True, "entry": entry})


@app.route('/api/pcorg/photo/<doc_id>', methods=['POST'])
@role_required("user")
def pcorg_add_photo(doc_id):
    """Photo(s) prise(s) sur le moment (telephone, tablette, poste) jointe(s)
    a la fiche, avec une legende facultative. Meme traitement que les photos
    des tablettes Field (validation Pillow, EXIF retire, 1920 px, miniature),
    rangees avec les captures cameras de l'evenement."""
    import field as _F
    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        return jsonify({"error": "photo requise"}), 400
    if len(files) > _F.FIELD_PHOTO_MAX_PER_BATCH:
        return jsonify({"error": f"{_F.FIELD_PHOTO_MAX_PER_BATCH} photos maximum"}), 400
    text = (request.form.get("text") or "").strip()[:PCORG_TEXT_MAX]
    doc, err = _pcorg_load_for_write(doc_id, projection={"status_code": 1, "category": 1, "event": 1, "year": 1})
    if err:
        return err
    ev = re.sub(r"[^A-Za-z0-9 _-]", "_", str(doc.get("event") or "cockpit"))
    yr = re.sub(r"[^0-9A-Za-z_-]", "_", str(doc.get("year") or datetime.now().year))
    photos = []
    for pf in files:
        try:
            url, thumb = _F._process_and_save_photo(pf, os.path.join(ev, yr))
        except _F.PhotoUploadError as e:
            msg = {"photo_too_large": "photo trop lourde (10 Mo max)",
                   "invalid_photo_format": "format de photo non pris en charge"}.get(e.code, e.code)
            return jsonify({"error": msg}), e.status
        photos.append({"photo": url, "thumb": thumb})
    if not text:
        text = "Photo jointe" if len(photos) == 1 else "%d photos jointes" % len(photos)
    entry = PH.make_entry(_pcorg_operator(request.user_payload), text)
    entry["photos"] = photos
    entry["photo"] = photos[0]["photo"]
    entry["thumb"] = photos[0]["thumb"]
    n = PH.append_entry(db["pcorg"], doc_id, entry, inc_bounce=True, extra_filter=_PCORG_OPEN_FILTER)
    if not n:
        return jsonify({"error": "fiche close ou supprimee entre-temps"}), 409
    return jsonify({"ok": True, "photos": photos})


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
    from cameras import HIK_PASSWORD
    try:
        oid = ObjectId(cam_id)
    except Exception:
        return jsonify({"error": "cam_id invalide"}), 400
    cam_doc = db["cockpit_cameras"].find_one({"_id": oid})
    if not cam_doc:
        return jsonify({"error": "Camera introuvable"}), 404

    fiche_doc = None
    if fiche_id:
        fiche_doc, err = _pcorg_load_for_write(
            fiche_id, projection={"event": 1, "year": 1, "status_code": 1, "category": 1})
        if err:
            return err

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

    photo_id = str(uuid.uuid4())[:8]
    ts = datetime.now(ZoneInfo("Europe/Paris"))
    ts_file = ts.strftime("%Y%m%d_%H%M%S")

    # Si fiche fournie, on stocke avec event/year dans field_photos
    # Sinon on stocke dans un dossier generique
    event_name = re.sub(r"[^A-Za-z0-9 _-]", "_", str((fiche_doc or {}).get("event", "cockpit")))
    year_val = re.sub(r"[^0-9A-Za-z_-]", "_", str((fiche_doc or {}).get("year", ts.year)))
    sub_dir = f"{event_name}/{year_val}"

    photos_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "uploads", "field_photos", event_name, year_val)
    os.makedirs(photos_dir, exist_ok=True)

    safe_cam_name = re.sub(r"[^A-Za-z0-9_-]", "_", cam_doc["name"])[:20]
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
        entry = PH.make_entry(_pcorg_operator(request.user_payload),
                              f"Capture camera {cam_doc['name']}", ts=ts, photo=photo_url)
        PH.append_entry(db["pcorg"], fiche_id, entry, inc_bounce=True)
        result["entry"] = entry

    return jsonify(result)


@app.route('/api/pcorg/update-gps/<doc_id>', methods=['POST'])
@role_required("user")
def pcorg_update_gps(doc_id):
    """Pose ou deplace la position d'une fiche. Le client transmet la zone et
    le carroyage recalcules au nouveau point (resolus cote carte)."""
    data = request.get_json(force=True) or {}
    try:
        gps = _pcorg_gps(data.get("lat"), data.get("lon"))
    except (ValueError, TypeError):
        return jsonify({"error": "lat/lon invalides"}), 400
    if gps is None:
        return jsonify({"error": "lat et lon requis"}), 400
    doc, err = _pcorg_load_for_write(
        doc_id, projection={"status_code": 1, "category": 1, "gps": 1, "area": 1,
                            "content_category.carroye": 1, "operator_id_create": 1})
    if err:
        return err
    if not _user_can_edit_fiche(request.user_payload, doc):
        return jsonify(_PCORG_READONLY_ERROR[0]), _PCORG_READONLY_ERROR[1]

    sets, owned = {"gps": gps}, {"gps"}
    changes = [{"field": "Position", "old": _pcorg_fmt_val(doc.get("gps")), "new": _pcorg_fmt_val(gps)}]
    if "area_desc" in data:
        new_area = (data.get("area_desc") or "").strip()
        old_area = ((doc.get("area") or {}).get("desc")) or ""
        if new_area != old_area:
            sets["area.desc"] = new_area
            owned.add("area.desc")
            changes.append({"field": "Zone", "old": old_area, "new": new_area})
    if "carroye" in data:
        new_c = (data.get("carroye") or "").strip()
        old_c = ((doc.get("content_category") or {}).get("carroye")) or ""
        if new_c != old_c:
            sets["content_category.carroye"] = new_c
            owned.add("content_category.carroye")
            changes.append({"field": "Carroye", "old": old_c, "new": new_c})

    verb = "deplacee" if doc.get("gps") else "definie"
    entry = PH.make_entry(_pcorg_operator(request.user_payload), f"Position {verb}",
                          system=True, changes=changes)
    n = PH.append_entry(db["pcorg"], doc_id, entry, set_fields=sets, owned=owned,
                        inc_bounce=True, extra_filter=_PCORG_OPEN_FILTER)
    if not n:
        return jsonify({"error": "fiche close ou supprimee entre-temps"}), 409
    return jsonify({"ok": True})


@app.route('/api/pcorg/set-urgency/<doc_id>', methods=['POST'])
@role_required("user")
def pcorg_set_urgency(doc_id):
    """Change le niveau d'urgence et consigne l'action dans la chronologie."""
    data = request.get_json(force=True) or {}
    niveau = data.get("niveau_urgence") or None
    if niveau and niveau not in VALID_URGENCY_LEVELS:
        return jsonify({"error": "niveau_urgence invalide"}), 400

    doc, err = _pcorg_load_for_write(doc_id, projection={"niveau_urgence": 1, "category": 1, "status_code": 1,
                                                         "operator_id_create": 1})
    if err:
        return err
    if not _user_can_edit_fiche(request.user_payload, doc):
        return jsonify(_PCORG_READONLY_ERROR[0]), _PCORG_READONLY_ERROR[1]

    old_niveau = doc.get("niveau_urgence") or None
    if old_niveau == niveau:
        return jsonify({"ok": True, "unchanged": True})

    cat = doc.get("category", "")
    action_text = (f"Niveau d'urgence : {_pcorg_urgency_label(cat, old_niveau)} "
                   f"→ {_pcorg_urgency_label(cat, niveau)}")
    entry = PH.make_entry(_pcorg_operator(request.user_payload), action_text, system=True)
    n = PH.append_entry(db["pcorg"], doc_id, entry, set_fields={"niveau_urgence": niveau},
                        owned={"niveau_urgence"}, inc_bounce=True, extra_filter=_PCORG_OPEN_FILTER)
    if not n:
        return jsonify({"error": "fiche close ou supprimee entre-temps"}), 409
    # Passage en UA/EU d'une fiche technique sans unite : proposition auto
    DA.maybe_auto_start(db, doc_id, trigger="urgence")
    return jsonify({"ok": True, "entry": entry})


@app.route('/api/pcorg/close/<doc_id>', methods=['POST'])
@role_required("user")
def pcorg_close(doc_id):
    """Cloture une fiche. Motif optionnel, consigne dans la meme entree que le
    changement de statut (format Prysm : "Statut: En cours -> Termine\\nmotif"),
    que l'affichage separe en pastille de statut + commentaire lisible.
    Droit : "Cloturer des fiches" de groupe, ou responsable de service de la
    categorie de la fiche (verifie apres chargement, la categorie en depend)."""
    data = request.get_json(silent=True) or {}
    motif = (data.get("comment") or "").strip()[:PCORG_TEXT_MAX]
    doc, err = _pcorg_load_for_write(
        doc_id, need_open=False,
        projection={"status_code": 1, "category": 1, "content_category": 1, "event": 1, "year": 1})
    if err:
        return err
    if not _user_can_close_fiche_cat(request.user_payload, doc.get("category")):
        return jsonify({"error": "Votre groupe n'autorise pas la cloture de fiches"}), 403
    if _pcorg_is_closed(doc):
        return jsonify({"error": "introuvable ou deja clos"}), 404

    user = request.user_payload
    operator_name = _pcorg_operator(user)
    now = datetime.now(ZoneInfo("Europe/Paris"))
    text = "Statut: En cours -> Terminé" + (f"\n{motif}" if motif else "")
    entry = PH.make_entry(operator_name, text, ts=now)
    n = PH.append_entry(
        db["pcorg"], doc_id, entry,
        set_fields={
            "close_ts": now,
            "close_iso": now.isoformat(),
            "status_code": 10,
            "operator_close": operator_name,
            "operator_id_close": user.get("email", ""),
            "cockpit_status_at": now,
        },
        owned={"status"}, inc_bounce=True, extra_filter=_PCORG_OPEN_FILTER,
    )
    if not n:
        # Deux clotures simultanees : la seconde ne pousse plus d'entree double
        return jsonify({"error": "introuvable ou deja clos"}), 404

    # Auto-disengage: reset tablet to "patrouille" when cockpit closes fiche
    _disengage_field_device(doc, doc_id)
    DA.on_close(db, doc_id)

    return jsonify({"ok": True})


@app.route('/api/pcorg/reopen/<doc_id>', methods=['POST'])
@role_required("user")
def pcorg_reopen(doc_id):
    """Rouvre une fiche close (erreur de cloture, reprise d'intervention).
    Motif obligatoire, meme droit que la cloture."""
    data = request.get_json(silent=True) or {}
    motif = (data.get("comment") or "").strip()[:PCORG_TEXT_MAX]
    if not motif:
        return jsonify({"error": "motif requis"}), 400
    doc, err = _pcorg_load_for_write(doc_id, need_open=False,
                                     projection={"status_code": 1, "category": 1})
    if err:
        return err
    if not _user_can_close_fiche_cat(request.user_payload, doc.get("category")):
        return jsonify({"error": "Votre groupe n'autorise pas la reouverture de fiches"}), 403
    if not _pcorg_is_closed(doc):
        return jsonify({"error": "fiche deja ouverte"}), 409

    now = datetime.now(ZoneInfo("Europe/Paris"))
    entry = PH.make_entry(_pcorg_operator(request.user_payload),
                          f"Statut: Terminé -> En cours\n{motif}", ts=now)
    n = PH.append_entry(
        db["pcorg"], doc_id, entry,
        set_fields={
            "status_code": 0, "close_ts": None, "close_iso": None,
            "operator_close": None, "operator_id_close": None,
            "cockpit_status_at": now,
        },
        owned={"status"}, inc_bounce=True, extra_filter={"status_code": 10},
    )
    if not n:
        return jsonify({"error": "fiche deja ouverte"}), 409
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

    # Le nom n'est unique que parmi les tablettes non revoquees : sans ce
    # filtre, une ancienne tablette revoquee du meme nom pouvait etre prise.
    # Meme regle que l'engagement : appairee sur l'evenement d'abord, sinon
    # une tablette qui le voit (SAISON pendant une epreuve active).
    try:
        from field import find_device_for_fiche
        device = find_device_for_fiche(db, device_name, event, year)
    except Exception:
        device = None
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
    operator_name = _pcorg_operator(user)
    now = datetime.now(timezone.utc)

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
        release_text = f"Liberation de {device_name} par {operator_name}"
        if cockpit_comment:
            release_text += f" : {cockpit_comment}"
        entry = PH.make_entry(operator_name, release_text)
        PH.append_entry(db["pcorg"], fiche_id, entry,
                        set_fields={"content_category.patrouille": ""},
                        owned={"content_category.patrouille"}, inc_bounce=True)

    return jsonify({"ok": True, "device_name": device_name})


@app.route('/api/pcorg/delete/<doc_id>', methods=['DELETE'])
@role_required("admin")
def pcorg_delete(doc_id):
    """Supprime une fiche d'intervention (admin uniquement).

    La fiche est d'abord archivee dans `pcorg_deleted` (qui, quand) : la
    suppression etait physique et sans trace. La tablette qui la portait
    comme fiche active est liberee.
    """
    doc = db["pcorg"].find_one({"_id": doc_id})
    if not doc:
        return jsonify({"error": "introuvable"}), 404
    user = request.user_payload
    archive = dict(doc)
    archive["_id"] = str(uuid.uuid4())
    archive["original_id"] = doc_id
    archive["deleted_at"] = datetime.now(timezone.utc)
    archive["deleted_by"] = user.get("email", "")
    archive["deleted_by_name"] = _pcorg_operator(user)
    db["pcorg_deleted"].insert_one(archive)
    result = db["pcorg"].delete_one({"_id": doc_id})
    if result.deleted_count == 0:
        return jsonify({"error": "introuvable"}), 404
    try:
        db["field_devices"].update_many(
            {"active_fiche_id": doc_id},
            {"$set": {"active_fiche_id": None}},
        )
    except Exception:
        logger.exception("Liberation tablette apres suppression fiche %s", doc_id)
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
    allowed = {
        "sous_classifications", "intervenants", "services",
        "fiche_simplifiee", "urgence_categories",
        "operateurs_internes", "donneurs_ordre",
    }
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
    # Le message d'erreur brut peut contenir des details SQL Server
    payload = request.user_payload
    if doc.get("last_error") and not (payload.get("app_role") == "admin" or payload.get("is_super_admin")):
        doc["last_error"] = "erreur de synchronisation"
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
    if not os.path.exists(python_exe):
        import sys as _sys
        python_exe = _sys.executable
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
    """Genere un resume de periode (KPIs + appel Claude) et le persiste.

    event et year sont optionnels : s'ils sont absents (ou si all_events=true),
    le resume porte sur tous les evenements / toutes annees.

    Body params optionnels :
    - model : override du modele Claude (whitelist : claude-sonnet-5, claude-sonnet-4-6,
      claude-opus-4-7, claude-haiku-4-5, ...). Permet A/B test.
    - dry_run : si true, renvoie le prompt assemble sans appeler Claude
      ni persister (synchrone, reponse {ok, summary, dry_run}).

    Hors dry_run, la generation part en tache de fond : reponse 202
    {ok, job, already_running}, suivi par GET /api/pcorg/summary/generate/status.
    429 {error: 'budget_exceeded'} si le budget IA mensuel bloquant est atteint.
    """
    data = request.get_json(silent=True) or {}
    all_events = bool(data.get("all_events"))
    event_raw = data.get("event")
    year_raw = data.get("year")
    event = (event_raw or "").strip() if isinstance(event_raw, str) else None
    if all_events or not event:
        event = None
    year = None
    if not all_events and year_raw not in (None, ""):
        try:
            year = int(year_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "year invalide"}), 400
    ts_start = _parse_period_dt(data.get("period_start"))
    ts_end = _parse_period_dt(data.get("period_end"))
    if not ts_start or not ts_end:
        return jsonify({"ok": False, "error": "period_start et period_end requis (ISO 8601)"}), 400
    if ts_end <= ts_start:
        return jsonify({"ok": False, "error": "period_end doit etre apres period_start"}), 400
    # Mode test : as_of permet de simuler le 'now' pour upcoming / attendance / doors.
    as_of_utc = None
    if data.get("as_of"):
        as_of_utc = _parse_period_dt(data.get("as_of"))

    model_override = data.get("model")  # validite verifiee dans le module
    dry_run = bool(data.get("dry_run"))

    user = request.user_payload or {}
    created_by_email = user.get("email", "") or ""
    created_by_name = (str(user.get("firstname", "") or "") + " " + str(user.get("lastname", "") or "")).strip()

    if dry_run:
        # Synchrone : aucun appel Claude (retro N-1 lue en cache seulement).
        try:
            doc = pcorg_summary.generate_period_summary(
                db, event, year, ts_start, ts_end, created_by_email, created_by_name,
                as_of_utc=as_of_utc, model=model_override, dry_run=True,
            )
        except Exception as e:
            logger.exception("pcorg_summary_generate (dry_run): erreur inattendue")
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, "summary": doc, "dry_run": True})

    # Generation reelle : 1 a 2 minutes d'appel Claude -> tache de fond, le
    # client suit l'avancement via /api/pcorg/summary/generate/status?job=.
    if not pcorg_summary.ANTHROPIC_API_KEY:
        return jsonify({"ok": False, "error": "COCKPIT_ANTHROPIC_API_KEY non configuree"}), 503
    try:
        pcorg_summary.check_ai_budget(db)
    except pcorg_summary.ClaudeError as e:
        return jsonify({"ok": False, "error": str(e)}), 429
    job_id, already = pcorg_summary.start_summary_job(
        db,
        {
            "event": event, "year": year, "ts_start": ts_start, "ts_end": ts_end,
            "created_by_email": created_by_email, "created_by_name": created_by_name,
            "as_of_utc": as_of_utc, "model": model_override,
        },
        owner=created_by_email or "anonymous",
    )
    return jsonify({"ok": True, "job": job_id, "already_running": bool(already)}), 202


@app.route('/api/pcorg/summary/generate/status', methods=['GET'])
@role_required("manager")
def pcorg_summary_generate_status():
    """Avancement d'une generation : etape, volume recu, secondes ecoulees,
    et le resume serialise une fois termine (status='done')."""
    user = request.user_payload or {}
    job = pcorg_summary.get_summary_job(
        request.args.get("job"), owner=(user.get("email") or "anonymous"),
    )
    if not job:
        return jsonify({"ok": False, "error": "job_inconnu"}), 404
    return jsonify({"ok": True, **job})


@app.route('/api/pcorg/summary/usage', methods=['GET'])
@role_required("admin")
def pcorg_summary_usage():
    """Agrege l'usage Claude des resumes generes sur une fenetre.

    Query params :
    - from : ISO datetime ou YYYY-MM-DD (defaut : 30j en arriere)
    - to   : ISO datetime ou YYYY-MM-DD (defaut : maintenant)
    - event, year : filtres optionnels (event filtre aussi l'agregat des retros N-1)

    Retourne :
    {
      from, to,
      by_model : {model: {input_tokens, output_tokens, cache_creation_input_tokens,
                          cache_read_input_tokens, calls, estimated_cost_usd}},
      summaries: {calls, ...}, retros: {calls, ...},
      total_estimated_cost_usd
    }

    NB : les tarifs MODEL_PRICING_USD_PER_MTOK sont figes en code, mettre a
    jour si Anthropic revise ses prix. Cache : creation +25 % du prix input,
    lecture cache -90 % du prix input (tarification standard cache 5 min).
    """
    from_raw = request.args.get("from")
    to_raw = request.args.get("to")
    now = datetime.now(timezone.utc)
    ts_to = pcorg_summary._parse_iso_dt(to_raw) if to_raw else now
    ts_from = pcorg_summary._parse_iso_dt(from_raw) if from_raw else (now - timedelta(days=30))
    if not ts_from or not ts_to:
        return jsonify({"ok": False, "error": "from / to invalides"}), 400

    event = request.args.get("event") or None
    year_q = request.args.get("year")
    year = None
    if year_q:
        try:
            year = int(year_q)
        except (TypeError, ValueError):
            year = None

    # Cout centralise (pcorg_summary.compute_cost_usd) : input_tokens EXCLUT
    # deja les tokens caches, les quatre compteurs s'additionnent. Sources :
    # resumes, retros N-1, analyses scans/frequentation et ai_usage_log.
    agg = pcorg_summary.aggregate_ai_usage(db, ts_from, ts_to, event=event, year=year)
    try:
        budget = pcorg_summary.ai_budget_status(db)
    except Exception as e:
        logger.warning("pcorg_summary_usage: budget illisible (%s)", e)
        budget = None

    return jsonify({
        "ok": True,
        "from": ts_from.isoformat(),
        "to": ts_to.isoformat(),
        "filters": {"event": event, "year": year},
        "summaries_calls": agg["counts"]["summaries"],
        "retros_calls": agg["counts"]["retros"],
        "scan_analyses_calls": agg["counts"]["scan_analyses"],
        "logged_calls": agg["counts"]["log"],
        "by_model": agg["by_model"],
        "by_feature": agg["by_feature"],
        "total_estimated_cost_usd": agg["total_estimated_cost_usd"],
        "unknown_pricing_models": agg["unknown_pricing_models"],
        "budget": budget,
        "pricing_table_usd_per_mtok": pcorg_summary.MODEL_PRICING_USD_PER_MTOK,
        "pricing_verified_on": pcorg_summary.PRICING_VERIFIED_ON,
        "pricing_note": ("cout = input x p_in + cache_creation x p_cache_write + "
                         "cache_read x p_cache_read + output x p_out "
                         "(input_tokens exclut les tokens caches)."),
    })


@app.route('/api/pcorg/summary/budget', methods=['GET'])
@role_required("admin")
def pcorg_summary_budget_get():
    """Budget IA mensuel + consommation du mois courant."""
    return jsonify({"ok": True, **pcorg_summary.ai_budget_status(db)})


@app.route('/api/pcorg/summary/budget', methods=['PUT'])
@role_required("admin")
def pcorg_summary_budget_set():
    """Body : {monthly_usd: number|null, block_when_exceeded: bool}."""
    data = request.get_json(silent=True) or {}
    user = request.user_payload or {}
    try:
        pcorg_summary.set_ai_budget(
            db, data.get("monthly_usd"), bool(data.get("block_when_exceeded")),
            updated_by_email=user.get("email") or "",
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, **pcorg_summary.ai_budget_status(db)})


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


@app.route('/api/pcorg/summary/recipients', methods=['GET'])
@role_required("manager")
def pcorg_summary_recipients():
    """Liste les utilisateurs Cockpit + groupes pour le picker d'envoi mail.

    On filtre les comptes sans email et on exclut les groupes systeme du
    dropdown (ils restent disponibles via API si besoin futur).
    """
    users = []
    cur = db['users'].find(
        {"roles_by_app.cockpit": {"$exists": True}, "email": {"$exists": True, "$ne": ""}},
        {"prenom": 1, "nom": 1, "email": 1, "service": 1, "roles_by_app.cockpit": 1},
    )
    for u in cur:
        full = ((u.get("prenom") or "") + " " + (u.get("nom") or "")).strip()
        if not full:
            full = u.get("email", "")
        users.append({
            "id": str(u["_id"]),
            "name": full,
            "email": u.get("email", ""),
            "service": u.get("service") or "",
            "role": (u.get("roles_by_app") or {}).get("cockpit") or "user",
        })
    users.sort(key=lambda x: x["name"].lower())

    groups = []
    for g in COL_GROUPS.find({}, {"name": 1}):
        name = g.get("name") or ""
        if name in SYSTEM_GROUP_NAMES:
            continue
        member_ids = [
            ug.get("user_id")
            for ug in COL_USER_GROUPS.find({"groups": g["_id"]}, {"user_id": 1})
        ]
        # Compte uniquement les membres avec email cockpit valide
        if member_ids:
            n = db['users'].count_documents({
                "_id": {"$in": member_ids},
                "roles_by_app.cockpit": {"$exists": True},
                "email": {"$exists": True, "$ne": ""},
            })
        else:
            n = 0
        groups.append({"id": str(g["_id"]), "name": name, "member_count": int(n)})
    groups.sort(key=lambda x: x["name"].lower())

    return jsonify({"ok": True, "users": users, "groups": groups})


def _resolve_recipients(user_ids, group_ids):
    """Resout (user_ids + group_ids) en une liste d'emails uniques.

    Filtre : user.roles_by_app.cockpit existe ET email present.
    """
    emails = []
    seen = set()

    def _add(email):
        if not email:
            return
        e = email.strip().lower()
        if e and e not in seen:
            seen.add(e)
            emails.append(email.strip())

    # Users directs
    direct_oids = []
    for uid in (user_ids or []):
        try:
            direct_oids.append(ObjectId(uid))
        except Exception:
            continue

    # Groups -> user_ids supplementaires
    group_oids = []
    for gid in (group_ids or []):
        try:
            group_oids.append(ObjectId(gid))
        except Exception:
            continue
    if group_oids:
        for ug in COL_USER_GROUPS.find(
            {"groups": {"$in": group_oids}}, {"user_id": 1}
        ):
            if ug.get("user_id"):
                direct_oids.append(ug["user_id"])

    if not direct_oids:
        return []

    cur = db['users'].find(
        {
            "_id": {"$in": direct_oids},
            "roles_by_app.cockpit": {"$exists": True},
            "email": {"$exists": True, "$ne": ""},
        },
        {"email": 1},
    )
    for u in cur:
        _add(u.get("email"))
    return emails


@app.route('/api/pcorg/morning-report/prefs', methods=['GET'])
@role_required("admin")
def pcorg_morning_report_get_prefs():
    prefs = pcorg_summary.get_morning_report_prefs(db)
    if hasattr(prefs.get("updated_at"), "isoformat"):
        prefs["updated_at"] = prefs["updated_at"].isoformat()
    return jsonify({"ok": True, **prefs})


@app.route('/api/pcorg/morning-report/prefs', methods=['PUT'])
@role_required("admin")
def pcorg_morning_report_set_pref():
    """Met a jour les preferences du rapport matinal.

    Body accepte deux formes :
      - {"global_enabled": true|false}  -> interrupteur global ON/OFF
      - {"user_id": "<oid>", "enabled": true|false} -> opt-in par user
    """
    data = request.get_json(silent=True) or {}
    user = request.user_payload or {}
    sender_email = user.get("email") or ""

    # Forme 1 : interrupteur global
    if "global_enabled" in data:
        try:
            pcorg_summary.set_morning_report_enabled(
                db, bool(data["global_enabled"]), updated_by_email=sender_email,
            )
        except Exception as e:
            logger.exception("pcorg_morning_report_set_pref (global): erreur")
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True})

    # Forme 2 : opt-in par user
    user_id = data.get("user_id")
    enabled = bool(data.get("enabled"))
    if not user_id:
        return jsonify({"ok": False, "error": "user_id ou global_enabled requis"}), 400
    try:
        pcorg_summary.set_morning_report_recipient(
            db, user_id, enabled, updated_by_email=sender_email,
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        logger.exception("pcorg_morning_report_set_pref: erreur inattendue")
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


@app.route('/api/pcorg/summary/<summary_id>/send', methods=['POST'])
@role_required("manager")
def pcorg_summary_send(summary_id):
    data = request.get_json(silent=True) or {}
    user_ids = data.get("user_ids") or []
    group_ids = data.get("group_ids") or []
    if not user_ids and not group_ids:
        return jsonify({"ok": False, "error": "Aucun destinataire selectionne"}), 400

    summary = pcorg_summary.get_summary(db, summary_id)
    if not summary:
        return jsonify({"ok": False, "error": "Resume introuvable"}), 404

    emails = _resolve_recipients(user_ids, group_ids)
    if not emails:
        return jsonify({"ok": False, "error": "Aucun email exploitable parmi les selections"}), 400

    user = request.user_payload or {}
    sender_email = user.get("email", "") or ""
    sender_name = (str(user.get("firstname", "") or "") + " " + str(user.get("lastname", "") or "")).strip()

    try:
        result = pcorg_summary_mail.send_summary_email(emails, summary)
    except pcorg_summary_mail.SmtpError as e:
        msg = str(e)
        code = 503 if "non configure" in msg else 502
        return jsonify({"ok": False, "error": msg}), code
    except Exception as e:
        logger.exception("pcorg_summary_send: erreur inattendue")
        return jsonify({"ok": False, "error": str(e)}), 500

    # Trace l'envoi dans le doc summary (audit)
    try:
        db['pcorg_summaries'].update_one(
            {"_id": summary_id},
            {"$push": {"email_sends": {
                "ts": datetime.now(timezone.utc),
                "by_email": sender_email,
                "by_name": sender_name,
                "to": emails,
                "user_ids": [str(u) for u in user_ids],
                "group_ids": [str(g) for g in group_ids],
                "ok": True,
                "smtp_host": result.get("smtp_host"),
            }}},
        )
    except Exception as e:
        logger.warning("pcorg_summary_send: trace email_sends a echoue : %s", e)

    return jsonify({
        "ok": True,
        "sent_count": result.get("sent_count"),
        "to": emails,
    })


################################################################################
# FEEDBACK SUR LES RAPPORTS (par section, immuable append-only)
################################################################################

@app.route('/api/pcorg/summary/<summary_id>/feedback', methods=['POST'])
@role_required("manager")
def pcorg_summary_feedback_add(summary_id):
    """Ajoute une entry de feedback sur une section d'un rapport.

    Body : { section, kind, target?, original_text?, corrected_text?,
             rule_text?, comment?, rating?,
             promote_to_memory?, memory_scope? }

    Si promote_to_memory=True et kind='rule' (ou rule_text fourni), une entry
    pcorg_ai_memory est aussi creee et son id retourne dans 'memory_id'. Le
    scope par defaut est event + section + phase=None.
    """
    data = request.get_json(silent=True) or {}
    section = data.get("section")
    kind = data.get("kind")
    user = request.user_payload or {}
    sender_email = user.get("email", "") or ""
    sender_name = (str(user.get("firstname", "") or "") + " " + str(user.get("lastname", "") or "")).strip()

    # Validation AVANT toute ecriture : une directive creee pour un feedback
    # ensuite refuse restait orpheline et active dans tous les prompts.
    try:
        pcorg_summary.validate_feedback(
            section, kind, corrected_text=data.get("corrected_text"),
            rule_text=data.get("rule_text"), rating=data.get("rating"),
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    # Si l'utilisateur demande aussi la promotion en memoire, on cree d'abord
    # la directive pour avoir son id, puis on attache l'id au feedback.
    memory_doc = None
    if data.get("promote_to_memory") and (data.get("rule_text") or kind == "rule"):
        rule_text = data.get("rule_text") or data.get("comment") or data.get("corrected_text")
        if not rule_text:
            return jsonify({"ok": False, "error": "rule_text requis pour promotion en memoire"}), 400
        # Scope par defaut : event courant du rapport + section visee.
        summary_doc = pcorg_summary.get_summary(db, summary_id)
        if not summary_doc:
            return jsonify({"ok": False, "error": "Resume introuvable"}), 404
        explicit_scope = data.get("memory_scope") or {}
        scope = {
            "event": explicit_scope.get("event", summary_doc.get("event")),
            "section": explicit_scope.get("section", section),
            "phase": explicit_scope.get("phase"),
            "year": explicit_scope.get("year"),
        }
        type_ = data.get("memory_type") or "principe"
        try:
            memory_doc = pcorg_ai_memory.promote_from_feedback(
                db, summary_id, data, rule_text,
                scope=scope, type_=type_,
                created_by_email=sender_email,
                created_by_name=sender_name,
            )
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            logger.exception("promote_from_feedback: erreur")
            return jsonify({"ok": False, "error": str(e)}), 500

    try:
        updated = pcorg_summary.add_feedback(
            db, summary_id,
            section=section,
            kind=kind,
            original_text=data.get("original_text"),
            corrected_text=data.get("corrected_text"),
            rule_text=data.get("rule_text"),
            comment=data.get("comment"),
            target=data.get("target"),
            rating=data.get("rating"),
            by_email=sender_email,
            by_name=sender_name,
            promoted_memory_id=str(memory_doc.get("_id")) if memory_doc else None,
        )
    except ValueError as e:
        updated, err = None, (str(e), 400)
    except Exception as e:
        logger.exception("add_feedback: erreur")
        updated, err = None, (str(e), 500)
    else:
        err = None if updated else ("Resume introuvable", 404)
    if err:
        # Filet : ne jamais laisser une directive sans son feedback.
        if memory_doc:
            try:
                pcorg_ai_memory.delete_directive(db, memory_doc.get("_id"))
            except Exception:
                logger.warning("feedback: directive orpheline %s non supprimee", memory_doc.get("_id"))
        return jsonify({"ok": False, "error": err[0]}), err[1]

    return jsonify({
        "ok": True,
        "summary": pcorg_summary._serialize_summary(updated, light=False),
        "memory": pcorg_ai_memory.serialize(memory_doc) if memory_doc else None,
    })


@app.route('/api/pcorg/summary/<summary_id>/quality-label', methods=['POST'])
@role_required("manager")
def pcorg_summary_quality_label(summary_id):
    """Pose un label qualite global (section=None) ou par section.
    Body : { label: "good|neutral|bad|null", section: "synthese|...|null" }
    """
    data = request.get_json(silent=True) or {}
    label = data.get("label")
    section = data.get("section") or None
    user = request.user_payload or {}
    sender_email = user.get("email", "") or ""
    try:
        updated = pcorg_summary.set_quality_label(
            db, summary_id, label, section=section, by_email=sender_email,
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        logger.exception("set_quality_label: erreur")
        return jsonify({"ok": False, "error": str(e)}), 500
    if not updated:
        return jsonify({"ok": False, "error": "Resume introuvable"}), 404
    return jsonify({
        "ok": True,
        "summary": pcorg_summary._serialize_summary(updated, light=False),
    })


@app.route('/api/pcorg/summary/<summary_id>/recommendation-status', methods=['POST'])
@role_required("manager")
def pcorg_summary_recommendation_status(summary_id):
    """Marque une recommandation comme appliquee/partielle/ignoree/non pertinente.
    Body : { bullet_index: int, status: "applied|partial|ignored|not_relevant|null" }
    """
    data = request.get_json(silent=True) or {}
    user = request.user_payload or {}
    sender_email = user.get("email", "") or ""
    try:
        updated = pcorg_summary.set_recommendation_status(
            db, summary_id,
            bullet_index=data.get("bullet_index"),
            status=data.get("status"),
            by_email=sender_email,
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        logger.exception("set_recommendation_status: erreur")
        return jsonify({"ok": False, "error": str(e)}), 500
    if not updated:
        return jsonify({"ok": False, "error": "Resume introuvable"}), 404
    return jsonify({
        "ok": True,
        "summary": pcorg_summary._serialize_summary(updated, light=False),
    })


@app.route('/api/pcorg/summary/<summary_id>/prompts', methods=['GET'])
@role_required("admin")
def pcorg_summary_prompts(summary_id):
    """Expose les prompts system + user persistes pour audit/debug.
    Admin uniquement (verbeux + sensible : peut contenir des donnees fiches).
    """
    data = pcorg_summary.get_prompts(db, summary_id)
    if data is None:
        return jsonify({"ok": False, "error": "Resume introuvable"}), 404
    return jsonify({"ok": True, **data})


################################################################################
# MEMOIRE CONSTITUTIONNELLE DE L'ASSISTANT IA
################################################################################

@app.route('/api/pcorg/ai-memory', methods=['GET'])
@role_required("manager")
def pcorg_ai_memory_list():
    """Liste les directives (filtres optionnels event/section/active)."""
    event = request.args.get("event") or None
    section = request.args.get("section") or None
    active_only = request.args.get("active_only") in ("1", "true", "yes")
    type_ = request.args.get("type") or None
    items = pcorg_ai_memory.list_directives(
        db, event=event, section=section, active_only=active_only, type_=type_,
    )
    return jsonify({
        "ok": True,
        "items": [pcorg_ai_memory.serialize(d) for d in items],
    })


@app.route('/api/pcorg/ai-memory', methods=['POST'])
@role_required("manager")
def pcorg_ai_memory_create():
    """Cree une directive.
    Body : { content, type?, scope?, active?, weight? }
    """
    data = request.get_json(silent=True) or {}
    user = request.user_payload or {}
    sender_email = user.get("email", "") or ""
    sender_name = (str(user.get("firstname", "") or "") + " " + str(user.get("lastname", "") or "")).strip()
    try:
        doc = pcorg_ai_memory.create_directive(
            db,
            content=data.get("content"),
            type_=data.get("type"),
            scope=data.get("scope"),
            active=data.get("active", True),
            weight=data.get("weight", 1.0),
            created_by_email=sender_email,
            created_by_name=sender_name,
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "item": pcorg_ai_memory.serialize(doc)})


@app.route('/api/pcorg/ai-memory/<directive_id>', methods=['PUT'])
@role_required("manager")
def pcorg_ai_memory_update(directive_id):
    """Met a jour une directive (content, type, scope, active, weight)."""
    data = request.get_json(silent=True) or {}
    user = request.user_payload or {}
    sender_email = user.get("email", "") or ""
    try:
        doc = pcorg_ai_memory.update_directive(
            db, directive_id, data, updated_by_email=sender_email,
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not doc:
        return jsonify({"ok": False, "error": "Directive introuvable"}), 404
    return jsonify({"ok": True, "item": pcorg_ai_memory.serialize(doc)})


@app.route('/api/pcorg/ai-memory/<directive_id>', methods=['DELETE'])
@role_required("admin")
def pcorg_ai_memory_delete(directive_id):
    """Supprime physiquement une directive. Admin uniquement."""
    ok = pcorg_ai_memory.delete_directive(db, directive_id)
    if not ok:
        return jsonify({"ok": False, "error": "Directive introuvable"}), 404
    return jsonify({"ok": True})


@app.route('/api/pcorg/ai-memory/stats', methods=['GET'])
@role_required("manager")
def pcorg_ai_memory_stats():
    """Compteurs synthetiques de la memoire (UI admin)."""
    return jsonify({"ok": True, **pcorg_ai_memory.stats(db)})


@app.route('/api/pcorg/ai-memory/suggest-rule', methods=['POST'])
@role_required("manager")
def pcorg_ai_memory_suggest_rule():
    """Reformule un commentaire en directive concise via Claude Haiku.

    Body : { comment, section?, event?, original_text?, corrected_text? }
    Retourne : { ok, rule_text, usage } ou erreur.
    """
    data = request.get_json(silent=True) or {}
    comment = (data.get("comment") or "").strip()
    if not comment and not (data.get("corrected_text") or "").strip():
        return jsonify({"ok": False, "error": "comment ou corrected_text requis"}), 400
    try:
        rule, usage = pcorg_ai_memory.suggest_rule_from_comment(
            comment=comment,
            section=data.get("section"),
            event=data.get("event"),
            original_text=data.get("original_text"),
            corrected_text=data.get("corrected_text"),
            model=data.get("model"),
            db=db,  # budget + journal ai_usage_log (feature 'suggest_rule')
            by_email=(request.user_payload or {}).get("email") or "",
        )
    except pcorg_summary.ClaudeError as e:
        msg = str(e)
        code = 503 if "ANTHROPIC_API_KEY" in msg else (429 if msg == "budget_exceeded" else 502)
        return jsonify({"ok": False, "error": msg}), code
    except Exception as e:
        logger.exception("suggest_rule_from_comment: erreur")
        return jsonify({"ok": False, "error": str(e)}), 500
    if not rule:
        return jsonify({"ok": False, "error": "reponse vide"}), 502
    return jsonify({"ok": True, "rule_text": rule, "usage": usage})


################################################################################
# EXPORT DATASET D'APPRENTISSAGE (fine-tuning futur)
################################################################################

@app.route('/api/pcorg/summary/export-dataset', methods=['GET'])
@role_required("admin")
def pcorg_summary_export_dataset():
    """Genere un dataset JSONL pour fine-tuning.

    Query params :
    - format : 'sft' (defaut) ou 'dpo'
    - from, to : ISO datetime
    - min_quality : 'good' pour ne garder que les rapports labellises bons OU
                    avec >=1 correction
    - include_memory : 1 pour concatener memory_block_text au system_prompt
    - prompt_version : entier pour filtrer
    """
    fmt = (request.args.get("format") or "sft").lower()
    if fmt not in ("sft", "dpo"):
        return jsonify({"ok": False, "error": "format invalide (sft|dpo)"}), 400
    ts_from = pcorg_summary._parse_iso_dt(request.args.get("from")) if request.args.get("from") else None
    ts_to = pcorg_summary._parse_iso_dt(request.args.get("to")) if request.args.get("to") else None
    min_quality = request.args.get("min_quality") or None
    include_memory = request.args.get("include_memory") in ("1", "true", "yes")
    pv_raw = request.args.get("prompt_version")
    pv = None
    if pv_raw:
        try:
            pv = int(pv_raw)
        except (TypeError, ValueError):
            pv = None

    def _stream():
        n = 0
        for sample in pcorg_summary.export_training_dataset(
            db, format_=fmt, ts_from=ts_from, ts_to=ts_to,
            min_quality=min_quality, include_memory=include_memory,
            prompt_version=pv,
        ):
            yield json.dumps(sample, ensure_ascii=False, default=pcorg_summary._json_default) + "\n"
            n += 1
        # Pas de footer (JSONL = un objet par ligne).

    from flask import Response
    return Response(
        _stream(),
        mimetype="application/x-jsonlines",
        headers={
            "Content-Disposition": (
                "attachment; filename=pcorg_dataset_" + fmt + "_"
                + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + ".jsonl"
            ),
        },
    )


@app.route('/api/pcorg/summary/export-stats', methods=['GET'])
@role_required("manager")
def pcorg_summary_export_stats():
    """Compteurs du dataset (volume de samples disponibles)."""
    return jsonify({"ok": True, **pcorg_summary.export_stats(db)})


################################################################################
# CONTROLE D'ACCES (HANDSHAKE)
################################################################################

COL_HSH_STRUCTURE = db['hsh_structure']
COL_HSH_ERREURS = db['hsh_erreurs']
COL_HSH_TX_AGG = db['hsh_transactions_agg']
COL_HSH_AGG_TITRES = db['hsh_agg_titres']
HSH_GLOBAL_ID = "___GLOBAL___"


@app.route('/circulation')
@role_required("manager")
def circulation_page():
    """Tableau de bord trafic plein ecran (TV poste de controle global).
    Consomme les memes feeds Waze que les widgets index (/alerts, /trafic/all_routes)."""
    payload = getattr(request, 'user_payload', {})
    user_roles = payload.get("roles", [])
    return render_template('circulation.html',
                           user_roles=user_roles,
                           user_firstname=payload.get("firstname", ""),
                           user_lastname=payload.get("lastname", ""),
                           user_email=payload.get("email", ""))


@app.route('/meteo-mur')
@role_required("manager")
def meteo_mur_page():
    """Tableau de bord meteo plein ecran (TV du PC Organisation).

    Meme role que /circulation pour le trafic : une page que personne ne
    pilote, qui se rafraichit seule et repond sans etre sollicitee aux
    questions du moment -- va-t-il pleuvoir, quand, faut-il agir.

    Consomme /api/meteo/mur, qui agrege previsions, vigilance, radar et
    humidite des sols en une seule charge."""
    payload = getattr(request, 'user_payload', {})
    return render_template('meteo_mur.html',
                           user_roles=payload.get("roles", []),
                           user_firstname=payload.get("firstname", ""),
                           user_lastname=payload.get("lastname", ""),
                           user_email=payload.get("email", ""))


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


@app.route('/watch-admin')
@role_required("admin")
def watch_admin_page():
    payload = getattr(request, 'user_payload', {})
    user_roles = payload.get("roles", [])
    return render_template('watch_admin.html',
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


# Presents sur site (compteur - correction - vehicules presents depuis la
# derniere remise a zero du compteur) : calcul unique dans presents_etat.py,
# partage avec la montre. Voir la docstring du module.
import presents_etat


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

    # 3b. Instantane du ___GLOBAL___ (compteur principal, corrections_compteurs /
    # corrections_vehicules, activation). Sans lui, le rejeu des presents d'une
    # edition archivee (live_frequentation.py) devait supposer zero correction et
    # estimer l'activation. Copie seulement s'il designe bien cet evenement : le
    # live-controle peut deja etre reconfigure pour l'edition suivante.
    g = db.data_access.find_one({"_id": HSH_GLOBAL_ID})
    if g and str(g.get("evenement") or "").strip() == evenement:
        snap = dict(g)
        snap["_id"] = "global"
        snap["archived_at"] = datetime.now(timezone.utc)
        db[f"hsh_archive_global_{archive_tag}"].replace_one({"_id": "global"}, snap, upsert=True)
        counts["global"] = 1

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
    if not event or not year or EC.is_saison(event):
        # SAISON (jours de visites libres sans billetterie ni course) : pas de projection
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
    hist_aliases = _event_hist_aliases(event)
    prev_hist_candidates = list(db['historique_controle'].find(
        {'type': 'frequentation', 'event': {'$in': hist_aliases}},
        sort=[('year', -1)]
    ))
    for cand in prev_hist_candidates:
        cand_year = cand.get('year')
        if isinstance(cand_year, (int, float)) and int(cand_year) < current_year_int:
            prev_hist_race_raw = cand.get('race')
            if not prev_hist_race_raw:
                portes_doc = db['historique_controle'].find_one(
                    {'type': 'portes', 'event': {'$in': hist_aliases}, 'year': cand_year},
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
        # Meme panier que /get_affluence : les produits references dans
        # globalHoraires.ticketing, sinon le taux de remplissage melange les
        # campings et les agregats TOTAL_* du referentiel.
        fill_pcts = []
        if prev_param:
            pts, final, _ = _fill_curve(prev_param, race_date_override=prev_hist_race_date,
                                        product_names=_ticketing_product_names(prev_param))
            pct = _interpolate_pct(pts, final, days_before)
            if pct:
                fill_pcts.append(pct)
        for cand in sorted(prev_candidates, key=lambda c: str(c.get('year', '')), reverse=True):
            try:
                cy = int(cand.get('year', ''))
            except (ValueError, TypeError):
                continue
            if cy < current_year_int and str(cand.get('year')) != str(prev_year_str):
                pts2, final2, _ = _fill_curve(cand, product_names=_ticketing_product_names(cand))
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

    # Soldes vehicules/enfants/accredites cumules depuis la derniere remise a
    # zero de CHAQUE compteur, jamais depuis minuit : un vehicule gare la
    # veille est toujours sur site. Calcul partage avec la montre.
    veh_enf_by_loc = presents_etat.soldes_categories(
        db, locations, doc.get("activation_timestamp"))

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
    corrections_veh = global_doc.get('corrections_vehicules', {}) or {}
    principal_id = global_doc.get('compteur_principal_id')
    principal_id_str = str(principal_id) if principal_id else None

    # Reference N-1 : edition precedente = historique_controle frequentation le
    # plus recent (annee < courante). Source de verite pour la presence N-1,
    # independante de parametrages (dont le doc N-1 peut manquer ou etre un stub
    # sans 'tickets', ex: LE MANS CLASSIC). L'historique peut etre stocke sous le
    # nom complet OU le code court (cf. _event_hist_aliases).
    race_n = None
    prev_race_ref = None
    prev_year_str = None
    event_days = []
    doc_n = None
    prev_hist_by_day = {}
    if event and year:
        doc_n = db['parametrages'].find_one({'event': event, 'year': year}) or {}
        gh = (doc_n.get('data') or {}).get('globalHoraires', {})
        event_days = [d.get('date') for d in gh.get('dates', []) if d.get('date')]
        race_n = _parse_race_date((doc_n.get('data') or {}).get('race') or gh.get('race'))
        try:
            cy = int(year)
        except (ValueError, TypeError):
            cy = None
        if cy is not None:
            # Serie N-1 au quart d'heure, chaque point date de l'instant qu'il
            # mesure (presents_etat.historique_n1, partage avec la montre). La
            # serie horaire faisait bouger la comparaison par paliers d'une
            # heure, et avec 45 min d'avance.
            n1 = presents_etat.historique_n1(db, event, cy, _event_hist_aliases(event))
            if n1 is not None:
                prev_year_str = str(n1['year'])
                prev_race_ref = n1['race']
                prev_hist_by_day = n1['par_jour']

    # data_access.timestamp est stocke en datetime NAIF representant de l'UTC.
    # ATTENTION : le serveur tourne en heure de Paris (datetime.now() = Paris),
    # donc une fenetre jour construite en naif-Paris et comparee a des timestamps
    # UTC decale la courbe du meme offset (+2h l'ete) : les points de 00h-02h
    # locaux sont stockes la veille en UTC et tombent hors fenetre. On construit
    # donc les bornes en Europe/Paris puis on convertit en UTC naif.
    tz_paris = ZoneInfo("Europe/Paris")

    def _paris_day_bounds_utc(d):
        """[minuit, minuit+1j) heure de Paris, convertis en UTC naif pour matcher
        data_access.timestamp (UTC naif)."""
        start_p = datetime(d.year, d.month, d.day, tzinfo=tz_paris)
        end_p = start_p + timedelta(days=1)
        return (start_p.astimezone(timezone.utc).replace(tzinfo=None),
                end_p.astimezone(timezone.utc).replace(tzinfo=None))

    today_local = datetime.now(tz_paris).date()
    # Jour de reference du graphique principal (par defaut aujourd'hui)
    try:
        target_date = datetime.strptime(target_date_param, '%Y-%m-%d').date() if target_date_param else today_local
    except Exception:
        target_date = today_local
    target_day_start, target_day_end = _paris_day_bounds_utc(target_date)

    # SAISON : ses jours publics sont les jours de VISITES LIBRES de toute
    # l'annee (02/10/2026). Les prendre comme "jours de l'evenement" ferait
    # partir la serie longue du premier de l'annee et calculer un pic par jour
    # de visites depuis janvier : on garde J-6 -> J+7 autour du jour cible.
    if event and EC.is_saison(event) and event_days:
        lo_s = (target_date - timedelta(days=6)).isoformat()
        hi_s = (target_date + timedelta(days=7)).isoformat()
        event_days = sorted(d for d in event_days if lo_s <= str(d)[:10] <= hi_s)

    # Plage "totale collecte" = depuis le premier jour public (ou 4j avant today) jusqu'a maintenant
    if event_days and not (event and EC.is_saison(event)):
        try:
            first_event_day = min(datetime.strptime(d, '%Y-%m-%d').date() for d in event_days)
            full_start, _ = _paris_day_bounds_utc(first_event_day)
        except Exception:
            full_start = target_day_start - timedelta(days=3)
    else:
        full_start = target_day_start - timedelta(days=3)
    full_end = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    # ------------------------------------------------------------------
    # Vehicules a deduire pour obtenir le VRAI present (humains = spectateurs
    # + enfants + accredites, MOINS les vehicules). On reconstruit, par zone
    # (Area/Venue) et par tranche de 5 min, le solde cumule
    # (entrees_vehicules - sorties_vehicules) issu de hsh_transactions_agg,
    # remonte aux zones via la hierarchie checkpoint -> parent_area/parent_venue
    # (meme logique que /counters). Le cumul part de la derniere remise a zero
    # du compteur de la zone, comme son 'current' : jamais de remise a zero
    # quotidienne, un vehicule gare la veille est toujours sur site.
    # Calcul partage avec la montre : presents_etat.Vehicules.
    # ------------------------------------------------------------------
    vehicules = presents_etat.Vehicules(
        db, locations, global_doc.get('activation_timestamp'),
        depuis_label=presents_etat.to_tranche_label(target_day_start).replace(
            hour=0, minute=0, second=0, microsecond=0))
    veh_present_at = vehicules.presents_at

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
        corr_veh = int(corrections_veh.get(lid, 0) or 0)

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
        # Pic du jour = plus haut RELEVE (toutes les 3 min), pas le max de la
        # serie tracee : celle-ci ne garde que le dernier releve de chaque quart
        # d'heure, si bien qu'un pic pouvait BAISSER en cours de quart d'heure
        # (37 500 a 13h09 puis 37 449 a 13h12, le 27/09/2026) et afficher moins
        # que le chiffre << presents >> vu quelques minutes plus tot.
        pic_today, pic_today_ts = 0, None
        for s in snaps:
            ts = s['timestamp']
            bucket = ts.replace(minute=(ts.minute // bucket_minutes) * bucket_minutes,
                                second=0, microsecond=0)
            key = bucket.isoformat() + 'Z'
            veh = veh_present_at(lid, ts, corr_veh)
            present = max(int(s.get('current', 0) or 0) - correction - veh, 0)
            if present > pic_today:
                pic_today, pic_today_ts = present, ts
            if key != last_bucket_key:
                series.append({'ts': key, 'present': present})
                last_bucket_key = key
            else:
                series[-1]['present'] = present
        # "current" = valeur la plus recente tout court (pas specifique au jour cible)
        latest = db['data_access'].find_one(
            {'requested_location_id': lid, 'requested_location_type': ltype},
            sort=[('timestamp', -1)],
            projection={'_id': 0, 'current': 1, 'timestamp': 1}
        )
        veh_now = 0
        if latest:
            veh_now = veh_present_at(lid, latest.get('timestamp'), corr_veh, until_end=True)
            current = max(int(latest.get('current', 0) or 0) - correction - veh_now, 0)
        else:
            current = 0

        zones.append({
            'location_id': lid,
            'location_type': ltype,
            'name': name,
            'is_principal': is_principal,
            'correction': correction,
            'current': current,
            'veh_excluded': veh_now,
            'pic_today': pic_today,
            # Instant du releve qui a donne le pic (UTC, 'Z'), pour l'heure affichee.
            'pic_today_ts': (pic_today_ts.isoformat() + 'Z') if pic_today_ts else None,
            'pic_n1_same_day': pic_n1_principal if is_principal else None,
            'max_n1_season': max_n1_principal if is_principal else None,
            'series': series,
        })

    # Resume par jour public (base sur la zone principale effective).
    # Le pic du jour est le plus haut releve, comme `pic_today` : le dernier
    # releve de chaque quart d'heure (la courbe) sous-estimait le pic et
    # pouvait le faire baisser.
    principal_zone = next((z for z in zones if z.get('is_principal')), None)
    principal_effective_id = principal_zone['location_id'] if principal_zone else None
    principal_effective_type = principal_zone['location_type'] if principal_zone else None
    days_summary = []
    for dstr in event_days:
        try:
            dd = datetime.strptime(dstr, '%Y-%m-%d').date()
        except Exception:
            continue
        pic_n = None
        if principal_effective_id and dd <= today_local:
            if dd == target_date and principal_zone is not None:
                # Reutilise le pic deja calcule depuis la serie principale bucketisee
                pt = principal_zone.get('pic_today')
                pic_n = pt if pt else None
            else:
                p_corr = int(corrections.get(principal_effective_id, 0) or 0)
                p_corr_veh = int(corrections_veh.get(principal_effective_id, 0) or 0)
                day_start, day_end = _paris_day_bounds_utc(dd)
                for s in db['data_access'].find(
                    {'requested_location_id': principal_effective_id,
                     'requested_location_type': principal_effective_type,
                     'timestamp': {'$gte': day_start, '$lt': day_end}},
                    {'_id': 0, 'timestamp': 1, 'current': 1}
                ):
                    s_veh = veh_present_at(principal_effective_id, s['timestamp'], p_corr_veh)
                    v = max(int(s.get('current', 0) or 0) - p_corr - s_veh, 0)
                    if pic_n is None or v > pic_n:
                        pic_n = v
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
    # Si WAHA n'a rien renvoye (timeout, session KO), on ne purge surtout pas
    # pour eviter de tout effacer sur un coup de mou reseau.
    if not remote:
        return jsonify({"synced": 0, "purged": 0,
                        "warning": "Aucun groupe renvoye par WAHA, purge ignoree"})
    synced_ids = set()
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
        synced_ids.add(gid)
    # Purge des groupes orphelins : presents en Mongo mais plus dans WAHA
    # (ex: changement de numero WhatsApp -> groupes de l'ancien compte caducs).
    purge = COL_WA_GROUPS.delete_many({"group_id": {"$nin": list(synced_ids)}})
    return jsonify({"synced": synced, "purged": purge.deleted_count})

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
# WIKI DES PROCÉDURES PC ORGA
#   Collections : cockpit_wiki_categories, cockpit_wiki_procedures
#   Consultation : /wiki (role user)   ·   Admin CRUD : /admin/wiki (role admin)
#   Seed initial : python seed_wiki.py
################################################################################
COL_WIKI_CAT = db["cockpit_wiki_categories"]
COL_WIKI_PROC = db["cockpit_wiki_procedures"]
try:
    COL_WIKI_PROC.create_index("code", unique=True)
    COL_WIKI_PROC.create_index([("status", 1)])
    COL_WIKI_CAT.create_index("key", unique=True)
except Exception as _e:
    logger.warning("wiki index init: %s", _e)

_WIKI_NODE_KINDS = {"start", "act", "watch", "engage", "ask", "end", "fork"}
_WIKI_MAX_DEPTH = 4  # garde-fou anti-récursion sur les embranchements imbriqués


def _wiki_clean_flow(flow, depth=0):
    """Nettoie/valide le logigramme : liste de noeuds {k,t}.
       - ask  : décision binaire, ajoute y/n
       - fork : embranchement, ajoute branches[{label, flow[]}] (récursif)"""
    out = []
    for n in (flow or []):
        if not isinstance(n, dict):
            continue
        k = (n.get("k") or "").strip()
        if k not in _WIKI_NODE_KINDS:
            continue
        node = {"k": k, "t": (n.get("t") or "").strip()}
        if k == "ask":
            node["y"] = (n.get("y") or "").strip()
            node["n"] = (n.get("n") or "").strip() or "—"
        elif k == "fork":
            branches = []
            if depth < _WIKI_MAX_DEPTH and isinstance(n.get("branches"), list):
                for b in n["branches"]:
                    if not isinstance(b, dict):
                        continue
                    branches.append({
                        "label": (b.get("label") or "").strip(),
                        "flow": _wiki_clean_flow(b.get("flow"), depth + 1),
                    })
            node["branches"] = branches
        out.append(node)
    return out


def _wiki_proc_from_payload(data):
    """Construit un document procédure propre à partir du JSON du front."""
    def _slist(v):
        return [str(x).strip() for x in (v or []) if str(x).strip()]
    return {
        "code": (data.get("code") or "").strip().upper(),
        "titre": (data.get("titre") or "").strip(),
        "dom": (data.get("dom") or "").strip(),
        "situation": (data.get("situation") or "").strip(),
        "questions": _slist(data.get("questions")),
        "acteurs": (data.get("acteurs") or "").strip(),
        "conduite": _slist(data.get("conduite")),
        "consigner": (data.get("consigner") or "").strip(),
        "pieges": (data.get("pieges") or "").strip(),
        "souscas": _slist(data.get("souscas")),
        "details": _slist(data.get("details")),
        "flow": _wiki_clean_flow(data.get("flow")),
        "status": "published" if data.get("status") == "published" else "draft",
    }


# ---- Pages ---------------------------------------------------------------
@app.route("/wiki")
@role_required("user")
def wiki_page():
    payload = getattr(request, "user_payload", {})
    return render_template("wiki.html",
                           user_roles=payload.get("roles", []),
                           user_firstname=payload.get("firstname", ""),
                           user_lastname=payload.get("lastname", ""),
                           user_email=payload.get("email", ""),
                           app_role=payload.get("app_role", "user"))


@app.route("/admin/wiki")
@role_required("admin")
def wiki_admin_page():
    payload = getattr(request, "user_payload", {})
    return render_template("wiki_admin.html",
                           user_roles=payload.get("roles", []),
                           user_firstname=payload.get("firstname", ""),
                           user_lastname=payload.get("lastname", ""),
                           user_email=payload.get("email", ""),
                           app_role=payload.get("app_role", "user"))


# ---- API consultation (utilisateur connecté) ----------------------------
@app.route("/api/wiki/categories", methods=["GET"])
@role_required("user")
def wiki_list_categories():
    cats = list(COL_WIKI_CAT.find().sort([("order", 1), ("key", 1)]))
    return jsonify([_pub(c) for c in cats])


@app.route("/api/wiki/procedures", methods=["GET"])
@role_required("user")
def wiki_list_procedures():
    procs = list(COL_WIKI_PROC.find({"status": "published"}).sort([("code", 1)]))
    return jsonify([_pub(p) for p in procs])


# ---- API admin : procédures (CRUD) --------------------------------------
@app.route("/api/wiki/admin/procedures", methods=["GET"])
@role_required("admin")
def wiki_admin_list_procedures():
    procs = list(COL_WIKI_PROC.find().sort([("code", 1)]))
    return jsonify([_pub(p) for p in procs])


@app.route("/api/wiki/admin/procedures", methods=["POST"])
@role_required("admin")
def wiki_admin_create_procedure():
    payload = getattr(request, "user_payload", {})
    data = request.get_json(force=True) or {}
    doc = _wiki_proc_from_payload(data)
    if not doc["code"] or not doc["titre"] or not doc["dom"]:
        return jsonify({"error": "code, titre et catégorie sont obligatoires"}), 400
    if COL_WIKI_PROC.find_one({"code": doc["code"]}):
        return jsonify({"error": "Ce code existe déjà"}), 409
    now = datetime.now(timezone.utc)
    who = payload.get("email") or "admin"
    doc.update({"version": 1, "created_at": now, "updated_at": now,
                "created_by": who, "updated_by": who})
    ins = COL_WIKI_PROC.insert_one(doc)
    doc["_id"] = ins.inserted_id
    return jsonify(_pub(doc)), 201


@app.route("/api/wiki/admin/procedures/<pid>", methods=["PUT"])
@role_required("admin")
def wiki_admin_update_procedure(pid):
    payload = getattr(request, "user_payload", {})
    try:
        oid = ObjectId(pid)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    cur = COL_WIKI_PROC.find_one({"_id": oid})
    if not cur:
        return jsonify({"error": "Introuvable"}), 404
    data = request.get_json(force=True) or {}
    patch = _wiki_proc_from_payload(data)
    if not patch["code"] or not patch["titre"] or not patch["dom"]:
        return jsonify({"error": "code, titre et catégorie sont obligatoires"}), 400
    if COL_WIKI_PROC.find_one({"code": patch["code"], "_id": {"$ne": oid}}):
        return jsonify({"error": "Ce code est déjà utilisé"}), 409
    patch["version"] = int(cur.get("version", 1)) + 1
    patch["updated_at"] = datetime.now(timezone.utc)
    patch["updated_by"] = payload.get("email") or "admin"
    res = COL_WIKI_PROC.find_one_and_update({"_id": oid}, {"$set": patch}, return_document=True)
    return jsonify(_pub(res))


@app.route("/api/wiki/admin/procedures/<pid>", methods=["DELETE"])
@role_required("admin")
def wiki_admin_delete_procedure(pid):
    try:
        oid = ObjectId(pid)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    r = COL_WIKI_PROC.delete_one({"_id": oid})
    if r.deleted_count == 0:
        return jsonify({"error": "Introuvable"}), 404
    return jsonify({"ok": True})


# ---- API admin : catégories ---------------------------------------------
@app.route("/api/wiki/admin/categories", methods=["GET"])
@role_required("admin")
def wiki_admin_list_categories():
    cats = list(COL_WIKI_CAT.find().sort([("order", 1), ("key", 1)]))
    return jsonify([_pub(c) for c in cats])


@app.route("/api/wiki/admin/categories", methods=["POST"])
@role_required("admin")
def wiki_admin_create_category():
    data = request.get_json(force=True) or {}
    key = re.sub(r"[^a-z0-9_-]+", "", (data.get("key") or "").strip().lower())
    label = (data.get("label") or "").strip()
    color = (data.get("color") or "#2563eb").strip()
    if not key or not label:
        return jsonify({"error": "clé et libellé obligatoires"}), 400
    if COL_WIKI_CAT.find_one({"key": key}):
        return jsonify({"error": "Cette clé existe déjà"}), 409
    order = int(data.get("order", COL_WIKI_CAT.count_documents({})))
    doc = {"key": key, "label": label, "color": color, "order": order}
    ins = COL_WIKI_CAT.insert_one(doc)
    doc["_id"] = ins.inserted_id
    return jsonify(_pub(doc)), 201


@app.route("/api/wiki/admin/categories/<cid>", methods=["PUT"])
@role_required("admin")
def wiki_admin_update_category(cid):
    try:
        oid = ObjectId(cid)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    data = request.get_json(force=True) or {}
    patch = {}
    if "label" in data:
        patch["label"] = (data.get("label") or "").strip()
    if "color" in data:
        patch["color"] = (data.get("color") or "").strip()
    if "order" in data:
        try:
            patch["order"] = int(data.get("order"))
        except Exception:
            pass
    if not patch:
        return jsonify({"error": "rien à mettre à jour"}), 400
    res = COL_WIKI_CAT.find_one_and_update({"_id": oid}, {"$set": patch}, return_document=True)
    if not res:
        return jsonify({"error": "Introuvable"}), 404
    return jsonify(_pub(res))


@app.route("/api/wiki/admin/categories/<cid>", methods=["DELETE"])
@role_required("admin")
def wiki_admin_delete_category(cid):
    try:
        oid = ObjectId(cid)
    except Exception:
        return jsonify({"error": "ID invalide"}), 400
    cat = COL_WIKI_CAT.find_one({"_id": oid})
    if not cat:
        return jsonify({"error": "Introuvable"}), 404
    used = COL_WIKI_PROC.count_documents({"dom": cat.get("key")})
    if used:
        return jsonify({"error": "Catégorie utilisée par %d procédure(s)" % used}), 409
    COL_WIKI_CAT.delete_one({"_id": oid})
    return jsonify({"ok": True})


################################################################################
# Exécution
################################################################################

if __name__ == "__main__":
    # Validation pour éviter debug=True en production
    if not DEV_MODE and app.debug:
        raise RuntimeError("L'application ne doit pas tourner en mode debug en production.")

    # Demarre le scheduler Alfred (resumes WhatsApp periodiques). En dev avec
    # use_reloader=True, Werkzeug fork 2 process : on ne lance qu'une seule fois
    # le scheduler, dans le process enfant (WERKZEUG_RUN_MAIN == 'true').
    if not DEV_MODE or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        try:
            alfred.start_scheduler()
        except Exception as e:
            logger.warning("Echec demarrage scheduler Alfred : %s", e)
        # Dispatch automatique : propositions expirees -> unite suivante / file
        try:
            DA.start_scheduler()
        except Exception as e:
            logger.warning("Echec demarrage scheduler dispatch auto : %s", e)
        # Planificateur PMV (envois programmes aux remorques, cf. pmv.py)
        try:
            import pmv
            pmv.start_scheduler()
        except Exception as e:
            logger.warning("Echec demarrage scheduler PMV : %s", e)

    # Lancement de l'application
    if DEV_MODE:
        logger.info(f"[DEV] Running TITAN Home in development mode on port {PORT}")
        app.run(debug=True, use_reloader=True, host="127.0.0.1", port=PORT)
    else:
        logger.warning(f"[PROD] Running TITAN Home on port {PORT}")
        serve(app, host="0.0.0.0", port=PORT)
