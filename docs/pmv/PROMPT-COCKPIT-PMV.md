# Prompt : nouvelle page PMV dans COCKPIT

> A coller tel quel dans Claude Code, lancé à la racine du repo `E:\TITAN\production\cockpit`.
> Avant : copier le dossier `dossier-developpeur-envoi-PMV/` sur le serveur, à l'endroit indiqué
> ci-dessous (ou corriger le chemin dans le prompt).

---

## Mission

Tu vas ajouter à COCKPIT une nouvelle fonctionnalité **PMV** : piloter depuis Cockpit les remorques
à panneau à message variable (PMV) que nous louons, pour y afficher nos messages (infos commerciales,
orientation, sécurité) sur le circuit et ses abords. Aujourd'hui, il faut passer par le logiciel du
fabricant (Sigma Play, vieux logiciel Windows), et nous voulons nous en passer.

**Ne code rien tout de suite.** Déroule les étapes dans l'ordre :
1. lis tout (le dossier PMV + les fichiers Cockpit cités) ;
2. pose-moi les questions ouvertes (liste plus bas) avec l'outil de questions, en proposant
   chaque fois une option recommandée ;
3. rédige un plan par phases (mode plan), que je valide ;
4. développe phase par phase, et fais valider chaque phase contre le faux panneau avant de passer
   à la suivante.

Le contexte général de Cockpit (stack, conventions, règles strictes) est dans `CLAUDE.md`. Relis-le
et respecte-le : Blueprint dédié, page Jinja autonome + `_sidebar.html`, JS en IIFE, CSRF, toasts,
format d'erreur `{"ok": false, "error": "<code>"}`, jamais de `abort(404)`, jamais de guillemets
typographiques dans le code, échappement HTML, dates naïves heure de Paris, section CLAUDE.md à la fin.
**Modèle le plus proche : `cameras.py`** (équipements distants, `_check_admin`, `_ensure_db`, `_pub_safe`).

---

## 1. Le matériel et ce qui est déjà prouvé

Tout le difficile est fait et validé sur de vraies remorques le 27/09/2026. Le dossier de référence
est ici : `<CHEMIN>\dossier-developpeur-envoi-PMV\` (**à me demander si tu ne le trouves pas**).
Lis-le **en entier** avant d'écrire une ligne, dans cet ordre :

| Fichier | Ce que c'est |
|---|---|
| `LISEZ-MOI.md` | le cahier des charges d'origine (prévu pour l'éditeur autonome) : règles de dialogue, sauvegarde, UX, recette |
| `docs/SIGMA3000_PROTOCOLE.md` | le protocole à l'octet, et **tout ce qui a été mesuré sur les remorques** (§ 10) |
| `reference/jetfile.py` | fonctions pures : image -> BMP 565 -> fichier-message -> trames. **La vérité exécutable** |
| `reference/envoyer.py` | le dialogue complet en ligne de commande (classe `Panneau`), **validé sur remorque** |
| `banc-essai/faux_panneau.py` | le **jumeau** d'une remorque (TCP 127.0.0.1:9520), journalise tout |
| `banc-essai/comparer_capture.py` | la recette : ce que le jumeau a reçu = ce qui a été validé ? |
| `banc-essai/donnees-remorque/` | `CONFIG.SYS`, `DEFAULT.SYS`, `SEQUENT.SYS` lus sur une vraie remorque |
| `vecteurs/` | 4 images d'essai et, pour chacune, les octets attendus (BMP 565, fichier-panneau, trames) |
| `editeur/PMV_Bitmap_Editor.html` | l'éditeur de dessin 96 x 64 existant (v1.3.00, fichier unique, ~140 Ko) |
| `relais/relais-pmv.ps1` | relais HTTP -> TCP pour le navigateur. **Inutile dans Cockpit** (voir § 2) |

Résumé du matériel :
- contrôleur **Sigma 3000** (fabricant QS / Qiangsheng), protocole **JetFileII** ;
- chaque remorque est derrière un **routeur 4G** : IP publique résolue par
  **`info<PLAQUE>.ddns.net`** (plaque sans tirets, ex. `infoDW330HC.ddns.net`), **TCP port 9520** ;
- adresse JetFileII destination **`0x0101`**, contrôle par somme (`55 A7`, réponses `55 A8`) ;
- dalle **96 x 64 pixels couleur**, image en **BMP 16 bits 5-6-5** encapsulée dans un fichier-message
  écrit en **`D:\T\PMVED.Nmg`** (mémoire flash), puis la liste de lecture **`SEQUENT.SYS`** qui le
  fait jouer ;
- performance mesurée : **~2 s par image** (Sigma Play : 4 à 6 s).

---

## 2. Le changement d'architecture par rapport au dossier

Le dossier d'origine visait une page HTML autonome : le protocole était en JavaScript et un relais
PowerShell faisait le TCP. **Dans Cockpit, c'est le serveur Flask qui parle aux remorques** :

```
navigateur (page /pmv)  --fetch + CSRF-->  Flask (blueprint pmv)  --TCP 9520-->  routeur 4G -> contrôleur
   UI, éditeur, aperçu                       protocole Python, verrous,          (ou le jumeau
                                             sauvegardes, audit, Mongo            127.0.0.1:9520)
