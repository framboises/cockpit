"""Indicateurs de la barre des jours de la timeline SAISON (saison_indicateurs.py).

Contre un double Mongo minimal : jamais la vraie base. Le double ignore les
filtres des requetes Momentus (il rend tout) : le module refait tous les
controles en Python, c'est ce qu'on verifie ici.
"""

import copy
import os
from datetime import date

os.environ.setdefault("TITAN_ENV", "dev")
import saison_indicateurs as SI  # noqa: E402


class FakeColl:
    def __init__(self, docs=None):
        self.docs = [copy.deepcopy(d) for d in (docs or [])]

    def find(self, query=None, projection=None):
        return [copy.deepcopy(d) for d in self.docs]

    def find_one(self, query=None, projection=None):
        query = query or {}
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                return copy.deepcopy(d)
        return None

    @staticmethod
    def _match(d, query):
        for k, v in query.items():
            if isinstance(v, dict) and "$exists" in v:
                if (k in d) != bool(v["$exists"]):
                    return False
            elif d.get(k) != v:
                return False
        return True

    def update_one(self, query, update, upsert=False):
        doc = next((d for d in self.docs if self._match(d, query)), None)
        if doc is None:
            if not upsert:
                return
            doc = dict(query)
            doc.update(copy.deepcopy(update.get("$setOnInsert") or {}))
            self.docs.append(doc)
        doc.update(copy.deepcopy(update.get("$set") or {}))


class FakeDB:
    name = "fake"

    def __init__(self, **cols):
        self.cols = {k: FakeColl(v) for k, v in cols.items()}

    def __getitem__(self, name):
        return self.cols.setdefault(name, FakeColl())


def _ev(_id, name, room, start, end, **flags):
    return {"_id": _id, "id": _id, "name": name, "eventTypeName": "Epreuve sportive",
            "contactRoles": [{"name": "SECRET CLIENT", "email": "x@y.fr"}], "expectedRevenue": 99999,
            "bookedSpaces": [{"roomId": room, "roomName": "Salle " + room, "startDate": start, "endDate": end,
                              "isAllDay": True, "usageType": "moveIn"}],
            "_sync": {"deleted_at": None}, **flags}


SAISON_PARAM = {"event": "SAISON", "year": "2026", "data": {"globalHoraires": {"dates": [
    {"date": "2026-10-03", "openTime": "10:00", "closeTime": "18:00", "visite_libre": True, "visite_guidee": False},
    {"date": "2026-10-04", "openTime": "09:00", "closeTime": "17:00", "visite_libre": False, "visite_guidee": True},
    {"date": "2026-10-05", "openTime": "09:00", "closeTime": "17:00"},
]}}}


def _db(events=None, functions=None):
    return FakeDB(momentus_events=events or [], momentus_functions=functions or [],
                  momentus_rooms=[{"_id": "room-833-A", "id": "room-833-A", "name": "Piste BUGATTI",
                                   "venueName": "PISTES"}],
                  parametrages=[SAISON_PARAM], cockpit_settings=[])


D1, D3 = date(2026, 10, 3), date(2026, 10, 5)


def _by_id(row):
    return {x["id"]: x for x in row}


def test_order_is_fixed_and_follows_config():
    res = SI.compute(_db(), D1, D3, SI.DEFAULT_INDICATORS)
    assert list(res) == ["2026-10-03", "2026-10-04", "2026-10-05"]
    ids = [it["id"] for it in SI.DEFAULT_INDICATORS]
    for row in res.values():
        assert [x["id"] for x in row] == ids


def test_disabled_indicator_is_dropped():
    cfg = copy.deepcopy(SI.DEFAULT_INDICATORS)
    cfg[2]["enabled"] = False
    res = SI.compute(_db(), D1, D1, cfg)
    assert "bugatti" not in [x["id"] for x in res["2026-10-03"]]


def test_visit_kinds():
    res = SI.compute(_db(), D1, D3, SI.DEFAULT_INDICATORS)
    d3, d4, d5 = (_by_id(res[d]) for d in ("2026-10-03", "2026-10-04", "2026-10-05"))
    assert d3["visites_libres"]["active"] and not d3["visites_guidees"]["active"]
    assert d3["visites_libres"]["details"][0]["start"] == "10:00"
    assert not d4["visites_libres"]["active"] and d4["visites_guidees"]["active"]
    # drapeaux absents = autorises
    assert d5["visites_libres"]["active"] and d5["visites_guidees"]["active"]


