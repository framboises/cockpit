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

L'app Vision (scan billets véhicule, repo voisin `../vision`) est **JWT-gated par Field** : une tablette ne peut utiliser `associer.html` que si elle a été enrôlée dans Cockpit avec `vision_enabled = true`.

- **Pairing admin** (`POST /field/admin/pairings`) accepte `vision_enabled` (bool) + `vision_lieu` (`Ouest`|`Panorama`|`Houx`). Stocké dans `field_pairings` puis copié dans `field_devices` au pair.
- **Modification post-pairing** : `POST /field/admin/devices/<id>/vision` avec `{vision_enabled, vision_lieu}`. UI : bouton `qr_code_scanner` dans la table devices de `field_dispatch.html`, prompt JS dans `field_admin.js`.
- **Launcher tablette** : `GET /field/vision/launch` (auth `@field_token_required`) → génère un JWT RS256 `{device_id, device_name, evenement, annee, lieu, exp, iss="cockpit-field"}` avec `exp = fin événement (parametrages.data.globalHoraires.demontage.end) + 1 jour de marge`, fallback 24 h si pas de demontage défini. Redirige vers `VISION_APP_URL?t=<JWT>` (target `_blank` depuis le bouton "Vision" de `field.html`).
- **Clés RSA** : générées via `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048` dans `cockpit/keys/vision_jwt_{private,public}.pem`. La privée est dans `.gitignore` (`keys/*_private.pem`). La publique est embarquée en dur dans `vision/associer.html` (constante `VISION_JWT_PUBKEY_PEM`) — Vision valide le JWT en local sans appel réseau, mode hors ligne préservé.
- **Variable d'env** : `VISION_APP_URL` (default `https://vision-a0f55.web.app/associer.html`), `VISION_JWT_PRIVATE_KEY` (override path).
- **Sync Firestore → MongoDB** : `vision_sync.py` propage les nouveaux champs `device_id` et `device_name` des docs `immatriculations` Firestore vers la collection MongoDB `vision_immatriculations` (index `device_id` ajouté).
- **Constante** `VISION_LIEUX = ["Ouest", "Panorama", "Houx"]` dans `field.py` — à mettre à jour si on ajoute des lieux Vision.
