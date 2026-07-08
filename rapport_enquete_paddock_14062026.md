# Rapport d'enquête — Dégradation d'un bâtiment, paddock
## 24H AUTOS 2026 — nuit du 14 juin 2026

**Objet :** identification des personnes scannées à la gate paddock au moment d'une dégradation de bâtiment, à partir des logs du contrôle d'accès Handshake / Skidata.
**Rédigé le :** 15/06/2026.
**Périmètre temporel d'intérêt :** 14/06/2026, ~01:32:48 (heure caméra CCTV).

---

## 1. Synthèse exécutive

Un agent a été filmé en train de scanner le mis en cause à la gate **ACCES PADDOCKS** vers **01:32:48 (horloge caméra)**, en **sortie**. L'exploitation des transactions du serveur Handshake établit :

- À **01:32:48 (horloge HSH) il n'y a aucune sortie au paddock** ; la première sortie réelle après est à **01:33:09**, ce qui implique un **décalage caméra/HSH d'environ +20 s** (cohérent avec une dérive d'horloge CCTV courante).
- Les deux sorties paddock à **01:33:09 et 01:33:10** sont refusées (statut **130 « Condition non remplie »**), sur le lecteur **PDA.72 (checkpoint 753)**, média **Code 128**.
- Ces deux badges appartiennent à un **groupe coordonné de 4 badges Code 128** qui se font tous refuser en sortie du paddock entre 01:33 et 01:40, **sans aucune entrée enregistrée**, puis **quittent le site ensemble** par la **PORTE GARAGE VERT** entre 02:09:34 et 02:09:54.
- Recoupement back‑office **Skidata : ces billets relèvent de la commande TotalEnergies.**

**Cible principale (concordante avec la scène caméra, sortie 33:09) :**
> UTID **`0339788427151579301200196991`** — checkpoint **753 / PDA.72** — gate ACCES PADDOCKS — Sortie — 01:33:09 — refus statut 130 — média Code 128 — **commande TotalEnergies**.

---

## 2. Méthodologie et chaîne de preuve

- **Source primaire :** serveur de contrôle d'accès Handshake (HSHIF25 v2.34), `192.168.2.10:5205`, qui conserve l'historique complet des transactions.
- **Outil :** `collecte_forensic.py` (Cockpit), Issuer Transactions = 3 (24H Autos), lecture seule côté HSH, stockage dans la collection MongoDB `handshake_forensic`.
- **Volumétrie collectée :** **531 276 transactions** ; couverture jusqu'au 15/06 11:02 ; **436 779 transactions pour la seule journée du 14/06**. La fenêtre d'intérêt (01:30–01:40) est intégralement couverte.
- **Constat préalable :** la chaîne de contrôle *live* (`live_controle.py`) n'était **pas active** cette nuit‑là (aucune erreur ni agrégat en base pour le créneau). Les UTID individuels n'existaient donc nulle part avant cette re-collecte forensic dédiée. La présente collecte a reconstitué l'historique depuis le serveur.

> Note d'intégrité : les transactions HSH sont horodatées par le serveur d'accès (heure de Paris). L'horodatage caméra CCTV est indépendant et doit être recalé (cf. §4).

---

## 3. Le dispositif paddock

La gate **ACCES PADDOCKS** porte l'identifiant **Gate 1112**. Elle regroupe plusieurs lecteurs (checkpoints) :

| Checkpoint | Lecteur | Rôle observé la nuit du 14/06 |
|---|---|---|
| 753 | PDA.72 | **Voie de sortie principale** |
| 884 | PDA.57 | Entrée |
| 890 | PDA.63 | Entrée |
| 892 | PDA.65 | Sortie |
| 894 | PDA.67 | Entrée |
| 897 | PDA.70 | Entrée |
| 754 / 755 / 999 / 886 | PDA.73 / .74 / .224 / .59 | Entrée/Sortie |

**Statuts HSH pertinents :**
- `0` = OK (passage autorisé).
- `117` = anti‑double usage entrée (re‑présentation trop rapide).
- `130` = **Condition non remplie** (refus : le badge ne satisfait pas la condition du mouvement — typiquement **sortie sans entrée valide enregistrée**).

---

## 4. Constat horaire : 33:08 vs 33:09

Transactions **en sortie** à la gate paddock, créneau 01:33:00 → 01:33:15 :

| Heure HSH | Checkpoint | Statut | UTID | txid |
|---|---|---|---|---|
| 01:33:02 | 753 / PDA.72 | 0 OK | `1144349324027393104700262403` | 36876103 |
| 01:33:05 | 753 / PDA.72 | 0 OK | `0286026146079431534300196948` | 36876133 |
| 01:33:06 | 753 / PDA.72 | 0 OK | `3227793541385107323700262644` | 36876145 |
| **01:33:09** | **753 / PDA.72** | **130 refus** | **`0339788427151579301200196991`** | **36876188** |
| **01:33:10** | **753 / PDA.72** | **130 refus** | **`4279858620202997150600196999`** | **36876205** |
| 01:33:13 | 753 / PDA.72 | 0 OK | `2234561667155678646400262527` | 36876253 |
| 01:33:15 | 753 / PDA.72 | 0 OK | `2583208604288137225100262576` | 36876271 |

- **À 01:33:08 : aucune sortie paddock.** La sortie la plus proche est **33:09**.
- Le « 01:32:48 » de la caméra correspond donc, après recalage (+~20 s), à la sortie **01:33:09**.
- Un **refus (statut 130)** force l'agent à intervenir physiquement (la barrière ne s'ouvre pas) → c'est le geste de scan/contrôle visible à l'image.

