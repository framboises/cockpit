"""Evenements vus par Alfred : nom canonique, surnoms, contexte redige.

Deux besoins communs a tous les outils Alfred :

1. RETROUVER l'evenement dont parle l'operateur. Il dit << les camions >>,
   << le GP >>, << la Classic >>, << SBK >> : le nom canonique de Cockpit
   (`evenement.nom`, `24H CAMIONS`) n'apparait presque jamais tel quel. Les
   sigles (`evenement.short`) sont connus, les surnoms sont saisis par
   l'exploitation dans la page Configuration (collection
   `alfred_evenement_contexte`).

2. DONNER DU CONTEXTE au modele : la nature de l'evenement, son public, son
   vocabulaire. Ce texte est saisi a la main et sert a COMPRENDRE la
   question, jamais de source de faits : un horaire ou un chiffre ecrit la
   serait recopie au lieu d'appeler l'outil qui le calcule. Le champ est
   donc livre avec une consigne explicite.

Fonctions pures et lectures Mongo, `db` en argument, aucun import Flask.
"""

import re
import unicodedata
from datetime import datetime, timezone

COLLECTION = "alfred_evenement_contexte"
DESCRIPTION_MAX = 800
NOTE_MAX = 400
SURNOMS_MAX = 12
CONSIGNE = ("Contexte pour comprendre la question uniquement : n'en tirer aucun "
            "horaire, chiffre ou fait, ceux-ci viennent des outils.")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    # << 24 heures >> = << 24h >>
    s = re.sub(r"\b(\d+) ?(?:heures?|h)\b", r"\1h", s)
    return s


def _mots(s):
    """Mots significatifs, pluriel replie (camion = camions)."""
    vides = {"le", "la", "les", "l", "de", "du", "des", "d", "edition", "evenement"}
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w
            for w in norm(s).split() if w not in vides}


def contextes(db):
    """{nom: doc contexte} pour tous les evenements renseignes."""
    try:
        return {d["event"]: d for d in db[COLLECTION].find({}, {"_id": 0}) if d.get("event")}
    except Exception:
        return {}


def catalogue(db):
    """[{nom, short, surnoms}] : evenements de Cockpit (collection evenement,
    a defaut les noms des parametrages)."""
    ctx = contextes(db)
    out, vus = [], set()
    try:
        for d in db["evenement"].find({}, {"_id": 0, "nom": 1, "short": 1}):
            nom = d.get("nom")
            if nom and nom not in vus:
                vus.add(nom)
                out.append({"nom": nom, "short": d.get("short"),
                            "surnoms": (ctx.get(nom) or {}).get("surnoms") or []})
        for p in db["parametrages"].find({}, {"event": 1}):
            nom = p.get("event")
            if nom and nom not in vus:
                vus.add(nom)
                out.append({"nom": nom, "short": None,
                            "surnoms": (ctx.get(nom) or {}).get("surnoms") or []})
    except Exception:
        pass
    return out


def resoudre_nom(db, texte, cat=None):
    """(nom canonique, candidats). nom None si rien ou ambigu (candidats alors
    rempli quand plusieurs evenements conviennent)."""
    q = norm(texte)
    if not q:
        return None, []
    cat = cat if cat is not None else catalogue(db)

    def formes(e):
        return [e["nom"]] + ([e["short"]] if e.get("short") else []) + list(e.get("surnoms") or [])

    # 1. Egalite exacte avec le nom, le sigle ou un surnom
    exacts = [e["nom"] for e in cat if any(norm(f) == q for f in formes(e) if f)]
    if len(exacts) == 1:
        return exacts[0], []
    # 2. Un surnom ou un sigle CONTENU dans la phrase (<< les 24h camions de l'an dernier >>)
    qm = _mots(q)
    contenus = [e["nom"] for e in cat
                if any(f and _mots(f) and _mots(f) <= qm for f in formes(e))]
    if len(set(contenus)) == 1:
        return contenus[0], []
    # 3. Les mots de la question contenus dans le nom (<< camions >> -> 24H CAMIONS)
    sous = [e["nom"] for e in cat if qm and qm <= _mots(e["nom"])]
    if len(sous) == 1:
        return sous[0], []
    cands = sorted(set(exacts or contenus or sous))
    return None, cands[:12]


def contexte(db, event, year=None):
    """Bloc contexte d'un evenement, ou None si rien n'est saisi."""
    if not event:
        return None
    try:
        d = db[COLLECTION].find_one({"event": event}, {"_id": 0}) or {}
    except Exception:
        return None
    desc = str(d.get("description") or "").strip()
    note = str(((d.get("editions") or {}).get(str(year)) or "")).strip() if year else ""
    surnoms = [s for s in d.get("surnoms") or [] if s]
    if not (desc or note or surnoms):
        return None
    out = {"evenement": event, "consigne": CONSIGNE}
    if desc:
        out["description"] = desc
    if note:
        out["note_edition_%s" % year] = note
    if surnoms:
        out["surnoms"] = surnoms
    return out


# ---------------------------------------------------------------------------
# Ecriture (page Configuration, admin)
# ---------------------------------------------------------------------------

def _texte(v, n):
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()[:n]


def nettoyer(payload):
    """Valide une saisie. (doc, erreur)."""
    p = payload or {}
    event = str(p.get("event") or "").strip()
    if not event:
        return None, "evenement_requis"
    brut = p.get("surnoms") or []
    if isinstance(brut, str):
        brut = re.split(r"[,;\n]", brut)
    surnoms = []
    for s in brut:
        s = _texte(s, 60)
        if s and norm(s) not in {norm(x) for x in surnoms}:
            surnoms.append(s)
    editions = {}
    for y, t in (p.get("editions") or {}).items():
        y = str(y).strip()
        if re.fullmatch(r"\d{4}", y) and _texte(t, NOTE_MAX):
            editions[y] = _texte(t, NOTE_MAX)
    return {"event": event,
            "description": _texte(p.get("description"), DESCRIPTION_MAX),
            "surnoms": surnoms[:SURNOMS_MAX],
            "editions": editions}, None


def enregistrer(db, payload, auteur=None):
    doc, err = nettoyer(payload)
    if err:
        return None, err
    doc["updated_at"] = datetime.now(timezone.utc)
    doc["updated_by"] = auteur
    db[COLLECTION].update_one({"event": doc["event"]}, {"$set": doc}, upsert=True)
    return doc, None


def conflits_surnoms(db, doc):
    """Surnoms deja portes par un AUTRE evenement (nom, sigle ou surnom) :
    un surnom partage rendrait la resolution ambigue."""
    out = []
    for e in catalogue(db):
        if e["nom"] == doc["event"]:
            continue
        autres = {norm(f) for f in [e["nom"], e.get("short")] + list(e.get("surnoms") or []) if f}
        out += ["%s (%s)" % (s, e["nom"]) for s in doc.get("surnoms") or [] if norm(s) in autres]
    return out
