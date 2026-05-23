// routing_test.js - Bac a sable de test pour le moteur Valhalla.
// Modale legere accessible depuis la sidebar admin de l'index : permet de
// poser un point A et un point B sur la carte, basculer le mode god, et
// observer la polyline + ETA. Affiche aussi les overrides admin actifs en
// transparence pour valider qu'ils sont bien appliques.
//
// 100% independant de routing.js (qui pilote la modale fiche/device avec
// envoi a la tablette). Aucun fiche_id, aucun device, aucun forward.

(function () {
  "use strict";

  var CIRCUIT_CENTER = [47.952, 0.225];
  var CIRCUIT_BBOX = [[47.898, 0.144], [48.006, 0.306]];

  var S = {
    overlay: null,
    map: null,
    overridesLayer: null,
    routeLayer: null,
    from: null,
    to: null,
    god: false,
    pickMode: "A",
    lastResult: null,
    routeAnimFrame: null,
  };

  // ---------------------------------------------------------------
  // Style Waze (glow + base + dash anime) - identique field/routing
  // ---------------------------------------------------------------

  function stopRouteAnimation() {
    if (S.routeAnimFrame) {
      cancelAnimationFrame(S.routeAnimFrame);
      S.routeAnimFrame = null;
    }
  }

  function startRouteAnimation(dashLine, god) {
    stopRouteAnimation();
    if (!dashLine) return;
    var speed = god ? 0.9 : 0.4;
    var offset = 0;
    function tick() {
      offset = (offset - speed) % 24;
      var el = dashLine.getElement ? dashLine.getElement() : null;
      if (el) el.style.strokeDashoffset = String(offset);
      S.routeAnimFrame = requestAnimationFrame(tick);
    }
    S.routeAnimFrame = requestAnimationFrame(tick);
  }

  function drawWazeRoute(layerGroup, pts, god) {
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
    startRouteAnimation(dashLine, god);
  }

  // ----------------------------------------------------------------
  // Helpers
  // ----------------------------------------------------------------

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : "";
  }

  function api(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; });
    });
  }

  function toast(msg) {
    if (typeof window.showToast === "function") return window.showToast(msg);
    console.log("[routing-test]", msg);
  }

  function decodePolyline6(encoded) {
    if (!encoded) return [];
    var pts = [], idx = 0, lat = 0, lon = 0, n = encoded.length;
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

  function fmtMin(s) {
    var m = Math.max(1, Math.round((s || 0) / 60));
    if (m < 60) return m + " min";
    return Math.floor(m / 60) + " h " + (m % 60 < 10 ? "0" : "") + (m % 60);
  }

  // ----------------------------------------------------------------
  // DOM
  // ----------------------------------------------------------------

  function build() {
    var overlay = document.createElement("div");
    overlay.className = "routing-modal-overlay";
    overlay.id = "routing-test-overlay";

    var modal = document.createElement("div");
    modal.className = "routing-modal";
    modal.style.maxWidth = "960px";

    modal.innerHTML = ''
      + '<div class="routing-modal-header">'
      + '  <div class="routing-modal-title">'
      + '    <span class="material-symbols-outlined">science</span>'
      + '    <span>Test itineraire (bac a sable)</span>'
      + '  </div>'
      + '  <button class="routing-modal-close" aria-label="Fermer">'
      + '    <span class="material-symbols-outlined">close</span>'
      + '  </button>'
      + '</div>'
      + '<div class="routing-modal-body">'
      + '  <div class="rt-hint" id="rt-hint">Clique sur la carte pour poser le point A (depart).</div>'
      + '  <div class="routing-stats" id="rt-stats"></div>'
      + '  <div id="rt-map" class="routing-map" style="height:440px;"></div>'
      + '  <div class="routing-controls">'
      + '    <label class="routing-priority">'
      + '      <input type="checkbox" id="rt-god">'
      + '      <span class="material-symbols-outlined" style="color:#dc2626;">emergency</span>'
      + '      <span>Mode intervention (god)</span>'
      + '    </label>'
      + '    <div class="routing-btn-row">'
      + '      <button id="rt-pick-a" class="routing-btn routing-btn-secondary">'
      + '        <span class="material-symbols-outlined">trip_origin</span><span>Repositionner A</span>'
      + '      </button>'
      + '      <button id="rt-pick-b" class="routing-btn routing-btn-secondary">'
      + '        <span class="material-symbols-outlined">place</span><span>Repositionner B</span>'
      + '      </button>'
      + '      <button id="rt-swap" class="routing-btn routing-btn-secondary">'
      + '        <span class="material-symbols-outlined">swap_horiz</span><span>Inverser</span>'
      + '      </button>'
      + '      <button id="rt-clear" class="routing-btn routing-btn-secondary">'
      + '        <span class="material-symbols-outlined">restart_alt</span><span>Effacer</span>'
      + '      </button>'
      + '      <button id="rt-recalc" class="routing-btn routing-btn-primary">'
      + '        <span class="material-symbols-outlined">refresh</span><span>Recalculer</span>'
      + '      </button>'
      + '    </div>'
      + '  </div>'
      + '</div>';

    overlay.appendChild(modal);
    return overlay;
  }

  // ----------------------------------------------------------------
  // Map
  // ----------------------------------------------------------------

  function initMap() {
    var div = document.getElementById("rt-map");
    if (!div) return;
    S.map = L.map(div, { zoomControl: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "&copy; OpenStreetMap",
    }).addTo(S.map);
    S.map.fitBounds(CIRCUIT_BBOX);

    S.overridesLayer = L.layerGroup().addTo(S.map);
    S.routeLayer = L.layerGroup().addTo(S.map);

    loadOverrides();

    S.map.on("click", function (e) {
      if (S.pickMode === "A") {
        S.from = [e.latlng.lat, e.latlng.lng];
        S.pickMode = S.to ? null : "B";
      } else if (S.pickMode === "B") {
        S.to = [e.latlng.lat, e.latlng.lng];
        S.pickMode = null;
      } else {
        return;
      }
      updateHint();
      render();
      if (S.from && S.to) recalc();
    });

    setTimeout(function () { if (S.map) S.map.invalidateSize(); }, 50);
  }

  function loadOverrides() {
    fetch("/api/routing-overrides/active", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || j.ok === false || !S.overridesLayer) return;
        (j.items || []).forEach(renderOverride);
      })
      .catch(function () { /* silencieux */ });
  }

  function renderOverride(it) {
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
      }).bindTooltip(label).addTo(S.overridesLayer);
    } else if (it.type === "block_polygon") {
      var ring = (it.coords || []).map(function (c) { return [c[1], c[0]]; });
      if (ring.length < 3) return;
      L.polygon(ring, {
        color: color, fillColor: color,
        fillOpacity: 0.15, weight: 1.5, opacity: 0.7,
      }).bindTooltip(label).addTo(S.overridesLayer);
    }
  }

  function render() {
    if (!S.routeLayer) return;
    stopRouteAnimation();
    S.routeLayer.clearLayers();

    if (S.from) {
      L.circleMarker(S.from, {
        radius: 9, fillColor: "#16a34a", color: "#fff",
        weight: 3, fillOpacity: 1, opacity: 1,
      }).bindTooltip("A (depart)").addTo(S.routeLayer);
    }
    if (S.to) {
      L.marker(S.to, {
        icon: L.divIcon({
          className: "",
          html: "<div class='routing-dest-pin'><span class='material-symbols-outlined'>place</span></div>",
          iconSize: [32, 32], iconAnchor: [16, 30],
        }),
      }).bindTooltip("B (arrivee)").addTo(S.routeLayer);
    }

    if (S.lastResult && S.lastResult.polyline) {
      var pts = decodePolyline6(S.lastResult.polyline);
      if (pts.length >= 2) {
        drawWazeRoute(S.routeLayer, pts, !!S.god);
        try {
          S.map.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 17 });
        } catch (e) { /* ignore */ }
      }
    } else if (S.from && S.to) {
      L.polyline([S.from, S.to], {
        color: "#9ca3af", weight: 2, opacity: 0.5, dashArray: "6 8",
      }).addTo(S.routeLayer);
    }
    renderStats();
  }

  function renderStats() {
    var box = document.getElementById("rt-stats");
    if (!box) return;
    while (box.firstChild) box.removeChild(box.firstChild);

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

    if (S.from) box.appendChild(pill("A", S.from[0].toFixed(5) + ", " + S.from[1].toFixed(5)));
    if (S.to) box.appendChild(pill("B", S.to[0].toFixed(5) + ", " + S.to[1].toFixed(5)));
    if (S.lastResult) {
      box.appendChild(pill("Distance", ((S.lastResult.distance_m || 0) / 1000).toFixed(2) + " km"));
      box.appendChild(pill("ETA", fmtMin(S.lastResult.duration_s)));
      box.appendChild(pill("Moteur", S.lastResult.engine || "?",
        S.lastResult.engine === "stub" ? "#9ca3af" : "#16a34a"));
      box.appendChild(pill("Mode", S.lastResult.mode || "auto",
        S.lastResult.mode === "god" ? "#dc2626" : "#2563eb"));
      if (S.lastResult.waze_avoided > 0) {
        box.appendChild(pill("Waze", S.lastResult.waze_avoided + " evite", "#f59e0b"));
      }
    }
  }

  function updateHint() {
    var h = document.getElementById("rt-hint");
    if (!h) return;
    if (S.pickMode === "A") h.textContent = "Clique sur la carte pour poser le point A (depart).";
    else if (S.pickMode === "B") h.textContent = "Clique sur la carte pour poser le point B (arrivee).";
    else h.textContent = "Point A et B poses. Utilise les boutons pour rejouer.";
  }

  // ----------------------------------------------------------------
  // Workflow
  // ----------------------------------------------------------------

  function recalc() {
    if (!S.from || !S.to) {
      toast("Pose A et B avant de recalculer.");
      return;
    }
    var btn = document.getElementById("rt-recalc");
    if (btn) btn.disabled = true;
    api("/api/route", { from: S.from, to: S.to, god: !!S.god }).then(function (resp) {
      if (btn) btn.disabled = false;
      if (!resp.ok || !resp.body || resp.body.ok === false) {
        var err = (resp.body && resp.body.error) || ("Erreur " + resp.status);
        toast("Calcul: " + err);
        S.lastResult = null;
        render();
        return;
      }
      S.lastResult = resp.body;
      render();
    }).catch(function (e) {
      if (btn) btn.disabled = false;
      toast("Reseau indisponible: " + (e && e.message));
    });
  }

  function wire() {
    var overlay = S.overlay;
    overlay.querySelector(".routing-modal-close").addEventListener("click", close);
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });

    document.getElementById("rt-god").addEventListener("change", function (e) {
      S.god = !!e.target.checked;
      if (S.from && S.to) recalc();
    });
    document.getElementById("rt-pick-a").addEventListener("click", function () {
      S.pickMode = "A";
      updateHint();
    });
    document.getElementById("rt-pick-b").addEventListener("click", function () {
      S.pickMode = "B";
      updateHint();
    });
    document.getElementById("rt-swap").addEventListener("click", function () {
      if (!S.from || !S.to) return;
      var tmp = S.from; S.from = S.to; S.to = tmp;
      render();
      recalc();
    });
    document.getElementById("rt-clear").addEventListener("click", function () {
      S.from = null; S.to = null; S.lastResult = null; S.pickMode = "A";
      updateHint();
      render();
    });
    document.getElementById("rt-recalc").addEventListener("click", recalc);
  }

  // ----------------------------------------------------------------
  // Public API
  // ----------------------------------------------------------------

  function open() {
    close();
    S.overlay = build();
    document.body.appendChild(S.overlay);
    initMap();
    wire();
    updateHint();
    render();
  }

  function close() {
    stopRouteAnimation();
    if (S.map) {
      try { S.map.remove(); } catch (e) { /* ignore */ }
    }
    if (S.overlay && S.overlay.parentNode) {
      S.overlay.parentNode.removeChild(S.overlay);
    }
    S.overlay = null;
    S.map = null;
    S.overridesLayer = null;
    S.routeLayer = null;
    S.from = null;
    S.to = null;
    S.god = false;
    S.pickMode = "A";
    S.lastResult = null;
  }

  window.RoutingTest = { open: open, close: close };
})();
