from datetime import datetime, timedelta, timezone

import pytest

import watch_state


class TestWbgtLevel:
    def test_sous_le_premier_seuil(self):
        assert watch_state.wbgt_level(22.0) == 0

    def test_paliers_iso(self):
        assert watch_state.wbgt_level(25.0) == 1
        assert watch_state.wbgt_level(27.4) == 1
        assert watch_state.wbgt_level(28.0) == 2
        assert watch_state.wbgt_level(30.0) == 3

    def test_danger_extreme_plafonne_a_trois(self):
        # Au-dela de "suspendre le travail lourd", un cran de plus ne change
        # aucune decision au poignet.
        assert watch_state.wbgt_level(35.0) == 3

    def test_absence_de_mesure(self):
        assert watch_state.wbgt_level(None) == 0

    def test_seuils_surcharges(self):
        assert watch_state.wbgt_level(26.0, thresholds=(27.0, 29.0, 31.0)) == 0
        assert watch_state.wbgt_level(29.5, thresholds=(27.0, 29.0, 31.0)) == 2


class TestEntryRate:
    def test_debit_nominal(self):
        t1 = datetime(2026, 8, 14, 12, 0)
        t0 = t1 - timedelta(minutes=15)
        # 800 entrees en 15 min = 3200 pers/h
        assert watch_state.entry_rate(48213, t1, 47413, t0) == 3200

    def test_snapshot_manquant(self):
        t1 = datetime(2026, 8, 14, 12, 0)
        assert watch_state.entry_rate(48213, t1, None, None) is None

    def test_delta_negatif_est_none(self):
        # Remise a zero du compteur : mieux vaut rien qu'un debit absurde.
        t1 = datetime(2026, 8, 14, 12, 0)
        t0 = t1 - timedelta(minutes=15)
        assert watch_state.entry_rate(12, t1, 48213, t0) is None

    def test_ecart_nul_est_none(self):
        t1 = datetime(2026, 8, 14, 12, 0)
        assert watch_state.entry_rate(48213, t1, 47413, t1) is None

    def test_horodatages_inverses_est_none(self):
        # Un snapshot anterieur plus recent que le courant : incoherence de
        # source, on ne publie pas un debit calcule a l'envers.
        t1 = datetime(2026, 8, 14, 12, 0)
        t2 = t1 + timedelta(minutes=15)
        assert watch_state.entry_rate(48213, t1, 47413, t2) is None

    def test_ecart_dans_la_borne_est_calcule(self):
        # Exactement a la limite (incluse) : le debit doit encore sortir.
        t1 = datetime(2026, 8, 14, 12, 0)
        t0 = t1 - timedelta(seconds=watch_state.RATE_MAX_GAP_S)
        assert watch_state.entry_rate(48213, t1, 47413, t0) is not None

    def test_ecart_trop_grand_est_none(self):
        # Interruption du flux Skidata (ou premier releve d'une edition) :
        # le releve anterieur date de plus d'une heure. Le delta d'entrees
        # ecrase par un delta de temps enorme rendrait un debit proche de
        # zero, presente comme le debit courant sur une foule qui entre.
        t1 = datetime(2026, 8, 14, 12, 0)
        t0 = t1 - timedelta(seconds=watch_state.RATE_MAX_GAP_S + 1)
        assert watch_state.entry_rate(48213, t1, 47413, t0) is None


