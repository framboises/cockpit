"""Indices de contrainte thermique : WBGT, temperature humide, humidex.

Sert le plan canicule. Les valeurs produites peuvent motiver une decision
d'exploitation sur une epreuve a 250 000 personnes : les formules retenues
sont donc citees, leur domaine de validite verifie, et leurs biais connus
signales avec la valeur plutot que passes sous silence.

CE QUI EST CALCULE
  Tw     temperature humide            Stull (2011)
  WBGT   wet bulb globe temperature    approximation du BoM australien
  humidex indice canadien              formule officielle d'Environnement Canada

CE QUI NE L'EST PAS
Le WBGT de reference se mesure avec un thermometre a globe noir, ou se calcule
par le modele iteratif d'echange thermique de Liljegren (2008), qui demande le
rayonnement solaire direct, l'angle zenithal et la vitesse du vent. Nous n'avons
pas le rayonnement en prevision -- seulement en observation quotidienne (GLOT,
et seulement depuis 2010). L'approximation retenue est donc bien une
approximation, et le champ `fiabilite` de chaque resultat dit quand elle
decroche.

BIAIS DOCUMENTES DE L'APPROXIMATION BoM, repris tels quels du producteur :
  - ne tient compte NI du rayonnement NI du vent ; suppose un rayonnement
    modere et un vent faible
  - SURESTIME la contrainte par temps couvert et venteux
  - SURESTIME la nuit et tot le matin, soleil bas ou sous l'horizon
  - biais haut aux forts points de rosee

Autrement dit : un WBGT eleve la nuit ou par grand vent doit etre relu, pas
applique. C'est ce que porte `fiabilite`.

Sources :
  Stull, R. (2011). Wet-Bulb Temperature from Relative Humidity and Air
    Temperature. J. Applied Meteorology and Climatology, 50(11), 2267-2269.
  Bureau of Meteorology (Australie), "Thermal Comfort observations".
  Liljegren et al. (2008), J Occup Environ Hyg 5(10), 645-655.
"""

import math

# Domaine de validite de Stull (2011). Hors de ces bornes, la formule n'est pas
# garantie : erreur de -1 a +0,65 degre a l'interieur, non caracterisee dehors.
STULL_T_MIN, STULL_T_MAX = -20.0, 50.0
STULL_HR_MIN, STULL_HR_MAX = 5.0, 99.0

# Seuils WBGT. Repris de la norme ISO 7243 pour un travail modere, personne
# acclimatee. Ils encadrent l'effort physique, donc les equipes en poste --
# pour le public assis en tribune, la contrainte est moindre.
SEUILS_WBGT = [
    (33.0, "danger_extreme", "Arret des activites physiques"),
    (30.0, "danger", "Travail lourd a suspendre, rotations courtes"),
    (28.0, "vigilance_haute", "Pauses regulieres, hydratation renforcee"),
    (25.0, "vigilance", "Surveillance des equipes en effort"),
    (0.0, "normal", ""),
]

# Humidex, seuils d'Environnement Canada.
SEUILS_HUMIDEX = [
    (54.0, "danger_extreme", "Coup de chaleur imminent"),
    (45.0, "danger", "Danger, effort a proscrire"),
    (40.0, "inconfort_grand", "Inconfort important"),
    (30.0, "inconfort", "Inconfort marque"),
    (0.0, "normal", ""),
]


def pression_vapeur(temperature_c, humidite_pct):
    """Pression partielle de vapeur d'eau, en hPa.

    Formule de Magnus-Tetens, celle qu'emploie le BoM pour son approximation.
    """
    if temperature_c is None or humidite_pct is None:
        return None
    return (humidite_pct / 100.0) * 6.105 * math.exp(
        17.27 * temperature_c / (237.7 + temperature_c))


def temperature_humide(temperature_c, humidite_pct):
    """Temperature humide (Tw) en degres, d'apres Stull (2011).

    Retourne None hors du domaine de validite plutot qu'une valeur fausse :
    une extrapolation silencieuse serait pire qu'une absence.
    """
    if temperature_c is None or humidite_pct is None:
        return None
    if not (STULL_T_MIN <= temperature_c <= STULL_T_MAX):
        return None
    if not (STULL_HR_MIN <= humidite_pct <= STULL_HR_MAX):
        return None

    t, hr = float(temperature_c), float(humidite_pct)
    return (t * math.atan(0.151977 * math.sqrt(hr + 8.313659))
            + math.atan(t + hr)
            - math.atan(hr - 1.676331)
            + 0.00391838 * (hr ** 1.5) * math.atan(0.023101 * hr)
            - 4.686035)


