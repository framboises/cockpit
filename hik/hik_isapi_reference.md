# Hikvision ISAPI - Reference complete

Reference des endpoints ISAPI pour les cameras Hikvision.
Authentification : **HTTP Digest Auth** (login/password).
Base URL : `http://<IP>:<PORT>`

Modele teste : **DS-2DE7A432IW-AEB** (Speed Dome H8, firmware V5.8.13)

---

## 1. SYSTEME

| Action | Methode | Endpoint | Body |
|--------|---------|----------|------|
| Infos camera | GET | `/ISAPI/System/deviceInfo` | - |
| Heure systeme | GET | `/ISAPI/System/time` | - |
| Modifier l'heure | PUT | `/ISAPI/System/time` | XML `<Time>` |
| Sync NTP | PUT | `/ISAPI/System/time/ntpServers` | XML |
| Redemarrer | PUT | `/ISAPI/System/reboot` | - |
| Restaurer config usine | PUT | `/ISAPI/System/factoryReset` | - |
| Logs systeme | GET | `/ISAPI/ContentMgmt/logSearch` | - |
| Capacites systeme | GET | `/ISAPI/System/capabilities` | - |
| Status systeme | GET | `/ISAPI/System/status` | - |
| Upgrade firmware | PUT | `/ISAPI/System/updateFirmware` | firmware binaire |
| Exporter config | GET | `/ISAPI/System/configurationData` | - |
| Importer config | PUT | `/ISAPI/System/configurationData` | fichier config |

---

## 2. PTZ - CONTROLE DE MOUVEMENT

### 2.1 Mouvement continu
| Action | Methode | Endpoint | Body XML |
|--------|---------|----------|----------|
| Mouvement continu | PUT | `/ISAPI/PTZCtrl/channels/<ch>/continuous` | `<PTZData><pan>[-100..100]</pan><tilt>[-100..100]</tilt><zoom>[-100..100]</zoom></PTZData>` |
| Stopper | PUT | `/ISAPI/PTZCtrl/channels/<ch>/continuous` | `<PTZData><pan>0</pan><tilt>0</tilt><zoom>0</zoom></PTZData>` |

Valeurs :
- `pan` : negatif = gauche, positif = droite
- `tilt` : negatif = bas, positif = haut
- `zoom` : negatif = dezoom, positif = zoom

### 2.2 Mouvement absolu
| Action | Methode | Endpoint | Body XML |
|--------|---------|----------|----------|
| Position absolue | PUT | `/ISAPI/PTZCtrl/channels/<ch>/absolute` | `<PTZData><AbsoluteHigh><azimuth>[0-3600]</azimuth><elevation>[-900..900]</elevation><absoluteZoom>[10-400]</absoluteZoom></AbsoluteHigh></PTZData>` |

Valeurs (en dixiemes de degre) :
- `azimuth` : 0-3600 (0.0 a 360.0 degres)
- `elevation` : -900 a 900 (-90.0 a 90.0 degres)
- `absoluteZoom` : 10-400 (1.0x a 40.0x)

### 2.3 Mouvement relatif
| Action | Methode | Endpoint | Body XML |
|--------|---------|----------|----------|
| Position relative | PUT | `/ISAPI/PTZCtrl/channels/<ch>/relative` | `<PTZData><Relative><positionX>[-100..100]</positionX><positionY>[-100..100]</positionY><relativeZoom>[-100..100]</relativeZoom></Relative></PTZData>` |

### 2.4 Status PTZ
| Action | Methode | Endpoint |
|--------|---------|----------|
| Position actuelle | GET | `/ISAPI/PTZCtrl/channels/<ch>/status` |

---

## 3. PRESETS

| Action | Methode | Endpoint | Body XML |
|--------|---------|----------|----------|
| Lister les presets | GET | `/ISAPI/PTZCtrl/channels/<ch>/presets` | - |
| Aller a un preset | PUT | `/ISAPI/PTZCtrl/channels/<ch>/presets/<id>/goto` | - |
| Sauver un preset | PUT | `/ISAPI/PTZCtrl/channels/<ch>/presets/<id>` | `<PTZPreset><id>N</id><presetName>nom</presetName><enabled>true</enabled></PTZPreset>` |
| Supprimer un preset | DELETE | `/ISAPI/PTZCtrl/channels/<ch>/presets/<id>` | - |

