"""Vent a 10 m sur l'enceinte : trames de la carte, serie au point, alerte rafale.

Lecteur seul de meteo_vent et meteo_vent_obs, ecrites par la brique
tools/Meteo (collecte_vent.py). Aucun Flask ici : le blueprint meteo et les
outils Alfred appellent les memes fonctions, les calculs n'existent qu'une fois.

SOURCES ET LIGNE DU TEMPS
  0 a +6 h, pas de 15 min : rafales AROME-PI (composantes U/V de la rafale
           quart-horaire). AROME-PI ne sert pas de vent moyen : celui de ces
           trames est AROME, interpole lineairement entre ses deux heures
           encadrantes (trame marquee "moyen_interpole").
  +6 h a +42 h, pas horaire : AROME 0,01 deg (vent moyen U/V et rafales).

Tout est stocke en m/s ; les sorties donnent m/s ET km/h, l'affichage choisit.
Direction meteo (d'ou vient le vent) = (270 - atan2(V, U)) mod 360.
"""

import math
import zlib
from datetime import datetime, timedelta, timezone

COLLECTION = "meteo_vent"
COLLECTION_OBS = "meteo_vent_obs"
FLUX_PI = "vent_aromepi"
FLUX_AROME = "vent_arome"
HORIZON_H = 42
NODATA = -999.0

# Mesure de station au-dela de laquelle on ne la montre plus comme "actuelle".
OBS_AGE_MAX_MIN = 60

LON_CIRCUIT = 0.2240
LAT_CIRCUIT = 47.9450

# Points cles de l'enceinte pour les fleches renforcees, retrouves par leur nom
# dans les couches tribunes puis hospitalites (FeatureCollection uniques).
# Surchargeables par parametrages __GLOBAL__ data.meteo.points_vent (liste de
# noms ou de {nom, lat, lon}).
# Quatre points ecartes : Tertre Rouge, La Chapelle et Garage Vert sont a
# quelques centaines de metres, leurs etiquettes se chevauchaient.
POINTS_CLES_DEFAUT = ["STANDS", "TERTRE ROUGE", "MAISON BLANCHE", "INDIANAPOLIS (int.)"]

POINTS_CARDINAUX = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                    "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]


def maintenant_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(valeur):
    """UTC naif -> ISO suffixe Z (sinon le navigateur le lit en heure locale)."""
    return None if valeur is None else valeur.isoformat() + "Z"


def direction_deg(u, v):
    if u is None or v is None:
        return None
    return round((270.0 - math.degrees(math.atan2(v, u))) % 360.0)


def cardinal(deg):
    if deg is None:
        return None
    return POINTS_CARDINAUX[int((deg % 360) / 22.5 + 0.5) % 16]


def kmh(ms):
    return None if ms is None else round(ms * 3.6)


# ---------------------------------------------------------------- lecture

def _tableau(doc, champ):
    """Grille (liste de lignes nord -> sud) ou None si le champ manque."""
    brut = doc.get(champ)
    if not brut:
        return None
    import numpy as np
    t = np.frombuffer(zlib.decompress(brut), dtype="float32").reshape(doc["ny"], doc["nx"]).copy()
    t[t <= NODATA + 1] = np.nan
    return t


def _dernier_run(db, flux):
    doc = db[COLLECTION].find_one({"flux": flux}, {"run_at": 1}, sort=[("run_at", -1)])
    return doc["run_at"] if doc else None


def _docs_run(db, flux, run_at, avec_grilles=False):
    projection = None if avec_grilles else {"u": 0, "v": 0, "rafale": 0, "u_rafale": 0, "v_rafale": 0, "nuages": 0}
    return list(db[COLLECTION].find({"flux": flux, "run_at": run_at}, projection).sort("echeance_min", 1))


def _encadrants(docs_arome, instant):
    """(avant, apres, poids de apres) des documents AROME autour de l'instant."""
    avant = apres = None
    for d in docs_arome:
        if d["valid_at"] <= instant:
            avant = d
        elif apres is None:
            apres = d
    if avant is None and apres is None:
        return None
    if avant is None:
        return apres, apres, 0.0
    if apres is None:
        # Au-dela de la derniere echeance : pas d'extrapolation de plus d'une heure.
        if (instant - avant["valid_at"]) > timedelta(hours=1):
            return None
        return avant, avant, 0.0
    total = (apres["valid_at"] - avant["valid_at"]).total_seconds()
    poids = (instant - avant["valid_at"]).total_seconds() / total if total else 0.0
    return avant, apres, poids


