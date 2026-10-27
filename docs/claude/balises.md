# Balises GPS : groupes communs, sources Anoloc et Teltonika, alertes

Mis en place le 12/10/2026. Une seule couche "groupes de balises" pour la carte,
les traces, la main courante, le dispatch Field et la centrale d'alerte ; deux
sources de balises qui y entrent de la meme facon.

## Pivot : les groupes (`anoloc_config.global`)

- `beacon_groups[]` : `id`, `label`, `icon`, `color`, `pco_category`, `declarant`,
  `enabled`, `anoloc_device_ids` (nom historique : TOUTES les balises, Anoloc et
  Teltonika), `device_labels` (libelles : rapprochement main courante par nom,
  collisions de noms Field).
- `enabled` = interrupteur general "Balises GPS actives" (carte, compteurs,
  alertes) en tete de Configuration > Balises GPS. La collecte Anoloc a son
  propre interrupteur (`live-control.collecting`).
- Ids : Anoloc = id Anoloc ; Teltonika = `tlt:<imei>` ; tablette = `field:<id>`
  (jamais dans les groupes, rattachee par `field_devices.beacon_group_id`).
- Libelle d'une Teltonika : celui du parc (`balises_devices.label`), recopie dans
  `device_labels` a chaque enregistrement de groupe ou de balise.
- ⚠️ Fenetre de groupe : les balises Anoloc deja dans le groupe restent affichees
  et cochees meme si l'API Anoloc ne repond pas. Avant, enregistrer un groupe
  avec l'API en panne (503 depuis le 06/08/2026) effacait toutes ses balises.

## Collections communes

| Collection | Contenu |
|---|---|
| `anoloc_latest` | etat courant par balise (`_id` = device_id), toutes sources |
| `anoloc_positions` | historique des positions (balises + tablettes), **TTL 10 mois** sur `collected_at` |
| `balises_messages` | messages Teltonika complets (`msg` imbrique + IO bruts), index unique `(imei, ts)`, TTL 10 mois sur `received_at` |
| `balises_devices` | parc Teltonika : `_id` tlt:imei, imei, label, model, enabled, commissioned_at, period_stop_s, period_move_s |
| `balises_config` | `teltonika` (periodes par defaut), `flespi` (enabled, channel_id, cursor, last_*), `ingest:<source>` (dernier envoi) |
| `balises_unknown` | IMEI inconnus vus (refuses) : bouton "Ajouter au parc" |

TTL : l'index `collected_at_1` existait sans TTL ; `balises_core.ensure_positions_ttl`
le modifie par `collMod` (un `create_index` avec d'autres options leve
IndexOptionsConflict). Appele au demarrage de Cockpit, par anoloc_collector et
flespi_poller. Pour Teltonika, `collected_at` = horodatage BALISE (une trace
range un rattrapage a sa place) ; `received_at` = reception.

## Etat en ligne / GPS : `balises_core.device_state` (une regle par source)

- Anoloc (inchange) : statut != offline et derniere vraie trame < 30 min ; GPS si < 5 min.
- Teltonika : en ligne tant que le dernier message a moins de 3 periodes attendues
  (+ 2 min). Periode = `period_move_s` si le dernier message disait "en mouvement",
  sinon `period_stop_s` (par balise, sinon defaut de Configuration). ATC700 : ~15 min
  a l'arret, bien plus frequent en mouvement (A MESURER : fenetre "Derniers
  messages" du parc, colonne Ecart). GPS = le dernier message avait un fix.
- Utilise par `/anoloc/live`, `/anoloc/status`, l'admin et les alertes : ne jamais
  recoder la regle ailleurs.

## Ingestion Teltonika : `balises_core.ingest(db, messages, source)`

Message normalise = cles a points style flespi (`ident`, `timestamp`,
`position.*`, `battery.level`, `movement.status`, `gsm.signal.level`,
`external.powersource.voltage`, `io` brut...). Regles :
1. IMEI absent du parc ou desactive : ignore, note dans `balises_unknown`.
2. `(imei, timestamp)` deja vu : doublon (rattrapage, renvoi, balise vue par flespi ET le recepteur).
3. Horodatage < plancher (2024-01-01 ou mise en service - 1 j) ou > maintenant + 5 min :
   garde dans `balises_messages` (`ts_valid: false`), ni position ni etat.
