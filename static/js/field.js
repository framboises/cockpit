/* =====================================================================
   COCKPIT Field - App terrain pour tablettes patrouille
   Expose en global : window.FieldApp
   ===================================================================== */
(function () {
  "use strict";

  var DEFAULT_CENTER = [47.938561591531936, 0.2243184111156285];
  var DEFAULT_ZOOM = 14;

  var POLL_INBOX_MS = 3000;
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
    threePOn: true,
    threePLayer: null,
    threePLoaded: false,
    inbox: [],
    seenIds: new Set(),
    watchId: null,
    clockTimer: null,
    inboxTimer: null,
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
    fetch("/field/position", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lat: latlng[0],
        lng: latlng[1],
        accuracy: pos.coords.accuracy,
        speed: pos.coords.speed,
        heading: pos.coords.heading,
        battery: state.batteryPct || null,
      }),
    }).catch(function () { /* ignore, on buffer plus tard */ });
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
  function loadGrid() {
    if (state.gridData) {
      renderGrid();
      return;
    }
    fetch("/field/resources/grid-ref", { headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (r.status === 401) { window.location.href = "/field/pair"; return null; }
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
    var group = L.layerGroup();
    hLines.forEach(function (l) {
      L.polyline(
        [[l.lat, l.lng_start], [l.lat, l.lng_end]],
        { color: "#f59e0b", weight: 1.4, opacity: 0.75, interactive: false }
      ).addTo(group);
    });
    vLines.forEach(function (l) {
      L.polyline(
        [[l.lat_start, l.lng], [l.lat_end, l.lng]],
        { color: "#f59e0b", weight: 1.4, opacity: 0.75, interactive: false }
      ).addTo(group);
    });
    group.addTo(state.map);
    state.gridLayer = group;
  }

  function toggleGrid() {
    if (state.gridOn) {
      if (state.gridLayer) {
        state.map.removeLayer(state.gridLayer);
        state.gridLayer = null;
      }
      state.gridOn = false;
      toast("Carroyage : off");
    } else {
      state.gridOn = true;
      loadGrid();
      toast("Carroyage : on");
    }
  }

  function load3P() {
    if (state.threePLoaded) return;
    state.threePLoaded = true;
    fetch("/field/resources/3p", { headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (r.status === 401) { window.location.href = "/field/pair"; return null; }
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
    group.addTo(state.map);
    state.threePLayer = group;
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c];
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
        if (r.status === 401) {
          window.location.href = "/field/pair";
          return null;
        }
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
    newOnes.forEach(function (m) { state.seenIds.add(m.id); });
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
    if (!modal) return;
    title.textContent = m.title || "Message";
    body.textContent = m.body || "";
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
    $("btn-grid").addEventListener("click", toggleGrid);
    $("btn-inbox").addEventListener("click", function () {
      var p = $("inbox-panel");
      p.hidden = !p.hidden;
    });
    $("inbox-close").addEventListener("click", function () { $("inbox-panel").hidden = true; });
    $("btn-sos").addEventListener("click", function () {
      toast("SOS : a venir", "warn");
      // wire-up complet dans commit 8
    });
    $("msg-modal-close").addEventListener("click", function () { $("msg-modal").hidden = true; });
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
    // Ressources carte : 3P visible par defaut, carroyage sur demande
    load3P();

    // Empecher le zoom double-tap / pinch natif
    document.addEventListener("gesturestart", function (e) { e.preventDefault(); });
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
    load3P: load3P,
  };
})();
