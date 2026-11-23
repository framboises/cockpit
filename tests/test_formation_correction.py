"""Correction serveur des 7 epreuves (formation/correction.py) et enonces (formation/banque.py).

Fonctions pures : aucune base, aucun serveur.
"""
import json
import random

from formation import banque, correction


def _radio_prise(msg_id, delai_ms=500, coef=1.0):
    """Prise de `msg_id` `delai_ms` (temps de jeu) apres son affichage."""
    debuts = correction.radio_affichages_ms()
    return {"type": "prise", "msg": msg_id, "t_ms": (debuts[msg_id][0] + delai_ms) * coef}


def _ids_radio(pour_moi):
    return [m["id"] for m in banque.charger()["radio"] if m["pour_moi"] is pour_moi]


# ---------------------------------------------------------------- radio

def test_radio_parfait():
    actions = [_radio_prise(i, 1000) for i in _ids_radio(True)]
    r = correction.score("radio", actions, 1.0)
    assert r["score"] == 100
    assert r["detail"]["pris_a_raison"] == 7
    assert r["detail"]["priorites_rapides"] == 1
    assert r["drapeaux"] == []


def test_radio_mixte():
    mien = _ids_radio(True)
    # 5 pris a raison sans la priorite r08, 2 pris a tort
    pris = [i for i in mien if i != "r08"][:5]
    actions = [_radio_prise(i, 2500) for i in pris]
    actions += [_radio_prise(i, 500) for i in _ids_radio(False)[:2]]
    r = correction.score("radio", actions, 1.0)
    assert r["detail"]["pris_a_raison"] == 5
    assert r["detail"]["pris_a_tort"] == 2
    assert r["detail"]["priorites_rapides"] == 0
    assert r["score"] == 43  # (5 - 2) / 7 * 100 = 42.86


def test_radio_priorite_lente_non_rapide():
    actions = [_radio_prise("r08", 2100)]
    r = correction.score("radio", actions, 1.0)
    assert r["detail"]["priorites_rapides"] == 0
    assert r["score"] == 14  # 1 / 7 * 100


def test_radio_priorite_coef_allonge_seuil():
    # 2,5 s reels apres l'affichage a coef 1.33 = 1,88 s de jeu : rapide
    debut = correction.radio_affichages_ms()["r08"][0]
    actions = [{"type": "prise", "msg": "r08", "t_ms": debut * 1.33 + 2500}]
    r = correction.score("radio", actions, 1.33)
    assert r["detail"]["priorites_rapides"] == 1


def test_radio_prise_avant_affichage_ignoree():
    debut = correction.radio_affichages_ms()["r03"][0]
    actions = [{"type": "prise", "msg": "r03", "t_ms": debut - 100}]
    r = correction.score("radio", actions, 1.0)
    assert r["detail"]["pris_a_raison"] == 0
    assert "horodatage_incoherent" in r["drapeaux"]


def test_radio_une_seule_prise_par_message():
    actions = [_radio_prise("r02", 100), _radio_prise("r02", 200)]
    r = correction.score("radio", actions, 1.0)
    assert r["detail"]["pris_a_tort"] == 1
    assert "doublon" in r["drapeaux"]


def test_radio_indicatif_pc_org():
    b = banque.charger()
    for m in b["radio"] + b["entrainement"]["radio"]:
        pour_moi = m["texte"].startswith(("PC Org Sud-Est de ", "PRIORITÉ, PRIORITÉ. PC Org Sud-Est de "))
        assert pour_moi is m["pour_moi"], m["id"]
    txt = json.dumps(b, ensure_ascii=False)
    # indicatif complet partout (decision du 23/11) : jamais « PC Org » seul
    assert txt.count("PC Org") == txt.count("PC Org Sud-Est")
    for ancien in ("PC Est", "PC Orga", "PC Nord", "PC Sud", "PC Village"):
        assert ancien not in txt, ancien


def test_radio_affichages_cadence():
    aff = correction.radio_affichages_ms()
    assert aff["r01"] == (800, 8800)
    assert aff["r05"] == (800 + 4 * 8000, 800 + 4 * 8000 + 6400)
    assert aff["r14"][1] == correction.duree_min_ms("radio", 1.0) == 800 + 2 * 54400


# ---------------------------------------------------------------- appel

def _meilleurs_choix_appel():
    out = []
    for appel in banque.charger()["appel"]:
        for etape, best in correction.appel_ideal(appel):
            out.append({"type": "reponse", "question": etape["id"], "choix": best["id"], "t_ms": 1000})
    return out


