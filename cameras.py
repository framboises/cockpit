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
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, request, render_template, Response, send_file
from pymongo import MongoClient
from bson.objectid import ObjectId

import camera_secrets
import cameras_models

cameras_bp = Blueprint("cameras", __name__)
logger = logging.getLogger(__name__)

# Repli historique si un appareil n'a pas de profil d'identifiants
HIK_PASSWORD = os.getenv("HIK_PASSWORD", "")


# ---------------------------------------------------------------------------
# Auth helper (import from app at request time to avoid circular import)
# ---------------------------------------------------------------------------
def _check_admin():
    from app import (CODING, decoder_jeton_portail,
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
            {"key": "preset", "label": "Preset a rappeler avant (vide = aucun)", "kind": "preset", "default": ""},
        ],
    },
]
ROLE_INDEX = {r["type"]: r for r in ROLE_TYPES}

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads", "camera_snapshots")

# Statut reseau : connexion TCP au port web, sans identifiant (voir anpr.py)
PROBE_TIMEOUT_S = 1.0
PROBE_TTL_S = 60
_probe_cache = {}
# Une seule vague de tests a la fois : les pages ouvertes pendant une vague
# attendent son resultat au lieu d'en relancer une (rafale de pages)
_probe_lock = threading.Lock()

# Capture manuelle : une seule a la fois par camera et un delai minimum
# entre deux (clics en boucle ou script)
CAPTURE_MIN_INTERVAL_S = 3
# Condition : la capture de masse attend qu'un appareil se libere (la capture
# manuelle, elle, refuse tout de suite)
_capture_guard = threading.Condition()
_capture_busy = set()
_capture_last = {}
CAPTURE_WAIT_S = 120


def _device_acquire(ip, wait):
    """Reserve l'appareil (une capture a la fois par IP). wait=False : refus
    immediat s'il est occupe ; sinon attente jusqu'a CAPTURE_WAIT_S."""
    with _capture_guard:
        if not wait:
            if ip in _capture_busy:
                return False
        elif not _capture_guard.wait_for(lambda: ip not in _capture_busy, timeout=CAPTURE_WAIT_S):
            return False
        _capture_busy.add(ip)
        return True


def _device_release(ip, channel):
    with _capture_guard:
        _capture_busy.discard(ip)
        _capture_last[(ip, channel)] = time.monotonic()
        _capture_guard.notify_all()

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
    db["cameras_audit"].create_index([("camera_id", 1), ("at", -1)])
    db["cameras_audit"].create_index("at", expireAfterSeconds=AUDIT_RETENTION_DAYS * 86400)
    _db = db


# ---------------------------------------------------------------------------
# Journal des actions (cameras_audit) : creation, modification (avant / apres),
# suppression, edition de masse, profils (JAMAIS le mot de passe), rappels de
# preset, essuie-glace. Positions et masques ont leur propre journal
# (cameras_geo_edits, cameras_privacy_log).
# ---------------------------------------------------------------------------
AUDIT_RETENTION_DAYS = 730


def _ch_summary(channels):
    return [{"no": c.get("no"), "name": c.get("name", ""), "enabled": c.get("enabled", True),
             "roles": [{k: v for k, v in r.items()} for r in c.get("roles") or []],
             "optics": c.get("optics") or ""}
            for c in channels or []]


def _audit_val(key, v):
    if key == "channels":
        return _ch_summary(v)
    if isinstance(v, ObjectId):
        return str(v)
    return v


def _audit(action, doc=None, detail=None, ids=None):
    """Une ligne du journal ; ne fait jamais echouer l'action journalisee."""
    try:
        entry = {"action": action, "by": _who(), "at": datetime.now(timezone.utc)}
        if doc is not None:
            entry.update({"camera_id": doc.get("_id"), "name": doc.get("name", ""), "ip": doc.get("ip", "")})
        if ids:
            entry["camera_ids"] = list(ids)
        if detail:
            entry["detail"] = detail
        _db["cameras_audit"].insert_one(entry)
    except Exception as e:
        logger.warning("Journal cameras non ecrit (%s) : %s", action, e)


def _diff(before, after, keys):
    """{champ: [avant, apres]} des champs reellement modifies."""
    out = {}
    for k in keys:
        if k == "updatedAt":
            continue
        a, b = _audit_val(k, before.get(k)), _audit_val(k, after.get(k))
        if a != b:
            out[k] = [a, b]
    return out


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


def _pub_view(v):
    if not isinstance(v, dict) or v.get("az") is None:
        return None
    return {"az": v.get("az"), "fov": v.get("fov"), "range": v.get("range"), "by": v.get("by", ""), "at": _iso(v.get("at"))}


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
             "roles": c.get("roles") or [], "optics": c.get("optics") or "", "optics_src": c.get("optics_src") or "",
             "view": _pub_view(c.get("view"))}
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
    from hik.drivers import HikCamera
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


def view_preset_id(value, brand=None):
    """Numero de preset de VUE (1-300) ou None. Les presets de fonction
    Hikvision (94 = redemarrage, 95 = menu OSD...) ne sont jamais acceptes :
    un usage qui les rappellerait a chaque mesure redemarrerait le dome en
    boucle."""
    v = str(value or "").strip()
    if not v.isdigit():
        return None
    n = int(v)
    if not 1 <= n <= 300:
        return None
    if (brand or "hikvision") == "hikvision" and n in HIK_RESERVED_PRESETS:
        return None
    return n


def _clean_roles(roles, brand=None):
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
            elif p["kind"] == "preset":
                n = view_preset_id(v, brand)
                v = str(n) if n else ""
            else:
                v = str(v or "")[:60]
            clean[p["key"]] = v
        out.append(clean)
    return out


# Optique d'un canal : "mobile" (PTZ, presets) ou "fixe". optics_src : origine
# ("presets" = lu sur la camera, "modele" = deduit du modele, a verifier,
# "manuel" = choisi dans la fiche), pose par scripts/cameras_optics_prefill.py
OPTICS = ("mobile", "fixe")


def _clean_channels(channels, old=None, brand=None):
    """old : canaux enregistres. L'angle de vue (dessine sur la carte, route
    /view) et l'origine de l'optique ne viennent jamais du formulaire : repris
    du canal de meme numero."""
    prev = {c.get("no"): c for c in (old or []) if isinstance(c, dict)}
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
        ch = {"no": no, "name": str(c.get("name") or "Canal %d" % no).strip()[:80],
              "enabled": bool(c.get("enabled", True)), "roles": _clean_roles(c.get("roles"), brand)}
        o = prev.get(no) or {}
        if o.get("view"):
            ch["view"] = o["view"]
        optics = c.get("optics", o.get("optics"))
        if optics in OPTICS:
            ch["optics"] = optics
            ch["optics_src"] = o.get("optics_src") if optics == o.get("optics") and o.get("optics_src") else "manuel"
        out.append(ch)
    return sorted(out, key=lambda c: c["no"]) or [{"no": 1, "name": "Canal 1", "enabled": True, "roles": []}]


