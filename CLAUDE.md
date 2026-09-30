# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

COCKPIT est une application web de supervision en temps réel pour la gestion d'événements (festivals, événements sportifs). Elle affiche une timeline opérationnelle avec widgets trafic, météo, alertes et parkings.

## Stack

- **Backend** : Flask (Python 3.10+), MongoDB, Waitress (prod)
- **Frontend** : HTML/CSS/JS vanilla, Leaflet.js (carto), Chart.js (graphiques)
- **Auth** : JWT stateless via cookies, rôles hiérarchiques (user < manager < admin)

## Commandes

```bash
# Installation
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Dev (port 5008, debug activé)
python app.py
# ou avec CODING=true pour contourner l'auth
CODING=true python app.py

# Production (port 4008, Waitress)
TITAN_ENV=prod python app.py
```

Pas de tests automatisés ni de linter configurés.

## Variables d'environnement

| Variable | Description | Défaut |
|----------|-------------|--------|
| `TITAN_ENV` | `dev` ou `prod` | `dev` |
| `SECRET_KEY` | Clé secrète Flask | valeur dev (interdit en prod) |
| `JWT_SECRET` | Clé JWT | valeur dev (interdit en prod) |
| `MONGO_URI` | URI MongoDB | `mongodb://localhost:27017/` |
| `CODING` | `true` bypass l'auth en dev | `false` |
| `COCKPIT_ANTHROPIC_API_KEY` | Clé API Anthropic de Cockpit (clé console « cockpit-prod »). ⚠️ Seule lue : `ANTHROPIC_API_KEY` est volontairement ignorée (lue par tout script du même compte Windows, elle rendait la consommation indiscernable) | — (routes IA en 503 si vide) |
| `CLAUDE_MODEL` | Modèle Claude utilisé par l'Assistant IA | `claude-sonnet-5-5` |
| `CLAUDE_TIMEOUT_SECONDS` | Timeout HTTP appel Claude (entre 2 chunks SSE) | `120` |
| `CLAUDE_MAX_TOKENS` | `max_tokens` envoyé à Claude | `16384` |
| `CLAUDE_MAX_TOKENS_RETRY` | `max_tokens` du retry sur troncature `stop_reason=max_tokens` | `32000` |
| `CLAUDE_RETRY_MAX_ATTEMPTS` | Nombre d'essais sur erreurs réseau / HTTP 429-503-529 | `3` |
| `CLAUDE_RETRY_BACKOFF_BASE_S` | Base de l'exponential backoff entre retries | `1.0` |
| `CRISE_JWT_SECRET` | Clé HS256 pour les sessions animateur d'exercice de crise | — (refus de démarrage en prod si vide) |
| `CRISE_JWT_TTL_HOURS` | Durée du cookie de session animateur | `8` |
| `CRISE_PIN_LOCKOUT_THRESHOLD` | Nombre d'échecs avant lockout d'IP | `6` |
| `CRISE_PIN_LOCKOUT_WINDOW_MIN` | Fenêtre d'évaluation des échecs | `15` |
| `CRISE_PIN_LOCKOUT_DURATION_MIN` | Durée du lockout après dépassement du seuil | `60` |
| `VALHALLA_URL` | URL du service Valhalla externe (calcul d'itinéraires) | `http://localhost:8002` |
| `VALHALLA_TIMEOUT_SECONDS` | Timeout HTTP des appels Valhalla | `5` |
| `ROUTING_WAZE_MAX_AGE_MIN` | Ancienneté max des alertes Waze prises en compte pour les pénalités | `30` |
| `CLAUDE_EFFORT` | `output_config.effort` des rapports IA (modèles qui le supportent) | `medium` |
| `RETEX_MAX_TOKENS` | `max_tokens` du RETEX de fin d'édition | `24000` |
| `PCA_CREATE_TEXT_INDEX` | `0` interdit la création à la volée de l'index texte `pca_text` sur `pcorg` | `1` |
| `WAHA_WEBHOOK_SECRET` | Secret HMAC des webhooks WAHA (`/api/wa/webhook`, SHA-512 attendu, SHA-1 tolérée avec avertissement) | — (vide : bypass en dev, **refus de tous les webhooks en prod**) |
| `OLLAMA_URL` | URL Ollama (résumés périodiques Alfred) | `http://srv-safe-docker.aco.local:11434` |
| `OLLAMA_MODEL` | Modèle Ollama des résumés | `alfred` |
| `OLLAMA_TIMEOUT` | Timeout lecture d'un résumé (s) | `300` |
| `OLLAMA_MAX_MESSAGES` | Nombre max de messages injectés dans le prompt de résumé | `400` |
| `ALFRED_ASK_URL` | Wrapper tool-calling Alfred sur la VM (mentions) | `http://srv-safe-docker.aco.local:5005/alfred/ask` |
| `ALFRED_ASK_SECRET` | Secret HMAC-SHA256 partagé avec le wrapper | — (mentions désactivées si vide) |
| `ALFRED_ASK_TIMEOUT` | Timeout d'une question au wrapper (s) | `90` |
| `ALFRED_FOLLOWUP_SECONDS` | Fenêtre de suite de conversation sans nouveau @alfred | `420` |
| `ALFRED_DM_REFUSAL_COOLDOWN` | Délai min entre deux refus DM au même contact (s) | `3600` |

## Architecture

```
app.py          → Routes Flask principales + auth JWT + CSRF
traffic.py      → Blueprint trafic (API Waze, cache 60s)
merge.py        → Utilitaire de fusion/sync données (UUID5 déterministe)
templates/      → Jinja2 (index, doors, terrains, general-stats, edit)
static/js/      → Modules JS par fonctionnalité
static/css/     → style.css (principal) + common.css (partagé)
static/libs/    → Bibliothèques tierces (Leaflet)
```

### Fichiers clés par taille/importance

- `app.py` (~1140 lignes) : cœur du backend, toutes les routes REST
- `static/js/timeline.js` (~2530 lignes) : moteur timeline, clustering, NOW line
- `static/css/style.css` (~1900 lignes) : layout grid, timeline, widgets
- `merge.py` (~920 lignes) : synchronisation et calculs de données

### Collections MongoDB

- `timetable` : événements chronologiques `{event, year, data: {date: [événements]}}`
- `parametrages` : config par événement/année
- `evenement` : références des événements (IDs Skidata)
- `meteo_previsions`, `donnees_meteo` : météo
- `data_access` : compteurs/accès Skidata
- `todos` : listes de tâches
- `traffic_alerts` : alertes Waze

### Authentification

- Décorateur `@role_required("user"|"manager"|"admin")` sur les routes protégées
- L'app vérifie `cockpit` dans les apps autorisées du token JWT
- En prod, les clés par défaut provoquent une erreur au démarrage

### Patterns frontend

- État global : `window.selectedEvent`, `window.selectedYear`
- Appels API via `apiPost()` avec headers CSRF
- Simulation timeline en console : `TimelineClock.setSim("2025-09-26 14:35")`, `.play()`, `.setSpeed(5)`

## Règles strictes

- **JAMAIS de guillemets typographiques** dans le code JS/CSS/Python. Utiliser uniquement les apostrophes droites `'` et guillemets droits `"`. Les curly quotes `'`, `'`, `"`, `"` provoquent des SyntaxError silencieuses.

## Intégration Vision (app externe `vision-a0f55.web.app`)

L'app Vision (scan billets véhicule, repo voisin `../vision`) est **JWT-gated par Cockpit** mais **totalement dissociée de Field** : module Python séparé, collections MongoDB séparées, JS séparé, modales séparées. La page admin Cockpit (`field_dispatch.html`) regroupe les deux UIs (section Field + section Vision) pour la commodité opérationnelle, mais aucun code n'est partagé entre les deux apps.

### Architecture

- **Module Python** : `cockpit/vision_admin.py` (blueprint `vision_admin_bp` enregistré dans `app.py` à côté de `field_bp`, exempté de CSRF). Réutilise uniquement les helpers génériques de `field.py` (`admin_required`, `_get_mongo_db`, `_client_ip`, `_rate_limit_pair`, `_generate_pairing_code`, `_now`, `_iso`, `_event_end_datetime`) — aucun accès aux collections `field_*`.
- **Collections MongoDB** : `vision_pairings` (codes 6 chiffres, TTL 15 min), `vision_devices` (tablettes Vision enrôlées, avec `tablet_uid` stable + `current_user`), `vision_sessions` (une entrée par identification opérateur, fermée à la déconnexion ou par sweep auto-logout 4 h). Indexes créés lazy au premier accès.
- **JS** : `static/js/vision_admin.js` (IIFE autonome avec ses propres helpers HTTP, son state, ses modales). Chargé après `field_admin.js` dans `field_dispatch.html`.
- **UI** : section "Tablettes Vision" dans `field_dispatch.html` sous la section Field, avec bouton dédié "Appairer une tablette Vision", table dédiée (`#vision-devices-table`), modales dédiées (`#vision-pair-modal`, `#vision-codes-modal`).

### Flux opérationnel

1. **Admin** ouvre Field Dispatch → section "Tablettes Vision" → bouton "Appairer une tablette Vision" → saisit nom + lieu (Ouest/Panorama/Houx) → un code 6 chiffres est généré.
2. **Opérateur** ouvre `https://vision-a0f55.web.app` directement sur la tablette (URL bookmarkée ou PWA, **pas via Field**) → écran bleu de pairing → saisit le code → Vision appelle `POST https://cockpit.lemans.org/field/api/vision/pair` (CORS depuis `vision-a0f55.web.app`).
3. **Cockpit** vérifie le code dans `vision_pairings`, génère un JWT RS256 (`exp = fin événement + 1 jour`, fallback 24 h), consomme le code, crée un doc `vision_devices` pour l'inventaire admin, retourne le JWT.
4. **Vision** stocke le JWT en `localStorage.vision_jwt`, recharge la page, l'app démarre.

### Routes

Toutes sous `/field/*` pour profiter de la whitelist d'auth Cockpit (`/field/*` est public sans portail) :

- `POST /field/api/vision/pair` (**public + CORS**) — échange code → JWT. Body : `{code, tablet_uid?}`.
- `POST /field/api/vision/heartbeat` (**public + CORS**, JWT Bearer) — remontée batterie/GPS + matérialisation révocation. Si device introuvable ou `revoked`, retourne `403 {error: "revoked"}` → la tablette purge son JWT et retombe sur le pairing. Met à jour la session opérateur active si elle existe.
- `POST /field/api/vision/identify` (**public + CORS**, JWT Bearer) — scan QR badge → lookup planbition. **Tente d'abord `person_id_external` (PersonID Adecco, index unique sparse), puis fallback sur `employee_number`** (champ historique). Crée une entrée `vision_sessions` avec `scanned_code`, `id_source` (`"person_id_external"` ou `"employee_number"`), `person_id_external`, `employee_number` canonique. Met à jour `vision_devices.current_user` avec les mêmes champs. Body : `{employee_number, tablet_uid?}` (le nom du champ reste `employee_number` côté API par compat, mais peut contenir n'importe lequel des deux identifiants). Erreur `unknown_employee` (404) si non trouvé (blocage strict).
- `POST /field/api/vision/logout` (**public + CORS**, JWT Bearer) — clôt la session opérateur active (`ended_reason: "logout"`), efface `current_user`. Le JWT reste valide.
- `GET /field/admin/vision/pairings` (admin) — liste codes actifs.
- `POST /field/admin/vision/pairings` (admin) — créer un code (`{name, lieu, event, year, notes?}`).
- `DELETE /field/admin/vision/pairings/<code>` (admin) — annuler un code.
- `GET /field/admin/vision/devices` (admin) — liste devices Vision enrôlés.
- `POST /field/admin/vision/devices/<id>/lieu` (admin) — changer le lieu (`{lieu}`).
- `POST /field/admin/vision/devices/<id>/revoke` (admin) — révoquer ; effet effectif au prochain heartbeat de la tablette (sweep côté serveur).
- `DELETE /field/admin/vision/devices/<id>` (admin) — supprimer définitivement.
- `GET /field/admin/vision/sessions?tablet_uid=&event=&year=&employee_number=` (admin) — historique des sessions opérateur (modale "Historique" dans `vision_admin.js`).

### JWT

- Algo RS256, `iss="cockpit-vision"`, `exp = parametrages.data.globalHoraires.demontage.end + 1 jour` (fallback 24 h).
- Clés RSA dans `cockpit/keys/vision_jwt_{private,public}.pem`. Privée dans `.gitignore` (`keys/*_private.pem`). Publique embarquée en dur dans `vision/associer.html` (`VISION_JWT_PUBKEY_PEM`). Validation côté Vision en local (`crypto.subtle.verify`) — pas d'appel réseau pour valider, mode hors ligne préservé.
- Variables d'env : `VISION_JWT_PRIVATE_KEY` (path override), `VISION_APP_URL` (default `https://vision-a0f55.web.app/associer.html`).

### CORS

- Whitelist `VISION_ALLOWED_ORIGINS` dans `vision_admin.py` : `["https://vision-a0f55.web.app", "https://vision-a0f55.firebaseapp.com"]`. Helper `_cors_response()` + preflight OPTIONS géré explicitement sur `/field/api/vision/pair`.

### Sync MongoDB

- `vision_sync.py` propage `device_id` et `device_name` des docs `immatriculations` Firestore vers `vision_immatriculations` (index `device_id` ajouté).
- ⚠️ **Chaque lecture Firestore est facturée.** Jusqu'au 29/09/2026 le script relisait tout l'événement à chaque passage (10 719 docs × 288 passages/jour ≈ 3,1 M lectures, ~1,60 €/jour, ~25 €/mois alors que VISION ne servait plus). Désormais, dans la tâche planifiée « Collecte LHPI vision » (toutes les 5 min) : lecture incrémentale sur `date` (ISO UTC, triable en chaîne, recouvrement 15 min, index mono-champ automatique, pas d'index composite), puis contrôle `count()` sur l'événement (~11 lectures) qui déclenche une resync complète si Firestore compte plus de docs que la base (tablette hors ligne remontant des scans datés dans le passé). Resync complète aussi toutes les 24 h et à chaque changement d'événement ; blacklist au plus toutes les 60 min. État dans `vision_config{_id: "sync_state"}` (`date_cursor`, `last_full_at`, `last_full_key`, `last_blacklist_at`) : le supprimer force une resync complète.
- ⚠️ **Le script doit finir par `os._exit`.** Les threads gRPC de `firebase_admin` gardaient le process vivant après « Sync terminee » ; avec `MultipleInstances = IgnoreNew`, la tâche restait bloquée jusqu'à sa limite de 72 h, puis repartait (d'où une facture visible certains jours seulement).

### Constantes

- `VISION_LIEUX = ["Ouest", "Panorama", "Houx"]` dans `vision_admin.py` — à mettre à jour si on ajoute des lieux Vision (et synchroniser les options du dropdown dans `field_dispatch.html` + le validator côté Vision).

## Assistant IA — résumé de période des fiches PC Organisation

Sur la sidebar de `index.html`, `edit.html`, `analyse_ops.html`, le bouton **« Assistant IA »** (classe `.sidebar-ai`) ouvre une modale qui génère un compte-rendu structuré d'une période sur la collection `pcorg`. Réservé au rôle **manager** (et au-dessus).

### Architecture

- **Module Python** : `pcorg_summary.py` — helpers purs (`compute_kpis`, `select_fiches_for_prompt`, `build_prompts`, `call_claude`, `save_summary`, `list_summaries`, `get_summary`, `delete_summary`, `generate_period_summary`). Appel HTTP direct à `https://api.anthropic.com/v1/messages` (pas de SDK `anthropic`), pattern calqué sur `traffic.py` (Waze).
- **Routes** dans `app.py` (à côté des routes `/api/pcorg/*`) :
  - `POST /api/pcorg/summary/generate` (`manager`) — body `{event, year, period_start, period_end, model?, dry_run?}` (ISO, datetime-local accepté → interprété en Europe/Paris). Court-circuite l'appel Claude si `kpis.total == 0` (sections "RAS"). `model` accepte une whitelist (`claude-sonnet-5-5`, `claude-sonnet-5`, `claude-sonnet-4-6`, `claude-sonnet-4-5`, `claude-opus-4-7`, `claude-opus-4-6`, `claude-haiku-4-5`) sinon fallback `CLAUDE_MODEL`. `dry_run=true` retourne le prompt assemblé sans appeler Claude (itération rapide sans coût).
  - `GET /api/pcorg/summary/list?event=&year=` (`manager`) — liste légère (sans `kpis`/`sections`).
  - `GET /api/pcorg/summary/<id>` (`manager`) — détail complet.
  - `DELETE /api/pcorg/summary/<id>` (`admin`).
  - `GET /api/pcorg/summary/usage?from=&to=&event=&year=` (`admin`) — agrège `input_tokens`/`output_tokens`/cache tokens de `pcorg_summaries` + `pcorg_n1_retros`, calcule un coût USD approximatif via `MODEL_PRICING_USD_PER_MTOK` (cache création +25 % input, lecture cache -90 % input). Tarifs figés en code, à mettre à jour si Anthropic révise.
