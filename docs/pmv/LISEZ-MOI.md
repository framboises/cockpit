# Envoi direct aux remorques PMV depuis l'éditeur — cahier des charges

> **Mobilitrees** · dossier remis le 27/09/2026 · à lire en entier avant d'écrire une ligne.

---

## 1. En bref

L'éditeur `PMV_Bitmap_Editor.html` dessine des messages de **96 × 64 pixels** pour des remorques à
panneau à message variable, installées au bord de routes réelles (épreuves ACO Le Mans, chantiers).
Aujourd'hui, pour afficher un dessin, il faut passer par le logiciel du fabricant (Sigma Play).

**Il faut ajouter à l'éditeur un onglet « 📤 Envoi »** qui affiche le dessin courant sur une remorque,
en un clic, avec sauvegarde et restauration de l'affichage précédent.

**Tout le difficile est déjà fait et prouvé** :

| Déjà fait | Où | Preuve |
|---|---|---|
| Le protocole du contrôleur (JetFileII, Sigma 3000), à l'octet | `docs/SIGMA3000_PROTOCOLE.md` | reproduit à l'octet la bibliothèque du fabricant |
| Une implémentation de référence complète, en Python | `reference/jetfile.py`, `reference/envoyer.py` | **affichage réel validé le 27/09 sur deux remorques, ~2 s par image** |
| Le relais local qui permet à une page de parler TCP | `relais/relais-pmv.ps1` | essayé contre le jumeau |
| Un **jumeau de la remorque** pour tout essayer sans remorque | `banc-essai/faux_panneau.py` | reproduit les défauts mesurés sur la vraie |
| Les **octets attendus** pour quatre images d'essai | `vecteurs/` | produits par la référence validée |
| Un outil de **recette automatique** | `banc-essai/comparer_capture.py` | témoin : détecte une image différente |

**Ce qui reste à écrire : du JavaScript dans la page** — porter `reference/jetfile.py` +
la logique de dialogue de `reference/envoyer.py`, et l'interface décrite au § 5.

---

## 2. Contenu du dossier

```
LISEZ-MOI.md                     ce document
editeur/PMV_Bitmap_Editor.html   l'éditeur, v1.3.00 — LE fichier à modifier
docs/SIGMA3000_PROTOCOLE.md      le protocole, et tout ce qui a été mesuré sur les remorques
reference/jetfile.py             image → fichier-message → trames (fonctions pures, à porter en JS)
reference/envoyer.py             le dialogue complet (essais, octet final, sauvegarde…), en ligne de commande
relais/relais-pmv.ps1            le relais HTTP ↔ TCP (PowerShell 5.1, rien à installer)
relais/Lancer-relais-PMV.cmd     son lanceur (double-clic)
banc-essai/faux_panneau.py       le jumeau de la remorque (Python 3, sur 127.0.0.1:9520)
banc-essai/donnees-remorque/     ce que le jumeau « contient » (lu sur une vraie remorque, nettoyé)
banc-essai/comparer_capture.py   la recette : ce que le jumeau a reçu = ce qui a été validé ?
vecteurs/                        4 images d'essai et, pour chacune, les octets attendus
```

Outils nécessaires : un navigateur (Chrome ou Edge), **Python 3** avec **Pillow** (`pip install pillow`)
pour le banc d'essai, Windows pour le relais. **Aucune remorque n'est nécessaire** avant la recette finale.

---

## 3. Architecture

```
 PMV_Bitmap_Editor.html  ──fetch──►  relais-pmv.ps1  ──TCP 9520──►  remorque (routeur 4G → contrôleur)
 (ouverte en file://)     http://localhost:9521        (ou le jumeau sur 127.0.0.1:9520)
   · fabrique l'image, le fichier, les trames            · ne comprend rien au protocole :
   · vérifie les réponses, gère essais et sauvegardes      il transporte des octets
```

**La page garde toute l'intelligence.** Une page web ne pouvant pas ouvrir de connexion TCP, le relais
ne fait que transporter. Il est **déjà écrit** ; ne le modifier qu'en cas de nécessité démontrée.

### 3.1 API du relais (voir l'en-tête de `relais-pmv.ps1`)

