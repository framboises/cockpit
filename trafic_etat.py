"""Etat du trafic : agregation par terrain, geofence, verdict global.

Ce module ne connait pas Flask. traffic.py porte un blueprint et importe app :
ni watch_pages ni aucun module de calcul ne peut l'importer sans cycle -- ce
qui obligerait a reecrire les seuils ailleurs, et donc a les faire diverger.
Le calcul vit donc ici, et traffic.py comme watch_pages le consomment.
"""

import logging
import math
import re

logger = logging.getLogger(__name__)

# Geofence du mur trafic (circulation.html:388). Sans lui, on compterait des
# alertes du flux Waze situees hors du perimetre du circuit.
ZONE_CENTER_LAT = 47.93827259819777
ZONE_CENTER_LON = 0.2229518934089374
ZONE_RADIUS_KM = 3.5

# Seuls ces types pilotent l'affichage du mur ; les autres sont ignores.
TYPES_COMPTES = ("ACCIDENT", "JAM", "HAZARD")

# Alias Waze -> type compte, miroir de normType() cote mur
# (circulation.html:486-491). Le mur normalise CHAQUE alerte AVANT de tester
# son appartenance a TYPE_META ; sans le meme alias ici, tout TRAFFIC_JAM et
# WEATHERHAZARD du geofence est compte par le mur et jete par la montre --
# le compte d'alertes de la montre est alors structurellement inferieur a
# celui affiche sur le mur, pour le meme flux Waze.
ALIAS_TYPES = {"TRAFFIC_JAM": "JAM", "WEATHERHAZARD": "HAZARD"}

TAG_RE = re.compile(r'^\s*(#([IOP])(\d+)?#|##)\s*(.+?)\s*$')
SECURITY_RE = re.compile(r'^\s*\*\*\s*(.+?)\s*$')


def parse_route_name(name):
    """(direction, terrain, tag, variant). Deplace depuis traffic.py.

    `variant` porte le numero du tag quand il y en a un : "2" pour
    `#I2# Ouest` comme pour `#P2# A28`, None sinon. Les tags I et O le
    rendaient toujours None avant aout 2026 ; le numero est desormais rendu
    pour eux aussi, ce qui est le SEUL moyen de distinguer deux itineraires
    d'un meme terrain et d'une meme direction -- `#I# Ouest` et
    `#I2# Ouest` coexistent en base et sont deux routes differentes, que la
    liste d'axes de la montre affiche cote a cote.

    AUCUN appelant ne lisait ce 4e element (verifie : traffic.py:301,
    pire_severite_mur, agreger_terrains le nomment tous `_variant`), le
    passage de None au numero ne change donc la sortie d'aucune route
    existante -- en particulier pas celle de
    /trafic/waiting_data_structured, prouvee identique a l'octet pres.
    """
    m = TAG_RE.match(name or "")
    if m:
        io = m.group(2)
        num = m.group(3)
        terrain = m.group(4).strip()
        if io == 'I':
            return "in", terrain, "I", num
        if io == 'O':
            return "out", terrain, "O", num
        if io == 'P':
            return None, terrain, "P", num
        return None, terrain, "neutral", None
    ms = SECURITY_RE.match(name or "")
    if ms:
        return None, ms.group(1).strip(), "security", None
    return None, (name or "").strip(), "free", None


def classify_congestion(current_time, historic_time):
    """(status, severite 0-4). Deplace depuis traffic.py, logique inchangee.

    ATTENTION, defaut connu et NON corrige ici : la branche de repli (pas de
    temps historique) compare des SECONDES a des seuils qui se lisent comme
    des minutes, donc tout trajet de plus d'une minute y sort << bouchon >>.
    Le chemin nominal passe par le ratio, ou les unites s'annulent, et il est
    juste. Corriger ce repli changerait l'affichage du mur : hors perimetre.
    """
    if not historic_time or historic_time <= 0:
        t = current_time or 0
        if t < 15:
            return ("normal", 1)
        if t < 30:
            return ("chargé", 2)
        if t < 60:
            return ("saturé", 3)
        return ("bouchon", 4)

    ratio = (current_time or 0) / float(historic_time)
    if ratio < 0.9:
        return ("plus fluide", 0)
    if ratio < 1.2:
        return ("normal", 1)
    if ratio < 1.6:
        return ("chargé", 2)
    if ratio < 2.5:
        return ("saturé", 3)
    return ("bouchon", 4)


