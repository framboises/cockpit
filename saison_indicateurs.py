"""saison_indicateurs.py - Indicateurs "d'un coup d'oeil" de la timeline SAISON.

Module pur (db en argument, ni Flask ni connexion propre). Sur la timeline de
la pseudo-epreuve SAISON (main courante permanente du site), chaque jour de la
barre de navigation porte :
  - des PASTILLES au-dessus du jour (rank "pill") : site ouvert aux visites
    libres / guidees ;
  - des POINTS sous le jour (rank "dot"), a position FIXE : piste ou circuit
    utilise (Bugatti, Maison Blanche, karting CIK / Alain Prost / Indy...).

Configuration GLOBALE (admin) dans cockpit_settings {_id: "saison_indicateurs"},
liste ordonnee d'indicateurs :
  {id, label, short (<= 4 car.), color "#RRGGBB", icon (material symbol),
   rank "pill"|"dot", enabled,
   source: {type: "momentus_rooms", room_ids: [...]}
         | {type: "visites", kind: "libre"|"guidee"}}

Regles Momentus (comme momentus_timeline) : objets supprimes, evenements
annules, perdus et prospects ecartes. Les BLACKOUTS COMPTENT (decision du
02/10/2026) : Momentus les pose sur les dates bloquees a la vente (epreuves
sportives, roulages), pas sur une fermeture ; ils sont signales "bloque".
Jamais de contactRoles ni de montant dans les reponses.

Seminaires (bloc a part, cle `seminaires` de la reponse) : nombre
d'evenements Momentus de types configures et personnes attendues par jour,
plus les plus gros en detail (compute_seminaires).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

SETTINGS_ID = "saison_indicateurs"
MAX_WINDOW_DAYS = 62
MAX_INDICATORS = 12
MAX_ROOMS = 40
MAX_DETAILS = 12
CACHE_TTL_S = 90

PHASES = {"moveIn": "reserve", "event": "exploitation", "moveOut": "demontage", "dark": "bloque"}

# Palette Okabe-Ito (sure pour les daltoniens) ajustee pour rester lisible sur
# le fond clair de la timeline ; la couleur n'est jamais seule : libelle court,
# position fixe des points, legende et infobulle.
DEFAULT_INDICATORS = [
    {"id": "visites_libres", "label": "Visites libres", "short": "VL", "color": "#047857",
     "icon": "directions_walk", "rank": "pill", "enabled": True,
     "source": {"type": "visites", "kind": "libre"}},
    {"id": "visites_guidees", "label": "Visites guidees", "short": "VG", "color": "#E69F00",
     "icon": "tour", "rank": "pill", "enabled": True,
     "source": {"type": "visites", "kind": "guidee"}},
    {"id": "bugatti", "label": "Piste Bugatti", "short": "BUG", "color": "#D55E00",
     "icon": "sports_score", "rank": "dot", "enabled": True,
     "source": {"type": "momentus_rooms", "room_ids": ["room-833-A"]}},
    {"id": "maison_blanche", "label": "Maison Blanche", "short": "MB", "color": "#0072B2",
     "icon": "route", "rank": "dot", "enabled": True,
     "source": {"type": "momentus_rooms",
                "room_ids": ["room-834-A", "room-415-A", "room-416-A", "room-417-A"]}},
    {"id": "karting_cik", "label": "Karting CIK", "short": "CIK", "color": "#7E3FBF",
     "icon": "toys", "rank": "dot", "enabled": True,
     "source": {"type": "momentus_rooms", "room_ids": ["room-107-A"]}},
    {"id": "karting_prost", "label": "Karting Alain Prost", "short": "AP", "color": "#CC79A7",
     "icon": "toys", "rank": "dot", "enabled": True,
     "source": {"type": "momentus_rooms", "room_ids": ["room-108-A"]}},
    {"id": "karting_indy", "label": "Karting Indy", "short": "IND", "color": "#E6C200",
     "icon": "toys", "rank": "dot", "enabled": True,
     "source": {"type": "momentus_rooms", "room_ids": ["room-162-A"]}},
]

# Seminaires du site (bloc a part, pas un indicateur) : evenements Momentus
# dont le type (normalise : casse, accents, espaces) est dans la liste.
# Metriques par jour (nombre, personnes) + les plus gros en detail.
DEFAULT_SEMINAIRES = {"enabled": True,
                      "types": ["Seminaire", "Activites seminaires", "Reception"],
                      "max_list": 5}
MAX_SEM_TYPES = 30
MAX_SEM_LIST = 20

_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
_ROOM_RE = re.compile(r"^room-\d{1,6}-[A-Za-z]{1,3}$")
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_ICON_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_UNSAFE = re.compile(r"[<>\"`\x00-\x1f]")
_MAIL = re.compile(r"\S+@\S+")
_PHONE = re.compile(r"(?:\+|\b0)\d(?:[\s.\-]?\d{2}){4}\b")

_cache = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def _clean(text, limit=120):
    s = _UNSAFE.sub("", str(text or ""))
    s = _PHONE.sub("[tel]", _MAIL.sub("[mail]", s))
    return " ".join(s.split())[:limit]


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _hhmm(value):
    m = re.match(r"^(\d{1,2}):(\d{2})", str(value or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def _status(ev):
    if ev.get("isDefinite"):
        return "confirme"
    if ev.get("isTentative"):
        return "option"
    return ""


def _excluded(ev):
    sync = ev.get("_sync") or {}
    return bool(sync.get("deleted_at") or ev.get("isCanceled") or ev.get("isLost")
                or ev.get("isProspect"))


def norm_type(value):
    """Type d'evenement Momentus normalise : 'Séminaire ' == 'seminaire'."""
    s = unicodedata.normalize("NFD", str(value or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.lower().split())


def _att(value):
    """Effectif Momentus exploitable (entier > 0), sinon None. Jamais invente."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return None


def parse_day(value):
    s = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    return _day(s)


def clear_cache():
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def validate_config(raw):
    """(indicateurs nettoyes, erreurs). Validation stricte : tout champ
    inconnu est ignore, tout champ invalide est une erreur."""
    errors = []
    if not isinstance(raw, list):
        return [], ["la configuration doit etre une liste"]
    if len(raw) > MAX_INDICATORS:
        return [], [f"{MAX_INDICATORS} indicateurs au plus"]
    out, seen = [], set()
    for i, it in enumerate(raw):
        where = f"indicateur {i + 1}"
        if not isinstance(it, dict):
            errors.append(f"{where} : objet attendu")
            continue
        iid = str(it.get("id") or "").strip()
        if not _ID_RE.match(iid):
            errors.append(f"{where} : identifiant invalide (a-z, 0-9, _ et -, 32 car.)")
        elif iid in seen:
            errors.append(f"{where} : identifiant en double ({iid})")
        seen.add(iid)
        label = _clean(it.get("label"), 200)
        if not label or len(label) > 40:
            errors.append(f"{where} : libelle requis (40 car. au plus)")
        short = _clean(it.get("short"), 20)
        if not short or len(short) > 4:
            errors.append(f"{where} : libelle court requis (4 car. au plus)")
        color = str(it.get("color") or "").strip()
        if not _COLOR_RE.match(color):
            errors.append(f"{where} : couleur #RRGGBB attendue")
        icon = str(it.get("icon") or "").strip() or "circle"
        if not _ICON_RE.match(icon):
            errors.append(f"{where} : icone invalide")
        rank = it.get("rank")
        if rank not in ("pill", "dot"):
            errors.append(f"{where} : rang 'pill' ou 'dot' attendu")
        enabled = it.get("enabled", True)
        if not isinstance(enabled, bool):
            errors.append(f"{where} : 'enabled' booleen attendu")
        src = it.get("source")
        clean_src = None
        if not isinstance(src, dict):
            errors.append(f"{where} : source requise")
        elif src.get("type") == "momentus_rooms":
            rooms = src.get("room_ids")
            if not isinstance(rooms, list) or not rooms:
                errors.append(f"{where} : au moins un espace Momentus")
            elif len(rooms) > MAX_ROOMS:
                errors.append(f"{where} : {MAX_ROOMS} espaces au plus")
            elif not all(isinstance(r, str) and _ROOM_RE.match(r) for r in rooms):
                errors.append(f"{where} : identifiant d'espace Momentus invalide")
            else:
                clean_src = {"type": "momentus_rooms", "room_ids": list(dict.fromkeys(rooms))}
        elif src.get("type") == "visites":
            if src.get("kind") not in ("libre", "guidee"):
                errors.append(f"{where} : type de visite 'libre' ou 'guidee' attendu")
            else:
                clean_src = {"type": "visites", "kind": src["kind"]}
        else:
            errors.append(f"{where} : type de source inconnu")
        if clean_src is not None:
            out.append({"id": iid, "label": label, "short": short, "color": color.upper(),
                        "icon": icon, "rank": rank, "enabled": bool(enabled), "source": clean_src})
    return (out if not errors else []), errors


def validate_seminaires(raw):
    """(bloc seminaires nettoye, erreurs). {enabled, types, max_list}."""
    if not isinstance(raw, dict):
        return None, ["seminaires : objet attendu"]
    errors = []
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append("seminaires : 'enabled' booleen attendu")
    types = raw.get("types")
    clean_types = []
    if not isinstance(types, list):
        errors.append("seminaires : liste de types attendue")
    elif len(types) > MAX_SEM_TYPES:
        errors.append(f"seminaires : {MAX_SEM_TYPES} types au plus")
    else:
        seen = set()
        for t in types:
            c = _clean(t, 200) if isinstance(t, str) else ""
            if not c or len(c) > 60:
                errors.append("seminaires : type invalide (texte de 1 a 60 car.)")
                continue
            n = norm_type(c)
            if n not in seen:
                seen.add(n)
                clean_types.append(c)
        if enabled is True and not clean_types and not errors:
            errors.append("seminaires : au moins un type d'evenement quand le bloc est actif")
    ml = raw.get("max_list", DEFAULT_SEMINAIRES["max_list"])
    if isinstance(ml, bool) or not isinstance(ml, int) or not 1 <= ml <= MAX_SEM_LIST:
        errors.append(f"seminaires : nombre d'evenements affiches entre 1 et {MAX_SEM_LIST}")
    if errors:
        return None, errors
    return {"enabled": enabled, "types": clean_types, "max_list": ml}, []


def ensure_seed(db):
    """Cree la configuration par defaut si elle n'existe pas (idempotent,
    jamais d'ecrasement d'une configuration existante). Le bloc seminaires
    est ajoute a une configuration existante qui ne l'a pas encore, sans
    toucher aux indicateurs."""
    db["cockpit_settings"].update_one(
        {"_id": SETTINGS_ID},
        {"$setOnInsert": {"indicators": copy.deepcopy(DEFAULT_INDICATORS),
                          "seminaires": copy.deepcopy(DEFAULT_SEMINAIRES),
                          "updated_at": datetime.now(timezone.utc), "updated_by": "seed"}},
        upsert=True)
    db["cockpit_settings"].update_one(
        {"_id": SETTINGS_ID, "seminaires": {"$exists": False}},
        {"$set": {"seminaires": copy.deepcopy(DEFAULT_SEMINAIRES)}})


def get_config(db, seed=True):
    doc = db["cockpit_settings"].find_one({"_id": SETTINGS_ID})
    if doc is None and seed:
        ensure_seed(db)
        doc = db["cockpit_settings"].find_one({"_id": SETTINGS_ID})
    inds = (doc or {}).get("indicators")
    if not isinstance(inds, list):
        inds = copy.deepcopy(DEFAULT_INDICATORS)
    clean, errors = validate_config(inds)
    return clean if not errors else copy.deepcopy(DEFAULT_INDICATORS)


def get_seminaires(db, seed=True):
    doc = db["cockpit_settings"].find_one({"_id": SETTINGS_ID})
    if (doc is None or "seminaires" not in doc) and seed:
        ensure_seed(db)
        doc = db["cockpit_settings"].find_one({"_id": SETTINGS_ID})
    raw = (doc or {}).get("seminaires")
    clean, errors = validate_seminaires(raw) if raw is not None else (None, ["absent"])
    return clean if not errors else copy.deepcopy(DEFAULT_SEMINAIRES)


def save_config(db, raw, user=None, seminaires=None):
    """Enregistre les indicateurs (raw, liste) et/ou le bloc seminaires.
    raw=None : indicateurs inchanges (seminaires seul)."""
    fields, errors = {}, []
    if raw is not None or seminaires is None:
        clean, errs = validate_config(raw)
        errors += errs
        fields["indicators"] = clean
    if seminaires is not None:
        sem, errs = validate_seminaires(seminaires)
        errors += errs
        fields["seminaires"] = sem
    if errors:
        return None, errors
    fields.update(updated_at=datetime.now(timezone.utc), updated_by=str(user or "")[:120])
    db["cockpit_settings"].update_one({"_id": SETTINGS_ID}, {"$set": fields}, upsert=True)
    clear_cache()
    return fields.get("indicators"), []


def event_types(db):
    """Types d'evenement Momentus distincts (non supprimes), avec leur nombre,
    regroupes par forme normalisee, pour le selecteur admin."""
    groups = {}
    for ev in db["momentus_events"].find({"_sync.deleted_at": None}, {"eventTypeName": 1}):
        name = _clean(ev.get("eventTypeName"), 60)
        if not name:
            continue
        g = groups.setdefault(norm_type(name), {"name": name, "count": 0, "_v": defaultdict(int)})
        g["count"] += 1
        g["_v"][name] += 1
    out = []
    for g in groups.values():
        # Libelle le plus frequent ('Séminaire' plutot que 'séminaire ')
        g["name"] = max(g.pop("_v").items(), key=lambda kv: (kv[1], kv[0]))[0]
        out.append(g)
    return sorted(out, key=lambda g: (-g["count"], g["name"].lower()))


def public_indicators(db, indicators):
    """Indicateurs actifs pour l'affichage (noms d'espaces resolus)."""
    room_ids = {r for it in indicators if it["source"]["type"] == "momentus_rooms"
                for r in it["source"]["room_ids"]}
    names = {}
    if room_ids:
        for r in db["momentus_rooms"].find({"id": {"$in": sorted(room_ids)}}, {"id": 1, "name": 1}):
            names[r.get("id")] = _clean(r.get("name"), 80)
    out = []
    for it in indicators:
        if not it.get("enabled"):
            continue
        src = it["source"]
        p = {k: it[k] for k in ("id", "label", "short", "color", "icon", "rank")}
        p["source_type"] = src["type"]
        if src["type"] == "visites":
            p["kind"] = src["kind"]
        else:
            p["rooms"] = [names.get(r, r) for r in src["room_ids"]]
        out.append(p)
    return out


def rooms_by_venue(db):
    """Espaces Momentus groupes par site, pour le selecteur admin."""
    groups = defaultdict(list)
    for r in db["momentus_rooms"].find({}, {"id": 1, "name": 1, "venueName": 1, "isActive": 1, "_sync": 1}):
        if (r.get("_sync") or {}).get("deleted_at"):
            continue
        rid = r.get("id") or r.get("_id")
        if not isinstance(rid, str) or not _ROOM_RE.match(rid):
            continue
        groups[_clean(r.get("venueName"), 80) or "(sans site)"].append(
            {"id": rid, "name": _clean(r.get("name"), 80) or rid, "active": r.get("isActive") is not False})
    return [{"venue": v, "rooms": sorted(rs, key=lambda x: x["name"].lower())}
            for v, rs in sorted(groups.items(), key=lambda kv: kv[0].lower())]


# ---------------------------------------------------------------------------
# Calcul
# ---------------------------------------------------------------------------
def _days(d_from, d_to):
    x = d_from
    while x <= d_to:
        yield x
        x += timedelta(days=1)


def _visit_days(db, d_from, d_to):
    import event_courant as EC
    out = {}
    for y in range(d_from.year, d_to.year + 1):
        try:
            out.update(EC.saison_visit_days(db, y) or {})
        except Exception:
            continue
    return out


def _momentus_details(db, room_to_inds, d_from, d_to):
    """{(indicator_id, 'YYYY-MM-DD'): {(event_id, room_id): detail}}.
    Une requete sur momentus_functions, une sur momentus_events."""
    lo, hi = d_from.isoformat(), d_to.isoformat()
    rooms = sorted(room_to_inds)
    out = defaultdict(dict)
    if not rooms:
        return out
    fns = list(db["momentus_functions"].find(
        {"_sync.deleted_at": None, "roomId": {"$in": rooms},
         "startDate": {"$lte": hi}, "endDate": {"$gte": lo}},
        {"eventId": 1, "roomId": 1, "roomName": 1, "startDate": 1, "endDate": 1,
         "startTime": 1, "endTime": 1, "isAllDay": 1, "_sync": 1}))
    fn_eids = sorted({f.get("eventId") for f in fns if f.get("eventId")})
    q = {"_sync.deleted_at": None, "isCanceled": {"$ne": True}, "isLost": {"$ne": True},
         "isProspect": {"$ne": True},
         "$or": [{"bookedSpaces": {"$elemMatch": {"roomId": {"$in": rooms},
                                                  "startDate": {"$lte": hi}, "endDate": {"$gte": lo}}}},
                 {"_id": {"$in": fn_eids}}]}
    proj = {"name": 1, "eventTypeName": 1, "bookedSpaces": 1, "isDefinite": 1, "isTentative": 1,
            "isProspect": 1, "isBlackout": 1, "isCanceled": 1, "isLost": 1, "_sync": 1,
            "accountName": 1}
    events = {}
    for ev in db["momentus_events"].find(q, proj):
        if not _excluded(ev):
            events[ev["_id"]] = ev

    def _put(ev, room_id, room_name, ds, start, end, phase):
        for iid in room_to_inds.get(room_id, ()):
            key = (ev["_id"], room_id)
            cur = out[(iid, ds)].get(key)
            if cur is None:
                out[(iid, ds)][key] = {
                    "event_id": ev["_id"], "event": _clean(ev.get("name")) or "Reservation Momentus",
                    "type": _clean(ev.get("eventTypeName"), 60), "room": _clean(room_name, 80),
                    "start": start, "end": end, "blackout": bool(ev.get("isBlackout")),
                    "status": _status(ev), "phase": phase, "_fn": phase == "",
                    "interne_aco": bool(compte_aco(ev)), "compte_aco": compte_aco(ev),
                }
            elif phase == "" or cur["_fn"] is False:
                # Fonctions : plus tot debut / plus tard fin du jour
                if phase == "" and cur["_fn"] is False:
                    cur.update(start=start, end=end, _fn=True)
                else:
                    if start and (not cur["start"] or start < cur["start"]):
                        cur["start"] = start
                    if end and (not cur["end"] or end > cur["end"]):
                        cur["end"] = end

    covered = set()
    for fn in fns:
        if (fn.get("_sync") or {}).get("deleted_at"):
            continue
        ev = events.get(fn.get("eventId"))
        a = _day(fn.get("startDate"))
        b = _day(fn.get("endDate")) or a
        if not ev or not a:
            continue
        b = max(a, b)
        all_day = bool(fn.get("isAllDay"))
        fs = "" if all_day else _hhmm(fn.get("startTime"))
        fe = "" if all_day else _hhmm(fn.get("endTime"))
        for x in _days(max(a, d_from), min(b, d_to)):
            ds = x.isoformat()
            covered.add((ev["_id"], fn.get("roomId"), ds))
            _put(ev, fn.get("roomId"), fn.get("roomName"), ds,
                 fs if x == a else "", fe if x == b else "", "")
    for ev in events.values():
        for bs in ev.get("bookedSpaces") or []:
            rid = bs.get("roomId")
            if rid not in room_to_inds:
                continue
            a, b = _day(bs.get("startDate")), _day(bs.get("endDate"))
            if not a or not b or b < d_from or a > d_to:
                continue
            all_day = bool(bs.get("isAllDay"))
            s = "" if all_day else _hhmm(bs.get("startTime"))
            e = "" if all_day else _hhmm(bs.get("endTime"))
            phase = PHASES.get(bs.get("usageType"), "reserve")
            for x in _days(max(a, d_from), min(b, d_to)):
                ds = x.isoformat()
                if (ev["_id"], rid, ds) in covered:
                    continue
                _put(ev, rid, bs.get("roomName"), ds, s if x == a else "", e if x == b else "", phase)
    return out


def compute(db, d_from, d_to, indicators=None):
    """{'YYYY-MM-DD': [{id, active, details, more, eids}]} dans l'ordre FIXE de
    la configuration (indicateurs actives seulement)."""
    if indicators is None:
        indicators = get_config(db)
    inds = [it for it in indicators if it.get("enabled")]
    room_to_inds = defaultdict(list)
    for it in inds:
        if it["source"]["type"] == "momentus_rooms":
            for r in it["source"]["room_ids"]:
                room_to_inds[r].append(it["id"])
    mom = _momentus_details(db, room_to_inds, d_from, d_to)
    need_visits = any(it["source"]["type"] == "visites" for it in inds)
    visits = _visit_days(db, d_from, d_to) if need_visits else {}

    out = {}
    for x in _days(d_from, d_to):
        ds = x.isoformat()
        row = []
        for it in inds:
            src = it["source"]
            details = []
            if src["type"] == "visites":
                info = visits.get(ds)
                flag = "visite_libre" if src["kind"] == "libre" else "visite_guidee"
                if info and info.get(flag):
                    details.append({
                        "event": "Visites libres" if src["kind"] == "libre" else "Visites guidees",
                        "room": "", "start": "" if info.get("is24h") else info.get("open") or "",
                        "end": "" if info.get("is24h") else info.get("close") or "",
                        "is24h": bool(info.get("is24h")), "blackout": False, "status": "", "phase": ""})
            else:
                for d in mom.get((it["id"], ds), {}).values():
                    d = dict(d)
                    d.pop("_fn", None)
                    details.append(d)
                details.sort(key=lambda d: (d["start"] or "00:00", d["event"].lower(), d["room"]))
            eids = sorted({d["event_id"] for d in details if d.get("event_id")})
            row.append({"id": it["id"], "active": bool(details),
                        "details": details[:MAX_DETAILS], "more": max(0, len(details) - MAX_DETAILS),
                        "eids": eids})
        out[ds] = row
    return out


def compute_cached(db, d_from, d_to, indicators=None):
    if indicators is None:
        indicators = get_config(db)
    sig = hashlib.sha1(json.dumps(indicators, sort_keys=True).encode()).hexdigest()
    key = (getattr(db, "name", ""), d_from.isoformat(), d_to.isoformat(), sig)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    res = compute(db, d_from, d_to, indicators)
    with _cache_lock:
        if len(_cache) > 64:
            _cache.clear()
        _cache[key] = (now, res)
    return res


def epreuves(db, d_from, d_to):
    """Epreuves Cockpit dont la fenetre (montage -> demontage, cf.
    event_courant.windows) croise [d_from, d_to], avec leurs jours publics et
    la couleur / le code court de la collection `evenement`. Sert au
    calendrier d'occupation de la timeline SAISON."""
    import event_courant as EC
    from zoneinfo import ZoneInfo
    paris = ZoneInfo("Europe/Paris")
    meta = {str(e.get("nom") or ""): e for e in db["evenement"].find({}, {"nom": 1, "couleur": 1, "short": 1})}
    lo, hi = d_from.isoformat(), d_to.isoformat()
    out = []
    try:
        wins = EC.windows(db)
    except Exception:
        return out
    for w in wins:
        a = w["start"].astimezone(paris).date().isoformat()
        b = w["end"].astimezone(paris).date().isoformat()
        if b < lo or a > hi:
            continue
        m = meta.get(w["event"]) or {}
        color = str(m.get("couleur") or "")
        out.append({
            "event": w["event"], "year": w["year"],
            "short": (m.get("short") or w["event"])[:10],
            "color": color if len(color) == 7 and color.startswith("#") else "#475569",
            "start": a, "end": b,
            "public_days": sorted(d for d in (w.get("public_days") or []) if lo <= d <= hi),
        })
    out.sort(key=lambda e: (e["start"], e["event"]))
    return out


