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
- `build_context` : repli sur le paramétrage le plus proche à 7 jours (l'ancien « repli » refaisait le même test).

### Aperçu

Bouton « Aperçu » de la modale admin : `CockpitAlerts.preview(def)` affiche le rendu réel sur le poste de l'admin, sans rien écrire (ni alerte, ni historique, ni WhatsApp). À utiliser pour toute nouvelle définition.

### Explication IA d'une alerte (`alert_ai.py`)

`POST /api/alerts/<id>/explain` (user, CSRF actif) : alerte active, archivée ou historisée. Contexte propre au type (trafic : Waze autour des pins + relevés −90/+60 min ; main courante : fiche, chronologie, fiches même zone/catégorie 60 min ; météo ; checkpoint ; SOS : tablettes les plus proches ; caméra ; saturation porte), UN appel Claude (700 tokens, effort low, JSON 5 clés : `contexte, cause_probable, evolution, action_suggeree, confiance`, « donnée insuffisante » imposée). Cache `alert_explanations` (TTL 7 j) : clics simultanés → 202 `en_cours` ; `?refresh=1` refusé avant 2 min. UI `CockpitAlerts.explainWidget`, hors rangée de boutons (n'empêche jamais l'acquittement ni la prise en compte) ; jamais d'appel en aperçu admin.

### Saturation porte prévue (`door_saturation_forecast`)

Source live : `hsh_transactions_agg` (5 min par tripode/PDA, `gate_name`) ; ⚠️ `tranche` = heure de Paris étiquetée UTC. Aucune capacité en base : capacité = somme des appareils actifs sur 1 h (tripode 900/h, PDA 650/h = p99 mesuré 2026), surcharge `params.capacities`. Prévision = débit 15 min × profil N-1 de la même porte aligné au jour de course (décalage arrondi à la semaine). Série horaire N-1 interpolée entre milieux d'heure (lue en marches, elle faisait sonner à HH:05).
Rejeu 2026 (5 éditions) : 33 alertes « constaté » toutes vraies ; 17 prévisions N-1, 5 confirmées (erreur médiane 21-31 %). La tendance seule (`trend_fallback`) est désactivée par défaut (1 sur 4 confirmée). Définition prod à créer désactivée, en mode `banner`. Mieux attendu en 2027 (archive 5 min comme N-1).
