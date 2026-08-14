# Montre cockpit — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Afficher au poignet, sur une tactix 8 Solar, le compteur d'entrées, la contrainte thermique et les alertes actives du PC Organisation, avec vibration sur franchissement de seuil à la hausse.

**Architecture:** Un endpoint Flask de lecture seule (`GET /api/v1/watch/state`) sert un JSON compact authentifié par jeton Bearer révocable ; une app Connect IQ à trois points d'entrée (device app, glance, service de fond) le consomme, partage un cache `Application.Storage` unique et déclenche les alertes uniquement sur transition montante.

**Tech Stack:** Flask 3.0 / pymongo 4.9 (existants) ; pytest (nouveau, dev seulement) ; Monkey C, Connect IQ SDK 9.2.0, tests Run No Evil (`monkeyc -t` + `monkeydo -t`). Aucune dépendance externe côté montre.

**Spec:** `docs/superpowers/specs/2026-08-14-montre-cockpit-design.md`

## Global Constraints

- **Device unique** : `fenix8solar51mm`. C'est l'identifiant Garmin de la tactix 8 Solar 51 mm (`displayName` = « fēnix® 8 Solar 51mm / tactix® 8 Solar 51mm »). Il n'existe aucun device `tactix*`.
- **SDK** : `~/Library/Application Support/Garmin/ConnectIQ/Sdks/connectiq-sdk-mac-9.2.0-2026-06-09-92a1605b2`. Dans tout ce plan, `$SDK` désigne ce chemin et `$KEY` la clé développeur générée en Tâche 1.
- **Écran** : 280 × 280 rond, MIP transflectif, 8 bpp. Pas de dégradé, anti-aliasing médiocre : gros chiffres pleins, aplats francs.
- **Budgets mémoire** : watch-app 768 Ko, glance 64 Ko, background 64 Ko.
- **Manifeste `version="3"`** : l'attribut est `minApiLevel`, **pas** `minSdkVersion` (celui-ci n'existe que dans les manifestes v1). Valeur : `6.0.0`.
- **Aucune dépendance externe en Monkey C.** Monkey C standard uniquement.
- **Jamais de guillemets typographiques** dans le code JS/CSS/Python/Monkey C. Uniquement `'` et `"` droits.
- **`csrf.exempt()` est proscrit.** Le blueprint `watch_bp` n'est jamais exempté.
- **Pas d'`abort(404)`** dans les routes du blueprint : le handler 404 global (`app.py:700`) redirige vers `/` et casserait tout appel machine. Erreurs au format `{"ok": false, "error": "<code>"}`.
- **`@role_required` redirige (302) au lieu de renvoyer 401.** La route `/state` ne doit pas l'utiliser ; elle porte son propre décorateur Bearer.
- **`year` est un `int`** partout dans le code montre, jamais une chaîne.
- **Datetimes** : `data_access.timestamp` est un datetime BSON. Les comparaisons se font entre datetimes, jamais entre chaînes.
- **Le nom de base n'est jamais codé en dur.** `from app import db` donne `titan_dev` en dev, `titan` en prod.

---

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `watch_state.py` | Calcul de l'état : fonctions pures (niveaux, débit, sélection d'alertes, mise en forme) + lectures Mongo, `db` passé en argument. Aucun import Flask. |
| `watch_api.py` | Blueprint : jetons, rate limit, cache 20 s, routes publique et admin. |
| `app.py` | Deux lignes : import et `register_blueprint`. |
| `templates/watch_admin.html` | Page d'administration. |
| `static/js/watch_admin.js` | IIFE autonome pilotant la page. |
| `tests/test_watch_state.py` | Tests des fonctions pures et des lectures Mongo (db factice). |
| `tests/test_watch_api.py` | Tests des routes via le client de test Flask. |
| `requirements-dev.txt` | `pytest`. N'affecte pas `requirements.txt` de production. |
| `garmin/cockpit-watch/` | Projet Connect IQ (détail en Tâche 1). |

Le découpage `watch_state.py` (calcul, `db` en argument) / `watch_api.py` (routes) reprend le pattern déjà en place dans ce dépôt entre `scan_frequentation.py` et `scan_report.py`.

---

## Tâche 1 : Squelette Connect IQ qui compile

**⚠️ CETTE TÂCHE SE TERMINE PAR UNE VALIDATION UTILISATEUR EXPLICITE.** Le demandeur a exigé de valider le manifeste, l'arborescence et les squelettes vides avant toute logique. Ne pas enchaîner sur la Tâche 2 sans son accord.

**Files:**
- Create: `garmin/cockpit-watch/manifest.xml`
- Create: `garmin/cockpit-watch/monkey.jungle`
- Create: `garmin/cockpit-watch/resources/drawables/drawables.xml`
- Create: `garmin/cockpit-watch/resources/drawables/launcher_icon.svg`
- Create: `garmin/cockpit-watch/resources/strings/strings.xml`
- Create: `garmin/cockpit-watch/resources/settings/settings.xml`
- Create: `garmin/cockpit-watch/resources/properties/properties.xml`
- Create: `garmin/cockpit-watch/source/CockpitApp.mc`
- Create: `garmin/cockpit-watch/source/CockpitView.mc`
- Create: `garmin/cockpit-watch/source/CockpitDelegate.mc`
- Create: `garmin/cockpit-watch/source/GlanceView.mc`
- Create: `garmin/cockpit-watch/source/BgService.mc`
- Create: `garmin/cockpit-watch/.gitignore`
- Modify: `.gitignore` (racine)

**Interfaces:**
- Consumes: rien.
- Produces: la classe `CockpitApp extends Application.AppBase` avec les trois surcharges `getInitialView()`, `getGlanceView()`, `getServiceDelegate()` ; les classes vides `CockpitView`, `CockpitDelegate`, `CockpitGlanceView`, `CockpitService`. Les propriétés `host`, `token`, `pollPeak`, `pollNormal`, `staleAfter`, `alertVibrate`, `mockData`, `mockScenario`.

- [ ] **Étape 1 : Générer la clé développeur**

Elle est absente du poste. Sans elle, `monkeyc` refuse de produire un `.prg`.

```bash
mkdir -p ~/.garmin_keys
openssl genrsa -out ~/.garmin_keys/developer_key.pem 4096
openssl pkcs8 -topk8 -inform PEM -outform DER \
  -in ~/.garmin_keys/developer_key.pem \
  -out ~/.garmin_keys/developer_key.der -nocrypt
ls -la ~/.garmin_keys/developer_key.der
```

Attendu : le fichier `.der` existe et pèse ~2,3 Ko. Cette clé **ne va jamais dans le dépôt**.

- [ ] **Étape 2 : Créer le manifeste**

`garmin/cockpit-watch/manifest.xml` :

```xml
<?xml version="1.0"?>
<iq:manifest version="3" xmlns:iq="http://www.garmin.com/xml/connectiq">
    <iq:application
        id="C736AB55A59548BEB4E53CA2D21F2BBB"
        type="watch-app"
        name="@Strings.AppName"
        entry="CockpitApp"
        launcherIcon="@Drawables.LauncherIcon"
        minApiLevel="6.0.0">
        <iq:products>
            <iq:product id="fenix8solar51mm"/>
        </iq:products>
        <iq:permissions>
            <iq:uses-permission id="Communications"/>
            <iq:uses-permission id="Background"/>
        </iq:permissions>
        <iq:languages>
            <iq:language>fre</iq:language>
            <iq:language>eng</iq:language>
        </iq:languages>
        <iq:barrels/>
    </iq:application>
</iq:manifest>
```

L'`id` est un UUID déjà généré pour ce projet : ne pas le changer, il identifie l'app sur la montre et sur le téléphone. Aucun élément `<iq:glance/>` n'existe : la doc Garmin (`Core_Topics/Glances.html`) est explicite, il suffit de surcharger `AppBase.getGlanceView()` pour apparaître dans la liste des glances.

- [ ] **Étape 3 : Créer la jungle**

`garmin/cockpit-watch/monkey.jungle` :

```
project.manifest = manifest.xml
```

Une seule ligne suffit : le compilateur prend `source/` et `resources/` par défaut. Ne **pas** ajouter d'`excludeAnnotations` ici : `(:glance)` et `(:background)` sont des annotations spéciales du compilateur, il gère seul le cloisonnement mémoire. `excludeAnnotations` ne sert qu'aux exclusions de build (`debug`, `release`, `extendedCode`).

- [ ] **Étape 4 : Créer les ressources**

`resources/strings/strings.xml` :

```xml
<?xml version="1.0" encoding="utf-8"?>
<strings>
    <string id="AppName">Cockpit</string>
    <string id="Stale">perime depuis</string>
    <string id="NoAlert">RAS</string>
    <string id="NoData">pas de donnee</string>
    <string id="Entries">entrees</string>
    <string id="Rate">pers/h</string>
</strings>
```

`resources/drawables/drawables.xml` :

```xml
<?xml version="1.0" encoding="utf-8"?>
<drawables>
    <bitmap id="LauncherIcon" filename="launcher_icon.svg" dithering="none" />
</drawables>
```

`resources/drawables/launcher_icon.svg` — un disque plein avec un chevron, lisible à 40 × 40 sur MIP :

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">
    <circle cx="20" cy="20" r="18" fill="none" stroke="#FFFFFF" stroke-width="3"/>
    <path d="M12 24 L20 13 L28 24" fill="none" stroke="#FFFFFF" stroke-width="3"/>
</svg>
```

`resources/settings/settings.xml` — ce que le porteur règle depuis Connect IQ Mobile :

```xml
<?xml version="1.0" encoding="utf-8"?>
<settings>
    <setting propertyKey="@Properties.host" title="Domaine">
        <settingConfig type="alphaNumeric" maxLength="64"/>
    </setting>
    <setting propertyKey="@Properties.token" title="Jeton">
        <settingConfig type="alphaNumeric" maxLength="64"/>
    </setting>
    <setting propertyKey="@Properties.pollPeak" title="Periode pic (s)">
        <settingConfig type="numeric" min="30" max="600"/>
    </setting>
    <setting propertyKey="@Properties.pollNormal" title="Periode normale (s)">
        <settingConfig type="numeric" min="60" max="1800"/>
    </setting>
    <setting propertyKey="@Properties.staleAfter" title="Seuil peremption (s)">
        <settingConfig type="numeric" min="30" max="3600"/>
    </setting>
    <setting propertyKey="@Properties.alertVibrate" title="Vibrer sur alerte">
        <settingConfig type="boolean"/>
    </setting>
    <setting propertyKey="@Properties.mockData" title="Donnees simulees">
        <settingConfig type="boolean"/>
    </setting>
    <setting propertyKey="@Properties.mockScenario" title="Scenario simule">
        <settingConfig type="numeric" min="0" max="3"/>
    </setting>
</settings>
```

`resources/properties/properties.xml` :

```xml
<?xml version="1.0" encoding="utf-8"?>
<properties>
    <property id="host" type="string">cockpit.lemans.org</property>
    <property id="token" type="string"></property>
    <property id="pollPeak" type="number">60</property>
    <property id="pollNormal" type="number">180</property>
    <property id="staleAfter" type="number">90</property>
    <property id="alertVibrate" type="boolean">true</property>
    <property id="mockData" type="boolean">false</property>
    <property id="mockScenario" type="number">0</property>
</properties>
```

- [ ] **Étape 5 : Créer les squelettes de source**

`source/CockpitApp.mc` :

```monkeyc
using Toybox.Application;
using Toybox.WatchUi;

class CockpitApp extends Application.AppBase {

    function initialize() {
        AppBase.initialize();
    }

    function onStart(state) {
    }

    function onStop(state) {
    }

    function getInitialView() {
        return [new CockpitView(), new CockpitDelegate()];
    }

    (:glance)
    function getGlanceView() {
        return [new CockpitGlanceView()];
    }

    function getServiceDelegate() {
        return [new CockpitService()];
    }
}
```

`source/CockpitView.mc` :

```monkeyc
using Toybox.WatchUi;
using Toybox.Graphics;

class CockpitView extends WatchUi.View {

    function initialize() {
        View.initialize();
    }

    function onLayout(dc) {
    }

    function onShow() {
    }

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        dc.drawText(dc.getWidth() / 2, dc.getHeight() / 2,
                    Graphics.FONT_MEDIUM, "Cockpit",
                    Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
    }

    function onHide() {
    }
}
```

`source/CockpitDelegate.mc` :

```monkeyc
using Toybox.WatchUi;

class CockpitDelegate extends WatchUi.BehaviorDelegate {

    function initialize() {
        BehaviorDelegate.initialize();
    }
}
```

`source/GlanceView.mc` :

```monkeyc
using Toybox.WatchUi;
using Toybox.Graphics;

(:glance)
class CockpitGlanceView extends WatchUi.GlanceView {

    function initialize() {
        GlanceView.initialize();
    }

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, dc.getHeight() / 2, Graphics.FONT_GLANCE, "Cockpit",
                    Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
    }
}
```

`source/BgService.mc` :

```monkeyc
using Toybox.System;
using Toybox.Background;

(:background)
class CockpitService extends System.ServiceDelegate {

    function initialize() {
        ServiceDelegate.initialize();
    }

