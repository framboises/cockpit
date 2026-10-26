# balises_mur.py - Mur TV de suivi GPS (/balises-mur?groupes=a,b,c)
#
# Un a trois groupes de balises (anoloc_config.global.beacon_groups) sur une
# carte : position, cap, vitesse, en ligne / en mouvement, trainee des
# dernieres minutes. Lecture seule de anoloc_latest et anoloc_positions ;
# l'etat en ligne / GPS vient de balises_core.device_state (une regle par
# source, jamais recodee ici).
#
# Choix publies pour le portail TITAN (afficheurs) : collection partagee
# `tv_choix`, document "cockpit:groupes_balises" = liste des groupes actifs
# que le portail propose a l'affectation d'un ecran pour remplir la variable
# {groupes} de l'adresse du catalogue TV. Republie a l'enregistrement de la
# configuration des balises, au demarrage et au plus toutes les 60 s par le
# mur lui-meme.

from datetime import datetime, timedelta, timezone
import hashlib
import json
import time

import balises_core as B

MAX_GROUPES = 3
TRAINEE_MIN = 10          # trainee affichee derriere chaque balise
TRAINEE_MAX_POINTS = 60   # par balise
EN_MOUVEMENT_KMH = B.MOVING_SPEED_KMH

TV_CHOIX_COL = "tv_choix"
TV_CHOIX_ID = "cockpit:groupes_balises"
_PUBLIE = {"empreinte": None, "a": 0.0}


def _iso(dt):
    dt = B._utc(dt)
    return dt.isoformat() if dt else None


def groupes_actifs(db):
    """Groupes actifs de la configuration, dans l'ordre de l'ecran de
    configuration, premier de chaque id (les doublons sont corriges a
    l'enregistrement)."""
    cfg = db["anoloc_config"].find_one({"_id": "global"}) or {}
    vus, out = set(), []
    for g in cfg.get("beacon_groups") or []:
        gid = g.get("id")
        if not gid or gid in vus or g.get("enabled") is False:
            continue
        vus.add(gid)
        out.append(g)
    return cfg, out


def lire_groupes(brut):
    """'a,b,c' -> ['a', 'b', 'c'] sans doublon, MAX_GROUPES au plus."""
    out = []
    for x in (brut or "").split(","):
        x = x.strip()
        if x and x not in out:
            out.append(x)
    return out[:MAX_GROUPES]


def _option(g):
    return {"valeur": g["id"], "libelle": g.get("label") or g["id"],
            "couleur": g.get("color") or "#6366f1", "icone": g.get("icon") or "location_on",
            "nb": len(g.get("anoloc_device_ids") or [])}


def publier_choix_tv(db, force=False):
    """Ecrit tv_choix/cockpit:groupes_balises si la liste a change (ou
    force). Retourne True si le document a ete ecrit."""
    if not force and time.time() - _PUBLIE["a"] < 60:
        return False
    _PUBLIE["a"] = time.time()
    _, groupes = groupes_actifs(db)
    options = [_option(g) for g in groupes]
    empreinte = hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()
    if not force and empreinte == _PUBLIE["empreinte"]:
        return False
    db[TV_CHOIX_COL].update_one({"_id": TV_CHOIX_ID}, {"$set": {
        "app_key": "cockpit",
        "variable": "groupes",
        "libelle": "Groupes de balises GPS",
        "aide": "1 a 3 groupes affiches sur le mur de suivi GPS",
        "min": 1,
        "max": MAX_GROUPES,
        "separateur": ",",
        "options": options,
        "maj_le": datetime.now(timezone.utc),
    }}, upsert=True)
    _PUBLIE["empreinte"] = empreinte
    return True


def _balise(dev_id, doc, grp, tlt_devices, tlt_settings, now):
    if not doc:
        return {"id": dev_id, "label": (grp.get("device_labels") or {}).get(dev_id, dev_id),
                "lat": None, "lng": None, "en_ligne": False, "mouvement": False, "gps": False,
                "vitesse": 0, "cap": 0, "batterie": None, "charge": None, "vu_a": None,
                "age_s": None, "source": B.source_of(dev_id), "trace": []}
    st = B.device_state(doc, now=now, device=tlt_devices.get(dev_id), settings=tlt_settings)
    vitesse = doc.get("speed") or 0
    try:
        vitesse = float(vitesse)
    except (TypeError, ValueError):
        vitesse = 0
    en_ligne = bool(st["online"])
    mouvement = en_ligne and (vitesse >= EN_MOUVEMENT_KMH or
                              (B.is_teltonika_id(dev_id) and bool(doc.get("movement"))))
    return {
        "id": dev_id,
        "label": doc.get("label") or dev_id,
        "lat": doc.get("lat"), "lng": doc.get("lng"),
        "en_ligne": en_ligne,
        "mouvement": mouvement,
        "gps": bool(st["gps_fix"]),
        "vitesse": round(vitesse) if en_ligne else 0,
        "cap": doc.get("heading") or 0,
        "batterie": doc.get("battery_pct"),
        "charge": doc.get("charging"),
        "vu_a": _iso(st["last_at"]),
        "age_s": round(st["age_s"]) if st["age_s"] is not None else None,
        "source": B.source_of(dev_id),
        "trace": [],
    }


