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
