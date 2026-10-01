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
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from pymongo import MongoClient
from bson.objectid import ObjectId

from whatsapp import WhatsAppService
import event_courant

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_TITAN_ENV = os.getenv("TITAN_ENV", "dev").strip().lower()
DB_NAME = "titan" if _TITAN_ENV in {"prod", "production"} else "titan_dev"

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

    # Evenement courant : source unique event_courant (epreuve active de
    # montage.start a demontage.end, priorite jours publics puis course la plus
    # proche ; SAISON en dehors). Remplace "le premier parametrage dont les
    # dates contiennent aujourd'hui" (dependant de l'ordre Mongo) et le repli
    # a 7 jours.
    try:
        acts = event_courant.active_events(db, now)
    except Exception as e:
        log.warning("  event_courant indisponible: %s", e)
        acts = []
    if not acts:
        log.info("  Aucun evenement actif pour la date %s", today_str)
        return ctx

    cur = acts[0]
    ctx["event"] = cur["event"]
    ctx["year"] = str(cur["year"])
    ctx["event_kind"] = cur["kind"]  # "epreuve" | "saison"
    ctx["phase"] = cur["phase"]      # public | montage | demontage | None (SAISON)
    if cur["kind"] == "epreuve":
        # Horaires publics de l'epreuve : seuls consommateurs, les detecteurs
        # d'ouverture/fermeture. Jamais pour SAISON (pas de dates : detecteurs OFF).
        p = db["parametrages"].find_one(
            {"event": cur["event"], "year": {"$in": [str(cur["year"]), int(cur["year"])]}},
            {"data.globalHoraires": 1})
        ctx["globalHoraires"] = ((p or {}).get("data") or {}).get("globalHoraires") or {}
    log.info("  Evenement courant: %s %s (%s, phase=%s, jour %s)",
             ctx["event"], ctx["year"], ctx["event_kind"], ctx["phase"], today_str)
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
    if context.get("event_kind") == "saison":
        return None  # SAISON : pas d'horaires publics, detecteur OFF
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
    if context.get("event_kind") == "saison":
        return None  # SAISON : pas d'horaires publics, detecteur OFF
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


# NOTE: detect_anpr_watchlist (polling LAPI) a ete supprime au profit du
# dispatch direct depuis ecoutehik2.py (cf. PCA/SCRIPTS/cockpit_dispatch.py).
# Les alertes plaque watchlist sont desormais creees en temps reel par le
# script de reception Hik, avec latence < 1s.


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
    dedup = "%s-%s-%s" % (slug, today_str, severity)

    # Une fois par jour et par niveau, aggravation comprise : une vigilance
    # levee le matin n'empeche plus l'alerte de l'apres-midi. La memoire est
    # dans l'etat du moteur, pas dans l'alerte active : celle-ci disparait au
    # bout de 3 h (TTL), et la meme vigilance re-sonnait alors dans la journee.
    rank = 2 if severity == "alerte" else 1
    state_col = db["cockpit_alert_engine_state"]
    state_key = "meteo-%s-%s" % (slug, today_str)
    state = state_col.find_one({"_id": state_key}) or {}
    if state.get("rank", 0) >= rank:
        return None
    state_col.update_one({"_id": state_key},
                         {"$set": {"rank": rank, "at": now}}, upsert=True)

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


