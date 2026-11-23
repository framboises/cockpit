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
- Engagements `{type: "engager", moyen, incident, t_ms}` pris dans l'ordre du temps,
  journal assaini par `correction.journal` (jamais d'exception : action qui
  n'est pas un objet, id non texte, `t_ms` invalide ou non fini -> ignore, drapeau) :
  * incident inconnu, pas encore ouvert (t < T_i), echu (t >= D_i) ou deja
    attribue -> ignore, drapeau ;
  * mauvais moyen deja en route sur l'incident (t < bloque_jusqua) -> ignore,
    drapeau `incident_en_attente` (on attend son compte rendu) ;
  * moyen inconnu -> ignore, drapeau ;
  * aucun chemin -> ignore, drapeau `chemin_impossible` ;
  * moyen encore occupe (t < libre_a) ou indisponible (fenetre `indispo` du
    scenario, en secondes, `de <= t/1000 < a`) -> ignore, drapeau `moyen_occupe` ;
  * type du moyen different du type de l'incident -> une erreur, et le moyen y
    va quand meme (23/11/2026) : il arrive a `t + cases * PAS_MS`, rend compte
    qu'il ne peut rien faire et se libere COMPTE_RENDU_MS plus tard, sur place.
    D'ici son arrivee l'incident reste bloque (`bloque_jusqua`) : le temps est
    perdu ; ensuite il est de nouveau ouvert jusqu'a son echeance. Sans chemin :
    l'erreur seule, le moyen ne bouge pas. Exception : incident
    `Secours` sans aucun moyen Secours libre a t -> une Patrouille (Securite)
    est acceptee en premier intervenant (`relais`) ;
  * sinon l'incident est attribue : `arrivee = t + (cases du chemin) * PAS_MS`,
    traite si `arrivee <= D_i`, le moyen est sur place (`pos = incident`) et
    libre a `arrivee + sp_i` (`sp` de l'incident en secondes, SUR_PLACE_MS par
    defaut : une rixe retient une patrouille plus longtemps qu'une barriere). `plus_proche` : aucun moyen admissible
    (meme regle de type, libre a t) n'avait un chemin plus court.
- Incident jamais traite (pas attribue, ou attribue mais arrive trop tard) : manque.
- Points : 1 par incident traite par le moyen le plus proche, POINTS_PAS_LE_PLUS_PROCHE
  sinon (correction.py : (points - 0.5 * erreurs) / nb * 100).
"""
from collections import deque
from functools import lru_cache

from formation import banque

PAS_MS = 430
SUR_PLACE_MS = 4000
COMPTE_RENDU_MS = 2000  # mauvais moyen arrive : rend compte qu'il ne peut rien faire, puis libre
POINTS_PAS_LE_PLUS_PROCHE = 0.5
TYPE_RELAIS = {"Secours": "Sécurité"}  # incident sans moyen libre de son type -> type admis en relais

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


def sur_place_ms(inc):
    """Temps sur place de l'incident (`sp`, secondes de jeu), SUR_PLACE_MS par defaut."""
    sp = inc.get("sp")
    return sp * 1000 if isinstance(sp, (int, float)) and not isinstance(sp, bool) and sp >= 0 else SUR_PLACE_MS


def libre(u, t):
    """Moyen libre au temps de jeu `t` (ms) : fin de sa mission et hors fenetre `indispo`."""
    if t < u["libre_a"]:
        return False
    return not any(de <= t < a for de, a in u["indispo"])


def type_admis(units, type_incident, t):
    """Type de moyen attendu sur l'incident a `t` : le sien, ou le type de relais
    (Patrouille sur un Secours) si aucun moyen de son type n'est libre."""
    relais = TYPE_RELAIS.get(type_incident)
    if relais and not any(v["type"] == type_incident and libre(v, t) for v in units.values()):
        return relais
    return type_incident


def rejouer(actions, coef, incidents=None):
    """Rejoue le journal des engagements. `incidents` remplace ceux de la banque
    (entrainement). Retourne {traites, manques, erreurs, ignores,
    detail_incidents, drapeaux}."""
    p = banque._banque()["patrouilles"]
    incs = {i["id"]: i for i in (incidents if incidents is not None else p["INCIDENTS"])}
    units = {u["id"]: {"type": u["type"], "pos": tuple(u["pos"]), "libre_a": 0.0,
                       "indispo": [(w["de"] * 1000, w["a"] * 1000) for w in u.get("indispo") or []]}
             for u in p["UNITS"]}
    etat = {iid: {"id": iid, "statut": "manque", "moyen": None, "arrivee_ms": None,
                  "plus_proche": None, "relais": False, "mauvais_moyens": []} for iid in incs}
    bloque = {iid: 0.0 for iid in incs}  # arrivee du mauvais moyen en route (ms de jeu)
    erreurs = ignores = 0
    drapeaux = []

    def drapeau(nom):
        if nom not in drapeaux:
            drapeaux.append(nom)

    from formation.correction import coef_valide, journal  # import tardif : correction importe ce module

    coef = coef_valide(coef)

    for t_ms, a in journal(actions, ("engager",), drapeaux):
        t = t_ms / coef
        iid, uid = a.get("incident"), a.get("moyen")
        inc = incs.get(iid) if isinstance(iid, str) else None
        u = units.get(uid) if isinstance(uid, str) else None
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
        if t < bloque[inc["id"]]:
            ignores += 1
            drapeau("incident_en_attente")
            continue
        if not libre(u, t):
            ignores += 1
            drapeau("moyen_occupe")
            continue
        admis = type_admis(units, inc["type"], t)
        if u["type"] != admis:
            erreurs += 1
            trajet = chemin(u["pos"], inc["pos"])
            if trajet is not None:  # il y va quand meme : temps perdu pour l'incident et pour lui
                arrivee = t + (len(trajet) - 1) * PAS_MS
                bloque[inc["id"]] = arrivee
                e["mauvais_moyens"].append(uid)
                u["pos"] = tuple(inc["pos"])
                u["libre_a"] = arrivee + COMPTE_RENDU_MS
            continue
        trajet = chemin(u["pos"], inc["pos"])
        if trajet is None:
            ignores += 1
            drapeau("chemin_impossible")
            continue
        cases = len(trajet) - 1
        meilleur = min((len(c) - 1 for c in (chemin(v["pos"], inc["pos"]) for v in units.values()
                                             if v["type"] == admis and libre(v, t)) if c is not None),
                       default=cases)
        arrivee = t + cases * PAS_MS
        e["moyen"] = uid
        e["arrivee_ms"] = round(arrivee)
        e["plus_proche"] = cases <= meilleur
        e["relais"] = admis != inc["type"]
        if arrivee <= ech:
            e["statut"] = "traite"
        u["pos"] = tuple(inc["pos"])
        u["libre_a"] = arrivee + sur_place_ms(inc)

    detail = [etat[iid] for iid in incs]
    traites = sum(1 for d in detail if d["statut"] == "traite")
    plus_proches = sum(1 for d in detail if d["statut"] == "traite" and d["plus_proche"])
    return {
        "points": plus_proches + (traites - plus_proches) * POINTS_PAS_LE_PLUS_PROCHE,
        "plus_proches": plus_proches,
        "traites": traites,
        "manques": len(detail) - traites,
        "erreurs": erreurs,
        "ignores": ignores,
        "detail_incidents": detail,
        "drapeaux": drapeaux,
    }
