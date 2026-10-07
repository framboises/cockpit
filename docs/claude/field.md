<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Tablettes Field — synchronisation avec le cockpit

La tablette (`static/js/field.js`) ne reçoit rien en temps réel : elle **poll** `/field/my-fiches` (statut, fiche active) et `/field/inbox` (messages, SOS), et rejoue ses écritures ratées via une **file hors ligne** IndexedDB. Le cockpit poll `/api/active-alerts` (`alert_poller.js`, 5 s) et `/anoloc/live` (15 s). Toutes les règles ci-dessous découlent de ces deux faits.

### Catégorie de la tablette

Chaque tablette porte **une** catégorie de fiche (`field_devices.category`), choisie à l'appairage ou dans la table de `/field-dispatch` (`POST /field/admin/devices/<id>/category`). À défaut, elle hérite du `pco_category` de son groupe de balises (`_device_category`) ; sans l'un ni l'autre, aucune restriction (comportement historique). Effets :

- `/field/my-fiches` ne rend que les fiches de cette catégorie, **plus** le propre SOS de la tablette (`content_category.field_sos`, toujours `PCO.Secours`) et sa fiche active (si le PC Org en change la catégorie après engagement, la tablette ne la perd pas).
- Création de fiche (`/field/create-fiche`, `/field/photo/send` avec fiche) : `403 category_not_allowed` hors catégorie. Une photo simplement envoyée au PC Org garde le choix libre.
- `/anoloc/vehicles-by-category` range chaque tablette sous **sa** catégorie (plus celle du groupe) : le PC Org ne la voit proposée que sur ces fiches.
- SOS (`_sos_recipients`) : diffusé aux catégories `PCO.Securite`/`PCO.Secours`, à la catégorie de l'émetteur et aux tablettes sans catégorie, sur tous les événements vus (voir « SAISON et épreuves simultanées ») ; jamais aux révoquées.

⚠️ La liste des catégories existe en trois copies à garder alignées : `FIELD_CATEGORIES` (`field.py`), `FICHE_CREATE_CATEGORIES`/`CAM_CATEGORIES` (`field.js`) et `FIELD_CATEGORIES` (`field_admin.js`).

### Mode déclarant (07/10/2026)

Drapeau **du groupe de balises** (`anoloc_config.global.beacon_groups[].declarant`, case « Mode déclarant » de la modale de groupe dans `edit.html` / `anoloc_admin.js`). Jamais choisi à l'appairage : l'opérateur donne le groupe, le mode suit. Lu à chaud (`field._device_declarant(db, device, groups)`, via `beacon_group_id`) : décocher le groupe rend la tablette normale au poll suivant. Cas d'usage : cellule d'appui prestataire qui patrouille et signale dégâts / réparations.

- **Jamais de fiche** : un déclarant dépose des **constats** (collection `declarations`, voir `declarations.md`). `/field/create-fiche` et `/field/photo/send` avec création de fiche → **403 `declarant`**. Un opérateur du droit « Traiter les constats terrain » les transforme en fiche depuis `/declarations`.
- **Jamais une unité** : exclue de `find_candidates`, `available_for_device` (`[]`), `self_assign` / `manual_assign` (`unite_invalide`), des unités de `/api/dispatch/board` et de `/anoloc/vehicles-by-category`. Pas de proposition (`my-fiches.proposal = null`), pas de `self_close`. `/field/my-fiches` ne rend que son propre SOS (réponse `declarant: true`).
- **SOS** : garde son bouton (son SOS part normalement), mais **ne reçoit pas** ceux des autres (`_sos_recipients`).
- **Photos** sous `uploads/field_photos/_declarant/<event>/<year>/` (`_device_photo_sub_dir`), purgées à **365 jours** (`FIELD_PHOTO_DECLARANT_TTL_DAYS`) au lieu de 30, sans lire Mongo.
- **Tablette** (`field.js`, `body.field-declarant`) : barre de statut, bandeau d'engagement, onglet « Disponibles » et bouton Caméra masqués ; bouton orange **« Déclarer »** (`#declare-modal` : photos via `<input capture>` réduites à 1920 px côté téléphone, description, priorité basse / normale / haute, position GPS ou appui long → `POST /field/declarations`) ; « Missions » devient **« Mes constats »** (`GET /field/declarations`, état Envoyé / Vu par le PC / Pris en charge / Traité / Classé), détail dans la modale de fiche avec complément texte + photo. Hors ligne : file d'attente (JSON ou multipart, idempotente par `client_token`). Messages et destinations envoyés par le PC Org inchangés. `FIELD_DEVICE.declarant` au chargement.
- Tests : `TestModeDeclarant` (`test_field_access.py`), `TestDeclarant` (`test_dispatch_auto.py`), `test_declarations.py`.

### Dispatch automatique et file du service (`dispatch_auto.py`)

