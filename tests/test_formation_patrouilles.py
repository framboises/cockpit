"""Plan, plus court chemin et rejeu deterministe des patrouilles (formation/patrouilles.py)."""
from formation import banque, patrouilles


def _incidents():
    return banque.charger()["patrouilles"]["INCIDENTS"]


def _units():
    return banque.charger()["patrouilles"]["UNITS"]


def journal_ideal(reaction_ms=1500, ordre=None):
    """Candidat ideal simule sur le modele du rejeu : tous les 100 ms, les incidents
    ouverts (apres `reaction_ms`) non attribues, du plus urgent au moins urgent (ou
    `ordre`), recoivent le moyen admissible libre le plus proche, s'il arrive a temps.
    Une Patrouille part sur un Secours seulement quand aucun Secours n'est libre."""
    ordre = ordre or (lambda i: (i["t"] + i["dl"], i["id"]))
    units = {u["id"]: {"type": u["type"], "pos": tuple(u["pos"]), "libre_a": 0.0,
                       "indispo": [(w["de"] * 1000, w["a"] * 1000) for w in u.get("indispo") or []]}
             for u in _units()}
    incs, fait, actions = _incidents(), set(), []
    fin = max(i["t"] + i["dl"] for i in incs) * 1000
    for t in range(0, fin, 100):
        ouverts = [i for i in incs if i["id"] not in fait
                   and i["t"] * 1000 + reaction_ms <= t < (i["t"] + i["dl"]) * 1000]
        for inc in sorted(ouverts, key=ordre):
            admis = patrouilles.type_admis(units, inc["type"], t)
            choix = [(len(patrouilles.chemin(u["pos"], inc["pos"])), uid) for uid, u in units.items()
                     if u["type"] == admis and patrouilles.libre(u, t)]
            if not choix:
                continue
            n, uid = min(choix)
            arrivee = t + (n - 1) * patrouilles.PAS_MS
            if arrivee > (inc["t"] + inc["dl"]) * 1000:
                continue
            units[uid]["pos"], units[uid]["libre_a"] = tuple(inc["pos"]), arrivee + patrouilles.sur_place_ms(inc)
            fait.add(inc["id"])
            actions.append({"type": "engager", "moyen": uid, "incident": inc["id"], "t_ms": t})
    return actions


def _inc(iid):
    return next(i for i in _incidents() if i["id"] == iid)


def _ech(iid):
    i = _inc(iid)
    return (i["t"] + i["dl"]) * 1000


def test_chemin_contourne_la_piste():
    p = patrouilles.chemin((0, 0), (0, 11))
    assert p[0] == (0, 0) and p[-1] == (0, 11)
    traversee = [c for c in p if c[1] == 6]
    assert traversee and all(c[0] in (1, 6) for c in traversee)
    assert len(p) - 1 == 13  # 11 colonnes + descente et remontee par la passerelle de la ligne 1


def test_chemin_evite_les_travaux():
    p = patrouilles.chemin((3, 2), (3, 5))
    assert (3, 3) not in p and (3, 4) not in p
    assert patrouilles.chemin((0, 0), (0, 0)) == [(0, 0)]


def test_scenario_long():
    incs = _incidents()
    assert len(incs) >= 40 and max(i["t"] + i["dl"] for i in incs) >= 180
    assert len(_units()) <= 9  # touches 1 a 9
    assert all(isinstance(i.get("sp"), int) and i["sp"] > 0 for i in incs)


def test_rejeu_ideal():
    actions = journal_ideal()
    r = patrouilles.rejouer(actions, 1.0)
    n = len(_incidents())
    assert (r["traites"], r["plus_proches"], r["erreurs"], r["ignores"]) == (n, n, 0, 0)
    assert r["points"] == n
    d = {x["id"]: x for x in r["detail_incidents"]}
    # relais Patrouille sur un Secours : S1 hors du plan (40-115 s) et S2 deja engage
    assert {k for k, x in d.items() if x["relais"]} == {"I9", "I14", "I21"}
    quand = {a["incident"]: (a["moyen"], a["t_ms"]) for a in actions}
    # dilemme Tango : le portail bloque (I7, 12 s) passe avant la coupure (I6, 27 s)
    assert quand["I7"][0] == quand["I6"][0] == "T1" and quand["I7"][1] < quand["I6"][1]
    # sortie de parking (Flux) pendant l'escorte de Moto 1 (125-165 s) : on attend la moto
    assert quand["I28"] == ("M1", 165000)