class TestSelectAlerts:
    CONFIG = [
        {"slug": "field_sos", "level": 3, "label": "SOS tablette"},
        {"slug": "meteo-vent", "level": 2, "label": "Vent fort"},
        {"slug": "opening", "level": 1, "label": "Ouverture"},
    ]

    def test_seuls_les_slugs_configures_partent(self):
        active = [
            {"definition_slug": "field_sos", "title": "SOS"},
            {"definition_slug": "checkpoint-reassign", "title": "Reaffectation"},
        ]
        out = watch_state.select_alerts(active, self.CONFIG)
        assert out == [{"l": 3, "m": "SOS tablette"}]

    def test_tri_par_niveau_decroissant(self):
        active = [
            {"definition_slug": "opening", "title": "x"},
            {"definition_slug": "field_sos", "title": "y"},
            {"definition_slug": "meteo-vent", "title": "z"},
        ]
        out = watch_state.select_alerts(active, self.CONFIG)
        assert [a["l"] for a in out] == [3, 2, 1]

    def test_libelle_tronque(self):
        config = [{"slug": "a", "level": 1,
                   "label": "Un libelle beaucoup trop long pour un poignet"}]
        out = watch_state.select_alerts([{"definition_slug": "a"}], config)
        assert out[0]["m"] == "Un libelle beaucoup trop"
        assert len(out[0]["m"]) == watch_state.LABEL_MAX

    def test_repli_sur_le_titre_si_pas_de_label(self):
        config = [{"slug": "a", "level": 2}]
        out = watch_state.select_alerts(
            [{"definition_slug": "a", "title": "ALERTE VENT"}], config)
        assert out[0]["m"] == "ALERTE VENT"

    def test_plafond_a_cinq(self):
        config = [{"slug": "s%d" % i, "level": 3} for i in range(8)]
        active = [{"definition_slug": "s%d" % i, "title": "t"} for i in range(8)]
        assert len(watch_state.select_alerts(active, config)) == 5

    def test_config_vide(self):
        assert watch_state.select_alerts([{"definition_slug": "a"}], []) == []

    def test_niveau_par_defaut_si_absent(self):
        # Une regle de configuration sans niveau explicite vaut 1 : la montre
        # signale, mais ne reveille pas.
        config = [{"slug": "a", "label": "Sans niveau"}]
        out = watch_state.select_alerts([{"definition_slug": "a"}], config)
        assert out == [{"l": 1, "m": "Sans niveau"}]


class TestEventLabel:
    def test_format_court(self):
        assert watch_state.event_label("24HM", 2026) == "24HM 26"

    def test_annee_sur_deux_chiffres(self):
        assert watch_state.event_label("GPE", 2025) == "GPE 25"

    def test_donnees_manquantes(self):
        assert watch_state.event_label(None, 2026) is None
        assert watch_state.event_label("24HM", None) is None


class TestResolveEvent:
    COUNTER = {"requested_event": "24H MOTOS", "year": "2026"}

    def test_mode_auto_suit_le_compteur(self):
        cfg = {"event_mode": "auto"}
        assert watch_state.resolve_event(cfg, self.COUNTER) == ("24H MOTOS", 2026)

    def test_mode_auto_convertit_l_annee_en_int(self):
        _, year = watch_state.resolve_event({}, self.COUNTER)
        assert year == 2026
        assert isinstance(year, int)

    def test_mode_epingle(self):
        cfg = {"event_mode": "pinned", "event": "24H CAMIONS", "year": 2025}
        assert watch_state.resolve_event(cfg, self.COUNTER) == ("24H CAMIONS", 2025)

    def test_auto_sans_compteur(self):
        assert watch_state.resolve_event({"event_mode": "auto"}, None) == (None, None)

    def test_annee_illisible(self):
        counter = {"requested_event": "X", "year": "n/a"}
        assert watch_state.resolve_event({}, counter) == ("X", None)

    def test_epingle_avec_annee_illisible(self):
        cfg = {"event_mode": "pinned", "event": "24H MOTOS", "year": "n/a"}
        assert watch_state.resolve_event(cfg, self.COUNTER) == ("24H MOTOS", None)


# Le double Mongo vit dans tests/conftest.py : watch_state et watch_peaks
# le partagent, et une seule implementation evite qu'ils divergent sur ce
# que Mongo fait reellement.
from conftest import FakeDb  # noqa: E402


NOW = datetime(2026, 8, 14, 12, 0)


def _counter(entries, minutes_ago, event="24H MOTOS", year="2026"):
    return {
        "requested_location_id": "628",
        "requested_location_type": "Area",
        "entries": entries,
        "timestamp": NOW - timedelta(minutes=minutes_ago),
        "requested_event": event,
        "year": year,
    }


class TestReadConfig:
    def test_defauts_si_absent(self):
        db = FakeDb()
        cfg = watch_state.read_config(db)
        assert cfg["event_mode"] == "auto"
        assert cfg["alerts"] == []
        assert cfg["wbgt_levels"] == list(watch_state.WBGT_DEFAULT_LEVELS)

    def test_lit_le_document(self):
        db = FakeDb(watch_config=[{
            "_id": "watch", "event_mode": "pinned",
            "event": "24H CAMIONS", "year": 2025,
            "alerts": [{"slug": "a", "level": 2}],
            "wbgt_levels": [26, 29, 32],
        }])
        cfg = watch_state.read_config(db)
        assert cfg["event_mode"] == "pinned"
        assert cfg["wbgt_levels"] == [26, 29, 32]


