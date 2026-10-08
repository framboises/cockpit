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


# ---------------------------------------------------------------------------
# Jamais de main courante par la vue complete (WhatsApp)
# ---------------------------------------------------------------------------

def test_vue_complete_sans_main_courante(monkeypatch):
    import json
    from flask import Flask
    import alfred_chat
    noms = [t["function"]["name"] for t in AT.manifest(sans_main_courante=True)]
    assert "cockpit_main_courante" not in noms and "cockpit_meteo" in noms

    monkeypatch.setattr(alfred_chat, "_db", lambda: _db())
    monkeypatch.setattr(alfred_chat, "_ensure_indexes", lambda db: None)
    vus = []
    monkeypatch.setattr(alfred_chat.alfred_tools, "call",
                        lambda db, name, args, ctx: (vus.append(ctx) or True, {"ok": 1}))
    app = Flask(__name__)

    def appel(tool, mode, scope=None):
        monkeypatch.setattr(alfred_chat, "_tools_auth", lambda: (None, mode))
        body = {"tool": tool, "args": {}}
        if scope:
            body["scope"] = scope
        with app.test_request_context(data=json.dumps(body), method="POST",
                                      content_type="application/json"):
            r = alfred_chat.tools_call()
        return r[1] if isinstance(r, tuple) else 200

    for t in AT.OUTILS_MAIN_COURANTE:
        assert appel(t, "unscoped") == 403
    assert not vus
    assert appel("cockpit_situation", "unscoped") == 200
    assert vus[-1] == {"sans_main_courante": True}

    monkeypatch.setattr(AT, "t_trafic", lambda *a: {"disponible": False})
    monkeypatch.setattr(AT, "t_meteo", lambda *a: {"disponible": False})
    monkeypatch.setattr(AT, "t_alertes", lambda *a: {"disponible": False})
    monkeypatch.setattr(AT, "t_timeline", lambda *a: {"disponible": False})
    monkeypatch.setattr(AT, "t_evenement", lambda *a: {})
    monkeypatch.setattr(AT, "t_presents", lambda *a: {"disponible": False})
    s = AT.t_situation(_db(), {}, {"sans_main_courante": True})
    assert s["main_courante"] == "non consultable sur ce canal"
    assert s["fiches_en_cours_les_plus_urgentes"] == []


def test_mot_range_dans_le_mauvais_champ(monkeypatch):
    # Vu en direct : le modele a envoye urgence="secours"
    r = _run(monkeypatch, {"urgence": "secours"})
    assert r["resolu"]["categorie"] == "Secours" and [f["numero"] for f in r["fiches"]] == [2]
    r = _run(monkeypatch, {"urgence": "bizarre"})
    assert "Non compris, ignoré : urgence « bizarre »" in r["resume"]
    r = _run(monkeypatch, {"periode": "a la saint glinglin"})
    assert r["vue"] == "point" and "période « a la saint glinglin »" in r["resume"]


def test_admin_voit_tout_le_pc(monkeypatch):
    import event_courant
    import alfred_chat
    monkeypatch.setattr(event_courant, "active_pairs",
                        lambda db, include_previous_saison=False: [("IAME", 2026), ("SAISON", 2026)])
    # Operateur (regle de l'ecran, IAME en PCO seulement) : la seule fiche PCO d'IAME
    monkeypatch.setattr(AT, "_event_pairs", lambda db, c: [("IAME", 2026)])
    r = M.t_main_courante(_db(), {}, {"cat_query": {"$regex": "^PCO"}}, now=NOW)
    assert r["compteurs"]["en_cours"] == 1 and r["fiches"][0]["numero"] == 6
    # Admin : epreuve + SAISON, PCO et PCS
    r = M.t_main_courante(_db(), {}, {"cat_query": {"$regex": "^PC[OS]\\."}, "mc_tout": True}, now=NOW)
    assert r["compteurs"]["en_cours"] == 5 and r["resolu"]["vue_admin"]
    # Le drapeau voyage signe dans le jeton, jamais revendique par la VM
    monkeypatch.setattr(alfred_chat, "TOOLS_SECRET", "s" * 40)
    tok = alfred_chat.make_scope("a@b.fr", "IAME", "2026", {"$regex": "^PC[OS]\\."}, None, mc_tout=True)
    assert alfred_chat.read_scope(tok)["mc_tout"] is True
    tok = alfred_chat.make_scope("a@b.fr", "IAME", "2026", {"$regex": "^PCO"}, None)
    assert alfred_chat.read_scope(tok)["mc_tout"] is False


def test_synthese_trois_lignes(monkeypatch):
    r = _run(monkeypatch, {"periode": "aujourd'hui"})
    l = r["synthese"].split("\n")
    assert len(l) == 3
    assert l[0].startswith("4 fiche(s) :") and "1 close(s), 3 en cours" in l[0]
    assert l[1].startswith("Urgences : n°2") and "urgence absolue" in l[1]
    assert "06 12" not in r["synthese"]


def test_edition_terminee_cherche_aussi_les_closes(monkeypatch):
    import alfred_evenements
    import event_courant
    db = _db()
    db["pcorg"].docs.append(_fiche(7, "PCO.Flux", _t(10, jours=12), "UA", clos=True,
                                   close_ts=_t(11, jours=12), texte="Giratoire", event="24H CAMIONS"))
    db["parametrages"] = Col([{"event": "24H CAMIONS", "year": "2026"}])
    monkeypatch.setattr(alfred_evenements, "resoudre_nom", lambda db, t: ("24H CAMIONS", []))
    monkeypatch.setattr(event_courant, "active_pairs",
                        lambda db, include_previous_saison=False: [("IAME", 2026)])
    r = M.t_main_courante(db, {"evenement": "24h camions", "urgence": "UA"}, {}, now=NOW)
    assert r["resolu"]["edition_terminee"] and r["resolu"]["statut"] == "toutes"
    assert [f["numero"] for f in r["fiches"]] == [7]
