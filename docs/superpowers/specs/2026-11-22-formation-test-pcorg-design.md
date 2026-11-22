# Module Formation : campagne d'e-mails et test en ligne du PC Organisation

Date : 22/11/2026. Statut : conception relue (approche A), decisions du DOA en section 12, en attente de validation.

Sources : `~/Dropbox/ACO/2026/PC ORG REBOOT/Prompt_ClaudeCode_test_en_ligne.md` (cahier des charges detaille,
fait foi sur tout ce que ce document ne precise pas), `test_pcorg.html` (maquette jouable, 7 epreuves),
`Candidature _ operateur du PC Organisation.xlsx` (export Forms, 566 lignes au 22/11).

## 1. Intention

Le DOA adjoint recrute une quarantaine d'operateurs du PC Organisation. Plus de 560 personnes ont rempli le
questionnaire (ouvert jusqu'au 30/11). Avant de convoquer aux selections sur site des 12 et 13 decembre
(convocations envoyees au plus tard le 04/12), chaque candidat passe **une seule fois** un test en ligne
d'environ 20 minutes. Le score aide le DOA a choisir qui convoquer : **aucune elimination automatique**.

Premiere brique d'un module « Formation » de Cockpit : (1) envoyer depuis Cockpit l'e-mail d'invitation a tous
les candidats, (2) une page publique, hors portail TITAN, ou le candidat s'identifie et passe le test,
(3) l'administration pour le DOA.

**Calendrier dur** : import final et envoi des e-mails le **lundi 30/11** ; tout doit etre en production et
recette avant. Fenetre du test proposee : 30/11 a l'envoi -> mercredi 02/12 23:59 (reglable).

## 2. Objectifs, non-objectifs, anti-objectifs

Objectifs : tout le cahier des charges (sections 1 a 8 du prompt), plus la campagne d'e-mails.

Non-objectifs (cette fois) : convocations aux sessions de decembre depuis Cockpit ; banque de questions
alimentee par le wiki des procedures ou par les fiches pcorg (la structure le permettra plus tard) ; parcours
de formation des operateurs retenus ; version audio des messages radio.

Anti-objectifs (echec meme si « ca marche ») :
- un candidat qui ne peut pas passer le test a cause d'un bug, ou qui le passe deux fois ;
- les bonnes reponses des epreuves a question lisibles dans le navigateur ;
- un e-mail qui revele qu'une adresse est (ou n'est pas) candidate ;
- les e-mails en spam chez la majorite des candidats ;
- toute donnee existante de Cockpit modifiee, toute deduction du sexe, tout traceur tiers ;
- un score montre au candidat.

## 3. Approches considerees

- **A. Module `formation` dans Cockpit (retenu).** Blueprint Flask, base MongoDB dediee, pages publiques sans
  portail, administration derriere le portail, envoi par le relais SMTP deja utilise par Cockpit. Suit les
  conventions existantes (crise, controle), un seul deploiement.
- B. Application separee : isolation totale, mais un deploiement, une exposition Internet et une sauvegarde de
  plus a monter en 8 jours, et contraire a l'idee d'un module de Cockpit. Ecartee.
- C. Maquette branchee telle quelle sur une API : plus rapide, mais reponses dans le code client et maquette
  monolithique difficile a faire evoluer. Ecartee.

## 4. Donnees

**Base MongoDB dediee** (appel du concepteur, pour respecter « ne jamais modifier titan_dev ni les donnees
existantes ») : `cockpit_formation_dev` en dev, `cockpit_formation` en prod, meme `MONGO_URI`. Le module
n'ouvre jamais `titan` / `titan_dev` en ecriture. A ajouter a la sauvegarde Mongo de prod.

Chaque document porte `campagne` (`pcorg-2026-12`) pour que le module serve a d'autres tests.

