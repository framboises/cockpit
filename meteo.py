"""Blueprint meteo : configuration de la zone de veille et lecture des grilles.

Cote configuration, une seule chose est parametrable : l'emprise sur laquelle
les collecteurs travaillent. Elle est stockee dans parametrages sur le couple
__GLOBAL__ et NON par evenement -- le circuit ne bouge pas d'une epreuve a
l'autre, et dupliquer la bbox sur 28 documents garantirait qu'ils divergent.

DEUX EMPRISES, PAS UNE
  bbox_site   l'enveloppe des zones d'exploitation, pour le calcul par zone
  veille_km   le rayon de detection autour du circuit, pour voir arriver les
              cellules. Une cellule a 35 km/h qu'on veut voir 45 min a
              l'avance est a 26 km : une emprise calee sur la seule enceinte
              ne la montrerait pas.

Les collecteurs (tools/Meteo/) lisent cette configuration ; ils ne sont pas
pilotes depuis Cockpit. Ce blueprint ne fait qu'exposer et valider.

Erreurs au format {"ok": false, "error": "<code>"} -- convention field.py:594.
Ne jamais utiliser abort(404) : le handler global d'app.py redirige vers / et
casserait un appel XHR.
"""

import math
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

# admin_required vient de field.py comme dans routing_overrides.py : il fait
# ses propres imports depuis app a l'appel, ce qui evite l'import circulaire
# (app importe ce module pour enregistrer le blueprint).
from field import admin_required

# Le calcul du mur et la lecture des bulletins de vigilance vivent dans
# meteo_etat, qui ne connait pas Flask : la montre les consomme sans passer par
# ce blueprint, et les seuils n'existent donc qu'a un seul endroit. L'import est
# sans cycle -- meteo_etat ne depend que de meteo_thermique.
import meteo_etat
from meteo_etat import etat_vigilance, niveau_vigilance


def lecture_required(fonction):
    """Acces en lecture : role manager suffit.

    Le mur du PC Organisation tourne sous un compte d'exploitation, pas sous
    un compte admin -- comme /circulation, qui est en manager. Exiger admin
    sur les endpoints de lecture afficherait une page vide sur la TV.

    L'ecriture (PUT /config) reste, elle, reservee aux admins.

    role_required est importe a l'appel et non en tete de module : app importe
    ce fichier pour enregistrer le blueprint, l'import inverse serait circulaire.
    """
    from functools import wraps

    @wraps(fonction)
    def enveloppe(*args, **kwargs):
        from app import role_required
        return role_required("manager")(fonction)(*args, **kwargs)

    return enveloppe

meteo_bp = Blueprint("meteo", __name__, url_prefix="/api/meteo")

# Centre du circuit, utilise comme repli et pour deriver la bbox de veille.
LON_CIRCUIT = 0.2240
LAT_CIRCUIT = 47.9450

VEILLE_KM_DEFAUT = 40
VEILLE_KM_MIN = 10
VEILLE_KM_MAX = 150

# Enveloppe des 156 terrains, marge de 500 m comprise. Sert de defaut et de
# controle : une bbox qui ne l'englobe pas laisserait des zones hors grille.
BBOX_SITE_DEFAUT = {"west": 0.188, "south": 47.906, "east": 0.357, "north": 48.015}

# Au-dela, la grille devient inutilement lourde sans rien apporter.
COTE_MAX_DEG = 3.0

# L'horizon et les seuils du mur (rafale, pluie, humidite des sols) sont dans
# meteo_etat : c'est la que le calcul les applique, et la montre les partage.


def _db():
    from app import db
    return db


def _config_doc():
    return _db()["parametrages"].find_one(
        {"event": "__GLOBAL__", "year": "__GLOBAL__"}) or {}


def _config_meteo():
    donnees = (_config_doc().get("data") or {}).get("meteo") or {}
    bbox = donnees.get("bbox_site") or dict(BBOX_SITE_DEFAUT)
    return {
        "bbox_site": bbox,
        "veille_km": int(donnees.get("veille_km") or VEILLE_KM_DEFAUT),
        "updated_at": donnees.get("updated_at"),
        "updated_by": donnees.get("updated_by"),
        "par_defaut": "bbox_site" not in donnees,
    }


def bbox_veille(veille_km, lon=LON_CIRCUIT, lat=LAT_CIRCUIT):
    """Emprise de detection, derivee du rayon de veille."""
    dlat = veille_km / 111.32
    dlon = veille_km / (111.32 * math.cos(math.radians(lat)))
    return {"west": lon - dlon, "south": lat - dlat,
            "east": lon + dlon, "north": lat + dlat}


