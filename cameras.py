# cameras.py -- Blueprint Cameras : configuration unique de toutes les cameras
#
# Modele (docs/claude/cameras.md) :
#   cockpit_cameras          un document par APPAREIL (IP) : marque, modele,
#                            site, profil d'identifiants, chemin d'envoi des
#                            evenements (camera_path, ex. /portehoux) et ses
#                            CANAUX (sources video) avec leurs USAGES.
#   cam_credential_profiles  identifiants partages ; mot de passe chiffre
#                            (camera_secrets.py, cle COCKPIT_CAMERA_KEY),
#                            jamais renvoye au navigateur.
# Usages d'un canal : catalogue ROLE_TYPES. L'usage "anpr" alimente la page
# LAPI (miroir dans anpr_camera_config, lu par anpr.py et cockpit_dispatch).
# Pilotage (capture, essuie-glace) : Hikvision seulement pour l'instant.
import os
import re
import time
import socket
import shutil
import logging
import ipaddress
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, render_template, Response, send_file
from pymongo import MongoClient
from bson.objectid import ObjectId

import camera_secrets

cameras_bp = Blueprint("cameras", __name__)
logger = logging.getLogger(__name__)

# Repli historique si un appareil n'a pas de profil d'identifiants
HIK_PASSWORD = os.getenv("HIK_PASSWORD", "")


# ---------------------------------------------------------------------------
# Auth helper (import from app at request time to avoid circular import)
# ---------------------------------------------------------------------------
def _check_admin():
    from app import (CODING, JWT_SECRET, JWT_ALGORITHM,
                     ROLE_HIERARCHY, ROLE_ORDER, APP_KEY)
    import jwt as pyjwt
    if CODING:
        sim_role = request.args.get("as", "admin")
        if sim_role not in ROLE_HIERARCHY:
            sim_role = "admin"
        sim_level = ROLE_HIERARCHY[sim_role]
        sim_roles = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= sim_level]
        request.user_payload = {
            "apps": ["cockpit"],
            "roles_by_app": {"cockpit": sim_role},
            "global_roles": [],
            "roles": sim_roles,
            "app_role": sim_role,
            "is_super_admin": False,
            "firstname": "Bruce",
            "lastname": "WAYNE",
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
    # super_admin global = admin partout (meme regle que role_required)
    from app import SUPER_ADMIN_ROLE, request_admin_grant
    if SUPER_ADMIN_ROLE in (payload.get("global_roles") or []):
        max_level = ROLE_HIERARCHY.get("admin", 3)
    # Page d'administration accordee par un groupe (app.ADMIN_PAGE_REGISTRY)
    if max_level < ROLE_HIERARCHY.get("admin", 3) and not request_admin_grant(payload):
        return jsonify({"error": "Admin required"}), 403
    effective_role = "admin" if max_level >= ROLE_HIERARCHY.get("admin", 3) else roles[0] if roles else "user"
    payload["roles"] = [r for r in ROLE_ORDER if ROLE_HIERARCHY.get(r, 0) <= ROLE_HIERARCHY.get(effective_role, 0)]
    payload["app_role"] = effective_role
    request.user_payload = payload
    return None


@cameras_bp.before_request
def _before():
    err = _check_admin()
    if err:
        return err


# ---------------------------------------------------------------------------
# Referentiels
# ---------------------------------------------------------------------------
BRANDS = {
    "hikvision": "Hikvision", "dahua": "Dahua", "bosch": "Bosch", "hanwha": "Hanwha",
    "axis": "Axis", "vivotek": "Vivotek", "uniview": "Uniview", "generic": "Generique",
}
# Marques que Cockpit sait piloter (capture, test, presets), toutes en Digest :
# Bosch (ONVIF + snap.jpg), Dahua (CGI), Hanwha (SUNAPI), Axis (VAPIX). Pas de commande de deplacement libre (fleches) : seuls
# les presets deja enregistres sur la camera peuvent etre rappeles.
CONTROLLABLE_BRANDS = {"hikvision", "bosch", "dahua", "hanwha", "axis"}
WIPER_BRANDS = {"hikvision"}
# Presets Hikvision reserves a des fonctions (94 = redemarrage, 95 = menu OSD,
# 92/93 = limites, 33-50 et 96-105 = scans, rondes, jour/nuit...) : jamais
# proposes ni rappeles. Les domes Hik les listent comme des presets ordinaires.
HIK_RESERVED_PRESETS = set(range(33, 51)) | set(range(92, 106))


# Appareils du sous-reseau 10.34.2.x qui ne sont PAS de la piste (valide par
# l'exploitation le 09/10/2026). Ne lever que le critere IP, jamais les autres.
PISTE_IP_EXCEPTIONS = {"10.34.2.180"}  # Technoparc Formation


class PisteCameraLocked(PermissionError):
    """Appareil des cameras de piste : aucun pilotage depuis Cockpit."""


def is_piste_device(doc):
    """Cameras de piste (groupe SeeTec "Pistes", enregistreurs XVR, 10.34.2.x).

    REGLE NON NEGOCIABLE (securite piste) : Cockpit n'envoie JAMAIS rien a ces
    appareils, pas meme une capture. Plusieurs criteres pour qu'un
    reclassement SeeTec ne les rende pas pilotables par erreur."""
    gp = str(doc.get("group_path") or "").lower()
    return ("pistes" in gp or "cam courses" in gp
            or str(doc.get("model") or "").upper().startswith(("DH-XVR", "DS-72", "DS-73", "DS-77", "DS-96"))
            or (str(doc.get("ip") or "").startswith("10.34.2.")
                and doc.get("ip") not in PISTE_IP_EXCEPTIONS))
VALID_PROTOCOLS = {"http", "https"}
IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9_\-]{1,60}$")
ALLOWED_ACTIONS = {"wiper"}

