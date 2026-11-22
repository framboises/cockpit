"""Correction serveur des 7 epreuves (fonctions pures, sans base ni Flask).

`score(epreuve, actions, coef)` -> `{score: 0-100, detail, drapeaux}`.
Chaque action porte `type` (`reponse` pour les epreuves a question), `t_ms`
(temps reel depuis le debut de l'epreuve) et ses champs. Le temps de jeu vaut
`t_ms / coef` : tous les delais ci-dessous (en ms de jeu) sont donc allonges
par le coefficient d'amenagement.

Formules de la maquette test_pcorg.html, bornees a 0-100 et arrondies comme
Math.round :
- radio : `{type: "prise", msg}`. Affichage du message k (base 0) :
  `RADIO_PREMIER + somme des cadences des messages 0..k-1`, la cadence apres un
  message valant RADIO_CADENCE_PRIO s'il est PRIORITE, RADIO_CADENCE sinon.
  Une prise ne vaut que pendant l'affichage du message
  (`affichage_k <= t < affichage_k+1`, le dernier jusqu'a son affichage + sa
  cadence) ; sinon ignoree, drapeau `horodatage_incoherent`. Une prise par
  message. `(pris_a_raison - pris_a_tort) / nb_pour_moi * 100 + 5 * priorites_rapides`,
  priorite rapide si `t - affichage <= RADIO_SEUIL_PRIO`.
- appel : `{question: etape, choix}` ; points / (2 * nb_etapes) * 100.
- mc : `{question: fiche, cat, zone, heure, moyen}` ; 1 point par champ egal
  (apres strip) / (4 * nb_fiches) * 100.
- patrouilles : rejeu `patrouilles.rejouer` ; (traites - 0.5 * erreurs) / nb * 100.
- acces : `{type: "decision", badge, decision: 1|0}` ; `badge_affiche` est
  purement informatif (le client pourrait le renvoyer pour remettre a zero ses
  5 s). Les badges passent dans l'ordre fixe de la banque : la reference du
  badge k est l'heure de la decision du badge k-1 (0 pour le premier) ; si
  elle manque, l'expiration automatique du client `ref_k-1 + ACCES_DELAI * coef
  + 450`. Une decision avec
  `t - ref > ACCES_DELAI + ACCES_MARGE` (ms de jeu) compte fausse ; avant sa
  reference : fausse, drapeau `horodatage_incoherent`. bonnes / nb_badges * 100.
- logique : bonnes / nb * 100 ; reponses au-dela de LOGIQUE_DUREE ignorees.
- situations : points / (2 * nb) * 100.
Question sans reponse = 0 point ; reponse en double : la premiere fait foi
(ordre du temps), drapeau `doublon` pour une prise radio ou une decision repetee.

Le journal vient du navigateur : `score` ne leve jamais d'exception. Action qui
n'est pas un objet ou id qui n'est pas un texte connu : ignoree, drapeau
`action_inconnue` ; `t_ms` absent, non numerique ou non fini : ignoree, drapeau
`horodatage_incoherent`.
"""
import math

from formation import banque, patrouilles

RADIO_PREMIER = 800
RADIO_CADENCE = 4000
RADIO_CADENCE_PRIO = 3200
RADIO_SEUIL_PRIO = 2000
ACCES_DELAI = 5000
ACCES_MARGE = 1000  # transition de 450 ms + reseau, en ms de jeu
LOGIQUE_DUREE = 240000
PLAFOND_QUESTIONS = 600000

EPREUVES = ("radio", "appel", "mc", "patrouilles", "acces", "logique", "situations")


def _arrondi(pct):
    """Math.round borne a 0-100."""
    return max(0, min(100, int(math.floor(pct + 0.5))))


def _ajoute(drapeaux, nom):
    if nom not in drapeaux:
        drapeaux.append(nom)


def temps_ms(action):
    """`t_ms` de l'action en float fini, ou None (absent, texte, NaN, infini, booleen)."""
    v = action.get("t_ms")
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        v = float(v)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def journal(actions, types, drapeaux):
    """Journal du navigateur assaini : liste de (t_ms, action) des `types` voulus,
    triee par le temps (tri stable). Une action qui n'est pas un objet leve
    `action_inconnue`, un `t_ms` invalide `horodatage_incoherent` ; elles sont ignorees.
    Ne leve jamais d'exception."""
    if not isinstance(actions, (list, tuple)):
        if actions:
            _ajoute(drapeaux, "action_inconnue")
        return []
    out = []
    for a in actions:
        if not isinstance(a, dict):
            _ajoute(drapeaux, "action_inconnue")
            continue
        if a.get("type") not in types:
            continue
        t = temps_ms(a)
        if t is None:
            _ajoute(drapeaux, "horodatage_incoherent")
            continue
        out.append((t, a))
    out.sort(key=lambda x: x[0])
    return out