def severite_axe(axe):
    """Severite 0-4 d'un axe, double verrou ratio ET perte de temps absolue.

    Port fidele de classify() -- circulation.html:587-602 (le mur). NE PAS
    FUSIONNER avec classify_congestion, qui n'evalue que le ratio et alimente
    /trafic/waiting_data_structured : la tache 1 a prouve ce payload
    identique a l'octet pres, une fusion le romprait et changerait
    l'affichage d'autres panneaux du cockpit. Les deux coexistent :
    classify_congestion pour les panneaux existants, severite_axe pour la
    montre -- qui doit reproduire le panneau Axes du mur, pas inventer un
    troisieme verdict.

    Le double verrou existe pour ecarter les troncons courts a gros ratio
    mais perte de temps negligeable. Commentaire d'origine, circulation.html
    ligne 594-596 : un troncon court (ex. 20s -> 80s) a un gros ratio mais ne
    coute qu'une minute -- ce n'est pas un bouchon, d'ou le delai plancher.

    `axe` porte les cles produites par agreger_terrains (ou tout dict
    partageant les memes noms de champs) : currentTime, historicTime,
    deltaSeconds. Fidele au mur, `deltaSeconds` est relu s'il existe, sinon
    recalcule en max(0, cur - hist) -- en pratique agreger_terrains ne
    produit `deltaSeconds: None` que lorsque historicTime vaut 0, deja
    intercepte par le repli hist <= 5 ci-dessous, mais le repli reste pour
    ne jamais planter sur un appelant qui omettrait le champ.
    """
    hist = axe.get("historicTime") or 0
    if hist <= 5:
        return 0  # pas de reference exploitable, cf. classify() JS
    cur = axe.get("currentTime") or 0
    ratio = cur / float(hist)
    delay = axe.get("deltaSeconds")
    if delay is None:
        delay = max(0, cur - hist)
    if ratio >= 3.0 and delay >= 600:
        return 4
    if ratio >= 2.2 and delay >= 300:
        return 3
    if ratio >= 1.6 and delay >= 120:
        return 2
    if ratio >= 1.35 and delay >= 60:
        return 1
    return 0


def axes_mur(routes):
    """Les axes du panneau << Axes >> du mur, UN PAR ITINERAIRE Waze.

    Meme ensemble, meme decoupage et meme classement individuel que
    renderAxes() (circulation.html:603) : filtre tag in (I, O, neutral, P),
    AUCUNE agregation, severite calculee route par route.

    NE PAS CONFONDRE AVEC agreger_terrains(), qui somme les troncons d'un
    meme terrain avant de classer, et qui alimente
    /trafic/waiting_data_structured. Les deux repondent a deux questions
    differentes et ne doivent pas fusionner : le mur ne montre nulle part la
    somme des troncons, il montre des itineraires. La montre affichait
    jusqu'ici les terrains agreges et pouvait donc annoncer, pour un meme
    axe, une severite que le mur n'affichait pas -- exactement la divergence
    que severite_axe() avait ete ecrite pour eliminer.

    Chaque axe porte sa polyligne (`line`) : c'est elle qui permet de
    rattacher une alerte Waze a un axe (cf. rattacher_alertes).
    """
    axes = []
    for route in routes or []:
        if not isinstance(route, dict):
            continue
        direction, terrain, tag, variant = parse_route_name(
            route.get("name", ""))
        if tag not in ("I", "O", "neutral", "P"):
            continue
        cur = int(route.get("time", 0) or 0)
        hist = int(route.get("historicTime", 0) or 0)
        delta = max(0, cur - hist) if hist > 0 else None
        # Deux itineraires peuvent partager terrain ET direction (`#I# Ouest`
        # et `#I2# Ouest`) : sans le numero, la montre afficherait deux
        # lignes rigoureusement identiques sauf le temps, illisibles.
        nom = terrain if not variant else (terrain + " " + str(variant))
        axes.append({
            "nom": nom,
            "terrain": terrain,
            "direction": direction,
            "parking": tag == "P",
            "currentTime": cur,
            "historicTime": hist,
            "deltaSeconds": delta,
            "severity": severite_axe({"currentTime": cur,
                                      "historicTime": hist,
                                      "deltaSeconds": delta}),
            "line": route.get("line") or [],
        })
    return axes


# Au-dela, une alerte n'est plus posee sur l'axe mais a cote. Seuil choisi
# au MILIEU D'UN VIDE MESURE, pas au juge : sur les deux relevés Waze
# disponibles en base (prod 31/03/2026, dev post-24H Motos), toute alerte
# qui appartient reellement a un axe surveille tombe entre 0 et 151 m, et
# tout le reste a 677 m ou plus. Aucune alerte, sur aucun des deux relevés,
# ne tombe entre 152 et 676 m. 250 m laisse donc un facteur ~1,7 de marge
# au-dessus du plus lointain vrai rattachement et ~2,7 sous le plus proche
# faux.
DISTANCE_RATTACHEMENT_M = 250.0

