"""Chat Alfred dans Cockpit (widget flottant des operateurs PC Organisation).

    Navigateur --(JWT + CSRF)--> /api/alfred-chat/*  --(HMAC)--> VM /alfred/ask
    VM (outils) --(HMAC)-------> /api/alfred-tools/* --> alfred_tools.py

CHAT (session Cockpit, role user + drapeau de groupe `alfred_chat`)

  GET  /api/alfred-chat/state           conversation courante, etat, sante
  POST /api/alfred-chat/ask             pose une question (CSRF) -> id du message
  GET  /api/alfred-chat/message/<id>    suivi d'une reponse (polling)
  POST /api/alfred-chat/new             nouvelle conversation (CSRF)
  POST /api/alfred-chat/feedback/<id>   pouce haut / bas + motif (CSRF), fige
                                        l'echange dans alfred_chat_retours (sans TTL)
  GET  /alfred-retours                  page admin des retours (+ API /retours, export JSONL)
  GET  /api/alfred-chat/sessions        conversations passees de l'operateur
  GET  /api/alfred-chat/session/<id>    relire une conversation passee
  GET  /api/alfred-chat/health          sante du wrapper (cache 20 s)

  ⚠️ ASYNCHRONE, ET C'EST VOULU. Waitress sert Cockpit avec 4 threads et une
  reponse d'Alfred prend 3 a 90 s : un appel synchrone par operateur
  bloquerait l'application entiere des la troisieme question simultanee. Le
  POST rend tout de suite ; un thread de fond appelle la VM sous un
  semaphore (ALFRED_CHAT_MAX_CONCURRENT, 2 par defaut) et ecrit la reponse
  dans Mongo ; le navigateur la relit toutes les 1,5 s. La reponse etant en
  base, changer de page pendant qu'Alfred reflechit ne perd rien.

  Le client HMAC est celui de WhatsApp (alfred._alfred_ask) : la signature
  n'est ecrite qu'une fois dans ce depot.

OUTILS (appeles par la VM, HMAC ALFRED_TOOLS_SECRET, sans session)

  GET  /api/alfred-tools/manifest       definitions tool-calling (format Ollama)
  POST /api/alfred-tools/call           {tool, args, scope?, request_id?}
  GET  /api/alfred-tools/retours?depuis= retours des operateurs (corpus VM)

  Recette de signature identique a /alfred/ask, dans l'autre sens :
  X-Alfred-Timestamp + X-Alfred-Signature = "sha256=" + HMAC(secret,
  ts + "." + corps brut) ; corps vide pour le GET ; fenetre +/- 300 s.

  PORTEE. Chaque question du chat embarque un jeton `scope` signe par
  Cockpit (categories main courante et alertes de l'operateur, 15 min). Le
  wrapper le renvoie tel quel a chaque appel d'outil : c'est ce qui empeche
  un operateur restreint a la Technique de lire les fiches Secours par
  Alfred. REFUS PAR DEFAUT : signe avec ALFRED_TOOLS_SECRET, un appel sans
  scope est refuse (403 scope_requis), quel que soit son request_id. La vue
  PC Org complete (mentions WhatsApp) exige un SECRET DISTINCT,
  ALFRED_TOOLS_UNSCOPED_SECRET, facultatif (absent = pas de vue complete).
  Scope invalide ou expire : refus, jamais d'elargissement.

Retention 90 jours (index TTL) sur messages, conversations et appels d'outils.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, jsonify, request

import alfred
import alfred_tools

logger = logging.getLogger(__name__)

alfred_chat_bp = Blueprint("alfred_chat", __name__)

TZ_PARIS = ZoneInfo("Europe/Paris")

MAX_CONCURRENT = max(1, int(os.getenv("ALFRED_CHAT_MAX_CONCURRENT", "2")))
QUEUE_WAIT_S = int(os.getenv("ALFRED_CHAT_QUEUE_WAIT_S", "60"))
RETENTION_DAYS = int(os.getenv("ALFRED_CHAT_RETENTION_DAYS", "90"))
HISTORY_MESSAGES = int(os.getenv("ALFRED_CHAT_HISTORY", "10"))
MAX_TOOL_HOPS = int(os.getenv("ALFRED_CHAT_MAX_TOOL_HOPS", "6"))
RATE_10MIN = int(os.getenv("ALFRED_CHAT_RATE_10MIN", "40"))
# Attente de la reponse du wrapper, plus longue que les mentions WhatsApp
# (ALFRED_ASK_TIMEOUT, 90 s) : le wrapper borne sa boucle d'outils a 180 s et
# rend alors un 504 global_timeout propre. Couper a 90 s laissait la VM
# travailler pour rien et affichait << trop long >> sans son diagnostic.
# On ne bloque qu'un thread de fond, jamais un thread Waitress.
CHAT_TIMEOUT_S = int(os.getenv("ALFRED_CHAT_TIMEOUT", "190"))
CONTENT_MAX = 2000
# prefix : contexte en tete du dernier message (fonctionne avec le wrapper
#          actuel, qui ignore les messages system)
# field  : champs `channel` + `context` structures (wrapper mis a jour)
# both   : les deux, le temps de la transition
CONTEXT_MODE = os.getenv("ALFRED_CHAT_CONTEXT_MODE", "prefix").strip().lower()
TOOLS_SECRET = os.getenv("ALFRED_TOOLS_SECRET", "").strip()
# Vue PC Org complete (mentions WhatsApp, sans operateur Cockpit). Secret
# DISTINCT et facultatif : absent = aucun appel d'outil sans jeton de portee.
UNSCOPED_SECRET = os.getenv("ALFRED_TOOLS_UNSCOPED_SECRET", "").strip()
SCOPE_TTL_S = 15 * 60
HMAC_WINDOW_S = 300
HEALTH_URL = os.getenv("ALFRED_HEALTH_URL", "").strip() or (
    alfred.ALFRED_ASK_URL.rsplit("/alfred/ask", 1)[0] + "/alfred/health")
HEALTH_TTL_S = 20

COL_SESSIONS = "alfred_chat_sessions"
COL_MESSAGES = "alfred_chat_messages"
COL_TOOL_CALLS = "alfred_tool_calls"

_sem = threading.BoundedSemaphore(MAX_CONCURRENT)
_health_cache = {"at": 0.0, "data": None}
_health_lock = threading.Lock()
_indexes_done = False

TOOL_LABELS = {
    "cockpit_lieux": "Horaires lieux",
    "cockpit_frequentation": "Fréquentation",
    "cockpit_situation": "Situation",
    "cockpit_main_courante": "Main courante",
    "cockpit_agenda": "Agenda",
    "cockpit_main_courante_fiches": "Main courante",
    "cockpit_main_courante_fiche": "Fiche",
    "cockpit_main_courante_compteurs": "Compteurs MC",
    "cockpit_trafic": "Trafic",
    "cockpit_meteo": "Meteo",
    "cockpit_alertes": "Alertes",
    "cockpit_timeline": "Timeline",
    "cockpit_presents": "Presents",
    "cockpit_wiki_procedures": "Wiki",
    "cockpit_evenement": "Evenement",
}

ERREURS = {
    "secret_not_configured": "Alfred n'est pas configure sur ce serveur (secret manquant).",
    "alfred_ask_unreachable": "Alfred est injoignable (VM ou reseau).",
    "ollama_unreachable": "Le modele d'Alfred est indisponible pour le moment.",
    "global_timeout": "Alfred a mis trop de temps a repondre.",
    "invalid_response": "Reponse illisible d'Alfred.",
    "file_pleine": "Alfred est tres sollicite, reessayez dans un instant.",
    "expire": "La reponse n'est jamais arrivee (serveur redemarre ?).",
}


# ---------------------------------------------------------------------------
# Acces
# ---------------------------------------------------------------------------

def _db():
    from app import db
    return db


def _role_required(role):
    """role_required d'app.py, resolu a l'appel (cf. musee_api.py)."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


