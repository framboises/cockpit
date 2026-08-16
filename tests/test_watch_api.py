import hashlib
from datetime import datetime, timezone

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
                            lambda d, n, n_utc, **kw: {"t": 1, "n": "24HM 26", "e": 2,
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

        def compte(d, n, n_utc, **kw):
            appels["n"] += 1
            return {"t": 1, "n": None, "e": 1, "er": None, "w": None,
                    "wl": 0, "al": []}

        monkeypatch.setattr(watch_api.watch_state, "build_state", compte)
        entetes = {"Authorization": "Bearer secret"}
        client.get("/api/v1/watch/state", headers=entetes)
        client.get("/api/v1/watch/state", headers=entetes)
        assert appels["n"] == 1


class TestEditions:
    PIC_TS = datetime(2026, 4, 18, 13, 5, 9, tzinfo=timezone.utc)

    def _client_arme(self, client, monkeypatch, editions, pics):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        monkeypatch.setattr(watch_api.watch_peaks, "list_editions",
                            lambda d, now_utc=None: list(editions))
        monkeypatch.setattr(
            watch_api.watch_peaks, "cached_peak",
            lambda d, ev, y, now_utc=None: pics.get((ev, y), (None, None)))
        return {"Authorization": "Bearer secret"}

    def test_sans_jeton_401(self, client):
        assert client.get("/api/v1/watch/editions").status_code == 401

    def test_liste_et_pics_en_une_requete(self, client, monkeypatch):
        entetes = self._client_arme(
            client, monkeypatch,
            editions=[
                {"event": "LE MANS CLASSIC", "year": 2026, "label": "LMC 26"},
                {"event": "24H MOTOS", "year": 2026, "label": "24HM 26"},
            ],
            pics={("LE MANS CLASSIC", 2026): (52409, self.PIC_TS),
                  ("24H MOTOS", 2026): (50690, self.PIC_TS)})
        rep = client.get("/api/v1/watch/editions", headers=entetes)
        assert rep.status_code == 200
        corps = rep.get_json()
        assert corps["ok"] is True
        assert [e["n"] for e in corps["ed"]] == ["LMC 26", "24HM 26"]
        assert corps["ed"][0] == {
            "n": "LMC 26", "ev": "LE MANS CLASSIC", "y": 2026,
            "pk": 52409, "pkt": int(self.PIC_TS.timestamp()),
        }

    def test_edition_sans_pic_est_omise(self, client, monkeypatch):
        # Omise, pas renvoyee avec pk null : une ligne vide se lit comme une
        # edition sans public, alors qu'elle ne dit que l'absence de mesure.
        entetes = self._client_arme(
            client, monkeypatch,
            editions=[
                {"event": "SUPERBIKE", "year": 2026, "label": "SBK 26"},
                {"event": "24H MOTOS", "year": 2026, "label": "24HM 26"},
            ],
            pics={("24H MOTOS", 2026): (50690, self.PIC_TS)})
        corps = client.get("/api/v1/watch/editions", headers=entetes).get_json()
        assert [e["n"] for e in corps["ed"]] == ["24HM 26"]

    def test_le_cache_evite_un_second_calcul(self, client, monkeypatch):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        appels = {"n": 0}

        def compte(d, now_utc=None):
            appels["n"] += 1
            return [{"event": "24H MOTOS", "year": 2026, "label": "24HM 26"}]

        monkeypatch.setattr(watch_api.watch_peaks, "list_editions", compte)
        monkeypatch.setattr(
            watch_api.watch_peaks, "cached_peak",
            lambda d, ev, y, now_utc=None: (50690, self.PIC_TS))
        entetes = {"Authorization": "Bearer secret"}
        client.get("/api/v1/watch/editions", headers=entetes)
        client.get("/api/v1/watch/editions", headers=entetes)
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
                            lambda d, n, n_utc, **kw: {"t": 1, "n": None, "e": 1,
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
                            lambda d, n, n_utc, **kw: {"t": 1, "n": None, "e": 1,
                                                 "er": None, "w": None, "wl": 0,
                                                 "al": []})
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
                            lambda d, n, n_utc, **kw: {"t": 1, "n": None, "e": 1,
                                                 "er": None, "w": None, "wl": 0,
                                                 "al": []})
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