def test_blackout_counts_and_is_flagged():
    db = _db(events=[_ev("event-1-A", "IAME", "room-107-A", "2026-10-03", "2026-10-04", isBlackout=True)])
    res = SI.compute(db, D1, D3, SI.DEFAULT_INDICATORS)
    cik = _by_id(res["2026-10-03"])["karting_cik"]
    assert cik["active"]
    assert cik["details"][0]["blackout"] is True
    assert cik["eids"] == ["event-1-A"]
    assert _by_id(res["2026-10-04"])["karting_cik"]["active"]
    assert not _by_id(res["2026-10-05"])["karting_cik"]["active"]


def test_canceled_lost_prospect_deleted_are_excluded():
    db = _db(events=[
        _ev("event-1-A", "Annule", "room-833-A", "2026-10-03", "2026-10-03", isCanceled=True),
        _ev("event-2-A", "Perdu", "room-833-A", "2026-10-03", "2026-10-03", isLost=True),
        _ev("event-3-A", "Prospect", "room-833-A", "2026-10-03", "2026-10-03", isProspect=True),
        dict(_ev("event-4-A", "Supprime", "room-833-A", "2026-10-03", "2026-10-03"),
             _sync={"deleted_at": "2026-10-01"}),
    ])
    res = SI.compute(db, D1, D1, SI.DEFAULT_INDICATORS)
    assert not _by_id(res["2026-10-03"])["bugatti"]["active"]


def test_function_hours_and_no_sensitive_fields():
    db = _db(events=[_ev("event-5-A", "Roulage Club", "room-834-A", "2026-10-03", "2026-10-03", isDefinite=True)],
             functions=[{"_id": "f1", "eventId": "event-5-A", "roomId": "room-834-A", "roomName": "Piste MAISON BLANCHE",
                         "startDate": "2026-10-03", "endDate": "2026-10-03", "startTime": "08:30",
                         "endTime": "12:00", "_sync": {"deleted_at": None}}])
    res = SI.compute(db, D1, D1, SI.DEFAULT_INDICATORS)
    mb = _by_id(res["2026-10-03"])["maison_blanche"]
    assert mb["active"] and len(mb["details"]) == 1
    det = mb["details"][0]
    assert (det["start"], det["end"], det["status"], det["blackout"]) == ("08:30", "12:00", "confirme", False)
    flat = repr(res)
    assert "SECRET" not in flat and "x@y.fr" not in flat and "99999" not in flat


def test_room_outside_indicators_is_ignored():
    db = _db(events=[_ev("event-6-A", "Seminaire", "room-2-A", "2026-10-03", "2026-10-03")])
    res = SI.compute(db, D1, D1, SI.DEFAULT_INDICATORS)
    assert not any(x["active"] for x in res["2026-10-03"] if x["id"] not in ("visites_libres",))


def test_validation_accepts_defaults():
    clean, errors = SI.validate_config(copy.deepcopy(SI.DEFAULT_INDICATORS))
    assert errors == [] and len(clean) == len(SI.DEFAULT_INDICATORS)


def test_validation_rejects_bad_input():
    base = copy.deepcopy(SI.DEFAULT_INDICATORS[2])
    cases = [
        dict(base, id="Bad Id"),
        dict(base, short="TROPLONG"),
        dict(base, color="red"),
        dict(base, rank="badge"),
        dict(base, enabled="yes"),
        dict(base, source={"type": "momentus_rooms", "room_ids": []}),
        dict(base, source={"type": "momentus_rooms", "room_ids": ["room-1-A\"><script>"]}),
        dict(base, source={"type": "visites", "kind": "nocturne"}),
        dict(base, source={"type": "sql"}),
        dict(base, icon="bad icon!"),
    ]
    for c in cases:
        clean, errors = SI.validate_config([c])
        assert errors and clean == [], c
    clean, errors = SI.validate_config([base, dict(base)])
    assert errors  # identifiant en double
    assert SI.validate_config("x")[1]
    assert SI.validate_config([base] * (SI.MAX_INDICATORS + 1))[1]


