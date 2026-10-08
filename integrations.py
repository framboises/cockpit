"""Connecteur Cockpit <-> applications externes (08/10/2026).

Premier usage : Friday, l'outil de suivi des interventions du service
informatique. Generique : chaque application est un document de la
collection `integrations` (page admin /integrations).

Principe
--------
* SORTANT (Cockpit -> application) : un admin choisit les fiches envoyees,
  par CATEGORIE entiere ou par SOUS-CLASSIFICATION (regles par application).
  Un balayage (thread, toutes les SCAN_S s) compare chaque fiche concernee a
  ce qui a deja ete envoye (`integration_links`) et met en file
  (`integration_outbox`) un evenement : creation, modification, nouvelles
  entrees de chronologie (avec photos en liens signes), cloture,
  reouverture, sortie du perimetre. Un seul point de passage : toutes les
  ecritures (Cockpit, tablettes Field, synchro Prysm) sont vues.
  L'envoi (thread) signe chaque requete (HMAC-SHA256), reessaie avec delai
  croissant, abandonne apres OUTBOX_GIVE_UP_H (relance manuelle en admin).
* ENTRANT (application -> Cockpit) : POST /api/integrations/<id>/events,
  signe avec le meme secret. Evenements : liaison / deliaison d'un ticket
  (INC), mise a jour (statut, agent), note, photo, resolution. Ecrits dans la
  chronologie de la fiche (origin "integration:<id>", jamais renvoyes) et
  dans `pcorg.integrations.<id>` (badge sur la fiche).
* JAMAIS de cloture depuis une application externe : une resolution pose
  `attention: "resolved"` (badge "A cloturer") ; l'operateur clot lui-meme.
* Une application ne peut agir QUE sur les fiches qui lui ont ete envoyees.

Securite
--------
Secret par application dans l'environnement (`secret_env`, defaut
COCKPIT_INTEG_<ID>_SECRET, 32 caracteres min) : jamais en base ni dans
l'interface. Signature = HMAC-SHA256(secret, "<timestamp>.<corps brut>"),
en-tetes X-Event-Id, X-Signature-Timestamp (secondes epoch, +/- 300 s),
X-Signature ("sha256=<hex>"). Rejeu : X-Event-Id unique (integration_inbox,
TTL 30 j). Photos sortantes : URL signee a duree limitee
(/api/integrations/<id>/photo?p=&exp=&sig=), aucune session.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify, request, render_template, send_file, abort
from pymongo import ReturnDocument, DESCENDING
from pymongo.errors import DuplicateKeyError

import pcorg_history as PH

logger = logging.getLogger(__name__)

integrations_bp = Blueprint("integrations", __name__)

COL_CFG = "integrations"
COL_LINKS = "integration_links"
COL_OUTBOX = "integration_outbox"
COL_INBOX = "integration_inbox"

SCAN_S = 5                    # balayage des fiches a envoyer
SEND_S = 3                    # passage d'envoi de la file
SCAN_WINDOW_DAYS = 30         # fiches non encore envoyees : creees depuis moins de N jours
LINK_KEEP_DAYS = 120          # fiches deja envoyees : suivies tant que modifiees depuis < N jours
OUTBOX_GIVE_UP_H = 24         # au-dela : etat "failed", relance manuelle
SIG_MAX_SKEW_S = 300
PHOTO_URL_TTL_S = 7 * 24 * 3600
HTTP_TIMEOUT_S = 10
INBOUND_MAX_BYTES = 20 * 1024 * 1024
SECRET_MIN_LEN = 32
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")

STATUS_LABELS_DEFAULT = {
    "open": "Ouvert", "in_progress": "En cours", "escalated": "Escalade",
    "resolved": "Resolu", "cancelled": "Annule",
}


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt):
    dt = _aware(dt)
    return dt.isoformat() if isinstance(dt, datetime) else None


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).strip().lower()


def _app_db():
    from app import db
    return db


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def default_secret_env(integ_id):
    return "COCKPIT_INTEG_%s_SECRET" % re.sub(r"[^A-Z0-9]", "_", integ_id.upper())


# ---------------------------------------------------------------------------
# Adresse d'envoi (anti-SSRF, revue de securite du 08/10/2026)
# ---------------------------------------------------------------------------
# L'adresse est saisie par un admin, mais Cockpit ne doit pas devenir un
# relais vers n'importe quelle machine. Regles, verifiees A L'ENREGISTREMENT
# ET A CHAQUE ENVOI :
#   * https uniquement, sans identifiants dans l'URL ;
#   * hote present dans COCKPIT_INTEG_ALLOWED_HOSTS (liste fixee sur le
#     serveur, hors de portee de l'interface). Friday etant interne, les
#     adresses privees sont admises POUR CES HOTES-LA seulement ;
#   * jamais le serveur lui-meme ni les adresses speciales (loopback,
#     link-local dont 169.254.169.254, multicast, non routables), meme pour
#     un hote autorise : verifie sur les adresses resolues au moment de l'envoi ;
#   * pas de redirection suivie ; seul le code HTTP de la reponse est garde.

def allowed_hosts():
    raw = os.getenv("COCKPIT_INTEG_ALLOWED_HOSTS", "") or ""
    return {h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip()}


def _resolve(host):
    """Adresses IP de l'hote (isole pour les tests)."""
    import socket
    return sorted({ai[4][0] for ai in socket.getaddrinfo(host, None)})


