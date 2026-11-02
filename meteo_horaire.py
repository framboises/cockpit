"""Meteo heure par heure, toutes sources fusionnees (02/11/2026) : bandeau et panneau meteo de Cockpit.

MEME LOGIQUE que Looker (looker/titan_looker/meteo/horaire.py) : reporter toute modification des deux cotes.

Sources (collecte tools/Meteo), de la plus sure a la moins sure ; une seule retenue par heure (`Source`), completee
par les autres pour les grandeurs qu'elle n'a pas :
  mesure     meteo_observations  station 72181001, fichier ouvert Meteo-France, quelques heures de retard
  prevision  meteo_previsions    AROME 48 h puis ARPEGE jusqu'a 96 h
  tendance   meteo_tendances     ECMWF IFS jusqu'a ~15 jours (Open-Meteo, CC BY 4.0)
Les trois collections ont la meme forme {Date: "YYYY-MM-DD", Heures: [{Heure: "HH:MM", ...}]} et les memes libelles :
les heures fusionnees gardent donc les libelles historiques de meteo_previsions, lus par static/js/meteo.js, et
gagnent `Source`, `Ciel` (pictogramme deduit, jamais invente) et `Jour`.

Lecture seule.
"""

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from astral import Observer
from astral.sun import sun

PARIS = ZoneInfo("Europe/Paris")
CIRCUIT = Observer(latitude=47.945, longitude=0.224)
SOURCES = (("mesure", "meteo_observations"), ("prevision", "meteo_previsions"), ("tendance", "meteo_tendances"))
LIBELLES_SOURCES = {"mesure": "Mesuré (station Météo-France du Mans)", "prevision": "Prévision Météo-France (AROME, ARPEGE)",
                    "tendance": "Tendance ECMWF (Open-Meteo)"}
GRAVITE = {"clair": 1, "peu-nuageux": 2, "nuageux": 3, "couvert": 4, "brouillard": 5, "bruine": 6, "pluie": 7,
           "averses": 8, "neige": 9, "orage": 10}
INTERNES = ("historique_temperature", "historique_pluie", "historique_vent_moyen", "historique_vent_rafale", "source")


