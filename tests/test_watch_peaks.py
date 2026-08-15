from datetime import datetime, timedelta, timezone

from conftest import FakeDb

import watch_peaks


# Course des 24H MOTOS 2026 : samedi 18 avril, 15h Paris = 13h UTC.
COURSE_2026 = datetime(2026, 4, 18, 13, 0, tzinfo=timezone.utc)


def _snapshot(current, instant, location="628", event="GPF"):
    """Un releve compteur.

    `event` vaut GPF par defaut A DESSEIN : les releves du 24H AUTOS 2025 sont
    reellement estampilles GPF en base (le collecteur tournait avec un libelle
    perime). Un test qui ecrirait le bon evenement partout ne prouverait rien
    -- il passerait aussi avec un code qui filtre sur `requested_event`.
    """
    return {
        "requested_location_id": location,
        "requested_event": event,
        "current": current,
        "timestamp": instant.replace(tzinfo=None),
    }


def _parametrages(event, year, race_iso):
    return {"event": event, "year": str(year),
            "data": {"globalHoraires": {"race": race_iso}}}


class TestEventAliases:
    def test_nom_et_sigle(self):
        db = FakeDb(evenement=[{"nom": "LE MANS CLASSIC", "short": "LMC"}])
        assert watch_peaks.event_aliases(db, "LE MANS CLASSIC") == \
            ["LE MANS CLASSIC", "LMC"]

    def test_sans_sigle(self):
        db = FakeDb(evenement=[{"nom": "FUN RACING CARS"}])
        assert watch_peaks.event_aliases(db, "FUN RACING CARS") == \
            ["FUN RACING CARS"]

    def test_evenement_vide(self):
        assert watch_peaks.event_aliases(FakeDb(), None) == []


class TestResolveRaceDt:
    def test_lit_parametrages(self):
        db = FakeDb(parametrages=[
            _parametrages("24H MOTOS", 2026, "2026-04-18T13:00:00.000Z"),
        ])
        assert watch_peaks.resolve_race_dt(db, "24H MOTOS", 2026) == COURSE_2026

    def test_annee_incoherente_est_ecartee(self):
        # LE CAS REEL : le document parametrages `year: 2025` de LE MANS
        # CLASSIC porte une course au 05/07/2026. Sans garde, la fenetre de
        # 2025 serait celle de 2026 et le pic de 2026 sortirait sous
        # l'etiquette 2025.
        db = FakeDb(
            parametrages=[
                _parametrages("LE MANS CLASSIC", 2025, "2026-07-05T14:00:00.000Z"),
            ],
            evenement=[{"nom": "LE MANS CLASSIC", "short": "LMC"}],
        )
        assert watch_peaks.resolve_race_dt(db, "LE MANS CLASSIC", 2025) is None

    def test_repli_alias_sur_historique_controle(self):
        # historique_controle range LE MANS CLASSIC sous << LMC >>. Le repli
        # de _load_race_dt interroge le seul nom long et ne trouvait donc
        # jamais rien. Avec les alias, la bonne date de 2025 est retrouvee
        # malgre le parametrages fautif.
        db = FakeDb(
            parametrages=[
                _parametrages("LE MANS CLASSIC", 2025, "2026-07-05T14:00:00.000Z"),
            ],
            evenement=[{"nom": "LE MANS CLASSIC", "short": "LMC"}],
            historique_controle=[
                {"type": "portes", "event": "LMC", "year": 2025,
                 "race": "2025-07-05T15:00:00"},
            ],
        )
        trouve = watch_peaks.resolve_race_dt(db, "LE MANS CLASSIC", 2025)
        assert trouve is not None
        assert trouve.year == 2025
        assert trouve.date().isoformat() == "2025-07-05"

    def test_aucune_source(self):
        assert watch_peaks.resolve_race_dt(FakeDb(), "24H MOTOS", 2026) is None

    def test_annee_illisible(self):
        assert watch_peaks.resolve_race_dt(FakeDb(), "24H MOTOS", "n/a") is None


class TestEditionWindow:
    def test_fenetre_encadre_la_course(self):
        db = FakeDb(parametrages=[
            _parametrages("24H MOTOS", 2026, "2026-04-18T13:00:00.000Z"),
        ])
        debut, fin = watch_peaks.edition_window(db, "24H MOTOS", 2026)
        assert debut == COURSE_2026 - watch_peaks.WINDOW_BEFORE
        assert fin == COURSE_2026 + watch_peaks.WINDOW_AFTER

    def test_sans_date_de_course(self):
        assert watch_peaks.edition_window(FakeDb(), "X", 2026) == (None, None)


