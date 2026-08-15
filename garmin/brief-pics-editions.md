# Montre Cockpit — lire le pic enceinte générale des éditions passées

Contexte pour toi qui as développé `garmin/cockpit-watch/` et l'API `/api/v1/watch/*`.
Les deux côtés sont dans le même dépôt (`E:\TITAN\production\cockpit`), tu touches donc
au serveur ET à la montre.

Objectif : la montre continue à afficher le temps réel de l'événement en cours **comme
aujourd'hui**, et gagne la possibilité de consulter, pour une édition passée, le **pic de
présents en enceinte générale avec son jour et son heure**.

Avant d'écrire quoi que ce soit, lis ce document en entier : la moitié du travail consiste
à ne pas tomber dans quatre pièges de données qui sont déjà en base.

---

## 1. Comment la donnée compteur est produite

Un collecteur (`live_controle.py`) interroge Skidata en boucle et insère un snapshot par
location et par cycle dans la collection **`data_access`** (base `titan` en prod,
`titan_dev` en dev — `from app import db`, jamais de nom codé en dur).

Il est piloté par un document singleton `data_access._id = "___GLOBAL___"` :

| champ | rôle |
|---|---|
| `live_controle_actif` | booléen. Le collecteur s'arrête si `false` |
| `evenement` / `evenement_clean` | libellé estampillé sur chaque snapshot produit |
| `compteur_principal_id` | id de la location « ENCEINTE GENERALE ». Vaut `"628"` |
| `locations_selectionnees` | liste `{id, type, name}` des compteurs interrogés |
| `dernier_cycle`, `dernier_inventaire`, `corrections_compteurs` | télémétrie / réglages |

Forme d'un snapshot (`data_access`, hors `___GLOBAL___`) :

```json
{
  "timestamp": ISODate("2026-04-18T13:05:09.376Z"),  // datetime NAIF contenant de l'UTC
  "year": 2026,                                       // int
  "counter_id": "640", "counter_name": "ENCEINTE GENERALE",
  "entries": "8", "exits": "8", "current": "0",       // STRINGS, à caster
  "requested_location_id": "628",                     // STRING
  "requested_location_type": "Area",
  "requested_event": "24H MOTOS"                      // libellé au moment de la collecte
}
```

- `current` = **présents** (entrées − sorties côté Skidata). C'est le « pic enceinte générale ».
- `entries` = entrées **cumulées**. C'est ce que la montre envoie aujourd'hui dans le champ `e`.
  Ce ne sont pas la même grandeur : ne pas les mélanger dans l'UI.
- `timestamp` est **naïf mais porte de l'UTC** (`app.py:7478`, `watch_api._maintenant`).
  `build_state` fait déjà `horodatage.replace(tzinfo=timezone.utc).timestamp()` — reprendre
  exactement ce geste pour tout nouvel horodatage.

## 2. Ce qui se passe à la fin d'un événement

Deux actions **distinctes**, dans la page `/live-controle` :

1. **Désactiver** (`PUT /api/live-controle/config`, `app.py:6845`) écrit seulement
   `live_controle_actif: false`. Rien d'autre ne bouge, les snapshots restent en place.
2. **Archiver** (`POST /api/live-controle/archive`, `app.py:6902`) : pour chaque collection
   de travail, `insert_many` vers une collection d'archive **puis** `delete_many` sur la
   source. Pour les compteurs :

   ```
   data_access  --{requested_event: <EV>, _id != "___GLOBAL___"}-->  hsh_archive_compteurs_<TAG>
   ```

   avec `TAG = re.sub(r'[^a-zA-Z0-9_-]', '_', EV) + "_" + str(datetime.now().year)`.

Conséquence centrale : **après archivage, `data_access` ne contient plus rien de cet
événement.** La donnée n'est pas perdue, elle a changé de collection. Aucun lecteur
« temps réel » du cockpit ne sait relire ces archives, `pcorg_summary` excepté.

## 3. État réel de la base au 15/08/2026

`___GLOBAL___` : `evenement = "LE MANS CLASSIC"`, `live_controle_actif = false`,
`compteur_principal_id = "628"`, 14 locations sélectionnées.

Archives compteurs existantes :

| collection | docs | période réellement couverte |
|---|---|---|
| `hsh_archive_compteurs_24H_MOTOS_2026` | 107 967 | avr. 2025 → avr. 2026 |
| `hsh_archive_compteurs_GPF_2026` | 59 041 | mai 2025 → mai 2026 |
| `hsh_archive_compteurs_LE_MANS_CLASSIC_2026` | 53 158 | juin → juil. 2026 |
| `hsh_archive_compteurs_24H_AUTOS_2026` | 32 547 | juin 2026 |

