<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Constats terrain (`declarations.py`, page `/declarations`) — 07/10/2026

Cas d'usage : cellule d'appui prestataire qui patrouille et signale dégâts, problèmes, demandes de réparation. Ses tablettes Field sont dans un **groupe de balises « mode déclarant »** (voir `field.md`) : elles déposent des **constats**, jamais des fiches. Un opérateur habilité décide s'il en fait une fiche d'intervention.

### Données — collection `declarations`

`_id` (uuid), `ref` (« C-2026-0001 », séquence par année dans `declarations_seq`), `event` / `year` (= **événement d'appairage de la tablette**, `field.device_home_pair` : SAISON d'une année passée → SAISON courant ; à défaut `fiche_target_event`. Affiché sur la page, la tablette et le mail ; filtre « événement » de la page = `?event=&year=`, liste des événements présents dans `events` de `/api/declarations`, compteurs des onglets restreints à l'événement filtré), `created_at`, `device_id`, `device_name`, `group_id`, `group_label`, `text`, `priority` (`basse` | `normale` | `haute`), `gps {lat, lng}`, `carroye`, `photos [{photo, thumb, ts}]`, `history [{ts, by, origin: field|cockpit, kind: declaration|complement|note|status, text, photos?}]`, `status`, `status_at`, `status_by`, `fiche_id`, `converted_at/by`, `classed_reason`, `report {state}`, `client_token`.

Cycle : `nouvelle` → `en_suivi` → `transformee` (fiche liée) | `classee` (motif obligatoire, réouvrable). Côté tablette (`_field_state`) : Envoyé / Vu par le PC / Pris en charge / **Traité** (fiche liée close) / Classé. Les notes internes du PC ne sont pas montrées au déclarant.

### Routes

- Tablette (`field_token_required`, refus `403 not_declarant` hors groupe déclarant ; **CSRF exemptée sur les deux POST seulement**, dans `app.py`) : `POST /field/declarations` (multipart ou JSON, 0-5 photos, idempotent `client_token` via `field._claim_client_request` kind `decl`), `GET /field/declarations` (100 derniers), `GET /field/declarations/<id>`, `POST /field/declarations/<id>/comment` (complément texte/photo, refusé si classé).
- Cockpit (user ; page dans `PAGE_REGISTRY` id `declarations`, contrôlée par « Pages accessibles ») : `GET /declarations`, `GET /api/declarations?status=a_traiter|nouvelle|en_suivi|transformee|classee&q=&days=` (500 max, `counts` par état), `GET /api/declarations/<id>` (+ `prefill` pour l'assistant), `POST …/status`, `POST …/note`, `POST …/link {fiche_id}`.
- **Droit** : `can_convert_declaration` (« Traiter les constats terrain », Configuration > Groupes ; aucun groupe ne l'a par défaut, admin toujours) — `app._user_can_treat_declaration`. Sans lui : consultation seule. Transformer exige en plus `can_create_fiche` (vérifié par `/api/pcorg/create`).

### Page (`declarations.js`)

Liste à gauche (segments À traiter / En fiche / Classés / Tous, recherche, période), carte sur tout le reste, détail en **panneau latéral** par-dessus la carte (feuille plein écran sur téléphone, bascule Liste / Carte). Note interne = champ toujours visible en pied de panneau (Ctrl+Entrée), « Classer » = formulaire de motif en place : plus de `showPromptToast`. « **Accuser réception** » (statut `en_suivi`, ex « Prendre en suivi ») : le déclarant voit « Vu par le PC ». Photos en visionneuse avec flèches.

### Discussion PC ↔ déclarant (07/10/2026)

- **PC → déclarant** : `POST /api/declarations/<id>/message {text}` (droit traiter, refusé `409 classee` sur un constat classé). Entrée `kind: "message"` (origin cockpit, **visible du déclarant**, contrairement à `note`), `$inc field_unread`, `last_message_at`, et **push** `field.send_push_to_device` (titre « PC Organisation - constat C-… », `url=/field?constat=<id>`, `device_id` reconverti en ObjectId comme dans `field_push_subs`). Réponse `pushed` = nombre d'envois (0 : pas d'abonnement, le message reste visible à l'ouverture). Écrire à un constat `nouvelle` vaut **accusé de réception** (`en_suivi`).
- **Déclarant → PC** : le complément du constat (`/field/declarations/<id>/comment`) incrémente `cockpit_unread`. Ouvrir le constat remet à zéro `field_unread` (tablette, GET détail) / `cockpit_unread` (Cockpit, GET détail par un opérateur habilité). `counts.reponses` = constats avec réponse non lue (en-tête de la page, badge violet « Réponse » sur la carte de liste).
- **Cockpit** : constat ouvert relu toutes les 6 s (`refreshDetail`, signature statut / nb d'entrées / fiche close / non lus), re-rendu seulement s'il a changé, brouillon et focus conservés ; pas de relecture pendant un formulaire (classer, choix d'événement). Un seul champ en pied de panneau, bascule « Au déclarant » (violet, défaut) / « Note interne » (jaune). Messages et réponses en bulles dans la chronologie.
- **Tablette** : badge violet sur « Mes constats » (nombre de messages non lus, prioritaire sur le nombre de constats en cours), puce « Message du PC », vibration + toast à l'arrivée (`declNotifyNew`), conversation rafraîchie si le constat est ouvert ; le formulaire devient « Répondre au PC ». Notification touchée : `/field?constat=<id>` ouvre le constat (`state.pendingConstat`) ; app déjà ouverte : `field-sw.js` poste `field-open-url` au client avant le focus. `/field/declarations` est en réseau seul (`isLiveRequest`). `SW_VERSION` v45.

### Transformation en fiche

1. **Choix de l'événement** (`event_choices` = `EC.active_events`, SAISON toujours proposé ; défaut = événement du constat) : épreuve en cours si le sujet est traité pendant l'épreuve, SAISON s'il l'est après. Sans épreuve active : pas de question.
2. **Même assistant que l'accueil** (`pcorg.js` en mode création seule, `PcorgCreate.open({prefill, onCreated})`). Pré-rempli : position, description « C-2026-0001 - texte », urgence (`basse→IMP`, `normale→UR`, `haute→UA`), **source Externe, appelant = groupe déclarant, canal Application** (canal ajouté le 07/10/2026 à `CANAUX` de `pcorg.js` et `CANAL_LABELS` de `dispatch_auto`). L'opérateur choisit catégorie et champs. À la création, `POST /link` : constat `transformee`, fiche marquée `declaration_id` / `declaration_ref`, et une entrée de chronologie « Constat terrain C-… » portant **les photos** est ajoutée à la fiche (`PH.append_entry`). `/link` refuse une fiche créée par un autre opérateur ou déjà rattachée. La page définit `window.getCurrentEventYear` (pcorg.js le lit, d'ordinaire fourni par `main.js`) et pose `window.selectedEvent/Year` sur l'événement du constat.

### Constat par mail + résumé IA — PRÉPARÉ, À FINALISER

`declarations_report.py` : `build_constat(decl, fiche)` (données indépendantes du support), `render_email_html` (habillage ACO de `pcorg_summary_mail`), `SUMMARY_PROMPT`, config `cockpit_settings._id="declarations_report"` (`CONFIG_DEFAULTS`, `enabled: False`). Aperçu : `GET /api/declarations/<id>/report/preview` (bouton « Aperçu du constat »). **Pas branché** : `generate_summary` rend `None`, `send_constat` lève `NotImplementedError`, `POST …/report/send` → `501 a_finaliser`. À trancher : destinataires (fixes / par groupe / par catégorie), déclenchement (bouton, clôture de la fiche), photos en pièces jointes (les URL `/field/photos` exigent une session), traçage dans `declarations.report`.

Tests : `tests/test_declarations.py` (MongoDB local réel, base jetable, faux module `app`).