def _ip_forbidden(ip_str):
    import ipaddress
    try:
        ip = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return True
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return bool(ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                or ip.is_reserved)


def check_outbound_url(url):
    """(ok, code) : l'adresse d'envoi respecte-t-elle les regles ci-dessus ?"""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(str(url or "").strip())
    except ValueError:
        return False, "url_invalide"
    if u.scheme != "https":
        return False, "https_obligatoire"
    if u.username or u.password or not u.hostname:
        return False, "url_invalide"
    host = u.hostname.lower().rstrip(".")
    if host not in allowed_hosts():
        return False, "hote_non_autorise"
    try:
        ips = _resolve(host)
    except Exception:
        return False, "hote_introuvable"
    if not ips or any(_ip_forbidden(ip) for ip in ips):
        return False, "adresse_interdite"
    return True, "ok"


def get_secret(cfg):
    name = (cfg or {}).get("secret_env") or default_secret_env((cfg or {}).get("_id") or "")
    val = os.getenv(name, "") or ""
    return val if len(val) >= SECRET_MIN_LEN else None


def list_configs(db):
    return list(db[COL_CFG].find({}).sort("_id", 1))


def get_config(db, integ_id):
    return db[COL_CFG].find_one({"_id": integ_id})


def clean_rules(raw):
    """[{category: "PCO.X", sous_classification: str|None}], sans doublon."""
    out, seen = [], set()
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        cat = str(r.get("category") or "").strip()
        if not cat.startswith("PCO.") or len(cat) > 40:
            continue
        sc = str(r.get("sous_classification") or "").strip()[:80] or None
        key = (cat, _norm(sc) if sc else None)
        if key in seen:
            continue
        seen.add(key)
        out.append({"category": cat, "sous_classification": sc})
    return out


def fiche_matches(cfg, fiche):
    cat = fiche.get("category")
    sc = _norm(((fiche.get("content_category") or {}).get("sous_classification")) or "")
    for r in cfg.get("rules") or []:
        if r.get("category") != cat:
            continue
        if not r.get("sous_classification") or _norm(r["sous_classification"]) == sc:
            return True
    return False


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------