| Requête | Effet | Réponse |
|---|---|---|
| `GET /etat` | le relais est-il là ? | `200 {"ok":true,"version":"1.0.0"}` |
| `POST /echange?hote=IP&port=9520&attente=ms` + corps binaire | envoie le corps, puis attend **une** trame de réponse complète | `200` + trame · `204` rien reçu dans le délai · `502` + texte d'erreur |
| `POST /echange?…` avec **corps vide** | n'envoie rien, **écoute seulement** | idem |
| `POST /fermer?hote=IP&port=9520` | ferme la connexion TCP | `204` |

Garde-fous du relais : écoute sur `localhost` seulement ; **refuse toute page qui n'est pas un fichier
local** (en-tête `Origin` autre que `null` → 403) ; n'accepte qu'une **adresse IPv4 numérique** et les
ports **9520 / 3001**. La connexion TCP est gardée ouverte entre deux requêtes pour une même remorque.

---

## 4. Le protocole — l'essentiel à porter

Tout est détaillé dans `docs/SIGMA3000_PROTOCOLE.md` ; la vérité exécutable est `reference/jetfile.py`.

### 4.1 Les fonctions à porter en JavaScript (pures, sans DOM)

| `reference/jetfile.py` | Rôle | Vérifiée contre |
|---|---|---|
| `bmp565(pixels)` | BMP **16 bits 5-6-5**, `BI_BITFIELDS`, lignes de bas en haut, 66 octets d'en-tête | `vecteurs/*.bmp565.bmp` |
| `panel_file_like_sigma_editor(bmp, 3)` | le fichier-message : `<SOH>Z00<STX>AA` + codes + bloc `RC` + BMP + `<EOT>` | `vecteurs/*.fichier-panneau.bin` |
| `frame(cmd, sub, args, data, seq, dst)` | une trame `55 A7` + somme | `vecteurs/*.trames-envoi.hex` |
| `check_reply(rep, seq, cmd, sub)` | validation d'une réponse `55 A8` | § 4.3 |
| `write_file_frames(chemin, contenu)` | commande **2/0x08**, paquets de 768 octets | `*.trames-envoi.hex` |
| `sequent_sys([('D','T','PMVED.Nmg')])` | la liste de lecture (44 octets) | `*.trames-envoi.hex` |
| `write_sys_frames('SEQUENT.SYS', …)` | commande **2/0x02** | `*.trames-envoi.hex` |
| (dans `envoyer.py`) `lire_fichier_systeme` | commande **1/0x02**, lecture par paquets | § 4.4 |

La source des pixels dans la page est la variable globale **`imgD`** (`ImageData` 96 × 64, RGBA), avec
les constantes **`W`** et **`H`**. ⚠️ L'export BMP existant (`buildBMPBlob`, 32 bits) **ne convient pas** :
le panneau veut du 16 bits 5-6-5.

### 4.2 🔴 Les règles de dialogue — chacune vient d'un incident réel du 27/09

| Règle | Pourquoi (mesuré sur remorque) |
|---|---|
| **Ajouter un octet `00` après CHAQUE trame** envoyée | Le contrôleur ne traite une trame qu'après avoir reçu **au moins un octet de plus**. Sans lui : aucune réponse ; avec : réponse en 0,04 s. |
| **Numéro de séquence unique sur toute la session** (1, 2, 3… sans jamais repartir à 1) | Un accusé **tardif** d'une lecture a été pris pour la réponse de la lecture suivante (même commande, même n°) : la sauvegarde contenait le mauvais fichier. |
| **Réponse qui ne correspond pas → l'ignorer et continuer d'écouter** (corps vide), sans renvoyer | Les accusés tardifs existent (4G). |
| Délais : **1 500 ms** au 1er essai, puis **10 000 ms**, 3 essais au total ; renvoyer la trame **identique** | Renvoyer un même paquet est sans effet de bord (vérifié). |
| Destination **`0x0101`**, port **9520**, paquets de **768** octets (`0x300`) | configuration des remorques |
| Message écrit en **`D:\T\PMVED.Nmg`** | `D:` = mémoire flash ; nom fixe, 8.3 |
| ⛔ **Ne JAMAIS émettre la commande 2/0x0C** | elle réécrit la configuration d'affichage ; effet non maîtrisé |
| Contrôle **somme** (`55 A7`) ; le mode CRC (`55 A3`) n'est **pas** utilisé | les remorques répondent en `55 A8` |