def _valider_bbox(bbox):
    """Retourne (ok, code d'erreur). Les controles disent pourquoi, pas juste non."""
    try:
        ouest = float(bbox["west"]); sud = float(bbox["south"])
        est = float(bbox["east"]); nord = float(bbox["north"])
    except (KeyError, TypeError, ValueError):
        return False, "bbox_incomplete"

    if not (-180 <= ouest < est <= 180) or not (-90 <= sud < nord <= 90):
        return False, "bbox_invalide"

    reference = BBOX_SITE_DEFAUT
    if (ouest > reference["west"] or est < reference["east"]
            or sud > reference["south"] or nord < reference["north"]):
        return False, "bbox_trop_petite"

    if (est - ouest) > COTE_MAX_DEG or (nord - sud) > COTE_MAX_DEG:
        return False, "bbox_trop_grande"

    return True, None


@meteo_bp.route("/config", methods=["GET"])
@lecture_required
def get_config():
    configuration = _config_meteo()
    configuration["bbox_veille"] = bbox_veille(configuration["veille_km"])
    configuration["bbox_site_defaut"] = BBOX_SITE_DEFAUT
    configuration["contraintes"] = {
        "veille_km_min": VEILLE_KM_MIN,
        "veille_km_max": VEILLE_KM_MAX,
        "cote_max_deg": COTE_MAX_DEG,
    }
    return jsonify(configuration)


@meteo_bp.route("/config", methods=["PUT"])
@admin_required
def set_config():
    donnees = request.get_json(silent=True) or {}
    bbox = donnees.get("bbox_site") or {}
    ok, erreur = _valider_bbox(bbox)
    if not ok:
        return jsonify({"ok": False, "error": erreur}), 400

    try:
        veille = int(donnees.get("veille_km") or VEILLE_KM_DEFAUT)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "veille_km_invalide"}), 400
    if not (VEILLE_KM_MIN <= veille <= VEILLE_KM_MAX):
        return jsonify({"ok": False, "error": "veille_km_hors_bornes"}), 400

    payload = getattr(request, "user_payload", {}) or {}
    auteur = " ".join(x for x in (payload.get("firstname"), payload.get("lastname")) if x) \
        or payload.get("email") or "inconnu"

    _db()["parametrages"].update_one(
        {"event": "__GLOBAL__", "year": "__GLOBAL__"},
        {"$set": {
            "data.meteo.bbox_site": {k: float(bbox[k]) for k in ("west", "south", "east", "north")},
            "data.meteo.veille_km": veille,
            "data.meteo.updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
            "data.meteo.updated_by": auteur,
        }},
        upsert=True,
    )
    return jsonify({"ok": True, "bbox_veille": bbox_veille(veille)})


@meteo_bp.route("/etat", methods=["GET"])
@lecture_required
def etat_collecte():
    """Fraicheur des flux meteo.

    L'API radar ne sert que la derniere image : une interruption creuse un
    trou definitif dans l'animation du passe. La fraicheur doit donc etre
    visible ici, et pas seulement dans cron_status.json cote serveur.
    """
    db = _db()
    maintenant = datetime.now(timezone.utc).replace(tzinfo=None)
    flux = []
    # PIAF se juge sur run_at et NON sur valid_at : ses echeances sont dans le
    # futur, donc valid_at donnerait un age negatif -- et surtout, un flux
    # arrete continuerait a paraitre frais tant que sa derniere prevision n'est
    # pas echue, soit deux heures d'angle mort.
    for nom, libelle, seuil_min, champ in (
        ("obs_lame_eau", "Radar observe", 20, "valid_at"),
        ("piaf", "Prevision PIAF", 30, "run_at"),
    ):
        dernier = db["meteo_grilles"].find_one({"flux": nom}, sort=[(champ, -1)])
        if not dernier or not dernier.get(champ):
            flux.append({"flux": nom, "libelle": libelle, "etat": "absent"})
            continue
        age_min = (maintenant - dernier[champ]).total_seconds() / 60.0
        flux.append({
            "flux": nom,
            "libelle": libelle,
            "valid_at": dernier[champ].isoformat(),
            "reference": champ,
            "age_min": round(age_min, 1),
            "latence_s": dernier.get("latence_s"),
            "max_mmh": dernier.get("max_mmh"),
            "etat": "ok" if age_min <= seuil_min else "retard",
            "seuil_min": seuil_min,
        })

    # Meteo-France publie la climatologie a J+1, parfois J+2 : un seuil de 48 h
    # signalerait un retard sur une chaine parfaitement saine.
    for collection, nom, libelle, seuil_h in (
        ("donnees_meteo", "climato", "Climatologie", 72),
        ("meteo_sol", "sol", "Humidite des sols", 72),
    ):
        champ = "Date" if collection == "donnees_meteo" else "date"
        dernier = db[collection].find_one(sort=[(champ, -1)])
        if not dernier:
            flux.append({"flux": nom, "libelle": libelle, "etat": "absent"})
            continue
        valeur = dernier.get(champ)
        if isinstance(valeur, str):
            try:
                valeur = datetime.strptime(valeur, "%Y%m%d")
            except ValueError:
                valeur = None
        if not isinstance(valeur, datetime):
            flux.append({"flux": nom, "libelle": libelle, "etat": "illisible"})
            continue
        age_h = (maintenant - valeur).total_seconds() / 3600.0
        flux.append({
            "flux": nom, "libelle": libelle,
            "valid_at": valeur.isoformat(),
            "age_h": round(age_h, 1),
            "etat": "ok" if age_h <= seuil_h else "retard",
            "seuil_h": seuil_h,
        })

    return jsonify({"flux": flux, "maintenant": maintenant.isoformat()})


