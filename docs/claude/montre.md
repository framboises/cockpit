<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## Montre connectée (app Connect IQ, `garmin/cockpit-watch/`)

Une app Garmin (tactix 8 Solar / fēnix 8 Solar 51 mm, sideload uniquement)
affiche au poignet du directeur des opérations adjoint huit pages en cycle
(HAUT/BAS) : tableau de bord, alertes, main courante PC org, trafic, météo,
fréquentation, guidage, timeline ; plus un menu de saut (MENU) et une page
« Pics par édition ».
Authentification par jeton Bearer émis depuis `/watch-admin`. Documentation
complète dans `garmin/cockpit-watch/README.md` — cette section ne couvre que
ce qui touche le code serveur Cockpit.

### `trafic_etat.py` et `meteo_etat.py` — pourquoi ils existent

Ni `traffic.py` ni `meteo.py` ne sont importables par un module de calcul
sans backend Flask : **les deux importent `app`** (pour le blueprint, la
config, les helpers Mongo partagés), et `app.py` déclenche son cycle d'auth
et ses collecteurs au chargement. Le payload de la montre (`watch_api.py` →
`watch_pages.py`) doit pourtant produire le même verdict trafic et la même
consigne météo que les murs (`circulation.html`, `meteo_mur.html`), sans
importer ni `app`, ni relancer ses collecteurs, ni dupliquer les seuils.

La solution retenue : extraire le calcul pur, sans aucun import Flask, dans
deux modules dédiés que `traffic.py`/`meteo.py` **et** `watch_pages.py`
consomment tous les deux :

- **`meteo_etat.py`** — extraction complète : `meteo.mur()` (route qui sert
  le mur) et `watch_pages.build_meteo()` appellent **littéralement la même
  fonction**, `meteo_etat.etat_mur(db, now)`. Zéro risque de divergence par
  construction — ce n'est pas une réimplémentation parallèle, c'est le même
  code exécuté deux fois. Vérifié à la tâche 14 : `build_meteo` ne réinvente
  aucun seuil (rafale, WBGT, orage), il ne fait que choisir la consigne la
  plus grave dans la liste déjà triée par gravité que rend `etat_mur`, et
  réduire `couleur_jour` sur l'échelle 0-3 du mur via
  `meteo_etat.ORDRE_COULEURS`.
