import pymongo
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
import uuid

# Configuration du logging pour écrire dans un fichier (écrasé à chaque lancement)
logging.basicConfig(
    filename="merge.log",
    filemode="w",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Connexion à MongoDB
client = pymongo.MongoClient("mongodb://localhost:27017")
db = client["titan"]  # Remplacer par le nom de votre base
parametrage_col = db["parametrages"]
timetable_col = db["timetable"]
todos_col = db["todos"]

def _mk_id(seed: str) -> str:
    # UUID5 = déterministe (même seed => même id), pas aléatoire
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))

# -------------------------------------------------------------------
# Fonction pour calculer la durée (HH:MM) entre deux heures (en considérant le passage au lendemain)
# -------------------------------------------------------------------
def compute_duration(open_time: str, close_time: str) -> str:
    try:
        t_open = datetime.strptime(open_time, "%H:%M")
        t_close = datetime.strptime(close_time, "%H:%M")
    except Exception as e:
        logger.error(f"Erreur lors de la conversion des heures '{open_time}' et '{close_time}': {e}")
        return ""
    if t_close > t_open:
        delta = t_close - t_open
    else:
        delta = (t_close + timedelta(days=1)) - t_open
    hours, remainder = divmod(delta.seconds, 3600)
    minutes = remainder // 60
    duration = f"{hours:02}:{minutes:02}"
    logger.debug(f"Durée calculée entre {open_time} et {close_time} : {duration}")
    return duration

# --- helper strict PC ---
def _strict_pc_skips(open_h: str, close_h: str) -> Tuple[bool, bool]:
    """
    Retourne (skip_open, skip_close) pour les PC (Organisation/Autorités)
    Règles:
      - 06:00–23:59  => skip_close = True
      - 00:00–23:59  => skip_open = True, skip_close = True (aucune vignette)
      - 00:00–HH:MM  => skip_open = True
      - sinon        => ne supprime rien
    """
    if open_h == "00:00" and close_h == "23:59":
        return True, True
    skip_open = (open_h == "00:00")
    skip_close = (close_h == "23:59")
    return skip_open, skip_close

def _attach_todos(vignette: dict, base_activity: str, category: str, place: str, add_on: str = "open") -> dict:
    """
    Ajoute 'todo': [str, ...] sur la vignette (par convention: ouvertures).
    - Ne crée rien si aucun todo n'existe pour ce type.
    - Laisse la clé 'todos_type' (debug/filtrage), retire toute ancienne clé 'todos'.
    """
    try:
        todo_type = _resolve_todo_type(base_activity, category, place)
        if not todo_type:
            return vignette
        cache = _get_todos_cache()
        todos_list = cache.get(todo_type) or []
        if not todos_list:
            return vignette
        # normalisation
        vignette.pop("todos", None)
        vignette["todo"] = todos_list
        vignette["todos_type"] = todo_type
    except Exception as e:
        logger.error(f"Echec _attach_todos ({base_activity}/{category}/{place}): {e}")
    return vignette

# À mettre en haut, près des helpers
def _parse_iso_to_local(iso_str: Optional[str], tz_hours: int = 2) -> Optional[datetime]:
    """Parse '2025-06-10T14:30:00Z' ou '...+00:00' -> datetime tz Europe/Paris(+2 l'été) ; None si invalide."""
    if not iso_str or not isinstance(iso_str, str):
        return None
    try:
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone(timedelta(hours=tz_hours)))
    except Exception as e:
        logger.error(f"_parse_iso_to_local: impossible de parser '{iso_str}': {e}")
        return None
    
def _mk_seed_id(event: str, year: str, date: str, activity: str, place: str = "", param_id: str = "") -> str:
    seed = f"{event}|{year}|{date}|{activity}|{place}|{param_id}"
    return _mk_id(seed)

# -------------------------------------------------------------------
# TODOS: cache  mapping type
# -------------------------------------------------------------------
_TODOS_CACHE: Optional[dict] = None

def _get_todos_cache() -> dict:
    """
    Charge une fois la collection 'todos' sous la forme:
      { "type": ["tâche 1", "tâche 2", ...], ... }
    """
    global _TODOS_CACHE
    if _TODOS_CACHE is not None:
        return _TODOS_CACHE
    try:
        _TODOS_CACHE = {
            doc.get("type"): (doc.get("todos") or [])
            for doc in todos_col.find({}, {"type": 1, "todos": 1})
        }
        # sécurité: forcer list[str]
        for k, v in list(_TODOS_CACHE.items()):
            if not isinstance(v, list):
                _TODOS_CACHE[k] = []
            else:
                _TODOS_CACHE[k] = [str(x) for x in v]
    except Exception as e:
        logger.error(f"Impossible de charger la collection 'todos': {e}")
        _TODOS_CACHE = {}
    return _TODOS_CACHE