def compute_seminaires(db, d_from, d_to, cfg=None):
    """{'YYYY-MM-DD': {count, pers, pers_unknown, items, more}} pour les
    jours qui ont au moins un seminaire (jours vides absents).

    Un evenement compte un jour s'il y a un espace reserve ou une fonction
    (avec espace) ce jour-la : les dates de l'evenement sont une enveloppe.
    Effectif d'un evenement un jour donne : le plus grand effectif des
    fonctions du jour (garanti, sinon attendu, sinon convenu), a defaut
    l'effectif estime de l'evenement ; inconnu = None (jamais invente).
    Ni contactRoles ni montants."""
    cfg = cfg or get_seminaires(db)
    out = {}
    if not cfg.get("enabled") or not cfg.get("types"):
        return out
    wanted = {norm_type(t) for t in cfg["types"]}
    max_list = int(cfg.get("max_list") or DEFAULT_SEMINAIRES["max_list"])
    lo, hi = d_from.isoformat(), d_to.isoformat()
    fns = list(db["momentus_functions"].find(
        {"_sync.deleted_at": None, "startDate": {"$lte": hi}, "endDate": {"$gte": lo}},
        {"eventId": 1, "roomId": 1, "roomName": 1, "startDate": 1, "endDate": 1, "startTime": 1,
         "endTime": 1, "isAllDay": 1, "guaranteedAttendance": 1, "expectedAttendance": 1,
         "agreedAttendance": 1, "_sync": 1}))
    fn_eids = sorted({f.get("eventId") for f in fns if f.get("eventId")})
    q = {"_sync.deleted_at": None, "isCanceled": {"$ne": True}, "isLost": {"$ne": True},
         "isProspect": {"$ne": True},
         "$or": [{"bookedSpaces": {"$elemMatch": {"startDate": {"$lte": hi}, "endDate": {"$gte": lo}}}},
                 {"_id": {"$in": fn_eids}}]}
    proj = {"name": 1, "eventTypeName": 1, "bookedSpaces": 1, "isDefinite": 1, "isTentative": 1,
            "isProspect": 1, "isCanceled": 1, "isLost": 1, "estimatedTotalAttendance": 1,
            "estimatedAttendance": 1, "_sync": 1, "accountId": 1, "accountName": 1}
    events = {}
    for ev in db["momentus_events"].find(q, proj):
        if not _excluded(ev) and norm_type(ev.get("eventTypeName")) in wanted:
            events[ev["_id"]] = ev
    if not events:
        return out

    # (event_id, jour) -> agregat
    agg = {}

    def _slot(eid, ds):
        a = agg.get((eid, ds))
        if a is None:
            a = agg[(eid, ds)] = {"present": False, "rooms": set(), "fn_att": None,
                                  "fn_start": "", "fn_end": "", "bs_start": "", "bs_end": ""}
        return a

    def _widen(a, ks, ke, s, e):
        if s and (not a[ks] or s < a[ks]):
            a[ks] = s
        if e and (not a[ke] or e > a[ke]):
            a[ke] = e

    for fn in fns:
        if (fn.get("_sync") or {}).get("deleted_at"):
            continue
        ev = events.get(fn.get("eventId"))
        a_d = _day(fn.get("startDate"))
        b_d = _day(fn.get("endDate")) or a_d
        if not ev or not a_d:
            continue
        b_d = max(a_d, b_d)
        all_day = bool(fn.get("isAllDay"))
        fs = "" if all_day else _hhmm(fn.get("startTime"))
        fe = "" if all_day else _hhmm(fn.get("endTime"))
        att = (_att(fn.get("guaranteedAttendance")) or _att(fn.get("expectedAttendance"))
               or _att(fn.get("agreedAttendance")))
        room = _clean(fn.get("roomName"), 80) if fn.get("roomId") else ""
        for x in _days(max(a_d, d_from), min(b_d, d_to)):
            a = _slot(ev["_id"], x.isoformat())
            # Notes a la journee sans espace ("Details de l'evenement global") :
            # effectif seulement, elles ne rendent pas l'evenement present.
            if fn.get("roomId"):
                a["present"] = True
                if room:
                    a["rooms"].add(room)
            if att and (a["fn_att"] is None or att > a["fn_att"]):
                a["fn_att"] = att
            _widen(a, "fn_start", "fn_end", fs if x == a_d else "", fe if x == b_d else "")
    for ev in events.values():
        for bs in ev.get("bookedSpaces") or []:
            a_d, b_d = _day(bs.get("startDate")), _day(bs.get("endDate"))
            if not a_d or not b_d or b_d < d_from or a_d > d_to:
                continue
            all_day = bool(bs.get("isAllDay"))
            s = "" if all_day else _hhmm(bs.get("startTime"))
            e = "" if all_day else _hhmm(bs.get("endTime"))
            room = _clean(bs.get("roomName"), 80)
            for x in _days(max(a_d, d_from), min(b_d, d_to)):
                a = _slot(ev["_id"], x.isoformat())
                a["present"] = True
                if room:
                    a["rooms"].add(room)
                _widen(a, "bs_start", "bs_end", s if x == a_d else "", e if x == b_d else "")

    # Fusion par CLIENT et par jour (accountId Momentus, a defaut le nom de
    # l'evenement normalise) : plusieurs reservations d'un meme client le meme
    # jour = une ligne (plage horaire elargie, espaces cumules). Effectif = le
    # plus grand des reservations fusionnees (memes personnes : on n'additionne
    # pas) ; le nombre de reservations est indique.
    merged = {}   # (ds, cle client) -> ligne
    for (eid, ds), a in agg.items():
        if not a["present"]:
            continue
        ev = events[eid]
        pers = a["fn_att"] or _att(ev.get("estimatedTotalAttendance")) or _att(ev.get("estimatedAttendance"))
        if a["fn_start"] or a["fn_end"]:
            start, end = a["fn_start"], a["fn_end"]
        else:
            start, end = a["bs_start"], a["bs_end"]
        ckey = client_key(ev)
        m = merged.get((ds, ckey))
        if m is None:
            m = merged[(ds, ckey)] = {
                "names": [], "account": client_label(ev), "types": [],
                "start": "", "end": "", "rooms": set(), "pers": None, "status": set(), "n": 0,
                "compte_aco": ""}
        m["n"] += 1
        m["compte_aco"] = m["compte_aco"] or compte_aco(ev)
        nm = _clean(ev.get("name")) or "Reservation Momentus"
        if nm not in m["names"]:
            m["names"].append(nm)
        tp = _clean(ev.get("eventTypeName"), 60)
        if tp and tp not in m["types"]:
            m["types"].append(tp)
        if start and (not m["start"] or start < m["start"]):
            m["start"] = start
        if end and (not m["end"] or end > m["end"]):
            m["end"] = end
        m["rooms"] |= a["rooms"]
        if pers and (m["pers"] is None or pers > m["pers"]):
            m["pers"] = pers
        m["status"].add(_status(ev))

    by_day = defaultdict(list)
    for (ds, ck), m in merged.items():
        rooms = sorted(m["rooms"])
        place = rooms[0] if len(rooms) == 1 else (f"{len(rooms)} espaces" if rooms else "")
        # Plusieurs noms d'evenement pour un meme client : le nom du client
        label = m["names"][0] if len(m["names"]) == 1 else (m["account"] or " / ".join(m["names"][:2]))
        if m["n"] > 1:
            label += f" ({m['n']} resa)"
        st = m["status"]
        by_day[ds].append({"event": label,
                           "type": " / ".join(m["types"][:2]),
                           "start": m["start"], "end": m["end"], "place": place, "pers": m["pers"],
                           "status": "confirme" if st == {"confirme"} else ("option" if "option" in st else next(iter(st), "")),
                           "reservations": m["n"],
                           # Cle stable du client (route /api/saison/client) et nom affiche
                           "client": ck, "account": m["account"] or m["names"][0],
                           # Reserve en direct par l'ACO (compte de service) : gestion interne
                           "interne_aco": bool(m["compte_aco"]), "compte_aco": m["compte_aco"]})
    for ds in sorted(by_day):
        items = by_day[ds]
        items.sort(key=lambda it: (-(it["pers"] or 0), it["start"] or "99:99", it["event"].lower()))
        known = [it["pers"] for it in items if it["pers"]]
        # Liste COMPLETE : l'infobulle montre les max_list premiers et deplie
        # le reste sur "+ N autres" ; `more` reste pour compatibilite.
        out[ds] = {"count": len(items), "pers": sum(known) if known else None,
                   "pers_unknown": len(items) - len(known),
                   "items": items, "more": max(0, len(items) - max_list)}
    return out


