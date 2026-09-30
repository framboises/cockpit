<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Routing (calcul d'itinéraires Valhalla)

Le calcul d'itinéraires opérationnels (modale fiche PC org, bac à sable test, app Field tablette) est servi par un **service Valhalla auto-hébergé sur une VM Linux dédiée** (`srv-safe-docker.aco.local:8002`). Cockpit ne consomme que l'API HTTP via `VALHALLA_URL`. Aucun service Valhalla dans le `docker-compose.yml` Cockpit.

### Architecture

- `routing.py` : blueprint Flask, routes `/api/route`, `/field/api/route`, `/api/route/forward`. Fallback stub (trait droit haversine) si Valhalla injoignable.
- `routing_overrides.py` : blueprint admin pour les corrections terrain (portails fermés, routes barrées, zones à forcer ouvertes). Fusionné avec les pénalités Waze dans `routing._compute()`.
- Frontend : `routing.js` (modale fiche), `routing_test.js` (bac à sable index), `routing_overrides_admin.js` (éditeur de carte dans `/field-dispatch`), `field.js:setRouteDestination` (tablette).

### Modes de calcul

- **Auto** (mode normal) : `costing="auto"`, respecte sens interdits, accès privés, portails, intègre les évitements Waze et les `block_*` overrides.
- **God / intervention prioritaire** : `costing="auto"` + `costing_options.auto.ignore_oneways/restrictions/access/closures` + pénalités gates/private/service à 0 + boost living_streets/tracks. Conserve la vitesse véhicule, contrairement à l'ancien hack `costing=bicycle`. Filtre les overrides selon leur `scope` (`all` / `normal_only` / `god_only`).

### Style visuel partagé (Waze-like)

Le tracé d'itinéraire dans les 3 contextes (Field tablette, modale fiche, bac à sable) utilise le même rendu :

1. **Glow bleu** `#2563eb` (weight 14 normal / 18 god, opacité 0.20 / 0.32)
2. **Trait plein** bleu (weight 5)
3. **Dash blanc animé** (weight 3, `dashArray "8 16"`) qui flotte dans le sens de circulation via `requestAnimationFrame` sur `strokeDashoffset`. Vitesse 0.4 normal / 0.9 god.
4. **Halo ambre `#f59e0b`** (weight 24, opacité 0.35) uniquement en god — code gyrophare bleu+ambre.

### Overrides admin (`routing_overrides`)

Collection MongoDB éditée via `/field-dispatch` (section "Carte routing — corrections terrain"). 3 types :

- `block_point` : envoyé à Valhalla en **`avoid_locations`** (point unique, Valhalla snape sur l'arête la plus proche et l'interdit). Pas de rayon.
- `block_polygon` : polygone envoyé en `exclude_polygons`. Pour les zones larges.
- `force_open` : marqueur "ce passage est en réalité ouvert" — **non appliqué au runtime** (Valhalla ne sait pas inclure). Sert d'inventaire pour les patches PBF (cf. `infra/valhalla/README.md`).

Cache module-level 30 s sur la lecture Mongo (`_get_all_active`), invalidé sur write via `_bump_cache`.

### Patches OSM appliqués sur la VM Valhalla

OSM tague toute la voirie interne du circuit en `highway=service`, ce qui fait que Valhalla applique des vitesses 7-25 km/h irréalistes pour les véhicules d'intervention. **Un patch local** force `maxspeed=40` sur tous les `highway=service` du PBF clippé avant build des tuiles. ETA divisée par ~3 (vitesses moyennes intra-paddock 10 → 36 km/h).

Le runbook complet (rebuild PBF, application du patch, restart container, debugging) est dans **`infra/valhalla/README.md`** + script de référence **`infra/valhalla/patch_aco_speeds.py`**. Ces fichiers ne sont **pas exécutés depuis le serveur Cockpit** — ils sont copiés sur la VM Valhalla avant lancement.

### Pièges

- **`VALHALLA_URL` doit être posée explicitement en prod**, sinon Cockpit pointe sur `localhost:8002` (défaut historique de l'époque où Valhalla tournait dans le même docker-compose). Si mal posé, `routing.py` tombe sur le fallback stub (trait droit) et tu ne le vois qu'en regardant `engine: "stub"` dans la réponse.
- **`block_point` est précis** : Valhalla snape sur l'arête la plus proche du point. Si tu poses le marqueur entre deux voies parallèles, c'est la plus proche qui sera interdite — pas forcément celle visée. Zoomer fort avant de poser.
- **Le mode god ignore aussi les `block_*` de scope `normal_only`** (par construction). Si tu veux qu'un blocage s'applique aussi à l'intervention, mettre `scope=all` ou `god_only`.
- **Le `force_open` n'a aucun effet runtime**. Il faut éditer le PBF (patch OSM type `+access=yes` sur le node concerné) puis rebuilder les tuiles côté VM. Voir `infra/valhalla/README.md` section "Évolutions possibles".
