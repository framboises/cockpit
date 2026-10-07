"""Evenements voisins (voisins_sync.py) et leur indicateur SAISON.

Parseurs sur des extraits reels des trois sources (Antares WordPress, LNB,
ESPN), reconciliation contre un double Mongo minimal : jamais la vraie base
ni le reseau.
"""

import copy
import os
from datetime import date, datetime

os.environ.setdefault("TITAN_ENV", "dev")
import saison_indicateurs as SI  # noqa: E402
import voisins_sync as VS  # noqa: E402


# ---------------------------------------------------------------------------
# Double Mongo (filtres simples : egalite, $gte/$lte/$ne/$in, cles pointees)
# ---------------------------------------------------------------------------
def _get(d, path):
    for p in path.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _ok(d, q):
    for k, v in (q or {}).items():
        x = _get(d, k)
        if isinstance(v, dict):
            for op, a in v.items():
                if op == "$gte" and not (x is not None and x >= a):
                    return False
                if op == "$lte" and not (x is not None and x <= a):
                    return False
                if op == "$ne" and x == a:
                    return False
                if op == "$in" and x not in a:
                    return False
        elif x != v:
            return False
    return True


def _set(d, path, val):
    ps = path.split(".")
    for p in ps[:-1]:
        d = d.setdefault(p, {})
    d[ps[-1]] = val


class _Res:
    def __init__(self, upserted_id=None):
        self.upserted_id = upserted_id


class Coll:
    def __init__(self, docs=None):
        self.docs = [copy.deepcopy(d) for d in docs or []]

    def find(self, q=None, proj=None):
        return [copy.deepcopy(d) for d in self.docs if _ok(d, q)]

    def find_one(self, q=None, proj=None, sort=None):
        rows = self.find(q)
        if sort:
            k, direction = sort[0]
            rows.sort(key=lambda d: _get(d, k) or "", reverse=direction < 0)
        return rows[0] if rows else None

    def update_one(self, q, upd, upsert=False):
        doc = next((d for d in self.docs if _ok(d, q)), None)
        new = None
        if doc is None:
            if not upsert:
                return _Res()
            doc = {k: v for k, v in q.items() if not isinstance(v, dict)}
            for k, v in (upd.get("$setOnInsert") or {}).items():
                _set(doc, k, copy.deepcopy(v))
            self.docs.append(doc)
            new = doc.get("_id")
        for k, v in (upd.get("$set") or {}).items():
            _set(doc, k, copy.deepcopy(v))
        return _Res(new)

    def update_many(self, q, upd):
        for d in self.docs:
            if _ok(d, q):
                for k, v in (upd.get("$set") or {}).items():
                    _set(d, k, copy.deepcopy(v))


class DB:
    name = "fake"

    def __init__(self, **cols):
        self.cols = {k: Coll(v) for k, v in cols.items()}

    def __getitem__(self, name):
        return self.cols.setdefault(name, Coll())


# ---------------------------------------------------------------------------
# Extraits reels des sources
# ---------------------------------------------------------------------------
ANTARES = [
    {"id": 3335, "link": "https://www.antaresarena.com/evenement/gaetan-roussel/",
     "title": {"rendered": "Ga&euml;tan Roussel"}, "class_list": ["post-3335", "event", "type-event", "type-concert"],
     "acf": {"subtitle": "<b>Tour</b>", "sessions": [
         {"session_date": "2026-11-03 20:00:00", "status_event": "on_sale"}]}},
    {"id": 4076, "link": "x", "title": {"rendered": "LES BODIN&#8217;S"},
     "class_list": ["type-event", "type-spectacle"],
     "acf": {"sessions": [{"session_date": "2027-03-12 20:00:00", "status_event": "on_sale"},
                          {"session_date": "2027-03-14 15:00:00", "status_event": "on_sale"},
                          {"session_date": "", "status_event": "on_sale"}]}},
    {"id": 9, "link": "x", "title": {"rendered": "NEJ"}, "class_list": ["type-concert"],
     "acf": {"sessions": [{"session_date": "2026-10-25 17:00:00", "status_event": "cancelled"}]}},
]


def _lnb_game(ext, home, away, when, status="SCHEDULED"):
    return {"external_id": ext, "match_id": f"m{ext}", "match_time_utc": when, "match_status": status,
            "competition_name": "Betclic ELITE", "round_description": "4eme journee",
            "teams": [{"external_id": home[0], "team_name": home[1]},
                      {"external_id": away[0], "team_name": away[1]}]}


LNB = [{"date": "2026-10-11", "data": [_lnb_game(1, (1892, "Saint-Quentin"), (1864, "Le Mans"), "2026-10-11T14:00:00.000Z")]},
       {"date": "2026-10-18", "data": [_lnb_game(2, (1864, "Le Mans"), (1859, "Bourg-en-Bresse"), "2026-10-18T17:00:00.000Z")]},
       {"date": "2026-12-27", "data": [_lnb_game(3, (1864, "Le Mans"), (1, "Cholet"), "27-12-26 19:00:00")]}]


