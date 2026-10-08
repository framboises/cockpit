"""Outil Alfred `cockpit_reference` : fiches reflexes du wiki PC Orga et
carroyage du site. Remplace `cockpit_wiki_procedures`.

Principe (decision d'exploitation du 08/10/2026) : c'est COCKPIT qui
identifie la fiche, pas le modele. Le modele transmet les mots de
l'operateur ; Cockpit les compare aux fiches publiees (titre, sous-cas,
situation, precisions, avec des synonymes de terrain) et rend :
- une fiche nette : un guide court redige ICI (questions a poser, actions,
  decisions du logigramme, a consigner, piege) + un lien vers la fiche ;
- deux ou trois fiches proches : la question a poser a l'operateur, redigee
  ici, pour les departager ;
- aucune : le dire, et ne JAMAIS improviser de procedure.
Le modele recopie `resume`. Pas de dialogue pas a pas mene par le modele.

Seules les fiches PUBLIEES sont lues (un brouillon n'est pas valide).
Lecture seule.
"""

import math
import re
import unicodedata

COL = "cockpit_wiki_procedures"
SEUIL_MIN = 3.0          # score minimal d'une fiche retenue
RATIO_AMBIGU = 0.75      # une 2e fiche a >= 75 % du score de la 1re : demander
CANDIDATS_MAX = 3

# Poids des champs d'une fiche. `mots_cles` (facultatif, liste) : vocabulaire
# de terrain ajoute a une fiche sans toucher au code.
POIDS = (("titre", 4.0), ("mots_cles", 3.0), ("souscas", 3.0), ("situation", 2.0),
         ("details", 1.0), ("questions", 1.0), ("conduite", 0.5), ("pieges", 0.5))

VIDES = set("""a au aux avec ce ces cet cette dans de des du elle en est et il ils je la le les leur
lui ma mais me mes moi mon ne nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton
tu un une vos votre vous y ya il ont sont etre avoir fait faire quoi comment quand faut dois doit
peut peux veut va vais avons avez plus tres bien tout tous toute toutes ici la-bas alors donc puis
cas procedure procedures fiche fiches wiki reflexe reflexes conduite tenir gerer gere aide aider
quelqu personne gens monsieur madame pres cote vers chez entre sans sous apres avant encore deja
quel quelle quels quelles fera sera serait temps heure heures demain aujourd hui soir matin nuit tot
tard maintenant minute minutes besoin trop beaucoup peu
""".split())

# Mots de terrain -> mots des fiches. Les declencheurs sont normalises (sans
# accents). Volontairement absents : << wc >>, << toilettes >> (servent a
# LOCALISER : << un homme par terre pres des WC >> n'est pas une fuite).
SYNONYMES = [
    ("bagarre bagarres baston bastons rixe rixes bagarrent battent battre echauffouree embrouille "
     "tapent battent bastonnent castagne", "altercation rixe"),
    ("frappe frapper frappee tabasse tabasser coups agresse agressee agresseur agresser",
     "agression"),
    # Sans << tombe >> ni << chute >> : << un panneau est tombe >> n'est pas un secours
    ("malaise inconscient inconsciente evanoui evanouie blesse blessee blesses blessure respire "
     "respirer saigne saignement cardiaque convulse bouge pale sent vomit vomi douleur",
     "secours victime malaise blessure"),
    ("perdu perdue egare egaree gamin gamine gosse gosses enfant enfants disparu disparue "
     "cherche recherche fugue retrouve fille fils", "enfant perdu recherche"),
    ("feu fumee fumees flamme flammes brule incendie", "incendie feu"),
    ("vole voleur voleurs volee voles pickpocket portefeuille arrache arrachage", "vol"),
    ("colis sac sacs bagage valise abandonne abandonnee suspect suspecte", "colis objet suspect"),
    ("bourre bourree ivre ivres saoul alcool alcoolise alcoolisee drogue drogues stupefiants defonce",
     "ivresse stupefiants"),
    ("drone drones survol survole volant", "drone survol"),
    ("talkie talkies gilet gilets materiel logistique livraison manque", "logistique materiel"),
    ("bruit bruits musique tapage sono nuisance nuisances", "nuisances sonores tapage"),
    ("courant electricite electrique disjoncte disjoncteur coupure eclairage lumiere noir",
     "panne electrique"),
    ("fuite fuites inondation inonde canalisation sanitaire sanitaires", "panne sanitaire fuite eau"),
    ("tripode tripodes scanner scanners scan lecteur douchette informatique",
     "controle acces panne tripode scanner"),
    ("faux fraude fraudeur fraudeurs contrefait contrefacon bracelet revendu prete", "fraude"),
    ("bouchon bouchons bouchonne bouchonner embouteillage congestion saturation sature",
     "circulation congestion"),
    ("gare garee garees stationne stationnee stationnement fourriere enlevement",
     "stationnement fourriere"),
    ("cle cles clef clefs autorisation", "demande acces cle autorisation"),
    ("escorte escorter convoi cortege", "escorte convoi"),
    ("accident collision accroche percute renverse renversee", "accident circulation"),
    ("intrus intrusion escalade escalader grimpe grillage cloture force forcer forcee",
     "intrusion acces force"),
    ("tag tags casse cassee casser degrade degradation vandalisme graffiti", "degradation"),
    ("evacuer evacuation evacue", "evacuation"),
    ("barriere barrieres heras balisage rubalise", "barrierage balisage"),
    ("panneau panneaux fleche fleches signaletique", "signaletique"),
    ("filtrage renfort renforts queue attente", "renfort filtrage"),
    ("reglement interdit interdite refus bouteille verre", "non respect reglement"),
    ("sabotage malveillance sectionne coupe coupes cable cables camera cameras",
     "malveillance dispositif"),
    ("ouvrir ouvre ouverture fermer ferme fermeture avancer retarder cadencer cadencement",
     "ouverture fermeture cadencement"),
]


