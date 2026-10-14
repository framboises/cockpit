# Cahier des charges — Administration du moteur d'itinéraire dans Cockpit

Version 2.2 — 13 octobre 2026 (piétons/vélos soumis aux règles ; règle `piste`)

## 1. Objet

Permettre aux administrateurs Cockpit (192.168.254.35) de gérer la connaissance
terrain du moteur d'itinéraire Valhalla (192.168.254.36) : portails, voies
autorisées ou interdites, sens de circulation, vitesses, accès à la piste.

Ces règles sont intégrées au graphe routier : **toutes les applications** qui
calculent des itinéraires avec Valhalla en bénéficient, sans rien changer de
leur côté (hormis le choix du mode, § 3).

Cockpit fournit l'interface. Le stockage, la validation et l'application des règles
sont assurés par le service **valhalla-admin** (`http://192.168.254.36:8003`), décrit au § 7.

## 2. Concepts

### 2.1 Couches et scénario

```
Données OSM (Le Mans)  →  Saison  →  Épreuve (facultative)  =  Scénario chargé
```

| Couche | Rôle | Exemples |
|---|---|---|
| **OSM** | Données cartographiques d'origine, jamais modifiées | Routes, portails, piste |
| **Saison** | Règles permanentes d'une année. Duplicable d'une année sur l'autre | « Saison 2026 » : portail X toujours fermé, voie Y ouverte, vitesse 40 km/h sur les voies de service |
| **Épreuve** | Règles d'un événement. **L'emportent sur la saison**, champ par champ | « 24h Moto 2026 » : portails fermés au public, accès stands réservé |
| **Scénario** | Le couple saison + épreuve (ou saison seule) actuellement chargé dans Valhalla | Saison 2026 + 24h Moto 2026 |

- Une épreuve n'appartient pas à une saison : elle peut se combiner avec n'importe laquelle.
- Modifier une saison ou une épreuve **n'a aucun effet** tant que le scénario n'est pas (re)chargé.
- **Charger** un scénario reconstruit le graphe : Valhalla est **indisponible ~30 s**
  pour toutes les applications. En cas d'échec, le scénario précédent est
  automatiquement restauré.

### 2.2 Modes de calcul

Le mode se choisit **à chaque demande d'itinéraire**, sur la même instance Valhalla :

| Mode | Usage | Piste | Sens uniques | Passages interdits (OSM ou « interdire ») | Passages fermés (« fermer ») |
|---|---|---|---|---|---|
| **normal** | Public, logistique | ❌ | respectés | ❌ | ❌ |
| **normal + piste** | Véhicules autorisés en piste | ✅ dans le sens de course | respectés | ❌ | ❌ |
| **god** | Sécurité, secours, organisation | ❌ | ignorés | ✅ | ❌ |
| **god + piste** | Sécurité en piste | ✅ dans les deux sens | ignorés | ✅ | ❌ |

**Piétons et vélos** (`"costing": "pedestrian"` / `"bicycle"`) sont eux aussi soumis
aux règles de la saison et de l'épreuve (§ 4.2). Ils n'ont pas de mode god et ne vont jamais sur la piste.

| Profil | Piste | Sens uniques | Passages « interdire » | Passages « fermer » |
|---|---|---|---|---|
| piéton | ❌ | sans objet | ❌ si `pietons` coché | ❌ |
| vélo | ❌ | respectés | ❌ si `velos` coché | ❌ |

« Piste » = toutes les voies `highway=raceway` d'OSM (90 tronçons, 22 km :
circuit des 24 Heures, Bugatti, Maison Blanche, stands, Porsche Center, karting,
motocross). En mode god, seules les voies carrossables sont concernées. Les
trottoirs, chemins piétons et pistes cyclables restent exclus, tout comme les
obstacles physiques (bornes, blocs). Seuls les portails restreints s'ouvrent.

## 3. Impact sur les applications clientes

