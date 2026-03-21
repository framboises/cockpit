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
      maxZoom: 22
    }).addTo(map);

    tileLayerSatEGIS = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      attribution: "&copy; Esri",
      maxZoom: 22
    });

    tileLayerSatACO = L.tileLayer("/tiles/{z}/{x}/{y}.png", {
      tms: true,
      maxZoom: 22,
      attribution: "ACO"
    });

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

    mapReady = true;
  }

  // ==========================================================================
  // VIEW TOGGLE
  // ==========================================================================

  function switchView(view) {
    if (view === currentView) return;
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

  function loadEventMarkers() {
    if (!map) return;

    const ev = window.selectedEvent;
    const yr = window.selectedYear;
    if (!ev || !yr) return;

    // Clear existing
    clearAllLayers();

    // Fetch parametrages + gm categories in parallel
    Promise.all([
      fetch("/get_parametrage?event=" + encodeURIComponent(ev) + "&year=" + encodeURIComponent(yr))
        .then(function (r) { return r.json(); }),
      fetch("/get_gm_categories")
        .then(function (r) { return r.json(); })
        .catch(function () { return []; })
    ]).then(function (results) {
      // cockpit get_parametrage returns data directly (not wrapped in {data:...})
      var paramData = results[0];
      gmCategories = results[1] || [];

      if (!paramData || typeof paramData !== "object") return;

      // Render each category
      gmCategories.forEach(function (cat) {
        renderCategoryLayer(cat, paramData);
      });
    }).catch(function (err) {
      console.error("[MapView] Erreur chargement:", err);
    });
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

    // Fetch GeoJSON features
    fetch("/gm_collection_data/" + encodeURIComponent(collection))
      .then(function (r) { return r.json(); })
      .then(function (geojson) {
        var features = geojson.features || geojson || [];

        var layerGroup = L.layerGroup().addTo(map);
        categoryLayers[catId] = { group: layerGroup, label: label, icon: icon, color: defaultColor };

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
          if (sc.hasRouteColor && item.routeColor) {
            colorPromise = resolveRouteColor(item.routeColor);
          } else {
            colorPromise = Promise.resolve(defaultColor);
          }

          colorPromise.then(function (color) {
            if (geomType === "Point" || geomType === "MultiPoint") {
              renderPointMarker(feature, item, catConfig, displayName, icon, color, layerGroup);
            } else if (geomType === "Polygon" || geomType === "MultiPolygon") {
              renderPolygonLayer(feature, item, catConfig, displayName, icon, color, layerGroup);
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

  function renderPointMarker(feature, item, catConfig, displayName, icon, color, layerGroup) {
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
      icon: icon
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

  function renderPolygonLayer(feature, item, catConfig, displayName, icon, color, layerGroup) {
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

    var cockpitData = { name: displayName, category: catConfig.label, catId: catConfig._id, icon: icon };
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
    });

    // Actions: tout / rien
    var actions = document.createElement("div");
    actions.className = "map-cat-actions";

    var btnAll = document.createElement("button");
    btnAll.className = "map-cat-all";
    btnAll.textContent = "Tout";
    btnAll.addEventListener("click", function () {
      Object.keys(enabledCategories).forEach(function (k) { enabledCategories[k] = true; });
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
  }

  // ==========================================================================
  // UTILS
  // ==========================================================================

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

    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [60, 60], maxZoom: 18 });
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

  // Rebuild dropdown after markers are loaded
  var _origLoadEventMarkers = loadEventMarkers;
  loadEventMarkers = function () {
    _origLoadEventMarkers();
    // Delay to let fetches complete
    setTimeout(buildCategoryDropdown, 1500);
  };

  document.addEventListener("DOMContentLoaded", function () {
    // View toggle buttons
    var btnTimeline = document.getElementById("view-timeline-btn");
    var btnMap = document.getElementById("view-map-btn");

    if (btnTimeline) btnTimeline.addEventListener("click", function () { switchView("timeline"); });
    if (btnMap) btnMap.addEventListener("click", function () { switchView("map"); });

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
        if (e.key === "Enter") {
          e.preventDefault();
          var q = this.value.trim();
          if (q) {
            fitBoundsOnItem(q);
            hideMapAutocomplete();
          }
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

  // Expose for external use
  window.CockpitMapView = {
    switchView: switchView,
    reload: loadEventMarkers,
    filter: applyFilters,
    resetFilters: resetFilters
  };

})();
