<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Applications liées (`integrations.py`, page admin `/integrations`) — 08/10/2026

Connecteur générique Cockpit ↔ applications externes. Premier usage : **Friday** (suivi des interventions du service informatique, dépôt `benguyard-hue/friday`, Node/Express/SQLite). Note remise au service informatique : `docs/integrations/friday-note-service-informatique.md` (contrat complet des messages, sécurité, travail côté Friday).

### Données
- `integrations` : une application par document `{_id: "friday", label, enabled, enabled_at, outbound_url, secret_env, rules: [{category, sous_classification|null}], status_labels}`. Règles = catégorie entière (`sous_classification: null`) ou sous-classification (comparaison sans accents ni casse). **Secret jamais en base** : variable d'environnement `secret_env` (défaut `COCKPIT_INTEG_<ID>_SECRET`, 32 caractères min., sinon liaison inactive).
- `integration_links` `{_id: "<integ>|<fiche>", sent_hash, sent_entries, sent_status, in_scope}` : ce qui a déjà été envoyé. **Une application ne peut agir que sur une fiche présente ici.**
- `integration_outbox` : file d'envoi (`pending` / `sent` / `failed`, `next_at`, `deadline` 24 h, `last_error`), envoyés purgés à 30 j (TTL `sent_at`).
- `integration_inbox` : `X-Event-Id` reçus (anti-rejeu, TTL 30 j) ; supprimé si l'événement est rejeté en 4xx (renvoi corrigé possible).
- `pcorg.integrations.<id>` : `{linked, ref, url, status, status_label, agent, attention, resolution, resolved_by, resolved_at, updated_at}` (badge). Écritures avec `$inc bounce_rev` (postes rafraîchis en ≤ 5 s) ; préservé par la synchro Prysm (`$set` partiel).

### Sortant
Thread `integrations.start_worker()` (démarré dans le `__main__` d'`app.py` avec les autres planificateurs, un seul process) : toutes les 5 s, `scan_integration` agrège les fiches des catégories des règles créées depuis l'activation (`enabled_at`, fenêtre 30 j) + les fiches déjà partagées (120 j), compare une empreinte (catégorie, sous-classification, texte, urgence, statut, lieu, carroyage, position, unité) et le nombre d'entrées de chronologie → `fiche.created` / `fiche.updated` / `fiche.comment` / `fiche.closed` / `fiche.reopened` / `fiche.unassigned`, avec la fiche complète et les `new_entries` (id stable = sha1(ts|opérateur|texte)[:16], **sans les entrées venues de cette application** : pas d'écho ; un passage sans entrée à transmettre n'envoie rien). Point de passage unique : Cockpit, tablettes Field et synchro Prysm sont tous vus. `process_outbox` : **dans l'ordre par application**, l'évènement le plus ancien bloque les suivants tant qu'il échoue (15 s → 1 h), `failed` après 24 h (relance manuelle en admin). Photos : URL signée 7 jours `/api/integrations/<id>/photo?p=&exp=&sig=` (HMAC du chemin relatif à `FIELD_PHOTOS_DIR`, sans session). `COCKPIT_PUBLIC_URL` (ou `PUBLIC_BASE_URL`) = base de ces URL.

### Adresse d'envoi (anti-SSRF, revue de sécurité 08/10/2026)
`check_outbound_url`, appelée **à l'enregistrement et à chaque envoi** : `https` seulement, pas d'identifiants dans l'URL, hôte présent dans **`COCKPIT_INTEG_ALLOWED_HOSTS`** (liste fixée sur le serveur, l'interface ne peut pas l'élargir). Les adresses privées sont admises pour ces hôtes-là (Friday est interne) ; **jamais** loopback, link-local (dont 169.254.169.254), multicast, non routables, y compris IPv4 dans IPv6, vérifié sur les adresses résolues au moment de l'envoi (`_resolve`). `requests.post(..., allow_redirects=False)`. Seul le **code HTTP** est conservé et affiché (`last_error`, bouton Tester), jamais le contenu de la réponse ; erreur réseau = type d'exception seulement.

### Signature (deux sens)
`X-Signature: sha256=` HMAC-SHA256(secret, `<X-Signature-Timestamp>.<corps brut>`), horodatage epoch secondes ± 300 s, comparaison `hmac.compare_digest`, `X-Event-Id` unique. Sortant : aussi `X-Requested-With` (garde CSRF de Friday).

### Entrant `POST /api/integrations/<id>/events`
Sans session, **CSRF exemptée sur cette vue seule** (`app.py`). 20 Mo max. Types : `ticket.linked`, `ticket.unlinked`, `ticket.updated` (statut / agent → entrée de chronologie), `ticket.note`, `ticket.photo` (base64 → `field._process_and_save_photo`, rangée `<event>/<year>`), `ticket.resolved` (`attention: "resolved"`). Entrées `origin: "integration:<id>"`, auteur « <label> - <agent> ». **Jamais de clôture** depuis l'extérieur (décision exploitation 08/10/2026) : la fiche affiche « Résolu … : à clôturer », l'opérateur clôt. Erreurs : 401 signature, 404 `fiche_non_partagee`, 409 `fiche_non_liee`, 400 contenu.

### Interface
- `pcorg.js` : bloc « Friday INC-… · statut · agent » dans la fiche (`buildIntegrationsBlock`, lien vers l'INC, orange « à clôturer » si résolu), icône `hub` / `task_alt` dans la liste. `PCO_PROJECTION` et `/detail` exposent `integrations` (`_pcorg_integrations_view`, seulement les liens actifs).
- Page `/integrations` (rôle admin réel, **non accordable** à un groupe : elle pilote ce qui sort de Cockpit) : état (secret présent, file, dernière erreur), « Tester » (ping signé), relance des échecs, édition (adresse, variable du secret, règles par catégorie / sous-classification). Lien « Applications liées » dans la barre latérale (admins).

Tests : `tests/test_integrations.py` (MongoDB local jetable : règles, ordre et abandon de la file, signature / horodatage / rejeu, périmètre, suivi complet sans clôture ni écho, photo signée).
