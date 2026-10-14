"""recepteur.py - Recepteur TCP des balises Teltonika (Codec 8 / 8E).

BIBLIOTHEQUE STANDARD UNIQUEMENT. Tourne sous le compte local dedie
svc-balises (service NSSM "BalisesRecepteur"), avec C:\\Python313, SANS acces
au dossier de Cockpit ni a son venv : il ne parle a Cockpit que par HTTP
signe sur la boucle locale. Deploiement : recepteur_balises/README.md.
Regles : docs/claude/balises.md.

Chaine :
  balise --TCP--> parse + CRC --> spool disque (fichier atomique + fsync)
          --> ACK a la balise --> thread d'envoi --> POST /api/balises/ingest
Le spool garde les messages tant que Cockpit ne les a pas acceptes : Cockpit
arrete ou redemarre = rien de perdu, et la balise est acquittee quand meme
(sinon elle renverrait sans fin). IMEI hors liste blanche : refuse (00) et
signale a Cockpit (ecran Configuration > Balises GPS, ajout en un clic).

Variables d'environnement (compte svc-balises) :
  BALISES_INGEST_SECRET   secret HMAC partage avec Cockpit (32 car. min)
  BALISES_COCKPIT_URL     defaut http://127.0.0.1:4008
  BALISES_PORT            defaut 5027
  BALISES_BIND            defaut 0.0.0.0
  BALISES_DATA_DIR        defaut <dossier du script>\\data (spool, logs, cache)
  BALISES_MAX_CONN        defaut 200 connexions simultanees
"""

import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import socket
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codec8 as C  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("BALISES_DATA_DIR") or os.path.join(BASE_DIR, "data")
SPOOL_DIR = os.path.join(DATA_DIR, "spool")
LOG_DIR = os.path.join(DATA_DIR, "logs")
WHITELIST_CACHE = os.path.join(DATA_DIR, "whitelist.json")

COCKPIT_URL = (os.getenv("BALISES_COCKPIT_URL") or "http://127.0.0.1:4008").rstrip("/")
PORT = int(os.getenv("BALISES_PORT") or 5027)
BIND = os.getenv("BALISES_BIND") or "0.0.0.0"
MAX_CONN = int(os.getenv("BALISES_MAX_CONN") or 200)
SOURCE = "teltonika-tcp"

IDLE_TIMEOUT_S = 120        # connexion muette : fermee
WHITELIST_REFRESH_S = 60
SEND_BATCH = 200            # messages par POST
SEND_PAUSE_S = 2
HTTP_TIMEOUT_S = 15

log = logging.getLogger("recepteur")
_conn_sem = threading.BoundedSemaphore(MAX_CONN)
_unknown_lock = threading.Lock()
_unknown = []               # IMEI refuses, remontes a Cockpit au prochain envoi
_wake = threading.Event()


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.handlers.TimedRotatingFileHandler(
        os.path.join(LOG_DIR, "recepteur.log"), when="midnight", backupCount=30, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# HTTP signe vers Cockpit (meme format que integrations.py / balises_api.py)
# ---------------------------------------------------------------------------

def _secret():
    s = os.getenv("BALISES_INGEST_SECRET", "") or ""
    if len(s) < 32:
        raise RuntimeError("BALISES_INGEST_SECRET absent ou trop court")
    return s


def _signed_request(method, path, body=b""):
    ts = int(time.time())
    sig = "sha256=" + hmac.new(_secret().encode("utf-8"), str(ts).encode("ascii") + b"." + body,
                               hashlib.sha256).hexdigest()
    req = urllib.request.Request(COCKPIT_URL + path, data=body if method == "POST" else None,
                                 method=method)
    req.add_header("X-Signature-Timestamp", str(ts))
    req.add_header("X-Signature", sig)
    if method == "POST":
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Liste blanche (servie par Cockpit, cache disque si Cockpit est arrete)
# ---------------------------------------------------------------------------

class Whitelist:
    def __init__(self):
        self.imeis = set()
        self.loaded_at = 0
        self.lock = threading.Lock()
        try:
            with open(WHITELIST_CACHE, "r", encoding="utf-8") as f:
                self.imeis = set(json.load(f).get("imeis") or [])
            log.info("liste blanche : %d IMEI (cache disque)", len(self.imeis))
        except (OSError, ValueError):
            pass

    def refresh(self):
        try:
            data = _signed_request("GET", "/api/balises/whitelist")
        except Exception as e:  # Cockpit arrete : on garde la derniere liste
            log.warning("liste blanche non rafraichie : %s", e)
            return
        imeis = set(str(i) for i in data.get("imeis") or [])
        with self.lock:
            changed = imeis != self.imeis
            self.imeis = imeis
            self.loaded_at = time.time()
        if changed:
            log.info("liste blanche : %d IMEI", len(imeis))
            tmp = WHITELIST_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"imeis": sorted(imeis)}, f)
            os.replace(tmp, WHITELIST_CACHE)

    def allows(self, imei):
        with self.lock:
            return imei in self.imeis


WHITELIST = None


# ---------------------------------------------------------------------------
# Spool : un fichier JSON par paquet, ecrit atomiquement avant l'ACK
# ---------------------------------------------------------------------------

