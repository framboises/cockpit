"""
patrols_propose.py - Propositions de patrouilles d'apres l'historique
(livraison 4, docs/claude/patrouilles.md section 6).

Entree : fiches PCS (PC Securite, defaut) ou PCO (PC Org), dominante
(surete, secours, incendie, prevention, generale), duree disponible,
deplacement (a pied / vehicule), evenement (SAISON par defaut, une epreuve
ou tout l'historique, toutes editions), option "meme tranche horaire".
Sortie : un parcours qui tient dans la duree, chaque arret explique.

Moteur 100 % code, toujours disponible :
  1. position de chaque fiche, de la plus sure a la moins sure : position
     qualifiee par Alfred (pcorg_digests.geo), GPS, carroyage, lieu cite dans
     le titre ou la zone de saisie reconnu parmi les POINTS DE PATROUILLE
     (nom + autres noms) puis le referentiel des lieux (pcorg_enrich) ; points
     d'une ronde qualifiee (ronde.points) places de la meme facon ;
  2. poids d'apres les champs qualifies : fiche ignoree / doublon / prise de
     service / jalon = 0 ; information = 0,3 ; ronde RAS = 0,5, ronde avec
     anomalie = 3 ; incident = gravite (validee d'abord) 1/2/4/8/16, sinon
     urgence ; agression d'agent x1,5 ; victimes ; editions recentes et meme
     tranche horaire favorisees ;
  3. foyers : regroupement a 60 m (a pied) / 120 m (vehicule), nom = lieu le
     plus cite du foyer (point, referentiel, dans / pres d'Alfred) ;
  4. parcours glouton dans la duree, complement par la couche de la
     dominante, ordre optimise et trajet reel Valhalla.

Les fiches PCS n'ont ni GPS ni carroyage : leur couverture vient des lieux
cites (points de patrouille avec leurs autres noms) puis, au fil des nuits,
de la qualification d'Alfred. La reponse donne la part de fiches placees,
qualifiees, et les lieux cites non places (a creer comme points).

"Affiner avec l'IA" (admins) : Claude relit les foyers (resumes Alfred ou
textes masques) et revoit leur score (pcorg_summary.call_claude, budget et
couts suivis).
"""
import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import patrols as P

logger = logging.getLogger(__name__)
PARIS = ZoneInfo("Europe/Paris")
ALL_EVENTS = "__all__"

RX = {
    "secours": r"malaise|bless|secours|pompier|samu|sdis|chute|inconscient|evacu|douleur|saign|cms|infirm",
    "incendie": r"incendi|\bfeu\b|feux|fum[ée]e|d[ée]part de feu|extincteur|br[uû]l|alarme incendie|\bssi\b|d[ée]senfumage|d[ée]tecteur",
    "prevention": r"travaux|chantier|prestataire|montage|d[ée]montage|accident du travail|nacelle|\bengin|levage|"
                  r"[ée]chafaud|plan de pr[ée]vention|ppsps|chute de hauteur|\bepi\b|casque",
}
# Par source de fiches : categories retenues par dominante (None = toutes les
# categories de la source, filtre par motif), couche de complement
DOMINANTE_RULES = {
    "pcs": {
        "surete": {"categories": ["PCS.Surete"], "layer": "3p"},
        "secours": {"categories": None, "regex": RX["secours"], "digest": "secours", "layer": "postesecours"},
        "incendie": {"categories": None, "regex": RX["incendie"], "digest": "alarme", "layer": "hospitalites"},
        "prevention": {"categories": None, "regex": RX["prevention"], "layer": None},
        "generale": {"categories": ["PCS.Surete"], "layer": None},
    },
    "pco": {
        "surete": {"categories": ["PCO.Securite"], "layer": "3p"},
        "secours": {"categories": ["PCO.Secours"], "layer": "postesecours"},
        "incendie": {"categories": None, "regex": RX["incendie"], "digest": "alarme", "layer": "hospitalites"},
        "prevention": {"categories": None, "regex": RX["prevention"], "layer": None},
        "generale": {"categories": ["PCO.Securite", "PCO.Secours", "PCO.Technique", "PCO.Flux"], "layer": "tribunes"},
    },
}
SOURCE_PREFIX = {"pcs": "PCS.", "pco": "PCO."}
GRAV_WEIGHT = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}
URG_WEIGHT = {"IMP": 1, "UR": 2, "UA": 4, "EU": 8}
TRAVEL = {   # vitesse moyenne sur site (km/h), detour, arret a chaque point (s), rayon de foyer (m)
    "pedestrian": {"kmh": 4.5, "detour": 1.35, "dwell": 120, "radius": 60},
    "vehicle": {"kmh": 20.0, "detour": 1.4, "dwell": 90, "radius": 120},
}
NAME_LAYERS = ("tribunes", "3p", "portes", "sanitaires", "postesecours", "batiment", "terrains", "hospitalites",
               "Fan Zone et Village", "passerelles", "box_numerotation")
