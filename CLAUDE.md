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
| `TITAN_JWKS_URL` | JWKS EdDSA du portail TITAN Home v2 (`decoder_jeton_portail` de `app.py` : EdDSA par le JWKS, HS256 par `JWT_SECRET`, puis révocation partagée `titan_shared`). JWKS injoignable : jetons EdDSA refusés, HS256 acceptés | `http://127.0.0.1:8080/.well-known/jwks.json` |
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
| `VALHALLA_ADMIN_URL` | Service valhalla-admin (saisons, épreuves, chargement des scénarios) | même hôte que `VALHALLA_URL`, port `8003` |
| `VALHALLA_ADMIN_API_KEY` | Clé `X-Api-Key` du service valhalla-admin (`VALHALLA_ADMIN_API_KEY` du `.env` du serveur Valhalla) | — (page `/admin/itineraire` inerte, calculs inchangés) |
| `WAZE_FEED_USER` / `WAZE_FEED_PASSWORD` | Basic auth du flux de fermetures relu par Waze (`/public/waze/closures.xml`), à déclarer dans le Partner Hub | — (flux en 503) |
| `WAZE_GEOCODE_TOKEN` | Jeton Reverse Geocoding Waze (Partner Hub > Toolbox > Partner feed) pour vérifier les noms de rue | — (vérification indisponible) |
| `VALHALLA_ADMIN_TIMEOUT_SECONDS` | Timeout des appels valhalla-admin (60 s minimum pour `/features`) | `15` |
| `ROUTING_WAZE_MAX_AGE_MIN` | Ancienneté max des alertes Waze prises en compte pour les pénalités | `30` |
| `CLAUDE_EFFORT` | `output_config.effort` des rapports IA (modèles qui le supportent) | `medium` |
| `RETEX_MAX_TOKENS` | `max_tokens` du RETEX de fin d'édition | `24000` |
| `PCA_CREATE_TEXT_INDEX` | `0` interdit la création à la volée de l'index texte `pca_text` sur `pcorg` | `1` |
| `WAHA_WEBHOOK_SECRET` | Secret HMAC des webhooks WAHA (`/api/wa/webhook`, SHA-512 attendu, SHA-1 tolérée avec avertissement) | — (vide : bypass en dev, **refus de tous les webhooks en prod**) |
| `COCKPIT_INTEG_<ID>_SECRET` | Secret HMAC partagé avec une application liée (`integrations.py`, ex. `COCKPIT_INTEG_FRIDAY_SECRET`), 32 caractères min. | — (liaison inactive si absent) |
| `COCKPIT_INTEG_ALLOWED_HOSTS` | Hôtes autorisés pour les envois vers les applications liées (virgules, ex. `friday.lemans.org`) ; https obligatoire, jamais loopback / link-local (anti-SSRF) | — (aucun envoi possible si vide) |
| `COCKPIT_INTEG_<ID>_ALLOWED_IPS` | IP sources admises (adresses ou CIDR, virgules) sur l'entrant et les photos signées d'une application liée, nom dérivé de son `secret_env` (ex. `COCKPIT_INTEG_FRIDAY_ALLOWED_IPS=192.168.254.53`) | — (**tout refusé** si vide) |
| `COCKPIT_PUBLIC_URL` | Adresse de Cockpit vue des applications liées (liens signés des photos) | `PUBLIC_BASE_URL` |
| `OLLAMA_URL` | URL Ollama (résumés périodiques Alfred) | `http://srv-safe-docker.aco.local:11434` |
| `OLLAMA_MODEL` | Modèle Ollama des résumés | `alfred` |
| `OLLAMA_TIMEOUT` | Timeout lecture d'un résumé (s) | `300` |
| `OLLAMA_MAX_MESSAGES` | Nombre max de messages injectés dans le prompt de résumé | `400` |
| `ALFRED_ASK_URL` | Wrapper tool-calling Alfred sur la VM (mentions) | `http://srv-safe-docker.aco.local:5005/alfred/ask` |
| `ALFRED_ASK_SECRET` | Secret HMAC-SHA256 partagé avec le wrapper | — (mentions désactivées si vide) |
| `ALFRED_ASK_TIMEOUT` | Timeout d'une question au wrapper (s) | `90` |
| `ALFRED_FOLLOWUP_SECONDS` | Fenêtre de suite de conversation sans nouveau @alfred | `420` |
| `ALFRED_DM_REFUSAL_COOLDOWN` | Délai min entre deux refus DM au même contact (s) | `3600` |
| `ALFRED_TOOLS_SECRET` | Secret HMAC des appels VM → Cockpit (`/api/alfred-tools/*`) et du jeton de portée du chat. Distinct d'`ALFRED_ASK_SECRET` | — (vide : outils refusés, chat sans portée) |
| `ALFRED_TOOLS_UNSCOPED_SECRET` | Secret HMAC DISTINCT donnant la vue PC Org complète aux outils sans jeton de portée (chemin WhatsApp du wrapper). Ignoré s'il est égal à `ALFRED_TOOLS_SECRET` | — (vide : tout appel sans portée refusé) |
| `ALFRED_CHAT_CONTEXT_MODE` | Contexte du chat vers le wrapper : `prefix` (ligne en tête du message), `field` (`channel` + `context`), `both` | `prefix` |
| `ALFRED_CHAT_TIMEOUT` | Attente de la réponse du wrapper pour le chat (s), au-delà de son global de 180 s | `190` |
| `ALFRED_CHAT_MAX_CONCURRENT` | Appels simultanés du chat vers la VM | `2` |
| `ALFRED_CHAT_QUEUE_WAIT_S` | Attente max d'une place avant « Alfred très sollicité » (s) | `60` |
| `ALFRED_CHAT_HISTORY` | Messages de la conversation envoyés au wrapper | `10` |
| `ALFRED_CHAT_MAX_TOOL_HOPS` | `max_tool_hops` envoyé par le chat | `6` |
| `ALFRED_CHAT_RATE_10MIN` | Questions max par opérateur sur 10 min | `40` |
| `ALFRED_CHAT_RETENTION_DAYS` | Rétention (TTL) conversations, messages, appels d'outils | `90` |
| `COCKPIT_CAMERA_KEY` | Cle maitre (32 octets base64) des mots de passe cameras chiffres (`camera_secrets.py`). Variable utilisateur du compte de Cockpit | — (identifiants et pilotage cameras indisponibles) |
| `ALFRED_HEALTH_URL` | Sonde de santé du wrapper | dérivée d'`ALFRED_ASK_URL` (`/alfred/health`) |
| `BALISES_INGEST_SECRET` | Secret HMAC partagé avec le récepteur Teltonika (`/api/balises/ingest`, `/api/balises/whitelist`), 32 caractères min. Aussi dans l'environnement du service `BalisesRecepteur` | — (ingestion refusée, 503) |
| `BALISES_INGEST_ALLOWED_IPS` | IP sources admises sur l'ingestion des balises | `127.0.0.1,::1` |
| `FLESPI_TOKEN` | Jeton flespi de la relève transitoire (`flespi_poller.py`), environnement du compte de la tâche | — (relève flespi en erreur) |

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
- Afficheurs TV du portail (compte `type_compte == "borne"` + `afficheur_id`, guide home 05 §10) : admis en lecture au-dessus de user sur les seuls chemins exacts de `PAGES_AFFICHEUR` (`app.py`) ; un compte borne est plafonné à user partout et n'écrit jamais (`_refuser_ecriture_borne`, toutes routes) ; `/api/active-alerts` lui rend `[]`. Mode cadre : CSP `frame-ancestors 'self' https://safe.lemans.org` sur ces pages (accueil seulement avec `?tv=1`). Une nouvelle page TV = l'ajouter à `PAGES_AFFICHEUR` avec ses API de lecture (un afficheur n'est pas soumis aux pages autorisées par groupe sur ces chemins, `_enforce_page_access`)

### Patterns frontend

- État global : `window.selectedEvent`, `window.selectedYear`
- Appels API via `apiPost()` avec headers CSRF
- Simulation timeline en console : `TimelineClock.setSim("2025-09-26 14:35")`, `.play()`, `.setSpeed(5)`

## Règles strictes

- **JAMAIS de guillemets typographiques** dans le code JS/CSS/Python. Utiliser uniquement les apostrophes droites `'` et guillemets droits `"`. Les curly quotes `'`, `'`, `"`, `"` provoquent des SyntaxError silencieuses.

## Documentation par domaine

Le detail de chaque domaine vit dans `docs/claude/`. **Lire le fichier concerne AVANT de modifier le code du domaine** : il porte les pieges verifies, les conventions de donnees et les regles non negociables (PMV notamment).

- `docs/claude/timeline.md` : Timeline v2 de l'accueil (vignettes compactes par heure, passe replie, indicateurs de jour des epreuves via la mecanique SAISON, recherche derriere une loupe, mode TV, gabarit en cache)
- `docs/claude/taches.md` : Taches de preparation des vignettes (collection timeline_tasks, chaine `todo` = vue calculee, merge qui ne touche jamais les ajouts manuels, tracabilite, recommandation IA -> tache, historique entre editions, analyse et suggestions IA sur les modeles)
- `docs/claude/waze.md` : Publication des fermetures du moteur d'itineraire vers Waze for Cities (flux CIFS public en Basic auth, horaires, desynchronisation avec les regles, jamais de flux vide sur panne)
- `docs/claude/vision.md` : Integration Vision (tablettes scan billets vehicule, JWT RS256, CORS, sync Firestore facturee)
- `docs/claude/ia-resume-pcorg.md` : Assistant IA : resume de periode pcorg, rapport matinal, couts et budget IA, prompt caching
- `docs/claude/crise.md` : Exercices de crise : auth PIN animateur, sessions JWT, anti-bruteforce
- `docs/claude/routing.md` : Routing Valhalla : 4 modes (normal, piste, god, god + piste) et leurs recettes, regles saison / epreuve du service valhalla-admin, page /admin/itineraire (chargement des scenarios), acces piste par groupe de balises
- `docs/claude/affluence.md` : Affluence previsionnelle : panier ticketing, ventes_prev vs ventes_prev_final, projections
- `docs/claude/scans.md` : Chaine scans : import Excel, historique_controle, rapport HTML, vue Frequentation, analyse Claude
- `docs/claude/presents.md` : Presents sur site (presents_etat.py) : remises a zero, pics, serie N-1
- `docs/claude/montre.md` : Montre Garmin (cote serveur et pieges Connect IQ) : trafic_etat, meteo_etat, payload, guidage
- `docs/claude/main-courante.md` : Main courante pcorg : pcorg_history, fusion SQL/Cockpit, droits, CSRF d'onglet ancien, aide a la saisie (pcorg_assist)
- `docs/claude/field.md` : Tablettes Field : categories, dispatch automatique, idempotence, statuts hors ligne, SOS
- `docs/claude/alertes.md` : Centrale d'alerte : rendu pilote par la definition, display_mode, explication IA, saturation porte
- `docs/claude/alfred.md` : Alfred (agent IA WhatsApp) : webhook WAHA, breaker, resumes Ollama ; chat Alfred des operateurs (widget, outils exposes a la VM, jeton de portee). Brief du wrapper VM : `docs/alfred-vm-brief.md`
- `docs/claude/ai-reports.md` : Briefing de situation et RETEX de fin d'edition (ai_reports.py)
- `docs/claude/frequentation-live.md` : Frequentation depuis l'archive du controle d'acces live (live_frequentation.py)
- `docs/claude/momentus.md` : Momentus Elite : synchro, reservations par lieu ; contient aussi le rapport de scans depuis l'archive live (live_scan_units.py)
- `docs/claude/saison-indicateurs.md` : Indicateurs SAISON de la barre des jours (visites, pistes utilisees, blackouts comptes, config globale admin)
- `docs/claude/voisins.md` : Evenements voisins (Antares, MSB, Le Mans FC) : sources publiques, voisins_events, timeline SAISON, indicateurs du calendrier
- `docs/claude/musee.md` : Musee des 24 Heures : bloc autonome de l'accueil, collecte HSH dediee (musee_collect.py), visiteurs du jour (compteurs entree seule), PDA mobile, configuration dans /live-controle onglet Musee (horaires par jour, perimetre HSH, aucune valeur en dur)
- `docs/claude/declarations.md` : Constats terrain des tablettes Field en mode declarant (page /declarations, transformation en fiche, droit can_convert_declaration, envoi mail + resume IA prepare)
- `docs/claude/integrations.md` : Applications liees (Friday du service informatique...) : regles d'envoi par categorie / sous-classification, file signee HMAC, evenements entrants, JAMAIS de cloture externe. Note pour le service informatique : `docs/integrations/friday-note-service-informatique.md`
- `docs/claude/portes.md` : Portes : page operateur /doors (direct, rejeu, bilan RETEX, analyse d'une porte) et mur TV /portes-mur (portes_etat.py) : mode entree / mixte / sortie sur 15 min, capacite par sens (securite en entree, appareils en sortie), passages valides, N-1 par sens, aucune prevision sur un N-1 tous sens
- `docs/claude/pmv.md` : PMV (remorques Sigma 3000, JetFileII) : regles de dialogue NON NEGOCIABLES, securite, jobs
- `docs/claude/cameras.md` : Cameras : page de configuration unique (appareils, canaux, usages LAPI / detection), profils d'identifiants chiffres (COCKPIT_CAMERA_KEY), import SeeTec, etat reseau
- `docs/claude/operateurs.md` : Prise de poste operateur (groupe Operateur, zones = couche Looker lue en direct, metiers = Bibler, fiches de poste en main courante, pause declaree jamais deduite, ciblage des alertes par zone / metier)
- `docs/claude/detection.md` : Page IA (/detection) et mur TV des intrusions (/intrusions-mur, detection.etat_mur) : intrusions (fielddetection) du recepteur Hik (episodes, photos 1 an, verification IA locale GPU, avis et cadres des operateurs), carte et zones, filtres de la centrale d'alertes ; Trafic, Foule et Secours / personnes au sol (Capture IA, tache planifiee capture_ia.py) ; Galerie d'entrainement
- `docs/claude/balises.md` : Balises GPS : groupes communs (anoloc_config.beacon_groups), sources Anoloc et Teltonika (recepteur TCP maison Codec 8/8E sous svc-balises, releve flespi transitoire), ingestion idempotente, regle en ligne/GPS par source, TTL 10 mois des positions, alertes balises de la centrale (vitesse, zones, arret, muette, batterie, mouvement)
