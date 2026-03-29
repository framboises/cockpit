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
  var pcorgMarkers = {}; // {id: L.marker} pour ouvrir les popups programmatiquement
  var pickCallback = null;
  var ignoredPins = {}; // IDs des interventions dont le bounce est ignore

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
    loadPcorgConfig();

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
        closeIntervention(d.id);
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
      icon: L.divIcon({ className: "", html: pinHtml, iconSize: [36, 36], iconAnchor: [18, 36] })
    }).addTo(detailMiniMap);
    setTimeout(function () { detailMiniMap.invalidateSize(); }, 150);
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
            lastData.open.forEach(function (item) { ignoredPins[item.id] = true; });
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
      var opVal = mkEl("span", ""); opVal.textContent = item.operator || "?";
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
      var chronoLoading = mkEl("div", "pcorg-popup-chrono-loading");
      chronoLoading.textContent = "...";
      chronoDiv.appendChild(chronoLoading);
      popBody.appendChild(chronoDiv);

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
      if (item.status_code !== 10) {
        var closePopBtn = mkEl("button", "pcorg-popup-btn pcorg-popup-btn-danger");
        closePopBtn.appendChild(matIcon("check_circle"));
        closePopBtn.appendChild(document.createTextNode(" Clore"));
        closePopBtn.addEventListener("click", (function (id) {
          return function () { closeIntervention(id); };
        })(item.id));
        popBtns.appendChild(closePopBtn);
      }

      // Ignore bounce button (only if bouncing)
      if (isOld && !ignoredPins[item.id]) {
        var ignoreBtn = mkEl("button", "pcorg-popup-btn pcorg-popup-btn-muted");
        ignoreBtn.appendChild(matIcon("notifications_off"));
        ignoreBtn.appendChild(document.createTextNode(" Ignorer"));
        ignoreBtn.addEventListener("click", (function (id) {
          return function () {
            ignoredPins[id] = true;
            refresh();
            showToast("info", "Alerte ignoree");
          };
        })(item.id));
        popBtns.appendChild(ignoreBtn);
      }

      popBody.appendChild(popBtns);
      popupDiv.appendChild(popBody);

      // Bounce animation for interventions > 1h (unless ignored)
      var shouldBounce = isOld && !ignoredPins[item.id];

      var marker = L.marker([item.lat, item.lon], {
        icon: icon,
        bounceOnAdd: false
      }).bindPopup(popupDiv, { className: "pcorg-popup-wrap", maxWidth: 440, minWidth: 380 })
        .addTo(pcorgMapLayer);

      pcorgMarkers[item.id] = marker;

      if (shouldBounce) {
        marker.getElement().classList.add("pcorg-pin-bounce");
      }

      // Lazy load details + chronology on popup open
      marker.on("popupopen", (function (itemId, sDiv, cDiv, color, cat) {
        return function () {
          if (cDiv._loaded) return;
          cDiv._loaded = true;
          fetch("/api/pcorg/detail/" + encodeURIComponent(itemId))
            .then(function (r) { return r.json(); })
            .then(function (d) {
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
                cDiv.appendChild(row);
              });
            })
            .catch(function () { cDiv.textContent = ""; });
        };
      })(item.id, specDiv, chronoDiv, st.color, item.category));
    });
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

  // ── Create modal ───────────────────────────────────────────────────────────
  var createModal, createOverlay, createMiniMap;
  var createLat = null, createLon = null;
  var createSelectedCat = "";

  // Listes de reference chargees depuis la config
  var pcorgConfig = { sous_classifications: {}, intervenants: [], services: [] };

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

  function showCreate() { createModal.classList.add("show"); createOverlay.classList.add("show"); }
  function hideCreate() {
    createModal.classList.remove("show"); createOverlay.classList.remove("show");
    if (createMiniMap) { createMiniMap.remove(); createMiniMap = null; }
  }

  function initCreateModal() {
    createModal = document.getElementById("pcorgCreateModal");
    createOverlay = document.getElementById("pcorgCreateOverlay");
    var btn = document.getElementById("pcorg-add-btn");
    var closeBtn = document.getElementById("pcorgCreateClose");
    var cancelBtn = document.getElementById("pcorgCreateCancel");
    var form = document.getElementById("pcorgCreateForm");
    var repickBtn = document.getElementById("pcorg-create-repick");
    var radioCheck = document.getElementById("pcorg-c-radio");
    var radioCanal = document.getElementById("pcorg-c-radio-canal");

    if (!createModal || !btn) return;

    // Close
    closeBtn.addEventListener("click", hideCreate);
    cancelBtn.addEventListener("click", hideCreate);
    createOverlay.addEventListener("click", hideCreate);

    // Radio canal toggle
    radioCheck.addEventListener("change", function () {
      radioCanal.style.display = radioCheck.checked ? "" : "none";
    });

    // Step 1: click "+" -> pick on map first
    btn.addEventListener("click", function () {
      showToast("info", "Cliquez sur la carte pour positionner l'intervention");
      if (window.CockpitMapView && window.CockpitMapView.currentView() !== "map") {
        window.CockpitMapView.switchView("map");
      }
      startGpsPick(function (lat, lon) {
        createLat = lat;
        createLon = lon;
        openCreateWithPosition(lat, lon);
      });
    });

    // Repick
    repickBtn.addEventListener("click", function () {
      hideCreate();
      startGpsPick(function (lat, lon) {
        createLat = lat;
        createLon = lon;
        openCreateWithPosition(lat, lon);
      });
    });

    // Build category buttons
    buildCatButtons();

    // Submit
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      submitCreate();
    });
  }

  function openCreateWithPosition(lat, lon) {
    // Update header
    var header = document.getElementById("pcorg-create-header");
    var st = createSelectedCat ? catStyle(createSelectedCat) : { color: "var(--brand)", icon: "add_circle" };
    header.style.background = st.color;
    document.getElementById("pcorg-create-pos-label").textContent =
      lat.toFixed(5) + ", " + lon.toFixed(5);

    // Update GPS display
    document.getElementById("pcorg-create-lat-display").textContent = lat.toFixed(6);
    document.getElementById("pcorg-create-lon-display").textContent = lon.toFixed(6);

    // Mini map
    var mapDiv = document.getElementById("pcorg-create-minimap");
    if (createMiniMap) { createMiniMap.remove(); createMiniMap = null; }
    showCreate();
    setTimeout(function () {
      createMiniMap = L.map(mapDiv, {
        center: [lat, lon], zoom: 17, zoomControl: false,
        attributionControl: false, dragging: false, scrollWheelZoom: false,
        doubleClickZoom: false, touchZoom: false
      });
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(createMiniMap);
      L.marker([lat, lon], {
        icon: L.divIcon({
          className: "",
          html: "<div class='pcorg-pin' style='background:var(--brand)'><span class='material-symbols-outlined'>add_location</span></div>",
          iconSize: [36, 36], iconAnchor: [18, 36]
        })
      }).addTo(createMiniMap);
      createMiniMap.invalidateSize();
    }, 150);
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
    // Update buttons
    document.querySelectorAll(".pcorg-create-cat-btn").forEach(function (b) {
      var isSel = b.getAttribute("data-cat") === cat;
      b.classList.toggle("selected", isSel);
      b.style.borderColor = isSel ? st.color : "";
      b.style.background = isSel ? st.color : "";
    });
    // Update header color
    var header = document.getElementById("pcorg-create-header");
    header.style.background = st.color;
    header.querySelector(".pcorg-fiche-icon").textContent = st.icon;
    header.querySelector(".pcorg-fiche-cat").textContent = "Nouvelle " + shortCat(cat);
    // Build specific fields
    buildSpecificCreateFields(cat);
  }

  function buildSpecificCreateFields(cat) {
    var container = document.getElementById("pcorg-create-specific");
    container.textContent = "";

    // Sous-classification from config
    var subs = extractLabels((pcorgConfig.sous_classifications || {})[cat]);
    if (subs.length > 0) {
      addCreateSelect(container, "pcorg-c-sous", "Sous-classification", subs);
    }

    var intervList = extractLabels(pcorgConfig.intervenants);
    var serviceList = extractLabels(pcorgConfig.services);

    // Category-specific fields
    if (cat === "PCO.Secours" || cat === "PCO.Securite" || cat === "PCO.Technique") {
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
      addCreateField(container, "pcorg-c-carroye", "Carroye");
    } else if (cat === "PCO.Fourriere") {
      addCreateField(container, "pcorg-c-lieu", "Lieu");
      addCreateField(container, "pcorg-c-detailsvl", "Vehicule (marque, couleur, modele)");
      addCreateField(container, "pcorg-c-immat", "Immatriculation");
      addCreateSelect(container, "pcorg-c-typedemande", "Type de demande",
        ["Parking sauvage", "Pas de titre", "Mauvais titre (sticker ou badge)", "Stationnement genant", "Autre"]);
      addCreateSelect(container, "pcorg-c-decision", "Decision",
        ["Remorquage demande", "Sabot pose", "Avertissement", "Annule"]);
    } else if (cat === "PCO.Flux") {
      if (intervList.length) {
        addCreateSelect(container, "pcorg-c-moyens1", "Moyens engages Niv.1", intervList);
        addCreateSelect(container, "pcorg-c-moyens2", "Moyens engages Niv.2", intervList);
      } else {
        addCreateField(container, "pcorg-c-moyens1", "Moyens engages Niv.1");
        addCreateField(container, "pcorg-c-moyens2", "Moyens engages Niv.2");
      }
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
    if (!createSelectedCat) {
      showToast("warning", "Selectionnez une categorie");
      return;
    }
    var text = document.getElementById("pcorg-c-text").value.trim();
    if (!text) {
      showToast("warning", "La description est obligatoire");
      return;
    }
    var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};

    // Build content_category
    var cc = {};
    var appelant = document.getElementById("pcorg-c-appelant").value.trim();
    if (appelant) cc.appelant = appelant;
    var telCheck = document.getElementById("pcorg-c-tel");
    if (telCheck && telCheck.checked) cc.telephone = true;
    var radioCheck = document.getElementById("pcorg-c-radio");
    var radioCanal = document.getElementById("pcorg-c-radio-canal");
    if (radioCheck && radioCheck.checked) {
      cc.radio = radioCanal.value.trim() || true;
    }

    // Sous-classification
    var sousEl = document.getElementById("pcorg-c-sous");
    if (sousEl && sousEl.value) cc.sous_classification = sousEl.value;

    // Category-specific
    var cat = createSelectedCat;
    if (cat === "PCO.Secours" || cat === "PCO.Securite" || cat === "PCO.Technique") {
      var i1 = getVal("pcorg-c-interv1"); if (i1) cc.intervenant1 = i1;
      var i2 = getVal("pcorg-c-interv2"); if (i2) cc.intervenant2 = i2;
      var svc = getVal("pcorg-c-service"); if (svc) cc.service_contacte = svc;
      var carr = getVal("pcorg-c-carroye"); if (carr) cc.carroye = carr;
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

    var payload = {
      event: ey.event,
      year: ey.year,
      category: cat,
      text: text,
      area_desc: document.getElementById("pcorg-c-area").value.trim(),
      content_category: cc,
      lat: createLat,
      lon: createLon
    };

    apiPost("/api/pcorg/create", payload)
      .then(function (r) {
        if (r.ok) {
          hideCreate();
          document.getElementById("pcorgCreateForm").reset();
          createSelectedCat = "";
          document.querySelectorAll(".pcorg-create-cat-btn").forEach(function (b) {
            b.classList.remove("selected"); b.style.borderColor = ""; b.style.background = "";
          });
          document.getElementById("pcorg-create-specific").textContent = "";
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

  // ── Bootstrap ──────────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", init);
})();
