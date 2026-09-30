"""
whatsapp.py - Service de notification WhatsApp via WAHA pour COCKPIT.

Fournit l'integration avec l'API REST WAHA pour envoyer des alertes
sur des groupes WhatsApp et en messages directs.

Anti-ban : 10 couches de protection (rate limit, cooldown, agregation,
circuit breaker, delai variable, etc.)
"""

import os
import hmac
import hashlib
import base64
import time
import random
import logging
import threading
from datetime import datetime, timezone, timedelta

import requests

# Taille max base64 acceptee par WAHA pour sendImage (defaut ~5 MB binaire).
WAHA_SENDIMAGE_MAX_BYTES = int(os.environ.get("WAHA_SENDIMAGE_MAX_BYTES", str(5 * 1024 * 1024)))

log = logging.getLogger("whatsapp")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_WAHA_URL = "http://localhost:3000"
DEFAULT_SESSION = "default"
DEFAULT_RATE_LIMIT_HOUR = 20
DEFAULT_RATE_LIMIT_DAY = 100
DEFAULT_GLOBAL_COOLDOWN = 10        # minutes entre 2 msg vers le meme destinataire
DEFAULT_TYPE_COOLDOWN = 30          # minutes entre 2 msg du meme type
DEFAULT_QUIET_START = "23:00"
DEFAULT_QUIET_END = "06:00"
MIN_DELAY_SECONDS = 2
MAX_DELAY_SECONDS = 5
MAX_MESSAGE_LENGTH = 500
CIRCUIT_BREAKER_THRESHOLD = 3       # erreurs consecutives avant pause
CIRCUIT_BREAKER_PAUSE_MIN = 30      # minutes de pause apres circuit breaker
HTTP_CONNECT_TIMEOUT = 5
HTTP_READ_TIMEOUT = 10

# Signature snapshot publique (lien envoye dans les WA, pas de session Cockpit)
SNAPSHOT_TTL_DAYS_DEFAULT = 7
SNAPSHOT_PUBLIC_PATH = "/public/snapshot"


def _snapshot_secret():
    """Cle HMAC pour signer les URLs publiques de snapshot.
    Doit etre identique entre Cockpit et la VM PCA (ecoutehik2/cockpit_dispatch).
    """
    s = os.environ.get("SNAPSHOT_PUBLIC_SECRET", "").strip()
    if s:
        return s.encode("utf-8")
    # Fallback dev : derive de JWT_SECRET pour eviter un crash, mais log un avertissement.
    fallback = os.environ.get("JWT_SECRET", "dev-snapshot-fallback")
    return fallback.encode("utf-8")


def _cockpit_public_base():
    return (os.environ.get("COCKPIT_PUBLIC_URL", "https://cockpit.lemans.org")).rstrip("/")


