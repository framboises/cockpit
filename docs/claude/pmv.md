<!-- Extrait de CLAUDE.md (racine du repo). Charge a la demande. -->

## PMV — remorques à panneau à message variable (`/pmv`)

Cockpit pilote directement les remorques PMV louées (contrôleur **Sigma 3000**,
fabricant QS, protocole **JetFileII**) pour y afficher des messages 96 × 64 au
bord des routes. Remplace le logiciel du fabricant (Sigma Play). Page réservée
au rôle **manager** ; registre, groupes, jumeau et « envoi sans sauvegarde »
réservés à **admin**.

Dossier de référence versionné : **`docs/pmv/`** (protocole à l'octet dans
`docs/SIGMA3000_PROTOCOLE.md`, implémentation validée sur remorque le 27/09/2026
dans `reference/`, vecteurs d'octets, jumeau `banc-essai/faux_panneau.py`,
éditeur d'origine, cahier des charges et prompt de départ).

### Architecture

```
templates/pmv.html + static/js/pmv.js + static/js/pmv_editor.js + static/css/pmv.css
        │ fetch + X-CSRFToken (jamais d'IP : un panneau_id du registre)
pmv.py            blueprint pmv_bp (/pmv, /api/pmv/*) : jobs, verrous, Mongo, planificateur
        │
pmv_panneau.py    dialogue TCP 9520 (classe Panneau), opérations test/envoi/restauration, DNS
        │
pmv_protocole.py  PUR (ni Flask ni Mongo) : image → BMP 5-6-5 → fichier-message → trames
```

**C'est le serveur qui parle aux remorques**, pas le navigateur (le relais
PowerShell du dossier d'origine est inutile ici). Chaque remorque est derrière un
routeur 4G joint par `info<PLAQUE>.ddns.net`, TCP **9520**, adresse JetFileII
`0x0101`. Le message est écrit en `D:\T\PMVED.Nmg` (flash) puis la liste de
lecture `SEQUENT.SYS` le fait jouer. ~2 s par image quand la 4G va bien (0,7 s
contre le jumeau).

### Règles de dialogue — NON NÉGOCIABLES (recopiées en tête de `pmv_panneau.py`)

Chacune vient d'un incident réel sur remorque :

1. **Octet `00` après CHAQUE trame** — sans lui le panneau ne répond jamais.
2. **Numéro de séquence unique sur toute la session TCP** — sinon un accusé
   tardif est pris pour la réponse suivante (liste de lecture détruite le 27/09).
3. **Réponse non conforme : l'ignorer et continuer d'écouter**, sans renvoyer.
4. Délais 1,5 s puis 10 s puis 10 s, trame renvoyée identique ; **échéance
   globale** de 180 s par opération.
5. Validation d'une réponse = `check_reply` de la référence.
6. **Liste blanche** `1/0x02`, `2/0x08`, `2/0x02` appliquée dans `frame()` ET
   `Panneau.commande()`. **Jamais `2/0x0C`** (réécrit la config d'affichage).
   **Pas de `1/0x10`** non plus, bien que `envoyer.py` l'émette : elle est
   absente des vecteurs validés et `CONFIG.SYS` suffit pour la dalle.
7. Lire `CONFIG.SYS` avant tout envoi ; **arrêt si la dalle n'est pas 96 × 64**.
8. **Sauvegarde de l'affichage d'avant** (`pmv_sauvegardes`, une par remorque) :
   `SEQUENT.SYS` illisible ou sans `SQ` → arrêt ; si la liste contient déjà
   `PMVED.Nmg`, la sauvegarde existante n'est **pas** écrasée (on garde
   l'affichage d'avant NOTRE premier envoi) ; sinon elle est remplacée.
9. **Une opération à la fois par remorque** : verrou par remorque, la seconde
   reçoit `409 busy`.
10. **La flash s'use** : même image déjà affichée d'après l'historique →
    `409 deja_affiche` (le front propose de forcer) ; en groupe ou programmé, la
    remorque est écartée ; deux programmations sur une même remorque doivent être
    espacées de **15 min**.

⚠️ **Ne jamais « améliorer » les fonctions reprises de `docs/pmv/reference/jetfile.py`**
(`bmp565`, `panel_file_like_sigma_editor`, `frame`, `check_reply`,
`write_file_frames`, `sequent_sys`, `write_sys_frames`). Un octet de différence et
le panneau n'affiche plus rien. Les tests les vérifient contre `docs/pmv/vecteurs/`.

### Sécurité

- Le contrôleur n'a **aucune authentification** : Cockpit est le point de contrôle.
- L'API ne reçoit **jamais d'adresse** : la cible vient du registre (plaque
  résolue, ou IP fixe saisie par un admin). Port forcé à 9520.
- `127.x` (le jumeau) n'est admis que sur une remorque marquée `jumeau`, et
  utilisable seulement par un admin (ou en `CODING`). Un DNS qui renverrait
  `127.x` est refusé.
- CSRF actif sur toutes les écritures (blueprint **non** exempté).
- Audit append-only `pmv_audit` (TTL 1 an) : qui, quand, IP, remorque, action, résultat.

### Résolution DNS

`pmv_panneau.resoudre_plaque` interroge **directement 1.1.1.1 puis 8.8.8.8 en UDP**
(paquet DNS minimal écrit à la main, sans cache : l'IP 4G change), repli sur le
DNS de Windows. NXDOMAIN = « remorque éteinte ». Cache de 60 s côté page
(`GET /panneaux/etat`), mais **chaque opération re-résout** avant de se connecter.

### Jobs, envoi groupé, planificateur

- Une opération = un **job** dans un thread (`_JOBS`, TTL 1 h), suivi par
  `GET /api/pmv/jobs/<id>` (étapes cochées, progression, journal hexa plafonné).
  `_reserver_job` prend le verrou et crée le job ; `_executer` fait le travail et
  **libère le verrou dans `finally`** (attrape `BaseException`).
- **Envoi groupé** (`lancer_envoi_groupe`) : réserve d'abord TOUTES les
  remorques, puis les traite **3 à la fois** (`ThreadPoolExecutor`). Suivi par
  `GET /api/pmv/runs/<id>`. Pas d'envoi « sans sauvegarde » en groupe.
- **Planificateur** `pmv.start_scheduler()` : thread `pmv-scheduler`, tick 30 s,
  démarré dans le bloc `__main__` d'`app.py` à côté d'Alfred (même garde
  reloader). Réservation atomique (`find_one_and_update` en_attente → en_cours).
  **Une programmation dépassée de plus de 10 min n'est pas envoyée** (`manquee`) :
  afficher en retard un message daté est pire que rien.
- ⚠️ `_JOBS`, les verrous et `_RUNS` supposent **un seul process** (vrai sous
  waitress, comme `scan_report.py`). Multi-workers : passer à un verrou Mongo.

### Routes (`/api/pmv/...`, manager sauf mention)

| Route | Rôle |
|---|---|
| `GET /pmv` | page (onglets Poste, Messages, Programmation, Historique, Registre admin) |
| `GET/POST/PUT/DELETE panneaux[/<id>]` | registre (écriture admin) |
| `POST panneaux/import` | import de l'export JSON des fiches de l'ancien éditeur (admin) |
| `GET panneaux/etat`, `GET panneaux/<id>/etat` | résolution, affichage déduit, dernier envoi, sauvegarde |
| `POST panneaux/<id>/test` · `/envoi` · `/restaurer` | lance un job ; `envoi` : `{message_id` ou `png_b64, force?, sans_sauvegarde?}` |
| `GET jobs/<id>[?journal=1]` | suivi d'un job |
| `GET/POST/PUT/DELETE groupes[/<id>]` | groupes (écriture admin) |
| `POST groupes/<id>/envoi`, `GET runs/<id>` | envoi groupé et suivi |
| `GET/POST/DELETE messages[/<id>]`, `PUT messages/<id>` | bibliothèque |
| `GET/POST programmations`, `DELETE programmations/<id>` | programmation (annulation si en attente) |
| `GET envois[/<id>]` | historique |
| `GET carte` | couche PMV de la carte d'accueil (sans DNS, hors jumeaux) |

Erreurs au format `{"ok": false, "error": "<code>"[, "message"]}` ; les messages
d'échec d'opération sont en français sans jargon (`MESSAGES_ERREUR`).

### Collections MongoDB (index créés à la volée, `_ensure_indexes`)

| Collection | Contenu |
|---|---|
| `pmv_panneaux` | plaque normalisée (`dw330hc`, unique), nom, localisation, lat/lng, `ip_fixe`, `jumeau`, `actif`, `derniere_resolution`, `dernier_affichage` |
| `pmv_messages` | nom, catégorie, étiquettes, event/year, `rgb` (Binary 18 432 o), `png_b64`, `sha256`, auteur |
| `pmv_groupes` | nom (unique), `panneau_ids[]` |
| `pmv_sauvegardes` | `SEQUENT.SYS` d'avant notre premier envoi, par `panneau_id` |
| `pmv_envois` | historique permanent : type, message, `sha256`, miniature, user, event/year, durée, résultat, étapes, journal, `groupe_run_id`, `programmation_id` |
| `pmv_programmations` | cible (panneau ou groupe), message, `at` (naïf Paris), statut `en_attente/en_cours/terminee/partielle/echec/annulee/manquee`, bilan |
| `pmv_audit` | journal d'audit, TTL 365 j |

### Éditeur (`static/js/pmv_editor.js`)

Portage complet de `docs/pmv/editeur/PMV_Bitmap_Editor.html` (v1.3.00) dans la
charte Cockpit : **mêmes algorithmes** (Bresenham, formes, triangle, remplissage,
polices 4×5 et 5×7, import BMP 1/4/8/24/32 bpp **+ 16 bpp 5-6-5**, volet Copier,
insertion, copier/coller), balisage dans `pmv.html` (préfixe `ped-`), icônes
Material Symbols, toasts Cockpit, évènements **pointeur** (tablette). Le volet
Réseau de l'original (DoH depuis le navigateur) n'est pas repris : le registre le
remplace. Raccourcis actifs seulement dans l'onglet Messages, hors saisie.

L'image quitte le navigateur en **PNG 96 × 64** ; le 5-6-5 est toujours fabriqué
**par le serveur** avec la fonction de référence. Le serveur refuse toute autre
taille (le recadrage se fait dans l'éditeur).

### État affiché

On ne lit que le **nom du fichier joué** (`SEQUENT.SYS`), jamais les pixels. Si
c'est `PMVED.Nmg`, l'UI dit « Affiche probablement : <dernier message envoyé par
Cockpit> » ; sinon « Message Sigma : <nom> ». Toujours formulé comme une déduction.

### Carte d'accueil

Bouton `signpost` en bas à droite de la carte (`map_view.js`, `togglePmv`),
**managers seulement** (`window.__userIsManager`), calqué sur le bouton portes.
Lit `/api/pmv/carte` (état de la dernière résolution connue, sans déclencher de
DNS), rafraîchi toutes les 2 min, lien « Ouvrir dans PMV » → `/pmv?panneau=<id>`.

### Recette

```bash
python -m pytest tests/pmv -q      # octets vs vecteurs, liste blanche, dialogue contre le jumeau
python docs/pmv/banc-essai/faux_panneau.py            # jumeau 127.0.0.1:9520
CODING=true python app.py                             # remorque de test "jumeau" en IP fixe 127.0.0.1
python docs/pmv/banc-essai/comparer_capture.py docs/pmv/vecteurs/<image>.png   # après un envoi : "conforme"
```

**Jamais d'envoi vers une vraie remorque sans l'auteur**, sur une remorque hors
service, avec quelqu'un qui voit le panneau.

### Hors périmètre (non mesuré sur remorque : ne pas coder sans essai validé)

Plusieurs images en alternance dans `SEQUENT.SYS`, messages texte en police du
panneau, effets, luminosité, extinction/allumage (`4/0x03`, `4/0x04`), mode CRC
(`55 A3`), mot de passe du contrôleur (`czLogin`), forme `80 7F` de `SEQUENT.SYS`
(seule la forme `0F FF` est prouvée).

### Pièges

- **Octet `00` final et séquence unique** : les deux défauts ne se voient pas
  contre un serveur tolérant ; le jumeau les exige (mode strict), la remorque aussi.
- **Tester une image identique ne réécrit rien** (`deja_affiche`) : pour un
  essai répété, passer `force` (confirmation côté UI).
- **`window.__userIsManager` doit être posé avant `map_view.js`** (c'est le cas
  dans `index.html`), sinon le bouton PMV n'apparaît jamais.
- **`static/libs/leaflet/` n'a pas ses images** (`layers.png`) : `pmv.html` charge
  Leaflet 1.9.4 depuis unpkg comme les autres pages, avec empreinte SRI.
- **Libellés sans accents** dans l'UI PMV, comme les autres pages admin.
