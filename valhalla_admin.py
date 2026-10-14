# valhalla_admin.py - Moteur d'itineraire : modes de calcul et administration
# du service valhalla-admin (saisons, epreuves, chargement des scenarios).
#
# Le service valhalla-admin (VM Valhalla, port 8003) stocke et valide les
# regles terrain (portails, voies ouvertes / interdites / fermees, sens,
# vitesses, piste) et les integre au graphe routier en "chargeant" un
# scenario = saison + epreuve facultative (~30 s d'indisponibilite de
# Valhalla pour TOUTES les applications, retour automatique au scenario
# precedent en cas d'echec). Cahier des charges : docs/claude/routing.md.
#
# Deux roles ici :
#   1. Les quatre modes de calcul (normal, normal_piste, god, god_piste) :
#      route_recipe(mode) rend la recette a fusionner dans toute requete
#      Valhalla (routing.py, balises_trace.py). Source : GET /profiles du
#      service, cache 10 min ; repli sur les recettes du cahier des charges
#      si le service ne repond pas (le calcul ne doit jamais en dependre).
#   2. Le relais /api/admin/itineraire/* vers le service : le navigateur n'a
#      ni la cle API (X-Api-Key) ni l'acces reseau (filtre sur l'IP de
#      Cockpit). Le relais ajoute X-Api-Key et X-Author (email Cockpit),
#      transmet If-Match / ETag tels quels et rend le statut du service
#      (401/403 = configuration, 409 = conflit de version ou chargement en
#      cours, 422 = message en francais du service, 428 = bug Cockpit).
#
# Page : /admin/itineraire (templates/itineraire_admin.html,
# static/js/itineraire_admin.js). Page d'administration accordable a un
# groupe (app.ADMIN_PAGE_REGISTRY, id "itineraire").

import copy
import logging
import math
import os
import re
import threading
import time
import unicodedata
from functools import wraps
from urllib.parse import urlsplit, urlunsplit

import requests
from flask import Blueprint, Response, jsonify, render_template, request

from field import admin_required

valhalla_admin_bp = Blueprint("valhalla_admin", __name__)
logger = logging.getLogger("valhalla_admin")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _default_admin_url():
    """Meme hote que VALHALLA_URL, port 8003."""
    base = os.getenv("VALHALLA_URL", "http://localhost:8002").rstrip("/")
    try:
        parts = urlsplit(base)
        host = parts.hostname or "localhost"
        return urlunsplit((parts.scheme or "http", "%s:8003" % host, "", "", ""))
    except ValueError:
        return "http://localhost:8003"


VALHALLA_ADMIN_URL = (os.getenv("VALHALLA_ADMIN_URL") or _default_admin_url()).rstrip("/")
VALHALLA_ADMIN_API_KEY = os.getenv("VALHALLA_ADMIN_API_KEY", "")
VALHALLA_ADMIN_TIMEOUT = int(os.getenv("VALHALLA_ADMIN_TIMEOUT_SECONDS", "15"))
# Les voies sans bbox pesent ~9 Mo : delai plus large pour /features
FEATURES_TIMEOUT = max(VALHALLA_ADMIN_TIMEOUT, 60)

PROFILES_TTL_S = 600
PROFILES_RETRY_S = 60
PROFILES_TIMEOUT_S = 2
SEARCH_CACHE_TTL_S = 3600


# ---------------------------------------------------------------------------
# Modes de calcul
# ---------------------------------------------------------------------------

ROUTE_MODES = ("normal", "normal_piste", "god", "god_piste")

# Recettes du cahier des charges (§ 3), repli si GET /profiles est
# indisponible. Les profils taxi / bus / motorcycle sont RESERVES a ce
# mecanisme : ils ne representent plus de vrais taxis, bus ou motos.
# ⚠️ Jamais ignore_access : il fait echouer les calculs et rouvre les
# passages "fermer" du scenario.
FALLBACK_RECIPES = {
    "normal": {"costing": "auto"},
    "normal_piste": {"costing": "taxi"},
    "god": {"costing": "bus", "costing_options": {
        "bus": {"ignore_oneways": True, "ignore_restrictions": True}}},
    "god_piste": {"costing": "motorcycle", "costing_options": {
        "motorcycle": {"ignore_oneways": True, "ignore_restrictions": True}}},
}

MODE_LABELS = {
    "normal": "Normal",
    "normal_piste": "Normal + piste",
    "god": "Intervention (god)",
    "god_piste": "Intervention + piste",
}


