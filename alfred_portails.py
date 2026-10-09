"""Portails, portillons et acces piste (collection `3p`) pour Alfred, via
`cockpit_lieux` (parametres `portail` et `autour`).

Hors GroundMaster : la couche 3P de la carte (641 points, nom, nature,
acces, zone, verrou, largeur, commentaire). Deux usages operateur :
- << ou est le portail ACT 2 ? >> : la fiche du portail, son carre du
  carroyage, un bouton qui le montre sur la carte de l'accueil et un lien
  Waze ;
- << les portails autour de la porte Nord / du P3B / de la tribune 13 >> :
  Cockpit trouve la position du lieu dans les couches de la carte (portes,
  tribunes, parkings et aires, passerelles, sanitaires, hospitalites,
  virages, boutiques, postes de secours, giratoires, box, carre), puis les
  points 3P a proximite immediate, tries par distance.

Lecture seule. Aucun code de cadenas n'est stocke dans la collection
(verifie le 09/10/2026) ; le champ Verrous ne donne que le type.
"""

import math
import re
import time
import unicodedata
from urllib.parse import quote

RAYON_DEFAUT = 150       # m autour d'un point
RAYON_SURFACE = 50       # m autour d'une aire / d'un parking (dedans = 0 m)
RAYON_MAX = 600
LISTE_MAX = 15
CARTE_MAX = 25           # points envoyes au bouton carte
CACHE_S = 600
_CACHE = {}

NATURES = {"portail": "Portail", "portails": "Portail", "portillon": "Portillon",
           "portillons": "Portillon", "acces piste": "Accès Piste", "piste": "Accès Piste"}
VIDES = {"le", "la", "les", "l", "de", "du", "des", "d", "a", "au", "aux", "en", "sur", "pres",
         "autour", "proche", "proches", "cote", "vers", "et", "un", "une"}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def compact(s):
    """<< AP 106 Bis >> -> << ap106bis >> : << ap106 bis >>, << AP-106bis >> se valent."""
    return norm(s).replace(" ", "")


