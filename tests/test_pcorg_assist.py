"""Tests de pcorg_assist : similarite, doublons, precedents, validation des
suggestions du modele, routes (auth, CSRF, cache de synthese).

Aucun appel reseau : _claude_stream_request est remplace par un double.
"""
from datetime import datetime, timedelta, timezone

import pytest

import pcorg_assist as PA
from conftest import FakeCollection, FakeDb


def _doc(_id, text, **kw):
    d = {"_id": _id, "text": text, "event": kw.pop("event", "24H AUTOS"),
         "year": kw.pop("year", 2026), "category": kw.pop("category", "PCO.Flux"),
         "status_code": kw.pop("status_code", 0),
         "ts": kw.pop("ts", datetime(2026, 6, 13, 14, 0, tzinfo=timezone.utc))}
    if "zone" in kw:
        d["area"] = {"desc": kw.pop("zone")}
    if "latlon" in kw:
        lat, lon = kw.pop("latlon")
        d["gps"] = {"type": "Point", "coordinates": [lon, lat]}
    if "sous" in kw:
        d["content_category"] = {"sous_classification": kw.pop("sous")}
    d.update(kw)
    return d


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_accents_mots_vides_pluriel(self):
        assert PA.tokenize("Les véhicules bloqués à la Porte 5") == ["vehicule", "bloque", "porte", "5"]

    def test_ponctuation_et_casse(self):
        assert PA.tokenize("MALAISE, tribune-Nord !") == ["malaise", "tribune", "nord"]

    def test_vide(self):
        assert PA.tokenize("") == []
        assert PA.tokenize(None) == []

    def test_search_terms_sans_doublon_ni_mot_vide(self):
        terms = PA.search_terms("la porte de la Porte nord et le parking")
        assert "la" not in terms and "et" not in terms
        assert terms.count("porte") == 1
        assert set(terms) == {"porte", "nord", "parking"}


# ---------------------------------------------------------------------------
# Doublons (F3)
# ---------------------------------------------------------------------------

class TestRankDuplicates:
    def test_texte_proche_en_tete_hors_sujet_ecarte(self):
        docs = [
            _doc("a", "Bouchon important parking Houx sortie saturee"),
            _doc("b", "Malaise spectateur tribune nord"),
            _doc("c", "Parking Houx sortie saturee vehicules bloques"),
        ]
        out = PA.rank_duplicates("Sortie parking Houx saturee, vehicules bloques", docs)
        ids = [o["id"] for o in out]
        assert ids[0] == "c"
        assert "a" in ids
        assert "b" not in ids

    def test_au_plus_trois(self):
        docs = [_doc(str(i), "Congestion pietons porte nord numero %d" % i) for i in range(6)]
        out = PA.rank_duplicates("Congestion pietons porte nord", docs)
        assert len(out) == 3

    def test_proximite_gps_departage(self):
        docs = [
            _doc("loin", "Altercation buvette", latlon=(47.95, 0.20)),
            _doc("pres", "Altercation buvette", latlon=(47.9500, 0.2100)),
        ]
        docs[0]["gps"]["coordinates"] = [0.30, 47.95]  # ~7 km
        out = PA.rank_duplicates("Altercation buvette", docs, lat=47.9501, lng=0.2101)
        assert out[0]["id"] == "pres"
        assert out[0]["distance_m"] < 100
        assert out[1]["id"] == "loin" and out[1]["distance_m"] > 5000

    def test_meme_zone_bonus(self):
        docs = [
            _doc("z1", "Barriere cassee acces", zone="PARKING OUEST"),
            _doc("z2", "Barriere cassee acces", zone="PARKING EST"),
        ]
        out = PA.rank_duplicates("Barriere cassee acces", docs, area_desc="_MC PCO/Parking Ouest")
        assert out[0]["id"] == "z1" and out[0]["same_zone"] is True
        assert out[1]["same_zone"] is False

    def test_zone_seule_ne_suffit_pas(self):
        docs = [_doc("x", "Livraison traiteur", zone="PADDOCK")]
        assert PA.rank_duplicates("Malaise spectateur", docs, area_desc="PADDOCK") == []

    def test_texte_vide_ou_sans_candidat(self):
        assert PA.rank_duplicates("", [_doc("a", "abc")]) == []
        assert PA.rank_duplicates("texte", []) == []

    def test_champs_serialises(self):
        out = PA.rank_duplicates("Malaise tribune nord", [_doc("a", "Malaise tribune nord", niveau_urgence="UA")])
        item = out[0]
        assert item["id"] == "a" and item["niveau_urgence"] == "UA"
        assert item["ts"].startswith("2026-06-13T16:00:00")  # heure de Paris
        assert item["excerpt"] == "Malaise tribune nord"


