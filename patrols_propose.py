"""
patrols_propose.py - Propositions de patrouilles d'apres l'historique
(livraison 4, docs/claude/patrouilles.md section 6).

Entree : dominante (surete, secours, incendie, prevention, generale), duree
disponible, deplacement (a pied / vehicule), evenement (toutes ses
editions), option "meme tranche horaire". Sortie : un parcours qui tient dans
la duree, chaque arret explique (foyer d'incidents ou objet d'une couche).

Moteur 100 % code, toujours disponible :
  1. fiches de la dominante avec une position : GPS, sinon carroyage
     (grid_ref) pour les editions sans GPS ;
  2. poids : gravite evaluee (pcorg_digests, gravite validee d'abord), sinon
     niveau d'urgence ; editions recentes et meme tranche horaire favorisees ;
  3. foyers : regroupement a 60 m (a pied) / 120 m (vehicule), score = somme
     des poids x bonus de recurrence (nombre d'editions) ; nom = objet Looker
     le plus proche ;
  4. parcours glouton : le foyer qui rapporte le plus par seconde de trajet,
     jusqu'a remplir la duree (vitesses forfaitaires), complete par des
     objets de la couche de la dominante s'il reste du temps ;
  5. ordre optimise et trajet reel par Valhalla ; arrets les moins utiles
     retires si la duree est depassee.

"Affiner avec l'IA" (admins) : les foyers et leurs fiches (sans donnees
personnelles) sont relus par Claude (pcorg_summary.call_claude, budget et
couts suivis), qui revoit le score de chaque foyer et l'explique ; le
parcours est recalcule avec ces scores.
"""
import json
import logging
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import patrols as P

logger = logging.getLogger(__name__)
PARIS = ZoneInfo("Europe/Paris")

DOMINANTE_RULES = {
    "surete": {"categories": ["PCO.Securite"], "layer": "3p"},
    "secours": {"categories": ["PCO.Secours"], "layer": "postesecours"},
    "incendie": {"regex": r"incendi|\bfeu\b|feux|fum[ée]e|d[ée]part de feu|extincteur|br[uû]l|alarme incendie|\bssi\b|d[ée]senfumage",
                 "layer": "hospitalites"},
    "prevention": {"regex": r"travaux|chantier|prestataire|montage|d[ée]montage|accident du travail|nacelle|\bengin|levage|"
                            r"[ée]chafaud|plan de pr[ée]vention|ppsps|chute de hauteur|\bepi\b|casque",
                   "layer": None},
    "generale": {"categories": ["PCO.Securite", "PCO.Secours", "PCO.Technique", "PCO.Flux"], "layer": "tribunes"},
}
GRAV_WEIGHT = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}
URG_WEIGHT = {"IMP": 1, "UR": 2, "UA": 4, "EU": 8}
TRAVEL = {   # vitesse moyenne sur site (km/h), detour, arret a chaque point (s), rayon de foyer (m)
    "pedestrian": {"kmh": 4.5, "detour": 1.35, "dwell": 120, "radius": 60},
    "vehicle": {"kmh": 20.0, "detour": 1.4, "dwell": 90, "radius": 120},
}
NAME_LAYERS = ("tribunes", "3p", "portes", "sanitaires", "postesecours", "batiment", "terrains", "hospitalites",
               "Fan Zone et Village", "passerelles", "box_numerotation")
MAX_STOPS = 25


def _grid(db):
    return {str(g.get("grid_ref") or "").upper().replace(" ", ""): (float(g["latitude"]), float(g["longitude"]))
            for g in db["grid_ref"].find({"latitude": {"$ne": None}}, {"grid_ref": 1, "latitude": 1, "longitude": 1})
            if g.get("grid_ref") and g.get("latitude") is not None}


def _position(f, grid):
    g = f.get("gps") or {}
    c = g.get("coordinates")
    if g.get("type") == "Point" and isinstance(c, list) and len(c) >= 2:
        try:
            return float(c[1]), float(c[0]), "gps"
        except (TypeError, ValueError):
            pass
    car = str((f.get("content_category") or {}).get("carroye") or "").upper().replace(" ", "")
    m = re.match(r"^([A-Z]{1,2}\d{1,3})", car)
    if m and m.group(1) in grid:
        lat, lng = grid[m.group(1)]
        return lat, lng, "carroyage"
    return None


