"""Outil Alfred cockpit_agenda : fenetres en mots d'operateur, vignettes
(fermetures, journees, en cours), regroupements, recherche, edition."""
from datetime import datetime, timezone

import alfred_agenda as A
import alfred_tools as AT

NOW = datetime(2026, 10, 8, 14, 30, tzinfo=timezone.utc)   # jeudi 16:30 a Paris


class Col:
    def __init__(self, docs=()):
        self.docs = list(docs)

    def find_one(self, q, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return d
        return None

    def find(self, q=None, proj=None):
        return [d for d in self.docs if all(d.get(k) == v for k, v in (q or {}).items())]


class Db(dict):
    def __missing__(self, k):
        self[k] = Col()
        return self[k]


def _it(date, act, start="", end="", place="", origin="parametrage", **kw):
    d = {"date": date, "activity": act, "start": start, "end": end, "place": place,
         "origin": origin, "department": kw.pop("department", "SAFE")}
    d.update(kw)
    return d


def _db():
    db = Db()
    saison = {
        "2026-10-07": [_it("2026-10-07", "VEILLE CLIENT", origin="momentus", department="Séminaire")],
        "2026-10-08": [
            _it("2026-10-08", "FRANCE ARMOR", "14:00", "19:00", "P2A", "momentus", department="Séminaire"),
            _it("2026-10-08", "FRANCE ARMOR", "18:45", "23:59", "Restaurant Karting", "momentus",
                department="Séminaire"),
            _it("2026-10-08", "CRTI", place="Musée", origin="momentus", department="Activités"),
            _it("2026-10-08", "Fermeture au public", "", "19:00", "Controle", phase="close"),
        ],
        "2026-10-11": [
            _it("2026-10-11", "Arrivées du public Antarès - DJADJA", "16:30", "18:00", "Antarès", "voisins",
                voisins_id="v1", voisins_role="arrivees"),
            _it("2026-10-11", "Antarès : DJADJA", "18:00", "20:30", "Antarès", "voisins", voisins_id="v1"),
            _it("2026-10-11", "Sortie du public Antarès - DJADJA", "20:30", "21:15", "Antarès", "voisins",
                voisins_id="v1", voisins_role="sortie"),
            _it("2026-10-11", "PTE", "09:00", "12:00", "Piste MAISON BLANCHE", "momentus",
                department="Roulage Piste", momentus_status="option"),
        ],
    }
    db["timetable"] = Col([
        {"event": "SAISON", "year": "2026", "data": saison},
        {"event": "24H AUTOS", "year": "2026", "data": {"2026-05-11": [_it("2026-05-11", "Debut du montage", "06:00")]}},
        {"event": "24H AUTOS", "year": "2027", "data": {}},
    ])
    db["parametrages"] = Col([{"event": "24H AUTOS", "year": "2026"}, {"event": "24H AUTOS", "year": "2027"}])
    return db


def _run(monkeypatch, args):
    import event_courant
    monkeypatch.setattr(event_courant, "active_pairs", lambda db, **k: [("SAISON", 2026)])
    return A.t_agenda(_db(), args, {}, now=NOW)


def test_fenetres():
    lib = lambda q: A.fenetre(q, NOW)
    d, f, l = lib("demain")
    assert d.strftime("%d %H") == "09 00" and l.startswith("demain")
    d, f, l = lib("samedi")
    assert d.strftime("%d/%m") == "10/10"
    d, f, l = lib("ce week-end")
    assert d.strftime("%d") == "10" and f.strftime("%d") == "12"
    d, f, l = lib("18/10")
    assert d.strftime("%d/%m %Y") == "18/10 2026"
    d, f, l = lib("ce soir")
    assert d.strftime("%H:%M") == "18:00" and f.strftime("%d %H") == "09 00"
    d, f, l = lib("les prochaines 6 heures")
    assert (f - d).total_seconds() == 6 * 3600
    d, f, l = lib("samedi 26 septembre")
    assert d.strftime("%d/%m/%Y") == "26/09/2026"
    assert lib("à la saint glinglin") is None


def test_fenetre_par_defaut_en_cours_et_fermeture(monkeypatch):
    r = _run(monkeypatch, {})
    res = r["resume"]
    # Reservation de la veille (journee finie a minuit) absente
    assert "VEILLE CLIENT" not in res
    # Une ligne par client Momentus, creneaux et lieux regroupes, en cours
    assert res.count("FRANCE ARMOR") == 1
    assert "14:00-23:59 FRANCE ARMOR, P2A, Restaurant Karting (Séminaire) [en cours]" in res
    assert "journée CRTI, Musée (Activités) [en cours]" in res
    # Fermeture sans heure de debut : rendue a son heure de fin
    assert "19:00 Fermeture au public" in res


def test_voisins_une_ligne_avec_le_public(monkeypatch):
    r = _run(monkeypatch, {"quoi": "match à Antarès"})
    assert r["vue"] == "recherche" and r["resolu"]["filtre"] == "événements voisins"
    assert "18:00-20:30 Antarès : DJADJA (arrivées du public dès 16:30, sortie jusqu'à 21:15)" in r["resume"]
    assert len(r["vignettes"]) == 1


def test_option_momentus_et_filtre_texte(monkeypatch):
    r = _run(monkeypatch, {"quoi": "piste Maison Blanche", "quand": "dimanche"})
    assert "PTE, Piste MAISON BLANCHE (Roulage Piste) [option, non confirmé]" in r["resume"]
    assert len(r["vignettes"]) == 1


def test_edition_suivante_sans_timeline(monkeypatch):
    import alfred_evenements
    monkeypatch.setattr(alfred_evenements, "resoudre_nom", lambda db, t: ("24H AUTOS", []))
    r = _run(monkeypatch, {"quoi": "montage", "evenement": "les 24 heures"})
    assert r["resolu"]["annee"] == 2026 and r["resolu"]["edition_passee"]
    assert "06:00 Debut du montage" in r["resume"] and "passée" in r["resume"]


def test_rien_et_moment_incompris(monkeypatch):
    r = _run(monkeypatch, {"quand": "à la saint glinglin"})
    assert "non compris" in r["resume"]
    r = _run(monkeypatch, {"quoi": "feu d'artifice"})
    assert "Rien de prévu" in r["resume"]


def test_manifeste_agenda_remplace_timeline():
    noms = [t["function"]["name"] for t in AT.manifest()]
    assert "cockpit_agenda" in noms and "cockpit_timeline" not in noms
    assert "cockpit_timeline" in AT.TOOLS
    assert any("agenda" in s for s in AT.presentation()["sait_faire"])
