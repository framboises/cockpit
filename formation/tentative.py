"""Regles du passage unique du test PC Organisation (spec 6.1 et 6.2).

Une tentative par candidat et campagne (index unique partiel de store) :
`{campagne, candidat_id, statut, debut, fin, coef, seed, sid, reprises,
reprises_trace: [{date, epreuve, ip}], epreuves: {id: {etat, debut, fin,
enonce, actions, score, detail, drapeaux}}}`.

Etats d'une epreuve : a_faire, en_cours, terminee, abandonnee.
Etats d'une tentative : en_cours, terminee, interrompue, reinitialisee.

Decision du DOA (22/11) : une epreuve n'est jamais rejouee. Une epreuve
trouvee `en_cours` a la connexion est close en l'etat (`abandonnee`, notee sur
les actions recues) ; une seule reprise, dans l'heure qui suit le debut de la
tentative, et elle continue a l'epreuve suivante.

Toutes les fonctions recoivent `now` (datetime aware UTC) : aucune horloge
interne. Chaque transition est un `find_one_and_update` conditionne par l'etat
lu (etat de l'epreuve, nombre d'actions, `sid`, reprises) : deux requetes
concurrentes ne peuvent pas faire deux transitions ; celle qui perd relit et
recommence. Refus : `PermissionError("session")` (autre session active),
`ValueError("ordre")` (epreuve qui n'est pas la suivante), `ValueError("etat")`
(epreuve pas en cours), `ValueError("tentative")` (aucune tentative en cours).
"""
import math
import random
import secrets
from datetime import timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from formation import banque, correction, store

UNE_HEURE = timedelta(hours=1)
MARGE_EXPIRATION_MS = 60000
MAX_ACTIONS = 500  # par epreuve : un journal hostile ne remplit pas le document
_FINIES = ("terminee", "abandonnee")
_ESSAIS = 8


def _col(db):
    return db["tentatives"]


def _utc(d):
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def _ms(debut, now):
    return int((now - _utc(debut)).total_seconds() * 1000)


def active(db, candidat_id):
    """Tentative active (en_cours, terminee, interrompue) du candidat, ou None."""
    return _col(db).find_one({"campagne": store.CAMPAGNE, "candidat_id": candidat_id,
                              "statut": {"$in": store.STATUTS_TENTATIVE_ACTIFS}})


def _cid(candidat):
    return str(candidat["_id"])


def _en_cours(t):
    for e in store.EPREUVES:
        if t["epreuves"][e]["etat"] == "en_cours":
            return e
    return None


def _premiere_a_faire(t):
    for e in store.EPREUVES:
        if t["epreuves"][e]["etat"] == "a_faire":
            return e
    return None


def etat_client(t):
    """Etat expose au candidat : jamais de score ni de drapeau."""
    if t is None:
        return {"statut": "non_commence"}
    courante = None
    if t["statut"] == "en_cours":
        courante = _en_cours(t) or _premiere_a_faire(t)
    return {"statut": t["statut"], "epreuve_courante": courante,
            "epreuves": [{"id": e, "etat": t["epreuves"][e]["etat"]} for e in store.EPREUVES],
            "coef": t["coef"]}


def _coef(t):
    return correction.coef_valide(t.get("coef", 1.0))


def _fusion(*listes):
    out = []
    for liste in listes:
        for d in liste or ():
            if d not in out:
                out.append(d)
    return out


def _clore(db, t, e, etat, now, drapeaux=(), filtre=None, maj=None, push=None):
    """Clot l'epreuve `e` en cours de `t` (etat terminee ou abandonnee) avec son
    score, calcule sur les actions lues ; la condition sur le nombre d'actions
    garantit que le score porte sur le journal fige. Derniere epreuve : la
    tentative passe `terminee` (sauf si `maj` fixe deja le statut).
    Renvoie la tentative mise a jour, ou None si l'etat a change entre-temps."""
    ep = t["epreuves"][e]
    actions = ep.get("actions", [])
    res = correction.score(e, actions, _coef(t))
    p = "epreuves.%s." % e
    f = {"_id": t["_id"], "statut": "en_cours", p + "etat": "en_cours",
         p + "actions": {"$size": len(actions)}}
    f.update(filtre or {})
    s = {p + "etat": etat, p + "fin": now, p + "score": res["score"], p + "detail": res["detail"],
         p + "drapeaux": _fusion(ep.get("drapeaux"), res["drapeaux"], drapeaux)}
    s.update(maj or {})
    reste = [x for x in store.EPREUVES if x != e and t["epreuves"][x]["etat"] not in _FINIES]
    if not reste and "statut" not in s:
        s.update({"statut": "terminee", "fin": now})
    upd = {"$set": s}
    if push:
        upd["$push"] = push
    return _col(db).find_one_and_update(f, upd, return_document=ReturnDocument.AFTER)


