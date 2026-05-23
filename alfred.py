"""alfred.py - Agent IA local pour WhatsApp.

Pilote l'IA Alfred hebergee sur la VM Linux srv-safe-docker.aco.local
(192.168.254.36). Deux modes d'appel selon le besoin :

  1. Resumes periodiques de groupe (long-context summarization, pas de tool) :
     POST {OLLAMA_URL}/api/generate
     -> appelle Ollama directement, modele OLLAMA_MODEL, prompt monolithique.

  2. Mentions @alfred (questions factuelles, tool calling) :
     POST {ALFRED_ASK_URL}/alfred/ask
     -> appelle le wrapper HTTP `alfred_wrapper` cote VM Linux. Celui-ci
        gere la boucle tool calling (parametrages_tool, etc.) et renvoie
        un texte final. Auth HMAC partagee (ALFRED_ASK_SECRET).

Cockpit reste maitre :
  - de la config par groupe       (collection wa_alfred_config)
  - de l'ingestion des messages   (collection wa_inbound_messages)
  - du scheduler 20 min           (thread daemon background)
  - des alertes mots-cles         (cockpit_active_alerts via le moteur existant)
  - du stockage des resumes       (collection wa_alfred_summaries)

WAHA pousse les messages entrants via webhook HMAC : POST /api/wa/webhook.
"""

import hmac
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, jsonify, request, abort
from pymongo import ASCENDING, DESCENDING, MongoClient
from bson.objectid import ObjectId


log = logging.getLogger("alfred")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Ollama tourne sur la VM srv-safe-docker.aco.local sans auth (LAN prive).
# Cockpit appelle /api/generate directement pour les resumes (pas de tool).
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://srv-safe-docker.aco.local:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "alfred").strip()
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300"))  # qwen 3B : 30-120s sur un long resume
OLLAMA_MAX_MESSAGES = int(os.getenv("OLLAMA_MAX_MESSAGES", "400"))  # garde-fou num_ctx

# Wrapper HTTP cote VM Linux pour les questions factuelles avec tool calling.
# Boucle tool_calls / acces Mongo geres cote VM Qwen, Cockpit ne voit qu'un
# echange texte-pour-texte. Auth HMAC sur (timestamp + "." + body_raw).
ALFRED_ASK_URL = os.getenv(
    "ALFRED_ASK_URL", "http://srv-safe-docker.aco.local:5005/alfred/ask"
).rstrip("/")
ALFRED_ASK_SECRET = os.getenv("ALFRED_ASK_SECRET", "").strip()
ALFRED_ASK_TIMEOUT = int(os.getenv("ALFRED_ASK_TIMEOUT", "90"))

WAHA_WEBHOOK_SECRET = os.getenv("WAHA_WEBHOOK_SECRET", "").strip()

TZ_PARIS = ZoneInfo("Europe/Paris")

# Cle d'identification d'Alfred dans le texte. Insensible a la casse.
# Match : "alfred", "@alfred", "Alfred,", " alfred ".
MENTION_RE = re.compile(r"(?:^|\W)@?alfred\b", re.IGNORECASE)

# Mention native WhatsApp dans le body : "@33612345678", "@1234567890.123456" (LID).
# Doit etre precedee d'un espace ou debut de ligne pour eviter de couper un mail
# "user@example.com". On exige un chiffre apres "@" pour ne pas chevaucher MENTION_RE
# (qui gere les mentions textuelles alphabetiques type "@alfred").
_NATIVE_MENTION_RE = re.compile(r"(?:^|(?<=\s))@\d[\w.-]*")


def _clean_content_for_llm(body):
    """Nettoie un body WhatsApp avant injection dans messages[*].content.

    Retire les mentions ("@alfred", "@33612345678") et collapse les espaces.
    NE retire PAS d'horodatage ou prefixe "Nom:" : ces patterns ne devraient
    pas apparaitre dans le body original (ils etaient injectes artificiellement
    par l'ancienne fonction _build_respond_prompt, supprimee dans cette version).
    """
    if not body:
        return ""
    s = MENTION_RE.sub("", str(body))
    s = _NATIVE_MENTION_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

# Cache pour le LID/numero WhatsApp d'Alfred (utilise pour matcher les mentions
# natives WhatsApp ou` le picker remplace "@alfred" par "@<lid>"). Stocke dans
# cockpit_wa_config.alfred_lid. Cache 5 minutes.
_alfred_lid_cache = (None, 0.0)
_alfred_lid_lock = threading.Lock()
ALFRED_LID_CACHE_TTL = 300


def _get_alfred_mention_ids():
    """Set des identifiants WhatsApp d'Alfred (LID + numero) reconnus dans
    mentionedJidList. Lu depuis cockpit_wa_config.alfred_lid (cache 5 min).
    """
    global _alfred_lid_cache
    with _alfred_lid_lock:
        val, ts = _alfred_lid_cache
        if val is not None and (time.time() - ts) < ALFRED_LID_CACHE_TTL:
            return val
        ids = set()
        try:
            db = _get_db()
            doc = db["cockpit_wa_config"].find_one(
                {"_id": "wa_config"}, {"alfred_lid": 1}
            ) or {}
            lid = (doc.get("alfred_lid") or "").strip()
            if lid:
                ids.add(lid)
                # Tolere le bare LID sans suffixe @lid
                if "@" in lid:
                    ids.add(lid.split("@", 1)[0])
        except Exception as e:
            log.warning("alfred: lecture alfred_lid failed : %s", e)
        _alfred_lid_cache = (ids, time.time())
        return ids

# Garde-fous anti-boucle : memoire process, OK pour 1 worker Waitress.
_mention_cooldown = {}   # chat_id -> ts epoch
MENTION_COOLDOWN_SECONDS = 5

# Followup conversation : apres une mention reussie, Alfred reste a l'ecoute
# des messages suivants du meme auteur dans le meme chat pendant N minutes,
# sans necessiter un nouveau @alfred. Chaque echange rafraichit la fenetre.
# Cle = (chat_id, from_id) -> les autres participants du groupe ne sont pas
# embarques dans la conversation par accident.
MENTION_FOLLOWUP_SECONDS = int(os.getenv("ALFRED_FOLLOWUP_SECONDS", "420"))  # 7 min
_followup_sessions = {}  # (chat_id, from_id) -> ts d'expiration
_followup_lock = threading.Lock()


def _followup_key(chat_id, from_id):
    return ((chat_id or ""), (from_id or ""))


def _is_in_followup(chat_id, from_id):
    if MENTION_FOLLOWUP_SECONDS <= 0 or not from_id:
        return False
    k = _followup_key(chat_id, from_id)
    with _followup_lock:
        exp = _followup_sessions.get(k)
        if exp and exp > time.time():
            return True
        if exp:
            _followup_sessions.pop(k, None)
    return False


def _refresh_followup(chat_id, from_id):
    if MENTION_FOLLOWUP_SECONDS <= 0 or not from_id:
        return
    k = _followup_key(chat_id, from_id)
    with _followup_lock:
        _followup_sessions[k] = time.time() + MENTION_FOLLOWUP_SECONDS


