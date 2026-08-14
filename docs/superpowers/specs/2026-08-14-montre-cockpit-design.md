# Montre cockpit — supervision opérationnelle au poignet

Design validé le 14/08/2026.

Une app Connect IQ pour tactix 8 Solar qui affiche en direct le compteur
d'entrées, la contrainte thermique et les alertes actives du PC Organisation,
et qui vibre quand un seuil est franchi à la hausse.

---

## 1. Périmètre

Deux livrables :

1. un endpoint de lecture `GET /api/v1/watch/state` dans l'application cockpit,
   plus une page d'administration pour émettre les jetons et régler ce qui
   remonte à la montre ;
2. un projet Connect IQ à trois points d'entrée — device app, glance, service
   de fond.

Hors périmètre : publication sur le store Connect IQ, écriture de quelque
donnée métier que ce soit depuis la montre, support d'un autre modèle que la
cible ci-dessous.

## 2. Cible matérielle et outillage

Tout ce tableau a été vérifié sur le poste, pas déduit.

| Point | Valeur |
|---|---|
| Device ID | **`fenix8solar51mm`** — Garmin range la tactix 8 Solar 51 mm sous `fēnix® 8 Solar 51mm / tactix® 8 Solar 51mm`, il n'existe aucun device `tactix*` |
| Écran | 280 × 280 rond, **MIP transflectif**, 8 bpp (256 couleurs) |
| Budgets mémoire | watch-app **768 Ko**, glance **64 Ko**, background **64 Ko** |
| API | groupe « API level 6.0 », `connectIQVersion` 6.0.2 |
| Stockage app | 10 Mo |
| SDK | 9.2.0 (`connectiq-sdk-mac-9.2.0-2026-06-09-92a1605b2`), celui pointé par `current-sdk.cfg` |
| Toolchain | `monkeyc` compile avec le Java 1.8 du poste — vérifié, build d'un sample pour la cible : `BUILD SUCCESSFUL` |
| Extension | `garmin.monkey-c-1.1.3` |
| Clé développeur | **absente du poste**, à générer |

Le budget background est de **64 Ko et non 32** : un vrai décodage de réponse y
est possible, inutile de tordre le code pour l'éviter.

Ajouter la tactix 8 AMOLED (`fenix847mm`) plus tard coûte une ligne
`<iq:product>` et une variante de layout — rien de structurel. Ses contraintes
d'affichage sont opposées (always-on, budget de pixels allumés).

## 3. Endpoint backend

### 3.1 Module et routes

Nouveau module `watch_api.py`, blueprint `watch_bp` monté sur `/api/v1/watch`.
La seule modification de `app.py` est l'enregistrement du blueprint.

| Route | Auth | Rôle |
|---|---|---|
| `GET /api/v1/watch/state` | Bearer | la seule que la montre appelle |
| `GET /api/v1/watch/admin/tokens` | `@role_required("admin")` | liste, sans jamais renvoyer le secret |
| `POST /api/v1/watch/admin/tokens` | admin | émet un jeton, le renvoie en clair **une seule fois** |
| `POST /api/v1/watch/admin/tokens/<id>/revoke` | admin | révocation |
| `GET` / `PUT /api/v1/watch/admin/config` | admin | événement, filtre d'alertes, seuils WBGT |

Le blueprint **n'est pas exempté de CSRF** — `csrf.exempt()` est proscrit dans
ce projet. `/state` est un `GET`, que Flask-WTF ne protège pas : aucune
exemption n'est nécessaire. Les routes admin qui écrivent gardent la protection
pleine, le JS envoie `X-CSRFToken`.

Erreurs au format `{"ok": false, "error": "<code>"}`, jamais `abort(404)` : le
handler 404 global de `app.py` redirige vers `/` et casserait un appel machine.

### 3.2 Contrat de réponse

```json
{"t": 1755172800, "n": "24HM 26", "e": 48213, "er": 3200,
 "w": 27.4, "wl": 1,
 "al": [{"l": 3, "m": "SOS tablette"}, {"l": 2, "m": "Vent 72 km/h"}]}
```

| Clé | Sens | Absent / indisponible |
|---|---|---|
| `t` | timestamp unix **du relevé compteur**, pas l'heure serveur | `null` |
| `n` | libellé court de l'événement rapporté (`evenement.short` + année sur 2 chiffres) | `null` |
| `e` | entrées cumulées | `null` |
| `er` | débit d'entrées, personnes/h | `null` |
| `w` | WBGT °C, une décimale | `null` |
| `wl` | niveau WBGT 0-3 | `0` |
| `al` | alertes actives, au plus 5, `m` tronqué à 24 caractères | `[]` |