def _resolve_todo_type(base_activity: str, category: str = "", place: str = "") -> Optional[str]:
    """
    Devine le type de todos à partir du libellé d'activité.
    Retourne une clé existant dans la collection 'todos' ou None.
    """
    s = (base_activity or "").lower()

    # principaux
    if s.startswith("porte"):
        return "portes"
    if "parking" in s:
        return "parkings"
    if "aire d'accueil" in s or "camping" in s:
        return "campings"
    if "pc organisation" in s:
        return "pcorga"
    if "pc autorités" in s or "pc autorit" in s:
        return "pcauthorities"
    if "centre accréditation" in s or "centre accreditation" in s:
        return "centreaccreditation"
    if "help desk" in s:
        return "helpdesk"
    if "fin de la validité du badge" in s or "badge" in s:
        return "badges"
    if "scan" in s:
        return "scan"
    if "démontage" in s or "fin du montage" in s:
        return "demontage"
    if "montage" in s or "début du montage" in s:
        return "montage"
    if "tribune" in s:
        return "tribunes"
    if "passerelle" in s:
        return "passerelles"
    if "sanitaire" in s:
        return "sanitaires"
    if s.strip() == "au public":
        return "global"
    return None

# -------------------------------------------------------------------
# Génération de deux vignettes (ou une seule quand requis) à partir d’une paire d’heures
# -------------------------------------------------------------------
def generate_vignettes_for_entry(date_str, open_time, close_time, base_activity, category, place, details, id_source, v_type, merge_remark=True):
    logger.debug(f"Generating vignettes for {base_activity} on {date_str}: open='{open_time}', close='{close_time}'")
    duration = compute_duration(open_time, close_time)

    # Calcul de la date de fermeture (si passe minuit)
    closing_date = date_str
    try:
        t_open = datetime.strptime(open_time, "%H:%M")
        t_close = datetime.strptime(close_time, "%H:%M")
        if t_close <= t_open:
            dt_date = datetime.strptime(date_str, "%Y-%m-%d")
            closing_date = (dt_date + timedelta(days=1)).strftime("%Y-%m-%d")
            logger.debug(f"Pour {base_activity}, fermeture déplacée au {closing_date}")
    except Exception as e:
        logger.error(f"Erreur lors du calcul de la date de fermeture pour {base_activity} le {date_str}: {e}")

    # 🔐 IDs DÉTERMINISTES (sans changer la signature ni les champs métier)
    open_activity  = f"Ouverture {base_activity}"
    close_activity = f"Fermeture {base_activity}"
    open_id  = _mk_id(f"{id_source}|{date_str}|{open_activity}")
    close_id = _mk_id(f"{id_source}|{closing_date}|{close_activity}")

    open_vignette = {
        "_id": open_id,  # 👈 ajouté
        "date": date_str,
        "start": open_time,
        "end": close_time if closing_date == date_str and close_time != "23:59" else "",
        "duration": duration,
        "category": category,
        "activity": open_activity,
        "place": place,
        "department": "SAFE",
        "type": v_type,
        "origin": "paramétrage",
        "remark": f"Fermeture prévue: {closing_date} {close_time}" if merge_remark and open_time != close_time and closing_date != date_str else "",
        "param_id": id_source,
        "preparation_checked": "non"
    }
    close_vignette = {
        "_id": close_id,  # 👈 ajouté
        "date": closing_date,
        "start": "",
        "end": close_time,
        "duration": duration,
        "category": category,
        "activity": close_activity,
        "place": place,
        "department": "SAFE",
        "type": v_type,
        "origin": "paramétrage",
        "remark": "",
        "param_id": id_source,
        "preparation_checked": "non"
    }
    logger.debug(f"Vignette Ouverture générée: {open_vignette}")
    # ✅ Attache les tâches obligatoires sur la vignette d'ouverture
    try:
        open_vignette = _attach_todos(open_vignette, base_activity, category, place, add_on="open")
    except Exception as e:
        logger.error(f"_attach_todos failed for {base_activity} ({date_str}): {e}")
    logger.debug(f"Vignette Fermeture générée: {close_vignette}")
    return open_vignette, close_vignette

