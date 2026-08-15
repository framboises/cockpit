# Montre Cockpit — quatre pages opérationnelles

Spec de conception. Ajoute à l'app Connect IQ `garmin/cockpit-watch/` quatre
pages alimentées par des sources déjà exposées par le cockpit : main courante,
trafic Waze, météo, fréquentation.

Statut : validée en séance (sections 1 à 3), à implémenter.

---

## 1. Objectif

Le porteur de la montre est le Directeur des Opérations Adjoint. Il consulte
au poignet, en marchant, ce qu'il lit sinon sur les murs du PC Organisation.
La montre doit répondre sans être sollicitée à quatre questions :

- **Qu'y a-t-il en instance ?** (main courante)
- **Le trafic est-il pris ?** (Waze)
- **Va-t-il pleuvoir, et faut-il agir ?** (météo)
- **Où en est la fréquentation ?** (statistiques)

Aucune de ces données n'est à recalculer : les quatre murs du cockpit ont déjà
leurs agrégateurs. Le travail consiste à **condenser**, pas à produire.

## 2. Point de départ

L'app existe et tourne. Depuis le lot « pics par édition »
(`garmin/brief-pics-editions.md`, 5 étapes livrées) elle porte :

- deux pages — tableau de bord et liste d'alertes — en cycle HAUT/BAS ;
- une vue « pics par édition » ouverte par MENU ;
- un champ `m` valant `live` ou `past` qui dit s'il y a un événement en cours ;
- les champs `pk`/`pkt`, pic de présents de l'édition rapportée et son instant.

Budgets mesurés sur `fenix8solar51mm` : app **1,5 %** de 786 432 o, glance
**11 %** de 65 536 o, service de fond **10 %**. Les quatre pages sont
exclusivement dans l'espace app ; glance et fond ne les voient pas.

## 3. Inventaire des pages et navigation

Six pages en cycle, dans l'ordre d'urgence opérationnelle :

| # | Page | Rôle |
|---|---|---|
| 1 | Tableau de bord | le coup d'œil de 2 s |
| 2 | Alertes | liste complète |
| 3 | Main courante | ce qui est en instance |
| 4 | Trafic | itinéraires chargés, alertes Waze |
| 5 | Météo | pluie à venir, vent, consigne |
| 6 | Fréquentation | pics et comparaison N-1 |

**Deux chemins d'accès**, parce que l'usage est double — coup d'œil la plupart
du temps, consultation quand quelque chose interpelle :

| Geste | Effet |
|---|---|
| HAUT / BAS | page suivante / précédente, en cycle sur les six |
| **MENU** | menu de saut : les six pages **plus** « Pics par édition » |
| ENTER | rafraîchissement immédiat |
| BACK | quitte l'app (comportement système) |

MENU ouvrait directement la vue éditions ; il ouvre désormais le menu de saut,
dont les éditions deviennent la dernière entrée. La vue elle-même ne change
pas.

**Deux voyants ajoutés au tableau de bord.** La page 1 porte déjà le chiffre
héros, le WBGT et les alertes. Il lui manque exactement deux réponses : *y
a-t-il quelque chose en instance* et *le trafic est-il pris*. Une ligne unique
sous le WBGT, du type `MC 3   TRAFIC saturé`, colorée par gravité — **absente
quand tout est calme**. L'absence dit « rien à signaler » mieux qu'une ligne de
zéros, et n'encombre pas une page qui doit se lire en deux secondes.

## 4. Le chiffre héros du tableau de bord

Aujourd'hui la page 1 affiche `e`, le **cumul d'entrées depuis le début**. Ce
n'est pas la grandeur qui gouverne une décision d'exploitation : le cumul ne
redescend jamais, il annonce 130 000 à 23 h quand il reste 40 000 personnes.

Le chiffre héros devient donc :

| Mode | Héros | Sous-ligne | Pied |
|---|---|---|---|
| `live` | **présents** (`p`) | `48 213 entrées · 3 200 pers/h` | âge du compteur |
| `past` | pic de l'édition (`pk`) | `pic dim. 10 mai 14h04` | motif (ci-dessous) |

