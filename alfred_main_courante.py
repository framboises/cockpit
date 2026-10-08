"""Outil Alfred `cockpit_main_courante` : la main courante PC Organisation en
un seul outil (remplace cockpit_main_courante_fiches / _fiche / _compteurs).

Un petit modele local choisit mal entre trois outils voisins et enchaine mal
compteurs puis liste puis detail. Un seul outil, des parametres en langage
d'operateur traduits ICI, et un `resume` pret a recopier :

- sans parametre : POINT DE SITUATION. Fiches en cours (les plus urgentes,
  puis la derniere activite), compteurs du jour et de la derniere heure ;
- `fiche` : detail d'une fiche et sa chronologie ;
- `periode` (cette nuit, depuis 2 h, hier...) : ce qui a ete ouvert dans la
  periode, closes comprises ;
- `categorie`, `urgence`, `texte`, `statut` : filtres.

Perimetre et droits : ceux du jeton de portee (ctx), appliques par
alfred_tools._pcorg_filter, exactement comme les anciens outils. Lecture seule.

Donnees personnelles : telephones, e-mails et immatriculations sont masques
dans tout texte rendu (le chat ne sert pas de reperoire, et les plaques
relevent de la LAPI, exclue du chat).
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo("Europe/Paris")
STATUT_CLOS = 10
LISTE_MAX = 12
LISTE_DATA_MAX = 25
ANCIENNE_JOURS = 30
RESUME_MAX = 3800

# Mot de l'operateur -> libelle de categorie (PCO.<x> et PCS.<x>)
CATEGORIES = [
    (("secours", "medical", "medicale", "blesse", "malaise", "sap"), "Secours"),
    (("securite", "secu", "incendie"), "Securite"),
    (("surete", "vigile", "vigiles", "filtrage"), "Surete"),
    (("technique", "tech", "electricite", "electrique", "panne"), "Technique"),
    (("flux", "circulation", "trafic", "parking", "parkings", "stationnement"), "Flux"),
    (("information", "info", "infos", "renseignement"), "Information"),
    (("fourriere", "enlevement", "enlevements"), "Fourriere"),
    (("maincourante", "main courante"), "MainCourante"),
]
LIBELLES_CAT = {"Securite": "Sécurité", "Surete": "Sûreté", "Fourriere": "Fourrière",
                "MainCourante": "Main courante"}
URGENCES = {"EU": "détresse vitale", "UA": "urgence absolue",
            "UR": "urgence relative", "IMP": "impliqué"}
RANG_URGENCE = {"EU": 0, "UA": 1, "UR": 2, "IMP": 3}


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def _utc(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _naif(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _hm(dt, now):
    u = _utc(dt)
    if u is None:
        return None
    p, n = u.astimezone(TZ_PARIS), now.astimezone(TZ_PARIS)
    return p.strftime("%H:%M") if p.date() == n.date() else p.strftime("%d/%m %H:%M")


# ---------------------------------------------------------------------------
# Masquage
# ---------------------------------------------------------------------------

_RX_TEL = re.compile(r"(?<!\d)(?:\+33\s?|0033\s?|0)[1-9](?:[\s.\-]?\d{2}){4}(?!\d)")
_RX_MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_RX_PLAQUE = re.compile(r"\b[A-HJ-NP-TV-Z]{2}[\s-]?\d{3}[\s-]?[A-HJ-NP-TV-Z]{2}\b")


def masquer(texte):
    t = _RX_MAIL.sub("[e-mail]", str(texte or ""))
    t = _RX_TEL.sub("[tél.]", t)
    return _RX_PLAQUE.sub("[immat.]", t)


def _court(texte, n):
    t = re.sub(r"\s+", " ", masquer(texte)).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Parametres en langage operateur
# ---------------------------------------------------------------------------

def categorie_demandee(raw):
    """Libelle (Secours, Surete...) ou None ; '' si un mot est donne mais inconnu."""
    if not raw:
        return None
    n = _norm(raw)
    for mots, lib in CATEGORIES:
        if any(re.search(r"\b%s\b" % re.escape(m), n) for m in mots):
            return lib
    return ""


def urgences_demandees(raw):
    if not raw:
        return None
    n = _norm(raw)
    codes = [c for c in ("EU", "UA", "UR", "IMP") if re.search(r"\b%s\b" % c.lower(), n)]
    if "vital" in n:
        codes.append("EU")
    if "absolu" in n:
        codes.append("UA")
    if "relativ" in n:
        codes.append("UR")
    if "implique" in n:
        codes.append("IMP")
    if not codes and re.search(r"\burgen", n):
        codes = ["EU", "UA"]
    return sorted(set(codes), key=lambda c: RANG_URGENCE[c]) or None


def periode_demandee(raw, now):
    """(debut, fin, libelle) en UTC conscient, ou None."""
    if not raw:
        return None
    n = _norm(raw)
    p = now.astimezone(TZ_PARIS)
    minuit = p.replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:dernieres?\s*)?(h|heure|heures|min|minute|minutes)\b", n)
    if m and ("depuis" in n or "dernier" in n or "derniere" in n or n.startswith(m.group(0))):
        v = float(m.group(1).replace(",", "."))
        d = timedelta(minutes=v) if m.group(2).startswith("min") else timedelta(hours=v)
        return now - d, now, "depuis %s" % m.group(0)
    if re.search(r"\b(derniere heure|une heure|1 heure)\b", n):
        return now - timedelta(hours=1), now, "dernière heure"
    if "nuit" in n:
        hier_soir = (minuit - timedelta(days=1)).replace(hour=20)
        fin = minuit.replace(hour=8)
        if p.hour < 8:
            fin = p
        return hier_soir, fin, "cette nuit (20:00-08:00)"
    if "avant hier" in n:
        return minuit - timedelta(days=2), minuit - timedelta(days=1), "avant-hier"
    if "hier" in n:
        return minuit - timedelta(days=1), minuit, "hier"
    if "matin" in n:
        return minuit.replace(hour=6), min(p, minuit.replace(hour=12)), "ce matin"
    if "apres midi" in n or "aprem" in n:
        return minuit.replace(hour=12), min(p, minuit.replace(hour=18)), "cet après-midi"
    if "soir" in n:
        return minuit.replace(hour=18), p, "ce soir"
    if "aujourd" in n or "journee" in n or "jour" == n:
        return minuit, p, "aujourd'hui"
    if "semaine" in n:
        return now - timedelta(days=7), now, "7 derniers jours"
    return None


def statut_demande(raw):
    n = _norm(raw)
    if not n:
        return None
    if re.search(r"\b(clos|close|closes|fermee|fermees|terminee|terminees|cloturee|cloturees)\b", n):
        return "closes"
    if re.search(r"\b(toute|toutes|tous)\b", n):
        return "toutes"
    return "en_cours"


def numero_fiche(raw):
    s = str(raw or "").strip()
    m = re.search(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b|\b[0-9a-fA-F]{24}\b", s)
    if m:
        return m.group(0)
    m = re.search(r"\d+", s)
    return m.group(0) if m and len(s) < 40 else None


# ---------------------------------------------------------------------------
# Perimetre
# ---------------------------------------------------------------------------

def perimetre(db, args, ctx, now):
    """(paires, resolu, candidats). Evenement cite > selection operateur >
    evenements actifs (comme les anciens outils)."""
    import alfred_tools as AT
    cite = str(args.get("evenement") or "").strip()
    if not cite:
        if (ctx or {}).get("mc_tout"):
            # Admin (jeton signe) : tout le PC, epreuves actives + SAISON
            import event_courant
            return event_courant.active_pairs(db, include_previous_saison=True), {"vue_admin": True}, []
        return AT._event_pairs(db, ctx), {}, []
    import alfred_evenements
    nom, cands = alfred_evenements.resoudre_nom(db, cite)
    if not nom:
        return None, {"evenement_cite": cite}, cands
    annees = sorted({int(p["year"]) for p in db["parametrages"].find({"event": nom}, {"year": 1})
                     if str(p.get("year") or "").isdigit()})
    m = re.search(r"\b(20\d\d)\b", str(args.get("annee") or "") + " " + cite)
    if m:
        yr = int(m.group(1))
    else:
        passees = [y for y in annees if y <= now.year]
        yr = (passees or annees or [now.year])[-1]
    paires = [(nom, yr)]
    if _norm(nom) == "saison":
        paires.append((nom, yr - 1))
    return paires, {"evenement": nom, "annee": yr}, []


def _filtre(db, ctx, paires):
    import event_courant
    cat_q = (ctx or {}).get("cat_query") or {"$regex": "^PC[OS]\\."}
    return {"$and": [event_courant.pairs_filter(paires), {"category": cat_q}]}


# ---------------------------------------------------------------------------
# Rendu
# ---------------------------------------------------------------------------

def _cat(doc):
    c = str(doc.get("category") or "")
    lib = c.split(".", 1)[1] if "." in c else c
    return LIBELLES_CAT.get(lib, lib)


def _activite(doc):
    ts = [_utc(h.get("ts")) for h in doc.get("comment_history") or [] if isinstance(h, dict)]
    ts = [t for t in ts + [_utc(doc.get("ts"))] if t]
    return max(ts) if ts else None


_RX_STATUT = re.compile(r"^\s*Statut\s*:[^\n]*(?:\n|$)", re.I)


def _texte_action(h):
    """Texte libre d'une action, sans la ligne de statut Prysm."""
    return _RX_STATUT.sub("", str(h.get("text") or "")).strip()