| Collection | Contenu |
|---|---|
| `candidats` | `_id`, `campagne`, `forms_id`, `nom`, `prenom`, `email` (minuscules, sans espaces), `tel` (chiffres, `+33`->`0`), `tel4`, `amenagement` (bool, **pose a la main**, cf. 4.1), `fictif` (bool), `importe_le`, `maj_le`, `forms_repondu_le` |
| `imports` | rapport de chaque import : fichier, nb lignes, crees, mis a jour, doublons (detail), inexploitables (detail), auteur, date |
| `envois` | un doc par e-mail envoye : candidat, type (`invitation`, `relance`, `test`), statut (`en_file`, `envoye`, `echec`), erreur SMTP, date, auteur de la campagne |
| `tentatives` | une par candidat et campagne (index unique) : statut, debut, fin, epreuves (cf. 6), reprises, session active, coefficient applique, scores serveur, drapeaux d'incoherence |
| `journal_admin` | reinitialisations (motif obligatoire), changements d'amenagement, envois de campagne, modifications de reglages |
| `essais_connexion` | echecs d'identification par e-mail et par IP, index TTL 15 min |
| `questions` | banque : `epreuve`, `id`, `contenu`, `bareme` ; graine depuis `formation/banque_pcorg.json` (contenu de la maquette) |
| `reglages` | par campagne : ouverture, fermeture, coefficient d'amenagement (1,33), ponderations, textes RGPD, gabarit d'e-mail, expediteur / reply-to |

### 4.1 Import

- Colonnes lues : `Id`, `Heure de fin`, `Nom1` (la colonne `Nom` est vide), `Prenom`, `Telephone mobile`,
  `Adresse e-mail`. Rien d'autre n'est importe (minimisation ; `Adresse de messagerie` vaut « anonymous »).
- **L'export n'a pas de colonne d'amenagement.** `amenagement` est faux a l'import et ne peut etre active que
  par le DOA dans l'administration (journalise). L'e-mail d'invitation invite a le demander par retour d'e-mail
  avant de commencer.
- Doublons d'e-mail : on garde la reponse la plus recente (`Heure de fin`) ; listes des doublons et des lignes
  inexploitables (e-mail ou telephone manquant ou invalide) dans le rapport d'import affiche et conserve.
- Relancable : upsert par `email` ; ne touche jamais `tentatives` ni `envois` ; un candidat absent du nouvel
  export n'est pas supprime (signale dans le rapport).
- Deux candidats d'e-mails differents avec les memes 4 chiffres : sans effet (la paire e-mail + 4 chiffres
  identifie).

## 5. Campagne d'e-mails

- Onglet « Campagne » de l'administration : gabarit (objet + corps texte et HTML simple, variables
  `{prenom}`, `{lien}`, `{date_limite}`), apercu, **envoi d'essai** a une adresse saisie, puis envoi.
- Cibles : « candidats jamais invites » (defaut), ou relance « invites qui n'ont pas commence » ; jamais aux
  `fictif`. Confirmation affichant le nombre exact de destinataires.
- Envoi par le relais SMTP de Cockpit (meme fonction que `pcorg_summary_mail.py` : `SMTP_HOST`, `SMTP_FROM`...)
  dans un fil d'arriere-plan du processus web, un e-mail toutes les 2 s (reglable), un destinataire par message
  (jamais de liste visible). L'etat vit dans `envois` : un redemarrage du serveur reprend les `en_file`. Bouton
  d'arret. Compteurs envoyes / echecs, renvoi des echecs.
- Le lien est le meme pour tous (`https://cockpit.lemans.org/test-pc-organisation`) ; l'identification se fait
  sur la page. Le texte de l'e-mail rappelle : identifiants (e-mail du questionnaire + 4 derniers chiffres du
  mobile), duree ~25 min, une seule fois, ordinateur, date limite, demande d'amenagement par retour d'e-mail.