def test_validation_strips_markup_in_labels():
    c = dict(copy.deepcopy(SI.DEFAULT_INDICATORS[2]), label="<b>Bugatti</b>")
    clean, errors = SI.validate_config([c])
    assert not errors and "<" not in clean[0]["label"]


def test_seed_is_idempotent_and_never_overwrites():
    db = _db()
    SI.ensure_seed(db)
    assert len(db["cockpit_settings"].docs) == 1
    clean, errors = SI.save_config(db, SI.DEFAULT_INDICATORS[:2], "admin@x")
    assert not errors
    SI.ensure_seed(db)
    assert len(db["cockpit_settings"].docs) == 1
    assert [it["id"] for it in SI.get_config(db)] == ["visites_libres", "visites_guidees"]


# ---------------------------------------------------------------------------
# Seminaires
# ---------------------------------------------------------------------------
SEM_CFG = {"enabled": True, "types": ["Seminaire", "Activites seminaires", "Reception"], "max_list": 5}


def _sem(_id, name, typ, room, start, end, est=None, **flags):
    ev = _ev(_id, name, room, start, end, **flags)
    ev["eventTypeName"] = typ
    if est is not None:
        ev["estimatedTotalAttendance"] = est
    return ev


def _fn(_id, eid, room, day, st=None, et=None, **att):
    return {"_id": _id, "eventId": eid, "roomId": room, "roomName": "Salle " + room if room else None,
            "startDate": day, "endDate": day, "startTime": st, "endTime": et,
            "isAllDay": st is None, "_sync": {"deleted_at": None}, **att}


def test_norm_type():
    assert SI.norm_type("Séminaire ") == SI.norm_type("seminaire") == "seminaire"
    assert SI.norm_type("Activités  séminaires") == "activites seminaires"
    assert SI.norm_type("Réception") == "reception"
    assert SI.norm_type(None) == ""


def test_seminaires_types_normalized_and_others_ignored():
    db = _db(events=[
        _sem("event-1-A", "CPAM", "Séminaire ", "room-900-A", "2026-10-03", "2026-10-03", est=434),
        _sem("event-2-A", "Gala", "RÉCEPTION", "room-901-A", "2026-10-03", "2026-10-03", est=100),
        _sem("event-3-A", "IAME", "Epreuve sportive", "room-107-A", "2026-10-03", "2026-10-03", est=900),
    ])
    res = SI.compute_seminaires(db, D1, D3, SEM_CFG)
    assert list(res) == ["2026-10-03"]
    day = res["2026-10-03"]
    assert day["count"] == 2 and day["pers"] == 534
    assert [it["event"] for it in day["items"]] == ["CPAM", "Gala"]


def test_seminaires_canceled_lost_prospect_deleted_excluded():
    db = _db(events=[
        _sem("event-1-A", "A", "Seminaire", "room-900-A", "2026-10-03", "2026-10-03", isCanceled=True),
        _sem("event-2-A", "B", "Seminaire", "room-900-A", "2026-10-03", "2026-10-03", isLost=True),
        _sem("event-3-A", "C", "Seminaire", "room-900-A", "2026-10-03", "2026-10-03", isProspect=True),
        dict(_sem("event-4-A", "D", "Seminaire", "room-900-A", "2026-10-03", "2026-10-03"),
             _sync={"deleted_at": "2026-10-01"}),
    ])
    assert SI.compute_seminaires(db, D1, D3, SEM_CFG) == {}


