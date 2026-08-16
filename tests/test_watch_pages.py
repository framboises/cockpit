from datetime import datetime, timedelta, timezone

from conftest import FakeDb

import watch_pages

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _fiche(categorie, close):
    return {"event": "24H MOTOS", "year": 2026, "category": categorie,
            "status_code": 10 if close else 2}


class TestMainCourante:
    def _db(self):
        return FakeDb(pcorg=[
            _fiche("PCO.Secours", False), _fiche("PCO.Secours", False),
            _fiche("PCO.Secours", True),
            _fiche("PCO.Technique", False),
            _fiche("PCO.Flux", True),
            _fiche("PCO.Information", False),
            _fiche("PCO.Fourriere", True),
        ])

    def test_paires_en_cours_terminees(self):
        bloc = watch_pages.build_main_courante(self._db(), "24H MOTOS", 2026, NOW)
        assert bloc["s"] == [2, 1]     # secours
        assert bloc["sc"] == [0, 0]    # securite
        assert bloc["tq"] == [1, 0]    # technique
        assert bloc["f"] == [0, 1]     # flux

    def test_les_trois_autres_categories_sont_repliees(self):
        # Information, MainCourante et Fourriere ne sont pas jetees : les
        # taire ferait disparaitre des fiches reelles de l'ecran.
        bloc = watch_pages.build_main_courante(self._db(), "24H MOTOS", 2026, NOW)
        assert bloc["o"] == [1, 1]

    def test_autre_evenement_ignore(self):
        db = FakeDb(pcorg=[
            {"event": "GPF", "year": 2026, "category": "PCO.Secours",
             "status_code": 2},
        ])
        bloc = watch_pages.build_main_courante(db, "24H MOTOS", 2026, NOW)
        assert bloc["s"] == [0, 0]

    def test_sans_evenement_rend_none(self):
        assert watch_pages.build_main_courante(FakeDb(), None, None, NOW) is None


