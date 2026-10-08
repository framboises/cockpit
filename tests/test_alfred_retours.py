"""Retours des operateurs sur Alfred : motif du pouce bas, instantane hors
purge, traitement admin, export pseudonymise."""
import json
from datetime import datetime, timedelta

import pytest

import alfred_retours as AR


# ---------------------------------------------------------------------------
# Mongo minimal (egalite, $lte, $in, $ne ; $set, $setOnInsert, upsert)
# ---------------------------------------------------------------------------

def _ok(doc, q):
    for k, v in (q or {}).items():
        x = doc.get(k)
        if isinstance(v, dict):
            for op, a in v.items():
                if op == "$lte" and not (x is not None and x <= a):
                    return False
                if op == "$gte" and not (x is not None and x >= a):
                    return False
                if op == "$in" and x not in a:
                    return False
                if op == "$ne" and x == a:
                    return False
        elif x != v:
            return False
    return True


class Cur(list):
    def sort(self, key, direction=1):
        super().sort(key=lambda d: d.get(key) or datetime.min, reverse=direction == -1)
        return self

    def limit(self, n):
        return Cur(self[:n])


class Col:
    def __init__(self, docs=()):
        self.docs = [dict(d) for d in docs]

    def find(self, q=None, proj=None):
        return Cur(dict(d) for d in self.docs if _ok(d, q))

    def find_one(self, q=None, proj=None, sort=None):
        r = self.find(q)
        return r[0] if r else None

    def insert_one(self, d):
        self.docs.append(dict(d))

    def update_one(self, q, u, upsert=False):
        for d in self.docs:
            if _ok(d, q):
                d.update(u.get("$set", {}))
                return
        if upsert:
            d = dict(q)
            d.update(u.get("$setOnInsert", {}))
            d.update(u.get("$set", {}))
            self.docs.append(d)

    def find_one_and_update(self, q, u, return_document=False):
        for d in self.docs:
            if _ok(d, q):
                d.update(u.get("$set", {}))
                return dict(d)
        return None

    def delete_one(self, q):
        for i, d in enumerate(self.docs):
            if _ok(d, q):
                del self.docs[i]
                return


class Db(dict):
    def __missing__(self, k):
        self[k] = Col()
        return self[k]


T0 = datetime(2026, 10, 8, 14, 0)
MID = "54fe342fddb14813afc62ce11048a79d"


def _db():
    db = Db()
    db[AR.COL_MESSAGES] = Col([
        {"_id": "q1", "session_id": "s", "role": "user", "content": "Combien aux camions ?",
         "status": "done", "created_at": T0, "user_email": "a@b.fr",
         "context": {"user_name": "Jean Dupont", "event": "24H CAMIONS", "year": "2026",
                     "page": "Accueil"}},
        {"_id": "e1", "session_id": "s", "role": "assistant", "content": "", "status": "error",
         "created_at": T0 + timedelta(seconds=1), "user_email": "a@b.fr"},
        {"_id": "q2", "session_id": "s", "role": "user", "content": "Et l'an dernier ?",
         "status": "done", "created_at": T0 + timedelta(minutes=1), "user_email": "a@b.fr",
         "context": {"user_name": "Jean Dupont", "event": "24H CAMIONS", "year": "2026"}},
        {"_id": MID, "session_id": "s", "role": "assistant", "content": "52 520 en 2025.",
         "status": "done", "created_at": T0 + timedelta(minutes=1, seconds=1),
         "user_email": "a@b.fr", "model": "alfred:latest", "hops": 2,
         "tool_calls": [{"name": "cockpit_frequentation", "args": "{}"}],
         "rating": -1, "rating_motif": "faux", "rating_comment": "c'etait 49 975"},
        {"_id": "plus_tard", "session_id": "s", "role": "user", "content": "merci",
         "status": "done", "created_at": T0 + timedelta(minutes=5)},
    ])
    db[AR.COL_TOOL_CALLS] = Col([
        {"tool": "cockpit_frequentation", "request_id": "chat-" + MID[:12], "args": '{"annee": "2025"}',
         "resolu": {"vue": "edition"}, "resume": "Pic 52 520", "ok": True, "duration_ms": 800,
         "created_at": T0},
        {"tool": "cockpit_meteo", "request_id": "chat-autre", "created_at": T0},
    ])
    return db


def test_figer_conversation_outils_et_contexte():
    db = _db()
    m = db[AR.COL_MESSAGES].find_one({"_id": MID})
    AR.figer(db, m)
    r = db[AR.COLLECTION].find_one({"_id": MID})
    assert r["statut"] == "nouveau" and r["motif"] == "faux"
    assert r["question"] == "Et l'an dernier ?"
    # Reponse en erreur omise, message posterieur exclu, reponse notee incluse
    assert [x["content"] for x in r["conversation"]] == [
        "Combien aux camions ?", "Et l'an dernier ?", "52 520 en 2025."]
    assert [a["outil"] for a in r["appels_cockpit"]] == ["cockpit_frequentation"]
    assert r["appels_cockpit"][0]["resume"] == "Pic 52 520"
    assert r["event"] == "24H CAMIONS" and r["user_name"] == "Jean Dupont"


