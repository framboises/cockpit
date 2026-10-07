# Brief pour Claude sur la VM Alfred (srv-safe-docker, 192.168.254.36)

> A copier tel quel dans la session Claude Code qui maintient le wrapper
> `/alfred/ask` sur la VM. Redige le 07/10/2026 depuis le depot Cockpit.

## 1. Ce qui change cote Cockpit

Cockpit (prod, 192.168.254.35) a maintenant un **widget de chat** pour les
operateurs du PC Organisation. Il appelle **le meme endpoint** que les
mentions WhatsApp, avec **la meme signature HMAC** (`ALFRED_ASK_SECRET`,
`X-Alfred-Timestamp`, `X-Alfred-Signature = "sha256=" + HMAC(secret, ts + "." + corps brut)`).
Rien ne change pour WhatsApp : son payload est identique a l'octet pres.

Les questions du chat ajoutent des champs **optionnels** au corps de
`POST /alfred/ask` :

```json
{
  "messages": [ ... ],                 // alterne user/assistant, finit sur user
  "max_tool_hops": 6,
  "request_id": "chat-3f9a1c2b7d10",   // prefixe "chat-" = vient du widget
  "is_new_mention": true,              // true au 1er echange d'une conversation
  "channel": "cockpit",                // absent pour WhatsApp
  "context": {
    "user_name": "Prenom Nom",
    "user_email": "...",
    "event": "24H MOTOS", "year": "2026",   // evenement selectionne par l'operateur
    "page": "Cockpit", "path": "/",         // page ouverte
    "now": "2026-10-07T14:35+02:00",        // heure de Paris
    "conversation_id": "9f2c...",           // stable sur toute la conversation, unique par operateur (cle de memoire du wrapper)
    "turn_id": "a81b...",                   // id du message de reponse attendu (tracabilite)
    "scope": "eyJlIjoi....<hex>"            // jeton opaque signe par Cockpit, voir section 4
  }
}
```

**Transition** : Cockpit tourne aujourd'hui en `ALFRED_CHAT_CONTEXT_MODE=prefix`
et n'envoie **pas** `channel` ni `context` (pour ne pas risquer un 400 si le
wrapper valide strictement). A la place, il met une ligne en tete du dernier
message utilisateur :

```
[Contexte Cockpit, a ne pas recopier] Operateur : X (PC Organisation, via Cockpit) ; Evenement selectionne : 24H MOTOS 2026 ; Page Cockpit : Cockpit ; Heure de Paris : 07/10/2026 14:35.

<question de l'operateur>
```

Des que le wrapper gere les champs ci-dessus, l'admin Cockpit passe a
`ALFRED_CHAT_CONTEXT_MODE=both` (les deux pendant la bascule), puis `field`.

## 2. Taches demandees sur le wrapper

### A. Accepter les nouveaux champs

- `channel`, `context`, `is_new_mention`, `request_id` : acceptes et
  optionnels. Un champ inconnu ne doit jamais provoquer de 400.
- Valider `context` (dict, chaines courtes) : c'est Cockpit qui le remplit
  cote serveur, l'operateur ne peut pas le falsifier, mais borner les tailles.

### B. Adapter le prompt systeme quand `channel == "cockpit"`

- Injecter le contexte **dans le prompt systeme**, pas dans le message
  utilisateur : operateur, evenement selectionne, page, heure de reference
  (`now`, a utiliser pour "aujourd'hui", "dans une heure", etc.).
- Si la ligne `[Contexte Cockpit, a ne pas recopier] ...` est presente en
  tete du dernier message (mode prefix/both), la **retirer** avant de passer
  le message au modele et utiliser son contenu comme contexte.
- Ton : interlocuteur = operateur du PC Org en exploitation. Vouvoiement,
  reponses courtes et factuelles. A trancher avec l'admin Cockpit : garder ou non le
  "Monsieur" de majordome sur ce canal (proposition : non, ou par le nom).
- Mise en forme autorisee sur ce canal (le widget la rend) : paragraphes
  courts, listes `- `, `**gras**`. Pas de tableaux ni de titres.
- Ajouter une **4e branche** aux trois existantes : "point de situation /
  ou en est-on" -> appeler `cockpit_situation` en premier.

### C. Brancher les outils exposes par Cockpit

Cockpit expose ses propres calculs (ceux qui alimentent les ecrans, la
montre et les murs TV) en lecture seule :

