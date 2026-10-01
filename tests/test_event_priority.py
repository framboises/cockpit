"""Priorite entre epreuves simultanees : choix admin, sinon l'epreuve deja en
cours garde la main (aucune regle automatique jours publics / course)."""
from datetime import datetime, timezone

import event_courant as EC


def _win(event, start_day, public=()):
    return {"event": event, "year": 2026,
            "start": datetime(2026, 9, start_day, tzinfo=timezone.utc),
            "end": datetime(2026, 9, 30, tzinfo=timezone.utc),
            "public_days": set(public), "race": None}


def test_sans_choix_l_epreuve_deja_en_cours_garde_la_main():
    kz = _win("KZ", 16, public={"2026-09-20"})
    camions = _win("24H CAMIONS", 14)
    ordre = sorted([kz, camions], key=lambda w: EC.priority_key(w, []))
    # CAMIONS a ouvert sa fenetre en premier : il reste prioritaire, meme si
    # KZ est en jours publics
    assert [w["event"] for w in ordre] == ["24H CAMIONS", "KZ"]


def test_le_choix_admin_l_emporte():
    kz = _win("KZ", 16)
    camions = _win("24H CAMIONS", 14)
    choix = [("KZ", 2026)]
    ordre = sorted([camions, kz], key=lambda w: EC.priority_key(w, choix))
    assert [w["event"] for w in ordre] == ["KZ", "24H CAMIONS"]


def test_payload_signale_le_conflit(monkeypatch):
    acts = [{"event": "KZ", "year": 2026, "phase": "public", "race": None, "kind": "epreuve", "chosen": False},
            {"event": "24H CAMIONS", "year": 2026, "phase": "montage", "race": None, "kind": "epreuve", "chosen": False},
            {"event": "SAISON", "year": 2026, "phase": None, "race": None, "kind": "saison"}]
    monkeypatch.setattr(EC, "active_events", lambda db, now=None: acts)
    p = EC.payload(None)
    assert p["conflict"] is True and p["priority_chosen"] is False
    assert p["current"]["event"] == "KZ"