# ---------------------------------------------------------------------------
# Precedents (F4)
# ---------------------------------------------------------------------------

class TestRankPrecedents:
    def test_bonus_sous_classification_et_seuil_texte(self):
        fiche = _doc("f", "Congestion vehicules sortie parking Houx", sous="Congestion vehicules")
        cands = [
            _doc("p1", "Congestion sortie parking Houx", year=2025),
            _doc("p2", "Congestion sortie parking Houx", year=2024, sous="Congestion vehicules"),
            _doc("p3", "Demande de badge accreditation", year=2025),
        ]
        ranked = PA.rank_precedents(fiche, cands)
        ids = [r[1]["_id"] for r in ranked]
        assert ids[:2] == ["p2", "p1"]
        assert "p3" not in ids
        assert "sous_classification" in ranked[0][2]

    def test_limite_top(self):
        fiche = _doc("f", "Enfant perdu tribune")
        cands = [_doc("p%d" % i, "Enfant perdu tribune %d" % i, year=2020 + i % 5) for i in range(12)]
        assert len(PA.rank_precedents(fiche, cands)) == PA.PRECEDENT_TOP

    def test_scope_filter(self):
        fiche = _doc("f", "x", event="24H MOTOS", year=2026)
        q = PA.scope_filter(fiche, "editions", None)
        assert q["event"] == "24H MOTOS" and q["year"] == {"$ne": 2026}
        assert q["_id"] == {"$ne": "f"}
        q_all = PA.scope_filter(fiche, "all", ["PCO.Flux"])
        assert q_all["$nor"] == [{"event": "24H MOTOS", "year": 2026}]
        assert q_all["category"] == {"$in": ["PCO.Flux"]}

    def test_scope_filter_saison_exclut_30_jours(self):
        fiche = _doc("s", "x", event="SAISON", year=2026, ts=datetime(2026, 3, 15, 10, 0))
        q = PA.scope_filter(fiche, "editions", None)
        assert q["event"] == "SAISON" and "year" not in q
        rng = q["$nor"][0]["ts"]
        assert rng["$gte"] == datetime(2026, 2, 13, 10, 0)
        assert rng["$lte"] == datetime(2026, 4, 14, 10, 0)
        q_all = PA.scope_filter(fiche, "all", None)
        assert q_all["$nor"][0]["event"] == "SAISON" and "ts" in q_all["$nor"][0]

    def test_closing_info_et_duree(self):
        doc = _doc("p", "Malaise", status_code=10,
                   close_ts=datetime(2026, 6, 13, 14, 45),
                   comment_history=[
                       {"ts": "2026-06-13T16:05:00+02:00", "operator": "DUPONT Jean [PCO]",
                        "text": "CMS engage sur place"},
                       {"ts": "2026-06-13T16:45:00+02:00", "operator": "DUPONT Jean",
                        "text": "Statut: En cours -> Termine\nVictime evacuee vers le CMS"},
                   ])
        info = PA.closing_info(doc)
        assert info["closing_comment"] == "Victime evacuee vers le CMS"
        assert [e["text"] for e in info["last_entries"]] == ["CMS engage sur place", "Victime evacuee vers le CMS"]
        assert info["last_entries"][0]["operator"] == "DUPONT Jean"
        assert PA._duration_min(doc) == 45

    def test_duree_fiche_ouverte_ou_sentinelle(self):
        assert PA._duration_min(_doc("o", "x")) is None
        sentinel = _doc("s", "x", status_code=10, close_ts=datetime(9000, 1, 1))
        assert PA._duration_min(sentinel) is None


# ---------------------------------------------------------------------------
# Suggestions (F3) : prompt, parsing, validation
# ---------------------------------------------------------------------------