`n` existe pour une seule raison : sans lui, une configuration épinglée sur le
mauvais événement est totalement invisible au poignet. Coût ~12 octets sur un
budget de 2 Ko.

`t` est l'horodatage de la **donnée**, pas de la réponse. C'est ce qui rend
l'affichage « périmé depuis X min » honnête : il révèle aussi bien un lien BLE
coupé qu'un flux Skidata arrêté alors que l'API répond parfaitement.

### 3.3 Authentification

Collection `watch_tokens` :

```
{_id, label, token_sha256, created_at, created_by,
 revoked: bool, revoked_at, last_used_at, last_ip, use_count}
```

Index unique sur `token_sha256`. Le jeton clair fait 32 octets urlsafe et
n'est **jamais stocké**. La vérification est un lookup indexé sur le hash :
aucune comparaison de secrets, donc pas de sujet de temps constant.

`last_used_at` n'est réécrit qu'une fois par minute au plus. Sans cette bride,
une montre à 1 min de polling produirait 1 440 écritures par jour pour de la
seule télémétrie.

Les jetons vivent dans la base de leur environnement : un jeton émis en dev
(`titan_dev`) ne vaut rien en prod (`titan`). Il faut en émettre un depuis la
prod.

**Rate limit** : fenêtre glissante en mémoire, par jeton, 60 requêtes / 5 min,
sur le modèle de `_rate_limit_pair` de `field.py`. La montre en consomme 5
(app à 1 min) plus 1 (background). La marge absorbe les reprises réseau sans
jamais couvrir une boucle folle. Dépassement → `429` + `Retry-After`.

### 3.4 Calcul de l'état

Calcul à la volée, protégé par un **cache mémoire de 20 s** — le pattern déjà
en place dans `traffic.py`. Aucun process supplémentaire, charge Mongo bornée
quel que soit le rythme de polling, et une panne se manifeste franchement par
un 5xx plutôt que par une donnée figée.

- **`e`** — `data_access`, dernier document du compteur `compteur_principal_id`
  (`628` = ENCEINTE GENERALE), champ `entries`. Absent ou live-contrôle
  inactif → `null`.
- **`er`** — même compteur au snapshot le plus proche de T-15 min,
  `(entries_now - entries_then) / Δh`. Un delta négatif (remise à zéro du
  compteur) donne `null`, jamais un débit absurde.
- **`t`** — `timestamp` du relevé compteur.
- **`w` / `wl`** — créneau horaire courant de `meteo_previsions`, enrichi par
  `meteo_thermique.analyser()`. Les 5 paliers ISO 7243 se replient sur 4
  niveaux : `0` sous 25 °C, `1` ≥ 25, `2` ≥ 28, `3` ≥ 30. `danger_extreme`
  (≥ 33) reste à 3 — au-delà de « suspendre le travail lourd », un cran de plus
  ne change aucune décision au poignet. Seuils surchargeables par la config.
- **`al`** — `cockpit_active_alerts` non expirées, filtrées sur l'événement
  retenu.

`cockpit_active_alerts` **ne porte aucun champ de sévérité** ; le `priority` de
`cockpit_alert_definitions` est un ordre d'affichage, pas une gravité
(`opening` = 1, `field_sos` = 99). Le niveau 0-3 de la montre est donc **déclaré
explicitement par slug** dans la configuration. Seuls les slugs listés partent
à la montre : le filtre et l'échelle de gravité sont le même objet.

Tri par niveau décroissant, au plus 5 entrées, `m` tronqué à 24 caractères —
c'est ce qui tient la réponse sous 2 Ko.

### 3.5 Événement et année

Le compteur **n'a besoin d'aucune sélection** : `hsh_get_counters` interroge
`data_access` sur le seul `requested_location_id`, trié par timestamp, et le
relevé porte lui-même son `requested_event` et son `year`. La donnée
s'auto-identifie.

Le choix ne compte que pour filtrer `cockpit_active_alerts`, qui portent
`event` + `year`.

Deux modes, réglés dans la page admin montre :

- **`auto`** (défaut) — on reprend le `requested_event` / `year` du dernier
  relevé du compteur principal. Chiffres et alertes viennent alors forcément du
  même événement.
