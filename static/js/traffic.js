// static/js/traffic.js

let selectedPolyline = null; // Variable globale pour garder la référence de la polyline sélectionnée
let selectedDecorator = null; // Ajouter une variable pour le décorateur
let trafficEnabled = false;  // Variable pour savoir si le trafic est activé

// Fonction pour formater les secondes en minutes et secondes
function formatTime(seconds) {
    if (seconds < 0) {
        return '0m 0s';
    }
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes}m ${remainingSeconds}s`;
}

// Fonction pour déterminer la couleur en fonction du niveau de congestion
function getColor(level) {
    switch (level) {
        case 1:
            return 'darkgreen';
        case 2:
            return 'yellow';
        case 3:
            return 'orange';
        case 4:
            return 'red';
        case 5:
            return 'darkred';
        default:
            return 'green';
    }
}

// Fonction pour dessiner des lignes avec des flèches et ajouter un popup
function drawTrafficLineWithDirection(route, color) {
    // Vérification si 'line' est présent et contient des coordonnées valides
    if (!route.line || route.line.length === 0) {
        console.error(`Aucune coordonnée trouvée pour la route: ${route.name}`);
        return;
    }

    const latlngs = route.line.map(point => [point.y, point.x]);

    // Ajoute la polyline avec direction
    const polyline = L.polyline(latlngs, {
        color: color,
        weight: 4,
        trafficLayer: true
    }).addTo(map);

    // Ajoute un popup à la polyline
    polyline.bindPopup(`<b>Nom: ${route.name}</b><br>Retard: ${formatTime(calculateDelay(route.time, route.historicTime))}<br>Temps de trajet actuel : ${formatTime(route.time)}<br>Temps de trajet moyen : ${formatTime(route.historicTime)}`);

    // Ajoute un décorateur avec flèche
    const decorator = L.polylineDecorator(polyline, {
        patterns: [
            {
                offset: 10, // Décalage de départ de la flèche
                repeat: 100, // Répétition
                symbol: L.Symbol.arrowHead({
                    pixelSize: 6,
                    polygon: false,
                    pathOptions: { stroke: true, color: color }
                })
            }
        ]
    }).addTo(map);

    window.alertMarkers.push(polyline);
    window.alertMarkers.push(decorator);
}

// Fonction pour calculer la distance entre deux points
function getDistance(pointA, pointB) {
    const dx = pointA.x - pointB.x;
    const dy = pointA.y - pointB.y;
    return Math.sqrt(dx * dx + dy * dy);
}

// Fonction pour comparer deux routes et déterminer si elles sont proches
function areRoutesClose(routeA, routeB) {
    for (let i = 0; i < routeA.length; i++) {
        for (let j = 0; j < routeB.length; j++) {
            const distance = getDistance(routeA[i], routeB[j]);
            if (distance < 0.0001) { // Ajuster la tolérance selon la carte
                return true;
            }
        }
    }
    return false;
}

function applyOffsetToLine(latlngs, offsetX, offsetY) {
    return latlngs.map(latlng => [latlng[0] + offsetY, latlng[1] + offsetX]);
}

// Fonction pour calculer le retard
function calculateDelay(time, historicTime) {
    return time - historicTime;
}

// Écouteur d'événement pour le bouton toggle
document.getElementById('trafficToggle').addEventListener('change', function () {
    trafficEnabled = this.checked;

    var container = document.getElementById('traffic-container');
    var toggleButton = document.getElementById('toggle-traffic-table');

    if (trafficEnabled) {
        // Afficher les lignes de trafic et déplier la table
        updateTrafficMap();
    } else {
        // Effacer les lignes de trafic et replier la table
        if (window.alertMarkers) {
            window.alertMarkers.forEach(marker => {
                map.removeLayer(marker);
            });
        }
    }
});

// Fonction pour mettre à jour la carte avec les données de trafic
function updateTrafficMap() {
    if (!trafficEnabled) return;  // Si le trafic est désactivé, ne rien faire
    fetch('/trafic/data')
        .then(response => response.json())
        .then(data => {

            // Vérifier si 'routes' existe
            if (!data.routes) {
                console.error('La clé "routes" est absente des données reçues.');
                return;
            }

            // Efface les couches de trafic précédentes
            map.eachLayer(function (layer) {
                if (layer.options && layer.options.trafficLayer) {
                    map.removeLayer(layer);
                }
            });

            // Trier les routes par retard décroissant
            const sortedRoutes = data.routes.slice().sort((a, b) => {
                const delayA = calculateDelay(a.time, a.historicTime);
                const delayB = calculateDelay(b.time, b.historicTime);
                return delayB - delayA;
            });

            // Parcourt chaque route et l'affiche sur la carte et dans le tableau
            sortedRoutes.forEach((route, index) => {

                // Vérifier les champs nécessaires
                if (!route.name || route.time === undefined || route.historicTime === undefined || !route.line) {
                    console.warn(`Route ${index + 1} manque de certains champs nécessaires.`);
                    return; // Passer à la route suivante
                }

                // Ajoute une polyline pour la route sur la carte
                let latlngs = route.line.map(point => [point.y, point.x]);

                // Comparer avec les routes précédentes
                const closeRoute = sortedRoutes.some((otherRoute, otherIndex) => 
                    otherIndex < index && areRoutesClose(route.line, otherRoute.line)
                );

                // Détermine la couleur en fonction du niveau de congestion
                let color = getColor(route.jamLevel);

                // Appliquer un décalage si la route est proche d'une autre
                if (closeRoute) {
                    latlngs = applyOffsetToLine(latlngs, 0.0005, 0.0005);
                }

                 // Ajoute une ligne de trafic avec une direction (flèche) sur la carte
                drawTrafficLineWithDirection(route, color); // Utilise la fonction avec direction
                // let polyline = L.polyline(latlngs, { color: color, trafficLayer: true }).addTo(map)
                //                    .bindPopup(`<b>Nom: ${route.name}</b><br>Retard: ${formatTime(calculateDelay(route.time, route.historicTime))}<br>Temps de trajet actuel : ${formatTime(route.time)}<br>Temps de trajet moyen : ${formatTime(route.historicTime)}`);
            });
        })
        .catch(error => console.error('Erreur lors de la récupération des données de trafic:', error));
}

// Fonction pour ajouter des événements de clic aux lignes du tableau
function addRowClickEvents(routes) {
    const trafficInfoBody = document.querySelector('#traffic-info-body');
    if (!trafficInfoBody) return;

    trafficInfoBody.querySelectorAll('tr').forEach((row, index) => {
        row.addEventListener('click', () => {
            const route = routes[index];
            if (!route || !route.line) return;

            // Supprimer la polyline et le décorateur précédemment sélectionnés
            if (selectedPolyline) {
                map.removeLayer(selectedPolyline);
            }
            if (selectedDecorator) {
                map.removeLayer(selectedDecorator);
            }

            // Zoom sur la route
            const latlngs = route.line.map(point => [point.y, point.x]);
            selectedPolyline = L.polyline(latlngs, { color: 'blue', weight: 5 }).addTo(map);
            map.fitBounds(selectedPolyline.getBounds());

            // Ajouter un décorateur avec flèches à la polyline
            selectedDecorator = L.polylineDecorator(selectedPolyline, {
                patterns: [
                    {
                        offset: 5, // Décalage de départ de la flèche
                        repeat: 50, // Répétition
                        symbol: L.Symbol.arrowHead({
                            pixelSize: 18,
                            polygon: true,
                            pathOptions: { stroke: true, color: 'blue' }
                        })
                    }
                ]
            }).addTo(map);

            // Ouvrir le popup de la polyline
            selectedPolyline.bindPopup(`<b>Nom: ${route.name}</b><br>Retard: ${formatTime(calculateDelay(route.time, route.historicTime))}<br>Temps de trajet actuel : ${formatTime(route.time)}<br>Temps de trajet moyen : ${formatTime(route.historicTime)}`).openPopup();
        });
    });
}

// Appel initial pour charger les données de trafic au chargement de la page
document.addEventListener('DOMContentLoaded', function() {
    updateTrafficMap();
    // Mise à jour automatique toutes les 60 secondes
    setInterval(updateTrafficMap, 60000);
});


/////////////////////////////////////////////////////////////////////////////////////////////////////
// TEMPS D'ATTENTE PARKINGS/CAMPINGS
/////////////////////////////////////////////////////////////////////////////////////////////////////

function updateParkingIndicators() {
    fetch('/trafic/waiting_data_structured')
        .then(response => response.json())
        .then(data => {
            const container = document.getElementById('parking-indicators');
            // Vider le conteneur pour éviter les doublons
            container.innerHTML = "";

            data.terrains.forEach(item => {
                const div = document.createElement('div');
                div.classList.add('parking-indicator');
                const waiting_time = Math.max(0, item.currentTime - item.historicTime);

                // Appliquer une couleur en fonction du niveau de sévérité avec 80% d'opacité
                if (item.severity === 1) {
                    div.style.backgroundColor = 'rgba(0, 128, 0, 0.8)'; // Vert
                } else if (item.severity === 2) {
                    div.style.backgroundColor = 'rgba(255, 165, 0, 0.8)'; // Orange
                } else if (item.severity === 3) {
                    div.style.backgroundColor = 'rgba(255, 0, 0, 0.8)'; // Rouge
                }

                div.innerHTML = `<span class="terrain-name">${item.terrain}</span><br>
                                 Attente actuelle : ${waiting_time} s<br>`;
                container.appendChild(div);
            });
        })
        .catch(error => console.error('Erreur lors de la récupération des données de trafic :', error));
}

// Appel initial et mise à jour toutes les 30 secondes
updateParkingIndicators();
setInterval(updateParkingIndicators, 30000);