- **`trafic_etat.py`** — situation différente : c'est un **port**, pas un
  partage. Le calcul de sévérité par axe et le verdict global du mur vivent
  en JavaScript, inline dans `circulation.html` (`classify()` ligne ~587,
  `computeVerdict()` ligne ~728) — aucun moyen de les faire appeler par le
  backend Python. `trafic_etat.verdict_global()` reproduit fidèlement la
  structure de décision de `computeVerdict()` (accident en zone ou sévérité
  ≥ 4 → CRITIQUE ; == 3 → TENSION ; == 2 → VIGILANCE ; sinon FLUIDE), et
  `parse_route_name`/`classify_congestion` sont *déplacés* depuis
  `traffic.py` (mêmes fonctions, nouvel emplacement — pas une réécriture).

  **La tâche 14 a trouvé et corrigé une divergence réelle sur la sévérité
  par axe qui alimente ce verdict.** `classify_congestion` (Python, utilisé
  par `/trafic/waiting_data_structured` et les autres panneaux du mur) ne
  regarde que le ratio courant/historique (paliers 0,9/1,2/1,6/2,5).
  `classify()` (JS, le panneau « Axes » de `circulation.html`) exige un
  **double verrou** : ratio élevé **ET** perte de temps absolue en secondes
  (paliers 1,35/1,6/2,2/3,0, chacun avec un plancher de retard). Son
  commentaire d'origine dit pourquoi (`circulation.html:594-596`) : *« un
  tronçon court (ex. 20s → 80s) a un gros ratio mais ne coûte qu'une minute :
  ce n'est pas un bouchon »* — sans ce plancher, un tronçon négligeable
  remonte en fausse alerte, précisément ce que le double verrou existe pour
  écarter. `trafic_etat.severite_axe()` porte maintenant ce double verrou à
  l'identique, et `watch_pages.build_trafic()` l'utilise pour recalculer la
  sévérité de chaque terrain (`terrain["severity"] =
  trafic_etat.severite_axe(terrain)`) **avant** de la passer à
  `verdict_global()` — donc pour la sévérité affichée par terrain ET pour
  le verdict global. Vérifié : le cas trouvé à la tâche 14 (axe unique,
  ratio 1,5, retard 500 s) donne désormais sévérité 1 des deux côtés (vd
  FLUIDE, plus VIGILANCE côté montre) ; le cas du tronçon court (15s → 45s,
  ratio ×3, retard 30 s) donne sévérité 0 des deux côtés, alors que
  `classify_congestion` l'aurait classé « bouchon », sévérité 4.

  ⚠️ **`classify_congestion` n'a pas été touchée** — elle continue
  d'alimenter `/trafic/waiting_data_structured`, dont la tâche 1 a prouvé le
  payload identique à l'octet près ; la modifier aurait cassé cette garantie
  et changé l'affichage d'autres panneaux du cockpit. Les deux fonctions
  coexistent désormais : `classify_congestion` pour les panneaux existants,
  `severite_axe` pour la montre.

  **Un second écart, indépendant de la formule, a été trouvé puis corrigé
  dans la même tâche : le PÉRIMÈTRE des axes couverts par le verdict
  global.** `agreger_terrains()` (montre) n'agrège que les noms préfixés
  `##`, `#I`, `#O`, et **exclut les axes `#P` (parkings)** ; le panneau
  « Axes » du mur les inclut (`circulation.html:793`,
  `r.category === "pkg_aa" || r.tag === "P"`, redondant puisque P est déjà
  dans pkg_aa). Un parking `#P` très chargé restait donc **invisible au
  verdict global de la montre jusqu'au niveau CRITIQUE** — un manque
  silencieux, jamais une fausse alerte, exactement la faute qu'un outil de
  supervision ne doit jamais commettre (une page qui paraît calme alors
  qu'elle ne sait pas). Corrigé par `trafic_etat.pire_severite_mur(routes)` :
  reproduit le même ensemble d'axes que le mur (toutes les routes taguées
  `I`/`O`/`neutral`/`P`, chacune classée **individuellement** par
  `severite_axe`, sans agrégation par terrain — comme le fait `classify()`
  côté mur), et alimente désormais `verdict_global()` à la place du maximum
  pris sur les seuls terrains agrégés. `watch_pages.build_trafic()` :

  ```python
  terrains = trafic_etat.agreger_terrains(routes)      # inchange, pour "r"
  for terrain in terrains:
      terrain["severity"] = trafic_etat.severite_axe(terrain)
  pire = trafic_etat.pire_severite_mur(routes)          # pour "vd", pas les terrains
  ```

  **Conséquence assumée** : la montre peut afficher un verdict élevé sans
  qu'aucun terrain de la liste `r` (page Trafic) ne l'explique, quand la
  cause est un parking — la page ne liste que les axes d'entrée/sortie, pas
  les parkings, choix d'affichage délibéré. C'est le bon compromis : un
  verdict visible sans terrain qui l'explique renvoie au moins
  l'utilisateur regarder le mur ou le cockpit ; un verdict qui reste FLUIDE
  ne renvoie nulle part. Vérifié : un axe `#P` à ratio ×6, retard 2500 s —
  invisible dans `agreger_terrains()` (`r == []`) — fait désormais monter
  `vd` à 3 (CRITIQUE), identique des deux côtés (avant la correction : `vd`
  restait à 0, FLUIDE).

  ⚠️ **Un écart résiduel, plus fin, n'a volontairement pas été corrigé —
  et il ne touche que l'AFFICHAGE (`r`), plus le verdict global** : pour
  construire la liste `r` de la page Trafic, `agreger_terrains()` **agrège**
  les axes d'un même `(terrain, direction)` (somme des temps
  courant/historique de tous les tronçons) avant de classer, quand le mur
  classe **chaque route individuelle** puis prend le pire pour son propre
  panneau. Cet écart-là est à double sens, pas un manque silencieux : le
  ratio agrégé ne peut pas dépasser le pire ratio individuel (moins
  alarmiste que le mur), mais le retard agrégé est la somme des retards de
  tous les tronçons et peut dépasser celui de n'importe quel axe pris seul
  (plus alarmiste que le mur) — le résultat dépend des données, dans un sens
  ou dans l'autre. Le verdict global (`vd`), lui, n'est plus concerné : il
  vient maintenant de `pire_severite_mur`, calculé sur les routes non
  agrégées, exactement comme le mur. Corriger la sévérité *affichée* par
  terrain demanderait de faire lire à la montre les routes individuelles
  plutôt que l'agrégat, rien que pour l'affichage — hors périmètre de cette
  tâche, qui portait sur le verdict.