Résidus dans `data_access` (16 898 docs au total) :

| `requested_event` | n | remarque |
|---|---|---|
| `24H MOTOS` | 3 540 | collecte du 22 au 23/04/2026, **postérieure** à l'archivage |
| `FUN RACING CARS` | 973 | jamais archivé |
| `24HMOTOS` | 36 | orthographe sans espace, hors de portée du filtre d'archivage |
| absent (`null`) | 12 349 | collecteur d'avant l'estampillage |

Pics de présents vérifiés (max de `current` sur la location `628`), heures converties en Paris :

| Édition | Pic | Quand | Où c'est stocké |
|---|---|---|---|
| 24H MOTOS 2025 | 40 077 | sam. 19/04/2025 15h07 | `..._24H_MOTOS_2026` |
| GPF 2025 | 101 194 | dim. 11/05/2025 14h05 | `..._GPF_2026` |
| 24H AUTOS 2025 | 145 571 | sam. 14/06/2025 16h10 | **`..._GPF_2026`** |
| 24H MOTOS 2026 | 50 690 | sam. 18/04/2026 15h05 | `..._24H_MOTOS_2026` |
| GPF 2026 | 98 593 | dim. 10/05/2026 14h04 | `..._GPF_2026` |
| 24H AUTOS 2026 | 148 919 | sam. 13/06/2026 16h08 | `..._24H_AUTOS_2026` |
| LE MANS CLASSIC 2026 | 52 409 | sam. 04/07/2026 16h29 | `..._LE_MANS_CLASSIC_2026` |

Jour de course, début d'après-midi, à chaque fois. La donnée est saine ; c'est son
**classement** qui ne l'est pas.

## 4. Les quatre pièges — à traiter, pas à contourner

**A. Le nom de la collection d'archive ment sur l'année.** Le suffixe vient de
`datetime.now().year` au moment du clic, pas de l'année de l'édition.
`hsh_archive_compteurs_24H_MOTOS_2026` contient 96 044 docs de **2025** (89 % du volume).
Ne jamais déduire l'année du nom de collection.

**B. Le nom de la collection ment aussi sur l'événement.** `requested_event` porte le
libellé présent dans `___GLOBAL___.evenement` **au moment de la collecte**, pas l'événement
réel. Le collecteur a tourné en juin 2025 avec le libellé resté sur `GPF` : les 31 041
snapshots du 24H AUTOS 2025 (pic 145 571) sont donc rangés sous `GPF`. Filtrer sur
`requested_event` pour retrouver une édition donne un résultat faux et silencieux.

→ **La seule clé fiable est le `timestamp`.** Résoudre une édition = définir sa fenêtre de
dates, puis balayer toutes les sources de snapshots sur cette fenêtre, sans regarder ni le
nom de collection ni `requested_event`.

**C. `read_counter` n'est borné ni en événement ni en date** (`watch_state.py:155`) :

```python
return db["data_access"].find_one(
    {"requested_location_id": str(location_id)},
    sort=[("timestamp", -1)],
)
```

LMC ayant été archivé, ce qui remonte aujourd'hui est le résidu 24H MOTOS du 23/04/2026,
`current: "0"`. Et comme `resolve_event` (`watch_state.py:111`) dérive l'événement de ce
même document en mode `auto`, la montre annonce en ce moment **« 24HM 26 » avec des
chiffres d'avril**. C'est le bug à corriger en premier, indépendamment du reste.

**D. Aucun index utile sur ces collections.** `data_access` et les quatre archives n'ont que
`_id_` et un `idx_type` inutilisé. Un max sur 108 000 docs est un COLLSCAN. Acceptable une
fois derrière un cache, inacceptable à chaque requête montre.

## 5. Travail côté serveur

### 5.1 Corriger le direct (prioritaire, autonome)

Dans `watch_state.py` :

- `read_counter(db, location_id, max_age=None)` : ajouter une borne de fraîcheur, par ex.
  `{"timestamp": {"$gte": now_utc - timedelta(hours=6)}}`. Aucun relevé dans la fenêtre =
  aucun événement en cours, et non « le dernier relevé connu ».
- `build_state` : quand il n'y a pas de relevé frais, ne pas inventer de direct. Renvoyer
  `m: "past"` (voir contrat) avec `e`, `er`, `t` à `null`.
- Vérifier que `resolve_event` en mode `auto` ne dérive plus d'un document périmé.