# -------------------------------------------------------------------
# Itération sur une plage de dates (format "YYYY-MM-DD")
# -------------------------------------------------------------------
def iterate_date_range(start_date_str, end_date_str):
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except Exception as e:
        logger.error(f"Erreur lors de la conversion des dates '{start_date_str}' ou '{end_date_str}': {e}")
        return
    current = start_date
    while current <= end_date:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)

# -------------------------------------------------------------------
# Traitement de la section globalHoraires
# -------------------------------------------------------------------
def process_global_horaires(global_data, event, year):
    logger.info("Traitement de globalHoraires")
    vignettes = []

    # -------- Section "center" --------
    for entry in global_data.get("center", []):
        try:
            date_str = entry["date"]
            open_t = entry.get("openTime")
            close_t = entry.get("closeTime")
            if not open_t or not close_t:
                logger.warning(f"center {date_str}: horaires manquants -> ignoré")
                continue
            id_source = entry.get("id", f"center_{date_str}_{open_t}")
            v_pair = generate_vignettes_for_entry(
                date_str, open_t, close_t,
                "Centre accréditation", "Accreditations", "Centre accréditation",
                {}, id_source, "Organization"
            )
            vignettes.extend(v_pair)
        except KeyError as e:
            logger.warning(f"center: clé manquante {e} -> entrée ignorée")

    # -------- Section "dates" (au public) --------
    for entry in global_data.get("dates", []):
        try:
            if entry.get("is24h") or entry.get("closed"):
                logger.info(f"Ignoré globalHoraires.dates pour {entry.get('date')} (is24h/closed)")
                continue
            date_str = entry["date"]
            open_t = entry.get("openTime")
            close_t = entry.get("closeTime")
            if not open_t or not close_t:
                logger.warning(f"dates {date_str}: horaires manquants -> ignoré")
                continue
            id_source = f"dates_{date_str}_{open_t}"
            v_pair = generate_vignettes_for_entry(
                date_str, open_t, close_t,
                "au public", "General", "Controle",
                {}, id_source, "Timetable"
            )
            vignettes.extend(v_pair)
        except KeyError as e:
            logger.warning(f"dates: clé manquante {e} -> entrée ignorée")

    # -------- Section "demontage" --------
    if "demontage" in global_data:
        dem = global_data.get("demontage") or {}
        start_dt = _parse_iso_to_local(dem.get("start"))
        end_dt   = _parse_iso_to_local(dem.get("end"))
        if not start_dt or not end_dt:
            logger.warning("globalHoraires.demontage incomplet -> ignoré")
        else:
            id_source = "demontage"
            v_pair = generate_vignettes_for_entry(
                start_dt.strftime("%Y-%m-%d"),
                start_dt.strftime("%H:%M"),
                end_dt.strftime("%H:%M"),
                "Démontage", "Controle", "Demontage",
                {}, id_source, "Timetable"
            )
            vignettes.extend(v_pair)

    # -------- Section "endBadge" (une seule vignette) --------
    if "endBadge" in global_data:
        dt = _parse_iso_to_local(global_data.get("endBadge"))
        if not dt:
            logger.warning("globalHoraires.endBadge absent/invalide -> ignoré")
        else:
            date_str = dt.strftime("%Y-%m-%d")
            id_source = "endBadge"
            v = {
                "_id": _mk_seed_id(event, year, date_str, "Fin de la validité du badge salarié", "Badges", id_source),
                "date": date_str,
                "start": dt.strftime("%H:%M"),
                "end": "",
                "duration": "",
                "category": "Controle",
                "activity": "Fin de la validité du badge salarié",
                "place": "Badges",
                "department": "SAFE",
                "type": "Timetable",
                "origin": "paramétrage",
                "remark": "",
                "param_id": id_source,
                "preparation_checked": "non"
            }
            v = _attach_todos(v, "Fin de la validité du badge salarié", "Controle", "Badges", add_on="open")
            vignettes.append(v)
            logger.debug(f"Vignette endBadge générée: {v}")

    # -------- Section "helpDesk" --------
    if "helpDesk" in global_data:
        hd = global_data.get("helpDesk") or {}
        start_d = hd.get("start")
        end_d   = hd.get("end")
        open_t  = hd.get("openTime")
        close_t = hd.get("closeTime")
        if not start_d or not end_d or not open_t or not close_t:
            logger.warning("globalHoraires.helpDesk incomplet -> ignoré")
        else:
            id_source = "helpDesk"
            for d in iterate_date_range(start_d, end_d):
                v_pair = generate_vignettes_for_entry(
                    d, open_t, close_t,
                    "Help Desk", "Accreditations", "Help Desk",
                    {}, id_source, "Organization"
                )
                vignettes.extend(v_pair)

    # -------- Section "montage" (longue période) --------
    if "montage" in global_data:
        mon = global_data.get("montage") or {}
        start_dt = _parse_iso_to_local(mon.get("start"))
        end_dt   = _parse_iso_to_local(mon.get("end"))
        if not start_dt or not end_dt:
            logger.warning("globalHoraires.montage incomplet -> ignoré")
        else:
            id_source = "montage"
            open_v = {
                "_id": _mk_seed_id(event, year, start_dt.strftime("%Y-%m-%d"), "Début du montage", "Montage", id_source),
                "date": start_dt.strftime("%Y-%m-%d"),
                "start": start_dt.strftime("%H:%M"),
                "end": "",
                "duration": compute_duration(start_dt.strftime("%H:%M"), end_dt.strftime("%H:%M")),
                "category": "Controle",
                "activity": "Début du montage",
                "place": "Montage",
                "department": "SAFE",
                "type": "Timetable",
                "origin": "paramétrage",
                "remark": f"Fermeture prévue: {end_dt.strftime('%H:%M')}",
                "param_id": id_source,
                "preparation_checked": "non"
            }
            open_v = _attach_todos(open_v, "Début du montage", "Controle", "Montage", add_on="open")
            close_v = {
                "date": end_dt.strftime("%Y-%m-%d"),
                "start": "",
                "end": end_dt.strftime("%H:%M"),
                "duration": "",
                "category": "Controle",
                "activity": "Fin du montage",
                "place": "Montage",
                "department": "SAFE",
                "type": "Timetable",
                "origin": "paramétrage",
                "remark": "",
                "param_id": id_source,
                "preparation_checked": "non"
            }
            vignettes.extend([open_v, close_v])

    # -------- Section "paddockScan" --------
    if "paddockScan" in global_data:
        dt = _parse_iso_to_local(global_data.get("paddockScan"))
        if not dt:
            logger.warning("globalHoraires.paddockScan invalide -> ignoré")
        else:
            id_source = "paddockScan"
            v = {
                "_id": _mk_seed_id(event, year, dt.strftime("%Y-%m-%d"), "Mise en place du contrôle par scan", "Scan", id_source),
                "date": dt.strftime("%Y-%m-%d"),
                "start": dt.strftime("%H:%M"),
                "end": "",
                "duration": "",
                "category": "Controle",
                "activity": "Mise en place du contrôle par scan",
                "place": "Scan",
                "department": "SAFE",
                "type": "Timetable",
                "origin": "paramétrage",
                "remark": "",
                "param_id": id_source,
                "preparation_checked": "non"
            }
            v = _attach_todos(v, "Mise en place du contrôle par scan", "Controle", "Scan", add_on="open")
            vignettes.append(v)
            logger.debug(f"Vignette paddockScan générée: {v}")

    # -------- Section "pcOrga" --------
    for entry in global_data.get("pcOrga", []):
        try:
            date_str = entry["date"]
            open_h = entry["openTime"]
            close_h = entry["closeTime"]
            id_source = entry.get("id", f"pcOrga_{date_str}_{open_h}")
            skip_open, skip_close = _strict_pc_skips(open_h, close_h)
            open_v, close_v = generate_vignettes_for_entry(
                date_str, open_h, close_h,
                "PC Organisation", "Controle", "PC Organisation",
                {}, id_source, "Organization"
            )
            if not skip_open:
                vignettes.append(open_v)
            if not skip_close:
                vignettes.append(close_v)
        except KeyError as e:
            logger.warning(f"pcOrga: clé manquante {e} -> entrée ignorée")

    # -------- Section "pcAuthorities" --------
    for entry in global_data.get("pcAuthorities", []):
        try:
            date_str = entry["date"]
            open_h = entry["openTime"]
            close_h = entry["closeTime"]
            id_source = entry.get("id", f"pcAuthorities_{date_str}_{open_h}")
            skip_open, skip_close = _strict_pc_skips(open_h, close_h)
            open_v, close_v = generate_vignettes_for_entry(
                date_str, open_h, close_h,
                "PC Autorités", "Controle", "PC Autorités",
                {}, id_source, "Organization"
            )
            if not skip_open:
                vignettes.append(open_v)
            if not skip_close:
                vignettes.append(close_v)
        except KeyError as e:
            logger.warning(f"pcAuthorities: clé manquante {e} -> entrée ignorée")

    # -------- Section "scan" (autre clé possible) --------
    if "scan" in global_data:
        dt = _parse_iso_to_local(global_data.get("scan"))
        if not dt:
            logger.warning("globalHoraires.scan invalide -> ignoré")
        else:
            id_source = "scan"
            v = {
                "_id": _mk_seed_id(event, year, dt.strftime("%Y-%m-%d"), "Mise en place du contrôle par scan", "Scan", id_source),
                "date": dt.strftime("%Y-%m-%d"),
                "start": dt.strftime("%H:%M"),
                "end": "",
                "duration": "",
                "category": "Controle",
                "activity": "Mise en place du contrôle par scan",
                "place": "Scan",
                "department": "SAFE",
                "type": "Timetable",
                "origin": "paramétrage",
                "remark": "",
                "param_id": id_source,
                "preparation_checked": "non"
            }
            v = _attach_todos(v, "Mise en place du contrôle par scan", "Controle", "Scan", add_on="open")
            vignettes.append(v)
            logger.debug(f"Vignette scan générée: {v}")

    logger.info(f"{len(vignettes)} vignettes générées pour globalHoraires")
    return vignettes

