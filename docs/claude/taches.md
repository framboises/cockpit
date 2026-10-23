<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Taches de preparation des vignettes (timeline_tasks.py, 23/10/2026)

**Source de verite : collection `timeline_tasks`**, une tache par document (schema en tete de `timeline_tasks.py`). La chaine `vignette.todo` du document timetable n'est plus qu'une VUE CALCULEE, reecrite a chaque changement (`refresh_vignette_cache`, `apply_merge`) : montre, Alfred, rapports IA et onglets anciens la lisent toujours. Ne jamais ecrire `todo` directement : passer par les fonctions du module.

### Modeles (collection `todos`, page `/config/todos`)

- Item : `{id, text, phase, control, critical, due_offset_min, origin}` (`normalize_template_items`). L'`id` rattache les taches deja creees a l'item : l'editeur (`todo_sets.js`) le renvoie tel quel, une reformulation garde l'etat et l'auteur. Item sans id : id deterministe `template_item_id(type, texte)`, ecrit par `ensure_template_ids` (merge et liste des modeles).
- `due_offset_min` : minutes AVANT l'heure de reference de la vignette (debut, sinon fin pour les fermetures : `due_base`). Vide = a l'heure. Sert au retard (`due_at`, heure de Paris `YYYY-MM-DDTHH:MM`).
- `origin` : `retex`, `ia`, `historique` (ajout accepte depuis le panneau d'analyse).

### Merge (`merge.py`)

- `_attach_todos_str` pose `_task_tpl` (items retenus, `[]` si le type est connu sans tache pour la phase) ; `update_timetable_document` le RETIRE avant patch (sinon il serait persiste) et le passe a `apply_merge`.
- Regles de `plan_template_sync` : taches manuelles JAMAIS supprimees (bug corrige : un ajout d'operateur disparaissait au merge) ; item retire du modele -> tache supprimee si encore a faire, conservee et detachee si traitee ; tache supprimee par un operateur (`deleted`) jamais recreee ; texte modifie a la main (`text_overridden`) respecte ; ajout manuel au texte identique a un nouvel item -> rattache, pas de doublon.
- Repli : si `timeline_tasks` echoue, `_merge_todos` fusionne les chaines en gardant les lignes absentes du modele.

### Reprise (migration paresseuse)

`ensure_migrated` : premiere lecture `/timetable`, premier merge ou premiere ecriture d'un document timetable. Drapeau `tasks_migrated_at` pose AVANT la conversion (update conditionnel, pas de double reprise), retire si la reprise echoue. Le champ `todo` historique est une chaine OU une liste (vieux documents 2025) : `parse_todo_str` gere les deux.

### Ecritures

- Routes `/api/timeline-tasks` (role user) : `POST` (ajout, `reco_ref` optionnel), `POST /<id>/status` (`todo|done|na|blocked`, motif obligatoire pour `blocked`), `PATCH /<id>` (texte, note, critique), `DELETE /<id>` (suppression douce), `GET /history`, `GET /by-reco` (manager). Auteur et heure traces (`done_by_name`, `status_by_name`, `history` 30 dernieres).
- `/update_timetable_event` et `/add_timetable_event` acceptent `tasks` + `tasks_known` (modale) ou l'ancienne chaine `todo` (`editor_payload`). Suppressions limitees a `tasks_known` (ids affiches a l'ouverture) : un onglet ouvert avant un merge ne supprime pas les taches creees entre-temps. Ancienne chaine : appariement par texte, aucune suppression.
- Statut de preparation deduit des taches quand elles changent (`prep_from_tasks` : toutes traitees -> `true`, une traitee ou bloquee -> `progress`, sinon `non`). "Sans objet" compte comme traitee.
- Suppression / duplication de vignette : `delete_for_vignette` / `copy_tasks` (copie a faire, manuelle).

### IA

- Recommandation -> tache : bouton "Tache" sur chaque puce de la section Recommandations (`ai_assistant.js`), `reco_ref {summary_id, bullet_index}`. Tache faite -> `set_recommendation_status(..., "applied")`.
- Contexte : `_serialize_upcoming` ajoute `taches {faites, total, a_faire}` (cumul sur les vignettes factorisees) -> rapport pcorg, briefing (`timetable`), Alfred (`t_timeline`). Briefing : source `taches` (`open_and_late` : en retard, bloquees avec motif, critiques a venir).
- Suggestions sur un modele : `POST /api/todo-sets/<id>/ai-suggest` (admin, facture, `record_ai_usage` feature `taches_modeles`), contrat une ligne par proposition `AJOUTER|RETIRER|MODIFIER|AVANCER`, ids verifies (`parse_suggestions`).

### Historique entre editions

- `match_key` = activite + lieu normalises : `vignette_history` regroupe le meme creneau sur TOUS les evenements (un parking garde ses problemes quel que soit l'evenement) et les autres jours de l'edition. Affiche dans le tiroir.
- `template_insights` (panneau "Analyse" de `/config/todos`) : taux par edition, par item, ajouts terrain recurrents (>= 2 editions ou >= 3 vignettes, ou issus d'une reco IA), items rarement faits / souvent en retard, puces RETEX (`retex_recommendations`, admin). ⚠️ Une edition avec moins de 10 % (min. 3) de taches traitees pour le type est "non suivie" : exclue des stats par item (avant 10/2026 presque rien n'etait coche, sinon tout paraitrait inutile).

### Front (`timeline.js`)

`tasksOf(item)` (repli sur la chaine si `item.tasks` absent), `taskIsLate` sur l'horloge de la timeline (simulation comprise), `_tlRefreshCardTasks` (re-rendu liste + compteur + pastille ; `_tlTick` le declenche quand une tache passe en retard). "Prete" posee a la main ne coche plus artificiellement les cases.
