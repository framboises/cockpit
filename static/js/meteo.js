// meteo.js

// ==========================================================================
// WIDGET METEO OPERATIONNEL (sidebar droite)
// ==========================================================================

var _meteoPanelChart = null;
var _meteoYoyChart = null;
var _meteoPreviousView = "timeline";

function fetchMeteoWidgetSummary() {
  if (!window.isBlockAllowed("widget-right-1")) return;
  fetch('/meteo_widget_summary')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) return;
      renderMeteoWidget(data);
    })
    .catch(function(err) { console.error("Erreur widget meteo:", err); });
}

function renderMeteoWidget(data) {
  // Jauge de risque
  var gauge = document.getElementById('meteo-risk-gauge');
  if (gauge) {
    gauge.setAttribute('data-level', data.risk_level);
    var icon = gauge.querySelector('.meteo-risk-icon');
    if (icon) {
      if (data.risk_level === 'green') icon.textContent = 'shield';
      else if (data.risk_level === 'orange') icon.textContent = 'warning';
      else icon.textContent = 'crisis_alert';
    }
    var label = document.getElementById('meteo-risk-label');
    if (label) label.textContent = data.risk_label;
  }

  // Snapshot conditions actuelles
  var c = data.current;
  var snapTemp = document.getElementById('meteo-snap-temp');
  var snapWind = document.getElementById('meteo-snap-wind');
  var snapRain = document.getElementById('meteo-snap-rain');
  if (snapTemp) snapTemp.textContent = c.temp + 'C';
  if (snapWind) snapWind.textContent = c.gust + ' km/h';
  if (snapRain) snapRain.textContent = c.rain + ' mm';

  // Alertes meteo -> injectees dans le widget Alertes (widget-right-3)
  var container = document.getElementById('widget-right-3-body');
  if (container) {
    // Supprimer les anciennes alertes meteo
    var oldMeteo = container.querySelectorAll('.alert-history-entry[data-type="meteo"]');
    oldMeteo.forEach(function(el) { el.remove(); });

    if (data.alerts.length > 0 && typeof _renderAlertEntry === 'function') {
      // Retirer le placeholder si present
      var placeholder = container.querySelector('.widget-placeholder');
      if (placeholder) placeholder.remove();

      var now = new Date();
      var timeStr = String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');

      data.alerts.forEach(function(a) {
        var entry = _renderAlertEntry(container, 'meteo', a.icon, 'Meteo', '', a.message, null, timeStr, null);
        container.insertBefore(entry, container.firstChild);
      });
    }
  }
}

// Init widget au chargement
document.addEventListener('DOMContentLoaded', function() {
  fetchMeteoWidgetSummary();

  var expandBtn = document.getElementById('meteo-expand-btn');
  if (expandBtn) {
    expandBtn.addEventListener('click', function() {
      var panel = document.getElementById('meteo-panel');
      if (panel && panel.style.display !== 'none') {
        collapseMeteoPanel();
      } else {
        expandMeteoPanel();
      }
    });
  }

  var closeBtn = document.getElementById('meteo-panel-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', function() {
      collapseMeteoPanel();
    });
  }
});

// Refresh widget toutes les 5 minutes
setInterval(fetchMeteoWidgetSummary, 5 * 60 * 1000);

// ==========================================================================
// PANEL ANALYSE METEO (zone centrale)
// ==========================================================================

function expandMeteoPanel(targetDate) {
  var timeline = document.getElementById('timeline-main');
  var mapMain = document.getElementById('map-main');
  var panel = document.getElementById('meteo-panel');
  if (!panel) return;

  // Sauvegarder la vue precedente
  if (window.CockpitMapView) {
    _meteoPreviousView = window.CockpitMapView.currentView();
  }

  // Fermer le panel pcorg s'il est ouvert
  var pcorgPanel = document.getElementById('pcorg-expanded-panel');
  if (pcorgPanel && pcorgPanel.style.display !== 'none') {
    pcorgPanel.style.display = 'none';
    var pcorgBtn = document.getElementById('pcorg-expand-btn');
    if (pcorgBtn) pcorgBtn.querySelector('.material-symbols-outlined').textContent = 'open_in_full';
  }

  if (timeline) timeline.style.display = 'none';
  if (mapMain) mapMain.style.display = 'none';
  panel.style.display = 'flex';

  var expandBtn = document.getElementById('meteo-expand-btn');
  if (expandBtn) expandBtn.querySelector('.material-symbols-outlined').textContent = 'close_fullscreen';

  var today = new Date();
  var date = targetDate || today.toISOString().split('T')[0];
  buildMeteoPanelTabs(date);
  loadMeteoPanelData(date);
}

