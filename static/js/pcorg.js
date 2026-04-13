(function () {
  "use strict";

  // ── Constants ──────────────────────────────────────────────────────────────
  var REFRESH_MS = 60000;
  var CATEGORY_STYLES = {
    "PCO.Secours":       { color: "#dc2626", icon: "local_hospital" },
    "PCO.Securite":      { color: "#ef4444", icon: "shield" },
    "PCO.Technique":     { color: "#f59e0b", icon: "build" },
    "PCO.Flux":          { color: "#0d9488", icon: "swap_calls" },
    "PCO.Fourriere":     { color: "#6b7280", icon: "directions_car" },
    "PCO.Information":   { color: "#2563eb", icon: "info" },
    "PCO.MainCourante":  { color: "#8b5cf6", icon: "edit_note" }
  };
  var FALLBACK_STYLE = { color: "#94a3b8", icon: "description" };

  // ── Urgency levels ────────────────────────────────────────────────────────
  var URGENCY_LEVELS = ["EU", "UA", "UR", "IMP"];
  var URGENCY_COLORS = {
    EU: "#dc2626", UA: "#f97316", UR: "#eab308", IMP: "#6b7280"
  };
  var URGENCY_LABELS = {
    SECOURS:  { EU: "D\u00e9tresse vitale", UA: "Urgence absolue", UR: "Urgence relative", IMP: "Impliqu\u00e9 m\u00e9dical" },
    SECURITE: { EU: "Danger imm\u00e9diat", UA: "Incident grave", UR: "Incident en cours", IMP: "T\u00e9moin / impliqu\u00e9" },
    MIXTE:    { EU: "Urgence extr\u00eame", UA: "Urgence prioritaire", UR: "Situation stable", IMP: "Impliqu\u00e9" }
  };

  function urgencyType(cat) {
    if (cat === "PCO.Secours") return "SECOURS";
    if (cat === "PCO.Securite") return "SECURITE";
    return "MIXTE";
  }

  function urgencyLabel(cat, level) {
    return (URGENCY_LABELS[urgencyType(cat)] || URGENCY_LABELS.MIXTE)[level] || level;
  }

  // ── State ──────────────────────────────────────────────────────────────────
  var refreshTimer = null;
  var lastData = null;
  var expandedId = null;
  var pcorgMapLayer = null;
  var pcorgMarkers = {}; // {id: L.marker} pour ouvrir les popups programmatiquement
  var pickCallback = null;
  // Bounce acknowledgement (localStorage per user)
  var ACK_STORAGE_KEY = "pcorg-pin-ack";
  function _loadAck() {
    try { return JSON.parse(localStorage.getItem(ACK_STORAGE_KEY)) || {}; } catch (e) { return {}; }
  }
  function _saveAck(ack) {
    try { localStorage.setItem(ACK_STORAGE_KEY, JSON.stringify(ack)); } catch (e) {}
  }
  function ackPin(id, rev) {
    var ack = _loadAck();
    ack[id] = rev;
    _saveAck(ack);
  }
  function ackAllPins(items) {
    var ack = _loadAck();
    items.forEach(function (item) { ack[item.id] = item.bounce_rev || 0; });
    _saveAck(ack);
  }
  function shouldBounce(item) {
    var rev = item.bounce_rev || 0;
    if (rev === 0) return false;
    var ack = _loadAck();
    var seen = ack[item.id];
    return seen === undefined || seen < rev;
  }

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var listOpen, listClosed, statsContainer, badge, placeholderOpen, placeholderClosed;

  // ── Device status resolution (anoloc cross-reference) ────────────────────
  var DEVICE_STATUS_META = {
    patrouille:        { label: "Disponible",         color: "#22c55e" },
    intervention:      { label: "Intervention",       color: "#f59e0b" },
    sur_place:         { label: "ASL",                color: "#3b82f6" },
    pause:             { label: "Pause",              color: "#94a3b8" },
    fin_intervention:  { label: "Fin d'inter",        color: "#8b5cf6" },
    running:           { label: "En mouvement",       color: "#22c55e" },
    stopped:           { label: "A l'arret",          color: "#f59e0b" },
    waiting:           { label: "En attente",         color: "#eab308" },
    offline:           { label: "Hors ligne",         color: "#ef4444" },
  };
  function _resolveDeviceStatus(dev) {
    if (!dev) return null;
    // Tablets: use patrol_status first
    if (dev.kind === "tablet" && dev.patrol_status) {
      return DEVICE_STATUS_META[dev.patrol_status] || DEVICE_STATUS_META.patrouille;
    }
    // Beacons / other
    if (!dev.online) return DEVICE_STATUS_META.offline;
    return DEVICE_STATUS_META[dev.status] || DEVICE_STATUS_META.running;
  }

  // ── Helpers ────────────────────────────────────────────────────────────────
  function catStyle(cat) {
    return CATEGORY_STYLES[cat] || FALLBACK_STYLE;
  }

  function timeAgo(isoStr) {
    if (!isoStr) return "";
    var diff = Date.now() - new Date(isoStr).getTime();
    var mins = Math.floor(diff / 60000);
    if (mins < 1) return "a l'instant";
    if (mins < 60) return mins + " min";
    var hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + "h" + (mins % 60 ? String(mins % 60).padStart(2, "0") : "");
    var days = Math.floor(hrs / 24);
    return days + "j";
  }

  function shortTime(isoStr) {
    if (!isoStr) return "";
    var d = new Date(isoStr);
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  function shortCat(cat) {
    return (cat || "").replace("PCO.", "");
  }

  function truncZone(desc) {
    if (!desc) return "";
    return desc.replace(/_MC PCO\/?/g, "").replace(/^\//, "");
  }

  var _STADE_TO_URGENCY = { "1": "IMP", "2": "UR", "3": "UA", "4": "EU" };

  function formatGroupDesc(groupDesc, category) {
    if (!groupDesc) return "";
    // Remplacer chaque "PCO/Stade N" ou "PCS/Stade N" par le label d'urgence
    return groupDesc.replace(/(?:PCO|PCS)\/Stade\s*(\d)/g, function (match, num) {
      var code = _STADE_TO_URGENCY[num];
      if (code) return urgencyLabel(category, code);
      return match;
    });
  }

  function mkEl(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  function matIcon(name, extraCls) {
    var s = document.createElement("span");
    s.className = "material-symbols-outlined" + (extraCls ? " " + extraCls : "");
    s.textContent = name;
    return s;
  }

  function txt(parent, text) {
    parent.textContent = text;
    return parent;
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    if (window.isBlockAllowed && !window.isBlockAllowed("widget-comms")) return;

    listOpen = document.getElementById("pcorg-list-open");
    listClosed = document.getElementById("pcorg-list-closed");
    statsContainer = document.getElementById("pcorg-stats");
    badge = document.getElementById("pcorg-badge");
    placeholderOpen = document.getElementById("pcorg-placeholder-open");
    placeholderClosed = document.getElementById("pcorg-placeholder-closed");

    initTabs();
    initCreateModal();
    initGpsModal();

    window.pcorgRefresh = refresh;
    loadPcorgConfig();
    loadVehiclesByCategory();

    setTimeout(refresh, 800);
    refreshTimer = setInterval(refresh, REFRESH_MS);

    // Retry pending map pins once map is ready
    var pinRetry = setInterval(function () {
      if (!pendingPins) { clearInterval(pinRetry); return; }
      if (getMap()) { updateMapPins(pendingPins); clearInterval(pinRetry); }
    }, 2000);

    // Context menu on map
    buildContextMenu();
    var ctxRetry = setInterval(function () {
      var m = getMap();
      if (!m) return;
      clearInterval(ctxRetry);
      m.on("contextmenu", onMapContextMenu);

      // Long-press tactile (600ms) pour ecrans tactiles (Huawei IdeaHub, etc.)
      var lpTimer = null, lpStart = null;
      var mapContainer = m.getContainer();
      mapContainer.addEventListener("touchstart", function (e) {
        if (e.touches.length !== 1) { clearTimeout(lpTimer); return; }
        lpStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
        lpTimer = setTimeout(function () {
          // Convertir le point touch en latlng Leaflet
          var touch = lpStart;
          var rect = mapContainer.getBoundingClientRect();
          var pt = L.point(touch.x - rect.left, touch.y - rect.top);
          var latlng = m.containerPointToLatLng(pt);
          onMapContextMenu({
            latlng: latlng,
            originalEvent: { preventDefault: function () {} },
            _touch: true
          });
          L.DomEvent.preventDefault(e);
        }, 600);
      }, { passive: false });
      mapContainer.addEventListener("touchmove", function (e) {
        if (!lpStart || !lpTimer) return;
        var dx = e.touches[0].clientX - lpStart.x;
        var dy = e.touches[0].clientY - lpStart.y;
        if (dx * dx + dy * dy > 100) { clearTimeout(lpTimer); lpTimer = null; }
      });
      mapContainer.addEventListener("touchend", function () {
        clearTimeout(lpTimer); lpTimer = null;
      });
      mapContainer.addEventListener("touchcancel", function () {
        clearTimeout(lpTimer); lpTimer = null;
      });
    }, 2000);
  }

  // ── Context menu ──────────────────────────────────────────────────────────
  var ctxMenu = null;
  var ctxLat = null, ctxLon = null;
  var _ctxIsTouch = false;

  var CTX_DESCRIPTIONS = {
    "PCO.Secours":       "Victime, malaise, blessure",
    "PCO.Securite":      "Incident, intrusion, vol",
    "PCO.Technique":     "Panne, infrastructure, materiel",
    "PCO.Flux":          "Circulation, acces, jauge",
    "PCO.Fourriere":     "Vehicule, stationnement",
    "PCO.Information":   "Signalement, observation",
    "PCO.MainCourante":  "Note, consigne, suivi"
  };

  function buildContextMenu() {
    ctxMenu = mkEl("div", "pcorg-ctx-menu");
    ctxMenu.id = "pcorg-ctx-menu";

    // Header with coordinates
    var header = mkEl("div", "pcorg-ctx-header");
    var headerLeft = mkEl("div", "pcorg-ctx-header-left");
    var headerIco = matIcon("add_location_alt", "pcorg-ctx-header-icon");
    headerLeft.appendChild(headerIco);
    var headerTxt = mkEl("div", "pcorg-ctx-header-text");
    var headerTitle = mkEl("div", "pcorg-ctx-title");
    headerTitle.textContent = "Nouvelle intervention";
    headerTxt.appendChild(headerTitle);
    var headerCoords = mkEl("div", "pcorg-ctx-coords");
    headerCoords.id = "pcorg-ctx-coords";
    headerTxt.appendChild(headerCoords);
    headerLeft.appendChild(headerTxt);
    header.appendChild(headerLeft);
    ctxMenu.appendChild(header);

    // Category items
    var list = mkEl("div", "pcorg-ctx-list");
    CATEGORY_ORDER.forEach(function (cat, idx) {
      var st = catStyle(cat);
      var item = mkEl("div", "pcorg-ctx-item");
      item.setAttribute("data-cat", cat);
      item.style.setProperty("--cat-color", st.color);
      item.style.animationDelay = (idx * 30) + "ms";

      var iconWrap = mkEl("div", "pcorg-ctx-icon-wrap");
      iconWrap.style.background = st.color + "18";
      iconWrap.style.color = st.color;
      var ico = matIcon(st.icon);
      iconWrap.appendChild(ico);
      item.appendChild(iconWrap);

      var content = mkEl("div", "pcorg-ctx-content");
      var label = mkEl("div", "pcorg-ctx-label");
      label.textContent = shortCat(cat);
      content.appendChild(label);
      var desc = mkEl("div", "pcorg-ctx-desc");
      desc.textContent = CTX_DESCRIPTIONS[cat] || "";
      content.appendChild(desc);
      item.appendChild(content);

      var arrow = matIcon("chevron_right", "pcorg-ctx-arrow");
      item.appendChild(arrow);

      // Build urgency sub-menu (populated dynamically based on config)
      var submenu = mkEl("div", "pcorg-ctx-submenu");
      submenu.setAttribute("data-cat-sub", cat);
      var uType = urgencyType(cat);
      URGENCY_LEVELS.forEach(function (level) {
        var subItem = mkEl("div", "pcorg-ctx-sub-item");
        subItem.style.setProperty("--sub-color", URGENCY_COLORS[level]);
        var dot = mkEl("span", "pcorg-ctx-sub-dot");
        dot.style.background = URGENCY_COLORS[level];
        subItem.appendChild(dot);
        var subContent = mkEl("div", "pcorg-ctx-sub-content");
        var subLabel = mkEl("div", "pcorg-ctx-sub-label");
        subLabel.textContent = URGENCY_LABELS[uType][level];
        subContent.appendChild(subLabel);
        var subCode = mkEl("div", "pcorg-ctx-sub-code");
        subCode.textContent = level;
        subContent.appendChild(subCode);
        subItem.appendChild(subContent);
        function onSubItemAction(e) {
          e.stopPropagation();
          e.preventDefault();
          // Save menu position before hiding (for vehicle picker placement)
          var menuPos = ctxMenu ? ctxMenu.getBoundingClientRect() : null;
          hideContextMenu();
          var userCanFS = !!window.__userFicheSimplifiee;
          var catFS = (pcorgConfig && pcorgConfig.fiche_simplifiee) || {};
          var vehicles = vehiclesByCategory[cat];
          var isQuick = userCanFS && catFS[cat];
          if (vehicles && vehicles.length > 0) {
            showVehiclePicker(ctxLat, ctxLon, cat, level, vehicles, isQuick, menuPos);
          } else if (isQuick) {
            quickCreate(ctxLat, ctxLon, cat, level);
          } else {
            openCreateFromContext(ctxLat, ctxLon, cat, level);
          }
        }
        // Vehicle sub-sub-menu (built once, shown/hidden dynamically via CSS hover)
        var vehSubmenu = mkEl("div", "pcorg-ctx-veh-submenu");
        vehSubmenu.setAttribute("data-cat-veh", cat);
        vehSubmenu.setAttribute("data-level-veh", level);
        subItem.appendChild(vehSubmenu);

        subItem.addEventListener("touchend", function (e) {
          // Touch: show vehicle picker as separate overlay
          onSubItemAction(e);
        });
        subItem.addEventListener("click", function (e) {
          if (_ctxIsTouch) return;
          // If vehicle submenu is visible, don't fire (user clicks vehicle inside)
          if (e.target.closest(".pcorg-ctx-veh-submenu")) return;
          onSubItemAction(e);
        });
        submenu.appendChild(subItem);
      });
      item.appendChild(submenu);

      // Touch: tap toggles sub-menu, second tap opens wizard
      item.addEventListener("touchend", function (e) {
        if (e.target.closest(".pcorg-ctx-submenu")) return;
        e.preventDefault(); // empeche le click synthetise
        if (item.classList.contains("has-submenu")) {
          var wasOpen = submenu.classList.contains("touch-open");
          ctxMenu.querySelectorAll(".pcorg-ctx-submenu.touch-open").forEach(function (s) {
            s.classList.remove("touch-open");
          });
          if (!wasOpen) {
            submenu.classList.add("touch-open");
            return;
          }
        }
        hideContextMenu();
        openCreateFromContext(ctxLat, ctxLon, cat);
      });
      // Mouse: click opens wizard directly (hover handles sub-menu via CSS)
      item.addEventListener("click", function (e) {
        if (e.target.closest(".pcorg-ctx-submenu")) return;
        if (_ctxIsTouch) return; // deja gere par touchend
        hideContextMenu();
        openCreateFromContext(ctxLat, ctxLon, cat);
      });
      list.appendChild(item);
    });
    ctxMenu.appendChild(list);

    document.body.appendChild(ctxMenu);

    // Close on click/touch anywhere or Escape
    document.addEventListener("click", function () { hideContextMenu(); });
    document.addEventListener("touchstart", function (e) {
      if (ctxMenu.classList.contains("show") && !e.target.closest("#pcorg-ctx-menu")) {
        hideContextMenu();
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") hideContextMenu();
    });
    document.addEventListener("contextmenu", function (e) {
      if (ctxMenu.classList.contains("show") && !e.target.closest(".leaflet-container")) {
        hideContextMenu();
      }
    });
  }

  function onMapContextMenu(e) {
    if (e.originalEvent && e.originalEvent.preventDefault) e.originalEvent.preventDefault();
    _ctxIsTouch = !!(e._touch);
    ctxLat = e.latlng.lat;
    ctxLon = e.latlng.lng;

    // Resolve zone from POI polygons
    var ctxZone = "";
    if (window.CockpitMapView && window.CockpitMapView.findZoneAtPoint) {
      ctxZone = window.CockpitMapView.findZoneAtPoint(ctxLat, ctxLon) || "";
    }

    // Update coordinates display
    var coordsEl = document.getElementById("pcorg-ctx-coords");
    if (coordsEl) {
      coordsEl.textContent = (ctxZone ? ctxZone + " - " : "") + ctxLat.toFixed(5) + ", " + ctxLon.toFixed(5);
    }

    // Show urgency sub-menus only for categories enabled in config
    var urgCats = (pcorgConfig && pcorgConfig.urgence_categories) || {};
    ctxMenu.querySelectorAll(".pcorg-ctx-submenu").forEach(function (sub) {
      var cat = sub.getAttribute("data-cat-sub");
      if (urgCats[cat]) {
        sub.style.display = "";
        sub.parentElement.classList.add("has-submenu");
      } else {
        sub.style.display = "none";
        sub.parentElement.classList.remove("has-submenu");
      }
    });

    // Populate vehicle sub-sub-menus
    ctxMenu.querySelectorAll(".pcorg-ctx-veh-submenu").forEach(function (vSub) {
      vSub.textContent = "";
      var vCat = vSub.getAttribute("data-cat-veh");
      var vLevel = vSub.getAttribute("data-level-veh");
      var vehicles = vehiclesByCategory[vCat];
      if (!vehicles || !vehicles.length) {
        vSub.style.display = "none";
        vSub.parentElement.classList.remove("has-veh-submenu");
        return;
      }
      vSub.style.display = "";
      vSub.parentElement.classList.add("has-veh-submenu");
      var catSt = catStyle(vCat);
      vSub.style.setProperty("--cat-color", catSt.color);
      var userCanFS = !!window.__userFicheSimplifiee;
      var catFS = (pcorgConfig && pcorgConfig.fiche_simplifiee) || {};
      var isQuick = userCanFS && !!catFS[vCat];
      vehicles.forEach(function (v) {
        var vBtn = mkEl("div", "pcorg-ctx-veh-item");
        // Resolve device status via anoloc cross-reference
        var anoRef = typeof window.getAnolocDeviceByLabel === "function"
          ? window.getAnolocDeviceByLabel(v.label) : null;
        var dev = anoRef ? anoRef.device : null;
        var dsMeta = dev ? _resolveDeviceStatus(dev) : null;
        var isAvailable = !dsMeta || dsMeta === DEVICE_STATUS_META.patrouille
          || dsMeta === DEVICE_STATUS_META.running;
        // Build label with status indicator
        var nameSpan = mkEl("span", "pcorg-ctx-veh-name");
        nameSpan.textContent = v.label;
        vBtn.appendChild(nameSpan);
        if (dsMeta) {
          var stSpan = mkEl("span", "pcorg-ctx-veh-status");
          var dot = mkEl("span", "pcorg-ctx-veh-dot");
          dot.style.background = dsMeta.color;
          stSpan.appendChild(dot);
          stSpan.appendChild(document.createTextNode(dsMeta.label));
          vBtn.appendChild(stSpan);
        }
        if (!isAvailable) {
          vBtn.classList.add("pcorg-ctx-veh-disabled");
        }
        function onVehPick(ev) {
          ev.stopPropagation(); ev.preventDefault();
          if (!isAvailable) return;
          hideContextMenu();
          if (isQuick) {
            quickCreate(ctxLat, ctxLon, vCat, vLevel, v.label);
          } else {
            createPendingPatrouille = v.label;
            openCreateFromContext(ctxLat, ctxLon, vCat, vLevel);
          }
        }
        vBtn.addEventListener("click", function (ev) { if (!_ctxIsTouch) onVehPick(ev); });
        vBtn.addEventListener("touchend", onVehPick);
        vSub.appendChild(vBtn);
      });
      // "Sans vehicule" option
      var noneBtn = mkEl("div", "pcorg-ctx-veh-none");
      noneBtn.textContent = "Sans v\u00e9hicule";
      function onNone(ev) {
        ev.stopPropagation(); ev.preventDefault();
        hideContextMenu();
        if (isQuick) {
          quickCreate(ctxLat, ctxLon, vCat, vLevel);
        } else {
          openCreateFromContext(ctxLat, ctxLon, vCat, vLevel);
        }
      }
      noneBtn.addEventListener("click", function (ev) { if (!_ctxIsTouch) onNone(ev); });
      noneBtn.addEventListener("touchend", onNone);
      vSub.appendChild(noneBtn);

      // Reposition on hover: flip up if overflows bottom
      vSub.parentElement.addEventListener("mouseenter", function () {
        if (vSub.style.display === "none") return;
        // Reset position
        vSub.style.top = "-6px";
        vSub.style.bottom = "";
        requestAnimationFrame(function () {
          var rect = vSub.getBoundingClientRect();
          if (rect.bottom > window.innerHeight - 8) {
            vSub.style.top = "";
            vSub.style.bottom = "-6px";
          }
        });
      });
    });

    // Reset item animations
    var items = ctxMenu.querySelectorAll(".pcorg-ctx-item");
    items.forEach(function (it) { it.classList.remove("pcorg-ctx-animate"); });

    var map = getMap();
    if (!map) return;
    var pt = map.latLngToContainerPoint(e.latlng);
    var mapEl = map.getContainer();
    var rect = mapEl.getBoundingClientRect();

    ctxMenu.style.left = (rect.left + pt.x) + "px";
    ctxMenu.style.top = (rect.top + pt.y) + "px";
    ctxMenu.classList.add("show");

    // Trigger stagger animation
    requestAnimationFrame(function () {
      items.forEach(function (it) { it.classList.add("pcorg-ctx-animate"); });
    });

    // Adjust if overflows viewport
    requestAnimationFrame(function () {
      var menuRect = ctxMenu.getBoundingClientRect();
      if (menuRect.right > window.innerWidth) {
        ctxMenu.style.left = (rect.left + pt.x - menuRect.width) + "px";
      }
      if (menuRect.bottom > window.innerHeight) {
        ctxMenu.style.top = (rect.top + pt.y - menuRect.height) + "px";
      }
      // Flip sub-menus if main menu is near right edge
      ctxMenu.classList.toggle("flip-sub", menuRect.right + 240 > window.innerWidth);
    });
  }

  function hideContextMenu() {
    if (ctxMenu) {
      ctxMenu.classList.remove("show");
      ctxMenu.querySelectorAll(".touch-open").forEach(function (s) { s.classList.remove("touch-open"); });
    }
  }

  function openCreateFromContext(lat, lon, cat, urgency) {
    resetCreateWizard();
    if (urgency) createSelectedUrgency = urgency;
    showCreate();
    initCreateMap();

    // Wait for map init, then set position + category and stay on step 1
    // so the user can see/adjust the pin on the mini-map before proceeding
    setTimeout(function () {
      // Pre-select category (updates header color + pin icon)
      selectCategory(cat);
      // Place pin and center mini-map on the clicked location
      setCreatePosition(lat, lon);
      if (createMiniMap) {
        createMiniMap.setView([lat, lon], Math.max(createMiniMap.getZoom(), 16));
      }
      goToStep(1);
    }, 400);
  }

  // ── Vehicle picker (after urgency selection) ───────────────────────────────
  var vehiclePicker = null;

  function showVehiclePicker(lat, lon, cat, level, vehicles, isQuick, menuPos) {
    hideVehiclePicker();
    var st = catStyle(cat);
    vehiclePicker = mkEl("div", "pcorg-vehicle-picker");

    var header = mkEl("div", "pcorg-vp-header");
    header.style.borderColor = st.color;
    var ico = matIcon("directions_car");
    ico.style.color = st.color;
    header.appendChild(ico);
    var title = mkEl("span", "");
    title.textContent = "V\u00e9hicule engag\u00e9";
    header.appendChild(title);
    vehiclePicker.appendChild(header);

    var list = mkEl("div", "pcorg-vp-list");
    vehicles.forEach(function (v) {
      var btn = mkEl("button", "pcorg-vp-btn");
      btn.textContent = v.label;
      function onPick(e) {
        e.stopPropagation();
        e.preventDefault();
        hideVehiclePicker();
        if (isQuick) {
          quickCreate(lat, lon, cat, level, v.label);
        } else {
          createPendingPatrouille = v.label;
          openCreateFromContext(lat, lon, cat, level);
        }
      }
      btn.addEventListener("click", function (e) { if (!_ctxIsTouch) onPick(e); });
      btn.addEventListener("touchend", onPick);
      list.appendChild(btn);
    });
    vehiclePicker.appendChild(list);

    var noneBtn = mkEl("button", "pcorg-vp-none");
    noneBtn.textContent = "Sans vehicule";
    function onNone(e) {
      e.stopPropagation();
      e.preventDefault();
      hideVehiclePicker();
      if (isQuick) {
        quickCreate(lat, lon, cat, level);
      } else {
        openCreateFromContext(lat, lon, cat, level);
      }
    }
    noneBtn.addEventListener("click", function (e) { if (!_ctxIsTouch) onNone(e); });
    noneBtn.addEventListener("touchend", onNone);
    vehiclePicker.appendChild(noneBtn);

    document.body.appendChild(vehiclePicker);

    // Position near last context menu
    if (menuPos) {
      vehiclePicker.style.left = menuPos.left + "px";
      vehiclePicker.style.top = menuPos.top + "px";
    } else {
      // Fallback: center of screen
      vehiclePicker.style.left = "50%";
      vehiclePicker.style.top = "50%";
      vehiclePicker.style.transform = "translate(-50%, -50%)";
    }

    requestAnimationFrame(function () {
      var rect = vehiclePicker.getBoundingClientRect();
      if (rect.right > window.innerWidth) vehiclePicker.style.left = (window.innerWidth - rect.width - 12) + "px";
      if (rect.bottom > window.innerHeight) vehiclePicker.style.top = (window.innerHeight - rect.height - 12) + "px";
    });

    setTimeout(function () {
      document.addEventListener("click", _vpOutside);
      document.addEventListener("touchstart", _vpOutside);
    }, 50);
    document.addEventListener("keydown", _vpEscape);
  }

  function _vpOutside(e) {
    if (vehiclePicker && !e.target.closest(".pcorg-vehicle-picker")) hideVehiclePicker();
  }
  function _vpEscape(e) {
    if (e.key === "Escape") hideVehiclePicker();
  }

  function hideVehiclePicker() {
    if (vehiclePicker) { vehiclePicker.remove(); vehiclePicker = null; }
    document.removeEventListener("click", _vpOutside);
    document.removeEventListener("touchstart", _vpOutside);
    document.removeEventListener("keydown", _vpEscape);
  }

  var quickCreatePending = false;
  function quickCreate(lat, lon, cat, level, patrouille) {
    if (quickCreatePending) return;
    var ev = window.selectedEvent, yr = window.selectedYear;
    if (!ev || !yr) {
      if (typeof showToast === "function") showToast("warning", "Evenement/annee non selectionnes");
      return;
    }
    // Resolve carroyage from map grid data
    var carroye = "";
    if (window.CockpitMapView && window.CockpitMapView.getCellLabel) {
      carroye = window.CockpitMapView.getCellLabel(lat, lon) || "";
    }
    // Resolve zone from POI polygons
    var areaDesc = "";
    if (window.CockpitMapView && window.CockpitMapView.findZoneAtPoint) {
      areaDesc = window.CockpitMapView.findZoneAtPoint(lat, lon) || "";
    }
    quickCreatePending = true;
    var payload = {
      event: ev, year: yr,
      category: cat, niveau_urgence: level,
      lat: lat, lon: lon,
      carroye: carroye,
      area_desc: areaDesc
    };
    if (patrouille) payload.patrouille = patrouille;
    apiPost("/api/pcorg/quick-create", payload).then(function (r) {
      quickCreatePending = false;
      if (r.ok) {
        if (typeof showToast === "function") showToast("success", urgencyLabel(cat, level) + " - fiche creee");
        refresh();
      } else {
        if (typeof showToast === "function") showToast("error", r.error || "Erreur");
      }
    }).catch(function () {
      quickCreatePending = false;
      if (typeof showToast === "function") showToast("error", "Erreur reseau");
    });
  }

  // ── Tabs ───────────────────────────────────────────────────────────────────
  function initTabs() {
    var widget = document.getElementById("widget-comms");
    if (!widget) return;
    var tabs = widget.querySelectorAll(".widget-tab");
    var panes = widget.querySelectorAll(".widget-tab-content");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var target = tab.getAttribute("data-tab");
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        panes.forEach(function (p) {
          p.classList.toggle("active", p.getAttribute("data-tab") === target);
        });
      });
    });
  }

  // ── Refresh ────────────────────────────────────────────────────────────────
  function refresh() {
    var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};
    if (!ey.event || !ey.year) return;
    // Recharge la liste des vehicules engageables (inclut les tablettes Field) une fois sur 4
    if (!refresh._vbcCounter || refresh._vbcCounter % 4 === 0) {
      loadVehiclesByCategory();
    }
    refresh._vbcCounter = (refresh._vbcCounter || 0) + 1;
    fetch("/api/pcorg/live?event=" + encodeURIComponent(ey.event) + "&year=" + encodeURIComponent(ey.year))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        lastData = data;
        // Filtrer par categories autorisees
        var ac = window.__userAllowedCategories;
        var filterCat = ac ? function (it) { return ac.indexOf(it.category) !== -1; } : function () { return true; };
        var openFiltered = (data.open || []).filter(filterCat);
        var closedFiltered = (data.closed || []).filter(filterCat);
        renderList(listOpen, openFiltered, false, placeholderOpen);
        renderList(listClosed, closedFiltered, true, placeholderClosed);
        renderStats(openFiltered, closedFiltered);
        syncTabHeights();
        updateBadge(openFiltered.length);
        updateMapPins(openFiltered);
        if (expPanel && expPanel.style.display !== "none") renderExpanded();
      })
      .catch(function (err) { console.error("[pcorg] refresh error", err); });
  }

  // ── Render list ────────────────────────────────────────────────────────────
  function renderList(container, items, isClosed, placeholder) {
    if (!container) return;
    var toRemove = container.querySelectorAll(".pcorg-row, .pcorg-detail");
    toRemove.forEach(function (node) { node.remove(); });

    if (placeholder) {
      placeholder.style.display = items.length ? "none" : "";
    }

    items.forEach(function (item) {
      var st = catStyle(item.category);

      // Row
      var row = mkEl("div", "pcorg-row" + (isClosed ? " closed" : ""));
      row.setAttribute("data-id", item.id);

      var bar = mkEl("div", "pcorg-row-bar");
      bar.style.backgroundColor = st.color;
      row.appendChild(bar);

      var ico = matIcon(st.icon, "pcorg-row-icon");
      ico.style.color = st.color;
      row.appendChild(ico);

      var content = mkEl("div", "pcorg-row-content");
      var title = mkEl("div", "pcorg-row-title");
      title.textContent = item.text || "(sans description)";
      content.appendChild(title);

      var meta = mkEl("div", "pcorg-row-meta");
      var catSpan = mkEl("span", "");
      var catB = document.createElement("b");
      catB.textContent = shortCat(item.category);
      catSpan.appendChild(catB);
      meta.appendChild(catSpan);
      if (item.sous_classification) {
        var scSpan = mkEl("span", "");
        scSpan.textContent = item.sous_classification;
        meta.appendChild(scSpan);
      }
      if (item.niveau_urgence) {
        var urgBadge = mkEl("span", "pcorg-urgency-badge pcorg-urgency-" + item.niveau_urgence);
        urgBadge.textContent = urgencyLabel(item.category, item.niveau_urgence);
        meta.appendChild(urgBadge);
      }
      var zone = truncZone(item.area_desc);
      if (zone) {
        var zSpan = mkEl("span", "");
        zSpan.textContent = zone;
        meta.appendChild(zSpan);
      }
      content.appendChild(meta);
      row.appendChild(content);

      var right = mkEl("div", "pcorg-row-right");
      var timeEl = mkEl("span", "pcorg-row-time");
      timeEl.textContent = isClosed ? shortTime(item.close_ts) : timeAgo(item.ts);
      right.appendChild(timeEl);

      var gpsIcon = matIcon(item.lat != null ? "location_on" : "location_off",
        "pcorg-row-gps " + (item.lat != null ? "has-gps" : "no-gps"));
      gpsIcon.style.fontSize = "14px";
      if (item.lat == null) {
        gpsIcon.title = "Ajouter une position";
        gpsIcon.addEventListener("click", (function (id) {
          return function (e) { e.stopPropagation(); openGpsModal(id); };
        })(item.id));
      } else {
        gpsIcon.title = "Voir sur la carte";
        gpsIcon.addEventListener("click", (function (lat, lon) {
          return function (e) { e.stopPropagation(); flyToPin(lat, lon); };
        })(item.lat, item.lon));
      }
      right.appendChild(gpsIcon);
      row.appendChild(right);

      row.addEventListener("click", (function (id, closed) {
        return function () { openDetailModal(id, closed); };
      })(item.id, isClosed));

      container.appendChild(row);
    });
  }

  // ── Sync tab heights to stats panel ─────────────────────────────────────────
  function syncTabHeights() {
    if (!statsContainer) return;
    // Measure stats height (it's the reference)
    var h = statsContainer.scrollHeight;
    if (h > 0) {
      var px = h + "px";
      if (listOpen) listOpen.style.height = px;
      if (listClosed) listClosed.style.height = px;
      statsContainer.style.height = px;
    }
  }

  // ── Stats dashboard ─────────────────────────────────────────────────────────
  var ALL_CATEGORIES = [
    "PCO.Secours", "PCO.Securite", "PCO.Technique",
    "PCO.Flux", "PCO.Information", "PCO.MainCourante", "PCO.Fourriere"
  ];

  function getAllowedCategories() {
    var ac = window.__userAllowedCategories;
    if (!ac) return ALL_CATEGORIES; // null = pas de restriction
    return ALL_CATEGORIES.filter(function (c) { return ac.indexOf(c) !== -1; });
  }

  var CATEGORY_ORDER = getAllowedCategories();

  function renderStats(openItems, closedItems) {
    if (!statsContainer) return;
    statsContainer.textContent = "";

    // Count per category
    var counts = {};
    CATEGORY_ORDER.forEach(function (cat) { counts[cat] = { open: 0, closed: 0 }; });

    openItems.forEach(function (item) {
      var cat = item.category || "";
      if (!counts[cat]) counts[cat] = { open: 0, closed: 0 };
      counts[cat].open++;
    });
    closedItems.forEach(function (item) {
      var cat = item.category || "";
      if (!counts[cat]) counts[cat] = { open: 0, closed: 0 };
      counts[cat].closed++;
    });

    var grid = mkEl("div", "pcorg-stats-grid");
    var totalOpen = 0, totalClosed = 0;

    CATEGORY_ORDER.forEach(function (cat) {
      var c = counts[cat];
      if (!c) c = { open: 0, closed: 0 };
      totalOpen += c.open;
      totalClosed += c.closed;
      var st = catStyle(cat);

      var card = mkEl("div", "pcorg-stat-card");
      card.style.borderLeftColor = st.color;
      card.title = shortCat(cat);

      var ico = matIcon(st.icon, "pcorg-stat-icon");
      ico.style.color = st.color;
      card.appendChild(ico);

      var cnts = mkEl("div", "pcorg-stat-counts");
      var openEl = mkEl("span", "pcorg-stat-open");
      openEl.style.color = c.open > 0 ? st.color : "var(--muted)";
      openEl.textContent = c.open;
      cnts.appendChild(openEl);

      var closedEl = mkEl("span", "pcorg-stat-closed");
      closedEl.textContent = c.closed;
      cnts.appendChild(closedEl);

      card.appendChild(cnts);
      card.style.cursor = "pointer";
      card.addEventListener("click", (function (catName) {
        return function () { focusMostRecentByCat(catName); };
      })(cat));
      grid.appendChild(card);
    });

    statsContainer.appendChild(grid);
  }

  // ── Focus most recent intervention by category ─────────────────────────────
  function focusMostRecentByCat(cat) {
    if (!lastData || !lastData.open) return;
    // Trouver l'intervention ouverte la plus recente de cette categorie avec GPS
    var match = null;
    for (var i = 0; i < lastData.open.length; i++) {
      var item = lastData.open[i];
      if (item.category === cat && item.lat != null && item.lon != null) {
        match = item;
        break; // deja triees par ts desc
      }
    }
    if (!match) {
      // Pas de GPS, ouvrir la fiche de la plus recente sans GPS
      for (var j = 0; j < lastData.open.length; j++) {
        if (lastData.open[j].category === cat) { match = lastData.open[j]; break; }
      }
      if (match) {
        openDetailModal(match.id, false);
      } else {
        showToast("info", "Aucune intervention en cours pour " + shortCat(cat));
      }
      return;
    }
    // Switch carte + fly + ouvrir popup
    var map = getMap();
    if (window.CockpitMapView && window.CockpitMapView.currentView() !== "map") {
      window.CockpitMapView.switchView("map");
    }
    setTimeout(function () {
      if (!map) return;
      // Ouvrir le popup d'abord pour mesurer sa hauteur
      var marker = pcorgMarkers[match.id];
      if (marker) marker.openPopup();
      // Decaler le centrage vers le bas pour que le popup ne soit pas coupe
      setTimeout(function () {
        var popupPx = 200; // estimation hauteur popup en pixels
        var popup = marker ? marker.getPopup() : null;
        if (popup && popup.getElement()) {
          popupPx = popup.getElement().offsetHeight || 200;
        }
        var targetPoint = map.project([match.lat, match.lon], 17);
        targetPoint.y -= popupPx / 2;
        var targetLatLng = map.unproject(targetPoint, 17);
        map.flyTo(targetLatLng, 17, { duration: 0.8 });
      }, 100);
    }, 300);
  }

  // ── Detail modal ────────────────────────────────────────────────────────────
  var detailModal = null;
  var detailOverlay = null;
  var detailMiniMap = null;

  function showFiche() {
    detailModal.classList.add("show");
    detailOverlay.classList.add("show");
  }
  function hideFiche() {
    detailModal.classList.remove("show");
    detailOverlay.classList.remove("show");
    destroyMiniMap();
  }

  function openDetailModal(id, isClosed) {
    detailModal = detailModal || document.getElementById("pcorgDetailModal");
    detailOverlay = detailOverlay || document.getElementById("pcorgDetailOverlay");
    if (!detailModal) return;
    var body = document.getElementById("pcorg-fiche-body");
    body.textContent = "";
    var loading = mkEl("div", "widget-placeholder");
    loading.appendChild(matIcon("hourglass_top"));
    var lt = mkEl("span", ""); lt.textContent = "Chargement..."; loading.appendChild(lt);
    body.appendChild(loading);
    showFiche();

    // Wire close
    var closeBtn = document.getElementById("pcorgDetailClose");
    closeBtn.onclick = function () { hideFiche(); };
    detailOverlay.onclick = function () { hideFiche(); };

    fetch("/api/pcorg/detail/" + encodeURIComponent(id))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { body.textContent = d.error; return; }
        // Ack bounce for this user
        ackPin(id, d.bounce_rev || 0);
        renderFiche(d, isClosed);
      })
      .catch(function () { body.textContent = "Erreur de chargement"; });
  }

  function destroyMiniMap() {
    if (detailMiniMap) { detailMiniMap.remove(); detailMiniMap = null; }
  }

  function renderFiche(d, isClosed) {
    var st = catStyle(d.category);
    var cc = d.content_category || {};

    // Header
    var header = document.getElementById("pcorg-fiche-header");
    header.style.background = st.color;
    header.querySelector(".pcorg-fiche-icon").textContent = st.icon;
    header.querySelector(".pcorg-fiche-cat").textContent = d.category || "";
    header.querySelector(".pcorg-fiche-subcat").textContent = cc.sous_classification || cc.classification || cc.typedemande || "";
    header.querySelector(".pcorg-fiche-num").textContent = d.sql_id ? "N\u00b0 " + d.sql_id : "";
    var statusEl = header.querySelector(".pcorg-fiche-status");
    statusEl.textContent = d.status_code === 10 ? "TERMINE" : "EN COURS";
    statusEl.className = "pcorg-fiche-status " + (d.status_code === 10 ? "closed" : "open");

    // Urgency badge in header
    var urgEl = header.querySelector(".pcorg-fiche-urgency");
    if (urgEl) {
      if (d.niveau_urgence) {
        urgEl.textContent = urgencyLabel(d.category, d.niveau_urgence);
        urgEl.className = "pcorg-fiche-urgency pcorg-urgency-" + d.niveau_urgence;
        urgEl.style.display = "";
      } else {
        urgEl.style.display = "none";
      }
    }

    // Body
    var body = document.getElementById("pcorg-fiche-body");
    body.textContent = "";

    // Description
    var descText = d.text_full || d.text;
    if (descText) {
      var desc = mkEl("div", "pcorg-fiche-desc");
      desc.style.borderLeftColor = st.color;
      desc.textContent = descText;
      body.appendChild(desc);
    }

    // Urgency level selector (only if not closed)
    if (!isClosed && d.status_code !== 10) {
      var urgSec = mkEl("div", "pcorg-fiche-section");
      urgSec.textContent = "Niveau d'urgence";
      body.appendChild(urgSec);
      body.appendChild(buildUrgencyButtons(d.niveau_urgence, d.category, d.id, false));
    }

    // Info row (fields + mini map)
    var infoRow = mkEl("div", "pcorg-fiche-info-row");

    // Fields
    var fields = mkEl("div", "pcorg-fiche-fields");
    var opDisplay = d.operator || "";
    if (d.operator_group) opDisplay += " (" + d.operator_group + ")";
    addField(fields, "Operateur", opDisplay);
    if (d.ts) addField(fields, "Ouverture", new Date(d.ts).toLocaleString("fr-FR"));
    if (d.close_ts && d.status_code === 10) addField(fields, "Cloture", new Date(d.close_ts).toLocaleString("fr-FR"));
    if (d.operator_close && d.operator_close !== d.operator) addField(fields, "Clos par", d.operator_close);
    addField(fields, "Zone", truncZone(d.area_desc));
    addField(fields, "Appelant", cc.appelant);
    var contact = [];
    if (cc.telephone) contact.push(typeof cc.telephone === "string" ? cc.telephone : "Telephone");
    if (cc.radio) contact.push(typeof cc.radio === "string" ? cc.radio : "Radio");
    if (contact.length) addField(fields, "Via", contact.join(" / "));
    addField(fields, "Carroye", cc.carroye);
    addField(fields, "Groupe", formatGroupDesc(d.group_desc, d.category));
    infoRow.appendChild(fields);

    // Vehicle engagement banner (prominent, before minimap)
    if (cc.patrouille) {
      var ficheDevInfo = window.getAnolocDeviceByLabel ? window.getAnolocDeviceByLabel(cc.patrouille) : null;
      var ficheDevSt = ficheDevInfo ? _resolveDeviceStatus(ficheDevInfo.device) : null;
      var vBanner = mkEl("div", "pcorg-fiche-vehicle");
      vBanner.appendChild(matIcon("directions_car", "pcorg-fiche-vehicle-ico"));
      var vInfo = mkEl("div", "pcorg-fiche-vehicle-info");
      var vLbl = mkEl("span", "pcorg-fiche-vehicle-lbl");
      vLbl.textContent = "Element engage";
      vInfo.appendChild(vLbl);
      var vName = mkEl("strong", "pcorg-fiche-vehicle-name");
      vName.textContent = cc.patrouille;
      vInfo.appendChild(vName);
      vBanner.appendChild(vInfo);
      if (ficheDevSt) {
        var fvStatusBadge = mkEl("span", "pcorg-fiche-vehicle-status");
        var fvDot = mkEl("span", "pcorg-fiche-vehicle-dot");
        fvDot.style.background = ficheDevSt.color;
        fvStatusBadge.appendChild(fvDot);
        fvStatusBadge.appendChild(document.createTextNode(ficheDevSt.label));
        fvStatusBadge.style.color = ficheDevSt.color;
        vBanner.appendChild(fvStatusBadge);
      }
      body.appendChild(vBanner);
    }

    // Mini map
    var mapDiv = mkEl("div", "pcorg-fiche-minimap");
    if (d.lat != null && d.lon != null) {
      mapDiv.id = "pcorg-minimap-container";
      infoRow.appendChild(mapDiv);
      body.appendChild(infoRow);
      setTimeout(function () { initMiniMap(d.lat, d.lon, st); }, 100);
    } else {
      mapDiv.classList.add("empty");
      mapDiv.textContent = "Pas de position";
      infoRow.appendChild(mapDiv);
      body.appendChild(infoRow);
    }

    // Category-specific details
    var specific = buildSpecificFields(d.category, cc);
    if (specific.length > 0) {
      var secTitle = mkEl("div", "pcorg-fiche-section");
      secTitle.textContent = "Details";
      body.appendChild(secTitle);
      var specFields = mkEl("div", "pcorg-fiche-fields");
      specific.forEach(function (f) { addField(specFields, f[0], f[1]); });
      body.appendChild(specFields);
    }

    // Extracted entities
    if ((d.phones && d.phones.length) || (d.plates && d.plates.length)) {
      var entSec = mkEl("div", "pcorg-fiche-section");
      entSec.textContent = "Extractions";
      body.appendChild(entSec);
      var entFields = mkEl("div", "pcorg-fiche-fields");
      if (d.phones && d.phones.length) addField(entFields, "Telephones", d.phones.join(", "));
      if (d.plates && d.plates.length) addField(entFields, "Plaques", d.plates.join(", "));
      body.appendChild(entFields);
    }

    // Chronology
    var history = d.comment_history || [];
    if (history.length > 0) {
      var chronoSec = mkEl("div", "pcorg-fiche-section");
      chronoSec.textContent = "Chronologie";
      body.appendChild(chronoSec);
      var timeline = mkEl("div", "pcorg-fiche-timeline");
      timeline.style.setProperty("--cat-color", st.color);
      history.forEach(function (entry) {
        var isStatus = entry.text && entry.text.indexOf("Statut:") === 0;
        var ent = mkEl("div", "pcorg-chrono-entry" + (isStatus ? " status-change" : ""));
        ent.style.setProperty("--dot-color", isStatus ? "var(--muted)" : st.color);
        ent.querySelector || null; // noop
        // dot color via border-color
        var dotStyle = "border-color:" + (isStatus ? "var(--muted)" : st.color);
        ent.setAttribute("style", "--dot-color:" + (isStatus ? "#94a3b8" : st.color));

        var head = mkEl("div", "pcorg-chrono-head");
        var tsEl = mkEl("span", "pcorg-chrono-ts");
        try {
          var dt = new Date(entry.ts);
          tsEl.textContent = String(dt.getHours()).padStart(2, "0") + ":" +
            String(dt.getMinutes()).padStart(2, "0");
        } catch (e) { tsEl.textContent = entry.ts || ""; }
        head.appendChild(tsEl);
        var opEl = mkEl("span", "pcorg-chrono-op");
        opEl.textContent = entry.operator || "";
        head.appendChild(opEl);
        ent.appendChild(head);

        var entryText = entry.text || entry.comment || "";
        if (entryText) {
          var txt = mkEl("div", "pcorg-chrono-text");
          txt.textContent = entryText;
          ent.appendChild(txt);
        }
        if (entry.photo) {
          var photoWrap = mkEl("div", "pcorg-chrono-photo-wrap");
          var img = mkEl("img", "pcorg-chrono-photo");
          img.src = entry.photo;
          img.alt = "Photo terrain";
          img.addEventListener("click", function () { openPhotoLightbox(entry.photo); });
          photoWrap.appendChild(img);
          ent.appendChild(photoWrap);
        }
        timeline.appendChild(ent);
      });
      body.appendChild(timeline);
    }

    // Add comment form (only if not closed)
    if (!isClosed && d.status_code !== 10) {
      var commentSec = mkEl("div", "pcorg-fiche-section");
      commentSec.textContent = "Consigner une action";
      body.appendChild(commentSec);

      var commentForm = mkEl("div", "pcorg-comment-form");
      var commentInput = mkEl("textarea", "form-input pcorg-comment-input");
      commentInput.rows = 2;
      commentInput.placeholder = "Action realisee, observation, consigne...";
      commentForm.appendChild(commentInput);

      var commentBtn = mkEl("button", "pcorg-comment-send");
      commentBtn.appendChild(matIcon("send"));
      commentBtn.title = "Envoyer";
      commentBtn.addEventListener("click", function () {
        var txt = commentInput.value.trim();
        if (!txt) return;
        commentBtn.disabled = true;
        apiPost("/api/pcorg/comment/" + encodeURIComponent(d.id), { text: txt })
          .then(function (r) {
            commentBtn.disabled = false;
            if (r.ok) {
              commentInput.value = "";
              showToast("success", "Commentaire ajoute");
              // Re-open detail to refresh chronology
              openDetailModal(d.id, false);
            } else {
              showToast("error", r.error || "Erreur");
            }
          });
      });
      commentForm.appendChild(commentBtn);
      body.appendChild(commentForm);
    }

    // Actions
    var actions = mkEl("div", "pcorg-fiche-actions");
    if (d.lat != null) {
      var btnMap = mkEl("button", "");
      btnMap.appendChild(matIcon("map"));
      btnMap.appendChild(document.createTextNode(" Voir sur carte"));
      btnMap.addEventListener("click", function () {
        hideFiche(); flyToPin(d.lat, d.lon);
      });
      actions.appendChild(btnMap);
    } else {
      var btnGps = mkEl("button", "");
      btnGps.appendChild(matIcon("add_location"));
      btnGps.appendChild(document.createTextNode(" Ajouter position"));
      btnGps.addEventListener("click", function () {
        hideFiche(); openGpsModal(d.id);
      });
      actions.appendChild(btnGps);
    }
    // Edit button (not closed)
    if (!isClosed && d.status_code !== 10) {
      var btnEdit = mkEl("button", "");
      btnEdit.appendChild(matIcon("edit"));
      btnEdit.appendChild(document.createTextNode(" Editer"));
      btnEdit.addEventListener("click", function () {
        renderFicheEdit(d);
      });
      actions.appendChild(btnEdit);
    }
    if (!isClosed && d.status_code !== 10 && (window.__userCanCloseFiche || window.__userIsAdmin)) {
      var btnClose = mkEl("button", "pcorg-btn-danger");
      btnClose.appendChild(matIcon("check_circle"));
      btnClose.appendChild(document.createTextNode(" Clore"));
      btnClose.addEventListener("click", function () {
        closeIntervention(d.id);
      });
      actions.appendChild(btnClose);
    }
    // Delete button (admin only)
    if (window.__userIsAdmin) {
      var btnDel = mkEl("button", "pcorg-btn-delete");
      btnDel.appendChild(matIcon("delete"));
      btnDel.appendChild(document.createTextNode(" Supprimer"));
      btnDel.addEventListener("click", function () {
        showConfirmToast("Supprimer definitivement cette intervention ?", { type: "error", okLabel: "Supprimer" }).then(function (ok) {
          if (!ok) return;
          deleteIntervention(d.id);
        });
      });
      actions.appendChild(btnDel);
    }
    body.appendChild(actions);
  }

  // ── Edit mode on fiche ────────────────────────────────────────────────────
  function renderFicheEdit(d) {
    var st = catStyle(d.category);
    var cc = d.content_category || {};
    var body = document.getElementById("pcorg-fiche-body");
    body.textContent = "";

    // Description
    var descSec = mkEl("div", "pcorg-fiche-section"); descSec.textContent = "Description"; body.appendChild(descSec);
    var descInput = mkEl("textarea", "form-input");
    descInput.id = "pcorg-edit-text"; descInput.rows = 2;
    descInput.value = d.text_full || d.text || "";
    body.appendChild(descInput);

    // Category
    var catSec = mkEl("div", "pcorg-fiche-section"); catSec.textContent = "Categorie"; body.appendChild(catSec);
    var catContainer = mkEl("div", "pcorg-create-cats");
    var editCat = d.category;
    CATEGORY_ORDER.forEach(function (cat) {
      var s = catStyle(cat);
      var btn = mkEl("button", "pcorg-create-cat-btn" + (cat === editCat ? " selected" : ""));
      btn.type = "button";
      btn.setAttribute("data-cat", cat);
      if (cat === editCat) { btn.style.borderColor = s.color; btn.style.background = s.color; }
      var ico = matIcon(s.icon);
      ico.style.color = cat === editCat ? "#fff" : s.color;
      btn.appendChild(ico);
      var label = mkEl("span", ""); label.textContent = shortCat(cat); btn.appendChild(label);
      btn.addEventListener("click", function () {
        editCat = cat;
        var ns = catStyle(cat);
        catContainer.querySelectorAll(".pcorg-create-cat-btn").forEach(function (b) {
          var sel = b.getAttribute("data-cat") === cat;
          b.classList.toggle("selected", sel);
          b.style.borderColor = sel ? ns.color : "";
          b.style.background = sel ? ns.color : "";
          var bico = b.querySelector(".material-symbols-outlined");
          if (bico) bico.style.color = sel ? "#fff" : catStyle(b.getAttribute("data-cat")).color;
        });
        var hdr = document.getElementById("pcorg-fiche-header");
        hdr.style.background = ns.color;
        hdr.querySelector(".pcorg-fiche-icon").textContent = ns.icon;
        hdr.querySelector(".pcorg-fiche-cat").textContent = cat;
        // Rebuild specific fields
        buildEditSpecificFields(editSpecContainer, cat, cc, editUrgency);
      });
      catContainer.appendChild(btn);
    });
    body.appendChild(catContainer);

    // Appelant + contact
    var editUrgency = d.niveau_urgence || "";
    var infoSec = mkEl("div", "pcorg-fiche-section"); infoSec.textContent = "Informations"; body.appendChild(infoSec);
    var grpApp = mkEl("div", "form-group");
    var lblApp = mkEl("label", ""); lblApp.textContent = "Appelant"; lblApp.setAttribute("for", "pcorg-edit-appelant");
    grpApp.appendChild(lblApp);
    var inpApp = mkEl("input", "form-input"); inpApp.type = "text"; inpApp.id = "pcorg-edit-appelant";
    inpApp.value = cc.appelant || "";
    grpApp.appendChild(inpApp);
    body.appendChild(grpApp);

    var grpContact = mkEl("div", "form-group");
    var lblCo = mkEl("label", ""); lblCo.textContent = "Contact via"; grpContact.appendChild(lblCo);
    var coRow = mkEl("div", ""); coRow.style.cssText = "display:flex;gap:8px;";
    var lblTel = mkEl("label", ""); lblTel.style.cssText = "font-size:0.78rem;display:flex;align-items:center;gap:4px";
    var rdTel = mkEl("input", ""); rdTel.type = "radio"; rdTel.name = "pcorg-edit-contact"; rdTel.id = "pcorg-edit-tel"; rdTel.value = "telephone";
    rdTel.checked = !!cc.telephone;
    lblTel.appendChild(rdTel); lblTel.appendChild(document.createTextNode("Telephone"));
    coRow.appendChild(lblTel);
    var lblRad = mkEl("label", ""); lblRad.style.cssText = "font-size:0.78rem;display:flex;align-items:center;gap:4px";
    var rdRad = mkEl("input", ""); rdRad.type = "radio"; rdRad.name = "pcorg-edit-contact"; rdRad.id = "pcorg-edit-radio"; rdRad.value = "radio";
    rdRad.checked = !!cc.radio;
    lblRad.appendChild(rdRad); lblRad.appendChild(document.createTextNode("Radio"));
    coRow.appendChild(lblRad);
    var inpCanal = mkEl("input", "form-input"); inpCanal.type = "text"; inpCanal.id = "pcorg-edit-radio-canal";
    inpCanal.placeholder = "Canal..."; inpCanal.style.cssText = "flex:1;display:" + (cc.radio ? "" : "none");
    inpCanal.value = (typeof cc.radio === "string") ? cc.radio : "";
    rdRad.addEventListener("change", function () { inpCanal.style.display = rdRad.checked ? "" : "none"; });
    rdTel.addEventListener("change", function () { if (rdTel.checked) inpCanal.style.display = "none"; });
    coRow.appendChild(inpCanal);
    grpContact.appendChild(coRow);
    body.appendChild(grpContact);

    // Category-specific fields (includes urgency, action, vehicle in correct order)
    var editSpecContainer = mkEl("div", "");
    body.appendChild(editSpecContainer);
    buildEditSpecificFields(editSpecContainer, editCat, cc, editUrgency);

    // Save / Cancel
    var editActions = mkEl("div", "pcorg-fiche-actions");
    var btnCancel = mkEl("button", "");
    btnCancel.appendChild(matIcon("close"));
    btnCancel.appendChild(document.createTextNode(" Annuler"));
    btnCancel.addEventListener("click", function () { openDetailModal(d.id, false); });
    editActions.appendChild(btnCancel);

    var btnSave = mkEl("button", "pcorg-btn-primary");
    btnSave.style.cssText = "background:var(--brand);color:#fff;border-color:var(--brand)";
    btnSave.appendChild(matIcon("save"));
    btnSave.appendChild(document.createTextNode(" Enregistrer"));
    btnSave.addEventListener("click", function () {
      submitFicheEdit(d.id, editCat, cc, editUrgency);
    });
    editActions.appendChild(btnSave);
    body.appendChild(editActions);
  }

  function buildEditSpecificFields(container, cat, cc, editUrgency) {
    container.textContent = "";
    var urgCats = (pcorgConfig && pcorgConfig.urgence_categories) || {};
    var subs = extractLabels((pcorgConfig.sous_classifications || {})[cat]);
    var intervList = extractLabels(pcorgConfig.intervenants);
    var serviceList = extractLabels(pcorgConfig.services);
    var vehicles = vehiclesByCategory[cat];

    function appendEditUrgency() {
      if (!urgCats[cat]) return;
      var urgSec = mkEl("div", "pcorg-fiche-section"); urgSec.textContent = "Niveau d'urgence";
      container.appendChild(urgSec);
      var urgContainer = mkEl("div", "pcorg-create-cats");
      urgContainer.id = "pcorg-edit-urgency-container";
      function renderBtns() {
        urgContainer.textContent = "";
        var uType = urgencyType(cat);
        var noneBtnU = mkEl("button", "pcorg-create-cat-btn" + (!editUrgency ? " selected" : ""));
        noneBtnU.type = "button";
        noneBtnU.style.cssText = !editUrgency ? "border-color:var(--muted);background:var(--muted);font-size:0.75rem" : "font-size:0.75rem";
        noneBtnU.textContent = "Aucun";
        noneBtnU.addEventListener("click", function () { editUrgency = ""; renderBtns(); });
        urgContainer.appendChild(noneBtnU);
        URGENCY_LEVELS.forEach(function (lvl) {
          var c = URGENCY_COLORS[lvl];
          var btnU = mkEl("button", "pcorg-create-cat-btn" + (editUrgency === lvl ? " selected" : ""));
          btnU.type = "button";
          if (editUrgency === lvl) { btnU.style.borderColor = c; btnU.style.background = c; }
          var dotU = mkEl("span", ""); dotU.style.cssText = "width:8px;height:8px;border-radius:50%;background:" + c;
          btnU.appendChild(dotU);
          var lblU = mkEl("span", ""); lblU.style.fontSize = "0.72rem";
          lblU.textContent = URGENCY_LABELS[uType][lvl];
          btnU.appendChild(lblU);
          btnU.addEventListener("click", function () { editUrgency = lvl; renderBtns(); });
          urgContainer.appendChild(btnU);
        });
      }
      renderBtns();
      container.appendChild(urgContainer);
    }

    function appendEditComment() {
      var actionSec = mkEl("div", "pcorg-fiche-section"); actionSec.textContent = "Action prise";
      container.appendChild(actionSec);
      var grpAction = mkEl("div", "form-group");
      var lblAction = mkEl("label", "");
      lblAction.textContent = "Consignez l'action ou la modification ";
      var star = mkEl("span", ""); star.style.color = "var(--danger,#ef4444)"; star.textContent = "*";
      lblAction.appendChild(star);
      lblAction.setAttribute("for", "pcorg-edit-comment");
      grpAction.appendChild(lblAction);
      var inpAction = mkEl("textarea", "form-input");
      inpAction.id = "pcorg-edit-comment"; inpAction.rows = 2;
      inpAction.placeholder = "Action realisee, modification apportee...";
      grpAction.appendChild(inpAction);
      container.appendChild(grpAction);
    }

    function appendEditSousClassification() {
      if (subs.length > 0) {
        addEditSelect(container, "pcorg-edit-sous", "Sous-classification", subs, cc.sous_classification || "");
      }
    }

    function appendEditVehicle() {
      if (vehicles && vehicles.length > 0) {
        var vehNames = vehicles.map(function (v) { return v.label; });
        addEditSelect(container, "pcorg-edit-patrouille", "Vehicule engage", vehNames, cc.patrouille || "");
      }
    }

    if (cat === "PCO.Secours" || cat === "PCO.Securite" || cat === "PCO.Technique") {
      appendEditSousClassification();
      appendEditUrgency();
      appendEditComment();
      appendEditVehicle();
      if (intervList.length) {
        addEditSelect(container, "pcorg-edit-interv1", "Intervenant 1", intervList, cc.intervenant1 || "");
        addEditSelect(container, "pcorg-edit-interv2", "Intervenant 2", intervList, cc.intervenant2 || "");
      } else {
        addEditField(container, "pcorg-edit-interv1", "Intervenant 1", cc.intervenant1 || "");
        addEditField(container, "pcorg-edit-interv2", "Intervenant 2", cc.intervenant2 || "");
      }
      if (serviceList.length) {
        addEditSelect(container, "pcorg-edit-service", "Service contacte", serviceList, cc.service_contacte || "");
      } else {
        addEditField(container, "pcorg-edit-service", "Service contacte", cc.service_contacte || "");
      }
    } else if (cat === "PCO.Flux") {
      appendEditSousClassification();
      appendEditUrgency();
      appendEditComment();
      appendEditVehicle();
      if (intervList.length) {
        addEditSelect(container, "pcorg-edit-moyens1", "Moyens Niv.1", intervList, cc.moyens_engages_niveau_1 || "");
        addEditSelect(container, "pcorg-edit-moyens2", "Moyens Niv.2", intervList, cc.moyens_engages_niveau_2 || "");
      } else {
        addEditField(container, "pcorg-edit-moyens1", "Moyens Niv.1", cc.moyens_engages_niveau_1 || "");
        addEditField(container, "pcorg-edit-moyens2", "Moyens Niv.2", cc.moyens_engages_niveau_2 || "");
      }
    } else if (cat === "PCO.Fourriere") {
      addEditSelect(container, "pcorg-edit-typedemande", "Type de demande",
        ["Parking sauvage", "Pas de titre", "Mauvais titre (sticker ou badge)", "Stationnement genant", "Autre"],
        cc.typedemande || "");
      addEditField(container, "pcorg-edit-lieu", "Lieu", cc.lieu || "");
      addEditField(container, "pcorg-edit-detailsvl", "Vehicule", cc.detailsvl || "");
      addEditField(container, "pcorg-edit-immat", "Immatriculation", cc.immat || "");
      addEditSelect(container, "pcorg-edit-decision", "Decision",
        ["Remorquage demande", "Sabot pose", "Avertissement", "Annule"],
        cc.decision || "");
      appendEditComment();
      appendEditVehicle();
    } else {
      // Information, MainCourante, autres
      appendEditSousClassification();
      appendEditUrgency();
      appendEditComment();
      appendEditVehicle();
    }
  }

  function addEditField(container, id, label, value) {
    var grp = mkEl("div", "form-group");
    var lbl = mkEl("label", ""); lbl.textContent = label; lbl.setAttribute("for", id);
    grp.appendChild(lbl);
    var inp = mkEl("input", "form-input"); inp.type = "text"; inp.id = id;
    inp.placeholder = label; inp.value = value || "";
    grp.appendChild(inp);
    container.appendChild(grp);
  }

  function addEditSelect(container, id, label, options, current) {
    var grp = mkEl("div", "form-group");
    var lbl = mkEl("label", ""); lbl.textContent = label; lbl.setAttribute("for", id);
    grp.appendChild(lbl);
    var sel = mkEl("select", "form-input"); sel.id = id;
    var opt0 = mkEl("option", ""); opt0.value = ""; opt0.textContent = "-- Choisir --";
    sel.appendChild(opt0);
    options.forEach(function (o) {
      var opt = mkEl("option", ""); opt.value = o; opt.textContent = o;
      if (o === current) opt.selected = true;
      sel.appendChild(opt);
    });
    // If current value not in list, add it
    if (current && options.indexOf(current) === -1) {
      var optC = mkEl("option", ""); optC.value = current; optC.textContent = current; optC.selected = true;
      sel.appendChild(optC);
    }
    grp.appendChild(sel);
    container.appendChild(grp);
  }

  function submitFicheEdit(id, editCat, origCc, editUrgency) {
    var comment = (document.getElementById("pcorg-edit-comment").value || "").trim();
    if (!comment) { showToast("warning", "L'action prise est obligatoire"); return; }

    var text = (document.getElementById("pcorg-edit-text").value || "").trim();
    if (!text) { showToast("warning", "La description est obligatoire"); return; }

    var ccUpdate = {};
    var appelant = (document.getElementById("pcorg-edit-appelant").value || "").trim();
    ccUpdate.appelant = appelant;
    var contactSel = document.querySelector('input[name="pcorg-edit-contact"]:checked');
    ccUpdate.telephone = (contactSel && contactSel.value === "telephone") || false;
    ccUpdate.radio = (contactSel && contactSel.value === "radio")
      ? ((document.getElementById("pcorg-edit-radio-canal").value || "").trim() || true)
      : false;

    var sousEl = document.getElementById("pcorg-edit-sous");
    if (sousEl) ccUpdate.sous_classification = sousEl.value;

    function eVal(eid) { var e = document.getElementById(eid); return e ? e.value.trim() : ""; }

    if (editCat === "PCO.Secours" || editCat === "PCO.Securite" || editCat === "PCO.Technique") {
      ccUpdate.intervenant1 = eVal("pcorg-edit-interv1");
      ccUpdate.intervenant2 = eVal("pcorg-edit-interv2");
      ccUpdate.service_contacte = eVal("pcorg-edit-service");
      ccUpdate.carroye = eVal("pcorg-edit-carroye");
    } else if (editCat === "PCO.Information" || editCat === "PCO.MainCourante") {
      ccUpdate.texte = eVal("pcorg-edit-texte");
      if (editCat === "PCO.MainCourante") {
        var alerteEl = document.getElementById("pcorg-edit-alerte");
        ccUpdate.alerte = alerteEl ? alerteEl.checked : false;
      }
    } else if (editCat === "PCO.Fourriere") {
      ccUpdate.lieu = eVal("pcorg-edit-lieu");
      ccUpdate.detailsvl = eVal("pcorg-edit-detailsvl");
      ccUpdate.immat = eVal("pcorg-edit-immat");
      ccUpdate.typedemande = eVal("pcorg-edit-typedemande");
      ccUpdate.decision = eVal("pcorg-edit-decision");
    } else if (editCat === "PCO.Flux") {
      ccUpdate.moyens_engages_niveau_1 = eVal("pcorg-edit-moyens1");
      ccUpdate.moyens_engages_niveau_2 = eVal("pcorg-edit-moyens2");
    }

    // Patrouille
    var patrEl = document.getElementById("pcorg-edit-patrouille");
    if (patrEl) ccUpdate.patrouille = patrEl.value;

    var payload = { text: text, category: editCat, content_category: ccUpdate, niveau_urgence: editUrgency || null };

    // 1) Save fields, then 2) post comment
    fetch("/api/pcorg/update/" + encodeURIComponent(id), {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": (document.querySelector('meta[name="csrf-token"]') || {}).content || ""
      },
      body: JSON.stringify(payload)
    })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        if (!r.ok) { showToast("error", r.error || "Erreur"); return; }
        // Post the action prise as comment
        return apiPost("/api/pcorg/comment/" + encodeURIComponent(id), { text: comment });
      })
      .then(function (r) {
        if (!r) return;
        if (r.ok) {
          showToast("success", "Intervention mise a jour");
          refresh();
          openDetailModal(id, false);
        } else {
          showToast("error", r.error || "Erreur commentaire");
        }
      });
  }

  function addField(parent, label, value) {
    if (!value) return;
    var row = mkEl("div", "pcorg-fiche-field");
    var lbl = mkEl("span", "pcorg-fiche-label");
    lbl.textContent = label;
    row.appendChild(lbl);
    var val = mkEl("span", "pcorg-fiche-value");
    val.textContent = value;
    row.appendChild(val);
    parent.appendChild(row);
  }

  function buildSpecificFields(category, cc) {
    var fields = [];
    var cat = category || "";
    if (cat === "PCO.Fourriere") {
      if (cc.detailsvl) fields.push(["Vehicule", cc.detailsvl]);
      if (cc.immat) fields.push(["Immatriculation", cc.immat]);
      if (cc.lieu) fields.push(["Lieu", cc.lieu]);
      if (cc.typedemande) fields.push(["Type demande", cc.typedemande]);
      if (cc.decision) fields.push(["Decision", cc.decision]);
      if (cc.dhenlevement) fields.push(["Enlevement", cc.dhenlevement]);
      if (cc.paiement) fields.push(["Paiement", cc.paiement]);
      if (cc.precurseur) fields.push(["Precurseur", cc.precurseur]);
    } else if (cat === "PCO.Flux") {
      if (cc.moyens_engages_niveau_1) fields.push(["Moyens Niv.1", cc.moyens_engages_niveau_1]);
      if (cc.moyens_engages_niveau_2) fields.push(["Moyens Niv.2", cc.moyens_engages_niveau_2]);
    } else if (cat === "PCO.Secours" || cat === "PCO.Securite" || cat === "PCO.Technique") {
      var intervs = [];
      for (var i = 1; i <= 5; i++) { if (cc["intervenant" + i]) intervs.push(cc["intervenant" + i]); }
      if (intervs.length) fields.push(["Intervenants", intervs.join(", ")]);
      if (cc.service_contacte) fields.push(["Service contacte", cc.service_contacte]);
    } else if (cat === "PCO.Information" || cat === "PCO.MainCourante") {
      if (cc.texte) fields.push(["Texte", cc.texte]);
      if (cc.alerte) fields.push(["Alerte", "Oui"]);
    }
    return fields;
  }

  function initMiniMap(lat, lon, st) {
    destroyMiniMap();
    var container = document.getElementById("pcorg-minimap-container");
    if (!container) return;
    detailMiniMap = L.map(container, {
      center: [lat, lon], zoom: 16, zoomControl: false,
      attributionControl: false, dragging: false, scrollWheelZoom: false,
      doubleClickZoom: false, touchZoom: false
    });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19
    }).addTo(detailMiniMap);
    var pinHtml = "<div class='pcorg-pin' style='background:" + st.color + "'>" +
      "<span class='material-symbols-outlined'>" + st.icon + "</span></div>";
    L.marker([lat, lon], {
      icon: L.divIcon({ className: "", html: pinHtml, iconSize: [36, 36], iconAnchor: [18, 36] })
    }).addTo(detailMiniMap);
    setTimeout(function () { detailMiniMap.invalidateSize(); }, 150);
  }

  // ── Change urgency level ────────────────────────────────────────────────────
  function setUrgencyLevel(id, level, category) {
    apiPost("/api/pcorg/set-urgency/" + encodeURIComponent(id), { niveau_urgence: level || null })
      .then(function (r) {
        if (r.ok) {
          var label = level ? urgencyLabel(category, level) : "Aucun";
          showToast("success", "Urgence \u2192 " + label);
          refresh();
          // Re-open detail modal to refresh
          openDetailModal(id, false);
        } else {
          showToast("error", r.error || "Erreur");
        }
      });
  }

  function buildUrgencyButtons(currentLevel, category, ficheId, compact) {
    var container = mkEl("div", "pcorg-urgency-selector" + (compact ? " compact" : ""));
    var levels = [
      { code: "EU", color: URGENCY_COLORS.EU },
      { code: "UA", color: URGENCY_COLORS.UA },
      { code: "UR", color: URGENCY_COLORS.UR },
      { code: "IMP", color: URGENCY_COLORS.IMP },
      { code: null, color: "#94a3b8" }
    ];
    levels.forEach(function (lvl) {
      var isActive = (currentLevel || null) === lvl.code;
      var btn = mkEl("button", "pcorg-urg-btn" + (isActive ? " active" : ""));
      btn.type = "button";
      btn.style.setProperty("--urg-color", lvl.color);
      if (isActive) { btn.style.background = lvl.color; btn.style.color = "#fff"; btn.style.borderColor = lvl.color; }
      var dot = mkEl("span", "pcorg-urg-dot");
      dot.style.background = lvl.color;
      btn.appendChild(dot);
      var text = mkEl("span", "");
      text.textContent = lvl.code ? (compact ? lvl.code : urgencyLabel(category, lvl.code)) : (compact ? "\u2013" : "Aucun");
      btn.appendChild(text);
      btn.addEventListener("click", function () {
        if (isActive) return;
        setUrgencyLevel(ficheId, lvl.code, category);
      });
      container.appendChild(btn);
    });
    return container;
  }

  // ── Close intervention ─────────────────────────────────────────────────────
  function closeIntervention(id) {
    showConfirmToast("Clore cette intervention ?").then(function (ok) {
      if (!ok) return;
      apiPost("/api/pcorg/close/" + encodeURIComponent(id), {})
        .then(function (r) {
          if (r.ok) {
            hideFiche();
            // Fermer les popups ouverts sur la carte
            var map = getMap();
            if (map) map.closePopup();
            showToast("success", "Intervention cloturee");
            refresh();
          } else {
            showToast("error", r.error || "Erreur");
          }
        });
    });
  }

  function deleteIntervention(id) {
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
    fetch("/api/pcorg/delete/" + encodeURIComponent(id), {
      method: "DELETE",
      headers: { "X-CSRFToken": csrf }
    })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        if (r.ok) {
          hideFiche();
          var map = getMap();
          if (map) map.closePopup();
          showToast("success", "Intervention supprimee");
          refresh();
        } else {
          showToast("error", r.error || "Erreur");
        }
      });
  }

  // ── Badge ──────────────────────────────────────────────────────────────────
  function updateBadge(count) {
    if (!badge) return;
    if (count > 0) {
      badge.textContent = count;
      badge.style.display = "";
    } else {
      badge.style.display = "none";
    }
  }

  // ── Map pins ───────────────────────────────────────────────────────────────
  var pendingPins = null;
  var ignoreAllControl = null;

  function addIgnoreAllControl(map) {
    if (ignoreAllControl) return;
    var IgnoreAll = L.Control.extend({
      options: { position: "topleft" },
      onAdd: function () {
        var container = L.DomUtil.create("div", "pcorg-ignore-all-ctrl");
        var btn = L.DomUtil.create("a", "pcorg-ignore-all-btn", container);
        btn.href = "#";
        btn.title = "Ignorer toutes les alertes interventions";
        btn.setAttribute("role", "button");
        var ico = document.createElement("span");
        ico.className = "material-symbols-outlined";
        ico.textContent = "notifications_off";
        ico.style.fontSize = "18px";
        ico.style.lineHeight = "30px";
        btn.appendChild(ico);
        L.DomEvent.disableClickPropagation(container);
        L.DomEvent.on(btn, "click", function (e) {
          L.DomEvent.preventDefault(e);
          if (lastData && lastData.open) {
            ackAllPins(lastData.open);
          }
          refresh();
          showToast("info", "Toutes les alertes ignorees");
        });
        return container;
      }
    });
    ignoreAllControl = new IgnoreAll();
    map.addControl(ignoreAllControl);
  }

  function getMap() {
    if (window.CockpitMapView && window.CockpitMapView.getMap) {
      return window.CockpitMapView.getMap();
    }
    return null;
  }

  function updateMapPins(openItems) {
    var map = getMap();
    if (!map) {
      pendingPins = openItems;
      return;
    }
    pendingPins = null;

    if (pcorgMapLayer) {
      pcorgMapLayer.clearLayers();
    } else {
      pcorgMapLayer = L.layerGroup().addTo(map);
    }
    pcorgMarkers = {};
    addIgnoreAllControl(map);

    openItems.forEach(function (item) {
      if (item.lat == null || item.lon == null) return;
      var st = catStyle(item.category);

      var pinOuter = document.createElement("div");
      pinOuter.style.position = "relative";

      var pulse = mkEl("div", "pcorg-pin-pulse");
      pulse.style.borderColor = st.color;
      pinOuter.appendChild(pulse);

      var pin = mkEl("div", "pcorg-pin");
      pin.style.background = st.color;
      pin.appendChild(matIcon(st.icon));
      pinOuter.appendChild(pin);

      var icon = L.divIcon({
        className: "",
        html: pinOuter.outerHTML,
        iconSize: [36, 36],
        iconAnchor: [18, 36],
        popupAnchor: [0, -38]
      });

      // Build popup
      var popupDiv = mkEl("div", "pcorg-popup");

      // Header bar
      var popHeader = mkEl("div", "pcorg-popup-header");
      popHeader.style.background = st.color;
      var popIcon = matIcon(st.icon, "pcorg-popup-icon");
      popHeader.appendChild(popIcon);
      var popCat = mkEl("span", "pcorg-popup-cat");
      popCat.textContent = shortCat(item.category);
      popHeader.appendChild(popCat);
      if (item.status_code !== 10) {
        var popStatus = mkEl("span", "pcorg-popup-status");
        popStatus.textContent = "EN COURS";
        popHeader.appendChild(popStatus);
      }
      if (item.niveau_urgence) {
        var popUrg = mkEl("span", "pcorg-popup-urgency");
        popUrg.textContent = urgencyLabel(item.category, item.niveau_urgence);
        popHeader.appendChild(popUrg);
      }
      popupDiv.appendChild(popHeader);

      var popBody = mkEl("div", "pcorg-popup-body");

      // Sous-classification
      if (item.sous_classification) {
        var scLine = mkEl("div", "pcorg-popup-subcat");
        scLine.textContent = item.sous_classification;
        popBody.appendChild(scLine);
      }

      // Description
      if (item.text) {
        var descLine = mkEl("div", "pcorg-popup-desc");
        descLine.textContent = item.text;
        popBody.appendChild(descLine);
      }

      // Vehicule engage (badge prominent with status)
      if (item.patrouille) {
        var popDevInfo = window.getAnolocDeviceByLabel ? window.getAnolocDeviceByLabel(item.patrouille) : null;
        var popDevSt = popDevInfo ? _resolveDeviceStatus(popDevInfo.device) : null;
        var vehBanner = mkEl("div", "pcorg-popup-vehicle");
        var vehLeft = mkEl("div", "pcorg-popup-vehicle-left");
        vehLeft.appendChild(matIcon("directions_car", "pcorg-popup-vehicle-icon"));
        var vehNameWrap = mkEl("div", "");
        var vehLabel = mkEl("span", "pcorg-popup-vehicle-label");
        vehLabel.textContent = "Element engage";
        vehNameWrap.appendChild(vehLabel);
        var vehName = mkEl("strong", "pcorg-popup-vehicle-name");
        vehName.textContent = item.patrouille;
        vehNameWrap.appendChild(vehName);
        vehLeft.appendChild(vehNameWrap);
        vehBanner.appendChild(vehLeft);
        if (popDevSt) {
          var vehStatusBadge = mkEl("span", "pcorg-popup-vehicle-status");
          var vehStDot = mkEl("span", "pcorg-popup-vehicle-dot");
          vehStDot.style.background = popDevSt.color;
          vehStatusBadge.appendChild(vehStDot);
          vehStatusBadge.appendChild(document.createTextNode(popDevSt.label));
          vehBanner.appendChild(vehStatusBadge);
        }
        popBody.appendChild(vehBanner);
      }

      // Info fields
      var elapsedMs = item.ts ? Date.now() - new Date(item.ts).getTime() : 0;
      var isOld = elapsedMs > 3600000; // > 1h

      var popFields = mkEl("div", "pcorg-popup-fields");
      if (truncZone(item.area_desc)) {
        var zField = mkEl("div", "pcorg-popup-field");
        zField.appendChild(matIcon("location_on", "pcorg-popup-fi"));
        var zVal = mkEl("span", ""); zVal.textContent = truncZone(item.area_desc);
        zField.appendChild(zVal);
        popFields.appendChild(zField);
      }
      var opField = mkEl("div", "pcorg-popup-field");
      opField.appendChild(matIcon("person", "pcorg-popup-fi"));
      var opVal = mkEl("span", "pcorg-popup-op-val"); opVal.textContent = item.operator || "?";
      opField.appendChild(opVal);
      popFields.appendChild(opField);

      // Heure de creation
      var tsField = mkEl("div", "pcorg-popup-field");
      tsField.appendChild(matIcon("event", "pcorg-popup-fi"));
      var tsVal = mkEl("span", "");
      tsVal.textContent = item.ts ? new Date(item.ts).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "?";
      tsField.appendChild(tsVal);
      popFields.appendChild(tsField);

      // Duree
      var durField = mkEl("div", "pcorg-popup-field");
      durField.appendChild(matIcon("timer", "pcorg-popup-fi"));
      var durVal = mkEl("span", isOld ? "pcorg-popup-old" : "");
      durVal.textContent = timeAgo(item.ts);
      durField.appendChild(durVal);
      popFields.appendChild(durField);

      popBody.appendChild(popFields);

      // Specific details placeholder (loaded on popup open)
      var specDiv = mkEl("div", "pcorg-popup-spec");
      popBody.appendChild(specDiv);

      // Chronology placeholder (loaded on popup open)
      var chronoDiv = mkEl("div", "pcorg-popup-chrono");
      chronoDiv.style.setProperty("--cat-color", st.color);
      var chronoLoading = mkEl("div", "pcorg-popup-chrono-loading");
      chronoLoading.textContent = "...";
      chronoDiv.appendChild(chronoLoading);
      popBody.appendChild(chronoDiv);

      // Urgency selector in popup (compact, only if open)
      if (item.status_code !== 10) {
        popBody.appendChild(buildUrgencyButtons(item.niveau_urgence, item.category, item.id, true));
      }

      // Buttons row
      var popBtns = mkEl("div", "pcorg-popup-btns");

      var popBtn = mkEl("button", "pcorg-popup-btn");
      popBtn.appendChild(matIcon("open_in_new"));
      popBtn.appendChild(document.createTextNode(" Ouvrir la fiche"));
      popBtn.addEventListener("click", (function (id) {
        return function () { openDetailModal(id, false); };
      })(item.id));
      popBtns.appendChild(popBtn);

      // Close intervention button
      if (item.status_code !== 10 && (window.__userCanCloseFiche || window.__userIsAdmin)) {
        var closePopBtn = mkEl("button", "pcorg-popup-btn pcorg-popup-btn-danger");
        closePopBtn.appendChild(matIcon("check_circle"));
        closePopBtn.appendChild(document.createTextNode(" Clore"));
        closePopBtn.addEventListener("click", (function (id) {
          return function () { closeIntervention(id); };
        })(item.id));
        popBtns.appendChild(closePopBtn);
      }

      popBody.appendChild(popBtns);
      popupDiv.appendChild(popBody);

      // Bounce animation based on bounce_rev vs user ack
      var doBounce = shouldBounce(item);

      var marker = L.marker([item.lat, item.lon], {
        icon: icon,
        bounceOnAdd: false
      }).bindPopup(popupDiv, { className: "pcorg-popup-wrap", maxWidth: 440, minWidth: 380 })
        .addTo(pcorgMapLayer);

      // Tooltip vehicule engage (permanent, a droite du pin)
      if (item.patrouille) {
        var tipDevInfo = window.getAnolocDeviceByLabel ? window.getAnolocDeviceByLabel(item.patrouille) : null;
        var tipDevSt = tipDevInfo ? _resolveDeviceStatus(tipDevInfo.device) : null;
        var tipColor = tipDevSt ? tipDevSt.color : "#94a3b8";
        var tipLabel = tipDevSt ? tipDevSt.label : "";
        var tipHtml = "<span class='veh-tip-name'>" + item.patrouille + "</span>"
          + (tipLabel ? "<span class='veh-tip-status' style='color:" + tipColor + "'><span class='veh-tip-dot' style='background:" + tipColor + "'></span>" + tipLabel + "</span>" : "");
        marker.bindTooltip(tipHtml, {
          permanent: true,
          direction: "right",
          offset: [12, -18],
          className: "pcorg-vehicle-tooltip",
        });
      }

      pcorgMarkers[item.id] = marker;

      if (doBounce) {
        marker.getElement().classList.add("pcorg-pin-bounce");
      }

      // Pan map to center pin slightly below middle (room for popup above)
      marker.on("click", function () {
        var map = getMap();
        if (!map) return;
        var latlng = marker.getLatLng();
        var px = map.latLngToContainerPoint(latlng);
        var offsetY = map.getSize().y * 0.25; // shift pin 25% below center
        var target = map.containerPointToLatLng([px.x, px.y - offsetY]);
        map.panTo(target, { animate: true, duration: 0.3 });
      });

      // Lazy load details + chronology on popup open + ack bounce
      marker.on("popupopen", (function (itemId, sDiv, cDiv, color, cat, itemRef, markerRef, opValEl) {
        return function () {
          // Ack: stop bounce for this user
          ackPin(itemId, itemRef.bounce_rev || 0);
          var el = markerRef.getElement();
          if (el) el.classList.remove("pcorg-pin-bounce");
          if (cDiv._loaded) return;
          cDiv._loaded = true;
          fetch("/api/pcorg/detail/" + encodeURIComponent(itemId))
            .then(function (r) { return r.json(); })
            .then(function (d) {
              // Update operator with group
              if (d.operator_group && opValEl) {
                opValEl.textContent = (d.operator || "?") + " (" + d.operator_group + ")";
              }
              // Specific fields (fourriere etc.)
              var cc = d.content_category || {};
              var specs = [];
              if (cat === "PCO.Fourriere") {
                if (cc.typedemande) specs.push(["Demande", cc.typedemande]);
                if (cc.detailsvl) specs.push(["Vehicule", cc.detailsvl]);
                if (cc.immat) specs.push(["Immat", cc.immat]);
                if (cc.decision) specs.push(["Decision", cc.decision]);
              }
              if (specs.length) {
                var specGrid = mkEl("div", "pcorg-popup-spec-grid");
                specs.forEach(function (s) {
                  var lbl = mkEl("span", "pcorg-popup-spec-lbl"); lbl.textContent = s[0];
                  specGrid.appendChild(lbl);
                  var val = mkEl("span", "pcorg-popup-spec-val"); val.textContent = s[1];
                  specGrid.appendChild(val);
                });
                sDiv.appendChild(specGrid);
              }

              cDiv.textContent = "";
              var history = d.comment_history || [];
              if (history.length === 0) {
                cDiv.style.display = "none";
                return;
              }
              history.forEach(function (entry) {
                var isStatus = entry.text && entry.text.indexOf("Statut:") === 0;
                var row = mkEl("div", "pcorg-popup-chrono-entry" + (isStatus ? " status" : ""));
                var dot = mkEl("span", "pcorg-popup-chrono-dot");
                dot.style.background = isStatus ? "#94a3b8" : color;
                row.appendChild(dot);
                var ts = mkEl("span", "pcorg-popup-chrono-ts");
                try {
                  var dt = new Date(entry.ts);
                  ts.textContent = String(dt.getHours()).padStart(2, "0") + ":" + String(dt.getMinutes()).padStart(2, "0");
                } catch (e) { ts.textContent = ""; }
                row.appendChild(ts);
                var op = mkEl("span", "pcorg-popup-chrono-op");
                op.textContent = entry.operator || "";
                row.appendChild(op);
                if (entry.text) {
                  var txt = mkEl("span", "pcorg-popup-chrono-text");
                  txt.textContent = entry.text;
                  row.appendChild(txt);
                }
                if (entry.photo) {
                  var pImg = mkEl("img", "pcorg-popup-chrono-photo");
                  pImg.src = entry.photo;
                  pImg.alt = "Photo";
                  pImg.addEventListener("click", function () { openPhotoLightbox(entry.photo); });
                  row.appendChild(pImg);
                }
                cDiv.appendChild(row);
              });
            })
            .catch(function () { cDiv.textContent = ""; });
        };
      })(item.id, specDiv, chronoDiv, st.color, item.category, item, marker, opVal));
    });
  }

  // ── Photo lightbox ─────────────────────────────────────────────────────
  function openPhotoLightbox(src) {
    var existing = document.getElementById("pcorg-photo-lightbox");
    if (existing) existing.remove();
    var overlay = document.createElement("div");
    overlay.id = "pcorg-photo-lightbox";
    overlay.className = "pcorg-lightbox";
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay || e.target.classList.contains("pcorg-lightbox-close")) {
        overlay.remove();
      }
    });
    var img = document.createElement("img");
    img.src = src;
    img.alt = "Photo terrain";
    overlay.appendChild(img);
    var closeBtn = document.createElement("button");
    closeBtn.className = "pcorg-lightbox-close";
    closeBtn.innerHTML = "<span class='material-symbols-outlined'>close</span>";
    closeBtn.addEventListener("click", function () { overlay.remove(); });
    overlay.appendChild(closeBtn);
    document.body.appendChild(overlay);
  }

  function flyToPin(lat, lon) {
    var map = getMap();
    if (!map) return;
    if (window.CockpitMapView && window.CockpitMapView.currentView() !== "map") {
      window.CockpitMapView.switchView("map");
    }
    setTimeout(function () {
      var targetPoint = map.project([lat, lon], 17);
      targetPoint.y -= 100;
      var targetLatLng = map.unproject(targetPoint, 17);
      map.flyTo(targetLatLng, 17, { duration: 0.8 });
    }, 300);
  }

  // ── Create modal (wizard 3 etapes) ─────────────────────────────────────────
  var createModal, createOverlay, createMiniMap;
  var createLat = null, createLon = null;
  var createSelectedCat = "";
  var createStep = 1;
  var createMarker = null;
  var createGridLayer = null;
  var createGrid25Layer = null;
  var createGridData = null;
  var createGridMeta = null;
  var createAreaDesc = "";
  var createGrid100On = false;
  var createGrid25On = false;
  var createCarroye = "";

  // Listes de reference chargees depuis la config
  var pcorgConfig = { sous_classifications: {}, intervenants: [], services: [], fiche_simplifiee: {}, urgence_categories: {} };
  var vehiclesByCategory = {};
  var createPendingPatrouille = "";

  function extractLabels(items) {
    if (!items || !items.length) return [];
    return items.map(function (it) { return (typeof it === "object") ? it.label : it; });
  }

  function loadPcorgConfig() {
    fetch("/api/pcorg-config")
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d) pcorgConfig = d; })
      .catch(function () {});
  }

  function loadVehiclesByCategory() {
    var ev = window.selectedEvent, yr = window.selectedYear;
    var url = "/anoloc/vehicles-by-category";
    if (ev && yr) {
      url += "?event=" + encodeURIComponent(ev) + "&year=" + encodeURIComponent(yr);
    }
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d) vehiclesByCategory = d; })
      .catch(function () {});
  }

  function showCreate() { createModal.classList.add("show"); createOverlay.classList.add("show"); }
  function hideCreate() {
    createModal.classList.remove("show"); createOverlay.classList.remove("show");
    destroyCreateMap();
    createStep = 1;
    createLat = null;
    createLon = null;
    createSelectedCat = "";
    createCarroye = "";
    createGrid100On = false;
    createGrid25On = false;
  }

  function destroyCreateMap() {
    if (createMiniMap) { createMiniMap.remove(); createMiniMap = null; }
    createMarker = null;
    createGridLayer = null;
    createGrid25Layer = null;
    createGridMeta = null;
  }

  function initCreateModal() {
    createModal = document.getElementById("pcorgCreateModal");
    createOverlay = document.getElementById("pcorgCreateOverlay");
    var btn = document.getElementById("pcorg-add-btn");
    var closeBtn = document.getElementById("pcorgCreateClose");
    var cancelBtn = document.getElementById("pcorgCreateCancel");
    var nextBtn = document.getElementById("pcorgCreateNext");
    var prevBtn = document.getElementById("pcorgCreatePrev");
    var form = document.getElementById("pcorgCreateForm");
    var radioCheck = document.getElementById("pcorg-c-radio");
    var telCheck = document.getElementById("pcorg-c-tel");
    var radioCanal = document.getElementById("pcorg-c-radio-canal");

    if (!createModal || !btn) return;

    closeBtn.addEventListener("click", hideCreate);
    cancelBtn.addEventListener("click", hideCreate);
    createOverlay.addEventListener("click", hideCreate);

    radioCheck.addEventListener("change", function () {
      radioCanal.style.display = radioCheck.checked ? "" : "none";
    });
    telCheck.addEventListener("change", function () {
      if (telCheck.checked) radioCanal.style.display = "none";
    });

    // Click "+" -> ouvrir la modale directement a l'etape 1
    btn.addEventListener("click", function () {
      resetCreateWizard();
      showCreate();
      goToStep(1);
      initCreateMap();
    });

    // Navigation
    nextBtn.addEventListener("click", function () {
      if (createStep === 1) {
        if (createLat === null) {
          showToast("warning", "Positionnez l'intervention sur la carte");
          return;
        }
        goToStep(2);
      } else if (createStep === 2) {
        if (!createSelectedCat) {
          showToast("warning", "Selectionnez une categorie");
          return;
        }
        var text = document.getElementById("pcorg-c-text").value.trim();
        if (!text) {
          showToast("warning", "La description est obligatoire");
          return;
        }
        var isInfoOrMC = createSelectedCat === "PCO.Information" || createSelectedCat === "PCO.MainCourante";
        if (!isInfoOrMC) {
          var appelant = document.getElementById("pcorg-c-appelant").value.trim();
          if (!appelant) {
            showToast("warning", "L'appelant est obligatoire");
            return;
          }
          var telOk = document.getElementById("pcorg-c-tel").checked;
          var radioOk = document.getElementById("pcorg-c-radio").checked;
          if (!telOk && !radioOk) {
            showToast("warning", "Selectionnez un mode de contact (telephone ou radio)");
            return;
          }
        }
        goToStep(3);
      }
    });

    prevBtn.addEventListener("click", function () {
      if (createStep > 1) goToStep(createStep - 1);
    });

    // Tile switcher
    document.getElementById("pcorg-create-tile").addEventListener("click", function () {
      if (!createMiniMap) return;
      if (createTileCurrent === "osm") {
        createMiniMap.removeLayer(createTileOSM);
        createTileSatEGIS.addTo(createMiniMap);
        createTileCurrent = "sat-egis";
      } else if (createTileCurrent === "sat-egis") {
        createMiniMap.removeLayer(createTileSatEGIS);
        createTileSatACO.addTo(createMiniMap);
        createTileCurrent = "sat-aco";
      } else {
        createMiniMap.removeLayer(createTileSatACO);
        createTileOSM.addTo(createMiniMap);
        createTileCurrent = "osm";
      }
    });

    // Grid toggles
    document.getElementById("pcorg-create-grid100").addEventListener("click", function () {
      toggleCreateGrid100();
    });
    document.getElementById("pcorg-create-grid25").addEventListener("click", function () {
      toggleCreateGrid25();
    });

    buildCatButtons();

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      submitCreate();
    });
  }

  function resetCreateWizard() {
    createLat = null;
    createLon = null;
    createSelectedCat = "";
    createSelectedUrgency = "";
    createPendingPatrouille = "";
    createCarroye = "";
    createAreaDesc = "";
    createGrid100On = false;
    createGrid25On = false;
    createStep = 1;
    // Reset header
    var header = document.getElementById("pcorg-create-header");
    header.style.background = "var(--brand)";
    header.querySelector(".pcorg-fiche-icon").textContent = "add_circle";
    header.querySelector(".pcorg-fiche-cat").textContent = "Nouvelle intervention";
    document.getElementById("pcorg-create-pos-label").textContent = "";
    // Reset form
    document.getElementById("pcorgCreateForm").reset();
    document.querySelectorAll(".pcorg-create-cat-btn").forEach(function (b) {
      b.classList.remove("selected"); b.style.borderColor = ""; b.style.background = "";
    });
    document.getElementById("pcorg-create-specific").textContent = "";
    document.getElementById("pcorg-c-radio-canal").style.display = "none";
    // Reset required stars to visible
    document.querySelectorAll(".pcorg-required-star").forEach(function (s) { s.style.display = ""; });
    document.getElementById("pcorg-create-pos-row").style.display = "none";
    document.getElementById("pcorg-create-map-info").style.display = "";
    document.getElementById("pcorg-create-grid25").style.display = "none";
    document.getElementById("pcorg-create-grid100").classList.remove("active");
    document.getElementById("pcorg-create-grid25").classList.remove("active");
  }

  function goToStep(step) {
    createStep = step;
    // Update step visibility
    document.querySelectorAll(".pcorg-create-step").forEach(function (el) {
      el.classList.toggle("active", el.getAttribute("data-step") === String(step));
    });
    // Update stepper
    document.querySelectorAll(".pcorg-step").forEach(function (el) {
      var s = parseInt(el.getAttribute("data-step"), 10);
      el.classList.toggle("active", s === step);
      el.classList.toggle("done", s < step);
    });
    // Update step lines
    var lines = document.querySelectorAll(".pcorg-step-line");
    if (lines[0]) lines[0].classList.toggle("done", step > 1);
    if (lines[1]) lines[1].classList.toggle("done", step > 2);
    // Update nav buttons
    document.getElementById("pcorgCreatePrev").style.display = step > 1 ? "" : "none";
    document.getElementById("pcorgCreateNext").style.display = step < 3 ? "" : "none";
    document.getElementById("pcorgCreateSubmit").style.display = step === 3 ? "" : "none";
    // Resize map if going back to step 1
    if (step === 1 && createMiniMap) {
      setTimeout(function () { createMiniMap.invalidateSize(); }, 100);
    }
  }

  var createTileOSM = null, createTileSatEGIS = null, createTileSatACO = null;
  var createTileCurrent = "osm";

  function initCreateMap() {
    var mapDiv = document.getElementById("pcorg-create-minimap");
    destroyCreateMap();
    createTileCurrent = "osm";
    setTimeout(function () {
      var mainMap = getMap();
      var center = mainMap ? mainMap.getCenter() : L.latLng(47.95, 0.22);
      var zoom = mainMap ? Math.max(mainMap.getZoom(), 15) : 16;

      createMiniMap = L.map(mapDiv, {
        center: center, zoom: zoom,
        zoomControl: true, attributionControl: false,
        dragging: true, scrollWheelZoom: true,
        doubleClickZoom: false, touchZoom: true
      });
      createTileOSM = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxNativeZoom: 19, maxZoom: 22
      }).addTo(createMiniMap);
      createTileSatEGIS = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
        maxNativeZoom: 19, maxZoom: 22
      });
      createTileSatACO = L.tileLayer("/tiles/{z}/{x}/{y}.png", {
        tms: true, maxZoom: 22
      });
      createMiniMap.invalidateSize();

      // Click to position
      createMiniMap.on("click", function (e) {
        setCreatePosition(e.latlng.lat, e.latlng.lng);
      });

      // Zoom change: show/hide 25m button
      createMiniMap.on("zoomend", function () {
        var btn25 = document.getElementById("pcorg-create-grid25");
        if (createGrid100On && createMiniMap.getZoom() >= 18) {
          btn25.style.display = "";
        } else {
          btn25.style.display = "none";
          if (createGrid25On) { clearCreateGrid25(); createGrid25On = false; btn25.classList.remove("active"); }
        }
      });

      // Auto-load grid silently for carroyage resolution
      loadCreateGridSilent();
    }, 150);
  }

  function loadCreateGridSilent() {
    var doLoad = function (data) {
      createGridData = data;
      if (!data || !data.lines) return;
      var lines = data.lines;
      var numCols = lines.num_cols || (lines.v_lines || []).length - 1;
      var numRows = lines.num_rows || (lines.h_lines || []).length - 1;
      var colOffset = lines.col_offset || 0;
      var rowOffset = lines.row_offset || 0;
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
      createGridMeta = {
        cols: cols, rows: rows,
        hLines: lines.h_lines || [], vLines: lines.v_lines || [],
        numCols: numCols, numRows: numRows,
        colOffset: colOffset, rowOffset: rowOffset
      };
    };
    if (window.CockpitMapView && window.CockpitMapView.getGridData && window.CockpitMapView.getGridData()) {
      doLoad(window.CockpitMapView.getGridData());
    } else {
      fetch("/api/grid-ref")
        .then(function (r) { return r.json(); })
        .then(doLoad)
        .catch(function () {});
    }
  }

  function setCreatePosition(lat, lon) {
    createLat = lat;
    createLon = lon;

    // Update or create marker
    if (createMarker) {
      createMarker.setLatLng([lat, lon]);
    } else {
      createMarker = L.marker([lat, lon], {
        icon: L.divIcon({
          className: "",
          html: "<div class='pcorg-pin' style='background:var(--brand)'><span class='material-symbols-outlined'>add_location</span></div>",
          iconSize: [36, 36], iconAnchor: [18, 36]
        })
      }).addTo(createMiniMap);
    }

    // Display coordinates
    document.getElementById("pcorg-create-lat-display").textContent = lat.toFixed(6);
    document.getElementById("pcorg-create-lon-display").textContent = lon.toFixed(6);
    document.getElementById("pcorg-create-pos-row").style.display = "";
    document.getElementById("pcorg-create-map-info").style.display = "none";

    // Resolve carroyage
    createCarroye = "";
    var carrEl = document.getElementById("pcorg-create-carroye-display");
    if (createGridMeta) {
      var label = resolveCreateGridCell(lat, lon);
      if (label) {
        createCarroye = label;
        carrEl.textContent = label;
      } else {
        carrEl.textContent = "Hors zone";
      }
    } else if (window.CockpitMapView && window.CockpitMapView.getCellLabel) {
      var mainLabel = window.CockpitMapView.getCellLabel(lat, lon);
      if (mainLabel) {
        createCarroye = mainLabel;
        carrEl.textContent = mainLabel;
      } else {
        carrEl.textContent = "--";
      }
    } else {
      carrEl.textContent = "--";
    }

    // Resolve zone from POI polygons
    createAreaDesc = "";
    var zoneItem = document.getElementById("pcorg-create-zone-item");
    var zoneDisp = document.getElementById("pcorg-create-zone-display");
    if (window.CockpitMapView && window.CockpitMapView.findZoneAtPoint) {
      var zoneName = window.CockpitMapView.findZoneAtPoint(lat, lon);
      if (zoneName) createAreaDesc = zoneName;
    }
    if (zoneItem && zoneDisp) {
      if (createAreaDesc) {
        zoneDisp.textContent = createAreaDesc;
        zoneItem.style.display = "";
      } else {
        zoneItem.style.display = "none";
      }
    }

    // Update header
    var posParts = [];
    if (createAreaDesc) posParts.push(createAreaDesc);
    if (createCarroye) posParts.push(createCarroye);
    posParts.push(lat.toFixed(5) + ", " + lon.toFixed(5));
    document.getElementById("pcorg-create-pos-label").textContent = posParts.join(" - ");
  }

  function resolveCreateGridCell(lat, lon) {
    if (!createGridMeta) return null;
    var m = createGridMeta;
    var col = null, row = null;
    for (var ci = 0; ci < m.numCols; ci++) {
      if (lon >= m.vLines[ci].lng && lon < m.vLines[ci + 1].lng) { col = ci; break; }
    }
    for (var ri = 0; ri < m.numRows; ri++) {
      if (lat <= m.hLines[ri].lat && lat > m.hLines[ri + 1].lat) { row = ri; break; }
    }
    if (col === null || row === null) return null;
    var colLbl = m.cols[col];
    var rowLbl = m.rows[row];
    if (!colLbl || !rowLbl) return null;
    return colLbl + "" + rowLbl;
  }

  // ── Grid on create map ──────────────────────────────────────────────────────
  function colLabel(idx) {
    if (idx < 26) return String.fromCharCode(65 + idx);
    return String.fromCharCode(65 + Math.floor(idx / 26) - 1) + String.fromCharCode(65 + (idx % 26));
  }

  function toggleCreateGrid100() {
    createGrid100On = !createGrid100On;
    document.getElementById("pcorg-create-grid100").classList.toggle("active", createGrid100On);
    if (createGrid100On) {
      renderCreateGrid100();
    } else {
      clearCreateGrid100();
      clearCreateGrid25();
      createGrid25On = false;
      document.getElementById("pcorg-create-grid25").classList.remove("active");
      document.getElementById("pcorg-create-grid25").style.display = "none";
    }
  }

  function renderCreateGrid100() {
    if (!createMiniMap || !createGridData || !createGridData.lines) return;
    var lines = createGridData.lines;
    var hLines = lines.h_lines || [];
    var vLines = lines.v_lines || [];

    createGridLayer = L.layerGroup().addTo(createMiniMap);
    hLines.forEach(function (l) {
      L.polyline([[l.lat, l.lng_start], [l.lat, l.lng_end]],
        { color: "#f59e0b", weight: 1, opacity: 0.6, interactive: false }
      ).addTo(createGridLayer);
    });
    vLines.forEach(function (l) {
      L.polyline([[l.lat_start, l.lng], [l.lat_end, l.lng]],
        { color: "#f59e0b", weight: 1, opacity: 0.6, interactive: false }
      ).addTo(createGridLayer);
    });

    var numCols = createGridMeta ? createGridMeta.numCols : (lines.num_cols || vLines.length - 1);
    var numRows = createGridMeta ? createGridMeta.numRows : (lines.num_rows || hLines.length - 1);
    var colOff = lines.col_offset || 0;
    var rowOff = lines.row_offset || 0;
    var labeledBounds = L.latLngBounds(
      [hLines[numRows].lat, vLines[colOff > 0 ? colOff : 0].lng],
      [hLines[rowOff > 0 ? rowOff : 0].lat, vLines[numCols].lng]
    );
    createMiniMap.fitBounds(labeledBounds, { padding: [20, 20] });

    if (createMiniMap.getZoom() >= 18) {
      document.getElementById("pcorg-create-grid25").style.display = "";
    }
  }

  function clearCreateGrid100() {
    if (createGridLayer && createMiniMap) {
      createMiniMap.removeLayer(createGridLayer);
      createGridLayer = null;
    }
    createGridMeta = null;
  }

  function toggleCreateGrid25() {
    createGrid25On = !createGrid25On;
    document.getElementById("pcorg-create-grid25").classList.toggle("active", createGrid25On);
    if (createGrid25On) {
      renderCreateGrid25();
    } else {
      clearCreateGrid25();
    }
  }

  function renderCreateGrid25() {
    if (!createMiniMap || !createGridData || !createGridData.lines) return;
    var lines25 = createGridData.lines_25;
    if (!lines25) return;
    clearCreateGrid25();
    createGrid25Layer = L.layerGroup().addTo(createMiniMap);
    (lines25.h_lines || []).forEach(function (l) {
      L.polyline([[l.lat, l.lng_start], [l.lat, l.lng_end]],
        { color: "#fb923c", weight: 1, opacity: 0.7, dashArray: "6 4", interactive: false }
      ).addTo(createGrid25Layer);
    });
    (lines25.v_lines || []).forEach(function (l) {
      L.polyline([[l.lat_start, l.lng], [l.lat_end, l.lng]],
        { color: "#fb923c", weight: 1, opacity: 0.7, dashArray: "6 4", interactive: false }
      ).addTo(createGrid25Layer);
    });
  }

  function clearCreateGrid25() {
    if (createGrid25Layer && createMiniMap) {
      createMiniMap.removeLayer(createGrid25Layer);
      createGrid25Layer = null;
    }
  }

  function buildCatButtons() {
    var container = document.getElementById("pcorg-create-cats");
    if (!container) return;
    CATEGORY_ORDER.forEach(function (cat) {
      var st = catStyle(cat);
      var btn = mkEl("button", "pcorg-create-cat-btn");
      btn.type = "button";
      btn.setAttribute("data-cat", cat);
      var ico = matIcon(st.icon);
      ico.style.color = st.color;
      btn.appendChild(ico);
      var label = mkEl("span", "");
      label.textContent = shortCat(cat);
      btn.appendChild(label);
      btn.addEventListener("click", function () {
        selectCategory(cat);
      });
      container.appendChild(btn);
    });
  }

  function selectCategory(cat) {
    createSelectedCat = cat;
    var st = catStyle(cat);
    document.querySelectorAll(".pcorg-create-cat-btn").forEach(function (b) {
      var isSel = b.getAttribute("data-cat") === cat;
      b.classList.toggle("selected", isSel);
      b.style.borderColor = isSel ? st.color : "";
      b.style.background = isSel ? st.color : "";
    });
    // Show/hide appelant & contact required stars for Information / MainCourante
    var isInfoOrMC = cat === "PCO.Information" || cat === "PCO.MainCourante";
    var appelantGroup = document.getElementById("pcorg-c-appelant-group");
    var contactGroup = document.getElementById("pcorg-c-contact-group");
    if (appelantGroup) {
      var star = appelantGroup.querySelector(".pcorg-required-star");
      if (star) star.style.display = isInfoOrMC ? "none" : "";
    }
    if (contactGroup) {
      var star2 = contactGroup.querySelector(".pcorg-required-star");
      if (star2) star2.style.display = isInfoOrMC ? "none" : "";
    }
    // Update header color
    var header = document.getElementById("pcorg-create-header");
    header.style.background = st.color;
    header.querySelector(".pcorg-fiche-icon").textContent = st.icon;
    header.querySelector(".pcorg-fiche-cat").textContent = "Nouvelle " + shortCat(cat);
    // Update pin color on map
    if (createMarker && createMiniMap) {
      createMarker.setIcon(L.divIcon({
        className: "",
        html: "<div class='pcorg-pin' style='background:" + st.color + "'><span class='material-symbols-outlined'>" + st.icon + "</span></div>",
        iconSize: [36, 36], iconAnchor: [18, 36]
      }));
    }
    // Build specific fields for step 3
    buildSpecificCreateFields(cat);
  }

  var createSelectedUrgency = "";

  function buildSpecificCreateFields(cat) {
    var container = document.getElementById("pcorg-create-specific");
    container.textContent = "";

    var urgCats = (pcorgConfig && pcorgConfig.urgence_categories) || {};
    var subs = extractLabels((pcorgConfig.sous_classifications || {})[cat]);
    var intervList = extractLabels(pcorgConfig.intervenants);
    var serviceList = extractLabels(pcorgConfig.services);
    var vehicles = vehiclesByCategory[cat];
    var urgRow; // shared by appendUrgency / updateUrgencyCreateBtns

    function appendUrgency() {
      if (!urgCats[cat]) { createSelectedUrgency = ""; return; }
      var presetUrgency = createSelectedUrgency || "";
      var urgGrp = mkEl("div", "form-group");
      var urgLbl = mkEl("label", ""); urgLbl.textContent = "Niveau d'urgence";
      urgGrp.appendChild(urgLbl);
      urgRow = mkEl("div", "pcorg-create-cats");
      urgRow.id = "pcorg-create-urgency-btns";
      var uType = urgencyType(cat);
      var noneBtnC = mkEl("button", "pcorg-create-cat-btn" + (!presetUrgency ? " selected" : ""));
      noneBtnC.type = "button";
      noneBtnC.style.cssText = !presetUrgency ? "border-color:var(--muted);background:var(--muted);font-size:0.75rem" : "font-size:0.75rem";
      noneBtnC.setAttribute("data-urgency", "");
      noneBtnC.textContent = "Aucun";
      noneBtnC.addEventListener("click", function () {
        createSelectedUrgency = "";
        updateUrgBtns();
      });
      urgRow.appendChild(noneBtnC);
      URGENCY_LEVELS.forEach(function (lvl) {
        var c = URGENCY_COLORS[lvl];
        var isSel = presetUrgency === lvl;
        var btnU = mkEl("button", "pcorg-create-cat-btn" + (isSel ? " selected" : ""));
        btnU.type = "button";
        btnU.setAttribute("data-urgency", lvl);
        if (isSel) { btnU.style.borderColor = c; btnU.style.background = c; }
        var dotU = mkEl("span", ""); dotU.style.cssText = "width:8px;height:8px;border-radius:50%;background:" + c;
        btnU.appendChild(dotU);
        var lblU = mkEl("span", ""); lblU.style.fontSize = "0.72rem";
        lblU.textContent = URGENCY_LABELS[uType][lvl];
        btnU.appendChild(lblU);
        btnU.addEventListener("click", function () {
          createSelectedUrgency = lvl;
          updateUrgBtns();
        });
        urgRow.appendChild(btnU);
      });
      urgGrp.appendChild(urgRow);
      container.appendChild(urgGrp);
      function updateUrgBtns() {
        urgRow.querySelectorAll(".pcorg-create-cat-btn").forEach(function (b) {
          var val = b.getAttribute("data-urgency");
          var sel = val === createSelectedUrgency;
          b.classList.toggle("selected", sel);
          if (val) {
            b.style.borderColor = sel ? URGENCY_COLORS[val] : "";
            b.style.background = sel ? URGENCY_COLORS[val] : "";
          } else {
            b.style.borderColor = sel ? "var(--muted)" : "";
            b.style.background = sel ? "var(--muted)" : "";
          }
        });
      }
    }

    function appendComment() {
      var sec = mkEl("div", "pcorg-fiche-section"); sec.textContent = "Action prise";
      container.appendChild(sec);
      var grp = mkEl("div", "form-group");
      var lbl = mkEl("label", ""); lbl.setAttribute("for", "pcorg-c-comment");
      lbl.textContent = "Consignez l'action ou l'observation ";
      var star = mkEl("span", ""); star.style.color = "var(--danger,#ef4444)"; star.textContent = "*";
      lbl.appendChild(star);
      grp.appendChild(lbl);
      var ta = mkEl("textarea", "form-input"); ta.id = "pcorg-c-comment"; ta.rows = 3;
      ta.required = true; ta.placeholder = "Action realisee, observation, consigne...";
      grp.appendChild(ta);
      container.appendChild(grp);
    }

    function appendSousClassification() {
      if (subs.length > 0) {
        addCreateSelect(container, "pcorg-c-sous", "Sous-classification", subs);
      }
    }

    function appendVehicle() {
      if (vehicles && vehicles.length > 0) {
        var vehNames = vehicles.map(function (v) { return v.label; });
        addCreateSelect(container, "pcorg-c-patrouille", "Vehicule engage", vehNames);
        if (createPendingPatrouille) {
          var selEl = document.getElementById("pcorg-c-patrouille");
          if (selEl) selEl.value = createPendingPatrouille;
          createPendingPatrouille = "";
        }
      }
    }

    // === Build fields in category-specific order ===
    if (cat === "PCO.Secours" || cat === "PCO.Securite" || cat === "PCO.Technique") {
      appendSousClassification();
      appendUrgency();
      appendComment();
      appendVehicle();
      if (intervList.length) {
        addCreateSelect(container, "pcorg-c-interv1", "Intervenant 1", intervList);
        addCreateSelect(container, "pcorg-c-interv2", "Intervenant 2", intervList);
      } else {
        addCreateField(container, "pcorg-c-interv1", "Intervenant 1");
        addCreateField(container, "pcorg-c-interv2", "Intervenant 2");
      }
      if (serviceList.length) {
        addCreateSelect(container, "pcorg-c-service", "Service contacte", serviceList);
      } else {
        addCreateField(container, "pcorg-c-service", "Service contacte");
      }
    } else if (cat === "PCO.Flux") {
      appendSousClassification();
      appendUrgency();
      appendComment();
      appendVehicle();
      if (intervList.length) {
        addCreateSelect(container, "pcorg-c-moyens1", "Moyens engages Niv.1", intervList);
        addCreateSelect(container, "pcorg-c-moyens2", "Moyens engages Niv.2", intervList);
      } else {
        addCreateField(container, "pcorg-c-moyens1", "Moyens engages Niv.1");
        addCreateField(container, "pcorg-c-moyens2", "Moyens engages Niv.2");
      }
    } else if (cat === "PCO.Fourriere") {
      addCreateSelect(container, "pcorg-c-typedemande", "Type de demande",
        ["Parking sauvage", "Pas de titre", "Mauvais titre (sticker ou badge)", "Stationnement genant", "Autre"]);
      addCreateField(container, "pcorg-c-lieu", "Lieu");
      addCreateField(container, "pcorg-c-detailsvl", "Vehicule (marque, couleur, modele)");
      addCreateField(container, "pcorg-c-immat", "Immatriculation");
      addCreateSelect(container, "pcorg-c-decision", "Decision",
        ["Remorquage demande", "Sabot pose", "Avertissement", "Annule"]);
      appendComment();
      appendVehicle();
    } else {
      appendSousClassification();
      appendUrgency();
      appendComment();
      appendVehicle();
    }
  }

  function addCreateField(container, id, label) {
    var grp = mkEl("div", "form-group");
    var lbl = mkEl("label", ""); lbl.textContent = label; lbl.setAttribute("for", id);
    grp.appendChild(lbl);
    var inp = mkEl("input", "form-input"); inp.type = "text"; inp.id = id; inp.placeholder = label;
    grp.appendChild(inp);
    container.appendChild(grp);
  }

  function addCreateSelect(container, id, label, options) {
    var grp = mkEl("div", "form-group");
    var lbl = mkEl("label", ""); lbl.textContent = label; lbl.setAttribute("for", id);
    grp.appendChild(lbl);
    var sel = mkEl("select", "form-input"); sel.id = id;
    var opt0 = mkEl("option", ""); opt0.value = ""; opt0.textContent = "-- Choisir --";
    sel.appendChild(opt0);
    options.forEach(function (o) {
      var opt = mkEl("option", ""); opt.value = o; opt.textContent = o;
      sel.appendChild(opt);
    });
    grp.appendChild(sel);
    container.appendChild(grp);
  }

  function submitCreate() {
    // Validate action prise
    var comment = getVal("pcorg-c-comment");
    if (!comment) {
      showToast("warning", "L'action prise est obligatoire");
      return;
    }
    // Validate sous-classification
    var sousEl = document.getElementById("pcorg-c-sous");
    if (sousEl && !sousEl.value) {
      showToast("warning", "La sous-classification est obligatoire");
      return;
    }
    var text = document.getElementById("pcorg-c-text").value.trim();
    var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};

    // Build content_category
    var cc = {};
    var appelant = document.getElementById("pcorg-c-appelant").value.trim();
    if (appelant) cc.appelant = appelant;
    var contactSel = document.querySelector('input[name="pcorg-c-contact"]:checked');
    if (contactSel && contactSel.value === "telephone") {
      cc.telephone = true;
    } else if (contactSel && contactSel.value === "radio") {
      var radioCanal = document.getElementById("pcorg-c-radio-canal");
      cc.radio = radioCanal.value.trim() || true;
    }

    // Carroyage from step 1
    if (createCarroye) cc.carroye = createCarroye;

    // Sous-classification
    var sousEl = document.getElementById("pcorg-c-sous");
    if (sousEl && sousEl.value) cc.sous_classification = sousEl.value;

    // Category-specific
    var cat = createSelectedCat;
    if (cat === "PCO.Secours" || cat === "PCO.Securite" || cat === "PCO.Technique") {
      var i1 = getVal("pcorg-c-interv1"); if (i1) cc.intervenant1 = i1;
      var i2 = getVal("pcorg-c-interv2"); if (i2) cc.intervenant2 = i2;
      var svc = getVal("pcorg-c-service"); if (svc) cc.service_contacte = svc;
    } else if (cat === "PCO.Fourriere") {
      var lieu = getVal("pcorg-c-lieu"); if (lieu) cc.lieu = lieu;
      var vl = getVal("pcorg-c-detailsvl"); if (vl) cc.detailsvl = vl;
      var imm = getVal("pcorg-c-immat"); if (imm) cc.immat = imm;
      var td = getVal("pcorg-c-typedemande"); if (td) cc.typedemande = td;
      var dec = getVal("pcorg-c-decision"); if (dec) cc.decision = dec;
    } else if (cat === "PCO.Flux") {
      var m1 = getVal("pcorg-c-moyens1"); if (m1) cc.moyens_engages_niveau_1 = m1;
      var m2 = getVal("pcorg-c-moyens2"); if (m2) cc.moyens_engages_niveau_2 = m2;
    }

    // Patrouille
    var patr = getVal("pcorg-c-patrouille");
    if (patr) cc.patrouille = patr;

    var payload = {
      event: ey.event,
      year: ey.year,
      category: cat,
      text: text,
      area_desc: createAreaDesc,
      content_category: cc,
      comment: getVal("pcorg-c-comment"),
      niveau_urgence: createSelectedUrgency || null,
      lat: createLat,
      lon: createLon
    };

    apiPost("/api/pcorg/create", payload)
      .then(function (r) {
        if (r.ok) {
          hideCreate();
          showToast("success", "Intervention creee");
          refresh();
        } else {
          showToast("error", r.error || "Erreur");
        }
      });
  }

  function getVal(id) {
    var el = document.getElementById(id);
    return el ? el.value.trim() : "";
  }

  // ── GPS modal ──────────────────────────────────────────────────────────────
  function initGpsModal() {
    var modal = document.getElementById("pcorgGpsModal");
    var closeBtn = document.getElementById("pcorgGpsClose");
    var cancelBtn = document.getElementById("pcorgGpsCancel");
    var saveBtn = document.getElementById("pcorgGpsSave");
    var pickBtn = document.getElementById("pcorg-gps-pick");

    if (!modal) return;

    closeBtn.addEventListener("click", function () { modal.classList.remove("show"); });
    cancelBtn.addEventListener("click", function () { modal.classList.remove("show"); });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.classList.remove("show");
    });

    pickBtn.addEventListener("click", function () {
      modal.classList.remove("show");
      startGpsPick(function (lat, lon) {
        document.getElementById("pcorg-gps-lat").value = lat.toFixed(6);
        document.getElementById("pcorg-gps-lon").value = lon.toFixed(6);
        modal.classList.add("show");
      });
    });

    saveBtn.addEventListener("click", function () {
      var id = document.getElementById("pcorg-gps-id").value;
      var lat = document.getElementById("pcorg-gps-lat").value;
      var lon = document.getElementById("pcorg-gps-lon").value;
      if (!lat || !lon) {
        showToast("warning", "Latitude et longitude requises");
        return;
      }
      apiPost("/api/pcorg/update-gps/" + encodeURIComponent(id), {
        lat: parseFloat(lat),
        lon: parseFloat(lon)
      }).then(function (r) {
        if (r.ok) {
          modal.classList.remove("show");
          showToast("success", "Position enregistree");
          refresh();
        } else {
          showToast("error", r.error || "Erreur");
        }
      });
    });
  }

  function openGpsModal(id) {
    var modal = document.getElementById("pcorgGpsModal");
    if (!modal) return;
    document.getElementById("pcorg-gps-id").value = id;
    document.getElementById("pcorg-gps-lat").value = "";
    document.getElementById("pcorg-gps-lon").value = "";
    modal.classList.add("show");
  }

  // ── GPS pick on map ────────────────────────────────────────────────────────
  function startGpsPick(callback) {
    var map = getMap();
    if (!map) {
      showToast("warning", "Carte non disponible");
      return;
    }
    if (window.CockpitMapView && window.CockpitMapView.currentView() !== "map") {
      window.CockpitMapView.switchView("map");
    }
    pickCallback = callback;
    document.body.classList.add("pcorg-pick-active");

    if (typeof showToast === "function") {
      showToast("Cliquez sur la carte pour placer le point", "info");
    }

    function onMapClick(e) {
      document.body.classList.remove("pcorg-pick-active");
      map.off("click", onMapClick);
      map.off("keydown", onEsc);
      if (pickCallback) {
        pickCallback(e.latlng.lat, e.latlng.lng);
        pickCallback = null;
      }
    }
    function onEsc(e) {
      if (e.originalEvent && e.originalEvent.key === "Escape") {
        document.body.classList.remove("pcorg-pick-active");
        map.off("click", onMapClick);
        map.off("keydown", onEsc);
        pickCallback = null;
      }
    }
    map.on("click", onMapClick);
    map.on("keydown", onEsc);
  }

  // ── Expanded list panel (zone centrale, meme pattern que meteo) ──────────

  var expPanel, expBody, expSearch, expCount;
  var expFilter = "all";
  var _expPreviousView = null;

  function initExpandedPanel() {
    expPanel = document.getElementById("pcorg-expanded-panel");
    expBody = document.getElementById("pcorg-expanded-body");
    expSearch = document.getElementById("pcorg-expanded-search");
    expCount = document.getElementById("pcorg-expanded-count");
    if (!expPanel) return;

    var expandBtn = document.getElementById("pcorg-expand-btn");
    var closeBtn = document.getElementById("pcorg-expanded-close");

    if (expandBtn) expandBtn.addEventListener("click", toggleExpanded);
    if (closeBtn) closeBtn.addEventListener("click", closeExpanded);

    // Filter tabs
    var tabs = expPanel.querySelectorAll(".pcorg-exp-tab");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        expFilter = tab.getAttribute("data-filter");
        renderExpanded();
      });
    });

    // Search
    if (expSearch) {
      expSearch.addEventListener("input", function () { renderExpanded(); });
    }
  }

  function toggleExpanded() {
    if (expPanel && expPanel.style.display !== "none") {
      closeExpanded();
      return;
    }
    openExpanded();
  }

  function openExpanded() {
    if (!expPanel || !lastData) return;
    var timeline = document.getElementById("timeline-main");
    var mapMain = document.getElementById("map-main");
    var meteoPanel = document.getElementById("meteo-panel");

    // Sauvegarder la vue precedente
    if (window.CockpitMapView) {
      _expPreviousView = window.CockpitMapView.currentView();
    } else {
      _expPreviousView = (mapMain && mapMain.style.display !== "none") ? "map" : "timeline";
    }

    // Fermer le panel meteo s'il est ouvert
    if (meteoPanel && meteoPanel.style.display !== "none" && window.MeteoPanel) {
      window.MeteoPanel.collapse();
    }

    if (timeline) timeline.style.display = "none";
    if (mapMain) mapMain.style.display = "none";
    expPanel.style.display = "flex";

    var expandBtn = document.getElementById("pcorg-expand-btn");
    if (expandBtn) expandBtn.querySelector(".material-symbols-outlined").textContent = "close_fullscreen";

    if (expSearch) expSearch.value = "";
    expFilter = "all";
    var tabs = expPanel.querySelectorAll(".pcorg-exp-tab");
    tabs.forEach(function (t) { t.classList.toggle("active", t.getAttribute("data-filter") === "all"); });
    renderExpanded();
    setTimeout(function () { if (expSearch) expSearch.focus(); }, 100);
  }

  function closeExpanded() {
    if (expPanel) expPanel.style.display = "none";
    var expandBtn = document.getElementById("pcorg-expand-btn");
    if (expandBtn) expandBtn.querySelector(".material-symbols-outlined").textContent = "open_in_full";
    var timeline = document.getElementById("timeline-main");
    var mapMain = document.getElementById("map-main");

    if (_expPreviousView === "map") {
      if (timeline) timeline.style.display = "none";
      if (mapMain) mapMain.style.display = "block";
    } else {
      if (timeline) timeline.style.display = "";
      if (mapMain) mapMain.style.display = "none";
    }
  }

  function renderExpanded() {
    if (!expBody || !lastData) return;
    var openItems = (lastData.open || []).map(function (it) { it._open = true; return it; });
    var closedItems = (lastData.closed || []).map(function (it) { it._open = false; return it; });

    var items;
    if (expFilter === "open") items = openItems;
    else if (expFilter === "closed") items = closedItems;
    else items = openItems.concat(closedItems);

    // Search filter
    var q = (expSearch ? expSearch.value : "").toLowerCase().trim();
    if (q) {
      items = items.filter(function (it) {
        return (it.text || "").toLowerCase().indexOf(q) !== -1
          || (it.category || "").toLowerCase().indexOf(q) !== -1
          || (it.area_desc || "").toLowerCase().indexOf(q) !== -1
          || (it.operator || "").toLowerCase().indexOf(q) !== -1
          || (it.sous_classification || "").toLowerCase().indexOf(q) !== -1;
      });
    }

    expBody.textContent = "";

    if (items.length === 0) {
      var empty = mkEl("div", "widget-placeholder");
      empty.style.padding = "60px 0";
      var emptyIco = matIcon("search_off", "");
      emptyIco.style.fontSize = "40px";
      empty.appendChild(emptyIco);
      var emptyTxt = mkEl("span", "");
      emptyTxt.textContent = q ? "Aucun resultat pour \"" + q + "\"" : "Aucune intervention";
      empty.appendChild(emptyTxt);
      expBody.appendChild(empty);
      if (expCount) expCount.textContent = "";
      return;
    }

    var table = mkEl("table", "pcorg-exp-table");
    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    ["Statut", "Categorie", "Description", "Operateur", "Ouverture", "Cloture"].forEach(function (h) {
      var th = document.createElement("th");
      th.textContent = h;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    items.forEach(function (item) {
      var st = catStyle(item.category);
      var tr = document.createElement("tr");

      // Statut
      var tdStatus = document.createElement("td");
      var statusBadge = mkEl("span", "pcorg-exp-status " + (item._open ? "open" : "closed"));
      statusBadge.textContent = item._open ? "En cours" : "Termin\u00e9e";
      tdStatus.appendChild(statusBadge);
      tr.appendChild(tdStatus);

      // Categorie
      var tdCat = document.createElement("td");
      var catEl = mkEl("span", "pcorg-exp-cat");
      catEl.style.color = st.color;
      var catIco = matIcon(st.icon, "");
      catIco.style.color = st.color;
      catEl.appendChild(catIco);
      var catTxt = document.createTextNode(shortCat(item.category));
      catEl.appendChild(catTxt);
      tdCat.appendChild(catEl);
      if (item.sous_classification) {
        var scEl = mkEl("div", "");
        scEl.style.fontSize = "0.65rem";
        scEl.style.color = "var(--muted)";
        scEl.textContent = item.sous_classification;
        tdCat.appendChild(scEl);
      }
      if (item.niveau_urgence) {
        var urgWrapExp = mkEl("div", "");
        urgWrapExp.style.marginTop = "2px";
        var urgBadgeExp = mkEl("span", "pcorg-urgency-badge pcorg-urgency-" + item.niveau_urgence);
        urgBadgeExp.style.marginLeft = "0";
        urgBadgeExp.textContent = urgencyLabel(item.category, item.niveau_urgence);
        urgWrapExp.appendChild(urgBadgeExp);
        tdCat.appendChild(urgWrapExp);
      }
      tr.appendChild(tdCat);

      // Description
      var tdDesc = document.createElement("td");
      var descEl = mkEl("span", "pcorg-exp-desc");
      descEl.textContent = item.text || "(sans description)";
      descEl.title = item.text || "";
      tdDesc.appendChild(descEl);
      tr.appendChild(tdDesc);

      // Operateur
      var tdOp = document.createElement("td");
      var opEl = mkEl("span", "pcorg-exp-operator");
      opEl.textContent = item.operator || "";
      tdOp.appendChild(opEl);
      tr.appendChild(tdOp);

      // Ouverture
      var tdOpen = document.createElement("td");
      var openEl = mkEl("span", "pcorg-exp-time");
      if (item.ts) {
        var dOpen = new Date(item.ts);
        openEl.textContent = String(dOpen.getDate()).padStart(2, "0") + "/" +
          String(dOpen.getMonth() + 1).padStart(2, "0") + " " +
          String(dOpen.getHours()).padStart(2, "0") + ":" +
          String(dOpen.getMinutes()).padStart(2, "0");
      }
      tdOpen.appendChild(openEl);
      tr.appendChild(tdOpen);

      // Cloture
      var tdClose = document.createElement("td");
      var closeEl = mkEl("span", "pcorg-exp-time");
      if (item.close_ts) {
        var dClose = new Date(item.close_ts);
        closeEl.textContent = String(dClose.getDate()).padStart(2, "0") + "/" +
          String(dClose.getMonth() + 1).padStart(2, "0") + " " +
          String(dClose.getHours()).padStart(2, "0") + ":" +
          String(dClose.getMinutes()).padStart(2, "0");
      } else {
        closeEl.textContent = "-";
        closeEl.style.color = "var(--muted)";
      }
      tdClose.appendChild(closeEl);
      tr.appendChild(tdClose);

      // Click -> open detail
      tr.addEventListener("click", (function (id, closed) {
        return function () { openDetailModal(id, closed); };
      })(item.id, !item._open));

      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    expBody.appendChild(table);

    // Count
    var openCount = items.filter(function (it) { return it._open; }).length;
    var closedCount = items.filter(function (it) { return !it._open; }).length;
    if (expCount) {
      expCount.textContent = items.length + " intervention" + (items.length > 1 ? "s" : "") +
        " - " + openCount + " en cours, " + closedCount + " terminee" + (closedCount > 1 ? "s" : "");
    }
  }

  // ── Public API (pour alert_poller) ──────────────────────────────────────────
  window.PcorgUI = { openFiche: function(id) { openDetailModal(id, false); } };

  // ── Bootstrap ──────────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    init();
    initExpandedPanel();
  });
})();
