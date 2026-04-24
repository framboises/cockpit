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

L'app Vision (scan billets véhicule, repo voisin `../vision`) est **JWT-gated par Cockpit** : la tablette ouvre Vision directement par URL (pas via Field), Vision affiche un écran de pairing au premier accès, l'opérateur saisit un code généré dans Cockpit Admin, et Cockpit retourne un JWT RS256.

### Modèle pairing (un code = une app)

- `field_pairings.app` : `"field"` (default) ou `"vision"`. Un code Field ne peut pas servir à Vision (et inversement) — message d'erreur explicite (`code_for_vision` / `code_for_field`).
- **Form admin** (`field_dispatch.html`) : radio Field/Vision en haut, champs conditionnels (beacon_group requis pour Field, vision_lieu requis pour Vision). Badges `FIELD`/`VISION` dans la liste des codes actifs et la table devices.
- **Cas particulier** : un code Field peut activer Vision en bonus (`vision_enabled=true` + `vision_lieu`) pour ouvrir Vision via la route legacy `/field/vision/launch` depuis Field.

### Routes

- `POST /field/admin/pairings` (admin) — création de code, payload `{app, name, event, year, [beacon_group_id|vision_lieu], notes?}`.
- `POST /field/admin/devices/<id>/vision` (admin) — modifier `vision_enabled`/`vision_lieu` à la volée. UI : bouton `qr_code_scanner` dans la table devices, prompt JS dans `field_admin.js`.
- `POST /field/api/vision/pair` (**public + CORS** depuis `vision-a0f55.web.app`) — endpoint principal appelé par Vision. Accepte `{code}`, vérifie `app="vision"`, génère le JWT, consomme le code (suppression + insertion d'un doc `field_devices` avec `app="vision"` pour traçabilité).
- `GET /field/vision/launch` (legacy, optionnel, auth `@field_token_required`) — génère un JWT et redirige `302` vers `VISION_APP_URL?t=<JWT>`. Conservé pour les tablettes Field qui veulent ouvrir Vision sans repairer ; bouton retiré de `field.html` par défaut.

### JWT

- Algo RS256, `iss="cockpit-field"`, `exp = parametrages.data.globalHoraires.demontage.end + 1 jour` (fallback 24 h).
- Clés RSA générées via `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048` dans `cockpit/keys/vision_jwt_{private,public}.pem`. Privée dans `.gitignore` (`keys/*_private.pem`). Publique embarquée en dur dans `vision/associer.html` (`VISION_JWT_PUBKEY_PEM`). Validation côté Vision en local (`crypto.subtle.verify`) — pas d'appel réseau pour valider, mode hors ligne préservé.
- Variables d'env : `VISION_JWT_PRIVATE_KEY` (path override, default `cockpit/keys/vision_jwt_private.pem`), `VISION_APP_URL` (default `https://vision-a0f55.web.app/associer.html`).

### CORS

- Whitelist `VISION_ALLOWED_ORIGINS` dans `field.py` : `["https://vision-a0f55.web.app", "https://vision-a0f55.firebaseapp.com"]`. Helper `_vision_cors_response()` ajoute les headers `Access-Control-Allow-Origin` / `Vary: Origin`. Preflight OPTIONS géré explicitement sur `/field/api/vision/pair`.
- Toutes les routes `/field/*` sont accessibles sans portail d'auth Cockpit (la route `/field/api/vision/pair` en hérite).

### Sync MongoDB

- `vision_sync.py` propage `device_id` et `device_name` des docs `immatriculations` Firestore vers la collection MongoDB `vision_immatriculations` (index `device_id` ajouté).

### Constantes

- `VISION_LIEUX = ["Ouest", "Panorama", "Houx"]` dans `field.py` — à mettre à jour si on ajoute des lieux Vision (et synchroniser les options du dropdown dans `field_dispatch.html`).