- Deliverabilite : essai vers une adresse Gmail et une Outlook.com des le premier lot livre (avant le 26/11).
  En cas de spam, repli sur l'envoi par le compte Microsoft 365 du DOA (decision a ce moment-la).

## 6. Parcours candidat (pages publiques)

Pages sans `role_required`, gabarits autonomes (pas de `base.html` Cockpit, pas de barre laterale), charte de la
maquette : fond nuit, Exo 2 (titres, police de la charte ACO, servie localement depuis `static/fonts`, pas de
Google Fonts : aucun appel tiers), Inter, JetBrains Mono, servies localement elles aussi. CSRF actif (jeton dans
le gabarit, en-tete `X-CSRFToken`). En-tetes : `Cache-Control: no-store`, `Referrer-Policy: no-referrer`.

1. **Accueil `/test-pc-organisation`** : presentation, mention d'information sur les donnees, formulaire
   e-mail + 4 chiffres. Hors fenetre : page d'information (avant / apres). Moins de 900 px de large : message
   invitant a utiliser un ordinateur ou une tablette en paysage (n'empeche pas la reconnexion plus tard).
2. **Identification** : message d'erreur generique unique ; 5 echecs par e-mail OU par IP (`request.remote_addr`,
   jamais l'en-tete X-Forwarded-For, cf. app.py) sur 15 min -> blocage temporaire (meme message generique +
   « reessayez dans 15 minutes »). Succes : cookie `formation_session` httpOnly, Secure (en prod), SameSite=Lax,
   1 h, JWT HS256 signe par `FORMATION_JWT_SECRET` (refus de demarrage en prod si vide), portant candidat et
   `sid`. Le `sid` est ecrit dans la tentative : une nouvelle connexion invalide l'ancienne session (une seule
   session active).
3. **Avertissement** : rappels (duree, 7 epreuves, pas de retour, son facultatif, ordinateur ou tablette
   paysage) et case a cocher au texte exact du prompt. « Commencer » cree la tentative (irreversible).
4. **Test** : prise de poste, puis les epreuves dans l'ordre, entrainements compris. Aucun score, aucune bonne
   reponse affichee pour les epreuves a question (« Reponse enregistree »). Retours immediats gardes pour radio,
   patrouilles, controle d'acces.
5. **Fin** : remerciement et suite (« L'equipe de selection vous contactera par e-mail ; les convocations partent
   au plus tard le 4 decembre »). Candidat deja termine qui se reconnecte : meme page.

### 6.1 Regle du passage unique et reprises

Etats d'une epreuve : `a_faire`, `en_cours` (debut serveur), `terminee` (figee, fin serveur, score), `abandonnee`.
Etats de la tentative : `en_cours`, `terminee`, `interrompue` (close avec les epreuves faites), `reinitialisee`.