def humidex(temperature_c, humidite_pct):
    """Humidex d'Environnement Canada : temperature ressentie par l'humidite.

    Non defini sous 20 degres -- l'indice n'a alors pas de sens physique.
    """
    if temperature_c is None or humidite_pct is None or temperature_c < 20.0:
        return None
    e = pression_vapeur(temperature_c, humidite_pct)
    if e is None:
        return None
    return temperature_c + 0.5555 * (e - 10.0)


def wbgt_approche(temperature_c, humidite_pct):
    """WBGT approche par la formule du BoM : 0.567*Ta + 0.393*e + 3.94.

    Ni le rayonnement ni le vent n'entrent dans ce calcul -- ils sont supposes
    moderes. Voir `fiabilite_wbgt` pour savoir quand cette hypothese tombe.
    """
    e = pression_vapeur(temperature_c, humidite_pct)
    if e is None:
        return None
    return 0.567 * temperature_c + 0.393 * e + 3.94


def rayonnement_wm2(rayonnement_jm2, periode_s=3600.0):
    """Flux moyen en W/m2 a partir d'un cumul en J/m2 sur la periode.

    AROME et ARPEGE servent un CUMUL horaire, pas un flux instantane : sans
    cette division on lirait des centaines de milliers de W/m2.
    """
    if rayonnement_jm2 is None or periode_s <= 0:
        return None
    return rayonnement_jm2 / periode_s


def fiabilite_wbgt(heure=None, vent_kmh=None, humidite_pct=None,
                   rayonnement_jm2=None):
    """Dit si l'approximation tient, et pourquoi elle ne tiendrait pas.

    Retourne (niveau, [raisons]). Le niveau vaut "bonne", "surestime",
    "sous_estime" ou "hors_domaine". Toute la valeur de cette fonction est de
    refuser d'afficher un chiffre nu quand le producteur annonce un biais.

    Depuis que les modeles fournissent le rayonnement, la principale hypothese
    de l'approximation -- "rayonnement modere suppose" -- peut enfin etre
    CONFRONTEE au reel plutot que supposee vraie.
    """
    raisons = []
    niveau = "bonne"

    # Le rayonnement mesure prime sur l'heure : c'est lui que l'approximation
    # suppose, et un ciel couvert a midi n'a rien d'un plein soleil.
    flux = rayonnement_wm2(rayonnement_jm2)
    if flux is not None:
        if flux < 120:
            raisons.append(f"rayonnement faible ({flux:.0f} W/m2) : l'approximation surestime")
            niveau = "surestime"
        elif flux > 700:
            raisons.append(f"rayonnement fort ({flux:.0f} W/m2) : "
                           f"la contrainte reelle peut DEPASSER cette valeur")
            niveau = "sous_estime"
    elif heure is not None and (heure < 8 or heure >= 20):
        # Repli sur l'heure quand le rayonnement manque.
        raisons.append("soleil bas ou couche : l'approximation surestime")
        niveau = "surestime"

    # Idem par vent soutenu : le refroidissement convectif n'est pas modelise.
    if vent_kmh is not None and vent_kmh > 20:
        raisons.append(f"vent {vent_kmh:.0f} km/h : refroidissement non pris en compte, surestime")
        niveau = "surestime"

    if humidite_pct is not None and humidite_pct > 90:
        raisons.append("point de rosee eleve : biais haut de la forme lineaire")
        if niveau == "bonne":
            niveau = "surestime"

    if humidite_pct is not None and not (STULL_HR_MIN <= humidite_pct <= STULL_HR_MAX):
        raisons.append("humidite hors du domaine valide")
        niveau = "hors_domaine"

    return niveau, raisons


def _classer(valeur, seuils):
    if valeur is None:
        return None, None
    for borne, code, consigne in seuils:
        if valeur >= borne:
            return code, consigne
    return None, None


