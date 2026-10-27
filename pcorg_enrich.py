# pcorg_enrich.py -- enrichissement des fiches structurees, en CODE PUR (sans modele)
#
# Complete pcorg_digests (pcorg_digest.py) avec ce qui se calcule sans IA :
#   - position : GPS d'origine, sinon carroyage de la fiche (grid_ref), sinon
#     lieu cite rapproche du referentiel des couches Looker (portes, tribunes,
#     terrains, giratoires, virages, hospitalites, passerelles...) ;
#   - zone de competence (couche "competences" des operateurs) ;
#   - delais de l'intervention a partir des heures extraites par le modele ;
#   - completude de la saisie ;
#   - doublons probables (meme evenement, heures proches, meme lieu / meme fait).
# Jamais d'ecriture dans pcorg ni dans les couches Looker (lecture seule).
#
# Couches EXCLUES du referentiel : cadastre et riverains (noms de proprietaires,
# donnees personnelles), cameras (noms techniques), sifflets.
import difflib
import re
import unicodedata
from datetime import datetime, timedelta

# collection Looker -> (libelle, nom court a ne pas rapprocher seul)
LAYERS = {
    "portes": "Porte", "tribunes": "Tribune", "terrains": "Terrain / parking", "giratoires": "Giratoire",
    "Virages": "Virage", "hospitalites": "Hospitalite", "passerelles": "Passerelle",
    "postesecours": "Poste de secours", "sanitaires": "Sanitaires", "3p": "Portail 3P",
    "mp": "Poste commissaires", "fanzone": "Fan zone", "batiment": "Batiment",
    "campements_commissaires": "Campement commissaires", "box_numerotation": "Box", "Boutiques": "Boutique",
}
ZONES_COLLECTION = "competences"
MIN_NAME_CHARS = 4          # un nom plus court n'est rapproche que par motif dedie (T12, V3...)
# Un nom d'UN seul mot n'est retenu que si le lieu cite est exactement ce nom, et
# jamais pour ces mots generiques : « PS.Nord » tombait sur un lieu « Nord »,
# « Virage Porsche » sur « Porsche » (essai v3 du 27/10/2026).
GENERIC_WORDS = {"nord", "sud", "est", "ouest", "village", "paddock", "piste", "circuit", "parking", "acces",
                 "entree", "sortie", "porte", "portail", "tribune", "musee", "concert", "annexe", "family",
                 "service", "accueil", "parc", "zone", "poste", "secteur", "aire", "camping"}
DUP_WINDOW_MIN = 45         # doublons : ecart maximal entre deux fiches
DUP_THRESHOLD = 0.72        # doublons : score minimal (0-1)


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ---------------------------------------------------------------------------
# Geometrie
# ---------------------------------------------------------------------------
def _ring_centroid(ring):
    pts = [p for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not pts:
        return None
    return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))   # (lat, lng)


def point_of(geom):
    """(lat, lng) representatif d'une geometrie GeoJSON, ou None."""
    if not isinstance(geom, dict):
        return None
    t, c = geom.get("type"), geom.get("coordinates")
    try:
        if t == "Point":
            return (float(c[1]), float(c[0]))
        if t == "MultiPoint":
            return (float(c[0][1]), float(c[0][0]))
        if t == "Polygon":
            return _ring_centroid(c[0])
        if t == "MultiPolygon":
            return _ring_centroid(c[0][0])
    except (TypeError, IndexError, ValueError):
        return None
    return None


