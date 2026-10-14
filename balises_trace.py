# balises_trace.py - Trace des balises recalee sur les voies (map matching Valhalla)
#
# Affichage UNIQUEMENT : les positions brutes (anoloc_positions) ne sont
# jamais modifiees ; alertes, zones et historique restent sur le brut.
#
# Principe : la trace est decoupee en trajets (coupure sur silence), chaque
# trajet est envoye a Valhalla /trace_attributes (map matching sur toute la
# suite de points, horodatee), puis chaque paire de points consecutifs est :
#   - "sure" (m=True)  : les deux points sont recales sur la voirie et le
#     chemin entre eux est plausible -> trace qui epouse les voies, trait plein
#   - "devinee" (m=False) : point hors voirie connue, rupture du recalage,
#     detour invente, Valhalla injoignable -> trait droit, affiche en pointilles
#
# Trajets termines mis en cache memoire (ils ne changent plus), seul le trajet
# en cours est recalcule a chaque rafraichissement.

import hashlib
import logging
import threading
import time
from collections import OrderedDict

import requests

from routing import VALHALLA_URL, VALHALLA_TIMEOUT, _decode_polyline6, _haversine_m
from valhalla_admin import route_recipe

logger = logging.getLogger("balises_trace")

TRIP_GAP_S = 300            # silence au-dela duquel on coupe le trajet
JITTER_M = 8                # points plus proches ignores pour le recalage (arret)
CHUNK_POINTS = 400          # points max par appel Valhalla
SEARCH_RADIUS_M = 40        # rayon de recherche d'une voie autour d'un point
ON_ROAD_MAX_M = 25          # au-dela, le point est considere hors voirie
DETOUR_FACTOR = 2.5         # chemin recale > 2.5 x la ligne droite...
DETOUR_EXTRA_M = 250        # ... et > ligne droite + 250 m : detour invente
MAX_SPEED_MS = 70           # 250 km/h : au-dela, chemin recale impossible
BACKOFF_S = 60              # pause des appels apres une panne Valhalla
CACHE_MAX = 300

_cache = OrderedDict()
_cache_lock = threading.Lock()
_down_until = 0.0


def _cache_get(key):
    with _cache_lock:
        val = _cache.get(key)
        if val is not None:
            _cache.move_to_end(key)
        return val


def _cache_put(key, val):
    with _cache_lock:
        _cache[key] = val
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def _dist(a, b):
    return _haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])


def split_trips(points):
    """Decoupe la suite de points (triee) en trajets sur silence > TRIP_GAP_S
    et retire le bruit d'arret (points a moins de JITTER_M du precedent garde,
    le dernier point d'un trajet est toujours garde)."""
    trips, cur = [], []
    for p in points:
        if cur and (p["t"] - cur[-1]["t"]) > TRIP_GAP_S:
            trips.append(cur)
            cur = []
        if cur and _dist(cur[-1], p) < JITTER_M:
            cur[-1] = dict(cur[-1], t_end=p["t"])
            continue
        cur.append(p)
    if cur:
        trips.append(cur)
    return trips


