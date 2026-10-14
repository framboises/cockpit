"""Recepteur TCP (recepteur_balises/recepteur.py) sur une vraie socket :
poignee de main IMEI, ACK des records, spool avant ACK, refus d'un IMEI
inconnu, trame corrompue sans ACK ; envoi signe vers un faux Cockpit.
Point d'entree Cockpit (balises_api.py) : signature, IP source."""
import hashlib
import hmac
import http.server
import json
import os
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "recepteur_balises"))
import recepteur as R  # noqa: E402

SECRET = "x" * 40
IMEI = "866107086018948"
IMEI_FRAME = bytes.fromhex("000F383636313037303836303138393438")
ATC700 = bytes.fromhex(
    "00000000000000498E01000001A130BCC11801002063A41C941210003700AE1C000000000009"
    "000400F00000150400450100715F00050042000000430FAB0044000000B5000A00B600060000"
    "000000000100007C92")


class _Cockpit(http.server.BaseHTTPRequestHandler):
    received = []

    def log_message(self, *a):
        pass

    def _ok(self, body):
        ts = self.headers.get("X-Signature-Timestamp")
        exp = "sha256=" + hmac.new(SECRET.encode(), ts.encode() + b"." + body,
                                   hashlib.sha256).hexdigest()
        return hmac.compare_digest(exp, self.headers.get("X-Signature") or "")

    def _reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._reply(200 if self._ok(b"") else 401, {"imeis": [IMEI]})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not self._ok(body):
            return self._reply(401, {"error": "signature"})
        _Cockpit.received.append(json.loads(body))
        self._reply(200, {"ok": True, "accepted": 1})