# ---------------------------------------------------------------------------
# Politique DM (messages prives)
# ---------------------------------------------------------------------------

# En DM, le webhook ne contient pas d'identifiant de groupe (@g.us). Alfred
# n'engage la conversation qu'avec une liste blanche de contacts autorises
# (cockpit_wa_config.dm_whitelist). Pour les autres, il envoie un message
# poli refusant la conversation sans l'accord de son Maitre.
DM_REFUSAL_COOLDOWN_SECONDS = int(os.getenv("ALFRED_DM_REFUSAL_COOLDOWN", "3600"))  # 1h
DM_REFUSAL_MESSAGE = (
    "Mes hommages, Monsieur. Je vous prie de m'excuser, mais je ne puis "
    "m'entretenir avec vous sans l'accord prealable de Maitre Bruce. "
    "Je transmettrai votre requete a la premiere occasion. Bien a vous, Alfred."
)
_dm_refusal_cooldown = {}  # chat_id -> ts d'envoi du dernier refus
_dm_refusal_lock = threading.Lock()


def _is_dm(chat_id):
    """True si chat_id est un message prive (pas un groupe @g.us)."""
    return bool(chat_id) and not str(chat_id).endswith("@g.us")


def _is_dm_whitelisted(chat_id):
    """Lit la whitelist DM dans cockpit_wa_config.dm_whitelist.

    Stocke en format riche : [{chat_id, label, added_at, added_by}, ...].
    Tolerant a l'ancien format (liste de strings) pour retro-compat.
    """
    if not chat_id:
        return False
    try:
        db = _get_db()
        doc = db["cockpit_wa_config"].find_one(
            {"_id": "wa_config"}, {"dm_whitelist": 1}
        ) or {}
        wl = doc.get("dm_whitelist") or []
        for it in wl:
            if isinstance(it, str) and it == chat_id:
                return True
            if isinstance(it, dict) and it.get("chat_id") == chat_id:
                return True
        return False
    except Exception as e:
        log.warning("alfred: lecture dm_whitelist failed : %s", e)
        return False


def list_dm_whitelist():
    """Retourne la liste des contacts autorises en DM (format riche)."""
    db = _get_db()
    doc = db["cockpit_wa_config"].find_one(
        {"_id": "wa_config"}, {"dm_whitelist": 1}
    ) or {}
    wl = doc.get("dm_whitelist") or []
    out = []
    for it in wl:
        if isinstance(it, str):
            out.append({"chat_id": it, "label": "", "added_at": None, "added_by": ""})
        elif isinstance(it, dict):
            ts = it.get("added_at")
            out.append({
                "chat_id": it.get("chat_id", ""),
                "label": it.get("label", ""),
                "added_at": ts.isoformat() if hasattr(ts, "isoformat") else (ts or None),
                "added_by": it.get("added_by", ""),
            })
    out.sort(key=lambda x: ((x.get("label") or "").lower(), x.get("chat_id") or ""))
    return out


def add_dm_whitelist(chat_id, label="", added_by=""):
    """Ajoute (ou met a jour) un contact autorise. Normalise un numero brut en
    <numero>@c.us. Refuse les chat_id de groupe (@g.us).
    """
    chat_id = (chat_id or "").strip()
    if not chat_id:
        raise ValueError("chat_id requis")
    if "@" not in chat_id:
        digits = "".join(ch for ch in chat_id if ch.isdigit())
        if not digits:
            raise ValueError("chat_id invalide (format attendu : <numero>@c.us ou <lid>@lid)")
        chat_id = "%s@c.us" % digits
    if chat_id.endswith("@g.us"):
        raise ValueError("la whitelist DM ne concerne pas les groupes (@g.us)")
    db = _get_db()
    entry = {
        "chat_id": chat_id,
        "label": (label or "").strip()[:80],
        "added_at": _now_utc(),
        "added_by": (added_by or "?")[:80],
    }
    # Retire les doublons (format dict ET ancien format string), puis ajoute
    db["cockpit_wa_config"].update_one(
        {"_id": "wa_config"},
        {"$pull": {"dm_whitelist": {"chat_id": chat_id}}},
    )
    db["cockpit_wa_config"].update_one(
        {"_id": "wa_config"},
        {"$pull": {"dm_whitelist": chat_id}},
    )
    db["cockpit_wa_config"].update_one(
        {"_id": "wa_config"},
        {"$push": {"dm_whitelist": entry}, "$set": {"updated_at": _now_utc()}},
        upsert=True,
    )
    return entry


def remove_dm_whitelist(chat_id):
    """Retire un contact de la whitelist. Gere les 2 formats."""
    chat_id = (chat_id or "").strip()
    if not chat_id:
        return False
    db = _get_db()
    r1 = db["cockpit_wa_config"].update_one(
        {"_id": "wa_config"},
        {"$pull": {"dm_whitelist": {"chat_id": chat_id}}},
    )
    r2 = db["cockpit_wa_config"].update_one(
        {"_id": "wa_config"},
        {"$pull": {"dm_whitelist": chat_id}},
    )
    return (r1.modified_count + r2.modified_count) > 0


def _maybe_send_dm_refusal(chat_id):
    """Envoie le message Pennyworth de refus, max une fois par chat et par heure."""
    if not chat_id:
        return
    with _dm_refusal_lock:
        last = _dm_refusal_cooldown.get(chat_id, 0)
        if time.time() - last < DM_REFUSAL_COOLDOWN_SECONDS:
            return
        _dm_refusal_cooldown[chat_id] = time.time()
    # Envoi en thread daemon pour ne pas bloquer la reponse webhook
    th = threading.Thread(
        target=_send_wa_text, args=(chat_id, DM_REFUSAL_MESSAGE), daemon=True
    )
    th.start()

# Plafonds de contexte pour le prompt Alfred.
MENTION_CONTEXT_MSGS = 20  # ~10 tours user/assistant pour le wrapper Alfred v3

# UX d'attente : si le wrapper met plus de N secondes a repondre (chargement
# du modele a froid, tool call lent, multi-hop), Alfred envoie une phrase
# d'interim sobre pour rassurer l'operateur. Les bavardages cache-chaud
# (~0.5-1.5s) ne declenchent jamais d'interim.
INTERIM_DELAY_SECONDS = 2.5
INTERIM_PHRASES = [
    "Un instant, Monsieur, je me renseigne.",
    "Permettez, Monsieur.",
    "Je m'en informe, Monsieur.",
]
SUMMARY_MIN_MESSAGES = 5
SUMMARY_MAX_MESSAGES = 400

# TTL d'ingestion (collection wa_inbound_messages purgee auto par Mongo).
INBOUND_TTL_DAYS = 14

# Scheduler : on tick toutes les 60 s, on declenche les groupes "summary"
# dont la fenetre est ecoulee.
SCHEDULER_INTERVAL_SECONDS = 60