SOUS = {
    "PCO.Flux": ["Congestion vehicules", "Congestion pietons"],
    "PCO.Securite": ["Enfant perdu", "Altercation-Rixe"],
}
URG = {"PCO.Secours", "PCO.Securite"}
ALLOWED = ["PCO.Flux", "PCO.Securite", "PCO.Secours"]


class TestValidateSuggestion:
    def test_categorie_inventee_ecartee(self):
        raw = {"category": "PCO.Incendie", "confidence": 0.9, "reason": "x"}
        assert PA.validate_suggestion(raw, ALLOWED, SOUS, URG) is None

    def test_categorie_non_autorisee_ecartee(self):
        raw = {"category": "PCO.Technique", "zone": "Porte 5"}
        out = PA.validate_suggestion(raw, ALLOWED, SOUS, URG)
        assert out["category"] is None and out["zone"] == "Porte 5"

    def test_sous_classification_canonique(self):
        raw = {"category": "PCO.Flux", "sous_classification": "congestion  VÉHICULES"}
        out = PA.validate_suggestion(raw, ALLOWED, SOUS, URG)
        assert out["category"] == "PCO.Flux"
        assert out["sous_classification"] == "Congestion vehicules"
        assert out["sous_classification_category"] == "PCO.Flux"

    def test_sous_classification_d_une_autre_categorie_ecartee(self):
        raw = {"category": "PCO.Flux", "sous_classification": "Enfant perdu"}
        assert PA.validate_suggestion(raw, ALLOWED, SOUS, URG)["sous_classification"] is None

    def test_urgence_seulement_si_categorie_configuree(self):
        flux = PA.validate_suggestion({"category": "PCO.Flux", "niveau_urgence": "UA"}, ALLOWED, SOUS, URG)
        assert flux["niveau_urgence"] is None
        secu = PA.validate_suggestion({"category": "PCO.Securite", "niveau_urgence": "ua"}, ALLOWED, SOUS, URG)
        assert secu["niveau_urgence"] == "UA"
        assert secu["niveau_urgence_label"] == "Incident grave"

    def test_urgence_invalide_ecartee(self):
        out = PA.validate_suggestion({"category": "PCO.Securite", "niveau_urgence": "P1"}, ALLOWED, SOUS, URG)
        assert out["niveau_urgence"] is None

    def test_categorie_courante_sert_de_reference(self):
        raw = {"category": None, "sous_classification": "Enfant perdu", "niveau_urgence": "UR"}
        out = PA.validate_suggestion(raw, ALLOWED, SOUS, URG, current_category="PCO.Securite")
        assert out["category"] is None
        assert out["sous_classification"] == "Enfant perdu"
        assert out["niveau_urgence"] == "UR"

    def test_confiance_bornee(self):
        assert PA.validate_suggestion({"category": "PCO.Flux", "confidence": 7}, ALLOWED, SOUS, URG)["confidence"] == 1.0
        assert PA.validate_suggestion({"category": "PCO.Flux", "confidence": "abc"}, ALLOWED, SOUS, URG)["confidence"] == 0.5

    def test_non_dict(self):
        assert PA.validate_suggestion(None, ALLOWED, SOUS, URG) is None
        assert PA.validate_suggestion(["PCO.Flux"], ALLOWED, SOUS, URG) is None


