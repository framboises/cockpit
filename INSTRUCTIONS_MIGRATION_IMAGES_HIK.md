# Migration images Hikvision : GridFS -> disque

## Contexte

Le recepteur webhook Hikvision (`ecoutehik.py`) stockait les images ANPR (plaques + vehicules) dans **MongoDB GridFS** (collection `hik_images`). Les documents `hik_anpr` contenaient des champs `plate_image_id` et `vehicle_image_id` (des ObjectId GridFS).

Le nouveau script (`ecoutehik2.py`) stocke les images sur **disque** dans `E:/TITAN/production/hik_images/YYYY/MM/DD/camera/` et enregistre le chemin relatif dans de nouveaux champs `plate_image_path` et `vehicle_image_path`.

Les anciens documents en base ont toujours les champs `_image_id` (GridFS). Les nouveaux documents ont les champs `_image_path` (chemin disque). Les deux formats coexistent dans la collection `hik_anpr`.

## Ce qu'il faut modifier

### 1. Backend : `cockpit/anpr.py`

**Serialisation** (fonction `_serialize`, ~ligne 200) :

Les champs actuels :
```python
"plate_image_id": str(doc["plate_image_id"]) if doc.get("plate_image_id") else None,
"vehicle_image_id": str(doc["vehicle_image_id"]) if doc.get("vehicle_image_id") else None,
```

Ajouter les nouveaux champs a cote (ne pas supprimer les anciens) :
```python
"plate_image_id": str(doc["plate_image_id"]) if doc.get("plate_image_id") else None,
"vehicle_image_id": str(doc["vehicle_image_id"]) if doc.get("vehicle_image_id") else None,
"plate_image_path": doc.get("plate_image_path"),
"vehicle_image_path": doc.get("vehicle_image_path"),
```

**Route image** (`/api/anpr/image/<image_id>`, ~ligne 479) :

Actuellement elle ne sert que du GridFS. Il faut gerer les deux cas : si l'argument ressemble a un ObjectId (24 car hex), lire depuis GridFS. Sinon, c'est un chemin relatif, lire depuis le disque.

```python
import os
HIK_IMAGE_DIR = "E:/TITAN/production/hik_images"

@anpr_bp.route("/api/anpr/image/<path:image_ref>")
def anpr_image(image_ref):
    """Sert une image : chemin disque (nouveau) ou ObjectId GridFS (ancien)."""
    _ensure_db()

    # Nouveau format : chemin relatif sur disque
    if "/" in image_ref:
        safe = os.path.normpath(image_ref)
        if ".." in safe:
            abort(400)
        full_path = os.path.join(HIK_IMAGE_DIR, safe)
        if not os.path.abspath(full_path).startswith(os.path.abspath(HIK_IMAGE_DIR)):
            abort(400)
        if os.path.isfile(full_path):
            return send_file(full_path, mimetype="image/jpeg",
                             headers={"Cache-Control": "public, max-age=86400"})
        # Fichier supprime (purge) -> fallback pixel transparent ci-dessous

    # Ancien format : ObjectId GridFS
    else:
        try:
            oid = ObjectId(image_ref)
            grid_file = _fs.get(oid)
            return Response(
                grid_file.read(),
                mimetype="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400"},
            )
        except Exception:
            pass

    # Fallback : pixel transparent 1x1
    return Response(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
        mimetype="image/png",
        status=404,
    )
```

Important : la signature de la route passe de `<image_id>` a `<path:image_ref>` pour accepter les chemins avec des `/`.

### 2. Frontend : `cockpit/static/js/anpr.js`

Le JS construit les URLs image a 4 endroits avec `API.image + encodeURIComponent(r.vehicle_image_id)` ou `r.plate_image_id`.

Il faut privilegier `_image_path` (nouveau) et tomber en fallback sur `_image_id` (ancien GridFS).

Ajouter une fonction helper en haut du fichier (apres la declaration de `API`) :

```javascript
function imgUrl(r, type) {
    // type = "vehicle" ou "plate"
    var path = r[type + "_image_path"];
    var id   = r[type + "_image_id"];
    if (path) return API.image + encodeURIComponent(path);
    if (id)   return API.image + encodeURIComponent(id);
    return null;
}
```

Puis remplacer les references directes :

| Ligne | Ancien | Nouveau |
|-------|--------|---------|
| ~220 | `r.vehicle_image_id` (condition) + `API.image + encodeURIComponent(r.vehicle_image_id)` | `imgUrl(r, "vehicle")` (condition + src) |
| ~277 | `r.vehicle_image_id` + `API.image + encodeURIComponent(r.vehicle_image_id)` | `imgUrl(r, "vehicle")` |
| ~307 | `r.vehicle_image_id` + `API.image + encodeURIComponent(r.vehicle_image_id)` | `imgUrl(r, "vehicle")` |
| ~309 | `r.plate_image_id` + `API.image + encodeURIComponent(r.plate_image_id)` | `imgUrl(r, "plate")` |

Exemple pour la ligne ~220 (mkRow) :
```javascript
// Avant :
if (r.vehicle_image_id) { var im = document.createElement("img"); im.className = "anpr-thumb"; im.src = API.image + encodeURIComponent(r.vehicle_image_id); ...

// Apres :
var vehicleUrl = imgUrl(r, "vehicle");
if (vehicleUrl) { var im = document.createElement("img"); im.className = "anpr-thumb"; im.src = vehicleUrl; ...
```

Meme pattern pour les 3 autres endroits.

## Resume des fichiers a modifier

| Fichier | Modification |
|---------|-------------|
| `cockpit/anpr.py` ~ligne 200 | Ajouter `plate_image_path` et `vehicle_image_path` dans `_serialize` |
| `cockpit/anpr.py` ~ligne 479 | Route image : accepter chemin disque + fallback GridFS |
| `cockpit/static/js/anpr.js` ~ligne 15 | Ajouter helper `imgUrl(r, type)` |
| `cockpit/static/js/anpr.js` lignes 220, 277, 307, 309 | Utiliser `imgUrl()` au lieu de `r.*_image_id` direct |

## Comportement attendu

- Document ancien (GridFS) : `plate_image_path` est null, `plate_image_id` existe -> URL `/api/anpr/image/507f1f77bcf86cd799439011` -> sert depuis GridFS
- Document nouveau (disque) : `plate_image_path` existe -> URL `/api/anpr/image/2026/04/06/cam1/plate_093040_a1b2c3d4.jpg` -> sert depuis disque
- Image purgee du disque : retourne le pixel transparent 1x1 (meme fallback qu'avant)
- Aucun changement visible pour l'utilisateur : les images s'affichent pareil