def connexion(db, candidat, sid, ip, now):
    """Nouvelle connexion du candidat (nouveau `sid`, qui devient la seule session
    active). Epreuve en cours trouvee : close `abandonnee` (drapeau `abandon`) ;
    premiere fois et dans l'heure du debut -> une reprise tracee, suite a
    l'epreuve suivante ; sinon tentative `interrompue`. Plus d'une heure apres le
    debut sans epreuve en cours : `interrompue` aussi. Coupure hors epreuve :
    rien n'est perdu, pas de reprise.

    La reprise est comptee quel que soit le temps passe depuis le debut de
    l'epreuve : ne pas appeler `expirer_si_besoin` avant `connexion`."""
    for _ in range(_ESSAIS):
        t = active(db, _cid(candidat))
        if t is None:
            return etat_client(None)
        if t["statut"] != "en_cours":
            t = _col(db).find_one_and_update({"_id": t["_id"]}, {"$set": {"sid": sid}},
                                             return_document=ReturnDocument.AFTER) or t
            return etat_client(t)
        dans_l_heure = now - _utc(t["debut"]) <= UNE_HEURE
        e = _en_cours(t)
        if e is not None:
            reprises = t.get("reprises", 0)
            if reprises == 0 and dans_l_heure:
                maj = {"sid": sid, "reprises": 1}
                push = {"reprises_trace": {"date": now, "epreuve": e, "ip": ip}}
            else:
                maj = {"sid": sid, "statut": "interrompue", "fin": now}
                push = None
            r = _clore(db, t, e, "abandonnee", now, drapeaux=["abandon"],
                       filtre={"reprises": reprises}, maj=maj, push=push)
        else:
            f = {"_id": t["_id"], "statut": "en_cours"}
            f.update({"epreuves.%s.etat" % x: {"$ne": "en_cours"} for x in store.EPREUVES})
            s = {"sid": sid}
            if not dans_l_heure:
                s.update({"statut": "interrompue", "fin": now})
            r = _col(db).find_one_and_update(f, {"$set": s}, return_document=ReturnDocument.AFTER)
        if r is not None:
            return etat_client(r)
    raise RuntimeError("connexion : etat de la tentative instable")


def commencer(db, candidat, sid, coef_reglage, now):
    """Cree la tentative (irreversible, coefficient fige). Idempotent : une
    tentative active existante est renvoyee telle quelle."""
    t = active(db, _cid(candidat))
    if t is not None:
        return etat_client(t)
    coef = correction.coef_valide(coef_reglage) if candidat.get("amenagement") is True else 1.0
    doc = {
        "campagne": store.CAMPAGNE, "candidat_id": _cid(candidat), "statut": "en_cours",
        "debut": now, "fin": None, "coef": coef, "seed": secrets.randbits(31), "sid": sid,
        "reprises": 0, "reprises_trace": [],
        "epreuves": {e: {"etat": "a_faire", "actions": [], "drapeaux": []} for e in store.EPREUVES},
    }
    try:
        _col(db).insert_one(doc)
    except DuplicateKeyError:
        pass  # double clic concurrent : l'autre requete a cree la tentative
    return etat_client(active(db, _cid(candidat)))


def _charger(db, candidat, sid):
    t = active(db, _cid(candidat))
    if t is None or t["statut"] != "en_cours":
        raise ValueError("tentative")
    if t.get("sid") != sid:
        raise PermissionError("session")
    return t


def debut_epreuve(db, candidat, sid, epreuve, now):
    """Demarre `epreuve` si c'est la premiere `a_faire` et qu'aucune n'est en
    cours ; renvoie l'enonce tire (conserve dans la tentative). Jamais deux fois."""
    t = _charger(db, candidat, sid)
    if epreuve not in store.EPREUVES or _en_cours(t) is not None or _premiere_a_faire(t) != epreuve:
        raise ValueError("ordre")
    i = store.EPREUVES.index(epreuve)
    enonce = banque.enonce(epreuve, random.Random(t["seed"] + i))
    f = {"_id": t["_id"], "statut": "en_cours", "sid": sid,
         "epreuves.%s.etat" % epreuve: "a_faire"}
    f.update({"epreuves.%s.etat" % x: {"$in": list(_FINIES)} for x in store.EPREUVES[:i]})
    p = "epreuves.%s." % epreuve
    r = _col(db).find_one_and_update(
        f, {"$set": {p + "etat": "en_cours", p + "debut": now, p + "enonce": enonce}})
    if r is None:
        _charger(db, candidat, sid)  # session remplacee entre-temps -> PermissionError
        raise ValueError("ordre")
    return enonce


def _assainir(donnees):
    """Champs de l'action utilisables comme cles MongoDB."""
    return {k: v for k, v in donnees.items()
            if isinstance(k, str) and k and not k.startswith("$") and "." not in k}