    function onTemporalEvent() {
        Background.exit(null);
    }
}
```

- [ ] **Étape 6 : Ignorer les artefacts de build**

`garmin/cockpit-watch/.gitignore` :

```
bin/
*.prg
*.prg.debug.xml
*.iq
```

Ajouter à la fin du `.gitignore` racine :

```
# Cles developpeur Connect IQ : ne doivent jamais entrer dans le depot
*.der
developer_key.pem
```

- [ ] **Étape 7 : Compiler**

```bash
SDK="$HOME/Library/Application Support/Garmin/ConnectIQ/Sdks/connectiq-sdk-mac-9.2.0-2026-06-09-92a1605b2"
cd garmin/cockpit-watch
mkdir -p bin
"$SDK/bin/monkeyc" -o bin/cockpit.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
```

Attendu : `BUILD SUCCESSFUL` et `bin/cockpit.prg` présent.

Si le build échoue sur `Target device id ... is not enabled in the application manifest file`, c'est que le bloc `<iq:products>` ne contient pas `fenix8solar51mm` — relire l'étape 2.

- [ ] **Étape 8 : Lancer dans le simulateur**

```bash
"$SDK/bin/connectiq" &
sleep 5
"$SDK/bin/monkeydo" bin/cockpit.prg fenix8solar51mm
```

Attendu : le simulateur affiche un écran noir avec « Cockpit » centré.

- [ ] **Étape 9 : Commit**

```bash
git add garmin/cockpit-watch .gitignore
git commit -m "feat(montre): squelette Connect IQ compilable pour tactix 8 Solar"
```

- [ ] **Étape 10 : ARRÊT — faire valider par le demandeur**

Lui montrer l'arborescence produite, le manifeste, et la capture du simulateur. **Ne pas continuer sans son accord explicite.**

---

## Tâche 2 : Cache et état côté montre

**Files:**
- Create: `garmin/cockpit-watch/source/Cache.mc`
- Create: `garmin/cockpit-watch/source/State.mc`
- Create: `garmin/cockpit-watch/source/CacheTest.mc`

**Interfaces:**
- Consumes: rien de la Tâche 1 hors le projet compilable.
- Produces:
  - `Cache.SCHEMA = 1`, `Cache.KEY = "st"`
  - `Cache.save(dict)` → `Void`
  - `Cache.load()` → `Dictionary or Null` (renvoie `null` si absent ou si `v != SCHEMA`)
  - `Cache.clear()` → `Void`
  - `State.alertMax(st)` → `Number` (0 si `al` vide ou absent)
  - `State.wbgtLevel(st)` → `Number`
  - `State.dataAgeSec(st, nowSec)` → `Number or Null` (âge de `t`)
  - `State.responseAgeSec(st, nowSec)` → `Number or Null` (âge de `rx`)
  - `State.worstAgeSec(st, nowSec)` → `Number or Null` (le pire des deux)
  - `State.isStale(st, nowSec, staleAfter)` → `Boolean`

- [ ] **Étape 1 : Écrire les tests qui échouent**

`source/CacheTest.mc`. Les tests Run No Evil sont compilés uniquement avec `-t`, ils ne pèsent pas sur le build de production.

```monkeyc
using Toybox.Test;
using Toybox.Application;

(:test)
function testCacheRoundTrip(logger) {
    Cache.clear();
    Cache.save({"v" => 1, "t" => 100, "e" => 42});
    var st = Cache.load();
    Test.assert(st != null);
    Test.assertEqual(st["e"], 42);
    return true;
}

(:test)
function testCacheRejectsOtherSchema(logger) {
    Application.Storage.setValue(Cache.KEY, {"v" => 99, "e" => 1});
    Test.assertEqual(Cache.load(), null);
    return true;
}

(:test)
function testCacheEmptyIsNull(logger) {
    Cache.clear();
    Test.assertEqual(Cache.load(), null);
    return true;
}

(:test)
function testAlertMaxTakesHighest(logger) {
    var st = {"al" => [[1, "a"], [3, "b"], [2, "c"]]};
    Test.assertEqual(State.alertMax(st), 3);
    return true;
}

(:test)
function testAlertMaxEmptyIsZero(logger) {
    Test.assertEqual(State.alertMax({"al" => []}), 0);
    Test.assertEqual(State.alertMax({}), 0);
    Test.assertEqual(State.alertMax(null), 0);
    return true;
}

(:test)
function testWorstAgeTakesOlder(logger) {
    // donnee de 300 s, reponse de 10 s : c'est la donnee qui est perimee
    var st = {"t" => 700, "rx" => 990};
    Test.assertEqual(State.worstAgeSec(st, 1000), 300);
    return true;
}

(:test)
function testWorstAgeHandlesMissing(logger) {
    Test.assertEqual(State.worstAgeSec({"rx" => 990}, 1000), 10);
    Test.assertEqual(State.worstAgeSec({}, 1000), null);
    return true;
}

(:test)
function testIsStale(logger) {
    Test.assertEqual(State.isStale({"t" => 900, "rx" => 900}, 1000, 90), true);
    Test.assertEqual(State.isStale({"t" => 950, "rx" => 950}, 1000, 90), false);
    // sans donnee du tout, on considere perime
    Test.assertEqual(State.isStale({}, 1000, 90), true);
    return true;
}
```

- [ ] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

```bash
SDK="$HOME/Library/Application Support/Garmin/ConnectIQ/Sdks/connectiq-sdk-mac-9.2.0-2026-06-09-92a1605b2"
cd garmin/cockpit-watch
"$SDK/bin/monkeyc" -t -o bin/test.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
```

Attendu : ÉCHEC à la compilation, `Cache` et `State` n'existent pas.

- [ ] **Étape 3 : Écrire `Cache.mc`**

```monkeyc
using Toybox.Application;

(:glance :background)
module Cache {

    // Version du schema stocke. Un cache d'une autre version est ignore
    // plutot que relu de travers apres une mise a jour de l'app.
    const SCHEMA = 1;
    const KEY = "st";

    function save(st) {
        st["v"] = SCHEMA;
        Application.Storage.setValue(KEY, st);
    }

    function load() {
        var st = Application.Storage.getValue(KEY);
        if (st == null || !(st instanceof Toybox.Lang.Dictionary)) {
            return null;
        }
        if (st["v"] != SCHEMA) {
            return null;
        }
        return st;
    }

    function clear() {
        Application.Storage.deleteValue(KEY);
    }
}
```

- [ ] **Étape 4 : Écrire `State.mc`**

```monkeyc
(:glance :background)
module State {

    function alertMax(st) {
        if (st == null) {
            return 0;
        }
        var al = st["al"];
        if (al == null || al.size() == 0) {
            return 0;
        }
        var max = 0;
        for (var i = 0; i < al.size(); i += 1) {
            var lvl = al[i][0];
            if (lvl != null && lvl > max) {
                max = lvl;
            }
        }
        return max;
    }

    function wbgtLevel(st) {
        if (st == null || st["wl"] == null) {
            return 0;
        }
        return st["wl"];
    }

    function dataAgeSec(st, nowSec) {
        if (st == null || st["t"] == null) {
            return null;
        }
        return nowSec - st["t"];
    }

    function responseAgeSec(st, nowSec) {
        if (st == null || st["rx"] == null) {
            return null;
        }
        return nowSec - st["rx"];
    }

    // Le pire des deux ages. Une donnee fraiche recue il y a longtemps et une
    // donnee vieille recue a l'instant sont deux pannes differentes : on
    // affiche la plus grave des deux.
    function worstAgeSec(st, nowSec) {
        var a = dataAgeSec(st, nowSec);
        var b = responseAgeSec(st, nowSec);
        if (a == null) {
            return b;
        }
        if (b == null) {
            return a;
        }
        return a > b ? a : b;
    }

    function isStale(st, nowSec, staleAfter) {
        var age = worstAgeSec(st, nowSec);
        if (age == null) {
            return true;
        }
        return age > staleAfter;
    }
}
```

- [ ] **Étape 5 : Lancer les tests pour vérifier qu'ils passent**

```bash
"$SDK/bin/connectiq" &
sleep 5
"$SDK/bin/monkeyc" -t -o bin/test.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
"$SDK/bin/monkeydo" bin/test.prg fenix8solar51mm -t
```

Attendu : les 8 tests passent, sortie se terminant par `PASSED`.

- [ ] **Étape 6 : Commit**

```bash
git add garmin/cockpit-watch/source
git commit -m "feat(montre): cache Storage versionne et lecture d'etat"
```

---

## Tâche 3 : Device app avec données simulées

C'est la brique que le demandeur veut voir en premier : une console lisible dans le simulateur, sans backend ni jeton.

**Files:**
- Create: `garmin/cockpit-watch/source/Mock.mc`
- Create: `garmin/cockpit-watch/source/Fmt.mc`
- Create: `garmin/cockpit-watch/source/FmtTest.mc`
- Modify: `garmin/cockpit-watch/source/CockpitView.mc`
- Modify: `garmin/cockpit-watch/source/CockpitDelegate.mc`
- Modify: `garmin/cockpit-watch/source/CockpitApp.mc` (étape 7 : `getInitialView()` passe désormais la vue au délégué)

**Interfaces:**
- Consumes: `Cache.save`, `Cache.load`, `State.alertMax`, `State.wbgtLevel`, `State.worstAgeSec`, `State.isStale` (Tâche 2).
- Produces:
  - `Mock.state(scenario, nowSec)` → `Dictionary` au schéma du cache
  - `Fmt.count(n)` → `String` (`48213` → `"48 213"`, `null` → `"--"`)
  - `Fmt.rate(n)` → `String` (`3200` → `"3200"`, `null` → `"--"`)
  - `Fmt.wbgt(w)` → `String` (`27.4` → `"27.4"`, `null` → `"--"`)
  - `Fmt.age(sec)` → `String` (`95` → `"1 min"`, `3700` → `"1 h"`, `null` → `"--"`)
  - `CockpitView.refresh()` → `Void`, recharge l'état et redessine

- [ ] **Étape 1 : Écrire les tests de formatage qui échouent**

`source/FmtTest.mc` :

```monkeyc
using Toybox.Test;

(:test)
function testCountGroupsThousands(logger) {
    Test.assertEqual(Fmt.count(48213), "48 213");
    Test.assertEqual(Fmt.count(213), "213");
    Test.assertEqual(Fmt.count(1000), "1 000");
    Test.assertEqual(Fmt.count(1234567), "1 234 567");
    Test.assertEqual(Fmt.count(0), "0");
    return true;
}

(:test)
function testCountNullIsDashes(logger) {
    Test.assertEqual(Fmt.count(null), "--");
    return true;
}

(:test)
function testAgeUnderOneMinute(logger) {
    Test.assertEqual(Fmt.age(30), "30 s");
    return true;
}

(:test)
function testAgeInMinutes(logger) {
    Test.assertEqual(Fmt.age(95), "1 min");
    Test.assertEqual(Fmt.age(600), "10 min");
    return true;
}

(:test)
function testAgeInHours(logger) {
    Test.assertEqual(Fmt.age(3700), "1 h");
    return true;
}

(:test)
function testAgeNullIsDashes(logger) {
    Test.assertEqual(Fmt.age(null), "--");
    return true;
}

(:test)
function testWbgtOneDecimal(logger) {
    Test.assertEqual(Fmt.wbgt(27.4), "27.4");
    Test.assertEqual(Fmt.wbgt(null), "--");
    return true;
}
```

- [ ] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

```bash
cd garmin/cockpit-watch
"$SDK/bin/monkeyc" -t -o bin/test.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
```

Attendu : ÉCHEC, `Fmt` n'existe pas.

- [ ] **Étape 3 : Écrire `Fmt.mc`**

```monkeyc
using Toybox.Lang;

(:glance :background)
module Fmt {

    const DASH = "--";

    // Espace insecable fine entre milliers : un compteur a six chiffres est
    // illisible d'un coup d'oeil sans separation.
    function count(n) {
        if (n == null) {
            return DASH;
        }
        var s = n.toString();
        var out = "";
        var c = 0;
        for (var i = s.length() - 1; i >= 0; i -= 1) {
            out = s.substring(i, i + 1) + out;
            c += 1;
            if (c % 3 == 0 && i > 0) {
                out = " " + out;
            }
        }
        return out;
    }

    function rate(n) {
        if (n == null) {
            return DASH;
        }
        return n.toString();
    }

    function wbgt(w) {
        if (w == null) {
            return DASH;
        }
        return w.format("%.1f");
    }

    function age(sec) {
        if (sec == null) {
            return DASH;
        }
        if (sec < 0) {
            sec = 0;
        }
        if (sec < 60) {
            return sec.toString() + " s";
        }
        if (sec < 3600) {
            return (sec / 60).toString() + " min";
        }
        return (sec / 3600).toString() + " h";
    }
}
```

- [ ] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

```bash
"$SDK/bin/monkeyc" -t -o bin/test.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
"$SDK/bin/monkeydo" bin/test.prg fenix8solar51mm -t
```

Attendu : les 7 tests de `Fmt` passent, plus les 8 de la Tâche 2.

- [ ] **Étape 5 : Écrire `Mock.mc`**

```monkeyc
(:background)
module Mock {

    // Quatre scenarios pour eprouver l'affichage et les transitions sans
    // attendre un vrai pic : nominal, montee du WBGT, alerte critique,
    // donnee perimee.
    function state(scenario, nowSec) {
        if (scenario == 1) {
            return {"t" => nowSec, "rx" => nowSec, "ok" => true,
                    "n" => "24HM 26", "e" => 51200, "er" => 4100,
                    "w" => 29.1, "wl" => 2, "al" => []};
        }
        if (scenario == 2) {
            return {"t" => nowSec, "rx" => nowSec, "ok" => true,
                    "n" => "24HM 26", "e" => 62800, "er" => 5200,
                    "w" => 31.6, "wl" => 3,
                    "al" => [[3, "SOS tablette"], [2, "Vent 72 km/h"],
                             [1, "Ouverture imminente"]]};
        }
        if (scenario == 3) {
            // Recue a l'instant, mais la donnee date de 22 minutes.
            return {"t" => nowSec - 1320, "rx" => nowSec, "ok" => true,
                    "n" => "24HM 26", "e" => 48213, "er" => null,
                    "w" => 27.4, "wl" => 1, "al" => []};
        }
        return {"t" => nowSec, "rx" => nowSec, "ok" => true,
                "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "w" => 27.4, "wl" => 1, "al" => [[1, "Ouverture imminente"]]};
    }
}
```

- [ ] **Étape 6 : Écrire la vue**

Remplacer intégralement `source/CockpitView.mc` :

```monkeyc
using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Application;
using Toybox.Timer;
using Toybox.Time;

class CockpitView extends WatchUi.View {

    hidden var mState = null;
    hidden var mTimer = null;
    hidden var mPage = 0;

    function initialize() {
        View.initialize();
    }

    function onLayout(dc) {
    }

    function onShow() {
        refresh();
        mTimer = new Timer.Timer();
        mTimer.start(method(:onTick), periodMs(), true);
    }

    function onHide() {
        if (mTimer != null) {
            mTimer.stop();
            mTimer = null;
        }
    }

    function onTick() {
        refresh();
    }

    function periodMs() {
        var peak = Application.Properties.getValue("pollPeak");
        var normal = Application.Properties.getValue("pollNormal");
        if (peak == null) { peak = 60; }
        if (normal == null) { normal = 180; }
        // On resserre le rythme des que ca chauffe, sans reglage manuel.
        var level = State.alertMax(mState);
        var wl = State.wbgtLevel(mState);
        if (level >= 2 || wl >= 2) {
            return peak * 1000;
        }
        return normal * 1000;
    }

