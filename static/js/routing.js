// ==========================================================================
// ROUTING — Navigation interne Valhalla pour equipes operationnelles
// ==========================================================================

(function () {
  "use strict";

  // --- State ---
  var routeLayer = null;      // L.layerGroup pour le trace
  var startMarker = null;
  var endMarker = null;
  var isoLayer = null;        // L.layerGroup pour isochrones
  var maneuverMarkers = [];
  var pickMode = null;        // "start" | "end" | null
  var startLatLng = null;
  var endLatLng = null;
  var currentProfile = "auto";
  var routeData = null;       // derniere reponse serveur
  var valhallaAvailable = false;

  // --- Polyline decode (Valhalla utilise encoded polyline precision 6) ---

  function decodePolyline(encoded, precision) {
    precision = precision || 6;
    var factor = Math.pow(10, precision);
    var len = encoded.length;
    var index = 0;
    var lat = 0;
    var lng = 0;
    var coords = [];

    while (index < len) {
      var b, shift = 0, result = 0;
      do {
        b = encoded.charCodeAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      var dlat = (result & 1) ? ~(result >> 1) : (result >> 1);
      lat += dlat;

      shift = 0;
      result = 0;
      do {
        b = encoded.charCodeAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      var dlng = (result & 1) ? ~(result >> 1) : (result >> 1);
      lng += dlng;

      coords.push([lat / factor, lng / factor]);
    }
    return coords;
  }

  // --- Helpers ---

  function formatDuration(seconds) {
    if (seconds < 60) return Math.round(seconds) + " s";
    var mins = Math.round(seconds / 60);
    if (mins < 60) return mins + " min";
    var h = Math.floor(mins / 60);
    var m = mins % 60;
    return h + " h " + (m > 0 ? m + " min" : "");
  }

  function formatDistance(km) {
    if (km < 1) return Math.round(km * 1000) + " m";
    return km.toFixed(1) + " km";
  }

  function getMap() {
    return window.CockpitMapView ? window.CockpitMapView.getMap() : null;
  }

  // --- Markers ---

  function createStartIcon() {
    return L.divIcon({
      className: "routing-marker routing-start",
      html: '<span class="material-symbols-outlined">trip_origin</span>',
      iconSize: [32, 32],
      iconAnchor: [16, 16],
    });
  }

  function createEndIcon() {
    return L.divIcon({
      className: "routing-marker routing-end",
      html: '<span class="material-symbols-outlined">flag</span>',
      iconSize: [32, 32],
      iconAnchor: [16, 16],
    });
  }

  function setStart(latlng) {
    var map = getMap();
    if (!map) return;
    startLatLng = latlng;
    if (startMarker) map.removeLayer(startMarker);
    startMarker = L.marker(latlng, { icon: createStartIcon(), draggable: true, zIndexOffset: 1000 }).addTo(map);
    startMarker.on("dragend", function () {
      startLatLng = startMarker.getLatLng();
      updateInputDisplay();
      if (endLatLng) computeRoute();
    });
    updateInputDisplay();
  }

  function setEnd(latlng) {
    var map = getMap();
    if (!map) return;
    endLatLng = latlng;
    if (endMarker) map.removeLayer(endMarker);
    endMarker = L.marker(latlng, { icon: createEndIcon(), draggable: true, zIndexOffset: 1000 }).addTo(map);
    endMarker.on("dragend", function () {
      endLatLng = endMarker.getLatLng();
      updateInputDisplay();
      if (startLatLng) computeRoute();
    });
    updateInputDisplay();
  }

  function updateInputDisplay() {
    var startInput = document.getElementById("routing-start-input");
    var endInput = document.getElementById("routing-end-input");
    if (startInput && startLatLng) {
      startInput.value = startLatLng.lat.toFixed(5) + ", " + startLatLng.lng.toFixed(5);
    }
    if (endInput && endLatLng) {
      endInput.value = endLatLng.lat.toFixed(5) + ", " + endLatLng.lng.toFixed(5);
    }
  }

  // --- Map click handler ---

  function onMapClick(e) {
    if (!pickMode) return;
    if (pickMode === "start") {
      setStart(e.latlng);
      // Auto-switch to end pick if no end yet
      if (!endLatLng) {
        activatePick("end");
      } else {
        deactivatePick();
        computeRoute();
      }
    } else if (pickMode === "end") {
      setEnd(e.latlng);
      deactivatePick();
      if (startLatLng) computeRoute();
    }
  }

  function activatePick(mode) {
    pickMode = mode;
    var map = getMap();
    if (map) map.getContainer().style.cursor = "crosshair";
    // Visual feedback on buttons
    var startBtn = document.getElementById("routing-pick-start");
    var endBtn = document.getElementById("routing-pick-end");
    if (startBtn) startBtn.classList.toggle("active", mode === "start");
    if (endBtn) endBtn.classList.toggle("active", mode === "end");
  }

  function deactivatePick() {
    pickMode = null;
    var map = getMap();
    if (map) map.getContainer().style.cursor = "";
    var startBtn = document.getElementById("routing-pick-start");
    var endBtn = document.getElementById("routing-pick-end");
    if (startBtn) startBtn.classList.remove("active");
    if (endBtn) endBtn.classList.remove("active");
  }

  // --- Route computation ---

  function computeRoute() {
    if (!startLatLng || !endLatLng) return;

    var summary = document.getElementById("routing-summary");
    var maneuverList = document.getElementById("routing-maneuvers");
    if (summary) summary.innerHTML = '<span class="material-symbols-outlined spin">progress_activity</span> Calcul en cours...';
    if (maneuverList) maneuverList.innerHTML = "";

    var payload = {
      from: { lat: startLatLng.lat, lng: startLatLng.lng },
      to: { lat: endLatLng.lat, lng: endLatLng.lng },
      vehicule: currentProfile,
    };

    apiPost("/api/routing/route", payload)
      .then(function (data) {
        if (data.error) {
          if (summary) summary.textContent = "Erreur : " + data.error;
          return;
        }
        routeData = data;
        drawRoute(data);
        showSummary(data);
        showManeuvers(data);
      })
      .catch(function (err) {
        if (summary) summary.textContent = "Erreur de connexion au serveur de routage";
        console.error("Routing error:", err);
      });
  }

  // --- Draw route on map ---

  function drawRoute(data) {
    var map = getMap();
    if (!map) return;
    clearRoute();

    routeLayer = L.layerGroup().addTo(map);

    data.legs.forEach(function (leg) {
      if (!leg.shape) return;
      var coords = decodePolyline(leg.shape, 6);

      // Glow layer
      var glow = L.polyline(coords, {
        color: "#2563eb",
        weight: 10,
        opacity: 0.25,
        lineCap: "round",
        lineJoin: "round",
        interactive: false,
      });
      routeLayer.addLayer(glow);

      // Main route
      var line = L.polyline(coords, {
        color: "#2563eb",
        weight: 5,
        opacity: 0.9,
        lineCap: "round",
        lineJoin: "round",
      });
      routeLayer.addLayer(line);

      // Animated dash overlay
      var dash = L.polyline(coords, {
        color: "#ffffff",
        weight: 2,
        opacity: 0.6,
        dashArray: "8 12",
        lineCap: "round",
        interactive: false,
      });
      routeLayer.addLayer(dash);
      animateDash(dash);

      // Maneuver markers
      leg.maneuvers.forEach(function (m, idx) {
        if (idx === 0 || idx === leg.maneuvers.length - 1) return; // skip start/end
        var shapeIdx = m.begin_shape_index;
        if (shapeIdx < coords.length) {
          var pos = coords[shapeIdx];
          var icon = getManeuverIcon(m.type);
          var marker = L.marker(pos, {
            icon: L.divIcon({
              className: "routing-maneuver-icon",
              html: '<span class="material-symbols-outlined">' + icon + '</span>',
              iconSize: [24, 24],
              iconAnchor: [12, 12],
            }),
            interactive: true,
          });
          marker.bindTooltip(m.instruction, { direction: "top", offset: [0, -10] });
          routeLayer.addLayer(marker);
          maneuverMarkers.push(marker);
        }
      });
    });

    // Fit bounds
    if (data.legs.length > 0 && data.legs[0].shape) {
      var allCoords = decodePolyline(data.legs[0].shape, 6);
      if (allCoords.length > 0) {
        map.fitBounds(L.latLngBounds(allCoords), { padding: [60, 60] });
      }
    }
  }

  var _dashOffset = 0;
  var _dashTimer = null;

  function animateDash(polyline) {
    if (_dashTimer) clearInterval(_dashTimer);
    _dashOffset = 0;
    _dashTimer = setInterval(function () {
      _dashOffset -= 1;
      if (polyline._path) {
        polyline._path.style.strokeDashoffset = _dashOffset;
      }
    }, 50);
  }

  function getManeuverIcon(type) {
    // Valhalla maneuver types
    var icons = {
      1: "turn_right",       // right
      2: "turn_left",        // left
      3: "turn_slight_right",
      4: "turn_slight_left",
      5: "straight",
      6: "straight",
      7: "turn_right",       // sharp right
      8: "turn_left",        // sharp left
      9: "u_turn_right",
      10: "u_turn_left",
      15: "roundabout_right",
      26: "roundabout_right",
      27: "flag",            // destination
    };
    return icons[type] || "arrow_forward";
  }

  // --- Summary display ---

  function showSummary(data) {
    var el = document.getElementById("routing-summary");
    if (!el) return;

    var dur = formatDuration(data.duration_s);
    var dist = formatDistance(data.distance_km);

    el.innerHTML = "";

    var durSpan = document.createElement("span");
    durSpan.className = "routing-summary-duration";
    durSpan.textContent = dur;
    el.appendChild(durSpan);

    var distSpan = document.createElement("span");
    distSpan.className = "routing-summary-distance";
    distSpan.textContent = dist;
    el.appendChild(distSpan);

    var profileSpan = document.createElement("span");
    profileSpan.className = "routing-summary-profile";
    profileSpan.textContent = getProfileLabel(currentProfile);
    el.appendChild(profileSpan);
  }

  function getProfileLabel(p) {
    var labels = {
      auto: "Vehicule",
      ambulance: "Ambulance",
      vl: "VL",
      pedestrian: "A pied",
      bicycle: "Velo",
    };
    return labels[p] || p;
  }

  // --- Maneuvers list ---

  function showManeuvers(data) {
    var list = document.getElementById("routing-maneuvers");
    if (!list) return;
    list.innerHTML = "";

    data.legs.forEach(function (leg) {
      leg.maneuvers.forEach(function (m) {
        var li = document.createElement("div");
        li.className = "routing-maneuver-item";

        var icon = document.createElement("span");
        icon.className = "material-symbols-outlined routing-maneuver-step-icon";
        icon.textContent = getManeuverIcon(m.type);
        li.appendChild(icon);

        var text = document.createElement("div");
        text.className = "routing-maneuver-text";

        var instr = document.createElement("span");
        instr.className = "routing-maneuver-instruction";
        instr.textContent = m.instruction;
        text.appendChild(instr);

        var meta = document.createElement("span");
        meta.className = "routing-maneuver-meta";
        meta.textContent = formatDistance(m.distance_km) + " - " + formatDuration(m.duration_s);
        text.appendChild(meta);

        li.appendChild(text);
        list.appendChild(li);

        // Click to zoom on maneuver
        li.addEventListener("click", function () {
          var map = getMap();
          if (!map || !routeData) return;
          var coords = [];
          routeData.legs.forEach(function (l) {
            if (l.shape) coords = coords.concat(decodePolyline(l.shape, 6));
          });
          if (m.begin_shape_index < coords.length) {
            map.setView(coords[m.begin_shape_index], 18);
          }
        });
      });
    });
  }

  // --- Clear ---

  function clearRoute() {
    var map = getMap();
    if (!map) return;
    if (routeLayer) { map.removeLayer(routeLayer); routeLayer = null; }
    if (_dashTimer) { clearInterval(_dashTimer); _dashTimer = null; }
    maneuverMarkers = [];
    routeData = null;
  }

  function clearAll() {
    var map = getMap();
    clearRoute();
    clearIsochrone();
    if (map && startMarker) { map.removeLayer(startMarker); startMarker = null; }
    if (map && endMarker) { map.removeLayer(endMarker); endMarker = null; }
    startLatLng = null;
    endLatLng = null;
    deactivatePick();

    var startInput = document.getElementById("routing-start-input");
    var endInput = document.getElementById("routing-end-input");
    var summary = document.getElementById("routing-summary");
    var maneuverList = document.getElementById("routing-maneuvers");
    if (startInput) startInput.value = "";
    if (endInput) endInput.value = "";
    if (summary) summary.innerHTML = "";
    if (maneuverList) maneuverList.innerHTML = "";
  }

  // --- Swap start/end ---

  function swapPoints() {
    var tmpLat = startLatLng;
    startLatLng = endLatLng;
    endLatLng = tmpLat;

    var map = getMap();
    if (map) {
      if (startMarker) map.removeLayer(startMarker);
      if (endMarker) map.removeLayer(endMarker);
      startMarker = null;
      endMarker = null;
      if (startLatLng) setStart(startLatLng);
      if (endLatLng) setEnd(endLatLng);
    }
    if (startLatLng && endLatLng) computeRoute();
  }

  // --- Isochrone ---

  function computeIsochrone() {
    if (!startLatLng) return;
    var summary = document.getElementById("routing-summary");
    if (summary) summary.innerHTML = '<span class="material-symbols-outlined spin">progress_activity</span> Calcul isochrone...';

    apiPost("/api/routing/isochrone", {
      center: { lat: startLatLng.lat, lng: startLatLng.lng },
      minutes: [3, 5, 10],
      vehicule: currentProfile,
    })
      .then(function (geojson) {
        if (geojson.error) {
          if (summary) summary.textContent = "Erreur : " + geojson.error;
          return;
        }
        drawIsochrone(geojson);
        if (summary) summary.textContent = "Isochrone : 3 / 5 / 10 min (" + getProfileLabel(currentProfile) + ")";
      })
      .catch(function () {
        if (summary) summary.textContent = "Erreur isochrone";
      });
  }

  function drawIsochrone(geojson) {
    var map = getMap();
    if (!map) return;
    clearIsochrone();

    var colors = ["#16a34a", "#f59e0b", "#dc2626"]; // 3min=vert, 5min=orange, 10min=rouge
    var opacities = [0.3, 0.2, 0.15];

    isoLayer = L.layerGroup().addTo(map);

    // Valhalla retourne features dans l'ordre inverse (le plus grand d'abord)
    var features = (geojson.features || []).slice().reverse();
    features.forEach(function (feature, i) {
      var colorIdx = Math.min(i, colors.length - 1);
      var layer = L.geoJSON(feature, {
        style: {
          fillColor: colors[colorIdx],
          fillOpacity: opacities[colorIdx],
          color: colors[colorIdx],
          weight: 2,
          opacity: 0.6,
        },
      });
      isoLayer.addLayer(layer);
    });

    // Fit bounds
    var bounds = isoLayer.getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [40, 40] });
  }

  function clearIsochrone() {
    var map = getMap();
    if (map && isoLayer) { map.removeLayer(isoLayer); isoLayer = null; }
  }

  // --- Panel toggle ---

  function toggleRoutingPanel() {
    var panel = document.getElementById("routing-panel");
    if (!panel) return;
    var isOpen = panel.classList.contains("open");
    if (isOpen) {
      panel.classList.remove("open");
      deactivatePick();
    } else {
      panel.classList.add("open");
      checkValhallaHealth();
    }
  }

  // --- Health check ---

  function checkValhallaHealth() {
    var status = document.getElementById("routing-status");
    fetch("/api/routing/health")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        valhallaAvailable = d.available;
        if (status) {
          if (d.available) {
            status.className = "routing-status online";
            status.textContent = "Valhalla connecte";
          } else {
            status.className = "routing-status offline";
            status.textContent = "Valhalla hors ligne";
          }
        }
      })
      .catch(function () {
        valhallaAvailable = false;
        if (status) {
          status.className = "routing-status offline";
          status.textContent = "Serveur indisponible";
        }
      });
  }

  // --- Init ---

  function init() {
    // Wait for map to be ready
    var checkInterval = setInterval(function () {
      var map = getMap();
      if (!map) return;
      clearInterval(checkInterval);

      // Register map click handler
      map.on("click", onMapClick);

      // Bind UI events
      bindEvents();
    }, 500);
  }

  function bindEvents() {
    // Routing control button (added to map by map_view.js pattern)
    var routeBtn = document.getElementById("routing-toggle-btn");
    if (routeBtn) routeBtn.addEventListener("click", toggleRoutingPanel);

    // Pick buttons
    var pickStart = document.getElementById("routing-pick-start");
    var pickEnd = document.getElementById("routing-pick-end");
    if (pickStart) pickStart.addEventListener("click", function () { activatePick("start"); });
    if (pickEnd) pickEnd.addEventListener("click", function () { activatePick("end"); });

    // Profile select
    var profileSelect = document.getElementById("routing-profile");
    if (profileSelect) {
      profileSelect.addEventListener("change", function () {
        currentProfile = profileSelect.value;
        if (startLatLng && endLatLng) computeRoute();
      });
    }

    // Action buttons
    var swapBtn = document.getElementById("routing-swap");
    if (swapBtn) swapBtn.addEventListener("click", swapPoints);

    var clearBtn = document.getElementById("routing-clear");
    if (clearBtn) clearBtn.addEventListener("click", clearAll);

    var isoBtn = document.getElementById("routing-isochrone-btn");
    if (isoBtn) isoBtn.addEventListener("click", computeIsochrone);

    // Close panel
    var closeBtn = document.getElementById("routing-close");
    if (closeBtn) closeBtn.addEventListener("click", function () {
      var panel = document.getElementById("routing-panel");
      if (panel) panel.classList.remove("open");
      deactivatePick();
    });
  }

  // Auto-init
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Expose
  window.CockpitRouting = {
    toggle: toggleRoutingPanel,
    setStart: setStart,
    setEnd: setEnd,
    computeRoute: computeRoute,
    computeIsochrone: computeIsochrone,
    clearAll: clearAll,
    getRouteData: function () { return routeData; },
  };

})();
