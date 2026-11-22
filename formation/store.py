"""Base dediee du module Formation, index, reglages et journal d'administration.

Le module n'ecrit jamais dans la base `titan` / `titan_dev` : tout vit dans
`cockpit_formation` (prod) ou `cockpit_formation_dev` (dev).
"""
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pymongo import ASCENDING, MongoClient

CAMPAGNE = "pcorg-2026-12"
EPREUVES = ["radio", "appel", "mc", "patrouilles", "acces", "logique", "situations"]

# MongoDB n'accepte pas $ne dans un index partiel : l'unicite d'une tentative
# par candidat porte sur les statuts ci-dessous. Les statuts d'une tentative
# sont exactement en_cours, terminee, interrompue, reinitialisee (cette
# derniere est exclue de l'unicite).
STATUTS_TENTATIVE_ACTIFS = ["en_cours", "terminee", "interrompue"]

_PARIS = ZoneInfo("Europe/Paris")
_db_tests = None
_client = None


def est_prod():
    """Meme regle qu'app.py (IS_PROD) : prod si TITAN_ENV vaut prod ou production."""
    return os.getenv("TITAN_ENV", "dev").strip().lower() in ("prod", "production")


def nom_base():
    return "cockpit_formation" if est_prod() else "cockpit_formation_dev"


def set_db_for_tests(db):
    global _db_tests
    _db_tests = db


def get_db():
    global _client
    if _db_tests is not None:
        return _db_tests
    if _client is None:
        _client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
    return _client[nom_base()]


def ensure_indexes(db):
    db["candidats"].create_index(
        [("campagne", ASCENDING), ("email", ASCENDING)], unique=True, name="uniq_campagne_email")
    db["tentatives"].create_index(
        [("campagne", ASCENDING), ("candidat_id", ASCENDING)], unique=True,
        partialFilterExpression={"statut": {"$in": STATUTS_TENTATIVE_ACTIFS}},
        name="uniq_tentative_active")
    for typ in ("invitation", "relance"):
        db["envois"].create_index(
            [("campagne", ASCENDING), ("candidat_id", ASCENDING), ("type", ASCENDING)],
            unique=True, partialFilterExpression={"type": typ}, name="uniq_envoi_" + typ)
    db["essais_connexion"].create_index("t", expireAfterSeconds=900, name="ttl_essais")


GABARIT_DEFAUT = {
    "objet": "PC Organisation : votre test en ligne, à passer avant le {date_limite}",
    "corps": """Bonjour {prenom},

Merci pour votre candidature au PC Organisation. Avant les sélections sur le Circuit, nous vous proposons un test en ligne d'environ 20 minutes : une vacation de nuit fictive en sept épreuves courtes. Aucune connaissance du site n'est nécessaire, toutes les règles sont expliquées à l'écran.

Le test : {lien}
Pour vous identifier : l'adresse e-mail utilisée dans le questionnaire et les 4 derniers chiffres de votre téléphone mobile.
À passer avant le {date_limite}.

Important : vous ne pouvez passer ce test qu'une seule fois, sans possibilité de recommencer. Une épreuve interrompue (fenêtre fermée, page rechargée, coupure de connexion) est perdue.
Avant de commencer :
- installez-vous seul, au calme, sur un ordinateur (pas de téléphone) ;
- utilisez une connexion stable, branchez votre ordinateur et coupez les notifications ;
- prévoyez 25 minutes sans interruption ; le son est conseillé mais facultatif.

Vous avez besoin d'un temps aménagé ? Répondez à ce message avant de commencer le test.

L'équipe de sélection vous contactera ensuite par e-mail ; les convocations partent au plus tard le 4 décembre.

Ludovic Arnault
Directeur des opérations adjoint
Automobile Club de l'Ouest""",
}

def _paris_utc(annee, mois, jour, h, mn):
    return datetime(annee, mois, jour, h, mn, tzinfo=_PARIS).astimezone(timezone.utc)


def _defauts():
    return {
        "ouverture": _paris_utc(2026, 11, 30, 8, 0),
        "fermeture": _paris_utc(2026, 12, 2, 23, 59),
        "coef_amenagement": 1.33,
        "ponderations": {e: 1 for e in EPREUVES},
        "reply_to": "",
        "contact_rgpd": "",
        "conservation_jusqu_au": "2027-01-30",
        "debit_envoi_s": 2.0,
        "expediteur_nom": "PC Organisation - ACO",
        "gabarit": dict(GABARIT_DEFAUT),
        "campagne_arretee": False,
    }


def _aware(v):
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


def reglages(db):
    out = _defauts()
    doc = db["reglages"].find_one({"_id": CAMPAGNE}) or {}
    for k, v in doc.items():
        if k in out:
            out[k] = _aware(v)
    if not (out["gabarit"] or {}).get("objet") or not (out["gabarit"] or {}).get("corps"):
        out["gabarit"] = dict(GABARIT_DEFAUT)
    return out


def journal(db, action, auteur, **details):
    db["journal_admin"].insert_one({
        "campagne": CAMPAGNE, "action": action, "auteur": auteur,
        "details": details, "t": datetime.now(timezone.utc)})


def maj_reglages(db, champs, auteur):
    """Ecrit les champs connus ; le journal garde l'ancienne et la nouvelle valeur des seuls champs modifies."""
    champs = {k: v for k, v in (champs or {}).items() if k in _defauts()}
    if champs:
        avant = reglages(db)
        db["reglages"].update_one({"_id": CAMPAGNE}, {"$set": champs}, upsert=True)
        changes = {k: v for k, v in champs.items() if _aware(v) != avant.get(k)}
        if changes:
            journal(db, "reglages", auteur, champs=sorted(changes),
                    avant={k: avant.get(k) for k in changes}, apres=changes)
    return reglages(db)
