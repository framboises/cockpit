"""Tests du client du pont Bibler (bibler_pont.py)."""
import jwt
import pytest
import requests

import bibler_pont

SECRET = "s" * 40


class FauxReponse:
    def __init__(self, statut=200, corps=None, brut=None, ctype="application/json"):
        self.status_code = statut
        self._corps = corps if corps is not None else {"ok": True}
        self.headers = {"Content-Type": ctype}
        self._brut = brut
        self.is_redirect = False

    def _octets(self):
        if self._brut is not None:
            return self._brut
        import json
        return json.dumps(self._corps).encode()

    def iter_content(self, chunk_size=1):
        b = self._octets()
        for i in range(0, len(b), 65536):
            yield b[i:i + 65536]

    def close(self):
        self.ferme = True


class FausseSession:
    def __init__(self, reponse=None, erreur=None):
        self.reponse = reponse or FauxReponse()
        self.erreur = erreur
        self.appels = []

    def request(self, methode, url, **kw):
        self.appels.append((methode, url, kw))
        if self.erreur:
            raise self.erreur
        return self.reponse


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("COCKPIT_PONT_URL_BIBLER", "http://bibler.test")
    monkeypatch.setenv("COCKPIT_PONT_SECRET_BIBLER", SECRET)
    bibler_pont._CACHE.clear()


def _session(monkeypatch, **kw):
    s = FausseSession(**kw)
    monkeypatch.setattr(bibler_pont, "_session", lambda: s)
    return s


def test_non_configure(monkeypatch):
    monkeypatch.delenv("COCKPIT_PONT_URL_BIBLER", raising=False)
    monkeypatch.delenv("COCKPIT_PONT_SECRET_BIBLER", raising=False)
    assert bibler_pont.configure() is False
    with pytest.raises(bibler_pont.BiblerNonConfigure):
        bibler_pont.lire("/api/v1/postes")


def test_secret_trop_court(monkeypatch):
    monkeypatch.setenv("COCKPIT_PONT_URL_BIBLER", "http://bibler.test")
    monkeypatch.setenv("COCKPIT_PONT_SECRET_BIBLER", "court")
    assert bibler_pont.configure() is False


def test_claims_du_jeton(env):
    t = bibler_pont.jeton(("postes:lire",))
    c = jwt.decode(t, SECRET, algorithms=["HS256"], audience="bibler")
    assert c["iss"] == "cockpit"
    assert c["aud"] == "bibler"
    assert c["sub"] == "service:cockpit"
    assert c["exp"] - c["iat"] == 60
    assert c["capacites"] == ["postes:lire"]
    assert c["jti"]


def test_chemin_refuse(env, monkeypatch):
    _session(monkeypatch)
    for chemin in ("/api/v1/../x", "http://x/api/v1/", "/autre", "/api/v1//x",
                   "/api/v1/postes/%2e%2e/x", "/api/v1/%2f"):
        with pytest.raises(ValueError):
            bibler_pont.lire(chemin)


def test_delai_depasse(env, monkeypatch):
    _session(monkeypatch, erreur=requests.Timeout("lent"))
    with pytest.raises(bibler_pont.BiblerIndisponible):
        bibler_pont.lire("/api/v1/postes")


def test_5xx_indisponible(env, monkeypatch):
    _session(monkeypatch, reponse=FauxReponse(503, {"ok": False}))
    with pytest.raises(bibler_pont.BiblerIndisponible):
        bibler_pont.lire("/api/v1/postes")


def test_erreur_4xx_relayee(env, monkeypatch):
    _session(monkeypatch, reponse=FauxReponse(409, {"erreur": "version_perimee"}))
    with pytest.raises(bibler_pont.BiblerErreur) as e:
        bibler_pont.envoyer("PATCH", "/api/v1/consignes/1", {})
    assert e.value.statut == 409
    assert e.value.corps["erreur"] == "version_perimee"


