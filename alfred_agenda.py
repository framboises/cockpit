"""Outil Alfred `cockpit_agenda` : ce qui est prevu, d'apres la timeline de
Cockpit (collection `timetable`, la meme que l'ecran). Remplace
`cockpit_timeline`.

La timeline melange trois sources, toutes rendues ici :
- parametrage : montage, badges, ouvertures et fermetures des lieux
  (factorisees comme a l'ecran : << Ouverture parkings (x12) >>) ;
- Momentus : reservations du site (seminaires, roulages, visites), surtout
  dans le document SAISON ;
- voisins : matchs et spectacles d'Antares, du MMArena (arrivees du public).

Ecarts voulus avec l'ancien outil (pcorg_summary.get_upcoming_timetable) :
une vignette sans heure de debut n'est plus perdue (fermeture = heure de
fin, reservation a la journee = << journee >>), et une vignette en cours
(commencee, pas finie) est rendue avec << en cours >>.

Les horaires d'UN lieu se demandent a cockpit_lieux (plages par public) ;
cet outil dit ce qui se passe et quand. Lecture seule.
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo("Europe/Paris")
FENETRE_DEFAUT_H = 12
RECHERCHE_JOURS = 60
LISTE_MAX = 40
RESUME_MAX = 3800
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
        "septembre", "octobre", "novembre", "decembre"]

FAMILLES = [
    ("voisins", ("voisin", "voisins", "antares", "match", "matchs", "concert", "concerts",
                 "spectacle", "spectacles", "msb", "mmarena", "basket", "foot", "football",
                 "fc", "stade", "domicile")),
    ("momentus", ("reservation", "reservations", "seminaire", "seminaires", "momentus",
                  "location", "locations", "privatisation", "roulage", "roulages", "client", "clients")),
    ("ouvertures", ("ouverture", "ouvertures", "fermeture", "fermetures")),
]
LIBELLE_FAMILLE = {"voisins": "événements voisins", "momentus": "réservations Momentus",
                   "ouvertures": "ouvertures et fermetures"}


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


# ---------------------------------------------------------------------------
# Fenetre de temps en mots d'operateur
# ---------------------------------------------------------------------------

def _minuit(p):
    return p.replace(hour=0, minute=0, second=0, microsecond=0)


def _jour_cite(n, p):
    """Date (minuit Paris) citee dans le texte, ou None."""
    m0 = _minuit(p)
    if re.search(r"\bapres demain\b", n):
        return m0 + timedelta(days=2)
    if re.search(r"\bdemain\b", n):
        return m0 + timedelta(days=1)
    if re.search(r"\b(aujourd hui|aujourdhui|ce jour)\b", n):
        return m0
    m = re.search(r"\b(20\d\d)[ -](\d\d)[ -](\d\d)\b", n)
    if m:
        try:
            return m0.replace(year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2}) (\d{1,2})(?: (20\d\d))?\b", n)
    if m and 1 <= int(m.group(2)) <= 12:
        try:
            d = m0.replace(month=int(m.group(2)), day=int(m.group(1)),
                           year=int(m.group(3)) if m.group(3) else p.year)
            return d if m.group(3) or d >= m0 - timedelta(days=60) else d.replace(year=d.year + 1)
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2}) (%s)\b" % "|".join(MOIS), n)
    if m:
        try:
            d = m0.replace(month=MOIS.index(m.group(2)) + 1, day=int(m.group(1)))
            return d if d >= m0 - timedelta(days=60) else d.replace(year=d.year + 1)
        except ValueError:
            return None
    for i, j in enumerate(JOURS):
        if re.search(r"\b%s\b" % j, n):
            ecart = (i - p.weekday()) % 7
            if "prochain" in n and ecart == 0:
                ecart = 7
            return m0 + timedelta(days=ecart)
    return None


def fenetre(raw, now):
    """(debut, fin, libelle) en heure de Paris, ou None si rien n'est compris."""
    p = now.astimezone(TZ_PARIS)
    n = _norm(raw)
    if not n:
        return None
    m = re.search(r"\b(\d{1,2})\s*(h|heure|heures)\b", n)
    if m and re.search(r"\b(prochaine|prochaines|dans|next)\b", n):
        h = max(1, min(int(m.group(1)), 72))
        return p, p + timedelta(hours=h), "prochaines %d heures" % h
    if re.search(r"\b(week end|weekend|we)\b", n):
        sam = _minuit(p) + timedelta(days=(5 - p.weekday()) % 7)
        if p.weekday() == 6:
            sam = _minuit(p) - timedelta(days=1)
        debut = max(sam, p) if sam <= p else sam
        return debut, sam + timedelta(days=2), "ce week-end"
    if re.search(r"\b(cette semaine|7 jours|semaine)\b", n):
        return p, p + timedelta(days=7), "7 prochains jours"
    jour = _jour_cite(n, p)
    base = jour if jour is not None else _minuit(p)
    for mot, h1, h2, lib in (("matin", 6, 12, "matin"), ("apres midi", 12, 18, "après-midi"),
                             ("aprem", 12, 18, "après-midi"), ("soir", 18, 24, "soir"),
                             ("nuit", 20, 32, "nuit")):
        if re.search(r"\b%s\b" % mot, n):
            debut, fin = base + timedelta(hours=h1), base + timedelta(hours=h2)
            if jour is None and fin <= p:
                return None
            return max(debut, p) if jour is None else debut, fin, _libelle_jour(base, p) + " " + lib
    if jour is not None:
        return jour, jour + timedelta(days=1), _libelle_jour(jour, p)
    return None


