<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Formation : test en ligne du PC Organisation (`formation/`) - 23/11/2026

Cas d'usage : environ 555 candidats operateurs du PC Organisation passent **une seule fois** un test de 7 epreuves (~20 min) avant les selections des 12 et 13/12. Le score aide le DOA a choisir qui convoquer, **aucune elimination automatique**. Trois briques : campagne d'e-mails depuis Cockpit, page publique hors portail `/test-pc-organisation`, administration `/admin/formation` (`@role_required("admin")`, entree `formation` du registre des pages d'administration). Spec : `docs/superpowers/specs/2026-11-22-formation-test-pcorg-design.md` (fait foi).

### Base dediee et collections

`formation.store.get_db()` : `cockpit_formation_dev` en dev, `cockpit_formation` si `TITAN_ENV=prod`. Le module n'ecrit **jamais** dans `titan` / `titan_dev` (a ajouter a la sauvegarde Mongo de prod). Campagne unique `store.CAMPAGNE = "pcorg-2026-12"`, portee par chaque document.

- `candidats` : `forms_id`, `nom`, `prenom`, `email` (minuscules, sans espaces), `tel` (chiffres, `+33` -> `0`), `tel4`, `amenagement` (pose a la main, journalise), `fictif`, `importe_le`, `maj_le`, `forms_repondu_le`. Index unique `(campagne, email)`.
- `tentatives` : une active par candidat (index unique partiel sur `en_cours` / `terminee` / `interrompue`, `reinitialisee` exclue). `debut`, `fin`, `coef` (fige au clic Commencer), `seed`, `sid`, `reprises`, `reprises_trace`, `epreuves {id: {etat, debut, fin, enonce, actions, score, detail, drapeaux}}`.
- `envois` : un doc par e-mail (`invitation` / `relance` / `test`, statut `en_file` / `en_cours` / `envoye` / `echec`). Deux index uniques partiels (invitation, relance) : jamais deux invitations au meme candidat.
- `imports` (rapport de chaque import), `journal_admin` (reinitialisations avec motif, amenagements, reglages **modifies seulement**, campagne), `essais_connexion` (echecs d'identification, TTL 15 min), `reglages` (`_id` = campagne : fenetre, coefficient, ponderations, gabarit, reply-to, contact RGPD, `campagne_arretee`).
- La banque (`formation/banque_pcorg.json`, `formation/banque.py`) est un fichier versionne, pas une collection. `banque.secrets_par_id(epreuve)` rend les reponses attendues (admin seulement) ; les enonces envoyes au candidat n'en contiennent jamais.

### Regles du passage unique (`tentative.py`, decision du DOA du 22/11)

- **Aucune epreuve n'est jamais rejouee.** Chaque action part au serveur au fil de l'eau. Epreuve `en_cours` trouvee a la connexion : close en l'etat (`abandonnee`), notee sur ce que le serveur a recu (le reste = manques).
- **Une seule reprise**, dans l'heure qui suit le debut de la tentative : le candidat continue a l'epreuve **suivante** (tracee dans `reprises_trace`). Deuxieme coupure ou retour apres 1 h : tentative `interrompue`, page de fin. Coupure hors epreuve (prise de poste, ecran de regles) : rien de perdu, pas une reprise.
- **Chaque chargement de `/test` tourne la session** (nouveau `sid`, cookie reemis, ancien onglet en 401 `session`) : un second onglet ou un rechargement clot l'epreuve en cours. `/api/etat` ne tourne jamais la session. **Prechargement / prerendu** (`Sec-Purpose`, `Purpose`) ignores : 204, aucune rotation.
- Fenetre fermee : seul un nouveau depart est refuse, une tentative en cours continue (dans l'heure).
- Reinitialisation (DOA, motif obligatoire, journalisee) : l'ancienne tentative passe `reinitialisee`, le candidat repart de zero.
- Chaque transition est un `find_one_and_update` conditionne par l'etat lu : deux requetes concurrentes ne font pas deux transitions.

### Contrat d'API

Public (`/test-pc-organisation`, CSRF obligatoire sur toute ecriture, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, IP = `request.remote_addr` jamais `X-Forwarded-For`) : `GET /` (accueil), `GET /test` (venu d'un autre site, `Sec-Fetch-Site: cross-site` : redirection vers l'accueil sans toucher a la session), `POST /api/connexion` (e-mail + 4 chiffres, message generique unique, 5 echecs / 15 min par e-mail OU IP ; e-mail bloque : la bonne paire passe encore depuis une IP sans autre essai dans la fenetre, pour qu'un tiers qui connait l'e-mail ne bloque pas le candidat), `GET /api/etat`, `POST /api/commencer` (case cochee), `POST /api/epreuve/<id>/debut` (renvoie l'enonce sans reponses et `ecoule_ms` ; reponse perdue : la **meme session** qui redemande l'epreuve deja en cours **sans aucune action recue** obtient le meme enonce, sans nouvelle transition, et recale son chrono sur `ecoule_ms` ; apres une action, 409 `ordre`. Le client rejoue `/debut` sur coupure ou timeout, jamais `/etat` qui clorait l'epreuve), `.../action` (16 Ko max), `.../fin`, `GET /api/csrf` (renouvelle le jeton sans recharger la page, un rechargement clot l'epreuve), `GET /api/entrainement/<radio|patrouilles>` (rien n'est enregistre). Session : cookie `formation_session`, JWT HS256 (`FORMATION_JWT_SECRET`), 1 h, **glissant** : `/api/commencer` et chaque `/debut` le reemettent (meme `sid`, heure neuve). Les appels d'API partent avec `referrerPolicy: "same-origin"` (controle HTTPS du Referer de Flask-WTF si `X-Forwarded-Proto` devient fiable) ; les pages gardent `Referrer-Policy: no-referrer`.

Admin (`/api/formation/admin/...`, admin) : `etat`, `candidats`, `tentative/<id>`, `candidat/<id>/amenagement`, `candidat/<id>/reinitialiser`, `stats`, `export.csv`, `export.xlsx`, `reglages` (GET / PUT), `import`, `gabarit` (PUT) et `gabarit/apercu`, `campagne/{essai,compter,lancer,arreter,reprendre,renvoyer-echecs,etat}`. Les `fictif` sont exclus des stats, exports et envois.

### Correction (`correction.py`, fonctions pures)

Le serveur rejoue le journal du navigateur ; il ne lit jamais un score envoye. Temps de jeu = `t_ms / coef` partout (coef 1,33 avec amenagement : cadence radio, seuil PRIORITE 2 s, echeances des incidents, 5 s par badge, 4 min de logique). Question sans reponse = 0 ; reponse en double : la premiere fait foi (drapeau `doublon`) ; action reemise (meme `aid`) : seule la premiere compte. Drapeaux (`horodatage_incoherent`, `action_inconnue`, `doublon`) a lire dans le detail d'une tentative, jamais d'elimination.

- radio : `(pris a raison - pris a tort) / nb pour moi * 100 + 5 par PRIORITE prise en <= 2 s`, prise valable seulement pendant l'affichage du message.
- 38 17 et mises en situation : points 2/1/0, `points / (2 * nb) * 100`. Main courante : 1 point par champ juste sur 4. Logique : bonnes / nb, au-dela de 4 min ignore.
- acces : decision par badge, fausse si apres 5 s (x coef) depuis la reference (decision du badge precedent) ; bonnes / 10.
- patrouilles : **rejeu deterministe en Python** (`patrouilles.py`, meme plus court chemin que le client, 0,43 s par case, 4 s sur place) ; `(traites - 0,5 * erreurs) / nb * 100`. **Test de parite** `tests/test_formation_patrouilles_js.py` : le moteur JS (`static/js/formation/patrouilles.js`) et le rejeu rendent les memes resultats sur les memes journaux. Toute modification de l'un exige de rejouer ce test.
- Score global (`tentative.score_global`) = moyenne ponderee des epreuves terminees ou abandonnees (ponderations des reglages, 1 partout). Tentative close (`terminee` ou `interrompue`) : les epreuves jamais jouees comptent **0** avec leur poids (un candidat interrompu ne passe pas devant un candidat complet) ; tentative en cours : epreuves jouees seules.

### Campagne d'e-mails (`mailer.py`)

Lien `{lien}` du gabarit : `FORMATION_PUBLIC_URL` + `/test-pc-organisation` (defaut `https://cockpit.lemans.org`). Jamais `COCKPIT_PUBLIC_URL` / `PUBLIC_BASE_URL`, qui servent a d'autres usages (`PUBLIC_BASE_URL` vaut safe.lemans.org ailleurs).

Un message par destinataire via le relais SMTP de Cockpit (`pcorg_summary_mail.smtp_envoyer`), un fil daemon par processus, un e-mail toutes les `debit_envoi_s` (2 s). Cibles : `non_invites` (invitation) ou `relance_non_commences` (relance) ; jamais aux `fictif`.

- **Confirmation du nombre exact** : `lancer` / `planifier` refusent (`confirmation_differente`) si le nombre confirme n'est pas celui recalcule. `reply_to` vide (`reply_to_vide`) ou `contact_rgpd` vide (`contact_rgpd_vide`, spec 12) : refus.
- **Arret persistant** : `campagne_arretee` dans les reglages, survit a un redemarrage ; les `en_file` restent en file. `reprendre` leve l'arret.
- **Orphelins** : un `en_cours` de plus de 5 min (fil mort) repasse `en_file` (`remettre_orphelins`, aussi au demarrage, sauf campagne arretee). Les echecs se renvoient en bloc.
- **Jamais deux invitations** : index uniques partiels + insertion qui ignore `DuplicateKeyError` ; relancer la campagne apres un double clic ou un redemarrage ne double rien.
- L'envoi d'essai utilise le gabarit **enregistre** : le bouton est inactif tant que le texte edite differe (enregistrer d'abord).

### Reimport (marche a suivre, page `/admin/formation`)

1. Exporter le questionnaire Forms en .xlsx (colonnes lues : `Id`, `Heure de fin`, `Nom1`, `Prénom`, `Téléphone mobile`, `Adresse e-mail`, rien d'autre).
2. Onglet **Candidats**, televerser le fichier. Le rapport donne crees / mis a jour / inchanges, doublons (on garde la reponse la plus recente), lignes inexploitables (e-mail ou mobile invalide) et **absents du fichier** (jamais supprimes).
3. Verifier le rapport (conserve dans `imports`), corriger a la source les inexploitables, reimporter si besoin : upsert par e-mail, ne touche ni `tentatives` ni `envois` ni `amenagement`.
4. Poser les amenagements demandes par e-mail (bascule par candidat, journalisee) **avant** que le candidat commence : le coefficient est fige au clic Commencer.
5. Onglet **Campagne** : essai a une adresse Gmail et une Outlook.com, puis « Compter », confirmer le nombre, lancer. Relancer plus tard avec la cible « invites qui n'ont pas commence ».

### Purge du 30/01/2027

Toutes les donnees du module sont supprimees le 30/01/2027 (mention RGPD en consequence ; pas de TTL, une suppression silencieuse en pleine selection serait pire qu'un oubli). Rappel affiche dans l'administration des le 23/01. Exporter les resultats avant, puis : `python scripts/formation_purge.py --campagne pcorg-2026-12` (a blanc, comptes seuls) puis `... --confirmer`. Supprime candidats, tentatives, envois, imports, essais, journal et reglages de la campagne.

Recette : `python scripts/formation_fictifs.py creer` (5 fictifs `candidat1@test.invalid` a `candidat5@test.invalid`, mobiles `0600000001` a `0600000005`, 4 chiffres `000N`, le 5 avec amenagement) puis `supprimer` (eux, leurs tentatives et envois, rien d'autre). **Supprimer les fictifs avant la campagne** (`python scripts/formation_fictifs.py supprimer`) et **ne jamais en creer en prod**. Les scripts lisent la base via `store.get_db()` (dev sauf `TITAN_ENV=prod`).

### Variables d'environnement

- `FORMATION_JWT_SECRET` : 32 caracteres minimum, **obligatoire en prod** (`app.py` refuse de demarrer sinon ; `acces.py` refuse hors dev). Cookie `Secure` hors dev.
- `FORMATION_PUBLIC_URL` : adresse publique de Cockpit pour le lien des e-mails (defaut `https://cockpit.lemans.org`, suffixe `/test-pc-organisation` ajoute).

### Indicatif radio

Decision du DOA (22/11) : le candidat est **PC Org**, operateur Sud-Est du PC Organisation sur le **canal Sud-Est** (bandeau « CANAL SUD-EST · PC ORG »). Jamais « PC Est », « PC Orga », « PC Nord / Sud / Village ». Un message radio commence par le destinataire puis l'emetteur : seuls ceux qui commencent par « PC Org de... » sont pour lui ; les echanges entre equipes de terrain et les messages emis par le PC Org ne le sont pas.

### Pieges

- Un rechargement de `/test` ou un second onglet **clot l'epreuve en cours** : ne jamais ajouter de rechargement automatique cote client. CSRF perime : `GET /api/csrf` puis un seul rejeu ; `core.js` ne recharge la page qu'en dernier recours, si ce renouvellement echoue.
- Aucun score ni bonne reponse cote candidat ; epreuves a question : « Reponse enregistree ». Appel, main courante, logique, situations : `points`, `ans`, `a`, `ok` retires de l'enonce (`banque.secrets_par_id`, admin seulement). **Voulu** pour radio, patrouilles et acces : `pour_moi` / `priorite` (radio), types d'incidents (patrouilles) et `ok` (acces) partent au client, le jeu en a besoin pour son retour immediat ; ce n'est pas une fuite a corriger, le score reste calcule par le serveur sur le journal.
- `csrf.exempt()` interdit, y compris sur les routes publiques.
- Pages candidat : aucune ressource tierce (polices dans `static/fonts/formation/`).
- Formats : e-mail et mobile normalises (`normalise.py`) a l'import comme a la connexion (majuscules, espaces, `+33 6...`) ; la paire e-mail + 4 chiffres identifie, deux candidats peuvent partager les 4 chiffres.
- Un `aid` repete (reemission apres coupure reseau) ne compte qu'une fois.
- Ne jamais modifier les donnees de `titan` / `titan_dev` depuis ce module.
