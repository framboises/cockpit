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
  - `channels[]` : `{no, name, enabled, roles[], optics, optics_src, view}`.
    Un canal = une source video (multi-capteurs : 10.34.1.171 a 5 canaux ;
    `/portehoux` = canal 4).
  - `optics` : `mobile` (PTZ) ou `fixe` ; `optics_src` : `presets` (lu sur
    la camera), `modele` (deduit, a verifier : pointilles orange dans la
    fiche), `manuel` (choisi dans la fiche, jamais ecrase). Pre-rempli par
    `scripts/cameras_optics_prefill.py [--apply]` (LECTURE SEULE des presets,
    piste exclue). Regle : presets presents (meme le seul 90 "Wiper" des Hik
    2SF / 2DF) = mobile ; aucun = fixe, sauf canal 1 d'un modele de dome
    (`_PTZ_MODEL_RE`) = mobile d'apres le modele. Pieges verifies le
    17/10/2026 : les Hik DS-2DP multi-capteurs (001, 009, 025, 119, 137, 165,
    178, 901) ont la PTZ sur le canal 1 et 4 fixes ; `_geo_type` (TYPE de la
    couche GPS) se trompe sur elles, il ne lit pas `optics`.
  - `view` : angle de vue dessine sur la carte `{az, fov, range, by, at}`
    (az en degres depuis le nord, sens horaire ; fov ouverture ; range en m).
    Ecrit UNIQUEMENT par `PUT /api/cameras/<id>/channels/<no>/view`
    (`{clear: true}` pour effacer) ; `_clean_channels` le reprend du canal de
    meme numero a chaque enregistrement de fiche (le formulaire ne l'envoie
    pas). Rien n'est envoye aux cameras ni a la couche GPS.
  - `camera_path` : chemin d'URL sur lequel l'appareil pousse ses evenements
    au recepteur (`http://10.24.0.251:8000/<chemin>`, ecoutehik2.py). Unique.
    Chaque evenement porte son `channelID`.
  - `roles[]` : usages du canal, catalogue `cameras.ROLE_TYPES` :
    - `anpr` : LAPI (`forward_role` entry/exit, `lieu` parking Vision,
      `visible`). Miroir automatique vers `anpr_camera_config`
      (`_sync_anpr_config`), toujours lu par `anpr.py` et
      `cockpit_dispatch.py` : ne JAMAIS editer `anpr_camera_config` a la main.
    - `fielddetection` : detection d'intrusion (page IA, mode Intrusions,
      `docs/claude/detection.md`).
    - `capture` : Capture IA (trafic) : paires de photos prises par la tache
      planifiee `capture_ia.py`, reglees dans la page IA, mode Trafic
      (`docs/claude/detection.md`). Pas de chemin d'envoi. Parametre `preset`
      (kind `preset`) : `view_preset_id` n'accepte qu'un preset de VUE 1-300,
      jamais un preset de fonction Hikvision (`HIK_RESERVED_PRESETS`) ; une
      valeur refusee est videe a l'enregistrement, et `capture_ia._preset_of`
      refuse encore au rappel (anciennes saisies) : mesure sans rappel. Avant
      le 24/10/2026, saisir 94 aurait redemarre le dome a chaque mesure.
  - UN SEUL canal LAPI par appareil (`_device_fields` refuse le second) :
    `anpr_camera_config` et le recepteur sont indexes par chemin d'envoi,
    commun a tout l'appareil.
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

## Pilotes (hik/drivers)

`HikCamera` (nom historique, multi-marques) = `hik/drivers/__init__.py`,
compose d'un socle et d'un module par marque :
- `base.py` : HTTP Digest (`_get` / `_put` / `_delete`), `_onvif`, `_kv`, et
  les REPARTITEURS par `brand` (`get_device_info`, `list_presets`,
  `goto_preset`, `set_preset`, `delete_preset`, `capture_image`, `privacy`).
  `PRIVACY_BRANDS` (masques pilotables) est defini ici, relu par cameras.py.