# Comptes de SERVICE de l'ACO dans Momentus ("ACO Sport", "ACO Karting",
# "ACO Production Evenements"...) : les activites internes y sont saisies sans
# client reel. Regrouper sur ce compte melangerait des activites sans rapport
# (ECOLE MOTO, FERRARI POZZI et tous les roulages sous "ACO Sport") : pour ces
# comptes, le "client" est l'evenement lui-meme (cle 'nom:').
_ACO_ACCOUNT_RE = re.compile(r"^\s*aco\b", re.IGNORECASE)


def is_service_account(account_name):
    return bool(account_name) and bool(_ACO_ACCOUNT_RE.match(str(account_name)))


def compte_aco(ev):
    """Nom du compte de service ACO ('ACO Sport'...) si la reservation a ete
    faite en direct par l'ACO (gestion INTERNE), sinon ''. Nom d'un service
    interne, pas une donnee personnelle."""
    acc = _clean((ev or {}).get("accountName"), 80)
    return acc if is_service_account(acc) else ""


INTERNE_ACO_LABEL = "Reserve en direct par l'ACO (gestion interne)"


def client_key(ev):
    """Cle stable d'un client Momentus : accountId, a defaut (ou pour un
    compte de service ACO) 'nom:' + nom d'evenement normalise (meme regle que
    la fusion des seminaires)."""
    acc = ev.get("accountId")
    if isinstance(acc, str) and acc.strip() and not is_service_account(ev.get("accountName")):
        return acc.strip()
    return "nom:" + norm_type(ev.get("name"))