def detect_meteo_rain_onset(definition, context):
    """Detecte la transition sec -> pluie dans les previsions horaires.
    Declenche X minutes avant le debut prevu de la pluie."""
    db = get_db()
    now = context["now"]
    params = definition.get("params") or {}
    lead_minutes = params.get("lead_minutes", 30)
    rain_min = params.get("rain_threshold", 0.1)  # mm, seuil pour considerer "pluvieux"

    try:
        from zoneinfo import ZoneInfo
        now_local = now.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        now_local = now

    today_str = now_local.strftime("%Y-%m-%d")
    now_minutes = now_local.hour * 60 + now_local.minute

    previsions = db["meteo_previsions"].find_one({"Date": today_str})
    if not previsions or "Heures" not in previsions:
        return None

    heures = previsions["Heures"]
    RAIN_KEYS = ["Pluviometrie (mm)", "Pluviom\u00e9trie (mm)"]

    def get_rain(h):
        for k in RAIN_KEYS:
            v = h.get(k)
            if v is not None:
                try:
                    return float(v)
                except (ValueError, TypeError):
                    pass
        return 0.0

    # Trier les heures et trouver la premiere transition sec -> pluvieux
    sorted_h = sorted(heures, key=lambda h: h.get("Heure", ""))
    rain_start_hour = None
    rain_val = 0.0

    for i, h in enumerate(sorted_h):
        heure_str = h.get("Heure", "")
        try:
            h_minutes = int(heure_str.split(":")[0]) * 60
        except (ValueError, IndexError):
            continue

        # Ignorer les heures deja passees (avec marge pour le lead time)
        if h_minutes < now_minutes - lead_minutes:
            continue

        val = get_rain(h)
        if val >= rain_min:
            # Verifier que l'heure precedente est seche
            prev_val = get_rain(sorted_h[i - 1]) if i > 0 else 0.0
            if prev_val < rain_min:
                rain_start_hour = heure_str
                rain_val = val
                break

    if not rain_start_hour:
        return None

    # Calculer si on est dans la fenetre d'alerte (lead_minutes avant le debut)
    try:
        rain_minutes = int(rain_start_hour.split(":")[0]) * 60
    except (ValueError, IndexError):
        return None

    diff = rain_minutes - now_minutes
    if diff < 0 or diff > lead_minutes:
        return None

    slug = definition["slug"]
    dedup = "%s-%s-%s" % (slug, today_str, rain_start_hour.replace(":", ""))

    existing = db["cockpit_active_alerts"].find_one({"dedup_key": dedup})
    if existing:
        return None

    time_str = now_local.strftime("%H:%M")
    message = "Pluie prevue a %s (%.1f mm) - actuellement sec" % (rain_start_hour, rain_val)

    return {
        "definition_slug": slug,
        "event": context.get("event", ""),
        "year": context.get("year", ""),
        "title": "PLUIE IMMINENTE",
        "message": message,
        "timeStr": time_str,
        "dedup_key": dedup,
        "triggeredAt": now,
        "expiresAt": now + timedelta(hours=1),
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


def detect_checkpoint_error_burst(definition, context):
    """Detecte les rafales d'erreurs HSH sur un meme checkpoint.
    Params:
        threshold: nombre d'erreurs min (defaut 3)
        window_seconds: fenetre temporelle en secondes (defaut 30)
    """
    db = get_db()
    now = context["now"]
    params = definition.get("params") or {}
    threshold = params.get("threshold", 3)
    window_sec = params.get("window_seconds", 30)

    try:
        from zoneinfo import ZoneInfo
        now_local = now.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        now_local = now

    # Fenetre de recherche : derniere minute (couvre largement la fenetre)
    lookback = now_local - timedelta(minutes=2)
    lookback_str = lookback.strftime("%Y-%m-%d %H:%M:%S")
    now_str = now_local.strftime("%Y-%m-%d %H:%M:%S")

    recent = list(db["hsh_erreurs"].find(
        {"date_paris": {"$gte": lookback_str, "$lte": now_str}},
        {"checkpoint": 1, "date_paris": 1, "status_label": 1, "direction": 1,
         "gate": 1, "area": 1}
    ).sort("date_paris", -1))

    if not recent:
        return None

    # Grouper par checkpoint
    by_cp = {}
    for doc in recent:
        cp_name = doc.get("checkpoint", {}).get("Name", "")
        if not cp_name:
            continue
        by_cp.setdefault(cp_name, []).append(doc)

    results = []
    slug = definition["slug"]

    for cp_name, errors in by_cp.items():
        # Trier par date desc
        errors.sort(key=lambda d: d.get("date_paris", ""), reverse=True)

        # Fenetre glissante : compter les erreurs dans window_sec depuis la plus recente
        if len(errors) < threshold:
            continue

        newest_str = errors[0].get("date_paris", "")
        try:
            newest_dt = datetime.strptime(newest_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue

        burst_count = 0
        for e in errors:
            try:
                e_dt = datetime.strptime(e.get("date_paris", ""), "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if (newest_dt - e_dt).total_seconds() <= window_sec:
                burst_count += 1

        if burst_count < threshold:
            continue

        # Dedup sur checkpoint + minute (eviter re-declenchement)
        dedup = "hsh-burst-%s-%s" % (cp_name, newest_dt.strftime("%Y%m%d%H%M"))
        if db["cockpit_active_alerts"].find_one({"dedup_key": dedup}):
            continue

        time_str = now_local.strftime("%H:%M")
        last_error = errors[0]
        gate_name = last_error.get("gate", {}).get("Name", "")
        area_name = last_error.get("area", {}).get("Name", "")
        status = last_error.get("status_label", "")

        msg = "%d erreurs en %ds sur %s" % (burst_count, window_sec, cp_name)
        if gate_name:
            msg += " (%s)" % gate_name
        msg += " - %s" % status

        log.info("  checkpoint_error_burst: %s", msg)

        results.append({
            "definition_slug": slug,
            "event": context.get("event", ""),
            "year": context.get("year", ""),
            "title": "RAFALE ERREURS CHECKPOINT",
            "message": msg,
            "timeStr": time_str,
            "actionData": {
                "checkpoint": cp_name,
                "gate": gate_name,
                "area": area_name,
                "count": burst_count,
                "last_error": status,
            },
            "dedup_key": dedup,
            "triggeredAt": now,
            "expiresAt": now + timedelta(minutes=10),
        })

    return results if results else None


# ---------------------------------------------------------------------------
# Main courante (pcorg) — alerte sur niveau d'urgence par categorie
# ---------------------------------------------------------------------------

# Ordre de gravite croissant
_URGENCY_RANK = {"IMP": 1, "UR": 2, "UA": 3, "EU": 4}

_URGENCY_LABELS = {
    "EU": "Detresse vitale / Danger immediat",
    "UA": "Urgence absolue / Incident grave",
    "UR": "Urgence relative / Incident en cours",
    "IMP": "Implique / temoin",
}

_URGENCY_ICON = {"EU": "crisis_alert", "UA": "warning", "UR": "info", "IMP": "person"}

_CATEGORY_LABELS = {
    "PCO.Secours": "Secours", "PCO.Securite": "Securite", "PCO.Technique": "Technique",
    "PCO.Flux": "Flux", "PCO.Fourriere": "Fourriere", "PCO.Information": "Information",
    "PCO.MainCourante": "", "PCS.Information": "PCS Information", "PCS.Surete": "PCS Surete",
}


def detect_pcorg_urgency(definition, context):
    """Detecte les nouvelles fiches main courante correspondant a une
    categorie et un niveau d'urgence minimum.

    Params attendus :
      category  : prefixe categorie a surveiller (ex: "PCO.Securite", "PCO." pour toutes)
      min_level : niveau minimum declencheur ("EU", "UA", "UR" ou "IMP")
      lookback_s: secondes de recul pour les fiches recentes (defaut 90)
    """
    db = get_db()
    now = context["now"]
    params = definition.get("params") or {}

    category_prefix = params.get("category", "PCO.")
    min_level = params.get("min_level", "UA")
    lookback = params.get("lookback_s", 90)

    min_rank = _URGENCY_RANK.get(min_level, 3)

    # Fiches recentes ouvertes avec un niveau d'urgence
    cutoff = now - timedelta(seconds=lookback)
    query = {
        # Prefixe echappe : "PCO." matchait n'importe quel caractere apres PCO
        "category": {"$regex": "^" + re.escape(category_prefix)},
        "niveau_urgence": {"$in": [k for k, v in _URGENCY_RANK.items() if v >= min_rank]},
        "status_code": {"$ne": 10},
        # Fiche auto-creee par un SOS tablette : l'alerte "SOS TABLETTE" a deja
        # ete levee par /field/sos. Sans cette exclusion, le moteur relevait
        # une seconde alerte plein ecran "ALERTE SECOURS" pour le meme SOS, un
        # cycle plus tard : le SOS semblait revenir, decale d'un poste a l'autre.
        "content_category.field_sos": {"$ne": True},
        "$or": [
            {"ts": {"$gte": cutoff}},
            {"synced_at": {"$gte": cutoff}},
        ],
    }

    fiches = list(db["pcorg"].find(query).sort("ts", -1).limit(20))
    if not fiches:
        return None

    # Memoire des fiches deja signalees (24 h). Sans elle, `synced_at` etant
    # rafraichi a chaque modification SQL, une fiche redeclenchait l'alerte
    # des que la precedente expirait (15 min).
    seen_col = db["pcorg_alerted"]
    try:
        seen_col.create_index("at", expireAfterSeconds=24 * 3600)
    except Exception:
        pass

    results = []
    for f in fiches:
        sql_id = f.get("sql_id") or str(f.get("_id", ""))
        niveau = f.get("niveau_urgence", "")
        # Le niveau fait partie de la cle : une aggravation (UR -> UA) realerte
        dedup = "pcorg-urg-%s-%s-%s" % (definition["slug"], sql_id, niveau)

        if db["cockpit_active_alerts"].find_one({"dedup_key": dedup}):
            continue
        if seen_col.find_one({"_id": dedup}):
            continue
        seen_col.update_one({"_id": dedup}, {"$set": {"at": now}}, upsert=True)

        cat = f.get("category", "")
        text = f.get("text") or f.get("text_full") or ""
        operator = f.get("operator") or ""
        area_desc = ""
        area = f.get("area")
        if isinstance(area, dict):
            area_desc = area.get("desc") or ""
        time_str = ""
        if f.get("time_local"):
            time_str = f["time_local"][:5]
        elif isinstance(f.get("ts"), datetime):
            ts_f = f["ts"] if f["ts"].tzinfo else f["ts"].replace(tzinfo=timezone.utc)
            time_str = ts_f.astimezone(ZoneInfo("Europe/Paris")).strftime("%H:%M")

        # Titre porteur de la categorie ("MAIN COURANTE FLUX") : le niveau est
        # deja affiche en badge, la categorie n'apparaissait nulle part.
        cat_label = _CATEGORY_LABELS.get(cat, cat.split(".")[-1])
        title = ("MAIN COURANTE %s" % cat_label).upper().strip()
        msg_parts = []
        if text:
            msg_parts.append(text[:200])
        if area_desc:
            msg_parts.append("Zone : %s" % area_desc)
        if operator:
            msg_parts.append("Operateur : %s" % operator)
        msg = " — ".join(msg_parts) if msg_parts else "Nouvelle fiche %s" % cat

        # Tag sur l'evenement de la FICHE (pas celui du contexte) : une fiche
        # SAISON ou d'une epreuve secondaire active garde son rattachement.
        f_event = f.get("event") or context.get("event", "")
        f_year = f.get("year")
        f_year = str(f_year) if f_year not in (None, "") else context.get("year", "")
        results.append({
            "definition_slug": definition["slug"],
            "event": f_event,
            "year": f_year,
            "title": title,
            "message": msg,
            "timeStr": time_str,
            "actionData": {
                "pcorg_id": str(f.get("_id", "")),
                "sql_id": sql_id,
                "category": cat,
                "niveau_urgence": niveau,
                "text": text[:200],
                "zone": area_desc,
                "operator": operator,
            },
            "dedup_key": dedup,
            "triggeredAt": now,
            "expiresAt": now + timedelta(minutes=15),
        })

    return results if results else None


# ---------------------------------------------------------------------------
# Saturation porte prevue (door_saturation_forecast)
# ---------------------------------------------------------------------------
#
# Source live : hsh_transactions_agg (live_controle.py), une ligne par
# checkpoint (tripode, PDA) et par tranche de 5 min, avec gate_name. C'est le
# seul debit PAR PORTE disponible en direct : data_access ne suit que des Area
# (compteurs cumules, pas de porte).
#
# ATTENTION FUSEAUX : `tranche` est en HEURE DE PARIS etiquetee UTC (le champ
# Handshake date_utc porte l'heure locale). On compare donc tout en heure de
# Paris naive, jamais en UTC.
#
# Capacite d'une porte = somme des capacites de ses appareils actifs sur la
# derniere heure (tripode ~900/h, PDA ~650/h : p99 mesure par appareil et par
# tranche de 5 min sur les archives 2026, et 650/h/agent est la norme de la
# chaine scans). Surchargeable porte par porte (params.capacities).
#
# Prevision : debit courant (15 min) x profil N-1 de la MEME porte, aligne sur
# le jour de course (decalage arrondi a la semaine : la course revient chaque
# annee le meme jour de semaine, ce qui neutralise le champ `race` qui porte
# tantot le depart tantot l'arrivee). Sans N-1 exploitable : tendance lineaire
# des 30 dernieres minutes, bornee.

DOOR_SAT_SERVICES = ("HELPDESK", "UAM", "LITIGE", "SERI", "PUNISHER")
DOOR_SAT_DEFAULTS = {
    "horizon_min": 30,        # regarde jusqu'a 30 min devant
    "threshold_pct": 90,      # % de la capacite qui declenche
    "min_rate": 300,          # /h : en dessous, jamais d'alerte (nuit, portes calmes)
    "window_min": 15,         # fenetre du debit courant
    "trend_window_min": 30,   # fenetre de la tendance (repli sans N-1)
    "max_growth": 3.0,        # borne du facteur de croissance prevu (profil N-1)
    # Sans N-1 : extrapoler la tendance des 30 dernieres min ? Desactive par
    # defaut : rejouee sur les 5 editions 2026, elle faisait passer la
    # precision de 72 % a 55 % (1 alerte tendance sur 4 seulement suivie d'un
    # depassement reel) pour 5 saturations de plus anticipees sur 17.
    "trend_fallback": False,
    "trend_max_growth": 1.5,  # borne de la tendance (bien plus bruitee que le N-1)
    "min_n1_rate": 120,       # /h : en dessous, le profil N-1 est du bruit
    "stale_min": 20,          # collecteur muet depuis plus longtemps -> rien
    "dedup_min": 30,          # une alerte par porte par 30 min
    "sens": "entrees",        # "entrees" ou "total" (entrees + sorties)
    "device_capacity_h": {"tripode": 900, "pda": 650, "autre": 650},
    "capacities": {},         # {"PORTE NORD PIETONS": 9000} : surcharge par porte
    # Facteur applique a la capacite theorique des appareils actifs, par
    # porte : {"PORTE SUD": 0.72}. Appris sur les archives 5 min par
    # scripts/replay_door_saturation.py (debit plafond reellement observe par
    # appareil a cette porte). Une porte absente garde la capacite theorique.
    "capacity_factors": {},
    # "securite" : capacite = agents de securite prevus au planning x
    # agent_rate_h (door_security.py, poste -> porte lu dans la bible). Seules
    # les portes pietonnes dotees d'agents sont surveillees : le goulet est la
    # palpation, pas le scan (rejeu 2026 : a la fiche "Renfort filtrage", la
    # porte est a 101 % de sa capacite de securite et 15 % de sa capacite de
    # scan). "scan" : capacite des appareils (comportement historique).
    "capacity_mode": "scan",
    "agent_rate_h": 350,      # personnes / h / agent de palpation
    # Mode securite : une alerte par EPISODE. Pas de nouvelle alerte pour la
    # porte avant `renotify_min`, sauf si le besoin s'aggrave (plus d'agents a
    # ajouter). Rejeu 2026 : avec 30 min, une porte durablement sous-dotee
    # (Porte Nord) sonnait toutes les demi-heures, 960 alertes sur 6 editions.
    "renotify_min": 120,
    # Re-alerter avant renotify_min si le besoin grandit ? Desactive : rejeu
    # 2026, 461 alertes au lieu de 316 pour 5 points de rappel des fiches, et
    # les aggravations annoncaient des besoins peu credibles (+10 a +23
    # agents) la ou le planning ne refletait plus l'effectif reel.
    "renotify_on_worse": False,
    "doors": [],              # filtre (vide = toutes les portes)
    "exclude": list(DOOR_SAT_SERVICES),
}

# Postes securite -> portes et agents prevus : bible + calendrier relus au
# plus toutes les 10 min (le detecteur tourne a chaque cycle du moteur).
_DOOR_SECU_CACHE = {}
_DOOR_SECU_TTL_S = 600


def _door_security_staffing(db, coll, event, year, now):
    """{porte normalisee: {creneau 30 min: agents}} pour l'edition, en cache.

    Les noms de portes servent au rapprochement bible -> controle d'acces : on
    prend TOUTES les portes de l'edition (distinct), pas seulement celles de la
    fenetre courante, sinon "Porte Nord - Acces Pietons" pourrait tomber sur
    PORTE NORD VEHICULES faute de voir la porte pietonne.
    """
    import time
    import door_security as DS
    key = (coll, event, year)
    hit = _DOOR_SECU_CACHE.get(key)
    # Horloge REELLE, pas `now` : en rejeu, l'heure simulee avance de 5 min
    # par cycle et rechargeait bible + planning tous les deux cycles.
    if hit and time.monotonic() - hit[0] < _DOOR_SECU_TTL_S:
        return hit[1]
    try:
        q = {"evenement": event} if coll == "hsh_transactions_agg" else {}
        gates = [g for g in db[coll].distinct("gate_name", q) if g]
        posts, _ = DS.load_door_posts(db, event, year, gates)
        staffing = {_door_norm(g): v for g, v in DS.load_security_staffing(db, event, year, posts).items()}
    except Exception:
        log.warning("door_saturation_forecast : effectifs securite illisibles", exc_info=True)
        staffing = {}
    _DOOR_SECU_CACHE[key] = (time.monotonic(), staffing)
    return staffing


def _agents_at(staffing, key, t):
    slot = t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)
    return (staffing.get(key) or {}).get(slot, 0)


def _door_norm(name):
    """Nom de porte comparable d'une edition a l'autre (casse, accents,
    PORTAIL/PORTE, pluriels PIETONS/VEHICULES)."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Z0-9]+", " ", s.upper()).strip()
    words = []
    for w in s.split():
        w = {"PORTAIL": "PORTE", "PIETONS": "PIETON", "VEHICULES": "VEHICULE"}.get(w, w)
        words.append(w)
    return " ".join(words)


def _device_kind(cp_name):
    n = str(cp_name or "").upper()
    if n.startswith("TRI"):
        return "tripode"
    if "PDA" in n:
        return "pda"
    return "autre"


def _floor5(dt):
    return dt.replace(minute=dt.minute - dt.minute % 5, second=0, microsecond=0)


def _paris_label(now_utc):
    """UTC conscient -> heure de Paris naive (echelle de `tranche`)."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(ZoneInfo("Europe/Paris")).replace(tzinfo=None)


def door_rate(buckets, end, window_min):
    """Debit (/h) sur [end - window, end) a partir de {debut de tranche 5 min:
    compte}. Une tranche absente vaut zero : le collecteur n'ecrit que les
    tranches ou il y a eu un passage (la fraicheur est verifiee a part)."""
    start = end - timedelta(minutes=window_min)
    total = sum(v for t, v in buckets.items() if start <= t < end)
    return total * 60.0 / window_min


def door_trend_slope(buckets, end, trend_window_min):
    """Pente (debit /h par minute) des moindres carres sur les tranches de
    [end - trend_window, end). None s'il y a moins de 3 tranches."""
    n = int(trend_window_min // 5)
    if n < 3:
        return None
    xs, ys = [], []
    for i in range(n):
        t = end - timedelta(minutes=5 * (n - i))
        xs.append(5.0 * i + 2.5)
        ys.append(buckets.get(t, 0) * 12.0)
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def n1_window_rate(series, center, width_min):
    """Debit N-1 (/h) moyen sur [center - w/2, center + w/2].

    `series` = [(debut naif, duree_min, compte)], triee, sans trou (les
    tranches vides valent 0, cf. load_n1_door_series). Le debit de chaque
    tranche est pose en son MILIEU et interpole lineairement entre deux
    milieux : une serie horaire lue en marches d'escalier faisait bondir le
    rapport futur/courant a chaque changement d'heure (08:55 -> 09:05 : x3
    sur une rampe d'ouverture), ce qui declenchait des alertes a HH:05.
    Echantillonnage a la minute. None hors de la plage mesuree.
    """
    pts = sorted((start + timedelta(minutes=dur / 2.0), count * 60.0 / dur)
                 for start, dur, count in series if dur)
    if not pts:
        return None
    first_start = min(s for s, _, _ in series)
    last_end = max(s + timedelta(minutes=d) for s, d, _ in series)
    times = [p[0] for p in pts]

    def _at(t):
        if t < first_start or t > last_end:
            return None
        if t <= times[0]:
            return pts[0][1]
        if t >= times[-1]:
            return pts[-1][1]
        i = 1
        while times[i] < t:
            i += 1
        (t0, r0), (t1, r1) = pts[i - 1], pts[i]
        f = (t - t0).total_seconds() / max((t1 - t0).total_seconds(), 1.0)
        return r0 + (r1 - r0) * f

    n = max(int(width_min), 1)
    a = center - timedelta(minutes=width_min / 2.0)
    vals = [v for v in (_at(a + timedelta(minutes=k + 0.5)) for k in range(n)) if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _densify(points, dur):
    """[(debut, compte)] -> [(debut, dur, compte)] sans trou entre le premier
    et le dernier point : le collecteur n'ecrit pas les tranches vides, et
    interpoler par-dessus un trou inventerait des passages."""
    if not points:
        return []
    d = {}
    for t, c in points:
        d[t] = d.get(t, 0) + c
    ts = sorted(d)
    out, t = [], ts[0]
    while t <= ts[-1]:
        out.append((t, dur, d.get(t, 0)))
        t += timedelta(minutes=dur)
    return out


def forecast_door(cur_rate, horizon_min, n1_now=None, n1_future=None, slope=None,
                  lead_min=0.0, max_growth=3.0, min_n1_rate=120, trend_max_growth=None):
    """Prevision du debit par pas de 5 min jusqu'a l'horizon.

    n1_future : {minutes devant: debit N-1 aligne} ; utilise si n1_now est
    exploitable (>= min_n1_rate). Sinon, tendance lineaire (slope, /h par
    minute), `lead_min` etant l'ecart entre le centre de la fenetre mesuree et
    maintenant. Rend (methode, [(minutes devant, debit prevu)]) ou (None, []).
    """
    steps = list(range(5, int(horizon_min) + 1, 5))
    if cur_rate is None or cur_rate <= 0 or not steps:
        return None, []
    cap = cur_rate * max_growth
    if n1_now is not None and n1_now >= min_n1_rate and n1_future:
        out = []
        for h in steps:
            fut = n1_future.get(h)
            if fut is None:
                continue
            ratio = min(max(fut / n1_now, 0.0), max_growth)
            out.append((h, cur_rate * ratio))
        if out:
            return "profil_n1", out
    if slope is not None:
        tcap = cur_rate * (trend_max_growth if trend_max_growth is not None else max_growth)
        return "tendance", [(h, min(max(cur_rate + slope * (h + lead_min), 0.0), tcap))
                            for h in steps]
    return None, []


def _race_date(db, event, year):
    """Date (Paris) de la course : historique_controle d'abord (plus fiable),
    puis parametrages (globalHoraires.race est en UTC avec Z), puis le
    dernier jour public."""
    try:
        year_int = int(year)
    except (TypeError, ValueError):
        return None

    def _parse(raw):
        if not raw:
            return None
        try:
            if isinstance(raw, datetime):
                dt = raw
            else:
                dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(ZoneInfo("Europe/Paris"))
        return dt.date()

    for h in db["historique_controle"].find(
            {"event": event, "year": year_int, "race": {"$exists": True}}, {"race": 1}):
        d = _parse(h.get("race"))
        if d:
            return d
    doc = (db["parametrages"].find_one({"event": event, "year": str(year_int)},
                                       {"data.race": 1, "data.globalHoraires": 1})
           or db["parametrages"].find_one({"event": event, "year": year_int},
                                          {"data.race": 1, "data.globalHoraires": 1}))
    if doc:
        data = doc.get("data") or {}
        gh = data.get("globalHoraires") or {}
        for raw in (gh.get("race"), data.get("race")):
            d = _parse(raw)
            if d:
                return d
        dates = sorted(str(x.get("date"))[:10] for x in (gh.get("dates") or []) if x.get("date"))
        if dates:
            return _parse(dates[-1])
    return None


def n1_day_offset(race_n, race_n1):
    """Decalage N -> N-1 en jours, arrondi a la semaine entiere."""
    if not race_n or not race_n1:
        return None
    days = (race_n - race_n1).days
    return int(round(days / 7.0)) * 7


def _event_aliases(db, event):
    aliases = [event]
    try:
        ev = db["evenement"].find_one({"nom": event}, {"short": 1}) or {}
        if ev.get("short") and ev["short"] not in aliases:
            aliases.append(ev["short"])
    except Exception:
        pass
    return aliases


def _archive_tag(event, year):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(event).strip()) + "_" + str(int(year))


def load_n1_door_series(db, event, year_n1, start, end, sens="entrees"):
    """{porte normalisee: [(debut, duree_min, compte)]} de l'edition N-1 sur
    [start, end) (heure de Paris naive, deja decalee), ou ({}, None).

    1. hsh_archive_tx_<event>_<annee> : meme source que le live (5 min,
       entrees seules, memes noms de portes) -- existe pour les editions
       archivees depuis 2026.
    2. historique_controle{type: portes} : horaire, TOUS SENS confondus. Ne
       sert qu'en profil (rapport futur/courant), jamais en valeur absolue.
    """
    coll = "hsh_archive_tx_" + _archive_tag(event, year_n1)
    try:
        has_archive = coll in db.list_collection_names()
    except Exception:
        has_archive = False
    out = {}
    if has_archive:
        for d in db[coll].find({"tranche": {"$gte": start, "$lt": end}},
                               {"gate_name": 1, "tranche": 1, "entrees": 1, "sorties": 1}):
            t = d.get("tranche")
            if not isinstance(t, datetime):
                continue
            v = int(d.get("entrees") or 0)
            if sens == "total":
                v += int(d.get("sorties") or 0)
            key = _door_norm(d.get("gate_name"))
            out.setdefault(key, {}).setdefault(t.replace(tzinfo=None), 0)
            out[key][t.replace(tzinfo=None)] += v
        if out:
            return ({k: _densify(list(v.items()), 5) for k, v in out.items()},
                    "hsh_archive_tx")
    doc = db["historique_controle"].find_one(
        {"type": "portes", "event": {"$in": _event_aliases(db, event)}, "year": int(year_n1)})
    if not doc:
        return {}, None
    for door in doc.get("doors") or []:
        key = _door_norm(door.get("name"))
        pts = []
        for s in door.get("scans") or []:
            ts = s.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    continue
            if not isinstance(ts, datetime):
                continue
            ts = ts.replace(tzinfo=None, minute=0, second=0, microsecond=0)
            if ts + timedelta(hours=1) <= start or ts >= end:
                continue
            pts.append((ts, int(s.get("scan_count") or 0)))
        if pts:
            out[key] = _densify(pts, 60)
    return out, ("historique_controle" if out else None)


def detect_door_saturation_forecast(definition, context):
    """Alerte AVANT qu'une porte ne sature : debit prevu >= threshold_pct de
    sa capacite dans les horizon_min minutes.

    Lecture seule. `context` peut porter `db` (tests, rejeu) et
    `door_tx_source` = {collection, event, year} pour rejouer une edition
    archivee (hsh_archive_tx_*) a une heure simulee (`now`).
    """
    db = context.get("db")
    if db is None:
        db = get_db()
    now = context["now"]
    slug = definition.get("slug") or "door-saturation"
    p = dict(DOOR_SAT_DEFAULTS)
    p.update(definition.get("params") or {})
    try:
        horizon = int(p["horizon_min"])
        thr_pct = float(p["threshold_pct"])
        min_rate = float(p["min_rate"])
        window = int(p["window_min"])
        trend_window = int(p["trend_window_min"])
        max_growth = float(p["max_growth"])
        min_n1 = float(p["min_n1_rate"])
        stale = int(p["stale_min"])
        dedup_min = int(p["dedup_min"])
        trend_max = float(p["trend_max_growth"])
        trend_on = bool(p["trend_fallback"])
        agent_rate = float(p["agent_rate_h"])
    except (TypeError, ValueError):
        log.warning("door_saturation_forecast %s : parametres invalides", slug)
        return None
    sens = "total" if p.get("sens") == "total" else "entrees"
    secu_mode = p.get("capacity_mode") == "securite"
    dev_cap = dict(DOOR_SAT_DEFAULTS["device_capacity_h"])
    dev_cap.update(p.get("device_capacity_h") or {})
    cap_over = {_door_norm(k): v for k, v in (p.get("capacities") or {}).items()}
    cap_factor = {}
    for k, v in (p.get("capacity_factors") or {}).items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0 < f <= 2:
            cap_factor[_door_norm(k)] = f
    only = {_door_norm(x) for x in (p.get("doors") or []) if x}
    exclude = {_door_norm(x) for x in (p.get("exclude") or []) if x}

    src = context.get("door_tx_source")
    if src:
        coll, event, year = src["collection"], src["event"], src.get("year")
    elif context.get("event_kind") == "saison":
        # Hors epreuve (SAISON) : pas de flux public a prevoir. Skip explicite
        # meme si ___GLOBAL___.live_controle_actif est reste a true.
        return None
    else:
        g = db["data_access"].find_one({"_id": "___GLOBAL___"}) or {}
        if not g.get("live_controle_actif") or not g.get("evenement"):
            return None
        coll, event = "hsh_transactions_agg", g["evenement"]
        year = context.get("year") or _paris_label(now).year
    try:
        year = int(year)
    except (TypeError, ValueError):
        return None

    now_label = _paris_label(now)
    look = max(60, trend_window, window) + 10
    q = {"tranche": {"$gte": now_label - timedelta(minutes=look), "$lt": now_label}}
    if coll == "hsh_transactions_agg":
        q["evenement"] = event
    docs = list(db[coll].find(q, {"gate_name": 1, "checkpoint_name": 1, "tranche": 1,
                                  "entrees": 1, "sorties": 1, "ok": 1, "erreurs": 1}))
    tranches = [d["tranche"] for d in docs if isinstance(d.get("tranche"), datetime)]
    if not tranches:
        return None
    latest = max(tranches)
    # Fin de la derniere tranche reellement collectee : la tranche en cours
    # n'est pas complete, et le collecteur peut avoir quelques minutes de retard.
    end = min(_floor5(now_label), latest.replace(tzinfo=None) + timedelta(minutes=5))
    if (now_label - end).total_seconds() > stale * 60:
        return None
    lead = (now_label - end).total_seconds() / 60.0 + window / 2.0

    doors = {}
    for d in docs:
        t = d.get("tranche")
        name = d.get("gate_name") or ""
        if not isinstance(t, datetime) or not name:
            continue
        key = _door_norm(name)
        if key in exclude or (only and key not in only):
            continue
        t = t.replace(tzinfo=None)
        e = doors.setdefault(key, {"name": name, "buckets": {}, "devices": {}, "ok": 0, "err": 0})
        v = int(d.get("entrees") or 0)
        if sens == "total":
            v += int(d.get("sorties") or 0)
        e["buckets"][t] = e["buckets"].get(t, 0) + v
        if t >= end - timedelta(minutes=60) and (d.get("ok") or d.get("erreurs")):
            e["devices"][d.get("checkpoint_name") or "?"] = _device_kind(d.get("checkpoint_name"))
        if t >= end - timedelta(minutes=window):
            e["ok"] += int(d.get("ok") or 0)
            e["err"] += int(d.get("erreurs") or 0)
    if not doors:
        return None

    # Profil N-1 aligne sur le jour de course (decalage en semaines entieres)
    n1, n1_source, offset = {}, None, None
    race_n = _race_date(db, event, year)
    race_n1 = None
    for alias in _event_aliases(db, event):
        race_n1 = _race_date(db, alias, year - 1)
        if race_n1:
            break
    offset = n1_day_offset(race_n, race_n1)
    if offset is not None:
        shift = timedelta(days=offset)
        n1, n1_source = load_n1_door_series(
            db, event, year - 1,
            end - timedelta(minutes=window) - shift - timedelta(minutes=60),
            now_label + timedelta(minutes=horizon + 60) - shift, sens=sens)
    else:
        shift = None

    staffing = {}
    if secu_mode:
        staffing = _door_security_staffing(db, coll, event, year, now)
        if not staffing:
            # Pas de bible ou de planning pour l'edition : s'abstenir, jamais
            # retomber sur la capacite de scan (qui ne voit pas le goulet).
            return None

    results = []
    for key, e in sorted(doors.items()):
        cur = door_rate(e["buckets"], end, window)
        agents_now = None
        if secu_mode:
            if key not in staffing:
                continue  # porte sans poste de securite pieton
            agents_now = _agents_at(staffing, key, now_label)
            if not agents_now:
                continue  # aucun agent prevu en ce moment : rien a comparer
            capacity = agents_now * agent_rate
            cap_src = "securite"
        elif key in cap_over:
            try:
                capacity = float(cap_over[key])
            except (TypeError, ValueError):
                continue
            cap_src = "param"
        else:
            capacity = float(sum(dev_cap.get(k, dev_cap.get("autre", 650))
                                 for k in e["devices"].values()))
            cap_src = "appareils"
            if key in cap_factor:
                capacity *= cap_factor[key]
                cap_src = "appareils_calibre"
        if capacity <= 0 or cur <= 0:
            continue
        slope = door_trend_slope(e["buckets"], end, trend_window) if trend_on else None
        n1_now, n1_future = None, {}
        series = n1.get(key) if shift is not None else None
        if series:
            center_now = end - timedelta(minutes=window / 2.0) - shift
            n1_now = n1_window_rate(series, center_now, window)
            for h in range(5, horizon + 1, 5):
                r = n1_window_rate(series, now_label + timedelta(minutes=h) - shift, window)
                if r is not None:
                    n1_future[h] = r
        method, pred = forecast_door(cur, horizon, n1_now, n1_future, slope, lead,
                                     max_growth, min_n1, trend_max)
        thr = capacity * thr_pct / 100.0
        hit = None
        if cur >= thr and cur >= min_rate:
            hit = (0, cur, "constate", capacity, agents_now)
        else:
            for h, r in pred:
                cap_h, ag_h = capacity, agents_now
                if secu_mode:
                    # Capacite AU MOMENT prevu : une fin de vacation fait
                    # baisser l'effectif alors que le flux monte.
                    ag_h = _agents_at(staffing, key, now_label + timedelta(minutes=h))
                    if not ag_h:
                        continue
                    cap_h = ag_h * agent_rate
                if r >= cap_h * thr_pct / 100.0 and r >= min_rate:
                    hit = (h, r, method, cap_h, ag_h)
                    break
        if not hit:
            continue
        h, rate, meth, capacity, agents_hit = hit
        agents_needed = None
        if secu_mode and agent_rate > 0:
            agents_needed = max(1, int(-(-rate // agent_rate)) - int(agents_hit or 0))

        if secu_mode:
            try:
                renotify = int(p.get("renotify_min") or dedup_min)
            except (TypeError, ValueError):
                renotify = dedup_min
            prev = db["cockpit_active_alerts"].find_one(
                {"definition_slug": slug, "actionData.door_key": key,
                 "triggeredAt": {"$gte": now - timedelta(minutes=max(renotify, dedup_min))}},
                sort=[("triggeredAt", -1)])
            if prev:
                prev_need = ((prev.get("actionData") or {}).get("agents_needed") or 0)
                recent = prev.get("triggeredAt")
                if isinstance(recent, datetime) and recent.tzinfo is None:
                    recent = recent.replace(tzinfo=timezone.utc)
                too_soon = isinstance(recent, datetime) and now - recent < timedelta(minutes=dedup_min)
                worse_ok = bool(p.get("renotify_on_worse")) and (agents_needed or 0) > prev_need
                if too_soon or not worse_ok:
                    continue
        else:
            since = now - timedelta(minutes=dedup_min)
            if db["cockpit_active_alerts"].find_one({"definition_slug": slug, "actionData.door_key": key,
                                                      "triggeredAt": {"$gte": since}}):
                continue
        at_label = now_label + timedelta(minutes=h)
        at_str = at_label.strftime("%H:%M")
        n_tri = sum(1 for k in e["devices"].values() if k == "tripode")
        n_pda = sum(1 for k in e["devices"].values() if k == "pda")
        err_pct = round(100.0 * e["err"] / (e["ok"] + e["err"]), 1) if (e["ok"] + e["err"]) else None
        if secu_mode:
            quand = "des maintenant" if h == 0 else "attendus vers %s" % at_str
            msg = "%s : ~%d/h %s pour %d agent%s securite (%d/h) - prevoir +%d agent%s" % (
                e["name"], round(rate, -1), quand, agents_hit, "s" if agents_hit > 1 else "",
                capacity, agents_needed, "s" if agents_needed > 1 else "")
        elif h == 0:
            msg = "%s : ~%d/h des maintenant (capacite %d/h)" % (e["name"], round(rate, -1), capacity)
        else:
            msg = "%s : ~%d/h prevu vers %s (capacite %d/h)" % (e["name"], round(rate, -1), at_str, capacity)
        bucket30 = int(now.timestamp() // (dedup_min * 60))
        results.append({
            "definition_slug": slug,
            "event": context.get("event", "") or event,
            "year": str(context.get("year", "") or year),
            "title": "RENFORT SECURITE PORTE" if secu_mode else "SATURATION PORTE PREVUE",
            "message": msg,
            "timeStr": at_str,
            "actionData": {
                "door": e["name"],
                "door_key": key,
                "current_rate": int(round(cur)),
                "predicted_rate": int(round(rate)),
                "predicted_at": at_str,
                "minutes_ahead": h,
                "capacity": int(round(capacity)),
                "capacity_source": cap_src,
                "threshold_pct": thr_pct,
                "devices": {"tripode": n_tri, "pda": n_pda, "total": len(e["devices"])},
                "method": meth,
                "n1_year": (year - 1) if n1_now is not None else None,
                "n1_source": n1_source if n1_now is not None else None,
                "n1_rate_now": int(round(n1_now)) if n1_now is not None else None,
                "sens": sens,
                "error_pct": err_pct,
                "capacity_mode": "securite" if secu_mode else "scan",
                "security_agents": agents_hit if secu_mode else None,
                "agents_needed": agents_needed,
                "agent_rate_h": agent_rate if secu_mode else None,
            },
            "dedup_key": "door-sat-%s-%s-%d" % (slug, key.replace(" ", "_"), bucket30),
            "triggeredAt": now,
            "expiresAt": now + timedelta(minutes=30),
        })
        log.info("  door_saturation_forecast: %s", msg)
    return results or None


# ---------------------------------------------------------------------------
# Registre des handlers
# ---------------------------------------------------------------------------

HANDLERS = {
    "schedule_proximity": detect_schedule_proximity,
    "schedule_transition": detect_schedule_transition,
    "traffic_cluster": detect_traffic_cluster,
    # anpr_watchlist : remplace par direct-write dans ecoutehik2.py (cockpit_dispatch)
    # camera_event   : direct-write dans ecoutehik2.py (cockpit_dispatch)
    "meteo_threshold": detect_meteo_threshold,
    "checkpoint_reassign": detect_checkpoint_reassign,
    "checkpoint_error_burst": detect_checkpoint_error_burst,
    "meteo_rain_onset": detect_meteo_rain_onset,
    "pcorg_urgency": detect_pcorg_urgency,
    "door_saturation_forecast": detect_door_saturation_forecast,
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
# Historique (widget Alertes de l'accueil)
# ---------------------------------------------------------------------------

def sync_alert_history(db):
    """Copie dans cockpit_alert_history chaque alerte active pas encore
    historisee, quelle que soit sa source (ce moteur, SOS tablette, cameras
    via cockpit_dispatch, mots-cles Alfred).

    Avant, chaque poste postait sa propre copie au moment de l'afficher :
    aucun poste ouvert = aucun historique, et le serveur dedoublonnait sur le
    texte du message. Une entree par alerte (cle alert_id, index unique).
    """
    col = db["cockpit_active_alerts"]
    hist = db["cockpit_alert_history"]
    n = 0
    for a in col.find({"historized": {"$ne": True}}).limit(200):
        ad = a.get("actionData") or {}
        try:
            hist.update_one(
                {"alert_id": str(a["_id"])},
                {"$setOnInsert": {
                    "alert_id": str(a["_id"]),
                    "type": a.get("definition_slug") or "",
                    "title": ad.get("title") or a.get("title") or "",
                    "timeStr": a.get("timeStr") or "",
                    "message": a.get("message") or "",
                    "hasAction": bool(ad.get("pins")),
                    "actionData": ad or None,
                    "createdAt": a.get("triggeredAt") or datetime.now(timezone.utc),
                }},
                upsert=True,
            )
            col.update_one({"_id": a["_id"]}, {"$set": {"historized": True}})
            n += 1
        except Exception as e:
            log.warning("Historisation alerte %s: %s", a.get("_id"), e)
    if n:
        log.info("  -> %d alerte(s) historisee(s)", n)


# ---------------------------------------------------------------------------
# Cycle principal
# ---------------------------------------------------------------------------

def run_cycle(sim_time=None):
    db = get_db()
    defs = load_enabled_definitions(db)
    if not defs:
        log.info("Aucune definition d'alerte active")
        sync_alert_history(db)
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

    # Historique : alertes de ce cycle et celles ecrites par les autres sources
    sync_alert_history(db)

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
