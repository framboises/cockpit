// ==============================
// alerte.js — modal OSM (1 alerte à la fois) + navigation — ACCIDENT & JAM
// ==============================

let accidentCount = 0;
let jamCount = 0;
let currentAlertIndex = 0;
let accidentAlerts = [];
let jamAlerts = [];

// Modale & Leaflet
let trafficMap = null;
let trafficLayerGroup = null;
let trafficModalType = null;   // 'ACCIDENT' | 'JAM'

// Debug flag
const DEBUG_TRAFFIC = true;

// ---------- Utils DOM ----------
function $(id){ return document.getElementById(id); }

// Attache un listener si l'élément existe, sinon log
function onSafe(id, evt, fn){
  const el = $(id);
  if (!el) { if (DEBUG_TRAFFIC) console.log(`[traffic] element #${id} introuvable au bind`); return; }
  el.addEventListener(evt, fn, false);
}

// ---------- Formatage & tri ----------
function formatTimestamp(pubMillis) {
  const d = new Date(pubMillis);
  return isNaN(d.getTime()) ? '—' : d.toLocaleString('fr-FR');
}
function sortAlertsByTime(alerts) {
  return (alerts || []).sort((a, b) => (b.pubMillis || 0) - (a.pubMillis || 0));
}

// ---------- Traductions ----------
function getSubtypeFr(subtype) {
  const map = {
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
  return map[subtype] || subtype || '—';
}

// ---------- Couleurs widget ----------
function updateAccidentCounter(count) {
  const num = $('accident-number'); if (num) num.textContent = count;
}
function updateJamCounter(count) {
  const num = $('jam-number'); if (num) num.textContent = count;
}
function setTrafficColor(spanId, count) {
  const span = $(spanId); if (!span) return;
  const container = span.closest('.traffic-counter'); if (!container) return;
  const labelEl = container.querySelector('.label');
  const counterEl = container.querySelector('.counter');
  const isAlert = count > 0;
  labelEl?.classList.toggle('red', isAlert);
  counterEl?.classList.toggle('red', isAlert);
}

// ---------- Rendu liste optionnelle ----------
function renderTrafficList(alerts) {
  const box = $('traffic-table-container');
  if (!box) return;
  const list = (alerts || []).slice(0, 30);
  if (!list.length) {
    box.innerHTML = '<div style="opacity:.8;font-size:12px;">Aucune alerte trafic</div>';
    return;
  }
  const rows = list.map(a => {
    const type = a.type || '—';
    const when = formatTimestamp(a.pubMillis);
    const street = a.street || a.roadName || '—';
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
      <tbody>${rows}</tbody>
    </table>`;
}

// ---------- Helpers carte & popup ----------
function getLatLngFromAlert(a) {
  const num = v => (v == null ? null : (typeof v === 'number' ? v : parseFloat(v)));

  // location {y:lat, x:lon}
  if (a?.location?.y != null && a?.location?.x != null) {
    const lat = num(a.location.y), lon = num(a.location.x);
    if (isFinite(lat) && isFinite(lon)) return [lat, lon];
  }
  // lat/lon à la racine
  if (a?.lat != null && a?.lon != null) {
    const lat = num(a.lat), lon = num(a.lon);
    if (isFinite(lat) && isFinite(lon)) return [lat, lon];
  }
  // point {lat,lon}
  if (a?.point?.lat != null && a?.point?.lon != null) {
    const lat = num(a.point.lat), lon = num(a.point.lon);
    if (isFinite(lat) && isFinite(lon)) return [lat, lon];
  }
  // GeoJSON [lon, lat]
  if (Array.isArray(a?.geometry?.coordinates)) {
    const lon = num(a.geometry.coordinates[0]);
    const lat = num(a.geometry.coordinates[1]);
    if (isFinite(lat) && isFinite(lon)) return [lat, lon];
  }
  return null;
}

function getJamColor(subtype) {
  switch (subtype) {
    case 'JAM_LIGHT_TRAFFIC': return '#FFA500';
    case 'JAM_MODERATE_TRAFFIC': return '#FF4500';
    case 'JAM_HEAVY_TRAFFIC': return '#FF0000';
    case 'JAM_STAND_STILL_TRAFFIC': return '#8B0000';
    default: return '#FF0000';
  }
}

function buildPopupHtml(a) {
  const when   = formatTimestamp(a.pubMillis);
  const subtype= getSubtypeFr(a.subtype || 'NO_SUBTYPE');
  const street = a.street || a.roadName || 'Non spécifiée';
  const conf   = (a.confidence != null) ? `${a.confidence}/10` : '—';
  const reliab = (a.reliability != null) ? `${a.reliability}/10` : '—';
  const desc   = a.reportDescription || a.description || '';
  const by     = (a.reportByMunicipalityUser === 'true') ? 'Municipalité' :
                 (a.reportBy || a.source || 'Utilisateur Waze');
  const city   = a.city || a.town || '';

  return `
    <div style="min-width:240px">
      <div style="font-weight:700;margin-bottom:6px;">${a.type || 'ALERTE'} — ${subtype}</div>
      <div><b>Rue</b> : ${street}${city ? ', ' + city : ''}</div>
      <div><b>Date</b> : ${when}</div>
      ${desc ? `<div style="margin-top:6px">${desc}</div>` : ''}
      <div style="margin-top:8px;opacity:.85">
        <b>Confiance</b> : ${conf} &nbsp;|&nbsp; <b>Fiabilité</b> : ${reliab}
      </div>
      <div style="opacity:.7;margin-top:6px">Source : ${by}</div>
    </div>`;
}

function renderSummary(a) {
  const box = $('trafficSummary');
  if (!box || !a) { if (box) box.textContent=''; return; }
  const subtype = getSubtypeFr(a.subtype || 'NO_SUBTYPE');
  const when    = formatTimestamp(a.pubMillis);
  const street  = a.street || a.roadName || '—';
  const city    = a.city || a.town || '';
  box.innerHTML = `<b>${a.type || 'ALERTE'}</b> — ${subtype} • ${when} • ${street}${city ? ', ' + city : ''}`;
}
function setPager(idx, total) {
  const pager = $('trafficPager');
  if (pager) pager.textContent = total ? `${idx + 1} / ${total}` : '— / —';
}
function setHeaderTitle(type, total) {
  const title = $('trafficMapTitle');
  if (!title) return;
  if (type === 'ACCIDENT') title.textContent = `Accidents (${total})`;
  else if (type === 'JAM') title.textContent = `Ralentissements (${total})`;
  else title.textContent = `Alertes (${total})`;
}

// Force l’ouverture de la modale même si CSS attend une classe
function ensureOpenModal() {
    const modal   = document.getElementById('trafficMapModal');
    const overlay = document.getElementById('modalOverlay');
    if (!modal || !overlay) return false;
  
    overlay.classList.add('show');
    modal.classList.add('show');
  
    // lock scroll
    document.documentElement.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';
    return true;
  }
  
  function ensureCloseModal() {
    const modal   = document.getElementById('trafficMapModal');
    const overlay = document.getElementById('modalOverlay');
    if (!modal || !overlay) return;
  
    overlay.classList.remove('show');
    modal.classList.remove('show');
  
    // unlock scroll
    document.documentElement.style.overflow = '';
    document.body.style.overflow = '';
  }  
  
  function isModalOpen() {
    const modal = document.getElementById('trafficMapModal');
    return !!(modal && modal.classList.contains('show'));
  }

// ---------- Rendu d’UNE alerte ----------
function renderCurrentAlertOnMap() {
  if (!trafficMap || !trafficLayerGroup || !trafficModalType) return;

  const list = (trafficModalType === 'ACCIDENT') ? accidentAlerts : jamAlerts;
  if (!list.length) return;

  trafficLayerGroup.clearLayers();

  const a = list[currentAlertIndex];
  const ll = getLatLngFromAlert(a);
  if (!ll) {
    if (DEBUG_TRAFFIC) console.log('[traffic] Alerte sans coordonnées exploitables:', a);
    return;
  }

  const iconHtml = (trafficModalType === 'ACCIDENT')
    ? `<div style="width:22px;height:22px;border-radius:50%;background:#e53935;border:2px solid #fff;box-shadow:0 0 0 2px rgba(0,0,0,.25)"></div>`
    : `<div style="width:22px;height:22px;border-radius:50%;background:${getJamColor(a.subtype)};border:2px solid #fff;box-shadow:0 0 0 2px rgba(0,0,0,.25)"></div>`;

  const icon = L.divIcon({
    className: 'traffic-marker',
    html: iconHtml,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
    popupAnchor: [0, -11]
  });

  const marker = L.marker(ll, { icon }).addTo(trafficLayerGroup);
  marker.bindPopup(buildPopupHtml(a)).openPopup();

  // Zoom confortable
  trafficMap.setView(ll, 16);

  renderSummary(a);
  setPager(currentAlertIndex, list.length);
}

// ---------- Ouverture ----------
function openTrafficModal(type, startIndex = 0) {
  console.log('[traffic] openTrafficModal ->', type, 'accidents:', accidentAlerts.length, 'jams:', jamAlerts.length);

  const alerts = (type === 'ACCIDENT') ? accidentAlerts : jamAlerts;
  if (!alerts || !alerts.length) {
    if (DEBUG_TRAFFIC) console.log(`[traffic] openTrafficModal(${type}) ignoré : 0 alerte`);
    if (ensureOpenModal()) {
      setHeaderTitle(type, 0);
      const mapDiv = $('trafficMap'); if (mapDiv) mapDiv.innerHTML = '';
      const sum = $('trafficSummary'); if (sum) sum.textContent = 'Aucune alerte à afficher.';
    }
    return;
  }

  trafficModalType = type;
  currentAlertIndex = Math.max(0, Math.min(startIndex, alerts.length - 1));

  ensureOpenModal();

  // Init carte
  const mapDiv = $('trafficMap');
  if (!trafficMap) {
    trafficMap = L.map(mapDiv, { zoomControl: true });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap'
    }).addTo(trafficMap);
    trafficLayerGroup = L.layerGroup().addTo(trafficMap);
  } else {
    trafficLayerGroup.clearLayers();
  }

  setHeaderTitle(type, alerts.length);
  setTimeout(() => trafficMap.invalidateSize(), 120);
  renderCurrentAlertOnMap();
}

// ---------- Navigation ----------
function gotoNextAlert() {
  const list = (trafficModalType === 'ACCIDENT') ? accidentAlerts : jamAlerts;
  if (!list.length) return;
  currentAlertIndex = (currentAlertIndex + 1) % list.length;
  renderCurrentAlertOnMap();
}
function gotoPrevAlert() {
  const list = (trafficModalType === 'ACCIDENT') ? accidentAlerts : jamAlerts;
  if (!list.length) return;
  currentAlertIndex = (currentAlertIndex - 1 + list.length) % list.length;
  renderCurrentAlertOnMap();
}

// ---------- Fetch & agrégation ----------
function updateAlerts() {
  fetch('/alerts')
    .then(r => r.json())
    .then(data => {
      accidentCount = 0; jamCount = 0;
      accidentAlerts = []; jamAlerts = [];

      (Array.isArray(data) ? data : []).forEach(alert => {
        if (!alert) return;
        const t = String(alert.type || '').toUpperCase();
        if (t === 'ACCIDENT') { accidentCount++; accidentAlerts.push(alert); }
        else if (t === 'JAM' || t === 'TRAFFIC_JAM') { jamCount++; jamAlerts.push(alert); }
      });

      accidentAlerts = sortAlertsByTime(accidentAlerts);
      jamAlerts = sortAlertsByTime(jamAlerts);

      updateAccidentCounter(accidentCount);
      updateJamCounter(jamCount);
      setTrafficColor('accident-number', accidentCount);
      setTrafficColor('jam-number', jamCount);

      const merged = sortAlertsByTime([...(Array.isArray(data) ? data : [])]);
      renderTrafficList(merged);

      // Resync modale ouverte
      if (isModalOpen() && trafficModalType) {
        const list = (trafficModalType === 'ACCIDENT') ? accidentAlerts : jamAlerts;
        if (list.length) {
          if (currentAlertIndex >= list.length) currentAlertIndex = list.length - 1;
          setHeaderTitle(trafficModalType, list.length);
          renderCurrentAlertOnMap();
        } else {
          ensureCloseModal();
        }
      }
    })
    .catch(err => console.error('Erreur lors de la récupération des alertes:', err));
}

// ---------- Bind après DOM prêt ----------
document.addEventListener('DOMContentLoaded', function() {
  // Bind clic sur toute la zone du compteur (plus tolérant que le <span> seul)
  const counters = $('widget-traffic')?.querySelectorAll('.traffic-counter') || [];
  const accContainer = counters[0];
  const jamContainer = counters[1];

  // Fallback sur les <span> si jamais
  const accSpan = $('accident-number');
  const jamSpan = $('jam-number');

  if (accContainer) accContainer.addEventListener('click', () => { if (DEBUG_TRAFFIC) console.log('[traffic] click Accident'); openTrafficModal('ACCIDENT', 0); });
  else if (accSpan) accSpan.addEventListener('click', () => { if (DEBUG_TRAFFIC) console.log('[traffic] click Accident (span)'); openTrafficModal('ACCIDENT', 0); });

  if (jamContainer) jamContainer.addEventListener('click', () => { if (DEBUG_TRAFFIC) console.log('[traffic] click Jam'); openTrafficModal('JAM', 0); });
  else if (jamSpan) jamSpan.addEventListener('click', () => { if (DEBUG_TRAFFIC) console.log('[traffic] click Jam (span)'); openTrafficModal('JAM', 0); });

  // Boutons modale & clavier
  onSafe('trafficNext', 'click', (e) => { e.stopPropagation(); gotoNextAlert(); });
  onSafe('trafficPrev', 'click', (e) => { e.stopPropagation(); gotoPrevAlert(); });
  onSafe('closeTrafficMap','click', (e) => { e.stopPropagation(); ensureCloseModal(); });  

  window.addEventListener('keydown', (e) => {
    if (!isModalOpen()) return;
    if (e.key === 'ArrowRight') gotoNextAlert();
    if (e.key === 'ArrowLeft')  gotoPrevAlert();
    if (e.key === 'Escape')     ensureCloseModal();
  });

  document.getElementById('modalOverlay')?.addEventListener('click', ensureCloseModal);

  // Fermer si on clique en dehors du contenu (dans la zone de la modale mais pas dans .modal-content)
    const modalEl = $('trafficMapModal');
    modalEl?.addEventListener('click', (e) => {
    const content = modalEl.querySelector('.modal-content');
    if (!content || !content.contains(e.target)) {
        ensureCloseModal();
    }
    });

    // Empêcher la fermeture quand on clique DANS le contenu
    modalEl?.querySelector('.modal-content')
    ?.addEventListener('click', (e) => e.stopPropagation());

  // Démarre la boucle data
  updateAlerts();
  setInterval(updateAlerts, 60000);
});