### 4.3 Valider une réponse (comme la bibliothèque du fabricant)

Réponse **valable** ⇔ commence par `55 A8`, **même** n° de séquence (octets 10-11), **même** commande et
sous-commande (octets 12-13), longueur complète, somme juste (octets 2-3 = somme des octets 4 → fin).
Puis octet 15 : `00` = succès ; `01` = lire le code d'état aux octets 16-17 (`90 00` = succès, autre =
refus) ; autre = refus. Toute réponse non valable = **à ignorer** (§ 4.2).

### 4.4 Lire un fichier système (1/0x02)

Arguments : nom sur 12 octets (11 caractères max, complété par `00`) + taille de paquet (u16) + n° de
paquet (u16, à partir de 1). Réponse : si 8 octets d'arguments, **taille totale = u32 aux octets 20-23** ;
sinon u16 aux octets 16-17. Données à partir de l'octet `16 + 4 × octet14`. Un **refus** au 1er paquet =
le fichier n'existe pas (renvoyer `null`) ; une **absence de réponse** = erreur réseau.

`CONFIG.SYS` : **largeur = u16 aux octets 2-3, hauteur = u16 aux octets 4-5** → doit valoir 96 × 64.
`SEQUENT.SYS` : commence par `SQ` ; nombre d'entrées = u16 aux octets 4-5 ; entrées de 36 octets à partir
de l'octet 8 ; **nom du fichier joué = 12 octets à l'offset 24 de l'entrée**.

### 4.5 Les trois opérations

**Tester la connexion** (lecture seule, rien n'est modifié) : lire `CONFIG.SYS` (dalle) puis
`SEQUENT.SYS` (ce qui est affiché). Afficher : joignable, dalle, fichier actuellement joué, durée.

**Envoyer** — exactement la séquence de `vecteurs/*.trames-envoi.hex` :
1. lire `CONFIG.SYS` → **arrêt si la dalle n'est pas 96 × 64** (rien n'a été modifié) ;
2. lire `SEQUENT.SYS` → **sauvegarde** (§ 6). Illisible ou ne commençant pas par `SQ` → **arrêt** ;
   l'utilisateur peut alors, par un second clic explicite, envoyer **sans** sauvegarde ;
3. écrire le fichier-message (2/0x08, 17 paquets pour une image) — barre de progression ;
4. écrire la liste de lecture (2/0x02) → **l'image s'affiche**.

**Remettre l'affichage d'avant** : écrire (2/0x02) la liste de lecture sauvegardée. Refuser toute
sauvegarde qui ne commence pas par `SQ`.

---

## 5. L'interface attendue — onglet « 📤 Envoi »

L'auteur veut **un outil pratique, avec une UX et une UI au top**. Suivre le style existant (thème
sombre, variables CSS `--bg`, `--panel`, `--ac`…, classes `.btn`, `.btn.pr`, `.lbl`, cartes du volet
Réseau). Le panneau latéral fait **210 px** de large.

1. **Nouvel onglet** `📤 Envoi` dans `#sb-tabs`, après `🌐 Réseau`. `switchPane` laisse le canevas de
   dessin visible pour tout onglet autre que Copier et Réseau : **le dessin reste sous les yeux**.
2. **Pastille du relais**, en haut : 🟢 « Relais prêt » / 🔴 « Relais absent » + une ligne d'aide
   (« double-cliquer sur `Lancer-relais-PMV.cmd` et laisser sa fenêtre ouverte »). Vérification
   automatique toutes les ~3 s tant que l'onglet est affiché : **la pastille passe au vert toute seule**.
3. **Remorque** : liste déroulante des **fiches du volet Réseau** (plaque mise en forme + localisation)
   + une entrée « ✎ Adresse IP… » qui ouvre un champ de saisie (IPv4 vérifiée). Pour une fiche, l'IP est
   résolue **par le module Réseau existant** (§ 7) et affichée (« 🟢 203.0.113.30 · résolue 14:32 », ou
   « 🔴 introuvable — remorque éteinte ? »). Mémoriser le dernier choix.
4. **Aperçu** « Ce qui va s'afficher » : le dessin courant, agrandi ×2, pixels nets, dans un cadre
   façon écran ; il **suit** le dessin en direct.
