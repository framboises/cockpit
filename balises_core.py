"""balises_core.py - Coeur commun des balises GPS (Anoloc et Teltonika).

Plan et regles : docs/claude/balises.md.

Les groupes de balises (`anoloc_config.global.beacon_groups`) restent le
pivot unique : carte, traces, main courante, dispatch Field et centrale
d'alerte les lisent sans savoir d'ou vient une balise. Une balise Teltonika
y figure sous l'id `tlt:<imei>` (comme une tablette sous `field:<id>`), dans
la meme liste `anoloc_device_ids` (nom historique conserve).

Ingestion Teltonika (une seule fonction, `ingest`, pour toutes les sources) :
  - recepteur TCP maison (recepteur_balises/, POST signe /api/balises/ingest)
  - flespi (flespi_poller.py, API REST, le temps d'ouvrir le port 5027)
Toutes deux produisent le meme message normalise (cles a points, style
flespi). Cle d'idempotence (imei, timestamp balise) : les rattrapages, les
renvois sans ACK et une balise vue par les deux sources ne creent jamais de
doublon.
"""

import logging
from datetime import datetime, timezone, timedelta

from pymongo.errors import DuplicateKeyError

log = logging.getLogger("balises")

COL_DEVICES = "balises_devices"
COL_MESSAGES = "balises_messages"
COL_CONFIG = "balises_config"
COL_UNKNOWN = "balises_unknown"
COL_POSITIONS = "anoloc_positions"
COL_LATEST = "anoloc_latest"

TLT_PREFIX = "tlt:"
SOURCE_ANOLOC = "anoloc"
SOURCE_TELTONIKA = "teltonika"

# Historique des positions (balises Anoloc, Teltonika et tablettes Field) et
# des messages Teltonika : 10 mois (12/10/2026).
POSITIONS_TTL_S = 304 * 24 * 3600

# Horodatage balise accepte jusqu'a maintenant + 5 min ; avant la mise en
# service de la balise (ou cette date plancher) il est aberrant (horloge pas
# encore synchronisee : 31/12/2021 observe sur l'ATC700).
FUTURE_TOLERANCE_S = 300
TS_FLOOR = datetime(2024, 1, 1, tzinfo=timezone.utc)

# Periodes d'emission par defaut (modifiables dans Configuration > Balises GPS
# et par balise). L'ATC700 a l'arret : 1 message / ~15 min ; en mouvement
# beaucoup plus frequent (a mesurer).
DEFAULT_PERIOD_STOP_S = 900
DEFAULT_PERIOD_MOVE_S = 60
# Une balise est hors ligne apres MISSED_PERIODS periodes sans message.
MISSED_PERIODS = 3
ONLINE_GRACE_S = 120
# Vitesse au-dela de laquelle une balise sans capteur de mouvement roule.
MOVING_SPEED_KMH = 5

# Anoloc (regles historiques de anoloc.py, inchangees)
ANOLOC_STALE_S = 1800
ANOLOC_GPS_FRESH_S = 300


def _utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now():
    return datetime.now(timezone.utc)


def device_id_for(imei):
    return TLT_PREFIX + str(imei).strip()


def is_teltonika_id(device_id):
    return str(device_id or "").startswith(TLT_PREFIX)


def source_of(device_id):
    did = str(device_id or "")
    if did.startswith(TLT_PREFIX):
        return SOURCE_TELTONIKA
    if did.startswith("field:"):
        return "field"
    return SOURCE_ANOLOC


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def ensure_positions_ttl(db):
    """TTL de 10 mois sur anoloc_positions.collected_at.

    L'index collected_at existe deja sans TTL (anoloc_collector) : on le
    modifie par collMod (create_index avec d'autres options leverait
    IndexOptionsConflict). Idempotent."""
    col = db[COL_POSITIONS]
    for name, info in col.index_information().items():
        if info.get("key") == [("collected_at", 1)]:
            if info.get("expireAfterSeconds") != POSITIONS_TTL_S:
                db.command("collMod", COL_POSITIONS, index={
                    "keyPattern": {"collected_at": 1},
                    "expireAfterSeconds": POSITIONS_TTL_S,
                })
            return
    col.create_index("collected_at", expireAfterSeconds=POSITIONS_TTL_S)


