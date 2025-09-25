/////////////////////////////////////////////////////////////////////////////////////////////////////
// CONSTANTES
/////////////////////////////////////////////////////////////////////////////////////////////////////

let categories = [];     // Liste des catégories
let datasets = {};       // datasets[categoryId] = data
let categorySuggestions = {};
let awesomplete; 
let marker;
let menuOpen = false;
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content ?? "";

// Déclaration de variables globales pour stocker les sélections
window.selectedEvent = null;
window.selectedYear = null;

/////////////////////////////////////////////////////////////////////////////////////////////////////
// UTILITAIRE
/////////////////////////////////////////////////////////////////////////////////////////////////////

// Petit utilitaire safe pour brancher un listener si l'élément existe
function on(elOrId, event, handler) {
    const el = typeof elOrId === "string" ? document.getElementById(elOrId) : elOrId;
    if (el) el.addEventListener(event, handler, false);
}

// Helpers REST
function apiPost(url, payload){
return fetch(url, {
    method: 'POST',
    headers: {
    'Content-Type': 'application/json',
    'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]')?.content) || ''
    },
    body: JSON.stringify(payload)
}).then(r => r.json());
}

// Récup utilitaire
function getCurrentEventYear() {
return {
    event: window.selectedEvent || '',
    year: String(window.selectedYear || '')
};
}

/////////////////////////////////////////////////////////////////////////////////////////////////////
// SIDEBAR
/////////////////////////////////////////////////////////////////////////////////////////////////////

document.addEventListener("DOMContentLoaded", function () {
    const sidebar = document.getElementById("sidebar");
    const hamburgerButton = document.getElementById("hamburger-button");
    const body = document.body;
    
    if (hamburgerButton && sidebar) {
        hamburgerButton.addEventListener("click", function () {
            const open = sidebar.classList.toggle("active");
            body.classList.toggle("body-with-sidebar", open);
            body.classList.toggle("body-without-sidebar", !open);
        });
    }
});

document.addEventListener('DOMContentLoaded', function () {
    // Référence aux éléments select
    const eventSelect = document.getElementById('event-select');
    const yearSelect  = document.getElementById('year-select');
  
    // --- Récupération et peuplement du select "Événement" ---
    fetch('/get_events')
    .then(response => response.json())
    .then(eventsData => {
        if (!eventSelect) return;

        let defaultFound = false;
        eventsData.forEach(item => {
            const option = document.createElement('option');
            option.value = item.nom;      // On utilise la propriété 'nom'
            option.textContent = item.nom;
            eventSelect.appendChild(option);

            if (item.nom === "24H AUTOS") {
                option.selected = true;
                window.selectedEvent = item.nom;
                defaultFound = true;
            }
        });
        if (!defaultFound && eventSelect.options.length > 0) {
            eventSelect.selectedIndex = 0;
            window.selectedEvent = eventSelect.options[0].value;
        }
    })
    .catch(error => console.error('Erreur lors de la récupération des événements :', error));
  
    // --- Peuplement du select "Année" ---
    const currentYear = new Date().getFullYear();
    const startYear   = 2024;
    if (yearSelect) {
        for (let year = startYear; year <= currentYear + 1; year++) {
            const option = document.createElement('option');
            option.value = year;
            option.textContent = year;
            if (year === currentYear) {
                option.selected = true;
                window.selectedYear = year;
            }
            yearSelect.appendChild(option);
        }
    }
  
    // --- Écouteurs d'événements pour mettre à jour les variables globales ---
    if (eventSelect) {
        eventSelect.addEventListener('change', function () {
            window.selectedEvent = this.value;
            console.log('Événement sélectionné :', window.selectedEvent);
        });
    }
    if (yearSelect) {
        yearSelect.addEventListener('change', function () {
            window.selectedYear = parseInt(this.value, 10);
            console.log('Année sélectionnée :', window.selectedYear);
        });
    }
});

