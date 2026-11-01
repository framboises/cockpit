<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Pont Bibler (`bibler_pont.py`, `bibler_postes.py`, `bibler_carte.js`, `consignes_bibler.js`) — 01/11/2026

Cas d'usage : depuis la carte d'accueil, voir les postes actifs et les personnels en poste (Bibler), donner une consigne ecrite a un poste, un secteur, une zone, un departement ou tout l'evenement, puis la suivre et la lever. Spec : `docs/superpowers/specs/2026-11-01-pont-bibler-consignes-design.md`.

### Principes

- **Bibler est proprietaire** des postes, de la presence, des specimens et des consignes. Cockpit n'ecrit jamais dans les collections de Bibler et ne lit pas `bibler_consignes*` en direct : tout passe par l'API `/api/v1` de Bibler.
- **Le navigateur ne parle qu'a Cockpit** : relais serveur a serveur (`bibler_postes.py`), pas de CORS, pas de cookie Bibler.
- **Le droit de rediger est tenu par Cockpit** : Cockpit verifie le droit de groupe, transmet l'auteur reel (`source.auteur` = email + nom, injecte depuis le JWT, jamais lu du corps), Bibler journalise (`source.app = "cockpit"`).
- **Aucun telephone ni e-mail d'agent** ne quitte Bibler par ce pont (ni dans `/api/v1`, ni dans le relais). Les textes de consignes sont controles par Bibler (telephone, e-mail refuses : 422).

### Architecture

```
navigateur --(JWT Cockpit, CSRF)--> bibler_postes.py (/api/bibler/*) --(jeton de service HS256)--> Bibler /api/v1/*
```

