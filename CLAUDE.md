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
