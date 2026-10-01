<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Alfred — agent IA WhatsApp (`alfred.py`)

WAHA pousse les messages sur `POST /api/wa/webhook` (HMAC, exempté de CSRF). Par groupe (`wa_alfred_config`) : `listen` (ingestion `wa_inbound_messages`, TTL 14 j, **toute l'année depuis le 01/10/2026** : plus gatée par le live-contrôle, tag event/year = `event_courant.current_event`), `respond_mentions`, `summary_enabled`. Les mentions partent vers le wrapper `/alfred/ask` de la VM (boucle d'outils hors de ce repo) ; les résumés vers Ollama, via le scheduler 60 s du `__main__` d'`app.py`.

- **Déclenchement** : `@alfred` explicite (le mot seul ne suffit plus), mention native (`alfred_lid`), suite de conversation 7 min, ou DM en liste blanche.
- **Envois** : toujours par `WhatsAppService.send_direct`, tracés dans `cockpit_wa_send_history` (`source: "direct"`) et comptés dans les plafonds horaire/journalier. Réponse : ignore les heures silencieuses, refusée si breaker ouvert ou plafond atteint ; phrase d'attente et refus DM sautent dès 80 % du plafond horaire (90 % du journalier).
- ⚠️ **Breaker partagé** : mémoire du module + `cockpit_wa_config{_id: "wa_breaker"}`, relu toutes les 15 s. Il était porté par l'instance, recréée à chaque envoi : il ne s'ouvrait jamais.
- **Résumés** : un seul à la fois par groupe ; après échec, attente `min(intervalle, échecs × 5 min)`. « Résumer » ignore l'attente mais pas la garde (`already_running`). Rétention 180 j.
- **Prompt de résumé** : transcription entre `<<<MESSAGES` et `MESSAGES>>>`, chevrons neutralisés, sauts de ligne aplatis ; heures en Paris. ⚠️ pymongo rend des datetimes naïfs UTC : jamais `astimezone()` sans `replace(tzinfo=utc)`.
- **Webhook** : en prod, secret vide = tous les webhooks refusés. SHA-1 toléré avec avertissement, à retirer une fois vérifié côté WAHA.
- **Évaluation** : chaque échange de mention dans `wa_alfred_exchanges` (TTL 90 j). Le durcissement du wrapper `/alfred/ask` se fait sur la VM.
