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

    // Info row (fields + mini map)
    var infoRow = mkEl("div", "pcorg-fiche-info-row");

    // Fields
    var fields = mkEl("div", "pcorg-fiche-fields");
    addField(fields, "Operateur", d.operator);
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
    addField(fields, "Groupe", d.group_desc);
    infoRow.appendChild(fields);

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

        if (entry.text) {
          var txt = mkEl("div", "pcorg-chrono-text");
          txt.textContent = entry.text;
          ent.appendChild(txt);
        }
        timeline.appendChild(ent);
      });
      body.appendChild(timeline);
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
    if (!isClosed && d.status_code !== 10) {
      var btnClose = mkEl("button", "pcorg-btn-danger");
      btnClose.appendChild(matIcon("check_circle"));
      btnClose.appendChild(document.createTextNode(" Clore"));
      btnClose.addEventListener("click", function () {
        hideFiche(); closeIntervention(d.id);
      });
      actions.appendChild(btnClose);
    }
    body.appendChild(actions);
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
      icon: L.divIcon({ className: "", html: pinHtml, iconSize: [28, 28], iconAnchor: [14, 28] })
    }).addTo(detailMiniMap);
    setTimeout(function () { detailMiniMap.invalidateSize(); }, 150);
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

    btn.addEventListener("click", function () { modal.classList.add("show"); });
    closeBtn.addEventListener("click", function () { modal.classList.remove("show"); });
    cancelBtn.addEventListener("click", function () { modal.classList.remove("show"); });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.classList.remove("show");
    });

    pickBtn.addEventListener("click", function () {
      modal.classList.remove("show");
      startGpsPick(function (lat, lon) {
        document.getElementById("pcorg-lat").value = lat.toFixed(6);
        document.getElementById("pcorg-lon").value = lon.toFixed(6);
        modal.classList.add("show");
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
            modal.classList.remove("show");
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
        alert("Latitude et longitude requises");
        return;
      }
      apiPost("/api/pcorg/update-gps/" + encodeURIComponent(id), {
        lat: parseFloat(lat),
        lon: parseFloat(lon)
      }).then(function (r) {
        if (r.ok) {
          modal.classList.remove("show");
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
    modal.classList.add("show");
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
