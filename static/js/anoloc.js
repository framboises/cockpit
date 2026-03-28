/**
 * anoloc.js - Module frontend Anoloc GPS
 * Gere le panneau Ressources (widget-right-4) et les marqueurs sur la carte Leaflet.
 * Lit les positions depuis /anoloc/live (MongoDB, alimente par anoloc_collector.py).
 */
(function () {
  "use strict";

  // --- State ---
  var anolocLayers = {};      // beaconGroupId -> L.layerGroup
  var anolocMarkers = {};     // deviceId -> L.marker
  var anolocVisible = false;
  var groupToggles = {};      // beaconGroupId -> boolean (visible on map)
  var refreshTimer = null;
  var REFRESH_MS = 15000;     // 15s
  var lastData = null;

  // --- DOM helpers ---
  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "className") e.className = attrs[k];
        else if (k === "textContent") e.textContent = attrs[k];
        else if (k.indexOf("on") === 0) e.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
        else e.setAttribute(k, attrs[k]);
      });
    }
    if (children) {
      (Array.isArray(children) ? children : [children]).forEach(function (c) {
        if (typeof c === "string") e.appendChild(document.createTextNode(c));
        else if (c) e.appendChild(c);
      });
    }
    return e;
  }

  function materialIcon(name, style) {
    var span = el("span", {className: "material-symbols-outlined"});
    span.textContent = name;
    if (style) span.style.cssText = style;
    return span;
  }

  // --- Init ---
  function init() {
    if (!window.isBlockAllowed || !window.isBlockAllowed("widget-right-4")) return;
    buildPanel();
    refresh();
    refreshTimer = setInterval(refresh, REFRESH_MS);

    // Observer le switch vers la carte pour injecter les markers
    var mapMain = document.getElementById("map-main");
    if (mapMain) {
      var observer = new MutationObserver(function () {
        var mapVisible = mapMain.style.display !== "none";
        if (mapVisible && lastData && anolocVisible) {
          setTimeout(function () { updateMarkers(lastData); }, 200);
        }
        // Desactiver le bouton carte quand on quitte la vue carte
        if (!mapVisible && anolocVisible) {
          anolocVisible = false;
          updateToggleBtnState();
        }
      });
      observer.observe(mapMain, { attributes: true, attributeFilter: ["style"] });
    }
  }

  // --- Panel Ressources (widget-right-4-body) ---
  function buildPanel() {
    var body = document.getElementById("widget-right-4-body");
    if (!body) return;
    body.textContent = "";

    // Header
    var dot = el("span", {className: "anoloc-status-dot", id: "anoloc-dot"});
    var countSpan = el("span", {id: "anoloc-count", textContent: "Chargement..."});
    var statusLine = el("div", {className: "anoloc-status-line"}, [dot, countSpan]);

    var toggleBtn = el("button", {className: "btn-icon anoloc-btn", id: "anoloc-toggle-map", title: "Afficher/masquer sur la carte"}, [
      materialIcon("map"),
    ]);
    var refreshBtn = el("button", {className: "btn-icon anoloc-btn", id: "anoloc-refresh", title: "Rafraichir"}, [
      materialIcon("refresh"),
    ]);
    var actions = el("div", {className: "anoloc-panel-actions"}, [toggleBtn, refreshBtn]);
    var header = el("div", {className: "anoloc-panel-header"}, [statusLine, actions]);
    body.appendChild(header);

    // Container groupes
    var groupList = el("div", {id: "anoloc-group-list"});
    body.appendChild(groupList);

    // Events
    toggleBtn.addEventListener("click", function () {
      // Basculer vers la carte si on est en timeline
      var wasTimeline = window.CockpitMapView && window.CockpitMapView.currentView() !== "map";
      if (wasTimeline && window.CockpitMapView.switchView) {
        window.CockpitMapView.switchView("map");
      }
      anolocVisible = !anolocVisible;
      toggleBtn.classList.toggle("active", anolocVisible);
      // Petit delai pour laisser la carte s'initialiser si on vient de switcher
      if (wasTimeline && anolocVisible) {
        setTimeout(function () {
          if (lastData) updateMarkers(lastData);
          applyVisibility();
        }, 300);
      } else {
        if (anolocVisible && lastData) updateMarkers(lastData);
        applyVisibility();
      }
    });
    refreshBtn.addEventListener("click", function () {
      refresh();
    });
  }

  // --- Refresh: fetch /anoloc/live ---
  function refresh() {
    fetch("/anoloc/live")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.enabled) {
          showDisabled();
          return;
        }
        lastData = data;
        updatePanel(data);
        updateMarkers(data);
      })
      .catch(function (err) {
        console.error("[Anoloc] refresh error:", err);
      });
  }

  function showDisabled() {
    var body = document.getElementById("widget-right-4-body");
    if (!body) return;
    body.textContent = "";
    var placeholder = el("div", {className: "widget-placeholder"}, [
      materialIcon("satellite_alt"),
      el("span", {textContent: "GPS non configure"}),
    ]);
    body.appendChild(placeholder);
  }

  // --- Update panel ---
  function updatePanel(data) {
    var groups = data.groups || {};
    var totalOnline = 0;
    var totalAll = 0;

    var groupList = document.getElementById("anoloc-group-list");
    if (!groupList) return;
    groupList.textContent = "";

    var groupIds = Object.keys(groups);
    groupIds.sort(function (a, b) {
      return (groups[a].label || "").localeCompare(groups[b].label || "");
    });

    groupIds.forEach(function (gid) {
      var grp = groups[gid];
      var devices = grp.devices || [];
      var online = 0;
      devices.forEach(function (d) { if (d.online) online++; });
      totalOnline += online;
      totalAll += devices.length;

      if (!(gid in groupToggles)) groupToggles[gid] = true;

      // --- Group header row ---
      var swatch = el("span", {className: "anoloc-group-swatch"});
      swatch.style.background = grp.color || "#6366f1";
      var icon = materialIcon(grp.icon || "location_on", "font-size:16px;");
      icon.style.color = grp.color || "#6366f1";
      icon.classList.add("anoloc-group-icon");
      var label = el("span", {className: "anoloc-group-label", textContent: grp.label || gid});
      var count = el("span", {className: "anoloc-group-count", textContent: online + "/" + devices.length});
      var info = el("div", {className: "anoloc-group-info"}, [swatch, icon, label, count]);

      var visIcon = materialIcon(groupToggles[gid] ? "visibility" : "visibility_off");
      var toggleBtnGrp = el("button", {
        className: "btn-icon anoloc-group-toggle" + (groupToggles[gid] ? " active" : ""),
        title: "Afficher/masquer",
      }, [visIcon]);
      var chevron = materialIcon("expand_more", "font-size:18px;transition:transform 0.2s;");
      chevron.style.transform = "rotate(-90deg)"; // replie par defaut
      var meta = el("div", {className: "anoloc-group-meta"}, [toggleBtnGrp, chevron]);

      var row = el("div", {className: "anoloc-group-row"}, [info, meta]);
      groupList.appendChild(row);

      // Device container (collapsible, replie par defaut)
      var devContainer = el("div", {className: "anoloc-dev-container anoloc-collapsed"});

      // Click group header to collapse/expand device list
      row.style.cursor = "pointer";
      row.addEventListener("click", function (e) {
        // Ne pas replier si on clique sur le bouton visibilite
        if (e.target.closest(".anoloc-group-toggle")) return;
        var collapsed = devContainer.classList.toggle("anoloc-collapsed");
        chevron.style.transform = collapsed ? "rotate(-90deg)" : "";
      });

      toggleBtnGrp.addEventListener("click", function (e) {
        e.stopPropagation();
        groupToggles[gid] = !groupToggles[gid];
        toggleBtnGrp.classList.toggle("active", groupToggles[gid]);
        visIcon.textContent = groupToggles[gid] ? "visibility" : "visibility_off";
        applyVisibility();
      });

      // --- Device list under group ---
      devices.sort(function (a, b) {
        return (a.label || "").localeCompare(b.label || "");
      });

      devices.forEach(function (dev, idx) {
        var statusClass = dev.online ? "online" : "offline";
        var statusLabel = "offline";
        if (dev.online) {
          if (dev.status === "running") statusLabel = "en mouvement";
          else if (dev.status === "waiting") statusLabel = "en attente";
          else if (dev.status === "stopped") statusLabel = "a l'arret";
          else statusLabel = "en ligne";
        }

        var devDot = el("span", {className: "anoloc-dev-dot " + statusClass});
        var devNum = el("span", {className: "anoloc-dev-num", textContent: String(idx + 1)});
        var devName = el("span", {className: "anoloc-dev-name", textContent: dev.label || dev.id});
        var devStatus = el("span", {className: "anoloc-dev-status " + statusClass, textContent: statusLabel});

        var devRight = el("div", {className: "anoloc-dev-right"});
        if (dev.online && dev.speed != null) {
          var speedEl = el("span", {className: "anoloc-dev-speed", textContent: dev.speed + " km/h"});
          devRight.appendChild(speedEl);
        }
        if (dev.online && dev.battery_pct != null) {
          var batIcon = dev.battery_pct > 60 ? "battery_full" : dev.battery_pct > 20 ? "battery_3_bar" : "battery_1_bar";
          var bat = el("span", {className: "anoloc-dev-battery"}, [
            materialIcon(batIcon, "font-size:14px;vertical-align:middle;"),
            document.createTextNode(dev.battery_pct + "%"),
          ]);
          devRight.appendChild(bat);
        }
        devRight.appendChild(devStatus);

        var devRow = el("div", {className: "anoloc-dev-row"}, [
          el("div", {className: "anoloc-dev-left"}, [devDot, devNum, devName]),
          devRight,
        ]);
        devContainer.appendChild(devRow);
      });
      groupList.appendChild(devContainer);
    });

    // Update panel header count
    var countEl = document.getElementById("anoloc-count");
    if (countEl) countEl.textContent = totalOnline + "/" + totalAll + " en ligne";
    var dotEl = document.getElementById("anoloc-dot");
    if (dotEl) dotEl.className = "anoloc-status-dot " + (totalOnline > 0 ? "online" : "offline");

    // Update widget header badge (visible even when collapsed)
    var headerCount = document.getElementById("anoloc-header-count");
    if (headerCount) headerCount.textContent = totalOnline + "/" + totalAll;
    var headerDot = document.getElementById("anoloc-header-dot");
    if (headerDot) headerDot.className = "anoloc-status-dot " + (totalOnline > 0 ? "online" : "offline");
  }

  // --- Update markers on the map ---
  function updateMarkers(data) {
    var mapObj = window.CockpitMapView && window.CockpitMapView.getMap
      ? window.CockpitMapView.getMap()
      : null;
    if (!mapObj) {
      // Carte pas encore initialisee, on reessaiera au prochain refresh
      return;
    }

    var groups = data.groups || {};
    var seenDevices = {};

    Object.keys(groups).forEach(function (gid) {
      var grp = groups[gid];
      var devices = grp.devices || [];

      // Ensure layer group exists
      if (!anolocLayers[gid]) {
        anolocLayers[gid] = L.layerGroup();
        if (anolocVisible && groupToggles[gid] !== false) {
          anolocLayers[gid].addTo(mapObj);
        }
      }

      devices.forEach(function (dev, idx) {
        seenDevices[dev.id] = true;
        var lat = dev.lat;
        var lng = dev.lng;
        if (lat == null || lng == null) return;

        var existing = anolocMarkers[dev.id];
        if (existing) {
          // Update position (smooth)
          existing.setLatLng([lat, lng]);
          existing.setIcon(createBeaconIcon(grp, idx + 1, dev));
          existing._anolocData = dev;
        } else {
          // Create new marker
          var marker = L.marker([lat, lng], {
            icon: createBeaconIcon(grp, idx + 1, dev),
            zIndexOffset: 10000,
          });
          marker._anolocData = dev;
          marker._anolocGroup = gid;

          marker.on("click", function () {
            var d = marker._anolocData;
            var popupNode = buildPopup(d, grp);
            marker.unbindPopup();
            marker.bindPopup(popupNode, {
              maxWidth: 280,
              closeButton: true,
              className: "cockpit-popup",
            }).openPopup();
          });

          marker.addTo(anolocLayers[gid]);
          anolocMarkers[dev.id] = marker;
        }
      });
    });

    // Remove markers for devices no longer in data
    Object.keys(anolocMarkers).forEach(function (devId) {
      if (!seenDevices[devId]) {
        var m = anolocMarkers[devId];
        if (m._anolocGroup && anolocLayers[m._anolocGroup]) {
          anolocLayers[m._anolocGroup].removeLayer(m);
        }
        delete anolocMarkers[devId];
      }
    });

    applyVisibility();
  }

  // --- Create beacon icon (DOM-based) ---
  function createBeaconIcon(grp, num, dev) {
    var statusClass = "offline";
    if (dev.online) {
      if (dev.status === "running") statusClass = "running";
      else if (dev.status === "waiting") statusClass = "waiting";
      else if (dev.status === "stopped") statusClass = "stopped";
      else statusClass = "online";
    }

    var container = el("div", {className: "anoloc-marker " + statusClass});
    container.style.background = grp.color || "#6366f1";

    var iconEl = materialIcon(grp.icon || "location_on");
    iconEl.classList.add("anoloc-marker-icon");
    container.appendChild(iconEl);

    var numEl = el("span", {className: "anoloc-marker-num", textContent: String(num)});
    container.appendChild(numEl);

    return L.divIcon({
      html: container.outerHTML,
      className: "anoloc-marker-wrapper",
      iconSize: null,
      iconAnchor: [18, 18],
    });
  }

  // --- Build popup (DOM-based) ---
  function buildPopup(dev, grp) {
    var popup = el("div", {className: "anoloc-popup"});

    // Title
    var titleIcon = materialIcon(grp.icon || "location_on", "font-size:16px;vertical-align:middle;margin-right:4px;");
    var title = el("div", {className: "anoloc-popup-title"}, [titleIcon, dev.label || dev.id]);
    title.style.color = grp.color || "#6366f1";
    popup.appendChild(title);

    // Status
    popup.appendChild(el("div", {className: "anoloc-popup-row"}, [
      "Statut: ", el("strong", {textContent: dev.status || "inconnu"}),
    ]));

    // Speed
    if (dev.speed != null) {
      popup.appendChild(el("div", {className: "anoloc-popup-row"}, [
        "Vitesse: ", el("strong", {textContent: dev.speed + " km/h"}),
      ]));
    }

    // Battery
    if (dev.battery_pct != null) {
      popup.appendChild(el("div", {className: "anoloc-popup-row"}, [
        "Batterie: ", el("strong", {textContent: dev.battery_pct + "%"}),
      ]));
    }

    // Last update
    if (dev.collected_at) {
      try {
        var d = new Date(dev.collected_at);
        popup.appendChild(el("div", {className: "anoloc-popup-row anoloc-popup-time"}, [
          "MAJ: " + d.toLocaleTimeString("fr-FR"),
        ]));
      } catch (e) { /* ignore */ }
    }

    return popup;
  }

  // --- Toggle button state ---
  function updateToggleBtnState() {
    var btn = document.getElementById("anoloc-toggle-map");
    if (btn) btn.classList.toggle("active", anolocVisible);
  }

  // --- Visibility ---
  function applyVisibility() {
    var mapObj = window.CockpitMapView && window.CockpitMapView.getMap
      ? window.CockpitMapView.getMap()
      : null;
    if (!mapObj) return;

    Object.keys(anolocLayers).forEach(function (gid) {
      var layer = anolocLayers[gid];
      var shouldShow = anolocVisible && groupToggles[gid] !== false;
      if (shouldShow && !mapObj.hasLayer(layer)) {
        mapObj.addLayer(layer);
      } else if (!shouldShow && mapObj.hasLayer(layer)) {
        mapObj.removeLayer(layer);
      }
    });
  }

  // --- Boot ---
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
