<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Main courante (fiches PC Organisation, collection `pcorg`)

Trois sources écrivent dans `pcorg` : la **synchro SQL** Prysm (`pcorg_sync.py` → `uploads/pcorg/sync_pcorg_sql.py`, toutes les 5 min, à sens unique SQL → Mongo, `_id = uuid5("sql|<sql_id>")`), **Cockpit** (`/api/pcorg/*` dans `app.py`, `server: "COCKPIT"`) et les **tablettes** (`field.py`). Interface : `static/js/pcorg.js` (petit bloc `#widget-comms`, panneau central `#pcorg-expanded-panel`, pins et menu clic droit de la carte, fiche, assistant de création).

### `pcorg_history.py` — le module partagé

Module pur (ni Flask ni Mongo propre) utilisé par `app.py`, `field.py` **et** la synchro. **Toute écriture dans la chronologie passe par `PH.append_entry`**, qui ajoute l'entrée à `comment_history` ET la ligne au champ texte `comment` en **une seule opération atomique** (pipeline d'update). L'ancien code lisait `comment`, concaténait en Python puis réécrivait : deux commentaires simultanés en perdaient un, et le commentaire tablette **remplaçait** tout le champ `comment`.

### Fusion SQL ↔ Cockpit (option A : Cockpit l'emporte sur ce qu'il a modifié)

⚠️ La synchro faisait un `$set` du document entier : la moindre réécriture Prysm effaçait la clôture, les commentaires, le véhicule engagé, la position et l'urgence saisis dans Cockpit. Désormais `write_merged()` relit chaque fiche et applique `PH.merge_sync_doc` :

- **Chronologie** : entrées SQL re-parsées + entrées locales conservées, triées. Chaque entrée porte `origin` (`sql` / `cockpit` / `field`) ; pour les anciennes, `is_local()` reconnaît les écritures Cockpit (microsecondes dans `ts`), tablette (datetime BSON, `field:`) ou porteuses de photo/codes. `comment` est reconstruit, le texte Prysm brut est gardé dans `comment_sql`.
- **Champs possédés** : chaque écriture Cockpit ajoute les champs touchés à `cockpit_owned` (`status`, `niveau_urgence`, `gps`, `text`, `category`, `area.desc`, `content_category.<clé>`, `ts`…) ; la synchro ne les écrase plus. Exception : si Prysm **clôt** après la dernière action Cockpit sur le statut (`cockpit_status_at`), SQL reprend la main sur le statut.
- **Garde de concurrence** : toute écriture Cockpit incrémente `cockpit_rev` ; la synchro écrit avec le filtre `cockpit_rev` lu, et refusionne les fiches modifiées entre-temps.
- **Base** : le script de synchro écrivait `titan` en dur (même lancé depuis le dev). Il suit maintenant `TITAN_ENV`, et `pcorg_sync.py` lui passe `--db` explicitement.

`scripts/migrate_pcorg_history.py` (simulation par défaut, `--apply` pour écrire) applique une fois la fusion aux fiches SQL déjà en base.

### Chronologie affichée

`PH.decorate_history` (route `detail` et détail tablette) sépare dans chaque entrée la **ligne de statut** (`Statut: En cours -> Terminé`, rendue en pastille), les **lignes de modification Prysm** (`Texte: a -> b`, libellé en un seul mot) et le **commentaire libre** qui suit, rendu en texte normal. Avant, toute l'entrée était en italique grisé, motif de clôture compris. Il signale aussi les entrées vides (`kind: "empty"`, ~2 000 en base, Prysm en écrit sans texte) et **ajoute une clôture synthétique** (`synthetic: true`, depuis `close_ts` / `operator_close`) quand la fiche est close sans ligne de statut dans la chronologie (plus de la moitié des fiches closes). Horodatages normalisés en ISO Paris (trois formats coexistaient).

