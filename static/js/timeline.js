// Fonction utilitaire pour convertir l'heure "HH:MM" en minutes depuis minuit
function timeToMinutes(timeStr) {
    if (!timeStr || timeStr.toUpperCase() === "TBC") return Infinity;
    const parts = timeStr.split(':');
    return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
}

// Fonction pour tronquer une chaîne à un nombre max de caractères
function truncateText(text, maxChars) {
    return text.length > maxChars ? text.substring(0, maxChars) + "…" : text;
}

// Fonction pour créer une vignette d'événement dans la timeline avec affichage en deux colonnes
function createEventItem(date, item) {
    const eventItem = document.createElement("div");
    eventItem.classList.add("event-item");

    // Définir une icône selon la catégorie
    let iconHtml = "";
    if (item.category.indexOf("Motos") !== -1) {
        iconHtml = `<span class="material-icons">motorcycle</span>`;
    } else if (item.category.indexOf("Evénement") !== -1) {
        iconHtml = `<span class="material-icons">event</span>`;
    } else {
        iconHtml = `<span class="material-icons">info</span>`;
    }

    // Extraire la partie française du titre (avant le slash)
    let fullTitle = item.activity.split('/')[0].trim();
    // Extraire la partie française du lieu (avant le slash)
    let fullPlace = item.place.split('/')[0].trim();
    // On tronque pour la version compacte (par exemple, à 50 caractères)
    let truncatedTitle = truncateText(fullTitle, 50);

    // Gestion de l'affichage des heures
    let timeInfo = "";
    if (item.start && item.start.trim() !== "" && item.start.toUpperCase() !== "TBC") {
        timeInfo = item.start;
        if (item.end && item.end.trim() !== "" && item.end.toUpperCase() !== "TBC") {
            timeInfo += " - " + item.end;
        }
    } else if (item.end && item.end.trim() !== "" && item.end.toUpperCase() !== "TBC") {
        timeInfo = item.end;
    } else {
        timeInfo = "TBC";
    }

    // Construction du résumé en deux colonnes
    eventItem.innerHTML = `
        <div class="event-summary">
            <div class="event-title">
                ${iconHtml}
                <h5>${truncatedTitle}</h5>
            </div>
            <div class="event-time">
                <p class="time-info">${timeInfo}</p>
                <p class="event-location">${fullPlace}</p>
            </div>
            <div class="buttons-container">
                <button class="expand-btn">
                    <span class="material-icons">expand_more</span>
                </button>
            </div>
        </div>
        <div class="toggle-content">
            <!-- Version détaillée -->
            <p><strong>Titre complet :</strong> ${fullTitle}</p>
            <p><strong>Heure de début :</strong> ${item.start || "TBC"}</p>
            <p><strong>Heure de fin :</strong> ${item.end || "TBC"}</p>
            <p><strong>Durée :</strong> ${item.duration}</p>
            <p><strong>Département :</strong> ${item.department}</p>
            <p><strong>Lieu détaillé :</strong> ${item.place ? item.place : "Non spécifié"}</p>
            <p><strong>Commentaires :</strong> ${item.remark ? item.remark : "Non spécifié"}</p>
        </div>
    `;

    // Attacher l'écouteur sur le bouton d'extension
    const expandBtn = eventItem.querySelector(".expand-btn");
    if (expandBtn) {
        expandBtn.addEventListener("click", function(e) {
            e.stopPropagation(); // Empêche l'ouverture de la modale lors du clic sur ce bouton
            toggleDetails(e, this);
        });
    }

    // Rendre la vignette cliquable pour ouvrir la modale détaillée (en dehors du bouton d'extension)
    eventItem.addEventListener("click", function(e) {
        if (!e.target.closest(".expand-btn")) {
            openTimetableItemModal(date, item);
        }
    });
    
    return eventItem;
}

// Fonction pour basculer l'affichage des détails (expand/collapse)
function toggleDetails(e, button) {
    const eventItem = button.closest('.event-item');
    if (!eventItem) return;
    eventItem.classList.toggle('expanded');
    const icon = button.querySelector('.material-icons');
    if (eventItem.classList.contains('expanded')) {
        icon.textContent = 'expand_less';
    } else {
        icon.textContent = 'expand_more';
    }
}