### Presets speciaux Hikvision (en lecture seule)
| Preset | Fonction |
|--------|----------|
| 33 | Auto-flip (demi-tour) |
| 34 | Retour a l'origine |
| 35-38 | Lancer patrouille 1-4 |
| 39 | Mode jour |
| 40 | Mode nuit |
| 41-44 | Lancer pattern 1-4 |
| 45 | One-touch patrol |
| 46 | Mode jour/nuit auto |
| 47 | Lumiere alarme ON |
| 48 | Lumiere alarme OFF |
| 50 | Desembuage (1 cycle) |
| 90 | Wiper |
| 92 | Definir limites manuelles |
| 93 | Sauver limites manuelles |
| 94 | Redemarrage distant |
| 95 | Appeler menu OSD |
| 96 | Stopper un scan |
| 97 | Scan aleatoire |
| 98 | Scan cadre |
| 99 | Auto scan |
| 100 | Scan inclinaison |
| 101 | Scan panoramique |
| 102-105 | Lancer patrouille 5-8 |

---

## 4. PATROUILLES

| Action | Methode | Endpoint |
|--------|---------|----------|
| Lister les patrouilles | GET | `/ISAPI/PTZCtrl/channels/<ch>/patrols` |
| Detail d'une patrouille | GET | `/ISAPI/PTZCtrl/channels/<ch>/patrols/<id>` |
| Lancer une patrouille | PUT | `/ISAPI/PTZCtrl/channels/<ch>/patrols/<id>/start` |
| Stopper une patrouille | PUT | `/ISAPI/PTZCtrl/channels/<ch>/patrols/<id>/stop` |
| Creer/modifier patrouille | PUT | `/ISAPI/PTZCtrl/channels/<ch>/patrols/<id>` |
| Supprimer une patrouille | DELETE | `/ISAPI/PTZCtrl/channels/<ch>/patrols/<id>` |

Body XML pour creer une patrouille :
```xml
<PTZPatrol>
  <id>1</id>
  <patrolName>Ronde nuit</patrolName>
  <enabled>true</enabled>
  <PatrolSequence>
    <PatrolPoint>
      <id>1</id>
      <presetID>1</presetID>
      <speed>50</speed>
      <dwelltime>30</dwelltime>
    </PatrolPoint>
    <PatrolPoint>
      <id>2</id>
      <presetID>3</presetID>
      <speed>50</speed>
      <dwelltime>20</dwelltime>
    </PatrolPoint>
  </PatrolSequence>
</PTZPatrol>
```

---

## 5. PATTERNS (enregistrement de trajectoire)

| Action | Methode | Endpoint |
|--------|---------|----------|
| Lister les patterns | GET | `/ISAPI/PTZCtrl/channels/<ch>/patterns` |
| Demarrer l'enregistrement | PUT | `/ISAPI/PTZCtrl/channels/<ch>/patterns/<id>/start` |
| Stopper l'enregistrement | PUT | `/ISAPI/PTZCtrl/channels/<ch>/patterns/<id>/stop` |
| Rejouer le pattern | PUT | `/ISAPI/PTZCtrl/channels/<ch>/patterns/<id>/run` |

---

## 6. AUXILIAIRES (wiper, lumiere, chauffage)

| Action | Methode | Endpoint | Body XML |
|--------|---------|----------|----------|
| Commande auxiliaire | PUT | `/ISAPI/PTZCtrl/channels/<ch>/auxcontrols/1` | voir ci-dessous |

Actions disponibles (champ `<auxiliaryAction>`) :
| Action | Description |
|--------|-------------|
| `wiper` | Essuie-glace (1 cycle) |
| `lightOn` | Allumer lumiere supplementaire |
| `lightOff` | Eteindre lumiere supplementaire |
| `heaterOn` | Allumer chauffage |
| `heaterOff` | Eteindre chauffage |

```xml
<AuxCtrl>
  <auxiliaryAction>wiper</auxiliaryAction>
</AuxCtrl>
```

---

## 7. LIMITES PTZ

