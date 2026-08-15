"""Pic de presents en enceinte generale, edition par edition.

Fonctions pures et lectures Mongo, `db` toujours passe en argument. Aucun
import Flask : ce module se teste sans application, comme watch_state.py et
scan_frequentation.py.

CE QUE CE MODULE REFUSE DE FAIRE, et pourquoi c'est l'essentiel :

Il ne deduit JAMAIS l'appartenance d'un releve a une edition depuis le nom de
sa collection d'archive ni depuis son champ `requested_event`. Les deux
mentent, chacun a sa facon :

  - le suffixe d'annee d'une collection vient de `datetime.now().year` au
    moment ou un humain a clique sur << Archiver >>, pas de l'annee de
    l'edition. `hsh_archive_compteurs_24H_MOTOS_2026` contient 89 % de
    releves de 2025.
  - `requested_event` porte le libelle qui trainait dans le document global
    du live-controle au moment de la collecte. Le collecteur ayant tourne en
    juin 2025 avec le libelle reste sur << GPF >>, les 31 041 releves du
    24H AUTOS 2025 -- dont le pic a 145 571 -- sont ranges sous GPF.

La seule cle fiable est le `timestamp`. Resoudre une edition, c'est donc
definir sa fenetre de dates puis balayer TOUTES les sources de releves sur
cette fenetre, sans jamais regarder ni le nom de collection ni l'evenement
estampille.
"""

import logging
from datetime import datetime, timedelta, timezone

import pcorg_summary as _ps
import watch_state as _ws

logger = logging.getLogger(__name__)

# Location de l'ENCEINTE GENERALE, celle dont `current` est le pic de presents.
DEFAULT_LOCATION_ID = "628"

# Fenetre de balayage autour de la date de course. Large avant, courte apres :
# la montee en charge commence des l'ouverture des campings une semaine plus
# tot, la descente est bouclee en deux jours.
WINDOW_BEFORE = timedelta(days=10)
WINDOW_AFTER = timedelta(days=3)

ARCHIVE_PREFIX = "hsh_archive_compteurs_"

# Cache persistant : le pic d'une edition close ne bouge plus jamais.
CACHE_COLLECTION = "watch_peaks"

# Seule l'edition en cours merite d'etre recalculee, et pas a chaque requete.
LIVE_CACHE_TTL = timedelta(seconds=60)

MAX_EDITIONS = 20