Deux voies d'engagement coexistent. **Directe** : le PC Org (ou un responsable) choisit l'unité, comme avant. **Proposition automatique** : la fiche est proposée à l'unité disponible la plus proche, qui a `timeout_s` (30 s par défaut) pour **Accepter / Refuser** ; refus ou silence → unité suivante ; après `max_attempts` (3) ou faute de candidat → **file du service**, traitée par ses responsables sur `/dispatch-service` (utilisateurs cockpit, plusieurs par service).

- **Page `/dispatch-service`** : 4 sections (À traiter, Proposition en cours, Engagées, Terminées = `board.closed`, 100 dernières fiches closes, `CLOSED_ON_BOARD`). Fiches en ligne compacte pliable (`state.expanded`), tri par section « Plus récentes / Plus anciennes » mémorisé (`localStorage ds_sort`, date de référence : mise en file, création, engagement, clôture). **« Nouvelle fiche » ouvre l'assistant 3 étapes de l'accueil** : `pcorg.js` chargé en mode création seule (`window.PCORG_CREATE_ONLY`, `window.PcorgCreate.open({categories, onCreated})`), balisage partagé `templates/_pcorg_create_modal.html` (inclus aussi par `index.html`). Le formulaire simplifié `#ds-new` ne sert plus que de repli. Sans `map_view.js`, la zone (`area_desc`) n'est pas déduite du point : seul le carroyage l'est. ⚠️ `style.css` stylise tout `<footer>` (pied de page du site) : les fenêtres de la page le neutralisent (`.ds-sheet footer.ds-fiche-foot`).
- ⚠️ **Service worker (`field-sw.js`)** : le cache « coquille » (cache-first) ne vaut que pour les chemins EXACTS de l'app (`isShellRequest`). Avant, une correspondance par sous-chaîne sur `"/field"` servait depuis le cache toute URL contenant `/field` : les fils de conversation (`/field/thread/<id>`) restaient figés — d'où « il faut recharger pour voir les nouveaux messages ». `isLiveRequest()` envoie toujours au réseau `thread/`, `available-missions`, `denied/check`, `status`, `push/`. Toute nouvelle route tablette lue en boucle doit y figurer. `catchUpNow()` relit inbox et fiches au retour dans l'app (focus, `pageshow`, `online`, notification push, premier toucher après un gel iOS).
- ⚠️ **Notifications push (Web Push, `field.send_push_to_device`)** : jusqu'au 01/10/2026 AUCUNE n'est jamais partie — `pywebpush` absent de l'environnement de prod (échec avalé en `debug`), et une fois installée la clé était mal passée (texte PEM lu comme du DER base64 : « Could not deserialize key data »). Désormais : `pywebpush` 2.3.0 installé en gardant `cryptography` 46, clé passée en instance `py_vapid.Vapid` (`_vapid_signer`), contact VAPID = `https://cockpit.lemans.org` par défaut (`VAPID_CONTACT_EMAIL` accepte un e-mail ; un `.local` est refusé par Apple), échecs consignés en `warning`, abonnements expirés (404/410) supprimés. Clé : `vapid_private.pem` (PKCS8) à la racine — la changer invalide tous les abonnements. iPhone : notifications seulement si Field est sur l'écran d'accueil (iOS ≥ 16.4) et autorisée depuis l'icône ; Android : Chrome suffit.
- ⚠️ **Réactivation après la fin de l'événement** : une tablette réactivée par un admin (`restoredAt`) APRÈS la fin du démontage n'est plus révoquée par l'expiration automatique (`_restored_after_event_end`, filtre du sweep). Avant, `/field/denied` renvoyait vers `/field` (tablette non révoquée) et les API répondaient `event_ended` → retour à `/field/denied` : boucle infinie.
- **Libre-service** : onglet « Disponibles » des Missions (`GET /field/available-missions`, `DA.available_for_device`) ; « M'engager » → `POST /field/missions/<id>/take` (`DA.self_assign`, engagement immédiat, `assigned_by: "libre-service"`, annule une proposition faite à une autre unité ; `409 deja_prise / unite_occupee / categorie_differente / metier_different / fiche_closee`).
- **Qui est responsable** : les membres d'un groupe cockpit coché « Responsable de service » (Configuration > Groupes, `dispatch_manager`) ; les catégories du groupe fixent la file visible (`dispatch_auto.managed_categories` → `app._user_dispatch_categories`). Plus de liste d'e-mails dans la config du dispatch (ancienne clé `managers` ignorée). Sur la page, un responsable ouvre la fiche, ajoute une action et la clôt ; aucune modification des éléments initiaux n'y est proposée (et « Fiches en lecture seule » la refuse côté serveur).

