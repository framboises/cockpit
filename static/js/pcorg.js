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

    // Context menu on map
    buildContextMenu();
    var ctxRetry = setInterval(function () {
      var m = getMap();
      if (!m) return;
      clearInterval(ctxRetry);
      m.on("contextmenu", onMapContextMenu);
    }, 2000);
  }

  // ── Context menu ──────────────────────────────────────────────────────────
  var ctxMenu = null;
  var ctxLat = null, ctxLon = null;

  function buildContextMenu() {
    ctxMenu = mkEl("div", "pcorg-ctx-menu");
    ctxMenu.id = "pcorg-ctx-menu";

    var title = mkEl("div", "pcorg-ctx-title");
    title.appendChild(matIcon("add_circle"));
    var titleTxt = mkEl("span", ""); titleTxt.textContent = "Nouvelle intervention";
    title.appendChild(titleTxt);
    ctxMenu.appendChild(title);

    var sep = mkEl("div", "pcorg-ctx-sep");
    ctxMenu.appendChild(sep);

    CATEGORY_ORDER.forEach(function (cat) {
      var st = catStyle(cat);
      var item = mkEl("div", "pcorg-ctx-item");
      item.setAttribute("data-cat", cat);

      var dot = mkEl("span", "pcorg-ctx-dot");
      dot.style.background = st.color;
      item.appendChild(dot);

      var ico = matIcon(st.icon, "pcorg-ctx-icon");
      ico.style.color = st.color;
      item.appendChild(ico);

      var label = mkEl("span", "pcorg-ctx-label");
      label.textContent = shortCat(cat);
      item.appendChild(label);

      item.addEventListener("click", function () {
        hideContextMenu();
        openCreateFromContext(ctxLat, ctxLon, cat);
      });
      ctxMenu.appendChild(item);
    });

    document.body.appendChild(ctxMenu);

    // Close on click anywhere
    document.addEventListener("click", function () { hideContextMenu(); });
    document.addEventListener("contextmenu", function (e) {
      // Hide if clicking outside map
      if (ctxMenu.classList.contains("show") && !e.target.closest(".leaflet-container")) {
        hideContextMenu();
      }
    });
  }

  function onMapContextMenu(e) {
    L.DomEvent.preventDefault(e);
    ctxLat = e.latlng.lat;
    ctxLon = e.latlng.lng;

    var map = getMap();
    if (!map) return;
    var pt = map.latLngToContainerPoint(e.latlng);
    var mapEl = map.getContainer();
    var rect = mapEl.getBoundingClientRect();

    ctxMenu.style.left = (rect.left + pt.x) + "px";
    ctxMenu.style.top = (rect.top + pt.y) + "px";
    ctxMenu.classList.add("show");

    // Adjust if overflows viewport
    requestAnimationFrame(function () {
      var menuRect = ctxMenu.getBoundingClientRect();
      if (menuRect.right > window.innerWidth) {
        ctxMenu.style.left = (rect.left + pt.x - menuRect.width) + "px";
      }
      if (menuRect.bottom > window.innerHeight) {
        ctxMenu.style.top = (rect.top + pt.y - menuRect.height) + "px";
      }
    });
  }

  function hideContextMenu() {
    if (ctxMenu) ctxMenu.classList.remove("show");
  }

  function openCreateFromContext(lat, lon, cat) {
    resetCreateWizard();
    showCreate();
    initCreateMap();

    // Wait for map init, then set position + category and jump to step 2
    setTimeout(function () {
      setCreatePosition(lat, lon);
      selectCategory(cat);
      goToStep(2);
    }, 400);
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
        buildEditSpecificFields(editSpecContainer, cat, cc);
      });
      catContainer.appendChild(btn);
    });
    body.appendChild(catContainer);

    // Appelant + contact
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
    var chkTel = mkEl("input", ""); chkTel.type = "checkbox"; chkTel.id = "pcorg-edit-tel";
    chkTel.checked = !!cc.telephone;
    lblTel.appendChild(chkTel); lblTel.appendChild(document.createTextNode("Telephone"));
    coRow.appendChild(lblTel);
    var lblRad = mkEl("label", ""); lblRad.style.cssText = "font-size:0.78rem;display:flex;align-items:center;gap:4px";
    var chkRad = mkEl("input", ""); chkRad.type = "checkbox"; chkRad.id = "pcorg-edit-radio";
    chkRad.checked = !!cc.radio;
    lblRad.appendChild(chkRad); lblRad.appendChild(document.createTextNode("Radio"));
    coRow.appendChild(lblRad);
    var inpCanal = mkEl("input", "form-input"); inpCanal.type = "text"; inpCanal.id = "pcorg-edit-radio-canal";
    inpCanal.placeholder = "Canal..."; inpCanal.style.cssText = "flex:1;display:" + (cc.radio ? "" : "none");
    inpCanal.value = (typeof cc.radio === "string") ? cc.radio : "";
    chkRad.addEventListener("change", function () { inpCanal.style.display = chkRad.checked ? "" : "none"; });
    coRow.appendChild(inpCanal);
    grpContact.appendChild(coRow);
    body.appendChild(grpContact);

    // Action prise (comment obligatoire)
    var actionSec = mkEl("div", "pcorg-fiche-section"); actionSec.textContent = "Action prise"; body.appendChild(actionSec);
    var grpAction = mkEl("div", "form-group");
    var lblAction = mkEl("label", ""); lblAction.textContent = "Consignez l'action ou la modification"; lblAction.setAttribute("for", "pcorg-edit-comment");
    grpAction.appendChild(lblAction);
    var inpAction = mkEl("textarea", "form-input");
    inpAction.id = "pcorg-edit-comment"; inpAction.rows = 2;
    inpAction.placeholder = "Action realisee, modification apportee...";
    grpAction.appendChild(inpAction);
    body.appendChild(grpAction);

    // Category-specific fields
    var specSec = mkEl("div", "pcorg-fiche-section"); specSec.textContent = "Details specifiques"; body.appendChild(specSec);
    var editSpecContainer = mkEl("div", "");
    body.appendChild(editSpecContainer);
    buildEditSpecificFields(editSpecContainer, editCat, cc);

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
      submitFicheEdit(d.id, editCat, cc);
    });
    editActions.appendChild(btnSave);
    body.appendChild(editActions);
  }

  function buildEditSpecificFields(container, cat, cc) {
    container.textContent = "";
    var subs = extractLabels((pcorgConfig.sous_classifications || {})[cat]);
    if (subs.length > 0) {
      addEditSelect(container, "pcorg-edit-sous", "Sous-classification", subs, cc.sous_classification || "");
    }
    var intervList = extractLabels(pcorgConfig.intervenants);
    var serviceList = extractLabels(pcorgConfig.services);

    if (cat === "PCO.Secours" || cat === "PCO.Securite" || cat === "PCO.Technique") {
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
      addEditField(container, "pcorg-edit-carroye", "Carroye", cc.carroye || "");
    } else if (cat === "PCO.Information" || cat === "PCO.MainCourante") {
      addEditField(container, "pcorg-edit-texte", "Texte complementaire", cc.texte || "");
      if (cat === "PCO.MainCourante") {
        var grpAlerte = mkEl("div", "form-group");
        var lblAl = mkEl("label", ""); lblAl.style.cssText = "font-size:0.78rem;display:flex;align-items:center;gap:4px";
        var chkAl = mkEl("input", ""); chkAl.type = "checkbox"; chkAl.id = "pcorg-edit-alerte";
        chkAl.checked = !!cc.alerte;
        lblAl.appendChild(chkAl); lblAl.appendChild(document.createTextNode("Alerte"));
        grpAlerte.appendChild(lblAl);
        container.appendChild(grpAlerte);
      }
    } else if (cat === "PCO.Fourriere") {
      addEditField(container, "pcorg-edit-lieu", "Lieu", cc.lieu || "");
      addEditField(container, "pcorg-edit-detailsvl", "Vehicule", cc.detailsvl || "");
      addEditField(container, "pcorg-edit-immat", "Immatriculation", cc.immat || "");
      addEditSelect(container, "pcorg-edit-typedemande", "Type de demande",
        ["Parking sauvage", "Pas de titre", "Mauvais titre (sticker ou badge)", "Stationnement genant", "Autre"],
        cc.typedemande || "");
      addEditSelect(container, "pcorg-edit-decision", "Decision",
        ["Remorquage demande", "Sabot pose", "Avertissement", "Annule"],
        cc.decision || "");
    } else if (cat === "PCO.Flux") {
      if (intervList.length) {
        addEditSelect(container, "pcorg-edit-moyens1", "Moyens Niv.1", intervList, cc.moyens_engages_niveau_1 || "");
        addEditSelect(container, "pcorg-edit-moyens2", "Moyens Niv.2", intervList, cc.moyens_engages_niveau_2 || "");
      } else {
        addEditField(container, "pcorg-edit-moyens1", "Moyens Niv.1", cc.moyens_engages_niveau_1 || "");
        addEditField(container, "pcorg-edit-moyens2", "Moyens Niv.2", cc.moyens_engages_niveau_2 || "");
      }
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

  function submitFicheEdit(id, editCat, origCc) {
    var comment = (document.getElementById("pcorg-edit-comment").value || "").trim();
    if (!comment) { showToast("warning", "L'action prise est obligatoire"); return; }

    var text = (document.getElementById("pcorg-edit-text").value || "").trim();
    if (!text) { showToast("warning", "La description est obligatoire"); return; }

    var ccUpdate = {};
    var appelant = (document.getElementById("pcorg-edit-appelant").value || "").trim();
    ccUpdate.appelant = appelant;
    var telChecked = document.getElementById("pcorg-edit-tel").checked;
    ccUpdate.telephone = telChecked || false;
    var radChecked = document.getElementById("pcorg-edit-radio").checked;
    ccUpdate.radio = radChecked ? ((document.getElementById("pcorg-edit-radio-canal").value || "").trim() || true) : false;

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

    var payload = { text: text, category: editCat, content_category: ccUpdate };

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

      // Bounce animation for interventions > 1h (unless ignored or created from cockpit)
      var shouldBounce = isOld && !ignoredPins[item.id] && item.server !== "COCKPIT";

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
  var createGrid100On = false;
  var createGrid25On = false;
  var createCarroye = "";

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
    var radioCanal = document.getElementById("pcorg-c-radio-canal");

    if (!createModal || !btn) return;

    closeBtn.addEventListener("click", hideCreate);
    cancelBtn.addEventListener("click", hideCreate);
    createOverlay.addEventListener("click", hideCreate);

    radioCheck.addEventListener("change", function () {
      radioCanal.style.display = radioCheck.checked ? "" : "none";
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
    createCarroye = "";
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

    // Update header
    document.getElementById("pcorg-create-pos-label").textContent =
      (createCarroye ? createCarroye + " - " : "") + lat.toFixed(5) + ", " + lon.toFixed(5);
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
    var telCheck = document.getElementById("pcorg-c-tel");
    if (telCheck && telCheck.checked) cc.telephone = true;
    var radioCheck = document.getElementById("pcorg-c-radio");
    var radioCanal = document.getElementById("pcorg-c-radio-canal");
    if (radioCheck && radioCheck.checked) {
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

    var payload = {
      event: ey.event,
      year: ey.year,
      category: cat,
      text: text,
      area_desc: "",
      content_category: cc,
      comment: getVal("pcorg-c-comment"),
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

  // ── Bootstrap ──────────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", init);
})();
