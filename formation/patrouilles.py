"""Plan, plus court chemin et rejeu deterministe des patrouilles.

Modele (le client static/js/formation/patrouilles.js le reproduit a l'identique,
au temps ecoule et non par accumulation de setInterval) :

- Temps de jeu : `t = t_ms / coef`, `t_ms` etant le temps reel depuis le debut
  de l'epreuve. Toutes les durees ci-dessous sont en temps de jeu.
- Plan RW x CL (8 x 12). Cases bloquees : la piste (colonne 6) hors passerelles
  (lignes PASS = 1 et 6) et les travaux TRAV. Deplacement en 4 voisins, plus
  court chemin par BFS, voisins explores dans l'ordre (1,0), (-1,0), (0,1),
  (0,-1) comme la maquette (le chemin retenu est donc unique).
- Incident i : ouvert a `T_i = t * 1000`, echeance `D_i = T_i + dl * 1000`.
- Moyen u : position initiale de UNITS, libre a 0.
- Engagements `{type: "engager", moyen, incident, t_ms}` pris dans l'ordre du temps :
  * incident inconnu, pas encore ouvert (t < T_i), echu (t >= D_i) ou deja
    attribue -> ignore, drapeau ;
  * moyen inconnu -> ignore, drapeau ;
  * moyen encore occupe (t < libre_a) -> ignore, drapeau `moyen_occupe` ;
  * type du moyen different du type de l'incident -> une erreur, rien d'autre
    (l'incident reste ouvert, le moyen ne bouge pas) ;
  * sinon l'incident est attribue : `arrivee = t + (cases du chemin) * PAS_MS`,
    traite si `arrivee <= D_i`, le moyen est sur place (`pos = incident`) et
    libre a `arrivee + SUR_PLACE_MS`.
- Incident jamais traite (pas attribue, ou attribue mais arrive trop tard) : manque.
"""
from collections import deque
from functools import lru_cache

from formation import banque

PAS_MS = 430
SUR_PLACE_MS = 4000

_VOISINS = [(1, 0), (-1, 0), (0, 1), (0, -1)]


@lru_cache(maxsize=1)
def _plan():
    p = banque._banque()["patrouilles"]
    return p["RW"], p["CL"], set(p["PASS"]), {tuple(c) for c in p["TRAV"]}


def bloquee(case):
    rw, cl, passes, trav = _plan()  # piste en colonne 6, comme la maquette
    r, c = case
    if r < 0 or c < 0 or r >= rw or c >= cl:
        return True
    return (c == 6 and r not in passes) or (r, c) in trav


def chemin(a, b):
    """Plus court chemin de `a` a `b` (cases incluses), ou None."""
    a, b = tuple(a), tuple(b)
    prev = {a: None}
    file = deque([a])
    while file:
        c = file.popleft()
        if c == b:
            out = []
            while c is not None:
                out.append(c)
                c = prev[c]
            return out[::-1]
        for dr, dc in _VOISINS:
            n = (c[0] + dr, c[1] + dc)
            if n in prev or bloquee(n):
                continue
            prev[n] = c
            file.append(n)
    return None


def rejouer(actions, coef, incidents=None):
    """Rejoue le journal des engagements. `incidents` remplace ceux de la banque
    (entrainement). Retourne {traites, manques, erreurs, ignores,
    detail_incidents, drapeaux}."""
    p = banque._banque()["patrouilles"]
    incs = {i["id"]: i for i in (incidents if incidents is not None else p["INCIDENTS"])}
    units = {u["id"]: {"type": u["type"], "pos": tuple(u["pos"]), "libre_a": 0.0} for u in p["UNITS"]}
    etat = {iid: {"id": iid, "statut": "manque", "moyen": None, "arrivee_ms": None} for iid in incs}
    erreurs = ignores = 0
    drapeaux = []

    def drapeau(nom):
        if nom not in drapeaux:
            drapeaux.append(nom)

    engagements = [a for a in actions if a.get("type") == "engager"]
    engagements.sort(key=lambda a: float(a.get("t_ms") or 0))
    for a in engagements:
        t = float(a.get("t_ms") or 0) / coef
        inc = incs.get(a.get("incident"))
        u = units.get(a.get("moyen"))
        if inc is None or u is None:
            ignores += 1
            drapeau("action_inconnue")
            continue
        t_ouv = inc["t"] * 1000
        ech = t_ouv + inc["dl"] * 1000
        e = etat[inc["id"]]
        if t < t_ouv:
            ignores += 1
            drapeau("horodatage_incoherent")
            continue
        if t >= ech:
            ignores += 1
            drapeau("incident_echu")
            continue
        if e["moyen"] is not None:
            ignores += 1
            drapeau("incident_deja_attribue")
            continue
        if t < u["libre_a"]:
            ignores += 1
            drapeau("moyen_occupe")
            continue
        if u["type"] != inc["type"]:
            erreurs += 1
            continue
        trajet = chemin(u["pos"], inc["pos"])
        if trajet is None:
            ignores += 1
            drapeau("action_inconnue")
            continue
        arrivee = t + (len(trajet) - 1) * PAS_MS
        e["moyen"] = a["moyen"]
        e["arrivee_ms"] = round(arrivee)
        if arrivee <= ech:
            e["statut"] = "traite"
        u["pos"] = tuple(inc["pos"])
        u["libre_a"] = arrivee + SUR_PLACE_MS

    detail = [etat[iid] for iid in incs]
    traites = sum(1 for d in detail if d["statut"] == "traite")
    return {
        "traites": traites,
        "manques": len(detail) - traites,
        "erreurs": erreurs,
        "ignores": ignores,
        "detail_incidents": detail,
        "drapeaux": drapeaux,
    }
