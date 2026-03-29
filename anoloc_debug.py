#!/usr/bin/env python3
"""
anoloc_debug.py - Script de debug pour inspecter les donnees Anoloc en direct.

Usage:
    python anoloc_debug.py              # Affiche tout
    python anoloc_debug.py live         # Donnees live brutes de l'API Anoloc
    python anoloc_debug.py devices      # Liste des devices depuis l'API Anoloc
    python anoloc_debug.py mongo        # Contenu de anoloc_latest dans MongoDB
    python anoloc_debug.py compare      # Compare API live vs MongoDB
    python anoloc_debug.py raw DEVICE_ID  # Frame brute d'un device specifique
"""

import os
import sys
import json
from datetime import datetime, timezone

import requests
from pymongo import MongoClient

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Paris")
except ImportError:
    import dateutil.tz
    TZ = dateutil.tz.gettz("Europe/Paris")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "titan"
USER_AGENT = "COCKPIT-TITAN-DEBUG/1.0"


def get_db():
    client = MongoClient(MONGO_URI)
    return client[DB_NAME]


def get_config():
    db = get_db()
    config = db["anoloc_config"].find_one({"_id": "global"})
    if not config:
        print("ERREUR: Pas de config anoloc_config.global dans MongoDB")
        sys.exit(1)
    return config


def anoloc_login(config):
    api_base = (config.get("api_base") or "").rstrip("/") or "https://app.lemans.anoloc.io/api/v3"
    login = config.get("login", "")
    password = config.get("password", "")
    resp = requests.post(
        f"{api_base}/login",
        json={"login": login, "password": password, "remember_me": True},
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("data", {}).get("token") or data.get("token")
    return api_base, token


def anoloc_get(api_base, token, endpoint):
    resp = requests.get(
        f"{api_base}{endpoint}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def fmt_date(d):
    """Formate une date en heure Paris lisible."""
    if not d:
        return "-"
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d.replace("Z", "+00:00"))
        except Exception:
            return d
    if hasattr(d, "astimezone"):
        d = d.astimezone(TZ)
    return d.strftime("%d/%m/%Y %H:%M:%S")


def time_ago(d):
    """Retourne un texte 'il y a Xmin' depuis une date."""
    if not d:
        return ""
    now = datetime.now(timezone.utc)
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d.replace("Z", "+00:00"))
        except Exception:
            return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    delta = (now - d).total_seconds()
    if delta < 60:
        return f"(il y a {int(delta)}s)"
    elif delta < 3600:
        return f"(il y a {int(delta/60)}min)"
    elif delta < 86400:
        return f"(il y a {int(delta/3600)}h{int((delta%3600)/60):02d})"
    else:
        return f"(il y a {int(delta/86400)}j)"


def cmd_live():
    """Affiche les donnees live brutes depuis l'API Anoloc."""
    config = get_config()
    api_base, token = anoloc_login(config)
    print(f"\n=== API LIVE ({api_base}/live) ===\n")

    result = anoloc_get(api_base, token, "/live")
    data = result.get("data") or result

    for dev_id, frame in sorted(data.items()):
        if not isinstance(frame, dict):
            print(f"  {dev_id}: [OFFLINE - pas de frame]")
            continue

        status = frame.get("status", "?")
        lat = frame.get("latitude")
        lng = frame.get("longitude")
        speed = frame.get("speed", 0)
        heading = frame.get("heading", 0)
        sent_at = frame.get("sent_at", "")
        last_real = frame.get("last_real_frame_sent_at", "")
        power = frame.get("power_supply") or {}
        bat = power.get("battery_percentage", "?")
        gsm = frame.get("gsm_level", "?")
        gps_fix = frame.get("gps_fix", "?")
        gps_view = frame.get("gps_view", "?")
        address = frame.get("address", "")

        print(f"  {dev_id}:")
        print(f"    status:     {status}")
        print(f"    position:   {lat}, {lng}")
        print(f"    speed:      {speed} km/h  heading: {heading}")
        print(f"    sent_at:    {fmt_date(sent_at)} {time_ago(sent_at)}")
        print(f"    last_real:  {fmt_date(last_real)} {time_ago(last_real)}")
        print(f"    batterie:   {bat}%  GSM: {gsm}  GPS fix: {gps_fix} view: {gps_view}")
        if address:
            print(f"    adresse:    {address}")
        print()


def cmd_devices():
    """Liste les devices depuis l'API Anoloc."""
    config = get_config()
    api_base, token = anoloc_login(config)
    print(f"\n=== DEVICES ({api_base}/devices) ===\n")

    result = anoloc_get(api_base, token, "/devices")
    devices = result.get("data", [])

    for dev in devices:
        print(f"  {dev.get('id')}:")
        print(f"    label:  {dev.get('label')}")
        print(f"    imei:   {dev.get('imei')}")
        print(f"    serial: {dev.get('serial')}")
        group = dev.get("group") or {}
        if group:
            print(f"    group:  {group.get('label')} ({group.get('id')})")
        last = dev.get("last_frame") or {}
        if last:
            print(f"    last_frame.status:  {last.get('status')}")
            print(f"    last_frame.sent_at: {fmt_date(last.get('sent_at'))} {time_ago(last.get('sent_at'))}")
        print()