4. Sans fix (`position.valid` faux, 0/0, satellites 0, IO 69 != 1) : telemetrie mise a jour, pas la position.
5. `anoloc_latest` : telemetrie seulement si plus recente que `last_msg_at`, position seulement si plus recente que `last_pos_at`.
6. Charge = `external.powersource.voltage` > 4 V.
7. En mouvement (`_moving`) = vitesse GPS >= 5 km/h avec fix, sinon capteur
   `movement.status`. Le capteur de l'ATC700 retombe parfois a false en roulant
   (vu le 13/10/2026 : "arret" a 66 km/h, suivi de trous de 1 a 1,5 min).

## Trace recalee sur les voies (`balises_trace.py`, affichage seul)

`/anoloc/trail` rend `points` (brut) + `segments` `[{m, c}]` + `matching`
(`ok` / `off` / `none`). Les positions en base ne sont JAMAIS modifiees.
- Trajets coupes sur silence > 5 min, bruit d'arret (< 8 m) fondu ; chaque trajet
  part a Valhalla `/trace_attributes` (map_snap horodate, chunks de 400 points,
  costing auto + options du mode god : sens uniques / acces ignores).
- Par paire de points : `m=true` (trait plein, suit la forme) si les deux points
  sont recales a <= 25 m d'une voie, arcs contigus, chemin <= max(2,5 x ligne droite,
  ligne droite + 250 m) et <= 250 km/h ; sinon `m=false` (trait droit en POINTILLES :
  parking, voie absente d'OSM, rupture).
- Valhalla en panne : tout en pointilles, pause de 60 s des appels. Reponse 400 =
  trace non recalable, mise en cache comme definitive.
- Cache memoire LRU (300 chunks) cle = hash des points : un trajet termine n'est
  recale qu'une fois. Tablettes (`field:`) jamais recalees (pietons).
- Front : bouton "Points GPS" de la popup (positions brutes superposees, heure a la seconde).

## Sources

### Recepteur maison (cible) : `recepteur_balises/`
`codec8.py` (Codec 8 / 8E, CRC-16/IBM, normalisation) + `recepteur.py` (serveur TCP
5027). Bibliotheque standard UNIQUEMENT : compte local `svc-balises`, `C:\Python313`,
sans acces a Cockpit ni au venv. Chaine : poignee de main IMEI (liste blanche
servie par Cockpit, cache disque) -> paquet -> CRC -> spool (un fichier JSON
atomique + fsync) -> ACK -> thread d'envoi -> `POST /api/balises/ingest`.
Deploiement : `recepteur_balises/README.md`.

`balises_api.py` : `/api/balises/ingest` et `/api/balises/whitelist`, HMAC-SHA256
(`BALISES_INGEST_SECRET`, meme format qu'integrations.py), IP source
`BALISES_INGEST_ALLOWED_IPS` (defaut 127.0.0.1, ::1 ; derriere IIS remote_addr est
l'IP reelle, jamais 127.0.0.1), CSRF exemptee. Secret absent = 503.

### flespi (transitoire) : `flespi_poller.py`
Le temps d'ouvrir le port 5027 : releve REST sortante du canal
(`GET /gw/channels/<id>/messages`, `data={"from": cursor, "count": 1000}`,
curseur = `server.timestamp`), tache planifiee chaque minute, 2 releves a 30 s.
`FLESPI_TOKEN` dans l'environnement du compte de la tache ; canal et activation
dans Configuration > Balises GPS > Source Teltonika. A desactiver une fois les
balises basculees (aucun doublon pendant la bascule).

## Alertes (`beacon_alerts.py`, branche dans alert_engine.HANDLERS)

Types `beacon_speed`, `beacon_zone`, `beacon_stopped`, `beacon_silent`,
`beacon_battery`, `beacon_movement` (admin /admin/alertes, formulaire dedie).
Parametre commun `groups` ([] = tous les groupes actifs). Tablettes exclues.
Etat par definition : `cockpit_alert_engine_state._id = "beacon:<slug>"`.

- Temps reel uniquement : un point de plus de 10 min (rattrapage) ne declenche rien.
- Point Anoloc date par `sent_at` (la meme trame est relue a chaque collecte).
- Vitesse : `max_kmh` sur `consecutive` points (2), une alerte par episode, `cooldown_min`.
- Zones : couche polygones Looker (`/api/operator/settings` + `/api/operator/zones`,
  comme la prise de poste), `zone_ids` ([] = toutes), `event` both/enter/exit,
  hysteresis `consecutive` points (bruit GPS ~45 m a l'arret). Premiere observation
  = etat initial, sans alerte.
- Arret prolonge : ancre deplacee au-dela de `radius_m` (50) ; alerte apres `max_min`
  si en ligne ; zones facultatives (seulement si arrete dedans).
- Muette : transition en ligne -> muette (`after_min` force un delai) ; jamais a
  l'activation sur des balises deja muettes.
- Batterie : <= `threshold_pct` hors charge, re-armee a +5 points ou en charge.
- Mouvement : arret -> mouvement (Teltonika : capteur ; Anoloc : statut / vitesse).
- `actionData` porte `lat`/`lng` (bouton localiser), `device_id`, `label`, `group`, `source`.

## Mur TV de suivi GPS (`/balises-mur`, `balises_mur.py`)

Mis en place le 26/10/2026. Page manager, aussi ouverte aux afficheurs TV du portail
(`PAGES_AFFICHEUR` : `/balises-mur`, `/api/balises/mur`), registre `balises_mur`, menu Ecrans TV.

- `?groupes=a,b,c` : 1 a 3 ids de `beacon_groups`, ordre conserve (`lire_groupes`, 3 au plus).
  Groupe inconnu, desactive ou non visible : ignore et signale (`ignores`, bandeau du panneau).
  Sans parametre : dernier choix du navigateur (localStorage `balisesMur.groupes`), sinon
  selecteur. Le bouton reglage (en-tete) change le choix et reecrit l'adresse (a mettre en favori).
- `?event&year` facultatifs : tablettes Field rattachees aux groupes (comme `/anoloc/live`).
  `?fond=ortho` : vue satellite IGN (ortho, secours Esri) ; `?fond=plan` ou absent : carte OSM
  passee en sombre. Choisi dans le selecteur avec les groupes ; sans `?fond=` : dernier
  choix du navigateur (localStorage `balisesMur.fond`). Changement a chaud, sans recharger.
- Visibilite : `anoloc._get_user_visible_groups` (un afficheur, sans groupe Cockpit, voit tout).
- `etat_mur` : en ligne / GPS par `balises_core.device_state` ; en mouvement = en ligne et
  vitesse >= 5 km/h (ou capteur Teltonika) ; trainee = `anoloc_positions` des 10 dernieres
  minutes (60 points max par balise, balises en ligne seulement, jamais recalee : affichage brut).
- Front : rafraichi toutes les 5 s, marqueurs glisses (animation 1,6 s, saut > 2 km sans
  animation), recadrage seulement si une balise en ligne sort du cadre ou si l'ensemble change,
  panneau = un bloc par groupe : en ligne / total et en mouvement, une ligne par balise en ligne
  (en mouvement d'abord, par vitesse), puis une ligne grisee par balise muette depuis moins de 12 h
  (a surveiller), les plus anciennes / jamais vues comptees en pied de bloc. Trop de lignes :
  pages equilibrees qui tournent toutes les 8 s (tous les blocs ensemble, points + "1-12 sur 23"),
  jamais de balise cachee. Hauteur d'un bloc selon ses lignes, plafonnee (un gros groupe pagine).

### Choix publies pour le portail : collection partagee `tv_choix`

Le portail TITAN (meme base Mongo) propose les groupes a l'affectation d'un ecran et remplit la
variable `{groupes}` de l'adresse du catalogue TV (`https://cockpit.lemans.org/balises-mur?groupes={groupes}`).
Document `_id: "cockpit:groupes_balises"` : `app_key` "cockpit", `variable` "groupes", `libelle`,
`aide`, `min` 1, `max` 3, `separateur` ",", `options [{valeur, libelle, couleur, icone, nb}]`
(groupes actifs, ordre de la configuration), `maj_le`. Ecrit par `balises_mur.publier_choix_tv` :
au demarrage (force), a l'enregistrement de Configuration > Balises GPS (force), et par le mur au
plus toutes les 60 s s'il a change. Cockpit ne lit jamais ce document : il fait foi pour le portail.
Second document `_id: "cockpit:fond_balises"`, meme ecriture : `variable` "fond", `min` 1, `max` 1,
`defaut ["plan"]`, `options` plan (Carte, OSM sombre) / ortho (Satellite IGN), sans `nb`. Adresse du
catalogue : `.../balises-mur?groupes={groupes}&fond={fond}`.

## A faire / a verifier

- Mapping AVL de l'ATC700 (`codec8.IO_MAP`) : confirmer avec l'AVL ID list
  (`pcb.temperature`, `custom.param.1600` inconnus). Les IO bruts sont gardes.
- Cadence ATC700 reglee le 13/10/2026 : 15 s en mouvement, 3 min a l'arret ->
  mettre `period_move_s` = 15 et `period_stop_s` = 180 (Source Teltonika).
- Codec 12 (commandes a distance) : non implemente.
- UDP : non implemente (balises en TCP).
