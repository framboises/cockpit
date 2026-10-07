"""cockpit_lieux : horaires et informations des lieux du parametrage, pour Alfred.

Reprend A L'IDENTIQUE les regles validees cote VM sur le corpus porte Nord
(docs/alfred-vm-brief.md, octobre 2026), avec les trois defauts qu'elle a
releves dans son propre outil corriges ici :

  1. `item.access[cle courte] == false` ferme ce public, sur TOUTES les dates
     (la VM ne l'appliquait que sur le chemin "une seule date").
  2. Une faute de frappe est corrigee MOT A MOT avant de tester l'ambiguite :
     "porte nrod" devient "porte nord", donc PIETONS et VL, au lieu de
     resoudre silencieusement vers PORTE NORD VL.
  3. Le resume separe les publics par " | " : ";" separait deja les plages.

POURQUOI LE CALCUL EST ICI. Fusionner "vendredi 06:00-23:59 + samedi 24/24 +
dimanche 00:00-21:00" en "en continu du vendredi 06:00 au dimanche 21:00" est
un raisonnement qu'un petit modele rate une fois sur dix (constate : "05:00"
invente, "00:00" au lieu de 06:00). Le code le fait juste a chaque fois ; le
modele n'a plus qu'a recopier `resume`.

Heures "murales" de Paris, sans fuseau, comme le parametrage. Module pur :
`db` en argument, aucun import Flask ni app. Ne leve jamais vers l'appelant.
"""

import difflib
import re
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo("Europe/Paris")

PUBLICS = ("organisation", "public", "vip")
CLE_COURTE = {"organisation": "orga", "public": "public", "vip": "vip"}
# "organisation (accrédités)" : l'operateur dit "accredites", le modele doit
# retrouver ce mot dans le resume (constate : il avait annonce les horaires
# du public comme ceux des accredites).
LIBELLE = {"organisation": "organisation (accrédités)", "public": "public", "vip": "VIP",
           "tous": "horaires"}
AU_PUBLIC = {"organisation": "à l'organisation (accrédités)", "public": "au public",
             "vip": "aux VIP", "tous": ""}

# Synonymes des publics, compares normalises (minuscules, sans accents).
SYNONYMES_PUBLIC = {
    "organisation": {"orga", "organisation", "organisateur", "organisateurs", "accredite",
                     "accredites", "accreditation", "accreditations", "personnel", "staff",
                     "equipe", "equipes", "benevole", "benevoles", "prestataire",
                     "prestataires"},
    "public": {"public", "grand public", "gp", "spectateur", "spectateurs", "visiteur",
               "visiteurs"},
    "vip": {"vip", "vips", "invite", "invites", "hospitalite", "hospitalites"},
    "tous": {"tous", "tout", "toutes", "tout le monde", "all"},
}

# Rubriques a plages par public : (cle data, categorie)
RUBRIQUES = (
    ("portesHoraires", "porte"),
    ("parkingsHoraires", "parking"),
    ("campingsHoraires", "camping"),
    ("tribunesHoraires", "tribune"),
    ("boutiquesHoraires", "boutique"),
)
RUBRIQUES_ACTIVATION = (("passerellesHoraires", "passerelle"), ("sanitairesActivation", "sanitaire"))
# Services de globalHoraires (liste de {date, openTime, closeTime[, is24h]})
SERVICES = (
    ("dates", "OUVERTURE DU SITE AU PUBLIC"),
    ("centreMedical", "CENTRE MEDICAL"),
    ("helpDesk", "HELP DESK"),
    ("pcOrga", "PC ORGA"),
    ("pcAuthorities", "PC AUTORITES"),
    ("pressRoom", "SALLE DE PRESSE"),
    ("center", "CENTER"),
)
TYPES = {
    "porte": "porte", "portes": "porte", "parking": "parking", "parkings": "parking",
    "camping": "camping", "campings": "camping", "tribune": "tribune", "tribunes": "tribune",
    "boutique": "boutique", "boutiques": "boutique", "hospitalite": "hospitalite",
    "hospitalites": "hospitalite", "passerelle": "passerelle", "passerelles": "passerelle",
    "sanitaire": "sanitaire", "sanitaires": "sanitaire", "wc": "sanitaire", "toilettes": "sanitaire",
    "service": "service", "services": "service",
}
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]
MOTS_VIDES = {"le", "la", "les", "l", "de", "du", "des", "d", "a", "au", "aux", "n",
              "no", "num", "numero", "nr"}