def test_refiger_garde_le_traitement_admin():
    db = _db()
    m = db[AR.COL_MESSAGES].find_one({"_id": MID})
    AR.figer(db, m)
    AR.traiter(db, MID, {"statut": "traite", "reponse_attendue": "49 975"}, "admin")
    AR.figer(db, dict(m, rating_motif="incomplet"))
    r = db[AR.COLLECTION].find_one({"_id": MID})
    assert r["statut"] == "traite" and r["reponse_attendue"] == "49 975" and r["motif"] == "incomplet"


def test_retirer_supprime_seulement_si_non_traite():
    db = _db()
    m = db[AR.COL_MESSAGES].find_one({"_id": MID})
    AR.figer(db, m)
    AR.retirer(db, MID)
    assert db[AR.COLLECTION].find_one({"_id": MID}) is None
    AR.figer(db, m)
    AR.traiter(db, MID, {"statut": "traite"}, "admin")
    AR.retirer(db, MID)
    r = db[AR.COLLECTION].find_one({"_id": MID})
    assert r["rating"] is None and r["annule_par_operateur"]


def test_traiter_valide_le_statut():
    db = _db()
    AR.figer(db, db[AR.COL_MESSAGES].find_one({"_id": MID}))
    assert AR.traiter(db, MID, {"statut": "n_importe"}, "x")[1] == "statut_invalide"
    assert AR.traiter(db, "absent", {"statut": "traite"}, "x")[1] == "introuvable"
    assert AR.traiter(db, MID, {}, "x")[1] == "rien_a_modifier"


def test_rattraper_les_notes_anciennes():
    db = _db()
    assert AR.rattraper(db) == 1
    assert AR.rattraper(db) == 0


def test_export_pseudonymise():
    db = _db()
    AR.figer(db, db[AR.COL_MESSAGES].find_one({"_id": MID}))
    lignes = list(AR.exporter(db, {}))
    assert len(lignes) == 1
    assert "a@b.fr" not in lignes[0] and "Dupont" not in lignes[0]
    d = json.loads(lignes[0])
    assert d["operateur"].startswith("op-") and d["motif"] == "faux"
    assert d["messages"][-1] == {"role": "assistant", "content": "52 520 en 2025."}


def test_synchro_vm_depuis_et_annulations(monkeypatch):
    import alfred_chat
    from flask import Flask
    db = _db()
    AR.figer(db, db[AR.COL_MESSAGES].find_one({"_id": MID}))
    AR.traiter(db, MID, {"statut": "traite"}, "admin")
    AR.retirer(db, MID)
    monkeypatch.setattr(alfred_chat, "_db", lambda: db)
    monkeypatch.setattr(alfred_chat, "_tools_auth", lambda: (None, "scoped"))
    app = Flask(__name__)
    with app.test_request_context("/?depuis=2000-01-01T00:00:00Z"):
        j = alfred_chat.tools_retours().get_json()
    assert j["ok"] and len(j["retours"]) == 1 and j["retours"][0]["note"] is None
    assert "a@b.fr" not in json.dumps(j)
    with app.test_request_context("/?depuis=n_importe"):
        assert alfred_chat.tools_retours()[1] == 400
    monkeypatch.setattr(alfred_chat, "_tools_auth", lambda: (("refus", 401), None))
    with app.test_request_context("/"):
        assert alfred_chat.tools_retours() == ("refus", 401)


def test_compteurs_et_filtre():
    db = _db()
    AR.figer(db, db[AR.COL_MESSAGES].find_one({"_id": MID}))
    c = AR.compteurs(db)
    assert c["negatifs"] == 1 and c["nouveaux_negatifs"] == 1 and c["motifs"] == {"faux": 1}
    assert AR.filtre("-1", "nouveau", "faux") == {"rating": -1, "statut": "nouveau", "motif": "faux"}
    assert AR.filtre("x", "y", "z") == {}


# ---------------------------------------------------------------------------
# Route du pouce
# ---------------------------------------------------------------------------

@pytest.fixture
def feedback(monkeypatch):
    import alfred_chat
    db = _db()
    monkeypatch.setattr(alfred_chat, "_db", lambda: db)
    monkeypatch.setattr(alfred_chat, "_me", lambda: ("a@b.fr", "Jean Dupont", {}))
    from flask import Flask
    app = Flask(__name__)

    def appel(body):
        with app.test_request_context(json=body, method="POST"):
            r = alfred_chat.chat_feedback.__wrapped__(MID)
        resp, code = (r if isinstance(r, tuple) else (r, 200))
        return code, resp.get_json()
    return db, appel


def test_route_pouce_bas_avec_motif(feedback):
    db, appel = feedback
    code, j = appel({"rating": -1, "motif": "incomplet", "comment": " manque 2024 "})
    assert code == 200 and j["message"]["rating_motif"] == "incomplet"
    r = db[AR.COLLECTION].find_one({"_id": MID})
    assert r["motif"] == "incomplet" and r["commentaire"] == "manque 2024"


def test_route_refuse_un_motif_inconnu_ou_sur_pouce_haut(feedback):
    _db_, appel = feedback
    assert appel({"rating": -1, "motif": "pirate"})[0] == 400
    assert appel({"rating": 1, "motif": "faux"})[0] == 400


def test_route_annulation_retire_l_instantane(feedback):
    db, appel = feedback
    appel({"rating": -1, "motif": "faux"})
    appel({"rating": 0})
    assert db[AR.COLLECTION].find_one({"_id": MID}) is None
