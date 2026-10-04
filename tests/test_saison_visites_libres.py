"""SAISON avec jours publics = jours de VISITES LIBRES (02/10/2026).

Le parametrage SAISON/<annee> peut desormais porter des jours publics
(globalHoraires.dates). SAISON reste reconnu par son NOM : ni montage, ni
demontage, ni course, ni billetterie, jamais une edition. Contre un double
Mongo : jamais la vraie base.
"""

import copy
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("TITAN_ENV", "dev")
import event_courant as EC  # noqa: E402
import live_frequentation as LF  # noqa: E402
import merge  # noqa: E402
import momentus_lieux as ML  # noqa: E402
import pcorg_summary as PS  # noqa: E402
from test_alert_engine_door_saturation import DB, Coll, _match  # noqa: E402

PARIS = ZoneInfo("Europe/Paris")


def _utc(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=PARIS).astimezone(timezone.utc)


SAISON_GH = {
    "dates": [
        {"date": "2026-10-03", "openTime": "10:00", "closeTime": "18:00", "is24h": False},
        {"date": "2026-10-04", "openTime": "10:00", "closeTime": "18:00", "is24h": False},
        {"date": "2026-12-20", "openTime": "09:30", "closeTime": "17:00", "is24h": False},
    ],
    # Saisie parasite : doit rester ignoree pour SAISON
    "montage": {"start": "2026-10-01T06:00:00Z", "end": "2026-10-02T18:00:00Z"},
    "demontage": {"start": "2026-10-05T06:00:00Z", "end": "2026-10-06T18:00:00Z"},
    "ticketing": [],
}
SAISON = {"event": "SAISON", "year": "2026", "data": {"globalHoraires": SAISON_GH}}


class TimetableColl(Coll):
    """Timetable avec un vrai update_one ($set + $inc, upsert) pour le merge.
    Lectures en copie profonde, comme un vrai driver."""

    def find(self, query=None, projection=None):
        return [copy.deepcopy(d) for d in super().find(query)]

    def update_one(self, query, update, upsert=False):
        self.writes += 1
        doc = next((d for d in self.docs if _match(d, query)), None)
        if doc is None:
            if not upsert:
                return
            doc = dict(query)
            self.docs.append(doc)
        for k, v in (update.get("$set") or {}).items():
            doc[k] = v
        for k, v in (update.get("$inc") or {}).items():
            doc[k] = doc.get(k, 0) + v


class TDB(DB):
    def __init__(self, **cols):
        tt = cols.pop("timetable", [])
        super().__init__(**cols)
        self.cols["timetable"] = TimetableColl(tt)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self[name]


def _db(*params, **cols):
    EC.invalidate()
    return TDB(parametrages=[SAISON] + list(params), **cols)


class TestEventCourant:
    def test_saison_avec_dates_jamais_epreuve(self):
        db = _db()
        assert all(w["event"] != "SAISON" for w in EC.windows(db))
        acts = EC.active_events(db, _utc(2026, 10, 3))
        assert [a["kind"] for a in acts] == ["saison"]

    def test_jour_de_visites_libres(self):
        db = _db()
        ok, info = EC.is_saison_public_day(db, "2026-10-03", _utc(2026, 10, 3))
        assert ok and info["open"] == "10:00" and info["close"] == "18:00"
        assert EC.is_saison_public_day(db, "2026-10-05", _utc(2026, 10, 5)) == (False, None)