def _tous_les_chemins(appel):
    """Tous les parcours possibles d'un appel : liste de [(etape, choix)]."""
    etapes = {e["id"]: e for e in appel["etapes"]}
    out = []

    def descendre(e, acc, vues):
        assert e["id"] not in vues, "boucle sur " + e["id"]
        for ch in e["choix"]:
            suite = ch.get("suite")
            if suite:
                assert suite in etapes, (e["id"], suite)
                descendre(etapes[suite], acc + [(e, ch)], vues | {e["id"]})
            else:
                out.append(acc + [(e, ch)])
    descendre(appel["etapes"][0], [], frozenset())
    return out


def test_appel_parfait():
    r = correction.score("appel", _meilleurs_choix_appel(), 1.0)
    assert r["score"] == 100 and r["drapeaux"] == []
    assert r["detail"]["max"] == 12  # 3 etapes ideales par appel, comme avant l'arbre


def test_appel_arbre_coherent():
    for appel in banque.charger()["appel"]:
        ids = [c["id"] for e in appel["etapes"] for c in e["choix"]] + [e["id"] for e in appel["etapes"]]
        assert len(ids) == len(set(ids))
        chemins = _tous_les_chemins(appel)
        ideal = [(e["id"], c["id"]) for e, c in correction.appel_ideal(appel)]
        maxi = sum(c["points"] for _, c in correction.appel_ideal(appel))
        assert maxi == 2 * len(ideal)
        atteintes = set()
        for ch in chemins:
            atteintes.update(e["id"] for e, _ in ch)
            pts = sum(c["points"] for _, c in ch)
            if [(e["id"], c["id"]) for e, c in ch] != ideal:
                assert pts < maxi, ch  # aucun detour ne vaut le chemin ideal
        # toutes les etapes sont atteignables
        assert atteintes == {e["id"] for e in appel["etapes"]}


def test_appel_mixte():
    actions = [
        {"type": "reponse", "question": "a1s1", "choix": "a1s1c2", "t_ms": 1},  # 1 (nom d'abord)
        {"type": "reponse", "question": "a1r1", "choix": "a1r1c1", "t_ms": 2},  # 0,5 (rattrapage)
        {"type": "reponse", "question": "a1s2", "choix": "a1s2c1", "t_ms": 3},  # 2
        {"type": "reponse", "question": "a1s3", "choix": "a1s3c3", "t_ms": 4},  # 0 (patrouille)
        {"type": "reponse", "question": "a1r6", "choix": "a1r6c1", "t_ms": 5},  # 0,5
        {"type": "reponse", "question": "a2s1", "choix": "a2s1c1", "t_ms": 6},  # 2
        {"type": "reponse", "question": "a2s1", "choix": "a2s1c3", "t_ms": 7},  # doublon, ignore
    ]
    r = correction.score("appel", actions, 1.0)
    assert r["score"] == 50  # 6 / 12
    assert r["detail"]["chemins"] == {"a1": ["a1s1", "a1r1", "a1s2", "a1s3", "a1r6"], "a2": ["a2s1"]}


def test_appel_reponse_hors_chemin_ignoree():
    # a1s1c1 mene a a1s2 : une reponse a a1r1 (branche non suivie) ne compte pas
    actions = [{"type": "reponse", "question": "a1s1", "choix": "a1s1c1", "t_ms": 1},
               {"type": "reponse", "question": "a1r1", "choix": "a1r1c1", "t_ms": 2}]
    r = correction.score("appel", actions, 1.0)
    assert r["detail"]["points"] == 2 and r["detail"]["chemins"]["a1"] == ["a1s1"]
    assert "action_inconnue" in r["drapeaux"]


def test_appel_fin_anticipee():
    # « je vous rappelle dans cinq minutes » clot l'appel : 2 + 2 + 0 sur 6
    actions = [{"type": "reponse", "question": q, "choix": c, "t_ms": k}
               for k, (q, c) in enumerate((("a1s1", "a1s1c1"), ("a1s2", "a1s2c1"), ("a1s3", "a1s3c4")))]
    r = correction.score("appel", actions, 1.0)
    assert r["detail"]["points"] == 4 and r["drapeaux"] == []
    fin = next(c for e in banque.charger()["appel"][0]["etapes"] for c in e["choix"] if c["id"] == "a1s3c4")
    assert "suite" not in fin and fin["reaction"]


# ---------------------------------------------------------------- main courante

def _fiche(fid, **kw):
    f = next(x for x in banque.charger()["mc"]["fiches"] if x["id"] == fid)
    a = dict(f["ans"])
    a.update(kw)
    return dict(type="reponse", question=fid, t_ms=1000, **a)


