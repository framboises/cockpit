// routing.js - Modale Cockpit pour le calcul d'itineraire vehicule -> fiche
// d'intervention, et envoi de l'itineraire force vers la tablette du vehicule.
//
// Architecture :
//   - apiPost('/api/route', { fiche_id }) : le serveur resout from = derniere
//     position du vehicule engage, to = GPS de la fiche, et applique les
//     penalites Waze.
//   - apiPost('/api/route/forward', { device_id, polyline, ... }) pousse un
//     message field_messages type=route a la tablette ciblee.
//   - L'operateur peut ajouter des waypoints intermediaires (clic sur la
//     carte) pour forcer un trajet alternatif, puis recalculer / envoyer.

(function () {
  "use strict";

  var STATE = {
    ficheId: null,
    deviceId: null,
    deviceName: null,
    event: null,
    year: null,
    from: null,         // [lat, lng]
    to: null,           // [lat, lng]
    waypoints: [],      // [[lat, lng], ...] forces par l'operateur
    god: false,
    lastResult: null,   // dernier objet { polyline, distance_m, duration_s, ... }
    map: null,
    mapLayers: null,    // L.layerGroup pour from/to/waypoints/polyline route
    overridesLayer: null, // L.layerGroup pour les corrections admin (transparence)
    overridesLoaded: false,
    waypointPickMode: false,
    overlay: null,
    routeAnimFrame: null, // rAF id pour le dash anime (style Waze)
  };

  // ---------------------------------------------------------------------
  // Style Waze (glow + base + dash anime) - identique field.js
  // ---------------------------------------------------------------------

  function _stopRouteAnimation() {
    if (STATE.routeAnimFrame) {
      cancelAnimationFrame(STATE.routeAnimFrame);
      STATE.routeAnimFrame = null;
    }
  }

  function _startRouteAnimation(dashLine, god) {
    _stopRouteAnimation();
    if (!dashLine) return;
    var speed = god ? 0.9 : 0.4;
    var offset = 0;
    function tick() {
      offset = (offset - speed) % 24;
      var el = dashLine.getElement ? dashLine.getElement() : null;
      if (el) el.style.strokeDashoffset = String(offset);
      STATE.routeAnimFrame = requestAnimationFrame(tick);
    }
    STATE.routeAnimFrame = requestAnimationFrame(tick);
  }

  function _drawWazeRoute(layerGroup, pts, god) {
    var color = "#2563eb";
    if (god) {
      L.polyline(pts, {
        color: "#f59e0b", weight: 24, opacity: 0.35,
        lineCap: "round", lineJoin: "round", interactive: false,
      }).addTo(layerGroup);
    }
    L.polyline(pts, {
      color: color, weight: god ? 18 : 14, opacity: god ? 0.32 : 0.20,
      lineCap: "round", lineJoin: "round", interactive: false,
    }).addTo(layerGroup);
    L.polyline(pts, {
      color: color, weight: 5, opacity: 0.95,
      lineCap: "round", lineJoin: "round", interactive: false,
    }).addTo(layerGroup);
    var dashLine = L.polyline(pts, {
      color: "#ffffff", weight: 3, opacity: 0.75,
      dashArray: "8 16", dashOffset: "0",
      lineCap: "round", lineJoin: "round", interactive: false,
    });
    dashLine.addTo(layerGroup);
    _startRouteAnimation(dashLine, god);
  }

  function _decodePolyline6(encoded) {
    if (!encoded) return [];
    var pts = [];
    var idx = 0, lat = 0, lon = 0, n = encoded.length;
    while (idx < n) {
      for (var axis = 0; axis < 2; axis++) {
        var shift = 0, result = 0, b;
        while (true) {
          if (idx >= n) return pts;
          b = encoded.charCodeAt(idx) - 63;
          idx++;
          result |= (b & 0x1f) << shift;
          shift += 5;
          if (b < 0x20) break;
        }
        var d = (result & 1) ? ~(result >>> 1) : (result >>> 1);
        if (axis === 0) lat += d; else lon += d;
      }
      pts.push([lat / 1e6, lon / 1e6]);
    }
    return pts;
  }

  function _csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return (m && m.content) || "";
  }

  function _api(url, payload) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": _csrf() },
      body: JSON.stringify(payload || {}),
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, json: j }; });
    });
  }

  function _toast(msg) {
    if (typeof window.showToast === "function") { window.showToast(msg); return; }
    if (typeof window.toast === "function") { window.toast(msg); return; }
    console.log("[routing]", msg);
  }

  function _formatMinutes(seconds) {
    var m = Math.max(1, Math.round((seconds || 0) / 60));
    if (m < 60) return m + " min";
    var h = Math.floor(m / 60);
    var rem = m % 60;
    return h + " h " + (rem < 10 ? "0" : "") + rem;
  }

  // -----------------------------------------------------------------------
  // DOM construction
  // -----------------------------------------------------------------------

  function _buildOverlay() {
    var overlay = document.createElement("div");
    overlay.id = "routing-modal-overlay";
    overlay.className = "routing-modal-overlay";

    var modal = document.createElement("div");
    modal.className = "routing-modal";

    var header = document.createElement("div");
    header.className = "routing-modal-header";
    var hTitle = document.createElement("div");
    hTitle.className = "routing-modal-title";
    var hIco = document.createElement("span");
    hIco.className = "material-symbols-outlined";
    hIco.textContent = "route";
    hTitle.appendChild(hIco);
    var hTxt = document.createElement("span");
    hTxt.textContent = "Itineraire";
    hTitle.appendChild(hTxt);
    header.appendChild(hTitle);
    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "routing-modal-close";
    closeBtn.setAttribute("aria-label", "Fermer");
    var closeIco = document.createElement("span");
    closeIco.className = "material-symbols-outlined";
    closeIco.textContent = "close";
    closeBtn.appendChild(closeIco);
    header.appendChild(closeBtn);
    modal.appendChild(header);

    var body = document.createElement("div");
    body.className = "routing-modal-body";

    // Stats row
    var stats = document.createElement("div");
    stats.className = "routing-stats";
    stats.id = "routing-stats";
    body.appendChild(stats);

    // Map
    var mapDiv = document.createElement("div");
    mapDiv.id = "routing-map";
    mapDiv.className = "routing-map";
    body.appendChild(mapDiv);

    // Controls
    var ctrls = document.createElement("div");
    ctrls.className = "routing-controls";

    var prio = document.createElement("label");
    prio.className = "routing-priority";
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.id = "routing-priority-cb";
    var prioIco = document.createElement("span");
    prioIco.className = "material-symbols-outlined";
    prioIco.style.color = "#dc2626";
    prioIco.textContent = "emergency";
    var prioTxt = document.createElement("span");
    prioTxt.textContent = "Intervention prioritaire (gyrophare)";
    prio.appendChild(cb);
    prio.appendChild(prioIco);
    prio.appendChild(prioTxt);
    ctrls.appendChild(prio);

    var btnRow = document.createElement("div");
    btnRow.className = "routing-btn-row";

    function mkBtn(id, label, iconName, cls) {
      var b = document.createElement("button");
      b.type = "button";
      b.id = id;
      b.className = "routing-btn " + (cls || "");
      var ic = document.createElement("span");
      ic.className = "material-symbols-outlined";
      ic.textContent = iconName;
      b.appendChild(ic);
      var lbl = document.createElement("span");
      lbl.textContent = label;
      b.appendChild(lbl);
      return b;
    }

    btnRow.appendChild(mkBtn("routing-btn-recalc", "Recalculer", "refresh", "routing-btn-secondary"));
    btnRow.appendChild(mkBtn("routing-btn-pick", "Forcer un point", "add_location", "routing-btn-secondary"));
    btnRow.appendChild(mkBtn("routing-btn-reset", "Reset", "restart_alt", "routing-btn-secondary"));
    btnRow.appendChild(mkBtn("routing-btn-send", "Envoyer a la tablette", "send", "routing-btn-primary"));
    ctrls.appendChild(btnRow);

    body.appendChild(ctrls);
    modal.appendChild(body);
    overlay.appendChild(modal);

    // Wire close
    closeBtn.addEventListener("click", function () { close(); });
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });

    return overlay;
  }

  // -----------------------------------------------------------------------
  // Overrides admin (transparence carte)
  // -----------------------------------------------------------------------

  function _loadOverrides() {
    if (STATE.overridesLoaded) return;
    STATE.overridesLoaded = true;
    fetch("/api/routing-overrides/active", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || j.ok === false || !STATE.overridesLayer) return;
        (j.items || []).forEach(_renderOverride);
      })
      .catch(function () { /* silencieux : visu non critique */ });
  }

  function _renderOverride(it) {
    if (!STATE.overridesLayer) return;
    var color = it.type === "force_open" ? "#16a34a" : "#dc2626";
    var iconName = it.type === "force_open" ? "door_open" : "block";
    var label = (it.label || "(sans libelle)")
      + (it.scope === "god_only" ? " (intervention seule)"
        : it.scope === "normal_only" ? " (normal seul)" : "");

    if (it.type === "block_point" || it.type === "force_open") {
      if (it.lat == null || it.lon == null) return;
      L.marker([it.lat, it.lon], {
        icon: L.divIcon({
          className: "",
          html: "<div class='rov-pin' style='background:" + color + "; opacity:0.85; width:22px; height:22px;'>"
            + "<span class='material-symbols-outlined' style='font-size:13px;'>" + iconName + "</span></div>",
          iconSize: [22, 22], iconAnchor: [11, 11],
        }),
      }).bindTooltip(label).addTo(STATE.overridesLayer);
    } else if (it.type === "block_polygon") {
      var ring = (it.coords || []).map(function (c) { return [c[1], c[0]]; });
      if (ring.length < 3) return;
      L.polygon(ring, {
        color: color, fillColor: color,
        fillOpacity: 0.15, weight: 1.5, opacity: 0.7,
      }).bindTooltip(label).addTo(STATE.overridesLayer);
    }
  }

  // -----------------------------------------------------------------------
  // Map / polyline rendering
  // -----------------------------------------------------------------------

  function _initMap() {
    if (STATE.map) return;
    var mapDiv = document.getElementById("routing-map");
    if (!mapDiv) return;
    var center = STATE.from || STATE.to || [47.9517, 0.2247];
    STATE.map = L.map(mapDiv, { zoomControl: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap"
    }).addTo(STATE.map);
    STATE.map.setView(center, 14);
    // overridesLayer en-dessous de mapLayers (route plus visible)
    STATE.overridesLayer = L.layerGroup().addTo(STATE.map);
    STATE.mapLayers = L.layerGroup().addTo(STATE.map);

    _loadOverrides();

    STATE.map.on("click", function (e) {
      if (!STATE.waypointPickMode) return;
      STATE.waypoints.push([e.latlng.lat, e.latlng.lng]);
      STATE.waypointPickMode = false;
      var btn = document.getElementById("routing-btn-pick");
      if (btn) btn.classList.remove("active");
      _recalc();
    });

    // Force resize une fois visible
    setTimeout(function () { if (STATE.map) STATE.map.invalidateSize(); }, 50);
  }

  function _renderRoute(result) {
    if (!STATE.mapLayers) return;
    _stopRouteAnimation();
    STATE.mapLayers.clearLayers();
    STATE.lastResult = result;

    var pts = result && result.polyline ? _decodePolyline6(result.polyline) : null;

    if (STATE.from) {
      L.circleMarker(STATE.from, {
        radius: 8, fillColor: "#16a34a", color: "#ffffff",
        weight: 2, fillOpacity: 1, opacity: 1
      }).bindTooltip("Vehicule" + (STATE.deviceName ? " : " + STATE.deviceName : ""))
        .addTo(STATE.mapLayers);
    }
    if (STATE.to) {
      L.marker(STATE.to, {
        icon: L.divIcon({
          className: "",
          html: "<div class='routing-dest-pin'><span class='material-symbols-outlined'>place</span></div>",
          iconSize: [32, 32], iconAnchor: [16, 30],
        })
      }).bindTooltip("Intervention").addTo(STATE.mapLayers);
    }

    STATE.waypoints.forEach(function (wp, i) {
      L.circleMarker(wp, {
        radius: 6, fillColor: "#f59e0b", color: "#ffffff",
        weight: 2, fillOpacity: 1, opacity: 1
      }).bindTooltip("Point force " + (i + 1)).addTo(STATE.mapLayers);
    });

    var bounds = null;
    if (pts && pts.length >= 2) {
      _drawWazeRoute(STATE.mapLayers, pts, !!STATE.god);
      bounds = L.latLngBounds(pts);
    } else if (STATE.from && STATE.to) {
      L.polyline([STATE.from, STATE.to], {
        color: "#2563eb", weight: 3, opacity: 0.5,
        dashArray: "6 8", interactive: false
      }).addTo(STATE.mapLayers);
      bounds = L.latLngBounds([STATE.from, STATE.to]);
    }

    if (bounds) {
      try {
        STATE.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 17 });
      } catch (e) { /* ignore */ }
    }

    _renderStats(result);
  }

  function _renderStats(result) {
    var stats = document.getElementById("routing-stats");
    if (!stats) return;
    while (stats.firstChild) stats.removeChild(stats.firstChild);

    function pill(label, value, color) {
      var p = document.createElement("div");
      p.className = "routing-stat-pill";
      if (color) p.style.borderColor = color;
      var l = document.createElement("span");
      l.className = "routing-stat-label";
      l.textContent = label;
      var v = document.createElement("span");
      v.className = "routing-stat-value";
      v.textContent = value;
      if (color) v.style.color = color;
      p.appendChild(l);
      p.appendChild(v);
      return p;
    }

    if (STATE.deviceName) {
      stats.appendChild(pill("Vehicule", STATE.deviceName));
    }
    if (result) {
      var km = ((result.distance_m || 0) / 1000).toFixed(1);
      stats.appendChild(pill("Distance", km + " km"));
      stats.appendChild(pill("ETA", _formatMinutes(result.duration_s || 0)));
      if (result.waze_avoided && result.waze_avoided > 0) {
        stats.appendChild(pill("Waze", result.waze_avoided + " evite", "#f59e0b"));
      }
      if (result.engine === "stub") {
        stats.appendChild(pill("Moteur", "estime", "#9ca3af"));
      }
      if (result.mode === "god") {
        stats.appendChild(pill("Mode", "prioritaire", "#dc2626"));
      }
    }
  }

  // -----------------------------------------------------------------------
  // Workflow
  // -----------------------------------------------------------------------

  function _recalc() {
    var payload = {
      fiche_id: STATE.ficheId,
      god: !!STATE.god,
    };
    if (STATE.waypoints.length) payload.waypoints = STATE.waypoints;

    var sendBtn = document.getElementById("routing-btn-send");
    if (sendBtn) sendBtn.disabled = true;

    return _api("/api/route", payload).then(function (resp) {
      if (!resp.ok || !resp.json || resp.json.ok === false) {
        var err = (resp.json && resp.json.error) || ("Erreur " + (resp.status || "inconnue"));
        _toast("Calcul itineraire: " + err);
        return;
      }
      var j = resp.json;
      // Le serveur peut completer from/to/device si on est passe par fiche_id
      if (j.from && j.from.length === 2) STATE.from = [parseFloat(j.from[0]), parseFloat(j.from[1])];
      if (j.to && j.to.length === 2) STATE.to = [parseFloat(j.to[0]), parseFloat(j.to[1])];
      if (j.device_id) STATE.deviceId = j.device_id;
      if (j.device_name) STATE.deviceName = j.device_name;
      if (j.event) STATE.event = j.event;
      if (j.year) STATE.year = j.year;
      _renderRoute(j);
      if (sendBtn) sendBtn.disabled = !STATE.deviceId;
    }).catch(function (e) {
      _toast("Calcul itineraire: " + ((e && e.message) || "reseau indisponible"));
    });
  }

  function _send() {
    if (!STATE.lastResult || !STATE.deviceId) {
      _toast("Aucun itineraire calcule a envoyer");
      return;
    }
    var payload = {
      device_id: STATE.deviceId,
      event: STATE.event,
      year: STATE.year,
      polyline: STATE.lastResult.polyline,
      distance_m: STATE.lastResult.distance_m,
      duration_s: STATE.lastResult.duration_s,
      from: STATE.from,
      to: STATE.to,
      waypoints: STATE.waypoints,
      god: !!STATE.god,
      title: "Itineraire (PC org)",
      body: "Itineraire envoye depuis le PC org. " +
        ((STATE.lastResult.distance_m / 1000).toFixed(1)) + " km, " +
        _formatMinutes(STATE.lastResult.duration_s) + ".",
    };
    var btn = document.getElementById("routing-btn-send");
    if (btn) { btn.disabled = true; btn.classList.add("loading"); }
    _api("/api/route/forward", payload).then(function (resp) {
      if (btn) { btn.disabled = false; btn.classList.remove("loading"); }
      if (!resp.ok || !resp.json || resp.json.ok === false) {
        var err = (resp.json && resp.json.error) || ("Erreur " + (resp.status || "inconnue"));
        _toast("Envoi itineraire: " + err);
        return;
      }
      _toast("Itineraire envoye a " + (STATE.deviceName || "la tablette"));
    }).catch(function (e) {
      if (btn) { btn.disabled = false; btn.classList.remove("loading"); }
      _toast("Envoi itineraire: " + ((e && e.message) || "reseau indisponible"));
    });
  }

  // -----------------------------------------------------------------------
  // Public API
  // -----------------------------------------------------------------------

  function close() {
    _stopRouteAnimation();
    if (STATE.map) {
      try { STATE.map.remove(); } catch (e) { /* ignore */ }
      STATE.map = null;
      STATE.mapLayers = null;
      STATE.overridesLayer = null;
      STATE.overridesLoaded = false;
    }
    if (STATE.overlay && STATE.overlay.parentNode) {
      STATE.overlay.parentNode.removeChild(STATE.overlay);
    }
    STATE.overlay = null;
    STATE.ficheId = null;
    STATE.deviceId = null;
    STATE.deviceName = null;
    STATE.event = null;
    STATE.year = null;
    STATE.from = null;
    STATE.to = null;
    STATE.waypoints = [];
    STATE.god = false;
    STATE.lastResult = null;
    STATE.waypointPickMode = false;
  }

  function openForFiche(ficheId, deviceName) {
    if (!ficheId) { _toast("Aucune fiche selectionnee"); return; }
    close();
    STATE.ficheId = String(ficheId);
    STATE.deviceName = deviceName || null;

    var overlay = _buildOverlay();
    STATE.overlay = overlay;
    document.body.appendChild(overlay);

    _initMap();

    // Wire control buttons (apres injection DOM)
    var cb = document.getElementById("routing-priority-cb");
    if (cb) {
      cb.addEventListener("change", function () {
        STATE.god = !!cb.checked;
        _recalc();
      });
    }
    var btnRecalc = document.getElementById("routing-btn-recalc");
    if (btnRecalc) btnRecalc.addEventListener("click", _recalc);
    var btnPick = document.getElementById("routing-btn-pick");
    if (btnPick) {
      btnPick.addEventListener("click", function () {
        STATE.waypointPickMode = !STATE.waypointPickMode;
        btnPick.classList.toggle("active", STATE.waypointPickMode);
        _toast(STATE.waypointPickMode ? "Cliquer un point sur la carte" : "Selection annulee");
      });
    }
    var btnReset = document.getElementById("routing-btn-reset");
    if (btnReset) {
      btnReset.addEventListener("click", function () {
        STATE.waypoints = [];
        _recalc();
      });
    }
    var btnSend = document.getElementById("routing-btn-send");
    if (btnSend) {
      btnSend.disabled = true;
      btnSend.addEventListener("click", _send);
    }

    _recalc();
  }

  window.RoutingModal = { openForFiche: openForFiche, close: close };
})();
