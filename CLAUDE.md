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
| `ANTHROPIC_API_KEY` | Clé API Anthropic (Assistant IA — résumé pcorg) | — (route renvoie 503 si vide) |
| `CLAUDE_MODEL` | Modèle Claude utilisé par l'Assistant IA | `claude-sonnet-4-6` |
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

### Constantes

- `VISION_LIEUX = ["Ouest", "Panorama", "Houx"]` dans `vision_admin.py` — à mettre à jour si on ajoute des lieux Vision (et synchroniser les options du dropdown dans `field_dispatch.html` + le validator côté Vision).

## Assistant IA — résumé de période des fiches PC Organisation

Sur la sidebar de `index.html`, `edit.html`, `analyse_ops.html`, le bouton **« Assistant IA »** (classe `.sidebar-ai`) ouvre une modale qui génère un compte-rendu structuré d'une période sur la collection `pcorg`. Réservé au rôle **manager** (et au-dessus).

### Architecture

- **Module Python** : `pcorg_summary.py` — helpers purs (`compute_kpis`, `select_fiches_for_prompt`, `build_prompts`, `call_claude`, `save_summary`, `list_summaries`, `get_summary`, `delete_summary`, `generate_period_summary`). Appel HTTP direct à `https://api.anthropic.com/v1/messages` (pas de SDK `anthropic`), pattern calqué sur `traffic.py` (Waze).
- **Routes** dans `app.py` (à côté des routes `/api/pcorg/*`) :
  - `POST /api/pcorg/summary/generate` (`manager`) — body `{event, year, period_start, period_end, model?, dry_run?}` (ISO, datetime-local accepté → interprété en Europe/Paris). Court-circuite l'appel Claude si `kpis.total == 0` (sections "RAS"). `model` accepte une whitelist (`claude-sonnet-4-6`, `claude-sonnet-4-5`, `claude-opus-4-7`, `claude-opus-4-6`, `claude-haiku-4-5`) sinon fallback `CLAUDE_MODEL`. `dry_run=true` retourne le prompt assemblé sans appeler Claude (itération rapide sans coût).
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

`frequentation` porte en plus `source: 'scan_import'`, `excluded_autre` et `doors_without_direction` — traçabilité de ce qui manque à la courbe de présents.

### Rattachement aux entités cartographiques

La clé durable est **`properties._id_feature`** (chaîne hexadécimale de 24 caractères). **Il n'existe pas de `id_feature`.** Présent sur 100 % des features de `portes`, `hospitalites`, `terrains`, `tribunes`.

Résolveur à 3 niveaux, dans l'ordre :

1. **Corrections manuelles** persistées dans `scan_feature_overrides`
2. **Récolte** des documents `historique_controle{type:portes}` existants — 39 noms curatés, zéro ambiguïté, source la plus fiable car elle connaît les libellés historiques
3. **Rapprochement normalisé** contre le GeoJSON (casse, accents, ponctuation ; strip des préfixes `AA `/`P `/`PARKING ` côté zones ; `ANCIEN 2025`/`NUMERO 2026` pour les tribunes)

`canonical_porte()` replie en plus les variantes orthographiques des deux côtés : `PORTAIL`→`PORTE`, `VEHICULES`→`VEHICULE`, `PIETONS`→`PIETON`. C'est ce qui rattache `PORTAIL HOUX 5` à `PORTE HOUX 5`.

Un rapprochement **multi-candidats est laissé non résolu** (`PORTE CIK` existe deux fois dans le GeoJSON) : l'UI propose alors les candidats et des suggestions par score de Jaccard.

Sur 24H MOTOS 2024 : 14/19 unités résolues automatiquement, 17/19 après mapping manuel. `PORTE NORD CLUB` et `CONCENTRATION` n'ont aucune entité — c'est normal, certaines unités sont des services, pas des lieux.

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

Modèle par défaut **`claude-sonnet-5`** (ajouté à `ALLOWED_MODELS` et `MODEL_PRICING_USD_PER_MTOK` dans `pcorg_summary.py`). Tarif catalogue 3 $/15 $ déclaré ; un tarif d'introduction 2 $/10 $ court jusqu'au 31/08/2026, donc l'estimation de coût est majorée d'ici là.

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

Sans `ANTHROPIC_API_KEY`, le rapport se génère **sans la section** (log en warning, `info.frequentation_analysis == 'absente'`) — jamais d'échec de génération. `POST /scan-report/generate` accepte `{"analysis": false}` pour couper l'analyse franchement.

⚠️ **`pcorg_summary.call_claude` filtre les sections sur `section_keys`, par défaut les neuf clés du résumé pcorg.** Tout appelant qui impose un autre contrat JSON **doit** passer `section_keys`, sinon ses sections sont silencieusement remplacées par des sections pcorg vides. C'était le cas de `generate_scan_analysis` (5 sections sur 7 perdues) avant que le paramètre n'existe.

### Collections créées

| Collection | Contenu |
|------------|---------|
| `historique_controle_archive` | Générations remplacées, append-only, pas de TTL |
| `scan_feature_overrides` | Choix manuels par nom de scan : `_id_feature` et/ou `category`. Index unique `(scan_name, kind, event, year)` |
| `scan_analyses` | Analyses Claude historisées. `kind` vaut `scans` ou `frequentation` ; les documents antérieurs au champ sont des analyses de scans. Les analyses de fréquentation portent une `fingerprint` (réutilisation sans appel API) |

### Pièges

- **`year` doit être un `int`.** Un `str` créerait un doublon sous l'index unique au lieu de mettre à jour.
- **`event` est le nom cockpit** (`24H MOTOS`), jamais le slug `24h_du_mans` de l'ancienne chaîne. `EVENT_ALIASES` ne sert plus qu'au repli sur les rapports historiques.
- **Datetimes naïfs Paris.** Écrire de l'UTC décalerait silencieusement les données de 2 h en été.
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