- **Déclenchement** par catégorie, réglable dans Field Dispatch (`cockpit_settings._id="field_dispatch"`, `GET/PUT /api/dispatch/config`) : `never` / `always` / `urgency` (niveaux IMP=1, UR=2, UA=3, EU=4). Défauts : Technique sur UA/EU, Sécurité toujours, Secours jamais. Points d'entrée : création et création rapide sans unité, modification (`/update`) d'une fiche sans unité, changement d'urgence (`/set-urgency`). Bouton « Proposer automatiquement » (`POST /api/dispatch/<id>/auto`) pour le reste.
- **Candidats** (`find_candidates`) : tablette qui voit l'événement de la fiche (`field.device_matches_pair`, voir « SAISON et épreuves simultanées »), même catégorie effective, statut `patrouille`, vue depuis < 15 min, pas de proposition en cours, **métier compatible** (`field_devices.metiers` choisis à l'appairage parmi les sous-classifications de la catégorie ; liste vide = tous ; comparaison sans accents : `Electricite` == `Electricité`, les deux existent dans `pcorg_lists`). Classement : position fraîche (< 5 min) d'abord, puis distance à vol d'oiseau.
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

### SAISON et épreuves simultanées

Source unique : `event_courant.py` (SAISON reconnu par son NOM, épreuve active de `montage.start` à `demontage.end`, bascule ; entre épreuves simultanées, priorité **choisie par un admin** — `cockpit_settings._id="event_priority"`, pastille de l'en-tête — sinon l'épreuve déjà en cours garde la main). Une tablette n'est plus liée à son seul appairage `(event, year)` (`field_devices.year` est une **chaîne**, `pcorg.year` un **entier** : comparer via `field._pair_key` / `EC.pairs_filter`).

- **`field.device_pairs(db, device)`** = `EC.active_pairs(include_previous_saison=True)` + la paire d'appairage (`device_home_pair`). Une tablette SAISON d'une année passée vaut SAISON courant : **pas de ré-appairage au 1er janvier**. Une tablette SAISON voit donc les fiches des épreuves actives, une tablette d'épreuve celles de SAISON.
- ⚠️ **Les noms ne sont uniques que par appairage** : `device_matches_pair` refuse une paire sur laquelle une **homonyme** active est appairée (la fiche « Secu 1 » de l'épreuve est à la « Secu 1 » de l'épreuve, pas à celle de SAISON). `device_fiche_pairs` = `device_pairs` moins ces paires. `find_device_for_fiche(name, event, year)` (engagement PC Org `app._engage_field_device`, libération `/api/field-device/release`) prend d'abord la tablette appairée sur la paire, sinon une tablette qui la voit. `_disengage_field_device` et la réaffectation de `manual_assign` désignent la tablette par `active_fiche_id` seul.
- Utilisé par : `/field/my-fiches`, détail de fiche (accès par le nom), `find_candidates`, `manual_assign` (`unite_invalide`), `self_assign` (`409 evenement_different`), `available_for_device`, `/anoloc/vehicles-by-category` (`_field_tablets_seeing_pair_by_group`), `_sos_recipients` (toute tablette non révoquée dont les `device_pairs` croisent ceux de l'émetteur, en pratique toutes puisque SAISON est toujours actif).
- `_device_expired` : tablette d'épreuve terminée pas encore balayée (le balayage `_sweep_event_if_ended` est paresseux, il n'a lieu qu'au prochain appel de LA tablette) : exclue des candidats, du SOS, de l'engagement par nom et de la file du service.
- **Création depuis la tablette** (`/field/create-fiche`, photo avec fiche, fiche SOS + alerte cockpit) : `fiche_target_event` = l'événement de la tablette si c'est une épreuve **active**, sinon `EC.current_event`.
- **Expiration** : SAISON n'est jamais révoqué automatiquement (`_sweep_event_if_ended` sort tout de suite). Épreuves inchangées.
- **Disponibles (libre-service)** : `available_for_device` trie désormais `ts` **décroissant** et borne à 30 jours (`AVAILABLE_MAX_AGE_DAYS`). Avant : tri croissant + `limit(300)` = les 300 fiches ouvertes les PLUS ANCIENNES, les nouvelles n'apparaissaient plus (inévitable avec SAISON).
- **File du service** (`/api/dispatch/board`) : sans paramètre, fiches de **tous les événements actifs** ; `event`/`year` devient un filtre optionnel (sélecteur « Tous les événements actifs » par défaut, mémorisé dans `ds_event_filter`, ne touche plus la sélection du cockpit). Projection (`BOARD_FICHE_PROJECTION` : 30 dernières entrées de chronologie + `$size` pour le total). Fiches ouvertes SAISON bornées à 30 jours, les plus anciennes comptées (`older_open`, bandeau). Nom de l'événement sur chaque carte quand plusieurs sont affichés (`multi_event`). « Nouvelle fiche » : le filtre, sinon `current` (événement courant).
- **Appairage** (`field_admin.js`) : la modale propose l'événement courant (`/api/event/current`) par défaut, les autres actifs et la sélection du header.

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
