# pcorg_anticipation.py -- fiche d'anticipation d'un evenement, en CODE PUR
#
# A partir des fiches structurees (pcorg_digests) des editions passees d'un
# evenement : ce qui revient, ou, a quelle heure, avec quelle gravite, et ce
# qu'il faut retenir (dysfonctionnements, alarmes jamais remises, agressions
# d'agents, fiches graves). Lisible en deux minutes avant l'evenement.
# Calculee a la demande (page /admin/gravite-fiches, onglet Anticipation) :
# quelques milliers de digests par evenement, aucun appel au modele.
#
# Seules les fiches de nature « incident » comptent dans les foyers et les
# creneaux (52 % de la base est le PC Surete : rondes, ouvertures, prises de
# service) ; les fiches sans nature (anciennes versions) comptent si leur
# gravite est >= 2.
from collections import Counter, defaultdict
from datetime import timezone
from zoneinfo import ZoneInfo

import pcorg_enrich as PE

PARIS = ZoneInfo("Europe/Paris")
GRAV_POIDS = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
PROJECTION = {"event": 1, "year": 1, "ts": 1, "gravite": 1, "review": 1, "nature": 1, "famille": 1,
              "type": 1, "taxo.sous_classification": 1, "taxo.sous_classification_officielle": 1,
              "sous_classification_proposee": 1, "geo": 1, "zone": 1, "lieu": 1, "ligne": 1, "resume": 1,
              "dysfonctionnement": 1, "alarme": 1, "alarme_non_remise": 1, "agression_agent": 1,
              "chaleur": 1, "secours": 1, "doublon_probable": 1, "doublon_declare": 1, "category": 1,
              "victime_personnel": 1, "prompt_version": 1}


def gravity(d):
    """Gravite utilisee partout : validee par un admin, sinon evaluee."""
    return (d.get("review") or {}).get("gravite_validee") or d.get("gravite") or 0


def is_incident(d):
    if d.get("nature"):
        return d["nature"] == "incident"
    return gravity(d) >= 2


def family(d):
    """Famille v5, sinon sous-classification officielle (versions precedentes)."""
    if d.get("famille"):
        return PE.FAMILLES.get(d["famille"], d["famille"])
    tx = d.get("taxo") or {}
    if tx.get("sous_classification") and tx.get("sous_classification_officielle"):
        return tx["sous_classification"]
    return d.get("sous_classification_proposee") or "Non classe"


def place_of(d):
    """(cle, libelle) du lieu : position nommee, sinon zone de competence, sinon lieu cite."""
    geo = d.get("geo") or {}
    if geo.get("name"):
        return "g:" + PE.norm(geo["name"]), geo["name"]
    if (d.get("zone") or {}).get("name"):
        return "z:" + PE.norm(d["zone"]["name"]), d["zone"]["name"]
    lieu = d.get("lieu")
    if lieu and PE.norm(lieu) not in ("", "non precise"):
        return "l:" + PE.norm(lieu), lieu
    return None, None


def _local(ts):
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(PARIS)


def _line(d):
    loc = _local(d.get("ts"))
    return {"id": d["_id"], "year": d.get("year"), "quand": loc.strftime("%d/%m %H:%M") if loc else None,
            "jour": JOURS[loc.weekday()] if loc else None, "gravite": gravity(d),
            "ligne": d.get("ligne") or d.get("resume") or d.get("type"), "famille": family(d)}


