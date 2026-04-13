#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_controle.py — Script unifié de collecte Handshake
Piloté par le document ___GLOBAL___ dans MongoDB (collection data_access).
Lancé par Windows Task Scheduler toutes les 2 minutes, ou à la demande via API.

Trois fonctions combinées :
  1. Inventaire Counter global (première exécution après activation)
  2. Collecte paginée des transactions → erreurs + arbre des locations
  3. Polling Counter pour les locations sélectionnées depuis le front
"""

import socket
import xml.etree.ElementTree as ET
import xml.dom.minidom
from pymongo import MongoClient, UpdateOne
import datetime
import time
import zoneinfo
import json
import os
import sys

DEV_MODE = "--dev" in sys.argv

# =========================================================
#                    CONFIGURATION
# =========================================================
HSH_IP = "192.168.2.10"
HSH_PORT = 5205
TELEGRAM_ID = (1234).to_bytes(4, "big")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_TITAN_ENV = os.getenv("TITAN_ENV", "dev").strip().lower()
DB_NAME = "titan" if _TITAN_ENV in {"prod", "production"} else "titan_dev"

CONNECT_TIMEOUT = 5
READ_TIMEOUT_COUNTER = 5
READ_TIMEOUT_TRANSACTIONS = 15

MAX_TX = 100
# bit 7 (128) = nouveau layout XML, bit 8 (256) = inclure erreurs
OPTION_TRANSACTIONS = 128 + 256  # 384

TZ_PARIS = zoneinfo.ZoneInfo("Europe/Paris")

TASK_NAME = "Live controle acces"

# =========================================================
#     TABLES DE CORRESPONDANCE (spec HSHIF25 v2.34)
# =========================================================
CODING_LABELS = {
    "0": "Code-barres standard 2 of 5",
    "1": "Code-barres interleaved 2 of 5",
    "256": "Code-barres visible Standard 2 of 5",
    "257": "Code-barres visible Interleaved 2 of 5",
    "258": "Code-barres visible UPC",
    "259": "Code-barres visible PDF417 (2D)",
    "260": "Code-barres visible EAN",
    "261": "Code-barres visible Code 39",
    "262": "Code-barres visible Code 128",
    "263": "Code-barres visible Codabar",
    "267": "Code-barres visible DataMatrix (2D)",
    "270": "Code-barres visible QR Code (2D)",
    "274": "Code-barres visible Aztec (2D)",
    "16777217": "RFID Flexspace (ISO 14443/15693)",
    "16777222": "RFID HID iCLASS",
    "16777223": "RFID Felica",
    "16777229": "RFID Generic ISO 15693",
    "16777231": "RFID Calypso",
    "16777235": "RFID NFC generique",
    "16777237": "RFID EM4036 Shared Chip",
    "16777241": "RFID Keycard ECO ISO DUAL",
    "16777242": "RFID Keyticket ISO DUAL",
    "16777245": "RFID Keycard ISO (ISO 15693)",
    "16777246": "RFID Keycard ISO DUAL (ISO 15693)",
    "16777348": "RFID Mifare/MyD (ISO 14443A)",
    "16777349": "RFID Mifare (ISO 14443B)",
}

STATUS_LABELS = {
    "0": "OK",
    "31": "Ticket illisible",
    "32": "Donnees ticket inconnues",
    "33": "Type de ticket inconnu",
    "34": "Erreur systeme interne / Ticket non autorise",
    "35": "Ticket non autorise",
    "36": "Verification etendue echouee",
    "37": "Ticket manipule",
    "38": "Erreur ecriture ticket (RFID)",
    "51": "Carte de controle acceptee",
    "52": "Carte de controle inconnue",
    "53": "Mise en page impression manquante ou invalide",
    "54": "Ticket insere a l'envers dans le lecteur",
    "61": "Ouverture manuelle",
    "62": "Passage en mode libre (ticket presente)",
    "63": "Passage force",
    "64": "Passage avec liberation supplementaire",
    "65": "Passage avec ticket groupe",
    "66": "Passage en mode ouverture permanente",
    "103": "Acces non autorise (permission manquante)",
    "104": "Acces refuse (refus configure)",
    "105": "Ticket journee/longue duree expire",
    "106": "Pas de WhitelistRecord",
    "107": "Checkpoint hors ligne / Entree non autorisee",
    "108": "Type de permission inconnu",
    "109": "Verification de validite echouee (ticket expire)",
    "110": "Venue : nombre max d'entrees depasse",
    "111": "Venue : delai de re-entree non ecoule",
    "112": "Venue : delai anti-double usage (entree) non ecoule",
    "113": "Venue : delai de re-sortie non ecoule",
    "114": "Venue : delai anti-double usage (sortie) non ecoule",
    "115": "Area : nombre max d'entrees depasse",
    "116": "Area : delai de re-entree non ecoule",
    "117": "Area : delai anti-double usage (entree) non ecoule",
    "118": "Area : delai de re-sortie non ecoule",
    "119": "Area : delai anti-double usage (sortie) non ecoule",
    "120": "Evenement : nombre max d'entrees depasse",
    "121": "Evenement : delai de re-entree non ecoule",
    "122": "Evenement : delai anti-double usage (entree) non ecoule",
    "123": "Evenement : delai de re-sortie non ecoule",
    "124": "Evenement : delai anti-double usage (sortie) non ecoule",
    "125": "Venue : max entrees du jour depasse",
    "126": "Area : max entrees du jour depasse",
    "127": "Evenement : max entrees du jour depasse",
    "128": "Ticket bloque (data carrier)",
    "129": "Permission bloquee",
    "130": "Condition non remplie",
    "131": "Credit points insuffisant",
    "132": "Ticket a points epuise",
    "133": "Entree sans sortie prealable",
    "134": "Annulation de liberation precedente",
    "135": "Ticket temps : duree depassee",
    "136": "Pas un ticket temps",
    "137": "Ticket temps : deja sorti",
    "138": "Ticket temps : deja entre",
    "139": "Ticket temps : pas encore entre",
    "140": "Verification informative OK",
    "141": "Sortie impossible",
    "142": "Ticket deja annule",
    "145": "Permission expiree",
    "150": "Compteur : limite atteinte",
    "175": "Carte de controle : ouverture manuelle",
    "200": "Erreur enregistrement, ticket non valide",
    "201": "Licences insuffisantes",
}

VALIDATED_LABELS = {
    "0": "En ligne (valide par le serveur HSH)",
    "1": "Hors ligne (valide par le lecteur local)",
}


def _label_coding(coding):
    if coding is None:
        return None
    return CODING_LABELS.get(str(coding), f"Coding inconnu ({coding})")


def _label_status(status):
    if status is None:
        return None
    return STATUS_LABELS.get(str(status), f"Status inconnu ({status})")


def _label_validated(validated):
    if validated is None:
        return None
    return VALIDATED_LABELS.get(str(validated), f"Validated inconnu ({validated})")

# =========================================================
#                  CRON STATUS
# =========================================================
def _status_path():
    path = os.getenv("CRON_STATUS_FILE", "").strip()
    if path:
        return path
    return os.path.join(os.path.dirname(__file__), "cron_status.json")


def _update_cron_status(status, message=""):
    path = _status_path()
    tasks = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            tasks = data if isinstance(data, list) else data.get("tasks", [])
        except Exception:
            tasks = []
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    updated = False
    for task in tasks:
        if task.get("name") == TASK_NAME:
            task["status"] = status
            task["last_run"] = now
            if message:
                task["message"] = message
            else:
                task.pop("message", None)
            updated = True
            break
    if not updated:
        entry = {"name": TASK_NAME, "status": status, "last_run": now}
        if message:
            entry["message"] = message
        tasks.append(entry)
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(tasks, handle, indent=2)
    os.replace(tmp_path, path)


# =========================================================
#                  MONGODB
# =========================================================
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[DB_NAME]
col_data_access = db["data_access"]
col_structure = db["hsh_structure"]
col_erreurs = db["hsh_erreurs"]
col_tx_agg = db["hsh_transactions_agg"]
col_agg_titres = db["hsh_agg_titres"]
col_barcodes_utids = db["access_barcodes_utids"]

GLOBAL_ID = "___GLOBAL___"

GLOBAL_DEFAULTS = {
    "_id": GLOBAL_ID,
    "live_controle_actif": False,
    "activation_timestamp": None,
    "evenement": "",
    "evenement_clean": "",
    "locations_selectionnees": [],
    "dernier_inventaire": None,
    "dernier_transaction_id": None,
    "dernier_cycle": None,
}


def charger_cache_titres():
    """Charge le mapping utid -> title depuis access_barcodes_utids."""
    cache = {}
    for doc in col_barcodes_utids.find({}, {"utid": 1, "title": 1}):
        utid = doc.get("utid")
        if utid:
            cache[utid] = doc.get("title", "")
    return cache


def categoriser_scan(utid, cache_titres, upid=None):
    """Retourne 'enfant', 'vehicule', 'accredite' ou 'personne'."""
    if upid and upid.endswith("-ACCRED"):
        return "accredite"
    if utid and utid.startswith("EWC"):
        return "accredite"
    if utid and utid in cache_titres:
        title = cache_titres[utid]
        if "Bracelet Enfant" in title:
            return "enfant"
        return "vehicule"
    return "personne"


def lire_global():
    doc = col_data_access.find_one({"_id": GLOBAL_ID})
    if doc is None:
        col_data_access.insert_one(GLOBAL_DEFAULTS.copy())
        doc = GLOBAL_DEFAULTS.copy()
    return doc


def maj_global(champs: dict):
    col_data_access.update_one({"_id": GLOBAL_ID}, {"$set": champs})


def est_premiere_execution(doc):
    inv = doc.get("dernier_inventaire")
    act = doc.get("activation_timestamp")
    if inv is None:
        return True
    if act is not None and inv < act:
        return True
    return False


def assurer_index():
    col_erreurs.create_index([("checkpoint.id", 1), ("date_paris", -1)])
    col_erreurs.create_index([("evenement", 1), ("date_paris", -1)])
    col_structure.create_index([("location_type", 1)])
    col_tx_agg.create_index([("evenement", 1), ("checkpoint_id", 1), ("tranche", -1)])
    col_tx_agg.create_index("tranche", expireAfterSeconds=30 * 24 * 3600)  # TTL 30 jours
    col_agg_titres.create_index([("evenement", 1), ("titre", 1), ("tranche", -1)])
    col_agg_titres.create_index("tranche", expireAfterSeconds=30 * 24 * 3600)
    col_structure.create_index([("evenement", 1)])


# =========================================================
#              COUCHE RÉSEAU TCP
# =========================================================
def _read_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connexion fermée pendant la lecture")
        buf.extend(chunk)
    return bytes(buf)


def _read_frame(sock):
    header = _read_exact(sock, 12)
    if header[:2] != b"\x10\x02":
        raise ValueError("Start Character inattendu (DLE STX manquant)")
    data_type = int.from_bytes(header[6:8], "big")
    xml_length = int.from_bytes(header[8:12], "big")
    payload = _read_exact(sock, xml_length) if xml_length > 0 else b""
    return data_type, payload


def _send_frame(sock, frame):
    sock.sendall(frame)


def _maybe_handle_keepalive(sock, data_type, payload):
    if data_type == 0 and payload.strip().upper() == b"KEEPALIVE":
        resp = b"RESPONSE"
        frame = (
            b"\x10\x02"
            + TELEGRAM_ID
            + (0).to_bytes(2, "big")
            + len(resp).to_bytes(4, "big")
            + resp
        )
        _send_frame(sock, frame)
        return True
    return False


def _encapsuler(xml_str, data_type_bytes):
    xml_bytes = xml_str.encode("utf-16-le")
    length = len(xml_bytes).to_bytes(4, "big")
    return b"\x10\x02" + TELEGRAM_ID + data_type_bytes + length + xml_bytes


def encapsuler_counter(xml_str):
    return _encapsuler(xml_str, b"\x00\x01")


def encapsuler_transactions(xml_str):
    return _encapsuler(xml_str, b"\x00\x03")


def envoyer_et_recevoir(sock, frame):
    """Envoie la trame, gère les KEEPALIVE, retourne le XML décodé."""
    _send_frame(sock, frame)
    while True:
        dt, payload = _read_frame(sock)
        if _maybe_handle_keepalive(sock, dt, payload):
            continue
        if payload:
            return payload.decode("utf-16-le", errors="ignore")
        return None


# =========================================================
#              CONSTRUCTEURS XML
# =========================================================
def build_counter_global_xml():
    return f"""<TSData>
    <Header>
        <Version>HSHIF25</Version>
        <Issuer>6</Issuer>
        <Receiver>1</Receiver>
        <ID>Counter_Global_{int(time.time())}</ID>
    </Header>
    <Inquiry Type="Counter"/>