class TestReadCounter:
    def test_prend_le_dernier_releve(self):
        db = FakeDb(data_access=[_counter(100, 30), _counter(150, 5)])
        doc = watch_state.read_counter(db, "628")
        assert doc["entries"] == 150

    def test_absent(self):
        assert watch_state.read_counter(FakeDb(), "628") is None

    def test_snapshot_anterieur(self):
        db = FakeDb(data_access=[
            _counter(100, 30), _counter(120, 16), _counter(150, 1),
        ])
        doc = watch_state.read_counter_before(db, "628", NOW - timedelta(minutes=15))
        assert doc["entries"] == 120

    def test_borne_de_fraicheur_ecarte_le_residu(self):
        # Le cas reel : LMC archive, il ne reste dans data_access qu'un
        # residu 24H MOTOS du 23/04. Sans borne, c'est lui qui remontait.
        residu = _counter(8, minutes_ago=113 * 24 * 60)
        db = FakeDb(data_access=[residu])
        assert watch_state.read_counter(db, "628") is residu
        assert watch_state.read_counter(
            db, "628",
            max_age=watch_state.COUNTER_MAX_AGE,
            now_utc=NOW.replace(tzinfo=timezone.utc)) is None

    def test_borne_de_fraicheur_garde_un_releve_recent(self):
        # La borne ne doit pas etre si serree qu'elle coupe le direct : une
        # heure d'anciennete reste un evenement en cours.
        recent = _counter(48213, minutes_ago=60)
        db = FakeDb(data_access=[recent])
        assert watch_state.read_counter(
            db, "628",
            max_age=watch_state.COUNTER_MAX_AGE,
            now_utc=NOW.replace(tzinfo=timezone.utc)) is recent


class TestReadLiveActive:
    def test_drapeau_leve(self):
        db = FakeDb(data_access=[
            {"_id": "___GLOBAL___", "live_controle_actif": True},
        ])
        assert watch_state.read_live_active(db) is True

    def test_drapeau_baisse(self):
        db = FakeDb(data_access=[
            {"_id": "___GLOBAL___", "live_controle_actif": False},
        ])
        assert watch_state.read_live_active(db) is False

    def test_champ_absent_vaut_inactif(self):
        # Meme defaut que /api/live-controle/status : absent = inactif.
        db = FakeDb(data_access=[{"_id": "___GLOBAL___"}])
        assert watch_state.read_live_active(db) is False

    def test_aucun_doc_global(self):
        assert watch_state.read_live_active(FakeDb()) is False


class TestReadPrincipalId:
    def test_lit_le_doc_global(self):
        db = FakeDb(data_access=[
            {"_id": "___GLOBAL___", "compteur_principal_id": "628"},
        ])
        assert watch_state.read_principal_id(db) == "628"

    def test_absent(self):
        assert watch_state.read_principal_id(FakeDb()) is None


class TestReadActiveAlerts:
    def test_ecarte_les_expirees(self):
        db = FakeDb(cockpit_active_alerts=[
            {"definition_slug": "a", "event": "24H MOTOS", "year": "2026",
             "expiresAt": NOW + timedelta(hours=1)},
            {"definition_slug": "b", "event": "24H MOTOS", "year": "2026",
             "expiresAt": NOW - timedelta(hours=1)},
        ])
        out = watch_state.read_active_alerts(db, "24H MOTOS", 2026, NOW)
        assert [a["definition_slug"] for a in out] == ["a"]

    def test_sans_evenement_ne_remonte_rien(self):
        db = FakeDb(cockpit_active_alerts=[
            {"definition_slug": "a", "expiresAt": NOW + timedelta(hours=1)},
        ])
        assert watch_state.read_active_alerts(db, None, None, NOW) == []


