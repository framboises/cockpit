# Valhalla — VM dédiée

Cockpit utilise un service Valhalla auto-hébergé pour le calcul d'itinéraires
(blueprint `routing.py`, modale Cockpit, tablette Field). Le service tourne
sur une **VM Linux dédiée**, pas dans le `docker-compose.yml` de Cockpit.

Ce dossier sert de **runbook** : tous les scripts qui s'exécutent côté VM
Valhalla sont versionnés ici pour reproductibilité, mais ils ne sont **jamais
lancés depuis le serveur Cockpit**. Copier sur la VM (scp / git pull) avant
exécution.

---

## Topologie

| Composant | Emplacement |
|---|---|
| Cockpit (Flask) | Serveur Windows (`E:\TITAN\production\cockpit`) |
| Valhalla (Docker) | VM Linux `srv-safe-docker.aco.local` |
| URL service | `http://srv-safe-docker.aco.local:8002` |
| Container Docker | `cockpit-valhalla` (image `gisops/valhalla:latest`) |
| Volume hôte | `/opt/cockpit-docker/valhalla/custom_files/` |
| PBF source | `lemans-circuit.osm.pbf` (clip bbox circuit + 5 km, ~7.6 MB) |
| PBF backup | `lemans-circuit.osm.pbf.bak` (source pour relance idempotente) |
| Tuiles | `valhalla_tiles/` + `valhalla_tiles.tar` |

Côté Cockpit, la variable d'env **`VALHALLA_URL`** pointe sur cette VM :

```
VALHALLA_URL=http://srv-safe-docker.aco.local:8002
```

Si la VM tombe ou que l'URL change, `routing.py` repassera automatiquement
sur le fallback stub (trait droit haversine + ETA approximé).

---

## Bbox du clip

```
0.144, 47.898, 0.306, 48.006     (W, S, E, N)
```

Englobe le circuit complet (Bugatti + GP) + Mulsanne + Arnage + ~5 km de
marge. Source géographique : **Geofabrik Pays-de-la-Loire**
(`https://download.geofabrik.de/europe/france/pays-de-la-loire-latest.osm.pbf`).

---

## Patches OSM appliqués localement

OSM tague la voirie interne du circuit en `highway=service` (sous-tag
`service=parking_aisle` ou pas), ce qui fait que Valhalla applique des
vitesses défaut **7-25 km/h** sur l'intégralité du paddock — totalement
irréaliste pour des véhicules d'intervention.

**Patch en place** : `maxspeed=40` forcé sur tous les `highway=service`
sans `maxspeed` explicite. ~4 190 ways patchés sur 4 605, 415 préservés
parce qu'ils ont déjà un maxspeed OSM. Résultat : ETA divisée par ~3,
vitesse moyenne intra-paddock 10 km/h → 36 km/h. Voir `patch_aco_speeds.py`.

Le patch est **purement local au PBF clippé**, OSM upstream n'est jamais
modifié.

---

## Procédures côté VM

### Installer pyosmium (une fois)

```bash
sudo apt install python3-pyosmium    # Ubuntu 24.04, pyosmium 4.x
```

### Rebuild complet (PBF source + patch + tuiles)

À faire :
- Quand on rafraîchit le PBF Geofabrik (drift OSM upstream),
- Quand on modifie le patch `patch_aco_speeds.py` (cible des ways,
  vitesse cible, etc.).

```bash
# 0. SCP du patch_aco_speeds.py depuis le repo si modifié
scp infra/valhalla/patch_aco_speeds.py adminsafe@srv-safe-docker.aco.local:/home/adminsafe/

# Sur la VM :
cd /opt/cockpit-docker/valhalla/custom_files

# 1. (Optionnel) Rafraîchir le PBF source clippé depuis Geofabrik
#    -> télécharger pays-de-la-loire-latest, osmium extract sur la bbox,
#    écraser lemans-circuit.osm.pbf.bak. Voir section "Rafraîchir le PBF".

# 2. Appliquer le patch maxspeed (idempotent grâce au .bak)
python3 /home/adminsafe/patch_aco_speeds.py \
    lemans-circuit.osm.pbf.bak \
    lemans-circuit.osm.pbf

# 3. Supprimer les tuiles existantes (côté container car owned root)
docker exec -u root cockpit-valhalla rm -rf \
    /custom_files/valhalla_tiles \
    /custom_files/valhalla_tiles.tar

# 4. Restart : gisops/valhalla rebuilde au boot s'il ne trouve pas de tuiles
docker restart cockpit-valhalla

# 5. Suivre les logs jusqu'à "valhalla_service"
docker logs -f cockpit-valhalla

# 6. Vérifier la santé
curl -s http://localhost:8002/status | jq
```

