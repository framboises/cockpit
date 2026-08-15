# Cockpit — app Connect IQ pour tactix 8 Solar

Affiche au poignet le compteur d'entrées, la contrainte thermique (WBGT) et les
alertes actives du PC Organisation. Vibre sur franchissement de seuil à la
hausse, jamais en boucle.

Distribuée en **sideload** uniquement (pas de publication sur le store Connect
IQ) : c'est le directeur des opérations adjoint qui porte la montre, pas le
grand public.

## Cible

Device Connect IQ : `fenix8solar51mm`. C'est l'identifiant Garmin de la
**tactix 8 Solar 51 mm** — il n'existe aucun device `tactix*`, le SDK la range
sous « fēnix® 8 Solar 51mm / tactix® 8 Solar 51mm ».

Écran 280 x 280 rond, MIP transflectif, 8 bpp, centre (140,140), rayon 140.
Pas de dégradé, anti-aliasing médiocre : gros chiffres pleins, aplats francs.
Budgets mémoire : watch-app 768 Ko, glance 64 Ko, background 64 Ko.

## Architecture

Trois points d'entrée (`CockpitApp.mc`) partagent un cache commun :

| Fichier | Rôle |
|---|---|
| `Cache.mc` | cache `Application.Storage` versionné (schéma `v`), partagé par les trois points d'entrée |
| `State.mc` | accès typés au cache : niveaux (`wbgtLevel`, `alertMax`, `worstLevel`), âges (`dataAgeSec` = âge de la donnée, `responseAgeSec` = âge de la réponse HTTP, `worstAgeSec` = le pire des deux) |
| `Fmt.mc` | formatage : groupement des milliers, âges lisibles, WBGT à une décimale |
| `Api.mc` | requête réseau vers `/api/v1/watch/state`, conversion du payload, garde anti-doublon bornée à 30 s |
| `Alerting.mc` | transition de niveau + vibration, jamais en boucle |
| `Mock.mc` | quatre scénarios simulés pour travailler sans backend |
| `CockpitView.mc` / `CockpitDelegate.mc` | console (device app) |
| `GlanceView.mc` | glance |
| `BgService.mc` | service de fond (`CockpitService extends ServiceDelegate`) |
| `CockpitApp.mc` | les trois points d'entrée : device app, glance, background |

29 tests Run No Evil couvrent `Cache`, `State` (via `Fmt`), `Fmt`, `Api` et
`Alerting`. Le backend (`watch_api.py`, `watch_state.py`) est couvert par 63
tests pytest.

## Prérequis

- Connect IQ SDK 9.2.0 installé via le SDK Manager Garmin
- Java (le 1.8 du poste suffit, vérifié)
- Une clé développeur

### Générer la clé développeur

Elle n'est pas dans le dépôt et ne doit jamais y entrer.

```bash
mkdir -p ~/.garmin_keys
openssl genrsa -out ~/.garmin_keys/developer_key.pem 4096
openssl pkcs8 -topk8 -inform PEM -outform DER \
  -in ~/.garmin_keys/developer_key.pem \
  -out ~/.garmin_keys/developer_key.der -nocrypt
```

## Commandes

Toutes depuis `garmin/cockpit-watch/`. `$SDK` désigne le dossier du SDK.

```bash
export SDK="$HOME/Library/Application Support/Garmin/ConnectIQ/Sdks/connectiq-sdk-mac-9.2.0-2026-06-09-92a1605b2"
```

### Build de développement

```bash
mkdir -p bin
"$SDK/bin/monkeyc" -o bin/cockpit.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
```

### Build de release (pour le sideload)

```bash
"$SDK/bin/monkeyc" -o bin/cockpit.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm -r
```

### Simulateur

```bash
"$SDK/bin/connectiq" &
"$SDK/bin/monkeydo" bin/cockpit.prg fenix8solar51mm
```

Régler les propriétés dans **Settings > Application Settings**. Mettre
`mockData` à vrai pour travailler sans backend ni jeton ; `mockScenario`
fait défiler quatre cas (0 nominal, 1 WBGT en hausse, 2 alerte critique,
3 donnée périmée).

Voir la glance : **Simulation > Glance View**.
Déclencher le service de fond : **Simulation > Trigger Background Event**.

### Tests

```bash
"$SDK/bin/connectiq" &
"$SDK/bin/monkeyc" -t -o bin/test.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
"$SDK/bin/monkeydo" bin/test.prg fenix8solar51mm -t
```

Un test isolé :

```bash
"$SDK/bin/monkeydo" bin/test.prg fenix8solar51mm -t testAlertsOnRise
```

⚠️ **`monkeydo -t` renvoie un code de sortie 1 même quand tous les tests
passent.** Revérifié pour cette rédaction : `echo $?` après un run 100 % vert
renvoie `1`. C'est la sortie texte qui fait foi, jamais le code de sortie :

