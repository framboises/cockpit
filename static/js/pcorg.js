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

  // ── State ──────────────────────────────────────────────────────────────────
  var refreshTimer = null;
  var lastData = null;
  var expandedId = null;
  var pcorgMapLayer = null;
  var pickCallback = null;

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var listOpen, listClosed, statsContainer, badge, placeholderOpen, placeholderClosed;

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

    setTimeout(refresh, 800);
    refreshTimer = setInterval(refresh, REFRESH_MS);

    // Retry pending map pins once map is ready
    var pinRetry = setInterval(function () {
      if (!pendingPins) { clearInterval(pinRetry); return; }
      if (getMap()) { updateMapPins(pendingPins); clearInterval(pinRetry); }
    }, 2000);
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
    fetch("/api/pcorg/live?event=" + encodeURIComponent(ey.event) + "&year=" + encodeURIComponent(ey.year))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        lastData = data;
        renderList(listOpen, data.open || [], false, placeholderOpen);
        renderList(listClosed, data.closed || [], true, placeholderClosed);
        renderStats(data.open || [], data.closed || []);
        syncTabHeights();
        updateBadge(data.counts ? data.counts.open : 0);
        updateMapPins(data.open || []);
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

      var detail = mkEl("div", "pcorg-detail");
      detail.setAttribute("data-id", item.id);

      row.addEventListener("click", (function (id, det, closed) {
        return function () { toggleDetail(id, det, closed); };
      })(item.id, detail, isClosed));

      container.appendChild(row);
      container.appendChild(detail);
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
  var CATEGORY_ORDER = [
    "PCO.Secours", "PCO.Securite", "PCO.Technique",
    "PCO.Flux", "PCO.Information", "PCO.MainCourante", "PCO.Fourriere"
  ];

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
      grid.appendChild(card);
    });

    statsContainer.appendChild(grid);
  }

  // ── Detail expand ──────────────────────────────────────────────────────────
  function toggleDetail(id, detailEl, isClosed) {
    if (detailEl.classList.contains("open")) {
      detailEl.classList.remove("open");
      expandedId = null;
      return;
    }
    document.querySelectorAll(".pcorg-detail.open").forEach(function (d) {
      d.classList.remove("open");
    });
    expandedId = id;
    detailEl.textContent = "";
    var loadingMsg = mkEl("em", "");
    loadingMsg.textContent = "Chargement...";
    detailEl.appendChild(loadingMsg);
    detailEl.classList.add("open");

    fetch("/api/pcorg/detail/" + encodeURIComponent(id))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        detailEl.textContent = "";
        if (d.error) {
          var errEl = mkEl("em", "");
          errEl.textContent = d.error;
          detailEl.appendChild(errEl);
          return;
        }
        buildDetailContent(detailEl, d, isClosed);
      })
      .catch(function () {
        detailEl.textContent = "";
        var errEl = mkEl("em", "");
        errEl.textContent = "Erreur de chargement";
        detailEl.appendChild(errEl);
      });
  }

  function addDetailField(parent, label, value) {
    if (!value) return;
    var lbl = mkEl("div", "pcorg-detail-label");
    lbl.textContent = label;
    parent.appendChild(lbl);
    var val = mkEl("div", "");
    val.textContent = value;
    parent.appendChild(val);
  }

  function buildDetailContent(container, d, isClosed) {
    if (d.text_full && d.text_full !== d.text) {
      addDetailField(container, "Description complete", d.text_full);
    }
    if (d.comment) {
      var lbl = mkEl("div", "pcorg-detail-label");
      lbl.textContent = "Commentaire";
      container.appendChild(lbl);
      var val = mkEl("div", "");
      // Comment may have newlines
      d.comment.split("\n").forEach(function (line, i) {
        if (i > 0) container.appendChild(document.createElement("br"));
        var t = document.createTextNode(line);
        val.appendChild(t);
      });
      container.appendChild(val);
    }
    addDetailField(container, "Classification", d.sous_classification);
    addDetailField(container, "Appelant", d.appelant);
    if (d.intervenants && d.intervenants.length) {
      addDetailField(container, "Intervenants", d.intervenants.join(", "));
    }
    addDetailField(container, "Groupe", d.group_desc);

    var opStr = d.operator || "?";
    if (d.operator_close && d.operator_close !== d.operator) {
      opStr += " / clos par " + d.operator_close;
    }
    addDetailField(container, "Operateur", opStr);

    if (d.ts) {
      addDetailField(container, "Ouverture", new Date(d.ts).toLocaleString("fr-FR"));
    }
    if (d.close_ts) {
      addDetailField(container, "Cloture", new Date(d.close_ts).toLocaleString("fr-FR"));
    }

    // Action buttons
    var actions = mkEl("div", "pcorg-detail-actions");

    if (d.lat != null) {
      var btnMap = mkEl("button", "");
      btnMap.appendChild(matIcon("map"));
      btnMap.appendChild(document.createTextNode(" Voir sur carte"));
      btnMap.addEventListener("click", function () { flyToPin(d.lat, d.lon); });
      actions.appendChild(btnMap);
    } else {
      var btnGps = mkEl("button", "");
      btnGps.appendChild(matIcon("add_location"));
      btnGps.appendChild(document.createTextNode(" Ajouter position"));
      btnGps.addEventListener("click", function () { openGpsModal(d.id); });
      actions.appendChild(btnGps);
    }

    if (!isClosed && d.status_code !== 10) {
      var btnClose = mkEl("button", "pcorg-btn-danger");
      btnClose.appendChild(matIcon("check_circle"));
      btnClose.appendChild(document.createTextNode(" Clore"));
      btnClose.addEventListener("click", function () { closeIntervention(d.id); });
      actions.appendChild(btnClose);
    }

    container.appendChild(actions);
  }

  // ── Close intervention ─────────────────────────────────────────────────────
  function closeIntervention(id) {
    if (!confirm("Clore cette intervention ?")) return;
    apiPost("/api/pcorg/close/" + encodeURIComponent(id), {})
      .then(function (r) {
        if (r.ok) {
          refresh();
        } else {
          alert(r.error || "Erreur");
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
        iconSize: [28, 28],
        iconAnchor: [14, 28],
        popupAnchor: [0, -30]
      });

      // Build popup using DOM
      var popupDiv = document.createElement("div");
      popupDiv.style.fontSize = "12px";
      popupDiv.style.maxWidth = "220px";

      var catLine = document.createElement("b");
      catLine.style.color = st.color;
      catLine.textContent = shortCat(item.category);
      popupDiv.appendChild(catLine);

      if (item.sous_classification) {
        popupDiv.appendChild(document.createTextNode(" - " + item.sous_classification));
      }
      popupDiv.appendChild(document.createElement("br"));
      popupDiv.appendChild(document.createTextNode(item.text || ""));

      var zone = truncZone(item.area_desc);
      if (zone) {
        popupDiv.appendChild(document.createElement("br"));
        var em = document.createElement("em");
        em.textContent = zone;
        popupDiv.appendChild(em);
      }
      popupDiv.appendChild(document.createElement("br"));
      var small = document.createElement("small");
      small.textContent = (item.operator || "") + " - " + shortTime(item.ts);
      popupDiv.appendChild(small);

      L.marker([item.lat, item.lon], { icon: icon })
        .bindPopup(popupDiv)
        .addTo(pcorgMapLayer);
    });
  }

  function flyToPin(lat, lon) {
    var map = getMap();
    if (!map) return;
    if (window.CockpitMapView && window.CockpitMapView.currentView() !== "map") {
      window.CockpitMapView.switchView("map");
    }
    setTimeout(function () {
      map.flyTo([lat, lon], 17, { duration: 0.8 });
    }, 300);
  }

  // ── Create modal ───────────────────────────────────────────────────────────
  function initCreateModal() {
    var modal = document.getElementById("pcorgCreateModal");
    var btn = document.getElementById("pcorg-add-btn");
    var closeBtn = document.getElementById("pcorgCreateClose");
    var cancelBtn = document.getElementById("pcorgCreateCancel");
    var form = document.getElementById("pcorgCreateForm");
    var pickBtn = document.getElementById("pcorg-pick-gps");

    if (!modal || !btn) return;

    btn.addEventListener("click", function () { modal.classList.add("active"); });
    closeBtn.addEventListener("click", function () { modal.classList.remove("active"); });
    cancelBtn.addEventListener("click", function () { modal.classList.remove("active"); });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.classList.remove("active");
    });

    pickBtn.addEventListener("click", function () {
      modal.classList.remove("active");
      startGpsPick(function (lat, lon) {
        document.getElementById("pcorg-lat").value = lat.toFixed(6);
        document.getElementById("pcorg-lon").value = lon.toFixed(6);
        modal.classList.add("active");
      });
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};
      var cat = document.getElementById("pcorg-cat").value;
      var text = document.getElementById("pcorg-text").value.trim();
      if (!cat || !text) {
        alert("Categorie et description sont obligatoires");
        return;
      }
      var lat = document.getElementById("pcorg-lat").value;
      var lon = document.getElementById("pcorg-lon").value;
      var payload = {
        event: ey.event,
        year: ey.year,
        category: cat,
        text: text,
        sous_classification: document.getElementById("pcorg-sous").value.trim(),
        area_desc: document.getElementById("pcorg-area").value.trim(),
        lat: lat ? parseFloat(lat) : null,
        lon: lon ? parseFloat(lon) : null
      };
      apiPost("/api/pcorg/create", payload)
        .then(function (r) {
          if (r.ok) {
            modal.classList.remove("active");
            form.reset();
            refresh();
          } else {
            alert(r.error || "Erreur");
          }
        });
    });
  }

  // ── GPS modal ──────────────────────────────────────────────────────────────
  function initGpsModal() {
    var modal = document.getElementById("pcorgGpsModal");
    var closeBtn = document.getElementById("pcorgGpsClose");
    var cancelBtn = document.getElementById("pcorgGpsCancel");
    var saveBtn = document.getElementById("pcorgGpsSave");
    var pickBtn = document.getElementById("pcorg-gps-pick");

    if (!modal) return;

    closeBtn.addEventListener("click", function () { modal.classList.remove("active"); });
    cancelBtn.addEventListener("click", function () { modal.classList.remove("active"); });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.classList.remove("active");
    });

    pickBtn.addEventListener("click", function () {
      modal.classList.remove("active");
      startGpsPick(function (lat, lon) {
        document.getElementById("pcorg-gps-lat").value = lat.toFixed(6);
        document.getElementById("pcorg-gps-lon").value = lon.toFixed(6);
        modal.classList.add("active");
      });
    });

    saveBtn.addEventListener("click", function () {
      var id = document.getElementById("pcorg-gps-id").value;
      var lat = document.getElementById("pcorg-gps-lat").value;
      var lon = document.getElementById("pcorg-gps-lon").value;
      if (!lat || !lon) {
        alert("Latitude et longitude requises");
        return;
      }
      apiPost("/api/pcorg/update-gps/" + encodeURIComponent(id), {
        lat: parseFloat(lat),
        lon: parseFloat(lon)
      }).then(function (r) {
        if (r.ok) {
          modal.classList.remove("active");
          refresh();
        } else {
          alert(r.error || "Erreur");
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
    modal.classList.add("active");
  }

  // ── GPS pick on map ────────────────────────────────────────────────────────
  function startGpsPick(callback) {
    var map = getMap();
    if (!map) {
      alert("Carte non disponible");
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

  // ── Bootstrap ──────────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", init);
})();
