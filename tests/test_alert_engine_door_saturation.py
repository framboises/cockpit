"""Tests de la detection door_saturation_forecast (alert_engine.py).

Calcul pur (debit, tendance, profil N-1, prevision) sur des series
synthetiques, puis le handler complet contre un double Mongo : jamais la
vraie base, jamais le cycle complet (qui enverrait des WhatsApp).
"""

import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("TITAN_ENV", "dev")
import alert_engine as AE  # noqa: E402

PARIS = ZoneInfo("Europe/Paris")


# ---------------------------------------------------------------------------
# Double Mongo local : operateurs utilises par le handler, rien de plus.
# Un operateur inconnu leve (un filtre ignore rendrait le test tautologique).
# ---------------------------------------------------------------------------

def _get(doc, dotted):
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def _cmp(v):
    if isinstance(v, datetime) and v.tzinfo is not None:
        return v.astimezone(timezone.utc).replace(tzinfo=None)
    return v


def _match(doc, query):
    for key, cond in (query or {}).items():
        if key == "$or":
            if not any(_match(doc, q) for q in cond):
                return False
            continue
        val, present = _get(doc, key)
        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                if op == "$exists":
                    if bool(arg) != present:
                        return False
                elif op in ("$gte", "$gt", "$lte", "$lt"):
                    if val is None:
                        return False
                    a, b = _cmp(val), _cmp(arg)
                    if op == "$gte" and not a >= b:
                        return False
                    if op == "$gt" and not a > b:
                        return False
                    if op == "$lte" and not a <= b:
                        return False
                    if op == "$lt" and not a < b:
                        return False
                elif op == "$in":
                    if val not in arg:
                        return False
                elif op == "$ne":
                    if val == arg:
                        return False
                else:
                    raise NotImplementedError(op)
        else:
            if _cmp(val) != _cmp(cond):
                return False
    return True


class Cursor(list):
    def sort(self, key, direction=1):
        super().sort(key=lambda d: _cmp(_get(d, key)[0]), reverse=direction == -1)
        return self

    def limit(self, n):
        return Cursor(self[:n])


class Coll:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.writes = 0

    def find(self, query=None, projection=None):
        return Cursor([d for d in self.docs if _match(d, query)])

    def find_one(self, query=None, projection=None, sort=None):
        r = self.find(query)
        return r[0] if r else None

    def count_documents(self, query=None):
        return len(self.find(query))

    def insert_one(self, doc):
        self.writes += 1
        self.docs.append(doc)

    def update_one(self, *a, **k):
        self.writes += 1

    def create_index(self, *a, **k):
        return None


class DB:
    def __init__(self, **cols):
        self.cols = {k: Coll(v) for k, v in cols.items()}

    def __getitem__(self, name):
        return self.cols.setdefault(name, Coll())

    def list_collection_names(self):
        return list(self.cols)


# ---------------------------------------------------------------------------
# Calcul pur
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 13, 10, 0)  # samedi, heure de Paris naive


def _series(rates_per_5min, start=T0 - timedelta(minutes=60)):
    """{debut de tranche: compte} depuis une liste de comptes par 5 min."""
    return {start + timedelta(minutes=5 * i): c for i, c in enumerate(rates_per_5min)}


class TestDoorRate:
    def test_debit_moyen_sur_la_fenetre(self):
        b = _series([10] * 9 + [50, 60, 70])  # 3 dernieres tranches avant 10:00
        assert AE.door_rate(b, T0, 15) == pytest.approx((50 + 60 + 70) * 4)

    def test_tranche_absente_vaut_zero(self):
        b = {T0 - timedelta(minutes=5): 100}
        assert AE.door_rate(b, T0, 15) == pytest.approx(400)

    def test_tranche_en_cours_exclue(self):
        b = {T0: 999, T0 - timedelta(minutes=5): 10}
        assert AE.door_rate(b, T0, 15) == pytest.approx(40)

    def test_aucune_donnee(self):
        assert AE.door_rate({}, T0, 15) == 0


class TestTrend:
    def test_pente_lineaire(self):
        # +1 passage par tranche de 5 min = +12/h toutes les 5 min = 2.4 /h par minute
        b = _series([0] * 6 + [10, 11, 12, 13, 14, 15])
        assert AE.door_trend_slope(b, T0, 30) == pytest.approx(2.4)

    def test_serie_plate(self):
        assert AE.door_trend_slope(_series([20] * 12), T0, 30) == pytest.approx(0)

    def test_fenetre_trop_courte(self):
        assert AE.door_trend_slope(_series([20] * 12), T0, 10) is None