| Action | Methode | Endpoint |
|--------|---------|----------|
| Lire les limites | GET | `/ISAPI/PTZCtrl/channels/<ch>/PTZLimits` |
| Configurer les limites | PUT | `/ISAPI/PTZCtrl/channels/<ch>/PTZLimits` |

---

## 8. HOME POSITION

| Action | Methode | Endpoint |
|--------|---------|----------|
| Lire la position home | GET | `/ISAPI/PTZCtrl/channels/<ch>/homePosition` |
| Definir la position home | PUT | `/ISAPI/PTZCtrl/channels/<ch>/homePosition` |
| Aller a la position home | PUT | `/ISAPI/PTZCtrl/channels/<ch>/homePosition/goto` |

---

## 9. PARK ACTION (retour auto apres inactivite)

| Action | Methode | Endpoint |
|--------|---------|----------|
| Lire le park action | GET | `/ISAPI/PTZCtrl/channels/<ch>/parkAction` |
| Configurer park action | PUT | `/ISAPI/PTZCtrl/channels/<ch>/parkAction` |

```xml
<ParkAction>
  <enabled>true</enabled>
  <parkTime>30</parkTime>  <!-- secondes d'inactivite -->
  <actionType>preset</actionType>  <!-- preset, patrol, pattern, scan -->
  <actionID>1</actionID>  <!-- ID du preset/patrol/pattern -->
</ParkAction>
```

---

## 10. STREAMING VIDEO

| Action | Methode | Endpoint |
|--------|---------|----------|
| Flux RTSP principal | - | `rtsp://<IP>:554/Streaming/channels/<ch>01` |
| Flux RTSP secondaire | - | `rtsp://<IP>:554/Streaming/channels/<ch>02` |
| Flux MJPEG (HTTP) | GET | `/ISAPI/Streaming/channels/<ch>01/httpPreview` |
| Capture JPEG (snapshot) | GET | `/ISAPI/Streaming/channels/<ch>01/picture` |
| Config du stream | GET | `/ISAPI/Streaming/channels/<ch>01` |
| Modifier config stream | PUT | `/ISAPI/Streaming/channels/<ch>01` |

Pour `<ch>` = 1, les IDs stream sont :
- `101` = stream principal (main)
- `102` = stream secondaire (sub)
- `103` = stream tertiaire (si disponible)

---

## 11. IMAGE / AFFICHAGE

| Action | Methode | Endpoint |
|--------|---------|----------|
| Parametres image | GET | `/ISAPI/Image/channels/<ch>/color` |
| Modifier image | PUT | `/ISAPI/Image/channels/<ch>/color` |
| Mode jour/nuit | GET | `/ISAPI/Image/channels/<ch>/ircutFilter` |
| Changer mode jour/nuit | PUT | `/ISAPI/Image/channels/<ch>/ircutFilter` |
| OSD | GET | `/ISAPI/System/Video/inputs/channels/<ch>/overlays` |
| Modifier OSD | PUT | `/ISAPI/System/Video/inputs/channels/<ch>/overlays` |
| Privacy mask | GET | `/ISAPI/System/Video/inputs/channels/<ch>/privacyMask` |
| Focus | PUT | `/ISAPI/System/Video/inputs/channels/<ch>/focus` |
| Iris | PUT | `/ISAPI/System/Video/inputs/channels/<ch>/iris` |

Mode jour/nuit :
```xml
<IrcutFilter>
  <IrcutFilterType>day</IrcutFilterType>  <!-- day, night, auto -->
</IrcutFilter>
```

---

## 12. ENREGISTREMENT ET STOCKAGE

| Action | Methode | Endpoint |
|--------|---------|----------|
| Demarrer enregistrement | PUT | `/ISAPI/ContentMgmt/record/control/manual/start/tracks/<ch>` |
| Stopper enregistrement | PUT | `/ISAPI/ContentMgmt/record/control/manual/stop/tracks/<ch>` |
| Status carte SD | GET | `/ISAPI/ContentMgmt/Storage` |
| Formater carte SD | PUT | `/ISAPI/ContentMgmt/Storage/hdd/<id>/format` |
| Rechercher enregistrements | POST | `/ISAPI/ContentMgmt/search` |
| Telecharger enregistrement | GET | `/ISAPI/ContentMgmt/download` |

