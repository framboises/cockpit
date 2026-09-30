<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Intégration Vision (app externe `vision-a0f55.web.app`)

L'app Vision (scan billets véhicule, repo voisin `../vision`) est **JWT-gated par Cockpit** mais **totalement dissociée de Field** : module Python séparé, collections MongoDB séparées, JS séparé, modales séparées. La page admin Cockpit (`field_dispatch.html`) regroupe les deux UIs (section Field + section Vision) pour la commodité opérationnelle, mais aucun code n'est partagé entre les deux apps.

### Architecture

- **Module Python** : `cockpit/vision_admin.py` (blueprint `vision_admin_bp` enregistré dans `app.py` à côté de `field_bp`, exempté de CSRF). Réutilise uniquement les helpers génériques de `field.py` (`admin_required`, `_get_mongo_db`, `_client_ip`, `_rate_limit_pair`, `_generate_pairing_code`, `_now`, `_iso`, `_event_end_datetime`) — aucun accès aux collections `field_*`.
- **Collections MongoDB** : `vision_pairings` (codes 6 chiffres, TTL 15 min), `vision_devices` (tablettes Vision enrôlées, avec `tablet_uid` stable + `current_user`), `vision_sessions` (une entrée par identification opérateur, fermée à la déconnexion ou par sweep auto-logout 4 h). Indexes créés lazy au premier accès.
- **JS** : `static/js/vision_admin.js` (IIFE autonome avec ses propres helpers HTTP, son state, ses modales). Chargé après `field_admin.js` dans `field_dispatch.html`.
- **UI** : section "Tablettes Vision" dans `field_dispatch.html` sous la section Field, avec bouton dédié "Appairer une tablette Vision", table dédiée (`#vision-devices-table`), modales dédiées (`#vision-pair-modal`, `#vision-codes-modal`).

### Flux opérationnel

1. **Admin** ouvre Field Dispatch → section "Tablettes Vision" → bouton "Appairer une tablette Vision" → saisit nom + lieu (Ouest/Panorama/Houx) → un code 6 chiffres est généré.
2. **Opérateur** ouvre `https://vision-a0f55.web.app` directement sur la tablette (URL bookmarkée ou PWA, **pas via Field**) → écran bleu de pairing → saisit le code → Vision appelle `POST https://cockpit.lemans.org/field/api/vision/pair` (CORS depuis `vision-a0f55.web.app`).
3. **Cockpit** vérifie le code dans `vision_pairings`, génère un JWT RS256 (`exp = fin événement + 1 jour`, fallback 24 h), consomme le code, crée un doc `vision_devices` pour l'inventaire admin, retourne le JWT.
4. **Vision** stocke le JWT en `localStorage.vision_jwt`, recharge la page, l'app démarre.

### Routes

Toutes sous `/field/*` pour profiter de la whitelist d'auth Cockpit (`/field/*` est public sans portail) :