class TestPeakForEdition:
    def _db(self, **archives):
        base = dict(
            parametrages=[
                _parametrages("24H MOTOS", 2026, "2026-04-18T13:00:00.000Z"),
            ],
            evenement=[{"nom": "24H MOTOS", "short": "24HM"}],
        )
        base.update(archives)
        return FakeDb(**base)

    def test_pic_trouve_malgre_un_evenement_estampille_faux(self):
        # PIEGE B, dans sa vraie forme : les releves d'UNE MEME edition
        # portent des libelles DIFFERENTS, parce que le libelle du document
        # global a change en cours de collecte. Le pic reel est sous le
        # mauvais libelle, une valeur plus basse sous le bon.
        #
        # Les etiquettes doivent donc etre melangees pour que le test morde :
        # avec un libelle unique, un code qui filtrerait sur `requested_event`
        # passerait aussi. C'est ce qu'un sabotage a revele.
        db = self._db(**{
            "hsh_archive_compteurs_GPF_2026": [
                _snapshot("40000", COURSE_2026 - timedelta(hours=2),
                          event="24H MOTOS"),
                _snapshot("50690", COURSE_2026 + timedelta(minutes=5),
                          event="GPF"),
                _snapshot("48000", COURSE_2026 + timedelta(hours=3),
                          event="24H MOTOS"),
            ],
        })
        pic, instant = watch_peaks.peak_for_edition(db, "24H MOTOS", 2026)
        assert pic == 50690
        assert instant == COURSE_2026 + timedelta(minutes=5)

    def test_releves_hors_fenetre_ignores(self):
        db = self._db(**{
            "hsh_archive_compteurs_GPF_2026": [
                _snapshot("50690", COURSE_2026),
                # Edition voisine, deux mois plus tard, bien plus haute.
                _snapshot("148919", COURSE_2026 + timedelta(days=56)),
            ],
        })
        pic, _ = watch_peaks.peak_for_edition(db, "24H MOTOS", 2026)
        assert pic == 50690

    def test_valeurs_inconvertibles_ignorees_sans_perdre_le_reste(self):
        # `current` vaut parfois "" ou "N/A". Un cast non protege ferait
        # tomber tout le balayage sur un seul releve bancal.
        db = self._db(**{
            "hsh_archive_compteurs_GPF_2026": [
                _snapshot("N/A", COURSE_2026 - timedelta(hours=1)),
                _snapshot("", COURSE_2026 - timedelta(minutes=30)),
                _snapshot(None, COURSE_2026 - timedelta(minutes=15)),
                _snapshot("50690", COURSE_2026),
            ],
        })
        pic, _ = watch_peaks.peak_for_edition(db, "24H MOTOS", 2026)
        assert pic == 50690

    def test_plusieurs_sources_le_max_gagne(self):
        db = self._db(**{
            "data_access": [_snapshot("12000", COURSE_2026)],
            "hsh_archive_compteurs_GPF_2026": [
                _snapshot("50690", COURSE_2026 - timedelta(minutes=10)),
            ],
            "hsh_archive_compteurs_24H_MOTOS_2026": [
                _snapshot("30000", COURSE_2026 - timedelta(hours=1)),
            ],
        })
        pic, _ = watch_peaks.peak_for_edition(db, "24H MOTOS", 2026)
        assert pic == 50690

    def test_autre_compteur_ignore(self):
        db = self._db(**{
            "hsh_archive_compteurs_GPF_2026": [
                _snapshot("50690", COURSE_2026, location="628"),
                _snapshot("99999", COURSE_2026, location="1156"),
            ],
        })
        pic, _ = watch_peaks.peak_for_edition(db, "24H MOTOS", 2026)
        assert pic == 50690

    def test_sans_date_de_course_pas_de_pic(self):
        db = FakeDb(evenement=[{"nom": "X", "short": "X"}])
        assert watch_peaks.peak_for_edition(db, "X", 2026) == (None, None)

    def test_aucun_releve(self):
        assert watch_peaks.peak_for_edition(self._db(), "24H MOTOS", 2026) == \
            (None, None)