def client_label(ev):
    """Nom affiche du client : le compte, sauf compte de service ACO (nom de
    l'evenement)."""
    acc = _clean(ev.get("accountName"))
    if acc and not is_service_account(acc):
        return acc
    return _clean(ev.get("name")) or "Reservation Momentus"


# ---------------------------------------------------------------------------
# Programme d'un client (GET /api/saison/client)
# ---------------------------------------------------------------------------
CLIENT_DEFAULT_BEFORE = 7
CLIENT_DEFAULT_AFTER = 90
CLIENT_MAX_DAYS = 180
_CLIENT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def parse_client(value):
    """Cle client valide (accountId ou 'nom:<nom normalise>'), sinon None."""
    s = str(value or "").strip()
    if s.startswith("nom:"):
        n = norm_type(s[4:])
        # Compare en Python seulement (jamais dans une requete Mongo)
        return "nom:" + n if n and len(n) <= 200 else None
    return s if _CLIENT_RE.match(s) else None


_EVENT_RE = re.compile(r"^event-\d{1,10}-[A-Za-z]{1,3}$")


def parse_event_id(value):
    """Identifiant d'evenement Momentus valide ('event-123-A'), sinon None."""
    s = str(value or "").strip()
    return s if _EVENT_RE.match(s) else None