def _derniere_action(doc):
    for h in reversed([h for h in doc.get("comment_history") or [] if isinstance(h, dict)]):
        if _texte_action(h):
            return h
    return None


def fiche_courte(doc, now):
    cc = doc.get("content_category") or {}
    der = _derniere_action(doc)
    nature = cc.get("sous_classification") or cc.get("classification")
    if nature and _norm(nature) in ("intervention " + _norm(_cat(doc)), _norm(_cat(doc))):
        nature = None
    texte = _court(doc.get("text_full") or doc.get("text"), 220)
    out = {
        "id": str(doc.get("_id")),
        "numero": doc.get("sql_id"),
        "ouverte": _hm(doc.get("ts"), now),
        "categorie": _cat(doc),
        "urgence": URGENCES.get(doc.get("niveau_urgence")),
        "nature": nature,
        "zone": (doc.get("area") or {}).get("desc"),
        "statut": "close" if doc.get("status_code") == STATUT_CLOS else "en cours",
        "texte": texte,
        "evenement": "%s %s" % (doc.get("event"), doc.get("year")),
    }
    if doc.get("status_code") == STATUT_CLOS:
        out["close"] = _hm(doc.get("close_ts"), now)
    if der:
        ta = _court(_texte_action(der), 160)
        if _norm(ta)[:40] != _norm(texte)[:40]:
            out["derniere_action"] = {"heure": _hm(der.get("ts"), now), "texte": ta}
    return {k: v for k, v in out.items() if v not in (None, "")}