    function refresh() {
        var mock = Application.Properties.getValue("mockData");
        if (mock != null && mock) {
            var scenario = Application.Properties.getValue("mockScenario");
            if (scenario == null) { scenario = 0; }
            Cache.save(Mock.state(scenario, Time.now().value()));
        }
        mState = Cache.load();
        WatchUi.requestUpdate();
    }

    function nextPage() {
        mPage = (mPage + 1) % 2;
        WatchUi.requestUpdate();
    }

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        if (mPage == 0) {
            drawMain(dc);
        } else {
            drawAlerts(dc);
        }
    }

    hidden function levelColor(level) {
        if (level >= 3) { return Graphics.COLOR_RED; }
        if (level >= 2) { return Graphics.COLOR_ORANGE; }
        if (level >= 1) { return Graphics.COLOR_YELLOW; }
        return Graphics.COLOR_GREEN;
    }

    hidden function drawMain(dc) {
        var w = dc.getWidth();
        var st = mState;
        var now = Time.now().value();

        // Evenement rapporte, en haut : sans lui, une configuration epinglee
        // sur le mauvais evenement serait invisible.
        var label = (st != null && st["n"] != null) ? st["n"] : "--";
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 26, Graphics.FONT_XTINY, label,
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Entrees, le chiffre principal.
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 58, Graphics.FONT_NUMBER_MEDIUM,
                    Fmt.count(st != null ? st["e"] : null),
                    Graphics.TEXT_JUSTIFY_CENTER);
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 108, Graphics.FONT_XTINY,
                    Fmt.rate(st != null ? st["er"] : null) + " pers/h",
                    Graphics.TEXT_JUSTIFY_CENTER);

        // WBGT, colore par son niveau.
        var wl = State.wbgtLevel(st);
        dc.setColor(levelColor(wl), Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 136, Graphics.FONT_MEDIUM,
                    "WBGT " + Fmt.wbgt(st != null ? st["w"] : null),
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Alertes : trois lignes au plus, le reste sur la seconde page.
        var y = 176;
        var al = (st != null && st["al"] != null) ? st["al"] : [];
        if (al.size() == 0) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, "RAS",
                        Graphics.TEXT_JUSTIFY_CENTER);
        } else {
            var n = al.size() > 3 ? 3 : al.size();
            for (var i = 0; i < n; i += 1) {
                dc.setColor(levelColor(al[i][0]), Graphics.COLOR_TRANSPARENT);
                dc.drawText(w / 2, y, Graphics.FONT_XTINY, al[i][1],
                            Graphics.TEXT_JUSTIFY_CENTER);
                y += 18;
            }
        }

        // Age de la donnee, en rouge des qu'elle est perimee.
        var staleAfter = Application.Properties.getValue("staleAfter");
        if (staleAfter == null) { staleAfter = 90; }
        var age = State.worstAgeSec(st, now);
        var stale = State.isStale(st, now, staleAfter);
        dc.setColor(stale ? Graphics.COLOR_RED : Graphics.COLOR_DK_GRAY,
                    Graphics.COLOR_TRANSPARENT);
        var foot = stale ? ("perime depuis " + Fmt.age(age)) : Fmt.age(age);
        dc.drawText(w / 2, 244, Graphics.FONT_XTINY, foot,
                    Graphics.TEXT_JUSTIFY_CENTER);
    }

    hidden function drawAlerts(dc) {
        var w = dc.getWidth();
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 30, Graphics.FONT_XTINY, "ALERTES",
                    Graphics.TEXT_JUSTIFY_CENTER);
        var al = (mState != null && mState["al"] != null) ? mState["al"] : [];
        if (al.size() == 0) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, 130, Graphics.FONT_SMALL, "RAS",
                        Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }
        var y = 66;
        for (var i = 0; i < al.size(); i += 1) {
            dc.setColor(levelColor(al[i][0]), Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, al[i][1],
                        Graphics.TEXT_JUSTIFY_CENTER);
            y += 30;
        }
    }
}
```

- [ ] **Étape 7 : Écrire le délégué d'entrée**

Remplacer `source/CockpitDelegate.mc` :

```monkeyc
using Toybox.WatchUi;

class CockpitDelegate extends WatchUi.BehaviorDelegate {

    hidden var mView;

    function initialize(view) {
        BehaviorDelegate.initialize();
        mView = view;
    }

    // ENTER force un rafraichissement immediat.
    function onSelect() {
        mView.refresh();
        return true;
    }

    // Page suivante : la liste complete des alertes.
    function onNextPage() {
        mView.nextPage();
        return true;
    }

    function onPreviousPage() {
        mView.nextPage();
        return true;
    }
}
```

Le délégué reçoit désormais la vue : modifier `CockpitApp.getInitialView()` en conséquence.

```monkeyc
    function getInitialView() {
        var view = new CockpitView();
        return [view, new CockpitDelegate(view)];
    }
```

- [ ] **Étape 8 : Vérifier dans le simulateur**

```bash
"$SDK/bin/monkeyc" -o bin/cockpit.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
"$SDK/bin/monkeydo" bin/cockpit.prg fenix8solar51mm
```

Dans le simulateur, ouvrir **Settings → Edit Persistent Storage → Application Settings**, mettre `mockData` à vrai et `mockScenario` à `0`, puis relancer. Attendu : « 24HM 26 », `48 213`, `3200 pers/h`, `WBGT 27.4` en jaune, une alerte, un âge en gris.

Passer `mockScenario` à `2` : `WBGT 31.6` en rouge, trois alertes dont la première en rouge. Puis à `3` : le pied affiche « perime depuis 22 min » en rouge.

Vérifier l'entrée : ENTER rafraîchit, DOWN bascule sur la page des alertes.

- [ ] **Étape 9 : Commit**

```bash
git add garmin/cockpit-watch/source
git commit -m "feat(montre): console device app avec donnees simulees"
```

---

## Tâche 4 : Glance

**Files:**
- Modify: `garmin/cockpit-watch/source/GlanceView.mc`
- Modify: `garmin/cockpit-watch/source/CockpitApp.mc`

**Interfaces:**
- Consumes: `Cache.load`, `State.alertMax`, `State.wbgtLevel`, `Fmt.count`, `Fmt.wbgt` (Tâches 2 et 3).
- Produces: `CockpitApp.getGlanceTheme()` renvoyant un `AppBase.GlanceTheme` dérivé du niveau en cache.

- [ ] **Étape 1 : Écrire la vue de glance**

Remplacer `source/GlanceView.mc`. Elle ne fait **aucune requête réseau** et ne parse rien : `Storage` rend des types natifs déjà décodés.

```monkeyc
using Toybox.WatchUi;
using Toybox.Graphics;

(:glance)
class CockpitGlanceView extends WatchUi.GlanceView {

    function initialize() {
        GlanceView.initialize();
    }

    function onUpdate(dc) {
        var st = Cache.load();
        var h = dc.getHeight();

        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, h / 4, Graphics.FONT_GLANCE,
                    Fmt.count(st != null ? st["e"] : null),
                    Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);

        var wl = State.wbgtLevel(st);
        var al = State.alertMax(st);
        var color = Graphics.COLOR_GREEN;
        var worst = wl > al ? wl : al;
        if (worst >= 3) { color = Graphics.COLOR_RED; }
        else if (worst >= 2) { color = Graphics.COLOR_ORANGE; }
        else if (worst >= 1) { color = Graphics.COLOR_YELLOW; }

        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, (h * 3) / 4, Graphics.FONT_GLANCE,
                    "WBGT " + Fmt.wbgt(st != null ? st["w"] : null)
                        + "   alerte " + al.toString(),
                    Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
    }
}
```

- [ ] **Étape 2 : Colorer la bande d'indicateur native**

Ajouter à `CockpitApp` :

```monkeyc
    // La bande verticale que le systeme dessine a gauche de la glance. La
    // colorer coute zero pixel dessine et donne le niveau au premier regard.
    (:glance)
    function getGlanceTheme() {
        var st = Cache.load();
        var wl = State.wbgtLevel(st);
        var al = State.alertMax(st);
        var worst = wl > al ? wl : al;
        if (worst >= 3) {
            return AppBase.GLANCE_THEME_RED;
        }
        if (worst >= 2) {
            return AppBase.GLANCE_THEME_GOLD;
        }
        return AppBase.GLANCE_THEME_GREEN;
    }
```

- [ ] **Étape 3 : Vérifier dans le simulateur**

```bash
"$SDK/bin/monkeyc" -o bin/cockpit.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
"$SDK/bin/monkeydo" bin/cockpit.prg fenix8solar51mm
```

Dans le simulateur : **Simulation → Glance View**. Attendu : deux lignes lisibles dans la zone de glance (217 × 88 px sur ce device), la seconde colorée selon le niveau.

- [ ] **Étape 4 : Mesurer la mémoire de la glance**

`monkeyc` sait rendre la consommation par espace de code, sans passer par l'interface graphique du simulateur :

```bash
"$SDK/bin/monkeyc" --build-stats 0 -o bin/cockpit.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
```

La sortie donne `Data:` et `Code:` pour `Foreground`, `Background` et `Glance`. La consommation de la glance est la **somme** de ses deux lignes, à comparer aux **65 536 octets** de budget de ce device.

Référence avant cette tâche : glance 1 490 + 1 321 = 2 811 octets, soit 4,3 % du budget. Si l'ajout de la vue de glance fait bondir ce chiffre, c'est qu'un module non annoté `(:glance)` a été tiré dans le périmètre.

Consigner la valeur mesurée dans le message de commit — c'est un chiffre, pas une supposition.

- [ ] **Étape 5 : Commit**

```bash
git add garmin/cockpit-watch/source
git commit -m "feat(montre): glance trois chiffres, sans reseau (memoire mesuree: X Ko)"
```

---

## Tâche 5 : Endpoint — fonctions pures de calcul d'état

**Files:**
- Create: `watch_state.py`
- Create: `tests/test_watch_state.py`
- Create: `requirements-dev.txt`
- Create: `conftest.py` (vide, à la racine)

**Interfaces:**
- Consumes: rien.
- Produces:
  - `WBGT_DEFAULT_LEVELS = (25.0, 28.0, 30.0)`, `MAX_ALERTS = 5`, `LABEL_MAX = 24`
  - `wbgt_level(wbgt_c, thresholds=None)` → `int` 0-3
  - `entry_rate(entries_now, ts_now, entries_before, ts_before)` → `int or None`
  - `select_alerts(active, config_alerts)` → `list[dict]` avec clés `l` et `m`
  - `event_label(short, year)` → `str or None`
  - `resolve_event(config, counter_doc)` → `tuple[str or None, int or None]`

- [ ] **Étape 1 : Déclarer la dépendance de test**

`requirements-dev.txt` :

```
# Dependances de developpement uniquement. requirements.txt reste le contrat
# de production et n'est pas touche.
-r requirements.txt
pytest>=8.0.0
```

Installer :

```bash
python3 -m pip install -r requirements-dev.txt
```

Le `python3` du poste **est déjà** le venv du projet (`/Users/ludovic/Dropbox/ACO/TITAN/virtual_titan`, Python 3.12) : ne pas en créer un, ne rien activer.

Créer aussi un `conftest.py` **vide à la racine du dépôt**. Sans lui, seule la forme `python3 -m pytest` fonctionne (elle ajoute le répertoire courant à `sys.path`) et un `pytest tests/...` nu échoue en `ModuleNotFoundError: No module named 'watch_state'` — vérifié empiriquement sur ce dépôt. Un `conftest.py` racine fait ajouter la racine à `sys.path` dans les deux cas.

```bash
touch conftest.py
```

- [ ] **Étape 2 : Écrire les tests qui échouent**

`tests/test_watch_state.py` :

```python
from datetime import datetime, timedelta

import pytest

import watch_state


class TestWbgtLevel:
    def test_sous_le_premier_seuil(self):
        assert watch_state.wbgt_level(22.0) == 0

    def test_paliers_iso(self):
        assert watch_state.wbgt_level(25.0) == 1
        assert watch_state.wbgt_level(27.4) == 1
        assert watch_state.wbgt_level(28.0) == 2
        assert watch_state.wbgt_level(30.0) == 3

    def test_danger_extreme_plafonne_a_trois(self):
        # Au-dela de "suspendre le travail lourd", un cran de plus ne change
        # aucune decision au poignet.
        assert watch_state.wbgt_level(35.0) == 3

    def test_absence_de_mesure(self):
        assert watch_state.wbgt_level(None) == 0

    def test_seuils_surcharges(self):
        assert watch_state.wbgt_level(26.0, thresholds=(27.0, 29.0, 31.0)) == 0
        assert watch_state.wbgt_level(29.5, thresholds=(27.0, 29.0, 31.0)) == 2


class TestEntryRate:
    def test_debit_nominal(self):
        t1 = datetime(2026, 8, 14, 12, 0)
        t0 = t1 - timedelta(minutes=15)
        # 800 entrees en 15 min = 3200 pers/h
        assert watch_state.entry_rate(48213, t1, 47413, t0) == 3200

    def test_snapshot_manquant(self):
        t1 = datetime(2026, 8, 14, 12, 0)
        assert watch_state.entry_rate(48213, t1, None, None) is None

    def test_delta_negatif_est_none(self):
        # Remise a zero du compteur : mieux vaut rien qu'un debit absurde.
        t1 = datetime(2026, 8, 14, 12, 0)
        t0 = t1 - timedelta(minutes=15)
        assert watch_state.entry_rate(12, t1, 48213, t0) is None

    def test_ecart_nul_est_none(self):
        t1 = datetime(2026, 8, 14, 12, 0)
        assert watch_state.entry_rate(48213, t1, 47413, t1) is None