def test_seminaires_attendance_functions_first_then_estimate_never_invented():
    db = _db(events=[
        _sem("event-1-A", "Fonctions", "Seminaire", "room-900-A", "2026-10-03", "2026-10-04", est=999),
        _sem("event-2-A", "Estime", "Seminaire", "room-901-A", "2026-10-03", "2026-10-03", est=40),
        _sem("event-3-A", "Inconnu", "Seminaire", "room-902-A", "2026-10-03", "2026-10-03", est=0),
    ], functions=[
        _fn("f1", "event-1-A", "room-900-A", "2026-10-03", "09:00", "12:00", guaranteedAttendance=120),
        _fn("f2", "event-1-A", "room-903-A", "2026-10-03", "14:00", "17:30", expectedAttendance=150),
        # note sans espace : effectif seulement, ne rend pas present
        _fn("f3", "event-1-A", None, "2026-10-05", agreedAttendance=7),
    ])
    res = SI.compute_seminaires(db, D1, D3, SEM_CFG)
    d3 = res["2026-10-03"]
    by = {it["event"]: it for it in d3["items"]}
    assert by["Fonctions"]["pers"] == 150                     # max des fonctions du jour
    assert (by["Fonctions"]["start"], by["Fonctions"]["end"]) == ("09:00", "17:30")
    assert by["Fonctions"]["place"] == "2 espaces"
    assert by["Estime"]["pers"] == 40 and by["Estime"]["place"] == "Salle room-901-A"
    assert by["Inconnu"]["pers"] is None
    assert d3["count"] == 3 and d3["pers"] == 190 and d3["pers_unknown"] == 1
    # 04/10 : espace reserve sans fonction -> estimation de l'evenement
    assert res["2026-10-04"]["items"][0]["pers"] == 999
    assert "2026-10-05" not in res
    flat = repr(res)
    assert "SECRET" not in flat and "x@y.fr" not in flat and "99999" not in flat


def test_seminaires_ordering_and_max_list():
    evs = [_sem(f"event-{i}-A", f"S{i}", "Seminaire", f"room-9{i}-A", "2026-10-03", "2026-10-03",
                est=i * 10 or None) for i in range(7)]
    res = SI.compute_seminaires(_db(events=evs), D1, D1, dict(SEM_CFG, max_list=3))
    day = res["2026-10-03"]
    # Liste COMPLETE (l'infobulle en montre max_list puis deplie le reste)
    assert [it["event"] for it in day["items"]] == ["S6", "S5", "S4", "S3", "S2", "S1", "S0"]
    assert day["count"] == 7 and day["more"] == 4 and day["pers"] == sum(range(10, 70, 10))
    # Cle client stable : accountId absent -> 'nom:<nom normalise>'
    assert day["items"][0]["client"] == "nom:s6" and day["items"][0]["account"] == "S6"


def test_seminaires_disabled_returns_nothing():
    db = _db(events=[_sem("event-1-A", "X", "Seminaire", "room-900-A", "2026-10-03", "2026-10-03", est=5)])
    assert SI.compute_seminaires(db, D1, D1, dict(SEM_CFG, enabled=False)) == {}


def test_seminaires_validation():
    clean, errors = SI.validate_seminaires(SEM_CFG)
    assert not errors and clean == SEM_CFG
    clean, errors = SI.validate_seminaires(dict(SEM_CFG, types=["Séminaire", "seminaire ", "<b>Reception</b>"]))
    assert not errors and len(clean["types"]) == 2 and "<" not in clean["types"][1]
    bad = [
        "x",
        dict(SEM_CFG, enabled="oui"),
        dict(SEM_CFG, types="Seminaire"),
        dict(SEM_CFG, types=[]),
        dict(SEM_CFG, types=[12]),
        dict(SEM_CFG, types=["x" * 61]),
        dict(SEM_CFG, types=["t%d" % i for i in range(SI.MAX_SEM_TYPES + 1)]),
        dict(SEM_CFG, max_list=0),
        dict(SEM_CFG, max_list=SI.MAX_SEM_LIST + 1),
        dict(SEM_CFG, max_list=True),
        dict(SEM_CFG, max_list="5"),
    ]
    for b in bad:
        clean, errors = SI.validate_seminaires(b)
        assert errors and clean is None, b
    # desactive sans type : accepte
    assert not SI.validate_seminaires(dict(SEM_CFG, enabled=False, types=[]))[1]