- Coupure (onglet ferme, reseau) : le candidat se reconnecte ; si l'heure qui suit le debut de la tentative
  n'est pas ecoulee et qu'il n'a encore jamais repris, il reprend **au debut de l'epreuve interrompue**
  (l'epreuve interrompue est rejouee, les terminees restent figees). La reprise est tracee (date, epreuve, IP).
- Deuxieme interruption, ou reconnexion plus d'une heure apres le debut : tentative `interrompue`, page de fin.
- Est une interruption : une connexion alors qu'une epreuve est `en_cours`. Le simple rechargement de la page
  pendant une epreuve compte donc comme reprise (le candidat en est averti sur l'ecran d'avertissement).
- Une epreuve rejouee recoit un nouveau tirage d'ordre (questions et choix) ; avec la banque actuelle le
  contenu est le meme (limite acceptee, signalee dans l'administration par le compteur de reprises).
- Reinitialisation par le DOA : motif obligatoire, journalisee ; l'ancienne tentative est conservee en
  `reinitialisee` (consultable), le candidat peut recommencer de zero.

### 6.2 Chronometres et amenagement

- Le serveur enregistre debut et fin de chaque epreuve. Tous les temps du test (cadence radio, seuil PRIORITE
  2 s, delais des incidents, 5 s par badge, 4 min de logique, duree maximale de chaque epreuve) sont multiplies
  par le coefficient si `amenagement` (1,33, reglable) ; le coefficient est fige dans la tentative au depart.
- Durees impossibles : reponse arrivee apres la limite de l'epreuve (x coefficient, + 5 s de marge reseau)
  -> ignoree ; epreuve temps reel terminee plus vite que sa duree minimale de deroulement -> score calcule mais
  epreuve marquee `incoherente` dans l'administration (pas d'elimination automatique).

## 7. Epreuves : contrat client / serveur

Principe : `POST /api/formation/epreuve/<id>/debut` -> le serveur marque le debut et renvoie l'enonce tire au
hasard **sans les reponses** des epreuves a question ; le client renvoie reponses ou journal ;
`.../fin` fige l'epreuve. Le moteur client est decoupe en un module JS par epreuve
(`static/js/formation/*.js`), recopie de la maquette moins les notes DOA, le tableau des scores, les boutons
« Recommencer » et « Vue candidat ».

| Epreuve | Envoye au client | Retour client | Correction serveur |
|---|---|---|---|
| Radio (14 messages) | textes, ordre, cadence (x coef), drapeau PRIORITE | journal `{msg, t_ms}` des prises | recalcul : pris a raison, a tort, priorites < 2 s (x coef), formule de la maquette |
| 38 17 (2 appels) | repliques et choix melanges, sans bareme | choix par etape | bareme 2/1/0 |
| Main courante (3) | message, listes | 4 champs par fiche | 1 point par champ juste |
| Patrouilles (8 incidents) | plan, moyens, incidents et horaires | journal des engagements `{t_ms, moyen, incident}` et refus | **rejeu deterministe en Python** (meme plus court chemin, 0,43 s par case, 4 s sur place) : engagement illegal (moyen occupe, mauvais type) compte en erreur, incident traite si arrivee avant echeance |
| Controle d'acces (10 badges) | badges melanges, regle | decision et `t_ms` par badge | recalcul ; decision apres 5 s (x coef) = pas de decision |
| Logique (5) | enonces, choix melanges | choix | bonne reponse |
| Mises en situation (3) | situations, choix melanges | choix | bareme 2/1/0 |

Pour les epreuves temps reel, le client connait ce qu'il faut pour le retour immediat (c'est le jeu) ; le score
qui compte est celui du serveur. Le moteur des patrouilles cote client passe au temps ecoule (et non a
l'accumulation de `setInterval`) pour que client et serveur tombent d'accord.

Entrainements non notes : 2 messages avant la radio, 1 incident avant les patrouilles ; rejouables a volonte,
pas d'enregistrement de score.

**Banque de questions** : `formation/banque_pcorg.json` versionne, charge dans `questions` au demarrage du
module si absent (ou par commande de reimport). Une epreuve tire N elements de la banque (N = taille actuelle).

## 8. Plan des patrouilles (section 5 du prompt)

Repris tel quel du prompt : plan pleine largeur, pions et incidents plus gros, selection dans les deux sens avec
deselection, glisser-deposer, trajet en pointilles, couleur + lettre par specialite et legende permanente, pas
de grisage des moyens incompatibles (refus + message + penalite), liste triee par temps restant, signal sonore
et visuel sous 8 s, listes mises a jour en place, raccourcis clavier 1 a 5. Regles de la maquette inchangees.

## 9. Administration `/admin/formation`

`@role_required("admin")` + entree `formation_admin` dans le registre des pages d'administration (accordable a
un groupe). Onglets :
- **Candidats** : import (televersement .xlsx, rapport), tableau filtre (statut non commence / en cours /
  termine / interrompu, date, duree, score par epreuve, global, amenagement, reprises, dernier envoi), bascule
  amenagement, detail d'une tentative (reponses, journal d'actions, drapeaux), reinitialisation avec motif.
