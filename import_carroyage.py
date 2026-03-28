#!/usr/bin/env python3
"""
Import du carroyage 100m depuis le GPKG vers MongoDB (collections grid_ref_qgis + grid_ref).
Convertit les coordonnées EPSG:3857 (Web Mercator) → WGS84 (lat/lng).

Usage:
    python import_carroyage.py [--mongo-uri mongodb://localhost:27017/] [--db titan]
"""

import sqlite3
import struct
import math
import argparse
from pymongo import MongoClient

GPKG_PATH = "uploads/carroyage-100-ELIPSO.gpkg"


# ---------- Conversion EPSG:3857 → WGS84 ----------

def mercator_to_wgs84(x, y):
    """Convertit des coordonnées Web Mercator (EPSG:3857) en WGS84 (EPSG:4326)."""
    lng = x * 180.0 / 20037508.342789244
    lat = math.degrees(2 * math.atan(math.exp(y * math.pi / 20037508.342789244)) - math.pi / 2)
    return lat, lng


# ---------- Lecture du GPKG ----------

def parse_gpkg_geom(blob):
    """Parse un GeoPackage Binary et retourne les points [(x, y), ...]."""
    # Header: "GP" (2) + version (1) + flags (1) + srs_id (4) + envelope
    flags = blob[3]
    envelope_type = (flags >> 1) & 0x07
    envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    env_size = envelope_sizes.get(envelope_type, 0)
    header_size = 8 + env_size

    wkb = blob[header_size:]
    byte_order = wkb[0]
    fmt = '<' if byte_order == 1 else '>'
    num_points = struct.unpack(f'{fmt}I', wkb[5:9])[0]
    points = []
    offset = 9
    for _ in range(num_points):
        x, y = struct.unpack(f'{fmt}dd', wkb[offset:offset + 16])
        points.append((x, y))
        offset += 16
    return points


