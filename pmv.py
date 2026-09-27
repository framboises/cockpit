"""pmv.py -- Blueprint PMV : pilotage des remorques a panneau a message variable.

Page /pmv + API /api/pmv/*. Le serveur parle lui-meme aux remorques (TCP 9520,
protocole JetFileII, cf. pmv_panneau.py et pmv_protocole.py) : le navigateur ne
recoit jamais d'adresse a joindre et n'envoie jamais d'IP, seulement un panneau_id
du registre.

Roles
  manager : voir, tester, envoyer, remettre l'affichage d'avant, bibliotheque.
  admin   : registre des remorques, groupes, envoi "sans sauvegarde", IP fixe,
            remorque jumeau (127.0.0.1, banc d'essai).

Une operation (test / envoi / restauration) peut durer plusieurs dizaines de
secondes en 4G : elle tourne dans un thread (job), le front suit
GET /api/pmv/jobs/<id>. Une seule operation a la fois par remorque (verrou) :
la seconde recoit 409 {"ok": false, "error": "busy"}.

_JOBS et les verrous supposent UN SEUL process (vrai sous waitress, meme hypothese
que scan_report.py). Sous gunicorn multi-workers il faudrait un verrou Mongo.
"""
import base64
import binascii
import hashlib
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from bson import Binary, ObjectId
from flask import Blueprint, jsonify, render_template, request

import pmv_panneau as pn
import pmv_protocole as pp

pmv_bp = Blueprint("pmv", __name__)
logger = logging.getLogger("pmv")

PARIS = ZoneInfo("Europe/Paris")

COL_PANNEAUX = "pmv_panneaux"
COL_MESSAGES = "pmv_messages"
COL_GROUPES = "pmv_groupes"
COL_SAUVEGARDES = "pmv_sauvegardes"
COL_ENVOIS = "pmv_envois"
COL_PROGRAMMATIONS = "pmv_programmations"
COL_AUDIT = "pmv_audit"

AUDIT_TTL_S = 365 * 24 * 3600
JOB_TTL_S = 3600
JOURNAL_MAX = 400
CACHE_ETAT_S = 60


def _now():
    """Datetime naif, heure de Paris (convention Cockpit)."""
    return datetime.now(PARIS).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Base (import tardif : app.py importe ce module avant de definir db)
# ---------------------------------------------------------------------------
_index_ok = False
_index_lock = threading.Lock()


def _db():
    from app import db
    _ensure_indexes(db)
    return db


def _ensure_indexes(db):
    global _index_ok
    if _index_ok:
        return
    with _index_lock:
        if _index_ok:
            return
        db[COL_PANNEAUX].create_index("plaque", unique=True)
        db[COL_MESSAGES].create_index([("event", 1), ("year", 1)])
        db[COL_MESSAGES].create_index("categorie")
        db[COL_GROUPES].create_index("nom", unique=True)
        db[COL_SAUVEGARDES].create_index("panneau_id", unique=True)
        db[COL_ENVOIS].create_index([("panneau_id", 1), ("fin", -1)])
        db[COL_ENVOIS].create_index([("event", 1), ("year", 1)])
        db[COL_PROGRAMMATIONS].create_index([("statut", 1), ("at", 1)])
        db[COL_AUDIT].create_index("ts", expireAfterSeconds=AUDIT_TTL_S)
        _index_ok = True


# ---------------------------------------------------------------------------
# Auth : JSON 401/403 pour l'API (role_required redirige, ce qui casserait un fetch)
# ---------------------------------------------------------------------------
def _err(code, status=400, **extra):
    corps = {"ok": False, "error": code}
    corps.update(extra)
    return jsonify(corps), status


def _identifier():
    """Pose request.user_payload ; rend une reponse d'erreur ou None."""
    import jwt as pyjwt
    from app import (APP_KEY, CODING, JWT_ALGORITHM, JWT_SECRET, ROLE_HIERARCHY,
                     ROLE_ORDER, SUPER_ADMIN_ROLE)
    if CODING:
        role = request.args.get("as", "admin")
        if role not in ROLE_HIERARCHY:
            role = "admin"
        request.user_payload = {
            "roles": [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= ROLE_HIERARCHY[role]],
            "app_role": role, "is_super_admin": False,
            "firstname": "Bruce", "lastname": "WAYNE", "email": "bruce@wayneenterprise.com",
        }
        return None
    token = request.cookies.get("access_token")
    if not token:
        return _err("non_authentifie", 401)
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return _err("session_invalide", 401)
    super_admin = SUPER_ADMIN_ROLE in (payload.get("global_roles") or [])
    roles_by_app = payload.get("roles_by_app") or {}
    app_role = roles_by_app.get(APP_KEY) if isinstance(roles_by_app, dict) else None
    role = "admin" if super_admin else app_role
    if role not in ROLE_HIERARCHY:
        return _err("acces_refuse", 403)
    payload["roles"] = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= ROLE_HIERARCHY[role]]
    payload["app_role"] = role
    payload["is_super_admin"] = super_admin
    request.user_payload = payload
    return None


def _a_le_role(role):
    return role in (getattr(request, "user_payload", {}) or {}).get("roles", [])


def _est_admin():
    return _a_le_role("admin")


def _coding():
    from app import CODING
    return bool(CODING)


@pmv_bp.before_request
def _avant():
    if request.endpoint == "pmv.page_pmv":
        return None          # la page est gardee par role_required (redirection HTML)
    err = _identifier()
    if err:
        return err
    if not _a_le_role("manager"):
        return _err("role_manager_requis", 403)
    return None


def admin_requis(f):
    @wraps(f)
    def enveloppe(*args, **kwargs):
        if not _est_admin():
            return _err("role_admin_requis", 403)
        return f(*args, **kwargs)
    return enveloppe


def _utilisateur():
    p = getattr(request, "user_payload", {}) or {}
    nom = ("%s %s" % (p.get("firstname", ""), p.get("lastname", ""))).strip()
    return {"email": p.get("email", ""), "nom": nom or p.get("email", ""),
            "admin": "admin" in p.get("roles", [])}


def _ip_client():
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "")


def _audit(action, resultat, panneau=None, duree_s=None, detail=None, user=None, ip=None):
    try:
        u = user or _utilisateur()
        _db()[COL_AUDIT].insert_one({
            "ts": _now(), "user": u.get("email"), "user_nom": u.get("nom"),
            "ip": ip if ip is not None else _ip_client(),
            "action": action, "resultat": resultat,
            "panneau_id": str(panneau["_id"]) if panneau else None,
            "plaque": panneau.get("plaque") if panneau else None,
            "duree_s": duree_s, "detail": detail,
        })
    except Exception as e:           # l'audit ne doit jamais faire echouer l'operation
        logger.warning("pmv audit : %s", e)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def _page_manager(f):
    @wraps(f)
    def enveloppe(*args, **kwargs):
        from app import role_required
        return role_required("manager")(f)(*args, **kwargs)
    return enveloppe


