/////////////////////////////////////////////////////////////////////////////////////////////////////
// CONSTANTES
/////////////////////////////////////////////////////////////////////////////////////////////////////

let categories = [];     // Liste des catégories
let datasets = {};       // datasets[categoryId] = data
let categorySuggestions = {};
let awesomplete; 
let marker;
let menuOpen = false;
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

// Déclaration de variables globales pour stocker les sélections
window.selectedEvent = null;
window.selectedYear = null;

/////////////////////////////////////////////////////////////////////////////////////////////////////
// LEAFLET INITIALISATION
/////////////////////////////////////////////////////////////////////////////////////////////////////

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

/////////////////////////////////////////////////////////////////////////////////////////////////////
// SIDEBAR
/////////////////////////////////////////////////////////////////////////////////////////////////////

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

document.addEventListener('DOMContentLoaded', function () {
  
    // Référence aux éléments select
    const eventSelect = document.getElementById('event-select');
    const yearSelect  = document.getElementById('year-select');
  
    // --- Récupération et peuplement du select "Événement" ---
    fetch('/get_events')
    .then(response => response.json())
    .then(eventsData => {
        let defaultFound = false;
        eventsData.forEach(item => {
        const option = document.createElement('option');
        option.value = item.nom;      // On utilise la propriété 'nom'
        option.textContent = item.nom;
        eventSelect.appendChild(option);

        // Si l'événement est "24H AUTOS", on le sélectionne par défaut
        if (item.nom === "24H AUTOS") {
            option.selected = true;
            window.selectedEvent = item.nom;
            defaultFound = true;
        }
        });
        // Si "24H AUTOS" n'est pas trouvé, on sélectionne le premier élément
        if (!defaultFound && eventSelect.options.length > 0) {
        eventSelect.selectedIndex = 0;
        window.selectedEvent = eventSelect.options[0].value;
        }
    })
    .catch(error => console.error('Erreur lors de la récupération des événements :', error));
  
    // --- Peuplement du select "Année" ---
    const currentYear = new Date().getFullYear();
    const startYear   = 2024;
    // On génère les options de 2024 jusqu'à (année en cours + 1)
    for (let year = startYear; year <= currentYear + 1; year++) {
      const option = document.createElement('option');
      option.value = year;
      option.textContent = year;
      // L'année en cours sera sélectionnée par défaut
      if (year === currentYear) {
        option.selected = true;
        window.selectedYear = year;
      }
      yearSelect.appendChild(option);
    }
  
    // --- Écouteurs d'événements pour mettre à jour les variables globales lors d'un changement ---
    eventSelect.addEventListener('change', function () {
      window.selectedEvent = this.value;
      console.log('Événement sélectionné :', window.selectedEvent);
    });
  
    yearSelect.addEventListener('change', function () {
      window.selectedYear = parseInt(this.value, 10);
      console.log('Année sélectionnée :', window.selectedYear);
    });
  });


/////////////////////////////////////////////////////////////////////////////////////////////////////
// ALERTES
/////////////////////////////////////////////////////////////////////////////////////////////////////

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

/////////////////////////////////////////////////////////////////////////////////////////////////////
// NAVBAR
/////////////////////////////////////////////////////////////////////////////////////////////////////

// Assurez-vous que window.selectedEvent et window.selectedYear sont définis dans votre application.
document.getElementById("stats-page-button").addEventListener("click", function(){
    if (!window.selectedEvent || !window.selectedYear) {
        showDynamicFlashMessage("Veuillez sélectionner un événement et une année", "error");
        return;
    }
    var eventParam = encodeURIComponent(window.selectedEvent);
    var yearParam  = encodeURIComponent(window.selectedYear);
    var url = "/general_stat?event=" + eventParam + "&year=" + yearParam;
    window.open(url, "_blank");
});

document.getElementById("parkings-page-button").addEventListener("click", function(){
    if (!window.selectedEvent || !window.selectedYear) {
        showDynamicFlashMessage("Veuillez sélectionner un événement et une année", "error");
        return;
    }
    var eventParam = encodeURIComponent(window.selectedEvent);
    var yearParam  = encodeURIComponent(window.selectedYear);
    var url = "/terrains?event=" + eventParam + "&year=" + yearParam;
    window.open(url, "_blank");
});

document.getElementById("doors-page-button").addEventListener("click", function(){
    if (!window.selectedEvent || !window.selectedYear) {
        showDynamicFlashMessage("Veuillez sélectionner un événement et une année", "error");
        return;
    }
    var eventParam = encodeURIComponent(window.selectedEvent);
    var yearParam  = encodeURIComponent(window.selectedYear);
    var url = "/doors?event=" + eventParam + "&year=" + yearParam;
    window.open(url, "_blank");
});