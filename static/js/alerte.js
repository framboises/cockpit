// === Compteurs & stockage local (plus de carte) ===
let accidentCount = 0;
let jamCount = 0;
let currentAlertIndex = 0;
let accidentAlerts = [];
let jamAlerts = [];

// Utils DOM safe
function $(id){ return document.getElementById(id); }
function on(elOrId, evt, fn){
  const el = typeof elOrId === "string" ? $(elOrId) : elOrId;
  if (el) el.addEventListener(evt, fn, false);
}

// Format date FR
function formatTimestamp(pubMillis) {
    const date = new Date(pubMillis);
    return date.toLocaleString('fr-FR');
}

// Tri décroissant par date
function sortAlertsByTime(alerts) {
    return alerts.sort((a, b) => b.pubMillis - a.pubMillis);
}

// Traductions Waze
function getSubtypeFr(subtype) {
    const subtypeMap = {
        'ACCIDENT_MINOR': 'Accident mineur',
        'ACCIDENT_MAJOR': 'Accident majeur',
        'NO_SUBTYPE': 'Sans sous-type',
        'HAZARD_ON_ROAD_CONSTRUCTION': 'Travaux',
        'HAZARD_ON_ROAD_TRAFFIC_LIGHT_FAULT': 'Feu de circulation en panne',
        'HAZARD_ON_ROAD_POT_HOLE': 'Nid-de-poule',
        'ROAD_CLOSED_EVENT': 'Route fermée',
        'JAM_LIGHT_TRAFFIC': 'Circulation légère',
        'JAM_MODERATE_TRAFFIC': 'Circulation modérée',
        'JAM_HEAVY_TRAFFIC': 'Circulation dense',
        'JAM_STAND_STILL_TRAFFIC': 'Circulation arrêtée'
    };
    return subtypeMap[subtype] || subtype;
}

// MAJ visuelle des compteurs (widget)
function updateAccidentCounter(count) {
    const num = $('accident-number');
    if (num) num.textContent = count;
}
function updateJamCounter(count) {
    const num = $('jam-number');
    if (num) num.textContent = count;
}

// Gestion de la couleur
function setTrafficColor(spanId, count) {
    const span = $(spanId);
    if (!span) return;
    const container = span.closest('.traffic-counter');
    if (!container) return;

    const labelEl = container.querySelector('.label');
    const counterEl = container.querySelector('.counter');

    const isAlert = count > 0;
    labelEl?.classList.toggle('red', isAlert);
    counterEl?.classList.toggle('red', isAlert);
}

// Rendu optionnel d'un tableau minimal dans #traffic-table-container (widget)
function renderTrafficList(alerts) {
    const box = $('traffic-table-container');
    if (!box) return;

    if (!alerts || !alerts.length) {
        box.innerHTML = '<div style="opacity:.8;font-size:12px;">Aucune alerte trafic</div>';
        return;
    }

    const rows = alerts.slice(0, 30).map(a => {
        const type = a.type;
        const when = formatTimestamp(a.pubMillis);
        const street = a.street || '—';
        const subtype = getSubtypeFr(a.subtype || 'NO_SUBTYPE');
        return `<tr><td>${type}</td><td>${subtype}</td><td>${street}</td><td>${when}</td></tr>`;
    }).join('');

    box.innerHTML = `
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead>
                <tr style="background:#ff007f;color:#fff;">
                    <th style="text-align:left;padding:6px;">Type</th>
                    <th style="text-align:left;padding:6px;">Sous-type</th>
                    <th style="text-align:left;padding:6px;">Rue</th>
                    <th style="text-align:left;padding:6px;">Date</th>
                </tr>
            </thead>
            <tbody>
                ${rows}
            </tbody>
        </table>`;
}

// Récupération + agrégation (sans carte)
function updateAlerts() {
    fetch('/alerts')
        .then(r => r.json())
        .then(data => {
            accidentCount = 0;
            jamCount = 0;
            accidentAlerts = [];
            jamAlerts = [];

            (data || []).forEach(alert => {
                if (alert.type === 'ACCIDENT') {
                    accidentCount++;
                    accidentAlerts.push(alert);
                } else if (alert.type === 'JAM') {
                    jamCount++;
                    jamAlerts.push(alert);
                }
            });

            accidentAlerts = sortAlertsByTime(accidentAlerts);
            jamAlerts = sortAlertsByTime(jamAlerts);

            // MAJ compteurs
            updateAccidentCounter(accidentCount);
            updateJamCounter(jamCount);

            // ➕ Ajoute ces 2 lignes :
            setTrafficColor('accident-number', accidentCount);
            setTrafficColor('jam-number',      jamCount);

            // Rendu compact (accidents + jams mélangés triés)
            const merged = sortAlertsByTime([...(data || [])]);
            renderTrafficList(merged);
        })
        .catch(err => {
            console.error('Erreur lors de la récupération des alertes:', err);
        });
}

// Clicks (on clique sur les NUMÉROS eux-mêmes, présents dans le widget)
on('accident-number', 'click', function () {
    if (!accidentAlerts.length) return;
    currentAlertIndex = (currentAlertIndex + 1) % accidentAlerts.length;
    // Ici on pourrait ouvrir un détail ou surligner la ligne dans le tableau.
    // Pas de map => noop visuel pour l’instant.
});
on('jam-number', 'click', function () {
    if (!jamAlerts.length) return;
    currentAlertIndex = (currentAlertIndex + 1) % jamAlerts.length;
    // Idem : noop sans carte.
});

// Boucle de rafraîchissement
document.addEventListener('DOMContentLoaded', function() {
    updateAlerts();
    setInterval(updateAlerts, 60000);
});
