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
import math
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
# Lieux appris des corrections des admins (page /admin/gravite-fiches) : texte
# cite normalise (_id) -> lieu {name, lat, lng, layer, label}. Prime sur le
# referentiel des couches pour le lieu cite et le titre.
LEARNED_COLLECTION = "pcorg_lieux_appris"
MIN_NAME_CHARS = 4          # un nom plus court n'est rapproche que par motif dedie (T12, V3...)
# Un nom d'UN seul mot n'est retenu que si le lieu cite est exactement ce nom, et
# jamais pour ces mots generiques : « PS.Nord » tombait sur un lieu « Nord »,
# « Virage Porsche » sur « Porsche » (essai v3 du 27/10/2026).
GENERIC_WORDS = {"nord", "sud", "est", "ouest", "village", "paddock", "piste", "circuit", "parking", "acces",
                 "entree", "sortie", "porte", "portail", "tribune", "musee", "concert", "annexe", "family",
                 "service", "accueil", "parc", "zone", "poste", "secteur", "aire", "camping"}
# Voisinage d'une position : couche -> (champ du nom, libelle)
NEAR_LAYERS = {
    "portes": ("Name", "Porte"), "tribunes": ("NUMERO 2026", "Tribune"), "Virages": ("NAME", "Virage"),
    "giratoires": ("name", "Giratoire"), "passerelles": ("Nom", "Passerelle"),
    "postesecours": ("Nom", "Poste de secours"), "hospitalites": ("NOM", "Hospitalite"),
    "mp": ("name", "Poste commissaires"), "3p": ("Nom", "Portail 3P"),
}
AREA_LAYERS = {
    "terrains": ("Name", "Terrain / parking"), "fanzone": ("Nom", "Fan zone"),
    "campements_commissaires": ("Name", "Campement commissaires"), "batiment": ("name", "Batiment"),
}
# Rayon de recherche des points proches selon l'origine de la position : un
# carreau de carroyage fait ~100 m, un lieu cite est le point de la couche
NEAR_RADIUS_M = {"gps": 150, "carroyage": 150, "lieu": 100, "titre": 100}
ZONE_RANK = {"departement": 1, "zone": 2, "secteur": 3}
DUP_WINDOW_MIN = 45        # doublons : ecart maximal entre deux fiches
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


