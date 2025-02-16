let categories = [];     // Liste des catégories
let datasets = {};       // datasets[categoryId] = data
let categorySuggestions = {};
let awesomplete; 
let marker;
let menuOpen = false;
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

// Icônes pré-définies
// Définition d'une classe d'icônes personnalisées
var ColorIcon = L.Icon.extend({
    options: {
        shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
        iconSize: [25, 41],
        iconAnchor: [12, 41],
        popupAnchor: [1, -34],
        shadowSize: [41, 41]
    }
});

// Création d'instances d'icônes pour différentes couleurs
var blueIcon = new ColorIcon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-blue.png' });
var redIcon = new ColorIcon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png' });
var greenIcon = new ColorIcon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-green.png' });
var orangeIcon = new ColorIcon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-orange.png' });
var yellowIcon = new ColorIcon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-yellow.png' });
var violetIcon = new ColorIcon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-violet.png' });
var greyIcon = new ColorIcon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-grey.png' });
var blackIcon = new ColorIcon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-black.png' });
var whiteIcon = new ColorIcon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-white.png' });
var goldIcon = new ColorIcon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-gold.png' });


var porteIcon = L.divIcon({
    html:'<div style="background-color: rgb(9,7,38);color:white;border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;font-weight:bold;">P</div>',
    iconSize:[36,36], className:'custom-porte-icon'
});

var siffletIcon = L.divIcon({
    html:'<div style="background-color: rgb(255, 132, 0);color:white;border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;font-weight:bold;">S</div>',
    iconSize:[36,36], className:'custom-porte-icon'
});

var mpIcon = L.divIcon({
    html:'<div style="background-color: rgb(255, 132, 0);color:white;border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;font-weight:bold;">MP</div>',
    iconSize:[36,36], className:'custom-porte-icon'
});

// Objets de correspondance des icônes
const iconMapping = {
    blueIcon: blueIcon,
    redIcon: redIcon,
    orangeIcon: orangeIcon,
    porteIcon: porteIcon,
    siffletIcon: siffletIcon,
    mpIcon: mpIcon,
    greenIcon: greenIcon,
    yellowIcon: yellowIcon,
    violetIcon: violetIcon,
    greyIcon: greyIcon,
    blackIcon: blackIcon,
    goldIcon: goldIcon,
};

//sidebar
document.addEventListener("DOMContentLoaded", function () {
    const sidebar = document.getElementById("sidebar");
    const hamburgerButton = document.getElementById("hamburger-button");
    const body = document.body;
    
    hamburgerButton.addEventListener("click", function () {
        if (sidebar.classList.contains("active")) {
            sidebar.classList.remove("active");
            body.classList.remove("body-with-sidebar");
            body.classList.add("body-without-sidebar");
        } else {
            sidebar.classList.add("active");
            body.classList.add("body-with-sidebar");
            body.classList.remove("body-without-sidebar");
        }
    });
});

// Utilitaires
function getPropertyValue(obj, fieldPath) {
    const fields = fieldPath.split('.');
    let current = obj;

    for (let i = 0; i < fields.length; i++) {
        if (!current) return null;
        current = current[fields[i]];
    }

    return current;
}

function extractSuggestions(cat) {
    const data = datasets[cat.id];
    if (!data) return [];

    const searchFields = cat.searchField.split(',').map(f => f.trim());
    const suggestionsSet = new Set();

    if (cat.dataType === 'geojson') {
        data.forEach(feature => {
            searchFields.forEach(fieldPath => {
                const val = getPropertyValue(feature, fieldPath);
                if (val) suggestionsSet.add(val); // Ajoute directement au Set
            });
        });
    } else if (cat.dataType === 'json') {
        data.forEach(item => {
            searchFields.forEach(fieldPath => {
                const val = getPropertyValue(item, fieldPath);
                if (val) suggestionsSet.add(val); // Ajoute directement au Set
            });
        });
    }

    return Array.from(suggestionsSet); // Convertit le Set en tableau à la fin
}

// Chargement des catégories
function loadCategoriesAndData() {
    fetch('/categories')
        .then(r => r.json())
        .then(config => {
            categories = config.categories || [];

            // Afficher les catégories immédiatement dans le <select>
            populateSearchTypeSelect();

            // Charger les données des catégories en parallèle
            const dataPromises = categories.map(cat =>
                fetch(cat.dataEndpoint)
                    .then(r => r.json())
                    .then(data => {
                        datasets[cat.id] = data;
                    })
                    .catch(err => console.error(`Erreur chargement ${cat.id}:`, err))
            );

            // Une fois toutes les données chargées, extraire les suggestions
            Promise.all(dataPromises).then(() => {
                console.time('Extraction des suggestions');
                categories.forEach(cat => {
                    categorySuggestions[cat.id] = extractSuggestions(cat);
                });
                console.timeEnd('Extraction des suggestions');

                // Mettre à jour la liste d'Awesomplete après extraction
                updateAwesompleteList();
            });
        })
        .catch(error => console.error('Erreur /categories:', error));
}

function populateSearchTypeSelect() {
    const searchTypeSelect = document.getElementById('search-type');
    const existingOptions = Array.from(searchTypeSelect.options).map(opt => opt.value);

    const categoryIds = ['all', ...categories.map(cat => cat.id)];
    if (existingOptions.join(',') === categoryIds.join(',')) return; // Rien à faire

    searchTypeSelect.innerHTML = '';
    let optionAll = document.createElement('option');
    optionAll.value = 'all';
    optionAll.textContent = 'Tous';
    searchTypeSelect.appendChild(optionAll);

    categories.forEach(cat => {
        let opt = document.createElement('option');
        opt.value = cat.id;
        opt.textContent = cat.label;
        searchTypeSelect.appendChild(opt);
    });
    searchTypeSelect.value = 'all';
}

function updateAwesompleteList() {
    const searchTypeValue=document.getElementById('search-type').value;
    const input=document.getElementById('grid-input');
    if(searchTypeValue==='all'){
        let allSuggestions=[];
        categories.forEach(cat=>{
            allSuggestions=allSuggestions.concat(categorySuggestions[cat.id]||[]);
        });
        awesomplete.list=Array.from(new Set(allSuggestions));
    }else{
        awesomplete.list=categorySuggestions[searchTypeValue]||[];
    }
    input.value='';
}

document.getElementById('search-type').addEventListener('change',updateAwesompleteList);

function searchAllCategories(term){
    let foundSomething=false;
    categories.forEach(cat=>{
        foundSomething=searchInCategory(cat,term)||foundSomething;
    });
    if (!foundSomething) {
        showDynamicFlashMessage(`Aucun résultat trouvé pour "${term}" dans toutes les catégories.`, "error");
    }
}

