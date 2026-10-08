# Liaison Cockpit ↔ Friday : note pour le service informatique

*Version du 08/10/2026. Côté Cockpit : en place (module `integrations.py`). Côté Friday : à développer par vos soins, selon cette note.*

## 1. Ce que l'on veut obtenir

1. Le PC Organisation crée des fiches dans Cockpit (sa main courante). Celles qui relèvent de l'informatique **arrivent automatiquement dans Friday**. Cockpit choisit lesquelles selon des règles réglées dans Cockpit : une catégorie entière, ou seulement certaines sous-classifications (par exemple *Technique > Informatique*).
2. Dans Friday, vous voyez la **liste des fiches Cockpit attribuées à votre service**. C'est **vous qui décidez** : lier une fiche à un INC existant de la main courante, en créer un nouveau, ou l'ignorer. Plusieurs fiches Cockpit peuvent viser le même INC (même panne signalée deux fois).
3. Une fois liée, tout ce que vous faites sur l'INC remonte dans la fiche Cockpit en quelques secondes : statut, agent, notes, photos, résolution.
4. Ce que le PC ajoute à la fiche (actions, précisions, photos) vous parvient aussi : **la chronologie Cockpit alimente votre INC**.
5. **Friday ne clôt jamais une fiche Cockpit.** Quand l'INC passe « résolu », Cockpit affiche « Résolu par … : à clôturer » et c'est l'opérateur du PC qui clôt.

**Modèle retenu :** la main courante de Friday (`call_log`, références `INC-AAAA-NNNN`). Vos « fiches » sont des fiches d'installation (VLAN, SSID…), pas des tickets.

## 2. Principe technique

Chaque application envoie des **messages HTTP signés** à l'autre (webhooks). Personne ne lit la base de l'autre, il n'y a pas de session partagée, ni de compte de service connecté à une interface.

```
Cockpit ── POST signé ──▶ Friday   /api/integrations/cockpit/events   (fiches, chronologie)
Friday  ── POST signé ──▶ Cockpit  /api/integrations/friday/events     (liaison, statut, notes, photos, résolution)
```

- **Fiabilité :** chaque côté tient une **file d'envoi persistante**. Un message qui n'a pas reçu de réponse 2xx est renvoyé avec un délai croissant : 15 s, 30 s, 1 min… jusqu'à 1 h, pendant 24 h. Ensuite il passe « en échec » et peut être relancé à la main. Les messages d'un même côté partent **dans l'ordre**.
- **Doublons :** chaque message porte un identifiant unique (`X-Event-Id`). Un message reçu deux fois doit être accepté (réponse 2xx) mais **ignoré la seconde fois**.

## 3. Sécurité (à lire en entier)

### 3.1 Signature des messages (dans les deux sens)
- **Le secret :** un **secret partagé** propre à cette liaison, d'au moins 32 caractères aléatoires, par exemple `openssl rand -hex 32`.
  - Chez nous, il est dans la variable d'environnement `COCKPIT_INTEG_FRIDAY_SECRET`. Chez vous, mettez-le dans `.env`, par exemple `COCKPIT_SECRET`.
  - Il ne doit **jamais** apparaître dans le code, dans Git, dans les journaux, dans une interface ni dans une URL.
  - Transmettez-le **de vive voix ou par un canal chiffré**, jamais par mail en clair ni sur WhatsApp ou Discord.
- **En-têtes de chaque requête :**

  | En-tête | Contenu |
  |---|---|
  | `X-Event-Id` | identifiant unique du message (UUID) |
  | `X-Signature-Timestamp` | heure d'envoi, en **secondes** depuis 1970 (epoch UTC) |
  | `X-Signature` | `sha256=` + HMAC-SHA256 hexadécimal, calculé avec le secret, sur la chaîne `<timestamp>` + `.` + `<corps brut de la requête>` |
  | `Content-Type` | `application/json` |

- **Vérification à la réception :**
  1. Lire le **corps brut**, avant tout parsing JSON. Avec Express, utiliser `express.raw({type: 'application/json'})` sur cette route, ou l'option `verify` de `express.json` pour garder le buffer.
  2. Refuser (**401**) si l'horodatage s'écarte de plus de **5 minutes** de l'heure du serveur. Les serveurs doivent donc être à l'heure (NTP).
  3. Recalculer la signature et comparer **à temps constant** avec `crypto.timingSafeEqual`, jamais avec `===`. Refuser (**401**) si elle ne correspond pas.
  4. Refuser le rejeu : stocker les `X-Event-Id` reçus (table, conservation 30 jours). S'il est déjà connu, répondre **200** sans rien refaire.

  Exemple Node.js :

  ```js
  const crypto = require('crypto');
  function verifySignature(req, rawBody) {
    const ts = parseInt(req.get('X-Signature-Timestamp'), 10);
    if (!ts || Math.abs(Date.now() / 1000 - ts) > 300) return false;
    const expected = 'sha256=' + crypto.createHmac('sha256', process.env.COCKPIT_SECRET)
      .update(ts + '.').update(rawBody).digest('hex');
    const got = Buffer.from(req.get('X-Signature') || '');
    const exp = Buffer.from(expected);
    return got.length === exp.length && crypto.timingSafeEqual(got, exp);
  }
  function sign(bodyString) {
    const ts = Math.floor(Date.now() / 1000);
    const sig = 'sha256=' + crypto.createHmac('sha256', process.env.COCKPIT_SECRET)
      .update(ts + '.' + bodyString).digest('hex');
    return { ts, sig };
  }
  ```