def client_for_event(db, event_id):
    """Cle client (cf. client_key) d'un evenement Momentus, pour le planning
    ouvert depuis une vignette de la timeline (`momentus_event_id`). None si
    l'identifiant est invalide ou l'evenement inconnu."""
    s = parse_event_id(event_id)
    if not s:
        return None
    ev = db["momentus_events"].find_one({"_id": s}, {"name": 1, "accountId": 1, "accountName": 1})
    return client_key(ev) if ev else None


def client_window(raw_from, raw_to, today):
    """(debut, fin, erreur). Defaut : today-7 -> today+90 ; 180 jours au plus."""
    a = parse_day(raw_from) if raw_from else today - timedelta(days=CLIENT_DEFAULT_BEFORE)
    b = parse_day(raw_to) if raw_to else today + timedelta(days=CLIENT_DEFAULT_AFTER)
    if not a or not b:
        return None, None, "periode_invalide"
    if b < a:
        return None, None, "periode_invalide"
    if (b - a).days + 1 > CLIENT_MAX_DAYS:
        return None, None, "periode_trop_longue"
    return a, b, None


def _fn_att(fn):
    return (_att(fn.get("guaranteedAttendance")) or _att(fn.get("expectedAttendance"))
            or _att(fn.get("agreedAttendance")))


