# waze_feed.py - Publication des fermetures du moteur d'itineraire vers Waze
# (Waze for Cities, "Partner feed" au format CIFS).
#
# Waze n'a pas d'API d'envoi : il RELIT toutes les quelques minutes un flux
# heberge par le partenaire. Cockpit sert donc :
#   GET /public/waze/closures.xml  (public, Basic auth WAZE_FEED_USER /
#                                   WAZE_FEED_PASSWORD, declare dans le
#                                   Partner Hub > Toolbox > Partner feed)
#
# Les fermetures publiees vivent dans Mongo `waze_closures`, INDEPENDANTES du
# scenario charge dans Valhalla (recharger Valhalla ne rouvre rien chez
# Waze). Chaque fermeture reference des regles d'une saison / epreuve
# (rule_keys) ; si ces regles n'existent plus ou ne ferment plus la voie,
# la fermeture est "desynchronisee" et retiree du flux (on_doc_written,
# appele par le relais valhalla_admin apres chaque ecriture).
#
# Regles Waze (developers.google.com/waze/data-feed, cifsv2.xsd) :
#   - id stable pendant toute la vie de la fermeture (uid + "-" + plage) ;
#   - starttime NON modifiable une fois la fermeture commencee ;
#   - toujours un endtime (sinon 14 jours) ; rouvrir = ramener endtime a
#     maintenant et laisser la fermeture 24 h dans le flux ;
#   - polyline "lat lon" 6 decimales, >= 30 m, <= 20 km, pas circulaire,
#     une seule rue ; ONE_DIRECTION ferme le sens de parcours de la polyline ;
#   - heures locales avec decalage (Europe/Paris, +01:00 / +02:00 selon la
#     date : jamais de decalage en dur).
#
# Doc : docs/claude/waze.md

import hmac
import logging
import math
import os
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, Response, jsonify, request

from field import admin_required, _get_mongo_db

waze_feed_bp = Blueprint("waze_feed", __name__)
logger = logging.getLogger("waze_feed")

PARIS = ZoneInfo("Europe/Paris")
COL = "waze_closures"
COL_CFG = "waze_feed_config"

WAZE_FEED_USER = os.getenv("WAZE_FEED_USER", "")
WAZE_FEED_PASSWORD = os.getenv("WAZE_FEED_PASSWORD", "")
WAZE_GEOCODE_TOKEN = os.getenv("WAZE_GEOCODE_TOKEN", "")
WAZE_GEOCODE_URL = os.getenv("WAZE_GEOCODE_URL",
                             "https://www.waze.com/row-partnerhub-api/waze-map/streetsInfo")
FEED_PATH = "/public/waze/closures.xml"

MIN_LEN_M = 30
MAX_LEN_M = 20000
DESC_MAX = 40
STREET_MAX = 120
MAX_SLOTS = 50
KEEP_AFTER_END = timedelta(hours=24)
RULE_KEY_RE = re.compile(r"^way/\d+$")


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _utc(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def parse_local(value):
    """'2027-06-09T14:00' (heure de Paris, sans decalage) -> datetime UTC."""
    try:
        dt = datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PARIS)
    return dt.astimezone(timezone.utc)


def fmt_local(dt):
    """datetime -> '2027-06-09T14:00:00+02:00' (Europe/Paris, decalage du jour)."""
    return _utc(dt).astimezone(PARIS).isoformat(timespec="seconds")


def fmt_input(dt):
    """datetime -> '2027-06-09T14:00' pour un champ datetime-local."""
    return _utc(dt).astimezone(PARIS).strftime("%Y-%m-%dT%H:%M")


