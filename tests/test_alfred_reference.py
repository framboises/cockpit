"""Outil Alfred cockpit_reference : Cockpit identifie la fiche (pas le
modele), guide court, departage, aucune fiche, question de suite, carroyage,
liens surs."""
import json

import alfred_reference as R
import alfred_tools as AT
from conftest import FakeDb


def _f(code, titre, dom, situation, souscas, questions=(), conduite=(), details=(), flow=(),
       status="published", **kw):
    d = {"code": code, "titre": titre, "dom": dom, "situation": situation, "souscas": list(souscas),
         "questions": list(questions), "conduite": list(conduite), "details": list(details),
         "flow": list(flow), "consigner": kw.get("consigner", ""), "pieges": kw.get("pieges", ""),
         "status": status, "acteurs": kw.get("acteurs", "")}
    return d


P01 = _f("P01", "Secours à victime", "secours",
         "Malaise, blessure, personne inconsciente ou en détresse.",
         ["Malaise / perte de connaissance", "Blessure ou traumatisme"],
         questions=["La personne est-elle consciente ? Respire-t-elle ?",
                    "Où exactement (zone, carroyage, repère le plus proche) ?"],
         conduite=["Qualifier l'état et localiser précisément.", "Engager le CMS sans délai."],
         details=["Précise l'état d'emblée : conscient, inconscient mais respire, ne respire pas. "
                  "C'est ce qui décide du niveau de moyens (CMS seul, ou CMS + Pompiers)."],
         flow=[{"k": "start", "t": "Alerte secours"},
               {"k": "ask", "t": "Gravité vitale ?", "y": "CMS + Pompiers, sécuriser", "n": "CMS seul"},
               {"k": "end", "t": "Clôture sur confirmation"}],
         consigner="Localisation exacte, horodatage.", pieges="Ne pas clôturer avant la prise en charge.",
         acteurs="CMS, Pompiers si gravité.")
P05 = _f("P05", "Agression", "securite", "Une personne est agressée physiquement.",
         ["Agression d'un spectateur", "Agression d'un agent"])
P06 = _f("P06", "Altercation ou rixe", "securite", "Dispute ou bagarre entre plusieurs personnes.",
         ["Bagarre entre groupes", "Altercation verbale"])
P13 = _f("P13", "Panne sanitaire ou fuite d'eau", "technique",
         "WC bouchés, fuite d'eau, sanitaires hors service.", ["WC hors service", "Fuite d'eau"])
P17 = _f("P17", "Signalétique", "technique", "Panneau manquant, tombé ou erroné.",
         ["Panneau tombé", "Fléchage erroné"])
P26 = _f("P26", "Colis ou objet suspect", "securite", "Bagage ou objet abandonné.",
         ["Sac abandonné", "Objet suspect"])
P33 = _f("P33", "Personne à mobilité réduite (PMR)", "acces", "Accueil d'une personne PMR.",
         ["Fauteuil roulant"], status="draft")


def _db(fiches=(P01, P05, P06, P13, P17, P26, P33), grid=()):
    return FakeDb(cockpit_wiki_procedures=list(fiches), grid_ref=list(grid))


def ref(**args):
    return R.t_reference(_db(), args, {})


def test_cockpit_identifie_la_fiche_avec_les_mots_de_l_operateur():
    for texte, code in (("un gars par terre près des WC de la tribune 13, il bouge plus", "P01"),
                        ("une personne a fait un malaise", "P01"),
                        ("bagarre devant la buvette", "P06"),
                        ("deux types se tapent dessus", "P06"),
                        ("un panneau de direction est tombé", "P17"),
                        ("une valise seule devant l'entrée", "P26")):
        r = ref(situation=texte)
        assert r["vue"] == "fiche" and r["code"] == code, (texte, r.get("vue"), r.get("candidats"))


