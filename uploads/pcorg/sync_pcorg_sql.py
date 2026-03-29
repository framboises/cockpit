#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_pcorg_sql.py
-----------------
Synchronise les messages PC Organisation depuis SQL Server (dbo.UserMessages)
vers MongoDB (titan.pcorg).

Auto-attribution de l'événement et de l'année : chaque message est rattaché
à un événement en fonction de sa date de création (UserMessageDateCreate)
et des plages [montage.start → demontage.end] définies dans titan.parametrages.

Récupère uniquement les catégories :
  - PCO.*  (toutes les sous-catégories)
  - PCS.Information
  - PCS.Surete

Sync incrémental basé sur DateWrite (capture créations ET modifications).
Pagination SQL par lots de 1000 lignes pour gérer les gros volumes.

Usage :
  python sync_pcorg_sql.py                # sync incrémental
  python sync_pcorg_sql.py --full         # resync complet
  python sync_pcorg_sql.py --dry-run      # simulation sans écriture
"""
import sys
import os
import re
import uuid
import html
import json
import socket
import argparse
from datetime import datetime, timezone
from collections import Counter
from zoneinfo import ZoneInfo

import pyodbc
from pymongo import MongoClient, UpdateOne
from lxml import etree
from dateutil import parser as dtparser

# ─── Configuration ───────────────────────────────────────────────────────────

SQL_HOST = "10.34.0.4"
SQL_DB = "AppHistoV4"
SQL_INSTANCE = "SQLAPP"
SQL_BROWSER_PORT = 1434
SQL_TIMEOUT = 5
KNOWN_DYNAMIC_PORT = "65422"

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = "titan"
MONGO_COLLECTION = "pcorg"
MONGO_SYNC_COLLECTION = "pcorg_sync_cursor"
MONGO_PARAMETRAGES_COLLECTION = "parametrages"

PARIS_TZ = ZoneInfo("Europe/Paris")

SQL_BATCH_SIZE = 1000

# Catégories à récupérer (préfixes)
CATEGORIES_PREFIXES = ["PCO.", "PCS.Information", "PCS.Surete"]

# Colonnes SQL à récupérer
SQL_COLUMNS = [
    "UserMessageId", "UserMessageGuid",
    "UserMessageDateCreate", "UserMessageDateClose",
    "AlarmId", "AreaId", "UserMessageAreaDesc",
    "UserMessageDescription", "UserMessageGroupNames", "UserMessageGroupDesc",
    "UserMessageAttachments", "UserMessagePhoto", "UserMessageVideo",
    "UserIdCreate", "UserIdClose", "UserNameCreate", "UserNameClose",
    "UserMessageSeverity", "UserMessageStatus",
    "UserMessageTo", "UserMessageCategory", "UserMessageComment",
    "UserMessageContentCategory", "UserMessageContentReferences",
    "UserMessageIsIncident", "UserMessageExtension", "UserMessageCustomExtension",
    "ServerName", "DateWrite",
]

# ─── Connexion SQL Server ────────────────────────────────────────────────────

def pick_driver():
    drivers = [d for d in pyodbc.drivers()]
    for name in ("ODBC Driver 18 for SQL Server",
                 "ODBC Driver 17 for SQL Server", "SQL Server"):
        if name in drivers:
            return name
    raise RuntimeError(
        "Aucun driver ODBC SQL Server installé. "
        "Drivers trouvés : " + ", ".join(drivers)
    )


def resolve_port_via_sql_browser(host, instance, timeout=2.0):
    """Interroge le SQL Browser (UDP 1434) pour le port TCP de l'instance."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        payload = b"\x03" + instance.encode("ascii") + b"\x00"
        sock.sendto(payload, (host, SQL_BROWSER_PORT))
        data, _ = sock.recvfrom(4096)
        text = data.decode("ascii", errors="ignore")
        parts = text.split(";")
        for i, part in enumerate(parts):
            if part.lower() == "tcp" and i + 1 < len(parts):
                port = parts[i + 1]
                if port.isdigit():
                    return port
    except Exception as e:
        print(f"  SQL Browser indisponible : {e}")
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return None


def build_conn_str(driver, server, user, password):
    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={SQL_DB}",
        f"UID={user}",
        f"PWD={password}",
        f"Connection Timeout={SQL_TIMEOUT}",
    ]
    if driver.startswith("ODBC Driver 18") or driver.startswith("ODBC Driver 17"):
        parts.append("Encrypt=no")
    return ";".join(parts) + ";"