function collapseMeteoPanel() {
  var panel = document.getElementById('meteo-panel');
  if (panel) panel.style.display = 'none';

  // Detruire les charts pour eviter les fuites memoire
  if (_meteoPanelChart) { _meteoPanelChart.destroy(); _meteoPanelChart = null; }
  if (_meteoYoyChart) { _meteoYoyChart.destroy(); _meteoYoyChart = null; }

  // Restaurer la vue precedente (timeline ou carte)
  var timeline = document.getElementById('timeline-main');
  var mapMain = document.getElementById('map-main');

  var expandBtn = document.getElementById('meteo-expand-btn');
  if (expandBtn) expandBtn.querySelector('.material-symbols-outlined').textContent = 'open_in_full';

  if (_meteoPreviousView === 'map') {
    if (timeline) timeline.style.display = 'none';
    if (mapMain) mapMain.style.display = 'block';
  } else {
    if (timeline) timeline.style.display = '';
    if (mapMain) mapMain.style.display = 'none';
  }
}

function buildMeteoPanelTabs(activeDate) {
  var tabsDiv = document.getElementById('meteo-panel-tabs');
  if (!tabsDiv) return;
  tabsDiv.innerHTML = '';

  var today = new Date();
  for (var i = 0; i < 4; i++) {
    var d = new Date(today);
    d.setDate(d.getDate() + i);
    var dateStr = d.toISOString().split('T')[0];
    var label = i === 0 ? "Aujourd'hui" : d.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric' });

    var btn = document.createElement('button');
    btn.className = 'meteo-panel-tab' + (dateStr === activeDate ? ' active' : '');
    btn.textContent = label;
    btn.setAttribute('data-date', dateStr);
    btn.addEventListener('click', function() {
      var dd = this.getAttribute('data-date');
      tabsDiv.querySelectorAll('.meteo-panel-tab').forEach(function(t) { t.classList.remove('active'); });
      this.classList.add('active');
      loadMeteoPanelData(dd);
    });
    tabsDiv.appendChild(btn);
  }
}

function loadMeteoPanelData(date) {
  // Charger previsions horaires + historique en parallele
  var fetchPrev = fetch('/meteo_previsions/' + encodeURIComponent(date)).then(function(r) { return r.json(); });
  var fetchHist = fetch('/historique_meteo/' + encodeURIComponent(date)).then(function(r) { return r.json(); });

  Promise.all([fetchPrev, fetchHist])
    .then(function(results) {
      var dayData = results[0];
      var histData = results[1];

      if (!dayData.error) {
        renderMeteoPanelChart(dayData);
      }
      renderMeteoPanelHistory(histData);
      renderMeteoYoyChart(histData);
    })
    .catch(function(err) {
      console.error('Erreur chargement panel meteo:', err);
    });
}