- `hikvision.py` (ISAPI, Uniview compris : PTZ, essuie-glace, Smart Events...),
  `dahua.py`, `bosch.py` (ONVIF, RCP+ `BOSCH_PRIV_CMD`), `hanwha.py`
  (`_hanwha_get`), `axis.py`.
- Ajouter une marque : un module pilote (mixin) + ses entrees dans les
  repartiteurs de `base.py` + la marque dans `cameras.CONTROLLABLE_BRANDS`.
- `privacy(set_to)` = masques VERIFIES sur le parc (anciennes fonctions
  `_privacy_*` de cameras.py, deplacees a l'identique le 24/10/2026).
  `get_privacy_masks` / `set_privacy_masks_enabled` / `_get_privacy_*` ne
  servent qu'a l'outil en ligne de commande, historiques et FAUX pour Dahua
  (VideoWidget) et Hanwha (media.cgi "Submenu Not Found").
- `hik/hik_control.py` ne contient plus que l'outil en ligne de commande ; il
  reexporte `HikCamera` (`from hik.hik_control import HikCamera` marche
  toujours, y compris lance depuis `hik/`).
- Verrou piste de l'outil en ligne de commande (24/10/2026) : `load_cameras`
  ecarte toute entree de `hik_cameras.json` reconnue par
  `cameras.is_piste_device` (IP 10.34.2.x hors exception, modele lu dans
  `notes`) OU dont l'IP est une camera de piste du referentiel
  `cockpit_cameras` (groupe SeeTec "Pistes" / "Cam Courses", enregistreurs).
  Regle ou base injoignable : l'outil REFUSE de demarrer (code 2), sans mode
  degrade. Les commandes `credential` passent avant (rien n'est envoye aux
  cameras). `--camera N` numerote la liste SANS la piste.
- Tests : `tests/test_camera_drivers.py` (URL par marque, lecture des
  reponses, masques), requests remplace par un double.

## Pilotage

Marques pilotables : `CONTROLLABLE_BRANDS` (hikvision, bosch, dahua, hanwha,
axis ; les thermiques HM-TD sont classees hikvision). Actions exposees :
capture par canal (`/capture?channel=n`, derniere capture servie par
`/snapshot?channel=n` ; une seule capture a la fois par IP et
`CAPTURE_MIN_INTERVAL_S` = 3 s entre deux captures d'un meme canal, sinon
429 ; la fiche capture tous les canaux actifs a la suite). Logique unique
`capture_to(doc, canal, save_path)` partagee avec la capture de la main
courante (reservation, delai, vignette DU canal capture, `CaptureBusy` = 429).
Seules les vignettes `<id>_latest.jpg` / `<id>_ch<N>_latest.jpg` sont ecrites
(fichier `.tmp` puis renommage) : plus de fichiers horodates depuis le
24/10/2026 (`scripts/cameras_purge_snapshots.py [--apply]` purge les anciens), test de connexion (met a jour modele et firmware),
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
- **Masques de vie privee** (`GET / POST /api/cameras/<id>/privacy`,
  ligne "Masques de vie privee" de l'Apercu de la fiche, lue a l'ouverture) :
  bascule GLOBALE des masques deja dessines sur la camera, jamais de
  creation / modification de zone. Releve lecture seule du 21/10/2026 :
  - Hikvision : `/ISAPI/System/Video/inputs/channels/<n>/privacyMask` sur
    chaque canal ayant des `PrivacyMaskRegion` (premier `<enabled>` du XML).
    32 cameras ont des masques, TOUS desactives ce jour-la.
  - Hanwha : `image.cgi?msubmenu=privacy&action=view|set&Channel=0&Enable=`
    (interrupteur global ; les zones ne sont listees que masques actifs sur
    certains firmwares : nombre inconnu sinon). `media.cgi
    msubmenu=privacymask` de l'outil hik_control.py repond "Submenu Not Found".
  - Dahua (domes SD) : `configManager.cgi name=PrivacyMasking`, 24
    emplacements `[canal][i]` sans interrupteur global ; seuls les
    emplacements dessines (Rect / Polygon non nuls) sont bascules. Ceux
    sondes (015, 016) n'ont aucun masque. `VideoWidget.EncodeBlend` de
    hik_control.py ne concerne pas ces domes.
  - Bosch : PAS en ONVIF (Media2 n'annonce pas les masques, `GetMasks` =
    "Action Failed" meme avec ConfigurationToken). Par RCP+ en DIGEST :
    `rcp.xml?command=0x0bd8&type=P_OCTET&direction=READ|WRITE&num=1`
    (CONF_PRIV_MSK_POLY, base de connaissances Bosch / Keenfinity). Contenu =
    enregistrements [longueur 2][type 2] : type 0x0002 = un masque (1er octet
    actif, 2e = numero, polygone ensuite ; vide = 12 octets), 0x0003 =
    reglage global des domes, 1er octet 01 = "Disable Masks". On ne reecrit
    QUE cet octet (payload `0x000c0003` + contenu conserve) : l'exemple Bosch
    remet a zero le 3e octet, qui vaut 01 chez nous. Teste le 21/10/2026 sur
    10.34.1.36 (183, AUTODOME 5000i, 7 masques, camera validee par Ludo) :
    bandeau noir disparu puis revenu, configuration identique octet pour
    octet apres retour. Lecture OK en firmwares 6.41 a 7.90 : 12 domes
    masques actifs, 21 inactifs, 8 sans masque. Fixes Bosch sans 0x0003 :
    non pilotees.
  - Axis : pas de groupe PrivacyMask. Non pilotable (message dans la fiche).
  Etat relu apres chaque bascule (`ok` = etat attendu) ; journal
  `cameras_privacy_log` {camera_id, name, ip, enabled, ok, channels, by, at,
  bulk}. Apres une bascule, capture de controle des canaux masques
  (`_privacy_capture`, 1,5 s d'attente, reservation de l'appareil comme la
  capture manuelle) : `captured` dans la reponse, la fiche recharge apercu et
  vignettes. Bosch : une relecture faite juste apres l'ecriture a une fois
  differe de l'origine, les lectures suivantes etaient identiques
  (application en cours sur la camera).
  Bascule de TOUTES les cameras : SUPER ADMIN seulement (`_is_super_admin` =
  `SUPER_ADMIN_ROLE` dans `global_roles` ; `meta.super_admin` affiche le
  bouton "Masques" de l'en-tete). `POST /api/cameras/privacy-all {enabled}` :
  toutes les cameras actives des marques pilotables, avec profil, hors piste ;
  8 en parallele dans un thread dedie, une seule bascule de masse a la fois
  (409) ; par camera : lecture, "nomask" / "unchanged" sans ecriture, sinon
  bascule + journal (`bulk`) + capture de controle. `GET` = progression
  (total, done, changed, unchanged, nomask, failed, failures, by), jauge du
  dialogue qui peut etre ferme pendant la manoeuvre.
  Logique par marque : `HikCamera.privacy` (hik/drivers). `hik/hik_control.py`
  (privacy / privacy-all en ligne de commande) n'est pas utilise par Cockpit.
- Rappeler un preset change la vue pour TOUS (operateurs SeeTec compris) :
  confirmation dans la page, puis capture automatique 4 s apres.

## Journal des actions

`cameras_audit` (TTL `AUDIT_RETENTION_DAYS` = 730 j) : `{action, camera_id,
name, ip, by, at, detail}` ; actions `creation`, `modification` (detail =
`{champ: [avant, apres]}`, canaux resumes sans angle de vue), `suppression`
(copie de la fiche), `suppression_masse` / `modification_masse`
(`camera_ids`), `preset`, `essuie_glace`, `profil_creation` /
`profil_modification` / `profil_suppression` (JAMAIS le mot de passe : seul
`mot_de_passe_change`). `_audit` ne fait jamais echouer l'action.
`GET /api/cameras/<id>/journal` fusionne ce journal, `cameras_geo_edits` et
`cameras_privacy_log` (section "Historique" de la fiche, chargee a la demande).

Tests : `tests/test_cameras.py` (regle piste, presets de fonction, un canal
LAPI, capture par canal). A completer a chaque nouvelle regle.

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

Capture de masse : `POST /api/cameras/capture-all {ids}` (CHAQUE CANAL ACTIF
de chaque appareil actif, pilotable, avec profil, depuis le 11/10/2026 ;
JAMAIS les cameras de piste), `GET` = progression (`total` / `done` en
canaux, `devices`). Une seule a la fois (409), puis `CAPTURE_ALL_COOLDOWN_S`
= 120 s apres la fin avant d'en relancer une (429). Elle partage la reservation
par IP de la capture manuelle (`_device_acquire` / `_device_release`) : elle
attend qu'un appareil occupe se libere (120 s max), la capture manuelle refuse
tout de suite. 8 appareils en parallele dans
un thread dedie (les threads Waitress ne sont pas occupes), les canaux d'un
meme appareil L'UN APRES L'AUTRE (un enregistreur n'est pas sollicite 5 fois
a la fois), `call_camera` (reveil 4G). Vignette par canal :
`<id>_latest.jpg` (canal 1) / `<id>_ch<N>_latest.jpg` ; `/api/cameras/map`
donne `snaps {no: date}` et la bulle de la carte montre une vignette par
canal capture.

Angles de vue (bouton "Angles de vue" de la carte, exclusif avec Deplacer) :
cones enregistres dessines sous les pins (orange = mobile, bleu = fixe,
gris = optique inconnue), pin entoure en pointilles = canal mobile actif sans
cone, compteur "x / y canaux mobiles dessines" dans la barre. Clic sur un pin
: volet du configurateur pour cette camera.

Configurateur (bouton "Configurateur" de la carte) : parcours de tous les
canaux actifs SANS angle de vue des cameras affichees (filtres de la liste,
cameras actives et placees), tries par nom ; s'il n'en reste aucun, propose de
tout revoir. Volet en colonne a gauche, carte cadree sur la camera et son
cone a droite (`fitBounds` avec la largeur du volet en marge). Par canal :
- capture fraiche AUTOMATIQUE (case "Capture auto", memorisee par poste) ;
  la derniere image connue s'affiche grisee en attendant ; cache navigateur
  2 min par canal ; la capture du canal SUIVANT est preparee pendant qu'on
  dessine ; refus 429 du serveur (capture en cours sur l'IP, delai de 3 s)
  : 3 nouvelles tentatives. Piste / non pilotable : derniere image seule.
- optique Fixe / Mobile : un clic = `PUT /channels/<no>/optics` (src "manuel").
- cone : poignee pleine (direction + portee), creuse (ouverture), champs ;
  point de depart = direction d'un autre canal deja dessine de l'appareil.
- "Enregistrer et suivant" (Entree), "Passer" (fleche droite, n'enregistre pas
  le cone), precedent (fleche gauche), F / M optique, C capture, Echap fermer.
But : preparer la position de repos des PTZ et reperer les trous de
couverture ; la remise en position automatique viendra ensuite.

Cameras en projet (bouton "Projet" de la carte, puis clic sur la carte) :
emplacements souhaites pour des travaux a venir. Collection A PART
`cameras_planned` `{name, note, status (souhaitee / validee / commandee),
lat, lng, channels[{no, optics, view}], by, at, updated_by, updatedAt}`
(canaux simules : 1 PTZ + 4 fixes, 1 PTZ + 1 fixe..., 8 au plus ; les
premiers enregistrements avaient optics / view a la racine,
`_planned_channels` les lit encore et la premiere ecriture les convertit) : JAMAIS dans
`cockpit_cameras` ni dans la couche GPS `titan.cameras`, donc invisibles du
pilotage, des captures, du LAPI, de la detection et des autres outils
cartographiques. Routes `GET/POST /api/cameras/planned`,
`PUT/DELETE /api/cameras/planned/<id>` (PUT : name, note, status, lat/lng,
et `ch` + optics / view / clear_view pour un canal),
`POST /api/cameras/planned/<id>/channels {optics, count}`,
`DELETE /api/cameras/planned/<id>/channels/<no>` (un canal minimum). Pin
violet en pointilles, cones violets en pointilles ; affichees quels que
soient les filtres, hors configurateur. Filtre d'etat "En projet
(seulement)" : masque toutes les cameras reelles (liste vide avec renvoi a la
carte), cadrage sur les projets. Le clic ouvre le volet du configurateur
(cote JS : objet `p:<id>`, canaux = `channels`) avec nom / statut / note a la
place de la photo, pastilles de canaux + "Mobile" / "Fixe" (objectif du
modele de l'emplacement pour cette optique, sinon profil par defaut) ; un
nouveau capteur fixe part 90 degres apres le dernier fixe dessine. Champ
"Modele" (`model_id`, catalogue ci-dessous) : applique tout de suite avec les
champs de la fiche, le volet est reconstruit ; multi-capteurs = EXACTEMENT sa
disposition (canal 1 = PTZ..., angles des premiers canaux gardes, canaux en
trop retires), sinon chaque canal d'un autre modele prend l'objectif du
modele de meme optique (`cameras._apply_model`).

Champs de planification d'une camera en projet (volet, sous le nom) :
`priority` (1 = la plus urgente, 1 a 999, vide = a la fin du dossier),
`mount` (`PLANNED_MOUNTS` : mat / angle_mur / mur_plat, vide = a definir),
`mount_height` (m, 0,5 a 60), `replaces` (ids de cameras EXISTANTES
remplacees = a deposer ; choix parmi celles a moins de 500 m, les plus proches
d'abord), `moves_camera_id` (projet = DEPLACEMENT d'une camera existante :
bouton "Planifier le deplacement" du volet d'une camera reelle,
`POST /api/cameras/planned {from_camera_id}` cree le projet a sa position
avec ses canaux, optique et angles de vue ; on glisse ensuite le pin).
Coherence (`_planned_conflict`, 409) : une camera n'est remplacee (deposee)
que par UN projet, reinstallee (deplacee) que par UN projet, et un projet ne
la remplace pas et la deplace a la fois. REEMPLOI admis : remplacee par un
projet ET reinstallee par un autre (`_planned_refs` rend `removed_by` /
`reused_by` sur chaque camera citee). Volet d'un projet sans camera deplacee :
bloc "Reemploi / deplacement" qui propose en tete les cameras deposees par
les autres projets pas encore reinstallees (les plus proches d'abord, bouton
"Reinstaller ici" = `moves_camera_id`), et une recherche parmi toutes les
cameras existantes libres ; apres l'ajout d'un remplacement, un message
rappelle qu'elles seront proposees. Puce d'une camera remplacee :
"reemploi : <projet>".
Sur la carte : trait orange pointille ancienne position -> projet, croix
rouge et trait rouge sur les cameras remplacees. Une camera supprimee depuis
reste citee, marquee "Camera supprimee".

## Dossier de planification des travaux (PDF)

Bouton "Dossier travaux" de la carte : options (titre, sous-titre, indice,
phase, etabli par, fonction, statuts retenus ; memorisees par poste) puis
`GET /api/cameras/planned/dossier.pdf?status=...&indice=...` (nouvel onglet,
journal `cameras_audit` action `dossier_travaux`). `cameras_dossier.py`
(reportlab, dependance de requirements.txt), A4 paysage, charte du dossier
"Porte du Houx indice A" : Barlow + Exo2 BoldItalic (`static/fonts/dossier/`,
OFL), bleu marine #131E41, accent #0971CE, logo ACO blanc sur carre marine
(`static/img/ACO-logo-monochrome-universel-blanc.png`, marge rognee),
cartouche bas droite "n/N PLANCHE" (rendu en DEUX passes pour connaitre N).
- Couverture (chiffres cles, plan reduit, sommaire), plan d'ensemble (carte +
  legende + tableau), UNE planche par projet triee par priorite (repere
  C1..Cn ; sans priorite : a la fin, par nom), recapitulatif paginé (16
  lignes) + points de vigilance + quantitatif.
- Planche emplacement : vue MICRO (cadree sur les cones et les cameras
  reprises a moins de `MICRO_MAX_REF_M` = 120 m ; existantes en gris, autres
  projets en violet pale) et vue MACRO (~600 m), echelle approchee, nord,
  barre d'echelle ; identification (priorite, statut, support + hauteur, GPS
  WGS84 decimal et DMS, Lambert 93 par les formules IGN, verifie sur le point
  de reference du dossier modele) ; canaux (objectif, azimut + cardinal,
  ouverture, portee) ; operation (deplacement : ancienne position et
  distance ; remplacements : distance) ; materiel.
- Objectifs et appareils : catalogue `cameras_models` (ligne "Modele" de
  l'identification, objectif "<reference> - <objectif>" par canal) ;
  multi-capteurs = UN appareil par disposition complete (1 + 4 : 5 canaux =
  1 appareil, 10 = 2) ; un deplacement ne commande rien ; camera deplacee :
  objectif deduit de SON modele, jamais d'objectif par defaut.
- Reemploi : planche du projet qui remplace "a deposer, reemployee en Cn"
  (sinon "retour au stock") ; planche du projet qui reinstalle "Reemploi
  d'une camera existante deposee par Cn" ; projet hors du dossier cite par
  son nom. Quantitatif : deplacements "dont n reemploi(s)", deposes = retour
  au stock seulement (une camera reemployee n'est pas comptee deux fois).
- `datasheets=1` (case "Joindre en annexe les fiches techniques") : planche
  intercalaire "Annexes" (A1, A2... par ordre d'apparition, pages) puis les
  PDF du constructeur joints tels quels (pypdf, requirements.txt) ; renvoi
  "fiche technique en annexe An" dans le materiel et le quantitatif. PDF
  illisible : ecarte. Les pages jointes ne portent pas de cartouche (N =
  planches du dossier).
- Fonds de carte : orthophoto IGN WMTS (data.geopf.fr, sans cle) telechargee
  par le SERVEUR (8 en parallele, 8 s), cache `uploads/tiles_cache/ign_ortho`
  (180 j, hors git), JPEG dans le PDF (~1,5 Mo pour 5 emplacements). Tuile
  indisponible : fond gris et mention sur la carte, le dossier sort quand meme.
- Tests : `tests/test_cameras_dossier.py` (tuiles remplacees par une image unie).

Catalogue des modeles (`cameras_models.py`, collection `cameras_models`,
bouton "Modeles" de l'en-tete) : `{brand, ref, match (motif reconnu dans le
modele des cameras reelles, le plus long l'emporte), multi, note,
lenses[{k, label, optics, fov, range, count}], datasheet{name, size, at, by}}`.
Routes `GET/POST /api/cameras/models`, `PUT/DELETE /api/cameras/models/<id>`
(409 : modele utilise par un projet, ou objectif retire encore utilise),
`POST/GET/DELETE /api/cameras/models/<id>/datasheet` (multipart `file`,
signature %PDF verifiee, 25 Mo, fichier `uploads/camera_models/<id>.pdf`
hors git, ecrit via .tmp). Cle d'objectif `k` unique dans le catalogue,
gardee a la modification (un nouvel objectif recoit `m<hex>`) : c'est elle
que portent les canaux en projet (champ `model`). Catalogue initial pose UNE
fois (marqueur `cameras_meta {_id: "models_seeded"}` : vide par choix, il ne
se remplit pas), cles de l'ancienne table LENSES. Valeurs : ouverture
horizontale et portee DORI "Observer" (63 px/m) des fiches techniques
fournies le 21/10/2026 ; PTZ au grand angle, portee "Observer" ramenee a la
focale mini (tele x focale mini / focale maxi).
- DS-2DP7D836 (1 + 4) : PTZ 59 deg / 48 m ; 4 objectifs fixes 106 deg / 30 m
  (fiche : 22 m ; portee harmonisee a 30 m a la demande de Ludo le
  21/10/2026, aussi appliquee en base aux canaux 2 a 5 deja dessines des
  1 + 4 et aux objectifs "2dp-pano" des cameras en projet).
- DS-2SF8C442 (1 + 1) : PTZ 59 deg / 28 m ; panoramique 190 deg / 31 m.
- iDS-2CD7A87G0 (modele de base de TOUTES les fixes) : 2,8 mm 100 deg / 37 m ;
  12 mm 44 deg / 81 m.
- DS-2CD63C5G1 (360) : plafond 360 deg / 11 m ; mur 180 deg / 11 m.
- PTZ generique : 59 deg / 50 m.
Objectif d'un canal (`lensKeyFor` de cameras.js = `cameras_models.lens_key`) :
choisi (canal en projet : champ `model`), sinon deduit du modele de
l'appareil par le motif du catalogue (rang du canal dans la disposition :
canal 1 = PTZ, autres = objectifs), sinon par l'optique (`FALLBACK` : fixe =
bullet-wide, mobile = ptz, s'ils existent encore). Il donne le cone par defaut. Selecteur "Modele / objectif" du
volet : applique ouverture et portee (direction gardee) ; enregistre pour un
canal en projet, session seulement pour une camera reelle. Verrou (cadenas,
memorise par poste, actif par defaut) : ouverture et portee figees, plus de
poignee d'ouverture, la poignee pleine ne fait que tourner le cone. Le pin se deplace en le glissant pendant que son volet est ouvert.

Telephone (<= 820 px) : barre du bas `.mobile-bottom-nav` de style.css
(Liste, Carte, "+" Ajouter, Filtres avec pastille du nombre de filtres
actifs, Identifiants) ; boutons du haut masques ; seule la recherche reste en
haut, les 4 filtres s'ouvrent en volet ("Filtres", par-dessus la carte en
mode carte) ; la carte prend toute la hauteur.

## Etat reseau

`GET /api/cameras/reachability` : connexion TCP au port de chaque appareil
actif, sans identifiant, 48 en parallele, resultat garde 60 s par IP. Meme
principe que les voies de la page LAPI (`anpr._reachability`). Une seule
vague de tests a la fois (`_probe_lock`) : une rafale de pages ouvertes
attend la vague en cours et relit son cache au lieu d'en relancer une.

## Etat des cameras (releve historisee, alerte hors ligne)

`cameras_health.py`, lance toutes les 2 min par la tache planifiee "Cockpit -
Etat cameras" (`scripts/cameras_health.bat`, installee par
`scripts/install_cameras_health_task.ps1`), JAMAIS dans le process Flask.
Connexion TCP au port web de chaque appareil ACTIF, sans identifiant ni
requete ; cameras de PISTE exclues (pas meme une connexion ; l'etat reseau
live de la page, lui, les teste encore en TCP). Aucun mot de passe lu : la
tache n'a pas besoin de COCKPIT_CAMERA_KEY. Verrou `logs/cameras_health.lock`,
journal `logs/cameras_health-AAAAMMJJ.log`, `--dry-run`.

- Hysteresis (`next_state`) : hors ligne apres `KO_CONFIRM` = 2 releves en
  echec (`KO_CONFIRM_CELL` = 3 pour une 4G, sondee 2 fois avec 8 s de delai),
  `since` = derniere reussite ; en ligne au premier succes. Premiere releve
  d'un appareil = etat pris tel quel, sans changement (pas d'alerte en masse
  a l'installation).
- `cameras_health` (`_id` = camera_id) : `ok`, `since`, `fails`, `raw_ok`,
  `first_seen`, `last_ok_at`, `last_check_at`, `cellular`, `lapi_last_at`
  (derniere plaque de `hik_anpr`, index `camera_path` + `event_dt`). Appareil
  supprime / desactive / reclasse piste : document retire.
- `cameras_health_log` : changements d'etat confirmes (TTL 400 j) ;
  `availability(db, id, heures)` en tire la disponibilite.
- Page Cameras : bulle du point d'etat "hors ligne depuis HH:MM (duree)"
  (`/api/cameras/reachability` ajoute `health_ok` / `since`), et en tete de
  la section Historique de la fiche : etat, disponibilite 24 h / 7 j, nombre de
  coupures sur 7 j, derniere plaque lue (`GET /api/cameras/<id>/health`).
- Centrale d'alerte : type `camera_offline` ("Cameras - camera hors ligne",
  `camera_alerts.py`, branche dans `alert_engine.HANDLERS` comme les balises).
  Ne sonde rien, lit `cameras_health`. Params (JSON, defauts proposes par
  l'admin) : `after_min` 10, `sites`, `camera_ids` ([] = toutes),
  `include_cellular` true, `notify_back` false, `lapi_silence_min` 0 (LAPI
  qui repond sans lire de plaque depuis N min), `stale_min` 10 (releve plus
  vieille = tache arretee : AUCUNE alerte sur un etat perime). Une alerte par
  coupure (etat `cockpit_alert_engine_state._id = "camera:<slug>"`), dedup
  `camera-offline-<id>-<since>`, position depuis la couche `titan.cameras`.
  Mode `banner` conseille.
- Tests : `tests/test_cameras_health.py`.

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

- `GET /api/cameras` (TABLEAU d'appareils) est reserve aux admins (blueprint
  `cameras_bp`). Capture de la main courante (`/api/pcorg/camera-capture`) :
  `cameras.capturable_doc` (meme filtre que le selecteur, sinon 404) puis
  `cameras.capture_to`. Le selecteur de camera de la main courante (pcorg.js) lit
  `GET /api/pcorg/cameras` (app.py, droits "user" comme la capture) :
  `cameras.capture_camera_list()` = actives, marque pilotable, profil
  d'identifiants, hors piste, avec `lat`/`lon` pris dans la couche carto
  `cameras` (FeatureCollection, propriete `IP`). Vignettes :
  `/api/pcorg/cameras/<id>/snapshot`. Une camera absente de la carte reste
  proposee, apres les autres (ordre alphabetique).
  Avant les plus proches : les cameras dont un canal actif COUVRE la fiche
  (`views` de `capture_camera_list` = angles de vue dessines ; point a moins
  de `range` et dans `fov/2` de `az`, `camCoveringView` de pcorg.js), avec
  la pastille "Couvre la fiche" (+ "position de repos" pour une PTZ) et la
  vignette de CE canal ; le clic capture ce canal (`channel` envoye a
  `/api/pcorg/camera-capture`). Position : couche par IP, sinon position
  manuelle de la page Detection.
- `/api/cameras-list` (centrale d'alertes) ne liste que les appareils qui ont
  un `camera_path` (+ chemins LAPI) : sans chemin, aucun evenement n'arrive.
- Le recepteur (`E:\TITAN\production\PCA\SCRIPTS\ecoutehik2.py`, service
  NSSM `HikRecepteur`) est hors depot ; `cockpit_dispatch.py` y resout les
  noms de cameras via `cockpit_cameras.camera_path`.
