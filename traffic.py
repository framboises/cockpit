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
    
@traffic_bp.route('/alerts')
def alerts():
    # Remplacez l'URL par celle qui contient les alertes
    url = "https://www.waze.com/row-partnerhub-api/partners/19308574489/waze-feeds/fa96cebf-1625-4b4f-91a0-a5af6db60e49?format=1"
    response = requests.get(url)
    data = response.json()
    alerts = data.get('alerts', [])
    return jsonify(alerts)