def distance_m(a, b):
    (la1, lo1), (la2, lo2) = a, b
    r = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _dans(pt, anneau):
    """Point dans un polygone (anneau [(lat, lng)...]), lancer de rayon."""
    y, x = pt
    dedans = False
    j = len(anneau) - 1
    for i in range(len(anneau)):
        yi, xi = anneau[i]
        yj, xj = anneau[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            dedans = not dedans
        j = i
    return dedans


# ---------------------------------------------------------------------------
# Donnees
# ---------------------------------------------------------------------------

def _features(db, col):
    d = db[col].find_one({}, {"features": 1}) or {}
    return d.get("features") or []


def _points(geom):
    """Points (lat, lng) et anneaux d'une geometrie GeoJSON."""
    t, c = (geom or {}).get("type"), (geom or {}).get("coordinates")
    pts, anneaux = [], []
    try:
        if t == "Point":
            pts.append((float(c[1]), float(c[0])))
        elif t == "MultiPoint":
            pts += [(float(p[1]), float(p[0])) for p in c]
        elif t == "Polygon":
            anneaux.append([(float(p[1]), float(p[0])) for p in c[0]])
        elif t == "MultiPolygon":
            anneaux += [[(float(p[1]), float(p[0])) for p in poly[0]] for poly in c]
    except (TypeError, ValueError, IndexError):
        pass
    return pts, anneaux


def portails(db):
    """Les points 3P, en cache 10 min (une seule FeatureCollection)."""
    k = ("3p", id(db))
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    out = []
    for f in _features(db, "3p"):
        p = f.get("properties") or {}
        pts, _ = _points(f.get("geometry"))
        if not pts or not str(p.get("Nom") or "").strip():
            continue
        nature = str(p.get("Nature") or "").strip().replace("Accés", "Accès") or "Accès"
        out.append({
            "nom": str(p.get("Nom")).strip(), "nature": nature,
            "acces": str(p.get("Accès") or "").strip(), "zone": str(p.get("Zone") or "").strip(),
            "position": str(p.get("Extérieur/Intérieur") or "").strip(),
            "verrou": str(p.get("Verrous") or "").strip().strip("-?").strip(),
            "largeur": str(p.get("Largeur (cm)") or "").strip(),
            "hauteur": str(p.get("Hauteur (cm)") or "").strip(),
            "commentaire": str(p.get("Commentaires") or "").strip(),
            "virage": str(p.get("Virage/Secteur") or "").strip(),
            "lat": pts[0][0], "lng": pts[0][1],
        })
    _CACHE[k] = (time.time(), out)
    return out


def _ajoute(out, nom, type_, alias, geom=None, pts=None, infos=None):
    p, a = _points(geom) if geom else (pts or [], [])
    if not p and not a:
        return
    noms = [nom] + [x for x in alias if x]
    out.append({"nom": nom, "type": type_, "cles": {norm(x) for x in noms if norm(x)},
                "points": p, "anneaux": a, "infos": [i for i in (infos or []) if i]})


def _num(v):
    try:
        n = int(float(str(v).replace(",", ".")))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


# Nom d'un batiment OpenStreetMap qui designe une personne (<< Maison Mme X >>)
_RX_PERSONNE = re.compile(r"\b(M\.|Mr|Mme|Mlle|Monsieur|Madame|Mademoiselle)\b")


def repertoire(db):
    """Les lieux de la carte qui ont une position, avec leurs noms d'usage."""
    k = ("rep", id(db))
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    out = []
    for f in _features(db, "portes"):
        p = f.get("properties") or {}
        n = str(p.get("Name") or "").strip()
        if n:
            d = str(p.get("description") or "").strip()
            _ajoute(out, n.title(), "porte", [n, re.sub(r"^PORTE\s+", "", n)], f.get("geometry"),
                    infos=["accès %s" % d.lower() if d else ""])
    for f in _features(db, "tribunes"):
        p = f.get("properties") or {}
        num = str(p.get("NUMERO 2026") or "").strip().lstrip("0")
        nom = str(p.get("NOM") or "").strip()
        if num or nom:
            alias = [nom, "tribune " + nom] + (["T" + num, "T " + num, "tribune " + num] if num else [])
            cap = _num(p.get("CAPACITE REELLE")) or _num(p.get("CAPACITE HOMOLOGATION"))
            _ajoute(out, ("Tribune %s %s" % (num, nom.title())).strip(), "tribune", alias, f.get("geometry"),
                    infos=[str(p.get("TYPE") or "").lower(), "capacité %d places" % cap if cap else "",
                           "%s accès" % p["NB ACCES"] if _num(p.get("NB ACCES")) else ""])
    for f in _features(db, "terrains"):
        p = f.get("properties") or {}
        n = str(p.get("Name") or "").strip()
        if n:
            alias = [n, n.replace(" ", ""), "parking " + n, "aire " + n, "aa " + n]
            _ajoute(out, n.title() if not re.match(r"^P\d", n) else n, "parking ou aire", alias,
                    f.get("geometry"), infos=[str(p.get("description") or "").strip()])
    for f in _features(db, "passerelles"):
        p = f.get("properties") or {}
        n = str(p.get("Nom") or "").strip()
        if n:
            _ajoute(out, "Passerelle " + n.title(), "passerelle", [n, "passerelle " + n], f.get("geometry"),
                    infos=[str(p.get("type") or ""), "PMR : %s" % p["PMR"] if p.get("PMR") else "",
                           "fermeture : %s" % p["fermeture"] if p.get("fermeture") else ""])
    for f in _features(db, "sanitaires"):
        p = f.get("properties") or {}
        n = str(p.get("Nom") or "").strip()
        if n:
            _ajoute(out, "Sanitaire " + n, "sanitaire",
                    [n, "sanitaire " + n, n.replace("WC", "WC "), "wc " + n.lower().replace("wc", "")],
                    f.get("geometry"),
                    infos=["%s WC" % p["WC"] if _num(p.get("WC")) else "",
                           "%s douches" % p["Douches"] if _num(p.get("Douches")) else "",
                           "PMR" if _num(p.get("PMR")) else "",
                           "gestionnaire %s" % p["Gestionnaire"] if p.get("Gestionnaire") else ""])
    for f in _features(db, "hospitalites"):
        p = f.get("properties") or {}
        n = str(p.get("NOM") or "").strip()
        if n:
            _ajoute(out, n, "hospitalité", [n, "hospitalite " + n], f.get("geometry"),
                    infos=["capacité %d" % _num(p.get("CAPACITE")) if _num(p.get("CAPACITE")) else "",
                           "parking %s" % p["PARKING"] if p.get("PARKING") else ""])
    for f in _features(db, "Boutiques"):
        p = f.get("properties") or {}
        n = str(p.get("nom") or "").strip()
        if n:
            _ajoute(out, "Boutique " + n, "boutique", [n, "boutique " + n], f.get("geometry"),
                    infos=[str(p.get("type") or "")])
    for f in _features(db, "postesecours"):
        p = f.get("properties") or {}
        n = str(p.get("Nom") or "").strip()
        if n:
            _ajoute(out, "Poste de secours " + n.title(), "poste de secours",
                    [n, "poste de secours " + n, "ps " + n], f.get("geometry"),
                    infos=["zone %s" % p["Zone"].lower() if p.get("Zone") else "",
                           str(p.get("Description") or "").strip()])
    for f in _features(db, "giratoires"):
        n = str((f.get("properties") or {}).get("name") or "").strip()
        if n:
            court = re.sub(r"(?i)^giratoire\s+(d[eu]s?\s+|de la\s+|de l'|d')?", "", n)
            _ajoute(out, n, "giratoire", [n, court, "rond point " + court, "giratoire " + court],
                    f.get("geometry"))
    for f in _features(db, "Virages"):
        p = f.get("properties") or {}
        n, d = str(p.get("NAME") or "").strip(), str(p.get("DESCRIPTION") or "").strip()
        if n:
            _ajoute(out, "%s %s" % (n, d), "virage", [n, d, "virage " + d, "virage " + n], f.get("geometry"))
    for f in _features(db, "box_numerotation"):
        n = str((f.get("properties") or {}).get("Numero") or "").strip()
        if n:
            _ajoute(out, "Box " + n, "box", ["box " + n, "stand " + n], f.get("geometry"))
    # Postes commissaires (couche << mp >> de la carte : MP17, MP17b)
    for f in _features(db, "mp"):
        p = f.get("properties") or {}
        n = str(p.get("name") or "").strip()
        m = re.match(r"(?i)^MP\s*(\d+)\s*(b|bis)?$", n)
        if n:
            num = (m.group(1) + (" bis" if m.group(2) else "")) if m else n
            alias = [n, "mp " + num, "poste commissaire " + num, "poste " + n,
                     "poste commissaire " + n] + (["mp%sb" % m.group(1)] if m and m.group(2) else [])
            _ajoute(out, "Poste commissaire " + n, "poste commissaire", alias, f.get("geometry"),
                    infos=[str(p.get("description") or "").strip().lower().capitalize()])
    # Campements commissaires (CC25, C11...) : surfaces
    for f in _features(db, "campements_commissaires"):
        p = f.get("properties") or {}
        n = str(p.get("Name") or "").strip()
        if n:
            d = str(p.get("description") or "").strip()
            d = "" if d.lower() == "none" else d
            # C11 et CC11 sont deux campements : chacun garde son prefixe
            m = re.match(r"(?i)^(C{1,2})\s*(\d+)(.*)$", n)
            alias = [n, "campement " + n, "campement commissaires " + n]
            if m:
                alias += [m.group(1) + " " + m.group(2) + m.group(3), "campement " + m.group(2) + m.group(3)]
            _ajoute(out, "Campement commissaires " + n, "campement commissaires", alias + [d],
                    f.get("geometry"), infos=[d])
    # Batiments (OpenStreetMap) : seulement ceux qui ont un nom, jamais une
    # personne (<< Maison Mme X >>)
    for f in _features(db, "batiment"):
        p = f.get("properties") or {}
        n = str(p.get("name") or "").strip()
        if n and not _RX_PERSONNE.search(n):
            usage = {"grandstand": "tribune", "hangar": "hangar", "retail": "commerce",
                     "service": "service", "school": "école"}.get(str(p.get("building") or ""), "")
            cap = _num(p.get("capacity")) or _num(p.get("capacite"))
            _ajoute(out, n, "bâtiment", [n, "batiment " + n], f.get("geometry"),
                    infos=[usage, "capacité %d" % cap if cap else ""])
    deja = {k for l in out if l["type"] == "parking ou aire" for k in l["cles"]}
    for d in db["refparkings"].find({}, {"name": 1, "gps": 1}):
        m = re.match(r"\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", str(d.get("gps") or ""))
        # Le polygone des terrains vaut mieux que le point : refparkings ne
        # complete que les parkings absents des terrains (<< P3B >> sortait deux fois)
        if m and d.get("name") and norm(d["name"]) not in deja:
            n = str(d["name"]).strip()
            _ajoute(out, n, "parking ou aire", [n, n.replace(" ", ""), "parking " + n],
                    pts=[(float(m.group(1)), float(m.group(2)))])
    _CACHE[k] = (time.time(), out)
    return out


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------

def trouver_portail(liste, nom):
    """Exact au nom compacte (<< act2 >>, << AP 106 bis >>), sinon les noms qui
    commencent par la demande (<< T52 1 >> -> T52 1, T52 10...)."""
    c = compact(nom)
    if not c:
        return [], []
    exact = [p for p in liste if compact(p["nom"]) == c]
    if exact:
        return exact, []
    proches = [p for p in liste if compact(p["nom"]).startswith(c)]
    return [], sorted(proches, key=lambda p: compact(p["nom"]))[:8]


# Un mot de type dans la demande restreint la recherche a ce type (<< porte
# Nord >> n'est pas le poste de secours << PORTE NORD >>)
MOTS_TYPE = {"porte": "porte", "portes": "porte", "tribune": "tribune", "tribunes": "tribune",
             "passerelle": "passerelle", "parking": "parking ou aire", "aire": "parking ou aire",
             "aa": "parking ou aire", "camping": "parking ou aire", "sanitaire": "sanitaire",
             "sanitaires": "sanitaire", "wc": "sanitaire", "giratoire": "giratoire", "rond": "giratoire",
             "boutique": "boutique", "box": "box", "virage": "virage", "hospitalite": "hospitalité",
             "poste": "poste de secours", "secours": "poste de secours", "commissaire": "poste commissaire",
             "commissaires": "poste commissaire", "mp": "poste commissaire",
             "campement": "campement commissaires", "campements": "campement commissaires",
             "batiment": "bâtiment", "batiments": "bâtiment", "giratoires": "giratoire",
             "passerelles": "passerelle", "virages": "virage"}


def trouver_lieu(db, texte):
    """(lieux retenus, candidats). Score : nom exact > tous les mots de la
    demande dans un nom > le plus de mots en commun. Plusieurs lieux de meme
    type au meme score (<< porte Nord >> : Pietons, VL, Bis) sont gardes
    ensemble : la proximite se mesure au plus proche d'entre eux."""
    q = [w for w in norm(texte).split() if w not in VIDES]
    if not q:
        return [], []
    qs = " ".join(q)
    types = {MOTS_TYPE[w] for w in q if w in MOTS_TYPE}
    if "poste" in q and "secours" not in q:
        types.discard("poste de secours")
    if "campement" in q or "campements" in q:
        types.discard("poste commissaire")     # << campement commissaires >>
    rep = repertoire(db)
    if types and any(l["type"] in types for l in rep):
        rep = [l for l in rep if l["type"] in types]
    notes = []
    for l in rep:
        best = 0
        for c in l["cles"]:
            mots = c.split()
            if c == qs or c.replace(" ", "") == qs.replace(" ", ""):
                best = max(best, 100)
            elif all(w in mots for w in q):
                best = max(best, 60 - len(mots))
            else:
                commun = sum(1 for w in q if w in mots)
                if commun:
                    best = max(best, 10 * commun - len(mots))
        if best > 0:
            notes.append((best, l))
    if types and (not notes or max(s for s, l in notes) < 20):
        # Aucun lieu de ce type ne porte ce nom (<< passerelle Panorama >>) :
        # chercher le nom parmi tous les lieux
        reste = " ".join(w for w in q if w not in MOTS_TYPE)
        return trouver_lieu(db, reste) if reste and reste != qs else ([], [])
    if not notes:
        return [], []
    notes.sort(key=lambda x: -x[0])
    top = notes[0][0]
    if top < 20:                          # pas un seul nom ne contient toute la demande
        return [], [l for s, l in notes[:5]]
    tete = [l for s, l in notes if s == top]
    if len({l["type"] for l in tete}) == 1 and len(tete) <= 4:
        return tete, []
    return [], tete[:6]


def distance_lieu(lieux, pt):
    d = None
    for l in lieux:
        for a in l["anneaux"]:
            if _dans(pt, a):
                return 0.0
            for v in a:
                x = distance_m(pt, v)
                d = x if d is None else min(d, x)
        for p in l["points"]:
            x = distance_m(pt, p)
            d = x if d is None else min(d, x)
    return d


def carre_de(db, lat, lng):
    """Carre du carroyage 100 m : centre le plus proche (grid_ref)."""
    k = ("grid", id(db))
    hit = _CACHE.get(k)
    if not hit or time.time() - hit[0] > CACHE_S:
        hit = (time.time(), [(g["grid_ref"], float(g["latitude"]), float(g["longitude"]))
                             for g in db["grid_ref"].find({}, {"grid_ref": 1, "latitude": 1, "longitude": 1})
                             if g.get("latitude") is not None])
        _CACHE[k] = hit
    best = None
    for ref, la, lo in hit[1]:
        if abs(la - lat) > 0.002 or abs(lo - lng) > 0.003:
            continue
        d = distance_m((lat, lng), (la, lo))
        if best is None or d < best[0]:
            best = (d, ref)
    return best[1] if best and best[0] <= 80 else None


# ---------------------------------------------------------------------------
# Rendus
# ---------------------------------------------------------------------------

def lien_carte(points, libelle):
    """Bouton << Voir sur la carte >> : /#carte=lat,lng,nom;... (le widget le
    revalide, recentre la carte de l'accueil et pose les reperes)."""
    morceaux = ["%.6f,%.6f,%s" % (p["lat"], p["lng"], quote(p["nom"][:40], safe="")) for p in points[:CARTE_MAX]]
    return {"libelle": libelle[:60], "url": "/#carte=" + ";".join(morceaux)}


def lien_waze(p):
    return {"libelle": ("Itinéraire Waze vers %s" % p["nom"])[:60],
            "url": "https://ul.waze.com/ul?ll=%.6f%%2C%.6f&navigate=yes" % (p["lat"], p["lng"])}


def _details(p):
    bits = [p["nature"].lower()]
    if p["acces"]:
        bits.append("accès %s" % p["acces"])
    if p["zone"]:
        bits.append("zone %s" % p["zone"])
    if p["position"] in ("Intérieur", "Extérieur"):
        bits.append(p["position"].lower())
    if p["verrou"]:
        bits.append("verrou : %s" % p["verrou"])
    if p["largeur"]:
        bits.append("largeur %s cm" % p["largeur"])
    return ", ".join(bits)


def fiche_portail(db, p):
    carre = carre_de(db, p["lat"], p["lng"])
    l = ["%s : %s." % (p["nom"], _details(p))]
    if p["commentaire"]:
        l.append("Note : %s." % p["commentaire"].rstrip("."))
    l.append("Position : %.5f, %.5f%s." % (p["lat"], p["lng"], ", carré %s" % carre if carre else ""))
    l.append("Boutons : « Voir sur la carte » et « Itinéraire Waze ».")
    return {"disponible": True, "vue": "portail", "trouve": True, "portail": dict(p, carre=carre),
            "liens": [lien_carte([p], "Voir %s sur la carte" % p["nom"]), lien_waze(p)],
            "resume": "\n".join(l)}


def centre(lieu):
    """Point representatif : le point, ou le centre des sommets d'une surface."""
    if lieu["points"]:
        return lieu["points"][0]
    sommets = [v for a in lieu["anneaux"] for v in a]
    return (sum(v[0] for v in sommets) / len(sommets), sum(v[1] for v in sommets) / len(sommets))


def fiche_lieu(db, texte):
    """<< Ou est le poste commissaire MP17 / le campement CC25 / le giratoire
    de l'oeuf ? >> : un lieu des couches de la carte, sa nature, ce que la
    couche en dit, son carre, et les boutons carte et Waze. None si inconnu."""
    lieux, candidats = trouver_lieu(db, texte)
    if not lieux:
        if candidats:
            return {"disponible": True, "vue": "lieu_ambigu", "trouve": False,
                    "candidats": [c["nom"] for c in candidats],
                    "resume": "Plusieurs lieux de la carte pour « %s » : %s. Lequel ?" % (
                        texte, ", ".join("%s (%s)" % (c["nom"], c["type"]) for c in candidats))}
        return None
    l, pts = [], []
    for x in lieux:
        la, lo = centre(x)
        carre = carre_de(db, la, lo)
        infos = ", ".join(x["infos"])
        l.append("%s (%s%s) : %scentre à %.5f, %.5f%s." % (
            x["nom"], x["type"], ", %s" % infos if infos else "", "surface, " if x["anneaux"] else "",
            la, lo, ", carré %s" % carre if carre else ""))
        pts.append({"nom": x["nom"], "lat": la, "lng": lo})
    l.append("Boutons : « Voir sur la carte » et « Itinéraire Waze ».")
    return {"disponible": True, "vue": "lieu_carte", "trouve": True,
            "lieux": [{"nom": x["nom"], "type": x["type"], "infos": x["infos"]} for x in lieux],
            "liens": [lien_carte(pts, ("Voir %s sur la carte" % pts[0]["nom"]) if len(pts) == 1
                                 else "Voir les %d sur la carte" % len(pts)), lien_waze(pts[0])],
            "resume": "\n".join(l)}


def nature_demandee(raw):
    n = norm(raw)
    if not n:
        return None
    for k, v in NATURES.items():
        if k in n:
            return v
    return None


def sans_nature(texte):
    """<< les portillons de la T52 >> -> << t52 >> (<< porte >> reste : << porte
    Nord >> est un lieu)."""
    t = " " + norm(texte) + " "
    for k in sorted(NATURES, key=len, reverse=True):
        t = t.replace(" %s " % k, " ")
    return " ".join(w for w in t.split() if w not in VIDES and w != "acces")


def t_portails(db, args):
    """Point d'entree depuis cockpit_lieux : args `portail`, `autour`,
    `nature`, `rayon`."""
    nom = str(args.get("portail") or "").strip()
    autour = str(args.get("autour") or "").strip()
    nature = nature_demandee(args.get("nature")) or nature_demandee(nom)
    if nom and autour and nature and not sans_nature(nom):
        nom = ""                  # << portillons >> + autour : un filtre, pas un nom
    liste = portails(db)
    if not liste:
        return {"disponible": False, "resume": "Couche des portails (3P) indisponible. Ne pas l'estimer."}

    proches_nom = []
    if nom and not autour:
        exact, proches = trouver_portail(liste, nom)
        proches_nom = proches
        if exact:
            return fiche_portail(db, exact[0])
        if proches and any(ch.isdigit() for ch in nom):
            return {"disponible": True, "vue": "portail_proches", "trouve": False,
                    "candidats": [p["nom"] for p in proches],
                    "resume": "Pas de portail nommé exactement « %s ». Noms proches : %s." % (
                        nom, ", ".join(p["nom"] for p in proches))}
        # << portail du Houx >>, << les portillons de la T52 >> : un LIEU
        autour = sans_nature(nom)

    if not autour:
        return {"disponible": True, "vue": "portail_vide", "trouve": False,
                "resume": "Préciser le nom du portail (ex. ACT 2, AP 106 Bis) ou le lieu autour "
                          "duquel chercher (porte Nord, P3B, tribune 13, carré P5)."}
    try:
        rayon = max(30, min(int(float(args.get("rayon") or RAYON_DEFAUT)), RAYON_MAX))
    except (TypeError, ValueError):
        rayon = RAYON_DEFAUT

    # Lieu de reference : un portail, un lieu de la carte, ou un carre. Le
    # carre seulement s'il est dit (<< carre P5 >>) ou si rien d'autre ne
    # correspond : << T52 >> est la tribune des stands, pas le carre T52.
    lieux, candidats = [], []
    m = re.fullmatch(r"\s*(carr[eé]\s*)?([A-Za-z]{1,2})\s?-?(\d{1,3})\s*", autour)

    def carre():
        g = db["grid_ref"].find_one({"grid_ref": m.group(2).upper() + str(int(m.group(3)))}) if m else None
        if g and g.get("latitude") is not None:
            return [{"nom": "carré " + g["grid_ref"], "type": "carré",
                     "points": [(float(g["latitude"]), float(g["longitude"]))], "anneaux": []}]
        return []
    if m and m.group(1):
        lieux = carre()
    if not lieux:
        exact, _ = trouver_portail(liste, autour)
        if exact:
            lieux = [{"nom": exact[0]["nom"], "type": "portail", "points": [(exact[0]["lat"], exact[0]["lng"])],
                      "anneaux": []}]
    if not lieux:
        lieux, candidats = trouver_lieu(db, autour)
    if not lieux and not candidats and m:
        lieux = carre()
    if not lieux:
        # Repli : le champ << Acces >> des portails (<< Welcome >>, << Pitlane >>)
        a = norm(autour)
        par_acces = [p for p in liste if a and norm(p["acces"]) == a and (not nature or p["nature"] == nature)]
        if par_acces:
            par_acces.sort(key=lambda p: compact(p["nom"]))
            l = ["%s rattachés à l'accès « %s » : %d." % (
                {"Portail": "Portails", "Portillon": "Portillons", "Accès Piste": "Accès piste"}.get(
                    nature, "Portails, portillons et accès piste"), par_acces[0]["acces"], len(par_acces))]
            l += ["- %s (%s%s)" % (p["nom"], p["nature"].lower(), ", %s" % p["verrou"] if p["verrou"] else "")
                  for p in par_acces[:LISTE_MAX]]
            if len(par_acces) > LISTE_MAX:
                l.append("… et %d autres." % (len(par_acces) - LISTE_MAX))
            return {"disponible": True, "vue": "acces", "trouve": True, "lieu": par_acces[0]["acces"],
                    "portails": [{"nom": p["nom"], "nature": p["nature"], "verrou": p["verrou"]}
                                 for p in par_acces[:LISTE_MAX]],
                    "liens": [lien_carte(par_acces, "Voir les %d sur la carte" % min(len(par_acces), CARTE_MAX))],
                    "resume": "\n".join(l + ["Bouton « Voir sur la carte »."])}
        if candidats:
            return {"disponible": True, "vue": "lieu_ambigu", "trouve": False,
                    "candidats": [c["nom"] for c in candidats],
                    "resume": "Plusieurs lieux possibles pour « %s » : %s. Lequel ?" % (
                        autour, ", ".join("%s (%s)" % (c["nom"], c["type"]) for c in candidats))}
        if proches_nom:          # << PON >> : un debut de nom de portail, pas un lieu
            return {"disponible": True, "vue": "portail_proches", "trouve": False,
                    "candidats": [p["nom"] for p in proches_nom],
                    "resume": "Portails dont le nom commence par « %s » : %s. Lequel ?" % (
                        nom, ", ".join(p["nom"] for p in proches_nom))}
        return {"disponible": True, "vue": "lieu_inconnu", "trouve": False,
                "resume": "Lieu « %s » sans position connue sur la carte (portes, tribunes, parkings, "
                          "aires, passerelles, sanitaires, giratoires, carré...). Le préciser." % autour}

    if not args.get("rayon") and any(l["anneaux"] for l in lieux):
        rayon = RAYON_SURFACE      # une aire, un parking : le bord compte deja
    proches = []
    for p in liste:
        if nature and p["nature"] != nature:
            continue
        d = distance_lieu(lieux, (p["lat"], p["lng"]))
        if d is not None and d <= rayon:
            proches.append((d, p))
    proches.sort(key=lambda x: x[0])
    ref = lieux[0]["nom"] if len(lieux) == 1 else "%s (%d points : %s)" % (
        re.sub(r"\s+(Pietons|Vl|Bis|\d+)$", "", lieux[0]["nom"]), len(lieux),
        ", ".join(l["nom"] for l in lieux))
    quoi = {"Portail": "Portails", "Portillon": "Portillons", "Accès Piste": "Accès piste"}.get(
        nature, "Portails, portillons et accès piste")
    if not proches:
        return {"disponible": True, "vue": "autour", "trouve": False, "lieu": ref,
                "resume": "%s : aucun à moins de %d m de %s." % (quoi, rayon, ref)}
    l = ["%s à moins de %d m de %s : %d." % (quoi, rayon, ref, len(proches))]
    for d, p in proches[:LISTE_MAX]:
        l.append("- %s (%s%s, %d m%s)" % (
            p["nom"], p["nature"].lower(), ", accès %s" % p["acces"] if p["acces"] else "", round(d),
            ", %s" % p["verrou"] if p["verrou"] else ""))
    if len(proches) > LISTE_MAX:
        l.append("… et %d autres (réduire le rayon ou préciser la nature)." % (len(proches) - LISTE_MAX))
    l.append("Bouton « Voir sur la carte » : les %d points." % min(len(proches), CARTE_MAX))
    pts = [p for d, p in proches]
    return {"disponible": True, "vue": "autour", "trouve": True, "lieu": ref, "rayon_m": rayon,
            "portails": [{"nom": p["nom"], "nature": p["nature"], "acces": p["acces"], "distance_m": round(d),
                          "verrou": p["verrou"]} for d, p in proches[:LISTE_MAX]],
            "liens": [lien_carte(pts, "Voir les %d sur la carte" % min(len(pts), CARTE_MAX)),
                      lien_waze(pts[0])],
            "resume": "\n".join(l)}