def test_ordre_d_arrivee_tombe_dans_les_dilemmes():
    naif = journal_ideal(ordre=lambda i: (i["t"], int(i["id"][1:])))
    r = patrouilles.rejouer(naif, 1.0)
    d = {x["id"]: x for x in r["detail_incidents"]}
    # un seul moyen pour deux incidents : le moins urgent pris d'abord, l'urgent expire
    # (I7 portail / I6 coupure, Tango ; I24 car / I23 barriere, Moto ; I31 rampe / I30 groupe, Tango)
    assert [k for k, x in d.items() if x["statut"] == "manque"] == ["I7", "I24", "I31"]


def test_lenteur_coute():
    n = len(_incidents())
    r = patrouilles.rejouer(journal_ideal(reaction_ms=9000), 1.0)
    assert r["traites"] < n


def test_pas_de_relais_hors_secours():
    # Flux sans Moto (escorte) : une Patrouille reste une erreur, rien n'est attribue
    r = patrouilles.rejouer([{"type": "engager", "moyen": "P1", "incident": "I28", "t_ms": 130000}], 1.0)
    assert r["erreurs"] == 1 and r["traites"] == 0


def test_sur_place_par_incident():
    # P3 sur la rixe I1 (1 case, 20 s sur place) : encore occupe 15 s apres l'arrivee
    a = [{"type": "engager", "moyen": "P3", "incident": "I1", "t_ms": 3000}]
    i1 = _inc("I1")
    assert patrouilles.sur_place_ms(i1) == i1["sp"] * 1000 == 20000
    occupe = a + [{"type": "engager", "moyen": "P3", "incident": "I4", "t_ms": 3430 + 19000}]
    r = patrouilles.rejouer(occupe, 1.0)
    assert r["ignores"] == 1 and "moyen_occupe" in r["drapeaux"]
    libre = a + [{"type": "engager", "moyen": "P3", "incident": "I4", "t_ms": 3430 + 20000}]
    assert patrouilles.rejouer(libre, 1.0)["ignores"] == 0
    # sans `sp` (entrainement, ancien scenario) : SUR_PLACE_MS
    assert patrouilles.sur_place_ms({"id": "X"}) == patrouilles.SUR_PLACE_MS
    assert patrouilles.sur_place_ms({"id": "X", "sp": True}) == patrouilles.SUR_PLACE_MS


def test_plus_proche_note():
    p3 = [{"type": "engager", "moyen": "P3", "incident": "I1", "t_ms": 3000}]
    p1 = [{"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": 3000}]
    r3, r1 = patrouilles.rejouer(p3, 1.0), patrouilles.rejouer(p1, 1.0)
    assert r3["traites"] == r1["traites"] == 1
    assert r3["plus_proches"] == 1 and r3["points"] == 1
    assert r1["plus_proches"] == 0 and r1["points"] == patrouilles.POINTS_PAS_LE_PLUS_PROCHE
    # P3 retenu sur la rixe : sur I4 (Acces Est), P2 est le plus proche des moyens libres
    a = p3 + [{"type": "engager", "moyen": "P2", "incident": "I4", "t_ms": 21000}]
    i4 = next(x for x in patrouilles.rejouer(a, 1.0)["detail_incidents"] if x["id"] == "I4")
    assert i4["plus_proche"] is True