# Mots de LIEU : ils aident, mais une fiche n'est jamais retenue sur eux seuls
# (constate le 08/10/2026 : << un chien errant sur le parking >> donnait la
# fiche Circulation par le seul mot << parking >>).
LIEUX = set("""parking parkings tribune tribunes porte portes village camping campings paddock piste
pistes zone zones entree entrees sortie sorties buvette bar bars stand stands wc toilette toilettes
aire aires route routes rond point rocade site enceinte gradin gradins pelouse fan virage virages
musee boutique boutiques restaurant hospitalite tente parvis allee passerelle tunnel""".split())


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def _racine(w):
    """Racine grossiere (pluriels, feminins, -ement, -ation) : assez pour
    rapprocher << blessee >> de << blessure >> au prefixe."""
    for fin in ("ements", "ement", "ations", "ation", "ees", "ee", "es", "s", "x", "e"):
        if w.endswith(fin) and len(w) - len(fin) >= 4:
            return w[: -len(fin)]
    return w


def _proche(a, b):
    """Meme mot, ou l'un prolonge l'autre (racines de 5 lettres au moins) :
    << bless >> ~ << blessur >>. Pas de simple prefixe commun : << spectateur >>
    ne doit pas valoir << spectacle >>, ni << agression >> << agressivite >>."""
    if a == b:
        return True
    return min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a))


def _mots(texte):
    return [_racine(w) for w in _norm(texte).split() if w not in VIDES and len(w) > 2]


def requete(texte):
    """Racines de la question, enrichies des synonymes de terrain."""
    n = _norm(texte)
    brut = [w for w in n.split() if w not in VIDES and len(w) > 2]
    mots = [_racine(w) for w in brut]
    for decl, ajout in SYNONYMES:
        if set(brut) & set(decl.split()):
            mots += _mots(ajout)
    # Sans doublons proches : << agressee >> + synonyme << agression >> ne
    # doivent pas compter deux fois la meme fiche
    out = []
    for w in mots:
        if not any(_proche(w, o) for o in out):
            out.append(w)
    return out


def _champs(p):
    out = {}
    for k, _ in POIDS:
        v = p.get(k)
        out[k] = " ".join(v) if isinstance(v, list) else str(v or "")
    return {k: set(_mots(v)) for k, v in out.items()}


def publiees(db):
    return list(db[COL].find({"status": "published"}, {"_id": 0}).sort("code", 1))


def identifier(fiches, texte):
    """[(score, fiche)] decroissant, scores > 0. IDF sur les fiches : un mot
    present partout (<< zone >>, << patrouille >>) pese peu."""
    q = requete(texte)
    if not q or not fiches:
        return []
    champs = [_champs(p) for p in fiches]
    N = len(fiches)

    def present(w, ch):
        return any(_proche(w, m) for mots in ch.values() for m in mots)
    idf = {}
    for w in q:
        df = sum(1 for ch in champs if present(w, ch))
        if df:
            idf[w] = math.log(1 + N / df)
    lieux = {_racine(w) for w in LIEUX}
    res = []
    for p, ch in zip(fiches, champs):
        s, fort = 0.0, False
        for w, poids_idf in idf.items():
            best = max((poids for k, poids in POIDS if any(_proche(w, m) for m in ch[k])), default=0)
            if w in lieux:
                best *= 0.25
            elif best >= 1.0:
                fort = True       # un mot de la SITUATION (pas un lieu) dans un champ porteur
            s += best * poids_idf
        if s > 0 and fort:
            res.append((round(s, 2), p))
    res.sort(key=lambda x: -x[0])
    return res


