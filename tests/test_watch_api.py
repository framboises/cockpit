import hashlib

import pytest


@pytest.fixture
def client(monkeypatch):
    """Application Flask minimale portant uniquement le blueprint montre."""
    from flask import Flask
    import watch_api

    watch_api.reset_rate_limit()
    watch_api.reset_cache()

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(watch_api.watch_bp)
    return app.test_client()


class TestHashToken:
    def test_sha256_hexadecimal(self):
        import watch_api
        assert watch_api.hash_token("abc") == hashlib.sha256(b"abc").hexdigest()


class TestBearerRequired:
    def test_sans_entete_401_json(self, client):
        rep = client.get("/api/v1/watch/state")
        assert rep.status_code == 401
        # Surtout pas une redirection : @role_required renvoie 302, ce qui
        # casserait la montre.
        assert rep.get_json() == {"ok": False, "error": "unauthorized"}

    def test_entete_malformee_401(self, client):
        rep = client.get("/api/v1/watch/state",
                         headers={"Authorization": "Token abc"})
        assert rep.status_code == 401

    def test_jeton_inconnu_401(self, client, monkeypatch):
        import watch_api
        monkeypatch.setattr(watch_api, "_db", lambda: _FakeDb())
        rep = client.get("/api/v1/watch/state",
                         headers={"Authorization": "Bearer inexistant"})
        assert rep.status_code == 401

    def test_jeton_revoque_401(self, client, monkeypatch):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": True,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        rep = client.get("/api/v1/watch/state",
                         headers={"Authorization": "Bearer secret"})
        assert rep.status_code == 401


class TestState:
    def test_jeton_valide_renvoie_le_payload(self, client, monkeypatch):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        monkeypatch.setattr(watch_api.watch_state, "build_state",
                            lambda d, n: {"t": 1, "n": "24HM 26", "e": 2,
                                          "er": 3, "w": 4.0, "wl": 1,
                                          "al": []})
        rep = client.get("/api/v1/watch/state",
                         headers={"Authorization": "Bearer secret"})
        assert rep.status_code == 200
        assert rep.get_json()["e"] == 2

    def test_le_cache_evite_un_second_calcul(self, client, monkeypatch):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        appels = {"n": 0}

        def compte(d, n):
            appels["n"] += 1
            return {"t": 1, "n": None, "e": 1, "er": None, "w": None,
                    "wl": 0, "al": []}

        monkeypatch.setattr(watch_api.watch_state, "build_state", compte)
        entetes = {"Authorization": "Bearer secret"}
        client.get("/api/v1/watch/state", headers=entetes)
        client.get("/api/v1/watch/state", headers=entetes)
        assert appels["n"] == 1


class TestRateLimit:
    def test_429_au_dela_du_plafond(self, client, monkeypatch):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        monkeypatch.setattr(watch_api.watch_state, "build_state",
                            lambda d, n: {"t": 1, "n": None, "e": 1,
                                          "er": None, "w": None, "wl": 0,
                                          "al": []})
        entetes = {"Authorization": "Bearer secret"}
        codes = [client.get("/api/v1/watch/state", headers=entetes).status_code
                 for _ in range(watch_api.RATE_LIMIT_MAX + 2)]
        assert codes[-1] == 429
        assert codes[0] == 200

    def test_bascule_exactement_au_seuil(self, client, monkeypatch):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        monkeypatch.setattr(watch_api.watch_state, "build_state",
                            lambda d, n: {"t": 1, "n": None, "e": 1, "er": None,
                                          "w": None, "wl": 0, "al": []})
        entetes = {"Authorization": "Bearer secret"}
        codes = [client.get("/api/v1/watch/state", headers=entetes).status_code
                 for _ in range(watch_api.RATE_LIMIT_MAX + 1)]
        # Les RATE_LIMIT_MAX premieres passent, la suivante est la premiere refusee.
        assert codes[:watch_api.RATE_LIMIT_MAX] == [200] * watch_api.RATE_LIMIT_MAX
        assert codes[watch_api.RATE_LIMIT_MAX] == 429

    def test_entete_retry_after_sur_429(self, client, monkeypatch):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        monkeypatch.setattr(watch_api.watch_state, "build_state",
                            lambda d, n: {"t": 1, "n": None, "e": 1, "er": None,
                                          "w": None, "wl": 0, "al": []})
        entetes = {"Authorization": "Bearer secret"}
        rep = None
        for _ in range(watch_api.RATE_LIMIT_MAX + 1):
            rep = client.get("/api/v1/watch/state", headers=entetes)
        assert rep.status_code == 429
        assert rep.headers.get("Retry-After") == str(watch_api.RATE_LIMIT_WINDOW_S)


