> ℹ️ Copie transmise au développeur : les adresses publiques des remorques sont remplacées par
> des adresses d'exemple (203.0.113.x). Le § 9 (étalon par la DLL du fabricant) suppose des binaires
> qui ne sont PAS fournis ; il n'est pas nécessaire pour réaliser l'intégration.

> Chemins : ce document cite ceux du projet d'origine. Dans ce dossier : `tools/jetfile.py` et
> `tools/envoyer.py` → `reference/` ; `tools/relais-pmv.ps1` → `relais/` ; `tools/faux_panneau.py` →
> `banc-essai/`. `tools/etalon_dll.py` et `tools/verif_etalon.py` ne sont pas fournis (ils exigent
> les binaires du fabricant).

# Parler directement aux remorques — ce que Sigma 3000 fait sur le fil

> Établi le **27/09/2026** en désossant le dossier `Sigma3000/` (le logiciel du fabricant du
> contrôleur des remorques). **Tout ce qui est marqué ✅ a été MESURÉ** : la bibliothèque du
> fabricant (`czJetFileII.dll`) a été exécutée contre un **jumeau de la remorque** tournant sur
> `127.0.0.1`, et chaque octet qu'elle a émis a été capturé. **Aucune trame n'a été envoyée à une
> vraie remorque.**
>
> La preuve se rejoue : `tools/faux_panneau.py`, `tools/etalon_dll.py`, `tools/verif_etalon.py` (§ 9).

---

## 1. En une phrase

Pour afficher un dessin de l'éditeur sur une remorque, il faut **trois briques** — et les trois
sont désormais connues à l'octet près :

1. **fabriquer le fichier-message** que lit le panneau (le fameux `.Nmg`) à partir du bitmap
   96 × 64 — § 5 ;
2. **l'envoyer** par paquets dans le système de fichiers du panneau (`D:\T\xxx.Nmg`) — § 4 ;
3. **écrire la liste de lecture** `SEQUENT.SYS` qui le fait jouer — § 6.

L'implémentation de référence est **`tools/jetfile.py`** (Python, sans la DLL), **identique octet
pour octet** à ce qu'émet la DLL du fabricant. C'est ce code qu'il faudra porter dans la page.

⚠️ **Un obstacle, qui n'est pas technique mais d'architecture : une page web ne peut pas ouvrir de
connexion TCP ou UDP.** Il faut un relais — § 8.

---

## 2. La chaîne réelle, telle que la configuration la décrit

| Maillon | Valeur relevée | Source |
|---|---|---|
| Logiciel utilisé | **Sigma Play** (Delphi) + Sigma Editor ; fabricant « QS » (Qiangsheng), protocole **JetFileII** | `Software Version Table(NEW).pdf`, chaînes des exécutables |
| Transport | **Ethernet, TCP, port 9520** | `LedSet.ini`, `NewLed.ini`, `SysSet.ini` (`EthernetSel=1`, `Port=9520`) |
| Adresse jointe | IP **publique** de la remorque (routeur 4G), celle que résout `info<PLAQUE>.ddns.net` | `NewLed.ini` `[Connect] IP=203.0.113.10` |
| Contrôleur derrière le routeur | `192.168.8.50`, masque `255.255.255.240`, passerelle `192.168.8.54` | réponse du panneau du **27/09 à 09:59** (`RecvData.tmps`) |
| Adresse JetFileII | **groupe 01, unité 01** (`0x0101`) — `0000` = diffusion | `SysSet.ini` `DestAddr=257` |
| Dalle | **96 × 64** | en-tête des `.Nmg`, fiche système |

🔴 **Sigma Play n'utilise PAS la DLL pour parler au panneau** : il a son propre code réseau
(`wsock32` : `connect`/`send`, pas de `sendto` ⇒ TCP) et n'appelle `czJetFileII.dll` que pour
la calibration des modules. **Les deux sont du même fabricant et parlent le même protocole**, mais
l'étalon mesuré ici est la DLL, pas Sigma Play — voir § 10.

