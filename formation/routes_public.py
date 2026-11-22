"""Pages et API candidat (/test-pc-organisation), spec 6.

Pages publiques (pas de role_required), CSRF actif sur toutes les ecritures
(en-tete X-CSRFToken, jeton lu dans la balise meta des gabarits).

Session : cookie `formation_session` (acces.py). `tentative.connexion` n'est
appele qu'a une identification reussie et a chaque chargement de la page
`/test` : nouveau `sid`, cookie reemis. Un rechargement ou un second onglet
clot donc l'epreuve en cours (reprise comptee) et l'ancien onglet recoit
401 {"error": "session"}. `/api/etat` n'appelle jamais `connexion`.
`expirer_si_besoin` n'est appele que dans les requetes de la session active.
"""
import json
import logging
import secrets
from datetime import datetime, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, jsonify, make_response, redirect, render_template, request, url_for
from flask_wtf.csrf import generate_csrf

from formation import acces, banque, store, tentative

formation_bp = Blueprint("formation", __name__, url_prefix="/test-pc-organisation")

MAX_ACTION_OCTETS = 16 * 1024
# Refus des regles du passage unique (tentative.py) transmis tels quels au client
CODES_REFUS = ("ordre", "etat", "donnees", "tentative")
logger = logging.getLogger(__name__)
ENTRAINEMENTS = ("radio", "patrouilles")
_PARIS = ZoneInfo("Europe/Paris")


def _now():
    return datetime.now(timezone.utc)


@formation_bp.after_request
def _entetes(reponse):
    reponse.headers["Cache-Control"] = "no-store"
    reponse.headers["Referrer-Policy"] = "no-referrer"
    return reponse


def _err(status, **corps):
    return jsonify(dict(ok=False, **corps)), status


def _erreur_session():
    return _err(401, error="session")


def _ip():
    return request.remote_addr or ""


def _candidat_du_cookie(db):
    """(candidat, sid) du cookie valide, ou None."""
    p = acces.lire_jeton(request.cookies.get(acces.COOKIE))
    if p is None:
        return None
    try:
        oid = ObjectId(p["sub"])
    except (InvalidId, TypeError):
        return None
    cand = db["candidats"].find_one({"_id": oid, "campagne": store.CAMPAGNE})
    return (cand, p["sid"]) if cand is not None else None


def _nouvelle_session(db, candidat, now):
    """Nouveau sid, qui devient la seule session active. Renvoie (etat, jeton)."""
    sid = secrets.token_urlsafe(18)
    jeton = acces.emettre_jeton(str(candidat["_id"]), sid, now)  # avant : secret absent -> rien n'est clos
    return tentative.connexion(db, candidat, sid, _ip(), now), jeton


def _corps_json():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else None


