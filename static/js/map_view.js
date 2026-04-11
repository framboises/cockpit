// ==========================================================================
// MAP VIEW — Carte OpenStreetMap pour cockpit
// Inspire de looker/event_view.js, sans flip cards
// ==========================================================================

(function () {
  "use strict";

  // --- State ---
  let map = null;
  let mapReady = false;
  let currentView = "timeline"; // "timeline" | "map"
  const categoryLayers = {};
  let gmCategories = [];
  let tileLayerOSM = null;
  let tileLayerSatACO = null;
  let tileLayerSatEGIS = null;
  let currentTile = "osm"; // "osm" | "sat-aco" | "sat-egis"

  // --- Map preferences state ---
  var mapDefaults = { hidden_categories: [], default_tile: "osm" };
  var userPrefs = null; // null = pas de prefs perso
  var prefsLoaded = false;
  var prefsPanelOpen = false;

  // Route color filter: catId -> { colorName: true/false }
  var enabledRouteColors = {};

  // --- Data preload cache ---
  var _preloadCache = { key: null, paramData: null, gmCategories: null, geoJsons: {} };

  // --- Icon color mapping (same as looker) ---
  const ICON_COLORS = {
    door_front: "#132646",
    local_parking: "#3B82F6",
    camping: "#2E7D32",
    hotel: "#FFD700",
    event_seat: "#FA8072",
    wc: "#b47272",
    campground: "#B46300",
    badge: "#FF00FF",
    build: "#FF8C00",
    rv_hookup: "#0D9488",
    restaurant: "#E11D48",
    medical_services: "#DC2626",
    security: "#7C3AED",
    directions_car: "#2563EB"
  };

  const AUTO_COLORS = [
    "#E6194B", "#3CB44B", "#FFE119", "#4363D8", "#F58231",
    "#911EB4", "#42D4F4", "#F032E6", "#BFEF45", "#FABED4",
    "#469990", "#DCBEFF", "#9A6324", "#FFFAC8", "#800000",
    "#AAFFC3", "#808000", "#FFD8B1", "#000075", "#A9A9A9"
  ];
  let autoColorIdx = 0;

  function getColor(icon) {
    if (ICON_COLORS[icon]) return ICON_COLORS[icon];
    return AUTO_COLORS[autoColorIdx++ % AUTO_COLORS.length];
  }

  // Cache for resolved route colors
  var routeColorCache = {};

  function resolveRouteColor(colorName) {
    if (!colorName) return Promise.resolve("#808080");
    if (routeColorCache[colorName]) return Promise.resolve(routeColorCache[colorName]);

    return fetch("/get_parking_color?color=" + encodeURIComponent(colorName))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var hex = (d && d.color) || "#808080";
        routeColorCache[colorName] = hex;
        return hex;
      })
      .catch(function () { return "#808080"; });
  }

  // --- Map center (Le Mans) ---
  const DEFAULT_CENTER = [47.938561591531936, 0.2243184111156285];
  const DEFAULT_ZOOM = 14;

  // ==========================================================================
  // INIT MAP
  // ==========================================================================

  function initMap() {
    if (!window.isBlockAllowed("map-main")) return;
    if (mapReady) return;

    const container = document.getElementById("cockpit-map");
    if (!container) return;

    map = L.map("cockpit-map", {
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      minZoom: 10,
      maxZoom: 22,
      zoomControl: true,
      scrollWheelZoom: true,
      doubleClickZoom: false
    });

    // Tile layers
    tileLayerOSM = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxNativeZoom: 19,
      maxZoom: 22
    }).addTo(map);

    tileLayerSatEGIS = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      attribution: "&copy; Esri",
      maxNativeZoom: 19,
      maxZoom: 22
    });

    tileLayerSatACO = L.tileLayer("/tiles/{z}/{x}/{y}.png", {
      tms: true,
      maxZoom: 22,
      attribution: "ACO"
    });

    // Fullscreen toggle control
    var fullscreenCtrl = L.control({ position: "topright" });
    fullscreenCtrl.onAdd = function () {
      var div = L.DomUtil.create("div", "leaflet-bar cockpit-tile-switcher cockpit-fullscreen-ctrl");
      var btn = document.createElement("button");
      btn.className = "tile-btn";
      btn.id = "map-fullscreen-btn";
      btn.title = "Plein ecran";
      var ico = document.createElement("span");
      ico.className = "material-symbols-outlined";
      ico.style.fontSize = "20px";
      ico.textContent = "fullscreen";
      btn.appendChild(ico);
      div.appendChild(btn);
      L.DomEvent.disableClickPropagation(div);
      btn.addEventListener("click", toggleMapFullscreen);
      return div;
    };
    fullscreenCtrl.addTo(map);

    // Recenter control
    var recenterCtrl = L.control({ position: "topright" });
    recenterCtrl.onAdd = function () {
      var div = L.DomUtil.create("div", "leaflet-bar cockpit-tile-switcher cockpit-recenter-ctrl");
      var btn = document.createElement("button");
      btn.className = "tile-btn";
      btn.id = "map-recenter-btn";
      btn.title = "Recentrer sur le circuit";
      var ico = document.createElement("span");
      ico.className = "material-symbols-outlined";
      ico.style.fontSize = "20px";
      ico.textContent = "my_location";
      btn.appendChild(ico);
      div.appendChild(btn);
      L.DomEvent.disableClickPropagation(div);
      btn.addEventListener("click", function () {
        if (map) map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
      });
      return div;
    };
    recenterCtrl.addTo(map);

    // Tile switcher control (safe DOM construction)
    var tileBtn = L.control({ position: "topright" });
    tileBtn.onAdd = function () {
      var div = L.DomUtil.create("div", "leaflet-bar cockpit-tile-switcher");
      var btn = document.createElement("button");
      btn.className = "tile-btn";
      btn.title = "Changer de fond de carte";
      var ico = document.createElement("span");
      ico.className = "material-symbols-outlined";
      ico.style.fontSize = "20px";
      ico.textContent = "satellite_alt";
      btn.appendChild(ico);
      div.appendChild(btn);
      L.DomEvent.disableClickPropagation(div);
      btn.addEventListener("click", cycleTileLayer);
      return div;
    };
    tileBtn.addTo(map);

    // Preferences control (below satellite button)
    var prefsCtrl = L.control({ position: "topright" });
    prefsCtrl.onAdd = function () {
      var div = L.DomUtil.create("div", "leaflet-bar cockpit-tile-switcher cockpit-prefs-ctrl");
      var btn = document.createElement("button");
      btn.className = "tile-btn";
      btn.title = "Preferences d'affichage";
      btn.id = "map-prefs-btn";
      var ico = document.createElement("span");
      ico.className = "material-symbols-outlined";
      ico.style.fontSize = "20px";
      ico.textContent = "tune";
      btn.appendChild(ico);
      div.appendChild(btn);
      L.DomEvent.disableClickPropagation(div);
      btn.addEventListener("click", togglePrefsPanel);
      return div;
    };
    prefsCtrl.addTo(map);

    // Build prefs panel (hidden by default)
    buildPrefsPanel();

    // Zoom level indicator (bottom left)
    var zoomCtrl = L.control({ position: "bottomleft" });
    zoomCtrl.onAdd = function () {
      var div = L.DomUtil.create("div", "cockpit-zoom-level");
      div.id = "map-zoom-level";
      div.textContent = "Z" + map.getZoom();
      return div;
    };
    zoomCtrl.addTo(map);
    map.on("zoomend", function () {
      var el = document.getElementById("map-zoom-level");
      if (el) el.textContent = "Z" + map.getZoom();
      scheduleDeclutter();
    });
    map.on("moveend", scheduleDeclutter);

    // Grid toggle control (below preferences)
    var gridCtrl = L.control({ position: "topright" });
    gridCtrl.onAdd = function () {
      var div = L.DomUtil.create("div", "leaflet-bar cockpit-tile-switcher cockpit-grid-ctrl");
      var btn = document.createElement("button");
      btn.className = "tile-btn";
      btn.id = "map-grid-btn";
      btn.title = "Carroyage tactique";
      var ico = document.createElement("span");
      ico.className = "material-symbols-outlined";
      ico.style.fontSize = "20px";
      ico.textContent = "grid_3x3";
      btn.appendChild(ico);
      div.appendChild(btn);
      L.DomEvent.disableClickPropagation(div);
      btn.addEventListener("click", toggleGrid);
      return div;
    };
    gridCtrl.addTo(map);

    // 25m sub-grid toggle (below grid button)
    var grid25Ctrl = L.control({ position: "topright" });
    grid25Ctrl.onAdd = function () {
      var div = L.DomUtil.create("div", "leaflet-bar cockpit-tile-switcher cockpit-grid25-ctrl");
      div.style.display = "none"; // hidden until zoom >= 18 and grid active
      div.id = "map-grid25-wrap";
      var btn = document.createElement("button");
      btn.className = "tile-btn";
      btn.id = "map-grid25-btn";
      btn.title = "Carroyage 25m";
      var ico = document.createElement("span");
      ico.className = "material-symbols-outlined";
      ico.style.fontSize = "20px";
      ico.textContent = "grid_4x4";
      btn.appendChild(ico);
      div.appendChild(btn);
      L.DomEvent.disableClickPropagation(div);
      btn.addEventListener("click", toggleGrid25);
      return div;
    };
    grid25Ctrl.addTo(map);

    // Show/hide 25m button based on zoom and grid state
    map.on("zoomend", updateGrid25ButtonVisibility);

    // 3P (Portes/Portails/Portillons) toggle control (bottom right)
    var portesCtrl = L.control({ position: "bottomright" });
    portesCtrl.onAdd = function () {
      var div = L.DomUtil.create("div", "leaflet-bar cockpit-measure-tools cockpit-portes-ctrl");
      var btn = document.createElement("button");
      btn.className = "tile-btn";
      btn.id = "map-portes-btn";
      btn.title = "Portes / Portails / Portillons";
      var ico = document.createElement("span");
      ico.className = "material-symbols-outlined";
      ico.style.fontSize = "20px";
      ico.textContent = "door_front";
      btn.appendChild(ico);
      div.appendChild(btn);
      L.DomEvent.disableClickPropagation(div);
      btn.addEventListener("click", togglePortes);
      return div;
    };
    portesCtrl.addTo(map);

    // Load portes names for search (always, regardless of layer visibility)
    loadPortesForSearch();

    // Measurement tools control (bottom right)
    var measureCtrl = L.control({ position: "bottomright" });
    measureCtrl.onAdd = function () {
      var div = L.DomUtil.create("div", "leaflet-bar cockpit-measure-tools");
      var tools = [
        { id: "measure-line", icon: "straighten", title: "Mesurer une distance", mode: "line" },
        { id: "measure-area", icon: "square_foot", title: "Mesurer une aire", mode: "area" },
        { id: "measure-circle", icon: "radio_button_unchecked", title: "Rayon / Diametre", mode: "circle" },
        { id: "measure-edit", icon: "edit", title: "Editer les sommets", mode: "edit" },
        { id: "measure-clear", icon: "delete_outline", title: "Effacer", mode: "clear" }
      ];
      tools.forEach(function (t) {
        var btn = document.createElement("button");
        btn.className = "tile-btn";
        btn.id = t.id;
        btn.title = t.title;
        btn.dataset.mode = t.mode;
        var ico = document.createElement("span");
        ico.className = "material-symbols-outlined";
        ico.style.fontSize = "20px";
        ico.textContent = t.icon;
        btn.appendChild(ico);
        btn.addEventListener("click", function () { toggleMeasureTool(t.mode); });
        div.appendChild(btn);
      });
      L.DomEvent.disableClickPropagation(div);
      return div;
    };
    measureCtrl.addTo(map);

    mapReady = true;
  }

  // ==========================================================================
  // CARROYAGE TACTIQUE
  // ==========================================================================

  var _gridVisible = false;
  var _gridLayer = null;
  var _gridData = null; // cached
  var _gridMeta = null;
  var _gridHeadersEl = null;
  var _gridPrevBounds = null;

  // 25m sub-grid state
  var _grid25Visible = false;
  var _grid25Layer = null;
  var _grid25Meta = null; // {colCenters, rowCenters, hLines, vLines, numCols, numRows}

  function toggleGrid() {
    _gridVisible = !_gridVisible;
    var btn = document.getElementById("map-grid-btn");
    if (btn) btn.classList.toggle("active", _gridVisible);

    if (_gridVisible) {
      renderGrid();
    } else {
      clearGrid();
    }
  }

  function clearGrid() {
    // Clear 25m sub-grid first
    clearGrid25();

    // Remove grid layer
    if (_gridLayer) {
      try { map.removeLayer(_gridLayer); } catch (e) {}
      _gridLayer = null;
    }
    // Remove sticky headers
    if (_gridHeadersEl) {
      _gridHeadersEl.remove();
      _gridHeadersEl = null;
    }

    // Unlock map bounds
    if (map) {
      map.setMaxBounds(null);
      map.setMinZoom(10);
      map.off("move", updateStickyHeaders);
      map.off("move", updateHighlightBands);
      map.off("zoom", updateStickyHeaders);
      map.off("zoomend", updateGridLineWeight);
      map.off("mousemove", onGridMouseMove);
      map.off("mouseout", onGridMouseOut);
      _gridColBand = null;
      _gridRowBand = null;
      _gridHighlightCol = null;
      _gridHighlightRow = null;
      // Restore previous view
      if (_gridPrevBounds) {
        map.fitBounds(_gridPrevBounds, { animate: true });
        _gridPrevBounds = null;
      }
    }

    _gridMeta = null;
  }

  // Column label helper: 0->A, 1->B, ..., 25->Z, 26->AA, 27->AB, ...
  function colLabel(idx) {
    if (idx < 26) return String.fromCharCode(65 + idx);
    return String.fromCharCode(65 + Math.floor(idx / 26) - 1) + String.fromCharCode(65 + (idx % 26));
  }

  function renderGrid() {
    clearGrid();
    if (!map) return;

    var doRender = function (data) {
      _gridData = data;
      if (!data || !data.lines) return;

      var lines = data.lines;
      var hLines = lines.h_lines || [];
      var vLines = lines.v_lines || [];
      var bounds = lines.bounds || {};
      var numCols = (lines.num_cols || vLines.length - 1);
      var numRows = (lines.num_rows || hLines.length - 1);

      _gridLayer = L.layerGroup().addTo(map);
      var _gridPolylines = [];

      // Draw grid lines from QGIS data
      hLines.forEach(function (l) {
        var pl = L.polyline(
          [[l.lat, l.lng_start], [l.lat, l.lng_end]],
          { color: "#f59e0b", weight: 1, opacity: 0.6, interactive: false }
        );
        _gridLayer.addLayer(pl);
        _gridPolylines.push(pl);
      });
      vLines.forEach(function (l) {
        var pl = L.polyline(
          [[l.lat_start, l.lng], [l.lat_end, l.lng]],
          { color: "#f59e0b", weight: 1, opacity: 0.6, interactive: false }
        );
        _gridLayer.addLayer(pl);
        _gridPolylines.push(pl);
      });

      // Apply offsets from calibration (stored in DB)
      var colOffset = lines.col_offset || 0;
      var rowOffset = lines.row_offset || 0;

      // Generate ALL column labels and row numbers with offset
      var cols = [];
      for (var ci = 0; ci < numCols; ci++) {
        var adjusted = ci - colOffset;
        cols.push(adjusted >= 0 ? colLabel(adjusted) : null);
      }
      var rows = [];
      for (var ri = 0; ri < numRows; ri++) {
        var rowNum = ri + 1 - rowOffset;
        rows.push(rowNum >= 1 ? rowNum : null);
      }

      // Compute center coordinates for each column and row
      // colCenters[i] = center longitude of column i
      var colCenters = [];
      for (var c = 0; c < numCols; c++) {
        colCenters.push((vLines[c].lng + vLines[c + 1].lng) / 2);
      }
      // rowCenters[i] = center latitude of row i
      var rowCenters = [];
      for (var r = 0; r < numRows; r++) {
        rowCenters.push((hLines[r].lat + hLines[r + 1].lat) / 2);
      }

      // Compute labeled area bounds (only the cells that have labels)
      var labeledSouth = hLines[numRows].lat;  // bottom of last row
      var labeledNorth = hLines[0].lat;         // top of first row
      var labeledWest = vLines[0].lng;          // left of first col
      var labeledEast = vLines[numCols].lng;    // right of last col

      // Restrict to labeled cells only
      if (colOffset > 0 && colOffset < numCols) {
        labeledWest = vLines[colOffset].lng;
      }
      if (rowOffset > 0 && rowOffset < numRows) {
        labeledNorth = hLines[rowOffset].lat;
      }

      var gridBounds = L.latLngBounds(
        [labeledSouth, labeledWest],
        [labeledNorth, labeledEast]
      );

      _gridMeta = {
        cols: cols,
        rows: rows,
        colCenters: colCenters,
        rowCenters: rowCenters,
        colOffset: colOffset,
        rowOffset: rowOffset,
        bounds: gridBounds,
        hLines: hLines,
        vLines: vLines,
        numCols: numCols,
        numRows: numRows,
        polylines: _gridPolylines
      };

      // Save current view, lock map to grid bounds, center
      _gridPrevBounds = map.getBounds();
      // Zoom avant seulement si on est trop dezoome pour voir le carroyage ;
      // si on est deja plus zoome, on garde le niveau actuel.
      var fitZoom = map.getBoundsZoom(gridBounds, false, [30, 30]);
      if (map.getZoom() < fitZoom) {
        map.fitBounds(gridBounds, { padding: [30, 30], animate: true });
      }
      setTimeout(function () {
        map.setMaxBounds(gridBounds.pad(0.02));
        map.setMinZoom(15);
      }, 300);

      buildStickyHeaders();

      map.on("move", updateStickyHeaders);
      map.on("move", updateHighlightBands);
      map.on("zoom", updateStickyHeaders);
      map.on("zoomend", updateGridLineWeight);
      map.on("mousemove", onGridMouseMove);
      map.on("mouseout", onGridMouseOut);
      updateGridLineWeight();
    };

    if (_gridData) {
      doRender(_gridData);
    } else {
      fetch("/api/grid-ref")
        .then(function (r) { return r.json(); })
        .then(doRender)
        .catch(function (err) {
          console.error("[MapView] Erreur carroyage:", err);
        });
    }
  }

  // --- Zoom-dependent grid line weight ---
  function updateGridLineWeight() {
    if (!_gridMeta || !_gridMeta.polylines) return;
    var zoom = map.getZoom();
    var weight = zoom >= 18 ? 1.8 : 1;
    var opacity = zoom >= 18 ? 0.75 : 0.6;
    _gridMeta.polylines.forEach(function (pl) {
      pl.setStyle({ weight: weight, opacity: opacity });
    });
  }

  // --- Mouse hover: highlight col/row bands + headers ---
  var _gridHighlightCol = null;
  var _gridHighlightRow = null;
  var _gridColBand = null;  // vertical band div
  var _gridRowBand = null;  // horizontal band div

  function ensureHighlightBands() {
    if (!_gridHeadersEl) return;
    if (!_gridColBand) {
      _gridColBand = document.createElement("div");
      _gridColBand.className = "grid-band grid-band-col";
      _gridHeadersEl.appendChild(_gridColBand);
    }
    if (!_gridRowBand) {
      _gridRowBand = document.createElement("div");
      _gridRowBand.className = "grid-band grid-band-row";
      _gridHeadersEl.appendChild(_gridRowBand);
    }
  }

  function onGridMouseMove(e) {
    if (!_gridMeta || !_gridHeadersEl || !map) return;
    var latlng = e.latlng;
    var info = activeGridInfo();

    ensureHighlightBands();

    // Find which column the mouse is in
    var col = null;
    for (var ci = 0; ci < info.numCols; ci++) {
      var leftLng = info.vLines[ci].lng;
      var rightLng = info.vLines[ci + 1].lng;
      if (latlng.lng >= leftLng && latlng.lng < rightLng) {
        col = ci;
        break;
      }
    }

    // Find which row the mouse is in
    var row = null;
    for (var ri = 0; ri < info.numRows; ri++) {
      var topLat = info.hLines[ri].lat;
      var botLat = info.hLines[ri + 1].lat;
      if (latlng.lat <= topLat && latlng.lat > botLat) {
        row = ri;
        break;
      }
    }

    // Update column band + header highlight
    if (col !== _gridHighlightCol) {
      if (_gridHighlightCol !== null) {
        var oldColEl = _gridHeadersEl.querySelector('.grid-sticky-col[data-idx="' + _gridHighlightCol + '"]');
        if (oldColEl) oldColEl.classList.remove("grid-highlight");
      }
      if (col !== null) {
        var newColEl = _gridHeadersEl.querySelector('.grid-sticky-col[data-idx="' + col + '"]');
        if (newColEl) newColEl.classList.add("grid-highlight");
        var leftPt = map.latLngToContainerPoint([0, info.vLines[col].lng]);
        var rightPt = map.latLngToContainerPoint([0, info.vLines[col + 1].lng]);
        _gridColBand.style.left = leftPt.x + "px";
        _gridColBand.style.width = (rightPt.x - leftPt.x) + "px";
        _gridColBand.style.display = "";
      } else {
        _gridColBand.style.display = "none";
      }
      _gridHighlightCol = col;
    }

    // Update row band + header highlight
    if (row !== _gridHighlightRow) {
      if (_gridHighlightRow !== null) {
        var oldRowEl = _gridHeadersEl.querySelector('.grid-sticky-row[data-idx="' + _gridHighlightRow + '"]');
        if (oldRowEl) oldRowEl.classList.remove("grid-highlight");
      }
      if (row !== null) {
        var newRowEl = _gridHeadersEl.querySelector('.grid-sticky-row[data-idx="' + row + '"]');
        if (newRowEl) newRowEl.classList.add("grid-highlight");
        // Position the horizontal band
        var topPt = map.latLngToContainerPoint([info.hLines[row].lat, 0]);
        var botPt = map.latLngToContainerPoint([info.hLines[row + 1].lat, 0]);
        _gridRowBand.style.top = topPt.y + "px";
        _gridRowBand.style.height = (botPt.y - topPt.y) + "px";
        _gridRowBand.style.display = "";
      } else {
        _gridRowBand.style.display = "none";
      }
      _gridHighlightRow = row;
    }
  }

  function onGridMouseOut() {
    if (!_gridHeadersEl) return;
    if (_gridHighlightCol !== null) {
      var el = _gridHeadersEl.querySelector('.grid-sticky-col[data-idx="' + _gridHighlightCol + '"]');
      if (el) el.classList.remove("grid-highlight");
      _gridHighlightCol = null;
    }
    if (_gridHighlightRow !== null) {
      var el2 = _gridHeadersEl.querySelector('.grid-sticky-row[data-idx="' + _gridHighlightRow + '"]');
      if (el2) el2.classList.remove("grid-highlight");
      _gridHighlightRow = null;
    }
    if (_gridColBand) _gridColBand.style.display = "none";
    if (_gridRowBand) _gridRowBand.style.display = "none";
  }

  // Reposition bands on map move (they are in pixel space)
  function updateHighlightBands() {
    if (!_gridMeta || !map) return;
    var info = activeGridInfo();
    if (_gridHighlightCol !== null && _gridColBand && _gridHighlightCol < info.numCols) {
      var leftPt = map.latLngToContainerPoint([0, info.vLines[_gridHighlightCol].lng]);
      var rightPt = map.latLngToContainerPoint([0, info.vLines[_gridHighlightCol + 1].lng]);
      _gridColBand.style.left = leftPt.x + "px";
      _gridColBand.style.width = (rightPt.x - leftPt.x) + "px";
    }
    if (_gridHighlightRow !== null && _gridRowBand && _gridHighlightRow < info.numRows) {
      var topPt = map.latLngToContainerPoint([info.hLines[_gridHighlightRow].lat, 0]);
      var botPt = map.latLngToContainerPoint([info.hLines[_gridHighlightRow + 1].lat, 0]);
      _gridRowBand.style.top = topPt.y + "px";
      _gridRowBand.style.height = (botPt.y - topPt.y) + "px";
    }
  }

  // ==========================================================================
  // CARROYAGE 25m (sous-grille)
  // ==========================================================================

  function updateGrid25ButtonVisibility() {
    var wrap = document.getElementById("map-grid25-wrap");
    if (!wrap) return;
    wrap.style.display = (_gridVisible && map.getZoom() >= 18) ? "" : "none";
    // Auto-hide 25m grid if zoomed out
    if (_grid25Visible && map.getZoom() < 18) {
      clearGrid25();
      _grid25Visible = false;
      var btn = document.getElementById("map-grid25-btn");
      if (btn) btn.classList.remove("active");
      // Rebuild 100m headers after leaving sub-grid
      buildStickyHeaders();
    }
  }

  function toggleGrid25() {
    _grid25Visible = !_grid25Visible;
    var btn = document.getElementById("map-grid25-btn");
    if (btn) btn.classList.toggle("active", _grid25Visible);
    if (_grid25Visible) {
      renderGrid25();
    } else {
      clearGrid25();
      // Rebuild 100m headers
      buildStickyHeaders();
    }
  }

  function clearGrid25() {
    if (_grid25Layer) {
      try { map.removeLayer(_grid25Layer); } catch (e) {}
      _grid25Layer = null;
    }
    _grid25Meta = null;
  }

  function renderGrid25() {
    clearGrid25();
    if (!map || !_gridData || !_gridData.lines_25 || !_gridMeta) return;

    var lines25 = _gridData.lines_25;
    var hLines = lines25.h_lines || [];
    var vLines = lines25.v_lines || [];
    var numCols = lines25.num_cols || (vLines.length - 1);
    var numRows = lines25.num_rows || (hLines.length - 1);

    _grid25Layer = L.layerGroup().addTo(map);

    // Draw 25m grid lines (thinner, different shade)
    hLines.forEach(function (l) {
      _grid25Layer.addLayer(L.polyline(
        [[l.lat, l.lng_start], [l.lat, l.lng_end]],
        { color: "#fb923c", weight: 1, opacity: 0.7, dashArray: "6 4", interactive: false }
      ));
    });
    vLines.forEach(function (l) {
      _grid25Layer.addLayer(L.polyline(
        [[l.lat_start, l.lng], [l.lat_end, l.lng]],
        { color: "#fb923c", weight: 1, opacity: 0.7, dashArray: "6 4", interactive: false }
      ));
    });

    // Compute 25m column/row centers
    var colCenters25 = [];
    for (var c = 0; c < numCols; c++) {
      colCenters25.push((vLines[c].lng + vLines[c + 1].lng) / 2);
    }
    var rowCenters25 = [];
    for (var r = 0; r < numRows; r++) {
      rowCenters25.push((hLines[r].lat + hLines[r + 1].lat) / 2);
    }

    // Map each 25m column to parent 100m column + sub-index (A-D)
    var meta100 = _gridMeta;
    var colLabels25 = [];
    for (var ci = 0; ci < numCols; ci++) {
      var lng = colCenters25[ci];
      var parentCol = null;
      for (var pi = 0; pi < meta100.numCols; pi++) {
        if (lng >= meta100.vLines[pi].lng && lng < meta100.vLines[pi + 1].lng) {
          parentCol = pi;
          break;
        }
      }
      if (parentCol === null || !meta100.cols[parentCol]) {
        colLabels25.push(null);
        continue;
      }
      // Find sub-index: count how many 25m columns are in this parent
      var subIdx = 0;
      for (var si = ci - 1; si >= 0; si--) {
        var sLng = colCenters25[si];
        if (sLng < meta100.vLines[parentCol].lng) break;
        subIdx++;
      }
      var subLetter = String.fromCharCode(65 + Math.min(subIdx, 3)); // A-D
      colLabels25.push(meta100.cols[parentCol] + subLetter);
    }

    // Map each 25m row to parent 100m row + sub-index (1-4)
    var rowLabels25 = [];
    for (var ri = 0; ri < numRows; ri++) {
      var lat = rowCenters25[ri];
      var parentRow = null;
      for (var pri = 0; pri < meta100.numRows; pri++) {
        if (lat <= meta100.hLines[pri].lat && lat > meta100.hLines[pri + 1].lat) {
          parentRow = pri;
          break;
        }
      }
      if (parentRow === null || !meta100.rows[parentRow]) {
        rowLabels25.push(null);
        continue;
      }
      var subRowIdx = 0;
      for (var sri = ri - 1; sri >= 0; sri--) {
        var sLat = rowCenters25[sri];
        if (sLat > meta100.hLines[parentRow].lat) break;
        subRowIdx++;
      }
      var subNum = Math.min(subRowIdx, 3) + 1; // 1-4
      rowLabels25.push(String(meta100.rows[parentRow]) + subNum);
    }

    _grid25Meta = {
      hLines: hLines,
      vLines: vLines,
      colCenters: colCenters25,
      rowCenters: rowCenters25,
      colLabels: colLabels25,
      rowLabels: rowLabels25,
      numCols: numCols,
      numRows: numRows
    };

    // Rebuild sticky headers with 25m labels
    buildStickyHeaders();
  }

  // --- Sticky headers (HTML overlay) ---

  // Returns the active grid data (25m if active, else 100m)
  function activeGridInfo() {
    if (_grid25Visible && _grid25Meta) {
      return {
        colLabels: _grid25Meta.colLabels,
        rowLabels: _grid25Meta.rowLabels,
        colCenters: _grid25Meta.colCenters,
        rowCenters: _grid25Meta.rowCenters,
        hLines: _grid25Meta.hLines,
        vLines: _grid25Meta.vLines,
        numCols: _grid25Meta.numCols,
        numRows: _grid25Meta.numRows
      };
    }
    return {
      colLabels: _gridMeta.cols,
      rowLabels: _gridMeta.rows,
      colCenters: _gridMeta.colCenters,
      rowCenters: _gridMeta.rowCenters,
      hLines: _gridMeta.hLines,
      vLines: _gridMeta.vLines,
      numCols: _gridMeta.numCols,
      numRows: _gridMeta.numRows
    };
  }

  function buildStickyHeaders() {
    if (_gridHeadersEl) _gridHeadersEl.remove();
    // Reset highlight state (old DOM elements are now detached)
    _gridColBand = null;
    _gridRowBand = null;
    _gridHighlightCol = null;
    _gridHighlightRow = null;
    if (!_gridMeta) return;

    var container = document.getElementById("cockpit-map");
    if (!container) return;

    var info = activeGridInfo();

    var wrap = document.createElement("div");
    wrap.className = "grid-sticky-wrap";
    wrap.id = "grid-sticky-wrap";

    // Top header bar
    var topBar = document.createElement("div");
    topBar.className = "grid-sticky-top";
    topBar.id = "grid-sticky-top";
    info.colLabels.forEach(function (col, idx) {
      if (!col) return;
      var el = document.createElement("span");
      el.className = "grid-sticky-col";
      el.textContent = col;
      el.dataset.idx = idx;
      topBar.appendChild(el);
    });
    wrap.appendChild(topBar);

    // Left header bar
    var leftBar = document.createElement("div");
    leftBar.className = "grid-sticky-left";
    leftBar.id = "grid-sticky-left";
    info.rowLabels.forEach(function (row, idx) {
      if (!row) return;
      var el = document.createElement("span");
      el.className = "grid-sticky-row";
      el.textContent = String(row);
      el.dataset.idx = idx;
      leftBar.appendChild(el);
    });
    wrap.appendChild(leftBar);

    // Corner badge
    var corner = document.createElement("div");
    corner.className = "grid-sticky-corner";
    var cornerIco = document.createElement("span");
    cornerIco.className = "material-symbols-outlined";
    cornerIco.style.fontSize = "14px";
    cornerIco.textContent = _grid25Visible ? "grid_4x4" : "grid_3x3";
    corner.appendChild(cornerIco);
    wrap.appendChild(corner);

    container.appendChild(wrap);
    _gridHeadersEl = wrap;

    updateStickyHeaders();
  }

  function updateStickyHeaders() {
    if (!_gridMeta || !map || !_gridHeadersEl) return;

    var info = activeGridInfo();
    var mapBounds = map.getBounds();

    var topBar = document.getElementById("grid-sticky-top");
    if (topBar) {
      var colEls = topBar.querySelectorAll(".grid-sticky-col");
      colEls.forEach(function (el) {
        var idx = parseInt(el.dataset.idx, 10);
        var lng = info.colCenters[idx];
        if (lng < mapBounds.getWest() || lng > mapBounds.getEast()) {
          el.style.display = "none";
          return;
        }
        var pt = map.latLngToContainerPoint([info.rowCenters[0], lng]);
        el.style.left = pt.x + "px";
        el.style.display = "";
      });
    }

    var leftBar = document.getElementById("grid-sticky-left");
    if (leftBar) {
      var rowEls = leftBar.querySelectorAll(".grid-sticky-row");
      rowEls.forEach(function (el) {
        var idx = parseInt(el.dataset.idx, 10);
        var lat = info.rowCenters[idx];
        if (lat < mapBounds.getSouth() || lat > mapBounds.getNorth()) {
          el.style.display = "none";
          return;
        }
        var pt = map.latLngToContainerPoint([lat, info.colCenters[0]]);
        el.style.top = pt.y + "px";
        el.style.display = "";
      });
    }
  }

  // ==========================================================================
  // LABEL DECLUTTERING (zoom >= 15)
  // ==========================================================================

  var _declutterLines = null;
  var _declutterStore = []; // [{marker, originalLatLng}]
  var _declutterTimer = null;

  function scheduleDeclutter() {
    if (_declutterTimer) clearTimeout(_declutterTimer);
    _declutterTimer = setTimeout(declutterLabels, 150);
  }

  function clearDeclutter() {
    _declutterStore.forEach(function (item) {
      item.marker.setLatLng(item.originalLatLng);
    });
    _declutterStore = [];
    if (_declutterLines) {
      try { map.removeLayer(_declutterLines); } catch (e) {}
      _declutterLines = null;
    }
  }

  function declutterLabels() {
    clearDeclutter();
    if (!map || map.getZoom() < 15) return;

    // Collect visible markers (point markers + polygon label markers)
    var items = [];
    Object.keys(categoryLayers).forEach(function (catId) {
      var data = categoryLayers[catId];
      if (!data || !data.group || enabledCategories[catId] === false) return;
      if (!map.hasLayer(data.group)) return;

      data.group.eachLayer(function (layer) {
        var cd = layer._cockpitData;
        if (!cd) return;
        if (typeof layer.getLatLng !== "function") return;
        var el = layer.getElement ? layer.getElement() : null;
        if (!el || el.style.display === "none") return;

        // Check route color visibility
        if (cd.routeColor && enabledRouteColors[catId] &&
            enabledRouteColors[catId][cd.routeColor] === false) return;

        var latlng = layer.getLatLng();
        // Only include markers visible in current viewport
        if (!map.getBounds().contains(latlng)) return;

        var rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;

        var pt = map.latLngToContainerPoint(latlng);
        items.push({
          marker: layer,
          latlng: latlng,
          cx: pt.x,
          cy: pt.y,
          w: rect.width,
          h: rect.height,
          // Bounding box
          x1: pt.x - rect.width / 2,
          y1: pt.y - rect.height / 2,
          x2: pt.x + rect.width / 2,
          y2: pt.y + rect.height / 2,
          moved: false
        });
      });
    });

    if (items.length < 2) return;

    // Sort: leftmost first, then topmost
    items.sort(function (a, b) { return a.x1 - b.x1 || a.y1 - b.y1; });

    var PAD = 4; // px padding between labels

    // Greedy overlap resolution - multiple passes
    for (var pass = 0; pass < 4; pass++) {
      var moved = false;
      for (var i = 0; i < items.length; i++) {
        for (var j = i + 1; j < items.length; j++) {
          if (!rectsOverlap(items[i], items[j], PAD)) continue;

          // Push item j away from item i
          var dx = items[j].cx - items[i].cx;
          var dy = items[j].cy - items[i].cy;
          var dist = Math.sqrt(dx * dx + dy * dy) || 1;

          // Overlap amounts
          var overlapX = (items[i].w / 2 + items[j].w / 2 + PAD) - Math.abs(items[j].cx - items[i].cx);
          var overlapY = (items[i].h / 2 + items[j].h / 2 + PAD) - Math.abs(items[j].cy - items[i].cy);

          // Push in the direction of least overlap
          var pushX = 0, pushY = 0;
          if (overlapX < overlapY) {
            pushX = (dx >= 0 ? 1 : -1) * (overlapX + 2);
          } else {
            pushY = (dy >= 0 ? 1 : -1) * (overlapY + 2);
          }

          items[j].cx += pushX;
          items[j].cy += pushY;
          items[j].x1 += pushX;
          items[j].x2 += pushX;
          items[j].y1 += pushY;
          items[j].y2 += pushY;
          items[j].moved = true;
          moved = true;
        }
      }
      if (!moved) break;
    }

    // Apply displacements
    _declutterLines = L.layerGroup().addTo(map);

    items.forEach(function (it) {
      if (!it.moved) return;

      var newPoint = L.point(it.cx, it.cy);
      var newLatLng = map.containerPointToLatLng(newPoint);

      _declutterStore.push({ marker: it.marker, originalLatLng: it.latlng });
      it.marker.setLatLng(newLatLng);

      // Leader line from new label position to original point
      var line = L.polyline([it.latlng, newLatLng], {
        color: "#888",
        weight: 1,
        opacity: 0.5,
        dashArray: "3 4",
        interactive: false
      });
      _declutterLines.addLayer(line);

      // Small dot at original position
      var dot = L.circleMarker(it.latlng, {
        radius: 3,
        color: "#888",
        fillColor: "#888",
        fillOpacity: 0.6,
        weight: 0,
        interactive: false
      });
      _declutterLines.addLayer(dot);
    });
  }

  function rectsOverlap(a, b, pad) {
    return !(a.x2 + pad <= b.x1 || b.x2 + pad <= a.x1 ||
             a.y2 + pad <= b.y1 || b.y2 + pad <= a.y1);
  }

  // ==========================================================================
  // PREFERENCES PANEL
  // ==========================================================================

  function buildPrefsPanel() {
    var existing = document.getElementById("map-prefs-panel");
    if (existing) existing.remove();

    var panel = document.createElement("div");
    panel.id = "map-prefs-panel";
    panel.className = "map-prefs-panel";
    panel.style.display = "none";

    // Header
    var header = document.createElement("div");
    header.className = "map-prefs-header";
    var title = document.createElement("span");
    title.textContent = "Preferences carte";
    var closeBtn = document.createElement("button");
    closeBtn.className = "map-prefs-close";
    closeBtn.title = "Fermer";
    var closeIco = document.createElement("span");
    closeIco.className = "material-symbols-outlined";
    closeIco.textContent = "close";
    closeBtn.appendChild(closeIco);
    closeBtn.addEventListener("click", togglePrefsPanel);
    header.appendChild(title);
    header.appendChild(closeBtn);
    panel.appendChild(header);

    // Tile selector
    var tileSection = document.createElement("div");
    tileSection.className = "map-prefs-section";
    var tileLabel = document.createElement("label");
    tileLabel.className = "map-prefs-label";
    tileLabel.textContent = "Fond de carte";
    tileLabel.setAttribute("for", "map-prefs-tile");
    var tileSelect = document.createElement("select");
    tileSelect.id = "map-prefs-tile";
    tileSelect.className = "form-input";
    var tileOpts = [
      { value: "osm", text: "OpenStreetMap" },
      { value: "sat-egis", text: "Satellite (Esri)" },
      { value: "sat-aco", text: "Satellite (ACO)" }
    ];
    tileOpts.forEach(function (o) {
      var opt = document.createElement("option");
      opt.value = o.value;
      opt.textContent = o.text;
      tileSelect.appendChild(opt);
    });
    tileSelect.value = currentTile;
    tileSection.appendChild(tileLabel);
    tileSection.appendChild(tileSelect);
    panel.appendChild(tileSection);

    // Categories section
    var catSection = document.createElement("div");
    catSection.className = "map-prefs-section";
    var catLabel = document.createElement("label");
    catLabel.className = "map-prefs-label";
    catLabel.textContent = "Categories";
    catLabel.setAttribute("for", "map-prefs-cats");
    catSection.appendChild(catLabel);
    var catList = document.createElement("div");
    catList.id = "map-prefs-cats";
    catList.className = "map-prefs-cats";
    catSection.appendChild(catList);

    // Select all / none
    var catActions = document.createElement("div");
    catActions.className = "map-prefs-cat-actions";
    var btnAll = document.createElement("button");
    btnAll.textContent = "Tout";
    btnAll.className = "map-cat-all";
    btnAll.addEventListener("click", function () {
      Object.keys(enabledCategories).forEach(function (k) { enabledCategories[k] = true; });
      updatePrefsCatList();
      applyFilters();
      updateToggleLabel();
    });
    var btnNone = document.createElement("button");
    btnNone.textContent = "Aucun";
    btnNone.className = "map-cat-none";
    btnNone.addEventListener("click", function () {
      Object.keys(enabledCategories).forEach(function (k) { enabledCategories[k] = false; });
      updatePrefsCatList();
      applyFilters();
      updateToggleLabel();
    });
    catActions.appendChild(btnAll);
    catActions.appendChild(btnNone);
    catSection.appendChild(catActions);
    panel.appendChild(catSection);

    // Save button
    var footer = document.createElement("div");
    footer.className = "map-prefs-footer";
    var saveBtn = document.createElement("button");
    saveBtn.className = "btn btn-primary";
    saveBtn.style.cssText = "width:100%; font-size:0.82rem;";
    var saveIco = document.createElement("span");
    saveIco.className = "material-symbols-outlined";
    saveIco.style.cssText = "font-size:14px; vertical-align:middle; margin-right:4px;";
    saveIco.textContent = "save";
    saveBtn.appendChild(saveIco);
    saveBtn.appendChild(document.createTextNode("Sauvegarder mon defaut"));
    saveBtn.addEventListener("click", saveUserPrefs);
    footer.appendChild(saveBtn);

    // Reset button
    var resetBtn = document.createElement("button");
    resetBtn.className = "btn btn-secondary";
    resetBtn.style.cssText = "width:100%; font-size:0.82rem; margin-top:6px;";
    var resetIco = document.createElement("span");
    resetIco.className = "material-symbols-outlined";
    resetIco.style.cssText = "font-size:14px; vertical-align:middle; margin-right:4px;";
    resetIco.textContent = "restart_alt";
    resetBtn.appendChild(resetIco);
    resetBtn.appendChild(document.createTextNode("Revenir aux defauts"));
    resetBtn.addEventListener("click", resetToDefaults);
    footer.appendChild(resetBtn);

    panel.appendChild(footer);

    // Append inside the Leaflet map div so it stays within the map bounds
    var mapContainer = document.getElementById("cockpit-map");
    if (mapContainer) mapContainer.appendChild(panel);

    // Prevent scroll from propagating to the map (zoom)
    L.DomEvent.disableScrollPropagation(panel);
  }

  function togglePrefsPanel() {
    var panel = document.getElementById("map-prefs-panel");
    if (!panel) return;
    prefsPanelOpen = !prefsPanelOpen;
    panel.style.display = prefsPanelOpen ? "" : "none";
    if (prefsPanelOpen) {
      updatePrefsCatList();
      var tileSelect = document.getElementById("map-prefs-tile");
      if (tileSelect) tileSelect.value = currentTile;
    }
  }

  function updatePrefsCatList() {
    var catList = document.getElementById("map-prefs-cats");
    if (!catList) return;
    catList.textContent = "";

    var cats = Object.keys(categoryLayers);
    if (!cats.length) {
      var empty = document.createElement("div");
      empty.style.cssText = "color:var(--muted); font-size:0.8rem; padding:8px 0;";
      empty.textContent = "Aucune categorie chargee";
      catList.appendChild(empty);
      return;
    }

    cats.forEach(function (catId) {
      var data = categoryLayers[catId];
      if (!data) return;

      var item = document.createElement("label");
      item.className = "map-prefs-cat-item";

      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = enabledCategories[catId] !== false;
      cb.addEventListener("change", function () {
        enabledCategories[catId] = cb.checked;
        applyFilters();
        updateToggleLabel();
        buildCategoryDropdown();
      });

      var iconDiv = document.createElement("div");
      iconDiv.className = "map-cat-icon";
      iconDiv.style.backgroundColor = data.color;
      var iconSpan = document.createElement("span");
      iconSpan.className = "material-symbols-outlined";
      iconSpan.textContent = data.icon;
      iconDiv.appendChild(iconSpan);

      var labelSpan = document.createElement("span");
      labelSpan.className = "map-cat-label";
      labelSpan.textContent = data.label;

      item.appendChild(cb);
      item.appendChild(iconDiv);
      item.appendChild(labelSpan);
      catList.appendChild(item);

      // Route color sub-filters
      if (data.hasRouteColors && data.routeColors && Object.keys(data.routeColors).length > 1) {
        var colorNames = Object.keys(data.routeColors).sort();
        var subList = document.createElement("div");
        subList.className = "map-prefs-route-colors";
        // Only show sub-filters when category is enabled
        if (enabledCategories[catId] === false) subList.style.display = "none";

        colorNames.forEach(function (colorName) {
          var hex = data.routeColors[colorName];
          if (!enabledRouteColors[catId]) enabledRouteColors[catId] = {};
          if (enabledRouteColors[catId][colorName] === undefined) enabledRouteColors[catId][colorName] = true;

          var colorItem = document.createElement("label");
          colorItem.className = "map-prefs-route-item";

          var colorCb = document.createElement("input");
          colorCb.type = "checkbox";
          colorCb.checked = enabledRouteColors[catId][colorName] !== false;
          colorCb.addEventListener("change", function () {
            enabledRouteColors[catId][colorName] = colorCb.checked;
            applyFilters();
            buildCategoryDropdown();
          });

          var dot = document.createElement("span");
          dot.className = "map-prefs-color-dot";
          dot.style.backgroundColor = hex;

          var colorLabel = document.createElement("span");
          colorLabel.textContent = colorName;

          colorItem.appendChild(colorCb);
          colorItem.appendChild(dot);
          colorItem.appendChild(colorLabel);
          subList.appendChild(colorItem);
        });

        catList.appendChild(subList);

        // Toggle sub-list visibility when category checkbox changes
        cb.addEventListener("change", function () {
          subList.style.display = cb.checked ? "" : "none";
        });
      }
    });
  }

  // ==========================================================================
  // LOAD & APPLY DEFAULTS / USER PREFS
  // ==========================================================================

  function loadMapPreferences() {
    return Promise.all([
      fetch("/api/map-defaults").then(function (r) { return r.json(); }).catch(function () { return {}; }),
      fetch("/api/map-preferences").then(function (r) { return r.json(); }).catch(function () { return {}; })
    ]).then(function (results) {
      mapDefaults = results[0] || {};
      if (!mapDefaults.hidden_categories) mapDefaults.hidden_categories = [];
      if (!mapDefaults.default_tile) mapDefaults.default_tile = "osm";

      var up = results[1] || {};
      userPrefs = (up.hidden_categories || up.default_tile || up.hidden_route_colors) ? up : null;
      prefsLoaded = true;

      // Apply tile preference
      var tilePref = userPrefs ? (userPrefs.default_tile || mapDefaults.default_tile) : mapDefaults.default_tile;
      if (tilePref && tilePref !== currentTile) {
        setTileLayer(tilePref);
      }
    });
  }

  function getHiddenCategories() {
    if (userPrefs && userPrefs.hidden_categories) return userPrefs.hidden_categories;
    return mapDefaults.hidden_categories || [];
  }

  function getHiddenRouteColors() {
    if (userPrefs && userPrefs.hidden_route_colors) return userPrefs.hidden_route_colors;
    return mapDefaults.hidden_route_colors || {};
  }

  function applyDefaultVisibility() {
    var hidden = getHiddenCategories();
    var hiddenSet = {};
    hidden.forEach(function (id) { hiddenSet[id] = true; });

    Object.keys(categoryLayers).forEach(function (catId) {
      if (hiddenSet[catId]) {
        enabledCategories[catId] = false;
      }
    });

    // Apply route color defaults
    var hiddenColors = getHiddenRouteColors();
    Object.keys(hiddenColors).forEach(function (catId) {
      if (!enabledRouteColors[catId]) enabledRouteColors[catId] = {};
      var colors = hiddenColors[catId];
      if (Array.isArray(colors)) {
        colors.forEach(function (c) { enabledRouteColors[catId][c] = false; });
      }
    });

    applyFilters();
  }

  function setTileLayer(tile) {
    if (!map) return;
    // Remove current
    if (currentTile === "osm" && map.hasLayer(tileLayerOSM)) map.removeLayer(tileLayerOSM);
    if (currentTile === "sat-egis" && map.hasLayer(tileLayerSatEGIS)) map.removeLayer(tileLayerSatEGIS);
    if (currentTile === "sat-aco" && map.hasLayer(tileLayerSatACO)) map.removeLayer(tileLayerSatACO);
    // Add new
    if (tile === "sat-egis") tileLayerSatEGIS.addTo(map);
    else if (tile === "sat-aco") tileLayerSatACO.addTo(map);
    else tileLayerOSM.addTo(map);
    currentTile = tile;
  }

  function saveUserPrefs() {
    var tileSelect = document.getElementById("map-prefs-tile");
    var tile = tileSelect ? tileSelect.value : currentTile;
    var hidden = [];
    Object.keys(enabledCategories).forEach(function (catId) {
      if (!enabledCategories[catId]) hidden.push(catId);
    });

    // Build hidden route colors map (only store disabled ones)
    var hiddenColors = {};
    Object.keys(enabledRouteColors).forEach(function (catId) {
      var disabled = [];
      Object.keys(enabledRouteColors[catId]).forEach(function (c) {
        if (!enabledRouteColors[catId][c]) disabled.push(c);
      });
      if (disabled.length) hiddenColors[catId] = disabled;
    });

    // Apply tile change immediately
    if (tile !== currentTile) setTileLayer(tile);

    var csrfToken = "";
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) csrfToken = meta.getAttribute("content") || "";

    fetch("/api/map-preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify({ hidden_categories: hidden, default_tile: tile, hidden_route_colors: hiddenColors })
    }).then(function (r) {
      if (!r.ok) throw new Error("Erreur " + r.status);
      return r.json();
    }).then(function () {
      userPrefs = { hidden_categories: hidden, default_tile: tile, hidden_route_colors: hiddenColors };
      if (typeof window.showToast === "function") {
        window.showToast("Preferences carte sauvegardees", "success");
      }
    }).catch(function (err) {
      if (typeof window.showToast === "function") {
        window.showToast("Erreur: " + err.message, "error");
      }
    });
  }

  function resetToDefaults() {
    // Apply global defaults
    var hidden = mapDefaults.hidden_categories || [];
    var hiddenSet = {};
    hidden.forEach(function (id) { hiddenSet[id] = true; });

    Object.keys(categoryLayers).forEach(function (catId) {
      enabledCategories[catId] = !hiddenSet[catId];
    });

    // Reset all route colors to enabled
    Object.keys(enabledRouteColors).forEach(function (catId) {
      Object.keys(enabledRouteColors[catId]).forEach(function (c) {
        enabledRouteColors[catId][c] = true;
      });
    });

    if (mapDefaults.default_tile && mapDefaults.default_tile !== currentTile) {
      setTileLayer(mapDefaults.default_tile);
    }

    var tileSelect = document.getElementById("map-prefs-tile");
    if (tileSelect) tileSelect.value = currentTile;

    applyFilters();
    updateToggleLabel();
    buildCategoryDropdown();
    updatePrefsCatList();

    // Delete user prefs
    var csrfToken = "";
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) csrfToken = meta.getAttribute("content") || "";

    fetch("/api/map-preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify({ hidden_categories: [], default_tile: "" })
    }).then(function () {
      userPrefs = null;
      if (typeof window.showToast === "function") {
        window.showToast("Preferences reintialisees aux defauts", "success");
      }
    }).catch(function () {});
  }

  // ==========================================================================
  // VIEW TOGGLE
  // ==========================================================================

  function switchView(view) {
    // Fermer le panel meteo si ouvert (avant le guard pour que ca marche meme si deja en vue map)
    var meteoPanel = document.getElementById("meteo-panel");
    if (meteoPanel) meteoPanel.style.display = "none";

    if (view === currentView) return;
    // Exit map fullscreen before switching views
    if (_mapFullscreen) toggleMapFullscreen();
    currentView = view;

    var timelineMain = document.getElementById("timeline-main");
    var mapMain = document.getElementById("map-main");
    var btnTimeline = document.getElementById("view-timeline-btn");
    var btnMap = document.getElementById("view-map-btn");
    var deptFilter = document.getElementById("timeline-dept-filter");
    var catFilter = document.getElementById("map-category-filter");
    var searchInput = document.getElementById("timeline-search-input");
    var timelineResults = document.getElementById("timeline-search-results");
    var mapResults = document.getElementById("map-search-results");

    if (view === "map") {
      if (timelineMain) timelineMain.style.display = "none";
      if (mapMain) mapMain.style.display = "block";
      if (btnTimeline) btnTimeline.classList.remove("active");
      if (btnMap) btnMap.classList.add("active");

      // Swap filters: hide dept + timeline results, show categories
      if (deptFilter) deptFilter.style.display = "none";
      if (catFilter) catFilter.style.display = "";
      if (timelineResults) timelineResults.style.display = "none";
      if (searchInput) {
        searchInput.placeholder = "Filtrer par nom...";
        searchInput.value = "";
      }

      if (!mapReady) initMap();
      setTimeout(function () { if (map) map.invalidateSize(); }, 100);
      loadEventMarkers();
    } else {
      if (timelineMain) timelineMain.style.display = "";
      if (mapMain) mapMain.style.display = "none";
      if (btnTimeline) btnTimeline.classList.add("active");
      if (btnMap) btnMap.classList.remove("active");

      // Swap filters: show dept, hide categories
      if (deptFilter) deptFilter.style.display = "";
      if (catFilter) catFilter.style.display = "none";
      if (timelineResults) timelineResults.style.display = "";
      if (searchInput) {
        searchInput.placeholder = "Rechercher (activite, categorie, lieu, remarque)...";
        searchInput.value = "";
      }
    }
  }

  // ==========================================================================
  // LOAD EVENT MARKERS
  // ==========================================================================

  /**
   * Pre-charge les donnees carte (parametrages, categories, GeoJSON) en arriere-plan.
   * Appele des qu'un evenement est selectionne, meme si on est en vue timeline.
   */
  function preloadMapData() {
    var ev = window.selectedEvent;
    var yr = window.selectedYear;
    if (!ev || !yr) return;

    var cacheKey = ev + ":" + yr;
    if (_preloadCache.key === cacheKey && _preloadCache.paramData) return; // deja en cache

    _preloadCache.key = cacheKey;
    _preloadCache.paramData = null;
    _preloadCache.gmCategories = null;
    _preloadCache.geoJsons = {};

    Promise.all([
      fetch("/get_parametrage?event=" + encodeURIComponent(ev) + "&year=" + encodeURIComponent(yr))
        .then(function (r) { return r.json(); }),
      fetch("/get_gm_categories")
        .then(function (r) { return r.json(); })
        .catch(function () { return []; })
    ]).then(function (results) {
      if (_preloadCache.key !== cacheKey) return; // evenement change entre-temps
      _preloadCache.paramData = results[0];
      _preloadCache.gmCategories = results[1] || [];

      // Pre-charger les GeoJSON de chaque categorie
      var cats = _preloadCache.gmCategories;
      cats.forEach(function (cat) {
        var collection = cat.collection;
        if (!collection || _preloadCache.geoJsons[collection]) return;
        fetch("/gm_collection_data/" + encodeURIComponent(collection))
          .then(function (r) { return r.json(); })
          .then(function (geojson) {
            if (_preloadCache.key === cacheKey) {
              _preloadCache.geoJsons[collection] = geojson;
            }
          })
          .catch(function () {});
      });
    }).catch(function (err) {
      console.error("[MapView] Erreur preload:", err);
    });
  }

  function loadEventMarkers() {
    if (!map) return;

    const ev = window.selectedEvent;
    const yr = window.selectedYear;
    if (!ev || !yr) return;

    // Clear existing
    clearAllLayers();

    var cacheKey = ev + ":" + yr;
    var useCache = _preloadCache.key === cacheKey && _preloadCache.paramData;

    if (useCache) {
      // Utiliser les donnees pre-chargees
      var paramData = _preloadCache.paramData;
      gmCategories = _preloadCache.gmCategories || [];
      if (!paramData || typeof paramData !== "object") return;
      gmCategories.forEach(function (cat) {
        renderCategoryLayer(cat, paramData);
      });
    } else {
      // Fallback: fetch classique
      Promise.all([
        fetch("/get_parametrage?event=" + encodeURIComponent(ev) + "&year=" + encodeURIComponent(yr))
          .then(function (r) { return r.json(); }),
        fetch("/get_gm_categories")
          .then(function (r) { return r.json(); })
          .catch(function () { return []; })
      ]).then(function (results) {
        var paramData = results[0];
        gmCategories = results[1] || [];
        if (!paramData || typeof paramData !== "object") return;
        gmCategories.forEach(function (cat) {
          renderCategoryLayer(cat, paramData);
        });
      }).catch(function (err) {
        console.error("[MapView] Erreur chargement:", err);
      });
    }
  }

  // ==========================================================================
  // RENDER CATEGORY LAYER
  // ==========================================================================

  function renderCategoryLayer(catConfig, paramData) {
    var catId = catConfig._id;
    var collection = catConfig.collection;
    var icon = catConfig.icon || "place";
    var defaultColor = getColor(icon);
    var label = catConfig.label || collection;
    var sc = catConfig.scheduleConfig || {};

    // Get items from parametrage
    var dataKey = catConfig.dataKey || collection;
    var items = paramData[dataKey];
    if (!items) return;

    // Normalize to array
    var itemArray;
    if (Array.isArray(items)) {
      itemArray = items;
    } else if (typeof items === "object") {
      itemArray = Object.values(items);
    } else {
      return;
    }

    // Filter active items only
    itemArray = itemArray.filter(function (it) {
      return it.active !== false;
    });

    if (!itemArray.length) return;

    // Utiliser le cache GeoJSON si disponible, sinon fetch
    var cachedGeoJson = _preloadCache.geoJsons[collection];
    var geoPromise = cachedGeoJson
      ? Promise.resolve(cachedGeoJson)
      : fetch("/gm_collection_data/" + encodeURIComponent(collection)).then(function (r) { return r.json(); });

    geoPromise.then(function (geojson) {
        var features = geojson.features || geojson || [];

        var layerGroup = L.layerGroup().addTo(map);
        var hasRouteColors = !!(sc.hasRouteColor);
        categoryLayers[catId] = { group: layerGroup, label: label, icon: icon, color: defaultColor, hasRouteColors: hasRouteColors, routeColors: {} };

        // Resolve mapping paths to get the correct property keys
        var mapping = catConfig.mapping || {};
        var nameMapping = mapping.name || "";
        // Extract the property key from mapping path like "properties.Nom" -> "Nom"
        var namePropKey = nameMapping.startsWith("properties.") ? nameMapping.slice(11) : null;

        features.forEach(function (feature) {
          if (!feature.geometry) return;

          // Match feature to parametrage item using mapping-aware name lookup
          var featureId = feature.properties?._id_feature || feature.properties?._id;
          var featureName = "";
          if (namePropKey && feature.properties) {
            featureName = feature.properties[namePropKey] || "";
          }
          if (!featureName && feature.properties) {
            featureName = feature.properties.Name || feature.properties.name || feature.properties.Nom || feature.properties.NOM || feature.properties.nom || "";
          }

          var item = itemArray.find(function (it) {
            return it.id === featureId || it._id === featureId || it.name === featureName;
          });

          if (!item) return;

          var displayName = item.name || featureName || label;
          var geomType = feature.geometry.type;

          // Determine color: routeColor if available, otherwise category default
          var colorPromise;
          var itemRouteColor = (sc.hasRouteColor && item.routeColor) ? item.routeColor : null;
          if (itemRouteColor) {
            colorPromise = resolveRouteColor(itemRouteColor);
          } else {
            colorPromise = Promise.resolve(defaultColor);
          }

          colorPromise.then(function (color) {
            // Track route colors for this category
            if (itemRouteColor) {
              categoryLayers[catId].routeColors[itemRouteColor] = color;
              // Initialize route color filter if not set
              if (!enabledRouteColors[catId]) enabledRouteColors[catId] = {};
              if (enabledRouteColors[catId][itemRouteColor] === undefined) {
                enabledRouteColors[catId][itemRouteColor] = true;
              }
            }
            if (geomType === "Point" || geomType === "MultiPoint") {
              renderPointMarker(feature, item, catConfig, displayName, icon, color, layerGroup, itemRouteColor);
            } else if (geomType === "Polygon" || geomType === "MultiPolygon") {
              renderPolygonLayer(feature, item, catConfig, displayName, icon, color, layerGroup, itemRouteColor);
            }
          });
        });
      })
      .catch(function () {
        // Collection may not exist, silently ignore
      });
  }

  // ==========================================================================
  // RENDER POINT MARKER
  // ==========================================================================

  function renderPointMarker(feature, item, catConfig, displayName, icon, color, layerGroup, routeColorName) {
    var coords = feature.geometry.coordinates;
    if (!coords || coords.length < 2) return;

    // GeoJSON is [lng, lat], Leaflet wants [lat, lng]
    var lat = coords[1];
    var lng = coords[0];

    var labelHtml = document.createElement("div");
    labelHtml.style.cssText = "background-color:" + color + ";color:white;padding:4px 6px;border-radius:6px;font-size:11px;font-family:Outfit,sans-serif;text-align:center;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,0.25);";

    var iconEl = document.createElement("span");
    iconEl.className = "material-symbols-outlined";
    iconEl.style.cssText = "font-size:14px;vertical-align:middle;margin-right:3px;";
    iconEl.textContent = icon;
    labelHtml.appendChild(iconEl);
    labelHtml.appendChild(document.createTextNode(displayName));

    var markerIcon = L.divIcon({
      html: labelHtml.outerHTML,
      className: "cockpit-marker",
      iconSize: null
    });

    var marker = L.marker([lat, lng], { icon: markerIcon }).addTo(layerGroup);

    // Store data for filtering
    marker._cockpitData = {
      name: displayName,
      category: catConfig.label,
      catId: catConfig._id,
      icon: icon,
      routeColor: routeColorName || null
    };

    marker.on("click", function () {
      marker.unbindPopup();
      var popupContent = generatePopup(item, catConfig, feature);
      marker.bindPopup(popupContent, {
        maxWidth: 350,
        closeButton: true,
        className: "cockpit-popup"
      }).openPopup();
    });
  }

  // ==========================================================================
  // RENDER POLYGON LAYER
  // ==========================================================================

  function renderPolygonLayer(feature, item, catConfig, displayName, icon, color, layerGroup, routeColorName) {
    var geom = feature.geometry;
    var latlngs;

    if (geom.type === "Polygon") {
      latlngs = geom.coordinates[0].map(function (c) { return [c[1], c[0]]; });
    } else {
      // MultiPolygon: first polygon
      latlngs = geom.coordinates[0][0].map(function (c) { return [c[1], c[0]]; });
    }

    var polygon = L.polygon(latlngs, {
      color: color,
      fillColor: color,
      fillOpacity: 0.35,
      weight: 2
    }).addTo(layerGroup);

    // Centroid label
    var centroid = getCentroid(latlngs);

    var labelHtml = '<div style="background-color:' + color + ';color:white;padding:3px 6px;border-radius:6px;font-size:11px;font-family:Outfit,sans-serif;text-align:center;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,0.25);">' +
      '<span class="material-symbols-outlined" style="font-size:14px;vertical-align:middle;margin-right:3px;">' + icon + '</span>' +
      escapeHtml(displayName) + '</div>';

    var labelIcon = L.divIcon({ html: labelHtml, className: "cockpit-marker", iconSize: null });
    var labelMarker = L.marker(centroid, { icon: labelIcon }).addTo(layerGroup);

    var cockpitData = { name: displayName, category: catConfig.label, catId: catConfig._id, icon: icon, routeColor: routeColorName || null };
    labelMarker._cockpitData = cockpitData;
    polygon._cockpitData = cockpitData;
    // Link polygon and label so filter can toggle both
    labelMarker._linkedPolygon = polygon;
    polygon._linkedMarker = labelMarker;

    var clickHandler = function () {
      labelMarker.unbindPopup();
      var popupContent = generatePopup(item, catConfig, feature);
      labelMarker.bindPopup(popupContent, { maxWidth: 350, closeButton: true, className: "cockpit-popup" }).openPopup();
    };

    polygon.on("click", clickHandler);
    labelMarker.on("click", clickHandler);
  }

  // ==========================================================================
  // POPUP
  // ==========================================================================

  function generatePopup(item, catConfig, feature) {
    var icon = catConfig.icon || "place";
    var color = getColor(icon);
    var sc = catConfig.scheduleConfig || {};
    var name = item.name || item._paramKey || (feature && feature.properties ? (feature.properties.Name || feature.properties.NOM || feature.properties.Nom) : "") || catConfig.label;

    var html = '<h4><span class="material-symbols-outlined" style="font-size:18px;color:' + color + ';">' + icon + '</span> ' + escapeHtml(name) + '</h4>';

    // --- Access ---
    if (item.access) {
      var badges = [];
      if (item.access.public) badges.push("Public");
      if (item.access.orga || item.access.organisation) badges.push("Organisation");
      if (item.access.vip) badges.push("VIP");
      if (badges.length) {
        html += '<div class="popup-field"><span class="popup-label">Acces</span><span class="popup-value">' + badges.join(", ") + '</span></div>';
      }
    }

    // --- Control ---
    if (item.controle && item.controle.visible) {
      var ct = item.controle.type || "Visuel";
      var ctText = ct;
      if (ct === "PDA" && item.controle.number) ctText = item.controle.number + " PDA";
      else if (ct === "TRIPODE" && item.controle.number) ctText = item.controle.number + " Tripodes";
      else if (ct === "VISUEL") ctText = "Visuel";
      html += '<div class="popup-field"><span class="popup-label">Controle</span><span class="popup-value">' + ctText + '</span></div>';
    }

    // --- Capacity / Jauge ---
    if (item.jauge && item.jauge.visible) {
      var cap = (item.capacite_pratique && item.capacite_pratique !== "" && item.capacite_pratique !== "0") ? item.capacite_pratique : item.capacite;
      if (cap) html += '<div class="popup-field"><span class="popup-label">Capacite</span><span class="popup-value">' + cap + '</span></div>';
      if (item.vente) html += '<div class="popup-field"><span class="popup-label">Vente</span><span class="popup-value">' + item.vente + '</span></div>';

      // Ticket types
      if (item.ticket) {
        var tix = [];
        if (item.ticket.digital) tix.push("Digital");
        if (item.ticket.mobile) tix.push("Mobile");
        if (item.ticket.sticker) tix.push("Sticker");
        if (item.ticket.thermique) tix.push("Thermique");
        if (item.ticket.voucher) tix.push("Voucher");
        if (tix.length) html += '<div class="popup-field"><span class="popup-label">Tickets</span><span class="popup-value">' + tix.join(", ") + '</span></div>';
      }
    } else if (item.capacite) {
      // Simple capacity without jauge
      html += '<div class="popup-field"><span class="popup-label">Capacite</span><span class="popup-value">' + item.capacite + '</span></div>';
    } else if (item.capacity) {
      // Hospitalite style
      html += '<div class="popup-field"><span class="popup-label">Capacite</span><span class="popup-value">' + item.capacity + '</span></div>';
    }

    // --- Card fields from category config ---
    if (catConfig.cardFields && catConfig.cardFields.length) {
      var cfMapping = catConfig.mapping || {};
      // Build a resolved item with mapped values from feature.properties
      var resolvedItem = {};
      Object.keys(cfMapping).forEach(function (key) {
        var path = cfMapping[key];
        if (typeof path === "string" && path.startsWith("properties.")) {
          var propKey = path.slice(11);
          if (feature && feature.properties && feature.properties[propKey] != null) {
            resolvedItem[key] = feature.properties[propKey];
          }
        }
      });

      catConfig.cardFields.forEach(function (cf) {
        var val = item[cf.key];
        // Fallback: check resolved mapped values, then raw feature properties
        if (val == null || val === "") {
          val = resolvedItem[cf.key];
        }
        if (val == null || val === "") {
          val = (feature && feature.properties) ? feature.properties[cf.key] : null;
        }
        if (val == null || val === "") return;

        var display;
        if (cf.formula) {
          // Evaluate formula — formulas come from admin-managed category config in DB
          try {
            var mergedItem = {};
            Object.keys(resolvedItem).forEach(function (k) { mergedItem[k] = resolvedItem[k]; });
            Object.keys(item).forEach(function (k) { mergedItem[k] = item[k]; });
            var value = parseFloat(val) || 0;
            var fn = new Function("item", "value", "return (" + cf.formula + ");"); // trusted admin config
            var result = fn(mergedItem, value);
            if (result == null || result === "" || result === 0) return;
            display = String(result);
          } catch (e) {
            display = String(val);
          }
        } else {
          if (String(val) === "0") return;
          display = String(val);
        }
        var label = cf.label || cf.key;
        if (cf.decimals != null) {
          var num = parseFloat(display);
          if (!isNaN(num)) display = num.toFixed(cf.decimals);
        }
        if (cf.suffix) display += " " + cf.suffix;
        html += '<div class="popup-field"><span class="popup-label">' + escapeHtml(label) + '</span><span class="popup-value">' + escapeHtml(display) + '</span></div>';
      });
    }

    // --- Schedule ---
    if (item.dates && item.dates.length) {
      var accessTypes = (sc.accessTypes && sc.accessTypes.length) ? sc.accessTypes : null;
      html += generateScheduleHtml(item.dates, accessTypes);
    }

    // --- Description ---
    if (item.description && String(item.description).trim()) {
      html += '<div class="popup-desc">' + escapeHtml(item.description) + '</div>';
    }

    // --- Comments ---
    if (item.comments) {
      var cmts = [];
      if (Array.isArray(item.comments)) cmts = item.comments.filter(Boolean);
      else if (typeof item.comments === "string" && item.comments.trim()) cmts = [item.comments];
      if (cmts.length) {
        html += '<div class="popup-comments"><strong>Commentaires</strong><ul>';
        cmts.forEach(function (c) { html += '<li>' + escapeHtml(c) + '</li>'; });
        html += '</ul></div>';
      }
    }

    return html;
  }

  // --- Day names ---
  var DAY_NAMES = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];

  function formatDateWithDay(isoDate) {
    if (!isoDate || isoDate.length < 10) return isoDate || "";
    var parts = isoDate.split("-");
    var y = parseInt(parts[0], 10);
    var m = parseInt(parts[1], 10) - 1;
    var day = parseInt(parts[2], 10);
    var dt = new Date(y, m, day);
    var dayName = DAY_NAMES[dt.getDay()];
    return dayName + " " + parts[2] + "/" + parts[1];
  }

  function isToday(isoDate) {
    if (!isoDate) return false;
    return isoDate === new Date().toISOString().slice(0, 10);
  }

  var CLOSED_CELL = '<span class="sched-closed"><span class="material-symbols-outlined">block</span></span>';
  var H24_CELL = '<span class="sched-24h">24h</span>';

  function formatSlotCell(slot) {
    if (!slot) return '<span class="sched-closed"><span class="material-symbols-outlined">block</span></span>';
    if (slot.closed) return CLOSED_CELL;
    if (slot.is24h) return H24_CELL;
    var o = slot.open || slot.openTime || "\u2014";
    var c = slot.close || slot.closeTime || "\u2014";
    return '<span class="sched-open">' + o + " \u2013 " + c + '</span>';
  }

  // --- Schedule table ---
  function generateScheduleHtml(dates, accessTypes) {
    if (!dates || !dates.length) return "";

    var hasAccessTypes = accessTypes && dates[0] && (dates[0].public || dates[0].organisation || dates[0].vip);

    var html = '<div class="popup-schedule-wrap">';
    html += '<div class="popup-schedule-title"><span class="material-symbols-outlined" style="font-size:16px;">schedule</span> Horaires</div>';
    html += '<table class="popup-schedule"><thead><tr><th class="sched-date-col">Date</th>';

    if (hasAccessTypes) {
      accessTypes.forEach(function (at) {
        var label = at === "public" ? "Public" : at === "organisation" ? "Orga" : at === "vip" ? "VIP" : at;
        html += '<th>' + label + '</th>';
      });
    } else {
      html += '<th>Horaires</th>';
    }
    html += '</tr></thead><tbody>';

    // Helper: check if a date entry or access-type slot is 24h
    function slotIs24h(entry, at) {
      if (at) {
        var s = entry[at];
        return s && s.is24h;
      }
      return !!entry.is24h;
    }
    function slotOpen(entry, at) {
      if (at) { var s = entry[at]; return s ? (s.open || s.openTime || null) : null; }
      return entry.openTime || null;
    }
    function slotClose(entry, at) {
      if (at) { var s = entry[at]; return s ? (s.close || s.closeTime || null) : null; }
      return entry.closeTime || null;
    }

    // Build a lookup by date for prev/next day checks
    var dateIndex = {};
    dates.forEach(function (d, idx) { dateIndex[d.date] = idx; });

    function getPrevEntry(idx) { return idx > 0 ? dates[idx - 1] : null; }
    function getNextEntry(idx) { return idx < dates.length - 1 ? dates[idx + 1] : null; }

    // Clean boundary times adjacent to 24h days
    function cleanOpen(open, prevEntry, at) {
      if (open !== "00:00") return open;
      if (prevEntry && slotIs24h(prevEntry, at)) return null;
      return open;
    }
    function cleanClose(close, nextEntry, at) {
      if (close !== "23:59" && close !== "00:00" && close !== "24:00") return close;
      if (nextEntry && slotIs24h(nextEntry, at)) return null;
      return close;
    }

    dates.forEach(function (d, idx) {
      var dateLabel = formatDateWithDay(d.date);
      var today = isToday(d.date);
      var rowClass = today ? ' class="sched-today"' : '';
      var prevEntry = getPrevEntry(idx);
      var nextEntry = getNextEntry(idx);

      html += '<tr' + rowClass + '><td class="sched-date-cell">' + dateLabel + '</td>';

      if (hasAccessTypes) {
        accessTypes.forEach(function (at) {
          var slot = d[at];
          if (!slot || slot.closed || slot.is24h) {
            html += '<td>' + formatSlotCell(slot) + '</td>';
          } else {
            var o = cleanOpen(slot.open || slot.openTime || "\u2014", prevEntry, at);
            var c = cleanClose(slot.close || slot.closeTime || "\u2014", nextEntry, at);
            if (!o && !c) {
              html += '<td>' + H24_CELL + '</td>';
            } else {
              var display = (o || "") + (o && c ? " \u2013 " : "") + (c || "");
              html += '<td><span class="sched-open">' + display + '</span></td>';
            }
          }
        });
      } else {
        // Simple format
        if (d.is24h) {
          html += '<td>' + H24_CELL + '</td>';
        } else {
          var open = cleanOpen(d.openTime || "\u2014", prevEntry, null);
          var close = cleanClose(d.closeTime || "\u2014", nextEntry, null);
          if (!open && !close) {
            html += '<td>' + H24_CELL + '</td>';
          } else {
            var display = (open || "") + (open && close ? " \u2013 " : "") + (close || "");
            html += '<td><span class="sched-open">' + display + '</span></td>';
          }
        }
      }
      html += '</tr>';
    });

    html += '</tbody></table></div>';
    return html;
  }

  // ==========================================================================
  // CATEGORY FILTER DROPDOWN
  // ==========================================================================

  var enabledCategories = {}; // catId -> true/false

  function buildCategoryDropdown() {
    var dropdown = document.getElementById("map-cat-dropdown");
    if (!dropdown) return;

    dropdown.textContent = "";

    var cats = Object.keys(categoryLayers);
    if (!cats.length) {
      var empty = document.createElement("div");
      empty.style.cssText = "padding:12px;color:var(--muted);font-size:0.84rem;text-align:center;";
      empty.textContent = "Aucune categorie chargee";
      dropdown.appendChild(empty);
      return;
    }

    cats.forEach(function (catId) {
      var data = categoryLayers[catId];
      if (!data) return;

      // Default: all enabled
      if (enabledCategories[catId] === undefined) enabledCategories[catId] = true;

      var item = document.createElement("label");
      item.className = "map-cat-item";

      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = enabledCategories[catId];
      cb.addEventListener("change", function () {
        enabledCategories[catId] = cb.checked;
        applyFilters();
        updateToggleLabel();
      });

      var iconDiv = document.createElement("div");
      iconDiv.className = "map-cat-icon";
      iconDiv.style.backgroundColor = data.color;
      var iconSpan = document.createElement("span");
      iconSpan.className = "material-symbols-outlined";
      iconSpan.textContent = data.icon;
      iconDiv.appendChild(iconSpan);

      var labelSpan = document.createElement("span");
      labelSpan.className = "map-cat-label";
      labelSpan.textContent = data.label;

      item.appendChild(cb);
      item.appendChild(iconDiv);
      item.appendChild(labelSpan);
      dropdown.appendChild(item);

      // Route color sub-filters in dropdown
      if (data.hasRouteColors && data.routeColors && Object.keys(data.routeColors).length > 1) {
        var colorNames = Object.keys(data.routeColors).sort();
        var subList = document.createElement("div");
        subList.className = "map-cat-route-colors";
        if (!enabledCategories[catId]) subList.style.display = "none";

        colorNames.forEach(function (colorName) {
          var hex = data.routeColors[colorName];
          if (!enabledRouteColors[catId]) enabledRouteColors[catId] = {};
          if (enabledRouteColors[catId][colorName] === undefined) enabledRouteColors[catId][colorName] = true;

          var colorItem = document.createElement("label");
          colorItem.className = "map-cat-route-item";

          var colorCb = document.createElement("input");
          colorCb.type = "checkbox";
          colorCb.checked = enabledRouteColors[catId][colorName] !== false;
          colorCb.addEventListener("change", function () {
            enabledRouteColors[catId][colorName] = colorCb.checked;
            applyFilters();
          });

          var dot = document.createElement("span");
          dot.className = "map-prefs-color-dot";
          dot.style.backgroundColor = hex;

          var colorLabel = document.createElement("span");
          colorLabel.style.cssText = "font-size:0.8rem;";
          colorLabel.textContent = colorName;

          colorItem.appendChild(colorCb);
          colorItem.appendChild(dot);
          colorItem.appendChild(colorLabel);
          subList.appendChild(colorItem);
        });

        dropdown.appendChild(subList);

        cb.addEventListener("change", function () {
          subList.style.display = cb.checked ? "" : "none";
        });
      }
    });

    // Actions: tout / rien
    var actions = document.createElement("div");
    actions.className = "map-cat-actions";

    var btnAll = document.createElement("button");
    btnAll.className = "map-cat-all";
    btnAll.textContent = "Tout";
    btnAll.addEventListener("click", function () {
      Object.keys(enabledCategories).forEach(function (k) { enabledCategories[k] = true; });
      // Also enable all route colors
      Object.keys(enabledRouteColors).forEach(function (catId) {
        Object.keys(enabledRouteColors[catId]).forEach(function (c) { enabledRouteColors[catId][c] = true; });
      });
      buildCategoryDropdown();
      applyFilters();
      updateToggleLabel();
    });

    var btnNone = document.createElement("button");
    btnNone.className = "map-cat-none";
    btnNone.textContent = "Aucun";
    btnNone.addEventListener("click", function () {
      Object.keys(enabledCategories).forEach(function (k) { enabledCategories[k] = false; });
      buildCategoryDropdown();
      applyFilters();
      updateToggleLabel();
    });

    actions.appendChild(btnAll);
    actions.appendChild(btnNone);
    dropdown.appendChild(actions);

    updateToggleLabel();
  }

  function updateToggleLabel() {
    var label = document.getElementById("map-cat-toggle-label");
    if (!label) return;
    var total = Object.keys(enabledCategories).length;
    var active = Object.values(enabledCategories).filter(Boolean).length;
    if (active === total) {
      label.textContent = "Toutes (" + total + ")";
    } else {
      label.textContent = active + "/" + total + " cat.";
    }
  }

  // ==========================================================================
  // FILTER (text + categories)
  // ==========================================================================

  function applyFilters() {
    if (!map || currentView !== "map") return;

    // Restore original positions before re-filtering
    clearDeclutter();

    var searchInput = document.getElementById("timeline-search-input");
    var q = (searchInput ? searchInput.value : "").toLowerCase().trim();

    Object.keys(categoryLayers).forEach(function (catId) {
      var data = categoryLayers[catId];
      if (!data || !data.group) return;

      var catEnabled = enabledCategories[catId] !== false;

      // If entire category is disabled, hide the whole layer group
      if (!catEnabled) {
        if (map.hasLayer(data.group)) map.removeLayer(data.group);
        return;
      }

      // Category enabled: add group if not on map
      if (!map.hasLayer(data.group)) map.addLayer(data.group);

      // Text filter within category
      data.group.eachLayer(function (layer) {
        var cd = layer._cockpitData;
        if (!cd) {
          // This is a polygon without _cockpitData linked from a marker - skip,
          // it will be handled via its linked marker
          if (!layer._linkedMarker) return;
          cd = layer._linkedMarker._cockpitData;
        }

        var name = (cd.name || "").toLowerCase();
        var cat = (cd.category || "").toLowerCase();
        var match = !q || name.indexOf(q) !== -1 || cat.indexOf(q) !== -1;

        // Route color filter
        if (match && cd.routeColor && enabledRouteColors[catId]) {
          if (enabledRouteColors[catId][cd.routeColor] === false) {
            match = false;
          }
        }

        // Toggle marker visibility
        if (layer.getElement) {
          var el = layer.getElement();
          if (el) el.style.display = match ? "" : "none";
        }

        // Toggle linked polygon
        if (layer._linkedPolygon) {
          if (match) {
            layer._linkedPolygon.setStyle({ opacity: 1, fillOpacity: 0.35 });
          } else {
            layer._linkedPolygon.setStyle({ opacity: 0, fillOpacity: 0 });
          }
        }
      });
    });

    // Re-declutter after filter changes
    scheduleDeclutter();
  }

  // ==========================================================================
  // UTILS
  // ==========================================================================

  // --- Map fullscreen toggle (within app layout, not browser fullscreen) ---
  var _mapFullscreen = false;
  function toggleMapFullscreen() {
    _mapFullscreen = !_mapFullscreen;
    var mainContent = document.getElementById("main-content");
    var btn = document.getElementById("map-fullscreen-btn");
    var ico = btn ? btn.querySelector(".material-symbols-outlined") : null;

    if (mainContent) mainContent.classList.toggle("map-fullscreen", _mapFullscreen);
    if (ico) ico.textContent = _mapFullscreen ? "fullscreen_exit" : "fullscreen";
    if (btn) btn.title = _mapFullscreen ? "Quitter le plein ecran" : "Plein ecran";

    setTimeout(function () { if (map) map.invalidateSize(); }, 100);
  }

  // ==========================================================================
  // 3P — PORTES / PORTAILS / PORTILLONS
  // ==========================================================================

  var _portesVisible = false;
  var _portesLayer = null;
  var _portesAllNames = []; // for search, loaded once
  var _portesMarkers = {};  // keyed by _id_feature to avoid duplicates

  function togglePortes() {
    _portesVisible = !_portesVisible;
    var btn = document.getElementById("map-portes-btn");
    if (btn) btn.classList.toggle("active", _portesVisible);

    if (_portesVisible) {
      if (!_portesLayer) _portesLayer = L.layerGroup().addTo(map);
      loadPortesInView();
      map.on("moveend", loadPortesInView);
    } else {
      map.off("moveend", loadPortesInView);
      if (_portesLayer) {
        map.removeLayer(_portesLayer);
        _portesLayer = null;
      }
      _portesMarkers = {};
    }
  }

  var _3pIcons = {
    "Portillon": "door_front",
    "Portail": "garage",
    "Acces Piste": "sports_motorsports",
    "": "door_front"
  };

  function get3pIcon(nature) {
    return _3pIcons[nature] || _3pIcons[(nature || "").replace(/[éè]/g, "e")] || "door_front";
  }

  function loadPortesInView() {
    if (!_portesVisible || !map || !_portesLayer) return;
    var b = map.getBounds();
    var url = "/api/3p?south=" + b.getSouth() + "&west=" + b.getWest() +
              "&north=" + b.getNorth() + "&east=" + b.getEast();

    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.features) return;
        // Remove markers no longer in view
        var inView = {};
        data.features.forEach(function (f) {
          var id = f.properties._id_feature || f.properties.Nom;
          inView[id] = true;
        });
        Object.keys(_portesMarkers).forEach(function (id) {
          if (!inView[id]) {
            _portesLayer.removeLayer(_portesMarkers[id]);
            delete _portesMarkers[id];
          }
        });
        // Add new markers
        data.features.forEach(function (f) {
          var p = f.properties;
          var id = p._id_feature || p.Nom;
          if (_portesMarkers[id]) return;
          var coords = f.geometry.coordinates;
          var lat = coords[1], lng = coords[0];
          var nom = p.Nom || "";
          var nature = p.Nature || "";
          var icon = get3pIcon(nature);

          var marker = L.marker([lat, lng], {
            icon: L.divIcon({
              className: "portes-pin",
              html: '<span class="material-symbols-outlined" style="font-size:22px;color:#132646;">' + icon + '</span>',
              iconSize: [28, 28],
              iconAnchor: [14, 14]
            })
          });

          var lines = ['<div class="portes-popup"><strong>' + nom + '</strong>'];
          if (nature) lines.push('<div style="color:var(--brand);font-size:0.82rem;font-weight:600;">' + nature + '</div>');
          var details = [];
          if (p["Accès"]) details.push(p["Accès"]);
          if (p.Zone) details.push(p.Zone);
          if (p["Largeur (cm)"]) details.push("L: " + p["Largeur (cm)"] + " cm");
          if (p["Hauteur (cm)"]) details.push("H: " + p["Hauteur (cm)"] + " cm");
          if (p.Verrous) details.push(p.Verrous);
          if (details.length) lines.push('<div style="color:var(--muted);font-size:0.80rem;">' + details.join(' &bull; ') + '</div>');
          if (p.Commentaires) lines.push('<div style="color:var(--muted);font-size:0.78rem;font-style:italic;margin-top:3px;">' + p.Commentaires + '</div>');
          if (p.Photos) {
            var thumbUrl = "/api/3p/photo/thumb/" + encodeURIComponent(p.Photos);
            var origUrl = "/api/3p/photo/original/" + encodeURIComponent(p.Photos);
            lines.push(
              '<img class="portes-thumb" src="' + thumbUrl + '" alt="' + nom + '" ' +
              'onclick="window._open3pLightbox(\'' + origUrl + '\', \'' + nom.replace(/'/g, "\\'") + '\')" ' +
              'onerror="this.style.display=\'none\'" />'
            );
          }
          lines.push('</div>');
          marker.bindPopup(lines.join(""), { maxWidth: 260 });

          _portesLayer.addLayer(marker);
          _portesMarkers[id] = marker;
        });
      })
      .catch(function (err) {
        console.error("[MapView] Erreur 3P:", err);
      });
  }

  // Load all 3P names for search (once, regardless of layer visibility)
  function loadPortesForSearch() {
    fetch("/api/3p")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.features) return;
        _portesAllNames = data.features.map(function (f) {
          var p = f.properties;
          return {
            name: p.Nom || "",
            category: "3P",
            nature: p.Nature || "",
            description: [p.Nature, p["Accès"], p.Zone].filter(Boolean).join(" - "),
            lat: f.geometry.coordinates[1],
            lng: f.geometry.coordinates[0],
            icon: "door_front"
          };
        });
      })
      .catch(function () {});
  }

  // --- Tile layer cycling ---
  function cycleTileLayer() {
    if (!map) return;
    if (currentTile === "osm") {
      map.removeLayer(tileLayerOSM);
      tileLayerSatEGIS.addTo(map);
      currentTile = "sat-egis";
    } else if (currentTile === "sat-egis") {
      map.removeLayer(tileLayerSatEGIS);
      tileLayerSatACO.addTo(map);
      currentTile = "sat-aco";
    } else {
      map.removeLayer(tileLayerSatACO);
      tileLayerOSM.addTo(map);
      currentTile = "osm";
    }
  }

  // --- FitBounds on a specific item ---
  function fitBoundsOnItem(name) {
    if (!map || !name) return;
    var q = name.toLowerCase();
    var bounds = L.latLngBounds([]);

    Object.keys(categoryLayers).forEach(function (catId) {
      var data = categoryLayers[catId];
      if (!data || !data.group) return;
      data.group.eachLayer(function (layer) {
        var cd = layer._cockpitData;
        if (!cd) return;
        if ((cd.name || "").toLowerCase() !== q) return;

        if (layer.getBounds) {
          bounds.extend(layer.getBounds());
        } else if (layer.getLatLng) {
          bounds.extend(layer.getLatLng());
        }
        // Also include linked polygon
        if (layer._linkedPolygon && layer._linkedPolygon.getBounds) {
          bounds.extend(layer._linkedPolygon.getBounds());
        }
      });
    });

    // Also check 3P portes
    _portesAllNames.forEach(function (p) {
      if (p.name.toLowerCase() === q) {
        bounds.extend(L.latLng(p.lat, p.lng));
      }
    });

    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [60, 60], maxZoom: 18 });
      // Open popup if it's a 3P porte
      _portesAllNames.forEach(function (p) {
        if (p.name.toLowerCase() === q && _portesMarkers) {
          Object.keys(_portesMarkers).forEach(function (id) {
            var m = _portesMarkers[id];
            if (m && m.getLatLng) {
              var ll = m.getLatLng();
              if (Math.abs(ll.lat - p.lat) < 0.0001 && Math.abs(ll.lng - p.lng) < 0.0001) {
                m.openPopup();
              }
            }
          });
        }
      });
    }
  }

  // --- Reset all filters ---
  function resetFilters() {
    var searchInput = document.getElementById("timeline-search-input");
    if (searchInput) searchInput.value = "";

    // Re-enable all categories
    Object.keys(enabledCategories).forEach(function (k) { enabledCategories[k] = true; });
    buildCategoryDropdown();
    applyFilters();
  }

  function clearAllLayers() {
    Object.keys(categoryLayers).forEach(function (key) {
      var data = categoryLayers[key];
      if (data && data.group) {
        data.group.clearLayers();
        map.removeLayer(data.group);
      }
      delete categoryLayers[key];
    });
    enabledCategories = {};
    enabledRouteColors = {};
    autoColorIdx = 0;
  }

  function getCentroid(latlngs) {
    var lat = 0, lng = 0;
    for (var i = 0; i < latlngs.length; i++) {
      lat += latlngs[i][0];
      lng += latlngs[i][1];
    }
    return [lat / latlngs.length, lng / latlngs.length];
  }

  function escapeHtml(s) {
    if (!s) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ==========================================================================
  // WIRING
  // ==========================================================================

  // Rebuild dropdown after markers are loaded + apply default visibility
  var _origLoadEventMarkers = loadEventMarkers;
  loadEventMarkers = function () {
    // Load prefs once before first marker load
    var prefsPromise = prefsLoaded ? Promise.resolve() : loadMapPreferences();
    prefsPromise.then(function () {
      _origLoadEventMarkers();
      // Delay to let fetches complete, then apply defaults + rebuild dropdown
      setTimeout(function () {
        applyDefaultVisibility();
        buildCategoryDropdown();
        updateToggleLabel();
        scheduleDeclutter();
      }, 1500);
    });
  };

  document.addEventListener("DOMContentLoaded", function () {
    // View toggle buttons
    var btnTimeline = document.getElementById("view-timeline-btn");
    var btnMap = document.getElementById("view-map-btn");

    if (btnTimeline) btnTimeline.addEventListener("click", function () { switchView("timeline"); });
    if (btnMap) btnMap.addEventListener("click", function () { switchView("map"); });

    // Si le mode carte est force par les permissions de bloc
    if (window.__forceMapView) {
      currentView = "timeline"; // reset pour que switchView ne skip pas
      switchView("map");
    }

    // Text search filter + autocomplete for map
    var searchInput = document.getElementById("timeline-search-input");
    var searchResults = document.getElementById("timeline-search-results");
    var searchClear = document.getElementById("timeline-search-clear");

    if (searchInput) {
      searchInput.addEventListener("input", function () {
        if (currentView !== "map") return;
        applyFilters();
        showMapAutocomplete(this.value);
      });

      searchInput.addEventListener("keydown", function (e) {
        if (currentView !== "map") return;
        var list = document.getElementById("timeline-search-results");
        var items = list ? list.querySelectorAll("li") : [];
        var active = list ? list.querySelector("li.active") : null;
        var idx = -1;
        if (active) {
          for (var i = 0; i < items.length; i++) { if (items[i] === active) { idx = i; break; } }
        }

        if (e.key === "ArrowDown") {
          e.preventDefault();
          if (active) active.classList.remove("active");
          idx = (idx + 1) % items.length;
          if (items[idx]) { items[idx].classList.add("active"); items[idx].scrollIntoView({ block: "nearest" }); }
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          if (active) active.classList.remove("active");
          idx = idx <= 0 ? items.length - 1 : idx - 1;
          if (items[idx]) { items[idx].classList.add("active"); items[idx].scrollIntoView({ block: "nearest" }); }
        } else if (e.key === "Enter") {
          e.preventDefault();
          if (active) {
            active.click();
          } else {
            var q = this.value.trim();
            if (q) { fitBoundsOnItem(q); hideMapAutocomplete(); }
          }
        } else if (e.key === "Escape") {
          hideMapAutocomplete();
        }
      });
    }

    // Clear button: reset all map filters
    if (searchClear) {
      searchClear.addEventListener("click", function () {
        if (currentView === "map") {
          resetFilters();
          hideMapAutocomplete();
        }
      });
    }

    // Category dropdown toggle
    var catToggle = document.getElementById("map-cat-toggle");
    var catDropdown = document.getElementById("map-cat-dropdown");
    if (catToggle && catDropdown) {
      catToggle.addEventListener("click", function (e) {
        e.stopPropagation();
        catDropdown.classList.toggle("open");
      });
      document.addEventListener("click", function (e) {
        if (!catDropdown.contains(e.target) && e.target !== catToggle && !catToggle.contains(e.target)) {
          catDropdown.classList.remove("open");
        }
      });
    }

    // Reload markers when event/year changes
    var eventSelect = document.getElementById("event-select");
    var yearSelect = document.getElementById("year-select");

    if (eventSelect) {
      eventSelect.addEventListener("change", function () {
        if (currentView === "map") loadEventMarkers();
      });
    }
    if (yearSelect) {
      yearSelect.addEventListener("change", function () {
        if (currentView === "map") loadEventMarkers();
      });
    }
  });

  // --- Map autocomplete ---
  function getMapItems() {
    var items = [];
    var seen = {};
    Object.keys(categoryLayers).forEach(function (catId) {
      var data = categoryLayers[catId];
      if (!data || !data.group) return;
      data.group.eachLayer(function (layer) {
        var cd = layer._cockpitData;
        if (!cd || !cd.name || seen[cd.name]) return;
        seen[cd.name] = true;
        items.push({ name: cd.name, category: cd.category, icon: cd.icon });
      });
    });
    // Always include 3P (portes) in search
    _portesAllNames.forEach(function (p) {
      if (seen[p.name]) return;
      seen[p.name] = true;
      items.push({ name: p.name, category: "3P - " + p.description, icon: p.icon });
    });
    return items;
  }

  function showMapAutocomplete(query) {
    var list = document.getElementById("timeline-search-results");
    if (!list) return;

    var q = (query || "").toLowerCase().trim();
    if (!q || q.length < 2) {
      hideMapAutocomplete();
      return;
    }

    var items = getMapItems().filter(function (it) {
      return it.name.toLowerCase().indexOf(q) !== -1 || it.category.toLowerCase().indexOf(q) !== -1;
    }).slice(0, 12);

    if (!items.length) {
      hideMapAutocomplete();
      return;
    }

    list.textContent = "";
    items.forEach(function (it) {
      var li = document.createElement("li");
      li.style.cursor = "pointer";

      var titleSpan = document.createElement("span");
      titleSpan.className = "tsr-title";
      titleSpan.textContent = it.name;

      var metaSpan = document.createElement("span");
      metaSpan.className = "tsr-date";
      metaSpan.textContent = it.category;

      li.appendChild(titleSpan);
      li.appendChild(metaSpan);

      li.addEventListener("click", function () {
        var input = document.getElementById("timeline-search-input");
        if (input) input.value = it.name;
        applyFilters();
        fitBoundsOnItem(it.name);
        hideMapAutocomplete();
      });

      list.appendChild(li);
    });

    list.style.display = "block";
    list.classList.add("show");
  }

  function hideMapAutocomplete() {
    var list = document.getElementById("timeline-search-results");
    if (list && currentView === "map") {
      list.style.display = "none";
      list.classList.remove("show");
    }
  }

  // ==========================================================================
  // TARGET ROUTE (polyline from traffic widget)
  // ==========================================================================

  var _targetRouteLayer = null;

  var _targetAnimFrame = null;

  function drawTargetRoute() {
    var target = window._targetRoute;
    if (!target || !target.line || !target.line.length) return;

    // Remove previous
    clearTargetRoute();
    if (!map) return;

    // Waze coords: {x: longitude, y: latitude} -> Leaflet: [lat, lon]
    var latlngs = [];
    for (var i = 0; i < target.line.length; i++) {
      var pt = target.line[i];
      if (pt && pt.y != null && pt.x != null) {
        latlngs.push([pt.y, pt.x]);
      }
    }
    if (!latlngs.length) return;

    // Color by severity
    var colors = {0: "#22c55e", 1: "#22c55e", 2: "#eab308", 3: "#f97316", 4: "#ef4444"};
    var color = colors[target.severity] || "#6366f1";

    _targetRouteLayer = L.layerGroup();

    // 1) Glow layer (wider, semi-transparent behind)
    var glow = L.polyline(latlngs, {
      color: color,
      weight: 14,
      opacity: 0.2,
      lineCap: "round",
      lineJoin: "round",
      interactive: false
    });
    _targetRouteLayer.addLayer(glow);

    // 2) Base solid line (interactive for click to reopen popup)
    var baseLine = L.polyline(latlngs, {
      color: color,
      weight: 5,
      opacity: 0.9,
      lineCap: "round",
      lineJoin: "round",
      interactive: true
    });
    _targetRouteLayer.addLayer(baseLine);

    // 3) Animated dashed line on top (white dashes flowing)
    var dashLine = L.polyline(latlngs, {
      color: "#ffffff",
      weight: 3,
      opacity: 0.6,
      dashArray: "8 16",
      dashOffset: "0",
      lineCap: "round",
      lineJoin: "round",
      interactive: false
    });
    _targetRouteLayer.addLayer(dashLine);

    // Fluide = rapide, bouchon = lent (simule le trafic reel)
    var speedMap = {0: 0.6, 1: 0.5, 2: 0.35, 3: 0.2, 4: 0.1};
    var speed = speedMap[target.severity] || 0.5;
    var offset = 0;
    function animateDash() {
      offset = (offset - speed) % 24;
      var el = dashLine.getElement ? dashLine.getElement() : null;
      if (el) {
        el.style.strokeDashoffset = String(offset);
      }
      _targetAnimFrame = requestAnimationFrame(animateDash);
    }

    // Start marker (depart — green circle)
    var startLatLng = latlngs[0];
    var startIcon = L.divIcon({
      className: "target-route-marker target-start",
      html: '<span class="material-symbols-outlined">trip_origin</span>',
      iconSize: [28, 28],
      iconAnchor: [14, 14]
    });
    var startMarker = L.marker(startLatLng, {icon: startIcon, interactive: false});
    _targetRouteLayer.addLayer(startMarker);

    // End marker (arrivee — flag)
    var endLatLng = latlngs[latlngs.length - 1];
    var endIcon = L.divIcon({
      className: "target-route-marker target-end",
      html: '<span class="material-symbols-outlined">flag</span>',
      iconSize: [28, 28],
      iconAnchor: [14, 14]
    });
    var endMarker = L.marker(endLatLng, {icon: endIcon, interactive: false});
    _targetRouteLayer.addLayer(endMarker);

    // Popup at midpoint
    var mid = latlngs[Math.floor(latlngs.length / 2)];
    var delta = target.delta != null ? target.delta : 0;
    var deltaM = Math.floor(delta / 60);
    var deltaS = delta % 60;
    var deltaStr = deltaM > 0 ? "+" + deltaM + "m " + deltaS + "s" : "+" + deltaS + "s";
    var curM = Math.floor((target.currentTime || 0) / 60);
    var curS = (target.currentTime || 0) % 60;
    var histM = Math.floor((target.historicTime || 0) / 60);
    var histS = (target.historicTime || 0) % 60;

    var popupNode = document.createElement("div");
    popupNode.style.cssText = "font-family:Outfit,sans-serif;font-size:12px;line-height:1.4;min-width:220px;";

    var title = document.createElement("strong");
    title.style.cssText = "font-size:13px;display:block;margin-bottom:4px;";
    title.textContent = target.name || "Route";
    popupNode.appendChild(title);

    // 2-column grid for info
    var grid = document.createElement("div");
    grid.style.cssText = "display:grid;grid-template-columns:1fr 1fr;gap:2px 12px;";

    var infoLines = [
      {icon: "schedule", label: "Actuel", text: curM + "m " + (curS < 10 ? "0" : "") + curS + "s"},
      {icon: "history", label: "Moyen", text: histM + "m " + (histS < 10 ? "0" : "") + histS + "s"},
      {icon: "trending_up", label: "Retard", text: deltaStr},
      {icon: "speed", label: "Ratio", text: (target.ratio != null ? target.ratio.toFixed(2) + "x" : "--")},
    ];

    for (var j = 0; j < infoLines.length; j++) {
      var cell = document.createElement("div");
      cell.style.cssText = "display:flex;align-items:center;gap:4px;padding:1px 0;";
      var ico = document.createElement("span");
      ico.className = "material-symbols-outlined";
      ico.style.cssText = "font-size:14px;color:" + color + ";";
      ico.textContent = infoLines[j].icon;
      var txt = document.createElement("span");
      txt.style.cssText = "font-size:12px;";
      txt.textContent = infoLines[j].text;
      cell.appendChild(ico);
      cell.appendChild(txt);
      grid.appendChild(cell);
    }
    popupNode.appendChild(grid);

    // Status line
    var statusRow = document.createElement("div");
    statusRow.style.cssText = "margin-top:3px;font-size:11px;color:" + color + ";font-weight:600;text-transform:uppercase;letter-spacing:0.3px;";
    statusRow.textContent = target.status || "";
    popupNode.appendChild(statusRow);

    var closeBtn = document.createElement("button");
    closeBtn.textContent = "Fermer la route";
    closeBtn.style.cssText = "margin-top:6px;padding:4px 10px;border:none;border-radius:5px;background:" + color + ";color:#fff;font-size:11px;cursor:pointer;font-family:inherit;font-weight:600;width:100%;";
    closeBtn.addEventListener("click", function() {
      clearTargetRoute();
    });
    popupNode.appendChild(closeBtn);

    baseLine.bindPopup(popupNode, {
      closeOnClick: true,
      autoClose: true,
      className: "target-route-popup",
      maxWidth: 280
    });

    _targetRouteLayer.addTo(map);
    map.fitBounds(baseLine.getBounds(), {padding: [50, 50]});

    // Open popup after map is settled + start animation
    setTimeout(function() {
      baseLine.openPopup(mid);
      animateDash();
    }, 300);

    // Clear the global flag
    window._targetRoute = null;
  }

  function clearTargetRoute() {
    if (_targetAnimFrame) {
      cancelAnimationFrame(_targetAnimFrame);
      _targetAnimFrame = null;
    }
    if (_targetRouteLayer) {
      try { map.removeLayer(_targetRouteLayer); } catch(e) {}
      _targetRouteLayer = null;
    }
  }

  // Listen for custom event from traffic widget
  document.addEventListener("drawTargetRoute", function() {
    if (!mapReady) initMap();
    setTimeout(drawTargetRoute, 200);
  });

  // ==========================================================================
  // ALL ROUTES OVERLAY (traffic overview)
  // ==========================================================================

  var _allRoutesLayer = null;
  var _allRoutesAnimFrame = null;
  var _allRoutesDashLines = [];

  function drawAllRoutes() {
    var routes = window._allRoutesData;
    if (!routes || !routes.length) return;

    clearAllRoutes();
    clearTargetRoute();
    if (!map) return;

    var colors = {0: "#22c55e", 1: "#22c55e", 2: "#eab308", 3: "#f97316", 4: "#ef4444"};
    var weightMap = {0: 3, 1: 3, 2: 4, 3: 5, 4: 6};
    var glowMap   = {0: 10, 1: 10, 2: 12, 3: 14, 4: 16};
    var speedMap = {0: 0.6, 1: 0.5, 2: 0.35, 3: 0.2, 4: 0.1};
    _allRoutesLayer = L.layerGroup();
    _allRoutesDashLines = [];
    var bounds = L.latLngBounds([]);

    // Trier par severite croissante : vert d'abord (dessous), rouge en dernier (dessus)
    var sorted = routes.slice().sort(function(a, b){
      return (a.severity || 0) - (b.severity || 0);
    });

    for (var i = 0; i < sorted.length; i++) {
      var r = sorted[i];
      if (!r.line || !r.line.length) continue;

      var latlngs = [];
      for (var j = 0; j < r.line.length; j++) {
        var pt = r.line[j];
        if (pt && pt.y != null && pt.x != null) latlngs.push([pt.y, pt.x]);
      }
      if (!latlngs.length) continue;

      var sev = r.severity || 0;
      var color = colors[sev] || "#6366f1";
      var curM = Math.floor((r.currentTime || 0) / 60);
      var curS = (r.currentTime || 0) % 60;
      var delta = Math.max(0, (r.currentTime || 0) - (r.historicTime || 0));
      var deltaM = Math.floor(delta / 60);
      var deltaS = delta % 60;
      var deltaStr = deltaM > 0 ? "+" + deltaM + "m " + deltaS + "s" : "+" + deltaS + "s";
      var tooltip = r.terrain + " \u2014 " + curM + "m" + (curS < 10 ? "0" : "") + curS + "s (" + deltaStr + ")";

      var w = weightMap[sev] || 4;
      var gw = glowMap[sev] || 12;

      // Glow
      _allRoutesLayer.addLayer(L.polyline(latlngs, {
        color: color, weight: gw, opacity: 0.15,
        lineCap: "round", lineJoin: "round", interactive: false
      }));

      // Base line
      var baseLine = L.polyline(latlngs, {
        color: color, weight: w, opacity: 0.85,
        lineCap: "round", lineJoin: "round"
      });
      baseLine.bindTooltip(tooltip, {sticky: true, className: "traffic-overlay-tooltip"});
      _allRoutesLayer.addLayer(baseLine);

      // Animated dashes
      var dashLine = L.polyline(latlngs, {
        color: "#ffffff", weight: 2, opacity: 0.5,
        dashArray: "6 12", dashOffset: "0",
        lineCap: "round", lineJoin: "round", interactive: false
      });
      _allRoutesLayer.addLayer(dashLine);
      _allRoutesDashLines.push({line: dashLine, speed: speedMap[sev] || 0.5, offset: 0});

      bounds.extend(baseLine.getBounds());
    }

    _allRoutesLayer.addTo(map);
    if (bounds.isValid()) map.fitBounds(bounds, {padding: [40, 40]});

    // Start animation
    function animateAll() {
      for (var k = 0; k < _allRoutesDashLines.length; k++) {
        var d = _allRoutesDashLines[k];
        d.offset = (d.offset - d.speed) % 18;
        var el = d.line.getElement ? d.line.getElement() : null;
        if (el) el.style.strokeDashoffset = String(d.offset);
      }
      _allRoutesAnimFrame = requestAnimationFrame(animateAll);
    }
    setTimeout(animateAll, 100);

    window._allRoutesData = null;
  }

  function clearAllRoutes() {
    if (_allRoutesAnimFrame) {
      cancelAnimationFrame(_allRoutesAnimFrame);
      _allRoutesAnimFrame = null;
    }
    _allRoutesDashLines = [];
    if (_allRoutesLayer) {
      try { map.removeLayer(_allRoutesLayer); } catch(e) {}
      _allRoutesLayer = null;
    }
  }

  document.addEventListener("drawAllRoutes", function() {
    if (!mapReady) initMap();
    setTimeout(drawAllRoutes, 200);
  });

  document.addEventListener("clearAllRoutes", function() {
    clearAllRoutes();
  });

  // ==========================================================================
  // ALERT PIN (from traffic counters)
  // ==========================================================================

  var _alertPinLayer = null;

  function showAlertPin() {
    var data = window._alertPinData;
    if (!data) return;

    clearAlertPin();
    if (!map) return;

    var lat = data.lat;
    var lon = data.lon;
    if (lat == null || lon == null) return;

    _alertPinLayer = L.layerGroup();

    // Icon colors by type
    var iconColors = {
      'ACCIDENT': '#ef4444',
      'JAM': '#f59e0b',
      'HAZARD': '#f97316',
      'ROAD_CLOSED': '#8b5cf6'
    };
    var iconNames = {
      'ACCIDENT': 'car_crash',
      'JAM': 'traffic_jam',
      'HAZARD': 'warning',
      'ROAD_CLOSED': 'block'
    };

    var color = iconColors[data.type] || '#ef4444';
    var iconName = iconNames[data.type] || 'warning';

    // Pulse ring (animated)
    var pulseIcon = L.divIcon({
      className: 'alert-pin-pulse',
      html: '<div class="alert-pulse-ring" style="border-color:' + color + '"></div>',
      iconSize: [48, 48],
      iconAnchor: [24, 24]
    });
    var pulseMarker = L.marker([lat, lon], {icon: pulseIcon, interactive: false});
    _alertPinLayer.addLayer(pulseMarker);

    // Main pin
    var pinIcon = L.divIcon({
      className: 'alert-pin-marker',
      html: '<div class="alert-pin" style="background:' + color + '"><span class="material-symbols-outlined">' + iconName + '</span></div><div class="alert-pin-tail" style="border-top-color:' + color + '"></div>',
      iconSize: [36, 46],
      iconAnchor: [18, 46],
      popupAnchor: [0, -46]
    });
    var marker = L.marker([lat, lon], {icon: pinIcon});

    // Popup
    var popup = document.createElement('div');
    popup.style.cssText = 'font-family:Outfit,sans-serif;font-size:12px;line-height:1.4;min-width:180px;';

    var title = document.createElement('strong');
    title.style.cssText = 'font-size:13px;display:block;margin-bottom:4px;color:' + color + ';';
    title.textContent = data.typeFr || data.type;
    popup.appendChild(title);

    if (data.subtypeFr) {
      var sub = document.createElement('div');
      sub.style.cssText = 'font-size:11px;margin-bottom:4px;color:#94a3b8;';
      sub.textContent = data.subtypeFr;
      popup.appendChild(sub);
    }

    var details = [];
    if (data.street) details.push({icon: 'location_on', text: data.street});
    if (data.city) details.push({icon: 'location_city', text: data.city});
    if (data.date) details.push({icon: 'schedule', text: data.date});
    if (data.description) details.push({icon: 'description', text: data.description});

    for (var i = 0; i < details.length; i++) {
      var row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:4px;margin:1px 0;';
      var ico = document.createElement('span');
      ico.className = 'material-symbols-outlined';
      ico.style.cssText = 'font-size:13px;color:' + color + ';';
      ico.textContent = details[i].icon;
      var txt = document.createElement('span');
      txt.textContent = details[i].text;
      row.appendChild(ico);
      row.appendChild(txt);
      popup.appendChild(row);
    }

    if (data.index != null && data.total != null) {
      var pager = document.createElement('div');
      pager.style.cssText = 'margin-top:6px;font-size:10px;color:#64748b;text-align:center;';
      pager.textContent = (data.index + 1) + ' / ' + data.total;
      popup.appendChild(pager);
    }

    marker.bindPopup(popup, {
      className: 'target-route-popup',
      maxWidth: 250,
      closeOnClick: false,
      autoClose: false
    });
    _alertPinLayer.addLayer(marker);

    _alertPinLayer.addTo(map);
    map.setView([lat, lon], 16);
    setTimeout(function() { marker.openPopup(); }, 200);

    window._alertPinData = null;
  }

  function clearAlertPin() {
    if (_alertPinLayer) {
      try { map.removeLayer(_alertPinLayer); } catch(e) {}
      _alertPinLayer = null;
    }
  }

  document.addEventListener('showAlertPin', function() {
    if (!mapReady) initMap();
    setTimeout(showAlertPin, 200);
  });

  // ==========================================================================
  // ALL ALERT PINS
  // ==========================================================================

  var _allAlertPinsLayer = null;

  function showAllAlertPins() {
    var alerts = window._allAlertPinsData;
    if (!alerts || !alerts.length) return;

    clearAllAlertPins();
    clearAlertPin();
    if (!map) return;

    var iconColors = {
      'ACCIDENT': '#ef4444',
      'JAM': '#f59e0b',
      'HAZARD': '#f97316',
      'ROAD_CLOSED': '#8b5cf6'
    };
    var iconNames = {
      'ACCIDENT': 'car_crash',
      'JAM': 'traffic_jam',
      'HAZARD': 'warning',
      'ROAD_CLOSED': 'block'
    };

    _allAlertPinsLayer = L.layerGroup();
    var bounds = L.latLngBounds([]);

    for (var i = 0; i < alerts.length; i++) {
      var a = alerts[i];
      var lat = a.lat, lon = a.lon;
      if (lat == null || lon == null) continue;

      var color = iconColors[a.type] || '#ef4444';
      var iconName = iconNames[a.type] || 'warning';

      var pinIcon = L.divIcon({
        className: 'alert-pin-marker',
        html: '<div class="alert-pin" style="background:' + color + '"><span class="material-symbols-outlined">' + iconName + '</span></div><div class="alert-pin-tail" style="border-top-color:' + color + '"></div>',
        iconSize: [36, 46],
        iconAnchor: [18, 46],
        popupAnchor: [0, -46]
      });

      var marker = L.marker([lat, lon], {icon: pinIcon});

      // Build popup
      var popup = document.createElement('div');
      popup.style.cssText = 'font-family:Outfit,sans-serif;font-size:12px;line-height:1.4;min-width:160px;';

      var title = document.createElement('strong');
      title.style.cssText = 'font-size:13px;display:block;margin-bottom:3px;color:' + color + ';';
      title.textContent = a.typeFr || a.type;
      popup.appendChild(title);

      if (a.street) {
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:4px;';
        var ico = document.createElement('span');
        ico.className = 'material-symbols-outlined';
        ico.style.cssText = 'font-size:13px;color:' + color + ';';
        ico.textContent = 'location_on';
        row.appendChild(ico);
        row.appendChild(document.createTextNode(a.street));
        popup.appendChild(row);
      }

      if (a.date) {
        var dateRow = document.createElement('div');
        dateRow.style.cssText = 'font-size:10px;color:#64748b;margin-top:2px;';
        dateRow.textContent = a.date;
        popup.appendChild(dateRow);
      }

      marker.bindPopup(popup, {
        className: 'target-route-popup',
        maxWidth: 220
      });

      _allAlertPinsLayer.addLayer(marker);
      bounds.extend([lat, lon]);
    }

    _allAlertPinsLayer.addTo(map);
    if (bounds.isValid()) map.fitBounds(bounds, {padding: [40, 40], maxZoom: 19});

    window._allAlertPinsData = null;
  }

  function clearAllAlertPins() {
    if (_allAlertPinsLayer) {
      try { map.removeLayer(_allAlertPinsLayer); } catch(e) {}
      _allAlertPinsLayer = null;
    }
  }

  document.addEventListener('showAllAlertPins', function() {
    if (!mapReady) initMap();
    setTimeout(showAllAlertPins, 200);
  });

  // ==========================================================================
  // MEASUREMENT TOOLS
  // ==========================================================================

  var _measureMode = null; // null | "line" | "area" | "circle"
  var _measureLayer = null;
  var _measurePoints = [];
  var _measureGuide = null;
  var _measureLabels = [];
  var _measureTooltip = null;
  var _measureFinalized = false;
  var _measureFinalShape = null; // the finalized polygon/polyline for edit mode
  var _measureVertexMarkers = [];
  var SNAP_PX = 15; // snap distance in pixels to close polygon

  var ALL_MEASURE_IDS = ["measure-line", "measure-area", "measure-circle", "measure-edit", "measure-clear"];

  function toggleMeasureTool(mode) {
    if (mode === "clear") {
      clearMeasure();
      return;
    }
    if (mode === "edit") {
      enterEditMode();
      return;
    }
    if (_measureMode === mode) {
      clearMeasure();
      return;
    }
    clearMeasure();
    _measureMode = mode;
    _measureFinalized = false;

    ALL_MEASURE_IDS.forEach(function (id) {
      var btn = document.getElementById(id);
      if (btn) btn.classList.toggle("active", btn.dataset.mode === mode);
    });

    _measureLayer = L.layerGroup().addTo(map);
    _measurePoints = [];
    _measureVertexMarkers = [];

    map.getContainer().style.cursor = "crosshair";
    map.on("click", onMeasureClick);
    map.on("mousemove", onMeasureMouseMove);
    map.on("dblclick", onMeasureDblClick);
    map.doubleClickZoom.disable();

    showMeasureTooltip(mode === "line" ? "Cliquez pour tracer" : mode === "area" ? "Cliquez les sommets" : "Cliquez le centre");
  }

  function clearMeasure() {
    _measureMode = null;
    _measurePoints = [];
    _measureFinalized = false;
    _measureFinalShape = null;
    _measureVertexMarkers = [];
    if (_measureLayer) {
      try { map.removeLayer(_measureLayer); } catch (e) {}
      _measureLayer = null;
    }
    _measureGuide = null;
    _measureLabels = [];
    hideMeasureTooltip();

    ALL_MEASURE_IDS.forEach(function (id) {
      var btn = document.getElementById(id);
      if (btn) btn.classList.remove("active");
    });

    if (map) {
      map.off("click", onMeasureClick);
      map.off("mousemove", onMeasureMouseMove);
      map.off("dblclick", onMeasureDblClick);
      map.doubleClickZoom.enable();
      map.getContainer().style.cursor = "";
    }
  }

  function onMeasureClick(e) {
    if (!_measureMode || !_measureLayer || _measureFinalized) return;
    var latlng = e.latlng;

    if (_measureMode === "circle") {
      if (_measurePoints.length === 0) {
        _measurePoints.push(latlng);
        addMeasureVertex(latlng);
        showMeasureTooltip("Cliquez pour definir le rayon");
      } else {
        finalizeMeasureCircle(latlng);
      }
      return;
    }

    // Ignore duplicate click (from dblclick firing 2 clicks)
    if (_measurePoints.length > 0) {
      var lastPt = _measurePoints[_measurePoints.length - 1];
      if (lastPt.distanceTo(latlng) < 1) return;
    }

    // Area: snap to first point to close
    if (_measureMode === "area" && _measurePoints.length >= 3) {
      var firstPt = map.latLngToContainerPoint(_measurePoints[0]);
      var clickPt = map.latLngToContainerPoint(latlng);
      var dist = firstPt.distanceTo(clickPt);
      if (dist < SNAP_PX) {
        finalizeMeasureArea();
        return;
      }
    }

    _measurePoints.push(latlng);
    addMeasureVertex(latlng);

    if (_measureMode === "line") {
      showMeasureTooltip("Cliquez pour continuer, double-clic pour terminer");
    } else {
      showMeasureTooltip(_measurePoints.length < 3 ? "Cliquez les sommets (min. 3)" : "Cliquez proche du 1er point pour fermer");
    }
  }

  function onMeasureMouseMove(e) {
    if (!_measureMode || !_measureLayer || !_measurePoints.length || _measureFinalized) return;
    var latlng = e.latlng;

    if (_measureMode === "circle" && _measurePoints.length === 1) {
      if (_measureGuide) _measureLayer.removeLayer(_measureGuide);
      var radius = _measurePoints[0].distanceTo(latlng);
      _measureGuide = L.circle(_measurePoints[0], {
        radius: radius, color: "#6366f1", weight: 2, opacity: 0.7,
        fillColor: "#6366f1", fillOpacity: 0.1, dashArray: "6 4", interactive: false
      });
      _measureLayer.addLayer(_measureGuide);
      showMeasureTooltip("Rayon: " + formatDist(radius));
      return;
    }

    if (_measureMode === "line" || _measureMode === "area") {
      if (_measureGuide) _measureLayer.removeLayer(_measureGuide);
      var pts = _measurePoints.concat([latlng]);
      if (_measureMode === "area" && pts.length >= 3) {
        _measureGuide = L.polygon(pts, {
          color: "#6366f1", weight: 2, opacity: 0.5,
          fillColor: "#6366f1", fillOpacity: 0.08, dashArray: "6 4", interactive: false
        });
      } else {
        _measureGuide = L.polyline(pts, {
          color: "#6366f1", weight: 2, opacity: 0.5, dashArray: "6 4", interactive: false
        });
      }
      _measureLayer.addLayer(_measureGuide);

      var totalDist = computeTotalDistance(pts);
      var tip = "Distance: " + formatDist(totalDist);
      if (_measureMode === "area" && pts.length >= 3) {
        tip += " | Aire: " + formatArea(computeArea(pts));
      }
      showMeasureTooltip(tip);
    }
  }

  function onMeasureDblClick(e) {
    if (!_measureMode || _measureFinalized) return;
    L.DomEvent.stop(e);
    if (_measureMode === "line" && _measurePoints.length >= 2) finalizeMeasureLine();
    else if (_measureMode === "area" && _measurePoints.length >= 3) finalizeMeasureArea();
  }

  function finalizeMeasureLine() {
    if (_measureGuide) { _measureLayer.removeLayer(_measureGuide); _measureGuide = null; }

    // Remove trailing duplicate points (from dblclick)
    while (_measurePoints.length > 1 &&
           _measurePoints[_measurePoints.length - 1].distanceTo(_measurePoints[_measurePoints.length - 2]) < 1) {
      _measurePoints.pop();
    }
    if (_measurePoints.length < 2) return;

    _measureFinalized = true;

    var line = L.polyline(_measurePoints, {
      color: "#6366f1", weight: 3, opacity: 0.9, interactive: false
    });
    _measureLayer.addLayer(line);
    _measureFinalShape = line;

    var totalDist = computeTotalDistance(_measurePoints);

    // Segment labels on each segment midpoint (only for 3+ points)
    if (_measurePoints.length > 2) {
      for (var i = 1; i < _measurePoints.length; i++) {
        var segDist = _measurePoints[i - 1].distanceTo(_measurePoints[i]);
        if (segDist < 1) continue;
        var segMid = L.latLng(
          (_measurePoints[i - 1].lat + _measurePoints[i].lat) / 2,
          (_measurePoints[i - 1].lng + _measurePoints[i].lng) / 2
        );
        addMeasureSegLabel(segMid, formatDist(segDist));
      }
    }

    // Total label at the last point
    addMeasureLabel(_measurePoints[_measurePoints.length - 1], formatDist(totalDist));

    showMeasureTooltip("Total: " + formatDist(totalDist));
    unbindMeasureEvents();
  }

  function finalizeMeasureArea() {
    if (_measureGuide) { _measureLayer.removeLayer(_measureGuide); _measureGuide = null; }
    _measureFinalized = true;

    var polygon = L.polygon(_measurePoints, {
      color: "#6366f1", weight: 3, opacity: 0.9,
      fillColor: "#6366f1", fillOpacity: 0.15, interactive: false
    });
    _measureLayer.addLayer(polygon);
    _measureFinalShape = polygon;

    var area = computeArea(_measurePoints);
    var perimeter = computeTotalDistance(_measurePoints.concat([_measurePoints[0]]));
    var center = polygon.getBounds().getCenter();

    addMeasureLabel(center, formatArea(area) + "\nPerimetre: " + formatDist(perimeter));
    showMeasureTooltip("Aire: " + formatArea(area) + " | Perimetre: " + formatDist(perimeter));
    unbindMeasureEvents();
  }

  function finalizeMeasureCircle(edgePoint) {
    if (_measureGuide) { _measureLayer.removeLayer(_measureGuide); _measureGuide = null; }
    _measureFinalized = true;

    var center = _measurePoints[0];
    var radius = center.distanceTo(edgePoint);

    var circle = L.circle(center, {
      radius: radius, color: "#6366f1", weight: 3, opacity: 0.9,
      fillColor: "#6366f1", fillOpacity: 0.1, interactive: false
    });
    _measureLayer.addLayer(circle);
    _measureFinalShape = circle;

    _measureLayer.addLayer(L.polyline([center, edgePoint], {
      color: "#6366f1", weight: 2, opacity: 0.6, dashArray: "4 4", interactive: false
    }));
    addMeasureVertex(edgePoint);

    var area = Math.PI * radius * radius;
    addMeasureLabel(center, "R: " + formatDist(radius) + "\nD: " + formatDist(radius * 2) + "\nAire: " + formatArea(area));
    showMeasureTooltip("Rayon: " + formatDist(radius) + " | Diametre: " + formatDist(radius * 2) + " | Aire: " + formatArea(area));
    unbindMeasureEvents();
  }

  // --- Edit mode: drag vertices, add midpoints, resize circle ---

  var _measureEditing = false;
  var _measureMidMarkers = [];
  var _measureRadiusLine = null;
  var _measureEdgeMarker = null;

  function enterEditMode() {
    if (!_measureFinalized || !_measureLayer || !_measureFinalShape) return;

    var editBtn = document.getElementById("measure-edit");
    if (editBtn) editBtn.classList.add("active");
    _measureEditing = true;

    // Remove old labels and vertex markers
    _measureLabels.forEach(function (m) { _measureLayer.removeLayer(m); });
    _measureLabels = [];
    _measureVertexMarkers.forEach(function (m) { _measureLayer.removeLayer(m); });
    _measureVertexMarkers = [];
    _measureMidMarkers = [];

    if (_measureFinalShape instanceof L.Circle) {
      enterEditCircle();
    } else {
      enterEditPoly();
    }
  }

  // --- Edit polygon/polyline ---

  function enterEditPoly() {
    buildEditVertices();
    map.getContainer().style.cursor = "grab";
    showMeasureTooltip("Glissez les sommets ou cliquez les points intermediaires pour ajouter");
  }

  function buildEditVertices() {
    // Remove old
    _measureVertexMarkers.forEach(function (m) { _measureLayer.removeLayer(m); });
    _measureVertexMarkers = [];
    _measureMidMarkers.forEach(function (m) { _measureLayer.removeLayer(m); });
    _measureMidMarkers = [];

    // Main vertices (draggable)
    _measurePoints.forEach(function (pt, idx) {
      var marker = L.circleMarker(pt, {
        radius: 7, color: "#6366f1", fillColor: "#fff",
        fillOpacity: 1, weight: 2.5, interactive: true, bubblingMouseEvents: false
      });
      marker.idx = idx;
      marker.on("mousedown", startVertexDrag);
      _measureLayer.addLayer(marker);
      _measureVertexMarkers.push(marker);
    });

    // Midpoint markers (smaller, for adding new vertices)
    var len = _measurePoints.length;
    var isPolygon = _measureFinalShape instanceof L.Polygon;
    var segments = isPolygon ? len : len - 1;
    for (var i = 0; i < segments; i++) {
      var a = _measurePoints[i];
      var b = _measurePoints[(i + 1) % len];
      var mid = L.latLng((a.lat + b.lat) / 2, (a.lng + b.lng) / 2);
      var midMarker = L.circleMarker(mid, {
        radius: 5, color: "#6366f1", fillColor: "#c7d2fe",
        fillOpacity: 0.8, weight: 1.5, interactive: true, bubblingMouseEvents: false
      });
      midMarker.insertAfter = i; // insert new point after index i
      midMarker.on("click", onMidpointClick);
      _measureLayer.addLayer(midMarker);
      _measureMidMarkers.push(midMarker);
    }
  }

  function onMidpointClick(e) {
    L.DomEvent.stop(e);
    var insertIdx = e.target.insertAfter + 1;
    var newPt = e.target.getLatLng();
    _measurePoints.splice(insertIdx, 0, newPt);
    // Update shape
    if (_measureFinalShape instanceof L.Polygon) {
      _measureFinalShape.setLatLngs(_measurePoints);
    } else {
      _measureFinalShape.setLatLngs(_measurePoints);
    }
    // Rebuild all edit vertices
    buildEditVertices();
    updateMeasureAfterEdit();
  }

  function startVertexDrag(e) {
    var marker = e.target;
    map.dragging.disable();
    map.getContainer().style.cursor = "grabbing";

    function onDrag(ev) {
      marker.setLatLng(ev.latlng);
      _measurePoints[marker.idx] = ev.latlng;
      if (_measureFinalShape) {
        _measureFinalShape.setLatLngs(_measurePoints);
      }
    }

    function onDragEnd() {
      map.off("mousemove", onDrag);
      map.off("mouseup", onDragEnd);
      map.dragging.enable();
      map.getContainer().style.cursor = "grab";
      buildEditVertices();
      updateMeasureAfterEdit();
    }

    map.on("mousemove", onDrag);
    map.on("mouseup", onDragEnd);
  }

  // --- Edit circle ---

  function enterEditCircle() {
    var center = _measureFinalShape.getLatLng();
    var radius = _measureFinalShape.getRadius();
    // Compute edge point (east of center)
    var edgePt = turf.destination(turf.point([center.lng, center.lat]), radius / 1000, 90, { units: "kilometers" });
    var edgeLatLng = L.latLng(edgePt.geometry.coordinates[1], edgePt.geometry.coordinates[0]);

    // Center marker (draggable)
    var centerMarker = L.circleMarker(center, {
      radius: 7, color: "#6366f1", fillColor: "#fff",
      fillOpacity: 1, weight: 2.5, interactive: true, bubblingMouseEvents: false
    });
    centerMarker.on("mousedown", function () { startCircleDrag("center", centerMarker); });
    _measureLayer.addLayer(centerMarker);
    _measureVertexMarkers.push(centerMarker);

    // Edge marker (draggable to resize)
    _measureEdgeMarker = L.circleMarker(edgeLatLng, {
      radius: 7, color: "#6366f1", fillColor: "#c7d2fe",
      fillOpacity: 1, weight: 2.5, interactive: true, bubblingMouseEvents: false
    });
    _measureEdgeMarker.on("mousedown", function () { startCircleDrag("edge", _measureEdgeMarker); });
    _measureLayer.addLayer(_measureEdgeMarker);
    _measureVertexMarkers.push(_measureEdgeMarker);

    // Radius line
    _measureRadiusLine = L.polyline([center, edgeLatLng], {
      color: "#6366f1", weight: 2, opacity: 0.6, dashArray: "4 4", interactive: false
    });
    _measureLayer.addLayer(_measureRadiusLine);

    map.getContainer().style.cursor = "grab";
    showMeasureTooltip("Glissez le centre ou le bord pour modifier");
  }

  function startCircleDrag(type, marker) {
    map.dragging.disable();
    map.getContainer().style.cursor = "grabbing";

    function onDrag(ev) {
      marker.setLatLng(ev.latlng);
      if (type === "center") {
        _measureFinalShape.setLatLng(ev.latlng);
        // Move edge marker to keep same radius
        var r = _measureFinalShape.getRadius();
        var ep = turf.destination(turf.point([ev.latlng.lng, ev.latlng.lat]), r / 1000, 90, { units: "kilometers" });
        var newEdge = L.latLng(ep.geometry.coordinates[1], ep.geometry.coordinates[0]);
        if (_measureEdgeMarker) _measureEdgeMarker.setLatLng(newEdge);
        if (_measureRadiusLine) _measureRadiusLine.setLatLngs([ev.latlng, newEdge]);
      } else {
        // Resize circle
        var center = _measureFinalShape.getLatLng();
        var newRadius = center.distanceTo(ev.latlng);
        _measureFinalShape.setRadius(newRadius);
        if (_measureRadiusLine) _measureRadiusLine.setLatLngs([center, ev.latlng]);
      }
    }

    function onDragEnd() {
      map.off("mousemove", onDrag);
      map.off("mouseup", onDragEnd);
      map.dragging.enable();
      map.getContainer().style.cursor = "grab";
      updateCircleMeasureLabel();
    }

    map.on("mousemove", onDrag);
    map.on("mouseup", onDragEnd);
  }

  function updateCircleMeasureLabel() {
    _measureLabels.forEach(function (m) { _measureLayer.removeLayer(m); });
    _measureLabels = [];
    var center = _measureFinalShape.getLatLng();
    var radius = _measureFinalShape.getRadius();
    var area = Math.PI * radius * radius;
    addMeasureLabel(center, "R: " + formatDist(radius) + "\nD: " + formatDist(radius * 2) + "\nAire: " + formatArea(area));
    showMeasureTooltip("Rayon: " + formatDist(radius) + " | Diametre: " + formatDist(radius * 2) + " | Aire: " + formatArea(area));
  }

  // --- Update labels after edit ---

  function updateMeasureAfterEdit() {
    _measureLabels.forEach(function (m) { _measureLayer.removeLayer(m); });
    _measureLabels = [];

    if (_measureFinalShape instanceof L.Polygon) {
      var area = computeArea(_measurePoints);
      var perimeter = computeTotalDistance(_measurePoints.concat([_measurePoints[0]]));
      var center = _measureFinalShape.getBounds().getCenter();
      addMeasureLabel(center, formatArea(area) + "\nPerimetre: " + formatDist(perimeter));
      showMeasureTooltip("Aire: " + formatArea(area) + " | Perimetre: " + formatDist(perimeter));
    } else {
      var totalDist = computeTotalDistance(_measurePoints);
      addMeasureLabel(_measurePoints[_measurePoints.length - 1], formatDist(totalDist));
      showMeasureTooltip("Total: " + formatDist(totalDist));
    }
  }

  function unbindMeasureEvents() {
    map.off("click", onMeasureClick);
    map.off("mousemove", onMeasureMouseMove);
    map.off("dblclick", onMeasureDblClick);
    map.doubleClickZoom.enable();
    map.getContainer().style.cursor = "";
  }

  // --- Helpers ---

  function addMeasureVertex(latlng) {
    var marker = L.circleMarker(latlng, {
      radius: 5, color: "#6366f1", fillColor: "#fff",
      fillOpacity: 1, weight: 2, interactive: false
    });
    _measureLayer.addLayer(marker);
    _measureVertexMarkers.push(marker);
  }

  function addMeasureLabel(latlng, text) {
    var html = '<div class="measure-label">' + escapeHtml(text).replace(/\n/g, "<br>") + '</div>';
    var icon = L.divIcon({ html: html, className: "measure-label-icon", iconSize: null });
    var marker = L.marker(latlng, { icon: icon, interactive: false });
    _measureLayer.addLayer(marker);
    _measureLabels.push(marker);
  }

  function addMeasureSegLabel(latlng, text) {
    var html = '<div class="measure-seg-label">' + text + '</div>';
    var icon = L.divIcon({ html: html, className: "measure-label-icon", iconSize: null });
    var marker = L.marker(latlng, { icon: icon, interactive: false });
    _measureLayer.addLayer(marker);
    _measureLabels.push(marker);
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
      return turf.area(turf.polygon([coords]));
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

  function showMeasureTooltip(text) {
    if (!_measureTooltip) {
      _measureTooltip = document.createElement("div");
      _measureTooltip.className = "measure-tooltip";
      var mapContainer = document.getElementById("cockpit-map");
      if (mapContainer) mapContainer.appendChild(_measureTooltip);
    }
    _measureTooltip.textContent = text;
    _measureTooltip.style.display = "";
  }

  function hideMeasureTooltip() {
    if (_measureTooltip) _measureTooltip.style.display = "none";
  }

  // Expose for external use
  window.CockpitMapView = {
    getMap: function () { return map; },
    currentView: function () { return currentView; },
    switchView: switchView,
    reload: loadEventMarkers,
    filter: applyFilters,
    resetFilters: resetFilters,
    drawTargetRoute: drawTargetRoute,
    clearTargetRoute: clearTargetRoute,
    drawAllRoutes: drawAllRoutes,
    clearAllRoutes: clearAllRoutes,
    showAlertPin: showAlertPin,
    clearAlertPin: clearAlertPin,
    showAllAlertPins: showAllAlertPins,
    clearAllAlertPins: clearAllAlertPins,
    preload: preloadMapData,
    getGridData: function () { return _gridData; },
    getGridMeta: function () { return _gridMeta; },
    getCellLabel: function (lat, lng) {
      var meta = _gridMeta;
      if (!meta) return null;
      var col = null, row = null;
      for (var ci = 0; ci < meta.numCols; ci++) {
        if (lng >= meta.vLines[ci].lng && lng < meta.vLines[ci + 1].lng) { col = ci; break; }
      }
      for (var ri = 0; ri < meta.numRows; ri++) {
        if (lat <= meta.hLines[ri].lat && lat > meta.hLines[ri + 1].lat) { row = ri; break; }
      }
      if (col === null || row === null) return null;
      var colLbl = meta.cols[col];
      var rowLbl = meta.rows[row];
      if (!colLbl || !rowLbl) return null;
      return colLbl + rowLbl;
    },
    colLabel: colLabel
  };

  // ==========================================================================
  // 3P LIGHTBOX (photo viewer)
  // ==========================================================================

  (function initLightbox() {
    // Create overlay structure via DOM methods (no innerHTML)
    var overlay = document.createElement("div");
    overlay.id = "lightbox-3p";
    overlay.className = "lightbox-3p";

    var backdrop = document.createElement("div");
    backdrop.className = "lightbox-3p-backdrop";
    overlay.appendChild(backdrop);

    var content = document.createElement("div");
    content.className = "lightbox-3p-content";

    var header = document.createElement("div");
    header.className = "lightbox-3p-header";
    var title = document.createElement("span");
    title.className = "lightbox-3p-title";
    header.appendChild(title);
    var closeBtn = document.createElement("button");
    closeBtn.className = "lightbox-3p-close";
    var closeIco = document.createElement("span");
    closeIco.className = "material-symbols-outlined";
    closeIco.textContent = "close";
    closeBtn.appendChild(closeIco);
    header.appendChild(closeBtn);
    content.appendChild(header);

    var body = document.createElement("div");
    body.className = "lightbox-3p-body";
    var spinner = document.createElement("div");
    spinner.className = "lightbox-3p-spinner";
    var spinIco = document.createElement("span");
    spinIco.className = "material-symbols-outlined spin";
    spinIco.textContent = "progress_activity";
    spinner.appendChild(spinIco);
    body.appendChild(spinner);
    var img = document.createElement("img");
    img.className = "lightbox-3p-img";
    body.appendChild(img);
    content.appendChild(body);

    overlay.appendChild(content);
    document.body.appendChild(overlay);

    function close() { overlay.classList.remove("open"); }
    backdrop.addEventListener("click", close);
    closeBtn.addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && overlay.classList.contains("open")) close();
    });

    window._open3pLightbox = function (url, nom) {
      title.textContent = nom || "";
      img.style.display = "none";
      spinner.style.display = "";
      overlay.classList.add("open");

      img.onload = function () {
        spinner.style.display = "none";
        img.style.display = "";
      };
      img.onerror = function () {
        spinner.style.display = "none";
        img.style.display = "none";
        title.textContent = (nom || "") + " — Photo indisponible";
      };
      img.src = url;
    };
  })();

})();
