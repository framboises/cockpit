// ==============================
// alerte.js — modal OSM (1 alerte à la fois) + navigation — ACCIDENT & JAM
// ==============================

let accidentCount = 0;
let jamCount = 0;
let hazardCount = 0;
let closedCount = 0;
let currentAlertIndex = 0;
let accidentAlerts = [];
let jamAlerts = [];
let hazardAlerts = [];
let closedAlerts = [];

// Modale & Leaflet
let trafficMap = null;
let trafficLayerGroup = null;
let trafficModalType = null;   // 'ACCIDENT' | 'JAM' | 'HAZARD' | 'ROAD_CLOSED'

// Navigation state per type for map pin cycling
let _alertNavState = {}; // {type: {index: 0}}

// Debug flag
const DEBUG_TRAFFIC = false;

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
  var map = {
    'ACCIDENT_MINOR': 'Accident mineur',
    'ACCIDENT_MAJOR': 'Accident majeur',
    'JAM_LIGHT_TRAFFIC': 'Trafic leger',
    'JAM_MODERATE_TRAFFIC': 'Trafic modere',
    'JAM_HEAVY_TRAFFIC': 'Trafic dense',
    'JAM_STAND_STILL_TRAFFIC': 'Trafic arrete',
    'HAZARD_ON_ROAD': 'Danger sur la route',
    'HAZARD_ON_SHOULDER': 'Danger bas-cote',
    'HAZARD_WEATHER': 'Danger meteo',
    'HAZARD_ON_ROAD_OBJECT': 'Objet sur la route',
    'HAZARD_ON_ROAD_POT_HOLE': 'Nid-de-poule',
    'HAZARD_ON_ROAD_ROAD_KILL': 'Animal sur la route',
    'HAZARD_ON_SHOULDER_CAR_STOPPED': 'Vehicule arrete bas-cote',
    'HAZARD_ON_SHOULDER_ANIMALS': 'Animaux bas-cote',
    'HAZARD_ON_SHOULDER_MISSING_SIGN': 'Panneau manquant',
    'HAZARD_WEATHER_FOG': 'Brouillard',
    'HAZARD_WEATHER_HAIL': 'Grele',
    'HAZARD_WEATHER_HEAVY_RAIN': 'Forte pluie',
    'HAZARD_WEATHER_HEAVY_SNOW': 'Forte neige',
    'HAZARD_WEATHER_FLOOD': 'Inondation',
    'HAZARD_WEATHER_FREEZING_RAIN': 'Pluie verglacante',
    'HAZARD_WEATHER_HEAT_WAVE': 'Canicule',
    'HAZARD_ON_ROAD_LANE_CLOSED': 'Voie fermee',
    'HAZARD_ON_ROAD_OIL': 'Huile sur la route',
    'HAZARD_ON_ROAD_ICE': 'Verglas',
    'HAZARD_ON_ROAD_CONSTRUCTION': 'Travaux',
    'HAZARD_ON_ROAD_CAR_STOPPED': 'Vehicule arrete',
    'HAZARD_ON_ROAD_TRAFFIC_LIGHT_FAULT': 'Feu en panne',
    'HAZARD_ON_ROAD_EMERGENCY_VEHICLE': 'Vehicule d\'urgence',
    'HAZARD_ON_ROAD_PEDESTRIAN': 'Pieton sur la route',
    'HAZARD_ON_SHOULDER_EMERGENCY_VEHICLE': 'Vehicule d\'urgence bas-cote',
    'HAZARD_WEATHER_MONSOON': 'Mousson',
    'HAZARD_WEATHER_TORNADO': 'Tornade',
    'HAZARD_WEATHER_HURRICANE': 'Ouragan',
    'ROAD_CLOSED_HAZARD': 'Fermee - danger',
    'ROAD_CLOSED_CONSTRUCTION': 'Fermee - travaux',
    'ROAD_CLOSED_EVENT': 'Fermee - evenement',
    'NO_SUBTYPE': ''
  };
  return map[subtype] || subtype || '';
}