---

## 3. La trame ✅

Toute commande est une trame binaire, **petit-boutiste** :

| Octets | Champ | Valeur |
|---|---|---|
| 0-1 | synchro | `55 A7` (requête, somme simple) — `55 A3` si contrôle CRC |
| 2-3 | somme | **somme des octets 4 → fin**, sur 16 bits |
| 4-5 | longueur des données | |
| 6-7 | adresse source | `0000` |
| 8-9 | adresse destination | `0101` (groupe 1, unité 1) ou `0000` |
| 10-11 | numéro de séquence | repart à 1 à chaque opération, +1 par paquet |
| 12 | commande | |
| 13 | sous-commande | |
| 14 | longueur des arguments, **en mots de 4 octets** | |
| 15 | drapeau | `00` |
| 16… | arguments (complétés à 4 octets), puis données | |

**Réponse attendue** (validée par la DLL) : synchro **`55 A8`** (ou `55 A4`), **même séquence**,
**même commande/sous-commande**, somme juste, et octet 15 = **`00` succès**. Si l'octet 15 vaut
`01`, les octets 16-17 portent un **code d'état** : `90 00` = succès, autre = erreur. Pas de
réponse valable ⇒ **3 réémissions**, délai **3 000 ms** chacune.

Paquets : **768 octets de données** (`0x300`) ; la documentation publique conseille < 1 024.

---

## 4. Les commandes utiles ✅

| Cmd | Rôle | Arguments | Mesuré |
|---|---|---|---|
| **1/0x10** | lire la **fiche système** du panneau | aucun | ✅ |
| **1/0x02** | **lire un fichier** système (ex. `CONFIG.SYS`) | nom (12) + taille paquet (u16) + n° paquet (u16) | ✅ |
| **2/0x08** | **écrire un fichier** (variante retenue par la DLL pour CE panneau) | taille totale (u32) + taille paquet (u16) + nb paquets (u16) + n° paquet (**u16**) + chemin `D:\T\NOM.Nmg\0` | ✅ |
| 2/0x0D | écrire un fichier (autre variante) | idem mais n° paquet en **u32** | ✅ |
| **2/0x02** | **écrire un fichier système** (ex. `SEQUENT.SYS`) | nom (12) + taille (u32) + taille paquet (u16) + nb paquets (u16) + n° paquet (u32) | ✅ |
| ⛔ 2/0x0C | réécrire le « style d'affichage par défaut » | 52 octets tirés de la fiche système **modifiée** | ✅ vu, **à ne pas reproduire** |
| 2/0x09 | message d'urgence temporaire | selon l'implémentation publique johnoneil/LEDSign | ❌ non mesuré |
| 4/0x03, 4/0x04 | éteindre / allumer le panneau | idem | ❌ non mesuré |

La DLL compte **~170 couples commande/sous-commande** (familles 1 lecture, 2 écriture, 3 test,
4 contrôle, 7 fichiers, 0x34/0x35/0x60/0x7a matériel…) ; seuls ceux du tableau servent ici.

⛔ **2/0x0C** : avant d'écrire le message, `czShowFiles` relit la fiche système du panneau,
**change l'octet 2 (`0x60` = 96 → `0x43`)** et quelques autres, puis la renvoie. On ne sait pas
ce que ce champ pilote. **Elle n'est pas nécessaire à l'affichage ; ne jamais l'émettre.**

### Séquence minimale pour afficher un message

```
2/0x08 × N   D:\T\MSG.Nmg        (le message, par paquets de 768 octets)
2/0x02 × 1   SEQUENT.SYS         (la liste de lecture qui le nomme)
```

C'est ce que fait `czShowFiles`, **moins** les lectures préalables (1/0x10, 1/0x02 — utiles pour
vérifier qu'on parle bien à une dalle 96 × 64) et **moins** 2/0x0C.

