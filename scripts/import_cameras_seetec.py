"""Import de l'inventaire cameras exporte de SeeTec (InstallationConfiguration.xlsx).

    python scripts/import_cameras_seetec.py <fichier.xlsx>            # a blanc : rapport seul
    python scripts/import_cameras_seetec.py <fichier.xlsx> --apply    # ecrit en base

- Une ligne "source video" SeeTec = un canal ; les canaux d'une meme IP forment
  un appareil (cockpit_cameras). Numero de canal = ordre d'apparition dans
  l'export (a verifier sur les multi-capteurs, cf. docs/claude/cameras.md).
- Chaque couple (utilisateur, mot de passe) devient un profil d'identifiants
  chiffre (cam_credential_profiles, camera_secrets.py). Aucun mot de passe
  n'est affiche ni journalise.
- Fusion par IP avec l'existant : _id conserve (captures de la main courante),
  chemin d'envoi, usages, emplacement et etiquettes conserves ; l'ancien champ
  "password" en clair est supprime.
- Usages repris : LAPI depuis anpr_camera_config (via l'IP des passages), et
  chemin d'envoi des autres cameras depuis les evenements deja recus.

Necessite COCKPIT_CAMERA_KEY (et TITAN_ENV=prod pour la base titan).
"""
import collections
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import load_workbook  # noqa: E402
from pymongo import MongoClient  # noqa: E402

import camera_secrets  # noqa: E402

BRAND_PREFIXES = [
    (("DS-", "iDS-", "HM-TD"), "hikvision"),
    (("AXIS", "P1455"), "axis"),
    (("NDP", "MIC", "NBN", "NDE", "AUTODOME"), "bosch"),
    (("SNP", "QNV", "XND", "XNP", "PNM", "XNV", "QND", "XNO", "PNV", "QNO", "TNO", "ANV"), "hanwha"),
    (("DH-",), "dahua"),
    (("FD", "IB", "IP8"), "vivotek"),
    (("IPC",), "uniview"),
]
BRAND_LABEL = {"hikvision": "Hikvision", "axis": "Axis", "bosch": "Bosch", "hanwha": "Hanwha",
               "dahua": "Dahua", "vivotek": "Vivotek", "uniview": "Uniview", "generic": "Generique"}


def brand_of(model):
    m = str(model or "").strip()
    for prefixes, brand in BRAND_PREFIXES:
        if m.startswith(prefixes):
            return brand
    return "generic"


def truthy(v):
    return str(v).strip().lower() == "true"


