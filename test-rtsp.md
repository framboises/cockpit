# Flux vidéo live tablette → PC org → Qonify : déploiement et tests

Principe : **3 liens RTSP fixes** déclarés une seule fois dans Qonify. L'opérateur choisit une tablette sur la carte Cockpit (« Voir flux vidéo »), puis la destination : **PC org uniquement** (rien dans Qonify) ou **Caméra terrain 1, 2 ou 3** (un lien occupé peut être remplacé après confirmation). L'agent accepte ; la caméra de la tablette arrive dans la tuile Qonify choisie, sans rien reconfigurer.

```
Tablette Field (Chrome, HTTPS)
   │ WHIP  https://cockpit.lemans.org/webrtc/field-N/whip   (signalisation, via IIS)
   │ média UDP 8189 direct vers le serveur
   ▼
mediamtx (service Windows, E:\TITAN\production\mediamtx) ── RTSP 8554/TCP ──► Qonify (3 caméras fixes)
   │ WHEP + UDP 8189
   ▼
PC org (modale de la carte)

Chaque publication / lecture : mediamtx ──► http://127.0.0.1:4008/field/api/stream/auth (Cockpit)
```

Détail technique : `docs/claude/field.md`, section « Flux vidéo live ».

---

## 1. Déploiement (déjà installé : service, règle IIS, variables, jetons)

Ordre important (PowerShell admin) :

1. **Redémarrer Cockpit** (nouvelle route d'autorisation, migration de l'index Mongo, choix de destination).
2. **Déployer la config mediamtx** :
   ```powershell
   pwsh -File E:\TITAN\production\cockpit\scripts\deploy_mediamtx.ps1
   ```
   Attendu : `Service mediamtx : Running` puis `API mediamtx OK, paths : field-1, field-2, field-3`.
3. Les tablettes récupèrent le nouveau `field.js` (service worker v47) au prochain chargement de `/field` : fermer et rouvrir l'appli sur chaque tablette.

Retour arrière : `E:\TITAN\production\mediamtx\mediamtx.yml.bak-<date>` puis `Restart-Service mediamtx`.

Coupure d'urgence (incident RGPD) : `Stop-Service mediamtx`.

---

## 2. Tests, dans l'ordre

### Test 1 : autorisation (sur le serveur, sans tablette)

```powershell
$u = "http://127.0.0.1:4008/field/api/stream/auth?k=" + [Environment]::GetEnvironmentVariable("MEDIAMTX_AUTH_HMAC_KEY","Machine")
Invoke-WebRequest -Method Post -Uri $u -ContentType "application/json" -Body '{"action":"read","path":"field-1","query":"token=faux"}' -SkipHttpErrorCheck | Select-Object StatusCode
```
Attendu : `403`. Puis dans VLC sur le serveur : `rtsp://127.0.0.1:8554/field-1?token=faux` → refusé (401) ; avec le vrai jeton (admin Field, carte de l'emplacement) → « pas de flux » (404), normal tant que personne ne publie.

### Test 1 bis : mire de test (sans tablette, sans ouverture UDP)

Prérequis une fois : `ffmpeg.exe` dans `E:\TITAN\production\mediamtx\` (build Windows « essentials » de gyan.dev, seul le fichier `bin\ffmpeg.exe` est utile), puis redémarrer Cockpit.

- Admin Field → emplacement libre → **Mire de test**.
- VLC sur le serveur → lien `rtsp://127.0.0.1:8554/field-1?token=<jeton 1>` → mire colorée avec compteur qui défile et « Camera terrain 1 - mire Cockpit ».
- Une fois le TCP 8554 ouvert : même chose dans Qonify, avec le lien en 192.168.254.35. Valide toute la partie Qonify avant même que les tablettes passent.
- **Couper** sur l'emplacement : l'image disparaît. Sinon arrêt seul au bout de 15 min.
- Rien ne s'affiche : `E:\TITAN\production\mediamtx\mire-field-1.log`.

### Test 2 : tablette sur le Wi-Fi du site → modale PC org

- Carte Cockpit → marker de la tablette → « Voir flux vidéo ».
- Tablette : modale « Flux vidéo demandé » en moins de 5 s → Accepter (autoriser la caméra la 1re fois).
- PC org : l'image arrive en moins de 3 s, en-tête « Caméra terrain 1 - <tablette> ».
- Laisser tourner **3 minutes** : le flux ne doit plus s'arrêter seul (c'était le bug, coupure vers 1-2 min).

