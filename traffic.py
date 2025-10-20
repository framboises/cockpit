# traffic.py
from flask import Blueprint, jsonify
import requests
import re

traffic_bp = Blueprint('traffic', __name__)

@traffic_bp.route('/trafic/data')
def get_trafic_data():
    url = 'https://www.waze.com/row-partnerhub-api/feeds-tvt/?id=1709107524427'

    try:
        response = requests.get(url)
        response.raise_for_status()

        trafic_data = response.json()  # Conversion en JSON

        # Vérification que les données contiennent bien la clé "routes"
        if not isinstance(trafic_data, dict) or "routes" not in trafic_data:
            return jsonify({"error": "Format de données inattendu, clé 'routes' manquante"}), 500

        # Filtrage des routes dont le champ "name" commence par "#"
        trafic_data["routes"] = [
            route for route in trafic_data["routes"]
            if isinstance(route, dict) and not route.get("name", "").startswith("#")
        ]

        return jsonify(trafic_data)

    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 500

# Balises acceptées : ##, #I#, #O#, #I1#, #O2#, ...
TAG_RE = re.compile(r'^\s*(#([IO])(\d+)?#|##)\s*(.+?)\s*$')

def parse_route_name(name: str):
    """
    Exemples:
      "## Ouest"      -> (None, "Ouest")
      "#I# Ouest"     -> ("in",  "Ouest")
      "#I2# Ouest"    -> ("in",  "Ouest")
      "#O1# Panorama" -> ("out", "Panorama")
    """
    m = TAG_RE.match(name or "")
    if not m:
        return None, (name or "").strip()
    io = m.group(2)          # 'I' ou 'O' ou None
    terrain = m.group(4).strip()
    if io == 'I':
        return "in", terrain
    if io == 'O':
        return "out", terrain
    return None, terrain     # cas "##"

def classify_congestion(current_time, historic_time):
    # (ta logique d’origine)
    if not historic_time or historic_time <= 0:
        t = current_time or 0
        if t < 15:   return ("normal",    1)
        if t < 30:   return ("chargé",    2)
        if t < 60:   return ("saturé",    3)
        if t >= 60:  return ("bouchon",   4)
        return ("normal", 1)

    ratio = (current_time or 0) / float(historic_time)
    if ratio < 0.9:    return ("plus fluide", 0)
    if ratio < 1.2:    return ("normal",      1)
    if ratio < 1.6:    return ("chargé",      2)
    if ratio < 2.5:    return ("saturé",      3)
    return ("bouchon", 4)

@traffic_bp.route('/trafic/waiting_data_structured')
def get_trafic_data_parking_structured():
    url = 'https://www.waze.com/row-partnerhub-api/feeds-tvt/?id=1709107524427'
    try:
        response = requests.get(url)
        response.raise_for_status()
        trafic_data = response.json()

        if not isinstance(trafic_data, dict) or "routes" not in trafic_data:
            return jsonify({"error": "Format de données inattendu, clé 'routes' manquante"}), 500

        # Agrégateur: clé = (terrain, direction)
        agg = {}

        for route in trafic_data["routes"]:
            if not isinstance(route, dict):
                continue

            raw_name = route.get("name", "")
            # On ne garde que ##, #I#, #O#, #I1#, #O2#, etc.
            if not (raw_name.startswith("##") or raw_name.startswith("#I") or raw_name.startswith("#O")):
                continue

            direction, terrain = parse_route_name(raw_name)
            cur  = int(route.get("time", 0) or 0)
            hist = int(route.get("historicTime", 0) or 0)

            key = (terrain, direction)
            if key not in agg:
                agg[key] = {
                    "terrain": terrain,
                    "direction": direction,    # "in" | "out" | None
                    "sumCurrent": 0,
                    "sumHistoric": 0,
                    "routesCount": 0,
                }
            agg[key]["sumCurrent"]  += max(0, cur)
            agg[key]["sumHistoric"] += max(0, hist)
            agg[key]["routesCount"] += 1

        terrains = []
        for (_terrain, _direction), rec in agg.items():
            sum_cur  = rec["sumCurrent"]
            sum_hist = rec["sumHistoric"]

            # Ratio/delta sur les SOMMES
            ratio_val = (sum_cur / sum_hist) if sum_hist > 0 else None
            ratio_round = round(ratio_val, 2) if ratio_val is not None else None
            delta_s   = max(0, sum_cur - sum_hist) if sum_hist > 0 else None
            delta_pct = round((ratio_val - 1) * 100) if ratio_val is not None else None

            status, severity = classify_congestion(sum_cur, sum_hist)

            terrains.append({
                "terrain": rec["terrain"],
                "direction": rec["direction"],
                "currentTime": sum_cur,
                "historicTime": sum_hist,
                "ratio": ratio_round,        # ex: 1.27
                "deltaSeconds": delta_s,     # ≥ 0 si hist > 0, sinon None
                "deltaPercent": delta_pct,   # ex: 27
                "status": status,
                "severity": severity,
                "routesCount": rec["routesCount"],
            })

        # Tri par ratio décroissant (None en fin)
        terrains.sort(key=lambda t: (-1 if t["ratio"] is None else t["ratio"]), reverse=True)

        return jsonify({
            "terrains": terrains,
            "updateTime": trafic_data.get("updateTime")
        })

    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 500
    
@traffic_bp.route('/alerts')
def alerts():
    # Remplacez l'URL par celle qui contient les alertes
    url = "https://www.waze.com/row-partnerhub-api/partners/19308574489/waze-feeds/fa96cebf-1625-4b4f-91a0-a5af6db60e49?format=1"
    response = requests.get(url)
    data = response.json()
    alerts = data.get('alerts', [])
    return jsonify(alerts)