@pmv_bp.route("/pmv")
@_page_manager
def page_pmv():
    payload = getattr(request, "user_payload", {}) or {}
    return render_template(
        "pmv.html",
        user_roles=payload.get("roles", []),
        user_firstname=payload.get("firstname", ""),
        user_lastname=payload.get("lastname", ""),
        user_email=payload.get("email", ""),
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def _iso(v):
    return v.isoformat(timespec="seconds") if isinstance(v, datetime) else v


def _pub(doc, exclure=()):
    if not doc:
        return None
    out = {}
    for k, v in doc.items():
        if k in exclure:
            continue
        if isinstance(v, ObjectId):
            v = str(v)
        elif isinstance(v, datetime):
            v = _iso(v)
        elif isinstance(v, (bytes, Binary)):
            continue
        elif isinstance(v, dict):
            v = _pub(v)
        elif isinstance(v, list):
            v = [_pub(x) if isinstance(x, dict) else (str(x) if isinstance(x, ObjectId) else _iso(x)) for x in v]
        out["id" if k == "_id" else k] = v
    return out


def _oid(texte):
    try:
        return ObjectId(texte)
    except Exception:
        return None


def _plaque_affichee(plaque):
    u = (plaque or "").upper()
    if len(u) == 7 and u[:2].isalpha() and u[2:5].isdigit() and u[5:].isalpha():
        return "%s-%s-%s" % (u[:2], u[2:5], u[5:])
    return u


def _get_panneau(pid):
    oid = _oid(pid)
    if oid is None:
        return None
    return _db()[COL_PANNEAUX].find_one({"_id": oid})


# ---------------------------------------------------------------------------
# Registre des remorques
# ---------------------------------------------------------------------------
def _nettoyer_texte(v, n=300):
    return str(v or "").strip()[:n]


def _valider_panneau(data, existant=None):
    """-> (champs, erreur)"""
    champs = {}
    if existant is None or "plaque" in data:
        plaque = pn.normaliser_plaque(data.get("plaque"))
        if not (4 <= len(plaque) <= 12):
            return None, "plaque_invalide"
        champs["plaque"] = plaque
    for k, n in (("nom", 80), ("localisation", 300), ("description", 500)):
        if k in data:
            champs[k] = _nettoyer_texte(data.get(k), n)
    for k, lo, hi in (("lat", -90, 90), ("lng", -180, 180)):
        if k in data:
            v = data.get(k)
            if v in (None, ""):
                champs[k] = None
            else:
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return None, "position_invalide"
                if not lo <= v <= hi:
                    return None, "position_invalide"
                champs[k] = v
    if "ip_fixe" in data:
        ip = _nettoyer_texte(data.get("ip_fixe"), 15)
        if ip and not pn.est_ipv4(ip):
            return None, "ip_invalide"
        champs["ip_fixe"] = ip or None
    if "jumeau" in data:
        champs["jumeau"] = bool(data.get("jumeau"))
    if "actif" in data:
        champs["actif"] = bool(data.get("actif"))
    fusion = dict(existant or {})
    fusion.update(champs)
    ip = fusion.get("ip_fixe")
    # 127.x = jumeau de banc d'essai : seulement sur une remorque explicitement marquee
    if ip and pn.est_boucle_locale(ip) and not fusion.get("jumeau"):
        return None, "boucle_locale_reservee_jumeau"
    if fusion.get("jumeau") and not (ip and pn.est_boucle_locale(ip)):
        return None, "jumeau_sans_boucle_locale"
    return champs, None


@pmv_bp.route("/api/pmv/panneaux", methods=["GET"])
def liste_panneaux():
    db = _db()
    docs = list(db[COL_PANNEAUX].find({}).sort([("actif", -1), ("nom", 1)]))
    admin = _est_admin()
    out = []
    for d in docs:
        p = _pub(d)
        p["plaque_affichee"] = _plaque_affichee(d.get("plaque"))
        if d.get("jumeau") and not admin and not _coding():
            continue
        out.append(p)
    return jsonify({"ok": True, "panneaux": out, "admin": admin})


@pmv_bp.route("/api/pmv/panneaux", methods=["POST"])
@admin_requis
def creer_panneau():
    data = request.get_json(silent=True) or {}
    champs, err = _valider_panneau(data)
    if err:
        return _err(err, 422)
    champs.setdefault("nom", _plaque_affichee(champs["plaque"]))
    champs.setdefault("actif", True)
    champs.setdefault("jumeau", False)
    champs.setdefault("ip_fixe", None)
    u = _utilisateur()
    champs.update({"created_at": _now(), "updated_at": _now(), "created_by": u["email"]})
    db = _db()
    if db[COL_PANNEAUX].find_one({"plaque": champs["plaque"]}):
        return _err("plaque_existante", 409)
    res = db[COL_PANNEAUX].insert_one(champs)
    doc = db[COL_PANNEAUX].find_one({"_id": res.inserted_id})
    _audit("registre_creation", "ok", doc)
    return jsonify({"ok": True, "panneau": _pub(doc)})


@pmv_bp.route("/api/pmv/panneaux/<pid>", methods=["PUT"])
@admin_requis
def modifier_panneau(pid):
    doc = _get_panneau(pid)
    if not doc:
        return _err("panneau_inconnu", 404)
    champs, err = _valider_panneau(request.get_json(silent=True) or {}, existant=doc)
    if err:
        return _err(err, 422)
    db = _db()
    if "plaque" in champs and champs["plaque"] != doc["plaque"]:
        if db[COL_PANNEAUX].find_one({"plaque": champs["plaque"]}):
            return _err("plaque_existante", 409)
    champs["updated_at"] = _now()
    db[COL_PANNEAUX].update_one({"_id": doc["_id"]}, {"$set": champs})
    _CACHE_ETAT.pop(str(doc["_id"]), None)
    doc = db[COL_PANNEAUX].find_one({"_id": doc["_id"]})
    _audit("registre_modification", "ok", doc, detail=sorted(k for k in champs if k != "updated_at"))
    return jsonify({"ok": True, "panneau": _pub(doc)})


@pmv_bp.route("/api/pmv/panneaux/<pid>", methods=["DELETE"])
@admin_requis
def supprimer_panneau(pid):
    doc = _get_panneau(pid)
    if not doc:
        return _err("panneau_inconnu", 404)
    if _verrou(str(doc["_id"])).locked():
        return _err("busy", 409)
    db = _db()
    db[COL_PANNEAUX].delete_one({"_id": doc["_id"]})
    db[COL_SAUVEGARDES].delete_one({"panneau_id": str(doc["_id"])})
    db[COL_GROUPES].update_many({}, {"$pull": {"panneau_ids": str(doc["_id"])}})
    _CACHE_ETAT.pop(str(doc["_id"]), None)
    _audit("registre_suppression", "ok", doc)
    return jsonify({"ok": True})


@pmv_bp.route("/api/pmv/panneaux/import", methods=["POST"])
@admin_requis
def importer_fiches():
    """Import de l'export JSON du volet Reseau de l'ancien editeur :
    {format:'pmv-fiches', v:1, fiches:[{plate, loc, desc}]} (ou la liste seule).
    Fusion par plaque : localisation/description mises a jour, nouvelles fiches ajoutees."""
    data = request.get_json(silent=True)
    fiches = data if isinstance(data, list) else (data or {}).get("fiches")
    if not isinstance(fiches, list):
        return _err("format_non_reconnu", 422)
    if len(fiches) > 500:
        return _err("trop_de_fiches", 422)
    db = _db()
    ajoutees = maj = ignorees = 0
    u = _utilisateur()
    for f in fiches:
        if not isinstance(f, dict):
            ignorees += 1
            continue
        plaque = pn.normaliser_plaque(f.get("plate") or f.get("plaque"))
        if not (4 <= len(plaque) <= 12):
            ignorees += 1
            continue
        loc = _nettoyer_texte(f.get("loc", f.get("localisation")), 300)
        desc = _nettoyer_texte(f.get("desc", f.get("description")), 500)
        existant = db[COL_PANNEAUX].find_one({"plaque": plaque})
        if existant:
            db[COL_PANNEAUX].update_one({"_id": existant["_id"]},
                                        {"$set": {"localisation": loc, "description": desc, "updated_at": _now()}})
            maj += 1
        else:
            db[COL_PANNEAUX].insert_one({
                "plaque": plaque, "nom": _plaque_affichee(plaque), "localisation": loc,
                "description": desc, "lat": None, "lng": None, "ip_fixe": None,
                "jumeau": False, "actif": True, "created_at": _now(), "updated_at": _now(),
                "created_by": u["email"],
            })
            ajoutees += 1
    _audit("registre_import", "ok", detail={"ajoutees": ajoutees, "maj": maj, "ignorees": ignorees})
    return jsonify({"ok": True, "ajoutees": ajoutees, "mises_a_jour": maj, "ignorees": ignorees})


# ---------------------------------------------------------------------------
# Etat : resolution DNS, affichage deduit, dernier envoi
# ---------------------------------------------------------------------------
_CACHE_ETAT = {}          # panneau_id -> (monotonic, resolution)
_CACHE_LOCK = threading.Lock()


def _resoudre(doc, force=False):
    """-> {etat, ip, ts, source}. etat : ok | introuvable | dns_indisponible | ip_fixe."""
    pid = str(doc["_id"])
    if doc.get("ip_fixe"):
        return {"etat": "jumeau" if doc.get("jumeau") else "ip_fixe", "ip": doc["ip_fixe"],
                "ts": _iso(_now()), "source": "registre"}
    with _CACHE_LOCK:
        c = _CACHE_ETAT.get(pid)
    if c and not force and time.monotonic() - c[0] < CACHE_ETAT_S:
        return c[1]
    try:
        ip, source = pn.resoudre_plaque(doc["plaque"])
        res = {"etat": "ok" if ip else "introuvable", "ip": ip, "source": source}
    except pn.DnsIndisponible as e:
        res = {"etat": "dns_indisponible", "ip": None, "source": None, "detail": str(e)}
    res["ts"] = _iso(_now())
    with _CACHE_LOCK:
        _CACHE_ETAT[pid] = (time.monotonic(), res)
    try:
        _db()[COL_PANNEAUX].update_one({"_id": doc["_id"]}, {"$set": {"derniere_resolution": res}})
    except Exception:
        pass
    return res


def _dernier_envoi(db, pid, seulement_ok=False):
    filtre = {"panneau_id": pid}
    if seulement_ok:
        filtre["resultat"] = "ok"
    return db[COL_ENVOIS].find_one(filtre, sort=[("fin", -1)], projection={"journal": 0, "etapes": 0})


def _affiche_deduit(doc, dernier_ok):
    """Ce qu'on peut dire de l'affichage : on ne lit que le NOM du fichier joue, jamais
    les pixels. Toujours une deduction."""
    lecture = doc.get("dernier_affichage") or {}
    noms = lecture.get("noms")
    ts_lecture = lecture.get("ts")
    envoi_plus_recent = dernier_ok and (not ts_lecture or dernier_ok.get("fin") and dernier_ok["fin"] >= ts_lecture)
    if envoi_plus_recent and dernier_ok.get("type") == "envoi":
        return {"type": "cockpit", "message_nom": dernier_ok.get("message_nom"),
                "png_b64": dernier_ok.get("png_b64"), "ts": _iso(dernier_ok.get("fin"))}
    if noms is None:
        return None
    if pp.NOM_MESSAGE in noms and dernier_ok and dernier_ok.get("type") == "envoi":
        return {"type": "cockpit", "message_nom": dernier_ok.get("message_nom"),
                "png_b64": dernier_ok.get("png_b64"), "ts": _iso(ts_lecture)}
    return {"type": "sigma", "noms": noms, "ts": _iso(ts_lecture)}


def _etat_panneau(doc, force=False):
    db = _db()
    pid = str(doc["_id"])
    dernier = _dernier_envoi(db, pid)
    dernier_ok = _dernier_envoi(db, pid, seulement_ok=True)
    sauv = db[COL_SAUVEGARDES].find_one({"panneau_id": pid}, projection={"sequent": 0})
    return {
        "id": pid,
        "resolution": _resoudre(doc, force=force) if doc.get("actif", True) else {"etat": "inactif"},
        "affiche_deduit": _affiche_deduit(doc, dernier_ok),
        "dernier_envoi": _pub(dernier),
        "sauvegarde": _pub(sauv),
        "occupe": _verrou(pid).locked(),
        "job_en_cours": _job_en_cours(pid),
    }


@pmv_bp.route("/api/pmv/panneaux/etat", methods=["GET"])
def etat_panneaux():
    docs = list(_db()[COL_PANNEAUX].find({}))
    if not (_est_admin() or _coding()):
        docs = [d for d in docs if not d.get("jumeau")]
    force = request.args.get("force") == "1"
    with ThreadPoolExecutor(max_workers=4) as ex:
        etats = list(ex.map(lambda d: _etat_panneau(d, force), docs))
    return jsonify({"ok": True, "etats": etats, "ts": _iso(_now())})


@pmv_bp.route("/api/pmv/panneaux/<pid>/etat", methods=["GET"])
def etat_panneau(pid):
    doc = _get_panneau(pid)
    if not doc or (doc.get("jumeau") and not (_est_admin() or _coding())):
        return _err("panneau_inconnu", 404)
    return jsonify({"ok": True, "etat": _etat_panneau(doc, force=request.args.get("force") == "1")})


# ---------------------------------------------------------------------------
# Jobs (un thread par operation) et verrous par remorque
# ---------------------------------------------------------------------------
_JOBS = {}
_JOBS_LOCK = threading.Lock()
_VERROUS = {}

LIBELLES_ETAPES = {
    "connexion": "Connexion",
    "dalle": "Dalle 96 x 64 verifiee",
    "affichage": "Affichage actuel lu",
    "sauvegarde": "Affichage actuel sauvegarde",
    "image": "Envoi de l'image",
    "affiche": "Mise a l'affiche",
}
ETAPES_PAR_TYPE = {
    "test": ["connexion", "dalle", "affichage"],
    "envoi": ["connexion", "dalle", "sauvegarde", "image", "affiche"],
    "restaurer": ["connexion", "dalle", "affiche"],
}

MESSAGES_ERREUR = {
    "injoignable": "La remorque ne repond pas : connexion impossible (eteinte ou hors couverture 4G ?).",
    "ne_repond_pas": "La remorque ne repond pas : aucune reponse apres 3 essais.",
    "connexion_fermee": "La connexion avec la remorque a ete coupee.",
    "delai_depasse": "Operation trop longue, interrompue (reseau 4G tres lent ?).",
    "refus_panneau": "Le panneau a refuse la commande.",
    "config_illisible": "Dimensions de la dalle illisibles : rien n'a ete envoye.",
    "dalle_incompatible": "Dalle incompatible : rien n'a ete envoye.",
    "sauvegarde_impossible": "Affichage actuel illisible : rien n'a ete envoye. Un admin peut envoyer sans sauvegarde.",
    "sauvegarde_invalide": "La sauvegarde n'est pas une liste de lecture valide : refus.",
    "dns_introuvable": "Adresse introuvable pour cette plaque : remorque eteinte ?",
    "dns_indisponible": "Resolution DNS impossible depuis le serveur.",
    "jumeau_reserve_admin": "Le jumeau de banc d'essai est reserve aux admins.",
    "commande_interdite": "Commande interdite par la liste blanche : rien n'a ete envoye.",
    "erreur_interne": "Erreur interne pendant l'operation.",
}


def _verrou(pid):
    with _JOBS_LOCK:
        v = _VERROUS.get(pid)
        if v is None:
            v = _VERROUS[pid] = threading.Lock()
        return v


def _balayer_jobs():
    """Appele avec _JOBS_LOCK tenu."""
    limite = time.time() - JOB_TTL_S
    for jid in [j for j, job in _JOBS.items() if job.get("_fin_epoch") and job["_fin_epoch"] < limite]:
        _JOBS.pop(jid, None)


def _job_en_cours(pid):
    with _JOBS_LOCK:
        for jid, job in _JOBS.items():
            if job["panneau_id"] == pid and job["status"] in ("queued", "running"):
                return jid
    return None


def _maj_job(jid, **champs):
    with _JOBS_LOCK:
        job = _JOBS.get(jid)
        if job:
            job.update(champs)


def _snapshot(jid):
    with _JOBS_LOCK:
        job = _JOBS.get(jid)
        if not job:
            return None
        out = {k: v for k, v in job.items() if not k.startswith("_")}
        out["etapes"] = [dict(e) for e in job["etapes"]]
        out["journal"] = list(job["journal"])
        return out


@pmv_bp.route("/api/pmv/jobs/<jid>", methods=["GET"])
def suivre_job(jid):
    snap = _snapshot(jid)
    if not snap:
        return _err("job_inconnu", 404)
    if request.args.get("journal") != "1":
        snap.pop("journal", None)
    return jsonify({"ok": True, "job": snap})


def _lancer_job(type_op, doc, user, ip_client, params):
    """Prend le verrou de la remorque et demarre le thread. -> job_id, ou None si occupee."""
    reserve = _reserver_job(type_op, doc, params)
    if reserve is None:
        return None
    jid, verrou = reserve
    try:
        threading.Thread(target=_executer, name="pmv-job-" + jid[:8], daemon=True,
                         args=(jid, verrou, type_op, doc, user, ip_client, params)).start()
    except BaseException:
        verrou.release()
        with _JOBS_LOCK:
            _JOBS.pop(jid, None)
        raise
    return jid


def _reserver_job(type_op, doc, params):
    """Prend le verrou de la remorque et cree le job (statut queued), SANS le demarrer.
    -> (job_id, verrou) ou None si la remorque est occupee. L'appelant DOIT ensuite
    appeler _executer(job_id, verrou, ...) : c'est lui qui libere le verrou."""
    pid = str(doc["_id"])
    verrou = _verrou(pid)
    if not verrou.acquire(blocking=False):
        return None
    jid = uuid.uuid4().hex
    job = {
        "id": jid, "type": type_op, "panneau_id": pid,
        "plaque": _plaque_affichee(doc.get("plaque")), "nom": doc.get("nom"),
        "status": "queued", "progress": 0,
        "etapes": [{"code": c, "libelle": LIBELLES_ETAPES[c], "etat": "attente", "detail": None}
                   for c in ETAPES_PAR_TYPE[type_op]],
        "resultat": None, "erreur_code": None, "message": None, "journal": [],
        "started_at": _iso(_now()), "finished_at": None, "duree_s": None,
        "groupe_run_id": params.get("groupe_run_id"),
    }
    with _JOBS_LOCK:
        _balayer_jobs()
        _JOBS[jid] = job
    return jid, verrou


def _executer(jid, verrou, type_op, doc, user, ip_client, params):
    t0 = time.monotonic()
    debut = _now()
    pid = str(doc["_id"])
    resultat, code, message, detail_ok = "ko", None, None, {}

    def journal(sens, texte):
        ligne = "%s %s %s" % (datetime.now(PARIS).strftime("%H:%M:%S.%f")[:-3], sens, texte)
        with _JOBS_LOCK:
            job = _JOBS.get(jid)
            if job is not None:
                job["journal"].append(ligne)
                if len(job["journal"]) > JOURNAL_MAX:
                    del job["journal"][:len(job["journal"]) - JOURNAL_MAX]

    def etape(code_etape, etat, detail=None, progression=None):
        with _JOBS_LOCK:
            job = _JOBS.get(jid)
            if job is None:
                return
            for e in job["etapes"]:
                if e["code"] == code_etape:
                    e["etat"] = etat
                    if detail is not None:
                        e["detail"] = detail
            if progression is not None:
                job["progress"] = round(progression * 100)

    _maj_job(jid, status="running")
    try:
        etape("connexion", "en_cours")
        if doc.get("jumeau") and not (user.get("admin") or _coding()):
            raise pn.EchecPanneau("jumeau_reserve_admin", MESSAGES_ERREUR["jumeau_reserve_admin"])
        if doc.get("ip_fixe"):
            ip = doc["ip_fixe"]
            if pn.est_boucle_locale(ip) and not doc.get("jumeau"):
                raise pn.EchecPanneau("jumeau_reserve_admin", MESSAGES_ERREUR["jumeau_reserve_admin"])
        else:
            res = _resoudre(doc, force=True)
            if res["etat"] == "introuvable":
                raise pn.EchecPanneau("dns_introuvable", MESSAGES_ERREUR["dns_introuvable"])
            if res["etat"] != "ok":
                raise pn.EchecPanneau("dns_indisponible", MESSAGES_ERREUR["dns_indisponible"])
            ip = res["ip"]
            if pn.est_boucle_locale(ip):       # un DNS ne doit jamais nous renvoyer sur nous-memes
                raise pn.EchecPanneau("jumeau_reserve_admin", MESSAGES_ERREUR["jumeau_reserve_admin"])
        journal("info", "%s -> %s" % (pn.nom_dns(doc["plaque"]) if not doc.get("ip_fixe") else "IP fixe", ip))
        with pn.Panneau(ip, pp.PORT, journal=journal) as p:
            etape("connexion", "ok", ip)
            if type_op == "test":
                r = pn.operation_test(p, etape)
                detail_ok = {"noms": r["noms"], "dalle": r["dalle"]}
                if r["noms"] is not None:
                    _db()[COL_PANNEAUX].update_one({"_id": doc["_id"]}, {"$set": {
                        "dernier_affichage": {"noms": r["noms"], "ts": _now(), "source": "test"}}})
                message = "Connexion reussie. Dalle 96 x 64, affiche : %s." % (
                    ", ".join(r["noms"]) if r["noms"] else "liste de lecture illisible")
            elif type_op == "envoi":
                db = _db()
                sauv = db[COL_SAUVEGARDES].find_one({"panneau_id": pid})
                r = pn.operation_envoi(p, params["fichier"],
                                       sauvegarde_existante=bytes(sauv["sequent"]) if sauv else None,
                                       sans_sauvegarde=params.get("sans_sauvegarde", False), etape=etape)
                if r["nouvelle_sauvegarde"] is not None:
                    db[COL_SAUVEGARDES].update_one({"panneau_id": pid}, {"$set": {
                        "panneau_id": pid, "sequent": Binary(r["nouvelle_sauvegarde"]),
                        "noms": pp.parse_sequent(r["nouvelle_sauvegarde"]),
                        "ts": _now(), "par": user.get("email")}}, upsert=True)
                db[COL_PANNEAUX].update_one({"_id": doc["_id"]}, {"$set": {
                    "dernier_affichage": {"noms": [pp.NOM_MESSAGE], "ts": _now(), "source": "envoi"}}})
                detail_ok = {"noms_avant": r["noms_avant"]}
                message = None
            elif type_op == "restaurer":
                sauv = _db()[COL_SAUVEGARDES].find_one({"panneau_id": pid})
                if not sauv:
                    raise pn.EchecPanneau("sauvegarde_invalide", "Aucune sauvegarde pour cette remorque.")
                r = pn.operation_restaurer(p, bytes(sauv["sequent"]), etape)
                _db()[COL_PANNEAUX].update_one({"_id": doc["_id"]}, {"$set": {
                    "dernier_affichage": {"noms": r["noms"], "ts": _now(), "source": "restauration"}}})
                detail_ok = {"noms": r["noms"]}
                message = "Affichage d'avant remis sur %s : %s. Verifiez a l'oeil." % (
                    _plaque_affichee(doc["plaque"]), ", ".join(r["noms"]))
        resultat = "ok"
    except pn.EchecPanneau as e:
        code = e.code
        message = MESSAGES_ERREUR.get(code, str(e))
        if code in ("dalle_incompatible",):
            message = "%s : rien n'a ete envoye." % str(e).split(" :")[0].capitalize()
        journal("erreur", str(e))
    except pp.CommandeInterdite as e:
        code, message = "commande_interdite", MESSAGES_ERREUR["commande_interdite"]
        journal("erreur", str(e))
    except BaseException as e:           # SystemExit & co : le job ne doit jamais rester bloque
        code, message = "erreur_interne", MESSAGES_ERREUR["erreur_interne"]
        logger.exception("pmv job %s", jid)
        journal("erreur", repr(e))
    finally:
        duree = round(time.monotonic() - t0, 1)
        if resultat == "ok" and type_op == "envoi":
            message = "Affiche sur %s en %s s. Verifiez a l'oeil." % (
                _plaque_affichee(doc["plaque"]), ("%.1f" % duree).replace(".", ","))
        with _JOBS_LOCK:
            job = _JOBS.get(jid)
            if job is not None:
                for e in job["etapes"]:
                    if e["etat"] == "en_cours":
                        e["etat"] = "ko" if resultat != "ok" else "ok"
                job.update({"status": "done" if resultat == "ok" else "error", "resultat": resultat,
                            "erreur_code": code, "message": message, "detail": detail_ok,
                            "finished_at": _iso(_now()), "duree_s": duree, "_fin_epoch": time.time()})
                if resultat == "ok":
                    job["progress"] = 100
                etapes_fin = [dict(e) for e in job["etapes"]]
                journal_fin = list(job["journal"])
            else:
                etapes_fin, journal_fin = [], []
        try:
            if type_op in ("envoi", "restaurer"):
                _db()[COL_ENVOIS].insert_one({
                    "job_id": jid, "type": type_op, "panneau_id": pid, "plaque": doc.get("plaque"),
                    "panneau_nom": doc.get("nom"), "jumeau": bool(doc.get("jumeau")),
                    "message_id": params.get("message_id"), "message_nom": params.get("message_nom"),
                    "sha256": params.get("sha256"), "png_b64": params.get("png_b64"),
                    "sans_sauvegarde": bool(params.get("sans_sauvegarde")),
                    "user": user.get("email"), "user_nom": user.get("nom"),
                    "event": params.get("event"), "year": params.get("year"),
                    "debut": debut, "fin": _now(), "duree_s": duree,
                    "resultat": resultat, "erreur_code": code, "message": message,
                    "etapes": etapes_fin, "journal": journal_fin,
                    "groupe_run_id": params.get("groupe_run_id"),
                    "programmation_id": params.get("programmation_id"),
                })
        except Exception as e:
            logger.warning("pmv historique : %s", e)
        _audit(type_op, resultat, doc, duree_s=duree, detail={"code": code, "job": jid},
               user=user, ip=ip_client)
        verrou.release()


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def _contexte_evenement(data):
    event = _nettoyer_texte(data.get("event"), 80) or None
    try:
        year = int(data.get("year")) if data.get("year") not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    return event, year


def _panneau_operable(pid):
    doc = _get_panneau(pid)
    if not doc:
        return None, _err("panneau_inconnu", 404)
    if doc.get("jumeau") and not (_est_admin() or _coding()):
        return None, _err("jumeau_reserve_admin", 403)
    if not doc.get("actif", True):
        return None, _err("panneau_inactif", 409)
    return doc, None


def _reponse_job(jid, doc, action):
    if jid is None:
        _audit(action, "busy", doc)
        return _err("busy", 409, message="Une operation est deja en cours sur cette remorque.")
    return jsonify({"ok": True, "job_id": jid})


@pmv_bp.route("/api/pmv/panneaux/<pid>/test", methods=["POST"])
def tester(pid):
    doc, err = _panneau_operable(pid)
    if err:
        return err
    jid = _lancer_job("test", doc, _utilisateur(), _ip_client(), {})
    return _reponse_job(jid, doc, "test")


def _decoder_image(texte):
    """PNG en base64 (data URL accepte) -> (pixels, png_normalise, sha256). Leve ImageInvalide."""
    if not isinstance(texte, str) or not texte:
        raise pp.ImageInvalide("image absente")
    if texte.startswith("data:"):
        texte = texte.split(",", 1)[-1]
    if len(texte) > 2_000_000:
        raise pp.ImageInvalide("image trop lourde")
    try:
        brut = base64.b64decode(texte, validate=True)
    except (binascii.Error, ValueError):
        raise pp.ImageInvalide("image illisible")
    px = pp.pixels_from_image_bytes(brut)
    rgb = pp.rgb_from_pixels(px)
    return px, pp.png_from_pixels(px), hashlib.sha256(rgb).hexdigest()


def preparer_envoi(data, user):
    """Commun a l'envoi unitaire, groupe et programme. -> (params, erreur_code, detail)."""
    params = {}
    message_id = data.get("message_id")
    if message_id:
        oid = _oid(message_id)
        msg = _db()[COL_MESSAGES].find_one({"_id": oid}) if oid else None
        if not msg:
            return None, "message_inconnu", None
        px = pp.pixels_from_rgb(bytes(msg["rgb"]))
        png = pp.png_from_pixels(px)
        params.update({"message_id": str(msg["_id"]), "message_nom": msg.get("nom")})
        sha = msg.get("sha256") or hashlib.sha256(bytes(msg["rgb"])).hexdigest()
    else:
        try:
            px, png, sha = _decoder_image(data.get("png_b64"))
        except pp.ImageInvalide as e:
            return None, "image_invalide", str(e)
        params["message_nom"] = _nettoyer_texte(data.get("nom"), 80) or None
    event, year = _contexte_evenement(data)
    params.update({
        "fichier": pp.fichier_message(px), "sha256": sha,
        "png_b64": base64.b64encode(png).decode("ascii"),
        "sans_sauvegarde": bool(data.get("sans_sauvegarde")),
        "event": event, "year": year,
    })
    if params["sans_sauvegarde"] and not user.get("admin"):
        return None, "role_admin_requis", None
    return params, None, None


def _deja_affiche(pid, sha):
    """Regle flash : le dernier envoi reussi porte la meme image et rien n'a ete lu depuis
    qui montrerait un autre affichage."""
    db = _db()
    dernier = _dernier_envoi(db, pid, seulement_ok=True)
    if not dernier or dernier.get("type") != "envoi" or dernier.get("sha256") != sha:
        return False
    doc = db[COL_PANNEAUX].find_one({"_id": ObjectId(pid)}, {"dernier_affichage": 1})
    lecture = (doc or {}).get("dernier_affichage") or {}
    return pp.NOM_MESSAGE in (lecture.get("noms") or [pp.NOM_MESSAGE])


@pmv_bp.route("/api/pmv/panneaux/<pid>/envoi", methods=["POST"])
def envoyer(pid):
    doc, err = _panneau_operable(pid)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    user = _utilisateur()
    params, code, detail = preparer_envoi(data, user)
    if code:
        return _err(code, 403 if code == "role_admin_requis" else 422, detail=detail)
    if not data.get("force") and _deja_affiche(str(doc["_id"]), params["sha256"]):
        return _err("deja_affiche", 409,
                    message="Ce message est deja affiche sur cette remorque (d'apres l'historique).")
    jid = _lancer_job("envoi", doc, user, _ip_client(), params)
    return _reponse_job(jid, doc, "envoi")


@pmv_bp.route("/api/pmv/panneaux/<pid>/restaurer", methods=["POST"])
def restaurer(pid):
    doc, err = _panneau_operable(pid)
    if err:
        return err
    sauv = _db()[COL_SAUVEGARDES].find_one({"panneau_id": str(doc["_id"])}, {"sequent": 1})
    if not sauv or not pp.is_sequent(bytes(sauv.get("sequent") or b"")):
        return _err("aucune_sauvegarde", 409, message="Aucune sauvegarde valable pour cette remorque.")
    jid = _lancer_job("restaurer", doc, _utilisateur(), _ip_client(), {})
    return _reponse_job(jid, doc, "restaurer")


# ---------------------------------------------------------------------------
# Bibliotheque de messages (manager)
# ---------------------------------------------------------------------------
def _pub_message(doc):
    p = _pub(doc)
    if p is not None:
        p["png_b64"] = doc.get("png_b64")
    return p


def _etiquettes(v):
    if isinstance(v, str):
        v = v.split(",")
    if not isinstance(v, list):
        return []
    out = []
    for e in v:
        e = _nettoyer_texte(e, 40)
        if e and e not in out:
            out.append(e)
    return out[:12]


def _image_message(data):
    """-> (champs image, erreur)"""
    try:
        px, png, sha = _decoder_image(data.get("png_b64"))
    except pp.ImageInvalide as e:
        return None, str(e)
    return {"rgb": Binary(pp.rgb_from_pixels(px)), "png_b64": base64.b64encode(png).decode("ascii"),
            "sha256": sha}, None


@pmv_bp.route("/api/pmv/messages", methods=["GET"])
def liste_messages():
    filtre = {}
    if request.args.get("categorie"):
        filtre["categorie"] = request.args["categorie"]
    if request.args.get("event"):
        filtre["event"] = request.args["event"]
    if request.args.get("year"):
        try:
            filtre["year"] = int(request.args["year"])
        except ValueError:
            pass
    q = _nettoyer_texte(request.args.get("q"), 60)
    if q:
        import re
        motif = {"$regex": re.escape(q), "$options": "i"}
        filtre["$or"] = [{"nom": motif}, {"etiquettes": motif}, {"categorie": motif}]
    db = _db()
    docs = db[COL_MESSAGES].find(filtre, projection={"rgb": 0}).sort("updated_at", -1).limit(500)
    categories = sorted(c for c in db[COL_MESSAGES].distinct("categorie") if c)
    return jsonify({"ok": True, "messages": [_pub_message(d) for d in docs], "categories": categories})


@pmv_bp.route("/api/pmv/messages", methods=["POST"])
def creer_message():
    data = request.get_json(silent=True) or {}
    nom = _nettoyer_texte(data.get("nom"), 80)
    if not nom:
        return _err("nom_requis", 422)
    image, err = _image_message(data)
    if err:
        return _err("image_invalide", 422, detail=err)
    event, year = _contexte_evenement(data)
    u = _utilisateur()
    doc = dict(image, nom=nom, categorie=_nettoyer_texte(data.get("categorie"), 40) or None,
               etiquettes=_etiquettes(data.get("etiquettes")), event=event, year=year,
               auteur=u["email"], auteur_nom=u["nom"], created_at=_now(), updated_at=_now())
    db = _db()
    res = db[COL_MESSAGES].insert_one(doc)
    _audit("message_creation", "ok", detail={"id": str(res.inserted_id), "nom": nom})
    doc = db[COL_MESSAGES].find_one({"_id": res.inserted_id}, projection={"rgb": 0})
    return jsonify({"ok": True, "message": _pub_message(doc)})


@pmv_bp.route("/api/pmv/messages/<mid>", methods=["PUT"])
def modifier_message(mid):
    oid = _oid(mid)
    db = _db()
    doc = db[COL_MESSAGES].find_one({"_id": oid}) if oid else None
    if not doc:
        return _err("message_inconnu", 404)
    data = request.get_json(silent=True) or {}
    champs = {}
    if "nom" in data:
        champs["nom"] = _nettoyer_texte(data.get("nom"), 80)
        if not champs["nom"]:
            return _err("nom_requis", 422)
    if "categorie" in data:
        champs["categorie"] = _nettoyer_texte(data.get("categorie"), 40) or None
    if "etiquettes" in data:
        champs["etiquettes"] = _etiquettes(data.get("etiquettes"))
    if data.get("png_b64"):
        image, err = _image_message(data)
        if err:
            return _err("image_invalide", 422, detail=err)
        champs.update(image)
    u = _utilisateur()
    champs.update({"updated_at": _now(), "modifie_par": u["email"], "modifie_par_nom": u["nom"]})
    db[COL_MESSAGES].update_one({"_id": oid}, {"$set": champs})
    _audit("message_modification", "ok", detail={"id": mid, "champs": sorted(champs)})
    doc = db[COL_MESSAGES].find_one({"_id": oid}, projection={"rgb": 0})
    return jsonify({"ok": True, "message": _pub_message(doc)})


@pmv_bp.route("/api/pmv/messages/<mid>", methods=["DELETE"])
def supprimer_message(mid):
    oid = _oid(mid)
    db = _db()
    doc = db[COL_MESSAGES].find_one({"_id": oid}, projection={"rgb": 0}) if oid else None
    if not doc:
        return _err("message_inconnu", 404)
    db[COL_MESSAGES].delete_one({"_id": oid})
    _audit("message_suppression", "ok", detail={"id": mid, "nom": doc.get("nom")})
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Historique
# ---------------------------------------------------------------------------
@pmv_bp.route("/api/pmv/envois", methods=["GET"])
def historique():
    filtre = {}
    if request.args.get("panneau_id"):
        filtre["panneau_id"] = request.args["panneau_id"]
    if request.args.get("event"):
        filtre["event"] = request.args["event"]
    if request.args.get("year"):
        try:
            filtre["year"] = int(request.args["year"])
        except ValueError:
            pass
    if not (_est_admin() or _coding()):
        filtre["jumeau"] = {"$ne": True}
    try:
        limite = max(1, min(200, int(request.args.get("limit", 50))))
    except ValueError:
        limite = 50
    docs = _db()[COL_ENVOIS].find(filtre, projection={"journal": 0}).sort("fin", -1).limit(limite)
    return jsonify({"ok": True, "envois": [_pub(d) for d in docs]})


@pmv_bp.route("/api/pmv/envois/<eid>", methods=["GET"])
def detail_envoi(eid):
    oid = _oid(eid)
    doc = _db()[COL_ENVOIS].find_one({"_id": oid}) if oid else None
    if not doc or (doc.get("jumeau") and not (_est_admin() or _coding())):
        return _err("envoi_inconnu", 404)
    return jsonify({"ok": True, "envoi": _pub(doc)})


# ---------------------------------------------------------------------------
# Groupes de remorques (lecture manager, ecriture admin)
# ---------------------------------------------------------------------------
def _ids_panneaux(ids):
    """-> liste d'ids (str) existants, dans l'ordre donne, sans doublon."""
    if not isinstance(ids, list):
        return []
    oids = [o for o in (_oid(i) for i in ids) if o is not None]
    existants = {str(d["_id"]) for d in _db()[COL_PANNEAUX].find({"_id": {"$in": oids}}, {"_id": 1})}
    out = []
    for i in ids:
        if i in existants and i not in out:
            out.append(i)
    return out


def _pub_groupe(g, panneaux_par_id):
    p = _pub(g)
    p["membres"] = [{"id": i, "plaque": _plaque_affichee(panneaux_par_id[i].get("plaque")),
                     "nom": panneaux_par_id[i].get("nom"), "jumeau": bool(panneaux_par_id[i].get("jumeau"))}
                    for i in g.get("panneau_ids", []) if i in panneaux_par_id]
    return p


@pmv_bp.route("/api/pmv/groupes", methods=["GET"])
def liste_groupes():
    db = _db()
    panneaux = {str(d["_id"]): d for d in db[COL_PANNEAUX].find({}, {"plaque": 1, "nom": 1, "jumeau": 1})}
    groupes = [_pub_groupe(g, panneaux) for g in db[COL_GROUPES].find({}).sort("nom", 1)]
    return jsonify({"ok": True, "groupes": groupes})


def _valider_groupe(data):
    nom = _nettoyer_texte(data.get("nom"), 60)
    if not nom:
        return None, "nom_requis"
    ids = _ids_panneaux(data.get("panneau_ids"))
    if not ids:
        return None, "groupe_vide"
    return {"nom": nom, "panneau_ids": ids}, None


@pmv_bp.route("/api/pmv/groupes", methods=["POST"])
@admin_requis
def creer_groupe():
    champs, err = _valider_groupe(request.get_json(silent=True) or {})
    if err:
        return _err(err, 422)
    db = _db()
    if db[COL_GROUPES].find_one({"nom": champs["nom"]}):
        return _err("groupe_existant", 409)
    champs.update({"created_at": _now(), "updated_at": _now(), "created_by": _utilisateur()["email"]})
    res = db[COL_GROUPES].insert_one(champs)
    _audit("groupe_creation", "ok", detail={"id": str(res.inserted_id), "nom": champs["nom"]})
    return jsonify({"ok": True, "id": str(res.inserted_id)})


@pmv_bp.route("/api/pmv/groupes/<gid>", methods=["PUT"])
@admin_requis
def modifier_groupe(gid):
    oid = _oid(gid)
    db = _db()
    if not oid or not db[COL_GROUPES].find_one({"_id": oid}):
        return _err("groupe_inconnu", 404)
    champs, err = _valider_groupe(request.get_json(silent=True) or {})
    if err:
        return _err(err, 422)
    if db[COL_GROUPES].find_one({"nom": champs["nom"], "_id": {"$ne": oid}}):
        return _err("groupe_existant", 409)
    champs["updated_at"] = _now()
    db[COL_GROUPES].update_one({"_id": oid}, {"$set": champs})
    _audit("groupe_modification", "ok", detail={"id": gid, "nom": champs["nom"]})
    return jsonify({"ok": True})


@pmv_bp.route("/api/pmv/groupes/<gid>", methods=["DELETE"])
@admin_requis
def supprimer_groupe(gid):
    oid = _oid(gid)
    db = _db()
    g = db[COL_GROUPES].find_one({"_id": oid}) if oid else None
    if not g:
        return _err("groupe_inconnu", 404)
    db[COL_GROUPES].delete_one({"_id": oid})
    annulees = db[COL_PROGRAMMATIONS].update_many(
        {"cible_type": "groupe", "cible_id": gid, "statut": "en_attente"},
        {"$set": {"statut": "annulee", "annulee_at": _now(), "motif": "groupe supprime"}}).modified_count
    _audit("groupe_suppression", "ok", detail={"id": gid, "nom": g.get("nom"), "programmations_annulees": annulees})
    return jsonify({"ok": True, "programmations_annulees": annulees})


# ---------------------------------------------------------------------------
# Envoi groupe : un job par remorque, 3 remorques traitees en parallele
# ---------------------------------------------------------------------------
PARALLELE_GROUPE = 3
_RUNS = {}
_RUNS_LOCK = threading.Lock()


def lancer_envoi_groupe(docs, params, user, ip_client, force=False, libelle=None,
                        programmation_id=None, a_la_fin=None):
    """Reserve d'abord TOUTES les remorques (verrou), puis les traite 3 par 3.
    Les remorques occupees, inactives ou affichant deja ce message sont ecartees et
    notees comme telles. Rend le run (dict) ; a_la_fin(run) est appele a la fin."""
    rid = uuid.uuid4().hex
    run = {"id": rid, "libelle": libelle, "started_at": _iso(_now()), "termine": False,
           "programmation_id": programmation_id, "entrees": []}
    reserves = []
    for doc in docs:
        pid = str(doc["_id"])
        entree = {"panneau_id": pid, "plaque": _plaque_affichee(doc.get("plaque")),
                  "nom": doc.get("nom"), "job_id": None, "ecarte": None}
        if not doc.get("actif", True):
            entree["ecarte"] = "panneau_inactif"
        elif doc.get("jumeau") and not (user.get("admin") or _coding()):
            entree["ecarte"] = "jumeau_reserve_admin"
        elif not force and _deja_affiche(pid, params["sha256"]):
            entree["ecarte"] = "deja_affiche"
        else:
            p = dict(params, groupe_run_id=rid, programmation_id=programmation_id)
            reserve = _reserver_job("envoi", doc, p)
            if reserve is None:
                entree["ecarte"] = "busy"
            else:
                entree["job_id"] = reserve[0]
                reserves.append((reserve[0], reserve[1], doc, p))
        run["entrees"].append(entree)
    with _RUNS_LOCK:
        limite = time.time() - JOB_TTL_S
        for k in [k for k, r in _RUNS.items() if r.get("_fin_epoch") and r["_fin_epoch"] < limite]:
            _RUNS.pop(k, None)
        _RUNS[rid] = run

    def coordinateur():
        try:
            with ThreadPoolExecutor(max_workers=PARALLELE_GROUPE, thread_name_prefix="pmv-grp") as ex:
                for jid, verrou, doc, p in reserves:
                    ex.submit(_executer, jid, verrou, "envoi", doc, user, ip_client, p)
        finally:
            run["termine"] = True
            run["_fin_epoch"] = time.time()
            if a_la_fin:
                try:
                    a_la_fin(_snapshot_run(rid))
                except Exception:
                    logger.exception("pmv fin de run %s", rid)

    threading.Thread(target=coordinateur, name="pmv-run-" + rid[:8], daemon=True).start()
    return run


LIBELLES_ECART = {
    "panneau_inactif": "Remorque desactivee : ecartee.",
    "jumeau_reserve_admin": "Jumeau reserve aux admins : ecarte.",
    "deja_affiche": "Affiche deja ce message : rien n'a ete renvoye (memoire flash menagee).",
    "busy": "Une operation etait deja en cours : ecartee.",
}


def _snapshot_run(rid):
    with _RUNS_LOCK:
        run = _RUNS.get(rid)
        if not run:
            return None
        out = {k: v for k, v in run.items() if not k.startswith("_")}
        out["entrees"] = [dict(e) for e in run["entrees"]]
    for e in out["entrees"]:
        if e["job_id"]:
            snap = _snapshot(e["job_id"]) or {}
            en_cours = next((x for x in snap.get("etapes", []) if x["etat"] == "en_cours"), None)
            e.update({"status": snap.get("status", "done"), "resultat": snap.get("resultat"),
                      "erreur_code": snap.get("erreur_code"), "message": snap.get("message"),
                      "progress": snap.get("progress"), "duree_s": snap.get("duree_s"),
                      "etape": en_cours["libelle"] if en_cours else None})
        else:
            e.update({"status": "ecarte", "resultat": "ecarte", "message": LIBELLES_ECART.get(e["ecarte"], e["ecarte"])})
    out["bilan"] = {
        "ok": sum(1 for e in out["entrees"] if e.get("resultat") == "ok"),
        "ko": sum(1 for e in out["entrees"] if e.get("resultat") == "ko"),
        "ecartes": sum(1 for e in out["entrees"] if e.get("resultat") == "ecarte"),
        "total": len(out["entrees"]),
    }
    return out


@pmv_bp.route("/api/pmv/groupes/<gid>/envoi", methods=["POST"])
def envoyer_groupe(gid):
    oid = _oid(gid)
    db = _db()
    g = db[COL_GROUPES].find_one({"_id": oid}) if oid else None
    if not g:
        return _err("groupe_inconnu", 404)
    data = request.get_json(silent=True) or {}
    user = _utilisateur()
    params, code, detail = preparer_envoi(data, user)
    if code:
        return _err(code, 403 if code == "role_admin_requis" else 422, detail=detail)
    if params.get("sans_sauvegarde"):
        return _err("sans_sauvegarde_unitaire_seulement", 422)
    docs = [db[COL_PANNEAUX].find_one({"_id": ObjectId(i)}) for i in g.get("panneau_ids", [])]
    docs = [d for d in docs if d]
    if not docs:
        return _err("groupe_vide", 422)
    run = lancer_envoi_groupe(docs, params, user, _ip_client(), force=bool(data.get("force")),
                              libelle="Groupe " + g["nom"])
    _audit("envoi_groupe", "lance", detail={"groupe": g["nom"], "run": run["id"], "remorques": len(docs)})
    return jsonify({"ok": True, "run": _snapshot_run(run["id"])})


@pmv_bp.route("/api/pmv/runs/<rid>", methods=["GET"])
def suivre_run(rid):
    snap = _snapshot_run(rid)
    if not snap:
        return _err("run_inconnu", 404)
    return jsonify({"ok": True, "run": snap})


# ---------------------------------------------------------------------------
# Programmation horaire + planificateur
# ---------------------------------------------------------------------------
ECART_FLASH_MIN = 15           # au plus une programmation par remorque par tranche de 15 min
RETARD_MAX_MIN = 10            # au-dela, une programmation manquee n'est PAS envoyee
HORIZON_JOURS = 60
TICK_S = 30


def _cibles_programmation(cible_type, cible_id):
    """-> (nom de la cible, [panneau_ids]) ou (None, None)."""
    db = _db()
    oid = _oid(cible_id)
    if not oid:
        return None, None
    if cible_type == "panneau":
        d = db[COL_PANNEAUX].find_one({"_id": oid})
        return (_plaque_affichee(d["plaque"]) + " - " + (d.get("nom") or ""), [str(d["_id"])]) if d else (None, None)
    if cible_type == "groupe":
        g = db[COL_GROUPES].find_one({"_id": oid})
        return ("Groupe " + g["nom"], list(g.get("panneau_ids", []))) if g else (None, None)
    return None, None


def _parse_heure(texte):
    try:
        dt = datetime.fromisoformat(str(texte).strip().replace("Z", ""))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=None, second=0, microsecond=0)