def test_seminaires_seed_added_without_touching_indicators():
    db = _db()
    db["cockpit_settings"].docs.append({"_id": SI.SETTINGS_ID, "indicators": copy.deepcopy(SI.DEFAULT_INDICATORS[:1])})
    assert SI.get_seminaires(db) == SI.DEFAULT_SEMINAIRES
    doc = db["cockpit_settings"].docs[0]
    assert doc["seminaires"] == SI.DEFAULT_SEMINAIRES
    assert [it["id"] for it in doc["indicators"]] == ["visites_libres"]
    # Enregistrement seminaires seul : indicateurs intacts ; invalide : refuse
    _, errors = SI.save_config(db, None, "admin@x", seminaires=dict(SEM_CFG, max_list=8))
    assert not errors and SI.get_seminaires(db)["max_list"] == 8
    assert [it["id"] for it in SI.get_config(db)] == ["visites_libres"]
    _, errors = SI.save_config(db, None, "admin@x", seminaires=dict(SEM_CFG, max_list=99))
    assert errors and SI.get_seminaires(db)["max_list"] == 8
    _, errors = SI.save_config(db, None, "admin@x")
    assert errors


def test_seminaires_meme_client_meme_jour_fusionne():
    # Deux reservations du meme client (accountId) le meme jour : une ligne,
    # plage horaire elargie, espaces cumules, effectif = le plus grand.
    a = _sem("e1", "ACME matin", "Seminaire", "r1", "2026-10-03", "2026-10-03", est=40, accountId="acc-1", accountName="ACME")
    b = _sem("e2", "ACME soir", "Seminaire", "r2", "2026-10-03", "2026-10-03", est=60, accountId="acc-1", accountName="ACME")
    c = _sem("e3", "AUTRE", "Seminaire", "r3", "2026-10-03", "2026-10-03", est=10, accountId="acc-2")
    fns = [_fn("f1", "e1", "r1", "2026-10-03", "08:00", "12:00"), _fn("f2", "e2", "r2", "2026-10-03", "18:00", "22:00")]
    res = SI.compute_seminaires(_db(events=[a, b, c], functions=fns), D1, D1, SEM_CFG)["2026-10-03"]
    assert res["count"] == 2 and res["pers"] == 70
    acme = next(i for i in res["items"] if i["reservations"] == 2)
    assert acme["event"] == "ACME (2 resa)"
    assert (acme["start"], acme["end"], acme["place"], acme["pers"]) == ("08:00", "22:00", "2 espaces", 60)
    assert (acme["client"], acme["account"]) == ("acc-1", "ACME")


# ---------------------------------------------------------------------------
# Programme d'un client (GET /api/saison/client)
# ---------------------------------------------------------------------------
def _prog_db():
    acme1 = _sem("e1", "ACME convention", "Seminaire", "r1", "2026-10-03", "2026-10-04", est=80,
                 accountId="acc-1", accountName="ACME", isDefinite=True,
                 totalActualRevenue=12345, staffAssignments=[{"name": "Commercial X"}])
    acme1["bookedSpaces"].append({"roomId": "r9", "roomName": "Parking VIP", "startDate": "2026-10-04",
                                  "endDate": "2026-10-04", "isAllDay": False, "startTime": "07:00",
                                  "endTime": "20:00", "usageType": "dark"})
    # Autre type (pas un seminaire) : fait partie du programme du client
    acme2 = _sem("e2", "ACME roulage", "Epreuve sportive", "room-833-A", "2026-10-05", "2026-10-05",
                 accountId="acc-1", accountName="ACME", isTentative=True)
    canceled = _sem("e3", "ACME annule", "Seminaire", "r1", "2026-10-03", "2026-10-03", accountId="acc-1",
                    isCanceled=True)
    lost = _sem("e4", "ACME perdu", "Seminaire", "r1", "2026-10-03", "2026-10-03", accountId="acc-1", isLost=True)
    prospect = _sem("e5", "ACME prospect", "Seminaire", "r1", "2026-10-03", "2026-10-03", accountId="acc-1",
                    isProspect=True)
    deleted = dict(_sem("e6", "ACME supprime", "Seminaire", "r1", "2026-10-03", "2026-10-03", accountId="acc-1"),
                   _sync={"deleted_at": "2026-10-01"})
    other = _sem("e7", "AUTRE", "Seminaire", "r1", "2026-10-03", "2026-10-03", accountId="acc-2")
    noacc = _sem("e8", "Gala  Été", "Reception", "r2", "2026-10-03", "2026-10-03")
    fns = [
        dict(_fn("f1", "e1", "r1", "2026-10-03", "09:00", "12:00", guaranteedAttendance=80),
             name="Pleniere", functionTypeName="Reunion"),
        dict(_fn("f2", "e1", "r2", "2026-10-03", "12:30", "14:00", expectedAttendance=95),
             name="Dejeuner", functionTypeName="Repas"),
        # Note sans espace : listee seulement les jours de presence
        dict(_fn("f3", "e1", None, "2026-10-03", agreedAttendance=7), name="Note globale"),
        dict(_fn("f4", "e1", None, "2026-10-07", agreedAttendance=7), name="Note hors presence"),
        dict(_fn("f5", "e3", "r1", "2026-10-03", "10:00", "11:00"), name="Fonction annulee"),
    ]
    return _db(events=[acme1, acme2, canceled, lost, prospect, deleted, other, noacc], functions=fns)