def read_export(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    rows = list(wb["Camera"].iter_rows(values_only=True))
    hdr = rows[1]
    devices = collections.OrderedDict()
    for r in rows[2:]:
        if r[0] is None:
            continue  # lignes de detail d'enregistrement
        d = dict(zip(hdr, r))
        ip = str(d.get("IP address") or "").strip()
        if not ip:
            continue
        dev = devices.setdefault(ip, {"ip": ip, "sources": []})
        dev["sources"].append(d)
    return devices


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path, apply = sys.argv[1], "--apply" in sys.argv
    if not camera_secrets.available():
        print("COCKPIT_CAMERA_KEY absente ou invalide : arret.")
        sys.exit(2)
    env = os.getenv("TITAN_ENV", "dev").strip().lower()
    db = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))["titan" if env in ("prod", "production") else "titan_dev"]
    col, col_prof = db["cockpit_cameras"], db["cam_credential_profiles"]
    now = datetime.now(timezone.utc)
    devices = read_export(path)
    print("Base : %s | export : %d sources, %d appareils" % (db.name, sum(len(d["sources"]) for d in devices.values()), len(devices)))

    # 1) Profils d'identifiants (un par couple utilisateur / mot de passe)
    pairs = collections.OrderedDict()
    for dev in devices.values():
        s0 = dev["sources"][0]
        key = (str(s0.get("User name") or "").strip(), str(s0.get("Password") or ""))
        p = pairs.setdefault(key, {"devices": 0, "brands": collections.Counter()})
        p["devices"] += 1
        p["brands"][brand_of(s0.get("Type"))] += 1
    # Profils deja crees par un import precedent : reconnus par dechiffrement
    existing = []
    for prof in col_prof.find():
        try:
            existing.append(((prof.get("user", ""), camera_secrets.decrypt(prof.get("secret", ""))), prof))
        except Exception:
            pass
    profile_ids = {}
    used_names = {p["name"] for _, p in existing}
    for idx, (key, info) in enumerate(sorted(pairs.items(), key=lambda kv: -kv[1]["devices"]), 1):
        found = next((p for k, p in existing if k == key), None)
        brand = info["brands"].most_common(1)[0][0]
        if found:
            profile_ids[key] = found["_id"]
            print("  profil existant  %-28s %3d appareils" % (found["name"], info["devices"]))
            continue
        name = "%s - %s" % (BRAND_LABEL.get(brand, brand), key[0] or "sans utilisateur")
        n = 2
        while name in used_names:
            name = "%s - %s (%d)" % (BRAND_LABEL.get(brand, brand), key[0] or "sans utilisateur", n)
            n += 1
        used_names.add(name)
        print("  nouveau profil   %-28s %3d appareils" % (name, info["devices"]))
        if apply:
            res = col_prof.insert_one({
                "name": name, "user": key[0], "secret": camera_secrets.encrypt(key[1]) if key[1] else "",
                "note": "Importe de SeeTec le %s" % now.strftime("%d/%m/%Y"),
                "createdAt": now, "updatedAt": now,
            })
            profile_ids[key] = res.inserted_id

    # 2) Chemins d'envoi deja observes (IP -> chemin) et configuration LAPI
    path_by_ip = {}
    for c in ("hik_alerts", "hik_anpr", "hik_detections", "hik_traffic"):
        for r in db[c].aggregate([{"$group": {"_id": {"p": "$camera_path", "ip": "$ip_address"}}}]):
            ip, p = r["_id"].get("ip"), r["_id"].get("p")
            if ip and p and p != "/lapisud51":
                path_by_ip.setdefault(ip, set()).add(p)
    anpr_cfg = {c["camera_path"]: c for c in db["anpr_camera_config"].find()}

    # 3) Appareils
    stats = collections.Counter()
    for ip, dev in devices.items():
        srcs = dev["sources"]
        s0 = srcs[0]
        model = str(s0.get("Type") or "").strip()
        fp = str(s0.get("File-Path") or "")
        parts = [p.strip() for p in fp.split("-->")]
        key = (str(s0.get("User name") or "").strip(), str(s0.get("Password") or ""))
        channels = []
        for i, s in enumerate(srcs, 1):
            channels.append({"no": i, "name": str(s.get("Name") or "").strip() or "Canal %d" % i,
                             "enabled": truthy(s.get("Source Activated")), "roles": []})
        fields = {
            "name": channels[0]["name"],
            "ip": ip,
            "brand": brand_of(model),
            "model": model,
            "firmware": str(s0.get("Firmware") or "").strip() if str(s0.get("Firmware") or "") != "Undefined" else "",
            "site": parts[1] if len(parts) > 1 else "",
            "group_path": fp,
            "vms_server": str(s0.get("DeviceManager") or "").strip(),
            "credential_profile_id": profile_ids.get(key),
            "source": {"seetec": True, "imported_at": now},
            "updatedAt": now,
        }
        cur = col.find_one({"ip": ip})
        paths = sorted(path_by_ip.get(ip, []))
        if cur:
            stats["mis a jour"] += 1
            # Conserver usages et noms de canaux deja saisis dans Cockpit
            old = {c.get("no"): c for c in (cur.get("channels") or [])}
            for ch in channels:
                if ch["no"] in old:
                    ch["roles"] = old[ch["no"]].get("roles") or []
            if not cur.get("camera_path") and len(paths) == 1:
                fields["camera_path"] = paths[0]
        else:
            stats["nouveaux"] += 1
            fields.update({"port": 80, "protocol": "http", "enabled": True, "location": fields["site"],
                           "tags": [], "createdAt": now, "camera_path": paths[0] if len(paths) == 1 else ""})
        fields["channels"] = channels
        cpath = fields.get("camera_path") or (cur or {}).get("camera_path") or ""
        # Usage LAPI repris de anpr_camera_config
        if cpath in anpr_cfg:
            cfg = anpr_cfg[cpath]
            ch = channels[0]
            if not any(r.get("type") == "anpr" for r in ch["roles"]):
                ch["roles"].append({"type": "anpr", "forward_role": cfg.get("forward_role", "entry"),
                                    "lieu": cfg.get("lieu", ""), "visible": cfg.get("enabled", True)})
                stats["usage LAPI repris"] += 1
        if len(paths) > 1:
            print("  ! %s envoie sur plusieurs chemins %s : a choisir dans la page" % (ip, paths))
        if apply:
            col.update_one({"ip": ip, "port": (cur or {}).get("port", 80)} if cur else {"ip": ip, "port": 80},
                           {"$set": fields, "$unset": {"password": "", "user": "", "channel": ""}}, upsert=True)
    absent = col.count_documents({"ip": {"$nin": list(devices)}})
    print("Appareils :", dict(stats), "| deja en base mais absents de l'export (conserves) :", absent)
    if apply:
        col.update_many({"ip": {"$nin": list(devices)}},
                        {"$set": {"source.seetec": False}, "$unset": {"password": ""}})
        res = col.update_many({"password": {"$exists": True}}, {"$unset": {"password": ""}})
        print("Mots de passe en clair supprimes de cockpit_cameras :", res.modified_count)
        print("Import termine.")
    else:
        print("A blanc : rien n'a ete ecrit (ajouter --apply).")


if __name__ == "__main__":
    main()
