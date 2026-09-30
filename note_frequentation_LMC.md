# LE MANS CLASSIC (LMC) — fréquentation et billetterie, toutes éditions en base

**Document de travail — matière brute destinée à la rédaction d'une note.**
Extrait de la base `titan` (production) le 29/09/2026. Tous les chiffres ci-dessous
sont issus de requêtes directes sur la base ou de documents du dossier `uploads/`,
jamais d'une estimation. Les incertitudes sont signalées comme telles.

> ⚠️ **À lire avant d'écrire quoi que ce soit** : la section 8 (« Pièges de lecture »)
> conditionne l'interprétation de presque tous les tableaux. Trois écueils en particulier :
> l'édition 2026 n'est **pas** mesurée sur le même périmètre que 2022–2025, le catalogue
> billetterie a **changé de structure** entre 2025 et 2026, et les totaux d'entrées ne sont
> **pas comparables** d'une édition à l'autre (le nombre de portes en service varie).

---

## 1. Les éditions présentes en base

Quatre éditions, sous **deux libellés différents** selon les collections — piège classique
du référentiel Cockpit : `historique_controle` range Le Mans Classic sous le sigle **`LMC`**,
tout le reste sous **`LE MANS CLASSIC`**.

| Édition | Jour de course | Période mesurée | Fréquentation | Billetterie | Détail portes |
|---|---|---|---|---|---|
| **2022** | samedi 02/07/2022 | 28/06 → 03/07 | ✅ `historique_controle` | ❌ | ❌ |
| **2023** | samedi 01/07/2023 | 27/06 → 02/07 | ✅ `historique_controle` | ✅ (PDF J-1 2025) | ✅ 22 portes |
| **2025** | samedi 05/07/2025 | 01/07 → 06/07 | ✅ `historique_controle` | ✅ (PDF J-1 2025) | ✅ 26 portes |
| **2026** | samedi 04/07/2026 | 28/06 → 06/07 | ⚠️ reconstituée (archives live-contrôle) | ✅ `parametrages` | ✅ 43 points de scan |

**Il n'y a pas d'édition 2024 en base**, et rien n'indique qu'elle ait existé.

**L'édition 2026 n'a pas été consolidée dans `historique_controle`.** C'est une lacune
opérationnelle à signaler : tant que ce document n'est pas écrit, l'édition 2026 sera
invisible comme référence N-1 pour toutes les comparaisons du cockpit (rapport matinal,
widget affluence, montre, vue Fréquentation du rapport scans). Les données brutes existent
— elles sont dans `hsh_archive_compteurs_LE_MANS_CLASSIC_2026` — mais aucun consommateur
du cockpit ne va les y chercher.

---

## 2. Fréquentation — synthèse par édition

Enceinte générale. « Pic de présents » = plus haut solde `entrées − sorties` observé.

| Édition | Entrées cumulées | Sorties cumulées | **Pic de présents** | Jour et heure du pic | Granularité |
|---|---|---|---|---|---|
| 2022 | 209 093 | 170 607 | **50 426** | samedi 02/07, 17h00 | horaire |
| 2023 | 310 254 | 260 349 | **69 065** | samedi 01/07, 16h00 | horaire |
| 2025 | 312 138 | 268 435 | **72 021** | samedi 05/07, 16h00 | horaire |
| 2026 ⚠️ | 265 514 | 247 121 | **52 409** (brut)<br>**48 618** hors véhicules | samedi 04/07, 16h29 | 3 min |

**Évolution du pic sur la série homogène 2022 → 2025 :** 50 426 → 69 065 (+37 %) → 72 021 (+4 %).