⚠️ **Parseur du champ `comment`** : un en-tête exige date, heure ET virgule (`dd/mm/yyyy HH:MM:SS ,`) — une date citée dans le texte ne coupe plus l'entrée — et l'en-tête final sans saut de ligne est reconnu (l'ancienne regex le perdait sur ~2 900 fiches). Prysm colle parfois l'en-tête au texte précédent : pas d'ancrage en début de ligne.

### Rafraîchissement des postes

`/api/pcorg/live` n'est relu que toutes les 60 s. `GET /api/pcorg/sig?event=&year=` rend une empreinte légère (nombre de fiches, closes, somme de `bounce_rev` / `cockpit_rev`, dernier `synced_at` ; cache serveur 2 s partagé) que `pcorg.js` lit toutes les 5 s (`checkChanges`) : la liste est rechargée dès qu'elle change. Une clôture depuis une tablette apparaît donc en ~5 s. ⚠️ Toute nouvelle écriture sur `pcorg` doit incrémenter `bounce_rev` ou `cockpit_rev` (c'est le cas de `PH.append_entry` avec `inc_bounce`), sinon les postes ne la verront qu'au cycle de 60 s.

### SAISON / main courante permanente

Cockpit sert de main courante toute l'année. `SAISON` (un parametrage par année civile, **aucune date**) est reconnu par son **nom** ; une épreuve est active de `montage.start` à `demontage.end`. Source unique : `event_courant.py` (`/api/event/current`).

- **Sélecteur** (`main.js`) : défaut = évènement du jour. Choix manuel gardé 12 h (`localStorage.cockpit_sel = {event, year, manual, at}`, `cockpit_event` / `cockpit_year` toujours écrits pour `sidebar.js`, `pmv.js`, `dispatch_service.js`). Au-delà, l'accueil suit l'évènement du jour (contrôle toutes les 5 min, **accueil seulement** : jamais sur `edit`). SAISON d'une année passée → année courante. Épreuves actives en tête (« En cours »). Puce d'en-tête « Evenement du jour : X » si la sélection n'est pas active, ou si SAISON est sélectionné pendant une épreuve. Pastille de statut SAISON : « Exploitation courante » (pas de logique montage/démontage).
- **`/live`** : pour SAISON, ouvertes des `PCORG_SAISON_OPEN_DAYS` (30) derniers jours, + `older_open` (nombre des plus anciennes, lien « N fiches ouvertes de plus de 30 jours » dans `pcorg.js`) ; `all_open=1` les rend toutes (plafond 1000). SAISON de l'année courante inclut les **ouvertes de SAISON/<année-1>** (`_pcorg_open_years`) : une fiche ouverte le 31/12 ne disparaît pas. Épreuve : inchangé.
- **`/sig`** : bornée aux fiches ouvertes ou dont `ts` / `synced_at` < 7 jours (chaque branche du `$or` porte event/year pour rester indexée). Sûr car les écritures sont refusées sur fiche close (la réouverture la remet dans « ouvertes »). ⚠️ Une nouvelle écriture sur fiche close ancienne ne serait vue qu'au cycle de 60 s.
- **`/stats`** : `?period=today|24h|7d|all` ou `?since=ISO` ; ouvertes = toutes celles ouvertes maintenant, closes = closes pendant la période (`close_ts`). Défaut : aujourd'hui (jour de Paris) pour SAISON, tout pour une épreuve. La réponse porte `period {key, since, label}` ; le libellé sous les compteurs est cliquable (période suivante).
- **`/search`** : index texte `pca_text` d'abord (`method: "text"`, tri par score) ; repli `$regex` (chronologie, opérateur, carroyage) si aucun résultat ou si la requête contient un chiffre. **`/closed`** : `total` compté à la première page seulement (`null` ensuite).
- **Index** (`_pcorg_ensure_indexes`, à la volée) : `ts`, `synced_at`, `(event, year, status_code, ts)`. La synchro ajoute `sql_id`.
- ⚠️ SAISON contient surtout des **PCS.*** (Sûreté, Information) : la main courante ne montre que `PCO.*`.

#### Synchro Prysm (`sync_pcorg_sql.py`)

- Fenêtres lues par `event_courant.windows` : SAISON, `__*` et fenêtres > 120 jours ignorées (24H AUTOS 2024 : démontage saisi en 2026, 760 jours).
- **Un** évènement par message (`trouver_evenement`) : même priorité qu'`event_courant` (épreuve choisie prioritaire par un admin, `cockpit_settings._id="event_priority"`, sinon celle dont la fenêtre a commencé la première). Hors fenêtre → `SAISON/<année de Paris>`. **Rien n'est jamais déplacé** : une fiche existante garde son événement (`--reassign` existe mais l'exploitation a refusé tout reclassement le 01/10/2026). La règle « jours publics puis course la plus proche » a été refusée.
- **SAISON affiche aussi les fiches PCS** (`PCS.Surete`, `PCS.Information`, ~99 % des fiches hors épreuve) : `_pcorg_cat_query(payload, event)` ; un groupe restreint les voit via la catégorie PCO équivalente (`PCS_EQUIVALENT`). Création toujours limitée aux PCO.
- ⚠️ **event/year figés** dès que la fiche existe (`write_merged`) : une réécriture Prysm ne déplace plus une fiche quand un parametrage change. `--reassign` recalcule (à lancer à la main, en connaissance de cause).
- Purge des doublons SAISON restreinte aux `sql_id` du passage ; plus de `count_documents` sur toute la collection (estimation). `enrich_pcorg_config` balaie encore toute la collection à chaque passage.

### Droits et validations (côté serveur)

- Les **catégories autorisées par groupe** (`get_user_allowed_categories`) ne filtraient que le widget, côté navigateur : `live`, `stats`, `closed`, `search`, `detail` et toutes les écritures les appliquent maintenant (`_pcorg_cat_query`, `_pcorg_cat_allowed`).
- **Drapeaux de groupe** (Configuration > Groupes, `cockpit_groups`), les droits s'additionnent entre groupes :
  - `allowed_pages` (« Pages accessibles ») : pages de la barre latérale ouvertes au groupe, `None` = toutes. Registre `PAGE_REGISTRY` (`app.py`, `GET /api/page-registry`) : pages user et manager seulement — le rôle reste exigé en plus, les pages admin ne dépendent que du rôle admin. Contrôle serveur `@app.before_request _enforce_page_access` sur les préfixes du registre (redirection vers la première page autorisée, 403 JSON pour une API) ; barre latérale filtrée par `page_allowed('<id>')` (context processor). Union des groupes ; sans groupe, celles du groupe par défaut. Assistant IA : bouton masqué seulement (pas de chemin contrôlé).
  - `admin_pages` (« Pages d'administration ») : pages admin accordées **explicitement** à un groupe (un groupe sans restriction n'en reçoit aucune). Registre `ADMIN_PAGE_REGISTRY` : chaque page liste aussi les routes admin qu'elle appelle (Field dispatch = `/field/admin`, `/api/alfred`, `/api/whatsapp`, `/api/dispatch/config`, `/api/admin/routing-overrides` ; Alertes = définitions, caméras, liste LAPI, `GET /api/groups`…). `request_admin_grant(payload)` est consulté partout où le rôle admin est exigé : `role_required("admin")`, `field.admin_required`, les `_check_admin` de `scan_report` / `anpr` / `analyse_ops` / `cameras`, `watch_api._admin_guard`. ⚠️ **Configuration (`/config/todos`) n'est jamais accordable** : elle gère groupes et utilisateurs (escalade de droits). ⚠️ Une nouvelle page admin ou une nouvelle route admin appelée par une page accordable doit être ajoutée à son entrée du registre, sinon la page s'ouvre et l'appel échoue en 403 pour les groupes.
  - `can_close_fiche` : clôturer / rouvrir toute fiche des catégories autorisées.
  - `can_create_fiche` (« Créer des fiches ») : `/api/pcorg/create` et `/quick-create` → **403 `code: "creation_interdite"`** sans lui (`_user_can_create_fiche`). Sans groupe : droit du groupe `__default__`. ⚠️ **Migration au démarrage** (`app.py`, après la création des groupes système) : tout groupe sans le champ le reçoit à `True` — tous créaient avant le 30/09/2026. Un groupe créé ensuite part **sans** (droit explicite). L'accueil masque le « + » et le menu clic droit (`window.__userCanCreateFiche`).
  - `dispatch_manager` (« Responsable de service ») : accès à `/dispatch-service` pour les catégories du groupe (aucune = toutes), et clôture / réouverture des fiches de ces catégories (`_user_can_close_fiche_cat`, vérifié après chargement de la fiche). `_user_dispatch_categories`.
  - `fiche_lecture_seule` : `update`, `update-gps`, `set-urgency` → **403 `code: "lecture_seule"`**, **sauf sur les fiches que l'utilisateur a créées** (`operator_id_create` == son e-mail, `_pcorg_created_by`). Commentaire (« action ») et clôture restent permis. Refusé seulement si **tous** les groupes de l'utilisateur sont en lecture seule (`_user_can_edit_fiche(payload, doc)`, contrôle fait APRÈS chargement de la fiche) ; sans groupe, pas de restriction. Cas d'usage : un groupe par service (électrique, technique…), les techniciens de terrain restant sur Field.
  - L'accueil expose `window.__userCanEditFiche`, `window.__userDispatchCategories` et `window.__userEmail` ; `/live` et `/detail` exposent `operator_id_create`. `pcorg.js` (`canEditFiche(fiche)`, `canCloseFicheCat`) masque Éditer / Déplacer, fige l'urgence et montre « Clore » selon ces droits. Le serveur reste l'arbitre.
- Écritures refusées sur fiche close (commentaire, urgence, position, caméra) ; clôture et réouverture atomiques (filtre sur `status_code`).
- `content_category` nettoyé (`_pcorg_clean_cc`) : pas de clé pointée ni `$`, pas de `field_created` / `field_sos` posés depuis Cockpit (ils ouvrent la clôture côté tablette). Position validée (bornes, NaN).
- Création idempotente : `client_token` (un par ouverture de l'assistant) → même `_id`, un double clic ne crée plus deux fiches.
- Suppression : archivée dans `pcorg_deleted` (qui, quand) avant `delete_one`, tablette libérée.

### Routes ajoutées ou modifiées

- `POST /api/pcorg/reopen/<id>` (droit `can_close_fiche`, **motif obligatoire**).
- `POST /api/pcorg/close/<id>` accepte `{comment}` : motif consigné sous la ligne de statut.
- `PUT /api/pcorg/update/<id>` n'écrit que les champs réellement modifiés, trace une entrée système `Fiche modifiee : …` avec `changes: [{field, old, new}]`, accepte `comment` (même opération) et `content_category_remove` (champs de l'ancienne catégorie). Changer de véhicule désengage l'ancienne tablette.
- `POST /api/pcorg/update-gps/<id>` sert aussi à **déplacer** une fiche ; le client envoie `area_desc` et `carroye` recalculés, une entrée système trace le déplacement.
- `GET /api/pcorg/closed` : pagination par curseur `before_ts` + `before_id` (l'offset glissait à chaque clôture : doublons et trous).
- `GET /api/pcorg/search` cherche aussi dans la chronologie (`comment`), le carroyage et le n° SQL.

### Interface (`pcorg.js`)

- Pins mis à jour **par différence** (`pinSignature`) : la popup ouverte ne se ferme plus toutes les 60 s. Niveau d'urgence affiché sur le pin, contrôle de filtre de couche (masquer, catégories, EU/UA seulement ; `localStorage` `pcorg-map-filter`), lien liste ↔ carte (`setActiveFiche` : ligne et pin mis en évidence).
- `ensureMapVisible()` : « Voir sur carte » depuis le panneau central laissait la zone centrale vide.
- Édition : même modèle de source que la création (`buildSourceEditor`), même constructeur de champs (`buildCategoryFields`), commentaire facultatif. L'urgence modifiée en édition n'était jamais enregistrée (passée par valeur), et `carroye` / `texte` / `alerte` étaient vidés à chaque enregistrement (champs absents du formulaire).
- Clic droit → véhicule → fiche complète : le véhicule était effacé par `resetCreateWizard`.
- Carroyage résolu sur une grille chargée en mémoire (`sharedGridMeta`), plus sur celle affichée.
- `apiCall()` ne rejette jamais : plus de bouton bloqué ni d'échec muet. Échap ferme fiche, assistant et modale GPS.

### Jeton CSRF d'un onglet resté ouvert

⚠️ Le jeton de `<meta name="csrf-token">` expire après `WTF_CSRF_TIME_LIMIT` (**1 h**, défaut Flask-WTF) : un onglet ancien échouait à la première écriture, souvent après un formulaire entièrement rempli. `static/js/csrf_refresh.js` (chargé après `toast.js` sur `index`, `edit`, `field_dispatch`, `analyse_ops`) renouvelle la balise via `GET /api/csrf-token` toutes les 15 min et au retour sur l'onglet. Le handler `CSRFError` renvoie `code: "csrf"` : `apiPost` (`main.js`) et `apiCall` (`pcorg.js`) renouvellent alors le jeton et **rejouent la requête une fois**. L'ouverture de l'assistant de création et de l'édition vérifie la session d'abord (`checkSessionBeforeForm`) et propose de recharger si l'**authentification** elle-même a expiré, avant la saisie plutôt qu'après. Tout module qui lit la balise `<meta>` au moment de l'appel (et non au chargement) en profite sans modification.

## Aide à la saisie et précédents des fiches (`pcorg_assist.py`)

Blueprint `pcorg_assist_bp` (user, CSRF actif), consommé par `pcorg.js` (préfixe `pca*`).

- **Suggestions** : `POST /api/pcorg/assist/suggest`, appelé par l'assistant de création dès 25 caractères (debounce 1,2 s, AbortController). Haiku 4.5, prompt bâti sur les référentiels de l'app (`CTX_DESCRIPTIONS`, `URGENCY_LABELS`, `pcorg_lists`) restreints aux catégories autorisées. ⚠️ Jamais appliquées d'office ; toute catégorie hors référentiel ou non autorisée est écartée (`validate_suggestion`). Cache 15 min, 4 appels simultanés max, échec silencieux.
- **Doublons** : sans LLM, fiches du même event/year ouvertes **depuis moins de 7 jours** ou créées depuis moins de 2 h ; TF-IDF + Jaccard, bonus zone et GPS ; la position seule ne suffit pas.
- **Précédents** : bloc « Précédents » de la fiche, à la demande. Autres éditions (ou tous événements) ; pour une fiche SAISON, seules les fiches SAISON à ± 30 jours sont exclues (`scope_filter`), préfiltre Mongo `$text` puis classement Python ; top 5 avec clôture et chronologie. 15-200 ms sur 30 000 fiches. « Synthèse IA » : 3-5 puces fondées uniquement sur les précédents, cache 30 j (`pcorg_precedent_syntheses`).
- ⚠️ **Index texte `pca_text`** créé à la volée sur `pcorg` si AUCUN index texte n'existe (une collection n'en porte qu'un). `PCA_CREATE_TEXT_INDEX=0` le désactive.