def api_session(fn):
    """Route d'API de la session active : 401 sans cookie valide ou si une autre
    session a pris la main ; epreuve expiree close avant le traitement ; erreurs
    des regles du passage unique traduites en statuts HTTP."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        db = store.get_db()
        sess = _candidat_du_cookie(db)
        if sess is None:
            return _erreur_session()
        cand, sid = sess
        now = _now()
        try:
            t = tentative.active(db, str(cand["_id"]))
            if t is not None:
                if t.get("sid") != sid:
                    return _erreur_session()
                t = tentative.expirer_si_besoin(db, t, now)
            return fn(db, cand, sid, t, now, *args, **kwargs)
        except PermissionError:
            return _erreur_session()
        except ValueError as e:
            code = str(e)
            if code not in CODES_REFUS:
                logger.exception("formation : refus inattendu sur %s", request.path)
                code = "requete"
            return _err(409, error=code)
        except RuntimeError:
            return _err(503, error="reessayez")
    return wrapper


# ---------------------------------------------------------------- pages

def _date_fr(v):
    if isinstance(v, datetime):
        return v.astimezone(_PARIS).strftime("%d/%m/%Y à %H:%M")
    if isinstance(v, str):
        try:
            return datetime.strptime(v, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return v
    return ""


@formation_bp.get("/")
@formation_bp.get("")  # ajoutee en premier : url_for donne /test-pc-organisation
def accueil():
    db = store.get_db()
    r = store.reglages(db)
    return render_template(
        "formation/accueil.html", fenetre=acces.etat_fenetre(r, _now()),
        contact_rgpd=r.get("contact_rgpd") or "",
        conservation=_date_fr(r.get("conservation_jusqu_au")),
        ouverture=_date_fr(r.get("ouverture")), fermeture=_date_fr(r.get("fermeture")))


def _prechargement():
    """Requete de prechargement / prerendu du navigateur (Sec-Purpose, Purpose)."""
    for h in ("Sec-Purpose", "Purpose"):
        v = (request.headers.get(h) or "").lower()
        if "prefetch" in v or "prerender" in v:
            return True
    return False


@formation_bp.get("/test")
def page_test():
    if _prechargement():
        return "", 204  # un prechargement ne doit jamais clore une epreuve en cours
    db = store.get_db()
    sess = _candidat_du_cookie(db)
    if sess is None:
        return redirect(url_for("formation.accueil"))
    try:
        etat, jeton = _nouvelle_session(db, sess[0], _now())
    except RuntimeError:
        return "Service momentanément indisponible, réessayez dans un instant.", 503
    reponse = make_response(render_template("formation/test.html", etat=etat))
    acces.poser_cookie(reponse, jeton)
    return reponse


# ---------------------------------------------------------------- API

@formation_bp.post("/api/connexion")
def api_connexion():
    d = _corps_json()
    if d is None:
        return _err(400, error="donnees")
    db = store.get_db()
    now = _now()
    email, tel4, ip = d.get("email"), d.get("tel4"), _ip()
    resultat, cand = acces.essayer(db, email, tel4, ip, now)
    if resultat == "bloque":
        return _err(429, message=acces.MESSAGE_BLOQUE)
    if cand is None:
        return _err(401, message=acces.MESSAGE_GENERIQUE)
    fenetre = acces.etat_fenetre(store.reglages(db), now)
    if fenetre != "ouvert" and tentative.active(db, str(cand["_id"])) is None:
        return _err(403, fenetre=fenetre)
    try:
        etat, jeton = _nouvelle_session(db, cand, now)
    except RuntimeError:
        return _err(503, error="reessayez")
    reponse = jsonify({"ok": True, "etat": etat})
    acces.poser_cookie(reponse, jeton)
    return reponse


@formation_bp.get("/api/etat")
@api_session
def api_etat(db, cand, sid, t, now):
    return jsonify({"ok": True, "etat": tentative.etat_client(t),
                    "fenetre": acces.etat_fenetre(store.reglages(db), now)})


@formation_bp.post("/api/commencer")
@api_session
def api_commencer(db, cand, sid, t, now):
    d = _corps_json()
    if d is None or d.get("accepte") is not True:
        return _err(400, error="accepte")
    r = store.reglages(db)
    fenetre = acces.etat_fenetre(r, now)
    if fenetre != "ouvert":
        return _err(403, fenetre=fenetre)
    etat = tentative.commencer(db, cand, sid, r.get("coef_amenagement"), now)
    return jsonify({"ok": True, "etat": etat})


@formation_bp.post("/api/epreuve/<epreuve>/debut")
@api_session
def api_debut(db, cand, sid, t, now, epreuve):
    enonce = tentative.debut_epreuve(db, cand, sid, epreuve, now)
    return jsonify({"ok": True, "enonce": enonce})


@formation_bp.post("/api/epreuve/<epreuve>/action")
@api_session
def api_action(db, cand, sid, t, now, epreuve):
    if (request.content_length or 0) > MAX_ACTION_OCTETS:
        return _err(413, error="trop_grand")
    brut = request.stream.read(MAX_ACTION_OCTETS + 1)  # borne meme sans Content-Length
    if len(brut) > MAX_ACTION_OCTETS:
        return _err(413, error="trop_grand")
    try:
        d = json.loads(brut)
    except (ValueError, UnicodeDecodeError):
        d = None
    if not isinstance(d, dict):
        return _err(400, error="donnees")
    tentative.action(db, cand, sid, epreuve, d, now)
    return jsonify({"ok": True})


@formation_bp.post("/api/epreuve/<epreuve>/fin")
@api_session
def api_fin(db, cand, sid, t, now, epreuve):
    etat = tentative.fin_epreuve(db, cand, sid, epreuve, now)
    return jsonify({"ok": True, "etat": etat})


@formation_bp.get("/api/csrf")
def api_csrf():
    """Nouveau jeton CSRF (sans session candidat) : le client le demande sur un
    refus CSRF et rejoue sa requete une fois, plutot que de recharger la page
    (un rechargement pendant une epreuve la clot)."""
    return jsonify({"csrf_token": generate_csrf()})


@formation_bp.get("/api/entrainement/<nom>")
def api_entrainement(nom):
    """Contenu d'entrainement (radio, patrouilles) : rien n'est enregistre."""
    if _candidat_du_cookie(store.get_db()) is None:
        return _erreur_session()
    if nom not in ENTRAINEMENTS:
        return _err(404, error="entrainement")
    return jsonify({"ok": True, "contenu": banque.charger()["entrainement"][nom]})
