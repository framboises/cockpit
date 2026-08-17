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
