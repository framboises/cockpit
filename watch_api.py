"""Blueprint de l'API montre.

Lecture seule, authentifiee par jeton Bearer revocable. Aucun acces direct a
Mongo depuis l'exterieur : cette couche est la seule surface exposee.
"""

import hashlib
import logging
import secrets
import time
from datetime import datetime, timezone
from functools import wraps

from bson.errors import InvalidId
from bson.objectid import ObjectId
from flask import Blueprint, jsonify, request

import watch_guidage
import watch_pages
import watch_peaks
import watch_state

logger = logging.getLogger(__name__)

watch_bp = Blueprint("watch", __name__, url_prefix="/api/v1/watch")

# Cache du payload. Meme principe que traffic.py : la charge Mongo est bornee
# quel que soit le rythme de polling, sans process supplementaire.
CACHE_TTL_S = 20

# Fenetre glissante par jeton. La montre consomme 5 requetes (app a 1 min) plus
# 1 (background) : la marge absorbe les reprises reseau sans jamais couvrir une
# boucle folle.
RATE_LIMIT_WINDOW_S = 300
RATE_LIMIT_MAX = 60

# Bride d'ecriture de la telemetrie : sans elle, une montre a 1 min de polling
# produirait 1 440 ecritures par jour pour un seul champ d'horodatage.
LAST_USED_THROTTLE_S = 60

# Le pic d'une edition close ne change plus jamais et watch_peaks a deja son
# cache persistant : cette fenetre-ci ne sert qu'a eviter de reparcourir la
# liste des editions et de relire Mongo a chaque ouverture du menu.
EDITIONS_CACHE_TTL_S = 3600

_cache = {"at": 0.0, "payload": None}
_editions_cache = {"at": 0.0, "payload": None}
_rate_log = {}
_indexes_ready = False


def _db():
    """Base de l'environnement courant. Jamais de nom code en dur."""
    from app import db
    return db


def reset_cache():
    _cache["at"] = 0.0
    _cache["payload"] = None
    _editions_cache["at"] = 0.0
    _editions_cache["payload"] = None


def reset_rate_limit():
    _rate_log.clear()


def _ensure_indexes(db):
    global _indexes_ready
    if _indexes_ready:
        return
    db["watch_tokens"].create_index("token_sha256", unique=True)
    _indexes_ready = True


def _client_ip():
    return request.headers.get(
        "X-Forwarded-For", request.remote_addr or "0.0.0.0"
    ).split(",")[0].strip()


def _maintenant():
    """Naif UTC, coherent avec ce que pymongo rend en lecture sur un client
    non tz_aware (celui de app.py). Comparer ce naif a un datetime conscient
    du fuseau leverait un TypeError des la deuxieme requete dans la fenetre
    de throttle : c'est exactement le bug trouve en verification bout en
    bout. Meme forme que meteo.py (datetime.now(timezone.utc).replace(
    tzinfo=None)), pas datetime.utcnow() qui est deprecie."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(db, label, created_by):
    """Emet un jeton. Le clair est renvoye une seule fois et jamais stocke."""
    _ensure_indexes(db)
    token = secrets.token_urlsafe(32)
    db["watch_tokens"].insert_one({
        "label": label,
        "token_sha256": hash_token(token),
        "created_at": _maintenant(),
        "created_by": created_by,
        "revoked": False,
        "revoked_at": None,
        "last_used_at": None,
        "last_ip": None,
        "use_count": 0,
    })
    return token


def verify_token(db, token):
    """Lookup indexe sur le hash : aucune comparaison de secrets."""
    _ensure_indexes(db)
    if not token:
        return None
    doc = db["watch_tokens"].find_one({"token_sha256": hash_token(token)})
    if not doc or doc.get("revoked"):
        return None
    return doc


def revoke_token(db, token_id):
    from bson import ObjectId
    try:
        oid = ObjectId(token_id)
    except Exception:
        return False
    res = db["watch_tokens"].update_one(
        {"_id": oid},
        {"$set": {"revoked": True,
                  "revoked_at": _maintenant()}},
    )
    return bool(getattr(res, "matched_count", 1))


def _touch_token(db, doc):
    """Telemetrie d'usage, bridee a une ecriture par minute."""
    dernier = doc.get("last_used_at")
    maintenant = _maintenant()
    if dernier is not None:
        if (maintenant - dernier).total_seconds() < LAST_USED_THROTTLE_S:
            return
    db["watch_tokens"].update_one(
        {"_id": doc["_id"]},
        {"$set": {"last_used_at": maintenant, "last_ip": _client_ip()},
         "$inc": {"use_count": 1}},
    )