def process_portes_horaires(portes_data, event, year):
    logger.info("Traitement de portesHoraires")
    vignettes = []
    for porte_name, porte_info in portes_data.items():
        id_source = porte_info.get("id", porte_name)
        for date_entry in porte_info.get("dates", []):
            date_str = date_entry["date"]
            details = porte_info.get("controle", {})
            org = date_entry.get("organisation", {})
            pub = date_entry.get("public", {})
            valid_org = org and "open" in org and "close" in org and not (org.get("is24h") or org.get("closed"))
            valid_pub = pub and "open" in pub and "close" in pub and not (pub.get("is24h") or pub.get("closed"))
            if valid_org and valid_pub and org["open"] == pub["open"] and org["close"] == pub["close"]:
                v_pair = generate_vignettes_for_entry(
                    date_str,
                    org["open"],
                    org["close"],
                    f"Porte {porte_name}",
                    "Controle",
                    porte_name,
                    {"remark": "Organisation & Public"},
                    id_source,
                    "Organization"
                )
                vignettes.extend(v_pair)
                logger.debug(f"Fusionnées vignettes pour Porte {porte_name} (Organisation & Public) le {date_str}")
            else:
                if valid_org:
                    v_pair = generate_vignettes_for_entry(
                        date_str,
                        org["open"],
                        org["close"],
                        f"Porte {porte_name} - Organisation",
                        "Controle",
                        porte_name,
                        {},
                        id_source,
                        "Organization"
                    )
                    vignettes.extend(v_pair)
                    logger.debug(f"Vignettes pour Porte {porte_name} - Organisation générées le {date_str}")
                if valid_pub:
                    v_pair = generate_vignettes_for_entry(
                        date_str,
                        pub["open"],
                        pub["close"],
                        f"Porte {porte_name} - Public",
                        "Porte",
                        porte_name,
                        {},
                        id_source,
                        "Organization"
                    )
                    vignettes.extend(v_pair)
                    logger.debug(f"Vignettes pour Porte {porte_name} - Public générées le {date_str}")
    logger.info(f"{len(vignettes)} vignettes générées pour portesHoraires")
    return vignettes