def test_guide_court_redige_par_cockpit():
    r = ref(situation="une personne a fait un malaise")
    res = r["resume"]
    assert res.startswith("Fiche P01 Secours à victime. (Si ce n'est pas la situation : la décrire autrement.)")
    assert "À demander :\n1. La personne est-elle consciente ? Respire-t-elle ?\n2. Où exactement" in res
    assert "À faire :\n1. Qualifier l'état et localiser précisément." in res
    assert "Décision : Gravité vitale ? oui : CMS + Pompiers, sécuriser ; non : CMS seul." in res
    assert "À consigner : Localisation exacte, horodatage." in res
    assert "Attention : Ne pas clôturer avant la prise en charge." in res
    assert r["liens"] == [{"libelle": "Ouvrir la fiche P01", "url": "/wiki#P01"}]
    assert "outon" not in res     # le bouton s'affiche sous la reponse, inutile de l'ecrire


def test_mode_dialogue_fiche_structuree_pour_le_modele():
    r = ref(situation="une personne a fait un malaise")
    assert r["mode"] == "dialogue" and r["identifiee_par_cockpit"] is True
    f = r["fiche"]
    assert f["code"] == "P01" and f["a_demander"][0].startswith("La personne est-elle consciente")
    assert f["a_faire"][1] == "Engager le CMS sans délai."
    assert f["decisions"] == ["Gravité vitale ? oui : CMS + Pompiers, sécuriser ; non : CMS seul."]
    assert f["a_consigner"] and f["attention"] and f["precisions"]
    assert f["deroule"] == ["DECISION (voir decisions) : Gravité vitale ?"]
    assert f["acteurs"] == "CMS, Pompiers si gravité."
    p16 = dict(P17, code="P16", flow=[{"k": "start", "t": "Demande"}, {"k": "act", "t": "Qualifier"},
                                      {"k": "engage", "t": "Tango : pose"}, {"k": "watch", "t": "Caméra"},
                                      {"k": "end", "t": "Clôture"}])
    d = R.t_reference(_db(fiches=(p16,)), {"code": "P16"}, {})["fiche"]["deroule"]
    assert d == ["Qualifier", "Engager : Tango : pose", "Surveiller : Caméra"]


def test_a_la_fin_les_etapes_apres_la_derniere_decision():
    # Live 09/10 : << A la decouverte, faire raccompagner >> sortait une fois sur deux
    flow = [{"k": "start", "t": "Signalement"}, {"k": "engage", "t": "Appel général radio"},
            {"k": "ask", "t": "Enfant ou personne vulnérable ?", "y": "Fiche Secours", "n": "Sécurité"},
            {"k": "engage", "t": "À la découverte : patrouille, raccompagner"},
            {"k": "end", "t": "Confirmer la réunification"}]
    p = dict(P01, code="P02", flow=flow)
    f = R.t_reference(_db(fiches=(p,)), {"code": "P02"}, {})["fiche"]
    assert f["a_la_fin"] == ["À la découverte : patrouille, raccompagner", "Confirmer la réunification"]
    assert "a_la_fin" in R.CONSIGNE_DIALOGUE
    # Sans decision : tout le deroule est << a la fin >>, la fin comprise
    p = dict(P01, flow=[{"k": "act", "t": "Faire"}, {"k": "end", "t": "Clore"}])
    assert R.structure(p)["a_la_fin"] == ["Faire", "Clore"]