def ensure_indexes(db):
    ensure_positions_ttl(db)
    db[COL_POSITIONS].create_index([("device_id", 1), ("collected_at", -1)])
    db[COL_MESSAGES].create_index([("imei", 1), ("ts", 1)], unique=True, name="imei_ts_unique")
    db[COL_MESSAGES].create_index([("device_id", 1), ("received_at", -1)])
    db[COL_MESSAGES].create_index("received_at", expireAfterSeconds=POSITIONS_TTL_S)
    db[COL_DEVICES].create_index("imei", unique=True)


# ---------------------------------------------------------------------------
# Parc Teltonika et reglages
# ---------------------------------------------------------------------------

def get_settings(db):
    doc = db[COL_CONFIG].find_one({"_id": "teltonika"}) or {}
    return {
        "period_stop_s": int(doc.get("period_stop_s") or DEFAULT_PERIOD_STOP_S),
        "period_move_s": int(doc.get("period_move_s") or DEFAULT_PERIOD_MOVE_S),
    }


def device_periods(device, settings):
    device = device or {}
    return (int(device.get("period_stop_s") or settings["period_stop_s"]),
            int(device.get("period_move_s") or settings["period_move_s"]))


def list_devices(db):
    return list(db[COL_DEVICES].find({}).sort("label", 1))


def devices_by_id(db):
    return {d["_id"]: d for d in db[COL_DEVICES].find({})}


def whitelist(db):
    return sorted(d["imei"] for d in db[COL_DEVICES].find({"enabled": True}, {"imei": 1}))


def clean_imei(raw):
    s = str(raw or "").strip()
    return s if s.isdigit() and 8 <= len(s) <= 20 else None


def save_device(db, body, now=None):
    """Cree ou met a jour une balise Teltonika. (doc, erreur)."""
    now = now or _now()
    imei = clean_imei(body.get("imei"))
    if not imei:
        return None, "imei_invalide"
    label = str(body.get("label") or "").strip()[:60]
    if not label:
        return None, "nom_requis"
    did = device_id_for(imei)

    def _period(key):
        v = body.get(key)
        if v in (None, ""):
            return None
        try:
            v = int(v)
        except (TypeError, ValueError):
            return None
        return max(10, min(v, 24 * 3600))

    commissioned = body.get("commissioned_at")
    if isinstance(commissioned, str) and commissioned:
        try:
            commissioned = _utc(datetime.fromisoformat(commissioned.replace("Z", "+00:00")))
        except ValueError:
            return None, "date_invalide"
    elif not isinstance(commissioned, datetime):
        commissioned = None
    existing = db[COL_DEVICES].find_one({"_id": did}) or {}
    doc = {
        "imei": imei,
        "label": label,
        "model": str(body.get("model") or "ATC700").strip()[:30],
        "enabled": bool(body.get("enabled", True)),
        # Facultative : sans elle, plancher TS_FLOOR (rejette deja les dates
        # d'horloge non synchronisee)
        "commissioned_at": commissioned or existing.get("commissioned_at"),
        "period_stop_s": _period("period_stop_s"),
        "period_move_s": _period("period_move_s"),
        "notes": str(body.get("notes") or "").strip()[:300],
        "updated_at": now,
    }
    db[COL_DEVICES].update_one({"_id": did}, {"$set": doc, "$setOnInsert": {"created_at": now}},
                               upsert=True)
    # Libelle repris dans anoloc_latest (carte) et dans les groupes
    # (device_labels : rapprochement main courante, collisions de noms)
    db[COL_LATEST].update_one({"_id": did}, {"$set": {"label": label}})
    db["anoloc_config"].update_many(
        {"_id": "global", "beacon_groups.anoloc_device_ids": did},
        {"$set": {"beacon_groups.$[g].device_labels.%s" % did: label}},
        array_filters=[{"g.anoloc_device_ids": did}],
    )
    db[COL_UNKNOWN].delete_one({"_id": imei})
    return db[COL_DEVICES].find_one({"_id": did}), None