def _rate_limited(cle):
    maintenant = time.time()
    debut = maintenant - RATE_LIMIT_WINDOW_S
    hist = [t for t in _rate_log.get(cle, []) if t > debut]
    if len(hist) >= RATE_LIMIT_MAX:
        _rate_log[cle] = hist
        return True
    hist.append(maintenant)
    _rate_log[cle] = hist
    if len(_rate_log) > 1000:
        mortes = [k for k, v in _rate_log.items()
                  if not any(t > debut for t in v)]
        for k in mortes:
            _rate_log.pop(k, None)
    return False


def bearer_required(f):
    """Auth par jeton. Repond 401 en JSON, jamais 302.

    @role_required redirige vers le portail, ce qui casserait un appel machine.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        entete = request.headers.get("Authorization", "")
        if not entete.startswith("Bearer "):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        token = entete[7:].strip()

        db = _db()
        doc = verify_token(db, token)
        if doc is None:
            logger.info("Jeton montre refuse depuis %s", _client_ip())
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        if _rate_limited(doc["token_sha256"]):
            reponse = jsonify({"ok": False, "error": "rate_limited"})
            reponse.headers["Retry-After"] = str(RATE_LIMIT_WINDOW_S)
            return reponse, 429

        _touch_token(db, doc)
        request.watch_token = doc
        return f(*args, **kwargs)

    return wrapper


EVENT_MODES = ("auto", "pinned")


def validate_config(payload):
    """Valide une configuration montre. Retourne (ok, code_erreur)."""
    mode = payload.get("event_mode", "auto")
    if mode not in EVENT_MODES:
        return False, "event_mode_invalide"

    if mode == "pinned":
        if not payload.get("event"):
            return False, "event_requis"
        try:
            int(payload.get("year"))
        except (TypeError, ValueError):
            return False, "year_requis"

    for regle in payload.get("alerts") or []:
        if not regle.get("slug"):
            return False, "slug_requis"
        try:
            niveau = int(regle.get("level"))
        except (TypeError, ValueError):
            return False, "level_invalide"
        if niveau < 1 or niveau > 3:
            return False, "level_invalide"

    seuils = payload.get("wbgt_levels")
    if seuils is not None:
        if len(seuils) != 3:
            return False, "wbgt_levels_invalides"
        try:
            valeurs = [float(s) for s in seuils]
        except (TypeError, ValueError):
            return False, "wbgt_levels_invalides"
        if valeurs != sorted(valeurs) or len(set(valeurs)) != 3:
            return False, "wbgt_levels_invalides"

    return True, None


def _admin_guard():
    """Renvoie une reponse d'erreur si l'appelant n'est pas admin, sinon None."""
    import jwt as pyjwt
    from app import (CODING, JWT_SECRET, JWT_ALGORITHM, APP_KEY,
                     SUPER_ADMIN_ROLE)

    if CODING:
        return None

    token = request.cookies.get("access_token")
    if not token:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except pyjwt.InvalidTokenError:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    # Meme regle que role_required, qui garde la page, et que
    # field.py:admin_required : un super_admin global vaut admin sur chaque
    # application, sans role cockpit explicite. Sans ca, la page s'ouvre mais
    # tous ses appels API echouent en 403.
    is_super_admin = SUPER_ADMIN_ROLE in (payload.get("global_roles") or [])
    role = (payload.get("roles_by_app") or {}).get(APP_KEY)
    if not is_super_admin and role != "admin":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return None


def _admin_identity():
    from app import CODING
    if CODING:
        return "dev"
    import jwt as pyjwt
    from app import JWT_SECRET, JWT_ALGORITHM
    token = request.cookies.get("access_token") or ""
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except pyjwt.InvalidTokenError:
        return "inconnu"
    return payload.get("email") or "inconnu"


@watch_bp.route("/admin/tokens", methods=["GET"])
def admin_list_tokens():
    refus = _admin_guard()
    if refus:
        return refus
    db = _db()
    docs = db["watch_tokens"].find({}, {"token_sha256": 0})
    sortie = []
    for doc in docs:
        doc["_id"] = str(doc["_id"])
        for cle in ("created_at", "revoked_at", "last_used_at"):
            valeur = doc.get(cle)
            if hasattr(valeur, "isoformat"):
                doc[cle] = valeur.isoformat()
        sortie.append(doc)
    return jsonify({"ok": True, "tokens": sortie})


