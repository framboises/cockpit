# traffic.py
from flask import Blueprint, jsonify
import requests

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

@traffic_bp.route('/trafic/waiting_data_structured')
def get_trafic_data_parking_structured():
    url = 'https://www.waze.com/row-partnerhub-api/feeds-tvt/?id=1709107524427'
    try:
        response = requests.get(url)
        response.raise_for_status()
        trafic_data = response.json()  # Conversion en JSON

        # Vérifier que le JSON contient bien la clé "routes"
        if not isinstance(trafic_data, dict) or "routes" not in trafic_data:
            return jsonify({"error": "Format de données inattendu, clé 'routes' manquante"}), 500

        structured_routes = []
        for route in trafic_data["routes"]:
            if not isinstance(route, dict):
                continue

            name = route.get("name", "")
            # Ne traiter que les routes dont le nom commence par "##"
            if not name.startswith("##"):
                continue

            # Extraction du nom du terrain en retirant le préfixe "##" et les espaces superflus
            terrain_name = name.lstrip("#").strip()
            current_time = route.get("time", 0)
            historic_time = route.get("historicTime", 0)
            # Calcul du ratio (si historic_time > 0)
            ratio = current_time / historic_time if historic_time else 0

            # Détermination du statut en fonction du ratio
            # Par exemple, si le temps actuel est proche du temps historique, c'est "normal"
            if ratio <= 1.1:
                status = "normal"
                severity = 1
            elif ratio <= 1.5:
                status = "modéré"
                severity = 2
            else:
                status = "grave"
                severity = 3

            structured_routes.append({
                "terrain": terrain_name,
                "currentTime": current_time,
                "historicTime": historic_time,
                "ratio": round(ratio, 2),
                "status": status,
                "severity": severity
            })

        # On peut aussi renvoyer l'heure de mise à jour si nécessaire
        return jsonify({
            "terrains": structured_routes,
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