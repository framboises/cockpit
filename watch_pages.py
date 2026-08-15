"""Blocs des quatre pages operationnelles de la montre.

Fonctions pures et lectures Mongo, `db` toujours passe en argument. Aucun
import de Flask, ni de app, meteo ou traffic : ces trois modules importent
app, et un import en retour ferait un cycle. Le calcul du trafic et de la
meteo vit dans trafic_etat et meteo_etat, sans Flask, precisement pour cela.

Chaque constructeur rend un dictionnaire compact ou None. Il ne leve jamais :
l'appelant met le bloc a null et les autres pages restent servies.
"""

import logging
from datetime import datetime, time, timedelta, timezone

import meteo_etat
import pcorg_summary
import trafic_etat
import watch_peaks
import watch_state

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
    # `vigilance` fusionne bulletin brut + etat_vigilance() + niveau_vigilance() :
    # aucun des trois ne porte de cle "niveau". La couleur du jour, elle,
    # existe reellement (niveau_vigilance) -- on la reduit sur l'echelle du
    # mur, sans en inventer une pour la montre.
    couleur_jour = vigilance.get("couleur_jour")
    if couleur_jour in meteo_etat.ORDRE_COULEURS:
        vg = meteo_etat.ORDRE_COULEURS.index(couleur_jour)
    else:
        vg = 0

    # `t` doit dater la DONNEE, pas l'instant du calcul -- sinon le bloc parait
    # eternellement frais meme si le flux se fige. Le seul horodatage reel
    # qu'expose etat_mur est le run PIAF (prochaine_pluie.run_at) ; les
    # previsions horaires n'en portent aucun (meteo_etat._fraicheur_mur le dit
    # explicitement). Ce n'est donc qu'une verite partielle -- ca date la
    # pluie a venir, pas la temperature -- mais c'est la meilleure date
    # disponible, et mieux vaut ca qu'une date inventee. Sans elle, `t` reste
    # None : la montre doit pouvoir dire "je ne sais pas dater" plutot que de
    # mentir sur la fraicheur.
    horodatage = None
    run_at = pluie.get("run_at")
    if run_at:
        try:
            horodatage = _epoch(datetime.fromisoformat(str(run_at)))
        except (TypeError, ValueError):
            horodatage = None

    return {
        "t": horodatage,
        "tc": actuel.get("temperature_c"),
        "v": actuel.get("vent_moyen_kmh"),
        "rf": actuel.get("vent_rafale_kmh"),
        "pl": pluie.get("dans_min") if attendue else None,
        "pm": pluie.get("pic_mmh") if attendue else None,
        "cn": consigne[:CONSIGNE_MAX] if consigne else None,
        "cl": niveau,
        "vg": vg,
    }


def _epoch_du_pic(jour, heure_str):
    """Epoch UTC du releve qui a produit le pic du jour, pas l'instant du calcul.

    `_max_current_in_snapshots` ne rend que l'heure Paris formattee ("14h15"),
    pas le datetime brut du releve. On la recombine avec `jour` pour obtenir
    un horodatage reel : c'est precisement ce qui date LA DONNEE (le moment
    ou le compteur a fourni cette valeur), et pas simplement << maintenant >>.
    Si le compteur se fige, cet horodatage cesse d'avancer alors que `now`
    continuerait de tourner -- c'est l'ecueil deja rencontre sur le bloc
    meteo (tache 5).
    """
    if not heure_str:
        return None
    try:
        heures, minutes = heure_str.split("h")
        instant_paris = datetime.combine(
            jour, time(int(heures), int(minutes)), tzinfo=pcorg_summary.TZ_PARIS)
    except (TypeError, ValueError):
        return None
    return _epoch(instant_paris)


def _pic_multi_source(db, jour, location_id):
    """Plus haut pic de presents d'un jour donne, toutes sources balayees.

    Le direct (`data_access`) ne porte que les jours pas encore archives :
    un jour clos, dont l'admin a purge le direct, n'y laisse plus rien. Sans
    balayer aussi les archives, interroger un jour passe rendrait un pic
    absent alors que la donnee existe bel et bien -- ailleurs. Meme principe
    que `watch_peaks.peak_for_edition`, qui refuse deja de deviner depuis le
    nom de la collection : on regarde toutes les sources, sans a priori.
    """
    pic = None
    heure = None
    for collection in watch_peaks.snapshot_collections(db):
        valeur, h = pcorg_summary._max_current_in_snapshots(
            db, collection, jour, location_id)
        if valeur is not None and (pic is None or valeur > pic):
            pic = valeur
            heure = h
    return pic, heure