# Collections
COL_CONFIG = "wa_alfred_config"
COL_INBOUND = "wa_inbound_messages"
COL_SUMMARIES = "wa_alfred_summaries"
COL_WA_GROUPS = "cockpit_wa_groups"        # deja peuplee par whatsapp_admin
COL_ACTIVE_ALERTS = "cockpit_active_alerts"  # cible des alertes mots-cles
COL_PARAMETRAGES = "parametrages"             # pour event/year actif


# ---------------------------------------------------------------------------
# MongoDB lazy
# ---------------------------------------------------------------------------

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_mongo_client = None
_mongo_db = None
_indexes_ready = False


def _get_db():
    global _mongo_client, _mongo_db
    if _mongo_db is None:
        _mongo_client = MongoClient(MONGO_URI)
        env = os.getenv("TITAN_ENV", "dev").strip().lower()
        _mongo_db = _mongo_client["titan" if env in {"prod", "production"} else "titan_dev"]
        _ensure_indexes(_mongo_db)
    return _mongo_db


def _ensure_indexes(db):
    global _indexes_ready
    if _indexes_ready:
        return
    try:
        db[COL_CONFIG].create_index("chat_id", unique=True)
        db[COL_INBOUND].create_index("msg_id", unique=True)
        db[COL_INBOUND].create_index([("chat_id", ASCENDING), ("timestamp", DESCENDING)])
        db[COL_INBOUND].create_index(
            "timestamp", expireAfterSeconds=INBOUND_TTL_DAYS * 86400
        )
        db[COL_SUMMARIES].create_index(
            [("chat_id", ASCENDING), ("period_start", DESCENDING)]
        )
        _indexes_ready = True
    except Exception as e:
        log.warning("alfred: index creation failed: %s", e)


# ---------------------------------------------------------------------------
# Helpers temps
# ---------------------------------------------------------------------------

def _now_utc():
    return datetime.now(timezone.utc)


def _to_dt(ts):
    """Convertit un timestamp WAHA (epoch en secondes ou ms) en datetime UTC."""
    if ts is None:
        return _now_utc()
    try:
        n = float(ts)
    except (TypeError, ValueError):
        return _now_utc()
    if n > 1e12:  # millisecondes
        n = n / 1000.0
    return datetime.fromtimestamp(n, tz=timezone.utc)


def _fmt_hhmm(dt):
    try:
        return dt.astimezone(TZ_PARIS).strftime("%H:%M")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# HMAC webhook
# ---------------------------------------------------------------------------

def _verify_webhook_hmac(raw_body, headers):
    """Verifie la signature HMAC du webhook WAHA.

    Tolerant : accepte tout header X-Webhook-Hmac* ou X-Hub-Signature*,
    teste SHA-256 / SHA-512 / SHA-1, et reconnait le format "algo=hex".
    En cas d'echec, log les details (tronques) pour debug.

    Si WAHA_WEBHOOK_SECRET est vide -> bypass total (dev only).
    """
    if not WAHA_WEBHOOK_SECRET:
        return True

    secret = WAHA_WEBHOOK_SECRET.encode("utf-8")

    candidates = []
    for h in headers.keys():
        lh = h.lower()
        if lh.startswith("x-webhook-hmac") or lh.startswith("x-hub-signature"):
            val = (headers.get(h) or "").strip().lower()
            for prefix in ("sha256=", "sha512=", "sha1="):
                if val.startswith(prefix):
                    val = val[len(prefix):]
                    break
            if val:
                candidates.append((h, val))

    expected = {
        "sha256": hmac.new(secret, raw_body, hashlib.sha256).hexdigest(),
        "sha512": hmac.new(secret, raw_body, hashlib.sha512).hexdigest(),
        "sha1":   hmac.new(secret, raw_body, hashlib.sha1).hexdigest(),
    }

    for _, sig in candidates:
        for exp in expected.values():
            if hmac.compare_digest(sig, exp):
                return True

    return False


# ---------------------------------------------------------------------------
# Ollama (appel direct, pas de wrapper)
# ---------------------------------------------------------------------------

def _format_messages_for_prompt(messages):
    """Tronque aux N derniers messages, formate '[ts] from: body'."""
    msgs = (messages or [])[-OLLAMA_MAX_MESSAGES:]
    lines = []
    for m in msgs:
        ts = (m.get("ts") or "?") if isinstance(m, dict) else "?"
        frm = (m.get("from") or "?") if isinstance(m, dict) else "?"
        body = (m.get("body") or "") if isinstance(m, dict) else ""
        lines.append("[%s] %s: %s" % (ts, frm, body))
    return "\n".join(lines)


def _build_respond_messages(history, alfred_ids):
    """Construit une liste OpenAI-style [{role, content}, ...] depuis l'historique.

    history : liste de docs wa_inbound_messages (ancien -> recent).
    alfred_ids : set de LIDs/numeros identifiant Alfred (cockpit_wa_config.alfred_lid).

    Regles :
      - role = "assistant" si from_me OR from_id dans alfred_ids, sinon "user"
      - content = body nettoye via _clean_content_for_llm
      - skip si content vide
      - fusion des messages consecutifs du meme role (Qwen prefere l'alternance)
      - tronque jusqu'au dernier message role=="user" (wrapper refuse sinon)
      - retourne [] si rien de valide
    """
    raw = []
    for m in history or []:
        body = m.get("body") or ""
        content = _clean_content_for_llm(body)
        if not content:
            continue
        # NE PAS utiliser from_me pour discriminer : sur un compte WhatsApp
        # business partage (Alfred + humain qui poste depuis le meme telephone),
        # WAHA marque from_me=True pour TOUS les messages sortants, y compris
        # ceux tapes manuellement par l'humain. Le seul discriminant fiable est :
        #   - source == "alfred_response" (marqueur pose par _persist_alfred_response)
        #   - OU from_id == LID Alfred exact (cockpit_wa_config.alfred_lid)
        from_id = str(m.get("from_id") or "")
        is_alfred = (
            m.get("source") == "alfred_response"
            or (from_id and from_id in (alfred_ids or set()))
        )
        role = "assistant" if is_alfred else "user"
        raw.append({"role": role, "content": content})

    # Fusion des messages consecutifs du meme role
    merged = []
    for msg in raw:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] = (merged[-1]["content"] + "\n" + msg["content"]).strip()
        else:
            merged.append(dict(msg))

    # Tronquer jusqu'au dernier role=="user" (le wrapper refuse une derniere
    # entree assistant : la conversation doit se terminer sur une question).
    while merged and merged[-1]["role"] != "user":
        merged.pop()

    return merged


def _build_summarize_prompt(chat_name, period_start, period_end, messages):
    formatted = _format_messages_for_prompt(messages)
    return (
        "Tu es Alfred. Messages WhatsApp du groupe \"%s\" entre %s et %s. "
        "Fais un compte-rendu structure en markdown francais avec ces sections : "
        "## Sujets traites, ## Decisions, ## Actions / TODO, ## Points d'attention. "
        "Si la conversation est uniquement du bavardage sans info operationnelle, "
        "ecris juste \"RAS\". Sois concis.\n\n%s"
    ) % (chat_name, period_start, period_end, formatted)