def read_gpkg(path):
    """Lit les lignes horizontales et verticales depuis la geometrie WKB du GeoPackage."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()

    cur.execute('SELECT geom FROM grille')
    h_lines_raw = []  # (x_start, x_end, y) pour lignes horizontales
    v_lines_raw = []  # (x, y_start, y_end) pour lignes verticales

    for (blob,) in cur.fetchall():
        pts = parse_gpkg_geom(blob)
        if len(pts) < 2:
            continue
        x1, y1 = pts[0]
        x2, y2 = pts[-1]

        if abs(y1 - y2) < 0.01:  # ligne horizontale (y constant)
            h_lines_raw.append((min(x1, x2), max(x1, x2), (y1 + y2) / 2))
        elif abs(x1 - x2) < 0.01:  # ligne verticale (x constant)
            v_lines_raw.append(((x1 + x2) / 2, max(y1, y2), min(y1, y2)))

    # Trier : h par y desc, v par x asc
    h_lines_raw.sort(key=lambda r: -r[2])
    v_lines_raw.sort(key=lambda r: r[0])

    conn.close()
    return h_lines_raw, v_lines_raw


# ---------- Construction des documents MongoDB ----------

def build_grid_lines(h_lines_raw, v_lines_raw):
    """Construit le document grid_lines (carroyage 100m) pour grid_ref_qgis."""
    h_lines = []
    for left_x, right_x, y in h_lines_raw:
        lat, lng_start = mercator_to_wgs84(left_x, y)
        _, lng_end = mercator_to_wgs84(right_x, y)
        h_lines.append({"lat": lat, "lng_start": lng_start, "lng_end": lng_end})

    v_lines = []
    for x, top_y, bot_y in v_lines_raw:
        lat_start, lng = mercator_to_wgs84(x, top_y)
        lat_end, _ = mercator_to_wgs84(x, bot_y)
        v_lines.append({"lat_start": lat_start, "lat_end": lat_end, "lng": lng})

    num_cols = len(v_lines) - 1
    num_rows = len(h_lines) - 1

    # Bounds
    bounds = {
        "north": h_lines[0]["lat"],
        "south": h_lines[-1]["lat"],
        "west": v_lines[0]["lng"],
        "east": v_lines[-1]["lng"],
    }

    return {
        "type": "grid_lines",
        "h_lines": h_lines,
        "v_lines": v_lines,
        "num_cols": num_cols,
        "num_rows": num_rows,
        "bounds": bounds,
        "col_offset": 10,
        "row_offset": 4,
    }


def build_grid_lines_25(grid_100):
    """Construit le sous-carroyage 25m à partir du carroyage 100m."""
    h100 = grid_100["h_lines"]
    v100 = grid_100["v_lines"]

    # Interpoler 4 subdivisions entre chaque ligne 100m
    h_lines_25 = []
    for i in range(len(h100) - 1):
        lat_top = h100[i]["lat"]
        lat_bot = h100[i + 1]["lat"]
        lng_start = h100[i]["lng_start"]
        lng_end = h100[i]["lng_end"]
        for sub in range(4):
            frac = sub / 4.0
            lat = lat_top + frac * (lat_bot - lat_top)
            h_lines_25.append({"lat": lat, "lng_start": lng_start, "lng_end": lng_end})
    # Dernière ligne
    h_lines_25.append({
        "lat": h100[-1]["lat"],
        "lng_start": h100[-1]["lng_start"],
        "lng_end": h100[-1]["lng_end"],
    })

    v_lines_25 = []
    for i in range(len(v100) - 1):
        lng_left = v100[i]["lng"]
        lng_right = v100[i + 1]["lng"]
        lat_start = v100[i]["lat_start"]
        lat_end = v100[i]["lat_end"]
        for sub in range(4):
            frac = sub / 4.0
            lng = lng_left + frac * (lng_right - lng_left)
            v_lines_25.append({"lat_start": lat_start, "lat_end": lat_end, "lng": lng})
    # Dernière ligne
    v_lines_25.append({
        "lat_start": v100[-1]["lat_start"],
        "lat_end": v100[-1]["lat_end"],
        "lng": v100[-1]["lng"],
    })

    num_cols_25 = len(v_lines_25) - 1
    num_rows_25 = len(h_lines_25) - 1

    return {
        "type": "grid_lines_25",
        "h_lines": h_lines_25,
        "v_lines": v_lines_25,
        "num_cols": num_cols_25,
        "num_rows": num_rows_25,
    }


def build_grid_ref(grid_100):
    """Construit les documents grid_ref (centre de chaque cellule → coordonnées)."""
    h = grid_100["h_lines"]
    v = grid_100["v_lines"]
    num_cols = grid_100["num_cols"]
    num_rows = grid_100["num_rows"]

    col_offset = grid_100.get("col_offset", 0)
    row_offset = grid_100.get("row_offset", 0)

    docs = []
    for ri in range(num_rows):
        row_num = ri + 1 - row_offset
        if row_num < 1:
            continue
        lat_center = (h[ri]["lat"] + h[ri + 1]["lat"]) / 2.0
        for ci in range(num_cols):
            adjusted = ci - col_offset
            if adjusted < 0:
                continue
            lng_center = (v[ci]["lng"] + v[ci + 1]["lng"]) / 2.0
            col_label = col_to_letter(adjusted)
            ref = f"{col_label}{row_num}"
            docs.append({
                "grid_ref": ref,
                "latitude": round(lat_center, 7),
                "longitude": round(lng_center, 7),
            })
    return docs


def col_to_letter(index):
    """Convertit un index de colonne (0-based) en lettre(s) : 0→A, 25→Z, 26→AA, etc."""
    result = ""
    i = index
    while True:
        result = chr(ord("A") + (i % 26)) + result
        i = i // 26 - 1
        if i < 0:
            break
    return result


# ---------- Import MongoDB ----------

def import_to_mongo(mongo_uri, db_name, grid_100_doc, grid_25_doc, grid_ref_docs):
    client = MongoClient(mongo_uri)
    db = client[db_name]

    # grid_ref_qgis
    coll_qgis = db["grid_ref_qgis"]
    coll_qgis.delete_many({"type": "grid_lines"})
    coll_qgis.delete_many({"type": "grid_lines_25"})
    coll_qgis.insert_one(grid_100_doc)
    print(f"  [grid_ref_qgis] grid_lines insere ({grid_100_doc['num_cols']}x{grid_100_doc['num_rows']})")
    coll_qgis.insert_one(grid_25_doc)
    print(f"  [grid_ref_qgis] grid_lines_25 insere ({grid_25_doc['num_cols']}x{grid_25_doc['num_rows']})")

    # grid_ref
    coll_ref = db["grid_ref"]
    coll_ref.drop()
    coll_ref.insert_many(grid_ref_docs)
    print(f"  [grid_ref] {len(grid_ref_docs)} cellules inserees")

    client.close()


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description="Import carroyage GPKG -> MongoDB")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017/", help="URI MongoDB")
    parser.add_argument("--db", default="titan", help="Nom de la base (defaut: titan)")
    parser.add_argument("--dry-run", action="store_true", help="Affiche sans inserer")
    args = parser.parse_args()

    print(f"Lecture du GPKG: {GPKG_PATH}")
    h_raw, v_raw = read_gpkg(GPKG_PATH)
    print(f"  {len(h_raw)} lignes horizontales, {len(v_raw)} lignes verticales")

    print("Construction du carroyage 100m...")
    grid_100 = build_grid_lines(h_raw, v_raw)
    print(f"  Grille: {grid_100['num_cols']} cols x {grid_100['num_rows']} rows")
    print(f"  Bounds: N={grid_100['bounds']['north']:.6f}, S={grid_100['bounds']['south']:.6f}, "
          f"W={grid_100['bounds']['west']:.6f}, E={grid_100['bounds']['east']:.6f}")

    print("Construction du sous-carroyage 25m...")
    grid_25 = build_grid_lines_25(grid_100)
    print(f"  Sous-grille: {grid_25['num_cols']} cols x {grid_25['num_rows']} rows")

    print("Construction des references de cellules...")
    grid_ref_docs = build_grid_ref(grid_100)
    print(f"  {len(grid_ref_docs)} cellules")

    if args.dry_run:
        print("\n[DRY RUN] Aucune insertion MongoDB.")
        print(f"  Exemple h_line[0]: {grid_100['h_lines'][0]}")
        print(f"  Exemple v_line[0]: {grid_100['v_lines'][0]}")
        print(f"  Exemple grid_ref[0]: {grid_ref_docs[0]}")
        print(f"  Exemple grid_ref[-1]: {grid_ref_docs[-1]}")
    else:
        print(f"\nInsertion dans MongoDB ({args.mongo_uri}, db={args.db})...")
        import_to_mongo(args.mongo_uri, args.db, grid_100, grid_25, grid_ref_docs)
        print("\nTermine !")


if __name__ == "__main__":
    main()