def _haversine_m(a, b):
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def polyline_length_m(pts):
    return sum(_haversine_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def rule_closes_for_waze(rule):
    """Une regle ferme-t-elle la voie aux conducteurs Waze ? fermer, interdire
    aux VL, ou route devenue piste. Les interdictions pietons / velos seules,
    les sens uniques et les ouvertures ne concernent pas Waze (v1)."""
    if not isinstance(rule, dict):
        return False
    if rule.get("access") == "fermer":
        return True
    if rule.get("access") == "interdire" and "vl" in (rule.get("interdire_pour") or []):
        return True
    return rule.get("piste") is True


# ---------------------------------------------------------------------------
# Validation d'une fermeture
# ---------------------------------------------------------------------------

def clean_closure(data, existing=None):
    """Valide un corps de creation / modification. Rend (champs, erreur)."""
    data = data or {}
    out = {}
    now = _now()

    if existing is None or "polyline" in data:
        pts = []
        for p in data.get("polyline") or []:
            try:
                lat, lon = float(p[0]), float(p[1])
            except (TypeError, ValueError, IndexError):
                return None, "polyline invalide"
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return None, "polyline invalide"
            if not pts or (round(lat, 6), round(lon, 6)) != (round(pts[-1][0], 6), round(pts[-1][1], 6)):
                pts.append([lat, lon])
        if len(pts) < 2:
            return None, "Il faut au moins deux points"
        if (round(pts[0][0], 6), round(pts[0][1], 6)) == (round(pts[-1][0], 6), round(pts[-1][1], 6)):
            return None, "Fermeture en boucle refusee par Waze : selectionner une portion qui a un debut et une fin"
        length = polyline_length_m(pts)
        if length < MIN_LEN_M:
            return None, "Fermeture trop courte pour Waze (%d m, minimum %d m)" % (length, MIN_LEN_M)
        if length > MAX_LEN_M:
            return None, "Fermeture trop longue pour Waze (%.1f km, maximum 20 km) : la decouper" % (length / 1000)
        out["polyline"] = pts
        out["length_m"] = int(length)

    if existing is None or "rule_keys" in data:
        keys = [k for k in (data.get("rule_keys") or []) if isinstance(k, str) and RULE_KEY_RE.match(k)]
        if not keys:
            return None, "Aucun troncon"
        out["rule_keys"] = keys

    if existing is None or "layer" in data or "doc_id" in data:
        layer = data.get("layer")
        doc_id = str(data.get("doc_id") or "").strip()
        if layer not in ("seasons", "events") or not doc_id:
            return None, "Document (saison / epreuve) manquant"
        out["layer"], out["doc_id"] = layer, doc_id
        out["doc_name"] = str(data.get("doc_name") or doc_id)[:120]

    if existing is None or "street" in data:
        street = str(data.get("street") or "").strip()
        if not street:
            return None, "Nom de rue obligatoire (Waze rapproche la fermeture par le nom)"
        out["street"] = street[:STREET_MAX]

    if existing is None or "description" in data:
        desc = str(data.get("description") or "").strip()
        if not desc:
            return None, "Message aux conducteurs obligatoire"
        if len(desc) > DESC_MAX:
            return None, "Message aux conducteurs : %d caracteres maximum" % DESC_MAX
        out["description"] = desc

    if existing is None or "direction" in data:
        direction = data.get("direction") or "BOTH_DIRECTIONS"
        if direction not in ("BOTH_DIRECTIONS", "ONE_DIRECTION"):
            return None, "Sens invalide"
        out["direction"] = direction

    if existing is None or "slots" in data:
        old = {s["sid"]: s for s in (existing or {}).get("slots") or []}
        slots, used = [], set()
        raw = data.get("slots") or []
        if not raw:
            return None, "Au moins une plage horaire"
        if len(raw) > MAX_SLOTS:
            return None, "%d plages maximum" % MAX_SLOTS
        for s in raw:
            start, end = parse_local(s.get("start")), parse_local(s.get("end"))
            if not start or not end:
                return None, "Plage horaire incomplete"
            if end <= start:
                return None, "Une plage finit avant de commencer"
            sid = s.get("sid") if s.get("sid") in old else None
            if sid:
                prev = old[sid]
                # Waze : debut non modifiable une fois la fermeture commencee
                if _utc(prev["start"]) <= now and abs((start - _utc(prev["start"])).total_seconds()) >= 60:
                    return None, "Le debut d'une plage deja commencee ne peut plus changer (regle Waze)"
                if prev.get("ended_early_at"):
                    end = min(end, _utc(prev["ended_early_at"]))
            else:
                if end <= now:
                    return None, "Une nouvelle plage est deja terminee"
                n = 1
                existing_sids = set(old) | used
                while "s%d" % n in existing_sids:
                    n += 1
                sid = "s%d" % n
            used.add(sid)
            slots.append({"sid": sid, "start": start, "end": end,
                          "ended_early_at": old.get(sid, {}).get("ended_early_at")})
        # Une plage commencee ne disparait pas en silence : on la termine
        for sid, prev in old.items():
            if sid not in used and _utc(prev["start"]) <= now < _utc(prev["end"]):
                return None, "Une plage en cours ne se supprime pas : utiliser Rouvrir maintenant"
            if sid not in used and _utc(prev["end"]) <= now:
                slots.append(prev)      # historique : reste 24 h dans le flux
        slots.sort(key=lambda s: _utc(s["start"]))
        out["slots"] = slots

    return out, None


# ---------------------------------------------------------------------------
# Statuts et flux
# ---------------------------------------------------------------------------

def slot_end(s):
    return _utc(s.get("ended_early_at")) or _utc(s["end"])


def closure_status(c, now=None):
    now = now or _now()
    if c.get("sync_ok") is False:
        return "desynchronisee"
    if not c.get("publish"):
        return "brouillon"
    slots = c.get("slots") or []
    if any(_utc(s["start"]) <= now < slot_end(s) for s in slots):
        return "en_cours"
    if any(_utc(s["start"]) > now for s in slots):
        return "programmee"
    return "terminee"


def feed_enabled(db):
    cfg = db[COL_CFG].find_one({"_id": "global"}) or {}
    return bool(cfg.get("enabled"))


def build_feed_xml(closures, enabled=True, now=None):
    """Flux CIFS (cifsv2.xsd). Une <incident> par plage horaire, id stable."""
    now = now or _now()
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<incidents xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
             'xsi:noNamespaceSchemaLocation="http://www.gstatic.com/road-incidents/cifsv2.xsd" '
             'timestamp="%s">' % fmt_local(now)]
    if enabled:
        for c in closures:
            if not c.get("publish") or c.get("sync_ok") is False:
                continue
            pts = c.get("polyline") or []
            poly = " ".join("%.6f %.6f" % (p[0], p[1]) for p in pts)
            for s in c.get("slots") or []:
                end = slot_end(s)
                if end <= now - KEEP_AFTER_END:
                    continue
                lines.append('  <incident id="%s">' % escape("%s-%s" % (c["uid"], s["sid"]), {'"': "&quot;"}))
                lines.append('    <parent_event id="%s"/>' % escape("aco-%s" % c.get("doc_id", ""), {'"': "&quot;"}))
                if c.get("created_at"):
                    lines.append("    <creationtime>%s</creationtime>" % fmt_local(c["created_at"]))
                if c.get("updated_at"):
                    lines.append("    <updatetime>%s</updatetime>" % fmt_local(c["updated_at"]))
                lines.append("    <type>ROAD_CLOSED</type>")
                lines.append("    <subtype>ROAD_CLOSED_EVENT</subtype>")
                lines.append("    <street>%s</street>" % escape(c.get("street", "")))
                lines.append("    <polyline>%s</polyline>" % poly)
                lines.append("    <direction>%s</direction>" % c.get("direction", "BOTH_DIRECTIONS"))
                lines.append("    <starttime>%s</starttime>" % fmt_local(s["start"]))
                lines.append("    <endtime>%s</endtime>" % fmt_local(end))
                lines.append("    <description>%s</description>" % escape(c.get("description", "")))
                lines.append("  </incident>")
    lines.append("</incidents>")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Synchronisation avec les regles (appelee par valhalla_admin._relay)