class TestPromptsEtParsing:
    def test_extract_json_avec_balises(self):
        txt = '```json\n{"category": "PCO.Flux", "confidence": 0.8}\n```'
        assert PA.extract_json_object(txt) == {"category": "PCO.Flux", "confidence": 0.8}
        assert PA.extract_json_object("pas de json") is None
        assert PA.extract_json_object("{casse") is None

    def test_prompt_limite_aux_categories_autorisees(self):
        system, user = PA.build_suggest_prompts("Bouchon sortie Houx", ["PCO.Flux"], SOUS, URG,
                                                area_desc="Parking Houx", current_category="PCO.Flux")
        assert "PCO.Flux : Circulation, acces, jauge" in system
        assert "PCO.Securite" not in system
        assert "Congestion vehicules | Congestion pietons" in system
        assert "toujours null" in system  # Flux n'a pas d'urgence configuree
        assert "Bouchon sortie Houx" in user and "Parking Houx" in user

    def test_prompt_urgence_libelles_app(self):
        system, _ = PA.build_suggest_prompts("x", ["PCO.Secours"], {}, URG)
        assert "Detresse vitale (Secours)" in system

    def test_parse_synthesis_json_et_repli(self):
        out = PA.parse_synthesis('{"points": ["A (24H AUTOS 2025)", " "], "fiabilite": "Bonne"}')
        assert out == {"points": ["A (24H AUTOS 2025)"], "fiabilite": "bonne"}
        out2 = PA.parse_synthesis("Voici :\n- premier point\n- second point\n")
        assert out2["points"] == ["premier point", "second point"]
        assert out2["fiabilite"] == "moyenne"

    def test_synthesis_prompt_contient_precedents(self):
        fiche = _doc("f", "Malaise tribune")
        prec = [{"edition": "24H AUTOS 2025", "ts": "2025-06-14T15:00:00+02:00",
                 "category": "PCO.Secours", "excerpt": "Malaise tribune T34",
                 "duration_min": 25, "status_closed": True,
                 "closing_comment": "Evacue CMS", "last_entries": []}]
        system, user = PA.build_synthesis_prompts(fiche, prec)
        assert "UNIQUEMENT sur les precedents" in system
        assert "24H AUTOS 2025" in user and "Evacue CMS" in user and "25 min" in user

    def test_synthesis_key_independante_de_l_ordre(self):
        assert PA.synthesis_key("f", ["b", "a"]) == PA.synthesis_key("f", ["a", "b"])
        assert PA.synthesis_key("f", ["a"]) != PA.synthesis_key("g", ["a"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class _Col(FakeCollection):
    def replace_one(self, filtre, doc, upsert=False):
        self.docs = [d for d in self.docs if d.get("_id") != filtre.get("_id")]
        self.docs.append(dict(doc))


class _Db(FakeDb):
    def __init__(self, **cols):
        self._cols = {k: _Col(v) for k, v in cols.items()}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _Col([])
        return self._cols[name]


def _fake_role_required(role):
    """Double de app.role_required : refuse sans en-tete X-Test-User."""
    from flask import request, jsonify

    def deco(f):
        def wrapper(*a, **kw):
            if not request.headers.get("X-Test-User"):
                return jsonify({"error": "auth"}), 401
            request.user_payload = {"email": "op@test", "app_role": "user"}
            return f(*a, **kw)
        return wrapper
    return deco


@pytest.fixture
def env(monkeypatch):
    from flask import Flask
    from flask_wtf.csrf import CSRFProtect

    PA.reset_cache()
    monkeypatch.setattr(PA, "_synth_index_ok", False)
    db = _Db(
        pcorg=[
            _doc("f1", "Malaise spectateur tribune nord", category="PCO.Secours"),
            _doc("p1", "Malaise spectateur tribune nord T34", category="PCO.Secours", year=2025,
                 status_code=10, ts=datetime(2025, 6, 13, 14, 0),
                 close_ts=datetime(2025, 6, 13, 14, 30)),
            _doc("tech", "Panne eclairage", category="PCO.Technique"),
        ],
        pcorg_config=[{"_id": "pcorg_lists",
                       "sous_classifications": {"PCO.Secours": [{"id": "1", "label": "Malaise"}]},
                       "urgence_categories": {"PCO.Secours": True}}],
    )
    monkeypatch.setattr(PA, "_db", lambda: db)
    monkeypatch.setattr(PA, "_resolve_role_required", lambda: _fake_role_required)
    monkeypatch.setattr(PA, "_allowed_categories", lambda p: ["PCO.Secours", "PCO.Flux"])
    monkeypatch.setattr(PA, "query_duplicate_candidates",
                        lambda col, e, y, a, now_utc=None: [d for d in col.docs if d["year"] == y])
    monkeypatch.setattr(PA, "fetch_precedent_candidates",
                        lambda col, doc, scope, allowed, limit=0: (
                            [d for d in col.docs if d["year"] != doc["year"]], "test"))
    calls = {"claude": 0, "usage": []}
    monkeypatch.setattr(PA.pcorg_summary, "record_ai_usage",
                        lambda db_, feature, model, usage, meta=None: calls["usage"].append(feature),
                        raising=False)

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="t", WTF_CSRF_ENABLED=False)
    CSRFProtect(app)
    app.register_blueprint(PA.pcorg_assist_bp)
    return {"app": app, "client": app.test_client(), "db": db, "calls": calls,
            "mp": monkeypatch}


H = {"X-Test-User": "1"}


def _fake_claude(env, text):
    def f(system, user, max_tokens, on_progress=None, model=None, system_cache=True, memory_block=None):
        env["calls"]["claude"] += 1
        env["calls"]["model"] = model
        env["calls"]["max_tokens"] = max_tokens
        return text, {"input_tokens": 10, "output_tokens": 5}, "end_turn"
    env["mp"].setattr(PA.pcorg_summary, "_claude_stream_request", f)
    env["mp"].setattr(PA.pcorg_summary, "ANTHROPIC_API_KEY", "k")


class TestRoutes:
    def test_auth_requise(self, env):
        c = env["client"]
        assert c.post("/api/pcorg/assist/suggest", json={}).status_code == 401
        assert c.get("/api/pcorg/assist/similar/f1").status_code == 401
        assert c.post("/api/pcorg/assist/similar/f1/synthesis", json={}).status_code == 401

    def test_csrf_actif_sur_post(self, env):
        env["app"].config["WTF_CSRF_ENABLED"] = True
        rep = env["client"].post("/api/pcorg/assist/suggest", json={"event": "x", "year": 1}, headers=H)
        assert rep.status_code == 400
        # GET non concerne
        assert env["client"].get("/api/pcorg/assist/similar/f1", headers=H).status_code == 200

    def test_suggest_sans_cle_rend_les_doublons(self, env):
        env["mp"].setattr(PA.pcorg_summary, "ANTHROPIC_API_KEY", "")
        rep = env["client"].post("/api/pcorg/assist/suggest", headers=H, json={
            "text": "Malaise d'un spectateur en tribune nord pres du bar", "event": "24H AUTOS", "year": 2026})
        d = rep.get_json()
        assert rep.status_code == 200 and d["ok"] is True
        assert d["ai"] == "unavailable" and d["suggestions"] is None
        assert [x["id"] for x in d["possible_duplicates"]] == ["f1"]

    def test_suggest_valide_la_reponse_du_modele(self, env):
        _fake_claude(env, '{"category": "PCO.Technique", "sous_classification": "Malaise", '
                          '"niveau_urgence": "UA", "confidence": 0.8, "reason": "x"}')
        body = {"text": "Malaise d'un spectateur en tribune nord pres du bar",
                "event": "24H AUTOS", "year": 2026, "category": "PCO.Secours"}
        d = env["client"].post("/api/pcorg/assist/suggest", headers=H, json=body).get_json()
        s = d["suggestions"]
        assert s["category"] is None          # Technique : non autorisee pour l'utilisateur
        assert s["sous_classification"] == "Malaise"
        assert s["niveau_urgence"] == "UA"
        assert env["calls"]["model"] == "claude-haiku-4-5"
        assert env["calls"]["max_tokens"] == PA.SUGGEST_MAX_TOKENS
        assert env["calls"]["usage"] == ["pcorg_assist_suggest"]
        # Meme texte : servi par le cache, pas de second appel
        d2 = env["client"].post("/api/pcorg/assist/suggest", headers=H, json=body).get_json()
        assert d2["ai"] == "cached" and env["calls"]["claude"] == 1

    def test_suggest_texte_court_sans_appel(self, env):
        _fake_claude(env, "{}")
        d = env["client"].post("/api/pcorg/assist/suggest", headers=H,
                               json={"text": "Malaise", "event": "E", "year": 2026}).get_json()
        assert d["ai"] == "skipped" and env["calls"]["claude"] == 0

    def test_suggest_event_requis(self, env):
        rep = env["client"].post("/api/pcorg/assist/suggest", headers=H, json={"text": "x"})
        assert rep.status_code == 400 and rep.get_json() == {"ok": False, "error": "event_year_requis"}

    def test_similar(self, env):
        d = env["client"].get("/api/pcorg/assist/similar/f1?scope=editions", headers=H).get_json()
        assert d["ok"] is True
        assert [p["id"] for p in d["precedents"]] == ["p1"]
        assert d["precedents"][0]["edition"] == "24H AUTOS 2025"
        assert d["precedents"][0]["duration_min"] == 30

    def test_similar_erreurs_json(self, env):
        c = env["client"]
        r = c.get("/api/pcorg/assist/similar/inconnu", headers=H)
        assert r.status_code == 404 and r.get_json()["error"] == "introuvable"
        r = c.get("/api/pcorg/assist/similar/tech", headers=H)
        assert r.status_code == 403 and r.get_json()["error"] == "categorie_non_autorisee"
        r = c.get("/api/pcorg/assist/similar/f1?scope=tout", headers=H)
        assert r.status_code == 400

    def test_synthese_et_cache(self, env):
        _fake_claude(env, '{"points": ["CMS engage, evacuation en 30 min (24H AUTOS 2025)"], "fiabilite": "moyenne"}')
        c = env["client"]
        d = c.post("/api/pcorg/assist/similar/f1/synthesis", headers=H,
                   json={"precedent_ids": ["p1"]}).get_json()
        assert d["ok"] is True and d["cached"] is False
        assert d["points"] == ["CMS engage, evacuation en 30 min (24H AUTOS 2025)"]
        assert env["calls"]["usage"] == ["pcorg_precedents"]
        d2 = c.post("/api/pcorg/assist/similar/f1/synthesis", headers=H,
                    json={"precedent_ids": ["p1"]}).get_json()
        assert d2["cached"] is True and env["calls"]["claude"] == 1
        saved = env["db"][PA.COL_SYNTHESES].docs[0]
        assert saved["fiche_id"] == "f1" and saved["precedent_ids"] == ["p1"]
        ttl = [kw for keys, kw in env["db"][PA.COL_SYNTHESES].indexes if keys == "created_at"]
        assert ttl and ttl[0]["expireAfterSeconds"] == 30 * 86400

    def test_synthese_ids_etrangers_ignores(self, env):
        _fake_claude(env, '{"points": ["x"]}')
        r = env["client"].post("/api/pcorg/assist/similar/f1/synthesis", headers=H,
                               json={"precedent_ids": ["tech"]})
        assert r.status_code == 404 and r.get_json()["error"] == "aucun_precedent"

    def test_synthese_passe_budget_et_effort_si_acceptes(self, env):
        vu = {}

        def f(system, user, max_tokens, on_progress=None, model=None, system_cache=True,
              memory_block=None, db=None, output_schema=None, effort=None, on_thinking=None):
            vu.update(db=db, effort=effort, max_tokens=max_tokens, model=model)
            return '{"points": ["x (24H AUTOS 2025)"]}', {}, "end_turn"
        env["mp"].setattr(PA.pcorg_summary, "_claude_stream_request", f)
        env["mp"].setattr(PA.pcorg_summary, "ANTHROPIC_API_KEY", "k")
        r = env["client"].post("/api/pcorg/assist/similar/f1/synthesis", headers=H, json={})
        assert r.status_code == 200
        assert vu["db"] is env["db"] and vu["effort"] == "low"
        assert vu["model"] == PA.pcorg_summary.CLAUDE_MODEL

    def test_synthese_budget_depasse(self, env):
        def f(system, user, max_tokens, model=None, system_cache=True, db=None):
            raise PA.pcorg_summary.ClaudeError("budget_exceeded")
        env["mp"].setattr(PA.pcorg_summary, "_claude_stream_request", f)
        env["mp"].setattr(PA.pcorg_summary, "ANTHROPIC_API_KEY", "k")
        r = env["client"].post("/api/pcorg/assist/similar/f1/synthesis", headers=H, json={})
        assert r.status_code == 429 and r.get_json()["error"] == "budget_ia_depasse"

    def test_synthese_sans_cle(self, env):
        env["mp"].setattr(PA.pcorg_summary, "ANTHROPIC_API_KEY", "")
        r = env["client"].post("/api/pcorg/assist/similar/f1/synthesis", headers=H, json={})
        assert r.status_code == 503 and r.get_json()["error"] == "cle_api_absente"