class TestSelectAlerts:
    CONFIG = [
        {"slug": "field_sos", "level": 3, "label": "SOS tablette"},
        {"slug": "meteo-vent", "level": 2, "label": "Vent fort"},
        {"slug": "opening", "level": 1, "label": "Ouverture"},
    ]

    def test_seuls_les_slugs_configures_partent(self):
        active = [
            {"definition_slug": "field_sos", "title": "SOS"},
            {"definition_slug": "checkpoint-reassign", "title": "Reaffectation"},
        ]
        out = watch_state.select_alerts(active, self.CONFIG)
        assert out == [{"l": 3, "m": "SOS tablette"}]

    def test_tri_par_niveau_decroissant(self):
        active = [
            {"definition_slug": "opening", "title": "x"},
            {"definition_slug": "field_sos", "title": "y"},
            {"definition_slug": "meteo-vent", "title": "z"},
        ]
        out = watch_state.select_alerts(active, self.CONFIG)
        assert [a["l"] for a in out] == [3, 2, 1]

    def test_libelle_tronque(self):
        config = [{"slug": "a", "level": 1,
                   "label": "Un libelle beaucoup trop long pour un poignet"}]
        out = watch_state.select_alerts([{"definition_slug": "a"}], config)
        assert out[0]["m"] == "Un libelle beaucoup trop"
        assert len(out[0]["m"]) == watch_state.LABEL_MAX

    def test_repli_sur_le_titre_si_pas_de_label(self):
        config = [{"slug": "a", "level": 2}]
        out = watch_state.select_alerts(
            [{"definition_slug": "a", "title": "ALERTE VENT"}], config)
        assert out[0]["m"] == "ALERTE VENT"

    def test_plafond_a_cinq(self):
        config = [{"slug": "s%d" % i, "level": 3} for i in range(8)]
        active = [{"definition_slug": "s%d" % i, "title": "t"} for i in range(8)]
        assert len(watch_state.select_alerts(active, config)) == 5

    def test_config_vide(self):
        assert watch_state.select_alerts([{"definition_slug": "a"}], []) == []


class TestEventLabel:
    def test_format_court(self):
        assert watch_state.event_label("24HM", 2026) == "24HM 26"

    def test_annee_sur_deux_chiffres(self):
        assert watch_state.event_label("GPE", 2025) == "GPE 25"

    def test_donnees_manquantes(self):
        assert watch_state.event_label(None, 2026) is None
        assert watch_state.event_label("24HM", None) is None


class TestResolveEvent:
    COUNTER = {"requested_event": "24H MOTOS", "year": "2026"}

    def test_mode_auto_suit_le_compteur(self):
        cfg = {"event_mode": "auto"}
        assert watch_state.resolve_event(cfg, self.COUNTER) == ("24H MOTOS", 2026)

    def test_mode_auto_convertit_l_annee_en_int(self):
        _, year = watch_state.resolve_event({}, self.COUNTER)
        assert year == 2026
        assert isinstance(year, int)

    def test_mode_epingle(self):
        cfg = {"event_mode": "pinned", "event": "24H CAMIONS", "year": 2025}
        assert watch_state.resolve_event(cfg, self.COUNTER) == ("24H CAMIONS", 2025)

    def test_auto_sans_compteur(self):
        assert watch_state.resolve_event({"event_mode": "auto"}, None) == (None, None)

    def test_annee_illisible(self):
        counter = {"requested_event": "X", "year": "n/a"}
        assert watch_state.resolve_event({}, counter) == ("X", None)
```

- [ ] **Étape 3 : Lancer les tests pour vérifier qu'ils échouent**

```bash
python3 -m pytest tests/test_watch_state.py -v
```

Attendu : ÉCHEC à la collecte, `ModuleNotFoundError: No module named 'watch_state'`.

- [ ] **Étape 4 : Écrire `watch_state.py`**

```python
"""Calcul de l'etat servi a la montre.

Fonctions pures et lectures Mongo, `db` toujours passe en argument. Aucun
import Flask : ce module se teste sans application.
"""

# Seuils WBGT en degres, replies sur l'echelle 0-3 de la montre. Ils viennent
# de SEUILS_WBGT (ISO 7243) dans meteo_thermique.py, dont le palier
# danger_extreme (33) est absorbe par le niveau 3 : au-dela de "suspendre le
# travail lourd", un cran de plus ne change aucune decision au poignet.
WBGT_DEFAULT_LEVELS = (25.0, 28.0, 30.0)

# Ce qui tient la reponse sous 2 Ko.
MAX_ALERTS = 5
LABEL_MAX = 24


def wbgt_level(wbgt_c, thresholds=None):
    """Replie un WBGT en degres sur l'echelle 0-3 de la montre."""
    if wbgt_c is None:
        return 0
    seuils = thresholds or WBGT_DEFAULT_LEVELS
    niveau = 0
    for seuil in seuils:
        if wbgt_c >= seuil:
            niveau += 1
    return min(niveau, 3)


def entry_rate(entries_now, ts_now, entries_before, ts_before):
    """Debit d'entrees en personnes/heure entre deux releves du compteur."""
    if entries_now is None or ts_now is None:
        return None
    if entries_before is None or ts_before is None:
        return None
    delta_s = (ts_now - ts_before).total_seconds()
    if delta_s <= 0:
        return None
    delta_e = entries_now - entries_before
    if delta_e < 0:
        # Remise a zero du compteur : mieux vaut rien qu'un debit absurde.
        return None
    return int(round(delta_e * 3600.0 / delta_s))


def select_alerts(active, config_alerts):
    """Filtre, note et tronque les alertes actives pour la montre.

    `cockpit_active_alerts` ne porte aucun champ de severite et le `priority`
    de `cockpit_alert_definitions` est un ordre d'affichage, pas une gravite
    (opening = 1, field_sos = 99). Le niveau vient donc exclusivement de la
    configuration, qui sert du meme coup de filtre : un slug absent ne part
    pas a la montre.
    """
    par_slug = {}
    for regle in config_alerts or []:
        slug = regle.get("slug")
        if slug:
            par_slug[slug] = regle

    sortie = []
    for doc in active or []:
        regle = par_slug.get(doc.get("definition_slug"))
        if regle is None:
            continue
        libelle = regle.get("label") or doc.get("title") or ""
        sortie.append({
            "l": int(regle.get("level", 1)),
            "m": libelle[:LABEL_MAX],
        })

    sortie.sort(key=lambda a: -a["l"])
    return sortie[:MAX_ALERTS]


def event_label(short, year):
    """Libelle court de l'evenement rapporte, du genre '24HM 26'."""
    if not short or year is None:
        return None
    return "%s %02d" % (short, int(year) % 100)


def resolve_event(config, counter_doc):
    """Retourne (event, year) : epingle si demande, sinon derive du compteur.

    En mode auto, on lit le `requested_event` / `year` du dernier releve : les
    chiffres et les alertes viennent alors forcement du meme evenement. Le doc
    global du live-controle n'est pas une source : il ne porte aucune annee et
    derive en pratique.
    """
    config = config or {}
    if config.get("event_mode") == "pinned":
        annee = config.get("year")
        try:
            annee = int(annee)
        except (TypeError, ValueError):
            annee = None
        return config.get("event"), annee

    if not counter_doc:
        return None, None
    annee = counter_doc.get("year")
    try:
        annee = int(annee)
    except (TypeError, ValueError):
        annee = None
    return counter_doc.get("requested_event"), annee
```

- [ ] **Étape 5 : Lancer les tests pour vérifier qu'ils passent**

```bash
python3 -m pytest tests/test_watch_state.py -v
```

Attendu : 22 tests passés.

- [ ] **Étape 6 : Commit**

```bash
git add watch_state.py tests/test_watch_state.py requirements-dev.txt
git commit -m "feat(montre): fonctions pures de calcul d'etat + socle pytest"
```

---

## Tâche 6 : Endpoint — lectures Mongo et assemblage du payload

**Files:**
- Modify: `watch_state.py`
- Modify: `tests/test_watch_state.py`

**Interfaces:**
- Consumes: `wbgt_level`, `entry_rate`, `select_alerts`, `event_label`, `resolve_event` (Tâche 5).
- Produces:
  - `read_config(db)` → `dict` (jamais `None` ; valeurs par défaut si le document est absent)
  - `read_counter(db, location_id)` → `dict or None`
  - `read_counter_before(db, location_id, moment)` → `dict or None`
  - `read_weather_now(db, now)` → `tuple[float or None, str or None]` (WBGT °C, niveau textuel)
  - `read_active_alerts(db, event, year, now)` → `list[dict]`
  - `read_principal_id(db)` → `str or None`
  - `read_event_short(db, event)` → `str or None`
  - `build_state(db, now)` → `dict` prêt à sérialiser

- [ ] **Étape 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_watch_state.py`. Une base factice évite toute dépendance nouvelle et tout accès à un vrai Mongo.

```python
class FakeCollection:
    """Collection Mongo minimale : juste ce que watch_state appelle."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.last_query = None

    def find_one(self, query=None, projection=None, sort=None):
        self.last_query = query
        docs = self._matching(query)
        if sort:
            cle, sens = sort[0]
            docs = sorted(docs, key=lambda d: d.get(cle),
                          reverse=(sens == -1))
        return docs[0] if docs else None

    def find(self, query=None, projection=None):
        self.last_query = query
        return list(self._matching(query))

    def _matching(self, query):
        if not query:
            return list(self.docs)
        out = []
        for doc in self.docs:
            if all(self._match(doc, cle, val) for cle, val in query.items()):
                out.append(doc)
        return out

    @staticmethod
    def _match(doc, cle, val):
        actuel = doc.get(cle)
        if isinstance(val, dict):
            if "$lte" in val and not (actuel is not None and actuel <= val["$lte"]):
                return False
            if "$gt" in val and not (actuel is not None and actuel > val["$gt"]):
                return False
            if "$in" in val and actuel not in val["$in"]:
                return False
            return True
        return actuel == val


class FakeDb:
    def __init__(self, **collections):
        self._cols = {k: FakeCollection(v) for k, v in collections.items()}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = FakeCollection([])
        return self._cols[name]


NOW = datetime(2026, 8, 14, 12, 0)


def _counter(entries, minutes_ago, event="24H MOTOS", year="2026"):
    return {
        "requested_location_id": "628",
        "requested_location_type": "Area",
        "entries": entries,
        "timestamp": NOW - timedelta(minutes=minutes_ago),
        "requested_event": event,
        "year": year,
    }


class TestReadConfig:
    def test_defauts_si_absent(self):
        db = FakeDb()
        cfg = watch_state.read_config(db)
        assert cfg["event_mode"] == "auto"
        assert cfg["alerts"] == []
        assert cfg["wbgt_levels"] == list(watch_state.WBGT_DEFAULT_LEVELS)

    def test_lit_le_document(self):
        db = FakeDb(watch_config=[{
            "_id": "watch", "event_mode": "pinned",
            "event": "24H CAMIONS", "year": 2025,
            "alerts": [{"slug": "a", "level": 2}],
            "wbgt_levels": [26, 29, 32],
        }])
        cfg = watch_state.read_config(db)
        assert cfg["event_mode"] == "pinned"
        assert cfg["wbgt_levels"] == [26, 29, 32]


class TestReadCounter:
    def test_prend_le_dernier_releve(self):
        db = FakeDb(data_access=[_counter(100, 30), _counter(150, 5)])
        doc = watch_state.read_counter(db, "628")
        assert doc["entries"] == 150

    def test_absent(self):
        assert watch_state.read_counter(FakeDb(), "628") is None

    def test_snapshot_anterieur(self):
        db = FakeDb(data_access=[
            _counter(100, 30), _counter(120, 16), _counter(150, 1),
        ])
        doc = watch_state.read_counter_before(db, "628", NOW - timedelta(minutes=15))
        assert doc["entries"] == 120


class TestReadPrincipalId:
    def test_lit_le_doc_global(self):
        db = FakeDb(data_access=[
            {"_id": "___GLOBAL___", "compteur_principal_id": "628"},
        ])
        assert watch_state.read_principal_id(db) == "628"

    def test_absent(self):
        assert watch_state.read_principal_id(FakeDb()) is None


class TestReadActiveAlerts:
    def test_ecarte_les_expirees(self):
        db = FakeDb(cockpit_active_alerts=[
            {"definition_slug": "a", "event": "24H MOTOS", "year": "2026",
             "expiresAt": NOW + timedelta(hours=1)},
            {"definition_slug": "b", "event": "24H MOTOS", "year": "2026",
             "expiresAt": NOW - timedelta(hours=1)},
        ])
        out = watch_state.read_active_alerts(db, "24H MOTOS", 2026, NOW)
        assert [a["definition_slug"] for a in out] == ["a"]

    def test_sans_evenement_ne_remonte_rien(self):
        db = FakeDb(cockpit_active_alerts=[
            {"definition_slug": "a", "expiresAt": NOW + timedelta(hours=1)},
        ])
        assert watch_state.read_active_alerts(db, None, None, NOW) == []


class TestBuildState:
    def _db(self):
        return FakeDb(
            data_access=[
                {"_id": "___GLOBAL___", "compteur_principal_id": "628"},
                _counter(47413, 15),
                _counter(48213, 0),
            ],
            watch_config=[{
                "_id": "watch", "event_mode": "auto",
                "alerts": [{"slug": "field_sos", "level": 3,
                            "label": "SOS tablette"}],
            }],
            cockpit_active_alerts=[{
                "definition_slug": "field_sos", "event": "24H MOTOS",
                "year": "2026", "title": "SOS",
                "expiresAt": NOW + timedelta(hours=1),
            }],
            evenement=[{"nom": "24H MOTOS", "short": "24HM"}],
            meteo_previsions=[{
                "Date": "2026-08-14",
                "Heures": [{"Heure": "12:00", "Temperature (C)": 30.0,
                            "Humidite (%)": 60}],
            }],
        )

    def test_payload_complet(self):
        st = watch_state.build_state(self._db(), NOW)
        assert st["e"] == 48213
        assert st["er"] == 3200
        assert st["n"] == "24HM 26"
        assert st["al"] == [{"l": 3, "m": "SOS tablette"}]
        assert st["t"] == int(NOW.timestamp())

    def test_base_vide_ne_leve_pas(self):
        st = watch_state.build_state(FakeDb(), NOW)
        assert st["e"] is None
        assert st["er"] is None
        assert st["al"] == []
        assert st["wl"] == 0

    def test_taille_sous_deux_ko(self):
        import json
        st = watch_state.build_state(self._db(), NOW)
        assert len(json.dumps(st).encode("utf-8")) < 2048
```

- [ ] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

```bash
python3 -m pytest tests/test_watch_state.py -v
```

Attendu : ÉCHEC, `AttributeError: module 'watch_state' has no attribute 'read_config'`.

- [ ] **Étape 3 : Ajouter les lectures Mongo à `watch_state.py`**

Ajouter en tête du fichier :

```python
from datetime import timedelta
```

Puis à la suite des fonctions pures :

