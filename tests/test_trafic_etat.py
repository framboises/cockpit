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