def _id(action, champ):
    """Identifiant texte du champ, sinon None (liste, nombre, objet...)."""
    v = action.get(champ)
    return v if isinstance(v, str) else None


def _premieres_reponses(actions, coef, drapeaux, limite_ms=None):
    """Premiere reponse par question (ordre du temps), dans la limite de temps de jeu."""
    vues = {}
    hors_delai = 0
    for t, a in journal(actions, ("reponse",), drapeaux):
        if limite_ms is not None and t / coef > limite_ms:
            hors_delai += 1
            continue
        q = _id(a, "question")
        if q is None:
            _ajoute(drapeaux, "action_inconnue")
            continue
        if q not in vues:
            vues[q] = a
    return vues, hors_delai


def radio_affichages_ms():
    """{msg_id: (debut, fin)} de l'affichage de chaque message, en ms de jeu."""
    out = {}
    t = RADIO_PREMIER
    for m in banque._banque()["radio"]:
        cadence = RADIO_CADENCE_PRIO if m["priorite"] else RADIO_CADENCE
        out[m["id"]] = (t, t + cadence)
        t += cadence
    return out


def _radio(actions, coef, drapeaux):
    msgs = {m["id"]: m for m in banque._banque()["radio"]}
    aff = radio_affichages_ms()
    pris = set()
    hits = fa = fast = 0
    for t_ms, a in journal(actions, ("prise",), drapeaux):
        mid = _id(a, "msg")
        if mid not in msgs:
            _ajoute(drapeaux, "action_inconnue")
            continue
        if mid in pris:
            _ajoute(drapeaux, "doublon")
            continue
        t = t_ms / coef
        debut, fin = aff[mid]
        if not (debut <= t < fin):
            _ajoute(drapeaux, "horodatage_incoherent")
            continue
        pris.add(mid)
        m = msgs[mid]
        if m["pour_moi"]:
            hits += 1
            if m["priorite"] and t - debut <= RADIO_SEUIL_PRIO:
                fast += 1
        else:
            fa += 1
    mine = sum(1 for m in msgs.values() if m["pour_moi"])
    pct = (hits - fa) / mine * 100 + fast * 5
    detail = {"pris_a_raison": hits, "pris_a_tort": fa, "manques": mine - hits,
              "priorites_rapides": fast, "nb_pour_moi": mine}
    return pct, detail


def _points_choix(questions, actions, coef, drapeaux):
    """Somme des points des premieres reponses ; questions = {id: [choix]}."""
    vues, _ = _premieres_reponses(actions, coef, drapeaux)
    pts, repondues = 0, 0
    for qid, a in vues.items():
        if qid not in questions:
            _ajoute(drapeaux, "action_inconnue")
            continue
        choix = {c["id"]: c for c in questions[qid]}
        c = choix.get(_id(a, "choix"))
        if c is None:
            _ajoute(drapeaux, "action_inconnue")
            continue
        repondues += 1
        pts += c["points"]
    return pts, repondues


def _appel(actions, coef, drapeaux):
    etapes = {e["id"]: e["choix"] for ap in banque._banque()["appel"] for e in ap["etapes"]}
    pts, rep = _points_choix(etapes, actions, coef, drapeaux)
    return pts / (2 * len(etapes)) * 100, {"points": pts, "max": 2 * len(etapes), "repondues": rep}


def _situations(actions, coef, drapeaux):
    sits = {s["id"]: s["choix"] for s in banque._banque()["situations"]}
    pts, rep = _points_choix(sits, actions, coef, drapeaux)
    return pts / (2 * len(sits)) * 100, {"points": pts, "max": 2 * len(sits), "repondues": rep}


def _mc(actions, coef, drapeaux):
    fiches = {f["id"]: f for f in banque._banque()["mc"]["fiches"]}
    vues, _ = _premieres_reponses(actions, coef, drapeaux)
    pts, par_fiche = 0, {}
    for qid, a in vues.items():
        f = fiches.get(qid)
        if f is None:
            _ajoute(drapeaux, "action_inconnue")
            continue
        ok = 0
        for champ, attendu in f["ans"].items():
            v = a.get(champ)
            if isinstance(v, str) and v.strip() == attendu:
                ok += 1
        par_fiche[qid] = ok
        pts += ok
    return pts / (4 * len(fiches)) * 100, {"champs_justes": pts, "max": 4 * len(fiches),
                                            "par_fiche": par_fiche}


