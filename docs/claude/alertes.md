<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

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
- `build_context` (01/10/2026) : délègue à `event_courant.active_events` (épreuve active de montage.start à demontage.end, priorité choisie par un admin entre épreuves simultanées, sinon celle déjà en cours ; hors épreuve **SAISON**/<année civile>). Le contexte porte `event`, `year`, `event_kind` (`epreuve`|`saison`) et `phase` ; `globalHoraires` seulement pour une épreuve. Fini le « premier paramétrage dont les dates contiennent aujourd'hui » (dépendant de l'ordre Mongo) et le repli à 7 jours. ⚠️ Une épreuve à fenêtre aberrante (> `MAX_WINDOW_DAYS`) est ignorée : ses horaires d'ouverture ne sonnent pas tant que la saisie n'est pas corrigée.
- Sur SAISON : détecteurs ouverture/fermeture **OFF** (garde explicite `event_kind == "saison"`) et saturation porte **skip** (sauf rejeu `door_tx_source`), même si `___GLOBAL___.live_controle_actif` est resté à true.
- `detect_pcorg_urgency` tague l'alerte avec l'event/year **de la fiche** (épreuve secondaire active, SAISON), le contexte en repli. La montre et le bandeau ne filtrent toujours pas par événement (watch_state.read_active_alerts).

### Aperçu

Bouton « Aperçu » de la modale admin : `CockpitAlerts.preview(def)` affiche le rendu réel sur le poste de l'admin, sans rien écrire (ni alerte, ni historique, ni WhatsApp). À utiliser pour toute nouvelle définition.

### Explication IA d'une alerte (`alert_ai.py`)

`POST /api/alerts/<id>/explain` (user, CSRF actif) : alerte active, archivée ou historisée. Contexte propre au type (trafic : Waze autour des pins + relevés −90/+60 min ; main courante : fiche, chronologie, fiches même zone/catégorie 60 min ; météo ; checkpoint ; SOS : tablettes les plus proches ; caméra ; saturation porte), UN appel Claude (700 tokens, effort low, JSON 5 clés : `contexte, cause_probable, evolution, action_suggeree, confiance`, « donnée insuffisante » imposée). Cache `alert_explanations` (TTL 7 j) : clics simultanés → 202 `en_cours` ; `?refresh=1` refusé avant 2 min. UI `CockpitAlerts.explainWidget`, hors rangée de boutons (n'empêche jamais l'acquittement ni la prise en compte) ; jamais d'appel en aperçu admin.

