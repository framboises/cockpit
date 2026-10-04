<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Musee des 24 Heures (bloc autonome de l'accueil)

**Autonome du live-controle et de Waze.** Ne lit ni n'ecrit `data_access` (`___GLOBAL___`), `hsh_structure`, `hsh_transactions_agg`, ni `live_controle_actif`. N'ecrit que `musee_*` (dont `musee_site_*`) et `cockpit_settings._id="musee"` (depuis l'onglet Musee). Lit les archives `hsh_archive_structure_*` (relations parent / enfant, lecture seule) et, pour le perimetre « Site - visites libres », les `parametrages` via `event_courant` (jours publics SAISON, fenetres des epreuves).

```
tache "Cockpit - Collecte Musee" (5 min, 24 h/24, scripts/install_musee_task.ps1)
  -> scripts/musee_collect.bat -> scripts/musee_collect.py
       musee_borne.py (fonctions reseau de live_controle.py, connexion TCP propre)
       borne HSH 192.168.2.10:5205 -> musee_releves (compteurs) + musee_passages (transactions de l'Area)
       -> musee.save_day -> musee_jours
GET /api/musee/state (musee_api.py, role user + bloc widget-musee) -> static/js/musee_block.js (poll 60 s)
/live-controle onglet Musee (static/js/musee_admin.js, admin) -> /api/musee/config|structure|test
```

### Configuration : AUCUNE valeur en dur

`musee.py` ne contient ni identifiant Handshake, ni nom de lieu, ni horaire. Tout est dans `cockpit_settings._id="musee"` (schema 2), edite dans **/live-controle, onglet Musee** (lien direct `/live-controle#musee`) :

| Champ | Contenu |
|---|---|
| `enabled` | collecte + bloc (false : collecteur sort, bloc masque) |
| `horaires` | `ouverture`/`fermeture` par defaut, `semaine` {`lun`..`dim`: `{ferme:true}` ou `{ouverture,fermeture}`, absent = defaut}, `exceptions` [{`date`, `ferme` ou `ouverture`+`fermeture`, `libelle`}] |
| `area` | {id, nom} : Area HSH = perimetre des visiteurs |
| `gates` | portes de l'Area dont le compteur est releve (informatif) |
| `checkpoints` | [{id, nom (borne), `libelle` (nom affiche), `inclus`, `mobile`}] |
| `transactions` | collecte des passages (heure exacte) |
| `releve_perime_min` | seuil « releve perime » (defaut technique 15) |
| `statuts_passage` | statuts comptes comme passage (defaut technique 0/107/133) |
| `maj` / `migration` | qui / quand ; `migration.v1` = copie des champs du schema 1 |

- **Horaires effectifs** (`musee.horaires_du_jour`) : exception > jour de semaine > defaut. Ils pilotent `statut_ouverture`, la **base du compteur** (dernier releve avant l'ouverture DE CE JOUR ; un jour ferme : ouverture par defaut) et l'axe de la courbe horaire. Chaque date de comparaison (S-1, N-1) garde ses propres horaires.
- **Sans document ou sans Area / horaires** : `configure=false`. Le collecteur logue « musee non configure » et sort (rc 0) ; `/api/musee/state` rend `statut: "a_configurer"`, le bloc affiche « a configurer » (+ lien pour un admin). Le collecteur ne cree JAMAIS le document.
- **Checkpoint exclu** (`inclus=false`) : compteur non releve, absent de la liste du bloc. Les visiteurs restent ceux de l'Area (un passage de l'Area par un checkpoint exclu compte toujours, et apparait sous son nom borne dans la repartition).
- **Migration** `scripts/musee_migrate_config.py` (idempotente, `--dry-run`, `--db`) : schema 1 -> 2, faite en prod le 01/10/2026 (10:00-19:00, Area 511, porte 512, 1208/1209/1210, 1040 mobile, aucune date fermee). `musee.normalize_config` lit aussi un doc v1 non migre (memes locations).

### Routes (musee_api.py, blueprint deja enregistre, CSRF actif)

- `GET /api/musee/config` (admin) : formulaire + `etat` (derniere collecte / dernier releve / derniere erreur / locations en echec, `musee.etat_collecte`) + horaires du jour.
- `PUT /api/musee/config` (admin) : `musee.validate_config` STRICT (HH:MM, fermeture > ouverture, dates ISO uniques, jours `lun..dim`, ids numeriques **existant dans la structure avec le bon type**, au moins un checkpoint inclus, seuil 1-1440, statuts numeriques). 400 + `erreurs[]` sinon.
- `GET /api/musee/structure` (admin, cache 1 h, `?refresh=1`) : liste a plat = **Inquiry Counter global de la borne** (`musee_borne.lire_inventaire`, meme requete que `live_controle.executer_inventaire` SANS son ecriture `hsh_structure`) ; borne injoignable -> archives. Relations Area > Gate > Checkpoint = **toutes les archives `hsh_archive_structure_*`** (l'inventaire est a plat) + passages musee des 60 derniers jours (`gate_id`/`area_id` stockes depuis le 01/10/2026). Checkpoint vu sous plusieurs Areas selon les editions -> `mobile_suggere`. `checkpoints` = tous les checkpoints connus (ajout manuel d'une installation pas encore archivee).
- `POST /api/musee/test` (admin) : `musee_borne.tester` = dry-run pour les valeurs du formulaire (compteurs + passages de la derniere heure, 5 pages max) + apercu du jour recalcule (lecture seule). Rien n'est ecrit.

### Structure HSH (constat 2026, PAS dans le code)

Area 511 `VISITES MUSEE` (compteur 523) ; Gate 512 `ENTREE MUSEE` ; checkpoints `TRI-MUS-55/56/57` (1208/1209/1210) ; `TRI-MUS-19` (304) aux editions SUPERBIKE / 24H MOTOS, absent de la borne aujourd'hui ; `PDA.3.11` = Checkpoint **1040**.

- ⚠️ **Compteurs ENTREE SEULE** : exits = 0, `current` = cumul jamais remis a zero (~105 000 au 01/10/2026). Le chiffre utile = **visiteurs du jour**, jamais un « presents ». `FirstEntriesDay` est aussi un cumul, inutilisable.
- ⚠️ **Le PDA.3.11 est MOBILE** : rattache au musee aux 24H AUTOS 2026, au HELPDESK de l'enceinte (Area 628) aux 24H MOTOS. Ses compteurs ne sont pas que du musee : les passages sont filtres sur l'**Area**, jamais sur le checkpoint (`musee.passage_doc`).
- **Borne sans session ni jeton** : chaque connexion TCP est independante (meme TELEGRAM_ID que le live-controle, deja le cas de `scan_import_hsh.py`). Le collecteur ouvre la sienne (2 essais), ~1 s de dialogue. Inquiry Transactions = TOUTE la borne (pas de filtre par lieu) : fenetre depuis le dernier passage moins 10 min (dedoublonnage par `_id = transaction_id`), From/To en **heure locale Paris** gardes a chaque page, plafond 100 pages (`tx_retard`, reprise au dernier passage vu, pas de trou). `live_controle` cree un client Mongo a l'import : `musee_borne` l'importe a l'appel seulement.

### Visiteurs du jour (`musee.compute_day`)

1. **Compteur** si un releve existe AVANT l'ouverture du jour : somme des increments de l'Area depuis ce releve. Remise a zero = chute d'`entries` (le nouveau releve compte en entier), lecture fautive isolee ignoree (meme regle que `presents_etat.counter_baseline`).
2. Sinon **transactions** si la couverture du jour part de minuit (`musee_jours.tx_depuis == "00:00:00"`, pas de `tx_retard`) : passages de l'Area dont le statut est dans `statuts_passage` (statuts que Skidata compte, cf. `scan_import_hsh.STATUTS_PASSAGE`). Les refus (106, 117...) ne comptent pas.
3. Sinon `compteur_partiel` (« depuis HH:MM seulement »), affiche tel quel.

Courbe horaire et repartition par checkpoint : transactions (heure exacte) quand la couverture est complete, sinon increments des compteurs attribues a l'heure du releve. Verifie le 01/10/2026 : +1 au compteur entre deux releves = +1 passage valide.

- **Releve perime** : dernier releve > `releve_perime_min` -> `releve_perime: true`, l'UI grise la valeur et affiche « releve perime », **jamais 0** ; sans donnee : `--`.
- **Comparaisons** : S-1 (meme jour de semaine) et N-1 (meme date) depuis `musee_jours` (total) + `compute_day(until=HH:MM)` (a meme heure, bornee a la fermeture du jour). `null` tant que la date n'a pas de donnees.
- **Retention** : TTL 400 j sur `musee_releves.ts`, `musee_passages.ts`, `musee_site_releves.ts`, `musee_site_passages.ts` (N-1 a la meme date). `musee_jours` et `musee_site_jours` sans TTL.
- **Bloc** `widget-musee` dans `BLOCK_REGISTRY` (colonne droite) : un groupe a liste `allowed_blocks` explicite ne le voit qu'une fois coche dans sa fiche ; admin le voit toujours. Visible meme si `live_controle_actif` est faux. Payload : `horaires_jour` (pied « Ferme aujourd'hui (Noel) » ou horaires + libelle d'exception), `area_nom`. Une disposition de groupe anterieure au bloc le range sous le Controle d'acces (`musee_block.js`).
- **Collecteur** : verrou `logs/musee_collect.lock` (msvcrt, libere a la mort du process), fin par `os._exit` (le client Mongo cree a l'import de `live_controle` garde des threads), logs `logs/musee_collect-YYYYMMDD.log`. Entre 00:00 et 00:15, l'agregat de la veille est fige. Une config modifiee est prise en compte a la collecte suivante (5 min max), sans redemarrage.
- **Tests** : `tests/test_musee_config.py` (horaires par jour, base du compteur, validation, migration, structure).

### Site - visites libres (second perimetre, 02/10/2026)

Un billet musee donne acces libre au site : les visiteurs entrent ET sortent par des checkpoints choisis dans l'onglet Musee (n'importe quelle Area). En 2026 : `TRI-NORV-27/28` et `TRI-NORV-PMR-29` (749/750/751, Gate 937 PORTE NORD BIS, Area 628 ENCEINTE GENERALE) - **donnees, pas code** (cle `site` amorcee le 02/10/2026 par `scripts/musee_migrate_config.py --site-checkpoints 749,750,751 --site-marge 30`, idempotent : ne touche plus un doc qui porte `site`).

- **Cle `site`** de `cockpit_settings.musee` : `{enabled, marge_min (0-240, defaut 30), checkpoints: [{id, nom, libelle, inclus, sens: mixte|entree|sortie}]}`. `sens` = indication utilisee SEULEMENT si la transaction n'a pas de sens (constat 02/10 : toutes en ont un). PUT sans cle `site` = inchangee. `validate_config` : ids Checkpoint de la structure, au moins un inclus si active.
- **Quand compter** (`musee.site_gate` / `site_gate_db`) : jour public du parametrage **SAISON** (`event_courant.saison_public_days`, saisi dans GroundMaster, `globalHoraires.dates` openTime/closeTime/is24h) ET aucune epreuve active (`event_courant.is_saison_public_day` : montage -> demontage, bascule). ⚠️ Ces tripodes servent de **delestage de la porte Nord pendant les epreuves** : y compter serait compter des spectateurs. Fenetre = horaires -/+ `marge_min`, bornee a 00:00-23:59 (is24h = journee entiere). Raisons : `desactive`, `a_configurer`, `pas_jour_public`, `epreuve`, `saison_illisible`, `avant`, `ouvert`, `termine`.
- **Collecteur** (meme tache, meme connexion) : un jour de visites libres hors epreuve, releve les compteurs du site **toute la journee** (le releve precedant le debut de fenetre = base) dans `musee_site_releves`, et filtre la MEME lecture de transactions (`musee_borne.lire_transactions`, plusieurs filtres) sur les **ids de checkpoint** (jamais l'Area : 628 couvre tout le site) -> `musee_site_passages` (`sens` E/S/?, `sens_source`). Fenetre transactions propre (`musee_site_jours.tx_depuis/tx_jusqu_a/tx_retard`), lecture unique depuis le plus ancien des deux curseurs. Sinon : log « Site visites libres : pas de collecte (raison) », rien n'est ecrit. `--dry-run` lit quand meme les compteurs du site (« lecture de controle »).
- **Calcul** (`compute_site_day`) : entrees / sorties = increments `entries` / `exits` depuis la base (meme `cumul_compteur` robuste que le musee), limites a la fenetre ; repli transactions si leur couverture part d'avant la fenetre, sinon partiel annonce. `presents_now = max(entrees - sorties, 0)`, `presents_max` = pic de la serie (entrees et sorties d'un meme releve nettees ensemble). Agregat `musee_site_jours` (entrees, sorties, presents_now, presents_max, par_heure {HH: {entrees, sorties}}, par_checkpoint, source, fenetre).
- ⚠️ Sur ces checkpoints `current` = entries + exits (pas des presents), et ils voient du passage hors visites libres (personnel : 47 transactions le 02/10 avant 11 h). Les jours publics comptent donc aussi ce passage.
- **API** : `/api/musee/state.site` {visible, actif, dans_fenetre, raison, raison_libelle, horaires, fenetre, entrees, sorties, presents, presents_max, par_heure [{heure, entrees, sorties}], par_checkpoint, releve_perime...}. `visible` = active ET (jour de visites libres OU donnees du jour) ; valeurs `None` sans donnee (le bloc affiche `--`). Erreur de calcul du site -> `{visible:false, erreur}`, jamais d'impact sur le musee. `GET /api/musee/config.site` : prochains jours SAISON (avec l'epreuve qui les recouvre), etat du jour, derniere collecte. `POST /api/musee/test` lit aussi les compteurs du site (`site_compteurs`).
- **Bloc** : partie compacte « Site - visites libres » sous les chiffres du musee (entrees, sorties, presents, pic, mini-courbe entrees/sorties). Le musee `enabled=false` masque tout (bloc et collecte).
- **Tests** : `tests/test_musee_site.py`.

### Affichage seulement en SAISON

Reglage de la **fiche groupe** (page /edit, section Blocs) : bascule `SAISON` sur chaque ligne de bloc -> `cockpit_groups.saison_only_blocks`. Generique (tout bloc), pas seulement le musee. Admins : fiche du groupe Admin. Plusieurs groupes : reserve a SAISON des qu'un groupe le demande. `main.js applySaisonOnlyBlocks` pose la classe `block-hidden-saison` (display none !important) sur le bloc et son onglet mobile quand la selection n'est pas SAISON. Affichage seulement, aucun droit retire.
