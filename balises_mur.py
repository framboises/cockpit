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
# mur lui-meme. Second document "cockpit:fond_balises" : variable {fond}
# (carte OSM sombre ou vue satellite IGN), un seul choix.

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time

import balises_core as B

MAX_GROUPES = 3
SUIVI_MAX_GROUPES = 30    # page operateur /suivi-gps
TRAINEE_MIN = 10          # trainee affichee derriere chaque balise
TRAINEE_MAX_POINTS = 60   # par balise
EN_MOUVEMENT_KMH = B.MOVING_SPEED_KMH

# Activite d'une unite (pastille du mur) :
#  - tablette Field : son statut (field_devices.status) ;
#  - balise sans tablette : engagee si une fiche OUVERTE d'un evenement actif
#    la nomme (content_category.patrouille, comme le PC Org l'engage), sur
#    place si l'arrivee est horodatee (intervention.arrived_at), sinon libre.
ACTIVITE_TABLETTE = {"patrouille": "libre", "intervention": "engagee", "sur_place": "sur_place",
                     "pause": "pause", "fin_intervention": "fin"}
ENGAGEES = ("engagee", "sur_place", "fin")
FICHE_MAX_JOURS = 7   # fiche jamais close au-dela : ignoree (oubli de cloture)

TV_CHOIX_COL = "tv_choix"
TV_CHOIX_ID = "cockpit:groupes_balises"
TV_CHOIX_FOND_ID = "cockpit:fond_balises"
FONDS = [
    {"valeur": "plan", "libelle": "Carte", "couleur": "#334155", "icone": "map"},
    {"valeur": "ortho", "libelle": "Satellite IGN", "couleur": "#15803d", "icone": "satellite_alt"},
]
FOND_DEFAUT = "plan"
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


def lire_groupes(brut, maxi=MAX_GROUPES):
    """'a,b,c' -> ['a', 'b', 'c'] sans doublon, maxi au plus."""
    out = []
    for x in (brut or "").split(","):
        x = x.strip()
        if x and x not in out:
            out.append(x)
    return out[:maxi]


_COULEUR = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_ICONE = re.compile(r"^[a-z0-9_]{1,40}$")


def _option(g):
    # Couleur et icone inserees dans du HTML / CSS (mur, portail) : format strict
    couleur, icone = str(g.get("color") or ""), str(g.get("icon") or "")
    return {"valeur": g["id"], "libelle": g.get("label") or g["id"],
            "couleur": couleur if _COULEUR.match(couleur) else "#6366f1",
            "icone": icone if _ICONE.match(icone) else "location_on",
            "nb": len(g.get("anoloc_device_ids") or [])}


def publier_choix_tv(db, force=False):
    """Ecrit tv_choix/cockpit:groupes_balises (et cockpit:fond_balises) si
    la liste a change (ou force). Retourne True si ecrit."""
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
    db[TV_CHOIX_COL].update_one({"_id": TV_CHOIX_FOND_ID}, {"$set": {
        "app_key": "cockpit",
        "variable": "fond",
        "libelle": "Fond de carte",
        "aide": "Carte OSM sombre ou vue satellite IGN",
        "min": 1,
        "max": 1,
        "separateur": ",",
        "defaut": [FOND_DEFAUT],
        "options": FONDS,
        "maj_le": datetime.now(timezone.utc),
    }}, upsert=True)
    _PUBLIE["empreinte"] = empreinte
    return True


def _nom(x):
    return " ".join(str(x or "").lower().split())


def engagements(db, paires, now=None):
    """{nom normalise: {fiche, texte, sur_place, depuis}} des fiches ouvertes
    des paires (event, year) actives qui nomment une unite engagee."""
    import event_courant as EC
    now = now or datetime.now(timezone.utc)
    if not paires:
        return {}
    q = {"close_ts": None, "content_category.patrouille": {"$nin": [None, ""]},
         "ts": {"$gte": now - timedelta(days=FICHE_MAX_JOURS)}}
    q.update(EC.pairs_filter(paires))
    out = {}
    for f in db["pcorg"].find(q, {"ts": 1, "text": 1, "content_category.patrouille": 1,
                                  "content_category.sous_classification": 1, "intervention": 1}).sort("ts", 1):
        cc = f.get("content_category") or {}
        it = f.get("intervention") or {}
        out[_nom(cc.get("patrouille"))] = {
            "fiche": str(f["_id"]),
            "texte": (f.get("text") or cc.get("sous_classification") or "")[:80],
            "sur_place": bool(it.get("arrived_at")),
            "depuis": _iso(it.get("arrived_at") or it.get("engaged_at") or f.get("ts")),
        }
    return out


def _textes_fiches(db, ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {str(f["_id"]): (f.get("text") or (f.get("content_category") or {}).get("sous_classification") or "")[:80]
            for f in db["pcorg"].find({"_id": {"$in": ids}}, {"text": 1, "content_category.sous_classification": 1})}


def _activite(b, engs, textes):
    """Pose b["activite"], b["activite_depuis"], b["fiche_texte"]."""
    if b.get("tablette"):
        act = ACTIVITE_TABLETTE.get(b.pop("statut_field", None) or "patrouille", "libre")
        b["activite_depuis"] = b.pop("statut_depuis", None)
        b["fiche_texte"] = textes.get(b.pop("fiche_id", None) or "") if act in ENGAGEES else None
    else:
        e = engs.get(_nom(b["label"]))
        if e:
            act = "sur_place" if e["sur_place"] else "engagee"
            b["activite_depuis"], b["fiche_texte"] = e["depuis"], e["texte"]
        else:
            act = "libre" if b["en_ligne"] else None
            b["activite_depuis"], b["fiche_texte"] = None, None
    # Hors ligne et libre : rien a dire (la balise est juste muette)
    if act == "libre" and not b["en_ligne"]:
        act = None
    b["activite"] = act


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
        "gsm": doc.get("gsm_level"),
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


def etat_mur(db, demandes, visibles=None, tablettes=None, now=None, paires=None, maxi=MAX_GROUPES):
    """Etat du mur pour les groupes `demandes` (ids, ordre conserve).

    visibles : ensemble des groupes visibles de l'utilisateur (None = tous,
    anoloc._get_user_visible_groups). tablettes : {group_id: [devices au
    format _balise]} des tablettes Field (facultatif, event/year). paires :
    [(event, year)] actives, pour les fiches qui engagent une balise. maxi :
    nombre de groupes servis (3 pour le mur, SUIVI_MAX_GROUPES pour la page
    operateur ; demandes=None = tous les groupes visibles).
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
    if demandes is None:
        demandes = [g["id"] for g in groupes]
    retenus = [par_id[g] for g in demandes if g in par_id][:maxi]
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
    engs = engagements(db, paires, now) if paires else {}
    textes = _textes_fiches(db, [b.get("fiche_id") for g in sortie for b in g["balises"] if b.get("tablette")])
    for g in sortie:
        for b in g["balises"]:
            b["trace"] = traces.get(b["id"], [])
            _activite(b, engs, textes)
        g["total"] = len(g["balises"])
        g["en_ligne"] = sum(1 for b in g["balises"] if b["en_ligne"])
        g["en_mouvement"] = sum(1 for b in g["balises"] if b["mouvement"])
        g["engagees"] = sum(1 for b in g["balises"] if b["activite"] in ENGAGEES)
        g["libres"] = sum(1 for b in g["balises"] if b["activite"] == "libre")
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
            "tablette": True, "trace": [], "statut_field": t.get("status") or "patrouille",
            "statut_depuis": _iso(t.get("status_since")), "fiche_id": t.get("active_fiche_id")}
