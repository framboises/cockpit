"""cockpit_situation : point de situation redige par Cockpit, vues alertes et
evenement (remplacent cockpit_alertes et cockpit_evenement), LAPI jamais."""
from datetime import datetime, timedelta, timezone

import pytest

import alfred_situation as S
import alfred_tools as AT
import event_courant
from conftest import FakeDb


@pytest.fixture
def sources(monkeypatch):
    monkeypatch.setattr(AT, "t_trafic", lambda *a: {
        "disponible": True, "verdict": "CHARGE", "accidents_en_zone": 1,
        "axes": [{"axe": "D338", "sens": "entrant", "etat": "dense", "retard": "+6 min"},
                 {"axe": "D139", "sens": None, "etat": "fluide", "retard": "+0 s"}]})
    monkeypatch.setattr(AT, "t_meteo", lambda *a: {
        "disponible": True, "actuel": {"temperature_c": 16.2, "vent_moyen_kmh": 19.4, "rafale_kmh": 32.8},
        "pluie": {"attendue": True, "dans_min": 5, "pic_mmh": 5.28}, "consignes": [],
        "contraintes": [{"libelle": "Vent", "niveau": "vigilance", "pic": 43, "unite": "km/h",
                         "pic_heure": "20:00", "consigne": "Surveiller bâches"}]})
    monkeypatch.setattr(AT, "t_presents", lambda *a: {"disponible": True, "presents": 12345})
    monkeypatch.setattr(AT, "t_main_courante", lambda *a: {
        "disponible": True,
        "compteurs": {"en_cours": 3, "par_categorie": {"Secours": 2, "Sûreté": 1}, "par_urgence": {"UA": 1},
                      "ouvertes_aujourdhui": 5, "closes_aujourdhui": 2},
        "fiches": [{"numero": 101, "ouverte": "16:02", "categorie": "Secours", "urgence": "urgence absolue",
                    "texte": "Malaise tribune 13"}]})
    monkeypatch.setattr(AT, "t_agenda", lambda *a: {"disponible": True, "vignettes": [
        {"debut": "18:00", "fin": "19:00", "activity": "Ouverture porte Nord", "place": "Porte Nord"},
        {"journee": True, "activity": "Séminaire"}]})
    monkeypatch.setattr(event_courant, "payload", lambda db, now=None: {
        "current": {"event": "24H AUTOS", "year": 2026, "phase": "public", "kind": "epreuve"},
        "active": [{"event": "24H AUTOS", "year": 2026, "phase": "public", "kind": "epreuve"},
                   {"event": "SAISON", "year": 2026, "phase": None, "kind": "saison"}],
        "conflict": False})
    monkeypatch.setattr(event_courant, "windows", lambda db: [{
        "event": "24H AUTOS", "year": 2026, "start": "2026-06-07 22:00:00+00:00",
        "end": "2026-06-14 22:00:00+00:00", "public_days": {"2026-06-11", "2026-06-13"}, "race": None}])
    import alfred_evenements
    monkeypatch.setattr(alfred_evenements, "contexte", lambda db, ev, yr: (
        {"evenement": ev, "description": "Course d'endurance automobile."} if ev == "24H AUTOS" else None))


def _alerte(slug, titre, msg, minutes=10):
    now = datetime.now(timezone.utc)
    return {"definition_slug": slug, "title": titre, "message": msg,
            "triggeredAt": now - timedelta(minutes=minutes), "expiresAt": now + timedelta(minutes=30)}


def test_point_de_situation_redige_par_cockpit(sources):
    db = FakeDb(cockpit_active_alerts=[_alerte("meteo-vent", "Vent fort", "Rafales 60 km/h")])
    r = S.t_situation(db, {}, {})
    l = r["resume"].split("\n")
    assert l[0].startswith("Point de situation, ") and "24H AUTOS 2026 (épreuve, phase ouverte au public, " \
                                                      "du 08/06 au 14/06/2026, public du 11/06 au 13/06)" in l[0]
    assert l[1] == "Présents sur site : 12 345."
    assert l[2] == "Trafic : charge, 1 accident. Axes chargés : D338 entrant dense (+6 min)."
    assert l[3].startswith("Météo : 16 °C, vent 19 km/h (rafales 33). Pluie attendue dans 5 min (jusqu'à 5,3 mm/h).")
    assert "Vent (vigilance, 43 km/h vers 20:00) : Surveiller bâches" in l[3]
    assert l[4] == "Alertes : 1 active : Vent fort."
    assert l[5] == "Main courante : 3 fiches en cours (Secours 2, Sûreté 1), dont UA 1 ; aujourd'hui 5 ouvertes, 2 closes."
    assert l[6] == "En tête de liste : n°101 16:02, Secours, urgence absolue « Malaise tribune 13 »."
    assert l[7] == "À venir (6 h) : 18:00-19:00 Ouverture porte Nord, Porte Nord."   # journee entiere a part


def test_whatsapp_sans_main_courante(sources):
    r = S.t_situation(FakeDb(), {}, {"sans_main_courante": True})
    assert "Main courante : non consultable sur ce canal." in r["resume"]
    assert "Malaise" not in r["resume"]


def test_alertes_jamais_lapi_et_plaques_masquees(sources):
    db = FakeDb(cockpit_active_alerts=[
        _alerte("anpr-watchlist", "Plaque surveillée AB-123-CD", "Vu porte Nord"),
        _alerte("traffic-cluster", "Bouchon D338", "Véhicule AB-123-CD arrêté", minutes=5)])
    r = S.t_situation(db, {"sujet": "alertes"}, {})
    assert r["vue"] == "alertes" and r["nombre"] == 1
    assert "AB-123-CD" not in r["resume"] and "surveillée" not in r["resume"]
    assert r["resume"].startswith("Alertes actives de la centrale (") and "Bouchon D338" in r["resume"]
    # L'ancien outil (masque) applique la meme regle
    assert AT.t_alertes(db, {}, {})["nombre"] == 1
    r = S.t_situation(FakeDb(), {"sujet": "alertes"}, {})
    assert r["resume"].startswith("Aucune alerte active")


def test_vue_evenement(sources):
    r = S.t_situation(FakeDb(), {"sujet": "evenement"}, {})
    assert r["resume"].split("\n") == [
        "Événement en cours : 24H AUTOS 2026 (épreuve, phase ouverte au public, du 08/06 au 14/06/2026, "
        "public du 11/06 au 13/06).",
        "Aussi actif : SAISON 2026 (main courante permanente du site, hors épreuve).",
        "24H AUTOS : Course d'endurance automobile."]


def test_sujet_en_mots_d_operateur():
    assert S.sujet_demande("les alertes") == "alertes"
    assert S.sujet_demande("centrale d'alerte") == "alertes"
    assert S.sujet_demande("événement") == "evenement"
    assert S.sujet_demande("") is None and S.sujet_demande("météo") is None


def test_manifeste_neuf_outils():
    noms = [t["function"]["name"] for t in AT.manifest()]
    assert "cockpit_alertes" not in noms and "cockpit_evenement" not in noms
    assert "cockpit_alertes" in AT.TOOLS and "cockpit_evenement" in AT.TOOLS     # encore executables
    assert len(noms) == 9
    sit = next(t for t in AT.manifest() if t["function"]["name"] == "cockpit_situation")
    assert sit["function"]["parameters"]["properties"]["sujet"]["enum"] == ["alertes", "evenement"]