</TSData>"""


def build_counter_location_xml(location_id, location_type):
    return f"""<TSData>
    <Header>
        <Version>HSHIF25</Version>
        <Issuer>6</Issuer>
        <Receiver>1</Receiver>
        <ID>Location_{location_id}</ID>
    </Header>
    <Inquiry Type="Counter">
        <Location Id="{location_id}" Type="{location_type}"/>
    </Inquiry>
</TSData>"""


def build_transactions_xml(from_dt=None, to_dt=None, last_tx_id=None):
    attrs = f'Type="Transactions" MaxTransactions="{MAX_TX}" Option="{OPTION_TRANSACTIONS}"'
    if from_dt:
        attrs += f' From="{from_dt}"'
    if to_dt:
        attrs += f' To="{to_dt}"'
    if last_tx_id is not None:
        attrs += f' LastTransactionId="{last_tx_id}"'
    return f"""<TSData>
    <Header>
        <Version>HSHIF25</Version>
        <Issuer>6</Issuer>
        <Receiver>1</Receiver>
        <ID>Tx_{int(time.time())}</ID>
    </Header>
    <Inquiry {attrs}/>
</TSData>"""


# =========================================================
#                    PARSERS
# =========================================================
def parse_counter_global(xml_str):
    """Parse tous les <Counter> d'un Inquiry Counter global.
    Retourne une liste de dicts."""
    root = ET.fromstring(xml_str)
    resultats = []
    for counter in root.findall(".//Counter"):
        loc = counter.find("Location")
        def _t(name):
            el = counter.find(name)
            return el.text if el is not None else None

        resultats.append({
            "counter_id": counter.get("Id"),
            "counter_name": counter.get("Name"),
            "counter_type": counter.get("Type"),
            "location_id": loc.get("Id") if loc is not None else None,
            "location_name": loc.get("Name") if loc is not None else None,
            "location_type": loc.get("Type") if loc is not None else None,
            "entries": _t("Entries"),
            "exits": _t("Exits"),
            "current": _t("Current"),
            "upper_limit": _t("UpperLimit"),
            "lower_limit": _t("LowerLimit"),
            "first_entries": _t("FirstEntries"),
            "first_entries_day": _t("FirstEntriesDay"),
            "locked": _t("Locked"),
        })
    return resultats


def parse_counter_single(xml_str):
    """Parse un Counter individuel (réponse à un Inquiry Counter filtré).
    Retourne un dict aplati conforme à data_access."""
    root = ET.fromstring(xml_str)
    ts = datetime.datetime.now(datetime.timezone.utc)
    data = {"timestamp": ts, "year": ts.year}

    counter = root.find(".//Counter")
    if counter is None:
        return None

    data["counter_id"] = counter.get("Id")
    data["counter_name"] = counter.get("Name")

    def _t(name):
        el = counter.find(name)
        return el.text if el is not None else "N/A"

    data["entries"] = _t("Entries")
    data["exits"] = _t("Exits")
    data["current"] = _t("Current")
    data["upper_limit"] = _t("UpperLimit")
    data["lower_limit"] = _t("LowerLimit")
    data["first_entries"] = _t("FirstEntries")
    data["first_entries_day"] = _t("FirstEntriesDay")

    locked = _t("Locked")
    data["locked"] = locked
    data["locked_status"] = "Ouvert" if locked == "0" else "Verrouille"

    loc = counter.find("Location")
    if loc is not None:
        data["location_name"] = loc.get("Name") or "Inconnu"
        data["location_type_found"] = loc.get("Type") or "Inconnu"
    else:
        data["location_name"] = "Inconnu"
        data["location_type_found"] = "Inconnu"

    return data


def _direction_from_nodes(area_elem, venue_elem):
    entry_val = exit_val = None
    if area_elem is not None:
        entry_val = area_elem.get("Entry")
        exit_val = area_elem.get("Exit")
    if (entry_val is None or exit_val is None) and venue_elem is not None:
        entry_val = entry_val if entry_val is not None else venue_elem.get("Entry")
        exit_val = exit_val if exit_val is not None else venue_elem.get("Exit")
    if entry_val == "1":
        return "Entree"
    if exit_val == "1":
        return "Sortie"
    return "Inconnu"


def _all_attribs(elem):
    """Retourne tous les attributs d'un élément XML sous forme de dict."""
    return dict(elem.attrib) if elem is not None else None


