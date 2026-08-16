"""Point de guidage envoye depuis le cockpit vers UNE montre.

L'operateur clique un point sur une carte du cockpit, choisit une montre, et
le point part. La montre l'affiche sur sa page Guidage (fleche, distance) et
peut le pousser dans ses lieux enregistres natifs pour passer la main a la
navigation Garmin.

Fonctions pures et lectures Mongo, `db` toujours passe en argument. Aucun
import de Flask ni de app : ce module est appele par watch_api (routes) et
par watch_api.state (payload), et doit rester testable sans backend.

UN SEUL POINT PAR MONTRE. Ce n'est pas une file de destinations : le guidage
repond a << ou dois-je aller MAINTENANT >>. Un second envoi remplace le
premier, il ne s'empile pas derriere.
"""

import logging
import math
from datetime import datetime, timezone

from pymongo import ReturnDocument

logger = logging.getLogger(__name__)

COLLECTION = "watch_guidage"

# Le libelle est affiche sur une seule ligne d'un cadran de 280 px, sous la
# fleche. Mesure a la sonde : au-dela, il est tronque a l'affichage -- autant
# le borner a l'ecriture, ou l'operateur voit ce qu'il tape.
LABEL_MAX = 24

_indexes_ready = False


def reset_indexes():
    """Pour les tests : force la recreation des index au prochain acces."""
    global _indexes_ready
    _indexes_ready = False


def _ensure_indexes(db):
    global _indexes_ready
    if _indexes_ready:
        return
    # Un seul document par montre : l'unicite est la garantie qu'un second
    # envoi REMPLACE le premier au lieu d'en creer un deuxieme que personne
    # ne lirait jamais.
    db[COLLECTION].create_index("token_id", unique=True)
    _indexes_ready = True


def _maintenant():
    """Naif UTC, convention pymongo de ce projet (cf. watch_api._maintenant).

    Melanger un naif et un conscient leve un TypeError a la premiere
    comparaison -- deja rencontre sur ce projet.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def validate_point(lat, lon, label):
    """(ok, code_erreur). Valide un point avant ecriture.

    Rejette explicitement NaN et l'infini : ils traversent `float()` sans
    broncher, passent les bornes (toute comparaison avec NaN est fausse,
    donc `not (-90 <= nan <= 90)` est VRAI et l'attrape, mais l'infini
    seul serait attrape par les bornes) et arriveraient jusqu'a la montre,
    ou ils produiraient une fleche pointant nulle part. `math.isfinite`
    tranche les deux d'un coup, sans dependre de la subtilite des bornes.
    """
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return False, "coordonnees_invalides"
    if not math.isfinite(lat) or not math.isfinite(lon):
        return False, "coordonnees_invalides"
    if not (-90.0 <= lat <= 90.0):
        return False, "latitude_hors_bornes"
    if not (-180.0 <= lon <= 180.0):
        return False, "longitude_hors_bornes"
    if label is None or not str(label).strip():
        return False, "libelle_vide"
    return True, None


def set_point(db, token_id, lat, lon, label, sent_by=None):
    """Pose (ou remplace) le point de guidage d'une montre.

    Rend le document ecrit, ou leve ValueError si le point est invalide.

    `seq` est incremente a CHAQUE envoi, y compris quand le point ne bouge
    pas. C'est lui, et non les coordonnees, qui declenche la vibration cote
    montre : renvoyer deux fois le meme point est un acte volontaire de
    l'operateur (<< je te le redis >>) qui doit se faire sentir, et decider
    d'une vibration en comparant des flottants serait fragile.
    """
    ok, erreur = validate_point(lat, lon, label)
    if not ok:
        raise ValueError(erreur)
    _ensure_indexes(db)

    doc = db[COLLECTION].find_one_and_update(
        {"token_id": token_id},
        {
            "$set": {
                "lat": float(lat),
                "lon": float(lon),
                "label": str(label).strip()[:LABEL_MAX],
                "sent_at": _maintenant(),
                "sent_by": sent_by,
            },
            "$inc": {"seq": 1},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


def clear_point(db, token_id):
    """Efface le point d'une montre. Rend True si un point existait.

    Le document est SUPPRIME, pas vide avec un drapeau : un point efface et
    un point jamais envoye sont operationnellement la meme chose (rien a
    afficher), et garder une coquille obligerait chaque lecteur a distinguer
    deux etats qui ne se distinguent pas a l'ecran.
    """
    _ensure_indexes(db)
    return db[COLLECTION].delete_one({"token_id": token_id}).deleted_count > 0


def read_point(db, token_id):
    """Bloc `gd` du payload, ou None. Ne leve jamais.

    Comme les quatre blocs de watch_pages : une source injoignable rend None
    et les autres pages restent servies. Une page Guidage vide est un etat
    normal -- l'ecrasante majorite du temps, il n'y a pas de point en cours.
    """
    if token_id is None:
        return None
    try:
        _ensure_indexes(db)
        doc = db[COLLECTION].find_one({"token_id": token_id})
    except Exception:
        logger.exception("Lecture du point de guidage impossible")
        return None
    if not doc:
        return None
    try:
        return {
            "lat": float(doc["lat"]),
            "lon": float(doc["lon"]),
            "n": doc.get("label") or "",
            "s": int(doc.get("seq") or 0),
            "t": _epoch(doc.get("sent_at")),
        }
    except (KeyError, TypeError, ValueError):
        logger.warning("Point de guidage malforme pour %s", token_id)
        return None


def _epoch(moment):
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def list_points(db):
    """Tous les points en cours, indexes par token_id (chaine).

    Alimente la carte du cockpit, qui doit montrer d'un coup d'oeil quelle
    montre est deja guidee et vers ou -- sans quoi l'operateur ecraserait
    un guidage en cours sans le savoir.
    """
    _ensure_indexes(db)
    sortie = {}
    for doc in db[COLLECTION].find():
        sortie[str(doc.get("token_id"))] = {
            "lat": doc.get("lat"),
            "lon": doc.get("lon"),
            "label": doc.get("label"),
            "seq": doc.get("seq"),
            "sent_at": _epoch(doc.get("sent_at")),
            "sent_by": doc.get("sent_by"),
        }
    return sortie
