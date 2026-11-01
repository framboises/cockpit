# Pont Bibler <-> Cockpit : postes en place et consignes sur la carte principale

Date : 01/11/2026. Statut : conception validee en discussion, a relire avant le plan d'implementation.

References : `bibler/docs/refonte/05_CONSIGNES_ET_COCKPIT.md` (modele des consignes, contrat §7, decisions Q24 a
Q28), code Bibler v2 en production (`titan_bibler/consignes`, `qui_est_ou`, `mise_en_poste/paquet.py`,
`integrations/ponts.py`).

## 1. Objectif

Depuis la carte principale de Cockpit, pour un evenement et une annee :

1. voir les postes actifs et les personnels en poste, en direct ou a un jour et une heure choisis ;
2. chercher un poste par numero ou par nom, voir ou il se trouve, sa mission et ses specimens controles ;
3. donner une consigne ecrite a un poste, un secteur, une zone, un departement ou tout l'evenement, permanente,
   pour le shift en cours seulement, ou pour tous les shifts d'un meme format (ex. 10:00-18:00) ;
4. suivre et lever les consignes.

Fonctionnalite accordee ou non par groupe Cockpit. Pas de nouvelle carte dans Cockpit : tout se greffe sur la
carte d'accueil. Une page de liste (categorie Terrain) complete le suivi.

Hors perimetre (phase 3, spec a part) : flux `/api/v1/evenements` vers la centrale d'alertes, creation d'une
consigne depuis une fiche de main courante, citation des consignes dans le briefing de situation.

## 2. Principes

- **Bibler est proprietaire** des postes, de la presence, des specimens et des consignes. Cockpit n'ecrit jamais
  dans les collections de Bibler et ne lit pas `bibler_consignes*` en direct : tout passe par l'API `/api/v1`.
- **Le navigateur ne parle qu'a Cockpit.** Cockpit relaie vers Bibler serveur a serveur (pas de CORS, pas de
  cookie Bibler).
- **Le droit de rediger est tenu par Cockpit** (Q25) : Cockpit verifie le droit de groupe, transmet l'auteur reel,
  Bibler journalise.
- **Reutiliser le code Bibler existant** : les routes `/api/v1` sont des enveloppes fines.

## 3. Cote Bibler

### 3.1 Authentification entrante (jeton de service)

Pendant de `integrations/ponts.py`, nouveau module `titan_bibler/integrations/entrant.py` + decorateur
`@service_requis("postes:lire")`.

- En-tete `Authorization: Bearer <JWT>` ; HS256, secret `BIBLER_PONT_SECRET_COCKPIT` (deja present pour l'envoi
  des constats, meme secret dans les deux sens).
