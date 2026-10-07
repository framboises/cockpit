"""Chat Alfred (alfred_chat.py) et outils exposes a la VM (alfred_tools.py).

Aucun appel reseau : requests.post est remplace, la base est un double.
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

import alfred
import alfred_chat
import alfred_tools
from conftest import FakeCollection, FakeCursor, FakeDb  # noqa: E402


SECRET = "outils-s3cret"


class _Cursor(FakeCursor):
    def limit(self, n):
        self.docs = self.docs[:n]
        return self


class _Col(FakeCollection):
    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def find(self, query=None, projection=None):
        self.last_query = query
        return _Cursor(self._matching(query))


class _Db(FakeDb):
    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _Col([])
        return self._cols[name]


def _sign(body, ts=None, secret=SECRET):
    ts = str(int(ts if ts is not None else time.time()))
    sig = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body,
                               hashlib.sha256).hexdigest()
    return {"X-Alfred-Timestamp": ts, "X-Alfred-Signature": sig,
            "Content-Type": "application/json"}


UNSCOPED = "vue-complete-s3cret"


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(alfred_chat, "TOOLS_SECRET", SECRET)
    monkeypatch.setattr(alfred_chat, "UNSCOPED_SECRET", "")


@pytest.fixture
def unscoped(monkeypatch, secret):
    monkeypatch.setattr(alfred_chat, "UNSCOPED_SECRET", UNSCOPED)


@pytest.fixture
def db(monkeypatch):
    d = _Db()
    monkeypatch.setattr(alfred_chat, "_db", lambda: d)
    return d


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(alfred_chat.alfred_chat_bp)
    return app.test_client()


# ---------------------------------------------------------------------------
# HMAC entrant (VM -> Cockpit)
# ---------------------------------------------------------------------------

def test_hmac_valide(secret):
    body = b'{"tool":"x"}'
    h = _sign(body)
    assert alfred_chat.verify_hmac(body, h["X-Alfred-Timestamp"],
                                   h["X-Alfred-Signature"]) == (True, None)


def test_hmac_corps_modifie_refuse(secret):
    h = _sign(b'{"tool":"x"}')
    ok, motif = alfred_chat.verify_hmac(b'{"tool":"y"}', h["X-Alfred-Timestamp"],
                                        h["X-Alfred-Signature"])
    assert not ok and motif == "signature_invalide"


def test_hmac_hors_fenetre_refuse(secret):
    vieux = time.time() - 301
    h = _sign(b"", ts=vieux)
    ok, motif = alfred_chat.verify_hmac(b"", h["X-Alfred-Timestamp"], h["X-Alfred-Signature"])
    assert not ok and motif == "timestamp_hors_fenetre"


def test_hmac_sans_secret_refuse_tout(monkeypatch):
    monkeypatch.setattr(alfred_chat, "TOOLS_SECRET", "")
    ok, motif = alfred_chat.verify_hmac(b"", str(int(time.time())), "sha256=00")
    assert not ok and motif == "secret_not_configured"


# ---------------------------------------------------------------------------
# Jeton de portee
# ---------------------------------------------------------------------------

def test_scope_aller_retour(secret):
    tok = alfred_chat.make_scope("a@b.fr", "24H MOTOS", "2026",
                                 {"$in": ["PCO.Technique"]}, ["meteo"])
    ctx = alfred_chat.read_scope(tok)
    assert ctx["email"] == "a@b.fr"
    assert ctx["cat_query"] == {"$in": ["PCO.Technique"]}
    assert ctx["alert_slugs"] == ["meteo"]


def test_scope_falsifie_refuse(secret):
    tok = alfred_chat.make_scope("a@b.fr", "E", "2026", {"$in": ["PCO.Technique"]}, [])
    b, sig = tok.rsplit(".", 1)
    elargi = alfred_chat._b64(json.dumps({"e": "a@b.fr", "c": None, "x": 9999999999}).encode())
    assert alfred_chat.read_scope(elargi + "." + sig) is None


def test_scope_expire_refuse(secret):
    tok = alfred_chat.make_scope("a@b.fr", "E", "2026", None, None,
                                 now=time.time() - alfred_chat.SCOPE_TTL_S - 5)
    assert alfred_chat.read_scope(tok) is None


# ---------------------------------------------------------------------------
# Routes outils
# ---------------------------------------------------------------------------

def test_manifest_exige_la_signature(client, secret, db):
    assert client.get("/api/alfred-tools/manifest").status_code == 401
    r = client.get("/api/alfred-tools/manifest", headers=_sign(b""))
    assert r.status_code == 200
    noms = {t["function"]["name"] for t in r.get_json()["tools"]}
    assert "cockpit_situation" in noms and "cockpit_trafic" in noms


def test_call_scope_invalide_403_jamais_elargi(client, secret, db):
    body = json.dumps({"tool": "cockpit_alertes", "args": {}, "scope": "abc.def"}).encode()
    r = client.post("/api/alfred-tools/call", data=body, headers=_sign(body))
    assert r.status_code == 403
    assert r.get_json()["error"] == "scope_invalide"


def test_call_transmet_la_portee_a_l_outil(client, secret, db, monkeypatch):
    vu = {}

    def stub(_db, args, ctx):
        vu["args"], vu["ctx"] = args, ctx
        return {"disponible": True}
    monkeypatch.setitem(alfred_tools.TOOLS, "cockpit_stub",
                        {"fn": stub, "description": "", "parameters": {}})
    tok = alfred_chat.make_scope("op@aco.fr", "E", "2026", {"$in": ["PCO.Flux"]}, ["x"])
    body = json.dumps({"tool": "cockpit_stub", "args": '{"limite": 3}',
                       "scope": tok, "request_id": "chat-1"}).encode()
    r = client.post("/api/alfred-tools/call", data=body, headers=_sign(body))
    assert r.status_code == 200, r.get_json()
    assert vu["args"] == {"limite": 3}
    assert vu["ctx"]["cat_query"] == {"$in": ["PCO.Flux"]}
    trace = db[alfred_chat.COL_TOOL_CALLS].docs[-1]
    assert trace["scoped"] is True and trace["email"] == "op@aco.fr"


@pytest.mark.parametrize("request_id", ["chat-abc", "wa-123", "", None])
def test_sans_scope_refuse_quel_que_soit_le_request_id(client, secret, db, request_id):
    # Le request_id n'est pas signe : il ne doit jamais ouvrir la vue complete.
    body = json.dumps({"tool": "cockpit_alertes", "request_id": request_id}).encode()
    r = client.post("/api/alfred-tools/call", data=body, headers=_sign(body))
    assert r.status_code == 403
    assert r.get_json()["error"] == "scope_requis"


def test_vue_complete_exige_le_secret_distinct(client, unscoped, db, monkeypatch):
    vu = {}
    monkeypatch.setitem(alfred_tools.TOOLS, "cockpit_stub", {
        "fn": lambda _db, a, ctx: vu.setdefault("ctx", ctx) or {"disponible": True},
        "description": "", "parameters": {}})
    body = json.dumps({"tool": "cockpit_stub"}).encode()
    r = client.post("/api/alfred-tools/call", data=body, headers=_sign(body, secret=UNSCOPED))
    assert r.status_code == 200
    assert vu["ctx"] == {}
    assert db[alfred_chat.COL_TOOL_CALLS].docs[-1]["auth"] == "unscoped"
    # Le meme corps signe avec le secret normal reste refuse
    r = client.post("/api/alfred-tools/call", data=body, headers=_sign(body))
    assert r.status_code == 403


def test_vue_complete_applique_quand_meme_un_scope_fourni(client, unscoped, db, monkeypatch):
    vu = {}
    monkeypatch.setitem(alfred_tools.TOOLS, "cockpit_stub", {
        "fn": lambda _db, a, ctx: vu.setdefault("ctx", ctx) or {"disponible": True},
        "description": "", "parameters": {}})
    tok = alfred_chat.make_scope("op@aco.fr", "E", "2026", {"$in": ["PCO.Flux"]}, [])
    body = json.dumps({"tool": "cockpit_stub", "scope": tok}).encode()
    r = client.post("/api/alfred-tools/call", data=body, headers=_sign(body, secret=UNSCOPED))
    assert r.status_code == 200
    assert vu["ctx"]["cat_query"] == {"$in": ["PCO.Flux"]}


def test_secrets_identiques_desactivent_la_vue_complete(client, secret, db, monkeypatch):
    monkeypatch.setattr(alfred_chat, "UNSCOPED_SECRET", SECRET)
    body = json.dumps({"tool": "cockpit_alertes"}).encode()
    r = client.post("/api/alfred-tools/call", data=body, headers=_sign(body))
    assert r.status_code == 403


def test_aucun_secret_503(client, db, monkeypatch):
    monkeypatch.setattr(alfred_chat, "TOOLS_SECRET", "")
    monkeypatch.setattr(alfred_chat, "UNSCOPED_SECRET", "")
    r = client.get("/api/alfred-tools/manifest", headers=_sign(b""))
    assert r.status_code == 503


def test_call_outil_inconnu_404(client, unscoped, db):
    body = json.dumps({"tool": "rm_rf"}).encode()
    r = client.post("/api/alfred-tools/call", data=body, headers=_sign(body, secret=UNSCOPED))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Historique envoye au wrapper
# ---------------------------------------------------------------------------

def _msg(role, content, status="done", minutes=0):
    return {"_id": "%s-%s" % (role, minutes), "session_id": "s", "role": role,
            "content": content, "status": status,
            "created_at": datetime(2026, 10, 7, 12, 0) + timedelta(minutes=minutes)}


def test_historique_omet_les_erreurs_et_fusionne(db):
    db[alfred_chat.COL_MESSAGES].docs = [
        _msg("user", "Q1", minutes=0),
        _msg("assistant", "", status="error", minutes=1),
        _msg("user", "Q2", minutes=2),
        _msg("assistant", "", status="running", minutes=3),
    ]
    out = alfred_chat._wrapper_messages(db, "s")
    assert out == [{"role": "user", "content": "Q1\nQ2"}]


def test_historique_alterne_et_finit_sur_user(db):
    db[alfred_chat.COL_MESSAGES].docs = [
        _msg("user", "Bonjour", minutes=0),
        _msg("assistant", "Bonjour Monsieur.", minutes=1),
        _msg("user", "Depart ?", minutes=2),
    ]
    out = alfred_chat._wrapper_messages(db, "s")
    assert [m["role"] for m in out] == ["user", "assistant", "user"]


def test_message_perdu_expire(db):
    vieux = {"_id": "m", "status": "running", "created_at": datetime.now(timezone.utc)
             .replace(tzinfo=None) - timedelta(hours=1)}
    db[alfred_chat.COL_MESSAGES].docs = [vieux]
    m = alfred_chat._expire_if_stale(db, dict(vieux))
    assert m["status"] == "error"
    assert db[alfred_chat.COL_MESSAGES].docs[0]["status"] == "error"


def test_id_de_conversation_envoye_au_wrapper(db, monkeypatch, secret):
    db[alfred_chat.COL_MESSAGES].docs = [_msg("user", "Porte nord ?", minutes=0),
                                         {"_id": "a1", "session_id": "s", "role": "assistant",
                                          "content": "", "status": "queued",
                                          "created_at": datetime(2026, 10, 7, 12, 1)}]
    vu = {}

    def fake_ask(**kw):
        vu.update(kw)
        return True, {"response": "ok", "tool_calls": [], "hops": 0, "model": "m",
                      "duration_ms": 1}
    monkeypatch.setattr(alfred_chat.alfred, "_alfred_ask", fake_ask)
    monkeypatch.setattr(alfred_chat, "CONTEXT_MODE", "both")
    alfred_chat._run_ask("a1", "s", {"user_name": "X"}, True, "tok")
    ctx = vu["extra"]["context"]
    assert ctx["conversation_id"] == "s" and ctx["turn_id"] == "a1"
    assert ctx["scope"] == "tok"


def test_erreurs_lisibles():
    assert "NTP" in alfred_chat._friendly_error("http_401")
    assert alfred_chat._friendly_error("global_timeout") == alfred_chat.ERREURS["global_timeout"]
    assert alfred_chat._sources([{"name": "query_parametrages"}, {"name": "query_parametrages"},
                                 {"name": "cockpit_meteo"}]) == [
        {"name": "query_parametrages", "label": "Parametrages"},
        {"name": "cockpit_meteo", "label": "Meteo"}]


# ---------------------------------------------------------------------------
# Client HMAC partage avec WhatsApp
# ---------------------------------------------------------------------------

class _Resp:
    status_code = 200

    def json(self):
        return {"ok": True, "response": "Le depart est a 16:00.", "tool_calls": [],
                "hops": 1, "model": "alfred:latest", "duration_ms": 900}


def _capture(monkeypatch):
    vu = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        vu["data"], vu["headers"], vu["timeout"] = data, headers, timeout
        return _Resp()
    monkeypatch.setattr(alfred, "ALFRED_ASK_SECRET", "s")
    monkeypatch.setattr(alfred.requests, "post", fake_post)
    return vu


def test_payload_whatsapp_inchange(monkeypatch):
    vu = _capture(monkeypatch)
    ok, _ = alfred._alfred_ask(messages=[{"role": "user", "content": "x"}])
    assert ok
    assert set(json.loads(vu["data"])) == {"max_tool_hops", "request_id", "messages"}


def test_options_du_chat_signees(monkeypatch):
    vu = _capture(monkeypatch)
    alfred._alfred_ask(messages=[{"role": "user", "content": "x"}], request_id="chat-1",
                       is_new_mention=True, extra={"channel": "cockpit", "context": {"a": 1}})
    body = json.loads(vu["data"])
    assert body["request_id"] == "chat-1" and body["is_new_mention"] is True
    assert body["channel"] == "cockpit"
    # La signature couvre les octets EXACTS envoyes, options comprises
    ts = vu["headers"]["X-Alfred-Timestamp"]
    attendu = "sha256=" + hmac.new(b"s", ts.encode() + b"." + vu["data"],
                                   hashlib.sha256).hexdigest()
    assert vu["headers"]["X-Alfred-Signature"] == attendu


# ---------------------------------------------------------------------------
# Outils : rendu lisible
# ---------------------------------------------------------------------------

def test_trafic_lisible_et_autoroute_pas_parking(monkeypatch):
    import watch_pages
    monkeypatch.setattr(watch_pages, "build_trafic", lambda db: {
        "t": int(time.time()) - 30, "vd": 2, "ac": 1, "jm": 0, "hz": 0, "z": 1,
        "r": [["A28", "p", 900, 3, 1, 400], ["Panorama", "i", 45, 0, 0, 0]]})
    out = alfred_tools.t_trafic(None, {}, {})
    assert out["verdict"] == "TENSION"
    assert out["axes"][0]["sens"] == "autoroute"
    assert out["axes"][0]["alertes_waze"] == ["accident"]
    assert out["axes"][1]["retard"] == "+0 s"


def test_trafic_alertes_perimees_pas_de_verdict(monkeypatch):
    import watch_pages
    monkeypatch.setattr(watch_pages, "build_trafic", lambda db: {
        "t": None, "vd": None, "ac": None, "jm": None, "hz": None, "z": None, "r": []})
    out = alfred_tools.t_trafic(None, {}, {})
    assert out["verdict"] is None and out["note_verdict"]


def test_categorie_hors_perimetre_refusee():
    ctx = {"cat_query": {"$in": ["PCO.Technique"]}}
    assert alfred_tools._cat_autorisee(ctx, "PCO.Technique")
    assert not alfred_tools._cat_autorisee(ctx, "PCO.Secours")
    assert alfred_tools._cat_autorisee({}, "PCO.Secours")
    assert alfred_tools._normalise_categorie("Sécurité") == "PCO.Securite"