function renderMeteoPanelChart(dayData) {
  if (_meteoPanelChart) { _meteoPanelChart.destroy(); _meteoPanelChart = null; }

  var canvas = document.getElementById('meteo-panel-chart');
  if (!canvas || !dayData.Heures) return;
  var ctx = canvas.getContext('2d');

  var labels = dayData.Heures.map(function(h) { return h.Heure; });
  var temps = dayData.Heures.map(function(h) {
    return parseFloat(h['Temperature (°C)'] || h['Temp\u00e9rature (\u00b0C)'] || 0);
  });
  var rain = dayData.Heures.map(function(h) {
    return parseFloat(h['Pluviometrie (mm)'] || h['Pluviom\u00e9trie (mm)'] || 0);
  });
  var gusts = dayData.Heures.map(function(h) {
    return parseFloat(h['Vent rafale (km/h)'] || 0);
  });

  _meteoPanelChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        {
          type: 'line',
          label: 'Temperature (C)',
          data: temps,
          borderColor: '#ef5350',
          backgroundColor: 'rgba(239,83,80,0.1)',
          borderWidth: 2,
          pointRadius: 3,
          fill: true,
          yAxisID: 'y1',
          tension: 0.3,
          order: 1
        },
        {
          type: 'bar',
          label: 'Pluie (mm)',
          data: rain,
          backgroundColor: 'rgba(66,165,245,0.5)',
          borderColor: '#42a5f5',
          borderWidth: 1,
          yAxisID: 'y2',
          order: 2
        },
        {
          type: 'line',
          label: 'Rafales (km/h)',
          data: gusts,
          borderColor: '#78909c',
          borderWidth: 1.5,
          borderDash: [5, 3],
          pointRadius: 0,
          fill: false,
          yAxisID: 'y1',
          tension: 0.3,
          order: 0
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: {
          label: function(tooltipCtx) {
            var lbl = tooltipCtx.dataset.label || '';
            var val = tooltipCtx.parsed.y;
            if (lbl.indexOf('Temperature') >= 0) return lbl + ': ' + val + 'C';
            if (lbl.indexOf('Pluie') >= 0) return lbl + ': ' + val + ' mm';
            if (lbl.indexOf('Rafales') >= 0) return lbl + ': ' + val + ' km/h';
            return lbl + ': ' + val;
          }
        }}
      },
      scales: {
        x: {
          ticks: { maxRotation: 45, font: { size: 10 } },
          grid: { display: false }
        },
        y1: {
          type: 'linear',
          position: 'left',
          title: { display: true, text: 'Temperature (C) / Rafales (km/h)', font: { size: 10 } },
          grid: { color: 'rgba(255,255,255,0.05)' }
        },
        y2: {
          type: 'linear',
          position: 'right',
          title: { display: true, text: 'Pluie (mm)', font: { size: 10 } },
          grid: { display: false },
          beginAtZero: true
        }
      }
    }
  });
}