def trames(db, maintenant=None):
    """Ligne du temps commune de la carte : AROME-PI puis AROME, sans les grilles."""
    maintenant = maintenant or maintenant_utc()
    fin = maintenant + timedelta(hours=HORIZON_H)
    liste = []

    run_pi = _dernier_run(db, FLUX_PI)
    run_ar = _dernier_run(db, FLUX_AROME)
    docs_ar = _docs_run(db, FLUX_AROME, run_ar) if run_ar else []

    dernier_pi = None
    if run_pi:
        for d in _docs_run(db, FLUX_PI, run_pi):
            if d["valid_at"] < maintenant - timedelta(minutes=15) or d["valid_at"] > fin:
                continue
            liste.append({
                "id": "pi|%s|%d" % (d["run_at"].strftime("%Y%m%dT%H%M"), d["echeance_min"]),
                "source": "AROME-PI", "run_at": iso_utc(d["run_at"]), "valid_at": iso_utc(d["valid_at"]),
                "echeance_min": d["echeance_min"],
                "moyen": "AROME interpole" if docs_ar and _encadrants(docs_ar, d["valid_at"]) else None,
                "rafale_max_site_kmh": kmh(d.get("rafale_max_site_ms")),
            })
            dernier_pi = d["valid_at"]

    debut_ar = dernier_pi or (maintenant - timedelta(minutes=30))
    for d in docs_ar:
        if d["valid_at"] <= debut_ar or d["valid_at"] > fin:
            continue
        liste.append({
            "id": "ar|%s|%d" % (d["run_at"].strftime("%Y%m%dT%H%M"), d["echeance_min"]),
            "source": "AROME", "run_at": iso_utc(d["run_at"]), "valid_at": iso_utc(d["valid_at"]),
            "echeance_min": d["echeance_min"], "moyen": "AROME",
            "rafale_max_site_kmh": kmh(d.get("rafale_max_site_ms")),
            "vent_max_site_kmh": kmh(d.get("vent_max_site_ms")),
        })

    reference = db[COLLECTION].find_one({}, {"bbox": 1, "nx": 1, "ny": 1, "pas_deg": 1})
    return {
        "trames": liste,
        "bbox": reference.get("bbox") if reference else None,
        "runs": {"AROME-PI": iso_utc(run_pi), "AROME": iso_utc(run_ar)},
        "observation": observation(db, maintenant),
        "maintenant": iso_utc(maintenant),
    }


def _arrondir(t):
    if t is None:
        return None
    import numpy as np
    return [None if not np.isfinite(x) else round(float(x), 1) for x in t.ravel()]


def _grilles_trame(db, ident):
    """(doc de reference, u, v, rafale, u_rafale, v_rafale, moyen) ou None."""
    try:
        nature, run_txt, echeance = ident.split("|")
        run_at = datetime.strptime(run_txt, "%Y%m%dT%H%M")
        echeance = int(echeance)
    except (ValueError, AttributeError):
        return None
    flux = FLUX_PI if nature == "pi" else FLUX_AROME if nature == "ar" else None
    if not flux:
        return None
    doc = db[COLLECTION].find_one({"flux": flux, "run_at": run_at, "echeance_min": echeance})
    if not doc:
        return None
    if flux == FLUX_AROME:
        return doc, _tableau(doc, "u"), _tableau(doc, "v"), _tableau(doc, "rafale"), None, None, "AROME"

    i = _interpole_arome(db, doc, ("u", "v"))
    return (doc, i.get("u"), i.get("v"), _tableau(doc, "rafale"), _tableau(doc, "u_rafale"),
            _tableau(doc, "v_rafale"), "AROME interpole" if i.get("u") is not None else None)


def _interpole_arome(db, doc, champs):
    """Champs AROME a l'instant d'un document AROME-PI, interpoles entre les
    deux heures encadrantes du dernier run. {champ: grille} (absents omis)."""
    run_ar = _dernier_run(db, FLUX_AROME)
    enc = _encadrants(_docs_run(db, FLUX_AROME, run_ar), doc["valid_at"]) if run_ar else None
    if not enc:
        return {}
    avant, apres, poids = enc
    a = db[COLLECTION].find_one({"_id": avant["_id"]})
    b = a if apres["_id"] == avant["_id"] else db[COLLECTION].find_one({"_id": apres["_id"]})
    if not (a and b and a["nx"] == doc["nx"] and a["ny"] == doc["ny"]):
        return {}
    sortie = {}
    for c in champs:
        ta, tb = _tableau(a, c), _tableau(b, c)
        if ta is not None and tb is not None:
            sortie[c] = ta * (1 - poids) + tb * poids
    return sortie