def build(digests, totals=None):
    """Fiche d'anticipation. digests : pcorg_digests d'UN evenement (toutes
    editions retenues, doublons compris : ils sont ecartes ici) ; totals :
    {annee: fiches pcorg} pour la couverture."""
    docs = [d for d in digests if d.get("gravite") and not d.get("ignoree")
            and not (d.get("doublon_probable") or d.get("doublon_declare"))]
    years = sorted({d.get("year") for d in docs if d.get("year") is not None})
    n_ed = len(years) or 1
    inc = [d for d in docs if is_incident(d)]

    editions = []
    for y in years:
        dy = [d for d in docs if d.get("year") == y]
        iy = [d for d in dy if is_incident(d)]
        editions.append({"year": y, "fiches": len(dy), "incidents": len(iy),
                         "graves": sum(1 for d in iy if gravity(d) >= 3),
                         "tres_graves": sum(1 for d in iy if gravity(d) >= 4),
                         "total_pcorg": (totals or {}).get(y)})

    # Familles : total, par edition, gravite moyenne
    fam = defaultdict(lambda: {"n": 0, "par_annee": Counter(), "grav": 0})
    for d in inc:
        f = fam[family(d)]
        f["n"] += 1
        f["par_annee"][d.get("year")] += 1
        f["grav"] += gravity(d)
    familles = sorted(({"famille": k, "n": v["n"], "par_edition": round(v["n"] / n_ed, 1),
                        "par_annee": {str(y): v["par_annee"].get(y, 0) for y in years},
                        "gravite_moy": round(v["grav"] / v["n"], 1)} for k, v in fam.items()),
                      key=lambda x: -x["n"])[:15]

    # Heures et creneaux (jour de semaine x heure), en heure de Paris
    heures = [0] * 24
    creneaux = Counter()
    for d in inc:
        loc = _local(d.get("ts"))
        if loc:
            heures[loc.hour] += 1
            creneaux[(loc.weekday(), loc.hour)] += GRAV_POIDS.get(gravity(d), 1)
    top_cr = [{"jour": JOURS[w], "heure": "%02d:00" % h, "score": s} for (w, h), s in creneaux.most_common(8)]

    # Foyers : lieux qui reviennent
    places = defaultdict(lambda: {"label": None, "n": 0, "score": 0, "years": set(), "hours": Counter(),
                                  "fams": Counter(), "max": 0, "fiches": []})
    for d in inc:
        key, label = place_of(d)
        if not key:
            continue
        p = places[key]
        p["label"] = p["label"] or label
        p["n"] += 1
        g = gravity(d)
        p["score"] += GRAV_POIDS.get(g, 1)
        p["max"] = max(p["max"], g)
        p["years"].add(d.get("year"))
        loc = _local(d.get("ts"))
        if loc:
            p["hours"][loc.hour] += 1
        p["fams"][family(d)] += 1
        p["fiches"].append(d)
    foyers = []
    for key, p in places.items():
        rec = len(p["years"])
        foyers.append({
            "lieu": p["label"], "fiches": p["n"], "editions": rec, "sur": n_ed, "gravite_max": p["max"],
            # recurrence : un lieu touche a chaque edition passe devant un pic isole
            "score": round(p["score"] * (1 + 0.5 * (rec - 1)), 1),
            "heures": ["%02d:00" % h for h, _ in p["hours"].most_common(3)],
            "familles": [{"famille": f, "n": n} for f, n in p["fams"].most_common(3)],
            "exemples": [_line(d) for d in sorted(p["fiches"], key=lambda x: -gravity(x))[:3]],
        })
    foyers.sort(key=lambda x: -x["score"])

    def lines(pred, limit=12, key=None):
        sel = [d for d in docs if pred(d)]
        sel.sort(key=key or (lambda x: (-gravity(x), str(x.get("ts")))))
        return [_line(d) for d in sel[:limit]]

    dysf = []
    for d in sorted((d for d in docs if d.get("dysfonctionnement")), key=lambda x: str(x.get("ts")), reverse=True)[:15]:
        o = _line(d)
        o.update(type=d["dysfonctionnement"].get("type"), description=d["dysfonctionnement"].get("description"))
        dysf.append(o)
    return {
        "editions": editions, "nb_editions": len(years), "incidents": len(inc),
        "familles": familles, "heures": heures, "creneaux": top_cr, "foyers": foyers[:15],
        "graves": lines(lambda d: gravity(d) >= 4, 15),
        "dysfonctionnements": dysf,
        "alarmes_non_remises": lines(lambda d: d.get("alarme_non_remise"), 10),
        "agressions_agents": lines(lambda d: d.get("agression_agent") or d.get("victime_personnel") is True, 10),
        "chaleur": lines(lambda d: d.get("chaleur"), 8),
    }