function searchInCategory(cat, term) {
    const data = datasets[cat.id];
    console.log(data);
    if (!data) return false;

    let matchedItems = [];

    // Rechercher les correspondances
    if (cat.dataType === 'geojson') {
        data.forEach(feature => {
            const val = getPropertyValue(feature, cat.searchField);
            if (val && val.toUpperCase() === term) {
                matchedItems.push(feature);
            }
        });
    } else if (cat.dataType === 'json') {
        data.forEach(item => {
            const val = getPropertyValue(item, cat.searchField);
            if (val && val.toUpperCase() === term) {
                matchedItems.push(item);
            }
        });
    }

    if (matchedItems.length > 0) {
        if (matchedItems.length === 1) {
            displayFeatureOnMap(matchedItems[0], cat);
        } else {
            displayMultipleFeaturesOnMap(matchedItems, cat);
        }
        return true;
    } else {
        return false;
    }
}

function getIconForCategory(cat, item = null) {
    if (cat.iconType === "div") {
        const textField = cat.iconTextField;
        const textValue = item ? getPropertyValue(item, textField) || "" : "";
        const shortText = textValue.substring(0, cat.iconTextMaxLength || 6);
        const bgColor = cat.iconBgColor || "#00ff00";
        const textColor = cat.iconTextColor || "#ffffff";

        return L.divIcon({
            html: `<div style="
                background-color: ${bgColor};
                color: ${textColor};
                border-radius: 50%;
                width: 36px;
                height: 36px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: bold;
                font-size: 14px;">
                ${shortText}
            </div>`,
            className: "",
            iconSize: [36, 36],
            iconAnchor: [18, 18],
            popupAnchor: [0, -18]
        });
    }

    // Utilisation de la correspondance dynamique pour les icônes
    return iconMapping[cat.icon] || (() => {
        console.warn("Icon not found for:", cat.icon);
        return blueIcon; // Par défaut
    })();
}