def _nuages(db, doc):
    """Couverture nuageuse totale (%) d'AROME, interpolee pour une trame AROME-PI."""
    if doc.get("flux") == FLUX_AROME:
        return _tableau(doc, "nuages")
    return _interpole_arome(db, doc, ("nuages",)).get("nuages")


def champ(db, ident):
    """Grilles d'une trame pour la carte, facon grib2json (en-tete + donnees nord -> sud)."""
    g = _grilles_trame(db, ident)
    if not g:
        return None
    doc, u, v, rafale, ur, vr, moyen = g
    bbox = doc["bbox"]
    return {
        "id": ident,
        "source": doc.get("modele"),
        "run_at": iso_utc(doc["run_at"]), "valid_at": iso_utc(doc["valid_at"]),
        "echeance_min": doc["echeance_min"],
        "moyen": moyen,
        "header": {"nx": doc["nx"], "ny": doc["ny"], "lo1": bbox["west"], "la1": bbox["north"],
                   "lo2": bbox["east"], "la2": bbox["south"], "dx": doc["pas_deg"], "dy": doc["pas_deg"],
                   "unite": "m/s"},
        "u": _arrondir(u), "v": _arrondir(v), "rafale": _arrondir(rafale),
        "u_rafale": _arrondir(ur), "v_rafale": _arrondir(vr),
        # Couverture nuageuse totale en % (AROME) : couche Nuages
        "nuages": _arrondir(_nuages(db, doc)),
    }


# ---------------------------------------------------------------- au point

def _indice(doc, lat, lon):
    b = doc["bbox"]
    if not (b["west"] - 0.005 <= lon <= b["east"] + 0.005 and b["south"] - 0.005 <= lat <= b["north"] + 0.005):
        return None
    c = int(round((lon - b["west"]) / doc["pas_deg"]))
    li = int(round((b["north"] - lat) / doc["pas_deg"]))
    return max(0, min(doc["ny"] - 1, li)), max(0, min(doc["nx"] - 1, c))


def _valeur(t, ij):
    if t is None or ij is None:
        return None
    x = float(t[ij[0], ij[1]])
    return x if math.isfinite(x) else None


def serie(db, lat=LAT_CIRCUIT, lon=LON_CIRCUIT, horizon_h=HORIZON_H, maintenant=None):
    """Serie direction / vitesse / rafale au point, sur la ligne du temps de la carte."""
    maintenant = maintenant or maintenant_utc()
    horizon = max(1, min(HORIZON_H, int(horizon_h or HORIZON_H)))
    lignes = []
    for t in trames(db, maintenant)["trames"]:
        valide = datetime.strptime(t["valid_at"][:19], "%Y-%m-%dT%H:%M:%S")
        if valide > maintenant + timedelta(hours=horizon):
            break
        g = _grilles_trame(db, t["id"])
        if not g:
            continue
        doc, u, v, rafale, ur, vr, moyen = g
        ij = _indice(doc, lat, lon)
        if ij is None:
            return {"disponible": False, "motif": "point_hors_emprise"}
        uu, vv = _valeur(u, ij), _valeur(v, ij)
        vitesse = math.hypot(uu, vv) if uu is not None and vv is not None else None
        # Sans vent moyen (AROME absent), la direction est celle de la rafale.
        direction = direction_deg(uu, vv) if vitesse is not None else direction_deg(_valeur(ur, ij), _valeur(vr, ij))
        lignes.append({
            "valid_at": t["valid_at"], "source": t["source"], "moyen": moyen,
            "direction_deg": direction, "direction": cardinal(direction),
            "vent_ms": None if vitesse is None else round(vitesse, 1), "vent_kmh": kmh(vitesse),
            "rafale_ms": None if _valeur(rafale, ij) is None else round(_valeur(rafale, ij), 1),
            "rafale_kmh": kmh(_valeur(rafale, ij)),
        })
    return {"disponible": bool(lignes), "lat": lat, "lon": lon, "serie": lignes,
            "motif": None if lignes else "aucune_prevision"}


