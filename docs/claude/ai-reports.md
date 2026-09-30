<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Briefing de situation et RETEX de fin d'édition (`ai_reports.py`)

UI `static/js/ai_reports.js` (modales injectées), boutons sidebar `.air-sidebar-btn` `data-air-open="briefing|retex"` — surtout PAS `.sidebar-ai`, que `ai_assistant.js` capture.

- **Briefing** (`situation_briefing.py`, manager) : `build_context` lit 7 sources indépendantes (main courante, alertes, présents, trafic, météo, Field, timetable) ; une source en échec est marquée `indisponible`, jamais bloquante (~1 s). `{llm:false}` = contexte brut gratuit ; `{llm:true}` = job, contrat 5 clés (`situation, points_chauds, a_surveiller_6h, ressources, consignes_releve`). ⚠️ Un relevé compteur > 15 min est `releve_perime`, jamais « 0 présent ».
- **Relève automatique** : `cockpit_settings._id="briefing_releve"` `{enabled, times, recipients}` ; tâche `scripts/install_briefing_task.ps1` (toutes les 15 min, **non installée**). Test sans coût : `python scripts/briefing_releve.py --force --no-llm --dry-run`.
- **RETEX** (`edition_retex.py`, admin) : jeu de données complet d'une édition (< 2 s), 2 éditions précédentes, avertissements de comparabilité transmis au modèle. Collection `edition_retex` versionnée ; `GET /api/ai/retex/<id>/html` = page imprimable. ⚠️ `cockpit_alert_history` a un TTL de 7 j : au-delà, bloc alertes `indisponible`, jamais « 0 alerte ».