def test_client_programme_filters_and_grouping():
    res = SI.client_programme(_prog_db(), "acc-1", date(2026, 10, 1), date(2026, 10, 10))
    assert res["account"] == "ACME"
    assert [e["id"] for e in res["events"]] == ["e1", "e2"]      # tous types, exclus ecartes
    assert [d["date"] for d in res["days"]] == ["2026-10-03", "2026-10-04", "2026-10-05"]
    d3 = res["days"][0]
    names = [(i["kind"], i["name"], i["room"]) for i in d3["items"]]
    # r1 couvert par une fonction le 03 : pas de ligne 'espace' en double
    assert ("space", "", "Salle r1") not in names
    assert names[0][1] == "Note globale"           # journee entiere d'abord
    assert [n for _, n, _ in names[1:]] == ["Pleniere", "Dejeuner"]
    ple = d3["items"][1]
    assert (ple["start"], ple["end"], ple["ftype"], ple["pers"]) == ("09:00", "12:00", "Reunion", 80)
    assert d3["pers"] == 95
    # 04/10 : espaces sans fonction, phases
    d4 = {i["room"]: i for i in res["days"][1]["items"]}
    assert d4["Salle r1"]["kind"] == "space" and d4["Salle r1"]["phase"] == "reserve"
    assert (d4["Parking VIP"]["phase"], d4["Parking VIP"]["start"], d4["Parking VIP"]["end"]) == ("bloque", "07:00", "20:00")
    assert res["totals"] == {"days": 3, "events": 2, "max_pers": 95}
    assert all(i["name"] != "Note hors presence" for d in res["days"] for i in d["items"])
    assert all(i["name"] != "Fonction annulee" for d in res["days"] for i in d["items"])


def test_client_programme_no_pii_nor_amounts():
    res = SI.client_programme(_prog_db(), "acc-1", date(2026, 10, 1), date(2026, 10, 10))
    flat = repr(res)
    for bad in ("SECRET", "x@y.fr", "99999", "12345", "Commercial X", "contactRoles", "Revenue", "staff"):
        assert bad not in flat, bad


def test_client_programme_by_name_and_window():
    db = _prog_db()
    res = SI.client_programme(db, SI.parse_client("nom:gala ete"), date(2026, 10, 1), date(2026, 10, 10))
    assert [e["id"] for e in res["events"]] == ["e8"] and res["account"] == "Gala Été"
    # Hors fenetre : rien
    res = SI.client_programme(db, "acc-1", date(2026, 10, 6), date(2026, 10, 10))
    assert res["days"] == [] and res["totals"]["days"] == 0
    # Fenetre bornee aux jours demandes
    res = SI.client_programme(db, "acc-1", date(2026, 10, 4), date(2026, 10, 4))
    assert [d["date"] for d in res["days"]] == ["2026-10-04"]
    assert SI.client_programme(db, "acc-404", date(2026, 10, 1), date(2026, 10, 10))["events"] == []


