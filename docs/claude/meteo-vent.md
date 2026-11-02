# Vent sur la carte d'accueil (meteo_vent.py, meteo_vent.js)

Couche Vent de la carte, sur la même carte que le radar de pluie, et outil Alfred. ⚠️ `meteo_vent.py` est **copié à l'identique dans Looker** (racine, « Radar météo » de la carte, `tests/v2/test_meteo_vent.py` vérifie la copie) : reporter toute modification dans les deux dépôts. Cockpit est **lecteur seul** : `meteo_vent` et `meteo_vent_obs` sont écrites par `E:\TITAN\tools\Meteo\collecte_vent.py`, via la tâche `TITAN Meteo` toutes les 15 min. Voir le README de la brique pour les sources et les pièges de l'API.

## Ligne du temps (`meteo_vent.trames`)

- **+15 min à +6 h, pas de 15 min, AROME-PI.** Les rafales viennent d'AROME-PI (composantes de la rafale quart-horaire). AROME-PI **ne sert pas de vent moyen** : celui de ces trames est AROME, interpolé linéairement entre ses deux heures encadrantes (`moyen: "AROME interpole"`).
- **Au-delà, et jusqu'à +42 h, pas horaire, AROME 0,01°** : vent moyen et rafales.
- Une trame passée de plus de 15 min est écartée. Identifiant : `pi|AAAAMMJJTHHMM|échéance` ou `ar|...`.
- Tout est en m/s en base. Les API rendent m/s et km/h ; la carte convertit en km/h ou en nœuds (choix mémorisé dans `localStorage`, clé `cockpit_vent_unite`).
- Direction météo (d'où vient le vent) : `(270 - atan2(V, U)) mod 360`. Les flèches de la carte montrent où **va** le vent, comme Windy.

## API (`/api/meteo/vent/*`, manager)

| Route | Rôle |
|---|---|
| `GET /sequence` | trames sans grilles, runs, mesure de station, points clés |
| `GET /trame?id=` | grilles `u`/`v` (vent moyen), `rafale`, `u_rafale`/`v_rafale`, en-tête façon grib2json (`lo1`, `la1`, `dx`, `dy`, lignes nord → sud). Mise en cache 1 h par le navigateur : une trame d'un run ne change jamais |
| `GET /point?lat&lon&horizon_h` | valeur actuelle (mesure + modèle + écart) et série au point |
| `GET /alerte?seuil_kmh` | premières échéances où la rafale max **sur l'enceinte** (`BBOX_SITE` du collecteur) atteint le seuil, et le pic |

`/api/meteo/etat` suit aussi `vent_aromepi` (seuil de 2 h sur `run_at`) et `vent_arome` (seuil de 8 h).

## Carte (`static/js/meteo_vent.js`)

- **Un seul bouton « Météo »** sur la carte (icône `partly_cloudy_day`, posé par `meteo_avance.js`, délégué à `CockpitMeteoVent.bouton()`). Si une couche est affichée, il éteint tout ; sinon il rouvre le dernier mode utilisé (`localStorage` `cockpit_meteo_mode`, Pluie par défaut). Bascule **Pluie / Vent / Rafales / Nuages / Pluie + Vent**, dans la barre du vent et greffée dans celle du radar.
- **Fond coloré masqué au zoom ≥ 18** (`FOND_ZOOM_MAX`) en Vent et Rafales : il nuisait à la lecture de la carte de l'enceinte. Les particules et les flèches restent.
- **Nuages** : couverture nuageuse totale AROME (`TOTAL_CLOUD_COVER`, %, champ `nuages` des documents AROME, interpolé sur les trames AROME-PI). Voile gris-bleu dont l'opacité suit la couverture (rien sous 5 %), sans particules ni flèches. Un run collecté avant l'ajout n'a pas de nuages : la barre affiche « Couverture nuageuse indisponible pour cette heure ». AROME-PI ne sert que les nuages bas, non utilisés pour garder une seule définition.
- **Vent :**
  - fond coloré par vitesse (palette type Windy, opacité 0,6, interpolation bilinéaire) ;
  - particules sur un canvas dans le pane `ventParticules` (z 455) ;
  - flèches sur une grille de 110 px (pane `ventFleches`) ;
  - flèches renforcées avec une étiquette « moyen / rafale » sur les points clés.
- **Rafales :** fond coloré selon la rafale ; particules et flèches suivent le vecteur de rafale quand il existe (AROME-PI).
- **Pluie + Vent :**
  - pas de fond coloré, il masquerait la pluie ;
  - **le curseur est celui du radar**, et le vent prend la trame la plus proche de l'image affichée ;
  - `meteo_avance.js` émet `cockpit:radar-image` et `cockpit:radar-etat`, et expose `window.CockpitMeteoRadar`.
  - Rallumer le radar par son bouton pendant que le vent est affiché fait basculer en Pluie + Vent.
- **Points clés :** retrouvés par leur nom dans les couches `tribunes` puis `hospitalites`. Par défaut : Stands, Tertre Rouge, Maison Blanche, Indianapolis. On peut les surcharger par `parametrages` `__GLOBAL__` `data.meteo.points_vent`, une liste de noms ou de `{nom, lat, lon}`.

## Alfred

Le vent passe par `cockpit_meteo`. Il n'y a pas de dixième outil : le manifeste reste à 9 outils visibles (`test_manifeste_neuf_outils`).

| Appel | Résultat |
|---|---|
| sans paramètre | le mur météo, inchangé |
| `sujet="vent"` | le vent actuel au circuit |
| `horizon_h` | la série |
| `lieu` | un lieu de la carte, résolu par `alfred_portails.trouver_lieu` |
| `seuil_rafale_kmh` | l'alerte |

Le code est dans `alfred_vent.py`. Les calculs sont ceux de `meteo_vent.py`.

## Pièges

- **Mesure de station absente** tant que l'API « Données d'observation » n'est pas souscrite (HTTP 403). La carte affiche « Mesure de station indisponible », Alfred le dit.
- **Pas de grille du vent dans `meteo_grilles`.** `sequence_radar` y prend la bbox d'un document quelconque, et une autre emprise décalerait le radar.
