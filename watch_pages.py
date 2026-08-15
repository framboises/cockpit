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

import meteo_etat
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

# Niveaux de consigne du mur, replies sur l'echelle 0-3 de la montre.
NIVEAUX_CONSIGNE = {"vigilance": 1, "danger": 2, "critique": 3}

# Une consigne plus longue deborde la largeur utile du cadran rond.
CONSIGNE_MAX = 44


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


def build_meteo(db, now):
    """Condense l'etat du mur. None si la source est injoignable.

    AUCUN SEUIL N'EST REINVENTE ICI. Le mur decide -- rafales, WBGT, orage --
    et redige la consigne en langage d'action ; la montre la repete. C'est ce
    qui garantit que le poignet et le mur ne se contrediront jamais.
    """
    try:
        etat = meteo_etat.etat_mur(db, now)
    except Exception as exc:
        logger.warning("watch_pages : etat meteo indisponible (%s)", exc)
        return None
    if not etat:
        return None

    actuel = etat.get("actuel") or {}
    pluie = etat.get("prochaine_pluie") or {}
    attendue = bool(pluie.get("attendue"))

    # La consigne retenue est la plus grave, pas la premiere : la liste du mur
    # est chronologique, or c'est la gravite qui commande l'action.
    consigne = None
    niveau = 0
    for entree in etat.get("consignes") or []:
        rang = NIVEAUX_CONSIGNE.get(entree.get("niveau"), 0)
        if rang > niveau:
            niveau = rang
            consigne = entree.get("texte")

    vigilance = etat.get("vigilance") or {}

    return {
        "t": _epoch(now),
        "tc": actuel.get("temperature_c"),
        "v": actuel.get("vent_moyen_kmh"),
        "rf": actuel.get("vent_rafale_kmh"),
        "pl": pluie.get("dans_min") if attendue else None,
        "pm": pluie.get("pic_mmh") if attendue else None,
        "cn": consigne[:CONSIGNE_MAX] if consigne else None,
        "cl": niveau,
        "vg": vigilance.get("niveau") or 0,
    }
