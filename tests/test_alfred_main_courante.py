"""Outil Alfred cockpit_main_courante : point de situation, filtres en
langage operateur, detail de fiche, perimetre et masquage."""
import re
from datetime import datetime, timedelta, timezone

import alfred_main_courante as M
import alfred_tools as AT

NOW = datetime(2026, 10, 8, 14, 30, tzinfo=timezone.utc)   # 16:30 a Paris


def _get(doc, key):
    for k in key.split("."):
        doc = doc.get(k) if isinstance(doc, dict) else None
    return doc


def _ok(doc, q):
    for k, v in (q or {}).items():
        if k == "$and":
            if not all(_ok(doc, x) for x in v):
                return False
            continue
        if k == "$or":
            if not any(_ok(doc, x) for x in v):
                return False
            continue
        x = _get(doc, k)
        if isinstance(v, dict):
            for op, a in v.items():
                if op == "$in" and x not in a:
                    return False
                if op == "$ne" and x == a:
                    return False
                if op == "$gte" and not (x is not None and x >= a):
                    return False
                if op == "$lt" and not (x is not None and x < a):
                    return False
                if op == "$exists" and (x is not None) != a:
                    return False
                if op == "$regex":
                    fl = re.I if "i" in v.get("$options", "") else 0
                    if not (isinstance(x, str) and re.search(a, x, fl)):
                        return False
        elif x != v:
            return False
    return True


class Cur(list):
    def sort(self, *a, **k):
        return self

    def limit(self, n):
        return Cur(self[:n])


class Col:
    def __init__(self, docs=()):
        self.docs = list(docs)

    def find(self, q=None, proj=None):
        return Cur(d for d in self.docs if _ok(d, q))

    def find_one(self, q=None, proj=None):
        r = self.find(q)
        return r[0] if r else None

    def count_documents(self, q):
        return len(self.find(q))


class Db(dict):
    def __missing__(self, k):
        self[k] = Col()
        return self[k]


def _t(h, m=0, jours=0):
    """Heure UTC naive (comme pymongo) du 08/10/2026, decalee de `jours`."""
    return datetime(2026, 10, 8, h, m) - timedelta(days=jours)


def _fiche(sql_id, cat, ts, urg=None, clos=False, close_ts=None, texte="", zone="Porte Nord",
           hist=(), event="SAISON", year=2026):
    return {"_id": "id-%s" % sql_id, "sql_id": sql_id, "category": cat, "ts": ts,
            "niveau_urgence": urg, "status_code": 10 if clos else 1, "close_ts": close_ts,
            "text": texte, "area": {"desc": zone}, "event": event, "year": year,
            "operator": "DUPONT Jean [1803-XYZ]",
            "content_category": {"sous_classification": "Intervention Sûreté"},
            "comment_history": [{"ts": t, "operator": "DUPONT Jean [1803-XYZ]", "text": x}
                                for t, x in hist]}


def _db():
    db = Db()
    db["pcorg"] = Col([
        _fiche(1, "PCS.Surete", _t(5), "IMP", texte="Ronde sûreté",
               hist=[(_t(5, 5), "05h00 ronde RAS")]),
        _fiche(2, "PCO.Secours", _t(13, 45), "UA", texte="Malaise spectateur tribune 13",
               hist=[(_t(13, 50), "Appel au 06 12 34 56 78, véhicule AB-123-CD sur place")]),
        _fiche(3, "PCS.Information", _t(9), texte="Prise de service",
               hist=[(_t(9, 1), "Prise de service"), (_t(10), "Statut: En cours -> Pris en compte")]),
        _fiche(4, "PCS.Surete", _t(2), "IMP", clos=True, close_ts=_t(3), texte="Ronde nuit"),
        _fiche(5, "PCS.Surete", _t(10, jours=200), texte="Cadenas oublié"),
        _fiche(6, "PCO.Technique", _t(12), "UR", texte="Panne éclairage", event="IAME"),
    ])
    return db


def _run(monkeypatch, args, ctx=None):
    monkeypatch.setattr(AT, "_event_pairs", lambda db, c: [("SAISON", 2026)])
    return M.t_main_courante(_db(), args, ctx or {}, now=NOW)


# ---------------------------------------------------------------------------
# Langage operateur
# ---------------------------------------------------------------------------

def test_categories_urgences_statut_numero():
    assert M.categorie_demandee("la sécu") == "Securite"
    assert M.categorie_demandee("sûreté") == "Surete"
    assert M.categorie_demandee("le médical") == "Secours"
    assert M.categorie_demandee("pompiers") == ""
    assert M.categorie_demandee(None) is None
    assert M.urgences_demandees("les urgentes") == ["EU", "UA"]
    assert M.urgences_demandees("détresse vitale") == ["EU"]
    assert M.urgences_demandees("UR") == ["UR"]
    assert M.statut_demande("les closes") == "closes"
    assert M.statut_demande("toutes") == "toutes"
    assert M.numero_fiche("la fiche n° 46760") == "46760"
    assert M.numero_fiche("82c8fa9b-ee3a-56dd-9fa0-f70ea1738df5") == "82c8fa9b-ee3a-56dd-9fa0-f70ea1738df5"
    assert M.numero_fiche("la grosse") is None