class TestTrafic:
    def _db(self, routes, alertes):
        return FakeDb(
            waze_trafic=[{"fetched_at": datetime(2026, 8, 15, 11, 59),
                          "data": {"routes": routes}}],
            waze_alerts=[{"fetched_at": datetime(2026, 8, 15, 11, 59),
                          "data": alertes}],
        )

    def test_tous_les_axes_sont_rendus_le_plus_charge_en_tete(self):
        # La liste ne se limite plus a quatre entrees : la page trafic les
        # pagine par six. Six axes rentrent donc tous, tries du plus charge
        # au moins charge -- la montre montre d'abord ce qui coince.
        routes = [{"name": "#I# T%d" % i, "time": 100 * (i + 1),
                   "historicTime": 100} for i in range(6)]
        bloc = watch_pages.build_trafic(self._db(routes, []), NOW)
        assert len(bloc["r"]) == 6
        assert bloc["r"][0][0] == "T5"
        assert [ligne[0] for ligne in bloc["r"]] == [
            "T5", "T4", "T3", "T2", "T1", "T0"]

    def test_le_plafond_d_axes_est_borne(self):
        # Filet contre une reconfiguration cote operateur : aucun releve
        # observe n'en a plus de 13, mais le payload ne doit pas pouvoir
        # enfler en silence.
        routes = [{"name": "#I# T%02d" % i, "time": 100 + i,
                   "historicTime": 100} for i in range(watch_pages.MAX_AXES + 7)]
        bloc = watch_pages.build_trafic(self._db(routes, []), NOW)
        assert len(bloc["r"]) == watch_pages.MAX_AXES

    def test_deux_itineraires_d_un_meme_terrain_restent_distincts(self):
        # `#I# Ouest` et `#I2# Ouest` coexistent en base : ce sont deux
        # itineraires differents, que le mur affiche sur deux lignes. Les
        # sommer (ancien comportement, via agreger_terrains) affichait une
        # severite que le mur ne montrait nulle part.
        routes = [
            {"name": "#I# Ouest", "time": 120, "historicTime": 100},
            {"name": "#I2# Ouest", "time": 900, "historicTime": 300},
        ]
        bloc = watch_pages.build_trafic(self._db(routes, []), NOW)
        assert len(bloc["r"]) == 2
        noms = [ligne[0] for ligne in bloc["r"]]
        assert noms == ["Ouest 2", "Ouest"]

    def test_temps_converti_en_minutes(self):
        # currentTime est en SECONDES cote Waze.
        routes = [{"name": "#I# Ouest", "time": 1080, "historicTime": 600}]
        bloc = watch_pages.build_trafic(self._db(routes, []), NOW)
        assert bloc["r"][0][2] == 18

    def test_verdict_et_comptes_geofences(self):
        import trafic_etat
        dedans = {"type": "ACCIDENT",
                  "location": {"y": trafic_etat.ZONE_CENTER_LAT,
                               "x": trafic_etat.ZONE_CENTER_LON}}
        dehors = {"type": "ACCIDENT",
                  "location": {"y": trafic_etat.ZONE_CENTER_LAT + 0.5,
                               "x": trafic_etat.ZONE_CENTER_LON}}
        bloc = watch_pages.build_trafic(self._db([], [dedans, dehors]), NOW)
        assert bloc["ac"] == 1
        assert bloc["vd"] == 3

    def test_sans_donnee_waze_rend_none(self):
        assert watch_pages.build_trafic(FakeDb(), NOW) is None

    def test_severite_recalculee_au_double_verrou_pas_au_ratio_seul(self):
        # Cas divergent trouve a la tache 14 : un axe unique, ratio 1,5,
        # 500s de retard. L'ancien calcul (classify_congestion, ratio seul)
        # aurait rendu severite 2 -> vd VIGILANCE, alors que le mur
        # (double verrou) reste severite 1 -> vd FLUIDE. build_trafic doit
        # desormais rendre le verdict du mur, pas celui du ratio seul.
        routes = [{"name": "#I# Ouest", "time": 1500, "historicTime": 1000}]
        bloc = watch_pages.build_trafic(self._db(routes, []), NOW)
        assert bloc["r"][0][3] == 1  # severite affichee, pas 2
        assert bloc["vd"] == 0       # FLUIDE, pas VIGILANCE

    def test_troncon_court_ne_declenche_pas_de_severite(self):
        # 15s -> 45s : ratio x3 mais 30s de retard seulement, sous le
        # plancher du mur. classify_congestion l'aurait classe "bouchon".
        routes = [{"name": "#I# Court", "time": 45, "historicTime": 15}]
        bloc = watch_pages.build_trafic(self._db(routes, []), NOW)
        assert bloc["r"][0][3] == 0
        assert bloc["vd"] == 0

    def test_parking_bouchonne_fait_monter_le_verdict_ET_apparait_en_liste(self):
        # La tache 14 avait corrige la moitie du probleme : le verdict voyait
        # le parking (via pire_severite_mur) mais la liste ne le montrait pas
        # (agreger_terrains exclut le prefixe #P). La montre pouvait donc
        # afficher CRITIQUE sans qu'aucune ligne ne l'explique -- compromis
        # assume a l'epoque, supprime maintenant que la page pagine ses axes.
        # Verdict et liste portent desormais sur le MEME ensemble.
        routes = [{"name": "#P3#Parking Sud", "time": 3000, "historicTime": 500}]
        bloc = watch_pages.build_trafic(self._db(routes, []), NOW)
        assert bloc["vd"] == 3                 # CRITIQUE
        assert len(bloc["r"]) == 1             # et la cause est listee
        assert bloc["r"][0][0] == "Parking Sud 3"
        assert bloc["r"][0][1] == "p"          # marque comme parking
        assert bloc["r"][0][3] == 4            # severite du mur

    # --- Rattachement des alertes aux axes ---------------------------
    #
    # Sans lui, la page dit << un accident dans le cercle >> sans dire ou.
    # Le seuil (trafic_etat.DISTANCE_RATTACHEMENT_M) est pose au milieu d'un
    # vide mesure sur les deux relevés Waze disponibles en base.

    def _route_droite(self, nom, lat, lon, longueur_deg=0.01):
        # Segment nord-sud passant par (lat, lon).
        return {"name": nom, "time": 100, "historicTime": 100,
                "line": [{"x": lon, "y": lat - longueur_deg},
                         {"x": lon, "y": lat + longueur_deg}]}

    def test_alerte_posee_sur_un_axe_le_marque(self):
        import trafic_etat
        lat = trafic_etat.ZONE_CENTER_LAT
        lon = trafic_etat.ZONE_CENTER_LON
        routes = [self._route_droite("#I# Sur", lat, lon),
                  self._route_droite("#I# Loin", lat, lon + 0.05)]
        accident = {"type": "ACCIDENT", "location": {"y": lat, "x": lon}}
        bloc = watch_pages.build_trafic(self._db(routes, [accident]), NOW)
        lignes = {ligne[0]: ligne[4] for ligne in bloc["r"]}
        assert lignes["Sur"] == watch_pages.FL_ACCIDENT
        assert lignes["Loin"] == 0

    def test_alerte_trop_loin_de_tout_axe_ne_marque_personne(self):
        # Elle compte quand meme dans le bilan : elle est dans le cercle,
        # elle n'accuse simplement aucun axe surveille.
        import trafic_etat
        lat = trafic_etat.ZONE_CENTER_LAT
        lon = trafic_etat.ZONE_CENTER_LON
        routes = [self._route_droite("#I# Axe", lat, lon)]
        # ~1,1 km a l'est, tres au-dela des 250 m du seuil.
        loin = {"type": "ACCIDENT", "location": {"y": lat, "x": lon + 0.015}}
        bloc = watch_pages.build_trafic(self._db(routes, [loin]), NOW)
        assert bloc["ac"] == 1
        assert bloc["r"][0][4] == 0

    def test_drapeaux_combines_sur_un_meme_axe(self):
        import trafic_etat
        lat = trafic_etat.ZONE_CENTER_LAT
        lon = trafic_etat.ZONE_CENTER_LON
        routes = [self._route_droite("#I# Axe", lat, lon)]
        alertes = [
            {"type": "ACCIDENT", "location": {"y": lat, "x": lon}},
            {"type": "TRAFFIC_JAM", "location": {"y": lat + 0.0005, "x": lon}},
        ]
        bloc = watch_pages.build_trafic(self._db(routes, alertes), NOW)
        assert bloc["r"][0][4] == watch_pages.FL_ACCIDENT | watch_pages.FL_BOUCHON

    def test_accident_fait_remonter_un_axe_fluide_en_tete(self):
        # Un accident qui vient de tomber sur un axe encore fluide passe
        # devant un axe deja bouchonne : c'est sur lui qu'on engage des
        # moyens, il ne doit pas se retrouver en troisieme sous-page.
        import trafic_etat
        lat = trafic_etat.ZONE_CENTER_LAT
        lon = trafic_etat.ZONE_CENTER_LON
        fluide = self._route_droite("#I# Fluide", lat, lon)
        bouche = self._route_droite("#I# Bouche", lat, lon + 0.05)
        bouche["time"] = 3000
        bouche["historicTime"] = 500
        accident = {"type": "ACCIDENT", "location": {"y": lat, "x": lon}}
        bloc = watch_pages.build_trafic(self._db([fluide, bouche], [accident]),
                                        NOW)
        assert bloc["r"][0][0] == "Fluide"
        assert bloc["r"][0][3] == 0      # bien fluide
        assert bloc["r"][1][0] == "Bouche"

    def test_comptes_bouchons_et_dangers_exposes(self):
        import trafic_etat
        lat = trafic_etat.ZONE_CENTER_LAT
        lon = trafic_etat.ZONE_CENTER_LON
        alertes = [
            {"type": "TRAFFIC_JAM", "location": {"y": lat, "x": lon}},
            {"type": "WEATHERHAZARD", "location": {"y": lat, "x": lon}},
            {"type": "HAZARD", "location": {"y": lat, "x": lon}},
        ]
        bloc = watch_pages.build_trafic(self._db([], alertes), NOW)
        assert bloc["jm"] == 1
        assert bloc["hz"] == 2      # WEATHERHAZARD est un alias de HAZARD
        assert bloc["ac"] == 0
        assert bloc["z"] == 3

    def test_alertes_perimees_rendent_les_drapeaux_a_none_pas_a_zero(self):
        # Meme regle que ac/z/vd : un drapeau a 0 se lit << rien sur cet
        # axe >>, ce qui serait un mensonge quand la source est muette.
        db = FakeDb(
            waze_trafic=[{"fetched_at": datetime(2026, 8, 15, 11, 59),
                          "data": {"routes": [
                              {"name": "#I# Ouest", "time": 100,
                               "historicTime": 100}]}}],
            waze_alerts=[{"fetched_at": datetime(2026, 8, 15, 11, 40),
                          "data": []}],
        )
        bloc = watch_pages.build_trafic(db, NOW)
        assert bloc["r"][0][4] is None
        assert bloc["jm"] is None
        assert bloc["hz"] is None

    def test_cas_nominal_axes_entree_seuls_reste_inchange(self):
        # Sans aucun axe #P, le calcul global sur toutes les routes
        # coincide avec l'ancien calcul par terrains agreges (une seule
        # route par terrain dans ce jeu) : cette correction ne doit rien
        # changer au cas nominal.
        routes = [{"name": "#I# Ouest", "time": 1080, "historicTime": 600}]
        bloc = watch_pages.build_trafic(self._db(routes, []), NOW)
        assert bloc["r"][0][3] == 2  # ratio 1.8, retard 480s -> severite 2
        assert bloc["vd"] == 1       # VIGILANCE

    # --- Fraicheur separee des alertes (correction 1) ----------------
    #
    # ac/z/vd viennent de waze_alerts, une collection DIFFERENTE de
    # waze_trafic (qui date le reste du bloc via trafic_etat.fraicheur).
    # Un collecteur d'alertes arrete rendait avant cette correction ac=0,
    # z=0 et un verdict affirmatif, avec une date parfaitement fraiche --
    # la panne silencieuse que ce projet elimine partout ailleurs.

    def test_alertes_perimees_rendent_ac_z_vd_a_none(self):
        # waze_trafic frais (11:59), waze_alerts vieux de 20 min (11:40) --
        # au-dela d'ALERTES_MAX_AGE (300s). Sans la garde, ac/z tomberaient
        # a 0 et vd resterait affirmatif.
        db = FakeDb(
            waze_trafic=[{"fetched_at": datetime(2026, 8, 15, 11, 59),
                          "data": {"routes": [
                              {"name": "#I# Ouest", "time": 100,
                               "historicTime": 100}]}}],
            waze_alerts=[{"fetched_at": datetime(2026, 8, 15, 11, 40),
                          "data": []}],
        )
        bloc = watch_pages.build_trafic(db, NOW)
        assert bloc["ac"] is None
        assert bloc["z"] is None
        assert bloc["vd"] is None

    def test_alertes_absentes_rendent_aussi_ac_z_vd_a_none(self):
        # Aucun document waze_alerts du tout (collecteur jamais lance, ou
        # collection purgee) : meme regle que perime, ac/z/vd a None.
        db = FakeDb(
            waze_trafic=[{"fetched_at": datetime(2026, 8, 15, 11, 59),
                          "data": {"routes": [
                              {"name": "#I# Ouest", "time": 100,
                               "historicTime": 100}]}}],
        )
        bloc = watch_pages.build_trafic(db, NOW)
        assert bloc["ac"] is None
        assert bloc["z"] is None
        assert bloc["vd"] is None
        # Sans horodatage alertes, on retombe sur le seul horodatage connu
        # (routes) -- jamais None alors qu'une moitie du bloc EST datee.
        assert bloc["t"] == watch_pages._epoch(datetime(2026, 8, 15, 11, 59))

    def test_deux_flux_frais_ac_z_vd_restent_affirmes(self):
        # Comportement inchange quand routes ET alertes sont fraiches --
        # non-regression explicite de la garde ajoutee.
        db = FakeDb(
            waze_trafic=[{"fetched_at": datetime(2026, 8, 15, 11, 59),
                          "data": {"routes": [
                              {"name": "#I# Ouest", "time": 100,
                               "historicTime": 100}]}}],
            waze_alerts=[{"fetched_at": datetime(2026, 8, 15, 11, 58),
                          "data": []}],
        )
        bloc = watch_pages.build_trafic(db, NOW)
        assert bloc["ac"] == 0
        assert bloc["z"] == 0
        assert bloc["vd"] == 0

    def test_t_prend_le_plus_ancien_des_deux_horodatages(self):
        # routes plus ancien (11:55) que alertes (11:59) : `t` doit porter
        # le plus ancien des deux, pas le plus recent -- un bloc n'est
        # frais que si ses DEUX moities le sont.
        db = FakeDb(
            waze_trafic=[{"fetched_at": datetime(2026, 8, 15, 11, 55),
                          "data": {"routes": [
                              {"name": "#I# Ouest", "time": 100,
                               "historicTime": 100}]}}],
            waze_alerts=[{"fetched_at": datetime(2026, 8, 15, 11, 59),
                          "data": []}],
        )
        bloc = watch_pages.build_trafic(db, NOW)
        assert bloc["t"] == watch_pages._epoch(datetime(2026, 8, 15, 11, 55))

    def test_t_prend_le_plus_ancien_meme_quand_alertes_plus_ancien(self):
        # Symetrique : alertes plus ancien (11:50) que routes (11:59).
        db = FakeDb(
            waze_trafic=[{"fetched_at": datetime(2026, 8, 15, 11, 59),
                          "data": {"routes": [
                              {"name": "#I# Ouest", "time": 100,
                               "historicTime": 100}]}}],
            waze_alerts=[{"fetched_at": datetime(2026, 8, 15, 11, 50),
                          "data": []}],
        )
        bloc = watch_pages.build_trafic(db, NOW)
        assert bloc["t"] == watch_pages._epoch(datetime(2026, 8, 15, 11, 50))


