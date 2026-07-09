# Analyse opérationnelle — collection `pcorg` (PC Organisation, Circuit des 24h du Mans)

> Diagnostic automatique de la main courante du PC Organisation (PCO) et du PC Sûreté (PCS).
> Source : MongoDB `titan_dev.pcorg`, alimentée depuis SQL Server `AppHistoV4.dbo.UserMessages` via `uploads/pcorg/sync_pcorg_sql.py`.

## 1. Périmètre

- **25 224 fiches** d'intervention analysées.
- **Étendue : 26/03/2024 → 14/05/2026** (~2 ans, toutes éditions confondues).
- Catégories captées à la source : `PCO.*`, `PCS.Information`, `PCS.Surete`.
- Périmètre retenu : **intégralité de la collection** (événements nommés + activité `SAISON` hors-événement).

### Répartition par édition

| Événement / année | Fiches |
|---|---:|
| SAISON 2025 | 5255 |
| SAISON 2024 | 5040 |
| SAISON 2026 | 2087 |
| 24H AUTOS 2025 | 1995 |
| 24H AUTOS 2024 | 1714 |
| 24H MOTOS 2025 | 1006 |
| LE MANS CLASSIC 2025 | 1003 |
| GPF 2025 | 1000 |
| GP EXPLORER 2025 | 987 |
| 24H MOTOS 2026 | 941 |
| GPF 2026 | 837 |
| 24H MOTOS 2024 | 771 |
| GPF 2024 | 724 |
| 24H CAMIONS 2025 | 656 |
| 24H CAMIONS 2024 | 520 |
| SUPERBIKE 2024 | 183 |
| CONGRES SDIS 2025 | 156 |
| SUPERBIKE 2025 | 105 |
| 24H AUTOS 2026 | 88 |
| SUPERBIKE 2026 | 70 |
| BPL 2026 | 66 |
| RALLYE DE LA SARTHE 2026 | 20 |

> L'activité `SAISON` (hors-événement : gardiennage, rondes sûreté permanentes, maintenance) représente à elle seule ~12 400 fiches (49 %). C'est le **bruit de fond permanent** du site, à distinguer de l'activité de pic des grands événements.

---

## 2. Distribution par catégorie (champ `category`)

| Catégorie | Fiches | % |
|---|---:|---:|
| PCS.Surete | 13396 | 53.1% |
| PCS.Information | 3600 | 14.3% |
| PCO.Information | 2756 | 10.9% |
| PCO.Securite | 2263 | 9.0% |
| PCO.Technique | 1795 | 7.1% |
| PCO.Secours | 840 | 3.3% |
| PCO.MainCourante | 376 | 1.5% |
| PCO.Fourriere | 108 | 0.4% |
| PCO.Flux | 90 | 0.4% |

**Lecture :** plus de la moitié du volume est de la **sûreté** (`PCS.Surete`, rondes et contrôles de bâtiments). L'activité réellement « événementielle » (sécurité du public, secours, technique, flux) se concentre dans les catégories `PCO.*` (~32 % cumulé).

---

## 3. Motifs d'intervention récurrents — **livrable central**

Classification de chaque fiche dans une **famille de motif** par règles de mots-clés et n-grammes appliquées au texte libre (`text_full`) enrichi de la sous-classification et du motif structurés. **10 familles couvrent 90 % du volume.**

