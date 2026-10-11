# Operateurs : prise de poste, zones, metiers, ciblage des alertes

Statut : PLAN valide (11/10/2026), implementation non commencee.

## Besoin

Un groupe peut etre marque "Operateur". A son arrivee sur Cockpit, un membre
de ce groupe doit prendre son poste : evenement, zone(s), metier. Ce poste :

- fixe l'evenement affiche pendant toute la duree du poste ;
- cree automatiquement une fiche Information "Prise de poste" (cloturee) dans
  la main courante de l'evenement choisi ;
- cible les alertes : un operateur ne recoit que les alertes de son
  evenement, de ses zones et de son metier (en plus du filtre par groupe).

REGLE : le poste ne restreint RIEN d'autre. L'operateur garde acces a tout
ce que ses groupes lui donnent : toutes les fiches de la main courante, toutes
les pages, toutes les zones. Seules les ALERTES sont filtrees. Le poste sert
aussi de contexte pour l'assistant IA plus tard.

## Decisions arbitrees

| Sujet | Decision |
|---|---|
| Option de groupe | Case `operateur` sur `cockpit_groups` |
| Zones | Une couche polygones de Looker, choisie dans Configuration parmi `categories_config.categories` (geojson, polygon/multipolygon). Choix de la couche reporte : menu de toutes les couches eligibles |
| Synchro zones | Aucune copie : lecture / ecriture directes dans la collection de la couche (meme base que Looker). Zones designees par `properties._id_feature`, jamais par leur nom |
| Metiers | Lus en direct dans `bible_structure` (Bibler) : `_id` (ex. `metier_securite`) + `metier` (ex. `SECURITE`). Niveau metier seulement |
| Multi-zone | Oui, plusieurs zones par poste |
| Metier et alertes | Chaque definition d'alerte declare ses metiers (`metiers: []` vide = tous) |
| Zone non couverte | Operateurs de la zone couverte la plus proche, quel que soit le metier |
| SOS / critiques | Jamais filtres par zone (filtre groupe seulement, comme aujourd'hui) |
| Duree du poste | Jusqu'a ce que l'operateur quitte (bouton "Fin de poste" ou deconnexion) |
| Evenement | Choisi a la prise de poste parmi les evenements en cours, verrouille tout le poste |
| Main courante | Fiches Information cloturees des leur creation : "Prise de poste", "Changement de zone", "Fin de poste" |
| Changement de zone | Autorise en cours de poste, sans fin de poste |
| Zone couverte, autre metier | Operateurs de la zone d'abord (tout metier), puis zone couverte la plus proche |
| Acces | Aucune restriction : seules les alertes sont filtrees, l'operateur voit toutes les fiches |
| Reprise | Poste ouvert = repris automatiquement a la reconnexion, sans nouvelle prise de poste |
| Presence | JAMAIS deduite (un operateur peut ne rien toucher pendant une heure). Seule une pause DECLAREE ("Je pars en pause") retire ses zones du routage |
| Fiches et Friday | Sous-classification dediee "Service" sous PCO.Information. Rien n'est exclu en dur : l'admin decide dans les regles Friday (categorie / sous-classification) |

## Modele de donnees

### `cockpit_groups` (existant)

- `operateur: bool` (defaut `false`). Un utilisateur est operateur si l'un de
  ses groupes l'est. Admin / super admin : jamais soumis a la prise de poste.

### `cockpit_settings._id = "operateurs"` (nouveau)

```
{
  zones_collection: "<collection_name Looker>" | null,
  zones_label_field: "<propriete affichee comme nom de zone>",
}
```

Tant que `zones_collection` est vide, la prise de poste ne demande que
l'evenement et le metier, et aucun filtre de zone ne s'applique.

### `cockpit_operator_posts` (nouveau)

```
{
  _id, user_id (ObjectId users), email, name,
  event, year,
  zones: ["<_id_feature>", ...], zones_labels: [...],   // labels figes pour l'historique
  metier_id, metier_label,
  status: "active" | "pause" | "ended",
  started_at, ended_at, end_reason: "fin_de_poste" | "logout" | "admin",
  pause_since,                         // null hors pause
  history: [{at, type: "prise" | "zones" | "pause" | "retour" | "fin", fiche_id, ...}]
}
```

Index unique partiel `{user_id: 1}` sur `status in [active, pause]` : un seul
poste ouvert par utilisateur (plusieurs onglets partagent le meme poste).

### `cockpit_alert_definitions` (existant)

- `metiers: ["metier_..."]`, vide = tous les metiers.

## Prise de poste (ecran)

1. `index()` : si l'utilisateur est operateur et n'a pas de poste actif, la
   page s'ouvre sur une fenetre bloquante (pas de fermeture, pas d'acces aux
   blocs derriere).
