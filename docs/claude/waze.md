<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Waze : publication des fermetures (Waze for Cities, flux CIFS)

Mis en place le 14/10/2026. Les routes fermées dans le moteur d'itinéraire (`/admin/itineraire`) peuvent être envoyées aux conducteurs Waze avec des horaires. Code : `waze_feed.py` (blueprint), section « Waze » de `static/js/itineraire_admin.js`, onglet « Waze » de `templates/itineraire_admin.html`. Tests : `tests/test_waze_feed.py`.

### Principe

- **Waze n'a pas d'API d'envoi** : il relit toutes les quelques minutes un flux hébergé par le partenaire. Cockpit sert `GET /public/waze/closures.xml` (public, **Basic auth** `WAZE_FEED_USER` / `WAZE_FEED_PASSWORD`, `Cache-Control: no-store`), à déclarer dans Partner Hub > Toolbox > Partner feed (format CIFS XML). Waze n'a pas d'IP fixe : **aucun filtrage IP ni pays possible** sur ce chemin (IIS doit transmettre l'en-tête `Authorization`, module Basic d'IIS désactivé).
- Collection Mongo **`waze_closures`** : une fermeture = des tronçons (`rule_keys`, `way/<id>`) d'une saison ou épreuve, une polyline figée `[[lat, lon], ...]`, `street`, `description` (40 car. max), `direction` (`BOTH_DIRECTIONS` / `ONE_DIRECTION` = sens de parcours de la polyline), `slots` (plages en UTC, `sid` stable), `publish`, `sync_ok`. Interrupteur général `waze_feed_config._id=global.enabled` (coupé = flux vide volontaire).
- **Indépendant du scénario chargé** : recharger Valhalla ne rouvre rien chez Waze.
- Flux : une `<incident>` par plage, `id = uid-sid` **stable**, `type ROAD_CLOSED`, `subtype ROAD_CLOSED_EVENT`, `parent_event = aco-<doc_id>`, heures **Europe/Paris avec décalage du jour** (`ZoneInfo`, jamais en dur). Plages terminées gardées 24 h.

### Règles Waze appliquées (developers.google.com/waze/data-feed, cifsv2.xsd)

- Début d'une plage **non modifiable une fois commencée** ; une plage en cours ne se supprime pas : **Rouvrir maintenant** (fin ramenée à maintenant, gardée 24 h dans le flux).
- Polyline ≥ 30 m, ≤ 20 km, non circulaire, une seule rue (nom identique aux deux bouts sur la carte Waze, vérifiable par le bouton « Vérifier le nom Waze » : API Reverse Geocoding `WAZE_GEOCODE_TOKEN`, rayon 50 m).
- Waze ferme des tronçons entiers de **sa** carte (pas OSM) et ne fait aucune exception pour les secours. Pas de fermeture de nœud (portail) : v1 = voies seulement.
- **Jamais de flux vide sur panne** : erreur Mongo → 503 (sinon Waze croirait toutes les routes rouvertes).

### Synchronisation avec les règles

Publiables : tronçons dont la règle du document **ferme** la voie (`fermer`, `interdire` avec `vl`, `piste: true`). Après chaque écriture réussie d'une saison / épreuve via le relais (`valhalla_admin._notify_waze`), `waze_feed.on_doc_written` passe `sync_ok=False` les fermetures dont un tronçon n'est plus fermé (ou dont le document est supprimé) : **retirées du flux**, badge « Désynchronisée » ; elles se resynchronisent seules si la règle revient. L'onglet Waze relance `POST /api/admin/itineraire/waze/resync` à l'ouverture.

### Écrans

- Bulle de la carte : bloc « Waze » (fermetures existantes des tronçons, bouton « Publier sur Waze... » si tous les tronçons sélectionnés sont fermés). Les tronçons doivent **se suivre** (un début et une fin) ; la polyline est construite dans l'ordre de la chaîne (`chainPolyline`, même orientation que `courseOrientation`).
- Fenêtre : rue (+ vérification Waze), message, sens fermé (deux sens / un sens = flèches bleues), plages pré-remplies avec les dates de l'épreuve, « Enregistrer en brouillon » / « Publier » (confirmation).
- Onglet « Waze » : interrupteur général, état des identifiants, adresse du flux à déclarer, « Voir le flux », liste (statuts brouillon / programmée / en cours / terminée / désynchronisée ; actions Voir, Modifier, Publier, Retirer, Rouvrir maintenant, Supprimer).

### Pièges

- Pas de validation manuelle demandée à Waze, pas de coordination avec le Département (décision de Ludo, 14/10/2026) : une publication part directement aux conducteurs.
- `routing.py` pénalise les alertes Waze `ROAD_CLOSED` : si nos fermetures reviennent par le flux d'alertes Waze, elles s'ajoutent aux règles du moteur (sans effet néfaste : la voie est déjà fermée dans le graphe).
- Le compte partenaire est le même que pour la lecture des alertes (`traffic.py`, région ROW).