def _as_utc(moment):
    """Aligne un datetime sur la convention aware-UTC.

    pymongo, client non tz_aware, rend des datetimes naifs qui contiennent de
    l'UTC. Les traiter comme de l'heure locale decalerait tout de deux heures
    en ete -- la panne exacte deja rencontree sur le champ `t` du payload.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def event_aliases(db, event):
    """Libelles sous lesquels un evenement peut etre stocke : [nom, sigle].

    Meme geste que app.py:_event_hist_aliases. `historique_controle` est
    incoherente : LE MANS CLASSIC y vit sous << LMC >>, SUPERBIKE sous
    << SBK >>, alors que data_access porte le nom long. Une recherche sur le
    seul nom long n'y trouve rien, en silence.
    """
    if not event:
        return []
    alias = [event]
    short = _ws.read_event_short(db, event)
    if short and short not in alias:
        alias.append(short)
    return alias


def resolve_race_dt(db, event, year):
    """Date de course d'une edition, en UTC, ou None.

    Repose sur pcorg_summary._load_race_dt (quatre niveaux de repli, et il
    sait que globalHoraires.race est la seule source stockee en UTC avec un
    << Z >> final), mais ajoute DEUX choses qui lui manquent :

    1. UNE GARDE SUR L'ANNEE. `parametrages` contient des dates de course qui
       ne sont pas celles de l'edition sous laquelle elles sont rangees : le
       document LE MANS CLASSIC `year: 2025` porte une course au 05/07/2026.
       Sans cette garde, la fenetre de 2025 serait celle de 2026 et le pic de
       2026 (52 409) serait rendu sous l'etiquette 2025 -- une confusion
       silencieuse, du meme genre que celles que ce module combat par
       ailleurs. Une date qui ne tombe pas dans son annee est ecartee, pas
       corrigee : on ne devine pas a la place de la saisie.

    2. LES ALIAS. Les niveaux 3 et 4 de _load_race_dt interrogent
       historique_controle sur le seul nom long, alors que la collection y
       range LMC et SBK sous leur sigle. Ces replis ne se declenchaient donc
       jamais pour ces evenements. Avec les alias, LE MANS CLASSIC 2025
       retombe sur historique_controle{portes} et sa course au 05/07/2025 --
       la bonne.
    """
    annee = _ws._safe_int(year)
    if not event or annee is None:
        return None

    officielle = _ps._load_race_dt(db, event, annee)
    if officielle is not None and officielle.year == annee:
        return officielle

    if officielle is not None:
        logger.warning(
            "watch_peaks : date de course %s ecartee pour %s %s "
            "(annee incoherente), repli sur historique_controle",
            officielle.date().isoformat(), event, annee)

    alias = event_aliases(db, event)
    if not alias:
        return None
    for hc_type in ("portes", "frequentation"):
        for valeur_annee in (annee, str(annee)):
            doc = db["historique_controle"].find_one(
                {"type": hc_type, "event": {"$in": alias},
                 "year": valeur_annee},
                {"race": 1},
            )
            candidate = _ps._parse_race_dt((doc or {}).get("race"))
            if candidate is not None and candidate.year == annee:
                return candidate
    return None


def edition_window(db, event, year):
    """Fenetre UTC d'une edition, derivee de la date de course.

    Retourne (debut, fin) conscients UTC, ou (None, None) si la date de
    course n'est pas resoluble -- auquel cas l'edition n'est pas consultable.
    Mieux vaut ne rien rendre qu'une fenetre inventee : elle attraperait les
    releves de l'edition voisine.
    """
    course = resolve_race_dt(db, event, year)
    if course is None:
        return None, None
    return course - WINDOW_BEFORE, course + WINDOW_AFTER


def snapshot_collections(db):
    """Toutes les sources de releves : le direct et chaque archive.

    On les balaye TOUTES sans exception. Se fier au nom pour choisir quelle
    archive contient une edition donnee est precisement le piege : le
    24H AUTOS 2025 dort dans la collection nommee GPF.
    """
    noms = ["data_access"]
    try:
        for nom in sorted(db.list_collection_names()):
            if nom.startswith(ARCHIVE_PREFIX):
                noms.append(nom)
    except Exception as exc:
        logger.warning("watch_peaks : inventaire des collections impossible "
                       "(%s), seul le direct sera balaye", exc)
    return noms


_INDEXES_FAITS = set()


def ensure_indexes(db):
    """Index (requested_location_id, timestamp) sur chaque source, en paresseux.

    Sans lui, un pic est un COLLSCAN sur 108 000 documents. Tolerable derriere
    un cache, inacceptable a chaque requete montre.
    """
    for nom in snapshot_collections(db):
        if nom in _INDEXES_FAITS:
            continue
        try:
            db[nom].create_index([("requested_location_id", 1), ("timestamp", 1)],
                                 name="watch_peaks_loc_ts", background=True)
            _INDEXES_FAITS.add(nom)
        except Exception as exc:
            # Une base en lecture seule ou un index deja pris sous un autre nom
            # ne doit pas priver la montre de son pic.
            logger.warning("watch_peaks : index sur %s impossible (%s)", nom, exc)
            _INDEXES_FAITS.add(nom)


def _scan_peak(db, nom_collection, debut, fin, location_id):
    """Max de `current` sur une collection, dans la fenetre. (max, ts) ou (None, None)."""
    filtre = {
        "requested_location_id": str(location_id),
        "timestamp": {"$gte": debut, "$lt": fin},
    }
    maxi = None
    maxi_ts = None
    try:
        curseur = db[nom_collection].find(filtre, {"current": 1, "timestamp": 1})
        for releve in curseur:
            brut = releve.get("current")
            # `current` est une chaine, et vaut parfois "" ou "N/A". Un cast
            # non protege ferait tomber tout le balayage sur un seul releve
            # bancal ; on ignore l'inconvertible et on continue.
            try:
                valeur = int(brut) if brut not in (None, "") else None
            except (TypeError, ValueError):
                continue
            if valeur is None:
                continue
            if maxi is None or valeur > maxi:
                maxi = valeur
                maxi_ts = releve.get("timestamp")
    except Exception as exc:
        logger.warning("watch_peaks : lecture de %s echouee (%s)", nom_collection, exc)
    return maxi, _as_utc(maxi_ts)


def peak_for_edition(db, event, year, location_id=DEFAULT_LOCATION_ID):
    """Pic de presents d'une edition. (pic_int, instant_utc) ou (None, None).

    Balaye data_access et toutes les archives sur la fenetre de dates, sans
    lire ni le nom de collection ni `requested_event`.
    """
    debut, fin = edition_window(db, event, year)
    if debut is None:
        return None, None

    ensure_indexes(db)

    maxi = None
    maxi_ts = None
    for nom in snapshot_collections(db):
        valeur, instant = _scan_peak(db, nom, debut, fin, location_id)
        if valeur is None:
            continue
        if maxi is None or valeur > maxi:
            maxi = valeur
            maxi_ts = instant
    return maxi, maxi_ts


def list_editions(db, now_utc=None):
    """Editions consultables, de la plus recente a la plus ancienne.

    Une edition est consultable si sa date de course est resoluble ET si son
    evenement a un sigle : sans sigle, event_label ne sait pas fabriquer le
    libelle court que la montre affiche, et une ligne sans nom n'aide
    personne. Les deux exclusions sont silencieuses par conception -- la
    liste montre ce qui est consultable, pas l'inventaire des lacunes.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    couples = set()
    for doc in db["parametrages"].find({}, {"event": 1, "year": 1}):
        event = doc.get("event")
        annee = _ws._safe_int(doc.get("year"))
        if not event or annee is None:
            continue
        couples.add((event, annee))

    editions = []
    for event, annee in couples:
        libelle = _ws.event_label(_ws.read_event_short(db, event), annee)
        if not libelle:
            continue
        course = resolve_race_dt(db, event, annee)
        if course is None:
            continue
        editions.append({
            "event": event,
            "year": annee,
            "label": libelle,
            "race_dt": course,
            "en_cours": (course - WINDOW_BEFORE) <= now_utc < (course + WINDOW_AFTER),
        })

    editions.sort(key=lambda e: e["race_dt"], reverse=True)
    return editions[:MAX_EDITIONS]