class TestBuildState:
    def _db(self):
        return FakeDb(
            data_access=[
                {"_id": "___GLOBAL___", "compteur_principal_id": "628",
                 "live_controle_actif": True},
                _counter(47413, 15),
                _counter(48213, 0),
            ],
            watch_config=[{
                "_id": "watch", "event_mode": "auto",
                "alerts": [{"slug": "field_sos", "level": 3,
                            "label": "SOS tablette"}],
            }],
            cockpit_active_alerts=[{
                "definition_slug": "field_sos", "event": "24H MOTOS",
                "year": "2026", "title": "SOS",
                "expiresAt": NOW + timedelta(hours=1),
            }],
            evenement=[{"nom": "24H MOTOS", "short": "24HM"}],
            meteo_previsions=[{
                "Date": "2026-08-14",
                "Heures": [{"Heure": "12:00", "Temperature (C)": 30.0,
                            "Humidite (%)": 60}],
            }],
        )

    def test_payload_complet(self):
        # `now_utc=NOW` : la valeur n'a pas a etre reellement en UTC pour ce
        # test d'assemblage, elle sert seulement a rendre l'alerte active
        # (expiresAt = NOW + 1h dans self._db()) comparable a l'appel reel de
        # build_state(db, now, now_utc). La semantique du fuseau est testee
        # a part dans TestFuseauxHoraires, qui NE PEUT PAS etre tautologique.
        st = watch_state.build_state(self._db(), NOW, now_utc=NOW)
        assert st["e"] == 48213
        assert st["er"] == 3200
        assert st["n"] == "24HM 26"
        assert st["al"] == [{"l": 3, "m": "SOS tablette"}]
        # `horodatage` est naif-UTC (comme le rend pymongo) : l'epoch publie
        # doit venir d'une interpretation UTC, jamais de .timestamp() nu qui
        # utiliserait le fuseau local du poste executant les tests.
        assert st["t"] == int(NOW.replace(tzinfo=timezone.utc).timestamp())
        assert st["m"] == "live"

    def test_base_vide_ne_leve_pas(self):
        st = watch_state.build_state(FakeDb(), NOW)
        assert st["e"] is None
        assert st["er"] is None
        assert st["al"] == []
        assert st["wl"] == 0
        assert st["m"] == "past"

    def _db_residu(self, actif):
        """Base d'apres archivage : plus que le residu d'une edition close."""
        return FakeDb(
            data_access=[
                {"_id": "___GLOBAL___", "compteur_principal_id": "628",
                 "live_controle_actif": actif},
                _counter(8, minutes_ago=113 * 24 * 60),
            ],
            evenement=[{"nom": "24H MOTOS", "short": "24HM"}],
        )

    def test_residu_apres_archivage_ne_fait_pas_de_direct(self):
        # LA regression a tenir. La montre affichait << 24HM 26 >> avec les
        # 8 entrees du 23 avril, presentees comme du direct perime de
        # 113 jours. Ni les chiffres ni le libelle d'evenement ne doivent
        # survivre a la borne de fraicheur.
        st = watch_state.build_state(self._db_residu(actif=True), NOW,
                                     now_utc=NOW.replace(tzinfo=timezone.utc))
        assert st["m"] == "past"
        assert st["e"] is None
        assert st["er"] is None
        assert st["t"] is None
        assert st["n"] is None

    def test_collecteur_desarme_coupe_le_direct_sans_attendre(self):
        # Meme base, mais un releve FRAIS : seul le drapeau distingue les
        # deux cas. Si on avait garde la seule borne de fraicheur, ce
        # scenario -- desactivation en fin d'edition -- serait reste
        # << live >> pendant six heures.
        db = FakeDb(
            data_access=[
                {"_id": "___GLOBAL___", "compteur_principal_id": "628",
                 "live_controle_actif": False},
                _counter(48213, minutes_ago=1),
            ],
            evenement=[{"nom": "24H MOTOS", "short": "24HM"}],
        )
        st = watch_state.build_state(db, NOW,
                                     now_utc=NOW.replace(tzinfo=timezone.utc))
        assert st["m"] == "past"
        assert st["e"] is None

    def test_evenement_epingle_survit_au_mode_past(self):
        # L'epinglage dit QUEL evenement, pas s'il y a du direct. Une
        # configuration epinglee garde donc son libelle et ses alertes meme
        # sans releve frais -- c'est le recours pour qui veut ses alertes
        # quand le live-controle est arrete.
        db = self._db_residu(actif=False)
        db["watch_config"].docs.append({
            "_id": "watch", "event_mode": "pinned",
            "event": "24H MOTOS", "year": 2026,
        })
        st = watch_state.build_state(db, NOW,
                                     now_utc=NOW.replace(tzinfo=timezone.utc))
        assert st["m"] == "past"
        assert st["n"] == "24HM 26"
        assert st["e"] is None

    def test_taille_sous_deux_ko(self):
        import json
        st = watch_state.build_state(self._db(), NOW, now_utc=NOW)
        assert len(json.dumps(st).encode("utf-8")) < 2048