def _chat_required(f):
    """Role user ET groupe autorisant le chat (Configuration > Groupes)."""
    @wraps(f)
    @_role_required("user")
    def wrapper(*args, **kwargs):
        from app import user_can_alfred_chat
        payload = getattr(request, "user_payload", {}) or {}
        if not user_can_alfred_chat(payload):
            return jsonify({"ok": False, "error": "non_autorise"}), 403
        return f(*args, **kwargs)
    return wrapper


def _me():
    p = getattr(request, "user_payload", {}) or {}
    name = ("%s %s" % (p.get("firstname", ""), p.get("lastname", ""))).strip()
    return (p.get("email") or "").strip().lower(), name, p


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ensure_indexes(db):
    global _indexes_done
    if _indexes_done:
        return
    ttl = RETENTION_DAYS * 86400
    try:
        db[COL_MESSAGES].create_index("created_at", expireAfterSeconds=ttl)
        db[COL_MESSAGES].create_index([("session_id", 1), ("created_at", 1)])
        db[COL_MESSAGES].create_index([("user_email", 1), ("status", 1), ("created_at", -1)])
        db[COL_SESSIONS].create_index("updated_at", expireAfterSeconds=ttl)
        db[COL_SESSIONS].create_index([("user_email", 1), ("updated_at", -1)])
        db[COL_TOOL_CALLS].create_index("created_at", expireAfterSeconds=ttl)
        _indexes_done = True
    except Exception as exc:
        logger.warning("alfred_chat : index (%s)", exc)


# ---------------------------------------------------------------------------
# Jeton de portee (Cockpit -> VM -> Cockpit)
# ---------------------------------------------------------------------------