def _patrouilles(actions, coef, drapeaux):
    r = patrouilles.rejouer(actions, coef)
    nb = len(r["detail_incidents"])
    detail = {k: r[k] for k in ("traites", "manques", "erreurs", "ignores", "detail_incidents")}
    for d in r["drapeaux"]:
        _ajoute(drapeaux, d)
    return (r["traites"] - 0.5 * r["erreurs"]) / nb * 100, detail


def _acces(actions, coef, drapeaux):
    """Badges dans l'ordre fixe de la banque ; `badge_affiche` purement informatif.
    Reference du badge k : decision du badge k-1 (0 pour le premier) ; si elle
    manque, expiration automatique du client `ref_k-1 + ACCES_DELAI * coef + 450`.
    Retarder la decision k-1 pour gagner du temps sur k coute le badge k-1 :
    aucun gain."""
    badges = banque._banque()["acces"]["badges"]
    index = {b["id"]: k for k, b in enumerate(badges)}
    decisions = {}
    for t, a in journal(actions, ("decision",), drapeaux):
        bid = _id(a, "badge")
        if bid not in index:
            _ajoute(drapeaux, "action_inconnue")
            continue
        if bid in decisions:
            _ajoute(drapeaux, "doublon")
            continue
        decisions[bid] = (t, a.get("decision"))
    bonnes = tardives = 0
    ref = 0.0  # ms reelles
    for b in badges:
        auto = ref + ACCES_DELAI * coef + 450
        d = decisions.get(b["id"])
        if d is not None:
            t, v = d
            if t < ref:
                _ajoute(drapeaux, "horodatage_incoherent")
            elif (t - ref) / coef > ACCES_DELAI + ACCES_MARGE:
                tardives += 1
            elif not isinstance(v, bool) and v in (0, 1) and v == b["ok"]:
                bonnes += 1
            ref = t if t >= ref else auto
        else:
            ref = auto
    nb = len(badges)
    return bonnes / nb * 100, {"bonnes": bonnes, "nb": nb, "decisions": len(decisions),
                               "tardives": tardives}


def _logique(actions, coef, drapeaux):
    qs = {q["id"]: q for q in banque._banque()["logique"]}
    vues, hors_delai = _premieres_reponses(actions, coef, drapeaux, LOGIQUE_DUREE)
    bonnes = 0
    for qid, a in vues.items():
        q = qs.get(qid)
        if q is None:
            _ajoute(drapeaux, "action_inconnue")
            continue
        if _id(a, "choix") == q["a"]:
            bonnes += 1
    return bonnes / len(qs) * 100, {"bonnes": bonnes, "nb": len(qs),
                                    "ignorees_hors_delai": hors_delai}


_CORRECTEURS = {
    "radio": _radio, "appel": _appel, "mc": _mc, "patrouilles": _patrouilles,
    "acces": _acces, "logique": _logique, "situations": _situations,
}


def score(epreuve, actions, coef):
    """Score serveur d'une epreuve : {score: int 0-100, detail: dict, drapeaux: list}."""
    if epreuve not in _CORRECTEURS:
        raise ValueError("epreuve inconnue : %s" % epreuve)
    drapeaux = []
    pct, detail = _CORRECTEURS[epreuve](actions, float(coef), drapeaux)
    return {"score": _arrondi(pct), "detail": detail, "drapeaux": drapeaux}


def duree_min_ms(epreuve, coef):
    """Duree minimale reelle d'une epreuve temps reel (None pour les autres)."""
    if epreuve == "radio":
        fin = max(f for _, f in radio_affichages_ms().values())
        return round(fin * coef)
    if epreuve == "patrouilles":
        return round(banque._banque()["patrouilles"]["INCIDENTS"][-1]["t"] * 1000 * coef)
    return None


def duree_max_ms(epreuve, coef):
    """Duree maximale reelle au-dela de laquelle l'epreuve est close."""
    if epreuve == "radio":
        return duree_min_ms("radio", coef) + 5000
    if epreuve == "patrouilles":
        return round(110000 * coef) + 5000
    if epreuve == "acces":
        return round(len(banque._banque()["acces"]["badges"]) * (ACCES_DELAI * coef + 1000)) + 5000
    if epreuve == "logique":
        return round(LOGIQUE_DUREE * coef) + 5000
    if epreuve in ("appel", "mc", "situations"):
        return round(PLAFOND_QUESTIONS * coef)
    raise ValueError("epreuve inconnue : %s" % epreuve)