def observation(db, maintenant=None):
    """Derniere mesure de la station Le Mans (DPObs), None si absente ou trop vieille."""
    maintenant = maintenant or maintenant_utc()
    doc = db[COLLECTION_OBS].find_one({}, {"_id": 0}, sort=[("valid_at", -1)])
    if not doc or (maintenant - doc["valid_at"]) > timedelta(minutes=OBS_AGE_MAX_MIN):
        return None
    return {"station": doc.get("station"), "valid_at": iso_utc(doc["valid_at"]),
            "direction_deg": doc.get("dd"), "direction": cardinal(doc.get("dd")),
            "vent_kmh": kmh(doc.get("ff_ms")), "rafale_kmh": kmh(doc.get("fxi_ms"))}


def actuel(db, lat=LAT_CIRCUIT, lon=LON_CIRCUIT, maintenant=None):
    """Mesure de station + premiere trame du modele, et l'ecart entre les deux."""
    maintenant = maintenant or maintenant_utc()
    s = serie(db, lat, lon, horizon_h=1, maintenant=maintenant)
    modele = s["serie"][0] if s.get("serie") else None
    obs = observation(db, maintenant)
    ecart = None
    if obs and modele and obs.get("vent_kmh") is not None and modele.get("vent_kmh") is not None:
        ecart = {"vent_kmh": modele["vent_kmh"] - obs["vent_kmh"],
                 "rafale_kmh": (modele["rafale_kmh"] - obs["rafale_kmh"])
                 if modele.get("rafale_kmh") is not None and obs.get("rafale_kmh") is not None else None}
    return {"disponible": bool(modele or obs), "modele": modele, "mesure": obs,
            "ecart_modele_mesure": ecart,
            "note_mesure": None if obs else "mesure de station indisponible"}


def alerte_rafale(db, seuil_kmh, maintenant=None):
    """Premieres echeances ou la rafale max sur l'enceinte atteint le seuil."""
    maintenant = maintenant or maintenant_utc()
    seuil = float(seuil_kmh)
    liste = trames(db, maintenant)["trames"]
    depassements = [t for t in liste
                    if t.get("rafale_max_site_kmh") is not None and t["rafale_max_site_kmh"] >= seuil]
    pic = max(liste, key=lambda t: t.get("rafale_max_site_kmh") or 0, default=None)
    return {
        "seuil_kmh": seuil,
        "depasse": bool(depassements),
        "premieres": [{"valid_at": t["valid_at"], "source": t["source"],
                       "rafale_max_site_kmh": t["rafale_max_site_kmh"]} for t in depassements[:5]],
        "nombre_echeances": len(depassements),
        "pic": ({"valid_at": pic["valid_at"], "rafale_max_site_kmh": pic.get("rafale_max_site_kmh"),
                 "source": pic["source"]} if pic and pic.get("rafale_max_site_kmh") is not None else None),
    }


# ---------------------------------------------------------------- points cles

def _centre_feature(feature):
    g = feature.get("geometry") or {}
    c = g.get("coordinates")
    if g.get("type") == "Point" and c and len(c) >= 2:
        return float(c[1]), float(c[0])
    return None


def points_cles(db):
    """[{nom, lat, lon}] des fleches renforcees de la carte."""
    conf = (((db["parametrages"].find_one({"event": "__GLOBAL__", "year": "__GLOBAL__"},
                                          {"data.meteo.points_vent": 1}) or {}).get("data") or {})
            .get("meteo") or {}).get("points_vent")
    demandes = conf if isinstance(conf, list) and conf else POINTS_CLES_DEFAUT
    index = {}
    for collection in ("tribunes", "hospitalites"):
        d = db[collection].find_one() or {}
        for f in d.get("features") or []:
            p = f.get("properties") or {}
            nom = str(p.get("Name") or p.get("NOM") or "").strip()
            pos = _centre_feature(f)
            if nom and pos and nom.upper() not in index:
                index[nom.upper()] = (nom, pos)
    points = []
    for demande in demandes:
        if isinstance(demande, dict) and demande.get("lat") is not None and demande.get("lon") is not None:
            points.append({"nom": str(demande.get("nom") or ""), "lat": float(demande["lat"]),
                           "lon": float(demande["lon"])})
            continue
        trouve = index.get(str(demande).strip().upper())
        if trouve:
            points.append({"nom": trouve[0].title(), "lat": trouve[1][0], "lon": trouve[1][1]})
    return points