class TestListEditions:
    def _db(self):
        return FakeDb(
            parametrages=[
                _parametrages("24H MOTOS", 2026, "2026-04-18T13:00:00.000Z"),
                _parametrages("24H AUTOS", 2026, "2026-06-13T14:00:00.000Z"),
                _parametrages("GPF", 2025, "2025-05-11T12:00:00.000Z"),
                # Sans sigle : pas de libelle montre possible.
                _parametrages("FUN RACING CARS", 2026, "2026-09-01T12:00:00.000Z"),
                # Sans date de course : non consultable.
                {"event": "BPL", "year": "2026", "data": {}},
                # Le singleton de configuration n'est pas une edition.
                {"_id": "__GLOBAL__", "event": "__GLOBAL__", "year": "__GLOBAL__"},
            ],
            evenement=[
                {"nom": "24H MOTOS", "short": "24HM"},
                {"nom": "24H AUTOS", "short": "24HA"},
                {"nom": "GPF", "short": "GPF"},
                {"nom": "BPL", "short": "BPL"},
                {"nom": "FUN RACING CARS"},
            ],
        )

    def test_antichronologique(self):
        out = watch_peaks.list_editions(self._db())
        assert [e["label"] for e in out] == ["24HA 26", "24HM 26", "GPF 25"]

    def test_ecarte_ce_qui_n_est_pas_consultable(self):
        labels = [e["label"] for e in watch_peaks.list_editions(self._db())]
        # Sans sigle, sans date de course, et le singleton de configuration.
        assert "BPL 26" not in labels
        assert not any(l.startswith("__GLOBAL__") for l in labels)
        assert len(labels) == 3

    def test_plafond(self):
        docs = [_parametrages("E%d" % i, 2000 + i,
                              "%d-06-01T12:00:00.000Z" % (2000 + i))
                for i in range(watch_peaks.MAX_EDITIONS + 5)]
        db = FakeDb(
            parametrages=docs,
            evenement=[{"nom": "E%d" % i, "short": "E%d" % i}
                       for i in range(watch_peaks.MAX_EDITIONS + 5)],
        )
        assert len(watch_peaks.list_editions(db)) == watch_peaks.MAX_EDITIONS

    def test_edition_en_cours_signalee(self):
        db = FakeDb(
            parametrages=[
                _parametrages("24H MOTOS", 2026, "2026-04-18T13:00:00.000Z"),
            ],
            evenement=[{"nom": "24H MOTOS", "short": "24HM"}],
        )
        pendant = watch_peaks.list_editions(db, now_utc=COURSE_2026)
        assert pendant[0]["en_cours"] is True
        apres = watch_peaks.list_editions(
            db, now_utc=COURSE_2026 + timedelta(days=30))
        assert apres[0]["en_cours"] is False


class TestCachedPeak:
    def _db(self):
        return FakeDb(
            parametrages=[
                _parametrages("24H MOTOS", 2026, "2026-04-18T13:00:00.000Z"),
            ],
            evenement=[{"nom": "24H MOTOS", "short": "24HM"}],
            **{"hsh_archive_compteurs_GPF_2026": [
                _snapshot("50690", COURSE_2026),
            ]}
        )

    def test_ecrit_puis_relit(self):
        db = self._db()
        apres = COURSE_2026 + timedelta(days=60)
        pic, instant = watch_peaks.cached_peak(db, "24H MOTOS", 2026,
                                               now_utc=apres)
        assert pic == 50690
        doc = db[watch_peaks.CACHE_COLLECTION].find_one({"_id": "24H MOTOS|2026"})
        assert doc["peak"] == 50690
        assert doc["source"] == "archive"
        # `peak_ts` est ecrit naif, comme tout ce que pymongo relira.
        assert doc["peak_ts"].tzinfo is None

        # La source disparait : seul le cache peut encore repondre.
        db["hsh_archive_compteurs_GPF_2026"].docs = []
        assert watch_peaks.cached_peak(db, "24H MOTOS", 2026,
                                       now_utc=apres) == (pic, instant)

    def test_edition_close_n_est_pas_recalculee(self):
        db = self._db()
        apres = COURSE_2026 + timedelta(days=60)
        watch_peaks.cached_peak(db, "24H MOTOS", 2026, now_utc=apres)
        # Un pic plus haut apparait : une edition close ne le verra pas, et
        # c'est voulu -- son pic est definitif.
        db["hsh_archive_compteurs_GPF_2026"].docs.append(
            _snapshot("99999", COURSE_2026))
        pic, _ = watch_peaks.cached_peak(db, "24H MOTOS", 2026,
                                         now_utc=apres + timedelta(days=1))
        assert pic == 50690

    def test_edition_en_cours_recalculee_apres_le_ttl(self):
        db = self._db()
        pendant = COURSE_2026
        pic, _ = watch_peaks.cached_peak(db, "24H MOTOS", 2026, now_utc=pendant)
        assert pic == 50690
        assert db[watch_peaks.CACHE_COLLECTION].find_one()["source"] == "live"

        db["hsh_archive_compteurs_GPF_2026"].docs.append(
            _snapshot("52000", COURSE_2026 + timedelta(minutes=1)))

        # Dans le TTL : on garde la valeur en cache.
        fige, _ = watch_peaks.cached_peak(
            db, "24H MOTOS", 2026,
            now_utc=pendant + watch_peaks.LIVE_CACHE_TTL - timedelta(seconds=1))
        assert fige == 50690

        # Passe le TTL : le pic monte, comme il doit pendant l'evenement.
        frais, _ = watch_peaks.cached_peak(
            db, "24H MOTOS", 2026,
            now_utc=pendant + watch_peaks.LIVE_CACHE_TTL + timedelta(seconds=1))
        assert frais == 52000

    def test_sans_pic_rien_n_est_mis_en_cache(self):
        db = FakeDb(
            parametrages=[
                _parametrages("24H MOTOS", 2026, "2026-04-18T13:00:00.000Z"),
            ],
            evenement=[{"nom": "24H MOTOS", "short": "24HM"}],
        )
        assert watch_peaks.cached_peak(db, "24H MOTOS", 2026) == (None, None)
        assert db[watch_peaks.CACHE_COLLECTION].find_one() is None