MAX_STOPS = 25
RX_RONDE = re.compile(r"\bronde", re.I)
RX_PRISE = re.compile(r"prise de service|fin de service|prise de poste|fin de poste|rel[eè]ve\b", re.I)
GENERIC_AREAS = {"_mc pcs", "_mc pco", "sud", "nord", "batiment", "acces"}


# ---------------------------------------------------------------------------
# Reperes : points de patrouille (nom + autres noms) et referentiel des lieux
# ---------------------------------------------------------------------------

class Locator:
    """Place un texte (titre, zone de saisie, point de ronde) : points de
    patrouille d'abord (les lieux que l'equipe a places, avec leurs autres
    noms), puis le referentiel des lieux de pcorg_enrich."""

    def __init__(self, db):
        import pcorg_enrich as PE
        self.PE = PE
        self.gaz = PE.Gazetteer(db)
        self.aliases = []      # (norm, nom affiche, lat, lng)
        for p in db["patrol_points"].find({"active": {"$ne": False}}, {"name": 1, "aliases": 1, "lat": 1, "lng": 1}):
            for a in [p.get("name")] + list(p.get("aliases") or []):
                n = PE.norm(a or "")
                if len(n) >= 2:
                    self.aliases.append((n, p.get("name"), p["lat"], p["lng"]))
        self.aliases.sort(key=lambda x: -len(x[0]))       # noms longs d'abord

    def text(self, txt):
        """(lat, lng, nom, source) ou None."""
        if not txt:
            return None
        t = " %s " % self.PE.norm(txt)
        if not t.strip():
            return None
        for n, name, lat, lng in self.aliases:
            if " %s " % n in t:
                return lat, lng, name, "point"
        e, _conf = self.gaz.match_text(txt)
        if e:
            name = e["name"]
            if str(name).strip().isdigit():         # tribune "17" -> "Tribune 17"
                name = "%s %s" % (str(e.get("label") or "Tribune").split()[0], name)
            return e["lat"], e["lng"], name, "referentiel"
        return None


def _area_short(f):
    a = f.get("area")
    desc = a.get("desc") if isinstance(a, dict) else None
    if not desc:
        return None
    return re.sub(r"^\d+\s*-\s*", "", str(desc).split("/")[-1]).strip() or None


def _locate(f, dg, loc, grid):
    """(lat, lng, nom du lieu ou None, source, lieu cite non place ou None)."""
    geo = (dg or {}).get("geo") or {}
    if geo.get("lat") is not None and geo.get("confidence") != "basse":
        name = geo.get("name") or next((x.get("name") for x in (dg.get("dans") or []) if x.get("name")), None) \
            or next((x.get("name") for x in (dg.get("pres") or []) if x.get("name")), None)
        return float(geo["lat"]), float(geo["lng"]), name, "alfred", None
    g = f.get("gps") or {}
    c = g.get("coordinates")
    if g.get("type") == "Point" and isinstance(c, list) and len(c) >= 2:
        try:
            return float(c[1]), float(c[0]), None, "gps", None
        except (TypeError, ValueError):
            pass
    car = str((f.get("content_category") or {}).get("carroye") or "").upper().replace(" ", "")
    m = re.match(r"^([A-Z]{1,2}\d{1,3})", car)
    if m and m.group(1) in grid:
        lat, lng = grid[m.group(1)]
        return lat, lng, None, "carroyage", None
    lieu = (dg or {}).get("lieu")
    for txt in (lieu if lieu and lieu != "non precise" else None, f.get("text"), _area_short(f)):
        hit = loc.text(txt)
        if hit:
            return hit[0], hit[1], hit[2], hit[3], None
    # Lieu cite non place : le lieu d'Alfred, sinon la zone de saisie si elle n'est pas generique
    miss = lieu if lieu and lieu != "non precise" else None
    area = _area_short(f)
    if not miss and area and area.lower() not in GENERIC_AREAS:
        miss = area
    return None, None, None, None, miss


