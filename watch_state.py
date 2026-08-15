"""Calcul de l'etat servi a la montre.

Fonctions pures et lectures Mongo, `db` toujours passe en argument. Aucun
import Flask : ce module se teste sans application.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Seuils WBGT en degres, replies sur l'echelle 0-3 de la montre. Ils viennent
# de SEUILS_WBGT (ISO 7243) dans meteo_thermique.py, dont le palier
# danger_extreme (33) est absorbe par le niveau 3 : au-dela de "suspendre le
# travail lourd", un cran de plus ne change aucune decision au poignet.
WBGT_DEFAULT_LEVELS = (25.0, 28.0, 30.0)

# Ce qui tient la reponse sous 2 Ko.
MAX_ALERTS = 5
LABEL_MAX = 24


def wbgt_level(wbgt_c, thresholds=None):
    """Replie un WBGT en degres sur l'echelle 0-3 de la montre."""
    if wbgt_c is None:
        return 0
    seuils = thresholds or WBGT_DEFAULT_LEVELS
    niveau = 0
    for seuil in seuils:
        if wbgt_c >= seuil:
            niveau += 1
    return min(niveau, 3)


# Au-dela de cet ecart, les deux releves ne decrivent plus le meme regime :
# une interruption du flux Skidata donnerait un debit proche de zero sur une
# foule qui entre, et rien dans le payload ne le signalerait.
RATE_MAX_GAP_S = 3600


def entry_rate(entries_now, ts_now, entries_before, ts_before):
    """Debit d'entrees en personnes/heure entre deux releves du compteur."""
    if entries_now is None or ts_now is None:
        return None
    if entries_before is None or ts_before is None:
        return None
    delta_s = (ts_now - ts_before).total_seconds()
    if delta_s <= 0:
        return None
    if delta_s > RATE_MAX_GAP_S:
        # Les deux releves ne decrivent plus le meme regime (interruption du
        # flux Skidata, ou premier releve d'une edition) : un debit calcule
        # sur un ecart pareil serait proche de zero quelle que soit la foule
        # reelle en train d'entrer, et rien ne le distinguerait d'un debit
        # nul legitime.
        return None
    delta_e = entries_now - entries_before
    if delta_e < 0:
        # Remise a zero du compteur : mieux vaut rien qu'un debit absurde.
        return None
    return int(round(delta_e * 3600.0 / delta_s))


def select_alerts(active, config_alerts):
    """Filtre, note et tronque les alertes actives pour la montre.

    `cockpit_active_alerts` ne porte aucun champ de severite et le `priority`
    de `cockpit_alert_definitions` est un ordre d'affichage, pas une gravite
    (opening = 1, field_sos = 99). Le niveau vient donc exclusivement de la
    configuration, qui sert du meme coup de filtre : un slug absent ne part
    pas a la montre.
    """
    par_slug = {}
    for regle in config_alerts or []:
        slug = regle.get("slug")
        if slug:
            par_slug[slug] = regle

    sortie = []
    for doc in active or []:
        regle = par_slug.get(doc.get("definition_slug"))
        if regle is None:
            continue
        libelle = regle.get("label") or doc.get("title") or ""
        sortie.append({
            "l": int(regle.get("level", 1)),
            "m": libelle[:LABEL_MAX],
        })

    sortie.sort(key=lambda a: -a["l"])
    return sortie[:MAX_ALERTS]


def event_label(short, year):
    """Libelle court de l'evenement rapporte, du genre '24HM 26'."""
    if not short or year is None:
        return None
    return "%s %02d" % (short, int(year) % 100)