@pmv_bp.route("/api/pmv/programmations", methods=["GET"])
def liste_programmations():
    db = _db()
    maintenant = _now()
    a_venir = list(db[COL_PROGRAMMATIONS].find({"statut": {"$in": ["en_attente", "en_cours"]}}).sort("at", 1))
    passees = list(db[COL_PROGRAMMATIONS].find({"statut": {"$nin": ["en_attente", "en_cours"]}}).sort("at", -1).limit(50))
    return jsonify({"ok": True, "a_venir": [_pub(d) for d in a_venir], "passees": [_pub(d) for d in passees],
                    "maintenant": _iso(maintenant), "ecart_min": ECART_FLASH_MIN})


@pmv_bp.route("/api/pmv/programmations", methods=["POST"])
def creer_programmation():
    data = request.get_json(silent=True) or {}
    user = _utilisateur()
    at = _parse_heure(data.get("at"))
    maintenant = _now()
    if at is None:
        return _err("heure_invalide", 422)
    if (at - maintenant).total_seconds() < 60:
        return _err("heure_passee", 422, message="L'heure doit etre dans le futur (au moins une minute).")
    if (at - maintenant).days > HORIZON_JOURS:
        return _err("heure_trop_lointaine", 422, message="Programmation limitee a %d jours." % HORIZON_JOURS)
    cible_type = data.get("cible_type")
    cible_nom, ids = _cibles_programmation(cible_type, data.get("cible_id"))
    if not ids:
        return _err("cible_inconnue", 422)
    db = _db()
    jumeaux = db[COL_PANNEAUX].count_documents({"_id": {"$in": [ObjectId(i) for i in ids]}, "jumeau": True})
    if jumeaux and not (user["admin"] or _coding()):
        return _err("jumeau_reserve_admin", 403)
    oid = _oid(data.get("message_id"))
    msg = db[COL_MESSAGES].find_one({"_id": oid}, {"nom": 1, "png_b64": 1}) if oid else None
    if not msg:
        return _err("message_inconnu", 422)
    # Regle flash : pas deux ecritures programmees sur la meme remorque a moins de 15 min
    fenetre = timedelta(minutes=ECART_FLASH_MIN)
    for autre in db[COL_PROGRAMMATIONS].find({"statut": "en_attente", "at": {"$gt": at - fenetre, "$lt": at + fenetre}}):
        _, autres_ids = _cibles_programmation(autre["cible_type"], autre["cible_id"])
        commun = set(autres_ids or []) & set(ids)
        if commun:
            return _err("programmation_trop_proche", 409, message=(
                "Une autre programmation touche deja %d de ces remorques a %s : il faut au moins %d minutes "
                "d'ecart (la memoire du panneau s'use a chaque ecriture)." % (
                    len(commun), autre["at"].strftime("%d/%m %H:%M"), ECART_FLASH_MIN)))
    event, year = _contexte_evenement(data)
    doc = {"cible_type": cible_type, "cible_id": data.get("cible_id"), "cible_nom": cible_nom,
           "nb_remorques": len(ids), "message_id": str(msg["_id"]), "message_nom": msg.get("nom"),
           "png_b64": msg.get("png_b64"), "at": at, "statut": "en_attente", "event": event, "year": year,
           "cree_par": user["email"], "cree_par_nom": user["nom"], "cree_par_admin": user["admin"],
           "created_at": maintenant}
    res = db[COL_PROGRAMMATIONS].insert_one(doc)
    _audit("programmation_creation", "ok", detail={"id": str(res.inserted_id), "cible": cible_nom,
                                                   "at": _iso(at), "message": msg.get("nom")})
    return jsonify({"ok": True, "id": str(res.inserted_id)})


