# Module Formation : test en ligne PC Organisation, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Campagne d'e-mails vers tous les candidats, test en ligne public a passage unique (7 epreuves, correction serveur) et administration, dans Cockpit.

**Architecture:** Paquet Python `formation/` (fonctions pures de correction et de regles, couche Mongo, deux blueprints : public et admin), base MongoDB dediee, pages candidat autonomes (charte de la maquette), page d'administration au gabarit Cockpit. Le navigateur n'a jamais les bonnes reponses des epreuves a question ; les epreuves temps reel envoient leurs actions au fil de l'eau et le serveur rejoue.

**Tech Stack:** Flask + Blueprints, Flask-WTF (CSRF), PyJWT HS256, pymongo, openpyxl, smtplib, pytest contre un MongoDB local jetable ; JS vanilla (modules par epreuve), CSS a variables.

**Spec:** `docs/superpowers/specs/2026-11-22-formation-test-pcorg-design.md` (fait foi) ; cahier des charges d'origine `~/Dropbox/ACO/2026/PC ORG REBOOT/Prompt_ClaudeCode_test_en_ligne.md` ; maquette `~/Dropbox/ACO/2026/PC ORG REBOOT/test_pcorg.html` (contenu, textes, regles, couleurs : a reprendre a l'identique sauf mention contraire).

## Global Constraints

- **Jamais de guillemets typographiques** dans le code JS/CSS/Python (`'` et `"` droits seulement). Les textes affiches en francais utilisent les guillemets « » ou des apostrophes droites.
- Base dediee : `cockpit_formation_dev` (dev) / `cockpit_formation` (prod, `TITAN_ENV=prod`). Le module n'ecrit jamais dans `titan` / `titan_dev`.
- `csrf.exempt()` interdit : toutes les ecritures (publiques comprises) envoient `X-CSRFToken`.
- Campagne unique : `CAMPAGNE = "pcorg-2026-12"` ; chaque document porte `campagne`.
- Ids d'epreuves, dans l'ordre : `radio`, `appel`, `mc`, `patrouilles`, `acces`, `logique`, `situations`.
- Aucun score, aucune bonne reponse montres au candidat ; epreuves a question : « Reponse enregistree ».
- Une epreuve n'est **jamais rejouee** (spec 6.1). Une seule reprise, dans l'heure suivant le debut de la tentative, et elle continue a l'epreuve suivante.
- Coefficient d'amenagement par defaut `1.33`, fige dans la tentative au clic « Commencer » ; le temps de jeu vaut `temps_reel / coef` partout.
- Pages candidat : aucune ressource tierce (polices servies depuis `static/fonts/formation/`), en-tetes `Cache-Control: no-store` et `Referrer-Policy: no-referrer`.
- Adresse IP : `request.remote_addr` uniquement, jamais `X-Forwarded-For`.
- Message d'identification refusee, mot pour mot : « Ces informations ne correspondent à aucune candidature. Vérifiez l'adresse utilisée dans le questionnaire. »
- Case a cocher, mot pour mot : « J'ai compris que je ne peux passer ce test qu'une seule fois. Je le fais seul, sur un ordinateur, dans un endroit calme, et j'ai devant moi environ 25 minutes sans interruption. »
- Ecran de fin, mot pour mot : « L'équipe de sélection vous contactera par e-mail ; les convocations partent au plus tard le 4 décembre. »
- Suppression des donnees : 30/01/2027 (purge manuelle, rappel des le 23/01).
- Tests : `pytest tests/test_formation_*.py -v` ; saut propre si MongoDB local absent (modele `tests/test_declarations.py`).

## Review Focus

1. **Rechargement de page pendant une epreuve** : l'epreuve est close et l'utilisateur continue a la suivante ; jamais de second essai de la meme epreuve, meme via un second onglet ou un second navigateur. Test : `test_second_onglet_ne_rejoue_pas` (Task 4).
2. **Candidat avec amenagement** : tous les delais allonges, y compris le seuil PRIORITE de la radio et l'echeance des incidents ; le rejeu serveur utilise le meme coefficient que le client. Test : `test_patrouilles_coef_allonge_echeances` (Task 3).
3. **E-mail saisi avec majuscules, espaces, ou mobile au format `+33 6 ...`** a l'import comme a la connexion : la paire doit correspondre. Test : `test_connexion_email_majuscules_espaces` (Task 5).
4. **Campagne relancee apres un redemarrage du serveur ou un double clic** : aucun candidat ne recoit deux fois l'invitation. Test : `test_planifier_deux_fois_ne_double_pas` (Task 8).
5. **Fermeture de la fenetre pendant qu'un candidat est en cours** : il termine sa tentative (dans la limite d'une heure), seul un nouveau depart est refuse. Test : `test_fenetre_fermee_tentative_en_cours_continue` (Task 5).

---

## File Structure

```
formation/__init__.py           -> exporte formation_bp (public) et formation_admin_bp
formation/store.py              -> base dediee, index, reglages (lecture/ecriture, valeurs par defaut), journal_admin
formation/normalise.py          -> normaliser_email, normaliser_tel
formation/importer.py           -> lecture de l'export Forms, upsert, rapport
formation/banque_pcorg.json     -> contenu des epreuves (repris de la maquette) + entrainements
formation/banque.py             -> chargement, enonces sans reponses, tirages
formation/correction.py         -> score de chaque epreuve (fonctions pures)
formation/patrouilles.py        -> plan, plus court chemin, rejeu deterministe
formation/tentative.py          -> regles de passage unique (machine a etats, fonctions sur dict + persistance)
formation/acces.py              -> identification, limitation des essais, jeton de session, fenetre d'ouverture
formation/mailer.py             -> gabarit, planification de campagne, fil d'envoi SMTP
formation/routes_public.py      -> pages et API candidat (/test-pc-organisation)
formation/routes_admin.py       -> page et API d'administration (/admin/formation, /api/formation/admin)
templates/formation/accueil.html, test.html, fin.html, admin.html
static/css/formation.css, formation_admin.css
static/fonts/formation/*        -> Exo 2 (charte), Inter, JetBrains Mono
static/js/formation/core.js     -> etat, appels API, sequencement des epreuves, son, garde beforeunload
static/js/formation/{radio,appel,mc,patrouilles,acces,logique,situations}.js
static/js/formation/admin.js
scripts/formation_fictifs.py, scripts/formation_purge.py
tests/test_formation_{import,correction,patrouilles,tentative,acces,mailer,admin}.py
docs/claude/formation.md        + lignes dans CLAUDE.md
app.py                          -> enregistrement des blueprints, ADMIN_PAGE_REGISTRY, verif secret en prod
templates/_sidebar.html         -> lien admin « Formation »
```