def parse_transactions(xml_str):
    """Parse les transactions d'une page en remontant la hiérarchie Ticket > Permission > Transaction.
    Retourne (not_complete, txs, max_txid)."""
    root = ET.fromstring(xml_str)

    inq = root.find(".//Inquiry")
    not_complete = False
    max_txid = None
    if inq is not None:
        not_complete = inq.get("NotComplete") == "1"
        raw = inq.get("MaxTransactionId")
        if raw is not None:
            try:
                max_txid = int(raw)
            except ValueError:
                pass

    txs = []

    # Parcours hiérarchique : Ticket > Permission > Transaction
    for ticket in root.findall(".//Ticket"):
        utid_el = ticket.find("UTID")
        coding_el = ticket.find("Coding")
        utid = utid_el.text if utid_el is not None else None
        coding = coding_el.text if coding_el is not None else None
        blocked = ticket.get("Blocked")

        for perm in ticket.findall("Permission"):
            upid_el = perm.find("UPID")
            upid = upid_el.text if upid_el is not None else None
            perm_blocked = perm.get("Blocked")

            for tx in perm.findall("Transaction"):
                txid = tx.get("TransactionId")
                date_str = tx.get("Date")
                date_utc = date_str
                date_paris = date_str

                if date_str:
                    try:
                        # Le HSH envoie l'heure locale (Paris), pas UTC
                        date_paris = datetime.datetime.strptime(
                            date_str, "%Y-%m-%dT%H:%M:%S"
                        ).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass

                venue = tx.find("Venue")
                area = tx.find("Area")
                gate = tx.find("Gate")
                cp = tx.find("Checkpoint")

                tx_doc = {
                    # Ticket parent
                    "utid": utid,
                    "coding": coding,
                    "ticket_blocked": blocked,
                    # Permission parent
                    "upid": upid,
                    "permission_blocked": perm_blocked,
                    # Transaction — tous les attributs
                    "transaction_id": int(txid) if txid and txid.isdigit() else txid,
                    "date_utc": date_utc,
                    "date_paris": date_paris,
                    "status": tx.get("Status"),
                    "validated": tx.get("Validated"),
                    "releases": tx.get("Releases"),
                    "test_mode": tx.get("TestMode"),
                    "direction": _direction_from_nodes(area, venue),
                    # Venue — tous les attributs
                    "venue": _all_attribs(venue),
                    # Area — tous les attributs
                    "area": _all_attribs(area),
                    # Gate — tous les attributs
                    "gate": _all_attribs(gate),
                    # Checkpoint — tous les attributs
                    "checkpoint": _all_attribs(cp),
                }

                # Attributs supplémentaires éventuels sur Transaction
                for attr_name in ("BelongsTo", "Timestamp"):
                    val = tx.get(attr_name)
                    if val is not None:
                        tx_doc[attr_name.lower()] = val

                # Conditions (si Option bit 18)
                conditions_el = tx.find("Conditions")
                if conditions_el is not None:
                    tx_doc["conditions"] = [
                        _all_attribs(c) for c in conditions_el.findall("Condition")
                    ]

                # ExtendedCheck (si Option bit 11)
                ext_check = tx.find("ExtendedCheck")
                if ext_check is not None:
                    tx_doc["extended_check"] = _all_attribs(ext_check)

                txs.append(tx_doc)

                # Fallback si MaxTransactionId absent (HSH < v2.26)
                try:
                    val = int(txid)
                except (TypeError, ValueError):
                    val = None
                if val is not None and (max_txid is None or val > max_txid):
                    max_txid = val

    # Fallback : transactions hors hiérarchie Ticket/Permission (ancien layout ou erreurs sans ticket)
    for tx in root.findall(".//Inquiry/Transaction"):
        txid = tx.get("TransactionId")
        # Vérifier qu'on ne l'a pas déjà parsée
        if any(t["transaction_id"] == (int(txid) if txid and txid.isdigit() else txid) for t in txs):
            continue

        date_str = tx.get("Date")
        date_utc = date_str
        date_paris = date_str
        if date_str:
            try:
                # Le HSH envoie l'heure locale (Paris), pas UTC
                date_paris = datetime.datetime.strptime(
                    date_str, "%Y-%m-%dT%H:%M:%S"
                ).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        venue = tx.find("Venue")
        area = tx.find("Area")
        gate = tx.find("Gate")
        cp = tx.find("Checkpoint")

        tx_doc = {
            "utid": None, "coding": None, "ticket_blocked": None,
            "upid": None, "permission_blocked": None,
            "transaction_id": int(txid) if txid and txid.isdigit() else txid,
            "date_utc": date_utc, "date_paris": date_paris,
            "status": tx.get("Status"), "validated": tx.get("Validated"),
            "releases": tx.get("Releases"), "test_mode": tx.get("TestMode"),
            "direction": _direction_from_nodes(area, venue),
            "venue": _all_attribs(venue), "area": _all_attribs(area),
            "gate": _all_attribs(gate), "checkpoint": _all_attribs(cp),
        }
        txs.append(tx_doc)

        try:
            val = int(txid)
        except (TypeError, ValueError):
            val = None
        if val is not None and (max_txid is None or val > max_txid):
            max_txid = val

    return not_complete, txs, max_txid


