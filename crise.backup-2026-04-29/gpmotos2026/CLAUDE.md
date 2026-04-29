# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Nature du projet

Mini-site HTML/CSS/JS **purement statique** (aucun build, aucune dépendance, aucun test) servant de tableau de bord pour l'**exercice de gestion de crise ACO — GP Moto 2026** (scénario : effondrement de la tribune T11, dimanche 10 mai 2026). Tout le contenu est en français.

Le site est destiné à être ouvert localement par les animateurs et la cellule de crise pendant l'exercice. Pas de backend, pas de stockage serveur ; seul un `localStorage` est utilisé pour mémoriser l'état de l'exercice.

## Lancer le site

Aucune commande de build ni de test. Pour développer, servir le dossier en HTTP local (les iframes ne fonctionnent pas en `file://` sur tous les navigateurs) :

```bash
python3 -m http.server 8000   # puis ouvrir http://localhost:8000
```

Ouvrir directement `index.html` fonctionne pour la majorité des vues mais peut casser le viewer d'iframes selon le navigateur.

## Architecture

### Trois couches de fichiers

- `index.html` — **shell de l'application** : header, sidebar, dashboard, vues internes (fiches, inputs, dispositions PCORG/PCA, cellule, viewer), modal média, horloges. Toutes les données (`fichesData`, `inputsData`, `csvRows`, `csvHeaders`) sont **hardcodées** en haut du `<script>` final. Toutes les vues vivent dans le même HTML et sont activées par `showView(name)` qui bascule la classe `.active`.
- `files/*.html` — **11 fiches animateur autonomes**, chacune avec ses propres styles et son propre `<script>` de print. Elles sont chargées dans une iframe (`#doc-frame`) par `openDoc(file, title)` (index.html:1909). Une fiche peut aussi s'ouvrir dans un nouvel onglet via `openDocInTab()`.
- `input/*` — **23 médias d'inject** (photos, vidéos, PDF, 1 CSV simulé en JS). Référencés par `inputsData` dans `index.html`. Photos et vidéos s'affichent dans une modal navigable avec `showModalContent()` (index.html:1968) ; les PDF s'ouvrent dans un nouvel onglet ; le CSV est rendu via `openCSV()` à partir des données JS hardcodées.

### Communication parent ↔ iframe (pattern important)

Le shell diffuse un **état d'exercice** (`prep` / `running` / `done`) que les fiches en iframe consomment :

1. `index.html` : bouton `#exercise-badge` → `cycleExerciseState()` → `applyExerciseState()` qui sauvegarde dans `localStorage['aco-exercise-state']` puis appelle `broadcastExerciseState()` (index.html:1919) qui fait `postMessage({type:'exerciseState', state})` vers l'iframe.
2. Une fiche peut **demander** l'état au chargement en envoyant `{type:'requestExerciseState'}` au parent (voir `files/01_timeline_animateur-V4.html:633`).
3. La timeline (`01_timeline_animateur-V4.html`) utilise cet état pour pause/play automatiquement son curseur de chronologie.

Si tu ajoutes une fiche qui doit réagir à l'état d'exercice, copier ce pattern : listener `message` côté enfant + `requestExerciseState` au chargement.

### Convention horaire

L'exercice a lieu en heure réelle mais le scénario se déroule **+10 jours et 4 heures plus tard**. La constante `SIM_OFFSET_MS = (10*24+4)*3600*1000` (index.html:2088) pilote les deux horloges affichées sur le dashboard via `tickClocks()`. La même convention est rappelée en clair dans plusieurs fiches — la modifier impose de mettre à jour ces fiches en parallèle.

### Index des fiches et inputs

Ajouter ou retirer un document **n'est pas un simple ajout de fichier** : il faut aussi mettre à jour, dans `index.html` :
- `fichesData` (vers la ligne 1737) pour une fiche `files/`
- `inputsData` (vers la ligne 1751) et `filtersDef` (counts) pour un média `input/`
- éventuellement les compteurs visibles dans le dashboard (`stats-group`, badges sidebar) et dans la sidebar (`<a class="nav-item" data-doc="...">`)

Le CSV `10-T11_liste_billets.csv` existe physiquement dans `input/` mais est **affiché depuis les tableaux `csvHeaders`/`csvRows`** hardcodés dans `index.html` (vers la ligne 2002), pas depuis le fichier. Modifier l'un sans l'autre crée une divergence.

## Conventions de contenu

- Sensibilité de l'exercice : noms et numéros de billets sont **fictifs / d'exercice** ; les données ressemblent à des coordonnées réelles mais sont anonymisées (`06.XX.XX.XX.XX`, `***` dans les emails). Garder ce niveau d'anonymisation pour toute donnée ajoutée.
- Tous les PDFs et fichiers d'inject portent le suffixe `_exercice` quand ils miment un document officiel, pour éviter toute confusion s'ils fuitent du contexte de l'exercice.
- Les fiches utilisent une convention de couleurs ACO (`--aco-navy`, `--aco-red`, `--aco-orange`, etc.) répétées dans chaque fichier — il n'y a pas de feuille de style partagée, modifier une couleur globale demande de toucher chaque fichier.

## Pièges fréquents

- `index.html` est un seul fichier de ~2100 lignes mêlant CSS, HTML et JS. Utiliser `Read` avec `offset`/`limit` plutôt que de relire l'intégralité.
- Les chemins de fichiers contiennent des **espaces** (`GP motos 2026`, `5-effondrement T11.mp4`, `8-image aerienne tribune.png`) et sont passés à `encodeURI()` côté JS. Toute manipulation côté shell doit être `quotée`.
- Le dossier vit dans **Dropbox**. Éviter les opérations destructives en masse — la synchro peut amplifier l'erreur.
- Pas de git. Avant toute refonte importante, proposer à l'utilisateur de faire une copie de sauvegarde du dossier.
