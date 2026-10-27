"""
patrols.py - Patrouilles programmees des tablettes Field (coeur, sans Flask).

Cahier des charges : docs/claude/patrouilles.md. Ce module porte les donnees et
les transitions ; les routes sont dans patrols_api.py, le moteur periodique
dans patrol_engine.py (tache planifiee Windows, JAMAIS de thread dans Cockpit).

    patrol_points     bibliotheque partagee des points d'interet
    patrol_scenarios  parcours ordonne de points (vehicule ou a pied)
    patrol_runs       une patrouille envoyee a une tablette (instantane du
                      scenario, propositions, avancement point par point)

Une tablette ne porte qu'UNE proposition a la fois, fiche ou patrouille :
`field_devices.pending_proposal` (fiche, dispatch_auto) et
`field_devices.patrol_proposal` {run_id, proposed_at, expires_at} s'excluent
(dispatch_auto._proposal_pending regarde les deux). Patrouille acceptee :
`field_devices.active_patrol_run`. Le statut de l'agent reste `patrouille`
(disponible) ; un engagement sur une fiche met la patrouille en pause
(`sync_pause`, appele par la tablette et par le moteur), le retour en
disponible la reprend au prochain point non fait.

Main courante : une fiche par patrouille, ouverte a l'acceptation, de la
categorie de la tablette (`content_category.patrol_run`), une entree par point
fait / passe, pause et reprise, close avec le bilan. `patrouille` = nom de la
tablette : la fiche est ainsi exclue du dispatch, du libre-service et des
alertes d'urgence (pas de niveau d'urgence) ; /field/my-fiches l'ecarte.
"""

import logging
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bson import ObjectId

import pcorg_history as PH

logger = logging.getLogger(__name__)

PARIS = ZoneInfo("Europe/Paris")
SETTINGS_ID = "patrols"
DEFAULTS = {
    "radius_m": 25,            # validation GPS d'un point
    "accept_timeout_s": 120,   # delai de reponse a une proposition
    "seen_window_min": 15,     # tablette "vue recemment"
}
ANSWER_GRACE_S = 4             # = dispatch_auto.ANSWER_GRACE_S
TRAVELS = ("vehicle", "pedestrian")
# Dominante (theme) d'un scenario : guide les propositions d'apres
# l'historique (livraison 4) et le groupe de tablettes vise. "Prevention" =
# plans de prevention / lieux de travail (Asker) et incidents lies aux travaux.
DOMINANTES = {
    "generale": "Generale",
    "surete": "Surete",
    "secours": "Secours",
    "incendie": "Prevention incendie",
    "prevention": "Prevention",
}
RUN_OPEN = ("proposed", "running", "paused")
RUN_STATES = RUN_OPEN + ("done", "partial", "refused", "expired", "cancelled")
BOT = "Patrouilles"
MAX_POINTS = 40
BUSY_STATUSES = ("intervention", "sur_place", "fin_intervention", "pause")


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt):
    dt = _aware(dt)
    return dt.isoformat() if isinstance(dt, datetime) else None


def _oid(v):
    try:
        return ObjectId(str(v))
    except Exception:
        return None


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _field():
    import field
    return field


def get_settings(db):
    doc = db["cockpit_settings"].find_one({"_id": SETTINGS_ID}) or {}
    out = dict(DEFAULTS)
    for k, (lo, hi) in (("radius_m", (5, 300)), ("accept_timeout_s", (30, 900)), ("seen_window_min", (1, 120))):
        try:
            out[k] = max(lo, min(hi, int(doc.get(k, out[k]))))
        except (TypeError, ValueError):
            pass
    return out


def ensure_indexes(db):
    try:
        db["patrol_runs"].create_index([("state", 1), ("current.expires_at", 1)], name="state_expiry")
        db["patrol_runs"].create_index([("device_id", 1), ("state", 1)], name="device_state")
        db["patrol_runs"].create_index("slot_key", unique=True, sparse=True, name="slot_key_unique")
        db["patrol_runs"].create_index([("created_at", -1)], name="created_desc")
        db["patrol_runs"].create_index([("plan_id", 1), ("state", 1)], name="plan_state")
    except Exception as e:  # pragma: no cover - droits / index deja la
        logger.debug("index patrol_runs : %s", e)


# ---------------------------------------------------------------------------
# Zones de competence (couche Looker de la prise de poste, operateurs.py)
# ---------------------------------------------------------------------------

def _zones(db):
    import operateurs as OP
    try:
        return OP.zones(db)
    except Exception as e:
        logger.warning("patrouilles : zones indisponibles (%s)", e)
        return []


def zones_of_point(db, lat, lng, zones=None):
    from pcorg_enrich import point_in_geom
    zones = _zones(db) if zones is None else zones
    return [z["id"] for z in zones if point_in_geom(lat, lng, z.get("geometry"))]


def device_covers(device, points):
    """Zone de competence de la tablette couvrant TOUS les points (aucune
    zone declaree = partout). Les zones des points sont calculees a
    l'enregistrement (`zone_ids`)."""
    mine = set(str(z) for z in (device or {}).get("competence_zones") or [])
    if not mine:
        return True
    return all(mine & set(p.get("zone_ids") or []) for p in points)


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------

def _clean_point(data, settings):
    name = str(data.get("name") or "").strip()[:80]
    if not name:
        return None, "nom_obligatoire"
    try:
        lat, lng = float(data.get("lat")), float(data.get("lng"))
    except (TypeError, ValueError):
        return None, "position_invalide"
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, "position_invalide"
    try:
        radius = max(5, min(300, int(data.get("radius_m") or settings["radius_m"])))
    except (TypeError, ValueError):
        radius = settings["radius_m"]
    try:
        dwell = max(0, min(3600, int(data.get("dwell_s") or 0)))
    except (TypeError, ValueError):
        dwell = 0
    return {
        "name": name, "lat": lat, "lng": lng, "radius_m": radius,
        "instruction": str(data.get("instruction") or "").strip()[:500],
        "photo_required": bool(data.get("photo_required")),
        "dwell_s": dwell,
    }, None


def save_point(db, data, by, point_id=None):
    """Cree ou modifie un point. Retourne (point, erreur)."""
    clean, err = _clean_point(data or {}, get_settings(db))
    if err:
        return None, err
    clean["zone_ids"] = zones_of_point(db, clean["lat"], clean["lng"])
    now = _now()
    clean.update(updated_at=now, updated_by=by)
    if point_id:
        oid = _oid(point_id)
        if not oid:
            return None, "not_found"
        r = db["patrol_points"].update_one({"_id": oid}, {"$set": clean})
        if not r.matched_count:
            return None, "not_found"
        return db["patrol_points"].find_one({"_id": oid}), None
    clean.update(_id=ObjectId(), active=True, source="manual", ia_reason=None,
                 created_by=by, created_at=now)
    db["patrol_points"].insert_one(clean)
    return clean, None


def set_point_active(db, point_id, active):
    oid = _oid(point_id)
    return bool(oid and db["patrol_points"].update_one({"_id": oid}, {"$set": {"active": bool(active)}}).matched_count)


def pub_point(p, zone_labels=None):
    return {
        "id": str(p["_id"]), "name": p.get("name"), "lat": p.get("lat"), "lng": p.get("lng"),
        "radius_m": p.get("radius_m"), "instruction": p.get("instruction") or "",
        "photo_required": bool(p.get("photo_required")), "dwell_s": p.get("dwell_s") or 0,
        "zone_ids": p.get("zone_ids") or [],
        "zones": [zone_labels.get(z, z) for z in p.get("zone_ids") or []] if zone_labels is not None else None,
        "active": p.get("active", True) is not False, "source": p.get("source") or "manual",
        "ia_reason": p.get("ia_reason"), "updated_at": _iso(p.get("updated_at")),
    }


# ---------------------------------------------------------------------------
# Moteur d'itineraire (Valhalla de la VM, memes recettes que routing.py)
# ---------------------------------------------------------------------------