def _libelle_jour(d, p):
    ecart = (d.date() - p.date()).days
    nom = "%s %s" % (JOURS[d.weekday()], d.strftime("%d/%m"))
    return {0: "aujourd'hui (%s)" % nom, 1: "demain (%s)" % nom}.get(ecart, nom)


# ---------------------------------------------------------------------------
# Vignettes
# ---------------------------------------------------------------------------

def _hm(date_str, s):
    s = str(s or "").strip()
    if not s or s.upper() == "TBC":
        return None
    try:
        return datetime.strptime("%s %s" % (date_str, s), "%Y-%m-%d %H:%M").replace(tzinfo=TZ_PARIS)
    except ValueError:
        return None


def _famille(it):
    o = it.get("origin")
    if o in ("voisins", "momentus"):
        return o
    if it.get("phase") in ("open", "close") or re.match(r"^(Ouverture|Fermeture)\b", str(it.get("activity") or "")):
        return "ouvertures"
    return "programme"


def _vignettes(doc, debut, fin):
    """Vignettes du doc qui touchent [debut, fin] (Paris)."""
    out = []
    data = (doc or {}).get("data") or {}
    if not isinstance(data, dict):
        return out
    d0, d1 = (debut - timedelta(days=1)).strftime("%Y-%m-%d"), fin.strftime("%Y-%m-%d")
    for date_str, items in data.items():
        if not isinstance(items, list) or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date_str)):
            continue
        if not (d0 <= date_str <= d1):
            continue
        for it in items:
            if not isinstance(it, dict) or not (it.get("activity") or "").strip():
                continue
            s, e = _hm(date_str, it.get("start")), _hm(date_str, it.get("end"))
            journee = False
            if s is None and e is None:
                s = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ_PARIS)
                e, journee = s + timedelta(days=1), True
            elif s is None:            # fermeture : seulement une heure de fin
                s = e
            elif e is None or e < s:
                e = s if e is None else e + timedelta(days=1)
            # Fini avant la fenetre (une vignette << journee >> de la veille finit
            # a minuit pile : elle n'est pas << en cours >> le lendemain)
            if s >= fin or (e <= debut and not (e == s == debut)):
                continue
            rem = str(it.get("remark") or "")
            out.append({
                "evenement": doc.get("event"),
                "datetime": s.isoformat(), "date": s.strftime("%Y-%m-%d"),
                "debut": None if journee else s.strftime("%H:%M"),
                "fin": None if journee or e == s or not it.get("end") else (
                    e.strftime("%H:%M") + (" le lendemain" if e.date() > s.date() else "")),
                "journee": journee, "en_cours": s < debut <= e and e > s,
                "activity": str(it.get("activity")).strip(), "place": str(it.get("place") or "").strip(),
                "department": str(it.get("department") or "").strip(),
                "famille": _famille(it),
                "option": it.get("momentus_status") == "option" or "| option |" in rem,
                "remarque": rem if it.get("origin") == "parametrage" else "",
                "_vid": it.get("voisins_id"), "_role": it.get("voisins_role"),
                "_t": s.timestamp(),
            })
    return out