# ---------------------------------------------------------------------------
# Rendus
# ---------------------------------------------------------------------------

def _decisions(flow, prefixe=""):
    """Lignes de decision du logigramme : << Gravite vitale ? oui : ... ; non : ... >>."""
    out = []
    for n in flow or []:
        if n.get("k") == "ask" and n.get("t"):
            out.append("%s%s oui : %s ; non : %s." % (prefixe, n["t"].rstrip(), n.get("y") or "-",
                                                      n.get("n") or "-"))
        elif n.get("k") == "fork" and n.get("branches"):
            labels = [b.get("label") for b in n["branches"] if b.get("label")]
            if labels:
                out.append("%s%s : %s." % (prefixe, (n.get("t") or "Selon le cas").rstrip(" :"),
                                           " / ".join(labels)))
    return out


def lien(p):
    return {"libelle": "Ouvrir la fiche %s" % p["code"], "url": "/wiki#%s" % p["code"]}


def guide(p, identifiee=False):
    l = ["Fiche %s %s." % (p["code"], p.get("titre") or "")]
    if identifiee:
        # Fiche choisie par Cockpit d'apres les mots de l'operateur : il doit
        # pouvoir corriger d'un coup d'oeil
        l[0] += " (Si ce n'est pas la situation : la décrire autrement.)"
    qs = [q for q in p.get("questions") or [] if q]
    if qs:
        l.append("À demander :")
        l += ["%d. %s" % (i, q) for i, q in enumerate(qs, 1)]
    cs = [c for c in p.get("conduite") or [] if c]
    if cs:
        l.append("À faire :")
        l += ["%d. %s" % (i, c) for i, c in enumerate(cs, 1)]
    dec = _decisions(p.get("flow"))
    if dec:
        l.append("Décision : " + " ".join(dec))
    if p.get("consigner"):
        l.append("À consigner : %s" % p["consigner"])
    if p.get("pieges"):
        l.append("Attention : %s" % p["pieges"])
    l.append("Fiche complète : bouton « Ouvrir la fiche %s »." % p["code"])
    return "\n".join(l)


def precision(p, texte):
    """Question de suite sur une fiche (<< et s'il ne respire pas ? >>) : les
    phrases de la fiche les plus proches, mot pour mot. None si rien.
    Les mots de l'operateur seuls, SANS synonymes : ils elargiraient a des
    phrases hors sujet (<< horodate tout >> pour << s'il ne respire pas >>)."""
    q = list(dict.fromkeys(_mots(texte)))
    if not q:
        return None
    phrases = [("details", d) for d in p.get("details") or []]
    phrases += [("decision", d) for d in _decisions(p.get("flow"))]
    phrases += [("conduite", c) for c in p.get("conduite") or []]
    phrases += [("piege", p.get("pieges"))] if p.get("pieges") else []
    notes = []
    for k, ph in phrases:
        mots = set(_mots(ph))
        s = sum(1 for w in q if any(_proche(w, m) for m in mots))
        if s:
            notes.append((s, k, ph))
    if not notes:
        return None
    notes.sort(key=lambda x: -x[0])
    top = notes[0][0]
    return [ph for s, k, ph in notes if s == top][:2]


# ---------------------------------------------------------------------------
# Carroyage
# ---------------------------------------------------------------------------

def carre_demande(texte):
    m = re.search(r"\b([A-Za-z]{1,2})\s?-?\s?(\d{1,3})\b", str(texte or ""))
    return (m.group(1).upper() + str(int(m.group(2)))) if m else None


def t_carre(db, raw):
    ref = carre_demande(raw)
    if not ref:
        return {"disponible": True, "vue": "carre", "trouve": False,
                "resume": "Carré « %s » non compris : une ou deux lettres puis un nombre (ex. P5, AA12)."
                          % str(raw)[:20]}
    d = db["grid_ref"].find_one({"grid_ref": ref}, {"_id": 0})
    if not d or d.get("latitude") is None:
        return {"disponible": True, "vue": "carre", "trouve": False, "carre": ref,
                "resume": "Carré %s absent du carroyage du site (100 m)." % ref}
    lat, lng = float(d["latitude"]), float(d["longitude"])
    liens = []
    if str(d.get("waze_url") or "").startswith("https://ul.waze.com/"):
        liens.append({"libelle": "Itinéraire Waze vers %s" % ref, "url": d["waze_url"]})
    return {"disponible": True, "vue": "carre", "trouve": True, "carre": ref,
            "latitude": round(lat, 6), "longitude": round(lng, 6), "liens": liens,
            "resume": "Carré %s (carroyage 100 m du site) : centre à %.5f, %.5f.%s"
                      % (ref, lat, lng, " Itinéraire : bouton « Itinéraire Waze vers %s »." % ref
                         if liens else "")}


