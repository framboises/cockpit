from datetime import datetime

from conftest import FakeDb

import trafic_etat


class TestGeofence:
    def test_alerte_au_centre_est_en_zone(self):
        alerte = {"location": {"y": trafic_etat.ZONE_CENTER_LAT,
                               "x": trafic_etat.ZONE_CENTER_LON}}
        assert trafic_etat.alerte_en_zone(alerte) is True

    def test_alerte_a_dix_km_est_hors_zone(self):
        # 0.09 degre de latitude vaut environ 10 km.
        alerte = {"location": {"y": trafic_etat.ZONE_CENTER_LAT + 0.09,
                               "x": trafic_etat.ZONE_CENTER_LON}}
        assert trafic_etat.alerte_en_zone(alerte) is False

    def test_alerte_sans_position_est_hors_zone(self):
        # Ne jamais compter ce qu'on ne sait pas placer : une alerte sans
        # coordonnees gonflerait le verdict sans qu'on puisse la situer.
        assert trafic_etat.alerte_en_zone({}) is False
        assert trafic_etat.alerte_en_zone({"location": {}}) is False


class TestVerdictGlobal:
    def test_accident_en_zone_passe_en_critique(self):
        assert trafic_etat.verdict_global({"ACCIDENT": 1}, 0) == 3

    def test_axe_en_bouchon_passe_en_critique(self):
        assert trafic_etat.verdict_global({"ACCIDENT": 0}, 4) == 3

    def test_paliers_intermediaires(self):
        assert trafic_etat.verdict_global({"ACCIDENT": 0}, 3) == 2
        assert trafic_etat.verdict_global({"ACCIDENT": 0}, 2) == 1
        assert trafic_etat.verdict_global({"ACCIDENT": 0}, 1) == 0

    def test_bouchons_waze_ne_pilotent_pas_le_verdict(self):
        # LA regle a tenir. Trente bouchons et dangers Waze dans le cercle ne
        # font PAS monter le verdict : ils peuvent tous etre hors des
        # itineraires surveilles. Seuls les axes et les accidents comptent.
        comptes = {"ACCIDENT": 0, "JAM": 30, "HAZARD": 12}
        assert trafic_etat.verdict_global(comptes, 1) == 0


class TestAgregerTerrains:
    def test_fusionne_les_troncons_du_meme_terrain(self):
        routes = [
            {"name": "#I# Ouest", "time": 120, "historicTime": 100},
            {"name": "#I2# Ouest", "time": 60, "historicTime": 50},
        ]
        out = trafic_etat.agreger_terrains(routes)
        assert len(out) == 1
        assert out[0]["currentTime"] == 180
        assert out[0]["historicTime"] == 150

    def test_ignore_les_itineraires_non_balises(self):
        routes = [{"name": "Fresne -> Leroy Merlin", "time": 173,
                   "historicTime": 162}]
        assert trafic_etat.agreger_terrains(routes) == []

    def test_tri_par_ratio_decroissant(self):
        routes = [
            {"name": "#I# Calme", "time": 100, "historicTime": 100},
            {"name": "#I# Bouche", "time": 300, "historicTime": 100},
        ]
        out = trafic_etat.agreger_terrains(routes)
        assert out[0]["terrain"] == "Bouche"


class TestSeveriteAxe:
    def test_axe_unique_ratio_1_5_retard_500s_donne_severite_1(self):
        # Le cas divergent trouve a la tache 14 : ratio 1,5, 500s de retard.
        # classify_congestion (ratio seul) rendait severite 2 -> vd montre
        # VIGILANCE alors que le mur (classify() JS) rend severite 1 et reste
        # FLUIDE (le verdict ne monte qu'a partir de severite >= 2). Ce test
        # verifie que severite_axe rend bien la valeur du mur, 1, pas 2.
        axe = {"currentTime": 1500, "historicTime": 1000, "deltaSeconds": 500}
        assert trafic_etat.severite_axe(axe) == 1

    def test_troncon_court_ratio_eleve_retard_faible_reste_fluide(self):
        # LE cas que le double verrou existe pour ecarter : 15s -> 45s est un
        # ratio x3 mais ne coute que 30s. Sans le plancher de retard (delay
        # >= 60s pour sortir de "Fluide"), ce troncon negligeable resterait
        # severite 0 -- alors que classify_congestion (ratio seul, l'ancien
        # calcul de la montre) l'aurait classe "bouchon", severite 4.
        axe = {"currentTime": 45, "historicTime": 15, "deltaSeconds": 30}
        assert trafic_etat.severite_axe(axe) == 0
        assert trafic_etat.classify_congestion(45, 15) == ("bouchon", 4)

    def test_vrai_bouchon_ratio_3_retard_600s_donne_severite_4(self):
        axe = {"currentTime": 1800, "historicTime": 600, "deltaSeconds": 1200}
        assert trafic_etat.severite_axe(axe) == 4

    def test_historic_time_sous_le_seuil_est_sans_reference(self):
        # hist <= 5 : aucune reference exploitable, severite 0 quel que soit
        # le courant -- meme regle que classify() cote mur.
        assert trafic_etat.severite_axe(
            {"currentTime": 200, "historicTime": 5, "deltaSeconds": 195}) == 0
        assert trafic_etat.severite_axe(
            {"currentTime": 200, "historicTime": 0, "deltaSeconds": None}) == 0

    def test_delta_seconds_absent_est_recalcule(self):
        # Fidele au mur : deltaSeconds est relu s'il existe, sinon
        # max(0, cur - hist). Meme resultat que le meme axe avec
        # deltaSeconds explicite.
        avec = trafic_etat.severite_axe(
            {"currentTime": 1800, "historicTime": 600, "deltaSeconds": 1200})
        sans = trafic_etat.severite_axe(
            {"currentTime": 1800, "historicTime": 600})
        assert avec == sans == 4