def _ollama_generate(prompt, num_predict=400, temperature=0.2):
    """Appelle Ollama /api/generate (stream=false). Retourne (ok, payload).

    payload (ok=True) : {"response": str, "model": str, "usage": {...}}
    payload (ok=False) : str (code d'erreur court)
    """
    url = "%s/api/generate" % OLLAMA_URL
    body = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": float(temperature),
            "num_predict": int(num_predict),
        },
    }
    try:
        r = requests.post(url, json=body, timeout=(5, OLLAMA_TIMEOUT))
        if r.status_code != 200:
            log.warning("ollama HTTP %d : %s", r.status_code, r.text[:200])
            return False, "http_%d" % r.status_code
        j = r.json()
        return True, {
            "response": j.get("response", ""),
            "model": j.get("model", OLLAMA_MODEL),
            "usage": {
                "eval_count": j.get("eval_count"),
                "eval_duration_ms": (j.get("eval_duration") or 0) // 1_000_000,
                "total_duration_ms": (j.get("total_duration") or 0) // 1_000_000,
            },
        }
    except requests.RequestException as e:
        log.warning("ollama injoignable (%s) : %s", url, e)
        return False, "ollama_unreachable"


def _alfred_ask(prompt=None, max_tool_hops=5, messages=None):
    """Appelle le wrapper /alfred/ask cote VM Linux pour une question avec
    tool calling. Retourne (ok, payload).

    Deux modes (mutuellement exclusifs) :
      - messages=[{role, content}, ...]  format conversation structuree (WhatsApp)
      - prompt="..."                     one-shot legacy (compat retro)

    payload (ok=True)  : {"response": str, "tool_calls": [...], "model": str,
                          "hops": int, "duration_ms": int}
    payload (ok=False) : str (code d'erreur court)

    Auth : HMAC-SHA256 sur (timestamp + "." + body_raw), cle ALFRED_ASK_SECRET
    partagee avec le wrapper. Fenetre anti-replay 5 min.
    """
    if not ALFRED_ASK_SECRET:
        log.error("alfred_ask: ALFRED_ASK_SECRET non configure")
        return False, "secret_not_configured"

    if messages is None and not prompt:
        log.error("alfred_ask: ni messages ni prompt fourni")
        return False, "missing_input"
    if messages is not None and prompt:
        log.warning("alfred_ask: messages ET prompt fournis, on garde messages")
        prompt = None

    body = {
        "max_tool_hops": int(max_tool_hops),
        "request_id": uuid.uuid4().hex[:12],
    }
    if messages is not None:
        body["messages"] = messages
    else:
        body["prompt"] = prompt
    body_raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    msg = ts.encode("utf-8") + b"." + body_raw
    sig = "sha256=" + hmac.new(
        ALFRED_ASK_SECRET.encode("utf-8"), msg, hashlib.sha256
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Alfred-Timestamp": ts,
        "X-Alfred-Signature": sig,
    }

    try:
        r = requests.post(
            ALFRED_ASK_URL, data=body_raw, headers=headers,
            timeout=(5, ALFRED_ASK_TIMEOUT),
        )
    except requests.RequestException as e:
        log.warning("alfred_ask: wrapper injoignable (%s) : %s", ALFRED_ASK_URL, e)
        return False, "alfred_ask_unreachable"

    try:
        j = r.json()
    except ValueError:
        log.warning("alfred_ask: reponse non-JSON HTTP %d : %s", r.status_code, r.text[:200])
        return False, "invalid_response"

    if r.status_code != 200 or not j.get("ok"):
        err = j.get("error") or ("http_%d" % r.status_code)
        log.warning("alfred_ask: KO (%s) detail=%s", err, j.get("detail", "")[:200])
        return False, err

    return True, {
        "response": (j.get("response") or "").strip(),
        "tool_calls": j.get("tool_calls") or [],
        "model": j.get("model") or "?",
        "hops": int(j.get("hops") or 0),
        "duration_ms": int(j.get("duration_ms") or 0),
    }


# ---------------------------------------------------------------------------
# WAHA reply helper (reutilise WhatsAppService existant)
# ---------------------------------------------------------------------------

def _send_wa_text(chat_id, text):
    """Envoi via WAHA en passant par WhatsAppService (heritage rate-limits/circuit).

    Retourne le msg_id WAHA en cas de succes, None sinon. Le msg_id est reutilise
    par _persist_alfred_response pour stocker la reponse Alfred dans
    wa_inbound_messages avec un id stable (idempotent si l'echo WAHA arrive).
    """
    try:
        from whatsapp import WhatsAppService  # import tardif : evite cycle
    except ImportError:
        log.error("alfred: whatsapp.py introuvable")
        return None
    db = _get_db()
    svc = WhatsAppService(db)
    if svc._is_circuit_open():
        log.info("alfred: circuit WAHA ouvert, reponse skip")
        return None
    msg_id = svc._send_text(chat_id, text)
    return msg_id or None


def _persist_alfred_response(chat_id, chat_name, text, msg_id=None,
                             event="", event_clean="", year=""):
    """Persiste une reponse Alfred dans wa_inbound_messages.

    Appelee apres un envoi WAHA reussi, INDEPENDAMMENT de listen/live_controle :
    sinon les tours assistant precedents seraient invisibles pour le builder
    _build_respond_messages des futures mentions (Qwen perdrait le referent et
    inventerait sur les follow-ups type "et pour les Motos ?").

    msg_id : si non None, utilise tel quel (idempotent si l'echo WAHA arrive
             plus tard avec le meme id). Sinon, genere "alfred-<uuid>".
    """
    db = _get_db()
    # from_id Alfred lu depuis cockpit_wa_config.alfred_lid (cache 5 min).
    alfred_ids = _get_alfred_mention_ids()
    alfred_from_id = next(iter(alfred_ids), "") if alfred_ids else ""

    final_msg_id = str(msg_id) if msg_id else ("alfred-" + uuid.uuid4().hex)
    doc = {
        "msg_id": final_msg_id,
        "chat_id": str(chat_id),
        "chat_name": chat_name or "",
        "from_id": alfred_from_id,
        "from_name": "Alfred",
        "from_me": True,
        "body": str(text or ""),
        "has_media": False,
        "timestamp": _now_utc(),
        "ingested_at": _now_utc(),
        "event": event,
        "event_clean": event_clean,
        "year": year,
        "mentioned_ids": [],
        "source": "alfred_response",  # marqueur pour distinguer de l'echo WAHA
    }
    try:
        db[COL_INBOUND].update_one(
            {"msg_id": final_msg_id},
            {"$setOnInsert": doc},
            upsert=True,
        )
        return final_msg_id
    except Exception as e:
        log.warning("alfred: persist response msg_id=%s : %s", final_msg_id, e)
        return None


# ---------------------------------------------------------------------------
# Config par groupe
# ---------------------------------------------------------------------------