def _valhalla_url():
    import routing
    return routing.VALHALLA_URL


def _costing(travel, mode="normal"):
    if travel == "pedestrian":
        return {"costing": "pedestrian"}
    import valhalla_admin as VA
    return VA.route_recipe(mode)


def _valhalla(path, payload, timeout=20):
    try:
        r = requests.post(_valhalla_url() + path, json=payload, timeout=timeout)
        if r.status_code == 400:
            return None, "no_route"
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.RequestException as e:
        logger.info("patrouilles : Valhalla injoignable (%s)", e)
        return None, "valhalla_unreachable"


def route_through(coords, travel, mode="normal"):
    """Itineraire passant par `coords` [(lat, lng)...] dans l'ordre. Chaque
    point est un arret ("break") : une jambe par troncon. Retourne
    ({distance_m, duration_s, polyline, legs:[{distance_m, duration_s}]}, err)."""
    import routing
    if len(coords) < 2:
        return {"distance_m": 0, "duration_s": 0, "polyline": routing._encode_polyline6(coords), "legs": []}, None
    payload = {"locations": [{"lat": a, "lon": b, "type": "break"} for a, b in coords],
               "directions_options": {"units": "kilometers"}, "id": "cockpit-patrol"}
    payload.update(_costing(travel, mode))
    resp, err = _valhalla("/route", payload)
    if err:
        return None, err
    trip = (resp or {}).get("trip") or {}
    legs = trip.get("legs") or []
    pts = []
    for leg in legs:
        seg = routing._decode_polyline6(leg.get("shape", ""))
        if pts and seg and pts[-1] == seg[0]:
            seg = seg[1:]
        pts.extend(seg)
    s = trip.get("summary") or {}
    return {
        "distance_m": int(float(s.get("length") or 0) * 1000),
        "duration_s": int(s.get("time") or 0),
        "polyline": routing._encode_polyline6(pts),
        "legs": [{"distance_m": int(float((l.get("summary") or {}).get("length") or 0) * 1000),
                  "duration_s": int((l.get("summary") or {}).get("time") or 0)} for l in legs],
        "uses_raceway": bool(s.get("has_toll")),
    }, None


def _matrix(coords, travel):
    payload = {"sources": [{"lat": a, "lon": b} for a, b in coords],
               "targets": [{"lat": a, "lon": b} for a, b in coords]}
    payload.update(_costing(travel))
    resp, err = _valhalla("/sources_to_targets", payload, timeout=30)
    if err:
        return None, err
    n = len(coords)
    big = 10 ** 9
    m = [[big] * n for _ in range(n)]
    for i, row in enumerate((resp or {}).get("sources_to_targets") or []):
        for c in row:
            j = c.get("to_index")
            if j is None:
                continue
            t = c.get("time")
            m[i][j] = int(t) if t is not None else big
    return m, None


def best_order(m, loop):
    """Ordre des points le plus court, le premier reste le depart. Exact
    jusqu'a 11 points (programmation dynamique), au-dela plus proche voisin
    puis 2-opt. Retourne la liste des indices."""
    n = len(m)
    if n <= 2:
        return list(range(n))
    if n <= 11:
        full = (1 << n) - 1
        best = {(1, 0): (0, None)}
        for mask in range(1, full + 1):
            if not mask & 1:
                continue
            for last in range(n):
                cur = best.get((mask, last))
                if cur is None:
                    continue
                for nxt in range(1, n):
                    if mask & (1 << nxt):
                        continue
                    key = (mask | (1 << nxt), nxt)
                    cost = cur[0] + m[last][nxt]
                    if key not in best or cost < best[key][0]:
                        best[key] = (cost, last)
        end, cost_end = None, None
        for last in range(1, n):
            c = best[(full, last)][0] + (m[last][0] if loop else 0)
            if cost_end is None or c < cost_end:
                end, cost_end = last, c
        order, mask, last = [], full, end
        while last is not None:
            order.append(last)
            prev = best[(mask, last)][1]
            mask &= ~(1 << last)
            last = prev
        return list(reversed(order))
    order, left = [0], set(range(1, n))
    while left:
        nxt = min(left, key=lambda j: m[order[-1]][j])
        order.append(nxt)
        left.remove(nxt)

    def length(o):
        total = sum(m[o[i]][o[i + 1]] for i in range(len(o) - 1))
        return total + (m[o[-1]][o[0]] if loop else 0)

    improved = True
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                cand = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                if length(cand) < length(order):
                    order, improved = cand, True
    return order


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def _points_by_id(db, ids):
    oids = [o for o in (_oid(i) for i in ids) if o]
    return {str(p["_id"]): p for p in db["patrol_points"].find({"_id": {"$in": oids}})}


def scenario_points(db, sc):
    by_id = _points_by_id(db, sc.get("points") or [])
    return [by_id[i] for i in sc.get("points") or [] if i in by_id]


def _clean_scenario(db, data):
    name = str(data.get("name") or "").strip()[:80]
    if not name:
        return None, "nom_obligatoire"
    travel = data.get("travel") if data.get("travel") in TRAVELS else "vehicle"
    ids = [str(x) for x in (data.get("points") or []) if x]
    if len(ids) > MAX_POINTS:
        return None, "trop_de_points"
    by_id = _points_by_id(db, ids)
    missing = [i for i in ids if i not in by_id]
    if missing:
        return None, "point_inconnu"
    color = str(data.get("color") or "").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):     # injectee dans du HTML (carte)
        color = "#2563eb"
    dominante = data.get("dominante") if data.get("dominante") in DOMINANTES else "generale"
    return {"name": name, "travel": travel, "loop": bool(data.get("loop")), "points": ids,
            "color": color, "dominante": dominante, "note": str(data.get("note") or "").strip()[:500]}, None


def estimate(db, sc):
    pts = scenario_points(db, sc)
    coords = [(p["lat"], p["lng"]) for p in pts]
    if sc.get("loop") and len(coords) > 1:
        coords.append(coords[0])
    out, err = route_through(coords, sc.get("travel") or "vehicle")
    if err:
        return {"error": err, "computed_at": _now()}
    out.update(computed_at=_now(), engine="valhalla")
    return out


def save_scenario(db, data, by, scenario_id=None):
    clean, err = _clean_scenario(db, data or {})
    if err:
        return None, err
    now = _now()
    clean["estimate"] = estimate(db, clean) if len(clean["points"]) >= 2 else None
    clean.update(updated_at=now, updated_by=by)
    if scenario_id:
        oid = _oid(scenario_id)
        if not oid:
            return None, "not_found"
        r = db["patrol_scenarios"].update_one({"_id": oid}, {"$set": clean, "$inc": {"version": 1}})
        if not r.matched_count:
            return None, "not_found"
        return db["patrol_scenarios"].find_one({"_id": oid}), None
    clean.update(_id=ObjectId(), archived=False, version=1, created_by=by, created_at=now)
    db["patrol_scenarios"].insert_one(clean)
    return clean, None


def optimize_order(db, data):
    """Ordre le plus court propose pour les points de `data` (le premier reste
    le depart ; boucle = retour au depart). Rien n'est enregistre."""
    ids = [str(x) for x in (data.get("points") or []) if x]
    by_id = _points_by_id(db, ids)
    pts = [by_id[i] for i in ids if i in by_id]
    if len(pts) < 3:
        return {"points": [str(p["_id"]) for p in pts]}, None
    travel = data.get("travel") if data.get("travel") in TRAVELS else "vehicle"
    m, err = _matrix([(p["lat"], p["lng"]) for p in pts], travel)
    if err:
        return None, err
    order = best_order(m, bool(data.get("loop")))

    def cost(o):
        c = sum(m[o[i]][o[i + 1]] for i in range(len(o) - 1))
        return c + (m[o[-1]][o[0]] if data.get("loop") else 0)

    before, after = cost(list(range(len(pts)))), cost(order)
    if after >= before:          # a duree egale, on garde l'ordre saisi
        order, after = list(range(len(pts))), before
    new_ids = [str(pts[i]["_id"]) for i in order]
    return {"points": new_ids, "before_s": before, "after_s": after,
            "changed": new_ids != [str(p["_id"]) for p in pts]}, None


