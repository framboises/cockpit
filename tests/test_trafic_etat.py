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


class TestAxesMur:
    def test_un_axe_par_itineraire_sans_agregation(self):
        # agreger_terrains sommerait ces deux troncons en un seul terrain
        # "Ouest" ; le mur en affiche deux lignes, axes_mur aussi.
        routes = [
            {"name": "#I# Ouest", "time": 120, "historicTime": 100},
            {"name": "#I2# Ouest", "time": 900, "historicTime": 300},
        ]
        assert len(trafic_etat.agreger_terrains(routes)) == 1
        axes = trafic_etat.axes_mur(routes)
        assert len(axes) == 2
        assert sorted(a["nom"] for a in axes) == ["Ouest", "Ouest 2"]

    def test_severite_calculee_itineraire_par_itineraire(self):
        # 900s contre 300s d'historique : ratio 3, retard 600s -> severite 4
        # sur CE troncon. Agrege avec l'autre (1020 contre 400), le ratio
        # tombe a 2,55 et la severite a 3 -- chiffre que le mur n'affiche
        # nulle part.
        routes = [
            {"name": "#I# Ouest", "time": 120, "historicTime": 100},
            {"name": "#I2# Ouest", "time": 900, "historicTime": 300},
        ]
        axes = {a["nom"]: a["severity"] for a in trafic_etat.axes_mur(routes)}
        assert axes["Ouest 2"] == 4
        assert axes["Ouest"] == 0

    def test_parkings_inclus_et_marques(self):
        routes = [{"name": "#P3#Parking Sud", "time": 3000,
                   "historicTime": 500}]
        axes = trafic_etat.axes_mur(routes)
        assert len(axes) == 1
        assert axes[0]["parking"] is True
        assert axes[0]["nom"] == "Parking Sud 3"

    def test_axe_non_balise_est_ignore(self):
        routes = [{"name": "Fresne -> Leroy Merlin", "time": 5000,
                   "historicTime": 100},
                  {"name": "** SDIS -> Sud", "time": 500, "historicTime": 100}]
        assert trafic_etat.axes_mur(routes) == []

    def test_meme_ensemble_que_pire_severite_mur(self):
        # Les deux doivent porter sur exactement les memes axes : un verdict
        # calcule sur un ensemble plus large que la liste affichee produit un
        # << CRITIQUE >> sans cause visible.
        routes = [
            {"name": "#I# Ouest", "time": 120, "historicTime": 100},
            {"name": "#P3#Parking Sud", "time": 3000, "historicTime": 500},
            {"name": "Fresne -> Leroy Merlin", "time": 5000, "historicTime": 1},
        ]
        axes = trafic_etat.axes_mur(routes)
        assert trafic_etat.pire_severite_mur(routes) == \
            max(a["severity"] for a in axes)