def test_mc_parfait():
    actions = [_fiche("m1"), _fiche("m2", heure=" 22:53 "), _fiche("m3")]
    assert correction.score("mc", actions, 1.0)["score"] == 100


def test_mc_mixte():
    actions = [_fiche("m1", zone="Village"), _fiche("m2", heure="2253", moyen="Aucun")]
    r = correction.score("mc", actions, 1.0)
    assert r["score"] == 42  # (3 + 2) / 12 * 100


# ---------------------------------------------------------------- patrouilles

# Journal ideal (test_formation_patrouilles.journal_ideal) : le moyen admissible le
# plus proche, le plus urgent d'abord, une Patrouille en relais d'un Secours introuvable.
def _ideal():
    from test_formation_patrouilles import journal_ideal
    return journal_ideal()


def test_patrouilles_parfait():
    r = correction.score("patrouilles", _ideal(), 1.0)
    assert r["score"] == 100


def test_patrouilles_mixte():
    actions = [a for a in _ideal() if a["incident"] != "I3"]
    actions.append({"type": "engager", "moyen": "M1", "incident": "I3", "t_ms": 15500})
    r = correction.score("patrouilles", actions, 1.0)
    n = len(banque.charger()["patrouilles"]["INCIDENTS"])
    assert r["detail"]["traites"] == n - 1
    assert r["detail"]["erreurs"] == 1
    assert r["score"] == round((n - 1 - 0.5) / n * 100)


# ---------------------------------------------------------------- acces

def _badges():
    return banque.charger()["acces"]["badges"]


def _journal_acces(decision_de, delai_ms=1500, coef=1.0):
    """Decisions successives, chacune `delai_ms` (temps de jeu) apres la precedente,
    affichage du badge suivant envoye 450 ms apres (informatif)."""
    actions, t = [], 0
    for b in _badges():
        actions.append({"type": "badge_affiche", "badge": b["id"], "t_ms": t})
        t += delai_ms * coef
        actions.append({"type": "decision", "badge": b["id"], "decision": decision_de(b), "t_ms": t})
    return actions


def test_acces_parfait():
    r = correction.score("acces", _journal_acces(lambda b: b["ok"]), 1.0)
    assert r["score"] == 100


def test_acces_mixte():
    faux = {"b01", "b02", "b03"}
    r = correction.score("acces", _journal_acces(lambda b: 1 - b["ok"] if b["id"] in faux else b["ok"]), 1.0)
    assert r["score"] == 85  # 3 fautes sur 20 badges


def test_acces_decision_tardive_fausse():
    # 6,2 s de jeu apres la decision precedente : au-dela de 5 s + 1 s de marge
    actions = _journal_acces(lambda b: b["ok"], delai_ms=6200)
    r = correction.score("acces", actions, 1.0)
    assert r["score"] == 0
    assert r["detail"]["tardives"] == 20
    # 5,9 s : dans la marge
    assert correction.score("acces", _journal_acces(lambda b: b["ok"], delai_ms=5900), 1.0)["score"] == 100
    # le meme delai reel de 6,2 s est dans les temps a coef 1.33 (4,66 s de jeu)
    actions = _journal_acces(lambda b: b["ok"], delai_ms=6200 / 1.33, coef=1.33)
    assert correction.score("acces", actions, 1.33)["score"] == 100


def test_acces_badge_affiche_tardif_ne_sauve_pas():
    # le client renvoie badge_affiche juste avant de decider pour remettre ses 5 s a zero
    actions, t = [], 0
    for b in _badges():
        t += 6500
        actions.append({"type": "badge_affiche", "badge": b["id"], "t_ms": t - 100})
        actions.append({"type": "decision", "badge": b["id"], "decision": b["ok"], "t_ms": t})
    assert correction.score("acces", actions, 1.0)["score"] == 0


def test_acces_decision_manquante_reference_expiration():
    # pas de decision sur b01 : b02 est montre a 5 s + 450 ms, decision 1 s plus tard
    b02 = _badges()[1]
    actions = [{"type": "decision", "badge": "b02", "decision": b02["ok"], "t_ms": 6450}]
    r = correction.score("acces", actions, 1.0)
    assert r["detail"]["bonnes"] == 1
    # decide avant meme sa reference : incoherent
    actions = [{"type": "decision", "badge": "b02", "decision": b02["ok"], "t_ms": 3000}]
    r = correction.score("acces", actions, 1.0)
    assert r["detail"]["bonnes"] == 0
    assert "horodatage_incoherent" in r["drapeaux"]