### Le champ `mr` et la règle du bloc absent

Deux conventions transverses aux quatre blocs de pages (`mc`/`tr`/`me`/`st`
dans le payload, cf. `watch_pages.py`) :

- **`mr`** (motif) distingue, en mode `past` (hors événement), un arrêt
  volontaire (`inactif` — le live-contrôle est désactivé côté cockpit, l'état
  normal 350 jours par an) d'un collecteur en panne (`sans_releve` — le
  drapeau dit encore actif mais plus aucun relevé n'arrive). Aucune des deux
  garde seule ne suffit : le drapeau attrape l'arrêt propre en une seconde,
  la fraîcheur attrape le collecteur planté que le drapeau mentirait
  indéfiniment. Les confondre sous un seul « édition terminée » perdrait
  l'information qui dit si une intervention est nécessaire.
- **Un bloc absent (`None`) reste `None` dans le payload**, jamais remplacé
  par un objet à champs vides. Chaque constructeur de `watch_pages.py` ne
  lève jamais — une source injoignable rend `None`, et l'appelant continue de
  servir les autres pages. Côté Monkey C, chaque vue de page (`TraficView`,
  `MeteoView`, etc.) teste explicitement ce `null` et affiche « indisponible »
  plutôt que de tenter un accès qui ferait planter le rendu — sauf
  `TraficView`, qui garde sa structure fixe (tirets) plutôt qu'un message,
  pour ne pas faire sauter la mise en page entre deux relevés.

### Deux pièges vérifiés sur ce lot

- **`monkeyc --build-stats` ne mesure pas ce qui est compilé par espace
  mémoire** (device app / glance / fond). Vérifié par expérience contrôlée à
  la tâche 8 : une fonction morte, jamais appelée, sans aucune annotation
  `(:glance)` ni `(:background)`, gonfle la glance ET le fond du même montant
  que si elle y était réellement exécutée. Cette métrique mesure la taille du
  binaire produit, pas son partitionnement logique. **La garantie qu'un
  module ne s'exécute jamais en glance/fond est structurelle** (absence
  d'annotation `(:glance)`/`(:background)` sur le fichier, et données rangées
  dans une clé `Application.Storage` que ces deux espaces ne lisent jamais),
  pas une lecture de `--build-stats`.
- **Sur un cadran rond, le texte est ancré par le HAUT**, jamais par son
  centre vertical (pas de `TEXT_JUSTIFY_VCENTER` dans ce projet) : un bloc
  posé à `y` occupe `[y, y + hauteur police]`. Un bloc de la moitié haute de
  l'écran est donc contraint par son **sommet** (la corde disponible se
  resserre en montant vers le bord), un bloc de la moitié basse par sa
  **base** (elle se resserre en descendant). Vérifier le mauvais bord donne
  un calcul de largeur disponible qui semble juste et laisse pourtant
  déborder le texte à l'écran réel. Ce piège s'est refermé **quatre fois**
  sur ce projet — toujours en vérifiant le bord qui ne contraint pas le bloc
  concerné.

  ⚠️ **La même règle vaut pour l'argument passé à `largeurUtile`**, et c'est
  là qu'elle a été oubliée le plus longtemps : appeler `largeurUtile(dc, y)`
  mesure la corde à l'ancre du texte, donc **surestime la place** pour tout
  bloc de la moitié basse. Écarts réellement mesurés avant correction : 36 px
  sur la ligne « édition » de `FrequentationView`, 18 px sur la seconde ligne
  de consigne météo — celle sur laquelle `CONSIGNE_MAX` avait justement été
  calibré. La fonction prend désormais la hauteur du bloc
  (`Pages.largeurUtile(dc, y, hauteur)`) et évalue le bord le plus éloigné du
  centre, y compris quand le bloc est à cheval sur celui-ci.

