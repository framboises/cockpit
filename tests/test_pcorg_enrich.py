"""Enrichissement en code pur des fiches structurees (pcorg_enrich.py) et champs v2."""
import json
from datetime import datetime

import pcorg_digest as PD
import pcorg_enrich as PE


def _gaz():
    g = PE.Gazetteer.__new__(PE.Gazetteer)
    g.entries = sorted([
        {"name": "PORTE MAISON-BLANCHE VL", "norm": PE.norm("PORTE MAISON-BLANCHE VL"), "layer": "portes",
         "label": "Porte", "lat": 47.95, "lng": 0.21},
        {"name": "Giratoire Antares", "norm": PE.norm("Giratoire Antares"), "layer": "giratoires",
         "label": "Giratoire", "lat": 47.96, "lng": 0.20},
        {"name": "HOUX", "norm": "houx", "layer": "passerelles", "label": "Passerelle", "lat": 47.94, "lng": 0.22},
    ], key=lambda e: -len(e["norm"]))
    g.tribunes = {34: {"name": "34", "norm": "34", "layer": "tribunes", "label": "Tribune", "lat": 47.951, "lng": 0.211}}
    g.virages = {}
    g.grid = {"AI20": (47.97, 0.19)}
    g.zones = [{"id": "z1", "name": "Secteur Test",
                "geom": {"type": "Polygon", "coordinates": [[[0.18, 47.93], [0.25, 47.93], [0.25, 47.98], [0.18, 47.98], [0.18, 47.93]]]}}]
    return g


class TestPosition:
    def test_gps_d_origine_prioritaire(self):
        geo = _gaz().locate({"gps": {"type": "Point", "coordinates": [0.2, 47.9]}}, "T34")
        assert geo["source"] == "gps" and geo["lat"] == 47.9

    def test_carroyage_avec_espace(self):
        geo = _gaz().locate({"content_category": {"carroye": "ai 20"}}, None)
        assert geo["source"] == "carroyage" and geo["name"] == "AI20"

    def test_tribune_citee(self):
        geo = _gaz().locate({}, "Tribune 34, escalier B")
        assert geo["layer"] == "tribunes" and geo["confidence"] == "haute"
        assert _gaz().locate({}, "T34 - Loge 12")["layer"] == "tribunes"

    def test_nom_le_plus_long_et_accents(self):
        geo = _gaz().locate({}, "bouchon giratoire Antarès")
        assert geo["name"] == "Giratoire Antares"
        assert _gaz().locate({}, "Porte Maison Blanche VL fermee")["layer"] == "portes"

    def test_titre_si_lieu_non_precise(self):
        geo = _gaz().locate({"text": "bouchon giratoire Antares"}, "non precise")
        assert geo["source"] == "titre" and geo["confidence"] == "haute"

    def test_nom_d_un_seul_mot_seulement_exact(self):
        g = _gaz()
        assert g.locate({}, "Houx")["name"] == "HOUX"                     # lieu cite = le nom
        assert g.locate({"text": "Fuite passerelle Houx"}, "non precise") is None
        g.entries.append({"name": "Nord", "norm": "nord", "layer": "x", "label": "x", "lat": 1, "lng": 1})
        assert g.locate({}, "Nord") is None                               # mot generique, jamais seul

    def test_rien(self):
        assert _gaz().locate({"text": "rien"}, "non precise") is None

    def test_zone(self):
        g = _gaz()
        assert g.zone_of(47.95, 0.2)["name"] == "Secteur Test"
        assert g.zone_of(48.5, 0.2) is None

    def test_zone_la_plus_fine_et_voisinage(self):
        def sq(a, b, c, d):
            return {"type": "Polygon", "coordinates": [[[b, a], [d, a], [d, c], [b, c], [b, a]]]}
        g = _gaz()
        big, small = sq(47.90, 0.15, 48.00, 0.30), sq(47.94, 0.19, 47.96, 0.21)
        g.zones = [
            {"id": "d", "name": "Departement Sud-Est", "level": "departement", "geom": big, "bbox": PE._bbox(big)},
            {"id": "s", "name": "Secteur Houx", "level": "secteur", "postes": "Porte Houx\nEpinettes",
             "geom": small, "bbox": PE._bbox(small)},
            {"id": "z", "name": "Zone Est", "level": "zone", "geom": big, "bbox": PE._bbox(big)},
            {"id": "l", "name": "Lot 5 - X", "level": "lot", "geom": small, "bbox": PE._bbox(small)},
        ]
        g.polys = [{"layer": "terrains", "label": "Terrain / parking", "name": "HOUX", "geom": small, "bbox": PE._bbox(small)}]
        g.near = [
            {"layer": "portes", "label": "Porte", "name": "PORTE HOUX", "lat": 47.9505, "lng": 0.2},
            {"layer": "portes", "label": "Porte", "name": "PORTE LOIN", "lat": 47.9600, "lng": 0.2},
            {"layer": "tribunes", "label": "Tribune", "name": "T12 FORD", "lat": 47.9510, "lng": 0.2},
        ]
        assert g.zone_of(47.95, 0.2)["name"] == "Secteur Houx"            # pas le departement
        s = g.surroundings(47.95, 0.2, "gps")
        assert s["competence"] == {"departement": "Departement Sud-Est", "zone": "Zone Est", "secteur": "Secteur Houx",
                                   "postes": ["Porte Houx", "Epinettes"], "lot": "Lot 5 - X"}
        assert s["dans"] == [{"layer": "terrains", "label": "Terrain / parking", "name": "HOUX"}]
        assert [(p["name"], p["dist_m"]) for p in s["pres"]] == [("PORTE HOUX", 56), ("T12 FORD", 111)]
        assert [p["name"] for p in g.surroundings(47.95, 0.2, "lieu")["pres"]] == ["PORTE HOUX"]   # rayon 100 m


