<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Tablettes Field — synchronisation avec le cockpit

La tablette (`static/js/field.js`) ne reçoit rien en temps réel : elle **poll** `/field/my-fiches` (statut, fiche active) et `/field/inbox` (messages, SOS), et rejoue ses écritures ratées via une **file hors ligne** IndexedDB. Le cockpit poll `/api/active-alerts` (`alert_poller.js`, 5 s) et `/anoloc/live` (15 s). Toutes les règles ci-dessous découlent de ces deux faits.

### Catégorie de la tablette

Chaque tablette porte **une** catégorie de fiche (`field_devices.category`), choisie à l'appairage ou dans la table de `/field-dispatch` (`POST /field/admin/devices/<id>/category`). À défaut, elle hérite du `pco_category` de son groupe de balises (`_device_category`) ; sans l'un ni l'autre, aucune restriction (comportement historique). Effets :

- `/field/my-fiches` ne rend que les fiches de cette catégorie, **plus** le propre SOS de la tablette (`content_category.field_sos`, toujours `PCO.Secours`) et sa fiche active (si le PC Org en change la catégorie après engagement, la tablette ne la perd pas).
- Création de fiche (`/field/create-fiche`, `/field/photo/send` avec fiche) : `403 category_not_allowed` hors catégorie. Une photo simplement envoyée au PC Org garde le choix libre.
- `/anoloc/vehicles-by-category` range chaque tablette sous **sa** catégorie (plus celle du groupe) : le PC Org ne la voit proposée que sur ces fiches.
- SOS (`_sos_recipients`) : diffusé aux catégories `PCO.Securite`/`PCO.Secours`, à la catégorie de l'émetteur et aux tablettes sans catégorie ; jamais aux révoquées.

⚠️ La liste des catégories existe en trois copies à garder alignées : `FIELD_CATEGORIES` (`field.py`), `FICHE_CREATE_CATEGORIES`/`CAM_CATEGORIES` (`field.js`) et `FIELD_CATEGORIES` (`field_admin.js`).

### Dispatch automatique et file du service (`dispatch_auto.py`)

Deux voies d'engagement coexistent. **Directe** : le PC Org (ou un responsable) choisit l'unité, comme avant. **Proposition automatique** : la fiche est proposée à l'unité disponible la plus proche, qui a `timeout_s` (30 s par défaut) pour **Accepter / Refuser** ; refus ou silence → unité suivante ; après `max_attempts` (3) ou faute de candidat → **file du service**, traitée par ses responsables sur `/dispatch-service` (utilisateurs cockpit, plusieurs par service).