# Usages d'un canal. "needs_path" : l'usage repose sur les evenements envoyes
# par la camera au recepteur (ecoutehik2.py) sur le chemin de l'appareil.
# "no_path_sources" : valeurs du parametre "source" pour lesquelles Cockpit va
# lui-meme chercher les evenements (pas de chemin d'envoi, cf. anpr_lpv.py).
ROLE_TYPES = [
    {
        "type": "anpr", "label": "LAPI", "icon": "directions_car", "color": "#2563eb",
        "desc": "Lecture de plaques : alimente la page LAPI.", "needs_path": True,
        "no_path_sources": ["axis_lpv"],
        "params": [
            {"key": "source", "label": "Source des lectures", "kind": "select", "default": "push",
             "options": [["push", "Envoi par la camera (chemin d'envoi)"],
                         ["axis_lpv", "Consultation toutes les 5 min (Axis License Plate Verifier)"]]},
            {"key": "forward_role", "label": "Sens avant =", "kind": "select", "default": "entry",
             "options": [["entry", "Entree"], ["exit", "Sortie"]]},
            {"key": "lieu", "label": "Parking Vision", "kind": "lieu", "default": ""},
            {"key": "visible", "label": "Afficher sur la page LAPI", "kind": "bool", "default": True},
        ],
    },
    {
        "type": "fielddetection", "label": "Detection d'intrusion", "icon": "directions_walk", "color": "#d97706",
        "desc": "Intrusions dans les zones de la camera (pietons, vehicules) : page Detection.",
        "needs_path": True, "params": [],
    },
    {
        # Analyse du trafic : paire de photos a intervalle regulier analysee par
        # l'IA locale (bouchons). Prise par la tache planifiee "Cockpit -
        # Capture IA" (capture_ia.py), JAMAIS par Cockpit. Reglage depuis la
        # page IA ; les alertes passent par la centrale d'alertes (regle
        # "Trafic - ralenti / bouchon"), pas par l'usage.
        "type": "capture", "label": "Analyse IA (trafic / foule / secours)", "icon": "traffic", "color": "#7c3aed",
        "desc": "Photos a intervalle regulier analysees par l'IA : bouchons, foule trop dense ou personne au sol (page IA, alertes par la centrale).",
        "needs_path": False,
        "params": [
            {"key": "purpose", "label": "Analyse", "kind": "select", "default": "traffic",
             "options": [["traffic", "Trafic (bouchons)"], ["crowd", "Foule (trop de monde)"], ["secours", "Secours (personne au sol)"]]},
            {"key": "interval_min", "label": "Une mesure toutes les", "kind": "select", "default": "3",
             "options": [["1", "1 min"], ["2", "2 min"], ["3", "3 min"], ["5", "5 min"], ["10", "10 min"], ["15", "15 min"]]},
            {"key": "gap_s", "label": "Intervalle entre les 2 paires", "kind": "select", "default": "8",
             "options": [["5", "5 s"], ["8", "8 s"], ["10", "10 s"], ["15", "15 s"], ["20", "20 s"]]},
            {"key": "preset", "label": "Preset a rappeler avant (vide = aucun)", "kind": "text", "default": ""},
        ],
    },
]
ROLE_INDEX = {r["type"]: r for r in ROLE_TYPES}

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads", "camera_snapshots")

# Statut reseau : connexion TCP au port web, sans identifiant (voir anpr.py)
PROBE_TIMEOUT_S = 1.0
PROBE_TTL_S = 60
_probe_cache = {}

# Cameras 4G (routeur cellulaire, IP publique) : le modem s'endort sans trafic
# et met jusqu'a une trentaine de secondes a se reveiller. Connexion plus
# patiente et une seconde tentative (la premiere sert souvent de reveil).
CELL_CONNECT_S = 20
CELL_READ_S = 30
CELL_ATTEMPTS = 2
CELL_PROBE_TIMEOUT_S = 3.0


def is_cellular(doc):
    """Appareil joint par une IP publique = routeur 4G (27 au 09/10/2026)."""
    try:
        return not ipaddress.ip_address(str(doc.get("ip") or "")).is_private
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# MongoDB (lazy init)
# ---------------------------------------------------------------------------
_db = None
_col_cameras = None
_col_profiles = None