# =========================================================
#              LOGIQUE MÉTIER
# =========================================================
def executer_inventaire(sock, evenement):
    """Inquiry Counter global → upsert dans hsh_structure."""
    print("  Inventaire Counter global...")
    xml_req = build_counter_global_xml()
    frame = encapsuler_counter(xml_req)
    resp = envoyer_et_recevoir(sock, frame)
    if not resp:
        print("  Pas de reponse pour l'inventaire Counter.")
        return

    compteurs = parse_counter_global(resp)
    print(f"  {len(compteurs)} compteurs recus.")

    ts = datetime.datetime.now(datetime.timezone.utc)
    ops = []
    for c in compteurs:
        loc_type = c["location_type"] or "Unknown"
        loc_id = c["location_id"] or c["counter_id"]
        doc_id = f"{loc_type}_{loc_id}"

        ops.append(UpdateOne(
            {"_id": doc_id},
            {
                "$set": {
                    "location_id": loc_id,
                    "location_name": c["location_name"],
                    "location_type": loc_type,
                    "counter_id": c["counter_id"],
                    "counter_name": c["counter_name"],
                    "derniers_compteurs": {
                        "entries": c["entries"],
                        "exits": c["exits"],
                        "current": c["current"],
                        "first_entries": c["first_entries"],
                        "first_entries_day": c["first_entries_day"],
                        "upper_limit": c["upper_limit"],
                        "lower_limit": c["lower_limit"],
                        "locked": c["locked"],
                        "timestamp": ts,
                    },
                    "evenement": evenement,
                    "derniere_maj": ts,
                },
                # source, parent_*, enfants : ne sont écrits qu'à la création
                # les relations existantes déduites des transactions sont préservées
                "$setOnInsert": {"source": "inventaire"},
            },
            upsert=True,
        ))

    if ops:
        col_structure.bulk_write(ops, ordered=False)
        print(f"  {len(ops)} locations upsertees dans hsh_structure.")

    maj_global({"dernier_inventaire": ts})