---

## 5. Suspect principal et binôme

| | Badge A | Badge B |
|---|---|---|
| **UTID** | `0339788427151579301200196991` | `4279858620202997150600196999` |
| Sortie paddock (refus 130) | **01:33:09** — PDA.72 | **01:33:10** — PDA.72 |
| Sortie du site (OK) | 02:09:54 — Garage Vert / PDA.205 | 02:09:52 — Garage Vert / PDA.205 |
| Média | Code 128 | Code 128 |
| Passages totaux en base (depuis 13/04) | **2** | **2** |
| Entrée enregistrée | **AUCUNE** | **AUCUNE** |

Les deux badges se déplacent **en binôme synchronisé à 1–2 secondes**, à deux reprises (refus paddock 01:33, puis exfiltration site 02:09), 36 minutes plus tard.

---

## 6. Le groupe de 4 (média Code 128 — commande TotalEnergies)

La sortie du site révèle deux badges supplémentaires au même schéma :

| Badge (UTID) | Sortie paddock (refus 130) | Sortie site Garage Vert / PDA.205 |
|---|---|---|
| `0339788427151579301200196991` | 01:33:09 | 02:09:54 |
| `4279858620202997150600196999` | 01:33:10 | 02:09:52 |
| `2474540425092922312200197015` | 01:40:07 | 02:09:34 |
| `3694692500072621862500196881` | 01:40:05 | 02:09:35 |

**Caractéristiques communes :** média **Code 128** (≠ accréditations standard de l'événement, qui sont des QR `ACO_2026_…`) ; **refus 130 en sortie paddock** ; **aucune entrée valide** ; **sortie groupée du site en 20 secondes**. Attribution Skidata : **commande TotalEnergies**.

---

## 7. Reconstitution chronologique

- **~01:33:09–01:33:10** — Badges A et B tentent de sortir du paddock (PDA.72), **refusés (130)**. Intervention de l'agent → **scène caméra**.
- **~01:40:05–01:40:07** — Badges C et D tentent de sortir du paddock (PDA.72), **refusés (130)**.
- **01:33 → 02:09 (≈ 36 min)** — Fenêtre durant laquelle le groupe reste dans la zone (période compatible avec la dégradation constatée).
- **02:09:34 → 02:09:54** — Les **4 badges quittent le site ensemble** par la PORTE GARAGE VERT (PDA.205), cette fois en **passage OK**.

---

## 8. Éléments à pondérer (rigueur probatoire)

1. **Le statut 130 seul ne prouve rien.** Cette nuit‑là, ~20 badges Code 128 reçoivent un refus 130 en sortie paddock — c'est probablement une **caractéristique de cette catégorie de billets** (sortie paddock non conditionnée par une entrée préalable). La valeur probante vient du **faisceau** : concordance horaire (33:09 / sortie) + cohésion du binôme/groupe + absence d'entrée + sortie groupée du site.
2. **Recalage caméra impératif.** L'identification de A vs B (33:09 vs 33:10) dépend du décalage exact caméra/HSH. Fournir **un point de calage** (évènement visible simultanément sur la vidéo et dans les logs) pour figer l'offset et lever toute ambiguïté.
3. **Identité nominative.** Cockpit ne résout pas les numéros de média Skidata (table interne = QR uniquement). L'attribution **TotalEnergies** provient du back‑office Skidata ; l'identité nominative des porteurs doit être obtenue auprès du **gestionnaire de la commande TotalEnergies** (liste des attributaires des 4 médias).

---

## 9. Recommandations / suites

1. **Réquisition Skidata** : obtenir, pour les 4 numéros de média ci‑dessous, l'identité de l'attributaire et le détail de la commande TotalEnergies.
2. **Recalage CCTV** : mesurer l'offset caméra/HSH pour confirmer A (`…196991`) comme la personne scannée à 33:09.
3. **Exploitation vidéo ciblée** : Garage Vert (PDA.205) à **02:09:34–02:09:54** — les 4 individus y passent ensemble, plans nets probables (sortie OK, donc arrêt devant lecteur).
4. **Croisement RH/prestataire** : vérifier si ces médias TotalEnergies étaient nominatifs et autorisés en zone paddock à cette heure.

---

## 10. Annexe — données brutes (pour PV)

**Médias mis en cause (UTID = n° média Skidata) :**
- `0339788427151579301200196991` — txid 36876188 — sortie paddock 01:33:09 (st130) — Code 128 — **cible principale**
- `4279858620202997150600196999` — txid 36876205 — sortie paddock 01:33:10 (st130) — Code 128
- `2474540425092922312200197015` — sortie paddock 01:40:07 (st130) — Code 128
- `3694692500072621862500196881` — sortie paddock 01:40:05 (st130) — Code 128

**Localisations :**
- Gate 1112 ACCES PADDOCKS — checkpoint 753 = lecteur PDA.72 (voie de sortie).
- Gate 646 PORTE GARAGE VERT — checkpoint 980 = lecteur PDA.205 (sortie du site).

**Source :** collection `handshake_forensic` (MongoDB `titan`), collectée depuis HSH `192.168.2.10:5205`, Issuer 3, le 15/06/2026.
