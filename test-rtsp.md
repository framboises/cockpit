# Test & déploiement du flux vidéo live (Windows Server 2022)

Ce guide décrit, dans l'ordre, **tout ce qu'il faut faire sur le serveur de production Windows Server 2022** pour activer la fonctionnalité "Flux vidéo live tablette → PC org → Qonify", puis la batterie de tests à dérouler.

L'architecture cible :

```
Tablette Field (Android Chrome, HTTPS)
        │  WHIP (WebRTC ingest)
        ▼
mediamtx (Windows service)  ─── RTSP 8554 ──►  Qonify (3 caméras pré-config)
        │  WHEP (WebRTC playback)
        ▼
PC org (navigateur, modale viewer)
```

**3 slots fixes** (`field-1`, `field-2`, `field-3`) avec view tokens stables. Qonify est configuré **une seule fois** avec 3 URLs RTSP qui ne changent jamais.

---

## Question préalable : quel domaine pour le WebRTC ?

Le WebRTC (WHIP/WHEP) côté tablette **exige du HTTPS valide**. Trois options possibles, par ordre de simplicité :

### Option A — Réutiliser `cockpit.lemans.org` avec un préfixe de path (recommandé)

C'est **l'option zéro friction** : aucun nouveau DNS, aucun nouveau certificat. On ajoute juste deux routes au reverse proxy qui sert déjà Cockpit aujourd'hui (IIS / nginx / Caddy / etc), qui rerouteront vers mediamtx en local.

