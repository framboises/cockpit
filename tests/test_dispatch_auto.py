"""Moteur de dispatch (dispatch_auto.py) contre un MongoDB local jetable.

Les transitions reposent sur des mises a jour atomiques conditionnelles et
sur les pipelines d'update de pcorg_history : une base simulee ne prouverait
rien. Base `titan_test_dispatch_<pid>`, supprimee en fin de test ; test
saute si aucun MongoDB n'ecoute sur localhost.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import dispatch_auto as DA
import field

# Heure reelle (et non une date figee) : les tests de routes passent par le
# vrai `now` du serveur, et une date figee finissait par rendre les tablettes
# de test "silencieuses depuis plus de 15 min", donc jamais proposees.
NOW = datetime.now(timezone.utc).replace(microsecond=0)
FICHE_LAT, FICHE_LNG = 47.95, 0.21


@pytest.fixture
def db(monkeypatch):
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except PyMongoError:
        pytest.skip("MongoDB local indisponible")
    name = "titan_test_dispatch_%d" % os.getpid()
    client.drop_database(name)
    d = client[name]
    monkeypatch.setattr(field, "send_push_to_device", lambda *a, **k: None)
    d["anoloc_config"].insert_one({"_id": "global", "beacon_groups": []})
    yield d
    client.drop_database(name)


def _dev(db, name, meters_north=0, category="PCO.Technique", metiers=None,
         status="patrouille", pos_age_s=30, seen_age_s=10, event="E", year="2026"):
    lat = FICHE_LAT + meters_north / 111_000.0
    doc = {
        "_id": ObjectId(), "name": name, "event": event, "year": year, "revoked": False,
        "category": category, "metiers": metiers or [], "status": status,
        "last_seen": NOW - timedelta(seconds=seen_age_s),
        "last_position": {"lat": lat, "lng": FICHE_LNG, "ts": NOW - timedelta(seconds=pos_age_s)},
    }
    db["field_devices"].insert_one(doc)
    return doc


def _fiche(db, fid="f1", urgence="UA", metier="Electricite", category="PCO.Technique",
           event="E", year=2026, ts=None):
    db["pcorg"].insert_one({
        "_id": fid, "event": event, "year": year, "category": category,
        "ts": ts or NOW,
        "text": "panne", "niveau_urgence": urgence, "status_code": 0,
        "gps": {"type": "Point", "coordinates": [FICHE_LNG, FICHE_LAT]},
        "content_category": {"sous_classification": metier},
        "comment": "", "comment_history": [],
    })
    return fid


def _set_cfg(db, **tech):
    DA.save_config(db, {"categories": {"PCO.Technique": tech}}, "test")


def _state(db, fid="f1"):
    return (db["pcorg"].find_one({"_id": fid}) or {}).get("dispatch") or {}


def _texts(db, fid="f1"):
    return [e["text"] for e in db["pcorg"].find_one({"_id": fid})["comment_history"]]


class TestConfig:
    def test_defauts(self, db):
        cfg = DA.get_config(db)["categories"]
        assert cfg["PCO.Technique"]["mode"] == "urgency"
        assert cfg["PCO.Technique"]["levels"] == ["UA", "EU"]
        assert cfg["PCO.Technique"]["self_close"] is True
        assert cfg["PCO.Securite"]["mode"] == "always"
        assert cfg["PCO.Secours"]["mode"] == "never"

    def test_declenchement(self, db):
        cfg = DA.get_config(db)
        f = {"category": "PCO.Technique", "niveau_urgence": "UA", "status_code": 0, "content_category": {}}
        assert DA.wants_auto(cfg, f)
        assert not DA.wants_auto(cfg, dict(f, niveau_urgence="UR"))
        assert DA.wants_auto(cfg, dict(f, category="PCO.Securite", niveau_urgence=None))
        assert not DA.wants_auto(cfg, dict(f, category="PCO.Secours", niveau_urgence="EU"))
        assert not DA.wants_auto(cfg, dict(f, content_category={"patrouille": "X"}))

    def test_nettoyage(self, db):
        cfg = DA.save_config(db, {"categories": {"PCO.Technique": {
            "mode": "n_importe", "levels": ["EU", "ZZ"], "timeout_s": 1,
            "managers": ["A@B.fr", "a@b.fr", "pas-un-mail"]}}}, "t")["categories"]["PCO.Technique"]
        assert cfg["mode"] == "urgency"          # valeur invalide ignoree
        assert cfg["levels"] == ["EU"]
        assert cfg["timeout_s"] == 10            # borne basse
        # Les responsables viennent des groupes cockpit, plus de la config
        assert "managers" not in cfg


class TestCandidats:
    def test_plus_proche_disponible(self, db):
        _dev(db, "Loin", 800)
        near = _dev(db, "Pres", 100)
        _dev(db, "Occupe", 10, status="intervention")
        _dev(db, "Secu", 5, category="PCO.Securite")
        _dev(db, "Muet", 5, seen_age_s=3600)
        _fiche(db)
        names = [c["device"]["name"] for c in DA.find_candidates(db, db["pcorg"].find_one({"_id": "f1"}), now=NOW)]
        assert names == ["Pres", "Loin"]
        assert near["name"] == names[0]

    def test_metier_sans_accent(self, db):
        _dev(db, "Plombier", 10, metiers=["Sanitaire"])
        _dev(db, "Elec", 50, metiers=["Electricité"])
        _dev(db, "Polyvalent", 90)
        _fiche(db, metier="Electricite")
        names = [c["device"]["name"] for c in DA.find_candidates(db, db["pcorg"].find_one({"_id": "f1"}), now=NOW)]
        assert names == ["Elec", "Polyvalent"]

    def test_position_fraiche_avant_position_perimee(self, db):
        _dev(db, "PresMaisVieux", 20, pos_age_s=1200)
        _dev(db, "LoinFrais", 600, pos_age_s=20)
        _fiche(db)
        names = [c["device"]["name"] for c in DA.find_candidates(db, db["pcorg"].find_one({"_id": "f1"}), now=NOW)]
        assert names == ["LoinFrais", "PresMaisVieux"]


class TestTournee:
    def test_proposition_puis_acceptation(self, db):
        a = _dev(db, "A", 100)
        _fiche(db)
        assert DA.start(db, "f1", now=NOW) == (True, "ok")
        st = _state(db)
        assert st["state"] == "proposing" and st["current"]["device_name"] == "A"
        dev = db["field_devices"].find_one({"_id": a["_id"]})
        assert dev["pending_proposal"]["fiche_id"] == "f1"
        prop = DA.proposal_for_device(db, dev, now=NOW + timedelta(seconds=5))
        assert prop["fiche_id"] == "f1" and prop["remaining_s"] == 25

        assert DA.accept(db, "f1", dev, now=NOW + timedelta(seconds=5)) == (True, "ok")
        fiche = db["pcorg"].find_one({"_id": "f1"})
        assert fiche["content_category"]["patrouille"] == "A"
        assert fiche["dispatch"]["state"] == "assigned"
        assert fiche["intervention"]["engaged_at"]
        assert "content_category.patrouille" in fiche["cockpit_owned"]
        dev = db["field_devices"].find_one({"_id": a["_id"]})
        assert dev["status"] == "intervention" and dev["active_fiche_id"] == "f1"
        assert dev["pending_proposal"] is None
        assert any(t.startswith("Proposee a A") for t in _texts(db))

    def test_refus_puis_suivant_puis_file(self, db):
        _set_cfg(db, max_attempts=2)
        a = _dev(db, "A", 100)
        b = _dev(db, "B", 300)
        _dev(db, "C", 500)
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        assert DA.refuse(db, "f1", a, now=NOW + timedelta(seconds=3))
        assert _state(db)["current"]["device_name"] == "B"
        # A est liberee de sa proposition
        assert db["field_devices"].find_one({"_id": a["_id"]})["pending_proposal"] is None
        assert DA.refuse(db, "f1", b, now=NOW + timedelta(seconds=6))
        st = _state(db)
        assert st["state"] == "queued" and st["current"] is None
        assert "2 proposition(s)" in st["queue_reason"]
        assert [x["answer"] for x in st["attempts"]] == ["refused", "refused"]

    def test_sans_reponse_via_tick(self, db):
        _dev(db, "A", 100)
        _dev(db, "B", 300)
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        assert DA.tick(db, now=NOW + timedelta(seconds=31)) == 0   # delai de grace
        assert DA.tick(db, now=NOW + timedelta(seconds=40)) == 1
        st = _state(db)
        assert st["current"]["device_name"] == "B"
        assert st["attempts"][0]["answer"] == "timeout"
        assert any(t == "Sans reponse de A" for t in _texts(db))

    def test_acceptation_trop_tardive_ou_mauvaise_unite(self, db):
        a = _dev(db, "A", 100)
        b = _dev(db, "B", 300)
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        assert DA.accept(db, "f1", b, now=NOW) == (False, "proposition_expiree")
        assert DA.accept(db, "f1", a, now=NOW + timedelta(seconds=60)) == (False, "proposition_expiree")

    def test_aucune_unite(self, db):
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        st = _state(db)
        assert st["state"] == "queued" and st["queue_reason"] == "aucune unite disponible"

    def test_une_seule_proposition_par_unite(self, db):
        _dev(db, "A", 100)
        _fiche(db, "f1")
        _fiche(db, "f2")
        DA.start(db, "f1", now=NOW)
        DA.start(db, "f2", now=NOW)
        assert _state(db, "f1")["current"]["device_name"] == "A"
        assert _state(db, "f2")["state"] == "queued"

    def test_engagement_direct_annule_la_proposition(self, db):
        a = _dev(db, "A", 100)
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        DA.on_manual_assign(db, "f1", by="PC Org", now=NOW)
        assert _state(db)["state"] == "manual"
        assert db["field_devices"].find_one({"_id": a["_id"]})["pending_proposal"] is None

    def test_maybe_auto_start_respecte_la_config(self, db):
        _dev(db, "A", 100)
        _fiche(db, "f1", urgence="UR")
        assert not DA.maybe_auto_start(db, "f1")
        _fiche(db, "f2", urgence="EU")
        assert DA.maybe_auto_start(db, "f2")


class TestFileEtFin:
    def test_engagement_par_responsable_sans_ecraser(self, db):
        busy = _dev(db, "Occupe", 100, status="intervention")
        db["field_devices"].update_one({"_id": busy["_id"]}, {"$set": {"active_fiche_id": "autre"}})
        _fiche(db)
        dev = db["field_devices"].find_one({"_id": busy["_id"]})
        assert DA.manual_assign(db, "f1", dev, "Chef", now=NOW) == (True, "ajoutee_aux_missions")
        assert db["field_devices"].find_one({"_id": busy["_id"]})["active_fiche_id"] == "autre"
        fiche = db["pcorg"].find_one({"_id": "f1"})
        assert fiche["content_category"]["patrouille"] == "Occupe"
        assert fiche["dispatch"]["assigned_by"] == "Chef"

    def test_responsable_refuse_categorie_differente(self, db):
        secu = _dev(db, "Secu", 100, category="PCO.Securite")
        _fiche(db)
        assert DA.manual_assign(db, "f1", secu, "Chef") == (False, "categorie_differente")

    def _engaged(self, db):
        a = _dev(db, "A", 100)
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        DA.accept(db, "f1", db["field_devices"].find_one({"_id": a["_id"]}), now=NOW)
        return db["field_devices"].find_one({"_id": a["_id"]})

    def test_fin_resolue_clot_la_fiche(self, db):
        dev = self._engaged(db)
        assert DA.finish(db, "f1", dev, "resolu", "ok") == (False, "compte_rendu_obligatoire")   # 2 car. < 3
        assert DA.finish(db, "f1", dev, "resolu", "Disjoncteur rearme", now=NOW) == (True, "ok")
        fiche = db["pcorg"].find_one({"_id": "f1"})
        assert fiche["status_code"] == 10
        assert fiche["intervention"]["outcome"] == "resolu"
        assert fiche["dispatch"]["state"] == "done"
        dev = db["field_devices"].find_one({"_id": dev["_id"]})
        assert dev["status"] == "patrouille" and dev["active_fiche_id"] is None
        # Renvoi par la file hors ligne : sans effet, sans erreur
        assert DA.finish(db, "f1", dev, "resolu", "Disjoncteur rearme") == (True, "deja_termine")

    def test_fin_non_resolue_retour_en_file(self, db):
        dev = self._engaged(db)
        assert DA.finish(db, "f1", dev, "materiel", "Il faut un tableau neuf", now=NOW) == (True, "ok")
        fiche = db["pcorg"].find_one({"_id": "f1"})
        assert fiche["status_code"] == 0
        assert fiche["content_category"]["patrouille"] == ""
        assert fiche["dispatch"]["state"] == "queued"
        assert "materiel" in fiche["dispatch"]["queue_reason"]
        assert db["field_devices"].find_one({"_id": dev["_id"]})["status"] == "patrouille"

    def test_cloture_terrain_desactivee(self, db):
        _set_cfg(db, self_close=False)
        dev = self._engaged(db)
        assert DA.finish(db, "f1", dev, "resolu", "Disjoncteur rearme") == (False, "cloture_terrain_desactivee")

    def test_etapes_horodatees_une_seule_fois(self, db):
        _fiche(db)
        DA.mark_step(db, "f1", "arrived_at", now=NOW)
        DA.mark_step(db, "f1", "arrived_at", now=NOW + timedelta(minutes=5))
        at = db["pcorg"].find_one({"_id": "f1"})["intervention"]["arrived_at"]
        assert at.replace(tzinfo=timezone.utc) == NOW


class TestRoutesTablette:
    def test_bout_en_bout(self, db, monkeypatch):
        from flask import Flask
        monkeypatch.setattr(field, "_get_mongo_db", lambda: db)
        monkeypatch.setattr(field, "_sweep_event_if_ended", lambda *a: False)
        app = Flask(__name__)
        app.register_blueprint(field.field_bp)
        client = app.test_client()
        a = _dev(db, "A", 100)
        db["field_devices"].update_one({"_id": a["_id"]}, {"$set": {"token_hash": field._hash_token("tokA")}})
        client.set_cookie(field.FIELD_COOKIE_NAME, "tokA", path=field.FIELD_COOKIE_PATH)
        js = {"Accept": "application/json"}
        _fiche(db)
        DA.start(db, "f1")

        data = client.get("/field/my-fiches", headers=js).get_json()
        assert data["proposal"]["fiche_id"] == "f1"
        assert data["self_close"] is True
        assert data["open"] == []                 # pas encore affectee

        rep = client.post("/field/proposal/f1/accept", headers=js, json={})
        assert rep.status_code == 200
        data = client.get("/field/my-fiches", headers=js).get_json()
        assert data["proposal"] is None
        assert [f["id"] for f in data["open"]] == ["f1"]
        assert data["device_status"] == "intervention"

        rep = client.post("/field/my-fiches/f1/finish", headers=js, json={"outcome": "resolu", "report": ""})
        assert rep.status_code == 400 and rep.get_json()["error"] == "compte_rendu_obligatoire"
        rep = client.post("/field/my-fiches/f1/finish", headers=js,
                          json={"outcome": "resolu", "report": "Prise remplacee"})
        assert rep.status_code == 200
        assert db["pcorg"].find_one({"_id": "f1"})["status_code"] == 10

    def test_pause_vaut_refus(self, db, monkeypatch):
        from flask import Flask
        monkeypatch.setattr(field, "_get_mongo_db", lambda: db)
        monkeypatch.setattr(field, "_sweep_event_if_ended", lambda *a: False)
        app = Flask(__name__)
        app.register_blueprint(field.field_bp)
        client = app.test_client()
        a = _dev(db, "A", 100)
        _dev(db, "B", 300)
        db["field_devices"].update_one({"_id": a["_id"]}, {"$set": {"token_hash": field._hash_token("tokA")}})
        client.set_cookie(field.FIELD_COOKIE_NAME, "tokA", path=field.FIELD_COOKIE_PATH)
        _fiche(db)
        DA.start(db, "f1")
        rep = client.post("/field/status", headers={"Accept": "application/json"}, json={"status": "pause"})
        assert rep.status_code == 200
        assert _state(db)["current"]["device_name"] == "B"


def test_reengagement_libere_l_ancienne_unite(db):
    a = _dev(db, "A", 100)
    b = _dev(db, "B", 300)
    _fiche(db)
    DA.start(db, "f1", now=NOW)
    DA.accept(db, "f1", db["field_devices"].find_one({"_id": a["_id"]}), now=NOW)
    ok, _ = DA.manual_assign(db, "f1", db["field_devices"].find_one({"_id": b["_id"]}), "Chef", now=NOW)
    assert ok
    old = db["field_devices"].find_one({"_id": a["_id"]})
    assert old["status"] == "patrouille" and old["active_fiche_id"] is None
    assert db["field_devices"].find_one({"_id": b["_id"]})["active_fiche_id"] == "f1"


class TestLibreService:
    def test_disponibles_filtrees_par_metier_et_unite(self, db):
        elec = _dev(db, "Elec", 100, metiers=["Electricite"])
        _fiche(db, "f1", metier="Electricite")
        _fiche(db, "f2", metier="Sanitaire")
        _fiche(db, "f3", metier="Electricite")
        db["pcorg"].update_one({"_id": "f3"}, {"$set": {"content_category.patrouille": "Autre"}})
        ids = [m["id"] for m in DA.available_for_device(db, db["field_devices"].find_one({"_id": elec["_id"]}))]
        assert ids == ["f1"]

    def test_prise_en_charge_puis_deja_prise(self, db):
        a = _dev(db, "A", 100)
        b = _dev(db, "B", 200)
        _fiche(db)
        assert DA.self_assign(db, "f1", a, now=NOW) == (True, "ok")
        fiche = db["pcorg"].find_one({"_id": "f1"})
        assert fiche["content_category"]["patrouille"] == "A"
        assert fiche["dispatch"]["assigned_by"] == "libre-service"
        assert db["field_devices"].find_one({"_id": a["_id"]})["status"] == "intervention"
        assert DA.self_assign(db, "f1", b, now=NOW) == (False, "deja_prise")

    def test_unite_occupee_refusee(self, db):
        busy = _dev(db, "Occupe", 100, status="intervention")
        _fiche(db)
        assert DA.self_assign(db, "f1", busy) == (False, "unite_occupee")

    def test_annule_la_proposition_faite_a_une_autre(self, db):
        a = _dev(db, "A", 100)
        b = _dev(db, "B", 900)
        _fiche(db)
        DA.start(db, "f1", now=NOW)
        assert _state(db)["current"]["device_name"] == "A"
        assert DA.self_assign(db, "f1", b, now=NOW) == (True, "ok")
        assert db["field_devices"].find_one({"_id": a["_id"]})["pending_proposal"] is None
        assert db["pcorg"].find_one({"_id": "f1"})["content_category"]["patrouille"] == "B"

    def test_disponibles_plus_recentes_et_bornees_a_30_jours(self, db):
        dev = _dev(db, "A", 100)
        _fiche(db, "vieille", ts=NOW - timedelta(days=40))
        db["pcorg"].insert_many([{
            "_id": "r%03d" % i, "event": "E", "year": 2026, "category": "PCO.Technique",
            "niveau_urgence": "UA", "status_code": 0, "ts": NOW - timedelta(days=1, minutes=i),
            "content_category": {}, "comment_history": [],
        } for i in range(301)])
        _fiche(db, "neuve", ts=NOW - timedelta(minutes=5))
        ids = [m["id"] for m in DA.available_for_device(db, dev, limit=500, now=NOW)]
        # Avant : tri croissant + limit(300) -> les 300 plus anciennes, la
        # fiche neuve n'apparaissait jamais.
        assert ids[0] == "neuve"
        assert "vieille" not in ids
        assert len(ids) == 300


# ---------------------------------------------------------------------------
# SAISON et epreuves simultanees (event_courant + field.device_pairs)
# ---------------------------------------------------------------------------

import event_courant as EC  # noqa: E402


def _epreuve_active(db, event="E", year="2026"):
    db["parametrages"].insert_one({"event": event, "year": year, "data": {"globalHoraires": {
        "montage": {"start": (NOW - timedelta(days=2)).isoformat()},
        "demontage": {"end": (NOW + timedelta(days=3)).isoformat()},
    }}})
    EC.invalidate()


def _cands(db, fid="f1"):
    return [c["device"]["_id"] for c in DA.find_candidates(db, db["pcorg"].find_one({"_id": fid}), now=NOW)]


class TestSaisonEtEpreuves:
    def setup_method(self):
        EC.invalidate()

    def test_tablette_saison_candidate_sur_epreuve_active(self, db):
        _epreuve_active(db)
        sai = _dev(db, "Saison", 100, event="SAISON", year=str(EC.saison_year(NOW)))
        _fiche(db)
        assert _cands(db) == [sai["_id"]]
        assert DA.manual_assign(db, "f1", sai, "Chef", now=NOW)[0]

    def test_tablette_saison_ignoree_hors_fenetre_de_l_epreuve(self, db):
        sai = _dev(db, "Saison", 100, event="SAISON", year=str(EC.saison_year(NOW)))
        _fiche(db)                                   # E/2026 sans fenetre active
        assert _cands(db) == []
        assert DA.manual_assign(db, "f1", sai, "Chef", now=NOW) == (False, "unite_invalide")
        assert DA.self_assign(db, "f1", sai, now=NOW) == (False, "evenement_different")

    def test_tablette_saison_d_une_annee_passee_vaut_saison_courant(self, db):
        yr = EC.saison_year(NOW)
        old = _dev(db, "Vieille", 100, event="SAISON", year=str(yr - 1))
        assert field.device_home_pair(old, NOW) == (EC.SAISON, yr)
        _fiche(db, event=EC.SAISON, year=yr)
        assert _cands(db) == [old["_id"]]
        assert [m["id"] for m in DA.available_for_device(db, old, now=NOW)] == ["f1"]
        assert DA.self_assign(db, "f1", old, now=NOW) == (True, "ok")

    def test_tablette_saison_jamais_revoquee_automatiquement(self, db):
        assert field._sweep_event_if_ended(db, "SAISON", "2020") is False

    def test_homonyme_appairee_sur_l_epreuve_prioritaire(self, db):
        _epreuve_active(db)
        epr = _dev(db, "Secu 1", 100)
        _dev(db, "Secu 1", 50, event="SAISON", year=str(EC.saison_year(NOW)))
        _fiche(db)
        # Les noms ne sont uniques que par appairage : la fiche "Secu 1" de
        # l'epreuve appartient a la tablette de l'epreuve.
        assert _cands(db) == [epr["_id"]]
        assert field.find_device_for_fiche(db, "Secu 1", "E", 2026)["_id"] == epr["_id"]

    def test_engagement_par_nom_trouve_la_tablette_saison(self, db):
        _epreuve_active(db)
        sai = _dev(db, "Patrouille 7", 100, event="SAISON", year=str(EC.saison_year(NOW)))
        assert field.find_device_for_fiche(db, "Patrouille 7", "E", 2026)["_id"] == sai["_id"]
        EC.invalidate()
        db["parametrages"].delete_many({})
        EC.invalidate()
        assert field.find_device_for_fiche(db, "Patrouille 7", "E", 2026) is None

    def test_file_du_service_sur_tous_les_evenements_actifs(self, db, monkeypatch):
        import sys
        import types
        from flask import Flask
        fake = types.ModuleType("app")
        fake.role_required = lambda role: (lambda f: f)
        fake._user_dispatch_categories = lambda u: ["PCO.Technique"]
        fake._user_can_edit_fiche = lambda u: False
        fake._user_can_create_fiche = lambda u: True
        monkeypatch.setitem(sys.modules, "app", fake)
        monkeypatch.setattr(DA, "_app_db", lambda: db)
        app = Flask(__name__)
        app.register_blueprint(DA.dispatch_bp)
        client = app.test_client()

        _epreuve_active(db)
        yr = EC.saison_year(NOW)
        _dev(db, "Saison", 100, event="SAISON", year=str(yr))
        _fiche(db, "epr")
        _fiche(db, "sai", event=EC.SAISON, year=yr)
        _fiche(db, "sai_vieille", event=EC.SAISON, year=yr, ts=NOW - timedelta(days=45))
        _fiche(db, "autre", event="Z", year=2026)

        d = client.get("/api/dispatch/board").get_json()
        assert sorted(f["id"] for f in d["fiches"]) == ["epr", "sai"]
        assert d["older_open"] == 1 and d["multi_event"] is True
        assert d["current"] == {"event": "E", "year": 2026}
        assert [u["name"] for u in d["units"]] == ["Saison"]
        # Filtre optionnel
        d = client.get("/api/dispatch/board?event=E&year=2026").get_json()
        assert [f["id"] for f in d["fiches"]] == ["epr"] and d["filtered"] is True

    def test_creation_depuis_la_tablette_suit_la_bascule(self, db):
        sai = _dev(db, "Saison", 100, event="SAISON", year=str(EC.saison_year(NOW)))
        epr = _dev(db, "Epr", 100)
        assert field.fiche_target_event(db, sai) == (EC.SAISON, EC.saison_year(NOW))
        assert field.fiche_target_event(db, epr) == (EC.SAISON, EC.saison_year(NOW))
        _epreuve_active(db)
        assert field.fiche_target_event(db, sai) == ("E", 2026)
        assert field.fiche_target_event(db, epr) == ("E", 2026)
