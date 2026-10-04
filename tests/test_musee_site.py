"""Musee, perimetre "Site - visites libres" : jours publics SAISON, bascule
epreuve, fenetre +/- marge, entrees / sorties / presents, validation et
amorce de la configuration. Les identifiants sont des donnees de test."""

import datetime as dt
import importlib.util
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import event_courant  # noqa: E402
import musee as M  # noqa: E402
from conftest import FakeDb  # noqa: E402

DAY = "2026-10-10"


def _doc(**site_over):
    site = {"enabled": True, "marge_min": 30,
            "checkpoints": [{"id": "749", "nom": "TRI-27", "inclus": True, "sens": "mixte"},
                            {"id": "750", "nom": "TRI-28", "inclus": True, "sens": "entree"},
                            {"id": "751", "nom": "PMR", "inclus": False, "sens": "mixte"}]}
    site.update(site_over)
    return {"schema": 2, "enabled": True,
            "horaires": {"ouverture": "10:00", "fermeture": "19:00"},
            "area": {"id": "11", "nom": "MUSEE"}, "checkpoints": [{"id": "21", "inclus": True}],
            "statuts_passage": ["0", "107", "133"], "site": site}


def _cfg(**site_over):
    return M.normalize_config(_doc(**site_over))


def _p(hm, date=DAY):
    return M.paris_at(date, hm)


def _saison(dates):
    return {"event": "SAISON", "year": DAY[:4], "data": {"globalHoraires": {"dates": dates}}}


def _epreuve(start, end):
    return {"event": "GPF", "year": DAY[:4],
            "data": {"globalHoraires": {"montage": {"start": start}, "demontage": {"end": end},
                                        "dates": []}}}


def _db(parametrages, **cols):
    event_courant.invalidate()
    base = dict(cockpit_settings=[], musee_site_releves=[], musee_site_passages=[],
                musee_site_jours=[], musee_releves=[], musee_passages=[], musee_jours=[])
    base.update(cols)
    return FakeDb(parametrages=parametrages, **base)


PUBLIC = {"date": DAY, "openTime": "10:00", "closeTime": "18:00", "is24h": False}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_normalize_site_defaut_inactif_et_locations():
    sans = M.normalize_config({"schema": 2, "horaires": {"ouverture": "10:00", "fermeture": "19:00"},
                               "area": {"id": "11"}})
    assert sans["site"]["enabled"] is False and sans["site"]["configure"] is False
    assert sans["site"]["marge_min"] == M.DEFAUT_SITE_MARGE_MIN
    cfg = _cfg()
    assert [(l["id"], l["sens"]) for l in cfg["site"]["locations"]] == [("749", "mixte"), ("750", "entree")]
    assert cfg["site"]["configure"] is True
    # Les checkpoints du site ne sont jamais des locations du musee.
    assert all(l["id"] not in ("749", "750") for l in cfg["locations"])


KNOWN = {"11": {"type": "Area", "nom": "MUSEE"}, "12": {"type": "Gate", "nom": "PORTE"},
         "21": {"type": "Checkpoint", "nom": "CP-A"},
         "749": {"type": "Checkpoint", "nom": "TRI-NORV-27"},
         "750": {"type": "Checkpoint", "nom": "TRI-NORV-28"}, "628": {"type": "Area", "nom": "ENCEINTE"}}


def _payload(site):
    return {"enabled": True, "transactions": True,
            "horaires": {"ouverture": "10:00", "fermeture": "19:00"},
            "area": {"id": "11"}, "checkpoints": [{"id": "21", "inclus": True}],
            "releve_perime_min": 15, "statuts_passage": ["0"], "site": site}