Chaque application ajoute à ses requêtes Valhalla (`/route`, `/sources_to_targets`,
`/isochrone`, `/optimized_route`…) la recette du mode voulu
(disponible dynamiquement : `GET /profiles`) :

| Mode | À fusionner dans le corps de la requête |
|---|---|
| normal | `"costing": "auto"` *(inchangé — comportement par défaut)* |
| normal + piste | `"costing": "taxi"` |
| god | `"costing": "bus", "costing_options": {"bus": {"ignore_oneways": true, "ignore_restrictions": true}}` |
| god + piste | `"costing": "motorcycle", "costing_options": {"motorcycle": {"ignore_oneways": true, "ignore_restrictions": true}}` |

- **Ne plus utiliser `ignore_access`** : il ferait échouer les calculs, et il
  ouvrirait les passages fermés par le scénario.
- Les profils `taxi`, `bus` et `motorcycle` sont **réservés** à ce mécanisme. Ils
  ne représentent plus de vrais taxis, bus ou motos.
- `trip.summary.has_toll == true` dans une réponse signifie **« l'itinéraire emprunte la piste »**.
  Les péages réels de la zone (A28) sont neutralisés.

## 4. Règles

### 4.1 Cible

Une règle vise un objet OSM, identifié par sa clé :
- `node/<id>` : un **point** (portail, barrière, ou n'importe quel point d'une voie) ;
- `way/<id>` : une **voie** (tronçon de route, de piste…).

Une même clé peut avoir une règle dans la saison **et** dans l'épreuve. Pour chaque
champ, la valeur de l'épreuve remplace celle de la saison.

### 4.2 Contenu d'une règle

| Champ | Valeurs | Point | Voie | Effet |
|---|---|---|---|---|
| `access` | `ouvrir` | ✅ | ✅ | Autorisé à **tout le monde** (VL, piétons, vélos), quelles que soient les restrictions OSM. Un chemin piéton « ouvert » ne devient pas carrossable |
| | `interdire` | ✅ | ✅ | Interdit aux catégories cochées dans `interdire_pour`. Pour les VL : **god passe quand même** |
| | `fermer` | ✅ | ✅ | Interdit à **tout le monde** : VL dans les 4 modes (god compris), piétons, vélos |
| `interdire_pour` | liste parmi `vl`, `pietons`, `velos` | ✅ | ✅ | **Obligatoire avec `interdire`** (au moins une valeur), interdit sinon |
| `piste` | `true` / `false` | — | ✅ | `true` : la voie **devient piste** (ex. D338 pendant les 24h) : fermée au public, aux piétons et aux vélos, empruntable seulement en mode piste. `false` : la voie **n'est plus piste** (ex. portion du Bugatti piétonnisée) : elle devient une voie ordinaire, à laquelle les autres champs s'appliquent. Absent : on suit OSM |
| `oneway` | `yes` (sens du tracé), `-1` (sens inverse), `no` (double sens) | — | ✅ | Sens unique imposé (ignoré en god) |
| `maxspeed` | entier 5–130 (km/h) | — | ✅ | Vitesse utilisée pour le temps de parcours |
| `tags` | `{"clé": "valeur" \| null}` | ✅ | ✅ | **Mode expert** : tags OSM forcés (`null` supprime) |
| `comment` | texte libre | ✅ | ✅ | Motif affiché dans Cockpit |