| # | Famille de motif | Fiches | % | % cumulé |
|---:|---|---:|---:|---:|
| 1 | Ronde / vérification sûreté | 7012 | 27.8% | 27.8% |
| 2 | Ouverture / fermeture (porte, portail, accès, cadenas) | 4369 | 17.3% | 45.1% |
| 3 | Prise / fin de service, relève agent | 4001 | 15.9% | 61.0% |
| 4 | AUTRES / non classé | 2525 | 10.0% | 71.0% |
| 5 | Nettoyage / prestation extérieure | 1677 | 6.6% | 77.6% |
| 6 | Secours à victime / malaise / blessé | 859 | 3.4% | 81.0% |
| 7 | Alarme / transmetteur / intrusion détectée | 649 | 2.6% | 83.6% |
| 8 | Technique - électricité / éclairage | 578 | 2.3% | 85.9% |
| 9 | Gêne circulation / véhicule gênant / stationnement | 541 | 2.1% | 88.1% |
| 10 | Gestion de flux / affluence / parking | 501 | 2.0% | 90.0% |
| 11 | Logistique / livraison / matériel | 439 | 1.7% | 91.8% |
| 12 | Vol / malveillance / dégradation | 429 | 1.7% | 93.5% |
| 13 | Technique - sanitaire / eau / plomberie | 321 | 1.3% | 94.8% |
| 14 | Accès / accréditation / autorisation | 278 | 1.1% | 95.9% |
| 15 | Clôture / barrière / grillage | 275 | 1.1% | 96.9% |
| 16 | Altercation / différend / litige | 184 | 0.7% | 97.7% |
| 17 | Technique - informatique / réseau / caméra | 157 | 0.6% | 98.3% |
| 18 | Fourrière / enlèvement / dépannage véhicule | 70 | 0.3% | 98.6% |
| 19 | Incendie / fumée / départ de feu | 62 | 0.2% | 98.8% |
| 20 | Individu / comportement suspect / SDF | 61 | 0.2% | 99.1% |
| 21 | Sans texte exploitable (code / vide) | 59 | 0.2% | 99.3% |
| 22 | Enfant / personne égarée | 47 | 0.2% | 99.5% |
| 23 | Filtrage / contrôle d'accès (agent en poste) | 39 | 0.2% | 99.6% |
| 24 | Rappel de consignes / point de situation | 29 | 0.1% | 99.8% |
| 25 | Bruit / nuisance sonore | 28 | 0.1% | 99.9% |
| 26 | Animal | 13 | 0.1% | 99.9% |
| 27 | Objet trouvé / perdu / abandonné | 11 | 0.0% | 100.0% |
| 28 | Stupéfiants / alcool | 7 | 0.0% | 100.0% |
| 29 | Météo / intempérie | 3 | 0.0% | 100.0% |

### Détail des principales familles (exemples réels + zones dominantes)

**Ronde / vérification sûreté** — 7012 fiches (27.8%)
  - _« Ronde Sureté Bt »_
  - _« Ronde Sureté Bt »_
  - _« Ronde Sureté Bt »_
  - Zones : _MC PCS (1429), Module Sportif (695), Siege (683)

**Ouverture / fermeture (porte, portail, accès, cadenas)** — 4369 fiches (17.3%)
  - _« Ouverture Siège Social »_
  - _« Acces TGBT Sté INEO »_
  - _« Cadenas Endommagé AA Karting Sud »_
  - Zones : _MC PCS (1181), _MC PCO (854), Acces (547)

**Prise / fin de service, relève agent** — 4001 fiches (15.9%)
  - _« Prise de service PS Nord »_
  - _« Prise + Fin de Service PS.Nord »_
  - _« Prise et Fin de Service PS Nord »_
  - Zones : _MC PCS (2985), Acces/Sud (396), Acces/Nord (259)

**Nettoyage / prestation extérieure** — 1677 fiches (6.6%)
  - _« Nettoyage Maison Blanche Sté Clean Performance »_
  - _« Nettoyage Musée Sté Ouest Nettoyage »_
  - _« Nettoyage PEC Sté Clean Performance »_
  - Zones : PEC (466), Musée auto (360), _MC PCS (251)

**Secours à victime / malaise / blessé** — 859 fiches (3.4%)
  - _« intervention Cms »_
  - _« centre medical spectateurs ouvert centre medical spectateurs ouvert est ouvert en astreint »_
  - _« blessure a la main »_
  - Zones : _MC PCO (138), _MC PCO/Interieur Dep./04-Zone Est (121), _MC PCO/Exterieur Dep./01-Zone Nord (115)

**Alarme / transmetteur / intrusion détectée** — 649 fiches (2.6%)
  - _« Alarme GALLAND »_
  - _« Alarme GALLAND »_
  - _« Mise hors service alarme intrusion »_
  - Zones : _MC PCS (201), _MC PCO/Interieur Dep./03-Zone Sud (58), _MC PCO/Interieur Dep./04-Zone Est (42)

**Technique - électricité / éclairage** — 578 fiches (2.3%)
  - _« Extinction Eclairage T13 »_
  - _« Mise en Service Eclairage Paddock »_
  - _« Mis en service éclairage piste »_
  - Zones : _MC PCS (168), _MC PCO (64), _MC PCO/Interieur Dep./04-Zone Est (57)

**Gêne circulation / véhicule gênant / stationnement** — 541 fiches (2.1%)
  - _« 3 véhicules stationnés devant les barrières de la zone de tri du Bleu Sud »_
  - _« Accès 2 Panorama bloqué par une caravane »_
  - _« poteaux genant »_
  - Zones : _MC PCO (104), _MC PCO/Interieur Dep./03-Zone Sud (89), _MC PCO/Interieur Dep./01-Grand Paddock (58)