def test_reponse_trop_grosse_content_length(env, monkeypatch):
    rep = FauxReponse(200, brut=b"x")
    rep.headers["Content-Length"] = str(5 * 1024 * 1024 + 1)
    lus = []
    rep.iter_content = lambda chunk_size=1: lus.append(1) or iter([b"x"])
    _session(monkeypatch, reponse=rep)
    with pytest.raises(bibler_pont.BiblerIndisponible):
        bibler_pont.lire("/api/v1/postes")
    assert lus == [] and rep.ferme


def test_reponse_trop_grosse_en_cours_de_lecture(env, monkeypatch):
    rep = FauxReponse(200, brut=b"x" * (5 * 1024 * 1024 + 1))
    _session(monkeypatch, reponse=rep)
    with pytest.raises(bibler_pont.BiblerIndisponible):
        bibler_pont.lire("/api/v1/postes")
    assert rep.ferme


def test_3xx_indisponible(env, monkeypatch):
    _session(monkeypatch, reponse=FauxReponse(302, {}))
    with pytest.raises(bibler_pont.BiblerIndisponible):
        bibler_pont.lire("/api/v1/postes")


def test_cache_params_liste_et_copie(env, monkeypatch):
    s = _session(monkeypatch, reponse=FauxReponse(200, {"ok": True, "l": [1]}))
    a = bibler_pont.lire("/api/v1/postes", {"ids": [1, 2]}, ttl=30)
    a["l"].append(99)
    b = bibler_pont.lire("/api/v1/postes", {"ids": [1, 2]}, ttl=30)
    assert len(s.appels) == 1
    assert b["l"] == [1]


def test_cache(env, monkeypatch):
    s = _session(monkeypatch)
    bibler_pont.lire("/api/v1/postes", {"a": 1}, ttl=30)
    bibler_pont.lire("/api/v1/postes", {"a": 1}, ttl=30)
    assert len(s.appels) == 1
    bibler_pont.lire("/api/v1/postes", {"a": 2}, ttl=30)
    assert len(s.appels) == 2


def test_idempotence_en_tete(env, monkeypatch):
    s = _session(monkeypatch)
    corps, statut = bibler_pont.envoyer("POST", "/api/v1/consignes", {"t": 1}, idempotence="abc")
    assert statut == 200 and corps["ok"] is True
    kw = s.appels[0][2]
    assert kw["headers"]["Idempotency-Key"] == "abc"
    assert kw["timeout"] == (3, 8)
    assert kw["allow_redirects"] is False
    assert kw["headers"]["Authorization"].startswith("Bearer ")


def test_lire_binaire(env, monkeypatch):
    _session(monkeypatch, reponse=FauxReponse(200, brut=b"PNG", ctype="image/png"))
    assert bibler_pont.lire_binaire("/api/v1/postes/1/photo") == (b"PNG", "image/png")


@pytest.mark.parametrize("url,attendu", [
    ("ftp://x", False),
    ("http://user:pw@h", False),
    ("http://h/?a=1", False),
    ("http://h#f", False),
    ("http://h/chemin", False),
    ("http://127.0.0.1:5010", True),
    ("https://bibler.lemans.org", True),
])
def test_validation_url(monkeypatch, url, attendu):
    monkeypatch.setenv("COCKPIT_PONT_URL_BIBLER", url)
    monkeypatch.setenv("COCKPIT_PONT_SECRET_BIBLER", SECRET)
    assert bibler_pont.configure() is attendu
    if not attendu:
        with pytest.raises(bibler_pont.BiblerNonConfigure):
            bibler_pont.lire("/api/v1/postes")


def test_avertissement_http_une_fois(monkeypatch, caplog):
    monkeypatch.setattr(bibler_pont, "_AVERTI_HTTP", False)
    monkeypatch.setenv("COCKPIT_PONT_URL_BIBLER", "http://bibler.exemple")
    monkeypatch.setenv("COCKPIT_PONT_SECRET_BIBLER", SECRET)
    with caplog.at_level("WARNING"):
        assert bibler_pont.configure() and bibler_pont.configure()
    assert len([r for r in caplog.records if "http" in r.getMessage()]) == 1