// Fonction pour récupérer et afficher le timetable dans la timeline
function fetchTimetable() {
    if (!window.selectedEvent || !window.selectedYear) {
        console.error("Les variables globales 'selectedEvent' et 'selectedYear' doivent être définies.");
        return;
    }
    const url = '/timetable?event=' + encodeURIComponent(window.selectedEvent) + '&year=' + encodeURIComponent(window.selectedYear);
    
    fetch(url)
        .then(response => response.json())
        .then(data => {
            const eventList = document.getElementById("event-list");

            const sectionsByDate = {}; // Pour stocker les sections par date

            if (data.data) {
                Object.keys(data.data).sort().forEach(date => {
                    const items = data.data[date];
                    // Trier les items selon getTimeForSort()
                    items.sort((a, b) => getTimeForSort(a) - getTimeForSort(b));

                    const dateSection = document.createElement("div");
                    dateSection.classList.add("timetable-date-section");
                    sectionsByDate[date] = dateSection;

                    // Créer un container pour le header de la date
                    const dateHeaderContainer = document.createElement("div");
                    dateHeaderContainer.classList.add("date-header-container");

                    const d = new Date(date);
                    const dateHeader = document.createElement("h5");
                    dateHeader.textContent = d.toLocaleDateString("fr-FR");
                    dateHeaderContainer.appendChild(dateHeader);

                    // Vérifier si la journée contient un item avec category "General" et activity "Ouverture au public"
                    const publicOpen = items.some(item => {
                        return item.category === "General" &&
                            item.activity.trim().toLowerCase() === "ouverture au public";
                    });
                    const banner = document.createElement("p");
                    banner.textContent = publicOpen ? "OUVERT AU PUBLIC" : "FERME AU PUBLIC";
                    banner.classList.add(publicOpen ? "banner-open" : "banner-closed");
                    dateHeaderContainer.appendChild(banner);

                    dateSection.appendChild(dateHeaderContainer);

                    // Créer les vignettes pour chaque item, sauf ceux avec General / Ouverture au public
                    items.forEach(item => {
                        if (item.category === "General" && item.activity.trim().toLowerCase() === "ouverture au public") {
                            // Ne pas générer la vignette pour cet item
                            return;
                        }
                        const eventItem = createEventItem(date, item);
                        dateSection.appendChild(eventItem);
                    });

                    eventList.appendChild(dateSection);
                });
            } else {
                eventList.innerHTML += "<p>Aucune donnée de timetable disponible.</p>";
            }
        })
        .catch(error => console.error("Erreur lors de la récupération du timetable :", error));
}

function getTimeForSort(item) {
    // Si start est défini, non vide et différent de "TBC", on l'utilise
    if (item.start && item.start.trim() !== "" && item.start.toUpperCase() !== "TBC") {
        return timeToMinutes(item.start);
    }
    // Sinon, si end est défini et valide, on l'utilise
    if (item.end && item.end.trim() !== "" && item.end.toUpperCase() !== "TBC") {
        return timeToMinutes(item.end);
    }
    // Sinon, on retourne Infinity pour le classer en fin
    return Infinity;
}

// Nouvelle fonction pour récupérer les paramètres (paramétrage) via POST
function fetchParametrage() {
    if (!window.selectedEvent || !window.selectedYear) {
        console.error("Les variables globales 'selectedEvent' et 'selectedYear' doivent être définies.");
        return Promise.resolve(null);
    }
    return fetch('/get_parametrage?event=' + encodeURIComponent(window.selectedEvent) + '&year=' + encodeURIComponent(window.selectedYear))
        .then(response => response.json())
        .then(data => {
            window.parametrage = data; // Stocke les données dans une variable globale
            console.log("Paramétrage :", data);
            return data;
        })
        .catch(error => {
            console.error("Erreur lors de la récupération des paramètres :", error);
            return Promise.resolve(null);
        });
}

// Exemple de fonction pour ouvrir la modale détaillée pour un item
function openTimetableItemModal(date, item) {
    console.log("Ouverture de la modale pour la date", date, "et l'item :", item);
    // Implémentez ici l'ouverture de votre modale détaillée
}