class TestN1Window:
    def test_horaire_interpole_entre_les_milieux(self):
        serie = [(T0 - timedelta(hours=1), 60, 600), (T0, 60, 1200)]
        # Milieux 9h30 (600/h) et 10h30 (1200/h) : a 10h00, 900/h
        assert AE.n1_window_rate(serie, T0, 30) == pytest.approx(900)
        # Fenetre 10h22-10h37 : rampe jusqu'a 10h30 puis palier
        assert AE.n1_window_rate(serie, T0 + timedelta(minutes=30), 15) == pytest.approx(17720 / 15.0)

    def test_pas_de_marche_au_changement_d_heure(self):
        # En marches d'escalier, 09:55 -> 10:05 passait de 600 a 1200 (x2) :
        # c'est ce qui declenchait des alertes a HH:05 au rejeu.
        serie = [(T0 - timedelta(hours=1), 60, 600), (T0, 60, 1200)]
        avant = AE.n1_window_rate(serie, T0 - timedelta(minutes=5), 15)
        apres = AE.n1_window_rate(serie, T0 + timedelta(minutes=5), 15)
        assert apres / avant < 1.25

    def test_cinq_minutes(self):
        serie = [(T0 + timedelta(minutes=5 * i), 5, 10 * i) for i in range(6)]
        # [10:05, 10:20) -> 10 + 20 + 30 = 60 en 15 min = 240/h
        assert AE.n1_window_rate(serie, T0 + timedelta(minutes=12.5), 15) == pytest.approx(240)

    def test_hors_plage(self):
        serie = [(T0, 60, 1200)]
        assert AE.n1_window_rate(serie, T0 + timedelta(hours=5), 15) is None

    def test_n1_nul_rend_zero_et_pas_none(self):
        assert AE.n1_window_rate([(T0, 60, 0)], T0 + timedelta(minutes=30), 15) == 0


class TestN1Source:
    def test_trou_comble_par_des_zeros(self):
        s = AE._densify([(T0, 10), (T0 + timedelta(minutes=15), 20)], 5)
        assert [c for _, _, c in s] == [10, 0, 0, 20]

    def test_archive_hsh_prioritaire(self):
        # hsh_archive_tx_<event>_<annee> : 5 min, entrees seules, noms normalises
        arch = [{"gate_name": "Porte Est", "tranche": T0, "entrees": 30, "sorties": 99},
                {"gate_name": "PORTE EST", "tranche": T0 + timedelta(minutes=10), "entrees": 40, "sorties": 0}]
        db = DB(**{"hsh_archive_tx_24H_AUTOS_2025": arch,
                   "historique_controle": [{"type": "portes", "event": "24H AUTOS", "year": 2025,
                                            "doors": [{"name": "PORTE EST", "scans": []}]}]})
        series, src = AE.load_n1_door_series(db, "24H AUTOS", 2025, T0 - timedelta(hours=1), T0 + timedelta(hours=1))
        assert src == "hsh_archive_tx"
        assert series["PORTE EST"] == [(T0, 5, 30), (T0 + timedelta(minutes=5), 5, 0),
                                       (T0 + timedelta(minutes=10), 5, 40)]

    def test_repli_historique_controle(self):
        db = DB(historique_controle=[{"type": "portes", "event": "24H AUTOS", "year": 2025, "doors": [
            {"name": "PORTE EST", "scans": [{"timestamp": T0, "scan_count": 100},
                                            {"timestamp": T0 + timedelta(hours=2), "scan_count": 50}]}]}])
        series, src = AE.load_n1_door_series(db, "24H AUTOS", 2025, T0 - timedelta(hours=1), T0 + timedelta(hours=3))
        assert src == "historique_controle"
        assert [c for _, d, c in series["PORTE EST"]] == [100, 0, 50]

    def test_aucune_source(self):
        assert AE.load_n1_door_series(DB(), "24H AUTOS", 2025, T0, T0 + timedelta(hours=1)) == ({}, None)


class TestForecast:
    def test_profil_n1(self):
        m, pred = AE.forecast_door(1000, 30, n1_now=500, n1_future={5: 600, 10: 800, 15: 1000,
                                                                    20: 1000, 25: 1000, 30: 900})
        assert m == "profil_n1"
        assert dict(pred)[15] == pytest.approx(2000)

    def test_profil_borne_par_max_growth(self):
        m, pred = AE.forecast_door(1000, 10, n1_now=150, n1_future={5: 3000, 10: 3000}, max_growth=3)
        assert dict(pred)[5] == pytest.approx(3000)

    def test_n1_nul_maintenant_bascule_sur_tendance(self):
        # N-1 a zero a cette heure (porte fermee l'an dernier) : pas de
        # division par zero, repli sur la tendance.
        m, pred = AE.forecast_door(600, 15, n1_now=0, n1_future={5: 900}, slope=10, lead_min=7.5,
                                   trend_max_growth=1.5)
        assert m == "tendance"
        assert dict(pred)[5] == pytest.approx(600 + 10 * 12.5)
        assert dict(pred)[15] == pytest.approx(min(600 + 10 * 22.5, 900))

    def test_n1_sous_le_seuil_de_bruit(self):
        m, _ = AE.forecast_door(600, 15, n1_now=50, n1_future={5: 900}, slope=0, min_n1_rate=120)
        assert m == "tendance"

    def test_rien_sans_debit(self):
        assert AE.forecast_door(0, 30, n1_now=500, n1_future={5: 900}) == (None, [])

    def test_tendance_negative_bornee_a_zero(self):
        _, pred = AE.forecast_door(100, 30, slope=-50)
        assert min(r for _, r in pred) == 0


