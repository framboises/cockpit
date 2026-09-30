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

## Documentation par domaine

Le detail de chaque domaine vit dans `docs/claude/`. **Lire le fichier concerne AVANT de modifier le code du domaine** : il porte les pieges verifies, les conventions de donnees et les regles non negociables (PMV notamment).

- `docs/claude/vision.md` : Integration Vision (tablettes scan billets vehicule, JWT RS256, CORS, sync Firestore facturee)
- `docs/claude/ia-resume-pcorg.md` : Assistant IA : resume de periode pcorg, rapport matinal, couts et budget IA, prompt caching
- `docs/claude/crise.md` : Exercices de crise : auth PIN animateur, sessions JWT, anti-bruteforce
- `docs/claude/routing.md` : Routing Valhalla : modes auto/god, overrides admin, patches OSM
- `docs/claude/affluence.md` : Affluence previsionnelle : panier ticketing, ventes_prev vs ventes_prev_final, projections
- `docs/claude/scans.md` : Chaine scans : import Excel, historique_controle, rapport HTML, vue Frequentation, analyse Claude
- `docs/claude/presents.md` : Presents sur site (presents_etat.py) : remises a zero, pics, serie N-1
- `docs/claude/montre.md` : Montre Garmin (cote serveur et pieges Connect IQ) : trafic_etat, meteo_etat, payload, guidage
- `docs/claude/main-courante.md` : Main courante pcorg : pcorg_history, fusion SQL/Cockpit, droits, CSRF d'onglet ancien, aide a la saisie (pcorg_assist)
- `docs/claude/field.md` : Tablettes Field : categories, dispatch automatique, idempotence, statuts hors ligne, SOS
- `docs/claude/alertes.md` : Centrale d'alerte : rendu pilote par la definition, display_mode, explication IA, saturation porte
- `docs/claude/alfred.md` : Alfred (agent IA WhatsApp) : webhook WAHA, breaker, resumes Ollama
- `docs/claude/ai-reports.md` : Briefing de situation et RETEX de fin d'edition (ai_reports.py)
- `docs/claude/frequentation-live.md` : Frequentation depuis l'archive du controle d'acces live (live_frequentation.py)
- `docs/claude/momentus.md` : Momentus Elite : synchro, reservations par lieu ; contient aussi le rapport de scans depuis l'archive live (live_scan_units.py)
- `docs/claude/pmv.md` : PMV (remorques Sigma 3000, JetFileII) : regles de dialogue NON NEGOCIABLES, securite, jobs