- `POST /field/api/vision/pair` (**public + CORS**) — échange code → JWT. Body : `{code, tablet_uid?}`.
- `POST /field/api/vision/heartbeat` (**public + CORS**, JWT Bearer) — remontée batterie/GPS + matérialisation révocation. Si device introuvable ou `revoked`, retourne `403 {error: "revoked"}` → la tablette purge son JWT et retombe sur le pairing. Met à jour la session opérateur active si elle existe.
- `POST /field/api/vision/identify` (**public + CORS**, JWT Bearer) — scan QR badge → lookup planbition. **Tente d'abord `person_id_external` (PersonID Adecco, index unique sparse), puis fallback sur `employee_number`** (champ historique). Crée une entrée `vision_sessions` avec `scanned_code`, `id_source` (`"person_id_external"` ou `"employee_number"`), `person_id_external`, `employee_number` canonique. Met à jour `vision_devices.current_user` avec les mêmes champs. Body : `{employee_number, tablet_uid?}` (le nom du champ reste `employee_number` côté API par compat, mais peut contenir n'importe lequel des deux identifiants). Erreur `unknown_employee` (404) si non trouvé (blocage strict).
- `POST /field/api/vision/logout` (**public + CORS**, JWT Bearer) — clôt la session opérateur active (`ended_reason: "logout"`), efface `current_user`. Le JWT reste valide.
- `GET /field/admin/vision/pairings` (admin) — liste codes actifs.
- `POST /field/admin/vision/pairings` (admin) — créer un code (`{name, lieu, event, year, notes?}`).
- `DELETE /field/admin/vision/pairings/<code>` (admin) — annuler un code.
- `GET /field/admin/vision/devices` (admin) — liste devices Vision enrôlés.
- `POST /field/admin/vision/devices/<id>/lieu` (admin) — changer le lieu (`{lieu}`).
- `POST /field/admin/vision/devices/<id>/revoke` (admin) — révoquer ; effet effectif au prochain heartbeat de la tablette (sweep côté serveur).
- `DELETE /field/admin/vision/devices/<id>` (admin) — supprimer définitivement.
- `GET /field/admin/vision/sessions?tablet_uid=&event=&year=&employee_number=` (admin) — historique des sessions opérateur (modale "Historique" dans `vision_admin.js`).

### JWT

- Algo RS256, `iss="cockpit-vision"`, `exp = parametrages.data.globalHoraires.demontage.end + 1 jour` (fallback 24 h).
- Clés RSA dans `cockpit/keys/vision_jwt_{private,public}.pem`. Privée dans `.gitignore` (`keys/*_private.pem`). Publique embarquée en dur dans `vision/associer.html` (`VISION_JWT_PUBKEY_PEM`). Validation côté Vision en local (`crypto.subtle.verify`) — pas d'appel réseau pour valider, mode hors ligne préservé.
- Variables d'env : `VISION_JWT_PRIVATE_KEY` (path override), `VISION_APP_URL` (default `https://vision-a0f55.web.app/associer.html`).

### CORS

- Whitelist `VISION_ALLOWED_ORIGINS` dans `vision_admin.py` : `["https://vision-a0f55.web.app", "https://vision-a0f55.firebaseapp.com"]`. Helper `_cors_response()` + preflight OPTIONS géré explicitement sur `/field/api/vision/pair`.

### Sync MongoDB

- `vision_sync.py` propage `device_id` et `device_name` des docs `immatriculations` Firestore vers `vision_immatriculations` (index `device_id` ajouté).
- ⚠️ **Chaque lecture Firestore est facturée.** Jusqu'au 29/09/2026 le script relisait tout l'événement à chaque passage (10 719 docs × 288 passages/jour ≈ 3,1 M lectures, ~1,60 €/jour, ~25 €/mois alors que VISION ne servait plus). Désormais, dans la tâche planifiée « Collecte LHPI vision » (toutes les 5 min) : lecture incrémentale sur `date` (ISO UTC, triable en chaîne, recouvrement 15 min, index mono-champ automatique, pas d'index composite), puis contrôle `count()` sur l'événement (~11 lectures) qui déclenche une resync complète si Firestore compte plus de docs que la base (tablette hors ligne remontant des scans datés dans le passé). Resync complète aussi toutes les 24 h et à chaque changement d'événement ; blacklist au plus toutes les 60 min. État dans `vision_config{_id: "sync_state"}` (`date_cursor`, `last_full_at`, `last_full_key`, `last_blacklist_at`) : le supprimer force une resync complète.
- ⚠️ **Le script doit finir par `os._exit`.** Les threads gRPC de `firebase_admin` gardaient le process vivant après « Sync terminee » ; avec `MultipleInstances = IgnoreNew`, la tâche restait bloquée jusqu'à sa limite de 72 h, puis repartait (d'où une facture visible certains jours seulement).

### Constantes

- `VISION_LIEUX = ["Ouest", "Panorama", "Houx"]` dans `vision_admin.py` — à mettre à jour si on ajoute des lieux Vision (et synchroniser les options du dropdown dans `field_dispatch.html` + le validator côté Vision).