def test_validation_site():
    ok = {"enabled": True, "marge_min": 30,
          "checkpoints": [{"id": "749", "sens": "sortie"}, {"id": "750", "inclus": True}]}
    doc, errs = M.validate_config(_payload(ok), known=KNOWN)
    assert errs == []
    assert doc["site"]["checkpoints"][0] == {"id": "749", "nom": "TRI-NORV-27", "libelle": "",
                                             "inclus": True, "sens": "sortie"}
    assert doc["site"]["checkpoints"][1]["sens"] == "mixte"

    def errs_of(**over):
        s = dict(ok)
        s.update(over)
        return M.validate_config(_payload(s), known=KNOWN)[1]
    assert errs_of(marge_min=-1)
    assert errs_of(marge_min=241)
    assert errs_of(marge_min="30")
    assert errs_of(enabled="oui")
    assert errs_of(checkpoints=[{"id": "404"}])                     # inconnu
    assert errs_of(checkpoints=[{"id": "628"}])                     # une Area
    assert errs_of(checkpoints=[{"id": "749", "sens": "haut"}])
    assert errs_of(checkpoints=[{"id": "749", "inclus": False}])    # actif sans checkpoint
    assert not errs_of(enabled=False, checkpoints=[])               # inactif : vide permis
    # Sans cle site : le document n'y touche pas (PUT $set la conserve).
    p = _payload(ok)
    del p["site"]
    doc2, errs2 = M.validate_config(p, known=KNOWN)
    assert errs2 == [] and "site" not in doc2


# ---------------------------------------------------------------------------
# Jour de visites libres (gating)
# ---------------------------------------------------------------------------

def test_fenetre_marge_et_bornes():
    site = _cfg()["site"]
    assert M.site_fenetre(site, {"open": "10:00", "close": "18:00"}) == {"debut": "09:30", "fin": "18:30"}
    assert M.site_fenetre(site, {"open": "00:10", "close": "23:50"}) == {"debut": "00:00", "fin": "23:59"}
    assert M.site_fenetre(site, {"open": "10:00", "close": "18:00", "is24h": True}) == \
        {"debut": "00:00", "fin": "23:59"}


def test_gate_pur():
    site = _cfg()["site"]
    info = {"open": "10:00", "close": "18:00", "is24h": False}
    assert M.site_gate(site, _p("12:00"), None, False)["raison"] == "pas_jour_public"
    g = M.site_gate(site, _p("12:00"), info, True)
    assert g["raison"] == "epreuve" and not g["collecte"]
    g = M.site_gate(site, _p("08:00"), info, False)
    assert g["raison"] == "avant" and g["collecte"] and not g["dans_fenetre"]
    g = M.site_gate(site, _p("09:45"), info, False)               # dans la marge
    assert g["raison"] == "ouvert" and g["dans_fenetre"]
    g = M.site_gate(site, _p("18:25"), info, False)
    assert g["dans_fenetre"]
    g = M.site_gate(site, _p("18:45"), info, False)
    assert g["raison"] == "termine" and g["collecte"] and not g["dans_fenetre"]
    off = _cfg(enabled=False)["site"]
    assert M.site_gate(off, _p("12:00"), info, False)["raison"] == "desactive"


def test_gate_db_jour_public_saison_et_bascule_epreuve():
    cfg = _cfg()
    now = _p("12:00").astimezone(M.UTC)
    # Pas de jour public saisi sur SAISON.
    g = M.site_gate_db(_db([_saison([])]), cfg, now)
    assert g["raison"] == "pas_jour_public" and not g["collecte"]
    # Jour public, aucune epreuve.
    g = M.site_gate_db(_db([_saison([PUBLIC])]), cfg, now)
    assert g["raison"] == "ouvert" and g["collecte"] and g["fenetre"] == {"debut": "09:30", "fin": "18:30"}
    # Jour public mais une epreuve est active (montage compris) : jamais compte.
    ep = _epreuve("2026-10-08T08:00:00", "2026-10-12T20:00:00")
    g = M.site_gate_db(_db([_saison([PUBLIC]), ep]), cfg, now)
    assert g["raison"] == "epreuve" and not g["collecte"]
    # Epreuve terminee la veille : on compte.
    ep2 = _epreuve("2026-10-01T08:00:00", "2026-10-09T20:00:00")
    assert M.site_gate_db(_db([_saison([PUBLIC]), ep2]), cfg, now)["collecte"]


def test_prochains_jours_signale_l_epreuve():
    ep = _epreuve("2026-10-12T08:00:00", "2026-10-14T20:00:00")
    jours = [PUBLIC, dict(PUBLIC, date="2026-10-13"), dict(PUBLIC, date="2026-09-01")]
    out = M.site_prochains_jours(_db([_saison(jours), ep]), "2026-10-02")
    assert [j["date"] for j in out] == ["2026-10-10", "2026-10-13"]
    assert out[0]["epreuve"] is None and out[1]["epreuve"] == "GPF 2026"