function getTypeFr(type) {
  var m = {
    'ACCIDENT': 'Accident',
    'JAM': 'Ralentissement',
    'HAZARD': 'Danger',
    'WEATHERHAZARD': 'Danger meteo',
    'ROAD_CLOSED': 'Route fermee',
    'CONSTRUCTION': 'Travaux'
  };
  return m[type] || type || '';
}

// ---------- Affichage alerte sur la carte principale ----------
function showAlertOnMap(type) {
  var list = getAlertsList(type);
  if (!list.length) return;

  // Init or advance index
  if (!_alertNavState[type]) _alertNavState[type] = {index: 0};
  else _alertNavState[type].index = (_alertNavState[type].index + 1) % list.length;

  var idx = _alertNavState[type].index;
  var a = list[idx];
  if (!a) return;

  var lat = null, lon = null;
  if (a.location) { lat = a.location.y; lon = a.location.x; }
  if (lat == null || lon == null) return;

  var stFr = getSubtypeFr(a.subtype);
  // Pour le titre: utiliser le subtype traduit s'il est plus precis que le type
  var titleFr = stFr || getTypeFr(type);

  window._alertPinData = {
    lat: lat,
    lon: lon,
    type: type,
    typeFr: titleFr,
    subtypeFr: (stFr && stFr !== titleFr) ? stFr : '',
    street: a.street || '',
    city: a.city || '',
    date: formatTimestamp(a.pubMillis),
    description: a.reportDescription || '',
    index: idx,
    total: list.length
  };

  if (window.CockpitMapView && window.CockpitMapView.switchView) {
    window.CockpitMapView.switchView('map');
    setTimeout(function() {
      document.dispatchEvent(new CustomEvent('showAlertPin'));
    }, 400);
  }
}

// ---------- Couleurs widget ----------
function updateAlertCounter(id, count) {
  var num = document.getElementById(id);
  if (num) num.textContent = count;
  var container = num ? num.closest('.traffic-counter') : null;
  if (container) container.classList.toggle('has-alerts', count > 0);
}

function updateTooltip(tooltipId, alerts) {
  var el = $(tooltipId);
  if (!el) return;
  el.textContent = '';
  if (!alerts.length) return;
  // Group by subtype
  var groups = {};
  alerts.forEach(function(a) {
    var st = getSubtypeFr(a.subtype) || a.type;
    groups[st] = (groups[st] || 0) + 1;
  });
  // Count by street
  var streets = {};
  alerts.forEach(function(a) {
    var s = a.street || a.city || '';
    if (s) streets[s] = (streets[s] || 0) + 1;
  });
  // Render subtypes
  Object.keys(groups).forEach(function(label) {
    if (!label) return;
    var line = document.createElement('div');
    line.className = 'tc-tooltip-line';
    line.textContent = groups[label] + 'x ' + label;
    el.appendChild(line);
  });
  // Render top streets (max 3)
  var streetList = Object.keys(streets).sort(function(a, b) { return streets[b] - streets[a]; }).slice(0, 3);
  if (streetList.length) {
    var sep = document.createElement('div');
    sep.style.cssText = 'border-top:1px solid rgba(148,163,184,0.15);margin:3px 0;';
    el.appendChild(sep);
    streetList.forEach(function(s) {
      var line = document.createElement('div');
      line.className = 'tc-tooltip-line';
      var ico = document.createElement('span');
      ico.className = 'material-symbols-outlined';
      ico.textContent = 'location_on';
      line.appendChild(ico);
      line.appendChild(document.createTextNode(s));
      el.appendChild(line);
    });
  }
}

// Backward compat
function updateAccidentCounter(count) { updateAlertCounter('accident-number', count); }
function updateJamCounter(count) { updateAlertCounter('jam-number', count); }
function setTrafficColor() {} // no-op, handled by updateCounter now

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