DEFAULT_GROUP_CONFIG = {
    "listen": False,             # whitelist : ingere les messages dans Mongo
    "respond_mentions": False,   # repond aux @alfred
    "summary_enabled": False,    # genere un resume periodique
    "summary_interval_min": 20,
    "keyword_rules": [],         # [{id, regex, flags, priority, label}]
}


def _load_config(chat_id):
    db = _get_db()
    doc = db[COL_CONFIG].find_one({"chat_id": chat_id}) or {}
    merged = dict(DEFAULT_GROUP_CONFIG)
    merged.update({k: v for k, v in doc.items() if v is not None})
    merged["chat_id"] = chat_id
    return merged


def _is_listened(chat_id):
    db = _get_db()
    doc = db[COL_CONFIG].find_one({"chat_id": chat_id}, {"listen": 1})
    return bool(doc and doc.get("listen"))


# ---------------------------------------------------------------------------
# Etat live_controle (gate global d'ingestion)
# ---------------------------------------------------------------------------

# Le bouton "Live controle" de la page /live-controle pilote l'ingestion
# Alfred. Quand il est OFF on n'enregistre rien et on ne declenche aucune
# alerte. Quand il est ON, on stocke event/year sur chaque message pour
# pouvoir les relier a un evenement.
COL_DATA_ACCESS = "data_access"
LIVE_GLOBAL_ID = "___GLOBAL___"


def _live_controle_state():
    """Retourne (active, evenement, evenement_clean, year_str).

    year derive de l'annee Paris courante (meme convention que la route
    /api/live-controle/archive).
    """
    db = _get_db()
    doc = db[COL_DATA_ACCESS].find_one(
        {"_id": LIVE_GLOBAL_ID},
        {"live_controle_actif": 1, "evenement": 1, "evenement_clean": 1},
    ) or {}
    active = bool(doc.get("live_controle_actif"))
    evenement = (doc.get("evenement") or "").strip()
    evenement_clean = (doc.get("evenement_clean") or "").strip()
    year = str(datetime.now(TZ_PARIS).year) if active else ""
    return active, evenement, evenement_clean, year


# ---------------------------------------------------------------------------
# Ingestion d'un message entrant
# ---------------------------------------------------------------------------

def _build_message_doc(payload, chat_name=None, event="", event_clean="", year=""):
    """Construit un doc message a partir du payload WAHA, sans le stocker.

    Utilise pour les mentions @alfred quand live_controle est OFF : on a besoin
    du doc en memoire pour repondre, mais on ne le persiste pas.
    """
    msg_id = payload.get("id") or payload.get("_id") or ""
    if isinstance(msg_id, dict):
        msg_id = msg_id.get("_serialized", "") or msg_id.get("id", "")
    if not msg_id:
        msg_id = "noid-%s" % uuid.uuid4().hex

    chat_id = payload.get("from") or ""
    if isinstance(chat_id, dict):
        chat_id = chat_id.get("_serialized", "")

    # Mentions natives WhatsApp : exposees par WAHA dans _data.mentionedJidList.
    # Format : ["<lid>@lid", ...] ou ["<numero>@c.us", ...]. Vide si l'auteur
    # a tape "@alfred" en texte brut sans utiliser le picker WhatsApp.
    mentioned = []
    try:
        data = payload.get("_data") or {}
        raw = data.get("mentionedJidList") or []
        if isinstance(raw, list):
            mentioned = [str(x) for x in raw if x]
    except Exception:
        pass

    return {
        "msg_id": str(msg_id),
        "chat_id": str(chat_id),
        "chat_name": chat_name or "",
        "from_id": str(payload.get("participant") or payload.get("author") or payload.get("from") or ""),
        "from_name": str(payload.get("notifyName") or payload.get("_data", {}).get("notifyName") or ""),
        "from_me": bool(payload.get("fromMe")),
        "body": str(payload.get("body") or ""),
        "has_media": bool(payload.get("hasMedia")),
        "timestamp": _to_dt(payload.get("timestamp")),
        "ingested_at": _now_utc(),
        "event": event,
        "event_clean": event_clean,
        "year": year,
        "mentioned_ids": mentioned,
    }


def _ingest_message(payload, chat_name=None, event="", event_clean="", year=""):
    """Insere un message webhook dans wa_inbound_messages (idempotent sur msg_id).

    event/event_clean/year proviennent du live_controle actif au moment de la
    reception : ils permettent de relier le message a un evenement precis lors
    des analyses ultérieures.

    Retourne le doc tel qu'insere (ou existant), ou None en cas d'erreur.
    """
    doc = _build_message_doc(payload, chat_name, event, event_clean, year)
    db = _get_db()
    try:
        db[COL_INBOUND].update_one(
            {"msg_id": doc["msg_id"]},
            {"$setOnInsert": doc},
            upsert=True,
        )
    except Exception as e:
        log.warning("alfred: ingest msg_id=%s : %s", doc["msg_id"], e)
        return None
    return doc


# ---------------------------------------------------------------------------
# Handler : mention @alfred
# ---------------------------------------------------------------------------

