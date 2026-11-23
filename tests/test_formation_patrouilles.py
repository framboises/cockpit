"""Plan, plus court chemin et rejeu deterministe des patrouilles (formation/patrouilles.py)."""
from formation import banque, patrouilles


def _incidents():
    return banque.charger()["patrouilles"]["INCIDENTS"]


def _units():
    return banque.charger()["patrouilles"]["UNITS"]


def _indispo(uid, t):
    u = next(x for x in _units() if x["id"] == uid)
    return any(w["de"] * 1000 <= t < w["a"] * 1000 for w in u.get("indispo") or [])


def journal_glouton():
    """A l'ouverture de chaque incident, engager le premier moyen du bon type
    (ordre de UNITS) des qu'il est libre, selon le modele du rejeu ; une Patrouille
    sur un Secours quand aucun Secours n'est disponible."""
    pos = {u["id"]: tuple(u["pos"]) for u in _units()}
    libre = {u["id"]: 0 for u in _units()}
    actions = []
    for inc in _incidents():
        t_ouv, ech = inc["t"] * 1000, (inc["t"] + inc["dl"]) * 1000
        cands = [u["id"] for u in _units() if u["type"] == inc["type"] and not _indispo(u["id"], t_ouv)]
        if not cands and inc["type"] == "Secours":
            cands = [u["id"] for u in _units() if u["type"] == "Sécurité"]
        uid = min(cands, key=lambda u: (max(libre[u], t_ouv), cands.index(u)))
        t = max(libre[uid], t_ouv)
        if t >= ech:
            continue
        p = patrouilles.chemin(pos[uid], tuple(inc["pos"]))
        arrivee = t + (len(p) - 1) * patrouilles.PAS_MS
        pos[uid] = tuple(inc["pos"])
        libre[uid] = arrivee + patrouilles.SUR_PLACE_MS
        actions.append({"type": "engager", "moyen": uid, "incident": inc["id"], "t_ms": t})
    return actions


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


def test_rejeu_glouton():
    actions = journal_glouton()
    # Calcul a la main sur la maquette : chaque moyen du bon type est libre a
    # l'ouverture de chaque incident ; trajets les plus longs I4 (M1, 9 cases,
    # 3,87 s) et I6 (S1, 10 cases, 4,3 s), tous bien avant l'echeance.
    # I6 (enfant blesse) : Secours 1 est en intervention hors plan de 44 a 100 s,
    # la patrouille part en premier intervenant.
    assert [(a["incident"], a["moyen"], a["t_ms"]) for a in actions] == [
        ("I1", "P1", 2000), ("I2", "T1", 10000), ("I3", "S1", 20000), ("I4", "M1", 30000),
        ("I5", "P1", 40000), ("I6", "P1", 50000), ("I7", "T1", 60000), ("I8", "P1", 68000)]
    r = patrouilles.rejouer(actions, 1.0)
    assert r["traites"] == 8
    assert r["manques"] == 0
    assert r["erreurs"] == 0
    assert r["ignores"] == 0
    d = {x["id"]: x for x in r["detail_incidents"]}
    assert d["I4"]["arrivee_ms"] == 30000 + 9 * 430
    assert d["I6"]["relais"] is True and d["I3"]["relais"] is False
    # P1 n'est pas le plus proche de la rixe I1 (P2 : 5 cases contre 8)
    assert d["I1"]["plus_proche"] is False
    assert r["points"] == r["plus_proches"] + 0.5 * (8 - r["plus_proches"])


def test_plus_proche_note():
    p2 = [{"type": "engager", "moyen": "P2", "incident": "I1", "t_ms": 3000}]
    p1 = [{"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": 3000}]
    r2, r1 = patrouilles.rejouer(p2, 1.0), patrouilles.rejouer(p1, 1.0)
    assert r2["traites"] == r1["traites"] == 1
    assert r2["plus_proches"] == 1 and r2["points"] == 1
    assert r1["plus_proches"] == 0 and r1["points"] == patrouilles.POINTS_PAS_LE_PLUS_PROCHE
    # un moyen occupe ne compte pas : P2 parti sur I1, P1 est le plus proche disponible de I5
    a = p2 + [{"type": "engager", "moyen": "P1", "incident": "I5", "t_ms": 41000}]
    i5 = next(x for x in patrouilles.rejouer(a, 1.0)["detail_incidents"] if x["id"] == "I5")
    assert i5["plus_proche"] is False  # P2 est revenu disponible a (1, 9), plus pres que P1 a (1, 1)