class TestPlaques:
    def test_formats(self):
        assert PE.plates("FOURRIERE : Mercedes Marco Polo  EY-422-KG") == ["EY422KG"]
        assert PE.plates("Fourriere - FV940YV", "immatriculation GN 176 TC") == ["FV940YV", "GN176TC"]
        assert PE.plates("fourriere: AL-039-GB / 505-BRA-44") == ["505BRA44", "AL039GB"]
        assert PE.plates("Ford Transit blanc 6513-LFL") == ["6513LFL"]
        assert PE.plates("P7C FOURRIERE FORD TOURNEO YR25KYP") == ["YR25KYP"]

    def test_pas_de_faux_positifs(self):
        txt = ("12/06/2025 09:04:19 , COULBAUT Benoit Golfette n10 stickee P7B stationnee sur le P6D. "
               "DEMANDE FAITE AU TANGO 11H33 Fourriere CD 92, 8h45 arrivee, T52 27, 3 barrieres, MP35b, PS Nord")
        assert PE.plates(txt) == []

    def test_doublon_tranche_par_les_plaques(self):
        base = {"type": "fourriere", "resume": "enlevement vehicule genant", "lieu_norm": "p7b"}
        assert PE.dup_score(dict(base, plaques=["AB123CD"]), dict(base, plaques=["EF456GH"])) == 0.0
        assert PE.dup_score(dict(base, plaques=["AB123CD"]), dict(base, plaques=["AB123CD", "X"])) == 0.95
        assert PE.dup_score(base, dict(base)) >= PE.DUP_THRESHOLD    # sans plaque : texte et lieu


class TestDelais:
    def test_delais_depuis_le_signalement(self):
        d = PE.delays({"signalement": "16:20", "engagement": "16:23", "sur_place": "16:41", "fin": "17:05"})
        assert d == {"engagement": 3, "sur_place": 21, "fin": 45}

    def test_passage_de_minuit_et_incoherence(self):
        assert PE.delays({"signalement": "23:50", "fin": "00:20"})["fin"] == 30
        assert PE.delays({"signalement": "08:00", "fin": "21:00"})["fin"] is None
        assert PE.delays({"engagement": "10:00"})["engagement"] is None

    def test_completude(self):
        c = PE.completeness({"lieu": "T34", "etapes": {"fin": "10:00"}, "issue_code": "resolu",
                             "moyens_engages": []}, None)
        assert c["score"] == 3 and c["manquants"] == ["moyens"]


class TestDoublons:
    def test_score(self):
        a = {"lieu_norm": "porte nord", "type": "bagarre", "resume": "Bagarre entre deux spectateurs a la porte nord."}
        b = dict(a, resume="Rixe entre deux spectateurs a la porte nord.")
        c = {"lieu_norm": "t34", "type": "panne ecran", "resume": "Ecran geant eteint."}
        assert PE.dup_score(a, b) >= PE.DUP_THRESHOLD > PE.dup_score(a, c)