function buildPopupContent(item, cat, collection, documentId) {
    let content = '<div class="popup-content">';
    let fields = [...cat.popupFields];

    // Gestion des champs prioritaires
    const nameField = fields.find(field =>
        field.toLowerCase().includes('name') || field.toLowerCase().includes('nom')
    );
    const descriptionField = fields.find(field => field.toLowerCase() === 'description');
    const photoField = fields.find(field => field.toLowerCase().includes('photo'));
    let hyperlinkField = null;
    let photoExists = false; // Indicateur de la présence d'une photo

    // Vérifier s'il existe un champ contenant un fichier
    const fileField = fields.find(field => field.toLowerCase().includes('file'));
    const fileVal = fileField ? getPropertyValue(item, fileField) : null;

    // Détecter un champ contenant un lien hypertexte
    fields.forEach(field => {
        const val = getPropertyValue(item, field);
        if (val && typeof val === 'string' && val.match(/https?:\/\/|www\.|:\/\//)) {
            hyperlinkField = field;
        }
    });

    // Supprimer les champs prioritaires et le champ hypertexte de la liste
    fields = fields.filter(field => field !== nameField && field !== descriptionField && field !== hyperlinkField && field !== fileField);

    // Fonction pour traduire les noms des champs
    const translateField = fieldName => {
        if (fieldName.toLowerCase() === 'name') return 'Nom';
        return fieldName.charAt(0).toUpperCase() + fieldName.slice(1);
    };

    // Affichage du nom et description
    if (nameField) {
        const val = getPropertyValue(item, nameField);
        if (val) {
            const fieldName = translateField(nameField.split('.').pop());
            content += `<strong>${fieldName}</strong> : ${val}<br>`;
        }
    }
    if (descriptionField) {
        const val = getPropertyValue(item, descriptionField);
        if (val) {
            const fieldName = translateField(descriptionField.split('.').pop());
            content += `<strong>${fieldName}</strong> : ${val}<br>`;
        }
    }

    // Ajout des autres champs (sauf photo)
    fields.sort((a, b) => a.localeCompare(b));
    fields.forEach(field => {
        if (field !== photoField) {
            const val = getPropertyValue(item, field);
            if (val) {
                const fieldName = translateField(field.split('.').pop());
                content += `<strong>${fieldName}</strong> : ${val}<br>`;
            }
        }
    });

    // Récupération des coordonnées GPS ou du centroïde
    let coordinates = detectOrTransformToPoint(item.geometry);
    let lat = coordinates ? coordinates[0] : null;
    let lng = coordinates ? coordinates[1] : null;

    // Affichage de la miniature de la photo si disponible
    if (photoField) {
        const photoVal = getPropertyValue(item, photoField);
        if (photoVal) {
            const thumbnailPath = `static/img/media/thumbnails/${photoVal}`;
            const originalPath = `static/img/media/original/${photoVal}`;

            content += `<div class="popup-photo">
                <img src="${thumbnailPath}" alt="Miniature" 
                    style="cursor: pointer;" 
                    onclick="openPhotoSwipe('${originalPath}')">
            </div>`;

            // ✅ Une photo est affichée, donc on ne doit pas ajouter le bouton "Photo HD"
            photoExists = true;
        }
    }

    // Ajout des boutons sous forme d'icônes
    let buttons = '<div class="popup-buttons">';

    // Vue aérienne
    if (lat && lng) {
        buttons += `<button onclick="openAerialView(${lat}, ${lng})" data-tooltip="Vue aérienne">
                        <i class="material-icons">satellite</i>
                    </button>`;
    }

    // Détails (hyperlien)
    if (hyperlinkField) {
        const link = getPropertyValue(item, hyperlinkField);
        if (link) {
            buttons += `<a href="${link}" target="_blank">
                            <button data-tooltip="Détails"><i class="material-icons">open_in_new</i></button>
                        </a>`;
        }
    }

    // Bouton pour ajouter une photo si aucune n'est affichée
    if (!photoExists) {
        buttons += `<button onclick="openUploadModal('photo', '${collection}', '${documentId}')" 
                    data-tooltip="Ajouter une photo">
                        <i class="material-icons">add_a_photo</i>
                    </button>`;
    }

    // Si un fichier existe, afficher un bouton pour le consulter
    if (fileVal) {
        buttons += `<a href="static/img/media/original/${fileVal}" target="_blank">
                        <button data-tooltip="Consulter le fichier">
                            <i class="material-icons">insert_drive_file</i>
                        </button>
                    </a>`;
    } else {
        // Sinon, afficher un bouton pour ajouter un fichier
        buttons += `<button onclick="openUploadModal('file', '${collection}', '${documentId}')" 
                    data-tooltip="Ajouter un fichier">
                        <i class="material-icons">upload</i>
                    </button>`;
    }

    // Bouton Waze
    if (lat && lng) {
        let wazeLink = `https://www.waze.com/ul?ll=${lat},${lng}&navigate=yes`;
        buttons += `<a href="${wazeLink}" target="_blank">
                        <button data-tooltip="Waze"><i class="material-icons">navigation</i></button>
                    </a>`;
    }

    buttons += '</div>'; // Fin des boutons

    content += buttons + '</div>'; // Ajout des boutons au contenu
    return content;
}

// Conversion GeoJSON vers latlng
function convertGeometryToLatLngs(geometry) {
    // Retourne un tableau de latlngs pour Polygon, ou un tableau de tableaux pour MultiPolygon
    if (geometry.type==='Polygon') {
        // Polygon: coordinates: [ [ [lng,lat], [lng,lat] ...] ]
        let coords=geometry.coordinates[0]; 
        return coords.map(c=>[c[1],c[0]]);
    } else if(geometry.type==='MultiPolygon') {
        // MultiPolygon: [ [ [ [lng,lat], ... ] ] ]
        // Renvoie un tableau de polygones
        return geometry.coordinates.map(polygonCoords=>{
            return polygonCoords[0].map(c=>[c[1],c[0]]);
        });
    }
    return [];
}

function detectOrTransformToPoint(geometry) {
    if (!geometry) return null;

    // Si c'est un point natif
    if (geometry.type === 'Point') {
        const coords = geometry.coordinates;
        return [coords[1], coords[0]];
    }

    // Si c'est un polygone ou un multipolygone, calcule le centroïde
    if (geometry.type === 'Polygon' || geometry.type === 'MultiPolygon') {
        if (window.turf && typeof turf.centroid === 'function') {
            const centroid = turf.centroid(geometry).geometry.coordinates;
            return [centroid[1], centroid[0]];
        } else {
            // Méthode manuelle si Turf.js n'est pas disponible
            return getPolygonCentroid(geometry);
        }
    }

    return null; // Aucun point détectable
}

function getPolygonCentroid(geometry) {
    let coordinates = geometry.type === 'Polygon'
        ? geometry.coordinates[0]
        : geometry.coordinates[0][0]; // Prend le premier polygone pour MultiPolygon

    let xSum = 0, ySum = 0, n = coordinates.length;

    coordinates.forEach(coord => {
        xSum += coord[0];
        ySum += coord[1];
    });

    return [xSum / n, ySum / n]; // Centroïde simple
}

function displayFeatureOnMap(item, cat) {
    // Déterminer la collection
    const collection = cat.id || item.collection || null;

    // Vérifier d'abord si l'ID est dans `properties._id_feature`
    let documentId = item.properties && item.properties._id_feature 
        ? item.properties._id_feature 
        : null;

    // Si `_id_feature` est null, essayer `_id` ou `id` directement sur `item`
    if (!documentId) {
        documentId = item._id ? item._id : (item.id ? item.id : null);
    }

    // Nettoyer les éléments multiples affichés précédemment
    if (categoryLayerGroup) {
        categoryLayerGroup.clearLayers();
    }

    // Nettoyer le marqueur unique existant
    if (marker) {
        map.removeLayer(marker);
        marker = null;
    }

    if (cat.dataType === 'geojson') {
        const geometry = item.geometry;

        if (cat.geometryType === 'point') {
            // Gestion des points
            if (geometry.type === 'Point') {
                // Détection automatique : coordonnées directes ou transformation nécessaire
                const latlng = detectOrTransformToPoint(geometry);
                if (latlng) {
                    let icon = getIconForCategory(cat, item);
                    marker = L.marker(latlng, { icon: icon }).addTo(map);
                    let popupContent = buildPopupContent(item, cat, collection, documentId);
                    marker.bindPopup(popupContent).openPopup();
                    map.setView(latlng, 13); // Zoom par défaut pour les points
                    return; // Fin de traitement
                }

            } else if (geometry.type === 'MultiPoint') {
                // Gestion des multipoints
                const fg = L.featureGroup();
                geometry.coordinates.forEach(coord => {
                    const latlng = [coord[1], coord[0]];
                    console.log(latlng);
                    let icon = getIconForCategory(cat, item);
                    let m = L.marker(latlng, { icon: icon }).addTo(fg);
                    let popupContent = buildPopupContent(item, cat, collection, documentId);
                    m.bindPopup(popupContent);
                });

                fg.addTo(map);
                marker = fg; // Stocker le groupe
                // Vérifier si un seul point et ajuster l'affichage
                if (fg.getLayers().length === 1) {
                    const singlePoint = fg.getLayers()[0].getLatLng();
                    map.setView(singlePoint, 16); // Zoom sur le point unique
                } else {
                    map.fitBounds(fg.getBounds()); // Ajustement à l'ensemble des points
                }
            }

        } else if (cat.geometryType === 'polygon') {
            // Gestion des polygones et multipolygones
            if (geometry.type === 'Polygon') {
                let latlngs = convertGeometryToLatLngs(geometry);
                marker = L.polygon(latlngs, cat.style || { color: 'blue' }).addTo(map);
                let popupContent = buildPopupContent(item, cat, collection, documentId);

                // Calcul de la surface pour les polygones
                if (window.turf && typeof turf.area === 'function') {
                    const geojson = marker.toGeoJSON();
                    const area = turf.area(geojson) / 10000; // Conversion en hectares
                    const vehicleCapacity = Math.floor(area * 300); // 300 véhicules par hectare
                    const campingCapacity = Math.floor(area * 150); // 150 emplacements camping par hectare

                    popupContent += `<br>Surfaces : ${area.toFixed(2)} ha`;
                    popupContent += `<br>Capacité véhicules : ${vehicleCapacity}`;
                    popupContent += `<br>Capacité camping : ${campingCapacity}`;

                    if (area < 0.5) {
                        // Petit polygone : Zoom manuel
                        const bounds = marker.getBounds();
                        const center = bounds.getCenter();
                        map.setView(center, 13); // Zoom fixe
                    } else {
                        // Grand polygone : FitBounds
                        map.fitBounds(marker.getBounds());
                    }
                } else {
                    map.fitBounds(marker.getBounds());
                }

                marker.bindPopup(popupContent).openPopup();

            } else if (geometry.type === 'MultiPolygon') {
                let multiLatLngs = geometry.coordinates.map(p => p[0].map(c => [c[1], c[0]]));
                marker = L.polygon(multiLatLngs, cat.style || { color: 'blue' }).addTo(map);
                let popupContent = buildPopupContent(item, cat, collection, documentId);

                // Calcul de la surface pour les multipolygones
                if (window.turf && typeof turf.area === 'function') {
                    const geojson = marker.toGeoJSON();
                    const area = turf.area(geojson) / 10000; // Conversion en hectares
                    const vehicleCapacity = Math.floor(area * 300); // 300 véhicules par hectare
                    const campingCapacity = Math.floor(area * 150); // 150 emplacements camping par hectare

                    popupContent += `<br>Surfaces : ${area.toFixed(2)} ha`;
                    popupContent += `<br>Capacité véhicules : ${vehicleCapacity}`;
                    popupContent += `<br>Capacité camping : ${campingCapacity}`;

                    if (area < 0.5) {
                        // Petit multipolygone : Zoom manuel
                        const bounds = marker.getBounds();
                        const center = bounds.getCenter();
                        map.setView(center, 13); // Zoom fixe
                    } else {
                        // Grand multipolygone : FitBounds
                        map.fitBounds(marker.getBounds());
                    }
                } else {
                    map.fitBounds(marker.getBounds());
                }

                marker.bindPopup(popupContent).openPopup();
            }
        }

    } else if (cat.dataType === 'json') {
        // Gestion des données simples (point)
        let lat = parseFloat(item.latitude);
        let lng = parseFloat(item.longitude);
        let icon = getIconForCategory(cat, item);
        marker = L.marker([lat, lng], { icon: icon }).addTo(map);
        let popupContent = buildPopupContent(item, cat, collection, documentId);
        marker.bindPopup(popupContent).openPopup();
        map.setView([lat, lng], 13); // Zoom par défaut pour les données simples
    }
}

function displayMultipleFeaturesOnMap(items, cat) {
    // Nettoyer le marqueur individuel affiché précédemment
    if (marker) {
        map.removeLayer(marker);
        marker = null;
    }

    // Nettoyer le contenu existant du layerGroup
    if (categoryLayerGroup) {
        categoryLayerGroup.clearLayers();
    }

    // Créer un FeatureGroup pour y mettre les polygones/markers
    const group = L.featureGroup();

    // Stocker la référence au premier élément ajouté et son contenu de popup
    let firstLayerOpened = { layer: null, popupContent: '' };

    items.forEach(item => {
        displayItemOnLayerGroup(item, cat, group, firstLayerOpened);
    });

    // Ajouter le groupe à la carte
    categoryLayerGroup.addLayer(group);

    // Zoomer sur l'ensemble
    if (group.getBounds && group.getBounds().isValid()) {
        map.fitBounds(group.getBounds(), {
            padding: [50, 50], // Ajoute un espace autour
            maxZoom: 17,      // Empêche le zoom excessif
            minZoom: 12,      // Empêche le zoom trop distant
        });
    }

    // Ouvrir le popup du premier élément
    if (firstLayerOpened.layer) {
        setTimeout(() => {
            firstLayerOpened.layer.bindPopup(firstLayerOpened.popupContent).openPopup();
        }, 100); // Délai pour éviter les interférences
    }
}

function searchGrid() {
    const inputValue=document.getElementById('grid-input').value.trim().toUpperCase();
    const searchTypeValue=document.getElementById('search-type').value;
    if (!inputValue) { 
        showDynamicFlashMessage("Veuillez entrer une valeur à rechercher", "error"); 
        return;
    }
    if(searchTypeValue==='all'){
        searchAllCategories(inputValue);
    }else{
        const cat=categories.find(c=>c.id===searchTypeValue);
        if(cat){
            const found=searchInCategory(cat,inputValue);
            if (!found) {
                showDynamicFlashMessage(`Aucun résultat pour "${inputValue}" dans ${cat.label}.`, "warning");
            }
        }
    }
}

document.getElementById('search-button').addEventListener('click',searchGrid);
const inputElement = document.getElementById('grid-input');

inputElement.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
        e.preventDefault(); // Empêche le comportement par défaut de soumission du formulaire

        // Vérifie si une suggestion d'AwesomeComplete est active
        const activeSuggestion = document.querySelector('.awesomplete li[aria-selected="true"]');
        if (activeSuggestion) {
            // Une suggestion est active, AwesomeComplete va gérer, pas besoin d'autre action
            return;
        }

        // Aucune suggestion active, lance la recherche directement avec la valeur actuelle
        if (inputElement.value.trim() !== '') {
            searchGrid();
        }
    }
});