def _valhalla_match(chunk):
    """Appel /trace_attributes. Rend la reponse JSON ou leve une exception."""
    shape, last_t = [], None
    for p in chunk:
        t = int(p["t"])
        if last_t is not None and t <= last_t:
            t = last_t + 1
        last_t = t
        shape.append({"lat": p["lat"], "lon": p["lng"], "time": t})
    payload = {
        "shape": shape,
        "shape_match": "map_snap",
        "use_timestamps": True,
        "trace_options": {"search_radius": SEARCH_RADIUS_M, "gps_accuracy": 10},
        "filters": {"attributes": [
            "edge.length", "edge.begin_shape_index", "edge.end_shape_index", "shape",
            "matched.point", "matched.type", "matched.edge_index",
            "matched.distance_along_edge", "matched.distance_from_trace_point",
        ], "action": "include"},
    }
    # Vehicules d'intervention : mode god + piste (sens uniques et passages
    # interdits ignores, piste dans les deux sens), sinon le recalage invente
    # des detours, notamment quand la balise roule sur le circuit
    payload.update(route_recipe("god_piste"))
    r = requests.post(VALHALLA_URL + "/trace_attributes", json=payload, timeout=VALHALLA_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _guess_pairs(chunk):
    return [(False, [[a["lat"], a["lng"]], [b["lat"], b["lng"]]]) for a, b in zip(chunk, chunk[1:])]


def pairs_from_match(chunk, res):
    """Transforme la reponse Valhalla en une liste (sure, coords) par paire
    de points consecutifs du chunk. Pure : testable sans Valhalla."""
    shape = _decode_polyline6(res.get("shape") or "")
    edges = res.get("edges") or []
    mps = res.get("matched_points") or []
    if len(mps) != len(chunk) or len(shape) < 2 or not edges:
        return _guess_pairs(chunk)

    cum = [0.0]
    for (la1, lo1), (la2, lo2) in zip(shape, shape[1:]):
        cum.append(cum[-1] + _haversine_m(la1, lo1, la2, lo2))

    def locate(mp):
        """Abscisse curviligne du point recale sur la forme, ou None."""
        if mp.get("type") not in ("matched", "interpolated"):
            return None
        if (mp.get("distance_from_trace_point") or 0) > ON_ROAD_MAX_M:
            return None
        ei = mp.get("edge_index")
        if not isinstance(ei, int) or ei < 0 or ei >= len(edges):
            return None
        e = edges[ei]
        b, en = e.get("begin_shape_index", 0), e.get("end_shape_index", 0)
        if not (0 <= b <= en < len(shape)):
            return None
        # le 1er / dernier arc est tronque : distance_along_edge porte sur l'arc entier
        src = e.get("source_percent_along", 0.0) or 0.0
        tgt = e.get("target_percent_along", 1.0) or 1.0
        frac = mp.get("distance_along_edge") or 0.0
        frac = (frac - src) / (tgt - src) if tgt > src else 0.0
        frac = min(1.0, max(0.0, frac))
        return ei, cum[b] + frac * (cum[en] - cum[b])

    def contiguous(e1, e2):
        for k in range(e1, e2):
            if edges[k].get("end_shape_index") != edges[k + 1].get("begin_shape_index"):
                return False
        return True

    def slice_shape(s1, s2):
        out = [_point_at(shape, cum, s1)]
        for i, c in enumerate(cum):
            if s1 < c < s2:
                out.append(list(shape[i]))
        out.append(_point_at(shape, cum, s2))
        return out

    locs = [locate(mp) for mp in mps]
    disp = []
    for p, mp, loc in zip(chunk, mps, locs):
        if loc is not None:
            disp.append([mp["lat"], mp["lon"]])
        else:
            disp.append([p["lat"], p["lng"]])

    pairs = []
    for i in range(len(chunk) - 1):
        la, lb = locs[i], locs[i + 1]
        ok = False
        if la is not None and lb is not None and contiguous(la[0], lb[0]):
            along = lb[1] - la[1]
            straight = _dist(chunk[i], chunk[i + 1])
            dt = max(1.0, chunk[i + 1]["t"] - chunk[i].get("t_end", chunk[i]["t"]))
            ok = (along >= -5
                  and along <= max(straight * DETOUR_FACTOR, straight + DETOUR_EXTRA_M)
                  and along / dt <= MAX_SPEED_MS)
        if ok:
            coords = slice_shape(la[1], lb[1]) if lb[1] > la[1] else [disp[i], disp[i + 1]]
            pairs.append((True, coords))
        else:
            pairs.append((False, [disp[i], disp[i + 1]]))
    return pairs


def _point_at(shape, cum, s):
    if s <= 0:
        return list(shape[0])
    if s >= cum[-1]:
        return list(shape[-1])
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    seg = cum[hi] - cum[lo]
    f = (s - cum[lo]) / seg if seg > 0 else 0.0
    (a1, o1), (a2, o2) = shape[lo], shape[hi]
    return [a1 + (a2 - a1) * f, o1 + (o2 - o1) * f]


def _chunk_key(chunk):
    h = hashlib.sha1()
    for p in chunk:
        h.update(("%d|%.6f|%.6f;" % (p["t"], p["lat"], p["lng"])).encode())
    return h.hexdigest()


def _match_chunk(chunk, state):
    """(paires, statut) d'un chunk : statut 'ok' | 'cache' | 'off'."""
    global _down_until
    key = _chunk_key(chunk)
    hit = _cache_get(key)
    if hit is not None:
        return hit, "cache"
    if state["down"] or time.time() < _down_until:
        state["down"] = True
        return _guess_pairs(chunk), "off"
    try:
        pairs = pairs_from_match(chunk, _valhalla_match(chunk))
    except requests.HTTPError as e:
        # 400 = trace impossible a recaler (hors carte...) : reponse definitive
        if e.response is not None and e.response.status_code == 400:
            pairs = _guess_pairs(chunk)
            _cache_put(key, pairs)
            return pairs, "ok"
        logger.warning("trace_attributes HTTP %s", e)
        _down_until = time.time() + BACKOFF_S
        state["down"] = True
        return _guess_pairs(chunk), "off"
    except Exception as e:
        logger.warning("trace_attributes injoignable : %s", e)
        _down_until = time.time() + BACKOFF_S
        state["down"] = True
        return _guess_pairs(chunk), "off"
    _cache_put(key, pairs)
    return pairs, "ok"


def _merge(segments, sure, coords):
    coords = [[round(c[0], 6), round(c[1], 6)] for c in coords]
    if not segments:
        segments.append({"m": sure, "c": coords})
        return
    # la trace reste continue : chaque morceau repart du point ou finit le precedent
    last = segments[-1]
    if last["m"] == sure:
        last["c"].extend(coords[1:])
    else:
        segments.append({"m": sure, "c": [last["c"][-1]] + coords[1:]})


def build_segments(points, match=True):
    """points : [{lat, lng, t (epoch s)}] tries. Rend (segments, statut) ;
    segments = [{"m": bool, "c": [[lat, lng], ...]}] contigus, statut
    'ok' (recale), 'off' (Valhalla indisponible : tout devine) ou 'none'."""
    if len(points) < 2:
        return [], "none"
    trips = split_trips(points)
    segments, state, statuses = [], {"down": False}, set()
    prev_end = None
    for trip in trips:
        if prev_end is not None:
            _merge(segments, False, [[prev_end["lat"], prev_end["lng"]], [trip[0]["lat"], trip[0]["lng"]]])
        if len(trip) >= 2:
            i = 0
            while i < len(trip) - 1:
                chunk = trip[i:i + CHUNK_POINTS]
                if match:
                    pairs, st = _match_chunk(chunk, state)
                    statuses.add(st)
                else:
                    pairs = _guess_pairs(chunk)
                for sure, coords in pairs:
                    _merge(segments, sure, coords)
                i += CHUNK_POINTS - 1
        prev_end = trip[-1]
    if not match:
        return segments, "none"
    return segments, ("off" if "off" in statuses else "ok")