Lecteurs du panneau : **`D:` = mémoire flash** (survit à l'extinction, s'use), **`E:` = RAM**
(effacée à l'extinction). Sigma Play et la DLL écrivent en `D:\T\`.

---

## 5. Le fichier-message — le « maillon manquant » du CLAUDE.md, enfin connu ✅

Le panneau lit un **fichier texte JetFileII** qui **contient l'image** :

```
en-tête   codes de mise en page   <EOT>   bloc RC   BMP 96×64 16 bits 5-6-5
```

- **Les codes** sont ceux du protocole public (pause, effets d'entrée/sortie, alignement…) ;
  `14 40 30` = « insérer ici l'image `0` du bloc RC ».
- **Le bloc RC** : `52 43 01 00 01 30 00 00` + taille du BMP (u32) + décalage du BMP depuis `RC`
  (u32, `0x28`) + bourrage jusqu'au BMP.
- **Le BMP** : 16 bits, `BI_BITFIELDS`, masques `F800 / 07E0 / 001F`, lignes de bas en haut —
  exactement ce que produit Sigma Editor. **Ce n'est PAS le BMP 32 bits qu'exporte l'éditeur
  aujourd'hui** (`buildBMPBlob`) : il faudra un second encodeur.

Deux en-têtes coexistent, tous deux produits par le fabricant, tous deux **reproduits à l'octet
près** par `tools/jetfile.py` :

| Forme | Produite par | Fonction de référence | Déjà affichée sur la remorque ? |
|---|---|---|---|
| `<SOH>Z00<STX>AA` + codes Sigma Editor | Sigma Editor → Sigma Play (le `.Nmg` « NG » est converti à l'envoi : corps à partir de l'octet 18, `<EOT>` final) | `panel_file_like_sigma_editor()` | **✅ oui — c'est ce qui part chaque jour** |
| `QZ00SAX` + codes minimaux | `czBmp2Nmg` de la DLL | `nmg_from_bmp()` | ❌ jamais |

⇒ **Utiliser la première forme**, celle que la remorque affiche déjà.

Le `.Nmg` de Sigma Editor (fichiers de la bibliothèque) = en-tête `NG` (largeur, hauteur…) + ce
même corps + une **annexe d'édition** (« NoteNmg file version v4.01 ») que le panneau ignore.
Les `.Nmg` de `ACO LE MANS/` sans bloc RC sont des **messages texte** (police du panneau), sans image.

---

## 6. La liste de lecture `SEQUENT.SYS` ✅

```
53 51 04 00  NN NN 00 00          "SQ", version 4, nombre d'entrées (u16), 0
puis par entrée, 36 octets :
  'D' 'T'  0F FF  (20 × 00)  nom du fichier sur 12 octets complétés par des 00
```

Une entrée ⇒ le panneau joue ce seul message en boucle. Sans liste, il joue tous les fichiers.

⚠️ **Sigma Play écrit l'entrée autrement** (`SequentList.tmps`, envoi du 27/09) : octets
`80 7F` au lieu de `0F FF`, et deux horodatages (début et fin de programmation) au lieu des zéros.
Les deux formes sont du fabricant ; seule celle de la DLL est prouvée **ici** — § 10.

---

## 7. Ce que le panneau dit de lui-même (fiche système, réponse à 1/0x10)

Lecture **probable** (déduite, non vérifiée champ par champ) de la réponse du 27/09 :

| Octets | Contenu |
|---|---|
| 0-1 | `AA 55` |
| 2, 4 | `0x60`, `0x40` → **96 × 64** |
| 0x20-0x21 | `01 01` → groupe 1, unité 1 |
| 0x24 | IP du contrôleur (`192.168.8.50`) |
| 0x28 | adresse MAC (`00:1D:6F:…`) |
| 0x55 | chaîne préfixée par sa longueur (`20250516085` : n° de série ou date) |
| 0x6B | nom du panneau (`DISPLAY`) |

C'est le bon point d'appui pour un bouton « **Tester la connexion** » : lecture seule, sans effet
sur l'affichage.

---

## 8. 🔴 L'obstacle : le navigateur ne sait pas faire de TCP

`PMV_Bitmap_Editor.html` est une page ouverte dans un navigateur. **Aucun navigateur ne permet à
une page d'ouvrir une connexion TCP ou UDP brute** : ni `fetch`, ni `WebSocket` ne parlent
JetFileII. Il faut donc un **relais sur le PC** qui reçoit les octets de la page (par
`http://127.0.0.1`) et les transmet à la remorque.

| Relais possible | Pour | Contre |
|---|---|---|
| **Script PowerShell** | présent sur **tout** Windows, rien à installer, un seul fichier lisible | Windows seulement |
| Script Python (bibliothèque standard) | déjà écrit à 90 % (`jetfile.py`) | Python à installer sur le PC de terrain |
| Petit exécutable | double-clic | binaire à produire, à signer, à maintenir |

Dans tous les cas, **la page garde toute l'intelligence** (image → fichier → trames) et le relais
ne fait que **transporter** : une page autoporteuse + un relais d'une centaine de lignes.

---

### 8.1 Pour ce dossier

Le relais est `relais/relais-pmv.ps1` (déjà écrit et essayé). L'intégration dans la page est
l'objet du cahier des charges `LISEZ-MOI.md`. `reference/envoyer.py` fait déjà tout en ligne de commande
(validé sur remorque) et sert de référence de comportement.

## 9. Rejouer la preuve

```
python tools/faux_panneau.py                      # le jumeau, en tâche de fond, sur 127.0.0.1:9520
cd <copie de travail contenant les DLL de Sigma3000/ et le fichier à envoyer>
<python32> tools/etalon_dll.py czBmp2Nmg s:image.bmp s:MSG.Nmg i:3 i:0x30
<python32> tools/etalon_dll.py czShowFiles a:MSG.Nmg i:1 i:0 i:0 s:127.0.0.1 i:9520 i:0
python tools/verif_etalon.py MSG.Nmg image.bmp MSG.Nmg
```

`<python32>` : l'archive « embeddable » **win32** de python.org, décompressée — la DLL est en 32
bits. `etalon_dll.py` **refuse toute adresse autre que 127.0.0.1** (essayé avec l'adresse de la
remorque : refus avant tout envoi).

Résultat du 27/09/2026 : **tout concorde** — 17 trames de fichier, la trame `SEQUENT.SYS`, le
`.Nmg` de `czBmp2Nmg`, et le témoin (un octet muté est bien détecté).

---

## 10. Prouvé, déduit, inconnu

| | État |
|---|---|
| Format de trame, somme, accusé, réémissions | ✅ mesuré (DLL) |
| Écriture de fichier, liste de lecture | ✅ mesuré (DLL), reproduit à l'octet |
| Fichier-message à partir d'un BMP 565 | ✅ reproduit à l'octet (deux formes) |
| Même comportement en TCP qu'en UDP | ✅ mesuré (DLL, trames identiques) |
| **Sigma Play émet la même chose que la DLL** | ⚠️ **déduit** (même fabricant, même protocole) — **non mesuré** |
| **Le panneau réel accepte ces trames et affiche** | ✅ **27/09/2026 14:19, remorque 203.0.113.20 : mire tout blanc AFFICHÉE** (`tools/envoyer.py --mire`, forme Sigma Editor, `SEQUENT.SYS` forme DLL `0F FF`, somme `55 A7`, destination `0101`, TCP) |

### Mesuré sur la remorque réelle le 27/09/2026

- **1/0x10 ne lit PAS la fiche système** : elle rend le **style d'affichage par défaut**, 52 octets de
  la forme de `DEFAULT.SYS` (`aa55 43 00 44 30 31 …`). 2/0x0C en est l'écriture. ⇒ la correction du § 4
  (« octet 96 changé ») venait d'une **fausse réponse du jumeau**, pas du panneau. On ne l'émet toujours pas.
- **Les dimensions se lisent dans `CONFIG.SYS`** (1/0x02), octets 2 et 4 : `60 00 40 00` = 96 × 64.
  Réponse de lecture : arguments `d0 00 01 00 | d0 00 00 00` (taille u16, nb paquets u16, taille u32).
- 🔴 **Latence 4G** : le premier accusé de chaque opération arrive souvent après 10 s ⇒ réémission, puis
  l'accusé TARDIF de la première arrive. **Avec des numéros de séquence qui repartent à 1 à chaque
  opération (comme la DLL), cet accusé tardif a été pris pour la réponse de l'opération suivante** :
  la « sauvegarde » de `SEQUENT.SYS` était une copie de `CONFIG.SYS`, et la liste de lecture d'origine
  de la remorque a été perdue (le fichier message d'origine, lui, n'a pas été touché).
  ⇒ `envoyer.py` : **numéro de séquence unique sur toute la session**, réponse non conforme **ignorée**
  (on écoute encore, on ne renvoie pas), et refus de toute liste de lecture qui ne commence pas par `SQ`.
- 🔴🔴 **Le panneau ne traite une trame qu'après avoir reçu AU MOINS UN OCTET DE PLUS derrière elle**
  (sonde en lecture seule, remorque 203.0.113.30, 14:42) : trame seule → aucune réponse en 6 s, même
  avec `TCP_NODELAY` ; trame + `00` → réponse en **0,04 s** ; 5 trames suivies chacune de `00` → 5
  réponses. C'était la cause des « accusés tardifs » ci-dessus. ⇒ `envoyer.py` ajoute un `00` après
  chaque trame : lecture seule en **0,36 s** (contre ~30 s au départ) ; Sigma met 4 à 6 s pour un envoi.
- ✅ Séquence noir → blanc → logo affichée sur 203.0.113.30 (14:38-14:41), chaque image correcte.
- ✅ Même boucle AVEC l'octet final (14:43) : **2,2 s / 2,3 s / 2,0 s** par image, **zéro réémission**
  (Sigma : 4 à 6 s). La remorque 203.0.113.20 a été remise en conformité par l'auteur via Sigma.
- ⚠️ Le jumeau (`faux_panneau.py`) répond encore à 1/0x10 avec la fiche de 208 octets : **à corriger**
  (lui faire rendre `Sigma3000/DEFAULT.SYS`).
| Forme `0F FF` vs `80 7F` de `SEQUENT.SYS` | ⚠️ les deux existent ; laquelle préférer reste à voir |
| Effet exact de 2/0x0C | ❌ inconnu ⇒ ne jamais l'émettre |

**Les deux étapes qui restent, et qui demandent l'auteur :**
1. **Capturer Sigma Play lui-même** contre le jumeau (il faut cliquer « Envoyer » dans son
   interface) — ⚠️ sur une **copie** de `Sigma3000/` dont **toutes** les adresses ont été
   remplacées par `127.0.0.1` : sa configuration contient **une douzaine d'adresses de vraies
   remorques** (`NetGroup.Net`, `PreGroup.Net`, `NewLed.ini`, `SysSet.ini`, `LedSet.ini`,
   `CurSiteInfo`, `CONFIG.SYS`…).
2. **Un premier envoi réel**, sur une remorque **hors service**, message de test, l'auteur présent.

---

## 11. 🔴 À savoir : le panneau obéit à qui lui parle

Rien dans ce qui a été capturé n'est authentifié : **quiconque connaît l'adresse publique d'une
remorque et le port 9520 peut changer ce qu'elle affiche au bord de la route.** Le nom DNS
(`info<PLAQUE>.ddns.net`) se devine à partir de la plaque, qui se lit sur la remorque. La DLL
connaît des commandes `czLogin` / `czChangePSW` : le contrôleur **sait** peut-être protéger
l'accès par mot de passe — non exploré ici.