class TestReponseV2:
    def test_champs_v2_nettoyes(self):
        r = PD.parse_reply(json.dumps({
            "gravite": 3, "lieu": "T34", "etapes": {"signalement": "9h05", "fin": "25:00"},
            "equipement": "", "moyens_engages": ["PS Nord", "Ludovic ARNAULT", "field:ben", "SDIS Sarthe"],
            "services_exterieurs": "SDIS, SAMU", "causes": ["Alcool", "inconnue"], "issue_code": "Resolu",
            "donnees_personnelles": ["nom", "religion"]}))
        assert r["etapes"]["signalement"] == "09:05" and r["etapes"]["fin"] is None
        assert r["moyens_engages"] == ["PS Nord", "SDIS Sarthe"]
        assert r["services_exterieurs"] == ["SDIS", "SAMU"]
        assert r["causes"] == ["alcool"] and r["issue_code"] == "resolu"
        assert r["donnees_personnelles"] == ["nom"] and r["equipement"] == "non precise"

    def test_noms_de_personnes(self):
        for s in ("Ludovic ARNAULT", "LEBERT Pierre", "field:ludo", "a.chaperon CHAPERON"):
            assert PD._looks_like_person(s), s
        for s in ("PS Nord", "SDIS Sarthe", "Patrouille 4", "Appui Flux", "CMS", "Mobile Teranga"):
            assert not PD._looks_like_person(s), s


def test_fiche_controlee_retraitee_pour_nouvelle_version_seulement():
    fiche = {"text": "x", "comment": "y", "category": "c", "niveau_urgence": None}
    d = {"src_hash": PD.src_hash(fiche), "prompt_version": "v1", "review": {"verdict": "ok"}}
    assert PD.needs_work(fiche, d)                                  # v1 -> v2 : nouveaux champs
    d["prompt_version"] = PD.PROMPT_VERSION
    assert not PD.needs_work(fiche, d)
    assert not PD.needs_work(fiche, dict(d, error="x", attempts=1))  # echec : pas de retraitement


class TestV3:
    def test_blocs_et_nature(self):
        r = PD.parse_reply(json.dumps({
            "gravite": 1, "nature": "Routine", "activite": "ronde",
            "ronde": {"debut": "21h17", "fin": "21:54", "points": ["Siege", "Panoramic 24"], "resultat": "RAS",
                      "anomalies": []},
            "alarme": {"action": "mise hors service", "batiment": "Musee", "prestataire": "non precise"},
            "secours": {"nature": "malaise", "sexe": "femme", "tranche_age": "18-30", "prise_en_charge": "CMS"},
            "jalon": None, "dysfonctionnement": {"type": "inconnu", "description": ""},
            "sous_classification": "electricite"}), ["Electricité", "Logistique"])
        assert r["nature"] == "routine" and r["activite"] == "ronde"
        assert r["ronde"] == {"debut": "21:17", "fin": "21:54", "points": ["Siege", "Panoramic 24"],
                              "resultat": "ras", "anomalies": []}
        assert r["alarme"]["action"] == "mise_hors_service" and "prestataire" not in r["alarme"]
        assert r["secours"]["prise_en_charge"] == "cms"
        assert r["jalon"] is None and r["dysfonctionnement"] is None      # vide une fois nettoye
        assert r["sous_classification_proposee"] == "Electricité"

    def test_sous_classification_hors_liste_refusee(self):
        r = PD.parse_reply('{"gravite": 2, "sous_classification": "Invente"}', ["Logistique"])
        assert r["sous_classification_proposee"] is None
        assert PD.parse_reply('{"gravite": 2, "sous_classification": "Logistique"}')["sous_classification_proposee"] is None

    def test_liste_officielle_envoyee_au_modele(self):
        src = PD.source_text({"text": "x", "comment": "y", "category": "PCO.Technique"}, ["Electricite", "Sanitaire"])
        assert "Electricite ; Sanitaire" in src
        assert "sous_classification = null" in PD.source_text({"text": "x"}, None)


