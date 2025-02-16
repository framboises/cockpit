let accidentCount = 0; // Compteur d'accidents
let jamCount = 0; // Compteur d'embouteillages
let currentAlertIndex = 0;
// Pendant l'updateAlerts, ajouter les alertes dans les tableaux respectifs
let accidentAlerts = [];
let jamAlerts = [];

function createPinIcon(iconUrl, pinColor) {
    return L.divIcon({
        className: 'custom-pin-icon',
        html: `
            <div class="pin-container">
                <div class="pin-icon" style="background-image: url(${iconUrl}); background-color: ${pinColor}; border-color: ${pinColor};"></div>
                <div class="pin-pointer" style="border-top-color: ${pinColor};"></div>
            </div>
        `,
        iconSize: [32, 42],
        iconAnchor: [16, 42],
        popupAnchor: [0, -42]
    });
}

// Fonction pour formater les timestamps en français
function formatTimestamp(pubMillis) {
    const date = new Date(pubMillis);
    return date.toLocaleString('fr-FR');
}

// Fonction pour regrouper les points par rue et dessiner une polyline
function connectPointsByStreet(alerts) {
    const streets = {};

    // Regrouper les points par nom de rue
    alerts.forEach(alert => {
        if (alert.type === 'ROAD_CLOSED' && alert.street) {
            if (!streets[alert.street]) {
                streets[alert.street] = [];
            }
            streets[alert.street].push([alert.location.y, alert.location.x]);
        }
    });

    // Dessiner une polyline pour chaque rue
    Object.keys(streets).forEach(street => {
        const points = streets[street];
        if (points.length > 1) {
            const polyline = L.polyline(points, {
                color: '#ff0000', // Rouge
                dashArray: '10, 10', // Pointillé rouge et blanc
                weight: 6 // Épaisseur de la ligne
            }).addTo(map);
            window.alertMarkers.push(polyline);
        }
    });
}

// Fonction pour zoomer sur un pin spécifique
function zoomOnAlert(alert) {
    const lat = alert.location.y;
    const lng = alert.location.x;
    map.setView([lat, lng], 16); // Zoom au niveau 16
}

// Fonction pour gérer les clics sur le compteur d'accidents
function handleAccidentClick() {
    if (accidentCount > 0) {
        currentAlertIndex = (currentAlertIndex + 1) % accidentAlerts.length; // Passer à l'alerte suivante
        const alert = accidentAlerts[currentAlertIndex]; // Récupérer l'alerte correspondante
        zoomOnAlert(alert);
    }
}

// Fonction pour gérer les clics sur le compteur d'embouteillages
function handleJamClick() {
    if (jamCount > 0) {
        currentAlertIndex = (currentAlertIndex + 1) % jamAlerts.length; // Passer à l'alerte suivante
        const alert = jamAlerts[currentAlertIndex]; // Récupérer l'alerte correspondante
        zoomOnAlert(alert);
    }
}

// Ajouter les gestionnaires de clics sur les compteurs
document.getElementById('accident-counter').addEventListener('click', handleAccidentClick);
document.getElementById('jam-counter').addEventListener('click', handleJamClick);

// Ne pas oublier de trier les alertes par `pubMillis` en ordre décroissant lors de l'initialisation
function sortAlertsByTime(alerts) {
    return alerts.sort((a, b) => b.pubMillis - a.pubMillis);
}