def _regrouper_momentus(vs):
    """Une reservation Momentus = une ligne par jour (le client et ses
    creneaux), pas une ligne par salle et par creneau."""
    out, groupes = [], {}
    for v in vs:
        if v["famille"] != "momentus":
            out.append(v)
            continue
        k = (v["date"], v["activity"], v["department"], v["evenement"])
        g = groupes.get(k)
        if g is None:
            g = groupes[k] = dict(v, _lieux=[v["place"]] if v["place"] else [])
            out.append(g)
            continue
        if v["place"] and v["place"] not in g["_lieux"]:
            g["_lieux"].append(v["place"])
        if v["journee"]:
            g["journee"] = True
        if v["debut"] and (not g["debut"] or v["debut"] < g["debut"]):
            g["debut"] = v["debut"]
        if v.get("fin") and (not g.get("fin") or v["fin"] > g["fin"]):
            g["fin"] = v["fin"]
        g["en_cours"] = g["en_cours"] or v["en_cours"]
        g["option"] = g["option"] and v["option"]
        g["_t"] = min(g["_t"], v["_t"])
    for g in groupes.values():
        l = g.pop("_lieux")
        g["place"] = ", ".join(l[:4]) + (" (+%d)" % (len(l) - 4) if len(l) > 4 else "")
    return out


def _regrouper_voisins(vs):
    """Arrivees / evenement / sortie d'un meme evenement voisin : une ligne,
    avec la plage de presence du public (utile pour les flux)."""
    out, groupes = [], {}
    for v in vs:
        if v["famille"] != "voisins" or not v.get("_vid"):
            out.append(v)
            continue
        groupes.setdefault(v["_vid"], []).append(v)
        if len(groupes[v["_vid"]]) == 1:
            out.append(("g", v["_vid"]))
    res = []
    for x in out:
        if not (isinstance(x, tuple) and x[0] == "g"):
            res.append(x)
            continue
        g = groupes[x[1]]
        main = next((v for v in g if v.get("_role") not in ("arrivees", "sortie")), g[0])
        m = dict(main)
        arr = [v for v in g if v.get("_role") == "arrivees"]
        sor = [v for v in g if v.get("_role") == "sortie"]
        extra = []
        if arr and arr[0]["debut"]:
            extra.append("arrivées du public dès %s" % arr[0]["debut"])
        if sor and (sor[0].get("fin") or sor[0]["debut"]):
            extra.append("sortie jusqu'à %s" % (sor[0].get("fin") or sor[0]["debut"]))
        if extra:
            m["activity"] = m["activity"] + " (" + ", ".join(extra) + ")"
        m["_t"] = min(v["_t"] for v in g)
        m["en_cours"] = any(v["en_cours"] for v in g)
        res.append(m)
    return res


VIDES = {"le", "la", "les", "de", "du", "des", "un", "une", "a", "au", "aux", "sur", "pour",
         "quand", "quel", "quelle", "quels", "quelles", "est", "prochain", "prochaine", "prochains",
         "y", "il", "ya", "apres", "avant", "ce", "cette", "joue", "jouent", "ne", "pas", "encore",
         "en", "et", "ou", "qui", "quoi", "se", "passe"}
# Nature d'un evenement voisin (department de la vignette principale)
SPORTS = ("basket", "football", "sport")
SOUS_VOISINS = [
    (("match", "matchs", "sport"), lambda v: _norm(v["department"]) in SPORTS),
    (("foot", "football", "fc", "stade", "mmarena"), lambda v: _norm(v["department"]) == "football"),
    (("basket", "msb"), lambda v: _norm(v["department"]) == "basket"),
    (("concert", "concerts", "spectacle", "spectacles"),
     lambda v: _norm(v["department"]) not in SPORTS),
    (("antares",), lambda v: "antares" in _norm(v["place"])),
]
SOUS_MOMENTUS = [
    (("seminaire", "seminaires"), "seminaire"),
    (("roulage", "roulages"), "roulage"),
]
MOTS_OPTION = ("option", "options", "confirme", "confirmee", "confirmes", "confirmees")


