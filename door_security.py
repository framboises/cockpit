"""Effectif de securite (palpation / filtrage) prevu par porte, et capacite
qui en decoule.

Pourquoi : le goulet d'une porte n'est pas le scan mais la securite en amont
des tripodes. Rejeu 2026 (scripts/replay_door_saturation.py) : les demandes
"Renfort filtrage" arrivent quand la porte ne debite que ~15 % de sa capacite
de scan. La capacite utile est donc  agents de securite presents x 350/h
(debit d'un agent de palpation, valeur terrain).

Deux sources, jointes par le numero de poste :

  bible (application BIBLER) : fiche de poste par (event, year, numero) --
      post.metier ("SECURITE"), post.affectation ("Porte Nord - Acces
      Pietons"), fiche.positioning ("Couloir de controle avant le controle du
      billet"). C'est la table d'association poste -> lieu -> metier tenue a
      jour par l'exploitation.
  calendrier_<annee>_<evenement> (Planbition) : agents prevus par poste
      (`shiftcode` = numero), par creneau de 30 min.

⚠️ La numerotation n'est PAS stable d'un evenement a l'autre (8790 = "Porte
Karting pietons BMW" aux 24H AUTOS, "Porte CIK Bis" ailleurs) : la
correspondance se lit toujours dans la bible de l'edition, jamais dans une
table codee en dur.

Seuls les postes SECURITE PIETONS comptent : un poste d'acces vehicules ne
palpe pas les spectateurs.

Module pur : `db` en argument, aucun import Flask.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from datetime import datetime

from scan_staffing import calendar_collection_name

logger = logging.getLogger(__name__)

AGENT_RATE_H = 350  # personnes / heure / agent de palpation (valeur terrain)

# Mots qui ne distinguent pas une porte d'une autre (des deux cotes : bible
# et noms du controle d'acces).
_GENERIC = {"PORTE", "PORTES", "ACCES", "ACCE", "PIETON", "VEHICULE", "PASSERELLE",
            "PORTAIL", "PUBLIC", "PRINCIPAL", "ENTREE", "RESERVE", "MEMBRE", "ACO",
            "AA", "A", "P", "DE", "DU", "DES", "LA", "LE", "LES", "ET"}


def norm(s):
    """Majuscules sans accents ni ponctuation, pluriels simples replies
    (HUNAUDIERES -> HUNAUDIERE, PIETONS -> PIETON)."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    words = []
    for w in re.sub(r"[^A-Z0-9]+", " ", s.upper()).split():
        if len(w) > 3 and w.endswith("S") and not w.endswith("SS"):
            w = w[:-1]
        words.append({"PORTAIL": "PORTE"}.get(w, w))
    return " ".join(words)


def _tokens(s):
    return set(norm(s).split()) - _GENERIC


def _is_pedestrian(post, fiche):
    aff = norm(post.get("affectation"))
    pos = norm((fiche or {}).get("positioning"))
    if "VEHICULE" in aff.split() and "PIETON" not in aff.split():
        return False
    return "PIETON" in aff.split() or "COULOIR DE CONTROLE" in pos or "CONTROLE DU BILLET" in pos


def match_gate(affectation, gate_names):
    """Porte du controle d'acces designee par une affectation bible, ou None.

    Retient la porte dont TOUS les mots distinctifs figurent dans
    l'affectation, la plus specifique en cas de choix ("NORD BIS" plutot que
    "NORD"), et a egalite la porte pietonne (la securite palpe les pietons).
    Plusieurs candidates aussi specifiques et aucune pietonne : None (mieux
    vaut ignorer un poste que le rattacher a la mauvaise porte).
    """
    aff = _tokens(affectation)
    if not aff:
        return None
    best, best_score = [], -1
    for g in gate_names:
        gt = _tokens(g)
        if not gt or not gt <= aff:
            continue
        score = len(gt)
        if score > best_score:
            best, best_score = [g], score
        elif score == best_score:
            best.append(g)
    if not best:
        return None
    if len(best) > 1:
        ped = [g for g in best if "PIETON" in norm(g).split()]
        not_veh = [g for g in best if "VEHICULE" not in norm(g).split()]
        best = ped or not_veh or best
    if len(best) > 1:
        # "Porte Passerelle Panorama" : PORTE PANORAMA plutot que P PANORAMA
        # (parking), "Porte Musee" : PORTE MUSEE plutot que ENTREE MUSEE.
        portes = [g for g in best if norm(g).startswith("PORTE ")]
        if len(portes) == 1:
            best = portes
    return best[0] if len(best) == 1 else None