Recherche d'enregistrements (body POST) :
```xml
<CMSearchDescription>
  <searchID>unique-id</searchID>
  <trackIDList><trackID>101</trackID></trackIDList>
  <timeSpanList>
    <timeSpan>
      <startTime>2026-03-29T00:00:00Z</startTime>
      <endTime>2026-03-29T23:59:59Z</endTime>
    </timeSpan>
  </timeSpanList>
  <maxResults>50</maxResults>
  <searchResultPostion>0</searchResultPostion>
  <metadataList>
    <metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor>
  </metadataList>
</CMSearchDescription>
```

---

## 13. EVENEMENTS ET ALARMES

### 13.1 Configuration du serveur d'alarmes
| Action | Methode | Endpoint |
|--------|---------|----------|
| Lire config serveur alarme | GET | `/ISAPI/Event/notification/httpHosts` |
| Configurer serveur alarme | PUT | `/ISAPI/Event/notification/httpHosts` |

```xml
<HttpHostNotificationList>
  <HttpHostNotification>
    <id>1</id>
    <url>/</url>
    <protocolType>HTTP</protocolType>
    <parameterFormatType>XML</parameterFormatType>
    <addressingFormatType>ipaddress</addressingFormatType>
    <ipAddress>10.24.0.100</ipAddress>
    <portNo>8000</portNo>
    <httpAuthenticationMethod>none</httpAuthenticationMethod>
  </HttpHostNotification>
</HttpHostNotificationList>
```

### 13.2 Flux d'alertes (long-polling)
| Action | Methode | Endpoint |
|--------|---------|----------|
| Flux continu d'alertes | GET | `/ISAPI/Event/notification/alertStream` |

Cette requete reste ouverte et renvoie les alertes en temps reel (multipart MIME).
Alternative au mode push (Alarm Server).

### 13.3 Triggers d'evenements
| Action | Methode | Endpoint |
|--------|---------|----------|
| Lister les triggers | GET | `/ISAPI/Event/triggers` |
| Lire un trigger | GET | `/ISAPI/Event/triggers/<eventType>-<ch>` |
| Configurer un trigger | PUT | `/ISAPI/Event/triggers/<eventType>-<ch>` |

### 13.4 Evenements basiques
| Action | Methode | Endpoint |
|--------|---------|----------|
| Motion detection | GET/PUT | `/ISAPI/System/Video/inputs/channels/<ch>/motionDetection` |
| Video tampering | GET/PUT | `/ISAPI/System/Video/inputs/channels/<ch>/tamperDetection` |
| Entree alarme | GET/PUT | `/ISAPI/System/IO/inputs/<id>` |
| Sortie alarme | GET/PUT | `/ISAPI/System/IO/outputs/<id>` |
| Declencher sortie alarme | PUT | `/ISAPI/System/IO/outputs/<id>/trigger` |
| Exceptions | GET/PUT | `/ISAPI/Event/triggers/exception` |

### 13.5 Smart Events (VCA)
| Action | Methode | Endpoint |
|--------|---------|----------|
| Intrusion de zone | GET/PUT | `/ISAPI/Smart/FieldDetection/<ch>` |
| Franchissement de ligne | GET/PUT | `/ISAPI/Smart/LineDetection/<ch>` |
| Entree de zone | GET/PUT | `/ISAPI/Smart/RegionEntrance/<ch>` |
| Sortie de zone | GET/PUT | `/ISAPI/Smart/RegionExiting/<ch>` |
| Objet abandonne | GET/PUT | `/ISAPI/Smart/UnattendedBaggage/<ch>` |
| Retrait d'objet | GET/PUT | `/ISAPI/Smart/ObjectRemoval/<ch>` |
| Flaneuse | GET/PUT | `/ISAPI/Smart/LoiterDetection/<ch>` |
| Attroupement | GET/PUT | `/ISAPI/Smart/PeopleGathering/<ch>` |
| Mouvement rapide | GET/PUT | `/ISAPI/Smart/FastMoving/<ch>` |
| Stationnement interdit | GET/PUT | `/ISAPI/Smart/ParkingDetection/<ch>` |
| Detection audio | GET/PUT | `/ISAPI/Smart/AudioDetection/<ch>` |
| Changement de scene | GET/PUT | `/ISAPI/Smart/SceneChangeDetection/<ch>` |
| Defocalisation | GET/PUT | `/ISAPI/Smart/DefocusDetection/<ch>` |
| Vibration | GET/PUT | `/ISAPI/Smart/VibrationDetection/<ch>` |