class TestIssueToken:
    def test_le_clair_n_est_jamais_stocke(self):
        import watch_api
        db = _FakeDb()
        token = watch_api.issue_token(db, "montre de test", "moi")
        doc = db["watch_tokens"].docs[0]
        assert token  # le clair est bien rendu a l'appelant
        assert doc["token_sha256"] == watch_api.hash_token(token)
        # Le clair ne doit apparaitre dans AUCUNE valeur du document.
        assert token not in repr(doc)
        assert doc.get("revoked") is False


class TestTouchTokenThrottle:
    def test_pas_de_seconde_ecriture_rapprochee(self, monkeypatch):
        # _touch_token lit request.headers (_client_ip) : il faut un contexte
        # de requete Flask, comme en production ou il n'est jamais appele
        # hors d'une route.
        from flask import Flask
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False,
        }])
        with Flask(__name__).test_request_context():
            watch_api._touch_token(db, db["watch_tokens"].docs[0])
            assert len(db["watch_tokens"].updates) == 1
            # Le document relu porte desormais un last_used_at recent : le
            # second appel doit etre bride. Simule ce que rend la base.
            db["watch_tokens"].docs[0]["last_used_at"] = watch_api._maintenant()
            watch_api._touch_token(db, db["watch_tokens"].docs[0])
            assert len(db["watch_tokens"].updates) == 1

    def test_last_used_at_naif_ne_leve_pas(self):
        # Reproduit le bug trouve en bout-en-bout : pymongo relit des datetimes
        # naifs, les comparer a un datetime conscient du fuseau levait
        # TypeError et rendait un 500 des le 2e appel.
        from flask import Flask
        import watch_api
        from datetime import datetime
        naif = datetime(2026, 8, 14, 12, 0)  # naif, comme le rend pymongo
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False, "last_used_at": naif,
        }])
        with Flask(__name__).test_request_context():
            watch_api._touch_token(db, db["watch_tokens"].docs[0])  # ne leve pas


class TestAdminConfigValidation:
    def test_niveau_hors_bornes_refuse(self):
        import watch_api
        ok, erreur = watch_api.validate_config({
            "event_mode": "auto",
            "alerts": [{"slug": "a", "level": 7}],
            "wbgt_levels": [25, 28, 30],
        })
        assert ok is False
        assert erreur == "level_invalide"

    def test_mode_inconnu_refuse(self):
        import watch_api
        ok, erreur = watch_api.validate_config({"event_mode": "parfois"})
        assert ok is False
        assert erreur == "event_mode_invalide"

    def test_epingle_sans_evenement_refuse(self):
        import watch_api
        ok, erreur = watch_api.validate_config({
            "event_mode": "pinned", "event": "", "year": 2026})
        assert ok is False
        assert erreur == "event_requis"

    def test_seuils_non_croissants_refuses(self):
        import watch_api
        ok, erreur = watch_api.validate_config({
            "event_mode": "auto", "wbgt_levels": [30, 28, 25]})
        assert ok is False
        assert erreur == "wbgt_levels_invalides"

    def test_configuration_valide(self):
        import watch_api
        ok, erreur = watch_api.validate_config({
            "event_mode": "pinned", "event": "24H MOTOS", "year": 2026,
            "alerts": [{"slug": "field_sos", "level": 3, "label": "SOS"}],
            "wbgt_levels": [25, 28, 30],
        })
        assert ok is True
        assert erreur is None


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.updates = []

    def find_one(self, query=None, projection=None, sort=None):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in (query or {}).items()):
                return doc
        return None

    def update_one(self, query, update, upsert=False):
        self.updates.append((query, update))

    def insert_one(self, doc):
        self.docs.append(doc)

    def create_index(self, *args, **kwargs):
        pass


class _FakeDb:
    def __init__(self, **collections):
        self._cols = {k: _FakeCollection(v) for k, v in collections.items()}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeCollection([])
        return self._cols[name]