def test_relais_patrouille_sur_secours():
    # Secours 1 disponible : la patrouille sur un secours reste une erreur
    r = patrouilles.rejouer([{"type": "engager", "moyen": "P2", "incident": "I3", "t_ms": 15000}], 1.0)
    assert r["erreurs"] == 1 and r["traites"] == 0
    # S1 hors du plan, mais S2 libre : toujours une erreur
    r = patrouilles.rejouer([{"type": "engager", "moyen": "P2", "incident": "I9", "t_ms": 47000}], 1.0)
    assert r["erreurs"] == 1
    # S1 hors du plan et S2 engage sur l'enfant (I8) : la patrouille est admise sur I9
    s2 = {"type": "engager", "moyen": "S2", "incident": "I8", "t_ms": 43000}
    r = patrouilles.rejouer([s2, {"type": "engager", "moyen": "P2", "incident": "I9", "t_ms": 47000}], 1.0)
    assert r["erreurs"] == 0 and r["traites"] == 2
    i9 = next(x for x in r["detail_incidents"] if x["id"] == "I9")
    assert i9["relais"] is True
    # et Secours 1 ne peut pas etre engage pendant sa fenetre
    r = patrouilles.rejouer([s2, {"type": "engager", "moyen": "S1", "incident": "I9", "t_ms": 47000}], 1.0)
    assert r["ignores"] == 1 and "moyen_occupe" in r["drapeaux"]
    # Tango sur le secours, meme sans Secours libre : erreur
    r = patrouilles.rejouer([s2, {"type": "engager", "moyen": "T1", "incident": "I9", "t_ms": 47000}], 1.0)
    assert r["erreurs"] == 1


def test_mauvais_type_compte_erreur():
    # Tango 1 sur la rixe I1 : erreur, et il y va quand meme (6 cases, arrivee 5,58 s)
    cases = len(patrouilles.chemin((2, 4), (1, 9))) - 1
    arrivee = 3000 + cases * patrouilles.PAS_MS
    faux = {"type": "engager", "moyen": "T1", "incident": "I1", "t_ms": 3000}
    # pendant son trajet l'incident est bloque : la patrouille envoyee entre-temps est ignoree
    r = patrouilles.rejouer([faux, {"type": "engager", "moyen": "P3", "incident": "I1", "t_ms": arrivee - 1}], 1.0)
    assert (r["erreurs"], r["traites"], r["ignores"]) == (1, 0, 1)
    assert "incident_en_attente" in r["drapeaux"]
    # a son compte rendu l'incident se rouvre : la patrouille le traite
    actions = [faux, {"type": "engager", "moyen": "P3", "incident": "I1", "t_ms": arrivee}]
    r = patrouilles.rejouer(actions, 1.0)
    assert (r["erreurs"], r["traites"], r["ignores"]) == (1, 1, 0)
    i1 = next(d for d in r["detail_incidents"] if d["id"] == "I1")
    assert i1["mauvais_moyens"] == ["T1"] and i1["moyen"] == "P3"
    # T1 reste sur la rixe le temps du compte rendu, puis repart de la
    assert arrivee + patrouilles.COMPTE_RENDU_MS < 9000
    r = patrouilles.rejouer(actions + [{"type": "engager", "moyen": "T1", "incident": "I2", "t_ms": 9000}], 1.0)
    i2 = next(d for d in r["detail_incidents"] if d["id"] == "I2")
    assert i2["arrivee_ms"] == 9000 + (len(patrouilles.chemin((1, 9), (0, 4))) - 1) * 430
    # mauvais moyen trop lent : l'incident expire pendant son trajet
    tard = _ech("I1") - 1000
    r = patrouilles.rejouer([{"type": "engager", "moyen": "T1", "incident": "I1", "t_ms": tard},
                             {"type": "engager", "moyen": "P3", "incident": "I1", "t_ms": tard + 500}], 1.0)
    assert r["traites"] == 0 and "incident_en_attente" in r["drapeaux"]


def test_moyen_occupe_ignore():
    # P3 part sur I1 a 3 s : 1 case, arrivee 3,43 s, libre a 23,43 s (20 s sur place)
    actions = [{"type": "engager", "moyen": "P3", "incident": "I1", "t_ms": 3000},
               {"type": "engager", "moyen": "P3", "incident": "I2", "t_ms": 10500}]
    r = patrouilles.rejouer(actions, 1.0)
    assert r["ignores"] == 1
    assert r["erreurs"] == 0  # occupe passe avant le controle du type
    assert "moyen_occupe" in r["drapeaux"]
    assert r["traites"] == 1


