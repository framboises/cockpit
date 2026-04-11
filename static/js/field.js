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
    threePOn: true,
    threePLayer: null,
    threePLoaded: false,
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
        if (r.status === 401) { window.location.href = "/field/pair"; return null; }
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
    var comment = window.prompt("Commentaire sur la fiche :\n" + (f.text || "").slice(0, 80), "");
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
  }

  // ---------------------------------------------------------------------
  // SOS
  // ---------------------------------------------------------------------
  function triggerSos() {
    if (state.sosInFlight) return;
    var ok = window.confirm("Declencher un SOS ?\n\nLe cockpit sera immediatement prevenu avec ta position GPS.");
    if (!ok) return;
    state.sosInFlight = true;
    var note = window.prompt("Note courte (optionnelle) :", "") || "";

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
        note: note.trim(),
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
    $("btn-grid").addEventListener("click", toggleGrid);
    $("btn-inbox").addEventListener("click", function () {
      var p = $("inbox-panel");
      p.hidden = !p.hidden;
    });
    $("inbox-close").addEventListener("click", function () { $("inbox-panel").hidden = true; });
    $("btn-sos").addEventListener("click", triggerSos);
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
    startFichesPoll();
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
    setRouteDestination: setRouteDestination,
    openInGoogleMaps: openInGoogleMaps,
    pollFiches: pollFiches,
    triggerSos: triggerSos,
  };
})();