def cached_peak(db, event, year, location_id=DEFAULT_LOCATION_ID, now_utc=None):
    """Pic d'une edition, via le cache persistant `watch_peaks`.

    Le pic d'une edition close est definitif : calcule une fois, relu ensuite.
    Seule l'edition en cours est recalculee, et pas plus d'une fois par minute
    -- pendant l'evenement le pic monte encore.
    """
    annee = _ws._safe_int(year)
    if not event or annee is None:
        return None, None
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    cle = "%s|%d" % (event, annee)
    cache = db[CACHE_COLLECTION]
    doc = cache.find_one({"_id": cle})

    if doc is not None:
        calcule_le = _as_utc(doc.get("computed_at"))
        frais = (doc.get("source") != "live"
                 or calcule_le is None
                 or now_utc - calcule_le < LIVE_CACHE_TTL)
        if frais:
            return doc.get("peak"), _as_utc(doc.get("peak_ts"))

    pic, instant = peak_for_edition(db, event, annee, location_id)
    if pic is None:
        return None, None

    debut, fin = edition_window(db, event, annee)
    en_cours = debut is not None and debut <= now_utc < fin
    try:
        cache.update_one(
            {"_id": cle},
            {"$set": {
                "event": event,
                "year": annee,
                "peak": pic,
                # Ecrit naif-UTC, comme tout ce que pymongo relira.
                "peak_ts": instant.replace(tzinfo=None) if instant else None,
                "computed_at": now_utc.replace(tzinfo=None),
                "source": "live" if en_cours else "archive",
            }},
            upsert=True,
        )
    except Exception as exc:
        # Un cache qui n'ecrit pas coute du temps de calcul, pas une reponse.
        logger.warning("watch_peaks : ecriture du cache %s echouee (%s)", cle, exc)
    return pic, instant
