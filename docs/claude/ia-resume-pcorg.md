<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Assistant IA — résumé de période des fiches PC Organisation

Sur la sidebar de `index.html`, `edit.html`, `analyse_ops.html`, le bouton **« Assistant IA »** (classe `.sidebar-ai`) ouvre une modale qui génère un compte-rendu structuré d'une période sur la collection `pcorg`. Réservé au rôle **manager** (et au-dessus).

### Architecture

- **Module Python** : `pcorg_summary.py` — helpers purs (`compute_kpis`, `select_fiches_for_prompt`, `build_prompts`, `call_claude`, `save_summary`, `list_summaries`, `get_summary`, `delete_summary`, `generate_period_summary`). Appel HTTP direct à `https://api.anthropic.com/v1/messages` (pas de SDK `anthropic`), pattern calqué sur `traffic.py` (Waze).
- **Routes** dans `app.py` (à côté des routes `/api/pcorg/*`) :
  - `POST /api/pcorg/summary/generate` (`manager`) — body `{event, year, period_start, period_end, model?, dry_run?}` (ISO, datetime-local accepté → interprété en Europe/Paris). Court-circuite l'appel Claude si `kpis.total == 0` (sections "RAS"). `model` accepte une whitelist (`claude-sonnet-5-5`, `claude-sonnet-5`, `claude-sonnet-4-6`, `claude-sonnet-4-5`, `claude-opus-4-7`, `claude-opus-4-6`, `claude-haiku-4-5`) sinon fallback `CLAUDE_MODEL`. `dry_run=true` retourne le prompt assemblé sans appeler Claude (itération rapide sans coût).
  - `GET /api/pcorg/summary/list?event=&year=` (`manager`) — liste légère (sans `kpis`/`sections`).
  - `GET /api/pcorg/summary/<id>` (`manager`) — détail complet.
  - `DELETE /api/pcorg/summary/<id>` (`admin`).
  - `GET /api/pcorg/summary/usage?from=&to=&event=&year=` (`admin`) — agrège `input_tokens`/`output_tokens`/cache tokens de `pcorg_summaries` + `pcorg_n1_retros`, calcule un coût USD approximatif via `MODEL_PRICING_USD_PER_MTOK` (cache création +25 % input, lecture cache -90 % input). Tarifs figés en code, à mettre à jour si Anthropic révise.