// Recherche déclenchée par AwesomeComplete lorsqu'une suggestion est sélectionnée
inputElement.addEventListener('awesomplete-selectcomplete', function() {
    searchGrid();
});

document.addEventListener('DOMContentLoaded', function() {
    awesomplete = new Awesomplete(document.getElementById('grid-input'), {
        list: [], minChars: 2, maxItems: 50, autoFirst: true
    });

    fetchMeteoPrevisions(); // 🌤 Charger la météo sans attendre
    setTimeout(fetchSunTimes, 50); // 🌞 Ajouter le soleil après un court délai
});

window.onload=function(){
    loadCategoriesAndData();
    awesomplete.list=[];
};

// Initialisation carte
var map=L.map('map',{
    center:[47.938561591531936,0.2243184111156285],
    zoom:14,
    minZoom:10,
    maxZoom:22,
    touchZoom:true,
    scrollWheelZoom:true,
    doubleClickZoom:false,
    zoomControl:true,
    boxZoom:true,
    dragPan:true,
    tap:false
});

// Couches disponibles
const osmLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxNativeZoom: 19,
    maxZoom: 22,
});
const arcgisSatelliteLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19,
});
const acoSatelliteLayer = L.tileLayer('/tiles/{z}/{x}/{y}.png', {
    maxZoom: 22,
    tms: true,
});

// Ajouter OSM par défaut
osmLayer.addTo(map);