def pub_scenario(db, sc, with_points=False, zone_labels=None):
    est = sc.get("estimate") or {}
    out = {
        "id": str(sc["_id"]), "name": sc.get("name"), "travel": sc.get("travel") or "vehicle",
        "loop": bool(sc.get("loop")), "color": sc.get("color") or "#2563eb", "note": sc.get("note") or "",
        "dominante": sc.get("dominante") or "generale",
        "points": sc.get("points") or [], "archived": bool(sc.get("archived")),
        "version": sc.get("version") or 1, "updated_at": _iso(sc.get("updated_at")),
        "updated_by": sc.get("updated_by"),
        "estimate": {"distance_m": est.get("distance_m"), "duration_s": est.get("duration_s"),
                     "polyline": est.get("polyline"), "legs": est.get("legs") or [],
                     "error": est.get("error"), "computed_at": _iso(est.get("computed_at"))} if est else None,
    }
    if with_points:
        pts = scenario_points(db, sc)
        out["point_docs"] = [pub_point(p, zone_labels) for p in pts]
        # Zones communes a tous les points : une tablette dont la zone en fait
        # partie peut recevoir la patrouille.
        common = None
        for p in pts:
            z = set(p.get("zone_ids") or [])
            common = z if common is None else common & z
        out["common_zones"] = sorted(common or [])
    return out


# ---------------------------------------------------------------------------
# Tablettes candidates
# ---------------------------------------------------------------------------

def _busy(device, now):
    """Proposition (fiche ou patrouille) en attente, ou patrouille en cours."""
    import dispatch_auto as DA
    if DA._proposal_pending(device, now):
        return "proposition_en_cours"
    if device.get("active_patrol_run"):
        return "patrouille_en_cours"
    return None


def candidates(db, run, now=None, exclude_ids=()):
    """Tablettes a qui proposer la patrouille, la meilleure en premier, et les
    raisons d'exclusion des autres (affichees a l'operateur).

    Eligibles : non revoquee, epreuve pas terminee, pas declarante, statut
    `patrouille` (disponible), vue recemment, sans proposition ni patrouille
    en cours, zone de competence couvrant tous les points. Cible "groupe" :
    tablettes du groupe de balises ; cible "device" : celle-la seule. La
    plus proche du premier point d'abord."""
    F = _field()
    now = now or _now()
    st = get_settings(db)
    target = run.get("target") or {}
    q = {"revoked": {"$ne": True}}
    if target.get("kind") == "device":
        q["_id"] = _oid(target.get("device_id"))
    elif target.get("kind") == "group":
        q["beacon_group_id"] = target.get("beacon_group_id")
    else:
        return [], []
    groups = F._beacon_groups(db)
    pts = (run.get("scenario") or {}).get("points") or []
    first = pts[0] if pts else None
    exclude = {str(x) for x in exclude_ids}
    ok, skipped = [], []
    for d in db["field_devices"].find(q):
        name = d.get("name") or "?"
        why = None
        if str(d["_id"]) in exclude:
            why = "deja_sollicitee"
        elif F._device_declarant(db, d, groups):
            why = "declarante"
        elif F._device_expired(db, d):
            why = "epreuve_terminee"
        elif (d.get("status") or "patrouille") != "patrouille":
            why = "statut_" + (d.get("status") or "?")
        else:
            seen = _aware(d.get("last_seen"))
            if not isinstance(seen, datetime) or (now - seen).total_seconds() > st["seen_window_min"] * 60:
                why = "pas_vue_recemment"
            else:
                why = _busy(d, now) or (None if device_covers(d, pts) else "hors_zone")
        if why:
            skipped.append({"device_id": str(d["_id"]), "name": name, "reason": why})
            continue
        pos = d.get("last_position") or {}
        dist = None
        if first and pos.get("lat") is not None and pos.get("lng") is not None:
            try:
                dist = haversine_m(first["lat"], first["lng"], float(pos["lat"]), float(pos["lng"]))
            except (TypeError, ValueError):
                dist = None
        ok.append({"device": d, "distance_m": round(dist) if dist is not None else None})
    ok.sort(key=lambda c: (c["distance_m"] is None, c["distance_m"] or 0))
    return ok, skipped


# ---------------------------------------------------------------------------
# Patrouilles (runs)
# ---------------------------------------------------------------------------

def _snapshot(db, sc):
    pts = scenario_points(db, sc)
    return {
        "id": str(sc["_id"]), "name": sc.get("name"), "travel": sc.get("travel") or "vehicle",
        "loop": bool(sc.get("loop")), "color": sc.get("color"), "version": sc.get("version") or 1,
        "dominante": sc.get("dominante") or "generale",
        "points": [{"point_id": str(p["_id"]), "name": p.get("name"), "lat": p["lat"], "lng": p["lng"],
                    "radius_m": p.get("radius_m") or DEFAULTS["radius_m"],
                    "instruction": p.get("instruction") or "", "photo_required": bool(p.get("photo_required")),
                    "dwell_s": p.get("dwell_s") or 0, "zone_ids": p.get("zone_ids") or []} for p in pts],
        "estimate": {k: (sc.get("estimate") or {}).get(k) for k in ("distance_m", "duration_s", "polyline")},
    }


def create_run(db, scenario_id, target, by, plan_id=None, slot_key=None, now=None):
    """Nouvelle patrouille, puis proposition a la premiere tablette eligible.
    Retourne (run, code, skipped). code : proposed | no_device | ..."""
    now = now or _now()
    sc = db["patrol_scenarios"].find_one({"_id": _oid(scenario_id), "archived": {"$ne": True}})
    if not sc:
        return None, "scenario_introuvable", []
    snap = _snapshot(db, sc)
    if not snap["points"]:
        return None, "scenario_vide", []
    if (target or {}).get("kind") not in ("device", "group"):
        return None, "cible_invalide", []
    run = {
        "_id": str(uuid.uuid4()), "plan_id": plan_id, "scenario": snap,
        "target": {"kind": target["kind"], "device_id": target.get("device_id"),
                   "beacon_group_id": target.get("beacon_group_id")},
        "state": "proposed", "device_id": None, "device_name": None, "current": None, "attempts": [],
        "fiche_id": None, "event": None, "year": None,
        "steps": [{"point_id": p["point_id"], "name": p["name"], "state": "pending"} for p in snap["points"]],
        "pauses": [], "created_at": now, "created_by": by, "started_at": None, "ended_at": None,
    }
    if slot_key:
        run["slot_key"] = slot_key
    db["patrol_runs"].insert_one(run)
    code, skipped = propose_next(db, run["_id"], now=now)
    return db["patrol_runs"].find_one({"_id": run["_id"]}), code, skipped