URLs résultantes :
- WHIP/WHEP/HLS via : `https://cockpit.lemans.org/webrtc/field-N/...`
- RTSP via : `rtsp://cockpit.lemans.org:8554/field-N?token=...` (RTSP n'a pas besoin de HTTPS, juste du DNS qui existe déjà)

**Avantage** : aucune action SSL. Le cert existant `cockpit.lemans.org` est réutilisé.

### Option B — Sous-domaine **frère** `cockpit-media.lemans.org`

Ajouter un enregistrement A `cockpit-media.lemans.org` → IP serveur. Si le cert existant est wildcard `*.lemans.org`, **il couvre déjà ce sous-domaine** (car c'est un seul niveau sous `lemans.org`).

**Avantage** : isolation propre du media server. Reverse proxy dédié, plus de risque de collision avec les routes Cockpit.

### Option C — `media.cockpit.lemans.org` (sub-sub-domain) — **à éviter**

Sub-sub-domain : un wildcard `*.lemans.org` **ne le couvre pas** (les wildcards Let's Encrypt sont limités à un niveau). Il faudrait :
- soit demander un cert dédié pour `media.cockpit.lemans.org` (HTTP-01 OK, mais opération supplémentaire à renouveler chaque 90 j)
- soit mettre en place un wildcard `*.cockpit.lemans.org` via DNS-01 (nécessite l'API du registrar/zone DNS).

**Option C écartée** dans la suite de ce guide. Les sections supposent l'**Option A** (path-based) — elle fonctionne avec n'importe quelle infra reverse proxy déjà en place. Si tu veux Option B (sous-domaine frère), les commandes sont quasi identiques, il suffit d'ajuster le hostname.

---

## 0. Prérequis serveur

À vérifier avant de commencer :

| Item | Comment vérifier |
|---|---|
| Windows Server 2022 à jour | `winver` |
| PowerShell 7 (recommandé) | `pwsh --version` |
| Cockpit déjà fonctionnel sur le serveur, certificat HTTPS valide | URL `https://cockpit.lemans.org` accessible |
| Reverse proxy actif sur cockpit.lemans.org (IIS+ARR, nginx, Caddy ou autre) | Identifier lequel |
| Ports ouverts dans le pare-feu : `8554/tcp` (RTSP direct, sortie VMS), `8189/udp` (WebRTC ICE par défaut mediamtx) | `Get-NetFirewallRule` |
| Le compte qui exécutera mediamtx peut écrire dans `C:\mediamtx\` | Test : `New-Item C:\mediamtx\test.txt` |

> ✓ **Pas besoin de nouveau DNS, pas de nouveau certificat** avec l'Option A (path-based). On greffe sur l'existant.
> ⚠️ La tablette ne pourra **pas** publier en WebRTC sans le HTTPS valide déjà en place sur `cockpit.lemans.org`. C'est un blocage navigateur incontournable.

---

## 1. Installer mediamtx (binaire Windows natif, sans Docker)

Sur Windows Server 2022, le plus simple est d'utiliser le binaire Windows officiel — pas besoin de Docker.

### 1.1. Télécharger mediamtx

```powershell
# PowerShell admin
$ver = "1.9.3"   # adapter à la dernière release : https://github.com/bluenviron/mediamtx/releases
$url = "https://github.com/bluenviron/mediamtx/releases/download/v$ver/mediamtx_v${ver}_windows_amd64.zip"
$dst = "C:\mediamtx"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Invoke-WebRequest -Uri $url -OutFile "$dst\mediamtx.zip"
Expand-Archive -Path "$dst\mediamtx.zip" -DestinationPath $dst -Force
Remove-Item "$dst\mediamtx.zip"
ls $dst   # doit contenir mediamtx.exe + mediamtx.yml par défaut
```

### 1.2. Remplacer la config par celle du repo

Copier `mediamtx.yml` du repo Cockpit (généré par le déploiement) vers `C:\mediamtx\mediamtx.yml`, en remplaçant le fichier d'exemple.

```powershell
# Adapter le chemin selon où le repo est cloné
Copy-Item "C:\inetpub\cockpit\mediamtx.yml" "C:\mediamtx\mediamtx.yml" -Force
```

### 1.3. Régler les hôtes ICE WebRTC (CRITIQUE)

Le signaling WHIP/WHEP passe par le reverse proxy en HTTPS, mais le **média lui-même** voyage en UDP directement entre la tablette et mediamtx. Il faut donc que mediamtx **annonce sa vraie IP publique** dans les ICE candidates, sinon la tablette en 4G ne pourra jamais se connecter.

Dans `C:\mediamtx\mediamtx.yml`, ajouter en haut (au-dessus de la section `webrtc:`) :

```yaml
# Hôtes/IPs annoncés dans les ICE candidates WebRTC.
# La tablette (4G ou Wi-Fi) doit pouvoir joindre ce hostname/IP en UDP 8189.
webrtcAdditionalHosts:
  - cockpit.lemans.org
```

Si la tablette est sur le même VLAN que le serveur (cas exceptionnel — réseau interne événement), ajouter aussi l'IP locale du serveur dans la liste pour éviter un aller-retour Internet inutile.

> ⚠️ Sans cette config, le test 3 (VLC RTSP) marchera car RTSP ne dépend pas d'ICE, mais le test 4 (modale PC org WebRTC) échouera silencieusement avec "no signal" en boucle.

### 1.4. Régler la clé HMAC partagée

Ouvrir `C:\mediamtx\mediamtx.yml`. Les hooks `runOnPublish/Read/Unpublish` contiennent `$$MEDIAMTX_AUTH_HMAC_KEY`. **Pour un déploiement Windows, le plus simple est de remplacer la variable par la valeur littérale** (curl Windows ne fait pas l'expansion shell de la même façon que sur Linux).

Générer une clé :

```powershell
$key = -join ((48..57) + (97..122) | Get-Random -Count 48 | ForEach-Object {[char]$_})
$key
# noter cette valeur, elle sera aussi mise dans la config Cockpit
```

Puis dans `mediamtx.yml`, remplacer toutes les occurrences de `$$MEDIAMTX_AUTH_HMAC_KEY` par la valeur générée. Exemple de hook après remplacement :

```yaml
runOnPublish: >
  curl -fsS -X POST
  -H "Content-Type: application/json"
  -H "X-Mediamtx-Auth: 7f3b9c8e2d4a1b6f8c0e9d7a2b4f6c8e1d3a5b7f"
  -d "{\"action\":\"publish\",\"path\":\"$MTX_PATH\",\"query\":\"$MTX_QUERY\"}"
  https://cockpit.lemans.org/field/api/stream/auth
```

> ⚠️ `curl.exe` est livré avec Windows 10/11 et Server 2019+. Vérifier : `curl --version`. Si absent, l'installer ou le mettre à `C:\Windows\System32\curl.exe`.

### 1.5. Tester mediamtx en mode interactif

```powershell
cd C:\mediamtx
.\mediamtx.exe
```

Vérifier dans la console que les 3 paths sont chargés :

```
INF [path field-1] runOnInit [...]
INF [RTSP] listener opened on :8554
INF [WebRTC] listener opened on :8889
INF [HLS] listener opened on :8888
```

Si tout est OK, `Ctrl+C` pour arrêter — on installera en service à l'étape 5.

---

## 2. Ajouter une route `/webrtc/*` au reverse proxy existant

L'**objectif** : que `https://cockpit.lemans.org/webrtc/<path mediamtx>` soit reroutée en local vers `http://127.0.0.1:8889/<path mediamtx>` (mediamtx WebRTC).

Adapter selon le reverse proxy déjà en place sur la machine. Ci-dessous les 3 cas courants.

### Cas A — IIS + URL Rewrite + ARR (Application Request Routing)

C'est l'option par défaut sur Windows Server. Si IIS sert déjà `cockpit.lemans.org`, il reste à ajouter une règle de rewrite.

**Prérequis IIS** :
- ARR installé (`https://www.iis.net/downloads/microsoft/application-request-routing`)
- URL Rewrite installé (généralement déjà là)
- ARR Proxy activé : ouvrir IIS Manager → sélectionner le serveur (racine) → "Application Request Routing Cache" → "Server Proxy Settings" → cocher "Enable proxy"

**Ajouter la règle** dans `web.config` du site `cockpit.lemans.org` (ou via IIS Manager → URL Rewrite → Add Rule → Reverse Proxy) :

```xml
<system.webServer>
  <rewrite>
    <rules>
      <rule name="Mediamtx WebRTC reverse proxy" stopProcessing="true">
        <match url="^webrtc/(.*)" />
        <action type="Rewrite" url="http://127.0.0.1:8889/{R:1}" />
      </rule>
      <!-- ... règles existantes Cockpit ... -->
    </rules>
  </rewrite>
</system.webServer>
```

Recharger IIS : `iisreset` (ou `Restart-WebAppPool`).

Tester :
```powershell
curl -I https://cockpit.lemans.org/webrtc/
# attendu : retour 200 ou 404 venant de mediamtx (pas de 502 = bon signe)
```

### Cas B — Caddy

Si Cockpit est servi par Caddy, il suffit d'ajouter un bloc `handle_path` dans le `Caddyfile` du domaine existant :

```caddy
cockpit.lemans.org {
    # ... directives existantes Cockpit ...

    # mediamtx WebRTC (WHIP / WHEP)
    handle_path /webrtc/* {
        reverse_proxy 127.0.0.1:8889 {
            flush_interval -1
        }
    }
}
```

Recharger Caddy : `caddy reload --config C:\caddy\Caddyfile` (ou `Restart-Service caddy` si en service NSSM).

### Cas C — nginx (Windows)

Dans le `server { }` qui sert `cockpit.lemans.org`, ajouter :

```nginx
location /webrtc/ {
    proxy_pass http://127.0.0.1:8889/;   # le slash final est important : strip /webrtc/
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;       # streaming WebRTC = pas de buffer
    proxy_request_buffering off;
}
```

Recharger nginx : `nginx -s reload`.

### Vérification (quel que soit le proxy)

Avant d'aller plus loin, valider que la chaîne reverse proxy → mediamtx fonctionne :

```powershell
# Sur n'importe quel poste
curl -I https://cockpit.lemans.org/webrtc/
# attendu : HTTP/1.1 200 ou 404 (selon ce que retourne mediamtx sur la racine)
# PAS attendu : 502 Bad Gateway, 504 Gateway Timeout, 404 du proxy lui-même
```

Si OK : on a bien `https://cockpit.lemans.org/webrtc/...` qui atteint mediamtx via le proxy. **Pas de cert à renouveler, pas de DNS à ajouter.**

---

## 3. Variables d'environnement Cockpit

Cockpit (Flask) a besoin de connaître les URLs mediamtx + les view tokens + la clé HMAC. Sur Windows, le plus propre est de poser les variables au niveau du service Windows qui lance Cockpit.

### 3.1. Générer les view tokens (3 valeurs stables)

```powershell
1..3 | ForEach-Object {
    $tok = -join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
    "Token slot $_ : $tok"
}
```

**Conserver ces 3 tokens** : ils seront mis dans Cockpit ET dans Qonify (URL RTSP). Une fois en prod, ne **jamais les changer en cours d'événement**.

### 3.2. Définir les variables d'env machine

```powershell
# Powershell admin
[System.Environment]::SetEnvironmentVariable(
    "FIELD_STREAM_VIEW_TOKENS",
    "tok1,tok2,tok3",   # remplacer par les valeurs générées au 3.1
    "Machine"
)
[System.Environment]::SetEnvironmentVariable(
    "FIELD_STREAM_SLOTS", "3", "Machine"
)
[System.Environment]::SetEnvironmentVariable(
    "FIELD_STREAM_MAX_DURATION_S", "300", "Machine"
)
[System.Environment]::SetEnvironmentVariable(
    "MEDIAMTX_BASE_URL", "https://cockpit.lemans.org/webrtc", "Machine"
)
[System.Environment]::SetEnvironmentVariable(
    "MEDIAMTX_RTSP_BASE", "rtsp://cockpit.lemans.org:8554", "Machine"
)
[System.Environment]::SetEnvironmentVariable(
    "MEDIAMTX_AUTH_HMAC_KEY",
    "7f3b9c8e2d4a1b6f8c0e9d7a2b4f6c8e1d3a5b7f",   # MÊME valeur qu'en 1.3
    "Machine"
)
```

### 3.3. Redémarrer Cockpit

Selon comment Cockpit tourne (NSSM service, IIS, scheduled task, Task Scheduler) — **redémarrer pour qu'il relise les variables d'env**.

```powershell
# Si Cockpit tourne en service NSSM
Restart-Service cockpit
# Ou par process manager utilisé
```

---

## 4. Configurer Qonify (une seule fois)

Dans Qonify, ajouter **3 caméras RTSP** — utilisant le DNS Cockpit existant, port RTSP direct (pas via reverse proxy) :

| Nom dans Qonify | URL RTSP |
|---|---|
| Caméra terrain 1 | `rtsp://cockpit.lemans.org:8554/field-1?token=<TOK1>` |
| Caméra terrain 2 | `rtsp://cockpit.lemans.org:8554/field-2?token=<TOK2>` |
| Caméra terrain 3 | `rtsp://cockpit.lemans.org:8554/field-3?token=<TOK3>` |

Remplacer `<TOK1/2/3>` par les 3 view tokens générés au 3.1.

> ✓ RTSP n'utilise pas HTTPS, donc pas de certificat à gérer. Le DNS `cockpit.lemans.org` existe déjà.
> ✓ Pare-feu : laisser passer 8554/tcp en entrée depuis le réseau Qonify uniquement (restreindre par IP source si possible).

---

## 5. Installer mediamtx en service Windows (NSSM)

Pour qu'il survive à un reboot, utiliser **NSSM** (Non-Sucking Service Manager).

### 5.1. Télécharger NSSM

```powershell
Invoke-WebRequest "https://nssm.cc/release/nssm-2.24.zip" -OutFile "C:\nssm.zip"
Expand-Archive "C:\nssm.zip" -DestinationPath "C:\nssm" -Force
$nssm = "C:\nssm\nssm-2.24\win64\nssm.exe"
```

### 5.2. Service mediamtx

```powershell
& $nssm install mediamtx "C:\mediamtx\mediamtx.exe" "C:\mediamtx\mediamtx.yml"
& $nssm set mediamtx AppDirectory "C:\mediamtx"
& $nssm set mediamtx Start SERVICE_AUTO_START
& $nssm set mediamtx AppStdout "C:\mediamtx\stdout.log"
& $nssm set mediamtx AppStderr "C:\mediamtx\stderr.log"
& $nssm set mediamtx AppRotateFiles 1
& $nssm set mediamtx AppRotateBytes 10485760
Start-Service mediamtx
Get-Service mediamtx   # doit être Running
```

> ✓ **Pas de service supplémentaire à installer** : le reverse proxy existant (IIS / nginx / Caddy de Cockpit) est déjà un service Windows.

---

## 6. Configurer le pare-feu Windows

```powershell
# Powershell admin
New-NetFirewallRule -DisplayName "RTSP (Qonify)" -Direction Inbound -Protocol TCP -LocalPort 8554 -Action Allow
New-NetFirewallRule -DisplayName "WebRTC ICE (mediamtx)" -Direction Inbound -Protocol UDP -LocalPort 8189 -Action Allow
```

> ✓ Le port 8889 (WebRTC HTTP) est seulement appelé en local par le reverse proxy → ne pas l'exposer en externe.
> ✓ Le port 8888 (HLS) est optionnel, ne pas l'exposer si non utilisé.
> ✓ Les ports 80/443 sont déjà ouverts (Cockpit existant), rien à ajouter.
> ✓ Restreindre 8554/tcp en source par l'IP du serveur Qonify est une bonne pratique (paramètre `-RemoteAddress`).

---

## 7. Tests à dérouler (dans l'ordre, ne pas sauter)

### Test 1 — mediamtx démarré et reverse proxy OK

```powershell
# Sur le serveur lui-même : mediamtx local
Get-Content C:\mediamtx\stdout.log -Tail 20
# attendu : lignes "RTSP listener opened on :8554", "WebRTC listener opened on :8889"

# Reverse proxy : la route /webrtc/ traverse jusqu'à mediamtx
curl -I https://cockpit.lemans.org/webrtc/
# attendu : HTTP/1.1 200 ou 404 (réponse de mediamtx, pas du proxy)
# pas attendu : 502 / 504 (= proxy ne joint pas mediamtx)
```

✅ Critère de succès : services Running, reverse proxy traverse, mediamtx répond.

---

### Test 2 — Webhook auth Cockpit ↔ mediamtx (sans tablette)

Simuler ce que mediamtx enverra quand un stream démarre, et vérifier que Cockpit valide :

```powershell
# Ce stream n'existe pas encore donc Cockpit doit refuser → 403 attendu
curl -X POST `
  -H "Content-Type: application/json" `
  -H "X-Mediamtx-Auth: 7f3b9c8e2d4a1b6f8c0e9d7a2b4f6c8e1d3a5b7f" `
  -d '{"action":"read","path":"field-1","query":"token=fake"}' `
  https://cockpit.lemans.org/field/api/stream/auth
# attendu : {"ok":false,"error":"bad_view_token"} status 403
```

```powershell
# Maintenant avec un view_token valide (TOK1) → doit valider
curl -X POST `
  -H "Content-Type: application/json" `
  -H "X-Mediamtx-Auth: 7f3b9c8e2d4a1b6f8c0e9d7a2b4f6c8e1d3a5b7f" `
  -d '{"action":"read","path":"field-1","query":"token=<TOK1>"}' `
  https://cockpit.lemans.org/field/api/stream/auth
# attendu : {"ok":true} status 200
```

✅ Critère de succès : 403 puis 200 selon le token. Sinon → vérifier la clé HMAC, le sous-domaine et la config réseau.

---

### Test 3 — Stabilité d'URL RTSP avec VLC (test critique pour Qonify)

C'est **LE** test qui valide que le pool de slots fonctionne. Si ce test passe, Qonify marchera.

#### 3a. Configurer VLC

Sur un poste opérateur (ou sur le serveur via Remote Desktop), ouvrir VLC :
- `Média → Ouvrir un flux réseau`
- URL : `rtsp://cockpit.lemans.org:8554/field-1?token=<TOK1>`
- VLC affichera "no signal" (le slot est libre, c'est normal).

**Laisser VLC ouvert sur cette URL pendant tous les tests qui suivent.**

#### 3b. Demander un flux d'une tablette A

- Sur PC org (cockpit), aller sur la carte, cliquer le marker de la tablette A → bouton **"Voir flux vidéo"** (rouge).
- Sur la tablette A (Field) : la modale "Flux vidéo demandé" doit apparaître dans **moins de 5 secondes** → **Accepter**.
- Côté PC org : la modale viewer doit afficher la caméra de la tablette A en **moins de 2 secondes** avec le header **"Caméra terrain 1 — Tablette A"**.
- Côté VLC : l'image de la tablette A doit apparaître automatiquement.

✅ Critère : VLC affiche la même image que la modale PC org, sans aucune action sur VLC.

#### 3c. Couper, redemander d'une tablette B

- Sur la modale PC org → bouton **Arrêter**.
- Côté VLC : retour à "no signal" (normal, slot libéré).
- Sur la carte cockpit → marker de la tablette **B** → "Voir flux vidéo" → tablette B accepte.
- **VLC doit afficher l'image de la tablette B sans aucune intervention de l'opérateur VLC.**

✅ Critère : c'est la preuve que la stabilité d'URL fonctionne et que Qonify pourra suivre le slot quel que soit la tablette qui pousse. **Si ce test échoue, ne pas continuer avec Qonify — le pool est cassé.**

---

### Test 4 — Pool plein (3 streams simultanés)

- Demander 3 flux simultanés depuis 3 tablettes différentes (T1, T2, T3 toutes acceptent).
- Demander un 4e flux d'une tablette T4.
- Côté PC org : modale "**Tous les flux sont occupés**" doit apparaître avec la liste T1/T2/T3 et un bouton **Couper** sur chacune.
- Cliquer Couper sur T1 → re-demander T4 → doit fonctionner et T4 prend le slot 1.

✅ Critère : pas plus de 3 flux simultanés, message clair sur le 4e.

---

### Test 5 — Refus tablette + auto-stop

- Demander un flux sur tablette A → tablette A reçoit la modale → tape **Refuser**.
- Côté PC org : la modale viewer doit toaster "Refusé par l'agent" et se fermer dans les 2s.
- Re-demander un flux → tablette accepte → laisser tourner **5 minutes pile**.
- À 5:00 : la tablette doit auto-couper le flux (overlay LIVE disparaît, toast "Flux terminé (5 min)").
- Côté PC org : la modale doit aussi détecter la fin et se fermer.

✅ Critère : auto-stop strict à 5 min, refus géré proprement.

---

### Test 6 — Crash brutal tablette (libération de slot)

- Demander un flux → accepter.
- Sur la tablette : **fermer brutalement l'onglet ou couper le réseau Wi-Fi/4G**.
- Sur la page Field Dispatch admin (cockpit) : la pastille du slot doit passer à "libre" dans les **30 secondes max** (cooldown `FIELD_STREAM_STALE_GRACE_S`).

✅ Critère : pas de slot bloqué après crash → le webhook `runOnUnpublish` ou le GC lazy fonctionne.

---

### Test 7 — Audit RGPD / sécurité

À vérifier sur la tablette pendant publication :

```javascript
// DevTools console tablette
document.querySelector('.field-stream-overlay video').srcObject.getAudioTracks().length
// attendu : 0
```

✅ Critère : `0` (pas de track audio = pas de captation micro).

À vérifier dans les logs mediamtx (`C:\mediamtx\stdout.log`) :

- Aucune mention de `record:` ou de fichier MP4 créé pendant un stream.
- Vérifier qu'aucun fichier n'apparaît dans `C:\mediamtx\` ou ses sous-dossiers pendant un stream.

✅ Critère : aucun enregistrement disque côté media server.

---

### Test 8 — Test final Qonify

- Dans Qonify, ouvrir le mur avec les 3 caméras configurées au point 4.
- Demander un flux d'une tablette → doit apparaître sur "Caméra terrain N" automatiquement.
- Couper, redemander d'une autre tablette → la même caméra Qonify continue de recevoir.
- Mesurer la latence Qonify visuellement (mouvement de main devant la tablette → délai d'apparition à l'écran). Cible : ≤ 2 s.

✅ Critère opérationnel : Qonify fonctionne sans aucune reconfiguration.

---

## 8. Dépannage rapide

| Symptôme | Cause probable | Action |
|---|---|---|
| Tablette : "getUserMedia failed" | HTTPS pas valide sur `cockpit.lemans.org` ou cert expiré | `curl -I https://cockpit.lemans.org/webrtc/` doit répondre sans erreur TLS |
| `502 Bad Gateway` sur `/webrtc/` | mediamtx n'est pas démarré ou pas sur le bon port | `Get-Service mediamtx`, vérifier port 8889 ouvert localement (`netstat -an | Select-String 8889`) |
| `404 Not Found` sur `/webrtc/<path>/whip` | Règle de rewrite IIS / handle_path Caddy mal configurée | Logs reverse proxy. Le path doit être strippé de `/webrtc/` avant proxy |
| Modale PC org reste "en attente" indéfiniment | Pas de connectivité tablette ou polling pollFiches HS | DevTools tablette : voir si `pollFiches` reçoit `pending_stream_request` non null |
| VLC affiche "Connection failed" | Port 8554 fermé ou mauvais token | Tester depuis le serveur local : `vlc rtsp://127.0.0.1:8554/field-1?token=<TOK1>` |
| Webhook auth toujours en 403 | Clé HMAC différente entre Cockpit et mediamtx | Comparer `MEDIAMTX_AUTH_HMAC_KEY` (env Cockpit) avec ce qui est dans `mediamtx.yml` |
| Slot bloqué après crash tablette | GC ne tourne pas (Cockpit n'a pas reçu d'allocation depuis) | Demander un nouveau flux → le GC s'exécute en lazy. Sinon redémarrer Cockpit. |
| Modale tablette ne joue pas le son | Pas d'interaction utilisateur depuis le boot | Le son est autorisé seulement après un premier tap. Normal au tout premier lancement. |

### Logs utiles

```powershell
# mediamtx en live
Get-Content C:\mediamtx\stdout.log -Tail 30 -Wait

# Reverse proxy : selon ce qui sert cockpit.lemans.org
#  - IIS : %SystemDrive%\inetpub\logs\LogFiles\
#  - Caddy : C:\caddy\stdout.log
#  - nginx : C:\nginx\logs\access.log et error.log

# État des slots côté Mongo (en supposant mongo shell installé)
mongosh
> use cockpit
> db.field_stream_slots.find()
> db.field_streams.find().sort({_id:-1}).limit(5)
```

---

## 9. Procédure de désactivation d'urgence

Si un incident RGPD est suspecté, couper immédiatement le service mediamtx :

```powershell
Stop-Service mediamtx
# tous les flux en cours s'arrêtent. Cockpit verra les slots se libérer au prochain GC (≤ 30s).
```

Pour réactiver :

```powershell
Start-Service mediamtx
```

---

## 10. Checklist de mise en service

Une fois tous les tests OK, valider les 8 points avant ouverture événement :

- [ ] mediamtx en service Windows, démarrage auto vérifié après reboot
- [ ] Reverse proxy existant (IIS/nginx/Caddy) routant `/webrtc/*` vers mediamtx
- [ ] Cert HTTPS de `cockpit.lemans.org` valide >30 jours (déjà géré par l'existant)
- [ ] 3 view tokens générés et notés en lieu sûr (gestionnaire de mots de passe ou coffre)
- [ ] Variables d'env Cockpit posées (`MEDIAMTX_BASE_URL=https://cockpit.lemans.org/webrtc`) et redémarrage Cockpit fait
- [ ] 3 caméras Qonify configurées (`rtsp://cockpit.lemans.org:8554/field-{1,2,3}?token=...`) et vérifiées avec une tablette test
- [ ] Test 2 (webhook auth 403/200) réussi
- [ ] Test 3 (stabilité URL VLC) réussi
- [ ] Test 5 (auto-stop 5 min) réussi
- [ ] Pare-feu Windows ouvert sur 8554/tcp et 8189/udp entrants

---

**Note importante** : la fonctionnalité a été conçue avec ces décisions actées :
- **Pas d'enregistrement** côté mediamtx (`record: no` global)
- **Pas d'audio** capté côté tablette (`audio: false` dans `getUserMedia`)
- **Auto-stop strict à 5 min**, pas de prolongation, nouvelle demande = nouveau consentement
- **Pool fixe de 3 slots**, redéploiement requis pour passer à plus

Pour modifier ces paramètres : voir `field.py` constantes `FIELD_STREAM_*` et `mediamtx.yml`.
