import pymongo
import logging
from datetime import datetime, timedelta, timezone

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
db = client["titan_dev"]  # Remplacer par le nom de votre base
parametrage_col = db["parametrages"]
timetable_col = db["timetable"]

# -------------------------------------------------------------------
# Fonction pour calculer la durée (HH:MM) entre deux heures (en considérant le passage au lendemain)
# -------------------------------------------------------------------
def compute_duration(open_time, close_time):
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

# -------------------------------------------------------------------
# Génération de deux vignettes (ou une seule quand requis) à partir d’une paire d’heures
# -------------------------------------------------------------------
def generate_vignettes_for_entry(date_str, open_time, close_time, base_activity, category, place, details, id_source, v_type, merge_remark=True):
    logger.debug(f"Generating vignettes for {base_activity} on {date_str}: open='{open_time}', close='{close_time}'")
    duration = compute_duration(open_time, close_time)
    # Calcul de la date de fermeture
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
    
    open_vignette = {
        "date": date_str,
        "start": open_time,
        "end": close_time if closing_date == date_str and close_time != "23:59" else "",
        "duration": duration,
        "category": category,
        "activity": f"Ouverture {base_activity}",
        "place": place,
        "department": "SAFE",
        "type": v_type,
        "origin": "paramétrage",
        "remark": f"Fermeture prévue: {closing_date} {close_time}" if merge_remark and open_time != close_time and closing_date != date_str else "",
        "param_id": id_source
    }
    close_vignette = {
        "date": closing_date,
        "start": "",
        "end": close_time,
        "duration": duration,
        "category": category,
        "activity": f"Fermeture {base_activity}",
        "place": place,
        "department": "SAFE",
        "type": v_type,
        "origin": "paramétrage",
        "remark": "",
        "param_id": id_source
    }
    logger.debug(f"Vignette Ouverture générée: {open_vignette}")
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
    # Section "center"
    for entry in global_data.get("center", []):
        date_str = entry["date"]
        id_source = entry.get("id", f"center_{date_str}_{entry.get('openTime','')}")
        v_pair = generate_vignettes_for_entry(
            date_str,
            entry["openTime"],
            entry["closeTime"],
            "Centre accréditation",
            "Accreditations",
            "Centre accréditation",
            {},
            id_source,
            "Organization"
        )
        vignettes.extend(v_pair)
    # Section "dates"
    for entry in global_data.get("dates", []):
        if entry.get("is24h") or entry.get("closed"):
            logger.info(f"Ignoré globalHoraires.dates pour {entry.get('date')} (is24h/closed)")
            continue
        date_str = entry["date"]
        id_source = f"dates_{date_str}_{entry.get('openTime','')}"
        v_pair = generate_vignettes_for_entry(
            date_str,
            entry["openTime"],
            entry["closeTime"],
            "au public",
            "General",
            "Controle",
            {},
            id_source,
            "Timetable"
        )
        vignettes.extend(v_pair)
    # Section "demontage"
    if "demontage" in global_data:
        dem = global_data["demontage"]
        start_dt = datetime.fromisoformat(dem["start"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=2)))
        end_dt = datetime.fromisoformat(dem["end"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=2)))
        id_source = "demontage"
        v_pair = generate_vignettes_for_entry(
            start_dt.strftime("%Y-%m-%d"),
            start_dt.strftime("%H:%M"),
            end_dt.strftime("%H:%M"),
            "Démontage",
            "Controle",
            "Demontage",
            {},
            id_source,
            "Timetable"
        )
        vignettes.extend(v_pair)
    # Section "endBadge" : une seule vignette
    if "endBadge" in global_data:
        end_badge_iso = global_data["endBadge"]
        dt = datetime.fromisoformat(end_badge_iso.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=2)))
        date_str = dt.strftime("%Y-%m-%d")
        id_source = "endBadge"
        v = {
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
            "param_id": id_source
        }
        vignettes.append(v)
        logger.debug(f"Vignette endBadge générée: {v}")
    # Section "helpDesk"
    if "helpDesk" in global_data:
        hd = global_data["helpDesk"]
        id_source = "helpDesk"
        for d in iterate_date_range(hd["start"], hd["end"]):
            v_pair = generate_vignettes_for_entry(
                d,
                hd["openTime"],
                hd["closeTime"],
                "Help Desk",
                "Accreditations",
                "Help Desk",
                {},
                id_source,
                "Organization"
            )
            vignettes.extend(v_pair)
    # Section "montage" – Traitement spécifique pour montage (longue période) :
    if "montage" in global_data:
        mon = global_data["montage"]
        start_dt = datetime.fromisoformat(mon["start"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=2)))
        end_dt = datetime.fromisoformat(mon["end"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=2)))
        id_source = "montage"
        # Vignette d'ouverture avec date du start_dt
        open_v = {
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
            "param_id": id_source
        }
        # Vignette de fermeture avec la date réelle de end_dt
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
            "param_id": id_source
        }
        vignettes.extend([open_v, close_v])
    # Section "paddockScan"
    if "paddockScan" in global_data:
        ps_iso = global_data["paddockScan"]
        dt = datetime.fromisoformat(ps_iso.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=2)))
        date_str = dt.strftime("%Y-%m-%d")
        id_source = "paddockScan"
        # On génère une seule vignette pour le scan
        v = {
            "date": date_str,
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
            "param_id": id_source
        }
        vignettes.append(v)
        logger.debug(f"Vignette paddockScan générée: {v}")
    # Section "pcOrga"
    for entry in global_data.get("pcOrga", []):
        date_str = entry["date"]
        id_source = entry.get("id", f"pcOrga_{date_str}_{entry.get('openTime','')}")
        v_pair = generate_vignettes_for_entry(
            date_str,
            entry["openTime"],
            entry["closeTime"],
            "PC Organisation",
            "Controle",
            "PC Organisation",
            {},
            id_source,
            "Organization"
        )
        vignettes.extend(v_pair)
    # Section "pcAuthorities"
    for entry in global_data.get("pcAuthorities", []):
        date_str = entry["date"]
        id_source = entry.get("id", f"pcAuthorities_{date_str}_{entry.get('openTime','')}")
        v_pair = generate_vignettes_for_entry(
            date_str,
            entry["openTime"],
            entry["closeTime"],
            "PC Autorités",
            "Controle",
            "PC Autorités",
            {},
            id_source,
            "Organization"
        )
        vignettes.extend(v_pair)
    # Section "scan"
    if "scan" in global_data:
        scan_iso = global_data["scan"]
        dt = datetime.fromisoformat(scan_iso.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=2)))
        date_str = dt.strftime("%Y-%m-%d")
        id_source = "scan"
        v = {
            "date": date_str,
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
            "param_id": id_source
        }
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
        for date_entry in parking.get("dates", []):
            date_str = date_entry["date"]
            org = date_entry.get("organisation", {})
            pub = date_entry.get("public", {})
            valid_org = org and "open" in org and "close" in org and not (org.get("is24h") or org.get("closed"))
            valid_pub = pub and "open" in pub and "close" in pub and not (pub.get("is24h") or pub.get("closed"))
            if valid_org and valid_pub and org["open"] == pub["open"] and org["close"] == pub["close"]:
                v_pair = generate_vignettes_for_entry(
                    date_str,
                    org["open"],
                    org["close"],
                    f"Parking {parking_name}",
                    "Parking",
                    parking_name,
                    {"remark": "Organisation & Public"},
                    id_source,
                    "Organization"
                )
                vignettes.extend(v_pair)
                logger.debug(f"Fusionnées vignettes pour Parking {parking_name} (Organisation & Public) le {date_str}")
            else:
                if valid_org:
                    v_pair = generate_vignettes_for_entry(
                        date_str,
                        org["open"],
                        org["close"],
                        f"Parking {parking_name} - Organisation",
                        "Controle",
                        parking_name,
                        {},
                        id_source,
                        "Organization"
                    )
                    vignettes.extend(v_pair)
                    logger.debug(f"Vignettes pour Parking {parking_name} - Organisation générées le {date_str}")
                if valid_pub:
                    v_pair = generate_vignettes_for_entry(
                        date_str,
                        pub["open"],
                        pub["close"],
                        f"Parking {parking_name} - Public",
                        "Controle",
                        parking_name,
                        {},
                        id_source,
                        "Organization"
                    )
                    vignettes.extend(v_pair)
                    logger.debug(f"Vignettes pour Parking {parking_name} - Public générées le {date_str}")
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
            # Si l'entrée est marquée is24h ou si les horaires sont manquants ou si "closed" est True, on ignore
            if not pub or "open" not in pub or "close" not in pub or date_entry.get("is24h") or pub.get("closed"):
                logger.debug(f"Ignoré camping {camping_name} pour {date_str}")
                continue

            # Déterminer si l'ouverture ou la fermeture doivent être ignorées en fonction des jours adjacents
            skip_open = False
            skip_close = False
            # Si l'heure d'ouverture est "00:00" et que la veille est en 24h
            if pub.get("open") == "00:00" and i > 0 and dates_list[i-1].get("is24h") is True:
                skip_open = True
            # Si l'heure de fermeture est "23:59" et que le lendemain est en 24h
            if pub.get("close") == "23:59" and i < len(dates_list)-1 and dates_list[i+1].get("is24h") is True:
                skip_close = True

            logger.debug(f"Pour camping {camping_name} le {date_str}, skip_open={skip_open}, skip_close={skip_close}")

            # Générer la paire de vignettes
            open_v, close_v = generate_vignettes_for_entry(
                date_str,
                pub["open"],
                pub["close"],
                f"Camping {camping_name}",
                "AA",
                camping_name,
                details,
                id_source,
                "Organization"
            )
            # N'ajouter que celles non ignorées
            if not skip_open:
                vignettes.append(open_v)
            if not skip_close:
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
        if date_key not in data_field:
            data_field[date_key] = []
        # Recherche d'une vignette existante par param_id et activity
        found = False
        for i, existing in enumerate(data_field[date_key]):
            if existing.get("param_id") == v.get("param_id") and existing.get("activity") == v.get("activity"):
                data_field[date_key][i] = v
                found = True
                logger.debug(f"Vignette mise à jour pour {v['activity']} le {date_key}")
                break
        if not found:
            data_field[date_key].append(v)
            logger.debug(f"Nouvelle vignette ajoutée pour {v['activity']} le {date_key}")

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
if __name__ == "__main__":
    event_value = "24H MOTOS"
    year_value = "2025"
    main(event_value, year_value)