def events_with_history(db, source="pcs"):
    """Evenements de la source, du plus fourni au moins, avec leurs annees."""
    prefix = SOURCE_PREFIX.get(source, "PCS.")
    pipe = [{"$match": {"category": {"$regex": "^" + re.escape(prefix)}}},
            {"$group": {"_id": "$event", "n": {"$sum": 1}, "years": {"$addToSet": "$year"}}},
            {"$sort": {"n": -1}}]
    out = [{"event": r["_id"], "n": r["n"], "years": sorted(y for y in r["years"] if y)}
           for r in db["pcorg"].aggregate(pipe) if r["_id"]]
    total = sum(e["n"] for e in out)
    return [{"event": ALL_EVENTS, "n": total, "years": sorted({y for e in out for y in e["years"]})}] + out


def _fiches(db, source, event, dominante):
    rule = (DOMINANTE_RULES.get(source) or DOMINANTE_RULES["pcs"]).get(dominante) \
        or DOMINANTE_RULES["pcs"]["generale"]
    prefix = SOURCE_PREFIX.get(source, "PCS.")
    q = {"content_category.patrol_run": {"$exists": False}}
    if event and event != ALL_EVENTS:
        q["event"] = event
    if rule.get("categories"):
        q["category"] = {"$in": rule["categories"]}
    else:
        q["category"] = {"$regex": "^" + re.escape(prefix)}
    proj = {"gps": 1, "content_category": 1, "category": 1, "niveau_urgence": 1, "ts": 1, "year": 1, "event": 1,
            "text": 1, "text_full": 1, "area": 1}
    return list(db["pcorg"].find(q, proj)), rule


def _hour(f):
    ts = f.get("ts")
    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    loc = ts.astimezone(PARIS)
    return loc.hour + loc.minute / 60.0


def _hour_gap(a, b):
    d = abs(a - b) % 24
    return min(d, 24 - d)


def _weight(f, dg, dominante):
    """(poids, genre) d'apres les champs qualifies ; genre : incident | ronde |
    anomalie | information | routine."""
    text = f.get("text_full") or f.get("text") or ""
    if dg:
        rv = dg.get("review") or {}
        if rv.get("verdict") == "ignoree" or dg.get("ignoree") or dg.get("doublon_declare") \
                or (rv.get("doublon") in ("confirme", True)):
            return 0, None
        if dg.get("prise_service") or dg.get("jalon"):
            return 0, None
        if dg.get("ronde"):
            if (dg["ronde"] or {}).get("resultat") == "anomalie":
                return 3, "anomalie"
            return 0.5, "ronde"
        nature = dg.get("nature")
        if nature == "information":
            return 0.3, "information"
        if nature == "routine":
            return 0.5, "routine"
        grav = (rv.get("gravite_validee")) or dg.get("gravite")
        w = GRAV_WEIGHT.get(grav) or URG_WEIGHT.get(f.get("niveau_urgence")) or 1
        if dg.get("agression_agent"):
            w *= 1.5
        if dominante == "secours" and dg.get("victimes"):
            try:
                w *= 1 + 0.2 * min(5, int(dg["victimes"]))
            except (TypeError, ValueError):
                pass
        return w, "incident"
    if RX_PRISE.search(text):
        return 0, None
    if RX_RONDE.search(text):
        return 0.5, "ronde"
    return URG_WEIGHT.get(f.get("niveau_urgence")) or 1, "incident"


