# Pont Bibler : postes en place et consignes sur la carte - Plan d'implementation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Depuis la carte principale de Cockpit, voir les postes Bibler et leurs personnels (direct ou heure choisie), consulter mission et specimens, et donner des consignes (poste, secteur, zone, departement, tout ; permanente, ce shift, tous les shifts d'un format), par un pont API signe vers Bibler v2.

**Architecture:** Bibler expose `/api/v1/*` protege par un jeton de service HS256 (iss=cockpit, aud=bibler), enveloppes fines sur `qui_est_ou`, `mise_en_poste/paquet.py` et `consignes/service.py`. Cockpit appelle Bibler serveur a serveur (`bibler_pont.py`), relaie au navigateur (`bibler_postes.py`, `/api/bibler/*`) avec ses droits de groupe, et greffe une couche sur la carte d'accueil (`static/js/bibler_carte.js`) plus une page liste `/consignes-bibler`.

**Tech Stack:** Python 3 / Flask / MongoDB / PyJWT des deux cotes ; Leaflet + JS vanilla (Cockpit) ; pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-pont-bibler-consignes-design.md` (lire avec `bibler/docs/refonte/05_CONSIGNES_ET_COCKPIT.md`).

Depots : Cockpit = `/Users/framboises/Dropbox/ACO/TITAN/cockpit` ; Bibler = `/Users/framboises/Dropbox/ACO/TITAN/bibler` (v2 en production, branche `main`). Commits directement sur `main` dans les deux depots, puis `git push`.

## Global Constraints

- JAMAIS de guillemets typographiques dans le code JS/CSS/Python (apostrophes et guillemets droits uniquement).
- Jeton : HS256, claims `iss=cockpit`, `aud=bibler`, `sub=service:cockpit`, `iat`, `exp`, `jti`, `capacites` ; duree 60 s cote Cockpit ; Bibler refuse `exp - iat > 120`, tolere 30 s d'horloge, refuse un `jti` deja vu dans les 2 min.
- Capacites : `postes:lire`, `consignes:lire`, `consignes:ecrire`.
- Secret partage : `COCKPIT_PONT_SECRET_BIBLER` (Cockpit) = `BIBLER_PONT_SECRET_COCKPIT` (Bibler), 32 caracteres minimum.
- IP admises par Bibler : `BIBLER_PONT_IPS_COCKPIT` (adresses ou CIDR, virgules) ; vide = tout refuse.
- URL Bibler cote Cockpit : `COCKPIT_PONT_URL_BIBLER` ; absente ou secret absent = fonctionnalite masquee, relais en 503 `bibler_non_configure`.
- Delais Cockpit -> Bibler : connexion 3 s, reponse 8 s ; reponse 5 Mo max ; redirections non suivies.
- Format de shift : `HH:MM-HH:MM` (motif `^\d{2}:\d{2}-\d{2}:\d{2}$`), 10 formats max par consigne ; `cible.formats` vide = tous les shifts.
- Aucun telephone ni e-mail d'agent dans les reponses `/api/v1` ni dans le relais Cockpit.
- Etats de poste : `pourvu`, `incomplet`, `en_retard`, `vide`, `a_venir` ; couleurs carte : vert, orange (incomplet et en_retard), rouge, gris.
- Titre 120 caracteres, texte 600 (limites existantes de Bibler `TITRE_MAX`, `TEXTE_MAX`).
- Droits de groupe Cockpit : `bibler_postes` (voir), `can_publish_consigne` (rediger, modifier, lever ; exige aussi `bibler_postes`) ; decoches par defaut ; admin et super admin : tout ; comptes borne et afficheurs : rien.
- Codes d'erreur Bibler : 401 `auth_requise`/`auth_invalide`, 403 `capacite_manquante`/`ip_refusee`, 404 `evenement_inconnu`/`poste_inconnu`, 409 `version_perimee`, 422 (`cible_requise`, donnees personnelles, `format_invalide`). Cockpit : 502 `bibler_indisponible`, 503 `bibler_non_configure`, 403 `droit_insuffisant`.

## Review Focus

1. **Nom d'evenement de Cockpit inconnu ou ancien nom cote Bibler** : un ancien nom doit etre resolu ; un nom inconnu doit donner 404 `evenement_inconnu` et un message clair sur la carte, pas une couche vide silencieuse. Test : Task 2 `test_evenement_ancien_nom_et_inconnu`.
2. **Shift qui passe minuit (18:00-02:00)** : format `18:00-02:00`, « ce shift » finit le lendemain a 02:00, le filtre de format reconnait la vacation. Tests : Task 3 `test_format_passe_minuit`, Task 8 `test_duree_shift_passe_minuit`.
3. **Horloges decalees entre serveurs** : un jeton emis 20 s « dans le futur » passe, 60 s ne passe pas. Test : Task 1 `test_tolerance_horloge`.
4. **Double clic sur Publier** : une seule consigne creee (meme `Idempotency-Key` -> meme reponse). Tests : Task 4 `test_creation_idempotente`, Task 8 `test_relais_transmet_cle_idempotence`.
5. **Bibler arrete pendant l'usage de la carte** : 502 `bibler_indisponible` en moins de 9 s, la couche reste affichee en grise. Tests : Task 5 `test_delai_depasse`, Task 6 `test_relais_bibler_indisponible`.

---

## Partie Bibler (depot `bibler`)

### Task 1 : Jeton de service entrant

**Files:**
- Create: `titan_bibler/integrations/entrant.py`
- Modify: `titan_bibler/integrations/reglages.py` (variable `BIBLER_PONT_IPS_<CLE>`)
- Modify: `titan_bibler/securite/garde_ecritures.py` (exemption des chemins `/api/v1/`)
- Modify: `titan_bibler/donnees/collections.py` (`PONT_JTI = "bibler_pont_jti"`, `PONT_IDEMPOTENCE = "bibler_pont_idempotence"`)
- Test: `tests/v2/test_api_v1_jeton.py`

**Interfaces:**
- Produces:
  - `class JetonRefuse(Exception)` avec `.code: str`, `.statut: int`
  - `verifier_jeton(jeton: str, *, ip: str, secret: str, ips_admises: str, maintenant: int | None = None) -> dict` (claims) ; leve `JetonRefuse`
  - `service_requis(*capacites: str)` : decorateur de vue ; lit `Authorization: Bearer`, `request.remote_addr`, le secret `secret_pont("cockpit")` et `ips_pont("cockpit")` ; pose `g.service = claims` ; repond `{"ok": False, "erreur": code, "message": ...}` avec le statut ; marque la vue `marquer_acces(vue, "service")`
  - `ips_pont(cle: str) -> str` dans `reglages.py` (variable `BIBLER_PONT_IPS_<CLE>`)
  - `PREFIXE_SERVICE = "/api/v1/"` dans `garde_ecritures.py` : `garder_ecriture()` renvoie `None` pour ces chemins (le decorateur fait le controle)

- [ ] **Step 1: Ecrire les tests** (`tests/v2/test_api_v1_jeton.py`), avec un secret de test de 40 caracteres et une fonction `_jeton(**surcharges)` qui signe des claims valides :
  - `test_jeton_valide` : `verifier_jeton(...)` renvoie les claims, `capacites == ["postes:lire"]`
  - `test_jeton_absent_ou_illisible` : `JetonRefuse.code == "auth_invalide"`, statut 401
  - `test_mauvais_emetteur_ou_destinataire` : `iss="bibler"` -> 401 `auth_invalide` ; `aud="cockpit"` -> 401
  - `test_duree_trop_longue` : `exp - iat = 600` -> 401
  - `test_tolerance_horloge` : `iat = maintenant + 20` passe ; `iat = maintenant + 60` -> 401
  - `test_jti_rejoue` : deuxieme verification du meme `jti` -> 401 `auth_invalide`
  - `test_ip_refusee` : `ips_admises=""` -> 403 `ip_refusee` ; `ips_admises="10.0.0.0/24"` et `ip="10.0.0.7"` passe ; `ip="10.0.1.7"` -> 403
- [ ] **Step 2: Lancer** `cd /Users/framboises/Dropbox/ACO/TITAN/bibler && pytest tests/v2/test_api_v1_jeton.py -q` : ECHEC (module absent)
- [ ] **Step 3: Implementer** `entrant.py` : `jwt.decode(..., algorithms=["HS256"], audience="bibler", issuer="cockpit", leeway=30, options={"require": ["exp", "iat", "jti", "iss", "aud", "sub"]})`, `sub == "service:cockpit"`, `exp - iat <= 120` ; `jti` inscrit par `insert_one` dans `bibler_pont_jti` (index TTL 120 s sur `at` declare par `donnees.index.declarer`), `DuplicateKeyError` = rejoue ; IP via `ipaddress.ip_address(ip) in ip_network(x, strict=False)`. Ne jamais journaliser le jeton.
- [ ] **Step 4: Relancer** : PASS
- [ ] **Step 5: Commit** `git add -A titan_bibler/integrations titan_bibler/securite/garde_ecritures.py titan_bibler/donnees/collections.py tests/v2/test_api_v1_jeton.py && git commit -m "feat(api_v1): jeton de service entrant de Cockpit (iss/aud, jti, IP)"`

### Task 2 : Routes de lecture `/api/v1` (postes, fiche, visuels, formats)

**Files:**
- Create: `titan_bibler/api_v1/__init__.py`, `titan_bibler/api_v1/routes.py` (blueprint `api`), `titan_bibler/api_v1/service.py`
- Modify: `titan_bibler/mise_en_poste/paquet.py` (`titres(..., avec_accreditations: bool = False)`)
- Modify: le test de couverture des routes de `tests/v2` (celui qui lit `ATTRIBUT_ACCES`) pour accepter le niveau `"service"`
- Test: `tests/v2/test_api_v1_postes.py`

**Interfaces:**
- Consumes: `service_requis` (Task 1) ; `qui_est_ou.service.presence(evenement, annee, perimetre, maintenant=...)` avec `perimetre = {"illimite": True, "relie": True, "libelle": "Cockpit", "numeros": None}` ; `paquet.fiche_du_poste`, `mission`, `chef`, `materiel`, `titres` ; `consignes.service.vise`, `etat` ; `donnees.evenement.resoudre(db, nom)`
- Produces (service.py) :
  - `evenement_cockpit(nom: str) -> str | None` : nom actuel Bibler de l'evenement (nom actuel ou ancien), sinon None
  - `liste_postes(evenement: str, annee: int, recherche: str = "", instant: datetime | None = None) -> dict` : `{mode: "reel"|"prevu", instant: iso, postes: [{numero, affectation, metier, departement, zone, secteur, coords: {lat, lon}|None, etat, presents, planifies, formats: [str], consignes: int}]}` ; `mode = "prevu"` si `instant > maintenant + 5 min`
  - `fiche_poste(evenement: str, annee: int, numero: int, instant: datetime | None = None) -> dict | None` : `{numero, affectation, structure: {metier, departement, zone, secteur}, coords, positionnement, mission, chef, materiel, specimens: [{libelle, famille, controle, visuel: str|None}], agents: [{nom, etat, depuis, prevu, format}], vacations: [{debut, fin, format, agents}], consignes: [serialiser(...)]}` ; `visuel` = nom de fichier rendu par `titres` (cle opaque)
  - `formats_vacation(evenement: str, annee: int, cible: dict) -> list[dict]` : `[{format: "10:00-18:00", vacations: int}]` tries par nombre decroissant ; `cible` au format `normaliser_cible` ; utilise `format_vacation` (Task 3 le deplace dans `consignes/service.py` ; ici, le definir dans `api_v1/service.py` et Task 3 l'importe)
  - `format_vacation(v: dict) -> str | None` : `f"{start_time}-{end_time}"` en `HH:MM` (zero a gauche), None si l'un manque
  - `chemin_visuel(nom: str) -> Path | None` : fichier du dossier des specimens (`specimens/mediatheque.SOUS_DOSSIER`), nom valide `^[A-Za-z0-9._-]{1,200}$`, existant, sinon None
- Routes (toutes `@service_requis("postes:lire")`, parametres `event`, `year`, `a` ISO optionnel) : `GET /api/v1/postes` (`q`), `GET /api/v1/postes/<int:numero>`, `GET /api/v1/specimens/visuel/<nom>` (`send_file`, `Cache-Control: private, max-age=86400`), `GET /api/v1/vacations/formats` (`postes`, `secteurs`, `zones`, `departements` en listes separees par virgules, `tout=1`)

- [ ] **Step 1: Ecrire les tests** (`tests/v2/test_api_v1_postes.py`) ; fixture `service(app)` qui pose `BIBLER_PONT_SECRET_COCKPIT` et `BIBLER_PONT_IPS_COCKPIT=127.0.0.1` (monkeypatch env) et renvoie un client avec en-tete `Authorization` (nouveau `jti` a chaque appel) ; jeu `jeu_presence` de `test_qui_est_ou.py` (evenement `24H AUTOS`) :
  - `test_sans_jeton_401` : `GET /api/v1/postes?event=24H AUTOS&year=<ANNEE>` sans en-tete -> 401
  - `test_liste_postes` : poste 12 present avec `coords == {"lat": 47.95, "lon": 0.21}`, `etat` dans les 5 etats, poste inactif absent ; aucune cle `telephone` nulle part dans le JSON (`"telephone" not in r.get_data(as_text=True)`)
  - `test_recherche` : `q=12` renvoie le poste 12 ; `q=tribune` renvoie le 120
  - `test_instant_futur_prevu` : `a` = maintenant + 2 jours -> `mode == "prevu"`
  - `test_fiche_poste` : `GET /api/v1/postes/12` -> `structure.secteur == "Porte A"`, cles `mission`, `specimens`, `agents`, `vacations`, `consignes` presentes ; poste 999 -> 404 `poste_inconnu`
  - `test_evenement_ancien_nom_et_inconnu` : nom inconnu -> 404 `evenement_inconnu` ; ancien nom inscrit dans `bibler_evenements` (`noms: ["24H AUTOS", "24H AUTOS ANCIEN"]`) -> 200
  - `test_formats` : vacations 10:00-18:00 sur le poste 12 -> `[{"format": "10:00-18:00", "vacations": n}]`
  - `test_visuel_nom_refuse` : `nom="../app.py"` -> 404
- [ ] **Step 2: Lancer** `pytest tests/v2/test_api_v1_postes.py -q` : ECHEC
- [ ] **Step 3: Implementer** service.py et routes.py (blueprint `api = Blueprint("api_v1", __name__)` ; `csrf.exempt(api)` avec `from titan_bibler.extensions import csrf`) ; `presence` est appelee avec le perimetre illimite, sans `g.utilisateur` ; la presence est enrichie du nombre de consignes en vigueur par poste (`vise` avec un cache partage). Les agents sont projetes sans `telephone`.
- [ ] **Step 4: Relancer** ce fichier puis la suite complete `pytest tests/v2 -q` : PASS (dont le test de couverture des routes)
- [ ] **Step 5: Commit** `git commit -m "feat(api_v1): postes, fiche, visuels et formats de shift pour Cockpit"`

### Task 3 : Consignes : formats de shift, fin absente, auteur en parametre

**Files:**
- Modify: `titan_bibler/consignes/service.py`
- Modify: `titan_bibler/consignes/routes.py` (passer l'utilisateur de session explicitement)
- Test: `tests/v2/test_consignes_formats.py`

**Interfaces:**
- Consumes: `format_vacation` (Task 2)
- Produces:
  - `normaliser_cible` : ajoute `formats: list[str]` (motif et max 10 des Global Constraints ; invalide -> `ConsigneInvalide("format_invalide", ..., 422)`)
  - `libelle_cible` : suffixe ` · shifts 10:00-18:00` quand `formats`
  - `creer(evenement, annee, donnees, auteur: dict | None = None, app_source: str = "bibler")`, `modifier(identifiant, donnees, auteur: dict | None = None)`, `lever(identifiant, auteur: dict | None = None)` : `auteur` au format `utilisateur_courant()` (`{type, id, email, nom}`) ; None = `utilisateur_courant()` (comportement actuel) ; `source.app = app_source`
  - `formats_personne(evenement: str, annee: Any, email: str, instant: datetime) -> set[str] | None` : formats des vacations en cours ou du jour de la personne (rapprochement `planbition_people.emails`), None si personne non retrouvee
  - `destinataires` : ne garde que les vacations dont `format_vacation` est dans `cible.formats` (si non vide)
  - `pour_terrain` : une consigne a `formats` n'est rendue que si `formats_personne(...)` est None ou intersecte `formats`
  - `_dates_validite` : `fin` absente -> dates des vacations de l'evenement a partir de `debut - 1 jour`, 10 dates max

- [ ] **Step 1: Ecrire les tests** :
  - `test_formats_normalises` : `["10:00-18:00", "10:00-18:00"]` -> `["10:00-18:00"]` ; `["10h-18h"]` -> 422 `format_invalide` ; 11 formats -> 422
  - `test_destinataires_filtres_par_format` : deux encadrants sur le poste 12, l'un 10:00-18:00, l'autre 18:00-02:00 ; consigne `formats=["10:00-18:00"]` -> un seul destinataire
  - `test_format_passe_minuit` : vacation `start_time="18:00"`, `end_time="02:00"` -> `format_vacation == "18:00-02:00"` et reconnue par le filtre
  - `test_pour_terrain_masque_autre_format` : l'encadrant 18:00-02:00 ne voit pas la consigne `["10:00-18:00"]` dans `/api/terrain/consignes`
  - `test_fin_absente_couvre_les_jours_suivants` : consigne sans `fin`, vacation planifiee a J+3 -> destinataire present
  - `test_auteur_explicite` : `creer(..., auteur={"type": "user", "id": "", "email": "op@aco.fr", "nom": "Op PC"}, app_source="cockpit")` -> `source == {"app": "cockpit", "auteur": ..., "auteur_nom": "Op PC"}` (forme exacte de `source` existante conservee)
- [ ] **Step 2: Lancer** `pytest tests/v2/test_consignes_formats.py -q` : ECHEC
- [ ] **Step 3: Implementer** (modifications ciblees de `consignes/service.py` ; routes PC org existantes inchangees fonctionnellement)
- [ ] **Step 4: Relancer** ce fichier puis `pytest tests/v2/test_consignes.py tests/v2/test_briefing_releve.py tests/v2/test_mise_en_poste.py -q` : PASS
- [ ] **Step 5: Commit** `git commit -m "feat(consignes): formats de shift, fin absente, auteur et application source en parametre"`

### Task 4 : Routes consignes `/api/v1`

**Files:**
- Modify: `titan_bibler/api_v1/routes.py`, `titan_bibler/api_v1/service.py`
- Test: `tests/v2/test_api_v1_consignes.py`

**Interfaces:**
- Consumes: Tasks 1 a 3
- Produces (routes, `event`/`year` en parametre ou dans le corps) :
  - `GET /api/v1/consignes` (`consignes:lire`) -> `{ok, consignes: liste_pc(...)}`
  - `GET /api/v1/consignes/<id>` (`consignes:lire`) -> `{ok, consigne, suivi}`
  - `GET /api/v1/consignes/cibles?q=` (`consignes:lire`)
  - `GET /api/v1/consignes/apercu` (`consignes:lire`, memes parametres que `vacations/formats` plus `formats`) -> `{ok, encadrants: int}` (via `destinataires` sur un document fictif)
  - `POST /api/v1/consignes` (`consignes:ecrire`) : `Idempotency-Key` obligatoire (400 `idempotence_requise`) ; corps avec `source.auteur = {email, nom}` obligatoire (400 `auteur_requis`) ; appelle `creer(..., auteur={"type": "user", "id": "", "email": ..., "nom": ...}, app_source="cockpit")` ; reponse memorisee dans `bibler_pont_idempotence` (`_id` = cle, TTL 24 h) et rejouee telle quelle
  - `PATCH /api/v1/consignes/<id>`, `POST /api/v1/consignes/<id>/lever` (`consignes:ecrire`, `source.auteur` obligatoire)
  - Chaque ecriture est journalisee dans `bibler_journal_ecritures` avec `app: "cockpit"` et l'e-mail de l'auteur (`securite/journal_ecritures.py`)

- [ ] **Step 1: Ecrire les tests** :
  - `test_lecture_sans_capacite` : jeton `capacites=["postes:lire"]` sur `GET /api/v1/consignes` -> 403 `capacite_manquante`
  - `test_creation_et_suivi` : POST (cible secteur `Porte A`, `formats=["10:00-18:00"]`, sans `fin`) -> 201, `source.app == "cockpit"` ; `GET /api/v1/consignes/<id>` -> `suivi` present
  - `test_creation_idempotente` : deux POST avec la meme `Idempotency-Key` -> meme `id`, une seule consigne en base
  - `test_auteur_requis` et `test_idempotence_requise` : 400
  - `test_version_perimee` : PATCH avec `version: 9` -> 409 `version_perimee`
  - `test_lever` : `etat == "levee"`
  - `test_donnees_personnelles` : texte avec `06 12 34 56 78` -> 422
  - `test_apercu` : `encadrants >= 1` pour le secteur du jeu
- [ ] **Step 2: Lancer** : ECHEC
- [ ] **Step 3: Implementer**
- [ ] **Step 4: Relancer** ce fichier puis `pytest tests/v2 -q` : PASS
- [ ] **Step 5: Commit** puis `git push` : `git commit -m "feat(api_v1): consignes pour Cockpit (creation idempotente, version, levee, apercu)"`

---

## Partie Cockpit (depot `cockpit`)

### Task 5 : Client `bibler_pont.py`

**Files:**
- Create: `bibler_pont.py`
- Test: `tests/test_bibler_pont.py`

**Interfaces:**
- Produces:
  - `class BiblerNonConfigure(Exception)`, `class BiblerIndisponible(Exception)`, `class BiblerErreur(Exception)` avec `.statut: int`, `.corps: dict` (reponses 4xx de Bibler, relayees)
  - `configure() -> bool` (URL et secret de 32 caracteres au moins presents)
  - `jeton(capacites: tuple[str, ...]) -> str` (claims des Global Constraints, `exp = iat + 60`, `jti = uuid4().hex`)
  - `lire(chemin: str, params: dict | None = None, capacites: tuple = ("postes:lire",), ttl: int = 0) -> dict` ; `ttl > 0` = cache memoire par `(chemin, tuple(sorted(params)))`
  - `lire_binaire(chemin: str, ttl: int = 86400) -> tuple[bytes, str]`
  - `envoyer(methode: str, chemin: str, corps: dict, capacites: tuple = ("consignes:ecrire",), idempotence: str | None = None) -> tuple[dict, int]`
  - Chemin verifie `^/api/v1/[A-Za-z0-9._~\-/%]*$`, sans `..` ni `//` (sinon `ValueError`)
  - Session `requests` avec `timeout=(3, 8)`, `allow_redirects=False` ; 5xx ou exception reseau -> `BiblerIndisponible` ; 4xx -> `BiblerErreur` ; reponse > 5 Mo -> `BiblerIndisponible`

