# Handshake — Scripts de communication avec le serveur SKIDATA

Ce dossier contient les scripts Python de communication avec le serveur **Handshake.Logic** (SKIDATA) via le protocole **HSHIF25** sur socket TCP (port 5205).

## Connexion

- **Serveur** : `192.168.2.10:5205`
- **Protocole** : TransferSockets — trame binaire encapsulant du XML UTF-16-LE
- **Format de trame** : `[DLE STX (2o)] [TELEGRAM_ID (4o BE)] [DATA_TYPE (2o BE)] [LENGTH (4o BE)] [XML UTF-16-LE] [DLE ETX optionnel]`

---

## Scripts Python

### Prototypes (versions itératives)

| Script | Description |
|---|---|
| `handshake.py` | **Premier prototype.** Envoie un `Inquiry Type="Counter"` global (tous les compteurs) et affiche la réponse brute en console. Pas de parsing, pas de fichier de sortie. Lecture de la réponse en un seul `recv(4096)` — ne gère pas les gros messages. |
| `handshake2.py` | **Inventaire complet de l'infrastructure.** Envoie un `Inquiry Type="Counter"` global, parse chaque `<Counter>` (Id, Name, Type, Entries, Exits, Current, Locked), puis produit trois sorties : `handshake_response.xml` (XML formaté), `handshake_data.csv` (Nom, Location ID, Type), `handshake_report.html` (rapport HTML avec tableau triable et statistiques : nombre de zones, portes, checkpoints, PDA, tripodes). |
| `handshake3.py` | **Interrogation d'un terminal par ID (interactif).** Demande un ID terminal en `input()`, envoie un `Inquiry Type="Information"` avec Counter/Location/Checkpoint, détecte automatiquement le type de réponse et affiche les détails (entrées, sorties, présents, état verrouillé). |
| `handshake4.py` | **Transactions d'un checkpoint pour la journée (interactif).** Demande un ID checkpoint en `input()`, envoie un `Inquiry Type="Transactions"` avec `TimeRange` du jour courant (format `YYYYMMDD`), parse et affiche chaque transaction en console. Sortie XML : `handshake_transactions_today.xml`. Note : le format `TimeRange` utilisé n'est pas conforme à la spec (devrait être `From`/`To` en attributs ISO 8601). |
| `handshake5.py` | **Compteur d'une location spécifique (interactif).** Demande ID + Type (Venue/Area/Gate/Checkpoint/Event/Layout) en `input()`, envoie un `Inquiry Type="Counter"` filtré par `<Location Id="..." Type="..."/>`. Affiche les détails complets : entrées, sorties, présents, capacité max, limite de réouverture, FirstEntries, FirstEntriesDay. Sortie XML : `handshake_location_{id}.xml`. |
| `handshake6.py` | **Transactions d'une location sur une période fixe.** Requête codée en dur : `Inquiry Type="Transactions"` avec `Option="128"` (nouveau layout), filtré sur la location 512 (Gate) pour le 2025-02-18. Sortie XML : `handshake_response.xml`. Script de test ponctuel, pas paramétrable. |

### Scripts de production