### 3.2 La route de réception chez vous
- **Exception CSRF :** créez **une seule** route sans session : `POST /api/integrations/cockpit/events`. Elle doit échapper à votre garde CSRF actuelle (`X-Requested-With` / `Origin`) et à l'authentification par session, **mais uniquement elle**.
  - Montez-la avant `app.use('/api', …)`, ou ajoutez son chemin exact à l'exception.
  - Cockpit envoie aussi `X-Requested-With: XMLHttpRequest`. La signature HMAC reste la seule protection qui compte.
- **Taille :** limitez le corps à 2 Mo sur cette route. Les photos n'y transitent pas : ce sont des liens (voir 4.3).
- **Validation :** validez chaque champ (types, longueurs). Ne stockez rien de non attendu. Échappez tout texte reçu avant affichage, car il vient de la saisie libre d'opérateurs : utilisez `textContent` et votre `sanitize-html.js`, jamais `innerHTML` avec ces textes.
- **Réponses :** répondez vite (moins de 10 s). Faites le travail lourd après la réponse, avec votre modèle `setImmediate`.
- **Droits :** ajoutez une rubrique de droits `cockpit` (aucun / lecture / écriture) pour la page « Fiches Cockpit ». Seuls les profils en écriture peuvent lier, créer un INC ou ignorer.

### 3.3 Réseau
- **Joignabilité :** les deux applications sont internes ; gardez-les ainsi. Friday doit pouvoir joindre Cockpit, et Cockpit doit pouvoir joindre `friday.lemans.org`. Vérifiez DNS et pare-feu entre les deux serveurs.
- **Adresse de réception de Friday :**
  - elle doit être en **HTTPS**, avec un certificat valide ;
  - son nom d'hôte (`friday.lemans.org`) doit être inscrit côté Cockpit dans la liste des hôtes autorisés, fixée sur le serveur ;
  - Cockpit refuse tout autre hôte, toute adresse locale (`localhost`, `127.0.0.1`…) et ne suit **aucune redirection**.

  Donnez-nous donc l'URL finale exacte, sans redirection http→https ni réécriture.
- **Chiffrement :** Cockpit n'envoie qu'en HTTPS. Pour vos envois vers Cockpit, utilisez aussi l'adresse HTTPS de Cockpit. La signature protège contre la modification, le chiffrement contre la lecture.

### 3.4 Points de sécurité relevés dans Friday, sans lien direct avec le projet (à corriger)
- **Compte par défaut :** le compte `admin` / `admin` est créé automatiquement au premier démarrage (`database.js`). Il faut le désactiver ou imposer un mot de passe dès l'installation.
- **Installation neuve impossible :**
  - `ALTER TABLE discord_messages` est exécuté avant son `CREATE TABLE` ;
  - les colonnes `fiches.fiche_number`, `calendar_events.assigned_users` et les `status` créés par `migrate-status.js` n'existent dans aucune migration.

  Une restauration ou une réinstallation planterait.
- **Contrôles de droits manquants :**
  - `PUT` et `DELETE /api/events/:id` ne vérifient pas l'accès à l'événement ;
  - `PATCH` et `DELETE /api/notes/:id` se contentent du droit de lecture.
- **Valeurs non contrôlées côté serveur :**
  - main courante : statut, catégorie, canal ;
  - fiches : statut, type ;
  - `PUT /api/settings` accepte n'importe quelle clé.

  Validez-les avec une liste fermée.
- **Secrets Graph :** `graphAuth.js` peut encore lire les secrets dans `ecosystem.config.js`. Tout doit venir de `.env`, qui ne doit jamais être versionné.
- **État en mémoire :** sessions SSE, jetons temporaires et limiteur de connexion sont en mémoire. C'est acceptable sur un processus unique, mais la file d'envoi vers Cockpit, elle, **doit** être en base, pour ne rien perdre au redémarrage.

## 4. Ce que Cockpit vous envoie