// Écouteur sur le bouton HUD pour lancer fetchTimetable()
document.addEventListener('DOMContentLoaded', function() {
    const hudButton = document.getElementById("hud-button");
    if (hudButton) {
        hudButton.addEventListener("click", function() {
            fetchTimetable();
            updateGlobalCounter();
        });
    } else {
        console.error("Le bouton avec l'ID 'hud-button' n'a pas été trouvé.");
    }
});

/////////////////////////////////////////////////////////////////////////////////////////////////////
// FONCTION AJOUT
/////////////////////////////////////////////////////////////////////////////////////////////////////

document.addEventListener('DOMContentLoaded', function(){
    // Références aux éléments
    const addEventButton = document.getElementById('add-event-button');
    const addEventModal = document.getElementById('addEventModal');
    const closeAddEvent = document.getElementById('closeAddEvent');
    const cancelAddEvent = document.getElementById('cancelAddEvent');
    const addEventForm = document.getElementById('addEventForm');
    const categorySelect = document.getElementById('category');

    // Fonction pour ouvrir la modale en ajoutant la classe "show"
    function openModal(modal) {
        modal.style.display = 'block';
        // Permettre la transition définie dans le CSS (opacity et scale)
        setTimeout(() => {
            modal.classList.add('show');
        }, 10);
    }
    
    // Fonction pour fermer la modale
    function closeModal(modal) {
        modal.classList.remove('show');
        // Après la transition, masquer la modale
        setTimeout(() => {
            modal.style.display = 'none';
        }, 300);
    }
    
    // Ouvrir la modale lors du clic sur le bouton "Ajouter un événement"
    addEventButton.addEventListener('click', function(){
        // Charger les catégories depuis le serveur
        fetch('/get_timetable_categories?event=' + encodeURIComponent(window.selectedEvent) + '&year=' + encodeURIComponent(window.selectedYear))
        .then(response => response.json())
        .then(data => {
            categorySelect.innerHTML = '';
            data.categories.forEach(cat => {
                const option = document.createElement('option');
                option.value = cat;
                option.textContent = cat;
                categorySelect.appendChild(option);
            });
        })
        .catch(err => console.error("Erreur chargement catégories:", err));    
        
        openModal(addEventModal);
    });
    
    // Fermer la modale au clic sur la croix ou le bouton Annuler
    closeAddEvent.addEventListener('click', () => closeModal(addEventModal));
    cancelAddEvent.addEventListener('click', () => closeModal(addEventModal));
    
    // Soumission du formulaire
    addEventForm.addEventListener('submit', function(e){
        e.preventDefault();
        // Récupérer les valeurs du formulaire
        const formData = {
            // On suppose que l'événement et l'année sont les mêmes que ceux actuellement sélectionnés
            event: window.selectedEvent || '24H MOTOS',
            year: window.selectedYear || '2025',
            date: document.getElementById('event-date').value,
            start: document.getElementById('start-time').value,
            end: document.getElementById('end-time').value,
            duration: document.getElementById('duration').value,
            category: document.getElementById('category').value,
            activity: document.getElementById('activity').value,
            place: document.getElementById('place').value,
            department: document.getElementById('department').value,
            remark: document.getElementById('remark').value,
            type: "Timetable",
            origin: "manual"
        };
        
        fetch('/add_timetable_event', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').getAttribute('content')
            },
            body: JSON.stringify(formData)
        })
        .then(response => response.json())
        .then(data => {
            if(data.success){
                // Afficher un message de succès (la fonction showDynamicFlashMessage est déjà définie dans main.js)
                showDynamicFlashMessage("Événement ajouté avec succès", "success");
                closeModal(addEventModal);
                // Optionnel : rafraîchir la timeline pour refléter l'ajout
                fetchTimetable();
            } else {
                showDynamicFlashMessage("Erreur lors de l'ajout de l'événement", "error");
            }
        })
        .catch(err => {
            console.error("Erreur lors de l'ajout de l'événement:", err);
            showDynamicFlashMessage("Erreur lors de l'ajout de l'événement", "error");
        });
    });
    
    // Fermer la modale si l'utilisateur clique en dehors du contenu de la modale
    window.addEventListener('click', function(e){
        if(e.target == addEventModal){
            closeModal(addEventModal);
        }
    });
});