def cmd_mongo():
    """Affiche le contenu de anoloc_latest dans MongoDB."""
    db = get_db()
    docs = list(db["anoloc_latest"].find())

    print(f"\n=== MONGODB anoloc_latest ({len(docs)} documents) ===\n")

    for doc in sorted(docs, key=lambda d: d.get("label", "")):
        dev_id = doc["_id"]
        print(f"  {dev_id} ({doc.get('label', '?')}):")
        print(f"    status:       {doc.get('status')}")
        print(f"    position:     {doc.get('lat')}, {doc.get('lng')}")
        print(f"    speed:        {doc.get('speed')} km/h")
        print(f"    batterie:     {doc.get('battery_pct')}%")
        print(f"    sent_at:      {fmt_date(doc.get('sent_at'))} {time_ago(doc.get('sent_at'))}")
        print(f"    collected_at: {fmt_date(doc.get('collected_at'))} {time_ago(doc.get('collected_at'))}")
        print(f"    groupe:       {doc.get('group_label')} ({doc.get('beacon_group')})")
        print()


def cmd_compare():
    """Compare les donnees API live avec MongoDB."""
    config = get_config()
    api_base, token = anoloc_login(config)
    db = get_db()

    result = anoloc_get(api_base, token, "/live")
    live_data = result.get("data") or result
    latest_docs = {doc["_id"]: doc for doc in db["anoloc_latest"].find()}

    # Mapping des device IDs configures
    device_group_map = {}
    for grp in config.get("beacon_groups", []):
        if not grp.get("enabled", True):
            continue
        for dev_id in grp.get("anoloc_device_ids", []):
            device_group_map[dev_id] = grp

    # Devices depuis l'API
    dev_result = anoloc_get(api_base, token, "/devices")
    devices_info = {d["id"]: d for d in dev_result.get("data", [])}

    print(f"\n=== COMPARAISON API vs MONGODB ===\n")
    print(f"  Devices dans l'API /live: {len(live_data)}")
    print(f"  Devices dans anoloc_latest: {len(latest_docs)}")
    print(f"  Devices configures: {len(device_group_map)}")
    print()

    all_ids = sorted(set(list(live_data.keys()) + list(latest_docs.keys()) + list(device_group_map.keys())))

    for dev_id in all_ids:
        dev_info = devices_info.get(dev_id, {})
        label = dev_info.get("label", dev_id)
        in_config = dev_id in device_group_map
        in_live = dev_id in live_data
        in_mongo = dev_id in latest_docs

        frame = live_data.get(dev_id)
        mongo_doc = latest_docs.get(dev_id)

        print(f"  {label} ({dev_id}):")
        print(f"    configure: {'OUI' if in_config else 'NON'}")
        print(f"    dans /live: {'OUI' if in_live else 'NON'}", end="")
        if in_live and isinstance(frame, dict):
            print(f" -> status={frame.get('status')} sent_at={fmt_date(frame.get('sent_at'))} {time_ago(frame.get('sent_at'))}")
        elif in_live:
            print(f" -> [liste vide = offline]")
        else:
            print()

        print(f"    dans mongo: {'OUI' if in_mongo else 'NON'}", end="")
        if in_mongo:
            print(f" -> status={mongo_doc.get('status')} collected_at={fmt_date(mongo_doc.get('collected_at'))} {time_ago(mongo_doc.get('collected_at'))}")
        else:
            print()

        if in_live and isinstance(frame, dict) and in_mongo:
            api_sent = frame.get("sent_at", "")
            mongo_sent = mongo_doc.get("sent_at")
            if mongo_sent and hasattr(mongo_sent, "isoformat"):
                mongo_sent = mongo_sent.isoformat()
            same = str(api_sent) == str(mongo_sent)
            print(f"    sent_at identique: {'OUI' if same else 'NON (API=' + str(api_sent) + ' Mongo=' + str(mongo_sent) + ')'}")
        print()


def cmd_raw(device_id):
    """Affiche la frame brute complete d'un device."""
    config = get_config()
    api_base, token = anoloc_login(config)

    result = anoloc_get(api_base, token, "/live")
    data = result.get("data") or result
    frame = data.get(device_id)

    print(f"\n=== FRAME BRUTE pour {device_id} ===\n")
    if frame is None:
        print("  Device non trouve dans /live")
        # Chercher par label
        dev_result = anoloc_get(api_base, token, "/devices")
        for d in dev_result.get("data", []):
            if d.get("label", "").lower() == device_id.lower() or d.get("id") == device_id:
                print(f"  Trouve comme device: {d.get('id')} ({d.get('label')})")
                frame = data.get(d.get("id"))
                if frame:
                    print(json.dumps(frame, indent=2, default=str, ensure_ascii=False))
                else:
                    print(f"  Pas de frame live pour {d.get('id')}")
                return
        print("  Device non trouve du tout")
    elif isinstance(frame, list):
        print("  Frame = [] (device offline)")
    else:
        print(json.dumps(frame, indent=2, default=str, ensure_ascii=False))


def cmd_all():
    """Affiche tout."""
    cmd_live()
    cmd_mongo()
    cmd_compare()


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "all"

    if cmd == "live":
        cmd_live()
    elif cmd == "devices":
        cmd_devices()
    elif cmd == "mongo":
        cmd_mongo()
    elif cmd == "compare":
        cmd_compare()
    elif cmd == "raw" and len(args) > 1:
        cmd_raw(args[1])
    elif cmd == "all":
        cmd_all()
    else:
        print(__doc__)