class TestMerge:
    def test_vignettes_visites_libres_sans_montage(self):
        vs = merge.process_global_horaires(SAISON_GH, "SAISON", "2026", db=_db())
        acts = sorted({v["activity"] for v in vs})
        assert acts == ["Fermeture au public", "Ouverture au public"]
        assert len(vs) == 6
        opens = [v for v in vs if v["phase"] == "open"]
        # Sans drapeau visite_libre / visite_guidee : les deux sont autorisees
        assert all(v["remark"] == "Visites libres et guidees" for v in opens)
        assert all(v["origin"] == "parametrage" for v in vs)

    def test_type_de_visite_par_jour(self):
        gh = {"dates": [
            {"date": "2026-10-03", "openTime": "10:00", "closeTime": "18:00", "visite_guidee": False},
            {"date": "2026-10-04", "openTime": "10:00", "closeTime": "18:00", "visite_libre": False},
        ]}
        vs = merge.process_global_horaires(gh, "SAISON", "2026", db=_db())
        rem = {v["date"]: v["remark"] for v in vs if v["phase"] == "open"}
        assert rem == {"2026-10-03": "Visites libres", "2026-10-04": "Visites guidees"}

    def test_jour_guidee_seule_pas_visite_libre(self):
        saison = copy.deepcopy(SAISON)
        saison["data"]["globalHoraires"]["dates"].append(
            {"date": "2026-10-10", "openTime": "10:00", "closeTime": "18:00", "visite_libre": False})
        EC.invalidate()
        db = TDB(parametrages=[saison])
        assert "2026-10-10" not in EC.saison_public_days(db, 2026)
        assert EC.saison_visit_days(db, 2026)["2026-10-10"]["visite_guidee"] is True

    def test_epreuve_inchangee(self):
        vs = merge.process_global_horaires(SAISON_GH, "24H CAMIONS", "2026", db=_db())
        acts = {v["activity"] for v in vs}
        assert "Debut du montage" in acts and "Demontage" in acts
        assert all(v.get("remark") != "Visites libres" for v in vs)

    def test_idempotent_et_momentus_intact(self):
        momentus = {"_id": "m1", "date": "2026-10-03", "activity": "Seminaire X",
                    "origin": "momentus", "start": "09:00"}
        ancien_montage = {"_id": "old", "date": "2026-10-01", "activity": "Debut du montage",
                          "origin": "parametrage", "param_id": "montage"}
        db = _db(timetable=[{"event": "SAISON", "year": "2026", "version": 3,
                             "data": {"2026-10-03": [momentus], "2026-10-01": [ancien_montage]}}])
        assert merge.run_merge(db, "SAISON", "2026")["ok"]
        doc = db["timetable"].docs[0]
        first = {d: sorted(x["_id"] for x in items) for d, items in doc["data"].items()}
        assert "2026-10-01" not in doc["data"]  # ancien montage purge
        assert any(x.get("origin") == "momentus" and x["_id"] == "m1"
                   for x in doc["data"]["2026-10-03"])
        assert sum(1 for x in doc["data"]["2026-10-03"] if x["activity"] == "Ouverture au public") == 1
        # Une remarque operateur survit au merge suivant
        for x in doc["data"]["2026-10-03"]:
            if x["activity"] == "Ouverture au public":
                x["remark"], x["remark_manual"] = "Navette musee", True
        merge.run_merge(db, "SAISON", "2026")
        doc = db["timetable"].docs[0]
        assert {d: sorted(x["_id"] for x in items) for d, items in doc["data"].items()} == first
        assert next(x for x in doc["data"]["2026-10-03"]
                    if x["activity"] == "Ouverture au public")["remark"] == "Navette musee"
        assert doc["version"] == 5


class TestRapports:
    def test_attendance_none_hors_visites(self):
        assert PS.compute_attendance_block(_db(), "SAISON", 2026, now_utc=_utc(2026, 11, 10)) is None

    def test_attendance_sans_billetterie_un_jour_de_visites(self):
        att = PS.compute_attendance_block(_db(), "SAISON", 2026, now_utc=_utc(2026, 10, 3, 8))
        assert att["slots"] == [] and att["saison"] is True
        assert [v["slot"] for v in att["visites_libres"]] == ["today", "tomorrow"]
        assert att["visites_libres"][0]["open"] == "10:00"

    def test_prompt_mentionne_visites_libres_sans_billetterie(self):
        att = PS.compute_attendance_block(_db(), "SAISON", 2026, now_utc=_utc(2026, 10, 3, 8))
        _, user = PS.build_prompts("SAISON", 2026, _utc(2026, 10, 2, 7), _utc(2026, 10, 3, 7),
                                   {"total": 0}, [], False, attendance=att)
        assert "visites libres" in user
        assert "Billetterie & Frequentation (3 jours" not in user


class TestFenetres:
    def test_momentus_event_window_saison(self):
        assert ML.event_window(_db(), "SAISON", 2026) is None

    def test_momentus_event_window_epreuve_repli_jours_publics(self):
        p = {"event": "GPF", "year": "2026",
             "data": {"globalHoraires": {"dates": [{"date": "2026-10-03"}, {"date": "2026-10-04"}]}}}
        win = ML.event_window(_db(p), "GPF", 2026)
        assert win and win[0].isoformat() == "2026-10-03" and win[1].isoformat() == "2026-10-04"

    def test_live_frequentation_jamais_edition(self):
        assert LF.race_moment(_db(), "SAISON", 2026) == (None, None)