```python
# Identifiant du document de configuration globale du live-controle.
HSH_GLOBAL_ID = "___GLOBAL___"

# Recul utilise pour calculer le debit. Assez long pour lisser le bruit d'un
# releve, assez court pour rester une photographie de l'instant.
RATE_WINDOW = timedelta(minutes=15)


def read_config(db):
    """Configuration montre, avec ses defauts. Ne renvoie jamais None."""
    doc = db["watch_config"].find_one({"_id": "watch"}) or {}
    return {
        "event_mode": doc.get("event_mode") or "auto",
        "event": doc.get("event"),
        "year": doc.get("year"),
        "alerts": doc.get("alerts") or [],
        "wbgt_levels": doc.get("wbgt_levels") or list(WBGT_DEFAULT_LEVELS),
    }


def read_principal_id(db):
    """Identifiant du compteur principal, choisi dans la page live-controle."""
    doc = db["data_access"].find_one({"_id": HSH_GLOBAL_ID}) or {}
    principal = doc.get("compteur_principal_id")
    return str(principal) if principal else None


def read_counter(db, location_id):
    """Dernier releve du compteur.

    Comme le widget compteurs du cockpit, on interroge le seul
    requested_location_id : le releve porte lui-meme son evenement et son
    annee, la donnee s'auto-identifie.
    """
    if not location_id:
        return None
    return db["data_access"].find_one(
        {"requested_location_id": str(location_id)},
        sort=[("timestamp", -1)],
    )


def read_counter_before(db, location_id, moment):
    """Releve le plus recent anterieur a `moment`, pour le calcul du debit."""
    if not location_id:
        return None
    return db["data_access"].find_one(
        {"requested_location_id": str(location_id),
         "timestamp": {"$lte": moment}},
        sort=[("timestamp", -1)],
    )


def read_event_short(db, event):
    """Sigle court de l'evenement (24H MOTOS -> 24HM)."""
    if not event:
        return None
    doc = db["evenement"].find_one({"nom": event}) or {}
    return doc.get("short")


def read_active_alerts(db, event, year, now):
    """Alertes actives non expirees de l'evenement retenu."""
    if not event or year is None:
        return []
    # `year` est stocke tantot en chaine tantot en entier selon les emetteurs.
    annees = [year, str(year)]
    docs = db["cockpit_active_alerts"].find({
        "event": event,
        "year": {"$in": annees},
    })
    return [d for d in docs
            if d.get("expiresAt") is None or d["expiresAt"] > now]


def read_weather_now(db, now):
    """WBGT du creneau horaire courant, en degres.

    Meme source que le mur meteo : le document du jour dans meteo_previsions,
    enrichi par meteo_thermique.analyser().
    """
    doc = db["meteo_previsions"].find_one({"Date": now.strftime("%Y-%m-%d")})
    if not doc:
        return None, None

    heure_courante = now.strftime("%H:00")
    entree = None
    for candidat in doc.get("Heures") or []:
        if candidat.get("Heure") == heure_courante:
            entree = candidat
            break
    if entree is None:
        return None, None

    # Les cles sont accentuees en base, avec repli non accentue : 0 est une
    # valeur legitime, on ne peut pas ecrire `.get(k) or defaut`.
    temperature = entree.get("Température (°C)")
    if temperature is None:
        temperature = entree.get("Temperature (C)")
    humidite = entree.get("Humidité (%)")
    if humidite is None:
        humidite = entree.get("Humidite (%)")
    if temperature is None or humidite is None:
        return None, None

    import meteo_thermique

    bloc = meteo_thermique.analyser(
        temperature, humidite,
        vent_kmh=entree.get("Vent moyen (km/h)"),
        heure=int(str(entree.get("Heure", "0")).split(":")[0]),
    )
    return bloc.get("wbgt_c"), bloc.get("wbgt_niveau")


def build_state(db, now):
    """Assemble le payload servi a la montre."""
    config = read_config(db)

    location_id = read_principal_id(db)
    courant = read_counter(db, location_id)
    entrees = courant.get("entries") if courant else None
    if entrees is not None:
        try:
            entrees = int(entrees)
        except (TypeError, ValueError):
            entrees = None

    horodatage = courant.get("timestamp") if courant else None

    debit = None
    if courant and horodatage is not None:
        anterieur = read_counter_before(db, location_id, horodatage - RATE_WINDOW)
        if anterieur:
            try:
                avant = int(anterieur.get("entries"))
            except (TypeError, ValueError):
                avant = None
            debit = entry_rate(entrees, horodatage,
                               avant, anterieur.get("timestamp"))

    event, year = resolve_event(config, courant)
    wbgt, _ = read_weather_now(db, now)
    actives = read_active_alerts(db, event, year, now)

    return {
        "t": int(horodatage.timestamp()) if horodatage else None,
        "n": event_label(read_event_short(db, event), year),
        "e": entrees,
        "er": debit,
        "w": round(wbgt, 1) if wbgt is not None else None,
        "wl": wbgt_level(wbgt, tuple(config["wbgt_levels"])),
        "al": select_alerts(actives, config["alerts"]),
    }
```

- [ ] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

```bash
python3 -m pytest tests/test_watch_state.py -v
```

Attendu : tous les tests passent, y compris `test_taille_sous_deux_ko`.

- [ ] **Étape 5 : Vérifier contre la vraie base**

```bash
python3 -c "
from datetime import datetime
from pymongo import MongoClient
import watch_state
db = MongoClient('mongodb://localhost:27017/')['titan_dev']
import json
print(json.dumps(watch_state.build_state(db, datetime.now()), ensure_ascii=False))
"
```

Attendu : un JSON valide. Sur la base de dev au repos, `e` vaut le dernier relevé du compteur `628` et `t` est ancien — c'est correct, il n'y a pas d'événement en cours.

- [ ] **Étape 6 : Commit**

```bash
git add watch_state.py tests/test_watch_state.py
git commit -m "feat(montre): lectures Mongo et assemblage du payload"
```

---

## Tâche 7 : Endpoint — jetons, rate limit, route `/state`

**Files:**
- Create: `watch_api.py`
- Create: `tests/test_watch_api.py`
- Modify: `app.py:1811` (enregistrement du blueprint)

**Interfaces:**
- Consumes: `watch_state.build_state` (Tâche 6).
- Produces:
  - `watch_bp` (blueprint Flask, `url_prefix="/api/v1/watch"`)
  - `hash_token(token)` → `str`
  - `issue_token(db, label, created_by)` → `str` (jeton en clair, rendu une seule fois)
  - `verify_token(db, token)` → `dict or None` (document du jeton)
  - `revoke_token(db, token_id)` → `bool`
  - `bearer_required(f)` — décorateur, répond `401` JSON, jamais de redirection
  - `CACHE_TTL_S = 20`

- [ ] **Étape 1 : Écrire les tests qui échouent**

`tests/test_watch_api.py` :

```python
import hashlib

import pytest


@pytest.fixture
def client(monkeypatch):
    """Application Flask minimale portant uniquement le blueprint montre."""
    from flask import Flask
    import watch_api

    watch_api.reset_rate_limit()
    watch_api.reset_cache()

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(watch_api.watch_bp)
    return app.test_client()


class TestHashToken:
    def test_sha256_hexadecimal(self):
        import watch_api
        assert watch_api.hash_token("abc") == hashlib.sha256(b"abc").hexdigest()


class TestBearerRequired:
    def test_sans_entete_401_json(self, client):
        rep = client.get("/api/v1/watch/state")
        assert rep.status_code == 401
        # Surtout pas une redirection : @role_required renvoie 302, ce qui
        # casserait la montre.
        assert rep.get_json() == {"ok": False, "error": "unauthorized"}

    def test_entete_malformee_401(self, client):
        rep = client.get("/api/v1/watch/state",
                         headers={"Authorization": "Token abc"})
        assert rep.status_code == 401

    def test_jeton_inconnu_401(self, client, monkeypatch):
        import watch_api
        monkeypatch.setattr(watch_api, "_db", lambda: _FakeDb())
        rep = client.get("/api/v1/watch/state",
                         headers={"Authorization": "Bearer inexistant"})
        assert rep.status_code == 401

    def test_jeton_revoque_401(self, client, monkeypatch):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": True,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        rep = client.get("/api/v1/watch/state",
                         headers={"Authorization": "Bearer secret"})
        assert rep.status_code == 401


class TestState:
    def test_jeton_valide_renvoie_le_payload(self, client, monkeypatch):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        monkeypatch.setattr(watch_api.watch_state, "build_state",
                            lambda d, n: {"t": 1, "n": "24HM 26", "e": 2,
                                          "er": 3, "w": 4.0, "wl": 1,
                                          "al": []})
        rep = client.get("/api/v1/watch/state",
                         headers={"Authorization": "Bearer secret"})
        assert rep.status_code == 200
        assert rep.get_json()["e"] == 2

    def test_le_cache_evite_un_second_calcul(self, client, monkeypatch):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        appels = {"n": 0}

        def compte(d, n):
            appels["n"] += 1
            return {"t": 1, "n": None, "e": 1, "er": None, "w": None,
                    "wl": 0, "al": []}

        monkeypatch.setattr(watch_api.watch_state, "build_state", compte)
        entetes = {"Authorization": "Bearer secret"}
        client.get("/api/v1/watch/state", headers=entetes)
        client.get("/api/v1/watch/state", headers=entetes)
        assert appels["n"] == 1


class TestRateLimit:
    def test_429_au_dela_du_plafond(self, client, monkeypatch):
        import watch_api
        db = _FakeDb(watch_tokens=[{
            "_id": "1", "token_sha256": watch_api.hash_token("secret"),
            "revoked": False,
        }])
        monkeypatch.setattr(watch_api, "_db", lambda: db)
        monkeypatch.setattr(watch_api.watch_state, "build_state",
                            lambda d, n: {"t": 1, "n": None, "e": 1,
                                          "er": None, "w": None, "wl": 0,
                                          "al": []})
        entetes = {"Authorization": "Bearer secret"}
        codes = [client.get("/api/v1/watch/state", headers=entetes).status_code
                 for _ in range(watch_api.RATE_LIMIT_MAX + 2)]
        assert codes[-1] == 429
        assert codes[0] == 200


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.updates = []

    def find_one(self, query=None, projection=None, sort=None):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in (query or {}).items()):
                return doc
        return None

    def update_one(self, query, update, upsert=False):
        self.updates.append((query, update))

    def insert_one(self, doc):
        self.docs.append(doc)

    def create_index(self, *args, **kwargs):
        pass


class _FakeDb:
    def __init__(self, **collections):
        self._cols = {k: _FakeCollection(v) for k, v in collections.items()}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeCollection([])
        return self._cols[name]
```

- [ ] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

```bash
python3 -m pytest tests/test_watch_api.py -v
```

Attendu : ÉCHEC, `ModuleNotFoundError: No module named 'watch_api'`.

- [ ] **Étape 3 : Écrire `watch_api.py`**

```python
"""Blueprint de l'API montre.

Lecture seule, authentifiee par jeton Bearer revocable. Aucun acces direct a
Mongo depuis l'exterieur : cette couche est la seule surface exposee.
"""

import hashlib
import logging
import secrets
import time
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, request

import watch_state

logger = logging.getLogger(__name__)

watch_bp = Blueprint("watch", __name__, url_prefix="/api/v1/watch")

# Cache du payload. Meme principe que traffic.py : la charge Mongo est bornee
# quel que soit le rythme de polling, sans process supplementaire.
CACHE_TTL_S = 20

# Fenetre glissante par jeton. La montre consomme 5 requetes (app a 1 min) plus
# 1 (background) : la marge absorbe les reprises reseau sans jamais couvrir une
# boucle folle.
RATE_LIMIT_WINDOW_S = 300
RATE_LIMIT_MAX = 60

# Bride d'ecriture de la telemetrie : sans elle, une montre a 1 min de polling
# produirait 1 440 ecritures par jour pour un seul champ d'horodatage.
LAST_USED_THROTTLE_S = 60

_cache = {"at": 0.0, "payload": None}
_rate_log = {}
_indexes_ready = False


def _db():
    """Base de l'environnement courant. Jamais de nom code en dur."""
    from app import db
    return db


def reset_cache():
    _cache["at"] = 0.0
    _cache["payload"] = None


def reset_rate_limit():
    _rate_log.clear()


def _ensure_indexes(db):
    global _indexes_ready
    if _indexes_ready:
        return
    db["watch_tokens"].create_index("token_sha256", unique=True)
    _indexes_ready = True


def _client_ip():
    return request.headers.get(
        "X-Forwarded-For", request.remote_addr or "0.0.0.0"
    ).split(",")[0].strip()


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(db, label, created_by):
    """Emet un jeton. Le clair est renvoye une seule fois et jamais stocke."""
    _ensure_indexes(db)
    token = secrets.token_urlsafe(32)
    db["watch_tokens"].insert_one({
        "label": label,
        "token_sha256": hash_token(token),
        "created_at": datetime.now(timezone.utc),
        "created_by": created_by,
        "revoked": False,
        "revoked_at": None,
        "last_used_at": None,
        "last_ip": None,
        "use_count": 0,
    })
    return token


def verify_token(db, token):
    """Lookup indexe sur le hash : aucune comparaison de secrets."""
    if not token:
        return None
    doc = db["watch_tokens"].find_one({"token_sha256": hash_token(token)})
    if not doc or doc.get("revoked"):
        return None
    return doc


def revoke_token(db, token_id):
    from bson import ObjectId
    try:
        oid = ObjectId(token_id)
    except Exception:
        return False
    res = db["watch_tokens"].update_one(
        {"_id": oid},
        {"$set": {"revoked": True,
                  "revoked_at": datetime.now(timezone.utc)}},
    )
    return bool(getattr(res, "matched_count", 1))


def _touch_token(db, doc):
    """Telemetrie d'usage, bridee a une ecriture par minute."""
    dernier = doc.get("last_used_at")
    maintenant = datetime.now(timezone.utc)
    if dernier is not None:
        if (maintenant - dernier).total_seconds() < LAST_USED_THROTTLE_S:
            return
    db["watch_tokens"].update_one(
        {"_id": doc["_id"]},
        {"$set": {"last_used_at": maintenant, "last_ip": _client_ip()},
         "$inc": {"use_count": 1}},
    )


def _rate_limited(cle):
    maintenant = time.time()
    debut = maintenant - RATE_LIMIT_WINDOW_S
    hist = [t for t in _rate_log.get(cle, []) if t > debut]
    if len(hist) >= RATE_LIMIT_MAX:
        _rate_log[cle] = hist
        return True
    hist.append(maintenant)
    _rate_log[cle] = hist
    if len(_rate_log) > 1000:
        mortes = [k for k, v in _rate_log.items()
                  if not any(t > debut for t in v)]
        for k in mortes:
            _rate_log.pop(k, None)
    return False


def bearer_required(f):
    """Auth par jeton. Repond 401 en JSON, jamais 302.

    @role_required redirige vers le portail, ce qui casserait un appel machine.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        entete = request.headers.get("Authorization", "")
        if not entete.startswith("Bearer "):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        token = entete[7:].strip()

        db = _db()
        doc = verify_token(db, token)
        if doc is None:
            logger.info("Jeton montre refuse depuis %s", _client_ip())
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        if _rate_limited(doc["token_sha256"]):
            reponse = jsonify({"ok": False, "error": "rate_limited"})
            reponse.headers["Retry-After"] = str(RATE_LIMIT_WINDOW_S)
            return reponse, 429

        _touch_token(db, doc)
        request.watch_token = doc
        return f(*args, **kwargs)

    return wrapper


@watch_bp.route("/state", methods=["GET"])
@bearer_required
def state():
    maintenant = time.time()
    if _cache["payload"] is not None and maintenant - _cache["at"] < CACHE_TTL_S:
        return jsonify(_cache["payload"])

    payload = watch_state.build_state(_db(), datetime.now())
    _cache["payload"] = payload
    _cache["at"] = maintenant
    return jsonify(payload)
```

