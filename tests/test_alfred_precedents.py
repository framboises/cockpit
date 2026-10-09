"""Outil Alfred cockpit_precedents : cas passes de la main courante, comptes
par Cockpit, donnees personnelles masquees, jamais sur WhatsApp."""
import json
from datetime import datetime, timedelta

import alfred_precedents as P
import alfred_tools as AT
import pcorg_assist as PA


class _Curseur(list):
    def sort(self, *a, **k):
        return self

    def limit(self, n):
        return _Curseur(self[:n])


class _Col:
    """Collection pcorg simulee : la recherche texte (index pca_text) rend
    `docs` ; on garde les requetes pour verifier perimetre et filtres."""

    def __init__(self, docs):
        self.docs, self.requetes = docs, []

    def find(self, q, proj=None):
        self.requetes.append(q)
        if "$text" in q:
            return _Curseur(self.docs)
        ids = set(q["_id"]["$in"])
        return _Curseur(d for d in self.docs if d["_id"] in ids)


class _Db:
    def __init__(self, docs):
        self.col = _Col(docs)

    def __getitem__(self, name):
        assert name == "pcorg"
        return self.col


def _fiche(i, texte, moyens, mins=30, event="24H AUTOS", year=2025, **kw):
    ts = datetime(year, 6, 14, 22, 0) + timedelta(hours=i)
    cc = {"sous_classification": "Secours a victime"}
    for j, m in enumerate(moyens, 1):
        cc["intervenant%d" % j] = m
    d = {"_id": "f%d" % i, "event": event, "year": year, "ts": ts, "close_ts": ts + timedelta(minutes=mins),
         "status_code": 10, "category": "PCO.Secours", "text": texte, "content_category": cc,
         "area": {"desc": "_MC PCO/Tribune 13"},
         "comment_history": [{"ts": ts, "operator": "Opérateur X", "text": kw.get("fin", "pris en charge CMS")}]}
    return d


CTX = {"event": "IAME", "year": "2026", "cat_query": {"$regex": "^PCO\\."}}


def _run(docs, monkeypatch, ctx=CTX, **args):
    monkeypatch.setattr(PA, "ensure_text_index", lambda col: True)
    monkeypatch.setattr(PA, "closing_info", lambda d: {
        "closing_comment": "", "last_entries": [{"text": h["text"]} for h in d.get("comment_history") or []]})
    db = _Db(docs)
    r = P.t_precedents(db, dict({"situation": "homme inconscient en tribune"}, **args), ctx)
    return r, db.col.requetes


def test_cockpit_compte_les_moyens_et_cite_les_plus_proches(monkeypatch):
    docs = [_fiche(1, "Homme inconscient tribune 13", ["CMS", "Pompiers"], 25),
            _fiche(2, "Homme inconscient en tribune", ["CMS", "Patrouille securite"], 40),
            _fiche(3, "homme inconscient tribune T22", ["CMS", "Patrouille sécurité", "Autres"], 10),
            _fiche(4, "Homme inconscient au sol en tribune", ["CMS"], 35, event="GPF", year=2024)]
    r, req = _run(docs, monkeypatch)
    assert r["vue"] == "cas" and r["trouves"] == 4
    # Libelles Prysm fusionnes, << Autres >> ignore
    assert r["moyens"] == {"CMS": 4, "Patrouille sécurité": 2, "Pompiers": 1}
    res = r["resume"]
    assert "Moyens engagés sur ces 4 cas : CMS 4, Patrouille sécurité 2, Pompiers 1." in res
    assert "Durée médiane jusqu'à la clôture : 30 min." in res
    assert "Les 3 plus proches :" in res and "issue : « pris en charge CMS »" in res
    assert res.rstrip().endswith("la fiche réflexe du wiki et le PCA priment.")
    # Perimetre : categories du jeton, fiches closes, edition en cours exclue
    q = req[0]
    assert q["category"] == {"$regex": "^PCO\\."} and q["status_code"] == 10
    assert q["$nor"] == [{"event": "IAME", "year": 2026}]
    # Jamais l'appelant, le telephone ni l'operateur
    brut = json.dumps(r, ensure_ascii=False, default=str)
    assert "Opérateur X" not in brut and "appelant" not in brut and "telephone" not in brut


def test_donnees_personnelles_masquees():
    t = P.masquer("Mme Dupont rappelée au 06 12 34 56 78, véhicule AB-123-CD, V. Beausmenil signale, "
                  "accord Tony, enfant Batist 8 ans, envoi Police à la Porte Nord, mail a.b@c.fr")
    for fuite in ("Dupont", "06 12", "AB-123-CD", "Beausmenil", "Tony", "Batist", "a.b@c.fr"):
        assert fuite not in t, (fuite, t)
    assert "Police" in t and "Porte Nord" in t and "8 ans" in t
    t = P.masquer("information transmise à Florent DUPONT, prise de service de MARAND Ulysse, "
                  "porte NORD, appel CMS Panorama")
    assert "DUPONT" not in t and "MARAND" not in t and "Ulysse" not in t, t
    assert "porte NORD" in t and "CMS Panorama" in t


def test_aucun_cas_et_situation_vide(monkeypatch):
    r, _ = _run([], monkeypatch)
    assert r["vue"] == "aucun" and "Ne pas en inventer" in r["resume"]
    r, _ = _run([], monkeypatch, situation="  ")
    assert r["vue"] == "vide"


def test_peu_de_cas_pas_de_tendance(monkeypatch):
    r, _ = _run([_fiche(1, "Homme inconscient tribune", ["CMS"])], monkeypatch)
    assert "Peu de cas comparables" in r["resume"] and "Durée médiane" not in r["resume"]


def test_la_question_entiere_est_reduite_a_la_situation():
    # Live 09/10 : la VM passe la phrase entiere de l'operateur
    assert P.nettoyer("Comment on avait géré les bagarres au camping ?") == "bagarres camping"
    assert P.nettoyer("Est-ce qu'on a déjà eu des colis suspects ?") == "colis suspects"
    assert P.nettoyer("Les odeurs de gaz, c'est déjà arrivé ?") == "odeurs gaz"
    assert P.nettoyer("Les tripodes en panne sur les 24H, ça s'était réglé comment ?",
                      "24H") == "tripodes panne"
    assert P.nettoyer("Un spectateur fait un malaise en tribune 13") == "spectateur malaise tribune 13"
    # La recherche porte sur l'incident : un lieu seul ramenait << vol tribune 13 >>
    assert P.coeur("spectateur malaise tribune 13 bouge plus").split()[0] == "malaise"
    assert "bouge" not in P.coeur("spectateur malaise tribune 13 bouge plus")
    assert "congestion" not in P.coeur("bouchonne parking bleu")
    assert "congestion" in P.coeur("bouchonne parking bleu", synonymes=True)
    assert P.coeur("tribune 13") == "tribune 13"


def test_evenement_limite_la_recherche(monkeypatch):
    _, req = _run([], monkeypatch, evenement="24H")
    assert req[0]["event"] == {"$regex": "24H", "$options": "i"}


def test_jamais_sur_whatsapp_ni_sans_jeton(monkeypatch):
    r, req = _run([_fiche(1, "x", [])], monkeypatch, ctx={"sans_main_courante": True})
    assert r["vue"] == "refus" and not req
    assert "cockpit_precedents" in AT.OUTILS_MAIN_COURANTE
    assert "cockpit_precedents" not in [t["function"]["name"] for t in AT.manifest(sans_main_courante=True)]
    assert "cockpit_precedents" in [t["function"]["name"] for t in AT.manifest()]