def _nombre(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _wmo(code):
    if code is None:
        return None
    code = int(code)
    if code == 0:
        return "clair"
    if code in (1, 2):
        return "peu-nuageux"
    if code == 3:
        return "couvert"
    if code in (45, 48):
        return "brouillard"
    if 51 <= code <= 57:
        return "bruine"
    if 61 <= code <= 67:
        return "pluie"
    if 71 <= code <= 77 or code in (85, 86):
        return "neige"
    if 80 <= code <= 82:
        return "averses"
    if code >= 95:
        return "orage"
    return None


def ciel(h, source):
    """Pictogramme d'une heure fusionnee (libelles meteo_previsions)."""
    code = _nombre(h.get("Code temps (WMO)"))
    if source == "tendance" and code is not None:
        return _wmo(code)
    pluie = _nombre(h.get("Pluviométrie (mm)"))
    foudre = _nombre(h.get("Foudre (impacts/km2)"))
    vis = _nombre(h.get("Visibilite (m)"))
    ww = _nombre(h.get("Temps present (WW)"))
    if (foudre or 0) > 0 or (ww is not None and (ww >= 91 or ww in (17, 29))):
        return "orage"
    if pluie is not None and pluie >= 0.1:
        return "averses" if pluie >= 2 else "pluie"
    if vis is not None and vis < 1000:
        return "brouillard"
    # Heure mesuree de jour : l'ensoleillement MESURE prime sur la nebulosite reprise de la prevision (fusion).
    ins = _nombre(h.get("Ensoleillement (min)"))
    if source == "mesure" and h.get("Jour") and ins is not None:
        return "clair" if ins >= 45 else "peu-nuageux" if ins >= 20 else "nuageux" if ins >= 5 else "couvert"
    n = _nombre(h.get("Nebulosite (%)"))
    if n is None and source == "mesure":
        n = _nombre(h.get("Nebulosite basse (%)"))
    if n is None and code is not None:
        return _wmo(code)
    if n is None:
        return None
    return "clair" if n < 20 else "peu-nuageux" if n < 50 else "nuageux" if n < 85 else "couvert"


def _soleil(jour):
    try:
        s = sun(CIRCUIT, date=jour, tzinfo=PARIS)
        return s["sunrise"], s["sunset"]
    except ValueError:
        return None


def jours(db, debut, fin):
    """{date ISO: [heures fusionnees triees]} pour [debut, fin] (dates)."""
    dates = [(debut + timedelta(days=i)).isoformat() for i in range((fin - debut).days + 1)]
    par_heure = {}
    for source, collection in SOURCES:
        for doc in db[collection].find({"Date": {"$in": dates}}, {"_id": 0, "Date": 1, "Heures": 1}):
            for h in doc.get("Heures") or []:
                if not isinstance(h, dict) or not h.get("Heure"):
                    continue
                if _nombre(h.get("Température (°C)")) is None and _nombre(h.get("Pluviométrie (mm)")) is None:
                    continue
                par_heure.setdefault((doc["Date"], str(h["Heure"])[:5]), {})[source] = h
    sortie = {d: [] for d in dates}
    soleils = {d: _soleil(date.fromisoformat(d)) for d in dates}
    for (jour, heure) in sorted(par_heure):
        sources = par_heure[(jour, heure)]
        retenue = next(s for s, _ in SOURCES if s in sources)
        fusion = {k: v for k, v in sources[retenue].items()}
        for s, _ in SOURCES:
            for cle, v in sources.get(s, {}).items():
                if cle not in INTERNES and fusion.get(cle) is None and v is not None:
                    fusion[cle] = v
        hh, mm = (int(x) for x in heure.split(":"))
        instant = datetime.combine(date.fromisoformat(jour), datetime.min.time(), PARIS) + timedelta(hours=hh, minutes=mm + 30)
        bornes = soleils.get(jour)
        fusion["Jour"] = bool(bornes and bornes[0] <= instant <= bornes[1])
        fusion["Source"] = retenue
        fusion["Ciel"] = ciel(fusion, retenue)
        fusion["Heure"] = heure
        sortie[jour].append(fusion)
    return sortie


def resume_jour(jour, heures):
    """Synthese d'un jour pour le bandeau : min / max, pluie, vent, ciel de la journee, source dominante."""
    temps = [t for t in (_nombre(h.get("Température (°C)")) for h in heures) if t is not None]
    pluies = [p for p in (_nombre(h.get("Pluviométrie (mm)")) for h in heures) if p is not None]
    rafales = [r for r in (_nombre(h.get("Vent rafale (km/h)")) for h in heures) if r is not None]
    de_jour = [h for h in heures if h.get("Jour")] or heures
    ciels = [h.get("Ciel") for h in de_jour if h.get("Ciel")]
    pire = max(ciels, key=lambda c: GRAVITE.get(c, 0)) if ciels else None
    milieu = de_jour[len(de_jour) // 2].get("Ciel") if de_jour else None
    x = y = 0.0
    for h in heures:
        d, v = _nombre(h.get("Direction vent (°)")), _nombre(h.get("Vent moyen (km/h)")) or 1
        if d is not None:
            x += math.sin(math.radians(d)) * v
            y += math.cos(math.radians(d)) * v
    direction = round((math.degrees(math.atan2(x, y)) + 360) % 360) if (x or y) else None
    sources = [h.get("Source") for h in heures]
    return {
        "Date": jour,
        "Température Max (°C)": round(max(temps)) if temps else None,
        "Température Min (°C)": round(min(temps)) if temps else None,
        "Somme Pluviométrie (mm)": round(sum(pluies), 1) if pluies else None,
        "Rafale max (km/h)": round(max(rafales)) if rafales else None,
        "Direction vent (°)": direction,
        "Ciel": pire if GRAVITE.get(pire, 0) >= GRAVITE["brouillard"] else (milieu or pire),
        "Source": max(set(sources), key=sources.count) if sources else None,
    }
