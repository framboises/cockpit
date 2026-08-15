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
    """(direction, terrain, tag, variant). Deplace depuis traffic.py."""
    m = TAG_RE.match(name or "")
    if m:
        io = m.group(2)
        num = m.group(3)
        terrain = m.group(4).strip()
        if io == 'I':
            return "in", terrain, "I", None
        if io == 'O':
            return "out", terrain, "O", None
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
    """
    pire = 0
    for route in routes or []:
        if not isinstance(route, dict):
            continue
        _direction, _terrain, tag, _variant = parse_route_name(
            route.get("name", ""))
        if tag not in ("I", "O", "neutral", "P"):
            continue
        cur = int(route.get("time", 0) or 0)
        hist = int(route.get("historicTime", 0) or 0)
        delta = max(0, cur - hist) if hist > 0 else None
        sev = severite_axe({"currentTime": cur, "historicTime": hist,
                            "deltaSeconds": delta})
        if sev > pire:
            pire = sev
    return pire


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


def lire_routes(db):
    """Itineraires du dernier releve Waze en base."""
    doc = db["waze_trafic"].find_one(sort=[("fetched_at", -1)]) or {}
    return ((doc.get("data") or {}).get("routes")) or []


def lire_alertes(db):
    """Alertes Waze du dernier releve, non filtrees."""
    doc = db["waze_alerts"].find_one(sort=[("fetched_at", -1)]) or {}
    return doc.get("data") or []


def fraicheur(db):
    """Horodatage du dernier releve Waze (routes), naif UTC, ou None."""
    doc = db["waze_trafic"].find_one(sort=[("fetched_at", -1)]) or {}
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
    doc = db["waze_alerts"].find_one(sort=[("fetched_at", -1)]) or {}
    return doc.get("fetched_at")
