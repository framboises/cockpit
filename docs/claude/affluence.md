<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Affluence prévisionnelle (mini widget + grand panneau)

Deux blocs de `index.html` lisent **la même route**, `/get_affluence` : le mini widget `widget-right-2` (onglets Affluence / Ventes / Sites, `static/js/affluence.js`) et le grand panneau « Analyse affluence » (`static/js/affluence_panel.js`), qui ajoute `/get_affluence_hourly` (courbe horaire des présents N vs N-1) et `/get_affluence_curves` (courbes de remplissage N / N-1 / N-2). Ils ne peuvent donc pas se contredire entre eux.

Tout part de `parametrages` : `tickets.products[*].ventes` (valeur courante) et `tickets.products[*].history[]` (snapshots hebdomadaires `{date, ventes}`). `tickets.lastUpdate` date le dernier import billetterie.

### Le panier : `globalHoraires.ticketing`, rien d'autre

**Le seul périmètre comparable d'une édition à l'autre est l'ensemble des produits référencés dans `globalHoraires.ticketing`** — les titres d'entrée enceinte générale, affectés à un jour public (`days: ["2026-09-27"]`) ou à tous (`days: "all"` pour un week-end). `_ticketing_product_names()` le construit ; toutes les sommes, la courbe de remplissage et la projection s'y restreignent.

⚠️ **Le référentiel billetterie contient des agrégats synthétiques qui double-comptent** : sur 24H CAMIONS 2025, `24C TOTAL_ENTREES`, `24C TOTAL_AA` et `24C TOTAL_PARKING` répliquent les produits de détail. Sommer tous les produits d'un doc donnait un final de 120 390 pour 52 117 titres réels. S'y ajoutent les campings et parkings, qui **saturent dès le printemps** (`AA Houx` à 99,5 % à J-92 quand les entrées sont à 23 %) : les mélanger aux entrées gonfle le taux de remplissage et écrase la projection.

⚠️ **Les noms de produits changent d'une édition à l'autre** (`24C WEEK_END` → `24C Entrée Week-end  - Course`, avec double espace). Aucun rapprochement par nom entre éditions n'est possible ni nécessaire : chaque édition a son propre panier, on ne compare que les **sommes**.

### `ventes_prev` vs `ventes_prev_final` — ne jamais confondre

C'est le piège central de ce bloc. Deux valeurs N-1 coexistent dans la réponse JSON, par jour, en total et par site :

| Champ | Sens | Comparer à |
|---|---|---|
| `ventes_prev` | N-1 **au même avancement** : la courbe N-1 interpolée au même nombre de jours avant course que N à sa dernière maj | les **ventes en cours** N |
| `ventes_prev_final` | N-1 **au soir de la course** | les **projections** (une projection est un total de fin de saison) |

Historiquement un seul champ existait, `ventes_prev`, qui portait le **final** — mais il était affiché sous le libellé « Vendus N-1 (même avancement) » et comparé aux ventes en cours. En pleine saison, ça affichait un effondrement de **-78 %** (16 172 contre les 52 117 titres finaux de 2025) là où l'édition était en réalité **+1 %** au même stade. L'alerte « Repli billetterie » du grand panneau et toutes les pastilles rouges par jour en découlaient.

Le ratio pic/ventes (`pic_prev / ventes_prev_final`) qui convertit une projection de ventes en pic de présents **doit** rester sur le final : le rapporter au N-1 de mi-saison multiplierait le pic projeté par trois.

`prev_reference_date` et `days_before` sont exposés pour que l'UI nomme la date de référence — rien ne la laisse deviner, ce n'est pas la même date calendaire mais le même J-*x*.

### Alignement au jour de course

Un jour public N est rapproché du jour public N-1 de **même offset à la course**, jamais de la même date calendaire. La référence N-1 est `prev_hist_race_date or prev_race_date` : **`historique_controle` prime sur `parametrages`**, plus fiable.

`_param_race_date()` lit `data.race` → `globalHoraires.race` → 1er jour public. Le repli compte : sur les trois éditions 24H CAMIONS, `data.race` est absent et seul `globalHoraires.race` est renseigné — sans repli, toutes les courbes sortaient vides et le graphe affichait « Pas d'historique de courbes disponible ».

⚠️ **`data.race` (naïf Paris) et `globalHoraires.race` (UTC, avec `Z`) ne désignent pas toujours le même instant** : sur 24H MOTOS 2025, `globalHoraires.race` porte l'**arrivée** (dim 20/04 15h) et `data.race` le **départ** (sam 19/04 15h). La convention Cockpit pour les Motos est le **départ, samedi 15h** — c'est ce que portent 2022, 2023, 2024, 2026 et `historique_controle{portes}` 2025. `data.race` valait `2025-04-14` (un lundi, faux) et décalait la courbe 2025 de 5 jours ; corrigé en base en août 2026.

### Projection