function renderMeteoPanelHistory(histData) {
  var container = document.getElementById('meteo-panel-history');
  if (!container) return;
  container.innerHTML = '';

  var table = document.createElement('table');
  table.className = 'meteo-history-table';

  // Thead
  var thead = document.createElement('thead');
  var headerRow = document.createElement('tr');
  ['Annee', 'Precip. mois (mm)', 'Min mois', 'Max mois', 'Moy. mois', 'Jour', 'Precip. jour'].forEach(function(txt) {
    var th = document.createElement('th');
    th.textContent = txt;
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  // Tbody
  var tbody = document.createElement('tbody');
  var currentYear = new Date().getFullYear();

  for (var year in histData) {
    var d = histData[year];
    var tr = document.createElement('tr');

    var tdYear = document.createElement('td');
    tdYear.textContent = year;
    tr.appendChild(tdYear);

    var precip = d['Precipitations Totales Mois (mm)'] || d['Pr\u00e9cipitations Totales Mois (mm)'] || '-';
    var tdPrecip = document.createElement('td');
    tdPrecip.textContent = precip + ' mm';
    tr.appendChild(tdPrecip);

    var tMin = d['Temperature Min Mois (°C)'] || d['Temp\u00e9rature Min Mois (\u00b0C)'] || '-';
    var tdMin = document.createElement('td');
    tdMin.textContent = tMin + 'C';
    tr.appendChild(tdMin);

    var tMax = d['Temperature Max Mois (°C)'] || d['Temp\u00e9rature Max Mois (\u00b0C)'] || '-';
    var tdMax = document.createElement('td');
    tdMax.textContent = tMax + 'C';
    tr.appendChild(tdMax);

    var tMoy = d['Temperature Moyenne Mois (°C)'] || d['Temp\u00e9rature Moyenne Mois (\u00b0C)'] || '-';
    var tdMoy = document.createElement('td');
    tdMoy.textContent = tMoy + 'C';
    tr.appendChild(tdMoy);

    if (d.message) {
      var tdMsg = document.createElement('td');
      tdMsg.setAttribute('colspan', '2');
      tdMsg.textContent = d.message;
      tr.appendChild(tdMsg);
    } else if (parseInt(year) === currentYear || !d['Temperature Jour (°C)']) {
      var tdJ1 = document.createElement('td');
      tdJ1.textContent = '-';
      tr.appendChild(tdJ1);
      var tdJ2 = document.createElement('td');
      tdJ2.textContent = '-';
      tr.appendChild(tdJ2);
    } else {
      var tj = d['Temperature Jour (°C)'] || d['Temp\u00e9rature Jour (\u00b0C)'];
      var pj = d['Precipitations Jour (mm)'] || d['Pr\u00e9cipitations Jour (mm)'];
      var tdJour = document.createElement('td');
      tdJour.textContent = tj ? tj.max + 'C / ' + tj.min + 'C' : '-';
      tr.appendChild(tdJour);
      var tdPJ = document.createElement('td');
      tdPJ.textContent = pj != null ? pj + ' mm' : '-';
      tr.appendChild(tdPJ);
    }

    tbody.appendChild(tr);
  }

  table.appendChild(tbody);
  container.appendChild(table);
}

function renderMeteoYoyChart(histData) {
  if (_meteoYoyChart) { _meteoYoyChart.destroy(); _meteoYoyChart = null; }

  var canvas = document.getElementById('meteo-panel-yoy-chart');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');

  var years = [];
  var precips = [];
  var tempMoys = [];

  for (var year in histData) {
    var d = histData[year];
    years.push(year);
    var p = parseFloat(d['Precipitations Totales Mois (mm)'] || d['Pr\u00e9cipitations Totales Mois (mm)'] || 0);
    var t = parseFloat(d['Temperature Moyenne Mois (°C)'] || d['Temp\u00e9rature Moyenne Mois (\u00b0C)'] || 0);
    precips.push(p);
    tempMoys.push(t);
  }

  _meteoYoyChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: years,
      datasets: [
        {
          label: 'Precip. mois (mm)',
          data: precips,
          backgroundColor: 'rgba(66,165,245,0.6)',
          borderColor: '#42a5f5',
          borderWidth: 1,
          yAxisID: 'y1'
        },
        {
          type: 'line',
          label: 'Temp. moyenne (C)',
          data: tempMoys,
          borderColor: '#ef5350',
          backgroundColor: 'rgba(239,83,80,0.1)',
          borderWidth: 2,
          pointRadius: 4,
          fill: false,
          yAxisID: 'y2'
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top', labels: { boxWidth: 12, font: { size: 11 } } }
      },
      scales: {
        x: { grid: { display: false } },
        y1: {
          type: 'linear',
          position: 'left',
          title: { display: true, text: 'Precip. (mm)', font: { size: 10 } },
          beginAtZero: true,
          grid: { color: 'rgba(255,255,255,0.05)' }
        },
        y2: {
          type: 'linear',
          position: 'right',
          title: { display: true, text: 'Temp. (C)', font: { size: 10 } },
          grid: { display: false }
        }
      }
    }
  });
}

// Expose pour map_view.js
window.MeteoPanel = {
  expand: expandMeteoPanel,
  collapse: collapseMeteoPanel,
  isOpen: function() {
    var p = document.getElementById('meteo-panel');
    return p && p.style.display !== 'none';
  }
};

// ==========================================================================
// HEADER METEO (6h previsions)
// ==========================================================================

// Fonction pour recuperer les previsions pour les 6 prochaines heures et les afficher dans la navbar
function fetchMeteoPrevisions6h() {
    if (!window.isBlockAllowed("meteo-previsions")) return;
    fetch('/meteo_previsions_6h')
        .then(response => response.json())
        .then(data => {
            let meteoPrevisionsDiv = document.getElementById('meteo-previsions');
            meteoPrevisionsDiv.innerHTML = ''; // Effacer tout contenu précédent

            if (data.length === 0) {
                meteoPrevisionsDiv.innerHTML = '<p>Aucune donnée météo disponible.</p>';
                return;
            }

            data.forEach(prevision => {
                let previsionElement = document.createElement('div');
                previsionElement.className = 'meteo-item';

                // Créer un bloc pour chaque prévision horaire avec la date affichée
                previsionElement.innerHTML = `
                    <div class="meteo-date">${prevision.Date}</div>
                    <div class="meteo-hour-temp">
                        ${prevision.Heure} - ${prevision['Température (°C)']}°
                    </div>
                    <div class="meteo-rain">
                        🌧️ ${prevision['Pluviométrie (mm)']} mm
                    </div>
                    <div class="meteo-wind">
                        🌪️ ${prevision['Vent rafale (km/h)']} km/h
                    </div>
                `;

                // Ajouter l'événement click pour ouvrir la modale pour la date correspondante
                previsionElement.addEventListener('click', () => {
                    openMeteoModal(prevision.Date);
                });

                meteoPrevisionsDiv.appendChild(previsionElement);
            });
        })
        .catch(error => console.error('Erreur lors de la récupération des prévisions météo :', error));
}

