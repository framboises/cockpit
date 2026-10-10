# Cameras : configuration unique, identifiants chiffres, usages

Page `/cameras` (`cameras.py`, `templates/cameras.html`, `static/js/cameras.js`,
`static/css/cameras.css`). Acces : admin ou groupe ayant la page "Cameras"
(`app.ADMIN_PAGE_REGISTRY`). C'est la SEULE page de configuration des cameras :
la page LAPI renvoie ici (`/cameras?usage=anpr`).

## Modele de donnees

- `cockpit_cameras` : un document par APPAREIL (cle unique ip + port).
  `name, ip, port, protocol, brand, model, firmware, site, location, notes,
  enabled, vms_server, group_path, credential_profile_id, camera_path,
  channels[], source.seetec`.
  - `channels[]` : `{no, name, enabled, roles[]}`. Un canal = une source video
    (multi-capteurs : 10.34.1.171 a 5 canaux ; `/portehoux` = canal 4).
  - `camera_path` : chemin d'URL sur lequel l'appareil pousse ses evenements
    au recepteur (`http://10.24.0.251:8000/<chemin>`, ecoutehik2.py). Unique.
    Chaque evenement porte son `channelID`.
  - `roles[]` : usages du canal, catalogue `cameras.ROLE_TYPES` :
    - `anpr` : LAPI (`forward_role` entry/exit, `lieu` parking Vision,
      `visible`). Miroir automatique vers `anpr_camera_config`
      (`_sync_anpr_config`), toujours lu par `anpr.py` et
      `cockpit_dispatch.py` : ne JAMAIS editer `anpr_camera_config` a la main.
    - `fielddetection` : detection d'intrusion (page Detection,
      `docs/claude/detection.md`).
- `cam_credential_profiles` : `{name, user, secret, note}`. `secret` =
  AES-256-GCM (`camera_secrets.py`, format `v1:<nonce>:<chiffre>`, AAD fixe).

## Identifiants (regles non negociables)

- Cle maitre : variable d'environnement UTILISATEUR `COCKPIT_CAMERA_KEY`
  (32 octets base64) du compte qui fait tourner Cockpit (`l.arnault`).
  Generee le 09/10/2026. Perte de la cle = mots de passe a ressaisir dans les
  profils (rien d'autre n'est perdu). Ne jamais la journaliser ni la commiter.
- Le mot de passe n'est JAMAIS renvoye au navigateur : saisie seule, affichage
  "defini". `PUT /api/camera-profiles/<id>` ne le remplace que s'il est saisi.