- **Frontend** : `static/js/ai_assistant.js` (IIFE autonome). Les templates exposent `window.__userIsManager` à côté de `window.__userIsAdmin` ; le JS bloque l'ouverture de la modale aux non-managers (en plus du backend).
- **Pipeline parallélisé** : `generate_period_summary` lance `compute_kpis` / `compute_comparisons` / `get_upcoming_timetable` / `compute_attendance_block` / `compute_door_reinforcement` / `select_fiches_for_prompt` dans un `ThreadPoolExecutor`, puis kicke `get_or_build_n1_retrospective` (1er appel Claude) dès que `comparisons` est dispo — il tourne en parallèle du reste. Gain ~5-10 s.
- **Bloc Billetterie & Fréquentation** (`compute_attendance_block`) : 3 slots `yesterday/today/tomorrow` injectés dans le user prompt. Chaque slot contient `billets_vendus`, `pic_observed` + `pic_observed_hour` (heure 'HHhMM' du pic constaté, jours passés uniquement, source : `historique_controle.frequentation` → `data_access` → archive), `pic_prev` + `pic_prev_hour` (pic et heure de l'édition précédente, jour-équivalent aligné sur la date de course), `pic_projection` (pic projeté = `pic_prev * billets_N / billets_prev`), `delta_pct_vs_prev`. Le system prompt impose à Claude d'inclure dans la `synthese` (a) le pic constaté de la veille avec heure et delta, (b) le pic projeté du jour avec l'heure approximative attendue (= `pic_prev_hour`).
- **Prompt caching** : le system prompt des 2 appels Claude est marqué `cache_control: ephemeral` (cache 5 min côté Anthropic). Sur les appels rapprochés (rapport matinal quotidien notamment), le system n'est plus refacturé. `usage` enregistre `cache_creation_input_tokens` / `cache_read_input_tokens` pour la télémétrie.
- **Robustesse** : retry exponentiel (1 s, 2 s, 4 s) sur `claude_unreachable`, `claude_stream_interrupted`, HTTP `429`/`503`/`529`. Retry one-shot sur `stop_reason=max_tokens` avec budget `CLAUDE_MAX_TOKENS_RETRY` et consigne de concision.

### Collection MongoDB `pcorg_summaries`

```javascript
{
  _id, event, year,
  period_start, period_end, created_at,
  created_by, created_by_name,
  fiches_count, truncated,
  selection_detail: { total, majors, others, selected, cut, majors_capped,
                      max_fiches, selected_by_urgency },
  kpis: { total, open, closed, by_category, by_urgency,
          top_zones, top_sous_classifications, top_operators, avg_duration_min },
  sections: { synthese, faits_marquants, secours, securite, technique, flux,
              fourriere, recommandations, prochaines_24h },
  raw_text, model,
  usage: { input_tokens, output_tokens,
           cache_creation_input_tokens, cache_read_input_tokens,
           retried_for_truncation? }
}
```

Index : `(event, 1), (year, 1), (period_start, -1)` créé lazy au premier accès.

### Prompt Claude

Le `system` impose un **JSON strict à 9 clés** (`synthese, faits_marquants, secours, securite, technique, flux, fourriere, recommandations, prochaines_24h`) en français. Si le retour n'est pas parsable, fallback sur extraction par regex des paires complètes + récupération de la dernière clé tronquée. Tolérance liste/dict dans `_normalize_sections` : si Claude renvoie une liste pour une section, elle est convertie en puces `\n- ` propres. Plafond de **80 fiches** envoyées à Claude (priorité aux fiches `niveau_urgence ∈ {EU, UA}` ou `is_incident: true` qui sont toutes incluses) ; flag `truncated` + `selection_detail.majors_capped` exposés (le second loggue un warning si > 80 majeures, contexte normal perdu).

### Erreurs

- `ANTHROPIC_API_KEY` vide → **503** `{ok: false, error: "ANTHROPIC_API_KEY non configuree"}`.
- Anthropic injoignable / timeout → **502** `{ok: false, error: "claude_unreachable"}`.
- HTTP non-2xx Claude → **502** `{ok: false, error: "claude_http_<code>"}`.

### Génération en tâche de fond, coûts et budget (septembre 2026)

- `POST /api/pcorg/summary/generate` renvoie `202 {ok, job}` ; le front suit `GET /api/pcorg/summary/generate/status?job=` (étape, %, caractères/tokens reçus, secondes). Registre en mémoire (`start_summary_job`), un job par utilisateur, **suppose un seul process** (waitress). `dry_run` reste synchrone et gratuit (rétro N-1 lue en cache seulement). Le rapport matinal appelle toujours `generate_period_summary` en direct.
- **Structured outputs** (`output_config.format`, schéma construit depuis `section_keys`) + `output_config.effort` (défaut `medium`, env `CLAUDE_EFFORT`), envoyés seulement aux modèles qui les supportent ; sur HTTP 400, second essai sans. Le parseur regex n'est plus qu'un filet.
- ⚠️ Le thinking adaptatif (Sonnet 5.5) partage `max_tokens` et est facturé en sortie ; les `thinking_delta` ne servent qu'à la barre de progression. `stop_reason: "refusal"` lève `ClaudeError("claude_refusal")`.
- Prompt caching gardé pour l'interactif, **désactivé** pour le rapport matinal, la rétro N-1 et l'analyse fréquentation (écriture +25 % jamais relue). La consigne de concision du retry sur troncature va dans le tour user.
- **Corrections** : `sections_corrected.<section>` (la dernière l'emporte) ; UI et mail affichent ce texte avec « corrigé par X ». La note rétro N-1 n'apparaît jamais dans le mail.
- **Coût** : formule unique `compute_cost_usd`. `input_tokens` EXCLUT les tokens cache. Tarifs vérifiés le 29/09/2026 (`PRICING_VERIFIED_ON`).
- `record_ai_usage(db, feature, model, usage, meta)` journalise dans **`ai_usage_log`** les appels non stockés ailleurs. `/api/pcorg/summary/usage` agrège `pcorg_summaries`, `pcorg_n1_retros`, `scan_analyses` et `ai_usage_log` par fonction et par modèle. Ne pas journaliser deux fois un appel déjà stocké avec son `usage`.
- **Budget** : `cockpit_settings._id="ai_budget"` `{monthly_usd, block_when_exceeded}` ; si bloquant et atteint, `check_ai_budget` lève `ClaudeError("budget_exceeded")` → 429. Vue « Coûts IA » (admin) dans la section Mémoire IA de `/edit`.
- **Mémoire** : `scope.year` et `scope.phase` filtrent réellement (phase déduite de `globalHoraires`, inconnue → directives à phase exclues) ; directives de section groupées « Pour la section X ». Tri poids desc puis date desc.
- **Édition précédente** : une seule définition, `find_previous_edition` (la plus récente antérieure ayant date de course et données), utilisée par les comparaisons, la rétro et les renforts portes.
- Sélection des fiches non majeures : échantillon stratifié sur la période (12 tranches max). Cache rétro : clé arrondie à l'heure. `pcorg_morning_report.py --dry-run` = prompt seul, `--no-send` = génère sans mail.


## Exercices de crise — auth PIN animateur

Sous-arbre `cockpit/crise/<exercise_id>/` (ex: `gpmotos2026/`) qui héberge les ressources d'animation des exercices de crise. Servi sous `/crise/...` par **deux blueprints distincts** :

- `crise_bp` (défini dans `app.py`) : catch-all statique sans auth pour les ressources publiques (hub `crise/index.html`, landing `<exercise>/index.html`, `<exercise>/player.html`, `<exercise>/livefeed.html`, `crise/assets/*`). Strip défensif des Set-Cookie. Filet supplémentaire : refuse de servir en clair les patterns protégés (master.html / files/* / input/* / auth) au cas où la priorité Werkzeug dévierait.
- `crise_auth_bp` (`crise_auth.py`) : routes spécifiques avec **PIN 8 chiffres** pour l'accès animateur. **Doit être enregistré avant `crise_bp`** dans `app.py` pour priorité de routing.

### Architecture

- **PIN** : 8 chiffres, **un par exercice**, hashé avec `werkzeug.security.generate_password_hash(method="pbkdf2:sha256:600000")` (~100 ms par essai côté serveur). Stocké dans MongoDB `crise_config`.
- **Session** : JWT HS256 signé avec `CRISE_JWT_SECRET`, claim `{iss: "cockpit-crise", sub: "crise-master", exercise: <id>, iat, exp}`. Cookie `crise_session` httpOnly + Secure (prod) + SameSite=Lax + **Path=/crise/<exercise>/** (cloisonnement par exercice). TTL 8 h par défaut.
- **CSRF** : la protection Flask-WTF reste **active** sur le POST d'auth (le template injecte `csrf_token()` et le JS l'envoie via `X-CSRFToken`). `crise_auth_bp` n'est PAS exempté de CSRF.
- **Anti-bruteforce** :
  - 0–2 échecs / 15 min : autorisé immédiatement
  - 3–5 échecs / 15 min : délai exponentiel côté serveur (1 s, 2 s, 4 s, 8 s)
  - 6+ échecs / 15 min : **lockout 1 h** sur l'IP pour cet exercice (réponse 429, ne s'allonge pas en bouclant)
  - tentatives loggées dans `crise_auth_attempts` (audit RETEX), TTL 1 h
- **Validation exercise_id** : regex `^[a-z0-9_\-]{1,64}$` + le sous-dossier `cockpit/crise/<exercise_id>/` doit exister (anti path traversal). `safe_join` sur tous les noms de fichiers servis.

### Routes

Toutes sous `/crise/<exercise_id>/...` :

- `GET  /crise/<exercise>/auth` (public) — page de login PIN avec 8 cases auto-submit. Si déjà authentifié → redirige vers `master.html`.
- `POST /crise/<exercise>/auth` (public, **CSRF active**, rate-limited) — body `{pin: "12345678"}`. Réponses : `200 {ok: true, redirect}` (cookie posé), `401 {error: "invalid_pin"}`, `429 {error: "locked_out", retry_after}`, `503 {error: "not_configured"}`.
- `GET  /crise/<exercise>/auth/logout` ou POST — efface le cookie, redirige vers `auth`.
- `GET  /crise/<exercise>/master.html` (auth requise, JWT cookie) — sert `master.html` de l'exercice. Sans cookie valide : 302 vers `auth`. `Cache-Control: no-store`.
- `GET  /crise/<exercise>/files/<path>` (auth requise) — sert les fiches d'animation.
- `GET  /crise/<exercise>/input/<path>` (auth requise) — sert les médias d'inject (photos, vidéos, PDF).

### Collections MongoDB

- `crise_config` : `{exercise_id, pin_hash, created_at, updated_at, pin_version}`. Index unique sur `exercise_id`. Un doc par exercice.
- `crise_auth_attempts` : `{exercise_id, ip, ts, success, ua}`. Index `(exercise_id, ip, ts DESC)` et TTL 1 h sur `ts`.

### Initialisation / rotation du PIN

```bash
python scripts/init_crise_pin.py
```

Le script prompt l'`exercise_id` (avec auto-détection des dossiers existants), demande deux fois le PIN (saisie masquée via `getpass`), met en garde si PIN trivial (`12345678`, mêmes chiffres, etc.), upsert dans `crise_config` avec incrément de `pin_version`, et **purge les tentatives précédentes** pour cet exercice. **Aucun PIN n'est jamais loggé**.

### Limites connues

- **PIN partagé entre animateurs** : si un animateur fuit le PIN, tous les accès sont compromis (rotation possible via `init_crise_pin.py`, qui invalide aussi les sessions actives via `pin_version` — mais à ce stade `pin_version` n'est pas vérifié au moment du JWT decode ; pour invalider toutes les sessions, changer aussi `CRISE_JWT_SECRET` ou attendre l'expiration).
- **Accès SSH au serveur** = lecture directe des fichiers `cockpit/crise/<exercise>/master.html`. C'est inhérent à toute appli web — si le service info a un accès admin OS, aucune protection applicative ne tient.
- **Bruteforce hors-ligne** impossible : le hash n'est pas exposé côté client, seul l'oracle serveur peut le tester (et il rate-limite + log).

### Pièges

- Les routes spécifiques de `crise_auth_bp` doivent être **enregistrées avant** `crise_bp` dans `app.py`. Werkzeug priorise les routes plus spécifiques, mais l'ordre d'enregistrement compte en cas d'ambiguïté.
- Le `_crise_strip_cookies` de `crise_bp` n'affecte pas `crise_auth_bp` (blueprints distincts, `after_request` indépendants). Les Set-Cookie de l'auth passent bien.
- Le path du cookie est `/crise/<exercise>/` : changer cette base casse les sessions en cours (idem si on renomme un dossier d'exercice).
- En prod, `CRISE_JWT_SECRET` doit être défini (refus de démarrage sinon, dans `crise_auth.py` au moment de l'import).

## Live feed régie TV (exercices de crise)

Mur d'images plein écran sur TV 75" piloté en temps réel depuis `master.html` via une régie graphique. Permet à l'animateur de diffuser un input (photo / vidéo / PDF / CSV / message libre) précédé d'une annonce flash rouge clignotante avec son. Multi-TV prévu nativement (toutes les TV affichent la même chose).

### Architecture

- **Backend** : extension du blueprint `crise_auth_bp` dans `cockpit/crise_auth.py` (pas de nouveau blueprint). Les écritures sont gated par cookie JWT animateur + CSRF (Flask-WTF reste actif). Lectures publiques (la TV n'a pas de PIN, le contenu est de toute façon visible dans la salle).
- **Frontend TV** : `crise/gpmotos2026/livefeed.html` — page autonome avec polling 1 s, machine à états IDLE → ANNOUNCING (3,5 s flash + son) → DISPLAYING. Click-to-start au premier load (geste utilisateur Chrome pour autoplay vidéo + son). Backoff sur erreur réseau (5 s puis 15 s).
- **Frontend régie** : `crise/gpmotos2026/regie.js` chargé par `master.html` sous route protégée `/crise/<ex>/regie.js`. IIFE autonome, init paresseuse au premier `showView('regie')`.
- **Manifeste partagé** : `crise/gpmotos2026/livefeed_inputs.json` (source de vérité pour validation côté serveur ET résolution `input_id → file` côté TV). Modifier `inputsData` dans `master.html` impose de régénérer ce JSON.

### Routes

Toutes sous `/crise/<exercise_id>/livefeed/...` (préfixe du blueprint `crise_auth_bp`) :

- `GET /livefeed/state?client=<uuid>` (**public**, TV) — retourne `{version, server_ts, payload, tv_clients[]}`. Le query param `client` sert de heartbeat implicite (le serveur upsert l'entrée dans `tv_clients[]`).
- `POST /livefeed/state` (**JWT + CSRF**) — body `{type, ...}`. Validation stricte du payload via `_validate_livefeed_payload` + manifeste. Réponses : `200 {ok, version, payload}`, `401 {error: "unauthorized"}`, `422 {error: "invalid_payload", detail}`.
- `POST /livefeed/clear` (**JWT + CSRF**) — équivalent à `POST /state` avec `{type: "idle"}`.
- `GET /livefeed/csrf` (**JWT**) — retourne `{csrf_token}` consommé par `regie.js` au boot.
- `GET /livefeed/inputs.json` (**JWT**) — sert le manifeste validé (utilisé par `regie.js` pour construire la grille).
- `GET /regie.js` (**JWT**) — sert `crise/<ex>/regie.js` avec gating équivalent à `master.html`.

### Schéma payload accepté

```python
# Diffusion d'un input (photo/video/pdf/csv)
{"type": "input", "input_id": int, "announce": bool, "duration_s": int|None}

# Diffusion d'un message libre
{"type": "message", "title": str(<=120), "body": str(<=1500),
 "level": "info|warning|alert|critical", "announce": bool, "duration_s": int|None}

# Retour à l'écran d'attente
{"type": "idle"}
```

`duration_s` ∈ `[1, 1800]` ou `None` (clear manuel). `announce=true` déclenche le flash rouge + son d'alerte avant l'affichage. Le `started_at` est posé serveur (utile pour la persistence reboot TV).

### Collections MongoDB

- `crise_livefeed_state` : **singleton par exercice** (index unique sur `exercise_id`). Document mis à jour via `find_one_and_update` avec `$inc: {version: 1}` (race conditions sérialisées). Contient `payload`, `tv_clients[]` (multi-TV avec `last_seen`), `version` monotone.
- `crise_livefeed_audit` : append-only, **TTL 7 jours** sur `ts`. Chaque action (`set` / `clear`) loggée avec timestamp + IP + UA + payload pour le RETEX.

Indexes créés lazy au premier accès dans `_ensure_indexes()`.

### Multi-TV

Les TV génèrent un `client_id` stable dans `sessionStorage` (format `tv-xxxx-yyyy`) et l'envoient en query param sur chaque GET `/state`. Le serveur upsert l'entrée dans `tv_clients[]`. Côté régie, on filtre les clients vus < 30 s pour afficher le compteur "TV en ligne (N)". Toutes les TV reçoivent la même diffusion (state singleton) — pas de différenciation par client.

### Gestion autoplay vidéo + son

Chrome bloque les `play()` avec son sans interaction utilisateur préalable. Solution : overlay click-to-start au premier chargement de `livefeed.html`, qui déclenche un `play()`/`pause()` factice sur l'`<audio>` d'alerte → la session est unlockée pour les futurs `play()`. Flag `sessionStorage.livefeed_unlocked = '1'`. Si le navigateur refuse quand même, fallback muted avec controls visibles.

### PDF rendering

`pdf.js` v3.11.174 hébergé localement dans `crise/gpmotos2026/assets/pdfjs/` (`pdf.min.js` ~320 Ko + `pdf.worker.min.js` ~1 Mo). Les pages sont rendues dans des `<canvas>` empilés verticalement (devicePixelRatio limité à 2). Auto-scroll lent (1 px / 30 ms), pause 5 s en bas, retour haut, boucle. Le scroll fonctionne nativement (même origine) — pas de limitation cross-origin contrairement à un CDN iframe.

### Pièges

- **Le manifeste `livefeed_inputs.json` doit rester synchronisé avec `inputsData` / `csvHeaders` / `csvRows` dans `master.html`.** Modifier l'un sans l'autre crée une divergence (ex: la régie affiche un input que le serveur rejettera, ou inversement).
- Toute nouvelle ressource servie par route protégée doit être ajoutée au regex `_CRISE_PROTECTED_RE` dans `app.py` (filet défensif du catch-all statique). Actuellement : `master.html|auth|files|input|regie.js`.
- En cas d'écriture rapide multi-clic, MongoDB `find_one_and_update` + `$inc:{version:1}` sérialise. Côté UI régie, désactivation 300 ms du bouton après clic.
- Le polling TV (1 s) génère ~3600 GET/h × N clients. Logs `GET /state` mis en `DEBUG` (silencieux par défaut) pour ne pas saturer les logs serveur.
- L'overlay click-to-start ne s'affiche qu'au premier chargement (flag `sessionStorage`). Si on rouvre l'onglet TV (refresh), il ne réapparaît pas — la session est conservée. Si l'animateur ferme la TV puis l'ouvre dans une nouvelle fenêtre privée, il faut re-cliquer.
- En cas de reboot TV, la TV reprend le state courant **sans rejouer l'annonce** (compare `started_at` avec `Date.now()`). Si `duration_s` est dépassé, retombe en idle.
- `regie.js` est gated comme `master.html` : si la session JWT expire pendant l'exercice, le rechargement de `master.html` redirige vers `/auth` mais `regie.js` ne se recharge pas tant qu'on reste sur la page. Penser à recharger après une longue session.

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

## Affluence prévisionnelle (mini widget + grand panneau)

Deux blocs de `index.html` lisent **la même route**, `/get_affluence` : le mini widget `widget-right-2` (onglets Affluence / Ventes / Sites, `static/js/affluence.js`) et le grand panneau « Analyse affluence » (`static/js/affluence_panel.js`), qui ajoute `/get_affluence_hourly` (courbe horaire des présents N vs N-1) et `/get_affluence_curves` (courbes de remplissage N / N-1 / N-2). Ils ne peuvent donc pas se contredire entre eux.

Tout part de `parametrages` : `tickets.products[*].ventes` (valeur courante) et `tickets.products[*].history[]` (snapshots hebdomadaires `{date, ventes}`). `tickets.lastUpdate` date le dernier import billetterie.

### Le panier : `globalHoraires.ticketing`, rien d'autre

**Le seul périmètre comparable d'une édition à l'autre est l'ensemble des produits référencés dans `globalHoraires.ticketing`** — les titres d'entrée enceinte générale, affectés à un jour public (`days: ["2026-09-27"]`) ou à tous (`days: "all"` pour un week-end). `_ticketing_product_names()` le construit ; toutes les sommes, la courbe de remplissage et la projection s'y restreignent.

⚠️ **Le référentiel billetterie contient des agrégats synthétiques qui double-comptent** : sur 24H CAMIONS 2025, `24C TOTAL_ENTREES`, `24C TOTAL_AA` et `24C TOTAL_PARKING` répliquent les produits de détail. Sommer tous les produits d'un doc donnait un final de 120 390 pour 52 117 titres réels. S'y ajoutent les campings et parkings, qui **saturent dès le printemps** (`AA Houx` à 99,5 % à J-92 quand les entrées sont à 23 %) : les mélanger aux entrées gonfle le taux de remplissage et écrase la projection.

⚠️ **Les noms de produits changent d'une édition à l'autre** (`24C WEEK_END` → `24C Entrée Week-end  - Course`, avec double espace). Aucun rapprochement par nom entre éditions n'est possible ni nécessaire : chaque édition a son propre panier, on ne compare que les **sommes**.

### `ventes_prev` vs `ventes_prev_final` — ne jamais confondre

C'est le piège central de ce bloc. Deux valeurs N-1 coexistent dans la réponse JSON, par jour, en total et par site :

| Champ | Sens | Comparer à |
|---|---|---|
| `ventes_prev` | N-1 **au même avancement** : la courbe N-1 interpolée au même nombre de jours avant course que N à sa dernière maj | les **ventes en cours** N |
| `ventes_prev_final` | N-1 **au soir de la course** | les **projections** (une projection est un total de fin de saison) |

Historiquement un seul champ existait, `ventes_prev`, qui portait le **final** — mais il était affiché sous le libellé « Vendus N-1 (même avancement) » et comparé aux ventes en cours. En pleine saison, ça affichait un effondrement de **-78 %** (16 172 contre les 52 117 titres finaux de 2025) là où l'édition était en réalité **+1 %** au même stade. L'alerte « Repli billetterie » du grand panneau et toutes les pastilles rouges par jour en découlaient.

Le ratio pic/ventes (`pic_prev / ventes_prev_final`) qui convertit une projection de ventes en pic de présents **doit** rester sur le final : le rapporter au N-1 de mi-saison multiplierait le pic projeté par trois.

`prev_reference_date` et `days_before` sont exposés pour que l'UI nomme la date de référence — rien ne la laisse deviner, ce n'est pas la même date calendaire mais le même J-*x*.

### Alignement au jour de course

Un jour public N est rapproché du jour public N-1 de **même offset à la course**, jamais de la même date calendaire. La référence N-1 est `prev_hist_race_date or prev_race_date` : **`historique_controle` prime sur `parametrages`**, plus fiable.

`_param_race_date()` lit `data.race` → `globalHoraires.race` → 1er jour public. Le repli compte : sur les trois éditions 24H CAMIONS, `data.race` est absent et seul `globalHoraires.race` est renseigné — sans repli, toutes les courbes sortaient vides et le graphe affichait « Pas d'historique de courbes disponible ».

⚠️ **`data.race` (naïf Paris) et `globalHoraires.race` (UTC, avec `Z`) ne désignent pas toujours le même instant** : sur 24H MOTOS 2025, `globalHoraires.race` porte l'**arrivée** (dim 20/04 15h) et `data.race` le **départ** (sam 19/04 15h). La convention Cockpit pour les Motos est le **départ, samedi 15h** — c'est ce que portent 2022, 2023, 2024, 2026 et `historique_controle{portes}` 2025. `data.race` valait `2025-04-14` (un lundi, faux) et décalait la courbe 2025 de 5 jours ; corrigé en base en août 2026.

### Projection

**Une projection par méthode, pas une méthode unique.** Chaque édition de référence (jusqu'à `MAX_REFERENCE_EDITIONS = 3`) fournit sa propre projection, à laquelle s'ajoute une projection « croissance » :

- **par édition** : `ventes_N_du_groupe / taux_de_remplissage_de_cette_édition`, sommé sur les groupes de jours (voir plus bas). Le taux est interpolé linéairement entre les deux snapshots encadrants — les relevés sont hebdomadaires avec des trous d'un mois.
- **croissance** : `ventes_prev_final × (ventes_N / ventes_prev)`, soit le final N-1 multiplié par la croissance constatée à avancement égal. Indépendante de la forme des courbes.

`total_projection` est la **moyenne pondérée des projections par édition** (`_reference_weights` : poids géométriques, N-1 pèse le double de N-2). `total_projection_low` / `_high` sont le **min/max de toutes les méthodes**, croissance comprise. `projection_spread_pct` mesure leur écart relatif.

⚠️ **La fourchette n'est pas un intervalle de confiance mais l'étendue du désaccord entre méthodes.** C'est précisément l'information utile : sur 24H CAMIONS 2026 à J-50, 2025 projette 52 632 et 2024 67 543 — 27 % d'écart pour des finals quasi identiques (53 149 et 52 117), parce que 2024 vendait beaucoup plus tard. Moyenner sans montrer cet écart affichait une fausse précision. Le grand panneau lève une alerte au-delà de 20 %, et une autre quand il n'y a qu'une seule édition de référence.

**Projection par groupe de jours, pas globale.** Un taux unique appliqué à tout le panier suppose que le mix produits de N est celui de N-1. Vrai sur le total, faux jour par jour : à J-50 sur 24H CAMIONS, le Pack VIP est écoulé à 66-81 % quand le billet Samedi en est à 14-19 %. `_ticketing_day_groups()` regroupe donc les produits par **portée de jours exprimée en offsets à la course** (`'all'`, `(0,)`, `(0, 1)`…) — signature stable d'une édition à l'autre, contrairement aux noms. Le passage au calcul par groupe a fait baisser la projection du dimanche 24H CAMIONS de 52 834 à 48 603 (−8 %) et son pic projeté de 45 683 à 42 025.

`projection_ratio` (= `total_ventes / total_projection`) ne survit que comme repli pour les sites sans historique.

⚠️ **Chaque site (parking/camping) est projeté sur SA propre courbe N-1**, pas sur le ratio des entrées. `EPINETTES` (792 vendus, final 2025 = 771) sortait à 2 906 — une jauge déjà pleine quadruplée, qui déclenchait les alertes de dépassement de capacité du grand panneau. Repli sur le ratio global si le site n'a pas d'historique N-1 exploitable.

⚠️ **Un panier `ticketing` vide écarte l'édition des références.** `_select_products` distingue `None` (pas de filtre) d'un ensemble **vide** (aucune config ticketing). 24H AUTOS 2024 est dans ce cas : 0 entrée ticketing pour 106 produits. Retomber sur tous ses produits lui ferait produire un taux de remplissage mêlant campings et agrégats — mieux vaut la perdre comme référence, ce que l'alerte de solidité signale.

⚠️ **Les sites se rapprochent par NOM entre éditions** (`prev_site_products`), et les noms bougent : `HERONNIERE` (2026) vs `HERRONIERE` (2025) ne matchent pas, `BEAUSEJOUR 1` porte le ticketing en 2026 mais c'est `BEAUSEJOUR 2` en 2025. Ces sites-là n'ont pas de N-1 et retombent sur le ratio global.

### Autres consommateurs

`/api/live-controle/counters-context` (widget compteurs) recalcule le même `projection_ratio` avec les mêmes helpers — le garder aligné. `pcorg_summary.compute_attendance_block` calcule son propre bloc Billetterie & Fréquentation, indépendant, et n'utilise le N-1 que pour des ratios de pic (pas de comparaison de ventes) : il n'était pas affecté.

### Pièges

- **La requête du parametrages N-1 est projetée.** Elle doit inclure `data.parkingsHoraires` / `data.campingsHoraires`, sinon le bloc Sites n'a jamais de N-1 — c'était le cas et personne ne l'avait vu, la colonne restant simplement vide.
- **`day_ventes` est multi-compté** (un billet week-end compte sur chaque jour) pour refléter la présence attendue ; les totaux utilisent l'**ensemble unique** des produits et ne doivent surtout pas agréger les jours.
- **`days_before` peut être négatif** quand le dernier import billetterie est postérieur au départ (normal pendant l'événement). L'interpolation borne alors sur le dernier point, donc toutes les méthodes convergent vers les ventes actuelles et la dispersion tombe à 0 % — c'est correct, pas un bug.
- **Restreindre le panier change la projection.** Sur 24H CAMIONS 2026 à J-92 : 24,6 % de remplissage tous produits contre 23,2 % sur les seules entrées, soit 47 496 contre 50 360 de projection.
- **La valeur affichée est la centrale, la fourchette est en infobulle** (`projectionTitle()`, présent dans les deux JS), sauf sur la carte KPI « Projection finale » du grand panneau où elle occupe la sous-ligne. Les pastilles de delta comparent la **centrale** au final N-1, jamais le milieu de la fourchette.

## Chaîne scans (import Excel → base → rapport → analyse)

La page `/scan-report` (admin) pilote toute la chaîne : déposer un export Excel de scans de billets, le rattacher aux entités cartographiques, écrire les documents `historique_controle`, régénérer le rapport HTML, et produire une analyse rédigée par Claude.

Elle remplace un enchaînement manuel de 5 scripts (`import_zone_scans.py`, `import_porte_scans.py`, `import_uam_help.py`, `audit_staffing_mapping.py`, `import_staffing_to_scans.py`) qui contenaient des chemins absolus `/Users/framboises/...` et `DB_NAME = 'titan_dev'` en dur. **Ces scripts sont conservés mais ne sont plus le chemin nominal.**

### Architecture

| Module | Rôle |
|--------|------|
| `scan_import.py` | Parseur xlsx, résolveur de features, constructeurs des 3 documents, archivage |
| `scan_report_build.py` | Adaptateur `complet` → contrat du gabarit HTML, génération du fichier |
| `scan_staffing.py` | Effectifs Accueil/Sécurité dérivés du calendrier, sans aucune saisie |
| `scan_analysis.py` | KPI agrégés, prompts, appel Claude, persistance |
| `scan_mapping.py` | Correction du mapping après import, reconstruction depuis `complet` |
| `scan_report.py` | Blueprint : routes, staging d'import, registre de jobs |
| `static/js/scan_report.js` | IIFE autonome : modales, mapping manuel, barres de progression |

Tous les modules reçoivent `db` en argument (`from app import db` donne `titan_dev` en dev, `titan` en prod, cf. `app.py:115`). **Ne jamais coder le nom de base en dur.**

### Format Excel attendu

Export Sirius hiérarchique (cf. `uploads/zone-complet-24HM-2024.xlsx`). Un modèle est téléchargeable depuis la page (`/scan-report/template.xlsx`), généré à la volée avec une feuille « Notice » et une feuille « Unités connues » alimentée depuis la base.

- Ligne 1 : datetime, **uniquement sur la 1re colonne de chaque groupe fusionné**
- Ligne 2 : sens `Entrée` | `Sortie` | **`Autre`**
- Ligne 3 : `SPACE_CODE - Identifiant` (ignorée)
- Colonnes A/B/C : zone | porte | device, avec report vers le bas
- Lignes 4+ : entiers, pas de 15 min, **créneaux vides omis** (non zéro-remplis)
- Colonne `Total` et ligne `zone == 'Total'` ignorées

Un groupe fusionné fait 1, 2 ou 3 colonnes selon les sens présents : le datetime n'est donc **pas toujours porté par la colonne `Entrée`**. Le parseur propage le dernier datetime rencontré (`_build_column_map`).

### Le sens « Autre »

**Ce n'est pas une catégorie de scan mais une configuration de boîtier** : un PDA non paramétré en entrée/sortie. Sur 24H MOTOS 2024, 30 boîtiers scannent exclusivement en `Autre`, et `PORTE ANNEXE` (19 389 scans) comme `PORTE PANORAMA` (21 893) ont 100 % de leur trafic dans cette colonne.

Conséquences, appliquées partout dans la chaîne :
- **compté** dans `portes.scan_count` (total de passages, sens confondus)
- **stocké à part** (`total_autre`) dans `complet`
- **exclu du calcul de présents** : sans direction, aucun solde n'est calculable
- **absent du rapport HTML** : le gabarit n'a que deux séries. La modale de régénération affiche le volume non représenté (`autre_scans_not_shown`)

Les fichiers 2025 n'ont pas cette colonne : le parseur la traite comme optionnelle.

### Les trois documents produits

Tous dans `historique_controle`, index unique `(event, year, type)`, `year` en **int**, `event` = nom cockpit majuscules (`24H MOTOS`, jamais le slug `24h_du_mans`). Datetimes **naïfs, heure locale Paris**.

| type | clé | granularité | sémantique |
|------|-----|-------------|------------|
| `complet` | `complet` | 15 min | **nouveau**. Une entrée par unité, `entree`/`sortie`/`autre` en **deltas par créneau**, `present` en cumul courant. Porte aussi `_id_feature`, `devices`, `uam_help`, `pda_renfort` |
| `frequentation` | `data` | horaire | série **globale unique** (agrégat ENCEINTE GENERALE), `entree`/`sortie` **cumulés**, `present = entree - sortie` |
| `portes` | `doors` | horaire | `[{name, doors_id, scans:[{id, timestamp (datetime BSON), scan_count}]}]`, `scan_count` = tous sens confondus |

`data_15min` de `complet` **n'a pas de champ `id`** : l'uuid n'a aucune signification inter-collection et pèse 45 des 136 octets de chaque enregistrement. Un garde-fou refuse l'écriture au-delà de 12 Mo (limite BSON 16 Mo).

`frequentation` porte en plus `source: 'scan_import'`, `excluded_autre`, `doors_without_direction` et `ignored_doors` — traçabilité de ce qui manque à la courbe de présents.

### Fuseaux horaires

**Toute la chaîne scans travaille en datetimes naïfs, heure locale Paris.** Aucun `Z`, aucun offset, nulle part. C'est la convention des documents déjà en base et de ce que lisent les autres logiciels.

Vérifié sur l'ensemble des documents : `frequentation.data[].date` et `complet.data_15min[].date` sont des **chaînes ISO sans fuseau**, `portes.doors[].scans[].timestamp` des **datetimes BSON naïfs** — identique entre l'ancienne chaîne (collecte temps réel) et `scan_import`. Excel n'ayant pas de fuseau, openpyxl rend des datetimes naïfs : les créneaux entrent déjà dans la bonne convention.

⚠️ **`parametrages.data.globalHoraires.race` est la seule source stockée en UTC, avec un `Z` final.** Elle ne parle pas la même langue que le reste :

| Source | Exemple (24H MOTOS 2026) | Fuseau |
|---|---|---|
| `historique_controle.race` | `2026-04-18T15:00:00` | naïf Paris |
| `parametrages.data.race` | `2026-04-18T15:00:00` | naïf Paris |
| `parametrages.data.globalHoraires.race` | `2026-04-18T13:00:00.000Z` | **UTC** |

`resolve_race` faisait un `str(raw)` sans conversion. Pour un couple déjà en base, le niveau 1 (`historique_controle`) répondait en premier et masquait le problème ; mais **la première édition importée d'un événement** — celle qui n'a pas encore de `historique_controle` — recevait la valeur UTC telle quelle : 2 h de décalage en été, 1 h en hiver, et un format avec `Z` inattendu en aval.

`to_naive_paris_iso()` normalise désormais tout ce qui entre, y compris la date saisie à la main dans la modale. Une valeur déjà naïve est renvoyée **inchangée** — elle est par convention en heure de Paris, on ne lui applique aucune conversion. Vérifié idempotent sur les 30 valeurs `race` en base.

Contrôle rapide : la course des 24H MOTOS tombe toujours **samedi 15h** — 2024, 2025 et 2026 renvoient bien `15:00:00`.

Les lecteurs (`pcorg_summary._parse_race_dt`, `_parse_iso_dt`) savaient déjà gérer les deux formes (naïf → Paris, `Z` → UTC). Le défaut était côté écriture, pas lecture.

### Rattachement aux entités cartographiques

La clé durable est **`properties._id_feature`** (chaîne hexadécimale de 24 caractères). **Il n'existe pas de `id_feature`.** Présent sur 100 % des features de `portes`, `hospitalites`, `terrains`, `tribunes`.

Résolveur à 3 niveaux, dans l'ordre :

1. **Corrections manuelles** persistées dans `scan_feature_overrides`
2. **Récolte** des documents `historique_controle{type:portes}` existants — 39 noms curatés, zéro ambiguïté, source la plus fiable car elle connaît les libellés historiques
3. **Rapprochement normalisé** contre le GeoJSON (casse, accents, ponctuation ; strip des préfixes `AA `/`P `/`PARKING ` côté zones ; `ANCIEN 2025`/`NUMERO 2026` pour les tribunes)

`canonical_porte()` replie en plus les variantes orthographiques des deux côtés : `PORTAIL`→`PORTE`, `VEHICULES`→`VEHICULE`, `PIETONS`→`PIETON`. C'est ce qui rattache `PORTAIL HOUX 5` à `PORTE HOUX 5`.

Un rapprochement **multi-candidats est laissé non résolu** (`PORTE CIK` existe deux fois dans le GeoJSON) : l'UI propose alors les candidats et des suggestions par score de Jaccard.

Sur 24H MOTOS 2024 : 14/19 unités résolues automatiquement, 17/19 après mapping manuel. `PORTE NORD CLUB` et `CONCENTRATION` n'ont aucune entité — ce sont des services, pas des lieux, et ils sont désormais marqués comme tels (voir ci-dessous).

**Collection `(aucune)` — l'unité n'est pas un lieu.** `feature_source` distingue deux situations que le code confondait :

| `feature_source` | Sens |
|---|---|
| `aucun` | rattachement **à faire** — signalé, proposé à chaque import |
| `sans_lieu` | **décision** : guichet, service, renfort mobile. La question est tranchée |

Sans cette distinction, `HELPDESK`, `LITIGE`, `UAM`, `SERI`, `PUNISHER`, `CONCENTRATION` remontaient « à localiser » à chaque import, alors qu'il n'y avait rien à localiser. Une unité `sans_lieu` ne reçoit ni candidats ni suggestions, sort du compteur « à localiser » et du filtre correspondant, et son liseré est neutre (bleu-gris) — surtout pas l'ambre du « à traiter ».

Choisir `(aucune)` **efface le rattachement mémorisé** (`_id_feature: None` dans l'override), sinon il reviendrait au prochain import. Le choix est mémorisé dans les deux sens : repasser une unité de « sans lieu » à « à localiser » survit aussi.

**La modale expose les 19 unités, pas seulement les non résolues.** Une résolution automatique peut se tromper, et la catégorie n'est qu'une proposition dans tous les cas. Un liseré à gauche de chaque ligne donne l'état — gris `proposé automatiquement`, vert `choix manuel`, ambre `non localisée` — et une case « N'afficher que les non localisées » réduit la liste sans rien perdre des choix déjà faits.

### Catégorie d'unité

`UNIT_CATEGORIES` dans `scan_import.py` : `porte`, `tribune`, `aire_accueil`, `parking`, `paddock`, `hospitalite`, `autre`. Proposée par `guess_category()`, **modifiable à l'import**, stockée sur chaque unité `complet` (`category` + `category_source`).

Ce n'est **pas** le rattachement géographique, et les deux sont indépendants : une unité peut être localisée sans qu'on sache la classer, et l'inverse. La catégorie pilote trois choses dans le rapport :

1. le regroupement de la liste latérale (`groupZones`)
2. l'encadré « Vue par catégorie » du tableau de bord
3. **la capacité retenue** — 650 personnes/h pour `tribune`, `paddock`, `hospitalite` ; 250 véhicules/h pour le reste — donc l'effectif recommandé

⚠️ **Le nom du champ côté rapport est `zone_category`, pas `category`** : `category` est déjà pris dans le contrat du gabarit et vaut `'zone'` ou `'porte'`.

Sans catégorie explicite (rapports générés avant, ou chemin de repli `parking_scans`), `guessCategoryFromName()` retombe sur les conventions de nommage 24H AUTOS (`TRIBUNE `, `AA `, `P `). C'est ce repli qui laissait `BEAUSEJOUR`, `KARTING SUD` et `PARKING OUEST` hors de toute catégorie, avec la capacité véhicule par défaut. Réimporter le classeur fixe la catégorie une fois pour toutes.

`save_overrides` mémorise **une catégorie seule**, sans `_id_feature` : classer une zone ne suppose pas de savoir où elle se trouve. `resolve_features` **fusionne** le choix mémorisé et celui de l'UI plutôt que de remplacer, sinon choisir une catégorie effacerait un rattachement déjà connu.

### Édition du mapping après import

Bouton dédié dans le bandeau (`edit_location_alt`), à côté de « Régénérer ». Corrige entité, catégorie et exclusion **sans reprendre le classeur** : `scan_mapping.py` reconstruit les trois documents depuis `complet`, qui porte déjà les séries 15 min de chaque unité.

Équivalence vérifiée chiffre par chiffre sur 24H MOTOS 2024 avant de brancher quoi que ce soit :

- `frequentation` recalculé depuis `complet` : 151 enregistrements, **0 différent**, cumul final 131 975 / 107 259 identique
- `portes` reconstruit : 13 portes, mêmes noms, mêmes `doors_id`, **280 516 scans** de part et d'autre

L'ancien document est archivé (`archived_reason: 'edition_mapping'`), donc une correction reste annulable. La table est la même que celle de l'import — `renderMapRows` prend un contexte (`importCtx` / `mappingCtx`) qui porte la cible DOM et le stockage des choix.

⚠️ **Ne fonctionne que pour les couples ayant un document `complet`.** 24H AUTOS 2025 tombe encore sur l'ancienne chaîne (`parking_scans`) : la route répond **404 `complet_absent`**. Il faut l'importer une fois.

### Unités ignorées

Une case « Ignorer » par ligne écarte l'unité : elle ne figure dans **aucun des trois documents** ni dans le rapport.

⚠️ **L'unité reste dans `complet` avec son drapeau `ignored`**, ses séries conservées — elle n'est retirée que des documents dérivés et du rapport (`build_payload_from_complet` la saute). C'est ce qui rend l'exclusion **réversible** depuis l'éditeur de mapping, sans reprendre le classeur. La supprimer aurait perdu la donnée. Utile pour les guichets et services qui ne sont pas des points de passage (`HELPDESK`, `LITIGE`, `UAM`, `SERI`, `PUNISHER`).

⚠️ **Écarter une porte modifie la série de l'enceinte générale**, donc la référence N-1 de toutes les comparaisons du cockpit. Ce n'est jamais anodin. La modale chiffre l'effet **en direct** pendant la saisie (« N unité(s) ignorée(s) — X scans exclus, dont Y portes : Z entrées retirées de la série de l'enceinte »), le redit après l'écriture, et le document `frequentation` garde la trace dans `ignored_doors`.

Testé sur 24H MOTOS 2024 : ignorer `PORTE MUSEE` fait passer le cumul d'entrées de 131 975 à 127 692 (−4 283), `complet` de 19 à 17 unités et `portes` de 13 à 11.

L'état est mémorisé **dans les deux sens** (`ignored: true` comme `false`) : réactiver une unité écartée doit survivre au prochain import, sinon elle disparaîtrait à nouveau sans que personne ne comprenne pourquoi.

⚠️ `build_frequentation_doc` ne recevait pas `resolved` — il a fallu le lui passer pour qu'il connaisse les exclusions. Les deux autres constructeurs l'avaient déjà.

### Archivage

Toute réécriture **archive d'abord** l'ancien document dans `historique_controle_archive` (copie intégrale + `archived_at`, `archived_by`, `archived_reason`, `original_id`), puis remplace. Plusieurs générations coexistent, rien n'est jamais perdu.

⚠️ **Réimporter dégrade potentiellement une référence N-1.** Le `frequentation` de 24H MOTOS 2024 issu de la collecte temps réel totalisait 166 328 entrées ; le xlsx en donne 131 975 (−20,7 %). Ces deux sources ne couvrent pas le même périmètre de portes, et ce document sert de comparaison N-1 à `pcorg_summary._find_hist_freq` et `app.py:2067`. La modale d'import affiche l'écart en rouge au-delà de 10 %.

### Routes

Toutes sur `scan_report_bp`, donc **admin-only** via `before_request` → `_check_admin()` (bypass `CODING=true`), et **CSRF actif** (ce blueprint n'est pas exempté).

| Route | Rôle |
|-------|------|
| `GET /scan-report` | Page + iframe |
| `GET /scan-report/static` | Sert le HTML généré |
| `GET /scan-report/available` | Couples (event, year) disponibles, alimente la sidebar |
| `GET /scan-report/template.xlsx` | Modèle Excel |
| `GET /scan-report/features?collection=` | Inventaire d'une collection géo (mapping manuel) |
| `GET /scan-report/mapping` | Mapping courant d'un couple, depuis `complet` |
| `POST /scan-report/mapping` | Applique des corrections et reconstruit les trois documents |
| `POST /scan-report/import/analyze` | multipart xlsx → aperçu + mapping, **sans rien écrire** |
| `POST /scan-report/import/commit` | Écrit les 3 documents, archive l'existant |
| `DELETE /scan-report/import/<token>` | Abandon, purge le fichier en attente |
| `POST /scan-report/generate` | Régénère le rapport (job asynchrone) |
| `GET /scan-report/generate/status?job=` | État d'un job (génération **et** analyse) |
| `POST /scan-report/analysis/generate` | Analyse rédigée (`dry_run: true` = prompt sans appel API) |
| `GET /scan-report/analysis/list` / `/<id>` | Historique et détail |

Erreurs au format `{"ok": false, "error": "<code>"}` (convention `field.py:594`). **Ne pas utiliser `abort(404)`** : le handler 404 global (`app.py:700`) redirige vers `/`, ce qui casserait un appel XHR.

### Import en deux temps

`analyze` parse et garde le classeur en mémoire (`_STAGING`, TTL 30 min, 3 entrées max) ; `commit` rejoue avec le mapping corrigé. Le fichier source est conservé sous `uploads/scan_imports/<token>.xlsx` pour la traçabilité, et balayé sur la **date du fichier** — ce nettoyage survit donc à un redémarrage, contrairement au registre mémoire.

⚠️ `_STAGING` et le registre de jobs supposent **un seul process**. Vrai sous waitress (même hypothèse que `analyse_ops.py:49`). Avec gunicorn multi-workers, il faudrait basculer le staging dans une collection Mongo à index TTL.

### Régénération enchaînée

Le rapport est un **fichier figé** : sans régénération il montre encore l'état d'avant. `import/commit` et `POST /scan-report/mapping` enchaînent donc la génération eux-mêmes (`start_generate_job`, extrait de la route `/generate`) et renvoient `regen_job` ; l'UI suit l'avancement dans le même panneau.

Ce n'est pas la génération qui coûtait du temps — 219 à 415 ms — mais **l'oubli de régénérer**. Mesuré à 2 s de bout en bout après une correction de mapping.

L'analyse rédigée reste active : son empreinte SHA-256 ne bouge que si les données qui l'alimentent ont changé. Corriger une catégorie ne touche pas la fréquentation → aucun appel au modèle. Écarter une porte la change → un appel, justifié.

### Génération du rapport

**`generate_parking_report.py` n'a subi que des modifications chirurgicales** : `main(event, year, output, db, progress_cb)`, extraction de `render_html()`, et `raise SystemExit` → `ReportGenerationError`. La constante `HTML_TEMPLATE` (~2 140 lignes, 90 % du fichier) et toutes les fonctions d'analyse sont **intactes**.

`scan_report_build.py` fabrique des pseudo-documents au contrat attendu (`{zone, total_entree, total_sortie, intervals:[{ts, entree, sortie}]}`) et appelle les `serialize_zone` / `serialize_porte` existants. Repli automatique sur `parking_scans`/`porte_scans` si aucun `complet` n'existe — c'est ce qui garde 24H AUTOS 2025 reproductible à l'identique.

Sortie : `reports/parking_report_<slug>_<year>.html`, écriture atomique (tmp + `os.replace`). Le dossier fait foi (`_list_reports()`), un rapport frais apparaît sans redémarrage. `LEGACY_REPORTS` ne sert plus qu'au repli sur `parking_report.html` à la racine.

Les unités **sans aucun flux dirigé sont écartées du rapport** (elles ne produiraient qu'un onglet vide) ; la modale les nomme.

### Effectifs

**Entièrement dérivés, aucune saisie.** Calculés à chaque génération du rapport par `scan_staffing.attach_to_payload`, pour les deux chemins de génération. Un rapport régénéré reflète donc toujours le dernier planning en base.

La chaîne de rattachement existe déjà et ne demande aucun arbitrage :

```
unité de scan → _id_feature → feature géo → post_numbers → shiftcode → calendrier_<année>_<événement>
```

Le calendrier porte `accueil_surete` (`A` / `S`) et `donnees_presences`, une liste de journées découpées en créneaux de 30 min avec `nombre_personnes`. **Accueil et sécurité se calculent exactement pareil** : la seule différence est la valeur de `accueil_surete`. Chaque bloc produit `count_op`, `agents_h_total`, `peak_simu`, `peak_simu_ts` et `hourly`.

- un créneau vaut 30 min, d'où agents-h = `somme(nombre_personnes) × 0,5`
- la courbe horaire retient le **maximum** des deux demi-heures, pas leur somme : c'est un effectif présent, pas un volume
- `post_config` donne le détail par poste (`access_control`, `palpation`, `placier`, `controle_tripode`)

Les unités du document `complet` portent déjà leur `_id_feature`. Celles issues de l'ancienne chaîne (`parking_scans`) n'en ont pas : `resolve_units_for_names` rejoue le résolveur de l'import, qui connaît les variantes orthographiques et les corrections manuelles.

⚠️ **`attach_to_payload` est seule maîtresse du champ `staffing`** : elle l'efface d'abord sur toutes les unités. Sans ça, un reste de l'ancienne chaîne à validation manuelle survivrait avec ses compteurs à zéro, qui se lisent comme « personne n'était en poste ».

⚠️ **Les `post_numbers` d'une feature ne sont pas filtrés par édition** — c'est une liste unique par lieu. Le filtrage se fait à la jointure : un poste absent du calendrier de l'année ne compte pas. C'est ce qui rend la liste réutilisable d'une édition à l'autre. Le rapport affiche `posts_matched / posts_total` quand les deux diffèrent.

⚠️ **Aucun poste de sécurité n'est rattaché à une feature** (24H AUTOS 2025 : 119 `post_numbers` résolus, 119 en `A`, 0 en `S`). Les 255 postes `S` du calendrier sont découpés sur un autre axe (`zone` : « Portes », « Paddock », « Extérieur Bugatti »… ; `secteur` : « Ouest », « Houx »…). Le code est prêt — la sécurité apparaîtra dès que ces postes seront ajoutés aux `post_numbers` côté carto. En attendant le rapport écrit « aucun poste sécurité rattaché à ce lieu », jamais un zéro muet.

⚠️ **Deux formats de calendrier coexistent.** Depuis 2025 : `shiftcode` + `donnees_presences`. En 2024 (`calendrier_2024_24hautos`, 2 826 docs) : colonnes Excel brutes (`'10h - 10h30'`, `'ACCUEIL / SÛRETE'`, `'N°'`), sans `shiftcode`. `load_calendar` lève `StaffingSourcesMissing` plutôt que de rendre des effectifs vides. Et **24H MOTOS 2024 n'a aucun calendrier** — ses effectifs sont donc absents, à juste titre.

Un échec du calcul ne perd jamais le rapport : il est journalisé et `info['staffing']` porte l'erreur.

### Aide UAM et renforts PDA

Calculés **automatiquement à l'import** (logique portée depuis `import_uam_help.py`), car déductibles du seul classeur :

- `uam_help` : un boîtier de la ligne `UAM` a scanné sur cette porte → du renfort mobile y est passé
- `pda_renfort` : sur une porte à tripodes, un PDA non-UAM a servi → **débordement des tourniquets**, signal d'exploitation fort

Nécessite le détail par boîtier, que l'agrégation par unité efface : le parseur conserve `device_hours` pour les seules portes d'enceinte.

Sans ligne `UAM` dans le fichier (cas de 24H MOTOS 2024), `uam_help` est vide partout — ce n'est pas une anomalie.

### Analyse rédigée (Claude)

`scan_analysis.py` réutilise **`pcorg_summary.call_claude`** plutôt que le SDK `anthropic` : cette fonction porte déjà le retry exponentiel (429/503/529), le retry sur troncature, le prompt caching et la télémétrie d'usage.

Modèle par défaut **`claude-sonnet-5-5`** (sorti le 28/09/2026, 2 $/10 $, dans `ALLOWED_MODELS` et `MODEL_PRICING_USD_PER_MTOK` de `pcorg_summary.py`). Des filtres de sécurité peuvent décliner une requête (`stop_reason: "refusal"`) : `_claude_stream_request` lève alors `ClaudeError("claude_refusal")` au lieu d'enregistrer un rapport vide.

Le prompt ne contient **que des KPI agrégés** (~3 600 tokens), jamais les créneaux bruts. System prompt imposant un JSON strict à 7 clés : `synthese`, `pics_et_saturation`, `portes_critiques`, `zones_critiques`, `comparaison_n1`, `anomalies`, `recommandations`.

Persistance dans `scan_analyses`, historisée (jamais écrasée) pour pouvoir comparer deux analyses.

⚠️ **Le prompt avertit explicitement le modèle sur la provenance des données.** Chaque jeu porte un champ `source` (`scan_import` vs `collecte_temps_reel`) et le system prompt impose de présenter tout écart entre éditions de sources différentes comme potentiellement lié au changement de mesure, jamais comme une variation de fréquentation avérée. Sans cela, le modèle conclurait à une baisse de 24 % entre 2023 et 2024 qui n'est qu'un artefact.

Si `ANTHROPIC_API_KEY` est vide → **503 `cle_api_absente`**. Le mode `dry_run` permet d'itérer sur le prompt sans consommer de tokens.

### Vue Fréquentation

Troisième onglet du rapport, à côté de **Zones** et **Portes** : un tableau de bord de la fréquentation de l'**enceinte générale**, jour par jour, comparé aux deux éditions précédentes et croisé avec la météo.

Module de données : `scan_frequentation.py` (fonctions pures, `db` en argument). Le bloc est injecté dans le payload par `scan_report_build._build_frequentation()` sous `DATA.frequentation`, et rendu par `renderFrequentation()` dans `HTML_TEMPLATE`. La clé est lue défensivement (`DATA.frequentation || null`) : les rapports générés avant cette vue continuent de fonctionner, l'onglet affiche un message explicite.

#### Alignement au jour de course

Les éditions ne sont **jamais** comparées par date calendaire mais par **décalage au jour de course** (`J-5 … J+1`), résolu via `pcorg_summary._load_race_dt` (4 niveaux de repli — `race` manque sur tous les documents 2025). L'axe des abscisses est un `slot = offset * 24 + heure`, partagé par la courbe maîtresse et les deux bandeaux météo.

C'est ce qui rend l'alignement gratuit : chaque édition d'un même événement produit le même squelette d'offsets (24H MOTOS `J-5(16h), J-4…J(24h), J+1(18h)` = 154 enregistrements tous les ans ; 24H AUTOS = 176).

⚠️ **Le champ `race` ne désigne pas la même chose selon le millésime.** Jusqu'en 2024 il porte le **départ** (24H AUTOS 2024 : samedi 16h), en 2025 il porte l'**arrivée** (dimanche 14h). Aligner tel quel compare le samedi d'une édition au dimanche d'une autre — un décalage d'un jour entier sur toute la vue, invisible parce que les courbes restent plausibles.

`_normalize_race_dates()` recale les éditions sur le **jour de semaine dominant** parmi celles chargées. C'est le seul invariant qui ne dépende pas du format de course : un événement annuel revient chaque année le même jour de semaine. En cas d'égalité, l'édition la plus ancienne fait référence (elle porte le champ d'origine, celui du départ). L'heuristique laisse GPF sur le dimanche et 24H AUTOS / MOTOS / CAMIONS / SBK / LMC sur le samedi.

**`pcorg_summary._load_race_dt` n'est pas corrigé** : il sert aux résumés quotidiens en production, la normalisation reste locale à cette vue.

#### Granularité : 15 min, pas l'heure

**Le pic de présents est LA valeur de référence, et un échantillonnage à l'heure pile le manque.** La vue lisait le document `frequentation`, qui est horaire, alors que le KPI du tableau de bord lit du 15 min — d'où deux chiffres différents pour la même chose :

| | horaire (avant) | 15 min (après) |
|---|---|---|
| 24H AUTOS 2025 | 138 600 à 16:00 | **142 622 à 16:15** (+4 022, 2,9 %) |
| 24H MOTOS 2024 | 26 431 à 13:00 | **26 573 à 14:15** (+142) |

`enclosure_series()` prend la source la plus fine disponible, dans cet ordre : `complet.data_15min` (agrégé sur les portes non ignorées) → `porte_scans.intervals` (ancienne chaîne, 15 min aussi) → `frequentation.data` (horaire, dernier recours). La granularité retenue est exposée dans `edition.granularity`.

⚠️ **`porte_scans` est indexée sur le SLUG** (`24h_du_mans`), pas sur le nom cockpit. Sans l'alias, la requête ne remonte rien et on retombe silencieusement sur l'horaire.

**Un slot vaut un quart d'heure** : `slot = offset * 96 + heure * 4 + minute // 15`. Une édition horaire tombe sur les multiples de 4, ce qui permet de superposer les deux granularités sur le même axe.

⚠️ **`spanGaps` reste désactivé** — il masquerait les vraies coupures de mesure. Les éditions horaires seraient donc réduites à des points isolés : `bridgeHourlyGaps()` comble **uniquement** les intervalles d'exactement une heure. Une heure manquante (8 slots) reste un trou, comme il se doit.

⚠️ Le solde peut être **légèrement négatif** en début de période (une sortie scannée avant toute entrée : −6 sur 24H AUTOS 2025). L'axe des présences est donc planché à zéro via `freqLineOptions(titre, {zeroFloor: true})` — surtout pas globalement, la température peut vraiment descendre sous zéro.

#### Le pic de présents, pas les entrées

Le **nombre de portes en service a changé d'une édition à l'autre** (24H AUTOS : 21 → 22 → 26 → 29). Le total d'entrées 2022 → 2023 bondit de +68 % : c'est la mesure, pas la foule.

Conséquence appliquée partout : **le pic de présents est le KPI principal** (il ne dépend quasiment pas des portes ouvertes en marge), les entrées sont secondaires. Quand `entries_comparable` est faux, la vue affiche un encadré nommant le nombre de portes par édition, et le prompt Claude interdit de commenter les écarts d'entrées.

#### Météo

Collection **`donnees_meteo`** (13 000+ documents, 1990 → 2026, un par jour calendaire). ⚠️ `historique_meteo` est une *route Flask*, pas une collection.

Clés disponibles, et rien d'autre : `Date`, `Température max (°C)`, `Température min (°C)`, `Précipitations (mm)`, `Ensoleillement (h)`. **Ni vent ni conditions** (le vent n'existe que dans `meteo_previsions`, qui sont des prévisions et ne remontent qu'à octobre 2024).

Deux pièges traités par `load_weather` : les clés sont **accentuées** avec repli non accentué et `0` est une valeur légitime (sentinelle `_MISSING`, jamais `.get(k) or default`) ; quelques jours 2025/2026 portent un **`NaN` BSON réel** qui casse `jsonify` (`_clean_number` le replie sur `None`).

#### Règles de visualisation

- **Jamais de double axe.** Fréquentation et température sur deux échelles inventeraient une corrélation. La météo est dans des **bandeaux alignés sous la courbe**, partageant l'axe temporel.
- Palette validée au script (`bun scripts/validate_palette.js … --mode dark --surface "#0f1620"`) : `#3987e5` (édition analysée), `#008300` (N-1), `#d55181` (N-2). Pire écart daltonisme ΔE 13,0 pour un seuil de 8. **La palette historique du rapport échoue** (`#4ade80` ↔ `#f87171`, ΔE 7,9 en deutéranopie) — hors périmètre, non corrigée.
- L'année courante porte l'emphase par l'**épaisseur** (2,6px + aire à 10 %), pas par la couleur. Légende maison sous le titre : l'identité ne repose jamais sur la seule couleur.
- La température est rendue en **marches** (`stepped: 'middle'`) : la mesure est journalière, une courbe lissée inventerait une variation intra-journalière.

#### Perte de contrôle d'accès

`access_control()` détecte les moments où l'enceinte cesse d'être comptée. Deux constats **distincts**, à ne pas confondre :

- **`final_present`** — à la dernière mesure, N personnes sont encore comptées à l'intérieur. Leurs sorties n'ont jamais été enregistrées. C'est structurel : 70 à 94 % du pic selon les éditions (24H AUTOS 2025 : 107 089, soit 77 % du pic).
- **`events`** — plages où la présence reste au-dessus de 25 % du pic alors que les scans tombent sous 20 % de l'attendu. `controle_non_tenu` quand les portes scannent encore un peu (l'évacuation après l'arrivée, portes ouvertes en grand) ; `mesure_absente` quand aucune donnée n'existe (24H AUTOS 2025 : jeudi 12/06 de 14h à 23h, 0 scan pour 74 024 présents).

⚠️ **Le seuil est calibré par heure du jour, pas sur une médiane globale.** À 3 h du matin l'absence de scan est normale — les spectateurs dorment sur place. Une médiane globale ferait remonter toutes les nuits comme des pertes de contrôle.

⚠️ **La série de présence s'arrête souvent avant celle des portes.** La dernière valeur connue est reportée, sinon la plage la plus intéressante — celle d'après l'arrivée — serait perdue.

Le volume de scans vient de `historique_controle{type:portes}`, pas de `frequentation` : c'est la seule source qui couvre la période d'évacuation.

#### Comparatif des unités entre éditions

`compare_units()` répond à « quelles portes étaient ouvertes cette année et pas l'an dernier » : communes, apparues, disparues.

⚠️ **Seules les portes sont comparables.** `historique_controle{type:portes}` est le seul inventaire par édition (276 unités sur 22 éditions) et il ne contient que des portes — vérifié, aucune hospitalité, tribune ni terrain. Les zones n'existent que pour l'édition courante (`parking_scans` 2025, `complet` 24H MOTOS 2024). La vue le dit explicitement plutôt que de laisser croire à un périmètre complet.

Les unités sans `doors_id` sont classées `sans_lieu` : ce sont des services mobiles (UAM, HELPDESK, LITIGE, SERI, PUNISHER), pas des lieux de passage.

⚠️ **La comparaison porte sur `_id_feature`, jamais sur le nom.** Les libellés changent d'une édition à l'autre — `PORTE HOUX` → `PORTE HOUX 5`, `PASSERELLE ANNEXE` → `PORTE ANNEXE`, `PORTE KARTING PIETON` → `…PIETONS` — pour le même `_id_feature`. Comparer les noms faisait lire 4 suppressions et 4 créations là où il n'y avait que des renommages : 17 portes communes annoncées vs 2023 au lieu de 21. Les renommages sont désormais listés à part (`renamed`), ni comme apparition ni comme disparition. Le nom ne sert de clé que pour les unités `sans_lieu`, qui n'ont pas de feature.

#### Sortie de la vue Fréquentation

`body.cat-freq` masque la recherche, la liste des unités, le sélecteur et le bouton Pics — la vue ne représente aucune liste d'unités. En sortir sans défaire cet état laissait **l'onglet allumé et la navigation escamotée** : le rapport paraissait bloqué sur Fréquentation.

`exitFrequentation()` restaure la catégorie précédente (mémorisée dans `lastUnitCategory` à l'entrée) et est appelée par `showHome`, `showPeaksOverview`, `showZoneDay` et `selectZone` — tous les chemins de sortie.

#### Jours de semaine sur l'axe

L'axe porte deux lignes : le décalage au jour de course (`J-2`) **et** le jour de semaine (`jeu.`). La course tombant chaque année le même jour, un offset désigne toujours le même jour — et c'est en jours de semaine que raisonne l'exploitation. `FREQ_RACE_DATE` est posé au rendu depuis l'édition analysée ; `offsetWeekday()` en dérive le nom. L'infobulle et le prompt Claude reprennent la même convention.

#### Jours non mesurés

Les jours à zéro en début de période (24H MOTOS 2023 J-5, LMC 2022 J-4) sont des **capteurs pas encore actifs**, pas une fréquentation nulle. Portés par `measured: false`, affichés `--` avec la mention « aucune mesure ce jour », exclus des comparaisons et signalés comme tels au modèle.

#### Éditions exclues

`EXCLUDED_EDITIONS = {('GPE', 2022), ('GPE', 2023)}` : GPE 2023 a une date de course fausse (2023-09-09 pour des données d'octobre — l'alignement serait décalé de 28 jours) et GPE 2022 a un cumul d'entrées qui finit à 0. `SBK` et `SUPERBIKE` 2024 sont les mêmes 80 enregistrements sous deux noms, dédoublonnés via `event_aliases`.

#### Analyse rédigée embarquée

Le rapport est un **fichier HTML autonome, zéro appel réseau**. L'analyse est donc générée **à la génération du rapport**, jamais à l'ouverture — un appel par régénération, jamais un par lecture.

`scan_analysis.generate_frequentation_analysis()` calcule une **empreinte SHA-256 des données envoyées au modèle** et réutilise l'analyse déjà en base si elle est identique. Une régénération pour une correction d'affichage ne consomme donc aucun token — c'est la seule économie qui compte vraiment, celle de l'appel qu'on ne fait pas. `force=True` la contourne.

Le prompt ne contient **que les agrégats journaliers** (7 jours × 3 éditions + météo + insights, ~3 200 tokens) : jamais la courbe horaire, qui coûterait dix fois le prompt entier sans rien apporter. Contrat JSON strict à 6 clés : `synthese`, `dynamique_journaliere`, `controle_acces`, `comparaison_editions`, `effet_meteo`, `recommandations`.

Sans `ANTHROPIC_API_KEY`, le rapport se génère **sans la section** (log en warning, `info.frequentation_analysis == 'absente'`) — jamais d'échec de génération. `POST /scan-report/generate` accepte `{"analysis": false}` pour couper l'analyse franchement, et la modale de régénération expose une case à cocher pour ça.

⚠️ **L'appel au modèle est la seule étape lente de la génération.** Mesuré sur ce poste (sans clé API donc sans appel) : 219 ms pour 24H MOTOS 2024, 415 ms pour 24H AUTOS 2025, rendu HTML de 1,3 Mo compris. Avec la clé, l'appel `claude-sonnet-5` ajoute 30 à 90 s, doublés en cas de retry sur troncature, plus l'exponential backoff sur 429/503/529. Une régénération qui « prend des plombes » attend le modèle, rien d'autre. Le libellé de progression le nomme explicitement et un compteur de secondes tourne, pour ne pas lire l'attente comme un blocage.

⚠️ **`pcorg_summary.call_claude` filtre les sections sur `section_keys`, par défaut les neuf clés du résumé pcorg.** Tout appelant qui impose un autre contrat JSON **doit** passer `section_keys`, sinon ses sections sont silencieusement remplacées par des sections pcorg vides. C'était le cas de `generate_scan_analysis` (5 sections sur 7 perdues) avant que le paramètre n'existe.

### Collections créées

| Collection | Contenu |
|------------|---------|
| `historique_controle_archive` | Générations remplacées, append-only, pas de TTL |
| `scan_feature_overrides` | Choix manuels par nom de scan : `_id_feature`, `category`, `ignored` et/ou `no_location`. Index unique `(scan_name, kind, event, year)` |
| `scan_analyses` | Analyses Claude historisées. `kind` vaut `scans` ou `frequentation` ; les documents antérieurs au champ sont des analyses de scans. Les analyses de fréquentation portent une `fingerprint` (réutilisation sans appel API) |

### Pièges

- **`year` doit être un `int`.** Un `str` créerait un doublon sous l'index unique au lieu de mettre à jour.
- **`event` est le nom cockpit** (`24H MOTOS`), jamais le slug `24h_du_mans` de l'ancienne chaîne. `EVENT_ALIASES` ne sert plus qu'au repli sur les rapports historiques.
- **Datetimes naïfs Paris.** Écrire de l'UTC décalerait silencieusement les données de 2 h en été. Seul `parametrages.data.globalHoraires.race` est en UTC avec un `Z` : tout ce qui entre passe par `scan_import.to_naive_paris_iso()`.
- **`SystemExit` dérive de `BaseException`** : dans un thread de travail, un `except Exception` ne l'attrape pas, le thread meurt en silence et le job reste bloqué. Les workers attrapent `BaseException` et libèrent la cible dans un `finally`.
- **Les noms d'unités viennent du classeur téléversé**, donc d'une source non maîtrisée. Toute interpolation dans du HTML côté JS doit passer par `esc()` — sinon un fichier avec une zone nommée `<img onerror=…>` exécute du script dans une page admin.
- **`reports/` et `uploads/scan_imports/` sont dans `.gitignore`.** Un rapport pèse 0,3 à 1,2 Mo.
- **`openpyxl` est désormais importé dans le process Flask** (et plus seulement par les scripts autonomes) : il est dans `requirements.txt`, avec `numpy` et `pandas` qui manquaient déjà (importés par `analyse_ops.py` au chargement — sans eux l'app ne démarre pas sur un environnement neuf).
- **La météo est dans `donnees_meteo`, pas `historique_meteo`** (qui est une route Flask). Les clés sont accentuées, `0` est légitime, et quelques jours portent un `NaN` BSON réel.
- **Les comparaisons entre éditions ne valent que sur le pic de présents.** Le nombre de portes en service a changé chaque année ; les totaux d'entrées comparés d'une édition à l'autre mesurent le dispositif, pas la foule.
- **Le champ `race` porte le départ jusqu'en 2024 et l'arrivée en 2025.** Toute comparaison pluriannuelle qui l'utilise brut est décalée d'un jour sans que rien ne le signale.
- **Un jour à zéro en début de période n'est pas une fréquentation nulle** mais un capteur pas encore actif. Le confondre ferait lire une chute inexistante.
- **`call_claude` filtre les sections sur `section_keys`** (défaut : les neuf clés pcorg). Un nouveau contrat JSON sans ce paramètre perd toutes ses sections en silence.
- **La catégorie d'une unité est choisie à l'import**, pas devinée du nom. Le repli par préfixe (`TRIBUNE`, `P `, `AA `, conventions 24H AUTOS) ne sert plus qu'aux rapports générés avant cette bascule et au chemin `parking_scans`. Un couple réimporté porte sa catégorie explicite, y compris pour les libellés hors convention (`BEAUSEJOUR`, `KARTING SUD`).
- **Changer la catégorie change l'effectif recommandé**, puisqu'elle détermine la capacité (650 personnes/h vs 250 véhicules/h). Ce n'est pas un réglage d'affichage.

## Présents sur site (widget Contrôle d'accès, TV general-stats, montre)

**Un seul calcul, dans `presents_etat.py`** (sans Flask, comme `meteo_etat.py`) : `presents = current - correction - véhicules présents`. Le consomment `/api/live-controle/counters` (widget accueil), `/api/live-controle/dashboard` (TV `general-stats` et plein écran du widget), et la montre (`watch_state` pour `p`, `watch_peaks` pour `pk`, `watch_pages.build_frequentation` pour `pj`/`n1`).

⚠️ **Les véhicules se cumulent depuis la dernière remise à zéro du compteur, jamais depuis minuit.** Le `current` d'une Area HSH cumule depuis son dernier reset ; retirer seulement les véhicules du jour comptait comme des personnes tous ceux garés la veille (24H CAMIONS 2026 : 741 retirés pour 1 297 sur site). `counter_baseline()` repère le reset par une chute de `entries` dans `data_access` depuis `activation_timestamp` (une lecture fautive isolée est ignorée), à défaut l'activation. Même règle pour enfants et accrédités.

⚠️ **`hsh_transactions_agg.tranche` est en heure de Paris étiquetée UTC** (le champ Handshake `date_utc` porte l'heure locale), alors que `data_access.timestamp` est du vrai UTC. Toute comparaison passe par `to_tranche_label()`. Sans cette conversion, la TV ignorait les 2 dernières heures de mouvements de véhicules.

⚠️ **Le pic d'édition de la montre (`pk`) ne se calcule sur les présents que pendant que le live-contrôle compte l'édition** (les soldes véhicules des éditions closes ne sont plus en base). Le max brut de `current` remontait la valeur fantôme d'avant reset (22 447 le 22/09 pour quelques centaines de présents). Le cache `watch_peaks` mémorise `methode: "presents"` et ne laisse jamais un recalcul brut l'écraser après la fin de l'édition.

**⚠️ **Un pic = le plus haut RELEVÉ (toutes les 3 min), jamais le max de la courbe.** La courbe ne garde que le dernier relevé de chaque quart d'heure : un pic calculé dessus pouvait baisser en cours de quart d'heure (37 500 à 13h09 puis 37 449 à 13h12 le 27/09/2026) et afficher moins que le chiffre « présents » vu juste avant. `pic_presents` et `/dashboard` (`pic_today`, `pic_today_ts`, `days_summary`) balayent les relevés bruts.

Le N-1 vient partout de `presents_etat.historique_n1`** : TV (`/dashboard`), pic N-1 de `/get_affluence` (donc pic projeté), montre, rapport matinal. Série **au quart d'heure** (`scan_frequentation.enclosure_series`, document `complet`), la série horaire ratant le vrai pic (24H CAMIONS 2025 samedi : 51 889 à l'heure contre 52 520 au quart d'heure).

⚠️ **Un créneau 15 min scan_import est étiqueté à sa FIN** (« 20:00 » = passages de 19:45 à 20:00). Mesuré sur 24H MOTOS 2026, seule édition ayant import ET relevés temps réel : corrélation 0,996 avec [HH:MM−15, HH:MM], 0,971 avec [HH:MM, HH:MM+15]. La série horaire somme H:00 à H:45 : son point « 20:00 » est l'état à **20:45**. Lue telle quelle, la courbe N-1 avait 45 min d'avance ; une première correction à +15 min l'avait mise en retard (−2 400 personnes en pleine montée).

**Rapport matinal** (`pcorg_summary.compute_attendance_block`) : `pic_observed` = pic des présents du dashboard tant que le live-contrôle compte l'édition (repli sur l'ancienne chaîne historique → data_access → archive sinon), `pic_prev` et `pic_prev_hour` = série N-1 ci-dessus. Seul `pic_projection` reste calculé à part (méthode « croissance » seule, qui tombe sur la borne basse de la fourchette du dashboard).

## Montre connectée (app Connect IQ, `garmin/cockpit-watch/`)

Une app Garmin (tactix 8 Solar / fēnix 8 Solar 51 mm, sideload uniquement)
affiche au poignet du directeur des opérations adjoint huit pages en cycle
(HAUT/BAS) : tableau de bord, alertes, main courante PC org, trafic, météo,
fréquentation, guidage, timeline ; plus un menu de saut (MENU) et une page
« Pics par édition ».
Authentification par jeton Bearer émis depuis `/watch-admin`. Documentation
complète dans `garmin/cockpit-watch/README.md` — cette section ne couvre que
ce qui touche le code serveur Cockpit.

### `trafic_etat.py` et `meteo_etat.py` — pourquoi ils existent

Ni `traffic.py` ni `meteo.py` ne sont importables par un module de calcul
sans backend Flask : **les deux importent `app`** (pour le blueprint, la
config, les helpers Mongo partagés), et `app.py` déclenche son cycle d'auth
et ses collecteurs au chargement. Le payload de la montre (`watch_api.py` →
`watch_pages.py`) doit pourtant produire le même verdict trafic et la même
consigne météo que les murs (`circulation.html`, `meteo_mur.html`), sans
importer ni `app`, ni relancer ses collecteurs, ni dupliquer les seuils.

La solution retenue : extraire le calcul pur, sans aucun import Flask, dans
deux modules dédiés que `traffic.py`/`meteo.py` **et** `watch_pages.py`
consomment tous les deux :

- **`meteo_etat.py`** — extraction complète : `meteo.mur()` (route qui sert
  le mur) et `watch_pages.build_meteo()` appellent **littéralement la même
  fonction**, `meteo_etat.etat_mur(db, now)`. Zéro risque de divergence par
  construction — ce n'est pas une réimplémentation parallèle, c'est le même
  code exécuté deux fois. Vérifié à la tâche 14 : `build_meteo` ne réinvente
  aucun seuil (rafale, WBGT, orage), il ne fait que choisir la consigne la
  plus grave dans la liste déjà triée par gravité que rend `etat_mur`, et
  réduire `couleur_jour` sur l'échelle 0-3 du mur via
  `meteo_etat.ORDRE_COULEURS`.
- **`trafic_etat.py`** — situation différente : c'est un **port**, pas un
  partage. Le calcul de sévérité par axe et le verdict global du mur vivent
  en JavaScript, inline dans `circulation.html` (`classify()` ligne ~587,
  `computeVerdict()` ligne ~728) — aucun moyen de les faire appeler par le
  backend Python. `trafic_etat.verdict_global()` reproduit fidèlement la
  structure de décision de `computeVerdict()` (accident en zone ou sévérité
  ≥ 4 → CRITIQUE ; == 3 → TENSION ; == 2 → VIGILANCE ; sinon FLUIDE), et
  `parse_route_name`/`classify_congestion` sont *déplacés* depuis
  `traffic.py` (mêmes fonctions, nouvel emplacement — pas une réécriture).

  **La tâche 14 a trouvé et corrigé une divergence réelle sur la sévérité
  par axe qui alimente ce verdict.** `classify_congestion` (Python, utilisé
  par `/trafic/waiting_data_structured` et les autres panneaux du mur) ne
  regarde que le ratio courant/historique (paliers 0,9/1,2/1,6/2,5).
  `classify()` (JS, le panneau « Axes » de `circulation.html`) exige un
  **double verrou** : ratio élevé **ET** perte de temps absolue en secondes
  (paliers 1,35/1,6/2,2/3,0, chacun avec un plancher de retard). Son
  commentaire d'origine dit pourquoi (`circulation.html:594-596`) : *« un
  tronçon court (ex. 20s → 80s) a un gros ratio mais ne coûte qu'une minute :
  ce n'est pas un bouchon »* — sans ce plancher, un tronçon négligeable
  remonte en fausse alerte, précisément ce que le double verrou existe pour
  écarter. `trafic_etat.severite_axe()` porte maintenant ce double verrou à
  l'identique, et `watch_pages.build_trafic()` l'utilise pour recalculer la
  sévérité de chaque terrain (`terrain["severity"] =
  trafic_etat.severite_axe(terrain)`) **avant** de la passer à
  `verdict_global()` — donc pour la sévérité affichée par terrain ET pour
  le verdict global. Vérifié : le cas trouvé à la tâche 14 (axe unique,
  ratio 1,5, retard 500 s) donne désormais sévérité 1 des deux côtés (vd
  FLUIDE, plus VIGILANCE côté montre) ; le cas du tronçon court (15s → 45s,
  ratio ×3, retard 30 s) donne sévérité 0 des deux côtés, alors que
  `classify_congestion` l'aurait classé « bouchon », sévérité 4.

  ⚠️ **`classify_congestion` n'a pas été touchée** — elle continue
  d'alimenter `/trafic/waiting_data_structured`, dont la tâche 1 a prouvé le
  payload identique à l'octet près ; la modifier aurait cassé cette garantie
  et changé l'affichage d'autres panneaux du cockpit. Les deux fonctions
  coexistent désormais : `classify_congestion` pour les panneaux existants,
  `severite_axe` pour la montre.

  **Un second écart, indépendant de la formule, a été trouvé puis corrigé
  dans la même tâche : le PÉRIMÈTRE des axes couverts par le verdict
  global.** `agreger_terrains()` (montre) n'agrège que les noms préfixés
  `##`, `#I`, `#O`, et **exclut les axes `#P` (parkings)** ; le panneau
  « Axes » du mur les inclut (`circulation.html:793`,
  `r.category === "pkg_aa" || r.tag === "P"`, redondant puisque P est déjà
  dans pkg_aa). Un parking `#P` très chargé restait donc **invisible au
  verdict global de la montre jusqu'au niveau CRITIQUE** — un manque
  silencieux, jamais une fausse alerte, exactement la faute qu'un outil de
  supervision ne doit jamais commettre (une page qui paraît calme alors
  qu'elle ne sait pas). Corrigé par `trafic_etat.pire_severite_mur(routes)` :
  reproduit le même ensemble d'axes que le mur (toutes les routes taguées
  `I`/`O`/`neutral`/`P`, chacune classée **individuellement** par
  `severite_axe`, sans agrégation par terrain — comme le fait `classify()`
  côté mur), et alimente désormais `verdict_global()` à la place du maximum
  pris sur les seuls terrains agrégés. `watch_pages.build_trafic()` :

  ```python
  terrains = trafic_etat.agreger_terrains(routes)      # inchange, pour "r"
  for terrain in terrains:
      terrain["severity"] = trafic_etat.severite_axe(terrain)
  pire = trafic_etat.pire_severite_mur(routes)          # pour "vd", pas les terrains
  ```

  **Conséquence assumée** : la montre peut afficher un verdict élevé sans
  qu'aucun terrain de la liste `r` (page Trafic) ne l'explique, quand la
  cause est un parking — la page ne liste que les axes d'entrée/sortie, pas
  les parkings, choix d'affichage délibéré. C'est le bon compromis : un
  verdict visible sans terrain qui l'explique renvoie au moins
  l'utilisateur regarder le mur ou le cockpit ; un verdict qui reste FLUIDE
  ne renvoie nulle part. Vérifié : un axe `#P` à ratio ×6, retard 2500 s —
  invisible dans `agreger_terrains()` (`r == []`) — fait désormais monter
  `vd` à 3 (CRITIQUE), identique des deux côtés (avant la correction : `vd`
  restait à 0, FLUIDE).

  ⚠️ **Un écart résiduel, plus fin, n'a volontairement pas été corrigé —
  et il ne touche que l'AFFICHAGE (`r`), plus le verdict global** : pour
  construire la liste `r` de la page Trafic, `agreger_terrains()` **agrège**
  les axes d'un même `(terrain, direction)` (somme des temps
  courant/historique de tous les tronçons) avant de classer, quand le mur
  classe **chaque route individuelle** puis prend le pire pour son propre
  panneau. Cet écart-là est à double sens, pas un manque silencieux : le
  ratio agrégé ne peut pas dépasser le pire ratio individuel (moins
  alarmiste que le mur), mais le retard agrégé est la somme des retards de
  tous les tronçons et peut dépasser celui de n'importe quel axe pris seul
  (plus alarmiste que le mur) — le résultat dépend des données, dans un sens
  ou dans l'autre. Le verdict global (`vd`), lui, n'est plus concerné : il
  vient maintenant de `pire_severite_mur`, calculé sur les routes non
  agrégées, exactement comme le mur. Corriger la sévérité *affichée* par
  terrain demanderait de faire lire à la montre les routes individuelles
  plutôt que l'agrégat, rien que pour l'affichage — hors périmètre de cette
  tâche, qui portait sur le verdict.

### Le champ `mr` et la règle du bloc absent

Deux conventions transverses aux quatre blocs de pages (`mc`/`tr`/`me`/`st`
dans le payload, cf. `watch_pages.py`) :

- **`mr`** (motif) distingue, en mode `past` (hors événement), un arrêt
  volontaire (`inactif` — le live-contrôle est désactivé côté cockpit, l'état
  normal 350 jours par an) d'un collecteur en panne (`sans_releve` — le
  drapeau dit encore actif mais plus aucun relevé n'arrive). Aucune des deux
  garde seule ne suffit : le drapeau attrape l'arrêt propre en une seconde,
  la fraîcheur attrape le collecteur planté que le drapeau mentirait
  indéfiniment. Les confondre sous un seul « édition terminée » perdrait
  l'information qui dit si une intervention est nécessaire.
- **Un bloc absent (`None`) reste `None` dans le payload**, jamais remplacé
  par un objet à champs vides. Chaque constructeur de `watch_pages.py` ne
  lève jamais — une source injoignable rend `None`, et l'appelant continue de
  servir les autres pages. Côté Monkey C, chaque vue de page (`TraficView`,
  `MeteoView`, etc.) teste explicitement ce `null` et affiche « indisponible »
  plutôt que de tenter un accès qui ferait planter le rendu — sauf
  `TraficView`, qui garde sa structure fixe (tirets) plutôt qu'un message,
  pour ne pas faire sauter la mise en page entre deux relevés.

### Deux pièges vérifiés sur ce lot

- **`monkeyc --build-stats` ne mesure pas ce qui est compilé par espace
  mémoire** (device app / glance / fond). Vérifié par expérience contrôlée à
  la tâche 8 : une fonction morte, jamais appelée, sans aucune annotation
  `(:glance)` ni `(:background)`, gonfle la glance ET le fond du même montant
  que si elle y était réellement exécutée. Cette métrique mesure la taille du
  binaire produit, pas son partitionnement logique. **La garantie qu'un
  module ne s'exécute jamais en glance/fond est structurelle** (absence
  d'annotation `(:glance)`/`(:background)` sur le fichier, et données rangées
  dans une clé `Application.Storage` que ces deux espaces ne lisent jamais),
  pas une lecture de `--build-stats`.
- **Sur un cadran rond, le texte est ancré par le HAUT**, jamais par son
  centre vertical (pas de `TEXT_JUSTIFY_VCENTER` dans ce projet) : un bloc
  posé à `y` occupe `[y, y + hauteur police]`. Un bloc de la moitié haute de
  l'écran est donc contraint par son **sommet** (la corde disponible se
  resserre en montant vers le bord), un bloc de la moitié basse par sa
  **base** (elle se resserre en descendant). Vérifier le mauvais bord donne
  un calcul de largeur disponible qui semble juste et laisse pourtant
  déborder le texte à l'écran réel. Ce piège s'est refermé **quatre fois**
  sur ce projet — toujours en vérifiant le bord qui ne contraint pas le bloc
  concerné.

  ⚠️ **La même règle vaut pour l'argument passé à `largeurUtile`**, et c'est
  là qu'elle a été oubliée le plus longtemps : appeler `largeurUtile(dc, y)`
  mesure la corde à l'ancre du texte, donc **surestime la place** pour tout
  bloc de la moitié basse. Écarts réellement mesurés avant correction : 36 px
  sur la ligne « édition » de `FrequentationView`, 18 px sur la seconde ligne
  de consigne météo — celle sur laquelle `CONSIGNE_MAX` avait justement été
  calibré. La fonction prend désormais la hauteur du bloc
  (`Pages.largeurUtile(dc, y, hauteur)`) et évalue le bord le plus éloigné du
  centre, y compris quand le bloc est à cheval sur celui-ci.

- **L'écran de la cible fait 280 × 280, pas 454 × 454.** Le fenix 8 *Solar*
  porte un écran MIP de 280 px ; c'est le fenix 8 AMOLED qui fait 454. Toutes
  les sondes de mise en page de ce projet ont créé leur tampon en **imposant**
  `454 × 454` au lieu de lire `System.getDeviceSettings()`. Elles mesuraient
  donc une page 1,6 fois trop haute **et un rayon de cadran de 227 au lieu de
  140** : positions verticales et largeurs disponibles étaient fausses
  ensemble. Résultat sur la vraie montre : quatre pages sur six débordaient,
  le pied de page se superposait au contenu, et un itinéraire sur deux de la
  page Trafic était dessiné hors écran.

  Ce défaut a traversé quatorze tâches, deux relectures d'ensemble et une
  centaine de tests, parce que **tout le monde partageait la même hypothèse
  fausse** : les tests de dessin prouvent qu'un rendu ne lève pas, et dessiner
  hors écran ne lève pas. Il a été trouvé par l'utilisateur, à l'œil, sur sa
  montre. **Ne jamais coder une dimension d'écran en dur, nulle part** — pas
  même dans une sonde jetable. `DebordementTest.mc` échoue désormais si un
  bloc sort du disque inscrit ou chevauche le pied, sur la taille lue à
  l'exécution.

### Le jeton est une CONSTANTE DE CODE, pas une Property

⚠️ **`Application.Properties` survit au sideload, comme `Storage`.** Une
valeur écrite un jour dans les réglages **écrase la valeur par défaut
compilée**, et c'est elle qui part sur le réseau. Le binaire peut porter le
bon jeton (`strings` le confirme) pendant que la montre en envoie un autre.

Cas réel : un ancien jeton laissé actif le temps d'une transition, révoqué
cinq heures plus tard. La montre s'est arrêtée net en 401 — et **cinq
reconstructions avec le nouveau jeton n'ont rien changé**, puisqu'elle
n'envoyait pas celui-là.

Le jeton vit donc dans `source/Jeton.mc` (`const VALEUR`), rempli par
`build-avec-jeton.sh` dans une copie temporaire. `Jeton.valeur()` donne la
priorité à la constante et ne retombe sur la Property qu'en repli — ce qui
donne le dernier mot à la construction, jamais à un reliquat de réglage.

Le pied de page affiche l'**empreinte** (4 caractères) du jeton employé sur
les erreurs `401` et `jeton absent` : c'est ce qui aurait tranché en cinq
secondes au lieu de cinq reconstructions.

### `Application.Storage` survit au sideload

⚠️ Réinstaller l'app ne vide **pas** son stockage. Un code d'erreur, un
cache, un compteur mémorisés par une version antérieure survivent à la
réinstallation censée les corriger.

Constaté : « jeton refuse » est resté affiché après **quatre
reconstructions successives**, alors que le jeton compilé était accepté par
le serveur (HTTP 200) et bien présent dans le binaire (`strings`). Le
message ne venait pas du serveur, il venait du stockage — écrit une fois,
effacé seulement après une requête réussie.

`CockpitApp.onStart` appelle donc `Api.oublierErreur()`. **Au démarrage, la
montre ne sait rien de son lien au serveur** ; toute valeur qui prétend le
contraire est fausse. Même principe qu'`Alerting`, qui ne vibre jamais sans
référence antérieure.

Règle générale : toute donnée de Storage qui décrit un ÉTAT COURANT (et non
un historique) doit être remise à zéro au démarrage, ou porter un numéro de
version. Sinon elle finit par mentir après une mise à jour.

### Météo : `consignes` ET `contraintes`, jamais l'une seule

⚠️ `meteo_etat.etat_mur` rend **deux** listes. `consignes` ne se déclenche
qu'aux seuils hauts (rafale ≥ 60 km/h, WBGT danger, orage avéré, pluie
≥ 5 mm) ; `contraintes` porte les quatre décisions permanentes (vent,
chaleur, orage, sol) et descend jusqu'à la **vigilance** — rafale ≥ 40 km/h.

`watch_pages.build_meteo` ne lisait que `consignes` : trou de 40 à 60 km/h
où le mur parlait et la montre se taisait. Constaté le 17/08/2026, rafale
prévue à 44,8 km/h à 17 h — visible widget et mur, absente du poignet.
C'est la contradiction que le commentaire de la fonction interdit : aucun
seuil n'était réinventé, mais la mauvaise liste était lue.

Les quatre contraintes sont **toujours présentes**, la plupart en `normal` :
seules les non-normales sont relayées. Et l'orage en vigilance porte une
consigne **vide** côté mur — un titre sans action est écarté.

### Les alertes de la montre ne filtrent pas sur l'événement

⚠️ **`watch_state.read_active_alerts` ne filtre QUE sur `expiresAt`.**
Filtrer sur event/year — ce que faisait la première version — perdait 100 %
des alertes hors période d'événement.

`alert_engine.build_context` ne résout un événement que si un paramétrage
couvre la date du jour (repli à 7 jours). Sinon les handlers écrivent
`context.get("event", "")` → **chaîne vide** (`alert_engine.py:490`), qu'aucun
couple réel ne matche, ni en mode `pinned` ni en mode `auto`. Or les alertes
trafic, météo et main courante tournent, elles, toute l'année.

Le cockpit ne filtre pas non plus (`app.py:4154`). La sélection est faite
par les **slugs cochés dans `/watch-admin`** (`select_alerts`), qui portent
aussi le niveau de gravité : un filtre, pas deux.

Les SOS terrain échappent au problème — `field.py:3983` écrit event/year
depuis la tablette enrôlée.

### La ligne d'axe suit le bloc « Temps d'accès », pas le mur

⚠️ **Deux références distinctes, et il ne faut pas les confondre.** Le mur
`circulation.html` donne les **seuils de sévérité** et le **verdict global**
(`severite_axe`, `verdict_global`). Le bloc « Temps d'accès » de la page
principale (`static/js/traffic.js`) donne le **format des chiffres** — et
c'est lui la bonne référence pour une liste d'axes.

Concrètement : `4m 20s` et non `24'` (sur des trajets de 33 s à 25 min, une
unité implicite se devine mal), `+45s` **toujours affiché, `+0s` compris**,
et `ENT`/`SOR`/`PKG` plutôt que des chevrons. `Fmt.duree` et `Fmt.retard`
sont des ports exacts de `formatTime` et `formatDelay`.

⚠️ **Masquer le retard nul était le défaut signalé à l'usage** : un axe
fluide (retard connu, nul) et un axe dont on ignore le retard se
ressemblaient. Un sabotage l'a confirmé après coup — le masquer laissait
264 tests au vert.

⚠️ Ce format demande **176 px dans son pire cas** sur une corde utile de
186 : il ne reste rien pour le nom sur une seule ligne. D'où **deux lignes
par axe**, et quatre axes par écran au lieu de six. Le payload transporte
donc des **secondes**, plus des minutes arrondies.

⚠️ **Le tag Waze `#P` designe les AUTOROUTES (A28, A11), pas les
parkings.** Les parkings sont en `##`, sans direction. Le cockpit le prouve
trois fois : onglet « Autoroutes » filtré sur `tag === "P"`, onglet
« Parkings » sur `category === "pkg_aa" && tag !== "P"`, et `tagLabel("P")`
qui rend l'icône `fork_right`. La première version de la page affichait
`PKG` sur les autoroutes et `--` sur les parkings — les deux faux, en sens
inverse. Un axe sans direction rend désormais une colonne **vide**, jamais
un tiret : le tiret signifie « inconnu » partout ailleurs dans cette app.

### Fraîcheur Waze : `latest`, jamais l'historique

Le collecteur externe (`waze_collector.py`, tâche planifiée **toutes les
2 min**) réécrit `{"_id": "latest"}` dans `waze_trafic` et `waze_alerts`, et
dépose un snapshot dans `waze_*_history` toutes les 16-18 min. **Lire
l'historique ferait annoncer un quart d'heure de retard en permanence.**

⚠️ **Le mur masque les pannes de collecteur, la montre non.** `traffic.py`
a un troisième étage : au-delà de `MONGO_MAX_AGE_SECONDS` (300 s), il
appelle l'API Waze en direct. La montre lit uniquement Mongo. Un « maj
42 min » au poignet alors que le mur paraît normal signale donc un
**collecteur arrêté**, pas un défaut d'affichage.

### Indicateur de pagination (`Pages.dessinerPagination`)

Losanges en haut à droite, plein pour la page courante. Partagé par les
deux pages à livret (Trafic, Timeline).

⚠️ **Se tait au-delà de 8 pages** : la corde ne fait que 122 px à cette
ordonnée, et des losanges indistinguables valent moins que pas de losanges
du tout — le compteur du pied prend le relais.

⚠️ **En haut à droite et non centré** : l'en-tête de page occupe déjà toute
la corde à son ordonnée.

### Timeline : ce qui tombe dans les 12 h à venir

8ᵉ page. **Aucun calcul métier n'est réécrit** :
`pcorg_summary.get_upcoming_timetable` existait déjà et porte la
factorisation des ouvertures simultanées — 65 vignettes brutes deviennent
9 lignes sur une journée de course. `watch_timeline.py` ne fait que
compacter sa sortie.

⚠️ **Le délai est calculé au POIGNET, à partir d'un epoch.** Un « dans
42 min » formaté côté serveur serait juste à l'émission et faux trois
minutes plus tard — ce qui est précisément l'âge que peut avoir un relevé.
Rien de formaté ne voyage.

⚠️ **Deux sources, délibérément.** `nx` (la prochaine vignette seule,
~70 octets) voyage dans le payload principal ; la liste complète (~570) vit
sur `/timeline`, requêtée à l'ouverture de la page. Ce n'est pas de la
redondance : le héros s'affiche depuis le cache dès l'ouverture, même hors
de portée du téléphone. La liste ne pouvait de toute façon pas entrer dans
le payload — il reste 182 octets sur le budget de 2 Ko.

⚠️ **`nx` est calculé dans les DEUX modes**, contrairement à `mc` et `st`.
Un sabotage a montré que la garde `mode == "live"` ne coûtait rien (en mode
auto hors événement, `resolve_event` rend déjà `(None, None)`) et retirait
la timeline **précisément quand elle sert le plus** : la veille et le matin,
pendant le montage, avant l'armement du live-contrôle. `/timeline`, lui,
garde les deux gardes en mode auto — sans elles il servirait la timeline
d'une édition terminée.

⚠️ **Le compte de factorisation ne cède jamais à la troncature.** Trouvé à
la sonde : `Ouverture des tribunes nord es (12)` sortait
`…nord es (1` — un chiffre **faux**, qui annonce une tribune là où il y en a
douze. Le nom est le seul élément élastique, comme sur la ligne d'axe de
`TraficView`.

### Guidage : un point GPS poussé du cockpit vers une montre

`/watch-admin` porte une carte : on clique un point, on le nomme, on choisit
une montre, il part. La montre l'affiche (flèche, distance) sur une 7ᵉ page,
et START le pousse dans ses **lieux enregistrés natifs** pour que la
navigation Garmin le reprenne. Documentation complète dans
`garmin/cockpit-watch/README.md` ; trois pièges valent d'être ici.

⚠️ **`/state` sert un payload mis en cache 20 s, IDENTIQUE pour toutes les
montres.** Un point de guidage est adressé à une seule : il est donc ajouté
par `watch_api._avec_guidage` sur une **copie**, après le cache, à partir du
jeton porteur de la requête. L'écrire dans le payload caché ferait fuiter le
point d'une montre vers toutes les autres pendant 20 secondes. C'est le seul
endroit de l'API montre où le payload n'est pas commun, et donc le seul où
une fuite est possible.

⚠️ **Le GPS n'est allumé que pendant que la page Guidage est affichée** —
seul capteur continu de toute l'app. `CockpitView.entrerPage`/`quitterPage`
l'allument et l'éteignent, `onHide` est le filet à la fermeture. Cette
extinction est **invisible à l'écran**, donc invisible à tout test de dessin :
un sabotage a montré que la supprimer laissait 194 tests au vert.

⚠️ **Un texte tronqué tient parfaitement dans l'écran** — aucun contrôle
géométrique ne le signale. La corde au pied de page ne fait que **133,7 px**
sur ce cadran : `boussole indisponible` (164 px) sortait `boussole indispo`,
et `GPS faible . START = enregistrer` (255 px) sortait `GPS faible . STA`.
Trouvé à la sonde, jamais par un test. Tous les libellés du pied sont
désormais mesurés, et cinq tests comparent le texte **réellement dessiné** au
texte entier attendu.

### Piège d'outillage : lancer le simulateur au premier plan

Une commande lancée en arrière-plan depuis une session d'agent **ne réveille
pas cet agent** : il attend indéfiniment une notification qui n'arrivera pas.
Ce piège s'est refermé **trois fois** sur ce lot, pour une vingtaine de
minutes perdues à chaque fois, toujours sur `monkeydo` (le simulateur met une
à deux minutes à rendre ses tests, ce qui donne envie de le mettre en fond).

Lancer `monkeyc` et `monkeydo` **au premier plan** et attendre le résultat
dans la même invocation. Seul `connectiq` — le service graphique — se lance
en arrière-plan, parce qu'il doit rester vivant.

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

## Tablettes Field — synchronisation avec le cockpit

La tablette (`static/js/field.js`) ne reçoit rien en temps réel : elle **poll** `/field/my-fiches` (statut, fiche active) et `/field/inbox` (messages, SOS), et rejoue ses écritures ratées via une **file hors ligne** IndexedDB. Le cockpit poll `/api/active-alerts` (`alert_poller.js`, 5 s) et `/anoloc/live` (15 s). Toutes les règles ci-dessous découlent de ces deux faits.

### Catégorie de la tablette

Chaque tablette porte **une** catégorie de fiche (`field_devices.category`), choisie à l'appairage ou dans la table de `/field-dispatch` (`POST /field/admin/devices/<id>/category`). À défaut, elle hérite du `pco_category` de son groupe de balises (`_device_category`) ; sans l'un ni l'autre, aucune restriction (comportement historique). Effets :

- `/field/my-fiches` ne rend que les fiches de cette catégorie, **plus** le propre SOS de la tablette (`content_category.field_sos`, toujours `PCO.Secours`) et sa fiche active (si le PC Org en change la catégorie après engagement, la tablette ne la perd pas).
- Création de fiche (`/field/create-fiche`, `/field/photo/send` avec fiche) : `403 category_not_allowed` hors catégorie. Une photo simplement envoyée au PC Org garde le choix libre.
- `/anoloc/vehicles-by-category` range chaque tablette sous **sa** catégorie (plus celle du groupe) : le PC Org ne la voit proposée que sur ces fiches.
- SOS (`_sos_recipients`) : diffusé aux catégories `PCO.Securite`/`PCO.Secours`, à la catégorie de l'émetteur et aux tablettes sans catégorie ; jamais aux révoquées.

⚠️ La liste des catégories existe en trois copies à garder alignées : `FIELD_CATEGORIES` (`field.py`), `FICHE_CREATE_CATEGORIES`/`CAM_CATEGORIES` (`field.js`) et `FIELD_CATEGORIES` (`field_admin.js`).

### Dispatch automatique et file du service (`dispatch_auto.py`)

Deux voies d'engagement coexistent. **Directe** : le PC Org (ou un responsable) choisit l'unité, comme avant. **Proposition automatique** : la fiche est proposée à l'unité disponible la plus proche, qui a `timeout_s` (30 s par défaut) pour **Accepter / Refuser** ; refus ou silence → unité suivante ; après `max_attempts` (3) ou faute de candidat → **file du service**, traitée par ses responsables sur `/dispatch-service` (utilisateurs cockpit, plusieurs par service).

- **Déclenchement** par catégorie, réglable dans Field Dispatch (`cockpit_settings._id="field_dispatch"`, `GET/PUT /api/dispatch/config`) : `never` / `always` / `urgency` (niveaux IMP=1, UR=2, UA=3, EU=4). Défauts : Technique sur UA/EU, Sécurité toujours, Secours jamais. Points d'entrée : création et création rapide sans unité, modification (`/update`) d'une fiche sans unité, changement d'urgence (`/set-urgency`). Bouton « Proposer automatiquement » (`POST /api/dispatch/<id>/auto`) pour le reste.
- **Candidats** (`find_candidates`) : même événement, même catégorie effective, statut `patrouille`, vue depuis < 15 min, pas de proposition en cours, **métier compatible** (`field_devices.metiers` choisis à l'appairage parmi les sous-classifications de la catégorie ; liste vide = tous ; comparaison sans accents : `Electricite` == `Electricité`, les deux existent dans `pcorg_lists`). Classement : position fraîche (< 5 min) d'abord, puis distance à vol d'oiseau.
- ⚠️ **Un téléphone verrouillé ne remonte plus sa position** (application web) : la « plus proche » peut s'appuyer sur une position de plusieurs minutes, d'où le tri par fraîcheur avant distance. Sur iPhone, les notifications n'arrivent que si l'app est installée sur l'écran d'accueil.
- **Une proposition à la fois par unité** : `field_devices.pending_proposal` réservé atomiquement. Passer en pause avec une proposition en attente vaut refus immédiat.
- **Accepter = engagement** : `cc.patrouille`, `dispatch.state="assigned"`, statut `intervention`, `active_fiche_id` en une fois. Pas de file hors ligne pour la réponse (une acceptation rejouée plus tard n'a plus de sens).
- **Engagement direct pendant une proposition** (`/api/pcorg/update` avec unité) : `on_manual_assign` annule la proposition. Clôture : `on_close`.
- **Responsable** (`manual_assign`) : si l'unité est occupée, la fiche s'ajoute à ses missions **sans écraser** sa fiche active.
- **Fin d'intervention par l'unité** (`self_close`, défaut Technique) : `POST /field/my-fiches/<id>/finish {outcome, report}`, compte-rendu obligatoire (≥ 5 caractères), photo conseillée. `resolu` clôt la fiche ; `partiel` / `materiel` / `impossible` la renvoient dans la file du service (`cc.patrouille` vidé). L'unité redevient disponible sans clic du PC Org. Rejouable (file hors ligne) : `deja_termine`.
- **Horodatages** `pcorg.intervention` : `engaged_at`, `arrived_at` (ASL manuelle ou GPS), `done_at`, `outcome`, `report` — écrits une seule fois (`mark_step`), base des délais par métier.
- État sur la fiche : `pcorg.dispatch` (`state`, `round`, `current`, `attempts[]`, `queued_at`, `queue_reason`, `assigned_by`). Chaque étape laisse une entrée système « Dispatch auto » dans la chronologie. `/api/pcorg/live` et `/detail` exposent `dispatch`.
- **Planificateur** `DA.start_scheduler()` (tick 3 s, démarré dans le `__main__` d'`app.py` à côté d'Alfred et PMV) : propositions expirées (+ 4 s de grâce) → unité suivante. Suppose un seul process (waitress) ; les transitions restent atomiques (filtre sur l'état attendu).
- Tests : `tests/test_dispatch_auto.py` contre un MongoDB **local réel** (base jetable `titan_test_dispatch_<pid>`, sautés sans Mongo) — les mises à jour conditionnelles et les pipelines de `pcorg_history` ne se simulent pas.

### Contrôles d'accès

- `/field/my-fiches/<id>/detail` : seulement une fiche affectée à la tablette, sa fiche active ou une fiche qu'elle a créée (`403 not_assigned`). Avant, toute tablette lisait toute fiche par son id.
- `/field/photos/*` : tablette non révoquée (cookie `field_token`) ou utilisateur cockpit (JWT, n'importe quel rôle), `Cache-Control: private`. La route était publique (`/field/*` est hors portail).

### Idempotence des actions tablette

⚠️ **Une requête peut arriver au serveur et sa réponse se perdre** (4G). La file hors ligne ou l'agent la renvoient alors. `/field/sos` (`sos_id`) et `/field/create-fiche` (`client_token`) portent une clé générée côté tablette, réservée dans `field_client_requests` (`_id = "<device>|<kind>|<clé>"`, TTL 2 j) par `_claim_client_request` : un renvoi retrouve le résultat du premier envoi. Sans elle, un SOS rejoué créait une fiche, une alerte plein écran sur chaque poste et une diffusion à chaque tablette de plus. Une clé réservée sans résultat depuis plus de 60 s est reprise (premier envoi mort en route) ; une erreur Mongo sur la clé ne bloque **jamais** un SOS.

`/field/status` ignore un renvoi du même statut (`unchanged: true`), sinon chaque retry ajoutait « Engagement confirmé » à la chronologie.

### Statuts rejoués hors ligne

⚠️ Un statut mis en file porte `queued_at` (epoch ms tablette). Le serveur répond **409 `stale_status`** s'il est antérieur à `status_since` : une tablette libérée par le cockpit repassait sinon en intervention au retour du réseau. La file le retire sans alarme et relance `pollFiches`.

⚠️ **Une réponse de poll partie avant un changement local porte l'ancien statut.** `pollFiches` ignore statut et fiche active si `state.statusChangedAt >= début du poll` (posé par `applyLocalStatus`). Sans cette garde, le statut « revenait en arrière » un cycle. Tout changement de statut passe par `postPatrolStatus` (file hors ligne, relecture immédiate, recadence des polls).

`fetchWithTimeout` (12 s) : une requête pendante ne bloque plus un changement de statut, elle part en file.

### Engagement, ASL, fin d'intervention

- L'engagement (« Prendre en charge », « Engagement ») envoie `fiche_id` : le serveur vérifie que la fiche est ouverte et affectée à la tablette (`403 not_assigned`, `409 fiche_indisponible`) et pose `active_fiche_id`. La chronologie reçoit **une** entrée, écrite par le serveur.
- L'ASL automatique (GPS < 10 m) trace désormais la même entrée que l'ASL manuelle.
- Le compte-rendu de fin d'intervention voyage **avec** le statut et s'écrit dans la chronologie côté serveur (il était posté à part, hors file hors ligne).
- En `fin_intervention`, la tablette poll au rythme normal (l'agent attend sa libération) ; seule la `pause` ralentit à 30 s. `anoloc.js` relit immédiatement après « Libérer ».

### SOS

- **Un seul SOS par déclenchement** : clé `sos_id`, 3 renvois automatiques (2/5/10 s) avec la même clé, puis file hors ligne.
- ⚠️ La fiche auto-créée (`PCO.Secours`, UA) est **exclue** de `alert_engine.detect_pcorg_urgency` (`content_category.field_sos`). Sans ça, le moteur levait une seconde alerte « ALERTE SECOURS » un cycle plus tard, décalée d'un poste à l'autre.
- `alert_poller.js` : deux mémoires. **Vu** (sessionStorage, par onglet) et **traité** (localStorage, tout le poste, posé au clic sur un bouton de l'alerte, TTL 24 h). Au chargement d'une page, une alerte récente déjà traitée sur le poste ne repasse plus en plein écran — avant, chaque navigation dans les 5 min réaffichait le SOS. Ne pas partager le « vu » entre onglets : un onglet caché masquerait l'alerte à l'onglet visible.
- Un SOS passe **devant** la file d'alertes (et interrompt une alerte non SOS) et n'est jamais filtré par les préférences locales. Poll immédiat au retour sur l'onglet : Chrome ralentit à 1/min les minuteries d'un onglet en arrière-plan.
- Tablettes : `/field/inbox` marque `sos_resolved` quand la fiche SOS est close ; le repère carte est retiré. Au redémarrage de l'app, un SOS non acquitté et non résolu est réaffiché.

### Inbox

⚠️ `/field/inbox` rend les **200 plus récents** (tri décroissant puis inversé). Le tri croissant + `limit(200)` rendait les 200 plus anciens : au-delà de 200 messages sur 7 jours, plus aucun nouveau message n'arrivait.

### Itinéraire

`setRouteDestination(latlng, polyline, god, ficheId)` mémorise la fiche visée ; `reconcileRoute()` efface le tracé quand elle est close ou désaffectée, ou que l'agent passe en fin d'intervention / disponible. `/api/route/forward` transporte `fiche_id`. Bouton `#btn-route-clear` visible tant qu'un tracé est affiché. Avant, rien n'effaçait jamais un itinéraire.

⚠️ `field-sw.js` sert `field.js` en **cache-first** : toute modification du JS tablette impose d'incrémenter `SW_VERSION`.

### Prise en charge partagée d'un SOS

`POST /api/active-alerts/<id>/take` (`user`, CSRF actif) : le premier opérateur qui clique « Je prends en charge » est posé sur l'alerte (`taken_at`, `taken_by`, `taken_by_name`) par un `find_one_and_update` conditionné à `taken_at` absent — **un seul gagnant**, les suivants reçoivent `409 already_taken` avec le nom. La route ajoute « SOS pris en charge par X » à la chronologie de la fiche SOS et prévient la tablette émettrice (message inbox + push).

Côté postes (`alert_poller.js`), `/api/active-alerts` renvoie ces champs : au poll suivant (5 s max) l'alerte affichée passe en vert « Pris en charge par X à HH:MM », l'alarme s'arrête et elle se ferme seule après 10 s ; un SOS déjà pris est retiré de la file et n'est jamais affiché en plein écran sur un poste qui ne l'avait pas encore vu. Chez celui qui prend, l'alerte se ferme et la fiche s'ouvre.

### Limite connue

Pas de push temps réel vers les postes cockpit (choix assumé) : délai d'affichage d'un SOS et de sa prise en charge de 0 à 5 s selon le poste, plus si l'onglet est en arrière-plan (rattrapage au retour sur l'onglet).

## Centrale d'alerte (`/admin/alertes`, plein écran des postes)

Producteurs : `alert_engine.py` (tâche planifiée, 30 s), `/field/sos` (`field.py`), caméras (`PCA/SCRIPTS/cockpit_dispatch.py`), mots-clés Alfred. Tous écrivent dans `cockpit_active_alerts` (TTL sur `expiresAt`, clé `definition_slug`). Affichage : `static/js/alert_poller.js` (chargé par `index.html`, et par l'admin en mode aperçu).

### Le rendu est piloté par la définition

`/api/active-alerts` joint à chaque alerte un champ `meta` (`name`, `icon`, `color`, `detection_type`, `display_mode`) lu dans `cockpit_alert_definitions`. Le poller pose `--alert-color` sur la boîte ; **un seul bloc CSS générique** colore en-tête, bordure et bouton (teintes assombries par `color-mix` pour que le blanc reste lisible sur une couleur claire). Seul le SOS garde ses règles propres (animations).

⚠️ Avant, tout était codé par slug (`ICON_MAP`, `TITLE_MAP`, un bloc CSS par slug, main courante détectée par le préfixe `pcorg-`). L'alerte `main-courante-flux`, créée depuis l'admin, sortait sans fond d'en-tête, avec un bouton blanc sur blanc, le titre brut « MAIN-COURANTE-FLUX » et sans bouton « Ouvrir la fiche ». **Ne jamais réintroduire de rendu par slug** : la mise en forme main courante se déclenche sur `detection_type === "pcorg_urgency"`. Les tables du poller ne servent plus que de repli.

### Mode d'affichage (`display_mode` de la définition)

| Mode | Effet |
|---|---|
| `banner` | toast + historique, jamais de plein écran |
| `fullscreen` (défaut) | plein écran à acquitter sur chaque poste |
| `critical` | plein écran + son + **prise en compte partagée** ; ne peut pas être coupé par l'opérateur |

Le SOS est toujours `critical`. `POST /api/active-alerts/<id>/take` accepte le SOS et toute alerte `critical` (sinon `400 not_takeable`) : premier arrivé gagnant, les autres postes ferment l'alerte au poll suivant. Une alerte liée à une fiche (`actionData.pcorg_id`) reçoit une entrée de chronologie ; seul le SOS prévient la tablette.

### Préférences locales

`localStorage["cockpit-alert-muted"]` = liste des slugs **coupés** sur ce poste, construite depuis `/api/alert-definitions/mine`. ⚠️ L'ancienne clé `cockpit-alert-prefs` était une liste blanche figée de 9 types : toucher une case coupait en silence secours, sécurité, flux, caméras… Elle est migrée puis supprimée au premier chargement.

### Historique

Écrit par le moteur (`alert_engine.sync_alert_history`, à chaque cycle) : une entrée par alerte active (clé `alert_id`, index unique), toutes sources confondues. `POST /api/alert-history` est un no-op conservé pour les onglets restés sur l'ancien JS. Avant, chaque poste postait sa copie : aucun poste ouvert = aucun historique.

### Moteur

- Main courante : titre `MAIN COURANTE <CATÉGORIE>` ; `actionData` porte `text`, `zone`, `operator` (le poste ne redécoupe plus le message).
- Météo : une alerte par jour **et par niveau**, mémorisée dans `cockpit_alert_engine_state` — l'aggravation vigilance → alerte sonne, la même vigilance ne re-sonne plus après le TTL de 3 h.
- `build_context` : repli sur le paramétrage le plus proche à 7 jours (l'ancien « repli » refaisait le même test).

### Aperçu

Bouton « Aperçu » de la modale admin : `CockpitAlerts.preview(def)` affiche le rendu réel sur le poste de l'admin, sans rien écrire (ni alerte, ni historique, ni WhatsApp). À utiliser pour toute nouvelle définition.

### Explication IA d'une alerte (`alert_ai.py`)

`POST /api/alerts/<id>/explain` (user, CSRF actif) : alerte active, archivée ou historisée. Contexte propre au type (trafic : Waze autour des pins + relevés −90/+60 min ; main courante : fiche, chronologie, fiches même zone/catégorie 60 min ; météo ; checkpoint ; SOS : tablettes les plus proches ; caméra ; saturation porte), UN appel Claude (700 tokens, effort low, JSON 5 clés : `contexte, cause_probable, evolution, action_suggeree, confiance`, « donnée insuffisante » imposée). Cache `alert_explanations` (TTL 7 j) : clics simultanés → 202 `en_cours` ; `?refresh=1` refusé avant 2 min. UI `CockpitAlerts.explainWidget`, hors rangée de boutons (n'empêche jamais l'acquittement ni la prise en compte) ; jamais d'appel en aperçu admin.

### Saturation porte prévue (`door_saturation_forecast`)

Source live : `hsh_transactions_agg` (5 min par tripode/PDA, `gate_name`) ; ⚠️ `tranche` = heure de Paris étiquetée UTC. Aucune capacité en base : capacité = somme des appareils actifs sur 1 h (tripode 900/h, PDA 650/h = p99 mesuré 2026), surcharge `params.capacities`. Prévision = débit 15 min × profil N-1 de la même porte aligné au jour de course (décalage arrondi à la semaine). Série horaire N-1 interpolée entre milieux d'heure (lue en marches, elle faisait sonner à HH:05).
Rejeu 2026 (5 éditions) : 33 alertes « constaté » toutes vraies ; 17 prévisions N-1, 5 confirmées (erreur médiane 21-31 %). La tendance seule (`trend_fallback`) est désactivée par défaut (1 sur 4 confirmée). Définition prod à créer désactivée, en mode `banner`. Mieux attendu en 2027 (archive 5 min comme N-1).

## Alfred — agent IA WhatsApp (`alfred.py`)

WAHA pousse les messages sur `POST /api/wa/webhook` (HMAC, exempté de CSRF). Par groupe (`wa_alfred_config`) : `listen` (ingestion `wa_inbound_messages`, TTL 14 j, gatée par le live-contrôle), `respond_mentions`, `summary_enabled`. Les mentions partent vers le wrapper `/alfred/ask` de la VM (boucle d'outils hors de ce repo) ; les résumés vers Ollama, via le scheduler 60 s du `__main__` d'`app.py`.

- **Déclenchement** : `@alfred` explicite (le mot seul ne suffit plus), mention native (`alfred_lid`), suite de conversation 7 min, ou DM en liste blanche.
- **Envois** : toujours par `WhatsAppService.send_direct`, tracés dans `cockpit_wa_send_history` (`source: "direct"`) et comptés dans les plafonds horaire/journalier. Réponse : ignore les heures silencieuses, refusée si breaker ouvert ou plafond atteint ; phrase d'attente et refus DM sautent dès 80 % du plafond horaire (90 % du journalier).
- ⚠️ **Breaker partagé** : mémoire du module + `cockpit_wa_config{_id: "wa_breaker"}`, relu toutes les 15 s. Il était porté par l'instance, recréée à chaque envoi : il ne s'ouvrait jamais.
- **Résumés** : un seul à la fois par groupe ; après échec, attente `min(intervalle, échecs × 5 min)`. « Résumer » ignore l'attente mais pas la garde (`already_running`). Rétention 180 j.
- **Prompt de résumé** : transcription entre `<<<MESSAGES` et `MESSAGES>>>`, chevrons neutralisés, sauts de ligne aplatis ; heures en Paris. ⚠️ pymongo rend des datetimes naïfs UTC : jamais `astimezone()` sans `replace(tzinfo=utc)`.
- **Webhook** : en prod, secret vide = tous les webhooks refusés. SHA-1 toléré avec avertissement, à retirer une fois vérifié côté WAHA.
- **Évaluation** : chaque échange de mention dans `wa_alfred_exchanges` (TTL 90 j). Le durcissement du wrapper `/alfred/ask` se fait sur la VM.

## Aide à la saisie et précédents des fiches (`pcorg_assist.py`)

Blueprint `pcorg_assist_bp` (user, CSRF actif), consommé par `pcorg.js` (préfixe `pca*`).

- **Suggestions** : `POST /api/pcorg/assist/suggest`, appelé par l'assistant de création dès 25 caractères (debounce 1,2 s, AbortController). Haiku 4.5, prompt bâti sur les référentiels de l'app (`CTX_DESCRIPTIONS`, `URGENCY_LABELS`, `pcorg_lists`) restreints aux catégories autorisées. ⚠️ Jamais appliquées d'office ; toute catégorie hors référentiel ou non autorisée est écartée (`validate_suggestion`). Cache 15 min, 4 appels simultanés max, échec silencieux.
- **Doublons** : sans LLM, fiches du même event/year ouvertes ou de moins de 2 h ; TF-IDF + Jaccard, bonus zone et GPS ; la position seule ne suffit pas.
- **Précédents** : bloc « Précédents » de la fiche, à la demande. Autres éditions (ou tous événements), préfiltre Mongo `$text` puis classement Python ; top 5 avec clôture et chronologie. 15-200 ms sur 30 000 fiches. « Synthèse IA » : 3-5 puces fondées uniquement sur les précédents, cache 30 j (`pcorg_precedent_syntheses`).
- ⚠️ **Index texte `pca_text`** créé à la volée sur `pcorg` si AUCUN index texte n'existe (une collection n'en porte qu'un). `PCA_CREATE_TEXT_INDEX=0` le désactive.

## Briefing de situation et RETEX de fin d'édition (`ai_reports.py`)

UI `static/js/ai_reports.js` (modales injectées), boutons sidebar `.air-sidebar-btn` `data-air-open="briefing|retex"` — surtout PAS `.sidebar-ai`, que `ai_assistant.js` capture.

- **Briefing** (`situation_briefing.py`, manager) : `build_context` lit 7 sources indépendantes (main courante, alertes, présents, trafic, météo, Field, timetable) ; une source en échec est marquée `indisponible`, jamais bloquante (~1 s). `{llm:false}` = contexte brut gratuit ; `{llm:true}` = job, contrat 5 clés (`situation, points_chauds, a_surveiller_6h, ressources, consignes_releve`). ⚠️ Un relevé compteur > 15 min est `releve_perime`, jamais « 0 présent ».
- **Relève automatique** : `cockpit_settings._id="briefing_releve"` `{enabled, times, recipients}` ; tâche `scripts/install_briefing_task.ps1` (toutes les 15 min, **non installée**). Test sans coût : `python scripts/briefing_releve.py --force --no-llm --dry-run`.
- **RETEX** (`edition_retex.py`, admin) : jeu de données complet d'une édition (< 2 s), 2 éditions précédentes, avertissements de comparabilité transmis au modèle. Collection `edition_retex` versionnée ; `GET /api/ai/retex/<id>/html` = page imprimable. ⚠️ `cockpit_alert_history` a un TTL de 7 j : au-delà, bloc alertes `indisponible`, jamais « 0 alerte ».

## Fréquentation depuis le contrôle d'accès live (`live_frequentation.py`)

Pour le RETEX et la vue Fréquentation du rapport de scans, l'**archive du contrôle d'accès live est la source primaire**, l'import Excel (`historique_controle`) le repli, **édition par édition** : `scan_frequentation.build_frequentation_block(..., source_priority=LIVE_FIRST)`. Le **défaut reste l'import seul** (`DEFAULT_SOURCE_PRIORITY`) : `presents_etat.historique_n1` (TV, montre, N-1 affluence) ne change pas de chiffre — vérifié à l'octet sur 10 éditions.

- **Rejeu exact du dashboard** sur `hsh_archive_compteurs_*` / `hsh_archive_tx_*` / `hsh_archive_structure_*` : `presents = current − correction − véhicules`, véhicules cumulés depuis la remise à zéro précédant chaque relevé (`presents_etat.detect_resets`, `vehicle_prefixes`, `solde_vehicules`, partagés avec le direct). Pic = plus haut RELEVÉ. Vérifié : 24H CAMIONS 2026 = 49 975 à 19h15 et 37 501 à 13h06, identiques au dashboard.
- ⚠️ **Les pics des rapports matinaux antérieurs à septembre 2026** (et des logs) sont le maximum BRUT du compteur, véhicules non retirés : +2 000 à +7 000 sur 24H AUTOS 2026. Le rejeu est la bonne référence.
- ⚠️ **Le nom d'archive ment** (suffixe = année du clic, autre édition dedans) : seule la FENÊTRE course −10 j / +3 j fait foi. `has_live_archive` exige des tranches tx dans cette fenêtre, donc toutes les éditions ≤ 2025 retombent sur l'import.
- ⚠️ **`___GLOBAL___` n'est pas archivé** par `hsh_archive_and_purge` : pour une édition archivée, Area 628, aucune correction, activation estimée (début de la série continue de relevés).
- Relevés antérieurs à une remise à zéro survenue avant la fin du jour de course = fantômes, écartés ; jours `measured: false` avec `unmeasured_reason` (`compteur_non_remis_a_zero`, `compteur_fige`, `aucune_mesure`).
- ⚠️ **Solde initial** : sans remise à zéro avant l'édition, tout est majoré (24H MOTOS 2026 : 8 916 au premier relevé, ~+7 300 vs import sur chaque pic). Signalé au modèle (`solde_initial_compteur`) et dans les réserves du RETEX.
- Créneaux 15 min étiquetés à leur FIN (convention de l'import). Entrées de l'enceinte = compteur (passages valides) moins véhicules tx ; portes = gates HSH rattachées à l'Area 628 (les tx comptent aussi les scans refusés).
- Sans relevé du compteur sur la période des scans (SUPERBIKE 2026) : repli `solde_scans` (solde des passages personnes).
- Les éditions live et import ne se comparent pas sur les entrées (`entries_comparable=False`, `mixed_sources`) ; la source de chaque édition est donnée au modèle et affichée dans l'annexe du RETEX.
- `hsh_archive_and_purge` copie aussi le `___GLOBAL___` (s'il désigne l'événement archivé) dans `hsh_archive_global_<tag>` (doc `_id: "global"`) ; le rejeu l'utilise si son activation tombe dans la fenêtre de l'édition. Rattrapé à la main pour 24H CAMIONS 2026 ; les archives antérieures n'en ont pas (estimation).

## Momentus Elite — réservations commerciales (synchro + lieux)

Momentus Elite (VenueOps) est l'outil de réservation des séminaires, réceptifs et hospitalités vendues. Instance **EU** (`auth-api.eu-venueops.com/token`, `api.eu-venueops.com`), identifiants **lecture seule** `MOMENTUS_CLIENT_ID` / `MOMENTUS_CLIENT_SECRET` (niveau Machine pour la tâche SYSTEM). Jeton ~23 h.

- **Synchro** `momentus_sync.py` (tâche `scripts/install_momentus_task.ps1`, toutes les heures à HH:05) : incrémental sur `lastModifiedOn` (recouvrement 15 min) + fonctions J-2/J+45 ; import complet automatique si le dernier a plus de 20 h (seul moyen de voir une suppression). N'émet que des GET et le POST de recherche `/events/all`. Rien n'est jamais supprimé : `_sync.deleted_at`. Garde-fou : pas de réconciliation si l'API rend < 90 % de l'existant. Verrou dans `momentus_sync_state`, historique `momentus_sync_runs` (TTL 90 j), `cron_status.json`.
- **Collections** : `momentus_events` (tel que rendu par l'API, espaces réservés inclus), `momentus_functions`, `momentus_venues`, `momentus_rooms`, `momentus_setup`, `momentus_lieux_mapping` (écrite par Groundmaster).
- ⚠️ **`venueIds` est obligatoire** sur `/v1/events/all` ; `query-by-date-range` exige aussi `roomIds` (0 résultat avec une liste vide). Rien avant 2020.
- ⚠️ **Les dates `start`/`end` d'un événement sont une enveloppe** (roulages et formations de février à décembre). Ce qui se passe un jour donné se lit dans `bookedSpaces` ou les fonctions (créneaux horaires).
- ⚠️ **`usageType: moveIn` n'est PAS un montage** : c'est le mode de réservation par défaut (79 % des espaces des séminaires, 91 % des travaux ; le séminaire CPAM de 434 personnes y est en moveIn 8h30-17h). Affiché « réservé ». `event` (exploitation), `moveOut` (démontage) et `dark` (bloqué) sont des choix volontaires. Le vrai pilote de montage est dans `spaceUsageName` du détail `/v1/booked-spaces/{eventId}` (« Montage par Julien », « Régie Max »…), non synchronisé et rempli à ~60 %.
- **Export Cowork** `scripts/export_cowork_activites.py` : JSON des 60 prochains jours (activités Momentus, grands événements Groundmaster, lieux avec coordonnées, résumé par jour, notice des limites) déposé chaque jour dans SharePoint SAFER / `COWORK/activites_site_60j.json` (Graph délégué, application Entra TITAN, cache `msal_cowork_cache.bin`, connexion initiale `--login`).
- ⚠️ `momentus_events` embarque `contactRoles` (nom, mail, téléphone clients). Les API de lecture (`momentus_lieux.bookings_for_rooms`) n'en exposent rien, ni les montants.

### Réservations par lieu de la carte

Choix utilisateur : **information seulement, aucune alerte de conflit**. Pendant un grand événement, la plupart des réservations Momentus sur un lieu sont l'événement lui-même vu côté commercial (partenaire installé dans un Garden), pas un conflit.

- **Trois apps, mêmes routes, mêmes fichiers partagés** (copies identiques, à reporter partout) : `momentus_lieux.py` (Cockpit, `../groundmaster/`, `../looker/`) et `static/js/momentus_lieu.js` (Cockpit, Looker ; `groundmaster/static/js/momentus-lieu.js`).
- **Looker** : `looker/momentus_carte.py` (fabrique de blueprint, GET user) + bouton « Momentus » en haut à droite de la carte (`MomentusLieu.addLayerControl`) avec choix de période : aujourd'hui, demain, 7 j, 30 j, mois en cours, dates libres.
- `momentus_lieux.py` (pur) : `lieu_payload(db, feature_id, from, to, event, year)` → période (dates, sinon montage → démontage du paramétrage, sinon aujourd'hui + 60 j), espaces rattachés, réservations avec phases fusionnées et créneaux horaires.
- Routes `momentus_api.py` (user) : `GET /api/momentus/mapped`, `GET /api/momentus/lieu/<feature_id>`. Mêmes routes dans Groundmaster.
- UI `static/js/momentus_lieu.js` (**copie identique** : `groundmaster/static/js/momentus-lieu.js`) : bouton « Réservations Momentus » dans la popup des lieux de la carte d'accueil (`map_view.generatePopup`), seulement si le lieu a un espace rattaché validé.
- **Calque carte** (bouton `storefront` en bas à droite, `map_view.toggleMomentus`, `GET /api/momentus/carte`) : tous les lieux rattachés ayant au moins une réservation sur la période de l'événement sélectionné, **qu'ils soient activés ou non dans Groundmaster**. La carte normale n'affiche que les lieux activés pour l'événement : sans ce calque, un Garden occupé par un séminaire pendant les 24H Camions (non activé pour l'événement) restait invisible. Violet plein = réservation en cours aujourd'hui.
- **La correspondance espace Momentus → `_id_feature` se fait dans Groundmaster** (`/momentus/lieux`, admin). Seuls les rattachements `valide` comptent : une proposition par nom identique peut être fausse (« Hunaudières » du PEC, une salle, contre le parking HUNAUDIERES).

### Rapport de scans depuis l'archive live (`live_scan_units.py`)

Pour une édition suivie par le contrôle d'accès live (2026 : 24H AUTOS, GPF, LMC, SUPERBIKE, 24H CAMIONS, 24H MOTOS), le rapport de scans est construit depuis l'archive HSH (tx + erreurs + structure) ; l'import Excel (`complet`) n'est que le repli, puis `parking_scans`. `scan_report_build.resolve_units_doc` porte cette priorité, utilisée par la génération, le mapping et l'analyse rédigée.

- **Pseudo `complet` EN MÉMOIRE, jamais écrit** dans `historique_controle` (référence N-1 de la TV, de la montre, des projections). Même contrat que l'import, plus `source: live_controle`.
- **Porte** = gate de l'Area enceinte (628) ; **zone** = l'AREA HSH (toutes ses gates agrégées), exactement la colonne « zone » de l'export. L'Area de la gate prime sur celle du checkpoint (PDA mobiles).
- Catégorie devinée du nom d'Area (`category_source: hsh_area`) ; rattachement par `scan_import.resolve_features` (overrides, récolte, geojson), identique à l'import.
- Créneaux 15 min étiquetés à leur FIN ; `tranche` et `date_utc` sont déjà en heure de Paris : aucune conversion.
- ⚠️ **Refus exclus** via `hsh_archive_erreurs_*` (par checkpoint + 5 min, répartition au prorata si l'archive est incomplète). Les statuts 107 et 133 sont des passages (le compteur les compte).
- **Portes : personnes seules** (véhicules exclus) ; **zones : véhicules inclus** si la catégorie est à flux véhicules. Écart cumulé à l'Excel sur 24H MOTOS 2026 : 18 691 contre 26 982 à 55 079 pour les autres conventions. ⚠️ L'Excel des zones compte aussi les refus (P OUEST 3 105 contre 2 473 valides).
- Parkings foot M1/M2/M3 (Areas 1227/1229/1231) écartés par ID : le match du 19/09 tombe dans la fenêtre de 24H CAMIONS 2026.
- **Mapping d'une édition live** : aucun document réécrit, seulement `scan_feature_overrides`, puis régénération. « Mémoriser » coché = override global, décoché = ciblé sur l'édition. On enregistre l'état complet de l'unité : un override ciblé remplace entièrement le global.
- `/scan-report/available` liste les éditions live même sans rapport (`generated_at: null`). L'en-tête du rapport affiche la source et les conventions.
- 24H MOTOS 2026 live contre import : portes +2,7 % au total, pic au même créneau sur 12 portes ; PORTE CIK +39 % et PORTE NORD VEHICULES +80 % restent inexpliquées (hypothèse : synchro tardive des PDA après l'export).
- Génération (analyse désactivée) : 1,5 s (24H CAMIONS), 1,8 s (24H MOTOS), 6 s (24H AUTOS).

## PMV — remorques à panneau à message variable (`/pmv`)

Cockpit pilote directement les remorques PMV louées (contrôleur **Sigma 3000**,
fabricant QS, protocole **JetFileII**) pour y afficher des messages 96 × 64 au
bord des routes. Remplace le logiciel du fabricant (Sigma Play). Page réservée
au rôle **manager** ; registre, groupes, jumeau et « envoi sans sauvegarde »
réservés à **admin**.

Dossier de référence versionné : **`docs/pmv/`** (protocole à l'octet dans
`docs/SIGMA3000_PROTOCOLE.md`, implémentation validée sur remorque le 27/09/2026
dans `reference/`, vecteurs d'octets, jumeau `banc-essai/faux_panneau.py`,
éditeur d'origine, cahier des charges et prompt de départ).

### Architecture

```
templates/pmv.html + static/js/pmv.js + static/js/pmv_editor.js + static/css/pmv.css
        │ fetch + X-CSRFToken (jamais d'IP : un panneau_id du registre)
pmv.py            blueprint pmv_bp (/pmv, /api/pmv/*) : jobs, verrous, Mongo, planificateur
        │
pmv_panneau.py    dialogue TCP 9520 (classe Panneau), opérations test/envoi/restauration, DNS
        │
pmv_protocole.py  PUR (ni Flask ni Mongo) : image → BMP 5-6-5 → fichier-message → trames
```

**C'est le serveur qui parle aux remorques**, pas le navigateur (le relais
PowerShell du dossier d'origine est inutile ici). Chaque remorque est derrière un
routeur 4G joint par `info<PLAQUE>.ddns.net`, TCP **9520**, adresse JetFileII
`0x0101`. Le message est écrit en `D:\T\PMVED.Nmg` (flash) puis la liste de
lecture `SEQUENT.SYS` le fait jouer. ~2 s par image quand la 4G va bien (0,7 s
contre le jumeau).

### Règles de dialogue — NON NÉGOCIABLES (recopiées en tête de `pmv_panneau.py`)

Chacune vient d'un incident réel sur remorque :

1. **Octet `00` après CHAQUE trame** — sans lui le panneau ne répond jamais.
2. **Numéro de séquence unique sur toute la session TCP** — sinon un accusé
   tardif est pris pour la réponse suivante (liste de lecture détruite le 27/09).
3. **Réponse non conforme : l'ignorer et continuer d'écouter**, sans renvoyer.
4. Délais 1,5 s puis 10 s puis 10 s, trame renvoyée identique ; **échéance
   globale** de 180 s par opération.
5. Validation d'une réponse = `check_reply` de la référence.
6. **Liste blanche** `1/0x02`, `2/0x08`, `2/0x02` appliquée dans `frame()` ET
   `Panneau.commande()`. **Jamais `2/0x0C`** (réécrit la config d'affichage).
   **Pas de `1/0x10`** non plus, bien que `envoyer.py` l'émette : elle est
   absente des vecteurs validés et `CONFIG.SYS` suffit pour la dalle.
7. Lire `CONFIG.SYS` avant tout envoi ; **arrêt si la dalle n'est pas 96 × 64**.
8. **Sauvegarde de l'affichage d'avant** (`pmv_sauvegardes`, une par remorque) :
   `SEQUENT.SYS` illisible ou sans `SQ` → arrêt ; si la liste contient déjà
   `PMVED.Nmg`, la sauvegarde existante n'est **pas** écrasée (on garde
   l'affichage d'avant NOTRE premier envoi) ; sinon elle est remplacée.
9. **Une opération à la fois par remorque** : verrou par remorque, la seconde
   reçoit `409 busy`.
10. **La flash s'use** : même image déjà affichée d'après l'historique →
    `409 deja_affiche` (le front propose de forcer) ; en groupe ou programmé, la
    remorque est écartée ; deux programmations sur une même remorque doivent être
    espacées de **15 min**.

⚠️ **Ne jamais « améliorer » les fonctions reprises de `docs/pmv/reference/jetfile.py`**
(`bmp565`, `panel_file_like_sigma_editor`, `frame`, `check_reply`,
`write_file_frames`, `sequent_sys`, `write_sys_frames`). Un octet de différence et
le panneau n'affiche plus rien. Les tests les vérifient contre `docs/pmv/vecteurs/`.

### Sécurité

- Le contrôleur n'a **aucune authentification** : Cockpit est le point de contrôle.
- L'API ne reçoit **jamais d'adresse** : la cible vient du registre (plaque
  résolue, ou IP fixe saisie par un admin). Port forcé à 9520.
- `127.x` (le jumeau) n'est admis que sur une remorque marquée `jumeau`, et
  utilisable seulement par un admin (ou en `CODING`). Un DNS qui renverrait
  `127.x` est refusé.
- CSRF actif sur toutes les écritures (blueprint **non** exempté).
- Audit append-only `pmv_audit` (TTL 1 an) : qui, quand, IP, remorque, action, résultat.

### Résolution DNS

`pmv_panneau.resoudre_plaque` interroge **directement 1.1.1.1 puis 8.8.8.8 en UDP**
(paquet DNS minimal écrit à la main, sans cache : l'IP 4G change), repli sur le
DNS de Windows. NXDOMAIN = « remorque éteinte ». Cache de 60 s côté page
(`GET /panneaux/etat`), mais **chaque opération re-résout** avant de se connecter.

### Jobs, envoi groupé, planificateur

- Une opération = un **job** dans un thread (`_JOBS`, TTL 1 h), suivi par
  `GET /api/pmv/jobs/<id>` (étapes cochées, progression, journal hexa plafonné).
  `_reserver_job` prend le verrou et crée le job ; `_executer` fait le travail et
  **libère le verrou dans `finally`** (attrape `BaseException`).
- **Envoi groupé** (`lancer_envoi_groupe`) : réserve d'abord TOUTES les
  remorques, puis les traite **3 à la fois** (`ThreadPoolExecutor`). Suivi par
  `GET /api/pmv/runs/<id>`. Pas d'envoi « sans sauvegarde » en groupe.
- **Planificateur** `pmv.start_scheduler()` : thread `pmv-scheduler`, tick 30 s,
  démarré dans le bloc `__main__` d'`app.py` à côté d'Alfred (même garde
  reloader). Réservation atomique (`find_one_and_update` en_attente → en_cours).
  **Une programmation dépassée de plus de 10 min n'est pas envoyée** (`manquee`) :
  afficher en retard un message daté est pire que rien.
- ⚠️ `_JOBS`, les verrous et `_RUNS` supposent **un seul process** (vrai sous
  waitress, comme `scan_report.py`). Multi-workers : passer à un verrou Mongo.

### Routes (`/api/pmv/...`, manager sauf mention)

| Route | Rôle |
|---|---|
| `GET /pmv` | page (onglets Poste, Messages, Programmation, Historique, Registre admin) |
| `GET/POST/PUT/DELETE panneaux[/<id>]` | registre (écriture admin) |
| `POST panneaux/import` | import de l'export JSON des fiches de l'ancien éditeur (admin) |
| `GET panneaux/etat`, `GET panneaux/<id>/etat` | résolution, affichage déduit, dernier envoi, sauvegarde |
| `POST panneaux/<id>/test` · `/envoi` · `/restaurer` | lance un job ; `envoi` : `{message_id` ou `png_b64, force?, sans_sauvegarde?}` |
| `GET jobs/<id>[?journal=1]` | suivi d'un job |
| `GET/POST/PUT/DELETE groupes[/<id>]` | groupes (écriture admin) |
| `POST groupes/<id>/envoi`, `GET runs/<id>` | envoi groupé et suivi |
| `GET/POST/DELETE messages[/<id>]`, `PUT messages/<id>` | bibliothèque |
| `GET/POST programmations`, `DELETE programmations/<id>` | programmation (annulation si en attente) |
| `GET envois[/<id>]` | historique |
| `GET carte` | couche PMV de la carte d'accueil (sans DNS, hors jumeaux) |

Erreurs au format `{"ok": false, "error": "<code>"[, "message"]}` ; les messages
d'échec d'opération sont en français sans jargon (`MESSAGES_ERREUR`).

### Collections MongoDB (index créés à la volée, `_ensure_indexes`)

| Collection | Contenu |
|---|---|
| `pmv_panneaux` | plaque normalisée (`dw330hc`, unique), nom, localisation, lat/lng, `ip_fixe`, `jumeau`, `actif`, `derniere_resolution`, `dernier_affichage` |
| `pmv_messages` | nom, catégorie, étiquettes, event/year, `rgb` (Binary 18 432 o), `png_b64`, `sha256`, auteur |
| `pmv_groupes` | nom (unique), `panneau_ids[]` |
| `pmv_sauvegardes` | `SEQUENT.SYS` d'avant notre premier envoi, par `panneau_id` |
| `pmv_envois` | historique permanent : type, message, `sha256`, miniature, user, event/year, durée, résultat, étapes, journal, `groupe_run_id`, `programmation_id` |
| `pmv_programmations` | cible (panneau ou groupe), message, `at` (naïf Paris), statut `en_attente/en_cours/terminee/partielle/echec/annulee/manquee`, bilan |
| `pmv_audit` | journal d'audit, TTL 365 j |

### Éditeur (`static/js/pmv_editor.js`)

Portage complet de `docs/pmv/editeur/PMV_Bitmap_Editor.html` (v1.3.00) dans la
charte Cockpit : **mêmes algorithmes** (Bresenham, formes, triangle, remplissage,
polices 4×5 et 5×7, import BMP 1/4/8/24/32 bpp **+ 16 bpp 5-6-5**, volet Copier,
insertion, copier/coller), balisage dans `pmv.html` (préfixe `ped-`), icônes
Material Symbols, toasts Cockpit, évènements **pointeur** (tablette). Le volet
Réseau de l'original (DoH depuis le navigateur) n'est pas repris : le registre le
remplace. Raccourcis actifs seulement dans l'onglet Messages, hors saisie.

L'image quitte le navigateur en **PNG 96 × 64** ; le 5-6-5 est toujours fabriqué
**par le serveur** avec la fonction de référence. Le serveur refuse toute autre
taille (le recadrage se fait dans l'éditeur).

### État affiché

On ne lit que le **nom du fichier joué** (`SEQUENT.SYS`), jamais les pixels. Si
c'est `PMVED.Nmg`, l'UI dit « Affiche probablement : <dernier message envoyé par
Cockpit> » ; sinon « Message Sigma : <nom> ». Toujours formulé comme une déduction.

### Carte d'accueil

Bouton `signpost` en bas à droite de la carte (`map_view.js`, `togglePmv`),
**managers seulement** (`window.__userIsManager`), calqué sur le bouton portes.
Lit `/api/pmv/carte` (état de la dernière résolution connue, sans déclencher de
DNS), rafraîchi toutes les 2 min, lien « Ouvrir dans PMV » → `/pmv?panneau=<id>`.

### Recette

```bash
python -m pytest tests/pmv -q      # octets vs vecteurs, liste blanche, dialogue contre le jumeau
python docs/pmv/banc-essai/faux_panneau.py            # jumeau 127.0.0.1:9520
CODING=true python app.py                             # remorque de test "jumeau" en IP fixe 127.0.0.1
python docs/pmv/banc-essai/comparer_capture.py docs/pmv/vecteurs/<image>.png   # après un envoi : "conforme"
```

**Jamais d'envoi vers une vraie remorque sans l'auteur**, sur une remorque hors
service, avec quelqu'un qui voit le panneau.

### Hors périmètre (non mesuré sur remorque : ne pas coder sans essai validé)

Plusieurs images en alternance dans `SEQUENT.SYS`, messages texte en police du
panneau, effets, luminosité, extinction/allumage (`4/0x03`, `4/0x04`), mode CRC
(`55 A3`), mot de passe du contrôleur (`czLogin`), forme `80 7F` de `SEQUENT.SYS`
(seule la forme `0F FF` est prouvée).

### Pièges

- **Octet `00` final et séquence unique** : les deux défauts ne se voient pas
  contre un serveur tolérant ; le jumeau les exige (mode strict), la remorque aussi.
- **Tester une image identique ne réécrit rien** (`deja_affiche`) : pour un
  essai répété, passer `force` (confirmation côté UI).
- **`window.__userIsManager` doit être posé avant `map_view.js`** (c'est le cas
  dans `index.html`), sinon le bouton PMV n'apparaît jamais.
- **`static/libs/leaflet/` n'a pas ses images** (`layers.png`) : `pmv.html` charge
  Leaflet 1.9.4 depuis unpkg comme les autres pages, avec empreinte SRI.
- **Libellés sans accents** dans l'UI PMV, comme les autres pages admin.