- [ ] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

```bash
python3 -m pytest tests/test_watch_api.py -v
```

Attendu : tous les tests passent.

- [ ] **Étape 5 : Enregistrer le blueprint**

Dans `app.py`, juste après la ligne 1811 (`app.register_blueprint(meteo_bp)`) :

```python
from watch_api import watch_bp
app.register_blueprint(watch_bp)
# Pas de csrf.exempt(watch_bp) : /state est un GET, que Flask-WTF ne protege
# pas, et les routes admin qui ecrivent doivent garder la protection.
```

- [ ] **Étape 6 : Vérifier de bout en bout contre la vraie base**

```bash
CODING=true python3 app.py &
sleep 5
TOKEN=$(python3 -c "
from pymongo import MongoClient
import watch_api
db = MongoClient('mongodb://localhost:27017/')['titan_dev']
print(watch_api.issue_token(db, 'test local', 'plan'))
")
curl -s -i "http://127.0.0.1:5008/api/v1/watch/state" \
  -H "Authorization: Bearer $TOKEN" | head -20
echo
curl -s -o /dev/null -w "sans jeton: %{http_code}\n" \
  "http://127.0.0.1:5008/api/v1/watch/state"
```

Attendu : `200` avec le JSON pour le premier appel, `401` pour le second. Vérifier que le corps fait moins de 2 Ko.

- [ ] **Étape 7 : Commit**

```bash
git add watch_api.py tests/test_watch_api.py app.py
git commit -m "feat(montre): endpoint /api/v1/watch/state, jetons revocables, rate limit"
```

---

## Tâche 8 : Routes admin et page de configuration

**Files:**
- Modify: `watch_api.py`
- Modify: `tests/test_watch_api.py`
- Create: `templates/watch_admin.html`
- Create: `static/js/watch_admin.js`
- Modify: `app.py` (route de la page)

**Interfaces:**
- Consumes: `issue_token`, `revoke_token`, `watch_state.read_config` (Tâches 6 et 7).
- Produces: `GET`/`POST /api/v1/watch/admin/tokens`, `POST /api/v1/watch/admin/tokens/<id>/revoke`, `GET`/`PUT /api/v1/watch/admin/config`, page `/watch-admin`.

- [ ] **Étape 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_watch_api.py` :

```python
class TestAdminConfigValidation:
    def test_niveau_hors_bornes_refuse(self):
        import watch_api
        ok, erreur = watch_api.validate_config({
            "event_mode": "auto",
            "alerts": [{"slug": "a", "level": 7}],
            "wbgt_levels": [25, 28, 30],
        })
        assert ok is False
        assert erreur == "level_invalide"

    def test_mode_inconnu_refuse(self):
        import watch_api
        ok, erreur = watch_api.validate_config({"event_mode": "parfois"})
        assert ok is False
        assert erreur == "event_mode_invalide"

    def test_epingle_sans_evenement_refuse(self):
        import watch_api
        ok, erreur = watch_api.validate_config({
            "event_mode": "pinned", "event": "", "year": 2026})
        assert ok is False
        assert erreur == "event_requis"

    def test_seuils_non_croissants_refuses(self):
        import watch_api
        ok, erreur = watch_api.validate_config({
            "event_mode": "auto", "wbgt_levels": [30, 28, 25]})
        assert ok is False
        assert erreur == "wbgt_levels_invalides"

    def test_configuration_valide(self):
        import watch_api
        ok, erreur = watch_api.validate_config({
            "event_mode": "pinned", "event": "24H MOTOS", "year": 2026,
            "alerts": [{"slug": "field_sos", "level": 3, "label": "SOS"}],
            "wbgt_levels": [25, 28, 30],
        })
        assert ok is True
        assert erreur is None
```

- [ ] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

```bash
python3 -m pytest tests/test_watch_api.py -k Validation -v
```

Attendu : ÉCHEC, `validate_config` n'existe pas.

- [ ] **Étape 3 : Ajouter la validation et les routes admin à `watch_api.py`**

```python
EVENT_MODES = ("auto", "pinned")


def validate_config(payload):
    """Valide une configuration montre. Retourne (ok, code_erreur)."""
    mode = payload.get("event_mode", "auto")
    if mode not in EVENT_MODES:
        return False, "event_mode_invalide"

    if mode == "pinned":
        if not payload.get("event"):
            return False, "event_requis"
        try:
            int(payload.get("year"))
        except (TypeError, ValueError):
            return False, "year_requis"

    for regle in payload.get("alerts") or []:
        if not regle.get("slug"):
            return False, "slug_requis"
        try:
            niveau = int(regle.get("level"))
        except (TypeError, ValueError):
            return False, "level_invalide"
        if niveau < 1 or niveau > 3:
            return False, "level_invalide"

    seuils = payload.get("wbgt_levels")
    if seuils is not None:
        if len(seuils) != 3:
            return False, "wbgt_levels_invalides"
        try:
            valeurs = [float(s) for s in seuils]
        except (TypeError, ValueError):
            return False, "wbgt_levels_invalides"
        if valeurs != sorted(valeurs) or len(set(valeurs)) != 3:
            return False, "wbgt_levels_invalides"

    return True, None


def _admin_guard():
    """Renvoie une reponse d'erreur si l'appelant n'est pas admin, sinon None."""
    import jwt as pyjwt
    from app import CODING, JWT_SECRET, JWT_ALGORITHM, APP_KEY

    if CODING:
        return None

    token = request.cookies.get("access_token")
    if not token:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except pyjwt.InvalidTokenError:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    role = (payload.get("roles_by_app") or {}).get(APP_KEY)
    if role != "admin":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return None


def _admin_identity():
    from app import CODING
    if CODING:
        return "dev"
    import jwt as pyjwt
    from app import JWT_SECRET, JWT_ALGORITHM
    token = request.cookies.get("access_token") or ""
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except pyjwt.InvalidTokenError:
        return "inconnu"
    return payload.get("email") or "inconnu"


@watch_bp.route("/admin/tokens", methods=["GET"])
def admin_list_tokens():
    refus = _admin_guard()
    if refus:
        return refus
    db = _db()
    docs = db["watch_tokens"].find({}, {"token_sha256": 0})
    sortie = []
    for doc in docs:
        doc["_id"] = str(doc["_id"])
        for cle in ("created_at", "revoked_at", "last_used_at"):
            valeur = doc.get(cle)
            if hasattr(valeur, "isoformat"):
                doc[cle] = valeur.isoformat()
        sortie.append(doc)
    return jsonify({"ok": True, "tokens": sortie})


@watch_bp.route("/admin/tokens", methods=["POST"])
def admin_create_token():
    refus = _admin_guard()
    if refus:
        return refus
    donnees = request.get_json(silent=True) or {}
    label = (donnees.get("label") or "").strip()
    if not label:
        return jsonify({"ok": False, "error": "label_requis"}), 400
    token = issue_token(_db(), label, _admin_identity())
    # Le clair n'est renvoye qu'ici, une seule fois.
    return jsonify({"ok": True, "token": token})


@watch_bp.route("/admin/tokens/<token_id>/revoke", methods=["POST"])
def admin_revoke_token(token_id):
    refus = _admin_guard()
    if refus:
        return refus
    if not revoke_token(_db(), token_id):
        return jsonify({"ok": False, "error": "introuvable"}), 404
    return jsonify({"ok": True})


@watch_bp.route("/admin/config", methods=["GET"])
def admin_get_config():
    refus = _admin_guard()
    if refus:
        return refus
    db = _db()
    config = watch_state.read_config(db)
    definitions = list(db["cockpit_alert_definitions"].find(
        {}, {"_id": 0, "slug": 1, "name": 1, "enabled": 1}))
    evenements = list(db["evenement"].find({}, {"_id": 0, "nom": 1, "short": 1}))
    return jsonify({"ok": True, "config": config,
                    "definitions": definitions, "evenements": evenements})


@watch_bp.route("/admin/config", methods=["PUT"])
def admin_put_config():
    refus = _admin_guard()
    if refus:
        return refus
    donnees = request.get_json(silent=True) or {}
    ok, erreur = validate_config(donnees)
    if not ok:
        return jsonify({"ok": False, "error": erreur}), 400

    maj = {
        "event_mode": donnees.get("event_mode", "auto"),
        "event": donnees.get("event"),
        "year": int(donnees["year"]) if donnees.get("year") else None,
        "alerts": donnees.get("alerts") or [],
        "wbgt_levels": donnees.get("wbgt_levels")
                       or list(watch_state.WBGT_DEFAULT_LEVELS),
        "updated_at": datetime.now(timezone.utc),
        "updated_by": _admin_identity(),
    }
    _db()["watch_config"].update_one({"_id": "watch"}, {"$set": maj},
                                     upsert=True)
    reset_cache()
    return jsonify({"ok": True})
```

- [ ] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

```bash
python3 -m pytest tests/ -v
```

Attendu : tous les tests passent.

- [ ] **Étape 5 : Créer la page admin**

⚠️ **Ce projet n'a aucun `base.html` et n'utilise `{% extends %}` nulle part** —
vérifié sur ses 24 templates. Chaque page est un document HTML autonome, avec
`<meta name="csrf-token" content="{{ csrf_token() }}">` en ligne 6, les
feuilles de style du projet, et `{% include '_sidebar.html' %}` pour la
navigation. **Ouvrir `templates/wiki_admin.html` et reprendre exactement sa
structure d'en-tête et de layout** (c'est la page admin la plus proche, et la
plus courte). Le fragment ci-dessous ne donne que le contenu propre à la
montre, à insérer dans cette coquille.

`templates/watch_admin.html` :

```html
<div class="watch-admin">
  <h1>Montre cockpit</h1>

  <section id="watch-tokens">
    <h2>Jetons</h2>
    <form id="watch-token-form">
      <input type="text" id="watch-token-label" placeholder="Libelle du jeton"
             maxlength="60" required>
      <button type="submit">Emettre</button>
    </form>
    <p id="watch-token-clear" hidden></p>
    <table id="watch-token-table">
      <thead>
        <tr><th>Libelle</th><th>Cree le</th><th>Derniere utilisation</th>
            <th>IP</th><th>Etat</th><th></th></tr>
      </thead>
      <tbody></tbody>
    </table>
  </section>

  <section id="watch-event">
    <h2>Evenement rapporte</h2>
    <label><input type="radio" name="watch-mode" value="auto"> Suivre le compteur</label>
    <label><input type="radio" name="watch-mode" value="pinned"> Epingler</label>
    <select id="watch-event-select" disabled></select>
    <input type="number" id="watch-year" min="2000" max="2100" disabled>
  </section>

  <section id="watch-alerts">
    <h2>Alertes envoyees a la montre</h2>
    <table id="watch-alert-table">
      <thead>
        <tr><th>Envoyer</th><th>Definition</th><th>Niveau</th><th>Libelle court</th></tr>
      </thead>
      <tbody></tbody>
    </table>
    <h3>Seuils WBGT</h3>
    <input type="number" id="watch-wbgt-1" step="0.5">
    <input type="number" id="watch-wbgt-2" step="0.5">
    <input type="number" id="watch-wbgt-3" step="0.5">
  </section>

  <button id="watch-save">Enregistrer</button>
  <p id="watch-status" role="status"></p>
