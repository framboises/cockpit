<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Fréquentation depuis le contrôle d'accès live (`live_frequentation.py`)

Pour le RETEX et la vue Fréquentation du rapport de scans, l'**archive du contrôle d'accès live est la source primaire**, l'import Excel (`historique_controle`) le repli, **édition par édition** : `scan_frequentation.build_frequentation_block(..., source_priority=LIVE_FIRST)`. Le **défaut reste l'import seul** (`DEFAULT_SOURCE_PRIORITY`) : `presents_etat.historique_n1` (TV, montre, N-1 affluence) ne change pas de chiffre — vérifié à l'octet sur 10 éditions.

- **Rejeu exact du dashboard** sur `hsh_archive_compteurs_*` / `hsh_archive_tx_*` / `hsh_archive_structure_*` : `presents = current − correction − véhicules`, véhicules cumulés depuis la remise à zéro précédant chaque relevé (`presents_etat.detect_resets`, `vehicle_prefixes`, `solde_vehicules`, partagés avec le direct). Pic = plus haut RELEVÉ. Vérifié : 24H CAMIONS 2026 = 49 975 à 19h15 et 37 501 à 13h06, identiques au dashboard.
- ⚠️ **Les pics des rapports matinaux antérieurs à septembre 2026** (et des logs) sont le maximum BRUT du compteur, véhicules non retirés : +2 000 à +7 000 sur 24H AUTOS 2026. Le rejeu est la bonne référence.
- ⚠️ **Le nom d'archive ment** (suffixe = année du clic, autre édition dedans) : seule la FENÊTRE course −10 j / +3 j fait foi. `has_live_archive` exige des tranches tx dans cette fenêtre, donc toutes les éditions ≤ 2025 retombent sur l'import.
- ⚠️ **`___GLOBAL___` n'est pas archivé** par `hsh_archive_and_purge` : pour une édition archivée, Area 628, aucune correction, activation estimée (début de la série continue de relevés).
- Relevés antérieurs à une remise à zéro survenue avant la fin du jour de course = fantômes, écartés ; jours `measured: false` avec `unmeasured_reason` (`compteur_non_remis_a_zero`, `compteur_fige`, `aucune_mesure`).
- ⚠️ **Solde initial** : sans remise à zéro avant l'édition, tout est majoré (24H MOTOS 2026 : 8 916 au premier relevé, ~+7 300 vs import sur chaque pic). Signalé au modèle (`solde_initial_compteur`) et dans les réserves du RETEX.
- Créneaux 15 min étiquetés à leur FIN (convention de l'import). Entrées de l'enceinte = compteur (passages valides) moins véhicules tx ; portes = gates HSH rattachées à l'Area 628 (les tx comptent aussi les scans refusés).
- Sans relevé du compteur sur la période des scans (SUPERBIKE 2026) : repli `solde_scans` (solde des passages personnes).
- Les éditions live et import ne se comparent pas sur les entrées (`entries_comparable=False`, `mixed_sources`) ; la source de chaque édition est donnée au modèle et affichée dans l'annexe du RETEX.
- `hsh_archive_and_purge` copie aussi le `___GLOBAL___` (s'il désigne l'événement archivé) dans `hsh_archive_global_<tag>` (doc `_id: "global"`) ; le rejeu l'utilise si son activation tombe dans la fenêtre de l'édition. Rattrapé à la main pour 24H CAMIONS 2026 ; les archives antérieures n'en ont pas (estimation).
