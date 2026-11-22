"""Acces du candidat au test PC Organisation (spec 6, points 1 et 2).

- Identification par e-mail (normalise comme a l'import) + 4 derniers chiffres
  du mobile, message d'erreur generique unique.
- Limitation des essais : 5 echecs sur 15 min pour l'e-mail OU pour l'IP
  (`request.remote_addr`, jamais X-Forwarded-For) -> blocage temporaire.
  Chaque echec est un document `essais_connexion` {cle, t} (index TTL 15 min).
- Jeton de session : JWT HS256 signe par FORMATION_JWT_SECRET, `aud=formation`,
  1 h, portant le candidat (`sub`) et le `sid` de la session active.
- Fenetre d'ouverture du test, lue dans les reglages.
"""
import hmac
import os
import re
from datetime import timedelta

import jwt

from formation import store
from formation.normalise import normaliser_email

MESSAGE_GENERIQUE = ("Ces informations ne correspondent à aucune candidature. "
                     "Vérifiez l'adresse utilisée dans le questionnaire.")
MESSAGE_BLOQUE = MESSAGE_GENERIQUE + " Réessayez dans 15 minutes."

SEUIL_ECHECS = 5
FENETRE_ECHECS = timedelta(minutes=15)
DUREE_JETON = timedelta(hours=1)
AUDIENCE = "formation"
COOKIE = "formation_session"
COOKIE_PATH = "/test-pc-organisation"
_SECRET_DEV = "dev-formation-secret-ne-jamais-utiliser-en-production"


def est_dev():
    return os.getenv("TITAN_ENV", "dev").strip().lower() in ("dev", "development", "")


def _secret():
    s = os.getenv("FORMATION_JWT_SECRET", "").strip()
    if s:
        return s
    if not est_dev():
        raise RuntimeError("FORMATION_JWT_SECRET absent hors dev")
    return _SECRET_DEV


def _cles(email, ip):
    e = normaliser_email(email) or re.sub(r"\s+", "", str(email or "")).lower()[:200]
    return ["email:" + e, "ip:" + str(ip or "")]


def est_bloque(db, email, ip, now):
    depuis = now - FENETRE_ECHECS
    col = db["essais_connexion"]
    return any(col.count_documents({"cle": c, "t": {"$gt": depuis}}, limit=SEUIL_ECHECS)
               >= SEUIL_ECHECS for c in _cles(email, ip))


def _echec(db, email, ip, now):
    db["essais_connexion"].insert_many(
        [{"campagne": store.CAMPAGNE, "cle": c, "t": now} for c in _cles(email, ip)])


def identifier(db, email, tel4, ip, now):
    """Candidat dont la paire (e-mail, 4 chiffres) correspond, ou None (paire
    inconnue ou acces bloque). Chaque echec est enregistre pour l'e-mail et l'IP."""
    if est_bloque(db, email, ip, now):
        return None
    e = normaliser_email(email) if isinstance(email, str) else None
    t4 = tel4.strip() if isinstance(tel4, str) else ""
    cand = None
    if e and re.fullmatch(r"[0-9]{4}", t4):
        cand = db["candidats"].find_one({"campagne": store.CAMPAGNE, "email": e})
        if cand is not None and not hmac.compare_digest(str(cand.get("tel4") or ""), t4):
            cand = None
    if cand is None:
        _echec(db, email, ip, now)
    return cand


def emettre_jeton(candidat_id, sid, now):
    payload = {"sub": str(candidat_id), "sid": sid, "aud": AUDIENCE,
               "iat": int(now.timestamp()), "exp": int((now + DUREE_JETON).timestamp())}
    return jwt.encode(payload, _secret(), algorithm="HS256")


def lire_jeton(jeton):
    if not jeton or not isinstance(jeton, str):
        return None
    try:
        p = jwt.decode(jeton, _secret(), algorithms=["HS256"], audience=AUDIENCE,
                       options={"require": ["exp", "sub", "aud"]})
    except (jwt.PyJWTError, RuntimeError):
        return None
    if not isinstance(p.get("sub"), str) or not isinstance(p.get("sid"), str):
        return None
    return p


def etat_fenetre(reglages, now):
    """avant / ouvert / apres, ou non_configure tant que reply_to ou
    contact_rgpd est vide (ou la fenetre absente)."""
    for k in ("reply_to", "contact_rgpd"):
        v = reglages.get(k)
        if not isinstance(v, str) or not v.strip():
            return "non_configure"
    ouv, fer = reglages.get("ouverture"), reglages.get("fermeture")
    if ouv is None or fer is None:
        return "non_configure"
    if now < ouv:
        return "avant"
    if now > fer:
        return "apres"
    return "ouvert"


def poser_cookie(reponse, jeton):
    reponse.set_cookie(COOKIE, jeton, max_age=int(DUREE_JETON.total_seconds()),
                       path=COOKIE_PATH, httponly=True, secure=not est_dev(), samesite="Lax")