def propose_next(db, run_id, now=None):
    """Propose la patrouille a la tablette eligible suivante. Sans candidat :
    patrouille `refused` (personne). Retourne (code, skipped)."""
    now = now or _now()
    run = db["patrol_runs"].find_one({"_id": run_id})
    if not run or run.get("state") != "proposed" or run.get("current"):
        return "noop", []
    tried = [a.get("device_id") for a in run.get("attempts") or []]
    cands, skipped = candidates(db, run, now=now, exclude_ids=tried)
    timeout = get_settings(db)["accept_timeout_s"]
    expires = now + timedelta(seconds=timeout)
    chosen = None
    for c in cands:
        dev = c["device"]
        # Reservation atomique : ni proposition de fiche, ni de patrouille,
        # ni patrouille en cours.
        r = db["field_devices"].update_one(
            {"_id": dev["_id"], "status": {"$in": ["patrouille", None]},
             "active_patrol_run": {"$in": [None, ""]},
             "$and": [
                 {"$or": [{"pending_proposal": None}, {"pending_proposal": {"$exists": False}},
                          {"pending_proposal.expires_at": {"$lt": now - timedelta(seconds=ANSWER_GRACE_S)}}]},
                 {"$or": [{"patrol_proposal": None}, {"patrol_proposal": {"$exists": False}},
                          {"patrol_proposal.expires_at": {"$lt": now - timedelta(seconds=ANSWER_GRACE_S)}}]},
             ]},
            {"$set": {"patrol_proposal": {"run_id": run_id, "proposed_at": now, "expires_at": expires}}},
        )
        if r.modified_count:
            chosen = c
            break
    if not chosen:
        r = db["patrol_runs"].update_one(
            {"_id": run_id, "state": "proposed", "current": None},
            {"$set": {"state": "refused", "ended_at": now,
                      "end_reason": "aucune tablette disponible" if not tried
                      else "%d refus ou silence, plus de tablette disponible" % len(tried)}})
        if r.modified_count:
            on_not_taken(db, run_id, now)
        return ("no_device" if not tried else "exhausted"), skipped
    dev = chosen["device"]
    cur = {"device_id": str(dev["_id"]), "device_name": dev.get("name") or "?", "proposed_at": now,
           "expires_at": expires, "distance_m": chosen["distance_m"]}
    r = db["patrol_runs"].update_one({"_id": run_id, "state": "proposed", "current": None},
                                     {"$set": {"current": cur}})
    if not r.modified_count:
        _release_proposal(db, dev["_id"], run_id)
        return "race", skipped
    try:
        sc = run.get("scenario") or {}
        _field().send_push_to_device(
            db, dev["_id"], title="Patrouille proposee : " + (sc.get("name") or ""),
            body="%d points, reponse attendue sous %d s" % (len(sc.get("points") or []), timeout),
            url="/field", tag="patrol-" + run_id)
    except Exception:
        pass
    return "proposed", skipped


ALERT_SLUG = "patrol_not_taken"


def on_not_taken(db, run_id, now):
    """Personne n'a pris la patrouille (refus, silence, aucune tablette) :
    alerte "Patrouille non prise" a la centrale et fiche Information close
    dans la main courante. Une seule fois par patrouille."""
    r = db["patrol_runs"].update_one({"_id": run_id, "not_taken_at": {"$exists": False}},
                                     {"$set": {"not_taken_at": now}})
    if not r.modified_count:
        return
    run = db["patrol_runs"].find_one({"_id": run_id}) or {}
    sc = run.get("scenario") or {}
    tried = ", ".join("%s (%s)" % (a.get("device_name"), {"refused": "refus", "timeout": "sans reponse"}
                                   .get(a.get("answer"), a.get("answer"))) for a in run.get("attempts") or [])
    why = run.get("end_reason") or "aucune tablette disponible"
    plan = db["patrol_plans"].find_one({"_id": _oid(run.get("plan_id"))}, {"name": 1}) if run.get("plan_id") else None
    origin = ("planification " + plan["name"]) if plan else ("envoi de " + (run.get("created_by") or "?"))
    try:
        event, year = _current_event(db, now)
    except Exception:
        event, year = None, None
    text = "Patrouille non prise : %s (%s)" % (sc.get("name") or "?", origin)
    ids = [_oid(a.get("device_id")) for a in run.get("attempts") or []]
    if (run.get("target") or {}).get("device_id"):
        ids.append(_oid(run["target"]["device_id"]))
    is_test = bool(ids) and db["field_devices"].count_documents(
        {"_id": {"$in": [i for i in ids if i]}, "test_mode": True}) > 0
    if is_test:
        text = "[TEST] " + text
    body = why + ((". Sollicitees : " + tried) if tried else "")
    fid = str(uuid.uuid5(uuid.NAMESPACE_URL, "patrol-not-taken|" + run_id))
    doc = {
        "_id": fid, "event": event, "year": year, "ts": now,
        "timestamp_iso": now.astimezone(PARIS).isoformat(), "close_ts": now,
        "close_iso": now.astimezone(PARIS).isoformat(), "category": "PCO.Information",
        "source": "PCO.Information", "text": text, "text_full": text, "comment": "", "comment_history": [],
        "operator": BOT, "operator_id_create": "patrol:" + run_id, "operator_close": BOT,
        "operator_id_close": "patrol:" + run_id, "status_code": 10, "severity": 0, "niveau_urgence": None,
        "is_incident": False, "area": None, "gps": None, "group": None,
        "content_category": {"patrol_not_taken": run_id, "patrol_scenario": sc.get("id")},
        "extracted": {"phones": None, "plates": None}, "tags": ["patrouille"],
        "synced_at": None, "sql_id": None, "guid": None, "server": "COCKPIT", "bounce_rev": 1,
    }
    try:
        db["pcorg"].insert_one(doc)
        PH.append_entry(db["pcorg"], fid, PH.make_entry(BOT, body, ts=now, system=True))
    except Exception as e:
        logger.warning("patrouille %s : fiche non prise (%s)", run_id, e)
    try:
        db["cockpit_active_alerts"].insert_one({
            "definition_slug": ALERT_SLUG, "event": event, "year": str(year) if year is not None else None,
            "title": ("[TEST] " if is_test else "") + "Patrouille non prise - " + (sc.get("name") or "?"),
            "message": body[:300],
            "timeStr": now.astimezone(PARIS).strftime("%H:%M"), "dedup_key": "patrol-not-taken-" + run_id,
            "triggeredAt": now, "expiresAt": now + timedelta(hours=2), "status": "active",
            "actionData": {"run_id": run_id, "plan_id": run.get("plan_id"), "pcorg_id": fid,
                           "scenario": sc.get("name")},
        })
    except Exception as e:
        logger.warning("patrouille %s : alerte non levee (%s)", run_id, e)
    logger.info("patrouille %s non prise : %s", run_id, body)


def _current_event(db, now=None):
    import event_courant as EC
    return EC.current_event(db, now)


def _release_proposal(db, device_id, run_id):
    db["field_devices"].update_one({"_id": _oid(device_id), "patrol_proposal.run_id": run_id},
                                   {"$set": {"patrol_proposal": None}})


def _close_attempt(db, run_id, device_id, answer, now, reason=None):
    run = db["patrol_runs"].find_one({"_id": run_id}, {"current": 1, "state": 1})
    cur = (run or {}).get("current") or {}
    if (run or {}).get("state") != "proposed" or cur.get("device_id") != str(device_id):
        return None
    att = dict(cur, answer=answer, answered_at=now, reason=reason)
    r = db["patrol_runs"].update_one(
        {"_id": run_id, "state": "proposed", "current.device_id": str(device_id)},
        {"$set": {"current": None}, "$push": {"attempts": att}})
    if not r.modified_count:
        return None
    _release_proposal(db, device_id, run_id)
    return att


def refuse(db, run_id, device_id, now=None, answer="refused", reason=None):
    """Refus (ou silence) : tablette suivante si la cible est un groupe, sinon
    la patrouille n'est pas prise."""
    now = now or _now()
    att = _close_attempt(db, run_id, device_id, answer, now, reason=(reason or "").strip()[:200] or None)
    if not att:
        return False
    run = db["patrol_runs"].find_one({"_id": run_id})
    if (run.get("target") or {}).get("kind") == "group":
        propose_next(db, run_id, now=now)
    else:
        r = db["patrol_runs"].update_one(
            {"_id": run_id, "state": "proposed", "current": None},
            {"$set": {"state": "refused", "ended_at": now,
                      "end_reason": ("refusee par %s" if answer == "refused" else "sans reponse de %s")
                      % att.get("device_name")}})
        if r.modified_count:
            on_not_taken(db, run_id, now)
    return True


def _fiche_category(db, device):
    F = _field()
    return F._device_category(db, device) or "PCO.Information"