- **Campagne** : cf. 5.
- **Resultats** : nombre de termines, distribution des scores par epreuve, temps moyen, export CSV et XLSX
  (candidat, statut, scores, global, amenagement, reprises, drapeaux).
- **Reglages** : fenetre d'ouverture, coefficient, ponderations (score global = moyenne ponderee des epreuves
  terminees, defaut 1 partout), mention RGPD, expediteur / reply-to.

Toute ecriture passe par le CSRF de Cockpit.

## 10. Qualite et livraison

- Tests pytest (`tests/test_formation_*.py`) : import (normalisation, doublons, relance), correction de chaque
  epreuve dont le rejeu des patrouilles, regle du passage unique (creation irreversible, reprise unique,
  delai d'une heure, session unique, reinitialisation), limitation des essais, fenetre d'ouverture, absence des
  reponses dans les enonces envoyes.
- 5 candidats `fictif` (commande de creation et de suppression ; exclus des envois et des statistiques).
- Recette sur l'environnement de dev : passage complet du test par Claude dans Chrome, captures montrees au DOA.
- Production : uniquement avec l'accord explicite du DOA. Pre-requis : `FORMATION_JWT_SECRET`, base dediee
  dans la sauvegarde, verification que `cockpit.lemans.org/test-pc-organisation` est joignable depuis Internet
  sans le portail.
- Documentation : `docs/claude/formation.md` + ligne dans `CLAUDE.md` (domaines, variables d'environnement).
- Livrables finaux : URL publique, marche a suivre de reimport, texte de l'e-mail d'invitation.

## 11. Laisse au constructeur

Decoupage des fichiers, noms internes, mise en page exacte de l'administration, graphiques (Chart.js deja
present), format interne du journal d'actions, details du fil d'envoi.

## 12. Decisions du DOA apres relecture (22/11)

- **Destinataires : tous les candidats actifs** (environ 555 apres doublons). Le fichier de proposition de
  selection (`Selection_PCOrg_decembre2026_PROPOSITION.xlsx`, score de dossier) n'est **pas** importe dans cette
  version (appel du concepteur : le DOA rapproche lui-meme a partir de l'export, qui garde l'identifiant Forms
  `forms_id` en premiere colonne pour la jointure).
- **Le relais SMTP envoie deja vers l'exterieur** : pas de second chemin d'envoi (Microsoft 365) dans le code.
  L'essai Gmail / Outlook.com reste fait avant le 26/11.
- **Conservation : suppression de toutes les donnees du module au 30/01/2027**, apres la selection. Mention RGPD
  en consequence. Mise en oeuvre (appel du concepteur) : commande explicite de purge de la campagne (candidats,
  tentatives, envois, essais, journal), date affichee dans l'administration avec un rappel a partir du 23/01 ;
  pas de TTL automatique (une suppression silencieuse en pleine selection serait pire qu'un oubli).
- **Ordre aleatoire** pour les epreuves a question (38 17, main courante, logique, mises en situation : ordre
  des questions et des choix). **Ordre fixe** pour la radio, les patrouilles et les badges (difficulte identique
  pour tous).
- **Temps** : pas de compte a rebours visible pour le 38 17, la main courante et les mises en situation ;
  plafond invisible de 10 min par epreuve (x coefficient) contre l'onglet laisse ouvert, aucune reponse ignoree
  pour lenteur en dessous. Les epreuves deja chronometrees gardent leur chrono (x 1,33 si amenagement). Prise de
  poste et ecrans de regles : sans limite, hors chronometre de l'epreuve (le debut serveur est pose au clic sur
  « Commencer » de l'epreuve).
- **Adresse de reponse (reply-to) et adresse de contact RGPD** : reglages de la campagne, saisis par le DOA dans
  l'administration ; l'envoi de la campagne et l'ouverture du test sont refuses tant qu'elles sont vides.