class FakePeaks:
    """Doublure de watch_peaks. C'est ce que l'injection achete : build_state
    se teste sans base d'archives ni date de course."""

    def __init__(self, editions=None, pics=None):
        self._editions = editions or []
        self._pics = pics or {}
        self.demandes = []

    def list_editions(self, db, now_utc=None):
        return list(self._editions)

    def cached_peak(self, db, event, year, now_utc=None):
        self.demandes.append((event, year))
        return self._pics.get((event, year), (None, None))


PIC_TS = datetime(2026, 4, 18, 13, 5, 9, tzinfo=timezone.utc)


class TestBuildStatePics:
    def _db_direct(self):
        return FakeDb(
            data_access=[
                {"_id": "___GLOBAL___", "compteur_principal_id": "628",
                 "live_controle_actif": True},
                _counter(48213, 0),
            ],
            evenement=[{"nom": "24H MOTOS", "short": "24HM"}],
        )

    def _db_hors_evenement(self):
        return FakeDb(
            data_access=[
                {"_id": "___GLOBAL___", "compteur_principal_id": "628",
                 "live_controle_actif": False},
            ],
            evenement=[{"nom": "LE MANS CLASSIC", "short": "LMC"}],
        )

    def test_pic_de_l_edition_en_cours(self):
        peaks = FakePeaks(pics={("24H MOTOS", 2026): (50690, PIC_TS)})
        st = watch_state.build_state(self._db_direct(), NOW, now_utc=NOW,
                                     peaks=peaks)
        assert st["m"] == "live"
        assert st["pk"] == 50690
        assert st["pkt"] == int(PIC_TS.timestamp())

    def test_hors_evenement_rapporte_la_derniere_edition(self):
        # Sans ce repli, l'ecran principal serait vide les 350 jours de
        # l'annee ou rien ne roule : plus de compteur, donc plus d'evenement
        # derive, donc plus de libelle ni de pic.
        peaks = FakePeaks(
            editions=[{"event": "LE MANS CLASSIC", "year": 2026,
                       "label": "LMC 26"}],
            pics={("LE MANS CLASSIC", 2026): (52409, PIC_TS)},
        )
        st = watch_state.build_state(self._db_hors_evenement(), NOW,
                                     now_utc=NOW, peaks=peaks)
        assert st["m"] == "past"
        assert st["e"] is None
        assert st["t"] is None
        assert st["n"] == "LMC 26"
        assert st["pk"] == 52409

    def test_evenement_epingle_n_est_pas_remplace(self):
        # L'epinglage est un choix explicite : le repli ne doit jamais passer
        # devant, meme hors evenement.
        db = self._db_hors_evenement()
        db["watch_config"].docs.append({
            "_id": "watch", "event_mode": "pinned",
            "event": "24H MOTOS", "year": 2026,
        })
        db["evenement"].docs.append({"nom": "24H MOTOS", "short": "24HM"})
        peaks = FakePeaks(
            editions=[{"event": "LE MANS CLASSIC", "year": 2026,
                       "label": "LMC 26"}],
            pics={("24H MOTOS", 2026): (50690, PIC_TS),
                  ("LE MANS CLASSIC", 2026): (52409, PIC_TS)},
        )
        st = watch_state.build_state(db, NOW, now_utc=NOW, peaks=peaks)
        assert st["n"] == "24HM 26"
        assert st["pk"] == 50690
        assert ("LE MANS CLASSIC", 2026) not in peaks.demandes

    def test_sans_module_peaks_le_payload_reste_valide(self):
        # watch_state doit rester utilisable seul : c'est ce qui interdit le
        # cycle d'import avec watch_peaks.
        st = watch_state.build_state(self._db_direct(), NOW, now_utc=NOW)
        assert st["pk"] is None
        assert st["pkt"] is None
        assert st["e"] == 48213

    def test_aucune_edition_consultable(self):
        peaks = FakePeaks(editions=[])
        st = watch_state.build_state(self._db_hors_evenement(), NOW,
                                     now_utc=NOW, peaks=peaks)
        assert st["n"] is None
        assert st["pk"] is None
        assert st["pkt"] is None

    def test_le_repli_saute_les_editions_sans_pic(self):
        # Cas rencontre sur la base reelle : l'edition la plus recente n'a pas
        # d'archive exploitable. La retenir donnerait un ecran qui affiche un
        # titre et des tirets ; on descend jusqu'a la premiere edition qu'on
        # sait chiffrer.
        db = self._db_hors_evenement()
        db["evenement"].docs.append({"nom": "24H AUTOS", "short": "24HA"})
        peaks = FakePeaks(
            editions=[
                {"event": "24H AUTOS", "year": 2026, "label": "24HA 26"},
                {"event": "LE MANS CLASSIC", "year": 2026, "label": "LMC 26"},
            ],
            pics={("LE MANS CLASSIC", 2026): (52409, PIC_TS)},
        )
        st = watch_state.build_state(db, NOW, now_utc=NOW, peaks=peaks)
        assert st["n"] == "LMC 26"
        assert st["pk"] == 52409