# ---------------------------------------------------------------------------
# Recherche sur toute la saison (GET /api/saison/search)
# ---------------------------------------------------------------------------
def _search_db():
    sncf = _sem("e1", "Convention SNCF Voyageurs", "Séminaire", "r1", "2026-10-03", "2026-10-03", est=120,
                accountId="acc-sncf", accountName="SNCF Réseau", isDefinite=True,
                totalActualRevenue=12345, staffAssignments=[{"name": "Commercial X"}])
    sncf["bookedSpaces"][0]["roomName"] = "Salle Panoramique"
    # Meme client, autre evenement, autre jour, nom d'evenement sans 'SNCF'
    sncf2 = _sem("e2", "Roulage cadres", "Epreuve sportive", "room-833-A", "2026-10-05", "2026-10-05",
                 accountId="acc-sncf", accountName="SNCF Réseau", isTentative=True)
    sncf2["bookedSpaces"][0]["roomName"] = "Piste BUGATTI"
    other = _sem("e3", "Stage pilotage", "Activites seminaires", "room-833-A", "2026-10-04", "2026-10-04",
                 accountId="acc-2", accountName="ACME")
    other["bookedSpaces"][0]["roomName"] = "Piste BUGATTI"
    canceled = _sem("e4", "SNCF annule", "Seminaire", "r1", "2026-10-04", "2026-10-04",
                    accountId="acc-sncf", accountName="SNCF Réseau", isCanceled=True)
    # Lieu trouve par l'espace d'une FONCTION seulement
    karting = _sem("e5", "Team building", "Seminaire", "r7", "2026-10-06", "2026-10-06", accountId="acc-3",
                   accountName="Durand SA")
    karting["bookedSpaces"][0]["roomName"] = "Salon VIP"
    fns = [dict(_fn("f1", "e5", "room-107-A", "2026-10-06", "14:00", "16:00", guaranteedAttendance=30),
                roomName="Karting CIK")]
    return _db(events=[sncf, sncf2, other, canceled, karting], functions=fns)


S_FROM, S_TO, S_TODAY = date(2026, 10, 1), date(2026, 10, 31), date(2026, 10, 4)


def test_search_normalization_and_min_length():
    assert SI.norm_query("  SNCF   Réseau ") == "sncf reseau"
    assert SI.norm_query("é") is None and SI.norm_query(" ") is None and SI.norm_query(None) is None
    assert SI.norm_query("x" * 81) is None
    assert SI.norm_query("<script>ab") == "script ab"
    assert SI.search(_search_db(), "s", S_FROM, S_TO)["days"] == []


def test_search_client_by_account_and_name():
    res = SI.search(_search_db(), "sncf", S_FROM, S_TO, S_TODAY)
    days = {d["date"]: d for d in res["days"]}
    # 03 : evenement + compte ; 05 : compte seulement ; 04 (annule) : rien
    assert sorted(days) == ["2026-10-03", "2026-10-05"]
    h3 = days["2026-10-03"]["hits"][0]
    assert (h3["kind"], h3["client"], h3["account"], h3["pers"]) == ("client", "acc-sncf", "SNCF Réseau", 120)
    assert h3["rooms"] == ["Salle Panoramique"]
    h5 = days["2026-10-05"]["hits"][0]
    assert h5["kind"] == "client" and h5["label"] == "Roulage cadres" and h5["status"] == "option"
    s = res["summary"]
    assert s["days"] == 2 and s["first"] == "2026-10-03" and s["next"] == "2026-10-05"
    assert s["by_month"] == {"2026-10": 2}
    # Accents / casse : 'reseau' trouve 'Réseau'
    assert len(SI.search(_search_db(), "RESEAU", S_FROM, S_TO)["days"]) == 2


def test_search_lieu_by_booked_space_and_function_room():
    res = SI.search(_search_db(), "bugatti", S_FROM, S_TO, S_TODAY)
    days = {d["date"]: d["hits"] for d in res["days"]}
    assert sorted(days) == ["2026-10-04", "2026-10-05"]
    assert all(h["kind"] == "lieu" for hs in days.values() for h in hs)
    assert days["2026-10-04"][0]["rooms"] == ["Piste BUGATTI"] and days["2026-10-04"][0]["client"] == "acc-2"
    res = SI.search(_search_db(), "karting", S_FROM, S_TO, S_TODAY)
    assert [d["date"] for d in res["days"]] == ["2026-10-06"]
    h = res["days"][0]["hits"][0]
    assert (h["kind"], h["matched"], h["start"], h["end"], h["pers"]) == ("lieu", ["Karting CIK"], "14:00", "16:00", 30)
    # Plusieurs mots : tous doivent etre trouves
    assert SI.search(_search_db(), "piste bugatti", S_FROM, S_TO)["summary"]["days"] == 2
    assert SI.search(_search_db(), "piste zzz", S_FROM, S_TO)["summary"]["days"] == 0