def build_foyers(db, event, dominante, travel, hour=None, source="pcs"):
    """Foyers : [{id, lat, lng, score, n, editions, gravite_moy, top_sous,
    types, kinds, fiche_ids, lieu}], du plus fort au plus faible, et les
    statistiques de couverture."""
    fiches, rule = _fiches(db, source, event, dominante)
    stats = {"fiches": 0, "placees": 0, "qualifiees": 0, "non_placees": [], "par_source": {}}
    if not fiches:
        return [], stats
    digests = {}
    try:
        import pcorg_digest as PD
        ids = [f["_id"] for f in fiches]
        for i in range(0, len(ids), 5000):
            for d in db[PD.COLL].find({"_id": {"$in": ids[i:i + 5000]}, "error": None}):
                digests[d["_id"]] = d
    except Exception as e:
        logger.debug("digests indisponibles : %s", e)
    rx = re.compile(rule["regex"], re.I) if rule.get("regex") else None
    loc = Locator(db)
    grid = loc.gaz.grid
    years = [f.get("year") for f in fiches if isinstance(f.get("year"), int)]
    last_year = max(years) if years else None
    pts, missing, per_src = [], Counter(), Counter()
    n_sel = n_qual = 0
    for f in fiches:
        dg = digests.get(f["_id"])
        if rx:
            hay = "%s %s %s" % (f.get("text_full") or f.get("text") or "",
                                (f.get("content_category") or {}).get("sous_classification") or "",
                                (dg or {}).get("type") or "")
            block = rule.get("digest")
            if not rx.search(hay) and not (dg and block and dg.get(block)):
                continue
        w, kind = _weight(f, dg, dominante)
        if not w:
            continue
        n_sel += 1
        if dg:
            n_qual += 1
        if last_year and isinstance(f.get("year"), int):
            w *= {0: 1.0, 1: 0.8}.get(last_year - f["year"], 0.6)
        if hour is not None:
            h = _hour(f)
            if h is not None:
                w *= 1.0 if _hour_gap(h, hour) <= 1.5 else 0.35
        lat, lng, lieu, src, miss = _locate(f, dg, loc, grid)
        grav = ((dg or {}).get("review") or {}).get("gravite_validee") or (dg or {}).get("gravite")
        if lat is None:
            if miss:
                missing[str(miss)[:50]] += 1
        else:
            per_src[src] += 1
            pts.append({"f": f, "lat": lat, "lng": lng, "src": src, "w": w, "grav": grav, "dg": dg, "kind": kind,
                        "lieu": lieu})
        # Points d'une ronde qualifiee : chaque lieu verifie compte un peu
        for name in ((dg or {}).get("ronde") or {}).get("points") or []:
            hit = loc.text(name)
            if hit:
                pts.append({"f": f, "lat": hit[0], "lng": hit[1], "src": hit[3], "w": 0.5, "grav": None, "dg": dg,
                            "kind": "ronde", "lieu": hit[2]})
    stats.update(fiches=n_sel, placees=sum(per_src.values()), qualifiees=n_qual, par_source=dict(per_src),
                 non_placees=[{"lieu": k, "n": v} for k, v in missing.most_common(15)])
    pts.sort(key=lambda p: -p["w"])
    radius = TRAVEL[travel]["radius"]
    clusters = []
    for p in pts:
        best = None
        for c in clusters:
            if P.haversine_m(p["lat"], p["lng"], c["lat"], c["lng"]) <= radius:
                best = c
                break
        if not best:
            best = {"lat": p["lat"], "lng": p["lng"], "items": []}
            clusters.append(best)
        best["items"].append(p)
        n = len(best["items"])
        best["lat"] += (p["lat"] - best["lat"]) / n
        best["lng"] += (p["lng"] - best["lng"]) / n
    out = []
    for i, c in enumerate(clusters):
        items = c["items"]
        eds = sorted({it["f"].get("year") for it in items if it["f"].get("year")})
        score = sum(it["w"] for it in items) * (1 + 0.3 * (len(eds) - 1))
        gravs = [it["grav"] for it in items if it["grav"]]
        subs, types, kinds, lieux = Counter(), Counter(), Counter(), Counter()
        for it in items:
            cc = it["f"].get("content_category") or {}
            # Motif d'abord ; la classification PCS ("Intervention Surete") ne dit rien
            s = cc.get("motif_intervention") or cc.get("sous_classification")
            if s:
                subs[str(s)] += 1
            if (it["dg"] or {}).get("type"):
                types[str(it["dg"]["type"])] += 1
            kinds[it["kind"]] += 1
            if it["lieu"]:
                lieux[str(it["lieu"])] += 1
        fiche_ids = []
        for it in sorted(items, key=lambda it: -it["w"]):
            if it["f"]["_id"] not in fiche_ids:
                fiche_ids.append(it["f"]["_id"])
            if len(fiche_ids) >= 12:
                break
        out.append({
            "id": "f%d" % i, "lat": c["lat"], "lng": c["lng"], "score": round(score, 2),
            "n": len({it["f"]["_id"] for it in items}), "editions": eds,
            "gravite_moy": round(sum(gravs) / len(gravs), 1) if gravs else None,
            "top_sous": [t[0] for t in subs.most_common(3)], "types": [t[0] for t in types.most_common(3)],
            "kinds": dict(kinds), "sources": sorted({it["src"] for it in items}),
            "lieu": lieux.most_common(1)[0][0] if lieux else None, "fiche_ids": fiche_ids,
        })
    out.sort(key=lambda c: -c["score"])
    return out, stats


def _gazetteer(db):
    items = []
    for coll in NAME_LAYERS:
        try:
            for it in P.layer_items(db, coll) or []:
                if "sans nom" not in it["name"]:      # un repere doit avoir un nom
                    items.append(it)
        except Exception:
            continue
    return items