def _contient(w, t):
    """Mot (ou son singulier) au DEBUT d'un mot du texte : << montage >> ne
    prend pas << demontage >> (constate le 08/10/2026 sur les 24h autos)."""
    if re.search(r"\b" + re.escape(w), t):
        return True
    return len(w) > 3 and w.endswith("s") and re.search(r"\b" + re.escape(w[:-1]), t) is not None


def _filtrer(vs, quoi):
    """(vignettes, libelle) : famille (match, seminaire, ouvertures) ou mots."""
    n = _norm(quoi)
    if not n:
        return vs, None
    mots = set(n.split())
    if mots & set(MOTS_OPTION):
        # << non confirmees >> : les options Momentus, quel que soit le reste
        return [v for v in vs if v["famille"] == "momentus" and v["option"]], \
            "réservations non confirmées (option)"
    for fam, cles in FAMILLES:
        if not mots & set(cles):
            continue
        r = [v for v in vs if v["famille"] == fam]
        if fam == "voisins":
            # Filtre sur la vignette principale, puis on garde tout le groupe
            # (arrivees et sortie du public) de chaque evenement retenu
            mains = [v for v in r if v.get("_role") not in ("arrivees", "sortie")]
            for cles_s, pred in SOUS_VOISINS:
                if mots & set(cles_s):
                    mains = [v for v in mains if pred(v)]
            reste = [w for w in mots - set(cles) - VIDES - {"mans"} if len(w) > 2]
            if reste:
                mains = [v for v in mains if any(_contient(w, _norm(v["activity"])) for w in reste)] or mains
            ids, garde = {v.get("_vid") for v in mains}, {id(v) for v in mains}
            r = [v for v in r if id(v) in garde or (v.get("_vid") and v.get("_vid") in ids)]
            return r, LIBELLE_FAMILLE[fam]
        lib = LIBELLE_FAMILLE[fam]
        if fam == "momentus":
            for cles_s, dep in SOUS_MOMENTUS:
                if mots & set(cles_s):
                    r = [v for v in r if dep in _norm(v["department"])]
                    lib = "réservations Momentus (%s)" % dep.replace("seminaire", "séminaires")
        reste = [w for w in mots - set(cles) - VIDES if len(w) > 2]
        if reste:
            r2 = [v for v in r if all(_contient(w, _norm(" ".join((v["activity"], v["place"], v["department"]))))
                                      for w in reste)]
            r = r2 or r
        return r, lib
    mots = [w for w in n.split() if w not in VIDES and len(w) > 1]
    if not mots:
        return vs, None
    def ok(v):
        t = _norm(" ".join((v["activity"], v["place"], v["department"], v.get("remarque") or "")))
        return all(_contient(w, t) for w in mots)
    return [v for v in vs if ok(v)], "« %s »" % quoi


def _ligne(v, plusieurs_ev):
    if v["journee"]:
        h = "journée"
    else:
        h = v["debut"] + ("-" + v["fin"] if v.get("fin") and v["fin"] != v["debut"] else "")
    s = "- %s %s" % (h, v["activity"])
    if v["place"] and _norm(v["place"]) not in _norm(v["activity"]):
        s += ", " + v["place"]
    if v["famille"] == "momentus" and v["department"]:
        s += " (%s)" % v["department"]
    if v["option"]:
        s += " [option, non confirmé]"
    if v["en_cours"]:
        s += " [en cours]"
    if plusieurs_ev and v["evenement"] and _norm(v["evenement"]) != "saison":
        s += " — " + v["evenement"]
    return s


# ---------------------------------------------------------------------------
# Outil
# ---------------------------------------------------------------------------