@meteo_bp.route("/vigilance", methods=["GET"])
@lecture_required
def vigilance():
    """Dernier bulletin, avec sa fraicheur.

    Produit de securite publique : restitution sans reinterpretation,
    horodatage du producteur toujours joint, et peremption etablie sur la
    validite qu'il publie lui-meme. Un bulletin perime doit se declarer tel,
    jamais se laisser lire comme courant -- ni l'inverse.
    """
    dernier = _db()["meteo_vigilance"].find_one(
        {"departement": "72"}, {"_id": 0}, sort=[("update_time", -1)])
    if not dernier:
        return jsonify({"disponible": False, "motif": "aucun_bulletin"})

    dernier["disponible"] = True
    dernier.update(etat_vigilance(dernier))
    dernier.update(niveau_vigilance(dernier))
    return jsonify(dernier)


@meteo_bp.route("/analyse", methods=["GET"])
@lecture_required
def analyse():
    """Tout ce qu'il faut au bloc Analyse meteo, en une requete.

    Previsions horaires enrichies des indices de contrainte thermique,
    vigilance, humidite des sols, etat du radar. Les indices sont calcules
    ici et non cote navigateur : une formule de securite doit vivre a un seul
    endroit, testable.
    """
    import meteo_thermique as thermique

    db = _db()
    aujourdhui = datetime.now()
    limite = (aujourdhui + timedelta(days=4)).strftime("%Y-%m-%d")

    jours = []
    for document in db["meteo_previsions"].find(
            {"Date": {"$gte": aujourdhui.strftime("%Y-%m-%d"), "$lte": limite}},
            {"_id": 0}).sort("Date", 1):
        heures = []
        for entree in document.get("Heures") or []:
            temperature = entree.get("Température (°C)")
            humidite = entree.get("Humidité (%)")
            vent = entree.get("Vent moyen (km/h)")
            try:
                heure_num = int(str(entree.get("Heure", "0")).split(":")[0])
            except ValueError:
                heure_num = None

            bloc = {
                "heure": entree.get("Heure"),
                "temperature_c": temperature,
                "pluie_mm": entree.get("Pluviométrie (mm)"),
                "vent_moyen_kmh": vent,
                "vent_rafale_kmh": entree.get("Vent rafale (km/h)"),
                "vent_direction_deg": entree.get("Direction vent (°)"),
                "vent_direction": entree.get("Direction vent"),
                "humidite_pct": humidite,
                "nebulosite_pct": entree.get("Nebulosite (%)"),
                "rayonnement_jm2": entree.get("Rayonnement (J/m2)"),
                "cape": entree.get("CAPE (J/kg)"),
                "foudre": entree.get("Foudre (impacts/km2)"),
                "grele": entree.get("Grele (kg/m2)"),
                "visibilite_m": entree.get("Visibilite (m)"),
                "pression_hpa": entree.get("Pression (hPa)"),
                "source": entree.get("source"),
                # La volatilite de la prevision : de combien le modele s'est
                # ravise depuis le passage precedent. Information propre a
                # cette collection, qu'aucune API ne rend.
                "revision_temp": entree.get("historique_temperature"),
                "revision_pluie": entree.get("historique_pluie"),
            }
            if temperature is not None and humidite is not None:
                bloc.update(thermique.analyser(
                    temperature, humidite,
                    vent_kmh=vent, heure=heure_num,
                    rayonnement_jm2=entree.get("Rayonnement (J/m2)"),
                    tw_natif=entree.get("Temp humide (°C)")))
            bloc["orage"] = thermique.risque_orage(
                entree.get("CAPE (J/kg)"), entree.get("Foudre (impacts/km2)"),
                entree.get("Grele (kg/m2)"))
            heures.append(bloc)

        jours.append({
            "date": document.get("Date"),
            "heures": heures,
            "contrainte": thermique.pic_contrainte(heures),
        })

    # Humidite des sols : le vrai indicateur de secheresse, la ou un cumul de
    # pluie ne dit rien de la reserve en eau.
    sol = db["meteo_sol"].find_one({"circuit": True}, {"_id": 0}, sort=[("date", -1)])
    sol_serie = list(db["meteo_sol"].find(
        {"circuit": True}, {"_id": 0, "date": 1, "swi": 1, "sswi_10j": 1}
    ).sort("date", -1).limit(30))

    radar = db["meteo_grilles"].find_one({"flux": "obs_lame_eau"},
                                         {"_id": 0, "valeurs": 0},
                                         sort=[("valid_at", -1)])

    return jsonify({
        "jours": jours,
        "sol": sol,
        "sol_serie": list(reversed(sol_serie)),
        "radar": {
            "valid_at": radar["valid_at"].isoformat() if radar else None,
            "max_mmh": radar.get("max_mmh") if radar else None,
            "png": f"/static/meteo/radar/{radar['png_path']}" if radar else None,
            "bbox": radar.get("bbox") if radar else None,
        } if radar else None,
        "seuils": {
            "wbgt": thermique.SEUILS_WBGT,
            "humidex": thermique.SEUILS_HUMIDEX,
        },
        "genere_a": datetime.now().isoformat(timespec="seconds"),
    })