class TestAdminGuardSuperAdmin:
    def _cookie_client(self, client, monkeypatch, global_roles, roles_by_app):
        """Pose un cookie access_token reel, signe avec un secret de test,
        et neutralise CODING pour forcer le chemin production du garde.
        C'est ce chemin, pas le bypass dev, qui distingue super_admin d'un
        role cockpit explicite.

        Le module app n'est jamais importe pour de vrai ici : app.py ouvre
        une connexion MongoDB reelle a l'import (ligne 113), ce qui laisse
        un MongoClient non ferme et un ResourceWarning au shutdown de
        l'interpreteur -- casserait la suite a zero avertissement pour un
        test qui ne veut verifier que la logique du garde. On substitue donc
        un module app minimal, ne portant que les constantes lues par
        _admin_guard ; jwt.decode() reste le vrai decodage PyJWT."""
        import sys
        import types
        import watch_api
        import jwt as pyjwt

        secret = "test-secret-key"
        fake_app = types.ModuleType("app")
        fake_app.CODING = False
        fake_app.JWT_SECRET = secret
        fake_app.JWT_ALGORITHM = "HS256"
        fake_app.APP_KEY = "cockpit"
        fake_app.SUPER_ADMIN_ROLE = "super_admin"
        monkeypatch.setitem(sys.modules, "app", fake_app)
        monkeypatch.setattr(watch_api, "_db", lambda: _FakeDb())

        token = pyjwt.encode(
            {"global_roles": global_roles, "roles_by_app": roles_by_app},
            secret, algorithm="HS256")
        client.set_cookie("access_token", token)

    def test_super_admin_sans_role_cockpit_est_accepte(self, client, monkeypatch):
        # role_required laisse deja passer ce profil sur la page : le garde
        # API doit s'aligner, sinon la page s'ouvre et ne fonctionne pas.
        self._cookie_client(client, monkeypatch,
                            global_roles=["super_admin"], roles_by_app={})
        rep = client.get("/api/v1/watch/admin/config")
        assert rep.status_code == 200

    def test_sans_role_ni_super_admin_est_refuse(self, client, monkeypatch):
        self._cookie_client(client, monkeypatch,
                            global_roles=[], roles_by_app={})
        rep = client.get("/api/v1/watch/admin/config")
        assert rep.status_code == 403


