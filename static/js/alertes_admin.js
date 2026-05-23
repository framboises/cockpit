(function(){
  var $ = function(s, r){ return (r||document).querySelector(s); };
  var $$ = function(s, r){ return Array.from((r||document).querySelectorAll(s)); };

  function jsonHeaders(){
    var h = {"Content-Type": "application/json"};
    var m = $('meta[name="csrf-token"]');
    if(m) h["X-CSRFToken"] = m.getAttribute("content");
    return h;
  }

  function escHtml(s){
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // ============================================================
  // API
  // ============================================================

  var DefAPI = {
    list: function(){ return fetch("/api/alert-definitions").then(function(r){ return r.json(); }); },
    create: function(d){ return fetch("/api/alert-definitions", {method:"POST", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    update: function(id, d){ return fetch("/api/alert-definitions/"+id, {method:"PUT", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    remove: function(id){ return fetch("/api/alert-definitions/"+id, {method:"DELETE", headers:jsonHeaders()}).then(function(r){ return r.json(); }); }
  };

  var WatchAPI = {
    list: function(){ return fetch("/api/anpr-watchlist").then(function(r){ return r.json(); }); },
    create: function(d){ return fetch("/api/anpr-watchlist", {method:"POST", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    update: function(id, d){ return fetch("/api/anpr-watchlist/"+id, {method:"PUT", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    remove: function(id){ return fetch("/api/anpr-watchlist/"+id, {method:"DELETE", headers:jsonHeaders()}).then(function(r){ return r.json(); }); }
  };

  var GroupAPI = {
    list: function(){ return fetch("/api/groups").then(function(r){ return r.json(); }); }
  };

  var DETECTION_TYPE_LABELS = {
    "camera_event": "Camera Hik - Smart Event",
    "schedule_proximity": "Horaire - proximite",
    "schedule_transition": "Horaire - transition",
    "traffic_cluster": "Trafic - cluster",
    "anpr_watchlist": "LAPI - plaque (deprecie)",
    "meteo_threshold": "Meteo - seuil",
    "meteo_rain_onset": "Meteo - pluie imminente",
    "checkpoint_reassign": "Controle acces - reaffectation",
    "checkpoint_error_burst": "Controle acces - erreurs",
    "pcorg_urgency": "Main courante - urgence"
  };

  var allGroups = [];
  var allCameras = [];
  var allCameraEventTypes = [];
  var _modalPrefill = null;  // {event_types: [...], cameras: [...]} pour pre-remplir depuis la console

  // ============================================================
  // Collapsible sections
  // ============================================================

  $$("[data-collapsible]").forEach(function(header){
    var body = header.nextElementSibling;
    header.addEventListener("click", function(){
      var isCollapsed = header.classList.toggle("collapsed");
      body.classList.toggle("collapsed", isCollapsed);
    });
  });

  // ============================================================
  // Definitions d'alertes
  // ============================================================

  function renderDefs(defs){
    var tbody = $("#alert-defs-table tbody");
    tbody.textContent = "";
    defs.forEach(function(d){
      var tr = document.createElement("tr");
      // Icone
      var tdIcon = document.createElement("td");
      var iconEl = document.createElement("span");
      iconEl.className = "material-symbols-outlined";
      iconEl.style.cssText = "color:" + escHtml(d.color || "#6366f1") + "; font-size:22px;";
      iconEl.textContent = d.icon || "notifications";
      tdIcon.appendChild(iconEl);
      tr.appendChild(tdIcon);
      // Nom
      var tdName = document.createElement("td");
      tdName.style.fontWeight = "600";
      tdName.textContent = d.name;
      tr.appendChild(tdName);
      // Description
      var tdDesc = document.createElement("td");
      tdDesc.textContent = d.description || "";
      tdDesc.style.color = "var(--muted)";
      tdDesc.style.fontSize = "0.85rem";
      tr.appendChild(tdDesc);
      // Type
      var tdType = document.createElement("td");
      var typeBadge = document.createElement("span");
      typeBadge.className = "badge";
      typeBadge.style.cssText = "font-size:0.72rem; padding:2px 8px; border-radius:10px; background:var(--surface-2); color:var(--text-secondary);";
      typeBadge.textContent = DETECTION_TYPE_LABELS[d.detection_type] || d.detection_type;
      tdType.appendChild(typeBadge);
      if(d.whatsapp && d.whatsapp.enabled){
        var waBadge = document.createElement("span");
        waBadge.className = "badge";
        waBadge.style.cssText = "font-size:0.68rem; padding:1px 6px; border-radius:8px; margin-left:4px; background:#25D36622; color:#25D366;";
        waBadge.textContent = "WA";
        waBadge.title = "Notification WhatsApp active";
        tdType.appendChild(waBadge);
      }
      tr.appendChild(tdType);
      // Groupes
      var tdGroups = document.createElement("td");
      var SYS_LABELS = {"__default__": "Defaut", "__admin__": "Admin"};
      if(d.groups && d.groups.length > 0){
        d.groups.forEach(function(gid){
          var g = allGroups.find(function(x){ return x._id === gid; });
          if(g){
            var badge = document.createElement("span");
            badge.className = "badge";
            var c = escHtml(g.color || "#6366f1");
            badge.style.cssText = "font-size:0.72rem; padding:2px 8px; border-radius:10px; margin-right:4px; background:" + c + "22; color:" + c + "; border:1px solid " + c + "44;";
            badge.textContent = SYS_LABELS[g.name] || g.name;
            tdGroups.appendChild(badge);
          }
        });
      } else {
        var allSpan = document.createElement("span");
        allSpan.style.cssText = "color:var(--muted); font-size:0.8rem;";
        allSpan.textContent = "Tous";
        tdGroups.appendChild(allSpan);
      }
      tr.appendChild(tdGroups);
      // Toggle actif
      var tdEnabled = document.createElement("td");
      tdEnabled.className = "col-shrink";
      var toggle = document.createElement("button");
      toggle.className = "btn-icon";
      toggle.title = d.enabled ? "Desactiver" : "Activer";
      var toggleIcon = document.createElement("span");
      toggleIcon.className = "material-symbols-outlined";
      toggleIcon.style.color = d.enabled ? "#22c55e" : "#94a3b8";
      toggleIcon.textContent = d.enabled ? "toggle_on" : "toggle_off";
      toggle.appendChild(toggleIcon);
      toggle.addEventListener("click", function(){
        DefAPI.update(d._id, {enabled: !d.enabled}).then(loadAll);
      });
      tdEnabled.appendChild(toggle);
      tr.appendChild(tdEnabled);
      // Actions
      var tdAct = document.createElement("td");
      tdAct.className = "col-shrink col-actions";
      var btnEdit = document.createElement("button");
      btnEdit.className = "btn-icon";
      btnEdit.title = "Modifier";
      var editIcon = document.createElement("span");
      editIcon.className = "material-symbols-outlined";
      editIcon.style.fontSize = "18px";
      editIcon.textContent = "edit";
      btnEdit.appendChild(editIcon);
      btnEdit.addEventListener("click", function(){ openDefModal(d); });
      var btnDel = document.createElement("button");
      btnDel.className = "btn-icon";
      btnDel.title = "Supprimer";
      var delIcon = document.createElement("span");
      delIcon.className = "material-symbols-outlined";
      delIcon.style.cssText = "font-size:18px; color:#ef4444;";
      delIcon.textContent = "delete";
      btnDel.appendChild(delIcon);
      btnDel.addEventListener("click", function(){
        showConfirmToast("Supprimer l'alerte '" + d.name + "' ?").then(function(ok){
          if(ok) DefAPI.remove(d._id).then(loadAll);
        });
      });
      tdAct.appendChild(btnEdit);
      tdAct.appendChild(btnDel);
      tr.appendChild(tdAct);
      tbody.appendChild(tr);
    });
  }

  // ============================================================
  // Modal definition
  // ============================================================

  function openDefModal(def){
    var modal = $("#alert-def-modal");
    var form = $("#alert-def-form");
    form.reset();
    if(def){
      $("#alert-def-modal-title").textContent = "Modifier l'alerte";
      form.querySelector('[name="_id"]').value = def._id;
      form.querySelector('[name="slug"]').value = def.slug || "";
      form.querySelector('[name="slug"]').disabled = true;
      form.querySelector('[name="name"]').value = def.name || "";
      form.querySelector('[name="description"]').value = def.description || "";
      form.querySelector('[name="icon"]').value = def.icon || "notifications";
      form.querySelector('[name="color"]').value = def.color || "#6366f1";
      form.querySelector('[name="detection_type"]').value = def.detection_type || "";
      form.querySelector('[name="params"]').value = def.params ? JSON.stringify(def.params, null, 2) : "";
      _setPriorityFromStored(def.priority != null ? def.priority : 3);
      form.querySelector('[name="enabled"]').checked = !!def.enabled;
      populateGroupCheckboxes(def.groups || []);
      if(window.WaAdmin) WaAdmin.setDefValues(def.whatsapp || {});
      _syncParamsUI(def.detection_type || "", def.params || {});
      _syncActiveBanner();
    } else {
      $("#alert-def-modal-title").textContent = "Nouvelle alerte";
      form.querySelector('[name="_id"]').value = "";
      form.querySelector('[name="slug"]').disabled = false;
      form.querySelector('[name="params"]').value = "{}";
      _setPriorityFromStored(3);
      form.querySelector('[name="enabled"]').checked = true;
      populateGroupCheckboxes([]);
      if(window.WaAdmin) WaAdmin.setDefValues({});
      // Pre-selectionner camera_event (cas d'usage principal) pour que la
      // modale s'ouvre directement sur les chips Smart Events au lieu du JSON.
      var dtSelect = form.querySelector('[name="detection_type"]');
      if (dtSelect) {
        dtSelect.value = "camera_event";
      }
      _syncParamsUI("camera_event", {});
      _syncActiveBanner();
    }
    modal.hidden = false;
  }

  function populateGroupCheckboxes(selectedIds){
    var container = $("#alert-def-groups-checkboxes");
    container.textContent = "";
    var SYSTEM_LABELS = {"__default__": "Defaut (sans groupe)", "__admin__": "Admin"};
    if(!allGroups.length){
      var empty = document.createElement("div");
      empty.className = "checklist-empty";
      empty.textContent = "Aucun groupe configure.";
      container.appendChild(empty);
      return;
    }
    allGroups.forEach(function(g){
      var displayName = SYSTEM_LABELS[g.name] || g.name;
      var lbl = document.createElement("label");
      lbl.className = "checklist-item";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = g._id;
      cb.checked = selectedIds.indexOf(g._id) >= 0;
      var dot = document.createElement("span");
      dot.className = "checklist-item-dot";
      dot.style.background = g.color || "#6366f1";
      var txt = document.createElement("span");
      txt.className = "checklist-item-label";
      txt.textContent = displayName;
      lbl.appendChild(cb);
      lbl.appendChild(dot);
      lbl.appendChild(txt);
      container.appendChild(lbl);
    });
  }

  // ── Params visuels pcorg_urgency ──

  var _pcorgSelectedLevel = "UA";

  function _syncParamsUI(detectionType, params) {
    var rawRow = $("#params-raw-row");
    var pcorgRow = $("#params-pcorg-row");
    var trafficRow = $("#params-traffic-row");
    var cameraRow = $("#params-camera-row");
    if (!rawRow || !pcorgRow || !trafficRow || !cameraRow) return;

    var hideAll = function(){
      rawRow.style.display = "none";
      pcorgRow.style.display = "none";
      trafficRow.style.display = "none";
      cameraRow.style.display = "none";
    };

    if (detectionType === "pcorg_urgency") {
      hideAll();
      pcorgRow.style.display = "";
      var p = params || {};
      var catSelect = $("#pcorg-param-category");
      if (catSelect) catSelect.value = p.category || "PCO.";
      _pcorgSelectedLevel = p.min_level || "UA";
      _renderLevelBtns();
    } else if (detectionType === "traffic_cluster") {
      hideAll();
      trafficRow.style.display = "";
      var tp = params || {};
      var radiusEl = $("#traffic-param-radius");
      var thresholdEl = $("#traffic-param-threshold");
      if (radiusEl) radiusEl.value = (tp.radius_m != null) ? tp.radius_m : 500;
      if (thresholdEl) thresholdEl.value = (tp.threshold != null) ? tp.threshold : 10;
    } else if (detectionType === "camera_event") {
      hideAll();
      cameraRow.style.display = "";
      _renderCameraEventTypesGrid(params || {});
      _renderCamerasGrid(params || {});
      _restoreCameraFilters(params || {});
    } else {
      hideAll();
      rawRow.style.display = "";
    }
  }

  // ============================================================
  // Section "camera_event" - grilles et collecte
  // ============================================================

  function _ensureCameraData(cb) {
    var needTypes = !allCameraEventTypes.length;
    var needCams = !allCameras.length;
    var pending = (needTypes ? 1 : 0) + (needCams ? 1 : 0);
    if (!pending) return cb && cb();
    function done(){ pending--; if(pending <= 0 && cb) cb(); }
    if (needTypes) {
      fetch("/api/camera-event-types").then(function(r){return r.json();}).then(function(d){
        allCameraEventTypes = Array.isArray(d) ? d : [];
        done();
      }).catch(function(){ done(); });
    }
    if (needCams) {
      fetch("/api/cameras-list").then(function(r){return r.json();}).then(function(d){
        allCameras = Array.isArray(d) ? d : [];
        done();
      }).catch(function(){ done(); });
    }
  }

  function _renderCameraEventTypesGrid(params) {
    var grid = $("#camera-event-types-grid");
    if (!grid) return;
    _ensureCameraData(function(){
      grid.textContent = "";
      if (!allCameraEventTypes.length) {
        var empty = document.createElement("div");
        empty.className = "checklist-empty";
        empty.textContent = "Aucun type d'event Hik disponible.";
        grid.appendChild(empty);
        return;
      }
      var selected = (params.event_types || _modalPrefill && _modalPrefill.event_types || []);
      allCameraEventTypes.forEach(function(t){
        var lbl = document.createElement("label");
        lbl.className = "checklist-item";
        lbl.title = t.desc || "";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = t.id;
        cb.checked = selected.indexOf(t.id) >= 0;
        var iconEl = document.createElement("span");
        iconEl.className = "material-symbols-outlined";
        iconEl.style.cssText = "font-size:18px; color:" + (t.color || "#6b7280") + ";";
        iconEl.textContent = t.icon || "videocam";
        var txt = document.createElement("span");
        txt.className = "checklist-item-label";
        txt.textContent = t.label || t.id;
        lbl.appendChild(cb);
        lbl.appendChild(iconEl);
        lbl.appendChild(txt);
        grid.appendChild(lbl);
      });
    });
  }

  function _renderCamerasGrid(params) {
    var grid = $("#camera-event-cameras-grid");
    if (!grid) return;
    _ensureCameraData(function(){
      grid.textContent = "";
      if (!allCameras.length) {
        var empty = document.createElement("div");
        empty.className = "checklist-empty";
        empty.textContent = "Aucune camera enabled (verifier /admin/cameras).";
        grid.appendChild(empty);
        return;
      }
      var selected = (params.cameras || _modalPrefill && _modalPrefill.cameras || []);
      // Couleur par lieu (deterministe simple)
      var palette = ["#3b82f6", "#10b981", "#f97316", "#a855f7", "#eab308", "#06b6d4", "#ec4899", "#84cc16"];
      var locColors = {};
      allCameras.forEach(function(c){
        var loc = c.location || "(sans lieu)";
        if (!(loc in locColors)) {
          locColors[loc] = palette[Object.keys(locColors).length % palette.length];
        }
      });
      allCameras.forEach(function(c){
        var loc = c.location || "(sans lieu)";
        var lbl = document.createElement("label");
        lbl.className = "checklist-item";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = c.camera_path || c.name;
        cb.checked = selected.indexOf(c.camera_path || c.name) >= 0;
        var dot = document.createElement("span");
        dot.className = "checklist-item-dot";
        dot.style.background = locColors[loc];
        var txt = document.createElement("span");
        txt.className = "checklist-item-label";
        txt.textContent = c.name;
        var sub = document.createElement("span");
        sub.className = "checklist-item-sub";
        sub.textContent = loc;
        lbl.appendChild(cb);
        lbl.appendChild(dot);
        lbl.appendChild(txt);
        lbl.appendChild(sub);
        grid.appendChild(lbl);
      });
      // Consume prefill after first render
      _modalPrefill = null;
    });
  }

  function _restoreCameraFilters(params) {
    var tw = params.time_window || {};
    var twEnabled = $("#camera-time-window-enabled");
    var twStart = $("#camera-time-window-start");
    var twEnd = $("#camera-time-window-end");
    if (twEnabled) twEnabled.checked = !!tw.enabled;
    if (twStart) twStart.value = tw.start || "22:00";
    if (twEnd) twEnd.value = tw.end || "06:00";
    var cdEl = $("#camera-cooldown-min");
    if (cdEl) cdEl.value = String(Math.round((params.cooldown_per_camera_s != null ? params.cooldown_per_camera_s : 300) / 60));
    var minConfEl = $("#camera-min-confidence");
    if (minConfEl) minConfEl.value = String(params.min_confidence != null ? params.min_confidence : 0);
  }

  function _collectCameraParams() {
    var event_types = [];
    $$("#camera-event-types-grid input[type=checkbox]:checked").forEach(function(cb){
      event_types.push(cb.value);
    });
    var cameras = [];
    $$("#camera-event-cameras-grid input[type=checkbox]:checked").forEach(function(cb){
      cameras.push(cb.value);
    });
    var twEnabled = $("#camera-time-window-enabled");
    var twStart = $("#camera-time-window-start");
    var twEnd = $("#camera-time-window-end");
    var cdEl = $("#camera-cooldown-min");
    var minConfEl = $("#camera-min-confidence");
    var cdMin = cdEl ? parseInt(cdEl.value, 10) : 5;
    if (!Number.isFinite(cdMin) || cdMin < 0) cdMin = 5;
    var minConf = minConfEl ? parseInt(minConfEl.value, 10) : 0;
    if (!Number.isFinite(minConf) || minConf < 0) minConf = 0;
    if (minConf > 100) minConf = 100;
    return {
      event_types: event_types,
      cameras: cameras,
      time_window: {
        enabled: !!(twEnabled && twEnabled.checked),
        start: twStart ? twStart.value : "22:00",
        end: twEnd ? twEnd.value : "06:00"
      },
      cooldown_per_camera_s: cdMin * 60,
      min_confidence: minConf
    };
  }

  function _renderLevelBtns() {
    $$("#pcorg-param-levels .pcorg-level-btn").forEach(function(btn) {
      btn.classList.toggle("selected", btn.dataset.level === _pcorgSelectedLevel);
    });
  }

  // Click on level buttons
  document.addEventListener("click", function(e) {
    var btn = e.target.closest(".pcorg-level-btn");
    if (!btn) return;
    e.preventDefault();
    _pcorgSelectedLevel = btn.dataset.level;
    _renderLevelBtns();
  });

  // Toggle when detection_type changes
  var dtSelect = $('[name="detection_type"]', $("#alert-def-form"));
  if (dtSelect) {
    dtSelect.addEventListener("change", function() {
      var current;
      try { current = JSON.parse($('[name="params"]', $("#alert-def-form")).value || "{}"); } catch(e) { current = {}; }
      _syncParamsUI(this.value, current);
    });
  }

  // ── Selecteur de priorite (5 niveaux) ──

  // Mappe une valeur numerique stockee vers le bouton le plus proche.
  // Ordre des niveaux : 1 (Critique), 2 (Haute), 3 (Standard), 10 (Info), 50 (Verbeux)
  function _priorityToButtonValue(num) {
    var n = parseInt(num, 10);
    if (!Number.isFinite(n)) return 3;
    if (n <= 1) return 1;
    if (n <= 2) return 2;
    if (n <= 3) return 3;
    if (n <= 19) return 10;
    return 50;
  }

  function _selectPriorityButton(value) {
    var hidden = $('[name="priority"]', $("#alert-def-form"));
    if (hidden) hidden.value = String(value);
    $$("#priority-level-row .priority-level-btn").forEach(function(btn) {
      btn.classList.toggle("selected", parseInt(btn.dataset.value, 10) === value);
    });
  }

  function _setPriorityFromStored(stored) {
    _selectPriorityButton(_priorityToButtonValue(stored));
  }

  document.addEventListener("click", function(e) {
    var btn = e.target.closest(".priority-level-btn");
    if (!btn) return;
    e.preventDefault();
    _selectPriorityButton(parseInt(btn.dataset.value, 10));
  });

  function _collectPcorgParams() {
    var catSelect = $("#pcorg-param-category");
    return {
      category: catSelect ? catSelect.value : "PCO.",
      min_level: _pcorgSelectedLevel || "UA"
    };
  }

  function _collectTrafficParams() {
    var radiusEl = $("#traffic-param-radius");
    var thresholdEl = $("#traffic-param-threshold");
    var radius = radiusEl ? parseInt(radiusEl.value, 10) : 500;
    var threshold = thresholdEl ? parseInt(thresholdEl.value, 10) : 10;
    if (!Number.isFinite(radius) || radius < 50) radius = 500;
    if (!Number.isFinite(threshold) || threshold < 1) threshold = 10;
    return { radius_m: radius, threshold: threshold };
  }

  function closeDefModal(){
    var modal = $("#alert-def-modal");
    modal.hidden = true;
    var slugInput = $("#alert-def-form").querySelector('[name="slug"]');
    slugInput.disabled = false;
  }

  $("#alert-def-modal-save").addEventListener("click", function(){
    var form = $("#alert-def-form");
    var id = form.querySelector('[name="_id"]').value;
    var detType = form.querySelector('[name="detection_type"]').value;
    var params;
    if (detType === "pcorg_urgency") {
      params = _collectPcorgParams();
    } else if (detType === "traffic_cluster") {
      params = _collectTrafficParams();
    } else if (detType === "camera_event") {
      params = _collectCameraParams();
      if (!params.event_types.length) {
        if(typeof showToast === "function") showToast("Coche au moins un type d'evenement", "error");
        return;
      }
      if (!params.cameras.length) {
        if(typeof showToast === "function") showToast("Coche au moins une camera", "error");
        return;
      }
    } else {
      try {
        params = JSON.parse(form.querySelector('[name="params"]').value || "{}");
      } catch(e){
        if(typeof showToast === "function") showToast("JSON des parametres invalide", "error");
        return;
      }
    }
    var selectedGroups = [];
    $$("#alert-def-groups-checkboxes input[type=checkbox]:checked").forEach(function(cb){
      selectedGroups.push(cb.value);
    });
    var payload = {
      slug: form.querySelector('[name="slug"]').value.trim(),
      name: form.querySelector('[name="name"]').value.trim(),
      description: form.querySelector('[name="description"]').value.trim(),
      icon: form.querySelector('[name="icon"]').value.trim(),
      color: form.querySelector('[name="color"]').value,
      detection_type: form.querySelector('[name="detection_type"]').value,
      params: params,
      priority: parseInt(form.querySelector('[name="priority"]').value) || 99,
      enabled: form.querySelector('[name="enabled"]').checked,
      groups: selectedGroups,
      whatsapp: (window.WaAdmin) ? WaAdmin.getDefValues() : null
    };
    if(!payload.slug || !payload.name){
      if(typeof showToast === "function") showToast("Slug et nom sont requis", "error");
      return;
    }
    var p = id ? DefAPI.update(id, payload) : DefAPI.create(payload);
    p.then(function(res){
      if(res.error){
        if(typeof showToast === "function") showToast(res.error, "error");
        return;
      }
      closeDefModal();
      loadAll();
      if(typeof showToast === "function") showToast(id ? "Alerte modifiee" : "Alerte creee", "success");
    });
  });

  // Fermeture modales
  $$(".crud-modal [data-close]").forEach(function(btn){
    btn.addEventListener("click", function(){
      btn.closest(".crud-modal").hidden = true;
      var slugInput = $("#alert-def-form").querySelector('[name="slug"]');
      if(slugInput) slugInput.disabled = false;
    });
  });

  $("#btn-add-alert-def").addEventListener("click", function(){ openDefModal(null); });

  // Banniere "Alerte active" — etat visuel synchronise avec la case
  function _syncActiveBanner(){
    var banner = $("#alert-def-active-banner");
    var label = $("#alert-def-active-label");
    var cb = $('[name="enabled"]', $("#alert-def-form"));
    if(!banner || !cb) return;
    var on = !!cb.checked;
    banner.classList.toggle("is-on", on);
    if(label) label.textContent = on ? "Alerte active" : "Alerte desactivee";
  }
  (function setupActiveBanner(){
    var cb = $('[name="enabled"]', $("#alert-def-form"));
    if(cb) cb.addEventListener("change", _syncActiveBanner);
    _syncActiveBanner();
  })();

  // ============================================================
  // Watchlist ANPR
  // ============================================================

  function renderWatchlist(items){
    var tbody = $("#anpr-watchlist-table tbody");
    tbody.textContent = "";
    if(!items.length){
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 4;
      td.style.cssText = "text-align:center; color:var(--muted); padding:16px;";
      td.textContent = "Aucune plaque surveillee";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    items.forEach(function(w){
      var tr = document.createElement("tr");
      // Plaque
      var tdPlate = document.createElement("td");
      var plateSpan = document.createElement("span");
      plateSpan.style.cssText = "font-family:monospace; font-weight:700; font-size:1rem; letter-spacing:1px; background:var(--surface-2); padding:4px 10px; border-radius:6px; border:2px solid var(--border);";
      plateSpan.textContent = w.plate;
      tdPlate.appendChild(plateSpan);
      tr.appendChild(tdPlate);
      // Label
      var tdLabel = document.createElement("td");
      tdLabel.textContent = w.label || "";
      tdLabel.style.color = "var(--muted)";
      tr.appendChild(tdLabel);
      // Toggle
      var tdEnabled = document.createElement("td");
      tdEnabled.className = "col-shrink";
      var toggle = document.createElement("button");
      toggle.className = "btn-icon";
      toggle.title = w.enabled ? "Desactiver" : "Activer";
      var toggleIcon = document.createElement("span");
      toggleIcon.className = "material-symbols-outlined";
      toggleIcon.style.color = w.enabled ? "#22c55e" : "#94a3b8";
      toggleIcon.textContent = w.enabled ? "toggle_on" : "toggle_off";
      toggle.appendChild(toggleIcon);
      toggle.addEventListener("click", function(){
        WatchAPI.update(w._id, {enabled: !w.enabled}).then(loadAll);
      });
      tdEnabled.appendChild(toggle);
      tr.appendChild(tdEnabled);
      // Actions
      var tdAct = document.createElement("td");
      tdAct.className = "col-shrink";
      var btnDel = document.createElement("button");
      btnDel.className = "btn-icon";
      btnDel.title = "Retirer";
      var delIcon = document.createElement("span");
      delIcon.className = "material-symbols-outlined";
      delIcon.style.cssText = "font-size:18px; color:#ef4444;";
      delIcon.textContent = "delete";
      btnDel.appendChild(delIcon);
      btnDel.addEventListener("click", function(){
        showConfirmToast("Retirer la plaque " + w.plate + " de la watchlist ?").then(function(ok){
          if(ok) WatchAPI.remove(w._id).then(loadAll);
        });
      });
      tdAct.appendChild(btnDel);
      tr.appendChild(tdAct);
      tbody.appendChild(tr);
    });
  }

  // Modal plaque
  $("#btn-add-plate").addEventListener("click", function(){
    var modal = $("#plate-modal");
    $("#plate-form").reset();
    modal.hidden = false;
  });

  $("#plate-modal-save").addEventListener("click", function(){
    var form = $("#plate-form");
    var plate = form.querySelector('[name="plate"]').value.trim().toUpperCase().replace(/\s+/g, "-");
    var label = form.querySelector('[name="label"]').value.trim();
    if(!plate){
      if(typeof showToast === "function") showToast("La plaque est requise", "error");
      return;
    }
    WatchAPI.create({plate: plate, label: label}).then(function(res){
      if(res.error){
        if(typeof showToast === "function") showToast(res.error, "error");
        return;
      }
      $("#plate-modal").hidden = true;
      loadAll();
      if(typeof showToast === "function") showToast("Plaque ajoutee a la watchlist", "success");
    });
  });

  // ============================================================
  // Console Hik temps reel
  // ============================================================

  var _hikConsoleTimer = null;
  var _hikConsoleWindow = 900;

  function _fmtRelDateTime(iso){
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var nowMs = Date.now();
    var diffS = Math.max(0, Math.round((nowMs - d.getTime()) / 1000));
    var rel;
    if (diffS < 60) rel = "il y a " + diffS + "s";
    else if (diffS < 3600) rel = "il y a " + Math.round(diffS / 60) + " min";
    else if (diffS < 86400) rel = "il y a " + Math.round(diffS / 3600) + " h";
    else rel = "il y a " + Math.round(diffS / 86400) + " j";
    var hh = String(d.getHours()).padStart(2, "0");
    var mm = String(d.getMinutes()).padStart(2, "0");
    var ss = String(d.getSeconds()).padStart(2, "0");
    return hh + ":" + mm + ":" + ss + " (" + rel + ")";
  }

  function renderHikConsole(rows){
    var tbody = $("#hik-console-table tbody");
    if (!tbody) return;
    tbody.textContent = "";
    if (!rows.length) {
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 7;
      td.style.cssText = "text-align:center; color:var(--muted); padding:18px; font-style:italic;";
      td.textContent = "Aucun event recu sur cette fenetre. Verifier qu'ecoutehik2.py tourne et que les cameras pushent.";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    rows.forEach(function(r){
      var tr = document.createElement("tr");
      // Icone event type
      var tdIcon = document.createElement("td");
      tdIcon.className = "col-shrink";
      var iconEl = document.createElement("span");
      iconEl.className = "material-symbols-outlined";
      iconEl.style.cssText = "font-size:22px; color:" + (r.event_color || "#6b7280") + ";";
      iconEl.textContent = r.event_icon || "videocam";
      tdIcon.appendChild(iconEl);
      tr.appendChild(tdIcon);
      // Camera (nom + sous-ligne lieu, "—" si absent pour hauteur uniforme)
      var tdCam = document.createElement("td");
      var camName = document.createElement("div");
      camName.style.cssText = "font-weight:600;";
      camName.textContent = r.camera_name || r.camera_path;
      tdCam.appendChild(camName);
      var locSpan = document.createElement("div");
      locSpan.style.cssText = "font-weight:400; font-size:0.78rem; color:var(--muted);";
      locSpan.textContent = r.camera_location || "—";
      tdCam.appendChild(locSpan);
      tr.appendChild(tdCam);
      // Event type (label + slug technique, "—" si event_type absent)
      var tdEt = document.createElement("td");
      var etLabel = document.createElement("div");
      etLabel.textContent = r.event_label || r.event_type || "—";
      tdEt.appendChild(etLabel);
      var etSub = document.createElement("div");
      etSub.style.cssText = "font-size:0.72rem; color:var(--muted); font-family:ui-monospace, monospace;";
      etSub.textContent = r.event_type || "—";
      tdEt.appendChild(etSub);
      tr.appendChild(tdEt);
      // Count
      var tdCount = document.createElement("td");
      tdCount.className = "col-shrink";
      tdCount.style.cssText = "font-weight:700; text-align:center;";
      tdCount.textContent = String(r.count);
      tr.appendChild(tdCount);
      // Last seen
      var tdLast = document.createElement("td");
      tdLast.style.cssText = "font-size:0.85rem;";
      tdLast.textContent = _fmtRelDateTime(r.last_dt);
      tr.appendChild(tdLast);
      // Snapshot thumbnail
      var tdSnap = document.createElement("td");
      tdSnap.className = "col-shrink";
      if (r.last_id && r.last_snapshot_path) {
        var imgWrap = document.createElement("a");
        imgWrap.href = "/api/hik-events-stream/snapshot/" + encodeURIComponent(r.last_id);
        imgWrap.target = "_blank";
        var img = document.createElement("img");
        img.src = "/api/hik-events-stream/snapshot/" + encodeURIComponent(r.last_id);
        img.alt = "Snapshot";
        img.style.cssText = "width:64px; height:36px; object-fit:cover; border-radius:4px; border:1px solid var(--line); background:#000; cursor:pointer;";
        img.loading = "lazy";
        img.onerror = function(){ this.style.display = "none"; };
        imgWrap.appendChild(img);
        tdSnap.appendChild(imgWrap);
      } else {
        var dash = document.createElement("span");
        dash.style.color = "var(--muted)";
        dash.textContent = "—";
        tdSnap.appendChild(dash);
      }
      tr.appendChild(tdSnap);
      // Action: creer une alerte
      var tdAct = document.createElement("td");
      tdAct.className = "col-shrink col-actions";
      var btn = document.createElement("button");
      btn.className = "btn btn-sm btn-primary";
      btn.style.cssText = "font-size:0.75rem; padding:4px 10px;";
      var btnIcon = document.createElement("span");
      btnIcon.className = "material-symbols-outlined";
      btnIcon.style.cssText = "font-size:14px; vertical-align:middle; margin-right:4px;";
      btnIcon.textContent = "add_alert";
      btn.appendChild(btnIcon);
      btn.appendChild(document.createTextNode("Creer une alerte"));
      btn.addEventListener("click", function(){
        _modalPrefill = {
          event_types: [r.event_type],
          cameras: [r.camera_path],
        };
        openDefModal(null);
        // Force le type de detection a camera_event et synchronise l'UI
        var form = $("#alert-def-form");
        var dtSelect = form.querySelector('[name="detection_type"]');
        if (dtSelect) {
          dtSelect.value = "camera_event";
        }
        var nameInput = form.querySelector('[name="name"]');
        var slugInput = form.querySelector('[name="slug"]');
        if (nameInput && !nameInput.value) {
          nameInput.value = (r.event_label || r.event_type) + " - " + (r.camera_name || r.camera_path);
        }
        if (slugInput && !slugInput.value) {
          var slugBase = (r.event_type + "-" + (r.camera_path || "cam"))
            .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
          slugInput.value = slugBase.slice(0, 64);
        }
        _syncParamsUI("camera_event", {
          event_types: [r.event_type],
          cameras: [r.camera_path],
        });
      });
      tdAct.appendChild(btn);
      tr.appendChild(tdAct);
      tbody.appendChild(tr);
    });
  }

  function loadHikConsole(){
    var statusEl = $("#hik-console-status");
    fetch("/api/hik-events-stream?since=" + encodeURIComponent(_hikConsoleWindow))
      .then(function(r){ return r.json(); })
      .then(function(d){
        var rows = (d && d.rows) || [];
        renderHikConsole(rows);
        if (statusEl) {
          statusEl.textContent = rows.length + " (camera, type) sur la fenetre (mise a jour " + new Date().toLocaleTimeString("fr-FR") + ")";
        }
      })
      .catch(function(err){
        if (statusEl) {
          statusEl.textContent = "Erreur de chargement : " + (err && err.message ? err.message : err);
          statusEl.style.color = "#dc2626";
        }
      });
  }

  function startHikConsolePolling(){
    if (_hikConsoleTimer) clearInterval(_hikConsoleTimer);
    loadHikConsole();
    _hikConsoleTimer = setInterval(loadHikConsole, 5000);
  }

  var hikWinSelect = $("#hik-console-window");
  if (hikWinSelect) {
    hikWinSelect.addEventListener("change", function(){
      _hikConsoleWindow = parseInt(this.value, 10) || 900;
      loadHikConsole();
    });
  }
  var hikRefreshBtn = $("#btn-hik-console-refresh");
  if (hikRefreshBtn) hikRefreshBtn.addEventListener("click", loadHikConsole);

  // ============================================================
  // Chargement initial
  // ============================================================

  function loadAll(){
    Promise.all([DefAPI.list(), WatchAPI.list(), GroupAPI.list()]).then(function(results){
      allGroups = results[2] || [];
      renderDefs(results[0] || []);
      renderWatchlist(results[1] || []);
    });
  }

  loadAll();
  startHikConsolePolling();

  // Sidebar (restore + toggle with memory)
  var sidebar = $("#sidebar");
  if (sidebar) {
    var stored = localStorage.getItem("sidebar-collapsed");
    if (stored === null || stored === "true") sidebar.classList.add("collapsed");
  }
  var toggleBtn = $("#sidebarToggle");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", function () {
      if (!sidebar) return;
      sidebar.classList.toggle("collapsed");
      localStorage.setItem("sidebar-collapsed", sidebar.classList.contains("collapsed"));
    });
  }
})();