```
GET  http://192.168.254.35:4008/api/alfred-tools/manifest   -> {"ok":true,"tools":[...]}  (format tool-calling Ollama/OpenAI)
POST http://192.168.254.35:4008/api/alfred-tools/call       -> {"ok":true,"tool":"...","result":{...},"duration_ms":12}
     corps : {"tool": "cockpit_trafic", "args": {...}, "scope": "<context.scope>", "request_id": "chat-..."}
```

- **Signature** : meme recette que `/alfred/ask`, mais avec des **secrets
  distincts** `ALFRED_TOOLS_SECRET` et, facultatif, `ALFRED_TOOLS_UNSCOPED_SECRET`
  (a ajouter au `.env` de la VM, valeurs identiques a celles de Cockpit, et
  differentes entre elles). Corps vide (`b""`) pour le GET. Fenetre +/- 300 s.
- Charger le manifeste au demarrage puis le rafraichir toutes les 10 min ;
  ajouter ces outils a la liste deja passee a Ollama, **sans retirer** les
  outils existants (`query_parametrages`, etc.).
- Quand le modele appelle un outil `cockpit_*` : POST `/call`, renvoyer
  `result` (JSON) au modele comme message `tool`.
- ⚠️ **DEUX SECRETS, c'est le secret qui decide de la vue** (mis a jour le
  07/10/2026, refus par defaut) :
  - `ALFRED_TOOLS_SECRET` : appels **avec** `scope` (canal chat). Signe avec
    ce secret, un appel **sans** `scope` est refuse (403 `scope_requis`),
    quel que soit le `request_id`.
  - `ALFRED_TOOLS_UNSCOPED_SECRET` (distinct, facultatif) : vue PC Org
    complete sans `scope`, a n'utiliser **que** sur le chemin WhatsApp. Sans
    lui, les mentions WhatsApp n'ont pas d'outils `cockpit_*` (elles gardent
    leurs outils Mongo actuels).
  - La regle d'exposition `request_id.startswith("chat-")` n'est donc plus
    la frontiere de securite : choisir le secret selon le canal (`channel ==
    "cockpit"` -> secret normal + scope obligatoire ; sinon secret de vue
    complete s'il existe, sinon pas d'outils `cockpit_*`).
- `request_id` : recopier celui de la question (`chat-...`), pour la
  tracabilite seulement.
- `scope` : **c'est le code du wrapper qui le recopie** depuis
  `context.scope`, jamais le modele (il ne doit meme pas le voir). Canal chat
  sans `context.scope` (Cockpit encore en mode `prefix`) : ne pas exposer les
  outils `cockpit_*`.
- Erreurs : 401 = signature/horloge, 403 `scope_invalide` = jeton expire
  (15 min) ou altere -> dire a l'operateur de reposer la question, ne
  **jamais** rappeler sans scope pour contourner. 404 = outil inconnu
  (manifeste a recharger).

Outils disponibles (le manifeste fait foi) :