2. Etapes : evenement (liste `EC.payload(db)` : epreuves actives, sinon
   SAISON), puis zone(s) sur une petite carte de la couche + liste a cocher,
   puis metier.
3. `POST /api/operator/post` cree le poste ET la fiche de prise de poste.
4. Poste actif : l'evenement est impose a `window.selectedEvent/Year`, le
   selecteur d'evenement est desactive, `localStorage.cockpit_sel` est ignore.
   Un bandeau en tete rappelle "Poste : <zones> - <metier> - <evenement>" avec
   le bouton "Fin de poste".
5. Changer de zones (et/ou de metier) : bouton "Changer de zone" du bandeau,
   sans fin de poste, fiche "Changement de zone" (cloturee).
   Changer d'evenement = fin de poste puis nouvelle prise de poste.
6. Aucun filtre sur la main courante ni sur les pages : l'operateur voit tout
   ce que ses groupes lui donnent.

## Reprise du poste (pause, coupure, navigateur ferme, autre poste de travail)

Le poste est rattache au COMPTE, pas au navigateur ni a l'onglet :

- tant que le poste est ouvert, toute ouverture de Cockpit avec ce compte
  (meme navigateur ou autre poste de travail, apres une coupure reseau, un
  plantage, un navigateur ferme) reprend le poste : pas de fenetre de prise
  de poste, pas de nouvelle fiche, simple message "Poste repris" ;
- fermer le navigateur ne change RIEN au routage : ses zones restent
  couvertes, ses alertes l'attendent a la reouverture ;
- seuls "Fin de poste", la deconnexion volontaire ou une cloture admin
  ferment le poste.

## Pause (declaree, jamais deduite)

Cockpit ne deduit JAMAIS l'absence d'un operateur : aucune inactivite, aucun
navigateur ferme ne le retire du routage (il peut ne rien toucher pendant une
heure si rien ne se passe sur le terrain).

- Bouton "Je pars en pause" dans le bandeau : statut `pause`, ses zones ne
  sont plus considerees comme couvertes, ses alertes de zone partent vers les
  autres operateurs (meme zone, sinon zone couverte la plus proche).
- Pendant la pause, il ne recoit que les alertes non filtrees par zone
  (critiques / SOS, alertes sans localisation) et le repli "tout le groupe".
- Bouton "Je reprends" (ou ouverture de Cockpit, avec proposition de
  reprendre) : statut `active`, zones de nouveau couvertes.
- Fiches "Pause" et "Retour de pause" (cloturees), comme les autres
  (proposition, a confirmer).

## Fiche main courante

Creee cote serveur par une fonction interne, PAS via `/api/pcorg/create` :
l'operateur n'a pas forcement le droit de creer des fiches ni la categorie
Information dans ses `allowed_categories`.

- `category: "PCO.Information"`, `event/year` du poste, operateur = l'utilisateur.
- `content_category.sous_classification` = "Service" : sous-classification
  creee automatiquement (si absente) dans `pcorg_config.pcorg_lists`
  `sous_classifications["PCO.Information"]`. Elle permet de filtrer ces fiches
  partout ou on filtre deja par sous-classification (regles Friday, vues).