def _safe_int(value):
    """Convertit en entier, ou None si la valeur n'est pas exploitable.

    Les annees arrivent tantot en int, tantot en chaine selon l'emetteur, et
    parfois en valeur libre saisie a la main. Un seul point de conversion evite
    que les deux chemins de resolve_event ne divergent.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_event(config, counter_doc):
    """Retourne (event, year) : epingle si demande, sinon derive du compteur.

    En mode auto, on lit le `requested_event` / `year` du dernier releve : les
    chiffres et les alertes viennent alors forcement du meme evenement. Le doc
    global du live-controle n'est pas une source : il ne porte aucune annee et
    derive en pratique.
    """
    config = config or {}
    if config.get("event_mode") == "pinned":
        return config.get("event"), _safe_int(config.get("year"))

    if not counter_doc:
        return None, None
    return counter_doc.get("requested_event"), _safe_int(counter_doc.get("year"))


# Identifiant du document de configuration globale du live-controle.
HSH_GLOBAL_ID = "___GLOBAL___"

# Recul utilise pour calculer le debit. Assez long pour lisser le bruit d'un
# releve, assez court pour rester une photographie de l'instant.
RATE_WINDOW = timedelta(minutes=15)

# Au-dela de cette anciennete, un releve ne decrit plus un evenement en cours.
# Le collecteur tourne en boucle continue : un principal muet depuis six heures
# est un collecteur arrete, pas une foule immobile.
COUNTER_MAX_AGE = timedelta(hours=6)


def read_config(db):
    """Configuration montre, avec ses defauts. Ne renvoie jamais None."""
    doc = db["watch_config"].find_one({"_id": "watch"}) or {}
    return {
        "event_mode": doc.get("event_mode") or "auto",
        "event": doc.get("event"),
        "year": doc.get("year"),
        "alerts": doc.get("alerts") or [],
        "wbgt_levels": doc.get("wbgt_levels") or list(WBGT_DEFAULT_LEVELS),
    }


def read_principal_id(db):
    """Identifiant du compteur principal, choisi dans la page live-controle."""
    doc = db["data_access"].find_one({"_id": HSH_GLOBAL_ID}) or {}
    principal = doc.get("compteur_principal_id")
    return str(principal) if principal else None


def read_live_active(db):
    """Le collecteur live-controle est-il arme ?

    Meme lecture et meme defaut que /api/live-controle/status (app.py:7907) :
    champ absent = inactif. C'est ce drapeau qui fait disparaitre le widget
    compteurs du cockpit (controle_access.js:277) ; sans lui, la montre
    affichait des chiffres que le cockpit, lui, avait cesse de montrer.
    """
    doc = db["data_access"].find_one({"_id": HSH_GLOBAL_ID}) or {}
    return bool(doc.get("live_controle_actif", False))


def read_counter(db, location_id, max_age=None, now_utc=None):
    """Dernier releve du compteur, borne en fraicheur si `max_age` est donne.

    Comme le widget compteurs du cockpit, on interroge le seul
    requested_location_id : le releve porte lui-meme son evenement et son
    annee, la donnee s'auto-identifie.

    Sans borne, cette requete remonte le dernier releve *connu*, quel que soit
    son age et son evenement. Apres l'archivage d'une edition, ce qui subsiste
    dans data_access est un residu d'une edition anterieure : la montre a
    affiche pendant des semaines les entrees du 23 avril sous le libelle
    << 24HM 26 >>. Aucun releve dans la fenetre signifie qu'aucun evenement
    n'est en cours -- pas qu'il faut se rabattre sur le dernier connu.
    """
    if not location_id:
        return None
    filtre = {"requested_location_id": str(location_id)}
    if max_age is not None:
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        # `timestamp` est naif mais porte de l'UTC (pymongo, client non
        # tz_aware). BSON normalise les deux cotes en UTC sur le fil, la
        # comparaison est donc juste malgre la difference de forme.
        filtre["timestamp"] = {"$gte": now_utc - max_age}
    return db["data_access"].find_one(filtre, sort=[("timestamp", -1)])


def read_counter_before(db, location_id, moment):
    """Releve le plus recent anterieur a `moment`, pour le calcul du debit."""
    if not location_id:
        return None
    return db["data_access"].find_one(
        {"requested_location_id": str(location_id),
         "timestamp": {"$lte": moment}},
        sort=[("timestamp", -1)],
    )


def read_event_short(db, event):
    """Sigle court de l'evenement (24H MOTOS -> 24HM)."""
    if not event:
        return None
    doc = db["evenement"].find_one({"nom": event}) or {}
    return doc.get("short")


def read_active_alerts(db, event, year, now_utc):
    """Alertes actives non expirees de l'evenement retenu.

    `now_utc` est CONSCIENT du fuseau : alert_engine ecrit expiresAt en UTC
    conscient et pymongo le relit naif UTC. Le comparer a une heure locale
    ecartait toute alerte expirant a moins de deux heures, c'est-a-dire
    presque toutes.
    """
    if not event or year is None:
        return []
    # `year` est stocke tantot en chaine tantot en entier selon les emetteurs.
    annees = [year, str(year)]
    return list(db["cockpit_active_alerts"].find({
        "event": event,
        "year": {"$in": annees},
        "expiresAt": {"$gt": now_utc},
    }))