### 13.6 Detection multi-cibles
| Action | Methode | Endpoint |
|--------|---------|----------|
| Config detection | GET/PUT | `/ISAPI/Smart/mixedTargetDetection/<ch>` |
| Comptage par zone | GET/PUT | `/ISAPI/Smart/regionTargetNumberCounting/<ch>` |

### 13.7 Visage
| Action | Methode | Endpoint |
|--------|---------|----------|
| Capture visage | GET/PUT | `/ISAPI/Smart/FaceDetect/<ch>` |
| Comparaison visage | GET/PUT | `/ISAPI/Smart/FaceContrast/<ch>` |
| Bibliotheques visages | GET | `/ISAPI/Intelligent/FDLib` |
| Ajouter visage | POST | `/ISAPI/Intelligent/FDLib/<libID>/picture` |

### 13.8 Comptage de personnes
| Action | Methode | Endpoint |
|--------|---------|----------|
| Config comptage | GET/PUT | `/ISAPI/Smart/PeopleCounting/<ch>` |
| Donnees comptage | GET | `/ISAPI/Smart/PeopleCounting/<ch>/counting` |
| Reset compteurs | DELETE | `/ISAPI/Smart/PeopleCounting/<ch>/counting` |

### 13.9 Trafic routier
| Action | Methode | Endpoint |
|--------|---------|----------|
| Detection vehicule (ANPR) | GET/PUT | `/ISAPI/Traffic/channels/<ch>/vehicleDetect` |
| Liste blocklist/allowlist | GET | `/ISAPI/Traffic/channels/<ch>/searchLPListInfo` |
| Ajouter plaque a une liste | POST | `/ISAPI/Traffic/channels/<ch>/licensePlateList` |

### 13.10 Thermometrie / Incendie
| Action | Methode | Endpoint |
|--------|---------|----------|
| Detection incendie | GET/PUT | `/ISAPI/Smart/FireDetection/<ch>` |
| Thermometrie | GET/PUT | `/ISAPI/Thermal/channels/<ch>/thermometry` |

---

## 14. RESEAU

| Action | Methode | Endpoint |
|--------|---------|----------|
| Config TCP/IP | GET/PUT | `/ISAPI/System/Network/interfaces` |
| Config DNS | GET/PUT | `/ISAPI/System/Network/dns` |
| Config NTP | GET/PUT | `/ISAPI/System/time/ntpServers` |
| Config DDNS | GET/PUT | `/ISAPI/System/Network/DDNS` |
| Config FTP | GET/PUT | `/ISAPI/System/Network/ftp` |
| Config Email | GET/PUT | `/ISAPI/System/Network/mailing` |
| Config RTSP | GET/PUT | `/ISAPI/Streaming/channels/<ch>01` |
| Config ports HTTP/HTTPS | GET/PUT | `/ISAPI/Security/adminAccesses` |
| Config SNMP | GET/PUT | `/ISAPI/System/Network/SNMP` |
| Utilisateurs en ligne | GET | `/ISAPI/Security/onlineUser` |

---

## 15. SECURITE

| Action | Methode | Endpoint |
|--------|---------|----------|
| Lister les utilisateurs | GET | `/ISAPI/Security/users` |
| Creer un utilisateur | POST | `/ISAPI/Security/users` |
| Modifier un utilisateur | PUT | `/ISAPI/Security/users/<id>` |
| Supprimer un utilisateur | DELETE | `/ISAPI/Security/users/<id>` |
| Filtre IP | GET/PUT | `/ISAPI/Security/ipAddrFilter` |
| Filtre MAC | GET/PUT | `/ISAPI/Security/macAddrFilter` |
| Config HTTPS | GET/PUT | `/ISAPI/Security/adminAccesses` |
| Certificats | GET | `/ISAPI/Security/certificates` |