# Projection locale : metres par degre de latitude, et par degre de
# longitude A L'EQUATEUR (a corriger par cos(latitude)).
_M_PAR_DEG_LAT = 110540.0
_M_PAR_DEG_LON_EQUATEUR = 111320.0


def distance_point_segment_m(lat, lon, p1, p2):
    """Distance en metres d'un point au segment [p1, p2].

    Projection equirectangulaire centree sur le point lui-meme : sur les
    quelques kilometres du geofence, l'erreur est tres inferieure a la
    precision GPS d'un signalement Waze, et le calcul reste une poignee de
    multiplications -- il tourne des dizaines de milliers de fois par
    construction de bloc.

    La distance est prise au SEGMENT, pas aux sommets : les polylignes Waze
    n'ont un point que tous les ~55 m, et se contenter des sommets
    introduirait jusqu'a ~27 m d'erreur sur un seuil de 250.
    """
    kx = _M_PAR_DEG_LON_EQUATEUR * math.cos(math.radians(lat))
    ky = _M_PAR_DEG_LAT
    bx = (p1["x"] - lon) * kx
    by = (p1["y"] - lat) * ky
    cx = (p2["x"] - lon) * kx
    cy = (p2["y"] - lat) * ky
    dx = cx - bx
    dy = cy - by
    longueur2 = dx * dx + dy * dy
    if longueur2 <= 0:
        return math.hypot(bx, by)
    # Projection du point (a l'origine du repere) sur le segment, bornee aux
    # deux extremites : sans le bornage, un point au-dela du bout du segment
    # serait mesure a la DROITE prolongee, donc bien trop pres.
    t = max(0.0, min(1.0, (-bx * dx - by * dy) / longueur2))
    return math.hypot(bx + t * dx, by + t * dy)


def distance_point_axe_m(lat, lon, axe):
    """Distance en metres d'un point a la polyligne d'un axe, ou None.

    None quand l'axe n'a pas de geometrie exploitable : un axe sans
    polyligne ne peut RIEN se voir rattacher, il ne doit surtout pas
    devenir le plus proche par defaut.
    """
    ligne = (axe or {}).get("line") or []
    meilleure = None
    for i in range(len(ligne) - 1):
        try:
            d = distance_point_segment_m(lat, lon, ligne[i], ligne[i + 1])
        except (TypeError, KeyError, ValueError):
            continue
        if meilleure is None or d < meilleure:
            meilleure = d
    return meilleure


def rattacher_alertes(axes, alertes, seuil_m=DISTANCE_RATTACHEMENT_M):
    """Pose sur chaque axe le compte des alertes qui tombent dessus.

    Chaque axe recoit `alertes`, un dict {type: compte} sur les memes trois
    types que compter_alertes (ACCIDENT, JAM, HAZARD), alias compris. Le
    total par type sur tous les axes est donc INFERIEUR OU EGAL au compte
    geofence du bilan : une alerte du cercle qui ne longe aucun axe
    surveille n'est rattachee nulle part, et c'est voulu -- elle compte
    dans le bilan, elle n'accuse aucun axe.

    Une alerte n'est rattachee qu'a UN SEUL axe, le plus proche. Deux
    itineraires d'un meme terrain se croisent ou se longent (`#I# Ouest` et
    `#I2# Ouest` passent a 10 m l'un de l'autre sur le releve de prod) :
    compter l'alerte sur les deux la ferait apparaitre en double dans la
    liste, et gonflerait un compte deja lisible dans le bilan.
    """
    for axe in axes or []:
        axe["alertes"] = {t: 0 for t in TYPES_COMPTES}
    if not axes:
        return axes

    for alerte in alertes or []:
        if not alerte_en_zone(alerte):
            continue
        type_ = str((alerte or {}).get("type") or "").upper()
        type_ = ALIAS_TYPES.get(type_, type_)
        if type_ not in TYPES_COMPTES:
            continue
        loc = (alerte or {}).get("location") or {}
        try:
            lat = float(loc.get("y"))
            lon = float(loc.get("x"))
        except (TypeError, ValueError):
            continue
        proche = None
        distance = None
        for axe in axes:
            d = distance_point_axe_m(lat, lon, axe)
            if d is None:
                continue
            if distance is None or d < distance:
                distance = d
                proche = axe
        if proche is not None and distance is not None and distance <= seuil_m:
            proche["alertes"][type_] += 1
    return axes