// Appel initial pour charger les données météo dès le chargement de la page
document.addEventListener('DOMContentLoaded', fetchMeteoPrevisions6h);

setTimeout(fetchSunTimes, 50); // 🌞 Ajouter le soleil après un court délai

// Ouvre la modale Météo (pilotage par classes uniquement)
function openMeteoModal(date) {
  if (!window.isBlockAllowed("widget-right-1")) return;
  console.log("Ouverture de la modale meteo pour le :", date);

  const modal        = document.getElementById('meteoModal');
  const overlay      = document.getElementById('modalOverlay');
  const modalContent = document.getElementById('meteo-details');

  if (!modal || !overlay || !modalContent) {
    console.warn('[meteo] Élément introuvable (meteoModal / modalOverlay / meteo-details)');
    return;
  }

  // État initial (contenu de chargement)
  modalContent.innerHTML = `<div class="modal-header">Chargement...</div>`;

  // Afficher overlay + modale par classes (pas de style inline)
  overlay.classList.add('show');
  modal.classList.add('show');
  modal.setAttribute('aria-hidden', 'false');

  // (Optionnel) bloquer le scroll de la page tant qu’une modale est ouverte
  document.documentElement.style.overflow = 'hidden';
  document.body.style.overflow = 'hidden';

  // 1) Prévisions journalières
  fetch(`/meteo_previsions/${date}`)
    .then(response => response.json())
    .then(day => {
      if (day.error) {
        console.error('❌ Erreur météo :', day.error);
        modalContent.innerHTML = `
          <div class="modal-header">
            <h3>Erreur de chargement des prévisions</h3>
            <span class="close" onclick="closeMeteoModal()">×</span>
          </div>`;
        return;
      }

      // 2) Historique pour la même date
      return fetch(`/historique_meteo/${date}`)
        .then(response => response.json())
        .then(historicalData => {
          // Structure de la modale
          modalContent.innerHTML = `
            <div class="modal-header">
              <h3>Prévisions météo du ${formatDateToFull(day.Date)}</h3>
              <span class="close" onclick="closeMeteoModal()">×</span>
            </div>
            <div class="modal-body">
              <div class="historical-info">
                <table id="historicalTable">
                  <thead>
                    <tr>
                      <th>Année</th>
                      <th><span class="icon">💧 Moy. mois</span></th>
                      <th><span class="icon">🌡️ Min mois</span></th>
                      <th><span class="icon">🔥 Max mois</span></th>
                      <th><span class="icon">🌡️ Moy mois</span></th>
                      <th><span class="icon">🌡️ Jour</span></th>
                      <th><span class="icon">💧 Jour</span></th>
                    </tr>
                  </thead>
                  <tbody></tbody>
                </table>
              </div>
              <div class="chart-container">
                <canvas id="meteoChart"></canvas>
              </div>
            </div>
          `;

          // Remplir le tableau historique
          const tbody = modalContent.querySelector('#historicalTable tbody');
          for (const year in historicalData) {
            const data = historicalData[year];
            const tr = document.createElement('tr');

            if (data.message) {
              tr.innerHTML = `
                <td>${year}</td>
                <td>${data['Précipitations Totales Mois (mm)']} mm</td>
                <td>${data['Température Min Mois (°C)']}°C</td>
                <td>${data['Température Max Mois (°C)']}°C</td>
                <td>${data['Température Moyenne Mois (°C)']}°C</td>
                <td colspan="2">${data.message}</td>
              `;
            } else {
              const jourColumns = (year == new Date().getFullYear() || !data['Température Jour (°C)'])
                ? `<td></td><td></td>`
                : `<td>${data['Température Jour (°C)'].max}°C / ${data['Température Jour (°C)'].min}°C</td>
                   <td>${data['Précipitations Jour (mm)']} mm</td>`;

              tr.innerHTML = `
                <td>${year}</td>
                <td>${data['Précipitations Totales Mois (mm)']} mm</td>
                <td>${data['Température Min Mois (°C)']}°C</td>
                <td>${data['Température Max Mois (°C)']}°C</td>
                <td>${data['Température Moyenne Mois (°C)']}°C</td>
                ${jourColumns}
              `;
            }

            tbody.appendChild(tr);
          }

          // Graphique
          const labels        = day.Heures.map(h => h.Heure);
          const temperatures  = day.Heures.map(h => parseFloat(h['Température (°C)']));
          const pluviometrie  = day.Heures.map(h => parseFloat(h['Pluviométrie (mm)']));
          const ctx = document.getElementById('meteoChart').getContext('2d');

          new Chart(ctx, {
            type: 'line',
            data: {
              labels,
              datasets: [
                { label: 'Température (°C)', data: temperatures, borderColor: 'red',  fill: false, yAxisID: 'y1' },
                { label: 'Pluviométrie (mm)', data: pluviometrie, borderColor: 'blue', fill: false, yAxisID: 'y2' }
              ]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              scales: {
                x: { ticks: { maxRotation: 90, minRotation: 45 } },
                y1: { type: 'linear', position: 'left',  title: { display: true, text: 'Température (°C)' }, grid: { color: 'rgba(255,99,132,.2)' } },
                y2: { type: 'linear', position: 'right', title: { display: true, text: 'Pluviométrie (mm)' }, grid: { display: false } }
              }
            }
          });
        })
        .catch(err => {
          console.error('❌ Erreur historique météo :', err);
          modalContent.innerHTML += `<div class="modal-body">Impossible de récupérer l'historique météo.</div>`;
        });
    })
    .catch(err => {
      console.error('❌ Erreur prévisions détaillées :', err);
      modalContent.innerHTML = `
        <div class="modal-header">
          <h3>Impossible de récupérer les prévisions météo</h3>
          <span class="close" onclick="closeMeteoModal()">×</span>
        </div>`;
    });
}