Les deux répondent à *combien de personnes* — l'une maintenant, l'autre au
maximum. Le cumul et le débit descendent en sous-ligne, ils ne disparaissent
pas.

**Les présents ne coûtent aucune lecture supplémentaire.** Le champ `current`
du compteur ENCEINTE GENERALE les porte, dans le document que
`watch_state.read_counter` charge déjà. Repli sur `entries - exits` si
`current` manque.

**La montre n'affiche JAMAIS un nombre de présents hors mode `live`.** `p` est
`null` par construction dès qu'il n'y a pas de relevé frais : c'est déjà ce
que garantissent les deux gardes posées le 14/08. La page ne remplit pas ce
vide avec la dernière valeur connue — un chiffre de présents périmé est
indiscernable d'un chiffre juste, et c'est précisément lui qu'on regarde pour
décider.

### 4.1 Nommer la cause, pas seulement l'état

`m: "past"` recouvre aujourd'hui **deux situations très différentes** :

| Cause | Ce que ça veut dire | Gravité |
|---|---|---|
| `live_controle_actif` est faux | le live-contrôle est arrêté sur le cockpit, hors événement ou volontairement | normal |
| le drapeau est vrai mais aucun relevé récent | **le collecteur est planté alors qu'on le croit en marche** | incident |

Les confondre serait perdre l'information la plus utile : la seconde est une
panne à traiter, la première est le fonctionnement normal 350 jours par an.

Le payload gagne donc `mr` (motif), valant `"inactif"` ou `"sans_releve"`, et
le pied de la page 1 écrit :

| `mr` | Pied | Couleur |
|---|---|---|
| `inactif` | `live inactif` | gris |
| `sans_releve` | `aucun releve` | **rouge** |

C'est le prolongement direct de la correction du 14/08 : nommer ce qui
manque vaut mieux qu'un libellé générique qui paraît tout couvrir.

## 5. Contenu des quatre pages