def test_acces_decision_sans_affichage_comptee():
    # badge_affiche n'est plus requis : seul l'ordre fixe et les decisions comptent
    actions = [{"type": "decision", "badge": "b01", "decision": 1, "t_ms": 100}]
    r = correction.score("acces", actions, 1.0)
    assert r["score"] == 5


def test_acces_doublon():
    actions = [{"type": "decision", "badge": "b01", "decision": 1, "t_ms": 100},
               {"type": "decision", "badge": "b01", "decision": 0, "t_ms": 200}]
    r = correction.score("acces", actions, 1.0)
    assert r["score"] == 5
    assert "doublon" in r["drapeaux"]


# ---------------------------------------------------------------- logique

def _bonnes_logique(t_ms=10000):
    return [{"type": "reponse", "question": q["id"], "choix": q["a"], "t_ms": t_ms}
            for q in banque.charger()["logique"]]


def test_logique_parfait():
    assert correction.score("logique", _bonnes_logique(), 1.0)["score"] == 100


def test_logique_mixte():
    actions = _bonnes_logique()
    actions[0]["choix"] = "l1c1"
    actions[1]["choix"] = "l2c1"
    assert correction.score("logique", actions, 1.0)["score"] == 60


def test_reponse_hors_delai_logique_ignoree():
    actions = _bonnes_logique()
    actions[4]["t_ms"] = 150001
    assert correction.score("logique", actions, 1.0)["score"] == 80
    # a coef 1.33 la meme reponse est dans les 2 min 30 x 1.33
    assert correction.score("logique", actions, 1.33)["score"] == 100


# ---------------------------------------------------------------- situations

def test_situations_parfait():
    actions = []
    for s in banque.charger()["situations"]:
        best = max(s["choix"], key=lambda c: c["points"])
        actions.append({"type": "reponse", "question": s["id"], "choix": best["id"], "t_ms": 1})
    assert correction.score("situations", actions, 1.0)["score"] == 100


def test_situations_mixte():
    actions = [
        {"type": "reponse", "question": "s1", "choix": "s1c2", "t_ms": 1},  # 2
        {"type": "reponse", "question": "s2", "choix": "s2c3", "t_ms": 2},  # 1
    ]
    assert correction.score("situations", actions, 1.0)["score"] == 50


# ---------------------------------------------------------------- generalites

def test_sans_reponse_zero():
    for ep in ("radio", "appel", "mc", "patrouilles", "acces", "logique", "situations"):
        r = correction.score(ep, [], 1.0)
        assert r["score"] == 0, ep


def test_durees():
    assert correction.duree_min_ms("radio", 1.33) == round(109600 * 1.33)
    assert correction.duree_max_ms("radio", 1.0) == 114600
    inc = banque.charger()["patrouilles"]["INCIDENTS"]
    assert correction.duree_min_ms("patrouilles", 1.0) == inc[-1]["t"] * 1000
    fin = max(i["t"] + i["dl"] for i in inc)
    assert correction.duree_max_ms("patrouilles", 1.0) == fin * 1000 + 10000 + 5000
    assert fin >= 180  # scenario long (23/11/2026)
    assert correction.duree_max_ms("acces", 1.0) == 125000
    assert correction.duree_max_ms("logique", 1.0) == 155000
    assert correction.duree_max_ms("mc", 1.33) == round(600000 * 1.33) + 5000
    assert correction.duree_min_ms("logique", 1.0) is None


# ---------------------------------------------------------------- enonces

def test_enonce_sans_reponses():
    for ep in ("appel", "mc", "logique", "situations"):
        txt = json.dumps(banque.enonce(ep, random.Random(1)), ensure_ascii=False)
        assert '"points"' not in txt, ep
        assert '"ans"' not in txt, ep
        assert '"a":' not in txt, ep
        assert '"ok"' not in txt, ep
    for q in banque.enonce("logique", random.Random(2))["questions"]:
        for c in q["choix"]:
            assert set(c) == {"id", "texte"}


def test_enonce_ne_modifie_pas_la_banque():
    banque.enonce("logique", random.Random(3))
    assert "a" in banque.charger()["logique"][0]


def test_ordre_fixe_temps_reel():
    for ep in ("radio", "patrouilles", "acces"):
        assert banque.enonce(ep, random.Random(1)) == banque.enonce(ep, random.Random(99)), ep
    radio = banque.enonce("radio", random.Random(5))["messages"]
    assert [m["id"] for m in radio] == ["r%02d" % i for i in range(1, 15)]
    assert all("pour_moi" in m and "priorite" in m for m in radio)
    assert all("ok" in b for b in banque.enonce("acces", random.Random(5))["badges"])