def client_programme(db, client, d_from, d_to):
    """Tout le programme d'un client sur [d_from, d_to] : ses evenements
    Momentus (TOUS types, hors annules / perdus / prospects / supprimes) jour
    par jour. Par jour : fonctions (horaires, nom, type, espace, effectif) puis
    espaces reserves sans fonction ce jour-la (phase, horaires). Une fonction
    sans espace (note a la journee) n'est listee que les jours ou l'evenement
    est present (espace reserve ou fonction avec espace).
    Ni contactRoles, ni e-mails, ni telephones, ni montants."""
    lo, hi = d_from.isoformat(), d_to.isoformat()
    proj = {"name": 1, "eventTypeName": 1, "bookedSpaces": 1, "isDefinite": 1, "isTentative": 1,
            "isProspect": 1, "isCanceled": 1, "isLost": 1, "isBlackout": 1, "_sync": 1,
            "accountId": 1, "accountName": 1, "estimatedTotalAttendance": 1, "estimatedAttendance": 1}
    base = {"_sync.deleted_at": None, "isCanceled": {"$ne": True}, "isLost": {"$ne": True},
            "isProspect": {"$ne": True}}
    if client.startswith("nom:"):
        # Sans compte, ou compte de service ACO (cle = nom de l'evenement)
        q = dict(base, **{"$or": [{"accountId": None}, {"accountId": ""},
                                  {"accountName": {"$regex": r"^\s*aco\b", "$options": "i"}}]})
    else:
        q = dict(base, accountId=client)
    events = {}
    for ev in db["momentus_events"].find(q, proj):
        if _excluded(ev) or client_key(ev) != client:
            continue
        events[ev["_id"]] = ev
    out = {"client": client, "account": "", "from": lo, "to": hi, "events": [], "days": [],
           "totals": {"days": 0, "events": 0, "max_pers": None},
           "interne_aco": False, "compte_aco": ""}
    if not events:
        return out
    acos = sorted({compte_aco(ev) for ev in events.values()} - {""})
    out["interne_aco"] = bool(acos)
    out["compte_aco"] = " / ".join(acos[:2])
    accs = defaultdict(int)
    for ev in events.values():
        a = client_label(ev)
        if a:
            accs[a] += 1
    out["account"] = (max(accs.items(), key=lambda kv: (kv[1], kv[0]))[0] if accs
                      else _clean(next(iter(events.values())).get("name")) or "Client Momentus")

    fns = list(db["momentus_functions"].find(
        {"_sync.deleted_at": None, "eventId": {"$in": sorted(events)},
         "startDate": {"$lte": hi}, "endDate": {"$gte": lo}},
        {"eventId": 1, "name": 1, "functionTypeName": 1, "roomId": 1, "roomName": 1,
         "startDate": 1, "endDate": 1, "startTime": 1, "endTime": 1, "isAllDay": 1,
         "guaranteedAttendance": 1, "expectedAttendance": 1, "agreedAttendance": 1, "_sync": 1}))

    days = defaultdict(list)     # ds -> lignes
    present = set()              # (event_id, ds)
    fn_rooms = set()             # (event_id, room_id, ds) couverts par une fonction
    notes = []                   # fonctions sans espace : (event_id, ds, ligne)
    for fn in fns:
        if (fn.get("_sync") or {}).get("deleted_at"):
            continue
        ev = events.get(fn.get("eventId"))
        a_d = _day(fn.get("startDate"))
        b_d = _day(fn.get("endDate")) or a_d
        if not ev or not a_d:
            continue
        b_d = max(a_d, b_d)
        all_day = bool(fn.get("isAllDay"))
        fs = "" if all_day else _hhmm(fn.get("startTime"))
        fe = "" if all_day else _hhmm(fn.get("endTime"))
        rid = fn.get("roomId")
        for x in _days(max(a_d, d_from), min(b_d, d_to)):
            ds = x.isoformat()
            line = {"kind": "function", "event_id": ev["_id"],
                    "name": _clean(fn.get("name")) or "Fonction",
                    "ftype": _clean(fn.get("functionTypeName"), 60),
                    "room": _clean(fn.get("roomName"), 80) if rid else "",
                    "start": fs if x == a_d else "", "end": fe if x == b_d else "",
                    "all_day": all_day, "pers": _fn_att(fn), "phase": ""}
            if rid:
                present.add((ev["_id"], ds))
                fn_rooms.add((ev["_id"], rid, ds))
                days[ds].append(line)
            else:
                notes.append((ev["_id"], ds, line))
    for ev in events.values():
        for bs in ev.get("bookedSpaces") or []:
            a_d, b_d = _day(bs.get("startDate")), _day(bs.get("endDate"))
            if not a_d or not b_d or b_d < d_from or a_d > d_to:
                continue
            all_day = bool(bs.get("isAllDay"))
            s = "" if all_day else _hhmm(bs.get("startTime"))
            e = "" if all_day else _hhmm(bs.get("endTime"))
            rid = bs.get("roomId")
            for x in _days(max(a_d, d_from), min(b_d, d_to)):
                ds = x.isoformat()
                present.add((ev["_id"], ds))
                if (ev["_id"], rid, ds) in fn_rooms:
                    continue
                days[ds].append({"kind": "space", "event_id": ev["_id"], "name": "",
                                 "ftype": "", "room": _clean(bs.get("roomName"), 80),
                                 "start": s if x == a_d else "", "end": e if x == b_d else "",
                                 "all_day": all_day, "pers": None,
                                 "phase": PHASES.get(bs.get("usageType"), "reserve")})
    for eid, ds, line in notes:
        if (eid, ds) in present:
            days[ds].append(line)

    used = set()
    max_pers = None
    for ds in sorted(days):
        lines = days[ds]
        # Doublons stricts (meme espace reserve deux fois le meme jour)
        seen, uniq = set(), []
        for ln in lines:
            k = tuple(sorted((kk, str(v)) for kk, v in ln.items()))
            if k not in seen:
                seen.add(k)
                uniq.append(ln)
        uniq.sort(key=lambda ln: (ln["kind"] != "function", ln["start"] or ("00:00" if ln["all_day"] else "99:99"),
                                  ln["end"] or "99:99", ln["name"].lower(), ln["room"].lower()))
        pers = [ln["pers"] for ln in uniq if ln["pers"]]
        day_pers = max(pers) if pers else None
        if day_pers and (max_pers is None or day_pers > max_pers):
            max_pers = day_pers
        used.update(ln["event_id"] for ln in uniq)
        out["days"].append({"date": ds, "pers": day_pers, "items": uniq})
    evs = []
    for eid in sorted(used, key=lambda i: (_clean(events[i].get("name")).lower(), i)):
        ev = events[eid]
        evs.append({"id": eid, "name": _clean(ev.get("name")) or "Reservation Momentus",
                    "type": _clean(ev.get("eventTypeName"), 60), "status": _status(ev),
                    "blackout": bool(ev.get("isBlackout")),
                    "interne_aco": bool(compte_aco(ev)), "compte_aco": compte_aco(ev)})
    out["events"] = evs
    out["totals"] = {"days": len(out["days"]), "events": len(evs), "max_pers": max_pers}
    return out