def _kinds_label(k):
    k = k or {}
    parts = []
    for key, label in (("incident", "incident"), ("anomalie", "anomalie de ronde"), ("ronde", "ronde"),
                       ("routine", "routine"), ("information", "info")):
        if k.get(key):
            parts.append("%d %s%s" % (k[key], label, "s" if k[key] > 1 and not label.endswith("s") else ""))
    return ", ".join(parts[:2])


def _name_foyer(c, gaz):
    detail = c["types"][0] if c.get("types") else (c["top_sous"][0] if c.get("top_sous") else _kinds_label(c.get("kinds")))
    sub = (" - " + detail) if detail else ""
    if c.get("lieu"):
        return (str(c["lieu"])[:60] + sub[:40])
    best, bd = None, 1e9
    for it in gaz:
        d = P.haversine_m(c["lat"], c["lng"], it["lat"], it["lng"])
        if d < bd:
            best, bd = it, d
    if best and bd <= 150:
        return ("Pres de %s" % best["name"])[:70] + sub[:40]
    return ("Foyer %.5f, %.5f" % (c["lat"], c["lng"])) + sub[:40]


def _leg_s(a, b, travel):
    t = TRAVEL[travel]
    d = P.haversine_m(a["lat"], a["lng"], b["lat"], b["lng"]) * t["detour"]
    return d / (t["kmh"] / 3.6)


def _greedy(cands, budget_s, travel, loop):
    """Parcours qui tient dans `budget_s` : depart = meilleur candidat, puis
    insertion du candidat au meilleur rapport score / temps ajoute."""
    if not cands:
        return []
    dwell = TRAVEL[travel]["dwell"]
    tour = [cands[0]]
    left = cands[1:]

    def tour_time(t):
        s = sum(_leg_s(t[i], t[i + 1], travel) for i in range(len(t) - 1)) + dwell * len(t)
        if loop and len(t) > 1:
            s += _leg_s(t[-1], t[0], travel)
        return s

    while left and len(tour) < MAX_STOPS:
        base = tour_time(tour)
        best = None
        for c in left:
            for pos in range(1, len(tour) + 1):
                t2 = tour[:pos] + [c] + tour[pos:]
                add = tour_time(t2) - base
                if base + add > budget_s:
                    continue
                ratio = c["score"] / max(add, 1.0)
                if not best or ratio > best[0]:
                    best = (ratio, c, pos)
        if not best:
            break
        tour.insert(best[2], best[1])
        left.remove(best[1])
    return tour


def _apply_review(foyers, review, travel):
    """Scores de l'IA (0-100) rattaches aux foyers PAR POSITION : un avis paye
    reste valable apres l'ajout ou le deplacement d'un lieu (les numeros de
    foyers changent, pas leurs positions). Foyer non relu (nouveau lieu) :
    score du code ramene sur 0-50, signale."""
    radius = TRAVEL[travel]["radius"] * 1.5
    top_code = max([f["score"] for f in foyers] or [1]) or 1
    for f in foyers:
        best, bd = None, radius
        for r in review:
            d = P.haversine_m(f["lat"], f["lng"], r["lat"], r["lng"])
            if d <= bd:
                best, bd = r, d
        f["score_code"] = f["score"]
        if best:
            f["score"] = float(best["score"])
            if best.get("reason"):
                f["ai_reason"] = best["reason"]
        else:
            f["score"] = round(50.0 * f["score"] / top_code, 1)
            f["ai_reason"] = "Non relu par l'IA (lieu place apres son avis) : score du moteur."
    foyers.sort(key=lambda c: -c["score"])


def review_key(source, event, dominante, travel):
    return "%s|%s|%s|%s" % (source, event, dominante, travel)


def get_review(db, source, event, dominante, travel):
    return db["patrol_ai_reviews"].find_one({"_id": review_key(source, event, dominante, travel)})


def save_review(db, source, event, dominante, travel, foyers, scores, reasons, comment, by, model):
    now = datetime.now(timezone.utc)
    doc = {"_id": review_key(source, event, dominante, travel), "at": now, "by": by, "model": model,
           "comment": comment, "expires_at": now + timedelta(days=60),
           "foyers": [{"lat": f["lat"], "lng": f["lng"], "name": f.get("lieu"), "score": scores[f["id"]],
                       "reason": reasons.get(f["id"]) or ""} for f in foyers if f["id"] in scores]}
    try:
        db["patrol_ai_reviews"].create_index("expires_at", expireAfterSeconds=0)
    except Exception:
        pass
    db["patrol_ai_reviews"].replace_one({"_id": doc["_id"]}, doc, upsert=True)
    return doc