</div>
<script src="{{ url_for('static', filename='js/watch_admin.js') }}"></script>
```

- [ ] **Étape 6 : Créer le JS**

`static/js/watch_admin.js`, IIFE autonome sur le modèle de `vision_admin.js` :

```javascript
(function () {
  'use strict';

  var API = '/api/v1/watch/admin';
  var state = { config: null, definitions: [], evenements: [] };

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function say(message, erreur) {
    var el = document.getElementById('watch-status');
    el.textContent = message;
    el.className = erreur ? 'error' : 'ok';
  }

  function request(method, url, body) {
    var options = {
      method: method,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      credentials: 'same-origin'
    };
    if (body) { options.body = JSON.stringify(body); }
    return fetch(url, options).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok || j.ok === false) { throw new Error(j.error || r.status); }
        return j;
      });
    });
  }

  function renderTokens(tokens) {
    var tbody = document.querySelector('#watch-token-table tbody');
    tbody.innerHTML = tokens.map(function (t) {
      var etat = t.revoked ? 'revoque' : 'actif';
      var bouton = t.revoked ? ''
        : '<button data-revoke="' + esc(t._id) + '">Revoquer</button>';
      return '<tr><td>' + esc(t.label) + '</td><td>' + esc(t.created_at) +
             '</td><td>' + esc(t.last_used_at || '--') + '</td><td>' +
             esc(t.last_ip || '--') + '</td><td>' + etat + '</td><td>' +
             bouton + '</td></tr>';
    }).join('');
  }

  function renderAlerts() {
    var choisis = {};
    (state.config.alerts || []).forEach(function (a) { choisis[a.slug] = a; });
    var tbody = document.querySelector('#watch-alert-table tbody');
    tbody.innerHTML = state.definitions.map(function (d) {
      var regle = choisis[d.slug];
      var coche = regle ? ' checked' : '';
      var niveau = regle ? regle.level : 1;
      var label = regle && regle.label ? regle.label : d.name;
      return '<tr><td><input type="checkbox" data-slug="' + esc(d.slug) + '"' +
             coche + '></td><td>' + esc(d.name) + '</td>' +
             '<td><select data-level="' + esc(d.slug) + '">' +
             [1, 2, 3].map(function (n) {
               return '<option value="' + n + '"' +
                      (n === niveau ? ' selected' : '') + '>' + n + '</option>';
             }).join('') + '</select></td>' +
             '<td><input type="text" maxlength="24" data-label="' +
             esc(d.slug) + '" value="' + esc(label) + '"></td></tr>';
    }).join('');
  }

  function renderEvent() {
    var select = document.getElementById('watch-event-select');
    select.innerHTML = state.evenements.map(function (e) {
      return '<option value="' + esc(e.nom) + '"' +
             (e.nom === state.config.event ? ' selected' : '') + '>' +
             esc(e.nom) + '</option>';
    }).join('');
    document.getElementById('watch-year').value = state.config.year || '';
    var mode = state.config.event_mode || 'auto';
    document.querySelectorAll('input[name="watch-mode"]').forEach(function (r) {
      r.checked = (r.value === mode);
    });
    toggleEventInputs(mode);

    var seuils = state.config.wbgt_levels || [25, 28, 30];
    document.getElementById('watch-wbgt-1').value = seuils[0];
    document.getElementById('watch-wbgt-2').value = seuils[1];
    document.getElementById('watch-wbgt-3').value = seuils[2];
  }

  function toggleEventInputs(mode) {
    var epingle = (mode === 'pinned');
    document.getElementById('watch-event-select').disabled = !epingle;
    document.getElementById('watch-year').disabled = !epingle;
  }

  function collect() {
    var alerts = [];
    document.querySelectorAll('[data-slug]').forEach(function (cb) {
      if (!cb.checked) { return; }
      var slug = cb.getAttribute('data-slug');
      alerts.push({
        slug: slug,
        level: parseInt(
          document.querySelector('[data-level="' + slug + '"]').value, 10),
        label: document.querySelector('[data-label="' + slug + '"]').value
      });
    });
    var mode = document.querySelector('input[name="watch-mode"]:checked').value;
    return {
      event_mode: mode,
      event: mode === 'pinned'
        ? document.getElementById('watch-event-select').value : null,
      year: mode === 'pinned'
        ? parseInt(document.getElementById('watch-year').value, 10) : null,
      alerts: alerts,
      wbgt_levels: [
        parseFloat(document.getElementById('watch-wbgt-1').value),
        parseFloat(document.getElementById('watch-wbgt-2').value),
        parseFloat(document.getElementById('watch-wbgt-3').value)
      ]
    };
  }

  function load() {
    request('GET', API + '/config').then(function (j) {
      state.config = j.config;
      state.definitions = j.definitions;
      state.evenements = j.evenements;
      renderEvent();
      renderAlerts();
    }).catch(function (e) { say('Chargement impossible : ' + e.message, true); });

    request('GET', API + '/tokens').then(function (j) {
      renderTokens(j.tokens);
    }).catch(function (e) { say('Jetons illisibles : ' + e.message, true); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    load();

    document.getElementById('watch-token-form')
      .addEventListener('submit', function (ev) {
        ev.preventDefault();
        var label = document.getElementById('watch-token-label').value;
        request('POST', API + '/tokens', { label: label }).then(function (j) {
          var p = document.getElementById('watch-token-clear');
          p.hidden = false;
          p.textContent = 'Jeton (affiche une seule fois) : ' + j.token;
          document.getElementById('watch-token-label').value = '';
          load();
        }).catch(function (e) { say('Emission refusee : ' + e.message, true); });
      });

    document.querySelector('#watch-token-table')
      .addEventListener('click', function (ev) {
        var id = ev.target.getAttribute && ev.target.getAttribute('data-revoke');
        if (!id) { return; }
        request('POST', API + '/tokens/' + id + '/revoke').then(function () {
          say('Jeton revoque.');
          load();
        }).catch(function (e) { say('Revocation refusee : ' + e.message, true); });
      });

    document.querySelectorAll('input[name="watch-mode"]').forEach(function (r) {
      r.addEventListener('change', function () { toggleEventInputs(r.value); });
    });

    document.getElementById('watch-save').addEventListener('click', function () {
      request('PUT', API + '/config', collect()).then(function () {
        say('Configuration enregistree.');
      }).catch(function (e) { say('Enregistrement refuse : ' + e.message, true); });
    });
  });
}());
```

- [ ] **Étape 7 : Ajouter la route de la page**

Dans `app.py`, à côté des autres routes de page (par exemple près de `@app.route('/live-controle')`, ligne 6784) :

```python
@app.route('/watch-admin')
@role_required("admin")
def watch_admin_page():
    return render_template('watch_admin.html')
```

- [ ] **Étape 8 : Vérifier la page**

```bash
CODING=true python3 app.py &
sleep 5
open http://127.0.0.1:5008/watch-admin
```

Attendu : émission d'un jeton (le clair s'affiche une fois), bascule auto/épinglé, cases d'alertes cochables avec niveau et libellé, enregistrement qui répond « Configuration enregistree. » Vérifier ensuite en base que `watch_config` contient bien ce qui a été saisi.

- [ ] **Étape 9 : Commit**

```bash
git add watch_api.py tests/test_watch_api.py templates/watch_admin.html \
        static/js/watch_admin.js app.py
git commit -m "feat(montre): routes admin et page de configuration"
```

---

## Tâche 9 : Requête réseau réelle côté montre

**Files:**
- Create: `garmin/cockpit-watch/source/Api.mc`
- Modify: `garmin/cockpit-watch/source/CockpitView.mc`

**Interfaces:**
- Consumes: `Cache.save` (Tâche 2), `Mock.state` (Tâche 3).
- Produces:
  - `Api.fetch(callback)` → `Void` ; `callback` reçoit `(ok, st)` où `st` est un dictionnaire au schéma du cache
  - `Api.toCacheDict(data, nowSec)` → `Dictionary` (conversion payload HTTP → schéma du cache)

- [ ] **Étape 1 : Écrire les tests de conversion qui échouent**

Créer `source/ApiTest.mc` :

```monkeyc
using Toybox.Test;

(:test)
function testToCacheCompactsAlerts(logger) {
    var data = {"t" => 100, "n" => "24HM 26", "e" => 5, "er" => 6,
                "w" => 27.4, "wl" => 1,
                "al" => [{"l" => 3, "m" => "SOS"}, {"l" => 1, "m" => "x"}]};
    var st = Api.toCacheDict(data, 200);
    Test.assertEqual(st["al"].size(), 2);
    Test.assertEqual(st["al"][0][0], 3);
    Test.assertEqual(st["al"][0][1], "SOS");
    return true;
}

(:test)
function testToCacheStampsResponseTime(logger) {
    var st = Api.toCacheDict({"t" => 100}, 200);
    Test.assertEqual(st["rx"], 200);
    Test.assertEqual(st["t"], 100);
    return true;
}

(:test)
function testToCacheHandlesMissingAlerts(logger) {
    var st = Api.toCacheDict({"t" => 100}, 200);
    Test.assertEqual(st["al"].size(), 0);
    return true;
}
```

- [ ] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

```bash
cd garmin/cockpit-watch
"$SDK/bin/monkeyc" -t -o bin/test.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
```

Attendu : ÉCHEC, `Api` n'existe pas.

- [ ] **Étape 3 : Écrire `Api.mc`**

```monkeyc
using Toybox.Communications;
using Toybox.Application;
using Toybox.Time;

(:background)
module Api {

    hidden var mCallback = null;

    // Le payload HTTP porte les alertes en dictionnaires ; le cache les stocke
    // en tableaux, moitie moins d'octets et d'objets a instancier au reveil de
    // la glance.
    function toCacheDict(data, nowSec) {
        var al = [];
        var brut = (data != null) ? data["al"] : null;
        if (brut != null) {
            for (var i = 0; i < brut.size(); i += 1) {
                al.add([brut[i]["l"], brut[i]["m"]]);
            }
        }
        return {
            "t" => (data != null) ? data["t"] : null,
            "n" => (data != null) ? data["n"] : null,
            "e" => (data != null) ? data["e"] : null,
            "er" => (data != null) ? data["er"] : null,
            "w" => (data != null) ? data["w"] : null,
            "wl" => (data != null && data["wl"] != null) ? data["wl"] : 0,
            "al" => al,
            "rx" => nowSec,
            "ok" => true
        };
    }

    function fetch(callback) {
        mCallback = callback;

        var mock = Application.Properties.getValue("mockData");
        if (mock != null && mock) {
            var scenario = Application.Properties.getValue("mockScenario");
            if (scenario == null) { scenario = 0; }
            callback.invoke(true, Mock.state(scenario, Time.now().value()));
            return;
        }

        var host = Application.Properties.getValue("host");
        var token = Application.Properties.getValue("token");
        if (host == null || host.length() == 0
            || token == null || token.length() == 0) {
            callback.invoke(false, null);
            return;
        }

        // HTTPS obligatoire : makeWebRequest refuse un certificat auto-signe.
        var url = "https://" + host + "/api/v1/watch/state";
        Communications.makeWebRequest(
            url,
            {},
            {
                :method => Communications.HTTP_REQUEST_METHOD_GET,
                :headers => {
                    "Authorization" => "Bearer " + token
                },
                :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
            },
            method(:onReceive)
        );
    }

    function onReceive(responseCode, data) {
        if (responseCode != 200 || data == null) {
            if (mCallback != null) {
                mCallback.invoke(false, null);
            }
            return;
        }
        var st = toCacheDict(data, Time.now().value());
        if (mCallback != null) {
            mCallback.invoke(true, st);
        }
    }
}
```

- [ ] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

```bash
"$SDK/bin/monkeyc" -t -o bin/test.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
"$SDK/bin/monkeydo" bin/test.prg fenix8solar51mm -t
```

Attendu : tous les tests passent, y compris ceux des tâches 2 et 3.

- [ ] **Étape 5 : Brancher la vue sur le réseau**

Dans `CockpitView.mc`, remplacer la méthode `refresh` :

```monkeyc
    function refresh() {
        Api.fetch(method(:onFetched));
    }

    function onFetched(ok, st) {
        if (ok && st != null) {
            Cache.save(st);
        }
        // En cas d'echec on garde le cache : l'age affiche dira lui-meme
        // depuis combien de temps la donnee n'a pas bouge.
        mState = Cache.load();
        WatchUi.requestUpdate();
    }
```

Supprimer de `CockpitView.mc` les imports et le bloc `mockData` désormais portés par `Api.fetch`.

- [ ] **Étape 6 : Vérifier dans le simulateur contre le backend local**

Le simulateur sait joindre `localhost`. Émettre un jeton, le saisir dans les réglages, et pointer `host` sur le serveur de dev.

```bash
CODING=true python3 app.py &
```

Dans le simulateur : **Settings → Application Settings**, `mockData` à faux, `host` à `127.0.0.1:5008`, `token` au jeton émis. Attendu : les chiffres réels s'affichent.

⚠️ Cette étape passe en HTTP, ce que le simulateur tolère. **La montre réelle exige HTTPS avec certificat valide.**

- [ ] **Étape 7 : Commit**

```bash
git add garmin/cockpit-watch/source
git commit -m "feat(montre): requete reseau reelle vers /api/v1/watch/state"
```

---

## Tâche 10 : Service de fond et alertes sur transition

**Files:**
- Create: `garmin/cockpit-watch/source/Alerting.mc`
- Create: `garmin/cockpit-watch/source/AlertingTest.mc`
- Modify: `garmin/cockpit-watch/source/BgService.mc`
- Modify: `garmin/cockpit-watch/source/CockpitApp.mc`

**Interfaces:**
- Consumes: `Cache.load`, `Cache.save`, `State.alertMax`, `State.wbgtLevel` (Tâche 2), `Api.fetch` (Tâche 9).
- Produces:
  - `Alerting.KEY_WL = "lwl"`, `Alerting.KEY_AL = "lal"`
  - `Alerting.shouldAlert(previousLevel, newLevel)` → `Boolean`
  - `Alerting.check(st)` → `Boolean` (vrai si une alerte a été déclenchée)
  - `Alerting.reset()` → `Void`

- [ ] **Étape 1 : Écrire les tests qui échouent**

`source/AlertingTest.mc` :

```monkeyc
using Toybox.Test;
using Toybox.Application;

(:test)
function testAlertsOnRise(logger) {
    Test.assertEqual(Alerting.shouldAlert(0, 1), true);
    Test.assertEqual(Alerting.shouldAlert(1, 3), true);
    return true;
}

(:test)
function testSilentOnFallOrPlateau(logger) {
    Test.assertEqual(Alerting.shouldAlert(2, 2), false);
    Test.assertEqual(Alerting.shouldAlert(3, 1), false);
    return true;
}

(:test)
function testFirstReadingDoesNotAlert(logger) {
    // Sans reference anterieure, on ne vibre pas : sinon la premiere synchro
    // apres installation reveille le porteur pour rien.
    Test.assertEqual(Alerting.shouldAlert(null, 2), false);
    return true;
}

(:test)
function testCheckMemorisesLevels(logger) {
    Alerting.reset();
    Alerting.check({"wl" => 1, "al" => []});
    Test.assertEqual(Application.Storage.getValue(Alerting.KEY_WL), 1);
    Test.assertEqual(Application.Storage.getValue(Alerting.KEY_AL), 0);
    return true;
}

(:test)
function testCheckTriggersOnceOnly(logger) {
    Alerting.reset();
    Alerting.check({"wl" => 0, "al" => []});
    var premier = Alerting.check({"wl" => 2, "al" => []});
    var second = Alerting.check({"wl" => 2, "al" => []});
    Test.assertEqual(premier, true);
    Test.assertEqual(second, false);
    return true;
}

(:test)
function testAlertLevelAlsoTriggers(logger) {
    Alerting.reset();
    Alerting.check({"wl" => 0, "al" => []});
    var declenche = Alerting.check({"wl" => 0, "al" => [[3, "SOS"]]});
    Test.assertEqual(declenche, true);
    return true;
}
```

- [ ] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

```bash
cd garmin/cockpit-watch
"$SDK/bin/monkeyc" -t -o bin/test.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
```

Attendu : ÉCHEC, `Alerting` n'existe pas.

- [ ] **Étape 3 : Écrire `Alerting.mc`**

```monkeyc
using Toybox.Application;
using Toybox.Attention;