def _device_fields(data, partial, old_channels=None, old_brand=None):
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
        f["channels"] = _clean_channels(data.get("channels"), old_channels, f.get("brand") or old_brand)
        # anpr_camera_config (et le recepteur) sont indexes par chemin : un seul
        # canal LAPI par appareil, sinon le second serait ignore sans le dire
        lapi = [c["no"] for c in f["channels"] if any(r["type"] == "anpr" for r in c["roles"])]
        if len(lapi) > 1:
            return None, "Un seul canal LAPI par appareil (canaux %s) : le chemin d'envoi est commun" % ", ".join(map(str, lapi))
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
    proposer les plus proches de la fiche en tete du selecteur. `views` :
    angles de vue dessines des canaux actifs (carte de la page Cameras), pour
    mettre en tete les cameras dont un canal couvre la fiche (pcorg.js)."""
    _ensure_db()
    pos = {}
    layer = _db["cameras"].find_one({"type": "FeatureCollection"}, {"features.geometry": 1, "features.properties.IP": 1}) or {}
    for f in layer.get("features") or []:
        ip = ((f.get("properties") or {}).get("IP") or "").strip()
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if ip and len(coords) >= 2:
            pos.setdefault(ip, (coords[1], coords[0]))
    out = []
    for d in _col_cameras.find(_CAPTURE_QUERY, {"name": 1, "ip": 1, "location": 1, "site": 1, "group_path": 1,
                                                "model": 1, "map": 1, "channels": 1}).sort("name", 1):
        if is_piste_device(d):
            continue
        p = pos.get(d.get("ip"))
        if not p and isinstance(d.get("map"), dict) and d["map"].get("lat") is not None:
            p = (d["map"]["lat"], d["map"]["lng"])  # placee a la main (page Detection)
        views = [{"no": c.get("no", 1), "name": c.get("name", ""), "optics": c.get("optics") or "",
                  "az": c["view"]["az"], "fov": c["view"]["fov"], "range": c["view"]["range"]}
                 for c in d.get("channels") or []
                 if c.get("enabled", True) is not False and isinstance(c.get("view"), dict) and c["view"].get("az") is not None]
        out.append({"_id": str(d["_id"]), "name": d.get("name", ""), "ip": d.get("ip", ""),
                    "location": d.get("location") or d.get("site", ""),
                    "lat": p[0] if p else None, "lon": p[1] if p else None, "views": views})
    return out


def capturable_doc(cam_id):
    """Appareil d'une camera proposee a la main courante, ou None. Meme filtre
    que capture_camera_list : un operateur ne peut ni capturer ni lire la
    capture d'une camera hors liste (piste, desactivee, sans profil...)."""
    if not re.match(r"^[0-9a-f]{24}$", cam_id or ""):
        return None
    _ensure_db()
    d = _col_cameras.find_one(dict(_CAPTURE_QUERY, _id=ObjectId(cam_id)))
    return None if not d or is_piste_device(d) else d


def latest_snapshot_path(cam_id, channel=None):
    """Derniere capture (vignette) d'une camera proposee a la main courante, ou None."""
    if not capturable_doc(cam_id):
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
        "super_admin": _is_super_admin(),
        "receiver": "http://10.24.0.251:8000",
    })


@cameras_bp.route("/api/cameras", methods=["POST"])
def create_camera():
    _ensure_db()
    data = request.get_json(silent=True) or {}
    fields, err = _device_fields(data, partial=False)
    if err:
        return jsonify({"error": err}), 400
    fields.setdefault("channels", _clean_channels(data.get("channels") or [{"no": 1, "name": fields["name"]}],
                                                  brand=fields.get("brand")))
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
    created = _col_cameras.find_one({"_id": res.inserted_id})
    _audit("creation", created)
    return jsonify(_pub_device(created, _profiles_by_id())), 201


