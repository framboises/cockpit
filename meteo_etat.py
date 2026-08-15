"""Etat complet du mur meteo, sans Flask.

meteo.py porte un blueprint et importe app : watch_pages ne peut pas
l'importer sans cycle. Or la montre doit repeter EXACTEMENT ce que le mur
decide -- les seuils de rafale, de WBGT et d'orage n'ont pas a exister en
deux endroits. Le calcul vit donc ici, et meteo.mur() comme
watch_pages.build_meteo le consomment.

Le bloc vigilance a suivi le meme chemin : etat_mur en a besoin, et un module
sans Flask ne peut pas aller le chercher dans meteo.py. meteo.py le reimporte
d'ici, si bien que /api/meteo/vigilance et app.py continuent d'appeler les
memes fonctions -- il n'en existe toujours qu'une version.
"""

from datetime import datetime, timedelta, timezone

import meteo_thermique as thermique

# ---------------------------------------------------------------------------
# Horizon et seuils du mur
# ---------------------------------------------------------------------------

# Douze heures : la duree d'une vacation. Assez pour preparer la releve, assez
# peu pour que chaque colonne reste lisible a quatre metres.
HORIZON_HEURES = 12

# Seuils de rafale. Ce sont les seuils INTERNES deja en vigueur dans Cockpit
# (app.py, METEO_THRESHOLDS) prolonges par ceux des consignes de ce module.
# Ils ne transcrivent aucune prescription reglementaire : la tenue reelle d'une
# tribune demontable ou d'un chapiteau depend de son homologation, que seul
# l'exploitant connait.
VENT_VIGILANCE_KMH = 40
VENT_DANGER_KMH = 60
VENT_CRITIQUE_KMH = 80

# Pluie horaire. 5 mm suffit a compromettre la portance d'un parking en herbe
# deja sature ; 15 mm est le seuil d'alerte historique de Cockpit.
PLUIE_VIGILANCE_MM = 5
PLUIE_ALERTE_MM = 15

# Indice d'humidite des sols. En dessous, la reserve utile est quasi epuisee :
# c'est le volet risque incendie, pas la portance.
SWI_TRES_SEC = 0.2


# ---------------------------------------------------------------------------
# Peremption d'un bulletin de vigilance
#
# LA VALIDITE EST PUBLIEE PAR METEO-FRANCE, ON NE L'INVENTE PAS.
#
# Chaque periode du bulletin porte `begin_validity_time` et `end_validity_time`
# (stockes en `debut` / `fin` par collecte_vigilance.py). Le bulletin du matin
# couvre typiquement 04:00 -> 22:00 UTC pour l'echeance J, puis J1 prend le
# relais. Tant qu'une periode couvre l'instant present, le bulletin dit quelque
# chose de maintenant : il n'est pas perime.
#
# Un seuil d'age fixe etait faux. Meteo-France publie deux fois par jour, a 6 h
# et 16 h locales -- soit un intervalle normal allant jusqu'a 14 h. Le seuil de
# six heures qui figurait ici declarait donc "perime" un bulletin parfaitement
# courant tous les jours de midi a 16 h, et de minuit a 6 h.
#
# L'age reste expose : il sert l'infobulle et le repli. Mais il ne decide plus.
# ---------------------------------------------------------------------------

# Repli quand le bulletin ne porte aucune periode exploitable. Choisi au-dessus
# de l'intervalle normal maximal entre deux publications (16 h -> 6 h = 14 h),
# avec une marge : au-dela, c'est la collecte qui est en cause, pas le bulletin.
PEREMPTION_REPLI_H = 15