def test_embranchement_chaque_reponse_avec_ses_actions():
    # Logigrammes reecrits le 09/10/2026 : decisions objectives a plusieurs reponses
    fork = {"k": "fork", "t": "Que constate-t-on sur la personne ?", "branches": [
        {"label": "Ne répond pas ou ne respire pas",
         "flow": [{"k": "engage", "t": "Pompiers en plus du CMS"}, {"k": "engage", "t": "Patrouille : sécuriser"}]},
        {"label": "Consciente, répond", "flow": [{"k": "act", "t": "CMS seul"}]}]}
    p = dict(P01, flow=[{"k": "start", "t": "Alerte"}, fork, {"k": "end", "t": "Clôture"}])
    r = R.t_reference(_db(fiches=(p,)), {"code": "P01"}, {})
    attendu = ("Que constate-t-on sur la personne ? Ne répond pas ou ne respire pas : Engager Pompiers "
               "en plus du CMS, Engager Patrouille : sécuriser ; Consciente, répond : CMS seul.")
    assert r["fiche"]["decisions"] == [attendu]
    assert r["fiche"]["deroule"] == ["DECISION (voir decisions) : Que constate-t-on sur la personne ?"]
    assert "Décision : " + attendu in r["resume"]
    assert "AUCUN moyen" in r["consigne"] and "resume" in r["consigne"]
    # Live VM 09/10 : << il bouge plus >> pris pour << gravite vitale : oui >>
    assert "par deduction" in r["consigne"] and "ne dit pas s'il respire" in r["consigne"]
    # Code cite / question de suite : la fiche suit aussi, pour continuer l'echange
    r = ref(code="P01", situation="et s'il ne respire pas ?")
    assert r["vue"] == "precision" and r["fiche"]["code"] == "P01" and r["passages"]
    assert ref(code="P01")["identifiee_par_cockpit"] is False
    # Ni departage ni << aucune >> : pas de fiche a derouler
    assert "fiche" not in ref(situation="une personne agressée lors d'une altercation")
    assert "fiche" not in ref(situation="quel temps fera-t-il demain")


def test_fiche_longue_les_precisions_cedent_avant_la_fiche():
    long = dict(P01, details=["Précision numéro %d " % i + "x" * 280 for i in range(5)],
                questions=["Question %d assez longue pour peser dans le résultat ?" % i + "y" * 450
                           for i in range(6)])
    db = _db(fiches=(long,))
    ok, r = AT.call(db, "cockpit_reference", {"code": "P01", "situation": "précision numéro"}, {})
    assert ok and "fiche" in r and not r.get("tronque")
    assert len(r["fiche"]["precisions"]) < 5
    assert len(json.dumps(r, ensure_ascii=False)) <= AT.TOOLS["cockpit_reference"]["max_chars"]
    # Les autres outils gardent le plafond commun
    assert AT._borner({"resume": "x", "gros": ["y" * 100] * 80})["tronque"]


def test_deux_fiches_proches_cockpit_ecrit_la_question():
    r = ref(situation="une personne agressée lors d'une altercation")
    assert r["vue"] == "ambigu" and set(r["candidats"]) == {"P05", "P06"}
    assert "Question à poser à l'opérateur : « Est-ce plutôt" in r["resume"]
    assert "Agression (P05)" in r["resume"] and "Altercation ou rixe (P06)" in r["resume"]
    assert {l["url"] for l in r["liens"]} == {"/wiki#P05", "/wiki#P06"}


def test_aucune_fiche_jamais_de_procedure_improvisee():
    r = ref(situation="quel temps fera-t-il demain")
    assert r["vue"] == "aucune" and not r.get("liens")
    assert "Ne pas improviser de procédure" in r["resume"]
    # Liste fermee pour que le modele choisisse, puis rappelle avec le code
    assert any(c.startswith("P01 Secours à victime") for c in r["catalogue"])
    assert "code=" in r["consigne"]


def test_un_mot_de_lieu_seul_ne_retient_pas_une_fiche():
    # << parking >> / << WC >> localisent, ils ne decrivent pas la situation
    p18 = _f("P18", "Circulation et congestion", "flux", "Bouchon sur un parking ou une route.",
             ["Parking saturé"])
    db = _db(fiches=(P01, P13, p18))
    assert R.t_reference(db, {"situation": "un chien errant sur le parking"}, {})["vue"] == "aucune"
    assert R.t_reference(db, {"situation": "ça bouchonne au parking Bleu"}, {})["code"] == "P18"