def _bbox(geom):
    """(min_lat, min_lng, max_lat, max_lng) d'un (Multi)Polygon : filtre rapide."""
    t, c = (geom or {}).get("type"), (geom or {}).get("coordinates")
    polys = [c] if t == "Polygon" else (c if t == "MultiPolygon" else [])
    pts = [p for poly in polys or [] for ring in poly or [] for p in ring or []
           if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not pts:
        return None
    return (min(p[1] for p in pts), min(p[0] for p in pts), max(p[1] for p in pts), max(p[0] for p in pts))


def _in_bbox(lat, lng, bb):
    return bb is None or (bb[0] <= lat <= bb[2] and bb[1] <= lng <= bb[3])


def dist_m(lat1, lng1, lat2, lng2):
    """Distance approchee en metres (equirectangulaire, suffisant a l'echelle du circuit)."""
    x = math.radians(lng2 - lng1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371000 * math.hypot(x, y)


def _zone_level(p):
    """Niveau d'une zone de competence d'apres ses proprietes Looker."""
    name = str(p.get("Nom") or p.get("name") or "")
    if name.lower().startswith("lot "):
        return "lot"
    if not p.get("Département"):
        return "departement"
    return "secteur" if p.get("Zone") else "zone"


def _feature_name(coll, p, key):
    name = p.get(key)
    if not name or str(name) == "None":
        return None
    if coll == "tribunes":       # « T12 FORD » : numero de l'annee + nom
        return ("T" + str(name).strip() + " " + str(p.get("NOM") or "")).strip()
    return str(name)


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
                                   "level": _zone_level(p), "postes": p.get("Poste") or "",
                                   "geom": f["geometry"], "bbox": _bbox(f["geometry"])})
        # Ce qui entoure un point : surfaces qui le contiennent, points proches
        self.near, self.polys = [], []
        for coll, (key, label) in NEAR_LAYERS.items():
            for f in (db[coll].find_one({}) or {}).get("features") or []:
                p = f.get("properties") or {}
                name = _feature_name(coll, p, key)
                pt = point_of(f.get("geometry"))
                if name and pt is not None:
                    self.near.append({"layer": coll, "label": label, "name": name, "lat": pt[0], "lng": pt[1]})
        for coll, (key, label) in AREA_LAYERS.items():
            for f in (db[coll].find_one({}) or {}).get("features") or []:
                name = (f.get("properties") or {}).get(key)
                if name and str(name) != "None" and f.get("geometry"):
                    self.polys.append({"layer": coll, "label": label, "name": str(name),
                                       "geom": f["geometry"], "bbox": _bbox(f["geometry"])})
        self.learned = []
        for l in db[LEARNED_COLLECTION].find({}):
            if l.get("lat") is not None and l.get("_id"):
                self.learned.append({"norm": l["_id"], "name": l.get("name") or l.get("texte"),
                                     "layer": l.get("layer") or "appris", "label": l.get("label") or "Lieu appris",
                                     "lat": float(l["lat"]), "lng": float(l["lng"])})
        self.learned.sort(key=lambda e: -len(e["norm"]))

    def match_learned(self, text):
        """Lieu appris cite dans un texte : texte identique, ou nom de plusieurs
        mots (ou de 6 lettres et plus) contenu dans le texte."""
        n = norm(text)
        if not n:
            return None
        t = " %s " % n
        for e in getattr(self, "learned", []):
            if n == e["norm"] or ((" " in e["norm"] or len(e["norm"]) >= 6) and " %s " % e["norm"] in t):
                return e
        return None

    def zone_of(self, lat, lng):
        """Zone de competence la plus FINE contenant le point (secteur > zone >
        departement) ; les lots de prestataires ne comptent pas ici."""
        best = None
        for z in self.zones:
            if z.get("level") == "lot" or not _in_bbox(lat, lng, z.get("bbox")) or not point_in_geom(lat, lng, z["geom"]):
                continue
            if best is None or ZONE_RANK.get(z.get("level"), 0) > ZONE_RANK.get(best.get("level"), 0):
                best = z
        return {"id": best["id"], "name": best["name"]} if best else None

    def surroundings(self, lat, lng, source):
        """Competence complete (departement / zone / secteur / lot), surfaces qui
        contiennent le point (terrain, parking, batiment, fan zone, campement) et
        point le plus proche de chaque couche (porte, tribune, portail...) dans un
        rayon qui depend de la precision de la position."""
        comp = {}
        for z in self.zones:
            if _in_bbox(lat, lng, z.get("bbox")) and point_in_geom(lat, lng, z["geom"]):
                lvl = z.get("level") or "zone"
                if lvl not in comp:
                    comp[lvl] = z["name"]
                    if lvl == "secteur" and z.get("postes"):
                        comp["postes"] = [x.strip() for x in z["postes"].splitlines() if x.strip()]
        dans = [{"layer": p["layer"], "label": p["label"], "name": p["name"]} for p in self.polys
                if _in_bbox(lat, lng, p["bbox"]) and point_in_geom(lat, lng, p["geom"])]
        radius = NEAR_RADIUS_M.get(source, NEAR_RADIUS_M["lieu"])
        best = {}
        for e in self.near:
            dm = dist_m(lat, lng, e["lat"], e["lng"])
            if dm <= radius and (e["layer"] not in best or dm < best[e["layer"]]["dist_m"]):
                best[e["layer"]] = {"layer": e["layer"], "label": e["label"], "name": e["name"], "dist_m": int(round(dm))}
        pres = sorted(best.values(), key=lambda x: x["dist_m"])
        return {"competence": comp or None, "dans": dans, "pres": pres}

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

    def locate(self, fiche, lieu=None, override=None):
        """Position de la fiche : correction d'un admin > GPS d'origine >
        carroyage > lieu appris > lieu cite (modele puis titre)."""
        if override and override.get("lat") is not None:
            return dict(override, source="operateur", confidence="haute")
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
            le = self.match_learned(txt)
            if le:
                return {"lat": le["lat"], "lng": le["lng"], "source": src, "layer": le["layer"],
                        "layer_label": le["label"], "name": le["name"], "confidence": "haute", "appris": le["norm"]}
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


