#!/usr/bin/env bash
#
# build_valhalla_tiles.sh
#
# Telecharge l'extrait OSM de la region Pays-de-la-Loire (Geofabrik), clippe
# une bbox autour du circuit des 24h du Mans (rayon 5 km + 1 km de marge), et
# place le PBF clippe dans le volume monte par le container Valhalla.
#
# Au demarrage, gisops/valhalla construit automatiquement les tuiles si elles
# ne sont pas presentes dans /custom_files/valhalla_tiles/.
#
# Pre-requis :
#   - osmium-tool (apt: osmium-tool / brew: osmium-tool)
#   - docker compose
#
# Usage :
#   bash scripts/build_valhalla_tiles.sh           # build initial / incremental
#   bash scripts/build_valhalla_tiles.sh --rebuild # force rebuild des tuiles
#
# Variables d'env (optionnelles) :
#   OSM_PBF_URL            URL Geofabrik (defaut Pays-de-la-Loire)
#   ROUTING_BBOX           bbox W,S,E,N (defaut circuit + 6 km)

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TILES_DIR="${DIR}/valhalla/custom_files"
mkdir -p "$TILES_DIR"

PBF_URL="${OSM_PBF_URL:-https://download.geofabrik.de/europe/france/pays-de-la-loire-latest.osm.pbf}"
# Bbox : centre 47.9517, 0.2247 (paddock circuit) +/- 0.06 deg lat (~6.6 km)
# et +/- 0.08 deg lon (~6 km a 48 N). Englobe circuit complet + Mulsanne + Arnage.
BBOX="${ROUTING_BBOX:-0.144,47.898,0.306,48.006}"
PBF_FULL="${TILES_DIR}/pays-de-la-loire.osm.pbf"
PBF_CLIP="${TILES_DIR}/lemans-circuit.osm.pbf"

REBUILD="no"
if [[ "${1:-}" == "--rebuild" ]]; then
  REBUILD="yes"
fi

if ! command -v osmium >/dev/null 2>&1; then
  echo "ERREUR: osmium-tool n'est pas installe." >&2
  echo "Installer :" >&2
  echo "  Linux  : sudo apt install osmium-tool" >&2
  echo "  macOS  : brew install osmium-tool" >&2
  exit 1
fi

if [[ ! -f "$PBF_CLIP" || "$REBUILD" == "yes" ]]; then
  echo "[1/3] Telechargement PBF -> $PBF_FULL"
  curl -fL --progress-bar -o "$PBF_FULL" "$PBF_URL"

  echo "[2/3] Clip bbox $BBOX -> $PBF_CLIP"
  osmium extract -b "$BBOX" "$PBF_FULL" -o "$PBF_CLIP" --overwrite
  rm -f "$PBF_FULL"
  echo "PBF clippe taille : $(du -h "$PBF_CLIP" | cut -f1)"
else
  echo "[1-2/3] PBF clippe deja present : $PBF_CLIP (utiliser --rebuild pour regenerer)"
fi

if [[ "$REBUILD" == "yes" ]]; then
  echo "Suppression des anciennes tuiles..."
  rm -rf "${TILES_DIR}/valhalla_tiles" "${TILES_DIR}/valhalla_tiles.tar"
fi

echo "[3/3] Demarrage du service Valhalla (build des tuiles automatique au premier lancement)"
cd "$DIR"
docker compose up -d valhalla

echo ""
echo "Tuiles en cours de construction. Suivre :"
echo "  docker compose logs -f valhalla"
echo ""
echo "Quand 'valhalla_service' apparait dans les logs, tester :"
echo "  curl http://localhost:8002/status"