5. **UN SEUL bouton plein** : « 📤 Envoyer sur DW-330-HC » (plaque ou IP choisie). Grisé tant que le
   relais est absent ou la destination invalide, avec la raison visible. Sous le bouton, un
   avertissement discret : l'image remplace aussitôt l'affichage du panneau ; l'affichage actuel est
   sauvegardé d'abord. **Un avertissement, pas un verrou : pas de boîte de confirmation.**
6. **Déroulé en étapes cochées** pendant l'opération : Connexion → Dalle 96 × 64 vérifiée → Affichage
   actuel sauvegardé → Envoi de l'image (**barre de progression**) → Mise à l'affiche. Chaque étape :
   en attente / en cours (animée) / ✓ / ✕, avec un détail court (« affichait : temp.Nmg », « 12,1 Ko »).
7. **Résultat** en clair : « ✅ Affiché sur DW-330-HC en 2,1 s — vérifiez à l'œil » ; ou « ❌ » +
   la cause en français, **sans jargon** (« la remorque ne répond pas », « relais absent », « dalle
   64 × 32 : rien n'a été envoyé »…).
8. **Actions secondaires** (boutons non pleins) : « 🔌 Tester la connexion » (lecture seule) et
   « ↩️ Remettre l'affichage d'avant » — ce dernier grisé s'il n'y a pas de sauvegarde, et accompagné
   d'une ligne « Affichage d'avant sauvegardé le 27/09 14:32 : **temp.Nmg** ».
9. **Journal technique** replié (`<details>`) : trames émises/reçues en hexadécimal, horodatées, pour
   le diagnostic. Plafonné (quelques centaines de lignes).
10. Pendant une opération : tous les boutons grisés ; l'utilisateur ne peut pas lancer deux
    opérations en parallèle. Aucune opération ne doit pouvoir « rester bloquée » : délais bornés.
11. **Aide** : ajouter une rubrique « Envoi » au panneau d'aide (`HELP_SECTIONS`, bouton dans la barre
    `.hnav-btn`), sur le modèle de la rubrique « Réseau IP ».

---

## 6. Sauvegarde de l'affichage d'avant — la règle à ne pas rater

- Stockage : `localStorage`, clé **`pmv_envoi_sauv_v1`** = `{ <clé remorque>: {ts, hex, noms} }`.
  Clé remorque = **la plaque** si la destination vient d'une fiche, sinon **l'adresse IP**.
- 🔴 **On garde l'affichage d'AVANT notre PREMIER envoi.** Si la liste de lecture lue contient déjà
  `PMVED.Nmg` (notre propre fichier), **ne pas écraser** la sauvegarde existante : sinon, au deuxième
  envoi, la « sauvegarde » ne contiendrait plus que notre image et l'affichage d'origine serait perdu
  (c'est arrivé avec l'outil en ligne de commande, qui sauvegarde à chaque envoi).