# ---------------------------------------------------------------------------
# Chronologie lue dans l'historique du deroule (v5) : le modele ne devine plus
# des heures deja horodatees. comment_history = [{ts ISO avec decalage,
# operator, text}] (30 177 fiches sur 30 607 au 28/10/2026), ts = ouverture
# (UTC naif), close_ts = cloture.
# ---------------------------------------------------------------------------
_WRITTEN_HM = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*(?:h|H|:)\s*([0-5]\d)(?!\d)")
_TERMINE = re.compile(r"->\s*termin", re.I)


def _local(v):
    """datetime (UTC naif ou avec decalage) ou ISO -> datetime heure de Paris."""
    from zoneinfo import ZoneInfo
    from datetime import timezone
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(v, datetime):
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(ZoneInfo("Europe/Paris"))


def written_hours(*texts):
    """Heures HH:MM ecrites dans un texte (« 19h15 », « 2h12 », « 07H00 », « 19:15 »)."""
    out = set()
    for t in texts:
        for h, m in _WRITTEN_HM.findall(str(t or "")):
            out.add("%02d:%s" % (int(h), m))
    return out


def chronology(fiche):
    """{ouverture, termine, cloture (HH:MM heure de Paris), duree_min (ouverture ->
    passage a Termine, sinon cloture), maj (entrees du deroule hors statut),
    statuts [{heure, texte}], heures (toutes les heures connues : horodatages et
    heures ecrites)} ; None sans aucune heure."""
    opened = _local(fiche.get("ts"))
    closed = _local(fiche.get("close_ts"))
    entries = []
    for h in fiche.get("comment_history") or []:
        if isinstance(h, dict):
            at = _local(h.get("ts"))
            if at is not None:
                entries.append((at, str(h.get("text") or "")))
    entries.sort(key=lambda x: x[0])
    if opened is None and not entries:
        return None
    statuts, maj, termine = [], 0, None
    for at, txt in entries:
        m = re.search(r"Statut\s*:\s*([^\n\r]+)", txt)
        if m:
            statuts.append({"heure": at.strftime("%H:%M"), "texte": m.group(1).strip()[:60]})
            if termine is None and _TERMINE.search(m.group(1)):
                termine = at
            rest = txt.replace(m.group(0), "").strip()
            if rest:
                maj += 1
        elif txt.strip():
            maj += 1
    start = opened or entries[0][0]
    end = termine or closed
    duree = int(round((end - start).total_seconds() / 60)) if end and end >= start else None
    # Heures ecrites dans le texte (sans les entetes « 21/09/2025 02:00:21 , NOM »
    # du deroule, qui sont des horodatages de saisie)
    body = re.sub(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}", " ", str(fiche.get("comment") or ""))
    ecrites = written_hours(fiche.get("text"), body, *(t for _, t in entries))
    heures = {start.strftime("%H:%M")} | {at.strftime("%H:%M") for at, _ in entries} | ecrites
    return {"ouverture": start.strftime("%H:%M"), "date": start.strftime("%Y-%m-%d"),
            "termine": termine.strftime("%H:%M") if termine else None,
            "cloture": closed.strftime("%H:%M") if closed else None,
            "duree_min": duree, "maj": maj, "statuts": statuts[:8], "heures": sorted(heures),
            "ecrites": sorted(ecrites)}


def checked_steps(etapes, chrono):
    """Heures des etapes du modele gardees SEULEMENT si elles figurent dans la
    fiche (horodatage ou heure ecrite) : le modele recopiait l'heure du
    signalement dans engagement et sur place. Signalement absent = ouverture
    de la fiche, fin absente = passage a Termine. Rend (etapes, retirees)."""
    etapes = dict(etapes or {})
    if not chrono:
        return etapes, []
    known = set(chrono.get("heures") or [])
    removed = []
    for k in ETAPES:
        v = etapes.get(k)
        if v and v not in known:
            etapes[k] = None
            removed.append(k)
    # Arrivee sur place a l'heure meme du signalement, heure qui n'est ecrite
    # nulle part (seulement l'horodatage de la saisie) : recopie du modele
    sig = etapes.get("signalement")
    if sig and etapes.get("sur_place") == sig and sig not in set(chrono.get("ecrites") or []):
        etapes["sur_place"] = None
        removed.append("sur_place")
    if not etapes.get("signalement"):
        etapes["signalement"] = chrono.get("ouverture")
    if not etapes.get("fin") and chrono.get("termine"):
        etapes["fin"] = chrono["termine"]
    return etapes, removed