def load_door_posts(db, event, year, gate_names):
    """{numero de poste: nom de porte} pour les postes SECURITE pietons.

    Lit la bible de l'edition ; a defaut, la plus recente du meme evenement
    (la numerotation est stable au sein d'un evenement d'une annee a l'autre,
    pas entre evenements). Rend aussi la liste des postes non rattaches, pour
    le diagnostic.
    """
    try:
        year = int(year)
    except (TypeError, ValueError):
        return {}, []
    q = {"event": event, "post.metier": "SECURITE", "post.zone": {"$regex": "^PORTES?$", "$options": "i"}}
    years = sorted({d.get("year") for d in db["bible"].find(q, {"year": 1}) if isinstance(d.get("year"), int)})
    if not years:
        return {}, []
    use = year if year in years else max([y for y in years if y <= year] or years)
    out, unmatched = {}, []
    for d in db["bible"].find(dict(q, year=use), {"post": 1, "fiche.positioning": 1}):
        p = d.get("post") or {}
        num = p.get("number")
        if not isinstance(num, int) or not p.get("active", True):
            continue
        # Zone PORTES de la bible = portes publiques. Sans ce filtre, "Paddock
        # Nord - Acces pietons" (8164) tombait sur PORTE NORD PIETONS et
        # "Porte Tertre Rouge Aire d'Accueil" (8501) sur PORTE TERTRE ROUGE.
        if norm(p.get("zone")) not in ("PORTE",):
            continue
        if not _is_pedestrian(p, d.get("fiche")):
            continue
        g = match_gate(p.get("affectation"), gate_names)
        if g:
            out[num] = g
        elif "PORTE" in norm(p.get("affectation")).split():
            unmatched.append((num, p.get("affectation")))
    return out, unmatched


def load_security_staffing(db, event, year, posts):
    """{porte: {debut de creneau 30 min (Paris naif): agents prevus}}.

    `posts` = {numero: porte} (load_door_posts). Rend {} si le calendrier est
    absent : l'appelant doit alors s'abstenir (jamais une capacite nulle, qui
    se lirait comme une porte saturee en permanence).
    """
    if not posts:
        return {}
    name = calendar_collection_name(event, year)
    try:
        if name not in db.list_collection_names():
            return {}
    except Exception:
        logger.warning("door_security : liste des collections illisible", exc_info=True)
        return {}
    out = defaultdict(lambda: defaultdict(int))
    for d in db[name].find({"accueil_surete": "S"}, {"shiftcode": 1, "donnees_presences": 1}):
        try:
            door = posts.get(int(d.get("shiftcode")))
        except (TypeError, ValueError):
            continue
        if not door:
            continue
        for jour in d.get("donnees_presences") or []:
            date = (jour or {}).get("date")
            for slot in (jour or {}).get("plages_horaires") or []:
                nb = slot.get("nombre_personnes") or 0
                if not nb or not date:
                    continue
                try:
                    t = datetime.strptime("%s %s" % (date, slot.get("heure_debut")), "%Y-%m-%d %H:%M")
                except (TypeError, ValueError):
                    continue
                out[door][t] += int(nb)
    return {k: dict(v) for k, v in out.items()}


def agents_at(staffing, door, t):
    """Agents prevus sur le creneau de 30 min contenant `t` (Paris naif)."""
    slot = t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)
    return (staffing.get(door) or {}).get(slot, 0)


def security_capacity(staffing, door, t, rate_h=AGENT_RATE_H):
    """Capacite de securite (/h) au creneau de `t`, None si aucun agent prevu."""
    n = agents_at(staffing, door, t)
    return n * rate_h if n else None
