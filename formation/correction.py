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
- acces : `{type: "badge_affiche", badge}` puis `{type: "decision", badge,
  decision: 1|0}`. Une decision est jugee contre le dernier affichage du badge
  qui la precede (aucun : ignoree, drapeau `horodatage_incoherent`) ; au-dela de
  ACCES_DELAI elle compte fausse. bonnes / nb_badges * 100.
- logique : bonnes / nb * 100 ; reponses au-dela de LOGIQUE_DUREE ignorees.
- situations : points / (2 * nb) * 100.
Question sans reponse = 0 point ; reponse en double : la premiere fait foi
(ordre du temps).
"""
import math

from formation import banque, patrouilles

RADIO_PREMIER = 800
RADIO_CADENCE = 4000
RADIO_CADENCE_PRIO = 3200
RADIO_SEUIL_PRIO = 2000
ACCES_DELAI = 5000
LOGIQUE_DUREE = 240000
PLAFOND_QUESTIONS = 600000

EPREUVES = ("radio", "appel", "mc", "patrouilles", "acces", "logique", "situations")


def _arrondi(pct):
    """Math.round borne a 0-100."""
    return max(0, min(100, int(math.floor(pct + 0.5))))


def _t(action):
    try:
        return float(action.get("t_ms") or 0)
    except (TypeError, ValueError):
        return 0.0


def _tries(actions, type_):
    out = [a for a in actions if isinstance(a, dict) and a.get("type") == type_]
    out.sort(key=_t)
    return out


def _ajoute(drapeaux, nom):
    if nom not in drapeaux:
        drapeaux.append(nom)


def _premieres_reponses(actions, coef=None, limite_ms=None):
    """Premiere reponse par question (ordre du temps), avec la limite de temps de jeu."""
    vues = {}
    hors_delai = 0
    for a in _tries(actions, "reponse"):
        if limite_ms is not None and _t(a) / coef > limite_ms:
            hors_delai += 1
            continue
        q = a.get("question")
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


def _radio(actions, coef):
    msgs = {m["id"]: m for m in banque._banque()["radio"]}
    aff = radio_affichages_ms()
    drapeaux, pris = [], set()
    hits = fa = fast = 0
    for a in _tries(actions, "prise"):
        mid = a.get("msg")
        if mid not in msgs:
            _ajoute(drapeaux, "action_inconnue")
            continue
        if mid in pris:
            continue
        t = _t(a) / coef
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
    return pct, detail, drapeaux


def _points_choix(questions, actions, champ_choix="choix"):
    """Somme des points des premieres reponses ; questions = {id: [choix]}."""
    vues, _ = _premieres_reponses(actions)
    drapeaux, pts, repondues = [], 0, 0
    for qid, a in vues.items():
        if qid not in questions:
            _ajoute(drapeaux, "action_inconnue")
            continue
        choix = {c["id"]: c for c in questions[qid]}
        c = choix.get(a.get(champ_choix))
        if c is None:
            _ajoute(drapeaux, "action_inconnue")
            continue
        repondues += 1
        pts += c["points"]
    return pts, repondues, drapeaux


def _appel(actions, coef):
    etapes = {e["id"]: e["choix"] for ap in banque._banque()["appel"] for e in ap["etapes"]}
    pts, rep, drapeaux = _points_choix(etapes, actions)
    return pts / (2 * len(etapes)) * 100, {"points": pts, "max": 2 * len(etapes), "repondues": rep}, drapeaux


def _situations(actions, coef):
    sits = {s["id"]: s["choix"] for s in banque._banque()["situations"]}
    pts, rep, drapeaux = _points_choix(sits, actions)
    return pts / (2 * len(sits)) * 100, {"points": pts, "max": 2 * len(sits), "repondues": rep}, drapeaux


def _mc(actions, coef):
    fiches = {f["id"]: f for f in banque._banque()["mc"]["fiches"]}
    vues, _ = _premieres_reponses(actions)
    drapeaux, pts, par_fiche = [], 0, {}
    for qid, a in vues.items():
        f = fiches.get(qid)
        if f is None:
            _ajoute(drapeaux, "action_inconnue")
            continue
        ok = sum(1 for champ, attendu in f["ans"].items()
                 if str(a.get(champ) or "").strip() == attendu)
        par_fiche[qid] = ok
        pts += ok
    return pts / (4 * len(fiches)) * 100, {"champs_justes": pts, "max": 4 * len(fiches),
                                            "par_fiche": par_fiche}, drapeaux


def _patrouilles(actions, coef):
    r = patrouilles.rejouer(actions, coef)
    nb = len(r["detail_incidents"])
    detail = {k: r[k] for k in ("traites", "manques", "erreurs", "ignores", "detail_incidents")}
    return (r["traites"] - 0.5 * r["erreurs"]) / nb * 100, detail, list(r["drapeaux"])


def _acces(actions, coef):
    badges = {b["id"]: b for b in banque._banque()["acces"]["badges"]}
    drapeaux, affiche, decides = [], {}, {}
    bonnes = tardives = 0
    evts = [a for a in actions if isinstance(a, dict) and a.get("type") in ("badge_affiche", "decision")]
    # a temps egal, l'affichage passe avant la decision
    evts.sort(key=lambda a: (_t(a), 0 if a.get("type") == "badge_affiche" else 1))
    for a in evts:
        bid = a.get("badge")
        if bid not in badges:
            _ajoute(drapeaux, "action_inconnue")
            continue
        if a["type"] == "badge_affiche":
            affiche[bid] = _t(a)
            continue
        if bid in decides:
            continue
        if bid not in affiche:
            _ajoute(drapeaux, "horodatage_incoherent")
            continue
        try:
            d = int(a.get("decision"))
        except (TypeError, ValueError):
            d = None
        delai = (_t(a) - affiche[bid]) / coef
        juste = delai <= ACCES_DELAI and d == badges[bid]["ok"]
        if delai > ACCES_DELAI:
            tardives += 1
        decides[bid] = juste
        bonnes += 1 if juste else 0
    nb = len(badges)
    return bonnes / nb * 100, {"bonnes": bonnes, "nb": nb, "decisions": len(decides),
                               "tardives": tardives}, drapeaux


def _logique(actions, coef):
    qs = {q["id"]: q for q in banque._banque()["logique"]}
    vues, hors_delai = _premieres_reponses(actions, coef, LOGIQUE_DUREE)
    drapeaux, bonnes = [], 0
    for qid, a in vues.items():
        q = qs.get(qid)
        if q is None:
            _ajoute(drapeaux, "action_inconnue")
            continue
        if a.get("choix") == q["a"]:
            bonnes += 1
    return bonnes / len(qs) * 100, {"bonnes": bonnes, "nb": len(qs),
                                    "ignorees_hors_delai": hors_delai}, drapeaux


_CORRECTEURS = {
    "radio": _radio, "appel": _appel, "mc": _mc, "patrouilles": _patrouilles,
    "acces": _acces, "logique": _logique, "situations": _situations,
}


def score(epreuve, actions, coef):
    """Score serveur d'une epreuve : {score: int 0-100, detail: dict, drapeaux: list}."""
    if epreuve not in _CORRECTEURS:
        raise ValueError("epreuve inconnue : %s" % epreuve)
    pct, detail, drapeaux = _CORRECTEURS[epreuve](list(actions or []), float(coef))
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