def read_weather_now(db, now):
    """WBGT du creneau horaire courant, en degres.

    Meme source que le mur meteo : le document du jour dans meteo_previsions,
    enrichi par meteo_thermique.analyser().
    """
    doc = db["meteo_previsions"].find_one({"Date": now.strftime("%Y-%m-%d")})
    if not doc:
        return None, None

    heure_courante = now.strftime("%H:00")
    entree = None
    for candidat in doc.get("Heures") or []:
        if candidat.get("Heure") == heure_courante:
            entree = candidat
            break
    if entree is None:
        return None, None

    # Les cles sont accentuees en base, avec repli non accentue : 0 est une
    # valeur legitime, on ne peut pas ecrire `.get(k) or defaut`.
    temperature = entree.get("Température (°C)")
    if temperature is None:
        temperature = entree.get("Temperature (C)")
    humidite = entree.get("Humidité (%)")
    if humidite is None:
        humidite = entree.get("Humidite (%)")
    if temperature is None or humidite is None:
        return None, None

    import meteo_thermique

    bloc = meteo_thermique.analyser(
        temperature, humidite,
        vent_kmh=entree.get("Vent moyen (km/h)"),
        heure=int(str(entree.get("Heure", "0")).split(":")[0]),
    )
    return bloc.get("wbgt_c"), bloc.get("wbgt_niveau")


