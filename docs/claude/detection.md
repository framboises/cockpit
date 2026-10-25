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
  fiche). Apercu admin du type compris (derniere intrusion reelle). Cadre
  rouge sur la cible (`rect` de la photo). Photo relue dans
  `cockpit_alert_history` (`alert_id`) quand l'alerte active a expire (10 min),
  sinon reprise de l'episode.
- WhatsApp (`whatsapp.py`, appele par le recepteur) : photo envoyee en
  image (sendImage) avec le texte en legende, plus le lien
  `<COCKPIT_PUBLIC_URL>/detection?episode=<id>` (`_detection_link`).
  Modifier `whatsapp.py` impose `Restart-Service HikRecepteur`.

PIEGE corrige le 09/10/2026 : le catalogue declare `fieldDetection` mais les
cameras envoient `fielddetection` ; la comparaison des types est desormais
insensible a la casse dans `cockpit_dispatch.dispatch_alert` (avant, une regle
"Intrusion de zone" ne pouvait jamais se declencher).

## Verification IA locale (filtre des fausses intrusions)

Les cameras classent "pieton" des oiseaux, panneaux, reflets, branches (cibles
de 20 px). Avant l'alerte, chaque photo d'intrusion est verifiee sur le GPU
du serveur (NVIDIA L4), sans service en ligne ni cout.

- Service NSSM `IntrusionVerifier` (`scripts/intrusion_verifier.py`, venv
  `titan_prod` partage avec Tagger, 127.0.0.1:4011, log
  `PCA\SCRIPTS\logs\intrusion_verifier.log`). YOLO11m COCO de Tagger
  (`tagger\models\analytics\ground\yolo11m.pt`) charge UNE fois et garde en
  memoire ; zoom x4 autour du cadre de la cible (160 px min.) ; 20-75 ms.
  YOLO DECIDE (personne / vehicule, cadres `box` normalises sur la photo
  entiere). SigLIP2 so400m de Tagger (`photo_embedder_siglip`, textes
  encodes une fois) EXPLIQUE seulement (`guess` : oiseau, panneau, branche,
  reflet, sol, grillage...) : il voit parfois "grillage" sur une personne
  derriere une cloture, il ne doit jamais decider. YOLO-World teste et
  ecarte (corneille -> personne 21 %). ~350 ms au total.
  `GET /health`, `POST /verify {photo, rect}`.
