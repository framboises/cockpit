<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Momentus Elite — réservations commerciales (synchro + lieux)

Momentus Elite (VenueOps) est l'outil de réservation des séminaires, réceptifs et hospitalités vendues. Instance **EU** (`auth-api.eu-venueops.com/token`, `api.eu-venueops.com`), identifiants **lecture seule** `MOMENTUS_CLIENT_ID` / `MOMENTUS_CLIENT_SECRET` (niveau Machine pour la tâche SYSTEM). Jeton ~23 h.

- **Synchro** `momentus_sync.py` (tâche `scripts/install_momentus_task.ps1`, toutes les heures à HH:05) : incrémental sur `lastModifiedOn` (recouvrement 15 min) + fonctions J-2/J+45 ; import complet automatique si le dernier a plus de 20 h (seul moyen de voir une suppression). N'émet que des GET et le POST de recherche `/events/all`. Rien n'est jamais supprimé : `_sync.deleted_at`. Garde-fou : pas de réconciliation si l'API rend < 90 % de l'existant. Verrou dans `momentus_sync_state`, historique `momentus_sync_runs` (TTL 90 j), `cron_status.json`.
- **Collections** : `momentus_events` (tel que rendu par l'API, espaces réservés inclus), `momentus_functions`, `momentus_venues`, `momentus_rooms`, `momentus_setup`, `momentus_lieux_mapping` (écrite par Groundmaster).
- ⚠️ **`venueIds` est obligatoire** sur `/v1/events/all` ; `query-by-date-range` exige aussi `roomIds` (0 résultat avec une liste vide). Rien avant 2020.
- ⚠️ **Les dates `start`/`end` d'un événement sont une enveloppe** (roulages et formations de février à décembre). Ce qui se passe un jour donné se lit dans `bookedSpaces` ou les fonctions (créneaux horaires).
- ⚠️ **`usageType: moveIn` n'est PAS un montage** : c'est le mode de réservation par défaut (79 % des espaces des séminaires, 91 % des travaux ; le séminaire CPAM de 434 personnes y est en moveIn 8h30-17h). Affiché « réservé ». `event` (exploitation), `moveOut` (démontage) et `dark` (bloqué) sont des choix volontaires. Le vrai pilote de montage est dans `spaceUsageName` du détail `/v1/booked-spaces/{eventId}` (« Montage par Julien », « Régie Max »…), non synchronisé et rempli à ~60 %.
- **Export Cowork** `scripts/export_cowork_activites.py` : JSON des 60 prochains jours (activités Momentus, grands événements Groundmaster, lieux avec coordonnées, résumé par jour, notice des limites) déposé chaque jour dans SharePoint SAFER / `COWORK/activites_site_60j.json` (Graph délégué, application Entra TITAN, cache `msal_cowork_cache.bin`, connexion initiale `--login`).
- ⚠️ `momentus_events` embarque `contactRoles` (nom, mail, téléphone clients). Les API de lecture (`momentus_lieux.bookings_for_rooms`) n'en exposent rien, ni les montants.

### Réservations par lieu de la carte

Choix utilisateur : **information seulement, aucune alerte de conflit**. Pendant un grand événement, la plupart des réservations Momentus sur un lieu sont l'événement lui-même vu côté commercial (partenaire installé dans un Garden), pas un conflit.

- **Trois apps, mêmes routes, mêmes fichiers partagés** (copies identiques, à reporter partout) : `momentus_lieux.py` (Cockpit, `../groundmaster/`, `../looker/`) et `static/js/momentus_lieu.js` (Cockpit, Looker ; `groundmaster/static/js/momentus-lieu.js`).
- **Looker** : `looker/momentus_carte.py` (fabrique de blueprint, GET user) + bouton « Momentus » en haut à droite de la carte (`MomentusLieu.addLayerControl`) avec choix de période : aujourd'hui, demain, 7 j, 30 j, mois en cours, dates libres.
- `momentus_lieux.py` (pur) : `lieu_payload(db, feature_id, from, to, event, year)` → période (dates, sinon montage → démontage du paramétrage, sinon aujourd'hui + 60 j), espaces rattachés, réservations avec phases fusionnées et créneaux horaires.
- Routes `momentus_api.py` (user) : `GET /api/momentus/mapped`, `GET /api/momentus/lieu/<feature_id>`. Mêmes routes dans Groundmaster.
- UI `static/js/momentus_lieu.js` (**copie identique** : `groundmaster/static/js/momentus-lieu.js`) : bouton « Réservations Momentus » dans la popup des lieux de la carte d'accueil (`map_view.generatePopup`), seulement si le lieu a un espace rattaché validé.
- **Calque carte** (bouton `storefront` en bas à droite, `map_view.toggleMomentus`, `GET /api/momentus/carte`) : tous les lieux rattachés ayant au moins une réservation sur la période de l'événement sélectionné, **qu'ils soient activés ou non dans Groundmaster**. La carte normale n'affiche que les lieux activés pour l'événement : sans ce calque, un Garden occupé par un séminaire pendant les 24H Camions (non activé pour l'événement) restait invisible. Violet plein = réservation en cours aujourd'hui.
- **La correspondance espace Momentus → `_id_feature` se fait dans Groundmaster** (`/momentus/lieux`, admin). Seuls les rattachements `valide` comptent : une proposition par nom identique peut être fausse (« Hunaudières » du PEC, une salle, contre le parking HUNAUDIERES).

### Rapport de scans depuis l'archive live (`live_scan_units.py`)

Pour une édition suivie par le contrôle d'accès live (2026 : 24H AUTOS, GPF, LMC, SUPERBIKE, 24H CAMIONS, 24H MOTOS), le rapport de scans est construit depuis l'archive HSH (tx + erreurs + structure) ; l'import Excel (`complet`) n'est que le repli, puis `parking_scans`. `scan_report_build.resolve_units_doc` porte cette priorité, utilisée par la génération, le mapping et l'analyse rédigée.

- **Pseudo `complet` EN MÉMOIRE, jamais écrit** dans `historique_controle` (référence N-1 de la TV, de la montre, des projections). Même contrat que l'import, plus `source: live_controle`.
- **Porte** = gate de l'Area enceinte (628) ; **zone** = l'AREA HSH (toutes ses gates agrégées), exactement la colonne « zone » de l'export. L'Area de la gate prime sur celle du checkpoint (PDA mobiles).
- Catégorie devinée du nom d'Area (`category_source: hsh_area`) ; rattachement par `scan_import.resolve_features` (overrides, récolte, geojson), identique à l'import.
- Créneaux 15 min étiquetés à leur FIN ; `tranche` et `date_utc` sont déjà en heure de Paris : aucune conversion.
- ⚠️ **Refus exclus** via `hsh_archive_erreurs_*` (par checkpoint + 5 min, répartition au prorata si l'archive est incomplète). Les statuts 107 et 133 sont des passages (le compteur les compte).
- **Portes : personnes seules** (véhicules exclus) ; **zones : véhicules inclus** si la catégorie est à flux véhicules. Écart cumulé à l'Excel sur 24H MOTOS 2026 : 18 691 contre 26 982 à 55 079 pour les autres conventions. ⚠️ L'Excel des zones compte aussi les refus (P OUEST 3 105 contre 2 473 valides).
- Parkings foot M1/M2/M3 (Areas 1227/1229/1231) écartés par ID : le match du 19/09 tombe dans la fenêtre de 24H CAMIONS 2026.
- **Mapping d'une édition live** : aucun document réécrit, seulement `scan_feature_overrides`, puis régénération. « Mémoriser » coché = override global, décoché = ciblé sur l'édition. On enregistre l'état complet de l'unité : un override ciblé remplace entièrement le global.
- `/scan-report/available` liste les éditions live même sans rapport (`generated_at: null`). L'en-tête du rapport affiche la source et les conventions.
- 24H MOTOS 2026 live contre import : portes +2,7 % au total, pic au même créneau sur 12 portes ; PORTE CIK +39 % et PORTE NORD VEHICULES +80 % restent inexpliquées (hypothèse : synchro tardive des PDA après l'export).
- Génération (analyse désactivée) : 1,5 s (24H CAMIONS), 1,8 s (24H MOTOS), 6 s (24H AUTOS).