class TestDistanceEtRattachement:
    # Un degre de latitude vaut ~110,54 km : 0,001 degre = ~110,5 m. Toutes
    # les distances attendues ci-dessous se verifient a la main sur cette
    # seule constante, sans rejouer le calcul teste.
    LAT = trafic_etat.ZONE_CENTER_LAT
    LON = trafic_etat.ZONE_CENTER_LON

    def _axe(self, nom, lat, lon, demi=0.01):
        return {"name": nom, "time": 100, "historicTime": 100,
                "line": [{"x": lon, "y": lat - demi},
                         {"x": lon, "y": lat + demi}]}

    def test_point_sur_le_segment_est_a_zero(self):
        p1 = {"x": self.LON, "y": self.LAT - 0.01}
        p2 = {"x": self.LON, "y": self.LAT + 0.01}
        d = trafic_etat.distance_point_segment_m(self.LAT, self.LON, p1, p2)
        assert d < 1.0

    def test_distance_perpendiculaire_au_segment(self):
        # Point decale de 0,001 degre de latitude = ~110,5 m d'un segment
        # est-ouest.
        p1 = {"x": self.LON - 0.01, "y": self.LAT}
        p2 = {"x": self.LON + 0.01, "y": self.LAT}
        d = trafic_etat.distance_point_segment_m(self.LAT + 0.001, self.LON,
                                                 p1, p2)
        assert 108 < d < 113

    def test_distance_bornee_a_l_extremite_du_segment(self):
        # Point au-dela du bout du segment : la distance est celle du bout,
        # pas celle de la droite prolongee (qui vaudrait zero ici).
        p1 = {"x": self.LON, "y": self.LAT}
        p2 = {"x": self.LON, "y": self.LAT + 0.001}
        d = trafic_etat.distance_point_segment_m(self.LAT + 0.003, self.LON,
                                                 p1, p2)
        assert 215 < d < 225      # ~2 x 110,5 m

    def test_axe_sans_geometrie_ne_capte_rien(self):
        # Un axe sans polyligne ne doit surtout pas devenir le plus proche
        # par defaut : il rend None, pas zero.
        assert trafic_etat.distance_point_axe_m(self.LAT, self.LON,
                                                {"line": []}) is None
        assert trafic_etat.distance_point_axe_m(self.LAT, self.LON, {}) is None

    def test_alerte_rattachee_a_l_axe_le_plus_proche_seulement(self):
        axes = trafic_etat.axes_mur([
            self._axe("#I# Proche", self.LAT, self.LON),
            self._axe("#I# Autre", self.LAT, self.LON + 0.0005),
        ])
        trafic_etat.rattacher_alertes(axes, [
            {"type": "ACCIDENT", "location": {"y": self.LAT, "x": self.LON}},
        ])
        marques = {a["nom"]: a["alertes"]["ACCIDENT"] for a in axes}
        # Les deux axes sont a moins de 250 m, mais l'alerte n'est comptee
        # qu'une fois, sur le plus proche.
        assert marques == {"Proche": 1, "Autre": 0}

    def test_le_seuil_encadre_le_vide_mesure(self):
        # CE test est celui qui fixe la VALEUR du seuil ; les autres se
        # contentent de verifier qu'il existe. Trouve par sabotage : ramener
        # DISTANCE_RATTACHEMENT_M de 250 a 20 ne faisait tomber aucun test.
        #
        # Les deux bornes sont les cas reels mesures sur le releve de prod du
        # 31/03/2026 : le bouchon D338 est a 151 m de l'axe Antares Sud (il
        # DOIT se rattacher, c'est le vrai rattachement le plus lointain
        # observe), la fermeture Rue Marcel Cerdan est a 793 m de tout axe
        # (elle ne DOIT pas, c'est le faux le plus proche observe).
        axes = trafic_etat.axes_mur([self._axe("#I# Axe", self.LAT, self.LON)])
        proche = 151.0 / 110540.0      # ~151 m au nord du bout du segment
        loin = 793.0 / 110540.0        # ~793 m
        trafic_etat.rattacher_alertes(axes, [
            {"type": "ACCIDENT",
             "location": {"y": self.LAT + 0.01 + proche, "x": self.LON}},
        ])
        assert axes[0]["alertes"]["ACCIDENT"] == 1, \
            "un vrai rattachement mesure a 151 m doit passer"
        trafic_etat.rattacher_alertes(axes, [
            {"type": "ACCIDENT",
             "location": {"y": self.LAT + 0.01 + loin, "x": self.LON}},
        ])
        assert axes[0]["alertes"]["ACCIDENT"] == 0, \
            "un faux rattachement mesure a 793 m doit etre ecarte"

    def test_alerte_au_dela_du_seuil_n_est_rattachee_a_rien(self):
        axes = trafic_etat.axes_mur([self._axe("#I# Axe", self.LAT, self.LON)])
        trafic_etat.rattacher_alertes(axes, [
            # ~331 m au nord du bout du segment : au-dela des 250 m.
            {"type": "ACCIDENT",
             "location": {"y": self.LAT + 0.013, "x": self.LON}},
        ])
        assert axes[0]["alertes"]["ACCIDENT"] == 0

    def test_alerte_hors_geofence_ignoree_meme_collee_a_un_axe(self):
        # Le geofence prime : un axe qui sortirait du cercle ne doit pas
        # ramener des alertes que le bilan ne compte pas.
        loin_lat = self.LAT + 0.5
        axes = trafic_etat.axes_mur([self._axe("#I# Loin", loin_lat, self.LON)])
        trafic_etat.rattacher_alertes(axes, [
            {"type": "ACCIDENT", "location": {"y": loin_lat, "x": self.LON}},
        ])
        assert axes[0]["alertes"]["ACCIDENT"] == 0

    def test_types_non_comptes_ne_marquent_aucun_axe(self):
        # ROAD_CLOSED est volontairement hors TYPES_COMPTES (circulation
        # .html:479) : autour du circuit, les fermetures sont l'etat nominal
        # et le flux en porte des signalements vieux de plusieurs mois.
        axes = trafic_etat.axes_mur([self._axe("#I# Axe", self.LAT, self.LON)])
        trafic_etat.rattacher_alertes(axes, [
            {"type": "ROAD_CLOSED",
             "location": {"y": self.LAT, "x": self.LON}},
        ])
        assert axes[0]["alertes"] == {"ACCIDENT": 0, "JAM": 0, "HAZARD": 0}

    def test_alias_de_type_pris_en_compte(self):
        axes = trafic_etat.axes_mur([self._axe("#I# Axe", self.LAT, self.LON)])
        trafic_etat.rattacher_alertes(axes, [
            {"type": "TRAFFIC_JAM", "location": {"y": self.LAT, "x": self.LON}},
            {"type": "WEATHERHAZARD",
             "location": {"y": self.LAT, "x": self.LON}},
        ])
        assert axes[0]["alertes"]["JAM"] == 1
        assert axes[0]["alertes"]["HAZARD"] == 1

    def test_alerte_sans_coordonnees_ne_plante_pas(self):
        axes = trafic_etat.axes_mur([self._axe("#I# Axe", self.LAT, self.LON)])
        trafic_etat.rattacher_alertes(axes, [{"type": "ACCIDENT"}, None,
                                             {"type": "ACCIDENT",
                                              "location": {}}])
        assert axes[0]["alertes"]["ACCIDENT"] == 0

    def test_chaque_axe_recoit_son_compteur_meme_sans_alerte(self):
        # Sans initialisation, la vue devrait distinguer un axe sans
        # compteur d'un axe a zero -- deux facons de dire la meme chose.
        axes = trafic_etat.axes_mur([self._axe("#I# Axe", self.LAT, self.LON)])
        trafic_etat.rattacher_alertes(axes, [])
        assert axes[0]["alertes"] == {"ACCIDENT": 0, "JAM": 0, "HAZARD": 0}


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
