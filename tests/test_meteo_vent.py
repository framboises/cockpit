import zlib
from datetime import datetime, timedelta

import numpy as np

from conftest import FakeDb

import meteo_vent

MAINTENANT = datetime(2026, 11, 2, 12, 0)
RUN_PI = datetime(2026, 11, 2, 11, 0)
RUN_AR = datetime(2026, 11, 2, 6, 0)
BBOX = {"west": 0.20, "south": 47.94, "east": 0.22, "north": 47.95}


def _grille(valeur):
    return zlib.compress(np.full((2, 3), valeur, dtype="float32").tobytes())


def _doc(flux, run, echeance, ident, **champs):
    doc = {"_id": ident, "flux": flux, "modele": "AROME-PI" if flux == meteo_vent.FLUX_PI else "AROME",
           "run_at": run, "valid_at": run + timedelta(minutes=echeance), "echeance_min": echeance,
           "bbox": BBOX, "nx": 3, "ny": 2, "pas_deg": 0.01}
    for k, v in champs.items():
        doc[k] = _grille(v) if not k.endswith("_ms") else v
    return doc


def _db():
    docs = [
        # AROME : vent d'ouest qui forcit de 2 a 4 m/s entre 12 h et 13 h
        _doc(meteo_vent.FLUX_AROME, RUN_AR, 360, "a6", u=2.0, v=0.0, rafale=5.0, rafale_max_site_ms=5.0),
        _doc(meteo_vent.FLUX_AROME, RUN_AR, 420, "a7", u=4.0, v=0.0, rafale=7.0, rafale_max_site_ms=7.0),
        _doc(meteo_vent.FLUX_AROME, RUN_AR, 480, "a8", u=4.0, v=0.0, rafale=20.0, rafale_max_site_ms=20.0),
        _doc(meteo_vent.FLUX_AROME, RUN_AR, 540, "a9", u=4.0, v=0.0, rafale=8.0, rafale_max_site_ms=8.0),
        # AROME-PI : rafales au quart d'heure, une echeance deja passee
        _doc(meteo_vent.FLUX_PI, RUN_PI, 15, "p15", u_rafale=6.0, v_rafale=0.0, rafale=6.0, rafale_max_site_ms=6.0),
        _doc(meteo_vent.FLUX_PI, RUN_PI, 90, "p90", u_rafale=8.0, v_rafale=0.0, rafale=8.0, rafale_max_site_ms=8.0),
        _doc(meteo_vent.FLUX_PI, RUN_PI, 120, "p120", u_rafale=9.0, v_rafale=0.0, rafale=9.0, rafale_max_site_ms=9.0),
    ]
    return FakeDb(meteo_vent=docs, meteo_vent_obs=[], parametrages=[], tribunes=[], hospitalites=[])


class TestDirection:
    def test_vent_d_ouest(self):
        assert meteo_vent.direction_deg(5.0, 0.0) == 270
        assert meteo_vent.cardinal(270) == "O"

    def test_vent_du_nord(self):
        # V negatif : l'air descend vers le sud, il VIENT du nord
        assert meteo_vent.direction_deg(0.0, -5.0) % 360 == 0
        assert meteo_vent.cardinal(0) == "N"


class TestTrames:
    def test_aromepi_puis_arome_sans_doublon(self):
        t = meteo_vent.trames(_db(), MAINTENANT)["trames"]
        # p15 (11:15) est passe de plus de 15 min : ecarte
        assert [x["id"] for x in t] == ["pi|20261102T1100|90", "pi|20261102T1100|120",
                                        "ar|20261102T0600|480", "ar|20261102T0600|540"]
        assert t[0]["moyen"] == "AROME interpole"

    def test_vent_moyen_interpole_sur_les_trames_aromepi(self):
        c = meteo_vent.champ(_db(), "pi|20261102T1100|90")   # 12:30, mi-chemin 12 h / 13 h
        assert c["moyen"] == "AROME interpole"
        assert c["u"][0] == 3.0
        assert c["u_rafale"][0] == 8.0

    def test_trame_inconnue(self):
        assert meteo_vent.champ(_db(), "pi|20261102T1100|999") is None
        assert meteo_vent.champ(_db(), "n'importe quoi") is None


class TestAlerteEtSerie:
    def test_premiere_echeance_au_dessus_du_seuil(self):
        a = meteo_vent.alerte_rafale(_db(), 50, MAINTENANT)
        assert a["depasse"] is True
        assert a["premieres"][0]["valid_at"] == "2026-11-02T14:00:00Z"
        assert a["pic"]["rafale_max_site_kmh"] == 72

    def test_aucun_depassement(self):
        assert meteo_vent.alerte_rafale(_db(), 100, MAINTENANT)["depasse"] is False

    def test_serie_au_point(self):
        s = meteo_vent.serie(_db(), 47.945, 0.21, 42, MAINTENANT)
        assert s["disponible"]
        assert s["serie"][0]["direction"] == "O"
        assert s["serie"][0]["vent_kmh"] == round(3.0 * 3.6)

    def test_point_hors_emprise(self):
        s = meteo_vent.serie(_db(), 48.5, 0.21, 42, MAINTENANT)
        assert s == {"disponible": False, "motif": "point_hors_emprise"}