class TestTaxonomie:
    def _taxo(self):
        t = PE.Taxonomy.__new__(PE.Taxonomy)
        t.sc = {PE.norm(x): x for x in ("Gene a la circulation", "Electricité", "Secours a victime",
                                         "Non-respect reglementation - Circuit", "Intrusion")}
        t.people = {PE.norm(x): x for x in ("Patrouille sécurité", "Service technique", "CMS")}
        return t

    def test_variantes_rapprochees(self):
        t = self._taxo()
        f = {"content_category": {"sous_classification": "Gêne circul", "service_contacte": "Service Technique",
                                  "intervenant1": "Patrouille securite", "intervenant2": "Patrouille sécurité",
                                  "motif_intervention": "Ronde sureté Interieure."}}
        x = t.of(f)
        assert x["sous_classification"] == "Gene a la circulation" and x["sous_classification_officielle"]
        assert x["service"] == "Service technique"
        assert x["intervenants"] == ["Patrouille sécurité"]
        assert x["motif"] == "ronde surete interieur"
        assert t.of({"content_category": {"sous_classification": "Electricite"}})["sous_classification"] == "Electricité"
        assert t.of({"content_category": {"sous_classification": "Non-respect réglement circuit"}})["sous_classification_officielle"]

    def test_hors_liste_garde(self):
        x = self._taxo().of({"content_category": {"sous_classification": "Barrierage"}})
        assert x["sous_classification"] == "Barrierage" and not x["sous_classification_officielle"]

    def test_motifs(self):
        for v in ("Ronde sureté Bt.", "Ronde sureté Bts", "ronde surete batiment"):
            assert PE._motif_key(v) == "ronde surete batiment"
        assert PE._motif_key("Préstation Nettoyage.") == PE._motif_key("Prestation Nettoyage")


def test_doublon_declare_et_zone():
    assert PE.declared_duplicate({"comment": "Convoi sorti a 14h30 ==> Doublon fiche 31645"}) == 31645
    assert PE.declared_duplicate({"text": "doublon de la fiche n°31650"}) == 31650
    assert PE.declared_duplicate({"text": "pas de doublon"}) is None
    a = PE.area_levels({"area": {"desc": "_MC PCO/Interieur Dep./03-Zone Sud"}})
    assert a["niveaux"] == ["_MC PCO", "Interieur Dep.", "03-Zone Sud"] and not a["generique"]
    assert PE.area_levels({"area": {"desc": "_MC PCS"}})["generique"]


def test_duree_ronde():
    import gravite_fiches as GF
    assert GF._minutes("21:17", "21:54") == 37
    assert GF._minutes("23:50", "00:10") == 20
    assert GF._minutes(None, "10:00") is None


class _FakeCol:
    def __init__(self, docs):
        self.docs = {d["_id"]: d for d in docs}

    def find(self, q, proj=None):
        return [d for d in self.docs.values() if d.get("batch") == q.get("batch") and d.get("error") is None]

    def update_many(self, q, upd):
        for d in self.docs.values():
            if d.get("batch") == q.get("batch"):
                d.update(upd["$set"])

    def update_one(self, q, upd):
        self.docs[q["_id"]].update(upd["$set"])


class TestSelectionAControler:
    def _lot(self):
        docs = [{"_id": "r%02d" % i, "batch": "L1", "gravite": 1, "nature": "routine", "error": None}
                for i in range(80)]
        docs += [{"_id": "g5", "batch": "L1", "gravite": 5, "nature": "incident", "error": None},
                 {"_id": "al", "batch": "L1", "gravite": 2, "nature": "routine", "alarme_non_remise": True,
                  "error": None},
                 {"_id": "sc", "batch": "L1", "gravite": 3, "nature": "incident", "secours": {"nature": "malaise"},
                  "error": None}]
        docs += [{"_id": "i%02d" % i, "batch": "L1", "gravite": 2, "nature": "incident", "error": None}
                 for i in range(17)]
        return {"c": _FakeCol(docs)}

    def test_vingt_pour_cent_et_cas_interessants(self):
        db = self._lot()
        assert PE.select_for_review(db, "c", "L1") == 20
        marked = {k: d["a_controler"] for k, d in db["c"].docs.items() if d.get("a_controler")}
        assert len(marked) == 20
        assert {"g5", "al", "sc"} <= set(marked)
        assert sum(1 for m in marked.values() if "aleatoire" in m["raisons"]) == 2
        routines = [k for k, d in db["c"].docs.items()
                    if d.get("a_controler") and k.startswith("r") and "aleatoire" not in d["a_controler"]["raisons"]]
        assert len(routines) < 10                       # pas 20 rondes identiques

    def test_reselection_garde_les_fiches_controlees(self):
        db = self._lot()
        PE.select_for_review(db, "c", "L1")
        done = [k for k, d in db["c"].docs.items() if d.get("a_controler")][:5]
        for k in done:
            db["c"].docs[k]["review"] = {"verdict": "ok"}
        PE.select_for_review(db, "c", "L1")
        assert all(db["c"].docs[k].get("a_controler") for k in done)

    def test_raisons(self):
        s, why = PE.review_reasons({"gravite": 4, "secours": {"x": 1}, "geo": {"source": "lieu"}})
        assert "gravite 4" in why and "secours" in why and "position deduite" in why and s >= 10


