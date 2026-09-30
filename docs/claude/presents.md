<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Présents sur site (widget Contrôle d'accès, TV general-stats, montre)

**Un seul calcul, dans `presents_etat.py`** (sans Flask, comme `meteo_etat.py`) : `presents = current - correction - véhicules présents`. Le consomment `/api/live-controle/counters` (widget accueil), `/api/live-controle/dashboard` (TV `general-stats` et plein écran du widget), et la montre (`watch_state` pour `p`, `watch_peaks` pour `pk`, `watch_pages.build_frequentation` pour `pj`/`n1`).

⚠️ **Les véhicules se cumulent depuis la dernière remise à zéro du compteur, jamais depuis minuit.** Le `current` d'une Area HSH cumule depuis son dernier reset ; retirer seulement les véhicules du jour comptait comme des personnes tous ceux garés la veille (24H CAMIONS 2026 : 741 retirés pour 1 297 sur site). `counter_baseline()` repère le reset par une chute de `entries` dans `data_access` depuis `activation_timestamp` (une lecture fautive isolée est ignorée), à défaut l'activation. Même règle pour enfants et accrédités.

⚠️ **`hsh_transactions_agg.tranche` est en heure de Paris étiquetée UTC** (le champ Handshake `date_utc` porte l'heure locale), alors que `data_access.timestamp` est du vrai UTC. Toute comparaison passe par `to_tranche_label()`. Sans cette conversion, la TV ignorait les 2 dernières heures de mouvements de véhicules.

⚠️ **Le pic d'édition de la montre (`pk`) ne se calcule sur les présents que pendant que le live-contrôle compte l'édition** (les soldes véhicules des éditions closes ne sont plus en base). Le max brut de `current` remontait la valeur fantôme d'avant reset (22 447 le 22/09 pour quelques centaines de présents). Le cache `watch_peaks` mémorise `methode: "presents"` et ne laisse jamais un recalcul brut l'écraser après la fin de l'édition.

**⚠️ **Un pic = le plus haut RELEVÉ (toutes les 3 min), jamais le max de la courbe.** La courbe ne garde que le dernier relevé de chaque quart d'heure : un pic calculé dessus pouvait baisser en cours de quart d'heure (37 500 à 13h09 puis 37 449 à 13h12 le 27/09/2026) et afficher moins que le chiffre « présents » vu juste avant. `pic_presents` et `/dashboard` (`pic_today`, `pic_today_ts`, `days_summary`) balayent les relevés bruts.

Le N-1 vient partout de `presents_etat.historique_n1`** : TV (`/dashboard`), pic N-1 de `/get_affluence` (donc pic projeté), montre, rapport matinal. Série **au quart d'heure** (`scan_frequentation.enclosure_series`, document `complet`), la série horaire ratant le vrai pic (24H CAMIONS 2025 samedi : 51 889 à l'heure contre 52 520 au quart d'heure).

⚠️ **Un créneau 15 min scan_import est étiqueté à sa FIN** (« 20:00 » = passages de 19:45 à 20:00). Mesuré sur 24H MOTOS 2026, seule édition ayant import ET relevés temps réel : corrélation 0,996 avec [HH:MM−15, HH:MM], 0,971 avec [HH:MM, HH:MM+15]. La série horaire somme H:00 à H:45 : son point « 20:00 » est l'état à **20:45**. Lue telle quelle, la courbe N-1 avait 45 min d'avance ; une première correction à +15 min l'avait mise en retard (−2 400 personnes en pleine montée).

**Rapport matinal** (`pcorg_summary.compute_attendance_block`) : `pic_observed` = pic des présents du dashboard tant que le live-contrôle compte l'édition (repli sur l'ancienne chaîne historique → data_access → archive sinon), `pic_prev` et `pic_prev_hour` = série N-1 ci-dessus. Seul `pic_projection` reste calculé à part (méthode « croissance » seule, qui tombe sur la borne basse de la fourchette du dashboard).
