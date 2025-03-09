/////////////////////////////////////////////////////////////////////////////////////////////////////
// RECUPERATION CONFIGURATION
/////////////////////////////////////////////////////////////////////////////////////////////////////




/////////////////////////////////////////////////////////////////////////////////////////////////////
// AFFICHAGE PORTES
/////////////////////////////////////////////////////////////////////////////////////////////////////



/////////////////////////////////////////////////////////////////////////////////////////////////////
// CHIFFRES GLOBAUX
/////////////////////////////////////////////////////////////////////////////////////////////////////

// Fonction qui récupère la valeur du compteur et la met à jour dans le DOM
function updateCounter() {
    const eventParam = encodeURIComponent(window.selectedEvent || '');
    const yearParam = encodeURIComponent(window.selectedYear || '');
    fetch('/get_counter?event=' + eventParam + '&year=' + yearParam)
        .then(response => response.json())
        .then(data => {
            document.querySelector('#counter_now').textContent = data.current;
        })
        .catch(error => console.error('Error fetching counter:', error));
}

// Fonction qui récupère la valeur du compteur maximum et la met à jour dans le DOM
function updateCounterMax() {
    const eventParam = encodeURIComponent(window.selectedEvent || '');
    const yearParam = encodeURIComponent(window.selectedYear || '');
    fetch('/get_counter_max?event=' + eventParam + '&year=' + yearParam)
        .then(response => response.json())
        .then(data => {
            document.querySelector('#counter_max').textContent = data.current;
        })
        .catch(error => console.error('Error fetching counter max:', error));
}

// Appel initial
function updateGlobalCounter () {
    updateCounter();
    updateCounterMax();
}

// Mise à jour toutes les 2 minutes
setInterval(function() {
    updateGlobalCounter();
}, 90000);