- **Déclenchement** par catégorie, réglable dans Field Dispatch (`cockpit_settings._id="field_dispatch"`, `GET/PUT /api/dispatch/config`) : `never` / `always` / `urgency` (niveaux IMP=1, UR=2, UA=3, EU=4). Défauts : Technique sur UA/EU, Sécurité toujours, Secours jamais. Points d'entrée : création et création rapide sans unité, modification (`/update`) d'une fiche sans unité, changement d'urgence (`/set-urgency`). Bouton « Proposer automatiquement » (`POST /api/dispatch/<id>/auto`) pour le reste.
- **Candidats** (`find_candidates`) : même événement, même catégorie effective, statut `patrouille`, vue depuis < 15 min, pas de proposition en cours, **métier compatible** (`field_devices.metiers` choisis à l'appairage parmi les sous-classifications de la catégorie ; liste vide = tous ; comparaison sans accents : `Electricite` == `Electricité`, les deux existent dans `pcorg_lists`). Classement : position fraîche (< 5 min) d'abord, puis distance à vol d'oiseau.
- ⚠️ **Un téléphone verrouillé ne remonte plus sa position** (application web) : la « plus proche » peut s'appuyer sur une position de plusieurs minutes, d'où le tri par fraîcheur avant distance. Sur iPhone, les notifications n'arrivent que si l'app est installée sur l'écran d'accueil.
- **Une proposition à la fois par unité** : `field_devices.pending_proposal` réservé atomiquement. Passer en pause avec une proposition en attente vaut refus immédiat.
- **Accepter = engagement** : `cc.patrouille`, `dispatch.state="assigned"`, statut `intervention`, `active_fiche_id` en une fois. Pas de file hors ligne pour la réponse (une acceptation rejouée plus tard n'a plus de sens).
- **Engagement direct pendant une proposition** (`/api/pcorg/update` avec unité) : `on_manual_assign` annule la proposition. Clôture : `on_close`.
- **Responsable** (`manual_assign`) : si l'unité est occupée, la fiche s'ajoute à ses missions **sans écraser** sa fiche active.
- **Fin d'intervention par l'unité** (`self_close`, défaut Technique) : `POST /field/my-fiches/<id>/finish {outcome, report}`, compte-rendu obligatoire (≥ 5 caractères), photo conseillée. `resolu` clôt la fiche ; `partiel` / `materiel` / `impossible` la renvoient dans la file du service (`cc.patrouille` vidé). L'unité redevient disponible sans clic du PC Org. Rejouable (file hors ligne) : `deja_termine`.
- **Horodatages** `pcorg.intervention` : `engaged_at`, `arrived_at` (ASL manuelle ou GPS), `done_at`, `outcome`, `report` — écrits une seule fois (`mark_step`), base des délais par métier.
- État sur la fiche : `pcorg.dispatch` (`state`, `round`, `current`, `attempts[]`, `queued_at`, `queue_reason`, `assigned_by`). Chaque étape laisse une entrée système « Dispatch auto » dans la chronologie. `/api/pcorg/live` et `/detail` exposent `dispatch`.
- **Planificateur** `DA.start_scheduler()` (tick 3 s, démarré dans le `__main__` d'`app.py` à côté d'Alfred et PMV) : propositions expirées (+ 4 s de grâce) → unité suivante. Suppose un seul process (waitress) ; les transitions restent atomiques (filtre sur l'état attendu).
- Tests : `tests/test_dispatch_auto.py` contre un MongoDB **local réel** (base jetable `titan_test_dispatch_<pid>`, sautés sans Mongo) — les mises à jour conditionnelles et les pipelines de `pcorg_history` ne se simulent pas.

### Contrôles d'accès

- `/field/my-fiches/<id>/detail` : seulement une fiche affectée à la tablette, sa fiche active ou une fiche qu'elle a créée (`403 not_assigned`). Avant, toute tablette lisait toute fiche par son id.
- `/field/photos/*` : tablette non révoquée (cookie `field_token`) ou utilisateur cockpit (JWT, n'importe quel rôle), `Cache-Control: private`. La route était publique (`/field/*` est hors portail).

### Idempotence des actions tablette

⚠️ **Une requête peut arriver au serveur et sa réponse se perdre** (4G). La file hors ligne ou l'agent la renvoient alors. `/field/sos` (`sos_id`) et `/field/create-fiche` (`client_token`) portent une clé générée côté tablette, réservée dans `field_client_requests` (`_id = "<device>|<kind>|<clé>"`, TTL 2 j) par `_claim_client_request` : un renvoi retrouve le résultat du premier envoi. Sans elle, un SOS rejoué créait une fiche, une alerte plein écran sur chaque poste et une diffusion à chaque tablette de plus. Une clé réservée sans résultat depuis plus de 60 s est reprise (premier envoi mort en route) ; une erreur Mongo sur la clé ne bloque **jamais** un SOS.

`/field/status` ignore un renvoi du même statut (`unchanged: true`), sinon chaque retry ajoutait « Engagement confirmé » à la chronologie.

### Statuts rejoués hors ligne

⚠️ Un statut mis en file porte `queued_at` (epoch ms tablette). Le serveur répond **409 `stale_status`** s'il est antérieur à `status_since` : une tablette libérée par le cockpit repassait sinon en intervention au retour du réseau. La file le retire sans alarme et relance `pollFiches`.

⚠️ **Une réponse de poll partie avant un changement local porte l'ancien statut.** `pollFiches` ignore statut et fiche active si `state.statusChangedAt >= début du poll` (posé par `applyLocalStatus`). Sans cette garde, le statut « revenait en arrière » un cycle. Tout changement de statut passe par `postPatrolStatus` (file hors ligne, relecture immédiate, recadence des polls).

`fetchWithTimeout` (12 s) : une requête pendante ne bloque plus un changement de statut, elle part en file.

### Engagement, ASL, fin d'intervention

- L'engagement (« Prendre en charge », « Engagement ») envoie `fiche_id` : le serveur vérifie que la fiche est ouverte et affectée à la tablette (`403 not_assigned`, `409 fiche_indisponible`) et pose `active_fiche_id`. La chronologie reçoit **une** entrée, écrite par le serveur.
- L'ASL automatique (GPS < 10 m) trace désormais la même entrée que l'ASL manuelle.
- Le compte-rendu de fin d'intervention voyage **avec** le statut et s'écrit dans la chronologie côté serveur (il était posté à part, hors file hors ligne).
- En `fin_intervention`, la tablette poll au rythme normal (l'agent attend sa libération) ; seule la `pause` ralentit à 30 s. `anoloc.js` relit immédiatement après « Libérer ».

### SOS

- **Un seul SOS par déclenchement** : clé `sos_id`, 3 renvois automatiques (2/5/10 s) avec la même clé, puis file hors ligne.
- ⚠️ La fiche auto-créée (`PCO.Secours`, UA) est **exclue** de `alert_engine.detect_pcorg_urgency` (`content_category.field_sos`). Sans ça, le moteur levait une seconde alerte « ALERTE SECOURS » un cycle plus tard, décalée d'un poste à l'autre.
- `alert_poller.js` : deux mémoires. **Vu** (sessionStorage, par onglet) et **traité** (localStorage, tout le poste, posé au clic sur un bouton de l'alerte, TTL 24 h). Au chargement d'une page, une alerte récente déjà traitée sur le poste ne repasse plus en plein écran — avant, chaque navigation dans les 5 min réaffichait le SOS. Ne pas partager le « vu » entre onglets : un onglet caché masquerait l'alerte à l'onglet visible.
- Un SOS passe **devant** la file d'alertes (et interrompt une alerte non SOS) et n'est jamais filtré par les préférences locales. Poll immédiat au retour sur l'onglet : Chrome ralentit à 1/min les minuteries d'un onglet en arrière-plan.
- Tablettes : `/field/inbox` marque `sos_resolved` quand la fiche SOS est close ; le repère carte est retiré. Au redémarrage de l'app, un SOS non acquitté et non résolu est réaffiché.

### Inbox

⚠️ `/field/inbox` rend les **200 plus récents** (tri décroissant puis inversé). Le tri croissant + `limit(200)` rendait les 200 plus anciens : au-delà de 200 messages sur 7 jours, plus aucun nouveau message n'arrivait.

### Itinéraire

`setRouteDestination(latlng, polyline, god, ficheId)` mémorise la fiche visée ; `reconcileRoute()` efface le tracé quand elle est close ou désaffectée, ou que l'agent passe en fin d'intervention / disponible. `/api/route/forward` transporte `fiche_id`. Bouton `#btn-route-clear` visible tant qu'un tracé est affiché. Avant, rien n'effaçait jamais un itinéraire.

⚠️ `field-sw.js` sert `field.js` en **cache-first** : toute modification du JS tablette impose d'incrémenter `SW_VERSION`.

### Prise en charge partagée d'un SOS

`POST /api/active-alerts/<id>/take` (`user`, CSRF actif) : le premier opérateur qui clique « Je prends en charge » est posé sur l'alerte (`taken_at`, `taken_by`, `taken_by_name`) par un `find_one_and_update` conditionné à `taken_at` absent — **un seul gagnant**, les suivants reçoivent `409 already_taken` avec le nom. La route ajoute « SOS pris en charge par X » à la chronologie de la fiche SOS et prévient la tablette émettrice (message inbox + push).

Côté postes (`alert_poller.js`), `/api/active-alerts` renvoie ces champs : au poll suivant (5 s max) l'alerte affichée passe en vert « Pris en charge par X à HH:MM », l'alarme s'arrête et elle se ferme seule après 10 s ; un SOS déjà pris est retiré de la file et n'est jamais affiché en plein écran sur un poste qui ne l'avait pas encore vu. Chez celui qui prend, l'alerte se ferme et la fiche s'ouvre.

### Limite connue

Pas de push temps réel vers les postes cockpit (choix assumé) : délai d'affichage d'un SOS et de sa prise en charge de 0 à 5 s selon le poste, plus si l'onglet est en arrière-plan (rattrapage au retour sur l'onglet).