def spool_write(messages):
    name = "%d_%s.json" % (time.time_ns(), uuid.uuid4().hex[:8])
    tmp = os.path.join(SPOOL_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(messages, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(SPOOL_DIR, name))
    _wake.set()


def _spool_files():
    try:
        return sorted(n for n in os.listdir(SPOOL_DIR) if n.endswith(".json"))
    except OSError:
        return []


def sender_loop():
    """Vide le spool vers Cockpit, dans l'ordre d'arrivee. En cas d'echec,
    reessaie avec un delai croissant (max 60 s) : rien n'est supprime avant
    un 200."""
    delay = SEND_PAUSE_S
    while True:
        _wake.wait(timeout=delay)
        _wake.clear()
        files = _spool_files()
        with _unknown_lock:
            unknown = list(_unknown)
        if not files and not unknown:
            delay = SEND_PAUSE_S
            continue
        batch, used = [], []
        for name in files:
            path = os.path.join(SPOOL_DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    msgs = json.load(f)
            except (OSError, ValueError) as e:
                log.error("spool illisible %s (%s) : mis de cote", name, e)
                os.replace(path, path + ".bad")
                continue
            if batch and len(batch) + len(msgs) > SEND_BATCH:
                break
            batch.extend(msgs)
            used.append(path)
        body = json.dumps({"source": SOURCE, "messages": batch, "unknown": unknown},
                          separators=(",", ":")).encode("utf-8")
        try:
            res = _signed_request("POST", "/api/balises/ingest", body)
        except urllib.error.HTTPError as e:
            log.warning("envoi refuse par Cockpit : HTTP %s", e.code)
            delay = min(delay * 2, 60)
            continue
        except Exception as e:
            log.warning("Cockpit injoignable : %s (%d fichier(s) en attente)", e, len(files))
            delay = min(delay * 2, 60)
            continue
        for path in used:
            try:
                os.remove(path)
            except OSError:
                pass
        with _unknown_lock:
            del _unknown[:len(unknown)]
        if batch:
            log.info("envoye : %d message(s) -> %s", len(batch),
                     {k: res.get(k) for k in ("accepted", "duplicates", "unknown", "bad_ts")})
        delay = SEND_PAUSE_S
        if len(used) < len(files):
            _wake.set()


def whitelist_loop():
    while True:
        WHITELIST.refresh()
        time.sleep(WHITELIST_REFRESH_S)


# ---------------------------------------------------------------------------
# Serveur TCP
# ---------------------------------------------------------------------------

class Handler(socketserver.BaseRequestHandler):
    def _recv_until(self, buf, parse):
        """Lit jusqu'a ce que parse(buf) rende une trame complete."""
        while True:
            res = parse(buf)
            if res is not None:
                return res
            chunk = self.request.recv(4096)
            if not chunk:
                return None
            buf.extend(chunk)

    def handle(self):
        if not _conn_sem.acquire(blocking=False):
            log.warning("trop de connexions, refus de %s", self.client_address[0])
            return
        peer = "%s:%s" % self.client_address[:2]
        imei = None
        try:
            self.request.settimeout(IDLE_TIMEOUT_S)
            buf = bytearray()
            res = self._recv_until(buf, C.parse_imei)
            if res is None:
                return
            imei, used = res
            del buf[:used]
            if not WHITELIST.allows(imei):
                self.request.sendall(b"\x00")
                log.info("IMEI inconnu refuse : %s (%s)", imei, peer)
                with _unknown_lock:
                    if len(_unknown) < 500:
                        _unknown.append({"imei": imei, "peer": peer})
                _wake.set()
                return
            self.request.sendall(b"\x01")
            while True:
                res = self._recv_until(buf, C.parse_avl_packet)
                if res is None:
                    return
                pkt, used = res
                del buf[:used]
                now = time.time()
                msgs = [C.normalize(r, imei, pkt["codec"], peer, now) for r in pkt["records"]]
                spool_write(msgs)
                self.request.sendall(C.ack_records(len(msgs)))
                log.info("%s : %d record(s) codec 0x%02X (%s)", imei, len(msgs), pkt["codec"], peer)
        except C.ProtocolError as e:
            log.warning("trame invalide de %s (imei %s) : %s", peer, imei, e)
        except (socket.timeout, ConnectionError, OSError) as e:
            log.info("connexion %s (imei %s) fermee : %s", peer, imei, e)
        finally:
            _conn_sem.release()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 64


def main():
    global WHITELIST
    os.makedirs(SPOOL_DIR, exist_ok=True)
    _setup_logging()
    _secret()  # refus de demarrer sans secret
    WHITELIST = Whitelist()
    WHITELIST.refresh()
    threading.Thread(target=whitelist_loop, name="whitelist", daemon=True).start()
    threading.Thread(target=sender_loop, name="sender", daemon=True).start()
    with Server((BIND, PORT), Handler) as srv:
        log.info("recepteur Teltonika en ecoute sur %s:%d (Cockpit %s, spool %s)",
                 BIND, PORT, COCKPIT_URL, SPOOL_DIR)
        srv.serve_forever()


if __name__ == "__main__":
    main()