# ---------------------------------------------------------------------------
# Entrees / sorties / presents
# ---------------------------------------------------------------------------

def _rel(hm, c749, c750):
    ts = _p(hm).astimezone(M.UTC)
    return {"ts": ts, "date": DAY, "heure": hm,
            "compteurs": {"749": {"entries": c749[0], "exits": c749[1]},
                          "750": {"entries": c750[0], "exits": c750[1]},
                          "751": {"entries": 999, "exits": 999}}}


def test_presents_par_compteurs_avec_base_avant_la_fenetre():
    cfg = _cfg()
    rel = [_rel("08:00", (100, 500), (2000, 10)),       # base (avant 09:30)
           _rel("11:00", (105, 510), (2040, 10)),       # +45 E, +10 S -> 35
           _rel("13:00", (110, 520), (2100, 12)),       # +65 E, +12 S -> 88
           _rel("17:00", (110, 590), (2100, 30)),       # +0, +88 -> 0
           _rel("19:00", (200, 600), (2200, 40))]       # hors fenetre (> 18:30)
    db = _db([], musee_site_releves=rel)
    day = M.compute_site_day(db, DAY, cfg, {"debut": "09:30", "fin": "18:30"})
    assert day["source"] == "compteur"
    assert (day["entrees"], day["sorties"]) == (110, 110)
    assert day["presents_now"] == 0
    assert day["presents_max"] == {"n": 88, "heure": "13:00"}
    assert day["par_heure"]["11"] == {"entrees": 45, "sorties": 10}
    assert day["par_checkpoint"]["750"] == {"nom": "TRI-28", "entrees": 100, "sorties": 20}
    assert "751" not in day["par_checkpoint"]              # exclu


def test_compteur_remis_a_zero_et_partiel():
    cfg = _cfg()
    rel = [_rel("09:00", (100, 100), (100, 100)),
           _rel("10:00", (110, 100), (3, 0)),               # 750 remis a zero : +3 entier
           _rel("11:00", (110, 104), (5, 1))]
    day = M.compute_site_day(_db([], musee_site_releves=rel), DAY, cfg, {"debut": "09:30", "fin": "18:30"})
    assert (day["entrees"], day["sorties"]) == (10 + 3 + 2, 4 + 1)
    # Premier releve apres le debut de la fenetre : partiel, annonce comme tel.
    day2 = M.compute_site_day(_db([], musee_site_releves=rel[1:]), DAY, cfg,
                              {"debut": "09:30", "fin": "18:30"})
    assert day2["source"] == "compteur_partiel" and day2["partiel_depuis"] == "10:00"


def _tx(i, hm, cid, sens, status="0"):
    return {"_id": i, "date": DAY, "heure": hm + ":00", "checkpoint_id": cid, "sens": sens,
            "status": status}


def test_presents_par_transactions():
    cfg = _cfg()
    tx = [_tx(1, "09:00", "749", "E"),                     # hors fenetre
          _tx(2, "10:00", "750", "E"), _tx(3, "10:05", "749", "E"), _tx(4, "10:10", "750", "E"),
          _tx(5, "11:00", "749", "S"), _tx(6, "11:30", "749", "E", status="106"),  # refus
          _tx(7, "12:00", "751", "E"),                     # checkpoint exclu
          _tx(8, "12:30", "749", "?")]                     # sens inconnu
    db = _db([], musee_site_passages=tx,
             musee_site_jours=[{"_id": DAY, "tx_depuis": "00:00:00", "tx_retard": False}])
    day = M.compute_site_day(db, DAY, cfg, {"debut": "09:30", "fin": "18:30"})
    assert day["source"] == "transactions"
    assert (day["entrees"], day["sorties"], day["presents_now"]) == (3, 1, 2)
    assert day["presents_max"] == {"n": 3, "heure": "10:10"}
    assert day["par_heure"]["10"] == {"entrees": 3, "sorties": 0}
    # Couverture partie apres le debut de fenetre : pas "complete".
    db2 = _db([], musee_site_passages=tx,
              musee_site_jours=[{"_id": DAY, "tx_depuis": "10:30:00", "tx_retard": False}])
    assert M.compute_site_day(db2, DAY, cfg, {"debut": "09:30", "fin": "18:30"})["source"] == \
        "transactions_partiel"