def _instant(valeur):
    """Parse un horodatage ISO du producteur. None si illisible."""
    if not valeur:
        return None
    try:
        return datetime.fromisoformat(str(valeur).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


ORDRE_COULEURS = ["vert", "jaune", "orange", "rouge"]


def niveau_vigilance(bulletin, maintenant=None):
    """Couleur du bulletin, distinguee selon l'echeance.

    DEUX COULEURS, PAS UNE, ET C'EST LA TOUT LE PROBLEME.

    Un bulletin porte l'echeance J et l'echeance J1. La jauge du cockpit ne
    retenait que J ; le panneau developpe prenait le maximum des deux. Un jaune
    annonce pour demain donnait donc "RAS — vigilance vert" dans le petit bloc
    et "vigilance jaune" dans le grand, au meme instant, sur un produit de
    securite publique. Constate en production.

    La regle est desormais unique et rendue ici :
      couleur_jour  ce que dit le bulletin pour MAINTENANT -- ce qu'affiche
                    toute jauge d'etat courant
      couleur_max   le pire sur l'ensemble des echeances -- ce qui sert a
                    annoncer, jamais a qualifier l'instant present
    Les deux sont exposees, et l'appelant choisit en connaissance de cause.
    """
    resultat = {"couleur_jour": "vert", "couleur_max": "vert",
                "phenomenes_jour": [], "phenomenes_max": [], "echeances": []}
    if not bulletin:
        return resultat

    maintenant = maintenant or datetime.now(timezone.utc)

    def rang(couleur):
        return ORDRE_COULEURS.index(couleur) if couleur in ORDRE_COULEURS else 0

    for periode in bulletin.get("periodes") or []:
        couleur = periode.get("couleur_max") or "vert"
        noms = [p.get("nom") for p in (periode.get("phenomenes") or []) if p.get("nom")]

        debut = _instant(periode.get("debut"))
        fin = _instant(periode.get("fin"))
        courante = bool(debut and fin and debut <= maintenant <= fin)

        resultat["echeances"].append({
            "echeance": periode.get("echeance"),
            "couleur": couleur, "phenomenes": noms,
            "debut": periode.get("debut"), "fin": periode.get("fin"),
            "courante": courante,
        })

        if rang(couleur) > rang(resultat["couleur_max"]):
            resultat["couleur_max"] = couleur
        for nom in noms:
            if nom not in resultat["phenomenes_max"]:
                resultat["phenomenes_max"].append(nom)

        if courante:
            if rang(couleur) > rang(resultat["couleur_jour"]):
                resultat["couleur_jour"] = couleur
            for nom in noms:
                if nom not in resultat["phenomenes_jour"]:
                    resultat["phenomenes_jour"].append(nom)

    # Repli : si aucune periode ne couvre l'instant present (bulletin en cours
    # de bascule), l'echeance J fait foi plutot que rien.
    if not any(e["courante"] for e in resultat["echeances"]):
        for e in resultat["echeances"]:
            if e["echeance"] in (None, "J"):
                resultat["couleur_jour"] = e["couleur"]
                resultat["phenomenes_jour"] = list(e["phenomenes"])
                break

    return resultat


def etat_vigilance(bulletin, maintenant=None):
    """Fraicheur d'un bulletin. Deux faits distincts, a ne pas confondre.

      perime           la validite publiee par Meteo-France est passee : le
                       producteur ne dit plus rien de l'instant present.
      retard_collecte  le bulletin couvre encore maintenant, mais aucune
                       actualisation n'est arrivee depuis plus d'un cycle de
                       publication. Il reste le meilleur element disponible, il
                       n'est simplement plus confirme.

    La distinction compte. Un bulletin du matin couvre J et J1 : sa validite
    publiee s'etend sur pres de deux jours. Si la collecte s'arrete, il resterait
    "valide" trente heures durant, alors qu'une reactualisation en orange a pu
    etre emise entre-temps sans qu'on la voie. Le premier drapeau seul laisserait
    donc passer ce cas.

    Retourne {perime, retard_collecte, age_h, valide_jusqua, motif}.
    `motif` dit sur quoi la decision repose, pour ne pas avoir a la deviner.
    """
    if not bulletin:
        return {"perime": None, "retard_collecte": None, "age_h": None,
                "valide_jusqua": None, "motif": "aucun_bulletin"}

    maintenant = maintenant or datetime.now(timezone.utc)

    emis = _instant(bulletin.get("update_time"))
    age_h = round((maintenant - emis).total_seconds() / 3600.0, 1) if emis else None
    retard = bool(age_h is not None and age_h > PEREMPTION_REPLI_H)

    # Validite publiee : on cherche la periode qui couvre l'instant present.
    fin_couvrante = None
    fin_max = None
    for periode in bulletin.get("periodes") or []:
        debut = _instant(periode.get("debut"))
        fin = _instant(periode.get("fin"))
        if fin and (fin_max is None or fin > fin_max):
            fin_max = fin
        if debut and fin and debut <= maintenant <= fin:
            if fin_couvrante is None or fin > fin_couvrante:
                fin_couvrante = fin

    if fin_couvrante is not None:
        return {"perime": False, "retard_collecte": retard, "age_h": age_h,
                "valide_jusqua": fin_couvrante.isoformat(),
                "motif": "validite_publiee"}

    if fin_max is not None:
        # Toutes les periodes sont derriere nous : le producteur lui-meme ne
        # dit plus rien de maintenant.
        return {"perime": True, "retard_collecte": retard, "age_h": age_h,
                "valide_jusqua": fin_max.isoformat(),
                "motif": "validite_publiee_depassee"}

    # Aucune periode exploitable : repli sur l'age.
    if age_h is None:
        return {"perime": None, "retard_collecte": None, "age_h": None,
                "valide_jusqua": None, "motif": "horodatage_illisible"}
    return {"perime": retard, "retard_collecte": retard, "age_h": age_h,
            "valide_jusqua": None, "motif": "repli_sur_age"}


def etat_mur(db, maintenant):
    """Tout ce qu'affiche le mur du PC Organisation, en un appel.

    Une page de mur n'est pas une page de consultation : personne ne clique,
    personne ne cherche. Elle doit repondre sans qu'on la sollicite aux
    questions du moment -- va-t-il pleuvoir, quand, et faut-il faire quelque
    chose. D'ou une charge unique, rafraichie seule.

    Corps deplace verbatim depuis meteo.mur(). Rend le meme dictionnaire :
    maintenant, actuel, prochaines, prochaine_pluie, consignes, contraintes,
    verdict, fraicheur, vigilance, sol, radar.

    PAS DE PARAMETRE DE CONFIGURATION. Le mur ne lit ni la bbox de site ni le
    rayon de veille : les accepter obligerait l'appelant a charger le document
    parametrages -- un find_one non indexe -- a chaque affichage, pour une
    valeur que personne ne lit. Le jour ou la meteo devra lire sa config, le
    parametre reviendra ; d'ici la il couterait un aller-retour Mongo par
    rafraichissement, sur un ecran qui se rafraichit tout seul en continu.
    """
    # --- Prochaine pluie, d'apres PIAF ---
    #
    # C'est LA question du jour J. On lit le dernier run et on cherche la
    # premiere echeance ou la zone recoit quelque chose de perceptible.
    prochaine_pluie = None
    dernier_run = db["meteo_grilles"].find_one({"flux": "piaf"}, sort=[("run_at", -1)])
    if dernier_run:
        echeances = list(db["meteo_grilles"].find(
            {"flux": "piaf", "run_at": dernier_run["run_at"]},
            {"_id": 0, "valeurs": 0}).sort("echeance_min", 1))
        pluvieuses = [e for e in echeances if (e.get("max_mmh") or 0) >= 0.2]
        prochaine_pluie = {
            "run_at": dernier_run["run_at"].isoformat(),
            "horizon_min": max((e.get("echeance_min") or 0) for e in echeances) if echeances else None,
            "attendue": bool(pluvieuses),
        }
        if pluvieuses:
            premiere = pluvieuses[0]
            pic = max(pluvieuses, key=lambda e: e.get("max_mmh") or 0)
            prochaine_pluie.update({
                "dans_min": premiere.get("echeance_min"),
                "a": premiere["valid_at"].isoformat(),
                "intensite_mmh": premiere.get("max_mmh"),
                "pic_mmh": pic.get("max_mmh"),
                "pic_dans_min": pic.get("echeance_min"),
            })

    # --- Conditions du moment et horizon ---
    #
    # L'horizon TRAVERSE MINUIT. Il etait borne au document du jour : a 22 h, le
    # mur n'affichait plus que deux creneaux, et l'equipe de nuit n'avait rien.
    # Or c'est precisement la nuit qu'on prepare le lendemain.
    heure_courante = maintenant.strftime("%H:00")
    actuel, prochaines = None, []
    for decalage in range(3):
        jour = (maintenant + timedelta(days=decalage)).strftime("%Y-%m-%d")
        document = db["meteo_previsions"].find_one({"Date": jour})
        if not document:
            continue
        for entree in document.get("Heures") or []:
            if decalage == 0 and entree.get("Heure", "") < heure_courante:
                continue
            entree = {**entree, "_jour": jour, "_decalage": decalage}
            if actuel is None:
                actuel = entree
            if len(prochaines) < HORIZON_HEURES:
                prochaines.append(entree)
        if len(prochaines) >= HORIZON_HEURES:
            break
    if actuel is None:
        document = db["meteo_previsions"].find_one({"Date": maintenant.strftime("%Y-%m-%d")})
        heures = (document or {}).get("Heures") or []
        actuel = heures[-1] if heures else None

    def enrichir(entree):
        if not entree:
            return None
        temperature = entree.get("Température (°C)")
        humidite = entree.get("Humidité (%)")
        try:
            heure_num = int(str(entree.get("Heure", "0")).split(":")[0])
        except ValueError:
            heure_num = None
        bloc = {
            "heure": entree.get("Heure"),
            "temperature_c": temperature,
            "humidite_pct": humidite,
            "pluie_mm": entree.get("Pluviométrie (mm)"),
            "vent_moyen_kmh": entree.get("Vent moyen (km/h)"),
            "vent_rafale_kmh": entree.get("Vent rafale (km/h)"),
            "vent_direction": entree.get("Direction vent"),
            "vent_direction_deg": entree.get("Direction vent (°)"),
            "nebulosite_pct": entree.get("Nebulosite (%)"),
            "source": entree.get("source"),
        }
        if temperature is not None and humidite is not None:
            bloc.update(thermique.analyser(
                temperature, humidite,
                vent_kmh=entree.get("Vent moyen (km/h)"), heure=heure_num,
                rayonnement_jm2=entree.get("Rayonnement (J/m2)"),
                tw_natif=entree.get("Temp humide (°C)")))
        bloc["orage"] = thermique.risque_orage(
            entree.get("CAPE (J/kg)"), entree.get("Foudre (impacts/km2)"),
            entree.get("Grele (kg/m2)"))
        return bloc

    actuel_enrichi = enrichir(actuel)
    suite = [enrichir(x) for x in prochaines]

    # --- Consignes : ce qu'il y a a faire, pas seulement a savoir ---
    consignes = []
    for bloc in suite:
        if not bloc:
            continue
        rafale = bloc.get("vent_rafale_kmh") or 0
        if rafale >= 80:
            consignes.append({"niveau": "critique", "heure": bloc["heure"],
                              "texte": f"Rafales {rafale:.0f} km/h — evacuation des structures provisoires"})
        elif rafale >= 60:
            consignes.append({"niveau": "danger", "heure": bloc["heure"],
                              "texte": f"Rafales {rafale:.0f} km/h — securiser les structures"})
        if bloc.get("orage") and bloc["orage"]["niveau"] == "avere":
            consignes.append({"niveau": "critique", "heure": bloc["heure"],
                              "texte": bloc["orage"]["message"] + " — mise a l'abri"})
        if bloc.get("wbgt_niveau") in ("danger", "danger_extreme"):
            consignes.append({"niveau": "danger", "heure": bloc["heure"],
                              "texte": f"WBGT {bloc['wbgt_c']} °C — {bloc['wbgt_consigne']}"})
        if (bloc.get("pluie_mm") or 0) >= 5:
            consignes.append({"niveau": "vigilance", "heure": bloc["heure"],
                              "texte": f"Pluie {bloc['pluie_mm']} mm — parkings en herbe"})

    # Une consigne par heure suffit : la plus grave.
    gravite = {"critique": 0, "danger": 1, "vigilance": 2}
    par_heure = {}
    for c in consignes:
        courante = par_heure.get(c["heure"])
        if not courante or gravite[c["niveau"]] < gravite[courante["niveau"]]:
            par_heure[c["heure"]] = c
    consignes = sorted(par_heure.values(), key=lambda c: c["heure"])

    radar = db["meteo_grilles"].find_one({"flux": "obs_lame_eau"},
                                         {"_id": 0, "valeurs": 0},
                                         sort=[("valid_at", -1)])
    bulletin = db["meteo_vigilance"].find_one({"departement": "72"}, {"_id": 0},
                                              sort=[("update_time", -1)])
    sol = db["meteo_sol"].find_one({"circuit": True}, {"_id": 0}, sort=[("date", -1)])

    contraintes = _contraintes(actuel_enrichi, suite, sol)
    verdict = _verdict(prochaine_pluie, consignes, contraintes)

    return {
        "maintenant": maintenant.isoformat(timespec="seconds"),
        "actuel": actuel_enrichi,
        "prochaines": suite,
        "prochaine_pluie": prochaine_pluie,
        "consignes": consignes,
        "contraintes": contraintes,
        "verdict": verdict,
        # Fraicheur de ce qui alimente REELLEMENT le mur. Il montrait l'age de
        # la mosaique radar, qui n'y sert plus a rien depuis que la carte en est
        # partie : ce qui compte ici est la prevision immediate, d'ou sort le
        # "pluie dans N minutes", et la prevision horaire, d'ou sort le reste.
        "fraicheur": _fraicheur_mur(db, maintenant, actuel_enrichi),
        # Fraicheur jointe au bulletin : le mur ne doit pas recalculer une
        # peremption de son cote, c'est ainsi qu'une regle fausse se duplique.
        "vigilance": ({**bulletin, **etat_vigilance(bulletin),
                       **niveau_vigilance(bulletin)} if bulletin else None),
        "sol": sol,
        "radar": {
            "valid_at": radar["valid_at"].isoformat() if radar else None,
            "age_min": (round((datetime.now(timezone.utc).replace(tzinfo=None)
                               - radar["valid_at"]).total_seconds() / 60, 1)
                        if radar else None),
            "max_mmh": radar.get("max_mmh") if radar else None,
            "png": f"/static/meteo/radar/{radar['png_path']}" if radar else None,
            "bbox": radar.get("bbox") if radar else None,
        } if radar else None,
    }


# ---------------------------------------------------------------------------
# Contraintes et verdict
#
# Un mur de PC ne sert pas a consulter des mesures : il sert a repondre, sans
# qu'on le lui demande, a "est-ce que quelque chose arrive, et quand". Les
# valeurs brutes viennent apres, pour qui veut verifier.
#
# Quatre contraintes, parce que ce sont les quatre decisions que la meteo
# declenche sur une epreuve en plein air : mettre a l'abri (orage), evacuer ou
# brider des structures (vent), amenager le travail des equipes (chaleur),
# fermer ou renforcer des parkings en herbe (pluie et etat du sol).
# ---------------------------------------------------------------------------

_RANGS = {"critique": 3, "danger": 2, "vigilance": 1, "normal": 0}


def _pire(*niveaux):
    return max(niveaux, key=lambda n: _RANGS.get(n, 0))


def _premiere_heure(suite, predicat):
    """Premiere heure de l'horizon qui satisfait le predicat. None sinon."""
    for bloc in suite:
        if bloc and predicat(bloc):
            return bloc.get("heure")
    return None


def _contrainte_vent(actuel, suite):
    rafale = (actuel or {}).get("vent_rafale_kmh") or 0
    pic = max([(b.get("vent_rafale_kmh") or 0) for b in suite if b] or [0])

    def classer(v):
        if v >= VENT_CRITIQUE_KMH:
            return "critique"
        if v >= VENT_DANGER_KMH:
            return "danger"
        if v >= VENT_VIGILANCE_KMH:
            return "vigilance"
        return "normal"

    niveau = _pire(classer(rafale), classer(pic))
    consigne = {
        "critique": "Evacuation des structures provisoires",
        "danger": "Securiser les structures provisoires",
        "vigilance": "Surveiller bacher, signalisation, chapiteaux",
        "normal": "",
    }[niveau]
    return {
        "cle": "vent", "libelle": "Vent",
        "valeur": round(rafale), "unite": "km/h", "detail": "en rafales",
        "niveau": niveau, "consigne": consigne,
        "pic": round(pic), "pic_heure": _premiere_heure(
            suite, lambda b: (b.get("vent_rafale_kmh") or 0) >= max(VENT_VIGILANCE_KMH, rafale)),
        "seuils": [VENT_VIGILANCE_KMH, VENT_DANGER_KMH, VENT_CRITIQUE_KMH],
    }


def _contrainte_chaleur(actuel, suite):
    wbgt = (actuel or {}).get("wbgt_c")
    correspondance = {"danger_extreme": "critique", "danger": "danger",
                      "vigilance_haute": "vigilance", "vigilance": "vigilance",
                      "normal": "normal"}
    niveau = correspondance.get((actuel or {}).get("wbgt_niveau"), "normal")
    pic = max([(b.get("wbgt_c") or 0) for b in suite if b] or [0])
    for bloc in suite:
        if bloc:
            niveau = _pire(niveau, correspondance.get(bloc.get("wbgt_niveau"), "normal"))
    return {
        "cle": "chaleur", "libelle": "Chaleur",
        "valeur": round(wbgt, 1) if wbgt is not None else None,
        "unite": "WBGT", "detail": "contrainte thermique",
        "niveau": niveau,
        "consigne": (actuel or {}).get("wbgt_consigne") or "",
        "pic": round(pic, 1) if pic else None,
        "pic_heure": _premiere_heure(
            suite, lambda b: correspondance.get(b.get("wbgt_niveau"), "normal") != "normal"),
        # La reserve du modele accompagne la valeur : un WBGT sous-estime ne se
        # lit pas comme un WBGT confirme.
        "fiabilite": (actuel or {}).get("wbgt_fiabilite"),
    }


def _contrainte_orage(actuel, suite):
    ordre = {"avere": "critique", "possible": "danger",
             "potentiel": "vigilance", "faible": "normal"}
    courant = ((actuel or {}).get("orage") or {}).get("niveau", "faible")
    niveau = ordre.get(courant, "normal")
    for bloc in suite:
        if bloc:
            niveau = _pire(niveau, ordre.get((bloc.get("orage") or {}).get("niveau"), "normal"))
    heure = _premiere_heure(
        suite, lambda b: ordre.get((b.get("orage") or {}).get("niveau"), "normal") != "normal")
    return {
        "cle": "orage", "libelle": "Orage",
        "valeur": None, "unite": "",
        "detail": {"critique": "declenchement prevu", "danger": "possible",
                   "vigilance": "potentiel sans declencheur",
                   "normal": "pas d instabilite"}[niveau],
        "niveau": niveau,
        "consigne": "Mise a l abri a preparer" if niveau in ("critique", "danger") else "",
        "pic": None, "pic_heure": heure,
    }


def _contrainte_sol(suite, sol):
    """Portance des parkings en herbe et, a l'oppose, risque incendie."""
    cumul = sum((b.get("pluie_mm") or 0) for b in suite if b)
    swi = (sol or {}).get("swi")

    niveau, consigne, detail = "normal", "", "sol praticable"
    if cumul >= PLUIE_ALERTE_MM:
        niveau, consigne = "danger", "Parkings en herbe a fermer ou renforcer"
        detail = "cumul sur l horizon"
    elif cumul >= PLUIE_VIGILANCE_MM:
        niveau, consigne = "vigilance", "Surveiller la portance des parkings en herbe"
        detail = "cumul sur l horizon"
    elif swi is not None and swi <= SWI_TRES_SEC:
        # Sol tres sec : la portance n'est plus le sujet, le feu l'est.
        niveau, consigne = "vigilance", "Sols tres secs — risque incendie"
        detail = "indice d humidite des sols"

    return {
        "cle": "sol", "libelle": "Sol",
        "valeur": round(cumul, 1) if cumul >= PLUIE_VIGILANCE_MM else (
            round(swi, 2) if swi is not None else None),
        "unite": "mm" if cumul >= PLUIE_VIGILANCE_MM else "SWI",
        "detail": detail, "niveau": niveau, "consigne": consigne,
        "pic": round(cumul, 1), "pic_heure": _premiere_heure(
            suite, lambda b: (b.get("pluie_mm") or 0) >= PLUIE_VIGILANCE_MM),
        "swi": swi,
    }


def _contraintes(actuel, suite, sol):
    suite = [b for b in suite if b]
    return [_contrainte_orage(actuel, suite), _contrainte_vent(actuel, suite),
            _contrainte_chaleur(actuel, suite), _contrainte_sol(suite, sol)]


def _fraicheur_mur(db, maintenant, actuel):
    """Sante des deux flux dont depend REELLEMENT le mur.

    Le bandeau montrait l'age de la mosaique radar. Elle n'alimente plus rien
    ici depuis que la carte en est partie : ce qui compte est la prevision
    immediate, d'ou sort le "pluie dans N minutes", et la prevision horaire,
    d'ou sortent la frise et les contraintes.

    PIAF se juge sur `run_at`, jamais sur `valid_at` : ses echeances sont dans
    le futur, un flux arrete continuerait donc a paraitre frais deux heures
    durant. Meme raison qu'en tete de /etat.

    Les previsions horaires ne portent aucun horodatage de collecte : on ne peut
    pas leur donner un age sans l'inventer. On constate donc leur PRESENCE --
    un creneau couvrant l'heure courante existe, ou il n'existe pas.
    """
    flux = []

    dernier = db["meteo_grilles"].find_one({"flux": "piaf"}, sort=[("run_at", -1)])
    if dernier and dernier.get("run_at"):
        age = round((maintenant - dernier["run_at"]).total_seconds() / 60.0, 1)
        flux.append({"cle": "piaf", "libelle": "prevision immediate",
                     "age_min": age, "seuil_min": 30, "ok": age <= 30})
    else:
        flux.append({"cle": "piaf", "libelle": "prevision immediate",
                     "age_min": None, "seuil_min": 30, "ok": False})

    flux.append({"cle": "previsions", "libelle": "prevision horaire",
                 "age_min": None, "seuil_min": None, "ok": actuel is not None})

    en_retard = [f for f in flux if not f["ok"]]
    return {"flux": flux, "en_retard": en_retard, "ok": not en_retard}


def _verdict(prochaine_pluie, consignes, contraintes):
    """La seule ligne que l'on lit depuis le fond de la salle.

    Ordre de priorite : ce qui impose une action maintenant, puis ce qui en
    imposera une, puis la pluie a courte echeance, puis le calme -- qui est une
    information a part entiere et doit s'ecrire, pas se deduire d'un ecran vide.
    """
    urgente = next((c for c in consignes if c["niveau"] == "critique"), None) \
        or next((c for c in consignes if c["niveau"] == "danger"), None)
    if urgente:
        return {"niveau": urgente["niveau"], "titre": urgente["texte"].split(" — ")[0].upper(),
                "detail": urgente["texte"], "echeance": urgente["heure"]}

    if prochaine_pluie and prochaine_pluie.get("attendue"):
        minutes = prochaine_pluie.get("dans_min")
        intensite = prochaine_pluie.get("intensite_mmh")
        titre = f"PLUIE DANS {minutes} MIN" if minutes is not None else "PLUIE ATTENDUE"
        detail = "prevision immediate PIAF"
        if intensite:
            detail = f"{intensite:.1f} mm/h attendus — {detail}"
        return {"niveau": "vigilance", "titre": titre, "detail": detail,
                "echeance": (prochaine_pluie.get("a") or "")[11:16]}

    vigilance = next((c for c in consignes if c["niveau"] == "vigilance"), None)
    if vigilance:
        return {"niveau": "vigilance", "titre": "POINT DE VIGILANCE",
                "detail": vigilance["texte"], "echeance": vigilance["heure"]}

    marquee = next((c for c in contraintes if c["niveau"] != "normal"), None)
    if marquee:
        return {"niveau": marquee["niveau"],
                "titre": marquee["libelle"].upper() + " A SURVEILLER",
                "detail": marquee["consigne"] or marquee["detail"],
                "echeance": marquee.get("pic_heure")}

    horizon = f"les {HORIZON_HEURES} prochaines heures"
    return {"niveau": "normal", "titre": "RIEN A SIGNALER",
            "detail": f"aucune contrainte meteo sur {horizon}", "echeance": None}