# ---------------------------------------------------------------------------
# Outil
# ---------------------------------------------------------------------------

def t_reference(db, args, ctx=None):
    args = args or {}
    if str(args.get("carre") or "").strip():
        return t_carre(db, args["carre"])
    fiches = publiees(db)
    code = str(args.get("code") or "").strip().upper()
    texte = str(args.get("situation") or "").strip()[:300]
    if not code:
        m = re.search(r"\b([PC])\s?(\d{1,2})\b", texte.upper())
        if m and re.search(r"\b(fiche|procedure)\b", _norm(texte)):
            code = "%s%02d" % (m.group(1), int(m.group(2)))
    if code:
        p = next((f for f in fiches if f.get("code") == code), None)
        if not p:
            return {"disponible": True, "vue": "introuvable", "trouve": False,
                    "resume": "Aucune fiche publiée %s dans le wiki. Ne pas improviser de procédure." % code}
        res = {"disponible": True, "vue": "fiche", "trouve": True, "code": code,
               "titre": p.get("titre"), "liens": [lien(p)]}
        reste = re.sub(r"\b(fiche|procedure)\s*%s\b|\b%s\b" % (code, code), " ", texte, flags=re.I).strip()
        ph = precision(p, reste) if reste and requete(reste) else None
        if ph:
            res["vue"] = "precision"
            res["resume"] = ("Sur la fiche %s %s : %s\nFiche complète : bouton « Ouvrir la fiche %s »."
                             % (code, p.get("titre"), " ".join(ph), code))
        else:
            res["resume"] = guide(p)
        return res
    if not texte:
        return {"disponible": True, "vue": "sommaire", "trouve": False,
                "resume": "Préciser la situation (ce qui se passe, en mots simples). Fiches publiées : "
                          + ", ".join("%s %s" % (f["code"], f.get("titre")) for f in fiches) + "."}
    scores = identifier(fiches, texte)
    if not scores or scores[0][0] < SEUIL_MIN:
        proches = [p for s, p in scores[:3] if s >= SEUIL_MIN / 2]
        # Repli : la liste FERMEE des fiches, pour que le modele choisisse
        # (rapprocher << se tapent dessus >> de << rixe >> est son point fort)
        # puis rappelle l'outil avec le code ; le contenu reste celui de la fiche.
        return {"disponible": True, "vue": "aucune", "trouve": False,
                "candidats": [p["code"] for p in proches],
                "catalogue": catalogue(fiches),
                "consigne": ("Si UNE fiche du catalogue correspond clairement a la situation decrite, "
                             "rappeler cockpit_reference avec code=<son code> et recopier ce resume-la. "
                             "Sinon recopier le resume ci-dessous. Ne jamais rediger de procedure."),
                "resume": ("Aucune fiche réflexe du wiki ne correspond nettement à « %s ». "
                           "Ne pas improviser de procédure." % texte)
                          + (" Fiches les plus proches : %s." % ", ".join(
                              "%s %s" % (p["code"], p.get("titre")) for p in proches) if proches else "")
                          + " Décrire la situation autrement (ce qui se passe, qui, où)."}
    top = scores[0][0]
    proches = [(s, p) for s, p in scores if s >= top * RATIO_AMBIGU][:CANDIDATS_MAX]
    if len(proches) > 1:
        noms = ["%s (%s)" % (p.get("titre"), p["code"]) for s, p in proches]
        l = ["Plusieurs fiches possibles pour « %s »." % texte,
             "Question à poser à l'opérateur : « Est-ce plutôt %s ? »" % (
                 ", ".join(noms[:-1]) + " ou " + noms[-1])]
        for s, p in proches:
            sc = [x for x in p.get("souscas") or [] if x]
            l.append("- %s %s : %s" % (p["code"], p.get("titre"),
                                       (p.get("situation") or "")[:160].rstrip() +
                                       (" Cas : " + ", ".join(sc[:4]) + "." if sc else "")))
        return {"disponible": True, "vue": "ambigu", "trouve": False,
                "candidats": [p["code"] for s, p in proches],
                "liens": [lien(p) for s, p in proches], "resume": "\n".join(l)}
    p = proches[0][1]
    return {"disponible": True, "vue": "fiche", "trouve": True, "code": p["code"],
            "titre": p.get("titre"), "score": top, "liens": [lien(p)],
            "resume": guide(p, identifiee=True)}


def catalogue(fiches):
    """Une ligne par fiche publiee : code, titre, sous-cas (court)."""
    out = []
    for p in fiches:
        sc = [x for x in p.get("souscas") or [] if x][:4]
        ligne = "%s %s%s" % (p["code"], p.get("titre") or "", (" : " + ", ".join(sc)) if sc else "")
        out.append(ligne[:90])   # 30 fiches tiennent sous le plafond d'un resultat
    return out