def _traces(db, ids, now):
    """{device_id: [[lat, lng], ...]} des TRAINEE_MIN dernieres minutes."""
    if not ids:
        return {}
    depuis = now - timedelta(minutes=TRAINEE_MIN)
    out = {}
    cur = db[B.COL_POSITIONS].find(
        {"device_id": {"$in": list(ids)}, "collected_at": {"$gte": depuis}},
        {"device_id": 1, "lat": 1, "lng": 1, "_id": 0},
    ).sort("collected_at", 1)
    for p in cur:
        if p.get("lat") is None or p.get("lng") is None:
            continue
        out.setdefault(p["device_id"], []).append([p["lat"], p["lng"]])
    return {k: v[-TRAINEE_MAX_POINTS:] for k, v in out.items()}


def etat_mur(db, demandes, visibles=None, tablettes=None, now=None):
    """Etat du mur pour les groupes `demandes` (ids, ordre conserve).

    visibles : ensemble des groupes visibles de l'utilisateur (None = tous,
    anoloc._get_user_visible_groups). tablettes : {group_id: [devices au
    format _balise]} des tablettes Field (facultatif, event/year).
    Retour : {maintenant, actif, dispo, groupes, ignores}."""
    now = now or datetime.now(timezone.utc)
    try:
        publier_choix_tv(db)
    except Exception:
        pass
    cfg, groupes = groupes_actifs(db)
    if visibles is not None:
        groupes = [g for g in groupes if g["id"] in visibles]
    par_id = {g["id"]: g for g in groupes}
    dispo = [_option(g) for g in groupes]
    retenus = [par_id[g] for g in demandes if g in par_id][:MAX_GROUPES]
    ignores = [g for g in demandes if g not in par_id]
    if not cfg.get("enabled"):
        return {"maintenant": now.isoformat(), "actif": False, "dispo": dispo,
                "groupes": [], "ignores": ignores}

    ids = [d for g in retenus for d in (g.get("anoloc_device_ids") or [])]
    latest = {d["_id"]: d for d in db[B.COL_LATEST].find({"_id": {"$in": ids}})} if ids else {}
    tlt_devices = B.devices_by_id(db)
    tlt_settings = B.get_settings(db)
    en_ligne_ids = set()
    sortie = []
    for g in retenus:
        balises = [_balise(d, latest.get(d), g, tlt_devices, tlt_settings, now)
                   for d in (g.get("anoloc_device_ids") or [])]
        balises.extend((tablettes or {}).get(g["id"]) or [])
        en_ligne_ids.update(b["id"] for b in balises if b["en_ligne"])
        sortie.append({**_option(g), "id": g["id"], "balises": balises})

    traces = _traces(db, [i for i in en_ligne_ids if not i.startswith("field:")], now)
    for g in sortie:
        for b in g["balises"]:
            b["trace"] = traces.get(b["id"], [])
        g["total"] = len(g["balises"])
        g["en_ligne"] = sum(1 for b in g["balises"] if b["en_ligne"])
        g["en_mouvement"] = sum(1 for b in g["balises"] if b["mouvement"])
    return {"maintenant": now.isoformat(), "actif": True, "dispo": dispo,
            "groupes": sortie, "ignores": ignores}


def tablette(t, now=None):
    """Tablette Field (field_devices) au format d'une balise du mur."""
    now = now or datetime.now(timezone.utc)
    pos = t.get("last_position") or {}
    vu = B._utc(t.get("last_seen"))
    age = (now - vu).total_seconds() if vu else None
    en_ligne = age is not None and age < 120
    try:
        vitesse = float(pos.get("speed") or 0)
    except (TypeError, ValueError):
        vitesse = 0
    return {"id": "field:" + str(t.get("_id")), "label": t.get("name") or "?",
            "lat": pos.get("lat"), "lng": pos.get("lng"), "en_ligne": en_ligne,
            "mouvement": en_ligne and vitesse >= EN_MOUVEMENT_KMH, "gps": en_ligne and pos.get("lat") is not None,
            "vitesse": round(vitesse) if en_ligne else 0, "cap": pos.get("heading") or 0,
            "batterie": pos.get("battery"), "charge": None, "vu_a": _iso(vu),
            "age_s": round(age) if age is not None else None, "source": "field",
            "tablette": True, "trace": []}