class TestPireSeveriteMur:
    def test_axe_parking_bouchonne_est_vu_alors_qu_il_est_exclu_de_l_agregation(self):
        # #P n'apparait jamais dans agreger_terrains() (prefixe non retenu),
        # mais le mur le classe (category pkg_aa). pire_severite_mur doit
        # donc le voir : c'est la raison d'etre de cette fonction.
        routes = [{"name": "#P3#Parking Sud", "time": 3000, "historicTime": 500}]
        assert trafic_etat.agreger_terrains(routes) == []
        assert trafic_etat.pire_severite_mur(routes) == 4

    def test_axes_entree_sortie_seuls_coincide_avec_l_agregation_a_un_axe(self):
        # Cas nominal : quand chaque terrain n'a qu'une route, agregation et
        # classement individuel donnent la meme severite.
        routes = [{"name": "#I# Ouest", "time": 1080, "historicTime": 600}]
        terrains = trafic_etat.agreger_terrains(routes)
        assert trafic_etat.pire_severite_mur(routes) == \
            trafic_etat.severite_axe(terrains[0])

    def test_axe_non_balise_est_ignore(self):
        routes = [{"name": "Fresne -> Leroy Merlin", "time": 5000,
                   "historicTime": 100}]
        assert trafic_etat.pire_severite_mur(routes) == 0

    def test_aucune_route_rend_zero(self):
        assert trafic_etat.pire_severite_mur([]) == 0
        assert trafic_etat.pire_severite_mur(None) == 0


class TestCompterAlertes:
    def test_ne_compte_que_ce_qui_est_en_zone(self):
        alertes = [
            {"type": "ACCIDENT", "location": {"y": trafic_etat.ZONE_CENTER_LAT,
                                              "x": trafic_etat.ZONE_CENTER_LON}},
            {"type": "ACCIDENT", "location": {"y": trafic_etat.ZONE_CENTER_LAT + 0.5,
                                              "x": trafic_etat.ZONE_CENTER_LON}},
        ]
        assert trafic_etat.compter_alertes(alertes)["ACCIDENT"] == 1

    def test_type_inconnu_ignore(self):
        alertes = [{"type": "ROAD_CLOSED",
                    "location": {"y": trafic_etat.ZONE_CENTER_LAT,
                                 "x": trafic_etat.ZONE_CENTER_LON}}]
        comptes = trafic_etat.compter_alertes(alertes)
        assert comptes["total"] == 0

    def test_traffic_jam_est_compte_comme_jam(self):
        # Miroir de normType() cote mur (circulation.html:486-491) : le
        # type Waze brut est TRAFFIC_JAM, pas JAM. Sans l'alias, le mur le
        # compte (il normalise avant de tester) et la montre le jette --
        # elle affiche alors structurellement moins d'alertes que le mur
        # pour le meme flux.
        alertes = [{"type": "TRAFFIC_JAM",
                    "location": {"y": trafic_etat.ZONE_CENTER_LAT,
                                 "x": trafic_etat.ZONE_CENTER_LON}}]
        comptes = trafic_etat.compter_alertes(alertes)
        assert comptes["JAM"] == 1
        assert comptes["total"] == 1

    def test_weatherhazard_est_compte_comme_hazard(self):
        alertes = [{"type": "WEATHERHAZARD",
                    "location": {"y": trafic_etat.ZONE_CENTER_LAT,
                                 "x": trafic_etat.ZONE_CENTER_LON}}]
        comptes = trafic_etat.compter_alertes(alertes)
        assert comptes["HAZARD"] == 1
        assert comptes["total"] == 1

    def test_alias_insensible_a_la_casse(self):
        # Le type Waze brut est deja passe en .upper() avant l'alias : une
        # alerte qui arriverait en casse mixte doit se comporter pareil.
        alertes = [{"type": "traffic_jam",
                    "location": {"y": trafic_etat.ZONE_CENTER_LAT,
                                 "x": trafic_etat.ZONE_CENTER_LON}}]
        comptes = trafic_etat.compter_alertes(alertes)
        assert comptes["JAM"] == 1


class TestFraicheurAlertes:
    def test_rend_le_fetched_at_du_dernier_releve(self):
        moment = datetime(2026, 8, 15, 11, 40)
        db = FakeDb(waze_alerts=[{"fetched_at": moment, "data": []}])
        assert trafic_etat.fraicheur_alertes(db) == moment

    def test_none_sans_document(self):
        assert trafic_etat.fraicheur_alertes(FakeDb()) is None