def _paires(db, args, ctx, now, annee_hint=None):
    import event_courant
    cite = str(args.get("evenement") or "").strip()
    if cite:
        import alfred_evenements
        nom, cands = alfred_evenements.resoudre_nom(db, cite)
        if not nom:
            return None, {"evenement_cite": cite}, cands
        annees = sorted({int(p["year"]) for p in db["parametrages"].find({"event": nom}, {"year": 1})
                         if str(p.get("year") or "").isdigit()})
        m = re.search(r"\b(20\d\d)\b", str(args.get("annee") or "") + " " + cite)
        if m:
            yr = int(m.group(1))
        elif annee_hint in annees:
            yr = annee_hint          # << le 26/09 >> : l'edition de cette date
        else:
            # Prochaine edition pas encore finie (le montage des 24h autos en
            # octobre 2026 = celui de 2027), sinon la plus recente
            auj = now.astimezone(TZ_PARIS).strftime("%Y-%m-%d")
            yr = None
            for y in annees:
                d = _doc(db, nom, y)
                jours = [k for k in ((d or {}).get("data") or {}) if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k))]
                if jours and max(jours) >= auj:
                    yr = y
                    break
            if yr is None:
                # Prochaine edition pas encore construite : la derniere qui a
                # une timeline, signalee comme passee
                avec = [y for y in annees if ((_doc(db, nom, y) or {}).get("data") or {})]
                yr = (avec or annees or [now.year])[-1]
                if avec:
                    return [(nom, yr)], {"evenement": nom, "annee": yr, "edition_passee": True}, []
        return [(nom, yr)], {"evenement": nom, "annee": yr}, []
    paires = []
    ev, yr = (ctx or {}).get("event"), (ctx or {}).get("year")
    if ev and str(yr or "").isdigit():
        paires.append((ev, int(yr)))
    for p in event_courant.active_pairs(db):
        if (p[0], int(p[1])) not in paires:
            paires.append((p[0], int(p[1])))
    return paires, {}, []


def _doc(db, ev, yr):
    col = db["timetable"]
    return col.find_one({"event": ev, "year": str(yr)}) or col.find_one({"event": ev, "year": int(yr)})