def mettre_a_jour_arbre(txs, evenement):
    """Enrichit hsh_structure avec les relations parent-enfant deduites des transactions."""
    ts = datetime.datetime.now(datetime.timezone.utc)
    ops = []

    # Helper pour lire ID/Name depuis les dicts d'attributs XML (clés en majuscules)
    def _id(d):
        return d.get("ID") or d.get("Id") or d.get("id") if d else None
    def _name(d):
        return d.get("Name") or d.get("name") if d else None

    # Comptage des liens uniques déduits
    liens_cp_gate = set()
    liens_gate_area = set()
    liens_area_venue = set()

    for tx in txs:
        venue = tx.get("venue")
        area = tx.get("area")
        gate = tx.get("gate")
        cp = tx.get("checkpoint")

        # Tracer les liens uniques
        if _id(cp) and _id(gate):
            liens_cp_gate.add((_id(cp), _name(cp) or "?", _id(gate), _name(gate) or "?"))
        if _id(gate) and _id(area):
            liens_gate_area.add((_id(gate), _name(gate) or "?", _id(area), _name(area) or "?"))
        if _id(area) and _id(venue):
            liens_area_venue.add((_id(area), _name(area) or "?", _id(venue), _name(venue) or "?"))

        # Checkpoint → parents: Gate, Area, Venue
        if _id(cp):
            cp_doc_id = f"Checkpoint_{_id(cp)}"
            # Extraire le timestamp de la transaction pour traquer l'activite
            tx_date_str = tx.get("date_utc")
            tx_ts = None
            if tx_date_str:
                try:
                    tx_ts = datetime.datetime.strptime(
                        tx_date_str, "%Y-%m-%dT%H:%M:%S"
                    ).replace(tzinfo=datetime.timezone.utc)
                except Exception:
                    pass
            update_set = {
                "location_id": _id(cp),
                "location_name": _name(cp),
                "location_type": "Checkpoint",
                "evenement": evenement,
                "derniere_maj": ts,
                "derniere_transaction": tx_ts or ts,
            }
            if _id(gate):
                update_set["parent_gate"] = {"id": _id(gate), "name": _name(gate)}
            if _id(area):
                update_set["parent_area"] = {"id": _id(area), "name": _name(area)}
            if _id(venue):
                update_set["parent_venue"] = {"id": _id(venue), "name": _name(venue)}
            ops.append(UpdateOne(
                {"_id": cp_doc_id},
                {"$set": update_set, "$setOnInsert": {"source": "transactions"}},
                upsert=True,
            ))

        # Gate → parent Area/Venue, enfant Checkpoint
        if _id(gate):
            gate_doc_id = f"Gate_{_id(gate)}"
            update_set = {
                "location_id": _id(gate),
                "location_name": _name(gate),
                "location_type": "Gate",
                "evenement": evenement,
                "derniere_maj": ts,
            }
            if _id(area):
                update_set["parent_area"] = {"id": _id(area), "name": _name(area)}
            if _id(venue):
                update_set["parent_venue"] = {"id": _id(venue), "name": _name(venue)}
            update_add = {}
            if _id(cp):
                update_add["enfants"] = {"id": _id(cp), "type": "Checkpoint", "name": _name(cp)}
            op = {"$set": update_set, "$setOnInsert": {"source": "transactions"}}
            if update_add:
                op["$addToSet"] = update_add
            ops.append(UpdateOne({"_id": gate_doc_id}, op, upsert=True))

        # Area → parent Venue, enfant Gate
        if _id(area):
            area_doc_id = f"Area_{_id(area)}"
            update_set = {
                "location_id": _id(area),
                "location_name": _name(area),
                "location_type": "Area",
                "evenement": evenement,
                "derniere_maj": ts,
            }
            if _id(venue):
                update_set["parent_venue"] = {"id": _id(venue), "name": _name(venue)}
            update_add = {}
            if _id(gate):
                update_add["enfants"] = {"id": _id(gate), "type": "Gate", "name": _name(gate)}
            op = {"$set": update_set, "$setOnInsert": {"source": "transactions"}}
            if update_add:
                op["$addToSet"] = update_add
            ops.append(UpdateOne({"_id": area_doc_id}, op, upsert=True))

        # Venue → enfant Area
        if _id(venue):
            venue_doc_id = f"Venue_{_id(venue)}"
            update_set = {
                "location_id": _id(venue),
                "location_name": _name(venue),
                "location_type": "Venue",
                "evenement": evenement,
                "derniere_maj": ts,
            }
            update_add = {}
            if _id(area):
                update_add["enfants"] = {"id": _id(area), "type": "Area", "name": _name(area)}
            op = {"$set": update_set, "$setOnInsert": {"source": "transactions"}}
            if update_add:
                op["$addToSet"] = update_add
            ops.append(UpdateOne({"_id": venue_doc_id}, op, upsert=True))

    if ops:
        col_structure.bulk_write(ops, ordered=False)

    # Log des liens déduits
    if liens_cp_gate or liens_gate_area or liens_area_venue:
        print(f"  Arbre deduit: {len(liens_cp_gate)} checkpoint->gate, {len(liens_gate_area)} gate->area, {len(liens_area_venue)} area->venue")
        for c_id, c_name, g_id, g_name in sorted(liens_cp_gate):
            print(f"    Checkpoint {c_name} ({c_id}) -> Gate {g_name} ({g_id})")
        for g_id, g_name, a_id, a_name in sorted(liens_gate_area):
            print(f"    Gate {g_name} ({g_id}) -> Area {a_name} ({a_id})")
        for a_id, a_name, v_id, v_name in sorted(liens_area_venue):
            print(f"    Area {a_name} ({a_id}) -> Venue {v_name} ({v_id})")