class TestMeteo:
    def test_condense_l_etat_du_mur(self, monkeypatch):
        etat = {
            "actuel": {"temperature_c": 21.3, "vent_moyen_kmh": 18,
                       "vent_rafale_kmh": 34},
            "prochaine_pluie": {"attendue": True, "dans_min": 25,
                                "pic_mmh": 2.4},
            "consignes": [
                {"niveau": "vigilance", "texte": "Pluie 2 mm - parkings en herbe"},
                {"niveau": "danger", "texte": "Rafales 62 km/h - securiser les structures"},
            ],
            "vigilance": {"couleur_jour": "jaune", "couleur_max": "orange"},
        }
        monkeypatch.setattr(watch_pages.meteo_etat, "etat_mur",
                            lambda d, m: etat)
        bloc = watch_pages.build_meteo(FakeDb(), datetime(2026, 8, 15, 12, 0))
        assert bloc["tc"] == 21.3
        assert bloc["rf"] == 34
        assert bloc["pl"] == 25
        assert bloc["pm"] == 2.4
        # La consigne retenue est LA PLUS GRAVE, pas la premiere de la liste.
        assert bloc["cn"].startswith("Rafales 62")
        assert bloc["cl"] == 2

    def test_sans_pluie_attendue_les_champs_sont_nuls(self, monkeypatch):
        etat = {"actuel": {"temperature_c": 18.0},
                "prochaine_pluie": {"attendue": False},
                "consignes": [], "vigilance": None}
        monkeypatch.setattr(watch_pages.meteo_etat, "etat_mur",
                            lambda d, m: etat)
        bloc = watch_pages.build_meteo(FakeDb(), datetime(2026, 8, 15, 12, 0))
        assert bloc["pl"] is None
        assert bloc["pm"] is None
        assert bloc["cn"] is None

    def test_source_qui_leve_rend_none(self, monkeypatch):
        def casse(d, m):
            raise RuntimeError("piaf injoignable")
        monkeypatch.setattr(watch_pages.meteo_etat, "etat_mur", casse)
        assert watch_pages.build_meteo(FakeDb(), datetime(2026, 8, 15, 12, 0)) is None

    def test_vg_derive_de_la_couleur_jour_reelle(self):
        # etat_mur n'est PAS monkeypatche ici : c'est la vraie fonction qui
        # fusionne bulletin brut + etat_vigilance() + niveau_vigilance(), et
        # aucune des trois ne porte de cle "niveau". Un double fabrique
        # d'apres l'implementation (comme avant ce correctif) validerait sa
        # propre hypothese plutot que le vrai contrat de meteo_etat.
        #
        # niveau_vigilance()/etat_vigilance() sont appelees par etat_mur SANS
        # lui passer son propre `maintenant` -- elles retombent donc sur
        # l'horloge murale (datetime.now(timezone.utc)). Le bulletin doit
        # couvrir l'instant reel d'execution, pas une date figee, sous peine
        # de comparer un naif a un conscient (TypeError, constate en ecrivant
        # ce test). C'est un comportement de meteo_etat (tache 2), hors
        # perimetre de cette tache.
        agora = datetime.now(timezone.utc)
        bulletin = {
            "departement": "72",
            "update_time": agora.isoformat(),
            "periodes": [
                {"echeance": "J", "couleur_max": "orange",
                 "phenomenes": [{"nom": "Vent"}],
                 "debut": (agora - timedelta(hours=1)).isoformat(),
                 "fin": (agora + timedelta(hours=1)).isoformat()},
            ],
        }
        db = FakeDb(meteo_vigilance=[bulletin])
        bloc = watch_pages.build_meteo(db, agora)
        # "orange" est a l'indice 2 de meteo_etat.ORDRE_COULEURS.
        assert bloc["vg"] == 2

    def test_vg_a_zero_sans_bulletin(self):
        bloc = watch_pages.build_meteo(FakeDb(), datetime(2026, 8, 15, 12, 0))
        assert bloc["vg"] == 0

    def test_t_date_le_dernier_run_piaf_pas_l_instant_du_calcul(self):
        # Si `t` datait l'instant de l'appel, le bloc paraitrait eternellement
        # frais meme quand le flux se fige. run_at (chaine ISO) est le seul
        # horodatage reel qu'expose etat_mur.
        maintenant = datetime(2026, 8, 15, 12, 0)
        run_at = datetime(2026, 8, 15, 11, 30)
        grilles = [{"flux": "piaf", "run_at": run_at, "echeance_min": 30,
                    "valid_at": run_at, "max_mmh": 0.0}]
        db = FakeDb(meteo_grilles=grilles)
        bloc = watch_pages.build_meteo(db, maintenant)
        assert bloc["t"] == watch_pages._epoch(run_at)
        assert bloc["t"] != watch_pages._epoch(maintenant)

    def test_consignes_reelles_les_plus_longues_ne_sont_pas_tronquees(self, monkeypatch):
        # CONSIGNE_MAX (44) avait ete calibre pour UNE ligne, alors que
        # MeteoView.couperConsigne repartit desormais les caracteres
        # survivants sur DEUX lignes -- la capacite de la seconde etait
        # jetee avant que la montre ne voie le texte. Rejoue les deux pires
        # consignes REELLES du systeme (meteo_etat.py : foudre+grele,
        # meteo_thermique.py : WBGT danger) et verifie qu'elles survivent
        # entieres. Un CONSIGNE_MAX qui redescendrait a 44 (ou tout en
        # dessous de 59) ferait tomber ce test.
        foudre = "Foudre prevue sur la zone avec grele — mise a l'abri"
        wbgt = "WBGT 30.5 °C — Travail lourd a suspendre, rotations courtes"
        assert len(foudre) == 52
        assert len(wbgt) == 59

        for consigne_texte in (foudre, wbgt):
            etat = {
                "actuel": {"temperature_c": 20.0},
                "prochaine_pluie": {"attendue": False},
                "consignes": [{"niveau": "danger", "texte": consigne_texte}],
                "vigilance": None,
            }
            monkeypatch.setattr(watch_pages.meteo_etat, "etat_mur",
                                lambda d, m, etat=etat: etat)
            bloc = watch_pages.build_meteo(FakeDb(), datetime(2026, 8, 15, 12, 0))
            assert bloc["cn"] == consigne_texte

    def test_valeurs_entieres_sont_converties_en_flottant(self, monkeypatch):
        # meteo_previsions ne garantit pas le type : une vitesse de vent
        # entiere est parfaitement legitime cote source. MeteoView.mc
        # appelle .format("%.1f"/"%.0f") sur tc/v/rf/pm -- une methode qui
        # n'existe que sur Float en Monkey C. Tous les tests/mocks existants
        # fournissaient deja des flottants : c'est exactement le cas qu'aucun
        # d'eux n'aurait attrape.
        etat = {
            "actuel": {"temperature_c": 21, "vent_moyen_kmh": 18,
                       "vent_rafale_kmh": 34},
            "prochaine_pluie": {"attendue": True, "dans_min": 25,
                                "pic_mmh": 2},
            "consignes": [], "vigilance": None,
        }
        monkeypatch.setattr(watch_pages.meteo_etat, "etat_mur",
                            lambda d, m: etat)
        bloc = watch_pages.build_meteo(FakeDb(), datetime(2026, 8, 15, 12, 0))
        assert isinstance(bloc["tc"], float)
        assert isinstance(bloc["v"], float)
        assert isinstance(bloc["rf"], float)
        assert isinstance(bloc["pm"], float)
        assert bloc["tc"] == 21.0
        assert bloc["pm"] == 2.0

    def test_t_est_none_sans_run_piaf(self):
        # Pas de donnee PIAF (cas reel en base dev) : `t` doit dire "je ne
        # sais pas dater", pas mentir en affichant l'instant du calcul.
        bloc = watch_pages.build_meteo(FakeDb(), datetime(2026, 8, 15, 12, 0))
        assert bloc["t"] is None


