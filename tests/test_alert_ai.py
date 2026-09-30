"""Tests de l'explication d'alerte (alert_ai.py).

Aucun appel reseau : le modele est remplace par un faux `caller`, la base
par un double Mongo local.
"""

import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
from bson.objectid import ObjectId

os.environ.setdefault("TITAN_ENV", "dev")
import alert_ai as AI  # noqa: E402


# ---------------------------------------------------------------------------
# Double Mongo (operateurs reellement utilises ; un inconnu leve)
# ---------------------------------------------------------------------------

def _get(doc, dotted):
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def _cmp(v):
    if isinstance(v, datetime) and v.tzinfo is not None:
        return v.astimezone(timezone.utc).replace(tzinfo=None)
    return v


def _match(doc, query):
    for key, cond in (query or {}).items():
        if key == "$or":
            if not any(_match(doc, q) for q in cond):
                return False
            continue
        val, present = _get(doc, key)
        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                if op == "$exists":
                    if bool(arg) != present:
                        return False
                elif op in ("$gte", "$gt", "$lte", "$lt"):
                    if val is None:
                        return False
                    a, b = _cmp(val), _cmp(arg)
                    if (op == "$gte" and not a >= b) or (op == "$gt" and not a > b) \
                            or (op == "$lte" and not a <= b) or (op == "$lt" and not a < b):
                        return False
                elif op == "$in":
                    if val not in arg:
                        return False
                elif op == "$ne":
                    if val == arg:
                        return False
                else:
                    raise NotImplementedError(op)
        elif _cmp(val) != _cmp(cond):
            return False
    return True


class Cursor(list):
    def sort(self, key, direction=1):
        super().sort(key=lambda d: _cmp(_get(d, key)[0]), reverse=direction == -1)
        return self

    def limit(self, n):
        return Cursor(self[:n])


class Res:
    def __init__(self, matched):
        self.matched_count = matched
        self.modified_count = matched


class DupKey(Exception):
    pass


class Coll:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, query=None, projection=None):
        return Cursor([d for d in self.docs if _match(d, query)])

    def find_one(self, query=None, projection=None, sort=None):
        r = self.find(query)
        return r[0] if r else None

    def count_documents(self, query=None):
        return len(self.find(query))

    def insert_one(self, doc):
        if "_id" in doc and any(d.get("_id") == doc["_id"] for d in self.docs):
            raise DupKey("E11000")
        self.docs.append(dict(doc))

    def update_one(self, flt, update, upsert=False):
        d = self.find_one(flt)
        if d is None:
            if upsert:
                d = dict(flt)
                self.docs.append(d)
            else:
                return Res(0)
        d.update(update.get("$set", {}))
        return Res(1)

    def delete_one(self, flt):
        d = self.find_one(flt)
        if d is not None:
            self.docs.remove(d)

    def create_index(self, *a, **k):
        return None


class DB:
    def __init__(self, **cols):
        self.cols = {k: Coll(v) for k, v in cols.items()}

    def __getitem__(self, name):
        return self.cols.setdefault(name, Coll())


NOW = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)   # 14:00 Paris
AID = ObjectId()
HID = ObjectId()

GOOD = json.dumps({
    "contexte": "Bouchon sur la D338.", "cause_probable": "Accident probable.",
    "evolution": "stable", "action_suggeree": "Informer le PC trafic.",
    "confiance": "moyenne, 2 sources"})


def _caller_factory(raw=GOOD):
    calls = []

    def caller(system, user, **kw):
        calls.append((system, user))
        return raw, {"input_tokens": 100, "output_tokens": 50}, "claude-test"
    return caller, calls


def _db_with_active(ad=None, slug="traffic-cluster", **extra):
    return DB(
        cockpit_active_alerts=[{"_id": AID, "definition_slug": slug, "title": "ALERTE TRAFIC",
                                "message": "Zone critique", "timeStr": "13:55",
                                "actionData": ad or {}, "triggeredAt": NOW.replace(tzinfo=None) - timedelta(minutes=5)}],
        cockpit_alert_definitions=[{"slug": slug, "name": "Zone critique trafic",
                                    "detection_type": "traffic_cluster" if slug == "traffic-cluster" else "",
                                    "params": {"radius_m": 500}}],
        **extra)


