# Rapport d'exploitation des fiches PC Organisation — base pour la refonte de la formation opérateurs

**Base :** `titan_dev` (MongoDB local) · **Collection principale :** `pcorg` · **Mode :** lecture seule
**Périmètre temporel :** 2024 → 2026 (3 saisons) · **Généré le :** 2026-07-08

> Ce rapport s'appuie sur la logique de lecture déjà codée dans l'application TITAN (routes `/api/pcorg/*` dans `app.py`, module analytique `analyse_ops.py`, rendu `static/js/pcorg.js`). Le mapping des champs, les libellés d'interface et les correspondances catégories/lieux/services ne sont **pas réinventés** : ils reprennent ceux de l'appli.

---

## Comment l'application lit la « main courante » (référence de lecture)

La main courante PC Orga est exposée dans l'interface sous **deux variantes**, qui lisent **les mêmes documents** via la même API :

| Variante | Élément UI | Source | Filtre |
|---|---|---|---|
| **Petite version** (widget) | `#widget-comms` « Main courante » — 3 onglets : *Synthèse* (dashboard), *En cours*, *Terminées* + boutons agrandir / nouvelle intervention | `GET /api/pcorg/live` + `GET /api/pcorg/stats` | `category` commençant par `^PCO` |
| **Grande version** (panneau élargi) | `#pcorg-expanded-panel` — filtres *Toutes / En cours / Terminées* + recherche plein texte, tableau complet (Statut, Catégorie, Description, Opérateur, Ouverture, Clôture) | idem `/api/pcorg/live` | idem `^PCO` |

Points structurants tirés du code :

- **Sérialisation** (`_pcorg_serialise`, `app.py`) : chaque fiche est aplatie en `{ts, close_ts, category, text, area_id/desc, operator, severity, is_incident, status_code, sous_classification, niveau_urgence, lat/lon, …}`. Les datetimes BSON naïfs sont forcés en UTC puis affichés en Europe/Paris.
- **Statut** (`pcorg.js`) : seule règle appliquée → `status_code === 10` ⇒ **TERMINÉ**, sinon **EN COURS**. C'est la seule sémantique de statut utilisée côté interface.
- **Urgence** (`niveau_urgence`) : échelle `EU / UA / UR / IMP` avec libellés **contextuels selon la catégorie** (`URGENCY_LABELS`, `app.py`) — ex. `EU` = « Détresse vitale » pour Secours, « Danger immédiat » pour Sécurité.
- **Classification fine** : `content_category.sous_classification` (ou `xml_struct.classification.sous`), avec listes de référence dans `pcorg_config`.
- **Module analytique** (`analyse_ops.py`, fonction canonique `_flatten_doc`) : calcule le **délai de traitement** `delay_min = close_ts − ts`, le **FCR** (même opérateur création/clôture ET clôture < 30 min), les **SLA** (10/30/60 min), la qualité de remplissage, les croisements. C'est la logique de référence pour interpréter durée et complétude. **Nuance importante :** ce module charge *tous* les documents d'un `event/year`, **PCS inclus**, alors que le widget main courante ne montre que les `PCO`.

---

## ÉTAPE 1 — Cartographie de la base

### 1.1 Collections `pcorg*`

| Collection | Docs | Rôle |
|---|---:|---|
| **`pcorg`** | **25 224** | **Source de vérité** — fiches d'intervention brutes (PC Orga *et* PC Sûreté). |
| `pcorg_summaries` | 11 | Comptes-rendus IA de période (Assistant IA, `pcorg_summary.py` → Claude). Dérivé, lecture seule. |
| `pcorg_n1_retros` | 6 | Rétrospectives « N‑1 » (édition précédente) servant de contexte comparatif à l'Assistant IA. |
| `pcorg_config` | 1 | Listes de référence de saisie (`sous_classifications`, `intervenants`, `services`, `urgence_categories`, `fiche_simplifiee`). |
| `pcorg_sync_config` | 1 | État de la synchro incrémentale (actif, last_run, last_success, last_error). |
| `pcorg_sync_cursor` | 1 | Curseur de synchro (`last_date_write`). |
| `pcorg_ai_memory` | 0 | Mémoire IA (vide à ce jour). |

### 1.2 Relation collection principale ↔ résumés IA

```
        (Prysm / Skidata + saisies Cockpit)
                    │  pcorg_sync.py (curseur date_write)
                    ▼
              ┌───────────┐
              │  pcorg    │  ← SOURCE DE VÉRITÉ (25 224 fiches)
              └───────────┘
                    │  lecture seule (jamais réécrit par l'IA)
        ┌───────────┼─────────────────┐
        ▼           ▼                 ▼
 pcorg_summaries  pcorg_n1_retros   pcorg_config
 (CR de période)  (compar. N‑1)     (référentiels saisie)
```