def pire_severite_mur(routes):
    """Pire severite_axe() sur TOUTES les routes que le panneau << Axes >>
    du mur retient -- routes brutes, AUCUNE agregation par terrain.

    Le filtre du mur (circulation.html:793) est
    `r.category === "pkg_aa" || r.tag === "P"`. `category` vaut "pkg_aa"
    pour les tags I/O/neutral/P (cf. traffic.py:get_all_routes) : le
    `|| tag === "P"` du mur est donc redondant, P est deja dans pkg_aa.
    Reproduit ici directement sur `parse_route_name` -- watch_pages ne peut
    pas appeler la route HTTP /trafic/all_routes (process Flask interne).

    C'EST DELIBEREMENT PLUS LARGE que agreger_terrains(), qui ne retient
    que les prefixes ##/#I/#O et exclut donc les axes #P (parkings).
    Cette fonction existe precisement pour que le VERDICT GLOBAL de la
    montre voie ce que agreger_terrains() ne montre pas : un parking tres
    charge doit faire monter le verdict, meme s'il n'apparait dans aucun
    terrain affiche en page 3 (ce choix d'affichage-la reste assume, cf.
    watch_pages.build_trafic). Un manque silencieux sur un outil de
    supervision -- une page qui parait calme alors qu'elle ne sait pas --
    est la faute que ce projet cherche a eliminer partout ailleurs ; un
    verdict eleve sans terrain qui l'explique renvoie au moins vers le mur
    ou le cockpit, il n'est pas muet.

    Contrairement a agreger_terrains, chaque route est classee
    INDIVIDUELLEMENT (pas de somme des temps de plusieurs troncons avant
    classement) : c'est exactement ce que fait classify() cote mur.

    Delegue desormais a axes_mur() pour que l'ensemble d'axes du VERDICT et
    celui de la LISTE affichee soient litteralement le meme code. Tant que
    les deux etaient ecrits separement, ils pouvaient diverger en silence --
    et un verdict calcule sur un ensemble plus large que la liste est
    precisement ce qui produisait un << CRITIQUE >> sans cause visible.
    """
    return max([axe["severity"] for axe in axes_mur(routes)] or [0])


def agreger_terrains(routes):
    """Agrege les itineraires surveilles par (terrain, direction).

    Deplace depuis /trafic/waiting_data_structured. `currentTime` est en
    SECONDES (verifie sur waze_trafic : time=208 pour un trajet Heronniere).
    """
    agg = {}
    for route in routes or []:
        if not isinstance(route, dict):
            continue
        nom = route.get("name", "")
        if not (nom.startswith("##") or nom.startswith("#I")
                or nom.startswith("#O")):
            continue
        direction, terrain, _tag, _variant = parse_route_name(nom)
        cur = int(route.get("time", 0) or 0)
        hist = int(route.get("historicTime", 0) or 0)
        cle = (terrain, direction)
        if cle not in agg:
            agg[cle] = {"terrain": terrain, "direction": direction,
                        "sumCurrent": 0, "sumHistoric": 0, "routesCount": 0}
        agg[cle]["sumCurrent"] += max(0, cur)
        agg[cle]["sumHistoric"] += max(0, hist)
        agg[cle]["routesCount"] += 1

    terrains = []
    for rec in agg.values():
        sum_cur = rec["sumCurrent"]
        sum_hist = rec["sumHistoric"]
        ratio = (sum_cur / sum_hist) if sum_hist > 0 else None
        status, severite = classify_congestion(sum_cur, sum_hist)
        terrains.append({
            "terrain": rec["terrain"],
            "direction": rec["direction"],
            "currentTime": sum_cur,
            "historicTime": sum_hist,
            "ratio": round(ratio, 2) if ratio is not None else None,
            "deltaSeconds": max(0, sum_cur - sum_hist) if sum_hist > 0 else None,
            "deltaPercent": round((ratio - 1) * 100) if ratio is not None else None,
            "status": status,
            "severity": severite,
            "routesCount": rec["routesCount"],
        })
    terrains.sort(key=lambda t: (-1 if t["ratio"] is None else t["ratio"]),
                  reverse=True)
    return terrains


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def alerte_en_zone(alerte):
    """L'alerte tombe-t-elle dans le cercle de 3,5 km autour du circuit ?

    Une alerte sans coordonnees est HORS zone : ne jamais compter ce qu'on ne
    sait pas placer, sinon le verdict monte sans qu'on puisse dire pourquoi.
    """
    loc = (alerte or {}).get("location") or {}
    lat = loc.get("y")
    lon = loc.get("x")
    if lat is None or lon is None:
        return False
    try:
        return haversine_km(ZONE_CENTER_LAT, ZONE_CENTER_LON,
                            float(lat), float(lon)) <= ZONE_RADIUS_KM
    except (TypeError, ValueError):
        return False