@watch_bp.route("/admin/tokens", methods=["POST"])
def admin_create_token():
    refus = _admin_guard()
    if refus:
        return refus
    donnees = request.get_json(silent=True) or {}
    label = (donnees.get("label") or "").strip()
    if not label:
        return jsonify({"ok": False, "error": "label_requis"}), 400
    token = issue_token(_db(), label, _admin_identity())
    # Le clair n'est renvoye qu'ici, une seule fois.
    return jsonify({"ok": True, "token": token})


@watch_bp.route("/admin/tokens/<token_id>/revoke", methods=["POST"])
def admin_revoke_token(token_id):
    refus = _admin_guard()
    if refus:
        return refus
    if not revoke_token(_db(), token_id):
        return jsonify({"ok": False, "error": "introuvable"}), 404
    return jsonify({"ok": True})


@watch_bp.route("/admin/config", methods=["GET"])
def admin_get_config():
    refus = _admin_guard()
    if refus:
        return refus
    db = _db()
    config = watch_state.read_config(db)
    definitions = list(db["cockpit_alert_definitions"].find(
        {}, {"_id": 0, "slug": 1, "name": 1, "enabled": 1}))
    evenements = list(db["evenement"].find({}, {"_id": 0, "nom": 1, "short": 1}))
    return jsonify({"ok": True, "config": config,
                    "definitions": definitions, "evenements": evenements})


@watch_bp.route("/admin/config", methods=["PUT"])
def admin_put_config():
    refus = _admin_guard()
    if refus:
        return refus
    donnees = request.get_json(silent=True) or {}
    ok, erreur = validate_config(donnees)
    if not ok:
        return jsonify({"ok": False, "error": erreur}), 400

    maj = {
        "event_mode": donnees.get("event_mode", "auto"),
        "event": donnees.get("event"),
        "year": int(donnees["year"]) if donnees.get("year") else None,
        "alerts": donnees.get("alerts") or [],
        "wbgt_levels": donnees.get("wbgt_levels")
                       or list(watch_state.WBGT_DEFAULT_LEVELS),
        "updated_at": datetime.now(timezone.utc),
        "updated_by": _admin_identity(),
    }
    _db()["watch_config"].update_one({"_id": "watch"}, {"$set": maj},
                                     upsert=True)
    reset_cache()
    return jsonify({"ok": True})


@watch_bp.route("/state", methods=["GET"])
@bearer_required
def state():
    """Etat commun a toutes les montres, plus le guidage propre a celle-ci.

    ⚠️ LE CACHE EST PARTAGE ENTRE TOUTES LES MONTRES. Le point de guidage,
    lui, est adresse a UNE montre : il ne peut donc pas entrer dans le
    payload mis en cache, sinon la premiere montre servie imposerait son
    point a toutes les autres pendant CACHE_TTL_S. Il est ajoute sur une
    COPIE, apres coup, a partir du jeton porteur de la requete -- et le
    payload en cache reste rigoureusement celui de tout le monde.

    La copie est superficielle (`dict(...)`) : on n'ajoute que des cles de
    premier niveau, les blocs imbriques ne sont jamais modifies.
    """
    maintenant = time.time()
    if _cache["payload"] is not None and maintenant - _cache["at"] < CACHE_TTL_S:
        payload = _cache["payload"]
    else:
        payload = watch_state.build_state(
            _db(), datetime.now(), datetime.now(timezone.utc),
            peaks=watch_peaks, pages=watch_pages)
        _cache["payload"] = payload
        _cache["at"] = maintenant

    return jsonify(_avec_guidage(payload, request.watch_token))


def _avec_guidage(payload, jeton):
    """Copie du payload, enrichie du guidage de CETTE montre.

    Deux champs, volontairement separes :

      - `gd` : le point complet (coordonnees, libelle). Il ne sert qu'a la
        page Guidage, donc il voyage dans les blocs de pages cote montre,
        que la glance et le service de fond ne deserialisent jamais.
      - `gs` : le seul compteur de sequence, dans le NOYAU du payload. C'est
        lui qui declenche la vibration a l'arrivee d'un point, et le service
        de fond ne lit que le noyau -- sans ce scalaire, un point envoye
        pendant que l'app est fermee n'aurait fait vibrer personne.

    Ne leve jamais : un guidage illisible laisse le reste du payload intact,
    meme regle que les quatre blocs de watch_pages.
    """
    sortie = dict(payload or {})
    bloc = None
    try:
        bloc = watch_guidage.read_point(_db(), (jeton or {}).get("_id"))
    except Exception:
        logger.exception("Guidage illisible, payload servi sans")
    sortie["gd"] = bloc
    sortie["gs"] = bloc["s"] if bloc else None
    return sortie