**Le chiffre 2026 ne se compare pas directement aux trois précédents** (voir § 8.1). Il est
reconstitué sur le seul compteur Skidata « ENCEINTE GENERALE » (Area 628), alors que la série
2022–2025 provient de documents dont le périmètre de portes n'est pas documenté. Deux indices
que les périmètres diffèrent : le total de scans lus sur **toutes** les portes en 2026
(278 187) dépasse le cumul d'entrées du compteur Area 628 (265 514) ; et la billetterie 2026
progresse (+8 % d'entrées vendues) quand la fréquentation mesurée reculerait de 27 % — une
divergence de cette ampleur signe un changement de mesure, pas un effondrement du public.

Le pic tombe **le samedi en milieu d'après-midi** sur les quatre éditions, entre 16h00 et 17h00.
C'est l'invariant le plus solide du jeu de données.

---

## 3. Fréquentation — détail journalier

### 2022 (course samedi 02/07)

| Date | Jour | J±n | Entrées | Sorties | Pic de présents | Heure du pic |
|---|---|---|---|---|---|---|
| 28/06 | mardi | J-4 | — | — | — | *aucune mesure* |
| 29/06 | mercredi | J-3 | 3 843 | 2 389 | 1 454 | 23h00 |
| 30/06 | jeudi | J-2 | 16 101 | 12 798 | 8 737 | 18h00 |
| 01/07 | vendredi | J-1 | 53 572 | 38 991 | 30 898 | 17h00 |
| **02/07** | **samedi** | **J** | **78 962** | **70 321** | **50 426** | **17h00** |
| 03/07 | dimanche | J+1 | 56 615 | 46 108 | 43 502 | 14h00 |

### 2023 (course samedi 01/07)

| Date | Jour | J±n | Entrées | Sorties | Pic de présents | Heure du pic |
|---|---|---|---|---|---|---|
| 27/06 | mardi | J-4 | 2 251 | 1 916 | 478 | 18h00 |
| 28/06 | mercredi | J-3 | 8 591 | 6 724 | 2 523 | 18h00 |
| 29/06 | jeudi | J-2 | 27 751 | 23 547 | 13 907 | 18h00 |
| 30/06 | vendredi | J-1 | 85 283 | 66 875 | 44 056 | 17h00 |
| **01/07** | **samedi** | **J** | **109 847** | **95 929** | **69 065** | **16h00** |
| 02/07 | dimanche | J+1 | 76 531 | 65 358 | 60 791 | 13h00 |

### 2025 (course samedi 05/07)

| Date | Jour | J±n | Entrées | Sorties | Pic de présents | Heure du pic |
|---|---|---|---|---|---|---|
| 01/07 | mardi | J-4 | 3 247 | 2 789 | 553 | 15h00 |
| 02/07 | mercredi | J-3 | 6 672 | 5 222 | 2 125 | 20h00 |
| 03/07 | jeudi | J-2 | 29 233 | 25 944 | 13 739 | 17h00 |
| 04/07 | vendredi | J-1 | 91 431 | 66 006 | 46 786 | 16h00 |
| **05/07** | **samedi** | **J** | **113 684** | **99 933** | **72 021** | **16h00** |
| 06/07 | dimanche | J+1 | 67 871 | 68 541 | 53 298 | 14h00 |

### 2026 (course samedi 04/07) — reconstitution

Le pic « hors véhicules » applique la formule du cockpit (`présents = current − véhicules`),
les véhicules étant cumulés depuis la remise à zéro du compteur, constatée le **28/06/2026
à 17h56 UTC**. Cette déduction n'est possible qu'en 2026 : les soldes véhicules des éditions
antérieures ne sont plus en base.

| Date | Jour | J±n | Entrées | Sorties | Pic brut | Pic hors véhicules | Heure du pic |
|---|---|---|---|---|---|---|---|
| 29/06 | lundi | J-5 | 2 154 | 1 949 | 583 | 254 | 15h26 |
| 30/06 | mardi | J-4 | 4 017 | 3 502 | 1 126 | 561 | 16h11 |
| 01/07 | mercredi | J-3 | 9 600 | 8 239 | 2 798 | 1 488 | 17h59 |
| 02/07 | jeudi | J-2 | 26 371 | 23 330 | 10 090 | 7 826 | 17h32 |
| 03/07 | vendredi | J-1 | 64 976 | 53 215 | 29 368 | 26 218 | 15h53 |
| **04/07** | **samedi** | **J** | **98 073** | **83 485** | **52 409** | **48 618** | **16h29** |
| 05/07 | dimanche | J+1 | 60 323 | 73 401 | 45 392 | 41 588 | 12h50 |
| 06/07 | lundi | J+2 | 0 | 0 | 18 393 | 15 100 | *résidu* |

Le « résidu » du lundi n'est pas une fréquentation : c'est le solde jamais soldé du compteur,
les sorties de fin d'événement n'étant pas scannées. Le phénomène existe sur toutes les
éditions (solde final de 38 486 en 2022, 49 905 en 2023, 43 703 en 2025, 18 393 en 2026).

**Forme de la montée en charge, comparée** — part de chaque journée dans le total d'entrées :

| Journée | 2022 | 2023 | 2025 | 2026 |
|---|---|---|---|---|
| J-2 (jeudi) | 7,7 % | 8,9 % | 9,4 % | 9,9 % |
| J-1 (vendredi) | 25,6 % | 27,5 % | 29,3 % | 24,5 % |
| J (samedi) | 37,8 % | 35,4 % | 36,4 % | 36,9 % |
| J+1 (dimanche) | 27,1 % | 24,7 % | 21,7 % | 22,7 % |

Le profil est remarquablement stable : le samedi pèse toujours entre 35 et 38 % des entrées.
Le seul décrochage est le vendredi 2026 (24,5 % contre 29,3 % en 2025).

---

## 4. Types de personnes vus aux portes (fréquentation)

### 4.1 Ventilation des passages — édition 2026 uniquement

C'est la **seule édition** pour laquelle la base conserve la catégorisation des scans. Elle
vient de `hsh_archive_tx_LE_MANS_CLASSIC_2026` (transactions Handshake, agrégées par créneau
de 5 min et par borne), restreinte ici aux 115 bornes rattachées à l'enceinte générale.

| Jour | Scans lus (entrées) | Véhicules | Enfants | Accrédités | **Autres (grand public)** | Erreurs de scan |
|---|---|---|---|---|---|---|
| lundi 29/06 | 1 332 | 1 212 | 0 | 0 | 120 | 332 |
| mardi 30/06 | 2 072 | 1 896 | 0 | 0 | 176 | 514 |
| mercredi 01/07 | 4 270 | 3 794 | 0 | 0 | 476 | 802 |
| jeudi 02/07 | 17 308 | 4 885 | 483 | 0 | 11 940 | 2 948 |
| vendredi 03/07 | 54 945 | 5 262 | 2 605 | 0 | 47 078 | 7 292 |
| **samedi 04/07** | **89 277** | 5 190 | 5 545 | 0 | **78 542** | 10 713 |
| dimanche 05/07 | 54 691 | 3 633 | 3 500 | 0 | 47 558 | 7 839 |
| **TOTAL** | **223 895** | **25 872** | **12 133** | **0** | **185 890** | **30 440** |
| *part* | 100 % | 11,6 % | 5,4 % | 0 % | **83,0 %** | — |

Sorties correspondantes : 198 305 dont 22 579 véhicules et 9 982 enfants.

**Lecture :** le grand public représente **83 % des passages** en enceinte, les véhicules 11,6 %
et les enfants 5,4 %. Le renversement du profil avant/pendant l'événement est net : jusqu'au
mercredi, les passages sont à **89 % des véhicules** (montage, exposants, organisation) ; à
partir du jeudi, le public prend le dessus (69 % le jeudi, 86 % le vendredi, 88 % le samedi).

⚠️ **Trois réserves sur ce tableau :**
- **« Accrédités » à zéro n'est pas un fait, c'est une panne de détection.** La règle de
  catégorisation repose sur un UPID finissant par `-ACCRED` ou un UTID commençant par `EWC` ;
  aucun scan LMC 2026 ne les porte. Les accrédités sont donc noyés dans « Autres » — le
  chiffre de 83 % de grand public est un **majorant**.
- **Couverture 84,3 %.** Les transactions lues (223 895) ne couvrent que 84,3 % des entrées
  comptées par le compteur (265 514). Les 15,7 % manquants sont des titres que la borne ne
  sait pas restituer (codes-barres physiques hors référentiel). Les **parts** sont fiables,
  les **volumes absolus** sont des minorants.
- **« Autres » ≠ « grand public » au sens strict.** La catégorie signifie « titre absent du
  référentiel des accréditations ACO » : elle contient le public payant, mais aussi tout titre
  émis par une billetterie tierce.

### 4.2 Nature des titres d'accréditation présentés — 2026

Depuis `hsh_archive_titres_LE_MANS_CLASSIC_2026`, **40 libellés de titres** identifiés,
33 290 entrées au total (le reste des passages relevant du grand public, non nominatif) :

| Titre | Entrées | Sorties | Nature |
|---|---|---|---|
| Bracelet Enfant | 11 422 | 10 748 | public (enfants) |
| TECH | 8 482 | 8 901 | technique / exposants |
| Navettes | 3 140 | 3 283 | transport |
| Montage / Démontage | 2 881 | 2 998 | logistique |
| AA Houx / Sticker | 2 462 | 2 310 | camping |
| Enceinte Générale | 1 589 | 1 639 | accès service |
| AA Commissaires — C11 | 558 | 479 | commissaires |
| P7 D | 354 | 405 | parking |
| Spare | 245 | 252 | divers |
| AA Commissaires — C10 | 230 | 141 | commissaires |
| PC Sud | 201 | 189 | organisation |
| AA Commissaires — Karting Loisir | 176 | 138 | commissaires |
| *29 autres libellés* | < 175 chacun | | parkings, paddocks, livraisons |

Profil temporel parlant : `Montage / Démontage` culmine le mercredi 01/07 (1 275 passages)
puis s'effondre à 28 le jeudi — le basculement montage → exploitation est net et daté.
À l'inverse `Bracelet Enfant` démarre le jeudi (431) et culmine le samedi (5 383).

### 4.3 Répartition par porte

**2025 — 26 points de scan, 630 678 scans (tous sens confondus)**

| Porte | Scans | Part |
|---|---|---|
| PORTE NORD PIETONS | 130 579 | 20,7 % |
| PORTE SUD | 86 411 | 13,7 % |
| PORTE KARTING PIETONS | 60 113 | 9,5 % |
| PORTE CIK | 40 932 | 6,5 % |
| PORTE EST | 38 338 | 6,1 % |
| PORTE PANORAMA | 37 539 | 6,0 % |
| PORTE CHATEAU | 34 534 | 5,5 % |
| PORTE GARAGE VERT | 29 781 | 4,7 % |
| PORTE ANNEXE | 28 950 | 4,6 % |
| PORTE TERTRE ROUGE | 25 851 | 4,1 % |
| PORTE HOUX 5 | 25 393 | 4,0 % |
| PORTE NORD VEHICULES | 24 038 | 3,8 % |
| PORTE MAISON BLANCHE PIETONS | 19 616 | 3,1 % |
| PORTE MAISON BLANCHE VEHICULES | 13 185 | 2,1 % |
| *12 autres* | 35 418 | 5,6 % |

Familles repérables au libellé : portes piétonnes **210 308 scans**, portes véhicules
**42 517**, non précisé **377 853**.

**2023 — 22 points de scan, 571 302 scans.** Même hiérarchie : PORTE NORD PIETONS 96 879
(17,0 %), PORTE SUD 68 191 (11,9 %), PORTE CIK 53 929 (9,4 %), PORTE ANNEXE 49 993 (8,8 %),
PORTE CHÂTEAU 38 405 (6,7 %). Piétons 114 503 / véhicules 24 566 / non précisé 432 233.

**2026 — 43 points de scan, 278 187 entrées lues** (toutes zones, enceinte + parkings + AA) :

| Porte | Entrées | Part | Véhicules | Enfants |
|---|---|---|---|---|
| PORTE NORD PIETONS | 72 608 | 26,1 % | 29 | 4 840 |
| ENTREE MUSEE | 27 188 | 9,8 % | 0 | 178 |
| PORTE SUD | 23 757 | 8,5 % | 16 455 | 514 |
| PORTE KARTING PIETONS | 19 515 | 7,0 % | 0 | 1 287 |
| PORTE PANORAMA | 17 485 | 6,3 % | 0 | 1 184 |
| PORTE EST | 13 366 | 4,8 % | 0 | 929 |
| PORTE CHATEAU | 12 317 | 4,4 % | 668 | 655 |
| AA ARNAGE | 11 292 | 4,1 % | 4 | 592 |
| PORTE GARAGE VERT | 10 978 | 3,9 % | 520 | 222 |
| PORTE TERTRE ROUGE | 10 276 | 3,7 % | 61 | 783 |
| PORTE ANNEXE | 9 964 | 3,6 % | 0 | 718 |
| PORTE NORD VEHICULES | 8 463 | 3,0 % | 2 743 | 40 |
| PORTE HOUX 5 | 5 856 | 2,1 % | 3 042 | 192 |
| *30 autres* | 35 122 | 12,6 % | | |

**PORTE NORD PIETONS est la porte structurante de toutes les éditions mesurées**, et son poids
relatif croît : 17,0 % (2023) → 20,7 % (2025) → 26,1 % (2026). La tendance est nette mais les
trois pourcentages ne reposent pas sur la même base de calcul (voir l'avertissement ci-dessous) :
à citer comme un ordre de grandeur, pas comme une série. **PORTE SUD est la porte véhicules de fait**
en 2026 : 16 455 des 23 757 entrées y sont des véhicules (69 %).

⚠️ Les trois tableaux ne se comparent pas ligne à ligne : 2023 et 2025 comptent des
**scans tous sens confondus**, 2026 des **entrées seules**, et le nombre de points de scan
passe de 22 à 26 puis 43. Les **parts** restent indicatives, les volumes non.

### 4.4 Occupation des parkings et aires d'accueil — 2026

| Zone | Entrées | Sorties | Pic d'occupation | Instant du pic |
|---|---|---|---|---|
| AA BEAUSEJOUR | 2 211 | 46 | 2 165 | 05/07 12h17 |
| P EXPO | 1 932 | 32 | 1 900 | 05/07 14h38 |
| P OUEST | 1 871 | 16 | 1 855 | 05/07 15h26 |
| AA ARNAGE | 10 756 | 10 238 | 1 812 | 04/07 23h26 |
| P PANORAMA | 1 687 | 2 | 1 685 | 05/07 14h32 |
| AA EPINETTES | 775 | 15 | 760 | 05/07 10h08 |
| AA MULSANNE | 4 601 | 4 491 | 567 | 05/07 13h02 |
| P MULSANNE | 114 | 2 | 112 | 05/07 12h47 |
| AA CLOS FLEURI / AA HIPPODROME / AA PRAIRIE / P ARNAGE / AA NURBURGRING | ≈ 0 | | 0 | — |

⚠️ La plupart de ces compteurs sont **en entrée seule** (P PANORAMA : 1 687 entrées pour
2 sorties). Leur « pic » est donc un cumul, pas une occupation instantanée. Seules AA ARNAGE
et AA MULSANNE comptent dans les deux sens.

---

## 5. Billetterie — ce que la base et les états hebdo contiennent

| Édition | Source | Nature | Date de l'état |
|---|---|---|---|
| 2022 | — | **aucune donnée** | — |
| 2023 | `uploads/LMC2025/25 LMC_Etat Hebdo_J-1.pdf` (colonnes comparatives) | totaux par famille + type de tarif | 30/06/2023 (J-1) |
| 2025 | idem | totaux par famille + type de tarif | 04/07/2025 (J-1) |
| 2026 | `parametrages` → `tickets.products` | 78 produits, 19 relevés hebdomadaires | 03/07/2026 (J-1) |

**Il n'y a pas de billetterie 2025 dans la base** : le document `parametrages` LE MANS CLASSIC
2025 a un bloc `tickets` vide. Les chiffres 2023 et 2025 ci-dessous viennent donc des états
hebdomadaires du service Billetterie déposés dans `uploads/LMC2025/`, pas de Mongo.

### 5.1 Synthèse 2023 vs 2025 (états J-1, périmètre identique)

| Famille de titres | 2023 | 2025 | Écart |
|---|---|---|---|
| **Entrées circuit** | **57 241** | **65 249** | **+8 008 (+14,0 %)** |
| Paddock / Gridwalk | 28 075 | 32 134 | +4 059 (+14,5 %) |
| Packages paddock week-end + tribune | 40 854 | 28 442 | −12 412 (−30,4 %) |
| Tribunes | 11 102 | 11 334 | +232 (+2,1 %) |
| Aires d'accueil / campings | 7 839 | 7 660 | −179 (−2,3 %) |
| Parkings publics | 7 219 | 6 370 | −849 (−11,8 %) |
| Packs hospitalité | 162 | 116 | −46 (−28,4 %) |
| *(hors total)* Parkings Peter Auto | *1 354* | *7 674* | *+6 320 (+466,8 %)* |
| **VOLUME TOTAL VENTES** | **152 492** | **151 305** | **−1 187 (−0,8 %)** |
| **CA VENTES** | **9 373 120 €** | **11 120 072 €** | **+1 746 952 € (+18,6 %)** |

*Contrôle de cohérence effectué : la somme des familles recompose le volume total **exactement**,
aux deux éditions, une fois les parkings Peter Auto exclus (2023 : 153 846 − 1 354 = 152 492 ;
2025 : 158 979 − 7 674 = 151 305). Le bloc Peter Auto n'entre donc pas dans le volume annoncé.*

**Le fait marquant de 2025 : un volume stable (−0,8 %) pour un chiffre d'affaires en forte
hausse (+18,6 %).** C'est un effet prix et un effet mix, pas un effet de fréquentation. Le prix
moyen du billet d'entrée passe de **64,34 € à 71,94 €** (+11,8 %), et le mix se déporte des
packages (−30 %) vers les entrées sèches (+14 %) et les paddocks (+14,5 %).

Objectifs commerciaux atteints : entrées **65 249 pour une cible de 55 000** (119 %),
paddocks **32 134 pour 32 000** (100 %).

### 5.2 Entrées circuit par formule — 2023 vs 2025

| Formule | 2023 | 2025 | Écart |
|---|---|---|---|
| Jeudi | 0 | 0 | — |
| Vendredi | 3 492 | 6 078 | **+74,1 %** |
| Dimanche | 8 758 | 9 420 | +7,6 % |
| 4 Jours | 44 696 | ~49 672 | ~+11,1 % |
| Virage Mulsanne-Arnage | 309 | 96 | −68,9 % |
| **Total (dont commandé)** | **57 255** | **65 266** | **+14,0 %** |

*Le 4 Jours 2025 est obtenu par différence ; la somme de ses lignes tarifaires lues dans le PDF
donne 49 660, soit un écart de 12 unités attribuable à la superposition des lignes dans le PDF.*

**Le billet 4 Jours reste la formule dominante : 78 % des entrées en 2023, 76 % en 2025.**
La croissance vient d'abord du **vendredi, qui fait +74 %** — la journée d'ouverture est en
train de devenir une vraie journée de public.

### 5.3 Entrées circuit par type de tarif — 2023 vs 2025

C'est la ventilation « types de personnes » côté billetterie. Elle n'existe que pour 2023 et 2025.

**Formule Vendredi**

| Tarif | 2023 | 2025 |
|---|---|---|
| Plein Tarif | 2 065 | 4 461 |
| Tarif ACO | 322 | 644 |
| Contrôleur CE | 295 | 319 |
| Étudiant | 232 | 432 |
| Salarié | — | 143 |
| PMR | 38 | 79 |
| Tarif Early (supprimé en 2025) | 538 | 0 |
| Groupe | 0 | 0 |

**Formule Dimanche**

| Tarif | 2023 | 2025 |
|---|---|---|
| Plein Tarif | 4 367 | 6 227 |
| Tarif ACO | 926 | 1 134 |
| Contrôleur CE | 1 312 | 1 007 |
| Étudiant | 764 | 955 |
| Groupe | 94 | 0 |
| PMR | 76 | 86 |
| Salarié | 7 | 11 |
| Tarif Early (supprimé) | 1 210 | 0 |

**Formule 4 Jours**

| Tarif | 2023 | 2025 |
|---|---|---|
| Plein Tarif | 13 632 | 26 923 |
| Tarif ACO | 8 861 | 13 778 |
| Contrôleur CE | 4 605 | 3 245 |
| Étudiant | 2 289 | 3 116 |
| Groupe / PMR | 1 183 | 1 480 |
| Club Peter Auto | 394 | 495 |
| Salarié | 324 | 402 |
| Exposant Peter Auto | 145 | 100 |
| Individuel Peter Auto | 57 | 37 |
| Tarif Early (supprimé) | 13 124 | 0 |

**Deux mouvements structurants :**
1. **La suppression du Tarif Early** en 2025 (13 124 + 1 210 + 538 = 14 872 billets en 2023,
   zéro en 2025) est la principale explication de la hausse du prix moyen. Ces volumes se
   reportent sur le Plein Tarif, qui double quasiment sur le 4 Jours (13 632 → 26 923).
2. **Le Tarif ACO progresse de +55 %** sur le 4 Jours (8 861 → 13 778) et représente **28 %**
   des entrées 4 Jours 2025. Toutes formules confondues, le Tarif ACO pèse **15 556 entrées
   sur 65 249, soit 24 %** du public payant. S'y ajoutent, sur des canaux distincts qu'il ne
   faut pas amalgamer avec la communauté ACO : les tarifs Contrôleur / CE (4 571, soit 7 %)
   et Salarié (556, soit 0,9 %). Les tarifs réduits « sociaux » restent marginaux :
   Étudiant 4 503 (6,9 %). Le PMR n'est isolable que sur Vendredi et Dimanche (165 titres) —
   sur le 4 Jours il est fusionné avec le tarif Groupe (1 480) dans l'état hebdo.

### 5.4 Billetterie 2026 (base Cockpit)

Catalogue de **78 produits**, dernier import du **03/07/2026** (veille de la course).

| Famille | Titres vendus | Produits | dont non vendus |
|---|---|---|---|
| Entrées circuit seules | 49 998 | 9 | 2 |
| Entrées + paddocks (combinées) | 18 950 | 6 | 0 |
| Entrées + tribune | 1 524 | 2 | 0 |
| **Sous-total « droit d'accès enceinte »** | **70 472** | | |
| Suppléments paddocks / gridwalk | 19 428 | 5 | 0 |
| Tribunes et emplacements réservés | 7 908 | 13 | 2 |
| Parkings | 9 883 | 23 | 6 |
| Aires d'accueil / campings | 4 057 | 11 | 6 |
| Hospitalités | 30 | 9 | 2 |
| **TOTAL CATALOGUE** | **111 778** | **78** | **18** |

**Produits les plus vendus :**

| Produit | Ventes |
|---|---|
| LMC Entrée Circuit - 4 Jours | 19 760 |
| LMC Entrée Circuit - Week End | 12 153 |
| LMC Supplément Paddocks (3 jours) | 11 801 |
| LMC Entrée 4 Jours + Paddocks | 6 045 |
| LMC Entrée Circuit 4 J. ou W-E + Tribune + Paddock | 5 262 |
| LMC Entrée Circuit - 4 Jours (Package) | 5 193 |
| LMC Entrée Circuit - Dimanche | 4 714 |
| LMC Supplément Paddocks Week-end | 4 594 |
| LMC Entrée Circuit - Vendredi | 4 591 |
| LMC Entrée Week-end + Paddocks | 4 573 |
| Catégorie Public (Ligne Droite) — tribune | 4 524 |
| LMC P Expo — parking | 3 021 |
| LMC AA Beauséjour — camping | 2 227 |

**Nouveauté 2026 : la formule Week-End** (12 153 + 1 919 en package = 14 072 titres), qui
n'existait pas dans la structure 2023/2025 (voir § 8.2). Les journées sèches Jeudi et Samedi
existent au catalogue mais n'ont **rien vendu**.

### 5.5 Courbe de vente 2026 (19 relevés hebdomadaires)

| Date du relevé | Tous produits | Entrées circuit | Δ entrées |
|---|---|---|---|
| 30/01/2026 | 47 295 | 28 769 | — |
| 06/02 | 48 931 | 29 795 | +1 026 |
| 13/02 | 50 885 | 30 958 | +1 163 |
| 20/02 | 52 431 | 31 963 | +1 005 |
| 27/02 | 53 788 | 32 857 | +894 |
| **06/03** | **61 878** | **40 087** | **+7 230** |
| 13/03 | 63 755 | 41 297 | +1 210 |
| 20/03 | 65 180 | 42 204 | +907 |
| 27/03 | 66 450 | 43 042 | +838 |
| 03/04 | 67 875 | 43 946 | +904 |
| 10/04 | 69 372 | 44 945 | +999 |
| 17/04 | 70 793 | 45 849 | +904 |
| **22/05** | **79 318** | **51 635** | **+5 786** |
| 29/05 | 81 938 | 52 872 | +1 237 |
| 05/06 | 84 654 | 54 653 | +1 781 |
| 12/06 | 89 820 | 57 804 | +3 151 |
| 19/06 | 97 110 | 61 480 | +3 676 |
| 26/06 | 102 760 | 64 705 | +3 225 |
| **03/07 (J-1)** | **111 778** | **70 472** | **+5 767** |

**Deux ressauts** (06/03 : +7 230 ; 22/05 : +5 786) qui ne correspondent pas au rythme
hebdomadaire : ce sont vraisemblablement des rattrapages d'import, pas des pics de vente.
Attention, **il manque quatre relevés entre le 17/04 et le 22/05** (cinq semaines sans mesure),
ce qui explique mécaniquement le ressaut du 22/05.

**L'accélération finale est forte : 27 % des entrées circuit se vendent après le 22/05**, et
+5 767 sur la seule dernière semaine (8 % du total en 7 jours). La vente de dernière minute
est un phénomène de premier ordre sur cet événement.

---

## 6. Rapprochement billetterie ↔ fréquentation

| Édition | Entrées circuit vendues | Entrées scannées (enceinte) | Ratio scans / billet |
|---|---|---|---|
| 2023 | 57 241 | 310 254 | 5,4 |
| 2025 | 65 249 | 312 138 | 4,8 |
| 2026 ⚠️ | 70 472 | 265 514 | 3,8 |

Le ratio n'est pas une anomalie : un billet 4 Jours est scanné à chaque entrée et à chaque
ré-entrée, et le total de scans inclut l'organisation, les exposants, les commissaires et les
véhicules. **Mais sa dérive (5,4 → 4,8 → 3,8) est exactement le symptôme d'un changement de
périmètre de mesure**, pas d'un changement de comportement du public. Elle confirme le § 8.1.

---

## 7. Ce qui manque en base

À signaler explicitement dans la note : ces absences ne sont pas des zéros.

- **Billetterie 2022 et 2025 : absentes de Mongo.** 2025 n'est récupérable que par les PDF
  hebdomadaires de `uploads/LMC2025/`. 2022 n'existe nulle part.
- **Fréquentation 2026 : non consolidée** dans `historique_controle`. L'édition sera invisible
  comme référence N-1 pour tous les modules du cockpit tant que le document n'est pas écrit.
- **Catégorisation des passages : 2026 seulement.** Aucune ventilation véhicules / enfants /
  accrédités n'existe pour 2022, 2023 et 2025.
- **Détection des accrédités : inopérante en 2026** (0 accrédité détecté sur 223 895 scans).
- **Aucune édition 2024.**
- **Le panier `globalHoraires.ticketing` de 2026 ne contient aucune entrée circuit** : 24 produits
  déclarés, uniquement des parkings, aires d'accueil et emplacements PMR, soit 14 062 ventes sur
  111 778. Le widget d'affluence prévisionnelle du cockpit, qui se restreint à ce panier, calcule
  donc pour LMC un taux de remplissage et une projection **sur les seuls parkings** — indicateur
  faux par construction pour cet événement. C'est un défaut de paramétrage à corriger, et un
  point à ne pas reprendre tel quel dans une note de fréquentation.
- **Aucun document `complet`** (séries au quart d'heure) pour aucune édition LMC : tous les pics
  2022–2025 sont issus d'un échantillonnage **horaire** et sous-estiment donc légèrement le pic réel
  (l'écart mesuré sur d'autres événements va de +0,5 % à +2,9 %).

---

## 8. Pièges de lecture — à respecter dans la rédaction

### 8.1 L'édition 2026 n'est pas sur le même périmètre que 2022–2025

**Ne pas écrire que la fréquentation a chuté de 27 % en 2026.** Les faits :

- 2022, 2023 et 2025 viennent de documents `historique_controle{type: frequentation}` dont le
  périmètre de portes n'est pas documenté (ces documents ne portent pas de champ `source`).
- 2026 est reconstitué par mes soins sur le seul compteur Skidata Area 628 « ENCEINTE GENERALE ».
- Le total des scans lus sur toutes les portes en 2026 (278 187) **dépasse** le cumul d'entrées
  de l'Area 628 (265 514) : il existe donc des points de passage hors de cette Area.
- La billetterie 2026 est en hausse (+8 % d'entrées vendues vs 2025) pendant que la mesure
  reculerait de 27 %. Les deux ne peuvent pas être vrais ensemble.
- La vérification directe est impossible : `data_access` ne contient plus aucun relevé des
  fenêtres 2025 ni 2026 (purge après archivage), et il n'existe aucune archive de compteurs
  antérieure à 2026.

**Formulation recommandée** : présenter 2022–2025 comme une série homogène, et 2026 à part,
en disant que son niveau n'est pas comparable et que seul son **profil** (forme de la montée
en charge, heure du pic, répartition par porte) l'est.

### 8.2 Le catalogue billetterie a changé de structure entre 2025 et 2026

Les 70 472 « entrées circuit » 2026 ne se comparent pas mécaniquement aux 65 249 de 2025 :

- En 2026 j'ai additionné **tous** les produits dont le nom porte « Entrée », y compris les
  formules combinées « Entrée + Paddocks » (18 950) et « Entrée + Tribune » (1 524).
- En 2023/2025, le bloc ENTRÉES du reporting billetterie ne contenait que les entrées sèches,
  les combinées étant dans un bloc « packages paddock week-end + tribune » séparé (28 442 en 2025).
- Les entrées sèches seules donnent **49 998 en 2026 contre 65 249 en 2025**, soit −23 %.

**Les deux lectures sont défendables et elles sont contradictoires** : +8 % ou −23 % selon le
périmètre. Donner les deux, ne pas trancher sans validation du service Billetterie.
S'ajoute une rupture de nomenclature : la formule **Week-End apparaît en 2026** et n'a
pas d'équivalent 2023/2025, où l'offre se répartissait entre Vendredi, Dimanche et 4 Jours.

### 8.3 Les totaux d'entrées ne mesurent pas la foule

Le nombre de points de scan passe de 22 (2023) à 26 (2025) puis 43 (2026). Un total d'entrées
en hausse peut ne refléter que l'ouverture de portes supplémentaires. **Le pic de présents est
le seul indicateur robuste** d'une édition à l'autre : il dépend peu des portes ouvertes en marge.

### 8.4 Le solde final n'est pas une population résiduelle

38 486 (2022), 49 905 (2023), 43 703 (2025), 18 393 (2026) « présents » à la dernière mesure :
ce sont des sorties jamais scannées, pas des gens restés sur site. À l'évacuation, les portes
sont ouvertes en grand et le contrôle d'accès cesse d'être tenu.

### 8.5 Les fuseaux horaires

Toute la chaîne travaille en **heure locale de Paris, en datetimes naïfs**. Une exception :
les compteurs Skidata (`hsh_archive_compteurs_*`) sont en **vrai UTC**, et les transactions
(`hsh_archive_tx_*`, champ `tranche`) portent de l'**heure de Paris étiquetée UTC**. Les
horaires de pic 2026 de ce document sont déjà convertis en heure de Paris.

### 8.6 Le libellé de l'événement

`LMC` dans `historique_controle`, `LE MANS CLASSIC` partout ailleurs. Une requête sur un seul
des deux libellés ne remonte rien, **en silence**.

### 8.7 Une date de course fausse en base

Le document `parametrages` LE MANS CLASSIC **year 2025** porte `globalHoraires.race` =
**05/07/2026**, soit la date de l'édition suivante. La date retenue dans ce document pour 2025
(samedi 05/07/2025) vient de `historique_controle{type: portes}`, qui est fiable.

---

## 9. Sources

| Donnée | Emplacement exact |
|---|---|
| Fréquentation 2022 / 2023 / 2025 | `titan.historique_controle` `{event: "LMC", year: <n>, type: "frequentation"}` |
| Détail portes 2023 / 2025 | `titan.historique_controle` `{event: "LMC", year: <n>, type: "portes"}` |
| Compteurs 2026 | `titan.hsh_archive_compteurs_LE_MANS_CLASSIC_2026` (53 158 relevés, ~3 min, Area 628) |
| Transactions catégorisées 2026 | `titan.hsh_archive_tx_LE_MANS_CLASSIC_2026` (52 255 docs) |
| Titres présentés 2026 | `titan.hsh_archive_titres_LE_MANS_CLASSIC_2026` (11 107 docs) |
| Structure des bornes 2026 | `titan.hsh_archive_structure_LE_MANS_CLASSIC_2026` (506 docs) |
| Billetterie 2026 | `titan.parametrages` `{event: "LE MANS CLASSIC", year: "2026"}` → `tickets.products` |
| Billetterie 2023 et 2025 | `uploads/LMC2025/25 LMC_Etat Hebdo_J-1.pdf`, page 3 |
| Historique des imports billetterie | `titan.ticket_imports` `{event: "LE MANS CLASSIC"}` (20 imports) |

**Remise à zéro du compteur 2026** : constatée le 28/06/2026 à 17h56 UTC (`entries` passant de
912 587 à 0). Tous les cumuls 2026 de ce document partent de cet instant.

**Attention** : le champ `year` est un **entier** dans `historique_controle` et une **chaîne**
dans `parametrages`. Une requête typée à tort ne remonte rien.