def _open_fiche(db, run, device, now):
    """Fiche de main courante de la patrouille (ouverte a l'acceptation)."""
    F = _field()
    try:
        event, year = F.fiche_target_event(db, device, now)
    except Exception:
        event, year = device.get("event"), device.get("year")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        pass
    sc = run.get("scenario") or {}
    name = device.get("name") or "?"
    n = len(sc.get("points") or [])
    text = "Patrouille programmee : %s (%d point%s)" % (sc.get("name") or "?", n, "s" if n > 1 else "")
    if device.get("test_mode"):        # tablette d'essai (Field dispatch)
        text = "[TEST] " + text
    fid = str(uuid.uuid5(uuid.NAMESPACE_URL, "patrol-run|" + run["_id"]))
    first = (sc.get("points") or [{}])[0]
    cat = _fiche_category(db, device)
    doc = {
        "_id": fid, "event": event, "year": year, "ts": now,
        "timestamp_iso": now.astimezone(PARIS).isoformat(), "close_ts": None, "close_iso": None,
        "category": cat, "source": cat, "text": text, "text_full": text, "comment": "",
        "comment_history": [], "operator": BOT, "operator_id_create": "patrol:" + run["_id"],
        "operator_close": None, "operator_id_close": None, "status_code": 0, "severity": 0,
        "niveau_urgence": None, "is_incident": False, "area": None,
        "gps": {"type": "Point", "coordinates": [first["lng"], first["lat"]]} if first.get("lat") is not None else None,
        "group": None,
        "content_category": {"patrouille": name, "patrol_run": run["_id"],
                             "patrol_scenario": sc.get("id"), "test": bool(device.get("test_mode")) or None},
        "extracted": {"phones": None, "plates": None},
        "tags": ["patrouille", "test"] if device.get("test_mode") else ["patrouille"],
        "synced_at": None, "sql_id": None, "guid": None, "server": "COCKPIT", "bounce_rev": 1,
    }
    try:
        db["pcorg"].insert_one(doc)
    except Exception as e:  # cle deja la (acceptation rejouee) : on garde la fiche existante
        logger.debug("fiche patrouille %s : %s", fid, e)
    est = sc.get("estimate") or {}
    parts = []
    if est.get("distance_m"):
        parts.append("%.1f km" % (est["distance_m"] / 1000.0))
    if est.get("duration_s"):
        parts.append("%d min estimees" % round(est["duration_s"] / 60))
    pts_txt = " > ".join(p.get("name") or "?" for p in sc.get("points") or [])
    PH.append_entry(db["pcorg"], fid, PH.make_entry(
        "field:" + name, "Patrouille acceptee%s\nParcours : %s%s" % (
            (" (" + ", ".join(parts) + ")") if parts else "", pts_txt,
            " (boucle)" if sc.get("loop") else ""), origin="field", ts=now), inc_bounce=True)
    return fid, event, year


def accept(db, run_id, device, now=None):
    """La tablette accepte : patrouille en cours, fiche ouverte. (ok, code)"""
    now = now or _now()
    run = db["patrol_runs"].find_one({"_id": run_id})
    if not run:
        return False, "not_found"
    cur = run.get("current") or {}
    if run.get("state") != "proposed" or cur.get("device_id") != str(device["_id"]):
        if run.get("device_id") == str(device["_id"]) and run.get("state") in ("running", "paused"):
            return True, "deja_acceptee"
        return False, "proposition_expiree"
    exp = _aware(cur.get("expires_at"))
    if isinstance(exp, datetime) and now > exp + timedelta(seconds=ANSWER_GRACE_S):
        return False, "proposition_expiree"
    fresh = db["field_devices"].find_one({"_id": device["_id"]}) or device
    if (fresh.get("status") or "patrouille") != "patrouille":
        return False, "unite_occupee"
    r = db["patrol_runs"].update_one(
        {"_id": run_id, "state": "proposed", "current.device_id": str(fresh["_id"])},
        {"$set": {"state": "running", "current": None, "device_id": str(fresh["_id"]),
                  "device_name": fresh.get("name"), "started_at": now},
         "$push": {"attempts": dict(cur, answer="accepted", answered_at=now)}})
    if not r.modified_count:
        return False, "proposition_expiree"
    db["field_devices"].update_one(
        {"_id": fresh["_id"]},
        {"$set": {"active_patrol_run": run_id, "patrol_proposal": None}})
    fid, event, year = _open_fiche(db, run, fresh, now)
    db["patrol_runs"].update_one({"_id": run_id}, {"$set": {"fiche_id": fid, "event": event, "year": year}})
    return True, "ok"


def _fiche_entry(db, run, text, now, by=None):
    if not run.get("fiche_id"):
        return
    op = ("field:" + run["device_name"]) if (by is None and run.get("device_name")) else (by or BOT)
    try:
        PH.append_entry(db["pcorg"], run["fiche_id"],
                        PH.make_entry(op, text, origin="field" if op.startswith("field:") else "cockpit", ts=now,
                                      system=True if op == BOT else None),
                        inc_bounce=True, extra_filter={"status_code": {"$ne": 10}})
    except Exception as e:
        logger.warning("patrouille %s : entree de main courante perdue (%s)", run["_id"], e)


def _counts(steps):
    c = {"done": 0, "skipped": 0, "missed": 0, "pending": 0}
    for s in steps:
        c[s.get("state") or "pending"] = c.get(s.get("state") or "pending", 0) + 1
    return c


def step(db, run_id, device, point_id, action, method="manual", comment=None, photo=None,
         at=None, now=None):
    """Point fait (`done`, GPS ou bouton) ou passe (`skipped`, motif).
    Idempotent par (patrouille, point) : un renvoi de la file hors ligne ne
    change rien. Retourne (ok, code, run)."""
    now = now or _now()
    if action not in ("done", "skipped"):
        return False, "action_invalide", None
    run = db["patrol_runs"].find_one({"_id": run_id})
    if not run or run.get("device_id") != str(device["_id"]):
        return False, "not_assigned", None
    st = next((s for s in run.get("steps") or [] if s.get("point_id") == str(point_id)), None)
    if not st:
        return False, "point_inconnu", None
    if st.get("state") != "pending":
        return True, "deja_fait", run
    if run.get("state") not in ("running", "paused"):
        return False, "patrouille_terminee", run
    comment = (comment or "").strip()[:500] or None
    if action == "skipped" and not comment:
        return False, "motif_obligatoire", None
    pt = next((p for p in (run.get("scenario") or {}).get("points") or [] if p["point_id"] == str(point_id)), {})
    if action == "done" and pt.get("photo_required") and not photo:
        return False, "photo_obligatoire", None
    when = _aware(at) if isinstance(at, datetime) else now
    if when > now + timedelta(minutes=2) or when < now - timedelta(days=1):
        when = now
    r = db["patrol_runs"].update_one(
        {"_id": run_id, "steps": {"$elemMatch": {"point_id": str(point_id), "state": "pending"}}},
        {"$set": {"steps.$.state": action, "steps.$.at": when, "steps.$.method": method,
                  "steps.$.comment": comment, "steps.$.photo": photo}})
    if not r.modified_count:
        return True, "deja_fait", db["patrol_runs"].find_one({"_id": run_id})
    idx = next(i for i, s in enumerate(run["steps"]) if s.get("point_id") == str(point_id))
    total = len(run["steps"])
    if action == "done":
        how = {"gps": "passage GPS", "manual": "bouton"}.get(method, method)
        text = "Point %d/%d fait : %s (%s)" % (idx + 1, total, pt.get("name") or st.get("name"), how)
    else:
        text = "Point %d/%d passe : %s\nMotif : %s" % (idx + 1, total, pt.get("name") or st.get("name"), comment)
    if action == "done" and comment:
        text += "\n" + comment
    # Photo {photo, thumb} : meme forme que les commentaires photo de la tablette
    extra = {"photos": [photo], "photo": photo.get("photo"), "thumb": photo.get("thumb")} if photo else {}
    if run.get("fiche_id"):
        try:
            PH.append_entry(db["pcorg"], run["fiche_id"], PH.make_entry(
                "field:" + (run.get("device_name") or "?"), text, origin="field", ts=when, **extra),
                inc_bounce=True, extra_filter={"status_code": {"$ne": 10}})
        except Exception as e:
            logger.warning("patrouille %s : entree perdue (%s)", run_id, e)
    run = db["patrol_runs"].find_one({"_id": run_id})
    if not _counts(run["steps"])["pending"]:
        finish(db, run_id, device, now=now, reason=None)
        run = db["patrol_runs"].find_one({"_id": run_id})
    return True, "ok", run