def process_parkings_horaires(parkings_list, event, year):
    logger.info("Traitement de parkingsHoraires")
    vignettes = []

    for parking in parkings_list:
        id_source = parking.get("id", parking.get("name", "parking"))
        parking_name = parking.get("name", "Parking")
        details = parking.get("controle", {})

        # on trie les dates pour pouvoir regarder veille/lendemain
        dates_list = sorted(parking.get("dates", []), key=lambda d: d["date"])

        def is24h_flag(entry, key: str) -> bool:
            """key ∈ {'organisation','public'} — renvoie True si ce sous-volet est 24/24."""
            sub = entry.get(key, {}) or {}
            return bool(entry.get("is24h")) or bool(sub.get("is24h"))

        for i, date_entry in enumerate(dates_list):
            date_str = date_entry["date"]
            org = date_entry.get("organisation", {}) or {}
            pub = date_entry.get("public", {}) or {}

            org_ok = ("open" in org and "close" in org and not org.get("closed"))
            pub_ok = ("open" in pub and "close" in pub and not pub.get("closed"))

            # drapeaux 24/24 pour aujourd'hui / veille / lendemain (par flux)
            org_24_today = is24h_flag(date_entry, "organisation")
            pub_24_today = is24h_flag(date_entry, "public")

            prev_entry = (dates_list[i-1] if i > 0 else {})
            next_entry = (dates_list[i+1] if i < len(dates_list)-1 else {})

            org_24_prev = is24h_flag(prev_entry, "organisation") if prev_entry else False
            pub_24_prev = is24h_flag(prev_entry, "public") if prev_entry else False
            org_24_next = is24h_flag(next_entry, "organisation") if next_entry else False
            pub_24_next = is24h_flag(next_entry, "public") if next_entry else False

            # --------- CAS COMBINÉ: mêmes horaires org/public ----------
            combo = (
                org_ok and pub_ok and
                not org_24_today and not pub_24_today and
                org.get("open") == pub.get("open") and
                org.get("close") == pub.get("close")
            )

            if combo:
                open_h  = org["open"]
                close_h = org["close"]

                # anti-redondances bords de période 24/24 (si l’un des flux bascule 24h)
                skip_open  = (open_h == "00:00" and (org_24_prev or pub_24_prev))
                skip_close = (close_h == "23:59" and (org_24_next or pub_24_next))

                open_v, close_v = generate_vignettes_for_entry(
                    date_str, open_h, close_h,
                    f"Parking {parking_name}",  # libellé sans suffixe
                    "Parking",                 # catégorie comme dans ton code pour le combiné
                    parking_name, details, id_source, "Organization"
                )

                # fermeture à 00:00 qui déborde au lendemain alors que demain est 24/24 → on jette
                drop_midnight_close_into_24h = (
                    close_h == "00:00" and
                    close_v["date"] != date_str and
                    (org_24_next or pub_24_next)
                )

                if not skip_open:
                    vignettes.append(open_v)
                if not skip_close and not drop_midnight_close_into_24h:
                    vignettes.append(close_v)

                logger.debug(f"[PARKING COMBO] {parking_name} {date_str} open={open_h} close={close_h} "
                             f"skip_open={skip_open} skip_close={skip_close} dropMidnight={drop_midnight_close_into_24h}")
                continue  # on a géré ce jour en combiné

            # --------- CAS SÉPARÉS: Organisation ----------
            if org_ok and not org_24_today:
                open_h  = org["open"]
                close_h = org["close"]

                skip_open  = (open_h == "00:00" and org_24_prev)
                skip_close = (close_h == "23:59" and org_24_next)

                open_v, close_v = generate_vignettes_for_entry(
                    date_str, open_h, close_h,
                    f"Parking {parking_name} - Organisation",
                    "Controle",               # on conserve ta catégorie existante
                    parking_name, details, id_source, "Organization"
                )

                drop_midnight_close_into_24h = (
                    close_h == "00:00" and
                    close_v["date"] != date_str and
                    org_24_next
                )

                if not skip_open:
                    vignettes.append(open_v)
                if not skip_close and not drop_midnight_close_into_24h:
                    vignettes.append(close_v)

                logger.debug(f"[PARKING ORG] {parking_name} {date_str} open={open_h} close={close_h} "
                             f"skip_open={skip_open} skip_close={skip_close} dropMidnight={drop_midnight_close_into_24h}")

            # --------- CAS SÉPARÉS: Public ----------
            if pub_ok and not pub_24_today:
                open_h  = pub["open"]
                close_h = pub["close"]

                skip_open  = (open_h == "00:00" and pub_24_prev)
                skip_close = (close_h == "23:59" and pub_24_next)

                open_v, close_v = generate_vignettes_for_entry(
                    date_str, open_h, close_h,
                    f"Parking {parking_name} - Public",
                    "Controle",               # on conserve ta catégorie existante
                    parking_name, details, id_source, "Organization"
                )

                drop_midnight_close_into_24h = (
                    close_h == "00:00" and
                    close_v["date"] != date_str and
                    pub_24_next
                )

                if not skip_open:
                    vignettes.append(open_v)
                if not skip_close and not drop_midnight_close_into_24h:
                    vignettes.append(close_v)

                logger.debug(f"[PARKING PUB] {parking_name} {date_str} open={open_h} close={close_h} "
                             f"skip_open={skip_open} skip_close={skip_close} dropMidnight={drop_midnight_close_into_24h}")

    logger.info(f"{len(vignettes)} vignettes générées pour parkingsHoraires")
    return vignettes