def t_agenda(db, args, ctx, now=None):
    args = args or {}
    now = now or datetime.now(timezone.utc)
    p = now.astimezone(TZ_PARIS)
    import pcorg_summary as PS

    f = fenetre(args.get("quand"), now)
    paires, resolu, cands = _paires(db, args, ctx, now, annee_hint=f[0].year if f else None)
    # << Le Mans FC joue quand >> : le modele passe << Le Mans >> en evenement
    # (ambigu : LE MANS CLASSIC, LE MANS FC - X...) ; c'est une question sur
    # les evenements voisins (constate le 08/10/2026)
    if paires is None and set(_norm("%s %s" % (args.get("evenement"), args.get("quoi"))).split()) \
            & set(dict(FAMILLES)["voisins"]):
        args = dict(args, quoi=("%s %s" % (args.get("evenement"), args.get("quoi") or "")).strip(),
                    evenement="")
        paires, resolu, cands = _paires(db, args, ctx, now, annee_hint=f[0].year if f else None)
    if paires is None:
        return {"disponible": True, "vue": "evenement_ambigu", "candidats": cands, "resolu": resolu,
                "resume": ("Événement « %s » non reconnu." % resolu.get("evenement_cite")) +
                          (" Candidats : " + ", ".join(cands) + ". Demander lequel." if cands else "")}
    quoi = str(args.get("quoi") or "").strip()[:80]
    recherche = False
    if f is None:
        if args.get("quand"):
            resolu["quand_non_compris"] = str(args["quand"])[:60]
        if quoi or resolu.get("evenement"):
            # << quand est le prochain match >> : on cherche loin devant
            # Evenement cite : toute son edition a venir (montage des 24h autos en mai)
            jours = 400 if resolu.get("evenement") else RECHERCHE_JOURS
            f, recherche = (p, p + timedelta(days=jours), "prochaines occurrences"), True
            if resolu.get("edition_passee"):
                ds = sorted(k for k in (_doc(db, *paires[0]).get("data") or {})
                            if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)))
                d0 = datetime.strptime(ds[0], "%Y-%m-%d").replace(tzinfo=TZ_PARIS)
                d1 = datetime.strptime(ds[-1], "%Y-%m-%d").replace(tzinfo=TZ_PARIS) + timedelta(days=1)
                f = (d0, d1, "édition %s (passée : la suivante n'a pas encore de timeline)" % paires[0][1])
        else:
            f = (p, p + timedelta(hours=FENETRE_DEFAUT_H), "prochaines %d heures" % FENETRE_DEFAUT_H)
    debut, fin, libelle = f
    resolu.update({"fenetre": libelle, "perimetre": " + ".join("%s %s" % x for x in paires)})

    vs, sans_doc = [], []
    for ev, yr in paires:
        d = _doc(db, ev, yr)
        if d:
            vs += _vignettes(d, debut, fin)
        else:
            sans_doc.append("%s %s" % (ev, yr))
    vs, filtre = _filtrer(vs, quoi)
    if filtre:
        resolu["filtre"] = filtre
    vs = _regrouper_voisins(_regrouper_momentus(sorted(vs, key=lambda v: (v["_t"], v["activity"]))))
    vs.sort(key=lambda v: (v["date"], not v["journee"], v["_t"], v["activity"]))
    # Factorisation des ouvertures/fermetures simultanees, comme l'ecran
    vs = PS._factorize_upcoming(vs)
    for v in vs:
        for k in ("_t", "_vid", "_role"):
            v.pop(k, None)
    if recherche:
        vs = vs[:10]

    entete = "Agenda %s, %s" % (resolu["perimetre"], libelle)
    if filtre:
        entete += ", " + filtre
    l = [entete + " : %d vignette(s)." % len(vs)]
    if resolu.get("quand_non_compris"):
        l.append("Moment « %s » non compris : %s." % (resolu["quand_non_compris"], libelle))
    if sans_doc:
        l.append("Pas de timeline pour %s." % ", ".join(sans_doc))
    if vs:
        rep = {}
        for v in vs:
            rep[v["famille"]] = rep.get(v["famille"], 0) + 1
        noms = {"programme": "programme", "ouvertures": "ouvertures/fermetures",
                "momentus": "réservations Momentus", "voisins": "événements voisins"}
        l.append("Dont : " + ", ".join("%s %d" % (noms[k], n) for k, n in rep.items()) + ".")
        plusieurs = len({v["evenement"] for v in vs}) > 1
        if recherche:
            v0 = vs[0]
            dj = datetime.strptime(v0["date"], "%Y-%m-%d").replace(tzinfo=TZ_PARIS)
            l.append("Prochaine occurrence : %s %s" % (_libelle_jour(dj, p), _ligne(v0, plusieurs)[2:]))
        # Fenetre courte (ce soir, prochaines heures) : les reservations a la
        # journee, deja en cours, tiennent sur une ligne par jour ; le detail
        # noyait les creneaux horaires (constate le 08/10/2026)
        courte = not recherche and (fin - debut) <= timedelta(hours=FENETRE_DEFAUT_H)
        jour_courant = None
        for v in vs[:LISTE_MAX]:
            if v["date"] != jour_courant:
                jour_courant = v["date"]
                dj = datetime.strptime(jour_courant, "%Y-%m-%d").replace(tzinfo=TZ_PARIS)
                l.append(_libelle_jour(dj, p)[:1].upper() + _libelle_jour(dj, p)[1:] + " :")
                if courte:
                    js = [x for x in vs[:LISTE_MAX] if x["date"] == jour_courant and x["journee"]]
                    if js:
                        l.append("- toute la journée : " + ", ".join(dict.fromkeys(x["activity"] for x in js)))
            if courte and v["journee"]:
                continue
            l.append(_ligne(v, plusieurs))
        if len(vs) > LISTE_MAX:
            l.append("... et %d autre(s) vignette(s) (préciser un moment ou un sujet)." % (len(vs) - LISTE_MAX))
    elif recherche:
        l.append("Rien de prévu dans les %d prochains jours pour ce sujet." % RECHERCHE_JOURS)
    else:
        l.append("Rien de prévu sur ce créneau dans la timeline.")
    resume = "\n".join(l)
    if len(resume) > RESUME_MAX:
        resume = resume[: RESUME_MAX - 40].rsplit("\n", 1)[0] + "\n[liste coupée]"
    return {"disponible": True, "vue": "recherche" if recherche else "fenetre", "resolu": resolu,
            "vignettes": [{k: v.get(k) for k in ("date", "debut", "fin", "journee", "en_cours", "activity",
                                                  "place", "famille", "option", "evenement")}
                          for v in vs[:LISTE_MAX]],
            "resume": resume}
