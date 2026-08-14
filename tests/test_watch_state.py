from datetime import datetime, timedelta

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


class FakeCollection:
    """Collection Mongo minimale : juste ce que watch_state appelle."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.last_query = None

    def find_one(self, query=None, projection=None, sort=None):
        self.last_query = query
        docs = self._matching(query)
        if sort:
            cle, sens = sort[0]
            docs = sorted(docs, key=lambda d: d.get(cle),
                          reverse=(sens == -1))
        return docs[0] if docs else None

    def find(self, query=None, projection=None):
        self.last_query = query
        return list(self._matching(query))

    def _matching(self, query):
        if not query:
            return list(self.docs)
        out = []
        for doc in self.docs:
            if all(self._match(doc, cle, val) for cle, val in query.items()):
                out.append(doc)
        return out

    @staticmethod
    def _match(doc, cle, val):
        actuel = doc.get(cle)
        if isinstance(val, dict):
            if "$lte" in val and not (actuel is not None and actuel <= val["$lte"]):
                return False
            if "$gt" in val and not (actuel is not None and actuel > val["$gt"]):
                return False
            if "$in" in val and actuel not in val["$in"]:
                return False
            return True
        return actuel == val


class FakeDb:
    def __init__(self, **collections):
        self._cols = {k: FakeCollection(v) for k, v in collections.items()}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = FakeCollection([])
        return self._cols[name]


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
                {"_id": "___GLOBAL___", "compteur_principal_id": "628"},
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
        st = watch_state.build_state(self._db(), NOW)
        assert st["e"] == 48213
        assert st["er"] == 3200
        assert st["n"] == "24HM 26"
        assert st["al"] == [{"l": 3, "m": "SOS tablette"}]
        assert st["t"] == int(NOW.timestamp())

    def test_base_vide_ne_leve_pas(self):
        st = watch_state.build_state(FakeDb(), NOW)
        assert st["e"] is None
        assert st["er"] is None
        assert st["al"] == []
        assert st["wl"] == 0

    def test_taille_sous_deux_ko(self):
        import json
        st = watch_state.build_state(self._db(), NOW)
        assert len(json.dumps(st).encode("utf-8")) < 2048