def action(db, candidat, sid, epreuve, donnees, now):
    """Ajoute une action (reponse, prise, decision...) a l'epreuve en cours, avec
    `recu_ms` (ms serveur depuis le debut de l'epreuve). `t_ms` en avance de plus
    de 2 s sur `recu_ms` : ramene a `recu_ms`, drapeau `horodatage_incoherent`.
    Action recue apres la duree maximale de l'epreuve : ignoree."""
    if not isinstance(donnees, dict):
        raise ValueError("donnees")
    t = _charger(db, candidat, sid)
    if epreuve not in store.EPREUVES or t["epreuves"][epreuve]["etat"] != "en_cours":
        raise ValueError("etat")
    ep = t["epreuves"][epreuve]
    recu = _ms(ep["debut"], now)
    if recu > correction.duree_max_ms(epreuve, _coef(t)):
        return None
    a = _assainir(donnees)
    a["recu_ms"] = recu
    p = "epreuves.%s." % epreuve
    tm = correction.temps_ms(a)
    avance = tm is not None and tm > recu + 2000
    if avance:
        a["t_ms"] = recu
    upd = {"$push": {p + "actions": a}}
    if avance:
        upd["$addToSet"] = {p + "drapeaux": "horodatage_incoherent"}
    r = _col(db).update_one({"_id": t["_id"], "statut": "en_cours", "sid": sid,
                             p + "etat": "en_cours",
                             p + "actions.%d" % (MAX_ACTIONS - 1): {"$exists": False}}, upd)
    if r.matched_count == 0:
        t = _charger(db, candidat, sid)
        if t["epreuves"][epreuve]["etat"] != "en_cours":
            raise ValueError("etat")
        # journal plein : action ignoree
    return None


def fin_epreuve(db, candidat, sid, epreuve, now):
    """Fige l'epreuve en cours (`terminee`, score serveur). Duree serveur sous
    0,9 x la duree minimale de deroulement : drapeau `incoherente`. Derniere
    epreuve : tentative `terminee`. Renvoie l'etat client (sans score)."""
    for _ in range(_ESSAIS):
        t = _charger(db, candidat, sid)
        if epreuve not in store.EPREUVES or t["epreuves"][epreuve]["etat"] != "en_cours":
            raise ValueError("etat")
        ep = t["epreuves"][epreuve]
        drapeaux = []
        dmin = correction.duree_min_ms(epreuve, _coef(t))
        if dmin is not None and _ms(ep["debut"], now) < 0.9 * dmin:
            drapeaux.append("incoherente")
        r = _clore(db, t, epreuve, "terminee", now, drapeaux=drapeaux, filtre={"sid": sid})
        if r is not None:
            return etat_client(r)
    raise RuntimeError("fin_epreuve : etat de la tentative instable")


def expirer_si_besoin(db, tentative, now):
    """Epreuve en cours depuis plus que sa duree maximale + 60 s (onglet laisse
    ouvert) : close `abandonnee` (drapeaux `abandon`, `expiree`), sans compter de
    reprise. Renvoie la tentative a jour."""
    t = tentative
    for _ in range(_ESSAIS):
        if t is None or t.get("statut") != "en_cours":
            return t
        e = _en_cours(t)
        if e is None:
            return t
        limite = correction.duree_max_ms(e, _coef(t)) + MARGE_EXPIRATION_MS
        if _ms(t["epreuves"][e]["debut"], now) <= limite:
            return t
        r = _clore(db, t, e, "abandonnee", now, drapeaux=["abandon", "expiree"])
        if r is not None:
            return r
        t = _col(db).find_one({"_id": t["_id"]})
    return t


def reinitialiser(db, candidat_id, motif, auteur, now):
    """Reinitialisation par le DOA : motif obligatoire, tentative conservee en
    `reinitialisee`, journalisee ; le candidat peut recommencer de zero."""
    motif = (motif or "").strip() if isinstance(motif, str) else ""
    if not motif:
        raise ValueError("motif")
    r = _col(db).update_one(
        {"campagne": store.CAMPAGNE, "candidat_id": candidat_id,
         "statut": {"$in": store.STATUTS_TENTATIVE_ACTIFS}},
        {"$set": {"statut": "reinitialisee",
                  "reinitialisation": {"date": now, "motif": motif, "auteur": auteur}}})
    if r.matched_count == 0:
        raise ValueError("tentative")
    store.journal(db, "reinitialisation", auteur, candidat_id=candidat_id, motif=motif)


def score_global(tentative, ponderations):
    """Moyenne ponderee des scores des epreuves terminees ou abandonnees, None si
    aucune (ou poids total nul). Poids absent ou invalide : 0. Tentative close
    (`terminee` ou `interrompue`) : les epreuves jamais jouees (`a_faire`)
    comptent 0 avec leur poids, pour qu'un candidat interrompu ne passe pas
    devant un candidat complet ; tentative en cours : epreuves jouees seules."""
    close = tentative.get("statut") in ("terminee", "interrompue")
    total = poids = 0.0
    for e, ep in (tentative.get("epreuves") or {}).items():
        if ep.get("etat") in _FINIES and isinstance(ep.get("score"), (int, float)):
            note = ep["score"]
        elif close and ep.get("etat") == "a_faire":
            note = 0
        else:
            continue
        w = (ponderations or {}).get(e, 0)
        if isinstance(w, bool) or not isinstance(w, (int, float)) or not (0 < w < math.inf):
            continue
        total += note * w
        poids += w
    return total / poids if poids else None