// Bouton personnalisé avec menu radio
const satelliteButton = L.control({ position: 'topright' });
satelliteButton.onAdd = function (map) {
    const div = L.DomUtil.create('div', 'custom-button');
    div.innerHTML = '<img src="' + satelliteIconUrl + '" alt="Satellite View" style="width: 22px; height: 20px; cursor: pointer;">';

    // Styles du bouton
    div.style.backgroundColor = 'white';
    div.style.padding = '5.5px';
    div.style.borderRadius = '5px';
    div.style.boxShadow = '0 0 5px rgba(0,0,0,0.5)';
    div.style.position = 'relative'; // Important pour le positionnement du menu
    div.style.display = 'inline-block';
    L.DomEvent.disableClickPropagation(div);

    // Créer le menu déroulant
    const dropdownMenu = document.createElement('div');
    dropdownMenu.style.position = 'absolute';
    dropdownMenu.style.top = '0px'; // Juste en dessous du bouton
    dropdownMenu.style.right = '40px';
    dropdownMenu.style.backgroundColor = 'white';
    dropdownMenu.style.border = '1px solid #ccc';
    dropdownMenu.style.borderRadius = '5px';
    dropdownMenu.style.boxShadow = '0 2px 5px rgba(0,0,0,0.3)';
    dropdownMenu.style.padding = '10px';
    dropdownMenu.style.zIndex = '1000';
    dropdownMenu.style.display = 'none'; // Cacher par défaut
    dropdownMenu.style.width = '150px';

    // Ajouter les options radio
    const layers = [
        { name: 'Carte standard OSM', layer: osmLayer },
        { name: 'Satellite EGIS', layer: arcgisSatelliteLayer },
        { name: 'Satellite ACO', layer: acoSatelliteLayer },
    ];

    let currentLayer = osmLayer;

    layers.forEach(({ name, layer }) => {
        const label = document.createElement('label');
        label.style.display = 'block';
        label.style.cursor = 'pointer';

        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'mapLayer';
        radio.style.marginRight = '10px';

        if (currentLayer === layer) {
            radio.checked = true;
        }

        // Gestionnaire d'événement pour changer la couche
        radio.addEventListener('change', () => {
            if (currentLayer !== layer) {
                map.removeLayer(currentLayer);
                map.addLayer(layer);
                currentLayer = layer;
            }
        });

        label.appendChild(radio);
        label.appendChild(document.createTextNode(name));
        dropdownMenu.appendChild(label);
    });

    // Ajouter le menu au bouton
    div.appendChild(dropdownMenu);

    // Gestion du clic sur le bouton
    div.onclick = function () {
        dropdownMenu.style.display = dropdownMenu.style.display === 'none' ? 'block' : 'none';
    };

    // Ajout d'un effet de survol
    div.onmouseover = function() {
        div.style.backgroundColor = '#ff007f'; // Fond rose au survol
    };
    div.onmouseout = function() {
        div.style.backgroundColor = 'white'; // Revenir au fond blanc en quittant le survol
    };  

    // Cacher le menu si on clique en dehors
    document.addEventListener('click', (e) => {
        if (!div.contains(e.target)) {
            dropdownMenu.style.display = 'none';
        }
    });

    return div;
};

// Ajouter le bouton à la carte
satelliteButton.addTo(map);

L.control.scale({position:'bottomleft',metric:true,imperial:false}).addTo(map);

function toggleFullScreen(){
    if(!document.fullscreenElement){
        document.documentElement.requestFullscreen().catch(err=>{
            showDynamicFlashMessage(`Erreur plein écran : ${err.message}`, "error");
        });
    }else{
        document.exitFullscreen();
    }
}

L.Control.FullscreenButton=L.Control.extend({
    onAdd:function(map){
        var button=L.DomUtil.create('button','leaflet-control-fullscreen-btn');
        button.innerHTML='<span class="material-icons">fullscreen</span>';
        button.title='Plein écran';
        L.DomEvent.on(button,'click',toggleFullScreen);
        L.DomEvent.disableClickPropagation(button);
        return button;
    }
});
L.control.fullscreenButton=function(opts){return new L.Control.FullscreenButton(opts);};
L.control.fullscreenButton({position:'topright'}).addTo(map);

function calculateDistance(lat1,lon1,lat2,lon2){
    var R=6371000;
    var phi1=lat1*Math.PI/180;
    var phi2=lat2*Math.PI/180;
    var deltaPhi=(lat2-lat1)*Math.PI/180;
    var deltaLambda=(lon2-lon1)*Math.PI/180;
    var a=Math.sin(deltaPhi/2)*Math.sin(deltaPhi/2)+Math.cos(phi1)*Math.cos(phi2)*Math.sin(deltaLambda/2)*Math.sin(deltaLambda/2);
    var c=2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
    return R*c;
}

function findNearestGrid(lat,lng){
    const carroyageData=datasets['carroyage']||[];
    let nearest=null;let minDistance=Infinity;
    carroyageData.forEach(function(grid){
        let distance=calculateDistance(lat,lng,grid.latitude,grid.longitude);
        if(distance<minDistance){nearest=grid;minDistance=distance;}
    });
    return nearest;
}

map.on('click', function (e) {
    if (menuOpen) return;

    const lat = e.latlng.lat;
    const lng = e.latlng.lng;

    // Trouver la grille la plus proche
    var nearestGrid = findNearestGrid(lat, lng);

    // Supprimer le marqueur existant (si nécessaire)
    if (marker) {
        map.removeLayer(marker);
    }

    // Placer le marqueur à l'endroit du clic
    marker = L.marker([lat, lng], { icon: blueIcon }).addTo(map);

    // Contenu initial du popup avec la grille la plus proche
    let popupContent = nearestGrid
        ? 'Grille la plus proche : ' + nearestGrid.grid_ref
        : 'Aucune grille trouvée';

    // Ajouter une section de boutons sous forme d'icônes (Vue Aérienne + Waze)
    let buttons = `<div class="popup-buttons" style="display: flex; justify-content: space-around; margin-top: 10px;">`;

    // Vue aérienne
    buttons += `<button onclick="openAerialView(${lat}, ${lng})" data-tooltip="Vue aérienne">
                    <i class="material-icons">satellite</i>
                </button>`;

    // Bouton Waze
    let wazeLink = `https://www.waze.com/ul?ll=${lat},${lng}&navigate=yes`;
    buttons += `<a href="${wazeLink}" target="_blank">
                    <button data-tooltip="Waze">
                        <i class="material-icons">navigation</i>
                    </button>
                </a>`;

    buttons += `</div>`; // Fin du conteneur de boutons

    // Ajouter les boutons au popup
    marker.bindPopup(popupContent + buttons).openPopup();

    marker.on('popupclose', function () {
        map.removeLayer(marker);
        marker = null;

        if (window.parcelPolygon && map.hasLayer(window.parcelPolygon)) {
            map.removeLayer(window.parcelPolygon);
            window.parcelPolygon = null;
        }
    });

    // Envoyer les coordonnées au serveur pour chercher la parcelle
    fetch('/search_parcel', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({ lat, lng }),
    })
        .then(response => {
            if (!response.ok) throw new Error('Aucune parcelle trouvée.');
            return response.json();
        })
        .then(data => {
            if (window.parcelPolygon) {
                map.removeLayer(window.parcelPolygon);
            }

            const latLngs = data.geometry.coordinates[0].map(ring =>
                ring.map(coord => [coord[1], coord[0]])
            );

            window.parcelPolygon = L.polygon(latLngs, { color: 'blue' }).addTo(map);

            let parcelInfo = `<br>Parcelle : ${data.parcelle}`;
            let ownerInfo = '';
            if (data.proprio_nom || data.proprio_prenom) {
                ownerInfo = `<br>Riverain : ${data.proprio_prenom || ''} ${data.proprio_nom || ''}`.trim();
            }

            marker.bindPopup(
                popupContent +
                parcelInfo +
                ownerInfo +
                buttons
            ).openPopup();

            map.fitBounds(window.parcelPolygon.getBounds(), { maxZoom: 18 });
        })
        .catch(error => {
            console.warn(error.message);
        });
});