def build_state(db, now, now_utc=None, peaks=None, pages=None):
    """Assemble le payload servi a la montre.

    `peaks` est le module watch_peaks, `pages` le module watch_pages : tous
    deux INJECTES et non importes. watch_peaks comme watch_pages importent
    deja watch_state (pour ses libelles et pour read_principal_id), les
    importer en retour ferait un cycle. L'injection garde aussi ce module
    testable sans eux -- `pages=None` doit laisser les quatre blocs a None.

    DEUX HORLOGES, deux conventions, ne pas les confondre :
      `now`     naif, heure locale Paris. C'est la convention des creneaux
                horaires de meteo_previsions.
      `now_utc` conscient, UTC. C'est la convention des documents ecrits par
                alert_engine et live_controle, que pymongo relit naifs UTC.

    Les avoir confondus a produit deux pannes silencieuses : aucune alerte ne
    parvenait a la montre, et toute donnee s'affichait perimee de deux heures.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    config = read_config(db)

    # DEUX GARDES, aucune ne suffit seule.
    #
    # Le drapeau attrape l'arret propre : quand l'exploitation desactive le
    # live-controle en fin d'edition, les releves cessent d'arriver mais les
    # derniers restent en base. Le drapeau le dit dans la seconde ; la seule
    # fraicheur mettrait six heures a s'en apercevoir.
    #
    # La fraicheur attrape le collecteur plante : la, le drapeau dit encore
    # << actif >> et mentirait indefiniment. C'est exactement ce que la
    # pastille de sante du cockpit signale en passant en << warning >>.
    location_id = read_principal_id(db)
    actif = read_live_active(db)
    courant = None
    if actif:
        courant = read_counter(db, location_id,
                               max_age=COUNTER_MAX_AGE, now_utc=now_utc)
    mode = "live" if courant else "past"
    entrees = _safe_int(courant.get("entries")) if courant else None

    # `mr` distingue deux situations que `m: past` a lui seul confondait :
    # un live-controle arrete (normal, 350 jours par an) et un collecteur
    # plante alors que le drapeau le dit encore en marche (incident a
    # traiter -- c'est exactement ce que la pastille de sante du cockpit
    # signale en passant en << warning >>).
    motif = None
    if mode == "past":
        motif = "inactif" if not actif else "sans_releve"

    # Les presents, pas le cumul d'entrees : c'est la grandeur qui gouverne
    # une decision d'exploitation. Jamais hors mode live -- un chiffre de
    # presents perime est indiscernable d'un chiffre juste, et c'est
    # precisement lui qu'on regarde pour decider.
    presents = None
    if courant:
        presents = _safe_int(courant.get("current"))
        if presents is None:
            sorties = _safe_int(courant.get("exits"))
            if entrees is not None and sorties is not None:
                presents = entrees - sorties

    horodatage = courant.get("timestamp") if courant else None

    debit = None
    if courant and horodatage is not None:
        anterieur = read_counter_before(db, location_id, horodatage - RATE_WINDOW)
        if anterieur:
            avant = _safe_int(anterieur.get("entries"))
            debit = entry_rate(entrees, horodatage,
                               avant, anterieur.get("timestamp"))

    event, year = resolve_event(config, courant)

    # Hors evenement et en mode auto, rien n'identifie plus d'edition : le
    # compteur ne repond plus et c'est justement ce qu'on voulait. On rapporte
    # alors la derniere edition consultable, sinon l'ecran principal serait
    # vide les 350 jours de l'annee ou rien ne roule. Un evenement epingle
    # n'est jamais remplace : l'epinglage est un choix explicite.
    pic = None
    pic_ts = None

    if peaks is not None and event is None:
        # On retient la premiere edition dont le pic est exploitable, pas
        # simplement la plus recente : nommer une edition sans savoir la
        # chiffrer donne un ecran qui affiche un titre et des tirets. Mieux
        # vaut une edition un peu plus ancienne mais renseignee -- c'est la
        # meme regle que /editions, qui omet ce qu'il ne sait pas mesurer.
        for edition in peaks.list_editions(db, now_utc=now_utc):
            candidat, instant = peaks.cached_peak(
                db, edition["event"], edition["year"], now_utc=now_utc)
            if candidat is None:
                continue
            event = edition["event"]
            year = edition["year"]
            pic, pic_ts = candidat, instant
            break

    wbgt, _ = read_weather_now(db, now)
    actives = read_active_alerts(db, event, year, now_utc)

    # Pic de presents de l'edition rapportee. En cours, il monte encore ; une
    # fois l'edition close, il est definitif. Un seul couple de champs dans
    # les deux cas, pour que la montre n'ait pas deux facons de lire un pic.
    if pic is None and peaks is not None and event and year is not None:
        pic, pic_ts = peaks.cached_peak(db, event, year, now_utc=now_utc)

    # Les quatre blocs des pages operationnelles. Chaque bloc dans son
    # propre try : une source qui tombe met SON bloc a null, jamais un 500
    # ni un payload perdu -- le serveur doit rester capable de donner le
    # WBGT quand Waze ne repond plus. En mode past, main courante et
    # frequentation n'ont pas d'objet hors evenement : quatre lectures
    # Mongo economisees a chaque cycle, 350 jours par an. Trafic et meteo,
    # eux, restent vivants toute l'annee.
    blocs = {"mc": None, "tr": None, "me": None, "st": None}
    if pages is not None:
        constructeurs = (
            ("mc", lambda: pages.build_main_courante(db, event, year, now_utc)
                   if mode == "live" else None),
            ("tr", lambda: pages.build_trafic(db, now_utc)),
            ("me", lambda: pages.build_meteo(db, now)),
            ("st", lambda: pages.build_frequentation(db, event, year, now)
                   if mode == "live" else None),
        )
        for cle, construire in constructeurs:
            try:
                blocs[cle] = construire()
            except Exception as exc:
                logger.warning("watch_state : bloc %s indisponible (%s)", cle, exc)

    payload = {
        # `horodatage` est naif mais contient de l'UTC (pymongo, client non
        # tz_aware). .timestamp() l'interpreterait en heure locale du
        # serveur et decalerait l'epoch de deux heures en ete.
        "t": int(horodatage.replace(tzinfo=timezone.utc).timestamp())
             if horodatage else None,
        "n": event_label(read_event_short(db, event), year),
        # `m` dit si les chiffres decrivent un evenement en cours. La montre
        # s'en sert pour ne pas crier << perime >> sur une edition close : en
        # `past`, `t` est null par construction et l'age n'a plus de sens.
        "m": mode,
        # Distingue arret volontaire (`inactif`) et collecteur en panne
        # (`sans_releve`) -- confondre les deux perdrait l'information la
        # plus utile. `None` en mode live.
        "mr": motif,
        # Les presents, jamais hors mode live (voir plus haut).
        "p": presents,
        "e": entrees,
        "er": debit,
        "pk": pic,
        # Meme convention que `t` : epoch UTC. La montre le rend en heure
        # locale, ce qui donne Paris.
        "pkt": int(pic_ts.timestamp()) if pic_ts else None,
        "w": round(wbgt, 1) if wbgt is not None else None,
        "wl": wbgt_level(wbgt, tuple(config["wbgt_levels"])),
        "al": select_alerts(actives, config["alerts"]),
    }
    payload.update(blocs)
    return payload