- **épinglé** — un couple `event` + `year` explicite, choisi dans un sélecteur
  alimenté par la collection `evenement`.

Le doc global du live-contrôle (`data_access._id = "___GLOBAL___"`) **n'est pas
retenu comme source** : il ne porte aucune année (`PUT /api/live-controle/config`
n'accepte que `evenement` et `evenement_clean`) et il dérive en pratique — il
portait `GPF` posé le 7 mai alors que les relevés du compteur 628 étaient des
`24H MOTOS 2026`.

### 3.6 Configuration

Collection `watch_config`, document singleton :

```
{_id: "watch",
 event_mode: "auto" | "pinned",
 event: "24H MOTOS", year: 2026,        // utilisés si pinned
 alerts: [{slug: "field_sos", level: 3, label: "SOS tablette"}, ...],
 wbgt_levels: [25, 28, 30],
 updated_at, updated_by}
```

### 3.7 Page admin

`/watch-admin`, réservée admin. `templates/watch_admin.html` +
`static/js/watch_admin.js`, IIFE autonome sur le modèle de `vision_admin.js`.

Trois blocs :

- **Jetons** — émettre (le secret s'affiche une fois), révoquer, voir la
  dernière utilisation et l'IP.
- **Événement** — bascule auto / épinglé, sélecteurs événement et année.
- **Alertes** — les définitions de `cockpit_alert_definitions` listées avec,
  pour chacune, une case « envoyer à la montre », un niveau 1-3 et un libellé
  court. Plus les trois seuils WBGT.

## 4. Application Connect IQ

### 4.1 Arborescence

```
garmin/cockpit-watch/
├── manifest.xml
├── monkey.jungle
├── README.md
├── resources/
│   ├── drawables/   launcher_icon.png (40×40) + drawables.xml
│   ├── strings/     strings.xml (fr)
│   ├── settings/    settings.xml    ← réglages Connect IQ Mobile
│   └── properties/  properties.xml  ← valeurs par défaut
└── source/
    ├── CockpitApp.mc       AppBase : getInitialView / getGlanceView / getServiceDelegate
    ├── Cache.mc            (:glance)(:background)  lecture/écriture Storage
    ├── State.mc            (:glance)(:background)  accès typés, calcul des niveaux
    ├── Api.mc              (:background)  makeWebRequest, URL, en-tête Bearer, mock
    ├── Alerting.mc         (:background)  transitions, vibrate + tone
    ├── CockpitView.mc      console device app
    ├── CockpitDelegate.mc  entrées
    ├── GlanceView.mc       (:glance)
    └── BgService.mc        (:background)  ServiceDelegate
```

`(:glance)` et `(:background)` sont des **annotations spéciales du compilateur**
(doc Monkey C : « Denotes code blocks available to the Background process /
when running in Glance Mode »). Le cloisonnement mémoire est donc pris en
charge par le compilateur ; inutile de manipuler `excludeAnnotations` dans la
jungle, qui ne sert qu'aux exclusions de build (`debug`, `release`,
`extendedCode`).

`manifest.xml` : `type="watch-app"`, `minSdkVersion="6.0.0"`, produit unique
`fenix8solar51mm`, permissions `Communications` et `Background`.

### 4.2 Réglages Connect IQ

| Propriété | Type | Défaut |
|---|---|---|
| `host` | string | `cockpit.lemans.org` |
| `token` | string | vide |
| `pollPeak` | number (s) | 60 |
| `pollNormal` | number (s) | 180 |
| `staleAfter` | number (s) | 90 |
| `alertVibrate` | boolean | true |
| `mockData` | boolean | false |
| `mockScenario` | number | 0 |

Le jeton n'est **jamais en dur** : il est saisi dans les réglages Connect IQ
Mobile, ce qui fonctionne aussi pour une app chargée par sideload.

### 4.3 Le cache

Une seule clé `Application.Storage`, un dictionnaire :

```
{v: 1, t, n, e, er, w, wl, al: [[l, m], ...], rx, ok}
```

`al` en tableau de tableaux plutôt qu'en tableau de dictionnaires : moitié
moins d'octets et d'objets à instancier. Surtout, **`Storage` rend des types
natifs déjà décodés** — la glance ne parse rien, elle lit un dictionnaire.
C'est là qu'est l'économie de mémoire, pas dans l'absence de bitmap.

`v` versionne le schéma : un cache d'une version antérieure est ignoré plutôt
que lu de travers.

`rx` (horodatage de la dernière réponse reçue) est **distinct de `t`**. Deux
pannes différentes : lien BLE coupé, ou flux Skidata arrêté alors que l'API
répond. L'app affiche le pire des deux âges et nomme lequel.

### 4.4 Les trois points d'entrée

**Device app.** Fetch immédiat à l'ouverture, puis timer à `pollNormal`,
qui passe à `pollPeak` dès que le niveau d'alerte maximal ou `wl` atteint 2.
Bascule manuelle par bouton, mode courant affiché. Écran 280 × 280 : entrées en
gros, débit dessous, WBGT et son niveau au milieu, jusqu'à 3 alertes en bas
(sinon « RAS »), âge de la donnée en pied — en rouge au-delà de `staleAfter`.
`ENTER` force un rafraîchissement ; une seconde page déroule la liste complète
des alertes.

**Glance.** Lit le cache, écrit trois nombres, aucune requête réseau. Le code
couleur passe par `AppBase.getGlanceTheme()`, surchargé pour renvoyer
`GLANCE_THEME_RED` / `GOLD` / `GREEN` selon le niveau en cache : la bande
d'indicateur native se colore sans rien dessiner. Les glances tournent dans un
espace restreint et **n'acceptent aucune entrée** — pas de contrôle à y prévoir.

**Background.** `registerForTemporalEvent` à 5 min — c'est le plancher imposé
par la plateforme, la doc est explicite (« cannot be set to occur less than 5
minutes after the last temporal event »), et **un seul événement temporel** peut
être enregistré à la fois. Fetch, écriture du cache, détection de transition,
`Background.exit(null)`.

### 4.5 Alertes : uniquement sur transition montante

`Alerting.mc` conserve `lastWl` et `lastAlMax` en Storage. `Attention.vibrate`
et `playTone` ne se déclenchent que si le nouveau niveau **dépasse** l'ancien.
Redescente ou stagnation : silence.

Les deux chemins (app et background) écrivent le même cache, donc le contrôle
vit dans le module partagé et s'appuie sur les valeurs mémorisées : le premier
qui écrit déplace la référence, il n'y a pas de double vibration.

### 4.6 Autonomie

**Le mode 24 h, c'est glance + background**, pas la device app ouverte en
continu : une app Connect IQ ouverte empêche la montre de retomber en veille et
certains firmwares la tuent sur inactivité. La device app est ce qu'on ouvre
pour regarder ; le background à 5 min entretient le cache et alerte entre-temps.

Le HTTP étant porté par le téléphone, le coût côté montre est du trafic BLE.
L'écran MIP transflectif ne consomme que par son rétroéclairage.

### 4.7 Données mockées

`mockData` à vrai fait servir un état figé par `Api.mc` au lieu d'appeler le
réseau : la device app est testable dans le simulateur sans backend ni jeton.
`mockScenario` fait défiler quelques cas — WBGT qui monte, alerte critique,
donnée périmée — pour vérifier transitions et vibrations sans attendre un pic
réel.

## 5. Risques et points à valider

| Risque | Traitement |
|---|---|
| **`Attention.vibrate` depuis un `ServiceDelegate`** n'est ni documenté ni interdit — zéro occurrence de « background » dans `Attention.html` | Appel isolé derrière une fonction dédiée. Si la montre réelle reste muette : repli sur `Background.exit(données)` et déclenchement à la prochaine ouverture de l'app ou de la glance, changement d'une ligne |
| `cockpit.lemans.org` doit présenter un **certificat valide** — `makeWebRequest` refuse l'auto-signé | À vérifier avant le premier test sur montre. L'app Vision appelle déjà ce domaine depuis Internet, ce qui est un bon signe mais pas une preuve |
| Le cache mémoire 20 s suppose **un seul process** | Vrai sous waitress, même hypothèse que `analyse_ops.py` et le staging de `scan_report.py`. À revoir si passage à gunicorn multi-workers |
| Clé développeur absente du poste | À générer avant le premier build, documenté dans le README |
| Budget mémoire glance | À mesurer dans le simulateur, pas à supposer |

## 6. Livrables

- `watch_api.py`, enregistrement du blueprint dans `app.py`
- `templates/watch_admin.html`, `static/js/watch_admin.js`
- `garmin/cockpit-watch/` complet
- `garmin/cockpit-watch/README.md` : génération de la clé développeur,
  commandes de build, lancement du simulateur, sideload du `.prg` dans
  `GARMIN/APPS`, réglage du jeton