def process_campings_horaires(campings_list, event, year):
    logger.info("Traitement de campingsHoraires")
    vignettes = []
    for camping in campings_list:
        id_source = camping.get("id", camping.get("name", "camping"))
        camping_name = camping.get("name", "Camping")
        details = camping.get("controle", {})
        # On trie les entrées par date
        dates_list = sorted(camping.get("dates", []), key=lambda d: d["date"])
        for i, date_entry in enumerate(dates_list):
            date_str = date_entry["date"]
            pub = date_entry.get("public", {})

            # 1) Déterminer 24/24 pour aujourd'hui/veille/lendemain
            is24h_today = bool(date_entry.get("is24h") or pub.get("is24h"))
            if is24h_today or not pub or "open" not in pub or "close" not in pub or pub.get("closed"):
                logger.debug(f"Ignoré camping {camping_name} pour {date_str} (is24h={is24h_today} ou horaires manquants/fermés)")
                continue

            prev_pub = dates_list[i-1].get("public", {}) if i > 0 else {}
            next_pub = dates_list[i+1].get("public", {}) if i < len(dates_list)-1 else {}
            is24h_prev = bool((dates_list[i-1].get("is24h") if i > 0 else False) or prev_pub.get("is24h"))
            is24h_next = bool((dates_list[i+1].get("is24h") if i < len(dates_list)-1 else False) or next_pub.get("is24h"))

            # 2) Anti-redondances sur les bords des périodes 24/24
            skip_open  = (pub.get("open")  == "00:00" and is24h_prev)   # ex: dernier jour d’une période 24/24 → pas d’ouverture à 00:00
            skip_close = (pub.get("close") == "23:59" and is24h_next)   # ex: veille d’une période 24/24 → pas de fermeture à 23:59

            # 3) Génération
            open_v, close_v = generate_vignettes_for_entry(
                date_str,
                pub["open"],
                pub["close"],
                f"Aire d'accueil {camping_name}",
                "AA",
                camping_name,
                details,
                id_source,
                "Organization"
            )

            # 4) Cas spécial: close à 00:00 qui "passe" au lendemain alors que le lendemain est 24/24 → on jette
            drop_midnight_close_into_24h = (
                pub.get("close") == "00:00" and
                close_v["date"] != date_str and   # donc ça a débordé au jour+1
                is24h_next
            )

            if not skip_open:
                vignettes.append(open_v)
            if not skip_close and not drop_midnight_close_into_24h:
                vignettes.append(close_v)
    logger.info(f"{len(vignettes)} vignettes générées pour campingsHoraires")
    return vignettes