- Si la liste lue ne contient pas `PMVED.Nmg` (quelqu'un a renvoyé un message par Sigma entre-temps),
  la nouvelle lecture **remplace** la sauvegarde.
- Dernière destination choisie : clé `pmv_envoi_dest_v1`.

---

## 7. Contraintes du projet — non négociables

1. **Fichier unique autoporteur** : pas de framework, pas de build, pas de dépendance, **aucune ressource
   externe** (tout inline). Les seuls appels réseau nouveaux vont au relais `http://localhost:9521`.
2. **Français** pour toute l'interface.
3. **96 × 64** : la dalle ne change pas.
4. **Volet Réseau : comportement inchangé.** Il résout `info<PLAQUE>.ddns.net` en DNS-over-HTTPS ;
   c'est une décision assumée de l'auteur, **ne pas la modifier**. Pour réutiliser le registre et la
   résolution, une seule ligne est admise dans son module, avant son `// Init` :
   `window.pmvNet={ fiches:loadF, resolve, fmtPlate };` — et rien d'autre dans ce module.
5. **Ne rien casser** : outils, raccourcis, import/export BMP, simulateur, volets existants.
6. **Version** : le badge de l'en-tête passe de `v1.3.00` à **`v1.4.00`** (seul endroit où figure la version).
7. Fins de ligne **LF**. Le fichier fait ~140 Ko : vérifier sa taille après chaque enregistrement.
8. Les fonctions de protocole portées doivent être **regroupées et pures**, pour être vérifiables
   hors navigateur contre les vecteurs.

---

## 8. Recette — dans cet ordre, et rien sur une remorque avant la fin de B

### A. Les octets (hors navigateur ou dans la console)
Pour chacune des 4 images de `vecteurs/` : `bmp565` = `*.bmp565.bmp` et fichier-message =
`*.fichier-panneau.bin`, **à l'octet près** ; la séquence de trames d'un envoi = `*.trames-envoi.hex`
(octet `00` final compris). `degrade.png` teste l'arrondi 5-6-5 ; `mire-couleurs.png` teste le sens
de l'image (le damier jaune est **en haut à gauche**).

### B. La page contre le jumeau
```
python banc-essai/faux_panneau.py        (fenêtre 1 — le jumeau, 127.0.0.1:9520)
relais/Lancer-relais-PMV.cmd             (fenêtre 2 — le relais)
ouvrir editeur/PMV_Bitmap_Editor.html par double-clic (file://), onglet Envoi, adresse IP 127.0.0.1
```
| # | Scénario | Attendu |
|---|---|---|
| 1 | relais fermé | pastille rouge + aide ; boutons grisés, raison visible |
| 2 | lancer le relais sans recharger la page | pastille verte en ≤ 3 s |
| 3 | Tester la connexion | dalle 96 × 64, « affiche : temp.Nmg » ; **aucune trame 2/0x…** dans le journal du jumeau |
| 4 | importer `vecteurs/mire-couleurs.png`, Envoyer | ✅ en < 3 s ; puis `python banc-essai/comparer_capture.py vecteurs/mire-couleurs.png` → **« conforme »** |
| 5 | idem avec `degrade.png`, `noir.png`, `blanc.png` | « conforme » à chaque fois |
| 6 | après 2 envois successifs | la sauvegarde affichée est toujours **temp.Nmg** (pas PMVED.Nmg) |
| 7 | Remettre l'affichage d'avant | le journal du jumeau montre `SEQUENT.SYS enregistré` nommant **temp.Nmg** |
| 8 | jumeau muet : écrire `none` dans `banc-essai/banc/reply_mode.txt`, Envoyer | 3 essais, puis « la remorque ne répond pas » ; interface rendue ; supprimer le fichier ensuite |
| 9 | arrêter le jumeau pendant un envoi | erreur claire, rien de bloqué |
| 10 | IP invalide saisie | bouton grisé, message |
| 11 | dessiner pendant que l'onglet est ouvert | l'aperçu suit |
| 12 | ouvrir la page par `http://…` au lieu de `file://` | le relais refuse (403) → message clair |

Le jumeau journalise tout dans `banc-essai/banc/faux_panneau.log` et `frames.bin`.

### C. Sur une vraie remorque — avec l'auteur, jamais seul
Remorque **hors service**, quelqu'un **qui voit le panneau**. Dans l'ordre : Tester la connexion →
Envoyer `noir.png` → `blanc.png` → un dessin → Remettre l'affichage d'avant. Délai attendu : **~2 s par
image** (Sigma Play met 4 à 6 s). En cas de doute, `reference/envoyer.py` fait la même chose en ligne de
commande et sert de comparaison : `python reference/envoyer.py <IP> --test`.

---

## 9. Hors périmètre

Mode CRC (`55 A3`) ; messages texte (police du panneau) ; plusieurs images ou programmation horaire ;
animation ; le format `.rgbms5` ; la résolution DNS (déjà faite par le volet Réseau) ; toute
modification du relais non justifiée par un essai.

## 10. Sécurité — à savoir

Le contrôleur n'exige **aucune authentification** : quiconque joint l'adresse d'une remorque sur le
port 9520 peut changer son affichage. D'où les garde-fous du relais (§ 3.1). **Ne jamais exposer le
relais sur le réseau** (il n'écoute que sur `localhost` : ne pas changer ça), ne jamais lui ajouter de
page ou d'origine autorisée, ne jamais publier ce dossier.

## 11. Livrable attendu

- `PMV_Bitmap_Editor.html` en **v1.4.00**, seul fichier modifié (le relais seulement si nécessaire, motivé) ;
- le **résultat de la recette A et B** (sorties de `comparer_capture.py`, liste des 12 scénarios cochés) ;
- une note courte : ce qui a été porté, où, et tout écart assumé par rapport à ce document.