def _process_mention_async(chat_id, message_doc, config):
    """Thread daemon : construit messages structures, appelle wrapper, repond via WAHA,
    persiste la reponse Alfred dans wa_inbound_messages."""
    try:
        db = _get_db()
        # Contexte = N derniers messages du chat (anciens d'abord)
        cur = (
            db[COL_INBOUND]
            .find({"chat_id": chat_id})
            .sort("timestamp", DESCENDING)
            .limit(MENTION_CONTEXT_MSGS)
        )
        history = list(cur)
        history.reverse()
        # Si live_controle est OFF, le message courant n'a pas ete persiste :
        # on l'ajoute manuellement au contexte sinon Alfred n'aurait litteralement
        # rien a quoi repondre.
        cur_id = message_doc.get("msg_id")
        if cur_id and not any(m.get("msg_id") == cur_id for m in history):
            history.append(message_doc)

        alfred_ids = _get_alfred_mention_ids()
        messages = _build_respond_messages(history, alfred_ids)
        if not messages:
            log.warning("alfred mention : aucun message valide apres nettoyage, skip")
            return
        if messages[-1]["role"] != "user":
            # _build_respond_messages tronque deja, mais defensif.
            log.warning("alfred mention : dernier message != user apres build, skip")
            return

        # UX d'attente : si le wrapper depasse INTERIM_DELAY_SECONDS, on envoie
        # une phrase sobre pour rassurer. Trois garde-fous :
        #   - flag `received` (Event) : evite l'envoi si la vraie reponse est
        #     arrivee entre le tick du timer et le debut du POST WAHA
        #   - `send_lock` : serialise interim et reponse finale pour garantir
        #     l'ordre chronologique cote WAHA (sinon les deux POST peuvent
        #     etre receptionnes dans le desordre selon la latence)
        #   - double-check du flag DANS la critical section : si la vraie
        #     reponse est prete et a acquis le lock juste avant, on n'envoie
        #     pas l'interim devenu obsolete
        received = threading.Event()
        send_lock = threading.Lock()

        def _send_interim():
            if received.is_set():
                return
            with send_lock:
                if received.is_set():
                    return
                try:
                    phrase = random.choice(INTERIM_PHRASES)
                    _send_wa_text(chat_id, phrase)
                    log.info("alfred mention : interim envoye (chat=%s)", chat_id)
                except Exception as e:
                    log.warning("alfred interim : echec envoi (%s)", e)

        interim_timer = threading.Timer(INTERIM_DELAY_SECONDS, _send_interim)
        interim_timer.daemon = True
        interim_timer.start()

        try:
            ok, resp = _alfred_ask(messages=messages, max_tool_hops=5)
        finally:
            received.set()
            interim_timer.cancel()

        if not ok:
            log.warning("alfred mention : echec wrapper (%s)", resp)
            return
        text = (resp or {}).get("response", "").strip()
        hops = (resp or {}).get("hops", 0)
        if hops:
            log.info("alfred mention : %d tool hop(s), duration=%dms",
                     hops, (resp or {}).get("duration_ms", 0))
        if not text:
            log.info("alfred mention : reponse vide, skip")
            return

        chat_name = config.get("chat_name") or message_doc.get("chat_name") or ""
        # Acquisition du lock partage avec _send_interim : si l'interim est en
        # cours d'envoi WAHA, on attend ici. Garantit que la vraie reponse
        # ne court-circuite pas l'interim cote reseau.
        with send_lock:
            sent_msg_id = _send_wa_text(chat_id, text)
        if not sent_msg_id:
            log.warning("alfred mention : echec envoi WAHA chat=%s", chat_id)
            return

        # Persiste la reponse pour que les follow-ups voient bien le tour
        # assistant precedent, independamment de listen/live_controle.
        _persist_alfred_response(
            chat_id, chat_name, text, msg_id=sent_msg_id,
            event=message_doc.get("event", ""),
            event_clean=message_doc.get("event_clean", ""),
            year=message_doc.get("year", ""),
        )
    except Exception as e:
        log.exception("alfred mention crash : %s", e)


def _maybe_trigger_mention(chat_id, message_doc, config, force_match=False):
    if not config.get("respond_mentions"):
        return
    body = message_doc.get("body") or ""
    from_id = message_doc.get("from_id") or ""

    # Quatre facons de declencher :
    #   1. force_match=True (DM autorise : mention implicite)
    #   2. Mention textuelle ("@alfred", "alfred ...")
    #   3. Mention native WhatsApp (mentionedJidList contient le LID Alfred)
    #   4. Suite de conversation : (chat, auteur) est en session followup
    matched = bool(force_match) or bool(MENTION_RE.search(body))
    if not matched:
        mentions = message_doc.get("mentioned_ids") or []
        if mentions:
            alfred_ids = _get_alfred_mention_ids()
            if alfred_ids and any(m in alfred_ids for m in mentions):
                matched = True
    if not matched and _is_in_followup(chat_id, from_id):
        matched = True
    if not matched:
        return
    # Cooldown anti-flood (par chat)
    last = _mention_cooldown.get(chat_id, 0)
    if time.time() - last < MENTION_COOLDOWN_SECONDS:
        return
    _mention_cooldown[chat_id] = time.time()
    # Refresh la session followup pour cet auteur, qu'il s'agisse d'une
    # mention initiale ou d'une suite de conversation.
    _refresh_followup(chat_id, from_id)
    th = threading.Thread(
        target=_process_mention_async,
        args=(chat_id, message_doc, config),
        daemon=True,
    )
    th.start()


# ---------------------------------------------------------------------------
# Handler : mot-cle -> alerte (cockpit_active_alerts)
# ---------------------------------------------------------------------------

def _compile_rule(rule):
    """Compile {regex, flags} en pattern. Renvoie None si invalide."""
    pat = (rule or {}).get("regex") or ""
    if not pat:
        return None
    flags = 0
    if "i" in (rule.get("flags") or "").lower():
        flags |= re.IGNORECASE
    try:
        return re.compile(pat, flags)
    except re.error as e:
        log.warning("alfred: regex invalide '%s' : %s", pat, e)
        return None


def _maybe_trigger_keywords(chat_id, message_doc, config):
    rules = config.get("keyword_rules") or []
    if not rules:
        return
    body = message_doc.get("body") or ""
    if not body:
        return
    db = _get_db()
    # Event/year viennent du live_controle au moment de l'ingestion : on les
    # relit sur le doc plutot que de refaire un lookup parametrages.
    event = message_doc.get("event") or ""
    year = message_doc.get("year") or ""
    now = _now_utc()
    chat_name = config.get("chat_name") or message_doc.get("chat_name") or chat_id
    for rule in rules:
        rid = rule.get("id") or rule.get("label") or rule.get("regex") or ""
        comp = _compile_rule(rule)
        if not comp or not comp.search(body):
            continue
        label = rule.get("label") or rule.get("regex") or "mot-cle"
        priority = int(rule.get("priority") or 3)
        sender = message_doc.get("from_name") or message_doc.get("from_id") or "?"
        dedup_key = "alfred-kw-%s-%s" % (rid, message_doc.get("msg_id", ""))
        alert_doc = {
            "definition_slug": "alfred-keyword",
            "alfred_rule_id": str(rid),
            "alfred_rule_label": label,
            "event": event,
            "year": year,
            "title": "WhatsApp %s : %s" % (chat_name, label),
            "message": "%s (%s) : %s" % (
                sender, _fmt_hhmm(message_doc.get("timestamp")),
                (body[:200] + "...") if len(body) > 200 else body,
            ),
            "timeStr": _fmt_hhmm(message_doc.get("timestamp")),
            "priority": priority,
            "actionData": {
                "source": "alfred",
                "chat_id": chat_id,
                "chat_name": chat_name,
                "msg_id": message_doc.get("msg_id"),
            },
            "dedup_key": dedup_key,
            "triggeredAt": now,
            "expiresAt": now + timedelta(hours=2),
        }
        try:
            db[COL_ACTIVE_ALERTS].update_one(
                {"dedup_key": dedup_key},
                {"$setOnInsert": alert_doc},
                upsert=True,
            )
            log.info("alfred keyword '%s' matched in %s", label, chat_name)
        except Exception as e:
            log.warning("alfred keyword insert failed (%s) : %s", dedup_key, e)


# ---------------------------------------------------------------------------
# Resumes periodiques
# ---------------------------------------------------------------------------

