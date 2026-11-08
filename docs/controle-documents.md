# Contrôle de documents (tablettes Field)

Rédigé le 08/11/2026. État : **écran, lecture et relais Cockpit faits**. Le serveur de consultation
(le « moteur ») et les fichiers consultés restent à construire. Ce document en fixe le contrat.

## 1. Objet

Un agent sur le terrain contrôle une personne, une entreprise ou un titre d'accès :

- pièce d'identité, passeport, titre de séjour (bande **MRZ**) ;
- permis de conduire européen (rubriques numérotées du recto) ;
- badge ou accréditation ACO / 24h (code-barres, numéro) ;
- titre d'accès (QR code) et **carte BTP** (QR code) ;
- ou une recherche **manuelle** : nom / prénom / date de naissance / numéro de document,
  raison sociale ou SIRET / SIREN, numéro de badge.

Cockpit ne consulte **aucun fichier lui-même**. Il vérifie le droit de la tablette, normalise la
demande, la transmet signée au **serveur de consultation**, assainit la réponse et trace chaque
consultation. Les fichiers interrogés seront choisis plus tard.

## 2. Parcours de l'agent

1. **Bouton Contrôle** (loupe sur une silhouette, `person_search`), en bas à gauche de la barre
   d'actions. Il n'apparaît que sur les tablettes d'un groupe coché (§ 5). Le bouton Calques qu'il
   remplace est devenu un bouton de carte, sous le zoom + / -.
2. **Accueil** : une grande tuile « Lire un document », puis trois onglets de recherche manuelle
   (Personne, Entreprise, Badge / titre), le **motif** (Contrôle d'accès, Vérification d'identité,
   Incident, Autre) et « Rechercher ».
3. **Lire un document** : la caméra s'ouvre avec un cadre au format carte et une zone pointillée
   pour la bande MRZ. Lampe si l'appareil l'a. La lecture est continue (une image toutes les 650 ms) :
   - **MRZ valide** (toutes les clés de contrôle justes) : vibration, retour à l'accueil, champs remplis ;
   - **code** (QR, code-barres) : onglet Badge / titre rempli, type deviné (carte BTP, badge, titre) ;
   - **permis** reconnu (nom et numéro lus) : champs remplis ;
   - sinon, les **lignes lues** s'affichent : l'agent choisit le champ (Nom, Prénom, N° doc,
     Entreprise) et touche la ligne. Le champ suivant est proposé automatiquement. La liste reste
     figée 3 s après un toucher.
4. **Relecture obligatoire** : rien ne part sans que l'agent ait vu les champs. Un bandeau rappelle
   ce qui a été lu, signale une lecture incertaine (clé fausse) et un **document expiré**.
5. **Résultat** :
   - verdict en tête : *Aucune correspondance* (gris), *Correspondance trouvée* (vert),
     *À vérifier* (orange), **ALERTE** (rouge clignotant, vibration longue) ;
   - une carte par résultat : titre, sous-titre, message, **consignes** mises en avant, champs, source ;
   - fichiers consultés, avec ceux qui étaient indisponibles ;
   - référence de la consultation (8 premiers caractères), pour l'échange avec le PC ;
   - « Modifier » (retour aux champs) et « Nouveau contrôle » (tout est effacé).
6. **Fermeture** : champs, lignes lues et résultats sont effacés. Rien n'est gardé sur la tablette
   (ni `localStorage`, ni file hors ligne). La caméra s'arrête dès que l'application passe en
   arrière-plan.

Hors ligne : la recherche est refusée avec un message. Elle n'est jamais mise en file : une
consultation n'a de sens qu'au moment du contrôle.

## 3. Lecture des documents

Tout est lu **sur la tablette**. Aucune image ne part au serveur.

| Moteur | Où | Ce qu'il lit |
|---|---|---|
| ML Kit (texte latin + codes), modèles embarqués | Application Android **1.4 et plus** (`FieldScanner.java`, méthode `FieldNative.scanImage`) | Texte (MRZ, permis, badges) et tous les codes |
| `BarcodeDetector` du navigateur | PWA, ou application 1.3 et moins | Codes seulement. L'écran le signale et propose la saisie |

Formats MRZ reconnus (`ctlParseMrz`, `field.js`), clés de contrôle OACI 9303 (poids 7-3-1)
vérifiées champ par champ et en composite :

