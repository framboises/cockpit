"""Meteo fusionnee (meteo_horaire.py) : priorite mesure > prevision > tendance, completion, ciel, synthese du jour."""

from datetime import date

import meteo_horaire as mh


class _Col:
    def __init__(self, docs):
        self.docs = docs

    def find(self, q, proj=None):
        dates = q["Date"]["$in"]
        return [d for d in self.docs if d["Date"] in dates]


class _Db(dict):
    def __getitem__(self, nom):
        return self.get(nom) or _Col([])


def _db():
    return _Db(
        meteo_observations=_Col([{"Date": "2026-11-02", "Heures": [
            {"Heure": "12:00", "Température (°C)": 13.0, "Pluviométrie (mm)": 0.0, "Ensoleillement (min)": 55}]}]),
        meteo_previsions=_Col([{"Date": "2026-11-02", "Heures": [
            {"Heure": "12:00", "Température (°C)": 11.0, "Pluviométrie (mm)": 0.0, "Direction vent (°)": 270, "Nebulosite (%)": 90},
            {"Heure": "13:00", "Température (°C)": 12.0, "Pluviométrie (mm)": 3.0, "historique_pluie": 1}]}]),
        meteo_tendances=_Col([{"Date": "2026-11-10", "Heures": [
            {"Heure": "12:00", "Température (°C)": 9.0, "Pluviométrie (mm)": 0.0, "Code temps (WMO)": 3}]}]),
    )


def test_priorite_et_completion():
    j = mh.jours(_db(), date(2026, 11, 2), date(2026, 11, 2))["2026-11-02"]
    midi, treize = j
    assert midi["Source"] == "mesure" and midi["Température (°C)"] == 13.0
    assert midi["Direction vent (°)"] == 270          # complete par la prevision
    assert midi["Jour"] is True and midi["Ciel"] == "clair"  # ensoleillement mesure, pas la nebulosite prevue
    assert treize["Source"] == "prevision" and treize["Ciel"] == "averses"


def test_tendance_lointaine_et_resume():
    j = mh.jours(_db(), date(2026, 11, 10), date(2026, 11, 10))["2026-11-10"]
    assert j[0]["Source"] == "tendance" and j[0]["Ciel"] == "couvert"
    r = mh.resume_jour("2026-11-10", j)
    assert r["Température Max (°C)"] == 9 and r["Ciel"] == "couvert" and r["Source"] == "tendance"


def test_jour_sans_donnee():
    assert mh.jours(_db(), date(2026, 11, 20), date(2026, 11, 20)) == {"2026-11-20": []}
