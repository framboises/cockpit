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
| `CLAUDE_TIMEOUT_SECONDS` | Timeout HTTP appel Claude | `60` |
| `CLAUDE_MAX_TOKENS` | `max_tokens` envoyé à Claude | `2048` |
| `CRISE_JWT_SECRET` | Clé HS256 pour les sessions animateur d'exercice de crise | — (refus de démarrage en prod si vide) |
| `CRISE_JWT_TTL_HOURS` | Durée du cookie de session animateur | `8` |
| `CRISE_PIN_LOCKOUT_THRESHOLD` | Nombre d'échecs avant lockout d'IP | `6` |
| `CRISE_PIN_LOCKOUT_WINDOW_MIN` | Fenêtre d'évaluation des échecs | `15` |
| `CRISE_PIN_LOCKOUT_DURATION_MIN` | Durée du lockout après dépassement du seuil | `60` |

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
  - `POST /api/pcorg/summary/generate` (`manager`) — body `{event, year, period_start, period_end}` (ISO, datetime-local accepté → interprété en Europe/Paris). Court-circuite l'appel Claude si `kpis.total == 0` (sections "RAS").
  - `GET /api/pcorg/summary/list?event=&year=` (`manager`) — liste légère (sans `kpis`/`sections`).
  - `GET /api/pcorg/summary/<id>` (`manager`) — détail complet.
  - `DELETE /api/pcorg/summary/<id>` (`admin`).
- **Frontend** : `static/js/ai_assistant.js` (IIFE autonome). Les templates exposent `window.__userIsManager` à côté de `window.__userIsAdmin` ; le JS bloque l'ouverture de la modale aux non-managers (en plus du backend).

### Collection MongoDB `pcorg_summaries`

```javascript
{
  _id, event, year,
  period_start, period_end, created_at,
  created_by, created_by_name,
  fiches_count, truncated,
  kpis: { total, open, closed, by_category, by_urgency,
          top_zones, top_sous_classifications, top_operators, avg_duration_min },
  sections: { faits_marquants, secours, securite, technique, recommandations },
  raw_text, model, usage: { input_tokens, output_tokens }
}
```

Index : `(event, 1), (year, 1), (period_start, -1)` créé lazy au premier accès.

### Prompt Claude

Le `system` impose un **JSON strict à 5 clés** (`faits_marquants`, `secours`, `securite`, `technique`, `recommandations`) en français. Si le retour n'est pas parsable, le texte brut est stocké dans `sections.faits_marquants` et conservé dans `raw_text` pour debug. Plafond de **80 fiches** envoyées à Claude (priorité aux fiches `niveau_urgence ∈ {EU, UA}` ou `is_incident: true` qui sont toutes incluses) ; flag `truncated` exposé dans la modale.

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
