<!-- Cahier des charges. Statut : livraisons 0 et 1 en service (27/10/2026), 2 a 4 a construire.
     Etat reel du code : section 10. Choix valides avec Ludo le 27/10/2026. -->

# Patrouilles programmees et envoi des intrusions aux tablettes

## 1. Objectif

Donner aux tablettes Field des **patrouilles programmees** : un parcours de points d'interet calcule
par le moteur d'itineraire (Valhalla), enregistre comme **scenario**, envoye a une tablette ou a un
groupe selon un **planning a peindre** (meme outil que l'armement de la detection), suivi en direct
et trace en main courante. Ensuite : **points proposes par l'IA** d'apres l'historique des incidents.
En parallele : **envoi automatique des intrusions confirmees** a la tablette la mieux placee.

## 2. Choix valides (27/10/2026)

| Sujet | Choix |
|---|---|
| Deplacement | Par scenario : vehicule (modes `normal` / `normal_piste` selon le droit piste du groupe) ou a pied (`pedestrian`, jamais de piste) |
| Ordre des points | Fixe a la main ; bouton « Optimiser » qui propose l'ordre le plus court, a valider ; boucle retour au depart en option |
| Validation d'un point | Automatique au passage GPS a moins de `radius_m`, ou bouton « Point fait » ; consigne et photo exigee reglables par point |
| Zone de competence | Zones de la couche Looker deja utilisee pour la prise de poste (`operateurs` `zones_collection`), par tablette ; aucune zone = partout |
| Contrainte de zone | Les POINTS doivent etre dans la zone (controle a l'enregistrement et a l'affectation) ; le trajet entre deux points peut en sortir si la route l'impose |
| Affectation | Par planification : une tablette precise, ou un groupe de balises (premiere tablette disponible dont la zone couvre les points) |
| Refus / silence | Tablette suivante du groupe ; personne (ou tablette imposee) : alerte au PC Org et trace en main courante |
| Main courante | Une fiche par patrouille : ouverte a l'acceptation, chaque point dans la chronologie, cloturee avec le bilan |
| Points d'interet | Bibliotheque partagee entre scenarios (l'IA l'alimentera) |
| Interruption | La tablette reste proposable au dispatch ; en intervention la patrouille passe en pause et reprend au prochain point non fait |
| Propositions d'apres l'historique | Moteur 100 % code AUTONOME (foyers, score de gravite, repartition, horaires) ; « Affiner avec l'IA » EN PLUS, reserve aux admins, avec le texte des fiches pour juger la gravite ; toujours valide par l'operateur |
| Intrusions : declencheur | Confirmation par la verification IA (dans le planning d'armement), reglable par groupe de cameras pour exiger plutot la validation d'un operateur |
| Intrusions : tablettes | Categories cochees (Securite par defaut), vues recemment, zone couvrant la camera ou sans zone ; priorite a celle en patrouille, puis la plus proche |
| Intrusions : envoi | Proposition courte (60 s) avec alerte plein ecran + push, photo, camera, lieu, itineraire ; sinon tablette suivante puis PC Org ; fiche Securite creee |

## 3. Existant reutilise

- **Planning** : `detection_arming.py` (semaines types peintes au quart d'heure, exceptions datees ou
  calees sur une epreuve via `event_courant`, jours feries, forcage temporaire) et sa grille JS de
  `detection.js`. A extraire en module commun (`schedule_grid.py` + `static/js/schedule_grid.js`) SANS
  changer le comportement de la detection (meme format, memes tests).
- **Itineraire** : `routing._compute(from, to, waypoints, mode)` (points `via`), evitements Waze en
  vehicule, `field._device_route_piste`. A ajouter : le costing pieton, et l'optimisation de l'ordre
  (`/optimized_route` de Valhalla, a verifier avec nos recettes).
- **Proposition / file / acceptation** : mecanique de `dispatch_auto.py` (une proposition a la fois par
  unite via `field_devices.pending_proposal`, delai, unite suivante, transitions atomiques filtrees sur
  l'etat attendu). Les patrouilles et intrusions y ajoutent leurs `kind`.
- ⚠️ **REGLE : aucune tache de fond dans le processus Cockpit.** Tout traitement periodique est un
  script lance par une **tache planifiee Windows** (comme `alert_engine.bat`, `live_controle.bat`).
  Le `start_scheduler` historique du dispatch auto dans `app.py` ne sert PAS de modele.
- **Moteur** : `patrol_engine.py` + `patrol_engine.bat` (meme gabarit que `alert_engine.bat` : venv
  `titan_prod`, `-X utf8`, `cd` dans le depot), tache planifiee « Cockpit - Patrouilles » toutes les
  minutes, compte `l.arnault` (variables utilisateur). Chaque execution boucle ~55 s (passage toutes les
  3 s : intrusions, delais de reponse, lancements) puis s'arrete. Verrou en base
  (`cockpit_settings._id="patrol_engine_lock"`, bail de 70 s pose par `find_one_and_update`) : une
  execution qui chevauche la precedente sort aussitot. Journal `logs/patrol_engine-AAAAMMJJ.log`.
- **Tablette** : polls `/field/my-fiches` + `/field/inbox`, file hors ligne idempotente, `setRouteDestination`,
  ASL GPS, push. Statut de l'agent inchange (`patrouille` = disponible).
- **Zones** : couche Looker de `operateurs` (polygones, `properties._id_feature`), lue en direct.
- **Main courante** : `pcorg_history.append_entry`, fiches marquees dans `content_category` et exclues
  du dispatch auto, du libre-service et de `alert_engine.detect_pcorg_urgency` (comme `field_sos`).
- **Intrusions** : episodes `hik_intrusions` (verdict `confirmed`, photos, `armed`), position de la
  camera `cockpit_cameras.map {lat, lng}`, groupes de cameras `detection_groups`.

## 4. Donnees (MongoDB)

```
patrol_points        {_id, name, lat, lng, radius_m (25), instruction, photo_required,
                      dwell_s (0), zone_ids [calcule], source: manual|ia, ia_reason,
                      active, created_by, created_at, updated_at}
patrol_scenarios     {_id, name, color, travel: vehicle|pedestrian, loop, points: [point_id],
                      estimate {distance_m, duration_s, polyline, engine, computed_at, order_optimized},
                      archived, version, created_by, updated_at}
patrol_plans         {_id, name, scenario_id, enabled,
                      target {kind: device|group, device_id | beacon_group_id},
                      arming {format detection_arming : mode, weeks, default_week, exceptions,
                              holidays, override},
                      launch {rule: span_start | every, every_min}, accept_timeout_s (120),
                      created_by, updated_at}
patrol_runs          {_id, plan_id|null, scenario snapshot {name, travel, loop, points figes},
                      slot_key "<plan>|<debut de plage UTC>" (unique : jamais deux lancements),
                      device_id, state: proposed|running|paused|done|partial|refused|expired|cancelled,
                      attempts [{device_id, at, answer, reason}], fiche_id,
                      steps [{point_id, name, state: pending|done|missed|skipped, at, method: gps|manual,
                              photo, comment}], started_at, ended_at, by}
field_devices        + competence_zones [_id_feature]   (vide = partout)
hik_intrusions       + field_dispatch {state, device_id, fiche_id, at, attempts}  (pose atomiquement)
detection_groups     + field_dispatch {mode: ai|operator|off, categories [PCO.Securite]}
cockpit_settings     _id "patrols" : rayon par defaut, delais, fenetre « vue recemment » (15 min)
```

## 5. Fonctionnement

### 5.1 Lancement
- **Planifie** : `patrol_engine.py` (tache planifiee) regarde a chaque passage les plans actifs. `span_start` = un lancement au
  debut de chaque plage peinte ; `every` = toutes les `every_min` minutes dans la plage. `slot_key`
  unique : un redemarrage ou un tick double ne cree jamais deux patrouilles.
- **Manuel** : « Envoyer maintenant » depuis un scenario (tablette ou groupe).
- Choix de la tablette (groupe) : non revoquee, non expiree, voit l'evenement (`device_matches_pair`),
  statut `patrouille`, vue depuis moins de 15 min, pas de proposition ni de patrouille en cours, zone de
  competence couvrant tous les points (ou sans zone), pas en mode declarant ; la plus proche du premier
  point d'abord.

### 5.2 Sur la tablette
- Carte de proposition : nom, nombre de points, distance et duree estimees, apercu du trace ;
  Accepter / Refuser (motif). Delai `accept_timeout_s`, compte a rebours.
- Acceptee : bandeau patrouille, itineraire vers le prochain point (`setRouteDestination` + polyline),
  liste des points avec avancement, consigne du point, « Point fait », « Passer » (motif), photo si
  exigee, « Terminer » / « Abandonner » (motif).
- Validation GPS : passage a moins de `radius_m` (meme principe que l'ASL automatique). Hors ligne : la
  validation part en file, idempotente par `(run, point)`.
- Interruption : engagement sur une fiche → `paused` ; retour en `patrouille` → reprise proposee au
  prochain point `pending`.

### 5.3 Main courante et suivi
- Fiche de la categorie de la tablette, `content_category.patrol_run = <id>`, ouverte a l'acceptation :
  « Patrouille programmee : <scenario> (N points) ». Une entree par point fait / passe, par pause et
  reprise ; cloture avec le bilan (faits / manques / passes, duree, distance).
- Refus de tous ou tablette imposee indisponible : alerte « Patrouille non prise » a la centrale et
  fiche Information cloturee.
- Cockpit : patrouilles du jour en direct (carte, avancement), historique et taux de realisation par
  scenario, popup du marqueur tablette sur la carte de l'accueil.

### 5.4 Envoi des intrusions confirmees
- **Sans toucher au recepteur Hik** (hors depot) : chaque passage de `patrol_engine.py` (3 s) lit les
  episodes recents `armed`, verdict `confirmed`, non `ecartee`, sans `field_dispatch`, d'un groupe de
  cameras en mode `ai` ; en mode `operator`, l'envoi part a l'avis « vrai » ou au bouton « Envoyer a
  une patrouille » de la page Detection. `field_dispatch.state` est pose par `find_one_and_update`
  conditionne : un seul envoi par episode.
- Tablette : categories du groupe de cameras (Securite par defaut), vue < 15 min, non declarante,
  zone de competence couvrant la camera (ou sans zone). Priorite : en patrouille (la plus proche),
  puis disponible la plus proche, puis toute tablette eligible vue recemment. Une tablette deja en
  intervention n'est pas choisie.
- Proposition courte (60 s) : alerte plein ecran + push avec photo de l'episode, camera, lieu,
  itineraire. Acceptee = engagement sur une fiche Securite creee (`content_category.intrusion_episode`,
  photo jointe) ; une patrouille en cours passe en pause. Sinon tablette suivante, puis PC Org.
- Les envois et refus sont visibles dans la fiche de l'episode (page Detection).
- **Anti-rafale** (indispensable, voir les volumes ci-dessous) : une camera deja envoyee depuis moins de
  `burst_min` (10 min par defaut, reglable par groupe) ne relance pas d'envoi ; les nouveaux episodes
  s'ajoutent a la fiche en cours (photo + entree de chronologie) et la tablette engagee recoit la mise a
  jour. Plafond d'envois par heure et par groupe, au-dela : PC Org seulement.
- **Filtre de libelle IA** par groupe de cameras : `personne` seul par defaut, `vehicule` en option.

Volumes mesures le 27/10/2026 (7 jours, `hik_intrusions`) : 3 516 episodes `ai.status = confirmed` sur
10 cameras, dont **112 dans le planning** (`armed` different de false), soit 16 par jour en moyenne
mais **80 le 24/10** ; 61 `personne`, 51 `vehicule` ; sur 70 avis operateur, 16 « faux » (23 %).
119 - Porte Houx represente 45 des 112. Sans anti-rafale ni filtre, une journee comme le 24/10 enverrait
80 interventions.

⚠️ Champ reel du verdict : `ai.status` (`confirmed` / `filtered` / `unverified`), pas `ai.verdict`.

## 6. Propositions d'apres l'historique (apres la planification)

Trois etages. Le socle (6.0) tourne la nuit. Le moteur (6.1) se suffit a lui-meme : il fonctionne sans
modele distant, sans cle, sans cout. L'affinage (6.2) est facultatif et demande par un admin.

### 6.0 Fiches structurees la nuit (Alfred local)

Les patrouilles se construisent sur des evenements PASSES : rien n'est calcule a la demande. Une tache
planifiee Windows extrait chaque nuit, avec le modele local d'Alfred, une version structuree de chaque
fiche, stockee en base et relue directement par le moteur.

- **Modele** : Ollama de la VM Alfred (`OLLAMA_URL`, `OLLAMA_MODEL` = `alfred` = Qwen3 30B-A3B instruct
  Q4_K_M sur GPU), `format: json`, `temperature: 0`. Essai du 27/10/2026 sur 24H AUTOS 2026 : 2 a 4 s par
  fiche, JSON valide, gravite plus juste que le champ urgence (ex. jeunes alcoolises au volant + insultes
  racistes saisis IMP, juges 4/5), noms retires ; tendance a enjoliver l'issue (« situation
  stabilisee ») : jamais presente comme un fait, voir 6.2.
- **Entree** : categorie, urgence, `text` (titre, 37 caracteres en mediane) et surtout `comment` (deroule
  horodate : 297 en mediane, jusqu'a 2 933 ; tronque a 6 000).
- **Sortie** : collection `pcorg_digests` (jamais d'ecriture dans `pcorg`, que la synchro SQL et la main
  courante reecrivent) :
  `{_id: <id fiche>, event, year, src_hash (sha1 de text + comment + categorie + urgence), model,
    prompt_version, at, gravite 1-5, type, victimes, moyens_engages, issue, resume, error, attempts}`.
  Index `event + year`, `gravite`.
- **Pourquoi toutes les fiches** : la main courante n'a AUCUNE gravite exploitable. Constat du 27/10/2026 :
  30 429 fiches sur 30 578 viennent de la base SQL (`sql_id`) ; `niveau_urgence` vide sur 25 386 et `IMP`
  sur 5 150 (42 fiches seulement UR / UA / EU) ; `severity` = 0 sur 30 565 ; `is_incident` toujours faux.
  La gravite evaluee est donc la seule disponible, utile bien au-dela des patrouilles.
- **A traiter** : fiches sans digest, ou dont `src_hash` / `prompt_version` a change. Ordre : fiches avec
  position d'abord (2 868, une nuit), puis les autres des plus recentes aux plus anciennes. Rattrapage
  complet : ~30 000 fiches a 2-4 s, ~25 h, soit environ 5 nuits de 5 h. Ensuite, quelques dizaines par nuit.
  Une fiche validee par un admin (6.0 bis) n'est jamais reevaluee.
- ⚠️ La gravite evaluee ne remplace JAMAIS `niveau_urgence` dans `pcorg` (donnee source, reecrite par la
  synchro SQL). Elle vit dans `pcorg_digests`. Champs d'entree exacts a confirmer au codage (`text`,
  `comment`, `text_full` present sur 30 478 fiches).
- **Tache** : `pcorg_digest_nightly.py` + `.bat` (gabarit `alert_engine.bat`), tache planifiee
  « Cockpit - Fiches structurees (nuit) » a 01:00, compte `l.arnault` (variables utilisateur). Fenetre
  bornee : s'arrete seule a 06:00 (`DIGEST_END_HOUR`) et reprend la nuit suivante ; une fiche a la fois
  (le GPU sert aussi au chat Alfred) ; verrou en base contre les chevauchements ; echec d'une fiche =
  `error` + `attempts`, retentee la nuit suivante (abandon apres 3) ; Ollama injoignable = arret propre.
  Journal `logs/pcorg_digest-AAAAMMJJ.log` avec le bilan (traitees, erreurs, duree, reste).
- **Suivi** : couverture par evenement (fiches avec position / structurees) visible dans l'onglet
  Propositions ; une proposition sur un evenement incompletement structure l'indique.

**Etat au 27/10/2026 (code : `pcorg_digest.py`, `pcorg_enrich.py`, `gravite_fiches.py`)**

- Consignes **v2** : gravite recalee sur les 35 premiers controles (v1 26/35 justes, v2 33/35 sur ces memes
  fiches, chiffre optimiste puisqu'elles ont servi a ecrire les consignes) ; faits extraits en plus : lieu
  cite, heures des etapes, equipement, moyens (noms de personnes filtres : `_looks_like_person`), services
  exterieurs, causes (liste fermee `CAUSES`), issue codee, donnees personnelles reperees.
- Code pur (`pcorg_enrich`, sans modele) : position = GPS d'origine > carroyage de la fiche
  (`content_category.carroye`, espaces retires : ~2 300 fiches sans GPS en gagnent une) > lieu cite rapproche
  du referentiel Looker (1 210 lieux nommes + 49 tribunes + 11 virages ; cadastre, riverains, cameras exclus) ;
  zone de competence ; delais depuis le signalement ; completude /4 ; doublons probables (meme evenement,
  < 45 min, score lieu + type + resume >= 0,72), un « non » d'admin n'est jamais repose.
  `pcorg_digest.py --enrich-only` recalcule ces champs sans le modele.
- Une fiche controlee est retraitee a chaque nouvelle version des consignes (nouveaux champs) ; le controle
  garde la gravite et la version JUGEES (`review.gravite_modele`, `review.prompt_version`) et la page mesure
  chaque version sur les fiches validees.
- Lancement uniquement par la page (lots de 50 / 100) ou la fenetre de nuit, via la tache planifiee
  « Cockpit - Fiches structurees » (`pcorg_digest.bat`, chaque minute, `--poll --night 01:00-06:00`).
- Consignes **v3** (27/10/2026), apres lecture d'un echantillon : 52 % de la base est le PC Surete (rondes,
  ouvertures / fermetures, prestations, alarmes) et 15 % des prises de service. Ajouts : `nature`
  (incident / routine / information) et `activite` ; blocs facultatifs `ronde` (horaires, points dans
  l'ordre, RAS / anomalies), `alarme` (mise hors service / remise), `dysfonctionnement` (RETEX : communication,
  documentation, materiel, effectif, procedure), `jalon` (ouverture des portes...), `secours` (nature, sexe,
  tranche d'age, prise en charge : categories seulement), `prise_service` (poste, horaires, materiel, sans
  nom) ; `sous_classification_proposee` = valeur EXACTE de la liste officielle de la categorie
  (`pcorg_config.pcorg_lists.sous_classifications`, envoyee avec la fiche). `num_predict` 1 200 par requete
  (le modele alfred est regle a 500) ; `num_ctx` jamais envoye (le changer recharge le modele partage avec
  le chat Alfred, charge en 16 384).
- Code pur v3 : taxonomies rapprochees de la liste officielle (`Taxonomy` : variantes d'accents, synonymes
  `SC_SYNONYMS`, 6 orthographes du motif « Ronde surete... »), niveaux de zone (`area.desc`), relances,
  doublons DECLARES (« Doublon fiche 31645 » -> `sql_id`, verite de terrain du detecteur), alarmes mises hors
  service sans remise en service du meme batiment sous 24 h (`alarme_non_remise`).
- Voisinage de la position (code pur, 27/10/2026) : `competence` (departement / zone / secteur + postes, lot
  prestataire ; `zone` = secteur, le plus fin), `dans` (terrain / parking, batiment, fan zone, campement
  contenant le point), `pres` (porte, tribune, virage, portail 3P... le plus proche par couche, 150 m GPS /
  carroyage, 100 m lieu cite). ⚠️ Le GPS d'une fiche peut etre le lieu de SAISIE, pas de l'incident ; les
  numeros de tribune de la couche sont ceux de 2026 (« ANCIEN 2025 » non exploite).
- Plaques d'immatriculation (`plaques`, SIV / FNI / britanniques) : deux fiches proches aux plaques
  differentes ne sont jamais doublons, une plaque commune = doublon (fiches fourriere au meme texte).
- Fiche de test / essai : bouton « Ignorer » (`review.verdict == "ignoree"`, `ignoree`) : jamais retraitee,
  hors justesse.
- Consignes **v4** (27/10/2026) : echelle v3 + agression / menace / insultes visant un agent = 4, agression
  sexuelle = 5, fausse alerte levee = 1, la conclusion du deroule prime sur le signalement. La zone de saisie
  (`area`) est envoyee raccourcie et nommee « approximative, ce n'est pas le lieu » (fiche du Village saisie
  en Grand Paddock, GPS et carroye concordants). Age / sante d'un anonyme ne sont pas des donnees
  personnelles (filtre en code). ⚠️ Mesure : TOUJOURS rejouer l'ancienne ET la nouvelle consigne sur les
  fiches controlees (le modele varie d'un passage a l'autre, ~2 fiches sur 85) avant de changer de version.
  Rejeu sur 85 controles v3 : v3 72, v4 72 ; des echelles 3 / 4 plus detaillees (conduite dangereuse,
  altercation, douleur avec ambulance = 3...) faisaient 66 a 71 : chaque regle corrige un cas limite et en
  decale un autre. Erreurs restantes : altercations, conduite dangereuse sans accident, intrusion sans suite
  (modele 4, PC 3).
  Nouveaux blocs : `agression_agent` (cible accueil / securite / autre_personnel, forme verbale / menace /
  physique ; pas les disputes entre personnels) et `chaleur` (impact personne / materiel / organisation ;
  pose d'office sur un `secours.nature == coup_de_chaleur`), marques « a controler » en priorite.
- Les notes libres des controles ne sont PAS lues par le modele : elles servent a ecrire la version suivante
  des consignes (relire toutes les notes, y compris sur les gravites justes).
- ⚠️ Pour les propositions de patrouilles (6.1) : ne retenir que `nature == "incident"`, sinon les foyers sont
  noyes sous les rondes RAS et les prises de service.

### 6.0 bis Controle par sondage (page admin dediee)

Page **/admin/gravite-fiches** (page d'administration `gravite_fiches` de `ADMIN_PAGE_REGISTRY` : admins,
accordable explicitement a un groupe). But : mesurer la justesse du modele et corriger, sans relire
30 000 fiches.

- **Sondage** : tirage aleatoire STRATIFIE (par gravite evaluee, categorie, evenement), avec
  sur-representation des gravites 4-5 et des fiches ou gravite evaluee et urgence saisie divergent.
  Filtres : evenement, categorie, gravite, non encore controlees.
- **Ecran de controle** : a gauche la fiche d'origine (titre, deroule, categorie, urgence saisie), a
  droite la sortie du modele (gravite, type, victimes, moyens, issue, resume). Reponse en un clic :
  « Juste », « Trop haut » / « Trop bas » + gravite corrigee, commentaire facultatif ; « Resume
  inexact » coche a part (enjolivement). Raccourcis clavier pour enchainer.
- **Stockage** : `pcorg_digests.review {verdict: ok | plus | moins, gravite_validee, resume_inexact,
  note, by, at}`. Gravite utilisee partout = `gravite_validee` si presente, sinon `gravite`.
- **Mesures** : taux de justesse global et par gravite, categorie, `prompt_version` ; matrice
  evaluee / validee ; ecart moyen ; taux de resumes inexacts ; marge d'erreur affichee selon la taille
  de l'echantillon (ex. 200 controles : ~ ±7 points).
- **Boucle d'amelioration** : les corrections servent d'exemples dans le prompt (quelques cas par niveau)
  ; nouvelle `prompt_version` = reevaluation des fiches non validees les nuits suivantes, puis nouveau
  sondage pour comparer les versions.
- Hors perimetre pour l'instant : afficher la gravite evaluee dans la main courante (badge), a decider
  une fois la justesse mesuree.

### 6.1 Moteur de propositions (code seul, toujours disponible)

- **Dominantes** (choix du 27/10/2026) : chaque scenario porte UNE dominante (`patrol_scenarios.dominante`,
  `patrols.DOMINANTES`), le theme de la patrouille ; la proposition se fait par dominante et vise le
  groupe de tablettes adapte :
  - `surete` : fiches de la categorie Securite (`PCO.Securite`), ponderees par la gravite evaluee ;
  - `secours` : fiches Secours (`PCO.Secours`, bloc `secours` des fiches structurees) ;
  - `incendie` : prevention incendie (bloc `alarme` incendie, nature incendie, equipements feu) ;
  - `prevention` : plan de prevention et lieu de travail, LES DEUX sources : zones de travail actives
    a la date de la patrouille dans Asker (`asker_plans_prevention.zoneIds`, `asker_planning_montage`
    zone + periode, zones `asker_zones`, certaines sans geometrie) ET incidents passes lies aux travaux ;
  - `generale` : sans theme (defaut).
  « Prevention chaleur » retiree a la demande (27/10/2026). A regler plus tard : rattacher les
  dominantes aux zones de competence et aux zones de travail.
- Entree : l'evenement (toutes editions) et/ou SAISON, dominante, nombre de patrouilles.
- Fiches avec position (`pcorg.gps`) regroupees en **foyers** (rapprochement a moins de X m).
- Par foyer : nombre d'incidents, nombre d'editions touchees (recurrence), categories et
  sous-classifications, niveaux d'urgence, repartition par heure et par jour, zone de competence.
- **Score de gravite deterministe** : somme par fiche de sa gravite (`pcorg_digests.gravite`, 1-5,
  ponderee 1 / 2 / 4 / 8 / 16), a defaut du poids de son urgence (IMP 1, UR 2, UA 4, EU 8), x poids de
  sous-classification (table reglable dans `cockpit_settings._id="patrols"`), multiplie par un bonus de
  recurrence (nombre d'editions). Le detail du calcul est affiche pour chaque foyer, avec la part des
  fiches structurees.
- Points proposes : les foyers les mieux notes, nommes d'apres la zone et la sous-classification
  dominante (« Zone T34 - Rixe »), avec leurs chiffres.
- **Repartition en N patrouilles** : regroupement par zone de competence, puis equilibrage par score
  cumule et par temps de parcours (matrice Valhalla) ; heures conseillees = pics de la repartition
  horaire des foyers de la patrouille.
- Resultat reproductible (memes donnees = meme proposition), affiche sur une carte des foyers, a
  corriger puis enregistrer.

### 6.2 « Affiner avec l'IA » (en plus, admins seulement)

- Bouton visible et route acceptee **uniquement pour un admin** : `is_super_admin` ou role cockpit
  `admin`, verifie cote serveur. **Pas** d'acces par une page d'administration accordee a un groupe
  (`request_admin_grant` ne suffit pas, meme si la page /patrouilles est accordee).
- Part de la proposition du moteur (6.1) et l'envoie a Claude (`COCKPIT_ANTHROPIC_API_KEY`,
  `CLAUDE_MODEL`, couts suivis comme les autres rapports IA) **avec les fiches de chaque foyer** : date,
  heure, categorie, sous-classification, urgence et la version structuree de 6.0 (gravite, type,
  victimes, moyens, issue, resume sans nom), pour que le modele juge la gravite reelle et pondere la
  patrouille en consequence. Pour les fiches de gravite 4-5 (peu nombreuses), le deroule original
  masque est joint : le modele verifie ce que le resume local a pu enjoliver.
- Bornes de volume : grace aux resumes, toutes les fiches d'un foyer passent ; foyers au-dela du rang
  40 resumes en chiffres seulement. Estimation et confirmation avant envoi (nombre de fiches, taille).
- ⚠️ Donnees personnelles : les resumes locaux sont deja sans nom ; masquage automatique en plus sur
  tout texte envoye (motifs plaque, telephone, email, « M. / Mme <Nom> »), et rappel a l'admin que le
  texte part chez Anthropic.
- Le modele renvoie, en JSON valide controle : score revu et justification par foyer, points retenus
  (nom, raison, priorite, heures), repartition en N scenarios et plages horaires. Affiche **a cote** de
  la proposition du moteur (ecarts mis en evidence) ; l'admin choisit, corrige, enregistre.
- Panne, cle absente ou budget atteint : message clair, la proposition du moteur reste utilisable.
- Points enregistres : `source: engine | ia`, `ia_reason` ; scenarios en brouillon. Rien n'est active
  sans validation.

## 7. Pages et droits

- Page **/patrouilles** (page d'administration accordable `patrouilles`, groupe Parametres) : onglets
  Points, Scenarios (editeur, ordre par glisser, Optimiser, apercu, controle de zone), Planifications
  (grille peinte commune), Suivi, Historique, Propositions (carte des foyers, moteur 6.1 ; bouton
  « Affiner avec l'IA » affiche aux seuls admins, 6.2).
- Zone de competence d'une tablette : table de Field dispatch et modale d'appairage.
- Reglage intrusions → tablettes : fenetre de groupe de la page Detection.

## 8. Decoupage en livraisons

0. **Fiches structurees la nuit** (6.0) puis **controle par sondage** (6.0 bis) : independantes du reste,
   peuvent demarrer tout de suite ; fiches avec position pretes apres une nuit, rattrapage complet en
   ~5 nuits, puis premier sondage pour mesurer la justesse avant de s'en servir.
1. **Socle** : bibliotheque de points, scenarios (ordre, Optimiser, apercu, a pied / vehicule), zones
   de competence, « Envoyer maintenant », execution tablette, fiche main courante, suivi.
2. **Planification** : grille peinte commune (extraction sans regression de la detection), plans,
   `patrol_engine.py` + tache planifiee, refus / escalade, alerte « Patrouille non prise ».
3. **Intrusions → tablettes** : reglage par groupe de cameras, surveillance des episodes, proposition
   avec photo, fiche Securite. Peut passer avant la 2 si besoin operationnel.
4. **Propositions** : moteur code seul (foyers, score, repartition, horaires), puis « Affiner avec
   l'IA » pour les admins.

## 9. Points a verifier avant de coder

Constats en base du 27/10/2026 (lecture seule) :

- **Couche de zones** : configuree (`operateurs.zones_collection = "competences"`, libelle `Nom`),
  **56 zones** retenues par `operateurs.zones` (55 `MultiPolygon` + 1 `Polygon`, toutes avec
  `_id_feature`) : departements, lots, secteurs. Recompte du 27/10/2026 par le code lui-meme ; un
  premier constat « un seul polygone » etait faux (il ne comptait que les `Polygon`).
- **Fiches avec position** (`pcorg.gps`, GeoJSON `Point` `[lng, lat]`) : 2 868 sur 30 578 ; 24H AUTOS
  2026 901, GPF 2026 448, 24H MOTOS 2026 405, GP EXPLORER 2025 351, LE MANS CLASSIC 2026 321, 24H CAMIONS
  2025 215 / 2026 180, SAISON 2026 8. Assez pour l'IA sur les grandes epreuves, insuffisant en SAISON.
- **Positions des cameras** : TOUJOURS passer par `detection._device_position` (position manuelle
  `cockpit_cameras.map`, sinon couche `titan.cameras` par IP puis nom). 220 appareils sur 337 ont une
  position (3 manuelles, 217 par la couche) et **les 32 canaux de detection en ont tous une** : rien ne
  bloque la livraison 3. (Un premier constat « 3 sur 337 » ne lisait que `cockpit_cameras.map`.)

Verifie en reel le 27/10/2026 (lecture seule, 5 points autour du circuit) :

- `/route`, `/optimized_route` et `/sources_to_targets` repondent avec les recettes `normal` (`auto`)
  et `normal_piste` (`taxi`) et en `pedestrian` (13,7 km en ordre saisi, 13,5 km en ordre optimise en
  vehicule ; 10 km / 2 h a pied). `/optimized_route` garde le PREMIER et le DERNIER point fixes et
  reordonne ceux du milieu : pour une boucle, mettre le depart aussi en dernier ; pour un ordre libre
  de bout en bout, passer par la matrice et un calcul cote Cockpit.
- Toute modification de `field.js` passe par la nouvelle empreinte du service worker (automatique).
- Delai d'une proposition (60 s intrusion, 120 s patrouille) : expire au passage suivant du moteur
  (3 s pres), y compris entre deux executions de la tache (bail du verrou).
- Livraison 1 (« Envoyer maintenant ») : la proposition est posee par la route, mais son expiration
  et le report a la tablette suivante passent par le moteur : la tache planifiee est donc creee des la
  livraison 1.

## 10. Etat du code (livraison 1, 27/10/2026)

Fichiers : `patrols.py` (coeur sans Flask), `patrols_api.py` (`patrols_bp` page + `/api/patrols/*`,
CSRF conserve ; `patrols_field_bp` `/field/patrol/*`, exempte de CSRF), `patrol_engine.py` +
`patrol_engine.bat` (tache « Cockpit - Patrouilles », definition `scripts/tache_patrouilles.xml`,
compte l.arnault, chaque minute, IgnoreNew), `templates/patrouilles.html`, `static/js/patrouilles.js`,
`static/css/patrouilles.css`, tablette dans `field.js` (bloc « Patrouilles programmees ») +
`#patrol-card` de `field.html` + fin de `field.css`. Tests : `tests/test_patrols.py` (MongoDB local).

- Page `/patrouilles` dans le groupe **Terrain** du menu (choix du 27/10 : avec Field dispatch,
  pas Parametres), page admin accordable `patrouilles`. Onglets Suivi (10 s), Scenarios, Points,
  Tablettes (zones de competence : ici, PAS dans Field dispatch), Historique, Reglages.
- **Une proposition a la fois** : `field_devices.patrol_proposal {run_id, proposed_at, expires_at}` a
  cote de `pending_proposal` (fiche). `dispatch_auto._proposal_pending` et la reservation de
  `propose_next` regardent les deux. Une tablette en patrouille EN COURS reste proposable au
  dispatch (choix du 27/10) ; l'accepter met la patrouille en pause.
- **Pause / reprise** : `patrols.sync_pause` compare le statut de l'agent (intervention, sur place,
  fin d'intervention, pause = pause) ; appele par `/field/status`, par `/field/my-fiches` et par le
  moteur (3 s) : tous les chemins d'engagement (proposition acceptee, PC Org, libre-service) sont
  couverts sans toucher a chacun. Fiche de patrouille close par le PC Org = patrouille annulee.
- **Fiche de main courante** : `_id` uuid5 de la patrouille (acceptation rejouee = meme fiche),
  categorie de la tablette (a defaut `PCO.Information`), `niveau_urgence` nul, `content_category`
  `{patrouille: <nom>, patrol_run, patrol_scenario}` : `patrouille` renseigne l'exclut du dispatch,
  du libre-service ; `/field/my-fiches` l'ecarte explicitement (`patrol_run` absent).
- **Points** : etat `pending | done | skipped | missed`, transition par `$elemMatch` sur `pending`
  (idempotent, rejouable par la file hors ligne). Passer = motif obligatoire. Photo exigee = refus
  `photo_obligatoire` sans photo ; au passage GPS la tablette DEMANDE la photo au lieu de valider.
  Dernier point traite = fin automatique (`done` si tout fait, sinon `partial`).
- **Validation GPS cote tablette** (`checkPatrolProximity`, chaque position de `watchPosition`) :
  distance <= rayon + min(15 m, precision / 2), apres `dwell_s`. Le serveur ne valide rien seul.
- **Itineraire** : `/field/patrol/<run>/route` (position -> prochain point), a pied `pedestrian`,
  vehicule `normal`, ou `normal_piste` si le groupe a l'acces piste. `setRouteDestination` sans
  fiche (`reconcileRoute` ne l'efface donc pas) ; recalcule a la reprise apres une intervention.
- **Optimiser** : matrice `/sources_to_targets` + ordre exact (programmation dynamique) jusqu'a 11
  points, plus proche voisin + 2-opt au-dela ; le premier point reste le depart ; a duree egale
  l'ordre saisi est garde. Rien n'est enregistre sans « Enregistrer ».
- **Personne ne prend** (`refused`) : pour l'instant seulement visible au Suivi ; alerte « Patrouille
  non prise » et fiche Information = livraison 2 (`patrols.on_not_taken`).
- Dominante enregistree sur le scenario (section 6.1), sans effet avant la livraison 4.

### Points tires des couches Looker (27/10/2026)

- Reglages : couches proposees (`cockpit_settings.patrols.layers`, defaut sanitaires, tribunes, `3p` =
  portails), liste lue dans `categories_config` (`patrols.available_layers`).
- Points > « Importer depuis une couche » : filtre nom / informations, zone de competence, lasso
  (polygone trace sur la carte, coche ce qu'il contient), clic sur la carte, tout cocher ; « Creer les
  points » ou « En faire un scenario » (40 points au plus, ordre optimise propose).
- Point importe : `source "layer:<coll>"`, `layer_ref {collection, fid}`, `info` (proprietes renseignees
  hors nom, 8 au plus, transmises a l'agent sous la consigne). Il **suit Looker** : nom, position et
  infos relus a l'ouverture de la page (`sync_layer_points`) et a chaque lancement (`create_run`) ;
  nom et position non modifiables a la main ; objet disparu = `layer_missing` (derniere position
  gardee). Re-import = mise a jour du meme point, jamais de doublon. Nom : champ de recherche de la
  couche (+ NOM s'il differe) ; numero seul = prefixe « Tribune 01 » ; objet sans nom = nature + acces.

### Livraison 2 : planifications (27/10/2026)

- `patrol_plans {name, scenario_id, target, enabled, launch {rule: span_start | every, every_min},
  arming, valid_from, valid_until, note, last_launch {slot_key, at, slot, result}}`. Le planning
  (`arming`) est au format de la detection et evalue par **`detection_arming.py` importe tel quel**
  (`group_state`, `month_view`, `explain` sur `{"arming": ...}`) : jamais de copie de la regle.
  Recurrence : semaines types au quart d'heure, exceptions datees (une fois ou chaque annee),
  exceptions calees sur une epreuve (montage-demontage ou jours publics, chaque annee), jours
  feries, forcage temporaire (Suspendre 4 h / Lancer pendant 4 h), periode de validite.
- Lancement (`patrols.launch_plans`, a chaque passage du moteur) : `span_start` = au debut de chaque
  plage, `every` = toutes les N minutes (au quart d'heure) depuis le debut de la plage (mode « tout
  le temps » : depuis minuit). Creneau rate de plus de 10 min (moteur arrete) : pas rattrape. Une
  patrouille precedente du meme plan encore proposee / en cours : creneau saute (`last_launch.result`).
  `slot_key = "<plan>|<debut UTC>"` unique (index) : jamais deux lancements du meme creneau.
- Personne ne prend (refus, silence, aucune tablette, tablette imposee occupee) : `on_not_taken`
  leve l'alerte `patrol_not_taken` (definition semee dans `_ALERT_SEEDS` d'app.py, plein ecran) et
  cree une fiche `PCO.Information` close (`content_category.patrol_not_taken`), une seule fois.
- Page : onglet Planifications (cartes avec interrupteur, etat du moment, prochains lancements,
  dernier resultat, forcages), editeur en 5 etapes (quelle patrouille, a qui, combien de fois,
  periode, quand) avec le composant **`static/js/schedule_grid.js`** (+ `schedule_grid.css`) :
  modeles, saisie « de... a... », grille a peindre, semaines types, exceptions, feries, calendrier
  du mois + prochains lancements (`/api/patrols/plans/preview`). La page Detection garde sa propre
  copie de l'editeur (a migrer sur ce composant plus tard, sans changer son format).
- Suivi : resume du jour (en cours, en pause, en attente, terminees, non prises), prochains
  lancements, bouton « Envoyer une patrouille » direct.