- `bibler_pont.py` : client. Jeton HS256 de 60 s, claims `iss=cockpit`, `aud=bibler`, `sub=service:cockpit`, `iat`, `exp`, `jti` (un par appel), `capacites` (`postes:lire`, `consignes:lire`, `consignes:ecrire`). Chemins relatifs valides (`/api/v1/...`, pas de `..`, pas de schema), redirections non suivies, reponse 5 Mo max, delais connexion 3 s / reponse 8 s. Erreur reseau ou 5xx : `BiblerIndisponible`. Cache memoire des lectures : postes 30 s, formats 5 min, visuels 24 h. `vider_cache()` vide les lectures JSON (pas les visuels) : appele apres **toute ecriture reussie** (POST, PATCH, lever) pour que badge de carte et liste se mettent a jour tout de suite.
- `bibler_postes.py` (blueprint `bibler_bp`) : droits (`peut_voir`, `peut_publier`), relais de lecture, relais d'ecriture, page `GET /consignes-bibler`.
- `static/js/bibler_carte.js` + `css/bibler_carte.css` : couche « Personnels en poste » sur la carte d'accueil (groupe oeil), recherche, panneau du poste, formulaire de consigne, liste « Consignes (n) ». Lien direct `/?bibler=consigne:<id>` : active la couche et surligne la cible.
- `templates/consignes_bibler.html` + `static/js/consignes_bibler.js` : page Terrain `/consignes-bibler` (`PAGE_REGISTRY` id `consignes_bibler`, role user, sidebar `_g_terrain` si `bibler_allowed()`). Liste par etat (en vigueur, programmees, levees, expirees), detail et accuses (`suivi.lignes`, qui manque d'abord), modifier / lever si `bibler_publish_allowed()`, « Voir sur la carte ». Evenement et annee : `cockpit_sel` (localStorage, choix de l'accueil), puis `cockpit_event` / `cockpit_year`, puis `/api/event/current`.
- Cote Bibler : `titan_bibler/integrations/entrant.py` (decorateur `@service_requis`) et `titan_bibler/api_v1/` (routes, `service.py`). **L'IP source est controlee AVANT le jeton** (`ip_admise`, puis `verifier_jeton`) : un appel d'une adresse non admise est refuse en 403 `ip_refusee` sans consommer de `jti`. Puis jeton : `exp - iat > 120` refuse, 30 s de tolerance d'horloge, `jti` deja vu dans les 2 min refuse, capacites. Format des refus Bibler : `{ok:false, erreur, message}`.
- Codes d'erreur de Bibler sur `/api/v1` (verifies dans `api_v1/routes.py`, `consignes/service.py`) :
  - 401 `auth_requise` / `auth_invalide`, 403 `ip_refusee` / `capacite_manquante` : refus du **jeton de service**, jamais relayes tels quels (voir `bibler_refus_service` ci-dessous).
  - 400 : `evenement_requis` (event / year absents), `instant_invalide` (parametre `a`), `corps_invalide`, `auteur_requis` (`source.auteur.email`), `idempotence_requise` (`Idempotency-Key` absente ou > 200 caracteres), et les refus de saisie qui **restent 400** : `titre_requis`, `trop_long`, `valeur_invalide`, `validite_invalide`, `date_invalide`, `question_incomplete`, `statut_invalide`.
  - 422 : **seulement** `cible_requise`, `donnees_personnelles`, `format_invalide` (`ERREURS_422` de `api_v1/routes.py`), plus `idempotence_conflit` ; `format_invalide` et `date_invalide` de l'apercu sont aussi en 422.
  - 404 : `evenement_inconnu`, `poste_inconnu`, `visuel_inconnu`, `consigne_introuvable`.
  - 409 : `version_perimee`, `consigne_levee` (PATCH d'une consigne levee), `deja_publiee` (repasser en brouillon), `requete_en_cours` (idempotence). Lever une consigne deja levee rend 200 (idempotent).

### Routes du relais (`/api/bibler/*`)

- Lecture (`@_lecture` : role user + droit `bibler_postes`) : `postes`, `postes/<numero>`, `visuel/<nom>` (JPEG/PNG uniquement, CSP sandbox), `formats`, `consignes`, `consignes/<id>` (consigne + `suivi`), `consignes/cibles`, `consignes/apercu`. Tout identifiant d'URL est valide avant l'appel (404 sinon).
- Ecriture (`@_ecriture` : role user + `can_publish_consigne`, jamais un compte borne) : `POST consignes`, `PATCH consignes/<id>` (champs `titre`, `texte`, `priorite`, `version`), `POST consignes/<id>/lever`. Erreurs d'ecriture au format `{ok:false, erreur, message}` ; les erreurs Bibler relayees gardent leur corps. Chaque ecriture est journalisee (`Consigne Bibler creee|modifiee|levee <id> par <email>`).
- Erreurs Cockpit : 502 `bibler_indisponible`, 503 `bibler_non_configure`, 403 `droit_insuffisant` (droit de groupe Cockpit, inchange), 502 `bibler_refus_service`.
- **Un 401 / 403 de Bibler n'est jamais relaye** (`_erreur_bibler`) : `static/js/session_portail.js` enveloppe `fetch` et coupe tout l'onglet Cockpit (« reconnectez-vous ») au premier 401 `auth_invalide` / `auth_requise`. Le relais rend 502 `{ok:false, erreur:"bibler_refus_service", message:"Bibler refuse l'acces de Cockpit (configuration du pont)."}` et journalise un avertissement avec la route et le code Bibler (jamais le jeton). Cause : secret different des deux cotes, IP de Cockpit absente de `BIBLER_PONT_IPS_COCKPIT`, horloges decalees. La carte et la page affichent « configuration du pont a verifier ».
- Tout autre 4xx de Bibler est relaye avec son statut et son corps, et journalise (`Bibler <methode> <route> -> <statut> <code>` : route Cockpit et code seulement, jamais le reste du corps).

### Variables d'environnement

| Cote | Variable | Role |
|------|----------|------|
| Cockpit | `COCKPIT_PONT_URL_BIBLER` | URL interne fixe de Bibler. Absente (ou secret absent / trop court) : fonctionnalite masquee (`bibler_allowed()` et `bibler_publish_allowed()` rendent False via `bibler_pont.configure()` : ni oeil sur la carte, ni sidebar, ni formulaire, meme pour un admin), routes du relais en 503 `bibler_non_configure` |
| Cockpit | `COCKPIT_PONT_SECRET_BIBLER` | Secret HS256 du jeton de service, 32 caracteres min. (meme valeur que `BIBLER_PONT_SECRET_COCKPIT`) |
| Bibler | `BIBLER_PONT_SECRET_COCKPIT` | Meme secret (existe deja, sert aussi a l'envoi des constats) |
| Bibler | `BIBLER_PONT_IPS_COCKPIT` | IP source de Cockpit admises (adresses ou CIDR, virgules). Vide : **tout refuse** |

**Meme serveur** : si Cockpit et Bibler tournent sur la meme machine, `127.0.0.1` dans `BIBLER_PONT_IPS_COCKPIT` admet **tout processus local** : seul le jeton (secret) protege alors. Et l'IP vue par Bibler est `request.remote_addr` : derriere IIS / ARR, Bibler ne doit prendre `X-Forwarded-For` que du mandataire de confiance (`wsgi.OPTIONS_MANDATAIRE`), jamais un en-tete forgeable par le client, sinon le filtre d'IP ne vaut rien.

Mise en service : generer un secret de 48 caracteres, le poser des deux cotes, renseigner l'URL et l'IP, redemarrer les deux services, puis cocher les droits dans les groupes et ajouter la page `consignes_bibler`.

### Droits de groupe (Configuration > Groupes)

- `bibler_postes` « Voir les postes Bibler sur la carte » : bouton oeil, panneau, lecture du relais, page `/consignes-bibler`.
- `can_publish_consigne` « Donner des consignes » : formulaire, modifier, lever. **Exige aussi `bibler_postes`** (`peut_publier`).
- Decochees par defaut. Admin et super admin : tout. Comptes borne et afficheurs TV : rien (meme sur la lecture).
- La page est aussi soumise a « Pages accessibles » (`consignes_bibler`).

### Traduction des durees (`consigne_depuis_formulaire`)

Le formulaire envoie `duree.mode` ; Cockpit le traduit en champs Bibler (R11) :

| Mode | `debut` / `fin` envoyes | `cible.formats` |
|------|--------------------------|-----------------|
| `permanente` | **aucun** (Bibler retient l'instant de publication, pas de `fin` : jusqu'a la levee) | `[]` |
| `format` (tous les shifts F) | **aucun** | `[F]` |
| `shift` (ce shift seulement) | `debut` / `fin` ISO avec fuseau de la vacation, `fin > debut`, 24 h max | `[F]` |

Les modes sans `debut` gardent un corps identique d'un rejeu a l'autre, ce qui permet a l'idempotence de rejouer la reponse. Format de shift `HH:MM-HH:MM` (heures 00-23, minutes 00-59 : `99:99-10:00` ou `24:00-02:00` sont refuses en 422 `format_invalide` ; 10 au plus ; `cible.formats` vide = tous les shifts). Un shift qui passe minuit (`18:00-02:00`) finit le lendemain a 02:00. Duree inconnue : 422 `duree_invalide` ; shift incoherent : 422 `shift_invalide`.

### Idempotence

`POST /api/bibler/consignes` exige `X-Idempotency-Key` (400 `idempotence_requise` sinon), transmise a Bibler (`Idempotency-Key`). Le navigateur genere un UUID **a l'ouverture du formulaire** : un double clic sur Publier ou un rejeu apres delai depasse ne cree qu'une consigne. Pendant l'envoi, Annuler est grise ; un formulaire quitte pendant l'envoi (autre poste, panneau ferme) est garde avec sa cle (`formulairesGardes` de `bibler_carte.js`) et rouvert tel quel sur le meme poste : un nouvel essai rejoue la meme creation. Erreurs associees : 409 `requete_en_cours`, 422 `idempotence_conflit` (meme cle, autre contenu).

Cote Bibler (`api_v1/service.py`, collection `bibler_pont_idempotence`, TTL 24 h) : la cle est reservee (`en_cours`) avant l'ecriture ; seule une reussite est memorisee, un echec libere la cle. Une reservation `en_cours` de **plus de 2 min** (processus arrete en pleine ecriture) est reprise par la requete suivante (`find_one_and_update` conditionne a l'anciennete : une seule reprise l'emporte) ; plus jeune, elle rend 409 `requete_en_cours`. PATCH utilise `version` (409 `version_perimee` : la vue est rechargee).

### Pieges

- **L'evenement va par nom** (`event` + `year`, tel que Cockpit le connait). Bibler (`evenement_cockpit` -> `Resolveur.par_nom`) resout le **nom actuel ou un ancien nom** du registre (exact, puis par comparaison normalisee) ; la cle stable n'est pas acceptee comme `event`. Un nom inconnu **ou ambigu** donne 404 `evenement_inconnu` (message clair sur la carte, pas une couche vide silencieuse) ; event ou year absents : 400 `evenement_requis`.
- **Aucun telephone ni e-mail** d'agent dans les reponses : ne jamais en ajouter au relais ni a l'affichage.
- **Bibler est proprietaire des consignes** : pas de copie dans MongoDB Cockpit, pas de lecture directe des collections. Pour changer une consigne, passer par le relais (qui vide le cache).
- **Mode `prevu` = planifie** : a un jour/heure futur, la carte montre le prevu (bandeau « prevu »), pas la presence reelle.
- **Bibler arrete** : 502 `bibler_indisponible` en moins de 9 s ; la couche reste affichee en grise.
- **Horloges** : un jeton emis 20 s « dans le futur » passe, 60 s ne passe pas (tolerance de 30 s cote Bibler).
- Pas de `innerHTML` avec des donnees ni de `confirm()` / `alert()` dans `bibler_carte.js` et `consignes_bibler.js` (confirmation dans la page).
- Cache de lecture par processus : plusieurs workers ne vident que leur propre cache apres une ecriture (au plus 30 s de retard sur les autres).