# ---------------------------------------------------------------------------

def on_doc_written(layer, doc_id, doc):
    """Apres une ecriture reussie d'une saison / epreuve : une fermeture dont
    un troncon n'est plus ferme (ou dont le document a disparu) est
    desynchronisee, donc retiree du flux ; elle se resynchronise seule si la
    regle revient."""
    try:
        db = _get_mongo_db()
        rules = (doc or {}).get("rules") if doc is not None else None
        for c in db[COL].find({"layer": layer, "doc_id": doc_id}):
            ok = rules is not None and all(rule_closes_for_waze(rules.get(k)) for k in c.get("rule_keys") or [])
            if ok != (c.get("sync_ok") is not False):
                db[COL].update_one({"_id": c["_id"]}, {"$set": {"sync_ok": ok, "sync_checked_at": _now()}})
                logger.warning("waze_feed: fermeture %s (%s) %s", c.get("uid"), c.get("street"),
                               "resynchronisee" if ok else "DESYNCHRONISEE, retiree du flux")
    except Exception as e:      # jamais bloquer l'ecriture de la regle
        logger.warning("waze_feed: synchronisation impossible pour %s/%s: %s", layer, doc_id, e)


def resync_all():
    """Relit les documents de toutes les fermetures (bouton / ouverture de l'onglet)."""
    import valhalla_admin as VA
    db = _get_mongo_db()
    seen = set()
    for c in db[COL].find({}, {"layer": 1, "doc_id": 1}):
        key = (c.get("layer"), c.get("doc_id"))
        if key in seen:
            continue
        seen.add(key)
        status, body, _ = VA.admin_call("GET", "/%s/%s" % key)
        if status == 200:
            on_doc_written(key[0], key[1], body)
        elif status == 404:
            on_doc_written(key[0], key[1], None)