def events_with_history(db):
    """Evenements ayant des fiches avec GPS ou carroyage, du plus fourni au moins."""
    pipe = [{"$match": {"$or": [{"gps.type": "Point"}, {"content_category.carroye": {"$nin": [None, ""]}}]}},
            {"$group": {"_id": "$event", "n": {"$sum": 1}, "years": {"$addToSet": "$year"}}},
            {"$sort": {"n": -1}}]
    out = []
    for r in db["pcorg"].aggregate(pipe):
        if r["_id"]:
            out.append({"event": r["_id"], "n": r["n"], "years": sorted(y for y in r["years"] if y)})
    return out


def _fiches(db, event, dominante):
    rule = DOMINANTE_RULES.get(dominante) or DOMINANTE_RULES["generale"]
    q = {"event": event, "content_category.patrol_run": {"$exists": False},
         "$or": [{"gps.type": "Point"}, {"content_category.carroye": {"$nin": [None, ""]}}]}
    if rule.get("categories"):
        q["category"] = {"$in": rule["categories"]}
    proj = {"gps": 1, "content_category.carroye": 1, "content_category.sous_classification": 1, "category": 1,
            "niveau_urgence": 1, "ts": 1, "year": 1, "text": 1, "text_full": 1}
    rx = re.compile(rule["regex"], re.I) if rule.get("regex") else None
    out = []
    for f in db["pcorg"].find(q, proj):
        # Texte ET sous-classification (ex. fiche Secours classee "Incendie")
        hay = (f.get("text_full") or f.get("text") or "") + " " + str((f.get("content_category") or {}).get("sous_classification") or "")
        if rx and not rx.search(hay):
            continue
        out.append(f)
    return out


def _hour(f):
    ts = f.get("ts")
    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(PARIS).hour + ts.astimezone(PARIS).minute / 60.0


def _hour_gap(a, b):
    d = abs(a - b) % 24
    return min(d, 24 - d)


def build_foyers(db, event, dominante, travel, hour=None):
    """Foyers d'incidents de la dominante : [{id, lat, lng, score, n, editions,
    gravite_moy, top_sous, fiche_ids, sample}], du plus fort au plus faible."""
    fiches = _fiches(db, event, dominante)
    if not fiches:
        return [], 0
    grid = _grid(db)
    digests = {}
    try:
        import pcorg_digest as PD
        for d in db[PD.COLL].find({"_id": {"$in": [f["_id"] for f in fiches]}},
                                  {"gravite": 1, "review.gravite_validee": 1, "resume": 1}):
            digests[d["_id"]] = d
    except Exception as e:
        logger.debug("digests indisponibles : %s", e)
    years = [f.get("year") for f in fiches if isinstance(f.get("year"), int)]
    last_year = max(years) if years else None
    pts = []
    for f in fiches:
        pos = _position(f, grid)
        if not pos:
            continue
        dg = digests.get(f["_id"]) or {}
        grav = ((dg.get("review") or {}).get("gravite_validee")) or dg.get("gravite")
        w = GRAV_WEIGHT.get(grav) or URG_WEIGHT.get(f.get("niveau_urgence")) or 1
        if last_year and isinstance(f.get("year"), int):
            w *= {0: 1.0, 1: 0.8}.get(last_year - f["year"], 0.6)
        if hour is not None:
            h = _hour(f)
            if h is not None:
                w *= 1.0 if _hour_gap(h, hour) <= 1.5 else 0.35
        pts.append({"f": f, "lat": pos[0], "lng": pos[1], "src": pos[2], "w": w, "grav": grav, "dg": dg})
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
        subs = {}
        for it in items:
            s = (it["f"].get("content_category") or {}).get("sous_classification")
            if s:
                subs[s] = subs.get(s, 0) + 1
        top = sorted(subs.items(), key=lambda x: -x[1])[:3]
        out.append({
            "id": "f%d" % i, "lat": c["lat"], "lng": c["lng"], "score": round(score, 2), "n": len(items),
            "editions": eds, "gravite_moy": round(sum(gravs) / len(gravs), 1) if gravs else None,
            "top_sous": [t[0] for t in top], "sources": sorted({it["src"] for it in items}),
            "fiche_ids": [it["f"]["_id"] for it in sorted(items, key=lambda it: -it["w"])[:12]],
        })
    out.sort(key=lambda c: -c["score"])
    return out, len(pts)


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


def _name_foyer(c, gaz):
    best, bd = None, 1e9
    for it in gaz:
        d = P.haversine_m(c["lat"], c["lng"], it["lat"], it["lng"])
        if d < bd:
            best, bd = it, d
    sub = (" - " + c["top_sous"][0]) if c.get("top_sous") else ""
    if best and bd <= 150:
        return ("Foyer pres de %s" % best["name"])[:70] + sub[:40]
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