def test_live_09_10_formulations_d_operateurs():
    assert ref(situation="Juste deux gars qui s'engueulent")["code"] == "P06"
    assert ref(situation="Il y a du grabuge au camping")["code"] == "P06"
    # << odeur >> seul ne designe pas une panne sanitaire
    p13 = dict(P13, situation="WC bouchés, fuite d'eau, odeur, sanitaires hors service.")
    assert R.t_reference(_db(fiches=(P01, p13)), {"situation": "Une odeur de gaz près des stands"},
                         {})["vue"] == "aucune"


def test_brouillon_jamais_propose():
    assert ref(situation="fauteuil roulant PMR")["vue"] == "aucune"
    assert "P33" not in str(ref(situation="fauteuil roulant PMR")["catalogue"])
    assert "Aucune fiche publiée P33" in ref(code="P33")["resume"]


def test_code_cite_et_question_de_suite():
    r = ref(code="p01")
    assert r["vue"] == "fiche" and r["resume"].startswith("Fiche P01 Secours à victime.")
    assert "Si ce n'est pas la situation" not in r["resume"]   # choisie par l'operateur
    r = ref(code="P01", situation="et s'il ne respire pas ?")
    assert r["vue"] == "precision"
    assert "ne respire pas" in r["resume"] and "CMS + Pompiers" in r["resume"]
    assert r["resume"].startswith("Sur la fiche P01 Secours à victime :")
    # << fiche P05 >> dans la situation = code
    assert ref(situation="donne-moi la fiche P05")["code"] == "P05"


def test_carre_du_carroyage():
    grid = [{"grid_ref": "P5", "latitude": 47.9641, "longitude": 0.1829,
             "waze_url": "https://ul.waze.com/ul?ll=47.9641%2C0.1829&navigate=yes"}]
    r = R.t_reference(_db(grid=grid), {"carre": "carré p 5"}, {})
    assert r["trouve"] and "Carré P5" in r["resume"] and "47.96410, 0.18290" in r["resume"]
    assert r["liens"][0]["url"].startswith("https://ul.waze.com/ul?")
    r = R.t_reference(_db(grid=grid), {"carre": "ZZ999"}, {})
    assert not r["trouve"] and "absent du carroyage" in r["resume"]


def test_resultat_aucune_tient_sous_le_plafond_avec_30_fiches():
    fiches = [_f("P%02d" % i, "Fiche numéro %d avec un titre assez long" % i, "securite", "Situation.",
                 ["Sous-cas un assez long", "Sous-cas deux assez long", "Sous-cas trois", "Sous-cas quatre"])
              for i in range(1, 31)]
    r = R.t_reference(_db(fiches=fiches), {"situation": "quel temps demain"}, {})
    borne = AT._borner(r)
    assert len(borne["catalogue"]) == 30 and "consigne" in borne
    assert len(json.dumps(borne, ensure_ascii=False)) <= AT.RESULT_MAX_CHARS


def test_manifeste_reference_remplace_wiki():
    noms = [t["function"]["name"] for t in AT.manifest()]
    assert "cockpit_reference" in noms and "cockpit_wiki_procedures" not in noms
    assert "cockpit_wiki_procedures" in AT.TOOLS
    assert any("fiches réflexes" in s for s in AT.presentation()["sait_faire"])


def test_liens_liste_blanche():
    import alfred_chat as AC
    ok = AC._liens_surs([{"url": "/wiki#P01", "libelle": "Ouvrir la fiche P01"},
                         {"url": "https://ul.waze.com/ul?ll=47.9%2C0.1&navigate=yes", "libelle": "Waze"},
                         {"url": "javascript:alert(1)", "libelle": "x"},
                         {"url": "https://evil.example/wiki#P01", "libelle": "x"},
                         {"url": "/wiki#P01\"><script>", "libelle": "x"},
                         {"url": "/wiki#P01", "libelle": "doublon"}])
    assert [l["url"] for l in ok] == ["/wiki#P01", "https://ul.waze.com/ul?ll=47.9%2C0.1&navigate=yes"]
