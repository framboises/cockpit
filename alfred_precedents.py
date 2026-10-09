"""Outil Alfred `cockpit_precedents` : les cas passes de la main courante
proches d'une situation (autres editions, fiches closes).

Principe (decision d'exploitation du 09/10/2026, plutot que d'entrainer le
modele sur les 30 000 fiches) : COCKPIT retrouve les cas et compte ce qui a
ete fait ; le modele recopie. Recherche = celle du bloc << Precedents >> des
fiches (pcorg_assist : index texte `pca_text`, TF-IDF + Jaccard). Cockpit
compte les moyens engages (champs intervenant1..5 de Prysm), la duree
jusqu'a la cloture, et cite les 3 cas les plus proches avec leur cloture.

Un cas passe n'est pas une consigne : la fiche reflexe du wiki et le PCA
priment, le resume le dit.

Perimetre : categories du jeton de portee (ctx.cat_query), jamais sur le
chemin sans jeton (WhatsApp). Donnees personnelles : telephones, e-mails,
immatriculations masques (alfred_main_courante.masquer) et noms precedes
d'une civilite (M., Mme...) ; les champs appelant / telephone / operateur ne
sortent jamais. Lecture seule.
"""

import re
import statistics
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo("Europe/Paris")
STATUT_CLOS = 10
CANDIDATS_MAX = 800
RETENUS = 20            # cas comptes (moyens, duree, editions)
CITES = 3               # cas detailles
TEXTE_MIN = 0.30        # part du texte dans le score : en dessous, pas un cas proche
                        # (0,20 laissait << Permis feu >> pour << feu de voiture >>)

PROJECTION = {"_id": 1, "event": 1, "year": 1, "ts": 1, "close_ts": 1, "category": 1, "text": 1,
              "text_full": 1, "area": 1, "status_code": 1, "niveau_urgence": 1,
              "content_category.sous_classification": 1, "content_category.intervenant1": 1,
              "content_category.intervenant2": 1, "content_category.intervenant3": 1,
              "content_category.intervenant4": 1, "content_category.intervenant5": 1}

# Libelles Prysm heterogenes -> un seul nom
MOYENS = {"patrouille securite": "Patrouille sécurité", "patrouille sécurité": "Patrouille sécurité",
          "fourriere": "Fourrière", "fourrière": "Fourrière", "tangos": "Tango",
          "relation clientele": "Relation clientèle", "pc préfecture": "PC Préfecture",
          "pc prefecture": "PC Préfecture", "dircourse": "Direction de course"}
MOYENS_IGNORES = {"", "autres", "aucun"}

_RX_CIVILITE = re.compile(
    r"\b(?:M\.|Mr\.?|Mme|Mlle|Monsieur|Madame|Mademoiselle)\s+"
    r"[A-ZÀ-Ý][\w'-]+(?:\s+[A-ZÀ-Ý][\w'-]+){0,2}")
# << V. Beausmenil >>, << A Boulay >> : initiale + nom
_RX_INITIALE = re.compile(r"\b[A-Z]\.?\s[A-ZÀ-Ý][a-zà-ÿ'-]{2,}\b")
# << Batist 8 ans >> : un prenom devant un age
_RX_PRENOM_AGE = re.compile(r"\b[A-ZÀ-Ý][a-zà-ÿ'-]{2,}(?=\s*,?\s*\d{1,2}\s*ans\b)")
# << accord Tony >>, << aupres de Tony >>, << contacte Julien >> : prenom apres un verbe
# ou une preposition de contact
_RX_APRES = re.compile(r"\b(accord|aupr[eè]s de|avec|par|contact[ée]?|appel[ée]?|pr[ée]venu)(\s)"
                       r"([A-ZÀ-Ý][a-zà-ÿ'-]{2,})\b")