def _espn_ev(eid, home, away, when, valid=True):
    return {"id": eid, "date": when, "timeValid": valid, "competitions": [{
        "status": {"type": {"name": "STATUS_SCHEDULED"}},
        "competitors": [{"homeAway": "home", "team": {"id": home[0], "displayName": home[1]}},
                        {"homeAway": "away", "team": {"id": away[0], "displayName": away[1]}}]}]}


ESPN = {"league": {"name": "French Ligue 1"}, "events": [
    _espn_ev("1", ("2697", "Le Mans"), ("1", "Toulouse"), "2026-10-16T18:45Z"),
    _espn_ev("2", ("9", "Paris Saint-Germain"), ("2697", "Le Mans"), "2026-10-10T18:45Z"),
    _espn_ev("3", ("2697", "Le Mans"), ("5", "Lyon"), "2027-01-02T20:00Z", valid=False)]}


# ---------------------------------------------------------------------------
# Parseurs
# ---------------------------------------------------------------------------
def test_antares_one_doc_per_session_paris_time_and_clean_text():
    docs = VS.parse_antares(ANTARES)
    assert [d["_id"] for d in docs] == ["antares|3335|2026-11-03T2000", "antares|4076|2027-03-12T2000",
                                        "antares|4076|2027-03-14T1500", "antares|9|2026-10-25T1700"]
    g = docs[0]
    assert (g["title"], g["subtitle"], g["kind"], g["date"], g["time"]) == (
        "Gaëtan Roussel", "Tour", "Concert", "2026-11-03", "20:00")
    assert docs[1]["title"] == "LES BODIN’S"
    assert docs[3]["status"] == "annule"
    assert all(d["venue"] == "antares" for d in docs)


def test_lnb_home_games_only_converted_to_paris():
    docs = VS.parse_lnb(LNB, 1864)
    assert [(d["title"], d["date"], d["time"]) for d in docs] == [
        ("MSB - Bourg-en-Bresse", "2026-10-18", "19:00"),   # UTC+2 (heure d'ete)
        ("MSB - Cholet", "2026-12-27", "20:00")]            # UTC+1, ancien format jj-mm-aa
    assert all(d["venue"] == "antares" and d["kind"] == "Basket" for d in docs)


def test_espn_home_games_and_unconfirmed_time():
    docs = VS.parse_espn(ESPN)
    assert [d["_id"] for d in docs] == ["fcmans|1", "fcmans|3"]
    assert (docs[0]["date"], docs[0]["time"], docs[0]["time_tbc"]) == ("2026-10-16", "20:45", False)
    assert (docs[1]["time"], docs[1]["time_tbc"]) == ("", True)
    assert docs[0]["venue"] == "stade" and docs[0]["title"] == "Le Mans FC - Toulouse"


# ---------------------------------------------------------------------------
# Base : upsert et reconciliation
# ---------------------------------------------------------------------------
TODAY = date(2026, 10, 7)


def test_store_marks_vanished_future_events_but_never_past_ones():
    db = DB()
    docs = VS.parse_espn(ESPN)
    past = dict(docs[0], _id="fcmans|old", date="2026-09-19")
    VS.store(db, {"fcmans": docs + [past]}, TODAY, now=datetime(2026, 10, 7))
    st = VS.store(db, {"fcmans": docs[:1]}, TODAY, now=datetime(2026, 10, 8))["fcmans"]
    by = {d["_id"]: d for d in db[VS.COLLECTION].docs}
    assert st["deleted"] == 1 and by["fcmans|3"]["_sync"]["deleted_at"] == datetime(2026, 10, 8)
    assert by["fcmans|old"]["_sync"]["deleted_at"] is None          # passe : jamais reconcilie
    assert by["fcmans|1"]["_sync"]["first_seen"] == datetime(2026, 10, 7)
    # Il revient : il perd sa suppression
    VS.store(db, {"fcmans": docs}, TODAY, now=datetime(2026, 10, 9))
    assert {d["_id"]: d for d in db[VS.COLLECTION].docs}["fcmans|3"]["_sync"]["deleted_at"] is None


def test_store_does_not_reconcile_a_thin_source():
    db = DB()
    docs = VS.parse_antares(ANTARES)
    VS.store(db, {"antares": docs}, TODAY)
    st = VS.store(db, {"antares": docs[:1]}, TODAY)["antares"]
    assert st["deleted"] == 0 and not st["reconciled"]


def test_failed_source_is_left_untouched():
    db = DB()
    VS.store(db, {"fcmans": VS.parse_espn(ESPN)}, TODAY)
    VS.store(db, {"antares": VS.parse_antares(ANTARES)}, TODAY)   # fcmans absent = en echec
    assert all(d["_sync"]["deleted_at"] is None for d in db[VS.COLLECTION].docs)


# ---------------------------------------------------------------------------
# Timeline et indicateurs
# ---------------------------------------------------------------------------
def _seeded():
    db = DB()
    VS.store(db, {"antares": VS.parse_antares(ANTARES), "msb": VS.parse_lnb(LNB, 1864),
                  "fcmans": VS.parse_espn(ESPN)}, TODAY)
    return db