| Script | Description |
|---|---|
| `requete_simple.py` | **Dump de toutes les transactions (première page).** Envoie un `Inquiry Type="Transactions" Option="128"` avec une fenêtre temporelle maximale (1970–2100), récupère la première page de résultats et la sauvegarde dans `handshake_response.xml`. Pas de pagination, pas de parsing. |
| `all.py` | **Récupération paginée de TOUTES les transactions.** Envoie des `Inquiry Type="Transactions" Option="128"` en boucle avec pagination via `LastTransactionId`. Chaque page est appendue dans `handshake_all_transactions.xml`. La pagination s'arrête quand `LastTransactionId` ne change plus. Ouvre une nouvelle connexion TCP par page. |
| `requete_arbo.py` | **100 dernières transactions (les plus récentes en premier).** Envoie un `Inquiry Type="Transactions"` avec `MaxTransactions="100"`, `Option=131328` (bit 17 = newest first + bit 8 = erreurs incluses). Sauvegarde dans `handshake_last_transactions.xml`. Détecte `NotComplete` mais ne pagine pas automatiquement — pagination manuelle via `LAST_TRANSACTION_ID`. |
| `requete_day.py` | **Collecte complète d'une journée avec pagination automatique et export CSV.** Envoie des `Inquiry Type="Transactions"` paginés (100 par page) sur une fenêtre `From`/`To` configurable. Connexion TCP unique pour toute la pagination. Parse chaque transaction (direction entrée/sortie, conversion UTC → Europe/Paris, Venue/Area/Gate/Checkpoint). Sorties : CSV (`transactions_YYYY-MM-DD.csv`) + XML par page. Le script le plus complet pour l'extraction de transactions. |
| `requete_bdd_controle_acces.py` | **Polling Counter → MongoDB (v1).** Interroge en boucle (toutes les 30s) un ensemble de locations (628=Enceinte, 511=Musée, 629=Panorama, 926=Ouest) via `Inquiry Type="Counter"`. Parse les compteurs et insère chaque relevé dans MongoDB (`titan.data_access`). Une connexion TCP par location par cycle. Mode DEV/PROD configurable. |
| `requete_bdd_controle_accesv2.py` | **Polling Counter → MongoDB (v2, version robuste).** Même principe que v1 mais avec : connexion TCP persistante par cycle (une seule connexion pour toutes les locations), gestion du KEEPALIVE (détection et réponse automatique), logging hexadécimal bas-niveau des trames, DataType `0x0001` au lieu de `0x0003`, intervalle de 180s, ajout du checkpoint 304 (Tripode musée). Version de production. |
| `live_controle.py` | **Collecte temps réel unifiée — version de production actuelle.** Lancé par le Task Scheduler Windows toutes les 2 minutes, piloté par le document `___GLOBAL___` dans MongoDB (`titan.data_access`). Combine 3 fonctions en un cycle unique sur une connexion TCP persistante : **(1) Inventaire Counter global** (première exécution après activation) — requête `Inquiry Counter` globale, upsert de tous les compteurs dans `titan.hsh_structure`. **(2) Collecte paginée des transactions** — fenêtre de 3 min (journée entière en mode `--dev`), curseur `LastTransactionId` entre les cycles, stockage des erreurs (status ≠ 0) dans `titan.hsh_erreurs` avec labels lisibles (ticket illisible, accès refusé, etc.), enrichissement de l'arbre de structure (Checkpoint → Gate → Area → Venue) dans `titan.hsh_structure`. **(3) Polling Counter des locations sélectionnées** — interroge les locations choisies depuis le front-end (`locations_selectionnees` dans `___GLOBAL___`), insère les compteurs (entrées, sorties, présents, verrouillé) dans `titan.data_access`. Pagination : max 100 tx/page, Option 384 (nouveau layout XML + erreurs). Timeouts : 5s connexion, 5s Counter, 15s Transactions. Met à jour `cron_status.json`. |
| `live_controle.bat` | Wrapper batch pour `live_controle.py` : force l'encodage UTF-8 (`chcp 65001`, `PYTHONIOENCODING=utf-8`, `-X utf8`) et utilise l'interpréteur Python de production (`titan_prod`). |

---

## Fichiers de données

| Fichier | Contenu |
|---|---|
| `handshake_response.xml` | Dernière réponse XML brute (compteurs globaux ou transactions) |
| `handshake_report.html` | Rapport HTML des compteurs avec tableau triable |
| `handshake_data.csv` | Export CSV des compteurs (Nom, Location ID, Type) |
| `handshake_last_transactions.xml` | 100 dernières transactions (requete_arbo.py) |
| `handshake_all_transactions.xml` | Toutes les transactions paginées (all.py) |
| `handshake_transactions_today.xml` | Transactions du jour pour un checkpoint |
| `handshake_location_{id}.xml` | Compteur d'une location spécifique |
| `handshake_arborescence.xml` | Réponse d'une tentative de requête Location (erreur) |
| `transactions_YYYY-MM-DD.csv` | Export CSV d'une journée de transactions (requete_day.py) |
| `transactions_YYYY-MM-DD_page_NNN.xml` | Pages XML d'une journée (requete_day.py) |
| `structure_hsh.xml` | Réponse d'erreur "Unknown Ticketing Server" (Issuer=5) |
| `structure_handshake.html` | Documentation HTML de la structure HSH |
| `handshake_arborescence.csv` | Export CSV de l'arborescence (minimal) |
| `handshake_arborescence_report.html` | Rapport HTML de l'arborescence |

## Documentation

| Fichier | Contenu |
|---|---|
| `handshake-xml-interface-spec V2.34.pdf` | Spécification officielle SKIDATA du protocole HSHIF25 (110 pages) |