def stocker_erreurs(txs, evenement, evenement_clean, cache_titres=None):
    """Insère les transactions en erreur (Status != '0') dans hsh_erreurs.
    Stocke l'intégralité des données de la transaction."""
    ts = datetime.datetime.now(datetime.timezone.utc)
    if cache_titres is None:
        cache_titres = {}
    ops = []
    for tx in txs:
        if tx["status"] == "0":
            continue
        tx_id = tx["transaction_id"]
        doc_id = f"{evenement_clean}_{tx_id}"
        annee = None
        if tx.get("date_utc"):
            try:
                annee = int(tx["date_utc"][:4])
            except (ValueError, TypeError):
                pass

        # Copie intégrale de la transaction + métadonnées + labels lisibles
        doc = dict(tx)
        doc["evenement"] = evenement
        doc["evenement_clean"] = evenement_clean
        doc["annee"] = annee
        doc["collecte_timestamp"] = ts
        doc["status_label"] = _label_status(tx["status"])
        doc["coding_label"] = _label_coding(tx.get("coding"))
        doc["validated_label"] = _label_validated(tx.get("validated"))
        doc["type_scan"] = categoriser_scan(tx.get("utid"), cache_titres, tx.get("upid"))

        ops.append(UpdateOne({"_id": doc_id}, {"$set": doc}, upsert=True))

    if ops:
        col_erreurs.bulk_write(ops, ordered=False)
        print(f"  {len(ops)} transactions en erreur upsertees dans hsh_erreurs.")


