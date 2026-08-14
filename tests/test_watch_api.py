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