@pmv_bp.route("/api/pmv/programmations/<prid>", methods=["DELETE"])
def annuler_programmation(prid):
    oid = _oid(prid)
    db = _db()
    res = db[COL_PROGRAMMATIONS].find_one_and_update(
        {"_id": oid, "statut": "en_attente"},
        {"$set": {"statut": "annulee", "annulee_at": _now(), "annulee_par": _utilisateur()["email"]}}) if oid else None
    if not res:
        return _err("programmation_non_annulable", 409,
                    message="Programmation introuvable ou deja lancee.")
    _audit("programmation_annulation", "ok", detail={"id": prid, "cible": res.get("cible_nom")})
    return jsonify({"ok": True})


def _executer_programmation(prog):
    """Appele par le planificateur, hors requete HTTP."""
    db = _db()
    user = {"email": prog.get("cree_par"), "nom": (prog.get("cree_par_nom") or "") + " (programme)",
            "admin": bool(prog.get("cree_par_admin"))}
    _, ids = _cibles_programmation(prog["cible_type"], prog["cible_id"])
    docs = [db[COL_PANNEAUX].find_one({"_id": ObjectId(i)}) for i in (ids or [])]
    docs = [d for d in docs if d]
    params, code, _detail = preparer_envoi({"message_id": prog["message_id"], "event": prog.get("event"),
                                            "year": prog.get("year")}, user)
    if code or not docs:
        db[COL_PROGRAMMATIONS].update_one({"_id": prog["_id"]}, {"$set": {
            "statut": "echec", "terminee_at": _now(),
            "motif": "message supprime" if code == "message_inconnu" else ("cible vide" if not docs else code)}})
        _audit("programmation_execution", "echec", user=user, ip="planificateur",
               detail={"id": str(prog["_id"]), "code": code or "cible_vide"})
        return

    def a_la_fin(run):
        bilan = run["bilan"] if run else {}
        statut = "terminee" if bilan.get("ko", 0) == 0 else ("echec" if bilan.get("ok", 0) == 0 else "partielle")
        db[COL_PROGRAMMATIONS].update_one({"_id": prog["_id"]}, {"$set": {
            "statut": statut, "terminee_at": _now(), "bilan": bilan,
            "resultats": [{"plaque": e["plaque"], "resultat": e.get("resultat"), "message": e.get("message")}
                          for e in (run or {}).get("entrees", [])]}})
        _audit("programmation_execution", statut, user=user, ip="planificateur",
               detail={"id": str(prog["_id"]), "bilan": bilan})

    run = lancer_envoi_groupe(docs, params, user, "planificateur", force=False,
                              libelle=prog.get("cible_nom"), programmation_id=str(prog["_id"]),
                              a_la_fin=a_la_fin)
    db[COL_PROGRAMMATIONS].update_one({"_id": prog["_id"]}, {"$set": {"run_id": run["id"]}})