def _bilan(run, now):
    c = _counts(run.get("steps") or [])
    total = len(run.get("steps") or [])
    started = _aware(run.get("started_at"))
    dur = round((now - started).total_seconds() / 60) if isinstance(started, datetime) else None
    paused_s = sum(int(p.get("duration_s") or 0) for p in run.get("pauses") or [])
    parts = ["%d/%d points faits" % (c["done"], total)]
    if c["skipped"]:
        parts.append("%d passe%s" % (c["skipped"], "s" if c["skipped"] > 1 else ""))
    if c["missed"]:
        parts.append("%d non fait%s" % (c["missed"], "s" if c["missed"] > 1 else ""))
    if dur is not None:
        parts.append("duree %d min" % dur + (" dont %d min de pause" % round(paused_s / 60) if paused_s >= 60 else ""))
    return ", ".join(parts), c


def finish(db, run_id, device=None, now=None, reason=None, abandon=False, by=None):
    """Fin de patrouille : points restants `missed`, etat done (tout fait) ou
    partial, `cancelled` si annulee par le PC Org (`by`). Fiche close avec le
    bilan, tablette liberee. Idempotent."""
    now = now or _now()
    run = db["patrol_runs"].find_one({"_id": run_id})
    if not run:
        return False, "not_found"
    if device is not None and run.get("device_id") != str(device["_id"]):
        return False, "not_assigned"
    if run.get("state") not in RUN_OPEN:
        return True, "deja_termine"
    if run["state"] == "proposed":
        cur = run.get("current") or {}
        r = db["patrol_runs"].update_one({"_id": run_id, "state": "proposed"},
                                         {"$set": {"state": "cancelled", "current": None, "ended_at": now,
                                                   "end_reason": reason or "annulee", "cancelled_by": by}})
        if r.modified_count and cur.get("device_id"):
            _release_proposal(db, cur["device_id"], run_id)
        return True, "ok"
    steps = run.get("steps") or []
    for s in steps:
        if s.get("state") == "pending":
            s["state"] = "missed"
    c = _counts(steps)
    state = "cancelled" if by else ("done" if c["done"] == len(steps) else "partial")
    pauses = run.get("pauses") or []
    if pauses and not pauses[-1].get("end"):
        start = _aware(pauses[-1].get("start"))
        pauses[-1]["end"] = now
        pauses[-1]["duration_s"] = int((now - start).total_seconds()) if isinstance(start, datetime) else 0
    r = db["patrol_runs"].update_one(
        {"_id": run_id, "state": {"$in": ["running", "paused"]}},
        {"$set": {"state": state, "steps": steps, "pauses": pauses, "ended_at": now,
                  "end_reason": (reason or "").strip()[:300] or None, "cancelled_by": by,
                  "abandoned": bool(abandon)}})
    if not r.modified_count:
        return True, "deja_termine"
    run = db["patrol_runs"].find_one({"_id": run_id})
    db["field_devices"].update_one({"_id": _oid(run.get("device_id")), "active_patrol_run": run_id},
                                   {"$set": {"active_patrol_run": None}})
    bilan, _ = _bilan(run, now)
    head = {"done": "Patrouille terminee", "partial": "Patrouille terminee incomplete",
            "cancelled": "Patrouille annulee"}[state]
    if abandon:
        head = "Patrouille abandonnee"
    text = "Statut: En cours -> Terminé\n%s : %s" % (head, bilan)
    if run.get("end_reason"):
        text += "\nMotif : " + run["end_reason"]
    op = by or ("field:" + (run.get("device_name") or "?"))
    if run.get("fiche_id"):
        PH.append_entry(
            db["pcorg"], run["fiche_id"],
            PH.make_entry(op, text, origin="cockpit" if by else "field", ts=now),
            set_fields={"status_code": 10, "close_ts": now, "close_iso": now.astimezone(PARIS).isoformat(),
                        "operator_close": op, "operator_id_close": "patrol:" + run_id, "cockpit_status_at": now},
            owned={"status"}, inc_bounce=True, extra_filter={"status_code": {"$ne": 10}})
    return True, "ok"


def sync_pause(db, run, device=None, now=None):
    """Aligne l'etat de la patrouille sur le statut de l'agent : engage sur une
    fiche (ou en pause) -> patrouille en pause ; de nouveau disponible ->
    reprise au prochain point non fait. Fiche de patrouille close par le PC
    Org -> patrouille annulee. Retourne le run a jour."""
    now = now or _now()
    if not run or run.get("state") not in ("running", "paused"):
        return run
    if run.get("fiche_id"):
        f = db["pcorg"].find_one({"_id": run["fiche_id"]}, {"status_code": 1, "operator_close": 1})
        if f and f.get("status_code") == 10:
            finish(db, run["_id"], now=now, reason="fiche close par le PC Org",
                   by=f.get("operator_close") or "PC Org")
            return db["patrol_runs"].find_one({"_id": run["_id"]})
    device = device or db["field_devices"].find_one({"_id": _oid(run.get("device_id"))})
    if not device or device.get("revoked"):
        finish(db, run["_id"], now=now, reason="tablette revoquee", by=BOT)
        return db["patrol_runs"].find_one({"_id": run["_id"]})
    status = device.get("status") or "patrouille"
    busy = status in BUSY_STATUSES
    if busy and run["state"] == "running":
        r = db["patrol_runs"].update_one(
            {"_id": run["_id"], "state": "running"},
            {"$set": {"state": "paused"}, "$push": {"pauses": {"start": now, "status": status,
                                                                 "fiche_id": device.get("active_fiche_id")}}})
        if r.modified_count:
            why = "agent en pause" if status == "pause" else "agent engage sur une intervention"
            _fiche_entry(db, run, "Patrouille en pause (%s)" % why, now, by=BOT)
    elif not busy and run["state"] == "paused":
        pauses = run.get("pauses") or []
        upd = {"state": "running"}
        if pauses and not pauses[-1].get("end"):
            start = _aware(pauses[-1].get("start"))
            upd["pauses.%d.end" % (len(pauses) - 1)] = now
            upd["pauses.%d.duration_s" % (len(pauses) - 1)] = (
                int((now - start).total_seconds()) if isinstance(start, datetime) else 0)
        r = db["patrol_runs"].update_one({"_id": run["_id"], "state": "paused"}, {"$set": upd})
        if r.modified_count:
            nxt = next((s for s in run.get("steps") or [] if s.get("state") == "pending"), None)
            _fiche_entry(db, run, "Patrouille reprise" + (" au point : " + nxt["name"] if nxt else ""), now, by=BOT)
    return db["patrol_runs"].find_one({"_id": run["_id"]})


# ---------------------------------------------------------------------------
# Planifications (livraison 2)
# ---------------------------------------------------------------------------
# Planning = format de la detection (detection_arming.py, REUTILISE tel quel,
# jamais copie) : semaines types peintes au quart d'heure, exceptions datees
# (une fois ou chaque annee) ou calees sur une epreuve (chaque annee), jours
# feries, forcage temporaire. Une plage active declenche un lancement a son
# debut (`span_start`) ou toutes les `every_min` minutes (`every`), dans la
# periode de validite facultative [valid_from, valid_until]. `slot_key`
# unique : jamais deux lancements pour le meme creneau, meme si deux
# executions du moteur se chevauchent.