class TestAlignement:
    def test_arrondi_a_la_semaine(self):
        from datetime import date
        # 2025 : race = depart (samedi 14/06) ou arrivee (dimanche 15/06)
        assert AE.n1_day_offset(date(2026, 6, 13), date(2025, 6, 14)) == 364
        assert AE.n1_day_offset(date(2026, 6, 13), date(2025, 6, 15)) == 364
        assert AE.n1_day_offset(None, date(2025, 6, 15)) is None

    def test_normalisation_noms(self):
        assert AE._door_norm("Portail Houx 5") == AE._door_norm("PORTE HOUX 5")
        assert AE._door_norm("PORTE KARTING PIETONS") == AE._door_norm("porte karting pieton")
        assert AE._door_norm("Porte  Véhicules") == "PORTE VEHICULE"

    def test_type_appareil(self):
        assert AE._device_kind("TRI-EST-11") == "tripode"
        assert AE._device_kind("PDA.225") == "pda"
        assert AE._device_kind("X") == "autre"


# ---------------------------------------------------------------------------
# Handler complet (double Mongo)
# ---------------------------------------------------------------------------

def _now(label):
    """Heure de Paris naive -> UTC consciente (+30 s, comme le cycle)."""
    return label.replace(tzinfo=PARIS).astimezone(timezone.utc) + timedelta(seconds=30)


def _tx(gate, cp, tranche, entrees, event="24H AUTOS"):
    return {"evenement": event, "gate_name": gate, "checkpoint_name": cp, "tranche": tranche,
            "entrees": entrees, "sorties": 0, "ok": entrees, "erreurs": 0}