VARIANTES_MAX = 3
CANDIDATS_MAX = 15
RESUME_MAX = 4500


# ---------------------------------------------------------------------------
# Normalisation, dates
# ---------------------------------------------------------------------------

def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def jour_fr(d):
    return "%s %d %s %d" % (JOURS[d.weekday()], d.day, MOIS[d.month - 1], d.year)


def _hm(s):
    m = re.match(r"^(\d{1,2}):(\d{2})", str(s or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 24 or mi > 59:
        return None
    return h, mi


def _date(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def public_canonique(raw):
    """organisation / public / vip / tous, ou None si non reconnu."""
    n = norm(raw)
    if not n:
        return None
    for canon, syn in SYNONYMES_PUBLIC.items():
        if n in syn:
            return canon
    for canon, syn in SYNONYMES_PUBLIC.items():
        if any(w in syn for w in n.split()):
            return canon
    return None


# ---------------------------------------------------------------------------
# Intervalles (algorithme valide cote VM)
# ---------------------------------------------------------------------------

def _intervalle(D, bloc):
    """[debut, fin) d'un bloc {open, close, is24h|open24h}, ou None."""
    if bloc.get("is24h") or bloc.get("open24h"):
        return D, D + timedelta(days=1)
    o, c = _hm(bloc.get("open")), _hm(bloc.get("close"))
    if o is None or c is None:
        return None
    debut = D + timedelta(hours=o[0], minutes=o[1])
    if str(bloc.get("close"))[:5] in ("23:59", "00:00", "24:00"):
        fin = D + timedelta(days=1)  # minuit : permet la continuite
    else:
        fin = D + timedelta(hours=c[0], minutes=c[1])
        if fin <= debut:
            fin += timedelta(days=1)  # fermeture le lendemain
    return debut, fin


def fusionner(intervalles):
    out = []
    for d, f in sorted(intervalles):
        if out and d <= out[-1][1]:
            out[-1][1] = max(out[-1][1], f)
        else:
            out.append([d, f])
    return [(d, f) for d, f in out]


def _bloc_du_jour(entree, public):
    """Bloc d'un public pour une entree de date. Format a plat (openTime /
    closeTime sur l'entree, sans detail par public) : applique a tous."""
    b = entree.get(public)
    if isinstance(b, dict):
        return b
    if any(k in entree for k in ("openTime", "closeTime", "is24h")) and not any(
            isinstance(entree.get(p), dict) for p in PUBLICS):
        return {"open": entree.get("openTime"), "close": entree.get("closeTime"),
                "is24h": entree.get("is24h"), "closed": False}
    return None


def plages_item(item, publics_demandes):
    """{public: {"plages": [(d, f)], "etat": "ok|ferme|non_precise|non_concerne"}}."""
    access = item.get("access") if isinstance(item.get("access"), dict) else None
    dates = [e for e in (item.get("dates") or []) if isinstance(e, dict)]
    out = {}
    for p in publics_demandes:
        if access is not None and access.get(CLE_COURTE[p]) is False:
            out[p] = {"plages": [], "etat": "ferme"}
            continue
        iv, vu, non_precise = [], False, False
        for e in dates:
            D = _date(e.get("date"))
            b = _bloc_du_jour(e, p)
            if D is None or b is None:
                continue
            vu = True
            if b.get("closed"):
                continue
            x = _intervalle(D, b)
            if x is None:
                non_precise = True
                continue
            iv.append(x)
        if not vu and access is not None and CLE_COURTE[p] not in access:
            out[p] = {"plages": [], "etat": "non_concerne"}
        elif iv:
            out[p] = {"plages": fusionner(iv), "etat": "ok"}
        elif non_precise:
            out[p] = {"plages": [], "etat": "non_precise"}
        elif vu or (access is not None and access.get(CLE_COURTE[p])):
            out[p] = {"plages": [], "etat": "ferme"}
        else:
            out[p] = {"plages": [], "etat": "non_concerne"}
    return out


def plages_service(liste):
    iv = []
    for e in liste or []:
        if not isinstance(e, dict):
            continue
        D = _date(e.get("date"))
        if D is None:
            continue
        x = _intervalle(D, {"open": e.get("openTime"), "close": e.get("closeTime"),
                            "is24h": e.get("is24h")})
        if x:
            iv.append(x)
    return fusionner(iv)


# ---------------------------------------------------------------------------
# Redaction (format summary_fr de la VM)
# ---------------------------------------------------------------------------

def texte_plage(d, f):
    D = d.replace(hour=0, minute=0)
    if d == D and f == D + timedelta(days=1):
        return "%s : 24/24" % jour_fr(d)
    if f <= D + timedelta(days=1):
        if f == D + timedelta(days=1):
            return "%s de %s à minuit (00:00)" % (jour_fr(d), d.strftime("%H:%M"))
        return "%s de %s à %s" % (jour_fr(d), d.strftime("%H:%M"), f.strftime("%H:%M"))
    if f.hour == 0 and f.minute == 0:
        return "en continu du %s à %s au %s à minuit" % (
            jour_fr(d), d.strftime("%H:%M"), jour_fr(f - timedelta(days=1)))
    return "en continu du %s à %s au %s à %s" % (
        jour_fr(d), d.strftime("%H:%M"), jour_fr(f), f.strftime("%H:%M"))


def _bloc_texte(p, info):
    lib = LIBELLE[p]
    if info["etat"] == "ok":
        return "%s : %s" % (lib, " ; ".join(texte_plage(d, f) for d, f in info["plages"]))
    if info["etat"] == "ferme":
        return "fermé %s" % AU_PUBLIC[p]
    if info["etat"] == "non_precise":
        return "%s : horaires non précisés dans le paramétrage" % lib
    return None


def _texte_jour(p, info, jour):
    """Phrase d'un public pour un jour precis, avec la plage continue qui le couvre."""
    J = jour.replace(hour=0, minute=0)
    J1 = J + timedelta(days=1)
    lib = LIBELLE[p]
    if info["etat"] == "ferme":
        return "fermé %s" % AU_PUBLIC[p]
    if info["etat"] == "non_precise":
        return "%s : horaires non précisés" % lib
    couvrantes = [(d, f) for d, f in info["plages"] if d < J1 and f > J]
    if not couvrantes:
        return "fermé %s ce jour-là" % AU_PUBLIC[p]
    morceaux = []
    for d, f in couvrantes:
        deb, fin = max(d, J), min(f, J1)
        if deb == J and fin == J1:
            s = "ouvert 24/24"
        else:
            s = "de %s à %s" % (deb.strftime("%H:%M"),
                                "minuit (00:00)" if fin == J1 else fin.strftime("%H:%M"))
        if d < J or f > J1:
            s += " (plage continue : %s)" % texte_plage(d, f)
        morceaux.append(s)
    return "%s : %s" % (lib, " ; ".join(morceaux))


def _texte_maintenant(p, info, now):
    lib = LIBELLE[p]
    if info["etat"] == "ferme":
        return "%s : fermé %s" % (lib, AU_PUBLIC[p])
    if info["etat"] != "ok":
        return None
    for d, f in info["plages"]:
        if d <= now < f:
            return "%s : OUVERT jusqu'au %s à %s" % (lib, jour_fr(f if f.hour or f.minute else f - timedelta(days=1)),
                                                     f.strftime("%H:%M") if f.hour or f.minute else "minuit")
    nxt = [d for d, f in info["plages"] if d > now]
    if nxt:
        return "%s : FERMÉ, ouvre le %s à %s" % (lib, jour_fr(nxt[0]), nxt[0].strftime("%H:%M"))
    return "%s : FERMÉ, plus d'ouverture prévue" % lib


def jours_groupes(dates):
    """'le samedi 26 septembre 2026' / 'du lundi 21 septembre 2026 au dimanche
    27 septembre 2026' / plusieurs groupes separes par ', '."""
    ds = sorted({d.date() for d in dates})
    groupes, debut, prec = [], None, None
    for d in ds:
        if prec is not None and (d - prec).days == 1:
            prec = d
            continue
        if debut is not None:
            groupes.append((debut, prec))
        debut = prec = d
    if debut is not None:
        groupes.append((debut, prec))
    return ", ".join("le %s" % jour_fr(a) if a == b else "du %s au %s" % (jour_fr(a), jour_fr(b))
                     for a, b in groupes)


def controle_item(item, jour=None):
    """Controle d'acces : par jour (dayControl controle/libre, creneaux
    dayControlSlots) et moyen du lieu (controle.type PDA/TRIPODE/VISUEL).
    (texte, dict). Constate en prod : sans ce bloc, le modele a invente
    "controlee en temps reel via Cockpit"."""
    ctrl = item.get("controle") if isinstance(item.get("controle"), dict) else {}
    moyen = str(ctrl.get("type") or "").strip()
    nombre = str(ctrl.get("number") or "").strip()
    controles, libres, creneaux = [], [], []
    for e in item.get("dates") or []:
        if not isinstance(e, dict):
            continue
        D = _date(e.get("date"))
        if D is None or (jour is not None and D.date() != jour.date()):
            continue
        slots = [s for s in (e.get("dayControlSlots") or []) if isinstance(s, dict)]
        if slots:
            creneaux.append((D, ["%s de %s à %s" % ("contrôlé" if s.get("type") == "controle"
                                                    else "accès libre", s.get("start"), s.get("end"))
                                 for s in slots]))
        elif e.get("dayControl") == "controle":
            controles.append(D)
        elif e.get("dayControl") == "libre":
            libres.append(D)
    morceaux = []
    if controles:
        morceaux.append("contrôlé " + jours_groupes(controles))
    if libres:
        morceaux.append("accès libre (sans contrôle) " + jours_groupes(libres))
    for d, txt in creneaux:
        morceaux.append("le %s : %s" % (jour_fr(d), ", ".join(txt)))
    info = {"jours_controles": [d.date().isoformat() for d in controles],
            "jours_libres": [d.date().isoformat() for d in libres],
            "moyen": moyen or None, "nombre": nombre or None}
    if not morceaux:
        return "contrôle d'accès non renseigné dans le paramétrage", info
    if moyen:
        m = "moyen de contrôle : %s%s" % (moyen, (" x" + nombre) if nombre else "")
    elif controles or creneaux:
        m = "moyen de contrôle non précisé"
    else:
        m = ""
    return "contrôle d'accès : " + " ; ".join(morceaux) + ((" (%s)" % m) if m else ""), info


def _plages_json(info):
    return [{"debut": d.isoformat(timespec="minutes"), "fin": f.isoformat(timespec="minutes")}
            for d, f in info["plages"]]


# ---------------------------------------------------------------------------
# Catalogue des lieux d'un parametrage
# ---------------------------------------------------------------------------

def _alias_numero(numero):
    """Formes radio d'un numero de tribune : "13" -> 13, t13 ; "03" -> 03, 3,
    t03, t3 ; "03 bis" -> 03 bis, 3 bis. Le numero vient du parametrage de
    l'evenement : il change d'une edition a l'autre (STANDS : 52 aux 24H MOTOS,
    34 au GP EXPLORER)."""
    n = norm(numero)
    if not n:
        return ""
    tete, _, reste = n.partition(" ")
    formes = {n, "t" + tete}
    if tete.isdigit() and tete != str(int(tete)):
        court = str(int(tete))
        formes |= {(court + " " + reste).strip(), "t" + court}
    return " ".join(sorted(formes))


def catalogue(data):
    """Liste de {nom, categorie, kind, item[, brut, alias]}.
    kind : plages | service | activation | info. Les tribunes numerotees
    s'affichent "SINGHER (n°13)" : c'est ainsi que les operateurs les
    designent a la radio, et "tribune 13" doit les retrouver."""
    out = []
    for cle, cat in RUBRIQUES:
        v = data.get(cle)
        for it in (v.values() if isinstance(v, dict) else (v or [])):
            if isinstance(it, dict) and it.get("name"):
                brut = str(it["name"]).strip()
                lieu = {"nom": brut, "categorie": cat, "kind": "plages", "item": it}
                numero = str(it.get("numero") or "").strip()
                if cat == "tribune" and numero:
                    lieu.update(nom="%s (n°%s)" % (brut, numero), brut=brut,
                                alias=_alias_numero(numero), numero=numero)
                out.append(lieu)
    for cle, cat in RUBRIQUES_ACTIVATION:
        v = data.get(cle)
        for it in (v.values() if isinstance(v, dict) else (v or [])):
            if isinstance(it, dict) and it.get("name"):
                out.append({"nom": str(it["name"]).strip(), "categorie": cat,
                            "kind": "activation", "item": it})
    for it in data.get("hospiHoraires") or []:
        if isinstance(it, dict) and it.get("name"):
            out.append({"nom": str(it["name"]).strip(), "categorie": "hospitalite",
                        "kind": "info", "item": it})
    gh = data.get("globalHoraires") if isinstance(data.get("globalHoraires"), dict) else {}
    for cle, nom in SERVICES:
        if isinstance(gh.get(cle), list) and gh.get(cle):
            out.append({"nom": nom, "categorie": "service", "kind": "service",
                        "item": {"liste": gh[cle]}})
    return out


def _mots(l):
    """Mots par lesquels on peut designer un lieu : nom affiche, nom brut, alias."""
    return set(norm("%s %s %s" % (l["nom"], l.get("brut", ""), l.get("alias", ""))).split())


def _vocabulaire(cat):
    vocab = set()
    for l in cat:
        vocab.update(_mots(l))
    return vocab


def corriger_mots(requete, vocab):
    """Correction mot a mot ("nrod" -> "nord"), AVANT le test d'ambiguite."""
    mots = []
    proteges = set(TYPES) | {w for syn in SYNONYMES_PUBLIC.values() for w in syn}
    for w in norm(requete).split():
        if w in vocab or len(w) < 3 or w in proteges:
            mots.append(w)
            continue
        proche = difflib.get_close_matches(w, list(vocab), n=1, cutoff=0.75)
        mots.append(proche[0] if proche else w)
    return mots


def trouver(cat, nom, type_=None):
    """(lieux retenus, candidats). Regle VM : exact ; tous les mots contenus
    (<= 3 variantes : toutes, au-dela : candidats) ; approche unique."""
    if type_:
        cat = [l for l in cat if l["categorie"] == type_]
    if not nom:
        return [], []
    vocab = _vocabulaire(cat)
    # Mots vides retires ("la 13", "tribune numero 13") ; "la chapelle"
    # reste trouve par "chapelle".
    mots = [w for w in corriger_mots(nom, vocab) if w not in MOTS_VIDES] or corriger_mots(nom, vocab)
    # Un mot de type absent des noms ("parking chinetti") filtre la categorie
    for w in list(mots):
        if w in TYPES and w not in vocab:
            cat = [l for l in cat if l["categorie"] == TYPES[w]] or cat
            mots.remove(w)
    if not mots:
        return [], [l["nom"] for l in cat][:CANDIDATS_MAX]
    q = " ".join(mots)
    exacts = [l for l in cat if q in (norm(l["nom"]), norm(l.get("brut", "")))]
    if exacts:
        return exacts[:1], []
    contenant = [l for l in cat if set(mots) <= _mots(l)]
    if not contenant:
        contenant = [l for l in cat if q in norm(l["nom"])]
    if 1 <= len(contenant) <= VARIANTES_MAX:
        return contenant, []
    if len(contenant) > VARIANTES_MAX:
        return [], [l["nom"] for l in contenant][:CANDIDATS_MAX]
    # Approche, mais MOT A MOT : chaque mot de la requete doit ressembler a un
    # mot du lieu. Sur le nom entier, "porte tertre" ressemblait a "porte est"
    # (le mot commun suffisait) : reponse sur la mauvaise porte, sans le dire.
    def proche(w, mots_lieu):
        return any(difflib.SequenceMatcher(None, w, m).ratio() >= 0.75 for m in mots_lieu)
    proches = [l for l in cat if all(proche(w, _mots(l)) for w in mots)]
    if len(proches) == 1:
        return proches, []
    return [], [l["nom"] for l in proches][:CANDIDATS_MAX]


# ---------------------------------------------------------------------------
# Resolution de l'evenement (priorite VM : cite > contexte hors SAISON > courant)
# ---------------------------------------------------------------------------

def _parametrage(db, event, year):
    for y in (str(year), int(year)) if str(year).isdigit() else (year,):
        doc = db["parametrages"].find_one({"event": event, "year": y})
        if doc:
            return doc
    return None


def resoudre_evenement(db, args, ctx, now):
    """(event, year, doc) ou (None, None, None)."""
    noms = sorted({d.get("event") for d in db["parametrages"].find({}, {"event": 1})
                   if d.get("event")})
    ev_arg = str(args.get("evenement") or "").strip()
    yr_arg = str(args.get("annee") or "").strip()
    event = None
    if ev_arg:
        q = norm(ev_arg)
        event = next((n for n in noms if norm(n) == q), None) or next(
            (n for n in noms if q in norm(n)), None)
    if not event:
        ce = str((ctx or {}).get("event") or "")
        if ce and norm(ce) != "saison":
            event = ce
            yr_arg = yr_arg or str((ctx or {}).get("year") or "")
    if not event:
        try:
            import event_courant
            ev, yr = event_courant.current_event(db, now.replace(tzinfo=TZ_PARIS))
            if norm(ev) != "saison":
                event, yr_arg = ev, yr_arg or str(yr)
            else:
                # SAISON : la prochaine epreuve (ou la plus recente) fait foi
                wins = event_courant.windows(db)
                n = now.replace(tzinfo=TZ_PARIS)
                futures = sorted([w for w in wins if w["start"] >= n], key=lambda w: w["start"])
                passees = sorted([w for w in wins if w["start"] < n], key=lambda w: w["start"])
                w = (futures or passees[::-1] or [None])[0]
                if w:
                    event, yr_arg = w["event"], yr_arg or str(w["year"])
        except Exception:
            pass
    if not event:
        return None, None, None
    if yr_arg:
        doc = _parametrage(db, event, yr_arg)
        return (event, str(yr_arg), doc) if doc else (event, str(yr_arg), None)
    annees = sorted({str(d.get("year")) for d in db["parametrages"].find({"event": event},
                                                                         {"year": 1})})
    cible = str(now.year)
    yr = cible if cible in annees else next((a for a in annees if a > cible), annees[-1] if annees else None)
    return event, yr, _parametrage(db, event, yr) if yr else None


def resoudre_jour(raw, now, dates_evenement):
    """YYYY-MM-DD, aujourd'hui, demain, ou nom de jour (le plus proche dans
    les dates de l'evenement, a defaut la prochaine occurrence)."""
    s = norm(raw)
    if not s:
        return None
    if s in ("aujourd hui", "aujourdhui", "ce jour"):
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if s == "demain":
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    d = _date(raw)
    if d:
        return d
    for i, j in enumerate(JOURS):
        if s.startswith(j):
            cands = sorted(x for x in dates_evenement if x.weekday() == i)
            if cands:
                futurs = [x for x in cands if x.date() >= now.date()]
                return (futurs or cands)[0]
            delta = (i - now.weekday()) % 7
            return (now + timedelta(days=delta)).replace(hour=0, minute=0, second=0, microsecond=0)
    return None


# ---------------------------------------------------------------------------
# Outil
# ---------------------------------------------------------------------------

def _infos(lieu):
    it = lieu["item"]
    keep = {}
    for k in ("capacite", "capacite_theorique", "capacite_pratique", "capacity", "jauge",
              "description", "comments", "information", "parking", "superficie"):
        v = it.get(k)
        if v not in (None, "", [], {}):
            keep[k] = v if not isinstance(v, str) else v[:200]
    ctrl = it.get("controle")
    if isinstance(ctrl, dict) and (ctrl.get("type") or ctrl.get("number")):
        keep["controle"] = {"type": ctrl.get("type"), "nombre": ctrl.get("number")}
    if isinstance(it.get("access"), dict):
        keep["acces"] = [LIBELLE[p] for p in PUBLICS if it["access"].get(CLE_COURTE[p])]
    return keep


def _decrire(lieu, publics, jour, maintenant, now, ev_lib):
    tete = "%s (%s, %s)" % (lieu["nom"], lieu["categorie"], ev_lib)
    res = {"nom": lieu["nom"], "categorie": lieu["categorie"]}
    if lieu.get("numero"):
        res["numero"] = lieu["numero"]
    if lieu["kind"] == "info":
        res["infos"] = _infos(lieu)
        return res, "%s — pas d'horaires dans le paramétrage (informations seulement)." % tete
    if lieu["kind"] == "activation":
        jours = sorted({str(e.get("date") if isinstance(e, dict) else e)[:10]
                        for e in lieu["item"].get("dates") or [] if e})
        res["jours_actifs"] = jours
        if not jours:
            return res, "%s — aucune date d'activation saisie." % tete
        return res, "%s — activé les : %s." % (tete, ", ".join(
            jour_fr(_date(j)) for j in jours if _date(j)))
    if lieu["kind"] == "service":
        infos = {"tous": {"plages": plages_service(lieu["item"]["liste"]), "etat": "ok"}}
        if not infos["tous"]["plages"]:
            infos["tous"]["etat"] = "non_precise"
    else:
        infos = plages_item(lieu["item"], publics)
        res["infos"] = _infos(lieu)
    ctrl_txt = ""
    if lieu["kind"] == "plages":
        ctrl_txt, res["controle"] = controle_item(lieu["item"], jour if not maintenant else now)
        ctrl_txt = " — " + ctrl_txt
    res["plages_continues"] = {p: _plages_json(i) for p, i in infos.items() if i["etat"] == "ok"}
    res["etat_par_public"] = {p: i["etat"] for p, i in infos.items()}
    if maintenant:
        lignes = [x for x in (_texte_maintenant(p, i, now) for p, i in infos.items()) if x]
        return res, "%s — maintenant (%s %s) : %s%s." % (
            tete, jour_fr(now), now.strftime("%H:%M"), " | ".join(lignes), ctrl_txt)
    if jour is not None:
        lignes = [_texte_jour(p, i, jour) for p, i in infos.items() if i["etat"] != "non_concerne"]
        return res, "%s le %s — %s%s." % (tete, jour_fr(jour), " | ".join(lignes), ctrl_txt)
    blocs = [b for b in (_bloc_texte(p, i) for p, i in infos.items()) if b]
    return res, "%s — horaires d'ouverture, plages continues par public — %s%s." % (
        tete, " | ".join(blocs), ctrl_txt)


def etat_maintenant(lieux, publics, now, ev_lib):
    """Vue d'ensemble "qu'est-ce qui est ouvert ?" : lieux ouverts a l'instant,
    sinon prochaines ouvertures, sinon "evenement termine". (resume, details).

    Constate en prod (07/10/2026, deux semaines apres les 24H CAMIONS) : sans
    cette vue, l'outil rendait la LISTE des lieux et le modele l'a lue comme
    "ouverts maintenant". La reponse dit donc explicitement quand rien n'est
    ouvert, et pourquoi."""
    ouverts, prochaines, derniere_fin = [], [], None
    for l in lieux:
        if l["kind"] == "service":
            infos = {"tous": {"plages": plages_service(l["item"]["liste"]), "etat": "ok"}}
        else:
            infos = plages_item(l["item"], publics)
        ici = []
        for p, i in infos.items():
            for d, f in i["plages"]:
                derniere_fin = f if derniere_fin is None or f > derniere_fin else derniere_fin
                if d <= now < f:
                    fin = ("%s à %s" % (jour_fr(f), f.strftime("%H:%M")) if f.hour or f.minute
                           else "%s à minuit" % jour_fr(f - timedelta(days=1)))
                    ici.append("%s jusqu'au %s" % (LIBELLE[p], fin))
                elif d > now:
                    prochaines.append((d, l["nom"], p))
        if ici:
            ouverts.append("%s (%s) : %s" % (l["nom"], l["categorie"], " ; ".join(ici)))
    tete = "En ce moment (%s %s), %s :" % (jour_fr(now), now.strftime("%H:%M"), ev_lib)
    if ouverts:
        return "%s %d lieu(x) ouvert(s) sur %d.\n%s" % (tete, len(ouverts), len(lieux),
                                                       "\n".join(ouverts)), len(ouverts)
    if prochaines:
        prochaines.sort()
        d, nom, p = prochaines[0]
        return ("%s AUCUN lieu n'est ouvert. Prochaine ouverture : %s (%s) le %s à %s."
                % (tete, nom, LIBELLE[p], jour_fr(d), d.strftime("%H:%M"))), 0
    if derniere_fin is not None and derniere_fin <= now:
        if derniere_fin.hour or derniere_fin.minute:
            quand = "le %s à %s" % (jour_fr(derniere_fin), derniere_fin.strftime("%H:%M"))
        else:
            quand = "le %s à minuit" % jour_fr(derniere_fin - timedelta(days=1))
        return ("%s AUCUN lieu n'est ouvert : l'événement est terminé (dernière fermeture %s)."
                % (tete, quand)), 0
    return "%s aucun horaire d'ouverture n'est renseigné." % tete, 0


def t_lieux(db, args, ctx, now=None):
    args = args or {}
    now = now or datetime.now(TZ_PARIS).replace(tzinfo=None, second=0, microsecond=0)
    event, year, doc = resoudre_evenement(db, args, ctx, now)
    if not doc:
        return {"disponible": False, "evenement": event, "annee": year,
                "note": "Aucun paramétrage trouvé pour cet événement : le dire, ne rien estimer."}
    data = doc.get("data") or {}
    ev_lib = "%s %s" % (event, year)
    cat = catalogue(data)
    type_ = TYPES.get(norm(args.get("type")))
    p_raw = args.get("public")
    p = public_canonique(p_raw) if p_raw else "tous"
    publics = list(PUBLICS) if p in (None, "tous") else [p]
    maintenant = bool(args.get("maintenant"))
    dates_ev = sorted({_date(e.get("date")) for l in cat if l["kind"] == "plages"
                       for e in (l["item"].get("dates") or []) if isinstance(e, dict)
                       and _date(e.get("date"))})
    jour = resoudre_jour(args.get("jour"), now, dates_ev) if args.get("jour") else None
    nom = str(args.get("nom") or "").strip()

    base = {"disponible": True, "evenement": event, "annee": year,
            "public_demande": None if p in (None, "tous") else p}
    if p_raw and p is None:
        base["note_public"] = "Public '%s' non reconnu : les trois publics sont donnés." % p_raw

    if nom:
        lieux, candidats = trouver(cat, nom, type_)
        if not lieux:
            if candidats:
                return dict(base, trouve=False, candidats=candidats,
                            resume="Plusieurs lieux correspondent à « %s » : %s. Demander lequel."
                                   % (nom, ", ".join(candidats)))
            # Aucun : proposer les lieux du meme type (mot de type dans la
            # requete, ou parametre type), pour un "vous pensez a... ?"
            t = type_ or next((TYPES[w] for w in norm(nom).split() if w in TYPES), None)
            voisins = [l["nom"] for l in cat if t and l["categorie"] == t][:40]
            return dict(base, trouve=False, lieux_du_meme_type=voisins,
                        resume="Aucun lieu « %s » dans le paramétrage %s.%s" % (
                            nom, ev_lib, (" Lieux de type %s existants : %s." % (
                                t, ", ".join(voisins))) if voisins else ""))
    else:
        # Sans nom de lieu : TOUJOURS l'etat a l'instant ("qu'est-ce qui est
        # ouvert ?"), jamais une simple liste de noms, que le modele lisait
        # comme une liste de lieux ouverts.
        lieux = [l for l in cat if (not type_ or l["categorie"] == type_)
                 and l["kind"] in ("plages", "service")]
        resume, n_ouverts = etat_maintenant(lieux, publics, now, ev_lib)
        if len(resume) > RESUME_MAX:
            resume = resume[:RESUME_MAX].rsplit("\n", 1)[0] + "\n[… préciser un type de lieu]"
        return dict(base, trouve=True, vue="etat_maintenant", lieux_ouverts=n_ouverts,
                    resume=resume)

    details, resumes = [], []
    for l in lieux:
        d, r = _decrire(l, publics, jour, maintenant, now, ev_lib)
        details.append(d)
        resumes.append(r)
    resume = "\n".join(resumes)
    if len(resume) > RESUME_MAX:
        coupe = resume[:RESUME_MAX].rsplit("\n", 1)[0]
        resume = coupe + "\n[… %d autre(s) lieu(x) : préciser le nom ou le type]" % (
            len(resumes) - coupe.count("\n") - 1)
        details = details[:coupe.count("\n") + 1]
    if len(lieux) > 1 and nom:
        resume = "Plusieurs lieux correspondent, voici chacun :\n" + resume
    if len(details) > VARIANTES_MAX:
        # Vue d'ensemble : le resume porte tout, le detail alourdirait le contexte
        details = [{"nom": d["nom"], "categorie": d["categorie"]} for d in details]
    return dict(base, trouve=True, resume=resume, lieux=details)