`POST {votre URL}/api/integrations/cockpit/events`, signé (§3.1). Corps JSON :

```json
{
  "event_id": "5b0c…",
  "type": "fiche.created",
  "integration": "friday",
  "source": "cockpit",
  "sent_at": "2026-10-08T08:52:10+00:00",
  "fiche": {
    "id": "a3f1…",
    "event": "SAISON", "year": 2026,
    "category": "PCO.Technique", "sous_classification": "Informatique",
    "text": "Borne wifi HS tribune 10",
    "niveau_urgence": "UR",
    "status": "open",
    "ts": "2026-10-08T08:51:55+00:00", "created_at": "…", "close_ts": null,
    "operator": "Prénom NOM", "operator_close": null,
    "area": "Tribune 10", "carroye": "AJ16", "lat": 47.95, "lng": 0.21,
    "unit": null, "declaration_ref": null
  },
  "new_entries": [
    { "id": "9c1e0f…", "ts": "…", "author": "Prénom NOM", "origin": "cockpit",
      "text": "Appel du speaker : plus de réseau en cabine",
      "photos": [ { "url": "https://…/api/integrations/friday/photo?p=…&exp=…&sig=…",
                    "thumb_url": "…" } ] }
  ]
}
```

### 4.1 Types d'événements

| `type` | Quand | À faire chez vous |
|---|---|---|
| `ping` | Bouton « Tester » de Cockpit | Vérifier la signature, répondre 200 |
| `fiche.created` | La fiche entre dans votre périmètre (règles Cockpit) | Créer / mettre à jour la fiche dans la boîte « Fiches Cockpit » |
| `fiche.updated` | Description, urgence, lieu, catégorie… modifiés | Mettre à jour la copie ; noter le changement sur l'INC lié |
| `fiche.comment` | Nouvelles entrées de chronologie | Ajouter les `new_entries` au journal de l'INC lié |
| `fiche.closed` / `fiche.reopened` | Le PC clôt / rouvre la fiche | Afficher l'information ; **ne pas** clore l'INC automatiquement (à votre choix) |
| `fiche.unassigned` | La fiche sort de votre périmètre (catégorie changée) | La retirer de la boîte ; prévenir si elle était liée |

### 4.2 Règles de traitement
- **Mise à jour par identifiant :** traitez chaque message comme une mise à jour de la fiche `fiche.id`. Le bloc `fiche` est toujours complet ; écrasez votre copie.
- **Dédoublonnage :** les `new_entries` portent un `id` stable. Ignorez celles déjà reçues.
- **Pas d'écho :** les entrées que **vous** nous avez envoyées ne vous sont jamais renvoyées.
- **Événement :** `event` / `year` désignent l'événement Cockpit (« SAISON 2026 », « 24H MOTOS 2027 »…). Faites la correspondance avec vos `events` par catégorie ou par un réglage.

### 4.3 Photos
Les photos sont des **liens signés valables 7 jours**, sans session. **Téléchargez-les tout de suite** et stockez-les comme vos photos de main courante (`call_log_photos`) : vérification des octets magiques, taille maximale. Ne gardez pas le lien. Un lien expiré ou modifié renvoie 404.

## 5. Ce que Friday doit envoyer à Cockpit

`POST {adresse Cockpit}/api/integrations/friday/events`, signé de la même façon (§3.1), un message **par fiche Cockpit**. Si un INC est lié à 3 fiches, envoyez 3 messages.

- **Réponses :**
  - **200** : traité. Une réponse avec `"duplicate": true` signifie qu'il avait déjà été traité.
  - **401** : signature ou horodatage refusé.
  - **404 `fiche_non_partagee`** : cette fiche ne vous a jamais été envoyée. Cockpit refuse toute action sur une fiche hors de votre périmètre.
  - **409 `fiche_non_liee`** : envoyez d'abord `ticket.linked`.
  - **400** : contenu invalide.

  Une erreur 4xx est définitive : corrigez puis renvoyez avec un **nouvel** `X-Event-Id`. Une 5xx ou un réseau en panne : renvoyez le **même** message plus tard.