def test_periodes():
    deb, fin, lib = M.periode_demandee("cette nuit", NOW)
    assert deb.astimezone(M.TZ_PARIS).strftime("%d %H") == "07 20"
    assert fin.astimezone(M.TZ_PARIS).strftime("%d %H") == "08 08"
    deb, fin, lib = M.periode_demandee("depuis 2h", NOW)
    assert fin - deb == timedelta(hours=2)
    deb, fin, lib = M.periode_demandee("les 30 dernières minutes", NOW)
    assert fin - deb == timedelta(minutes=30)
    deb, fin, lib = M.periode_demandee("hier", NOW)
    assert lib == "hier" and (fin - deb) == timedelta(days=1)
    assert M.periode_demandee("n'importe", NOW) is None


def test_masquage_telephone_mail_plaque():
    t = M.masquer("Appeler 06 12 34 56 78 ou +33 6 12 34 56 78, a@b.fr, AB-123-CD et AB123CD")
    assert "06 12" not in t and "a@b.fr" not in t and "AB-123-CD" not in t and "AB123CD" not in t
    assert t.count("[tél.]") == 2 and "[immat.]" in t
    assert M.masquer("Porte 13, tribune 2024, 46760") == "Porte 13, tribune 2024, 46760"


# ---------------------------------------------------------------------------
# Vues
# ---------------------------------------------------------------------------

def test_point_de_situation(monkeypatch):
    r = _run(monkeypatch, {})
    assert r["vue"] == "point"
    c = r["compteurs"]
    # En cours : 1, 2, 3, 5 (4 close, 6 hors perimetre IAME)
    assert c["en_cours"] == 4 and c["anciennes_sans_activite"] == 1
    assert c["ouvertes_aujourdhui"] == 4 and c["closes_aujourdhui"] == 1
    assert c["ouvertes_derniere_heure"] == 1
    # Plus urgente d'abord (UA), puis IMP, puis sans urgence ; l'ancienne absente
    assert [f["numero"] for f in r["fiches"]] == [2, 1, 3]
    res = r["resume"]
    assert "4 fiche(s) en cours" in res and "urgence absolue 1" in res
    assert "1 fiche(s) restée(s) ouverte(s) sans activité depuis plus de 30 jours" in res
    assert "06 12" not in res and "AB-123-CD" not in res
    # Ligne de statut Prysm jamais donnee comme derniere action
    f3 = [f for f in r["fiches"] if f["numero"] == 3][0]
    assert "derniere_action" not in f3       # = texte de la fiche, non repete


def test_perimetre_du_jeton_applique(monkeypatch):
    r = _run(monkeypatch, {}, ctx={"cat_query": {"$in": ["PCO.Secours"]}})
    assert r["compteurs"]["en_cours"] == 1 and r["fiches"][0]["numero"] == 2
    r = _run(monkeypatch, {"fiche": "1"}, ctx={"cat_query": {"$in": ["PCO.Secours"]}})
    assert r["trouvee"] is False


def test_periode_nuit_inclut_les_closes(monkeypatch):
    r = _run(monkeypatch, {"periode": "cette nuit"})
    assert r["vue"] == "liste" and r["resolu"]["statut"] == "toutes"
    assert sorted(f["numero"] for f in r["fiches"]) == [1, 4]
    assert "cette nuit" in r["resume"]


def test_filtres_categorie_urgence_texte(monkeypatch):
    assert [f["numero"] for f in _run(monkeypatch, {"categorie": "sûreté"})["fiches"]] == [1, 5]
    assert [f["numero"] for f in _run(monkeypatch, {"urgence": "urgentes"})["fiches"]] == [2]
    assert [f["numero"] for f in _run(monkeypatch, {"texte": "tribune 13"})["fiches"]] == [2]
    r = _run(monkeypatch, {"categorie": "pompiers"})
    assert r["fiches"] == [] and "inconnue" in r["resume"]


def test_detail_fiche_masque_et_sans_matricule(monkeypatch):
    r = _run(monkeypatch, {"fiche": "fiche 2"})
    assert r["trouvee"] and r["fiche"]["ouverte_par"] == "DUPONT Jean"
    assert r["fiche"]["chronologie"][0]["par"] == "DUPONT Jean"
    assert "[tél.]" in r["resume"] and "[immat.]" in r["resume"]
    assert _run(monkeypatch, {"fiche": "99999"})["trouvee"] is False


# ---------------------------------------------------------------------------
# Manifeste et presentation
# ---------------------------------------------------------------------------

def test_manifeste_et_presentation():
    noms = [t["function"]["name"] for t in AT.manifest()]
    assert "cockpit_main_courante" in noms
    for ancien in ("cockpit_main_courante_fiches", "cockpit_main_courante_fiche",
                   "cockpit_main_courante_compteurs", "cockpit_presents"):
        assert ancien not in noms and ancien in AT.TOOLS
    p = AT.presentation()
    assert p["nom"] == "Alfred" and len(p["sait_faire"]) == len(noms)
    assert any("main courante" in s for s in p["sait_faire"])
    assert "lecture seule" in p["texte"]