def _norm_mode_key(key):
    """'god + piste', 'god+piste', 'God-Piste', 'god_piste' -> 'god_piste'."""
    k = re.sub(r"[\s+\-]+", "_", str(key or "").strip().lower()).strip("_")
    return k if k in ROUTE_MODES else None


def mode_is_god(mode):
    return str(mode or "").startswith("god")


def mode_has_piste(mode):
    return str(mode or "").endswith("_piste")


def resolve_mode(data):
    """Mode d'une requete : `mode` explicite, sinon les booleens god / piste
    (forme historique des clients : modale fiche, bac a sable, tablette)."""
    data = data or {}
    m = _norm_mode_key(data.get("mode"))
    if m:
        return m
    god = bool(data.get("god"))
    piste = bool(data.get("piste"))
    return ("god" if god else "normal") + ("_piste" if piste else "")


def _clean_recipe(raw):
    """Garde costing (+ costing_options) d'une recette, None si invalide."""
    if not isinstance(raw, dict):
        return None
    for wrap in ("recipe", "request", "body", "params"):
        if isinstance(raw.get(wrap), dict) and "costing" not in raw:
            raw = raw[wrap]
    costing = raw.get("costing")
    if not isinstance(costing, str) or not costing:
        return None
    out = {"costing": costing}
    if isinstance(raw.get("costing_options"), dict):
        out["costing_options"] = raw["costing_options"]
    return out


def parse_profiles(js):
    """GET /profiles -> {mode: recette}. Tolere un dict par mode (eventuellement
    sous "profiles" / "modes") ou une liste d'objets portant id / mode / name.
    Les modes absents ou illisibles gardent la recette du cahier."""
    if isinstance(js, dict):
        for k in ("profiles", "modes"):
            if isinstance(js.get(k), (dict, list)):
                js = js[k]
                break
    items = []
    if isinstance(js, dict):
        items = list(js.items())
    elif isinstance(js, list):
        for it in js:
            if isinstance(it, dict):
                items.append((it.get("id") or it.get("mode") or it.get("name"), it))
    out = {}
    for key, val in items:
        m = _norm_mode_key(key)
        rec = _clean_recipe(val)
        if m and rec:
            out[m] = rec
    return out


_profiles = {"data": None, "ts": 0.0, "ok": False}
_profiles_lock = threading.Lock()


def _get_profiles():
    now = time.time()
    ttl = PROFILES_TTL_S if _profiles["ok"] else PROFILES_RETRY_S
    if _profiles["data"] is not None and now - _profiles["ts"] < ttl:
        return _profiles["data"]
    with _profiles_lock:
        if _profiles["data"] is not None and time.time() - _profiles["ts"] < ttl:
            return _profiles["data"]
        data, ok = dict(FALLBACK_RECIPES), False
        if VALHALLA_ADMIN_API_KEY:
            try:
                r = requests.get(VALHALLA_ADMIN_URL + "/profiles",
                                 headers={"X-Api-Key": VALHALLA_ADMIN_API_KEY},
                                 timeout=PROFILES_TIMEOUT_S)
                r.raise_for_status()
                parsed = parse_profiles(r.json())
                data.update(parsed)
                ok = bool(parsed)
            except (requests.RequestException, ValueError) as e:
                logger.info("valhalla_admin: /profiles indisponible, recettes du cahier: %s", e)
        _profiles.update(data=data, ts=time.time(), ok=ok)
        return data


def route_recipe(mode):
    """Recette (costing + costing_options) a fusionner dans une requete
    Valhalla (/route, /trace_attributes, /sources_to_targets...)."""
    m = _norm_mode_key(mode) or "normal"
    rec = _get_profiles().get(m) or FALLBACK_RECIPES[m]
    return copy.deepcopy(rec)


# ---------------------------------------------------------------------------
# Relais vers le service
# ---------------------------------------------------------------------------

LAYERS = ("seasons", "events")
OSM_KINDS = ("node", "way")
FEATURE_PARAMS = ("kind", "bbox", "highway", "season", "event", "only_rules")
_DOC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,119}$")


def _author():
    u = getattr(request, "admin_user", None) or {}
    return (u.get("email") or "cockpit").strip()[:120]