- Claims exiges : `iss=cockpit`, `aud=bibler`, `sub=service:cockpit`, `iat`, `exp` (duree <= 120 s, tolerance
  d'horloge 30 s), `jti`, `capacites` (liste).
- `jti` deja vu dans les 2 dernieres minutes : refus (collection `bibler_pont_jti`, index TTL).
- IP source dans `BIBLER_PONT_IPS_COCKPIT` (adresses ou CIDR, virgules) ; vide = tout refuse. Adresse reelle
  derriere IIS selon la regle deja en place dans Bibler (V9).
- Capacites : `postes:lire`, `consignes:lire`, `consignes:ecrire`.
- Refus : 401 `auth_requise` / `auth_invalide`, 403 `capacite_manquante` / `ip_refusee`. Aucun jeton ni secret
  dans les journaux. Routes exemptees du CSRF (pas de cookie).

### 3.2 Evenement

Parametres `event` (nom tel que Cockpit le connait) et `year`. Resolution par `donnees/evenement` (nom actuel,
ancien nom ou cle stable `event_cle`). Inconnu : 404 `evenement_inconnu`.

### 3.3 Routes `/api/v1` (blueprint `titan_bibler/api_v1/`)

| Route | Capacite | Contenu | Reutilise |
|---|---|---|---|
| `GET /api/v1/postes?event&year&q=&a=` | postes:lire | Postes actifs : numero, affectation, metier, departement, zone, secteur, coords, etat (`pourvu`, `incomplet`, `en_retard`, `vide`, `a_venir`), presents, planifies, formats de shift du moment, nombre de consignes en vigueur. `a` = instant ISO (defaut : maintenant) ; `mode` rendu `reel` (passe ou direct) ou `prevu` (futur) | `qui_est_ou.service.presence` avec perimetre illimite et `maintenant=a` |
| `GET /api/v1/postes/<numero>?event&year&a=` | postes:lire | Fiche : structure, coords, positionnement, mission, chef direct, materiel, specimens controles (libelle, famille, controle scan/visuel, cle du visuel ; accreditations comprises), agents en service (nom, etat, depuis, prevu, format de vacation ; jamais de telephone), vacations du poste dans la journee, consignes qui visent le poste | `mise_en_poste.paquet` (`fiche_du_poste`, `mission`, `chef`, `materiel`, `titres` avec option d'inclure les accreditations), `consignes.service.vise` |
| `GET /api/v1/specimens/visuel/<cle>` | postes:lire | Image d'un specimen (JPEG/PNG), cle opaque rendue par la fiche | mediatheque |
| `GET /api/v1/vacations/formats?event&year&postes=&secteurs=&zones=&departements=&tout=` | postes:lire | Formats de shift (`HH:MM-HH:MM`) des postes de la cible, avec le nombre de vacations | `planbition_assignments` |
| `GET /api/v1/consignes?event&year` | consignes:lire | Liste et taux (lu, transmis) | `liste_pc` |
| `GET /api/v1/consignes/<id>` | consignes:lire | Detail, versions, suivi (qui manque) | `serialiser`, `suivi` |
| `GET /api/v1/consignes/cibles?event&year&q=` | consignes:lire | Aide au ciblage | `cibles` |
| `GET /api/v1/consignes/apercu` (memes champs que la cible) | consignes:lire | Nombre d'encadrants concernes | `destinataires` |
| `POST /api/v1/consignes` | consignes:ecrire | Creer ; en-tete `Idempotency-Key` obligatoire (rejeu = meme reponse) | `creer` |
| `PATCH /api/v1/consignes/<id>` | consignes:ecrire | Nouvelle version ; 409 `version_perimee` | `modifier` |
| `POST /api/v1/consignes/<id>/lever` | consignes:ecrire | Lever | `lever` |

Ecritures : le corps porte `source.auteur = {email, nom, user_id?}` (obligatoire) ; Bibler enregistre
`source.app = "cockpit"` et journalise dans `bibler_journal_ecritures`. `creer`, `modifier`, `lever` recoivent
l'auteur en parametre au lieu de le lire dans `g.utilisateur` (les routes du PC org de Bibler passent l'utilisateur
de la session, comportement inchange).

### 3.4 Formats de shift dans les consignes

Nouveau champ `cible.formats: ["10:00-18:00", ...]`, vide par defaut (= tous les shifts).

- Format d'une vacation : `start_time`-`end_time` de `planbition_assignments`, en heures locales `HH:MM`.
- `normaliser_cible` valide le motif `^\d{2}:\d{2}-\d{2}:\d{2}$` (10 formats au plus).
- `destinataires` : ne retient que les vacations dont le format est dans la liste.
- `pour_terrain` et la mise en poste des agents : une consigne a formats n'est montree qu'a une personne dont la
  vacation du moment a l'un de ces formats. Un encadrant sans vacation retrouvee la voit (repli prudent).
- Le libelle de cible ajoute « shifts 10:00-18:00 ». L'ecran PC org de Bibler affiche le champ ; sa saisie dans
  Bibler n'est pas requise pour cette spec.

### 3.5 Les trois choix de duree (envoyes par Cockpit)

| Choix | `debut` | `fin` | `cible.formats` |
|---|---|---|---|
| Permanente | maintenant | aucune (jusqu'a la levee) | [] |
| Ce shift seulement | debut du shift choisi | fin du shift choisi | [format du shift] |
| Tous les shifts F | maintenant | aucune | [F] |

`fin` absente : `etat` la traite deja comme en vigueur. `_dates_validite` borne aujourd'hui a debut + 1 jour
quand `fin` est vide ; il est etendu aux dates de vacations de l'evenement posterieures au debut (10 jours au
plus), pour que `destinataires` couvre toute la suite de l'evenement.

## 4. Cote Cockpit

### 4.1 `bibler_pont.py`

- `COCKPIT_PONT_URL_BIBLER` (URL interne fixe) et `COCKPIT_PONT_SECRET_BIBLER` (32 caracteres au moins ; meme
  valeur que `BIBLER_PONT_SECRET_COCKPIT`). Absents : fonctionnalite masquee, relais en 503 `bibler_non_configure`.
- Jeton HS256 de 60 s (claims du §3.1), un `jti` par appel.
- Chemins relatifs verifies (pas de `..`, pas de schema), redirections non suivies, reponse de 5 Mo au plus.
- Delais : connexion 3 s, reponse 8 s. Erreur reseau ou 5xx : `BiblerIndisponible`.
- Cache memoire : liste des postes 30 s par (event, year, a arrondi a la minute) ; visuels 24 h ; formats 5 min.

### 4.2 Relais `bibler_postes.py` (blueprint)

`/api/bibler/postes`, `/api/bibler/postes/<numero>`, `/api/bibler/visuel/<cle>`, `/api/bibler/formats`,
`/api/bibler/consignes` (GET, POST), `/api/bibler/consignes/<id>` (GET, PATCH), `/api/bibler/consignes/<id>/lever`,
`/api/bibler/consignes/cibles`, `/api/bibler/consignes/apercu`.

- Lecture : droit `bibler_postes`. Ecriture : `can_publish_consigne` en plus, CSRF Cockpit habituel, refus des
  comptes borne (`_refuser_ecriture_borne`).
- Le relais ajoute `source.auteur` depuis le JWT Cockpit (email, nom) et une `Idempotency-Key` fournie par le
  navigateur (UUID genere a l'ouverture du formulaire).
- Erreurs Bibler relayees avec leur code (`version_perimee`, `cible_requise`, donnees personnelles...) ;
  `BiblerIndisponible` -> 502 `bibler_indisponible`.

### 4.3 Carte principale : `static/js/bibler_carte.js`

Charge sur l'accueil seulement si le groupe a `bibler_postes`. Se branche sur `CockpitMapView.getMap()` ; ajoute
`.cockpit-bibler-ctrl` au groupe oeil (`RIGHT_DATA` de `map_view.js`).

- **Bouton « Personnels en poste »** dans le groupe oeil. Active : couche de marqueurs + barre compacte :
  `[Direct | jour et heure]  [recherche n° ou nom]  [Consignes (n)]`.
- **Marqueurs** : postes actifs, regroupes au dezoom ; couleur par etat (pourvu vert, incomplet ou en retard
  orange, vide rouge, a venir gris) ; badge si consigne en vigueur. Postes sans coordonnees : listes dans la
  recherche, signales « sans position ».
- **Temps** : Direct = actualisation 60 s quand l'onglet est visible. Jour et heure = vue figee, bandeau « presence
  reelle » (passe) ou « prevu » (futur). Evenement et annee : `window.selectedEvent` / `window.selectedYear`.
- **Recherche** : numero exact ou texte (affectation, departement, zone, secteur), suggestions ; choix = centrage
  et ouverture de la fiche.
- **Panneau du poste** (lateral, sur la carte) : numero, affectation, etat et effectif ; chemin de structure,
  chef ; mission et positionnement repliables ; vignettes des specimens (scan / visuel) ; agents en service ;
  shifts du jour ; consignes en vigueur qui le visent ; bouton « Donner une consigne » (si droit).
- **Formulaire de consigne** dans le panneau :
  1. A qui : puces preremplies `Poste N` · `Secteur` · `Zone` · `Departement` · `Tout l'evenement` ;
  2. Quand : `Permanente` · `Ce shift (HH:MM-HH:MM aujourd'hui)` · `Tous les shifts HH:MM-HH:MM` (formats de la
     cible choisie) ;
  3. Titre (120), texte (600), priorite ; options repliees : question de controle, dans le briefing ;
  4. Apercu « environ N encadrants concernes », bouton Publier. Rappel : pas de donnees personnelles.
- **Liste « Consignes (n) »** : en vigueur avec taux de lecture et de transmission ; clic = surlignage des postes
  cibles + suivi (qui manque) ; modifier, lever.
- **Erreurs** : Bibler indisponible = bandeau, derniere couche conservee en grise ; 409 = « modifiee entre-temps »
  et rechargement ; 422 = message de Bibler.

### 4.4 Page Terrain `/consignes-bibler`

Entree `PAGE_REGISTRY` (`id: consignes_bibler`, categorie Terrain, role user, chemins `/consignes-bibler`). Liste
sans carte : en vigueur, programmees, levees, expirees ; detail et accuses ; modifier et lever (avec le droit) ;
« Voir sur la carte » ouvre l'accueil avec la couche active et la cible surlignee (`/?bibler=consigne:<id>`).

### 4.5 Droits de groupe

| Case (gestion des groupes, `edit.html` + `groups.js` + `app.py`) | Defaut | Effet |
|---|---|---|
| `bibler_postes` « Voir les postes Bibler sur la carte » | decochee | Bouton oeil, panneau, lecture du relais |
| `can_publish_consigne` « Donner des consignes » | decochee | Formulaire, modifier, lever (exige aussi `bibler_postes`) |

Admin et super admin : tout. Comptes borne et afficheurs TV : rien. Fonctions `_user_can_view_bibler` et
`_user_can_publish_consigne` sur le modele de `_user_can_treat_declaration`.

## 5. Donnees personnelles et securite

- Aucun telephone ni e-mail d'agent ne quitte Bibler par ce pont ; noms affiches seulement aux groupes autorises.
- Les textes de consignes restent controles par Bibler (telephone, e-mail refuses).
- Secrets uniquement dans l'environnement ; aucun journal ne contient jeton ou secret.
- Toute ecriture est journalisee des deux cotes (Cockpit : journal applicatif ; Bibler : journal des ecritures avec
  `source.app` et auteur).

## 6. Tests

- Bibler : jeton entrant (absent, expire, mauvais `aud`/`iss`, `jti` rejoue, IP refusee, capacite manquante) ;
  chaque route `/api/v1` (evenement inconnu, perimetre illimite, pas de telephone dans les reponses) ;
  `cible.formats` (normalisation, destinataires, `pour_terrain`) ; `fin` absente ; idempotence de la creation.
- Cockpit : `bibler_pont` (claims du jeton, chemins refuses, delai -> `BiblerIndisponible`) ; relais (sans
  `bibler_postes` -> 403, ecriture sans `can_publish_consigne` -> 403, compte borne -> 403, auteur injecte) ;
  traduction des trois choix de duree.
- Recette manuelle sur la carte : direct, heure passee, heure future, recherche, consigne par poste / secteur /
  shift / format, levee, Bibler arrete.

## 7. Configuration a poser

| Application | Variable |
|---|---|
| Cockpit | `COCKPIT_PONT_URL_BIBLER`, `COCKPIT_PONT_SECRET_BIBLER` |
| Bibler | `BIBLER_PONT_SECRET_COCKPIT` (existe), `BIBLER_PONT_IPS_COCKPIT` |

A ajouter au tableau des variables du `CLAUDE.md` de Cockpit et a `docs/claude/bibler.md` (nouveau).

## 8. Decoupage

1. Bibler : authentification entrante, routes de lecture (postes, fiche, visuels, formats).
2. Cockpit : pont, relais en lecture, couche carte (marqueurs, temps, recherche, panneau), droit `bibler_postes`.
3. Bibler : `cible.formats`, `fin` absente, auteur en parametre, routes consignes `/api/v1`.
4. Cockpit : formulaire de consigne, liste « Consignes (n) », droit `can_publish_consigne`, page `/consignes-bibler`.