# Mots en majuscule initiale qui ne sont pas des personnes (lieux, services)
_PAS_UN_NOM = {"Police", "Pompiers", "Gendarmerie", "Patrouille", "Porte", "Tribune", "Village", "Paddock",
               "Houx", "Panorama", "Beauséjour", "Maison", "Prysm", "Cockpit", "Tango", "Tangos", "Direction",
               "Service", "Sécurité", "Securite", "Reflex", "Proguard", "Aeneas", "Le", "La", "Les"}


def masquer(texte):
    import alfred_main_courante as AM
    t = _RX_CIVILITE.sub("[nom]", AM.masquer(texte))
    t = _RX_INITIALE.sub("[nom]", t)
    t = _RX_PRENOM_AGE.sub(lambda m: m.group(0) if m.group(0) in _PAS_UN_NOM else "[prénom]", t)
    return _RX_APRES.sub(lambda m: m.group(0) if m.group(3) in _PAS_UN_NOM
                         else m.group(1) + m.group(2) + "[nom]", t)


# Lignes de chronologie qui ne disent pas comment l'affaire s'est terminee
_RX_TRACE = re.compile(r"^\s*(Description|Fiche modifi|Categorie|Catégorie|Sous-classification|Statut)\b"
                       r"|\s->\s", re.I)


def _court(texte, n):
    t = re.sub(r"\s+", " ", masquer(texte)).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _date(dt):
    import pcorg_history as PH
    d = PH.to_aware(dt)
    return d.astimezone(TZ_PARIS).strftime("%d/%m/%Y %H:%M") if d else "?"


def _edition(doc):
    return ("%s %s" % (doc.get("event") or "", doc.get("year") or "")).strip()


def _moyens(doc):
    cc = doc.get("content_category") or {}
    out = []
    for i in range(1, 6):
        v = str(cc.get("intervenant%d" % i) or "").strip()
        if v.lower() in MOYENS_IGNORES:
            continue
        v = MOYENS.get(v.lower(), v)
        if v not in out:
            out.append(v)
    return out