def admin_call(method, path, params=None, json_body=None, if_match=None,
               timeout=None, author=None):
    """Appel au service. Rend (status, corps, en-tetes) ; (502, ...) si le
    service est injoignable, (503, ...) si la cle API n'est pas posee."""
    if not VALHALLA_ADMIN_API_KEY:
        return 503, {"detail": "VALHALLA_ADMIN_API_KEY absente du serveur Cockpit"}, {}
    headers = {"X-Api-Key": VALHALLA_ADMIN_API_KEY}
    if method != "GET":
        headers["X-Author"] = author or _author()
    if if_match is not None and str(if_match) != "":
        headers["If-Match"] = str(if_match)
    try:
        r = requests.request(method, VALHALLA_ADMIN_URL + path, params=params,
                             json=json_body, headers=headers,
                             timeout=timeout or VALHALLA_ADMIN_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("valhalla_admin: %s %s injoignable: %s", method, path, e)
        return 502, {"detail": "service valhalla-admin injoignable"}, {}
    if r.status_code == 204 or not r.content:
        body = None
    else:
        try:
            body = r.json()
        except ValueError:
            body = {"detail": r.text[:500]}
    return r.status_code, body, r.headers


def _relay(method, path, params=None, json_body=None, timeout=None):
    status, body, headers = admin_call(
        method, path, params=params, json_body=json_body,
        if_match=request.headers.get("If-Match"), timeout=timeout)
    _notify_waze(method, path, status, body)
    if status == 204:
        return Response(status=204)
    resp = jsonify(body if body is not None else {})
    resp.status_code = status
    etag = headers.get("ETag") if headers else None
    if etag:
        resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-store"
    return resp


_DOC_PATH_RE = re.compile(r"^/(seasons|events)/([^/]+)(/rules/.*)?$")


def _notify_waze(method, path, status, body):
    """Fermetures publiees vers Waze (waze_feed.py) : apres une ecriture
    reussie d'une saison / epreuve, verifier que leurs troncons sont
    toujours fermes, sinon les retirer du flux."""
    if method == "GET":
        return
    m = _DOC_PATH_RE.match(path)
    if not m:
        return
    import waze_feed
    if status in (200, 201) and isinstance(body, dict) and "rules" in body:
        waze_feed.on_doc_written(m.group(1), m.group(2), body)
    elif status == 204 and method == "DELETE" and not m.group(3):
        waze_feed.on_doc_written(m.group(1), m.group(2), None)


def _check_layer(layer, doc_id=None):
    if layer not in LAYERS:
        return jsonify({"detail": "couche inconnue"}), 404
    if doc_id is not None and not _DOC_ID_RE.match(doc_id):
        return jsonify({"detail": "identifiant invalide"}), 404
    return None


def _role_required(role):
    """role_required d'app.py, importe a l'appel (import circulaire)."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


@valhalla_admin_bp.route("/admin/itineraire")
@_role_required("admin")
def itineraire_page():
    u = getattr(request, "user_payload", None) or {}
    return render_template("itineraire_admin.html", user=u,
                           mode_labels=MODE_LABELS,
                           configured=bool(VALHALLA_ADMIN_API_KEY))


@valhalla_admin_bp.route("/api/admin/itineraire/status", methods=["GET"])
@admin_required
def api_status():
    return _relay("GET", "/status")


@valhalla_admin_bp.route("/api/admin/itineraire/profiles", methods=["GET"])
@admin_required
def api_profiles():
    return _relay("GET", "/profiles")


@valhalla_admin_bp.route("/api/admin/itineraire/features", methods=["GET"])
@admin_required
def api_features():
    params = {k: request.args[k] for k in FEATURE_PARAMS if request.args.get(k) not in (None, "")}
    only_rules = str(params.get("only_rules", "")).lower() in ("1", "true", "yes")
    if params.get("kind") == "highways" and "bbox" not in params and not only_rules:
        # ~9 Mo sans bbox : la recherche passe par /search (cache serveur)
        return jsonify({"detail": "bbox obligatoire pour les voies"}), 422
    return _relay("GET", "/features", params=params, timeout=FEATURES_TIMEOUT)


@valhalla_admin_bp.route("/api/admin/itineraire/load", methods=["POST"])
@admin_required
def api_load():
    data = request.get_json(silent=True) or {}
    season_id = (data.get("season_id") or "").strip()
    event_id = (data.get("event_id") or "").strip() or None
    if not season_id:
        return jsonify({"detail": "saison obligatoire"}), 422
    logger.warning("valhalla_admin: chargement du scenario %s + %s par %s",
                   season_id, event_id or "aucune epreuve", _author())
    return _relay("POST", "/load", json_body={"season_id": season_id, "event_id": event_id})


@valhalla_admin_bp.route("/api/admin/itineraire/<layer>", methods=["GET", "POST"])
@admin_required
def api_layer(layer):
    err = _check_layer(layer)
    if err:
        return err
    if request.method == "GET":
        return _relay("GET", "/" + layer)
    return _relay("POST", "/" + layer, json_body=request.get_json(silent=True) or {})


@valhalla_admin_bp.route("/api/admin/itineraire/<layer>/<doc_id>", methods=["GET", "PATCH", "DELETE"])
@admin_required
def api_doc(layer, doc_id):
    err = _check_layer(layer, doc_id)
    if err:
        return err
    path = "/%s/%s" % (layer, doc_id)
    if request.method == "PATCH":
        return _relay("PATCH", path, json_body=request.get_json(silent=True) or {})
    return _relay(request.method, path)


@valhalla_admin_bp.route("/api/admin/itineraire/<layer>/<doc_id>/duplicate", methods=["POST"])
@admin_required
def api_duplicate(layer, doc_id):
    err = _check_layer(layer, doc_id)
    if err:
        return err
    return _relay("POST", "/%s/%s/duplicate" % (layer, doc_id),
                  json_body=request.get_json(silent=True) or {})


@valhalla_admin_bp.route("/api/admin/itineraire/<layer>/<doc_id>/rules/<kind>/<int:osm_id>",
                         methods=["PUT", "DELETE"])
@admin_required
def api_rule(layer, doc_id, kind, osm_id):
    err = _check_layer(layer, doc_id)
    if err:
        return err
    if kind not in OSM_KINDS:
        return jsonify({"detail": "type d'objet OSM inconnu"}), 404
    path = "/%s/%s/rules/%s/%d" % (layer, doc_id, kind, osm_id)
    if request.method == "PUT":
        return _relay("PUT", path, json_body=request.get_json(silent=True) or {})
    return _relay("DELETE", path)


@valhalla_admin_bp.route("/api/admin/itineraire/<layer>/<doc_id>/history", methods=["GET"])
@admin_required
def api_history(layer, doc_id):
    err = _check_layer(layer, doc_id)
    if err:
        return err
    return _relay("GET", "/%s/%s/history" % (layer, doc_id))


@valhalla_admin_bp.route("/api/admin/itineraire/<layer>/<doc_id>/history/<int:version>", methods=["GET"])
@admin_required
def api_history_version(layer, doc_id, version):
    err = _check_layer(layer, doc_id)
    if err:
        return err
    return _relay("GET", "/%s/%s/history/%d" % (layer, doc_id, version))


# ---------------------------------------------------------------------------
# Identifiant du noeud OSM d'un sommet de voie ("poser un portail ici")
# ---------------------------------------------------------------------------
# Le GeoJSON du service ne donne pas les identifiants des sommets (cahier
# § 9, GET /features/nearest a venir). En attendant : l'editeur envoie la
# voie et les coordonnees EXACTES du sommet clique (prises dans la geometrie
# du service), on lit la voie sur l'API OSM officielle et on garde le noeud
# le plus proche (5 m max). Correspondance par position et non par rang : la
# voie peut avoir gagne ou deplace des noeuds dans OSM depuis le PBF du
# moteur. Le service refuse (422) un noeud absent de ses donnees.

OSM_API = os.getenv("OSM_API_URL", "https://api.openstreetmap.org/api/0.6").rstrip("/")
OSM_UA = "Cockpit-ACO/1.0 (administration moteur d'itineraire)"
_way_nodes = {}          # way_id -> (ts, [(node_id, lon, lat), ...])
WAY_NODES_TTL_S = 3600
NODE_MATCH_M = 5.0       # rayon de rapprochement sommet du moteur / noeud OSM


def _dist_m(lat1, lon1, lat2, lon2):
    """Distance approchee (equirectangulaire), suffisante a quelques metres."""
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371000.0 * math.hypot(x, y)


def way_osm_nodes(way_id):
    hit = _way_nodes.get(way_id)
    if hit and time.time() - hit[0] < WAY_NODES_TTL_S:
        return hit[1]
    r = requests.get("%s/way/%d/full.json" % (OSM_API, way_id), timeout=10,
                     headers={"User-Agent": OSM_UA})
    r.raise_for_status()
    els = r.json().get("elements") or []
    coords = {e["id"]: (e.get("lon"), e.get("lat")) for e in els if e.get("type") == "node"}
    way = next((e for e in els if e.get("type") == "way"), {})
    nodes = [(nid,) + coords[nid] for nid in way.get("nodes") or [] if nid in coords]
    _way_nodes[way_id] = (time.time(), nodes)
    return nodes


@valhalla_admin_bp.route("/api/admin/itineraire/node-at", methods=["GET"])
@admin_required
def api_node_at():
    try:
        way_id = int(request.args.get("way", ""))
        lon = float(request.args.get("lon", ""))
        lat = float(request.args.get("lat", ""))
    except ValueError:
        return jsonify({"detail": "parametres way, lon, lat attendus"}), 422
    try:
        nodes = way_osm_nodes(way_id)
    except (requests.RequestException, ValueError) as e:
        logger.warning("valhalla_admin: API OSM indisponible pour way/%d: %s", way_id, e)
        return jsonify({"detail": "API OpenStreetMap indisponible, reessayer"}), 502
    # Noeud OSM le plus proche du sommet clique. Pas d'egalite stricte : une
    # voie retouchee dans OSM depuis le PBF du moteur a des points deplaces de
    # quelques metres (constate sur way/446967054). Un noeud absent des
    # donnees du moteur sera refuse par le service (422) a l'enregistrement.
    best, best_d = None, None
    for nid, nlon, nlat in nodes:
        if nlon is None:
            continue
        d = _dist_m(lat, lon, nlat, nlon)
        if best_d is None or d < best_d:
            best, best_d = nid, d
    if best is not None and best_d <= NODE_MATCH_M:
        return jsonify({"node": "node/%d" % best, "osm_id": best, "ecart_m": round(best_d, 1)})
    return jsonify({"detail": "Ce point a change dans OpenStreetMap depuis les donnees du moteur : "
                              "choisir un autre point de la voie"}), 404


# ---------------------------------------------------------------------------
# Recherche d'un objet par identifiant OSM ou par nom de voie
# ---------------------------------------------------------------------------
# Le service ne sait pas chercher : on garde en memoire, une heure, l'index
# (id, nom, type, centre) des barrieres, voies et piste de toute la zone.
# Les geometries ne sont pas gardees (voies ~9 Mo).

_search = {"items": None, "ts": 0.0}
_search_lock = threading.Lock()


def _feature_center(geom):
    coords = (geom or {}).get("coordinates")
    if not coords:
        return None
    t = (geom or {}).get("type")
    if t == "Point":
        return [coords[1], coords[0]]
    if t == "LineString" and len(coords):
        mid = coords[len(coords) // 2]
        return [mid[1], mid[0]]
    return None


def _feature_bounds(geom):
    coords = (geom or {}).get("coordinates")
    if (geom or {}).get("type") != "LineString" or not coords:
        return None
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


def _search_index():
    now = time.time()
    if _search["items"] is not None and now - _search["ts"] < SEARCH_CACHE_TTL_S:
        return _search["items"]
    with _search_lock:
        if _search["items"] is not None and time.time() - _search["ts"] < SEARCH_CACHE_TTL_S:
            return _search["items"]
        items, seen = [], set()
        for kind in ("barriers", "raceways", "highways"):
            status, body, _ = admin_call("GET", "/features", params={"kind": kind},
                                         timeout=FEATURES_TIMEOUT)
            if status != 200 or not isinstance(body, dict):
                return None
            for f in body.get("features") or []:
                p = f.get("properties") or {}
                tags = p.get("osm_tags") or {}
                fid = f.get("id") or "%s/%s" % (p.get("osm_type"), p.get("osm_id"))
                # La piste figure aussi dans la couche des voies : une seule fois
                if fid in seen:
                    continue
                seen.add(fid)
                items.append({
                    "id": fid,
                    "name": tags.get("name") or tags.get("ref") or "",
                    "type": tags.get("barrier") or tags.get("highway") or kind,
                    "layer": kind,
                    "center": _feature_center(f.get("geometry")),
                    "bounds": _feature_bounds(f.get("geometry")),
                })
        _search.update(items=items, ts=time.time())
        return items


def _fold(s):
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    return s.lower().strip()


@valhalla_admin_bp.route("/api/admin/itineraire/search", methods=["GET"])
@admin_required
def api_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"items": []})
    items = _search_index()
    if items is None:
        return jsonify({"detail": "recherche indisponible (service valhalla-admin)"}), 502
    m = re.match(r"^(?:(node|way)\s*/\s*)?(\d{3,})$", q.lower())
    if m:
        kind, num = m.group(1), m.group(2)
        found = [it for it in items if it["id"].endswith("/" + num)
                 and (not kind or it["id"].startswith(kind + "/"))]
        # Un point ordinaire d'une voie n'est pas dans les couches : la regle
        # reste possible par son identifiant (l'editeur le propose)
        return jsonify({"items": found[:50], "osm_ref": "%s/%s" % (kind, num) if kind else None})
    fq = _fold(q)
    found = [it for it in items if it["name"] and fq in _fold(it["name"])]
    # Une meme rue = plusieurs troncons : on les garde tous (regroupes cote JS)
    found.sort(key=lambda it: (_fold(it["name"]) != fq, it["name"]))
    return jsonify({"items": found[:200]})
