"""Outil Alfred cockpit_vent : vent mesure et prevu, alerte de rafale sur l'enceinte.

Memes calculs que la couche Vent de la carte (meteo_vent.py) : Alfred ne peut
pas annoncer un vent que la carte contredit. Trois usages dans un seul outil,
comme les autres outils fusionnes du manifeste :
  sans parametre          vent actuel au circuit (mesure station + modele)
  horizon_h / lieu        serie direction / vitesse / rafales au point
  seuil_rafale_kmh        premieres echeances ou la rafale atteint le seuil
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import meteo_vent

TZ_PARIS = ZoneInfo("Europe/Paris")
SERIE_MAX = 16


def _heure(iso):
    try:
        d = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return "?"
    d = d.astimezone(TZ_PARIS)
    aujourdhui = datetime.now(TZ_PARIS).date()
    jour = "" if d.date() == aujourdhui else ("demain " if (d.date() - aujourdhui).days == 1
                                              else d.strftime("%d/%m "))
    return jour + d.strftime("%Hh%M")


def _point(db, lieu):
    """(lat, lon, nom) du lieu cite, ou le centre du circuit."""
    if not lieu:
        return meteo_vent.LAT_CIRCUIT, meteo_vent.LON_CIRCUIT, None, None
    import alfred_portails
    lieux, candidats = alfred_portails.trouver_lieu(db, lieu)
    if not lieux:
        noms = ", ".join(c["nom"] for c in candidats[:5])
        return None, None, None, ("Lieu « %s » introuvable sur la carte%s." % (
            lieu, (" ; candidats : " + noms) if noms else ""))
    la, lo = alfred_portails.centre(lieux[0])
    return la, lo, lieux[0]["nom"], None


def _a(nom):
    return "au circuit" if not nom else "à " + nom


def _ligne(x):
    morceaux = []
    if x.get("vent_kmh") is not None:
        morceaux.append("%d km/h %s" % (x["vent_kmh"], x.get("direction") or ""))
    if x.get("rafale_kmh") is not None:
        morceaux.append("rafales %d km/h" % x["rafale_kmh"])
    return ", ".join(morceaux) or "valeur indisponible"


def t_vent(db, args, ctx):
    try:
        if args.get("seuil_rafale_kmh") not in (None, ""):
            seuil = float(args["seuil_rafale_kmh"])
            if not 10 <= seuil <= 250:
                return {"disponible": False, "resume": "Seuil de rafale hors bornes (10 à 250 km/h)."}
            a = meteo_vent.alerte_rafale(db, seuil)
            if a["depasse"]:
                p = a["premieres"][0]
                resume = ("Rafales ≥ %d km/h prévues sur l'enceinte à partir de %s (%d km/h, %s) ; "
                          "%d échéance(s) au-dessus du seuil" % (
                              seuil, _heure(p["valid_at"]), p["rafale_max_site_kmh"], p["source"],
                              a["nombre_echeances"]))
            else:
                resume = "Aucune rafale ≥ %d km/h prévue sur l'enceinte d'ici 42 h" % seuil
            if a.get("pic"):
                resume += " ; pic %d km/h à %s." % (a["pic"]["rafale_max_site_kmh"], _heure(a["pic"]["valid_at"]))
            return dict(a, disponible=True, resume=resume + " Source : Météo-France.")

        lat, lon, nom, erreur = _point(db, str(args.get("lieu") or "").strip())
        if erreur:
            return {"disponible": False, "resume": erreur}

        if args.get("horizon_h") not in (None, ""):
            s = meteo_vent.serie(db, lat, lon, args.get("horizon_h"))
            if not s["disponible"]:
                return {"disponible": False, "resume": "Prévision de vent indisponible : ne pas l'estimer."}
            lignes = s["serie"]
            # Au quart d'heure sur 6 h puis horaire : on garde au plus 16 lignes
            # en echantillonnant, sans jamais sauter le pic de rafale.
            pas = max(1, len(lignes) // SERIE_MAX + (1 if len(lignes) % SERIE_MAX else 0))
            pic = max(lignes, key=lambda x: x.get("rafale_kmh") or 0)
            retenues = [x for i, x in enumerate(lignes) if i % pas == 0 or x is pic]
            texte = ["Vent prévu %s :" % _a(nom)] +["%s : %s (%s)" % (_heure(x["valid_at"]), _ligne(x), x["source"])
                                                for x in retenues]
            texte.append("Pic de rafale : %s km/h à %s. Source : Météo-France." % (
                pic.get("rafale_kmh"), _heure(pic["valid_at"])))
            return {"disponible": True, "lieu": nom or "circuit", "serie": retenues, "resume": "\n".join(texte)}

        a = meteo_vent.actuel(db, lat, lon)
        if not a["disponible"]:
            return {"disponible": False, "resume": "Vent indisponible : ne pas l'estimer."}
        texte = []
        if a.get("mesure"):
            texte.append("Mesuré à la station Le Mans (%s) : %s." % (_heure(a["mesure"]["valid_at"]), _ligne(a["mesure"])))
        else:
            texte.append("Pas de mesure de station disponible.")
        if a.get("modele"):
            texte.append("Modèle %s %s (%s) : %s." % (a["modele"]["source"], _a(nom), _heure(a["modele"]["valid_at"]),
                                                     _ligne(a["modele"])))
        return dict(a, lieu=nom or "circuit", resume=" ".join(texte) + " Source : Météo-France.")
    except Exception:
        import logging
        logging.getLogger(__name__).exception("cockpit_vent")
        return {"disponible": False, "source": "vent", "note": "Donnee indisponible : ne pas l'estimer, le dire."}