def _duree_txt(mins):
    if mins is None:
        return None
    if mins < 60:
        return "%d min" % mins
    h, m = divmod(mins, 60)
    return "%d h %02d" % (h, m) if h < 48 else "%d j" % (h // 24)


def _requete(db, texte, ctx, evenement):
    q = {"category": (ctx or {}).get("cat_query") or {"$regex": "^PC[OS]\\."},
         "status_code": STATUT_CLOS}
    ev, yr = (ctx or {}).get("event"), (ctx or {}).get("year")
    if ev and yr:
        try:
            q["$nor"] = [{"event": str(ev), "year": int(yr)}]   # l'edition en cours = la main courante
        except (TypeError, ValueError):
            pass
    if evenement:
        q["event"] = {"$regex": re.escape(evenement[:30]), "$options": "i"}
    return q


def t_precedents(db, args, ctx=None):
    import pcorg_assist as PA
    args, ctx = args or {}, ctx or {}
    if ctx.get("sans_main_courante"):
        return {"disponible": True, "vue": "refus",
                "resume": "Les cas passés de la main courante ne sont pas consultables sur ce canal."}
    texte = str(args.get("situation") or "").strip()[:300]
    if not PA.tokenize(texte):
        return {"disponible": True, "vue": "vide",
                "resume": "Préciser la situation à rapprocher des cas passés (ce qui se passe, où)."}
    col = db["pcorg"]
    if not PA.ensure_text_index(col):
        return {"disponible": False, "source": "precedents",
                "resume": "Recherche des cas passés indisponible (index texte absent). Ne pas l'estimer."}
    evenement = str(args.get("evenement") or "").strip()
    q = _requete(db, texte, ctx, evenement)
    q["$text"] = {"$search": " ".join(PA.search_terms(texte))}
    proj = dict(PROJECTION)
    proj["score"] = {"$meta": "textScore"}
    cands = list(col.find(q, proj).sort([("score", {"$meta": "textScore"})]).limit(CANDIDATS_MAX))
    ranked = PA.rank_precedents({"text": texte}, cands, top=RETENUS, min_text=TEXTE_MIN)
    portee = "autres éditions" + (" de « %s »" % evenement if evenement else "") + ", fiches closes"
    if not ranked:
        return {"disponible": True, "vue": "aucun", "trouves": 0,
                "resume": "Aucun cas passé proche de « %s » (%s). Ne pas en inventer." % (texte, portee)}

    docs = [c for s, c, r in ranked]
    moyens, editions, natures, durees = Counter(), Counter(), Counter(), []
    for d in docs:
        moyens.update(_moyens(d))
        editions[_edition(d)] += 1
        n = ((d.get("content_category") or {}).get("sous_classification") or "").strip()
        if n:
            natures[n] += 1
        m = PA._duration_min(d)
        if m is not None and m <= 7 * 24 * 60:
            durees.append(m)

    # Les cas cites : leur cloture et leurs dernieres actions
    cites = docs[:CITES]
    complets = {d["_id"]: d for d in col.find({"_id": {"$in": [c["_id"] for c in cites]}},
                                               {"comment_history": 1, "comment": 1, "status_code": 1,
                                                "close_ts": 1, "ts": 1})}
    cas = []
    for d in cites:
        full = dict(d)
        full.update(complets.get(d["_id"], {}))
        info = PA.closing_info(full)
        fin = info.get("closing_comment") or ""
        if not fin:
            utiles = [e.get("text") or "" for e in info.get("last_entries") or []
                      if (e.get("text") or "").strip() and not _RX_TRACE.search(e.get("text") or "")]
            fin = utiles[-1] if utiles else ""
        cas.append({
            "edition": _edition(d), "date": _date(d.get("ts")),
            "nature": ((d.get("content_category") or {}).get("sous_classification") or "").strip() or None,
            "zone": re.sub(r"_MC PC[OS]/?", "", str((d.get("area") or {}).get("desc") or "")).strip("/ ") or None,
            "texte": _court(d.get("text") or d.get("text_full"), 160),
            "moyens": _moyens(d),
            "issue": _court(fin, 220) or None,
            "duree": _duree_txt(PA._duration_min(full)),
        })

    n = len(docs)
    l = ["Cas passés proches de « %s » (%s) : %d retrouvé%s, %s." % (
        texte, portee, n, "s" if n > 1 else "",
        ", ".join("%s (%d)" % e for e in editions.most_common(4)))]
    if moyens:
        l.append("Moyens engagés sur ces %d cas : %s." % (n, ", ".join(
            "%s %d" % m for m in moyens.most_common(6))))
    else:
        l.append("Moyens engagés : non renseignés sur ces fiches.")
    if len(durees) >= 3:
        l.append("Durée médiane jusqu'à la clôture : %s." % _duree_txt(int(statistics.median(durees))))
    l.append("Les %d plus proches :" % len(cas))
    for i, c in enumerate(cas, 1):
        bits = [c["edition"], c["date"]] + [x for x in (c["nature"], c["zone"]) if x]
        ligne = "%d. %s : « %s »" % (i, ", ".join(bits), c["texte"])
        if c["moyens"]:
            ligne += " ; moyens : %s" % ", ".join(c["moyens"])
        if c["issue"]:
            ligne += " ; issue : « %s »" % c["issue"]
        if c["duree"]:
            ligne += " (%s)" % c["duree"]
        l.append(ligne)
    if n < 3:
        l.append("Peu de cas comparables : ne pas en tirer de tendance.")
    l.append("Un cas passé n'est pas une consigne : la fiche réflexe du wiki et le PCA priment.")
    return {"disponible": True, "vue": "cas", "trouves": n,
            "moyens": dict(moyens.most_common(8)), "editions": dict(editions.most_common(6)),
            "natures": dict(natures.most_common(4)), "cas": cas,
            "consigne": ("Recopier resume tel quel. N'en tirer aucune consigne et ne rien ajouter : "
                         "la conduite a tenir vient de cockpit_reference."),
            "resume": "\n".join(l)}