// Ajouter un écouteur pour fermer la modale en cliquant en dehors
document.getElementById('modalOverlay').addEventListener('click', function (event) {
    closeMeteoModal();
});

// Empêcher la fermeture si on clique à l'intérieur de la modale
document.getElementById('meteoModal').addEventListener('click', function (event) {
    event.stopPropagation(); // Bloque la propagation du clic pour éviter de fermer la modale
});

// Ferme la modale Météo (en respectant la cohabitation avec la modale Trafic)
function closeMeteoModal() {
  const modal   = document.getElementById('meteoModal');
  const overlay = document.getElementById('modalOverlay');

  if (!modal || !overlay) return;

  modal.classList.remove('show');
  modal.setAttribute('aria-hidden', 'true');

  // Ne retirer l’overlay que si aucune autre modale n’est ouverte
  const trafficOpen = document.getElementById('trafficMapModal')?.classList.contains('show');
  const addEventOpen = document.getElementById('addEventModal')?.classList.contains('show'); // si tu as d’autres modales
  if (!trafficOpen && !addEventOpen) {
    overlay.classList.remove('show');
    // Débloquer le scroll seulement quand plus aucune modale n’est visible
    document.documentElement.style.overflow = '';
    document.body.style.overflow = '';
  }
}

// Fonction pour formater la date en "Jour Mois Année"
function formatDateToFull(dateStr) {
    const date = new Date(dateStr);
    // Options pour formater la date
    const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
    // Formatter la date en français
    return date.toLocaleDateString('fr-FR', options);
}

document.addEventListener('DOMContentLoaded', function() {
    // Modifier le gestionnaire d'événement sur les jours météo
    document.querySelectorAll('.meteo-day').forEach(dayElement => {
        if (dayElement) {
            dayElement.onclick = function() {
                const date = this.getAttribute('data-date'); // Assure-toi d'avoir un attribut "data-date" dans tes éléments
                openMeteoModal(date);
            };
        }
    });
});

// Fermer la modale si on clique en dehors de celle-ci
window.onclick = function(event) {
    let modal = document.getElementById('meteoModal');
    if (event.target === modal) {
        modal.style.display = "none";
    }
}