def process_hospis_horaires(hospis_list, event, year):
    logger.info("Traitement de hospisHoraires")
    vignettes = []
    for hospis in hospis_list:
        id_source = hospis.get("id", hospis.get("name", "hospis"))
        hospis_name = hospis.get("name", "Hospitalité")
        details = hospis.get("controle", {})
        for date_entry in hospis.get("dates", []):
            date_str = date_entry["date"]
            open_time = date_entry.get("openTime")
            close_time = date_entry.get("closeTime")
            is24h = date_entry.get("is24h")
            if is24h or not open_time or not close_time:
                logger.debug(f"Ignoré hospitalité {hospis_name} pour {date_str} (is24h={is24h} ou horaires manquants)")
            else:
                v_pair = generate_vignettes_for_entry(
                    date_str,
                    open_time,
                    close_time,
                    f"Hospitalité {hospis_name}",
                    "Hospi",
                    hospis_name,
                    details,
                    id_source,
                    "Organization"
                )
                vignettes.extend(v_pair)
    logger.info(f"{len(vignettes)} vignettes générées pour hospisHoraires")
    return vignettes

def process_parametrage_document(doc):
    event = doc["event"]
    year = doc["year"]
    data = doc["data"]
    vignettes = []
    logger.info(f"Début du traitement du document de paramétrage pour {event} {year}")
    if "globalHoraires" in data:
        vignettes.extend(process_global_horaires(data["globalHoraires"], event, year))
    if "portesHoraires" in data:
        vignettes.extend(process_portes_horaires(data["portesHoraires"], event, year))
    if "parkingsHoraires" in data:
        vignettes.extend(process_parkings_horaires(data["parkingsHoraires"], event, year))
    if "campingsHoraires" in data:
        vignettes.extend(process_campings_horaires(data["campingsHoraires"], event, year))
    if "hospisHoraires" in data:
        vignettes.extend(process_hospis_horaires(data["hospisHoraires"], event, year))
    logger.info(f"Fin du traitement du document de paramétrage, {len(vignettes)} vignettes générées")
    return vignettes