// Force l'ouverture de la modale même si CSS attend une classe
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

// ---------- Helper: liste d'alertes par type ----------
function getAlertsList(type) {
  if (type === 'ACCIDENT') return accidentAlerts;
  if (type === 'JAM') return jamAlerts;
  if (type === 'HAZARD') return hazardAlerts;
  if (type === 'ROAD_CLOSED') return closedAlerts;
  return [];
}

function getAlertColor(type) {
  if (type === 'ACCIDENT') return '#e53935';
  if (type === 'JAM') return '#f59e0b';
  if (type === 'HAZARD') return '#f97316';
  if (type === 'ROAD_CLOSED') return '#8b5cf6';
  return '#e53935';
}

// ---------- Rendu d'UNE alerte ----------
function renderCurrentAlertOnMap() {
  if (!trafficMap || !trafficLayerGroup || !trafficModalType) return;

  const list = getAlertsList(trafficModalType);
  if (!list.length) return;

  trafficLayerGroup.clearLayers();

  const a = list[currentAlertIndex];
  const ll = getLatLngFromAlert(a);
  if (!ll) {
    if (DEBUG_TRAFFIC) console.log('[traffic] Alerte sans coordonnées exploitables:', a);
    return;
  }

  const markerColor = getAlertColor(trafficModalType);
  const iconHtml = `<div style="width:22px;height:22px;border-radius:50%;background:${markerColor};border:2px solid #fff;box-shadow:0 0 0 2px rgba(0,0,0,.25)"></div>`;

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

  // Aucun résultat ?
  if (!alerts || !alerts.length) {
    if (DEBUG_TRAFFIC) {
      console.log(`[traffic] [DEBUG] openTrafficModal(${type}) — affichage forcé avec 0 alerte`);
      ensureOpenModal();
      setHeaderTitle(type, 0);

      // Message d'état vide
      const sum = $('trafficSummary');
      if (sum) sum.textContent = 'Aucune alerte à afficher.';

      // Ne crée pas la carte si elle n'existe pas ; si elle existe, vide juste les couches
      if (trafficLayerGroup) trafficLayerGroup.clearLayers();

      // On ne va PAS plus loin (pas de renderCurrentAlertOnMap)
      return;
    } else {
      console.log(`[traffic] openTrafficModal(${type}) ignoré : 0 alerte`);
      return; // pas d'ouverture en mode normal
    }
  }

  // On a des alertes : on ouvre normalement
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
  const list = getAlertsList(trafficModalType);
  if (!list.length) return;
  currentAlertIndex = (currentAlertIndex + 1) % list.length;
  renderCurrentAlertOnMap();
}
function gotoPrevAlert() {
  const list = getAlertsList(trafficModalType);
  if (!list.length) return;
  currentAlertIndex = (currentAlertIndex - 1 + list.length) % list.length;
  renderCurrentAlertOnMap();
}