def _jour_semaine_aligne(jour_n1, jour_semaine_cible):
    """Recale une date de course N-1 sur le jour de semaine de la course N.

    `globalHoraires.race` (que `resolve_race_dt` privilegie) ne designe pas
    toujours la meme chose d'un millesime a l'autre : certaines editions y
    portent le DEPART, d'autres l'ARRIVEE -- documente dans CLAUDE.md pour
    24H MOTOS 2025 (arrivee, dimanche) contre 2026 (depart, samedi), et
    constate aussi sur 24H AUTOS. Comparer les dates telles quelles decale
    tout l'alignement d'un jour, de la pire maniere : le resultat reste
    plausible a l'oeil (un dimanche succede a un samedi sans rien
    d'absurde), donc l'erreur ne se voit pas.

    Le seul invariant qui ne depende pas de la convention de saisie est
    qu'un evenement annuel revient chaque annee le meme jour de semaine
    (meme principe que `scan_frequentation._normalize_race_dates`, qui
    resout le meme probleme pour la vue Frequentation du rapport scans).
    On ne corrige donc pas la date elle-meme, seulement son jour de
    semaine, au plus proche (ecart signe ramene dans [-3, +3] jours) --
    jamais de saut de plus de trois jours, qui trahirait autre chose qu'une
    simple confusion depart/arrivee.

    `resolve_race_dt` n'est PAS touchee : elle resout une edition a la
    fois et ne peut pas connaitre le jour de semaine de sa voisine, la
    normalisation est par nature une operation de paire. Ses fenetres de
    balayage de pic (+/-10 et +3 jours) tolerent de toute facon un ecart
    d'un jour sans consequence : rien n'a besoin d'etre elargi.
    """
    delta = (jour_n1.weekday() - jour_semaine_cible) % 7
    if delta == 0:
        return jour_n1
    decalage = delta if delta <= 3 else delta - 7
    return jour_n1 - timedelta(days=decalage)


def build_frequentation(db, event, year, now, location_id=None):
    """Pic de presents du jour, son heure, et le pic N-1 au jour EQUIVALENT.

    L'alignement N-1 se fait au decalage au jour de course, jamais a la date
    calendaire : c'est la convention de tout le cockpit (cf. CLAUDE.md,
    bloc affluence), et une edition ne tombe pas le meme jour du mois d'une
    annee sur l'autre. `resolve_race_dt` porte deja la garde sur l'annee et
    les alias historique_controle -- on ne la contourne pas.

    La date de course N-1 est en plus recalee sur le jour de semaine de la
    course N avant d'appliquer le decalage (voir `_jour_semaine_aligne`) :
    sans cela, une edition dont `globalHoraires.race` change de convention
    (depart / arrivee) d'un millesime a l'autre fausserait l'alignement
    d'un jour entier, silencieusement.

    `location_id` par defaut n'est PAS une constante figee : c'est le
    compteur PRINCIPAL choisi par l'exploitation dans le live-controle
    (`watch_state.read_principal_id`), le meme que celui que lit la page 1
    de la montre. Repli sur `watch_peaks.DEFAULT_LOCATION_ID` seulement si
    le document global ne le precise pas. Sans cet alignement, le jour ou
    l'exploitation bascule le compteur principal (panne d'un boitier,
    passage sur un secours), cette page continuerait de lire l'ancien
    appareil en dur -- deux pages de la meme montre montrant deux
    compteurs physiques differents pour le meme instant, sans que rien ne
    le signale. `pj`/`ph` ET `n1` utilisent le meme `location_id` : ce sont
    deux chiffres affiches cote a cote comme une comparaison, ils doivent
    venir du meme appareil ou l'ecart ne veut rien dire. Si le principal
    est repointe vers un boitier sans historique N-1, `n1` tombe
    naturellement a `None` -- jamais un chiffre invente.
    """
    if not event or year is None:
        return None
    if location_id is None:
        location_id = watch_state.read_principal_id(db) or watch_peaks.DEFAULT_LOCATION_ID

    jour = now.date()
    pic, heure = _pic_multi_source(db, jour, location_id)

    pic_n1 = None
    course = watch_peaks.resolve_race_dt(db, event, int(year))
    course_n1 = watch_peaks.resolve_race_dt(db, event, int(year) - 1)
    if course is not None and course_n1 is not None:
        course_n1_jour = _jour_semaine_aligne(course_n1.date(), course.weekday())
        decalage = jour - course.date()
        jour_n1 = course_n1_jour + decalage
        pic_n1, _ = _pic_multi_source(db, jour_n1, location_id)

    return {
        "t": _epoch_du_pic(jour, heure),
        "pj": pic,
        "ph": heure,
        "n1": pic_n1,
    }