def _ensure_db():
    global _db, _col_cameras, _col_profiles
    if _db is not None:
        return
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    titan_env = os.getenv("TITAN_ENV", "dev").strip().lower()
    db = MongoClient(uri)["titan" if titan_env in {"prod", "production"} else "titan_dev"]
    _col_cameras = db["cockpit_cameras"]
    _col_profiles = db["cam_credential_profiles"]
    _col_cameras.create_index([("ip", 1), ("port", 1)], unique=True)
    _col_cameras.create_index("enabled")
    _col_cameras.create_index("camera_path")
    _col_profiles.create_index("name", unique=True)
    _db = db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _iso(dt):
    if not isinstance(dt, datetime):
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _oid(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


def _profiles_by_id():
    return {p["_id"]: p for p in _col_profiles.find()}


def _pub_device(doc, profiles):
    prof = profiles.get(doc.get("credential_profile_id"))
    brand = doc.get("brand", "generic")
    return {
        "_id": str(doc["_id"]),
        "name": doc.get("name", ""),
        "ip": doc.get("ip", ""),
        "port": doc.get("port", 80),
        "protocol": doc.get("protocol", "http"),
        "brand": brand,
        "model": doc.get("model", ""),
        "firmware": doc.get("firmware", ""),
        "site": doc.get("site", ""),
        "location": doc.get("location", ""),
        "vms_server": doc.get("vms_server", ""),
        "group_path": doc.get("group_path", ""),
        "camera_path": doc.get("camera_path", ""),
        "enabled": doc.get("enabled", True),
        "tags": doc.get("tags", []) or [],
        "notes": doc.get("notes", ""),
        "credential_profile_id": str(prof["_id"]) if prof else "",
        "profile_name": prof["name"] if prof else "",
        "controllable": brand in CONTROLLABLE_BRANDS and not is_piste_device(doc),
        "piste": is_piste_device(doc),
        "wiper": brand in WIPER_BRANDS,
        "cellular": is_cellular(doc),
        "seetec": bool((doc.get("source") or {}).get("seetec")),
        "channels": [
            {"no": c.get("no", i + 1), "name": c.get("name", ""), "enabled": c.get("enabled", True),
             "roles": c.get("roles") or []}
            for i, c in enumerate(doc.get("channels") or [{"no": 1, "name": doc.get("name", ""), "roles": []}])
        ],
        "updatedAt": _iso(doc.get("updatedAt")),
    }


def _pub_profile(p, counts):
    return {
        "_id": str(p["_id"]), "name": p.get("name", ""), "user": p.get("user", ""),
        "has_password": bool(p.get("secret")), "note": p.get("note", ""),
        "devices": counts.get(p["_id"], 0), "updatedAt": _iso(p.get("updatedAt")),
    }


def credentials_for(doc):
    """(utilisateur, mot de passe) d'un appareil : profil chiffre, sinon repli historique."""
    _ensure_db()
    prof = _col_profiles.find_one({"_id": doc.get("credential_profile_id")}) if doc.get("credential_profile_id") else None
    if prof:
        return prof.get("user") or "admin", camera_secrets.decrypt(prof.get("secret", ""))
    return doc.get("user", "admin"), HIK_PASSWORD


def make_hik_camera(doc, channel=None):
    """HikCamera pour un appareil (et un canal). Utilise aussi par la main courante."""
    from hik.hik_control import HikCamera
    if is_piste_device(doc):
        raise PisteCameraLocked("Camera de piste : aucun pilotage depuis Cockpit (securite piste)")
    user, password = credentials_for(doc)
    channels = doc.get("channels") or []
    cam = HikCamera(
        name=doc.get("name", ""),
        ip=doc["ip"],
        port=doc.get("port", 80),
        user=user,
        password=password,
        channel=int(channel or (channels[0].get("no") if channels else doc.get("channel", 1)) or 1),
        protocol=doc.get("protocol", "http"),
        brand=doc.get("brand", "hikvision"),
    )
    if is_cellular(doc):
        cam.timeout = (CELL_CONNECT_S, CELL_READ_S)
    return cam


def call_camera(doc, fn):
    """fn() avec reveil des cameras 4G : nouvelle tentative si la connexion
    echoue (pas sur un delai de lecture : la camera est alors joignable)."""
    attempts = CELL_ATTEMPTS if is_cellular(doc) else 1
    for i in range(attempts):
        try:
            return fn()
        except requests.exceptions.ConnectionError:
            if i + 1 >= attempts:
                raise
            logger.info("Camera 4G %s (%s) : pas de reponse, nouvelle tentative (reveil)", doc.get("name"), doc.get("ip"))


def camera_error(doc, e, what):
    """Message lisible pour l'operateur, journalise sur UNE ligne (une camera
    injoignable n'est pas un bug : pas de trace complete)."""
    if isinstance(e, requests.exceptions.ConnectionError):
        msg = ("Camera 4G en veille ou hors couverture : pas de reponse, reessayez dans une minute"
               if is_cellular(doc) else "Camera injoignable sur le reseau")
    elif isinstance(e, requests.exceptions.Timeout):
        msg = "La camera n'a pas repondu a temps"
    elif isinstance(e, requests.exceptions.HTTPError) and e.response is not None and e.response.status_code == 401:
        msg = "Identifiants refuses par la camera (profil a verifier)"
    elif isinstance(e, requests.exceptions.RequestException) or isinstance(e, RuntimeError):
        msg = str(e)
    else:
        logger.exception("%s impossible pour la camera %s (%s)", what, doc.get("name"), doc.get("ip"))
        return str(e)
    logger.warning("%s impossible pour la camera %s (%s) : %s", what, doc.get("name"), doc.get("ip"), msg)
    return msg


def _get_cam_doc(cam_id):
    _ensure_db()
    oid = _oid(cam_id)
    if not oid:
        return None, (jsonify({"error": "Identifiant invalide"}), 400)
    doc = _col_cameras.find_one({"_id": oid})
    if not doc:
        return None, (jsonify({"error": "Camera introuvable"}), 404)
    return doc, None


def _require_control(doc):
    if is_piste_device(doc):
        return jsonify({"error": "Camera de piste : aucun pilotage depuis Cockpit (securite piste)"}), 403
    if doc.get("brand") not in CONTROLLABLE_BRANDS:
        return jsonify({"error": "Pilotage non disponible pour la marque %s" % BRANDS.get(doc.get("brand"), doc.get("brand"))}), 400
    try:
        credentials_for(doc)
    except camera_secrets.CameraKeyMissing as e:
        return jsonify({"error": str(e)}), 503
    return None


def _clean_roles(roles):
    out = []
    seen = set()
    for r in roles or []:
        t = (r or {}).get("type")
        spec = ROLE_INDEX.get(t)
        if not spec or t in seen:
            continue
        seen.add(t)
        clean = {"type": t}
        for p in spec["params"]:
            v = r.get(p["key"], p["default"])
            if p["kind"] == "bool":
                v = bool(v)
            elif p["kind"] == "select":
                v = v if v in [o[0] for o in p["options"]] else p["default"]
            else:
                v = str(v or "")[:60]
            clean[p["key"]] = v
        out.append(clean)
    return out


def _clean_channels(channels):
    out = []
    used = set()
    for i, c in enumerate(channels or []):
        try:
            no = int(c.get("no", i + 1))
        except (TypeError, ValueError):
            continue
        if no < 1 or no > 64 or no in used:
            continue
        used.add(no)
        out.append({"no": no, "name": str(c.get("name") or "Canal %d" % no).strip()[:80],
                    "enabled": bool(c.get("enabled", True)), "roles": _clean_roles(c.get("roles"))})
    return sorted(out, key=lambda c: c["no"]) or [{"no": 1, "name": "Canal 1", "enabled": True, "roles": []}]


def _device_fields(data, partial):
    """Valide les champs modifiables d'un appareil. -> (champs, erreur)."""
    f = {}
    if not partial or "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            return None, "Nom requis"
        f["name"] = name[:120]
    if not partial or "ip" in data:
        ip = (data.get("ip") or "").strip()
        if not IP_RE.match(ip):
            return None, "Adresse IP invalide"
        f["ip"] = ip
    if "port" in data or not partial:
        try:
            port = int(data.get("port", 80))
        except (TypeError, ValueError):
            return None, "Port invalide"
        if port < 1 or port > 65535:
            return None, "Port invalide (1-65535)"
        f["port"] = port
    if "protocol" in data or not partial:
        f["protocol"] = data.get("protocol") if data.get("protocol") in VALID_PROTOCOLS else "http"
    if "brand" in data or not partial:
        f["brand"] = data.get("brand") if data.get("brand") in BRANDS else "generic"
    for key, size in (("model", 80), ("firmware", 40), ("site", 60), ("location", 120), ("notes", 1000)):
        if key in data:
            f[key] = str(data.get(key) or "").strip()[:size]
    if "camera_path" in data:
        p = (data.get("camera_path") or "").strip()
        if p and not p.startswith("/"):
            p = "/" + p
        if p and not PATH_RE.match(p):
            return None, "Chemin d'envoi invalide (ex. /portehoux : lettres, chiffres, - et _)"
        f["camera_path"] = p
    if "enabled" in data:
        f["enabled"] = bool(data.get("enabled"))
    if "tags" in data:
        tags = data.get("tags")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        f["tags"] = [str(t)[:30] for t in (tags or [])][:20]
    if "credential_profile_id" in data:
        pid = data.get("credential_profile_id")
        if pid:
            oid = _oid(pid)
            if not oid or not _col_profiles.find_one({"_id": oid}, {"_id": 1}):
                return None, "Profil d'identifiants inconnu"
            f["credential_profile_id"] = oid
        else:
            f["credential_profile_id"] = None
    if "channels" in data:
        f["channels"] = _clean_channels(data.get("channels"))
    return f, None


def _check_path_conflict(path, own_id=None):
    if not path:
        return None
    q = {"camera_path": path}
    if own_id:
        q["_id"] = {"$ne": own_id}
    other = _col_cameras.find_one(q, {"name": 1})
    return "Chemin %s deja utilise par %s" % (path, other["name"]) if other else None


def anpr_lpv_path(doc):
    """Chemin interne des passages d'une LAPI consultee (Axis LPV, sans chemin
    d'envoi) : cle de anpr_camera_config et de hik_anpr (anpr_lpv.py)."""
    return doc.get("camera_path") or "/lpv-" + str(doc.get("ip", "")).replace(".", "-")


def _sync_anpr_config():
    """Miroir des usages LAPI vers anpr_camera_config (lu par anpr.py et le dispatch).

    Une seule saisie (cette page) ; un chemin qui perd son usage LAPI est
    masque de la page LAPI (enabled False), son historique est conserve.
    """
    col = _db["anpr_camera_config"]
    active = set()
    for d in _col_cameras.find({"channels.roles.type": "anpr"}):
        for ch in d.get("channels") or []:
            role = next((r for r in ch.get("roles") or [] if r.get("type") == "anpr"), None)
            if not role:
                continue
            path = anpr_lpv_path(d) if role.get("source") == "axis_lpv" else d.get("camera_path")
            if not path:
                break
            active.add(path)
            col.update_one({"camera_path": path}, {"$set": {
                "camera_path": path,
                "label": ch.get("name") or d.get("name", path),
                "forward_role": role.get("forward_role", "entry"),
                "lieu": role.get("lieu", ""),
                "enabled": bool(role.get("visible", True)) and d.get("enabled", True),
                "camera_id": d["_id"],
                "channel": ch.get("no", 1),
            }}, upsert=True)
            break
    col.update_many({"camera_path": {"$nin": list(active)}}, {"$set": {"enabled": False}})


def _probe(ip, port):
    timeout = CELL_PROBE_TIMEOUT_S if is_cellular({"ip": ip}) else PROBE_TIMEOUT_S
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
@cameras_bp.route("/cameras")
def cameras_page():
    _ensure_db()
    payload = getattr(request, "user_payload", {})
    return render_template(
        "cameras.html",
        user_roles=payload.get("roles", []),
        user_firstname=payload.get("firstname", ""),
        user_lastname=payload.get("lastname", ""),
        user_email=payload.get("email", ""),
    )


# ---------------------------------------------------------------------------
# Appareils
# ---------------------------------------------------------------------------
# Cameras proposees a la main courante (liste ET vignettes : meme filtre)
_CAPTURE_QUERY = {"enabled": {"$ne": False}, "brand": {"$in": sorted(CONTROLLABLE_BRANDS)},
                  "credential_profile_id": {"$ne": None}}


def capture_camera_list():
    """Cameras dont la main courante peut joindre une capture (route
    /api/pcorg/cameras d'app.py, droits "user" comme la capture) : actives,
    marque pilotable, profil d'identifiants, jamais de piste. Position GPS lue
    dans la couche carto `cameras` (FeatureCollection, propriete IP) pour
    proposer les plus proches de la fiche en tete du selecteur."""
    _ensure_db()
    pos = {}
    layer = _db["cameras"].find_one({"type": "FeatureCollection"}, {"features.geometry": 1, "features.properties.IP": 1}) or {}
    for f in layer.get("features") or []:
        ip = ((f.get("properties") or {}).get("IP") or "").strip()
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if ip and len(coords) >= 2:
            pos.setdefault(ip, (coords[1], coords[0]))
    out = []
    for d in _col_cameras.find(_CAPTURE_QUERY, {"name": 1, "ip": 1, "location": 1, "site": 1, "group_path": 1, "model": 1}).sort("name", 1):
        if is_piste_device(d):
            continue
        p = pos.get(d.get("ip"))
        out.append({"_id": str(d["_id"]), "name": d.get("name", ""), "ip": d.get("ip", ""),
                    "location": d.get("location") or d.get("site", ""),
                    "lat": p[0] if p else None, "lon": p[1] if p else None})
    return out


def latest_snapshot_path(cam_id, channel=None):
    """Derniere capture (vignette) d'une camera proposee a la main courante,
    ou None. Meme filtre que capture_camera_list : un operateur ne peut pas
    lire la capture d'une camera hors liste (piste, desactivee...)."""
    if not re.match(r"^[0-9a-f]{24}$", cam_id or ""):
        return None
    _ensure_db()
    d = _col_cameras.find_one(dict(_CAPTURE_QUERY, _id=ObjectId(cam_id)), {"ip": 1, "group_path": 1, "model": 1})
    if not d or is_piste_device(d):
        return None
    path = os.path.join(UPLOAD_DIR, _snapshot_name(cam_id, channel))
    return path if os.path.isfile(path) else None


@cameras_bp.route("/api/cameras", methods=["GET"])
def list_cameras():
    """Tous les appareils (toutes marques). Tableau : format attendu aussi par
    le selecteur de camera de la main courante (pcorg.js)."""
    _ensure_db()
    profiles = _profiles_by_id()
    docs = _col_cameras.find().sort([("site", 1), ("name", 1)])
    return jsonify([_pub_device(d, profiles) for d in docs])


@cameras_bp.route("/api/cameras/meta", methods=["GET"])
def cameras_meta():
    _ensure_db()
    counts = {}
    for r in _col_cameras.aggregate([{"$group": {"_id": "$credential_profile_id", "n": {"$sum": 1}}}]):
        counts[r["_id"]] = r["n"]
    try:
        from vision_admin import VISION_LIEUX
    except Exception:
        VISION_LIEUX = []
    return jsonify({
        "profiles": [_pub_profile(p, counts) for p in _col_profiles.find().sort("name", 1)],
        "roles": ROLE_TYPES,
        "brands": BRANDS,
        "controllable": sorted(CONTROLLABLE_BRANDS),
        "sites": sorted(s for s in _col_cameras.distinct("site") if s),
        "lieux": list(VISION_LIEUX),
        "key_ok": camera_secrets.available(),
        "receiver": "http://10.24.0.251:8000",
    })


@cameras_bp.route("/api/cameras", methods=["POST"])
def create_camera():
    _ensure_db()
    data = request.get_json(silent=True) or {}
    fields, err = _device_fields(data, partial=False)
    if err:
        return jsonify({"error": err}), 400
    fields.setdefault("channels", _clean_channels(data.get("channels") or [{"no": 1, "name": fields["name"]}]))
    conflict = _check_path_conflict(fields.get("camera_path"))
    if conflict:
        return jsonify({"error": conflict}), 409
    now = datetime.now(timezone.utc)
    fields.setdefault("enabled", True)
    fields.update({"createdAt": now, "updatedAt": now, "source": {"seetec": False}})
    try:
        res = _col_cameras.insert_one(fields)
    except Exception as e:
        if "duplicate key" in str(e).lower():
            return jsonify({"error": "Une camera avec cette IP et ce port existe deja"}), 409
        return jsonify({"error": str(e)}), 500
    _sync_anpr_config()
    return jsonify(_pub_device(_col_cameras.find_one({"_id": res.inserted_id}), _profiles_by_id())), 201


@cameras_bp.route("/api/cameras/<cam_id>", methods=["PUT"])
def update_camera(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    fields, msg = _device_fields(data, partial=True)
    if msg:
        return jsonify({"error": msg}), 400
    conflict = _check_path_conflict(fields.get("camera_path"), doc["_id"])
    if conflict:
        return jsonify({"error": conflict}), 409
    fields["updatedAt"] = datetime.now(timezone.utc)
    try:
        _col_cameras.update_one({"_id": doc["_id"]}, {"$set": fields, "$unset": {"password": ""}})
    except Exception as e:
        if "duplicate key" in str(e).lower():
            return jsonify({"error": "Une camera avec cette IP et ce port existe deja"}), 409
        return jsonify({"error": str(e)}), 500
    _sync_anpr_config()
    after = _col_cameras.find_one({"_id": doc["_id"]})
    out = _pub_device(after, _profiles_by_id())
    # Nom, IP, canaux : repercutes sur les points de la couche GPS
    if any(k in fields for k in ("name", "ip", "channels")):
        try:
            out["geo_sync"] = _sync_layer(doc, after)
        except Exception as e:
            logger.warning("Couche GPS non mise a jour pour %s : %s", doc.get("name"), e)
    return jsonify(out)


@cameras_bp.route("/api/cameras/<cam_id>", methods=["DELETE"])
def delete_camera(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    _col_cameras.delete_one({"_id": doc["_id"]})
    _sync_anpr_config()
    return jsonify({"ok": True})


@cameras_bp.route("/api/cameras/bulk", methods=["POST"])
def bulk_update():
    """Edition de masse : {ids: [...], set: {credential_profile_id, site, enabled,
    protocol, port, brand, location}} ou {ids: [...], delete: true}."""
    _ensure_db()
    data = request.get_json(silent=True) or {}
    ids = [o for o in (_oid(i) for i in data.get("ids") or []) if o]
    if not ids:
        return jsonify({"error": "Aucune camera selectionnee"}), 400
    if data.get("delete"):
        res = _col_cameras.delete_many({"_id": {"$in": ids}})
        _sync_anpr_config()
        return jsonify({"ok": True, "deleted": res.deleted_count})
    allowed = {"credential_profile_id", "site", "enabled", "protocol", "port", "brand", "location"}
    patch_in = {k: v for k, v in (data.get("set") or {}).items() if k in allowed}
    if not patch_in:
        return jsonify({"error": "Rien a modifier"}), 400
    fields, err = _device_fields(patch_in, partial=True)
    if err:
        return jsonify({"error": err}), 400
    fields["updatedAt"] = datetime.now(timezone.utc)
    res = _col_cameras.update_many({"_id": {"$in": ids}}, {"$set": fields})
    _sync_anpr_config()
    return jsonify({"ok": True, "modified": res.modified_count})


@cameras_bp.route("/api/cameras/reachability", methods=["GET"])
def reachability():
    """{id: {ok, at}} pour les appareils actifs ; resultat garde 1 min par IP."""
    _ensure_db()
    docs = list(_col_cameras.find({"enabled": {"$ne": False}}, {"ip": 1, "port": 1}))
    now_m = time.monotonic()
    stale = list({(d["ip"], d.get("port", 80)) for d in docs
                  if (d["ip"], d.get("port", 80)) not in _probe_cache
                  or now_m - _probe_cache[(d["ip"], d.get("port", 80))][0] >= PROBE_TTL_S})
    if stale:
        with ThreadPoolExecutor(max_workers=48) as pool:
            results = list(pool.map(lambda t: _probe(*t), stale))
        at = datetime.now(timezone.utc)
        for key, ok in zip(stale, results):
            _probe_cache[key] = (time.monotonic(), ok, at)
    out = {}
    for d in docs:
        hit = _probe_cache.get((d["ip"], d.get("port", 80)))
        if hit:
            out[str(d["_id"])] = {"ok": hit[1], "at": _iso(hit[2])}
    return jsonify(out)


# ---------------------------------------------------------------------------
# Carte des cameras et capture de masse
# ---------------------------------------------------------------------------
@cameras_bp.route("/api/cameras/map", methods=["GET"])
def cameras_map():
    """Appareils ayant une position : manuelle (page Detection,
    `cockpit_cameras.map`) ou couche GPS `titan.cameras` (par IP puis nom).
    Une IP = un lieu (regle de detection._device_position)."""
    _ensure_db()
    import detection
    profiles = _profiles_by_id()
    out, unplaced = [], 0
    for d in _col_cameras.find():
        pos, source, geo = detection._device_position(_db, d)
        if not pos:
            unplaced += 1
            continue
        pub = _pub_device(d, profiles)
        # Derniere capture de chaque canal (vignettes de la bulle)
        snaps = {}
        for c in pub["channels"]:
            f = os.path.join(UPLOAD_DIR, _snapshot_name(pub["_id"], c["no"]))
            if os.path.isfile(f):
                snaps[str(c["no"])] = _iso(datetime.fromtimestamp(os.path.getmtime(f), timezone.utc))
        snap = os.path.join(UPLOAD_DIR, _snapshot_name(pub["_id"], None))
        pub.update({
            "lat": pos["lat"], "lng": pos["lng"], "map_source": source,
            "geo_type": (geo or {}).get("type", ""),
            "snap_at": _iso(datetime.fromtimestamp(os.path.getmtime(snap), timezone.utc)) if os.path.isfile(snap) else None,
            "snaps": snaps,
        })
        out.append(pub)
    return jsonify({"cameras": out, "unplaced": unplaced})


# ---------------------------------------------------------------------------
# Couche GPS titan.cameras (UN document FeatureCollection, partage avec les
# autres outils cartographiques). Conventions : un point par CANAL ("SEETEC
# NAME" = nom du canal, "NEW ID" / "NEW NAME" = numero et reste du nom),
# rapprochement par "IP", "_id_feature" unique. Cockpit cree les points d'un
# appareil place sur la carte et repercute ses changements de nom / IP /
# canaux ; il ne supprime JAMAIS un point.
# ---------------------------------------------------------------------------
# "120 - [F] - Porte Houx", "048- P9C Siege", "151 Giratoire Technoparc"
_NAME_RE = re.compile(r"^\s*(\d+[A-Za-z]?)(?:\s*-\s*|\s+)(.+?)\s*$")
_PTZ_MODEL_RE = re.compile(r"(2DE|2DF|2SF|2DY|SD\d|PTZ|AUTODOME|SNP)", re.I)


def _split_name(name):
    m = _NAME_RE.match(name or "")
    return (m.group(1), m.group(2)) if m else ("", (name or "").strip())


def _geo_type(doc, ch_name, first):
    if is_cellular(doc):
        return "DOME 4G"
    if "interphone" in (doc.get("name") or "").lower() or "intercom" in (doc.get("model") or "").lower():
        return "INTERPHONE"
    if "[F]" in (ch_name or ""):
        return "FIXE"
    return "DOME" if first and _PTZ_MODEL_RE.search(doc.get("model") or "") else "FIXE"


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _layer_points(ip):
    d = _db["cameras"].find_one({}, {"features": 1}) or {}
    return [f for f in d.get("features") or [] if str((f.get("properties") or {}).get("IP") or "").strip() == ip]


def _new_feature(doc, ch, first, lng, lat):
    nid, nname = _split_name(ch.get("name") or doc.get("name"))
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"TYPE": _geo_type(doc, ch.get("name"), first), "NEW ID": nid, "NEW NAME": nname,
                           "SEETEC NAME": ch.get("name") or doc.get("name", ""), "IP": str(doc.get("ip") or "").strip(),
                           "_id_feature": str(ObjectId())}}


def _layer_set_props(fid, props):
    _db["cameras"].update_one({"features.properties._id_feature": fid},
                              {"$set": {"features.$[f].properties.%s" % k: v for k, v in props.items()}},
                              array_filters=[{"f.properties._id_feature": fid}])


def _invalidate_geo():
    try:
        import detection
        detection._geo_cache["by_ip"] = None
    except Exception:
        pass


def _sync_layer(before, after, dry=False):
    """Repercute nom / IP / canaux d'un appareil sur ses points de la couche.
    Rapprochement canal <-> point : 1) par nom (ancien ou nouveau nom du canal
    = SEETEC NAME), 2) points restants attribues aux canaux restants dans
    l'ordre, 3) canal toujours sans point : point cree au meme endroit (une
    IP = un lieu). Un point existant n'est donc jamais duplique ni supprime.
    dry=True : ne modifie rien, renvoie ce qui serait fait."""
    ip_old = str(before.get("ip") or "").strip()
    pts = _layer_points(ip_old) if ip_old else []
    if not pts:
        return None
    ip_new = str(after.get("ip") or "").strip()
    old_ch = {c.get("no"): c.get("name") or "" for c in before.get("channels") or []}
    new_chs = after.get("channels") or [{"no": 1, "name": after.get("name", "")}]
    fid = lambda p: p["properties"]["_id_feature"]
    pairs, used = {}, set()
    for i, ch in enumerate(new_chs):
        want = {_norm(old_ch.get(ch.get("no"))), _norm(ch.get("name"))} - {""}
        hit = next((p for p in pts if fid(p) not in used and _norm(p["properties"].get("SEETEC NAME")) in want), None)
        if hit is not None:
            pairs[i] = hit
            used.add(fid(hit))
    free = [p for p in pts if fid(p) not in used]
    for i in range(len(new_chs)):
        if i not in pairs and free:
            pairs[i] = free.pop(0)
            used.add(fid(pairs[i]))
    lng, lat = pts[0]["geometry"]["coordinates"][:2]
    updated, created, ops = 0, 0, []
    for i, ch in enumerate(new_chs):
        name = ch.get("name") or after.get("name", "")
        nid, nname = _split_name(name)
        props = {"IP": ip_new, "SEETEC NAME": name, "NEW ID": nid, "NEW NAME": nname}
        hit = pairs.get(i)
        if hit is not None:
            diff = {k: v for k, v in props.items() if hit["properties"].get(k) != v}
            if diff:
                ops.append(("maj", fid(hit), {k: (hit["properties"].get(k), v) for k, v in diff.items()}))
                if not dry:
                    _layer_set_props(fid(hit), diff)
                updated += 1
        else:
            ops.append(("creation", name, None))
            if not dry:
                _db["cameras"].update_one({"_id": _db["cameras"].find_one({}, {"_id": 1})["_id"]},
                                          {"$push": {"features": _new_feature(after, ch, i == 0, lng, lat)}})
            created += 1
    # Points en surnombre (plus de points que de canaux) : IP seulement
    for p in pts:
        if fid(p) not in used and ip_new != ip_old:
            ops.append(("maj", fid(p), {"IP": (ip_old, ip_new)}))
            if not dry:
                _layer_set_props(fid(p), {"IP": ip_new})
            updated += 1
    if (updated or created) and not dry:
        _invalidate_geo()
        logger.info("Couche GPS : %s (%s) -> %d point(s) mis a jour, %d cree(s)", after.get("name"), ip_new, updated, created)
    return {"updated": updated, "created": created, "ops": ops} if dry else {"updated": updated, "created": created}


@cameras_bp.route("/api/cameras/<cam_id>/geo", methods=["PUT"])
def set_geo(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        lat, lng = float(data["lat"]), float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Position invalide"}), 400
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify({"error": "Position invalide"}), 400
    return jsonify(place_device(doc, lat, lng))


def place_device(doc, lat, lng):
    """Place / deplace l'appareil. Ecrit DIRECTEMENT dans la couche GPS
    `titan.cameras` : tous les points de son IP (un point par canal pour les
    multi-capteurs ; une IP = un lieu), crees s'il n'y figure pas encore ; la
    position manuelle de la page Detection est effacee (elle suit la couche).
    Utilise aussi par le bouton Placer de la page Detection. Chaque
    deplacement est trace dans `cameras_geo_edits`."""
    _ensure_db()
    ip = str(doc.get("ip") or "").strip()
    layer = _db["cameras"]
    old = [{"fid": (f.get("properties") or {}).get("_id_feature"), "coordinates": (f.get("geometry") or {}).get("coordinates")}
           for d in layer.find({"features.properties.IP": ip}, {"features": 1})
           for f in d.get("features") or [] if str((f.get("properties") or {}).get("IP") or "").strip() == ip] if ip else []
    now = datetime.now(timezone.utc)
    p = getattr(request, "user_payload", {}) or {}
    who = ("%s %s" % (p.get("firstname", ""), p.get("lastname", ""))).strip() or p.get("email", "")
    # Anciennes positions par canal (page Detection) : effacees, si le tableau existe
    unset_ch = {"channels.$[].map": ""} if isinstance(doc.get("channels"), list) and doc["channels"] else {}
    if old:
        layer.update_many({"features.properties.IP": ip},
                          {"$set": {"features.$[f].geometry.coordinates": [lng, lat]}},
                          array_filters=[{"f.properties.IP": ip}])
        _col_cameras.update_one({"_id": doc["_id"]}, {"$unset": dict({"map": ""}, **unset_ch)})
        source = "geo"
    elif ip and layer.find_one({}, {"_id": 1}):
        # Absente de la couche : un point par canal, au meme endroit
        chs = doc.get("channels") or [{"no": 1, "name": doc.get("name", "")}]
        feats = [_new_feature(doc, ch, i == 0, lng, lat) for i, ch in enumerate(chs)]
        layer.update_one({"_id": layer.find_one({}, {"_id": 1})["_id"]}, {"$push": {"features": {"$each": feats}}})
        _col_cameras.update_one({"_id": doc["_id"]}, {"$unset": dict({"map": ""}, **unset_ch)})
        old = [{"fid": f["properties"]["_id_feature"], "coordinates": None, "created": True} for f in feats]
        source = "geo"
    else:
        upd = {"$set": {"map": {"lat": lat, "lng": lng}}}
        if unset_ch:
            upd["$unset"] = unset_ch
        _col_cameras.update_one({"_id": doc["_id"]}, upd)
        source = "manual"
    _db["cameras_geo_edits"].insert_one({"camera_id": doc["_id"], "name": doc.get("name"), "ip": ip, "source": source,
                                         "old": old or doc.get("map"), "new": [lng, lat], "by": who, "at": now})
    _invalidate_geo()  # relu tout de suite par Detection et la carte
    created = sum(1 for o in old if o.get("created"))
    logger.info("Camera %s (%s) placee par %s : %d point(s) de la couche GPS (%d cree(s))", doc.get("name"), ip, who, len(old), created)
    return {"ok": True, "lat": lat, "lng": lng, "map_source": source, "points": len(old), "created": created}


# Une seule capture de masse a la fois, dans ses propres threads : elle ne
# monopolise pas les threads du serveur web (une camera 4G peut prendre 1 min).
CAPTURE_ALL_WORKERS = 8
_capture_job = {"running": False}
_capture_lock = threading.Lock()


def _capture_one(doc, no=None):
    cam = make_hik_camera(doc, no)
    cam_id = str(doc["_id"])
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    save_path = os.path.join(UPLOAD_DIR, "%s_ch%d_%s.jpg" % (cam_id, cam.channel, time.strftime("%Y%m%d_%H%M%S")))
    call_camera(doc, lambda: cam.capture_image(save_path))
    shutil.copy2(save_path, os.path.join(UPLOAD_DIR, _snapshot_name(cam_id, cam.channel)))


def _capture_channels(doc):
    """Numeros des canaux actifs d'un appareil (au moins le 1er)."""
    nos = [int(c.get("no", i + 1)) for i, c in enumerate(doc.get("channels") or []) if c.get("enabled", True) is not False]
    return nos or [1]


def _run_capture_job(docs):
    # Un appareil a la fois par thread, ses canaux l'un apres l'autre : un
    # enregistreur a 5 cameras n'est pas sollicite 5 fois en meme temps
    def one(doc):
        nos = _capture_channels(doc)
        for no in nos:
            try:
                _capture_one(doc, no)
                ok, err = True, ""
            except Exception as e:
                ok, err = False, camera_error(doc, e, "Capture de masse")
            with _capture_lock:
                _capture_job["done"] += 1
                if ok:
                    _capture_job["ok"] += 1
                else:
                    _capture_job["failed"].append({"id": str(doc["_id"]), "channel": no,
                                                   "name": doc.get("name", "") + (" (canal %d)" % no if len(nos) > 1 else ""),
                                                   "error": err})
    try:
        with ThreadPoolExecutor(max_workers=CAPTURE_ALL_WORKERS) as pool:
            list(pool.map(one, docs))
    finally:
        with _capture_lock:
            _capture_job["running"] = False
            _capture_job["finished_at"] = _iso(datetime.now(timezone.utc))
        logger.info("Capture de masse terminee : %d/%d", _capture_job["ok"], _capture_job["total"])


@cameras_bp.route("/api/cameras/capture-all", methods=["GET", "POST"])
def capture_all():
    """POST {ids} : capture chaque canal actif de chaque appareil pilotable
    (jamais les cameras de piste) ; GET : progression (total / done en
    canaux)."""
    if request.method == "GET":
        with _capture_lock:
            return jsonify(dict(_capture_job, failed=list(_capture_job.get("failed") or [])))
    _ensure_db()
    ids = [o for o in (_oid(i) for i in (request.get_json(silent=True) or {}).get("ids") or []) if o]
    docs, skipped = [], 0
    for d in _col_cameras.find({"_id": {"$in": ids}, "enabled": {"$ne": False}}):
        if is_piste_device(d) or d.get("brand") not in CONTROLLABLE_BRANDS or not d.get("credential_profile_id"):
            skipped += 1
            continue
        docs.append(d)
    if not docs:
        return jsonify({"error": "Aucune camera pilotable dans la selection"}), 400
    with _capture_lock:
        if _capture_job.get("running"):
            return jsonify({"error": "Une capture de masse est deja en cours"}), 409
        _capture_job.clear()
        total = sum(len(_capture_channels(d)) for d in docs)
        _capture_job.update({"running": True, "total": total, "devices": len(docs), "done": 0, "ok": 0, "failed": [],
                             "skipped": skipped, "started_at": _iso(datetime.now(timezone.utc)), "finished_at": None})
    threading.Thread(target=_run_capture_job, args=(docs,), daemon=True, name="capture-all").start()
    logger.info("Capture de masse lancee : %d camera(s), %d canal(aux), %d ignoree(s)", len(docs), total, skipped)
    return jsonify({"ok": True, "total": total, "devices": len(docs), "skipped": skipped}), 202


# ---------------------------------------------------------------------------
# Profils d'identifiants (mot de passe chiffre, ecriture seule)
# ---------------------------------------------------------------------------
@cameras_bp.route("/api/camera-profiles", methods=["POST"])
def create_profile():
    _ensure_db()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:60]
    if not name:
        return jsonify({"error": "Nom du profil requis"}), 400
    password = data.get("password") or ""
    try:
        secret = camera_secrets.encrypt(password) if password else ""
    except camera_secrets.CameraKeyMissing as e:
        return jsonify({"error": str(e)}), 503
    now = datetime.now(timezone.utc)
    try:
        res = _col_profiles.insert_one({"name": name, "user": (data.get("user") or "").strip()[:60],
                                        "secret": secret, "note": (data.get("note") or "").strip()[:200],
                                        "createdAt": now, "updatedAt": now})
    except Exception as e:
        if "duplicate key" in str(e).lower():
            return jsonify({"error": "Un profil porte deja ce nom"}), 409
        return jsonify({"error": str(e)}), 500
    return jsonify(_pub_profile(_col_profiles.find_one({"_id": res.inserted_id}), {})), 201


@cameras_bp.route("/api/camera-profiles/<pid>", methods=["PUT"])
def update_profile(pid):
    _ensure_db()
    oid = _oid(pid)
    if not oid:
        return jsonify({"error": "Identifiant invalide"}), 400
    data = request.get_json(silent=True) or {}
    patch = {"updatedAt": datetime.now(timezone.utc)}
    if "name" in data:
        name = (data.get("name") or "").strip()[:60]
        if not name:
            return jsonify({"error": "Nom du profil requis"}), 400
        patch["name"] = name
    if "user" in data:
        patch["user"] = (data.get("user") or "").strip()[:60]
    if "note" in data:
        patch["note"] = (data.get("note") or "").strip()[:200]
    # Mot de passe : remplace seulement s'il est saisi (jamais renvoye)
    if data.get("password"):
        try:
            patch["secret"] = camera_secrets.encrypt(data["password"])
        except camera_secrets.CameraKeyMissing as e:
            return jsonify({"error": str(e)}), 503
    try:
        res = _col_profiles.find_one_and_update({"_id": oid}, {"$set": patch}, return_document=True)
    except Exception as e:
        if "duplicate key" in str(e).lower():
            return jsonify({"error": "Un profil porte deja ce nom"}), 409
        return jsonify({"error": str(e)}), 500
    if not res:
        return jsonify({"error": "Profil introuvable"}), 404
    return jsonify(_pub_profile(res, {oid: _col_cameras.count_documents({"credential_profile_id": oid})}))


@cameras_bp.route("/api/camera-profiles/<pid>", methods=["DELETE"])
def delete_profile(pid):
    _ensure_db()
    oid = _oid(pid)
    if not oid:
        return jsonify({"error": "Identifiant invalide"}), 400
    n = _col_cameras.count_documents({"credential_profile_id": oid})
    if n:
        return jsonify({"error": "Profil utilise par %d camera(s) : reaffectez-les d'abord" % n}), 409
    _col_profiles.delete_one({"_id": oid})
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Pilotage (Hikvision)
# ---------------------------------------------------------------------------
def _channel_arg():
    try:
        return int(request.args.get("channel") or 0) or None
    except ValueError:
        return None


def _snapshot_name(cam_id, channel):
    return "%s_ch%d_latest.jpg" % (cam_id, channel) if channel and channel != 1 else "%s_latest.jpg" % cam_id


@cameras_bp.route("/api/cameras/<cam_id>/capture", methods=["POST"])
def capture_snapshot(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    err = _require_control(doc)
    if err:
        return err
    channel = _channel_arg()
    cam = make_hik_camera(doc, channel)
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        save_path = os.path.join(UPLOAD_DIR, "%s_ch%d_%s.jpg" % (cam_id, cam.channel, time.strftime("%Y%m%d_%H%M%S")))
        call_camera(doc, lambda: cam.capture_image(save_path))
        shutil.copy2(save_path, os.path.join(UPLOAD_DIR, _snapshot_name(cam_id, cam.channel)))
        with open(save_path, "rb") as f:
            return Response(f.read(), mimetype="image/jpeg")
    except Exception as e:
        return jsonify({"error": camera_error(doc, e, "Capture")}), 502


@cameras_bp.route("/api/cameras/<cam_id>/snapshot", methods=["GET"])
def get_snapshot(cam_id):
    """Derniere capture (vignette) de l'appareil ou d'un canal."""
    if not re.match(r"^[0-9a-f]{24}$", cam_id):
        return "", 400
    latest = os.path.join(UPLOAD_DIR, _snapshot_name(cam_id, _channel_arg()))
    if not os.path.isfile(latest):
        return "", 204
    return send_file(latest, mimetype="image/jpeg", max_age=30)


@cameras_bp.route("/api/cameras/<cam_id>/action", methods=["POST"])
def camera_action(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    if action not in ALLOWED_ACTIONS:
        return jsonify({"error": "Action inconnue : %s" % action}), 400
    err = _require_control(doc)
    if err:
        return err
    if action == "wiper" and doc.get("brand") not in WIPER_BRANDS:
        return jsonify({"error": "Essuie-glace non pilotable pour cette marque"}), 400
    cam = make_hik_camera(doc, data.get("channel"))
    try:
        result = call_camera(doc, cam.wiper) if action == "wiper" else False
        return jsonify({"ok": bool(result)})
    except Exception as e:
        return jsonify({"error": camera_error(doc, e, "Essuie-glace")}), 502


# Canaux sans PTZ (objectif fixe : la camera repond 403 / 404 sur
# /ISAPI/PTZCtrl/channels/<n>/presets) : retenus 24 h pour ne plus la
# solliciter a chaque ouverture de fiche (ex. 151 Technoparc, canal 2).
_NO_PTZ = {}
NO_PTZ_TTL_S = 86400


def _view_presets(doc, cam):
    """Presets de vue rappelables (sans les presets de fonction Hikvision).
    Canal sans PTZ : liste vide, sans avertissement."""
    key = (str(doc.get("_id")), cam.channel)
    if time.time() - _NO_PTZ.get(key, 0) < NO_PTZ_TTL_S:
        return []
    try:
        presets = cam.list_presets(raise_errors=True)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code in (403, 404):
            _NO_PTZ[key] = time.time()
            logger.info("Camera %s canal %s : pas de PTZ (HTTP %s), aucun preset", doc.get("name"), cam.channel, e.response.status_code)
            return []
        raise
    if doc.get("brand") == "hikvision":
        presets = [p for p in presets if p["id"] not in HIK_RESERVED_PRESETS]
    return presets


@cameras_bp.route("/api/cameras/<cam_id>/presets", methods=["GET"])
def list_presets(cam_id):
    """Presets enregistres sur la camera (PTZ). Liste vide = aucun preset."""
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    err = _require_control(doc)
    if err:
        return err
    cam = make_hik_camera(doc, _channel_arg())
    try:
        presets = call_camera(doc, lambda: _view_presets(doc, cam))
    except requests.exceptions.RequestException as e:
        return jsonify({"error": camera_error(doc, e, "Lecture des presets")}), 502
    except Exception as e:
        logger.info("Presets illisibles pour la camera %s : %s", cam_id, e)
        return jsonify({"error": "Presets illisibles (camera fixe ou injoignable) : %s" % e}), 502
    return jsonify({"presets": [{"id": p["id"], "name": p.get("name", "")} for p in presets]})


@cameras_bp.route("/api/cameras/<cam_id>/preset", methods=["POST"])
def goto_preset(cam_id):
    """Rappelle un preset : la camera change de vue (aussi pour les operateurs SeeTec)."""
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        preset = int(data.get("preset"))
    except (TypeError, ValueError):
        return jsonify({"error": "Preset invalide"}), 400
    if not 1 <= preset <= 300:
        return jsonify({"error": "Preset invalide"}), 400
    err = _require_control(doc)
    if err:
        return err
    cam = make_hik_camera(doc, data.get("channel"))
    try:
        # Seul un preset de vue present sur la camera peut etre rappele
        if preset not in {p["id"] for p in call_camera(doc, lambda: _view_presets(doc, cam))}:
            return jsonify({"error": "Preset %d absent de la camera" % preset}), 400
        ok = cam.goto_preset(preset, raise_errors=True)
    except Exception as e:
        return jsonify({"error": camera_error(doc, e, "Rappel de preset")}), 502
    logger.info("Camera %s (%s) : preset %d rappele", doc.get("name"), doc.get("ip"), preset)
    return jsonify({"ok": bool(ok)})


@cameras_bp.route("/api/cameras/<cam_id>/test", methods=["POST"])
def test_connection(cam_id):
    """Interroge l'appareil (modele, firmware) avec ses identifiants."""
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    err = _require_control(doc)
    if err:
        return err
    cam = make_hik_camera(doc)
    try:
        info = call_camera(doc, lambda: cam.get_device_info(raise_errors=True))
    except Exception as e:
        return jsonify({"ok": False, "error": camera_error(doc, e, "Test de connexion")})
    if "_error" in info:
        return jsonify({"ok": False, "error": info["_error"]})
    # Modele et firmware remontes par l'appareil : mis a jour au passage
    patch = {k: v for k, v in (("model", info.get("model")), ("firmware", info.get("firmwareVersion"))) if v}
    added = _detect_channels(doc, cam)
    if added:
        patch["channels"] = doc["channels"]
        patch["updatedAt"] = datetime.now(timezone.utc)
    if patch:
        _col_cameras.update_one({"_id": doc["_id"]}, {"$set": patch})
    return jsonify({"ok": True, "info": info, "channels_added": added})


def _detect_channels(doc, cam):
    """Hikvision : ajoute les entrees video absentes (multi-capteurs, ex.
    DS-2DP7D836 = 5 canaux). N'enleve ni ne renomme jamais un canal existant
    (usages LAPI / detection conserves). Renvoie les numeros ajoutes."""
    if doc.get("brand") != "hikvision":
        return []
    try:
        xml = re.sub(r'xmlns="[^"]*"', "", cam._get("/ISAPI/System/Video/inputs/channels").text)
    except Exception as e:
        logger.info("Canaux illisibles pour la camera %s (%s) : %s", doc.get("name"), doc.get("ip"), e)
        return []
    inputs = re.findall(r"<VideoInputChannel[^>]*>\s*<id>(\d+)</id>(.*?)</VideoInputChannel>", xml, re.S)
    channels = doc.get("channels") or [{"no": 1, "name": doc.get("name", ""), "enabled": True, "roles": []}]
    have = {int(c.get("no", 0)) for c in channels}
    added = []
    for no, body in inputs:
        no = int(no)
        if no in have or not 1 <= no <= 64:
            continue
        m = re.search(r"<name>([^<]*)</name>", body)
        name = (m.group(1).strip() if m else "") or "%s - canal %d" % (doc.get("name", ""), no)
        channels.append({"no": no, "name": name[:120], "enabled": True, "roles": []})
        added.append(no)
    channels.sort(key=lambda c: int(c.get("no", 0)))
    doc["channels"] = channels
    return added