Les tests existants sont dans `tests/test_watch_state.py` (`FakeDb`), à étendre.

### 5.2 Nouveau module `watch_peaks.py`

Même pattern que le reste du dépôt : fonctions pures, `db` en argument, aucun import Flask,
testable sans application (cf. `watch_state.py`, `scan_frequentation.py`).

```python
def edition_window(db, event, year):
    """Fenêtre UTC d'une édition, dérivée de la date de course."""
```

Réutiliser `pcorg_summary._load_race_dt(db, event, year)` — il porte déjà 4 niveaux de repli
et gère le fait que `parametrages.data.globalHoraires.race` est la **seule** source stockée
en UTC avec un `Z` final, tout le reste étant naïf Paris. Fenêtre proposée :
`[race − 10 jours, race + 3 jours]`, à ajuster si une édition déborde.

```python
def peak_for_edition(db, event, year, location_id="628"):
    """(pic_int, timestamp_utc) ou (None, None).

    Balaye data_access + TOUTES les collections hsh_archive_compteurs_*,
    filtre sur la fenêtre de dates et requested_location_id, prend max(int(current)).
    Ne lit ni le nom de collection ni requested_event (pièges A et B).
    """
```

`current` est une chaîne pouvant valoir `""` ou `"N/A"` : caster défensivement, ignorer
l'inconvertible (voir `pcorg_summary._max_current_in_snapshots`, même geste).

```python
def list_editions(db):
    """Éditions consultables, triées de la plus récente à la plus ancienne."""
```

Source des couples (event, year) : `parametrages` filtré sur ceux qui ont une date de course
résoluble. Le libellé court vient de `evenement.short` (`watch_state.read_event_short`) et le
libellé montre de `event_label(short, year)` → `"24HA 26"`.

⚠️ Les noms d'événements divergent entre collections : `LE MANS CLASSIC` dans `data_access`,
`LMC` dans `historique_controle` ; idem `SBK` / `SUPERBIKE`. `app.py:_event_hist_aliases`
fait déjà `[nom, short]` — s'en inspirer plutôt que de le réécrire.

### 5.3 Cache : obligatoire

Le pic d'une édition terminée ne change plus jamais. Persister le résultat dans une petite
collection `watch_peaks`, un document par édition :

```json
{"_id": "24H MOTOS|2026", "event": "24H MOTOS", "year": 2026,
 "peak": 50690, "peak_ts": ISODate("2026-04-18T13:05:09.376Z"),
 "computed_at": ISODate(...), "source": "archive"}
```

Calcul paresseux au premier accès, relecture directe ensuite. Seule l'édition **en cours**
doit être recalculée (TTL court, 60 s). Ne pas recalculer les éditions closes.

Créer au passage, en lazy comme partout ailleurs dans ce dépôt, l'index
`(requested_location_id, timestamp)` sur `data_access` et sur chaque
`hsh_archive_compteurs_*` — le premier calcul passe alors de plusieurs secondes à quelques
dizaines de millisecondes.

### 5.4 Ne pas utiliser `historique_controle` comme source du pic

C'est tentant : `historique_controle{type: "frequentation"}` couvre 27 éditions depuis 2022
avec un pic déjà calculé. Mais c'est une **autre grandeur** — un solde reconstruit à partir
des scans de portes, au pas horaire — et elle ne concorde pas avec le compteur Skidata :
24H MOTOS 2026 y vaut 40 056 à 14h contre 50 690 à 15h05 côté compteur.

La montre affiche du compteur Skidata en direct. Le pic d'une édition passée doit venir de
la **même mesure**, sinon un utilisateur qui a vu 50 690 pendant la course en relira 40 056
en août. Utiliser `historique_controle` uniquement en dernier recours, et alors marquer la
source dans le payload.

## 6. Contrat d'API

### `GET /api/v1/watch/state` — inchangé, trois champs de plus

Le contrat actuel (`watch_api.py:353`, cache 20 s, Bearer, 60 req / 300 s) ne bouge pas.
Ajouts :

| clé | type | sens |
|---|---|---|
| `m` | `"live"` \| `"past"` | y a-t-il un relevé frais pour l'événement rapporté |
| `pk` | int \| null | pic de présents de l'édition rapportée |
| `pkt` | epoch (s, UTC) \| null | instant du pic |

En `m: "live"`, `pk` est le pic **courant** de l'édition en cours (il monte pendant
l'événement). En `m: "past"`, `e`, `er` et `t` valent `null` et `pk`/`pkt` portent le pic
définitif. Un seul couple de champs dans les deux cas : la montre n'a pas deux façons de
lire un pic.