**Gestion de flux / affluence / parking** — 501 fiches (2.0%)
  - _« parking M1 »_
  - _« Information pour changement de parking 16h40 - Le responsable sud souhaite savoir où peuve »_
  - _« Demande de gestion de flux au PC Nord Demande à patrouille 1 de se rendre au niveau du PCN »_
  - Zones : _MC PCO (196), _MC PCO/Exterieur Dep./01-Zone Nord (61), _MC PCS (43)

**Logistique / livraison / matériel** — 439 fiches (1.7%)
  - _« Panneau sortie véhicule »_
  - _« le bungalow du departement Nord Ouest à l'entrée Nord n'a pas d'eau »_
  - _« Laisser passer les livraisons au Paddock jusqu'à 10h00 Consigne passée à l'entrée des Padd »_
  - Zones : _MC PCS (69), _MC PCO (67), _MC PCO/Interieur Dep./04-Zone Est (50)

**Vol / malveillance / dégradation** — 429 fiches (1.7%)
  - _« Vol worker camp »_
  - _« Le personnel de sécurité S3M est arrivé sur place à BeauSéjour »_
  - _« Demande de renfort »_
  - Zones : _MC PCO/Exterieur Dep./01-Zone Nord (58), _MC PCO/Interieur Dep./02-Village (57), _MC PCO (51)

**Technique - sanitaire / eau / plomberie** — 321 fiches (1.3%)
  - _« SANITAIRE V3 »_
  - _« Remonté d eau »_
  - _« Plus d eau chaude tribune 17 »_
  - Zones : _MC PCO (42), _MC PCO/Interieur Dep./01-Grand Paddock (40), _MC PCO/Interieur Dep./04-Zone Est (37)

**Accès / accréditation / autorisation** — 278 fiches (1.1%)
  - _« centre ACCREDITATION »_
  - _« CENTRE ACCREDITATION »_
  - _« Arrivée de Commissaires pour Briefing avec Pass Montage/Démontage Facilité l'accès aux com »_
  - Zones : _MC PCO (147), _MC PCS (34), _MC PCO/Interieur Dep./03-Zone Sud (33)

**Clôture / barrière / grillage** — 275 fiches (1.1%)
  - _« Barrière accordéon »_
  - _« Livraison de 2 Barrières Accordéon pour l'entrée des Parcs Vu avec Tony CHEVALLIER, livrai »_
  - _« Barrière Panorama »_
  - Zones : _MC PCO (53), _MC PCS (31), _MC PCO/Exterieur Dep./01-Zone Nord (29)

> **Interprétation.** Le volume est massivement dominé par l'**activité de routine sûreté** : rondes (27,8 %), ouvertures/fermetures d'accès (17,3 %) et prises/fins de service (15,9 %) = **61 % à elles trois**. Les **incidents à valeur opérationnelle** (secours, alarmes, gêne circulation, vols/malveillance, pannes techniques, altercations) représentent l'essentiel du reste et constituent le vrai cœur de pilotage du PC.

---

## 4. Distribution par service destinataire (`service_contacte` enrichi)

Le champ structuré `content_category.service_contacte` n'est rempli que sur **3901 fiches (15,5 %)**. Par détection des services cités dans le texte libre et les commentaires, la couverture passe à **8775 fiches (34.8 %)**. _(Comptage multi-services possible par fiche : une fiche peut solliciter plusieurs services.)_

| Service sollicité | Mentions |
|---|---:|
| Patrouille sécurité / Tango | 2183 |
| Nettoyage / propreté | 1923 |
| Service ACO | 1919 |
| Service technique | 1754 |
| Secours médical (CMS/pompiers) | 1171 |
| SERI | 997 |
| Logistique | 494 |
| Service électricité | 455 |
| SSIAP | 428 |
| Forces de l'ordre | 176 |
| Direction de course | 129 |
| Autres | 93 |
| PCA | 12 |
| Promoteur | 11 |
| Appui Flux | 10 |

> Les patrouilles sécurité/Tango, la propreté (prestataires nettoyage), le Service ACO et le Service technique sont les destinataires les plus fréquents. Le **secours médical (CMS/pompiers)** apparaît sur ~1 170 fiches.

---

## 5. Charge temporelle

### Par heure de la journée (pics de charge)

