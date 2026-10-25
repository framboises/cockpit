<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Portes : page operateur `/doors`, mur TV `/portes-mur` (`portes_etat.py`)

Menu : « Portes » est dans **Surveillance** (outil operateur), « Mur portes » dans **Ecrans TV**.

Un seul calcul, `portes_etat.py` (pur, `db` en argument, comme `presents_etat.py`), trois usages :

| Usage | Route | Contenu |
|---|---|---|
| Page operateur (user) | `/doors?event&year` (+ `porte`, `jour`, `a`, `vue=bilan` en lien direct) | Direct (60 s), Rejeu (jour + quart d'heure), Bilan RETEX ; analyse detaillee d'UNE porte |
| Mur TV (manager, afficheur) | `/portes-mur?event=&year=` (+ `jour`, `a` pour rejouer) | vue generale + alertes, aucune interaction ; dans `PAGES_AFFICHEUR` avec `/api/portes/etat`. **`event` / `year` sont poses par le portail TITAN** sur l'URL de l'afficheur pour choisir l'edition : nom Cockpit ou sigle (`24HM` → `24H MOTOS`, `_portes_nom_evenement`), annee en cours si absente, edition du live-controle sans parametre. Resolus cote serveur et injectes dans la page (`data-event`, `data-year`) |
| API | `GET /api/portes/etat` (`vue=tv` : sans series ni fiches), `/api/portes/bilan`, `/api/portes/editions` | sans event/year : edition du live-controle (`___GLOBAL___.evenement`), sinon `event_courant` |

Sources : `live_frequentation.find_sources` (archive `hsh_archive_tx_*` ou direct `hsh_transactions_agg`, fenetre course −10 j / +3 j), structure HSH (`live_scan_units.structure_maps` / `classify_gate` : porte = gate de l'Area enceinte), refus (`hsh_erreurs` / `hsh_archive_erreurs_*`), `door_security` (bible + planning), `timetable` (vignettes), `pcorg` (fiches).

### Mesure (archives 2026, 24H MOTOS / GPF / SUPERBIKE)

- **En live, 100 % des passages ont un sens** (Entry/Exit de l'Area traversee). Le sens « Autre » de l'import Excel 2024 n'existe plus.
- **Aucune porte de l'enceinte n'a de mode fixe** : ~50/50 entrees/sorties sur une edition. Le mode bascule dans la journee par **reaffectation des appareils** (Porte Nord pietons, GPF dimanche : 2 % de sorties et 10 appareils en entree a 9 h, 96 % de sorties et 11 appareils en sortie a 14 h). A sens unique : parkings, musee, quelques portes vehicules.
- Debit d'un appareil (p99 par 5 min) : PDA ~790/h en entree, ~1 020/h en sortie ; tripode ~820/h en entree, ~900/h en sortie. Les constantes restent celles du detecteur (tripode 900, PDA 650).
- Refus : 3 a 7 % des scans d'une porte (Porte Nord bis 15 %, Porte Nord vehicules 10 %).

### Regles

- **Mode** sur 15 min glissantes : entree si sorties < 20 %, sortie si > 80 %, mixte entre les deux. Moins de 10 passages : le sens des appareils decide (`calme`). Aucun passage depuis 30 min : `fermee`. Chaque appareil recoit son sens (E, S, ES) sur la fenetre.
- **Capacite par sens** : un appareil compte dans un sens **au prorata de ses passages** dans ce sens. Entree : la charge qui compte est celle de la **securite** (agents prevus × 350/h, `door_security`) quand la porte a des postes, sinon celle des appareils en entree. Sortie : appareils en sortie, jamais la securite.
- **Niveau** : seuls les sens autorises par le mode ET au-dessus de 300/h comptent (`sens_critique`) — 3 sorties sur un appareil d'entree donnaient 204 %. Vigilance 80 %, saturation 100 %, `saturation_prevue` si la prevision 60 min atteint 100 % a la capacite PREVUE (fin de vacation).
- **Passages valides** : `compter()` lit `entrees_valides` / `sorties_valides` (collecteur depuis le 25/10/2026, statuts 0, 107, 133) ; sinon refus par sens de l'archive des erreurs (`live_scan_units.valid_counts`), au prorata a defaut. ⚠️ `entrees` / `sorties` des tranches comptent les refus : ne jamais les lire comme des passages.
- **Sorties = borne basse** a l'exode (controle leve) : 24 483 sorties scannees pour 100 951 entrees le dimanche du GPF 2026.
- Creneaux 15 min de ce module **etiquetes a leur DEBUT** (l'import Excel l'est a sa fin, converti a la lecture du N-1). Tout en heure de Paris naive ; `tranche` est deja l'heure de Paris.

### N-1 et prevision

`charger_n1` : la meme porte (`norm_porte`), edition N-1 decalee d'un nombre entier de semaines (`decalage_n1`). Sources dans l'ordre : archive live N-1 (5 min, par sens, des 2027 pour N = 2027), `historique_controle{complet}` (15 min, par sens), `historique_controle{portes}` (horaire, **tous sens confondus** → `portes_total`).

Prevision = debit courant × rapport N-1 futur / maintenant, borne a ×3, N-1 >= 120/h (methode du detecteur). ⚠️ **Aucune prevision sur un N-1 `portes_total`** : sa vague de sortie a l'exode passait pour une ruee a l'entree (27 000/h a 16 h sur la Porte Nord). Le graphe l'affiche a part (« N-1 tous sens »). En 2026, la plupart des editions n'ont donc pas de prevision ; elle arrive avec les archives 2026 comme reference.

### Signaux

`pda_renfort` : un PDA hors UAM scanne sur une porte qui a eu des tripodes dans la journee (debordement). `uam` : appareils vus sur la gate UAM. Fiches : `pcorg` flux / securite du jour dont le texte contient tous les mots distinctifs de la porte (`door_security._tokens`), la porte la plus specifique seulement. Vignettes : celles dont le lieu ou l'activite designe une porte (`match_gate`, ex. « Bascule controlee - Tribune 08 PANORAMA ») et le programme general.

### Bilan RETEX

Par jour et par porte : entrees, sorties, vehicules, pics 15 min, minutes par mode, charge securite max et temps au-dessus de 100 % (seulement au-dessus de 300/h : la nuit, 1 agent de garde donnait 122 % a 1 h), agents max, temps de renfort PDA, refus. ~0,4 s pour une edition.

⚠️ Les charges de securite au-dessus de 200 % (Porte Nord, Porte Est le dimanche matin) viennent du **planning**, qui ne reflete pas les renforts reels (cf. `alertes.md`). Ce n'est pas un defaut de calcul.

### Autres corrections liees (25/10/2026)

- `door_saturation_forecast` (mode scan) : capacite **dans le sens surveille** (appareil au prorata de ses entrees), debits en **passages valides** (live et N-1 archive).
- `/api/live-controle/debit-gates` : la « derniere heure » comparait un vrai UTC a `tranche` (heure de Paris etiquetee UTC) et additionnait 3 h l'ete. `active-checkpoints` : jour a minuit heure de Paris.
