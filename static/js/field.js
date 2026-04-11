/* =====================================================================
   COCKPIT Field - App terrain pour tablettes patrouille
   Expose en global : window.FieldApp
   ===================================================================== */
(function () {
  "use strict";

  var DEFAULT_CENTER = [47.938561591531936, 0.2243184111156285];
  var DEFAULT_ZOOM = 14;

  var POLL_INBOX_MS = 3000;
  var POLL_FICHES_MS = 15000;       // fiches PCORG assignees : toutes les 15s
  var POSITION_PUSH_MS = 5000;     // push GPS toutes les 5s max
  var POSITION_MIN_MOVE_M = 5;     // ou si on bouge de plus de 5m

  // ---------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------
  var state = {
    map: null,
    tileLayers: {},
    currentLayerKey: "plan",
    layerOrder: ["plan", "sat_esri", "sat_aco"],
    meMarker: null,
    meCircle: null,
    lastPushedAt: 0,
    lastPushedPos: null,
    followMe: true,
    gridOn: false,
    gridLayer: null,
    gridData: null,
    gridMeta: null,            // {cols, rows, hLines, vLines, ...}
    grid25On: false,
    grid25Layer: null,
    grid25Meta: null,
    gridStickyEl: null,        // wrap DOM
    gridPolylines: [],         // current 100m polylines (for weight tweak)
    threePOn: false,
    threePLayer: null,
    threePLoaded: false,
    poiCategories: [],         // [{label, dataKey, collection, icon, ...}]
    poiLayers: {},             // dataKey -> {layer, loaded}
    routeLayer: null,
    routeDestination: null,  // [lat, lng]
    fichesLayer: null,
    fichesMarkers: {},       // id -> marker
    fiches: [],
    fichesTimer: null,
    seenFicheIds: new Set(),
    sosInFlight: false,
    inbox: [],
    seenIds: new Set(),
    watchId: null,
    clockTimer: null,
    inboxTimer: null,
    // Outils de mesure
    measureMode: null,
    measureLayer: null,
    measurePoints: [],
    measureGuide: null,
    measureLabels: [],
    measureFinalized: false,
    // Crosshair grille (touch)
    crosshairTimer: null,
  };

  // ---------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------
  function $(id) { return document.getElementById(id); }

  function haversine(a, b) {
    if (!a || !b) return Infinity;
    var R = 6371000;
    var dLat = (b[0] - a[0]) * Math.PI / 180;
    var dLng = (b[1] - a[1]) * Math.PI / 180;
    var s = Math.sin(dLat / 2) * Math.sin(dLat / 2)
          + Math.cos(a[0] * Math.PI / 180) * Math.cos(b[0] * Math.PI / 180)
          * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
  }

  function formatClock(d) {
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  function toast(msg, type) {
    var el = $("field-toast");
    if (!el) return;
    el.textContent = msg;
    el.className = "field-toast" + (type ? " " + type : "");
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.hidden = true; }, 2500);
  }

  // ---------------------------------------------------------------------
  // Session perdue (tablette revoquee/supprimee cote admin)
  // Plutot que de naviguer brutalement vers /field/pair (qui declenche
  // ERR_FAILED si le service worker n'a pas pu cacher la page), on affiche
  // un overlay plein ecran. Toutes les requetes 401 passent par ici.
  // ---------------------------------------------------------------------
  var _sessionLost = false;
  var _sessionLostPoll = null;

  function stopAllPolling() {
    try {
      if (state.fichesTimer) { clearInterval(state.fichesTimer); state.fichesTimer = null; }
      if (state.inboxTimer) { clearInterval(state.inboxTimer); state.inboxTimer = null; }
      if (state.clockTimer) { clearInterval(state.clockTimer); state.clockTimer = null; }
      if (state.crosshairTimer) { clearTimeout(state.crosshairTimer); state.crosshairTimer = null; }
      if (state.watchId != null && navigator.geolocation) {
        navigator.geolocation.clearWatch(state.watchId);
        state.watchId = null;
      }
    } catch (e) { /* noop */ }
  }

  function handleSessionLost() {
    if (_sessionLost) return null;
    _sessionLost = true;
    stopAllPolling();

    var overlay = document.createElement("div");
    overlay.className = "field-session-lost";
    overlay.id = "field-session-lost";

    var box = document.createElement("div");
    box.className = "field-session-lost-box";

    var icon = document.createElement("div");
    icon.className = "field-session-lost-icon material-symbols-outlined";
    icon.textContent = "block";
    box.appendChild(icon);

    var title = document.createElement("div");
    title.className = "field-session-lost-title";
    title.textContent = "Session terminee";
    box.appendChild(title);

    var sub = document.createElement("div");
    sub.className = "field-session-lost-sub";
    sub.textContent = "Cette tablette n'est plus autorisee. Contactez le PC pour reactiver l'acces.";
    box.appendChild(sub);

    var status = document.createElement("div");
    status.className = "field-session-lost-status";
    status.id = "field-session-lost-status";
    status.textContent = "Verification automatique...";
    box.appendChild(status);

    var actions = document.createElement("div");
    actions.className = "field-session-lost-actions";

    var btnRetry = document.createElement("button");
    btnRetry.className = "btn-primary";
    btnRetry.textContent = "Verifier maintenant";
    btnRetry.addEventListener("click", function () { checkSessionRestored(true); });
    actions.appendChild(btnRetry);

    var btnRePair = document.createElement("button");
    btnRePair.className = "btn-primary btn-cancel";
    btnRePair.textContent = "Re-appairer la tablette";
    btnRePair.addEventListener("click", function () {
      // Navigation manuelle - ici l'utilisateur l'a explicitement demandee
      // donc le ERR_FAILED eventuel est attendu / acceptable.
      try { window.location.href = "/field/pair"; } catch (e) {}
    });
    actions.appendChild(btnRePair);

    box.appendChild(actions);
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    // Polling automatique pour reprendre la session des qu'elle est restauree.
    _sessionLostPoll = setInterval(function () { checkSessionRestored(false); }, 6000);
    // Premiere verification rapide
    setTimeout(function () { checkSessionRestored(false); }, 1500);

    return null;
  }

  function checkSessionRestored(manual) {
    var status = document.getElementById("field-session-lost-status");
    if (manual && status) status.textContent = "Verification...";
    fetch("/field/denied/check", { headers: { "Accept": "application/json" }, cache: "no-store" })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (d) {
        if (d && d.status === "active") {
          if (status) status.textContent = "Session restauree, rechargement...";
          if (_sessionLostPoll) { clearInterval(_sessionLostPoll); _sessionLostPoll = null; }
          // Reload doux : on recharge la page d'application qui repartira sur
          // une session valide. Pas de ERR_FAILED car /field est cachee par
          // le service worker.
          setTimeout(function () { window.location.reload(); }, 600);
        } else if (manual && status) {
          status.textContent = "Toujours non autorisee.";
        }
      })
      .catch(function () {
        if (manual && status) status.textContent = "Reseau indisponible.";
      });
  }

  // ---- Mini modal confirm/prompt (remplace window.confirm/prompt) ----
  function fieldConfirm(message, opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var overlay = document.createElement("div");
      overlay.className = "field-modal field-mini-modal";
      var box = document.createElement("div");
      box.className = "field-modal-box";
      var body = document.createElement("div");
      body.className = "field-modal-body";
      body.textContent = message;
      var actions = document.createElement("div");
      actions.className = "field-modal-actions";
      var btnNo = document.createElement("button");
      btnNo.className = "btn-primary btn-cancel";
      btnNo.textContent = opts.cancelLabel || "Annuler";
      var btnOk = document.createElement("button");
      btnOk.className = "btn-primary";
      btnOk.textContent = opts.okLabel || "Confirmer";
      actions.appendChild(btnNo);
      actions.appendChild(btnOk);
      box.appendChild(body);
      box.appendChild(actions);
      overlay.appendChild(box);
      document.body.appendChild(overlay);
      function done(result) {
        try { document.body.removeChild(overlay); } catch (e) {}
        resolve(result);
      }
      btnNo.addEventListener("click", function () { done(false); });
      btnOk.addEventListener("click", function () { done(true); });
    });
  }

  function fieldPrompt(message, opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var overlay = document.createElement("div");
      overlay.className = "field-modal field-mini-modal";
      var box = document.createElement("div");
      box.className = "field-modal-box";
      var body = document.createElement("div");
      body.className = "field-modal-body";
      var p = document.createElement("div");
      p.textContent = message;
      p.style.marginBottom = "10px";
      body.appendChild(p);
      var input = document.createElement("input");
      input.type = "text";
      input.value = opts.defaultValue || "";
      input.style.cssText = "width:100%; padding:10px 12px; font-size:15px; border-radius:8px; background:#1e293b; border:1px solid #334155; color:#f1f5f9; font-family:inherit;";
      body.appendChild(input);
      var actions = document.createElement("div");
      actions.className = "field-modal-actions";
      var btnNo = document.createElement("button");
      btnNo.className = "btn-primary btn-cancel";
      btnNo.textContent = opts.cancelLabel || "Annuler";
      var btnOk = document.createElement("button");
      btnOk.className = "btn-primary";
      btnOk.textContent = opts.okLabel || "OK";
      actions.appendChild(btnNo);
      actions.appendChild(btnOk);
      box.appendChild(body);
      box.appendChild(actions);
      overlay.appendChild(box);
      document.body.appendChild(overlay);
      setTimeout(function () { try { input.focus(); } catch (e) {} }, 30);
      function done(result) {
        try { document.body.removeChild(overlay); } catch (e) {}
        resolve(result);
      }
      btnNo.addEventListener("click", function () { done(null); });
      btnOk.addEventListener("click", function () { done(input.value); });
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); done(input.value); }
        if (e.key === "Escape") { e.preventDefault(); done(null); }
      });
    });
  }

  // ---------------------------------------------------------------------
  // Map
  // ---------------------------------------------------------------------
  function initMap() {
    state.map = L.map("field-map", {
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      minZoom: 10,
      maxZoom: 22,
      zoomControl: true,
      attributionControl: true,
      doubleClickZoom: false,
    });

    state.tileLayers.plan = L.tileLayer(
      "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { attribution: "&copy; OSM", maxNativeZoom: 19, maxZoom: 22 }
    );
    state.tileLayers.sat_esri = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "&copy; Esri", maxNativeZoom: 19, maxZoom: 22 }
    );
    state.tileLayers.sat_aco = L.tileLayer(
      "/field/resources/tiles/{z}/{x}/{y}.png",
      { tms: true, maxZoom: 22, attribution: "ACO" }
    );

    state.tileLayers.plan.addTo(state.map);
    state.currentLayerKey = "plan";

    // Stop suivre "moi" des qu'on touche la carte manuellement
    state.map.on("dragstart zoomstart", function (e) {
      if (e.originalEvent) state.followMe = false;
    });
  }

  function cycleLayer() {
    var idx = state.layerOrder.indexOf(state.currentLayerKey);
    var nextIdx = (idx + 1) % state.layerOrder.length;
    var nextKey = state.layerOrder[nextIdx];
    Object.keys(state.tileLayers).forEach(function (k) {
      if (state.map.hasLayer(state.tileLayers[k])) {
        state.map.removeLayer(state.tileLayers[k]);
      }
    });
    state.tileLayers[nextKey].addTo(state.map);
    state.currentLayerKey = nextKey;
    var labelByKey = { plan: "Plan", sat_esri: "Satellite", sat_aco: "Satellite ACO" };
    toast(labelByKey[nextKey] || nextKey);
  }

  // ---------------------------------------------------------------------
  // GPS
  // ---------------------------------------------------------------------
  function setGpsStatus(status) {
    var el = $("gps-status");
    if (!el) return;
    el.classList.remove("ok", "warn", "err");
    if (status === "ok") el.classList.add("ok");
    else if (status === "warn") el.classList.add("warn");
    else if (status === "err") el.classList.add("err");
  }

  function startGeolocation() {
    if (!navigator.geolocation) {
      setGpsStatus("err");
      toast("Geolocalisation non supportee", "err");
      return;
    }
    setGpsStatus("warn");
    try {
      state.watchId = navigator.geolocation.watchPosition(
        onGpsUpdate,
        onGpsError,
        { enableHighAccuracy: true, maximumAge: 2000, timeout: 20000 }
      );
    } catch (e) {
      setGpsStatus("err");
    }
  }

  function onGpsUpdate(pos) {
    setGpsStatus("ok");
    var latlng = [pos.coords.latitude, pos.coords.longitude];
    renderMeMarker(latlng, pos.coords.accuracy);
    maybePushPosition(pos);
  }

  function onGpsError(err) {
    setGpsStatus("err");
    if (err && err.code === 1) {
      toast("Permission GPS refusee", "err");
    }
  }

  function renderMeMarker(latlng, accuracy) {
    if (!state.meMarker) {
      var icon = L.divIcon({
        className: "",
        html: "<div class='me-marker'></div>",
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      });
      state.meMarker = L.marker(latlng, { icon: icon, interactive: false }).addTo(state.map);
      state.meCircle = L.circle(latlng, {
        radius: accuracy || 20,
        className: "me-accuracy",
      }).addTo(state.map);
      if (state.followMe) state.map.setView(latlng, Math.max(state.map.getZoom(), 17));
    } else {
      state.meMarker.setLatLng(latlng);
      if (state.meCircle) state.meCircle.setLatLng(latlng).setRadius(accuracy || 20);
      if (state.followMe) state.map.panTo(latlng, { animate: true, duration: 0.3 });
    }
  }

  function recenter() {
    state.followMe = true;
    if (state.meMarker) {
      state.map.setView(state.meMarker.getLatLng(), Math.max(state.map.getZoom(), 18));
    } else {
      state.map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
    }
  }

  function maybePushPosition(pos) {
    var now = Date.now();
    var latlng = [pos.coords.latitude, pos.coords.longitude];
    var elapsed = now - state.lastPushedAt;
    var moved = haversine(state.lastPushedPos, latlng);
    if (elapsed < POSITION_PUSH_MS && moved < POSITION_MIN_MOVE_M) return;
    state.lastPushedAt = now;
    state.lastPushedPos = latlng;
    var body = {
      lat: latlng[0],
      lng: latlng[1],
      accuracy: pos.coords.accuracy,
      speed: pos.coords.speed,
      heading: pos.coords.heading,
      battery: state.batteryPct || null,
      ts: now,
    };
    if (!navigator.onLine) {
      bufferPosition(body);
      return;
    }
    fetch("/field/position", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(function () {
      // Si l'envoi echoue, on stocke pour plus tard
      bufferPosition(body);
    });
  }

  // ---------------------------------------------------------------------
  // Clock / battery / network
  // ---------------------------------------------------------------------
  function startClock() {
    function tick() {
      var el = $("field-clock");
      if (el) el.textContent = formatClock(new Date());
    }
    tick();
    state.clockTimer = setInterval(tick, 1000);
  }

  function setNetStatus(online) {
    var el = $("net-status");
    if (!el) return;
    el.classList.remove("ok", "warn", "err");
    if (online) el.classList.add("ok");
    else el.classList.add("err");
    var icon = el.querySelector(".material-symbols-outlined");
    if (icon) icon.textContent = online ? "signal_wifi_4_bar" : "signal_wifi_off";
  }

  function initNetStatus() {
    setNetStatus(navigator.onLine);
    window.addEventListener("online", function () { setNetStatus(true); });
    window.addEventListener("offline", function () { setNetStatus(false); });
  }

  function initBattery() {
    if (!navigator.getBattery) return;
    navigator.getBattery().then(function (bat) {
      function update() {
        var pct = Math.round((bat.level || 0) * 100);
        state.batteryPct = pct;
        var el = $("battery-pct");
        if (el) el.textContent = pct + "%";
        var pill = $("battery-status");
        if (pill) {
          pill.classList.remove("ok", "warn", "err");
          if (pct <= 15) pill.classList.add("err");
          else if (pct <= 30) pill.classList.add("warn");
          else pill.classList.add("ok");
          var icon = pill.querySelector(".material-symbols-outlined");
          if (icon) {
            if (pct >= 90) icon.textContent = "battery_full";
            else if (pct >= 60) icon.textContent = "battery_5_bar";
            else if (pct >= 40) icon.textContent = "battery_3_bar";
            else if (pct >= 20) icon.textContent = "battery_2_bar";
            else icon.textContent = "battery_alert";
          }
        }
      }
      update();
      bat.addEventListener("levelchange", update);
      bat.addEventListener("chargingchange", update);
    }).catch(function () { /* api absente */ });
  }

  // ---------------------------------------------------------------------
  // Ressources carte : carroyage + 3P
  // ---------------------------------------------------------------------
  // Helper colonne A..Z, AA..
  function colLabel(idx) {
    if (idx < 26) return String.fromCharCode(65 + idx);
    return String.fromCharCode(65 + Math.floor(idx / 26) - 1) + String.fromCharCode(65 + (idx % 26));
  }

  function loadGrid() {
    if (state.gridData) {
      renderGrid();
      return;
    }
    fetch("/field/resources/grid-ref", { headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (r.status === 401) { return handleSessionLost(); }
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.lines) {
          toast("Carroyage indisponible", "warn");
          return;
        }
        state.gridData = data;
        renderGrid();
      })
      .catch(function () { toast("Echec chargement carroyage", "err"); });
  }

  function renderGrid() {
    if (!state.gridData || !state.gridData.lines) return;
    if (state.gridLayer) {
      state.map.removeLayer(state.gridLayer);
      state.gridLayer = null;
    }
    var lines = state.gridData.lines;
    var hLines = lines.h_lines || [];
    var vLines = lines.v_lines || [];
    var numCols = lines.num_cols || (vLines.length - 1);
    var numRows = lines.num_rows || (hLines.length - 1);
    var colOffset = lines.col_offset || 0;
    var rowOffset = lines.row_offset || 0;

    var group = L.layerGroup();
    var polylines = [];
    hLines.forEach(function (l) {
      var pl = L.polyline(
        [[l.lat, l.lng_start], [l.lat, l.lng_end]],
        { color: "#f59e0b", weight: 1.4, opacity: 0.75, interactive: false }
      );
      pl.addTo(group);
      polylines.push(pl);
    });
    vLines.forEach(function (l) {
      var pl = L.polyline(
        [[l.lat_start, l.lng], [l.lat_end, l.lng]],
        { color: "#f59e0b", weight: 1.4, opacity: 0.75, interactive: false }
      );
      pl.addTo(group);
      polylines.push(pl);
    });
    group.addTo(state.map);
    state.gridLayer = group;
    state.gridPolylines = polylines;

    // Compute meta (col labels, row numbers, centers)
    var cols = [];
    for (var ci = 0; ci < numCols; ci++) {
      var adj = ci - colOffset;
      cols.push(adj >= 0 ? colLabel(adj) : null);
    }
    var rows = [];
    for (var ri = 0; ri < numRows; ri++) {
      var rn = ri + 1 - rowOffset;
      rows.push(rn >= 1 ? rn : null);
    }
    var colCenters = [];
    for (var c = 0; c < numCols; c++) {
      colCenters.push((vLines[c].lng + vLines[c + 1].lng) / 2);
    }
    var rowCenters = [];
    for (var r = 0; r < numRows; r++) {
      rowCenters.push((hLines[r].lat + hLines[r + 1].lat) / 2);
    }
    state.gridMeta = {
      cols: cols, rows: rows,
      colCenters: colCenters, rowCenters: rowCenters,
      hLines: hLines, vLines: vLines,
      numCols: numCols, numRows: numRows,
    };

    buildStickyHeaders();
    state.map.on("move zoom", updateStickyHeaders);

    // Bouton sous-grille (visible si zoom >= 18)
    updateGrid25ButtonVisibility();
    state.map.on("zoomend", updateGrid25ButtonVisibility);
  }

  function clearGrid() {
    if (state.gridLayer) {
      state.map.removeLayer(state.gridLayer);
      state.gridLayer = null;
    }
    state.gridPolylines = [];
    if (state.gridStickyEl) {
      state.gridStickyEl.remove();
      state.gridStickyEl = null;
    }
    state.gridMeta = null;
    state.map.off("move zoom", updateStickyHeaders);
    state.map.off("zoomend", updateGrid25ButtonVisibility);
    clearGrid25();
    var row = $("lyr-grid-25-row");
    if (row) row.hidden = true;
  }

  function toggleGrid(on) {
    var want = (on === undefined) ? !state.gridOn : !!on;
    if (want === state.gridOn) return;
    state.gridOn = want;
    var cb = $("lyr-grid-100");
    if (cb) cb.checked = want;
    if (want) {
      loadGrid();
      toast("Carroyage : on");
    } else {
      clearGrid();
      toast("Carroyage : off");
    }
  }

  // ----- Sticky headers -----
  function buildStickyHeaders() {
    if (state.gridStickyEl) {
      state.gridStickyEl.remove();
      state.gridStickyEl = null;
    }
    if (!state.gridMeta) return;
    var info = activeGridInfo();
    var container = $("field-map");
    if (!container) return;

    var wrap = document.createElement("div");
    wrap.className = "grid-sticky-wrap";

    var top = document.createElement("div");
    top.className = "grid-sticky-top";
    info.cols.forEach(function (lbl, idx) {
      if (!lbl) return;
      var el = document.createElement("span");
      el.className = "grid-sticky-col";
      el.textContent = lbl;
      el.dataset.idx = idx;
      top.appendChild(el);
    });
    wrap.appendChild(top);

    var left = document.createElement("div");
    left.className = "grid-sticky-left";
    info.rows.forEach(function (lbl, idx) {
      if (!lbl) return;
      var el = document.createElement("span");
      el.className = "grid-sticky-row";
      el.textContent = String(lbl);
      el.dataset.idx = idx;
      left.appendChild(el);
    });
    wrap.appendChild(left);

    var corner = document.createElement("div");
    corner.className = "grid-sticky-corner";
    var ico = document.createElement("span");
    ico.className = "material-symbols-outlined";
    ico.textContent = state.grid25On ? "grid_4x4" : "grid_3x3";
    corner.appendChild(ico);
    wrap.appendChild(corner);

    container.appendChild(wrap);
    state.gridStickyEl = wrap;
    updateStickyHeaders();
  }

  function activeGridInfo() {
    if (state.grid25On && state.grid25Meta) {
      return {
        cols: state.grid25Meta.cols,
        rows: state.grid25Meta.rows,
        colCenters: state.grid25Meta.colCenters,
        rowCenters: state.grid25Meta.rowCenters,
        hLines: state.grid25Meta.hLines,
        vLines: state.grid25Meta.vLines,
        numCols: state.grid25Meta.numCols,
        numRows: state.grid25Meta.numRows,
      };
    }
    return state.gridMeta;
  }

  function updateStickyHeaders() {
    if (!state.gridStickyEl || !state.gridMeta || !state.map) return;
    var info = activeGridInfo();
    var bounds = state.map.getBounds();

    var topEls = state.gridStickyEl.querySelectorAll(".grid-sticky-col");
    topEls.forEach(function (el) {
      var idx = parseInt(el.dataset.idx, 10);
      var lng = info.colCenters[idx];
      if (lng < bounds.getWest() || lng > bounds.getEast()) {
        el.style.display = "none";
        return;
      }
      var pt = state.map.latLngToContainerPoint([info.rowCenters[0] || 0, lng]);
      el.style.left = pt.x + "px";
      el.style.display = "";
    });

    var leftEls = state.gridStickyEl.querySelectorAll(".grid-sticky-row");
    leftEls.forEach(function (el) {
      var idx = parseInt(el.dataset.idx, 10);
      var lat = info.rowCenters[idx];
      if (lat < bounds.getSouth() || lat > bounds.getNorth()) {
        el.style.display = "none";
        return;
      }
      var pt = state.map.latLngToContainerPoint([lat, info.colCenters[0] || 0]);
      el.style.top = pt.y + "px";
      el.style.display = "";
    });
  }

  // ----- 25m sub-grid -----
  function updateGrid25ButtonVisibility() {
    var row = $("lyr-grid-25-row");
    if (!row) return;
    var canShow = state.gridOn && state.map.getZoom() >= 18 && state.gridData && state.gridData.lines_25;
    row.hidden = !canShow;
    if (!canShow && state.grid25On) {
      // Auto-hide sub-grid si on dezoome
      toggleGrid25(false);
    }
  }

  function toggleGrid25(on) {
    var want = (on === undefined) ? !state.grid25On : !!on;
    if (want === state.grid25On) return;
    state.grid25On = want;
    var cb = $("lyr-grid-25");
    if (cb) cb.checked = want;
    if (want) {
      renderGrid25();
    } else {
      clearGrid25();
      buildStickyHeaders();
    }
  }

  function clearGrid25() {
    if (state.grid25Layer) {
      state.map.removeLayer(state.grid25Layer);
      state.grid25Layer = null;
    }
    state.grid25Meta = null;
  }

  function renderGrid25() {
    clearGrid25();
    if (!state.gridData || !state.gridData.lines_25 || !state.gridMeta) return;
    var lines25 = state.gridData.lines_25;
    var hL = lines25.h_lines || [];
    var vL = lines25.v_lines || [];
    var nC = lines25.num_cols || (vL.length - 1);
    var nR = lines25.num_rows || (hL.length - 1);

    var group = L.layerGroup();
    hL.forEach(function (l) {
      L.polyline(
        [[l.lat, l.lng_start], [l.lat, l.lng_end]],
        { color: "#fb923c", weight: 1, opacity: 0.7, dashArray: "6 4", interactive: false }
      ).addTo(group);
    });
    vL.forEach(function (l) {
      L.polyline(
        [[l.lat_start, l.lng], [l.lat_end, l.lng]],
        { color: "#fb923c", weight: 1, opacity: 0.7, dashArray: "6 4", interactive: false }
      ).addTo(group);
    });
    group.addTo(state.map);
    state.grid25Layer = group;

    var colCenters25 = [];
    for (var c = 0; c < nC; c++) {
      colCenters25.push((vL[c].lng + vL[c + 1].lng) / 2);
    }
    var rowCenters25 = [];
    for (var r = 0; r < nR; r++) {
      rowCenters25.push((hL[r].lat + hL[r + 1].lat) / 2);
    }

    var meta100 = state.gridMeta;
    var colLabels25 = [];
    for (var ci = 0; ci < nC; ci++) {
      var lng = colCenters25[ci];
      var pCol = null;
      for (var pi = 0; pi < meta100.numCols; pi++) {
        if (lng >= meta100.vLines[pi].lng && lng < meta100.vLines[pi + 1].lng) {
          pCol = pi; break;
        }
      }
      if (pCol === null || !meta100.cols[pCol]) {
        colLabels25.push(null);
        continue;
      }
      var subIdx = 0;
      for (var si = ci - 1; si >= 0; si--) {
        if (colCenters25[si] < meta100.vLines[pCol].lng) break;
        subIdx++;
      }
      colLabels25.push(meta100.cols[pCol] + String.fromCharCode(65 + Math.min(subIdx, 3)));
    }

    var rowLabels25 = [];
    for (var ri = 0; ri < nR; ri++) {
      var lat = rowCenters25[ri];
      var pRow = null;
      for (var pri = 0; pri < meta100.numRows; pri++) {
        if (lat <= meta100.hLines[pri].lat && lat > meta100.hLines[pri + 1].lat) {
          pRow = pri; break;
        }
      }
      if (pRow === null || !meta100.rows[pRow]) {
        rowLabels25.push(null);
        continue;
      }
      var subRow = 0;
      for (var sri = ri - 1; sri >= 0; sri--) {
        if (rowCenters25[sri] > meta100.hLines[pRow].lat) break;
        subRow++;
      }
      rowLabels25.push(String(meta100.rows[pRow]) + (Math.min(subRow, 3) + 1));
    }

    state.grid25Meta = {
      cols: colLabels25, rows: rowLabels25,
      colCenters: colCenters25, rowCenters: rowCenters25,
      hLines: hL, vLines: vL,
      numCols: nC, numRows: nR,
    };
    buildStickyHeaders();
  }

  // Crosshair : retourne label de la cellule contenant lat/lng
  function getCellLabelAt(lat, lng) {
    var info = activeGridInfo();
    if (!info) return { col: null, row: null };
    var col = null, row = null;
    for (var ci = 0; ci < info.numCols; ci++) {
      if (lng >= info.vLines[ci].lng && lng < info.vLines[ci + 1].lng) { col = ci; break; }
    }
    for (var ri = 0; ri < info.numRows; ri++) {
      if (lat <= info.hLines[ri].lat && lat > info.hLines[ri + 1].lat) { row = ri; break; }
    }
    if (col === null || row === null) return { col: null, row: null };
    return {
      col: col, row: row,
      colLabel: info.cols[col],
      rowLabel: info.rows[row],
    };
  }

  function showCrosshair(latlng) {
    if (!state.gridOn || !state.gridMeta) return;
    var info = activeGridInfo();
    var cell = getCellLabelAt(latlng.lat, latlng.lng);
    if (cell.col === null || cell.row === null) return;
    var ch = $("grid-crosshair");
    var lblEl = $("grid-crosshair-label");
    if (!ch || !lblEl) return;
    // Position des lignes de croix au centre de la cellule cliquee
    var cLng = info.colCenters[cell.col];
    var cLat = info.rowCenters[cell.row];
    var pt = state.map.latLngToContainerPoint([cLat, cLng]);
    var v = ch.querySelector(".gx-line-v");
    var h = ch.querySelector(".gx-line-h");
    if (v) v.style.left = pt.x + "px";
    if (h) h.style.top = pt.y + "px";
    lblEl.textContent = (cell.colLabel || "") + (cell.rowLabel || "");
    lblEl.style.left = pt.x + "px";
    lblEl.style.top = pt.y + "px";
    ch.hidden = false;
    // Highlight des entetes
    var topEl = state.gridStickyEl && state.gridStickyEl.querySelector('.grid-sticky-col[data-idx="' + cell.col + '"]');
    var leftEl = state.gridStickyEl && state.gridStickyEl.querySelector('.grid-sticky-row[data-idx="' + cell.row + '"]');
    if (state.gridStickyEl) {
      state.gridStickyEl.querySelectorAll(".grid-highlight").forEach(function (e) { e.classList.remove("grid-highlight"); });
    }
    if (topEl) topEl.classList.add("grid-highlight");
    if (leftEl) leftEl.classList.add("grid-highlight");
    if (state.crosshairTimer) clearTimeout(state.crosshairTimer);
    state.crosshairTimer = setTimeout(function () {
      if (ch) ch.hidden = true;
      if (state.gridStickyEl) {
        state.gridStickyEl.querySelectorAll(".grid-highlight").forEach(function (e) { e.classList.remove("grid-highlight"); });
      }
    }, 4000);
  }

  // ----- 3P (toggle) -----
  function toggle3P(on) {
    var want = (on === undefined) ? !state.threePOn : !!on;
    if (want === state.threePOn) return;
    state.threePOn = want;
    var cb = $("lyr-3p");
    if (cb) cb.checked = want;
    if (want) {
      load3P();
    } else {
      if (state.threePLayer) {
        state.map.removeLayer(state.threePLayer);
        state.threePLayer = null;
      }
      state.threePLoaded = false;
    }
  }

  function load3P() {
    if (state.threePLoaded && state.threePLayer) {
      // Deja charge : juste reattacher
      state.map.addLayer(state.threePLayer);
      return;
    }
    state.threePLoaded = true;
    fetch("/field/resources/3p", { headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (r.status === 401) { return handleSessionLost(); }
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.features) return;
        render3P(data.features);
      })
      .catch(function () { /* silent */ });
  }

  function render3P(features) {
    if (state.threePLayer) {
      state.map.removeLayer(state.threePLayer);
      state.threePLayer = null;
    }
    var group = L.layerGroup();
    features.forEach(function (f) {
      var coords = (f.geometry || {}).coordinates || [];
      if (coords.length < 2) return;
      var lat = coords[1], lng = coords[0];
      var props = f.properties || {};
      var name = props.Nom || props.Name || props.name || "3P";
      var cm = L.circleMarker([lat, lng], {
        radius: 5,
        color: "#0ea5e9",
        weight: 2,
        fillColor: "#bae6fd",
        fillOpacity: 0.9,
      });
      cm.bindPopup("<b>" + escapeHtml(name) + "</b>");
      cm.addTo(group);
    });
    if (state.threePOn) group.addTo(state.map);
    state.threePLayer = group;
  }

  // ----- POI (groundmaster categories) -----
  function loadPoiCategories() {
    fetch("/field/resources/gm-categories", { headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (r.status === 401) { return handleSessionLost(); }
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.categories) return;
        state.poiCategories = data.categories;
        renderPoiList();
      })
      .catch(function () { /* silent */ });
  }

  function renderPoiList() {
    var list = $("lyr-poi-list");
    if (!list) return;
    list.innerHTML = "";
    if (!state.poiCategories.length) {
      list.innerHTML = "<div class='layers-empty'>Aucun POI disponible.</div>";
      return;
    }
    state.poiCategories.forEach(function (cat) {
      var key = cat.dataKey || cat.collection;
      if (!key) return;
      var label = cat.label || key;
      var icon = cat.icon || "place";
      var row = document.createElement("label");
      row.className = "layer-row";
      var input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.poi = key;
      var st = state.poiLayers[key];
      input.checked = !!(st && st.visible);
      input.addEventListener("change", function () {
        togglePoi(key, input.checked, cat);
      });
      var name = document.createElement("span");
      name.className = "layer-name";
      name.innerHTML = "<span class='material-symbols-outlined'>" + escapeHtml(icon) + "</span> " + escapeHtml(label);
      row.appendChild(input);
      row.appendChild(name);
      list.appendChild(row);
    });
  }

  function togglePoi(key, on, cat) {
    var st = state.poiLayers[key];
    if (on) {
      if (st && st.layer) {
        st.layer.addTo(state.map);
        st.visible = true;
        return;
      }
      // Charger
      var collection = (cat && cat.collection) || key;
      fetch("/field/resources/gm-collection/" + encodeURIComponent(collection), { headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var features = (data && data.features) || [];
          var layer = renderPoiFeatures(features, cat);
          state.poiLayers[key] = { layer: layer, visible: true, collection: collection };
        })
        .catch(function () { toast("Echec POI " + (cat && cat.label || key), "err"); });
    } else {
      if (st && st.layer) {
        state.map.removeLayer(st.layer);
        st.visible = false;
      }
    }
  }

  function renderPoiFeatures(features, cat) {
    var group = L.layerGroup();
    var color = "#10b981";
    var icon = (cat && cat.icon) || "place";
    features.forEach(function (f) {
      var geom = f.geometry || {};
      var props = f.properties || {};
      var name = props.Nom || props.Name || props.name || (cat && cat.label) || "";
      var type = (geom.type || "").toLowerCase();
      if (type === "point") {
        var c = geom.coordinates || [];
        if (c.length < 2) return;
        var divIcon = L.divIcon({
          className: "",
          html: "<div class='poi-marker'><span class='material-symbols-outlined'>" + escapeHtml(icon) + "</span></div>",
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        });
        var m = L.marker([c[1], c[0]], { icon: divIcon });
        if (name) m.bindPopup("<b>" + escapeHtml(name) + "</b>");
        m.addTo(group);
      } else if (type === "polygon" || type === "multipolygon") {
        try {
          L.geoJSON(f, {
            style: { color: color, weight: 2, opacity: 0.9, fillOpacity: 0.18 },
            onEachFeature: function (feat, layer) {
              if (name) layer.bindPopup("<b>" + escapeHtml(name) + "</b>");
            },
          }).addTo(group);
        } catch (e) { /* ignore */ }
      } else if (type === "linestring" || type === "multilinestring") {
        try {
          L.geoJSON(f, {
            style: { color: color, weight: 3, opacity: 0.9 },
            onEachFeature: function (feat, layer) {
              if (name) layer.bindPopup("<b>" + escapeHtml(name) + "</b>");
            },
          }).addTo(group);
        } catch (e) { /* ignore */ }
      }
    });
    group.addTo(state.map);
    return group;
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---------------------------------------------------------------------
  // Itineraires
  // ---------------------------------------------------------------------
  function setRouteDestination(latlng) {
    if (state.routeLayer) {
      state.map.removeLayer(state.routeLayer);
      state.routeLayer = null;
    }
    state.routeDestination = latlng;
    if (!latlng) return;

    var group = L.layerGroup();
    // Destination : gros marker rouge
    var destIcon = L.divIcon({
      className: "",
      html: "<div class='route-dest-marker'><span class='material-symbols-outlined'>place</span></div>",
      iconSize: [36, 36],
      iconAnchor: [18, 34],
    });
    L.marker(latlng, { icon: destIcon }).addTo(group);

    // Trait pointille depuis "moi" si dispo
    if (state.meMarker) {
      var mePos = state.meMarker.getLatLng();
      L.polyline(
        [[mePos.lat, mePos.lng], latlng],
        { color: "#dc2626", weight: 3, opacity: 0.7, dashArray: "6 8", interactive: false }
      ).addTo(group);
    }
    group.addTo(state.map);
    state.routeLayer = group;

    // Ajuste le zoom pour englober "moi" + destination si possible
    try {
      if (state.meMarker) {
        var b = L.latLngBounds([state.meMarker.getLatLng(), latlng]);
        state.map.fitBounds(b, { padding: [80, 80], maxZoom: 18 });
        state.followMe = false;
      } else {
        state.map.setView(latlng, 17);
      }
    } catch (e) { /* ignore */ }
  }

  function openInGoogleMaps(latlng) {
    if (!latlng || latlng.length < 2) return;
    var lat = latlng[0], lng = latlng[1];
    // Intent Android natif : ouvre directement Google Maps en mode navigation
    var intent = "google.navigation:q=" + lat + "," + lng;
    var webFallback = "https://www.google.com/maps/dir/?api=1&destination=" + lat + "," + lng + "&travelmode=driving";

    // Tenter l'intent Android, retomber sur le web
    var opened = false;
    try {
      // Sur Android Chrome, window.location vers google.navigation: ouvre Maps.
      // Sur iOS/Desktop cela echouera silencieusement -> on bascule sur l'URL web.
      window.location.href = intent;
      opened = true;
    } catch (e) {
      opened = false;
    }
    // Retomber au web apres un petit delai si l'intent n'a rien fait
    setTimeout(function () {
      if (document.hasFocus && document.hasFocus()) {
        window.open(webFallback, "_blank");
      }
    }, 800);
  }

  function handleRouteMessage(m) {
    var wp = (m.payload && m.payload.waypoints) || [];
    if (!wp.length) return;
    var first = wp[0];
    if (!Array.isArray(first) || first.length < 2) return;
    var lat = parseFloat(first[0]);
    var lng = parseFloat(first[1]);
    if (isNaN(lat) || isNaN(lng)) return;
    setRouteDestination([lat, lng]);
  }

  // ---------------------------------------------------------------------
  // Fiches PCORG assignees a cette tablette
  // ---------------------------------------------------------------------
  function startFichesPoll() {
    pollFiches();
    state.fichesTimer = setInterval(pollFiches, POLL_FICHES_MS);
  }

  function pollFiches() {
    fetch("/field/my-fiches", { headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (r.status === 401) { return handleSessionLost(); }
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        var open = data.open || [];
        state.fiches = open;
        renderFiches();
        detectNewFiches(open);
      })
      .catch(function () { /* silent */ });
  }

  function detectNewFiches(open) {
    var newOnes = [];
    open.forEach(function (f) {
      if (!state.seenFicheIds.has(f.id)) {
        state.seenFicheIds.add(f.id);
        newOnes.push(f);
      }
    });
    if (newOnes.length === 0) return;
    // Premier poll : ne pas toaster, juste enregistrer
    if (!state.fichesFirstPolled) {
      state.fichesFirstPolled = true;
      return;
    }
    var first = newOnes[0];
    toast("Nouvelle fiche : " + (first.text || "(sans texte)").slice(0, 60), "warn");
  }

  function renderFiches() {
    if (!state.fichesLayer) {
      state.fichesLayer = L.layerGroup().addTo(state.map);
      state.fichesMarkers = {};
    }
    var seen = {};
    state.fiches.forEach(function (f) {
      seen[f.id] = true;
      if (f.lat == null || f.lng == null) return;
      var existing = state.fichesMarkers[f.id];
      if (existing) {
        existing.setLatLng([f.lat, f.lng]);
      } else {
        var icon = L.divIcon({
          className: "",
          html: "<div class='fiche-marker urgency-" + (f.niveau_urgence || "norm") + "'>"
              + "<span class='material-symbols-outlined'>priority_high</span></div>",
          iconSize: [32, 38],
          iconAnchor: [16, 36],
        });
        var marker = L.marker([f.lat, f.lng], { icon: icon });
        marker.on("click", function () { showFicheModal(f); });
        marker.addTo(state.fichesLayer);
        state.fichesMarkers[f.id] = marker;
      }
    });
    // Retirer les markers des fiches disparues (cloturees/reassignees)
    Object.keys(state.fichesMarkers).forEach(function (id) {
      if (!seen[id]) {
        state.fichesLayer.removeLayer(state.fichesMarkers[id]);
        delete state.fichesMarkers[id];
      }
    });
  }

  function showFicheModal(f) {
    // Reutilise le modal msg-modal avec un bouton "Commentaire"
    var modal = $("msg-modal");
    var title = $("msg-modal-title");
    var body = $("msg-modal-body");
    var ack = $("msg-modal-ack");
    var routeBtn = $("msg-modal-route");
    if (!modal) return;

    modal.classList.remove("priority-high", "type-alert", "type-route", "type-instruction");
    modal.classList.add("type-instruction");
    if (f.niveau_urgence && f.niveau_urgence !== "IMP") modal.classList.add("priority-high");

    title.textContent = (f.niveau_urgence ? "[" + f.niveau_urgence + "] " : "")
      + (f.category || "Fiche PCORG");
    var lines = [];
    if (f.text) lines.push(f.text);
    if (f.area) lines.push("\nZone : " + f.area);
    if (f.comment) lines.push("\nCommentaire : " + f.comment);
    if (f.operator) lines.push("\nCreee par : " + f.operator);
    body.textContent = lines.join("");

    // Bouton itineraire si coordonnees dispo
    if (routeBtn) {
      if (f.lat != null && f.lng != null) {
        routeBtn.hidden = false;
        routeBtn.onclick = function () { openInGoogleMaps([f.lat, f.lng]); };
      } else {
        routeBtn.hidden = true;
      }
    }

    ack.textContent = "Ajouter un commentaire";
    ack.onclick = function () { openFicheCommentDialog(f); };
    modal.hidden = false;
  }

  function openFicheCommentDialog(f) {
    fieldPrompt("Commentaire sur la fiche :\n" + (f.text || "").slice(0, 80), {
      okLabel: "Envoyer",
    }).then(function (comment) {
      if (comment == null) return;
      comment = comment.trim();
      if (!comment) return;
      fetch("/field/my-fiches/" + encodeURIComponent(f.id) + "/comment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ comment: comment }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data && data.ok) {
            toast("Commentaire envoye");
            $("msg-modal").hidden = true;
            // Reset du bouton ack pour le prochain message
            $("msg-modal-ack").textContent = "J'ai compris";
            pollFiches();
          } else {
            toast("Echec : " + ((data && data.error) || "?"), "err");
          }
        })
        .catch(function () { toast("Erreur reseau", "err"); });
    });
  }

  // ---------------------------------------------------------------------
  // SOS
  // ---------------------------------------------------------------------
  function triggerSos() {
    if (state.sosInFlight) return;
    fieldConfirm("Declencher un SOS ? Le cockpit sera immediatement prevenu avec ta position GPS.", {
      okLabel: "Declencher",
      cancelLabel: "Annuler",
    }).then(function (ok) {
      if (!ok) return;
      fieldPrompt("Note courte (optionnelle) :", { okLabel: "Envoyer SOS" }).then(function (rawNote) {
        if (rawNote == null) return; // utilisateur a annule
        state.sosInFlight = true;
        var note = (rawNote || "").trim();

        var lat = null, lng = null;
        if (state.meMarker) {
          var ll = state.meMarker.getLatLng();
          lat = ll.lat;
          lng = ll.lng;
        }
        fetch("/field/sos", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            lat: lat,
            lng: lng,
            battery: state.batteryPct || null,
            note: note,
          }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            state.sosInFlight = false;
            if (data && data.ok) {
              toast("SOS envoye au cockpit", "warn");
            } else {
              toast("Echec SOS", "err");
            }
          })
          .catch(function () {
            state.sosInFlight = false;
            toast("Erreur reseau (SOS)", "err");
          });
      });
    });
  }

  // ---------------------------------------------------------------------
  // Inbox
  // ---------------------------------------------------------------------
  function startInboxPoll() {
    pollInbox();
    state.inboxTimer = setInterval(pollInbox, POLL_INBOX_MS);
  }

  function pollInbox() {
    fetch("/field/inbox", { headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (r.status === 401) { return handleSessionLost(); }
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.ok) return;
        state.inbox = data.messages || [];
        renderInbox();
        detectNew();
      })
      .catch(function () { /* silent */ });
  }

  function detectNew() {
    var newOnes = [];
    state.inbox.forEach(function (m) {
      if (!state.seenIds.has(m.id) && !m.ack_at) {
        newOnes.push(m);
      }
    });
    if (newOnes.length === 0) return;
    // Afficher la premiere priorite haute, sinon un toast
    var high = newOnes.find(function (m) { return m.priority === "high"; });
    var first = high || newOnes[0];
    newOnes.forEach(function (m) {
      state.seenIds.add(m.id);
      // Les itineraires sont dessines sur la carte meme sans ouvrir le modal
      if (m.type === "route") handleRouteMessage(m);
    });
    showMessageModal(first);
    if (newOnes.length > 1) {
      toast("+" + (newOnes.length - 1) + " autre(s) message(s)");
    }
  }

  function renderInbox() {
    var list = $("inbox-list");
    var badge = $("inbox-badge");
    if (!list) return;
    var unread = state.inbox.filter(function (m) { return !m.ack_at; });
    if (badge) {
      if (unread.length > 0) {
        badge.hidden = false;
        badge.textContent = unread.length > 9 ? "9+" : String(unread.length);
      } else {
        badge.hidden = true;
      }
    }
    if (state.inbox.length === 0) {
      list.innerHTML = "<div class='inbox-empty'>Aucun message.</div>";
      return;
    }
    // Ordre antichronologique
    var sorted = state.inbox.slice().sort(function (a, b) {
      return (b.created_at || "").localeCompare(a.created_at || "");
    });
    list.innerHTML = "";
    sorted.forEach(function (m) {
      var div = document.createElement("div");
      div.className = "inbox-item";
      if (!m.ack_at) div.classList.add("unread");
      if (m.priority === "high") div.classList.add("priority-high");
      var t = document.createElement("div");
      t.className = "item-title";
      t.textContent = m.title || "(sans titre)";
      var b = document.createElement("div");
      b.className = "item-body";
      b.textContent = (m.body || "").slice(0, 120);
      var ts = document.createElement("div");
      ts.className = "item-time";
      ts.textContent = m.created_at ? new Date(m.created_at).toLocaleString() : "";
      div.appendChild(t);
      div.appendChild(b);
      div.appendChild(ts);
      div.addEventListener("click", function () { showMessageModal(m); });
      list.appendChild(div);
    });
  }

  function showMessageModal(m) {
    var modal = $("msg-modal");
    var title = $("msg-modal-title");
    var body = $("msg-modal-body");
    var ack = $("msg-modal-ack");
    var routeBtn = $("msg-modal-route");
    if (!modal) return;
    title.textContent = m.title || "Message";
    body.textContent = m.body || "";

    // Accent visuel selon type/priority
    modal.classList.remove("priority-high", "type-alert", "type-route", "type-instruction");
    if (m.priority === "high") modal.classList.add("priority-high");
    if (m.type === "alert") modal.classList.add("type-alert");
    if (m.type === "route") modal.classList.add("type-route");
    if (m.type === "instruction") modal.classList.add("type-instruction");

    // Bouton "Demarrer l'itineraire" pour les messages de type route
    var waypoints = (m.payload && m.payload.waypoints) || null;
    var hasRoute = (m.type === "route" && waypoints && waypoints.length);
    if (routeBtn) {
      routeBtn.hidden = !hasRoute;
      if (hasRoute) {
        routeBtn.onclick = function () {
          openInGoogleMaps(waypoints[0]);
        };
      } else {
        routeBtn.onclick = null;
      }
    }

    modal.hidden = false;
    ack.onclick = function () {
      fetch("/field/ack/" + encodeURIComponent(m.id), { method: "POST" })
        .then(function () {
          modal.hidden = true;
          pollInbox();
        })
        .catch(function () { toast("Echec ack", "err"); });
    };
  }

  // ---------------------------------------------------------------------
  // UI wiring
  // ---------------------------------------------------------------------
  function wireUi() {
    $("btn-recenter").addEventListener("click", recenter);
    $("btn-layers").addEventListener("click", cycleLayer);
    $("btn-grid").addEventListener("click", openLayersPanel);
    $("btn-inbox").addEventListener("click", function () {
      var p = $("inbox-panel");
      p.hidden = !p.hidden;
    });
    $("inbox-close").addEventListener("click", function () { $("inbox-panel").hidden = true; });
    $("btn-sos").addEventListener("click", triggerSos);
    $("msg-modal-close").addEventListener("click", function () { $("msg-modal").hidden = true; });

    // Panneau Calques (close + checkboxes)
    var lyClose = $("layers-close");
    if (lyClose) lyClose.addEventListener("click", function () { $("layers-panel").hidden = true; });
    var cbGrid = $("lyr-grid-100");
    if (cbGrid) cbGrid.addEventListener("change", function () { toggleGrid(cbGrid.checked); });
    var cbGrid25 = $("lyr-grid-25");
    if (cbGrid25) cbGrid25.addEventListener("change", function () { toggleGrid25(cbGrid25.checked); });
    var cb3p = $("lyr-3p");
    if (cb3p) cb3p.addEventListener("change", function () { toggle3P(cb3p.checked); });

    // Plein ecran
    var btnFs = $("btn-fullscreen");
    if (btnFs) btnFs.addEventListener("click", toggleFullscreen);
    document.addEventListener("fullscreenchange", updateFullscreenIcon);

    // Outils de mesure
    var measureIds = ["measure-line", "measure-area", "measure-circle", "measure-clear"];
    measureIds.forEach(function (id) {
      var b = $(id);
      if (!b) return;
      b.addEventListener("click", function () { toggleMeasureTool(b.dataset.mode); });
    });

    // Click sur la carte : si grille active, afficher le crosshair
    if (state.map) {
      state.map.on("click", function (e) {
        if (state.measureMode) return; // les outils de mesure prennent la main
        if (state.gridOn) showCrosshair(e.latlng);
      });
    }
  }

  function openLayersPanel() {
    var p = $("layers-panel");
    if (!p) return;
    // Sync checkboxes avec l'etat
    var cbGrid = $("lyr-grid-100");
    if (cbGrid) cbGrid.checked = !!state.gridOn;
    var cbGrid25 = $("lyr-grid-25");
    if (cbGrid25) cbGrid25.checked = !!state.grid25On;
    var cb3p = $("lyr-3p");
    if (cb3p) cb3p.checked = !!state.threePOn;
    updateGrid25ButtonVisibility();
    p.hidden = !p.hidden;
  }

  // ---------------------------------------------------------------------
  // Plein ecran
  // ---------------------------------------------------------------------
  function toggleFullscreen() {
    var doc = document;
    var elem = document.documentElement;
    var isFs = !!(doc.fullscreenElement || doc.webkitFullscreenElement);
    if (!isFs) {
      var req = elem.requestFullscreen || elem.webkitRequestFullscreen;
      if (req) {
        try { req.call(elem); } catch (e) { /* ignore */ }
      }
    } else {
      var exit = doc.exitFullscreen || doc.webkitExitFullscreen;
      if (exit) {
        try { exit.call(doc); } catch (e) { /* ignore */ }
      }
    }
  }

  function updateFullscreenIcon() {
    var btn = $("btn-fullscreen");
    if (!btn) return;
    var icon = btn.querySelector(".material-symbols-outlined");
    if (!icon) return;
    var isFs = !!(document.fullscreenElement || document.webkitFullscreenElement);
    icon.textContent = isFs ? "fullscreen_exit" : "fullscreen";
  }

  // ---------------------------------------------------------------------
  // Outils de mesure (port simplifie de map_view.js)
  // ---------------------------------------------------------------------
  var SNAP_PX = 14;
  var MEASURE_IDS = ["measure-line", "measure-area", "measure-circle"];

  function toggleMeasureTool(mode) {
    if (mode === "clear") {
      clearMeasure();
      return;
    }
    if (state.measureMode === mode) {
      clearMeasure();
      return;
    }
    clearMeasure();
    state.measureMode = mode;
    state.measureFinalized = false;

    MEASURE_IDS.forEach(function (id) {
      var btn = $(id);
      if (btn) btn.classList.toggle("active", btn.dataset.mode === mode);
    });

    state.measureLayer = L.layerGroup().addTo(state.map);
    state.measurePoints = [];
    state.measureLabels = [];

    state.map.getContainer().style.cursor = "crosshair";
    state.map.on("click", onMeasureClick);
    state.map.on("mousemove", onMeasureMouseMove);
    state.map.on("dblclick", onMeasureDblClick);
    if (state.map.doubleClickZoom) state.map.doubleClickZoom.disable();

    showMeasureTooltip(
      mode === "line" ? "Touchez pour tracer"
      : mode === "area" ? "Touchez les sommets"
      : "Touchez le centre"
    );
  }

  function clearMeasure() {
    state.measureMode = null;
    state.measurePoints = [];
    state.measureFinalized = false;
    state.measureGuide = null;
    state.measureLabels = [];
    if (state.measureLayer) {
      try { state.map.removeLayer(state.measureLayer); } catch (e) {}
      state.measureLayer = null;
    }
    hideMeasureTooltip();
    MEASURE_IDS.forEach(function (id) {
      var btn = $(id);
      if (btn) btn.classList.remove("active");
    });
    if (state.map) {
      state.map.off("click", onMeasureClick);
      state.map.off("mousemove", onMeasureMouseMove);
      state.map.off("dblclick", onMeasureDblClick);
      if (state.map.doubleClickZoom) state.map.doubleClickZoom.enable();
      state.map.getContainer().style.cursor = "";
    }
  }

  function addMeasureVertex(latlng) {
    if (!state.measureLayer) return;
    var marker = L.circleMarker(latlng, {
      radius: 5, color: "#6366f1", fillColor: "#fff",
      fillOpacity: 1, weight: 2, interactive: false,
    });
    state.measureLayer.addLayer(marker);
  }

  function onMeasureClick(e) {
    if (!state.measureMode || !state.measureLayer || state.measureFinalized) return;
    var latlng = e.latlng;

    if (state.measureMode === "circle") {
      if (state.measurePoints.length === 0) {
        state.measurePoints.push(latlng);
        addMeasureVertex(latlng);
        showMeasureTooltip("Touchez pour definir le rayon");
      } else {
        finalizeMeasureCircle(latlng);
      }
      return;
    }

    if (state.measurePoints.length > 0) {
      var lastPt = state.measurePoints[state.measurePoints.length - 1];
      if (lastPt.distanceTo(latlng) < 1) return;
    }

    if (state.measureMode === "area" && state.measurePoints.length >= 3) {
      var firstPt = state.map.latLngToContainerPoint(state.measurePoints[0]);
      var clickPt = state.map.latLngToContainerPoint(latlng);
      if (firstPt.distanceTo(clickPt) < SNAP_PX) {
        finalizeMeasureArea();
        return;
      }
    }

    state.measurePoints.push(latlng);
    addMeasureVertex(latlng);

    if (state.measureMode === "line") {
      showMeasureTooltip("Touchez pour continuer, double-tap pour terminer");
    } else {
      showMeasureTooltip(state.measurePoints.length < 3
        ? "Touchez les sommets (min. 3)"
        : "Touchez le 1er point pour fermer");
    }
  }

  function onMeasureMouseMove(e) {
    if (!state.measureMode || !state.measureLayer || !state.measurePoints.length || state.measureFinalized) return;
    var latlng = e.latlng;

    if (state.measureMode === "circle" && state.measurePoints.length === 1) {
      if (state.measureGuide) state.measureLayer.removeLayer(state.measureGuide);
      var radius = state.measurePoints[0].distanceTo(latlng);
      state.measureGuide = L.circle(state.measurePoints[0], {
        radius: radius, color: "#6366f1", weight: 2, opacity: 0.7,
        fillColor: "#6366f1", fillOpacity: 0.1, dashArray: "6 4", interactive: false,
      });
      state.measureLayer.addLayer(state.measureGuide);
      showMeasureTooltip("Rayon: " + formatDist(radius));
      return;
    }

    if (state.measureMode === "line" || state.measureMode === "area") {
      if (state.measureGuide) state.measureLayer.removeLayer(state.measureGuide);
      var pts = state.measurePoints.concat([latlng]);
      if (state.measureMode === "area" && pts.length >= 3) {
        state.measureGuide = L.polygon(pts, {
          color: "#6366f1", weight: 2, opacity: 0.5,
          fillColor: "#6366f1", fillOpacity: 0.08, dashArray: "6 4", interactive: false,
        });
      } else {
        state.measureGuide = L.polyline(pts, {
          color: "#6366f1", weight: 2, opacity: 0.5, dashArray: "6 4", interactive: false,
        });
      }
      state.measureLayer.addLayer(state.measureGuide);

      var totalDist = computeTotalDistance(pts);
      var tip = "Distance: " + formatDist(totalDist);
      if (state.measureMode === "area" && pts.length >= 3) {
        tip += " | Aire: " + formatArea(computeArea(pts));
      }
      showMeasureTooltip(tip);
    }
  }

  function onMeasureDblClick(e) {
    if (!state.measureMode || state.measureFinalized) return;
    L.DomEvent.stop(e);
    if (state.measureMode === "line" && state.measurePoints.length >= 2) finalizeMeasureLine();
    else if (state.measureMode === "area" && state.measurePoints.length >= 3) finalizeMeasureArea();
  }

  function finalizeMeasureLine() {
    if (state.measureGuide) { state.measureLayer.removeLayer(state.measureGuide); state.measureGuide = null; }
    while (state.measurePoints.length > 1 &&
           state.measurePoints[state.measurePoints.length - 1].distanceTo(state.measurePoints[state.measurePoints.length - 2]) < 1) {
      state.measurePoints.pop();
    }
    if (state.measurePoints.length < 2) return;
    state.measureFinalized = true;

    L.polyline(state.measurePoints, {
      color: "#6366f1", weight: 3, opacity: 0.9, interactive: false,
    }).addTo(state.measureLayer);

    var totalDist = computeTotalDistance(state.measurePoints);
    if (state.measurePoints.length > 2) {
      for (var i = 1; i < state.measurePoints.length; i++) {
        var segDist = state.measurePoints[i - 1].distanceTo(state.measurePoints[i]);
        if (segDist < 1) continue;
        var segMid = L.latLng(
          (state.measurePoints[i - 1].lat + state.measurePoints[i].lat) / 2,
          (state.measurePoints[i - 1].lng + state.measurePoints[i].lng) / 2
        );
        addMeasureSegLabel(segMid, formatDist(segDist));
      }
    }
    addMeasureLabel(state.measurePoints[state.measurePoints.length - 1], formatDist(totalDist));
    showMeasureTooltip("Total: " + formatDist(totalDist));
    unbindMeasureEvents();
  }

  function finalizeMeasureArea() {
    if (state.measureGuide) { state.measureLayer.removeLayer(state.measureGuide); state.measureGuide = null; }
    state.measureFinalized = true;

    var polygon = L.polygon(state.measurePoints, {
      color: "#6366f1", weight: 3, opacity: 0.9,
      fillColor: "#6366f1", fillOpacity: 0.15, interactive: false,
    });
    state.measureLayer.addLayer(polygon);
    var area = computeArea(state.measurePoints);
    var perimeter = computeTotalDistance(state.measurePoints.concat([state.measurePoints[0]]));
    var center = polygon.getBounds().getCenter();
    addMeasureLabel(center, formatArea(area) + "\nPerimetre: " + formatDist(perimeter));
    showMeasureTooltip("Aire: " + formatArea(area) + " | Perimetre: " + formatDist(perimeter));
    unbindMeasureEvents();
  }

  function finalizeMeasureCircle(edgePoint) {
    if (state.measureGuide) { state.measureLayer.removeLayer(state.measureGuide); state.measureGuide = null; }
    state.measureFinalized = true;

    var center = state.measurePoints[0];
    var radius = center.distanceTo(edgePoint);

    L.circle(center, {
      radius: radius, color: "#6366f1", weight: 3, opacity: 0.9,
      fillColor: "#6366f1", fillOpacity: 0.1, interactive: false,
    }).addTo(state.measureLayer);

    L.polyline([center, edgePoint], {
      color: "#6366f1", weight: 2, opacity: 0.6, dashArray: "4 4", interactive: false,
    }).addTo(state.measureLayer);
    addMeasureVertex(edgePoint);

    var area = Math.PI * radius * radius;
    addMeasureLabel(center, "R: " + formatDist(radius) + "\nD: " + formatDist(radius * 2) + "\nAire: " + formatArea(area));
    showMeasureTooltip("Rayon: " + formatDist(radius) + " | D: " + formatDist(radius * 2) + " | Aire: " + formatArea(area));
    unbindMeasureEvents();
  }

  function unbindMeasureEvents() {
    state.map.off("click", onMeasureClick);
    state.map.off("mousemove", onMeasureMouseMove);
    state.map.off("dblclick", onMeasureDblClick);
    if (state.map.doubleClickZoom) state.map.doubleClickZoom.enable();
    state.map.getContainer().style.cursor = "";
  }

  function addMeasureLabel(latlng, text) {
    if (!state.measureLayer) return;
    var html = '<div class="measure-label">' + escapeHtml(text).replace(/\n/g, "<br>") + '</div>';
    var icon = L.divIcon({ html: html, className: "measure-label-icon", iconSize: null });
    var marker = L.marker(latlng, { icon: icon, interactive: false });
    state.measureLayer.addLayer(marker);
    state.measureLabels.push(marker);
  }

  function addMeasureSegLabel(latlng, text) {
    if (!state.measureLayer) return;
    var html = '<div class="measure-seg-label">' + escapeHtml(text) + '</div>';
    var icon = L.divIcon({ html: html, className: "measure-label-icon", iconSize: null });
    var marker = L.marker(latlng, { icon: icon, interactive: false });
    state.measureLayer.addLayer(marker);
    state.measureLabels.push(marker);
  }

  function computeTotalDistance(points) {
    var d = 0;
    for (var i = 1; i < points.length; i++) d += points[i - 1].distanceTo(points[i]);
    return d;
  }

  function computeArea(points) {
    if (points.length < 3) return 0;
    if (typeof turf !== "undefined") {
      var coords = points.map(function (p) { return [p.lng, p.lat]; });
      coords.push(coords[0]);
      try { return turf.area(turf.polygon([coords])); } catch (e) { return 0; }
    }
    return 0;
  }

  function formatDist(meters) {
    if (meters >= 1000) return (meters / 1000).toFixed(2) + " km";
    return Math.round(meters) + " m";
  }

  function formatArea(sqm) {
    if (sqm >= 10000) return (sqm / 10000).toFixed(2) + " ha";
    return Math.round(sqm) + " m\u00B2";
  }

  var _measureTooltip = null;
  function showMeasureTooltip(text) {
    if (!_measureTooltip) {
      _measureTooltip = document.createElement("div");
      _measureTooltip.className = "measure-tooltip";
      var mapContainer = $("field-map");
      if (mapContainer) mapContainer.appendChild(_measureTooltip);
    }
    _measureTooltip.textContent = text;
    _measureTooltip.style.display = "";
  }

  function hideMeasureTooltip() {
    if (_measureTooltip) _measureTooltip.style.display = "none";
  }

  // ---------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------
  function boot() {
    initMap();
    wireUi();
    startClock();
    initNetStatus();
    initBattery();
    startGeolocation();
    startInboxPoll();
    startFichesPoll();
    // Ressources carte : 3P et carroyage sont desactives par defaut.
    // Categories POI chargees pour le panneau Calques.
    loadPoiCategories();
    // PWA : service worker, wake lock, buffer de positions
    registerServiceWorker();
    acquireWakeLock();
    initPositionBuffer();

    // Empecher le zoom double-tap / pinch natif
    document.addEventListener("gesturestart", function (e) { e.preventDefault(); });

    // Re-acquerir le wake lock quand l'ecran revient
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") acquireWakeLock();
    });
  }

  // ---------------------------------------------------------------------
  // PWA : service worker, wake lock, IndexedDB
  // ---------------------------------------------------------------------
  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    try {
      navigator.serviceWorker.register("/field/sw.js", { scope: "/field" })
        .then(function (reg) {
          // Forcer la mise a jour si un nouveau SW est dispo
          if (reg && reg.update) reg.update();
        })
        .catch(function (err) {
          console.warn("[field] SW registration failed:", err);
        });
    } catch (e) { /* ignore */ }
  }

  var _wakeLock = null;
  function acquireWakeLock() {
    if (!("wakeLock" in navigator)) return;
    if (_wakeLock) return;
    try {
      navigator.wakeLock.request("screen").then(function (lock) {
        _wakeLock = lock;
        lock.addEventListener("release", function () { _wakeLock = null; });
      }).catch(function () { _wakeLock = null; });
    } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------------
  // IndexedDB : buffer des positions GPS quand offline
  // ---------------------------------------------------------------------
  var IDB_NAME = "cockpit-field";
  var IDB_STORE = "position-buffer";
  var _idb = null;

  function openIdb() {
    if (_idb) return Promise.resolve(_idb);
    return new Promise(function (resolve, reject) {
      if (!("indexedDB" in window)) { reject("no_idb"); return; }
      var req = indexedDB.open(IDB_NAME, 1);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(IDB_STORE)) {
          db.createObjectStore(IDB_STORE, { keyPath: "id", autoIncrement: true });
        }
      };
      req.onsuccess = function () { _idb = req.result; resolve(_idb); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function bufferPosition(pos) {
    openIdb().then(function (db) {
      var tx = db.transaction(IDB_STORE, "readwrite");
      tx.objectStore(IDB_STORE).add({
        lat: pos.lat, lng: pos.lng, accuracy: pos.accuracy,
        speed: pos.speed, heading: pos.heading, battery: pos.battery,
        ts: pos.ts || Date.now(),
      });
    }).catch(function () { /* ignore */ });
  }

  function flushPositionBuffer() {
    if (!navigator.onLine) return;
    openIdb().then(function (db) {
      var tx = db.transaction(IDB_STORE, "readwrite");
      var store = tx.objectStore(IDB_STORE);
      var req = store.getAll();
      req.onsuccess = function () {
        var items = req.result || [];
        if (items.length === 0) return;
        // Envoyer le dernier point seulement (optimisation : on ne rejoue
        // pas tout l'historique, on veut juste la position a jour)
        var last = items[items.length - 1];
        fetch("/field/position", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(last),
        }).then(function (r) {
          if (r && r.ok) {
            var txDel = db.transaction(IDB_STORE, "readwrite");
            txDel.objectStore(IDB_STORE).clear();
          }
        }).catch(function () { /* retry au prochain online */ });
      };
    }).catch(function () { /* ignore */ });
  }

  function initPositionBuffer() {
    window.addEventListener("online", flushPositionBuffer);
    // Flush periodique au cas ou
    setInterval(flushPositionBuffer, 30000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.FieldApp = {
    recenter: recenter,
    cycleLayer: cycleLayer,
    pollInbox: pollInbox,
    toggleGrid: toggleGrid,
    toggleGrid25: toggleGrid25,
    toggle3P: toggle3P,
    togglePoi: togglePoi,
    toggleFullscreen: toggleFullscreen,
    toggleMeasureTool: toggleMeasureTool,
    load3P: load3P,
    loadPoiCategories: loadPoiCategories,
    setRouteDestination: setRouteDestination,
    openInGoogleMaps: openInGoogleMaps,
    pollFiches: pollFiches,
    triggerSos: triggerSos,
  };
})();