def fiche_detail(doc, now):
    out = fiche_courte(doc, now)
    out.pop("derniere_action", None)
    out["texte"] = _court(doc.get("text_full") or doc.get("text"), 1500)
    out["ouverte_par"] = re.sub(r"\s*\[[^\]]*\]\s*$", "", str(doc.get("operator") or "")) or None
    hist = [h for h in doc.get("comment_history") or []
            if isinstance(h, dict) and (h.get("text") or "").strip()]
    out["nb_actions"] = len(hist)
    out["chronologie"] = [{"heure": _hm(h.get("ts"), now),
                           "par": re.sub(r"\s*\[[^\]]*\]\s*$", "", str(h.get("operator") or "")) or None,
                           "texte": _court(h.get("text"), 400)} for h in hist[-15:]]
    if len(hist) > 15:
        out["actions_anterieures_non_affichees"] = len(hist) - 15
    return {k: v for k, v in out.items() if v not in (None, "")}


def _ligne(f):
    tete = ["n°%s" % f["numero"] if f.get("numero") else "fiche Cockpit sans n°", f.get("ouverte")]
    if f.get("statut") == "close":
        tete.append("close %s" % f.get("close", ""))
    tete += [f.get("categorie"), f.get("urgence"), f.get("zone")]
    s = "- " + ", ".join(x for x in tete if x) + " : " + (f.get("nature") + ". " if f.get("nature") else "") + f.get("texte", "")
    if f.get("derniere_action"):
        a = f["derniere_action"]
        s += " (dernière action %s : %s)" % (a.get("heure") or "?", a.get("texte"))
    return s