# ---------------------------------------------------------------------------
# Recherche sur toute la saison (GET /api/saison/search)
# ---------------------------------------------------------------------------
SEARCH_DEFAULT_BEFORE = 30
SEARCH_DEFAULT_AFTER = 365
SEARCH_MAX_DAYS = 400
SEARCH_MIN_LEN = 2
SEARCH_MAX_LEN = 80
SEARCH_HITS_PER_DAY = 20
SEARCH_MAX_HITS = 2500
SEARCH_MAX_ROOMS = 6
_KIND_ORDER = {"epreuve": 0, "client": 1, "lieu": 2, "visite": 3}


def norm_query(value):
    """Requete normalisee (casse, accents, espaces) ou None si trop courte /
    trop longue. Compare en Python seulement (jamais dans une requete Mongo)."""
    q = norm_type(_UNSAFE.sub(" ", str(value or "")))
    if len(q) < SEARCH_MIN_LEN or len(q) > SEARCH_MAX_LEN:
        return None
    return q


def search_window(raw_from, raw_to, today):
    """(debut, fin, erreur). Defaut : today-30 -> today+365 ; 400 jours au plus."""
    a = parse_day(raw_from) if raw_from else today - timedelta(days=SEARCH_DEFAULT_BEFORE)
    b = parse_day(raw_to) if raw_to else today + timedelta(days=SEARCH_DEFAULT_AFTER)
    if not a or not b or b < a:
        return None, None, "periode_invalide"
    if (b - a).days + 1 > SEARCH_MAX_DAYS:
        return None, None, "periode_trop_longue"
    return a, b, None


def _matches(tokens, *texts):
    blob = " | ".join(norm_type(t) for t in texts if t)
    return bool(blob) and all(t in blob for t in tokens)