⚠️ **Désactivé depuis le 30/09/2026** : le bouton « Expliquer » est retiré de l'alerte plein écran (`alert_poller.js`) et de l'historique (`main.js`), et la route répond **410 `desactive`** sans appeler le modèle (un onglet resté sur l'ancien JS ne peut plus consommer). Motif : un appel payant par clic d'opérateur. `ALERT_AI_EXPLAIN=1` réactive la route ; `explainWidget` existe toujours pour la rebrancher.

### Saturation porte prévue (`door_saturation_forecast`)

Source live : `hsh_transactions_agg` (5 min par tripode/PDA, `gate_name`) ; ⚠️ `tranche` = heure de Paris étiquetée UTC. Aucune capacité en base : capacité = somme des appareils actifs sur 1 h (tripode 900/h, PDA 650/h = p99 mesuré 2026), surcharge `params.capacities`. Prévision = débit 15 min × profil N-1 de la même porte aligné au jour de course (décalage arrondi à la semaine). Série horaire N-1 interpolée entre milieux d'heure (lue en marches, elle faisait sonner à HH:05).
Rejeu 2026 (5 éditions) : 33 alertes « constaté » toutes vraies ; 17 prévisions N-1, 5 confirmées (erreur médiane 21-31 %). La tendance seule (`trend_fallback`) est désactivée par défaut (1 sur 4 confirmée). Définition prod à créer désactivée, en mode `banner`. Mieux attendu en 2027 (archive 5 min comme N-1).

**Banc de rejeu** : `scripts/replay_door_saturation.py` (lecture seule, archives `hsh_archive_tx_*_2026` en mémoire, ~20 s par édition). Rejoue le détecteur lui-même et le confronte à trois références : débit ≥ seuil (L1, auto-référence), apparition d'un PDA sur une porte à tripodes (L2), fiche main courante de flux sur la porte (L3, titre seul, prises de service / bagarres / nuisances exclues — vérifié à la main, 73 fiches sur 6 éditions). Options `--capacity apprise` (facteur par porte appris en validation croisée, paramètre `capacity_factors` du détecteur), `--consec`, `--threshold`, `--horizon`, `--print-factors`, `--json`.

⚠️ **Constat du 30/09/2026 : la capacité de SCAN n'est pas le goulet.** Les demandes « Renfort filtrage / fouille » arrivent quand la porte ne débite que **15 % de sa capacité théorique de scan** (médiane, q75 32 %). Le goulet est la palpation en amont des tripodes : le détecteur actuel (débit vs capacité des appareils) ne peut structurellement pas les voir — rappel des fiches 3 %, et 13 % de précision même en capacité apprise. Pluie : 2 % du temps chargé en 2026, aucun effet mesurable (échantillon insuffisant).

**Capacité de sécurité** (`door_security.py`) : agents prévus × **350 personnes/h/agent** (valeur terrain). Poste → porte lu dans **`bible`** (application BIBLER : `post.metier == "SECURITE"`, `post.zone == "PORTES"`, piéton par l'affectation ou `fiche.positioning` « Couloir de contrôle avant le contrôle du billet »), rapproché des noms de portes du contrôle d'accès par mots distinctifs (`match_gate`) ; agents par créneau de 30 min lus dans `calendrier_<année>_<événement>` (`shiftcode` = numéro de poste).

⚠️ **La numérotation des postes varie d'un événement à l'autre** (8790 = « Porte Karting piétons BMW » aux 24H AUTOS, « Porte CIK Bis » aux 24H CAMIONS) : toujours lire la bible de l'édition, jamais une table codée. ⚠️ Sans le filtre `zone == PORTES`, « Paddock Nord - Accès piétons » (8164) tombait sur la Porte Nord et « Porte Tertre Rouge Aire d'Accueil » (8501) sur la porte publique.

Rejeu 2026 : à la fiche de flux, la porte débite **101 %** de sa capacité de sécurité (médiane ; 77 % 30 min avant) contre 15 % de sa capacité de scan. Un seuil à 60 % retrouve 50 % des fiches (3 % avec la capacité de scan). Précision mesurée ~11 % : borne basse, toute saturation ne donne pas lieu à une fiche. Porte Nord piétons est au-dessus de sa capacité prévue 58 % du temps chargé (29 fiches de renfort sur l'année).

**Mode `capacity_mode: "securite"` du détecteur** : capacité = agents prévus × `agent_rate_h` (350), **au créneau prévu** (une fin de vacation fait baisser la capacité alors que le flux monte : l'alerte l'anticipe sans hausse de flux). Seules les portes piétonnes dotées d'agents sont surveillées ; sans bible ni planning pour l'édition, le détecteur **s'abstient** (jamais de repli sur la capacité de scan). Titre « RENFORT SECURITE PORTE », message « PORTE EST : ~1800/h des maintenant pour 4 agents securite (1400/h) - prevoir +2 agents » (`agents_needed` = ⌈débit / 350⌉ − agents prévus). Bible + planning relus au plus toutes les 10 min (`_DOOR_SECU_CACHE`, horloge **réelle** : sur l'heure simulée, le rejeu rechargeait tout tous les deux cycles).

Une alerte **par épisode** : `renotify_min` 120, `renotify_on_worse` false. Rejeu 2026, seuil 100 %, horizon 30 min :

| Réglage | Alertes (6 éd.) | Fiches anticipées | Avance médiane | Alertes suivies renfort PDA ou fiche |
|---|---|---|---|---|
| capacité de scan (historique) | 53 | 3 % | — | 8 % |
| sécurité, dédup 30 min | 960 | 67 % | 54 min | 22 % |
| sécurité, épisode 2 h + aggravation | 461 | 64 % | 36 min | 25 % |
| **sécurité, épisode 2 h (défaut)** | **316** | **59 %** | **26 min** | **23 %** |
| sécurité, épisode 3 h | 236 | 49 % | 34 min | 23 % |

⚠️ Les aggravations annonçaient des besoins peu crédibles (+10 à +23 agents) : là, le planning ne reflète plus l'effectif réel (renforts devenus permanents, Porte Nord). La précision « suivie d'une fiche » est une borne basse. Défaut du code : `capacity_mode: "scan"` (compatibilité) ; l'admin propose `securite` à la création.
