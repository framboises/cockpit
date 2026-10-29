"""Fiche d'anticipation d'un evenement (pcorg_anticipation.py) : code pur."""
from datetime import datetime

import pcorg_anticipation as PA


def _d(i, year, hour, grav, lieu="Trucker Camp", nature="incident", **kw):
    d = {"_id": "f%d" % i, "event": "24H CAMIONS", "year": year, "ts": datetime(year, 9, 20, hour, 0),
         "gravite": grav, "nature": nature, "famille": "rixe", "geo": {"name": lieu}, "ligne": "ligne %d" % i}
    d.update(kw)
    return d


def test_lieu_recurrent_devant_un_pic_isole():
    docs = [_d(1, 2024, 0, 3), _d(2, 2025, 0, 3), _d(3, 2025, 14, 4, lieu="Porte Nord")]
    out = PA.build(docs)
    assert out["nb_editions"] == 2
    assert out["foyers"][0]["lieu"] == "Trucker Camp" and out["foyers"][0]["editions"] == 2
    # 00:00 UTC en septembre = 02:00 heure de Paris
    assert out["foyers"][0]["heures"] == ["02:00"]


def test_routines_et_doublons_exclus_gravite_validee_prioritaire():
    docs = [_d(1, 2025, 10, 1, nature="routine"), _d(2, 2025, 10, 3, doublon_probable={"de": "f9"}),
            _d(3, 2025, 10, 2, review={"gravite_validee": 5})]
    out = PA.build(docs)
    assert out["incidents"] == 1 and out["foyers"][0]["gravite_max"] == 5
    assert [g["id"] for g in out["graves"]] == ["f3"]


def test_famille_v5_sinon_sous_classification_officielle():
    assert PA.family({"famille": "rixe"}) == "Rixe / bagarre"
    assert PA.family({"taxo": {"sous_classification": "Altercation-Rixe",
                               "sous_classification_officielle": True}}) == "Altercation-Rixe"
    assert PA.family({}) == "Non classe"
