#!/usr/bin/env python3
"""
alert_engine.py - Moteur de detection d'alertes TITAN Cockpit.

Lance par tache planifiee Windows toutes les 30 secondes (2 taches decalees).
Charge les definitions d'alertes depuis MongoDB, evalue chaque handler,
et ecrit les alertes declenchees dans cockpit_active_alerts.

Usage:
    python alert_engine.py
"""

import os
import sys
import math
import logging
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient
from bson.objectid import ObjectId

from whatsapp import WhatsAppService

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "titan"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AlertEngine] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("alert_engine")

# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------

_client = None
_db = None

def get_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        _db = _client[DB_NAME]
    return _db

# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def parse_time_to_min(t):
    """Convertit '14:30' en 870 minutes."""
    if not t or not isinstance(t, str):
        return None
    parts = t.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, TypeError):
        return None

def format_minutes_delta(m):
    if m <= 0:
        return "maintenant"
    if m < 60:
        return "%d min" % m
    h = m // 60
    r = m % 60
    if r == 0:
        return "%dh" % h
    return "%dh%02d" % (h, r)

def haversine(lat1, lon1, lat2, lon2):
    """Distance en metres entre deux points GPS."""
    R = 6371000
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = (math.sin(dLat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dLon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def alert_weight(alert_type):
    t = (alert_type or "").upper()
    if t == "ACCIDENT":
        return 5
    if t in ("HAZARD", "WEATHERHAZARD"):
        return 3
    if t == "JAM":
        return 2
    return 0

# ---------------------------------------------------------------------------
# Chargement du contexte
# ---------------------------------------------------------------------------

def load_enabled_definitions(db):
    return list(db["cockpit_alert_definitions"].find({"enabled": True}))

def build_context(db, sim_time=None):
    """Construit le contexte partage : event/year actif, horaires, etc."""
    now = sim_time or datetime.now(timezone.utc)
    ctx = {"now": now}

    # Determiner la date du jour (heure Paris)
    try:
        from zoneinfo import ZoneInfo
        today_str = now.astimezone(ZoneInfo("Europe/Paris")).strftime("%Y-%m-%d")
    except Exception:
        today_str = now.strftime("%Y-%m-%d")

    # Chercher le parametrage dont les dates couvrent aujourd'hui
    params = list(db["parametrages"].find(
        {"data.globalHoraires.dates": {"$exists": True}},
        {"event": 1, "year": 1, "data.globalHoraires": 1}
    ))

    best = None
    for p in params:
        gh = (p.get("data") or {}).get("globalHoraires")
        if not gh or not gh.get("dates"):
            continue
        dates = [d.get("date") for d in gh["dates"] if d.get("date")]
        if today_str in dates:
            best = p
            break

    # Fallback : prendre le parametrage dont les dates sont les plus proches
    if not best:
        for p in params:
            gh = (p.get("data") or {}).get("globalHoraires")
            if not gh or not gh.get("dates"):
                continue
            dates = [d.get("date") for d in gh["dates"] if d.get("date")]
            if not dates:
                continue
            # Si les dates sont dans le futur proche ou passe recent (7 jours)
            min_d = min(dates)
            max_d = max(dates)
            if min_d <= today_str <= max_d:
                best = p
                break

    if best:
        gh = (best.get("data") or {}).get("globalHoraires", {})
        ctx["event"] = best.get("event", "")
        ctx["year"] = str(best.get("year", ""))
        ctx["globalHoraires"] = gh
        log.info("  Evenement actif: %s %s (dates couvrent %s)", ctx["event"], ctx["year"], today_str)
    else:
        log.info("  Aucun evenement actif pour la date %s", today_str)

    return ctx

# ---------------------------------------------------------------------------
# Handlers de detection
# ---------------------------------------------------------------------------

def _find_schedule(public_dates, now_local):
    """Trouve le schedule applicable : aujourd'hui ou overnight d'hier.
    Retourne (schedule_doc, is_from_yesterday).
    Inclut une marge de 5 min apres la fermeture pour la detection de transition."""
    today_iso = now_local.strftime("%Y-%m-%d")
    yesterday_iso = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
    now_minutes = now_local.hour * 60 + now_local.minute

    dates_by_key = {}
    for d in public_dates:
        dates_by_key[d.get("date", "")] = d

    log.debug("  _find_schedule: today=%s yesterday=%s now_min=%d, dates_available=%s",
              today_iso, yesterday_iso, now_minutes, list(dates_by_key.keys()))

    # 1) Verifier si on est dans la queue overnight d'hier
    #    (avant fermeture + 5 min de marge pour la transition "site ferme")
    yesterday_pub = dates_by_key.get(yesterday_iso)
    if yesterday_pub and not yesterday_pub.get("is24h"):
        yd_open = parse_time_to_min(yesterday_pub.get("openTime"))
        yd_close = parse_time_to_min(yesterday_pub.get("closeTime"))
        log.debug("  yesterday schedule: open=%s(%s) close=%s(%s)", yesterday_pub.get("openTime"), yd_open, yesterday_pub.get("closeTime"), yd_close)
        if yd_open is not None and yd_close is not None and yd_close < yd_open:
            log.debug("  overnight detected, now_min=%d <= close+5=%d ? %s", now_minutes, yd_close + 5, now_minutes <= yd_close + 5)
            if now_minutes <= yd_close + 5:
                return yesterday_pub, True
        else:
            log.debug("  not overnight (close >= open)")
    else:
        log.debug("  no yesterday schedule for %s", yesterday_iso)

    # 2) Sinon, schedule d'aujourd'hui
    today_pub = dates_by_key.get(today_iso)
    if today_pub and not today_pub.get("is24h"):
        log.debug("  using today schedule: open=%s close=%s", today_pub.get("openTime"), today_pub.get("closeTime"))
        return today_pub, False

    log.debug("  no schedule found")
    return None, False


def detect_schedule_proximity(definition, context):
    """Detecte la proximite d'une ouverture ou fermeture de site."""
    gh = context.get("globalHoraires")
    if not gh or not gh.get("dates"):
        return None

    params = definition.get("params") or {}
    minutes_before = params.get("minutes_before", 30)
    schedule_event = params.get("schedule_event", "open")  # "open" ou "close"

    now = context["now"]
    try:
        from zoneinfo import ZoneInfo
        now_local = now.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        now_local = now

    now_minutes = now_local.hour * 60 + now_local.minute
    today_iso = now_local.strftime("%Y-%m-%d")

    pub, is_yesterday = _find_schedule(gh.get("dates", []), now_local)
    if not pub:
        return None

    open_min = parse_time_to_min(pub.get("openTime"))
    close_min = parse_time_to_min(pub.get("closeTime"))
    if open_min is None or close_min is None:
        return None

    is_overnight = close_min < open_min

    if schedule_event == "open":
        if is_yesterday:
            # On est dans le overnight d'hier, l'ouverture d'hier est passee
            return None
        target_min = open_min
        diff = target_min - now_minutes
    else:
        # Fermeture
        if is_yesterday:
            # Overnight : on est avant close_min du schedule d'hier
            diff = close_min - now_minutes
        elif is_overnight:
            # Meme jour overnight : fermeture apres minuit
            # Si on est apres l'ouverture, la fermeture est dans (1440 - now + close)
            if now_minutes >= open_min:
                diff = (1440 - now_minutes) + close_min
            else:
                return None
        else:
            diff = close_min - now_minutes

    if diff < 0 or diff > minutes_before:
        return None

    target_time = pub.get("openTime") if schedule_event == "open" else pub.get("closeTime")
    slug = definition["slug"]
    # Dedup sur la date du schedule (pas today_iso pour overnight)
    dedup_date = pub.get("date", today_iso)

    if schedule_event == "open":
        title = "OUVERTURE IMMINENTE"
        message = "Ouverture au public dans " + format_minutes_delta(diff)
    else:
        title = "FERMETURE IMMINENTE"
        message = "Fermeture au public dans " + format_minutes_delta(diff)

    return {
        "definition_slug": slug,
        "event": context.get("event", ""),
        "year": context.get("year", ""),
        "title": title,
        "message": message,
        "timeStr": target_time or "",
        "dedup_key": "%s-%s" % (slug, dedup_date),
        "triggeredAt": now,
        "expiresAt": now + timedelta(minutes=minutes_before + 5),
    }


def detect_schedule_transition(definition, context):
    """Detecte les transitions ouvert/ferme du site."""
    gh = context.get("globalHoraires")
    if not gh or not gh.get("dates"):
        return None

    params = definition.get("params") or {}
    transition = params.get("transition", "open")  # "open" ou "close"

    now = context["now"]
    try:
        from zoneinfo import ZoneInfo
        now_local = now.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        now_local = now

    now_minutes = now_local.hour * 60 + now_local.minute
    today_iso = now_local.strftime("%Y-%m-%d")

    pub, is_yesterday = _find_schedule(gh.get("dates", []), now_local)
    if not pub:
        return None

    open_min = parse_time_to_min(pub.get("openTime"))
    close_min = parse_time_to_min(pub.get("closeTime"))
    if open_min is None or close_min is None:
        return None

    db = get_db()
    state_col = db["cockpit_alert_engine_state"]
    dedup_date = pub.get("date", today_iso)
    state_key = "transition-%s-%s" % (definition["slug"], dedup_date)
    existing = state_col.find_one({"_id": state_key})
    if existing:
        return None  # Deja declenche pour ce schedule

    if transition == "open":
        if is_yesterday:
            return None  # L'ouverture d'hier est passee
        if now_minutes >= open_min and now_minutes <= open_min + 5:
            state_col.update_one(
                {"_id": state_key},
                {"$set": {"triggered": True, "at": now}},
                upsert=True
            )
            return {
                "definition_slug": definition["slug"],
                "event": context.get("event", ""),
                "year": context.get("year", ""),
                "title": "SITE OUVERT",
                "message": "Le site est maintenant ouvert au public",
                "timeStr": pub.get("openTime", ""),
                "dedup_key": "%s-%s" % (definition["slug"], dedup_date),
                "triggeredAt": now,
                "expiresAt": now + timedelta(minutes=30),
            }

    elif transition == "close":
        # Determiner si on est dans la fenetre de fermeture (close_min .. close_min+5)
        if is_yesterday:
            # Overnight : on est le lendemain, close_min est l'heure de fermeture
            check = now_minutes
            target = close_min
        else:
            is_overnight = close_min < open_min
            if is_overnight:
                # Fermeture apres minuit - ne se declenche pas le jour J mais le lendemain
                return None
            check = now_minutes
            target = close_min

        if check >= target and check <= target + 5:
            state_col.update_one(
                {"_id": state_key},
                {"$set": {"triggered": True, "at": now}},
                upsert=True
            )
            return {
                "definition_slug": definition["slug"],
                "event": context.get("event", ""),
                "year": context.get("year", ""),
                "title": "SITE FERME",
                "message": "Le site est maintenant ferme au public",
                "timeStr": pub.get("closeTime", ""),
                "dedup_key": "%s-%s" % (definition["slug"], dedup_date),
                "triggeredAt": now,
                "expiresAt": now + timedelta(minutes=30),
            }

    return None


def detect_traffic_cluster(definition, context):
    """Detecte les clusters d'incidents trafic Waze."""
    db = get_db()
    params = definition.get("params") or {}
    radius_m = params.get("radius_m", 500)
    threshold = params.get("threshold", 12)

    # Charger les alertes Waze depuis le cache MongoDB
    doc = db["waze_alerts"].find_one({"_id": "latest"})
    if not doc or not doc.get("data"):
        return None

    alerts = doc["data"]
    if not isinstance(alerts, list):
        return None

    # Filtrer les alertes avec coordonnees
    located = []
    for a in alerts:
        if not isinstance(a, dict):
            continue
        loc = a.get("location")
        if not loc or loc.get("y") is None or loc.get("x") is None:
            continue
        t = (a.get("type") or "").upper()
        if t in ("ROAD_CLOSED", "CONSTRUCTION"):
            continue
        located.append(a)

    if len(located) < 2:
        return None

    # Clustering simple : pour chaque alerte, trouver ses voisins
    best_cluster = None
    best_score = 0

    for i, center in enumerate(located):
        cluster = [center]
        score = alert_weight(center.get("type"))
        for j, other in enumerate(located):
            if i == j:
                continue
            dist = haversine(
                center["location"]["y"], center["location"]["x"],
                other["location"]["y"], other["location"]["x"]
            )
            if dist <= radius_m:
                cluster.append(other)
                score += alert_weight(other.get("type"))
        if score > best_score and len(cluster) >= 2:
            best_score = score
            best_cluster = cluster

    if not best_cluster or best_score < threshold:
        return None

    # Fingerprint geo
    fp_lat = sum(a["location"]["y"] for a in best_cluster) / len(best_cluster)
    fp_lon = sum(a["location"]["x"] for a in best_cluster) / len(best_cluster)
    geo_key = "%d,%d" % (round(fp_lat * 1000), round(fp_lon * 1000))

    # Cooldown 30 min
    now = context["now"]
    dedup = "cluster-%s" % geo_key
    existing = db["cockpit_active_alerts"].find_one({"dedup_key": dedup})
    if existing:
        triggered = existing.get("triggeredAt")
        if triggered and (now - triggered).total_seconds() < 1800:
            return None

    # Compter par type
    counts = {}
    for a in best_cluster:
        t = (a.get("type") or "UNKNOWN").upper()
        counts[t] = counts.get(t, 0) + 1

    # Rue principale
    streets = {}
    for a in best_cluster:
        s = a.get("street")
        if s:
            streets[s] = streets.get(s, 0) + 1
    main_street = max(streets, key=streets.get) if streets else "zone non identifiee"

    # Message
    parts = []
    if counts.get("ACCIDENT"):
        n = counts["ACCIDENT"]
        parts.append("%d accident%s" % (n, "s" if n > 1 else ""))
    hazards = counts.get("HAZARD", 0) + counts.get("WEATHERHAZARD", 0)
    if hazards:
        parts.append("%d danger%s" % (hazards, "s" if hazards > 1 else ""))
    if counts.get("JAM"):
        n = counts["JAM"]
        parts.append("%d bouchon%s" % (n, "s" if n > 1 else ""))

    message = "Zone critique : " + ", ".join(parts)
    if main_street:
        message += " -- " + main_street

    # Pins pour la carte
    cluster_pins = []
    for a in best_cluster:
        cluster_pins.append({
            "lat": a["location"]["y"],
            "lon": a["location"]["x"],
            "type": a.get("type", ""),
            "street": a.get("street", ""),
        })

    return {
        "definition_slug": definition["slug"],
        "event": context.get("event", ""),
        "year": context.get("year", ""),
        "title": "ALERTE TRAFIC",
        "message": message,
        "timeStr": "%d alertes dans un rayon de %dm" % (len(best_cluster), radius_m),
        "actionData": {"pins": cluster_pins},
        "dedup_key": dedup,
        "triggeredAt": now,
        "expiresAt": now + timedelta(minutes=30),
    }


def detect_anpr_watchlist(definition, context):
    """Detecte les plaques surveillees dans les detections LAPI recentes."""
    db = get_db()
    now = context["now"]

    # Charger les plaques actives de la watchlist
    watched = list(db["cockpit_anpr_watchlist"].find({"enabled": True}))
    if not watched:
        return None

    plates = {w["plate"]: w for w in watched}

    # Chercher les detections recentes (< 60s pour couvrir l'intervalle entre 2 runs)
    cutoff = now - timedelta(seconds=60)
    recent = list(db["hik_anpr"].find({
        "license_plate": {"$in": list(plates.keys())},
        "event_dt": {"$gte": cutoff},
    }).sort([("event_dt", -1)]).limit(20))

    results = []
    for det in recent:
        plate = det.get("license_plate", "")
        event_dt = det.get("event_dt")
        dt_str = event_dt.strftime("%Y%m%d%H%M%S") if event_dt else "unknown"
        dedup = "anpr-%s-%s" % (plate, dt_str)

        # Verifier dedup
        if db["cockpit_active_alerts"].find_one({"dedup_key": dedup}):
            continue

        w = plates.get(plate, {})
        camera = det.get("camera_path", "")
        # Extraire un label camera lisible
        camera_cfg = db["anpr_camera_config"].find_one({"camera_path": camera})
        camera_label = camera_cfg.get("label", camera) if camera_cfg and isinstance(camera_cfg, dict) else camera

        time_str = event_dt.strftime("%H:%M:%S") if event_dt else ""
        label = w.get("label", "")
        msg = "Plaque %s detectee" % plate
        if label:
            msg += " (%s)" % label
        msg += " - Camera : %s" % camera_label

        results.append({
            "definition_slug": definition["slug"],
            "event": context.get("event", ""),
            "year": context.get("year", ""),
            "title": "PLAQUE SURVEILLEE DETECTEE",
            "message": msg,
            "timeStr": time_str,
            "actionData": {"plate": plate, "camera": camera_label},
            "dedup_key": dedup,
            "triggeredAt": now,
            "expiresAt": now + timedelta(minutes=10),
        })

    return results if results else None


def detect_meteo_threshold(definition, context):
    """Detecte les depassements de seuils meteo (vent, pluie)."""
    db = get_db()
    now = context["now"]

    params = definition.get("params") or {}
    field = params.get("field", "")  # "vent_rafale" ou "pluviometrie"
    warn_threshold = params.get("warn", 0)
    alert_threshold = params.get("alert", 0)
    unit = params.get("unit", "")

    try:
        from zoneinfo import ZoneInfo
        paris = ZoneInfo("Europe/Paris")
        now_local = now.astimezone(paris)
    except Exception:
        now_local = now

    today_str = now_local.strftime("%Y-%m-%d")
    current_hour = now_local.strftime("%H:00")

    previsions = db["meteo_previsions"].find_one({"Date": today_str})
    if not previsions or "Heures" not in previsions:
        return None

    heures = previsions["Heures"]
    # Heures restantes de la journee
    upcoming = [h for h in heures if h.get("Heure", "") >= current_hour]
    if not upcoming:
        return None

    # Mapping champ -> cles MongoDB possibles
    FIELD_KEYS = {
        "vent_rafale": ["Vent rafale (km/h)"],
        "pluviometrie": ["Pluviometrie (mm)", "Pluviom\u00e9trie (mm)"],
    }
    keys = FIELD_KEYS.get(field, [])
    if not keys:
        return None

    # Trouver la valeur max dans les heures a venir
    max_val = 0
    max_hour = ""
    for h in upcoming:
        val = 0
        for k in keys:
            v = h.get(k)
            if v is not None:
                try:
                    val = float(v)
                except (ValueError, TypeError):
                    continue
                break
        if val > max_val:
            max_val = val
            max_hour = h.get("Heure", "")

    if max_val < warn_threshold:
        return None

    severity = "alerte" if max_val >= alert_threshold else "vigilance"
    slug = definition["slug"]
    dedup = "%s-%s" % (slug, today_str)

    # Ne pas re-declencher si deja envoye aujourd'hui avec meme severite ou pire
    existing = db["cockpit_active_alerts"].find_one({"dedup_key": dedup})
    if existing:
        return None

    if field == "vent_rafale":
        title = "ALERTE VENT" if severity == "alerte" else "VIGILANCE VENT"
        message = "Rafales de %d %s prevues a %s" % (int(max_val), unit, max_hour)
    elif field == "pluviometrie":
        title = "ALERTE PLUIE" if severity == "alerte" else "VIGILANCE PLUIE"
        message = "Precipitations de %.1f %s prevues a %s" % (max_val, unit, max_hour)
    else:
        title = "ALERTE METEO"
        message = "%s : %.1f %s a %s" % (field, max_val, unit, max_hour)

    return {
        "definition_slug": slug,
        "event": context.get("event", ""),
        "year": context.get("year", ""),
        "title": title,
        "message": message,
        "timeStr": max_hour,
        "dedup_key": dedup,
        "triggeredAt": now,
        "expiresAt": now + timedelta(hours=3),
    }


# ---------------------------------------------------------------------------
# Handler : checkpoint_reassign
# ---------------------------------------------------------------------------

def detect_checkpoint_reassign(definition, context):
    """Detecte les checkpoints dont le parent_gate a change depuis le dernier cycle."""
    db = get_db()
    now = context["now"]

    # Charger tous les checkpoints avec un parent_gate
    checkpoints = list(db["hsh_structure"].find({
        "location_type": "Checkpoint",
        "parent_gate.id": {"$exists": True},
    }, {
        "_id": 1,
        "location_id": 1,
        "location_name": 1,
        "parent_gate": 1,
        "parent_area": 1,
        "evenement": 1,
    }))

    if not checkpoints:
        return None

    # Charger l'etat precedent (snapshot des affectations checkpoint -> gate)
    state_key = "cp-gate-snapshot"
    state_doc = db["cockpit_alert_engine_state"].find_one({"_id": state_key})
    prev_map = state_doc.get("assignments", {}) if state_doc else {}

    # Construire le snapshot actuel : { checkpoint_id: gate_id }
    current_map = {}
    for cp in checkpoints:
        cp_id = str(cp.get("location_id", ""))
        gate_id = cp.get("parent_gate", {}).get("id", "")
        if cp_id and gate_id:
            current_map[cp_id] = gate_id

    # Sauvegarder le snapshot pour le prochain cycle
    db["cockpit_alert_engine_state"].update_one(
        {"_id": state_key},
        {"$set": {"assignments": current_map, "at": now}},
        upsert=True
    )

    # Premier cycle : pas de comparaison possible
    if not prev_map:
        log.debug("checkpoint_reassign: premier snapshot (%d checkpoints)", len(current_map))
        return None

    # Comparer : chercher les changements de gate
    results = []
    for cp in checkpoints:
        cp_id = str(cp.get("location_id", ""))
        new_gate_id = cp.get("parent_gate", {}).get("id", "")
        old_gate_id = prev_map.get(cp_id, "")

        if not old_gate_id or not new_gate_id:
            continue
        if old_gate_id == new_gate_id:
            continue

        # Changement detecte !
        cp_name = cp.get("location_name", cp_id)
        new_gate_name = cp.get("parent_gate", {}).get("name", new_gate_id)

        # Chercher le nom de l'ancienne gate
        old_gate_doc = db["hsh_structure"].find_one(
            {"location_type": "Gate", "location_id": old_gate_id},
            {"location_name": 1}
        )
        old_gate_name = old_gate_doc.get("location_name", old_gate_id) if old_gate_doc else old_gate_id

        try:
            now_local = now.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Paris"))
        except Exception:
            now_local = now
        time_str = now_local.strftime("%H:%M")

        dedup = "cp-reassign-%s-%s" % (cp_id, now.strftime("%Y%m%d%H%M"))
        if db["cockpit_active_alerts"].find_one({"dedup_key": dedup}):
            continue

        msg = "%s : %s -> %s" % (cp_name, old_gate_name, new_gate_name)
        area_name = cp.get("parent_area", {}).get("name", "")
        if area_name:
            msg += " (area: %s)" % area_name

        log.info("  checkpoint_reassign: %s", msg)

        results.append({
            "definition_slug": definition["slug"],
            "event": context.get("event", ""),
            "year": context.get("year", ""),
            "title": "CHANGEMENT AFFECTATION CHECKPOINT",
            "message": msg,
            "timeStr": time_str,
            "actionData": {
                "checkpoint_id": cp_id,
                "checkpoint_name": cp_name,
                "old_gate": old_gate_name,
                "new_gate": new_gate_name,
            },
            "dedup_key": dedup,
            "triggeredAt": now,
            "expiresAt": now + timedelta(minutes=30),
        })

    return results if results else None


# ---------------------------------------------------------------------------
# Registre des handlers
# ---------------------------------------------------------------------------

HANDLERS = {
    "schedule_proximity": detect_schedule_proximity,
    "schedule_transition": detect_schedule_transition,
    "traffic_cluster": detect_traffic_cluster,
    "anpr_watchlist": detect_anpr_watchlist,
    "meteo_threshold": detect_meteo_threshold,
    "checkpoint_reassign": detect_checkpoint_reassign,
}

# ---------------------------------------------------------------------------
# Upsert alerte active
# ---------------------------------------------------------------------------

def upsert_active_alert(db, alert_doc):
    """Insere ou met a jour une alerte active avec deduplication."""
    dedup = alert_doc.get("dedup_key")
    if not dedup:
        db["cockpit_active_alerts"].insert_one(alert_doc)
        return
    try:
        db["cockpit_active_alerts"].update_one(
            {"dedup_key": dedup},
            {"$setOnInsert": alert_doc},
            upsert=True
        )
    except Exception as e:
        log.warning("Erreur upsert alerte (dedup=%s): %s", dedup, e)

# ---------------------------------------------------------------------------
# Cycle principal
# ---------------------------------------------------------------------------

def run_cycle(sim_time=None):
    db = get_db()
    defs = load_enabled_definitions(db)
    if not defs:
        log.info("Aucune definition d'alerte active")
        return

    context = build_context(db, sim_time=sim_time)
    log.info("Cycle: %d definitions, event=%s, year=%s",
             len(defs), context.get("event", "?"), context.get("year", "?"))

    alerts_created = 0
    wa_batch = []  # (alert_doc, definition) pour notification WhatsApp
    for d in defs:
        handler = HANDLERS.get(d.get("detection_type"))
        if not handler:
            continue
        try:
            result = handler(d, context)
            if result is None:
                continue
            # Le handler ANPR peut retourner une liste
            if isinstance(result, list):
                for r in result:
                    upsert_active_alert(db, r)
                    alerts_created += 1
                    wa_batch.append((r, d))
            else:
                upsert_active_alert(db, result)
                alerts_created += 1
                wa_batch.append((result, d))
        except Exception as e:
            log.error("Erreur handler '%s' (slug=%s): %s",
                      d.get("detection_type"), d.get("slug"), e, exc_info=True)

    # Notification WhatsApp en batch (agregation anti-ban)
    if wa_batch:
        try:
            wa_service = WhatsAppService(db)
            wa_service.notify_batch(wa_batch)
        except Exception as e:
            log.warning("Erreur notification WhatsApp: %s", e)

    # Nettoyage des etats de transition anciens (> 2 jours)
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    try:
        db["cockpit_alert_engine_state"].delete_many({"at": {"$lt": cutoff}})
    except Exception:
        pass

    if alerts_created:
        log.info("  -> %d alerte(s) creee(s)", alerts_created)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Moteur de detection d'alertes TITAN Cockpit")
    parser.add_argument("--sim-time", type=str, default=None,
                        help="Simuler une heure (ex: '2026-06-14 13:35'). Utilise le fuseau Europe/Paris.")
    args = parser.parse_args()

    sim_time = None
    if args.sim_time:
        try:
            from zoneinfo import ZoneInfo
            naive = datetime.strptime(args.sim_time, "%Y-%m-%d %H:%M")
            sim_time = naive.replace(tzinfo=ZoneInfo("Europe/Paris")).astimezone(timezone.utc)
            log.setLevel(logging.DEBUG)
            log.info("=== MODE SIMULATION : %s (Paris) ===", args.sim_time)
        except Exception as e:
            log.error("Format sim-time invalide (attendu: 'YYYY-MM-DD HH:MM'): %s", e)
            sys.exit(0)

    log.info("=== Demarrage alert_engine ===")
    try:
        run_cycle(sim_time=sim_time)
    except Exception as e:
        log.error("Erreur fatale: %s", e, exc_info=True)
        sys.exit(0)
    finally:
        if _client is not None:
            _client.close()
        sys.exit(0)
    log.info("=== Fin alert_engine ===")


if __name__ == "__main__":
    main()
