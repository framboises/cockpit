// meteo.js
// Fonction pour récupérer les prévisions pour les 6 prochaines heures et les afficher dans la navbar
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