def test_incident_deja_attribue_ou_echu_ignore():
    actions = [{"type": "engager", "moyen": "P3", "incident": "I1", "t_ms": 2000},
               {"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": 2500},
               {"type": "engager", "moyen": "S1", "incident": "I3", "t_ms": 45000},
               {"type": "engager", "moyen": "M1", "incident": "I5", "t_ms": 23000}]
    r = patrouilles.rejouer(actions, 1.0)
    assert r["ignores"] == 3  # I1 deja attribue, I3 echu (45 s), I5 pas encore ouvert
    assert r["traites"] == 1
    assert r["manques"] == len(_incidents()) - 1
    assert "horodatage_incoherent" in r["drapeaux"]


def test_patrouilles_coef_allonge_echeances():
    # I1 : P1 -> I1 = 8 cases (3,44 s)
    t = _ech("I1") + 2000 - 8 * patrouilles.PAS_MS  # arrivee 2 s apres l'echeance a coef 1
    actions = [{"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": t}]
    r = patrouilles.rejouer(actions, 1.0)
    assert r["traites"] == 0 and r["manques"] == len(_incidents())
    i1 = next(d for d in r["detail_incidents"] if d["id"] == "I1")
    assert i1["statut"] == "manque" and i1["arrivee_ms"] == _ech("I1") + 2000
    r = patrouilles.rejouer(actions, 1.33)
    assert r["traites"] == 1


def test_rejouer_entrainement():
    inc = banque.charger()["entrainement"]["patrouilles"]["INCIDENTS"]
    actions = [{"type": "engager", "moyen": "P1", "incident": "E1", "t_ms": 3000}]
    r = patrouilles.rejouer(actions, 1.0, incidents=inc)
    assert r["traites"] == 1 and r["manques"] == 0
    assert [d["id"] for d in r["detail_incidents"]] == ["E1"]
    assert r["detail_incidents"][0]["arrivee_ms"] == 3000 + 2 * patrouilles.PAS_MS
    # les incidents de l'epreuve ne sont pas connus de l'entrainement
    r = patrouilles.rejouer([{"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": 3000}], 1.0, incidents=inc)
    assert r["traites"] == 0 and "action_inconnue" in r["drapeaux"]


def test_chemin_impossible():
    inc = [{"id": "X1", "t": 0, "pos": [3, 3], "type": "Sécurité", "txt": "", "zone": "", "dl": 30}]
    r = patrouilles.rejouer([{"type": "engager", "moyen": "P1", "incident": "X1", "t_ms": 1000}], 1.0, incidents=inc)
    assert r["ignores"] == 1
    assert r["drapeaux"] == ["chemin_impossible"]


def test_rejouer_journal_hostile():
    actions = ["x", None, 3, {"type": "engager", "moyen": ["P1"], "incident": "I1", "t_ms": 3000},
               {"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": "abc"},
               {"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": float("nan")},
               {"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": 10 ** 400}]
    r = patrouilles.rejouer(actions, 1.0)
    assert r["traites"] == 0
    assert "action_inconnue" in r["drapeaux"] and "horodatage_incoherent" in r["drapeaux"]
    assert patrouilles.rejouer({"x": 1}, 1.0)["traites"] == 0


def test_rejouer_coef_invalide():
    actions = [{"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": 3000}]
    for coef in (0, -2, float("nan"), float("inf"), None):
        assert patrouilles.rejouer(actions, coef)["traites"] == 1


def test_reemission_meme_aid_comptee_une_fois():
    # Reemission apres timeout : le mauvais type n'est compte qu'une fois
    faux = {"type": "engager", "moyen": "T1", "incident": "I1", "t_ms": 3000, "aid": "k3x-1"}
    r = patrouilles.rejouer([faux, dict(faux, recu_ms=18000)], 1.0)
    assert r["erreurs"] == 1
    assert "doublon" in r["drapeaux"]
    # aid differents : rien n'est fusionne, mais le second envoi tombe pendant le trajet du
    # premier mauvais moyen (incident en attente de son compte rendu) : ignore, une erreur
    r = patrouilles.rejouer([faux, dict(faux, aid="k3x-2")], 1.0)
    assert r["erreurs"] == 1 and r["ignores"] == 1 and "incident_en_attente" in r["drapeaux"]
    # sans aid (journal ancien) : meme regle
    sans = {k: v for k, v in faux.items() if k != "aid"}
    assert patrouilles.rejouer([sans, sans], 1.0)["erreurs"] == 1