def test_ordre_melange_questions():
    ordres = set()
    choix = set()
    for graine in range(20):
        e = banque.enonce("logique", random.Random(graine))
        ordres.add(tuple(q["id"] for q in e["questions"]))
        choix.add(tuple(c["id"] for c in e["questions"][0]["choix"]))
    assert len(ordres) > 1
    assert len(choix) > 1
    # les autres epreuves a question sont aussi melangees
    for ep, cle in (("situations", "questions"), ("mc", "fiches"), ("appel", "appels")):
        vus = {tuple(x["id"] for x in banque.enonce(ep, random.Random(g))[cle]) for g in range(20)}
        assert len(vus) > 1, ep


# ---------------------------------------------------------------- journal hostile

POUBELLE = [
    "texte", 42, None, ["liste"],
    {"type": "prise", "msg": ["r01"], "t_ms": 1000},
    {"type": "reponse", "question": ["l1"], "choix": "l1c3", "t_ms": 1000},
    {"type": "reponse", "question": {"x": 1}, "choix": ["a"], "t_ms": 1000},
    {"type": "decision", "badge": ["b01"], "decision": 1, "t_ms": 1000},
    {"type": "engager", "moyen": ["P1"], "incident": "I1", "t_ms": 3000},
    {"type": "engager", "moyen": "P1", "incident": {"id": "I1"}, "t_ms": 3000},
    {"type": "badge_affiche", "badge": ["b01"], "t_ms": 0},
]
TEMPS_INVALIDES = [float("nan"), float("inf"), "abc", None, True, [1], 10 ** 400, "1e999"]


def _actions_temps_invalides():
    out = []
    for t in TEMPS_INVALIDES:
        out += [{"type": "prise", "msg": "r01", "t_ms": t},
                {"type": "reponse", "question": "l1", "choix": "l1c3", "t_ms": t},
                {"type": "reponse", "question": "a1s1", "choix": "a1s1c1", "t_ms": t},
                {"type": "reponse", "question": "s1", "choix": "s1c2", "t_ms": t},
                dict(_fiche("m1"), t_ms=t),
                {"type": "decision", "badge": "b01", "decision": 1, "t_ms": t},
                {"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": t}]
    return out


def test_journal_hostile_ne_leve_pas():
    for ep in correction.EPREUVES:
        r = correction.score(ep, POUBELLE, 1.0)
        assert r["score"] == 0, ep
        assert "action_inconnue" in r["drapeaux"], ep
        r = correction.score(ep, _actions_temps_invalides(), 1.0)
        assert r["score"] == 0, ep
        assert "horodatage_incoherent" in r["drapeaux"], ep
        for autre in ({"x": 1}, "abc", 12, None):
            assert correction.score(ep, autre, 1.0)["score"] == 0, ep


def test_journal_hostile_champs_mc():
    actions = [dict(_fiche("m1"), cat=["Technique"], zone=None, heure=2241)]
    assert correction.score("mc", actions, 1.0)["detail"]["champs_justes"] == 1  # moyen seul


def test_journal_hostile_melange_garde_le_valide():
    actions = POUBELLE + _actions_temps_invalides() + _bonnes_logique()
    assert correction.score("logique", actions, 1.0)["score"] == 100


def test_coef_invalide_vaut_un():
    actions = _bonnes_logique()
    for coef in (0, -1, float("nan"), float("inf"), "x", None, 10 ** 400):
        assert correction.score("logique", actions, coef)["score"] == 100
        assert correction.score("radio", [_radio_prise("r01")], coef)["detail"]["pris_a_raison"] == 1


def test_reemission_meme_aid_toutes_epreuves():
    # Reemission apres timeout (meme aid) : la premiere recue fait foi, drapeau doublon
    faux = {"type": "engager", "moyen": "T1", "incident": "I1", "t_ms": 3000, "aid": "p-1"}
    r = correction.score("patrouilles", [faux, dict(faux)], 1.0)
    assert r["detail"]["erreurs"] == 1 and "doublon" in r["drapeaux"]
    drapeaux = []
    j = correction.journal([{"type": "reponse", "t_ms": 5, "aid": "a"}, {"type": "reponse", "t_ms": 1, "aid": "a"},
                            {"type": "reponse", "t_ms": 2, "aid": "b"}], ("reponse",), drapeaux)
    assert [t for t, _ in j] == [2.0, 5.0] and drapeaux == ["doublon"]