// ==================== DRAWER ÉVÉNEMENT ====================
(function(){
  const drawer    = document.getElementById('event-drawer');
  const overlay   = document.getElementById('event-drawer-overlay');
  const closeBtn  = document.getElementById('drawer-close');
  const bodyEl    = document.getElementById('event-drawer-body');
  const titleEl   = document.getElementById('drawer-title');

  // Expose global pour que timeline.js puisse l'appeler
  window.openEventDrawer = function(eventItem) {
    if (!drawer || !bodyEl) return;

    // Titre
    titleEl.textContent = eventItem?.activity || 'Événement';

    // Rendu champs (adapte aux clés que tu as)
    const fields = [
      { label: 'Date', value: eventItem?.date },
      { label: 'Heure', value: formatTimeRange(eventItem?.start, eventItem?.end) },
      { label: 'Catégorie', value: eventItem?.category },
      { label: 'Lieu', value: eventItem?.place },
      { label: 'Département', value: eventItem?.department },
      { label: 'Durée', value: eventItem?.duration },
      { label: 'Remarque', value: eventItem?.remark }
    ].filter(f => f.value && String(f.value).trim().length);

    bodyEl.innerHTML = fields.map(f => `
      <div class="field">
        <div class="label">${f.label}</div>
        <div class="value">${escapeHtml(String(f.value))}</div>
      </div>
    `).join('') || '<div style="opacity:.7">Aucune information.</div>';

    // Stocker l’item courant pour les boutons
    drawer.dataset.itemId = eventItem?._id || '';
    drawer.dataset.itemRaw = JSON.stringify(eventItem || {});

    // Ouvrir
    drawer.classList.add('open');
    overlay?.classList.add('show');
    drawer.setAttribute('aria-hidden', 'false');
  };

  window.closeEventDrawer = function() {
    drawer?.classList.remove('open');
    overlay?.classList.remove('show');
    drawer?.setAttribute('aria-hidden', 'true');
  };

  // Helpers
  function pad2(n){ return (n+'').padStart(2,'0'); }
  function formatTimeRange(start, end){
    if (!start && !end) return '';
    const s = (start && start !== 'TBC') ? start : '—';
    const e = (end && end !== 'TBC') ? end   : '—';
    return `${s} - ${e}`;
  }
  function escapeHtml(s){
    return s.replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  // Fermetures
  overlay?.addEventListener('click', window.closeEventDrawer);
  closeBtn?.addEventListener('click', window.closeEventDrawer);

  // Boutons d’action (branche selon tes endpoints)
    document.getElementById('drawer-edit')?.addEventListener('click', () => {
    try {
        const drawer = document.getElementById('event-drawer');
        const item = JSON.parse(drawer.dataset.itemRaw || '{}');

        // Référence à la modale d'ajout existante
        const addEventModal = document.getElementById('addEventModal');
        const addEventForm  = document.getElementById('addEventForm');

        // Champs
        const fDate   = document.getElementById('event-date');
        const fStart  = document.getElementById('start-time');
        const fEnd    = document.getElementById('end-time');
        const fDur    = document.getElementById('duration');
        const fCat    = document.getElementById('category');
        const fAct    = document.getElementById('activity');
        const fPlace  = document.getElementById('place');
        const fDept   = document.getElementById('department');
        const fRemark = document.getElementById('remark');

        if (!addEventModal || !addEventForm) {
        showDynamicFlashMessage("Modale d’édition introuvable.", "error");
        return;
        }

        // Indiquer qu'on est en mode EDIT
        window.formMode = 'edit';
        window.editingItemId = item?._id || null;

        // Injecter un input hidden pour l'ID si pas déjà présent
        let hiddenId = addEventForm.querySelector('input[name="_id"]');
        if (!hiddenId) {
        hiddenId = document.createElement('input');
        hiddenId.type = 'hidden';
        hiddenId.name = '_id';
        addEventForm.appendChild(hiddenId);
        }
        hiddenId.value = window.editingItemId || '';

        // Pré-remplir
        if (fDate)   fDate.value   = (item.date || '').slice(0,10); // yyyy-mm-dd
        if (fStart)  fStart.value  = item.start   || '';
        if (fEnd)    fEnd.value    = item.end     || '';
        if (fDur)    fDur.value    = item.duration|| '';
        if (fCat)    fCat.value    = item.category|| '';
        if (fAct)    fAct.value    = item.activity|| '';
        if (fPlace)  fPlace.value  = item.place   || '';
        if (fDept)   fDept.value   = item.department || '';
        if (fRemark) fRemark.value = item.remark  || '';

        // Changer le titre de la modale
        const title = addEventModal.querySelector('h3');
        if (title) title.textContent = "Modifier un événement";

        // Ouvrir la modale (réutilise tes fonctions si tu en as ; sinon ouverture simple)
        addEventModal.style.display = 'block';
        setTimeout(() => addEventModal.classList.add('show'), 10);

        // Fermer le drawer
        if (window.closeEventDrawer) window.closeEventDrawer();

    } catch(e){
        console.error(e);
        showDynamicFlashMessage('Erreur à l’ouverture de l’édition', 'error');
    }
    });

    // === Bouton DUPLIQUER ===
    document.getElementById('drawer-duplicate')?.addEventListener('click', async () => {
    try {
        const drawer = document.getElementById('event-drawer');
        const item = JSON.parse(drawer.dataset.itemRaw || '{}');

        if (!item?._id || !item?.date) {
        showDynamicFlashMessage("Événement incomplet (id/date manquant).", "error");
        return;
        }

        // Demander la date cible (par défaut, même jour)
        const defaultDate = item.date;
        const target = window.prompt("Dupliquer à la date (YYYY-MM-DD) :", defaultDate);
        if (target === null) return; // annulé

        const re = /^\d{4}-\d{2}-\d{2}$/;
        if (!re.test(target)) {
        showDynamicFlashMessage("Format de date invalide (YYYY-MM-DD).", "error");
        return;
        }

        const { event, year } = getCurrentEventYear();
        if (!event || !year) {
        showDynamicFlashMessage("Sélectionnez un événement et une année.", "error");
        return;
        }

        const payload = {
        event, year,
        date: item.date,
        _id: item._id,
        target_date: target
        };

        const res = await apiPost('/duplicate_timetable_event', payload);
        if (res?.success) {
        showDynamicFlashMessage("Événement dupliqué.", "success");
        // rafraîchir timeline
        const eventList = document.getElementById("event-list");
        if (eventList) eventList.innerHTML = "";
        if (window.fetchTimetable) window.fetchTimetable();
        // On peut laisser le drawer ouvert, mais on met à jour son contenu si on a dupliqué sur même jour
        } else {
        showDynamicFlashMessage(res?.message || "Erreur lors de la duplication.", "error");
        }
    } catch (e) {
        console.error(e);
        showDynamicFlashMessage("Erreur inattendue lors de la duplication.", "error");
    }
    });

    // === Bouton SUPPRIMER ===
    document.getElementById('drawer-delete')?.addEventListener('click', async () => {
    try {
        const drawer = document.getElementById('event-drawer');
        const item = JSON.parse(drawer.dataset.itemRaw || '{}');

        if (!item?._id || !item?.date) {
        showDynamicFlashMessage("Événement incomplet (id/date manquant).", "error");
        return;
        }

        const ok = window.confirm("Confirmer la suppression de cet événement ?");
        if (!ok) return;

        const { event, year } = getCurrentEventYear();
        if (!event || !year) {
        showDynamicFlashMessage("Sélectionnez un événement et une année.", "error");
        return;
        }

        const payload = {
        event, year,
        date: item.date,
        _id: item._id
        };

        const res = await apiPost('/delete_timetable_event', payload);
        if (res?.success) {
        showDynamicFlashMessage("Événement supprimé.", "success");
        // fermer le drawer et rafraîchir la timeline
        if (window.closeEventDrawer) window.closeEventDrawer();
        const eventList = document.getElementById("event-list");
        if (eventList) eventList.innerHTML = "";
        if (window.fetchTimetable) window.fetchTimetable();
        } else {
        showDynamicFlashMessage(res?.message || "Erreur lors de la suppression.", "error");
        }
    } catch (e) {
        console.error(e);
        showDynamicFlashMessage("Erreur inattendue lors de la suppression.", "error");
    }
    });

})();

/////////////////////////////////////////////////////////////////////////////////////////////////////
// ALERTES (flash)
/////////////////////////////////////////////////////////////////////////////////////////////////////

function showDynamicFlashMessage(message, category = 'success', duration = 3000) {
    const flashContainer = document.getElementById('flash-container');
    if (!flashContainer) {
        console.error("Conteneur de messages flash introuvable !");
        return;
    }
    const flashMessage = document.createElement('div');
    flashMessage.textContent = message;
    flashMessage.className = `flash-popup ${category}`;
    flashContainer.appendChild(flashMessage);

    setTimeout(() => {
        flashMessage.style.opacity = '0';
        setTimeout(() => flashMessage.remove(), 500);
    }, duration);
}

/////////////////////////////////////////////////////////////////////////////////////////////////////
// NAVBAR (safe listeners)
/////////////////////////////////////////////////////////////////////////////////////////////////////

on("stats-page-button", "click", function(){
    if (!window.selectedEvent || !window.selectedYear) {
        showDynamicFlashMessage("Veuillez sélectionner un événement et une année", "error");
        return;
    }
    const url = "/general_stat?event=" + encodeURIComponent(window.selectedEvent) + "&year=" + encodeURIComponent(window.selectedYear);
    window.open(url, "_blank");
});

on("parkings-page-button", "click", function(){
    if (!window.selectedEvent || !window.selectedYear) {
        showDynamicFlashMessage("Veuillez sélectionner un événement et une année", "error");
        return;
    }
    const url = "/terrains?event=" + encodeURIComponent(window.selectedEvent) + "&year=" + encodeURIComponent(window.selectedYear);
    window.open(url, "_blank");
});

on("doors-page-button", "click", function(){
    if (!window.selectedEvent || !window.selectedYear) {
        showDynamicFlashMessage("Veuillez sélectionner un événement et une année", "error");
        return;
    }
    const url = "/doors?event=" + encodeURIComponent(window.selectedEvent) + "&year=" + encodeURIComponent(window.selectedYear);
    window.open(url, "_blank");
});