class TestGuidage:
    """Le point de guidage est adresse a UNE montre, alors que le payload
    /state est mis en cache et servi a toutes. C'est le seul endroit de
    l'API ou cette distinction existe, et c'est donc le seul endroit ou une
    fuite d'une montre vers une autre est possible."""

    SECRET_A = "secret-a"
    SECRET_B = "secret-b"

    def _monde(self, monkeypatch):
        """Deux montres enrolees, une base partagee, build_state neutralise."""
        import sys
        import types
        import watch_api
        from bson.objectid import ObjectId
        from conftest import FakeDb

        self.id_a = ObjectId()
        self.id_b = ObjectId()
        db = FakeDb(watch_tokens=[
            {"_id": self.id_a, "token_sha256": watch_api.hash_token(self.SECRET_A),
             "revoked": False, "label": "montre ludo"},
            {"_id": self.id_b, "token_sha256": watch_api.hash_token(self.SECRET_B),
             "revoked": False, "label": "montre adjoint"},
        ], watch_guidage=[])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        monkeypatch.setattr(watch_api.watch_state, "build_state",
                            lambda d, n, n_utc, **kw: {"t": 1, "al": []})
        # CODING=True court-circuite le garde admin, comme en dev.
        # _admin_guard importe les cinq constantes d'un coup AVANT de
        # tester CODING : les omettre leve ImportError, pas un 401.
        fake_app = types.ModuleType("app")
        fake_app.CODING = True
        fake_app.JWT_SECRET = "test"
        fake_app.JWT_ALGORITHM = "HS256"
        fake_app.APP_KEY = "cockpit"
        fake_app.SUPER_ADMIN_ROLE = "super_admin"
        monkeypatch.setitem(sys.modules, "app", fake_app)
        watch_api.watch_guidage.reset_indexes()
        return db

    def _etat(self, client, secret):
        return client.get("/api/v1/watch/state",
                          headers={"Authorization": "Bearer " + secret}).get_json()

    def _envoyer(self, client, token_id, lat, lon, label):
        return client.post("/api/v1/watch/admin/guidage",
                           json={"token_id": str(token_id), "lat": lat,
                                 "lon": lon, "label": label})

    # --- Isolation entre montres ------------------------------------

    def test_deux_montres_ne_partagent_pas_leur_point(self, client, monkeypatch):
        # LA raison d'etre de _avec_guidage. Les deux appels tombent dans la
        # MEME fenetre de cache (CACHE_TTL_S) : si le point entrait dans le
        # payload cache, la seconde montre recevrait celui de la premiere.
        self._monde(monkeypatch)
        self._envoyer(client, self.id_a, 47.95, 0.22, "Porte Houx 5")
        self._envoyer(client, self.id_b, 47.94, 0.23, "Tribune 12")
        a = self._etat(client, self.SECRET_A)
        b = self._etat(client, self.SECRET_B)
        assert a["gd"]["n"] == "Porte Houx 5"
        assert b["gd"]["n"] == "Tribune 12"

    def test_une_montre_sans_point_ne_recoit_rien(self, client, monkeypatch):
        # Variante du precedent, et le cas le plus dangereux : A est guidee,
        # B ne l'est pas. B doit recevoir null, pas le point de A.
        self._monde(monkeypatch)
        self._envoyer(client, self.id_a, 47.95, 0.22, "Porte Houx 5")
        assert self._etat(client, self.SECRET_A)["gd"] is not None
        b = self._etat(client, self.SECRET_B)
        assert b["gd"] is None
        assert b["gs"] is None

    def test_le_payload_cache_reste_vierge(self, client, monkeypatch):
        # Le cache partage ne doit JAMAIS porter de guidage : sinon la
        # premiere montre servie contaminerait le cache pour 20 secondes.
        import watch_api
        self._monde(monkeypatch)
        self._envoyer(client, self.id_a, 47.95, 0.22, "Porte Houx 5")
        self._etat(client, self.SECRET_A)
        assert "gd" not in watch_api._cache["payload"]
        assert "gs" not in watch_api._cache["payload"]

    # --- Forme du payload -------------------------------------------

    def test_gd_porte_le_point_et_gs_la_sequence(self, client, monkeypatch):
        # gd va dans les blocs de pages cote montre, gs dans le noyau : c'est
        # gs, et lui seul, que lit le service de fond pour vibrer.
        self._monde(monkeypatch)
        self._envoyer(client, self.id_a, 47.95, 0.22, "Porte Houx 5")
        etat = self._etat(client, self.SECRET_A)
        assert etat["gd"] == {"lat": 47.95, "lon": 0.22, "n": "Porte Houx 5",
                              "s": 1, "t": etat["gd"]["t"]}
        assert etat["gs"] == 1

    def test_sans_guidage_les_deux_champs_sont_nuls(self, client, monkeypatch):
        self._monde(monkeypatch)
        etat = self._etat(client, self.SECRET_A)
        assert etat["gd"] is None
        assert etat["gs"] is None

    def test_renvoi_du_meme_point_fait_monter_la_sequence(self, client, monkeypatch):
        # C'est ce que la montre observe pour vibrer une seconde fois.
        self._monde(monkeypatch)
        self._envoyer(client, self.id_a, 47.95, 0.22, "Porte Houx 5")
        self._envoyer(client, self.id_a, 47.95, 0.22, "Porte Houx 5")
        assert self._etat(client, self.SECRET_A)["gs"] == 2

    def test_guidage_illisible_ne_casse_pas_le_payload(self, client, monkeypatch):
        # Regle commune a tous les blocs : une source abimee met le bloc a
        # null, elle ne fait pas tomber le reste de l'etat.
        import watch_api
        self._monde(monkeypatch)
        monkeypatch.setattr(watch_api.watch_guidage, "read_point",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
        etat = self._etat(client, self.SECRET_A)
        assert etat["gd"] is None
        assert etat["t"] == 1        # le reste du payload est intact

    # --- Routes d'administration ------------------------------------

    def test_envoi_nominal(self, client, monkeypatch):
        self._monde(monkeypatch)
        rep = self._envoyer(client, self.id_a, 47.95, 0.22, "Porte Houx 5")
        assert rep.status_code == 200
        assert rep.get_json() == {"ok": True, "seq": 1, "label": "Porte Houx 5"}

    def test_coordonnees_invalides_refusees(self, client, monkeypatch):
        self._monde(monkeypatch)
        rep = self._envoyer(client, self.id_a, "au nord", 0.22, "X")
        assert rep.status_code == 400
        assert rep.get_json()["error"] == "coordonnees_invalides"

    def test_libelle_vide_refuse(self, client, monkeypatch):
        self._monde(monkeypatch)
        rep = self._envoyer(client, self.id_a, 47.95, 0.22, "  ")
        assert rep.status_code == 400
        assert rep.get_json()["error"] == "libelle_vide"

    def test_montre_absente_du_corps(self, client, monkeypatch):
        self._monde(monkeypatch)
        rep = client.post("/api/v1/watch/admin/guidage",
                          json={"lat": 47.95, "lon": 0.22, "label": "X"})
        assert rep.status_code == 400
        assert rep.get_json()["error"] == "montre_absente"

    def test_identifiant_de_montre_malforme(self, client, monkeypatch):
        # Un ObjectId invalide leverait InvalidId et rendrait un 500.
        self._monde(monkeypatch)
        rep = self._envoyer(client, "pas-un-objectid", 47.95, 0.22, "X")
        assert rep.status_code == 400

    def test_montre_inconnue_refusee(self, client, monkeypatch):
        from bson.objectid import ObjectId
        self._monde(monkeypatch)
        rep = self._envoyer(client, ObjectId(), 47.95, 0.22, "X")
        assert rep.status_code == 404

    def test_montre_revoquee_refusee(self, client, monkeypatch):
        # Ecrire un point pour un jeton revoque le laisserait en base sans
        # que rien ne le lise, et l'operateur croirait avoir envoye.
        db = self._monde(monkeypatch)
        db["watch_tokens"].docs[0]["revoked"] = True
        rep = self._envoyer(client, self.id_a, 47.95, 0.22, "X")
        assert rep.status_code == 404

    def test_effacement(self, client, monkeypatch):
        self._monde(monkeypatch)
        self._envoyer(client, self.id_a, 47.95, 0.22, "X")
        rep = client.delete("/api/v1/watch/admin/guidage/" + str(self.id_a))
        assert rep.get_json() == {"ok": True, "efface": True}
        assert self._etat(client, self.SECRET_A)["gd"] is None

    def test_effacement_sans_point_ne_ment_pas(self, client, monkeypatch):
        self._monde(monkeypatch)
        rep = client.delete("/api/v1/watch/admin/guidage/" + str(self.id_a))
        assert rep.get_json()["efface"] is False

    def test_liste_pour_la_carte(self, client, monkeypatch):
        # La carte du cockpit doit montrer quelle montre est deja guidee,
        # sinon l'operateur ecraserait un guidage en cours sans le savoir.
        self._monde(monkeypatch)
        self._envoyer(client, self.id_a, 47.95, 0.22, "Porte Houx 5")
        liste = client.get("/api/v1/watch/admin/guidage").get_json()["guidage"]
        assert liste[str(self.id_a)]["label"] == "Porte Houx 5"
        assert str(self.id_b) not in liste


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.updates = []

    def find_one(self, query=None, projection=None, sort=None):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in (query or {}).items()):
                return doc
        return None

    def find(self, query=None, projection=None, sort=None):
        return [doc for doc in self.docs
                if all(doc.get(k) == v for k, v in (query or {}).items())]

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
