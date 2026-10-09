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
Mode dialogue (09/10/2026) : une fiche identifiee porte aussi `fiche`, ses
elements separes (a_demander, a_faire, decisions, precisions...) et une
`consigne` : le modele mene l'echange (ordre, questions deja connues
sautees, une ou deux a la fois) sans rien ajouter qui ne soit dans la fiche.
`resume` (guide complet) reste le repli a recopier en cas de doute.

Seules les fiches PUBLIEES sont lues (un brouillon n'est pas valide).
Lecture seule.
"""

import json
import math
import re
import unicodedata

COL = "cockpit_wiki_procedures"
# Score minimal d'une fiche retenue. 6 (et non 3) d'apres 5 800 vraies phrases
# de main courante : -1,8 pt de bonnes fiches, -6,3 pts de mauvaises, devenues
# << aucune >> (sans danger : le modele recoit alors le catalogue).
SEUIL_MIN = 6.0
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
juste deux trois gars mec mecs type types donner dedans dehors vu voit
demande demandes intervention presence arrivee mise place suite niveau ete securite
""".split())

# Mots de terrain -> mots des fiches. Les declencheurs sont normalises (sans
# accents). Volontairement absents : << wc >>, << toilettes >> (servent a
# LOCALISER : << un homme par terre pres des WC >> n'est pas une fuite).
SYNONYMES = [
    ("bagarre bagarres baston bastons rixe rixes bagarrent battent battre echauffouree embrouille "
     "tapent battent bastonnent castagne", "altercation rixe"),
    # Sans << coups >> : << des coups de pied dans la barriere >> est une degradation
    ("frappe frapper frappee tabasse tabasser agresse agressee agresseur agresser",
     "agression"),
    # Sans << tombe >> ni << chute >> : << un panneau est tombe >> n'est pas un secours
    ("malaise inconscient inconsciente evanoui evanouie blesse blessee blesses blessure respire "
     "respirer saigne saignement cardiaque convulse bouge pale sent vomit vomi douleur",
     "secours victime malaise blessure"),
    # Vraies fiches de main courante (verification du 09/10/2026)
    ("chute chuter entorse fracture cheville genou epaule deboite deboitee luxation tordue plaie "
     "coupure ouvert suicide comate transi samu smur ambulance hypothermie secours pci masculin "
     "feminin vomissement vomissements", "secours victime blessure"),
    ("attouchement attouchements exhibition exhibitionniste denude denudee voyeur sexuel sexuelle",
     "agression comportement sexuel"),
    ("coule coulent bouche bouchee bouches bouchees fosse debordement", "panne sanitaire fuite"),
    ("forcage squat squatte squattee squatteurs", "intrusion acces force"),
    ("table tables chaise chaises", "logistique materiel"),
    ("drift rodeo dangereuse dangereusement", "non respect reglement"),
    ("rupteur rupteurs cone cones run runs burn burns casque casques pocket", "non respect reglement"),
    ("menace menacant menacante menaces marteau couteau machette", "altercation rixe arme"),
    ("escaladent escalade escaladee grimpent enjambe", "intrusion acces"),
    ("perdu perdue egare egaree gamin gamine gosse gosses enfant enfants disparu disparue "
     "cherche recherche fugue retrouve", "enfant perdu recherche"),
    ("feu fumee fumees flamme flammes brule incendie", "incendie feu"),
    # << vol >> seul pese autant pour Drone (<< vol de drone >>) : << objet plainte >> departage
    ("vol vols vole voleur voleurs volee voles pickpocket portefeuille arrache arrachage",
     "vol objet plainte"),
    ("colis sac sacs bagage valise abandonne abandonnee suspect suspecte", "colis objet suspect"),
    ("bourre bourree ivre ivres saoul alcool alcoolise alcoolisee drogue drogues stupefiants defonce",
     "ivresse stupefiants"),
    ("drone drones survol survole volant", "drone survol"),
    ("talkie talkies gilet gilets materiel logistique livraison manque", "logistique materiel"),
    ("bruit bruits musique tapage sono nuisance nuisances", "nuisances sonores tapage"),
    ("courant electricite electrique disjoncte disjoncteur coupure eclairage eclairages lumiere "
     "lumieres noir",
     "panne electrique"),
    # Sans << sanitaires >> : lieu autant que panne (<< odeur de brule dans les sanitaires >> = feu)
    ("fuite fuites inondation inonde canalisation", "panne sanitaire fuite eau"),
    ("tripode tripodes scanner scanners scan lecteur douchette informatique",
     "controle acces panne tripode scanner"),
    ("faux fraude fraudeur fraudeurs contrefait contrefacon bracelet revendu prete", "fraude"),
    ("bouchon bouchons bouchonne bouchonner embouteillage congestion saturation sature",
     "circulation congestion"),
    ("gare garee garees stationne stationnee stationnement fourriere enlevement",
     "stationnement fourriere"),
    # Live 09/10 : << une voiture arretee en pleine voie >> donnait Accident
    ("arrete arretee arretes arretees immobilise immobilisee", "vehicule bloquant stationnement genant"),
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
    # Sans << camera >> : << on a vu a la camera >> (levee de doute) n'est pas
    # un sabotage (live 09/10 : une degradation partait en P04)
    ("sabotage malveillance sectionne sectionnes coupe coupes cable cables", "malveillance dispositif"),
    ("engueulent engueule engueulade dispute disputent grabuge insultent insulte", "altercation rixe"),
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
# Mots d'indice, traites comme les lieux (jamais suffisants seuls) : << une
# odeur de gaz >> donnait Panne sanitaire par le seul mot << odeur >>.
LIEUX |= {"odeur", "odeurs", "probleme", "problemes", "souci", "soucis",
          # Main courante reelle : << Nettoyage poubelle >> donnait Incendie,
          # << Demande d'intervention au Portail 3 >> Intrusion
          "poubelle", "poubelles", "portail", "portails", "portillon", "portillons", "agent", "agents",
          "patrouille", "patrouilles", "groupe", "individu", "individus", "spectateur", "spectateurs"}


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
            # Chaque reponse avec SES actions : << Ne respire pas : Pompiers en
            # plus du CMS, Patrouille : securiser ; Consciente : CMS seul. >>
            cas = []
            for b in n["branches"]:
                if not b.get("label"):
                    continue
                actes = [("Engager " if x.get("k") == "engage" else "") + x["t"].strip()
                         for x in b.get("flow") or [] if x.get("t") and x.get("k") != "fork"]
                cas.append("%s : %s" % (b["label"].strip(), ", ".join(actes)) if actes else b["label"].strip())
            if cas:
                out.append("%s%s %s." % (prefixe, (n.get("t") or "Selon le cas").rstrip(" :"),
                                         " ; ".join(cas)))
    return out


ETAPES = {"act": "", "engage": "Engager : ", "watch": "Surveiller : "}


def _deroule(flow):
    """Le logigramme dans l'ordre (actions, engagements, decisions), sans
    debut ni fin : le modele sait OU en est la fiche, pas seulement quoi
    decider."""
    out = []
    for n in flow or []:
        k, t = n.get("k"), (n.get("t") or "").strip()
        if k in ETAPES and t:
            out.append(ETAPES[k] + t)
        elif k in ("ask", "fork") and t:
            # La question seule : ses reponses et actions sont dans `decisions`
            # (sans doublon, la fiche tient sous le plafond d'un resultat)
            out.append("DECISION (voir decisions) : " + t)
    return out


def _a_la_fin(flow):
    """Etapes a donner quand l'affaire est reglee : celles qui suivent la
    derniere decision du logigramme, et la fin (live 09/10 : << A la
    decouverte, faire raccompagner... >> ne sortait qu'une fois sur deux)."""
    flow = flow or []
    dern = max([i for i, n in enumerate(flow) if n.get("k") in ("ask", "fork")], default=-1)
    out = []
    for n in flow[dern + 1:]:
        k, t = n.get("k"), (n.get("t") or "").strip()
        if t and (k in ETAPES or k == "end"):
            out.append(t)
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
    # Pas de ligne << bouton Ouvrir la fiche >> : le widget l'affiche deja
    return "\n".join(l)


PRECISIONS_MAX_CHARS = 1500
TAILLE_MAX = 6700             # marge sous le plafond de l'outil (alfred_tools : max_chars 7000)

CONSIGNE_DIALOGUE = (
    "Mener l'echange avec l'operateur a partir du champ fiche, et de lui SEUL. "
    "1) Nommer la fiche en premier (<< Fiche P01 Secours a victime >>) pour qu'il puisse corriger. "
    "2) Ne pas redemander ce qu'il a deja dit EXPLICITEMENT (<< tribune 13 >> : le lieu est connu). "
    "Un indice n'est pas une reponse : << il bouge plus >> ne dit pas s'il respire, la question "
    "reste a poser. "
    "3) Une action urgente de a_faire se dit tout de suite, avant les questions. "
    "Ne JAMAIS trancher une decision (oui / non, une branche) par deduction : seulement si les mots "
    "de l'operateur repondent a la question de la decision ; sinon poser cette question. "
    "4) Poser au plus deux questions a la fois : d'abord la question d'une DECISION du deroule "
    "encore sans reponse, puis celles de a_demander. "
    "5) A sa reponse, donner l'action ou la decision correspondante de la fiche ; si la fiche n'est "
    "plus dans la conversation, rappeler cockpit_reference avec code + situation = sa reponse. "
    "6) Phrases courtes, style radio, reformulation libre, mais AUCUN moyen, service, numero, delai "
    "ou action qui ne figure pas dans la fiche. 7) Situation traitee : donner les etapes de a_la_fin, "
    "puis rappeler a_consigner et attention. "
    "Le bouton << Ouvrir la fiche >> est ajoute par Cockpit, ne pas ecrire de lien. "
    "En cas de doute : recopier resume tel quel.")


def structure(p):
    """La fiche en elements separes, pour que le modele mene l'echange
    (ordre, questions deja connues sautees) sans rien inventer."""
    details, n = [], 0
    for d in p.get("details") or []:
        if d and n + len(d) <= PRECISIONS_MAX_CHARS:
            details.append(d)
            n += len(d)
    return {"code": p["code"], "titre": p.get("titre") or "",
            "situation": p.get("situation") or "",
            "sous_cas": [x for x in p.get("souscas") or [] if x],
            "a_demander": [q for q in p.get("questions") or [] if q],
            "a_faire": [c for c in p.get("conduite") or [] if c],
            "decisions": _decisions(p.get("flow")),
            "deroule": _deroule(p.get("flow")),
            "a_la_fin": _a_la_fin(p.get("flow")),
            # Les services que la fiche autorise a engager (et a quelle condition)
            "acteurs": str(p.get("acteurs") or ""),
            "precisions": details,
            "a_consigner": p.get("consigner") or "",
            "attention": p.get("pieges") or ""}


def _dialogue(res, p, identifiee=False):
    res.update({"mode": "dialogue", "fiche": structure(p), "consigne": CONSIGNE_DIALOGUE,
                "identifiee_par_cockpit": identifiee})
    # Sous le plafond d'un resultat (alfred_tools._borner retirerait la fiche
    # entiere) : les precisions cedent d'abord, une a une
    while len(json.dumps(res, ensure_ascii=False)) > TAILLE_MAX and res["fiche"]["precisions"]:
        res["fiche"]["precisions"].pop()
    return res


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
            "resume": "Carré %s (carroyage 100 m du site) : centre à %.5f, %.5f." % (ref, lat, lng)}


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
            res["passages"] = ph
            res["resume"] = "Sur la fiche %s %s : %s" % (code, p.get("titre"), " ".join(ph))
        else:
            res["resume"] = guide(p)
        return _dialogue(res, p)
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
    return _dialogue({"disponible": True, "vue": "fiche", "trouve": True, "code": p["code"],
                      "titre": p.get("titre"), "score": top, "liens": [lien(p)],
                      "resume": guide(p, identifiee=True)}, p, identifiee=True)


def catalogue(fiches):
    """Une ligne par fiche publiee : code, titre, sous-cas (court)."""
    out = []
    for p in fiches:
        sc = [x for x in p.get("souscas") or [] if x][:4]
        ligne = "%s %s%s" % (p["code"], p.get("titre") or "", (" : " + ", ".join(sc)) if sc else "")
        out.append(ligne[:90])   # 30 fiches tiennent sous le plafond d'un resultat
    return out