| Heure | Fiches | |
|---:|---:|---|
| 00h | 921 | ███████████████████ |
| 01h | 789 | ████████████████ |
| 02h | 554 | ███████████ |
| 03h | 472 | ██████████ |
| 04h | 764 | ████████████████ |
| 05h | 1274 | ███████████████████████████ |
| 06h | 1776 | █████████████████████████████████████ |
| 07h | 1694 | ████████████████████████████████████ |
| 08h | 1079 | ███████████████████████ |
| 09h | 1036 | ██████████████████████ |
| 10h | 998 | █████████████████████ |
| 11h | 839 | █████████████████ |
| 12h | 632 | █████████████ |
| 13h | 680 | ██████████████ |
| 14h | 899 | ███████████████████ |
| 15h | 890 | ██████████████████ |
| 16h | 930 | ███████████████████ |
| 17h | 962 | ████████████████████ |
| 18h | 911 | ███████████████████ |
| 19h | 1096 | ███████████████████████ |
| 20h | 885 | ██████████████████ |
| 21h | 1594 | █████████████████████████████████ |
| 22h | 1876 | ████████████████████████████████████████ |
| 23h | 1633 | ██████████████████████████████████ |

**Deux pics de charge nets :**
- **06h–08h** (~1 700/h) : ouverture du site, prises de service, rondes matinales, mises en service éclairage.
- **21h–23h** (~1 600–1 900/h) : fermetures, rondes de nuit, mises sous alarme, fins de service.
- Creux marqué en **02h–04h**. Activité de jour relativement plate (~800–1 000/h) avec rebond de fin d'après-midi (17h–19h).

### Par jour de la semaine

| Jour | Fiches | % |
|---|---:|---:|
| Lundi | 2986 | 11.8% |
| Mardi | 2917 | 11.6% |
| Mercredi | 3238 | 12.8% |
| Jeudi | 3830 | 15.2% |
| Vendredi | 4378 | 17.4% |
| Samedi | 4429 | 17.6% |
| Dimanche | 3446 | 13.7% |

> Montée en charge **jeudi → samedi** (vendredi-samedi = 35 % du volume), cohérent avec le rythme des grands événements (épreuves le week-end).

### Journées les plus chargées

| Date | Fiches | Contexte probable |
|---|---:|---|
| 2024-06-15 | 231 | 24H Autos 2024 (course) |
| 2025-06-14 | 221 | 24H Autos 2025 (course) |
| 2025-10-04 | 218 | 24H Camions / GP Explorer 2025 |
| 2024-06-14 | 213 | 24H Autos 2024 (J-1) |
| 2025-06-13 | 200 | 24H Autos 2025 (essais) |
| 2025-10-05 | 196 | 24H Camions 2025 |
| 2025-06-12 | 191 | 24H Autos 2025 |
| 2025-04-19 | 181 | 24H Motos 2025 |
| 2025-05-10 | 181 | GPF 2025 |
| 2025-07-04 | 181 | Le Mans Classic 2025 |
| 2024-06-13 | 180 | 24H Autos 2024 |
| 2025-09-20 | 180 | événement sept. 2025 |
| 2024-05-11 | 178 | GPF 2024 |
| 2024-06-16 | 177 | 24H Autos 2024 (fin) |

---

## 6. Distribution par zone (`area.desc`)

| Zone | Fiches | % |
|---|---:|---:|
| _MC PCS | 7245 | 28.7% |
| _MC PCO | 2876 | 11.4% |
| Acces | 1349 | 5.3% |
| PEC | 1286 | 5.1% |
| Musée auto | 990 | 3.9% |
| Siege | 984 | 3.9% |
| _MC PCO/Interieur Dep./03-Zone Sud | 873 | 3.5% |
| _MC PCO/Interieur Dep./04-Zone Est | 790 | 3.1% |
| _MC PCO/Exterieur Dep./01-Zone Nord | 759 | 3.0% |
| TechnoParc | 751 | 3.0% |
| Module Sportif | 747 | 3.0% |
| _MC PCO/Interieur Dep./01-Grand Paddock | 570 | 2.3% |
| Acces/Sud | 495 | 2.0% |
| _MC PCO/Exterieur Dep./04-Zone Bleue | 482 | 1.9% |
| Musée éphemère | 477 | 1.9% |
| _MC PCO/Interieur Dep./02-Village | 462 | 1.8% |
| CIK | 387 | 1.5% |
| Maison Blanche | 385 | 1.5% |
| _MC PCO/Exterieur Dep./03-Zone Rouge | 360 | 1.4% |
| Acces/Nord | 305 | 1.2% |

> Les libellés `_MC PCS` / `_MC PCO` sont les **mains courantes génériques** (fiche non géolocalisée précisément). Les zones réellement localisées les plus actives : **Accès** (filtrage/contrôle), **PEC**, **Musée auto**, **Siège**, et le **carroyage** opérationnel (Zone Sud/Est/Nord, Grand Paddock, Village).

---

## 7. Délai de traitement (`close_ts − ts`)