# ---------------------------------------------------------------------------
# Garde-fou des resumes (v5) : une issue ou un moyen qui n'est pas ecrit dans
# la fiche est retire. Mesure du 28/10/2026 : 97 resumes sur 119 citant
# evacuation / ambulance / stabilise / maitrise n'avaient pas le mot dans la fiche.
# ---------------------------------------------------------------------------
# racine cherchee dans le resume -> racines qui la justifient dans la fiche
CLAIMS = {
    "evacu": ("evacu", "transport", "ambulance", "hopital", "hospital", "vsav", "smur", "urgences", "chu"),
    "ambulance": ("ambulance", "ambu", "vsav", "asm", "transport", "evacu"),
    "hopital": ("hopital", "hospital", "chu", "clinique", "urgences"),
    "hospitalis": ("hopital", "hospital", "chu", "clinique", "urgences"),
    "transporte": ("transport", "ambulance", "evacu", "vsav", "hopital"),
    "stabilis": ("stabil",),
    "maitris": ("maitris", "eteint", "apais", "calme", "circonscrit"),
    "apais": ("apais", "calme", "maitris"),
    "interpel": ("interpel", "garde a vue", "police", "gendarm"),
    "pompier": ("pompier", "sdis", "vsav", "fpt", "ccf"),
    "sdis": ("pompier", "sdis", "vsav", "fpt", "ccf"),
    "samu": ("samu", "smur"),
    "smur": ("samu", "smur"),
    "police": ("police", "bac", "commissariat"),
    "gendarm": ("gendarm", "psig"),
    "deces": ("deces", "decede", "dcd", "mort"),
}
# Phrase de la consigne v4 recopiee dans 444 resumes sur 4 765 : remplacee par victime_personnel
_PERSONNEL_RE = re.compile(r"\b(aucune?|pas|non|ni|n'est|n'a|sans)\b.{0,40}\bpersonnel", re.I)
# Une phrase est coupee en propositions : « Pris en charge par le CMS, sans
# evacuation » garde le CMS (essai sur les v4 du 28/10/2026)
_CLAUSE_RE = re.compile(r",\s*|;\s*|\s+et\s+|\s+puis\s+")


def unsupported_claims(text, source):
    """Racines de CLAIMS presentes dans text mais justifiees par rien dans source."""
    t, s = " %s " % norm(text), " %s " % norm(source)
    out = []
    for claim, proofs in CLAIMS.items():
        if re.search(r" %s" % claim, t) and not any(re.search(r" %s" % p, s) for p in proofs):
            out.append(claim)
    return out


def _sentences(text):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", str(text or "").strip()) if x.strip()]


def guard_text(text, source, drop_personnel=True):
    """(texte garde, racines retirees) : chaque phrase qui affirme une issue ou un
    moyen non ecrit dans la fiche est retiree, ainsi que la phrase sur le
    personnel. Texte vide apres nettoyage -> "non precise"."""
    kept, removed = [], []
    for s in _sentences(text):
        end = s[-1] if s[-1] in ".!?" else "."
        body = s.rstrip(".!? ")
        seps = _CLAUSE_RE.findall(body)
        clauses = _CLAUSE_RE.split(body)
        out = []
        for i, c in enumerate(clauses):
            bad = unsupported_claims(c, source)
            if bad:
                removed += [b for b in bad if b not in removed]
                continue
            if drop_personnel and _PERSONNEL_RE.search(c):
                continue
            # le separateur qui precedait la proposition gardee (sauf en tete)
            out.append((seps[i - 1] if i and out else "") + c)
        s2 = "".join(out).strip()
        if s2:
            s2 = re.sub(r"^(et|puis)\s+", "", s2)
            kept.append(s2[0].upper() + s2[1:] + end)
    return (" ".join(kept) or "non precise"), removed


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


def guard_source(fiche):
    """Ce que la fiche ecrit vraiment : titre, deroule, service contacte, intervenants."""
    cc = fiche.get("content_category") or {}
    extra = [cc.get(k) for k in ("service_contacte", "intervenant1", "intervenant2", "intervenant3",
                                 "intervenant4", "intervenant5", "motif_intervention", "sous_classification")]
    return " ".join(str(x or "") for x in [fiche.get("text"), fiche.get("comment")] + extra)