def _tri_urgence(doc):
    act = _activite(doc)
    return (RANG_URGENCE.get(doc.get("niveau_urgence"), 9), -(act.timestamp() if act else 0))


def _repartition(docs, cle):
    out = {}
    for d in docs:
        k = cle(d)
        if k:
            out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _txt_rep(rep):
    return ", ".join("%s %d" % kv for kv in rep.items())


def _borne(resume):
    return resume if len(resume) <= RESUME_MAX else resume[: RESUME_MAX - 40].rsplit("\n", 1)[0] + "\n[liste coupée]"


# ---------------------------------------------------------------------------
# Outil
# ---------------------------------------------------------------------------

def t_main_courante(db, args, ctx, now=None):
    args = args or {}
    ctx = ctx or {}
    now = now or datetime.now(timezone.utc)
    import alfred_tools as AT

    # 1. Detail d'une fiche
    num = numero_fiche(args.get("fiche"))
    if args.get("fiche") and not num:
        return {"disponible": True, "vue": "fiche", "trouvee": False,
                "resume": "Numéro de fiche illisible : demander le numéro affiché sur la fiche."}
    if num:
        ors = [{"_id": num}]
        try:
            from bson.objectid import ObjectId
            ors.append({"_id": ObjectId(num)})
        except Exception:
            pass
        if num.isdigit():
            ors += [{"sql_id": int(num)}, {"sql_id": num}]
        doc = db["pcorg"].find_one({"$or": ors}, {"comment": 0, "comment_sql": 0})
        if not doc or not AT._cat_autorisee(ctx, doc.get("category")):
            return {"disponible": True, "vue": "fiche", "trouvee": False,
                    "resolu": {"fiche": num},
                    "resume": "Fiche n°%s introuvable dans le périmètre de l'opérateur." % num}
        f = fiche_detail(doc, now)
        lignes = [_ligne(dict(f, texte=f["texte"]))]
        if f.get("ouverte_par"):
            lignes.append("Ouverte par : %s" % f["ouverte_par"])
        lignes.append("Chronologie (%d action(s)%s) :" % (
            f["nb_actions"], ", 15 dernières" if f.get("actions_anterieures_non_affichees") else ""))
        lignes += ["  %s %s : %s" % (a.get("heure") or "?", "(" + a["par"] + ")" if a.get("par") else "",
                                     a.get("texte")) for a in f["chronologie"]] or ["  aucune action"]
        return {"disponible": True, "vue": "fiche", "trouvee": True, "resolu": {"fiche": num},
                "fiche": f, "resume": _borne("\n".join(lignes))}

    # 2. Perimetre
    paires, resolu, cands = perimetre(db, args, ctx, now)
    if paires is None:
        return {"disponible": True, "vue": "evenement_ambigu", "candidats": cands, "resolu": resolu,
                "resume": ("Événement « %s » non reconnu." % resolu.get("evenement_cite")) +
                          (" Candidats : " + ", ".join(cands) + ". Demander lequel." if cands else "")}
    flt = _filtre(db, ctx, paires)
    libelle_per = " + ".join("%s %s" % p for p in paires)
    resolu["perimetre"] = libelle_per

    # 3. Filtres. Le modele range parfois un mot dans le mauvais champ
    # (urgence: "secours") : on le recupere plutot que de l'ignorer en silence.
    incompris = []
    args = dict(args)
    if args.get("urgence") and not urgences_demandees(args["urgence"]):
        if categorie_demandee(args["urgence"]) and not args.get("categorie"):
            args["categorie"] = args.pop("urgence")
        else:
            incompris.append("urgence « %s »" % str(args.pop("urgence"))[:40])
    cat = categorie_demandee(args.get("categorie"))
    if cat == "":
        return {"disponible": True, "vue": "liste", "fiches": [], "resolu": resolu,
                "resume": "Catégorie « %s » inconnue. Catégories : secours, sécurité, sûreté, "
                          "technique, flux, information, fourrière." % args.get("categorie")}
    if cat:
        flt["$and"].append({"category": {"$regex": "^PC[OS]\\.%s$" % cat}})
        resolu["categorie"] = LIBELLES_CAT.get(cat, cat)
    urg = urgences_demandees(args.get("urgence"))
    if urg:
        flt["$and"].append({"niveau_urgence": {"$in": urg}})
        resolu["urgence"] = urg
    texte = str(args.get("texte") or "").strip()[:80]
    if texte:
        rx = {"$regex": re.escape(texte), "$options": "i"}
        flt["$and"].append({"$or": [{"text": rx}, {"text_full": rx}, {"area.desc": rx},
                                    {"comment": rx}, {"content_category.sous_classification": rx}]})
        resolu["texte"] = texte
    per = periode_demandee(args.get("periode"), now)
    statut = statut_demande(args.get("statut")) or ("toutes" if per else "en_cours")
    if per:
        flt["$and"].append({"ts": {"$gte": _naif(per[0]), "$lt": _naif(per[1])}})
        resolu["periode"] = per[2]
    elif args.get("periode"):
        resolu["periode_non_comprise"] = str(args["periode"])[:60]
        incompris.append("période « %s »" % resolu["periode_non_comprise"])
    if incompris:
        resolu["non_compris"] = incompris
    if statut == "en_cours":
        flt["$and"].append({"status_code": {"$ne": STATUT_CLOS}})
    elif statut == "closes":
        flt["$and"].append({"status_code": STATUT_CLOS})
    resolu["statut"] = statut
    filtre_actif = bool(cat or urg or texte or per or statut != "en_cours")
    vue = "liste" if filtre_actif else "point"
    resolu["vue"] = vue

    docs = list(db["pcorg"].find(flt, {"comment": 0, "comment_sql": 0}).limit(2000))
    heure = now.astimezone(TZ_PARIS).strftime("%d/%m %H:%M")

    # 4. Point de situation (sans filtre)
    if vue == "point":
        limite = now - timedelta(days=ANCIENNE_JOURS)
        recentes = [d for d in docs if (_activite(d) or now) >= limite]
        anciennes = len(docs) - len(recentes)
        recentes.sort(key=_tri_urgence)
        minuit = now.astimezone(TZ_PARIS).replace(hour=0, minute=0, second=0, microsecond=0)
        base = _filtre(db, ctx, paires)
        jour_ouv = db["pcorg"].count_documents({"$and": base["$and"] + [{"ts": {"$gte": _naif(minuit)}}]})
        jour_clo = db["pcorg"].count_documents({"$and": base["$and"] + [
            {"status_code": STATUT_CLOS}, {"close_ts": {"$gte": _naif(minuit)}}]})
        h1 = now - timedelta(hours=1)
        h_ouv = db["pcorg"].count_documents({"$and": base["$and"] + [{"ts": {"$gte": _naif(h1)}}]})
        h_clo = db["pcorg"].count_documents({"$and": base["$and"] + [
            {"status_code": STATUT_CLOS}, {"close_ts": {"$gte": _naif(h1)}}]})
        par_cat = _repartition(docs, _cat)
        par_urg = _repartition(docs, lambda d: URGENCES.get(d.get("niveau_urgence")))
        fiches = [fiche_courte(d, now) for d in recentes[:LISTE_DATA_MAX]]
        l = ["Main courante %s, %s." % (libelle_per, heure)]
        if incompris:
            l.append("Non compris, ignoré : %s." % ", ".join(incompris))
        if not docs:
            l.append("Aucune fiche en cours.")
        else:
            s = "%d fiche(s) en cours (%s)" % (len(docs), _txt_rep(par_cat))
            vitales = sum(1 for d in docs if d.get("niveau_urgence") in ("EU", "UA"))
            if par_urg:
                s += ", dont " + _txt_rep(par_urg)
                if not vitales:
                    s += " ; aucune détresse vitale ni urgence absolue"
            else:
                s += ", aucune avec un niveau d'urgence"
            l.append(s + ".")
        l.append("Aujourd'hui : %d ouverte(s), %d close(s). Dernière heure : %d ouverte(s), %d close(s)."
                 % (jour_ouv, jour_clo, h_ouv, h_clo))
        if recentes:
            l.append("Fiches en cours, les plus urgentes puis la dernière activité :")
            l += [_ligne(f) for f in fiches[:LISTE_MAX]]
            if len(recentes) > LISTE_MAX:
                l.append("... et %d autre(s) en cours non détaillée(s) (filtrer par catégorie ou urgence)."
                         % (len(recentes) - LISTE_MAX))
        if anciennes:
            l.append("%d fiche(s) restée(s) ouverte(s) sans activité depuis plus de %d jours, non "
                     "détaillées (probablement à clore)." % (anciennes, ANCIENNE_JOURS))
        return {"disponible": True, "vue": "point", "resolu": resolu,
                "compteurs": {"en_cours": len(docs), "par_categorie": par_cat, "par_urgence": par_urg,
                              "ouvertes_aujourdhui": jour_ouv, "closes_aujourdhui": jour_clo,
                              "ouvertes_derniere_heure": h_ouv, "closes_derniere_heure": h_clo,
                              "anciennes_sans_activite": anciennes},
                "fiches": fiches, "resume": _borne("\n".join(l))}

    # 5. Liste filtree
    if per:
        docs.sort(key=lambda d: _utc(d.get("ts")) or now)
    else:
        docs.sort(key=_tri_urgence)
    fiches = [fiche_courte(d, now) for d in docs[:LISTE_DATA_MAX]]
    crit = [resolu.get("categorie"), ", ".join(URGENCES[c] for c in urg) if urg else None,
            "« %s »" % texte if texte else None, resolu.get("periode"),
            {"en_cours": "en cours", "closes": "closes", "toutes": "ouvertes ou closes"}[statut]]
    l = ["Main courante %s, %s. Fiches %s : %d." % (
        libelle_per, heure, ", ".join(c for c in crit if c), len(docs))]
    if incompris:
        l.append("Non compris, ignoré : %s." % ", ".join(incompris))
    if docs:
        l.append("Par catégorie : %s." % _txt_rep(_repartition(docs, _cat)))
        if per:
            l.append("Dans l'ordre chronologique :")
        l += [_ligne(f) for f in fiches[:LISTE_MAX]]
        if len(docs) > LISTE_MAX:
            l.append("... et %d autre(s) non détaillée(s)." % (len(docs) - LISTE_MAX))
    return {"disponible": True, "vue": "liste", "resolu": resolu, "total": len(docs),
            "fiches": fiches, "resume": _borne("\n".join(l))}