```
PASSED (passed=29, failed=0, errors=0)
```

Un script CI qui se fierait au code de sortie casserait silencieusement dès
le premier lancement.

Tests backend :

```bash
python -m pytest tests/test_watch_state.py tests/test_watch_api.py -q
```

`63 passed`.

### Sideload

Pas de publication sur le store. On copie le `.prg` à la main.

```bash
"$SDK/bin/monkeyc" -o bin/cockpit.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm -r
cp bin/cockpit.prg /Volumes/GARMIN/GARMIN/APPS/
cp bin/cockpit-settings.json /Volumes/GARMIN/GARMIN/SETTINGS/   # voir ci-dessous
diskutil eject /Volumes/GARMIN
```

⚠️ **Le fichier de réglages est probablement à copier lui aussi, et ce point
reste à confirmer sur la montre.** Le build produit deux fichiers : le `.prg`
et un `cockpit-settings.json` à côté. Le binaire du simulateur cherche ses
réglages dans `0:/GARMIN/Settings/<nom>-settings.json`, c'est-à-dire dans le
système de fichiers de l'appareil et non à côté de l'exécutable — ce qui
laisse penser qu'une montre sideloadée sans ce fichier n'aurait aucun réglage,
donc **pas de jeton, donc une montre muette**.

Si le dossier `GARMIN/SETTINGS/` n'existe pas sur la montre, le créer. Si les
réglages n'apparaissent pas dans Connect IQ Mobile après le sideload, c'est la
première chose à vérifier.

## Réglages

Ils se saisissent depuis Connect IQ Mobile (Cockpit > Réglages), pas dans le
code : le jeton n'est jamais en dur.

| Réglage | Défaut | Rôle |
|---|---|---|
| `host` | `cockpit.lemans.org` | domaine de l'endpoint, **HTTPS obligatoire** |
| `token` | vide | jeton Bearer, émis depuis `/watch-admin` |
| `pollPeak` | 60 s | période quand ça chauffe |
| `pollNormal` | 180 s | période au repos |
| `staleAfter` | 90 s | au-delà, la donnée s'affiche en rouge |
| `alertVibrate` | vrai | couper la vibration |
| `mockData` | faux | travailler sans réseau |
| `mockScenario` | 0 | scénario simulé |

## Backend

L'app consomme `GET /api/v1/watch/state` de l'application cockpit,
authentifié par `Authorization: Bearer <jeton>`.

Émettre un jeton : page `/watch-admin` (admin). Le jeton en clair n'est
affiché **qu'une seule fois**. Il est stocké haché en base et révocable.

Un jeton émis en dev (`titan_dev`) ne vaut rien en prod (`titan`).

C'est aussi dans `/watch-admin` qu'on choisit l'événement rapporté (mode
auto par défaut, qui suit le compteur) et quelles alertes partent à la
montre, avec leur niveau 1-3.

## Métriques mesurées

Mémoire, mesurée avec `monkeyc --build-stats 0` (fiable partout ; l'interface
**View Memory** du simulateur est bloquée dans certains environnements) :

```
Glance      4920 octets sur 65536   ( 7,5 %)
Background  4549 octets sur 65536   ( 6,9 %)
Foreground  7534 octets sur 786432  ( 1,0 %)
```

```bash
"$SDK/bin/monkeyc" -o bin/cockpit.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm -r \
  --build-stats 0
```

Ces chiffres sont ceux du **build de release** (`-r`), celui qu'on charge sur
la montre. Un build de debug donne des totaux légèrement supérieurs (environ
+130 octets sur la glance et sur le service de fond, +180 sur la device app),
les blocs annotés `(:debug)` y étant conservés. Comparer deux mesures faites
dans des modes différents donne un écart qui n'a rien à voir avec le code.

Confortablement sous les trois budgets.

Affichage, mesuré via `dc.getFontHeight()` sur `fenix8solar51mm` :

```
FONT_XTINY = 22 px, FONT_MEDIUM = 39 px, FONT_NUMBER_MEDIUM = 69 px, FONT_GLANCE = 26 px
Ecran rond 280 x 280, centre (140,140), rayon 140
Zone de glance : 217 x 88 px, rectangulaire (pas de contrainte de bord rond)
```

## Pièges rencontrés

Chacun a coûté un aller-retour de compilation ou d'exécution. Ils sont
consignés ici pour ne pas les redécouvrir :

1. **`monkeydo -t` renvoie 1 même en succès complet** — voir section Tests.
2. **`hidden` est invalide au niveau module**, variables comme fonctions. Le
   compilateur répond `extraneous input 'hidden' expecting {... 'public'
   ...}`. Réservé aux membres de classe (utilisé par exemple sur
   `CockpitView.largeurUtile`).