var drawnItems=new L.FeatureGroup();
map.addLayer(drawnItems);

var drawPolygonBtn=document.getElementById('drawPolygonBtn');
var measureDistanceBtn=document.getElementById('measureDistanceBtn');
var editBtn=document.getElementById('editBtn');
var clearPolygonsBtn=document.getElementById('clearPolygonsBtn');
var drawCircleBtn=document.getElementById('drawCircleBtn');
L.DomEvent.disableClickPropagation(drawPolygonBtn);
L.DomEvent.disableClickPropagation(measureDistanceBtn);
L.DomEvent.disableClickPropagation(editBtn);
L.DomEvent.disableClickPropagation(clearPolygonsBtn);
L.DomEvent.disableClickPropagation(drawCircleBtn);

var drawControl;
var measureControl;
var editControl;
var editMode=false;
var measureCircleControl;

drawPolygonBtn.onclick=function(){
    disableActiveModes();
    drawControl=new L.Draw.Polygon(map,{
        allowIntersection:false,
        showArea:true,
        shapeOptions:{color:'#bada55',opacity:1}
    });
    drawControl.enable();
    map.once(L.Draw.Event.CREATED,function(event){
        var layer=event.layer;
        drawnItems.addLayer(layer);
        calculateArea(layer);
        drawControl.disable();
    });
};

function disableActiveModes(){
    if(editMode){
        map.removeControl(editControl);
        editMode=false;
    }
    if(drawControl)drawControl.disable();
    if(measureControl)measureControl.disable();
    if(measureCircleControl){
        measureCircleControl.disable();
        map.off('mousemove');
    }
}

function calculateArea(layer){
    var geojson=layer.toGeoJSON();
    var area=turf.area(geojson)/10000;
    var campers=Math.floor(area*150);
    var cars=Math.floor(area*300);
    layer.unbindPopup();
    layer.bindPopup(
        'Surface: '+area.toFixed(2)+' ha<br>'+
        'Capacité: '+campers+' campeurs<br>'+
        'Capacité: '+cars+' voitures'
    ).openPopup();
}

measureDistanceBtn.onclick=function(){
    disableActiveModes();
    measureControl=new L.Draw.Polyline(map,{shapeOptions:{color:'#ff0000',weight:2}});
    measureControl.enable();
    map.once(L.Draw.Event.CREATED,function(event){
        var layer=event.layer;
        drawnItems.addLayer(layer);
        var latlngs=layer.getLatLngs();var distance=0;
        for(var i=0;i<latlngs.length-1;i++){
            distance+=latlngs[i].distanceTo(latlngs[i+1]);
        }
        var walkingSpeed=1.1111;
        var walkingTimeInSeconds=distance/walkingSpeed;
        var minutes=Math.floor(walkingTimeInSeconds/60);
        var seconds=Math.floor(walkingTimeInSeconds%60);
        var distanceDisplay=distance>1000?(distance/1000).toFixed(2)+' km':distance.toFixed(2)+' m';
        layer.bindPopup(
            'Distance : '+distanceDisplay+'<br>'+
            'Temps de marche : '+minutes+'m '+seconds+'s'
        ).openPopup();
        measureControl.disable();
    });
};

editBtn.onclick=function(){
    if(editMode){
        map.removeControl(editControl);
        editMode=false;
    }else{
        disableActiveModes();
        editControl=new L.Control.Draw({
            edit:{featureGroup:drawnItems,remove:true},
            draw:false
        });
        map.addControl(editControl);
        editMode=true;
        map.on(L.Draw.Event.EDITSTOP,function(){
            drawnItems.getLayers().forEach(function(layer){
                if(layer instanceof L.Polygon) calculateArea(layer);
                else if(layer instanceof L.Circle) updateCirclePopup(layer);
            });
        });
    }
};

function updateCirclePopup(layer){
    var radius=layer.getRadius();
    var diameter=2*radius;
    var area=Math.PI*Math.pow(radius,2);
    layer.bindPopup(
        'Rayon : '+radius.toFixed(2)+' m<br>'+
        'Diamètre : '+diameter.toFixed(2)+' m<br>'+
        'Surface : '+(area/10000).toFixed(2)+' ha'
    ).openPopup();
}

clearPolygonsBtn.onclick=function(){drawnItems.clearLayers();};

drawCircleBtn.onclick=function(){
    disableActiveModes();
    measureCircleControl=new L.Draw.Circle(map,{shapeOptions:{color:'#007bff',weight:2}});
    measureCircleControl.enable();
    map.once(L.Draw.Event.CREATED,function(event){
        var layer=event.layer;
        var radius=layer.getRadius();
        var diameter=2*radius;
        var area=Math.PI*Math.pow(radius,2);
        drawnItems.addLayer(layer);
        layer.bindPopup(
            'Rayon : '+radius.toFixed(2)+' m<br>'+
            'Diamètre : '+(diameter/1000).toFixed(2)+' km<br>'+
            'Surface : '+(area/10000).toFixed(2)+' ha'
        ).openPopup();
        measureCircleControl.disable();
    });
};

var menuBtn=document.getElementById('menuBtn');
var subButtons=document.getElementsByClassName('subButton');
menuBtn.onclick=function(){
    menuOpen=!menuOpen;
    for(var i=0;i<subButtons.length;i++){
        subButtons[i].style.display=menuOpen?'flex':'none';
    }
    if(!menuOpen) disableActiveModes();
};

var categoryLayerGroup=new L.FeatureGroup();
map.addLayer(categoryLayerGroup);

var showAllButton=document.getElementById('show-all-button');
showAllButton.addEventListener('click',function(){
    var searchTypeValue=document.getElementById('search-type').value;
    if(searchTypeValue==='all'||searchTypeValue==='carroyage'){
        showDynamicFlashMessage("Non disponible pour cette catégorie.", "error");
        return;
    }
    showAllElementsOfCategory(searchTypeValue);
});