---

### Task 1: Socle du module (base dediee, reglages, enregistrement)

**Files:**
- Create: `formation/__init__.py`, `formation/store.py`, `formation/routes_public.py` (squelette), `formation/routes_admin.py` (squelette)
- Modify: `app.py` (pres de l'enregistrement de `declarations_bp`, ~l.2903 ; `ADMIN_PAGE_REGISTRY` ~l.722 ; verifications de secrets en prod ~l.100), `templates/_sidebar.html` (a cote du lien `/admin/wiki`, l.232)
- Test: `tests/test_formation_store.py`

**Interfaces:**
- Produces:
  - `store.CAMPAGNE: str = "pcorg-2026-12"`, `store.EPREUVES: list[str]` (ordre des Global Constraints)
  - `store.get_db() -> pymongo.database.Database` (nom selon `TITAN_ENV` ; surcharge par `store.set_db_for_tests(db)`)
  - `store.ensure_indexes(db) -> None` : `candidats` unique `(campagne, email)` ; `tentatives` unique partiel `(campagne, candidat_id)` sur `statut != "reinitialisee"` ; `envois` unique `(campagne, candidat_id, type)` partiel sur `type="invitation"` ; `essais_connexion` TTL 900 s sur `t`
  - `store.reglages(db) -> dict` et `store.maj_reglages(db, champs: dict, auteur: str) -> dict` (journalise)
  - `store.journal(db, action: str, auteur: str, **details) -> None`
  - Reglages par defaut : `ouverture = 2026-11-30T08:00 Europe/Paris`, `fermeture = 2026-12-02T23:59 Europe/Paris`, `coef_amenagement = 1.33`, `ponderations = {e: 1 for e in EPREUVES}`, `reply_to = ""`, `contact_rgpd = ""`, `conservation_jusqu_au = 2027-01-30`, `debit_envoi_s = 2.0`, `expediteur_nom = "PC Organisation - ACO"`, `gabarit` (Task 8)
  - `formation_bp` (url_prefix `/test-pc-organisation`) et `formation_admin_bp` enregistres dans `app.py` ; `ADMIN_PAGE_REGISTRY += {"id": "formation", "label": "Formation", "icon": "school", "paths": ["/admin/formation", "/api/formation/admin"]}`
  - Prod : `FORMATION_JWT_SECRET` vide ou < 32 caracteres -> `ValueError` au demarrage (meme style que `CRISE_JWT_SECRET`)

- [ ] **Step 1: Ecrire les tests** : `test_nom_de_base_dev_et_prod` (monkeypatch `TITAN_ENV` -> `cockpit_formation_dev` / `cockpit_formation`) ; `test_reglages_par_defaut` (valeurs ci-dessus, `coef_amenagement == 1.33`) ; `test_maj_reglages_journalise` (un document `journal_admin` avec `action == "reglages"` et l'auteur) ; `test_index_tentative_unique` (deux tentatives actives pour un candidat -> `DuplicateKeyError`, une `reinitialisee` + une active -> accepte).
- [ ] **Step 2: Lancer** `pytest tests/test_formation_store.py -v` -> FAIL (module absent)
- [ ] **Step 3: Implementer** `store.py`, squelettes des blueprints, enregistrement dans `app.py`, lien de la barre laterale (`admin_page_allowed('formation')`, icone `school`)
- [ ] **Step 4: Lancer** les tests -> PASS ; `CODING=true python app.py` demarre sans erreur
- [ ] **Step 5: Commit** `feat(formation): socle du module, base dediee et reglages`

### Task 2: Import de l'export Forms

**Files:**
- Create: `formation/normalise.py`, `formation/importer.py`
- Modify: `formation/routes_admin.py` (route `POST /api/formation/admin/import`)
- Test: `tests/test_formation_import.py` (fixture : petit .xlsx construit avec openpyxl dans `tmp_path`, memes en-tetes que l'export reel)

**Interfaces:**
- Consumes: `store.get_db`, `store.CAMPAGNE`, `store.journal`
- Produces:
  - `normaliser_email(s) -> str | None` (minuscules, sans espaces, `None` si pas de `@`)
  - `normaliser_tel(s) -> str | None` (chiffres seuls ; `33XXXXXXXXX` -> `0XXXXXXXXX` ; 10 chiffres commencant par 0 sinon `None`)
  - `importer(db, fichier: bytes | str, auteur: str, now=None) -> dict` rapport `{lignes, crees, mis_a_jour, inchanges, doublons: [{email, ids_forms, garde}], inexploitables: [{forms_id, motif}], absents_du_fichier: [email]}` ; enregistre le rapport dans `imports`
  - Document candidat : `{campagne, forms_id, nom, prenom, email, tel, tel4, amenagement: False, fictif: False, forms_repondu_le, importe_le, maj_le}`
- Colonnes lues (par libelle d'en-tete, pas par position) : `Id`, `Heure de fin`, `Nom1`, `Prénom`, `Téléphone mobile`, `Adresse e-mail`. Toute autre colonne est ignoree.

- [ ] **Step 1: Ecrire les tests** : `test_normaliser_tel` (`"+33 6 17 96 31 79"` -> `"0617963179"`, `"0617963179"` -> idem, `"617963179"` -> `None`) ; `test_normaliser_email` (`" F.Arbia@Gmail.com "` -> `"f.arbia@gmail.com"`) ; `test_doublon_garde_le_plus_recent` (deux lignes meme e-mail, `Heure de fin` differente : le doc garde le `forms_id` le plus recent, rapport le liste) ; `test_inexploitables` (e-mail vide ou tel invalide -> dans `inexploitables` avec motif, non importe) ; `test_relance_ne_touche_pas_tentatives` (import, creation d'une tentative et d'un envoi, reimport avec nom modifie : nom mis a jour, `amenagement` deja `True` conserve, tentative et envoi intacts) ; `test_aucune_colonne_superflue` (le doc candidat n'a que les cles listees).
- [ ] **Step 2: Lancer** -> FAIL
- [ ] **Step 3: Implementer** (`openpyxl.load_workbook(read_only=True)`, upsert par `(campagne, email)`, `$setOnInsert` pour `amenagement`, `fictif`, `importe_le`) et la route d'import (televersement multipart, `role_required("admin")` par le motif paresseux de `declarations._role_required`, reponse = rapport)
- [ ] **Step 4: Lancer** -> PASS ; import a blanc du vrai fichier dans la base dev : 566 lignes, environ 555 candidats, rapport des doublons coherent avec les 11 du fichier de proposition
- [ ] **Step 5: Commit** `feat(formation): import de l'export Forms avec rapport`

### Task 3: Banque de questions et correction serveur

**Files:**
- Create: `formation/banque_pcorg.json`, `formation/banque.py`, `formation/correction.py`, `formation/patrouilles.py`
- Test: `tests/test_formation_correction.py`, `tests/test_formation_patrouilles.py`

**Interfaces:**
- Produces:
  - `banque_pcorg.json` : cles `radio` (14 messages `{id, texte, pour_moi, priorite}` = `RADIO` de la maquette), `appel` (`CALLS`, chaque choix `{id, texte, points}`), `mc` (`MC` + `CATS`, `ZONES`, `MOYENS`), `patrouilles` (`RW=8, CL=12, PASS=[1,6], TRAV, MAPZONES, UNITS, INCIDENTS`), `acces` (`BADGES` avec `ok`, heure `"01:30"`, couleurs admises `ROUGE`, `OR`), `logique` (`LOGIC`), `situations` (`JUDGE`), `entrainement` (`radio` : 2 messages, 1 pour PC Est et 1 non ; `patrouilles` : 1 incident Securite pres de P1, delai 30 s). Ids stables (`r01`..., `a1s1c2`...).
  - `banque.charger() -> dict` (lecture du JSON, cache) et `banque.enonce(epreuve: str, rng: random.Random) -> dict` : **retire** `points`, `ans`, `a`, `ok` des epreuves `appel`, `mc`, `logique`, `situations` ; melange questions et choix pour ces quatre ; ordre **fixe** pour `radio`, `patrouilles`, `acces` (spec 12). Pour `radio`, `patrouilles`, `acces`, l'enonce garde ce qu'il faut au retour immediat (`pour_moi`, `priorite`, types d'incidents, `ok` des badges).
  - Constantes de temps (ms, en temps de jeu) dans `correction.py` : `RADIO_PREMIER=800`, `RADIO_CADENCE=4000`, `RADIO_CADENCE_PRIO=3200`, `RADIO_SEUIL_PRIO=2000`, `ACCES_DELAI=5000`, `LOGIQUE_DUREE=240000`, `PLAFOND_QUESTIONS=600000` ; dans `patrouilles.py` : `PAS_MS=430`, `SUR_PLACE_MS=4000`.
  - `correction.score(epreuve: str, actions: list[dict], coef: float) -> dict` `{score: int 0-100, detail: dict, drapeaux: list[str]}`, ou chaque action porte `t_ms` (temps reel depuis le debut de l'epreuve) et ses champs. Formules (maquette, bornees 0-100, arrondi) :
    - radio : actions `{type:"prise", msg}` ; `(pris_a_raison - pris_a_tort) / nb_pour_moi * 100 + 5 * priorites_rapides` ; priorite rapide si `t_ms/coef - affichage_ms(msg) <= RADIO_SEUIL_PRIO` avec `affichage_ms` calcule de la cadence ; une prise sur un message non encore affiche -> ignoree + drapeau `horodatage_incoherent` ; une seule prise par message.
    - appel : `{question, choix}` ; somme des points / (2 * nb_etapes) * 100.
    - mc : `{question, cat, zone, heure, moyen}` ; 1 point par champ egal (apres `strip`) / (4 * nb) * 100.
    - patrouilles : `patrouilles.rejouer(actions, coef)` (ci-dessous) ; `(traites - 0.5 * erreurs) / 8 * 100`.
    - acces : `{badge, decision: 1|0}` ; decision avec `t_ms/coef > ACCES_DELAI` depuis l'affichage du badge -> comptee fausse ; bonnes / 10 * 100. L'affichage du badge k est envoye par le client (`{type:"badge_affiche", badge}`) ; le serveur borne par son propre horodatage de reception (Task 4).
    - logique : bonnes / 5 * 100 ; reponses au-dela de `LOGIQUE_DUREE * coef` ignorees.
    - situations : points / (2 * 3) * 100.
    - Toute question sans reponse = 0 point. Reponse en double a la meme question : la premiere fait foi.
  - `patrouilles.chemin(a: tuple, b: tuple) -> list[tuple] | None` (BFS 4 voisins, cases bloquees = piste hors passerelles + `TRAV`, ordre des voisins `[(1,0),(-1,0),(0,1),(0,-1)]` comme la maquette).
  - `patrouilles.rejouer(actions: list[dict], coef: float) -> dict` `{traites, manques, erreurs, ignores, detail_incidents}`. Algorithme (temps de jeu `t = t_ms / coef`) :
    ```
    incident i : ouvert a T_i = INCIDENTS[i].t*1000, echeance D_i = T_i + dl_i*1000
    moyen u : pos initiale, libre_a = 0
    pour chaque action {type:"engager", moyen, incident} triee par t :
      inc pas encore ouvert (t < T_i), deja echu (t >= D_i) ou deja attribue -> ignore (+ drapeau)
      t < u.libre_a -> ignore (+ drapeau "moyen_occupe")
      type(u) != type(inc) -> erreurs += 1 ; rien d'autre
      sinon : p = chemin(u.pos, inc.pos) ; arrivee = t + (len(p)-1)*PAS_MS
              inc traite si arrivee <= D_i ; u.pos = inc.pos ; u.libre_a = arrivee + SUR_PLACE_MS
    incidents jamais traites -> manques
    ```
  - `correction.duree_min_ms(epreuve, coef) -> int | None` : radio = `RADIO_PREMIER + somme des cadences` * coef ; patrouilles = `INCIDENTS[-1].t*1000 * coef` ; autres `None`. `correction.duree_max_ms(epreuve, coef) -> int` : radio = `duree_min + 5000` ; patrouilles = `110000*coef + 5000` ; acces = `10 * (ACCES_DELAI*coef + 1000) + 5000` ; logique = `LOGIQUE_DUREE*coef + 5000` ; appel, mc, situations = `PLAFOND_QUESTIONS*coef`.

- [ ] **Step 1: Ecrire les tests** : un test par epreuve sur la maquette : parcours parfait -> 100 (radio : les 7 messages « PC Est de » pris, la priorite H40 en moins de 2 s) ; un parcours mixte au score calcule a la main (radio : 5 pris a raison, 2 a tort, 0 rapide -> 43) ; `test_enonce_sans_reponses` (pour `appel`, `mc`, `logique`, `situations` : `json.dumps(enonce)` ne contient ni `"points"`, ni `"ans"`, ni `"a":`, et pour `logique` aucun choix n'est marque) ; `test_ordre_fixe_temps_reel` (deux rng differents -> meme ordre radio / patrouilles / acces) ; `test_ordre_melange_questions` (deux rng -> ordres differents pour `logique` sur 20 tirages) ; `test_reponse_hors_delai_logique_ignoree` ; `test_acces_decision_tardive_fausse` ; patrouilles : `test_chemin_contourne_la_piste` (de (0,0) a (0,11) passe par la ligne 1 ou 6 en colonne 6), `test_rejeu_glouton` (journal : a l'ouverture de chaque incident, engager le moyen du bon type des qu'il est libre ; nombre de traites calcule une fois a la main sur la maquette et fige dans le test, erreurs == 0), `test_mauvais_type_compte_erreur`, `test_moyen_occupe_ignore`, `test_patrouilles_coef_allonge_echeances` (un engagement arrive 2 s apres l'echeance a coef 1 est traite a coef 1.33).
- [ ] **Step 2: Lancer** `pytest tests/test_formation_correction.py tests/test_formation_patrouilles.py -v` -> FAIL
- [ ] **Step 3: Implementer** le JSON (copie fidele de la maquette), `banque.py`, `correction.py`, `patrouilles.py`
- [ ] **Step 4: Lancer** -> PASS
- [ ] **Step 5: Commit** `feat(formation): banque de questions et correction serveur des 7 epreuves`

### Task 4: Regles du passage unique

**Files:**
- Create: `formation/tentative.py`
- Test: `tests/test_formation_tentative.py`

**Interfaces:**
- Consumes: `store`, `banque.enonce`, `correction.score`, `correction.duree_min_ms`, `correction.duree_max_ms`
- Produces (toutes prennent `db` et `now: datetime` aware UTC) :
  - Etats d'epreuve : `a_faire`, `en_cours`, `terminee`, `abandonnee`. Etats de tentative : `en_cours`, `terminee`, `interrompue`, `reinitialisee`.
  - `connexion(db, candidat: dict, sid: str, ip: str, now) -> dict` : etat expose au client `{statut, epreuve_courante, epreuves: [{id, etat}], coef}` ; applique les regles de reconnexion : epreuve `en_cours` trouvee -> elle passe `abandonnee` (score calcule sur les actions recues, drapeau `abandon`) ; si `reprises == 0` et `now - debut <= 1 h` -> `reprises = 1`, trace `{date, epreuve, ip}`, la tentative continue a l'epreuve suivante ; sinon tentative `interrompue`. Plus d'une heure apres le debut sans epreuve en cours -> `interrompue` aussi. Ecrit `sid` dans la tentative (session unique). Sans tentative : `{statut: "non_commence"}`.
  - `commencer(db, candidat, sid, coef_reglage: float, now) -> dict` : cree la tentative (`coef = coef_reglage if candidat.amenagement else 1.0`, `debut = now`, epreuves toutes `a_faire`, `seed` aleatoire) ; idempotent si une tentative active existe (renvoie l'etat, ne recree rien).
  - `debut_epreuve(db, candidat, sid, epreuve: str, now) -> dict` : refuse (`ValueError("ordre")`) si ce n'est pas la premiere epreuve `a_faire` ; passe `en_cours`, `debut`, et stocke l'enonce tire (`banque.enonce(epreuve, Random(seed + index))`) ; renvoie l'enonce.
  - `action(db, candidat, sid, epreuve, donnees: dict, now) -> None` : refuse si l'epreuve n'est pas `en_cours` ou si `sid` differe (`PermissionError("session")`) ; ajoute `{**donnees, recu_ms}` (`recu_ms` = ms serveur depuis le debut de l'epreuve) ; `t_ms > recu_ms + 2000` -> `t_ms` ramene a `recu_ms` et drapeau `horodatage_incoherent` ; `recu_ms > duree_max_ms` -> action ignoree.
  - `fin_epreuve(db, candidat, sid, epreuve, now) -> dict` : `terminee`, `fin`, `score = correction.score(...)`, drapeau `incoherente` si duree serveur < `0.9 * duree_min_ms` ; derniere epreuve -> tentative `terminee`, `fin`.
  - `expirer_si_besoin(db, tentative, now) -> dict` : epreuve `en_cours` depuis plus de `duree_max_ms + 60 s` -> close comme `abandonnee` sans compter de reprise (le candidat a laisse l'onglet ouvert).
  - `reinitialiser(db, candidat_id, motif: str, auteur: str, now) -> None` : `ValueError` si `motif.strip()` vide ; tentative -> `reinitialisee` (conservee) ; `store.journal(db, "reinitialisation", auteur, candidat_id=..., motif=...)`.
  - `score_global(tentative, ponderations: dict) -> float | None` : moyenne ponderee des epreuves `terminee` ou `abandonnee`.

- [ ] **Step 1: Ecrire les tests** : `test_commencer_irreversible` (commencer deux fois -> une seule tentative) ; `test_ordre_des_epreuves_impose` ; `test_coupure_abandonne_et_continue` (debut radio, nouvelle connexion -> radio `abandonnee` avec score, epreuve courante `appel`, `reprises == 1`) ; `test_second_onglet_ne_rejoue_pas` (connexion 2 pendant radio en cours -> radio `abandonnee`, et `debut_epreuve(radio)` leve `ValueError`) ; `test_deuxieme_coupure_interrompt` ; `test_reconnexion_apres_une_heure_interrompt` ; `test_ancienne_session_refusee` (action avec l'ancien `sid` -> `PermissionError`) ; `test_coupure_hors_epreuve_sans_reprise` (connexion entre deux epreuves -> `reprises` inchange) ; `test_coef_fige` (amenagement active apres `commencer` -> coef inchange) ; `test_duree_incoherente_drapeau` ; `test_action_apres_duree_max_ignoree` ; `test_reinitialiser_motif_obligatoire` ; `test_reinitialiser_conserve_et_libere` (nouvelle tentative possible, l'ancienne en `reinitialisee`) ; `test_score_global_pondere`.
- [ ] **Step 2: Lancer** -> FAIL
- [ ] **Step 3: Implementer** (mises a jour atomiques `find_one_and_update` avec condition sur l'etat de l'epreuve et le `sid`, pour que deux requetes concurrentes ne puissent pas faire deux transitions)
- [ ] **Step 4: Lancer** -> PASS
- [ ] **Step 5: Commit** `feat(formation): regles du passage unique, aucune epreuve rejouee`

### Task 5: Acces public et API candidat

**Files:**
- Create: `formation/acces.py`
- Modify: `formation/routes_public.py`
- Test: `tests/test_formation_acces.py` (app Flask de test avec `CSRFProtect` actif et `WTF_CSRF_ENABLED=True`, comme Cockpit ; jeton lu dans la page d'accueil)

**Interfaces:**
- Consumes: Task 1 a 4
- Produces:
  - `acces.identifier(db, email: str, tel4: str, ip: str, now) -> dict | None` : candidat si la paire correspond (apres `normaliser_email` et `tel4` 4 chiffres) et que le candidat n'est pas bloque ; enregistre chaque echec dans `essais_connexion` (cles `email:<x>` et `ip:<y>`) ; `acces.est_bloque(db, email, ip, now) -> bool` (>= 5 echecs sur 15 min pour l'une des deux cles).
  - `acces.emettre_jeton(candidat_id: str, sid: str, now) -> str` et `acces.lire_jeton(jeton) -> dict | None` (HS256 `FORMATION_JWT_SECRET`, `exp` 1 h, `aud="formation"`). En dev, secret de repli `"dev-formation-secret-..."` (>= 32 car.).
  - `acces.etat_fenetre(reglages, now) -> "avant" | "ouvert" | "apres" | "non_configure"` (`non_configure` si `reply_to` ou `contact_rgpd` vide).
  - Cookie `formation_session` : httpOnly, `Secure` hors dev, `SameSite=Lax`, `path=/test-pc-organisation`, `max_age=3600`.
  - Routes (toutes avec `Cache-Control: no-store`, `Referrer-Policy: no-referrer`) :
    - `GET /test-pc-organisation` -> `accueil.html` (formulaire, ou page « pas encore ouvert / termine » selon la fenetre ; mention RGPD avec `contact_rgpd` et `conservation_jusqu_au`)
    - `GET /test-pc-organisation/test` -> `test.html` (redirige vers l'accueil sans session valide)
    - `POST /test-pc-organisation/api/connexion` `{email, tel4}` -> 200 `{ok: true, etat}` + cookie ; 401 `{ok: false, message: <message generique>}` ; 429 `{ok: false, message: <message generique> + " Réessayez dans 15 minutes."}` ; 403 `{ok: false, fenetre}` hors fenetre **sauf** tentative `en_cours` existante
    - `GET /test-pc-organisation/api/etat`
    - `POST /test-pc-organisation/api/commencer` `{accepte: true}` -> 400 si `accepte` n'est pas `true` ; 403 hors fenetre
    - `POST /test-pc-organisation/api/epreuve/<id>/debut` -> `{enonce}` ; `.../action` `{t_ms, type, ...}` ; `.../fin` -> `{ok, etat}`
    - `GET /test-pc-organisation/api/entrainement/<radio|patrouilles>` -> contenu d'entrainement (rien n'est enregistre)
  - Session invalide ou remplacee : 401 `{ok:false, error:"session"}` (le client renvoie a l'accueil).

- [ ] **Step 1: Ecrire les tests** : `test_message_generique_identique` (e-mail inconnu et bon e-mail + mauvais chiffres : meme statut, meme corps) ; `test_blocage_apres_5_echecs_email` et `..._ip` ; `test_connexion_email_majuscules_espaces` (` F.Arbia@GMAIL.com ` + `3179` reussit) ; `test_cookie_httponly_samesite` ; `test_post_sans_csrf_refuse` ; `test_hors_fenetre_refuse_commencer` ; `test_fenetre_fermee_tentative_en_cours_continue` ; `test_non_configure_tant_que_reply_to_vide` ; `test_enonce_api_sans_reponses` (reponse de `/debut` pour `logique` sans la cle `a`) ; `test_session_remplacee_401` ; `test_entetes_no_store`.
- [ ] **Step 2: Lancer** -> FAIL
- [ ] **Step 3: Implementer** `acces.py` et les routes (gabarits minimaux suffisants pour les tests ; leur habillage vient en Task 6)
- [ ] **Step 4: Lancer** -> PASS
- [ ] **Step 5: Commit** `feat(formation): identification candidat, session unique et API du test`

### Task 6: Pages candidat et epreuves (hors patrouilles)

**Files:**
- Create: `templates/formation/accueil.html`, `templates/formation/test.html`, `static/css/formation.css`, `static/fonts/formation/` (Exo 2 Regular, Bold, Italic, BoldItalic copiees de `~/Dropbox/ACO/2026/PC ORG REBOOT/Polices charte/` ; Inter 400/500/600 et JetBrains Mono 500/700 en woff2, sous-ensemble latin, depuis les paquets npm `@fontsource/inter@5.1.0` et `@fontsource/jetbrains-mono@5.1.1` via jsdelivr, telecharges une fois et versionnes), `static/js/formation/core.js`, `radio.js`, `appel.js`, `mc.js`, `acces.js`, `logique.js`, `situations.js`

**Interfaces:**
- Consumes: API de Task 5
- Produces:
  - `core.js` : `Formation.api(path, body)` (fetch + `X-CSRFToken` depuis `<meta name="csrf-token">`, 401 -> retour a l'accueil) ; `Formation.action(epreuve, donnees)` (envoi immediat, file de reemission sur echec reseau, `t_ms` mesure par `performance.now()` depuis le debut) ; `Formation.epreuves` = registre `{radio: {intro, jouer}, ...}` rempli par chaque module ; `Formation.son` (coupe par defaut, bouton « Son ») ; garde `beforeunload` active pendant une epreuve ; bandeau « Ne rechargez pas la page » en tete de chaque epreuve.
  - Chaque module d'epreuve expose `{intro(el, enonce), jouer(el, enonce, coef, fini)}` et appelle `fini()` a la fin ; il reprend le rendu et les textes de la maquette **sans** : notes DOA, scores, boutons « Recommencer » / « Vue candidat », coloration juste / faux des epreuves a question.

- [ ] **Step 1: Accueil** : bandeau PC Organisation (charte maquette), presentation, formulaire e-mail + 4 chiffres, mention RGPD (finalite : selection des operateurs du PC Organisation ; conservation jusqu'au 30 janvier 2027 ; droits d'acces et de suppression a `contact_rgpd`), message < 900 px (« Ce test se passe sur un ordinateur ou une tablette en mode paysage. Revenez depuis un ordinateur : vos identifiants restent valables. »), pages « pas encore ouvert » (date d'ouverture) et « termine ».
- [ ] **Step 2: Avertissement** (dans `test.html`, avant la tentative) : rappels sans ambiguite (spec 6.1, « Messages au candidat ») : une seule fois, aucune possibilite de recommencer ; toute epreuve interrompue (fermeture, rechargement, coupure) est perdue et comptee en l'etat ; au calme, seul, ordinateur ou tablette paysage, pas de telephone ; connexion stable (filaire ou bon Wi-Fi, eviter le partage 4G) ; batterie branchee, notifications coupees ; environ 25 minutes ; 7 epreuves ; pas de retour en arriere ; son conseille mais facultatif. Case a cocher au texte exact ; bouton « Commencer » actif seulement case cochee ; confirmation avant l'appel a `/commencer`.
- [ ] **Step 3: Prise de poste** (texte de la maquette `RENDER.brief`), entrainement radio (2 messages, sans enregistrement, rejouable), puis `radio.js` (cadence et seuil x coef, retour immediat garde, `action({type:"prise", msg})`), `appel.js`, `mc.js`, `logique.js`, `situations.js` (« Reponse enregistree » apres chaque choix, aucune coloration), `acces.js` (`badge_affiche` puis decision, delai 5 s x coef, retour immediat garde).
- [ ] **Step 4: Ecran de fin** (texte exact des Global Constraints), aussi pour une tentative `interrompue` ou deja `terminee`.
- [ ] **Step 5: Accessibilite** : contrastes de la maquette, `:focus-visible`, `prefers-reduced-motion` respecte (aucune animation), son coupe par defaut.
- [ ] **Step 6: Verifier** dans Chrome (dev, `CODING=true python app.py`, candidat fictif de Task 10 ou cree a la main) : parcours complet jusqu'a la fin ; dans l'onglet reseau et dans les sources, aucune requete hors `127.0.0.1`, aucune bonne reponse dans les enonces de `appel` / `mc` / `logique` / `situations` ; rechargement pendant `appel` -> epreuve suivante.
- [ ] **Step 7: Commit** `feat(formation): pages candidat et epreuves du test`

### Task 7: Epreuve des patrouilles (nouveau plan)

**Files:**
- Create: `static/js/formation/patrouilles.js`
- Modify: `static/css/formation.css`

**Interfaces:**
- Consumes: `Formation.action`, enonce `patrouilles` (Task 3), `GET .../entrainement/patrouilles`
- Produces: `action({type:"engager", moyen, incident})` et `action({type:"refus", moyen, incident})` ; moteur **au temps ecoule** : position et disponibilite d'un moyen calculees a partir de l'heure d'engagement, `PAS_MS = 430` et `SUR_PLACE_MS = 4000` en temps de jeu (x coef en temps reel), identique au rejeu de `formation/patrouilles.py`.

- [ ] **Step 1: Plan** pleine largeur de la colonne (rapport 12/8 garde), liste laterale dessous en dessous de 1200 px ; pions et incidents >= 44 px ; pion = couleur + lettre (P Patrouille `#2E8BF0`, T Tango `#8A6BE0`, S Secours `#E5534B`, M Moto `#1FA38A`) ; legende permanente sous le plan.
- [ ] **Step 2: Interaction** : selection incident -> moyen ou moyen -> incident ; reclic = deselection ; etat selectionne tres visible (contour blanc epais + halo) ; glisser-deposer d'un moyen sur un incident (Pointer Events) ; trajet en pointilles (SVG superpose) jusqu'a l'arrivee ; moyens incompatibles **non grises** : refus avec message et penalite (maquette) ; raccourcis 1 a 5 pour les moyens.
- [ ] **Step 3: Listes** : incidents tries par temps restant, compteur lisible, signal visuel (rouge clignotant sauf `reduced-motion`) et sonore (si son actif) sous 8 s ; mise a jour **en place** des textes (pas de `innerHTML` de la liste a chaque tic ; elements crees une fois, ordre change par deplacement de noeuds seulement quand l'ordre change).
- [ ] **Step 4: Entrainement** (1 incident, non note, rejouable) avant l'epreuve.
- [ ] **Step 5: Verifier** dans Chrome : un passage complet ; comparer le score serveur (detail de la tentative en base) au nombre d'incidents vus traites a l'ecran (doivent coincider) ; tester avec un candidat amenage (echeances allongees).
- [ ] **Step 6: Commit** `feat(formation): plan des patrouilles jouable et rejoue cote serveur`

### Task 8: Campagne d'e-mails

**Files:**
- Create: `formation/mailer.py`
- Modify: `formation/routes_admin.py`, `pcorg_summary_mail.py` (extraire la connexion SMTP dans `smtp_envoyer(message, destinataires) -> None` reutilisee par les deux modules, sans changer le comportement du rapport)
- Test: `tests/test_formation_mailer.py` (faux `smtplib.SMTP` par monkeypatch qui enregistre les messages)

**Interfaces:**
- Consumes: `store.reglages`, `store.journal`
- Produces:
  - `mailer.rendre(gabarit: dict, candidat: dict, reglages: dict) -> tuple[str, str, str]` (objet, texte, html) ; variables `{prenom}`, `{lien}`, `{date_limite}` (ex. « mercredi 2 décembre à 23 h 59 »), `{contact}` ; html = texte echappe et mis en paragraphes, liens cliquables, sans image ni pixel.
  - `mailer.envoyer_essai(db, adresse: str, auteur) -> None` (prenom « Camille », journalise).
  - `mailer.planifier(db, cible: "non_invites" | "relance_non_commences", auteur, now) -> int` : cree les `envois` `en_file` (jamais pour `fictif`, jamais deux invitations au meme candidat grace a l'index unique de Task 1 ; relance = type `relance`, un seul par candidat) ; refuse si `reply_to` vide.
  - `mailer.FilEnvoi` : `demarrer(db)`, `arreter()`, `etat() -> {en_file, envoyes, echecs, actif}` ; un fil daemon unique par processus ; traite les `en_file` un par un (`find_one_and_update` -> `en_cours`), un message par destinataire, `From: "<expediteur_nom> <SMTP_FROM>"`, `Reply-To: reply_to`, pause `debit_envoi_s` ; echec -> `echec` + erreur ; au demarrage de Cockpit, les `en_cours` orphelins repassent `en_file` et le fil redemarre s'il reste des `en_file`.
  - Routes admin : `POST /api/formation/admin/campagne/essai` `{adresse}`, `.../campagne/compter` `{cible}` -> `{n}`, `.../campagne/lancer` `{cible, confirmation_n}` (refus si `confirmation_n` != nombre recalcule), `.../campagne/arreter`, `.../campagne/renvoyer-echecs`, `GET .../campagne/etat`, `PUT .../gabarit`.
  - Gabarit par defaut (objet et corps, modifiables dans l'administration) :
    ```
    Objet : PC Organisation : votre test en ligne, à passer avant le {date_limite}

    Bonjour {prenom},

    Merci pour votre candidature au PC Organisation. Avant les sélections sur le Circuit, nous vous proposons un test en ligne d'environ 20 minutes : une vacation de nuit fictive en sept épreuves courtes. Aucune connaissance du site n'est nécessaire, toutes les règles sont expliquées à l'écran.

    Le test : {lien}
    Pour vous identifier : l'adresse e-mail utilisée dans le questionnaire et les 4 derniers chiffres de votre téléphone mobile.
    À passer avant le {date_limite}.

    Important : vous ne pouvez passer ce test qu'une seule fois, sans possibilité de recommencer. Une épreuve interrompue (fenêtre fermée, page rechargée, coupure de connexion) est perdue.
    Avant de commencer :
    - installez-vous seul, au calme, sur un ordinateur (pas de téléphone) ;
    - utilisez une connexion stable, branchez votre ordinateur et coupez les notifications ;
    - prévoyez 25 minutes sans interruption ; le son est conseillé mais facultatif.

    Vous avez besoin d'un temps aménagé ? Répondez à ce message avant de commencer le test.

    L'équipe de sélection vous contactera ensuite par e-mail ; les convocations partent au plus tard le 4 décembre.

    Ludovic Arnault
    Directeur des opérations adjoint
    Automobile Club de l'Ouest
    ```

- [ ] **Step 1: Ecrire les tests** : `test_rendre_variables` ; `test_un_message_par_destinataire` (aucun en-tete `To` a plusieurs adresses, aucun `Cc`/`Bcc`) ; `test_fictifs_exclus` ; `test_planifier_deux_fois_ne_double_pas` ; `test_relance_seulement_non_commences` ; `test_reply_to_obligatoire` ; `test_echec_smtp_marque_echec_et_continue` ; `test_reprise_apres_redemarrage` (`en_cours` orphelin -> renvoye une fois) ; `test_lancer_refuse_si_confirmation_differente` ; `test_rapport_pcorg_inchange` (le test existant `tests/test_pcorg_summary_ai.py` passe toujours).
- [ ] **Step 2: Lancer** -> FAIL
- [ ] **Step 3: Implementer**
- [ ] **Step 4: Lancer** `pytest tests/test_formation_mailer.py tests/test_pcorg_summary_ai.py -v` -> PASS
- [ ] **Step 5: Commit** `feat(formation): campagne d'e-mails avec suivi et reprise`

### Task 9: Administration

**Files:**
- Create: `templates/formation/admin.html` (gabarit Cockpit : `_sidebar.html`, `style.css`, comme `wiki_admin.html`), `static/css/formation_admin.css`, `static/js/formation/admin.js`
- Modify: `formation/routes_admin.py`
- Test: `tests/test_formation_admin.py`

**Interfaces:**
- Consumes: Task 2, 4, 8
- Produces (toutes `role_required("admin")`, accordables par la page `formation`) :
  - `GET /admin/formation` (onglets Candidats, Campagne, Resultats, Reglages)
  - `GET /api/formation/admin/candidats?statut=&q=` -> lignes `{id, forms_id, nom, prenom, email, statut ("non_commence"|"en_cours"|"terminee"|"interrompue"), debut, duree_s, scores: {epreuve: int|None}, global, amenagement, reprises, drapeaux, dernier_envoi}` (fictifs exclus sauf `?fictifs=1`)
  - `GET /api/formation/admin/tentative/<candidat_id>` -> tentative complete (reponses, actions, enonces tires, drapeaux, reprises, historique des tentatives reinitialisees)
  - `POST /api/formation/admin/candidat/<id>/amenagement` `{actif: bool}` (journalise ; sans effet sur une tentative deja commencee, l'interface le dit)
  - `POST /api/formation/admin/candidat/<id>/reinitialiser` `{motif}` (400 si vide)
  - `GET /api/formation/admin/stats` -> `{inscrits, invites, commences, termines, interrompus, temps_moyen_s, distribution: {epreuve: [nb par tranche de 10]}}` (graphiques Chart.js deja present dans Cockpit)
  - `GET /api/formation/admin/export.csv` et `export.xlsx` : premiere colonne `forms_id`, puis nom, prenom, e-mail, statut, debut, duree, scores par epreuve, global, amenagement, reprises, drapeaux (CSV `;` + BOM UTF-8 pour Excel)
  - `GET/PUT /api/formation/admin/reglages` (fenetre, coefficient, ponderations, reply_to, contact_rgpd, expediteur_nom, debit) ; bandeau de rappel de purge des le 23/01/2027
  - Import (Task 2) branche dans l'onglet Candidats avec affichage du rapport

- [ ] **Step 1: Ecrire les tests** : `test_admin_requiert_role` (faux module `app` comme `test_declarations.py`, role refuse -> 403) ; `test_reinitialiser_sans_motif_400` ; `test_amenagement_journalise` ; `test_export_csv_colonnes` (en-tete exact, `forms_id` en premier, fictifs exclus) ; `test_export_xlsx_lisible` (rouvert avec openpyxl) ; `test_stats_compte_les_statuts` ; `test_statut_non_commence_sans_tentative`.
- [ ] **Step 2: Lancer** -> FAIL
- [ ] **Step 3: Implementer** routes, page et `admin.js` (tableau triable et filtrable, detail en panneau, confirmations, CSRF via `apiPost`/`X-CSRFToken`)
- [ ] **Step 4: Lancer** -> PASS ; verification visuelle dans Chrome
- [ ] **Step 5: Commit** `feat(formation): administration des candidats, campagne, resultats et reglages`

### Task 10: Candidats fictifs, purge, documentation

**Files:**
- Create: `scripts/formation_fictifs.py`, `scripts/formation_purge.py`, `docs/claude/formation.md`
- Modify: `CLAUDE.md` (ligne dans « Documentation par domaine » ; variables `FORMATION_JWT_SECRET`)
- Test: `tests/test_formation_scripts.py`

**Interfaces:**
- Produces:
  - `python scripts/formation_fictifs.py creer|supprimer` : 5 candidats `fictif: True`, e-mails `candidat1@test.invalid` a `candidat5@test.invalid`, mobiles `0600000001` a `0600000005`, `candidat5` avec amenagement ; `supprimer` efface ces candidats, leurs tentatives et envois, rien d'autre.
  - `python scripts/formation_purge.py --campagne pcorg-2026-12 --confirmer` : supprime candidats, tentatives, envois, imports, essais et journal de la campagne ; sans `--confirmer`, affiche seulement les comptes.
  - `docs/claude/formation.md` : collections, regles du passage unique, contrat d'API, correction, campagne, reimport, purge, pieges.

- [ ] **Step 1: Ecrire les tests** : `test_supprimer_fictifs_ne_touche_que_fictifs` ; `test_purge_sans_confirmer_ne_supprime_rien` ; `test_purge_campagne`.
- [ ] **Step 2: Lancer** -> FAIL ; **Step 3: Implementer** ; **Step 4: Lancer** -> PASS ; `pytest tests/test_formation_*.py -v` complet -> PASS ; `graphify update .`
- [ ] **Step 5: Commit** `feat(formation): candidats fictifs, purge et documentation`

### Task 11: Recette de bout en bout (dev) et livrables

- [ ] **Step 1:** Base dev vide, import du vrai export, creation des fictifs, reglages (reply_to et contact provisoires du DOA), fenetre ouverte.
- [ ] **Step 2:** Dans Chrome : passage complet avec `candidat1` (captures de chaque ecran), passage avec rechargement pendant une epreuve (`candidat2`), passage amenage (`candidat5`), tentative de 6 identifications fausses (blocage), largeur < 900 px (message).
- [ ] **Step 3:** Administration : tableau, detail, reinitialisation de `candidat2` avec motif, export CSV et XLSX ouverts, statistiques.
- [ ] **Step 4:** Envoi d'essai depuis l'administration (si le relais SMTP est joignable depuis le poste de dev ; sinon note pour la prod) vers une adresse Gmail et une Outlook.com fournies par le DOA ; verifier boite de reception et non spam.
- [ ] **Step 5:** Presenter au DOA : captures, resultats des tests, liste des pre-requis de prod (`FORMATION_JWT_SECRET`, base `cockpit_formation` dans la sauvegarde, `https://cockpit.lemans.org/test-pc-organisation` joignable depuis Internet sans le portail, reglages saisis, fictifs supprimes) ; marche a suivre de reimport ; texte final de l'e-mail. **Aucun deploiement en production sans son accord explicite.**