3. **`method(:maFonction)` ne fonctionne pas depuis l'intérieur d'un
   module**, seulement depuis une classe. Depuis un module, il faut
   `new Lang.Method(MonModule, :maFonction)`.
4. **`Test.assertEqual(X, null)` plante à l'exécution.** La signature du SDK
   est `assertEqual(value1 as Lang.Object, value2 as Lang.Object or Null)` :
   seul le **second** argument accepte `null`. Utiliser `Test.assert(X ==
   null)`.
5. **Certains callbacks exigent une annotation de type stricte.**
   `Timer.start` impose `as Void` sur sa méthode cible ; le callback de
   `makeWebRequest` impose la signature complète, `PersistedContent.Iterator`
   compris.
6. **Sur un écran rond, la largeur utile dépend de la hauteur**, et c'est le
   bord du bloc **le plus éloigné du centre** qui contraint : le sommet pour
   un bloc de la moitié haute, le bas pour la moitié basse.
   `CockpitView.largeurUtile(dc, y)` calcule la corde. Un texte qui tient au
   milieu déborde en haut ou en bas.
7. **La zone de glance est rectangulaire**, elle : n'y importe pas la
   logique de corde ci-dessus.
8. **Les titres de `settings.xml` doivent être des ressources, jamais des
   littéraux.** Écrire `title="Domaine"` en dur le compile en `$literal0$`,
   rangé sous une pseudo-langue interne nommée `valyrian`. Les réglages
   deviennent alors illisibles par tout éditeur — donc impossible de saisir
   le jeton, donc montre muette. Passer par `title="@Strings.SetHost"`.
9. **Chaque langue déclarée dans le manifeste exige son dossier
   `resources-<lang>/`.** Déclarer `<iq:language>fre</iq:language>` sans
   `resources-fre/` ne produit aucune traduction utilisable. Vérifié sur le
   sample `Strings` du SDK : 12 langues déclarées, 12 dossiers. Le dossier
   `resources/` sert de défaut et n'a pas besoin d'être déclaré.
10. **L'éditeur de réglages du simulateur ne lit pas le fichier produit par
    le build.** Il attend un fichier agrégé, aux clés `apps` / `languages`,
    dans le système de fichiers de l'appareil simulé — là où `monkeyc`
    produit un fichier aux clés `settings` / `languages` à côté du `.prg`.
    Les deux ne sont pas interchangeables, et `monkeydo` ne pousse jamais le
    second. Pour tester un scénario sans passer par cet éditeur, construire
    un `.prg` dédié avec la valeur en dur.

## Ce qui peut rendre la montre silencieusement muette

C'est le vrai risque d'exploitation : dans chaque cas, la montre continue
d'afficher quelque chose, sans jamais signaler qu'elle a perdu le fil.

- **Certificat expiré.** Celui de `cockpit.lemans.org` est valide jusqu'au
  21/02/2027 (vérifié le 14/08/2026 : `CN=*.lemans.org`, émis par RapidSSL
  TLS RSA CA G1 / DigiCert Global Root G2). Passé cette date, `makeWebRequest`
  refuse la requête et **la montre affiche une donnée qui vieillit sans
  message explicite** — elle repasse simplement en rouge au bout de
  `staleAfter`.
- **Jeton révoqué** depuis `/watch-admin`, ou jeton émis dans le mauvais
  environnement : un jeton créé en dev (`titan_dev`) ne vaut rien en prod
  (`titan`). Même symptôme que le certificat : la donnée affichée vieillit,
  rien n'indique pourquoi.
- **Téléphone hors de portée ou Garmin Connect Mobile fermé.** C'est le
  téléphone qui exécute la requête, pas la montre : sans lui, aucun appel ne
  part, même si le réseau cellulaire est excellent là où se trouve le
  directeur des opérations.
- **Flux Skidata arrêté alors que l'API répond parfaitement.** Le cache
  distingue deux âges, `t` (âge de la donnée) et `rx` (âge de la réponse) :
  l'app affiche toujours le pire des deux. Une API qui répond à l'heure mais
  sert une donnée figée reste donc détectée — mais seulement via l'âge
  affiché, jamais par un message dédié.
- **Aucune alerte configurée** dans `/watch-admin` : la collection ne
  portant aucune sévérité, un slug absent de la configuration ne part jamais
  à la montre. Une page d'admin vide donne une montre qui n'alerte sur rien,
  ce qui se lit exactement comme une situation calme.

## Le mode 24 h

Le mode « usage continu 24 h » désigne la **glance + le service de fond**,
pas la device app ouverte en permanence. Une app Connect IQ ouverte empêche
la montre de retomber en veille, et certains firmwares la tuent au bout d'un
moment d'inactivité. La device app est ce qu'on ouvre pour regarder ; le
service de fond, cadencé à 5 minutes (le plancher imposé par la plateforme,
pas un choix), entretient le cache et alerte entre deux consultations.