Au moins un des champs `access`, `piste`, `oneway`, `maxspeed` ou `tags` est obligatoire.
Une route qui devient piste (`piste: true` sur une voie qui n'est pas une piste OSM) **doit** préciser
`oneway` (`yes` ou `-1`) : c'est le sens de course, que le mode « normal + piste » respecte.
La vitesse appliquée est alors `raceway_maxspeed`.
Avec une règle de saison et une règle d'épreuve sur le même objet, la liste `interdire_pour`
de l'épreuve **remplace** celle de la saison (pas de cumul).
Une règle `interdire`/`fermer` sur un point ordinaire le transforme en portail.

### 4.3 Cas d'usage : 24 Heures du Mans, Le Mans Classic

Dans OSM, les portions publiques du grand circuit (D338 Tertre Rouge → Hunaudières → Mulsanne,
D140 / D139 Mulsanne → Arnage) sont des **routes**, et le circuit Bugatti est de la **piste**.
Pour une épreuve qui utilise le grand circuit :

| Besoin | Règle (sur chaque tronçon `way`) |
|---|---|
| La route départementale devient piste | `{"piste": true, "oneway": "yes" \| "-1", "comment": "D338 en piste"}` |
| Une portion de piste devient zone piétonne (véhicules de sécurité autorisés) | `{"piste": false, "access": "interdire", "interdire_pour": ["vl"]}` |
| Idem, sans aucun véhicule | `{"piste": false, "access": "interdire", "interdire_pour": ["vl"], "tags": {"bus": "no", "motorcycle": "no"}}` |

- **Sens** : `oneway: "yes"` si le tracé OSM du tronçon va dans le sens de course, `"-1"` sinon.
  Cockpit peut le déduire en affichant le sens du tracé (flèche) sur la carte de l'éditeur.
- **Traversées à niveau** : une route qui croise la portion devenue piste reste traversable.
  Pour l'empêcher, fermer (`fermer`) les tronçons d'accès de part et d'autre du croisement, **pas le nœud
  du croisement**, qui couperait aussi la piste.
- **Le Mans Classic** : dupliquer l'épreuve « 24 Heures du Mans » (E3, « dupliquer ») puis ajuster les
  différences. Les épreuves ne dépendent pas d'une saison : la même épreuve se charge avec n'importe quelle saison.
- Rappel : la liste des tronçons est à constituer une fois dans l'éditeur carte, puis réutilisée chaque année par duplication.

### 4.4 Réglages (settings)

| Clé | Saison | Épreuve | Défaut | Effet |
|---|---|---|---|---|
| `service_default_maxspeed` | obligatoire | facultatif (`null` = hérite) | 40 | Vitesse des voies de service sans vitesse OSM |
| `raceway_maxspeed` | obligatoire | facultatif | 40 | Vitesse sur la piste (modes piste) |

## 5. Écrans Cockpit

### E1 — Tableau de bord « Moteur d'itinéraire »

- Scénario chargé : saison + épreuve (nom, version), date et auteur du chargement (`GET /status` → `active`).
- Alerte **« Modifications non appliquées »** si `pending_changes` n'est pas vide (la saison et/ou l'épreuve chargées ont été modifiées depuis).
- État de Valhalla (`valhalla_ready`) et du dernier chargement (`last_load.state`).
- Action principale **« Charger un scénario »** → E5.

### E2 — Saisons

- Liste (`GET /seasons`) : nom, année, nombre de règles, version, dernière modification, badge « chargée ».
- Actions : **créer** (nom, année, réglages), **dupliquer** (nouveau nom + année, ex. « Saison 2027 »),
  **renommer / modifier les réglages**, **supprimer** (refusé si chargée), **éditer sur la carte** → E4, **historique** → E6.

### E3 — Épreuves