| Format | Documents | Lignes |
|---|---|---|
| TD3 | Passeports | 2 x 44 |
| TD1 | CNI françaises depuis 2021, titres de séjour, cartes européennes | 3 x 30 |
| TD2 | Anciens titres et cartes | 2 x 36 |
| CNI_FR | Ancienne CNI française (`IDFRA...`) | 2 x 36 |

Corrections d'OCR : espaces retirés, `«` lu pour `<`, et dans les champs numériques seulement,
`O/Q/D` lus comme 0, `I/L` comme 1, `Z` comme 2, `S` comme 5, `B` comme 8, `G` comme 6. Une MRZ dont
une clé reste fausse n'est **jamais** acceptée automatiquement : la lecture continue.

Permis européen (`ctlParseLicence`) : il faut « PERMIS DE CONDUIRE » ou « DRIVING LICENCE » dans le
texte, puis les rubriques `1.` (nom), `2.` (prénoms), `3.` (date de naissance), `5.` (numéro).
La ligne lisible par machine au verso du permis français n'est **pas** exploitée : son format reste
à confirmer sur de vrais exemplaires.

Codes (`ctlClassifyCode`) : `cartebtp` dans le contenu donne une **carte BTP**. Un code-barres
linéaire de 8 à 20 chiffres donne un **badge** (les accréditations ACO et 24h Motos ont 16 chiffres).
Un QR, Aztec, PDF417 ou Data Matrix donne un **titre**. Le serveur de consultation reçoit le
contenu brut et décide. Le type n'est qu'une indication.

Vérifié hors tablette : spécimens OACI TD1, TD2, TD3, ancienne CNI française construite avec ses
clés, correction d'OCR, clé fausse refusée, permis, classement des codes, SIRET.
**À vérifier sur la tablette** : lecture réelle d'une CNI, d'un passeport, d'un permis, d'un
badge et d'une carte BTP (lumière, reflets, mise au point).

## 4. Architecture

```
Tablette Field ──(cookie field_token)──> Cockpit /field/controle/search ──(HMAC)──> Serveur de consultation ──> fichiers
   lecture ML Kit                         droit du groupe, normalisation          (à construire)
   relecture, affichage                   limite, journal, assainissement
```

- Tablette : `static/js/field.js`, section CONTROLE (`openControleModal`, `ctlParseMrz`...).
  Natif : `FieldScanner.java`.
- Cockpit : route `field.field_controle_search` (dans `field_bp`, déjà hors CSRF comme toutes les
  routes tablette), logique dans `controle.py`.
- Serveur de consultation : service séparé, à construire. Il sera le seul à connaître les fichiers.

## 5. Droits et mode test