// ---------- Fetch & agrégation ----------
function updateAlerts() {
  fetch('/alerts')
    .then(r => r.json())
    .then(data => {
      accidentCount = 0; jamCount = 0; hazardCount = 0; closedCount = 0;
      accidentAlerts = []; jamAlerts = []; hazardAlerts = []; closedAlerts = [];

      (Array.isArray(data) ? data : []).forEach(alert => {
        if (!alert) return;
        const t = String(alert.type || '').toUpperCase();
        if (t === 'ACCIDENT') { accidentCount++; accidentAlerts.push(alert); }
        else if (t === 'JAM' || t === 'TRAFFIC_JAM') { jamCount++; jamAlerts.push(alert); }
        else if (t === 'HAZARD' || t === 'WEATHERHAZARD') { hazardCount++; hazardAlerts.push(alert); }
        else if (t === 'ROAD_CLOSED') { closedCount++; closedAlerts.push(alert); }
      });

      accidentAlerts = sortAlertsByTime(accidentAlerts);
      jamAlerts = sortAlertsByTime(jamAlerts);
      hazardAlerts = sortAlertsByTime(hazardAlerts);
      closedAlerts = sortAlertsByTime(closedAlerts);

      updateAlertCounter('accident-number', accidentCount);
      updateAlertCounter('jam-number', jamCount);
      updateAlertCounter('hazard-number', hazardCount);
      updateAlertCounter('closed-number', closedCount);

      updateTooltip('tooltip-accident', accidentAlerts);
      updateTooltip('tooltip-jam', jamAlerts);
      updateTooltip('tooltip-hazard', hazardAlerts);
      updateTooltip('tooltip-closed', closedAlerts);

      const merged = sortAlertsByTime([...(Array.isArray(data) ? data : [])]);
      renderTrafficList(merged);

      // Resync modale ouverte
      if (isModalOpen() && trafficModalType) {
        const list = getAlertsList(trafficModalType);
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
  if (!window.isBlockAllowed("widget-traffic")) return;
  // Bind clic + hover sur chaque compteur
  var widgetEl = $('widget-traffic');
  if (widgetEl) {
    var allCounters = widgetEl.querySelectorAll('.traffic-counter[data-alert-type]');
    allCounters.forEach(function(ctr) {
      var alertType = ctr.getAttribute('data-alert-type');
      var tooltip = ctr.querySelector('.tc-tooltip');

      // Click -> show on map
      ctr.addEventListener('click', function() {
        if (DEBUG_TRAFFIC) console.log('[traffic] click ' + alertType);
        showAlertOnMap(alertType);
      });

      // Hover -> position tooltip fixed
      if (tooltip) {
        ctr.addEventListener('mouseenter', function() {
          if (!tooltip.hasChildNodes()) return;
          var rect = ctr.getBoundingClientRect();
          tooltip.style.display = 'block';
          var tw = tooltip.offsetWidth;
          var th = tooltip.offsetHeight;
          var left = rect.left + rect.width / 2 - tw / 2;
          if (left < 4) left = 4;
          if (left + tw > window.innerWidth - 4) left = window.innerWidth - 4 - tw;
          tooltip.style.left = left + 'px';
          // Au-dessus si ca tient, sinon en dessous
          var topPos = rect.top - th - 8;
          if (topPos < 4) {
            tooltip.style.top = (rect.bottom + 8) + 'px';
            tooltip.classList.add('below');
            tooltip.classList.remove('above');
          } else {
            tooltip.style.top = topPos + 'px';
            tooltip.classList.add('above');
            tooltip.classList.remove('below');
          }
        });
        ctr.addEventListener('mouseleave', function() {
          tooltip.style.display = 'none';
        });
      }
    });
  }

  // Bouton "toutes les alertes sur la carte"
  var btnAllAlerts = document.getElementById('btn-show-all-alerts');
  if (btnAllAlerts) {
    btnAllAlerts.addEventListener('click', function() {
      var all = [].concat(accidentAlerts, jamAlerts, hazardAlerts, closedAlerts);
      if (!all.length) return;
      var pins = [];
      for (var i = 0; i < all.length; i++) {
        var a = all[i];
        if (!a.location) continue;
        var stFr = getSubtypeFr(a.subtype);
        pins.push({
          lat: a.location.y,
          lon: a.location.x,
          type: a.type,
          typeFr: stFr || getTypeFr(a.type),
          street: a.street || '',
          date: formatTimestamp(a.pubMillis)
        });
      }
      if (!pins.length) return;
      window._allAlertPinsData = pins;
      if (window.CockpitMapView && window.CockpitMapView.switchView) {
        window.CockpitMapView.switchView('map');
        setTimeout(function() {
          document.dispatchEvent(new CustomEvent('showAllAlertPins'));
        }, 400);
      }
    });
  }

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

  // Démarre la boucle data
  updateAlerts();
  setInterval(updateAlerts, 60000);
});