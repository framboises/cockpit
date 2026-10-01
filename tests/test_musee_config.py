"""Musee : horaires par jour, validation de la configuration, migration v1,
structure Handshake. Aucune valeur operationnelle n'est codee dans musee.py :
les identifiants ci-dessous sont des donnees de test."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import musee as M  # noqa: E402
from conftest import FakeDb  # noqa: E402


def _cfg(**over):
    doc = {
        "schema": 2,
        "enabled": True,
        "horaires": {
            "ouverture": "10:00", "fermeture": "19:00",
            "semaine": {"lun": {"ferme": True}, "dim": {"ouverture": "11:00", "fermeture": "18:00"}},
            "exceptions": [
                {"date": "2026-12-25", "ferme": True, "libelle": "Noel"},
                {"date": "2026-07-19", "ferme": False, "ouverture": "10:00", "fermeture": "22:00",
                 "libelle": "Nocturne"},
            ],
        },
        "area": {"id": "11", "nom": "MUSEE TEST"},
        "gates": [{"id": "12", "nom": "ENTREE"}],
        "checkpoints": [
            {"id": "21", "nom": "CP-A", "libelle": "Caisse A", "inclus": True, "mobile": False},
            {"id": "22", "nom": "CP-B", "libelle": "", "inclus": False, "mobile": False},
            {"id": "23", "nom": "PDA", "libelle": "", "inclus": True, "mobile": True},
        ],
        "transactions": True,
        "releve_perime_min": 20,
        "statuts_passage": ["0", "107"],
    }
    doc.update(over)
    return M.normalize_config(doc)


def _p(date, hm):
    return M.paris_at(date, hm)


# ---------------------------------------------------------------------------
# Horaires du jour
# ---------------------------------------------------------------------------

def test_horaires_defaut_un_mardi():
    h = M.horaires_du_jour(_cfg(), "2026-10-06")          # mardi
    assert h == {"ferme": False, "ouverture": "10:00", "fermeture": "19:00",
                 "source": "defaut", "libelle": ""}


def test_horaires_lundi_ferme_et_dimanche_specifique():
    lun = M.horaires_du_jour(_cfg(), "2026-10-05")
    assert lun["ferme"] and lun["source"] == "semaine"
    dim = M.horaires_du_jour(_cfg(), "2026-10-04")
    assert (dim["ouverture"], dim["fermeture"], dim["source"]) == ("11:00", "18:00", "semaine")


def test_exception_prioritaire_sur_le_jour_de_semaine():
    # 2026-07-19 est un dimanche (11h-18h) mais nocturne exceptionnelle.
    h = M.horaires_du_jour(_cfg(), "2026-07-19")
    assert (h["ouverture"], h["fermeture"], h["source"], h["libelle"]) == \
        ("10:00", "22:00", "exception", "Nocturne")
    noel = M.horaires_du_jour(_cfg(), "2026-12-25")
    assert noel["ferme"] and noel["libelle"] == "Noel"


def test_statut_ouverture_utilise_les_horaires_du_jour():
    cfg = _cfg()
    assert M.statut_ouverture(cfg, _p("2026-10-04", "10:30"))[0] == "avant_ouverture"   # dimanche 11h
    assert M.statut_ouverture(cfg, _p("2026-10-06", "10:30"))[0] == "ouvert"            # mardi 10h
    assert M.statut_ouverture(cfg, _p("2026-10-05", "12:00"))[0] == "ferme_jour"        # lundi
    st, lib = M.statut_ouverture(cfg, _p("2026-12-25", "12:00"))
    assert st == "ferme_jour" and "Noel" in lib
    assert M.statut_ouverture(cfg, _p("2026-07-19", "21:00"))[0] == "ouvert"            # nocturne


def _releve(date, hm, entries):
    ts = _p(date, hm).astimezone(M.UTC)
    return {"ts": ts, "date": date, "heure": hm, "compteurs": {"11": {"entries": entries}}}


def test_base_du_compteur_avant_l_ouverture_du_jour():
    # Dimanche : ouverture 11h. Un releve a 10:30 est donc une base valable.
    date = "2026-10-04"
    db = FakeDb(musee_releves=[_releve(date, "10:30", 1000), _releve(date, "12:00", 1040)],
                musee_jours=[], musee_passages=[])
    day = M.compute_day(db, date, _cfg())
    assert day["source"] == "compteur" and day["visiteurs"] == 40
    # Le meme releve un mardi (ouverture 10h) arrive apres l'ouverture : partiel.
    date2 = "2026-10-06"
    db2 = FakeDb(musee_releves=[_releve(date2, "10:30", 1000), _releve(date2, "12:00", 1040)],
                 musee_jours=[], musee_passages=[])
    day2 = M.compute_day(db2, date2, _cfg())
    assert day2["source"] == "compteur_partiel" and day2["partiel_depuis"] == "10:30"


def test_statuts_passage_configurables():
    date = "2026-10-06"
    passages = [{"date": date, "heure": "10:%02d:00" % i, "status": s, "checkpoint_id": "21",
                 "checkpoint_nom": "CP-A"} for i, s in enumerate(["0", "107", "133", "106"])]
    db = FakeDb(musee_releves=[], musee_passages=passages,
                musee_jours=[{"_id": date, "tx_depuis": "00:00:00", "tx_retard": False}])
    day = M.compute_day(db, date, _cfg())                    # statuts 0 et 107
    assert day["source"] == "transactions" and day["visiteurs"] == 2
    assert day["par_checkpoint"]["21"]["nom"] == "Caisse A"  # nom affiche


def test_build_state_courbe_et_jour_ferme():
    db = FakeDb(musee_releves=[], musee_passages=[], musee_jours=[])
    st = M.build_state(db, now=_p("2026-10-04", "12:00"), cfg=_cfg())      # dimanche 11-18
    assert [r["heure"] for r in st["par_heure"]] == ["11", "12", "13", "14", "15", "16", "17"]
    assert st["horaires_jour"]["source"] == "semaine" and st["area_nom"] == "MUSEE TEST"
    assert [c["id"] for c in st["par_checkpoint"]] == ["21", "23"]          # 22 exclu
    ferme = M.build_state(db, now=_p("2026-10-05", "12:00"), cfg=_cfg())   # lundi
    assert ferme["statut"] == "ferme_jour" and ferme["par_heure"] == []
    assert ferme["ouverture"] is None


def test_sans_configuration_a_configurer():
    db = FakeDb(cockpit_settings=[], musee_releves=[], musee_passages=[], musee_jours=[])
    assert M.get_config(db) is None
    st = M.build_state(db, now=_p("2026-10-06", "12:00"))
    assert st["configure"] is False and st["statut"] == "a_configurer"
    sans_area = M.normalize_config({"schema": 2, "horaires": {"ouverture": "10:00", "fermeture": "19:00"}})
    assert sans_area["configure"] is False and sans_area["locations"] == []


def test_locations_derivees_et_seuil_perime():
    cfg = _cfg()
    assert [(l["id"], l["role"]) for l in cfg["locations"]] == \
        [("11", "area"), ("12", "gate"), ("21", "checkpoint"), ("23", "checkpoint")]
    assert cfg["releve_perime_s"] == 1200
    assert cfg["statuts_passage"] == ["0", "107"]


def test_passage_filtre_sur_l_area_pas_sur_le_checkpoint():
    tx = {"transaction_id": 5, "date_paris": "2026-10-06 10:00:00", "status": "0",
          "area": {"ID": "628"}, "checkpoint": {"ID": "23", "Name": "PDA"}, "direction": "Entree"}
    assert M.passage_doc(tx, "11") is None                 # checkpoint mobile, autre Area
    tx["area"] = {"ID": "11"}
    tx["gate"] = {"ID": "12", "Name": "ENTREE"}
    d = M.passage_doc(tx, "11")
    assert d["checkpoint_id"] == "23" and d["gate_id"] == "12" and d["area_id"] == "11"
    assert M.passage_doc(tx, "") is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

KNOWN = {"11": {"type": "Area", "nom": "MUSEE TEST"}, "12": {"type": "Gate", "nom": "ENTREE"},
         "21": {"type": "Checkpoint", "nom": "CP-A"}, "23": {"type": "Checkpoint", "nom": "PDA"}}


def _payload(**over):
    p = {
        "enabled": True, "transactions": True,
        "horaires": {"ouverture": "10:00", "fermeture": "19:00",
                     "semaine": {"lun": {"ferme": True}, "mar": None,
                                 "dim": {"ouverture": "11:00", "fermeture": "18:00"}},
                     "exceptions": [{"date": "2026-12-25", "ferme": True, "libelle": " Noel "}]},
        "area": {"id": "11", "nom": "x"},
        "gates": [{"id": "12"}],
        "checkpoints": [{"id": "21", "libelle": "Caisse A", "inclus": True, "mobile": False},
                        {"id": "23", "inclus": True, "mobile": True}],
        "releve_perime_min": 15,
        "statuts_passage": ["133", "0", "107", "0"],
    }
    p.update(over)
    return p


def test_validation_ok_et_normalisation():
    doc, errs = M.validate_config(_payload(), known=KNOWN)
    assert errs == []
    assert doc["area"] == {"id": "11", "nom": "MUSEE TEST"}           # nom de la structure
    assert doc["horaires"]["semaine"] == {"lun": {"ferme": True},
                                          "dim": {"ouverture": "11:00", "fermeture": "18:00"}}
    assert doc["horaires"]["exceptions"][0]["libelle"] == "Noel"
    assert doc["statuts_passage"] == ["0", "107", "133"]
    assert doc["checkpoints"][1] == {"id": "23", "nom": "PDA", "libelle": "", "inclus": True,
                                     "mobile": True}
    cfg = M.normalize_config(doc)
    assert cfg["configure"] and M.horaires_du_jour(cfg, "2026-10-05")["ferme"]


def _erreurs(**over):
    return M.validate_config(_payload(**over), known=KNOWN)[1]


def test_validation_horaires_invalides():
    assert _erreurs(horaires={"ouverture": "9:00", "fermeture": "19:00"})
    assert _erreurs(horaires={"ouverture": "10:00", "fermeture": "24:00"})
    assert _erreurs(horaires={"ouverture": "19:00", "fermeture": "10:00"})
    assert _erreurs(horaires={"ouverture": "10:00", "fermeture": "19:00",
                              "semaine": {"lundi": {"ferme": True}}})
    assert _erreurs(horaires={"ouverture": "10:00", "fermeture": "19:00",
                              "semaine": {"sam": {"ouverture": "12:00", "fermeture": "11:00"}}})


def test_validation_exceptions():
    base = {"ouverture": "10:00", "fermeture": "19:00"}
    assert _erreurs(horaires=dict(base, exceptions=[{"date": "2026-02-30", "ferme": True}]))
    assert _erreurs(horaires=dict(base, exceptions=[{"date": "25/12/2026", "ferme": True}]))
    assert _erreurs(horaires=dict(base, exceptions=[{"date": "2026-12-25", "ferme": True},
                                                    {"date": "2026-12-25", "ferme": True}]))
    assert _erreurs(horaires=dict(base, exceptions=[{"date": "2026-07-19", "ferme": False,
                                                     "ouverture": "10:00"}]))
    assert not _erreurs(horaires=dict(base, exceptions=[{"date": "2026-07-19", "ferme": False,
                                                         "ouverture": "10:00",
                                                         "fermeture": "22:00"}]))


def test_validation_perimetre_contre_la_structure():
    assert _erreurs(area={"id": "99"})                             # inconnue
    assert _erreurs(area={"id": "12"})                             # une Gate, pas une Area
    assert _erreurs(area=None)
    assert _erreurs(checkpoints=[{"id": "21", "inclus": True}, {"id": "404"}])
    assert _erreurs(gates=[{"id": "21"}])                          # un Checkpoint
    assert _erreurs(checkpoints=[{"id": "21", "inclus": False}])   # aucun inclus
    assert _erreurs(checkpoints=[{"id": "21; drop"}])
    # Sans structure (test a blanc) : seul le format est controle.
    assert M.validate_config(_payload(area={"id": "99"}), known=None)[1] == []


def test_validation_reglages_avances():
    assert _erreurs(releve_perime_min=0)
    assert _erreurs(releve_perime_min=True)
    assert _erreurs(releve_perime_min="15")
    assert _erreurs(statuts_passage=[])
    assert _erreurs(statuts_passage=["0", "OK"])
    assert _erreurs(enabled="oui")
    assert M.validate_config("pas un objet")[1]


# ---------------------------------------------------------------------------
# Migration du schema 1
# ---------------------------------------------------------------------------

V1 = {
    "_id": "musee", "enabled": True, "ouverture": "10:00", "fermeture": "19:00",
    "jours_fermes": ["2026-12-25"], "area_id": "11", "transactions": True,
    "locations": [
        {"id": "11", "type": "Area", "nom": "MUSEE TEST", "role": "area"},
        {"id": "12", "type": "Gate", "nom": "ENTREE", "role": "gate"},
        {"id": "21", "type": "Checkpoint", "nom": "CP-A", "role": "checkpoint"},
        {"id": "23", "type": "Checkpoint", "nom": "PDA", "role": "checkpoint", "mobile": True},
    ],
}


def test_migration_v1_sans_perte_et_idempotente():
    v2 = M.migrate_legacy(V1)
    assert v2["schema"] == 2
    assert v2["horaires"] == {"ouverture": "10:00", "fermeture": "19:00", "semaine": {},
                              "exceptions": [{"date": "2026-12-25", "ferme": True, "libelle": ""}]}
    assert v2["area"] == {"id": "11", "nom": "MUSEE TEST"}
    assert v2["gates"] == [{"id": "12", "nom": "ENTREE"}]
    assert [(c["id"], c["mobile"], c["inclus"]) for c in v2["checkpoints"]] == \
        [("21", False, True), ("23", True, True)]
    assert not any(k in v2 for k in ("ouverture", "jours_fermes", "area_id", "locations"))
    assert M.migrate_legacy(v2) == v2
    # Lecture d'un doc v1 non migre : memes locations qu'avant la migration.
    cfg = M.normalize_config(V1)
    assert [(l["id"], l["type"], l["role"], l["mobile"]) for l in cfg["locations"]] == \
        [(l["id"], l["type"], l["role"], bool(l.get("mobile"))) for l in V1["locations"]]
    assert M.horaires_du_jour(cfg, "2026-12-25")["ferme"]


def test_script_de_migration(capsys):
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "musee_migrate_config.py")
    spec = importlib.util.spec_from_file_location("musee_migrate_config", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.migrate(FakeDb(cockpit_settings=[]), out=lambda *_a: None) == "absent"
    db = FakeDb(cockpit_settings=[dict(V1, schema=2)])
    assert mod.migrate(db, out=lambda *_a: None) == "deja"
    assert mod.migrate(FakeDb(cockpit_settings=[dict(V1)]), dry_run=True,
                       out=lambda *_a: None) == "dry-run"


# ---------------------------------------------------------------------------
# Structure Handshake
# ---------------------------------------------------------------------------

def test_structure_relations_et_checkpoint_mobile():
    ancienne = [
        {"location_id": "11", "location_type": "Area", "location_name": "MUSEE TEST",
         "enfants": [{"id": "12", "type": "Gate"}]},
        {"location_id": "12", "location_type": "Gate", "location_name": "ENTREE",
         "parent_area": {"id": "11"}, "enfants": [{"id": "23", "type": "Checkpoint"}]},
        {"location_id": "23", "location_type": "Checkpoint", "location_name": "PDA"},
        {"location_id": "21", "location_type": "Checkpoint", "location_name": "CP-A",
         "parent_area": {"id": "11"}, "parent_gate": {"id": "12"}},
    ]
    recente = [
        {"location_id": "50", "location_type": "Area", "location_name": "ENCEINTE"},
        {"location_id": "51", "location_type": "Gate", "location_name": "HELPDESK",
         "parent_area": {"id": "50"}},
        {"location_id": "23", "location_type": "Checkpoint", "location_name": "PDA",
         "parent_gate": {"id": "51"}},
    ]
    inventaire = [{"location_id": i, "location_type": t, "location_name": n} for i, t, n in
                  [("11", "Area", "MUSEE TEST"), ("12", "Gate", "ENTREE"),
                   ("23", "Checkpoint", "PDA"), ("50", "Area", "ENCEINTE"),
                   ("51", "Gate", "HELPDESK")]]
    s = M.build_structure(inventaire, [("ED1 2026", ancienne), ("ED2 2026", recente)],
                          passages=[{"checkpoint_id": "24", "checkpoint_nom": "CP-NEUF",
                                     "gate_id": "12", "area_id": "11"}])
    musee = next(a for a in s["areas"] if a["id"] == "11")
    assert [g["id"] for g in musee["gates"]] == ["12"]
    cps = {c["id"]: c for c in musee["checkpoints"]}
    assert set(cps) == {"21", "23", "24"}
    assert cps["23"]["mobile_suggere"] and cps["23"]["autres_areas"][0]["id"] == "50"
    assert not cps["21"]["mobile_suggere"] and cps["21"]["borne"] is False
    assert cps["24"]["editions"] == ["collecte musee"]
    assert s["known"]["12"]["type"] == "Gate"
    # Borne injoignable : la structure vient des archives seules.
    s2 = M.build_structure(None, [("ED1 2026", ancienne)])
    assert [a["id"] for a in s2["areas"]] == ["11"] and s2["areas"][0]["borne"] is False