// Fonction pour mettre à jour les alertes sur la carte
// Ajouter les marqueurs avec des icônes en style "pin"
function updateAlerts() {
    fetch('/alerts')
        .then(response => response.json())
        .then(data => {
            // Efface les anciens marqueurs
            if (window.alertMarkers) {
                window.alertMarkers.forEach(marker => {
                    map.removeLayer(marker);
                });
            }
            window.alertMarkers = [];

            accidentCount = 0; // Réinitialiser le compteur
            jamCount = 0; // Réinitialiser le compteur d'embouteillages

            accidentAlerts = []; // Réinitialiser les listes
            jamAlerts = [];

            data.forEach((alert) => {
                const popupContent = `
                    <b>Sous-type</b>: ${getSubtypeFr(alert.subtype)}<br>
                    <b>Date et heure</b>: ${formatTimestamp(alert.pubMillis)}<br>
                    <b>Description</b>: ${getSubtypeFr(alert.reportDescription)}<br>
                    <b>Rue</b>: ${alert.street || 'Non spécifiée'}<br>
                    <b>Confiance</b>: ${alert.confidence}/10<br>
                    <b>Fiabilité</b>: ${alert.reliability}/10<br>
                    <b>Utilisateur</b>: ${alert.reportByMunicipalityUser === 'true' ? 'Municipalité' : 'Utilisateur Waze'}
                `;

                if (alert.type === 'ACCIDENT') {
                    accidentCount += 1;
                    accidentAlerts.push(alert); // Ajouter à la liste d'accidents

                    // Ajouter un marqueur d'accident avec une icône en style "pin"
                    const marker = L.marker([alert.location.y, alert.location.x], { 
                        icon: createPinIcon('/static/img/accident-icon.png') 
                    })
                    .addTo(map)
                    .bindPopup(`<b>ACCIDENT</b><br>${popupContent}`);
                    window.alertMarkers.push(marker);

                } else if (alert.type === 'ROAD_CLOSED') {
                    // Ajouter un marqueur pour route fermée avec une icône en style "pin"
                    const marker = L.marker([alert.location.y, alert.location.x], { 
                        icon: createPinIcon('/static/img/closed-road-icon.png') 
                    })
                    .addTo(map)
                    .bindPopup(`<b>ROUTE FERMÉE</b><br>${popupContent}`);
                    window.alertMarkers.push(marker);

                } else if (alert.type === 'JAM') {
                    jamCount += 1; // Incrémenter le compteur d'embouteillages
                    jamAlerts.push(alert); // Ajouter à la liste d'embouteillages

                    const pinColor = getPinColor(alert.subtype);
                
                    // Ajouter un marqueur pour l'embouteillage avec la couleur appropriée
                    const marker = L.marker([alert.location.y, alert.location.x], {
                        icon: createPinIcon('/static/img/jam-icon.png', pinColor)
                    })
                    .addTo(map)
                    .bindPopup(`<b>EMBOUTEILLAGE</b><br>${popupContent}`);
                    window.alertMarkers.push(marker);
                }
            });

            // Trier les alertes par pubMillis en ordre inverse
            accidentAlerts = sortAlertsByTime(accidentAlerts);
            jamAlerts = sortAlertsByTime(jamAlerts);

            // Connecter les points des routes fermées par rue
            connectPointsByStreet(data);

            // Mettre à jour le compteur d'accidents
            updateAccidentCounter(accidentCount);
            updateJamCounter(jamCount); // Nouvelle fonction pour les embouteillages
        })
        .catch(error => console.error('Erreur lors de la récupération des alertes:', error));
}

// Fonction pour traduire les sous-types en français
function getSubtypeFr(subtype) {
    const subtypeMap = {
        'ACCIDENT_MINOR': 'Accident Mineur',
        'ACCIDENT_MAJOR': 'Accident Majeur',
        'NO_SUBTYPE': 'Sans Sous-type',
        'HAZARD_ON_ROAD_CONSTRUCTION': 'Danger sur la Route (Construction)',
        'HAZARD_ON_ROAD_TRAFFIC_LIGHT_FAULT': 'Danger sur la Route (Défaut Feu de Circulation)',
        'HAZARD_ON_ROAD_POT_HOLE': 'Danger sur la Route (Nid-de-poule)',
        'ROAD_CLOSED_EVENT': 'Route Fermée',
        'JAM_MODERATE_TRAFFIC': 'Circulation modérée',
        'JAM_HEAVY_TRAFFIC': 'Circulation dense',
        'JAM_STAND_STILL_TRAFFIC': 'Circulation arrêtée',
        'JAM_LIGHT_TRAFFIC': 'Circulation légère'
    };
    return subtypeMap[subtype] || subtype;
}

// Fonction pour mettre à jour le compteur d'accidents
function updateAccidentCounter(count) {
    const counter = document.getElementById('accident-number');
    const counterDiv = document.getElementById('accident-counter');
    const labelDiv = document.getElementById('accident-label');
    counter.textContent = count;

    if (count > 0) {
        counterDiv.classList.add('red');
        labelDiv.classList.add('red');
    } else {
        counterDiv.classList.remove('red');
        labelDiv.classList.remove('red');
    }
}

// Fonction pour mettre à jour le compteur d'embouteillages
function updateJamCounter(count) {
    const counter = document.getElementById('jam-number');
    const counterDiv = document.getElementById('jam-counter');
    const labelDiv = document.getElementById('jam-label');
    counter.textContent = count;

    if (count > 0) {
        counterDiv.classList.add('red');
        labelDiv.classList.add('red');
    } else {
        counterDiv.classList.remove('red');
        labelDiv.classList.remove('red');
    }
}

function getPinColor(subtype) {
    switch (subtype) {
        case 'JAM_LIGHT_TRAFFIC':
            return '#FFA500'; // Jaune clair
        case 'JAM_MODERATE_TRAFFIC':
            return '#FF4500'; // Orange
        case 'JAM_HEAVY_TRAFFIC':
            return '#FF0000'; // Orange foncé
        case 'JAM_STAND_STILL_TRAFFIC':
            return '#8B0000'; // Rouge foncé
        default:
            return '#FF0000'; // Rouge par défaut
    }
}

// Mise à jour de la carte et des alertes au chargement de la page
document.addEventListener('DOMContentLoaded', function() {
    updateAlerts();

    // Mise à jour automatique toutes les 60 secondes
    setInterval(updateAlerts, 60000);
});