def test_site_passage_doc_filtre_sur_le_checkpoint_et_sens():
    locs = {l["id"]: l for l in _cfg()["site"]["locations"]}
    tx = {"transaction_id": 9, "date_paris": DAY + " 10:00:00", "status": "0", "direction": "Sortie",
          "area": {"ID": "628"}, "gate": {"ID": "937"}, "checkpoint": {"ID": "749", "Name": "TRI-27"}}
    d = M.site_passage_doc(tx, locs)
    assert (d["sens"], d["sens_source"], d["area_id"]) == ("S", "transaction", "628")
    tx2 = dict(tx, direction="Inconnu", checkpoint={"ID": "750"})
    assert M.site_passage_doc(tx2, locs)["sens"] == "E"            # indication "entree"
    assert M.site_passage_doc(dict(tx, direction="Inconnu"), locs)["sens"] == "?"
    assert M.site_passage_doc(dict(tx, checkpoint={"ID": "751"}), locs) is None   # exclu
    assert M.site_passage_doc(dict(tx, checkpoint={"ID": "21"}), locs) is None


def test_state_site_visible_seulement_si_pertinent():
    cfg = _cfg()
    now = _p("12:00").astimezone(M.UTC)
    # Pas un jour de visites libres et aucune donnee : masque.
    st = M.build_site_state(_db([_saison([])]), cfg, now)
    assert st["visible"] is False and st["raison"] == "pas_jour_public"
    # Jour de visites libres sans aucun releve : visible, valeurs None (jamais 0).
    st = M.build_site_state(_db([_saison([PUBLIC])]), cfg, now)
    assert st["visible"] and st["entrees"] is None and st["presents"] is None
    assert st["releve_perime"] is True and st["par_heure"] == []
    # Epreuve active mais donnees du matin : visible, raison "epreuve".
    ep = _epreuve("2026-10-10T11:00:00", "2026-10-12T20:00:00")
    rel = [_rel("08:00", (0, 0), (0, 0)), _rel("10:30", (5, 1), (0, 0))]
    db = _db([_saison([PUBLIC]), ep], musee_site_releves=rel,
             musee_site_jours=[{"_id": DAY, "fenetre": {"debut": "09:30", "fin": "18:30"}}])
    st = M.build_site_state(db, cfg, now)
    assert st["visible"] and st["raison"] == "epreuve" and st["entrees"] == 5 and st["presents"] == 4


# ---------------------------------------------------------------------------
# Amorce de la configuration (scripts/musee_migrate_config.py --site-checkpoints)
# ---------------------------------------------------------------------------

def _mod():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "musee_migrate_config.py")
    spec = importlib.util.spec_from_file_location("musee_migrate_config", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_amorce_site_idempotente():
    mod = _mod()
    quiet = lambda *_a: None  # noqa: E731
    archive = [{"location_id": 749, "location_type": "Checkpoint", "location_name": "TRI-NORV-27"},
               {"location_id": 750, "location_type": "Checkpoint", "location_name": "TRI-NORV-28"},
               {"location_id": 937, "location_type": "Gate", "location_name": "PORTE NORD BIS"}]
    doc = dict(_doc(), _id="musee")
    del doc["site"]
    assert mod.seed_site(FakeDb(cockpit_settings=[]), ["749"], out=quiet) == "absent"
    db = FakeDb(cockpit_settings=[dict(doc)], hsh_archive_structure_GPF_2026=archive)
    assert mod.seed_site(db, ["749", "404"], out=quiet) == "invalide"        # inconnu
    assert mod.seed_site(db, ["937"], out=quiet) == "invalide"               # une Gate
    assert mod.seed_site(db, ["749", "750"], dry_run=True, out=quiet) == "dry-run"
    assert "site" not in db["cockpit_settings"].find_one({"_id": "musee"})
    assert mod.seed_site(db, ["749", "750"], marge=30, out=quiet) == "migre"
    stored = db["cockpit_settings"].find_one({"_id": "musee"})
    assert [c["nom"] for c in stored["site"]["checkpoints"]] == ["TRI-NORV-27", "TRI-NORV-28"]
    assert M.normalize_config(stored)["site"]["configure"] is True
    assert mod.seed_site(db, ["749"], out=quiet) == "deja"
    # Le reste du document est intact.
    assert stored["area"] == {"id": "11", "nom": "MUSEE"}
