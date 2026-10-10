# Detection : intrusions des cameras (fielddetection)

Page `/detection` (`detection.py`, `templates/detection.html`,
`static/js/detection.js`, `static/css/detection.css`). Acces : admin ou groupe
ayant la page "Detection". Les canaux visibles sont ceux qui ont l'usage
"Detection d'intrusion" dans la page Cameras (`docs/claude/cameras.md`).

## Chaine

1. La camera (ex. 10.34.1.171 canal 4, chemin `/portehoux`) pousse au
   recepteur `ecoutehik2.py` (service NSSM `HikRecepteur`, hors depot,
   `E:\TITAN\production\PCA\SCRIPTS\`) :
   - `fielddetection` (XML + photo `intrusionImage` ~760 Ko) : zone
     (`regionID`, sensibilite, contour en coordonnees 0-1000), cible
     `detectionTarget` (`human` / `vehicle`), cadre `TargetRect` (fractions
     de l'image, origine en haut a gauche), `targetID` (un par objet suivi) ;
   - `duration` toutes les ~5 s tant que la cible reste : seulement
     `relationEvent = fielddetection`, ni zone, ni cible, ni photo.
2. `intrusions.py` (a cote du recepteur) :
   - analyse complete -> `hik_intrusion_events` (TTL 365 j) ;
   - photo recompressee 1600 px q55 (~300 Ko) + vignette 480x360 decoupee
     autour de la cible dans l'ORIGINAL, sous
     `hik_images/intrusions/AAAA/MM/JJ/<chemin>/` (`_t/` = vignette) ;
     purge quotidienne > 365 j (thread du recepteur). Hors perimetre de la
     purge LAPI (dossiers annee numeriques a la racine de hik_images) ;
   - EPISODE `hik_intrusions` par (chemin, canal) : un message a moins de
     `HIK_INTRUSION_GAP_S` (30 s) du precedent prolonge l'episode. Une meme
     personne qui s'arrete ou passe derriere un obstacle change de targetID :
     le regroupement evite 1 alerte par targetID.
   - `status` : new -> seen -> closed (traitement operateur, note, auteur).
3. `cockpit_dispatch.py` cree les alertes Cockpit (centrale d'alertes) avec la
   photo (`snapshot_path`) et `actionData.target_type / region_id / channel /
   episode_id / zones`.

## Carte et zones

- UNE IP = UN LIEU : la position est celle de l'APPAREIL, partagee par tous
  ses canaux (multi-capteurs), et la carte n'affiche qu'UN pin par appareil
  (statistiques cumulees ; clic = filtre `<camera_id>:*`, tous les canaux).
  Par priorite (`_device_position`) :
  1. placee a la main dans la page (`cockpit_cameras.map {lat, lng}`,
     `PUT /api/detection/cameras/<camera_id>/position`, qui efface les
     anciennes positions par canal `channels[].map`, encore lues en repli) ;
  2. sinon la couche cartographique `titan.cameras` (UN document
     FeatureCollection, ~276 points, proprietes `IP`, `SEETEC NAME`,
     `NEW NAME`, `TYPE`) : rapprochement par IP, puis par nom de l'appareil
     ou d'un canal (`SEETEC NAME`), sinon le seul point de l'IP. Lue en
     direct (cache 60 s) : corriger la couche suffit.
  Seuls les appareils dont un canal a l'usage "fielddetection" sont places.
- Zones par IP : bouton "Dessiner une zone" sur chaque appareil du volet de
  configuration ; autant de polygones que voulu, chacun pre-rattache a tous
  les canaux de l'appareil (decocher ceux qui ne filment pas la zone). Ce
  sont des `detection_zones` ordinaires (filtres, alertes).
- `detection_zones` : `{name, color, polygon [[lat,lng]...], links:
  [{camera_id, channel, regions[]}]}`. Une intrusion appartient a une zone si
  son canal est lie (et, si `regions` est renseigne, si l'une de ses zones
  camera y figure). Le lien est logique : le polygone sert a l'affichage.

## Groupes et planning d'activation

- `detection_groups` : `{name, color, members [{camera_id, channel}], arming:
  {mode schedule|always|off, weeks [{id, name, grid: 7 chaines de 96 "0"/"1"
  (lundi d'abord, 1 caractere = 1 quart d'heure)}], default_week,
  exceptions [{id, label, kind dates|event, start, end, yearly, event, span
  window|public, action week|on|off, week_id}], holidays
  normal|sunday|on|off, override {state on|off, until, by} | null}}`.
  Regle unique dans `detection_arming.py` (depot cockpit), importee par la
  page ET par le recepteur (`intrusions.is_armed`) : ne jamais la dupliquer.
  Le recepteur doit etre redemarre (`Restart-Service HikRecepteur`) quand ce
  module change (pas quand un planning change : cache 20 s).
- PRIORITE : forcage > mode (toujours / pause) > exception (la premiere de la
  liste qui couvre le jour) > jour ferie (francais, calcule, Paques compris)
  > semaine type par defaut.
- Exception `event` : suit les dates d'une epreuve de `parametrages` via
  `event_courant.windows` (montage -> demontage, ou jours publics), chaque
  annee. Exception `dates` `yearly` : jour/mois, peut chevaucher le 1er janvier.
- Ancien format `slots [{days, start, end}]` : converti a la volee
  (`normalize`), puis remplace au premier enregistrement.
- Page : fenetre "groupe" (Configurer > Modifier) avec grille a peindre
  (rectangle au clic-glisser), semaines types multiples, exceptions
  ordonnables, calendrier mensuel calcule par le serveur
  (`POST /api/detection/groups/preview` sur le brouillon non enregistre).
- Un canal est ACTIVE si un de ses groupes l'est. Canal dans aucun groupe :
  toujours actif. Forcage temporaire 15 min a 72 h.
- HORS ACTIVATION le recepteur JETTE tout avant traitement : ni photo, ni
  episode, ni `hik_alerts`, ni console, ni alerte. Seul `detection_dropped`
  (par jour, chemin, canal ; TTL 90 j) compte les messages ignores (affiche
  dans la page pour prouver que la camera fonctionne).
- Cache de 20 s des groupes dans le recepteur : un changement de groupe ou de
  programme s'applique en moins de 20 s, sans redemarrage.

## Centrale d'alertes

- Type de regle `detection_intrusion` ("Detection - intrusion (groupes de
  cameras)") : `params.groups` (ids de `detection_groups`) et/ou
  `params.channels` (`"<camera_id>:<canal>"`), `target_types`, `zones`.
  UNE alerte par intrusion (dedup `det-<slug>-<episode_id>`), avec la photo.
  Pas de plage horaire dans la regle : l'activation du groupe en tient lieu.
- Regle "Camera Hikvision - Smart Event" (`camera_event`, historique) : par
  chemin d'appareil, avec `target_types` et `zones` en plus.
- Message : "Pieton detecte sur <camera> (zone <nom>)".
- Notification (`alert_poller.js`, reconnue par `actionData.episode_id`) :
  pastille Pieton / Vehicule, zones, premiere photo puis vignettes de toutes
  les photos de l'intrusion (`/api/detection/episodes/<id>`, relu a
  l'affichage et 10 s apres ; sans droit Detection : premiere photo seule),
  bouton "Ouvrir dans Detection" -> `/detection?episode=<id>` (ouvre la
  fiche). Apercu admin du type compris.
- WhatsApp (`whatsapp.py`, appele par le recepteur) : photo envoyee en
  image (sendImage) avec le texte en legende, plus le lien
  `<COCKPIT_PUBLIC_URL>/detection?episode=<id>` (`_detection_link`).
  Modifier `whatsapp.py` impose `Restart-Service HikRecepteur`.

PIEGE corrige le 09/10/2026 : le catalogue declare `fieldDetection` mais les
cameras envoient `fielddetection` ; la comparaison des types est desormais
insensible a la casse dans `cockpit_dispatch.dispatch_alert` (avant, une regle
"Intrusion de zone" ne pouvait jamais se declencher).

## Telephone (<= 820 px, point de rupture de la charte)

- Une vue a la fois via la barre d'onglets du bas : `body.dt-view-list |
  dt-view-map | dt-view-groups` (les autres vues en `visibility: hidden`).
  PIEGE : la fiche (`.dt-detail`) et la configuration sont rangees dans la
  vue Carte ; elles forcent `visibility: visible` sinon elles restent
  invisibles depuis la liste.
- Periode deplacee de l'en-tete vers les filtres (`placePeriod`), filtres
  replies derriere "Filtres (n)".
- Onglet Groupes : activer / desactiver d'un geste (forcage 1 a 24 h),
  "Revenir au programme" ; le programme horaire s'edite depuis un ordinateur.
- Fiche et visionneuse plein ecran (encoches via `env(safe-area-inset-*)`),
  glisser gauche / droite entre les photos, bulle de l'assistant masquee
  (`body.dt-sheet-open`).
- Configuration en panneau bas, carte au-dessus ; bouton "Placer a ma
  position" (GPS, contexte HTTPS requis).
- Badges : onglet Intrusions = a traiter, Carte = en cours ; titre de l'onglet
  du navigateur prefixe du compteur ; toast (+ vibration Android) a chaque
  nouvelle intrusion pendant que la page est ouverte.

## Diagnostic

Capture brute des messages d'un chemin : `PCA\SCRIPTS\raw_capture.json`
(`{"paths": ["/portehoux"], "until": "<ISO>", "max_per_path": 400}`), relu a
chaud, ecrit dans `PCA\SCRIPTS\logs\raw_capture\<chemin>\`.