(:background)
module Alerting {

    const KEY_WL = "lwl";
    const KEY_AL = "lal";

    function reset() {
        Application.Storage.deleteValue(KEY_WL);
        Application.Storage.deleteValue(KEY_AL);
    }

    // On ne vibre que sur franchissement a la hausse. Une redescente ou un
    // plateau reste silencieux, sinon l'alerte devient un bruit de fond qu'on
    // finit par ignorer.
    function shouldAlert(previousLevel, newLevel) {
        if (previousLevel == null) {
            return false;
        }
        if (newLevel == null) {
            return false;
        }
        return newLevel > previousLevel;
    }

    function check(st) {
        var wl = State.wbgtLevel(st);
        var al = State.alertMax(st);

        var lastWl = Application.Storage.getValue(KEY_WL);
        var lastAl = Application.Storage.getValue(KEY_AL);

        var declenche = shouldAlert(lastWl, wl) || shouldAlert(lastAl, al);

        Application.Storage.setValue(KEY_WL, wl);
        Application.Storage.setValue(KEY_AL, al);

        if (declenche) {
            buzz(wl > al ? wl : al);
        }
        return declenche;
    }

    // Isole exprès : si la vibration depuis le service de fond se revele
    // inoperante sur la montre reelle, le repli tient dans cette fonction.
    hidden function buzz(level) {
        var actif = Application.Properties.getValue("alertVibrate");
        if (actif != null && !actif) {
            return;
        }
        if (Attention has :vibrate) {
            var profil = [new Attention.VibeProfile(100, level >= 3 ? 800 : 400)];
            Attention.vibrate(profil);
        }
        if (Attention has :playTone) {
            Attention.playTone(level >= 3 ? Attention.TONE_ALERT_HI
                                          : Attention.TONE_ALERT_LO);
        }
    }
}
```

- [ ] **Étape 4 : Écrire le service de fond**

Remplacer `source/BgService.mc` :

```monkeyc
using Toybox.System;
using Toybox.Background;

(:background)
class CockpitService extends System.ServiceDelegate {

    function initialize() {
        ServiceDelegate.initialize();
    }

    function onTemporalEvent() {
        Api.fetch(method(:onFetched));
    }

    function onFetched(ok, st) {
        if (ok && st != null) {
            Cache.save(st);
            Alerting.check(st);
        }
        Background.exit(null);
    }
}
```

- [ ] **Étape 5 : Enregistrer l'événement temporel**

Dans `CockpitApp.mc`, ajouter les imports et modifier `onStart` :

```monkeyc
using Toybox.Background;
using Toybox.Time;
```

```monkeyc
    function onStart(state) {
        // 5 minutes est le plancher impose par la plateforme, pas un choix :
        // "Temporal events cannot be set to occur less than 5 minutes after
        // the last temporal event occurred". Un seul evenement temporel peut
        // etre enregistre a la fois.
        if (Toybox has :Background) {
            Background.registerForTemporalEvent(new Time.Duration(300));
        }
    }
```

- [ ] **Étape 6 : Lancer les tests pour vérifier qu'ils passent**

```bash
"$SDK/bin/connectiq" &
sleep 5
"$SDK/bin/monkeyc" -t -o bin/test.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
"$SDK/bin/monkeydo" bin/test.prg fenix8solar51mm -t
```

Attendu : les 6 tests d'`Alerting` passent, ainsi que tous les précédents.

- [ ] **Étape 7 : Mesurer la mémoire du service de fond**

```bash
"$SDK/bin/monkeyc" --build-stats 0 -o bin/cockpit.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
```

La consommation du service de fond est la **somme** des lignes `Background` de `Data:` et `Code:`, à comparer aux **65 536 octets** de budget de ce device. Référence avant les tâches 9 et 10 : 1 426 + 1 244 = 2 670 octets, soit 4,1 %.

`Api.mc` et `Alerting.mc` étant tous deux `(:background)`, ce chiffre va monter — c'est attendu. Ce qui ne le serait pas, c'est de voir le poids de la vue ou du formatage y apparaître. Consigner la valeur dans le message de commit.

- [ ] **Étape 8 : Commit**

```bash
git add garmin/cockpit-watch/source
git commit -m "feat(montre): service de fond 5 min et alerte sur transition montante (memoire: X Ko)"
```

---

## Tâche 11 : Validation sur la montre réelle

Cette tâche ne produit pas de code : elle lève les deux incertitudes que le simulateur ne peut pas trancher.

**Files:** aucun (constats à consigner dans le README en Tâche 12).

- [ ] **Étape 1 : Vérifier le certificat du domaine public**

```bash
echo | openssl s_client -connect cockpit.lemans.org:443 -servername cockpit.lemans.org 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

Attendu : un certificat non expiré émis par une autorité reconnue. `makeWebRequest` refuse un auto-signé — si l'émetteur est interne, la montre ne joindra jamais l'endpoint et il faut régler ça avant d'aller plus loin.

- [ ] **Étape 2 : Sideload sur la montre**

```bash
cd garmin/cockpit-watch
"$SDK/bin/monkeyc" -o bin/cockpit.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm -r
```

Brancher la montre en USB, attendre son montage, puis :

```bash
cp bin/cockpit.prg /Volumes/GARMIN/GARMIN/APPS/
diskutil eject /Volumes/GARMIN
```

Le drapeau `-r` produit un build de release (les blocs `(:debug)` sont exclus). Débrancher proprement, la montre redémarre l'app.

- [ ] **Étape 3 : Régler le jeton**

Émettre un jeton depuis `/watch-admin` en **production**, puis le saisir dans Connect IQ Mobile → Cockpit → Réglages, avec `host` = `cockpit.lemans.org` et `mockData` à faux.

Rappel : un jeton émis en dev (`titan_dev`) ne vaut rien en prod (`titan`).

- [ ] **Étape 4 : Vérifier la vibration depuis le service de fond**

C'est **le** point non documenté : le SDK ne mentionne nulle part si `Attention.vibrate` fonctionne depuis un `ServiceDelegate`.

Protocole : fermer l'app, laisser la montre au repos, puis depuis `/watch-admin` faire monter artificiellement un niveau (ajouter une alerte de niveau 3 sur un slug actif, ou baisser le premier seuil WBGT sous la valeur courante). Attendre le prochain cycle de fond (≤ 5 min).

- Si la montre vibre : rien à faire, consigner le résultat.
- Si elle reste muette : appliquer le repli. Dans `BgService.onFetched`, remplacer `Alerting.check(st)` par `Background.exit(st)` et faire l'appel à `Alerting.check` dans `CockpitApp.onBackgroundData(data)`. L'alerte se déclenche alors à la prochaine ouverture de l'app ou de la glance.

- [ ] **Étape 5 : Mesurer la consommation sur 24 h**

Noter le pourcentage de batterie au départ, laisser la montre en usage normal (app fermée, glance consultée de temps en temps, service de fond actif), relever après 24 h. Consigner le chiffre dans le README — c'est la seule façon honnête de répondre à l'objectif d'autonomie.

- [ ] **Étape 6 : Commit du constat**

```bash
git commit --allow-empty -m "test(montre): validation sur tactix 8 Solar reelle"
```

---

## Tâche 12 : README

**Files:**
- Create: `garmin/cockpit-watch/README.md`

- [ ] **Étape 1 : Écrire le README**

```markdown
# Cockpit — app Connect IQ pour tactix 8 Solar

Affiche au poignet le compteur d'entrees, la contrainte thermique (WBGT) et les
alertes actives du PC Organisation. Vibre sur franchissement de seuil a la
hausse, jamais en boucle.

## Cible

Device Connect IQ : `fenix8solar51mm`. C'est l'identifiant Garmin de la
**tactix 8 Solar 51 mm** — il n'existe aucun device `tactix*`, le SDK la range
sous « fēnix® 8 Solar 51mm / tactix® 8 Solar 51mm ».

Ecran 280 x 280 rond, MIP, 8 bpp. Budgets memoire : watch-app 768 Ko,
glance 64 Ko, background 64 Ko.

## Prerequis

- Connect IQ SDK 9.2.0 installe via le SDK Manager Garmin
- Java (le 1.8 du poste suffit, verifie)
- Une cle developpeur

### Generer la cle developpeur

Elle n'est pas dans le depot et ne doit jamais y entrer.

```bash
mkdir -p ~/.garmin_keys
openssl genrsa -out ~/.garmin_keys/developer_key.pem 4096
openssl pkcs8 -topk8 -inform PEM -outform DER \
  -in ~/.garmin_keys/developer_key.pem \
  -out ~/.garmin_keys/developer_key.der -nocrypt
```

## Commandes

Toutes depuis `garmin/cockpit-watch/`. `$SDK` designe le dossier du SDK.

```bash
export SDK="$HOME/Library/Application Support/Garmin/ConnectIQ/Sdks/connectiq-sdk-mac-9.2.0-2026-06-09-92a1605b2"
```

### Build de developpement

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

Regler les proprietes dans **Settings > Application Settings**. Mettre
`mockData` a vrai pour travailler sans backend ni jeton ; `mockScenario`
fait defiler quatre cas (0 nominal, 1 WBGT en hausse, 2 alerte critique,
3 donnee perimee).

Voir la glance : **Simulation > Glance View**.
Declencher le service de fond : **Simulation > Trigger Background Event**.
Mesurer la memoire : **File > View Memory**.

### Tests

```bash
"$SDK/bin/connectiq" &
"$SDK/bin/monkeyc" -t -o bin/test.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm
"$SDK/bin/monkeydo" bin/test.prg fenix8solar51mm -t
```

Un test isole :

```bash
"$SDK/bin/monkeydo" bin/test.prg fenix8solar51mm -t testAlertsOnRise
```

### Sideload

Pas de publication sur le store. On copie le `.prg` a la main.

```bash
"$SDK/bin/monkeyc" -o bin/cockpit.prg -f monkey.jungle \
  -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm -r
cp bin/cockpit.prg /Volumes/GARMIN/GARMIN/APPS/
diskutil eject /Volumes/GARMIN
```

## Reglages

Ils se saisissent depuis Connect IQ Mobile (Cockpit > Reglages), pas dans le
code : le jeton n'est jamais en dur.

| Reglage | Defaut | Role |
|---|---|---|
| `host` | `cockpit.lemans.org` | domaine de l'endpoint, **HTTPS obligatoire** |
| `token` | vide | jeton Bearer, emis depuis `/watch-admin` |
| `pollPeak` | 60 s | periode quand ca chauffe |
| `pollNormal` | 180 s | periode au repos |
| `staleAfter` | 90 s | au-dela, la donnee s'affiche en rouge |
| `alertVibrate` | vrai | couper la vibration |
| `mockData` | faux | travailler sans reseau |
| `mockScenario` | 0 | scenario simule |

## Backend

L'app consomme `GET /api/v1/watch/state` de l'application cockpit,
authentifie par `Authorization: Bearer <jeton>`.

Emettre un jeton : page `/watch-admin` (admin). Le jeton en clair n'est
affiche **qu'une seule fois**. Il est stocke hashe en base et revocable.

Un jeton emis en dev (`titan_dev`) ne vaut rien en prod (`titan`).

C'est aussi dans `/watch-admin` qu'on choisit l'evenement rapporte (mode
auto par defaut, qui suit le compteur) et quelles alertes partent a la
montre, avec leur niveau 1-3.

## Points a savoir

- **HTTPS avec certificat valide est obligatoire.** `makeWebRequest` refuse un
  auto-signe. Le simulateur tolere HTTP, la montre non.
- **La requete est executee par le telephone** via Garmin Connect Mobile :
  l'endpoint doit etre joignable depuis Internet.
- **5 minutes est le plancher** du service de fond, impose par la plateforme.
- **Le mode 24 h, c'est glance + background**, pas l'app ouverte en continu :
  une app Connect IQ ouverte empeche la montre de dormir.
- **L'alerte ne se declenche que sur transition montante.** Une redescente ou
  un plateau reste silencieux.
```

- [ ] **Étape 2 : Compléter avec les constats de la Tâche 11**

Ajouter en fin de README une section « Constats sur montre reelle » avec :
le verdict sur `Attention.vibrate` depuis le service de fond (fonctionne ou
repli applique), la consommation mesuree sur 24 h, et les valeurs de memoire
relevees pour la glance et le background.

- [ ] **Étape 3 : Commit**

```bash
git add garmin/cockpit-watch/README.md
git commit -m "docs(montre): README build, simulateur, sideload"
```

---

## Auto-revue du plan

**Couverture du spec.** Chaque section du spec est portée par une tâche :
§3.1 routes → T7 et T8 · §3.2 contrat → T6 · §3.3 jetons et rate limit → T7 ·
§3.4 calcul → T5 et T6 · §3.5 événement → T5 (`resolve_event`) et T8
(sélecteur) · §3.6 configuration → T8 · §3.7 page admin → T8 ·
§4.1 arborescence → T1 · §4.2 réglages → T1 · §4.3 cache → T2 ·
§4.4 trois points d'entrée → T3, T4, T10 · §4.5 transitions → T10 ·
§4.6 autonomie → T11 (mesure) · §4.7 mock → T3 et T9 ·
§5 risques → T11 · §6 livrables → T12.

**Écart assumé avec le spec.** Le spec écrit `minSdkVersion="6.0.0"` ; le
manifeste v3 du SDK utilise `minApiLevel`. Le plan retient `minApiLevel`, qui
est la forme correcte pour cette version de manifeste.

**Cohérence des noms.** `Cache.load` / `Cache.save` / `Cache.clear`,
`State.alertMax` / `State.wbgtLevel` / `State.worstAgeSec` / `State.isStale`,
`Fmt.count` / `Fmt.rate` / `Fmt.wbgt` / `Fmt.age`, `Api.fetch` /
`Api.toCacheDict`, `Alerting.shouldAlert` / `Alerting.check` /
`Alerting.reset` sont employés partout sous la même forme. Côté Python :
`build_state`, `read_config`, `read_counter`, `read_counter_before`,
`read_principal_id`, `read_active_alerts`, `read_weather_now`,
`read_event_short`, `wbgt_level`, `entry_rate`, `select_alerts`,
`event_label`, `resolve_event`, `validate_config`, `issue_token`,
`verify_token`, `revoke_token`, `hash_token`, `reset_cache`,
`reset_rate_limit` — identiques entre définitions et usages.