def compter_alertes(alertes):
    """Comptes par type, geofences. Rend aussi le total des types comptes."""
    comptes = {t: 0 for t in TYPES_COMPTES}
    for alerte in alertes or []:
        if not alerte_en_zone(alerte):
            continue
        type_ = str((alerte or {}).get("type") or "").upper()
        type_ = ALIAS_TYPES.get(type_, type_)
        if type_ in comptes:
            comptes[type_] += 1
    comptes["total"] = sum(comptes[t] for t in TYPES_COMPTES)
    return comptes


def verdict_global(comptes, pire_severite):
    """Verdict 0-3 du mur trafic. Regle de circulation.html:728, a l'identique.

    LES BOUCHONS ET DANGERS WAZE NE PILOTENT PAS LE VERDICT. Seuls comptent
    les axes surveilles et les accidents en zone : un bouchon Waze peut se
    trouver n'importe ou dans le cercle, hors des itineraires suivis, et
    faisait diverger le verdict du panneau << Axes >>. Toute tentative de les
    reintroduire ici ferait diverger le poignet du mur.
    """
    accidents = (comptes or {}).get("ACCIDENT", 0)
    pire = pire_severite or 0
    if accidents > 0 or pire >= 4:
        return 3
    if pire == 3:
        return 2
    if pire == 2:
        return 1
    return 0


# Le collecteur externe (waze_collector.py, tache planifiee toutes les
# 2 min) REECRIT un document unique par collection, `_id: "latest"`. C'est
# lui qu'il faut cibler -- exactement comme le fait le mur
# (traffic.py:166), et non le plus recent par tri sur `fetched_at`.
#
# Les deux ramenent le meme document tant que la collection n'en contient
# qu'un, ce qui est le cas aujourd'hui. Mais un tri sans index depend de ce
# que la collection contient, la ou un acces par cle primaire dit ce qu'on
# veut : LE releve courant. Si un jour un document d'archive atterrissait
# la, le tri pourrait le ramener et la montre daterait ses chiffres sur lui
# -- une divergence silencieuse avec le mur, qui n'y serait pas expose.
#
# ⚠️ NE PAS CONFONDRE avec `waze_trafic_history`, ou le collecteur depose un
# SNAPSHOT toutes les 16-18 min : cette collection-la sert aux comparaisons
# dans le temps, pas a l'etat courant. Dater la montre dessus lui ferait
# annoncer un quart d'heure de retard en permanence.
_ID_COURANT = "latest"


def lire_routes(db):
    """Itineraires du releve Waze courant."""
    doc = db["waze_trafic"].find_one({"_id": _ID_COURANT}) or {}
    return ((doc.get("data") or {}).get("routes")) or []


def lire_alertes(db):
    """Alertes Waze du releve courant, non filtrees."""
    doc = db["waze_alerts"].find_one({"_id": _ID_COURANT}) or {}
    return doc.get("data") or []


def fraicheur(db):
    """Horodatage du releve Waze courant (routes), naif UTC, ou None."""
    doc = db["waze_trafic"].find_one({"_id": _ID_COURANT}) or {}
    return doc.get("fetched_at")


def fraicheur_alertes(db):
    """Horodatage du dernier releve Waze (alertes), naif UTC, ou None.

    `ac`/`z` (watch_pages.build_trafic) viennent de waze_alerts, une
    collection DIFFERENTE de waze_trafic -- fraicheur() ne la couvre pas.
    Sans cette fonction, un collecteur d'alertes arrete rendait `ac`/`z` a
    0 avec la date, parfaitement fraiche, du flux routes : une page calme
    parce qu'elle a cesse de savoir. traffic.py refuse deja un document
    Mongo perime pour le mur (MONGO_MAX_AGE_SECONDS) et retombe sur un
    appel Waze direct -- la montre n'a pas ce repli, elle doit donc au
    moins savoir dater ce qu'elle affiche.
    """
    doc = db["waze_alerts"].find_one({"_id": _ID_COURANT}) or {}
    return doc.get("fetched_at")
