"""Circuit breaker partage et envois directs (reponses Alfred) de whatsapp.py.

Aucun appel reseau : requests.post est remplace par un faux qui compte les
appels. La base est le double de conftest, complete de insert_one (seul
manque pour WhatsAppService._record_send).
"""

from datetime import datetime, timedelta, timezone

import pytest

import whatsapp
from conftest import FakeCollection, FakeDb  # noqa: E402


class _Col(FakeCollection):
    def insert_one(self, doc):
        self.docs.append(dict(doc))


class _Db(FakeDb):
    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _Col([])
        return self._cols[name]


def _make_db(**cfg):
    conf = {
        "_id": "wa_config", "enabled": True,
        "waha_url": "http://waha.invalid", "session_name": "default",
        "rate_limit_per_hour": 20, "rate_limit_per_day": 100,
        "global_cooldown_minutes": 10, "type_cooldown_minutes": 30,
        "quiet_hours": {"enabled": False},
    }
    conf.update(cfg)
    db = _Db()
    db["cockpit_wa_config"].docs.append(conf)
    return db


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(payload)
        self.content = b"x"

    def json(self):
        return self._payload


class _FakePost:
    def __init__(self, status=201):
        self.status = status
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if self.status in (200, 201):
            return _Resp(self.status, {"id": "wamid-%d" % len(self.calls)})
        return _Resp(self.status, {"error": "ko"})


@pytest.fixture(autouse=True)
def _reset_breaker():
    whatsapp._breaker_reset_memory()
    yield
    whatsapp._breaker_reset_memory()


@pytest.fixture
def post(monkeypatch):
    fp = _FakePost()
    monkeypatch.setattr(whatsapp.requests, "post", fp)
    return fp


# ---------------------------------------------------------------------------
# Breaker partage
# ---------------------------------------------------------------------------

def test_breaker_s_ouvre_meme_avec_une_instance_par_envoi(post):
    """Le defaut d'origine : une instance par envoi remettait le compteur a 0."""
    db = _make_db()
    post.status = 500
    for _ in range(whatsapp.CIRCUIT_BREAKER_THRESHOLD):
        whatsapp.WhatsAppService(db)._send_text("g@g.us", "x")
    assert whatsapp.WhatsAppService(db)._is_circuit_open() is True
    assert whatsapp.WhatsAppService(db)._circuit_open_until is not None


def test_breaker_survit_a_un_nouveau_process_via_mongo(post):
    """alert_engine = un process par cycle : l'etat doit venir de Mongo."""
    db = _make_db()
    post.status = 500
    for _ in range(whatsapp.CIRCUIT_BREAKER_THRESHOLD):
        whatsapp.WhatsAppService(db)._send_text("g@g.us", "x")
    whatsapp._breaker_reset_memory()  # "nouveau process"
    assert whatsapp.WhatsAppService(db)._is_circuit_open() is True
    doc = db["cockpit_wa_config"].find_one({"_id": whatsapp.BREAKER_DOC_ID})
    assert doc["consecutive_errors"] >= whatsapp.CIRCUIT_BREAKER_THRESHOLD


def test_erreurs_de_deux_process_s_additionnent(post):
    db = _make_db()
    post.status = 500
    whatsapp.WhatsAppService(db)._send_text("g@g.us", "x")
    whatsapp._breaker_reset_memory()
    whatsapp.WhatsAppService(db)._send_text("g@g.us", "x")
    whatsapp._breaker_reset_memory()
    whatsapp.WhatsAppService(db)._send_text("g@g.us", "x")
    assert whatsapp.WhatsAppService(db)._is_circuit_open() is True


def test_succes_remet_le_compteur_a_zero(post):
    db = _make_db()
    post.status = 500
    whatsapp.WhatsAppService(db)._send_text("g@g.us", "x")
    whatsapp.WhatsAppService(db)._send_text("g@g.us", "x")
    post.status = 201
    whatsapp.WhatsAppService(db)._send_text("g@g.us", "x")
    doc = db["cockpit_wa_config"].find_one({"_id": whatsapp.BREAKER_DOC_ID})
    assert doc["consecutive_errors"] == 0
    post.status = 500
    whatsapp.WhatsAppService(db)._send_text("g@g.us", "x")
    assert whatsapp.WhatsAppService(db)._is_circuit_open() is False