def _generate_summary_for_chat(chat_id):
    """Genere un resume des messages depuis last_summary_at (ou interval).

    Stocke dans wa_alfred_summaries + maj last_summary_at.
    """
    db = _get_db()
    cfg_doc = db[COL_CONFIG].find_one({"chat_id": chat_id}) or {}
    interval = int(cfg_doc.get("summary_interval_min") or 20)
    now = _now_utc()
    last = cfg_doc.get("last_summary_at")
    if last and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    period_start = last if last else (now - timedelta(minutes=interval))
    period_end = now

    msgs = list(
        db[COL_INBOUND]
        .find({
            "chat_id": chat_id,
            "timestamp": {"$gte": period_start, "$lt": period_end},
        })
        .sort("timestamp", ASCENDING)
        .limit(SUMMARY_MAX_MESSAGES)
    )
    if len(msgs) < SUMMARY_MIN_MESSAGES:
        log.info("alfred summary %s : %d messages < seuil %d, skip",
                 chat_id, len(msgs), SUMMARY_MIN_MESSAGES)
        # On bouge quand meme last_summary_at pour ne pas re-evaluer en boucle
        db[COL_CONFIG].update_one(
            {"chat_id": chat_id},
            {"$set": {"last_summary_at": now}},
        )
        return None

    chat_name = cfg_doc.get("chat_name") or (msgs[0].get("chat_name") if msgs else "")
    payload_msgs = [
        {
            "ts": _fmt_hhmm(m.get("timestamp")),
            "from": m.get("from_name") or m.get("from_id") or "?",
            "body": m.get("body", ""),
        }
        for m in msgs if (m.get("body") or "").strip()
    ]
    prompt = _build_summarize_prompt(
        chat_name, period_start.isoformat(), period_end.isoformat(), payload_msgs
    )
    ok, resp = _ollama_generate(prompt, num_predict=1200, temperature=0.1)
    if not ok:
        log.warning("alfred summary %s : ollama KO (%s)", chat_id, resp)
        return None

    text = (resp or {}).get("response", "").strip()
    if not text:
        log.warning("alfred summary %s : reponse vide", chat_id)
        return None

    doc = {
        "_id": ObjectId(),
        "chat_id": chat_id,
        "chat_name": chat_name,
        "period_start": period_start,
        "period_end": period_end,
        "msg_count": len(msgs),
        "raw_text": text,
        "model": (resp or {}).get("model") or OLLAMA_MODEL,
        "usage": (resp or {}).get("usage"),
        "created_at": now,
    }
    try:
        db[COL_SUMMARIES].insert_one(doc)
        db[COL_CONFIG].update_one(
            {"chat_id": chat_id},
            {"$set": {"last_summary_at": now}},
        )
        log.info("alfred summary %s ok (%d msgs, %d chars)", chat_id, len(msgs), len(text))
        return doc
    except Exception as e:
        log.warning("alfred summary %s : insert fail : %s", chat_id, e)
        return None


def _scheduler_tick():
    """Boucle declenchee chaque minute : check tous les groupes 'summary'.

    Les resumes ne tournent que si live_controle est actif (les messages a
    resumer dependent de l'ingestion, elle-meme gatee par live_controle).
    """
    live_active, _, _, _ = _live_controle_state()
    if not live_active:
        return
    db = _get_db()
    try:
        # listen=true est requis : sans ingestion, il n'y a rien a resumer.
        cursor = db[COL_CONFIG].find({"summary_enabled": True, "listen": True})
    except Exception as e:
        log.warning("alfred scheduler : lecture config failed : %s", e)
        return
    now = _now_utc()
    for cfg in cursor:
        chat_id = cfg.get("chat_id")
        if not chat_id:
            continue
        interval = int(cfg.get("summary_interval_min") or 20)
        last = cfg.get("last_summary_at")
        if last and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        due = (last is None) or (now - last >= timedelta(minutes=interval))
        if not due:
            continue
        # Lance dans un thread separe pour ne pas bloquer le tick si Alfred est lent
        th = threading.Thread(
            target=_generate_summary_for_chat,
            args=(chat_id,),
            daemon=True,
        )
        th.start()