- Plus aucun mot de passe en clair dans `cockpit_cameras` (champ `password`
  supprime a l'import et a chaque enregistrement).
- Pour piloter une camera en Python : `cameras.make_hik_camera(doc, canal)`
  (dechiffre le profil ; repli `HIK_PASSWORD` si l'appareil n'a pas de profil).
  Utilise aussi par la capture de la main courante (`/api/pcorg/camera-capture`).

## Pilotage

Marques pilotables : `CONTROLLABLE_BRANDS` (hikvision, bosch, dahua, hanwha,
axis ; les thermiques HM-TD sont classees hikvision). Actions exposees :
capture par canal (`/capture?channel=n`, derniere capture servie par
`/snapshot?channel=n`), test de connexion (met a jour modele et firmware),
presets (`GET /presets`, `POST /preset {preset, channel}`), essuie-glace
(`WIPER_BRANDS` = hikvision seulement). Vivotek, Uniview et interphones
generiques sont geres (inventaire, identifiants, usages) mais pas pilotes.

- **CAMERAS DE PISTE : JAMAIS RIEN (regle non negociable, securite piste).**
  `is_piste_device()` : groupe SeeTec "Pistes" / "Cam Courses", enregistreurs
  (DH-XVR, DS-72/73/77/96), sous-reseau 10.34.2.x. Verrou dans
  `make_hik_camera` (leve `PisteCameraLocked` avant tout envoi, donc aussi
  pour la capture de la main courante) et dans `_require_control` (403).
  Concerne les 9 XVR Dahua (Bugatti, Grand circuit, MB) et le DVR Hik du
  CIK. 10.34.2.180 (Technoparc Formation) est hors piste : exception du seul
  critere IP (`PISTE_IP_EXCEPTIONS`, valide par l'exploitation). Ne jamais
  assouplir sans demande explicite, ni sonder ces appareils "en lecture".
- **Pas de deplacement libre** (fleches, zoom) : decision de l'exploitation,
  la vue ne change QUE par rappel d'un preset deja enregistre sur la camera.
  Le serveur refuse un preset absent de la liste de la camera.
- **Presets de fonction Hikvision** : les domes Hik listent 33-50 et 92-105
  comme des presets ordinaires alors que ce sont des commandes (94 =
  redemarrage, 95 = menu OSD, 92/93 = limites, scans, rondes). Filtres par
  `HIK_RESERVED_PRESETS`, jamais proposes ni rappeles. Noms Hik encodes URL
  par SeeTec (`*+Vue+Nord`), decodes a la lecture.
- **Bosch** (AUTODOME 5000i, MIC 7000i, firmwares 6.41 a 7.90, profil
  "Bosch - service") : tout en Digest. Infos, presets, GotoPreset en ONVIF
  (`/onvif/device_service`, `media_service`, `ptz_service`, premier profil
  media) ; capture `/snap.jpg?JpegCam=<canal>`. Basic est refuse par
  snap.jpg et RCP+ repond en Basic sans authentifier (`<auth>0`). Les
  AUTODOME n'ont pas d'essuie-glace ; le MIC 7000i en a un (commande
  auxiliaire ONVIF `tt:Wiper`), non branche.
- **Dahua** (domes SD6C / SD49 4G, profil "Dahua - admin", Digest) : CGI
  `magicBox.cgi` (infos, `getSoftwareVersion`), `snapshot.cgi?channel=<n>`,
  `ptz.cgi?action=getPresets|start&code=GotoPreset`. Presets avec
  `PresetFunction` autre que `None` ecartes. Noms par defaut ("Preset1",
  "Preposition1") : a renommer sur la camera pour des vues parlantes.
- **Hanwha** (SUNAPI, Digest ; 35 appareils ont le profil nomme
  "Hikvision - admin", identifiants communs) : les erreurs arrivent en HTTP
  200 avec un corps `NG` + `Error Code` (`_hanwha_get`). Presets via
  `ptzconfig.cgi?msubmenu=preset&action=view` (format
  `Channel.0.Preset.1.Name=`), rappel `ptzcontrol.cgi ... action=control`.
  Code 608 = camera fixe (liste vide). Seules les SNP-6320RH sont PTZ.
- **Axis** (VAPIX, Digest) : `param.cgi` (Brand, Properties.Firmware),
  `jpg/image.cgi`, `ptz.cgi?query=presetposall` ("PTZ disabled" sur les
  fixes), rappel `gotoserverpresetno` (204 = succes, "Error" en 200 =
  refus). L'AXIS 221 (10.34.1.101) refuse tous les profils.
- `capture_image` refuse une reponse qui n'est pas une image (Dahua et
  Hanwha repondent parfois 200 avec un texte d'erreur).
- **Cameras 4G** (`is_cellular` = IP publique, routeurs cellulaires : 27
  appareils au 09/10/2026, Hik DS-2DE5425IWG-K/4G et domes Dahua) : le modem
  s'endort sans trafic. `make_hik_camera` leur donne 20 s de connexion / 30 s
  de lecture, et `call_camera` refait UNE tentative si la connexion echoue
  (la premiere sert de reveil) : jusqu'a ~1 min pour une capture. Point
  orange "en veille" dans la liste (test TCP de 3 s au lieu de 1 s).
- "Tester la connexion" sur une Hikvision ajoute les canaux absents
  (`_detect_channels`, `/ISAPI/System/Video/inputs/channels`), nommes
  "<appareil> - canal N" si la camera n'a pas de nom ; jamais de suppression
  ni de renommage (usages conserves). Cas d'origine : 042 - P9C Toit Siege
  remplacee (Bosch -> Hik multi-capteurs 5 canaux, 10.34.1.100).
- Toute action de pilotage passe par `call_camera` + `camera_error` : une
  camera injoignable est journalisee sur UNE ligne (warning), sans trace ;
  seules les erreurs inattendues gardent `logger.exception`.
- Rappeler un preset change la vue pour TOUS (operateurs SeeTec compris) :
  confirmation dans la page, puis capture automatique 4 s apres.

## LAPI par consultation (Axis License Plate Verifier)

`anpr_lpv.py`, lance toutes les 5 min par la tache planifiee Windows
"Cockpit - LAPI Axis" (`scripts/anpr_lpv.bat`, installee par
`scripts/install_anpr_lpv_task.ps1`). JAMAIS dans le process Flask (choix de
l'exploitation : pas de tache cachee dans Cockpit). La tache tourne sous le
COMPTE DE COCKPIT (cle `COCKPIT_CAMERA_KEY` = variable utilisateur ; sous
SYSTEM : "COCKPIT_CAMERA_KEY absente"). Verrou `logs/anpr_lpv.lock`, journal
`logs/anpr_lpv-AAAAMMJJ.log`. Pour les cameras qui n'envoient rien au recepteur : usage
LAPI d'un canal avec le parametre `source = "axis_lpv"` (le chemin d'envoi
n'est alors plus exige, `no_path_sources` dans `ROLE_TYPES`).

- **LAPI Porte Nord (10.34.0.192, Axis P1455-LE) commande la barriere** (la
  decision vient d'un systeme d'acces externe : les listes LPV sont vides).
  NE JAMAIS modifier sa configuration (LPV, regles, destinations) : on lit
  seulement, avec le meme appel que l'interface LPV.
- Premiere consultation d'une camera (aucun `lpv_ts` connu) : base de depart
  = les 10 dernieres lectures (`events.cgi?limit=10`), pas de reprise de
  l'historique. Ensuite : TOUTES les lectures posterieures a la derniere
  connue, `search.cgi?timestampfrom=<lpv_ts+1>&timestampto=...&text=` par
  pages de 100 (`offset`). LPV ne garde que 1000 lectures : une tache arretee
  longtemps perd ce qui a ete ecrase sur la camera.
- Passages ecrits dans `hik_anpr` sous le chemin interne
  `cameras.anpr_lpv_path()` (`/lpv-10-34-0-192`), miroir dans
  `anpr_camera_config` par `_sync_anpr_config`. Dedoublonnage :
  `uuid = axislpv-<ip>-<CAR_ID>` (+ `lpv_car_id`, `lpv_ts` en microsecondes
  = curseur de la consultation suivante, `source`, `lpv_action`).
  Dates UTC naives comme le recepteur Hik.
- Codes LPV (interface fflprapp, `main.js`) : `DIRECTION` 2 = entree
  (forward), 3 = sortie (reverse), autres = inconnu ; `ACTION` dans
  {1,2,4,101,102,201,202} = acceptee -> `vehicle_list_name: allowList`.
  `THRESHOLD` (0-1) -> `confidence` (0-100).
- Images : `tools.cgi?action=getImage&name=...` (plaque `LP_BMP`, vehicule
  `ROI_BMP`), copiees sous `HIK_IMAGE_DIR/AAAA/MM/JJ/lpv-.../`.
- Alertes liste de surveillance et console Hik : `cockpit_dispatch.py` du
  recepteur (hors depot) charge par chemin (`ANPR_DISPATCH_FILE`), memes
  appels que `ecoutehik2.py`.
- Test manuel : `python anpr_lpv.py --dry-run` (interroge, n'ecrit rien) ou
  `python anpr_lpv.py` (TITAN_ENV=prod, sous le compte de Cockpit).
- La configuration LPV (`config.cgi`) expose des mots de passe en clair a
  tout compte de la camera : ne jamais la journaliser ni la copier.

## Carte des cameras

Bouton "Carte" de l'en-tete (vue memorisee par poste, `?view=map`). Suit les
filtres du tableau. `GET /api/cameras/map` : appareils ayant une position,
par `detection._device_position` (une IP = un lieu : position manuelle de la
page Detection, sinon couche `titan.cameras` par IP puis nom), `snap_at` =
date du fichier `<id>_latest.jpg`. Survol d'un pin : fiche courte + derniere
capture (chargee au survol) ; clic : fiche de l'appareil. Cadrage sur les
cameras a moins de 30 km du circuit (la boutique de Paris n'ecrase pas la vue).

Correction de position (bouton "Deplacer" de la carte, puis glisser un pin,
confirmation) : `PUT /api/cameras/<id>/geo {lat, lng}` ECRIT DANS LA COUCHE
`titan.cameras` : tous les points de l'IP (un point par canal pour les
multi-capteurs) et efface la position manuelle de la page Detection (qui
suit alors la couche, cache GPS invalide). Historique `cameras_geo_edits`
(anciennes coordonnees par `_id_feature`, nouvelles, auteur, date) : de quoi
revenir en arriere a la main. Logique unique `place_device`, utilisee aussi
par le bouton "Placer" de la page Detection.

Couche `titan.cameras` tenue a jour par Cockpit (conventions de la couche,
partagee avec d'autres outils) :
- Appareil absent de la couche place sur la carte (liste "Placer une camera
  sans position" du mode Deplacer, ou page Detection) : UN POINT PAR CANAL
  cree au meme endroit (`SEETEC NAME` = nom du canal, `NEW ID` / `NEW NAME`
  = numero et reste du nom, `TYPE` FIXE / DOME / DOME 4G / INTERPHONE devine
  : 4G = IP publique, "[F]" = fixe, modele PTZ sur le 1er canal = DOME,
  `_id_feature` = nouvel ObjectId).
- Enregistrement d'une fiche (`PUT /api/cameras/<id>`) : `_sync_layer`
  repercute IP, nom et canaux sur les points de l'ancienne IP. Rapprochement
  par nom de canal, puis points restants aux canaux restants dans l'ordre,
  puis creation pour un canal sans point. JAMAIS de suppression de point
  (suppression d'une camera ou d'un canal : point conserve). Simulation sans
  ecriture : `_sync_layer(doc, doc, dry=True)` ; au 09/10/2026 elle ne
  touchait que 179 (nom de canal) et 042 (4 canaux sans point).

Capture de masse : `POST /api/cameras/capture-all {ids}` (premier canal de
chaque appareil actif, pilotable, avec profil ; JAMAIS les cameras de piste),
`GET` = progression. Une seule a la fois (409), 8 en parallele dans un thread
dedie (les threads Waitress ne sont pas occupes), `call_camera` (reveil 4G).

## Etat reseau

`GET /api/cameras/reachability` : connexion TCP au port de chaque appareil
actif, sans identifiant, 48 en parallele, resultat garde 60 s par IP. Meme
principe que les voies de la page LAPI (`anpr._reachability`).

## Import SeeTec

`scripts/import_cameras_seetec.py <InstallationConfiguration.xlsx> [--apply]`
(TITAN_ENV=prod, COCKPIT_CAMERA_KEY chargee). Une ligne source = un canal,
regroupe par IP ; numero de canal = ordre dans l'export (verifie sur
10.34.1.171 : capture du canal 4 = vue des intrusions /portehoux). Un profil
par couple utilisateur / mot de passe (reconnu par dechiffrement aux imports
suivants), fusion par IP en conservant `_id`, chemin, usages. Import initial
du 09/10/2026 : 329 appareils / 546 sources, 12 profils, 8 appareils hors
export conserves. L'export contient les mots de passe en clair : ne pas le
laisser trainer.

## Pieges

- `GET /api/cameras` doit rester un TABLEAU d'appareils (`_id, name, ip,
  enabled, location`) : le selecteur de camera de la main courante (pcorg.js)
  le lit tel quel.
- `/api/cameras-list` (centrale d'alertes) ne liste que les appareils qui ont
  un `camera_path` (+ chemins LAPI) : sans chemin, aucun evenement n'arrive.
- Le recepteur (`E:\TITAN\production\PCA\SCRIPTS\ecoutehik2.py`, service
  NSSM `HikRecepteur`) est hors depot ; `cockpit_dispatch.py` y resout les
  noms de cameras via `cockpit_cameras.camera_path`.