def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_scope(email, event, year, cat_query, alert_slugs, now=None, mc_tout=False):
    """mc_tout : main courante de tout le PC (epreuve + SAISON, PCO et PCS),
    reserve aux admins ; sinon la regle de l'ecran pour l'evenement choisi."""
    if not TOOLS_SECRET:
        return None
    d = {"e": email, "ev": event, "yr": year, "c": cat_query,
         "a": alert_slugs, "x": int((now or time.time()) + SCOPE_TTL_S)}
    if mc_tout:
        d["mt"] = 1
    body = json.dumps(d, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(TOOLS_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return _b64(body) + "." + sig


def read_scope(token, now=None):
    """dict ctx d'outil, ou None si invalide/expire."""
    if not TOOLS_SECRET or not token or "." not in str(token):
        return None
    b, sig = str(token).rsplit(".", 1)
    try:
        body = _unb64(b)
    except Exception:
        return None
    attendu = hmac.new(TOOLS_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(attendu, sig):
        return None
    try:
        d = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    if int(d.get("x") or 0) < (now or time.time()):
        return None
    return {"email": d.get("e"), "event": d.get("ev"), "year": d.get("yr"),
            "cat_query": d.get("c"), "alert_slugs": d.get("a"),
            "mc_tout": bool(d.get("mt"))}


def _scope_for(payload, event, year):
    """Portee de l'operateur, calculee avec les helpers de droits d'app.py."""
    from app import _get_user_alert_slugs, _pcorg_cat_query
    # Main courante : un operateur voit ce que montre l'ecran pour l'evenement
    # choisi ; un ADMIN voit tout le PC (epreuve + SAISON, PCO et PCS),
    # decision d'exploitation du 08/10/2026.
    admin = "admin" in (payload.get("roles") or [])
    try:
        cat_q = {"$regex": "^PC[OS]\\."} if admin else _pcorg_cat_query(payload, event)
    except Exception:
        cat_q = {"$in": []}  # droit illisible : rien plutot que tout
    try:
        slugs = _get_user_alert_slugs(payload)
    except Exception:
        slugs = []
    return make_scope((payload.get("email") or "").lower(), event, year, cat_q, slugs,
                      mc_tout=admin)


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

def _current_session(db, email):
    return db[COL_SESSIONS].find_one({"user_email": email, "closed_at": None},
                                     sort=[("updated_at", -1)])


def _label_tool(name):
    n = str(name or "")
    if n in TOOL_LABELS:
        return TOOL_LABELS[n]
    for pre in ("cockpit_", "query_", "get_", "search_"):
        if n.startswith(pre):
            n = n[len(pre):]
    return n.replace("_", " ").strip().capitalize() or "Outil"


def _sources(tool_calls):
    out, vus = [], set()
    for tc in tool_calls or []:
        name = tc.get("name") if isinstance(tc, dict) else None
        if not name or name in vus:
            continue
        vus.add(name)
        out.append({"name": name, "label": _label_tool(name)})
    return out


def _iso(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _pub(m):
    return {
        "id": m["_id"], "role": m.get("role"), "content": m.get("content") or "",
        "status": m.get("status"), "error": m.get("error"),
        "sources": _sources(m.get("tool_calls")),
        "hops": m.get("hops"), "duration_ms": m.get("duration_ms"),
        "created_at": _iso(m.get("created_at")), "rating": m.get("rating"),
        "rating_motif": m.get("rating_motif"),
    }


def _expire_if_stale(db, m):
    """Un thread perdu (redemarrage) laisserait le message 'pending' a vie."""
    if m.get("status") not in ("queued", "running"):
        return m
    limite = QUEUE_WAIT_S + CHAT_TIMEOUT_S + 60
    if (_now() - m["created_at"]).total_seconds() > limite:
        db[COL_MESSAGES].update_one({"_id": m["_id"], "status": m["status"]},
                                    {"$set": {"status": "error", "error_code": "expire",
                                              "error": ERREURS["expire"],
                                              "finished_at": _now()}})
        m = dict(m, status="error", error=ERREURS["expire"])
    return m


def _session_messages(db, session_id, limit=80):
    docs = list(db[COL_MESSAGES].find({"session_id": session_id})
                .sort("created_at", -1).limit(limit))
    docs.reverse()
    return [_expire_if_stale(db, d) for d in docs]


def _wrapper_messages(db, session_id):
    """Historique au format du wrapper : alterne, finit sur l'utilisateur.
    Les reponses en erreur sont omises (et les questions qui se suivent
    alors fusionnees), comme _build_respond_messages cote WhatsApp."""
    raw = []
    for m in db[COL_MESSAGES].find({"session_id": session_id}).sort("created_at", 1):
        if m.get("role") == "assistant" and m.get("status") != "done":
            continue
        c = (m.get("content") or "").strip()
        if c:
            raw.append({"role": m["role"], "content": c})
    merged = []
    for msg in raw:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] += "\n" + msg["content"]
        else:
            merged.append(dict(msg))
    while merged and merged[-1]["role"] != "user":
        merged.pop()
    merged = merged[-HISTORY_MESSAGES:]
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged


def _context_line(ctx):
    now = datetime.now(TZ_PARIS).strftime("%d/%m/%Y %H:%M")
    morceaux = ["Operateur : %s (PC Organisation, via Cockpit)" % (ctx.get("user_name") or "?")]
    if ctx.get("event"):
        morceaux.append("Evenement selectionne : %s %s" % (ctx["event"], ctx.get("year") or ""))
    if ctx.get("page"):
        morceaux.append("Page Cockpit : %s" % ctx["page"])
    morceaux.append("Heure de Paris : %s" % now)
    return "[Contexte Cockpit, a ne pas recopier] " + " ; ".join(morceaux) + "."


def _friendly_error(code):
    c = str(code or "")
    if c in ERREURS:
        return ERREURS[c]
    low = c.lower()
    if "401" in low or "sig" in low or "auth" in low or "timestamp" in low:
        return "Signature refusee par Alfred (secret ou horloge NTP a verifier)."
    if "504" in low or "timeout" in low:
        return ERREURS["global_timeout"]
    if "503" in low:
        return ERREURS["ollama_unreachable"]
    return "Alfred n'a pas pu repondre (%s)." % (c or "erreur")


def _clean_tool_calls(tcs):
    out = []
    for tc in (tcs or [])[:20]:
        if not isinstance(tc, dict):
            continue
        args = tc.get("arguments", tc.get("args"))
        try:
            s = json.dumps(args, ensure_ascii=False, default=str)
        except Exception:
            s = str(args)
        out.append({"name": str(tc.get("name") or "")[:80], "args": s[:500]})
    return out


def _run_ask(msg_id, session_id, ctx, is_new, scope):
    db = _db()
    col = db[COL_MESSAGES]
    if not _sem.acquire(timeout=QUEUE_WAIT_S):
        col.update_one({"_id": msg_id}, {"$set": {
            "status": "error", "error_code": "file_pleine",
            "error": ERREURS["file_pleine"], "finished_at": _now()}})
        return
    try:
        col.update_one({"_id": msg_id}, {"$set": {"status": "running", "started_at": _now()}})
        messages = _wrapper_messages(db, session_id)
        if not messages:
            raise RuntimeError("historique vide")
        if CONTEXT_MODE in ("prefix", "both"):
            messages[-1] = {"role": "user",
                            "content": _context_line(ctx) + "\n\n" + messages[-1]["content"]}
        extra = None
        if CONTEXT_MODE in ("field", "both"):
            extra = {"channel": "cockpit", "context": {
                "user_name": ctx.get("user_name"), "user_email": ctx.get("user_email"),
                "event": ctx.get("event"), "year": ctx.get("year"),
                "page": ctx.get("page"), "path": ctx.get("path"),
                "now": datetime.now(TZ_PARIS).isoformat(timespec="minutes"),
                # Cle de memoire cote wrapper (resultats d'outils des tours
                # precedents). Stable sur toute la conversation, unique par
                # operateur : un hash du 1er message changerait des que
                # l'historique glisse (6 messages gardes) et collisionnerait
                # entre deux operateurs qui ouvrent par "Bonjour".
                "conversation_id": session_id,
                "turn_id": msg_id,
                "scope": scope}}
        t0 = time.time()
        ok, res = alfred._alfred_ask(messages=messages, max_tool_hops=MAX_TOOL_HOPS,
                                     request_id="chat-" + msg_id[:12],
                                     is_new_mention=is_new, extra=extra,
                                     timeout=CHAT_TIMEOUT_S)
        if ok and res.get("response"):
            col.update_one({"_id": msg_id}, {"$set": {
                "status": "done", "content": res["response"],
                "tool_calls": _clean_tool_calls(res.get("tool_calls")),
                "hops": res.get("hops"), "model": res.get("model"),
                "duration_ms": res.get("duration_ms") or int((time.time() - t0) * 1000),
                "finished_at": _now()}})
        else:
            code = res if not ok else "reponse_vide"
            col.update_one({"_id": msg_id}, {"$set": {
                "status": "error", "error_code": str(code)[:80],
                "error": _friendly_error(code), "finished_at": _now(),
                "duration_ms": int((time.time() - t0) * 1000)}})
    except BaseException as exc:
        logger.exception("alfred_chat : question %s", msg_id)
        col.update_one({"_id": msg_id}, {"$set": {
            "status": "error", "error_code": "interne",
            "error": "Erreur interne Cockpit : %s" % str(exc)[:120], "finished_at": _now()}})
    finally:
        _sem.release()
        db[COL_SESSIONS].update_one({"_id": session_id}, {"$set": {"updated_at": _now()}})


# ---------------------------------------------------------------------------
# Sante du wrapper
# ---------------------------------------------------------------------------

def health(force=False):
    with _health_lock:
        if not force and _health_cache["data"] and time.time() - _health_cache["at"] < HEALTH_TTL_S:
            return _health_cache["data"]
    t0 = time.time()
    try:
        r = requests.get(HEALTH_URL, timeout=(2, 3))
        j = r.json() if r.status_code == 200 else {}
        data = {"ok": bool(j.get("ok")), "ollama": j.get("ollama"), "mongo": j.get("mongo"),
                "http": r.status_code}
    except Exception as exc:
        data = {"ok": False, "error": type(exc).__name__}
    data["latency_ms"] = int((time.time() - t0) * 1000)
    data["configured"] = bool(alfred.ALFRED_ASK_SECRET)
    data["checked_at"] = datetime.now(timezone.utc).isoformat()
    with _health_lock:
        _health_cache.update(at=time.time(), data=data)
    return data


# ---------------------------------------------------------------------------
# Routes chat
# ---------------------------------------------------------------------------

@alfred_chat_bp.route("/api/alfred-chat/state", methods=["GET"])
@_chat_required
def chat_state():
    db = _db()
    _ensure_indexes(db)
    email, name, _p = _me()
    s = _current_session(db, email)
    msgs = _session_messages(db, s["_id"]) if s else []
    return jsonify({
        "ok": True, "session_id": s["_id"] if s else None,
        "messages": [_pub(m) for m in msgs],
        "busy": any(m.get("status") in ("queued", "running") for m in msgs),
        "user": {"name": name},
        "health": health(),
    })


@alfred_chat_bp.route("/api/alfred-chat/ask", methods=["POST"])
@_chat_required
def chat_ask():
    db = _db()
    _ensure_indexes(db)
    email, name, payload = _me()
    data = request.get_json(silent=True) or {}
    content = str(data.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "message_vide"}), 400
    if len(content) > CONTENT_MAX:
        return jsonify({"ok": False, "error": "message_trop_long", "max": CONTENT_MAX}), 400

    col = db[COL_MESSAGES]
    if col.find_one({"user_email": email, "status": {"$in": ["queued", "running"]},
                     "created_at": {"$gte": _now() - timedelta(minutes=3)}}):
        return jsonify({"ok": False, "error": "occupe",
                        "message": "Alfred repond deja a votre question precedente."}), 409
    if col.count_documents({"user_email": email, "role": "user",
                            "created_at": {"$gte": _now() - timedelta(minutes=10)}}) >= RATE_10MIN:
        return jsonify({"ok": False, "error": "trop_de_questions"}), 429

    raw_ctx = data.get("context") if isinstance(data.get("context"), dict) else {}
    event = str(raw_ctx.get("event") or "").strip()[:80] or None
    year = str(raw_ctx.get("year") or "").strip()[:8] or None
    if not event:
        try:
            import event_courant
            ev, yr = event_courant.current_event(db)
            event, year = ev, str(yr)
        except Exception:
            pass
    ctx = {"user_email": email, "user_name": name, "event": event, "year": year,
           "page": str(raw_ctx.get("page") or "")[:80],
           "path": str(raw_ctx.get("path") or "")[:120]}

    s = _current_session(db, email)
    is_new = s is None or not col.find_one({"session_id": s["_id"], "role": "assistant",
                                            "status": "done"})
    now = _now()
    if s is None:
        s = {"_id": uuid.uuid4().hex, "user_email": email, "user_name": name,
             "title": content[:80], "created_at": now, "updated_at": now, "closed_at": None}
        db[COL_SESSIONS].insert_one(s)
    else:
        db[COL_SESSIONS].update_one({"_id": s["_id"]}, {"$set": {"updated_at": now}})

    user_msg = {"_id": uuid.uuid4().hex, "session_id": s["_id"], "user_email": email,
                "role": "user", "content": content, "status": "done",
                "context": ctx, "created_at": now}
    asst_msg = {"_id": uuid.uuid4().hex, "session_id": s["_id"], "user_email": email,
                "role": "assistant", "content": "", "status": "queued",
                "created_at": now + timedelta(milliseconds=1)}
    col.insert_one(user_msg)
    col.insert_one(asst_msg)

    scope = _scope_for(payload, event, year)
    threading.Thread(target=_run_ask, args=(asst_msg["_id"], s["_id"], ctx, is_new, scope),
                     daemon=True, name="alfred-chat-" + asst_msg["_id"][:8]).start()
    return jsonify({"ok": True, "session_id": s["_id"],
                    "user_message": _pub(user_msg), "assistant_message": _pub(asst_msg)})


@alfred_chat_bp.route("/api/alfred-chat/message/<mid>", methods=["GET"])
@_chat_required
def chat_message(mid):
    db = _db()
    email, _n, _p = _me()
    m = db[COL_MESSAGES].find_one({"_id": str(mid), "user_email": email})
    if not m:
        return jsonify({"ok": False, "error": "introuvable"}), 404
    return jsonify({"ok": True, "message": _pub(_expire_if_stale(db, m))})


@alfred_chat_bp.route("/api/alfred-chat/new", methods=["POST"])
@_chat_required
def chat_new():
    db = _db()
    email, _n, _p = _me()
    db[COL_SESSIONS].update_many({"user_email": email, "closed_at": None},
                                 {"$set": {"closed_at": _now(), "updated_at": _now()}})
    return jsonify({"ok": True, "session_id": None, "messages": []})


@alfred_chat_bp.route("/api/alfred-chat/feedback/<mid>", methods=["POST"])
@_chat_required
def chat_feedback(mid):
    db = _db()
    email, _n, _p = _me()
    data = request.get_json(silent=True) or {}
    try:
        rating = int(data.get("rating"))
    except (TypeError, ValueError):
        rating = 0
    if rating not in (-1, 0, 1):
        return jsonify({"ok": False, "error": "note_invalide"}), 400
    import alfred_retours as AR
    motif = str(data.get("motif") or "").strip() or None
    if motif and (rating != -1 or motif not in AR.MOTIFS):
        return jsonify({"ok": False, "error": "motif_invalide"}), 400
    m = db[COL_MESSAGES].find_one_and_update(
        {"_id": str(mid), "user_email": email, "role": "assistant", "status": "done"},
        {"$set": {"rating": rating or None, "rating_motif": motif,
                  "rating_comment": str(data.get("comment") or "").strip()[:AR.COMMENTAIRE_MAX] or None,
                  "rated_at": _now()}},
        return_document=True)
    if not m:
        return jsonify({"ok": False, "error": "introuvable"}), 404
    # Instantane hors purge 90 j : c'est lui que lit la page Retours Alfred.
    try:
        if rating:
            AR.figer(db, m)
        else:
            AR.retirer(db, m["_id"])
    except Exception:
        logger.exception("alfred_chat : instantane du retour %s", mid)
    return jsonify({"ok": True, "message": _pub(m), "motifs": AR.MOTIFS})


@alfred_chat_bp.route("/api/alfred-chat/sessions", methods=["GET"])
@_chat_required
def chat_sessions():
    db = _db()
    email, _n, _p = _me()
    docs = list(db[COL_SESSIONS].find({"user_email": email}).sort("updated_at", -1).limit(30))
    return jsonify({"ok": True, "sessions": [
        {"id": d["_id"], "title": d.get("title") or "", "updated_at": _iso(d.get("updated_at")),
         "current": d.get("closed_at") is None,
         "count": db[COL_MESSAGES].count_documents({"session_id": d["_id"], "role": "user"})}
        for d in docs]})


@alfred_chat_bp.route("/api/alfred-chat/session/<sid>", methods=["GET"])
@_chat_required
def chat_session(sid):
    db = _db()
    email, _n, _p = _me()
    s = db[COL_SESSIONS].find_one({"_id": str(sid), "user_email": email})
    if not s:
        return jsonify({"ok": False, "error": "introuvable"}), 404
    return jsonify({"ok": True, "session": {"id": s["_id"], "title": s.get("title"),
                                            "current": s.get("closed_at") is None},
                    "messages": [_pub(m) for m in _session_messages(db, s["_id"], 200)]})


@alfred_chat_bp.route("/api/alfred-chat/health", methods=["GET"])
@_chat_required
def chat_health():
    return jsonify(health(force=request.args.get("force") == "1"))


# ---------------------------------------------------------------------------
# Contexte des evenements (page Configuration, admin)
# ---------------------------------------------------------------------------

@alfred_chat_bp.route("/api/alfred-chat/contextes", methods=["GET"])
@_role_required("admin")
def contextes_liste():
    import alfred_evenements as AE
    db = _db()
    docs = AE.contextes(db)
    annees = {}
    for d in db["parametrages"].find({}, {"event": 1, "year": 1}):
        if d.get("event") and str(d.get("year") or "").isdigit():
            annees.setdefault(d["event"], set()).add(str(d["year"]))
    out = []
    for e in AE.catalogue(db):
        if AE.norm(e["nom"]) == "saison":
            continue
        c = docs.get(e["nom"]) or {}
        out.append({"event": e["nom"], "short": e.get("short"),
                    "description": c.get("description") or "",
                    "surnoms": c.get("surnoms") or [],
                    "editions": c.get("editions") or {},
                    "annees": sorted(annees.get(e["nom"], set()), reverse=True),
                    "updated_at": _iso(c.get("updated_at")), "updated_by": c.get("updated_by")})
    out.sort(key=lambda x: (not (x["description"] or x["surnoms"]), x["event"]))
    return jsonify({"ok": True, "evenements": out,
                    "limites": {"description": AE.DESCRIPTION_MAX, "note": AE.NOTE_MAX,
                                "surnoms": AE.SURNOMS_MAX}})


@alfred_chat_bp.route("/api/alfred-chat/contexte", methods=["POST"])
@_role_required("admin")
def contexte_enregistrer():
    import alfred_evenements as AE
    db = _db()
    data = request.get_json(silent=True) or {}
    noms = {e["nom"] for e in AE.catalogue(db)}
    if str(data.get("event") or "").strip() not in noms:
        return jsonify({"ok": False, "error": "evenement_inconnu"}), 400
    doc, err = AE.nettoyer(data)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    conflits = AE.conflits_surnoms(db, doc)
    if conflits:
        return jsonify({"ok": False, "error": "surnom_deja_pris", "conflits": conflits}), 409
    email, name, _p = _me()
    doc, err = AE.enregistrer(db, data, auteur=name or email)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    doc.pop("_id", None)
    doc["updated_at"] = _iso(doc.get("updated_at"))
    return jsonify({"ok": True, "contexte": doc})


# ---------------------------------------------------------------------------
# Retours des operateurs (page admin /alfred-retours)
# ---------------------------------------------------------------------------

@alfred_chat_bp.route("/alfred-retours", methods=["GET"])
@_role_required("admin")
def retours_page():
    from flask import render_template
    import alfred_retours as AR
    p = getattr(request, "user_payload", {}) or {}
    return render_template("alfred_retours.html", user_roles=p.get("roles", []),
                           motifs=AR.MOTIFS)


@alfred_chat_bp.route("/api/alfred-chat/retours", methods=["GET"])
@_role_required("admin")
def retours_liste():
    import alfred_retours as AR
    db = _db()
    AR.rattraper(db)
    q = AR.filtre(request.args.get("rating"), request.args.get("statut"),
                  request.args.get("motif"))
    docs = db[AR.COLLECTION].find(q).sort("rated_at", -1).limit(300)
    return jsonify({"ok": True, "retours": [AR.publier(d) for d in docs if d.get("rating")],
                    "compteurs": AR.compteurs(db), "motifs": AR.MOTIFS, "statuts": AR.STATUTS})


@alfred_chat_bp.route("/api/alfred-chat/retours/<rid>", methods=["POST"])
@_role_required("admin")
def retours_traiter(rid):
    import alfred_retours as AR
    email, name, _p = _me()
    doc, err = AR.traiter(_db(), rid, request.get_json(silent=True) or {}, name or email)
    if err:
        return jsonify({"ok": False, "error": err}), 404 if err == "introuvable" else 400
    return jsonify({"ok": True, "retour": AR.publier(doc)})


@alfred_chat_bp.route("/api/alfred-chat/retours/<rid>/supprimer", methods=["POST"])
@_role_required("admin")
def retours_supprimer(rid):
    import alfred_retours as AR
    r = _db()[AR.COLLECTION].delete_one({"_id": str(rid)})
    return jsonify({"ok": bool(r.deleted_count)}), 200 if r.deleted_count else 404


@alfred_chat_bp.route("/api/alfred-chat/retours/export", methods=["GET"])
@_role_required("admin")
def retours_export():
    from flask import Response
    import alfred_retours as AR
    q = AR.filtre(request.args.get("rating"), request.args.get("statut"),
                  request.args.get("motif"))
    nom = "alfred_retours_%s.jsonl" % datetime.now(TZ_PARIS).strftime("%Y%m%d_%H%M")
    return Response("".join(AR.exporter(_db(), q)), mimetype="application/x-ndjson",
                    headers={"Content-Disposition": "attachment; filename=%s" % nom})


# ---------------------------------------------------------------------------
# Routes outils (VM -> Cockpit)
# ---------------------------------------------------------------------------

def verify_hmac(raw_body, ts, sig, secret=None, now=None):
    """Meme recette que /alfred/ask. (ok, motif)."""
    secret = TOOLS_SECRET if secret is None else secret
    if not secret:
        return False, "secret_not_configured"
    try:
        t = int(str(ts or ""))
    except ValueError:
        return False, "timestamp_invalide"
    if abs((now or time.time()) - t) > HMAC_WINDOW_S:
        return False, "timestamp_hors_fenetre"
    attendu = "sha256=" + hmac.new(secret.encode("utf-8"),
                                   str(t).encode("utf-8") + b"." + (raw_body or b""),
                                   hashlib.sha256).hexdigest()
    if not hmac.compare_digest(attendu, str(sig or "")):
        return False, "signature_invalide"
    return True, None


def _unscoped_secret():
    """Secret de la vue complete, ou "" s'il est absent ou egal au secret
    normal (un seul secret pour les deux usages annulerait la separation)."""
    s = UNSCOPED_SECRET
    if s and s == TOOLS_SECRET:
        logger.error("alfred-tools : ALFRED_TOOLS_UNSCOPED_SECRET identique a "
                     "ALFRED_TOOLS_SECRET, vue complete desactivee")
        return ""
    return s


def _tools_auth():
    """(reponse_de_refus, mode). mode = "scoped" (ALFRED_TOOLS_SECRET : jeton
    de portee OBLIGATOIRE) ou "unscoped" (ALFRED_TOOLS_UNSCOPED_SECRET : vue
    PC Org complete, reservee au chemin WhatsApp du wrapper).

    C'est le SECRET qui decide de la vue, jamais un champ du corps : un
    request_id ou un canal se recopient, un secret non."""
    raw = request.get_data(cache=True) or b""
    ts = request.headers.get("X-Alfred-Timestamp")
    sig = request.headers.get("X-Alfred-Signature")
    unscoped = _unscoped_secret()
    if not TOOLS_SECRET and not unscoped:
        return (jsonify({"ok": False, "error": "secret_not_configured"}), 503), None
    motif = "secret_not_configured"
    if TOOLS_SECRET:
        ok, motif = verify_hmac(raw, ts, sig, secret=TOOLS_SECRET)
        if ok:
            return None, "scoped"
    if unscoped:
        ok, motif2 = verify_hmac(raw, ts, sig, secret=unscoped)
        if ok:
            return None, "unscoped"
        if motif == "secret_not_configured":
            motif = motif2
    logger.warning("alfred-tools : refus %s (%s)", motif, request.remote_addr)
    return (jsonify({"ok": False, "error": motif}), 401), None


@alfred_chat_bp.route("/api/alfred-tools/manifest", methods=["GET"])
def tools_manifest():
    refus, mode = _tools_auth()
    if refus:
        return refus
    return jsonify({"ok": True, "tools": alfred_tools.manifest(sans_main_courante=mode == "unscoped"),
                    "presentation": alfred_tools.presentation()})


@alfred_chat_bp.route("/api/alfred-tools/retours", methods=["GET"])
def tools_retours():
    """Synchro du corpus cote VM : retours crees ou modifies depuis `depuis`
    (ISO, UTC), export pseudonymise, pouces retires inclus (note null).
    Meme signature que /manifest (corps vide)."""
    refus, _mode = _tools_auth()
    if refus:
        return refus
    import alfred_retours as AR
    q = {}
    depuis = str(request.args.get("depuis") or "").strip()
    if depuis:
        try:
            d = datetime.fromisoformat(depuis.replace("Z", "+00:00"))
        except ValueError:
            return jsonify({"ok": False, "error": "depuis_invalide"}), 400
        if d.tzinfo:
            d = d.astimezone(timezone.utc).replace(tzinfo=None)
        q["maj_at"] = {"$gte": d}
    jusqu_a = datetime.now(timezone.utc)
    lignes = [json.loads(x) for x in AR.exporter(_db(), q, annules=True)]
    return jsonify({"ok": True, "retours": lignes, "jusqu_a": jusqu_a.isoformat()})


@alfred_chat_bp.route("/api/alfred-tools/call", methods=["POST"])
def tools_call():
    refus, mode = _tools_auth()
    if refus:
        return refus
    try:
        data = json.loads((request.get_data(cache=True) or b"{}").decode("utf-8") or "{}")
    except ValueError:
        return jsonify({"ok": False, "error": "json_invalide"}), 400
    name = str(data.get("tool") or data.get("name") or "")
    args = data.get("args", data.get("arguments")) or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    scope_token = data.get("scope")
    # Refus par defaut : signe avec ALFRED_TOOLS_SECRET, un appel SANS jeton
    # de portee est refuse, quel que soit son request_id (non signe, donc
    # sans valeur d'autorite). Seul le secret distinct de la vue complete
    # dispense du jeton. Un jeton fourni est toujours applique, meme en vue
    # complete : il ne peut que restreindre.
    if scope_token:
        ctx = read_scope(scope_token)
        if ctx is None:
            return jsonify({"ok": False, "error": "scope_invalide"}), 403
    elif mode == "unscoped":
        # Vue complete (WhatsApp) : jamais de main courante, cf. alfred_tools
        if name in alfred_tools.OUTILS_MAIN_COURANTE:
            return jsonify({"ok": False, "error": "hors_canal",
                            "message": "La main courante n'est pas consultable sur ce canal."}), 403
        ctx = {"sans_main_courante": True}
    else:
        return jsonify({"ok": False, "error": "scope_requis"}), 403
    db = _db()
    _ensure_indexes(db)
    t0 = time.time()
    ok, result = alfred_tools.call(db, name, args, ctx)
    ms = int((time.time() - t0) * 1000)
    # Journal Cockpit, a recouper ligne a ligne avec celui du wrapper par
    # request_id : arguments RECUS (pas ceux que le modele croit avoir passes)
    # et ce que l'outil en a deduit (evenement, public, lieux trouves).
    resolu = {k: result.get(k) for k in ("evenement", "annee", "public_demande", "trouve", "vue",
                                          "lieux_ouverts") if isinstance(result, dict) and k in result}
    if isinstance(result, dict) and isinstance(result.get("lieux"), list):
        resolu["lieux"] = [x.get("nom") for x in result["lieux"] if isinstance(x, dict)][:6]
    if isinstance(result, dict) and result.get("candidats"):
        resolu["candidats"] = result["candidats"][:6]
    if isinstance(result, dict) and isinstance(result.get("resolu"), dict):
        resolu.update(result["resolu"])   # cockpit_frequentation : vue, annees, jour
    logger.info("alfred-tools %s rid=%s auth=%s scoped=%s ok=%s %dms args=%s resolu=%s",
                name, str(data.get("request_id") or "-")[:40], mode, bool(scope_token), ok, ms,
                json.dumps(args, ensure_ascii=False, default=str)[:400],
                json.dumps(resolu, ensure_ascii=False, default=str)[:400])
    try:
        db[COL_TOOL_CALLS].insert_one({
            "tool": name, "args": json.dumps(args, ensure_ascii=False, default=str)[:500],
            "request_id": str(data.get("request_id") or "")[:40] or None,
            "scoped": bool(scope_token), "auth": mode, "email": ctx.get("email"),
            "resolu": resolu, "ok": ok, "duration_ms": ms, "created_at": _now(),
            # Ce que le modele a lu, pour juger une reponse notee (page Retours).
            "resume": (str(result.get("resume") or result.get("error") or "")[:2000] or None)
                      if isinstance(result, dict) else None})
    except Exception:
        pass
    status = 200 if ok else (404 if result.get("error") == "outil_inconnu" else 500)
    return jsonify({"ok": ok, "tool": name, "result": result, "duration_ms": ms}), status