def test_relais_patrouille_sur_secours():
    i3 = {"type": "engager", "moyen": "P2", "incident": "I3", "t_ms": 21000}
    # Secours 1 disponible : la patrouille sur un secours reste une erreur
    r = patrouilles.rejouer([i3], 1.0)
    assert r["erreurs"] == 1 and r["traites"] == 0
    # Secours 1 indisponible (44-100 s) : la patrouille est admise sur I6
    r = patrouilles.rejouer([{"type": "engager", "moyen": "P1", "incident": "I6", "t_ms": 51000}], 1.0)
    assert r["erreurs"] == 0 and r["traites"] == 1
    i6 = next(x for x in r["detail_incidents"] if x["id"] == "I6")
    assert i6["relais"] is True and i6["plus_proche"] is True
    # et Secours 1 ne peut pas etre engage pendant sa fenetre
    r = patrouilles.rejouer([{"type": "engager", "moyen": "S1", "incident": "I6", "t_ms": 51000}], 1.0)
    assert r["ignores"] == 1 and "moyen_occupe" in r["drapeaux"]
    # Tango sur le secours, meme sans Secours libre : erreur
    r = patrouilles.rejouer([{"type": "engager", "moyen": "T1", "incident": "I6", "t_ms": 51000}], 1.0)
    assert r["erreurs"] == 1


def test_mauvais_type_compte_erreur():
    # Tango 1 sur la rixe I1 : erreur, rien d'autre ; P1 reste disponible
    actions = [{"type": "engager", "moyen": "T1", "incident": "I1", "t_ms": 3000},
               {"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": 4000}]
    r = patrouilles.rejouer(actions, 1.0)
    assert r["erreurs"] == 1
    assert r["traites"] == 1
    # T1 n'a pas bouge : il traite encore I2 depuis sa position initiale (2 cases)
    r = patrouilles.rejouer(actions + [{"type": "engager", "moyen": "T1", "incident": "I2", "t_ms": 10000}], 1.0)
    i2 = next(d for d in r["detail_incidents"] if d["id"] == "I2")
    assert i2["arrivee_ms"] == 10000 + 2 * 430


def test_moyen_occupe_ignore():
    # P1 part sur I1 a 8 s : 8 cases, arrivee 11,44 s, libre a 15,44 s
    actions = [{"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": 8000},
               {"type": "engager", "moyen": "P1", "incident": "I2", "t_ms": 10500}]
    r = patrouilles.rejouer(actions, 1.0)
    assert r["ignores"] == 1
    assert r["erreurs"] == 0  # occupe passe avant le controle du type
    assert "moyen_occupe" in r["drapeaux"]
    assert r["traites"] == 1


def test_incident_deja_attribue_ou_echu_ignore():
    actions = [{"type": "engager", "moyen": "P2", "incident": "I1", "t_ms": 2000},
               {"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": 2500},
               {"type": "engager", "moyen": "S1", "incident": "I3", "t_ms": 45000},
               {"type": "engager", "moyen": "M1", "incident": "I4", "t_ms": 29000}]
    r = patrouilles.rejouer(actions, 1.0)
    assert r["ignores"] == 3  # I1 deja attribue, I3 echu (45 s), I4 pas encore ouvert
    assert r["traites"] == 1
    assert r["manques"] == 7
    assert "horodatage_incoherent" in r["drapeaux"]


def test_patrouilles_coef_allonge_echeances():
    # I1 : ouvert a 2 s, echeance 32 s ; P1 -> I1 = 8 cases (3,44 s)
    t = 32000 + 2000 - 8 * patrouilles.PAS_MS  # arrivee 2 s apres l'echeance a coef 1
    actions = [{"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": t}]
    r = patrouilles.rejouer(actions, 1.0)
    assert r["traites"] == 0 and r["manques"] == 8
    i1 = next(d for d in r["detail_incidents"] if d["id"] == "I1")
    assert i1["statut"] == "manque" and i1["arrivee_ms"] == 34000
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
    # aid differents (engager puis refus du meme instant, ou deux essais) : rien n'est fusionne
    r = patrouilles.rejouer([faux, dict(faux, aid="k3x-2")], 1.0)
    assert r["erreurs"] == 2
    # sans aid (journal ancien) : comportement inchange
    sans = {k: v for k, v in faux.items() if k != "aid"}
    assert patrouilles.rejouer([sans, sans], 1.0)["erreurs"] == 2
