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
  var anolocVisible = true;
  var groupToggles = {};      // beaconGroupId -> boolean (visible on map)
  var groupExpanded = {};     // beaconGroupId -> boolean (panel expanded)
  var refreshTimer = null;
  var REFRESH_MS = 15000;     // 15s
  var lastData = null;

  // --- Trail state ---
  var trailPolylines = {};    // deviceId -> L.polyline
  var trailDecorators = {};   // deviceId -> [L.circleMarker] (time dots)
  var activeTrails = {};      // deviceId -> {minutes, color, groupId}
  var TRAIL_DURATIONS = [
    { label: "30 min", value: 30 },
    { label: "1h", value: 60 },
    { label: "2h", value: 120 },
    { label: "4h", value: 240 },
    { label: "Journee", value: 1440 },
  ];

  // --- Lock/follow state ---
  var lockedDeviceId = null;       // deviceId currently locked (only one at a time per tab)
  var watcherId = "w-" + Math.random().toString(36).slice(2, 10) + "-" + Date.now();
  var lockKeepAliveTimer = null;

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

    // Unlock device when tab closes (best-effort via sendBeacon)
    window.addEventListener("beforeunload", function () {
      if (lockedDeviceId) {
        var mongoId = tabletMongoId(lockedDeviceId);
        if (mongoId) {
          var csrfMeta = document.querySelector('meta[name="csrf-token"]');
          var csrf = csrfMeta ? csrfMeta.content : "";
          var payload = JSON.stringify({ mode: "normal", watcher_id: watcherId });
          try {
            navigator.sendBeacon(
              "/field/admin/device/" + encodeURIComponent(mongoId) + "/tracking",
              new Blob([payload], { type: "application/json" })
            );
          } catch (e) { /* fallback: TTL will expire in 90s */ }
        }
      }
    });

    // Observer le switch vers la carte pour injecter les markers
    var mapMain = document.getElementById("map-main");
    if (mapMain) {
      var observer = new MutationObserver(function () {
        var mapVisible = mapMain.style.display !== "none";
        if (mapVisible && lastData && anolocVisible) {
          setTimeout(function () { updateMarkers(lastData); applyVisibility(); }, 200);
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

    // Etat initial : visible sur la carte
    toggleBtn.classList.add("active");

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

  // --- Refresh: fetch /anoloc/live (avec scope event/year pour inclure tablettes) ---
  function refresh() {
    var qs = new URLSearchParams();
    if (window.selectedEvent) qs.set("event", window.selectedEvent);
    if (window.selectedYear) qs.set("year", String(window.selectedYear));
    var url = "/anoloc/live" + (qs.toString() ? ("?" + qs.toString()) : "");
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        console.log("[Anoloc] /anoloc/live response:", JSON.stringify(data).substring(0, 500));
        console.log("[Anoloc] groups count:", Object.keys(data.groups || {}).length, "enabled:", data.enabled);
        if (!data.enabled) {
          showDisabled();
          return;
        }
        lastData = data;
        updatePanel(data);
        updateMarkers(data);
        refreshActiveTrails();
        followLockedDevice();
        // Mettre a jour les tooltips des pins PCORG avec les statuts frais
        if (typeof window.pcorgUpdateTooltips === "function") {
          window.pcorgUpdateTooltips();
        }
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
      var expanded = !!groupExpanded[gid];
      var chevron = materialIcon("expand_more", "font-size:18px;transition:transform 0.2s;");
      chevron.style.transform = expanded ? "" : "rotate(-90deg)";
      var meta = el("div", {className: "anoloc-group-meta"}, [toggleBtnGrp, chevron]);

      var row = el("div", {className: "anoloc-group-row"}, [info, meta]);
      groupList.appendChild(row);

      // Device container (collapsible, restaurer etat ouvert/ferme)
      var devContainer = el("div", {className: "anoloc-dev-container" + (expanded ? "" : " anoloc-collapsed")});

      // Click group header to collapse/expand device list
      row.style.cursor = "pointer";
      (function (groupId) {
        row.addEventListener("click", function (e) {
          if (e.target.closest(".anoloc-group-toggle")) return;
          var collapsed = devContainer.classList.toggle("anoloc-collapsed");
          chevron.style.transform = collapsed ? "rotate(-90deg)" : "";
          groupExpanded[groupId] = !collapsed;
        });
      })(gid);

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
        var statusLabel = "hors ligne";
        if (dev.online) {
          if (dev.status === "running") statusLabel = "en mouvement";
          else if (dev.status === "waiting") statusLabel = "en attente";
          else if (dev.status === "stopped") statusLabel = "a l'arret";
          else statusLabel = "en ligne";
          if (dev.gps_fix === 0) statusLabel += " (sans GPS)";
        }

        // Statut patrouille pour les tablettes
        var patrolBadge = null;
        if (dev.kind === "tablet" && dev.patrol_status) {
          var pMeta = {
            patrouille: { label: "Disponible", color: "#22c55e" },
            intervention: { label: "Intervention", color: "#f59e0b" },
            sur_place: { label: "ASL", color: "#3b82f6" },
            pause: { label: "Pause", color: "#94a3b8" },
            fin_intervention: { label: "Fin d'inter", color: "#8b5cf6" },
          };
          var pm = pMeta[dev.patrol_status] || pMeta.patrouille;
          patrolBadge = el("span", {
            className: "anoloc-patrol-badge",
            textContent: pm.label,
          });
          patrolBadge.style.cssText = "background:" + pm.color + ";color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;margin-left:6px;white-space:nowrap;";
        }

        var devDot = el("span", {className: "anoloc-dev-dot " + statusClass});
        var devNum = el("span", {className: "anoloc-dev-num", textContent: String(idx + 1)});
        var devName = el("span", {className: "anoloc-dev-name", textContent: dev.label || dev.id});
        // Icone type tablette a afficher a gauche du nom
        var devKindIcon = null;
        if (dev.kind === "tablet") {
          devKindIcon = materialIcon("tablet_android", "font-size:13px;margin-right:4px;vertical-align:middle;");
          devKindIcon.title = "Tablette terrain";
        }
        var devStatus = el("span", {className: "anoloc-dev-status " + statusClass, textContent: statusLabel});

        var devRight = el("div", {className: "anoloc-dev-right"});
        if (dev.online && dev.speed != null) {
          var speedEl = el("span", {className: "anoloc-dev-speed", textContent: Math.round(dev.speed) + " km/h"});
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

        var leftChildren = [devDot, devNum];
        if (devKindIcon) leftChildren.push(devKindIcon);
        leftChildren.push(devName);
        if (patrolBadge) leftChildren.push(patrolBadge);
        var devRow = el("div", {className: "anoloc-dev-row"}, [
          el("div", {className: "anoloc-dev-left"}, leftChildren),
          devRight,
        ]);
        devRow.style.cursor = "pointer";
        (function(deviceId, groupId) {
          devRow.addEventListener("click", function (e) {
            e.stopPropagation();
            var marker = anolocMarkers[deviceId];
            if (!marker) return;
            // Activer la visibilite du groupe si necessaire
            if (!anolocVisible) {
              anolocVisible = true;
              var mainBtn = document.getElementById("anoloc-toggle");
              if (mainBtn) mainBtn.classList.add("active");
            }
            if (groupToggles[groupId] === false) {
              groupToggles[groupId] = true;
              applyVisibility();
            }
            // Basculer vers la carte
            if (window.CockpitMapView && window.CockpitMapView.switchView) {
              window.CockpitMapView.switchView("map");
            }
            // Zoom et centre
            var mapObj = window.CockpitMapView && window.CockpitMapView.getMap
              ? window.CockpitMapView.getMap() : null;
            if (mapObj) {
              mapObj.setView(marker.getLatLng(), 18, {animate: true});
              // Ouvrir le popup
              setTimeout(function () {
                marker.fire("click");
              }, 400);
            }
          });
        })(dev.id, gid);
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

    console.log("[Anoloc] updatePanel done: " + groupIds.length + " groups rendered, " + totalOnline + "/" + totalAll + " devices, groupList children:", groupList ? groupList.children.length : "NO groupList", "body classes:", document.getElementById("widget-right-4-body") ? document.getElementById("widget-right-4-body").className : "NO body");
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
        dev._groupId = gid; // store for trail functions
        seenDevices[dev.id] = true;
        var lat = dev.lat;
        var lng = dev.lng;
        if (lat == null || lng == null) return;

        // Masquer les balises arretees ET hors ligne (pas de signal GPS)
        var isStoppedOffline = (dev.status === "stopped" && !dev.online);
        if (isStoppedOffline) {
          // Retirer le marker s'il existait
          var old = anolocMarkers[dev.id];
          if (old) {
            if (anolocLayers[gid]) anolocLayers[gid].removeLayer(old);
            delete anolocMarkers[dev.id];
          }
          return;
        }

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

          // Clic droit sur une tablette : ouvrir le composer de message
          marker.on("contextmenu", function (e) {
            var d = marker._anolocData;
            if (d && d.kind === "tablet" && window.FieldAdmin && window.FieldAdmin.openCompose) {
              if (e.originalEvent) {
                e.originalEvent.preventDefault();
                e.originalEvent.stopPropagation();
              }
              // id au format "field:<objectId>" -> on extrait l'ObjectId
              var rawId = String(d.id || "");
              var deviceId = rawId.indexOf("field:") === 0 ? rawId.slice(6) : rawId;
              window.FieldAdmin.openCompose({ device_id: deviceId, device_name: d.name });
            }
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
        // Also remove trail and lock if device disappeared
        removeTrail(devId);
        delete activeTrails[devId];
        if (lockedDeviceId === devId) unlockDevice(devId);
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

    var isTablet = dev.kind === "tablet";
    var container = el("div", {className: "anoloc-marker " + statusClass + (isTablet ? " tablet" : "")});
    container.style.background = grp.color || "#6366f1";

    // Icone : toujours l'icone du groupe (tablettes et balises)
    var iconEl = materialIcon(grp.icon || "location_on");
    iconEl.classList.add("anoloc-marker-icon");
    container.appendChild(iconEl);

    var numEl = el("span", {className: "anoloc-marker-num", textContent: String(num)});
    container.appendChild(numEl);

    // Badge tablette (petit rond a droite)
    if (isTablet) {
      var badge = el("span", {className: "anoloc-marker-badge"});
      badge.textContent = "T";
      badge.title = "Tablette terrain";
      container.appendChild(badge);
    }

    return L.divIcon({
      html: container.outerHTML,
      className: "anoloc-marker-wrapper",
      iconSize: null,
      iconAnchor: [18, 18],
    });
  }

  // --- Lock/follow functions ---
  function lockDevice(deviceId, isTablet) {
    // If already locked on another device, unlock it first
    if (lockedDeviceId && lockedDeviceId !== deviceId) {
      unlockDevice(lockedDeviceId);
    }
    lockedDeviceId = deviceId;

    // If it's a tablet, tell it to switch to high_freq + start keep-alive
    if (isTablet) {
      var mongoId = tabletMongoId(deviceId);
      if (mongoId) {
        setTabletTrackingMode(mongoId, "high_freq");
        startLockKeepAlive(mongoId);
      }
    }

    // Immediately pan to the device
    var marker = anolocMarkers[deviceId];
    if (marker) {
      var mapObj = window.CockpitMapView && window.CockpitMapView.getMap
        ? window.CockpitMapView.getMap() : null;
      if (mapObj) {
        mapObj.setView(marker.getLatLng(), Math.max(mapObj.getZoom(), 17), { animate: true });
      }
    }
  }

  function unlockDevice(deviceId) {
    if (!deviceId) return;
    stopLockKeepAlive();
    // If it was a tablet, revert to normal
    var mongoId = tabletMongoId(deviceId);
    if (mongoId) {
      setTabletTrackingMode(mongoId, "normal");
    }
    if (lockedDeviceId === deviceId) {
      lockedDeviceId = null;
    }
  }

  function tabletMongoId(deviceId) {
    var raw = String(deviceId);
    return raw.indexOf("field:") === 0 ? raw.slice(6) : null;
  }

  function setTabletTrackingMode(mongoId, mode) {
    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    var csrf = csrfMeta ? csrfMeta.content : "";
    fetch("/field/admin/device/" + encodeURIComponent(mongoId) + "/tracking", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
      },
      body: JSON.stringify({ mode: mode, watcher_id: watcherId }),
    }).catch(function () { /* silent */ });
  }

  function startLockKeepAlive(mongoId) {
    stopLockKeepAlive();
    // Ping every 60s to keep the TTL alive (server TTL = 90s)
    lockKeepAliveTimer = setInterval(function () {
      setTabletTrackingMode(mongoId, "high_freq");
    }, 60000);
  }

  function stopLockKeepAlive() {
    if (lockKeepAliveTimer) {
      clearInterval(lockKeepAliveTimer);
      lockKeepAliveTimer = null;
    }
  }

  function followLockedDevice() {
    if (!lockedDeviceId) return;
    var marker = anolocMarkers[lockedDeviceId];
    if (!marker) {
      // Device disappeared, unlock
      unlockDevice(lockedDeviceId);
      return;
    }
    var mapObj = window.CockpitMapView && window.CockpitMapView.getMap
      ? window.CockpitMapView.getMap() : null;
    if (!mapObj) return;
    mapObj.panTo(marker.getLatLng(), { animate: true, duration: 0.5 });
  }

  // --- Trail functions ---
  function fetchTrail(deviceId, minutes, color, groupId) {
    fetch("/anoloc/trail?device_id=" + encodeURIComponent(deviceId) + "&minutes=" + minutes)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.ok) return;
        renderTrail(deviceId, data.points, color, groupId);
      })
      .catch(function () { /* silent */ });
  }

  function renderTrail(deviceId, points, color, groupId) {
    removeTrail(deviceId);
    if (!points || points.length < 2) return;

    var mapObj = window.CockpitMapView && window.CockpitMapView.getMap
      ? window.CockpitMapView.getMap() : null;
    if (!mapObj) return;

    var latlngs = points.map(function (p) { return [p.lat, p.lng]; });

    // Main polyline
    var line = L.polyline(latlngs, {
      color: color || "#6366f1",
      weight: 3,
      opacity: 0.7,
      dashArray: "8,6",
      lineJoin: "round",
    });

    // Layer group: use the device's group layer or add directly to map
    var layer = anolocLayers[groupId];
    if (layer) {
      line.addTo(layer);
    } else {
      line.addTo(mapObj);
    }
    trailPolylines[deviceId] = line;

    // Time dots every ~10 points (or at least 5 dots)
    var dots = [];
    var step = Math.max(1, Math.floor(points.length / 12));
    for (var i = 0; i < points.length; i += step) {
      var p = points[i];
      var progress = i / (points.length - 1); // 0..1
      var opacity = 0.3 + progress * 0.7;     // older = faded
      var radius = 2.5 + progress * 1.5;
      var dot = L.circleMarker([p.lat, p.lng], {
        radius: radius,
        color: color || "#6366f1",
        fillColor: color || "#6366f1",
        fillOpacity: opacity,
        weight: 1,
        opacity: opacity,
      });
      // Tooltip with time
      if (p.ts) {
        try {
          var dt = new Date(p.ts);
          var ts = String(dt.getHours()).padStart(2, "0") + ":" + String(dt.getMinutes()).padStart(2, "0");
          dot.bindTooltip(ts, { direction: "top", className: "anoloc-trail-tooltip" });
        } catch (e) { /* ignore */ }
      }
      if (layer) dot.addTo(layer);
      else dot.addTo(mapObj);
      dots.push(dot);
    }
    trailDecorators[deviceId] = dots;
  }

  function removeTrail(deviceId) {
    if (trailPolylines[deviceId]) {
      trailPolylines[deviceId].remove();
      delete trailPolylines[deviceId];
    }
    if (trailDecorators[deviceId]) {
      trailDecorators[deviceId].forEach(function (d) { d.remove(); });
      delete trailDecorators[deviceId];
    }
  }

  function toggleTrail(deviceId, minutes, color, groupId) {
    if (activeTrails[deviceId]) {
      removeTrail(deviceId);
      delete activeTrails[deviceId];
      return false;
    }
    activeTrails[deviceId] = { minutes: minutes, color: color, groupId: groupId };
    fetchTrail(deviceId, minutes, color, groupId);
    return true;
  }

  function refreshActiveTrails() {
    Object.keys(activeTrails).forEach(function (deviceId) {
      var t = activeTrails[deviceId];
      fetchTrail(deviceId, t.minutes, t.color, t.groupId);
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
    var statusText = dev.status || "inconnu";
    var statusMap = {running: "en mouvement", stopped: "a l'arret", waiting: "en attente", offline: "hors ligne", towing: "remorquage"};
    popup.appendChild(el("div", {className: "anoloc-popup-row"}, [
      "Statut: ", el("strong", {textContent: statusMap[statusText] || statusText}),
    ]));

    // GPS
    popup.appendChild(el("div", {className: "anoloc-popup-row"}, [
      "GPS: ", el("strong", {textContent: dev.gps_fix ? "OK" : "pas de signal"}),
    ]));

    // Patrol status (tablets only)
    if (dev.kind === "tablet" && dev.patrol_status) {
      var psMeta = {
        patrouille: { label: "Disponible", color: "#22c55e" },
        intervention: { label: "Intervention", color: "#f59e0b" },
        sur_place: { label: "ASL", color: "#3b82f6" },
        pause: { label: "Pause", color: "#94a3b8" },
        fin_intervention: { label: "Fin d'intervention", color: "#8b5cf6" },
      };
      var psm = psMeta[dev.patrol_status] || psMeta.patrouille;
      var psBadge = el("strong", {textContent: psm.label});
      psBadge.style.color = psm.color;
      popup.appendChild(el("div", {className: "anoloc-popup-row"}, [
        "Activite: ", psBadge,
      ]));

      // Bouton liberation si fin_intervention
      if (dev.patrol_status === "fin_intervention") {
        var releaseRow = el("div", {className: "anoloc-release-row"});
        var releaseInput = el("input", {
          type: "text",
          className: "anoloc-release-input",
          placeholder: dev.fin_comment ? "Commentaire (optionnel)" : "Commentaire (obligatoire)",
        });
        var releaseBtn = el("button", {className: "anoloc-release-btn"}, [
          materialIcon("check_circle", "font-size:16px;vertical-align:middle;margin-right:4px;"),
          "Liberer",
        ]);
        releaseBtn.addEventListener("click", function () {
          var comment = releaseInput.value.trim();
          releaseBtn.disabled = true;
          releaseBtn.textContent = "...";
          var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};
          apiPost("/api/field-device/release", {
            device_name: dev.label,
            event: ey.event || "",
            year: ey.year || "",
            comment: comment,
          }).then(function (r) { return r.json(); })
            .then(function (resp) {
              if (resp && resp.ok) {
                if (marker && marker.getPopup()) marker.closePopup();
              } else {
                releaseBtn.disabled = false;
                releaseBtn.textContent = "Liberer";
                var errMsg = (resp && resp.message) || (resp && resp.error) || "Erreur";
                releaseInput.value = "";
                releaseInput.placeholder = errMsg;
                releaseInput.style.borderColor = "#ef4444";
              }
            })
            .catch(function () {
              releaseBtn.disabled = false;
              releaseBtn.textContent = "Liberer";
            });
        });
        releaseRow.appendChild(releaseInput);
        releaseRow.appendChild(releaseBtn);
        popup.appendChild(releaseRow);
        if (dev.fin_comment) {
          var fcRow = el("div", {className: "anoloc-popup-row"});
          fcRow.style.cssText = "font-size:11px;color:#94a3b8;font-style:italic;";
          fcRow.textContent = "Commentaire tablette: " + dev.fin_comment;
          popup.appendChild(fcRow);
        }
      }
    }

    // Speed
    if (dev.speed != null && dev.gps_fix) {
      popup.appendChild(el("div", {className: "anoloc-popup-row"}, [
        "Vitesse: ", el("strong", {textContent: Math.round(dev.speed) + " km/h"}),
      ]));
    }

    // Battery (with icon + color)
    if (dev.battery_pct != null) {
      var batPct = dev.battery_pct;
      var batIconName = batPct > 60 ? "battery_full" : batPct > 20 ? "battery_3_bar" : "battery_1_bar";
      var batColor = batPct > 60 ? "#22c55e" : batPct > 20 ? "#eab308" : "#ef4444";
      var batStrong = el("strong", {textContent: batPct + "%"});
      batStrong.style.color = batColor;
      popup.appendChild(el("div", {className: "anoloc-popup-row"}, [
        materialIcon(batIconName, "font-size:16px;vertical-align:middle;margin-right:4px;color:" + batColor),
        "Batterie: ", batStrong,
      ]));
    }

    // Last real position
    if (dev.last_real_at) {
      try {
        var lr = new Date(dev.last_real_at);
        popup.appendChild(el("div", {className: "anoloc-popup-row anoloc-popup-time"}, [
          "Derniere position: " + lr.toLocaleString("fr-FR"),
        ]));
      } catch (e) {}
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

    // Trail controls
    var trailSection = el("div", {className: "anoloc-trail-controls"});
    var isActive = !!activeTrails[dev.id];
    var currentMinutes = isActive ? activeTrails[dev.id].minutes : 60;
    var trailColor = grp.color || "#6366f1";

    // Duration select
    var durationSelect = el("select", {className: "anoloc-trail-select"});
    TRAIL_DURATIONS.forEach(function (dur) {
      var opt = el("option", {value: dur.value, textContent: dur.label});
      if (dur.value === currentMinutes) opt.selected = true;
      durationSelect.appendChild(opt);
    });

    // Toggle button
    var trailBtn = el("button", {
      className: "anoloc-trail-btn" + (isActive ? " active" : ""),
    }, [
      materialIcon(isActive ? "close" : "timeline", "font-size:16px;vertical-align:middle;margin-right:4px;"),
      isActive ? "Masquer" : "Trace",
    ]);
    trailBtn.style.borderColor = trailColor;
    if (isActive) trailBtn.style.background = trailColor;

    trailBtn.addEventListener("click", function () {
      var mins = parseInt(durationSelect.value, 10) || 60;
      var nowActive = toggleTrail(dev.id, mins, trailColor, dev._groupId || "");
      trailBtn.className = "anoloc-trail-btn" + (nowActive ? " active" : "");
      trailBtn.style.background = nowActive ? trailColor : "";
      trailBtn.textContent = "";
      trailBtn.appendChild(materialIcon(nowActive ? "close" : "timeline", "font-size:16px;vertical-align:middle;margin-right:4px;"));
      trailBtn.appendChild(document.createTextNode(nowActive ? "Masquer" : "Trace"));
      if (nowActive) {
        activeTrails[dev.id].minutes = mins;
      }
    });

    // Re-fetch on duration change while active
    durationSelect.addEventListener("change", function () {
      if (activeTrails[dev.id]) {
        var mins = parseInt(durationSelect.value, 10) || 60;
        activeTrails[dev.id].minutes = mins;
        fetchTrail(dev.id, mins, trailColor, activeTrails[dev.id].groupId);
      }
    });

    trailSection.appendChild(durationSelect);
    trailSection.appendChild(trailBtn);
    popup.appendChild(trailSection);

    // Lock/follow button
    var isLocked = lockedDeviceId === dev.id;
    var lockBtn = el("button", {
      className: "anoloc-lock-btn" + (isLocked ? " active" : ""),
    }, [
      materialIcon(isLocked ? "lock_open" : "gps_fixed", "font-size:16px;vertical-align:middle;margin-right:4px;"),
      isLocked ? "Deverrouiller" : "Suivre",
    ]);
    if (isLocked) {
      lockBtn.style.background = grp.color || "#6366f1";
      lockBtn.style.borderColor = "transparent";
    }

    lockBtn.addEventListener("click", function () {
      if (lockedDeviceId === dev.id) {
        unlockDevice(dev.id);
        lockBtn.className = "anoloc-lock-btn";
        lockBtn.style.background = "";
        lockBtn.style.borderColor = "";
        lockBtn.textContent = "";
        lockBtn.appendChild(materialIcon("gps_fixed", "font-size:16px;vertical-align:middle;margin-right:4px;"));
        lockBtn.appendChild(document.createTextNode("Suivre"));
      } else {
        lockDevice(dev.id, dev.kind === "tablet");
        lockBtn.className = "anoloc-lock-btn active";
        lockBtn.style.background = grp.color || "#6366f1";
        lockBtn.style.borderColor = "transparent";
        lockBtn.textContent = "";
        lockBtn.appendChild(materialIcon("lock_open", "font-size:16px;vertical-align:middle;margin-right:4px;"));
        lockBtn.appendChild(document.createTextNode("Deverrouiller"));
      }
    });
    popup.appendChild(lockBtn);

    // Quick message button (tablets only)
    if (dev.kind === "tablet") {
      var msgBtn = el("button", {
        className: "anoloc-lock-btn",
      }, [
        materialIcon("send", "font-size:16px;vertical-align:middle;margin-right:4px;"),
        "Envoyer un message",
      ]);
      msgBtn.style.marginTop = "4px";
      msgBtn.style.background = "#3b82f6";
      msgBtn.style.borderColor = "transparent";
      msgBtn.style.color = "#fff";
      msgBtn.addEventListener("click", function () {
        openSendMessageModal(dev);
      });
      popup.appendChild(msgBtn);
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

  // --- Public API: lookup device by label (for pcorg.js cross-reference) ---
  window.getAnolocDeviceByLabel = function (name) {
    if (!lastData || !lastData.groups || !name) return null;
    var groups = lastData.groups;
    for (var gid in groups) {
      var grp = groups[gid];
      var devs = grp.devices || [];
      for (var i = 0; i < devs.length; i++) {
        if (devs[i].label === name) {
          return { device: devs[i], group: grp };
        }
      }
    }
    return null;
  };

  // --- Send message modal ---
  function _csrfToken() {
    var m = document.querySelector("meta[name='csrf-token']");
    return m ? m.content : "";
  }

  function openSendMessageModal(dev) {
    var old = document.getElementById("anoloc-send-msg-modal");
    if (old) old.remove();

    var rawId = String(dev.id || "");
    var deviceId = rawId.indexOf("field:") === 0 ? rawId.slice(6) : rawId;

    var overlay = el("div", {className: "anoloc-msg-overlay", id: "anoloc-send-msg-modal"});
    var box = el("div", {className: "anoloc-msg-box"});

    // Header
    var header = el("div", {className: "anoloc-msg-header"});
    var headerLeft = el("div", {className: "anoloc-msg-header-left"}, [
      materialIcon("chat", "font-size:20px;color:#3b82f6;"),
      el("span", {textContent: dev.label || dev.id}),
    ]);
    var closeBtn = el("button", {className: "icon-btn anoloc-msg-close"}, [
      materialIcon("close"),
    ]);
    closeBtn.addEventListener("click", function () { overlay.remove(); });
    header.appendChild(headerLeft);
    header.appendChild(closeBtn);
    box.appendChild(header);

    // Tabs
    var tabs = el("div", {className: "anoloc-msg-tabs"});
    var tabNew = el("button", {className: "anoloc-msg-tab active", textContent: "Nouveau"});
    var tabHistory = el("button", {className: "anoloc-msg-tab", textContent: "Conversations"});
    tabs.appendChild(tabNew);
    tabs.appendChild(tabHistory);
    box.appendChild(tabs);

    // Panel containers
    var panelNew = el("div", {className: "anoloc-msg-panel"});
    var panelHistory = el("div", {className: "anoloc-msg-panel"});
    panelHistory.hidden = true;

    tabNew.addEventListener("click", function () {
      tabNew.classList.add("active"); tabHistory.classList.remove("active");
      panelNew.hidden = false; panelHistory.hidden = true;
    });
    tabHistory.addEventListener("click", function () {
      tabHistory.classList.add("active"); tabNew.classList.remove("active");
      panelHistory.hidden = false; panelNew.hidden = true;
      loadConversations(deviceId, panelHistory, overlay);
    });

    // === Panel NEW MESSAGE ===
    buildNewMessagePanel(panelNew, deviceId, overlay);

    // === Panel HISTORY ===
    panelHistory.appendChild(el("div", {className: "anoloc-msg-loading", textContent: "Chargement..."}));

    box.appendChild(panelNew);
    box.appendChild(panelHistory);
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.remove();
    });
  }

  function buildNewMessagePanel(panel, deviceId, overlay) {
    var body = el("div", {className: "anoloc-msg-body"});

    var typeRow = el("div", {className: "anoloc-msg-row"});
    typeRow.appendChild(el("label", {className: "anoloc-msg-label", textContent: "Type"}));
    var typeSelect = el("select", {className: "anoloc-msg-select"});
    [{v: "info", l: "Info"}, {v: "instruction", l: "Instruction"}, {v: "alert", l: "Alerte"}].forEach(function (t) {
      typeSelect.appendChild(el("option", {value: t.v, textContent: t.l}));
    });
    typeRow.appendChild(typeSelect);
    body.appendChild(typeRow);

    var titleRow = el("div", {className: "anoloc-msg-row"});
    titleRow.appendChild(el("label", {className: "anoloc-msg-label", textContent: "Titre"}));
    var titleInput = el("input", {type: "text", className: "anoloc-msg-input", placeholder: "Titre du message (max 120 car.)"});
    titleInput.maxLength = 120;
    titleRow.appendChild(titleInput);
    body.appendChild(titleRow);

    var bodyRow = el("div", {className: "anoloc-msg-row"});
    bodyRow.appendChild(el("label", {className: "anoloc-msg-label", textContent: "Message"}));
    var bodyTextarea = el("textarea", {className: "anoloc-msg-textarea", placeholder: "Contenu du message (optionnel)"});
    bodyTextarea.rows = 3;
    bodyRow.appendChild(bodyTextarea);
    body.appendChild(bodyRow);

    // Photo
    var photoRow = el("div", {className: "anoloc-msg-row"});
    photoRow.appendChild(el("label", {className: "anoloc-msg-label", textContent: "Photo"}));
    var photoWrap = el("div", {className: "anoloc-msg-photo-wrap"});
    var fileInput = el("input", {type: "file"});
    fileInput.accept = "image/*";
    fileInput.style.display = "none";
    var photoBtn = el("button", {className: "anoloc-msg-photo-btn"}, [
      materialIcon("add_photo_alternate", "font-size:18px;vertical-align:middle;margin-right:4px;"),
      "Ajouter une image",
    ]);
    photoBtn.addEventListener("click", function () { fileInput.click(); });
    var preview = el("div", {className: "anoloc-msg-photo-preview"});
    preview.hidden = true;
    fileInput.addEventListener("change", function () {
      if (!fileInput.files || !fileInput.files[0]) return;
      var reader = new FileReader();
      reader.onload = function (ev) {
        preview.textContent = "";
        var img = el("img"); img.src = ev.target.result;
        preview.appendChild(img);
        var removeBtn = el("button", {className: "anoloc-msg-photo-remove"}, [materialIcon("close", "font-size:16px;")]);
        removeBtn.addEventListener("click", function (e) { e.stopPropagation(); fileInput.value = ""; preview.hidden = true; photoBtn.hidden = false; });
        preview.appendChild(removeBtn);
        preview.hidden = false; photoBtn.hidden = true;
      };
      reader.readAsDataURL(fileInput.files[0]);
    });
    photoWrap.appendChild(fileInput);
    photoWrap.appendChild(photoBtn);
    photoWrap.appendChild(preview);
    photoRow.appendChild(photoWrap);
    body.appendChild(photoRow);

    panel.appendChild(body);

    // Footer
    var footer = el("div", {className: "anoloc-msg-footer"});
    var statusEl = el("span", {className: "anoloc-msg-status"});
    var sendBtn = el("button", {className: "anoloc-msg-send"}, [
      materialIcon("send", "font-size:18px;vertical-align:middle;margin-right:6px;"),
      "Envoyer",
    ]);
    sendBtn.addEventListener("click", function () {
      var title = titleInput.value.trim();
      var bodyText = bodyTextarea.value.trim();
      if (!title && !bodyText) { statusEl.textContent = "Titre ou message requis"; statusEl.style.color = "#ef4444"; return; }
      sendBtn.disabled = true;
      statusEl.textContent = "Envoi..."; statusEl.style.color = "var(--muted)";

      var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};
      var formData = new FormData();
      formData.append("event", ey.event || "");
      formData.append("year", ey.year || "");
      formData.append("target", JSON.stringify({ device_ids: [deviceId] }));
      formData.append("type", typeSelect.value);
      formData.append("priority", typeSelect.value === "alert" ? "high" : "normal");
      formData.append("title", title);
      formData.append("body", bodyText);
      if (fileInput.files && fileInput.files[0]) formData.append("photo", fileInput.files[0]);

      fetch("/field/admin/send-with-photo", {
        method: "POST",
        headers: { "X-CSRFToken": _csrfToken() },
        body: formData,
      })
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          sendBtn.disabled = false;
          if (resp && resp.ok) {
            statusEl.textContent = "Envoye !"; statusEl.style.color = "#22c55e";
            titleInput.value = ""; bodyTextarea.value = ""; fileInput.value = "";
            preview.hidden = true; photoBtn.hidden = false;
            setTimeout(function () { statusEl.textContent = ""; }, 3000);
          } else {
            statusEl.textContent = resp.error || "Erreur"; statusEl.style.color = "#ef4444";
          }
        })
        .catch(function () { sendBtn.disabled = false; statusEl.textContent = "Erreur reseau"; statusEl.style.color = "#ef4444"; });
    });
    footer.appendChild(statusEl);
    footer.appendChild(sendBtn);
    panel.appendChild(footer);

    setTimeout(function () { titleInput.focus(); }, 100);
  }

  // === Conversations panel ===
  function loadConversations(deviceId, panel, overlay) {
    panel.textContent = "";
    panel.appendChild(el("div", {className: "anoloc-msg-loading", textContent: "Chargement..."}));

    var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};
    var qs = "?device_id=" + encodeURIComponent(deviceId);
    if (ey.event) qs += "&event=" + encodeURIComponent(ey.event);
    if (ey.year) qs += "&year=" + encodeURIComponent(ey.year);

    fetch("/field/admin/messages" + qs, { headers: { "X-CSRFToken": _csrfToken() } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        panel.textContent = "";
        if (!data || !data.ok || !data.messages || data.messages.length === 0) {
          panel.appendChild(el("div", {className: "anoloc-msg-empty", textContent: "Aucun message echange."}));
          return;
        }
        // Grouper par thread : afficher les messages racines (sans thread_id)
        var roots = [];
        var replyMap = {};
        data.messages.forEach(function (m) {
          if (!m.thread_id) {
            roots.push(m);
          } else {
            if (!replyMap[m.thread_id]) replyMap[m.thread_id] = 0;
            replyMap[m.thread_id]++;
          }
        });
        if (roots.length === 0) {
          // All are replies? Show all messages as flat list
          roots = data.messages;
        }
        var list = el("div", {className: "anoloc-conv-list"});
        roots.forEach(function (m) {
          var replies = m.reply_count || replyMap[m.id] || 0;
          var row = el("div", {className: "anoloc-conv-item"});
          var titleEl = el("div", {className: "anoloc-conv-title", textContent: m.title || m.body || "(sans titre)"});
          var meta = el("div", {className: "anoloc-conv-meta"});
          var when = "";
          try { when = new Date(m.created_at).toLocaleString("fr-FR"); } catch (e) {}
          meta.textContent = when;
          if (replies > 0) {
            var badge = el("span", {className: "anoloc-conv-replies", textContent: replies + " rep."});
            meta.appendChild(badge);
          }
          // Unread indicator if field replied
          var hasFieldReply = (data.messages || []).some(function (r) {
            return (r.thread_id === m.id) && r.direction === "field_to_cockpit" && r.status === "sent";
          });
          if (hasFieldReply) {
            row.classList.add("has-new-reply");
          }
          row.appendChild(titleEl);
          row.appendChild(meta);
          row.addEventListener("click", function () {
            openThreadView(m.id, panel, overlay);
          });
          list.appendChild(row);
        });
        panel.appendChild(list);
      })
      .catch(function () {
        panel.textContent = "";
        panel.appendChild(el("div", {className: "anoloc-msg-empty", textContent: "Erreur de chargement."}));
      });
  }

  function openThreadView(threadId, panel, overlay) {
    panel.textContent = "";
    panel.appendChild(el("div", {className: "anoloc-msg-loading", textContent: "Chargement..."}));

    fetch("/field/admin/thread/" + encodeURIComponent(threadId), {
      headers: { "X-CSRFToken": _csrfToken() },
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        panel.textContent = "";
        if (!data || !data.ok) {
          panel.appendChild(el("div", {className: "anoloc-msg-empty", textContent: "Erreur."}));
          return;
        }
        // Back button
        var backBtn = el("button", {className: "anoloc-conv-back"}, [
          materialIcon("arrow_back", "font-size:18px;vertical-align:middle;margin-right:4px;"),
          "Retour",
        ]);
        backBtn.addEventListener("click", function () {
          // Re-extract deviceId from first message
          var firstMsg = (data.messages && data.messages[0]) || {};
          var devId = firstMsg.device_id || "";
          loadConversations(devId, panel, overlay);
        });
        panel.appendChild(backBtn);

        // Thread bubbles
        var thread = el("div", {className: "anoloc-thread-bubbles"});
        (data.messages || []).forEach(function (m) {
          var isField = m.direction === "field_to_cockpit";
          var bubble = el("div", {className: "anoloc-thread-bubble" + (isField ? " from-field" : " from-cockpit")});

          if (m.title) {
            var t = el("div", {className: "anoloc-thread-title", textContent: m.title});
            bubble.appendChild(t);
          }
          if (m.body) {
            var b = el("div", {className: "anoloc-thread-text", textContent: m.body});
            bubble.appendChild(b);
          }
          var photoUrl = m.payload && m.payload.photo;
          if (photoUrl) {
            var img = el("img", {className: "anoloc-thread-photo"});
            img.src = photoUrl;
            bubble.appendChild(img);
          }
          var metaEl = el("div", {className: "anoloc-thread-meta"});
          var who = isField ? (m.device_name || "Tablette") : (m.from || "Cockpit");
          var when = "";
          try { when = new Date(m.created_at).toLocaleTimeString("fr-FR", {hour: "2-digit", minute: "2-digit"}); } catch (e) {}
          metaEl.textContent = who + (when ? " - " + when : "");
          bubble.appendChild(metaEl);
          thread.appendChild(bubble);
        });
        panel.appendChild(thread);
        thread.scrollTop = thread.scrollHeight;

        // Reply form
        var replySection = el("div", {className: "anoloc-thread-reply"});
        var replyInput = el("input", {type: "text", className: "anoloc-thread-reply-input", placeholder: "Repondre..."});
        var replyFileInput = el("input", {type: "file"});
        replyFileInput.accept = "image/*";
        replyFileInput.style.display = "none";
        var replyPhotoBtn = el("button", {className: "anoloc-thread-reply-photo"}, [
          materialIcon("photo_camera", "font-size:18px;"),
        ]);
        replyPhotoBtn.addEventListener("click", function () { replyFileInput.click(); });

        var replyPreview = el("div", {className: "anoloc-thread-reply-preview"});
        replyPreview.hidden = true;
        replyFileInput.addEventListener("change", function () {
          if (!replyFileInput.files || !replyFileInput.files[0]) return;
          var reader = new FileReader();
          reader.onload = function (ev) {
            replyPreview.textContent = "";
            var img = el("img"); img.src = ev.target.result;
            replyPreview.appendChild(img);
            var rm = el("button", {className: "anoloc-msg-photo-remove"}, [materialIcon("close", "font-size:14px;")]);
            rm.addEventListener("click", function (e) { e.stopPropagation(); replyFileInput.value = ""; replyPreview.hidden = true; });
            replyPreview.appendChild(rm);
            replyPreview.hidden = false;
          };
          reader.readAsDataURL(replyFileInput.files[0]);
        });

        var replySendBtn = el("button", {className: "anoloc-thread-reply-send"}, [
          materialIcon("send", "font-size:18px;"),
        ]);

        replySendBtn.addEventListener("click", function () {
          var text = replyInput.value.trim();
          var hasPhoto = replyFileInput.files && replyFileInput.files[0];
          if (!text && !hasPhoto) { replyInput.focus(); return; }
          replySendBtn.disabled = true;

          var fd = new FormData();
          fd.append("body", text);
          if (hasPhoto) fd.append("photo", replyFileInput.files[0]);

          fetch("/field/admin/reply/" + encodeURIComponent(threadId), {
            method: "POST",
            headers: { "X-CSRFToken": _csrfToken() },
            body: fd,
          })
            .then(function (r) { return r.json(); })
            .then(function (resp) {
              replySendBtn.disabled = false;
              if (resp && resp.ok) {
                replyInput.value = "";
                replyFileInput.value = "";
                replyPreview.hidden = true;
                openThreadView(threadId, panel, overlay);
              }
            })
            .catch(function () { replySendBtn.disabled = false; });
        });
        replyInput.addEventListener("keydown", function (e) {
          if (e.key === "Enter") { e.preventDefault(); replySendBtn.click(); }
        });

        var replyRow = el("div", {className: "anoloc-thread-reply-row"});
        replyRow.appendChild(replyFileInput);
        replyRow.appendChild(replyPhotoBtn);
        replyRow.appendChild(replyInput);
        replyRow.appendChild(replySendBtn);
        replySection.appendChild(replyPreview);
        replySection.appendChild(replyRow);
        panel.appendChild(replySection);
      })
      .catch(function () {
        panel.textContent = "";
        panel.appendChild(el("div", {className: "anoloc-msg-empty", textContent: "Erreur de chargement."}));
      });
  }

  // --- Boot ---
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