def test_search_epreuve_and_visites(monkeypatch):
    monkeypatch.setattr(SI, "epreuves", lambda db, a, b: [
        {"event": "24H MOTOS", "year": "2027", "short": "24HM", "color": "#ff0000",
         "start": "2026-10-08", "end": "2026-10-10", "public_days": ["2026-10-09"]}])
    res = SI.search(_search_db(), "24h motos", S_FROM, S_TO, S_TODAY)
    hits = {d["date"]: d["hits"][0] for d in res["days"]}
    assert sorted(hits) == ["2026-10-08", "2026-10-09", "2026-10-10"]
    assert hits["2026-10-09"]["kind"] == "epreuve" and hits["2026-10-09"]["phase"] == "public"
    assert hits["2026-10-08"]["phase"] == "montage"
    res = SI.search(_search_db(), "visites guidees", S_FROM, S_TO, S_TODAY)
    assert {d["date"] for d in res["days"]} == {"2026-10-04", "2026-10-05"}
    assert all(h["kind"] == "visite" for d in res["days"] for h in d["hits"])
    # 'es' ne doit pas remonter tous les jours de visite
    assert all(h["kind"] != "visite" for d in SI.search(_search_db(), "es", S_FROM, S_TO)["days"] for h in d["hits"])


def test_search_no_pii_nor_amounts():
    flat = repr([SI.search(_search_db(), q, S_FROM, S_TO, S_TODAY) for q in ("sncf", "bugatti", "karting", "salle")])
    for bad in ("SECRET", "x@y.fr", "99999", "12345", "Commercial X", "contactRoles", "Revenue", "staff"):
        assert bad not in flat, bad


def test_search_limits_per_day(monkeypatch):
    evs = [_sem(f"e{i}", f"Client {i}", "Seminaire", f"r{i}", "2026-10-03", "2026-10-03", accountId=f"acc-{i}",
                accountName=f"Groupe {i}") for i in range(30)]
    monkeypatch.setattr(SI, "SEARCH_HITS_PER_DAY", 5)
    res = SI.search(_db(events=evs), "groupe", S_FROM, S_TO, S_TODAY)
    d = res["days"][0]
    assert d["count"] == 30 and len(d["hits"]) == 5 and d["more"] == 25
    assert res["summary"]["hits"] == 30


def test_search_window():
    today = date(2026, 10, 2)
    assert SI.search_window(None, None, today) == (date(2026, 9, 2), date(2027, 10, 2), None)
    assert SI.search_window("2026-01-01", "2027-02-04", today)[2] is None        # 400 j
    assert SI.search_window("2026-01-01", "2027-02-05", today)[2] == "periode_trop_longue"
    assert SI.search_window("2026-10-05", "2026-10-01", today)[2] == "periode_invalide"
    assert SI.search_window("demain", None, today)[2] == "periode_invalide"


def test_client_window_and_key_validation():
    today = date(2026, 10, 2)
    a, b, err = SI.client_window(None, None, today)
    assert (a, b, err) == (date(2026, 9, 25), date(2026, 12, 31), None)
    assert SI.client_window("2026-01-01", "2026-06-29", today)[2] is None        # 180 j
    assert SI.client_window("2026-01-01", "2026-06-30", today)[2] == "periode_trop_longue"
    assert SI.client_window("2026-10-05", "2026-10-01", today)[2] == "periode_invalide"
    assert SI.client_window("hier", None, today)[2] == "periode_invalide"
    assert SI.parse_client("account-7752-A") == "account-7752-A"
    assert SI.parse_client("nom:  Gala  ÉTÉ ") == "nom:gala ete"
    for bad in ("", None, "nom:", "acc 1", "acc-1'; drop", "{\"$ne\": 1}", "x" * 81):
        assert SI.parse_client(bad) is None, bad
    # Meme cle que les seminaires
    assert SI.client_key({"accountId": "acc-1", "name": "X"}) == "acc-1"
    assert SI.client_key({"accountId": None, "name": "Gala  Été"}) == SI.parse_client("nom:Gala Ete")