@pytest.fixture(autouse=True)
def _no_usage_module(monkeypatch):
    fake = types.SimpleNamespace(ANTHROPIC_API_KEY="k", CLAUDE_MODEL="claude-test", usage=[])
    fake.record_ai_usage = lambda db, feature, model, usage, meta=None: fake.usage.append(
        (feature, model, usage, meta))
    monkeypatch.setattr(AI, "_pcorg_summary", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Chargement
# ---------------------------------------------------------------------------

class TestLoadAlert:
    def test_active(self):
        a = AI.load_alert(_db_with_active(), str(AID))
        assert a["source"] == "active" and a["slug"] == "traffic-cluster"
        assert a["triggeredAt"].tzinfo is not None

    def test_historique_par_alert_id_puis_par_id(self):
        db = DB(cockpit_alert_history=[
            {"_id": HID, "alert_id": str(AID), "type": "meteo-vent", "title": "ALERTE VENT",
             "message": "m", "createdAt": NOW.replace(tzinfo=None)}])
        a = AI.load_alert(db, str(AID))
        assert a["source"] == "historique" and a["key"] == str(AID)
        b = AI.load_alert(db, str(HID))
        assert b["key"] == str(AID)   # meme cle de cache par les deux chemins

    def test_archive(self):
        db = DB(cockpit_active_alerts_archive=[{"_id": AID, "definition_slug": "x"}])
        assert AI.load_alert(db, str(AID))["source"] == "archive"

    def test_id_invalide(self):
        assert AI.load_alert(DB(), "../../etc") is None
        assert AI.load_alert(DB(), str(ObjectId())) is None


class TestKind:
    @pytest.mark.parametrize("slug,dt,ad,kind", [
        ("field_sos", "", {}, "field_sos"),
        ("cam-1", "camera_event", {"event_type": "x", "camera_path": "/c"}, "camera"),
        ("meteo-vent", "meteo_threshold", {}, "meteo"),
        ("meteo-pluie-imminente", "meteo_rain_onset", {}, "meteo"),
        ("x", "pcorg_urgency", {}, "pcorg_urgency"),
        ("x", "checkpoint_error_burst", {}, "checkpoint_error_burst"),
        ("x", "door_saturation_forecast", {}, "door_saturation_forecast"),
        ("traffic-cluster", "", {"pins": [1]}, "traffic_cluster"),
        ("alfred-mot", "", {}, "generique"),
    ])
    def test_mapping(self, slug, dt, ad, kind):
        assert AI.detection_kind({"slug": slug, "actionData": ad}, {"detection_type": dt}) == kind


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestParse:
    def test_json_propre(self):
        s = AI.parse_explanation(GOOD)
        assert list(s) == list(AI.EXPLAIN_KEYS)

    def test_bloc_markdown_et_cle_manquante(self):
        s = AI.parse_explanation('```json\n{"contexte": "a", "evolution": ["x", "y"]}\n```')
        assert s["contexte"] == "a"
        assert s["evolution"] == "- x\n- y"
        assert s["cause_probable"] == "donnee insuffisante"

    def test_json_tronque_recupere_par_regex(self):
        s = AI.parse_explanation('{"contexte": "a", "cause_probable": "b", "evolution": "c')
        assert s["contexte"] == "a" and s["cause_probable"] == "b"

    def test_charabia(self):
        assert AI.parse_explanation("desole") is None
        assert AI.parse_explanation("") is None


# ---------------------------------------------------------------------------
# Cache, rafraichissement, erreurs
# ---------------------------------------------------------------------------

class TestExplainCache:
    def test_un_seul_appel_pour_plusieurs_operateurs(self, _no_usage_module):
        db = _db_with_active()
        caller, calls = _caller_factory()
        s1, b1 = AI.explain_alert(db, str(AID), user={"email": "a@x"}, now=NOW, caller=caller)
        s2, b2 = AI.explain_alert(db, str(AID), user={"email": "b@x"}, now=NOW + timedelta(seconds=30), caller=caller)
        assert (s1, s2) == (200, 200)
        assert len(calls) == 1
        assert b1["cached"] is False and b2["cached"] is True
        assert b2["sections"]["contexte"] == "Bouchon sur la D338."
        assert _no_usage_module.usage[0][0] == "alert_explain"
        assert _no_usage_module.usage[0][3]["alert_id"] == str(AID)

    def test_refresh_refuse_avant_2_min(self):
        db = _db_with_active()
        caller, calls = _caller_factory()
        AI.explain_alert(db, str(AID), now=NOW, caller=caller)
        s, b = AI.explain_alert(db, str(AID), refresh=True, now=NOW + timedelta(seconds=60), caller=caller)
        assert s == 200 and b["refresh_refused"] is True and len(calls) == 1
        assert 0 < b["retry_after_s"] <= 60

    def test_refresh_apres_2_min(self):
        db = _db_with_active()
        caller, calls = _caller_factory()
        AI.explain_alert(db, str(AID), now=NOW, caller=caller)
        s, b = AI.explain_alert(db, str(AID), refresh=True, now=NOW + timedelta(minutes=3), caller=caller)
        assert s == 200 and len(calls) == 2 and b["cached"] is False

    def test_calcul_en_cours_202(self):
        db = _db_with_active()
        db["alert_explanations"].docs.append({"_id": str(AID), "status": "pending",
                                              "started_at": NOW.replace(tzinfo=None) - timedelta(seconds=10)})
        caller, calls = _caller_factory()
        s, b = AI.explain_alert(db, str(AID), now=NOW, caller=caller)
        assert s == 202 and b["error"] == "en_cours" and not calls

    def test_calcul_bloque_repris(self):
        db = _db_with_active()
        db["alert_explanations"].docs.append({"_id": str(AID), "status": "pending",
                                              "started_at": NOW.replace(tzinfo=None) - timedelta(minutes=5)})
        caller, calls = _caller_factory()
        s, _ = AI.explain_alert(db, str(AID), now=NOW, caller=caller)
        assert s == 200 and len(calls) == 1

    def test_erreur_modele_libere_la_reservation(self):
        db = _db_with_active()

        def boom(system, user):
            raise RuntimeError("claude_http_529")
        s, b = AI.explain_alert(db, str(AID), now=NOW, caller=boom)
        assert s == 502 and b["error"] == "claude_http_529"
        assert db["alert_explanations"].find_one({"_id": str(AID)}) is None

    def test_reponse_illisible(self):
        db = _db_with_active()
        caller, _ = _caller_factory("pas du json")
        s, b = AI.explain_alert(db, str(AID), now=NOW, caller=caller)
        assert s == 502 and b["error"] == "reponse_illisible"
        assert db["alert_explanations"].find_one({"_id": str(AID)}) is None

    def test_sans_cle_api(self, monkeypatch):
        monkeypatch.setattr(AI, "api_key_configured", lambda: False)
        s, b = AI.explain_alert(_db_with_active(), str(AID), now=NOW)
        assert s == 503 and b["error"] == "cle_api_absente"

    def test_slug_non_autorise(self):
        caller, calls = _caller_factory()
        s, b = AI.explain_alert(_db_with_active(), str(AID), now=NOW, caller=caller, allowed_slugs=["autre"])
        assert s == 404 and not calls

    def test_introuvable(self):
        caller, calls = _caller_factory()
        s, b = AI.explain_alert(DB(), str(ObjectId()), now=NOW, caller=caller)
        assert s == 404 and b == {"ok": False, "error": "alerte_introuvable"}


# ---------------------------------------------------------------------------
# Route (faux module `app` : role_required, db, _get_user_alert_slugs)
# ---------------------------------------------------------------------------

@pytest.fixture
def route_client(monkeypatch):
    from flask import Flask, request as flask_request
    db = _db_with_active()
    calls = []

    def role_required(role):
        def deco(f):
            def inner(*a, **k):
                calls.append(role)
                flask_request.user_payload = {"email": "op@x", "firstname": "Op", "lastname": "PC"}
                return f(*a, **k)
            return inner
        return deco
    fake_app = types.SimpleNamespace(db=db, role_required=role_required,
                                     _get_user_alert_slugs=lambda p: None)
    monkeypatch.setitem(sys.modules, "app", fake_app)
    caller, model_calls = _caller_factory()
    monkeypatch.setattr(AI, "call_model", caller)
    flask_app = Flask(__name__)
    flask_app.register_blueprint(AI.alert_ai_bp)
    return flask_app.test_client(), db, calls, model_calls


class TestCallModel:
    def test_petit_budget_effort_bas_et_garde_budget(self, _no_usage_module):
        seen = {}

        def req(system, user, max_tokens, on_progress=None, model=None, db=None, effort=None):
            seen.update(max_tokens=max_tokens, model=model, db=db, effort=effort)
            return GOOD, {"output_tokens": 10}, "end_turn"
        _no_usage_module._claude_stream_request = req
        raw, usage, model = AI.call_model("s", "u", db="DB")
        assert seen == {"max_tokens": 700, "model": "claude-test", "db": "DB", "effort": "low"}

    def test_signature_ancienne_sans_effort_ni_db(self, _no_usage_module):
        def req(system, user, max_tokens, on_progress=None, model=None):
            return GOOD, {}, "end_turn"
        _no_usage_module._claude_stream_request = req
        assert AI.call_model("s", "u", db="DB")[0] == GOOD


class TestRoute:
    def test_post_explique_et_met_en_cache(self, route_client):
        client, db, roles, model_calls = route_client
        r = client.post("/api/alerts/%s/explain" % AID)
        assert r.status_code == 200 and r.get_json()["sections"]["evolution"] == "stable"
        assert roles == ["user"]
        r2 = client.post("/api/alerts/%s/explain?refresh=1" % AID)
        assert r2.get_json()["refresh_refused"] is True
        assert len(model_calls) == 1
        assert db["alert_explanations"].find_one({"_id": str(AID)})["created_by"] == "op@x"

    def test_get_refuse(self, route_client):
        client = route_client[0]
        assert client.get("/api/alerts/%s/explain" % AID).status_code == 405

    def test_id_inconnu_json_404(self, route_client):
        client = route_client[0]
        r = client.post("/api/alerts/%s/explain" % ObjectId())
        assert r.status_code == 404 and r.get_json() == {"ok": False, "error": "alerte_introuvable"}


# ---------------------------------------------------------------------------
# Contextes par type
# ---------------------------------------------------------------------------

def _ctx(db, alert_slug=None):
    alert = AI.load_alert(db, str(AID))
    definition = AI.load_definition(db, alert["slug"])
    return AI.build_explain_context(db, alert, definition, NOW)


class TestContextes:
    def test_trafic_alertes_proches_et_evolution(self):
        pins = [{"lat": 47.96, "lon": 0.224, "type": "JAM", "street": "D338"}]
        near = {"type": "ACCIDENT", "street": "D338", "location": {"y": 47.9602, "x": 0.2242},
                "pubMillis": NOW.timestamp() * 1000 - 600000}
        far = {"type": "JAM", "location": {"y": 48.2, "x": 0.5}}
        hist = [{"fetched_at": NOW.replace(tzinfo=None) - timedelta(minutes=m),
                 "data": [near] * n} for m, n in ((40, 1), (20, 2))]
        db = _db_with_active({"pins": pins},
                             waze_alerts=[{"_id": "latest", "data": [near, far],
                                           "fetched_at": NOW.replace(tzinfo=None)}],
                             waze_alerts_history=hist)
        kind, ctx = _ctx(db)
        assert kind == "traffic_cluster"
        c = ctx["contexte"]
        assert len(c["alertes_waze_actuelles"]) == 1
        assert c["alertes_waze_actuelles"][0]["age_min"] == 10
        assert [e["comptes"].get("ACCIDENT") for e in c["evolution_releves"]] == [1, 2]
        assert ctx["alerte"]["declenchee_a"] == "13/06 13:55"   # heure de Paris

    def test_main_courante(self):
        t = NOW.replace(tzinfo=None) - timedelta(minutes=10)
        fiche = {"_id": "f1", "category": "PCO.Secours", "niveau_urgence": "UA", "text": "Malaise",
                 "area": {"desc": "Tribune 12"}, "status_code": 0, "ts": t,
                 "comment_history": [{"ts": t, "operator": "PC", "text": "Engagement VPSP"}],
                 "content_category": {"victimes": 1, "nested": {"x": 1}}}
        voisines = [{"_id": "f2", "category": "PCO.Securite", "area": {"desc": "Tribune 12"},
                     "ts": t - timedelta(minutes=20), "text": "Rixe"},
                    {"_id": "f3", "category": "PCO.Flux", "area": {"desc": "Ailleurs"},
                     "ts": t - timedelta(minutes=20), "text": "hors perimetre"},
                    {"_id": "f4", "category": "PCO.Secours", "area": {"desc": "X"},
                     "ts": t - timedelta(hours=3), "text": "trop vieux"}]
        db = _db_with_active({"pcorg_id": "f1"}, slug="pcorg-secours-ua", pcorg=[fiche] + voisines)
        db["cockpit_alert_definitions"].docs[0]["detection_type"] = "pcorg_urgency"
        kind, ctx = _ctx(db)
        assert kind == "pcorg_urgency"
        c = ctx["contexte"]
        assert c["fiche"]["zone"] == "Tribune 12" and c["fiche"]["statut"] == "ouverte"
        assert c["fiche"]["details"] == {"victimes": 1}
        assert c["chronologie"][0]["texte"] == "Engagement VPSP"
        assert [v["texte"] for v in c["fiches_meme_zone_ou_categorie_60min"]] == ["Rixe"]

    def test_sos_tablettes_les_plus_proches(self):
        dev = ObjectId()
        fresh = NOW.replace(tzinfo=None) - timedelta(minutes=2)
        devices = [
            {"_id": dev, "name": "T1", "event": "24H", "last_position": {"lat": 47.95, "lng": 0.21, "ts": fresh, "battery": 40}},
            {"_id": ObjectId(), "name": "PROCHE", "event": "24H", "last_position": {"lat": 47.9505, "lng": 0.21, "ts": fresh}},
            {"_id": ObjectId(), "name": "LOIN", "event": "24H", "last_position": {"lat": 47.96, "lng": 0.21, "ts": fresh}},
            {"_id": ObjectId(), "name": "PERIME", "event": "24H", "last_position": {"lat": 47.9501, "lng": 0.21,
                                                                                     "ts": fresh - timedelta(hours=2)}},
        ]
        db = _db_with_active({"device_id": str(dev), "device_name": "T1", "lat": 47.95, "lng": 0.21,
                              "battery": 41}, slug="field_sos", field_devices=devices)
        kind, ctx = _ctx(db)
        assert kind == "field_sos"
        noms = [v["tablette"] for v in ctx["contexte"]["tablettes_les_plus_proches"]]
        assert noms == ["PROCHE", "LOIN"]
        assert ctx["contexte"]["tablette_maintenant"]["batterie"] == 40

    def test_checkpoint_rafale(self):
        base = "2026-06-13 13:5"
        errs = [{"checkpoint": {"Name": "PDA.1"}, "gate": {"Name": "PORTE SUD"}, "status_label": s,
                 "direction": "Entree", "utid": u, "date_paris": base + str(i)}
                for i, (s, u) in enumerate([("Pas de WhitelistRecord", "https://waze.com"),
                                            ("Pas de WhitelistRecord", "ACO_2026_X"),
                                            ("Deja entre", "6000123456789012")])]
        db = _db_with_active({"checkpoint": "PDA.1", "gate": "PORTE SUD"}, slug="cp-burst", hsh_erreurs=errs)
        db["cockpit_alert_definitions"].docs[0]["detection_type"] = "checkpoint_error_burst"
        kind, ctx = _ctx(db)
        e = ctx["contexte"]["erreurs_checkpoint_20min"]
        assert e["total"] == 3
        assert e["par_statut"]["Pas de WhitelistRecord"] == 2
        assert e["format_titre"] == {"QR/URL parasite": 1, "logique ACO": 1, "RFID 16 chiffres": 1}
        assert ctx["contexte"]["erreurs_porte_20min"]["total"] == 3

    def test_meteo(self, monkeypatch):
        fake = types.SimpleNamespace(etat_mur=lambda db, now: {
            "actuel": {"t": 20}, "prochaines": list(range(10)), "consignes": [], "contraintes": [],
            "verdict": "calme", "vigilance": None, "prochaine_pluie": None, "maintenant": now})
        monkeypatch.setitem(sys.modules, "meteo_etat", fake)
        db = _db_with_active({}, slug="meteo-vent")
        db["cockpit_alert_definitions"].docs[0]["detection_type"] = "meteo_threshold"
        kind, ctx = _ctx(db)
        assert kind == "meteo"
        assert ctx["contexte"]["prochaines_heures"] == [0, 1, 2, 3, 4, 5]
        assert "avertissement" not in ctx["contexte"]

    def test_saturation_porte(self):
        # tranche = heure de Paris naive : 13:50 Paris = 11:50 UTC
        tx = [{"gate_name": "PORTE EST", "tranche": datetime(2026, 6, 13, 13, 50), "entrees": 120, "erreurs": 4},
              {"gate_name": "PORTE EST", "tranche": datetime(2026, 6, 13, 13, 50), "entrees": 30, "erreurs": 0},
              {"gate_name": "PORTE SUD", "tranche": datetime(2026, 6, 13, 13, 50), "entrees": 999, "erreurs": 0}]
        db = _db_with_active({"door": "PORTE EST", "current_rate": 1440, "predicted_rate": 1700,
                              "capacity": 1800, "method": "profil_n1"}, slug="door-sat",
                             hsh_transactions_agg=tx)
        db["cockpit_alert_definitions"].docs[0]["detection_type"] = "door_saturation_forecast"
        kind, ctx = _ctx(db)
        assert kind == "door_saturation_forecast"
        assert ctx["contexte"]["prevision"]["capacity"] == 1800
        assert ctx["contexte"]["entrees_par_5min"] == [{"tranche": "13:50", "entrees": 150, "erreurs": 4}]

    def test_generique(self):
        db = _db_with_active({"mot": "incendie"}, slug="alfred-x")
        kind, ctx = _ctx(db)
        assert kind == "generique"
        assert ctx["contexte"]["donnees_alerte"] == {"mot": "incendie"}

    def test_contexte_en_echec_ne_bloque_pas(self, monkeypatch):
        def boom(*a):
            raise RuntimeError("x")
        monkeypatch.setitem(AI.CONTEXT_BUILDERS, "generique", boom)
        db = _db_with_active({"mot": "a"}, slug="alfred-x")
        kind, ctx = _ctx(db)
        assert ctx["contexte"]["erreur"] == "contexte indisponible"

    def test_prompt_borne(self):
        ctx = {"contexte": {"x": "a" * 600, "l": ["b" * 500] * 40}}
        system, user = AI.build_prompts(AI._jsonable(ctx))
        assert len(user) < AI.MAX_CONTEXT_CHARS + 200
        assert "donnee insuffisante" in system