- **L'écran de la cible fait 280 × 280, pas 454 × 454.** Le fenix 8 *Solar*
  porte un écran MIP de 280 px ; c'est le fenix 8 AMOLED qui fait 454. Toutes
  les sondes de mise en page de ce projet ont créé leur tampon en **imposant**
  `454 × 454` au lieu de lire `System.getDeviceSettings()`. Elles mesuraient
  donc une page 1,6 fois trop haute **et un rayon de cadran de 227 au lieu de
  140** : positions verticales et largeurs disponibles étaient fausses
  ensemble. Résultat sur la vraie montre : quatre pages sur six débordaient,
  le pied de page se superposait au contenu, et un itinéraire sur deux de la
  page Trafic était dessiné hors écran.

  Ce défaut a traversé quatorze tâches, deux relectures d'ensemble et une
  centaine de tests, parce que **tout le monde partageait la même hypothèse
  fausse** : les tests de dessin prouvent qu'un rendu ne lève pas, et dessiner
  hors écran ne lève pas. Il a été trouvé par l'utilisateur, à l'œil, sur sa
  montre. **Ne jamais coder une dimension d'écran en dur, nulle part** — pas
  même dans une sonde jetable. `DebordementTest.mc` échoue désormais si un
  bloc sort du disque inscrit ou chevauche le pied, sur la taille lue à
  l'exécution.

### Le jeton est une CONSTANTE DE CODE, pas une Property

⚠️ **`Application.Properties` survit au sideload, comme `Storage`.** Une
valeur écrite un jour dans les réglages **écrase la valeur par défaut
compilée**, et c'est elle qui part sur le réseau. Le binaire peut porter le
bon jeton (`strings` le confirme) pendant que la montre en envoie un autre.

Cas réel : un ancien jeton laissé actif le temps d'une transition, révoqué
cinq heures plus tard. La montre s'est arrêtée net en 401 — et **cinq
reconstructions avec le nouveau jeton n'ont rien changé**, puisqu'elle
n'envoyait pas celui-là.

Le jeton vit donc dans `source/Jeton.mc` (`const VALEUR`), rempli par
`build-avec-jeton.sh` dans une copie temporaire. `Jeton.valeur()` donne la
priorité à la constante et ne retombe sur la Property qu'en repli — ce qui
donne le dernier mot à la construction, jamais à un reliquat de réglage.

Le pied de page affiche l'**empreinte** (4 caractères) du jeton employé sur
les erreurs `401` et `jeton absent` : c'est ce qui aurait tranché en cinq
secondes au lieu de cinq reconstructions.

### `Application.Storage` survit au sideload

⚠️ Réinstaller l'app ne vide **pas** son stockage. Un code d'erreur, un
cache, un compteur mémorisés par une version antérieure survivent à la
réinstallation censée les corriger.

Constaté : « jeton refuse » est resté affiché après **quatre
reconstructions successives**, alors que le jeton compilé était accepté par
le serveur (HTTP 200) et bien présent dans le binaire (`strings`). Le
message ne venait pas du serveur, il venait du stockage — écrit une fois,
effacé seulement après une requête réussie.

`CockpitApp.onStart` appelle donc `Api.oublierErreur()`. **Au démarrage, la
montre ne sait rien de son lien au serveur** ; toute valeur qui prétend le
contraire est fausse. Même principe qu'`Alerting`, qui ne vibre jamais sans
référence antérieure.

Règle générale : toute donnée de Storage qui décrit un ÉTAT COURANT (et non
un historique) doit être remise à zéro au démarrage, ou porter un numéro de
version. Sinon elle finit par mentir après une mise à jour.

### Météo : `consignes` ET `contraintes`, jamais l'une seule

⚠️ `meteo_etat.etat_mur` rend **deux** listes. `consignes` ne se déclenche
qu'aux seuils hauts (rafale ≥ 60 km/h, WBGT danger, orage avéré, pluie
≥ 5 mm) ; `contraintes` porte les quatre décisions permanentes (vent,
chaleur, orage, sol) et descend jusqu'à la **vigilance** — rafale ≥ 40 km/h.

`watch_pages.build_meteo` ne lisait que `consignes` : trou de 40 à 60 km/h
où le mur parlait et la montre se taisait. Constaté le 17/08/2026, rafale
prévue à 44,8 km/h à 17 h — visible widget et mur, absente du poignet.
C'est la contradiction que le commentaire de la fonction interdit : aucun
seuil n'était réinventé, mais la mauvaise liste était lue.

Les quatre contraintes sont **toujours présentes**, la plupart en `normal` :
seules les non-normales sont relayées. Et l'orage en vigilance porte une
consigne **vide** côté mur — un titre sans action est écarté.

### Les alertes de la montre ne filtrent pas sur l'événement