- **Droit** : case « Contrôle de documents (tablettes Field) » du **groupe de balises**
  (Configuration > Anoloc), drapeau `controle`. Lu à chaud comme `declarant` : `/field` et
  `/field/my-fiches` le renvoient, le bouton apparaît ou disparaît sans recharger, et la route le
  revérifie à chaque appel (403 `controle_non_autorise`, qui ferme l'écran).
- **Mode test** (tablette en mode test de `/field-dispatch`) **et aucun serveur configuré** :
  résultats **fictifs**, marqués « Résultats fictifs » :
  - nom contenant `ALERTE` : alerte avec consignes ;
  - `VERIF` : à vérifier ;
  - `TEST` : correspondance ;
  - autre nom : aucune correspondance ;
  - entreprise, badge : correspondance.

  Dès qu'un serveur est configuré, le mode test l'interroge aussi.

## 6. API tablette → Cockpit

`POST /field/controle/search` (cookie `field_token`), corps JSON :

```json
{
  "query": {"type": "personne", "nom": "MARTIN", "prenom": "Claire", "date_naissance": "1985-03-15",
            "numero_document": "X4RTBPFW4", "type_document": "cni", "nationalite": "FRA"},
  "source": "mrz",
  "motif": "acces"
}
```

| `query.type` | Champs | Obligatoire |
|---|---|---|
| `personne` | `nom`, `prenom`, `date_naissance` (AAAA-MM-JJ), `numero_document`, `type_document` (`cni`, `passeport`, `titre_sejour`, `permis`, `visa`, `autre`), `nationalite` (ISO 3 lettres) | `nom` (2 car.) ou `numero_document` |
| `entreprise` | `raison_sociale`, `siret` (14 chiffres) ou SIREN (9), clé de Luhn vérifiée, règle La Poste comprise | l'un des deux |
| `code` | `valeur` (512 car. max), `format` (`qr_code`, `code_128`... ou `manual`), `type_code` (`carte_btp`, `badge`, `titre`, `autre`) | `valeur` |

`source` : `manuel`, `mrz`, `ocr`, `code`. `motif` : `acces`, `identite`, `incident`, `autre`
(par défaut `acces`). Noms : lettres (accents compris), espaces, tiret, apostrophe, point.

Réponse 200 :

```json
{
  "ok": true, "consultation_id": "9f2c...", "demo": false,
  "statut": "alerte",
  "resultats": [{"niveau": "alerte", "titre": "MARTIN Claire", "sous_titre": "Interdiction d'accès",
                 "source": "Interdits de site", "message": "Jusqu'au 31/12/2026.",
                 "consignes": "Ne pas intervenir seul. Prévenir le PC Org.",
                 "champs": [{"label": "Naissance", "valeur": "15/03/1985"}]}],
  "sources": [{"nom": "Interdits de site", "etat": "ok"}, {"nom": "Accréditations", "etat": "indisponible"}],
  "messages": []
}
```

Erreurs : 400 (`type_invalide`, `nom_requis`, `nom_invalide`, `date_invalide`, `document_invalide`,
`siret_invalide`, `entreprise_requise`, `code_requis`, `requete_invalide`), 403
`controle_non_autorise`, 429 `trop_de_consultations`, 502 `consultation_indisponible`, 503
`consultation_non_configuree`. Les 502 et 503 portent aussi `consultation_id`, et la tentative est tracée.

## 7. Contrat du serveur de consultation

### Appel

`POST <CONTROLE_API_URL>`, corps JSON UTF-8, délai `CONTROLE_API_TIMEOUT` (8 s par défaut) :

```json
{
  "consultation_id": "9f2c4e...",
  "query": {"type": "personne", "nom": "MARTIN", "prenom": "Claire", "date_naissance": "1985-03-15",
            "numero_document": "X4RTBPFW4", "type_document": "cni", "nationalite": "FRA"},
  "motif": "acces",
  "source": "mrz",
  "origine": {"application": "cockpit-field", "tablette_id": "665f...", "tablette": "Secu 1",
              "groupe": "g-secu", "evenement": "24H MOTOS", "annee": "2026"},
  "ts": "2026-11-08T10:12:00+00:00"
}
```

### Authentification

En-têtes :

- `X-Cockpit-Timestamp` : secondes Unix ;
- `X-Cockpit-Signature` : `sha256=` + HMAC-SHA256 hexadécimal, clé `CONTROLE_API_SECRET`, sur
  `<timestamp>.` suivi des **octets exacts du corps** ;
- `X-Cockpit-Consultation` : `consultation_id`.

Le serveur doit :

1. recalculer la signature sur le corps brut et la comparer en temps constant ;
2. refuser un horodatage à plus de 300 s de son horloge ;
3. refuser un `consultation_id` déjà vu dans cette fenêtre (anti-rejeu) ;
4. n'accepter que l'IP de Cockpit et HTTPS ;
5. journaliser de son côté (`consultation_id`, `tablette_id`, motif, critères, résultat).

Le secret fait au moins 32 caractères. Il est distinct de tout autre secret de Cockpit. Sans lui,
ou sans URL, Cockpit répond 503 sans rien appeler. Les tablettes ne parlent **jamais** au serveur
de consultation : seule l'adresse de Cockpit leur est connue.

### Réponse attendue (HTTP 200)

```json
{
  "statut": "alerte",
  "resultats": [{"niveau": "alerte", "titre": "...", "sous_titre": "...", "source": "...",
                 "message": "...", "consignes": "...", "champs": [{"label": "...", "valeur": "..."}]}],
  "sources": [{"nom": "...", "etat": "ok"}],
  "messages": ["..."]
}
```

| Champ | Valeurs | Limite appliquée par Cockpit |
|---|---|---|
| `statut` | `aucun`, `correspondance`, `attention`, `alerte`. Absent ou inconnu : déduit des niveaux | |
| `resultats[].niveau` | `info`, `ok`, `attention`, `alerte`. Inconnu : `info` | 20 résultats |
| `titre` / `sous_titre` / `source` | texte | 120 / 160 / 60 caractères |
| `message` / `consignes` | texte | 500 caractères |
| `champs` | `{label, valeur}` | 20 champs, 40 / 200 caractères |
| `sources[].etat` | `ok`, `indisponible`, `non_consulte` | 10 sources |
| `messages` | textes | 5, 300 caractères |

Tout est affiché en **texte brut** (jamais de HTML). Autre code HTTP, JSON illisible ou délai
dépassé : 502 `consultation_indisponible` sur la tablette.

Conseil au moteur : renvoyer `aucun` avec la liste des `sources` interrogées (l'agent sait ce qui a
été consulté), et réserver `alerte` aux cas avec consignes.

## 8. Traçabilité et RGPD

Collection `controle_consultations`, un document par tentative valide, y compris 502 et 503 :
`_id` (= `consultation_id`), `at`, `device_id`, `device_name`, `group_id`, `event`, `year`,
`motif`, `source`, `query` (critères), `position` (dernière position connue de la tablette),
`provider` (`api`, `demo` ou vide), `statut`, `nb_resultats`, `error`, `duration_ms`, `expire_at`.
Index TTL sur `expire_at` : `CONTROLE_RETENTION_DAYS` (365 j par défaut). Le détail des résultats
**n'est pas** gardé par Cockpit : seul le serveur de consultation sait ce qu'il a répondu.

**À faire valider avant la mise en service** (DPO / juriste) :

- base légale et finalité de chaque fichier consulté. Un organisateur privé ne peut pas consulter
  les fichiers de police (FPR, etc.) : ce sont nos propres fichiers (accréditations, interdictions
  de site, entreprises intervenantes...) et des sources ouvertes (SIRENE) ;
- analyse d'impact (AIPD) : contrôle d'identité et lecture de pièces sur la voie publique ;
- information des personnes contrôlées et des agents ;
- habilitation nominative des agents. Le droit est aujourd'hui **par groupe de tablettes**, sans
  identité de l'agent ;
- durée de conservation du journal et liste des personnes qui peuvent le lire (aucune page de
  lecture n'existe encore) ;
- carte BTP : vérification par le QR code (page officielle de vérification) à confirmer sur un
  exemplaire, ainsi que le droit de l'automatiser côté serveur.

## 9. Variables d'environnement (Cockpit)

| Variable | Rôle | Défaut |
|---|---|---|
| `CONTROLE_API_URL` | Adresse du serveur de consultation (HTTPS) | vide : 503, ou résultats fictifs en mode test |
| `CONTROLE_API_SECRET` | Secret HMAC partagé, 32 caractères minimum | vide : comme ci-dessus |
| `CONTROLE_API_TIMEOUT` | Délai d'attente (s) | `8` |
| `CONTROLE_RATE_10MIN` | Consultations max par tablette sur 10 min | `40` |
| `CONTROLE_RETENTION_DAYS` | Conservation du journal (jours, TTL) | `365` |

## 10. Reste à faire

1. **Serveur de consultation** : service autonome, vérification HMAC et anti-rejeu, connecteurs
   par fichier, recherche approchée sur les noms (accents, tirets, noms composés, inversion
   nom / prénom), réponse au format du § 7.
2. Choix des fichiers et validation RGPD (§ 8).
3. Page de lecture du journal pour les personnes habilitées.
4. Bouton « Prévenir le PC » sur une alerte (message prérempli avec la référence de consultation).
5. Habilitation par agent si elle est exigée (identité de l'agent dans la demande).
6. Validation de la lecture sur de vrais documents (§ 3) et, si besoin, du verso du permis.
7. Signature de production de l'APK avant d'équiper toute la flotte (1.4 nécessaire pour la lecture).

## 11. Tests

- Serveur : `tests/test_field_controle.py` (droit du groupe, 503 tracé, mode test, signature et
  assainissement, 502, limite, demandes invalides, normalisation, Luhn).
- Lecture et écran : vérifiés hors tablette (spécimens OACI, permis, codes, parcours complet dans un
  DOM simulé), pas encore en conditions réelles.