- CORRIGE le 14/10/2026 : sur 284 intrusions filtrees, ~220 etaient de vraies
  personnes / vehicules (cibles de 15-40 px sur des photos 1600x900). Cause :
  un carre de 160 px agrandi a 640 devient flou, YOLO ne voit plus rien.
  Desormais DEUX cadrages (`FRAMINGS` : zoom x8 / 320 px min. a imgsz 640, et
  zoom x4 / 160 px a imgsz 320, sans agrandir), detections fusionnees
  (`_dedupe`). Et si SigLIP voit personne + vehicule >= 0,6 alors que YOLO en
  devine un >= 0,1, `real_conf` = score SigLIP (`by: "detecteur+avis"`,
  `yolo_conf` = score YOLO brut) : vehicules eblouis par leurs phares. Mesure :
  186 vraies cibles recuperees, 0 des 29 fausses alertes validees par
  l'operateur reclassee. Tout est dans le service : le recepteur n'a pas
  change (il compare toujours `real_conf` a `min_conf`). Le recepteur ne
  recopie pas `by` dans `ai` (seul le script de reverification l'ecrit).
- Recepteur : `intrusions.verify_photo` apres `record_intrusion`, appele par
  `ecoutehik2.py`. Personne / vehicule / velo >= `min_conf` -> alerte ; sinon
  `ai_filtered` : ni alerte ni WhatsApp, l'intrusion et ses photos restent.
  Service muet, photo absente ou filtre coupe : l'alerte PART (jamais
  d'intrusion ratee a cause du filtre). Une photo confirmee suffit pour
  l'episode (verdict `confirmed` > `unverified` > `filtered`, jamais ecrase).
- Annotation operateur (verite terrain) : bouton "Indiquer personne /
  vehicule" sous le zoom (et "Indiquer ou est..." quand l'avis est Erreur) ->
  editeur plein ecran (souris / doigt, plusieurs cadres, loupe sur la cible,
  "Rien sur cette photo" = liste vide). `POST
  /api/detection/episodes/<id>/photos/<idx>/label {photo (url, controle
  409), boxes: [{x,y,w,h,category: personne|vehicule}]}` -> `photos.<idx>.label
  {boxes, by, at}`. Sur une intrusion filtree sans avis, poser une personne /
  un vehicule pose l'avis "down" (`auto: "annotation"`). Cadres "verifie :
  ..." en trait plein epais sur la photo et le zoom.
- Hors planning (depuis le 10/10/2026) : les intrusions ne sont PLUS jetees.
  Photo, episode et verification IA comme les autres, `armed: false` sur
  l'episode (absent = dans le planning ; passe a true si le planning demarre
  pendant l'intrusion), JAMAIS d'alerte. `detection_dropped` compte toujours
  ces messages par jour. Page : filtre `planning` ("" = planning seul, defaut ;
  `off` ; `all`), badge "Hors planning". But : les traiter et entrainer le
  modele. Volume a surveiller (photos 1 an, ~250 Ko).
- Reglage : `cockpit_settings` `_id: "detection_ai_filter"` `{enabled
  (defaut true), min_conf (defaut 0.3)}`, relu toutes les 60 s sans
  redemarrage.
- Episode : `ai {status, label, conf, real_conf, min_conf, photo, guess, ms,
  model, at}` (label = avis SigLIP en tete si filtree), `ai_checks`, par photo
  `photos.$.ai {decision, real_conf, detections, guess}` (cadres "IA : oiseau
  73 %" en pointilles sur la photo et le zoom, a cote du cadre "camera :
  personne"), avis operateur `ai_vote {vote: up|down, truth?, note?, by, at}`
  (`POST /api/detection/episodes/<id>/ai-vote`).
- Avis = PILULES CLIQUABLES (depuis le 14/10/2026, demande de l'utilisateur) :
  "Qu'est-ce que la cible signalee (cadre orange) ?" ; les propositions de
  SigLIP avec leur pourcentage puis les autres categories (`AI_TRUTHS` :
  personne, vehicule, velo, animal, oiseau, panneau, branche, reflet, sol,
  grillage, rien). `{truth, note?}` : le pouce `vote` est DEDUIT (up si
  "vraie intrusion" = "alerte partie"), commentaire facultatif (300 car.).
  `{truth: null}` / `{vote: null}` retire ; `{vote}` seul reste accepte
  (anciens avis, "Oui a tout" de la galerie). Galerie : 3 propositions +
  personne / vehicule + "Autre..." (ouvre la fiche). Seul le cadre orange
  compte : les vehicules gares vus par l'IA autour ne sont que du decor.
- "A verifier" (badge orange, `aiDoubt`) : filtree alors que SigLIP voit une
  personne / un vehicule >= 0,5. Jamais "fausse alerte probable" dans ce cas.
- ECARTEE PAR L'IA (25/10/2026, `detection.portee`) : filtree sans doute
  (oiseau, grillage, reflet...) ou avis operateur "down" = `ecartee` : JAMAIS
  "en cours", "a traiter", pastille de la carte ni zone rouge (avant, elles
  restaient `new` et allumaient les cameras alors qu'aucune alerte n'etait
  partie). Filtree mais SigLIP voit personne / vehicule >= 0,5 = `doute` : a
  traiter, jamais en cours. Avis "up" = `alerte`. `overview.totals.ecartees` /
  `doutes` (KPI "ecartees par l'IA", "dont N a verifier"). Elles restent
  listees (filtre IA) pour l'entrainement. Meme regle sur le mur TV.
- Hors planning (`armed: false`) : JAMAIS compte "en cours" ni "a traiter"
  (`overview` : totals, by_channel, by_zone ; badges des cartes). Une
  intrusion au sens propre n'existe que pendant le planning.
- Page : badge "Filtree IA : <label>" / "A verifier" / "IA : <label>" /
  "Non verifiee", filtre `ai=filtered|alerted|voted`, bloc "Verification IA"
  avec pilules dans la fiche. Les avis (photo + cadre + verdict + avis) servent a regler le seuil
  et a constituer un jeu d'entrainement ; aucun reentrainement automatique.
- UNE SEULE reponse de l'IA a l'ecran, SUR LA CIBLE DU CADRE ORANGE
  (16/10/2026) : `detection._ai_answer(photo_ai, rect)` publie `answer
  {label, p, src, k?}` par photo et `ai.answer` + `ai.photo` (photo qui a
  decide) pour l'episode et la galerie (dont la carte montre CETTE photo).
  QUI EST QUI (regle unique, 16/10/2026 soir) : cadre orange = alerte de la
  camera = ce que l'operateur classe ("c'est quoi ?"). Le cadre du detecteur
  qui EST cette cible = `_target_det` : meilleur IoU avec le cadre orange
  (candidat si IoU >= 0.1 ou centre dans le cadre elargi de 25 %), JAMAIS la
  meilleure confiance (sinon un grand vehicule qui deborde sur un petit
  cadre de personne gagnait : 137 08:44:42, IA 1 vehicule 47 % contre IA 2
  personne 35 % dedans). La reponse affichee = classe et confiance de CE
  cadre (k), sans substitution "detecteur+avis" (elle donnait "vehicule" sur
  un cadre "personne") ; sans cadre coincident : 1er de l'avis SigLIP
  (calcule sur le cadrage du cadre orange), sans k. Le front dessine
  toujours le cadre k (meme < 20 %), cercle sa pastille d'orange et la
  question le nomme ("= cadre IA n"). Le detecteur voit TOUTE la photo : avant, une voiture garee a cote
  (137 du 16/10 12:07, personne dans le cadre) donnait "vehicule 77 %". Seule
  la pilule de la reponse porte un pourcentage "(IA N %)" ; detail en
  infobulle (`aiDetail`). Mesure sur 4455 confirmees : 346 sans personne /
  vehicule du detecteur dans le cadre, dont 306 ou l'avis y voit bien une
  cible (> 60 %, ex. vehicule ebloui par ses phares), 27 douteuses, 13
  probablement confirmees a tort par un objet hors cadre (025, 901). La
  DECISION du verificateur n'est pas changee (tout le cadrage compte).
- Retour PAR CADRE DU DETECTEUR (pointilles, 16/10/2026) : sous les pilules
  de la photo, une ligne par cadre IA (numerote "IA 1", "IA 2"... comme sur
  la photo ; survol = le cadre ressort, clic sur l'etiquette = sa ligne) :
  Juste / Faux / "C'est une personne|un vehicule".
  `POST /api/detection/episodes/<id>/photos/<idx>/detections/<k>` {photo,
  verdict: juste|faux|personne|vehicule|null} -> `photos.N.det_fb.<k>`
  {verdict, ai_category, ai_class, conf, box, by, at} (k = index dans
  `ai.detections`, copie du cadre au cas ou). Reclasser vers sa propre
  classe = "juste". Le cadre qui EST la cible de la camera (`answer.k`,
  choisi par `_ai_answer`) n'a PAS de ligne : son retour est deduit de la
  reponse "c'est quoi ?" de la photo (`set_photo_truth`, `auto: "photo"` :
  meme classe = juste, autre personne/vehicule = reclasse, velo = personne,
  le reste = faux ; retire avec la reponse ; un retour manuel n'est jamais
  ecrase). Bloc masque s'il n'y a pas d'autre cadre. DISTINCT de `label` (cadres dessines = verite complete
  de la photo) : un retour par cadre ne dit rien des cibles ratees.
  Destination : reentrainement du detecteur YOLO (niveau 3, PAS encore
  automatise, aucun script ne lit det_fb aujourd'hui). La case du zoom garde son 4:3 (`align-self: flex-start`) :
  etiree par la colonne d'a cote, le recadrage CSS tombait a cote de la cible.
- Reponse PHOTO PAR PHOTO (15/10/2026) : une photo = une cible de la camera
  (verifie : 6467 photos pour 6425 targetID sur 3 jours), et un episode peut
  en cumuler 30. `photos.N.truth {truth, note, vote?, by, at}` via
  `POST /api/detection/episodes/<id>/photos/<idx>/truth` ({photo} controle,
  409 si la liste a change) ; pilules sous le zoom de la galerie, passage
  auto a la photo suivante sans reponse, coche verte sur les vignettes.
  Lien avec l'avis d'episode : la photo qui a decide (`ai.photo`) alimente
  `ai_vote` (auto "photo") tant que l'operateur n'a pas repondu sur
  l'episode ; l'inverse remplit la photo si elle n'a pas de reponse.
- INTRUSIONS SANS PHOTO (15/10/2026) : certaines cameras envoient
  `pictures: 0` (131 Porte Est : 12/12 episodes sur 7 jours ; envoi de
  l'image non coche cote camera, on n'y touche pas). `detection_snapshot.py`
  (thread de Cockpit, lance avec les workers dans app.py) : episode cree il
  y a moins de 2 min sans photo -> reserve `snap {state}` (pending / ok /
  skipped / error) puis GET ISAPI picture du canal (lecture seule, jamais
  de piste, marque pilotable + profil requis) ; rangee comme le recepteur,
  `photos[].snapshot: true` + `snap_delay_s`, cadre = celui du 1er message.
  Dans Cockpit et pas dans le recepteur : seul le compte de Cockpit a
  COCKPIT_CAMERA_KEY (le recepteur tourne en SYSTEM). Exclues de
  l'entrainement (la cible a pu bouger).
- ENTRAINEMENT DE NUIT (15/10/2026) : `scripts/intrusion_training.py`, tache
  "Cockpit - Entrainement IA intrusions" 03:30 (`intrusion_training.bat`).
  Exemples = une photo : reponse photo > cadres poses (vide = rien) > avis
  d'intrusion sur `ai.photo` (sans `truth`, vrai/faux deduit du pouce).
  Niveau 2 (des 150 reponses precises, 3 categories >= 10, 30 nouvelles) :
  couche lineaire sur les empreintes SigLIP (`POST /embed` du verificateur,
  meme cadrage serre que l'avis), partant des phrases (rappel L2). Validation
  croisee 5 x 80/20 ; installee si +3 points, pas plus d'intrusions ratees,
  personne / vehicule jamais -5 points. `head.json` dans
  `tagger\models\analytics\intrusion_head\`, relu A CHAUD par le
  verificateur (mtime / 30 s), ancien en `head.prev.json`, `--rollback`.
  ATTENTION : l'avis entre dans la decision (regle detecteur+avis).
  Niveau 1 (des 80 reponses dont 15 vraies et 15 fausses, 20 nouvelles) :
  seuil `detection_ai_filter.min_conf` minimisant 5 x ratees + fausses, borne
  0,15-0,60, 0,10 max par nuit (`min_conf_prev` garde). Etat et historique :
  `cockpit_settings` `detection_ai_training` (`seen_truth` / `seen_total`).
  Rapport mail (SMTP 192.168.254.2, safe@lemans.org, `INTRUSION_TRAINING_MAIL_TO`)
  a chaque analyse, point d'avancement le lundi sinon. `--dry-run`, `--force`.
  PIEGE : la tache en S4U echoue (LogonUserS4U, compte de domaine) : compte
  avec mot de passe (comme Capture IA) ou SYSTEM.
- Le recepteur ne verifie plus apres la premiere photo confirmee : la fiche
  fait analyser a l'affichage chaque photo sans `ai` (`POST .../photos/<idx>/verify`,
  ~200 ms, `ai.on_demand: true`). Ne change NI le verdict d'episode NI les alertes.
- Teste le 10/10/2026 sur 13 photos : personnes 0,82-0,88, voiture 0,74 ;
  oiseaux, panneau, reflet, branche -> rien ou oiseau. La corneille posee sur
  un panneau sort en "frisbee" 0,47 (label "objet") : filtree.
- Complement cote camera : taille minimale de cible par zone,
  `/ISAPI/Smart/channels/<canal>/calibrations/fielddetection` (FilterSize
  MinTargetSize / MaxTargetSize, normalise 0-1000), dessinable dans
  l'interface web ; a 0 par defaut (aucun filtre).

## Page IA : modes Carte / Intrusions / Trafic / Foule / Secours / Galerie (depuis le 10/10/2026)

La page `/detection` est devenue la page "IA" (menu "IA", icone
`psychology`). Selecteur de mode dans l'en-tete (`ia_capture.js`) : Carte |
Intrusions | Trafic | Foule | Galerie (Trafic et Foule SEPARES : memes
donnees `GET /api/detection/capture/cameras`, filtrees par `purpose`).
OUVERTURE SUR LA CARTE GLOBALE (`?mode=`
pour un lien direct ; `?episode=` reste en mode Intrusions). Le mode
Intrusions est l'ecran historique (detection.js). Sa carte, creee cachee,
se recadre quand elle redevient visible (`needFit`, sinon vue de la Terre).

Carte globale : `GET /api/detection/global-map` = toutes les cameras placees
(`_device_position`), usages (intrusion, trafic, LAPI), etat du trafic,
intrusions du jour (dans le planning : total, non filtrees, a traiter).
Pins : logo pour TOUTES les cameras (videocam gris, intrusion orange,
trafic = couleur de l'etat, LAPI bleu, piste sombre avec cadenas), pastille
rouge = intrusions a traiter ; filtres Toutes / Surveillees par l'IA /
Intrusion / Trafic. Clic = VOLET DE DETAIL a droite (`GET
/api/detection/global-map/<id>`) : usages, marque / modele (IP pour les
admins), trafic de chaque canal (derniere paire, zones), liens LAPI /
Cameras. Refait le 14/10/2026 :
- Image : un onglet par canal (pastille rouge = intrusions du jour dans le
  planning), la PLUS RECENTE entre capture de la page Cameras
  (`/api/pcorg/cameras/<id>/snapshot?channel=N`), derniere photo
  d'intrusion et derniere photo Capture IA (`channels[].image {url, at,
  source}`) ; clic = ouvrir en grand.
- Intrusions du jour : 3 compteurs cliquables "dans le planning", "a
  traiter" (planning seul), "hors planning" (`channels[].intrusions {armed,
  open, offplan}`) qui ouvrent la liste filtree (camera + planning,
  `IADetection.filterCamera(key, planning)`) ; vignettes des 8 dernieres
  dans le planning (`episodes`) / hors planning (`episodes_off`).
- Canaux et usages ("Analyser le trafic"...) dans une section repliee.
- PIEGE : un canal qui envoie des intrusions SANS usage "Detection
  d'intrusion" dans la page Cameras (901 canal 1 au 14/10/2026) ne peut pas
  etre mis dans un groupe de planning, donc il est arme 24 h/24
  (`detection_arming.channel_armed` : hors de tout groupe = actif). Idem
  d'un chemin camera inconnu de Cockpit (/camera-restaurant, `camera_id`
  absent).
- Plusieurs photos par intrusion : un episode regroupe TOUTES les detections
  du canal tant qu'il n'y a pas 30 s de silence (`HIK_INTRUSION_GAP_S`), 30
  photos max., une par cible (`target_id`) : un VL puis un pieton = un seul
  episode. Une fois l'episode confirme, les photos suivantes ne sont plus
  verifiees par l'IA.

Zones : dessiner, modifier ou supprimer une zone RECALCULE l'historique de
la camera (14 jours, `capture_ia.recompute_zones`, vehicules gardes) ;
avant, l'historique restait calcule sur l'image entiere (Technoparc rouge).

Intrusions : "Cloturer" ferme le volet de la fiche. detection.js expose
`window.IADetection` (config, openGroup = editeur de planning, openEpisode).

### Capture IA (trafic, bouchons)
- Usage de canal `capture`, libelle "Analyse du trafic"
  (`cameras.ROLE_TYPES`) : `interval_min` (defaut 3), `gap_s` (intervalle
  entre les 2 paires, defaut 8 ; mis a 8 sur Antares et Technoparc),
  `preset` (facultatif : rappele a CHAQUE mesure, les operateurs voient la
  camera bouger), `purpose` (traffic). Regle depuis le mode Trafic (ou la page
  Cameras). JAMAIS de reglage de la camera (resolution, flux) : photos
  pleine taille par l'interface web.
- Capture : `capture_ia.py`, tache planifiee "Cockpit - Capture IA" TOUTES
  LES MINUTES sous le compte de Cockpit (`scripts/capture_ia.bat`,
  `scripts/install_capture_ia_task.ps1`, mot de passe Windows demande),
  JAMAIS dans Cockpit. Verrou `logs/capture_ia.lock`, journal
  `logs/capture_ia-AAAAMMJJ.log`. Ne prend que les canaux dus
  (`ia_capture_state.last_try`) et dans leur planning (`channel_armed` :
  memes groupes que les intrusions ; sans groupe = 24 h/24). 8 cameras en
  parallele, reveil 4G par `call_camera`. Jamais les cameras de piste.
  `--force` (toutes), `--dry-run` (n'ecrit pas en base).
- Mesure DEPUIS LE 10/10/2026 (`method: "flux"`) : DEUX PAIRES, images a
  0 / 1 s puis `gap_s` / `gap_s` + 1 s (`grab_frames`). Flux RTSP principal
  (OpenCV FFMPEG, TCP, `grab()` puis `retrieve()` des seules images
  retenues, horodatage du flux = ecarts exacts) si le port 554 repond, sinon
  photos enchainees (`source: "photos"`, Antares : ~2 s par photo, ecarts
  reels ~0/2/8/10 s). URL avec mot de passe : JAMAIS journalisee. Technoparc
  accepte le flux pendant qu'un operateur le regarde (verifie le 10/10/2026
  15:49) ; un seul "453 Not Enough Bandwidth" vu, quand deux connexions de
  Cockpit ont demarre a la meme seconde (essai + tache) -> repli photos
  (ne pas tester pendant la tache). Detection des 4 images (`/detect`).
  `classify_pair` : vitesse de chaque vehicule de la 1re image d'une paire
  en LONGUEURS DE VEHICULE PAR SECONDE (`_track` : meme place couleur >= 0,6,
  ou voisin couleur >= 0,85, a moins de 3 longueurs/s), verifiee sur l'image
  de l'autre paire : < 0,15 (`STOP_V`) et toujours la quasi immobile =
  `arrete`, a peu avance = `lent` (file au pas), reparti = `bref` (cede le
  passage, compte comme ROULANT) ; < 0,3 (`SLOW_V`) = `lent` ; sinon ou pas
  retrouve = `roule`. MEME PLACE = meme image : en plus de la couleur, la
  correlation des pixels (`_ncc`, gris 32x32, max du meme rectangle et de
  chaque cadre) doit atteindre 0,8 (`STILL_NCC`) ; sinon une voiture qui
  roule remplacee par une autre au meme endroit passait pour immobile
  (Antares 16:31, voiture aux velos -> C3 grise : "arret bref"). Garees
  0,88-0,99, faux immobiles -0,26-0,70. Vitesse = deplacement du centre.
  `vehicles_a` = 1re paire, `vehicles_b` = 2e paire (verifiee 8 s plus
  tot), `vehicles_a2` / `vehicles_b2` = 2e image de chaque paire (statut du
  jumeau, `autre` si relie a aucun), `speed`, `pair_s`, `source`, 4 `photos`
  (`HHMMSS_a..d.jpg`). Essai sur 22 mesures par camera : voiture qui circule
  0,7-2, garee 0,00 ; seuil lent a 0,6 = faux "ralenti" (voitures au loin
  qui arrivent de face, cedez-le-passage). Aucun bouchon reel encore vu :
  detection du bouchon a valider au premier embouteillage.
- Par zone (`ia_capture_zones`, polygones 0-1 dessines sur la photo ; sans
  zone : image entiere) : etat sur les vehicules DES DEUX PAIRES ; moins de
  3 vehicules par image en moyenne = fluide ; >= 60 % arretes = bouchon ;
  >= 60 % arretes + lents = ralenti. Comptes affiches : 1re paire. Etat
  camera = pire zone.
- Mesures AVANT le 10/10/2026 (`method` absent) : photo A, photo B `gap_s`
  plus tard ; chaque vehicule de A `arrete` / `deplace` / `parti`, de B
  `suivi` / `arrive` (`match_vehicles`, garde pour ces mesures).
- Zones de STATIONNEMENT INTERDIT (`ia_capture_zones.kind =
  "stationnement"`, `max_min` 1-1440, rouge ; les zones "trafic" sont
  `kind` absent ou "trafic") : hors calcul de l'etat du trafic.
  `capture_ia.parking_metrics` : vehicules ARRETES (meme cadre sur A et B)
  dans la zone, puis remontee des mesures precedentes (6 h max, IoU >= 0,5)
  tant que le vehicule est a la meme place ; un trou > 3 rythmes (hors
  planning, panne) arrete la remontee. Precision = rythme des mesures. Un
  vehicule deja la au debut de l'historique compte depuis la 1re mesure.
  Alerte : regle `parking_camera` ("Stationnement interdit (analyse
  camera)", `params.channels`) -> `dispatch_parking`, UNE alerte par
  vehicule stationne (zone + heure d'arrivee, `ia_capture_state.
  parking_alerted`). Teste le 10/10/2026 hors ligne sur Technoparc (voitures
  du trottoir : 11 min).
- Classement A/B : `arrete` si IoU >= 0,5, ou si le cadre voisin est a moins
  d'un tiers de la taille du vehicule (le cadre d'un petit vehicule gare
  "tremble" : avant, 4 voitures garees sortaient "deplacees").
- RESSEMBLANCE EXIGEE (`match_vehicles(va, vb, ims)`, histogramme couleur
  4x4x4 du coeur du cadre, intersection) : meme place >= 0,6
  (`SAME_SPOT_COLOR`), deplace >= 0,85 (`MOVED_COLOR`). Sans elle, la seule
  position reliait deux voitures DIFFERENTES (Antares 10/10/2026 : rouge ->
  grise, blanche -> noire ; 111 faux "deplace" et 21 faux "arrete", etat
  "ralenti" invente). Mesure : vraies voitures garees 0,76-0,97, voitures
  differentes 0,13-0,80 (deux noires 0,78). Historique reapparie le
  10/10/2026. Photo B : gris = MEME vehicule que sur A, bleu = arrive.
- Stockage : `ia_capture_samples` (photos `hik_images/captures/AAAA/MM/JJ/
  <id>_ch<n>/HHMMSS_a..d.jpg` en 1920 px q80 + `_t/` vignettes), purge
  apres `CAPTURE_RETENTION_DAYS` (30) SAUF les mesures avec avis (`vote`) ou
  annotation (`label`) : jeu d'entrainement. Etat : `ia_capture_state`
  (`state`, `streak`, `last_error`, `planning`).
- INTERRUPTEUR GENERAL (10/10/2026) : bouton "Captures en marche / a
  l'arret" dans les barres Trafic et Foule (`PUT
  /api/detection/capture/switch {purpose: traffic|crowd, on}`, document
  `ia_capture_state._switch`, renvoye dans `capture/cameras` -> `switch`).
  A l'arret : `capture_ia.capture_enabled` saute tous les canaux de la
  fonction (meme `--force`), donc ni photo, ni mesure, ni alerte ;
  "Mesurer maintenant" refuse (409). Absent = en marche. La purge et les
  statistiques continuent. 4G (11/10/2026) : `_switch.<fonction>.cell`
  (absent = incluses) ; faux = seules les cameras filaires sont mesurees
  (`cameras.is_cellular` : IP publique), data 4G preservee ; bouton "4G
  incluses / exclues" a cote de l'interrupteur (cache sans camera 4G dans
  la fonction), bandeau orange, puce "4G en pause" ; "Mesurer maintenant"
  refuse sur une 4G exclue. PUT `switch {purpose, on?, cell?}` ecrit en
  `$set` pointe (un champ n'efface pas l'autre).
- QUATRE NIVEAUX (10/10/2026) : fluide / DENSE / ralenti / bouchon
  (`STATE_ORDER` 0-3). Mouvement (`state_motion`) : arretes >= 60 % =
  bouchon, arretes + lents >= 60 % = ralenti, >= 30 % ET >= 6 vehicules par
  image (`DENSE_MIN_VEH` ; sinon 1 lent sur 3 faisait "dense") = dense.
  NOMBRE DE VEHICULES APPRIS PAR ZONE / TRACE : avis de l'operateur sur
  chaque zone d'une mesure (boutons "Vrai niveau" sous la zone, mode
  Trafic ; `POST /api/detection/capture/samples/<id>/zone-vote`, garde dans
  `ia_capture_samples.zone_votes.<zone>` avec `veh` = vehicules par image,
  `state_motion`, `waze_ratio`...). `learn_levels` : seuil d'un niveau =
  milieu entre le 80e centile des avis en dessous et le 20e centile des
  avis a ce niveau ou au-dessus (>= 2 avis de chaque cote), croissant ;
  garde sur la zone (`ia_capture_zones.levels {dense, ralenti, bouchon,
  votes, agree}`). Etat final = le PIRE du mouvement et du nombre
  (`_apply_levels`). Limite : un avis "fluide" ne corrige pas un faux
  ralenti du mouvement (seuls les nombres sont appris). Avis -> historique
  du jour recalcule. Mesures avec avis de zone : jamais purgees. Alertes
  trafic inchangees (ralenti / bouchon). GALERIE : carte compacte (photo +
  "juste ?" global + "Niveau par trace : x/y donne(s)") ; les traces (nom
  pres du depart, fleche a l'arrivee) et le "Vrai niveau" PAR trace sont
  dans l'AGRANDISSEMENT (choix de l'utilisateur : cartes trop chargees
  sinon). `/api/detection/gallery` donne `zones` / `zone_votes` ; "sans avis"
  = ni `vote` ni `zone_votes` ; "Oui a tout le reste" = avis global
  seulement. Le detail `samples/<id>` porte `trace` sur chaque vehicule
  (`assign_traces` avec les traces ACTUELS, non stocke) : pastille a la
  couleur du trace dans les photos. COMPTEUR du jeu d'entrainement en tete
  de galerie (`GET /api/detection/gallery/stats`, recharge avec la
  galerie) : avis oui / non par source, photos d'intrusion annotees (cadres
  ou "rien") vers `TRAIN_TARGET_PHOTOS` = 300 (objectif avant un
  re-entrainement du detecteur, qui N'EXISTE PAS encore), avis par trace.
- STATISTIQUES (`ia_capture_stats`, GARDEES SANS LIMITE, contrairement aux
  mesures) : une ligne par camera, canal, zone trafic (`image` sans zone) et
  quart d'heure (`slot` UTC ; `day`, `dow` 0 = lundi, `hm` en heure de
  Paris). Champs : `samples`, `images`, `veh_max` (max de vehicules sur UNE
  image), `veh_sum` (somme des moyennes par image -> moyenne = veh_sum /
  samples), `measured` / `stopped` / `slow` (vehicules mesures, 1re image de
  chaque paire), `speed_sum` / `speed_n` (longueurs/s, deux paires),
  `states.{fluide,ralenti,bouchon}`. `capture_ia.add_stats` a chaque mesure
  (une fois : `in_stats` sur la mesure) ; rattrapage `capture_ia.py
  --stats-backfill`. Nombre de vehicules = REMPLISSAGE, pas un debit. Zone
  redessinee : les quarts d'heure deja comptes ne sont PAS recalcules
  (l'historique au-dela de 30 jours n'existe plus ailleurs).
- WAZE CROISE : itineraires Waze (flux partenaire, `waze_trafic` latest,
  historique `waze_trafic_history` garde 1 an par TTL, ~1 releve / 16 min)
  lies a un canal trafic, PLUSIEURS possibles : choisis dans le mode Trafic
  (bouton "Itineraires Waze", tries par distance au trace ; `ia_capture_links`
  "<camera_id>:<canal>" `{waze_routes: [id]}`) OU etiquetes dans leur nom
  Waze `[cam:133]` (tous les canaux) / `[cam:133.2]` (un canal) : numero =
  debut du nom de la camera (`capture_ia.waze_tagged`). Ces itineraires
  etiquetes sont IGNORES PARTOUT AILLEURS (mur, /trafic/*, montre, briefing,
  explication IA des alertes, export CSV) : `trafic_etat.sans_routes_camera`,
  applique dans `traffic._get_waze_trafic_payload` et
  `trafic_etat.lire_routes`. Recommande : creer
  dans Waze des itineraires COURTS sur les segments vus par la camera, avec
  l'etiquette (un itineraire de plusieurs km dit peu sur ce qui se passe
  devant la camera : Antares 10/10 17:25, "Leroy Merlin -> Tertre rouge"
  x1,69 mais troncon devant la camera x1,14). SENS DE CIRCULATION :
  ITINERAIRE TRACE sur la photo (`ia_capture_zones.kind = "itineraire"`,
  `line` = points du DEPART a l'ARRIVEE, `waze_route` / `waze_name`
  facultatifs ; editeur "Zones de mesure", type par defaut en trafic). Un
  vehicule appartient au trace le plus proche (centre a moins de 0,7 x sa
  taille + 0,01, distances corrigees 16:9) S'IL VA DANS LE SENS du trace
  quand il bouge (`move` = deplacement du centre sur la paire, fractions/s,
  pose par `classify_pair` ; cosinus >= -0,2) : deux sens colles sont
  separes (test : 146/146 vehicules au bon trace face a un trace inverse
  voisin). Les traces comptent comme des zones trafic (etat, alertes,
  statistiques : `capture_ia.measure_targets`). L'itineraire Waze d'un trace
  est lie d'office et ne va que sur la ligne de ce trace dans
  `ia_capture_stats` (`route_zones` : ancien choix par zone, plus propose).
  Zone creee / modifiee / supprimee : `rebuild` des statistiques par la
  tache. `waze_for_sample` : par
  itineraire lie temps / temps habituel (`ratio`), `jam` 0-4, et le
  sous-itineraire Waze le plus proche de la camera (<= 150 m, `sub`) ;
  signalements Waze a moins de 400 m (`alerts` par type). Garde sur la
  mesure (`waze`) et dans `ia_capture_stats` (`waze_n`, `waze.<id>.{n,
  time_sum, hist_sum, ratio_sum, ratio_max, jam_max, sub_n, sub_ratio_sum,
  sub_ratio_max, name, sub_name}`, `waze_alerts.<type>` max). Releve Waze a
  plus de 20 min de la mesure : ignore. Liens changes (page IA) ou etiquette
  apparue / retiree dans Waze (`ia_capture_links.tagged`) : `rebuild` ->
  la tache refait les quarts d'heure encore couverts par des mesures
  (`rebuild_stats`), jamais Cockpit.
- Volume 4G sans toucher aux reglages : ~950 Ko par photo (Dahua 2560x1440),
  deux paires (4 photos) / 3 min = ~1,8 Go/jour/camera. Le port RTSP (554)
  des routeurs 4G n'est pas ouvert : photos enchainees.
- Alertes trafic : PAR LA CENTRALE D'ALERTES uniquement (pas de reglage
  sur l'usage). Type de regle `traffic_camera` ("Trafic - ralenti /
  bouchon") : `params {channels: ["<camera_id>:<canal>"], min_state:
  bouchon|ralenti, consecutive (2), cooldown_min (30)}`. Creees par
  `capture_ia.dispatch_traffic` (tache planifiee) au format du recepteur
  (`cockpit_active_alerts`, photo A en `snapshot_path`, `camera_path`
  "capture:<cle>", WhatsApp de la regle). Une alerte par episode et par
  regle (`ia_capture_state.alert_episodes`), fermee a la premiere mesure
  sous le seuil. JAMAIS d'alerte pour une camera SANS ZONE (vehicules gares
  = faux bouchon : 151 Giratoire Technoparc, 10/10/2026) ; pastille "Zones
  a dessiner" dans le mode Trafic.
- API (`ia_capture.py`, blueprint de la page) : `GET/POST
  /api/detection/capture/cameras`, `PUT/DELETE .../<cid>/<ch>`, `POST
  .../now` (prochain passage de la tache), `GET .../presets` (lecture),
  `GET /api/detection/capture/samples?key=&hours=`, `GET .../samples/<id>`,
  `POST .../samples/<id>/feedback {vote, state}`, zones CRUD
  `/api/detection/capture/zones`, `POST /api/detection/groups/<gid>/member
  {camera_id, channel, member}` (n'ecrase pas le reste du groupe).
- `_detection_channels` inclut les canaux en capture (`modes`) : ils
  peuvent entrer dans un groupe (planning) et apparaissent sur la carte.

### Foule (trop de monde dans une zone)
- Meme usage `capture`, `purpose: "crowd"` ("Analyse IA (trafic / foule)") :
  UNE photo toutes les `interval_min` (5 par defaut), gardee en pleine
  taille (4096 px max, q85 : les personnes au fond sont minuscules).
- Modele : ZIP-B NWPU-Crowd (Yiming-M/EBC-ZIP, MIT, 2025, suite de
  CLIP-EBC), poids UNIQUES dans `tagger\models\analytics\crowd\zip\`
  (code `code\ZIP-main`, poids `weights_nwpu_b\ebc_b_best\best_mae.pth`).
  Charge par le service IntrusionVerifier (`POST /density {photo, zones}` :
  fenetres 672 px, normalisation CLIP, comptes par polygone, carte reduite
  27 x 48 pour l'affichage, + YOLO personnes pour info).
  PIEGE ultralytics : `classes` passe a `predict` RESTE sur le predicteur
  pour les appels suivants. `/density` passe `classes=[0]` : `/verify` et
  `/detect` passent donc `classes=None`. Oublie le 10/10/2026 : de 12 h 18
  a 13 h 40 (Paris), plus aucun vehicule vu par le trafic et une intrusion
  vehicule (Tertre Rouge) filtree a tort. Dependances
  ajoutees au venv titan_prod le 10/10/2026 : open_clip_torch, peft
  (accelerate), sans changer torch. Reseau CLIP de base telecharge une
  fois (cache HF du compte du service). Chargement ~35 s au demarrage.
- Tagger utilise le MEME modele : export TorchScript
  `tagger\scripts\crowd\export_zip_model.py` ->
  `aerial_density\crowd_density_zip_nwpu_b_torchscript(_cuda).pt`
  (entree ImageNet convertie en CLIP dans le modele), prefere par
  `tagger\app.py` s'il existe (version `crowd_density_zip_nwpu_b_v1`).
  PIEGE Tagger (pre-existant) : tuiles 1024 sans recouvrement, la derniere
  recalee sur le bord : la bande commune est comptee deux fois (photo 1080
  px de haut : ~2 rangees presque superposees).
- Zones `kind: "foule"` : `surface_m2` (saisie ou mesuree sur la vue
  aerienne IGN dans le dialogue, aire geodesique), `seuil_charge` (2
  p/m2), `seuil_trop` (4 p/m2), `geo`. Niveau = personnes / surface :
  normal / charge / trop ; sans surface : "inconnu" (compte seul). Recalcul
  de l'historique depuis la carte reduite quand une zone change.
- Alerte : regle `crowd_camera` ("Foule dense (analyse camera)") `params
  {channels, min_level: charge|trop, consecutive (1), cooldown_min (30)}`,
  `capture_ia.dispatch_crowd`, une alerte par episode.
- Carte globale : pin FOULE = carre arrondi (trafic = rond), couleur du
  niveau ; volet de detail : "Analyser le trafic" / "Analyser la foule".
- Cameras pilotes (10/10/2026) : C06 - Portiques canal 1 (interieur
  musee ; le canal 2 thermique ne cadre pas les portiques) et 221 - P1A
  (canal 2 de "220 - P1A - Village", panorama 3632 x 1632), filaires, RTSP
  ouvert.

### Secours (personne au sol) -- depuis le 11/10/2026
- Usage capture `purpose: "secours"` (3e option de l'usage "Analyse IA"
  de la page Cameras ; un canal = UNE fonction : trafic, foule OU secours).
  Une photo toutes les `interval_min` (5 par defaut), `capture_ia.capture_secours`.
- Modele : yolo11m-pose (Ultralytics, 17 points COCO), telecharge le
  11/10/2026 dans `tagger/models/analytics/ground/yolo11m-pose.pt` (Tagger
  n'avait aucun modele de pose). Charge par IntrusionVerifier (`POST /pose
  {photo}` -> `persons [{conf, box, kpts}]`, `size`) ; imgsz 1920 conf 0.2
  (en 1280, la moitie des personnes de ~100 px manquaient). ~140 ms.
- Posture decidee par `capture_ia.posture` (reglable sans redemarrer le
  service) : axe epaules (ou tete) -> chevilles (ou genoux, hanches) en
  pixels ; >= 60 degres de la verticale = "allonge", <= 35 = "vertical"
  (debout OU assis), entre = "incline" ; sans points, cadre >= 1,6 fois
  plus large que haut = allonge. Compte (`counted`) si allonge, conf >= 0,4
  et dans une zone "secours" (sans zone : toute l'image).
- Etat : "rien" / "sol" (allonge sur cette photo) / "immobile" (deja
  allonge au meme endroit sur la photo precedente, < 3 intervalles :
  centres a moins d'une taille de cadre ; `since` repris, `lying_min`).
  Mesure : `persons_pose`, `lying`, `lying_min`, `total`.
- Essai du 11/10/2026 : 34 personnes debout (Foule, intrusions) toutes
  "vertical" ; personnes tournees de 75-90 degres : 15/17 "allonge" (conf
  >= 0,4). Pas encore de vraie photo de personne au sol.
- ALERTES (11/10/2026) : regle "Personne au sol (analyse camera)" de la
  centrale (`detection_type: "secours_camera"`, params `channels`,
  `min_state` sol | immobile (defaut immobile), `min_minutes`,
  `consecutive`, `cooldown_min`). `capture_ia.dispatch_secours` : une alerte
  par episode et par regle (photo + `box` de la 1re personne au sol,
  `secours_state`, `lying`, `lying_min`), episode ferme a la 1re photo sans
  personne au sol, `secours_episodes` dans `ia_capture_state`. Teste sur
  base factice (1 alerte par episode, cooldown respecte). Creee desactivee
  par l'operateur ; sans regle : observation. `recompute_zones` saute les
  mesures secours (duree enchainee d'une photo a l'autre). Les regles
  trafic / foule ne proposent pas ces cameras (et inversement).
- Page IA : mode "Secours" (liste, interrupteur `secours`, Ajouter), carte
  globale (filtre Secours, pin `personal_injury` prioritaire quand
  quelqu'un est au sol), galerie (source `secours`, avis Oui / Non avec
  vrai etat rien / sol / immobile), zones de type "secours".

### Galerie (entrainement)
A REVOIR (11/10/2026, mode par defaut) : tout actif = des milliers de
photos par jour, presque toutes identiques a la precedente. Chaque mesure
recoit `review {score, reasons}` (`capture_ia.review_of`, apres insertion,
`set_review` ; rattrapage `capture_ia.py --review-backfill`) :
changement d'etat vs la mesure precedente du canal (+3), trafic
ralenti / bouchon (+2) ou dense (+1), zone ou mouvement et nombre
(`state_motion` / `state_count`) different (+2), Waze >= x1,5 mais IA
fluide ou Waze <= x1,1 mais IA ralenti / bouchon (+3) ; foule trop (+2),
a +-20 % d'une capacite (+2) ; secours personne allongee (+4), penchee
(+1), immobile (+2). Score 0 : tirage de controle 1 sur 40
(`REVIEW_AUDIT`) pour mesurer la justesse du reste sans biais.
Intrusions : `_intrusion_review` a la lecture (toutes celles jugees par
l'IA, douteuses d'abord). `mode=revoir` : sans avis ET score >= 1, tri
score puis date, pagination `offset` (le front ne compte que les cartes
encore sans avis) ; `mode=todo` / `all` comme avant (`before`). `counts
{revoir, todo}` affiches. Au 11/10/2026 : 49 mesures sur 1 131 a revoir
(~4 %). Index `review.score, at`.
PURGE (11/10/2026) : sans avis ET sans interet (`review.score` 0, tirage de
controle exclu puisqu'il a un score) supprimees apres 7 jours
(`CAPTURE_LOW_INTEREST_DAYS`), les autres sans avis apres 30
(`CAPTURE_RETENTION_DAYS`), jamais celles annotees. Trafic : seulement si
deja dans `ia_capture_stats` (`in_stats`), qui restent sans limite. Mesures
sans `review` (avant le rattrapage) : regle des 30 jours.
`GET /api/detection/gallery?source=all|intrusions|traffic|crowd|secours&mode=revoir|todo|all&before=&offset=`
(`captures` = trafic + foule) : intrusions (photo de couverture, verdict
IA, pouces) et captures (etat, "juste ? oui / non -> vrai etat")
melangees, plus recentes d'abord, filtre "sans avis". Clic sur une
intrusion : fiche en mode Intrusions (cadres). Bouton "Oui a tout le
reste" : l'operateur marque d'abord les erreurs (Non), puis valide d'un
clic les photos AFFICHEES encore sans avis (hors intrusions non verifiees
et niveaux "inconnu"), un appel par photo.
Pourcentage affiche (fiche, badge, galerie) : le PLUS HAUT de l'IA,
detecteur ou avis SigLIP, quelle que soit la decision ("l'IA voit surtout").

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

## Mur TV des intrusions (`/intrusions-mur`)

Ecran de surveillance du PC (role manager, afficheurs du portail), sans clic,
rafraichi toutes les 5 s. Menu : Ecrans TV > "Mur intrusions". Page
`templates/intrusions_mur.html` (CSS / JS en ligne, comme les autres murs),
calcul `detection.etat_mur(db, now)`, routes dans `app.py` (HORS du
blueprint Detection, reserve aux admins et au groupe Detection) :
`/api/intrusions/mur` et `/api/intrusions/photo?p=intrusions/...` (chemin
EXACT pour `PAGES_AFFICHEUR`, la photo passe en query string ; `image_mur`
n'accepte que `intrusions/`, jamais `captures/`).

- Intrusion du mur = DANS LE PLANNING et non `ecartee` (`portee`) : les
  ecartees et hors planning sont seulement comptees dans le pied ; une
  douteuse est a traiter, jamais en cours.
- En cours (message < `EPISODE_GAP_S`) : en-tete et cadre de la carte rouges,
  "INTRUSION EN COURS", DERNIERE photo de l'episode (la plus fraiche) avec le
  cadre de la cible et un zoom sur elle, camera, zone, type, duree a la
  seconde ; les autres en cours en vignettes ; le bandeau "a traiter" est
  masque pour laisser la place a la photo.
- Calme : "AUCUNE INTRUSION" en vert, derniere intrusion (photo de
  couverture), puis les 4 dernieres non traitees (`status new`, 12 h).
  Pastille "a jour HH:MM" / "DONNEES ARRETEES" : un ecran calme ne doit pas
  ressembler a un ecran fige.
- Carte : un point par appareil (`_detection_channels`, usage
  fielddetection, position connue) ; rouge = en cours (etiquette), orange =
  a traiter (nombre), vert = active (`arming`, canal sans groupe = actif),
  gris = hors planning ou desactive. Zones dessinees, rouges quand une
  intrusion en cours les concerne. Carte figee (cadree sur cameras + zones).
- Pied : intrusions du jour (heure de Paris), pietons, vehicules, non
  traitees, ecartees par l'IA, hors planning, histogramme par heure.
- Le mur ne traite rien : le traitement reste dans `/detection`.