Durée typique du rebuild des tuiles sur ce PBF (~7.6 MB) : **15-20 secondes**.

### Patch uniquement (sans toucher au PBF source)

Si le PBF source est déjà à jour et qu'on veut juste réappliquer le patch
maxspeed :

```bash
cd /opt/cockpit-docker/valhalla/custom_files
python3 /home/adminsafe/patch_aco_speeds.py \
    lemans-circuit.osm.pbf.bak \
    lemans-circuit.osm.pbf
docker exec -u root cockpit-valhalla rm -rf \
    /custom_files/valhalla_tiles \
    /custom_files/valhalla_tiles.tar
docker restart cockpit-valhalla
```

### Rafraîchir le PBF source (drift OSM upstream)

```bash
cd /opt/cockpit-docker/valhalla/custom_files

# 1. Télécharger Geofabrik
curl -fL --progress-bar -o pays-de-la-loire.osm.pbf \
    https://download.geofabrik.de/europe/france/pays-de-la-loire-latest.osm.pbf

# 2. Clip sur la bbox circuit + 5 km
osmium extract -b "0.144,47.898,0.306,48.006" \
    pays-de-la-loire.osm.pbf \
    -o lemans-circuit.osm.pbf.bak --overwrite

# 3. Cleanup
rm pays-de-la-loire.osm.pbf

# 4. Continuer avec "Patch uniquement" ci-dessus.
```

---

## Pièges (lessons learned)

1. **`rm` côté hôte échoue silencieusement** sur les fichiers de tuiles : le
   container tourne en UID `valhalla` et écrit en `root:root` côté hôte. Si
   on supprime avec `rm` sans `sudo`, ça rate sans erreur explicite, le
   restart réutilise les anciennes tuiles, et on croit que le rebuild a
   eu lieu alors que non. **Toujours utiliser `docker exec -u root` ou
   `sudo rm`** pour ces fichiers.

2. **pyosmium 4.x sur Ubuntu 24.04** : `apply_file()` ne déduit pas le format
   depuis l'extension `.bak`. Si tu passes un `.bak` directement, ça
   plante. Solution : utiliser `osmium.io.File(path, "osm.pbf")`
   explicitement. C'est déjà fait dans `patch_aco_speeds.py`.

3. **Idempotence** : le patch repart toujours du `.bak` (source de vérité
   immutable), pas du fichier déjà patché. Tu peux relancer N fois sans
   accumulation de tags.

4. **Tuiles cached côté Valhalla** : même après modification du PBF, si
   `valhalla_tiles.tar` existe, gisops/valhalla réutilise les anciennes
   tuiles. Toujours supprimer le `.tar` ET le dossier `valhalla_tiles/`.

---

## Debugging

### Vitesses réellement appliquées par Valhalla

Depuis Cockpit, lancer le script `trace_valhalla` qui pose un appel
`/route` puis `/trace_attributes` et te sort les vitesses edge par edge.
Voir le snippet PowerShell dans l'historique des sessions ou demander à
Claude Code de te le régénérer.

### Healthcheck

```bash
curl -s http://srv-safe-docker.aco.local:8002/status | jq
# attendu : {"version":"3.5.1","tileset_last_modified":<unix_ts>,"available_actions":[...]}
```

### Test fonctionnel

```bash
curl -sX POST http://srv-safe-docker.aco.local:8002/route \
  -H "Content-Type: application/json" \
  -d '{"locations":[{"lat":47.94658,"lon":0.21165},{"lat":47.94946,"lon":0.20590}],"costing":"auto"}' \
  | jq '.trip.summary'
# Attendu apres patch : time ~280s pour 2.89 km
```

---

## Évolutions possibles

- **Vitesses différenciées par usage** : actuellement 40 km/h partout sur
  `highway=service`. On peut affiner : 60 km/h sur les axes paddock
  principaux (Allée Bugatti...), 20-30 km/h sur les vraies allées de
  parking étroites. Nécessite une liste blanche de way_ids dans le script.

- **Profil emergency custom** : compiler un costing Valhalla dédié
  intervention au lieu du `auto + ignore_*` actuel (cf. CLAUDE.md
  section routing). Travail conséquent, à éviter tant que `auto + ignore_*`
  suffit.

- **Cron de rafraîchissement OSM** : aujourd'hui le PBF est figé au
  moment de la dernière exécution manuelle de la procédure "rafraîchir
  le PBF source". On pourrait cronner un rebuild mensuel pour capter
  les modifications upstream.