# Familles d'incident (v5, liste fermee) : remplace le champ `type` libre
# (255 valeurs differentes sur 4 765 fiches v4, casse et accents compris)
FAMILLES = {
    "malaise": "Malaise", "blessure": "Blessure / chute", "secours_autre": "Autre secours",
    "rixe": "Rixe / bagarre", "agression": "Agression / menace", "vol": "Vol", "degradation": "Degradation",
    "intrusion": "Intrusion / acces non autorise", "comportement": "Ivresse / comportement / nuisance",
    "objet_suspect": "Objet ou colis suspect", "incendie": "Feu / incendie", "fuite_gaz": "Fuite gaz / produit",
    "electricite": "Panne electrique", "technique": "Panne technique / materiel", "eau_sanitaire": "Eau / sanitaires",
    "circulation": "Circulation / accident routier", "stationnement": "Stationnement / fourriere",
    "personne_egaree": "Personne / enfant egare", "objet_perdu": "Objet perdu / trouve",
    "acces_billetterie": "Acces / billetterie / controle", "alarme": "Alarme", "meteo": "Meteo",
    "ronde": "Ronde", "service": "Prise / fin de service", "exploitation": "Exploitation / logistique",
    "information": "Information / consigne", "autre": "Autre",
}
ISSUE_LABELS = {"resolu": "resolu", "transmis": "transmis", "sans_suite": "sans suite",
                "en_attente": "en attente"}


def reading_line(d, geo, chrono):
    """Ligne de lecture rapide : « Rixe ~20 pers. · Trucker Camp D2 · 02:00 · resolu »."""
    head = d.get("titre") if norm(d.get("titre")) not in ("", "non precise") else None
    head = head or FAMILLES.get(d.get("famille")) or d.get("type")
    lieu = (geo or {}).get("name") or (d.get("lieu") if norm(d.get("lieu")) not in ("", "non precise") else None)
    parts = [head, lieu if lieu and norm(lieu) not in norm(head) else None,
             (chrono or {}).get("ouverture"), ISSUE_LABELS.get(d.get("issue_code"))]
    return " · ".join(str(p) for p in parts if p)[:160] or None


def guarded_fields(fiche, d, chrono):
    """Champs gardes du modele (v5) recalcules depuis sa reponse brute
    (`*_modele`) : idempotent, et appliquable aux digests v4 sans le modele."""
    src = guard_source(fiche)
    out, removed = {}, []
    for k in ("resume", "issue", "titre"):
        raw = d.get(k + "_modele") if d.get(k + "_modele") is not None else d.get(k)
        if raw is None:
            continue
        txt, rm = guard_text(raw, src, drop_personnel=(k == "resume"))
        out[k] = txt
        out[k + "_modele"] = raw
        removed += ["%s:%s" % (k, r) for r in rm]
    raw_steps = d.get("etapes_modele") if d.get("etapes_modele") is not None else d.get("etapes")
    steps, rm = checked_steps(raw_steps, chrono)
    out["etapes"], out["etapes_modele"] = steps, raw_steps
    removed += ["etapes:%s" % r for r in rm]
    out["retouches"] = removed
    return out


def enrich(gaz, fiche, d, taxo=None):
    """Champs calcules a stocker dans le digest."""
    chrono = chronology(fiche)
    g = guarded_fields(fiche, d, chrono)
    d = dict(d, **g)
    geo = gaz.locate(fiche, d.get("lieu"), override=(d.get("lieu_corrige") or {}).get("geo"))
    around = gaz.surroundings(geo["lat"], geo["lng"], geo["source"]) if geo and hasattr(gaz, "near") else {}
    out = {"geo": geo, "zone": gaz.zone_of(geo["lat"], geo["lng"]) if geo else None,
           "competence": around.get("competence"), "dans": around.get("dans") or [], "pres": around.get("pres") or [],
           "delais": delays(d.get("etapes")), "completude": completeness(d, geo),
           "lieu_norm": norm(geo["name"]) if geo and geo.get("name") else norm(d.get("lieu")),
           "area": area_levels(fiche), "relances": _relances(fiche),
           "plaques": plates(fiche.get("text"), fiche.get("comment")),
           "doublon_declare_sql": declared_duplicate(fiche),
           "chrono": chrono, "ligne": reading_line(d, geo, chrono)}
    out.update(g)
    # Completude « heures » : une heure ECRITE par l'operateur ou une etape
    # au-dela du signalement (qui vaut maintenant l'ouverture de la fiche)
    comp = out["completude"]
    has_hours = bool((chrono or {}).get("ecrites")) or any((d.get("etapes") or {}).get(k)
                                                           for k in ("engagement", "sur_place"))
    if has_hours != ("heures" not in comp["manquants"]):
        comp["manquants"] = [m for m in comp["manquants"] if m != "heures"] + ([] if has_hours else ["heures"])
        comp["score"] = comp["max"] - len(comp["manquants"])
    if d.get("victime_personnel") is None and d.get("agression_agent"):
        out["victime_personnel"] = True
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


