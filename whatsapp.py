"""
whatsapp.py - Service de notification WhatsApp via WAHA pour COCKPIT.

Fournit l'integration avec l'API REST WAHA pour envoyer des alertes
sur des groupes WhatsApp et en messages directs.

Anti-ban : 10 couches de protection (rate limit, cooldown, agregation,
circuit breaker, delai variable, etc.)
"""

import os
import time
import random
import logging
from datetime import datetime, timezone, timedelta

import requests

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


class WhatsAppService:

    def __init__(self, db):
        self.db = db
        self._config_cache = None
        self._config_ts = None
        self._consecutive_errors = 0
        self._circuit_open_until = None

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
                self._consecutive_errors = 0
                data = r.json()
                return data.get("id") or data.get("key", {}).get("id")
            log.warning("WAHA sendText status=%d body=%s", r.status_code, r.text[:200])
            self._on_send_error()
            return None
        except Exception as e:
            log.warning("WAHA sendText erreur: %s", e)
            self._on_send_error()
            return None

    def _on_send_error(self):
        """Incremente le compteur d'erreurs et ouvre le circuit breaker si besoin."""
        self._consecutive_errors += 1
        if self._consecutive_errors >= CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open_until = (
                datetime.now(timezone.utc)
                + timedelta(minutes=CIRCUIT_BREAKER_PAUSE_MIN)
            )
            log.warning(
                "Circuit breaker OUVERT: %d erreurs consecutives, pause %d min",
                self._consecutive_errors, CIRCUIT_BREAKER_PAUSE_MIN,
            )

    # ------------------------------------------------------------------
    # Anti-ban checks
    # ------------------------------------------------------------------

    def _is_circuit_open(self):
        """True si le circuit breaker est ouvert."""
        if self._circuit_open_until is None:
            return False
        if datetime.now(timezone.utc) >= self._circuit_open_until:
            log.info("Circuit breaker ferme (delai expire)")
            self._circuit_open_until = None
            self._consecutive_errors = 0
            return False
        return True

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
        return self.db["cockpit_wa_send_history"].count_documents(
            {"recipient_id": recipient_id, "sentAt": {"$gte": since}, "status": "sent"}
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

    def format_batch_message(self, alerts_with_defs):
        """Formate un message agrege pour plusieurs alertes."""
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
        else:
            lines = []
            for alert_doc, definition in alerts_with_defs:
                lines.append("- %s" % self._format_single_alert(alert_doc, definition))
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
                     message_text, status, waha_msg_id=None, error=None):
        """Enregistre un envoi dans cockpit_wa_send_history."""
        now = datetime.now(timezone.utc)
        self.db["cockpit_wa_send_history"].insert_one({
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
        })

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

            # DM pour alertes critiques
            if wa.get("dm_on_critical"):
                priority = definition.get("priority", 5)
                if priority <= 2:
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

            text = self.format_batch_message(alert_list)
            meta = recipient_meta.get(recipient_id, {})

            # Delai humain entre envois (sauf le premier)
            if sent_count > 0:
                self._human_delay()

            msg_id = self._send_text(recipient_id, text)

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