function displayItemOnLayerGroup(item, cat, layerGroup, firstLayerOpened) {
    let firstLayer = firstLayerOpened.layer; // Référence au premier élément déjà ajouté (si applicable)

    // Déterminer la collection
    const collection = cat.id || item.collection || null;

    // Vérifier d'abord si l'ID est dans `properties._id_feature`
    let documentId = item.properties && item.properties._id_feature 
        ? item.properties._id_feature 
        : null;

    // Si `_id_feature` est null, essayer `_id` ou `id` directement sur `item`
    if (!documentId) {
        documentId = item._id ? item._id : (item.id ? item.id : null);
    }

    if (cat.dataType === 'geojson') {
        const geometry = item.geometry;

        if (cat.geometryType === 'point') {
            if (geometry.type === 'Point') {
                // Cas d'un point simple
                const latlng = detectOrTransformToPoint(geometry);
                if (latlng) {
                    let icon = getIconForCategory(cat, item);
                    let marker = L.marker(latlng, { icon: icon }).addTo(layerGroup);
                    let popupContent = buildPopupContent(item, cat, collection, documentId);
                    if (!firstLayer) {
                        firstLayerOpened.layer = marker;
                        firstLayerOpened.popupContent = popupContent;
                    } else {
                        marker.bindPopup(popupContent);
                    }
                }
            } else if (geometry.type === 'MultiPoint') {
                // Nouveau cas : gestion des MultiPoint
                const fg = L.featureGroup();
                geometry.coordinates.forEach(coord => {
                    const latlng = [coord[1], coord[0]];
                    let icon = getIconForCategory(cat, item);
                    let m = L.marker(latlng, { icon: icon }).addTo(fg);
                    let popupContent = buildPopupContent(item, cat, collection, documentId);
                    m.bindPopup(popupContent);
                });
                fg.addTo(layerGroup);
                if (!firstLayer) {
                    firstLayerOpened.layer = fg;
                    // Optionnel : vous pouvez stocker le popupContent du premier marker
                    firstLayerOpened.popupContent = fg.getLayers()[0].getPopup().getContent();
                } else {
                    fg.eachLayer(function(layer) {
                        layer.bindPopup(buildPopupContent(item, cat, collection, documentId));
                    });
                }
            }
        } else if (cat.geometryType === 'polygon') {
            // Gestion des polygones et multipolygones
            let popupContent = buildPopupContent(item, cat, collection, documentId);
            let polygon;

            if (geometry.type === 'Polygon') {
                let latlngs = convertGeometryToLatLngs(geometry);
                polygon = L.polygon(latlngs, cat.style || { color: 'blue' }).addTo(layerGroup);
            } else if (geometry.type === 'MultiPolygon') {
                let multiLatLngs = geometry.coordinates.map(p => p[0].map(c => [c[1], c[0]]));
                polygon = L.polygon(multiLatLngs, cat.style || { color: 'blue' }).addTo(layerGroup);
            }

            if (polygon) {
                if (!firstLayer) {
                    firstLayerOpened.layer = polygon;
                    firstLayerOpened.popupContent = popupContent;
                } else {
                    polygon.bindPopup(popupContent);
                }
            }
        }
    } else if (cat.dataType === 'json') {
        // Gestion des données JSON simples (point)
        const lat = parseFloat(item.latitude);
        const lng = parseFloat(item.longitude);
        let icon = getIconForCategory(cat, item);
        let marker = L.marker([lat, lng], { icon: icon }).addTo(layerGroup);
        let popupContent = buildPopupContent(item, cat, collection, documentId);
        if (!firstLayer) {
            firstLayerOpened.layer = marker;
            firstLayerOpened.popupContent = popupContent;
        } else {
            marker.bindPopup(popupContent);
        }
    }
}

function showAllElementsOfCategory(categoryId) {
    categoryLayerGroup.clearLayers();
    if (marker) {
        map.removeLayer(marker);
        marker = null;
    }

    const cat = categories.find(c => c.id === categoryId);
    const data = datasets[categoryId];
    if (!data) {
        showDynamicFlashMessage("Aucune donnée pour cette catégorie.", "warning");
        return;
    }

    // Stocker la référence du premier élément ajouté et son contenu de popup
    let firstLayerOpened = { layer: null, popupContent: '' };

    data.forEach(item => {
        displayItemOnLayerGroup(item, cat, categoryLayerGroup, firstLayerOpened);
    });

    // Ouvrir le popup du premier élément
    if (firstLayerOpened.layer) {
        setTimeout(() => {
            firstLayerOpened.layer.bindPopup(firstLayerOpened.popupContent).openPopup();
        }, 100); // Délai pour éviter les interférences
    }

    zoomToLayerGroup(categoryLayerGroup);
}

function zoomToLayerGroup(layerGroup) {
    // Calcul des limites (bounds) de tous les layers du groupe
    let bounds = null;
    layerGroup.eachLayer(function (layer) {
        if (layer instanceof L.Marker) {
            // Marker : utilisation de latlng
            const latLng = layer.getLatLng();
            if (!bounds) {
                bounds = L.latLngBounds(latLng, latLng);
            } else {
                bounds.extend(latLng);
            }
        } else if (layer.getBounds && typeof layer.getBounds === 'function') {
            // Polygon, Polyline, ou autre : utilisation de bounds
            const layerBounds = layer.getBounds();
            if (!bounds) {
                bounds = layerBounds;
            } else {
                bounds.extend(layerBounds);
            }
        }
    });

    if (bounds) {
        // Définir les options de zoom avec padding pour éviter un zoom excessif
        map.fitBounds(bounds, {
            padding: [50, 50], // Ajoute du padding autour des limites
            maxZoom: 16,      // Limite le zoom maximal
            animate: true     // Animation fluide
        });
    } else {
        showDynamicFlashMessage("Aucun élément trouvé pour ajuster le zoom.", "warning");
    }
}

// Référence au bouton d'effacement
const clearMapButton = document.getElementById('clear-map-button');

clearMapButton.addEventListener('click', () => {
    clearAllElements();
});

function clearAllElements() {
    // Supprimer uniquement les éléments ajoutés par la recherche
    if (categoryLayerGroup) {
        categoryLayerGroup.clearLayers();
    }

    // Supprimer le polygone de la parcelle s'il existe
    if (window.parcelPolygon && map.hasLayer(window.parcelPolygon)) {
        map.removeLayer(window.parcelPolygon);
        window.parcelPolygon = null;
    }

    // Supprimer tous les éléments dessinés sur la carte (polygones, cercles, lignes, etc.)
    if (drawnItems) {
        drawnItems.clearLayers();
    }

    // Réinitialiser le marqueur individuel
    if (marker && map.hasLayer(marker)) {
        map.removeLayer(marker);
        marker = null;
    }

    // Réinitialiser tous les popups et marqueurs des vues aériennes
    if (aerialPopups.length > 0) {
        aerialPopups.forEach(popup => map.removeLayer(popup));
        aerialPopups = [];
        aerialViewActive = false;
    }

    if (aerialMarkers.length > 0) {
        aerialMarkers.forEach(marker => map.removeLayer(marker));
        aerialMarkers = [];
        aerialViewActive = false;
    }

    // Réinitialiser le champ de texte
    const inputElement = document.getElementById('grid-input');
    if (inputElement) {
        inputElement.value = ''; // Efface le contenu du champ
    }

    // Réinitialiser la vue (centrage et zoom)
    map.setView([47.938561591531936, 0.2243184111156285], 14);
}