Si la modale reste sur « en attente » alors que la tablette affiche « EN DIRECT » : dans Mongo, `field_streams` du flux. `publish_auth_at` posé = la tablette a bien joint mediamtx, c'est l'**UDP 8189** qui ne passe pas (voir section 3).

### Test 3 : VLC = Qonify (stabilité du lien)

Sur un poste du réseau de Qonify, VLC → Ouvrir un flux réseau → lien de « Caméra terrain 1 » (admin Field, clic sur le lien = copie).

- Tablette A diffuse sur l'emplacement 1 → VLC affiche l'image (tu peux avoir à relancer la lecture dans VLC ; Qonify, lui, réessaie seul).
- PC org → Arrêter : l'image disparaît **immédiatement** de VLC.
- Tablette B → « Voir flux vidéo » → elle prend l'emplacement 1 → VLC (relancé) affiche B avec **le même lien**.

C'est le test qui valide l'idée des 3 liens fixes.

### Test 4 : codec

Dans VLC : Outils → Informations sur le codec → doit afficher **H264**. Si c'est VP8, la tablette n'a pas d'encodeur H264 (console de la tablette : « H264 indisponible ») : Qonify ne pourra pas lire ce modèle de tablette.

### Test 5 : emplacements pleins, refus, arrêt automatique

- 3 tablettes en diffusion, une 4e demandée → « Tous les flux sont occupés » avec bouton Couper.
- Refus sur la tablette → la modale PC org affiche « Refusé par l'agent terrain ».
- Laisser un flux 5 min → arrêt automatique sur la tablette et sur le PC org.

### Test 5 bis : choix de la destination

- « Voir flux vidéo » → **PC org uniquement** : image dans la modale, pied « PC org uniquement, pas envoyé dans Qonify » ; aucun emplacement occupé dans l'admin Field ; VLC sur les 3 liens : rien.
- « Voir flux vidéo » → **Caméra terrain 2** alors que 1 est libre : le flux va bien sur le 2.
- Choisir un emplacement occupé → confirmation « Remplacer X ? » → l'ancien flux s'arrête, le nouveau prend la tuile.

### Test 6 : coupure brutale

Pendant une diffusion, couper le Wi-Fi de la tablette. Dans les ~30 à 60 s, l'emplacement repasse « Libre » dans l'admin Field et la modale PC org affiche « Flux terminé ».

### Test 7 : tablette en 4G (hors Wi-Fi du site)

Même chose que le test 2 avec une tablette en 4G. Dépend de la redirection UDP 8189 sur l'IP publique (section 3).

### Test 8 : Qonify

Déclarer les 3 caméras (section 4), refaire le test 3 avec le mur Qonify, mesurer la latence (main devant la tablette), cible ≤ 2 s.

---

## 3. Réseau : demandes aux administrateurs

Serveur Cockpit : **192.168.254.35** (carte DMZ, seule active ; la carte VLAN24 10.24.0.251 est déconnectée au 13/10/2026), nom public `cockpit.lemans.org` = **89.127.253.6**. Pare-feu Windows du serveur désactivé : rien à ouvrir localement. Le serveur ne sait pas se joindre par son IP publique (pas de hairpin NAT) : tout ce qui est interne doit viser une IP interne.

| # | Flux | Source → destination | Port | Pour |
|---|---|---|---|---|
| 1 | Qonify lit les 3 flux | 10.34.0.1 (Qonify) → 192.168.254.35 | **TCP 8554** | Liens RTSP fixes |
| 2 | Média WebRTC tablettes Wi-Fi | VLAN Wi-Fi des tablettes → 192.168.254.35 | **UDP 8189** | Image tablette (le HTTPS passe déjà) |
| 3 | Média WebRTC tablettes 4G | Internet → 89.127.253.6, NAT vers 192.168.254.35 (même destination que le 443) | **UDP 8189**, port conservé 8189 → 8189 | Tablettes hors site |
| 4 | Média WebRTC postes PC org | VLAN des postes → 192.168.254.35 | **UDP 8189** | Modale PC org |

Vérifier sur le serveur que l'UDP arrive (PowerShell admin, pendant un essai tablette) : `pktmon filter add UDP8189 -t UDP -p 8189; pktmon start --capture --counters-only`, puis `pktmon counters`, puis `pktmon stop; pktmon filter remove`. Rx à 0 sur la carte DMZ = rien n'arrive jusqu'au serveur (NAT / pare-feu amont).