- **Frontend** : `static/js/ai_assistant.js` (IIFE autonome). Les templates exposent `window.__userIsManager` à côté de `window.__userIsAdmin` ; le JS bloque l'ouverture de la modale aux non-managers (en plus du backend).
- **Pipeline parallélisé** : `generate_period_summary` lance `compute_kpis` / `compute_comparisons` / `get_upcoming_timetable` / `compute_attendance_block` / `compute_door_reinforcement` / `select_fiches_for_prompt` dans un `ThreadPoolExecutor`, puis kicke `get_or_build_n1_retrospective` (1er appel Claude) dès que `comparisons` est dispo — il tourne en parallèle du reste. Gain ~5-10 s.
- **Bloc Billetterie & Fréquentation** (`compute_attendance_block`) : 3 slots `yesterday/today/tomorrow` injectés dans le user prompt. Chaque slot contient `billets_vendus`, `pic_observed` + `pic_observed_hour` (heure 'HHhMM' du pic constaté, jours passés uniquement, source : `historique_controle.frequentation` → `data_access` → archive), `pic_prev` + `pic_prev_hour` (pic et heure de l'édition précédente, jour-équivalent aligné sur la date de course), `pic_projection` (pic projeté = `pic_prev * billets_N / billets_prev`), `delta_pct_vs_prev`. Le system prompt impose à Claude d'inclure dans la `synthese` (a) le pic constaté de la veille avec heure et delta, (b) le pic projeté du jour avec l'heure approximative attendue (= `pic_prev_hour`).
- **Prompt caching** : le system prompt des 2 appels Claude est marqué `cache_control: ephemeral` (cache 5 min côté Anthropic). Sur les appels rapprochés (rapport matinal quotidien notamment), le system n'est plus refacturé. `usage` enregistre `cache_creation_input_tokens` / `cache_read_input_tokens` pour la télémétrie.
- **Robustesse** : retry exponentiel (1 s, 2 s, 4 s) sur `claude_unreachable`, `claude_stream_interrupted`, HTTP `429`/`503`/`529`. Retry one-shot sur `stop_reason=max_tokens` avec budget `CLAUDE_MAX_TOKENS_RETRY` et consigne de concision.

### Collection MongoDB `pcorg_summaries`

```javascript
{
  _id, event, year,
  period_start, period_end, created_at,
  created_by, created_by_name,
  fiches_count, truncated,
  selection_detail: { total, majors, others, selected, cut, majors_capped,
                      max_fiches, selected_by_urgency },
  kpis: { total, open, closed, by_category, by_urgency,
          top_zones, top_sous_classifications, top_operators, avg_duration_min },
  sections: { synthese, faits_marquants, secours, securite, technique, flux,
              fourriere, recommandations, prochaines_24h },
  raw_text, model,
  usage: { input_tokens, output_tokens,
           cache_creation_input_tokens, cache_read_input_tokens,
           retried_for_truncation? }
}
```

Index : `(event, 1), (year, 1), (period_start, -1)` créé lazy au premier accès.

### Prompt Claude

Le `system` impose un **JSON strict à 9 clés** (`synthese, faits_marquants, secours, securite, technique, flux, fourriere, recommandations, prochaines_24h`) en français. Si le retour n'est pas parsable, fallback sur extraction par regex des paires complètes + récupération de la dernière clé tronquée. Tolérance liste/dict dans `_normalize_sections` : si Claude renvoie une liste pour une section, elle est convertie en puces `\n- ` propres. Plafond de **80 fiches** envoyées à Claude (priorité aux fiches `niveau_urgence ∈ {EU, UA}` ou `is_incident: true` qui sont toutes incluses) ; flag `truncated` + `selection_detail.majors_capped` exposés (le second loggue un warning si > 80 majeures, contexte normal perdu).

### Erreurs

- `ANTHROPIC_API_KEY` vide → **503** `{ok: false, error: "ANTHROPIC_API_KEY non configuree"}`.
- Anthropic injoignable / timeout → **502** `{ok: false, error: "claude_unreachable"}`.
- HTTP non-2xx Claude → **502** `{ok: false, error: "claude_http_<code>"}`.

### Génération en tâche de fond, coûts et budget (septembre 2026)

- `POST /api/pcorg/summary/generate` renvoie `202 {ok, job}` ; le front suit `GET /api/pcorg/summary/generate/status?job=` (étape, %, caractères/tokens reçus, secondes). Registre en mémoire (`start_summary_job`), un job par utilisateur, **suppose un seul process** (waitress). `dry_run` reste synchrone et gratuit (rétro N-1 lue en cache seulement). Le rapport matinal appelle toujours `generate_period_summary` en direct.
- **Structured outputs** (`output_config.format`, schéma construit depuis `section_keys`) + `output_config.effort` (défaut `medium`, env `CLAUDE_EFFORT`), envoyés seulement aux modèles qui les supportent ; sur HTTP 400, second essai sans. Le parseur regex n'est plus qu'un filet.
- ⚠️ Le thinking adaptatif (Sonnet 5.5) partage `max_tokens` et est facturé en sortie ; les `thinking_delta` ne servent qu'à la barre de progression. `stop_reason: "refusal"` lève `ClaudeError("claude_refusal")`.
- Prompt caching gardé pour l'interactif, **désactivé** pour le rapport matinal, la rétro N-1 et l'analyse fréquentation (écriture +25 % jamais relue). La consigne de concision du retry sur troncature va dans le tour user.
- **Corrections** : `sections_corrected.<section>` (la dernière l'emporte) ; UI et mail affichent ce texte avec « corrigé par X ». La note rétro N-1 n'apparaît jamais dans le mail.
- **Coût** : formule unique `compute_cost_usd`. `input_tokens` EXCLUT les tokens cache. Tarifs vérifiés le 29/09/2026 (`PRICING_VERIFIED_ON`).
- `record_ai_usage(db, feature, model, usage, meta)` journalise dans **`ai_usage_log`** les appels non stockés ailleurs. `/api/pcorg/summary/usage` agrège `pcorg_summaries`, `pcorg_n1_retros`, `scan_analyses` et `ai_usage_log` par fonction et par modèle. Ne pas journaliser deux fois un appel déjà stocké avec son `usage`.
- **Budget** : `cockpit_settings._id="ai_budget"` `{monthly_usd, block_when_exceeded}` ; si bloquant et atteint, `check_ai_budget` lève `ClaudeError("budget_exceeded")` → 429. Vue « Coûts IA » (admin) dans la section Mémoire IA de `/edit`.
- **Mémoire** : `scope.year` et `scope.phase` filtrent réellement (phase déduite de `globalHoraires`, inconnue → directives à phase exclues) ; directives de section groupées « Pour la section X ». Tri poids desc puis date desc.
- **Édition précédente** : une seule définition, `find_previous_edition` (la plus récente antérieure ayant date de course et données), utilisée par les comparaisons, la rétro et les renforts portes.
- Sélection des fiches non majeures : échantillon stratifié sur la période (12 tranches max). Cache rétro : clé arrondie à l'heure. `pcorg_morning_report.py --dry-run` = prompt seul, `--no-send` = génère sans mail.