Délai entre création et clôture de la fiche. **À interpréter avec prudence** : beaucoup de fiches (notamment *prises de service* et *rondes programmées*) sont ouvertes en début de poste et closes en fin de poste — le délai reflète alors la durée du poste, pas un temps de réaction. Médiane globale = **7,8 min**, mais p90 = ~13 h à cause de cette queue structurelle.

### Par catégorie

| Catégorie | n | Médiane (min) | p90 (min) |
|---|---:|---:|---:|
| PCS.Surete | 13396 | 1 | 405 |
| PCS.Information | 3600 | 687 | 1065 |
| PCO.Information | 2756 | 16 | 852 |
| PCO.Securite | 2263 | 60 | 994 |
| PCO.Technique | 1795 | 215 | 1771 |
| PCO.Secours | 840 | 28 | 249 |
| PCO.MainCourante | 376 | 15 | 1150 |
| PCO.Fourriere | 108 | 434 | 3092 |
| PCO.Flux | 89 | 64 | 1276 |

### Par famille de motif (top volume)

| Famille | n | Médiane (min) | p90 (min) |
|---|---:|---:|---:|
| Ronde / vérification sûreté | 7012 | 1 | 5 |
| Ouverture / fermeture (porte, portail, accès, cadenas) | 4369 | 5 | 849 |
| Prise / fin de service, relève agent | 4001 | 677 | 1034 |
| AUTRES / non classé | 2524 | 12 | 953 |
| Nettoyage / prestation extérieure | 1677 | 56 | 634 |
| Secours à victime / malaise / blessé | 859 | 30 | 280 |
| Alarme / transmetteur / intrusion détectée | 649 | 18 | 688 |
| Technique - électricité / éclairage | 578 | 218 | 1393 |
| Gêne circulation / véhicule gênant / stationnement | 541 | 67 | 1087 |
| Gestion de flux / affluence / parking | 501 | 59 | 1252 |
| Logistique / livraison / matériel | 439 | 167 | 1638 |
| Vol / malveillance / dégradation | 429 | 61 | 1649 |
| Technique - sanitaire / eau / plomberie | 321 | 330 | 2601 |
| Accès / accréditation / autorisation | 278 | 22 | 1494 |

**Lecture opérationnelle :**
- **Rondes sûreté : médiane 1 min** → fiches ouvertes/closes immédiatement (acte enregistré a posteriori), pas un délai de réaction.
- **Secours à victime : médiane ~30 min, p90 ~280 min** → cycle d'intervention médicale réel, queue maîtrisée (pas de fiche traînante).
- **Technique (électricité, sanitaire) : médianes élevées (200–330 min) et p90 très longs (>24 h)** → interventions de fond, résolution lente, fiches qui restent ouvertes plusieurs jours. **Point d'attention : suivi de la résolution technique.**
- **Prise/fin de service : médiane ~11 h** → confirme l'artefact « durée de poste ».

---

## 8. Synthèse — diagnostic opérationnel

1. **61 % du volume est de la routine sûreté** (rondes, ouvertures/fermetures, prises de service). Ce socle est compressible/automatisable : une partie pourrait être saisie de façon agrégée plutôt qu'à la fiche, pour désaturer la main courante.
2. **Les vrais incidents opérationnels** (secours 3,4 %, alarmes 2,6 %, gêne circulation 2,1 %, vols/malveillance 1,7 %, technique 5 % cumulé, altercations 0,7 %) sont noyés dans ce socle. Un **tag « incident » fiable** (le champ `is_incident` est `False` partout aujourd'hui) permettrait de les isoler.
3. **Deux pics de charge structurels : 06–08h et 21–23h**, à dimensionner en effectif PC. Le pic du soir est le plus intense.
4. **Montée en charge jeudi→samedi**, cohérente avec les grands événements ; le samedi est le jour le plus chargé.
5. **Qualité de données à améliorer** : `service_contacte` rempli à 15,5 %, `sous_classification` à 20 %, `niveau_urgence` à 5,8 %, `is_incident` inexploitable. La richesse opérationnelle est aujourd'hui surtout dans le **texte libre** — d'où l'intérêt d'un pré-remplissage assisté (classification auto à la saisie).
6. **Suivi technique défaillant sur la clôture** : les fiches techniques (élec/sanitaire) restent ouvertes très longtemps (p90 > 24 h). À instrumenter (relances de clôture).

---

_Rapport généré automatiquement à partir de `titan_dev.pcorg`. Méthode : découverte de schéma sans a priori, classification des motifs par règles mots-clés/n-grammes sur le texte libre, enrichissement du service destinataire par détection textuelle._