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

### Droits et validations (côté serveur)

- Les **catégories autorisées par groupe** (`get_user_allowed_categories`) ne filtraient que le widget, côté navigateur : `live`, `stats`, `closed`, `search`, `detail` et toutes les écritures les appliquent maintenant (`_pcorg_cat_query`, `_pcorg_cat_allowed`).
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
- **Doublons** : sans LLM, fiches du même event/year ouvertes ou de moins de 2 h ; TF-IDF + Jaccard, bonus zone et GPS ; la position seule ne suffit pas.
- **Précédents** : bloc « Précédents » de la fiche, à la demande. Autres éditions (ou tous événements), préfiltre Mongo `$text` puis classement Python ; top 5 avec clôture et chronologie. 15-200 ms sur 30 000 fiches. « Synthèse IA » : 3-5 puces fondées uniquement sur les précédents, cache 30 j (`pcorg_precedent_syntheses`).
- ⚠️ **Index texte `pca_text`** créé à la volée sur `pcorg` si AUCUN index texte n'existe (une collection n'en porte qu'un). `PCA_CREATE_TEXT_INDEX=0` le désactive.