LAUNCH_RULES = ("span_start", "every")
LAUNCH_GRACE_MIN = 10        # un creneau rate de plus de 10 min (moteur arrete) n'est pas rattrape
MAX_SPAN_LOOKBACK_Q = 7 * 96


def _clean_plan(db, data):
    import detection_arming as DAR
    name = str(data.get("name") or "").strip()[:80]
    if not name:
        return None, "nom_obligatoire"
    sc = db["patrol_scenarios"].find_one({"_id": _oid(data.get("scenario_id")), "archived": {"$ne": True}})
    if not sc:
        return None, "scenario_introuvable"
    t = data.get("target") or {}
    if t.get("kind") == "device" and t.get("device_id"):
        target = {"kind": "device", "device_id": str(t["device_id"])}
    elif t.get("kind") == "group" and t.get("beacon_group_id"):
        target = {"kind": "group", "beacon_group_id": str(t["beacon_group_id"])}
    else:
        return None, "cible_invalide"
    launch = data.get("launch") or {}
    rule = launch.get("rule") if launch.get("rule") in LAUNCH_RULES else "span_start"
    try:
        every = max(15, min(24 * 60, int(launch.get("every_min") or 120)))
    except (TypeError, ValueError):
        every = 120
    every = max(15, int(round(every / 15.0)) * 15)     # au quart d'heure, comme la grille
    arming = DAR.normalize(data.get("arming"))
    arming.pop("override", None)
    vf, vu = (str(data.get(k) or "").strip()[:10] or None for k in ("valid_from", "valid_until"))
    for v in (vf, vu):
        if v:
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                return None, "date_invalide"
    if vf and vu and vu < vf:
        return None, "date_invalide"
    return {"name": name, "scenario_id": str(sc["_id"]), "target": target, "enabled": bool(data.get("enabled", True)),
            "launch": {"rule": rule, "every_min": every}, "arming": arming,
            "valid_from": vf, "valid_until": vu, "note": str(data.get("note") or "").strip()[:500]}, None


def save_plan(db, data, by, plan_id=None):
    clean, err = _clean_plan(db, data or {})
    if err:
        return None, err
    now = _now()
    clean.update(updated_at=now, updated_by=by)
    if plan_id:
        oid = _oid(plan_id)
        if not oid:
            return None, "not_found"
        old = db["patrol_plans"].find_one({"_id": oid}, {"arming.override": 1})
        if not old:
            return None, "not_found"
        clean["arming"]["override"] = (old.get("arming") or {}).get("override")
        db["patrol_plans"].update_one({"_id": oid}, {"$set": clean})
        return db["patrol_plans"].find_one({"_id": oid}), None
    clean["arming"]["override"] = None
    clean.update(_id=ObjectId(), created_by=by, created_at=now)
    db["patrol_plans"].insert_one(clean)
    return clean, None


def set_plan_override(db, plan_id, state, hours, by):
    """Forcage temporaire : `on` (lancements comme si la plage etait active),
    `off` (suspendue), None = retour au planning."""
    oid = _oid(plan_id)
    ov = None
    if state in ("on", "off"):
        try:
            h = max(1, min(24 * 14, int(hours or 4)))
        except (TypeError, ValueError):
            h = 4
        ov = {"state": state, "until": _now() + timedelta(hours=h), "by": by}
    r = db["patrol_plans"].update_one({"_id": oid}, {"$set": {"arming.override": ov}})
    return bool(r.matched_count)


def _plan_group(plan):
    """Objet attendu par detection_arming (cle `arming`)."""
    return {"arming": plan.get("arming") or {}}


def _in_validity(plan, now):
    day = now.astimezone(PARIS).date().isoformat()
    if plan.get("valid_from") and day < plan["valid_from"]:
        return False
    if plan.get("valid_until") and day > plan["valid_until"]:
        return False
    return True


def plan_active(db, plan, now):
    import detection_arming as DAR
    if not plan.get("enabled") or not _in_validity(plan, now):
        return False
    return DAR.group_state(_plan_group(plan), now, db)[0]


def _span_start(db, plan, now):
    """Debut de la plage active en cours (quart d'heure pres). Mode
    "toujours" : minuit (heure de Paris), pour que `every` reste regulier."""
    import detection_arming as DAR
    g = _plan_group(plan)
    loc = now.astimezone(PARIS)
    t = loc.replace(minute=loc.minute - loc.minute % 15, second=0, microsecond=0).astimezone(timezone.utc)
    midnight = loc.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    for _ in range(MAX_SPAN_LOOKBACK_Q):
        prev = t - timedelta(minutes=15)
        if not DAR.group_state(g, prev, db)[0] or not _in_validity(plan, prev):
            return t
        if (plan.get("arming") or {}).get("mode") == "always" and t <= midnight:
            return midnight
        t = prev
    return t


def due_slot(db, plan, now):
    """Creneau a lancer maintenant : debut du creneau (UTC) ou None."""
    if not plan_active(db, plan, now):
        return None
    start = _span_start(db, plan, now)
    launch = plan.get("launch") or {}
    if launch.get("rule") == "every":
        step = timedelta(minutes=int(launch.get("every_min") or 120))
        k = int((now - start) / step)
        slot = start + k * step
    else:
        slot = start
    if now - slot > timedelta(minutes=LAUNCH_GRACE_MIN):
        return None
    return slot


def launch_plans(db, now=None):
    """Lance les creneaux dus. Une patrouille precedente du meme plan encore
    proposee ou en cours : le creneau est saute (trace dans `last_launch`)."""
    now = now or _now()
    n = 0
    for plan in list(db["patrol_plans"].find({"enabled": True})):
        try:
            slot = due_slot(db, plan, now)
        except Exception as e:
            logger.warning("planification %s : %s", plan.get("name"), e)
            continue
        if not slot:
            continue
        key = "%s|%s" % (plan["_id"], slot.strftime("%Y-%m-%dT%H:%M"))
        if (plan.get("last_launch") or {}).get("slot_key") == key:
            continue
        busy = db["patrol_runs"].find_one({"plan_id": str(plan["_id"]), "state": {"$in": list(RUN_OPEN)}}, {"_id": 1})
        if busy:
            result = "sautee : patrouille precedente encore en cours"
        else:
            try:
                run, code, _ = create_run(db, plan["scenario_id"], plan.get("target"), "Planification " + plan["name"],
                                          plan_id=str(plan["_id"]), slot_key=key, now=now)
            except Exception as e:
                if "duplicate key" in str(e).lower():     # autre execution du moteur, meme creneau
                    continue
                logger.warning("planification %s : lancement en echec (%s)", plan.get("name"), e)
                continue
            result = {"proposed": "proposee", "no_device": "aucune tablette disponible"}.get(code, code)
            n += 1
        db["patrol_plans"].update_one({"_id": plan["_id"]},
                                      {"$set": {"last_launch": {"slot_key": key, "at": now, "slot": slot, "result": result}}})
        logger.info("planification %s, creneau %s : %s", plan.get("name"), slot.isoformat(), result)
    return n


def next_launches(db, plan, start=None, limit=8, days=14):
    """Prochains lancements prevus [(instant UTC)] (apercu de la page), au
    quart d'heure, dans la periode de validite."""
    import detection_arming as DAR
    start = (start or _now()).replace(second=0, microsecond=0)
    if not plan.get("enabled"):
        return []
    g = _plan_group(plan)
    launch = plan.get("launch") or {}
    every = timedelta(minutes=int(launch.get("every_min") or 120))
    out = []
    loc = start.astimezone(PARIS)
    t = loc.replace(minute=loc.minute - loc.minute % 15).astimezone(timezone.utc)
    prev_on = DAR.group_state(g, t - timedelta(minutes=15), db)[0] and _in_validity(plan, t - timedelta(minutes=15))
    span_start = _span_start(db, plan, t) if prev_on else None
    end = start + timedelta(days=days)
    while t < end and len(out) < limit:
        on = DAR.group_state(g, t, db)[0] and _in_validity(plan, t)
        if on and not prev_on:
            span_start = t
            if t >= start:
                out.append(t)
        elif on and launch.get("rule") == "every" and span_start is not None:
            if (t - span_start) % every == timedelta(0) and t >= start and t != span_start:
                out.append(t)
        prev_on = on
        t += timedelta(minutes=15)
    return out