# ---------------------------------------------------------------------------
# Flux public
# ---------------------------------------------------------------------------

def _basic_ok():
    auth = request.authorization
    if not auth or not WAZE_FEED_USER or not WAZE_FEED_PASSWORD:
        return False
    return (hmac.compare_digest(str(auth.username or ""), WAZE_FEED_USER)
            and hmac.compare_digest(str(auth.password or ""), WAZE_FEED_PASSWORD))


@waze_feed_bp.route(FEED_PATH, methods=["GET"])
def public_feed():
    if not WAZE_FEED_USER or not WAZE_FEED_PASSWORD:
        return Response("flux non configure\n", status=503, mimetype="text/plain")
    if not _basic_ok():
        return Response("authentification requise\n", status=401, mimetype="text/plain",
                        headers={"WWW-Authenticate": 'Basic realm="waze-feed"'})
    try:
        db = _get_mongo_db()
        enabled = feed_enabled(db)
        closures = list(db[COL].find({"publish": True})) if enabled else []
    except Exception as e:
        # Jamais un flux vide sur panne : Waze croirait toutes les routes rouvertes
        logger.error("waze_feed: lecture Mongo impossible: %s", e)
        return Response("indisponible\n", status=503, mimetype="text/plain")
    xml = build_feed_xml(closures, enabled=enabled)
    return Response(xml, mimetype="application/xml",
                    headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


# ---------------------------------------------------------------------------
# Administration (/api/admin/itineraire/waze/*, page /admin/itineraire)
# ---------------------------------------------------------------------------

def _author():
    u = getattr(request, "admin_user", None) or {}
    return (u.get("email") or "cockpit")[:120]


def pub(c, now=None):
    now = now or _now()
    return {
        "uid": c["uid"],
        "layer": c.get("layer"), "doc_id": c.get("doc_id"), "doc_name": c.get("doc_name"),
        "rule_keys": c.get("rule_keys") or [],
        "polyline": c.get("polyline") or [],
        "length_m": c.get("length_m"),
        "street": c.get("street"), "description": c.get("description"),
        "direction": c.get("direction"),
        "slots": [{"sid": s["sid"], "start": fmt_input(s["start"]), "end": fmt_input(slot_end(s)),
                   "started": _utc(s["start"]) <= now, "ended": slot_end(s) <= now,
                   "ended_early": bool(s.get("ended_early_at"))} for s in c.get("slots") or []],
        "publish": bool(c.get("publish")),
        "sync_ok": c.get("sync_ok") is not False,
        "status": closure_status(c, now),
        "street_check": c.get("street_check"),
        "created_by": c.get("created_by"), "updated_by": c.get("updated_by"),
        "updated_at": fmt_local(c["updated_at"]) if c.get("updated_at") else None,
        "published_by": c.get("published_by"),
    }


@waze_feed_bp.route("/api/admin/itineraire/waze/config", methods=["GET", "PUT"])
@admin_required
def api_config():
    db = _get_mongo_db()
    if request.method == "PUT":
        enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
        db[COL_CFG].update_one({"_id": "global"}, {"$set": {
            "enabled": enabled, "updated_by": _author(), "updated_at": _now()}}, upsert=True)
        logger.warning("waze_feed: publication vers Waze %s par %s", "ACTIVEE" if enabled else "COUPEE", _author())
    cfg = db[COL_CFG].find_one({"_id": "global"}) or {}
    base = (os.getenv("COCKPIT_PUBLIC_URL") or "https://cockpit.lemans.org").rstrip("/")
    return jsonify({
        "enabled": bool(cfg.get("enabled")),
        "updated_by": cfg.get("updated_by"),
        "feed_url": base + FEED_PATH,
        "feed_auth_ok": bool(WAZE_FEED_USER and WAZE_FEED_PASSWORD),
        "feed_user": WAZE_FEED_USER,
        "geocode_ok": bool(WAZE_GEOCODE_TOKEN),
    })


@waze_feed_bp.route("/api/admin/itineraire/waze/closures", methods=["GET"])
@admin_required
def api_list():
    db = _get_mongo_db()
    q = {}
    if request.args.get("layer") and request.args.get("doc_id"):
        q = {"layer": request.args["layer"], "doc_id": request.args["doc_id"]}
    now = _now()
    items = [pub(c, now) for c in db[COL].find(q).sort("updated_at", -1)]
    return jsonify({"items": items})


@waze_feed_bp.route("/api/admin/itineraire/waze/closures", methods=["POST"])
@admin_required
def api_create():
    data = request.get_json(silent=True) or {}
    fields, err = clean_closure(data)
    if err:
        return jsonify({"detail": err}), 422
    now = _now()
    doc = dict(fields)
    doc.update({
        "uid": "aco-wz-" + secrets.token_hex(4),
        "publish": bool(data.get("publish")),
        "sync_ok": True,
        "street_check": data.get("street_check") if isinstance(data.get("street_check"), dict) else None,
        "created_by": _author(), "created_at": now, "updated_by": _author(), "updated_at": now,
    })
    if doc["publish"]:
        doc["published_by"], doc["published_at"] = _author(), now
    db = _get_mongo_db()
    db[COL].insert_one(doc)
    logger.warning("waze_feed: fermeture %s (%s) creee par %s, publiee=%s",
                   doc["uid"], doc["street"], _author(), doc["publish"])
    return jsonify(pub(doc)), 201


@waze_feed_bp.route("/api/admin/itineraire/waze/closures/<uid>", methods=["PATCH", "DELETE"])
@admin_required
def api_update(uid):
    db = _get_mongo_db()
    c = db[COL].find_one({"uid": uid})
    if not c:
        return jsonify({"detail": "fermeture inconnue"}), 404
    now = _now()
    if request.method == "DELETE":
        if c.get("publish") and any(slot_end(s) > now for s in c.get("slots") or []):
            return jsonify({"detail": "Fermeture publiee pas encore terminee : la retirer de Waze "
                                      "(Rouvrir maintenant ou Retirer la publication) avant de la supprimer"}), 409
        db[COL].delete_one({"_id": c["_id"]})
        logger.warning("waze_feed: fermeture %s supprimee par %s", uid, _author())
        return Response(status=204)
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    upd = {"updated_by": _author(), "updated_at": now}
    if action == "publish":
        if c.get("sync_ok") is False:
            return jsonify({"detail": "Fermeture desynchronisee : les troncons ne sont plus fermes dans Cockpit"}), 409
        upd.update(publish=True, published_by=_author(), published_at=now)
    elif action == "unpublish":
        # Retirer du flux une fermeture deja commencee ne la rouvrirait pas
        # proprement chez Waze : on demande Rouvrir maintenant
        if any(_utc(s["start"]) <= now < slot_end(s) for s in c.get("slots") or []):
            return jsonify({"detail": "Fermeture en cours : utiliser Rouvrir maintenant"}), 409
        upd["publish"] = False
    elif action == "reopen":
        slots = c.get("slots") or []
        active = [s for s in slots if _utc(s["start"]) <= now < slot_end(s)]
        if not active:
            return jsonify({"detail": "Aucune plage en cours"}), 409
        for s in active:
            s["ended_early_at"] = now
        upd["slots"] = slots
    else:
        fields, err = clean_closure(data, existing=c)
        if err:
            return jsonify({"detail": err}), 422
        upd.update(fields)
        if isinstance(data.get("street_check"), dict):
            upd["street_check"] = data["street_check"]
    db[COL].update_one({"_id": c["_id"]}, {"$set": upd})
    logger.warning("waze_feed: fermeture %s (%s) %s par %s", uid, c.get("street"), action or "modifiee", _author())
    return jsonify(pub(db[COL].find_one({"_id": c["_id"]})))


@waze_feed_bp.route("/api/admin/itineraire/waze/resync", methods=["POST"])
@admin_required
def api_resync():
    resync_all()
    return jsonify({"ok": True})


@waze_feed_bp.route("/api/admin/itineraire/waze/preview", methods=["GET"])
@admin_required
def api_preview():
    db = _get_mongo_db()
    enabled = feed_enabled(db)
    xml = build_feed_xml(list(db[COL].find({"publish": True})) if enabled else [], enabled=enabled)
    return Response(xml, mimetype="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})


def waze_street_names(lat, lon):
    r = requests.get(WAZE_GEOCODE_URL, params={"lat": lat, "lon": lon, "token": WAZE_GEOCODE_TOKEN}, timeout=10)
    r.raise_for_status()
    names = []
    for it in (r.json() or {}).get("result") or []:
        for n in it.get("names") or []:
            if n and n not in names:
                names.append(n)
    return names


def _fold(s):
    return unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower().strip()


@waze_feed_bp.route("/api/admin/itineraire/waze/check-street", methods=["POST"])
@admin_required
def api_check_street():
    """Noms de rue Waze au premier et au dernier point (rayon 50 m) : Waze
    exige le meme nom aux deux extremites (correspondance partielle admise)."""
    if not WAZE_GEOCODE_TOKEN:
        return jsonify({"detail": "WAZE_GEOCODE_TOKEN absente du serveur Cockpit"}), 503
    data = request.get_json(silent=True) or {}
    pts = data.get("polyline") or []
    street = str(data.get("street") or "")
    if len(pts) < 2:
        return jsonify({"detail": "polyline manquante"}), 422
    try:
        first = waze_street_names(float(pts[0][0]), float(pts[0][1]))
        last = waze_street_names(float(pts[-1][0]), float(pts[-1][1]))
    except (requests.RequestException, ValueError, TypeError, IndexError) as e:
        logger.warning("waze_feed: geocodage inverse Waze indisponible: %s", e)
        return jsonify({"detail": "Service de noms de rue Waze indisponible"}), 502
    common = [n for n in first if _fold(n) in [_fold(x) for x in last]]
    fs = _fold(street)
    match = bool(fs) and any(fs in _fold(n) or _fold(n) in fs for n in common)
    return jsonify({"first": first, "last": last, "common": common, "match": match,
                    "checked_at": fmt_local(_now())})