# ---------------------------------------------------------------------------
# Selection des fiches a controler dans un lot (marquees a la generation)
# ---------------------------------------------------------------------------
REVIEW_SHARE = 0.2          # 20 fiches a controler par lot de 100
REVIEW_MIN = 5
REVIEW_RANDOM = 2           # tirees au hasard : mesure de justesse non biaisee


def review_reasons(d):
    """(score, raisons) d'une fiche structuree : ce qui merite l'oeil d'un admin."""
    score, why = 0, []

    def add(points, reason):
        nonlocal score
        score += points
        why.append(reason)

    g = d.get("gravite") or 0
    if g >= 4:
        add(6 if g == 5 else 5, "gravite %d" % g)
    if d.get("alarme_non_remise"):
        add(5, "alarme jamais remise")
    if d.get("agression_agent"):
        add(4, "agression d'agent")
    if d.get("chaleur"):
        add(3, "chaleur")
    if d.get("secours"):
        add(3, "secours")
    if d.get("dysfonctionnement"):
        add(3, "dysfonctionnement")
    if d.get("jalon"):
        add(2, "jalon")
    if d.get("doublon_probable") or d.get("doublon_declare"):
        add(2, "doublon")
    if (d.get("geo") or {}).get("source") in ("lieu", "titre"):
        add(2, "position deduite")
    if d.get("sous_classification_proposee") and not (d.get("taxo") or {}).get("sous_classification"):
        add(1, "sous-classification proposee")
    if d.get("nature") == "routine" and g >= 2:
        add(2, "routine jugee grave")
    if d.get("nature") == "incident" and g == 1:
        add(1, "incident juge mineur")
    if (d.get("donnees_personnelles") or []) and g >= 3:
        add(1, "donnees personnelles")
    return score, why


