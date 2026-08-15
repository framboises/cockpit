"""Blocs des quatre pages operationnelles de la montre.

Fonctions pures et lectures Mongo, `db` toujours passe en argument. Aucun
import de Flask, ni de app, meteo ou traffic : ces trois modules importent
app, et un import en retour ferait un cycle. Le calcul du trafic et de la
meteo vit dans trafic_etat et meteo_etat, sans Flask, precisement pour cela.

Chaque constructeur rend un dictionnaire compact ou None. Il ne leve jamais :
l'appelant met le bloc a null et les autres pages restent servies.
"""

import logging
from datetime import datetime, timezone

import trafic_etat

logger = logging.getLogger(__name__)

# Au-dela, la liste ne tient plus sur un cadran rond et le detail se lit sur
# le cockpit.
MAX_TERRAINS = 4

# Les quatre categories nommees sur la page, et leur cle dans le payload.
CATEGORIES_NOMMEES = (
    ("s", "PCO.Secours"),
    ("sc", "PCO.Securite"),
    ("tq", "PCO.Technique"),
    ("f", "PCO.Flux"),
)

# Repliees sur la ligne << + N (M) >>. Les compter a zero serait faux.
CATEGORIES_AUTRES = ("PCO.Information", "PCO.MainCourante", "PCO.Fourriere")

# Une fiche est close quand status_code vaut 10 (app.py:4808).
STATUT_CLOS = 10


def _epoch(moment):
    """Epoch UTC d'un datetime naif-UTC, convention pymongo."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def build_main_courante(db, event, year, now_utc=None):
    """Compteurs [en cours, terminees] par categorie. None si pas d'evenement."""
    if not event or year is None:
        return None
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    pipeline = [
        {"$match": {"event": event, "year": int(year),
                    "category": {"$regex": "^PCO"}}},
        {"$group": {"_id": {"cat": "$category",
                            "clos": {"$eq": ["$status_code", STATUT_CLOS]}},
                    "n": {"$sum": 1}}},
    ]
    comptes = {}
    for ligne in db["pcorg"].aggregate(pipeline):
        cat = (ligne["_id"] or {}).get("cat") or ""
        clos = bool((ligne["_id"] or {}).get("clos"))
        seau = comptes.setdefault(cat, [0, 0])
        seau[1 if clos else 0] = int(ligne.get("n", 0))

    bloc = {"t": _epoch(now_utc)}
    for cle, categorie in CATEGORIES_NOMMEES:
        bloc[cle] = list(comptes.get(categorie, [0, 0]))
    autres = [0, 0]
    for categorie in CATEGORIES_AUTRES:
        paire = comptes.get(categorie, [0, 0])
        autres[0] += paire[0]
        autres[1] += paire[1]
    bloc["o"] = autres
    return bloc


def build_trafic(db, now_utc=None):
    """Verdict global, comptes geofences et terrains les plus charges."""
    routes = trafic_etat.lire_routes(db)
    alertes = trafic_etat.lire_alertes(db)
    if not routes and not alertes:
        return None

    terrains = trafic_etat.agreger_terrains(routes)
    comptes = trafic_etat.compter_alertes(alertes)
    pire = max([t["severity"] for t in terrains], default=0)

    # Tri par gravite decroissante, pas par ratio : la montre montre d'abord
    # ce qui coince, et deux terrains de ratios voisins peuvent tomber dans
    # des paliers differents.
    ordonnes = sorted(terrains, key=lambda t: t["severity"], reverse=True)

    resume = []
    for terrain in ordonnes[:MAX_TERRAINS]:
        sens = {"in": "i", "out": "o"}.get(terrain.get("direction"), "-")
        resume.append([
            terrain["terrain"],
            sens,
            int(round((terrain["currentTime"] or 0) / 60.0)),
            terrain["severity"],
        ])

    return {
        "t": _epoch(trafic_etat.fraicheur(db)),
        "vd": trafic_etat.verdict_global(comptes, pire),
        "ac": comptes.get("ACCIDENT", 0),
        "z": comptes.get("total", 0),
        "r": resume,
    }
