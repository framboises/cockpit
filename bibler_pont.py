"""Client serveur a serveur Cockpit -> Bibler (pont Bibler).

Authentification : jeton de service HS256 a courte duree (60 s), claims
iss=cockpit, aud=bibler, sub=service:cockpit, iat, exp, jti, capacites.
Secret partage : COCKPIT_PONT_SECRET_BIBLER (32 caracteres min), URL de
Bibler : COCKPIT_PONT_URL_BIBLER. Les deux sont lues a chaque appel.

Delais : connexion 3 s, reponse 8 s ; reponse 5 Mo max ; redirections non
suivies. Ni le secret ni le jeton ne sont jamais journalises.
"""
import logging
import re
import threading
import time
import uuid

import jwt
import requests

logger = logging.getLogger(__name__)

SECRET_MIN = 32
JETON_TTL_S = 60
TIMEOUT = (3, 8)
TAILLE_MAX = 5 * 1024 * 1024
_CHEMIN_RE = re.compile(r"^/api/v1/[A-Za-z0-9._~\-/%]*$")

_CACHE = {}
_CACHE_LOCK = threading.Lock()
_SESSION = None


class BiblerNonConfigure(Exception):
    """URL ou secret du pont absents ou invalides."""


class BiblerIndisponible(Exception):
    """Bibler injoignable, trop lent, en erreur 5xx ou reponse trop grosse."""


class BiblerErreur(Exception):
    """Reponse 4xx de Bibler, relayee telle quelle."""

    def __init__(self, statut, corps):
        super().__init__("bibler %s" % statut)
        self.statut = statut
        self.corps = corps


def _url_base():
    import os
    return (os.environ.get("COCKPIT_PONT_URL_BIBLER") or "").strip().rstrip("/")


def _secret():
    import os
    return (os.environ.get("COCKPIT_PONT_SECRET_BIBLER") or "").strip()


def configure():
    """True si l'URL et un secret de 32 caracteres au moins sont presents."""
    return bool(_url_base()) and len(_secret()) >= SECRET_MIN


def _session():
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
    return _SESSION


def jeton(capacites):
    """Jeton de service HS256 portant les capacites demandees."""
    secret = _secret()
    if len(secret) < SECRET_MIN:
        raise BiblerNonConfigure("secret du pont absent")
    iat = int(time.time())
    claims = {
        "iss": "cockpit",
        "aud": "bibler",
        "sub": "service:cockpit",
        "iat": iat,
        "exp": iat + JETON_TTL_S,
        "jti": uuid.uuid4().hex,
        "capacites": list(capacites),
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def _verifier_chemin(chemin):
    if (not isinstance(chemin, str) or not _CHEMIN_RE.match(chemin)
            or ".." in chemin or "//" in chemin):
        raise ValueError("chemin Bibler invalide")


def _appel(methode, chemin, capacites, params=None, corps=None, idempotence=None):
    """Un appel HTTP ; retourne la reponse (statut < 400) ou leve."""
    if not configure():
        raise BiblerNonConfigure("pont Bibler non configure")
    _verifier_chemin(chemin)
    entetes = {
        "Authorization": "Bearer " + jeton(capacites),
        "Accept": "application/json",
    }
    if idempotence:
        entetes["Idempotency-Key"] = str(idempotence)
    kw = {"headers": entetes, "timeout": TIMEOUT, "allow_redirects": False}
    if params:
        kw["params"] = params
    if corps is not None:
        kw["json"] = corps
    try:
        rep = _session().request(methode, _url_base() + chemin, **kw)
    except requests.RequestException as e:
        logger.warning("Bibler injoignable (%s %s) : %s", methode, chemin, type(e).__name__)
        raise BiblerIndisponible("Bibler injoignable") from e
    statut = rep.status_code
    if statut >= 500 or 300 <= statut < 400:
        logger.warning("Bibler a repondu %s (%s %s)", statut, methode, chemin)
        raise BiblerIndisponible("Bibler a repondu %s" % statut)
    if len(rep.content) > TAILLE_MAX:
        raise BiblerIndisponible("reponse Bibler trop volumineuse")
    if statut >= 400:
        try:
            donnees = rep.json()
        except ValueError:
            donnees = {}
        if not isinstance(donnees, dict):
            donnees = {}
        raise BiblerErreur(statut, donnees)
    return rep


def _json(rep):
    try:
        donnees = rep.json()
    except ValueError as e:
        raise BiblerIndisponible("reponse Bibler illisible") from e
    return donnees


def _cache_get(cle):
    with _CACHE_LOCK:
        v = _CACHE.get(cle)
        if v and v[0] > time.time():
            return v[1]
        _CACHE.pop(cle, None)
    return None


def _cache_set(cle, valeur, ttl):
    with _CACHE_LOCK:
        if len(_CACHE) > 200:
            _CACHE.clear()
        _CACHE[cle] = (time.time() + ttl, valeur)


def lire(chemin, params=None, capacites=("postes:lire",), ttl=0):
    """GET JSON ; ttl > 0 active un cache memoire par (chemin, parametres)."""
    cle = None
    if ttl > 0:
        cle = (chemin, tuple(sorted((params or {}).items(), key=lambda kv: str(kv[0]))), tuple(capacites))
        hit = _cache_get(cle)
        if hit is not None:
            return hit
    donnees = _json(_appel("GET", chemin, capacites, params=params))
    if cle is not None:
        _cache_set(cle, donnees, ttl)
    return donnees


def lire_binaire(chemin, ttl=86400):
    """GET binaire (image) ; retourne (octets, type de contenu), mis en cache."""
    cle = ("bin", chemin)
    hit = _cache_get(cle) if ttl > 0 else None
    if hit is not None:
        return hit
    rep = _appel("GET", chemin, ("postes:lire",))
    resultat = (rep.content, rep.headers.get("Content-Type", "application/octet-stream"))
    if ttl > 0:
        _cache_set(cle, resultat, ttl)
    return resultat


def envoyer(methode, chemin, corps, capacites=("consignes:ecrire",), idempotence=None):
    """Ecriture JSON ; retourne (corps de reponse, statut)."""
    rep = _appel(methode.upper(), chemin, capacites, corps=corps, idempotence=idempotence)
    return _json(rep), rep.status_code
