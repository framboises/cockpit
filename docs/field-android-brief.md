# Field Android : brief de developpement (a reprendre sur le Mac)

Redige le 07/11/2026 depuis le serveur Cockpit. Les numeros de ligne valent pour le commit 26621de.

**Etat : code ecrit le 07/11/2026, voir `docs/claude/field.md`. Firebase en service, version de production signee le 08/11/2026 (1.5). Restent : patrouille longue ecran eteint sur le terrain, validation RGPD, diffusion par MDM.**

## Objectif

Une application Android pour Field, avec deux gains par rapport a la PWA :

1. **GPS ecran verrouille** : `/field/position` continue de recevoir la position.
2. **Notifications fiables** : reveil du telephone, canal SOS distinct.

La PWA reste en service, notamment pour les iPhone. Il n'y a **qu'un seul code web** : l'application
charge `https://cockpit.lemans.org/field`, donc toute amelioration de `field.js`, `field.html` ou du
CSS arrive aux deux versions au deploiement du serveur. L'APK ne se republie que si le natif change.

A lire avant de commencer : `docs/claude/field.md` et `docs/claude/patrouilles.md`.

## Choix retenus

- **Capacitor** avec `server.url` distant : il apporte les permissions WebView, le selecteur de
  fichiers (`<input capture>`) et le pont JS / natif.
- **Pas de TWA** : elle ne regle pas le GPS ecran verrouille.
- **Service natif maison** plutot qu'un plugin tiers : c'est lui qui poste les positions, ce qui
  fonctionne meme si le JS de la WebView est gele.
- **Web Push n'existe pas en WebView** : l'application passe par Firebase Cloud Messaging (FCM).

## Organisation

- Projet Android : `mobile/field-android/` dans ce repo.
- A ajouter au `.gitignore` : `node_modules`, `build/`, `google-services.json`, `*.jks`, `*.keystore`.
- Prendre une version de Capacitor compatible avec les versions de Node et du JDK du Mac.
- Le Mac sert au natif. Le code serveur se deploie ensuite sur le serveur Cockpit, par `main`.

## Natif (Android)

### Service de position

Service au premier plan `foregroundServiceType="location"` et notification permanente.
Fused Location Provider. A chaque position :

- **POST `/field/position`** avec le corps deja utilise par `field.js` :
  `lat, lng, accuracy, speed, heading, battery, ts`. Aucune nouvelle route.
- **Cookie** : `field_token` est HttpOnly, chemin `/field`. Le lire avec
  `CookieManager.getCookie("https://cockpit.lemans.org/field")`.
- **Renouvellement** : le serveur repose le cookie (30 jours glissants) sur n'importe quelle reponse.
  Reinjecter tout `Set-Cookie` dans `CookieManager` puis `flush()`, sinon une tablette restee
  verrouillee finit par perdre sa session.
- **Reponse 401** (`not_paired`, `device_revoked`, `event_ended`) : arreter le service.
- **Hors ligne** : garder le dernier point et le renvoyer au retour du reseau, comme
  `flushPositionBuffer` (seule la position la plus recente est utile).
- **Vers le JS** : transmettre aussi chaque position a la page par un evenement du plugin.

Permissions : `ACCESS_FINE_LOCATION`, `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_LOCATION`,
`POST_NOTIFICATIONS`, `CAMERA`. Demander l'exclusion de l'optimisation de batterie.

### Notifications

`FirebaseMessagingService` qui recoit des messages **data** de priorite haute et affiche la
notification lui-meme, avec les memes champs que Web Push : `title, body, url, tag, type`.

- Canal **SOS** (`type == "sos"`) : importance maximale, vibration longue, notification persistante.
- Canal **normal** pour le reste.
- Toucher la notification : ouvrir l'application et transmettre `url` a la page.
- A la reception, prevenir la page, qui doit relire inbox et fiches.
- Remonter le jeton FCM a l'ouverture et dans `onNewToken`.

### Plugin expose au JS (exemple : `FieldNative`)

`startTracking({profile})`, `stopTracking()`, `setPosting({enabled})`, `getFcmToken()`,
evenements `location`, `pushReceived`, `openUrl`.

Limiter la navigation de la WebView a `cockpit.lemans.org` : le pont natif ne doit etre accessible
qu'aux pages de Cockpit.

## Web (`static/js/field.js`)

Tout reste conditionne : `window.Capacitor && Capacitor.isNativePlatform()`. Sans cela, la PWA ne
doit strictement pas changer de comportement.

| Ligne | Fonction | Changement en mode natif |
|---|---|---|
| 722, 749 | `startGeolocation`, `applyGpsProfile` | piloter `FieldNative` au lieu de `watchPosition` |
| 734 | `selectGpsProfile` | ne pas passer en `paused` sur `visibilityState === "hidden"` |
| 782 | `onGpsUpdate` | appele par l'evenement `location` (carte, patrouille, vitesse inchanges) |
| 855 | `maybePushPosition` | ne plus poster, sauf position simulee `_sim` : le natif s'en charge |
| 4683 | `setSimPosition` | `setPosting({enabled:false})` en mode test, sinon le vrai GPS ecrase la simulation |
| 10291, 10339 | `catchUpNow`, messages du service worker | brancher `pushReceived` et `openUrl` sur le meme traitement |
| 10462 | `initPushSubscription` | enregistrer le jeton FCM a la place |