def propose(db, dominante, duration_min, travel, event, loop=False, hour=None, ai_review=None, source="pcs"):
    """Proposition complete : {stops, estimate, foyers (carte), stats, notes}.
    `ai_review` : foyers notes par l'IA [{lat, lng, score, reason}]."""
    source = source if source in SOURCE_PREFIX else "pcs"
    travel = travel if travel in TRAVEL else "pedestrian"
    budget = max(5, min(240, int(duration_min or 45))) * 60
    foyers, stats = build_foyers(db, event, dominante, travel, hour, source)
    if ai_review:
        _apply_review(foyers, ai_review, travel)
    notes = []
    gaz = _gazetteer(db)
    for f in foyers[:60]:
        f["name"] = _name_foyer(f, gaz)
        f["kind"] = "foyer"
    cands = [f for f in foyers[:40] if f["score"] > 0 and (f["n"] >= 2 or f["score"] >= 4)]
    if len(cands) < 5:      # dominante peu fournie : les foyers d'une seule fiche comptent aussi
        cands = [f for f in foyers[:40] if f["score"] > 0]
    if stats["fiches"]:
        pct = round(100.0 * stats["placees"] / stats["fiches"])
        notes.append("%d fiche(s) retenue(s), %d %% placee(s), %d qualifiee(s) par Alfred." % (
            stats["fiches"], pct, stats["qualifiees"]))
        if pct < 60 and stats["non_placees"]:
            notes.append("Placez les lieux cites ci-dessous (bouton Placer) pour mieux couvrir l'historique.")
    tour = _greedy(cands, budget, travel, loop)
    # Complement : objets de la couche de la dominante pres du parcours
    rule = (DOMINANTE_RULES.get(source) or {}).get(dominante) or {}
    layer = rule.get("layer")
    if layer and len(tour) < MAX_STOPS:
        try:
            objs = P.layer_items(db, layer) or []
        except Exception:
            objs = []
        near = []
        for o in objs:
            if "sans nom" in o["name"]:
                continue
            d = min((P.haversine_m(o["lat"], o["lng"], s["lat"], s["lng"]) for s in tour), default=1e9) if tour else 0
            if not tour or d <= 400:
                near.append(dict(o, kind="layer", layer=layer, score=0.5, d=d))
        near.sort(key=lambda o: o["d"])
        # 6 objets au plus : un complement, pas l'essentiel de la ronde
        extra = _greedy(tour + near[:6], budget, travel, loop) if tour else _greedy(near[:6], budget, travel, loop)
        added = [s for s in extra if s.get("kind") == "layer"]
        if added:
            tour = extra
            notes.append("%d objet(s) de la couche %s ajoute(s) pour remplir la duree." % (len(added), layer))
    base = {"foyers": [{k: f.get(k) for k in ("id", "lat", "lng", "score", "n", "name")} for f in foyers[:80]],
            "stats": stats, "budget_s": budget, "dominante": dominante, "travel": travel, "loop": bool(loop),
            "event": event, "source": source}
    if not tour:
        return dict(base, stops=[], estimate=None, notes=notes + ["Aucun foyer place pour cette dominante."])
    # Ordre optimise (depart fixe) puis trajet reel ; retrait si trop long
    estimate = None
    for _ in range(MAX_STOPS + 5):      # retrait arret par arret jusqu'a tenir la duree
        coords = [(s["lat"], s["lng"]) for s in tour]
        if len(tour) >= 3:
            m, err = P._matrix(coords, travel)
            if not err:
                order = P.best_order(m, loop)
                tour = [tour[i] for i in order]
                coords = [(s["lat"], s["lng"]) for s in tour]
        route_coords = coords + ([coords[0]] if loop and len(coords) > 1 else [])
        est, err = P.route_through(route_coords, travel)
        if err == "no_route" and len(tour) > 2:
            # Un arret injoignable dans ce mode (zone pietonne en vehicule...) : on le retire
            bad = next((i for i in range(1, len(tour))
                        if P.route_through([coords[i - 1], coords[i]], travel)[1] == "no_route"), None)
            if bad is not None:
                notes.append("%s retire : injoignable %s." % (tour[bad].get("name"), "a pied" if travel == "pedestrian" else "en vehicule"))
                tour.pop(bad)
                continue
        if err:
            notes.append("Trajet reel non calcule (%s) : estimation a vol d'oiseau." % err)
            break
        total = est["duration_s"] + TRAVEL[travel]["dwell"] * len(tour)
        estimate = dict(est, total_s=total)
        if total <= budget * 1.1 or len(tour) <= 2:
            break
        worst = min(range(1, len(tour)), key=lambda i: tour[i]["score"])     # jamais le depart
        tour.pop(worst)
    stops = []
    for s in tour:
        st = {"kind": s.get("kind", "foyer"), "name": s.get("name"), "lat": s["lat"], "lng": s["lng"],
              "score": s.get("score")}
        if st["kind"] == "foyer":
            st.update(id=s["id"], n=s["n"], lieu=s.get("lieu"), editions=s["editions"], gravite_moy=s["gravite_moy"],
                      top_sous=s["top_sous"], types=s.get("types"), kinds=s.get("kinds"), fiche_ids=s["fiche_ids"],
                      sources=s["sources"], score_code=s.get("score_code"), ai_reason=s.get("ai_reason"))
        else:
            st.update(layer=s.get("layer"), fid=s.get("fid"), info=s.get("info"))
        stops.append(st)
    return dict(base, stops=stops, estimate=estimate, notes=notes)


