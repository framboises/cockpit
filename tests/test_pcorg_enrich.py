"""Enrichissement en code pur des fiches structurees (pcorg_enrich.py) et champs v2."""
import json
from datetime import datetime

import pcorg_digest as PD
import pcorg_enrich as PE


def _gaz():
    g = PE.Gazetteer.__new__(PE.Gazetteer)
    g.entries = sorted([
        {"name": "PORTE MAISON-BLANCHE VL", "norm": PE.norm("PORTE MAISON-BLANCHE VL"), "layer": "portes",
         "label": "Porte", "lat": 47.95, "lng": 0.21},
        {"name": "Giratoire Antares", "norm": PE.norm("Giratoire Antares"), "layer": "giratoires",
         "label": "Giratoire", "lat": 47.96, "lng": 0.20},
        {"name": "HOUX", "norm": "houx", "layer": "passerelles", "label": "Passerelle", "lat": 47.94, "lng": 0.22},
    ], key=lambda e: -len(e["norm"]))
    g.tribunes = {34: {"name": "34", "norm": "34", "layer": "tribunes", "label": "Tribune", "lat": 47.951, "lng": 0.211}}
    g.virages = {}
    g.grid = {"AI20": (47.97, 0.19)}
    g.zones = [{"id": "z1", "name": "Secteur Test",
                "geom": {"type": "Polygon", "coordinates": [[[0.18, 47.93], [0.25, 47.93], [0.25, 47.98], [0.18, 47.98], [0.18, 47.93]]]}}]
    return g


class TestPosition:
    def test_gps_d_origine_prioritaire(self):
        geo = _gaz().locate({"gps": {"type": "Point", "coordinates": [0.2, 47.9]}}, "T34")
        assert geo["source"] == "gps" and geo["lat"] == 47.9

    def test_carroyage_avec_espace(self):
        geo = _gaz().locate({"content_category": {"carroye": "ai 20"}}, None)
        assert geo["source"] == "carroyage" and geo["name"] == "AI20"

    def test_tribune_citee(self):
        geo = _gaz().locate({}, "Tribune 34, escalier B")
        assert geo["layer"] == "tribunes" and geo["confidence"] == "haute"
        assert _gaz().locate({}, "T34 - Loge 12")["layer"] == "tribunes"

    def test_nom_le_plus_long_et_accents(self):
        geo = _gaz().locate({}, "bouchon giratoire Antarès")
        assert geo["name"] == "Giratoire Antares"
        assert _gaz().locate({}, "Porte Maison Blanche VL fermee")["layer"] == "portes"

    def test_titre_si_lieu_non_precise(self):
        geo = _gaz().locate({"text": "Fuite passerelle Houx"}, "non precise")
        assert geo["source"] == "titre" and geo["confidence"] == "moyenne"

    def test_rien(self):
        assert _gaz().locate({"text": "rien"}, "non precise") is None

    def test_zone(self):
        g = _gaz()
        assert g.zone_of(47.95, 0.2)["name"] == "Secteur Test"
        assert g.zone_of(48.5, 0.2) is None


class TestDelais:
    def test_delais_depuis_le_signalement(self):
        d = PE.delays({"signalement": "16:20", "engagement": "16:23", "sur_place": "16:41", "fin": "17:05"})
        assert d == {"engagement": 3, "sur_place": 21, "fin": 45}

    def test_passage_de_minuit_et_incoherence(self):
        assert PE.delays({"signalement": "23:50", "fin": "00:20"})["fin"] == 30
        assert PE.delays({"signalement": "08:00", "fin": "21:00"})["fin"] is None
        assert PE.delays({"engagement": "10:00"})["engagement"] is None

    def test_completude(self):
        c = PE.completeness({"lieu": "T34", "etapes": {"fin": "10:00"}, "issue_code": "resolu",
                             "moyens_engages": []}, None)
        assert c["score"] == 3 and c["manquants"] == ["moyens"]


class TestDoublons:
    def test_score(self):
        a = {"lieu_norm": "porte nord", "type": "bagarre", "resume": "Bagarre entre deux spectateurs a la porte nord."}
        b = dict(a, resume="Rixe entre deux spectateurs a la porte nord.")
        c = {"lieu_norm": "t34", "type": "panne ecran", "resume": "Ecran geant eteint."}
        assert PE.dup_score(a, b) >= PE.DUP_THRESHOLD > PE.dup_score(a, c)


class TestReponseV2:
    def test_champs_v2_nettoyes(self):
        r = PD.parse_reply(json.dumps({
            "gravite": 3, "lieu": "T34", "etapes": {"signalement": "9h05", "fin": "25:00"},
            "equipement": "", "moyens_engages": ["PS Nord", "Ludovic ARNAULT", "field:ben", "SDIS Sarthe"],
            "services_exterieurs": "SDIS, SAMU", "causes": ["Alcool", "inconnue"], "issue_code": "Resolu",
            "donnees_personnelles": ["nom", "religion"]}))
        assert r["etapes"]["signalement"] == "09:05" and r["etapes"]["fin"] is None
        assert r["moyens_engages"] == ["PS Nord", "SDIS Sarthe"]
        assert r["services_exterieurs"] == ["SDIS", "SAMU"]
        assert r["causes"] == ["alcool"] and r["issue_code"] == "resolu"
        assert r["donnees_personnelles"] == ["nom"] and r["equipement"] == "non precise"

    def test_noms_de_personnes(self):
        for s in ("Ludovic ARNAULT", "LEBERT Pierre", "field:ludo", "a.chaperon CHAPERON"):
            assert PD._looks_like_person(s), s
        for s in ("PS Nord", "SDIS Sarthe", "Patrouille 4", "Appui Flux", "CMS", "Mobile Teranga"):
            assert not PD._looks_like_person(s), s


def test_fiche_controlee_retraitee_pour_nouvelle_version_seulement():
    fiche = {"text": "x", "comment": "y", "category": "c", "niveau_urgence": None}
    d = {"src_hash": PD.src_hash(fiche), "prompt_version": "v1", "review": {"verdict": "ok"}}
    assert PD.needs_work(fiche, d)                                  # v1 -> v2 : nouveaux champs
    d["prompt_version"] = PD.PROMPT_VERSION
    assert not PD.needs_work(fiche, d)
    assert not PD.needs_work(fiche, dict(d, error="x", attempts=1))  # echec : pas de retraitement


def test_point_dans_polygone_avec_trou():
    geom = {"type": "Polygon", "coordinates": [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}
    assert PE.point_in_geom(2, 2, geom)
    assert not PE.point_in_geom(5, 5, geom)
    assert not PE.point_in_geom(20, 20, geom)
    assert datetime  # module importe pour les dates des doublons