- [ ] **Step 1: Ecrire les tests** (session `requests` remplacee par un faux objet ; env par `monkeypatch.setenv`) :
  - `test_non_configure` : sans variable -> `configure() is False`, `lire` leve `BiblerNonConfigure`
  - `test_claims_du_jeton` : `jwt.decode` avec le secret -> `iss == "cockpit"`, `aud == "bibler"`, `sub == "service:cockpit"`, `exp - iat == 60`, `capacites == ["postes:lire"]`
  - `test_chemin_refuse` : `"/api/v1/../x"`, `"http://x/api/v1/"`, `"/autre"` -> `ValueError`
  - `test_delai_depasse` : faux `requests.Timeout` -> `BiblerIndisponible`
  - `test_erreur_4xx_relayee` : 409 `{"erreur": "version_perimee"}` -> `BiblerErreur` avec `statut == 409`
  - `test_cache` : deux `lire(..., ttl=30)` identiques -> un seul appel reseau
  - `test_idempotence_en_tete` : `envoyer(..., idempotence="abc")` envoie `Idempotency-Key: abc`
- [ ] **Step 2: Lancer** `cd /Users/framboises/Dropbox/ACO/TITAN/cockpit && pytest tests/test_bibler_pont.py -q` : ECHEC
- [ ] **Step 3: Implementer** (aucun appel reseau a l'import ; aucun secret ni jeton dans les journaux)
- [ ] **Step 4: Relancer** : PASS
- [ ] **Step 5: Commit** `git commit -m "feat(bibler): client du pont Bibler (jeton de service, delais, cache)"`

### Task 6 : Droits de groupe et relais de lecture

**Files:**
- Create: `bibler_postes.py` (blueprint `bibler_bp`)
- Modify: `app.py` (fonctions de droit, champs de groupe a la creation ligne ~5093 et au patch ligne ~5162, enregistrement du blueprint pres de `declarations_bp` ligne ~2812, context processor `_inject_page_access`)
- Modify: `templates/edit.html` (deux cases pres de `can_convert_declaration`, ligne ~963), `static/js/groups.js` (lecture, ecriture, remise a zero des deux cases, comme `can_convert_declaration` lignes 662, 698, 784)
- Test: `tests/test_bibler_postes.py`

**Interfaces:**
- Consumes: `bibler_pont` (Task 5)
- Produces:
  - `app._user_can_view_bibler(payload) -> bool` et `app._user_can_publish_consigne(payload) -> bool` (sur le modele de `_user_can_treat_declaration` ; publish exige view ; admin/super admin : True ; compte borne : False)
  - Context processor : `bibler_allowed()` et `bibler_publish_allowed()` dans les gabarits
  - Routes de lecture (`role_required("user")` par import tardif comme `declarations._role_required`, puis droit `bibler_postes` sinon 403 `droit_insuffisant`) :
    `GET /api/bibler/postes` (`event`, `year`, `q`, `a` -> `/api/v1/postes`, `ttl=30` en direct, 0 avec `a`),
    `GET /api/bibler/postes/<int:numero>`, `GET /api/bibler/visuel/<nom>` (`lire_binaire`, cache 24 h), `GET /api/bibler/formats` (`ttl=300`),
    `GET /api/bibler/consignes`, `GET /api/bibler/consignes/<id>`, `GET /api/bibler/consignes/cibles`, `GET /api/bibler/consignes/apercu`
  - Erreurs : `BiblerNonConfigure` -> 503 `bibler_non_configure` ; `BiblerIndisponible` -> 502 `bibler_indisponible` ; `BiblerErreur` -> meme statut et corps

- [ ] **Step 1: Ecrire les tests** (faux module `app` comme `tests/test_declarations.py` ; `bibler_pont.lire` remplace par monkeypatch) :
  - `test_lecture_sans_droit_403`
  - `test_lecture_relayee` : parametres transmis tels quels a `/api/v1/postes`
  - `test_relais_bibler_indisponible` : 502 `bibler_indisponible`
  - `test_non_configure_503`
  - `test_erreur_bibler_relayee` : 404 `evenement_inconnu` relaye avec son corps
  - `test_droits_groupe` (fonctions de `app.py` testees avec des groupes factices) : sans case -> False ; `can_publish_consigne` sans `bibler_postes` -> False ; admin -> True
- [ ] **Step 2: Lancer** `pytest tests/test_bibler_postes.py -q` : ECHEC
- [ ] **Step 3: Implementer**
- [ ] **Step 4: Relancer** puis `pytest tests -q` : PASS
- [ ] **Step 5: Commit** `git commit -m "feat(bibler): droits de groupe et relais de lecture vers Bibler"`

### Task 7 : Couche carte « Personnels en poste » (lecture)

**Files:**
- Create: `static/js/bibler_carte.js`, `static/css/bibler_carte.css`
- Modify: `static/js/map_view.js` (ajouter `".cockpit-bibler-ctrl"` a `RIGHT_DATA`, ligne 393)
- Modify: `templates/index.html` (inclure CSS et JS apres `map_view.js`, ligne ~957, sous `{% if bibler_allowed is defined and bibler_allowed() %}` ; poser `window.BIBLER_PUBLISH = {{ 'true' if bibler_publish_allowed() else 'false' }}`)

**Interfaces:**
- Consumes: routes de lecture de Task 6 ; `window.CockpitMapView.getMap()` ; `window.selectedEvent`, `window.selectedYear`
- Produces: `window.CockpitBibler = { activer(), desactiver(), ouvrirPoste(numero), surlignerConsigne(id) }` ; panneau DOM `#bibler-panneau` reutilise par Task 9

Comportement (spec §4.3) :
- Controle Leaflet `div.leaflet-bar.cockpit-measure-tools.cockpit-bibler-ctrl` (meme construction que `.cockpit-momentus-ctrl`, `map_view.js:327`), bouton icone `badge`, titre « Personnels en poste ».
- Actif : `L.layerGroup` de `L.circleMarker` regroupes par une grille simple au dezoom (zoom < 15 : un marqueur par cellule de 40 px avec le nombre) ; couleur par etat ; badge consigne = contour epais.
- Barre compacte : bascule Direct / champ `datetime-local` ; recherche avec suggestions (filtre local sur la liste, 8 max) ; bouton « Consignes (n) » (rempli par Task 9).
- Direct : `setInterval` 60 s, suspendu quand `document.hidden` ; heure choisie : pas d'actualisation, bandeau « Presence reelle a HH:MM » ou « Prevu a HH:MM » selon `mode`.
- Changement d'evenement ou d'annee (ecouter le meme evenement que les autres couches de `map_view.js`) : rechargement.
- Clic marqueur ou suggestion : `map.flyTo` + `ouvrirPoste(numero)` -> `GET /api/bibler/postes/<n>` -> panneau : en-tete (numero, affectation, etat, presents/planifies), chemin de structure, chef, mission et positionnement repliables (`<details>`), vignettes specimens (`/api/bibler/visuel/<nom>`, etiquette scan/visuel), agents, shifts du jour, consignes en vigueur.
- Erreurs : 502/503 -> bandeau « Bibler indisponible », marqueurs gardes avec `opacity 0.4` ; 404 `evenement_inconnu` -> bandeau « Evenement inconnu de Bibler ».
- Textes inseres par `textContent` (jamais `innerHTML` avec des donnees).

- [ ] **Step 1: Implementer** le module et l'inclusion
- [ ] **Step 2: Verifier** `node --check static/js/bibler_carte.js` : aucune sortie ; `grep -nP "[\x{2018}\x{2019}\x{201C}\x{201D}]" static/js/bibler_carte.js static/css/bibler_carte.css` : aucune ligne
- [ ] **Step 3: Recette** `CODING=true python app.py`, Bibler local configure (`COCKPIT_PONT_URL_BIBLER=http://127.0.0.1:5010`) : bouton visible dans le groupe oeil ; marqueurs ; recherche « 12 » ouvre la fiche ; heure future affiche « Prevu » ; Bibler arrete -> bandeau, marqueurs grises
- [ ] **Step 4: Commit** `git commit -m "feat(bibler): couche Personnels en poste sur la carte d'accueil"`

### Task 8 : Relais d'ecriture et traduction de la duree

**Files:**
- Modify: `bibler_postes.py`
- Test: `tests/test_bibler_postes.py` (ajouts)

**Interfaces:**
- Consumes: `bibler_pont.envoyer` (Task 5), droits (Task 6)
- Produces:
  - `consigne_depuis_formulaire(form: dict, maintenant: datetime) -> dict` : corps pour `/api/v1/consignes` ; leve `ValueError(code)` (`duree_invalide`, `shift_invalide`)
    - `form.duree = {"mode": "permanente"}` -> `debut = maintenant` (ISO UTC), pas de `fin`, `cible.formats = []`
    - `{"mode": "shift", "debut": iso, "fin": iso, "format": "10:00-18:00"}` -> `debut`, `fin` tels quels (exige `fin > debut` et `fin - debut <= 24 h`), `cible.formats = [format]`
    - `{"mode": "format", "format": "10:00-18:00"}` -> `debut = maintenant`, pas de `fin`, `cible.formats = [format]`
    - copie `titre`, `texte`, `priorite`, `cible` (departements, zones, secteurs, postes, tout_evenement), `question_controle`, `dans_briefing`, `event`, `year`, `statut = "publiee"`
  - `POST /api/bibler/consignes` : droit `can_publish_consigne` (403 sinon), refus des comptes borne (`_refuser_ecriture_borne` d'`app.py`), CSRF actif ; en-tete navigateur `X-Idempotency-Key` obligatoire (400 `idempotence_requise`) transmis comme `Idempotency-Key` ; ajoute `source.auteur = {"email": payload.email, "nom": "Prenom Nom"}`
  - `PATCH /api/bibler/consignes/<id>` (corps `{titre?, texte?, priorite?, version}` + auteur) et `POST /api/bibler/consignes/<id>/lever`
  - Chaque ecriture reussie : une ligne `logger.info("Consigne Bibler %s %s par %s", action, id, email)` (journal applicatif, spec §5)

- [ ] **Step 1: Ecrire les tests** :
  - `test_duree_permanente`, `test_duree_shift`, `test_duree_format` : champs exacts ci-dessus
  - `test_duree_shift_passe_minuit` : debut 18:00 J, fin 02:00 J+1, format `18:00-02:00` -> accepte
  - `test_duree_shift_invalide` : `fin <= debut` ou 30 h -> `ValueError("shift_invalide")`
  - `test_ecriture_sans_droit_403` et `test_ecriture_compte_borne_403`
  - `test_relais_injecte_auteur` : `source.auteur.email == "op@aco.fr"`
  - `test_relais_transmet_cle_idempotence` : `envoyer` recoit `idempotence == "k-1"` ; sans en-tete -> 400
- [ ] **Step 2: Lancer** : ECHEC
- [ ] **Step 3: Implementer**
- [ ] **Step 4: Relancer** `pytest tests/test_bibler_postes.py -q` : PASS
- [ ] **Step 5: Commit** `git commit -m "feat(bibler): relais d'ecriture des consignes (duree permanente, shift, format)"`

### Task 9 : Formulaire de consigne et liste « Consignes (n) » sur la carte

**Files:**
- Modify: `static/js/bibler_carte.js`, `static/css/bibler_carte.css`

**Interfaces:**
- Consumes: Task 7 (`#bibler-panneau`, `CockpitBibler`), routes Task 6 (cibles, formats, apercu, consignes) et Task 8 (ecritures) ; `apiPost` et en-tetes CSRF existants de Cockpit
- Produces: `CockpitBibler.surlignerConsigne(id)` complet ; lecture de `?bibler=consigne:<id>` au chargement de l'accueil (active la couche puis surligne)

Comportement (spec §4.3) :
- Bouton « Donner une consigne » visible seulement si `window.BIBLER_PUBLISH`.
- A qui : puces `Poste N`, `Secteur X`, `Zone Y`, `Departement Z`, `Tout l'evenement` construites depuis la fiche ; une seule active (defaut : poste).
- Quand : `Permanente` (defaut), `Ce shift (HH:MM-HH:MM aujourd'hui)` (une puce par vacation du jour de la fiche, absente si aucune), `Tous les shifts F` (une puce par format de `GET /api/bibler/formats` pour la cible active).
- Titre (maxlength 120), texte (maxlength 600, compteur), priorite (Information, Important, Urgent) ; `<details>` « Options » : question de controle (question, bonne, 2 ou 3 mauvaises), case « Dans le briefing ».
- Apercu `GET /api/bibler/consignes/apercu` a chaque changement de cible ou de duree (debounce 300 ms) : « environ N encadrants concernes ».
- Rappel fixe « Pas de nom, de telephone ni d'e-mail dans la consigne ».
- `X-Idempotency-Key` = `crypto.randomUUID()` genere a l'ouverture du formulaire ; bouton desactive pendant l'envoi ; succes -> fermeture, rechargement couche et compteur ; 409 -> « Consigne modifiee entre-temps », rechargement ; 422 -> message de Bibler sous le formulaire.
- « Consignes (n) » : liste deroulante des consignes `en_vigueur` (titre, cible, priorite, `part_lue`/`part_transmise`) ; clic -> `surlignerConsigne(id)` (postes cibles en surbrillance, `fitBounds`) et detail du suivi (lignes `suivi.lignes` : nom, perimetre, lu/valide/transmis) ; actions Modifier (titre, texte, priorite ; envoie `version`) et Lever (confirmation dans le panneau, pas de `confirm()`), si `BIBLER_PUBLISH`.

- [ ] **Step 1: Implementer**
- [ ] **Step 2: Verifier** `node --check static/js/bibler_carte.js` et absence de guillemets typographiques (meme commande que Task 7)
- [ ] **Step 3: Recette** : consigne poste/permanente ; secteur/ce shift ; zone/tous les 10:00-18:00 ; double clic sur Publier = une seule consigne ; modifier puis lever ; groupe sans `can_publish_consigne` = aucun bouton d'ecriture ; `/?bibler=consigne:<id>` surligne la cible
- [ ] **Step 4: Commit** `git commit -m "feat(bibler): consignes depuis la carte (cible, duree, suivi, levee)"`

### Task 10 : Page Terrain `/consignes-bibler`, documentation

**Files:**
- Create: `templates/consignes_bibler.html`, `static/js/consignes_bibler.js`
- Modify: `bibler_postes.py` (route `GET /consignes-bibler`, `role_required("user")` + droit `bibler_postes`)
- Modify: `app.py` (`PAGE_REGISTRY` : `{"id": "consignes_bibler", "label": "Consignes Bibler", "icon": "campaign", "role": "user", "paths": ["/consignes-bibler"]}`)
- Modify: `templates/_sidebar.html` (lien dans `_g_terrain`, condition `page_allowed('consignes_bibler')` et `bibler_allowed()`)
- Create: `docs/claude/bibler.md` ; Modify: `CLAUDE.md` (variables `COCKPIT_PONT_URL_BIBLER`, `COCKPIT_PONT_SECRET_BIBLER` ; ligne `docs/claude/bibler.md` dans « Documentation par domaine »)
- Test: `tests/test_bibler_postes.py` (`test_page_sans_droit_403`)

**Interfaces:**
- Consumes: routes Tasks 6 et 8

Comportement : liste par etat (en vigueur, programmees, levees, expirees) depuis `GET /api/bibler/consignes`, detail et accuses, modifier et lever (avec le droit), bouton « Voir sur la carte » -> `/?bibler=consigne:<id>`. `docs/claude/bibler.md` : architecture du pont, variables des deux cotes, droits de groupe, traduction des durees, pieges (evenement par nom, aucun telephone, Bibler proprietaire des consignes).

- [ ] **Step 1: Ecrire** `test_page_sans_droit_403` ; lancer : ECHEC
- [ ] **Step 2: Implementer** page, registre, barre laterale, docs
- [ ] **Step 3: Verifier** `pytest tests -q` : PASS ; `node --check static/js/consignes_bibler.js` ; recette de la page
- [ ] **Step 4: Mettre a jour le graphe** `graphify update .` (regle du `CLAUDE.md` de TITAN)
- [ ] **Step 5: Commit et push** `git commit -m "feat(bibler): page Terrain des consignes Bibler et documentation du pont" && git push`

---

## Mise en service (apres les taches)

1. Generer un secret de 48 caracteres ; le poser dans `COCKPIT_PONT_SECRET_BIBLER` (compte de service de Cockpit) et `BIBLER_PONT_SECRET_COCKPIT` (Bibler).
2. `COCKPIT_PONT_URL_BIBLER` = URL interne de Bibler ; `BIBLER_PONT_IPS_COCKPIT` = IP du serveur Cockpit.
3. Redemarrer les deux services ; cocher `bibler_postes` (et `can_publish_consigne` pour les redacteurs) dans les groupes voulus ; ajouter la page `consignes_bibler` aux groupes.