@meteo_bp.route("/mur", methods=["GET"])
@lecture_required
def mur():
    """Tout ce qu'affiche le mur du PC Organisation, en une requete.

    Le calcul vit dans meteo_etat, partage avec la montre : les seuils de
    rafale, de WBGT et d'orage ne doivent exister qu'a un seul endroit,
    sinon le poignet finit par contredire le mur.
    """
    return jsonify(meteo_etat.etat_mur(_db(), datetime.now(), _config_meteo()))


@meteo_bp.route("/radar/sequence", methods=["GET"])
@lecture_required
def sequence_radar():
    """Images ordonnees pour l'animation : observation puis prevision.

    C'est tout ce dont une page radar a besoin -- des URL et des bornes. Aucun
    decodage ni reprojection cote navigateur : le travail est fait a la
    collecte, une fois, pour que les deux flux partagent la meme grille.
    """
    db = _db()
    try:
        heures = max(1, min(12, int(request.args.get("heures", 3))))
    except ValueError:
        heures = 3

    depuis = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=heures)
    images = []

    for document in db["meteo_grilles"].find(
            {"flux": "obs_lame_eau", "valid_at": {"$gte": depuis}},
            {"valeurs": 0}).sort("valid_at", 1):
        images.append({
            "flux": "observation",
            "valid_at": document["valid_at"].isoformat(),
            "url": f"/static/meteo/radar/{document['png_path']}",
            "max_mmh": document.get("max_mmh"),
        })

    dernier_run = db["meteo_grilles"].find_one({"flux": "piaf"}, sort=[("run_at", -1)])
    if dernier_run:
        for document in db["meteo_grilles"].find(
                {"flux": "piaf", "run_at": dernier_run["run_at"]},
                {"valeurs": 0}).sort("echeance_min", 1):
            images.append({
                "flux": "prevision",
                "valid_at": document["valid_at"].isoformat(),
                "echeance_min": document.get("echeance_min"),
                "url": f"/static/meteo/radar/{document['png_path']}",
                "max_mmh": document.get("max_mmh"),
            })

    bornes = None
    if images:
        reference = db["meteo_grilles"].find_one({}, {"bbox": 1})
        if reference:
            bornes = reference.get("bbox")

    return jsonify({"images": images, "bbox": bornes, "n": len(images)})