class TestReadWeatherNow:
    def _db_avec(self, entree):
        return FakeDb(meteo_previsions=[{
            "Date": "2026-08-14",
            "Heures": [entree],
        }])

    def test_cles_accentuees(self):
        # Les documents en base portent les cles accentuees ; c'est la forme
        # nominale, la variante sans accent n'est qu'un repli.
        db = self._db_avec({"Heure": "12:00",
                            "Température (°C)": 30.0,
                            "Humidité (%)": 60})
        wbgt, niveau = watch_state.read_weather_now(db, NOW)
        assert wbgt is not None
        assert niveau is not None

    def test_repli_sans_accent(self):
        db = self._db_avec({"Heure": "12:00",
                            "Temperature (C)": 30.0, "Humidite (%)": 60})
        wbgt, _ = watch_state.read_weather_now(db, NOW)
        assert wbgt is not None

    def test_zero_est_une_valeur_legitime(self):
        # 0 degre et 0 % d'humidite sont des mesures valides : un `or defaut`
        # les remplacerait silencieusement.
        db = self._db_avec({"Heure": "12:00",
                            "Temperature (C)": 0, "Humidite (%)": 0})
        wbgt, _ = watch_state.read_weather_now(db, NOW)
        assert wbgt is not None

    def test_creneau_absent(self):
        db = self._db_avec({"Heure": "03:00",
                            "Temperature (C)": 30.0, "Humidite (%)": 60})
        assert watch_state.read_weather_now(db, NOW) == (None, None)

    def test_aucun_document(self):
        assert watch_state.read_weather_now(FakeDb(), NOW) == (None, None)


class TestFuseauxHoraires:
    """Non-regression des deux pannes critiques trouvees en relecture finale.

    Ces deux tests ne peuvent PAS etre tautologiques : l'epoch/l'instant de
    reference est calcule par un chemin totalement independant de celui
    teste (jamais la meme transformation naive/UTC des deux cotes de
    l'egalite), contrairement a l'ancien `test_payload_complet` qui
    comparait `int(NOW.timestamp())` -- calcule avec le meme defaut fautif
    que le code -- et ne pouvait donc rien detecter.
    """

    def test_alerte_expirant_bientot_est_conservee(self):
        # Reproduit la panne CRITIQUE 1 : expiresAt naif-UTC (tel que
        # pymongo le relit), compare a un now_utc CONSCIENT. L'alerte expire
        # reellement dans 20 minutes : avec l'ancien filtre (comparaison a
        # une heure locale), l'ecart de 1h a 2h la faisait paraitre deja
        # expiree.
        maintenant_utc = datetime.now(timezone.utc)
        expire = maintenant_utc.replace(tzinfo=None) + timedelta(minutes=20)
        db = FakeDb(cockpit_active_alerts=[{
            "definition_slug": "a", "event": "24H MOTOS", "year": "2026",
            "expiresAt": expire,
        }])
        out = watch_state.read_active_alerts(
            db, "24H MOTOS", 2026, maintenant_utc)
        assert len(out) == 1

    def test_t_publie_le_bon_epoch(self):
        # Reproduit la panne CRITIQUE 2 : `horodatage` est naif mais
        # contient de l'UTC (pymongo, client non tz_aware). L'epoch publie
        # doit valoir l'epoch reel, pas l'epoch decale de l'heure locale du
        # serveur.
        instant = datetime.now(timezone.utc)
        naif_utc = instant.replace(tzinfo=None)
        db = FakeDb(
            data_access=[
                {"_id": "___GLOBAL___", "compteur_principal_id": "628",
                 "live_controle_actif": True},
                {"requested_location_id": "628", "entries": 100,
                 "timestamp": naif_utc, "requested_event": "24H MOTOS",
                 "year": "2026"},
            ],
        )
        st = watch_state.build_state(db, datetime.now(), now_utc=instant)
        # `instant.timestamp()` est un chemin de calcul independant : un
        # datetime conscient sait convertir son propre epoch sans repasser
        # par le `.replace(tzinfo=utc)` du code teste.
        assert abs(st["t"] - instant.timestamp()) < 2