# -------------------------------------------------------------------
# Mise à jour du document timetable
# -------------------------------------------------------------------
def update_timetable_document(event, year, vignettes):
    logger.info(f"Mise à jour du document timetable pour {event} {year}")
    timetable_doc = timetable_col.find_one({"event": event, "year": year})
    if not timetable_doc:
        timetable_doc = {"event": event, "year": year, "data": {}}
        logger.info("Création d'un nouveau document timetable")
    data_field = timetable_doc.get("data", {})

    for v in vignettes:
        date_key = v["date"]
        if not v.get("_id"):
            v["_id"] = _mk_seed_id(
                event, year, date_key,
                str(v.get("activity","")), str(v.get("place","")), str(v.get("param_id",""))
            )
        logger.warning(f"[update_timetable_document] _id manquant → généré: {v['_id']} ({v.get('activity')} @ {date_key})")
        data_field.setdefault(date_key, [])
        items = data_field[date_key]

        # 1) si _id présent → on remplace l'existant
        if v.get("_id"):
            idx = next((i for i, x in enumerate(items) if x.get("_id") == v["_id"]), None)
            if idx is not None:
                # Priorité à manual-edit si jamais tu merges deux flux
                keep = v if v.get("origin") == "manual-edit" or items[idx].get("origin") != "manual-edit" else items[idx]
                items[idx] = keep
                continue

        # 2) fallback historique: (param_id, activity)
        idx = next((i for i, x in enumerate(items)
                    if x.get("param_id") == v.get("param_id") and x.get("activity") == v.get("activity")), None)
        
        # 🔧 Normalisation éventuelle (anciens champs)
        if "todos" in v and "todo" not in v:
            try:
                v["todo"] = [str(x) for x in (v.pop("todos") or [])]
            except Exception:
                v.pop("todos", None)
                
        # 🔧 Valeur par défaut du statut de préparation
        if not v.get("preparation_checked"):
            v["preparation_checked"] = "non"

        if idx is not None:
            items[idx] = v
        else:
            items.append(v)

    timetable_doc["data"] = data_field
    timetable_col.update_one({"event": event, "year": year}, {"$set": timetable_doc}, upsert=True)
    logger.info("Document timetable mis à jour")

# -------------------------------------------------------------------
# Fonction principale
# -------------------------------------------------------------------
def main(event, year):
    logger.info(f"Début du traitement pour l'événement {event} et l'année {year}")
    parametrage_doc = parametrage_col.find_one({"event": event, "year": year})
    if not parametrage_doc:
        logger.error(f"Aucun document de paramétrage trouvé pour l'événement {event} et l'année {year}")
        return
    vignettes = process_parametrage_document(parametrage_doc)
    update_timetable_document(event, year, vignettes)
    logger.info(f"Traitement terminé pour l'événement {event} et l'année {year}")

# -------------------------------------------------------------------
# Exécution principale
# -------------------------------------------------------------------
# -------------------------------------------------------------------
# Lancement interactif (choix année + événement)
# -------------------------------------------------------------------
EVENT_CHOICES = [
    "24H AUTOS",
    "24H MOTOS",
    "GPF",
    "GP EXPLORER",
    "SUPERBIKE",
    "LE MANS CLASSIC",
    "24H CAMIONS",
    "CONGRES SDIS",
]

def _prompt_year() -> str:
    while True:
        y = input("Année (format YYYY) : ").strip()
        if len(y) == 4 and y.isdigit():
            return y
        print("⛔ Format invalide. Merci d'entrer une année sur 4 chiffres, ex: 2025.")

def _prompt_event() -> str:
    print("\nSélectionne l'événement :")
    for i, name in enumerate(EVENT_CHOICES, start=1):
        print(f"  {i}. {name}")
    while True:
        s = input("Numéro ou nom exact : ").strip()
        # choix par numéro
        if s.isdigit():
            idx = int(s)
            if 1 <= idx <= len(EVENT_CHOICES):
                return EVENT_CHOICES[idx - 1]
        # choix par nom (insensible à la casse/espaces)
        for name in EVENT_CHOICES:
            if s.lower() == name.lower():
                return name
        print("⛔ Choix invalide. Entre un numéro de la liste ou le nom exact.")

if __name__ == "__main__":
    try:
        year_value = _prompt_year()
        event_value = _prompt_event()
        print(f"\n➡️  Lancement du merge pour '{event_value}' {year_value}...\n")
        main(event_value, year_value)
        print("\n✅ Terminé.")
    except KeyboardInterrupt:
        print("\nOpération annulée.")