def test_breaker_expire_se_referme_en_base():
    db = _make_db()
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    db["cockpit_wa_config"].docs.append({
        "_id": whatsapp.BREAKER_DOC_ID, "consecutive_errors": 5, "open_until": past,
    })
    assert whatsapp.WhatsAppService(db)._is_circuit_open() is False
    doc = db["cockpit_wa_config"].find_one({"_id": whatsapp.BREAKER_DOC_ID})
    assert doc["open_until"] is None and doc["consecutive_errors"] == 0


def test_get_stats_expose_le_breaker_partage():
    db = _make_db()
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    db["cockpit_wa_config"].docs.append({
        "_id": whatsapp.BREAKER_DOC_ID, "consecutive_errors": 3, "open_until": future,
    })
    stats = whatsapp.WhatsAppService(db).get_stats()
    assert stats["circuit_breaker"] == "open"
    assert stats["circuit_breaker_until"] == future.isoformat()


# ---------------------------------------------------------------------------
# send_direct : comptage et plafonds
# ---------------------------------------------------------------------------

def _history(db):
    return db["cockpit_wa_send_history"].docs


def _fill_history(db, n, minutes_ago=5):
    at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    for _ in range(n):
        db["cockpit_wa_send_history"].insert_one(
            {"sentAt": at, "status": "sent", "recipient_id": "other@g.us"})


def test_reponse_alfred_est_comptee_dans_l_historique(post):
    db = _make_db()
    mid = whatsapp.WhatsAppService(db).send_direct("g@g.us", "Bonjour", kind="alfred_reply")
    assert mid
    h = _history(db)
    assert len(h) == 1
    assert h[0]["status"] == "sent" and h[0]["source"] == "direct"
    assert h[0]["alert_slug"] == "direct:alfred_reply"
    assert h[0]["recipient_type"] == "group"
    # Comptee dans le plafond horaire que lisent aussi les alertes
    assert whatsapp.WhatsAppService(db)._count_sent_since(
        datetime.now(timezone.utc) - timedelta(hours=1)) == 1


def test_reponse_refusee_au_plafond_journalier(post):
    db = _make_db(rate_limit_per_day=10, rate_limit_per_hour=50)
    _fill_history(db, 10, minutes_ago=120)
    assert whatsapp.WhatsAppService(db).send_direct("g@g.us", "x") is None
    assert post.calls == []


def test_reponse_refusee_au_plafond_horaire(post):
    db = _make_db(rate_limit_per_hour=5)
    _fill_history(db, 5)
    assert whatsapp.WhatsAppService(db).send_direct("g@g.us", "x") is None
    assert post.calls == []


def test_interim_saute_pres_des_plafonds_mais_reponse_passe(post):
    db = _make_db(rate_limit_per_hour=10)
    _fill_history(db, 8)  # 80 % du plafond horaire
    svc = whatsapp.WhatsAppService(db)
    assert svc.send_direct("g@g.us", "Un instant", priority=whatsapp.PRIORITY_INTERIM) is None
    assert svc.send_direct("g@g.us", "Reponse", priority=whatsapp.PRIORITY_REPLY)
    assert len(post.calls) == 1


def test_heures_silencieuses_ne_bloquent_que_la_priorite_basse(post, monkeypatch):
    db = _make_db()
    monkeypatch.setattr(whatsapp.WhatsAppService, "_is_quiet_hours", lambda self: True)
    svc = whatsapp.WhatsAppService(db)
    assert svc.send_direct("33600000000@c.us", "Refus", priority=whatsapp.PRIORITY_LOW) is None
    assert svc.send_direct("33600000000@c.us", "Reponse", priority=whatsapp.PRIORITY_REPLY)


def test_envoi_direct_refuse_si_breaker_ouvert(post):
    db = _make_db()
    db["cockpit_wa_config"].docs.append({
        "_id": whatsapp.BREAKER_DOC_ID, "consecutive_errors": 3,
        "open_until": datetime.now(timezone.utc) + timedelta(minutes=10),
    })
    assert whatsapp.WhatsAppService(db).send_direct("g@g.us", "x") is None
    assert post.calls == []


def test_echec_waha_trace_en_erreur(post):
    db = _make_db()
    post.status = 500
    assert whatsapp.WhatsAppService(db).send_direct("g@g.us", "x") is None
    assert _history(db)[0]["status"] == "error"


def test_reponse_alfred_ne_declenche_pas_le_cooldown_destinataire_des_alertes(post):
    db = _make_db()
    svc = whatsapp.WhatsAppService(db)
    assert svc.send_direct("g@g.us", "x")
    assert svc._check_cooldown_recipient("g@g.us") is True