- Liste (`GET /events`) : nom, dates (`starts_on`, `ends_on`, informatives), nombre de règles, badge « chargée ».
- Mêmes actions que E2 (création avec dates au lieu de l'année).

### E4 — Éditeur cartographique (saison ou épreuve)

- Carte du terrain avec trois couches activables, chargées pour la **bbox de la vue** :
  - Barrières : `GET /features?kind=barriers` (points) ;
  - Voies : `GET /features?kind=highways&bbox=…` (lignes ; **toujours passer la bbox**, ~9 Mo sinon) ;
  - Piste : `GET /features?kind=raceways`.
- Paramètres `season=<id>&event=<id|none>` : afficher les règles de la saison / épreuve en cours d'édition.
- Statut piste effectif d'une voie : `effective_rule.piste` s'il est présent, sinon `properties.raceway` (OSM).
- Code couleur conseillé (à partir de `effective_rule.access`, puis `osm_restricted`) :
  `fermer` rouge, `interdire` orange (pictogrammes VL / piéton / vélo selon `interdire_pour`), `ouvrir` vert, interdit OSM sans règle gris hachuré,
  piste bleu (flèche de sens selon `oneway`), règle venant de la saison en trait plein, de l'épreuve en pointillé.
- Clic sur un objet : panneau latéral avec
  - les tags OSM d'origine (`osm_tags`), en lecture seule ;
  - la règle de la saison (`season_rule`) et celle de l'épreuve (`event_rule`) ;
  - un formulaire : accès (aucun / ouvrir / interdire / fermer), sens, vitesse, commentaire, et un mode expert pour les tags ;
  - pour une voie : un sélecteur **Piste** « inchangé (OSM) / devient piste / n'est plus piste ». Pour « devient piste »
    sur une voie non-piste, le sens de course est obligatoire (choix « sens du tracé » / « sens inverse », avec la flèche du tracé affichée) ;
  - si « interdire » est choisi : **trois cases à cocher obligatoires** « Véhicules (VL) », « Piétons », « Vélos »
    (au moins une ; aucune pré-cochée, pour forcer le choix), envoyées dans `interdire_pour` ;
  - les boutons **Enregistrer** (`PUT …/rules/{node|way}/{id}`) et **Supprimer la règle** (`DELETE …`).
- Clic sur un point quelconque d'une voie (pas une barrière) : proposer « poser un portail ici »
  (règle `access` sur `node/<id>`). Cela demande l'identifiant du nœud OSM, que la géométrie
  GeoJSON ne fournit pas aujourd'hui : évolution prévue au § 9.
- Recherche d'un objet par identifiant OSM ou par nom de voie.

### E5 — Chargement d'un scénario

1. Choix de la saison (obligatoire) et de l'épreuve (liste + « aucune »).
2. Avertissement : **« Valhalla sera indisponible environ 30 secondes pour toutes les applications. »** Confirmation obligatoire.
3. `POST /load` puis interrogation de `GET /status` toutes les 3 s tant que `last_load.state == "running"`, avec affichage des étapes (`last_load.steps`).
4. Résultat :
   - `success` : afficher les 4 trajets de contrôle (`reference_routes`) ;
   - `rolled_back` : message d'erreur (`error`) + « le scénario précédent est toujours actif » ;
   - `failed` : erreur, contacter l'administrateur système.
- 409 sur `POST /load` : un chargement est déjà en cours, afficher son suivi.

### E6 — Historique

- `GET /{seasons|events}/{id}/history` : liste des versions ; `…/history/{v}` : contenu.
- Comparaison de deux versions (règles ajoutées / modifiées / supprimées).
- **Restaurer** une version : `PATCH /{layer}/{id}` avec `{"rules": …, "settings": …}` de la version choisie (crée une nouvelle version).

### E7 — Testeur d'itinéraire *(souhaitable)*

Deux points sur la carte, calcul direct sur Valhalla (`:8002/route`) dans les 4 modes
avec les recettes de `GET /profiles` ; affichage des tracés, distances, temps et indicateur « piste ».

## 6. Règles de gestion

- **Verrouillage optimiste** : toute écriture (`PATCH`, `PUT`/`DELETE` de règle, `DELETE` de document)
  exige `If-Match: <version>` (version lue juste avant, aussi dans l'en-tête `ETag`).
  Sur **409**, Cockpit relit le document, informe l'utilisateur (« modifié par X entre-temps ») et lui propose de réappliquer sa modification.
- Chaque écriture porte l'en-tête `X-Author: <identifiant Cockpit de l'utilisateur>` (traçabilité, historique).
- Une saison ou une épreuve **chargée ne peut pas être supprimée** (409).
- Un seul chargement à la fois (409).
- Les identifiants de document sont générés par le service à partir du nom (`Saison 2026` → `saison-2026`) et ne changent pas si on renomme.
- Une règle ne peut viser qu'un objet présent dans les données OSM (422 sinon).

## 7. API valhalla-admin

- Base : `http://192.168.254.36:8003` — documentation interactive : `/docs`.
- En-têtes : `X-Api-Key` (obligatoire, valeur `VALHALLA_ADMIN_API_KEY` du `.env` du serveur Valhalla), `X-Author`, `If-Match`.
- Accès réseau limité à 192.168.254.35 (filtrage applicatif).

| Méthode | Chemin | Corps / paramètres | Réponse |
|---|---|---|---|
| GET | `/health` | — (sans clé) | `{"status":"ok"}` |
| GET | `/status` | — | `active`, `pending_changes`, `last_load`, `valhalla_ready` |
| GET | `/profiles` | — | recettes des 4 modes (§ 3) |
| GET | `/features` | `kind=barriers\|highways\|raceways`, `bbox`, `highway`, `season`, `event`, `only_rules` | GeoJSON |
| POST | `/load` | `{"season_id": "…", "event_id": "…" \| null}` | 202 + état ; 409 si en cours |
| GET | `/seasons`, `/events` | — | liste résumée |
| POST | `/seasons`, `/events` | `{"name", "year" \| "starts_on"/"ends_on", "description"?, "settings"?, "rules"?}` | 201 + document |
| GET | `/{layer}/{id}` | — | document + `ETag` |
| PATCH | `/{layer}/{id}` | champs à modifier (`rules` remplace l'ensemble) + `If-Match` | document |
| DELETE | `/{layer}/{id}` | `If-Match` | 204 ; 409 si chargé |
| POST | `/{layer}/{id}/duplicate` | `{"name", "year"?…}` (facultatif) | 201 + copie |
| PUT | `/{layer}/{id}/rules/{node\|way}/{osm_id}` | règle (§ 4.2) + `If-Match` | document |
| DELETE | `/{layer}/{id}/rules/{node\|way}/{osm_id}` | `If-Match` | document |
| GET | `/{layer}/{id}/history` | — | `{"versions": [1,2,…]}` |
| GET | `/{layer}/{id}/history/{v}` | — | document à la version v |

### Exemples

Document saison :

```json
{
  "id": "saison-2026", "version": 7, "name": "Saison 2026", "year": 2026,
  "description": "Règles permanentes 2026",
  "settings": {"service_default_maxspeed": 40, "raceway_maxspeed": 40},
  "rules": {
    "node/251820935": {"access": "fermer", "comment": "Portail Nord condamné"},
    "way/993399245":  {"access": "interdire", "interdire_pour": ["vl"], "comment": "Allée Bugatti réservée"},
    "way/354149821":  {"access": "interdire", "interdire_pour": ["pietons", "velos"], "comment": "Zone technique"},
    "way/1101935458": {"oneway": "yes", "maxspeed": 30}
  },
  "created_at": "…", "updated_at": "…", "updated_by": "j.dupont",
  "duplicated_from": {"id": "saison-2025", "version": 12}
}
```

Feature (`GET /features`) :

```json
{"type": "Feature", "id": "way/993399245",
 "geometry": {"type": "LineString", "coordinates": [[0.2101, 47.9461], …]},
 "properties": {"osm_type": "way", "osm_id": 993399245,
   "osm_tags": {"highway": "service", "name": "Allée Bugatti"},
   "raceway": false, "osm_restricted": false,
   "season_rule": {"access": "interdire", "interdire_pour": ["vl"]}, "event_rule": {"access": "ouvrir"},
   "effective_rule": {"access": "ouvrir"}}}
```

État (`GET /status`) :

```json
{"active": {"season": {"id": "saison-2026", "name": "Saison 2026", "version": 7},
            "event": {"id": "24h-moto-2026", "name": "24h Moto 2026", "version": 3},
            "loaded_at": "…", "loaded_by": "j.dupont"},
 "pending_changes": ["event"],
 "last_load": {"state": "success", "steps": [{"at": "…", "msg": "…"}],
               "patch": {"raceway_ways": 90, "service_speed_patched": 4190, "rules_applied": 12},
               "reference_routes": {"normal": {"time_s": 287, "length_km": 2.89, "uses_raceway": false}, …}},
 "valhalla_ready": true}
```

`last_load.state` : `never`, `running`, `success`, `rolled_back` (échec, scénario précédent restauré),
`failed`, `interrupted` (service redémarré pendant un chargement).

### Codes d'erreur

| Code | Cause | Comportement Cockpit attendu |
|---|---|---|
| 401 | clé API invalide | erreur de configuration |
| 403 | IP non autorisée | erreur de configuration |
| 404 | document, version ou règle inconnus | rafraîchir la liste |
| 409 | version obsolète (`detail.current_version`), document chargé, chargement en cours | cf. § 6 |
| 422 | données invalides (`detail` explicite en français) | afficher le message |
| 428 | `If-Match` manquant | bug Cockpit |

## 8. Validation réalisée (instance de test isolée)

| Vérification | Résultat |
|---|---|
| Saison initiale identique au patch historique (4 190 voies de service à 40 km/h) | ✅ |
| Normal : piste jamais utilisée ; normal + piste : sens de course uniquement | ✅ |
| God : franchit les voies interdites OSM ; god + piste : piste à contresens | ✅ |
| « interdire » (saison) : évité en normal, emprunté en god | ✅ |
| « fermer » une voie / un point (épreuve) : évité par les 4 modes | ✅ |
| Épreuve « ouvrir » l'emporte sur saison « interdire » | ✅ |
| Piétons et vélos jamais routés sur la piste | ✅ |
| « interdire » VL seuls : le piéton passe ; « interdire » vélos seuls : le vélo contourne | ✅ |
| « interdire » piétons (épreuve) : le piéton contourne (0,85 → 1,40 km) | ✅ |
| « fermer » un point : piéton et vélo bloqués (46 m à vol d'oiseau → 1,46 km de détour) | ✅ |
| D338 Hunaudières passée en piste (épreuve) : public et piétons détournés ; « normal + piste » dans le sens de course seulement ; « god + piste » dans les deux sens | ✅ |
| Portion du Bugatti piétonnisée : piéton direct (1,93 → 0,11 km), VL normal détourné, god autorisé, plus comptée comme piste | ✅ |
| `piste: true` sans sens de course, ou sur un nœud : refusé (422) | ✅ |
| `interdire` sans `interdire_pour`, valeur inconnue, ou `interdire_pour` avec une autre action : refusés (422) | ✅ |
| Échec de chargement → restauration automatique du scénario précédent | ✅ |
| Duplication de saison, verrouillage optimiste, refus de suppression d'un document chargé | ✅ |

## 9. Limites et évolutions

- **Mise à jour des données OSM** : après un nouveau téléchargement, des règles peuvent viser des objets disparus.
  *Évolution* : un endpoint de contrôle de cohérence qui liste les règles orphelines.
- **Poser un portail n'importe où** : il faut aujourd'hui l'identifiant du nœud. *Évolution* : `GET /features/nearest?lon=&lat=` qui renvoie le nœud OSM le plus proche.
- **Temps de parcours** : le profil `motorcycle` (god + piste) donne des temps proches, mais pas identiques, à ceux du profil `auto`.
- **Horaires OSM** (`access:conditional`…) : toute règle `access` les neutralise sur l'objet visé.
- **Indisponibilité** de ~30 s par chargement (reconstruction complète du graphe).