// Fonction mise à jour pour afficher un message flash dynamique
function showDynamicFlashMessage(message, category = 'success', duration = 3000) {
    const flashContainer = document.getElementById('flash-container');

    // Vérifier si le conteneur existe, sinon le créer
    if (!flashContainer) {
        console.error("Conteneur de messages flash introuvable !");
        return;
    }

    // Créer un nouvel élément pour le message
    const flashMessage = document.createElement('div');
    flashMessage.textContent = message;
    flashMessage.className = `flash-popup ${category}`; // Ajoute la classe en fonction de la catégorie

    // Ajouter le message au conteneur
    flashContainer.appendChild(flashMessage);

    // Supprimer automatiquement le message après la durée spécifiée
    setTimeout(() => {
        flashMessage.style.opacity = '0';
        setTimeout(() => {
            flashMessage.remove();
        }, 500); // Attendre la transition
    }, duration);
}

// Stocker les popups et marqueurs pour les gérer proprement
let aerialPopups = [];
let aerialMarkers = [];
let aerialViewActive = false; // État du bouton Vue Aérienne

function openAerialView(lat, lng) {
    if (aerialViewActive) {
        // 🔴 Mode OFF : Supprimer tous les popups et marqueurs
        aerialPopups.forEach(popup => map.removeLayer(popup));
        aerialMarkers.forEach(marker => map.removeLayer(marker));

        aerialPopups = [];
        aerialMarkers = [];
        aerialViewActive = false; // Désactiver la vue aérienne
        return;
    }

    // 🟢 Mode ON : Charger les photos aériennes
    const collectionName = "24H_AUTOS_2024_1_tagger"; // ⚠️ À rendre dynamique si besoin

    fetch("/get_aerial_photos", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken
        },
        body: JSON.stringify({ lat, lng, collection: collectionName })
    })
    .then(response => response.json())
    .then(data => {
        if (data.length === 0) {
            alert("Aucune photo aérienne disponible à proximité.");
            return;
        }

        // Supprimer les anciens popups et marqueurs avant d'ajouter les nouveaux
        aerialPopups.forEach(popup => map.removeLayer(popup));
        aerialMarkers.forEach(marker => map.removeLayer(marker));
        aerialPopups = [];
        aerialMarkers = [];

        let bounds = [];

        // Ajouter un popup + un marqueur pour chaque photo trouvée
        data.forEach(photo => {
            const popupContent = `
                <div class="popup-aerial" style="text-align: center;">
                    <img src="${photo.thumbnail_url}" alt="${photo.filename}" 
                        style="width: 250px; max-width: 100%; border-radius: 5px; cursor: pointer;" 
                        onclick="openPhotoSwipe('${photo.medium_url}')">
                    <p style="margin: 5px 0;"><strong>Distance :</strong> ${Math.round(photo.distance)}m</p>
                </div>
            `;
            // Ajouter un marqueur pour chaque photo
            const marker = L.marker([photo.lat, photo.lng], { icon: blueIcon }).addTo(map);
            aerialMarkers.push(marker); // Stocker le marqueur

            // Ajouter un popup indépendant (NON lié à un marqueur)
            const popup = L.popup({ autoClose: false, closeOnClick: false, maxWidth: 150, minWidth: 150 })
                .setLatLng([photo.lat, photo.lng])
                .setContent(popupContent)
                .addTo(map); // L'ajouter directement sur la carte

            aerialPopups.push(popup);
            bounds.push([photo.lat, photo.lng]);
        });

        // Ajuster la vue si plusieurs points
        if (bounds.length > 1) {
            map.fitBounds(bounds, { padding: [50, 50] });
        }

        aerialViewActive = true; // Activer la vue aérienne
    })
    .catch(error => console.error("Erreur lors de la récupération des photos:", error));
}

function openUploadModal(type = "file", collection = "", documentId = "") {
    console.log("Ouverture de l'upload modal pour :", type, collection, documentId);

    let uploadTitle = (type === "photo") ? "Uploader une photo" : "Uploader un document";
    document.getElementById("upload-modal-title").innerText = uploadTitle;

    // Met à jour les champs cachés
    document.getElementById("uploadType").value = type;
    document.getElementById("collectionName").value = collection;
    document.getElementById("documentId").value = documentId;

    const modalElement = document.getElementById("uploadModal");
    const overlay = document.querySelector(".modal-overlay");

    // Appliquer les styles dynamiques
    modalElement.classList.remove("photo-mode", "file-mode");
    modalElement.classList.add(type === "photo" ? "photo-mode" : "file-mode");

    // Affichage propre avec transition fluide
    overlay.style.display = "block";
    modalElement.style.display = "block";

    setTimeout(() => {
        modalElement.classList.add("show");
        overlay.classList.add("show");
    }, 10);
}

function closeUploadModal() {
    const modalElement = document.getElementById("uploadModal");
    const overlay = document.querySelector(".modal-overlay");

    modalElement.classList.remove("show");
    overlay.classList.remove("show");

    setTimeout(() => {
        modalElement.style.display = "none";
        overlay.style.display = "none";
    }, 300);
}

function updateUploadFileName() {
    const fileInput = document.getElementById("uploadInput");
    const fileName = document.getElementById("upload-file-name");

    fileName.textContent = fileInput.files.length > 0 ? fileInput.files[0].name : "Sélectionnez un fichier";
}

document.addEventListener("DOMContentLoaded", function () {
    const closeButton = document.querySelector("#uploadModal .close");
    if (closeButton) {
        closeButton.addEventListener("click", closeUploadModal);
    }
});

async function submitUpload() {
    const formData = new FormData();
    const fileInput = document.getElementById("uploadInput");
    const uploadType = document.getElementById("uploadType").value;
    const collectionName = document.getElementById("collectionName").value;
    const documentId = document.getElementById("documentId").value;

    if (!fileInput.files.length) {
        alert("Veuillez sélectionner un fichier !");
        return;
    }

    formData.append("file", fileInput.files[0]);
    formData.append("type", uploadType);
    formData.append("collection", collectionName);
    formData.append("document_id", documentId);

    try {
        const response = await fetch("/upload_file", {
            method: "POST",
            headers: {
                "X-CSRFToken": csrfToken
            },
            body: formData,
        });

        const result = await response.json();

        if (response.ok) {
            alert("Upload réussi : " + result.filename);
            closeUploadModal();
        } else {
            alert("Erreur : " + result.error);
        }
    } catch (error) {
        console.error("Erreur lors de l'upload :", error);
        alert("Une erreur est survenue.");
    }
}