Le budget de 2 Ko (`watch_state.py:15`) tient largement.

### `GET /api/v1/watch/editions` — nouveau

Même `@bearer_required`, cache long (1 h, ou lecture directe de `watch_peaks`).

```json
{"ok": true, "ed": [
  {"n": "LMC 26",  "ev": "LE MANS CLASSIC", "y": 2026, "pk": 52409,  "pkt": 1783175368},
  {"n": "24HA 26", "ev": "24H AUTOS",       "y": 2026, "pk": 148919, "pkt": 1781359710},
  {"n": "GPF 26",  "ev": "GPF",             "y": 2026, "pk": 98593,  "pkt": 1778414696},
  {"n": "24HM 26", "ev": "24H MOTOS",       "y": 2026, "pk": 50690,  "pkt": 1776517509}
]}
```

Antichronologique, plafonné à ~20 entrées. Une seule requête donne à la montre la liste
**et** tous les pics : pas d'appel par édition, la limite de 60 req / 300 s n'est jamais
menacée. Les éditions sans pic exploitable sont omises, pas renvoyées avec `pk: null`.

`pkt` est un epoch UTC, comme `t` : la montre le rend en heure locale, ce qui donne Paris.

## 7. Travail côté montre (`garmin/cockpit-watch/`)

- **`source/Api.mc`** — `toCacheDict` doit reporter `m`, `pk`, `pkt` (elle recopie
  aujourd'hui champ par champ, ces trois-là seraient silencieusement perdus). Ajouter une
  seconde fonction de fetch pour `/editions`, avec la même garde `mInFlightSince`.
- **`source/State.mc`** — `isStale` s'appuie sur `dataAgeSec(st)` donc sur `t`. En mode
  `past`, `t` est `null` : sans garde, la montre criera « périmé » sur une édition close, ce
  qui est faux. Ajouter `isPast(st)` et court-circuiter la péremption dans ce cas.
  `worstLevel` reste inchangé.
- **`source/Fmt.mc`** — formatage du pic (séparateur de milliers) et du jour depuis un epoch
  via `Time.Gregorian.info(..., FORMAT_MEDIUM)`, en heure locale de la montre.
- **`source/CockpitView.mc` / `CockpitDelegate.mc`** — vue de consultation des éditions.
  Suggestion : la vue principale reste le direct ; un appui `MENU` ouvre la liste des
  éditions, chaque entrée affichant `pic` + `jour`. Libre à toi sur l'ergonomie, la tactix 8
  a la place pour trois lignes.
- **`source/Mock.mc`** — ajouter un scénario `past` (`mockData` / `mockScenario` sont déjà
  câblés dans `Api.fetch`), pour développer sans serveur.
- **Tests Run No Evil** — `ApiTest.mc`, `StateTest`, `FmtTest.mc` existent. Couvrir au
  minimum : `toCacheDict` conserve les trois nouveaux champs ; `isStale` est faux en mode
  `past` avec `t == null` ; le formatage d'un epoch de pic.

Ne pas oublier la contrainte du dépôt : **aucun guillemet typographique** dans le code
(Monkey C compris), uniquement `'` et `"` droits.

## 8. Ordre de travail proposé

1. Borner `read_counter` sur la fraîcheur, corriger le direct, faire passer
   `tests/test_watch_state.py`. La montre cesse d'afficher les chiffres d'avril.
2. `watch_peaks.py` + collection de cache + index. Vérifier les 7 pics du tableau §3 :
   ce sont les valeurs attendues, écart nul.
3. Étendre `/state` (`m`, `pk`, `pkt`), ajouter `/editions`.
4. Côté montre : transport, état, formatage, vue, mocks, tests.
5. Vérification bout en bout sur la vraie base avant sideload.

## 9. Hors périmètre, mais à signaler dans ton rapport final

Ces défauts sont réels et documentés ici, mais ne relèvent pas de cette tâche :

- L'archivage ne filtre pas par année et nomme les collections avec l'année courante
  (pièges A et B). Une reventilation par `timestamp` est possible sans perte, puisque chaque
  document porte sa propre date.
- Le JS de `/live-controle` archive **puis** désactive (`handshake_admin.js:935`) : le
  collecteur tourne encore pendant l'opération, d'où les résidus.
- Les autres lecteurs de `data_access` souffrent du même défaut que `read_counter` :
  `/api/live-controle/counters-context` (`app.py:3110`), `/get_counter` et `/get_counter_max`
  (`app.py:1937`, `1970`), le repli de `/get_affluence_hourly` (`app.py:2854`).