def delete_device(db, imei):
    """Retire une balise du parc (refusee ensuite par le recepteur). Son
    historique reste (TTL) ; elle est retiree des groupes."""
    did = device_id_for(imei)
    db[COL_DEVICES].delete_one({"_id": did})
    db[COL_LATEST].delete_one({"_id": did})
    db["anoloc_config"].update_one(
        {"_id": "global"},
        {"$pull": {"beacon_groups.$[].anoloc_device_ids": did},
         "$unset": {"beacon_groups.$[].device_labels.%s" % did: ""}},
    )


def note_unknown(db, imei, source, peer=None, now=None):
    """IMEI inconnu vu (refuse par le recepteur, ignore depuis flespi) :
    affiche dans l'admin pour l'ajouter au parc en un clic."""
    imei = clean_imei(imei)
    if not imei:
        return
    now = now or _now()
    db[COL_UNKNOWN].update_one(
        {"_id": imei},
        {"$set": {"last_seen": now, "source": source, "peer": peer},
         "$inc": {"count": 1}, "$setOnInsert": {"first_seen": now}},
        upsert=True,
    )


# ---------------------------------------------------------------------------
# Groupes
# ---------------------------------------------------------------------------

def group_map(db):
    """{device_id: groupe} pour les groupes actifs."""
    cfg = db["anoloc_config"].find_one({"_id": "global"}, {"beacon_groups": 1}) or {}
    out = {}
    for g in cfg.get("beacon_groups") or []:
        if g.get("enabled") is False:
            continue
        for did in g.get("anoloc_device_ids") or []:
            out.setdefault(did, g)
    return out


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def _ts(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v > 1e12:  # millisecondes
        v /= 1000.0
    try:
        return datetime.fromtimestamp(v, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def ts_valid(ts, now, device):
    if ts is None:
        return False
    floor = TS_FLOOR
    commissioned = _utc((device or {}).get("commissioned_at"))
    if commissioned:
        # Mise en service saisie apres les premiers essais : tolerance 1 jour
        floor = max(floor, commissioned - timedelta(days=1))
    return floor <= ts <= now + timedelta(seconds=FUTURE_TOLERANCE_S)


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _nest(msg):
    """Cles a points -> documents imbriques (Mongo et les points dans les
    noms de champ ne font pas bon menage). Une collision (cle a la fois
    feuille et prefixe) garde la cle a plat avec des '_'."""
    out = {}
    for key, val in msg.items():
        parts = str(key).split(".")
        cur = out
        ok = True
        for p in parts[:-1]:
            nxt = cur.get(p)
            if nxt is None:
                nxt = cur[p] = {}
            elif not isinstance(nxt, dict):
                ok = False
                break
            cur = nxt
        if ok and not isinstance(cur.get(parts[-1]), dict):
            cur[parts[-1]] = val
        else:
            out[str(key).replace(".", "_")] = val
    return out


def summarize(msg):
    """Champs utiles extraits d'un message normalise."""
    valid = bool(msg.get("position.valid"))
    lat = _num(msg.get("position.latitude"))
    lng = _num(msg.get("position.longitude"))
    if lat is None or lng is None or (lat == 0 and lng == 0):
        valid = False
    speed = _num(msg.get("position.speed"))
    movement = msg.get("movement.status")
    ext_v = _num(msg.get("external.powersource.voltage"))
    out = {
        "valid": valid, "lat": lat, "lng": lng, "speed": speed,
        "heading": _num(msg.get("position.direction")),
        "alt": _num(msg.get("position.altitude")),
        "sats": _num(msg.get("position.satellites")),
        "hdop": _num(msg.get("position.hdop")),
        "movement": bool(movement) if movement is not None else None,
        "battery_pct": _num(msg.get("battery.level")),
        "battery_voltage": _num(msg.get("battery.voltage")),
        "external_voltage": ext_v,
        "charging": (ext_v > 4.0) if ext_v is not None else None,
        "gsm_level": _num(msg.get("gsm.signal.level")),
        "gnss_state": _num(msg.get("gnss.state.enum")),
    }
    if out["battery_pct"] is not None:
        out["battery_pct"] = int(round(out["battery_pct"]))
    return out


def _moving(s):
    # Le capteur de mouvement de l'ATC700 retombe parfois a false en roulant
    # (vu le 13/10/2026 : "arret" a 66 km/h) : une vitesse GPS franche l'emporte
    if s["valid"] and (s["speed"] or 0) >= MOVING_SPEED_KMH:
        return True
    if s["movement"] is not None:
        return s["movement"]
    return False


def ingest(db, messages, source, now=None):
    """Enregistre des messages normalises. Rend les compteurs
    {accepted, duplicates, unknown, invalid, bad_ts}.

    - IMEI absent du parc (ou desactive) : ignore, note dans balises_unknown
    - (imei, timestamp) deja vu : doublon ignore (index unique)
    - timestamp aberrant : message garde (balises_messages, ts_valid false)
      mais ni position ni etat courant
    - sans fix : telemetrie (batterie, signal...) mise a jour, pas la position
    - etat courant (anoloc_latest) : seulement si le message est plus recent
      que celui deja affiche (un rattrapage arrive apres le temps reel)
    """
    now = now or _now()
    stats = {"accepted": 0, "duplicates": 0, "unknown": 0, "invalid": 0, "bad_ts": 0}
    devices = {d["imei"]: d for d in db[COL_DEVICES].find({"enabled": True})}
    groups = group_map(db)
    for msg in messages or []:
        if not isinstance(msg, dict):
            stats["invalid"] += 1
            continue
        imei = clean_imei(msg.get("ident"))
        if not imei:
            stats["invalid"] += 1
            continue
        device = devices.get(imei)
        if not device:
            stats["unknown"] += 1
            note_unknown(db, imei, source, msg.get("peer"), now)
            continue
        ts = _ts(msg.get("timestamp"))
        if ts is None:
            stats["invalid"] += 1
            continue
        did = device["_id"]
        good_ts = ts_valid(ts, now, device)
        s = summarize(msg)
        server_ts = _ts(msg.get("server.timestamp"))
        try:
            db[COL_MESSAGES].insert_one({
                "imei": imei, "device_id": did, "ts": ts, "ts_valid": good_ts,
                "received_at": now, "server_ts": server_ts, "source": source,
                "position_valid": s["valid"], "lat": s["lat"], "lng": s["lng"],
                "speed": s["speed"], "battery_pct": s["battery_pct"],
                "movement": s["movement"], "msg": _nest(msg),
            })
        except DuplicateKeyError:
            stats["duplicates"] += 1
            continue
        stats["accepted"] += 1
        if not good_ts:
            stats["bad_ts"] += 1
            continue
        grp = groups.get(did) or {}
        moving = _moving(s)
        status = "running" if moving else "stopped"
        if s["valid"]:
            db[COL_POSITIONS].insert_one({
                "device_id": did, "beacon_group": grp.get("id", ""),
                "label": device.get("label") or imei,
                "lat": s["lat"], "lng": s["lng"], "speed": s["speed"] or 0,
                "heading": s["heading"] or 0, "status": status,
                "battery_pct": s["battery_pct"], "gps_fix": 1,
                "sent_at": ts, "last_real_at": ts,
                # collected_at = horodatage BALISE (traces et TTL) : un
                # rattrapage se range a sa place, pas a l'heure de reception
                "collected_at": ts, "received_at": now,
                "source": SOURCE_TELTONIKA, "imei": imei,
            })
        _update_latest(db, did, device, grp, ts, s, status, source, now)
    return stats


def _update_latest(db, did, device, grp, ts, s, status, source, now):
    col = db[COL_LATEST]
    col.update_one({"_id": did}, {
        "$setOnInsert": {"device_id": did, "imei": device["imei"], "source": SOURCE_TELTONIKA},
        "$set": {"label": device.get("label") or device["imei"],
                 "beacon_group": grp.get("id", ""), "group_label": grp.get("label", ""),
                 "icon": grp.get("icon", "location_on"), "color": grp.get("color", "#6366f1")},
    }, upsert=True)
    tel = {"last_msg_at": ts, "received_at": now, "ingest_source": source,
           "status": status, "movement": _moving(s), "gps_valid_last": s["valid"]}
    for k in ("battery_pct", "battery_voltage", "external_voltage", "charging",
              "gsm_level", "gnss_state"):
        if s[k] is not None:
            tel[k] = s[k]
    col.update_one({"_id": did, "$or": [{"last_msg_at": {"$exists": False}},
                                        {"last_msg_at": {"$lte": ts}}]}, {"$set": tel})
    if s["valid"]:
        col.update_one({"_id": did, "$or": [{"last_pos_at": {"$exists": False}},
                                            {"last_pos_at": {"$lte": ts}}]}, {"$set": {
            "lat": s["lat"], "lng": s["lng"], "speed": s["speed"] or 0,
            "heading": s["heading"] or 0, "alt": s["alt"], "sats": s["sats"], "hdop": s["hdop"],
            "last_pos_at": ts, "sent_at": ts, "last_real_at": ts, "collected_at": ts,
        }})


# ---------------------------------------------------------------------------
# Etat courant (carte, compteurs, centrale d'alerte) : UNE regle par source
# ---------------------------------------------------------------------------

def device_state(doc, now=None, device=None, settings=None):
    """{online, gps_fix, last_at, age_s, expected_s} d'un document
    anoloc_latest, quelle que soit la source.

    Anoloc : statut Anoloc != offline et derniere vraie trame < 30 min ;
    GPS si cette trame a < 5 min (regles historiques).
    Teltonika : en ligne tant que le dernier message a moins de
    MISSED_PERIODS periodes attendues (periode en mouvement si le dernier
    message disait 'en mouvement', sinon periode a l'arret) ; GPS = le
    dernier message avait un fix valide."""
    now = now or _now()
    doc = doc or {}
    if is_teltonika_id(doc.get("_id") or doc.get("device_id")):
        settings = settings or {"period_stop_s": DEFAULT_PERIOD_STOP_S,
                                "period_move_s": DEFAULT_PERIOD_MOVE_S}
        stop_s, move_s = device_periods(device, settings)
        expected = move_s if doc.get("movement") else stop_s
        last = _utc(doc.get("last_msg_at"))
        age = (now - last).total_seconds() if last else None
        online = age is not None and age <= MISSED_PERIODS * expected + ONLINE_GRACE_S
        return {"online": online, "gps_fix": 1 if online and doc.get("gps_valid_last") else 0,
                "last_at": last, "age_s": age, "expected_s": expected}
    status = doc.get("status", "offline")
    online = status != "offline"
    last_real = _utc(doc.get("last_real_at"))
    age = (now - last_real).total_seconds() if last_real else None
    if online and age is not None and age > ANOLOC_STALE_S:
        online = False
    gps_fix = doc.get("gps_fix", 0)
    if last_real and online:
        gps_fix = 1 if age < ANOLOC_GPS_FRESH_S else 0
    return {"online": online, "gps_fix": gps_fix, "last_at": last_real or _utc(doc.get("collected_at")),
            "age_s": age, "expected_s": ANOLOC_STALE_S}