## Points à savoir

- **HTTPS avec certificat valide est obligatoire.** `makeWebRequest` refuse
  un auto-signé. Le simulateur tolère HTTP, la montre non.
- **La requête est exécutée par le téléphone** via Garmin Connect Mobile :
  l'endpoint doit être joignable depuis Internet.
- **5 minutes est le plancher** du service de fond, imposé par la
  plateforme, un seul événement temporel enregistré à la fois.
- **L'alerte ne se déclenche que sur transition montante.** Une redescente
  ou un plateau reste silencieux, sinon l'alerte devient un bruit de fond
  qu'on finit par ignorer.

## Constats sur montre réelle

**À compléter** — la validation sur la tactix 8 Solar physique (tâche 11)
n'a pas encore eu lieu : elle demande un accès USB à la montre, qui n'a pas
été disponible pendant ce projet. Trois questions restent ouvertes.

### 1. `Attention.vibrate` fonctionne-t-il depuis le `ServiceDelegate` ?

Le SDK ne le documente ni ne l'interdit — zéro occurrence du mot
« background » dans `Attention.html`. `BgService.onFetched` appelle
aujourd'hui `Alerting.check(st)` directement.

Protocole (voir `task-11-brief.md`, étape 4) : fermer l'app, laisser la
montre au repos, faire monter artificiellement un niveau depuis
`/watch-admin`, attendre le prochain cycle de fond (<= 5 min).

- Si la montre vibre : rien à faire.
- Si elle reste muette : appliquer le repli. Dans `BgService.onFetched`,
  remplacer `Alerting.check(st)` par `Background.exit(st)` et déplacer
  l'appel à `Alerting.check` dans `CockpitApp.onBackgroundData(data)`.
  L'alerte se déclenche alors à la prochaine ouverture de l'app ou de la
  glance, plutôt qu'en tâche de fond. L'appel à `Attention` est confiné à
  la seule fonction `Alerting.buzz()`, ce qui fait tenir ce repli en une
  ligne.

**Résultat constaté :** _(à compléter après la tâche 11)_

### 2. Consommation réelle sur 24 h

Relever le pourcentage de batterie au départ, laisser la montre en usage
normal (app fermée, glance consultée de temps en temps, service de fond
actif), relever après 24 h.

**Résultat constaté :** _(à compléter après la tâche 11)_ % sur 24 h

### 3. Rendu visuel des quatre scénarios simulés — VALIDÉ

Le layout avait été établi **par le calcul**, à partir des hauteurs de police
mesurées sur le device (section Métriques mesurées) et de
`CockpitView.largeurUtile` : le sandbox de développement bloquait les
captures d'écran du simulateur.

**Résultat constaté (14/08/2026, simulateur, fenix8solar51mm)** : les quatre
scénarios s'affichent correctement. Les trois défauts corrigés au calcul sont
confirmés absents à l'écran — le compteur d'entrées ne mord pas sur la ligne
de débit, les lignes d'alerte ne se chevauchent pas, et le pied de page n'est
pas rogné par le bord rond.

Pour reproduire sans passer par l'éditeur de réglages du simulateur (voir
piège 10), construire quatre exécutables avec le scénario en dur :

```bash
# depuis une copie temporaire du projet, remplacer dans Api.fetch
#   var mock = Application.Properties.getValue("mockData");  ->  var mock = true;
#   var scenario = Application.Properties.getValue(...);      ->  var scenario = N;
# puis compiler un .prg par scenario
```

### 4. Connect IQ Mobile affiche-t-il les huit réglages ?

**C'est la vérification la plus importante des quatre** : sans elle, pas de
saisie du jeton, donc pas de montre.

L'éditeur de réglages du **simulateur** ne permet pas de la faire (piège 10 :
il lit un fichier d'une autre forme). Le seul test valable est donc sur le
téléphone, après sideload.

Protocole : sideloader le `.prg` **et** le `cockpit-settings.json` (voir la
section Sideload), puis ouvrir Connect IQ Mobile > Cockpit > Réglages.

Attendu : huit réglages libellés en français — Domaine, Jeton, Periode pic,
Periode normale, Seuil peremption, Vibrer sur alerte, Donnees simulees,
Scenario simule.

Si la liste est vide ou illisible, vérifier dans l'ordre : le
`cockpit-settings.json` a-t-il bien été copié dans `GARMIN/SETTINGS/` ; le
fichier déclare-t-il les langues `fra`/`fre` (et non une pseudo-langue) —
contrôle rapide :

```bash
python3 -c "import json;print(list(json.load(open('bin/cockpit-settings.json'))['languages'].keys()))"
```

**Résultat constaté :** _(à compléter après la tâche 11)_