// Fonction pour récupérer et afficher les horaires de lever et coucher du soleil
function fetchSunTimes() {
    fetch('/sun_times')
        .then(response => response.json())
        .then(data => {
            let leverTime = new Date(data.lever);
            let coucherTime = new Date(data.coucher);

            // Déterminer l'ordre d'affichage en fonction des horaires
            // Par défaut, on affiche le lever (☀️) puis le coucher (🌙)
            let firstIcon = "☀️", firstTime = leverTime;
            let secondIcon = "🌙", secondTime = coucherTime;
            
            // Si le lever arrive après le coucher (cas de nuit par exemple),
            // alors on inverse l'ordre pour afficher d'abord l'événement le plus proche.
            if (leverTime > coucherTime) {
                firstIcon = "🌙";
                firstTime = coucherTime;
                secondIcon = "☀️";
                secondTime = leverTime;
            }

            let meteoPrevisionsDiv = document.getElementById('meteo-previsions');

            // Vérifier si le bloc existe déjà pour éviter les doublons
            if (document.getElementById('sun-times-block')) return;

            // Créer le bloc unique pour l'affichage du lever et coucher du soleil,
            // en utilisant les icônes dynamiques.
            let sunTimesDiv = document.createElement('div');
            sunTimesDiv.id = 'sun-times-block';
            sunTimesDiv.className = 'meteo-day sun-block';
            sunTimesDiv.innerHTML = `
                <div class="sun-item"><span class="sun-icon">${firstIcon}</span> ${formatHour(firstTime)}</div>
                <div class="sun-item"><span class="sun-icon">${secondIcon}</span> ${formatHour(secondTime)}</div>
            `;

            // Insérer ce bloc en premier dans le conteneur météo
            meteoPrevisionsDiv.prepend(sunTimesDiv);
        })
        .catch(error => console.error('Erreur lors de la récupération des horaires du soleil :', error));
}

// Fonction pour formater l'heure en "HH:MM"
function formatHour(dateTimeStr) {
    let date = new Date(dateTimeStr);
    return date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

// Fonction pour récupérer les prévisions et injecter les événements
function fetchMeteoPrevisions() {
    fetch('/meteo_previsions')
        .then(response => response.json())
        .then(data => {
            let meteoPrevisionsDiv = document.getElementById('meteo-previsions');
            meteoPrevisionsDiv.innerHTML = ''; // Effacer tout contenu précédent

            data.slice(0, 3).forEach(day => {  // Ne prendre que les 3 premiers jours
                let dayElement = document.createElement('div');
                dayElement.className = 'meteo-day';
                dayElement.setAttribute('data-date', day.Date);  // Ajoute la date en tant qu'attribut

                // Extraire seulement le jour et le mois de la date
                let date = new Date(day.Date);
                let formattedDate = `${date.getDate().toString().padStart(2, '0')}/${(date.getMonth() + 1).toString().padStart(2, '0')}`;

                // Flèches de variation pour la température
                let variationTemp = day['Variation Température (°C)'];
                let tempArrow = variationTemp > 0 ? 'arrow_upward' : variationTemp < 0 ? 'arrow_downward' : 'arrow_back';

                // Flèches de variation pour la pluie
                let variationPluie = day['Variation Pluviométrie (mm)'];
                let pluieArrow = variationPluie > 0 ? 'arrow_upward' : variationPluie < 0 ? 'arrow_downward' : 'arrow_back';

                // Création du bloc HTML avec le min/max des températures
                dayElement.innerHTML = `
                    <div class="meteo-date">${formattedDate}</div>
                    <div class="meteo-item">
                        <span class="meteo-icon">🌡️</span> 
                        ${day['Température Min (°C)']}° / ${day['Température Max (°C)']}°
                        <span class="material-symbols-outlined">${tempArrow}</span>
                    </div>
                    <div class="meteo-item">
                        <span class="meteo-icon">🌧️</span> 
                        ${day['Somme Pluviométrie (mm)']} mm
                        <span class="material-symbols-outlined">${pluieArrow}</span>
                    </div>
                `;

                meteoPrevisionsDiv.appendChild(dayElement);

                // Ajouter l'événement onclick à chaque jour
                dayElement.onclick = function() {
                    openMeteoModal(day.Date);
                };
            });
        })
        .catch(error => console.error('Erreur lors de la récupération des prévisions météo :', error));
}

