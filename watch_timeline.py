"""Les prochaines vignettes de la timeline operationnelle, pour la montre.

C'est la raison d'etre du cockpit ramenee au poignet : non pas un chiffre,
mais << qu'est-ce qui tombe dans l'heure >>.

AUCUN CALCUL METIER N'EST REECRIT ICI. `pcorg_summary.get_upcoming_timetable`
fait deja tout le travail -- fenetre glissante, tri, et surtout la
FACTORISATION des ouvertures simultanees (huit parkings qui ouvrent a 7 h
font une ligne, pas huit). Sur les 24H Motos 2026, elle ramene 65 vignettes
brutes a 9 lignes : c'est exactement ce qui rend l'information lisible sur
un cadran. Ce module ne fait que compacter sa sortie pour le transport.

`pcorg_summary` n'importe ni Flask ni app (verifie) : cet enchainement ne
cree aucun cycle, contrairement a traffic.py et meteo.py qui ont impose
trafic_etat.py et meteo_etat.py.

Fonctions pures et lectures Mongo, `db` toujours passe en argument. Elles ne
levent jamais : une source injoignable rend None ou une liste vide, et les
autres pages restent servies.
"""

import logging
from datetime import datetime, timezone

import pcorg_summary

logger = logging.getLogger(__name__)

# Fenetre par defaut : la fin de la vacation. Au-dela, ce n'est plus une
# question de poignet mais de planification, et ca se lit sur le cockpit.
FENETRE_HEURES = 12

# Plafond de la liste servie a la montre. Apres factorisation, une journee
# de course tient largement dessous (9 lignes sur les 24H Motos 2026) ; ce
# plafond n'existe que pour qu'un evenement inhabituel ne puisse pas faire
# enfler la reponse sans que personne ne s'en apercoive.
MAX_VIGNETTES = 12

# Mesures a la sonde sur le cadran (FONT_XTINY, corde de la zone de liste) :
# au-dela, le libelle est tronque a l'affichage. Autant le borner ici, ou la
# troncature est au moins consciente et documentee.
ACTIVITE_MAX = 30
LIEU_MAX = 24


def _epoch(iso_paris):
    """Epoch UTC d'un datetime ISO ecrit en heure de Paris (avec offset).

    Le transport ne porte QUE des epochs, jamais des heures formatees : la
    montre affiche un compte a rebours (<< dans 42 min >>), qu'elle doit
    pouvoir recalculer elle-meme. Une heure formatee cote serveur serait
    juste a l'emission et fausse trois minutes plus tard -- alors que le
    releve, lui, peut avoir jusqu'a trois minutes de retard.
    """
    if not iso_paris:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_paris))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        # Ne devrait pas arriver (_serialize_upcoming rend un ISO avec
        # offset), mais un naif interprete comme UTC decalerait de deux
        # heures en ete -- le genre de defaut qui ne se voit qu'au moment ou
        # il compte.
        return None
    return int(dt.timestamp())


def _compacter(vignette):
    """Vignette -> [epoch, activite, lieu, compte de factorisation].

    Un tableau et non un dictionnaire : la liste peut porter douze entrees,
    et les noms de cles se paieraient douze fois. Meme choix que les alertes
    et les axes trafic.

    Le compte de factorisation (`x8`) est rendu SEPAREMENT plutot que laisse
    dans le libelle : la montre choisit ainsi de l'afficher ou non selon la
    place, et n'a pas a analyser une chaine pour le retrouver.
    """
    quand = _epoch(vignette.get("datetime"))
    if quand is None:
        return None
    activite = (vignette.get("activity") or "").strip()
    # `_factorize_upcoming` colle deja " (×8)" au libelle. On le retire pour
    # rendre le compte a part -- sinon la montre afficherait le nombre deux
    # fois, une fois dans le texte et une fois dans sa pastille.
    compte = vignette.get("factor_count")
    if compte:
        coupe = activite.rfind(" (")
        if coupe > 0:
            activite = activite[:coupe]
    return [
        quand,
        activite[:ACTIVITE_MAX],
        (vignette.get("place") or "").strip()[:LIEU_MAX],
        int(compte) if compte else 0,
    ]


def prochaines(db, event, year, heures=FENETRE_HEURES, now_utc=None):
    """Liste compactee des prochaines vignettes. Vide si rien ou si erreur.

    Ne leve jamais : un timetable absent ou malforme rend une liste vide,
    qui se lit a l'ecran << rien de prevu >> -- l'etat normal l'essentiel de
    l'annee.
    """
    if not event or year is None:
        return []
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    try:
        brutes = pcorg_summary.get_upcoming_timetable(
            db, event, year, hours=heures, now_utc=now_utc)
    except Exception:
        logger.exception("Timeline injoignable, page servie vide")
        return []

    sortie = []
    for vignette in brutes or []:
        compacte = _compacter(vignette)
        if compacte is not None:
            sortie.append(compacte)
        if len(sortie) >= MAX_VIGNETTES:
            break
    return sortie


def prochaine(db, event, year, heures=FENETRE_HEURES, now_utc=None):
    """La PROCHAINE vignette seule, ou None.

    Sert le bloc `nx` du payload principal : soixante-dix octets qui
    permettent a la page d'afficher quelque chose des son ouverture, depuis
    le cache, sans attendre la requete paresseuse -- et meme hors de portee
    du telephone. La liste complete, elle, vit sur /timeline.
    """
    liste = prochaines(db, event, year, heures=heures, now_utc=now_utc)
    return liste[0] if liste else None