def search(db, query, d_from, d_to, today=None):
    """Jours de [d_from, d_to] ou la requete trouve un client (nom d'evenement,
    compte Momentus, type d'evenement), un lieu (espace reserve ou espace d'une
    fonction ce jour-la), une epreuve Cockpit, ou des visites du site.

    Un passage sur momentus_functions + un sur momentus_events (memes regles
    que les seminaires : supprimes, annules, perdus, prospects ecartes ; les
    blackouts comptent). Resultats fusionnes par (jour, client). Jamais de
    contactRoles, e-mails, telephones ni montants."""
    q = norm_query(query)
    today = today or date.today()
    out = {"q": q or "", "from": d_from.isoformat(), "to": d_to.isoformat(), "days": [],
           "summary": {"days": 0, "hits": 0, "first": None, "next": None, "by_month": {}},
           "truncated": False}
    if not q:
        return out
    tokens = q.split()
    lo, hi = d_from.isoformat(), d_to.isoformat()
    room_hit = {}

    def _room_ok(name):
        n = _clean(name, 80)
        if n not in room_hit:
            room_hit[n] = _matches(tokens, n)
        return room_hit[n]

    fns = list(db["momentus_functions"].find(
        {"_sync.deleted_at": None, "startDate": {"$lte": hi}, "endDate": {"$gte": lo}},
        {"eventId": 1, "roomId": 1, "roomName": 1, "startDate": 1, "endDate": 1, "startTime": 1,
         "endTime": 1, "isAllDay": 1, "guaranteedAttendance": 1, "expectedAttendance": 1,
         "agreedAttendance": 1, "_sync": 1}))
    fn_eids = sorted({f.get("eventId") for f in fns if f.get("eventId")})
    qe = {"_sync.deleted_at": None, "isCanceled": {"$ne": True}, "isLost": {"$ne": True},
          "isProspect": {"$ne": True},
          "$or": [{"bookedSpaces": {"$elemMatch": {"startDate": {"$lte": hi}, "endDate": {"$gte": lo}}}},
                  {"_id": {"$in": fn_eids}}]}
    proj = {"name": 1, "eventTypeName": 1, "bookedSpaces": 1, "isDefinite": 1, "isTentative": 1,
            "isProspect": 1, "isCanceled": 1, "isLost": 1, "isBlackout": 1, "_sync": 1,
            "accountId": 1, "accountName": 1, "estimatedTotalAttendance": 1, "estimatedAttendance": 1}
    events, ev_text = {}, {}
    for ev in db["momentus_events"].find(qe, proj):
        if _excluded(ev):
            continue
        txt = _matches(tokens, ev.get("name"), ev.get("accountName"), ev.get("eventTypeName"))
        # Evenement qui ne correspond ni par son texte ni par un de ses espaces :
        # inutile d'aller plus loin
        if not txt and not any(_room_ok(bs.get("roomName")) for bs in ev.get("bookedSpaces") or []):
            ev_text[ev["_id"]] = False
            events[ev["_id"]] = ev       # garde : une fonction peut matcher par son espace
            continue
        ev_text[ev["_id"]] = txt
        events[ev["_id"]] = ev

    agg = {}   # (eid, ds) -> {present, rooms, matched, att, s, e, bs_s, bs_e}

    def _slot(eid, ds):
        a = agg.get((eid, ds))
        if a is None:
            a = agg[(eid, ds)] = {"present": False, "rooms": set(), "matched": set(), "att": None,
                                  "s": "", "e": "", "bs": "", "be": ""}
        return a

    def _widen(a, ks, ke, s, e):
        if s and (not a[ks] or s < a[ks]):
            a[ks] = s
        if e and (not a[ke] or e > a[ke]):
            a[ke] = e

    for fn in fns:
        if (fn.get("_sync") or {}).get("deleted_at"):
            continue
        eid = fn.get("eventId")
        ev = events.get(eid)
        a_d = _day(fn.get("startDate"))
        if not ev or not a_d:
            continue
        rid = fn.get("roomId")
        room = _clean(fn.get("roomName"), 80) if rid else ""
        r_ok = bool(room) and _room_ok(room)
        if not ev_text.get(eid) and not r_ok:
            continue
        b_d = max(a_d, _day(fn.get("endDate")) or a_d)
        all_day = bool(fn.get("isAllDay"))
        fs = "" if all_day else _hhmm(fn.get("startTime"))
        fe = "" if all_day else _hhmm(fn.get("endTime"))
        att = _fn_att(fn)
        for x in _days(max(a_d, d_from), min(b_d, d_to)):
            a = _slot(eid, x.isoformat())
            if rid:
                a["present"] = True
                if room:
                    a["rooms"].add(room)
                    if r_ok:
                        a["matched"].add(room)
            if att and (a["att"] is None or att > a["att"]):
                a["att"] = att
            _widen(a, "s", "e", fs if x == a_d else "", fe if x == b_d else "")
    for eid, ev in events.items():
        txt = ev_text.get(eid)
        for bs in ev.get("bookedSpaces") or []:
            room = _clean(bs.get("roomName"), 80)
            r_ok = bool(room) and _room_ok(room)
            if not txt and not r_ok:
                continue
            a_d, b_d = _day(bs.get("startDate")), _day(bs.get("endDate"))
            if not a_d or not b_d or b_d < d_from or a_d > d_to:
                continue
            all_day = bool(bs.get("isAllDay"))
            s = "" if all_day else _hhmm(bs.get("startTime"))
            e = "" if all_day else _hhmm(bs.get("endTime"))
            for x in _days(max(a_d, d_from), min(b_d, d_to)):
                a = _slot(eid, x.isoformat())
                a["present"] = True
                if room:
                    a["rooms"].add(room)
                    if r_ok:
                        a["matched"].add(room)
                _widen(a, "bs", "be", s if x == a_d else "", e if x == b_d else "")

    # Fusion par (jour, client) : plusieurs reservations d'un client le meme
    # jour = un resultat (comme les seminaires)
    merged = {}
    for (eid, ds), a in agg.items():
        if not a["present"]:
            continue
        ev = events[eid]
        txt = ev_text.get(eid)
        if not txt and not a["matched"]:
            continue
        ck = client_key(ev)
        m = merged.get((ds, ck))
        if m is None:
            m = merged[(ds, ck)] = {"names": [], "account": client_label(ev), "types": [],
                                    "client_hit": False, "rooms": set(), "matched": set(),
                                    "s": "", "e": "", "pers": None, "status": set(), "blackout": True,
                                    "n": 0, "compte_aco": ""}
        m["n"] += 1
        m["compte_aco"] = m["compte_aco"] or compte_aco(ev)
        nm = _clean(ev.get("name")) or "Reservation Momentus"
        if nm not in m["names"]:
            m["names"].append(nm)
        tp = _clean(ev.get("eventTypeName"), 60)
        if tp and tp not in m["types"]:
            m["types"].append(tp)
        m["client_hit"] = m["client_hit"] or bool(txt)
        m["rooms"] |= a["rooms"]
        m["matched"] |= a["matched"]
        s, e = (a["s"], a["e"]) if (a["s"] or a["e"]) else (a["bs"], a["be"])
        if s and (not m["s"] or s < m["s"]):
            m["s"] = s
        if e and (not m["e"] or e > m["e"]):
            m["e"] = e
        pers = a["att"] or _att(ev.get("estimatedTotalAttendance")) or _att(ev.get("estimatedAttendance"))
        if pers and (m["pers"] is None or pers > m["pers"]):
            m["pers"] = pers
        m["status"].add(_status(ev))
        m["blackout"] = m["blackout"] and bool(ev.get("isBlackout"))

    by_day = defaultdict(list)
    for (ds, ck), m in merged.items():
        kind = "client" if m["client_hit"] else "lieu"
        rooms = sorted(m["rooms"], key=str.lower)
        matched = sorted(m["matched"], key=str.lower)
        label = m["names"][0] if len(m["names"]) == 1 else (m["account"] or " / ".join(m["names"][:2]))
        st = m["status"]
        by_day[ds].append({
            "kind": kind, "label": label, "client": ck, "account": m["account"] or m["names"][0],
            "event": " / ".join(m["names"][:3]), "type": " / ".join(m["types"][:2]),
            "rooms": (matched if kind == "lieu" else rooms)[:SEARCH_MAX_ROOMS],
            "nrooms": len(matched if kind == "lieu" else rooms), "matched": matched[:SEARCH_MAX_ROOMS],
            "start": m["s"], "end": m["e"], "pers": m["pers"], "reservations": m["n"],
            "status": "confirme" if st == {"confirme"} else ("option" if "option" in st else next(iter(st), "")),
            "blackout": m["blackout"],
            "interne_aco": bool(m["compte_aco"]), "compte_aco": m["compte_aco"]})

    # Epreuves Cockpit (fenetre montage -> demontage)
    for ep in epreuves(db, d_from, d_to):
        if not _matches(tokens, ep["event"], ep["short"], f"{ep['event']} {ep['year']}"):
            continue
        a_d, b_d = _day(ep["start"]), _day(ep["end"])
        pub = set(ep.get("public_days") or [])
        for x in _days(max(a_d, d_from), min(b_d, d_to)):
            ds = x.isoformat()
            by_day[ds].append({"kind": "epreuve", "label": f"{ep['event']} {ep['year']}",
                               "event": ep["event"], "color": ep["color"],
                               "phase": "public" if ds in pub else "montage",
                               "rooms": [], "nrooms": 0, "start": "", "end": "", "pers": None})

    # Visites du site (SAISON) : "visite", "visites libres", "guidee"...
    vis_terms = {"libre": "visites libres", "guidee": "visites guidees"}
    want = set()
    if any(t.startswith(("visit", "guid")) for t in tokens):
        want = {k for k, lab in vis_terms.items() if all(t in lab for t in tokens)}
    if want:
        for ds, info in sorted(_visit_days(db, d_from, d_to).items()):
            if not (lo <= ds <= hi) or not info:
                continue
            for k in sorted(want):
                if info.get("visite_" + k):
                    by_day[ds].append({
                        "kind": "visite", "label": "Visites libres" if k == "libre" else "Visites guidees",
                        "rooms": [], "nrooms": 0,
                        "start": "" if info.get("is24h") else info.get("open") or "",
                        "end": "" if info.get("is24h") else info.get("close") or "", "pers": None})

    total = 0
    by_month = defaultdict(int)
    t_iso = today.isoformat()
    for ds in sorted(by_day):
        hits = by_day[ds]
        hits.sort(key=lambda h: (_KIND_ORDER.get(h["kind"], 9), h["start"] or "99:99", h["label"].lower()))
        by_month[ds[:7]] += 1
        if out["summary"]["first"] is None:
            out["summary"]["first"] = ds
        if out["summary"]["next"] is None and ds >= t_iso:
            out["summary"]["next"] = ds
        if total >= SEARCH_MAX_HITS:
            out["truncated"] = True
            continue
        shown = hits[:SEARCH_HITS_PER_DAY]
        total += len(shown)
        out["days"].append({"date": ds, "count": len(hits), "more": max(0, len(hits) - len(shown)),
                            "hits": shown})
    out["summary"].update(days=len(by_day), hits=sum(len(h) for h in by_day.values()),
                          by_month=dict(sorted(by_month.items())))
    return out


def search_cached(db, query, d_from, d_to, today=None):
    q = norm_query(query)
    key = ("search", getattr(db, "name", ""), q, d_from.isoformat(), d_to.isoformat(),
           (today or date.today()).isoformat())
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    res = search(db, query, d_from, d_to, today)
    with _cache_lock:
        if len(_cache) > 64:
            _cache.clear()
        _cache[key] = (now, res)
    return res


def compute_seminaires_cached(db, d_from, d_to, cfg=None):
    cfg = cfg or get_seminaires(db)
    sig = hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    key = ("sem", getattr(db, "name", ""), d_from.isoformat(), d_to.isoformat(), sig)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    res = compute_seminaires(db, d_from, d_to, cfg)
    with _cache_lock:
        if len(_cache) > 64:
            _cache.clear()
        _cache[key] = (now, res)
    return res


def payload(db, d_from, d_to):
    """Reponse de GET /api/saison/indicateurs. `days` garde son format ;
    les seminaires sont dans une cle a part."""
    indicators = get_config(db)
    sem_cfg = get_seminaires(db)
    return {"ok": True, "from": d_from.isoformat(), "to": d_to.isoformat(),
            "indicators": public_indicators(db, indicators),
            "days": compute_cached(db, d_from, d_to, indicators),
            "epreuves": epreuves(db, d_from, d_to),
            "seminaires": {"enabled": bool(sem_cfg.get("enabled")),
                           "max_list": sem_cfg.get("max_list"),
                           "days": compute_seminaires_cached(db, d_from, d_to, sem_cfg)}}
