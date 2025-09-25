// === Trafic sans Leaflet ===
// On ne dessine plus de polylines, on alimente le widget (table) et les compteurs.

let trafficEnabled = false;

// Utils DOM
function $(id){ return document.getElementById(id); }
function on(elOrId, evt, fn){
  const el = typeof elOrId === "string" ? $(elOrId) : elOrId;
  if (el) el.addEventListener(evt, fn, false);
}

// Helpers
function formatTime(seconds) {
    if (seconds == null || isNaN(seconds)) return '—';
    seconds = Math.max(0, seconds|0);
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}m ${s}s`;
}
function getColor(level) {
    switch (level) {
        case 1: return 'darkgreen';
        case 2: return 'yellow';
        case 3: return 'orange';
        case 4: return 'red';
        case 5: return 'darkred';
        default: return 'green';
    }
}
function calculateDelay(time, historicTime) {
    if (time == null || historicTime == null) return 0;
    return (time|0) - (historicTime|0);
}

// Rendu d’un tableau minimal des routes (si tu veux garder une lecture)
function renderRoutesTable(routes) {
    const box = $('traffic-table-container');
    if (!box) return;

    if (!routes || !routes.length) {
        box.innerHTML = '<div style="opacity:.8;font-size:12px;">Pas de données trafic</div>';
        return;
    }

    const rows = routes.map(r => {
        const delay = calculateDelay(r.time, r.historicTime);
        const color = getColor(r.jamLevel);
        return `
        <tr>
          <td style="padding:6px;">${r.name || '—'}</td>
          <td style="padding:6px;">${formatTime(r.time)} / ${formatTime(r.historicTime)}</td>
          <td style="padding:6px;font-weight:700;color:${color};">${formatTime(delay)}</td>
          <td style="padding:6px;">${r.jamLevel ?? '—'}</td>
        </tr>`;
    }).join('');

    box.innerHTML = `
      <table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead>
          <tr style="background:#ff007f;color:#fff;">
            <th style="text-align:left;padding:6px;">Itinéraire</th>
            <th style="text-align:left;padding:6px;">Actuel / Moyen</th>
            <th style="text-align:left;padding:6px;">Retard</th>
            <th style="text-align:left;padding:6px;">Niveau</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
}

// Toggle du switch (ne fait qu’afficher/masquer le tableau)
on('trafficToggle', 'change', function (e) {
    trafficEnabled = !!e.target.checked;
    const box = $('traffic-table-container');
    if (box) box.style.display = trafficEnabled ? 'block' : 'none';
    if (trafficEnabled) {
        updateTrafficData();
    }
});

// Récupère les données et alimente le tableau
function updateTrafficData() {
    if (!trafficEnabled) return;
    fetch('/trafic/data')
        .then(r => r.json())
        .then(data => {
            const routes = Array.isArray(data?.routes) ? data.routes.slice() : [];
            // Tri par retard décroissant
            routes.sort((a, b) => calculateDelay(b.time, b.historicTime) - calculateDelay(a.time, a.historicTime));
            renderRoutesTable(routes);
        })
        .catch(err => console.error('Erreur lors de la récupération des données de trafic:', err));
}

// === TEMPS D'ATTENTE PARKINGS/CAMPINGS -> maintenant dans le widget gauche ===
function updateParkingIndicators() {
    const container = $('parking-indicators-list'); // <-- NOUVEL ID
    if (!container) return;

    fetch('/trafic/waiting_data_structured')
        .then(response => response.json())
        .then(data => {
            container.innerHTML = "";

            const terrains = Array.isArray(data?.terrains) ? data.terrains : [];
            terrains.forEach(item => {
                const waiting_time = Math.max(0, (item.currentTime|0) - (item.historicTime|0));
                const div = document.createElement('div');
                div.className = 'parking-row';

                // état (open/busy/closed) calculé depuis severity si tu veux
                let stateClass = 'state-open';
                let stateText = 'open';
                if (item.severity === 2) { stateClass = 'state-busy'; stateText = 'busy'; }
                if (item.severity === 3) { stateClass = 'state-closed'; stateText = 'closed'; }

                div.innerHTML = `
                    <div class="name">${item.terrain || '—'}</div>
                    <div class="state-pill ${stateClass}">
                        ${stateText} · ${formatTime(waiting_time)}
                    </div>
                `;
                container.appendChild(div);
            });
        })
        .catch(error => console.error('Erreur lors de la récupération des données de trafic :', error));
}

document.addEventListener('DOMContentLoaded', function() {
    // Par défaut, on masque le tableau tant que le switch n’est pas ON
    const box = $('traffic-table-container');
    if (box) box.style.display = 'none';

    updateParkingIndicators();
    setInterval(updateParkingIndicators, 30000);

    // Si le toggle est déjà coché (état mémorisé par le navigateur)
    const toggle = $('trafficToggle');
    if (toggle && toggle.checked) {
        trafficEnabled = true;
        if (box) box.style.display = 'block';
        updateTrafficData();
    }

    // Rafraîchir les routes si activé
    setInterval(() => { if (trafficEnabled) updateTrafficData(); }, 60000);
});