@cameras_bp.route("/api/cameras/<cam_id>", methods=["PUT"])
def update_camera(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    fields, msg = _device_fields(data, partial=True, old_channels=doc.get("channels"), old_brand=doc.get("brand"))
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
    changes = _diff(doc, after, fields.keys())
    if changes:
        _audit("modification", after, changes)
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
    _audit("suppression", doc, {k: _audit_val(k, v) for k, v in doc.items() if k not in ("_id", "password")})
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
        gone = list(_col_cameras.find({"_id": {"$in": ids}}, {"name": 1, "ip": 1, "camera_path": 1}))
        res = _col_cameras.delete_many({"_id": {"$in": ids}})
        _sync_anpr_config()
        _audit("suppression_masse", ids=ids, detail={"cameras": [{"name": g.get("name"), "ip": g.get("ip"),
                                                                  "camera_path": g.get("camera_path", "")} for g in gone]})
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
    _audit("modification_masse", ids=ids, detail={k: _audit_val(k, v) for k, v in fields.items() if k != "updatedAt"})
    return jsonify({"ok": True, "modified": res.modified_count})


@cameras_bp.route("/api/cameras/reachability", methods=["GET"])
def reachability():
    """{id: {ok, at}} pour les appareils actifs ; resultat garde 1 min par IP."""
    _ensure_db()
    docs = list(_col_cameras.find({"enabled": {"$ne": False}}, {"ip": 1, "port": 1}))
    with _probe_lock:
        # Calcule sous le verrou : une page qui attendait trouve le cache frais
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
    # Etat confirme par la releve planifiee (cameras_health.py) : depuis quand
    health = {h["_id"]: h for h in _db["cameras_health"].find({}, {"ok": 1, "since": 1})}
    out = {}
    for d in docs:
        hit = _probe_cache.get((d["ip"], d.get("port", 80)))
        if hit:
            out[str(d["_id"])] = {"ok": hit[1], "at": _iso(hit[2])}
            h = health.get(d["_id"])
            if h and "ok" in h:
                out[str(d["_id"])].update({"health_ok": bool(h["ok"]), "since": _iso(h.get("since"))})
    return jsonify(out)


@cameras_bp.route("/api/cameras/<cam_id>/health", methods=["GET"])
def camera_health(cam_id):
    """Etat confirme et disponibilite 24 h / 7 j (releve cameras_health.py)."""
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    import cameras_health
    h = _db["cameras_health"].find_one({"_id": doc["_id"]})
    if not h or "ok" not in h:
        return jsonify({"monitored": False, "piste": is_piste_device(doc), "enabled": doc.get("enabled", True)})
    return jsonify({
        "monitored": True, "ok": bool(h["ok"]), "since": _iso(h.get("since")),
        "last_check_at": _iso(h.get("last_check_at")), "lapi_last_at": _iso(h.get("lapi_last_at")),
        "availability_24h": cameras_health.availability(_db, doc["_id"], 24),
        "availability_7d": cameras_health.availability(_db, doc["_id"], 24 * 7),
        "outages_7d": _db["cameras_health_log"].count_documents({
            "camera_id": doc["_id"], "ok": False,
            "at": {"$gte": datetime.now(timezone.utc) - timedelta(days=7)}}),
    })


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


def _channel_of(cam_id, no):
    """-> (doc, canaux, index du canal, erreur). Appareil sans liste de canaux :
    canal 1 implicite (cree a l'ecriture)."""
    doc, err = _get_cam_doc(cam_id)
    if err:
        return None, None, None, err
    chans = doc.get("channels") or [{"no": 1, "name": doc.get("name", ""), "enabled": True, "roles": []}]
    idx = next((i for i, c in enumerate(chans) if c.get("no", i + 1) == no), None)
    if idx is None:
        return None, None, None, (jsonify({"error": "Canal introuvable"}), 404)
    return doc, chans, idx, None


@cameras_bp.route("/api/cameras/<cam_id>/channels/<int:no>/optics", methods=["PUT"])
def set_channel_optics(cam_id, no):
    """Optique d'un canal choisie dans le parcours des angles de vue :
    {optics: "mobile" | "fixe"} -> optics_src "manuel". Cockpit seulement."""
    doc, chans, idx, err = _channel_of(cam_id, no)
    if err:
        return err
    optics = (request.get_json(silent=True) or {}).get("optics")
    if optics not in OPTICS:
        return jsonify({"error": "Optique invalide (mobile ou fixe)"}), 400
    chans[idx]["optics"] = optics
    chans[idx]["optics_src"] = "manuel"
    _col_cameras.update_one({"_id": doc["_id"]}, {"$set": {"channels": chans}})
    return jsonify({"ok": True, "no": no, "optics": optics, "optics_src": "manuel"})


@cameras_bp.route("/api/cameras/<cam_id>/channels/<int:no>/view", methods=["PUT"])
def set_channel_view(cam_id, no):
    """Angle de vue d'un canal dessine sur la carte : {az (degres depuis le
    nord, sens horaire), fov (ouverture en degres), range (portee en m)} ou
    {clear: true}. Ecrit dans cockpit_cameras uniquement : rien n'est envoye
    a la camera ni a la couche GPS."""
    doc, chans, idx, err = _channel_of(cam_id, no)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if data.get("clear"):
        chans[idx].pop("view", None)
    else:
        try:
            az, fov, rng = float(data["az"]) % 360, float(data["fov"]), float(data["range"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Angle de vue invalide"}), 400
        if not (1 <= fov <= 360 and 1 <= rng <= 5000):
            return jsonify({"error": "Ouverture (1 a 360 degres) ou portee (1 a 5000 m) invalide"}), 400
        p = getattr(request, "user_payload", {}) or {}
        who = ("%s %s" % (p.get("firstname", ""), p.get("lastname", ""))).strip() or p.get("email", "")
        chans[idx]["view"] = {"az": round(az, 1), "fov": round(fov, 1), "range": round(rng),
                              "by": who, "at": datetime.now(timezone.utc)}
    _col_cameras.update_one({"_id": doc["_id"]}, {"$set": {"channels": chans}})
    return jsonify({"ok": True, "no": no, "view": _pub_view(chans[idx].get("view"))})


# ---------------------------------------------------------------------------
# Cameras en projet : emplacements souhaites (travaux a venir), poses sur la
# carte avec optique et angle de vue prevus. Collection a part
# `cameras_planned` : JAMAIS dans cockpit_cameras ni dans la couche GPS
# titan.cameras (aucun pilotage, capture, LAPI ou detection ne les voit).
# ---------------------------------------------------------------------------
PLANNED_STATUS = {"souhaitee": "Souhaitee", "validee": "Validee", "commandee": "Commandee"}


def _who():
    p = getattr(request, "user_payload", {}) or {}
    return ("%s %s" % (p.get("firstname", ""), p.get("lastname", ""))).strip() or p.get("email", "")


PLANNED_MAX_CHANNELS = 8
# Objectif (champ `model`) d'un canal simule : cle d'objectif du catalogue
# des modeles (cameras_models.py, collection cameras_models)
# Support de pose (dossier de planification des travaux)
PLANNED_MOUNTS = {"mat": "Mat", "angle_mur": "Angle de mur", "mur_plat": "Mur plat"}
PLANNED_MAX_REPLACES = 20


def _planned_channels(d):
    """Canaux d'une camera en projet (multi-capteurs simules : 1 PTZ + 4 fixes...).
    Premiers enregistrements a canal unique : optics / view a la racine."""
    chans = d.get("channels")
    if isinstance(chans, list) and chans:
        return [dict(c) for c in chans]
    ch = {"no": 1}
    if d.get("optics"):
        ch["optics"] = d["optics"]
    if d.get("view"):
        ch["view"] = d["view"]
    return [ch]


def _planned_refs(docs):
    """{ObjectId: {_id, name, ip, exists, removed_by, reused_by}} des cameras
    existantes citees par des projets (remplacees ou deplacees) : une camera
    supprimee depuis reste affichee, marquee absente. removed_by / reused_by :
    projet qui la remplace (depose) / qui la reinstalle ailleurs (reemploi)."""
    ids = {o for d in docs for o in (d.get("replaces") or []) + ([d["moves_camera_id"]] if d.get("moves_camera_id") else [])}
    if not ids:
        return {}
    found = {c["_id"]: c for c in _col_cameras.find({"_id": {"$in": list(ids)}}, {"name": 1, "ip": 1})}
    removed, reused = {}, {}
    for p in _db["cameras_planned"].find({}, {"name": 1, "replaces": 1, "moves_camera_id": 1}):
        ref = {"_id": str(p["_id"]), "name": p.get("name", "")}
        for o in p.get("replaces") or []:
            removed[o] = ref
        if p.get("moves_camera_id"):
            reused[p["moves_camera_id"]] = ref
    return {o: {"_id": str(o), "name": (found.get(o) or {}).get("name", "Camera supprimee"),
                "ip": (found.get(o) or {}).get("ip", ""), "exists": o in found,
                "removed_by": removed.get(o), "reused_by": reused.get(o)} for o in ids}


def _planned_devices(d, models=None):
    """Appareils comptes au dossier pour ce projet (meme regle que le PDF :
    cameras_models.devices) ; un deplacement ne commande rien."""
    if d.get("moves_camera_id"):
        return []
    models = cameras_models.catalogue(_db) if models is None else models
    pm = next((m for m in models if m["_id"] == d.get("model_id")), None) if d.get("model_id") else None
    return [lab for lab, _m in cameras_models.devices(cameras_models.resolve_channels(models, _planned_channels(d)), pm)]


def _pub_planned(d, refs=None, models=None):
    refs = _planned_refs([d]) if refs is None else refs
    return {"_id": str(d["_id"]), "name": d.get("name", ""), "note": d.get("note", ""),
            "status": d.get("status", "souhaitee"), "lat": d.get("lat"), "lng": d.get("lng"),
            "priority": d.get("priority"), "mount": d.get("mount") or "", "mount_height": d.get("mount_height"),
            "model_id": str(d["model_id"]) if d.get("model_id") else "",
            "devices": _planned_devices(d, models),
            "replaces": [refs[o] for o in d.get("replaces") or [] if o in refs],
            "moves": refs.get(d.get("moves_camera_id")) if d.get("moves_camera_id") else None,
            "channels": [{"no": c.get("no"), "optics": c.get("optics") or "", "model": c.get("model") or "",
                          "view": _pub_view(c.get("view"))}
                         for c in _planned_channels(d)],
            "by": d.get("by", ""), "at": _iso(d.get("at")), "updated_by": d.get("updated_by", ""),
            "updatedAt": _iso(d.get("updatedAt"))}


def _planned_fields(data, chans):
    """Champs modifiables d'une camera en projet. `ch` (numero de canal, 1 par
    defaut) cible optics / view / clear_view ; chans = canaux actuels, modifies
    sur place. -> (champs, a effacer, erreur)."""
    f, unset = {}, {}
    if "name" in data:
        name = str(data.get("name") or "").strip()[:120]
        if not name:
            return None, None, "Nom requis"
        f["name"] = name
    if "note" in data:
        f["note"] = str(data.get("note") or "").strip()[:1000]
    if "status" in data:
        if data["status"] not in PLANNED_STATUS:
            return None, None, "Statut inconnu"
        f["status"] = data["status"]
    if "priority" in data:
        if data["priority"] in (None, ""):
            unset["priority"] = ""
        else:
            try:
                prio = int(data["priority"])
            except (TypeError, ValueError):
                return None, None, "Priorite invalide (nombre entier, 1 = la plus urgente)"
            if not 1 <= prio <= 999:
                return None, None, "Priorite invalide (1 a 999)"
            f["priority"] = prio
    if "mount" in data:
        if not data["mount"]:
            unset["mount"] = ""
        elif data["mount"] in PLANNED_MOUNTS:
            f["mount"] = data["mount"]
        else:
            return None, None, "Support de pose inconnu"
    if "mount_height" in data:
        if data["mount_height"] in (None, ""):
            unset["mount_height"] = ""
        else:
            try:
                h = round(float(str(data["mount_height"]).replace(",", ".")), 1)
            except (TypeError, ValueError):
                return None, None, "Hauteur de pose invalide"
            if not 0.5 <= h <= 60:
                return None, None, "Hauteur de pose invalide (0,5 a 60 m)"
            f["mount_height"] = h
    if "replaces" in data:
        ids = []
        for i in data.get("replaces") or []:
            o = _oid(i)
            if not o or o in ids:
                continue
            ids.append(o)
        if len(ids) > PLANNED_MAX_REPLACES:
            return None, None, "%d cameras remplacees au plus" % PLANNED_MAX_REPLACES
        if ids and _col_cameras.count_documents({"_id": {"$in": ids}}) != len(ids):
            return None, None, "Camera a remplacer introuvable"
        f["replaces"] = ids
    if "moves_camera_id" in data:
        if not data["moves_camera_id"]:
            unset["moves_camera_id"] = ""
        else:
            o = _oid(data["moves_camera_id"])
            if not o or not _col_cameras.find_one({"_id": o}, {"_id": 1}):
                return None, None, "Camera a deplacer introuvable"
            f["moves_camera_id"] = o
    if "model_id" in data:
        if not data["model_id"]:
            unset["model_id"] = ""
        else:
            o = _oid(data["model_id"])
            m = _db["cameras_models"].find_one({"_id": o}) if o else None
            if not m:
                return None, None, "Modele inconnu"
            f["model_id"] = o
            if data.get("apply_layout", True):
                err = _apply_model(m, chans)
                if err:
                    return None, None, err
                f["channels"] = chans
                unset.update({"optics": "", "view": ""})
    if "lat" in data or "lng" in data:
        try:
            lat, lng = float(data["lat"]), float(data["lng"])
        except (KeyError, TypeError, ValueError):
            return None, None, "Position invalide"
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return None, None, "Position invalide"
        f["lat"], f["lng"] = lat, lng
    if "optics" in data or "view" in data or "model" in data or data.get("clear_view"):
        try:
            no = int(data.get("ch") or 1)
        except (TypeError, ValueError):
            return None, None, "Canal invalide"
        ch = next((c for c in chans if c.get("no") == no), None)
        if ch is None:
            return None, None, "Canal introuvable"
        if "optics" in data:
            if data["optics"] in OPTICS:
                ch["optics"] = data["optics"]
            elif not data["optics"]:
                ch.pop("optics", None)
            else:
                return None, None, "Optique invalide (mobile ou fixe)"
        if "model" in data:
            if data["model"] in _lens_keys():
                ch["model"] = data["model"]
            elif not data["model"]:
                ch.pop("model", None)
            else:
                return None, None, "Modele inconnu"
        if data.get("clear_view"):
            ch.pop("view", None)
        elif "view" in data:
            v = data.get("view") or {}
            try:
                az, fov, rng = float(v["az"]) % 360, float(v["fov"]), float(v["range"])
            except (KeyError, TypeError, ValueError):
                return None, None, "Angle de vue invalide"
            if not (1 <= fov <= 360 and 1 <= rng <= 5000):
                return None, None, "Ouverture (1 a 360 degres) ou portee (1 a 5000 m) invalide"
            ch["view"] = {"az": round(az, 1), "fov": round(fov, 1), "range": round(rng), "by": _who(),
                          "at": datetime.now(timezone.utc)}
        f["channels"] = chans
        # Anciens champs du canal unique : remplaces par channels[]
        unset.update({"optics": "", "view": ""})
    return f, unset, None


def _lens_keys():
    return cameras_models.lens_index(cameras_models.catalogue(_db))


def _apply_model(m, chans):
    """Canaux d'un emplacement poses d'apres le modele choisi (modifies sur
    place, angles de vue gardes). Multi-capteurs : exactement sa disposition
    (canal 1 = PTZ...). Sinon : chaque canal d'un autre modele prend
    l'objectif du modele de meme optique (a defaut le premier)."""
    lenses = {x["k"]: x for x in m.get("lenses") or []}
    if not lenses:
        return "Modele sans objectif"
    if m.get("multi"):
        lay = cameras_models.layout(m)
        if len(lay) > PLANNED_MAX_CHANNELS:
            return "%d canaux au plus par emplacement" % PLANNED_MAX_CHANNELS
        nxt = max([c.get("no", 0) for c in chans] or [0]) + 1
        out = []
        for j, k in enumerate(lay):
            if j < len(chans):
                c = chans[j]
            else:
                c, nxt = {"no": nxt}, nxt + 1
            c.update({"model": k, "optics": lenses[k]["optics"]})
            out.append(c)
        chans[:] = out
        return None
    for c in chans:
        if c.get("model") in lenses:
            continue
        k = next((x["k"] for x in m["lenses"] if x["optics"] == c.get("optics")), m["lenses"][0]["k"])
        c.update({"model": k, "optics": lenses[k]["optics"]})
    return None


def _planned_doc(pid):
    _ensure_db()
    oid = _oid(pid)
    return _db["cameras_planned"].find_one({"_id": oid}) if oid else None


def _planned_conflict(doc_id, merged, take_move=False):
    """Message d'erreur si le projet est incoherent avec lui-meme ou les autres.
    Une camera n'est deplacee (reinstallee) que par un projet et remplacee
    (deposee) que par un projet ; un projet ne la remplace pas et la deplace a
    la fois. Remplacee par un projet ET reinstallee par un autre : reemploi,
    admis (la camera deposee repart sur un nouvel emplacement). take_move :
    la camera deja reinstallee par un autre projet lui est reprise (deplacements
    en chaine reorganises), voir _planned_save."""
    moves, repl = merged.get("moves_camera_id"), merged.get("replaces") or []
    if moves and moves in repl:
        return "Une camera ne peut pas etre a la fois deplacee et remplacee par ce projet"
    q = {"_id": {"$ne": doc_id}} if doc_id else {}
    if moves and not take_move:
        other = _db["cameras_planned"].find_one(dict(q, moves_camera_id=moves), {"name": 1})
        if other:
            return "Cette camera est deja reinstallee par le projet %s" % other.get("name", "")
    for o in repl:
        other = _db["cameras_planned"].find_one(dict(q, replaces=o), {"name": 1})
        if other:
            c = _col_cameras.find_one({"_id": o}, {"name": 1}) or {}
            return "%s est deja remplacee par le projet %s" % (c.get("name", "Une camera"), other.get("name", ""))
    return None


def _planned_save(doc, f, unset=None, take_move=False):
    """take_move : la camera deplacee par ce projet est retiree du projet qui
    la reinstallait jusque-la (nom de ces projets dans `released`)."""
    merged = dict(doc, **f)
    for k in unset or {}:
        merged.pop(k, None)
    take_move = take_move and bool(f.get("moves_camera_id"))
    conflict = _planned_conflict(doc["_id"], merged, take_move)
    if conflict:
        return jsonify({"error": conflict}), 409
    now = datetime.now(timezone.utc)
    f.update({"updated_by": _who(), "updatedAt": now})
    upd = {"$set": f}
    if unset:
        upd["$unset"] = unset
    released = []
    if take_move:
        for o in list(_db["cameras_planned"].find({"_id": {"$ne": doc["_id"]}, "moves_camera_id": f["moves_camera_id"]}, {"name": 1})):
            _db["cameras_planned"].update_one({"_id": o["_id"]}, {"$unset": {"moves_camera_id": ""},
                                                                  "$set": {"updated_by": _who(), "updatedAt": now}})
            released.append(o.get("name", ""))
            logger.info("Camera en projet %s : deplacement repris par %s (%s)", o.get("name"), merged.get("name"), _who())
    _db["cameras_planned"].update_one({"_id": doc["_id"]}, upd)
    out = _pub_planned(_db["cameras_planned"].find_one({"_id": doc["_id"]}))
    if released:
        out["released"] = released
    return jsonify(out)


@cameras_bp.route("/api/cameras/planned", methods=["GET"])
def list_planned():
    _ensure_db()
    docs = list(_db["cameras_planned"].find().sort("name", 1))
    refs = _planned_refs(docs)
    models = cameras_models.catalogue(_db)
    return jsonify([_pub_planned(d, refs, models) for d in docs])


@cameras_bp.route("/api/cameras/planned", methods=["POST"])
def create_planned():
    """{lat, lng, ...} : nouvel emplacement. {from_camera_id} : deplacement
    d'une camera existante, cree a sa position actuelle avec ses canaux
    (optique, angle de vue) ; il reste a glisser le pin a la nouvelle place."""
    _ensure_db()
    data = request.get_json(silent=True) or {}
    chans = [{"no": 1}]
    if data.get("from_camera_id"):
        src = _col_cameras.find_one({"_id": _oid(data["from_camera_id"])}) if _oid(data["from_camera_id"]) else None
        if not src:
            return jsonify({"error": "Camera introuvable"}), 404
        import detection
        pos, _source, _geo = detection._device_position(_db, src)
        if not pos:
            return jsonify({"error": "Camera sans position : placez-la d'abord sur la carte"}), 400
        data = {"lat": pos["lat"], "lng": pos["lng"], "name": ("Deplacement - %s" % src.get("name", ""))[:120],
                "status": data.get("status") or "souhaitee", "moves_camera_id": str(src["_id"])}
        chans = [dict({"no": i + 1}, **{k: c[k] for k in ("optics", "view") if c.get(k)})
                 for i, c in enumerate(c for c in src.get("channels") or [] if c.get("enabled", True) is not False)
                 ][:PLANNED_MAX_CHANNELS] or [{"no": 1}]
    if "lat" not in data or "lng" not in data:
        return jsonify({"error": "Position requise"}), 400
    data.setdefault("name", "Projet %d" % (_db["cameras_planned"].count_documents({}) + 1))
    data.setdefault("status", "souhaitee")
    f, _unset, err = _planned_fields(data, chans)
    if err:
        return jsonify({"error": err}), 400
    conflict = _planned_conflict(None, f)
    if conflict:
        return jsonify({"error": conflict}), 409
    now = datetime.now(timezone.utc)
    f.update({"channels": chans, "by": _who(), "at": now, "updatedAt": now})
    res = _db["cameras_planned"].insert_one(f)
    logger.info("Camera en projet %s creee par %s", f.get("name"), f["by"])
    return jsonify(_pub_planned(_db["cameras_planned"].find_one({"_id": res.inserted_id}))), 201


@cameras_bp.route("/api/cameras/planned/dossier.pdf", methods=["GET"])
def planned_dossier():
    """Dossier de planification des travaux (PDF A4 paysage, cameras_dossier.py).
    ?status=souhaitee,validee (defaut : tous), ids=<id>,<id> (facultatif),
    title, subtitle, indice, phase, author (defaut : l'utilisateur), role,
    datasheets=1 (fiches techniques des modeles en annexe)."""
    _ensure_db()
    import io
    import cameras_dossier
    a = request.args
    statuses = [x for x in (a.get("status") or "").split(",") if x in PLANNED_STATUS] or list(PLANNED_STATUS)
    ids = [o for o in (_oid(x) for x in (a.get("ids") or "").split(",") if x) if o]
    opts = {"statuses": statuses, "ids": ids or None, "title": a.get("title"), "subtitle": a.get("subtitle"),
            "indice": a.get("indice"), "phase": a.get("phase"), "author": a.get("author") or _who(), "role": a.get("role"),
            "datasheets": a.get("datasheets") in ("1", "true")}
    try:
        pdf, n = cameras_dossier.build_pdf(_db, opts)
    except Exception:
        logger.exception("Dossier de planification des cameras impossible")
        return jsonify({"error": "Generation du dossier impossible (voir le journal du serveur)"}), 500
    if not pdf:
        return jsonify({"error": "Aucune camera en projet pour ces statuts"}), 404
    indice = "".join(ch for ch in (opts["indice"] or "A").upper() if ch.isalnum())[:3] or "A"
    name = "Dossier_travaux_cameras_ind%s_%s.pdf" % (indice, datetime.now().strftime("%Y%m%d"))
    logger.info("Dossier de planification des cameras : %d emplacement(s), par %s", n, _who())
    _audit("dossier_travaux", detail={"emplacements": n, "statuts": statuses, "indice": indice})
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=False, download_name=name)


@cameras_bp.route("/api/cameras/planned/<pid>", methods=["PUT"])
def update_planned(pid):
    doc = _planned_doc(pid)
    if not doc:
        return jsonify({"error": "Camera en projet introuvable"}), 404
    data = request.get_json(silent=True) or {}
    f, unset, err = _planned_fields(data, _planned_channels(doc))
    if err:
        return jsonify({"error": err}), 400
    return _planned_save(doc, f, unset, take_move=bool(data.get("take_move")))


@cameras_bp.route("/api/cameras/planned/<pid>/channels", methods=["POST"])
def add_planned_channels(pid):
    """Ajoute des canaux simules : {optics, count} (ex. 4 fixes autour d'une PTZ)."""
    doc = _planned_doc(pid)
    if not doc:
        return jsonify({"error": "Camera en projet introuvable"}), 404
    data = request.get_json(silent=True) or {}
    optics, model = data.get("optics"), data.get("model") or ""
    if optics not in OPTICS:
        return jsonify({"error": "Optique invalide (mobile ou fixe)"}), 400
    if model and model not in _lens_keys():
        return jsonify({"error": "Modele inconnu"}), 400
    try:
        count = int(data.get("count") or 1)
    except (TypeError, ValueError):
        return jsonify({"error": "Nombre invalide"}), 400
    chans = _planned_channels(doc)
    if count < 1 or len(chans) + count > PLANNED_MAX_CHANNELS:
        return jsonify({"error": "%d canaux au plus par emplacement" % PLANNED_MAX_CHANNELS}), 400
    nxt = max(c.get("no", 0) for c in chans) + 1
    chans += [dict({"no": nxt + i, "optics": optics}, **({"model": model} if model else {})) for i in range(count)]
    return _planned_save(doc, {"channels": chans}, {"optics": "", "view": ""})


@cameras_bp.route("/api/cameras/planned/<pid>/channels/<int:no>", methods=["DELETE"])
def delete_planned_channel(pid, no):
    doc = _planned_doc(pid)
    if not doc:
        return jsonify({"error": "Camera en projet introuvable"}), 404
    chans = _planned_channels(doc)
    if len(chans) < 2:
        return jsonify({"error": "Un emplacement garde au moins un canal"}), 400
    left = [c for c in chans if c.get("no") != no]
    if len(left) == len(chans):
        return jsonify({"error": "Canal introuvable"}), 404
    return _planned_save(doc, {"channels": left}, {"optics": "", "view": ""})


@cameras_bp.route("/api/cameras/planned/<pid>", methods=["DELETE"])
def delete_planned(pid):
    doc = _planned_doc(pid)
    if not doc:
        return jsonify({"error": "Camera en projet introuvable"}), 404
    _db["cameras_planned"].delete_one({"_id": doc["_id"]})
    logger.info("Camera en projet %s supprimee par %s", doc.get("name"), _who())
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Catalogue des modeles (cameras_models.py) : objectifs des canaux, modele des
# cameras en projet, fiches techniques PDF jointes au dossier travaux
# ---------------------------------------------------------------------------
def _model_doc(mid):
    _ensure_db()
    oid = _oid(mid)
    return _db["cameras_models"].find_one({"_id": oid}) if oid else None


@cameras_bp.route("/api/cameras/models", methods=["GET"])
def list_models():
    _ensure_db()
    models = cameras_models.catalogue(_db)
    use = cameras_models.usage(_db, models)
    return jsonify([cameras_models.pub(m, use) for m in models])


@cameras_bp.route("/api/cameras/models", methods=["POST"])
def create_model():
    _ensure_db()
    models = cameras_models.catalogue(_db)
    f, err = cameras_models.clean(request.get_json(silent=True) or {}, None, cameras_models.lens_index(models))
    if err:
        return jsonify({"error": err}), 400
    now = datetime.now(timezone.utc)
    f.update({"by": _who(), "at": now, "updatedAt": now})
    res = _db["cameras_models"].insert_one(f)
    _audit("modele_cree", detail={"modele": cameras_models.name(f)})
    return jsonify(cameras_models.pub(_db["cameras_models"].find_one({"_id": res.inserted_id}))), 201


@cameras_bp.route("/api/cameras/models/<mid>", methods=["PUT"])
def update_model(mid):
    m = _model_doc(mid)
    if not m:
        return jsonify({"error": "Modele introuvable"}), 404
    models = cameras_models.catalogue(_db)
    f, err = cameras_models.clean(request.get_json(silent=True) or {}, m, cameras_models.lens_index(models))
    if err:
        return jsonify({"error": err}), 400
    gone = {x["k"] for x in m.get("lenses") or []} - {x["k"] for x in f["lenses"]}
    for k in gone:
        n = cameras_models.lens_in_use(_db, k)
        if n:
            lab = next((x.get("label") for x in m["lenses"] if x["k"] == k), k)
            return jsonify({"error": "Objectif %s utilise par %d camera(s) en projet : changez-les d'abord" % (lab, n)}), 409
    f.update({"updated_by": _who(), "updatedAt": datetime.now(timezone.utc)})
    _db["cameras_models"].update_one({"_id": m["_id"]}, {"$set": f})
    _audit("modele_modifie", detail={"modele": cameras_models.name(f)})
    return jsonify(cameras_models.pub(_db["cameras_models"].find_one({"_id": m["_id"]}), cameras_models.usage(_db, cameras_models.catalogue(_db))))


@cameras_bp.route("/api/cameras/models/<mid>", methods=["DELETE"])
def delete_model(mid):
    m = _model_doc(mid)
    if not m:
        return jsonify({"error": "Modele introuvable"}), 404
    n = cameras_models.usage(_db, cameras_models.catalogue(_db)).get(m["_id"], 0)
    if n:
        return jsonify({"error": "Modele utilise par %d camera(s) en projet" % n}), 409
    _db["cameras_models"].delete_one({"_id": m["_id"]})
    try:
        os.remove(cameras_models.datasheet_path(m["_id"]))
    except OSError:
        pass
    _audit("modele_supprime", detail={"modele": cameras_models.name(m)})
    return jsonify({"ok": True})


@cameras_bp.route("/api/cameras/models/<mid>/datasheet", methods=["POST"])
def upload_datasheet(mid):
    """Fiche technique PDF du modele (multipart, champ `file`), remplace la
    precedente. Verifiee par sa signature %PDF, pas par son extension."""
    m = _model_doc(mid)
    if not m:
        return jsonify({"error": "Modele introuvable"}), 404
    up = request.files.get("file")
    if not up:
        return jsonify({"error": "Fichier requis"}), 400
    data = up.read(cameras_models.DATASHEET_MAX_BYTES + 1)
    if len(data) > cameras_models.DATASHEET_MAX_BYTES:
        return jsonify({"error": "Fiche technique trop lourde (%d Mo au plus)" % (cameras_models.DATASHEET_MAX_BYTES // (1024 * 1024))}), 413
    if not data.startswith(b"%PDF-"):
        return jsonify({"error": "La fiche technique doit etre un PDF"}), 400
    os.makedirs(cameras_models.DATASHEET_DIR, exist_ok=True)
    path = cameras_models.datasheet_path(m["_id"])
    with open(path + ".tmp", "wb") as fh:
        fh.write(data)
    os.replace(path + ".tmp", path)
    fname = os.path.basename(up.filename or "fiche.pdf")[:120] or "fiche.pdf"
    ds = {"name": fname, "size": len(data), "at": datetime.now(timezone.utc), "by": _who()}
    _db["cameras_models"].update_one({"_id": m["_id"]}, {"$set": {"datasheet": ds}})
    _audit("modele_fiche", detail={"modele": cameras_models.name(m), "fichier": fname})
    return jsonify(cameras_models.pub(_db["cameras_models"].find_one({"_id": m["_id"]})))


@cameras_bp.route("/api/cameras/models/<mid>/datasheet", methods=["GET"])
def get_datasheet(mid):
    m = _model_doc(mid)
    path = cameras_models.datasheet_path(m["_id"]) if m else ""
    if not m or not m.get("datasheet") or not os.path.isfile(path):
        return jsonify({"error": "Pas de fiche technique"}), 404
    return send_file(path, mimetype="application/pdf", as_attachment=False,
                     download_name=m["datasheet"].get("name") or "fiche.pdf")


@cameras_bp.route("/api/cameras/models/<mid>/datasheet", methods=["DELETE"])
def delete_datasheet(mid):
    m = _model_doc(mid)
    if not m:
        return jsonify({"error": "Modele introuvable"}), 404
    try:
        os.remove(cameras_models.datasheet_path(m["_id"]))
    except OSError:
        pass
    _db["cameras_models"].update_one({"_id": m["_id"]}, {"$unset": {"datasheet": ""}})
    return jsonify(cameras_models.pub(_db["cameras_models"].find_one({"_id": m["_id"]})))


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
# Delai minimum entre la fin d'une capture de masse et le lancement suivant
CAPTURE_ALL_COOLDOWN_S = 120
_capture_job = {"running": False}
_capture_all_end = [-1e9]
_capture_lock = threading.Lock()


def _capture_one(doc, no=None):
    """Capture d'un canal dans sa vignette (l'appelant a reserve l'appareil).
    Pas de fichier horodate : seules les vignettes sont gardees."""
    cam = make_hik_camera(doc, no)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    latest = os.path.join(UPLOAD_DIR, _snapshot_name(str(doc["_id"]), cam.channel))
    try:
        call_camera(doc, lambda: cam.capture_image(latest + ".tmp"))
    except Exception:
        _drop(latest + ".tmp")
        raise
    os.replace(latest + ".tmp", latest)


def _capture_channels(doc):
    """Numeros des canaux actifs d'un appareil (au moins le 1er)."""
    nos = [int(c.get("no", i + 1)) for i, c in enumerate(doc.get("channels") or []) if c.get("enabled", True) is not False]
    return nos or [1]


def _run_capture_job(docs):
    # Un appareil a la fois par thread, ses canaux l'un apres l'autre : un
    # enregistreur a 5 cameras n'est pas sollicite 5 fois en meme temps
    def one(doc):
        nos = _capture_channels(doc)
        ip = doc.get("ip")
        for no in nos:
            # Attend son tour si une capture manuelle occupe l'appareil
            if not _device_acquire(ip, wait=True):
                ok, err = False, "Camera occupee par une autre capture"
            else:
                try:
                    _capture_one(doc, no)
                    ok, err = True, ""
                except Exception as e:
                    ok, err = False, camera_error(doc, e, "Capture de masse")
                finally:
                    _device_release(ip, no)
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
            _capture_all_end[0] = time.monotonic()
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
        wait = CAPTURE_ALL_COOLDOWN_S - (time.monotonic() - _capture_all_end[0])
        if wait > 0:
            return jsonify({"error": "Derniere capture de masse trop recente : patienter %d s" % max(1, round(wait))}), 429
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
    _audit("profil_creation", detail={"profil": name, "user": (data.get("user") or "").strip()[:60],
                                      "mot_de_passe": bool(password)})
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
    _audit("profil_modification", detail=dict({k: v for k, v in patch.items() if k in ("name", "user", "note")},
                                              profil=res.get("name"), mot_de_passe_change="secret" in patch))
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
    prof = _col_profiles.find_one_and_delete({"_id": oid})
    if prof:
        _audit("profil_suppression", detail={"profil": prof.get("name"), "user": prof.get("user", "")})
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


def _drop(path):
    try:
        os.remove(path)
    except OSError:
        pass


class CaptureBusy(RuntimeError):
    """Capture refusee : appareil occupe ou derniere capture du canal trop recente (HTTP 429)."""


def capture_to(doc, channel, save_path=None):
    """Capture manuelle d'un canal (page Cameras, main courante) : une a la
    fois par appareil (IP), CAPTURE_MIN_INTERVAL_S entre deux captures d'un
    meme canal (les canaux d'un enregistreur se capturent a la suite sans
    attendre), vignette du BON canal mise a jour. save_path None : la vignette
    seule. -> (chemin de l'image, numero du canal). Leve PisteCameraLocked,
    CaptureBusy, CameraKeyMissing ou l'erreur de la camera."""
    cam = make_hik_camera(doc, channel)
    ip, key = doc.get("ip"), (doc.get("ip"), cam.channel)
    with _capture_guard:
        if ip in _capture_busy:
            raise CaptureBusy("Capture deja en cours sur cette camera")
        wait = CAPTURE_MIN_INTERVAL_S - (time.monotonic() - _capture_last.get(key, -1e9))
        if wait > 0:
            raise CaptureBusy("Patienter %d s avant une nouvelle capture" % max(1, round(wait)))
        _capture_busy.add(ip)
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        latest = os.path.join(UPLOAD_DIR, _snapshot_name(str(doc["_id"]), cam.channel))
        target = save_path or latest + ".tmp"
        try:
            call_camera(doc, lambda: cam.capture_image(target))
        except Exception:
            _drop(latest + ".tmp")
            raise
        if save_path:
            shutil.copy2(save_path, latest)
        else:
            os.replace(target, latest)
            target = latest
        return target, cam.channel
    finally:
        _device_release(ip, cam.channel)


@cameras_bp.route("/api/cameras/<cam_id>/capture", methods=["POST"])
def capture_snapshot(cam_id):
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    err = _require_control(doc)
    if err:
        return err
    try:
        path, _no = capture_to(doc, _channel_arg())
        with open(path, "rb") as f:
            return Response(f.read(), mimetype="image/jpeg")
    except CaptureBusy as e:
        return jsonify({"error": str(e)}), 429
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
        _audit("essuie_glace", doc, {"channel": cam.channel, "ok": bool(result)})
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
    _audit("preset", doc, {"channel": cam.channel, "preset": preset, "ok": bool(ok)})
    return jsonify({"ok": bool(ok)})


# ---------------------------------------------------------------------------
# Masques de vie privee : lecture de l'etat et bascule globale par appareil.
# Seuls les masques DEJA dessines sur la camera sont actives / desactives :
# Cockpit n'en cree, n'en deplace ni n'en supprime jamais. Releve du
# 21/10/2026 : Hikvision (interrupteur par canal, 32 cameras avec masques),
# Hanwha (image.cgi msubmenu=privacy, interrupteur global), Dahua domes
# (24 emplacements PrivacyMasking[canal][i], sans interrupteur global :
# emplacement "dessine" = Rect / Polygon non nuls), Bosch domes (RCP+
# 0x0bd8, ONVIF GetMasks = "Action Failed"). Axis : non pilotable. Chaque
# bascule est tracee dans `cameras_privacy_log`.
# ---------------------------------------------------------------------------
# Marques dont les masques sont pilotables : logique par marque dans les
# pilotes (hik/drivers, methode privacy), verifiee sur le parc
from hik.drivers import PRIVACY_BRANDS  # noqa: E402


def _privacy_run(doc, set_to=None):
    cam = make_hik_camera(doc)
    chans = call_camera(doc, lambda: cam.privacy(set_to))
    known = [c for c in chans if c["masks"] is not False]
    return {
        "supported": True,
        "channels": chans,
        "configured": bool(chans),
        "enabled": any(c["enabled"] for c in known),
        "all_enabled": bool(known) and all(c["enabled"] for c in known),
    }


def _is_super_admin():
    from app import SUPER_ADMIN_ROLE
    return SUPER_ADMIN_ROLE in ((getattr(request, "user_payload", {}) or {}).get("global_roles") or [])


PRIVACY_SETTLE_S = 1.5  # la camera applique le masque avant la capture de controle


def _privacy_log(doc, set_to, ok, state=None, detail="", bulk=False, who=None):
    entry = {"camera_id": doc["_id"], "name": doc.get("name"), "ip": doc.get("ip"), "enabled": set_to, "ok": ok,
             "by": who if who is not None else _who(), "at": datetime.now(timezone.utc)}
    if state is not None:
        entry["channels"] = state.get("channels")
    if detail:
        entry["detail"] = detail
    if bulk:
        entry["bulk"] = True
    _db["cameras_privacy_log"].insert_one(entry)


def _privacy_capture(doc, state):
    """Capture de controle des canaux masques apres une bascule (vignettes a
    jour). Reserve l'appareil comme la capture manuelle. -> canaux captures."""
    nos = [c["no"] for c in state.get("channels") or []] or [1]
    active = set(_capture_channels(doc))
    nos = [n for n in nos if n in active] or nos[:1]
    time.sleep(PRIVACY_SETTLE_S)
    done, ip = [], doc.get("ip")
    for no in nos:
        if not _device_acquire(ip, wait=True):
            continue
        try:
            _capture_one(doc, no)
            done.append(no)
        except Exception as e:
            camera_error(doc, e, "Capture apres bascule des masques")
        finally:
            _device_release(ip, no)
    return done


# Bascule de TOUS les masques (super admin) : une seule a la fois, dans ses
# propres threads, progression par appareil (GET)
PRIVACY_ALL_WORKERS = 8
_privacy_job = {"running": False}
_privacy_job_lock = threading.Lock()


def _run_privacy_job(docs, set_to, who):
    def one(doc):
        res = "failed"
        err = ""
        try:
            state = _privacy_run(doc, None)
            if not state.get("configured"):
                res = "nomask"
            elif (state["all_enabled"] if set_to else not state["enabled"]):
                res = "unchanged"
            else:
                state = _privacy_run(doc, set_to)
                ok = state["all_enabled"] if set_to else not state["enabled"]
                _privacy_log(doc, set_to, ok, state, bulk=True, who=who)
                if ok:
                    res = "changed"
                    _privacy_capture(doc, state)
                else:
                    err = "etat relu different apres la bascule"
        except Exception as e:
            err = camera_error(doc, e, "Masques de vie privee (masse)")
            _privacy_log(doc, set_to, False, detail=err, bulk=True, who=who)
        with _privacy_job_lock:
            _privacy_job["done"] += 1
            _privacy_job[res] = _privacy_job.get(res, 0) + 1
            if res == "failed":
                _privacy_job["failures"].append({"id": str(doc["_id"]), "name": doc.get("name", ""), "error": err})
            if res == "changed":
                _privacy_job["captured"].append(str(doc["_id"]))

    try:
        with ThreadPoolExecutor(max_workers=PRIVACY_ALL_WORKERS) as pool:
            list(pool.map(one, docs))
    finally:
        with _privacy_job_lock:
            _privacy_job["running"] = False
            _privacy_job["finished_at"] = _iso(datetime.now(timezone.utc))
        logger.info("Masques de vie privee (masse, %s) termine : %s", "activation" if set_to else "desactivation",
                    {k: _privacy_job.get(k, 0) for k in ("changed", "unchanged", "nomask", "failed")})


@cameras_bp.route("/api/cameras/privacy-all", methods=["GET", "POST"])
def privacy_all():
    """SUPER ADMIN. POST {enabled: bool} : active / desactive les masques deja
    dessines de toutes les cameras pilotables (Hikvision, Hanwha, Dahua, Bosch
    domes ; jamais la piste), capture de controle des cameras basculees.
    GET : progression."""
    if not _is_super_admin():
        return jsonify({"error": "Reserve aux super administrateurs"}), 403
    if request.method == "GET":
        with _privacy_job_lock:
            return jsonify(dict(_privacy_job, failures=list(_privacy_job.get("failures") or []),
                                captured=list(_privacy_job.get("captured") or [])))
    _ensure_db()
    set_to = bool((request.get_json(silent=True) or {}).get("enabled"))
    docs = [d for d in _col_cameras.find({"enabled": {"$ne": False}, "brand": {"$in": sorted(PRIVACY_BRANDS)}})
            if not is_piste_device(d) and d.get("credential_profile_id")]
    if not docs:
        return jsonify({"error": "Aucune camera pilotable"}), 400
    with _privacy_job_lock:
        if _privacy_job.get("running"):
            return jsonify({"error": "Une bascule de masse des masques est deja en cours"}), 409
        _privacy_job.clear()
        _privacy_job.update({"running": True, "enabled": set_to, "total": len(docs), "done": 0, "changed": 0,
                             "unchanged": 0, "nomask": 0, "failed": 0, "failures": [], "captured": [], "by": _who(),
                             "started_at": _iso(datetime.now(timezone.utc)), "finished_at": None})
    threading.Thread(target=_run_privacy_job, args=(docs, set_to, _who()), daemon=True,
                     name="privacy-all").start()
    logger.warning("Masques de vie privee : %s de masse lancee par %s sur %d camera(s)",
                   "ACTIVATION" if set_to else "DESACTIVATION", _who(), len(docs))
    return jsonify({"ok": True, "total": len(docs)}), 202


@cameras_bp.route("/api/cameras/<cam_id>/privacy", methods=["GET", "POST"])
def camera_privacy(cam_id):
    """GET : etat des masques de vie privee (lu sur la camera). POST
    {enabled: bool} : active / desactive les masques deja dessines, puis relit."""
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    err = _require_control(doc)
    if err:
        return err
    if doc.get("brand") not in PRIVACY_BRANDS:
        return jsonify({"supported": False, "reason": "Masques non pilotables pour la marque %s : a regler dans l'interface de la camera"
                        % BRANDS.get(doc.get("brand"), doc.get("brand"))})
    set_to = None
    if request.method == "POST":
        set_to = bool((request.get_json(silent=True) or {}).get("enabled"))
    try:
        state = _privacy_run(doc, set_to)
    except Exception as e:
        msg = camera_error(doc, e, "Masques de vie privee")
        if set_to is not None:
            _privacy_log(doc, set_to, False, detail=msg)
        return jsonify({"error": msg}), 502
    if set_to is not None:
        ok = state["configured"] and (state["all_enabled"] if set_to else not state["enabled"])
        _privacy_log(doc, set_to, ok, state)
        logger.info("Masques de vie privee %s sur %s (%s) par %s : %s", "ACTIVES" if set_to else "DESACTIVES",
                    doc.get("name"), doc.get("ip"), _who(), "ok" if ok else "etat relu different")
        state["ok"] = ok
        # Vignettes a jour : capture des canaux masques
        state["captured"] = _privacy_capture(doc, state) if state["configured"] else []
    last = _db["cameras_privacy_log"].find_one({"camera_id": doc["_id"], "ok": True}, sort=[("at", -1)])
    if last:
        state["last"] = {"enabled": last["enabled"], "by": last.get("by", ""), "at": _iso(last.get("at"))}
    return jsonify(state)


@cameras_bp.route("/api/cameras/<cam_id>/journal", methods=["GET"])
def camera_journal(cam_id):
    """Historique de l'appareil, le plus recent d'abord : actions de
    cameras_audit, deplacements (cameras_geo_edits) et masques
    (cameras_privacy_log)."""
    doc, err = _get_cam_doc(cam_id)
    if err:
        return err
    n = 50
    rows = []
    for a in _db["cameras_audit"].find({"camera_id": doc["_id"]}).sort("at", -1).limit(n):
        rows.append({"action": a["action"], "by": a.get("by", ""), "at": a.get("at"), "detail": a.get("detail")})
    for g in _db["cameras_geo_edits"].find({"camera_id": doc["_id"]}).sort("at", -1).limit(n):
        rows.append({"action": "position", "by": g.get("by", ""), "at": g.get("at"), "detail": {"new": g.get("new")}})
    for p in _db["cameras_privacy_log"].find({"camera_id": doc["_id"]}).sort("at", -1).limit(n):
        rows.append({"action": "masques", "by": p.get("by", ""), "at": p.get("at"),
                     "detail": {"enabled": p.get("enabled"), "ok": p.get("ok"), "bulk": bool(p.get("bulk"))}})
    rows.sort(key=lambda r: r["at"] or datetime.min, reverse=True)
    for r in rows:
        r["at"] = _iso(r["at"])
    return jsonify(rows[:n])


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