def _tick_planificateur():
    db = _db()
    maintenant = _now()
    for prog in list(db[COL_PROGRAMMATIONS].find({"statut": "en_attente", "at": {"$lte": maintenant}}).sort("at", 1)):
        # Reservation atomique : un seul lanceur, meme si deux ticks se chevauchaient
        reserve = db[COL_PROGRAMMATIONS].find_one_and_update(
            {"_id": prog["_id"], "statut": "en_attente"},
            {"$set": {"statut": "en_cours", "lancee_at": maintenant}})
        if not reserve:
            continue
        if maintenant - prog["at"] > timedelta(minutes=RETARD_MAX_MIN):
            # Serveur arrete a l'heure prevue : afficher en retard un message date serait pire que rien
            db[COL_PROGRAMMATIONS].update_one({"_id": prog["_id"]}, {"$set": {
                "statut": "manquee", "terminee_at": maintenant,
                "motif": "heure depassee de plus de %d min (serveur indisponible ?) : non envoyee" % RETARD_MAX_MIN}})
            _audit("programmation_execution", "manquee", ip="planificateur",
                   user={"email": prog.get("cree_par"), "nom": prog.get("cree_par_nom")},
                   detail={"id": str(prog["_id"])})
            continue
        try:
            _executer_programmation(prog)
        except Exception:
            logger.exception("pmv programmation %s", prog["_id"])
            db[COL_PROGRAMMATIONS].update_one({"_id": prog["_id"]}, {"$set": {
                "statut": "echec", "terminee_at": _now(), "motif": "erreur interne"}})


