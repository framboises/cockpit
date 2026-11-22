"""Banque de questions du test PC Organisation (formation/banque_pcorg.json).

Le JSON reprend la maquette test_pcorg.html (RADIO, CALLS, CATS / ZONES / MOYENS
/ MC, plan et moyens des patrouilles, BADGES, LOGIC, JUDGE) avec des ids stables :
radio r01..r14, appel a1 / etape a1s1 / choix a1s1c1, main courante m1..m3,
incidents I1..I8, badges b01..b10, logique l1 / choix l1c1, situations s1 / s1c1.

`enonce()` construit ce qui part vers le candidat :
- appel, mc, logique, situations : sans `points`, `ans`, `a`, `ok`, questions
  et choix melanges par le `rng` fourni (spec 12). Les etapes d'un appel gardent
  leur ordre (c'est une conversation) ; les listes de la main courante
  (categories, lieux, moyens) restent dans l'ordre de la maquette.
- radio, patrouilles, acces : ordre fixe, identique pour tous, avec ce qu'il faut
  au retour immediat du jeu (`pour_moi`, `priorite`, types d'incidents, `ok`).
"""
import copy
import json
import os

_CHEMIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banque_pcorg.json")
_cache = None

_SECRETS = ("points", "ans", "a", "ok")


def _banque():
    """Banque en cache, a ne pas modifier (usage interne du module)."""
    global _cache
    if _cache is None:
        with open(_CHEMIN, encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def charger():
    """Contenu complet de la banque (copie : l'appelant peut la modifier)."""
    return copy.deepcopy(_banque())


def secrets_par_id(epreuve):
    """Reponses attendues d'une epreuve : {id: {points|ok|ans|a}} (usage serveur, jamais envoye au candidat)."""
    acc = {}

    def walk(obj):
        if isinstance(obj, dict):
            if isinstance(obj.get("id"), str):
                sec = {k: obj[k] for k in _SECRETS if k in obj}
                if sec:
                    acc[obj["id"]] = sec
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    walk(_banque().get(epreuve))
    return copy.deepcopy(acc)


def _sans_secrets(obj):
    if isinstance(obj, dict):
        return {k: _sans_secrets(v) for k, v in obj.items() if k not in _SECRETS}
    if isinstance(obj, list):
        return [_sans_secrets(v) for v in obj]
    return obj


def _melange(liste, rng):
    out = list(liste)
    rng.shuffle(out)
    return out


def enonce(epreuve, rng):
    """Enonce de `epreuve` pour le candidat (dict pret a serialiser)."""
    b = charger()
    if epreuve == "radio":
        return {"messages": b["radio"]}
    if epreuve == "patrouilles":
        return b["patrouilles"]
    if epreuve == "acces":
        return b["acces"]
    if epreuve == "appel":
        appels = []
        for a in _melange(b["appel"], rng):
            for e in a["etapes"]:
                e["choix"] = _melange(e["choix"], rng)
            appels.append(a)
        return _sans_secrets({"appels": appels})
    if epreuve == "mc":
        mc = b["mc"]
        mc["fiches"] = _melange(mc["fiches"], rng)
        return _sans_secrets(mc)
    if epreuve in ("logique", "situations"):
        questions = []
        for q in _melange(b[epreuve], rng):
            q["choix"] = _melange(q["choix"], rng)
            questions.append(q)
        return _sans_secrets({"questions": questions})
    raise ValueError("epreuve inconnue : %s" % epreuve)