```

Conséquences :
- **pas de relais, pas de portage JS du protocole** : on reprend `reference/jetfile.py` et
  `reference/envoyer.py` en Python, côté serveur ;
- les fonctions de protocole restent **pures, regroupées dans un module à part** (ex. `pmv_protocole.py`),
  sans Flask ni Mongo, pour être testées contre `vecteurs/` ;
- le dialogue réseau (classe type `Panneau`) est dans un second module ou une section distincte ;
- la **résolution DNS** `info<PLAQUE>.ddns.net` se fait côté serveur (`socket.getaddrinfo`, pas le DoH
  de l'éditeur). Attention au cache DNS de Windows Server : l'IP 4G change. A vérifier (question 9) ;
- les sauvegardes et le registre des remorques passent du `localStorage` à **MongoDB** : ils deviennent
  partagés entre opérateurs, c'est voulu ;
- l'authentification Cockpit (JWT, OTP, rôles) protège l'accès. L'ancien garde-fou du relais
  (« Origin null uniquement ») disparaît. Les autres restent **côté serveur** (§ 4).

---

## 3. Les règles de dialogue — NON NÉGOCIABLES

Chacune vient d'un incident réel sur remorque (détails : `LISEZ-MOI.md` § 4.2 et `SIGMA3000_PROTOCOLE.md` § 10).
Recopie-les en commentaire en tête du module réseau.

1. **Ajouter un octet `00` après CHAQUE trame envoyée.** Sans lui, le contrôleur ne répond pas.
2. **Numéro de séquence unique sur toute la session TCP** (1, 2, 3..., jamais de retour à 1).
   Sinon, un accusé tardif est pris pour la réponse suivante. C'est ce qui a détruit la liste de
   lecture d'une remorque le 27/09.
3. **Réponse qui ne correspond pas** (séquence, commande, somme) : **l'ignorer et continuer d'écouter**,
   sans renvoyer.
4. Délais : **1 500 ms** au 1er essai, puis **10 000 ms**, **3 essais** au total, trame renvoyée
   **identique**. Durée d'une opération toujours bornée.
5. Validation d'une réponse exactement comme `check_reply` / `Panneau.commande` (§ 4.3 du LISEZ-MOI).
6. ⛔ **Liste blanche des commandes, appliquée dans le code** : seules `1/0x02` (lire un fichier
   système), `2/0x08` (écrire un fichier) et `2/0x02` (écrire un fichier système) peuvent partir.
   **Jamais `2/0x0C`** (réécrit la configuration d'affichage, effet inconnu). Toute autre commande lève
   une exception avant l'envoi. La liste s'élargira seulement après un essai validé sur remorque.
7. Avant tout envoi : lire `CONFIG.SYS` et **arrêter si la dalle n'est pas 96 x 64** (rien n'a été modifié).
8. **Sauvegarde de l'affichage d'avant** (`LISEZ-MOI.md` § 6), par remorque :
   - lire `SEQUENT.SYS` avant d'écrire. Illisible ou ne commençant pas par `SQ` : arrêt. Envoi
     « sans sauvegarde » seulement sur action explicite ;
   - si la liste lue contient déjà `PMVED.Nmg` (notre fichier), **ne pas écraser** la sauvegarde
     existante : on garde l'affichage d'avant notre PREMIER envoi ;
   - sinon (quelqu'un est repassé par Sigma entre-temps), la nouvelle lecture **remplace** la sauvegarde ;
   - « Remettre l'affichage d'avant » réécrit la sauvegarde (`2/0x02`) et refuse toute sauvegarde
     qui ne commence pas par `SQ`.
9. **Une seule opération à la fois par remorque** : Waitress est multi-thread. Mets un verrou par
   remorque. Si c'est occupé, réponds `{"ok": false, "error": "busy"}`.
10. La mémoire `D:` est une **flash** qui s'use : pas de renvoi en boucle, pas de programmation à haute
    fréquence. Si le même message est déjà affiché (d'après l'historique), le signaler avant de le renvoyer.

---

## 4. Sécurité

- Le contrôleur **n'a aucune authentification** : qui joint l'IP sur le port 9520 change l'affichage.
  Cockpit devient donc le point de contrôle.
- Le serveur ne se connecte **qu'aux remorques enregistrées** (IP résolue depuis leur plaque, ou IP fixe
  saisie par un admin), sur le port 9520. Jamais une IP libre venant de la requête sans contrôle de rôle.
  Autoriser `127.0.0.1` (le jumeau) seulement en dev (`CODING=true`) ou pour un admin (à me confirmer).
- CSRF actif sur toutes les actions. Rôles selon mes réponses aux questions.
- **Audit** append-only `pmv_audit` (qui, quand, IP client, remorque, action, résultat, durée) avec TTL,
  comme les autres modules. Il sert au RETEX, et à savoir qui a affiché quoi au bord d'une route.
- Le dossier PMV et les IP réelles ne sont jamais publiés, ni dans le front ni dans un repo public.

---

## 5. Les fonctionnalités attendues

Je veux un **vrai outil de poste de commandement**, pratique, avec une UX au niveau du reste de Cockpit.
Propose-moi un découpage en phases. Ma proposition :

**Phase 1 — le socle (indispensable)**
- Module protocole pur + module dialogue + tests contre les vecteurs.
- **Registre des remorques** (CRUD) : plaque (format `AA-000-AA`), nom court, localisation texte,
  position GPS (saisie ou clic sur la carte), description, IP fixe optionnelle, actif ou non.
  Import possible de l'export JSON des fiches du volet Réseau de l'éditeur (lire `loadF` dans l'éditeur).
- Pour chaque remorque : **résolution IP** (« 🟢 203.0.113.30 · résolue 14:32 » ou
  « 🔴 introuvable, remorque éteinte ? »), **Tester la connexion** en lecture seule (dalle, fichier
  actuellement joué), **Envoyer une image**, **Remettre l'affichage d'avant**.
- **Déroulé en étapes cochées** pendant l'opération (Connexion -> Dalle 96 x 64 vérifiée -> Affichage
  actuel sauvegardé -> Envoi de l'image avec barre de progression -> Mise à l'affiche). Chaque étape :
  en attente / en cours / ✓ / ✕, avec un détail court. Comme une opération peut durer plusieurs dizaines
  de secondes en 4G, l'opération tourne dans un **thread serveur** (job). Le front suit
  `GET /api/pmv/jobs/<id>`. Aucune requête HTTP ne doit rester pendante pendant 30 s.
- **Résultat en français, sans jargon** : « ✅ Affiché sur DW-330-HC en 2,1 s. Vérifiez à l'œil. »,
  « ❌ La remorque ne répond pas », « ❌ Dalle 64 x 32 : rien n'a été envoyé »...
- **Journal technique** replié : trames émises/reçues en hexadécimal, horodatées, plafonnées.
- **Historique des envois** (collection dédiée) : qui, quand, quelle remorque, quel message
  (miniature), résultat, durée.

**Phase 2 — les messages**
- **Éditeur 96 x 64** : reprendre les outils de `PMV_Bitmap_Editor.html` (dessin, texte, formes,
  import/export BMP et PNG, simulateur...). Lis-le et propose l'intégration (question 3).
- **Aperçu** « Ce qui va s'afficher » : x2 ou plus, pixels nets, dans un cadre façon écran.
- **Import d'image** (PNG/BMP/JPG) : redimensionnée ou refusée si elle n'est pas en 96 x 64, à me
  proposer. Conversion 5-6-5 **côté serveur** avec la fonction de référence, pour garder les mêmes octets.
- **Bibliothèque de messages** : nom, image 96 x 64, catégorie ou étiquettes, événement/année
  (`window.selectedEvent` / `selectedYear`), auteur, date. Envoi en un clic depuis la bibliothèque.

**Phase 3 — le poste de commandement**
- **Carte Leaflet** des remorques (comme les autres cartes de Cockpit), avec leur état : joignable ou
  non, message affiché (miniature), dernier envoi.
- **Groupes de remorques** (ex. « Parkings Nord ») et **envoi groupé** : une remorque après l'autre ou
  en parallèle limité, avec le déroulé par remorque. Confirmation via `showConfirmToast` pour un
  groupe (à me confirmer).
- **État affiché** : on ne peut lire que le **nom du fichier joué** (`SEQUENT.SYS`), pas les pixels.
  Si c'est `PMVED.Nmg`, afficher « affiche probablement : <dernier message envoyé par Cockpit> ».
  Sinon : « message Sigma : <nom> ». Toujours le formuler comme une déduction.
- **Programmation horaire** : envoi planifié d'un message à une heure donnée (regarde s'il existe déjà
  un planificateur dans Cockpit). Respecter la règle flash (§ 3.10).

**Hors périmètre** tant qu'un essai sur remorque ne l'a pas validé (à me signaler, pas à coder) :
plusieurs images en alternance dans `SEQUENT.SYS`, messages texte avec la police du panneau,
animations/effets, luminosité, extinction/allumage (`4/0x03`, `4/0x04`, non mesurés), mode CRC,
mot de passe du contrôleur (`czLogin`, non exploré).

---

## 6. Réponses déjà connues (ne pas me les reposer)

- **Protocole** : JetFileII propriétaire (QS / Sigma 3000), **TCP brut port 9520**, doc complète à l'octet
  + implémentation de référence validée + trames d'exemple (`vecteurs/`) + jumeau. Pas de REST, pas de
  DIASER, NTCIP ni SNMP.
- **Capacités** : bitmap **96 x 64 couleur** (16 bits 5-6-5), une image fixe par envoi. Le texte se
  dessine dans l'image (l'éditeur le fait). Lignes/caractères natifs, pictogrammes natifs, alternance,
  luminosité : non utilisés ou non mesurés.
- **Accès réseau** : direct, en TCP, vers l'IP publique du routeur 4G de chaque remorque (DNS dynamique
  `info<PLAQUE>.ddns.net`). Aucun cloud tiers.
- **Temps** : ~2 s par image quand le réseau va bien. Jusqu'à plusieurs dizaines de secondes si la 4G est
  mauvaise (réémissions).

---

## 7. Questions à me poser (avec une option recommandée pour chacune)

1. **Rôles** : qui peut envoyer (manager ? admin ?), qui peut gérer le registre, la bibliothèque, les
   groupes ? Qui peut « envoyer sans sauvegarde » ?
2. **Réseau du serveur** : le Windows Server de prod peut-il sortir en TCP 9520 vers Internet (pare-feu,
   proxy) ? Propose-moi un test en lecture seule pour le vérifier.
3. **Éditeur** : (a) porter l'éditeur dans la page Cockpit (charte Cockpit, plus long), (b) l'embarquer
   tel quel (iframe dans `static/`, transmission de l'image par `postMessage`), ou (c) phase 1 avec
   import d'image seulement, éditeur ensuite. Donne ta recommandation après avoir lu le fichier.
4. **Emplacement du dossier de référence** dans le repo (ex. `docs/pmv/` ou `tools/pmv/`), et s'il doit
   être versionné (il contient la procédure pour piloter des panneaux non protégés).
5. **Affichage ailleurs** : widget sur l'accueil ? mur TV ? montre Garmin (état seulement, jamais
   d'envoi depuis la montre, à me confirmer) ?
6. **Envoi groupé** : confirmation obligatoire ? séquentiel ou parallèle ?
7. **Historique et audit** : durée de conservation (TTL) ? rattachement à l'événement/année ?
8. **Jumeau en production** : autoriser un admin à viser `127.0.0.1` en prod pour les démonstrations, ou
   seulement en dev ?
9. **DNS** : utiliser le DNS du serveur, ou interroger un résolveur public pour éviter le cache
   (l'éditeur utilisait Google/Cloudflare DoH) ?
10. Tout autre point que tu identifies en lisant le code.

---

## 8. Recette — obligatoire, dans cet ordre

**A. Les octets** : tests `pytest` dans `tests/pmv/` qui vérifient, pour les 4 images de `vecteurs/`,
`bmp565` = `*.bmp565.bmp`, fichier-message = `*.fichier-panneau.bin`, et la séquence de trames d'un
envoi = `*.trames-envoi.hex` (octet `00` final compris). `degrade.png` teste l'arrondi, `mire-couleurs.png`
le sens de l'image (damier jaune **en haut à gauche**). Ajoute un test « témoin » : un octet modifié
doit être détecté. Ajoute aussi un test qui vérifie que `2/0x0C` est refusé par la liste blanche.

**B. Cockpit contre le jumeau** (`python banc-essai/faux_panneau.py`, remorque de test en `127.0.0.1`,
`CODING=true`) :

| # | Scénario | Attendu |
|---|---|---|
| 1 | Tester la connexion | dalle 96 x 64, « affiche : temp.Nmg » ; **aucune trame `2/0x..`** dans `faux_panneau.log` |
| 2 | Envoyer `mire-couleurs.png`, puis `degrade`, `noir`, `blanc` | ✅ à chaque fois ; `python banc-essai/comparer_capture.py vecteurs/<image>.png` -> **conforme** |
| 3 | Deux envois successifs | la sauvegarde reste **temp.Nmg** (pas PMVED.Nmg) |
| 4 | Remettre l'affichage d'avant | le journal du jumeau montre `SEQUENT.SYS enregistré` nommant **temp.Nmg** |
| 5 | Jumeau muet (`none` dans `banc-essai/banc/reply_mode.txt`) | 3 essais, puis « la remorque ne répond pas » ; interface rendue ; supprimer le fichier ensuite |
| 6 | Arrêter le jumeau pendant un envoi | erreur claire, verrou libéré, rien de bloqué |
| 7 | Deux envois simultanés sur la même remorque (deux onglets) | le second reçoit `busy` |
| 8 | Utilisateur sans le rôle requis / sans CSRF | refusé, format d'erreur standard |
| 9 | Plaque introuvable en DNS | message clair, bouton d'envoi grisé avec la raison |
| 10 | Envoi groupé sur 2 remorques de test (jumeau + remorque éteinte) | une ✅, une ❌, les deux au journal |

Livre les sorties de `pytest` et de `comparer_capture.py`, et la liste des scénarios cochés.

**C. Sur une vraie remorque : jamais seul.** Seulement avec moi, sur une remorque **hors service**,
avec quelqu'un qui voit le panneau. Tester la connexion -> `noir` -> `blanc` -> un dessin -> Remettre
l'affichage d'avant. En cas de doute, comparer avec `python reference/envoyer.py <IP> --test`.
**Tu ne lances jamais toi-même un envoi vers une IP qui n'est pas 127.0.0.1.**

---

## 9. Livrables

- Blueprint `pmv` (page `/pmv` + API `/api/pmv/...`) enregistré dans `app.py`, lien dans `_sidebar.html`.
- Modules protocole (pur) et dialogue, `templates/pmv.html`, `static/js/pmv.js` (+ CSS si besoin).
- Collections préfixées `pmv_` (ex. `pmv_panneaux`, `pmv_messages`, `pmv_groupes`, `pmv_sauvegardes`,
  `pmv_envois`, `pmv_audit`), index créés à la volée (`_ensure_db`).
- Tests `tests/pmv/` et résultats de recette A et B.
- Section **PMV** dans `CLAUDE.md` : architecture, routes, collections, règles de dialogue (§ 3), pièges
  (accusés tardifs, octet `00`, flash, `2/0x0C`), procédure de recette.
- Une note courte : ce qui a été repris du dossier, où, et tout écart assumé.