| `type` | Champs | Effet dans Cockpit |
|---|---|---|
| `ticket.linked` | `fiche_id`, `ref` (INC-2026-0042), `url` (lien vers l'INC dans Friday), `status`, `agent` | Badge « Friday INC-… » sur la fiche + entrée de chronologie |
| `ticket.unlinked` | `fiche_id` | Retire le lien |
| `ticket.updated` | `fiche_id`, `status`, `status_label` (optionnel), `agent` | Badge à jour ; changement de statut / d'agent dans la chronologie |
| `ticket.note` | `fiche_id`, `text`, `author` | Entrée de chronologie |
| `ticket.photo` | `fiche_id`, `author`, `photo: {filename, data_base64, caption}` (JPEG / PNG / WebP, 10 Mo max) | Photo dans la chronologie |
| `ticket.resolved` | `fiche_id`, `resolution` (texte), `resolved_by`, `resolved_at` | Badge orange « Résolu… à clôturer » ; **la fiche reste ouverte** |

**Statuts :** utilisez vos valeurs `open`, `in_progress`, `escalated`, `resolved`, `cancelled`. Cockpit les affiche « Ouvert, En cours, Escaladé, Résolu, Annulé ». Pour un autre libellé, envoyez `status_label`.

**Exemple :**

```json
{ "type": "ticket.updated", "fiche_id": "a3f1…", "ref": "INC-2026-0042",
  "status": "in_progress", "agent": "Paul R." }
```

## 6. Travail à réaliser dans Friday (liste)

1. **Base de données** (migrations au démarrage, dans `database.js`) :
   - table `cockpit_inbox` : `fiche_id` UNIQUE, `payload` JSON (dernier état), `category`, `event`, `year`, `state` (`nouvelle` / `liee` / `ignoree`), `received_at`, `updated_at` ;
   - table `cockpit_entries` : `entry_id` UNIQUE, `fiche_id`, contenu, pour le dédoublonnage ;
   - table `cockpit_events_seen` : `event_id` UNIQUE, `received_at`, pour le rejeu ; à purger à 30 jours ;
   - table `cockpit_outbox` : `id`, `type`, `fiche_id`, `body`, `state`, `attempts`, `next_at`, `deadline`, `last_error`, `created_at` ;
   - table de liaison `call_log_cockpit_links (call_log_id, fiche_id)`, pour qu'un INC puisse être lié à plusieurs fiches ;
   - sur `call_log` : `resolved_at`, `resolved_by`, `resolution` (texte de clôture) et `updated_at`.
2. **Route de réception** `POST /api/integrations/cockpit/events` : signature, rejeu, validation (§3), puis mise à jour de `cockpit_inbox` et ajout des `new_entries` au journal de l'INC lié.
3. **Page « Fiches Cockpit »** (nouvelle rubrique `cockpit`), sur le modèle de `call-log.html` :
   - la liste des fiches reçues : nouvelles, liées, ignorées ;
   - le détail : texte, chronologie, photos, carte ;
   - trois actions : **lier à un INC existant**, **créer un INC** (pré-rempli, catégorie IT), **ignorer**.
4. **Service d'envoi** `services/cockpitService.js` + `jobs/cockpitOutboxJob.js` :
   - mise en file en base ;
   - envoi signé dans l'ordre ;
   - nouvelles tentatives à délai croissant, inspirées de votre `discordFetch` ;
   - abandon après 24 h, avec une alerte (par exemple votre notification WhatsApp ou Discord existante).
5. **Points d'appel** : mettre un message en file pour **chaque fiche Cockpit liée** à l'INC concerné :
   - liaison / déliaison → `ticket.linked` / `ticket.unlinked` ;
   - `PATCH /api/call-log/:id` (`routes/callLog.js`) : comparer l'ancien et le nouveau statut / agent / notes → `ticket.updated` ou `ticket.note` ; passage à `resolved` → `ticket.resolved`, avec le texte de résolution ;
   - `POST /api/call-log/:id/photos` → `ticket.photo` (lire le fichier stocké, l'encoder en base64).
6. **Configuration** dans `.env` : `COCKPIT_URL` (adresse de Cockpit) et `COCKPIT_SECRET`.

## 7. Mise en service et tests

1. **Secret :** nous générons le secret ensemble et chacun le place dans son environnement.
2. **Configuration Cockpit :**
   - nous inscrivons `friday.lemans.org` dans la liste des hôtes autorisés de Cockpit ;
   - puis, dans Cockpit (Applications liées, page admin), nous saisissons votre adresse de réception en `https://` et les règles d'envoi.
3. **Premier test :** le bouton **« Tester »** de Cockpit envoie un `ping` signé. Votre route doit répondre 200, et 401 si vous changez un caractère du secret.
4. **Second test :** une fiche *Technique > Informatique* de test dans Cockpit doit apparaître dans votre boîte en moins de 10 secondes. Liez-la à un INC, changez son statut, ajoutez une note et une photo, puis passez-le en « résolu » : tout doit apparaître dans la fiche Cockpit, **qui doit rester ouverte**.
5. **Pannes :** arrêtez Friday 5 minutes et créez une fiche dans Cockpit. Elle doit arriver au redémarrage, dans l'ordre. Faites la même chose dans l'autre sens.

**Contact côté Cockpit :** [à compléter]. Le détail du fonctionnement de Cockpit est dans `docs/claude/integrations.md` du dépôt Cockpit.