Regle de suivi proposee :

- `pause` : service arrete. Pas de suivi pendant une pause declaree.
- `intervention`, `sur_place`, SOS, patrouille en cours, suivi cockpit (`tracking_mode`) : `high`.
- `fin_intervention` : `low`.
- Sinon : `normal`, pour que le dispatch dispose d'une position fraiche.

## Serveur (`field.py`)

Changements additifs. Les tablettes PWA ne sont pas concernees.

1. **Route `POST /field/push/fcm-register`** (`field_token_required`, a cote de
   `/field/push/subscribe`, verifier qu'elle echappe au CSRF de la meme facon). Ecrit dans
   `field_push_subs` avec `kind: "fcm"`, `fcm_token` et `endpoint: "fcm:<token>"`, car l'index
   unique porte sur `endpoint`.
2. **`send_push_to_device`** (ligne 277) : si `kind == "fcm"`, envoyer par FCM au lieu de Web Push,
   et supprimer l'abonnement si le jeton est invalide (retour `"expired"`). Aujourd'hui la boucle
   ignore toute entree sans `subscription`.
3. **`firebase_admin`** : deja dans `requirements.txt` (`vision_sync.py`). Utiliser une application
   nommee, importee a la demande, avec une nouvelle variable d'environnement a documenter dans
   `CLAUDE.md`. Ne lancer ni thread ni tache dans le processus Cockpit.
4. **Reponse de `/field/position`** (ligne 1647) : ajouter `status` et `tracking_mode`. Le service
   natif peut ainsi adapter sa cadence quand le JS est gele. Verifier d'ou vient `tracking_mode`
   dans `/field/my-fiches`.

**Firebase : creer un projet dedie.** Ne pas reutiliser `firebase-service-account.json`, qui
appartient a Vision et dont la synchro Firestore est facturee. Ne jamais versionner
`google-services.json` ni la cle de service.

## Pieges et points a tester sur une vraie tablette

Tester en priorite. Aucun de ces points n'a ete verifie.

1. **Validation des points de patrouille** : elle est **dans le JS** (`checkPatrolProximity`,
   ligne 5198). Si la WebView gele ecran verrouille, le serveur recoit les positions mais les points
   ne se valident pas. Une patrouille ecran eteint est **le test decisif**. Si cela echoue, il
   faudra deplacer cette validation cote natif ou serveur.
2. **Flux video** : envoi WebRTC WHIP en H264 vers mediamtx depuis la WebView (`_preferH264`),
   encodage dependant de l'appareil. Si la WebView ne propose pas H264, le VMS ne lira rien.
3. **Hors ligne** : service worker `field-sw.js` et file IndexedDB sous WebView, dont un demarrage
   a froid sans reseau. Capacitor intercepte les requetes : verifier le cookie et le cache.
4. **Tenue du service** : Android limite le demarrage d'un service de localisation depuis
   l'arriere-plan. Il doit demarrer application ouverte. Verifier le comportement apres un arret du
   processus, et sur les modeles reels, certains constructeurs coupant les applications.
5. **Reappairage** : la WebView n'a pas la session de Chrome. La fiche de l'ancienne tablette reste
   active sous le meme nom, d'ou `name_conflict` au reappairage : revoquer l'ancienne ou changer de nom.
6. **Maintien de l'ecran** : le Wake Lock web peut etre absent en WebView. Il n'est plus necessaire
   au GPS, mais verifier l'affichage du panneau Outils > Etat.
7. **Photos** : `<input capture>` et reduction a 1920 px.
8. **Vibrations et son** : `navigator.vibrate` et `AudioContext` ecran verrouille (alerte de vitesse).

Pour tester sans risque, utiliser le **mode test** de `/field-dispatch` : pas de vraie fiche proposee
automatiquement. Attention, les positions simulees n'entrent pas dans `anoloc_positions`, et seul un
essai reel sur le terrain valide le GPS.

## RGPD

Le suivi ecran verrouille change la nature du traitement pour les agents : a faire valider. Prevoir
au minimum la notification permanente, l'arret en pause et hors service, et l'information des agents.

## Distribution

APK signe installe a la main ou par MDM, ou diffusion privee sur Google Play. Conserver la cle de
signature en lieu sur : sans elle, plus aucune mise a jour possible. Faire annoncer sa version par
l'APK, pour que le serveur puisse signaler une mise a jour native necessaire.

## Ordre de travail conseille

1. Coquille Capacitor qui charge `/field` : appairage, carte, photos, flux video, hors ligne.
2. Service de position et test de patrouille ecran verrouille (piege 1) : c'est le point decisif.
3. FCM de bout en bout, cote natif et serveur.
4. Regle de suivi, reglage de la batterie, distribution.
5. Mettre a jour `docs/claude/field.md` et ajouter une entree dans `CLAUDE.md`.
