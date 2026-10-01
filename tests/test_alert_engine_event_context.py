"""alert_engine.build_context branche sur event_courant (01/10/2026).

Contre un double Mongo (celui des tests door_saturation) : jamais la vraie
base, jamais le cycle complet.
"""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("TITAN_ENV", "dev")
import alert_engine as AE  # noqa: E402
import event_courant  # noqa: E402
import pcorg_summary as PS  # noqa: E402
from test_alert_engine_door_saturation import DB  # noqa: E402

PARIS = ZoneInfo("Europe/Paris")


def _utc(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=PARIS).astimezone(timezone.utc)


def _param(event, year, montage, demontage, public_days, race=None):
    gh = {"montage": {"start": montage}, "demontage": {"end": demontage},
          "dates": [{"date": d, "openTime": "08:00", "closeTime": "20:00"} for d in public_days]}
    if race:
        gh["race"] = race
    return {"event": event, "year": str(year), "data": {"globalHoraires": gh}}


def _db(*params):
    event_courant.invalidate()
    return DB(parametrages=[{"event": "SAISON", "year": "2026", "data": {}}] + list(params))


A = _param("24H CAMIONS", 2026, "2026-09-15T06:00:00Z", "2026-09-30T18:00:00Z",
           ["2026-09-25", "2026-09-26", "2026-09-27"], race="2026-09-27T12:00:00Z")
B = _param("GPF", 2026, "2026-09-20T06:00:00Z", "2026-10-05T18:00:00Z",
           ["2026-10-03", "2026-10-04"], race="2026-10-04T12:00:00Z")
ABERRANT = _param("24H AUTOS", 2024, "2024-06-01T06:00:00Z", "2026-07-01T18:00:00Z",
                  ["2024-06-15"], race="2024-06-16T14:00:00Z")


class TestBuildContext:
    def test_saison_hors_epreuve(self):
        ctx = AE.build_context(_db(A, ABERRANT), sim_time=_utc(2026, 11, 10))
        assert (ctx["event"], ctx["year"]) == ("SAISON", "2026")
        assert ctx["event_kind"] == "saison" and ctx["phase"] is None
        assert "globalHoraires" not in ctx

    def test_epreuve_montage(self):
        ctx = AE.build_context(_db(A), sim_time=_utc(2026, 9, 16))
        assert (ctx["event"], ctx["year"], ctx["phase"]) == ("24H CAMIONS", "2026", "montage")
        assert ctx["globalHoraires"]["dates"]

    def test_chevauchement_priorite_jours_publics(self):
        # 26/09 : A en jours publics, B en montage -> A, quel que soit l'ordre Mongo
        for params in ((A, B), (B, A)):
            ctx = AE.build_context(_db(*params), sim_time=_utc(2026, 9, 26))
            assert ctx["event"] == "24H CAMIONS" and ctx["phase"] == "public"

    def test_chevauchement_course_la_plus_proche(self):
        # 29/09 : A en demontage, B en montage -> phases ex aequo, course la plus proche (A)
        ctx = AE.build_context(_db(A, B), sim_time=_utc(2026, 9, 29))
        assert ctx["event"] == "24H CAMIONS"
        ctx = AE.build_context(_db(A, B), sim_time=_utc(2026, 10, 2))
        assert ctx["event"] == "GPF"

    def test_fenetre_aberrante_ignoree(self):
        ctx = AE.build_context(_db(ABERRANT), sim_time=_utc(2025, 8, 1))
        assert ctx["event"] == "SAISON"


class TestDetecteursSaison:
    def test_horaires_off_sur_saison(self):
        ctx = {"now": _utc(2026, 9, 26, 7, 50), "event": "SAISON", "year": "2026",
               "event_kind": "saison",
               "globalHoraires": A["data"]["globalHoraires"]}  # meme avec des horaires
        d = {"slug": "s", "params": {"minutes_before": 30, "schedule_event": "open"}}
        assert AE.detect_schedule_proximity(d, ctx) is None
        assert AE.detect_schedule_transition({"slug": "t", "params": {}}, ctx) is None

    def test_horaires_on_sur_epreuve(self):
        ctx = {"now": _utc(2026, 9, 26, 7, 50), "event": "24H CAMIONS", "year": "2026",
               "event_kind": "epreuve", "globalHoraires": A["data"]["globalHoraires"]}
        d = {"slug": "s", "params": {"minutes_before": 30, "schedule_event": "open"}}
        res = AE.detect_schedule_proximity(d, ctx)
        assert res and res["title"] == "OUVERTURE IMMINENTE"

    def test_saturation_porte_skip_sur_saison(self):
        db = DB(data_access=[{"_id": "___GLOBAL___", "live_controle_actif": True,
                              "evenement": "24H AUTOS"}])

        def _interdit(*a, **k):
            raise AssertionError("aucune lecture des passages sur SAISON")
        db["hsh_transactions_agg"].find = _interdit
        db["data_access"].find_one = _interdit
        ctx = {"now": _utc(2026, 11, 10), "db": db, "event": "SAISON", "year": "2026",
               "event_kind": "saison"}
        assert AE.detect_door_saturation_forecast({"slug": "d", "params": {}}, ctx) is None


class TestPcorgSummarySaison:
    def test_detect_active_event_delegue(self):
        assert PS.detect_active_event(_db(A, B), now_utc=_utc(2026, 9, 26)) == ("24H CAMIONS", 2026)
        assert PS.detect_active_event(_db(A), now_utc=_utc(2026, 11, 10)) == ("SAISON", 2026)

    def test_phase_none_pour_saison(self):
        assert PS.detect_event_phase(_db(), "SAISON", 2026, _utc(2026, 11, 9), _utc(2026, 11, 10)) is None

    def test_year_match(self):
        assert PS._year_match("24H CAMIONS", "2026") == 2026
        assert PS._year_match("SAISON", 2026) == {"$in": [2025, 2026]}

    def test_n1_saison_memes_dates(self):
        t0, t1 = _utc(2026, 1, 1, 7), _utc(2026, 1, 2, 7)
        fiches = [{"event": "SAISON", "year": 2025, "ts": _utc(2025, 1, 1, 9), "category": "PCO.Secours"},
                  {"event": "SAISON", "year": 2024, "ts": _utc(2024, 12, 31, 23), "category": "PCO.Flux"}]
        db = DB(pcorg=fiches)
        db["pcorg"].aggregate = lambda pipe: []
        out = PS.compute_comparisons(db, "SAISON", 2026, t0, t1)
        py = out["prev_year_aligned"]
        assert py["year_prev"] == 2025 and py["race_dt_n"] is None
        assert py["period_start"].startswith("2025-01-01T06:00")
        assert py["kpis"]["total"] == 1

    def test_same_dates_29_fevrier(self):
        assert PS._same_dates_prev_year(_utc(2028, 2, 29, 10)) == _utc(2027, 2, 28, 10)