def select_for_review(db, coll, batch, share=REVIEW_SHARE):
    """Marque `a_controler` {raisons, rang} sur ~20 % des fiches du lot : 2 au
    hasard (justesse non biaisee), puis les plus interessantes, avec au plus
    quelques fiches par couple nature x gravite (pas 20 rondes identiques).
    Rejouable : le marquage du lot est recalcule a chaque appel."""
    import random
    docs = list(db[coll].find({"batch": batch, "error": None, "gravite": {"$ne": None}}))
    if not docs:
        return 0
    k = min(len(docs), max(REVIEW_MIN, int(round(len(docs) * share))))
    rng = random.Random(batch)                       # meme lot = meme tirage
    picks = {}
    # Deja marquees ET controlees : on les garde (re-selection en fin de traitement)
    for d in docs:
        if d.get("a_controler") and d.get("review"):
            picks[d["_id"]] = d["a_controler"].get("raisons") or ["diversite"]
    n_random = sum(1 for w in picks.values() if "aleatoire" in w)
    pool = [d for d in docs if d["_id"] not in picks]
    for d in rng.sample(pool, max(0, min(REVIEW_RANDOM - n_random, k - len(picks), len(pool)))):
        picks[d["_id"]] = ["aleatoire"]
    scored = sorted(((review_reasons(d), d) for d in docs if d["_id"] not in picks),
                    key=lambda x: -x[0][0])
    cap = max(2, k // 5)
    buckets = {}
    for (score, why), d in scored:
        if len(picks) >= k:
            break
        key = (d.get("nature"), d.get("gravite"))
        if buckets.get(key, 0) >= cap and score < 5:   # les cas graves passent toujours
            continue
        buckets[key] = buckets.get(key, 0) + 1
        picks[d["_id"]] = why or ["diversite"]
    # Complement si la diversite a trop filtre : toujours dans le couple
    # nature x gravite le moins represente, incidents d'abord, puis score
    rest = [(score, why, d) for (score, why), d in scored if d["_id"] not in picks]
    while len(picks) < k and rest:
        rest.sort(key=lambda x: (buckets.get((x[2].get("nature"), x[2].get("gravite")), 0),
                                 x[2].get("nature") != "incident", -x[0]))
        score, why, d = rest.pop(0)
        key = (d.get("nature"), d.get("gravite"))
        buckets[key] = buckets.get(key, 0) + 1
        picks[d["_id"]] = why or ["diversite"]
    db[coll].update_many({"batch": batch}, {"$set": {"a_controler": None}})
    for rank, (fid, why) in enumerate(picks.items(), 1):
        db[coll].update_one({"_id": fid}, {"$set": {"a_controler": {"raisons": why, "rang": rank}}})
    return len(picks)


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


# Immatriculations : SIV (AB-123-CD), FNI (1234 AB 72, 6513-LFL), britannique (YR25 KYP)
_PLATE_RES = (
    re.compile(r"\b([A-Z]{2})[ -]?(\d{3})[ -]?([A-Z]{2})\b"),
    # FNI : jamais colle a une autre plaque ni suivi d'une date / heure (« KG 10/06 »)
    re.compile(r"(?<![A-Z0-9-])(\d{2,4})[ -]?([A-Z]{2,3})[ -]?(\d{2}|2A|2B)(?![A-Z0-9/:-])"),
    re.compile(r"(?<![A-Z0-9-])(\d{3,4})-([A-Z]{2,3})(?![A-Z0-9-])"),
    re.compile(r"\b([A-Z]{2}\d{2})([A-Z]{3})\b"),
)
_PLATE_STOP = {"ET", "OU", "AU", "AUX", "LE", "LA", "LES", "DE", "DU", "DES", "EN", "BIS", "TER", "SUR", "PAR",
               "MIN", "KM", "PAX", "PERS", "UN", "UNE", "ME", "MN", "HEU", "SEC", "PAS", "VL", "PL", "MAX",
               "ANS", "AN", "HH", "HS", "EST", "PEC", "CLE", "EME", "MAI", "CIK", "ACO", "ADS", "QUI", "NOV",
               "JUI", "AOU", "SEP", "OCT", "DEC", "JAN", "FEV", "MAR", "AVR", "ER", "RE", "NE"}


def plates(*texts):
    """Plaques citees (normalisees sans separateur) : deux fiches fourriere au
    meme texte mais aux plaques differentes ne sont PAS des doublons."""
    t = unicodedata.normalize("NFKD", " ".join(str(x or "") for x in texts)).encode("ascii", "ignore").decode().upper()
    out = set()
    for rx in _PLATE_RES:
        for m in rx.finditer(t):
            p = "".join(m.groups())
            if any(g in _PLATE_STOP for g in m.groups()):        # « T11 et 12 », « CC26 bis »
                continue
            if re.search(r"\d", p) and re.search(r"[A-Z]", p) and len(p) >= 6:
                out.add(p)
    # « 505-BRA-44 » est aussi lu « 505-BRA » : on garde la forme la plus longue
    return sorted(p for p in out if not any(p != q and p in q for q in out))


def dup_score(a, b):
    """Score 0-1 entre deux fiches structurees proches dans le temps. Les
    plaques tranchent : differentes = pas un doublon, une commune = doublon."""
    pa, pb = set(a.get("plaques") or []), set(b.get("plaques") or [])
    if pa and pb:
        return 0.95 if pa & pb else 0.0
    same_place = bool(a.get("lieu_norm")) and a.get("lieu_norm") == b.get("lieu_norm") \
        and a.get("lieu_norm") != "non precise"
    t = difflib.SequenceMatcher(None, norm(a.get("type")), norm(b.get("type"))).ratio()
    r = difflib.SequenceMatcher(None, norm(a.get("resume")), norm(b.get("resume"))).ratio()
    return round(0.35 * same_place + 0.25 * t + 0.40 * r, 3)


def link_duplicates(db, coll, event, year):
    """Marque `doublon_probable` {de, score} sur la plus recente de deux fiches
    proches. Un « non » d'un admin (review.doublon == "non") n'est jamais repose."""
    docs = list(db[coll].find({"event": event, "year": year, "error": None, "ts": {"$ne": None}},
                              {"ts": 1, "type": 1, "resume": 1, "lieu_norm": 1, "category": 1, "plaques": 1,
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
