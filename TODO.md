# TODO — COCKPIT

## En cours

- [ ] Analyse Ops : tester sur tous les evenements (24H Autos, GPF, GP Explorer, Le Mans Classic, Camions, SAISON)
- [ ] Analyse Ops : verifier performance calcul SAISON (12 000 messages, objectif < 30s)
- [ ] Analyse Ops : enrichir le module comparatif avec le graphique superposition horaire N/N-1
- [ ] Analyse Ops : ameliorer le croisement effectifs calendrier avec ratio incidents/agent par zone

## A faire

### Analyse Ops
- [ ] Ajouter un export PDF/HTML du rapport complet pour diffusion post-evenement
- [ ] Module ANPR : activer quand les donnees Hikvision seront synchronisees en local
- [ ] Chrono : ajouter un mode play automatique sur la frise (avance dans le temps)
- [ ] Carte : ajouter le fond satellite ACO en option de calque
- [ ] Replay carte : ajouter un compteur d'incidents en temps reel pendant le replay

### Tableau de bord Live Control (NOUVEAU)
- [ ] Creer une page Live Control pour piloter en temps reel le controle d'acces et les cameras
- [ ] Integrer les flux Skidata (data_access) en temps reel : jauges par porte, courbe d'affluence live
- [ ] Integrer les cameras Hikvision : ANPR live (plaques detectees), comptage personnes/vehicules
- [ ] Afficher les alertes cameras (vibration, scene change) en temps reel sur une carte
- [ ] Tableau de bord des portes : statut ouvert/ferme, compteur entrees/sorties, capacite restante
- [ ] Historique controle : comparaison N/N-1 en direct pendant l'evenement
- [ ] Mode supervision : vue globale avec indicateurs critiques (seuils de capacite, alertes)

### Suivi GPS (Anoloc)
- [ ] Alertes geofencing : creer une alerte quand une balise entre ou sort d'une zone definie (polygone sur la carte)
- [ ] Admin : interface de creation de zones geofencing (dessin polygone + association balises/groupes)
- [ ] Notifications : afficher les alertes geofencing dans le fil d'alertes du cockpit

### Cockpit general
- [ ] Corriger les 2 labels map-prefs manquants generes par map_view.js (accessibilite)
- [ ] Responsive tablette pour la page Analyse Ops

## Fait

- [x] Page Analyse Ops : infrastructure complete (blueprint, template, CSS, JS)
- [x] 20 modules d'analyse avec cache MongoDB
- [x] Croisements meteo, affluence, Waze, effectifs, ANPR, zones vulnerables
- [x] Carte avec 5 calques commutables (carroyes, GPS, hotspots, chaleur, Waze)
- [x] Replay chronologique sur carte
- [x] Frise chronologique zoomable avec fiches alternees haut/bas multi-niveaux
- [x] Recherche full-text dans les fiches
- [x] Vue detaillee enrichie pour chaque widget (projection dans le bloc central)
- [x] Widgets repliables dans les panneaux lateraux
- [x] Graphiques plein ecran (modal)
- [x] Fix maxNativeZoom OSM/ArcGIS pour zoom au-dela de 19
- [x] Fix correlation meteo (accents clefs MongoDB, NaN JSON, valeur 0)
- [x] Fix affluence Skidata (type year int/str, current string)
- [x] Bouton Analyse Ops dans sidebar (admin only) sur index.html et edit.html