def sign_snapshot_url(alert_id, ttl_days=None):
    """Genere une URL publique signee pour le snapshot d'une alerte.

    Format : <COCKPIT_PUBLIC_URL>/public/snapshot/<alert_id>?exp=<unix>&sig=<hmac32>

    Securite : HMAC-SHA256 tronquee a 32 hex (128 bits) sur "<alert_id>:<exp>".
    La cle vient de SNAPSHOT_PUBLIC_SECRET (env). TTL par defaut 7 jours.

    L'URL est servie par GET /public/snapshot/<alert_id> qui verifie :
      1. exp non depasse
      2. sig HMAC match (comparison constant-time)
      3. l'alerte existe (active ou archive) et le snapshot est sous HIK_IMAGES_ROOT
    """
    if not alert_id:
        return ""
    ttl_days = int(ttl_days or SNAPSHOT_TTL_DAYS_DEFAULT)
    exp = int(time.time() + ttl_days * 86400)
    payload = "%s:%d" % (str(alert_id), exp)
    sig = hmac.new(_snapshot_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return "%s%s/%s?exp=%d&sig=%s" % (
        _cockpit_public_base(), SNAPSHOT_PUBLIC_PATH, str(alert_id), exp, sig
    )


def verify_snapshot_signature(alert_id, exp, sig):
    """Verifie une URL signee. Renvoie True si valide et non expiree."""
    if not alert_id or not exp or not sig:
        return False
    try:
        exp_int = int(exp)
    except (ValueError, TypeError):
        return False
    if exp_int < int(time.time()):
        return False
    payload = "%s:%d" % (str(alert_id), exp_int)
    expected = hmac.new(_snapshot_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expected, str(sig))


# ---------------------------------------------------------------------------
# Circuit breaker PARTAGE
# ---------------------------------------------------------------------------
#
# Le breaker vivait sur l'instance (self._consecutive_errors). Or une instance
# est creee a chaque envoi Alfred et a chaque cycle alert_engine : le compteur
# repartait de zero a chaque fois et n'atteignait jamais le seuil. Le breaker
# ne s'ouvrait donc jamais, et un WAHA en panne (ou un numero en cours de
# bannissement) etait relance sans fin.
#
# Deux niveaux :
#   - memoire module (verrou) : partage par toutes les instances d'un process
#     (Cockpit sous waitress = un seul process, plusieurs threads) ;
#   - document Mongo cockpit_wa_config{_id: "wa_breaker"} : alert_engine est un
#     process court relance toutes les 30 s, sans lui il repartirait de zero a
#     chaque run. Relu au plus toutes les BREAKER_SYNC_SECONDS.
#
# Document separe de wa_config : l'enregistrement de la config admin ne doit
# pas pouvoir ecraser (ni etre ecrase par) l'etat du breaker. Mongo
# injoignable : on degrade sur la memoire seule, jamais d'exception.

BREAKER_DOC_ID = "wa_breaker"
BREAKER_SYNC_SECONDS = 15

_BREAKER_LOCK = threading.Lock()
_BREAKER = {"errors": 0, "open_until": None, "synced_at": 0.0}


def _as_utc(dt):
    """pymongo rend des datetimes naifs (UTC) : on les rend conscients."""
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt if isinstance(dt, datetime) else None


def _breaker_col(db):
    return db["cockpit_wa_config"]


def _breaker_sync(db, force=False):
    """Recharge l'etat depuis Mongo (au plus toutes les BREAKER_SYNC_SECONDS).

    Mongo est la verite partagee entre process : un breaker ouvert par
    alert_engine doit bloquer aussi les reponses Alfred de Cockpit.
    """
    with _BREAKER_LOCK:
        if not force and (time.time() - _BREAKER["synced_at"]) < BREAKER_SYNC_SECONDS:
            return
        _BREAKER["synced_at"] = time.time()
    try:
        doc = _breaker_col(db).find_one({"_id": BREAKER_DOC_ID}) or {}
    except Exception as e:
        log.warning("WhatsApp breaker: lecture Mongo impossible (%s), memoire seule", e)
        return
    with _BREAKER_LOCK:
        _BREAKER["errors"] = int(doc.get("consecutive_errors") or 0)
        _BREAKER["open_until"] = _as_utc(doc.get("open_until"))


def breaker_is_open(db):
    """True si le breaker est ouvert. Le referme (memoire + Mongo) a expiration."""
    _breaker_sync(db)
    now = datetime.now(timezone.utc)
    with _BREAKER_LOCK:
        until = _BREAKER["open_until"]
        if until is None:
            return False
        if now < until:
            return True
        _BREAKER["open_until"] = None
        _BREAKER["errors"] = 0
    log.info("Circuit breaker ferme (delai expire)")
    try:
        # Filtre sur l'echeance : si un autre process vient de le rouvrir
        # (open_until plus lointain), on ne l'efface pas.
        _breaker_col(db).update_one(
            {"_id": BREAKER_DOC_ID, "open_until": {"$lte": now}},
            {"$set": {"open_until": None, "consecutive_errors": 0, "updated_at": now}},
        )
    except Exception as e:
        log.warning("WhatsApp breaker: fermeture Mongo impossible : %s", e)
    return False


def breaker_on_error(db):
    """Compte une erreur d'envoi ; ouvre le breaker au seuil. Rend le compteur."""
    now = datetime.now(timezone.utc)
    with _BREAKER_LOCK:
        _BREAKER["errors"] += 1
        errors = _BREAKER["errors"]
    try:
        # $inc atomique : deux process qui echouent en meme temps s'additionnent
        doc = _breaker_col(db).find_one_and_update(
            {"_id": BREAKER_DOC_ID},
            {"$inc": {"consecutive_errors": 1}, "$set": {"updated_at": now}},
            upsert=True, return_document=True,  # ReturnDocument.AFTER == True
        ) or {}
        errors = max(errors, int(doc.get("consecutive_errors") or 0))
        with _BREAKER_LOCK:
            _BREAKER["errors"] = errors
    except Exception as e:
        log.warning("WhatsApp breaker: ecriture Mongo impossible : %s", e)
    if errors >= CIRCUIT_BREAKER_THRESHOLD:
        until = now + timedelta(minutes=CIRCUIT_BREAKER_PAUSE_MIN)
        with _BREAKER_LOCK:
            already = _BREAKER["open_until"] is not None and _BREAKER["open_until"] > now
            if not already:
                _BREAKER["open_until"] = until
        if not already:
            log.warning(
                "Circuit breaker OUVERT: %d erreurs consecutives, pause %d min",
                errors, CIRCUIT_BREAKER_PAUSE_MIN,
            )
            try:
                _breaker_col(db).update_one(
                    {"_id": BREAKER_DOC_ID},
                    {"$set": {"open_until": until, "opened_at": now}},
                    upsert=True,
                )
            except Exception as e:
                log.warning("WhatsApp breaker: ouverture Mongo impossible : %s", e)
    return errors


def breaker_on_success(db):
    """Remet le compteur a zero. N'ecrit en base que s'il y avait des erreurs
    (sinon chaque envoi reussi couterait une ecriture inutile)."""
    with _BREAKER_LOCK:
        had_errors = _BREAKER["errors"] > 0
        _BREAKER["errors"] = 0
    if not had_errors:
        return
    try:
        _breaker_col(db).update_one(
            {"_id": BREAKER_DOC_ID},
            {"$set": {"consecutive_errors": 0,
                      "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception as e:
        log.warning("WhatsApp breaker: reset Mongo impossible : %s", e)


def _breaker_reset_memory():
    """Remise a zero memoire (tests uniquement)."""
    with _BREAKER_LOCK:
        _BREAKER["errors"] = 0
        _BREAKER["open_until"] = None
        _BREAKER["synced_at"] = 0.0


# Priorites des envois "directs" (hors alertes) : voir send_direct().
PRIORITY_REPLY = "reply"      # reponse a quelqu'un qui vient de demander
PRIORITY_INTERIM = "interim"  # phrase d'attente, confort pur
PRIORITY_LOW = "low"          # message non sollicite (refus DM a un inconnu)

# Seuils "proche des limites" pour les envois de confort : au-dela, on garde
# le quota restant pour les vraies reponses et les alertes.
NEAR_LIMIT_HOUR_RATIO = 0.8
NEAR_LIMIT_DAY_RATIO = 0.9


class WhatsAppService:

    def __init__(self, db):
        self.db = db
        self._config_cache = None
        self._config_ts = None

    # Compat : ces deux attributs etaient portes par l'instance. Ils lisent
    # desormais l'etat partage du module (get_stats les expose a l'admin).
    @property
    def _consecutive_errors(self):
        with _BREAKER_LOCK:
            return _BREAKER["errors"]

    @property
    def _circuit_open_until(self):
        with _BREAKER_LOCK:
            return _BREAKER["open_until"]

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def get_config(self):
        """Charge la config WA depuis MongoDB (cache 60s)."""
        now = datetime.now(timezone.utc)
        if (self._config_cache
                and self._config_ts
                and (now - self._config_ts).total_seconds() < 60):
            return self._config_cache

        doc = self.db["cockpit_wa_config"].find_one({"_id": "wa_config"})
        if not doc:
            doc = {
                "_id": "wa_config",
                "enabled": False,
                "waha_url": os.getenv("WAHA_URL", DEFAULT_WAHA_URL),
                "session_name": os.getenv("WAHA_SESSION", DEFAULT_SESSION),
                "rate_limit_per_hour": DEFAULT_RATE_LIMIT_HOUR,
                "rate_limit_per_day": DEFAULT_RATE_LIMIT_DAY,
                "global_cooldown_minutes": DEFAULT_GLOBAL_COOLDOWN,
                "type_cooldown_minutes": DEFAULT_TYPE_COOLDOWN,
                "quiet_hours": {
                    "enabled": False,
                    "start": DEFAULT_QUIET_START,
                    "end": DEFAULT_QUIET_END,
                },
                "default_message_prefix": "[COCKPIT]",
                "api_key": os.getenv("WAHA_API_KEY", ""),
            }
            self.db["cockpit_wa_config"].insert_one(doc)

        self._config_cache = doc
        self._config_ts = now
        return doc

    def is_enabled(self):
        """True si WA est globalement active."""
        cfg = self.get_config()
        return bool(cfg.get("enabled"))

    def _base_url(self):
        cfg = self.get_config()
        return (cfg.get("waha_url") or DEFAULT_WAHA_URL).rstrip("/")

    def _session(self):
        cfg = self.get_config()
        return cfg.get("session_name") or DEFAULT_SESSION

    def _headers(self):
        """Headers pour les requetes WAHA (avec API key si configuree)."""
        cfg = self.get_config()
        api_key = cfg.get("api_key") or os.getenv("WAHA_API_KEY", "")
        h = {}
        if api_key:
            h["X-Api-Key"] = api_key
        return h

    # ------------------------------------------------------------------
    # Session WAHA
    # ------------------------------------------------------------------

    def check_session(self):
        """Retourne le status de la session WAHA."""
        try:
            r = requests.get(
                "%s/api/sessions/%s" % (self._base_url(), self._session()),
                headers=self._headers(),
                timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
            )
            if r.status_code == 200:
                return r.json()
            return {"status": "ERROR", "code": r.status_code}
        except Exception as e:
            return {"status": "UNREACHABLE", "error": str(e)}

    def get_qr_code(self):
        """Retourne le QR code base64 pour appairage."""
        try:
            r = requests.get(
                "%s/api/%s/auth/qr" % (self._base_url(), self._session()),
                headers=self._headers(),
                timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
            )
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None

    def get_groups(self):
        """Liste les groupes WhatsApp depuis WAHA."""
        try:
            r = requests.get(
                "%s/api/%s/chats" % (self._base_url(), self._session()),
                headers=self._headers(),
                timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
            )
            if r.status_code == 200:
                chats = r.json()
                groups = []
                for c in chats:
                    cid = c.get("id", "")
                    if isinstance(cid, dict):
                        cid = cid.get("_serialized", "")
                    if str(cid).endswith("@g.us"):
                        c["_chat_id"] = cid
                        groups.append(c)
                return groups
            return []
        except Exception as e:
            log.warning("Erreur listing groupes WAHA: %s", e)
            return []

    # ------------------------------------------------------------------
    # Envoi bas niveau
    # ------------------------------------------------------------------

    def _send_text(self, chat_id, text):
        """POST /api/sendText. Retourne le message_id ou None."""
        try:
            headers = self._headers()
            headers["Content-Type"] = "application/json"
            r = requests.post(
                "%s/api/sendText" % self._base_url(),
                headers=headers,
                json={
                    "chatId": chat_id,
                    "text": text,
                    "session": self._session(),
                },
                timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
            )
            if r.status_code in (200, 201):
                breaker_on_success(self.db)
                data = r.json()
                return data.get("id") or data.get("key", {}).get("id")
            log.warning("WAHA sendText status=%d body=%s", r.status_code, r.text[:200])
            self._on_send_error()
            return None
        except Exception as e:
            log.warning("WAHA sendText erreur: %s", e)
            self._on_send_error()
            return None

    def _send_image(self, chat_id, image_path, caption=""):
        """POST /api/sendImage avec une image en base64. Retourne le message_id.

        L'image est lue depuis le disque (HIK_IMAGES_ROOT). Limitee a
        WAHA_SENDIMAGE_MAX_BYTES (5 MB par defaut) pour respecter les limites
        WhatsApp et eviter d'exploser la RAM/payload WAHA.
        """
        if not image_path or not os.path.isfile(image_path):
            log.warning("WAHA sendImage: fichier introuvable %s", image_path)
            return None
        try:
            file_size = os.path.getsize(image_path)
            if file_size > WAHA_SENDIMAGE_MAX_BYTES:
                log.warning(
                    "WAHA sendImage: image trop volumineuse (%d > %d bytes), skip",
                    file_size, WAHA_SENDIMAGE_MAX_BYTES,
                )
                return None
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            payload = {
                "chatId": chat_id,
                "file": {
                    "mimetype": "image/jpeg",
                    "filename": os.path.basename(image_path),
                    "data": b64,
                },
                "session": self._session(),
            }
            if caption:
                payload["caption"] = caption
            headers = self._headers()
            headers["Content-Type"] = "application/json"
            r = requests.post(
                "%s/api/sendImage" % self._base_url(),
                headers=headers,
                json=payload,
                # Plus de read timeout : l'upload WAHA peut prendre 5-15s
                timeout=(HTTP_CONNECT_TIMEOUT, 30),
            )
            if r.status_code in (200, 201):
                breaker_on_success(self.db)
                data = r.json() if r.content else {}
                return data.get("id") or data.get("key", {}).get("id") or "sent"
            log.warning("WAHA sendImage status=%d body=%s", r.status_code, r.text[:200])
            self._on_send_error()
            return None
        except Exception as e:
            log.warning("WAHA sendImage erreur: %s", e)
            self._on_send_error()
            return None

    def _on_send_error(self):
        """Incremente le compteur d'erreurs (partage) et ouvre le breaker si besoin."""
        breaker_on_error(self.db)

    # ------------------------------------------------------------------
    # Anti-ban checks
    # ------------------------------------------------------------------

    def _is_circuit_open(self):
        """True si le circuit breaker (partage process + Mongo) est ouvert."""
        return breaker_is_open(self.db)

    def _is_quiet_hours(self):
        """True si on est dans les heures silencieuses (Europe/Paris)."""
        cfg = self.get_config()
        qh = cfg.get("quiet_hours") or {}
        if not qh.get("enabled"):
            return False

        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Europe/Paris"))
        h_now = now.hour * 60 + now.minute

        start_parts = str(qh.get("start", DEFAULT_QUIET_START)).split(":")
        end_parts = str(qh.get("end", DEFAULT_QUIET_END)).split(":")
        h_start = int(start_parts[0]) * 60 + int(start_parts[1])
        h_end = int(end_parts[0]) * 60 + int(end_parts[1])

        if h_start <= h_end:
            return h_start <= h_now < h_end
        # Passage de minuit (ex: 23h -> 6h)
        return h_now >= h_start or h_now < h_end

    def _check_rate_limit_hour(self):
        """True si on est sous la limite horaire."""
        cfg = self.get_config()
        limit = cfg.get("rate_limit_per_hour", DEFAULT_RATE_LIMIT_HOUR)
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        count = self.db["cockpit_wa_send_history"].count_documents(
            {"sentAt": {"$gte": since}, "status": "sent"}
        )
        return count < limit

    def _check_rate_limit_day(self):
        """True si on est sous la limite journaliere."""
        cfg = self.get_config()
        limit = cfg.get("rate_limit_per_day", DEFAULT_RATE_LIMIT_DAY)
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        count = self.db["cockpit_wa_send_history"].count_documents(
            {"sentAt": {"$gte": since}, "status": "sent"}
        )
        return count < limit

    def _check_cooldown_dedup(self, dedup_key, cooldown_minutes):
        """True si cette alerte n'a pas deja ete notifiee recemment."""
        if not dedup_key:
            return True
        since = datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)
        return self.db["cockpit_wa_send_history"].count_documents(
            {"alert_dedup_key": dedup_key, "sentAt": {"$gte": since}, "status": "sent"}
        ) == 0

    def _check_cooldown_type(self, slug):
        """True si ce type d'alerte n'a pas ete notifie recemment."""
        cfg = self.get_config()
        cooldown = cfg.get("type_cooldown_minutes", DEFAULT_TYPE_COOLDOWN)
        since = datetime.now(timezone.utc) - timedelta(minutes=cooldown)
        return self.db["cockpit_wa_send_history"].count_documents(
            {"alert_slug": slug, "sentAt": {"$gte": since}, "status": "sent"}
        ) == 0

    def _check_cooldown_recipient(self, recipient_id):
        """True si ce destinataire n'a pas recu de message recemment."""
        cfg = self.get_config()
        cooldown = cfg.get("global_cooldown_minutes", DEFAULT_GLOBAL_COOLDOWN)
        since = datetime.now(timezone.utc) - timedelta(minutes=cooldown)
        # Les envois directs (reponses Alfred) sont exclus : ils comptent dans
        # les plafonds horaire/journalier, mais une reponse Alfred dans un
        # groupe ne doit pas y bloquer les alertes pendant 10 min.
        return self.db["cockpit_wa_send_history"].count_documents(
            {"recipient_id": recipient_id, "sentAt": {"$gte": since},
             "status": "sent", "source": {"$ne": "direct"}}
        ) == 0

    def _human_delay(self):
        """Delai variable entre envois pour simuler un comportement humain."""
        delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
        time.sleep(delay)

    # ------------------------------------------------------------------
    # Formatage
    # ------------------------------------------------------------------

    def _format_single_alert(self, alert_doc, definition):
        """Formate une alerte en ligne pour l'agregation."""
        priority = definition.get("priority", 3)
        if priority <= 1:
            marker = "[!!!]"
        elif priority <= 2:
            marker = "[!!]"
        elif priority <= 3:
            marker = "[!]"
        else:
            marker = "[i]"

        name = definition.get("name", alert_doc.get("title", "Alerte"))
        msg = alert_doc.get("message", "")
        return "%s %s : %s" % (marker, name, msg)

    def format_batch_message(self, alerts_with_defs, include_snapshot_urls=True):
        """Formate un message agrege pour plusieurs alertes.

        Si include_snapshot_urls=False : pas de ligne "Photo : <url>" ajoutee
        (cas ou l'image va etre envoyee directement via sendImage).
        Sinon, URL publique signee HMAC (TTL 7j) servie par /public/snapshot/<id>.
        """
        cfg = self.get_config()
        prefix = cfg.get("default_message_prefix", "[COCKPIT]")

        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Europe/Paris"))
        time_str = now.strftime("%H:%M")

        if len(alerts_with_defs) == 1:
            alert_doc, definition = alerts_with_defs[0]
            name = definition.get("name", alert_doc.get("title", "Alerte"))
            msg = alert_doc.get("message", "")
            text = "%s %s\n%s\n\n%s" % (prefix, name, time_str, msg)
            if include_snapshot_urls:
                action_data = alert_doc.get("actionData") or {}
                if action_data.get("has_snapshot") and alert_doc.get("_id"):
                    snap_url = sign_snapshot_url(alert_doc["_id"])
                    if snap_url:
                        text += "\nPhoto : " + snap_url
        else:
            lines = []
            for alert_doc, definition in alerts_with_defs:
                line = "- %s" % self._format_single_alert(alert_doc, definition)
                if include_snapshot_urls:
                    action_data = alert_doc.get("actionData") or {}
                    if action_data.get("has_snapshot") and alert_doc.get("_id"):
                        snap_url = sign_snapshot_url(alert_doc["_id"])
                        if snap_url:
                            line += " - " + snap_url
                lines.append(line)
            text = "%s %d alertes - %s\n%s" % (
                prefix, len(alerts_with_defs), time_str, "\n".join(lines)
            )

        # Tronquer a MAX_MESSAGE_LENGTH
        if len(text) > MAX_MESSAGE_LENGTH:
            text = text[:MAX_MESSAGE_LENGTH - 3] + "..."
        return text

    def format_test_message(self):
        """Message de test."""
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Europe/Paris"))
        return "[COCKPIT] Message test\n%s\n\nCeci est un test de notification WhatsApp." % (
            now.strftime("%H:%M %d/%m/%Y")
        )

    # ------------------------------------------------------------------
    # Historique
    # ------------------------------------------------------------------

    def _record_send(self, alert_slug, alert_name, dedup_key,
                     recipient_type, recipient_id, recipient_name,
                     message_text, status, waha_msg_id=None, error=None,
                     source=None):
        """Enregistre un envoi dans cockpit_wa_send_history.

        source : None pour les alertes (historique), "direct" pour les envois
        hors alertes (reponses Alfred) via send_direct().
        """
        now = datetime.now(timezone.utc)
        doc = {
            "alert_slug": alert_slug,
            "alert_name": alert_name,
            "alert_dedup_key": dedup_key,
            "recipient_type": recipient_type,
            "recipient_id": recipient_id,
            "recipient_name": recipient_name,
            "message_text": message_text[:200],
            "status": status,
            "waha_message_id": waha_msg_id,
            "error": str(error)[:200] if error else None,
            "sentAt": now,
            "createdAt": now,
        }
        if source:
            doc["source"] = source
        self.db["cockpit_wa_send_history"].insert_one(doc)

    # ------------------------------------------------------------------
    # Envoi direct (reponses Alfred) -- soumis aux memes plafonds
    # ------------------------------------------------------------------

    def _count_sent_since(self, since):
        return self.db["cockpit_wa_send_history"].count_documents(
            {"sentAt": {"$gte": since}, "status": "sent"}
        )

    def send_direct(self, chat_id, text, priority=PRIORITY_REPLY,
                    kind="alfred", recipient_name=""):
        """Envoi texte hors alertes (Alfred). Rend le msg_id WAHA ou None.

        Alfred appelait _send_text directement : ses reponses n'entraient pas
        dans cockpit_wa_send_history, donc ni dans les plafonds anti-ban ni
        dans les stats admin. Un echange soutenu pouvait faire depasser au
        numero le volume que les plafonds sont censes proteger.

        Regles selon la priorite :
          - toutes : refus si breaker ouvert ou plafond JOURNALIER atteint
            (plafond dur, protege le numero) ;
          - reply : refus aussi au plafond horaire ; PAS de gate heures
            silencieuses (quelqu'un vient de poser la question, il attend) ;
          - interim / low : refus des qu'on approche des plafonds (80 % horaire,
            90 % journalier) : le quota restant va aux vraies reponses ;
          - low : refus en heures silencieuses (message non sollicite).
        Le flag global `enabled` (notifications d'alertes) n'est volontairement
        pas applique : il n'a jamais gate Alfred.

        Chaque envoi tente est trace (status sent/error, source "direct").
        Les refus ne sont que journalises (pas d'entree d'historique).
        """
        if not chat_id or not text:
            return None
        if self._is_circuit_open():
            log.warning("WhatsApp direct (%s/%s) refuse : circuit breaker ouvert",
                        kind, priority)
            return None
        cfg = self.get_config()
        now = datetime.now(timezone.utc)
        lim_h = int(cfg.get("rate_limit_per_hour", DEFAULT_RATE_LIMIT_HOUR) or 0)
        lim_d = int(cfg.get("rate_limit_per_day", DEFAULT_RATE_LIMIT_DAY) or 0)
        try:
            n_h = self._count_sent_since(now - timedelta(hours=1))
            n_d = self._count_sent_since(now - timedelta(hours=24))
        except Exception as e:
            # Sans comptage, on ne sait pas si on est sous le plafond : on
            # laisse passer une reponse directe, pas un message de confort.
            log.warning("WhatsApp direct : comptage impossible (%s)", e)
            if priority != PRIORITY_REPLY:
                return None
            n_h = n_d = 0
        if n_d >= lim_d:
            log.warning("WhatsApp direct (%s/%s) refuse : plafond journalier %d atteint",
                        kind, priority, lim_d)
            return None
        if priority == PRIORITY_REPLY:
            if n_h >= lim_h:
                log.warning("WhatsApp direct (%s) refuse : plafond horaire %d atteint",
                            kind, lim_h)
                return None
        else:
            if n_h >= lim_h * NEAR_LIMIT_HOUR_RATIO or n_d >= lim_d * NEAR_LIMIT_DAY_RATIO:
                log.info("WhatsApp direct (%s/%s) saute : proche des plafonds (%d/%d h, %d/%d j)",
                         kind, priority, n_h, lim_h, n_d, lim_d)
                return None
            if priority == PRIORITY_LOW and self._is_quiet_hours():
                log.info("WhatsApp direct (%s/low) saute : heures silencieuses", kind)
                return None

        msg_id = self._send_text(chat_id, text)
        try:
            self._record_send(
                alert_slug="direct:%s" % kind,
                alert_name="Alfred" if kind.startswith("alfred") else kind,
                dedup_key=None,
                recipient_type="group" if str(chat_id).endswith("@g.us") else "dm",
                recipient_id=chat_id,
                recipient_name=recipient_name or chat_id,
                message_text=text,
                status="sent" if msg_id else "error",
                waha_msg_id=msg_id,
                error=None if msg_id else "Echec envoi WAHA",
                source="direct",
            )
        except Exception as e:
            log.warning("WhatsApp direct : historique non ecrit (%s)", e)
        return msg_id or None

    # ------------------------------------------------------------------
    # Envoi haut niveau -- message test
    # ------------------------------------------------------------------

    def send_test(self, chat_id):
        """Envoie un message test. Retourne (ok, detail)."""
        if self._is_circuit_open():
            return False, "Circuit breaker actif"
        text = self.format_test_message()
        msg_id = self._send_text(chat_id, text)
        if msg_id:
            return True, "Message envoye (id=%s)" % msg_id
        return False, "Echec envoi"

    # ------------------------------------------------------------------
    # Point d'entree principal -- batch
    # ------------------------------------------------------------------

    def notify_batch(self, alerts_with_defs):
        """Point d'entree appele par alert_engine en fin de cycle.

        alerts_with_defs: liste de tuples (alert_doc, definition)

        Logique :
        1. Filtrer les alertes ayant whatsapp.enabled
        2. Verifier les gardes-fous globaux
        3. Agreger en un seul message par destinataire
        4. Envoyer avec delai humain entre chaque destinataire
        """
        if not alerts_with_defs:
            return

        if not self.is_enabled():
            return

        if self._is_circuit_open():
            log.info("WhatsApp: circuit breaker actif, envoi ignore")
            return

        if self._is_quiet_hours():
            log.info("WhatsApp: heures silencieuses, envoi ignore")
            return

        if not self._check_rate_limit_hour():
            log.warning("WhatsApp: rate limit horaire atteint")
            return

        if not self._check_rate_limit_day():
            log.warning("WhatsApp: rate limit journalier atteint")
            return

        # Filtrer : garder seulement les alertes WA-enabled + cooldown OK
        eligible = []
        for alert_doc, definition in alerts_with_defs:
            wa = definition.get("whatsapp") or {}
            if not wa.get("enabled"):
                continue
            dedup_key = alert_doc.get("dedup_key")
            cooldown = wa.get("cooldown_minutes", DEFAULT_TYPE_COOLDOWN)
            if not self._check_cooldown_dedup(dedup_key, cooldown):
                continue
            slug = definition.get("slug", "")
            if not self._check_cooldown_type(slug):
                continue
            eligible.append((alert_doc, definition))

        if not eligible:
            return

        # Construire la map destinataire -> alertes
        # Un destinataire = un group_id ou un phone@c.us
        recipient_alerts = {}  # recipient_id -> [(alert_doc, definition), ...]
        recipient_meta = {}    # recipient_id -> {"type": "group"|"dm", "name": "..."}

        for alert_doc, definition in eligible:
            wa = definition.get("whatsapp") or {}
            groups = wa.get("groups") or []
            for gid in groups:
                recipient_alerts.setdefault(gid, []).append((alert_doc, definition))
                if gid not in recipient_meta:
                    grp = self.db["cockpit_wa_groups"].find_one({"group_id": gid})
                    recipient_meta[gid] = {
                        "type": "group",
                        "name": grp.get("name", gid) if grp else gid,
                    }

            # DM aux contacts nommes (escalade individuelle).
            # Le flag s'appelle historiquement dm_on_critical mais ne dependait
            # plus du niveau de priorite : si la case est cochee dans la
            # definition, les contacts listes recoivent un DM a chaque
            # declenchement de cette alerte.
            if wa.get("dm_on_critical"):
                for phone in (wa.get("dm_recipients") or []):
                    chat_id = "%s@c.us" % phone
                    recipient_alerts.setdefault(chat_id, []).append(
                        (alert_doc, definition)
                    )
                    if chat_id not in recipient_meta:
                        ct = self.db["cockpit_wa_contacts"].find_one({"phone": phone})
                        recipient_meta[chat_id] = {
                            "type": "dm",
                            "name": ct.get("name", phone) if ct else phone,
                        }

        # Envoyer un message agrege par destinataire
        sent_count = 0
        for recipient_id, alert_list in recipient_alerts.items():
            # Cooldown par destinataire
            if not self._check_cooldown_recipient(recipient_id):
                log.info("WhatsApp: cooldown destinataire %s", recipient_id)
                continue

            # Re-verifier rate limits avant chaque envoi
            if not self._check_rate_limit_hour() or not self._check_rate_limit_day():
                log.warning("WhatsApp: rate limit atteint pendant le batch")
                break

            if self._is_circuit_open():
                log.warning("WhatsApp: circuit breaker ouvert pendant le batch")
                break

            meta = recipient_meta.get(recipient_id, {})

            # Detecter les alertes avec snapshot accessible localement (sur la
            # machine qui execute ce code -- ecoutehik2 sur la VM PCA, ou
            # cockpit en dev). Si oui : on envoie l'image en direct (sendImage),
            # sinon on envoie le texte (avec URL signee en fallback).
            snapshot_pairs = []
            for a, d in alert_list:
                ad = a.get("actionData") or {}
                sp = ad.get("snapshot_path") or ""
                if ad.get("has_snapshot") and sp and os.path.isfile(sp):
                    snapshot_pairs.append((a, d, sp))

            # Texte sans URL si on va envoyer l'image (evite le doublon).
            text = self.format_batch_message(
                alert_list,
                include_snapshot_urls=(len(snapshot_pairs) == 0),
            )

            # Delai humain entre envois (sauf le premier)
            if sent_count > 0:
                self._human_delay()

            msg_id = None
            if len(alert_list) == 1 and snapshot_pairs:
                # Cas optimal : 1 alerte avec snapshot -> 1 sendImage avec caption
                _, _, snap_path = snapshot_pairs[0]
                msg_id = self._send_image(recipient_id, snap_path, caption=text)
                if msg_id is None:
                    # Fallback : envoyer le texte avec URL signee si l'image echoue
                    fallback_text = self.format_batch_message(alert_list, include_snapshot_urls=True)
                    msg_id = self._send_text(recipient_id, fallback_text)
            else:
                # Cas standard : envoyer le texte, puis 1 image par alerte avec snapshot
                msg_id = self._send_text(recipient_id, text)
                if msg_id and snapshot_pairs:
                    for (a, _, snap_path) in snapshot_pairs:
                        self._human_delay()
                        # Caption courte par image pour ne pas dupliquer le texte global
                        cam = (a.get("actionData") or {}).get("camera_label", "")
                        cap = "%s - %s" % (a.get("title", ""), cam) if cam else a.get("title", "")
                        self._send_image(recipient_id, snap_path, caption=cap)

            # Slug agrege pour l'historique
            slugs = list(set(d.get("slug", "") for _, d in alert_list))
            slug_str = ",".join(slugs[:3])
            name_str = ", ".join(
                d.get("name", "") for _, d in alert_list[:3]
            )
            dedup_str = "|".join(
                a.get("dedup_key", "") for a, _ in alert_list if a.get("dedup_key")
            )

            self._record_send(
                alert_slug=slug_str,
                alert_name=name_str,
                dedup_key=dedup_str,
                recipient_type=meta.get("type", "group"),
                recipient_id=recipient_id,
                recipient_name=meta.get("name", recipient_id),
                message_text=text,
                status="sent" if msg_id else "error",
                waha_msg_id=msg_id,
                error=None if msg_id else "Echec envoi WAHA",
            )

            if msg_id:
                sent_count += 1
                log.info("WhatsApp: envoye a %s (%d alertes)",
                         meta.get("name", recipient_id), len(alert_list))

        if sent_count:
            log.info("WhatsApp: %d message(s) envoye(s) ce cycle", sent_count)

    # ------------------------------------------------------------------
    # Stats pour le dashboard admin
    # ------------------------------------------------------------------

    def get_stats(self):
        """Retourne les stats pour le dashboard admin."""
        now = datetime.now(timezone.utc)
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(hours=24)
        cfg = self.get_config()

        sent_hour = self.db["cockpit_wa_send_history"].count_documents(
            {"sentAt": {"$gte": hour_ago}, "status": "sent"}
        )
        sent_day = self.db["cockpit_wa_send_history"].count_documents(
            {"sentAt": {"$gte": day_ago}, "status": "sent"}
        )
        errors_day = self.db["cockpit_wa_send_history"].count_documents(
            {"sentAt": {"$gte": day_ago}, "status": "error"}
        )
        last_error = self.db["cockpit_wa_send_history"].find_one(
            {"status": "error"},
            sort=[("sentAt", -1)],
        )

        return {
            "sent_this_hour": sent_hour,
            "limit_hour": cfg.get("rate_limit_per_hour", DEFAULT_RATE_LIMIT_HOUR),
            "sent_today": sent_day,
            "limit_day": cfg.get("rate_limit_per_day", DEFAULT_RATE_LIMIT_DAY),
            "errors_today": errors_day,
            "circuit_breaker": "open" if self._is_circuit_open() else "closed",
            "circuit_breaker_until": (
                self._circuit_open_until.isoformat()
                if self._circuit_open_until else None
            ),
            "last_error": {
                "message": last_error.get("error"),
                "at": last_error.get("sentAt").isoformat() if last_error and last_error.get("sentAt") else None,
            } if last_error else None,
        }