---

## 16. AUDIO BIDIRECTIONNEL

| Action | Methode | Endpoint |
|--------|---------|----------|
| Ouvrir canal audio | PUT | `/ISAPI/System/TwoWayAudio/channels/<ch>/open` |
| Fermer canal audio | PUT | `/ISAPI/System/TwoWayAudio/channels/<ch>/close` |
| Envoyer audio | PUT | `/ISAPI/System/TwoWayAudio/channels/<ch>/audioData` |

---

## 17. CAPACITES DE LA CAMERA

| Action | Methode | Endpoint |
|--------|---------|----------|
| Capacites globales | GET | `/ISAPI/System/capabilities` |
| Capacites PTZ | GET | `/ISAPI/PTZCtrl/channels/<ch>/capabilities` |
| Capacites streaming | GET | `/ISAPI/Streaming/channels/<ch>01/capabilities` |
| Capacites Smart/VCA | GET | `/ISAPI/Smart/capabilities` |
| Capacites evenements | GET | `/ISAPI/Event/capabilities` |
| Capacites image | GET | `/ISAPI/Image/channels/<ch>/capabilities` |

---

## EXEMPLES CURL

### Infos camera
```bash
curl --digest -u admin:ACOcamip72 http://10.34.1.63/ISAPI/System/deviceInfo
```

### Aller au preset 3 (Bugatti)
```bash
curl --digest -u admin:ACOcamip72 -X PUT http://10.34.1.63/ISAPI/PTZCtrl/channels/1/presets/3/goto
```

### Capturer une image
```bash
curl --digest -u admin:ACOcamip72 http://10.34.1.63/ISAPI/Streaming/channels/101/picture -o capture.jpg
```

### Mouvement continu vers la gauche
```bash
curl --digest -u admin:ACOcamip72 -X PUT \
  -H "Content-Type: application/xml" \
  -d '<PTZData><pan>-50</pan><tilt>0</tilt><zoom>0</zoom></PTZData>' \
  http://10.34.1.63/ISAPI/PTZCtrl/channels/1/continuous
```

### Stopper le mouvement
```bash
curl --digest -u admin:ACOcamip72 -X PUT \
  -H "Content-Type: application/xml" \
  -d '<PTZData><pan>0</pan><tilt>0</tilt><zoom>0</zoom></PTZData>' \
  http://10.34.1.63/ISAPI/PTZCtrl/channels/1/continuous
```

### Configurer le serveur d'alarmes
```bash
curl --digest -u admin:ACOcamip72 -X PUT \
  -H "Content-Type: application/xml" \
  -d '<HttpHostNotificationList>
  <HttpHostNotification>
    <id>1</id>
    <url>/tunnel-annexe</url>
    <protocolType>HTTP</protocolType>
    <parameterFormatType>XML</parameterFormatType>
    <addressingFormatType>ipaddress</addressingFormatType>
    <ipAddress>10.24.0.100</ipAddress>
    <portNo>8000</portNo>
    <httpAuthenticationMethod>none</httpAuthenticationMethod>
  </HttpHostNotification>
</HttpHostNotificationList>' \
  http://10.34.1.63/ISAPI/Event/notification/httpHosts
```

### Lire la config de la parking detection
```bash
curl --digest -u admin:ACOcamip72 http://10.34.1.63/ISAPI/Smart/ParkingDetection/1
```

### Flux d'alertes en continu (long-polling)
```bash
curl --digest -u admin:ACOcamip72 --no-buffer http://10.34.1.63/ISAPI/Event/notification/alertStream
```

---

## NOTES

- `<ch>` = numero de channel (generalement `1`)
- Authentification : toujours `--digest` (pas Basic)
- Les reponses sont en XML avec namespace `http://www.hikvision.com/ver20/XMLSchema`
- Les erreurs renvoient un XML `<ResponseStatus>` avec `<statusCode>` et `<statusString>`
- Pour connaitre les fonctions supportees par ta camera, utilise les endpoints `/capabilities`
- Certains endpoints ne sont disponibles que si la fonction VCA correspondante est activee
