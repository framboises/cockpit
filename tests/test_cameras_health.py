"""Releve de l'etat des cameras (cameras_health.py) et alerte camera_offline
(camera_alerts.py) : hysteresis, piste jamais sondee, une alerte par coupure,
releve perimee = aucune alerte, disponibilite."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import FakeDb

import camera_alerts
import cameras_health as H

T0 = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)
CAM = {"_id": "c1", "name": "120 - Porte Houx", "ip": "10.34.1.171", "port": 80, "site": "Circuit", "channels": []}
PISTE = {"_id": "p1", "name": "XVR Bugatti", "ip": "10.34.2.12", "port": 80, "channels": []}
CELL = {"_id": "g1", "name": "Dome 4G", "ip": "89.1.2.3", "port": 80, "channels": []}


def _db(*cams):
    return FakeDb(cockpit_cameras=[dict(c) for c in cams])


def _releve(db, results, at):
    """results : {camera_id: bool} ; un appareil non prevu fait echouer le test."""
    return H.run(db, now=at, prober=lambda d: results[d["_id"]])


# --- hysteresis ------------------------------------------------------------
def test_premiere_releve_sans_changement():
    fields, change = H.next_state({}, False, T0)
    assert change is None and fields["ok"] is False and fields["first_seen"] == T0


def test_un_echec_isole_ne_coupe_pas():
    prev = {"ok": True, "fails": 0, "last_ok_at": T0}
    fields, change = H.next_state(prev, False, T0 + timedelta(minutes=2))
    assert change is None and "ok" not in fields and fields["fails"] == 1


def test_deux_echecs_coupent_depuis_le_dernier_succes():
    prev = {"ok": True, "fails": 1, "last_ok_at": T0}
    fields, change = H.next_state(prev, False, T0 + timedelta(minutes=4))
    assert change is False and fields["ok"] is False and fields["since"] == T0


def test_4g_trois_echecs():
    prev = {"ok": True, "fails": 1, "last_ok_at": T0}
    _f, change = H.next_state(prev, False, T0 + timedelta(minutes=4), cellular=True)
    assert change is None
    prev["fails"] = 2
    _f, change = H.next_state(prev, False, T0 + timedelta(minutes=6), cellular=True)
    assert change is False


def test_retour_au_premier_succes():
    fields, change = H.next_state({"ok": False, "fails": 5}, True, T0)
    assert change is True and fields["ok"] is True and fields["fails"] == 0


# --- releve ------------------------------------------------------------------
def test_piste_jamais_sondee():
    db = _db(CAM, PISTE)
    _releve(db, {"c1": True}, T0)  # sonder p1 leverait KeyError
    assert [h["_id"] for h in db["cameras_health"].docs] == ["c1"]


def test_desactivee_retiree_du_releve():
    db = _db(CAM)
    _releve(db, {"c1": True}, T0)
    db["cockpit_cameras"].docs[0]["enabled"] = False
    _releve(db, {}, T0 + timedelta(minutes=2))
    assert db["cameras_health"].docs == []


def test_coupure_journalisee():
    db = _db(CAM)
    _releve(db, {"c1": True}, T0)
    _releve(db, {"c1": False}, T0 + timedelta(minutes=2))
    out = _releve(db, {"c1": False}, T0 + timedelta(minutes=4))
    assert out["changes"] == 1
    log = db["cameras_health_log"].docs
    assert len(log) == 1 and log[0]["ok"] is False and log[0]["at"] == T0


def test_dry_run_n_ecrit_rien():
    db = _db(CAM)
    H.run(db, dry_run=True, now=T0, prober=lambda d: True)
    assert db["cameras_health"].docs == []


# --- disponibilite -----------------------------------------------------------
def test_disponibilite():
    db = FakeDb(cameras_health=[{"_id": "c1", "ok": True, "first_seen": T0 - timedelta(days=30), "since": T0}],
                cameras_health_log=[{"camera_id": "c1", "ok": False, "at": T0 - timedelta(hours=6)},
                                    {"camera_id": "c1", "ok": True, "at": T0}])
    # 6 h de coupure sur 24 h
    assert H.availability(db, "c1", 24, now=T0 + timedelta(hours=6)) == pytest.approx(0.75, abs=1e-3)


def test_disponibilite_releve_recente():
    db = FakeDb(cameras_health=[{"_id": "c1", "ok": True, "first_seen": T0, "since": T0}])
    assert H.availability(db, "c1", 24 * 7, now=T0 + timedelta(hours=2)) == 1.0


# --- alerte camera_offline -----------------------------------------------------
DEF = {"slug": "cam-off", "params": {"after_min": 10}}


def _health(ok, since, checked, **kw):
    return dict({"_id": "c1", "name": "120 - Porte Houx", "ip": "10.34.1.171", "site": "Circuit",
                 "ok": ok, "since": since, "last_check_at": checked}, **kw)


def _eval(db, now, definition=DEF):
    return camera_alerts.evaluate(definition, {"now": now}, db) or []


def test_alerte_apres_le_delai_et_une_seule_fois():
    db = FakeDb(cameras_health=[_health(False, T0, T0 + timedelta(minutes=4))])
    assert _eval(db, T0 + timedelta(minutes=5)) == []
    db["cameras_health"].docs[0]["last_check_at"] = T0 + timedelta(minutes=10)
    out = _eval(db, T0 + timedelta(minutes=11))
    assert len(out) == 1 and out[0]["title"] == "CAMERA HORS LIGNE"
    assert out[0]["actionData"]["camera_id"] == "c1"
    assert _eval(db, T0 + timedelta(minutes=12)) == []


def test_nouvelle_coupure_nouvelle_alerte():
    db = FakeDb(cameras_health=[_health(False, T0, T0 + timedelta(minutes=10))])
    assert len(_eval(db, T0 + timedelta(minutes=11))) == 1
    db["cameras_health"].docs[0].update(ok=True, since=T0 + timedelta(minutes=20), last_check_at=T0 + timedelta(minutes=20))
    assert _eval(db, T0 + timedelta(minutes=21)) == []  # notify_back false
    t1 = T0 + timedelta(minutes=30)
    db["cameras_health"].docs[0].update(ok=False, since=t1, last_check_at=t1 + timedelta(minutes=12))
    assert len(_eval(db, t1 + timedelta(minutes=12))) == 1


def test_releve_perimee_aucune_alerte():
    db = FakeDb(cameras_health=[_health(False, T0, T0)])
    assert _eval(db, T0 + timedelta(minutes=30)) == []


def test_filtres_site_et_4g():
    db = FakeDb(cameras_health=[_health(False, T0, T0 + timedelta(minutes=20), cellular=True)])
    now = T0 + timedelta(minutes=21)
    assert _eval(db, now, {"slug": "a", "params": {"sites": ["Parkings"]}}) == []
    assert _eval(db, now, {"slug": "b", "params": {"include_cellular": False}}) == []
    assert len(_eval(db, now, {"slug": "c", "params": {}})) == 1


def test_lapi_sans_lecture():
    d = {"slug": "lapi", "params": {"lapi_silence_min": 60}}
    db = FakeDb(cameras_health=[_health(True, T0, T0 + timedelta(minutes=90), lapi_last_at=T0)])
    out = _eval(db, T0 + timedelta(minutes=90), d)
    assert len(out) == 1 and out[0]["title"] == "LAPI SANS LECTURE"
    assert _eval(db, T0 + timedelta(minutes=92), d) == []