def connect_sql(user, password):
    """Tente la connexion SQL avec la même stratégie de fallback que sql.py."""
    driver = pick_driver()
    print(f"  Driver ODBC : {driver}")

    candidates = []
    resolved = resolve_port_via_sql_browser(SQL_HOST, SQL_INSTANCE)
    if resolved:
        print(f"  Port résolu via SQL Browser : {resolved}")
        candidates.append(f"{SQL_HOST},{resolved}")
    if KNOWN_DYNAMIC_PORT:
        candidates.append(f"{SQL_HOST},{KNOWN_DYNAMIC_PORT}")
    candidates.extend([
        f"{SQL_HOST},1433",
        f"{SQL_HOST}\\{SQL_INSTANCE}",
        f"{SQL_HOST}",
    ])

    last_err = None
    for server in candidates:
        conn_str = build_conn_str(driver, server, user, password)
        try:
            conn = pyodbc.connect(conn_str)
            print(f"  Connecté à {server}")
            return conn
        except Exception as e:
            last_err = e

    raise ConnectionError(
        f"Impossible de se connecter à SQL Server ({SQL_HOST}). "
        f"Dernière erreur : {last_err}"
    )


# ─── Plages événements depuis parametrages ───────────────────────────────────

# Plages en dur pour les années sans document parametrages
PLAGES_FALLBACK = [
    {"event": "SUPERBIKE",   "year": 2024, "start": "2024-04-01", "end": "2024-04-08"},
    {"event": "24H MOTOS",   "year": 2024, "start": "2024-04-13", "end": "2024-04-25"},
    {"event": "GPF",         "year": 2024, "start": "2024-05-06", "end": "2024-05-14"},
    {"event": "24H AUTOS",   "year": 2024, "start": "2024-05-25", "end": "2024-06-20"},
    {"event": "24H CAMIONS", "year": 2024, "start": "2024-09-23", "end": "2024-10-01"},
]


def charger_plages_evenements(mongo_client):
    """Charge les plages [montage.start, demontage.end] depuis titan.parametrages.
    Retourne une liste de dicts {"event", "year", "start", "end"} (datetimes aware).
    """
    col = mongo_client[MONGO_DB][MONGO_PARAMETRAGES_COLLECTION]
    docs = col.find(
        {"event": {"$ne": "__GLOBAL__"}, "year": {"$ne": "__GLOBAL__"}},
        {"event": 1, "year": 1, "data.globalHoraires.montage.start": 1,
         "data.globalHoraires.demontage.end": 1,
         "data.globalHoraires.dates": 1},
    )

    plages = []
    for doc in docs:
        event = doc.get("event")
        year_raw = doc.get("year")
        if not event or not year_raw:
            continue

        # Normaliser year en int
        try:
            year = int(year_raw)
        except (ValueError, TypeError):
            print(f"  [WARN] Année invalide pour {event}: {year_raw}, ignoré")
            continue

        # Extraire les dates : montage/demontage en priorite, sinon dates publiques
        gh = (doc.get("data") or {}).get("globalHoraires") or {}
        montage_start_raw = (gh.get("montage") or {}).get("start")
        demontage_end_raw = (gh.get("demontage") or {}).get("end")

        start_dt, end_dt = None, None
        if montage_start_raw and demontage_end_raw:
            start_dt, _ = to_iso_dt(montage_start_raw)
            end_dt, _ = to_iso_dt(demontage_end_raw)

        # Fallback : min/max des dates d'ouverture publique
        if not start_dt or not end_dt:
            pub_dates = gh.get("dates") or []
            date_strs = sorted(d.get("date") for d in pub_dates if d.get("date"))
            if date_strs:
                start_dt, _ = to_iso_dt(date_strs[0] + "T00:00:00")
                end_dt, _ = to_iso_dt(date_strs[-1] + "T23:59:59")
                if start_dt and end_dt:
                    print(f"  [INFO] {event} {year} : fallback dates publiques "
                          f"{date_strs[0]} -> {date_strs[-1]}")

        if not start_dt or not end_dt:
            print(f"  [WARN] Aucune date exploitable pour {event} {year}, ignore")
            continue

        plages.append({
            "event": event,
            "year": year,
            "start": start_dt,
            "end": end_dt,
        })

    # Ajouter les plages en dur (si pas déjà couvertes par parametrages)
    existing = {(p["event"], p["year"]) for p in plages}
    for fb in PLAGES_FALLBACK:
        if (fb["event"], fb["year"]) not in existing:
            start_dt, _ = to_iso_dt(fb["start"])
            end_dt, _ = to_iso_dt(fb["end"] + "T23:59:59")
            if start_dt and end_dt:
                plages.append({
                    "event": fb["event"],
                    "year": fb["year"],
                    "start": start_dt,
                    "end": end_dt,
                })

    # Trier par date de début
    plages.sort(key=lambda p: p["start"])
    return plages