def pub_plan(db, plan, scenarios=None, now=None):
    import detection_arming as DAR
    now = now or _now()
    sc = (scenarios or {}).get(plan.get("scenario_id"))
    if sc is None:
        sc = db["patrol_scenarios"].find_one({"_id": _oid(plan.get("scenario_id"))}, {"name": 1, "color": 1})
    ll = plan.get("last_launch") or {}
    arming = DAR.normalize(plan.get("arming"))
    ov = (plan.get("arming") or {}).get("override") or None
    if ov and isinstance(ov.get("until"), datetime) and _aware(ov["until"]) <= now:
        ov = None
    arming["override"] = {"state": ov["state"], "until": _iso(ov["until"]), "by": ov.get("by")} if ov else None
    active = plan_active(db, plan, now)
    return {
        "id": str(plan["_id"]), "name": plan.get("name"), "scenario_id": plan.get("scenario_id"),
        "scenario_name": (sc or {}).get("name"), "scenario_color": (sc or {}).get("color"),
        "target": plan.get("target"), "enabled": bool(plan.get("enabled")), "launch": plan.get("launch"),
        "arming": arming, "valid_from": plan.get("valid_from"), "valid_until": plan.get("valid_until"),
        "note": plan.get("note") or "", "active_now": active,
        "explain": DAR.explain(_plan_group(plan), now, db) if plan.get("enabled") else "desactivee",
        "next": [_iso(t) for t in next_launches(db, plan, now, limit=5)],
        "last_launch": {"at": _iso(ll.get("at")), "slot": _iso(ll.get("slot")), "result": ll.get("result")} if ll else None,
        "updated_at": _iso(plan.get("updated_at")), "updated_by": plan.get("updated_by"),
    }


# ---------------------------------------------------------------------------
# Moteur (patrol_engine.py)
# ---------------------------------------------------------------------------

def tick(db, now=None):
    """Lancements planifies, propositions expirees -> tablette suivante,
    pauses et fiches closes. Retourne le nombre d'actions."""
    now = now or _now()
    n = launch_plans(db, now)
    for run in list(db["patrol_runs"].find(
            {"state": "proposed", "current.expires_at": {"$lt": now - timedelta(seconds=ANSWER_GRACE_S)}},
            {"current": 1}).limit(50)):
        if refuse(db, run["_id"], (run.get("current") or {}).get("device_id"), now=now, answer="timeout"):
            n += 1
    # Proposition bloquee entre deux etapes (arret du moteur) : on relance.
    for run in list(db["patrol_runs"].find(
            {"state": "proposed", "current": None, "created_at": {"$lt": now - timedelta(seconds=10)}},
            {"_id": 1}).limit(50)):
        if propose_next(db, run["_id"], now=now)[0] in ("proposed", "no_device", "exhausted"):
            n += 1
    for run in list(db["patrol_runs"].find({"state": {"$in": ["running", "paused"]}}).limit(200)):
        before = run.get("state")
        after = (sync_pause(db, run, now=now) or {}).get("state")
        if after != before:
            n += 1
    return n


# ---------------------------------------------------------------------------
# Vues
# ---------------------------------------------------------------------------

def _pub_steps(run):
    pts = {p["point_id"]: p for p in (run.get("scenario") or {}).get("points") or []}
    out = []
    for s in run.get("steps") or []:
        p = pts.get(s.get("point_id")) or {}
        out.append({"point_id": s.get("point_id"), "name": s.get("name"), "state": s.get("state"),
                    "at": _iso(s.get("at")), "method": s.get("method"), "comment": s.get("comment"),
                    "photo": s.get("photo"), "lat": p.get("lat"), "lng": p.get("lng"),
                    "radius_m": p.get("radius_m"), "instruction": p.get("instruction") or "",
                    "photo_required": bool(p.get("photo_required"))})
    return out


def pub_run(run, device=None):
    sc = run.get("scenario") or {}
    cur = run.get("current") or {}
    c = _counts(run.get("steps") or [])
    out = {
        "id": run["_id"], "state": run.get("state"), "plan_id": run.get("plan_id"),
        "scenario": {"id": sc.get("id"), "name": sc.get("name"), "travel": sc.get("travel"),
                     "loop": sc.get("loop"), "color": sc.get("color"), "dominante": sc.get("dominante"),
                     "estimate": sc.get("estimate") or {}},
        "target": run.get("target"), "device_id": run.get("device_id"), "device_name": run.get("device_name"),
        "current": {"device_id": cur.get("device_id"), "device_name": cur.get("device_name"),
                    "expires_at": _iso(cur.get("expires_at")), "distance_m": cur.get("distance_m")} if cur else None,
        "attempts": [{"device_name": a.get("device_name"), "answer": a.get("answer"), "reason": a.get("reason"),
                      "at": _iso(a.get("answered_at"))} for a in run.get("attempts") or []],
        "fiche_id": run.get("fiche_id"), "steps": _pub_steps(run), "counts": c,
        "created_at": _iso(run.get("created_at")), "created_by": run.get("created_by"),
        "started_at": _iso(run.get("started_at")), "ended_at": _iso(run.get("ended_at")),
        "end_reason": run.get("end_reason"), "abandoned": bool(run.get("abandoned")),
        "paused_s": sum(int(p.get("duration_s") or 0) for p in run.get("pauses") or []),
    }
    if device is not None:
        pos = device.get("last_position") or {}
        out["position"] = {"lat": pos.get("lat"), "lng": pos.get("lng"), "ts": _iso(pos.get("ts"))} if pos else None
        out["device_status"] = device.get("status") or "patrouille"
    return out


def tablet_view(db, device, now=None):
    """Bloc `patrol` et `patrol_proposal` de /field/my-fiches."""
    now = now or _now()
    prop = None
    pp = device.get("patrol_proposal") or {}
    if pp.get("run_id"):
        exp = _aware(pp.get("expires_at"))
        if isinstance(exp, datetime) and exp + timedelta(seconds=ANSWER_GRACE_S) >= now:
            run = db["patrol_runs"].find_one({"_id": pp["run_id"]})
            cur = (run or {}).get("current") or {}
            if run and run.get("state") == "proposed" and cur.get("device_id") == str(device["_id"]):
                sc = run.get("scenario") or {}
                est = sc.get("estimate") or {}
                prop = {"run_id": run["_id"], "name": sc.get("name"), "travel": sc.get("travel"),
                        "loop": bool(sc.get("loop")), "points": [p["name"] for p in sc.get("points") or []],
                        "distance_m": est.get("distance_m"), "duration_s": est.get("duration_s"),
                        "polyline": est.get("polyline"), "distance_to_start_m": cur.get("distance_m"),
                        "expires_at": _iso(exp), "remaining_s": max(0, round((exp - now).total_seconds()))}
    active = None
    if device.get("active_patrol_run"):
        run = db["patrol_runs"].find_one({"_id": device["active_patrol_run"]})
        run = sync_pause(db, run, device=device, now=now) if run else None
        if run and run.get("state") in ("running", "paused"):
            sc = run.get("scenario") or {}
            active = {"run_id": run["_id"], "name": sc.get("name"), "travel": sc.get("travel"),
                      "loop": bool(sc.get("loop")), "color": sc.get("color"), "state": run["state"],
                      "fiche_id": run.get("fiche_id"), "steps": _pub_steps(run),
                      "polyline": (sc.get("estimate") or {}).get("polyline"),
                      "started_at": _iso(run.get("started_at"))}
    return active, prop