def analyser(temperature_c, humidite_pct, vent_kmh=None, heure=None,
             rayonnement_jm2=None, tw_natif=None):
    """Bloc thermique complet pour une echeance.

    `tw_natif` : temperature humide fournie par le modele (AROME l'expose sous
    WET_BULB_TEMPERATURE). Quand elle est la, elle est PREFEREE a
    l'approximation de Stull -- une valeur calculee par le modele vaut mieux
    qu'un ajustement empirique, meme bon. Verifie le 05/08/2026 : les deux
    concordent a 0,22-0,29 degre pres, soit le domaine d'erreur annonce par
    Stull lui-meme.
    """
    tw_calcule = temperature_humide(temperature_c, humidite_pct)
    tw = tw_natif if tw_natif is not None else tw_calcule
    origine_tw = "modele" if tw_natif is not None else "Stull 2011"

    wbgt = wbgt_approche(temperature_c, humidite_pct)
    hx = humidex(temperature_c, humidite_pct)

    niveau_wbgt, consigne_wbgt = _classer(wbgt, SEUILS_WBGT)
    niveau_hx, consigne_hx = _classer(hx, SEUILS_HUMIDEX)
    fiabilite, raisons = fiabilite_wbgt(heure=heure, vent_kmh=vent_kmh,
                                        humidite_pct=humidite_pct,
                                        rayonnement_jm2=rayonnement_jm2)

    ecart_tw = None
    if tw_natif is not None and tw_calcule is not None:
        ecart_tw = round(abs(tw_natif - tw_calcule), 2)

    return {
        "temperature_c": temperature_c,
        "humidite_pct": humidite_pct,
        "vent_kmh": vent_kmh,
        "rayonnement_wm2": (round(rayonnement_wm2(rayonnement_jm2))
                            if rayonnement_jm2 is not None else None),
        "tw_c": round(tw, 1) if tw is not None else None,
        "tw_origine": origine_tw,
        "tw_ecart_stull": ecart_tw,
        "wbgt_c": round(wbgt, 1) if wbgt is not None else None,
        "wbgt_niveau": niveau_wbgt,
        "wbgt_consigne": consigne_wbgt,
        "wbgt_fiabilite": fiabilite,
        "wbgt_reserves": raisons,
        "humidex": round(hx, 1) if hx is not None else None,
        "humidex_niveau": niveau_hx,
        "humidex_consigne": consigne_hx,
        "methode": "BoM (approximation) + " + origine_tw,
    }


# Seuils de CAPE, en J/kg. Repartition classique en meteorologie : sous 300
# l'atmosphere est stable, au-dela de 2500 les orages sont violents. Le CAPE
# dit le POTENTIEL, pas la certitude : il faut un declencheur pour convertir
# cette energie en orage. D'ou la lecture croisee avec la foudre prevue.
SEUILS_CAPE = [
    (2500.0, "tres_fort", "Potentiel d'orages violents"),
    (1500.0, "fort", "Potentiel orageux marque"),
    (800.0, "modere", "Instabilite notable"),
    (300.0, "faible", "Instabilite faible"),
    (0.0, "nul", ""),
]


def risque_orage(cape, foudre=None, grele=None):
    """Lecture croisee du potentiel orageux.

    Sur un evenement en plein air, c'est la foudre qui declenche une mise a
    l'abri, pas la pluie -- et elle arrive avant elle. Le CAPE seul ne suffit
    pas : il mesure une energie disponible, encore faut-il qu'elle se declenche.
    La foudre prevue par le modele est le signal direct ; le CAPE dit la
    severite possible si ca part.
    """
    if cape is None and foudre is None and grele is None:
        return None

    niveau_cape, consigne = _classer(cape, SEUILS_CAPE) if cape is not None else (None, None)
    impacts = foudre or 0.0
    grelons = grele or 0.0

    if impacts > 0.5 or grelons > 0.1:
        niveau = "avere"
        message = "Foudre prevue sur la zone"
        if grelons > 0.1:
            message += " avec grele"
    elif impacts > 0:
        niveau = "possible"
        message = "Quelques impacts prevus"
    elif niveau_cape in ("tres_fort", "fort"):
        niveau = "potentiel"
        message = (consigne or "") + " sans declenchement prevu"
    else:
        niveau = "faible"
        message = ""

    return {
        "niveau": niveau,
        "message": message,
        "cape": cape,
        "cape_niveau": niveau_cape,
        "foudre": foudre,
        "grele": grele,
    }


def pic_contrainte(series):
    """Echeance la plus contraignante d'une serie, et son etendue.

    Une journee ne se resume pas a son maximum : on rend aussi la plage
    horaire ou le WBGT depasse le premier seuil operationnel, car c'est elle
    qui dicte les rotations d'equipe.
    """
    valides = [s for s in series if s.get("wbgt_c") is not None]
    if not valides:
        return None

    pic = max(valides, key=lambda s: s["wbgt_c"])
    seuil = SEUILS_WBGT[-2][0]  # premier palier non "normal"
    au_dessus = [s for s in valides if s["wbgt_c"] >= seuil]

    return {
        "pic": pic,
        "seuil_vigilance": seuil,
        "heures_au_dessus": len(au_dessus),
        "debut": au_dessus[0].get("heure") if au_dessus else None,
        "fin": au_dessus[-1].get("heure") if au_dessus else None,
    }
