<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

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
