# Plans de circulation : à intégrer dans Cockpit (note du 29/10/2026)

Décision du 29/10/2026 : les **plans de circulation** sont retirés définitivement de la refonte de
Looker (`looker/docs/refonte/00_PLAN_REFONTE.md`, ex-vague V15) et seront intégrés **plus tard dans
Cockpit**, qui possède déjà le moteur d'itinéraire et l'édition des règles (`/admin/itineraire`,
service valhalla-admin, voir `docs/claude/routing.md`). Looker n'en gardera aucune partie : il
reste l'atlas (référentiel cartographique) et peut seulement afficher en lecture une couche
« fermetures » si Cockpit l'expose.

Rien n'est commencé : cette note garde ce qui avait été prévu côté Looker, pour reprendre le sujet ici.

## Ce qui était prévu

- **Une variante = une épreuve valhalla-admin** (fermer, interdire VL / piétons / vélos, sens
  unique, piste, vitesse), rattachée à un événement. L'édition des règles reste dans
  `/admin/itineraire` (décision Q15 du 29/10 : Cockpit est l'éditeur).
- Ce que Cockpit n'a pas encore et qu'il faudrait ajouter :
  - horaires et motifs de chaque mesure ;
  - parkings et accès ;
  - demande et charge ;
  - panneaux manquants, avec un pont vers SignManager (plan de pose) ;
  - planches A3 « public » et « technique » ;
  - liste des mesures pour l'arrêté ;
  - comparaison de variantes.
- **Pas d'essai de charge** (décision Q14 du 29/10) : on dessine les variantes (planches, arrêté)
  sans calculer les itinéraires détournés. Charger une épreuve sur le Valhalla de production le
  rend indisponible ~30 s pour toutes les applications ; une seconde instance « bac à sable » a été
  écartée.
- Client Valhalla : celui de Cockpit (4 modes : normal, normal + piste, god, god + piste ; recettes
  lues sur `GET /profiles` ; jamais `ignore_access`).
- Ponts : panneaux vers SignManager, parkings vers Groundmaster, piétons vers Flux piétons (Looker).

## Matière existante

- Atelier du plan canicule (`2026/PLAN CANICULE/atelier`), partie « plans de circulation » :
  mesures par voie OSM avec horaires, planches, projet `Le_Mans_FC.json` (à migrer).
- Critère de sortie qui avait été fixé : reproduire la planche « public » du 24 H 2026.
- Charge estimée côté Looker : ~12 jours.

Le reste de l'atelier (chantiers et budget, relief et PSA) part dans Looker (espace Projets, vagues
V13 et V14).