def trouver_evenements(dt, plages):
    """Pour un datetime donne, cherche dans quelles plages il tombe.
    Retourne une liste de (event, year). Si hors plage -> [("SAISON", annee)].
    Gere les chevauchements : un message peut appartenir a plusieurs evenements.
    """
    if dt is None:
        return [("SAISON", None)]
    matches = []
    for p in plages:
        if p["start"] <= dt <= p["end"]:
            matches.append((p["event"], p["year"]))
    if not matches:
        dt_local = dt.astimezone(PARIS_TZ) if dt.tzinfo else dt.replace(tzinfo=PARIS_TZ)
        return [("SAISON", dt_local.year)]
    return matches


# ─── Helpers parsing (repris de import_pcorg_csv_to_mongo.py) ────────────────

def clean_null(val):
    """Nettoie une valeur SQL : None, chaîne vide → None."""
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        return None if s == "" or s.lower() == "(null)" or s == "NULL" else s
    return val


def decode_text(s):
    if s is None:
        return None
    s = s.replace("\\x0D\\x0A", "\n").replace("\\x0A", "\n").replace("\\x22", '"')
    return html.unescape(s)


def decode_xml_text(s):
    if s is None:
        return None
    s = decode_text(s)
    s = re.sub(r'^\s*<\?xml[^>]*\?>', '', s.strip())
    return s


def to_iso_dt(val):
    """Parse un datetime (str ou datetime) → (datetime_aware, iso_str)."""
    if val is None:
        return None, None
    if isinstance(val, datetime):
        dt = val
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=PARIS_TZ)
        return dt, dt.isoformat()
    s = str(val).strip()
    if not s:
        return None, None
    try:
        dt = dtparser.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=PARIS_TZ)
        return dt, dt.isoformat()
    except Exception:
        return None, None


def xml_key_normalize(name):
    if not name:
        return ""
    n = name.strip()
    repl = {
        "SousClassification": "sous_classification",
        "Servicecontacté": "service_contacte",
        "ServiceContacté": "service_contacte",
        "ServiceContacte": "service_contacte",
        "Servicecontacte": "service_contacte",
        "DateHeure": "date_heure",
        "Carroye": "carroye",
        "Appelant": "appelant",
        "Texte": "texte",
        "telephone": "telephone",
        "Telephone": "telephone",
        "radio": "radio",
        "Radio": "radio",
        "Localisation": "localisation",
        "Lieu": "lieu",
        "Porte": "porte",
        "Zone": "zone",
        "Immatriculation": "plaque",
        "Badge": "badge",
        "Entreprise": "entreprise",
        "Personne": "personne",
        "Nom": "nom",
        "Prénom": "prenom",
        "Prenom": "prenom",
        "Quantité": "quantite",
        "Quantite": "quantite",
    }
    if n in repl:
        return repl[n]
    n2 = re.sub(r'(?<!^)(?=[A-Z])', '_', n).lower()
    n2 = n2.replace("intervenant_", "intervenant")
    return n2


def xml_cast_value(k, v):
    if v is None:
        return None
    s = v.strip()
    if s == "":
        return None
    if s.lower() in ("true", "false", "oui", "non"):
        return s.lower() in ("true", "oui")
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except ValueError:
            pass
    if "date" in k or "heure" in k:
        _, iso = to_iso_dt(s)
        return iso or s
    return s


def parse_namevalue_xml(xml_text):
    out = {}
    if not xml_text:
        return out
    try:
        root = etree.fromstring(
            xml_text.encode("utf-8"),
            parser=etree.XMLParser(recover=True),
        )
        for nv in root.findall(".//NameValue"):
            name_elt = nv.find("Name")
            value_elt = nv.find("Value")
            name = (name_elt.text if name_elt is not None else "")
            value = (value_elt.text if value_elt is not None else "")
            key = xml_key_normalize(name)
            val = xml_cast_value(key, value)
            if key:
                if key in out:
                    if isinstance(out[key], list):
                        if val not in out[key]:
                            out[key].append(val)
                    else:
                        if val != out[key]:
                            out[key] = [out[key], val]
                else:
                    out[key] = val
    except Exception:
        pass
    return out


def extract_structured_from_xml(xml_fields):
    caller = {}
    flags = {}
    classification = {}
    intervenants = []
    resource = {}
    service_contacte = None
    reported_at = None

    if xml_fields.get("appelant"):
        caller["appelant"] = xml_fields["appelant"]
    if "telephone" in xml_fields:
        flags["telephone"] = bool(xml_fields["telephone"])
    if "radio" in xml_fields:
        flags["radio"] = bool(xml_fields["radio"])
    if xml_fields.get("sous_classification"):
        classification["sous"] = xml_fields["sous_classification"]
    if xml_fields.get("classification"):
        classification["principale"] = xml_fields["classification"]
    if xml_fields.get("motif_intervention"):
        classification["motif"] = xml_fields["motif_intervention"]
    for i in range(1, 6):
        v = xml_fields.get(f"intervenant{i}")
        if v:
            intervenants.append(v)
    if xml_fields.get("carroye"):
        resource["carroye"] = xml_fields["carroye"]
    if xml_fields.get("service_contacte"):
        service_contacte = xml_fields["service_contacte"]
    if xml_fields.get("date_heure"):
        reported_at = xml_fields["date_heure"]

    return {
        "caller": caller or None,
        "flags": flags or None,
        "classification": classification or None,
        "intervenants": intervenants or None,
        "resource": resource or None,
        "service_contacte": service_contacte,
        "reported_at": reported_at,
    }


