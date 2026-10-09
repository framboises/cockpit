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


# << Florent DUPONT >>, << MARAND Ulysse >>, << SOUARE ABDOUL Karim >> : prenom
# + NOM en capitales, dans un sens ou dans l'autre
_RX_PRENOM_NOM = re.compile(
    r"\b(?:[A-ZÀ-Ý][a-zà-ÿ'-]{2,}\s+[A-ZÀ-Ý]{3,}(?:\s+[A-ZÀ-Ý]{3,})?"
    r"|[A-ZÀ-Ý]{3,}(?:\s+[A-ZÀ-Ý]{3,})?\s+[A-ZÀ-Ý][a-zà-ÿ'-]{2,})\b")


def _prenom_nom(m):
    mots = m.group(0).split()
    # << Porte NORD >>, << Zone EST >>, << Cam CMS >> : pas une personne
    if any(w in _PAS_UN_NOM or w in _SIGLES for w in mots):
        return m.group(0)
    return "[nom]"


_SIGLES = {"CMS", "CMP", "SERI", "PCA", "PCO", "PCS", "ACO", "BPS", "PDA", "NORD", "SUD", "EST", "OUEST",
           "HOUX", "AA", "RAS", "SAMU", "SMUR", "SDIS", "OPV", "VIP", "PMR", "MCLAREN", "KTM", "BMW", "AENEAS",
           "TERANGA", "PROGUARD", "SKIDATA", "WC", "CIK", "LMC", "GPF", "IAME", "FIA", "DSE", "RD", "PS"}


def masquer(texte):
    import alfred_main_courante as AM
    t = _RX_CIVILITE.sub("[nom]", AM.masquer(texte))
    t = _RX_PRENOM_NOM.sub(_prenom_nom, t)
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


# Mots de la QUESTION, pas de la situation : la VM passe la phrase entiere
# (live 09/10 : << Comment on avait gere les bagarres au camping ? >> ne
# trouvait rien, << bagarre au camping >> 15 cas).
MOTS_QUESTION = set("""est ce qu que quoi on nous vous ils a avait avaient avons ete etait etaient deja eu
eus fait faits faire fallu comment combien quand pourquoi gere geree geres gerees gerer regle reglee
regles reglees regler resolu resolus traite traites traitee traiter passe passes passait arrive
arrivee arrives arrivees arriver arrivait cas similaire similaires semblable semblables precedent
precedents habitude habituellement dhabitude d generalement souvent fois autre autres an annee dernier
derniere dernieres derniers edition editions avant sur les des la le du de au aux un une ca cela
s se etre y en il elle t on avez""".split())


def nettoyer(texte, evenement=""):
    """La situation sans les mots de la question ni l'evenement cite."""
    import pcorg_assist as PA
    ev = set(PA.fold(evenement).split())
    mots = [w for w in re.findall(r"[\wÀ-ÿ'-]+", texte)
            if PA.fold(w).replace("'", " ").split()
            and not all(x in MOTS_QUESTION or x in ev for x in PA.fold(w).replace("'", " ").split())]
    return " ".join(mots).strip()


# Indices d'etat qui ne nomment pas l'incident : << il bouge plus >> ramenait
# des portails qui << bougent avec le vent >>
INDICES = {"bouge", "bougent", "bouger", "plus", "encore", "toujours", "rien", "vraiment", "beaucoup",
           "derriere", "devant", "dessous", "dessus", "face", "proche", "proximite"}
INDICES_FICHES = {"odeur", "odeurs", "probleme", "problemes", "souci", "soucis", "poubelle", "poubelles",
                  "portail", "portails", "portillon", "portillons"}


def coeur(texte, synonymes=False):
    """Les mots de l'INCIDENT, sans lieux, numeros ni mots d'indice : << un
    spectateur fait un malaise en tribune 13 >> cherchait aussi << tribune 13 >>
    et ramenait un vol en tribune 13. Vide : le texte entier."""
    import alfred_reference as R
    import pcorg_assist as PA
    # Les LIEUX seulement : les mots d'indice des fiches reflexes (odeur,
    # poubelle...) decrivent ici l'incident (<< odeur de gaz >> sans << odeur >>
    # tombait sous le seuil)
    lieux = R.LIEUX - INDICES_FICHES
    garde = [w for w in texte.split()
             if not any(t.isdigit() or t in lieux or t in R.VIDES or t in INDICES
                        for t in PA.fold(w).split() or [""])]
    if not synonymes:
        return " ".join(garde) or texte
    # Second essai seulement (moins de 3 cas) : synonymes de terrain des fiches
    # reflexes. << ca bouchonne >> ne trouvait rien (les fiches disent
    # << congestion >>) ; en premier essai, << chien perdu >> ramenait des
    # enfants perdus par << perdu >> -> << enfant perdu recherche >>.
    plies = set(PA.fold(" ".join(garde)).split())
    ajouts = []
    for decl, ajout in R.SYNONYMES:
        if plies & set(decl.split()):
            ajouts += [w for w in ajout.split() if w not in plies and w not in ajouts]
    return " ".join(garde + ajouts) or texte


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
    evenement = str(args.get("evenement") or "").strip()
    texte = nettoyer(str(args.get("situation") or "").strip()[:300], evenement)
    if not PA.tokenize(texte):
        return {"disponible": True, "vue": "vide",
                "resume": "Préciser la situation à rapprocher des cas passés (ce qui se passe, où)."}
    col = db["pcorg"]
    if not PA.ensure_text_index(col):
        return {"disponible": False, "source": "precedents",
                "resume": "Recherche des cas passés indisponible (index texte absent). Ne pas l'estimer."}
    q = _requete(db, texte, ctx, evenement)
    proj = dict(PROJECTION)
    proj["score"] = {"$meta": "textScore"}

    def chercher(incident):
        qq = dict(q)
        qq["$text"] = {"$search": " ".join(PA.search_terms(incident))}
        cands = list(col.find(qq, proj).sort([("score", {"$meta": "textScore"})]).limit(CANDIDATS_MAX))
        return PA.rank_precedents({"text": incident}, cands, top=RETENUS, min_text=TEXTE_MIN)

    incident = coeur(texte)
    ranked = chercher(incident)
    if len(ranked) < 3:
        large = coeur(texte, synonymes=True)
        if large != incident:
            autre = chercher(large)
            if len(autre) > len(ranked):
                ranked = autre
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
        l.append("Moyens engagés sur %s : %s." % ("ces %d cas" % n if n > 1 else "ce cas", ", ".join(
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
