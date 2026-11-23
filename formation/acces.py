"""Acces du candidat au test PC Organisation (spec 6, points 1 et 2).

- Identification par e-mail (normalise comme a l'import) + 4 derniers chiffres
  du mobile, message d'erreur generique unique.
- Limitation des essais : 5 echecs sur 15 min pour l'e-mail OU pour l'IP
  (`request.remote_addr`, jamais X-Forwarded-For, IPv6 regroupee par /64) ->
  blocage temporaire ; plafond dur de 20 echecs sur 24 h par e-mail.
  Chaque echec est un document `essais_connexion` {cle, t} (index TTL 24 h).
- Jeton de session : JWT HS256 signe par FORMATION_JWT_SECRET, `aud=formation`,
  1 h, portant le candidat (`sub`) et le `sid` de la session active.
- Fenetre d'ouverture du test, lue dans les reglages.
"""
import hmac
import ipaddress
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
# Plafond dur par e-mail, que rien ne contourne (pas meme une IP neuve) : sur
# les 3 jours de la fenetre, au plus ~60 essais sur 10 000 codes possibles.
PLAFOND_EMAIL_JOUR = 20
FENETRE_JOUR = timedelta(hours=24)
DUREE_JETON = timedelta(hours=1)
AUDIENCE = "formation"
COOKIE = "formation_session"
COOKIE_PATH = "/test-pc-organisation"
_SECRET_DEV = "dev-formation-secret-ne-jamais-utiliser-en-production"


def est_dev():
    """Refus par defaut : dev (secret de repli, cookie sans Secure) seulement si
    TITAN_ENV vaut explicitement dev, development ou vide. Toute autre valeur
    (staging, faute de frappe...) exige FORMATION_JWT_SECRET et pose Secure.
    Le nom de la base reste regle par store.est_prod()."""
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
    return ["email:" + e, "ip:" + _cle_ip(ip)]


def _cle_ip(ip):
    """IPv4 telle quelle ; IPv6 regroupee par /64 (un abonne en dispose d'un
    entier, chaque adresse ne doit pas offrir de nouveaux essais)."""
    brut = str(ip or "")
    try:
        a = ipaddress.ip_address(brut)
    except ValueError:
        return brut
    if a.version == 4:
        return str(a)
    if a.ipv4_mapped is not None:
        return str(a.ipv4_mapped)
    return str(ipaddress.ip_network(str(a) + "/64", strict=False))


def _compte(db, cle, depuis):
    return db["essais_connexion"].count_documents({"cle": cle, "t": {"$gt": depuis}})


def est_bloque(db, email, ip, now):
    """>= 5 echecs sur 15 min pour l'e-mail ou pour l'IP, ou >= 20 sur 24 h
    pour l'e-mail."""
    depuis = now - FENETRE_ECHECS
    cle_email = _cles(email, ip)[0]
    return (any(_compte(db, c, depuis) >= SEUIL_ECHECS for c in _cles(email, ip))
            or _compte(db, cle_email, now - FENETRE_JOUR) >= PLAFOND_EMAIL_JOUR)


def _paire(db, email, tel4):
    e = normaliser_email(email) if isinstance(email, str) else None
    t4 = tel4.strip() if isinstance(tel4, str) else ""
    if not e or not re.fullmatch(r"[0-9]{4}", t4):
        return None
    cand = db["candidats"].find_one({"campagne": store.CAMPAGNE, "email": e})
    if cand is None or not hmac.compare_digest(str(cand.get("tel4") or ""), t4):
        return None
    return cand


def essayer(db, email, tel4, ip, now):
    """Un essai d'identification : ("ok", candidat), ("refuse", None) ou
    ("bloque", None). L'essai est enregistre comme echec AVANT le comptage
    (e-mail et IP) : des requetes simultanees se voient entre elles et ne
    passent pas toutes sous le seuil. Plus de 5 essais sur 15 min (celui-ci
    compris) : bloque, l'essai reste compte. Exception pour l'e-mail : la bonne
    paire passe depuis une IP sans autre essai dans la fenetre (anti-blocage
    cible par un tiers). Paire correcte : l'essai est retire (un succes n'est
    pas un echec)."""
    cle_email, cle_ip = _cles(email, ip)
    docs = [{"campagne": store.CAMPAGNE, "cle": c, "t": now} for c in (cle_email, cle_ip)]
    ids = db["essais_connexion"].insert_many(docs).inserted_ids
    depuis = now - FENETRE_ECHECS
    n_ip = _compte(db, cle_ip, depuis)
    if n_ip > SEUIL_ECHECS:
        return "bloque", None
    if _compte(db, cle_email, now - FENETRE_JOUR) > PLAFOND_EMAIL_JOUR:
        return "bloque", None
    email_bloque = _compte(db, cle_email, depuis) > SEUIL_ECHECS
    cand = _paire(db, email, tel4)
    # E-mail bloque : seule la bonne paire, depuis une IP sans autre essai dans
    # la fenetre (n_ip == 1 : celui-ci), passe. Un tiers qui connait l'e-mail ne
    # peut donc pas empecher le candidat d'entrer, et chaque IP neuve n'offre
    # qu'un seul essai de plus sur un e-mail bloque.
    if email_bloque and (cand is None or n_ip > 1):
        return "bloque", None
    if cand is None:
        return "refuse", None
    db["essais_connexion"].delete_many({"_id": {"$in": ids}})
    return "ok", cand


def identifier(db, email, tel4, ip, now):
    """Candidat dont la paire (e-mail, 4 chiffres) correspond, ou None (paire
    inconnue ou acces bloque). Chaque echec est enregistre pour l'e-mail et l'IP."""
    return essayer(db, email, tel4, ip, now)[1]


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