def _live_docs(gate, counts, devices=("TRI-A-1", "TRI-A-2"), end=T0):
    """Comptes par 5 min (le dernier finit a `end`), repartis sur les appareils."""
    docs = []
    n = len(counts)
    for i, c in enumerate(counts):
        t = end - timedelta(minutes=5 * (n - i))
        for j, cp in enumerate(devices):
            docs.append(_tx(gate, cp, t, c // len(devices) + (1 if j < c % len(devices) else 0)))
    return docs


def _base_db(live, n1_counts=None, global_actif=True):
    cols = {
        "hsh_transactions_agg": live,
        "data_access": [{"_id": "___GLOBAL___", "live_controle_actif": global_actif,
                         "evenement": "24H AUTOS"}],
        "parametrages": [{"event": "24H AUTOS", "year": "2026",
                          "data": {"globalHoraires": {"race": "2026-06-13T14:00:00.000Z"}}}],
        "cockpit_active_alerts": [],
    }
    if n1_counts is not None:
        # N-1 : historique_controle horaire, course samedi 14/06/2025
        scans = [{"timestamp": datetime(2025, 6, 14, h), "scan_count": c} for h, c in n1_counts.items()]
        cols["historique_controle"] = [
            {"event": "24H AUTOS", "year": 2025, "type": "portes", "race": "2025-06-14T16:00:00",
             "doors": [{"name": "PORTE EST", "scans": scans}]}]
    return DB(**cols)


DEF = {"slug": "door-sat", "params": {}}


class TestHandler:
    def test_base_vide(self):
        db = _base_db([])
        assert AE.detect_door_saturation_forecast(DEF, {"now": _now(T0), "db": db}) is None

    def test_live_controle_inactif(self):
        db = _base_db(_live_docs("PORTE EST", [150] * 12), global_actif=False)
        assert AE.detect_door_saturation_forecast(DEF, {"now": _now(T0), "db": db}) is None

    def test_collecteur_muet(self):
        # Derniere tranche il y a 40 min : on ne previent pas sur des donnees perimees
        db = _base_db(_live_docs("PORTE EST", [300] * 12, end=T0 - timedelta(minutes=40)))
        assert AE.detect_door_saturation_forecast(DEF, {"now": _now(T0), "db": db}) is None

    def test_nuit_debit_faible(self):
        # 2 tripodes = 1800/h de capacite ; 20 passages / 5 min = 240/h < min_rate
        night = datetime(2026, 6, 13, 3, 0)
        db = _base_db(_live_docs("PORTE EST", [20] * 12, end=night))
        assert AE.detect_door_saturation_forecast(DEF, {"now": _now(night), "db": db}) is None

    def test_profil_n1_annonce_la_saturation(self):
        # Debit courant 120/5 min = 1440/h (80 % de 1800/h) ; l'an dernier la
        # porte passait de 1000/h a 10h a 1500/h a 10h30 : prevu ~2160/h.
        db = _base_db(_live_docs("PORTE EST", [120] * 12), n1_counts={9: 800, 10: 1000, 11: 1500})
        res = AE.detect_door_saturation_forecast(DEF, {"now": _now(T0), "db": db,
                                                       "event": "24H AUTOS", "year": "2026"})
        assert res and len(res) == 1
        a = res[0]
        ad = a["actionData"]
        assert a["title"] == "SATURATION PORTE PREVUE"
        assert ad["door"] == "PORTE EST"
        assert ad["method"] == "profil_n1"
        assert ad["capacity"] == 1800
        assert ad["current_rate"] == 1440
        assert ad["predicted_rate"] >= 0.9 * 1800
        assert ad["n1_year"] == 2025 and ad["n1_source"] == "historique_controle"
        assert re.match(r"^PORTE EST : ~\d+/h prevu vers \d\d:\d\d \(capacite 1800/h\)$", a["message"])
        assert a["expiresAt"] - a["triggeredAt"] == timedelta(minutes=30)
        # Lecture seule
        assert db["cockpit_active_alerts"].writes == 0

    def test_profil_n1_calme_pas_d_alerte(self):
        # Meme debit courant, mais l'an dernier le flux RETOMBAIT : rien.
        db = _base_db(_live_docs("PORTE EST", [120] * 12), n1_counts={9: 1500, 10: 1000, 11: 600})
        assert AE.detect_door_saturation_forecast(DEF, {"now": _now(T0), "db": db}) is None

    def test_constate(self):
        db = _base_db(_live_docs("PORTE EST", [150] * 12))
        res = AE.detect_door_saturation_forecast(DEF, {"now": _now(T0), "db": db})
        assert res and res[0]["actionData"]["method"] == "constate"
        assert "des maintenant" in res[0]["message"]

    def test_dedup_par_porte(self):
        db = _base_db(_live_docs("PORTE EST", [150] * 12))
        db["cockpit_active_alerts"].docs.append({
            "definition_slug": "door-sat", "actionData": {"door_key": "PORTE EST"},
            "triggeredAt": (_now(T0) - timedelta(minutes=10)).replace(tzinfo=None)})
        assert AE.detect_door_saturation_forecast(DEF, {"now": _now(T0), "db": db}) is None

    def test_surcharge_de_capacite(self):
        db = _base_db(_live_docs("PORTE EST", [150] * 12))
        d = {"slug": "door-sat", "params": {"capacities": {"Porte Est": 5000}}}
        assert AE.detect_door_saturation_forecast(d, {"now": _now(T0), "db": db}) is None

    def test_services_exclus(self):
        db = _base_db(_live_docs("HELPDESK", [150] * 12))
        assert AE.detect_door_saturation_forecast(DEF, {"now": _now(T0), "db": db}) is None

    def test_filtre_portes(self):
        db = _base_db(_live_docs("PORTE EST", [150] * 12))
        d = {"slug": "door-sat", "params": {"doors": ["PORTE SUD"]}}
        assert AE.detect_door_saturation_forecast(d, {"now": _now(T0), "db": db}) is None

    def test_tendance_sans_n1(self):
        # Montee reguliere, pas de N-1 : la tendance (bornee a x1.5) annonce
        counts = [50, 55, 60, 70, 80, 90, 95, 100, 105, 110, 112, 115]
        db = _base_db(_live_docs("PORTE EST", counts))
        d = {"slug": "door-sat", "params": {"trend_fallback": True}}
        res = AE.detect_door_saturation_forecast(d, {"now": _now(T0), "db": db})
        assert res and res[0]["actionData"]["method"] == "tendance"
        assert res[0]["actionData"]["minutes_ahead"] > 0

    def test_tendance_desactivable(self):
        counts = [50, 55, 60, 70, 80, 90, 95, 100, 105, 110, 112, 115]
        db = _base_db(_live_docs("PORTE EST", counts))
        d = {"slug": "door-sat", "params": {"trend_fallback": False}}
        assert AE.detect_door_saturation_forecast(d, {"now": _now(T0), "db": db}) is None

    def test_enregistre_dans_handlers(self):
        assert AE.HANDLERS["door_saturation_forecast"] is AE.detect_door_saturation_forecast