def test_point_dans_polygone_avec_trou():
    geom = {"type": "Polygon", "coordinates": [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}
    assert PE.point_in_geom(2, 2, geom)
    assert not PE.point_in_geom(5, 5, geom)
    assert not PE.point_in_geom(20, 20, geom)
    assert datetime  # module importe pour les dates des doublons


# ---------------------------------------------------------------------------
# v5 : chronologie lue dans l'historique, heures verifiees, garde-fou des resumes
# ---------------------------------------------------------------------------
FICHE_V5 = {
    "text": "Femme bloquee dans les WC S12", "ts": datetime(2026, 5, 9, 16, 42, 0),
    "close_ts": datetime(2026, 5, 9, 17, 30, 0),
    "comment": "09/05/2026 18:42:48 , X\n Appel CP T12, ST ACO se rend sur place\n"
               "09/05/2026 19:16:34 , X\n 19h15 Technicien sur place\n09/05/2026 19:25:25 , X\n Statut: En cours -> Terminé",
    "comment_history": [
        {"ts": "2026-05-09T18:42:48+02:00", "text": "Appel CP T12, ST ACO se rend sur place"},
        {"ts": "2026-05-09T19:16:34+02:00", "text": "19h15 Technicien sur place"},
        {"ts": "2026-05-09T19:25:25+02:00", "text": "Statut: En cours -> Terminé"},
    ],
}


class TestChronologie:
    def test_ouverture_termine_duree_en_heure_de_paris(self):
        c = PE.chronology(FICHE_V5)
        assert c["ouverture"] == "18:42" and c["termine"] == "19:25" and c["cloture"] == "19:30"
        assert c["duree_min"] == 43 and c["maj"] == 2
        assert c["ecrites"] == ["19:15"] and "18:42" in c["heures"]

    def test_sans_aucune_heure(self):
        assert PE.chronology({"text": "x"}) is None

    def test_heures_non_ecrites_retirees_et_completees(self):
        c = PE.chronology(FICHE_V5)
        steps, rm = PE.checked_steps({"signalement": None, "engagement": "18:50", "sur_place": "19:15",
                                      "fin": None}, c)
        assert steps == {"signalement": "18:42", "engagement": None, "sur_place": "19:15", "fin": "19:25"}
        assert rm == ["engagement"]

    def test_sur_place_recopie_du_signalement(self):
        c = PE.chronology(FICHE_V5)
        steps, rm = PE.checked_steps({"signalement": "18:42", "sur_place": "18:42"}, c)
        assert steps["sur_place"] is None and "sur_place" in rm


class TestGardeFou:
    def test_issue_non_ecrite_retiree(self):
        src = "Homme 30 ans coma ethylique en PLS au camping du Houx. Le CMS se rend sur place."
        txt, rm = PE.guard_text("Homme en coma ethylique pris en charge par le CMS. Il a ete evacue par ambulance.", src)
        assert txt == "Homme en coma ethylique pris en charge par le CMS." and set(rm) == {"evacu", "ambulance"}

    def test_issue_ecrite_gardee(self):
        txt, rm = PE.guard_text("Victime evacuee par le VSAV.", "transport par les pompiers, VSAV sur place")
        assert rm == [] and txt == "Victime evacuee par le VSAV."

    def test_phrase_sur_le_personnel_retiree(self):
        txt, _ = PE.guard_text("Vol de velo au paddock. Aucun membre du personnel n'est concerne.", "vol velo")
        assert txt == "Vol de velo au paddock."

    def test_tout_retire_vaut_non_precise(self):
        assert PE.guard_text("Situation stabilisee.", "rien")[0] == "non precise"

    def test_champs_gardes_idempotents_depuis_la_reponse_brute(self):
        d = {"resume": "Technicien sur place. Situation maitrisee.", "etapes": {"sur_place": "19:15"}}
        c = PE.chronology(FICHE_V5)
        g1 = PE.guarded_fields(FICHE_V5, d, c)
        assert g1["resume"] == "Technicien sur place." and "resume:maitris" in g1["retouches"]
        g2 = PE.guarded_fields(FICHE_V5, dict(d, **g1), c)
        assert g2["resume"] == g1["resume"] and g2["resume_modele"] == d["resume"]

    def test_ligne_de_lecture(self):
        d = {"titre": "Femme bloquee dans les WC", "lieu": "S12", "issue_code": "resolu"}
        assert PE.reading_line(d, None, PE.chronology(FICHE_V5)) == "Femme bloquee dans les WC · S12 · 18:42 · resolu"