# ---------------------------------------------------------------------------
# Creation du scenario depuis une proposition
# ---------------------------------------------------------------------------

def create_from_proposal(db, data, by):
    """Points (foyers : point existant a moins de 25 m reutilise, sinon point
    `source: engine` ; objets de couche : import_points) puis scenario.
    Retourne (scenario, erreur)."""
    stops = data.get("stops") or []
    if len(stops) < 2:
        return None, "pas_assez_de_points"
    settings = P.get_settings(db)
    zones = P._zones(db)
    now = datetime.now(timezone.utc)
    ids = []
    for s in stops[:P.MAX_POINTS]:
        if s.get("kind") == "layer" and s.get("layer") and s.get("fid"):
            got, err = P.import_points(db, s["layer"], [s["fid"]], by)
            if got:
                ids.append(got[0])
            continue
        try:
            lat, lng = float(s["lat"]), float(s["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        near = None
        for p in db["patrol_points"].find({"active": {"$ne": False}, "lat": {"$gt": lat - 0.0005, "$lt": lat + 0.0005}},
                                          {"lat": 1, "lng": 1}):
            if P.haversine_m(lat, lng, p["lat"], p["lng"]) <= 25:
                near = p
                break
        if near:
            if str(near["_id"]) not in ids:
                ids.append(str(near["_id"]))
            continue
        reason = "%d fiche(s) sur %s%s" % (int(s.get("n") or 0), ", ".join(str(y) for y in s.get("editions") or []) or "?",
                                           (", gravite moyenne %s" % s["gravite_moy"]) if s.get("gravite_moy") else "")
        if s.get("types"):
            reason += ", " + ", ".join(s["types"])
        if s.get("ai_reason"):
            reason += " - IA : " + str(s["ai_reason"])[:300]
        doc = {"_id": P.ObjectId(), "name": str(s.get("name") or "Foyer")[:80], "lat": lat, "lng": lng,
               "radius_m": settings["radius_m"], "instruction": "", "photo_required": False, "dwell_s": 0,
               "zone_ids": P.zones_of_point(db, lat, lng, zones), "active": True, "source": "engine",
               "ia_reason": reason, "aliases": [], "created_by": by, "created_at": now, "updated_at": now, "updated_by": by}
        db["patrol_points"].insert_one(doc)
        ids.append(str(doc["_id"]))
    if len(ids) < 2:
        return None, "pas_assez_de_points"
    labels = {"surete": "Surete", "secours": "Secours", "incendie": "Prevention incendie", "prevention": "Prevention",
              "generale": "Generale"}
    name = str(data.get("name") or "").strip() or "Ronde %s %s" % (labels.get(data.get("dominante"), ""),
                                                                    datetime.now(PARIS).strftime("%d/%m %H:%M"))
    ev = data.get("event")
    sc, err = P.save_scenario(db, {"name": name, "travel": data.get("travel"), "loop": bool(data.get("loop")),
                                   "dominante": data.get("dominante"), "points": ids,
                                   "color": {"surete": "#ef4444", "secours": "#dc2626", "incendie": "#f97316",
                                             "prevention": "#16a34a"}.get(data.get("dominante"), "#2563eb"),
                                   "note": "Propose d'apres l'historique %s (%s)" % (
                                       (data.get("source") or "pcs").upper(), "tout" if ev == ALL_EVENTS else (ev or "?"))}, by)
    return sc, err


# ---------------------------------------------------------------------------
# Affiner avec l'IA (admins) : Claude revoit le score des foyers
# ---------------------------------------------------------------------------

_MASKS = [
    (re.compile(r"(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}"), "[tel]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"),
    (re.compile(r"\b[A-Z]{2}[-\s]?\d{3}[-\s]?[A-Z]{2}\b"), "[plaque]"),
    (re.compile(r"\b(?:M\.|Mme|Mlle|Monsieur|Madame)\s+[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?"), "[personne]"),
]


def mask(text):
    s = str(text or "")
    for rx, rep in _MASKS:
        s = rx.sub(rep, s)
    return s


AI_SYSTEM = """Tu es l'adjoint du responsable securite d'un circuit automobile (Le Mans).
On te donne des FOYERS de fiches passees (regroupements geographiques de fiches de main courante du
PC Securite ou du PC Organisation) pour une DOMINANTE de patrouille. Chaque fiche est un resume (ou un
texte brut) avec sa date, son genre (incident, ronde, anomalie de ronde, information) et sa gravite.
Pour chaque foyer, juge l'utilite d'y passer en patrouille preventive : gravite reelle, recidive,
possibilite d'agir sur place, lieu sensible (batiment, acces, stockage). Une ronde de routine sans
anomalie compte peu ; une anomalie de ronde, une intrusion, une degradation, une agression comptent beaucoup.
Hors sujet pour la dominante = 0.
Reponds UNIQUEMENT en JSON valide :
{"foyers": [{"id": "f3", "score": 0-100, "raison": "une phrase"}], "commentaire": "2 phrases au plus sur la ronde"}"""


def ai_rescore(db, event, dominante, travel, hour=None, model=None, source="pcs"):
    """Scores revus par Claude {foyer_id: score}, raisons et commentaire."""
    import pcorg_summary as PS
    foyers, _stats = build_foyers(db, event, dominante, travel, hour, source)
    top = foyers[:30]
    if not top:
        return {}, {}, "Aucun foyer.", []
    ids = [fid for f in top for fid in f["fiche_ids"][:6]]
    fiches = {f["_id"]: f for f in db["pcorg"].find({"_id": {"$in": ids}},
                                                    {"text": 1, "text_full": 1, "category": 1, "niveau_urgence": 1,
                                                     "content_category": 1, "ts": 1})}
    try:
        import pcorg_digest as PD
        resumes = {d["_id"]: d for d in db[PD.COLL].find({"_id": {"$in": ids}},
                                                           {"resume": 1, "gravite": 1, "nature": 1, "type": 1})}
    except Exception:
        resumes = {}
    lines = []
    for f in top:
        lines.append("FOYER %s (%s) : %d fiches, editions %s, score code %.1f, gravite moyenne %s, genres %s, types %s"
                     % (f["id"], f.get("lieu") or "lieu non nomme", f["n"], f["editions"], f["score"],
                        f["gravite_moy"] or "?", f.get("kinds"), ", ".join(f.get("types") or []) or "-"))
        for fid in f["fiche_ids"][:6]:
            x = fiches.get(fid)
            if not x:
                continue
            ts = x.get("ts")
            when = ts.replace(tzinfo=timezone.utc).astimezone(PARIS).strftime("%Y-%m-%d %H:%M") if isinstance(ts, datetime) else "?"
            dg = resumes.get(fid) or {}
            body = dg.get("resume") or mask((x.get("text_full") or x.get("text") or "")[:240])
            lines.append("  - %s %s %s : %s" % (when, (x.get("category") or ""),
                                                ("gravite %s" % dg["gravite"]) if dg.get("gravite") else "", body))
    user = "Source : %s\nEvenement : %s\nDominante : %s\nDeplacement : %s\n\n%s" % (
        "PC Securite (PCS)" if source == "pcs" else "PC Organisation (PCO)",
        "tout l'historique" if event == ALL_EVENTS else event, dominante,
        "a pied" if travel == "pedestrian" else "vehicule", "\n".join(lines))
    _sections, raw, usage = PS.call_claude(AI_SYSTEM, user, model=model or PS.CLAUDE_MODEL, db=db,
                                           structured=False, system_cache=False)
    try:
        PS.record_ai_usage(db, "patrouilles_propositions", model or PS.CLAUDE_MODEL, usage,
                           {"event": event, "dominante": dominante, "source": source, "foyers": len(top)})
    except Exception:
        pass
    txt = (raw or "").strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt).strip()
    m = re.search(r"\{.*\}", txt, re.S)
    data = json.loads(m.group(0) if m else txt)
    scores, reasons = {}, {}
    for it in data.get("foyers") or []:
        fid = str(it.get("id") or "")
        try:
            scores[fid] = max(0.0, min(100.0, float(it.get("score"))))
        except (TypeError, ValueError):
            continue
        reasons[fid] = str(it.get("raison") or "")[:300]
    return scores, reasons, str(data.get("commentaire") or "")[:600], top
