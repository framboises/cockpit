# Contexte

Je suis Directeur des Opérations Adjoint sur un circuit automobile. Je veux
une application Garmin Connect IQ qui affiche en direct, à mon poignet, des
données opérationnelles remontées par mon PC Organisation, avec des alertes
sur seuils.

Backend existant : Flask / Python / MongoDB (base `titan_dev` en dev et `titan` en production) avec l'application cockpit. 

Montre cible : Tactix 8 Solar — Connect IQ 9.2.0

# Ce que je veux construire

## 1. Endpoint backend dans l'application cockpit

`GET /api/v1/watch/state`, auth `Authorization: Bearer <token>`.
Réponse JSON compacte, clés d'un caractère, < 2 Ko :

  t  timestamp unix de la donnée
  e  compteur d'entrées cumulé
  er débit d'entrées (pers./h)
  w  WBGT (°C, 1 décimale)
  wl niveau WBGT 0-3
  al liste des alertes actives : [{l: niveau, m: libellé court}]

Rate limit, lecture seule, token révocable, aucun accès direct à `titan_dev`
depuis l'extérieur.

## 2. App Connect IQ (le cœur du travail)

Un seul projet, trois points d'entrée :

- **Device app** : console détaillée, polling toutes les 1 min en pic /
  3 minutes en mode dégradé, affichage de l'âge de la donnée ("périmé depuis X min")
  si la dernière réponse a plus de 90 s.
- **Glance** : trois chiffres (entrées, WBGT, niveau d'alerte en code couleur),
  lus depuis le cache `Application.Storage`, sans requête réseau.
- **Background service** : `registerForTemporalEvent` à 5 min, entretient le
  cache et déclenche une vibration si `wl` franchit un seuil à la hausse.

Alertes : `Attention.vibrate` + `playTone`, uniquement sur *transition* de
niveau, jamais en boucle.

# Contraintes dures à respecter

- HTTPS avec certificat valide obligatoire (`makeWebRequest` refuse l'auto-signé).
- La requête est exécutée par le téléphone via Garmin Connect Mobile : l'endpoint
  doit être joignable depuis Internet.
- Token stocké dans les Properties de l'app (réglages Connect IQ), jamais en dur.
- Budget mémoire glance très serré : pas de parsing lourd, pas de bitmap.
- Background : 32 Ko de RAM, code minimal.
- Distribution par sideload du `.prg` (USB, dossier `GARMIN/APPS`). Pas de
  publication sur le store, ne prévois rien pour ça.
- Économie de batterie : cible 24 h d'usage continu.

# Méthode

1. Commence par me poser les questions bloquantes (modèle exact, version SDK
   installée, domaine de l'endpoint) — ne devine pas.
   Fais les vérifications sur les sdk garmin connect iq installée
2. Puis pose le `manifest.xml`, l'arborescence et les squelettes vides, et
   fais-moi valider avant d'écrire la logique.
3. Ensuite implémente brique par brique, en commençant par la device app avec
   des données mockées, testable dans le simulateur VS Code.
4. Pas de dépendance externe. Monkey C standard uniquement.
5. Un README avec les commandes de build, de test simulateur et de sideload.