def parse_gps(video_field):
    """Parse le champ UserMessageVideo qui contient des coordonnées GPS
    au format 'lon_fr;lat_fr' (virgule française comme séparateur décimal)."""
    if not video_field:
        return None
    parts = video_field.split(";")
    if len(parts) != 2:
        return None
    try:
        lon = float(parts[0].replace(",", "."))
        lat = float(parts[1].replace(",", "."))
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            return {
                "type": "Point",
                "coordinates": [lon, lat],
            }
    except (ValueError, TypeError):
        pass
    return None


# Comment history parsing
COMMENT_ENTRY_RE = re.compile(
    r'(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})\s*,\s*(.+?)\s*\n(.*?)(?=\d{2}/\d{2}/\d{4}|\Z)',
    re.DOTALL,
)


def parse_comment_history(comment):
    """Parse le champ comment brut en liste d'entrees chronologiques.
    Retourne [{"ts": "ISO", "operator": "Nom", "text": "message"}, ...]
    """
    if not comment:
        return []
    entries = []
    for m in COMMENT_ENTRY_RE.finditer(comment):
        ts_raw, operator, text = m.group(1), m.group(2).strip(), m.group(3).strip()
        # Parse DD/MM/YYYY HH:MM:SS -> ISO
        ts_iso = None
        try:
            dt = datetime.strptime(ts_raw, "%d/%m/%Y %H:%M:%S")
            dt = dt.replace(tzinfo=PARIS_TZ)
            ts_iso = dt.isoformat()
        except ValueError:
            ts_iso = ts_raw
        entries.append({
            "ts": ts_iso,
            "operator": operator,
            "text": text,
        })
    return entries


# Regex extraction
PHONE_RE = re.compile(r'(?:\+33|0)\s*[1-9](?:[\s\.-]*\d{2}){4}')
PLATE_RE = re.compile(
    r'\b([A-Z]{2}-\d{3}-[A-Z]{2}|\d{1,4}\s?[A-Z]{1,3}\s?\d{2,3})\b'
)


def extract_entities(text):
    phones = set()
    plates = set()
    if not text:
        return [], []
    for m in PHONE_RE.finditer(text):
        num = re.sub(r'\D', '', m.group(0))
        if num.startswith('33'):
            num = '0' + num[2:]
        phones.add(num)
    for m in PLATE_RE.finditer(text.upper()):
        plates.add(m.group(1))
    return sorted(phones), sorted(plates)