def propose(db, dominante, duration_min, travel, event, loop=False, hour=None, ai_scores=None, ai_reasons=None):
    """Proposition complete : {stops, estimate, foyers (carte), stats, notes}."""
    travel = travel if travel in TRAVEL else "pedestrian"
    budget = max(5, min(240, int(duration_min or 45))) * 60
    foyers, n_pos = build_foyers(db, event, dominante, travel, hour)
    if ai_scores:
        # Scores de l'IA (0-100) : les foyers qu'elle n'a pas notes passent derriere
        for f in foyers:
            s = ai_scores.get(f["id"])
            f["score_code"] = f["score"]
            f["score"] = max(0.0, float(s)) if s is not None else 0.0
            if ai_reasons and ai_reasons.get(f["id"]):
                f["ai_reason"] = ai_reasons[f["id"]]
        foyers.sort(key=lambda c: -c["score"])
    notes = []
    gaz = _gazetteer(db)
    for f in foyers[:60]:
        f["name"] = _name_foyer(f, gaz)
        f["kind"] = "foyer"
    cands = [f for f in foyers[:40] if f["score"] > 0 and (f["n"] >= 2 or f["score"] >= 4)]
    if len(cands) < 5:      # dominante peu fournie : les foyers d'une seule fiche comptent aussi
        cands = [f for f in foyers[:40] if f["score"] > 0]
    if n_pos < 40:
        notes.append("Historique mince : %d fiche(s) placee(s) pour cette dominante sur cet evenement." % n_pos)
    tour = _greedy(cands, budget, travel, loop)
    # Complement : objets de la couche de la dominante pres du parcours
    layer = (DOMINANTE_RULES.get(dominante) or {}).get("layer")
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
    if not tour:
        return {"stops": [], "estimate": None, "foyers": foyers[:60], "stats": {"fiches": n_pos, "foyers": len(foyers)},
                "notes": ["Aucun foyer pour cette dominante sur cet evenement."]}
    # Ordre optimise (depart fixe) puis trajet reel ; retrait si trop long
    estimate = None
    for _ in range(6):
        coords = [(s["lat"], s["lng"]) for s in tour]
        if len(tour) >= 3:
            m, err = P._matrix(coords, travel)
            if not err:
                order = P.best_order(m, loop)
                tour = [tour[i] for i in order]
                coords = [(s["lat"], s["lng"]) for s in tour]
        route_coords = coords + ([coords[0]] if loop and len(coords) > 1 else [])
        est, err = P.route_through(route_coords, travel)
        if err:
            notes.append("Trajet reel non calcule (%s) : estimation a vol d'oiseau." % err)
            break
        total = est["duration_s"] + TRAVEL[travel]["dwell"] * len(tour)
        estimate = dict(est, total_s=total)
        if total <= budget * 1.1 or len(tour) <= 2:
            break
        # Retirer l'arret le moins utile (jamais le depart)
        worst = min(range(1, len(tour)), key=lambda i: tour[i]["score"])
        tour.pop(worst)
    stops = []
    for s in tour:
        st = {"kind": s.get("kind", "foyer"), "name": s.get("name"), "lat": s["lat"], "lng": s["lng"],
              "score": s.get("score")}
        if st["kind"] == "foyer":
            st.update(id=s["id"], n=s["n"], editions=s["editions"], gravite_moy=s["gravite_moy"],
                      top_sous=s["top_sous"], fiche_ids=s["fiche_ids"], sources=s["sources"],
                      score_code=s.get("score_code"), ai_reason=s.get("ai_reason"))
        else:
            st.update(layer=s.get("layer"), fid=s.get("fid"), info=s.get("info"))
        stops.append(st)
    return {"stops": stops, "estimate": estimate, "foyers": [{k: f.get(k) for k in ("id", "lat", "lng", "score", "n", "name")}
                                                             for f in foyers[:60]],
            "stats": {"fiches": n_pos, "foyers": len(foyers)}, "notes": notes,
            "budget_s": budget, "dominante": dominante, "travel": travel, "loop": bool(loop), "event": event}


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
            ids.append(str(near["_id"]))
            continue
        reason = "%d incident(s) sur %s%s" % (int(s.get("n") or 0), ", ".join(str(y) for y in s.get("editions") or []) or "?",
                                              (", gravite moyenne %s" % s["gravite_moy"]) if s.get("gravite_moy") else "")
        if s.get("ai_reason"):
            reason += " - IA : " + str(s["ai_reason"])[:300]
        doc = {"_id": P.ObjectId(), "name": str(s.get("name") or "Foyer")[:80], "lat": lat, "lng": lng,
               "radius_m": settings["radius_m"], "instruction": "", "photo_required": False, "dwell_s": 0,
               "zone_ids": P.zones_of_point(db, lat, lng, zones), "active": True, "source": "engine",
               "ia_reason": reason, "created_by": by, "created_at": now, "updated_at": now, "updated_by": by}
        db["patrol_points"].insert_one(doc)
        ids.append(str(doc["_id"]))
    if len(ids) < 2:
        return None, "pas_assez_de_points"
    labels = {"surete": "Surete", "secours": "Secours", "incendie": "Prevention incendie", "prevention": "Prevention",
              "generale": "Generale"}
    name = str(data.get("name") or "").strip() or "Ronde %s %s" % (labels.get(data.get("dominante"), ""),
                                                                    datetime.now(PARIS).strftime("%d/%m %H:%M"))
    sc, err = P.save_scenario(db, {"name": name, "travel": data.get("travel"), "loop": bool(data.get("loop")),
                                   "dominante": data.get("dominante"), "points": ids,
                                   "color": {"surete": "#ef4444", "secours": "#dc2626", "incendie": "#f97316",
                                             "prevention": "#16a34a"}.get(data.get("dominante"), "#2563eb"),
                                   "note": "Propose d'apres l'historique (%s)" % (data.get("event") or "?")}, by)
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
On te donne des FOYERS d'incidents passes (regroupements geographiques de fiches de main courante)
pour une DOMINANTE de patrouille. Pour chaque foyer, juge la gravite REELLE et l'utilite d'y passer
en patrouille preventive (risque de recidive, gravite, possibilite d'agir sur place).
Un foyer de nombreuses fiches benignes (objets trouves, renseignements) vaut moins qu'un foyer de
quelques fiches graves (agression, malaise grave, depart de feu). Hors sujet pour la dominante = 0.
Reponds UNIQUEMENT en JSON valide :
{"foyers": [{"id": "f3", "score": 0-100, "raison": "une phrase"}], "commentaire": "2 phrases au plus sur la ronde"}"""


def ai_rescore(db, event, dominante, travel, hour=None, model=None):
    """Scores revus par Claude {foyer_id: score}, raisons et commentaire."""
    import pcorg_summary as PS
    foyers, _ = build_foyers(db, event, dominante, travel, hour)
    top = foyers[:30]
    if not top:
        return {}, {}, "Aucun foyer."
    ids = [fid for f in top for fid in f["fiche_ids"][:6]]
    fiches = {f["_id"]: f for f in db["pcorg"].find({"_id": {"$in": ids}},
                                                    {"text": 1, "text_full": 1, "category": 1, "niveau_urgence": 1,
                                                     "content_category.sous_classification": 1, "ts": 1})}
    lines = []
    for f in top:
        lines.append("FOYER %s : %d fiches, editions %s, score code %.1f, gravite moyenne %s, sous-classifications : %s"
                     % (f["id"], f["n"], f["editions"], f["score"], f["gravite_moy"] or "?", ", ".join(f["top_sous"]) or "-"))
        for fid in f["fiche_ids"][:6]:
            x = fiches.get(fid)
            if not x:
                continue
            ts = x.get("ts")
            when = ts.replace(tzinfo=timezone.utc).astimezone(PARIS).strftime("%Y-%m-%d %H:%M") if isinstance(ts, datetime) else "?"
            lines.append("  - %s %s %s urgence %s : %s" % (when, (x.get("category") or "").replace("PCO.", ""),
                                                           (x.get("content_category") or {}).get("sous_classification") or "",
                                                           x.get("niveau_urgence") or "-",
                                                           mask((x.get("text_full") or x.get("text") or "")[:240])))
    user = "Evenement : %s\nDominante : %s\nDeplacement : %s\n\n%s" % (event, dominante,
                                                                      "a pied" if travel == "pedestrian" else "vehicule",
                                                                      "\n".join(lines))
    _sections, raw, usage = PS.call_claude(AI_SYSTEM, user, model=model or PS.CLAUDE_MODEL, db=db,
                                           structured=False, system_cache=False)
    try:
        PS.record_ai_usage(db, "patrouilles_propositions", model or PS.CLAUDE_MODEL, usage,
                           {"event": event, "dominante": dominante, "foyers": len(top)})
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
    return scores, reasons, str(data.get("commentaire") or "")[:600]