⚠️ **`watch_state.read_active_alerts` ne filtre QUE sur `expiresAt`.**
Filtrer sur event/year — ce que faisait la première version — perdait 100 %
des alertes hors période d'événement.

`alert_engine.build_context` ne résout un événement que si un paramétrage
couvre la date du jour (repli à 7 jours). Sinon les handlers écrivent
`context.get("event", "")` → **chaîne vide** (`alert_engine.py:490`), qu'aucun
couple réel ne matche, ni en mode `pinned` ni en mode `auto`. Or les alertes
trafic, météo et main courante tournent, elles, toute l'année.

Le cockpit ne filtre pas non plus (`app.py:4154`). La sélection est faite
par les **slugs cochés dans `/watch-admin`** (`select_alerts`), qui portent
aussi le niveau de gravité : un filtre, pas deux.

Les SOS terrain échappent au problème — `field.py:3983` écrit event/year
depuis la tablette enrôlée.

### La ligne d'axe suit le bloc « Temps d'accès », pas le mur

⚠️ **Deux références distinctes, et il ne faut pas les confondre.** Le mur
`circulation.html` donne les **seuils de sévérité** et le **verdict global**
(`severite_axe`, `verdict_global`). Le bloc « Temps d'accès » de la page
principale (`static/js/traffic.js`) donne le **format des chiffres** — et
c'est lui la bonne référence pour une liste d'axes.

Concrètement : `4m 20s` et non `24'` (sur des trajets de 33 s à 25 min, une
unité implicite se devine mal), `+45s` **toujours affiché, `+0s` compris**,
et `ENT`/`SOR`/`PKG` plutôt que des chevrons. `Fmt.duree` et `Fmt.retard`
sont des ports exacts de `formatTime` et `formatDelay`.

⚠️ **Masquer le retard nul était le défaut signalé à l'usage** : un axe
fluide (retard connu, nul) et un axe dont on ignore le retard se
ressemblaient. Un sabotage l'a confirmé après coup — le masquer laissait
264 tests au vert.

⚠️ Ce format demande **176 px dans son pire cas** sur une corde utile de
186 : il ne reste rien pour le nom sur une seule ligne. D'où **deux lignes
par axe**, et quatre axes par écran au lieu de six. Le payload transporte
donc des **secondes**, plus des minutes arrondies.

⚠️ **Le tag Waze `#P` designe les AUTOROUTES (A28, A11), pas les
parkings.** Les parkings sont en `##`, sans direction. Le cockpit le prouve
trois fois : onglet « Autoroutes » filtré sur `tag === "P"`, onglet
« Parkings » sur `category === "pkg_aa" && tag !== "P"`, et `tagLabel("P")`
qui rend l'icône `fork_right`. La première version de la page affichait
`PKG` sur les autoroutes et `--` sur les parkings — les deux faux, en sens
inverse. Un axe sans direction rend désormais une colonne **vide**, jamais
un tiret : le tiret signifie « inconnu » partout ailleurs dans cette app.

### Fraîcheur Waze : `latest`, jamais l'historique

Le collecteur externe (`waze_collector.py`, tâche planifiée **toutes les
2 min**) réécrit `{"_id": "latest"}` dans `waze_trafic` et `waze_alerts`, et
dépose un snapshot dans `waze_*_history` toutes les 16-18 min. **Lire
l'historique ferait annoncer un quart d'heure de retard en permanence.**

⚠️ **Le mur masque les pannes de collecteur, la montre non.** `traffic.py`
a un troisième étage : au-delà de `MONGO_MAX_AGE_SECONDS` (300 s), il
appelle l'API Waze en direct. La montre lit uniquement Mongo. Un « maj
42 min » au poignet alors que le mur paraît normal signale donc un
**collecteur arrêté**, pas un défaut d'affichage.

### Indicateur de pagination (`Pages.dessinerPagination`)

Losanges en haut à droite, plein pour la page courante. Partagé par les
deux pages à livret (Trafic, Timeline).

⚠️ **Se tait au-delà de 8 pages** : la corde ne fait que 122 px à cette
ordonnée, et des losanges indistinguables valent moins que pas de losanges
du tout — le compteur du pied prend le relais.

⚠️ **En haut à droite et non centré** : l'en-tête de page occupe déjà toute
la corde à son ordonnée.

### Timeline : ce qui tombe dans les 12 h à venir

