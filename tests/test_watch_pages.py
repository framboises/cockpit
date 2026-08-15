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

    def test_quatre_terrains_au_plus_les_plus_charges(self):
        routes = [{"name": "#I# T%d" % i, "time": 100 * (i + 1),
                   "historicTime": 100} for i in range(6)]
        bloc = watch_pages.build_trafic(self._db(routes, []), NOW)
        assert len(bloc["r"]) == 4
        # Le plus charge en premier : la montre montre d'abord ce qui coince.
        assert bloc["r"][0][0] == "T5"

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

    def test_t_est_none_sans_run_piaf(self):
        # Pas de donnee PIAF (cas reel en base dev) : `t` doit dire "je ne
        # sais pas dater", pas mentir en affichant l'instant du calcul.
        bloc = watch_pages.build_meteo(FakeDb(), datetime(2026, 8, 15, 12, 0))
        assert bloc["t"] is None
