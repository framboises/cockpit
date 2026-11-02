<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Routing (calcul d'itinéraires Valhalla)

Le calcul d'itinéraires opérationnels (modale fiche PC org, bac à sable, app Field tablette, recalage des traces des balises) est servi par un **Valhalla auto-hébergé sur la VM Linux** `srv-safe-docker.aco.local` = **192.168.254.36** (`:8002`, `VALHALLA_URL`). Les règles terrain sont administrées par le service **valhalla-admin** de la même VM (`:8003`, `VALHALLA_ADMIN_URL`, doc interactive `/docs`). **Référence : le cahier des charges v2.2, `docs/claude/valhalla-admin-cahier.md`** (contenu des règles § 4.2, cas 24h / Le Mans Classic § 4.3). `/openapi.json` du service ne décrit pas le contenu des règles (objet libre) ; ses 422 sont en français et affichés tels quels.

Formats vérifiés en réel le 13/10/2026 : `/profiles` = objet par mode (`normal`, `normal_piste`, `god`, `god_piste`), `/seasons` et `/events` = listes avec `rules_count`, `GET /{layer}/{id}` avec `ETag: "<version>"` (entre guillemets), 409 de conflit = `{"detail": {"message", "current_version"}}`, `/features` = FeatureCollection (`effective_rule` à `null` sans règle). Le service valide le corps avant la version : l'acceptation d'un `If-Match` sans guillemets (ce qu'envoie la page) reste à confirmer à la première écriture.

### Principe (refonte du 13/10/2026)

```
Données OSM (Le Mans)  →  Saison  →  Épreuve (facultative)  =  Scénario chargé
```

- **Saison** : règles permanentes d'une année (duplicable). **Épreuve** : règles d'un événement, l'emportent sur la saison **champ par champ**, combinables avec n'importe quelle saison.
- Les règles visent un objet OSM (`node/<id>` point ou portail, `way/<id>` voie) : `access` (`ouvrir` / `interdire` / `fermer`), `interdire_pour` (liste parmi `vl`, `pietons`, `velos` : **obligatoire avec `interdire`**, au moins une valeur, **422 avec tout autre accès** ; `ouvrir` et `fermer` valent pour tout le monde), `piste` (voies, v2.2 : `true` = devient piste, `false` = n'est plus piste, absent = inchangé ; statut effectif = `effective_rule.piste` s'il est présent, sinon `raceway` ; une voie non piste OSM (`raceway == false`) qui devient piste exige `oneway` `yes` ou `-1`, sens de course unique : l'éditeur ne propose alors que ces deux choix et affiche des flèches jaunes du sens du tracé ; sur une piste OSM, `oneway` reste facultatif), `oneway` (`yes` = sens du tracé / `-1` / `no`, voies), `maxspeed` (5-130, voies), `tags` (mode expert, `null` supprime), `comment`. Réglages : `service_default_maxspeed`, `raceway_maxspeed` (obligatoires en saison, `null` = hérite en épreuve).
- Les règles sont **intégrées au graphe** au chargement du scénario : rien n'est envoyé dans les requêtes de calcul. ⚠️ Modifier une saison / épreuve **n'a aucun effet** tant que le scénario n'est pas rechargé. ⚠️ **Charger = Valhalla indisponible ~30 s pour toutes les applications** ; en cas d'échec, le scénario précédent est restauré (`rolled_back`).

### Les 4 modes de calcul (`valhalla_admin.py`)

| Mode | Recette fusionnée dans la requête | Piste | Sens uniques | `interdire` / interdit OSM | `fermer` |
|---|---|---|---|---|---|
| `normal` | `costing: auto` | non | respectés | non | non |
| `normal_piste` | `costing: taxi` | sens de course | respectés | non | non |
| `god` | `costing: bus` + `ignore_oneways`, `ignore_restrictions` | non | ignorés | oui | non |
| `god_piste` | `costing: motorcycle` + idem | deux sens | ignorés | oui | non |

- `route_recipe(mode)` lit les recettes sur `GET /profiles` (cache 10 min) avec **repli sur celles du cahier** si le service ne répond pas : un calcul ne dépend jamais de valhalla-admin.
- `resolve_mode(data)` : `mode` explicite ou booléens `god` / `piste` (forme des clients existants).
- ⚠️ **Jamais `ignore_access`** : il fait échouer les calculs et rouvrirait les passages `fermer`. Les profils `taxi` / `bus` / `motorcycle` sont **réservés** à ce mécanisme.
- Piétons et vélos : `costing: pedestrian` / `bicycle` appliquent d'eux-mêmes les règles du scénario, sans recette : `fermer` bloque tout le monde, `interdire` les catégories cochées, `ouvrir` ouvre à tous ; jamais de piste, pas de mode god. Cockpit ne calcule aujourd'hui que des itinéraires véhicule.
- ⚠️ **`interdire_pour` saison + épreuve ne se cumulent pas** : la liste de l'épreuve remplace celle de la saison (saison « VL » + épreuve « piétons » = piétons seuls pendant l'épreuve). L'éditeur le rappelle quand la saison interdit déjà l'objet.
- `trip.summary.has_toll == true` = **l'itinéraire emprunte la piste** (péages réels neutralisés) → champ `uses_raceway` des réponses de `routing.py`.

### Où les modes sont utilisés

- `routing.py` : `/api/route` (modale fiche `routing.js`, bac à sable `routing_test.js`) et `/field/api/route` (tablette). Cases « Intervention prioritaire » et « Accès piste ». Évitements Waze conservés en modes normaux (`avoid_locations` / `exclude_polygons`), jamais en god.
- **Piste sur tablette** : drapeau `route_piste` du **groupe de balises** (case « Accès piste dans les itinéraires » de la modale de groupe, `edit.html` / `anoloc_admin.js`), lu à chaud par `field._device_route_piste` comme `declarant`. Sans le drapeau, la case n'apparaît pas et le serveur **refuse** (`403 piste_not_allowed`) plutôt que de dégrader en silence.
- `balises_trace.py` : recalage `/trace_attributes` en **`god_piste`** (véhicules d'intervention, piste dans les deux sens).
- `/api/admin/itineraire/route-test` : testeur, A → B dans les 4 modes (sans Waze, sans repli).
- **Aucun chemin** (HTTP 400 de Valhalla, typiquement un passage `fermer`) → erreur `no_route`, **jamais** le trait droit estimé ; le repli `engine: "stub"` reste réservé à Valhalla injoignable (dont les ~30 s de rechargement).

### Page d'administration `/admin/itineraire`

`valhalla_admin.py` (blueprint + relais), `templates/itineraire_admin.html`, `static/js/itineraire_admin.js`, `static/css/itineraire_admin.css`. Page admin accordable à un groupe (`ADMIN_PAGE_REGISTRY` id `itineraire`), entrée « Moteur d'itinéraire » du groupe Paramètres.

- **Tableau de bord** : scénario chargé (`GET /status`), alerte « Modifications non appliquées » (`pending_changes`), état Valhalla et dernier chargement, rafraîchi toutes les 30 s.
- **Saisons / Épreuves** : créer, renommer / réglages, dupliquer (année suivante proposée), supprimer (refusé si chargée), historique, éditer sur la carte.
- **Carte** (onglet par défaut, édition à la souris, aucun identifiant OSM à saisir) : sélecteurs Saison + Épreuve (« aucune » = on modifie la saison, sinon l'épreuve). Toutes les voies de la vue chargées au fil des déplacements (zoom ≥ 13, cache par id, ~1,3 Mo pour la zone du circuit), piste toujours, barrières à partir du zoom 15, chemins piétons / vélos masquables. **Clic** = sélection, **Maj+clic** = plusieurs tronçons (une rue, la D338...), **Échap** = annuler. La bulle applique la même action à toute la sélection en **une seule écriture** (`PATCH` du jeu de règles, une version) : accès (ouvrir / interdire + cases VL, piétons, vélos / fermer / retirer), piste (devient / n'est plus / suivre OSM), **sens de circulation** (sens unique / double sens / suivre OSM : pour un plan de circulation préfecture, le sens unique se choisit une fois avec les flèches bleues et « Inverser le sens », même sur toute une rue, et Cockpit en déduit `yes` / `-1` par tronçon), motif, et en options vitesse et tags. **Devient piste** : le sens de course se choisit une fois (flèches bleues, bouton « Inverser le sens ») et Cockpit en déduit `oneway` `yes` / `-1` tronçon par tronçon en suivant leur enchaînement (les tronçons OSM d'une même route ne sont pas tracés dans le même sens). **Portail sur un point quelconque** : sélectionner la voie, cliquer l'un de ses points blancs ; Cockpit retrouve le nœud (`/api/admin/itineraire/node-at` : API OSM officielle, correspondance par coordonnées exactes car OSM a pu gagner des nœuds depuis le PBF du moteur) et signale un croisement. **Tester un trajet** sur la même carte (4 modes, scénario CHARGÉ). Recherche par nom de voie (une ligne par rue, sélectionne tous ses tronçons). Liste repliable des règles du document.
- **Portails hors réseau** (§ E4 « Barrières hors réseau » du cahier valhalla-admin, propriétés de `/features?kind=barriers`) :
  - **Les propriétés :**
    - `on_network: false` : le portail est dessiné sur un mur ou une clôture, et sa règle n'a **aucun effet**.
    - `nearest_way` : `{id, distance_m}`, à 50 m au plus, sinon `null`.
    - `auto_snap: true` : la voie est à 1 m au plus, et le service rattache lui-même le portail au chargement.
  - **Picto sur la carte :** alerte rouge si le portail n'est pas rattaché, info grise discrète s'il l'est. Sans règle, le picto n'apparaît que lorsque les barrières sont affichées.
  - **Panneau :**
    - un bandeau d'alerte, la voie la plus proche surlignée en orange (chargée autour du portail si elle est hors de la vue) ;
    - un bouton « Poser la règle sur cette voie » : il ouvre la bulle de la voie préremplie (accès, `interdire_pour`, motif ; état `S.m.prefill`) et rappelle la longueur de la voie, avec un avertissement au-delà de 5 m. L'opérateur applique lui-même. La règle du portail n'est **jamais** supprimée automatiquement.
  - **Encadré « Règles sans effet » sous la carte :**
    - appel `only_rules=true&off_network=true` + `season` / `event`, filtré sur `auto_snap === false` ;
    - badge du nombre ; un clic sur une ligne centre la carte et ouvre la bulle.
  - **Service pas à jour** (propriétés absentes, `off_network` ignoré) : rien ne s'affiche, l'encadré reste masqué. Le chargement affiche `last_load.patch.barriers_snapped` s'il est présent.
- **Données OSM** (onglet, `static/js/itineraire_osm.js`, utilitaires partagés par `window.ItAdmin`) : mise à jour de l'extrait OSM du moteur, figé jusque-là.
  - **Bandeau au tableau de bord.** Il affiche :
    - la date des données en service, la date et l'auteur de leur mise en service ;
    - « Nouvelles données disponibles » (`upstream.newer`), ou « Candidat en attente de décision ».
  - **Télécharger les données à jour** : `POST /osm/refresh`, qui prend 5 à 10 min (~370 Mo). L'écran suit `GET /osm` toutes les 3 s tant que `job.state == "running"`, toutes les 60 s sinon. En `failed`, il affiche l'erreur ; en `interrupted`, il propose de relancer.
  - **Rapport du candidat** (`GET /osm/candidate`) :
    - une synthèse et les compteurs par niveau ;
    - les alertes groupées par niveau, filtrables par type et par document ;
    - une carte : géométrie avant (gris pointillé), après (couleur du niveau), successeurs numérotés.
  - **Décisions** : `PUT /osm/candidate/alerts/<id>` (id avec `:` et `/`, encodé ; le relais le retransmet tel quel).
    - `reassign` : successeurs pré-cochés et saisie libre, avec contrôle du type `way/` ou `node/` ; badge « sens inversé ».
    - `extend` : liste des morceaux qui recevront la règle.
    - « Tout ignorer » : niveaux attention et info.
  - **Accepter** : actif seulement si `blocking_open == 0` et `stale` faux ; sinon « Relancer l'analyse » (`reanalyze`).
    - Le récapitulatif est reconstruit côté Cockpit à partir des décisions (règles supprimées, réaffectées, étendues).
    - La case « Recharger le scénario actif » est cochée par défaut, avec l'avertissement des 30 s.
    - Le résultat affiche `applied` et `conflicts` (règle déjà présente, non écrasée), puis le suivi du chargement dans la feuille existante.
    - Le relais revérifie ensuite les fermetures Waze des documents modifiés, comme après une écriture de l'éditeur.
  - **Rejeter** : `DELETE /osm/candidate`, après confirmation.
  - **Archives** : bouton Restaurer (`load: true`), refusé si un candidat est ouvert. Avertissement : les règles modifiées par une acceptation ne sont **pas** remises en l'état ; passer par l'historique des documents. Après une restauration réussie, le relais lance `waze_feed.resync_all()` : les documents touchés ne sont pas connus, il relit ceux de toutes les fermetures publiées.
  - **Résumé du candidat** : `summary` est imbriqué (`{"ways": {"before", "after", "added", "removed"}, "barriers": {...}}`), affiché « Voies routables : 15 234 → 15 295 (+61 / −0) » ; une clé inconnue s'affiche telle quelle.
  - **Pendant une opération OSM**, décisions, acceptation, restauration et chargement de scénario sont désactivés (`ItOsm.occupe()`, garde dans `openLoad` / `startLoad` : le 409 du service se lirait à tort comme « chargement déjà en cours »).
  - **Service pas à jour** (`GET /osm` en 404) : bandeau masqué, onglet explicatif.
- **Chargement** : saison + épreuve, avertissement et confirmation, `POST /load` puis `GET /status` toutes les 3 s, trajets de contrôle (`reference_routes`) en cas de succès. 409 = chargement déjà en cours → affichage de son suivi.
- **Historique** : comparaison de deux versions (règles ajoutées / modifiées / supprimées, réglages), restauration = `PATCH` avec `rules` + `settings` de la version (nouvelle version).

### Relais `/api/admin/itineraire/*` (valhalla-admin)

- Le navigateur n'a ni la clé ni l'accès réseau (le service filtre sur l'IP de Cockpit, 192.168.254.35) : le relais ajoute `X-Api-Key` (`VALHALLA_ADMIN_API_KEY`) et `X-Author` (email Cockpit), transmet `If-Match` / `ETag`, rend le statut du service tel quel.
- **Verrouillage optimiste** : toute écriture porte `If-Match: <version>` lue juste avant. Sur 409 avec `detail.current_version`, le front relit, affiche « modifié par X entre-temps » et propose de réappliquer (`withConflict`). 409 sans version = document chargé ou chargement en cours.
- Codes : 401 / 403 = configuration (clé, IP), 422 = message français du service affiché tel quel, 428 = `If-Match` manquant (bug Cockpit), 503 = clé absente côté Cockpit, 502 = service injoignable.

### Pièges

- **`VALHALLA_URL` doit être posée explicitement en prod**, sinon Cockpit pointe sur `localhost:8002` et tombe sur le repli stub (`engine: "stub"`). `VALHALLA_ADMIN_URL` en dérive (même hôte, port 8003) si elle n'est pas posée.
- **Carte de l'éditeur : portails sur leur propre calque** (`pane` `itBarriers`, z-index 450, rendu canvas dédié), au-dessus des voies. Sur un calque commun, les voies chargées après coup passaient dessus et captaient le clic d'un portail posé sur une voie. Les portails portant une règle restent affichés à tous les zooms, les autres à partir du zoom 15.
- **Sans `VALHALLA_ADMIN_API_KEY`**, les calculs fonctionnent (recettes du cahier) mais la page d'administration est inerte.
- **Règles OSM orphelines** : après une mise à jour des données OSM, une règle peut viser un objet disparu (pas encore de contrôle de cohérence côté service).
- **Horaires OSM** (`access:conditional`...) : toute règle `access` les neutralise sur l'objet visé.
- **Ancien mécanisme supprimé le 13/10/2026** : `routing_overrides` (blocages `avoid_locations` / `exclude_polygons` envoyés à chaque requête, éditeur dans `/field-dispatch`) et le patch `maxspeed=40` des voies de service (`infra/valhalla/patch_aco_speeds.py`, remplacé par le réglage `service_default_maxspeed` de la saison). La collection Mongo `routing_overrides` est laissée en base pour mémoire ; ses blocages HOU 12 (`node/2950266542`) et C11 3 (`node/2951025110`) sont à recréer en règles `fermer` dans la saison.