def test_vignettes_skip_cancelled_and_are_safe():
    items = VS.build_items(_seeded(), date(2026, 10, 1), date(2027, 3, 31))
    assert "2026-10-25" not in items                                 # NEJ annule
    v = items["2026-10-16"][0]
    assert (v["activity"], v["place"], v["start"], v["origin"], v["category"], v["voisins_venue"]) == (
        "Le Mans FC - Toulouse (Stade MMArena)", "Stade MMArena", "20:45", "voisins", "Voisins", "stade")
    assert (v["end"], v["voisins_role"], v["voisins_icon"]) == ("22:40", "evenement", "sports_soccer")
    a = items["2026-11-03"][0]
    assert a["activity"] == "Antarès : Gaëtan Roussel" and a["voisins_venue"] == "antares"
    # Horaire non fixe : la vignette seule, sans flux
    assert len(items["2027-01-02"]) == 1 and "horaire a confirmer" in items["2027-01-02"][0]["remark"]
    assert items["2027-01-02"][0]["voisins_time_tbc"] is True
    for day in items.values():
        for it in day:
            assert "/" not in it["activity"] + it["remark"] and "<" not in it["activity"] + it["remark"]


def test_flux_vignettes_arrivals_and_exit():
    items = VS.build_items(_seeded(), date(2026, 10, 16), date(2026, 10, 18))
    foot = {it["voisins_role"]: it for it in items["2026-10-16"]}
    assert set(foot) == {"evenement", "arrivees", "sortie"}
    assert (foot["arrivees"]["start"], foot["arrivees"]["end"]) == ("18:45", "20:45")   # 2 h avant
    assert (foot["sortie"]["start"], foot["sortie"]["end"]) == ("22:40", "23:25")
    assert len({it["_id"] for it in items["2026-10-16"]}) == 3                        # ids distincts
    msb = {it["voisins_role"]: it for it in items["2026-10-18"]}
    assert msb["arrivees"]["start"] == "17:45" and msb["evenement"]["end"] == "21:15"
    # Spectacle d'humour : fenetre plus courte que le concert
    assert VS.flux({"source": "antares", "kind": "Humour", "time": "20:00"}) == ("19:00", "22:00")
    assert VS.flux({"source": "antares", "kind": "Concert", "time": "20:00"}) == ("18:30", "22:30")
    assert VS._shift("23:30", 45) == "00:15"


def test_public_event_projection_is_safe():
    d = dict(VS.parse_espn(ESPN)[0], url="javascript:alert(1)")
    p = VS.public_event(d)
    assert p["url"] == "" and p["icon"] == "sports_soccer" and p["venue_label"] == "Stade MMArena"
    assert (p["arrivals"], p["end_est"]) == ("18:45", "22:40")


def test_indicator_lists_events_per_venue():
    cfg = copy.deepcopy(SI.DEFAULT_VOISINS)
    res = SI.compute(_seeded(), date(2026, 10, 16), date(2026, 10, 18), cfg)
    by = {ds: {x["id"]: x for x in row} for ds, row in res.items()}
    assert by["2026-10-16"]["voisin_stade"]["active"] and not by["2026-10-16"]["voisin_antares"]["active"]
    det = by["2026-10-18"]["voisin_antares"]["details"][0]
    assert (det["event"], det["start"], det["kind"]) == ("MSB - Bourg-en-Bresse", "19:00", "Basket")
    assert (det["icon"], det["arrivals"], det["end_est"]) == ("sports_basketball", "17:45", "21:15")
    assert not by["2026-10-17"]["voisin_antares"]["active"]
    tbc = SI.compute(_seeded(), date(2027, 1, 2), date(2027, 1, 2), cfg)["2027-01-02"]
    assert {x["id"]: x for x in tbc}["voisin_stade"]["details"][0]["status"] == "a confirmer"


def test_voisins_source_validation():
    ok, errors = SI.validate_config(copy.deepcopy(SI.DEFAULT_VOISINS))
    assert not errors and [x["source"] for x in ok] == [{"type": "voisins", "venue": "antares"},
                                                        {"type": "voisins", "venue": "stade"}]
    bad = dict(copy.deepcopy(SI.DEFAULT_VOISINS[0]), source={"type": "voisins", "venue": "<x>"})
    assert SI.validate_config([bad])[1]
    pub = SI.public_indicators(DB(), ok)
    assert pub[0]["source_type"] == "voisins" and pub[0]["venue"] == "antares" and "rooms" not in pub[0]


def test_voisins_added_once_to_existing_config_and_removal_is_final():
    db = DB(cockpit_settings=[{"_id": SI.SETTINGS_ID, "indicators": copy.deepcopy(SI.DEFAULT_INDICATORS[:2])}])
    assert [it["id"] for it in SI.get_config(db)] == ["visites_libres", "visites_guidees",
                                                      "voisin_antares", "voisin_stade"]
    SI.save_config(db, SI.DEFAULT_INDICATORS[:2], "admin@x")
    assert [it["id"] for it in SI.get_config(db)] == ["visites_libres", "visites_guidees"]
