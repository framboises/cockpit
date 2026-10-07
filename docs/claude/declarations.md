<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Constats terrain (`declarations.py`, page `/declarations`) — 07/10/2026

Cas d'usage : cellule d'appui prestataire qui patrouille et signale dégâts, problèmes, demandes de réparation. Ses tablettes Field sont dans un **groupe de balises « mode déclarant »** (voir `field.md`) : elles déposent des **constats**, jamais des fiches. Un opérateur habilité décide s'il en fait une fiche d'intervention.

### Données — collection `declarations`

`_id` (uuid), `ref` (« C-2026-0001 », séquence par année dans `declarations_seq`), `event` / `year` (= `field.fiche_target_event` au dépôt), `created_at`, `device_id`, `device_name`, `group_id`, `group_label`, `text`, `priority` (`basse` | `normale` | `haute`), `gps {lat, lng}`, `carroye`, `photos [{photo, thumb, ts}]`, `history [{ts, by, origin: field|cockpit, kind: declaration|complement|note|status, text, photos?}]`, `status`, `status_at`, `status_by`, `fiche_id`, `converted_at/by`, `classed_reason`, `report {state}`, `client_token`.

Cycle : `nouvelle` → `en_suivi` → `transformee` (fiche liée) | `classee` (motif obligatoire, réouvrable). Côté tablette (`_field_state`) : Envoyé / Vu par le PC / Pris en charge / **Traité** (fiche liée close) / Classé. Les notes internes du PC ne sont pas montrées au déclarant.

### Routes

- Tablette (`field_token_required`, refus `403 not_declarant` hors groupe déclarant ; **CSRF exemptée sur les deux POST seulement**, dans `app.py`) : `POST /field/declarations` (multipart ou JSON, 0-5 photos, idempotent `client_token` via `field._claim_client_request` kind `decl`), `GET /field/declarations` (100 derniers), `GET /field/declarations/<id>`, `POST /field/declarations/<id>/comment` (complément texte/photo, refusé si classé).
- Cockpit (user ; page dans `PAGE_REGISTRY` id `declarations`, contrôlée par « Pages accessibles ») : `GET /declarations`, `GET /api/declarations?status=a_traiter|nouvelle|en_suivi|transformee|classee&q=&days=` (500 max, `counts` par état), `GET /api/declarations/<id>` (+ `prefill` pour l'assistant), `POST …/status`, `POST …/note`, `POST …/link {fiche_id}`.
- **Droit** : `can_convert_declaration` (« Traiter les constats terrain », Configuration > Groupes ; aucun groupe ne l'a par défaut, admin toujours) — `app._user_can_treat_declaration`. Sans lui : consultation seule. Transformer exige en plus `can_create_fiche` (vérifié par `/api/pcorg/create`).

### Transformation en fiche

Bouton « Transformer en fiche » → **même assistant que l'accueil** (`pcorg.js` en mode création seule, `PcorgCreate.open({prefill: {lat, lon, text, urgency}, onCreated})`). Pré-rempli : position, description « C-2026-0001 - texte », urgence (`basse→IMP`, `normale→UR`, `haute→UA`) ; l'opérateur choisit catégorie et champs. À la création, `POST /link` : constat `transformee`, fiche marquée `declaration_id` / `declaration_ref`, et une entrée de chronologie « Constat terrain C-… » portant **les photos** est ajoutée à la fiche (`PH.append_entry`). `/link` refuse une fiche créée par un autre opérateur ou déjà rattachée. La page définit `window.getCurrentEventYear` (pcorg.js le lit, d'ordinaire fourni par `main.js`) et pose `window.selectedEvent/Year` sur l'événement du constat.

### Constat par mail + résumé IA — PRÉPARÉ, À FINALISER

`declarations_report.py` : `build_constat(decl, fiche)` (données indépendantes du support), `render_email_html` (habillage ACO de `pcorg_summary_mail`), `SUMMARY_PROMPT`, config `cockpit_settings._id="declarations_report"` (`CONFIG_DEFAULTS`, `enabled: False`). Aperçu : `GET /api/declarations/<id>/report/preview` (bouton « Aperçu du constat »). **Pas branché** : `generate_summary` rend `None`, `send_constat` lève `NotImplementedError`, `POST …/report/send` → `501 a_finaliser`. À trancher : destinataires (fixes / par groupe / par catégorie), déclenchement (bouton, clôture de la fiche), photos en pièces jointes (les URL `/field/photos` exigent une session), traçage dans `declarations.report`.

Tests : `tests/test_declarations.py` (MongoDB local réel, base jetable, faux module `app`).