@pytest.fixture
def recepteur(tmp_path, monkeypatch):
    cockpit = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Cockpit)
    threading.Thread(target=cockpit.serve_forever, daemon=True).start()
    _Cockpit.received = []
    monkeypatch.setenv("BALISES_INGEST_SECRET", SECRET)
    monkeypatch.setattr(R, "COCKPIT_URL", "http://127.0.0.1:%d" % cockpit.server_port)
    monkeypatch.setattr(R, "SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setattr(R, "WHITELIST_CACHE", str(tmp_path / "whitelist.json"))
    monkeypatch.setattr(R, "SEND_PAUSE_S", 0.2)
    os.makedirs(R.SPOOL_DIR)
    R.WHITELIST = R.Whitelist()
    R.WHITELIST.refresh()
    srv = R.Server(("127.0.0.1", 0), R.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], cockpit
    srv.shutdown()
    srv.server_close()
    cockpit.shutdown()


def _connect(port):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    return s


def test_liste_blanche_et_cache(recepteur):
    assert R.WHITELIST.allows(IMEI)
    assert json.load(open(R.WHITELIST_CACHE))["imeis"] == [IMEI]


def test_paquet_acquitte_apres_spool(recepteur):
    port, _ = recepteur
    s = _connect(port)
    s.sendall(IMEI_FRAME)
    assert s.recv(1) == b"\x01"
    # paquet envoye en deux morceaux (lecture incrementale)
    s.sendall(ATC700[:30])
    time.sleep(0.1)
    s.sendall(ATC700[30:])
    assert s.recv(4) == b"\x00\x00\x00\x01"
    s.close()
    files = [f for f in os.listdir(R.SPOOL_DIR) if f.endswith(".json")]
    assert len(files) == 1
    msgs = json.load(open(os.path.join(R.SPOOL_DIR, files[0])))
    assert msgs[0]["ident"] == IMEI and msgs[0]["battery.level"] == 95
    assert msgs[0]["peer"].startswith("127.0.0.1:")


def test_imei_inconnu_refuse(recepteur):
    port, _ = recepteur
    s = _connect(port)
    s.sendall(bytes.fromhex("000F") + b"123456789012345")
    assert s.recv(1) == b"\x00"
    s.close()
    time.sleep(0.1)
    assert R._unknown and R._unknown[-1]["imei"] == "123456789012345"
    del R._unknown[:]


def test_crc_faux_sans_ack(recepteur):
    port, _ = recepteur
    s = _connect(port)
    s.sendall(IMEI_FRAME)
    assert s.recv(1) == b"\x01"
    bad = bytearray(ATC700)
    bad[-1] ^= 0xFF
    s.sendall(bytes(bad))
    assert s.recv(4) == b""  # connexion fermee sans ACK
    s.close()
    assert not [f for f in os.listdir(R.SPOOL_DIR) if f.endswith(".json")]


def test_envoi_signe_vide_le_spool(recepteur):
    port, _ = recepteur
    s = _connect(port)
    s.sendall(IMEI_FRAME)
    s.recv(1)
    s.sendall(ATC700)
    s.recv(4)
    s.close()
    threading.Thread(target=R.sender_loop, daemon=True).start()
    deadline = time.time() + 5
    while time.time() < deadline and not _Cockpit.received:
        time.sleep(0.05)
    assert _Cockpit.received and _Cockpit.received[0]["messages"][0]["ident"] == IMEI
    deadline = time.time() + 2
    while time.time() < deadline and os.listdir(R.SPOOL_DIR):
        time.sleep(0.05)
    assert not [f for f in os.listdir(R.SPOOL_DIR) if f.endswith(".json")]


# ---------------------------------------------------------------------------
# Point d'entree Cockpit
# ---------------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    from flask import Flask
    import balises_api as A
    calls = []
    monkeypatch.setattr(A, "_db", lambda: _NoDb())
    monkeypatch.setattr(A.B, "ingest", lambda db, msgs, src: calls.append((msgs, src)) or
                        {"accepted": len(msgs), "duplicates": 0, "unknown": 0, "invalid": 0, "bad_ts": 0})
    monkeypatch.setattr(A.B, "whitelist", lambda db: [IMEI])
    monkeypatch.setenv("BALISES_INGEST_SECRET", SECRET)
    monkeypatch.delenv("BALISES_INGEST_ALLOWED_IPS", raising=False)
    app = Flask(__name__)
    app.register_blueprint(A.balises_bp)
    tc = app.test_client()
    tc.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    return A, tc, calls


class _NoDb:
    def __getitem__(self, name):
        return _NoCol()


class _NoCol:
    def update_one(self, *a, **k):
        pass


def _signed(A, body, secret=SECRET, ts=None):
    ts = ts or int(time.time())
    return {"X-Signature-Timestamp": str(ts), "X-Signature": A.sign(secret, ts, body)}


def test_api_ingest_signe(api):
    A, tc, calls = api
    body = json.dumps({"source": "teltonika-tcp", "messages": [{"ident": IMEI}]}).encode()
    r = tc.post("/api/balises/ingest", data=body, headers=_signed(A, body))
    assert r.status_code == 200 and r.get_json()["accepted"] == 1
    assert calls[0][1] == "teltonika-tcp"


def test_api_refus(api):
    A, tc, calls = api
    body = b'{"messages": []}'
    assert tc.post("/api/balises/ingest", data=body,
                   headers=_signed(A, body, secret="y" * 40)).status_code == 401
    assert tc.post("/api/balises/ingest", data=body,
                   headers=_signed(A, body, ts=int(time.time()) - 3600)).status_code == 401
    r = tc.post("/api/balises/ingest", data=body, headers=_signed(A, body),
                environ_overrides={"REMOTE_ADDR": "92.184.117.43"})
    assert r.status_code == 403
    assert not calls


def test_api_sans_secret_ferme(api, monkeypatch):
    A, tc, _ = api
    monkeypatch.setenv("BALISES_INGEST_SECRET", "court")
    assert tc.get("/api/balises/whitelist", headers=_signed(A, b"")).status_code == 503


def test_api_whitelist(api):
    A, tc, _ = api
    r = tc.get("/api/balises/whitelist", headers=_signed(A, b""))
    assert r.get_json() == {"imeis": [IMEI]}