class TestFrequentation:
    def test_lit_le_compteur_principal_choisi_par_lexploitation(self, monkeypatch):
        # La page 1 de la montre lit deja le compteur PRINCIPAL choisi dans
        # le live-controle (watch_state.read_principal_id), pas un id fige.
        # Si cette page-ci restait sur "628" en dur, le jour ou
        # l'exploitation bascule le principal (panne d'un boitier, secours),
        # les deux pages de la meme montre liraient deux appareils physiques
        # differents pour le meme instant, sans que rien ne le signale. `pj`
        # et `n1` doivent en plus venir du MEME compteur : ce sont deux
        # chiffres compares cote a cote.
        locs_interrogees = []

        def faux_max(db, coll, jour, loc, event=None):
            locs_interrogees.append(loc)
            return (52100, "14h15")

        monkeypatch.setattr(watch_pages.pcorg_summary,
                            "_max_current_in_snapshots", faux_max)
        monkeypatch.setattr(watch_pages.watch_peaks, "resolve_race_dt",
                            lambda db, ev, an: None)

        db = FakeDb(data_access=[
            {"_id": "___GLOBAL___", "compteur_principal_id": "999"},
        ])
        bloc = watch_pages.build_frequentation(
            db, "24H MOTOS", 2026, datetime(2026, 4, 17, 12, 0))

        assert bloc["pj"] == 52100
        # Le compteur choisi par l'exploitation ("999"), jamais "628" en dur.
        assert locs_interrogees == ["999"]

    def test_repli_sur_le_compteur_par_defaut_sans_principal_configure(self, monkeypatch):
        # Base sans document global (ou sans compteur_principal_id) : repli
        # sur watch_peaks.DEFAULT_LOCATION_ID, le comportement actuel.
        locs_interrogees = []

        def faux_max(db, coll, jour, loc, event=None):
            locs_interrogees.append(loc)
            return (52100, "14h15")

        monkeypatch.setattr(watch_pages.pcorg_summary,
                            "_max_current_in_snapshots", faux_max)
        monkeypatch.setattr(watch_pages.watch_peaks, "resolve_race_dt",
                            lambda db, ev, an: None)

        bloc = watch_pages.build_frequentation(
            FakeDb(), "24H MOTOS", 2026, datetime(2026, 4, 17, 12, 0))

        assert bloc["pj"] == 52100
        assert locs_interrogees == [watch_pages.watch_peaks.DEFAULT_LOCATION_ID]

    def test_pic_du_jour_et_n1_au_jour_equivalent(self, monkeypatch):
        # On isole le calcul de l'acces Mongo : ce qui est teste ici, c'est
        # l'ALIGNEMENT au jour de course, pas la lecture des snapshots.
        #
        # La course 2026 et la course 2025 sont volontairement placees a
        # deux dates calendaires DIFFERENTES (18 avril / 19 avril, meme
        # samedi) -- exactement le cas reel des 24H MOTOS (cf. rapport,
        # verifie sur la base dev : samedi 18/04/2026, samedi 19/04/2025).
        # Avec deux courses au meme jour du mois, un alignement naif << meme
        # date calendaire l'annee d'avant >> coinciderait par accident avec
        # le bon alignement par decalage, et le sabotage de l'etape 6 ne
        # ferait tomber aucun test -- c'est tautologique.
        appels = []

        def faux_max(db, coll, jour, loc, event=None):
            appels.append(jour)
            return (52100, "14h15") if len(appels) == 1 else (49800, "15h02")

        monkeypatch.setattr(watch_pages.pcorg_summary,
                            "_max_current_in_snapshots", faux_max)

        def fausse_course(db, ev, an):
            return {2026: datetime(2026, 4, 18, 13, 0, tzinfo=timezone.utc),
                    2025: datetime(2025, 4, 19, 13, 0, tzinfo=timezone.utc)}[an]

        monkeypatch.setattr(watch_pages.watch_peaks, "resolve_race_dt",
                            fausse_course)

        bloc = watch_pages.build_frequentation(
            FakeDb(), "24H MOTOS", 2026,
            datetime(2026, 4, 17, 12, 0), location_id="628")

        assert bloc["pj"] == 52100
        assert bloc["ph"] == "14h15"
        assert bloc["n1"] == 49800
        # jour = veille de la course 2026 (17/04, J-1). Le jour equivalent
        # N-1 est donc la veille de la course 2025 (19/04 - 1 = 18/04), PAS
        # le 17 avril 2025 (meme date calendaire que 2026, mais J-2 par
        # rapport a la course 2025).
        assert appels[1] == datetime(2025, 4, 19).date() - timedelta(days=1)

    def test_n1_recale_sur_le_jour_de_semaine_de_lannee_courante(self, monkeypatch):
        # globalHoraires.race porte tantot le depart, tantot l'arrivee selon
        # le millesime (documente dans CLAUDE.md) : ici la course 2026 tombe
        # un samedi, la course 2025 (telle que resolve_race_dt la rend, sans
        # normalisation) un dimanche -- meme evenement, jour de semaine
        # different. Sans recalage, l'alignement comparerait le samedi de
        # course N a l'apres-course de N-1, un decalage d'un jour qui reste
        # plausible a l'oeil et donc invisible sans ce test.
        appels = []

        def faux_max(db, coll, jour, loc, event=None):
            appels.append(jour)
            return (52100, "14h15") if len(appels) == 1 else (40077, "15h07")

        monkeypatch.setattr(watch_pages.pcorg_summary,
                            "_max_current_in_snapshots", faux_max)

        def fausse_course(db, ev, an):
            return {2026: datetime(2026, 4, 18, 13, 0, tzinfo=timezone.utc),  # samedi
                    2025: datetime(2025, 4, 20, 13, 0, tzinfo=timezone.utc)}[an]  # dimanche

        monkeypatch.setattr(watch_pages.watch_peaks, "resolve_race_dt",
                            fausse_course)

        bloc = watch_pages.build_frequentation(
            FakeDb(), "24H MOTOS", 2026,
            datetime(2026, 4, 18, 20, 0), location_id="628")

        assert bloc["n1"] == 40077
        # Le jour de course 2025 (dimanche 20/04) doit etre recale sur le
        # samedi (jour de semaine de la course 2026) AVANT d'appliquer le
        # decalage a la course : 20/04 - 1 jour = 19/04, jamais le 20/04
        # tel quel.
        assert appels[1] == datetime(2025, 4, 19).date()

    def test_sans_date_de_course_pas_de_n1(self, monkeypatch):
        monkeypatch.setattr(watch_pages.watch_peaks, "resolve_race_dt",
                            lambda db, ev, an: None)
        monkeypatch.setattr(watch_pages.pcorg_summary,
                            "_max_current_in_snapshots",
                            lambda *a, **k: (52100, "14h15"))
        bloc = watch_pages.build_frequentation(
            FakeDb(), "24H MOTOS", 2026, datetime(2026, 4, 17, 12, 0))
        assert bloc["pj"] == 52100
        assert bloc["n1"] is None

    def test_sans_evenement_rend_none(self):
        assert watch_pages.build_frequentation(
            FakeDb(), None, None, datetime(2026, 4, 17, 12, 0)) is None

    def test_t_date_le_releve_du_pic_pas_l_instant_du_calcul(self, monkeypatch):
        # `_max_current_in_snapshots` ne rend que l'heure Paris formattee du
        # releve qui a produit le pic ("14h15") -- c'est cette heure-la, pas
        # `now`, qui doit dater le bloc. Si le compteur se figeait a 14h15,
        # `t` doit rester fige a 14h15 lui aussi.
        monkeypatch.setattr(watch_pages.watch_peaks, "resolve_race_dt",
                            lambda db, ev, an: None)
        monkeypatch.setattr(watch_pages.pcorg_summary,
                            "_max_current_in_snapshots",
                            lambda *a, **k: (52100, "14h15"))
        maintenant = datetime(2026, 4, 17, 20, 0)
        bloc = watch_pages.build_frequentation(
            FakeDb(), "24H MOTOS", 2026, maintenant)
        attendu = datetime(2026, 4, 17, 14, 15, tzinfo=watch_pages.pcorg_summary.TZ_PARIS)
        assert bloc["t"] == watch_pages._epoch(attendu)
        assert bloc["t"] != watch_pages._epoch(maintenant)

    def test_sans_pic_du_jour_t_est_none(self, monkeypatch):
        # Aucun releve aujourd'hui : on ne sait pas dater, `t` doit le dire
        # plutot que de mentir sur une fraicheur inventee.
        monkeypatch.setattr(watch_pages.watch_peaks, "resolve_race_dt",
                            lambda db, ev, an: None)
        monkeypatch.setattr(watch_pages.pcorg_summary,
                            "_max_current_in_snapshots",
                            lambda *a, **k: (None, None))
        bloc = watch_pages.build_frequentation(
            FakeDb(), "24H MOTOS", 2026, datetime(2026, 4, 17, 12, 0))
        assert bloc["pj"] is None
        assert bloc["t"] is None

    def test_pic_du_jour_lu_reellement_dans_data_access_et_larchive_n1(self, monkeypatch):
        # Chemin reel, sans monkeypatch de _max_current_in_snapshots : on
        # verifie que le vrai balayage Mongo (via FakeDb) retrouve le pic du
        # jour dans data_access, et le pic N-1 dans une collection d'archive
        # -- watch_peaks.snapshot_collections doit bien les balayer toutes.
        # resolve_race_dt reste monkeypatche : le construire fidelement sur
        # FakeDb demanderait de reproduire parametrages/historique_controle,
        # hors perimetre de ce test (ce que couvre deja watch_peaks).
        #
        # Les deux courses tombent le MEME jour de semaine (samedi) mais a
        # des dates calendaires differentes (18 avril 2026, 19 avril 2025) --
        # exactement le cas reel verifie sur la base dev, et deliberement
        # PAS le meme jour du mois : sinon `_jour_semaine_aligne` ne
        # recalerait rien et ce test ne distinguerait plus un alignement par
        # decalage-de-course d'un alignement par date calendaire.
        _ps = watch_pages.pcorg_summary

        def snap(loc, current, heure_paris):
            return {"_id": "snap-%s" % heure_paris.isoformat(),
                    "requested_location_id": loc,
                    "current": str(current),
                    "timestamp": heure_paris.astimezone(timezone.utc)
                                            .replace(tzinfo=None)}

        jour_2026 = datetime(2026, 4, 18, 14, 15, tzinfo=_ps.TZ_PARIS)
        jour_2025 = datetime(2025, 4, 19, 15, 2, tzinfo=_ps.TZ_PARIS)

        db = FakeDb(
            data_access=[
                snap("628", 100, datetime(2026, 4, 18, 9, 0, tzinfo=_ps.TZ_PARIS)),
                snap("628", 52100, jour_2026),
            ],
            hsh_archive_compteurs_test=[
                snap("628", 49800, jour_2025),
            ],
        )

        def course(db_arg, ev, an):
            return {2026: datetime(2026, 4, 18, 13, 0, tzinfo=timezone.utc),  # samedi
                    2025: datetime(2025, 4, 19, 13, 0, tzinfo=timezone.utc)}[an]  # samedi

        monkeypatch.setattr(watch_pages.watch_peaks, "resolve_race_dt", course)

        bloc = watch_pages.build_frequentation(
            db, "24H MOTOS", 2026, datetime(2026, 4, 18, 20, 0),
            location_id="628")

        assert bloc["pj"] == 52100
        assert bloc["ph"] == "14h15"
        assert bloc["n1"] == 49800