Questions à leur poser :

- Sur quel réseau est Qonify, et quelle IP du serveur Cockpit voit-il ? C'est cette IP qui va dans les liens RTSP (pas `cockpit.lemans.org`, qui renvoie l'IP publique). Option : DNS interne qui résout `cockpit.lemans.org` en IP interne (split DNS).
- Les tablettes en Wi-Fi sortent-elles sur Internet par 89.127.253.6 ? Si oui, elles ne peuvent joindre le serveur qu'en interne (point 2).
- Ne **pas** publier 8554 sur Internet (inutile). 8889 et 9997 restent locaux au serveur.

Une fois l'IP vue par Qonify confirmée, la poser dans Cockpit pour que l'admin Field affiche les bons liens, puis redémarrer Cockpit :
```powershell
[Environment]::SetEnvironmentVariable("MEDIAMTX_RTSP_BASE", "rtsp://192.168.254.35:8554", "Machine")
```

---

## 4. Configuration Qonify (une seule fois)

Validé le 13/10/2026 dans HxGN dC3 Video 9.2 :

- Créer la caméra : hôte `192.168.254.35`, port `8554`, pilote **Generic video** (pas ONVIF, pas de pilote constructeur).
- Flux vidéo → Éditer : type **RTSP** (pas Motion JPEG), champ **URL** = l'URL complète avec le jeton (tableau ci-dessous), pas d'autorisation à cocher.
- C'est le **gestionnaire de périphériques** (DeviceManager, 10.34.0.10 à .18) qui lit le flux, pas 10.34.0.1 : c'est son IP qui doit passer le pare-feu en TCP 8554.
- Stormshield : une règle créée sans le **mode IPS** n'est pas appliquée complètement (connexions ouvertes puis coupées, ou rien du tout). Vérifier le mode de la règle si VLC marche depuis un poste admin mais pas depuis le réseau vidéo.

| Caméra Qonify | URL RTSP |
|---|---|
| Caméra terrain 1 | `rtsp://<IP serveur>:8554/field-1?token=<jeton 1>` |
| Caméra terrain 2 | `rtsp://<IP serveur>:8554/field-2?token=<jeton 2>` |
| Caméra terrain 3 | `rtsp://<IP serveur>:8554/field-3?token=<jeton 3>` |

Jetons : affichés dans l'admin Field (carte de chaque emplacement), aussi dans `E:\TITAN\production\mediamtx\secrets.txt`. Si Qonify refuse un `?` dans l'URL : URL sans `?token=...`, utilisateur quelconque (`qonify`), **mot de passe = jeton**. Transport RTSP : TCP. Ne jamais changer les jetons en cours d'événement.

Quand aucune tablette ne diffuse sur un emplacement, Qonify voit la caméra « hors ligne » et réessaie seul : c'est normal.

---

## 5. Dépannage

| Symptôme | Cause probable | Action |
|---|---|---|
| Tablette : « Flux impossible : whip_401/403 » | Cockpit refuse la publication | Log Cockpit `field: mediamtx publish refuse (...)` |
| Tout est refusé (401) même avec le bon jeton | Clé différente entre mediamtx et Cockpit, ou Cockpit pas redémarré | Relancer `deploy_mediamtx.ps1` après redémarrage de Cockpit |
| « EN DIRECT » sur la tablette, rien sur PC org / VLC, `publish_auth_at` posé | UDP 8189 bloqué entre la tablette et le serveur | Section 3, points 2 ou 3 |
| Journal mediamtx : `is publishing` puis, pour le lecteur, `deadline exceeded while waiting connection` | Le poste qui regarde (PC org) ne joint pas 192.168.254.35 en UDP 8189 | Section 3, point 4 (cause du faux « problème iPhone » du 13/10/2026) |
| VLC / Qonify « connexion impossible » | TCP 8554 bloqué ou mauvaise IP | Tester depuis le serveur `rtsp://127.0.0.1:8554/...` |
| Qonify noir alors que la modale PC org marche | Codec VP8 | Test 4 |
| `502` sur `/webrtc/` | mediamtx arrêté | `Get-Service mediamtx`, `E:\TITAN\production\mediamtx\stdout.log` |