- `text` : "Prise de poste - <Prenom Nom> - Zones : A, B - Metier : SECURITE".
- `_id` deterministe (uuid5 sur l'id du poste) : un double clic ne cree pas
  deux fiches.
- Marqueur `operator_post_id` + tag `poste_operateur`.
- Cloturee des sa creation (`close_ts` = creation, operateur de cloture =
  l'utilisateur) : simple trace.
- Meme mecanisme pour "Changement de zone" (anciennes -> nouvelles zones) et
  "Fin de poste" (duree du poste).
- Pas de dispatch automatique (`DA.maybe_auto_start` non appele).
- Applications liees (Friday) : AUCUNE exclusion en dur. Ces fiches passent
  par les regles d'envoi comme les autres ; l'admin choisit par categorie /
  sous-classification (ex. envoyer Information sauf "Service").

## Fin de poste

- Le poste reste ouvert jusqu'au bouton "Fin de poste", a la deconnexion
  volontaire (`/logout_redirect`) ou a une cloture par un admin.
- Vue admin dans Configuration : postes ouverts (en poste / en pause, zones,
  metier, depuis quand), bouton "Cloturer le poste" (cree la fiche de fin).

## Ciblage des alertes

Dans `_get_user_alert_slugs` / `GET /api/active-alerts`, le filtre groupe
existant reste le premier filtre. Ensuite, pour un utilisateur operateur avec
poste actif :

1. Alerte `critical` (dont SOS) : livree (pas de filtre zone ni metier).
2. Alerte d'un autre evenement que celui du poste (quand l'alerte porte un
   `event`) : non livree.
3. Zone de l'alerte inconnue : livree (comportement actuel).
4. Sinon, destinataires = postes `active` (pas en pause) dont l'utilisateur
   est dans les groupes de la definition :
   a. postes couvrant la zone de l'alerte ET metier dans `def.metiers` ;
   b. sinon postes couvrant la zone, tout metier ;
   c. sinon postes de la zone couverte la plus proche, tout metier ;
   d. sinon (aucun poste en service nulle part) : tout le groupe.

Non-operateurs (et operateurs sans poste, cas transitoire) : comportement
actuel inchange.

### Zone d'une alerte

Calculee a la lecture (les producteurs sont multiples, dont un script hors
repo) et mise en cache par alerte :

| Producteur | Source de localisation |
|---|---|
| SOS Field | `actionData.lat/lng` (critique : pas filtre de toute facon) |
| Trafic | `actionData.lat/lon` |
| pcorg_urgency | fiche `gps` si present, sinon `actionData.zone` (nom `area.desc`) rapproche du nom de zone |
| Saturation porte | position de la porte (`3p`, `actionData.door_key`) |
| Intrusions / camera | position de la camera ou centre de la zone de detection |
| Autres | aucune -> livree a tout le groupe |

Geometrie en Python pur (pas de shapely dans le projet) : point dans
polygone (ray casting) et distance point / polygone apres projection
equirectangulaire locale. "Zone la plus proche" = distance minimale entre le
point de l'alerte (ou le centroide de sa zone) et les polygones des zones
couvertes. Couche mise en cache 60 s.

## Configuration (page /config/todos)

Nouvelle section "Zones et metiers" :

- menu de choix de la couche (couches polygones de `categories_config`) et
  de la propriete servant de nom ;
- carte de la couche : renommer / retracer une zone ecrit dans la collection
  Looker (`features.$.properties.*`, `features.$.geometry` par
  `_id_feature`), donc visible immediatement dans Looker ;
- ATTENTION : refuser les couches alimentees par un import automatique
  (sinon l'import ecrase les modifications) ;
- liste des metiers lus dans `bible_structure` (lecture seule, edites dans
  Bibler) ;
- postes actifs.

Groupe : case "Operateur (prise de poste obligatoire)" dans la modale des
groupes (`edit.html`, `groups.js`, `POST/PUT /api/groups`).

Definition d'alerte : liste "Metiers concernes" dans `/admin/alertes`.

## Routes prevues

- `GET  /api/operator/post` : poste actif de l'utilisateur (ou null).
- `GET  /api/operator/options` : evenements en cours, zones, metiers.
- `POST /api/operator/post` : prise de poste (+ fiche).
- `POST /api/operator/post/zones` : changement de zones / metier.
- `POST /api/operator/post/pause` et `/resume` : pause declaree, reprise.
- `POST /api/operator/post/end` : fin de poste.
- `GET  /api/operator/posts` (admin) / `POST /api/operator/posts/<id>/end` (admin).
- `GET/PUT /api/operator/settings` (admin) : couche, propriete de nom.
- `GET  /api/operator/zones` (+ `PUT` d'une zone, admin) : couche avec ids.

## Ordre de realisation

1. Modele + reglages + section Configuration (couche, metiers) + case groupe.
2. Prise de poste : routes, fenetre bloquante, verrouillage evenement, fiche.
3. Changement de zone, pause / reprise, fin de poste, vue admin des postes.
4. Zone d'une alerte (resolution + geometrie) avec tests unitaires.
5. Ciblage dans `/api/active-alerts` + metiers sur les definitions.
6. Edition des zones depuis Cockpit (ecriture dans la couche Looker).

## Limites connues

- Alertes WhatsApp (`whatsapp.groups`) non concernees par le ciblage par zone.