| Outil | Contenu |
|---|---|
| `cockpit_lieux` | Horaires (plages continues par public) et infos de tous les lieux du parametrage + services ; `resume` a recopier tel quel. Remplace `query_parametrages` sur le canal cockpit |
| `cockpit_situation` | Synthese en un appel : evenement, presents, trafic, meteo, alertes, compteurs et dernieres fiches MC, echeances 6 h |
| `cockpit_main_courante_fiches` | Fiches filtrables (statut, categorie, urgence, texte, depuis_heures, limite) |
| `cockpit_main_courante_fiche` | Detail d'une fiche + chronologie (par id ou numero Prysm) |
| `cockpit_main_courante_compteurs` | En cours / closes / creees aujourd'hui par categorie |
| `cockpit_trafic` | Verdict du mur circulation, accidents, temps et retard par axe |
| `cockpit_meteo` | Mur meteo : actuel, pluie, vigilance, consignes, contraintes |
| `cockpit_alertes` | Alertes actives de la centrale |
| `cockpit_timeline` | Prochaines echeances (factorisees) |
| `cockpit_presents` | Presents sur site (meme calcul que l'accueil) |
| `cockpit_wiki_procedures` | Procedures / fiches reflexes publiees |
| `cockpit_evenement` | Evenement(s) en cours et phase |

**Regle de priorite** a mettre dans le prompt : pour ces sujets, utiliser
les outils `cockpit_*` plutot que des requetes Mongo directes. Les chiffres
affiches par Cockpit sont des calculs (presents corriges des vehicules,
verdict trafic a double verrou, consignes meteo du mur) : une requete brute
donnerait un chiffre different de l'ecran que l'operateur a sous les yeux.
Et seuls les outils Cockpit appliquent les droits de l'operateur.

**Anti-hallucination** : tout resultat portant `"disponible": false` doit
etre dit tel quel ("je n'ai pas cette information en ce moment"), jamais
estime. Toutes les heures rendues sont en heure de Paris.

### D. Reponse

Inchangee, avec un point d'attention : `tool_calls` doit contenir
`{"name": ..., "arguments": {...}}` pour **chaque** appel. Cockpit les
affiche sous la reponse comme sources ("Trafic", "Main courante"...).

### E. Journalisation

`request_id` commence par `chat-` pour le widget : le garder dans les logs
pour recouper avec Cockpit (`alfred_chat_messages`, `alfred_tool_calls`).

## 3. Exemple de client Python (VM -> Cockpit)

```python
import hashlib, hmac, json, os, time, requests

COCKPIT = os.environ.get("COCKPIT_URL", "http://192.168.254.35:4008")
SCOPED = os.environ.get("ALFRED_TOOLS_SECRET", "").encode()
UNSCOPED = os.environ.get("ALFRED_TOOLS_UNSCOPED_SECRET", "").encode()  # WhatsApp seulement

def _headers(body: bytes, secret: bytes) -> dict:
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(secret, ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Alfred-Timestamp": ts, "X-Alfred-Signature": sig}

def cockpit_manifest() -> list:
    secret = SCOPED or UNSCOPED
    if not secret:
        return []
    r = requests.get(COCKPIT + "/api/alfred-tools/manifest", headers=_headers(b"", secret),
                     timeout=(3, 10), allow_redirects=False)
    r.raise_for_status()
    return r.json()["tools"]

def cockpit_call(tool: str, args: dict, scope: str | None, request_id: str | None) -> dict:
    # Le SECRET suit la presence du scope : avec scope -> secret normal ;
    # sans scope -> secret de vue complete, sinon on n'appelle pas.
    secret = SCOPED if scope else UNSCOPED
    if not secret:
        return {"disponible": False, "note": "Outil Cockpit non disponible sur ce canal."}
    payload = {"tool": tool, "args": args or {}, "request_id": request_id}
    if scope:
        payload["scope"] = scope
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")   # signer CES octets
    r = requests.post(COCKPIT + "/api/alfred-tools/call", data=body,   # et envoyer CES octets
                      headers=_headers(body, secret), timeout=(3, 20), allow_redirects=False)
    j = r.json()
    if r.status_code == 403:
        return {"disponible": False, "note": "Autorisation expiree, reposer la question."}
    return j.get("result") if j.get("ok") else {"disponible": False, "erreur": j.get("error")}
```

## 4. Le jeton `scope` (pour comprendre, pas pour le decoder)

Cockpit signe a chaque question les droits de l'operateur : categories de
main courante et types d'alertes autorises par ses groupes, valable 15 min.
Un operateur d'un groupe "Technique" ne doit pas lire les fiches Secours via
Alfred. Le wrapper le transporte sans l'interpreter ; Cockpit le verifie et
filtre. Un jeton modifie ou expire est refuse (403), jamais elargi.

## 5. Prerequis reseau et verification

- Flux **.36 -> .35:4008** a ouvrir (en plus de .35 -> .36:5005 deja ouvert
  pour WhatsApp). Adapter `COCKPIT_URL` si Cockpit est derriere un proxy.
- NTP synchronise des deux cotes (fenetre +/- 300 s dans les deux sens).
- Test rapide depuis la VM : `cockpit_manifest()` doit rendre 11 outils ;
  `cockpit_call("cockpit_situation", {}, None, "test")` rend
  `"disponible": true` seulement si `ALFRED_TOOLS_UNSCOPED_SECRET` est pose
  (sinon refus local, c'est attendu). Avec un scope reel (question du chat),
  l'appel passe par `ALFRED_TOOLS_SECRET`.
- Puis prevenir l'admin Cockpit pour passer `ALFRED_CHAT_CONTEXT_MODE=both`.