_planif_lance = False
_planif_lock = threading.Lock()


def start_scheduler():
    """Demarre la boucle du planificateur PMV une seule fois (thread daemon).
    Appele depuis le bloc __main__ d'app.py, a cote de alfred.start_scheduler()."""
    global _planif_lance
    with _planif_lock:
        if _planif_lance:
            return
        _planif_lance = True

    def boucle():
        logger.info("pmv scheduler: demarre (tick %ds)", TICK_S)
        while True:
            try:
                _tick_planificateur()
            except Exception:
                logger.exception("pmv scheduler: tick en erreur")
            time.sleep(TICK_S)

    threading.Thread(target=boucle, name="pmv-scheduler", daemon=True).start()


# ---------------------------------------------------------------------------
# Couche PMV de la carte d'accueil (lecture seule, sans resolution DNS)
# ---------------------------------------------------------------------------
@pmv_bp.route("/api/pmv/carte", methods=["GET"])
def carte_accueil():
    db = _db()
    out = []
    for d in db[COL_PANNEAUX].find({"actif": {"$ne": False}, "jumeau": {"$ne": True},
                                     "lat": {"$ne": None}, "lng": {"$ne": None}}):
        pid = str(d["_id"])
        dernier = _dernier_envoi(db, pid)
        dernier_ok = _dernier_envoi(db, pid, seulement_ok=True)
        res = d.get("derniere_resolution") or {}
        out.append({
            "id": pid, "plaque": _plaque_affichee(d.get("plaque")), "nom": d.get("nom"),
            "localisation": d.get("localisation"), "lat": d.get("lat"), "lng": d.get("lng"),
            "etat": res.get("etat") or "inconnu", "etat_ts": res.get("ts"),
            "affiche_deduit": _affiche_deduit(d, dernier_ok),
            "dernier_envoi": {"fin": _iso(dernier.get("fin")), "resultat": dernier.get("resultat"),
                              "user_nom": dernier.get("user_nom")} if dernier else None,
        })
    return jsonify({"ok": True, "panneaux": out})