8ᵉ page. **Aucun calcul métier n'est réécrit** :
`pcorg_summary.get_upcoming_timetable` existait déjà et porte la
factorisation des ouvertures simultanées — 65 vignettes brutes deviennent
9 lignes sur une journée de course. `watch_timeline.py` ne fait que
compacter sa sortie.

⚠️ **Le délai est calculé au POIGNET, à partir d'un epoch.** Un « dans
42 min » formaté côté serveur serait juste à l'émission et faux trois
minutes plus tard — ce qui est précisément l'âge que peut avoir un relevé.
Rien de formaté ne voyage.

⚠️ **Deux sources, délibérément.** `nx` (la prochaine vignette seule,
~70 octets) voyage dans le payload principal ; la liste complète (~570) vit
sur `/timeline`, requêtée à l'ouverture de la page. Ce n'est pas de la
redondance : le héros s'affiche depuis le cache dès l'ouverture, même hors
de portée du téléphone. La liste ne pouvait de toute façon pas entrer dans
le payload — il reste 182 octets sur le budget de 2 Ko.

⚠️ **`nx` est calculé dans les DEUX modes**, contrairement à `mc` et `st`.
Un sabotage a montré que la garde `mode == "live"` ne coûtait rien (en mode
auto hors événement, `resolve_event` rend déjà `(None, None)`) et retirait
la timeline **précisément quand elle sert le plus** : la veille et le matin,
pendant le montage, avant l'armement du live-contrôle. `/timeline`, lui,
garde les deux gardes en mode auto — sans elles il servirait la timeline
d'une édition terminée.

⚠️ **Le compte de factorisation ne cède jamais à la troncature.** Trouvé à
la sonde : `Ouverture des tribunes nord es (12)` sortait
`…nord es (1` — un chiffre **faux**, qui annonce une tribune là où il y en a
douze. Le nom est le seul élément élastique, comme sur la ligne d'axe de
`TraficView`.

### Guidage : un point GPS poussé du cockpit vers une montre

`/watch-admin` porte une carte : on clique un point, on le nomme, on choisit
une montre, il part. La montre l'affiche (flèche, distance) sur une 7ᵉ page,
et START le pousse dans ses **lieux enregistrés natifs** pour que la
navigation Garmin le reprenne. Documentation complète dans
`garmin/cockpit-watch/README.md` ; trois pièges valent d'être ici.

⚠️ **`/state` sert un payload mis en cache 20 s, IDENTIQUE pour toutes les
montres.** Un point de guidage est adressé à une seule : il est donc ajouté
par `watch_api._avec_guidage` sur une **copie**, après le cache, à partir du
jeton porteur de la requête. L'écrire dans le payload caché ferait fuiter le
point d'une montre vers toutes les autres pendant 20 secondes. C'est le seul
endroit de l'API montre où le payload n'est pas commun, et donc le seul où
une fuite est possible.

⚠️ **Le GPS n'est allumé que pendant que la page Guidage est affichée** —
seul capteur continu de toute l'app. `CockpitView.entrerPage`/`quitterPage`
l'allument et l'éteignent, `onHide` est le filet à la fermeture. Cette
extinction est **invisible à l'écran**, donc invisible à tout test de dessin :
un sabotage a montré que la supprimer laissait 194 tests au vert.

⚠️ **Un texte tronqué tient parfaitement dans l'écran** — aucun contrôle
géométrique ne le signale. La corde au pied de page ne fait que **133,7 px**
sur ce cadran : `boussole indisponible` (164 px) sortait `boussole indispo`,
et `GPS faible . START = enregistrer` (255 px) sortait `GPS faible . STA`.
Trouvé à la sonde, jamais par un test. Tous les libellés du pied sont
désormais mesurés, et cinq tests comparent le texte **réellement dessiné** au
texte entier attendu.

### Piège d'outillage : lancer le simulateur au premier plan

Une commande lancée en arrière-plan depuis une session d'agent **ne réveille
pas cet agent** : il attend indéfiniment une notification qui n'arrivera pas.
Ce piège s'est refermé **trois fois** sur ce lot, pour une vingtaine de
minutes perdues à chaque fois, toujours sur `monkeydo` (le simulateur met une
à deux minutes à rendre ses tests, ce qui donne envie de le mettre en fond).

Lancer `monkeyc` et `monkeydo` **au premier plan** et attendre le résultat
dans la même invocation. Seul `connectiq` — le service graphique — se lance
en arrière-plan, parce qu'il doit rester vivant.