- Les collections de résumés sont des **artefacts dérivés** : elles n'alimentent jamais `pcorg` en retour. Chaque `pcorg_summaries` fige une **période** (`period_start/end`), ses `kpis`, et 9 `sections` narratives (synthèse, faits marquants, secours, sécurité, technique, flux, fourrière, recommandations, prochaines 24h).
- Pour la formation, **seule `pcorg` est la matière première**. Les `pcorg_summaries` peuvent servir d'**exemples de synthèses rédigées** (modèles de compte-rendu), et `pcorg_config` fournit la **taxonomie officielle** à enseigner.

### 1.3 Schéma de `pcorg` — champs clés (taux de remplissage sur 25 224 docs)

| Champ | Rempli | Type | Rôle (d'après le code) |
|---|---:|---|---|
| `_id` | 100 % | str (UUID5 déterministe, cf. `merge.py`) | Identifiant stable de la fiche |
| `event` | 100 % | str | Épreuve (11 valeurs) |
| `year` | 100 % | int | Saison (2024–2026) |
| `category` | 100 % | str | Type d'événement (`PCO.*` / `PCS.*`) |
| `ts` | 100 % | datetime | **Horodatage d'ouverture** (UTC) |
| `close_ts` | ~100 % | datetime | Horodatage de clôture → base du `delay_min` |
| `status_code` | 100 % | int | 10 = terminé ; {0,1,5,7} = en cours/autres |
| `text` / `text_full` | 99,5 % | str | **Description libre** de l'intervention |
| `comment` / `comment_history` | 100 % | str / list | **Journal des mises à jour** (opérateur + horodatage) |
| `operator` / `operator_close` | 100 % / 99,8 % | str | Opérateur création / clôture |
| `area.desc` | 99,5 % | str | **Lieu / zone** (arborescence circuit) |
| `group.desc` | 99,7 % | str | PC émetteur (PCO/PCS, Stade, Information…) |
| `content_category.sous_classification` | 19,8 % | str | **Classification fine** (voir référentiel) |
| `xml_struct.service_contacte` | 15,5 % | str | **Service concerné / contacté** |
| `xml_struct.caller.appelant` | 29,8 % | str | Appelant/origine |
| `xml_struct.flags.telephone` / `.radio` | ~22 % | bool | Canal d'alerte |
| `gps.coordinates` | 5,8 % | list | Géolocalisation (fiches Cockpit récentes) |
| `niveau_urgence` | 8,0 % | str | Échelle EU/UA/UR/IMP (surtout fiches Cockpit récentes) |
| `severity` | 100 % | int | **Quasi inexploité** : 0 dans 99,9 % des cas |
| `is_incident` | 100 % | bool | **Toujours `False`** dans l'historique |

> ⚠️ **Avertissement méthodologique décisif pour la formation.** Les marqueurs de gravité « théoriques » de l'appli (`severity`, `is_incident`) **ne sont pas peuplés** dans les données historiques (héritage Prysm), et `niveau_urgence` n'existe que sur les fiches créées dans Cockpit (récentes). **La gravité réelle d'un cas se lit donc dans le texte (`text_full`) et la `sous_classification`, pas dans les flags.** Toute sélection de cas doit s'appuyer sur le contenu, ce qui est la méthode retenue dans ce rapport.

### 1.4 Référentiels officiels (`pcorg_config` / code) — à enseigner tels quels

- **Catégories PCO** : Secours, Sécurité, Technique, Flux, Information, Main courante, Fourrière.
- **Sous-classifications Secours** : Secours à victime, Accident de circulation, Départ de feux, Incendie, Malaise.
- **Sous-classifications Sécurité** : Intrusion, Altercation‑Rixe, Vol, Gêne à la circulation, Acte de malveillance, Stationnement gênant, Colis/objet suspect, Enfant perdu, Dégradation, Agression, Fraude accréditation‑billet, Nuisances sonores, Drone non autorisé, Ivresse manifeste, Stupéfiants.
- **Sous-classifications Technique** : Logistique, Électricité, Sanitaire, Informatique, Barriérage, Signalétique, Clôture, Fluide, Contrôle Accès, Serrurerie, Portail‑Portillon.
- **Sous-classifications Flux** : Congestion véhicules/piétons, Renfort contrôle accès, Passage piéton à sécuriser, Voie secours encombrée, Balisage à poser, Régulation manuelle, Parking complet, Évacuation de foule.
- **Services** : CMS, SDIS 72, SAMU 72, Gendarmerie, Police municipale, DPS, PC Sécurité, PC Course, Direction technique/sécurité, Accueil, Billetterie.

---

## ÉTAPE 2 — Analyse quantitative

> **Deux périmètres coexistent dans `pcorg`.** Le total brut est de **25 224 fiches**, mais **16 996 (67 %)** sont des mains courantes **PC Sûreté** (`PCS.Surete`, `PCS.Information`), majoritairement issues de la saison courante et non du champ opérationnel du PC Orga. **Le périmètre pertinent pour la formation des opérateurs PC Orga est PCO.\*, soit 8 228 fiches.** Les tableaux ci-dessous distinguent les deux.

### 2.1 Volumétrie globale

| Périmètre | Fiches | Part |
|---|---:|---:|
| **PCO** (PC Organisation) | **8 228** | 32,6 % |
| PCS (PC Sûreté) | 16 996 | 67,4 % |
| **Total `pcorg`** | **25 224** | 100 % |

**Par année (PCO seul)** : 2024 → 2 690 · 2025 → 4 354 · 2026 → 1 184 (saison en cours).

### 2.2 Répartition par épreuve (périmètre PCO)

| Épreuve | Fiches PCO |
|---|---:|
| 24H AUTOS | 2 490 |
| GPF (Grand Prix de France Moto) | 1 750 |
| 24H MOTOS | 1 662 |
| 24H CAMIONS | 695 |
| GP EXPLORER | 669 |
| LE MANS CLASSIC | 551 |
| SAISON (activité hors épreuve) | 278 |
| SUPERBIKE | 92 |
| CONGRES SDIS | 37 |
| BPL / RALLYE SARTHE | 4 / — |

### 2.3 Typologie des interventions (périmètre PCO)

| Catégorie | Fiches | % PCO |
|---|---:|---:|
| **Information** | 2 756 | 33,5 % |
| **Sécurité** | 2 263 | 27,5 % |
| **Technique** | 1 795 | 21,8 % |
| **Secours** | 840 | 10,2 % |
| **Main courante** (divers) | 376 | 4,6 % |
| **Fourrière** | 108 | 1,3 % |
| **Flux** | 90 | 1,1 % |

> Enseignement : le cœur d'activité du PC Orga est **Information + Sécurité + Technique (83 %)**. Les cas à fort enjeu (Secours, gestion de foule) sont **rares mais critiques** — d'où l'intérêt d'une formation par cas.

### 2.4 Top sous-classifications (tous PCO/PCS confondus, top 15)

| Sous-classification | n |
|---|---:|
| Secours à victime | 812 |
| Logistique | 590 |
| Gêne circulation | 353 + 76 |
| Intrusion (dont zone réglementée) | 344 + 81 |
| Acte de malveillance | 316 |
| Électricité | 314 + 87 |
| Sanitaire | 271 |
| Non-respect règlement circuit | 266 + 87 |
| Informatique | 152 |
| Stationnement | 151 |
| Clôture | 112 |
| Altercation | 106 |
| Vol | 98 |
| Agression | 74 |
| Barriérage | 65 |

### 2.5 Répartition géographique (top zones, `area.desc`)

Après retrait des conteneurs de main courante génériques (`_MC PCS`, `_MC PCO`), les zones opérationnelles les plus sollicitées :

| Zone | n |
|---|---:|
| Accès (Sud/Nord) | 1 349 + 495 + 305 |
| PEC | 1 286 |
| Musée auto / éphémère | 990 + 477 |
| Siège | 984 |
| Zone Sud / Est / Nord (Int./Ext. Dép.) | 873 / 790 / 759 |
| TechnoParc | 751 |
| Module Sportif | 747 |
| Grand Paddock | 570 |
| Zone Bleue / Rouge | 482 / 360 |
| Village | 462 |
| CIK · Maison Blanche | 387 · 385 |

> Points chauds récurrents à intégrer aux cas : **Accès, PEC, Zones Sud/Est/Nord, Grand Paddock, Village, Maison Blanche**.

### 2.6 Répartition temporelle

**Par heure (Europe/Paris)** — deux pics nets :

- **Pic nocturne 21h–00h** (jusqu'à 1 876 fiches à 22h) : activité PC Sûreté + gestion de foule/soirées.
- **Pic matinal 05h–08h** (1 776 à 06h) : montée en charge, ouvertures, contrôle d'accès, logistique.
- Creux 02h–04h.

**Par jour de semaine** : montée progressive du mardi au week‑end — **vendredi (4 350) et samedi (4 416)** sont les jours critiques, dimanche (3 591) reste soutenu.

### 2.7 Répartition par service concerné (top, `service_contacte`)

| Service | n |
|---|---:|
| Service ACO | 1 915 |
| Service technique | 690 |
| Patrouille sécurité | 385 |
| Service Élec. | 229 |
| CMS | 206 |
| Tangos | 120 |
| SERI | 107 |
| PS Nord | 51 |

### 2.8 Croisements utiles (catégorie × heure de pointe)

| Catégorie | Heure de pic | Volume au pic |
|---|---|---:|
| Sécurité (PCO) | **15h** | 165 |
| Secours (PCO) | **17h** | 63 |
| Technique (PCO) | **09h** | 150 |
| Information (PCO) | **07h** | 238 |
| Main courante | 07h | 34 |
| Sûreté (PCS) | 22h | 1 636 |

### 2.9 Délais de traitement (fiches clôturées, `delay_min`)

- **Médiane : 8 min** · **P90 : ~13 h** · longue traîne (clôtures tardives/oublis).
- **< 10 min : 51,5 %** · < 30 min : 57,4 % · < 60 min : 61,3 %.

> Lecture : plus de la moitié des fiches sont clôturées quasi immédiatement (fiches d'information/traçabilité), mais **~40 % restent ouvertes > 1 h** — ce sont les cas à suivi, matière première des cas d'étude « traitement complexe ».

---

## ÉTAPE 3 — Cas pédagogiques

Sélection fondée sur le **contenu** (`text_full`, `sous_classification`) et la **richesse du suivi** (`comment_history`), puisque les flags de gravité ne sont pas fiables (cf. §1.3). Les fiches « test » et les boilerplates « générée en procédure d'urgence » ont été écartés.

### 3.A — Cas RARES mais CRITIQUES (gravité élevée)

| # | Thème | Épreuve / Année | Catégorie | Extrait | `_id` |
|---|---|---|---|---|---|
| 1 | Détresse vitale | 24H AUTOS 2024 | Secours | « **ARRÊT CARDIAQUE** » (suivi 6 maj, 366 min) | `b4574cc4-ea90-5dfe-9f0f-7812bd654c97` |
| 2 | Accident grave | 24H MOTOS 2026 | Sécurité | « **AGENT PERCUTÉ PAR MOTO** » (urg=IMP) | `ef3ed96b-7cbb-591e-84aa-3bfa527d4b66` |
| 3 | Incendie | 24H MOTOS 2026 | Secours | « Incendie AA Houx, **moto en feu** » | `6a1d3ba7-6bdd-5c44-b313-f4e60b1f25db` |
| 4 | Incendie | 24H AUTOS 2024 | Secours | « **feu de voiture** camping prairie » | `a4289786-e0ee-59d7-9c66-63e4557f2eda` |
| 5 | Violence / ordre public | GPF 2026 | Sécurité | « **Rixe** jeunes du quartier / public GP Moto, prise en charge pompiers, intervention PN » (urg=UA) | `21664832-1929-5ce6-888b-644a63942340` |
| 6 | Agression personnel | 24H MOTOS 2025 | Sécurité | « **Agression Personnel ACO** – Portail 1 du Houx » (suivi long, 2 214 min) | `477d2d68-8e0e-566b-be42-88f3bab57e1d` |
| 7 | Vol + agression | LE MANS CLASSIC 2025 | Sécurité | « **Vol + agression** dans la boutique » | `19f4ed37-9738-5bf2-9e55-24beeeb4467b` |
| 8 | Personne vulnérable | SUPERBIKE 2025 | Sécurité | « **Enfant perdu** au niveau du Village » | `c7bf5831-8ebd-50aa-90c8-e5d277f604a3` |
| 9 | Gestion de foule | 24H CAMIONS 2025 | Information | « P3/P1/P2 – **Évacuation de la T23** » (10 maj) | `cb8888e6-a367-5363-9bbe-dd99fec252c2` |
| 10 | Sécurité flux / forçage | GPF 2026 | Flux | « **Portail forcé par les voitures** car laissé ouvert pour les piétons » | `5388a1ef-c09c-5915-a66a-159066700be1` |

### 3.B — Cas EMBLÉMATIQUES (représentatifs des catégories fréquentes, bien documentés)

| # | Catégorie illustrée | Épreuve / Année | Extrait | `_id` |
|---|---|---|---|---|
| 11 | **Sécurité – Vol** | LE MANS CLASSIC 2025 | « Houx – Déclaration du **vol d'une golfette** Martini n°43 » (11 maj, 672 min) | `e4fcd7fe-f152-5002-a3ef-c44cec2be6d1` |
| 12 | **Secours – Malaise** | GP EXPLORER 2025 | « **Malaise** au Point info raccordement + perte de son véhicule » (9 maj) | `7c66cc5f-46a8-527e-a147-cf499c42b4b1` |
| 13 | **Technique – Logistique** | 24H MOTOS 2026 | « Demande de **nettoyage sièges plateforme PMR** T16 » (9 maj) | `eed45a32-ec03-5934-8926-5549e9243e80` |
| 14 | **Information – Coordination accès** | 24H AUTOS 2025 | « Camion poubelle BeauSéjour, coordination PS Nord / portail » (12 maj) | `a06847e6-368a-584f-8964-1c39e6b683fc` |
| 15 | **Flux – Congestion** | 24H CAMIONS 2025 | « Début de **densification du flux** Tertre → Antares » | `7baeef9c-9062-5caa-bfeb-979df6796b26` |
| 16 | **Fourrière** | 24H CAMIONS 2025 | « **remorque gênante** CP‑894‑QW derrière la T22 » (7 maj) | `d1e0a5a3-96c4-5f50-a598-056b4173bc5a` |

### 3.C — Traitements LONGS / COMPLEXES (cas d'étude de coordination)

| # | Sujet | Épreuve / Année | Suivi | `_id` |
|---|---|---|---|---|
| 17 | Panne **tripods / contrôle d'accès** Porte Est | 24H MOTOS 2025 | **17 maj**, 1 408 min | `560afbbc-0ac5-52dc-90a8-bcd175619101` |
| 18 | **Barre décrochée en haut de la tribune 11** (risque public) | GP EXPLORER 2025 | 14 maj, 955 min | `84b5412a-5284-5811-8a46-347af4ed67d5` |
| 19 | Coordination **HERAS portail 2 non retirés** bloquant sortie véhicules | GPF 2026 | multi‑services (Technique↔Flux) | `0e8a35f1-a190-5d24-b76c-cc4f86dd9bac` |
| 20 | **Délestage passerelle P2B** | LE MANS CLASSIC 2025 | 15 maj, 1 073 min | `f33b68ba-d74a-5b6d-933a-0a0c7517bab6` |

### 3.D — Incohérences / zones de flou détectées (matière « qualité de saisie »)

Ces éléments sont eux-mêmes des **supports pédagogiques** (ce qu'il ne faut pas faire) :

- **D1 — 12 fiches PCO jamais clôturées** (`status_code ≠ 10`). Plusieurs sont des saisies de test manifestes en SAISON 2024 (`"456"`, `"678"`, `"890"`…). ⇒ enseigner la clôture systématique et le nettoyage des tests.
- **D2 — Aucune clôture instantanée (0 min) sur fiche substantielle** : bon signe, la traçabilité temporelle tient.
- **D3 — 15 fiches Secours/Sécurité sans aucun texte** : perte d'information sur des catégories sensibles ⇒ insister sur la description minimale obligatoire.
- **D4 — 9 fiches Secours/Sécurité/Technique/Flux clôturées sans sous-classification** : faible mais à corriger (la classification conditionne toute l'analyse a posteriori).
- **Taux de remplissage de la sous-classification globalement bas (~20 %)** : c'est le **principal levier qualité** à travailler en formation — sans classification, l'exploitation statistique et le RETEX sont bridés.

---

## Synthèse pour la refonte de la formation

1. **Distinguer explicitement PCO et PCS** : la formation opérateurs PC Orga porte sur les **8 228 fiches PCO**, pas sur le volume brut. Le mélange dans la collection est une source d'erreur d'analyse.
2. **Enseigner par cas réels du site** : le corpus de 20 fiches ci-dessus (arrêt cardiaque, incendies, rixe, agression, enfant perdu, évacuation de tribune, pannes contrôle d'accès, forçage de portail…) couvre les 7 catégories et les zones chaudes réelles (Houx, Village, PEC, tribunes, portails).
3. **Cœur de métier = Information + Sécurité + Technique (83 %)** ; les cas graves sont rares → l'apprentissage par simulation de cas est justifié.
4. **Rythme opérationnel** à intégrer aux mises en situation : pics **05h–08h** et **21h–00h**, jours critiques **vendredi/samedi**.
5. **Priorité qualité** : la **sous-classification** et la **description texte** sont les maillons faibles (remplissage ~20 %, quelques fiches vides). En faire un objectif pédagogique explicite améliorera mécaniquement le RETEX des prochaines éditions.
6. Réutiliser `pcorg_config` comme **taxonomie officielle** enseignée, et les `pcorg_summaries` comme **modèles de compte-rendu de période**.

*Aucune donnée n'a été modifiée (lecture seule).*