def _in_ring(lat, lng, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def point_in_geom(lat, lng, geom):
    t, c = (geom or {}).get("type"), (geom or {}).get("coordinates")
    polys = [c] if t == "Polygon" else (c if t == "MultiPolygon" else [])
    for poly in polys or []:
        if poly and _in_ring(lat, lng, poly[0]) and not any(_in_ring(lat, lng, h) for h in poly[1:]):
            return True
    return False


# ---------------------------------------------------------------------------
# Referentiel des lieux
# ---------------------------------------------------------------------------
class Gazetteer:
    def __init__(self, db):
        self.entries = []          # {name, norm, layer, label, lat, lng}
        self.tribunes = {}         # numero (int) -> entry
        self.virages = {}          # numero (int) -> entry
        self.grid = {}             # code carroyage sans espace -> (lat, lng)
        self.zones = []            # {id, name, geom}
        cfg = db["categories_config"].find_one({}) or {}
        keys = {c.get("collection_name"): (c.get("searchField") or "").replace("properties.", "")
                for c in cfg.get("categories") or []}
        for coll, label in LAYERS.items():
            key = keys.get(coll)
            if not key:
                continue
            doc = db[coll].find_one({}) or {}
            for f in doc.get("features") or []:
                name = (f.get("properties") or {}).get(key)
                pt = point_of(f.get("geometry"))
                if not name or pt is None:
                    continue
                e = {"name": str(name), "norm": norm(name), "layer": coll, "label": label,
                     "lat": pt[0], "lng": pt[1]}
                if coll == "tribunes" and str(name).strip().isdigit():
                    self.tribunes[int(name)] = e
                elif coll == "Virages" and re.fullmatch(r"V\d{1,2}", str(name).strip(), re.I):
                    self.virages[int(str(name).strip()[1:])] = e
                elif len(e["norm"]) >= MIN_NAME_CHARS:
                    self.entries.append(e)
        # Noms les plus longs d'abord : "porte maison blanche vl" avant "maison blanche"
        self.entries.sort(key=lambda e: -len(e["norm"]))
        for g in db["grid_ref"].find({}, {"grid_ref": 1, "latitude": 1, "longitude": 1}):
            code = re.sub(r"\s+", "", str(g.get("grid_ref") or "")).upper()
            if code and g.get("latitude") is not None:
                self.grid[code] = (float(g["latitude"]), float(g["longitude"]))
        zdoc = db[ZONES_COLLECTION].find_one({}) or {}
        for f in zdoc.get("features") or []:
            p = f.get("properties") or {}
            if f.get("geometry"):
                self.zones.append({"id": p.get("_id_feature"), "name": p.get("Nom") or p.get("name"),
                                   "geom": f["geometry"]})

    def zone_of(self, lat, lng):
        for z in self.zones:
            if point_in_geom(lat, lng, z["geom"]):
                return {"id": z["id"], "name": z["name"]}
        return None

    def match_text(self, text):
        """Lieu du referentiel cite dans un texte : (entry, confiance) ou (None, None)."""
        t = " %s " % norm(text)
        if not t.strip():
            return None, None
        m = re.search(r" t(?:ribune)? ?0?(\d{1,2}) ", t)
        if m and int(m.group(1)) in self.tribunes:
            return self.tribunes[int(m.group(1))], "haute"
        whole = t.strip()
        for e in self.entries:
            if " " in e["norm"]:
                if " %s " % e["norm"] in t:
                    return e, "haute"
            elif whole == e["norm"] and e["norm"] not in GENERIC_WORDS:
                return e, "moyenne"
        m = re.search(r" (?:virage )?v(\d{1,2}) ", t)
        if m and int(m.group(1)) in self.virages:
            return self.virages[int(m.group(1))], "moyenne"
        return None, None

    def locate(self, fiche, lieu=None):
        """Position de la fiche : GPS d'origine > carroyage > lieu cite (modele puis titre)."""
        gps = fiche.get("gps") or {}
        coords = gps.get("coordinates") if isinstance(gps, dict) else None
        if coords and len(coords) >= 2:
            return {"lat": float(coords[1]), "lng": float(coords[0]), "source": "gps", "confidence": "haute"}
        carro = re.sub(r"\s+", "", str((fiche.get("content_category") or {}).get("carroye") or "")).upper()
        if carro and carro in self.grid:
            lat, lng = self.grid[carro]
            return {"lat": lat, "lng": lng, "source": "carroyage", "name": carro, "confidence": "haute"}
        for src, txt in (("lieu", lieu), ("titre", fiche.get("text"))):
            if not txt or norm(txt) in ("", "non precise"):
                continue
            e, conf = self.match_text(txt)
            if e:
                return {"lat": e["lat"], "lng": e["lng"], "source": src, "layer": e["layer"],
                        "layer_label": e["label"], "name": e["name"], "confidence": conf}
        return None


# ---------------------------------------------------------------------------
# Delais et completude
# ---------------------------------------------------------------------------
ETAPES = ("signalement", "engagement", "sur_place", "fin")


def _hm(s):
    m = re.fullmatch(r"\s*(\d{1,2})\s*[:hH]\s*(\d{2})\s*", str(s or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def delays(etapes):
    """{engagement, sur_place, fin} en minutes depuis le signalement (passage de
    minuit gere). Valeur absente ou incoherente (> 12 h) = None."""
    t = {k: _hm((etapes or {}).get(k)) for k in ETAPES}
    base = t["signalement"]
    out = {}
    for k in ("engagement", "sur_place", "fin"):
        v = None
        if base is not None and t[k] is not None:
            d = t[k] - base
            d = d + 1440 if d < 0 else d
            v = d if d <= 720 else None
        out[k] = v
    return out


def completeness(d, geo):
    checks = {
        "lieu": bool(geo) or norm(d.get("lieu")) not in ("", "non precise"),
        "heures": any(_hm((d.get("etapes") or {}).get(k)) is not None for k in ETAPES),
        "issue": d.get("issue_code") not in (None, "", "non_precise"),
        "moyens": bool(d.get("moyens_engages")),
    }
    return {"score": sum(checks.values()), "max": len(checks),
            "manquants": [k for k, v in checks.items() if not v]}


# ---------------------------------------------------------------------------
# Taxonomies : valeurs saisies rapprochees de la liste officielle (pcorg_config)
# ---------------------------------------------------------------------------
# Variantes vues dans les fiches que la normalisation seule ne rapproche pas
SC_SYNONYMS = {
    "gene circul": "gene a la circulation",
    "non respect reglement circuit": "non respect reglementation circuit",
    "non respect reglementation": "non respect reglementation circuit",
    "intrusion zone reglementee": "intrusion",
    "secours victime": "secours a victime",
}


def _motif_key(s):
    """Motif d'intervention PC Surete : 6 orthographes de « Ronde surete interieur »."""
    k = norm(s)
    k = re.sub(r"\binterieure\b", "interieur", k)
    k = re.sub(r"\bexterieure\b", "exterieur", k)
    k = re.sub(r"\bbts?\b", "batiment", k)
    k = re.sub(r"\bpre?station\b", "prestation", k)
    return k.strip()


class Taxonomy:
    def __init__(self, db):
        doc = db["pcorg_config"].find_one({"_id": "pcorg_lists"}) or {}
        self.sc = {}                 # norm -> libelle officiel (toutes categories)
        for items in (doc.get("sous_classifications") or {}).values():
            for it in items or []:
                lab = str((it or {}).get("label") or "").strip()
                if lab:
                    self.sc.setdefault(norm(lab), lab)
        self.people = {}             # norm -> libelle officiel (intervenants + services)
        for key in ("intervenants", "services"):
            for it in doc.get(key) or []:
                lab = str((it or {}).get("label") or "").strip()
                if lab:
                    self.people.setdefault(norm(lab), lab)

    @staticmethod
    def _match(value, table, synonyms=None):
        """(libelle canonique, officiel ?) ; valeur vide -> (None, False)."""
        k = norm(value)
        if not k:
            return None, False
        k = (synonyms or {}).get(k, k)
        if k in table:
            return table[k], True
        close = difflib.get_close_matches(k, list(table.keys()), n=1, cutoff=0.86)
        if close:
            return table[close[0]], True
        return str(value).strip(), False

    def of(self, fiche):
        cc = fiche.get("content_category") or {}
        sc, sc_off = self._match(cc.get("sous_classification"), self.sc, SC_SYNONYMS)
        svc, _ = self._match(cc.get("service_contacte"), self.people)
        intervenants = []
        for i in range(1, 6):
            v, _ = self._match(cc.get("intervenant%d" % i), self.people)
            if v and v not in intervenants:
                intervenants.append(v)
        motif = cc.get("motif_intervention")
        return {"sous_classification": sc, "sous_classification_officielle": sc_off,
                "motif": _motif_key(motif) or None, "service": svc, "intervenants": intervenants,
                "classification": cc.get("classification") or None}


# « n°31650 » devient « n31650 » une fois normalise (le ° disparait)
_DUP_DECL_RE = re.compile(r"doublon (?:de )?(?:la )?(?:avec )?(?:fiche |msg |message )?(?:n ?o ?|n ?)?(\d{3,6})")


def declared_duplicate(fiche):
    """Numero SQL d'une fiche citee comme doublon par l'operateur (« Doublon fiche 31645»)."""
    m = _DUP_DECL_RE.search(norm("%s %s" % (fiche.get("text") or "", fiche.get("comment") or "")))
    return int(m.group(1)) if m else None


def area_levels(fiche):
    """« _MC PCO/Interieur Dep./03-Zone Sud » -> niveaux ; zone generique = non localisee."""
    a = fiche.get("area")
    desc = (a.get("desc") if isinstance(a, dict) else a) or ""
    levels = [x.strip() for x in str(desc).split("/") if x.strip()]
    return {"niveaux": levels, "generique": desc.strip() in ("_MC PCS", "_MC PCO", "")}


def _relances(fiche):
    try:
        n = int(str((fiche.get("content_category") or {}).get("relance")).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 20 else None


def enrich(gaz, fiche, d, taxo=None):
    """Champs calcules a stocker dans le digest."""
    geo = gaz.locate(fiche, d.get("lieu"))
    out = {"geo": geo, "zone": gaz.zone_of(geo["lat"], geo["lng"]) if geo else None,
           "delais": delays(d.get("etapes")), "completude": completeness(d, geo),
           "lieu_norm": norm(geo["name"]) if geo and geo.get("name") else norm(d.get("lieu")),
           "area": area_levels(fiche), "relances": _relances(fiche),
           "doublon_declare_sql": declared_duplicate(fiche)}
    if taxo is not None:
        out["taxo"] = taxo.of(fiche)
    return out


# ---------------------------------------------------------------------------
# Alarmes mises hors service et jamais remises (faille de surete)
# ---------------------------------------------------------------------------
ALARM_WINDOW_H = 24


def flag_unrestored_alarms(db, coll, event, year):
    """Pose `alarme_non_remise` (True / False) sur chaque mise hors service :
    True si aucune remise en service du MEME batiment dans les 24 h suivantes."""
    docs = list(db[coll].find({"event": event, "year": year, "alarme": {"$ne": None}, "ts": {"$ne": None}},
                              {"ts": 1, "alarme": 1, "alarme_non_remise": 1}).sort("ts", 1))
    n = 0
    for i, d in enumerate(docs):
        al = d.get("alarme") or {}
        if al.get("action") != "mise_hors_service":
            flag = False
        else:
            bat = norm(al.get("batiment"))
            limit = d["ts"] + timedelta(hours=ALARM_WINDOW_H)
            flag = True
            for e in docs[i + 1:]:
                if e["ts"] > limit:
                    break
                ea = e.get("alarme") or {}
                if ea.get("action") in ("remise_en_service", "les_deux") and (
                        not bat or difflib.SequenceMatcher(None, bat, norm(ea.get("batiment"))).ratio() >= 0.75):
                    flag = False
                    break
        if flag != d.get("alarme_non_remise"):
            db[coll].update_one({"_id": d["_id"]}, {"$set": {"alarme_non_remise": flag}})
        n += 1 if flag else 0
    return n


def resolve_declared_duplicates(db, coll, event, year):
    """Relie `doublon_declare_sql` a la fiche citee (pcorg.sql_id) : verite de
    terrain pour mesurer le detecteur de doublons probables."""
    n = 0
    for d in db[coll].find({"event": event, "year": year, "doublon_declare_sql": {"$ne": None}},
                           {"doublon_declare_sql": 1, "doublon_declare": 1}):
        target = db["pcorg"].find_one({"sql_id": d["doublon_declare_sql"]}, {"_id": 1})
        val = {"de": target["_id"], "sql_id": d["doublon_declare_sql"]} if target else None
        if val != d.get("doublon_declare"):
            db[coll].update_one({"_id": d["_id"]}, {"$set": {"doublon_declare": val}})
        n += 1 if val else 0
    return n


# ---------------------------------------------------------------------------
# Doublons probables
# ---------------------------------------------------------------------------
def _ts(v):
    return v if isinstance(v, datetime) else None


def dup_score(a, b):
    """Score 0-1 entre deux fiches structurees proches dans le temps."""
    same_place = bool(a.get("lieu_norm")) and a.get("lieu_norm") == b.get("lieu_norm") \
        and a.get("lieu_norm") != "non precise"
    t = difflib.SequenceMatcher(None, norm(a.get("type")), norm(b.get("type"))).ratio()
    r = difflib.SequenceMatcher(None, norm(a.get("resume")), norm(b.get("resume"))).ratio()
    return round(0.35 * same_place + 0.25 * t + 0.40 * r, 3)


def link_duplicates(db, coll, event, year):
    """Marque `doublon_probable` {de, score} sur la plus recente de deux fiches
    proches. Un « non » d'un admin (review.doublon == "non") n'est jamais repose."""
    docs = list(db[coll].find({"event": event, "year": year, "error": None, "ts": {"$ne": None}},
                              {"ts": 1, "type": 1, "resume": 1, "lieu_norm": 1, "category": 1,
                               "review": 1, "doublon_probable": 1}).sort("ts", 1))
    found = {}
    win = timedelta(minutes=DUP_WINDOW_MIN)
    for i, a in enumerate(docs):
        ta = _ts(a.get("ts"))
        if ta is None:
            continue
        for b in docs[i + 1:]:
            tb = _ts(b.get("ts"))
            if tb is None or tb - ta > win:
                break
            s = dup_score(a, b)
            if s >= DUP_THRESHOLD and (b["_id"] not in found or found[b["_id"]]["score"] < s):
                found[b["_id"]] = {"de": a["_id"], "score": s}
    n = 0
    for d in docs:
        if (d.get("review") or {}).get("doublon") == "non":
            continue
        new = found.get(d["_id"])
        if new != d.get("doublon_probable"):
            db[coll].update_one({"_id": d["_id"]}, {"$set": {"doublon_probable": new}})
        n += 1 if new else 0
    return n