def mk_uuid(event_label, year, datecreate, category, description, area_id,
             user_id_create):
    seed = (
        f"{event_label}|{year}|{(datecreate or '').strip()}"
        f"|{(category or '').strip()}|{(description or '').strip()}"
        f"|{(area_id or '').strip()}|{(user_id_create or '').strip()}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


# ─── Curseur de sync ─────────────────────────────────────────────────────────

def get_sync_cursor(mongo_col):
    """Récupère le dernier DateWrite synchronisé (curseur global)."""
    # Chercher le curseur global
    doc = mongo_col.find_one({"_id": "sync_all"})
    if doc:
        return doc.get("last_date_write")
    # Migration : chercher le max des anciens curseurs par event/year
    all_cursors = list(mongo_col.find({"_id": {"$regex": "^sync_"}}))
    if all_cursors:
        max_dw = max(
            (c.get("last_date_write") for c in all_cursors if c.get("last_date_write")),
            default=None,
        )
        if max_dw:
            print(f"  Migration : curseur global initialisé depuis anciens curseurs ({max_dw})")
            return max_dw
    return None


def set_sync_cursor(mongo_col, last_date_write):
    """Met à jour le curseur de sync global."""
    mongo_col.update_one(
        {"_id": "sync_all"},
        {"$set": {
            "last_date_write": last_date_write,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


# ─── Requête SQL paginée ────────────────────────────────────────────────────

def build_category_filter():
    """Construit la clause WHERE pour filtrer les catégories voulues."""
    conditions = []
    for prefix in CATEGORIES_PREFIXES:
        conditions.append(f"UserMessageCategory LIKE '{prefix}%'")
    return " OR ".join(conditions)


def build_select_columns():
    """Construit la liste des colonnes SELECT avec CAST pour les datetimeoffset."""
    # datetimeoffset n'est pas supporté par pyodbc, on les convertit en varchar
    datetimeoffset_cols = {"UserMessageDateCreate", "UserMessageDateClose"}
    parts = []
    for col in SQL_COLUMNS:
        if col in datetimeoffset_cols:
            parts.append(f"CONVERT(varchar(50), {col}, 127) AS {col}")
        else:
            parts.append(col)
    return ", ".join(parts)


def fetch_messages_batch(conn, last_date_write=None, offset=0, batch_size=SQL_BATCH_SIZE):
    """Récupère un lot de messages SQL, paginé par OFFSET/FETCH."""
    cols = build_select_columns()
    cat_filter = build_category_filter()

    query = f"SELECT {cols} FROM dbo.UserMessages WHERE ({cat_filter})"
    params = []

    if last_date_write:
        query += " AND DateWrite > ?"
        params.append(last_date_write)

    query += f" ORDER BY DateWrite ASC OFFSET {offset} ROWS FETCH NEXT {batch_size} ROWS ONLY"

    cur = conn.cursor()
    cur.execute(query, params)
    columns = [desc[0] for desc in cur.description]
    rows = []
    for row in cur.fetchall():
        rows.append(dict(zip(columns, row)))
    return rows


# ─── Transformation ─────────────────────────────────────────────────────────

def transform_row(row, event_label, year):
    """Transforme une ligne SQL en document MongoDB (même format que l'import CSV)."""
    # Champs bruts
    msg_id = row.get("UserMessageId")
    msg_guid = clean_null(row.get("UserMessageGuid"))
    datecreate_raw = row.get("UserMessageDateCreate")
    dateclose_raw = row.get("UserMessageDateClose")
    alarm_id = clean_null(row.get("AlarmId"))
    area_id = clean_null(row.get("AreaId"))
    area_desc = clean_null(row.get("UserMessageAreaDesc"))
    description = decode_text(clean_null(row.get("UserMessageDescription")))
    group_names = clean_null(row.get("UserMessageGroupNames"))
    group_desc = clean_null(row.get("UserMessageGroupDesc"))
    attachments = clean_null(row.get("UserMessageAttachments"))
    photo = clean_null(row.get("UserMessagePhoto"))
    video_raw = clean_null(row.get("UserMessageVideo"))
    user_id_create = clean_null(row.get("UserIdCreate"))
    user_id_close = clean_null(row.get("UserIdClose"))
    user_name_create = clean_null(row.get("UserNameCreate"))
    user_name_close = clean_null(row.get("UserNameClose"))
    severity_raw = row.get("UserMessageSeverity")
    status_raw = row.get("UserMessageStatus")
    to_field = clean_null(row.get("UserMessageTo"))
    category = clean_null(row.get("UserMessageCategory"))
    comment = decode_text(clean_null(row.get("UserMessageComment")))
    content_cat_raw = decode_xml_text(clean_null(row.get("UserMessageContentCategory")))
    content_ref_raw = decode_xml_text(clean_null(row.get("UserMessageContentReferences")))
    is_incident_raw = row.get("UserMessageIsIncident")
    extension = clean_null(row.get("UserMessageExtension"))
    custom_ext = clean_null(row.get("UserMessageCustomExtension"))
    server = clean_null(row.get("ServerName"))
    date_write = row.get("DateWrite")

    # Timestamps
    ts_dt, ts_iso = to_iso_dt(datecreate_raw)
    close_dt, close_iso = to_iso_dt(dateclose_raw)

    # Date locale
    date_local = time_local = None
    if ts_dt:
        dt_local = (
            ts_dt.astimezone(PARIS_TZ) if ts_dt.tzinfo
            else ts_dt.replace(tzinfo=PARIS_TZ)
        )
        date_local = dt_local.date().isoformat()
        time_local = dt_local.time().isoformat(timespec="seconds")

    # Sévérité / statut
    try:
        severity_i = int(severity_raw) if severity_raw is not None else None
    except (ValueError, TypeError):
        severity_i = None
    try:
        status_i = int(status_raw) if status_raw is not None else None
    except (ValueError, TypeError):
        status_i = None

    # Incident
    is_incident = None
    if is_incident_raw is not None:
        is_incident = bool(int(is_incident_raw)) if isinstance(
            is_incident_raw, (int, float)
        ) else str(is_incident_raw).lower() in ("true", "1", "yes", "oui")

    # Parse XML
    xml_cat = parse_namevalue_xml(content_cat_raw) if content_cat_raw else {}
    xml_ref = parse_namevalue_xml(content_ref_raw) if content_ref_raw else {}

    # text_full
    extra_text = xml_cat.get("texte") if "texte" in xml_cat else None
    text_full = description or ""
    if extra_text and extra_text not in text_full:
        text_full = (text_full + "\n" + extra_text) if text_full else extra_text

    # Structured XML
    xml_struct = extract_structured_from_xml(xml_cat)

    # GPS depuis le champ vidéo
    gps = parse_gps(video_raw)

    # Extraction entités
    phones, plates = extract_entities(
        (text_full or "") + "\n" + (comment or "")
    )

    # ID stable
    datecreate_str = ts_iso or str(datecreate_raw or "")
    _id = mk_uuid(
        event_label or "SAISON", year or 0, datecreate_str,
        category or "", description or "",
        str(area_id) if area_id else "", str(user_id_create) if user_id_create else "",
    )

    doc = {
        "_id": _id,
        "event": event_label,
        "year": year,
        "sql_id": msg_id,
        "guid": msg_guid,
        "ts": ts_dt,
        "timestamp_iso": ts_iso,
        "close_ts": close_dt,
        "close_iso": close_iso,
        "date_local": date_local,
        "time_local": time_local,
        "source": category,
        "category": category,
        "to": to_field,
        "operator": user_name_create,
        "operator_close": user_name_close,
        "operator_id_create": (
            int(user_id_create) if user_id_create else None
        ),
        "operator_id_close": (
            int(user_id_close) if user_id_close else None
        ),
        "text": description,
        "text_full": text_full or None,
        "comment": comment,
        "comment_history": parse_comment_history(comment),
        "severity": severity_i,
        "status_code": status_i,
        "is_incident": is_incident,
        "alarm_id": int(alarm_id) if alarm_id and str(alarm_id).isdigit() else None,
        "area": (
            {"id": int(area_id) if str(area_id).isdigit() else area_id,
             "desc": area_desc}
            if (area_id or area_desc) else None
        ),
        "group": (
            {"names": group_names, "desc": group_desc}
            if (group_names or group_desc) else None
        ),
        "attachments": attachments,
        "photo": photo,
        "gps": gps,
        "content_category": xml_cat or None,
        "content_category_xml": clean_null(row.get("UserMessageContentCategory")),
        "content_references": xml_ref or None,
        "content_references_xml": clean_null(row.get("UserMessageContentReferences")),
        "xml_struct": xml_struct or None,
        "extracted": {
            "phones": phones or None,
            "plates": plates or None,
        },
        "extension": extension,
        "custom_extension": custom_ext,
        "server": server,
        "date_write": date_write,
        "synced_at": datetime.now(timezone.utc),
        "tags": [],
    }

    return doc


# ─── Main ────────────────────────────────────────────────────────────────────

def _gen_id():
    return uuid.uuid4().hex[:8]


def enrich_pcorg_config(mongo_db, pcorg_col):
    """Decouvre les nouvelles sous-classifications, intervenants et services
    dans pcorg et les ajoute automatiquement a pcorg_config."""
    config_col = mongo_db["pcorg_config"]
    doc = config_col.find_one({"_id": "pcorg_lists"})
    if not doc:
        doc = {"_id": "pcorg_lists", "sous_classifications": {},
               "intervenants": [], "services": []}

    sc = doc.get("sous_classifications") or {}
    interv = doc.get("intervenants") or []
    services = doc.get("services") or []

    # Extraire les labels existants pour comparaison rapide
    def existing_labels(items):
        return {(it["label"] if isinstance(it, dict) else it) for it in items}

    interv_set = existing_labels(interv)
    service_set = existing_labels(services)
    sc_sets = {cat: existing_labels(items) for cat, items in sc.items()}

    added = 0

    # Scanner les sous-classifications par categorie
    pipeline_sc = [
        {"$match": {"category": {"$regex": "^PCO"},
                     "content_category.sous_classification": {"$ne": None, "$ne": ""}}},
        {"$group": {"_id": {"cat": "$category",
                            "sc": "$content_category.sous_classification"}}},
    ]
    for row in pcorg_col.aggregate(pipeline_sc):
        cat = row["_id"].get("cat")
        val = row["_id"].get("sc")
        if not cat or not val:
            continue
        if cat not in sc:
            sc[cat] = []
            sc_sets[cat] = set()
        if val not in sc_sets[cat]:
            sc[cat].append({"id": _gen_id(), "label": val})
            sc_sets[cat].add(val)
            added += 1

    # Scanner les intervenants (intervenant1-5)
    for i in range(1, 6):
        field = f"content_category.intervenant{i}"
        pipeline_int = [
            {"$match": {"category": {"$regex": "^PCO"}, field: {"$ne": None, "$ne": ""}}},
            {"$group": {"_id": f"${field}"}},
        ]
        for row in pcorg_col.aggregate(pipeline_int):
            val = row["_id"]
            if val and val not in interv_set:
                interv.append({"id": _gen_id(), "label": val})
                interv_set.add(val)
                added += 1

    # Scanner les moyens engages (niveau 1 et 2, meme liste)
    for field_name in ["content_category.moyens_engages_niveau_1",
                       "content_category.moyens_engages_niveau_2"]:
        pipeline_m = [
            {"$match": {"category": {"$regex": "^PCO"}, field_name: {"$ne": None, "$ne": ""}}},
            {"$group": {"_id": f"${field_name}"}},
        ]
        for row in pcorg_col.aggregate(pipeline_m):
            val = row["_id"]
            if val and val not in interv_set:
                interv.append({"id": _gen_id(), "label": val})
                interv_set.add(val)
                added += 1

    # Scanner les services contactes
    pipeline_svc = [
        {"$match": {"category": {"$regex": "^PCO"},
                     "content_category.service_contacte": {"$ne": None, "$ne": ""}}},
        {"$group": {"_id": "$content_category.service_contacte"}},
    ]
    for row in pcorg_col.aggregate(pipeline_svc):
        val = row["_id"]
        if val and val not in service_set:
            services.append({"id": _gen_id(), "label": val})
            service_set.add(val)
            added += 1

    if added > 0:
        # Trier par label
        for cat in sc:
            sc[cat].sort(key=lambda x: (x["label"] if isinstance(x, dict) else x))
        interv.sort(key=lambda x: (x["label"] if isinstance(x, dict) else x))
        services.sort(key=lambda x: (x["label"] if isinstance(x, dict) else x))

        config_col.update_one(
            {"_id": "pcorg_lists"},
            {"$set": {
                "sous_classifications": sc,
                "intervenants": interv,
                "services": services,
            }},
            upsert=True,
        )
        print(f"  Config pcorg enrichie : {added} nouvelle(s) valeur(s) ajoutee(s)")
    else:
        print(f"  Config pcorg : aucune nouvelle valeur")


def main():
    ap = argparse.ArgumentParser(
        description="Sync PC Organisation : SQL Server → MongoDB (auto-attribution événement)"
    )
    ap.add_argument("--mongo", default=MONGO_URI, help="URI MongoDB")
    ap.add_argument(
        "--full", action="store_true",
        help="Resync complet (ignore le curseur de dernier sync)",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Ne pas écrire en base, afficher le résumé",
    )
    args = ap.parse_args()

    # ── Connexion SQL Server ──
    sql_user = os.getenv("MSSQL_USER")
    if not sql_user:
        sql_user = input("MSSQL_USER non défini, login SQL : ").strip()
    sql_password = os.getenv("MSSQL_PASSWORD")
    if not sql_password:
        sql_password = input("Mot de passe SQL Server : ")

    # ── Connexion MongoDB ──
    print("Connexion MongoDB...")
    mongo_client = MongoClient(args.mongo)
    mongo_db = mongo_client[MONGO_DB]
    pcorg_col = mongo_db[MONGO_COLLECTION]
    sync_col = mongo_db[MONGO_SYNC_COLLECTION]

    # ── Charger les plages événements ──
    print("Chargement des événements depuis titan.parametrages...")
    plages = charger_plages_evenements(mongo_client)
    if not plages:
        print("  ATTENTION : aucun événement trouvé dans parametrages !")
        print("  Les messages seront stockés avec event=None, year=None")
    else:
        print(f"  {len(plages)} événement(s) chargé(s) :")
        for p in plages:
            print(f"    {p['event']} {p['year']} : "
                  f"{p['start'].strftime('%d/%m/%Y')} → {p['end'].strftime('%d/%m/%Y')}")

    print(f"\n{'='*60}")
    print(f"Sync PC Organisation → MongoDB")
    print(f"  Catégories : {', '.join(CATEGORIES_PREFIXES)}")
    print(f"  Mode : {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print(f"  Sync : {'COMPLET' if args.full else 'INCRÉMENTAL'}")
    print(f"{'='*60}")

    # Index
    if not args.dry_run:
        try:
            pcorg_col.create_index([("event", 1), ("year", 1), ("ts", 1)])
            pcorg_col.create_index([("event", 1), ("year", 1), ("category", 1)])
            pcorg_col.create_index([("event", 1), ("year", 1), ("area.id", 1)])
            pcorg_col.create_index([("event", 1), ("year", 1), ("sql_id", 1)])
            pcorg_col.create_index([("guid", 1)], sparse=True)
            pcorg_col.create_index([("gps", "2dsphere")], sparse=True)
        except Exception as e:
            print(f"  [WARN] Index : {e}")

    # ── Curseur de sync ──
    last_date_write = None
    if not args.full:
        last_date_write = get_sync_cursor(sync_col)
        if last_date_write:
            print(f"\nDernier sync : {last_date_write}")
        else:
            print("\nPremier sync (aucun curseur trouvé)")
    else:
        print("\nSync complet demandé")

    # ── Connexion SQL Server ──
    print("Connexion SQL Server...")
    sql_conn = connect_sql(sql_user, sql_password)

    # ── Récupération et traitement par lots ──
    total_sql = 0
    total_upserted = 0
    max_date_write = last_date_write
    cat_counts = Counter()
    event_counts = Counter()
    offset = 0

    print(f"Récupération par lots de {SQL_BATCH_SIZE}...")

    while True:
        batch = fetch_messages_batch(sql_conn, last_date_write, offset, SQL_BATCH_SIZE)
        if not batch:
            break

        batch_size = len(batch)
        total_sql += batch_size
        ops = []
        batch_max_dw = None

        for row in batch:
            # Determiner le(s) evenement(s) a partir de la date de creation
            ts_dt, _ = to_iso_dt(row.get("UserMessageDateCreate"))
            evts = trouver_evenements(ts_dt, plages)

            for evt, yr in evts:
                doc = transform_row(row, evt, yr)
                cat_counts[doc.get("category", "?")] += 1
                event_counts[f"{evt} {yr}"] += 1

                if not args.dry_run:
                    ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": doc}, upsert=True))
                else:
                    total_upserted += 1

            dw = row.get("DateWrite")
            if dw and (batch_max_dw is None or dw > batch_max_dw):
                batch_max_dw = dw

        # Upsert le lot
        if not args.dry_run and ops:
            res = pcorg_col.bulk_write(ops, ordered=False)
            total_upserted += (res.upserted_count or 0) + (res.modified_count or 0)

        # Mise à jour du curseur après chaque lot (reprise possible)
        if batch_max_dw and (max_date_write is None or batch_max_dw > max_date_write):
            max_date_write = batch_max_dw
        if not args.dry_run and max_date_write:
            set_sync_cursor(sync_col, max_date_write)

        print(f"  Lot {offset // SQL_BATCH_SIZE + 1} : {batch_size} messages traités "
              f"(total: {total_sql})")

        # Si on a reçu moins que la taille du lot, c'est le dernier
        if batch_size < SQL_BATCH_SIZE:
            break

        offset += SQL_BATCH_SIZE

    sql_conn.close()

    if total_sql == 0:
        print("\nAucun nouveau message. Rien à synchroniser.")
        return

    # ── Résumé ──
    print(f"\n{'='*60}")
    print(f"Sync terminé")
    print(f"  Messages SQL récupérés : {total_sql}")
    print(f"  Documents {'préparés' if args.dry_run else 'upsertés'} : {total_upserted}")
    print(f"  Répartition par événement :")
    for evt_key, cnt in event_counts.most_common():
        print(f"    {evt_key}: {cnt}")
    print(f"  Répartition par catégorie :")
    for cat, cnt in cat_counts.most_common():
        print(f"    {cat}: {cnt}")
    if max_date_write:
        print(f"  Curseur DateWrite : {max_date_write}")

    # Nettoyage des doublons SAISON (messages réattribués à un vrai événement)
    if not args.dry_run:
        dup_pipeline = [
            {"$match": {"sql_id": {"$ne": None}}},
            {"$group": {
                "_id": "$sql_id", "count": {"$sum": 1},
                "docs": {"$push": {"id": "$_id", "event": "$event"}},
            }},
            {"$match": {"count": {"$gt": 1}}},
        ]
        saison_to_delete = []
        for dup in pcorg_col.aggregate(dup_pipeline):
            has_non_saison = any(d["event"] != "SAISON" for d in dup["docs"])
            if has_non_saison:
                for d in dup["docs"]:
                    if d["event"] == "SAISON":
                        saison_to_delete.append(d["id"])
        if saison_to_delete:
            res = pcorg_col.delete_many({"_id": {"$in": saison_to_delete}})
            print(f"  Doublons SAISON purges : {res.deleted_count}")

    # Enrichir la config pcorg avec les nouvelles valeurs decouvertes
    if not args.dry_run:
        enrich_pcorg_config(mongo_db, pcorg_col)

    # Comptage total en base
    if not args.dry_run:
        total = pcorg_col.count_documents({})
        with_gps = pcorg_col.count_documents({"gps": {"$ne": None}})
        with_event = pcorg_col.count_documents({"event": {"$ne": None}})
        print(f"  Total en base : {total} docs ({with_event} attribués, {with_gps} avec GPS)")

    print(f"{'='*60}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompu.")
        sys.exit(0)
    except pyodbc.Error as e:
        print(f"\n[ERREUR SQL/ODBC] {e}")
        sys.exit(3)
    except Exception as e:
        print(f"\n[ERREUR] {e}")
        sys.exit(4)