**Une projection par méthode, pas une méthode unique.** Chaque édition de référence (jusqu'à `MAX_REFERENCE_EDITIONS = 3`) fournit sa propre projection, à laquelle s'ajoute une projection « croissance » :

- **par édition** : `ventes_N_du_groupe / taux_de_remplissage_de_cette_édition`, sommé sur les groupes de jours (voir plus bas). Le taux est interpolé linéairement entre les deux snapshots encadrants — les relevés sont hebdomadaires avec des trous d'un mois.
- **croissance** : `ventes_prev_final × (ventes_N / ventes_prev)`, soit le final N-1 multiplié par la croissance constatée à avancement égal. Indépendante de la forme des courbes.

`total_projection` est la **moyenne pondérée des projections par édition** (`_reference_weights` : poids géométriques, N-1 pèse le double de N-2). `total_projection_low` / `_high` sont le **min/max de toutes les méthodes**, croissance comprise. `projection_spread_pct` mesure leur écart relatif.

⚠️ **La fourchette n'est pas un intervalle de confiance mais l'étendue du désaccord entre méthodes.** C'est précisément l'information utile : sur 24H CAMIONS 2026 à J-50, 2025 projette 52 632 et 2024 67 543 — 27 % d'écart pour des finals quasi identiques (53 149 et 52 117), parce que 2024 vendait beaucoup plus tard. Moyenner sans montrer cet écart affichait une fausse précision. Le grand panneau lève une alerte au-delà de 20 %, et une autre quand il n'y a qu'une seule édition de référence.

**Projection par groupe de jours, pas globale.** Un taux unique appliqué à tout le panier suppose que le mix produits de N est celui de N-1. Vrai sur le total, faux jour par jour : à J-50 sur 24H CAMIONS, le Pack VIP est écoulé à 66-81 % quand le billet Samedi en est à 14-19 %. `_ticketing_day_groups()` regroupe donc les produits par **portée de jours exprimée en offsets à la course** (`'all'`, `(0,)`, `(0, 1)`…) — signature stable d'une édition à l'autre, contrairement aux noms. Le passage au calcul par groupe a fait baisser la projection du dimanche 24H CAMIONS de 52 834 à 48 603 (−8 %) et son pic projeté de 45 683 à 42 025.

`projection_ratio` (= `total_ventes / total_projection`) ne survit que comme repli pour les sites sans historique.

⚠️ **Chaque site (parking/camping) est projeté sur SA propre courbe N-1**, pas sur le ratio des entrées. `EPINETTES` (792 vendus, final 2025 = 771) sortait à 2 906 — une jauge déjà pleine quadruplée, qui déclenchait les alertes de dépassement de capacité du grand panneau. Repli sur le ratio global si le site n'a pas d'historique N-1 exploitable.

⚠️ **Un panier `ticketing` vide écarte l'édition des références.** `_select_products` distingue `None` (pas de filtre) d'un ensemble **vide** (aucune config ticketing). 24H AUTOS 2024 est dans ce cas : 0 entrée ticketing pour 106 produits. Retomber sur tous ses produits lui ferait produire un taux de remplissage mêlant campings et agrégats — mieux vaut la perdre comme référence, ce que l'alerte de solidité signale.

⚠️ **Les sites se rapprochent par NOM entre éditions** (`prev_site_products`), et les noms bougent : `HERONNIERE` (2026) vs `HERRONIERE` (2025) ne matchent pas, `BEAUSEJOUR 1` porte le ticketing en 2026 mais c'est `BEAUSEJOUR 2` en 2025. Ces sites-là n'ont pas de N-1 et retombent sur le ratio global.

### Autres consommateurs

⚠️ **SAISON** : ses jours publics sont des jours de visites libres sans billetterie (cf. `main-courante.md`). `/get_affluence` et `/api/live-controle/counters-context` court-circuitent SAISON par son nom (réponse vide), `compute_attendance_block` ne rend aucun slot.

`/api/live-controle/counters-context` (widget compteurs) recalcule le même `projection_ratio` avec les mêmes helpers — le garder aligné. `pcorg_summary.compute_attendance_block` calcule son propre bloc Billetterie & Fréquentation, indépendant, et n'utilise le N-1 que pour des ratios de pic (pas de comparaison de ventes) : il n'était pas affecté.

### Pièges

- **La requête du parametrages N-1 est projetée.** Elle doit inclure `data.parkingsHoraires` / `data.campingsHoraires`, sinon le bloc Sites n'a jamais de N-1 — c'était le cas et personne ne l'avait vu, la colonne restant simplement vide.
- **`day_ventes` est multi-compté** (un billet week-end compte sur chaque jour) pour refléter la présence attendue ; les totaux utilisent l'**ensemble unique** des produits et ne doivent surtout pas agréger les jours.
- **`days_before` peut être négatif** quand le dernier import billetterie est postérieur au départ (normal pendant l'événement). L'interpolation borne alors sur le dernier point, donc toutes les méthodes convergent vers les ventes actuelles et la dispersion tombe à 0 % — c'est correct, pas un bug.
- **Restreindre le panier change la projection.** Sur 24H CAMIONS 2026 à J-92 : 24,6 % de remplissage tous produits contre 23,2 % sur les seules entrées, soit 47 496 contre 50 360 de projection.
- **La valeur affichée est la centrale, la fourchette est en infobulle** (`projectionTitle()`, présent dans les deux JS), sauf sur la carte KPI « Projection finale » du grand panneau où elle occupe la sous-ligne. Les pastilles de delta comparent la **centrale** au final N-1, jamais le milieu de la fourchette.