Maquettes (écran rond 454 × 454, mesures de mise en page à faire au moment de
l'implémentation avec la sonde décrite en §11) :

```
   MAIN COURANTE           TRAFIC                 MÉTÉO             FRÉQUENTATION

    🏥  2 (14)          ▛ TENSION ▟             21 °C          Pic jour  52 100
    🛡  0 (3)        Ouest    ↓ 18 saturé   vent 18 raf. 34         à 14h15
    🔧  1 (8)        Houx     ↑ 12 chargé  ───────────────
    ⇄  0 (5)        Panorama ↓  9 normal   Pluie dans 25 min   N-1  49 800
       + 1 (12)      ────────────────        2,4 mm/h au pic         +4,6 %
                      3 alertes · 0 accident
   il y a 40 s          il y a 1 min       Rafales 62 —          Pic édition
                                        sécuriser les structures     50 690
```

### 5.1 Main courante

Les compteurs **en instance** (fiches non clôturées), pris sur
`/api/pcorg/stats` — agrégation par `category` sur `^PCO`, `status_code == 10`
signifiant clôturée.

**Des icônes, pas des mots.** Chaque ligne porte l'icône de la catégorie, le
nombre **en cours**, et le nombre **terminées entre parenthèses** :

```
   🏥  2 (14)        local_hospital   #dc2626   PCO.Secours
   🛡  0 (3)         shield           #ef4444   PCO.Securite
   🔧  1 (8)         build            #f59e0b   PCO.Technique
   ⇄  0 (5)         swap_calls       #0d9488   PCO.Flux
        + 1 (12)     — les trois autres catégories
```

Les icônes sont **celles du cockpit** (`CATEGORY_STYLES` dans `pcorg.js`), pour
que le poignet et l'écran parlent la même langue visuelle. Rendues en SVG dans
`resources/drawables/` — le projet en a déjà un (`launcher_icon.svg`), le
compilateur les rastérise.

⚠️ **Secours `#dc2626` et Sécurité `#ef4444` sont deux rouges quasi
identiques.** Sur un cadran de 454 px, la couleur ne les distingue pas : c'est
la **forme** de l'icône qui porte l'identité. Ne jamais compter sur la teinte
seule pour ces deux-là.

Les trois autres catégories existantes — `PCO.Information`,
`PCO.MainCourante`, `PCO.Fourriere` — sont **repliées sur une ligne
« + N (M) »**, jamais omises : les compter à zéro serait faux, et les taire
ferait disparaître des fiches réelles.

Le compte de fiches terminées vient du même appel : `/api/pcorg/stats` rend
déjà `{open, closed}` par catégorie.

⚠️ **Le widget cockpit filtre les catégories par utilisateur**
(`window.__userAllowedCategories`). Les jetons montre (`watch_tokens`) n'ont
pas cette notion — ils portent un `label` et un `created_by`, rien de plus.
**Un jeton montre donne donc une vue non filtrée de la main courante.** C'est
le comportement voulu pour le porteur actuel, mais cela devient une règle
d'émission : ne délivrer un jeton montre qu'à quelqu'un habilité à toutes les
catégories.

### 5.2 Trafic

Source : l'agrégation par terrain et direction de `traffic.py`
(`/trafic/waiting_data_structured`), qui rend déjà `terrain`, `direction`
(`in`/`out`/`null`), `currentTime`, `historicTime`, `ratio`, `status`,
`severity` (0 à 4), triés par ratio décroissant.

Les quatre terrains les plus chargés, **triés par gravité décroissante et non
par ordre alphabétique** : la montre montre d'abord ce qui coince. La flèche
donne le sens, entrée ou sortie.

⚠️ **`currentTime` est en SECONDES.** Vérifié sur la donnée réelle
(`waze_trafic` : `time=208` pour un trajet Héronnière, soit 3,5 min). La montre
affiche des minutes.

⚠️ **`classify_congestion` compare des secondes à des seuils en minutes quand
`historicTime` vaut 0** (`traffic.py`, branche de repli : `t < 15` → normal,
`t >= 60` → bouchon). Sur le chemin nominal le ratio annule les unités et le
résultat est juste ; sur le repli, tout trajet de plus d'une minute est
étiqueté « bouchon ». La montre affichera donc la sévérité **des seuls
terrains ayant un temps historique**, et rangera les autres sans étiquette de
gravité. C'est un défaut du cockpit signalé en §12, pas corrigé ici.

Les alertes Waze (`waze_alerts`, dictionnaires typés `ACCIDENT`, `JAM`,
`HAZARD`…) sont **comptées, pas listées** : sur un poignet, le nombre
déclenche l'action, le détail se lit sur le cockpit.

### 5.2.1 Le verdict global

Le mur trafic (`/circulation`) affiche un verdict global en quatre niveaux,
qui est **l'information la plus dense de toute la page**. La montre le reprend,
en haut de la page Trafic, coloré :

| `vd` | Mot | Couleur | Reco du mur |
|---|---|---|---|
| 0 | FLUIDE | vert | circulation normale |
| 1 | VIGILANCE | jaune | trafic plus dense, surveiller |
| 2 | TENSION | orange | ralentissements, anticiper une déviation |
| 3 | CRITIQUE | rouge | intervenir : fluidifier ou dévier |

⚠️ **La règle de calcul n'est pas celle qu'on devinerait, et elle doit être
reproduite à l'identique.** Le commentaire de `circulation.html` l'explique :
les **bouchons et dangers Waze ne pilotent PAS le verdict**. Seuls comptent les
axes surveillés et les accidents en zone — parce qu'un bouchon Waze peut se
trouver n'importe où dans le cercle, hors des itinéraires suivis, et faisait
diverger le verdict du panneau « Axes ».

```
vd = 3  si  accidents_en_zone > 0  OU  pire_severite >= 4   (bouchon sur un axe)
vd = 2  si  pire_severite == 3                              (axe saturé)
vd = 1  si  pire_severite == 2                              (axe chargé)
vd = 0  sinon
```

⚠️ **Le comptage d'alertes est géofencé** : cercle de `ZONE_RADIUS_KM = 3.5`
autour de `ZONE_CENTER = (47.93827259819777, 0.2229518934089374)`, distance
haversine. Sans ce filtre, la montre compterait des alertes du flux Waze
situées hors du périmètre et afficherait un verdict que le mur ne montre pas.

Le payload porte donc `vd` (verdict), `ac` (accidents en zone) et `z` (total
d'alertes en zone) — jamais des comptes bruts non géofencés.

### 5.3 Météo

Source : `/api/meteo/mur` (`meteo.py:mur()`), qui agrège déjà prévisions,
vigilance, radar et humidité des sols en une charge.

Trois strates, dans cet ordre de priorité :

1. **L'instant** — température, vent moyen, rafale. Le WBGT reste en page 1,
   il n'a pas à être dit deux fois.
2. **`prochaine_pluie`** — `dans_min`, `intensite_mmh`, `pic_mmh`. Le
   commentaire de `meteo.py` la nomme lui-même « LA question du jour J ».
3. **La consigne la plus grave** — `consignes[]`, déjà **rédigée en langage
   d'action** par le backend (« Rafales 62 km/h — sécuriser les structures »),
   avec son `niveau` (`critique` / `danger` / `vigilance`).

**Aucun seuil n'est réinventé côté montre.** Le backend décide, la montre
répète. C'est ce qui garantit que le mur et le poignet ne se contrediront
jamais.

Le niveau de vigilance Météo-France (`niveau_vigilance`) est porté séparément
pour colorer la page.

⚠️ Le mur expose `temperature_c`, `humidite_pct`, `vent_moyen_kmh`,
`vent_rafale_kmh`, `nebulosite_pct`, `pluie_mm`, plus `wbgt_c` et
`wbgt_consigne` par enrichissement. **Il n'expose pas de « ressenti »** — la
maquette initiale en montrait un, il n'existe pas. Le WBGT joue ce rôle et est
déjà transporté en page 1.

Humidité et nébulosité **ne sont pas transportées** : elles ne commandent
aucune décision au poignet, et le WBGT les intègre déjà.

### 5.4 Fréquentation

Ne duplique pas la page 1 : les présents et le débit y sont déjà.

| Affiché | Origine |
|---|---|
| Pic du jour + son heure | max de `current` sur la journée en cours, compteur principal |
| Pic N-1 au jour équivalent + delta % | même calcul sur l'édition précédente, aligné sur le jour de course |
| Pic de l'édition | `pk`/`pkt`, **déjà dans le payload**, coût nul |

L'alignement N-1 se fait **au décalage au jour de course, jamais à la date
calendaire** — c'est la convention de tout le cockpit.

**Le calcul passe par `pcorg_summary._max_current_in_snapshots(db, coll,
date_paris, location_id)`**, qui rend `(max, "HHhMM")` : exactement les deux
valeurs affichées. La date de course des deux éditions vient de
`watch_peaks.resolve_race_dt`, alias-aware et gardée sur l'année.

`compute_attendance_block` rend bien les mêmes champs (`pic_observed`,
`pic_observed_hour`, `pic_prev`, `delta_pct_vs_prev`) et serait tentant, mais
il est **écarté** pour deux raisons : il calcule en plus toute la billetterie
— ventes, projections, taux de remplissage — dont la montre n'affiche rien, et
il rend `None` quand l'événement n'a pas de configuration billetterie
publique, ce qui blanchirait une page qui n'a besoin que du compteur. Derrière
un cache de 20 s sollicité en permanence, ce travail inutile se paierait à
chaque cycle.

## 6. Modes `live` et `past`

Le champ `m` existe déjà. Il commande le comportement des quatre pages :

| Page | En `past` |
|---|---|
| Trafic | **reste vivante** — Waze tourne toute l'année |
| Météo | **reste vivante** — le mur météo ne s'arrête pas avec la course |
| Main courante | affiche « hors événement » |
| Fréquentation | affiche « hors événement » |

**Ces deux pages diront « hors événement », jamais des zéros.** Un `SECOURS 0`
hors saison se lit comme un calme opérationnel alors qu'il ne dit rien du
tout. C'est la même règle que celle appliquée au `null` qui ne se dessine pas
comme un zéro (§9).

**Le serveur ne construit pas ce qu'il ne servira pas** : en `past`, les blocs
`mc` et `st` ne sont pas calculés. Quatre lectures Mongo économisées à chaque
cycle, 350 jours par an.

## 7. Contrat de payload

Un seul appel, `GET /api/v1/watch/state`, inchangé par ailleurs (Bearer,
cache 20 s, 60 requêtes / 300 s).

**Pourquoi une seule requête et pas quatre.** Sur une montre, c'est le réveil
de la radio qui consomme, pas les octets transportés. Passer de 124 à ~800
octets dans la même requête ne se voit pas sur la batterie ; faire une requête
par page, si — une montre consultée vingt fois par jour ferait vingt réveils
de plus. Le cache serveur de 20 s borne le travail Mongo quel que soit le
nombre de montres.

```json
{
  "t": 1776971469, "n": "24HM 26", "m": "live", "mr": null,
  "p": 47320, "e": 48213, "er": 3200,
  "pk": 50690, "pkt": 1776517509,
  "w": 27.4, "wl": 1, "al": [],

  "mc": {"t": 1776971400,
         "s": [2, 14], "sc": [0, 3], "tq": [1, 8], "f": [0, 5], "o": [1, 12]},

  "tr": {"t": 1776971440, "vd": 2, "ac": 0, "z": 3,
         "r": [["Ouest", "i", 18, 3], ["Houx", "o", 12, 2]]},

  "me": {"t": 1776971400, "tc": 21.3, "v": 18, "rf": 34,
         "pl": 25, "pm": 2.4, "cl": 2,
         "cn": "Rafales 62 km/h - securiser les structures", "vg": 1},

  "st": {"t": 1776971400, "pj": 52100, "ph": "14h15", "n1": 49800}
}
```

| Clé | Sens |
|---|---|
| `p` | présents maintenant — **`null` hors mode `live`** |
| `mr` | motif du mode `past` : `"inactif"` ou `"sans_releve"` ; `null` en `live` |
| `mc.s` / `.sc` / `.tq` / `.f` / `.o` | secours, sécurité, technique, flux, autres — **`[en cours, terminées]`** |
| `tr.vd` | verdict global 0-3, règle du mur reproduite à l'identique (§5.2.1) |
| `tr.ac` | accidents dans le géofence de 3,5 km |
| `tr.z` | total d'alertes Waze dans le géofence |
| `tr.r[i]` | `[terrain, "i"/"o"/"-", minutes, sévérité 0-4]` |
| `me.pl` / `.pm` | pluie dans N minutes / intensité au pic (`null` si aucune attendue) |
| `me.cn` / `.cl` | consigne rédigée / son niveau 0-3 |
| `me.vg` | vigilance Météo-France 0-3 |
| `st.pj` / `.ph` | pic du jour et son heure |
| `st.n1` | pic N-1 au jour équivalent |

Estimation **≈ 800 octets**, contre 124 aujourd'hui — sous la moitié du
plafond de 2 Ko fixé au départ. La taille sera mesurée, pas estimée, et un
test la verrouillera.

**Un horodatage par bloc.** Un payload à quatre sources ne peut pas avoir une
seule date : Waze se rafraîchit à la minute, la main courante en continu, la
météo à l'heure. Chaque page date **sa** donnée. C'est directement la leçon du
défaut corrigé le 14/08 — un `t` unique laissait croire que tout l'écran avait
le même âge.

## 8. Architecture serveur

Nouveau module `watch_pages.py`, même discipline que `watch_state.py` et
`watch_peaks.py` : **fonctions pures, `db` toujours passé en argument, aucun
import Flask**, testable sans application.

| Fonction | Rend |
|---|---|
| `build_main_courante(db, event, year)` | bloc `mc` ou `None` |
| `build_trafic(db, now_utc)` | bloc `tr` ou `None` |
| `build_meteo(db, now)` | bloc `me` ou `None` |
| `build_frequentation(db, event, year, now_utc)` | bloc `st` ou `None` |

Assemblage dans `watch_state.build_state`, **par injection** comme
`watch_peaks` : `watch_pages` importera `watch_state` pour ses helpers, donc
l'inverse ferait un cycle. Paramètre `pages=None`, et `watch_state` reste
utilisable seul — un test le vérifiera, comme pour `peaks`.

**Chaque bloc dans son propre `try`.** Une source qui tombe met son bloc à
`null` — jamais un 500, jamais un payload perdu. Le serveur doit rester
capable de donner le WBGT quand Waze ne répond plus.

## 9. Absence de donnée

**`null` ne se dessine pas comme zéro.** `SECOURS 0` veut dire « rien en
cours » ; `SECOURS —` veut dire « je ne sais pas ». Les confondre ferait lire
un silence technique comme un calme opérationnel — c'est le même piège que le
« périmé » corrigé le 14/08, sous un autre visage.

Trois états distincts, trois rendus :

| État | Rendu |
|---|---|
| Donnée présente | la valeur |
| Bloc `null` (source en panne) | tirets, et la page nomme le bloc absent |
| Mode `past` (page sans objet) | « hors événement » |

## 10. Côté montre

- **`Api.mc`** — `toCacheDict` doit reporter les quatre blocs. Elle recopie
  champ par champ : tout champ oublié est perdu **en silence**, sans erreur ni
  trace. C'est exactement ce qui a failli arriver à `m`/`pk`/`pkt`.
- **Cache `Storage`** — **deux clés**. Le noyau (compteur, WBGT, alertes) reste
  dans la clé actuelle, lue par la glance et le service de fond ; les quatre
  blocs vont dans une seconde clé, lue par la seule app. Sans cette séparation
  la glance désérialiserait à chaque affichage des données de trafic qu'elle
  n'affiche jamais, sur un budget de 64 Ko.
- **Quatre vues** + le menu de saut, sur le modèle d'`EditionsView`.
- **`Mock.mc`** — les scénarios existants gagnent les quatre blocs, plus un
  scénario « source en panne » (blocs à `null`) que rien ne couvre aujourd'hui.
- **Aucun guillemet typographique**, règle du dépôt, Monkey C compris.

## 11. Tests

**Serveur** — un test par constructeur de bloc : valeur nominale, source
absente, source qui lève, mode `past`. Plus un test de taille du payload.

**Montre** — Run No Evil : `toCacheDict` conserve les quatre blocs ; chaque
vue se dessine sans lever dans ses trois états (donnée, `null`, `past`) ; la
mise en page ne déborde pas.

**Mise en page mesurée, pas estimée.** Une sonde jetable
(`Graphics.createBufferedBitmap`, `getTextWidthInPixels`, `getFontHeight`)
donne les largeurs réelles du device et la corde disponible à chaque `y`. Sur
un cadran rond, un texte qui tient au centre déborde en haut ou en bas — et
**c'est le sommet qui contraint un bloc de la moitié haute, la base pour un
bloc de la moitié basse**. Vérifier le mauvais bord a déjà laissé passer trois
chevauchements sur ce projet.

**Vérification par sabotage.** Chaque garde doit avoir un test qui tombe quand
on la retire. Un test qui passe autant avec le code correct qu'avec le code
fautif ne vaut rien — deux l'ont prouvé sur ce projet, dont un qui prétendait
vérifier qu'on ignore `requested_event` alors que tous ses relevés portaient
le même libellé.

## 12. Hors périmètre, à signaler

- **`classify_congestion` compare des secondes à des seuils en minutes** sur sa
  branche de repli (`historicTime <= 0`), étiquetant « bouchon » tout trajet de
  plus d'une minute (`traffic.py`).
- **`/api/live-controle/counters` (`app.py:7389`) lit `data_access` sans borne
  de fraîcheur ni d'événement**, comme `read_counter` avant correction. Il ne
  se voit pas parce que `controle_access.js:277` masque tout le widget quand
  `live_controle_actif` est faux — le cockpit protège l'affichage, pas la
  requête.
- **`pcorg_summary._load_race_dt` ignore les alias d'événement** : ses replis
  sur `historique_controle` interrogent le nom long alors que la collection y
  range `LMC` et `SBK` sous leur sigle.
- **Un jeton montre donne une vue non filtrée de la main courante** (§5.1).

## 13. Ce qui n'est pas fait

- Pas de détail des alertes Waze sur la montre — un compte suffit à décider,
  le détail se lit sur le cockpit.
- Pas de courbe ni d'historique : la montre dit l'état, pas la tendance.
- Pas d'action depuis la montre — lecture seule, comme le reste de l'app.
- Pas de réglage par page : les quatre pages sont toujours présentes, pas de
  personnalisation dans les Properties.