def agreger_transactions(txs, evenement, cache_titres=None):
    """Agrege les transactions par checkpoint + tranche de 5 minutes."""
    if cache_titres is None:
        cache_titres = {}
    buckets = {}
    for tx in txs:
        cp = tx.get("checkpoint")
        if not cp:
            continue
        cp_id = cp.get("ID") or cp.get("Id") or cp.get("id")
        cp_name = cp.get("Name") or cp.get("name") or ""
        if not cp_id:
            continue

        # Calculer la tranche de 5 min a partir de date_utc
        date_str = tx.get("date_utc")
        if not date_str:
            continue
        try:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=datetime.timezone.utc
            )
        except Exception:
            continue
        # Arrondir aux 5 min inferieures
        minute = (dt.minute // 5) * 5
        tranche = dt.replace(minute=minute, second=0, microsecond=0)

        key = (cp_id, tranche.isoformat())
        if key not in buckets:
            gate = tx.get("gate")
            gate_name = ""
            if gate:
                gate_name = gate.get("Name") or gate.get("name") or ""
            buckets[key] = {
                "cp_id": cp_id, "cp_name": cp_name,
                "gate_name": gate_name,
                "tranche": tranche,
                "ok": 0, "erreurs": 0, "entrees": 0, "sorties": 0,
                "entrees_vehicules": 0, "sorties_vehicules": 0,
                "entrees_enfants": 0, "sorties_enfants": 0,
                "entrees_accredites": 0, "sorties_accredites": 0,
            }

        b = buckets[key]
        if tx.get("status") == "0":
            b["ok"] += 1
        else:
            b["erreurs"] += 1
        direction = tx.get("direction", "")
        if direction == "Entree":
            b["entrees"] += 1
        elif direction == "Sortie":
            b["sorties"] += 1

        # Compteurs par type de scan
        cat = categoriser_scan(tx.get("utid"), cache_titres, tx.get("upid"))
        if cat == "vehicule":
            if direction == "Entree":
                b["entrees_vehicules"] += 1
            elif direction == "Sortie":
                b["sorties_vehicules"] += 1
        elif cat == "enfant":
            if direction == "Entree":
                b["entrees_enfants"] += 1
            elif direction == "Sortie":
                b["sorties_enfants"] += 1
        elif cat == "accredite":
            if direction == "Entree":
                b["entrees_accredites"] += 1
            elif direction == "Sortie":
                b["sorties_accredites"] += 1

    if not buckets:
        return

    ops = []
    for b in buckets.values():
        doc_id = f"{evenement}_{b['cp_id']}_{b['tranche'].strftime('%Y%m%dT%H%M')}"
        ops.append(UpdateOne(
            {"_id": doc_id},
            {
                "$inc": {
                    "ok": b["ok"],
                    "erreurs": b["erreurs"],
                    "entrees": b["entrees"],
                    "sorties": b["sorties"],
                    "entrees_vehicules": b["entrees_vehicules"],
                    "sorties_vehicules": b["sorties_vehicules"],
                    "entrees_enfants": b["entrees_enfants"],
                    "sorties_enfants": b["sorties_enfants"],
                    "entrees_accredites": b["entrees_accredites"],
                    "sorties_accredites": b["sorties_accredites"],
                },
                "$set": {
                    "checkpoint_id": b["cp_id"],
                    "checkpoint_name": b["cp_name"],
                    "gate_name": b["gate_name"],
                    "evenement": evenement,
                    "tranche": b["tranche"],
                },
            },
            upsert=True,
        ))
    if ops:
        col_tx_agg.bulk_write(ops, ordered=False)
        print(f"  {len(ops)} tranches agregees dans hsh_transactions_agg.")


def agreger_par_titre(txs, evenement, cache_titres=None):
    """Agrege les transactions par titre de billet + tranche de 5 minutes."""
    if cache_titres is None:
        cache_titres = {}
    buckets = {}
    for tx in txs:
        if tx.get("status") != "0":
            continue
        utid = tx.get("utid")
        if not utid or utid not in cache_titres:
            continue
        titre = cache_titres[utid]
        if not titre:
            continue

        date_str = tx.get("date_utc")
        if not date_str:
            continue
        try:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=datetime.timezone.utc
            )
        except Exception:
            continue
        minute = (dt.minute // 5) * 5
        tranche = dt.replace(minute=minute, second=0, microsecond=0)

        key = (titre, tranche.isoformat())
        if key not in buckets:
            buckets[key] = {"titre": titre, "tranche": tranche, "entrees": 0, "sorties": 0}

        direction = tx.get("direction", "")
        if direction == "Entree":
            buckets[key]["entrees"] += 1
        elif direction == "Sortie":
            buckets[key]["sorties"] += 1

    if not buckets:
        return

    ops = []
    for b in buckets.values():
        doc_id = f"{evenement}_{b['titre']}_{b['tranche'].strftime('%Y%m%dT%H%M')}"
        ops.append(UpdateOne(
            {"_id": doc_id},
            {
                "$inc": {"entrees": b["entrees"], "sorties": b["sorties"]},
                "$set": {
                    "titre": b["titre"],
                    "evenement": evenement,
                    "tranche": b["tranche"],
                },
            },
            upsert=True,
        ))
    if ops:
        col_agg_titres.bulk_write(ops, ordered=False)
        print(f"  {len(ops)} tranches agregees dans hsh_agg_titres.")


def executer_transactions(sock, doc_global, cache_titres=None):
    """Collecte paginée des transactions → erreurs + arbre."""
    if cache_titres is None:
        cache_titres = {}
    evenement = doc_global.get("evenement", "")
    evenement_clean = doc_global.get("evenement_clean", "")
    last_tx_id = doc_global.get("dernier_transaction_id")

    # Forçage de période depuis l'admin (1-3 jours)
    force_jours = doc_global.get("force_collecte_jours")
    if force_jours:
        try:
            force_jours = min(int(force_jours), 3)
        except (ValueError, TypeError):
            force_jours = None

    # Déterminer la fenêtre ou le curseur
    from_dt = None
    to_dt = None
    if force_jours:
        # Forçage admin : remonter de N jours, ignorer le curseur
        last_tx_id = None
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        from_utc = now_utc - datetime.timedelta(days=force_jours)
        from_dt = from_utc.strftime("%Y-%m-%dT%H:%M:%S")
        to_dt = now_utc.strftime("%Y-%m-%dT%H:%M:%S")
        from_paris = from_utc.astimezone(TZ_PARIS).strftime("%d/%m %H:%M")
        to_paris = now_utc.astimezone(TZ_PARIS).strftime("%d/%m %H:%M")
        print(f"  Transactions: FORCE {force_jours}j — {from_paris} -> {to_paris} (heure Paris)")
        # Nettoyer le flag immediatement
        maj_global({"force_collecte_jours": None})
    elif last_tx_id is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if DEV_MODE:
            # Mode dev : toute la journée en cours (heure Paris)
            now_paris = now_utc.astimezone(TZ_PARIS)
            debut_journee_paris = now_paris.replace(hour=0, minute=0, second=0, microsecond=0)
            from_utc = debut_journee_paris.astimezone(datetime.timezone.utc)
        else:
            from_utc = now_utc - datetime.timedelta(minutes=3)
        from_dt = from_utc.strftime("%Y-%m-%dT%H:%M:%S")
        to_dt = now_utc.strftime("%Y-%m-%dT%H:%M:%S")
        from_paris = from_utc.astimezone(TZ_PARIS).strftime("%H:%M:%S")
        to_paris = now_utc.astimezone(TZ_PARIS).strftime("%H:%M:%S")
        print(f"  Transactions: fenetre {from_paris} -> {to_paris} (heure Paris){' [DEV]' if DEV_MODE else ''}")
    else:
        print(f"  Transactions: depuis LastTransactionId={last_tx_id}")

    # Augmenter le timeout pour la pagination
    sock.settimeout(READ_TIMEOUT_TRANSACTIONS)

    page = 0
    total_tx = 0
    total_erreurs = 0
    cursor = last_tx_id

    while True:
        page += 1
        xml_req = build_transactions_xml(
            from_dt=from_dt,
            to_dt=to_dt,
            last_tx_id=cursor,
        )
        frame = encapsuler_transactions(xml_req)
        resp = envoyer_et_recevoir(sock, frame)
        if not resp:
            print(f"  Page {page}: pas de reponse.")
            break

        not_complete, txs, max_txid = parse_transactions(resp)
        nb = len(txs)
        total_tx += nb

        # Stocker les erreurs
        nb_erreurs = sum(1 for tx in txs if tx["status"] != "0")
        total_erreurs += nb_erreurs
        if txs:
            stocker_erreurs(txs, evenement, evenement_clean, cache_titres)
            mettre_a_jour_arbre(txs, evenement)
            agreger_transactions(txs, evenement, cache_titres)
            agreger_par_titre(txs, evenement, cache_titres)

        print(f"  Page {page:03d}: {nb} transactions ({nb_erreurs} erreurs) | NotComplete={'1' if not_complete else '0'}")

        if not_complete and max_txid is not None:
            cursor = str(max_txid)
            # Après la première page, on passe en mode curseur (plus de From/To)
            from_dt = None
            to_dt = None
            time.sleep(0.1)
        else:
            # Mettre à jour le curseur pour le prochain cycle
            if max_txid is not None:
                cursor = str(max_txid)
            break

    # Sauvegarder le curseur
    update = {"dernier_transaction_id": int(cursor) if cursor else None}
    maj_global(update)

    print(f"  Total: {total_tx} transactions, {total_erreurs} erreurs.")

    # Remettre le timeout pour les counters
    sock.settimeout(READ_TIMEOUT_COUNTER)


def executer_compteurs(sock, evenement, evenement_clean):
    """Inquiry Counter pour chaque location sélectionnée → data_access."""
    # Relire ___GLOBAL___ pour avoir les locations à jour
    doc = lire_global()
    locations = doc.get("locations_selectionnees", [])

    if not locations:
        print("  Aucune location selectionnee pour le polling Counter.")
        return

    print(f"  Polling Counter pour {len(locations)} locations...")
    ts = datetime.datetime.now(datetime.timezone.utc)

    for loc in locations:
        loc_id = loc.get("id")
        loc_type = loc.get("type")
        if not loc_id or not loc_type:
            continue

        try:
            xml_req = build_counter_location_xml(loc_id, loc_type)
            frame = encapsuler_counter(xml_req)
            resp = envoyer_et_recevoir(sock, frame)
            if not resp:
                print(f"    {loc_id}/{loc_type}: pas de reponse.")
                continue

            data = parse_counter_single(resp)
            if data is None:
                print(f"    {loc_id}/{loc_type}: aucun Counter dans la reponse.")
                continue

            data["requested_location_id"] = loc_id
            data["requested_location_type"] = loc_type
            data["requested_event"] = evenement
            data["requested_event_clean"] = evenement_clean

            col_data_access.insert_one(data)
            print(f"    {loc_id}/{loc_type}: {data.get('counter_name', '?')} — E:{data.get('entries')} S:{data.get('exits')} P:{data.get('current')}")

        except Exception as e:
            print(f"    {loc_id}/{loc_type}: erreur — {e}")


# =========================================================
#              CYCLE PRINCIPAL
# =========================================================
def main():
    # 1. Lire ___GLOBAL___
    doc = lire_global()

    if not doc.get("live_controle_actif", False):
        # Nettoyage des champs d'état pour repartir propre à la prochaine activation
        if doc.get("dernier_inventaire") is not None or doc.get("dernier_transaction_id") is not None:
            maj_global({
                "dernier_inventaire": None,
                "dernier_transaction_id": None,
                "dernier_cycle": None,
            })
            print("Live controle desactive. Etat reinitialise. Sortie.")
        else:
            print("Live controle desactive. Sortie.")
        return

    evenement = doc.get("evenement", "")
    evenement_clean = doc.get("evenement_clean", "")
    if not evenement:
        print("Aucun evenement configure dans ___GLOBAL___. Sortie.")
        return

    print(f"Live controle actif — evenement: {evenement}{' [MODE DEV]' if DEV_MODE else ''}")

    # Assurer les index au premier lancement
    assurer_index()

    # 2. Ouvrir une connexion TCP unique
    with socket.create_connection((HSH_IP, HSH_PORT), timeout=CONNECT_TIMEOUT) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(READ_TIMEOUT_COUNTER)

        # 3. Première exécution ? → Inventaire Counter global
        if est_premiere_execution(doc):
            print("[ETAPE 1] Premiere execution — inventaire complet")
            executer_inventaire(sock, evenement)
        else:
            print("[ETAPE 1] Inventaire deja fait — skip")

        # 4. Charger le cache des titres pour catégoriser les scans
        cache_titres = charger_cache_titres()
        print(f"  Cache titres: {len(cache_titres)} UTID charges")

        # 5. Collecte des transactions
        print("[ETAPE 2] Collecte des transactions")
        executer_transactions(sock, doc, cache_titres)

        # 6. Collecte des compteurs
        print("[ETAPE 3] Collecte des compteurs selectionnes")
        executer_compteurs(sock, evenement, evenement_clean)

        # 6. Fermeture propre
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    # 7. Mettre à jour le dernier cycle
    maj_global({"dernier_cycle": datetime.datetime.now(datetime.timezone.utc)})
    print("Cycle termine.")


# =========================================================
#              POINT D'ENTRÉE
# =========================================================
if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _update_cron_status("down", str(exc))
        print(f"Erreur: {exc}")
    else:
        _update_cron_status("ok")
    finally:
        mongo_client.close()
