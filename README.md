# COCKPIT – Timetable & Event Control

COCKPIT est une application web de supervision qui permet de suivre en temps réel la **timeline d’un événement** (24H, GP Explorer, etc.).  
Elle centralise toutes les activités (ouvertures/fermetures de portes, parkings, aires d’accueil, courses, animations) et offre des outils pour la **préparation**, la **visualisation** et le **pilotage opérationnel**.

---

## 🚀 Fonctionnalités principales

### Timeline
- Affichage des événements regroupés **par jour**, avec bandeaux "OUVERT / FERMÉ au public".
- Classement chronologique des activités (heures de début/fin).
- Regroupement automatique en **clusters** pour :
  - Parkings
  - Aires d’accueil
  - Portes
- Suppression automatique des doublons d’ouverture/fermeture à minuit (cas 24/24).

### Statuts de préparation
- Chaque événement peut être marqué comme :
  - **Non préparé**
  - **En cours**
  - **Prêt**
- Déduction automatique depuis les **TODO listes** associées.
- Affichage clair via des **pastilles colorées** (chips).

### TODO intégrées
- Gestion de tâches par événement :
  - Cases à cocher inline.
  - Sauvegarde instantanée en base.
  - Passage automatique à "Prêt" quand toutes les cases sont validées.
- Édition directe via le **drawer** (tiroir latéral).

### Drawer événement
- Vue détaillée d’un événement : date, horaires, catégorie, lieu, département, remarques, préparation, TODO.
- Fonctions rapides :
  - **Modifier**
  - **Dupliquer**
  - **Supprimer**

### Ajout & édition d’événements
- Formulaire modal pour ajouter un nouvel événement.
- Possibilité d’édition inline via le drawer.
- Catégories dynamiques chargées depuis le backend.

---

## 🟥 Ligne rouge "NOW"

L’application intègre une **ligne rouge horizontale** dans la timeline représentant l’**heure courante**.  
Cette ligne se comporte comme un repère visuel et une ancre de navigation.

### Fonctionnement
- La ligne est **fixe** dans la timeline (positionnée à ~100px du haut).
- L’application calcule quel est **l’événement courant ou à venir** en fonction de l’heure.
- La timeline **scroll automatiquement** pour caler cet événement sous la ligne rouge.

### Activation / désactivation
Un bouton dédié est disponible dans la barre supérieure :

- Icône ⏱️ (*schedule*) → **activer l’auto-scroll** (la ligne rouge apparaît).
- Icône ⏸️ (*pause*) → **désactiver l’auto-scroll** (la ligne disparaît).

---

## 🧪 Mode simulation (tests sans attendre l’heure réelle)

Pour faciliter les tests, l’application expose une **horloge simulable** via la console du navigateur.

### Commandes disponibles

- **Revenir en temps réel** :
```js
TimelineClock.useReal()
```

- **Simuler une heure précise** :
```js
TimelineClock.setSim("2025-09-26 14:35")
```
*(format : `YYYY-MM-DD HH:MM`)*

- **Obtenir l’heure courante utilisée par l’app** :
```js
TimelineClock.get()
```

- **Forcer un recalage manuel de la ligne rouge** :
```js
NowLineController._tick()
```

- **Activer/désactiver l’auto-scroll** :
```js
NowLineController.start()
NowLineController.stop()
NowLineController.toggle()
```

---

## ⚙️ Architecture

- **Frontend** : HTML, CSS, JavaScript (vanilla, Material Icons).
- **Backend** : Flask (Python) + MongoDB.
- **Organisation du code** :
  - `main.js` → gestion générale (sélections d’événement/année, drawer global, flash messages, navigation).
  - `timeline.js` → affichage chronologique, clustering, statuts, ligne rouge NOW, modales d’ajout/édition.

---

## 🎨 Esthétique

- Ligne rouge **fluide et lumineuse** (gradient rose/rouge, glow).
- Badge **NOW** affiché à droite de la ligne.
- Scroll animé en **smooth** pour lisibilité.
- Boutons cohérents avec le design global (icônes Material).

---

## 🛠️ Points techniques

- Chaque carte (`event-item`) est enrichie avec :
  - `data-date` (YYYY-MM-DD)
  - `data-minute` (minute de tri, calculée sur start/end)
- La ligne rouge utilise un offset fixe (`top:100px`) pour caler le scrolling.
- L’auto-scroll vérifie :
  - La date courante (réelle ou simulée).
  - Le prochain événement ≥ heure actuelle.
  - Sinon, le dernier événement de la section.

---

## ✅ Checklist d’utilisation

1. **Charger la page** et sélectionner un **événement + année**.
2. Cliquer sur le bouton **HUD** → la timeline et le paramétrage se chargent.
3. Activer le bouton **⏱️ schedule** pour afficher la ligne rouge.
4. Vérifier que la timeline suit bien l’heure réelle.
5. (Optionnel) Ouvrir la **console navigateur** pour simuler une heure avec `TimelineClock.setSim(...)`.

---

## 📌 Exemple visuel (schéma simplifié)

```
──────────────────────────────  ← Ligne rouge NOW
[14:30] Ouverture porte Est
[14:45] Début essais libres
[15:00] Ouverture parking P7
...
```

---

## 🔧 Installation & Lancement

### 1. Prérequis
- [Python 3.10+](https://www.python.org/)
- [MongoDB Community Server](https://www.mongodb.com/try/download/community)
- [Node.js](https://nodejs.org/) (optionnel si tu veux builder du JS plus avancé)
- Navigateur moderne (Chrome, Edge, Firefox…)

### 2. Cloner le projet
```bash
git clone https://github.com/toncompte/cockpit.git
cd cockpit
```

### 3. Créer un environnement virtuel Python
```bash
python -m venv venv
source venv/bin/activate   # Linux / macOS
venv\Scripts\activate    # Windows
```

### 4. Installer les dépendances Python
```bash
pip install -r requirements.txt
```

### 5. Lancer MongoDB
Assure-toi que ton service MongoDB est démarré :
```bash
mongod --dbpath /chemin/vers/tes/donnees
```

### 6. Lancer le serveur Flask
```bash
flask run
```
ou en production avec [Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/):
```bash
python -m waitress --port=5000 app:app
```

### 7. Accéder à l’application
Ouvre ton navigateur à l’adresse :
```
http://localhost:5000
```

---

Avec ce système, tu peux **tester n’importe quelle heure** sans attendre l’événement réel et vérifier que la timeline déroule correctement.