_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_scheduler():
    """Demarre la boucle scheduler une seule fois (thread daemon)."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def _loop():
        log.info("alfred scheduler: started (interval %ds)", SCHEDULER_INTERVAL_SECONDS)
        while True:
            try:
                _scheduler_tick()
            except Exception as e:
                log.exception("alfred scheduler tick crash: %s", e)
            time.sleep(SCHEDULER_INTERVAL_SECONDS)

    t = threading.Thread(target=_loop, name="alfred-scheduler", daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

alfred_bp = Blueprint("alfred", __name__)


# ---- Webhook WAHA --------------------------------------------------------

@alfred_bp.route("/api/wa/webhook", methods=["POST"])
def wa_webhook():
    raw = request.get_data() or b""
    if not _verify_webhook_hmac(raw, request.headers):
        log.warning("alfred webhook: HMAC invalide (ip=%s)", request.remote_addr)
        abort(401)
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return jsonify(ok=False, error="invalid_json"), 400

    event = body.get("event") or ""
    payload = body.get("payload") or {}

    # On ne traite que les nouveaux messages. message.any inclut aussi les
    # messages envoyes (fromMe=true) -- on les ingere mais on ne reagit pas.
    if event not in ("message", "message.any"):
        return jsonify(ok=True, skipped="event_not_handled"), 200

    # WAHA met le chat_id dans `from` pour les messages recus, mais dans `to`
    # pour les messages envoyes par soi-meme (fromMe=true). On essaie aussi
    # `chatId` exposeed par certaines versions. On prefere toujours un id de
    # groupe (@g.us) s'il y en a un parmi les candidats.
    def _extract(raw):
        if isinstance(raw, dict):
            return raw.get("_serialized") or raw.get("id") or ""
        return str(raw or "")

    candidates = [
        _extract(payload.get("chatId")),
        _extract(payload.get("from")),
        _extract(payload.get("to")),
    ]
    group_ids = [c for c in candidates if c.endswith("@g.us")]
    chat_id = group_ids[0] if group_ids else next((c for c in candidates if c), "")

    if not chat_id:
        return jsonify(ok=True, skipped="no_chat_id"), 200

    # DMs : politique speciale. Whitelist obligatoire (dm_whitelist), sinon
    # message poli style Pennyworth + skip. On laisse passer les fromMe.
    is_dm = _is_dm(chat_id)
    is_from_me_pre = bool(payload.get("fromMe"))
    if is_dm and not is_from_me_pre and not _is_dm_whitelisted(chat_id):
        _maybe_send_dm_refusal(chat_id)
        return jsonify(ok=True, skipped="dm_not_whitelisted"), 200

    # Trois axes independants pour les groupes :
    #   - listen           -> ingestion en base (gatee aussi par live_controle)
    #   - respond_mentions -> Alfred repond aux @alfred (TOUJOURS, autonome)
    #   - summary_enabled  -> resumes periodiques (pilote cote scheduler)
    # En DM autorise, Alfred ecoute systematiquement et repond a tout message.
    cfg = _load_config(chat_id)
    if is_dm:
        cfg["respond_mentions"] = True
    elif not cfg.get("listen") and not cfg.get("respond_mentions"):
        return jsonify(ok=True, skipped="no_action_configured"), 200

    # Resolve nom du groupe
    db = _get_db()
    grp = db[COL_WA_GROUPS].find_one({"group_id": chat_id}, {"name": 1})
    chat_name = grp.get("name") if grp else ""
    cfg["chat_name"] = chat_name or cfg.get("chat_name")

    # Re-injecte le chat_id resolu dans le payload (WAHA peut avoir mis ton
    # numero dans `from` pour les messages fromMe=true).
    payload["from"] = chat_id

    # Stockage = listen ON ET live_controle ON. Sinon on construit un doc
    # volatile pour permettre une reponse aux mentions sans persister.
    live_active, event, event_clean, year = _live_controle_state()
    should_ingest = bool(cfg.get("listen")) and live_active
    if should_ingest:
        msg_doc = _ingest_message(
            payload, chat_name=chat_name,
            event=event, event_clean=event_clean, year=year,
        )
        if not msg_doc:
            return jsonify(ok=False, error="ingest_failed"), 500
    else:
        msg_doc = _build_message_doc(payload, chat_name=chat_name)

    # On ne reagit pas aux messages qu'on a envoyes nous-meme
    if msg_doc.get("from_me"):
        return jsonify(ok=True, ingested=should_ingest, fromMe=True), 200

    # Keywords : depend d'une ingestion reelle (besoin de event/year pour
    # rattacher l'alerte a un evenement). Pas d'ingestion = pas d'alerte.
    if should_ingest:
        try:
            _maybe_trigger_keywords(chat_id, msg_doc, cfg)
        except Exception as e:
            log.exception("alfred keyword pipeline crash : %s", e)

    # Mentions @alfred : autonomes, fonctionnent en permanence si la case
    # est cochee, independamment de listen et de live_controle.
    # En DM autorise (is_dm), tout message est une mention implicite.
    try:
        _maybe_trigger_mention(chat_id, msg_doc, cfg, force_match=is_dm)
    except Exception as e:
        log.exception("alfred mention pipeline crash : %s", e)

    return jsonify(ok=True, ingested=should_ingest), 200


# ---- Admin : config par groupe ------------------------------------------

# Les routes admin sont enregistrees apres role_required dans app.py via wrapper.
# Pour rester independant, on expose les fonctions et app.py les decore.

def list_configs():
    """Retourne la liste des groupes WAHA connus + leur config Alfred (merge)."""
    db = _get_db()
    groups = list(db[COL_WA_GROUPS].find({}, {"group_id": 1, "name": 1, "enabled": 1}))
    cfgs = {c["chat_id"]: c for c in db[COL_CONFIG].find({})}
    out = []
    for g in groups:
        gid = g.get("group_id") or ""
        c = cfgs.get(gid, {})
        merged = dict(DEFAULT_GROUP_CONFIG)
        for k, v in c.items():
            if v is not None and k not in {"_id"}:
                merged[k] = v
        merged["chat_id"] = gid
        merged["chat_name"] = g.get("name") or c.get("chat_name") or gid
        merged["wa_enabled"] = bool(g.get("enabled"))
        last = c.get("last_summary_at")
        merged["last_summary_at"] = last.isoformat() if isinstance(last, datetime) else None
        out.append(merged)
    out.sort(key=lambda x: (x.get("chat_name") or "").lower())
    return out


def upsert_config(chat_id, data, updated_by="?"):
    """Met a jour la config d'un groupe. Renvoie le doc fusionne."""
    db = _get_db()
    # Resolve nom du groupe pour le stocker (utile dans les resumes)
    grp = db[COL_WA_GROUPS].find_one({"group_id": chat_id}, {"name": 1})
    chat_name = grp.get("name") if grp else (data.get("chat_name") or chat_id)

    # Sanitize keyword_rules
    rules_in = data.get("keyword_rules") or []
    rules_out = []
    for r in rules_in:
        if not isinstance(r, dict):
            continue
        regex = (r.get("regex") or "").strip()
        if not regex:
            continue
        if _compile_rule(r) is None:
            continue
        rules_out.append({
            "id": r.get("id") or uuid.uuid4().hex[:10],
            "regex": regex,
            "flags": (r.get("flags") or "i").lower(),
            "label": (r.get("label") or regex)[:80],
            "priority": int(r.get("priority") or 3),
        })

    # summary_enabled requiert listen=true (sans ingestion, rien a resumer).
    listen = bool(data.get("listen"))
    summary_enabled = bool(data.get("summary_enabled")) and listen
    upd = {
        "chat_id": chat_id,
        "chat_name": chat_name,
        "listen": listen,
        "respond_mentions": bool(data.get("respond_mentions")),
        "summary_enabled": summary_enabled,
        "summary_interval_min": max(5, int(data.get("summary_interval_min") or 20)),
        "keyword_rules": rules_out,
        "updated_at": _now_utc(),
        "updated_by": updated_by,
    }
    db[COL_CONFIG].update_one(
        {"chat_id": chat_id},
        {"$set": upd},
        upsert=True,
    )
    return upd


# ---- Admin : resumes ----------------------------------------------------

def list_summaries(chat_id=None, limit=50):
    db = _get_db()
    q = {"chat_id": chat_id} if chat_id else {}
    cur = db[COL_SUMMARIES].find(
        q,
        {"raw_text": 0},  # gros, lazy
    ).sort("period_start", DESCENDING).limit(int(limit))
    out = []
    for d in cur:
        d["_id"] = str(d["_id"])
        for k in ("period_start", "period_end", "created_at"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].isoformat()
        out.append(d)
    return out


def get_summary(summary_id):
    db = _get_db()
    try:
        oid = ObjectId(summary_id)
    except Exception:
        return None
    d = db[COL_SUMMARIES].find_one({"_id": oid})
    if not d:
        return None
    d["_id"] = str(d["_id"])
    for k in ("period_start", "period_end", "created_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat()
    return d


def delete_summary(summary_id):
    db = _get_db()
    try:
        oid = ObjectId(summary_id)
    except Exception:
        return False
    r = db[COL_SUMMARIES].delete_one({"_id": oid})
    return r.deleted_count > 0


def trigger_summary_now(chat_id):
    """Force un resume immediat pour le groupe (utile pour tester depuis admin)."""
    th = threading.Thread(
        target=_generate_summary_for_chat, args=(chat_id,), daemon=True
    )
    th.start()
    return True


def clear_group_history(chat_id, deleted_by="?"):
    """Vide l'historique des messages WhatsApp ingeres pour un groupe.

    Supprime tous les docs wa_inbound_messages dont chat_id correspond, y compris
    les reponses Alfred persistees (source="alfred_response"). Les resumes deja
    generes dans wa_alfred_summaries ne sont PAS supprimes (ils restent
    consultables independamment).

    Use case : reset du contexte conversationnel quand Alfred derive sur des
    anciens echanges pollues, ou nettoyage volontaire de la base apres un test.

    Retourne le nombre de docs supprimes.
    """
    if not chat_id:
        return 0
    db = _get_db()
    res = db[COL_INBOUND].delete_many({"chat_id": str(chat_id)})
    log.info(
        "alfred: clear_group_history chat_id=%s deleted=%d by=%s",
        chat_id, res.deleted_count, deleted_by,
    )
    return int(res.deleted_count or 0)