def sign(secret, ts, body_bytes):
    mac = hmac.new(secret.encode("utf-8"), str(ts).encode("ascii") + b"." + body_bytes, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(secret, ts_header, sig_header, body_bytes, now=None):
    """(ok, code). Horodatage a +/- SIG_MAX_SKEW_S, comparaison a temps constant."""
    try:
        ts = int(str(ts_header or "").strip())
    except ValueError:
        return False, "timestamp_invalide"
    now_s = int((now or _now()).timestamp())
    if abs(now_s - ts) > SIG_MAX_SKEW_S:
        return False, "timestamp_hors_delai"
    expected = sign(secret, ts, body_bytes)
    if not hmac.compare_digest(expected, str(sig_header or "").strip()):
        return False, "signature_invalide"
    return True, "ok"


def photo_url_sig(secret, path, exp):
    return hmac.new(secret.encode("utf-8"), ("%s.%d" % (path, exp)).encode("utf-8"), hashlib.sha256).hexdigest()


def signed_photo_url(cfg, base_url, photo_path):
    """URL publique signee (duree limitee) d'une photo de /field/photos/..."""
    secret = get_secret(cfg)
    if not secret or not photo_path or not str(photo_path).startswith("/field/photos/"):
        return None
    rel = str(photo_path)[len("/field/photos/"):]
    exp = int(time.time()) + PHOTO_URL_TTL_S
    from urllib.parse import urlencode
    qs = urlencode({"p": rel, "exp": exp, "sig": photo_url_sig(secret, rel, exp)})
    return "%s/api/integrations/%s/photo?%s" % (base_url.rstrip("/"), cfg["_id"], qs)


# ---------------------------------------------------------------------------
# Construction des evenements sortants
# ---------------------------------------------------------------------------

def _entry_id(e):
    raw = "%s|%s|%s" % (_iso(e.get("ts")) or e.get("ts") or "", e.get("operator") or "", e.get("text") or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def fiche_snapshot(fiche):
    cc = fiche.get("content_category") or {}
    gps = fiche.get("gps") or {}
    coords = gps.get("coordinates") if isinstance(gps, dict) else None
    area = fiche.get("area") or {}
    return {
        "id": str(fiche["_id"]),
        "event": fiche.get("event"),
        "year": fiche.get("year"),
        "category": fiche.get("category"),
        "sous_classification": cc.get("sous_classification") or None,
        "text": fiche.get("text") or "",
        "niveau_urgence": fiche.get("niveau_urgence"),
        "status": "closed" if fiche.get("status_code") == 10 else "open",
        "ts": _iso(fiche.get("ts")),
        "created_at": _iso(fiche.get("created_at")),
        "close_ts": _iso(fiche.get("close_ts")),
        "operator": fiche.get("operator") or "",
        "operator_close": fiche.get("operator_close") or None,
        "area": (area.get("desc") if isinstance(area, dict) else None) or None,
        "carroye": cc.get("carroye") or None,
        "lat": coords[1] if isinstance(coords, list) and len(coords) >= 2 else None,
        "lng": coords[0] if isinstance(coords, list) and len(coords) >= 2 else None,
        "unit": cc.get("patrouille") or None,
        "declaration_ref": fiche.get("declaration_ref") or None,
    }


SNAPSHOT_KEYS = ("category", "sous_classification", "text", "niveau_urgence", "status",
                 "close_ts", "area", "carroye", "lat", "lng", "unit", "operator_close")


def _snap_hash(snap):
    return hashlib.sha1(json.dumps({k: snap.get(k) for k in SNAPSHOT_KEYS},
                                   sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _public_entries(cfg, entries, base_url):
    """Entrees de chronologie a transmettre (sans celles venues de CETTE application)."""
    own = "integration:" + cfg["_id"]
    out = []
    for e in entries:
        if e.get("origin") == own:
            continue
        text = (e.get("text") or "").strip()
        photos = e.get("photos") or ([{"photo": e.get("photo"), "thumb": e.get("thumb")}] if e.get("photo") else [])
        if not text and not photos:
            continue
        item = {
            "id": _entry_id(e),
            "ts": _iso(e.get("ts")) or e.get("ts"),
            "author": str(e.get("operator") or "").replace("field:", "Tablette "),
            "origin": e.get("origin") or None,
            "text": text,
            "photos": [],
        }
        for p in photos:
            u = signed_photo_url(cfg, base_url, p.get("photo"))
            if u:
                item["photos"].append({"url": u, "thumb_url": signed_photo_url(cfg, base_url, p.get("thumb")) or u})
        out.append(item)
    return out


def _enqueue(db, cfg, etype, fiche_id, payload, now):
    ev_id = str(uuid.uuid4())
    body = dict(payload, event_id=ev_id, type=etype, integration=cfg["_id"], source="cockpit",
                sent_at=None)
    db[COL_OUTBOX].insert_one({
        "_id": ev_id, "integration": cfg["_id"], "type": etype, "fiche_id": fiche_id,
        "payload": body, "state": "pending", "attempts": 0, "created_at": now, "next_at": now,
        "deadline": now + timedelta(hours=OUTBOX_GIVE_UP_H), "last_error": None, "sent_at": None,
    })
    return ev_id


def scan_integration(db, cfg, base_url, now=None):
    """Compare les fiches concernees a l'etat deja envoye, met en file les
    evenements. Retourne le nombre d'evenements crees."""
    now = now or _now()
    rules = cfg.get("rules") or []
    if not rules:
        return 0
    integ = cfg["_id"]
    cats = sorted({r["category"] for r in rules})
    since_new = now - timedelta(days=SCAN_WINDOW_DAYS)
    enabled_at = _aware(cfg.get("enabled_at"))
    if isinstance(enabled_at, datetime) and enabled_at > since_new:
        since_new = enabled_at          # pas de rattrapage des fiches anterieures a l'activation
    links = {l["fiche_id"]: l for l in db[COL_LINKS].find(
        {"integration": integ, "updated_at": {"$gte": now - timedelta(days=LINK_KEEP_DAYS)}})}
    flt = {"$or": [
        {"category": {"$in": cats}, "ts": {"$gte": since_new}},
        {"_id": {"$in": list(links.keys())}},
    ]}
    # Une seule requete : champs utiles + nombre d'entrees de chronologie
    # (la chronologie elle-meme n'est relue que pour les fiches modifiees)
    proj = {k: 1 for k in ("event", "year", "category", "content_category", "text", "niveau_urgence",
                           "status_code", "ts", "created_at", "close_ts", "operator", "operator_close",
                           "area", "gps", "declaration_ref")}
    proj["hcount"] = {"$size": {"$ifNull": ["$comment_history", []]}}
    created = 0
    for f in db["pcorg"].aggregate([{"$match": flt}, {"$project": proj}]):
        fid = str(f["_id"])
        link = links.get(fid)
        match = fiche_matches(cfg, f)
        if not link and not match:
            continue
        snap = fiche_snapshot(f)
        h = _snap_hash(snap)
        hcount = int(f.get("hcount") or 0)
        if link and not match and link.get("in_scope"):
            # Sortie du perimetre (categorie / sous-classification changee)
            _enqueue(db, cfg, "fiche.unassigned", fid, {"fiche": snap}, now)
            db[COL_LINKS].update_one({"_id": link["_id"]}, {"$set": {
                "in_scope": False, "sent_hash": h, "sent_entries": hcount, "updated_at": now}})
            created += 1
            continue
        if link and not match:
            continue
        if link and link.get("sent_hash") == h and link.get("sent_entries", 0) >= hcount and link.get("in_scope"):
            continue
        first = not link or not link.get("in_scope")
        # Premier envoi (ou retour dans le perimetre) : toute la chronologie,
        # ensuite seulement les nouvelles entrees (Friday dedoublonne par id)
        start = 0 if first else int(link.get("sent_entries") or 0)
        new_entries = []
        if hcount > start:
            full = db["pcorg"].find_one({"_id": f["_id"]}, {"comment_history": {"$slice": [start, hcount - start]}})
            new_entries = _public_entries(cfg, (full or {}).get("comment_history") or [], base_url)
        if first:
            etype = "fiche.created"
        else:
            prev_status = link.get("sent_status")
            if prev_status != snap["status"]:
                etype = "fiche.closed" if snap["status"] == "closed" else "fiche.reopened"
            elif link.get("sent_hash") != h:
                etype = "fiche.updated"
            else:
                etype = "fiche.comment"
                if not new_entries:
                    # Seulement des entrees venues de l'application elle-meme :
                    # rien a lui renvoyer, on avance le compteur
                    db[COL_LINKS].update_one({"_id": link["_id"]}, {"$set": {"sent_entries": hcount}})
                    continue
        _enqueue(db, cfg, etype, fid, {"fiche": snap, "new_entries": new_entries}, now)
        db[COL_LINKS].update_one({"_id": "%s|%s" % (integ, fid)}, {
            "$set": {"integration": integ, "fiche_id": fid, "sent_hash": h, "sent_entries": hcount,
                     "sent_status": snap["status"], "in_scope": True, "updated_at": now},
            "$setOnInsert": {"first_sent_at": now}}, upsert=True)
        created += 1
    return created


# ---------------------------------------------------------------------------
# Envoi
# ---------------------------------------------------------------------------

def _backoff_s(attempts):
    return min(3600, 15 * (2 ** max(0, attempts - 1)))


def send_one(db, cfg, item, now=None):
    import requests
    now = now or _now()
    secret = get_secret(cfg)
    url = (cfg.get("outbound_url") or "").strip()
    if not secret or not url:
        return False, "configuration_incomplete"
    ok_url, code = check_outbound_url(url)      # a chaque envoi (DNS, liste d'hotes)
    if not ok_url:
        return False, "adresse refusee : %s" % code
    payload = dict(item["payload"], sent_at=_iso(now))
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    ts = int(now.timestamp())
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Event-Id": item["_id"],
        "X-Event-Type": item["type"],
        "X-Signature-Timestamp": str(ts),
        "X-Signature": sign(secret, ts, body),
        "X-Requested-With": "XMLHttpRequest",      # garde CSRF de Friday
        "User-Agent": "Cockpit-Integration/1",
    }
    try:
        r = requests.post(url, data=body, headers=headers, timeout=HTTP_TIMEOUT_S,
                          allow_redirects=False)
    except Exception as e:
        # Type d'erreur seulement : ni URL ni contenu dans ce qui est stocke / affiche
        return False, "reseau (%s)" % type(e).__name__
    if 200 <= r.status_code < 300:
        return True, "HTTP %d" % r.status_code
    # Code seulement : le contenu de la reponse n'est ni stocke ni affiche
    return False, "HTTP %d" % r.status_code


def process_outbox(db, now=None, limit=50):
    """Envoie la file, application par application, DANS L'ORDRE : tant que
    l'evenement le plus ancien d'une application echoue, les suivants
    attendent (pas de "cloture" recue avant la "creation")."""
    now = now or _now()
    n = 0
    for cfg in list_configs(db):
        if not cfg.get("enabled"):
            continue
        for _ in range(limit):
            item = db[COL_OUTBOX].find_one({"integration": cfg["_id"], "state": "pending"},
                                           sort=[("created_at", 1), ("_id", 1)])
            if not item or (_aware(item.get("next_at")) or now) > now:
                break
            ok, msg = send_one(db, cfg, item, now)
            attempts = int(item.get("attempts") or 0) + 1
            if ok:
                db[COL_OUTBOX].update_one({"_id": item["_id"]}, {"$set": {
                    "state": "sent", "sent_at": now, "attempts": attempts, "last_error": None}})
                n += 1
                continue
            deadline = _aware(item.get("deadline")) or now
            give_up = now >= deadline
            db[COL_OUTBOX].update_one({"_id": item["_id"]}, {"$set": {
                "state": "failed" if give_up else "pending", "attempts": attempts, "last_error": msg,
                "last_try_at": now, "next_at": now + timedelta(seconds=_backoff_s(attempts))}})
            if not give_up:
                break           # on reessaiera plus tard, les suivants attendent
    return n


def ensure_indexes(db):
    try:
        db[COL_OUTBOX].create_index([("state", 1), ("next_at", 1)])
        db[COL_OUTBOX].create_index([("integration", 1), ("created_at", DESCENDING)])
        db[COL_OUTBOX].create_index("sent_at", expireAfterSeconds=30 * 24 * 3600)
        db[COL_LINKS].create_index([("integration", 1), ("updated_at", DESCENDING)])
        db[COL_INBOX].create_index("received_at", expireAfterSeconds=30 * 24 * 3600)
    except Exception as e:  # pragma: no cover
        logger.debug("integrations index : %s", e)


_worker_started = False
_worker_lock = threading.Lock()


def _base_url():
    return (os.getenv("COCKPIT_PUBLIC_URL") or os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")


def start_worker(db_getter=None):
    """Thread de fond : balayage + envoi. Idempotent."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    def _loop():
        db = (db_getter or _app_db)()
        ensure_indexes(db)
        last_scan = 0.0
        while True:
            try:
                if time.time() - last_scan >= SCAN_S:
                    last_scan = time.time()
                    for cfg in list_configs(db):
                        if cfg.get("enabled") and get_secret(cfg):
                            scan_integration(db, cfg, _base_url())
                process_outbox(db)
            except Exception as e:
                logger.warning("integrations : %s", e)
            time.sleep(SEND_S)

    threading.Thread(target=_loop, name="integrations", daemon=True).start()
    logger.info("Connecteur applications externes demarre")


# ---------------------------------------------------------------------------
# Entrant : POST /api/integrations/<id>/events
# ---------------------------------------------------------------------------

def _clean(s, n):
    return str(s or "").strip()[:n]


def _status_label(cfg, status, label=None):
    if label:
        return _clean(label, 60)
    labels = dict(STATUS_LABELS_DEFAULT, **(cfg.get("status_labels") or {}))
    return labels.get(status, status)


def _author(cfg, who):
    who = _clean(who, 80)
    return "%s%s" % (cfg.get("label") or cfg["_id"], (" - " + who) if who else "")


def _add_entry(db, cfg, fiche_id, text, author, photos=None, extra_set=None):
    entry = PH.make_entry(_author(cfg, author), text, origin="integration:" + cfg["_id"])
    if photos:
        entry["photos"] = photos
        entry["photo"] = photos[0]["photo"]
        entry["thumb"] = photos[0]["thumb"]
    PH.append_entry(db["pcorg"], fiche_id, entry, set_fields=extra_set or None, inc_bounce=True)


def _set_link(db, cfg, fiche_id, fields):
    sets = {"integrations.%s.%s" % (cfg["_id"], k): v for k, v in fields.items()}
    sets["integrations.%s.updated_at" % cfg["_id"]] = _now()
    db["pcorg"].update_one({"_id": fiche_id}, {"$set": sets, "$inc": {"bounce_rev": 1}})


def _save_photo(db, cfg, fiche, data):
    """Photo recue en base64 (JSON) -> meme traitement que les photos Field."""
    import field as F
    from werkzeug.datastructures import FileStorage
    from io import BytesIO
    raw = data.get("data_base64") or ""
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception:
        raise ValueError("photo_base64_invalide")
    name = _clean(data.get("filename"), 80) or "photo.jpg"
    if "." not in name:
        name += ".jpg"
    fs = FileStorage(stream=BytesIO(blob), filename=name)
    ev = re.sub(r"[^A-Za-z0-9 _-]", "_", str(fiche.get("event") or "cockpit"))
    yr = re.sub(r"[^0-9A-Za-z_-]", "_", str(fiche.get("year") or ""))
    try:
        url, thumb = F._process_and_save_photo(fs, os.path.join(ev, yr))
    except F.PhotoUploadError as e:
        raise ValueError(e.code)
    return {"photo": url, "thumb": thumb}


def handle_event(db, cfg, ev):
    """Applique un evenement entrant. Retourne (status_http, corps)."""
    etype = _clean(ev.get("type"), 40)
    fiche_id = _clean(ev.get("fiche_id"), 80)
    if not fiche_id:
        return 400, {"ok": False, "error": "fiche_id_requis"}
    # Seules les fiches envoyees a CETTE application sont modifiables par elle
    if not db[COL_LINKS].find_one({"_id": "%s|%s" % (cfg["_id"], fiche_id)}, {"_id": 1}):
        return 404, {"ok": False, "error": "fiche_non_partagee"}
    fiche = db["pcorg"].find_one({"_id": fiche_id}, {"event": 1, "year": 1, "integrations": 1})
    if not fiche:
        return 404, {"ok": False, "error": "fiche_introuvable"}
    cur = ((fiche.get("integrations") or {}).get(cfg["_id"]) or {})
    ref = _clean(ev.get("ref"), 40) or cur.get("ref")
    url = _clean(ev.get("url"), 300) or cur.get("url")
    if url and not re.match(r"^https?://", url):
        url = None
    agent = _clean(ev.get("agent"), 80)
    author = _clean(ev.get("author"), 80) or agent

    if etype == "ticket.linked":
        if not ref:
            return 400, {"ok": False, "error": "ref_requise"}
        status = _clean(ev.get("status"), 30) or "open"
        _set_link(db, cfg, fiche_id, {"linked": True, "ref": ref, "url": url, "status": status,
                                      "status_label": _status_label(cfg, status, ev.get("status_label")),
                                      "agent": agent or None, "attention": None,
                                      "linked_at": _now()})
        _add_entry(db, cfg, fiche_id, "Fiche liee au ticket %s%s" % (ref, (" (agent " + agent + ")") if agent else ""),
                   author)
        return 200, {"ok": True}

    if not cur.get("linked"):
        return 409, {"ok": False, "error": "fiche_non_liee"}

    if etype == "ticket.unlinked":
        _set_link(db, cfg, fiche_id, {"linked": False, "attention": None})
        _add_entry(db, cfg, fiche_id, "Fiche deliee du ticket %s" % (cur.get("ref") or ""), author)
        return 200, {"ok": True}

    if etype == "ticket.updated":
        status = _clean(ev.get("status"), 30) or cur.get("status")
        fields = {"status": status, "status_label": _status_label(cfg, status, ev.get("status_label")),
                  "ref": ref, "url": url}
        if "agent" in ev:
            fields["agent"] = agent or None
        if status != "resolved" and cur.get("attention") == "resolved":
            fields["attention"] = None      # ticket rouvert
        _set_link(db, cfg, fiche_id, fields)
        parts = []
        if status != cur.get("status"):
            parts.append("statut %s -> %s" % (cur.get("status_label") or cur.get("status") or "?",
                                               fields["status_label"]))
        if "agent" in ev and (agent or None) != cur.get("agent"):
            parts.append("agent : %s" % (agent or "aucun"))
        if parts:
            _add_entry(db, cfg, fiche_id, "Ticket %s : %s" % (ref or "", ", ".join(parts)), author)
        return 200, {"ok": True}

    if etype == "ticket.note":
        text = _clean(ev.get("text"), 5000)
        if not text:
            return 400, {"ok": False, "error": "texte_requis"}
        _add_entry(db, cfg, fiche_id, text, author)
        return 200, {"ok": True}

    if etype == "ticket.photo":
        try:
            ph = _save_photo(db, cfg, fiche, ev.get("photo") or {})
        except ValueError as e:
            return 400, {"ok": False, "error": str(e)}
        caption = _clean((ev.get("photo") or {}).get("caption"), 500) or "Photo du ticket %s" % (ref or "")
        _add_entry(db, cfg, fiche_id, caption, author, photos=[ph])
        return 200, {"ok": True}

    if etype == "ticket.resolved":
        resolution = _clean(ev.get("resolution"), 5000)
        _set_link(db, cfg, fiche_id, {"status": "resolved",
                                      "status_label": _status_label(cfg, "resolved", ev.get("status_label")),
                                      "attention": "resolved", "resolution": resolution or None,
                                      "resolved_by": _clean(ev.get("resolved_by"), 80) or author or None,
                                      "resolved_at": _clean(ev.get("resolved_at"), 40) or _iso(_now())})
        _add_entry(db, cfg, fiche_id, "Ticket %s resolu%s. A cloturer par l'operateur." % (
            ref or "", (" : " + resolution) if resolution else ""), _clean(ev.get("resolved_by"), 80) or author)
        return 200, {"ok": True}

    return 400, {"ok": False, "error": "type_inconnu"}


@integrations_bp.route("/api/integrations/<integ_id>/events", methods=["POST"])
def inbound_events(integ_id):
    """Point d'entree des applications externes (signature HMAC, sans session)."""
    db = _app_db()
    cfg = get_config(db, integ_id) if ID_RE.match(integ_id or "") else None
    if not cfg or not cfg.get("enabled"):
        return jsonify({"ok": False, "error": "integration_inconnue_ou_inactive"}), 404
    secret = get_secret(cfg)
    if not secret:
        return jsonify({"ok": False, "error": "secret_non_configure"}), 503
    if (request.content_length or 0) > INBOUND_MAX_BYTES:
        return jsonify({"ok": False, "error": "trop_volumineux"}), 413
    body = request.get_data(cache=False) or b""
    ok, code = verify(secret, request.headers.get("X-Signature-Timestamp"),
                      request.headers.get("X-Signature"), body)
    if not ok:
        logger.warning("integrations %s : requete refusee (%s) depuis %s", integ_id, code, request.remote_addr)
        return jsonify({"ok": False, "error": code}), 401
    ev_id = _clean(request.headers.get("X-Event-Id"), 80)
    if not ev_id:
        return jsonify({"ok": False, "error": "event_id_requis"}), 400
    try:
        ev = json.loads(body.decode("utf-8"))
        if not isinstance(ev, dict):
            raise ValueError
    except Exception:
        return jsonify({"ok": False, "error": "json_invalide"}), 400
    try:
        db[COL_INBOX].insert_one({"_id": "%s|%s" % (integ_id, ev_id), "received_at": _now(),
                                  "type": _clean(ev.get("type"), 40), "fiche_id": _clean(ev.get("fiche_id"), 80),
                                  "result": None})
    except DuplicateKeyError:
        return jsonify({"ok": True, "duplicate": True})
    status, out = handle_event(db, cfg, ev)
    db[COL_INBOX].update_one({"_id": "%s|%s" % (integ_id, ev_id)},
                             {"$set": {"result": out.get("error") or "ok", "http": status}})
    if status >= 400:
        # Rejet definitif : le meme X-Event-Id corrige pourra etre renvoye
        db[COL_INBOX].delete_one({"_id": "%s|%s" % (integ_id, ev_id)})
    return jsonify(out), status


@integrations_bp.route("/api/integrations/<integ_id>/photo")
def signed_photo(integ_id):
    """Photo d'une fiche partagee, par URL signee (aucune session)."""
    import field as F
    from werkzeug.utils import safe_join
    db = _app_db()
    cfg = get_config(db, integ_id) if ID_RE.match(integ_id or "") else None
    secret = get_secret(cfg) if cfg and cfg.get("enabled") else None
    rel = request.args.get("p") or ""
    try:
        exp = int(request.args.get("exp") or 0)
    except ValueError:
        exp = 0
    if not secret or exp < time.time() or ".." in rel or rel.startswith("/"):
        abort(404)
    if not hmac.compare_digest(photo_url_sig(secret, rel, exp), request.args.get("sig") or ""):
        abort(404)
    path = safe_join(F.FIELD_PHOTOS_DIR, rel)
    if not path or not os.path.isfile(path):
        abort(404)
    resp = send_file(path, mimetype="image/jpeg", max_age=3600)
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


# ---------------------------------------------------------------------------
# Administration : /integrations (admin)
# ---------------------------------------------------------------------------

def _role_required(role):
    def deco(f):
        from functools import wraps

        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


def _is_real_admin():
    u = getattr(request, "user_payload", None) or {}
    return bool(u.get("is_super_admin") or u.get("app_role") == "admin")


def _pub_cfg(db, cfg):
    stats = {s: db[COL_OUTBOX].count_documents({"integration": cfg["_id"], "state": s})
             for s in ("pending", "failed")}
    last_err = db[COL_OUTBOX].find_one({"integration": cfg["_id"], "last_error": {"$ne": None}},
                                       {"last_error": 1, "last_try_at": 1}, sort=[("last_try_at", -1)])
    return {
        "id": cfg["_id"],
        "label": cfg.get("label") or cfg["_id"],
        "enabled": bool(cfg.get("enabled")),
        "outbound_url": cfg.get("outbound_url") or "",
        "secret_env": cfg.get("secret_env") or default_secret_env(cfg["_id"]),
        "secret_ok": bool(get_secret(cfg)),
        "rules": cfg.get("rules") or [],
        "status_labels": cfg.get("status_labels") or {},
        "enabled_at": _iso(cfg.get("enabled_at")),
        "inbound_path": "/api/integrations/%s/events" % cfg["_id"],
        "allowed_hosts": sorted(allowed_hosts()),
        "shared": db[COL_LINKS].count_documents({"integration": cfg["_id"], "in_scope": True}),
        "linked": db["pcorg"].count_documents({"integrations.%s.linked" % cfg["_id"]: True}),
        "outbox": stats,
        "last_error": (last_err or {}).get("last_error"),
        "last_error_at": _iso((last_err or {}).get("last_try_at")),
    }


@integrations_bp.route("/integrations")
@_role_required("admin")
def integrations_page():
    if not _is_real_admin():
        abort(403)
    u = getattr(request, "user_payload", None) or {}
    return render_template("integrations.html", user=u, public_url=_base_url())


@integrations_bp.route("/api/integrations-admin", methods=["GET"])
@_role_required("admin")
def admin_list():
    if not _is_real_admin():
        abort(403)
    db = _app_db()
    return jsonify({"ok": True, "integrations": [_pub_cfg(db, c) for c in list_configs(db)]})


@integrations_bp.route("/api/integrations-admin", methods=["POST"])
@_role_required("admin")
def admin_save():
    """Creation / modification. Le secret n'est jamais saisi ici : seule la
    variable d'environnement qui le porte est indiquee."""
    if not _is_real_admin():
        abort(403)
    db = _app_db()
    data = request.get_json(silent=True) or {}
    integ_id = _clean(data.get("id"), 31).lower()
    if not ID_RE.match(integ_id):
        return jsonify({"ok": False, "error": "identifiant_invalide"}), 400
    url = _clean(data.get("outbound_url"), 300)
    if url:
        ok_url, code = check_outbound_url(url)
        if not ok_url:
            return jsonify({"ok": False, "error": code,
                            "allowed_hosts": sorted(allowed_hosts())}), 400
    secret_env = _clean(data.get("secret_env"), 80) or default_secret_env(integ_id)
    if not re.match(r"^[A-Z][A-Z0-9_]{3,79}$", secret_env):
        return jsonify({"ok": False, "error": "variable_secret_invalide"}), 400
    cur = get_config(db, integ_id) or {}
    enabled = bool(data.get("enabled"))
    doc = {
        "label": _clean(data.get("label"), 60) or integ_id,
        "outbound_url": url,
        "secret_env": secret_env,
        "rules": clean_rules(data.get("rules")),
        "enabled": enabled,
        "updated_at": _now(),
        "updated_by": (getattr(request, "user_payload", None) or {}).get("email"),
    }
    labels = data.get("status_labels")
    if isinstance(labels, dict):
        doc["status_labels"] = {_clean(k, 30): _clean(v, 60) for k, v in labels.items() if k}
    if enabled and not cur.get("enabled"):
        doc["enabled_at"] = _now()
    db[COL_CFG].update_one({"_id": integ_id}, {"$set": doc, "$setOnInsert": {"created_at": _now()}}, upsert=True)
    return jsonify({"ok": True, "integration": _pub_cfg(db, get_config(db, integ_id))})


@integrations_bp.route("/api/integrations-admin/<integ_id>/retry", methods=["POST"])
@_role_required("admin")
def admin_retry(integ_id):
    if not _is_real_admin():
        abort(403)
    db = _app_db()
    n = db[COL_OUTBOX].update_many({"integration": integ_id, "state": "failed"},
                                   {"$set": {"state": "pending", "next_at": _now(), "attempts": 0,
                                             "deadline": _now() + timedelta(hours=OUTBOX_GIVE_UP_H)}}
                                   ).modified_count
    return jsonify({"ok": True, "requeued": n})


@integrations_bp.route("/api/integrations-admin/<integ_id>/test", methods=["POST"])
@_role_required("admin")
def admin_test(integ_id):
    """Envoie un evenement "ping" signe et rend la reponse de l'application."""
    if not _is_real_admin():
        abort(403)
    db = _app_db()
    cfg = get_config(db, integ_id)
    if not cfg:
        return jsonify({"ok": False, "error": "introuvable"}), 404
    item = {"_id": str(uuid.uuid4()), "type": "ping",
            "payload": {"event_id": None, "type": "ping", "integration": integ_id, "source": "cockpit"}}
    item["payload"]["event_id"] = item["_id"]
    ok, msg = send_one(db, cfg, item)
    return jsonify({"ok": ok, "message": msg})


@integrations_bp.route("/api/integrations-admin/<integ_id>/outbox", methods=["GET"])
@_role_required("admin")
def admin_outbox(integ_id):
    if not _is_real_admin():
        abort(403)
    db = _app_db()
    rows = list(db[COL_OUTBOX].find({"integration": integ_id}, {"payload": 0})
                .sort("created_at", -1).limit(50))
    return jsonify({"ok": True, "items": [{
        "id": r["_id"], "type": r.get("type"), "fiche_id": r.get("fiche_id"), "state": r.get("state"),
        "attempts": r.get("attempts"), "created_at": _iso(r.get("created_at")),
        "sent_at": _iso(r.get("sent_at")), "last_error": r.get("last_error"),
    } for r in rows]})