# --- Guidage : routes d'administration --------------------------------
#
# Envoyer un point sur la montre de quelqu'un est une DIRECTIVE, pas une
# consultation : meme garde admin que la gestion des jetons, et on trace qui
# a envoye quoi (`sent_by`).


@watch_bp.route("/admin/guidage", methods=["GET"])
def admin_list_guidage():
    refus = _admin_guard()
    if refus:
        return refus
    return jsonify({"ok": True, "guidage": watch_guidage.list_points(_db())})


@watch_bp.route("/admin/guidage", methods=["POST"])
def admin_set_guidage():
    refus = _admin_guard()
    if refus:
        return refus
    corps = request.get_json(silent=True) or {}
    token_id = corps.get("token_id")
    if not token_id:
        return jsonify({"ok": False, "error": "montre_absente"}), 400

    db = _db()
    try:
        cible = ObjectId(str(token_id))
    except (InvalidId, TypeError):
        return jsonify({"ok": False, "error": "montre_inconnue"}), 400
    # La montre doit exister ET ne pas etre revoquee : ecrire un point pour
    # un jeton revoque le laisserait en base sans que rien ne le lise
    # jamais, et l'operateur croirait avoir envoye quelque chose.
    #
    # Le drapeau lu est `revoked`, exactement celui que verify_token teste --
    # PAS `revoked_at`, qui l'accompagne mais qu'aucune autre garde de ce
    # fichier ne consulte. Deux gardes qui ne lisent pas le meme champ
    # finissent toujours par diverger.
    doc = db["watch_tokens"].find_one({"_id": cible})
    if doc is None or doc.get("revoked"):
        return jsonify({"ok": False, "error": "montre_inconnue"}), 404

    try:
        point = watch_guidage.set_point(
            db, cible, corps.get("lat"), corps.get("lon"),
            corps.get("label"), sent_by=_admin_identity())
    except ValueError as erreur:
        return jsonify({"ok": False, "error": str(erreur)}), 400

    logger.info("Guidage envoye vers la montre %s par %s : %s",
                token_id, _admin_identity(), point.get("label"))
    return jsonify({"ok": True, "seq": point.get("seq"),
                    "label": point.get("label")})


@watch_bp.route("/admin/guidage/<token_id>", methods=["DELETE"])
def admin_clear_guidage(token_id):
    refus = _admin_guard()
    if refus:
        return refus
    try:
        cible = ObjectId(str(token_id))
    except (InvalidId, TypeError):
        return jsonify({"ok": False, "error": "montre_inconnue"}), 400
    efface = watch_guidage.clear_point(_db(), cible)
    return jsonify({"ok": True, "efface": efface})


@watch_bp.route("/editions", methods=["GET"])
@bearer_required
def editions():
    """Editions consultables avec leur pic, en une seule requete.

    Une requete donne la liste ET tous les pics : la montre n'a pas a demander
    edition par edition, et le plafond de 60 requetes / 300 s n'est jamais
    menace par l'ouverture du menu.

    Les editions sans pic exploitable sont OMISES, pas renvoyees avec un pic
    nul. Une ligne vide dans une liste se lit comme une edition sans public,
    alors qu'elle ne dit que l'absence de mesure -- et la montre n'a pas la
    place d'expliquer la nuance.
    """
    maintenant = time.time()
    if (_editions_cache["payload"] is not None
            and maintenant - _editions_cache["at"] < EDITIONS_CACHE_TTL_S):
        return jsonify(_editions_cache["payload"])

    db = _db()
    now_utc = datetime.now(timezone.utc)
    liste = []
    for edition in watch_peaks.list_editions(db, now_utc=now_utc):
        pic, instant = watch_peaks.cached_peak(
            db, edition["event"], edition["year"], now_utc=now_utc)
        if pic is None:
            continue
        liste.append({
            "n": edition["label"],
            "ev": edition["event"],
            "y": edition["year"],
            "pk": pic,
            "pkt": int(instant.timestamp()) if instant else None,
        })

    payload = {"ok": True, "ed": liste}
    _editions_cache["payload"] = payload
    _editions_cache["at"] = maintenant
    return jsonify(payload)
