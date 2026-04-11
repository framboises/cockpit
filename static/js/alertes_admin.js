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
    "schedule_proximity": "Horaire - proximite",
    "schedule_transition": "Horaire - transition",
    "traffic_cluster": "Trafic - cluster",
    "anpr_watchlist": "LAPI - plaque",
    "meteo_threshold": "Meteo - seuil",
    "meteo_rain_onset": "Meteo - pluie imminente",
    "checkpoint_reassign": "Controle acces - reaffectation",
    "checkpoint_error_burst": "Controle acces - erreurs",
    "pcorg_urgency": "Main courante - urgence"
  };

  var allGroups = [];

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
      form.querySelector('[name="priority"]').value = def.priority || 99;
      form.querySelector('[name="enabled"]').checked = !!def.enabled;
      populateGroupCheckboxes(def.groups || []);
      if(window.WaAdmin) WaAdmin.setDefValues(def.whatsapp || {});
      _syncParamsUI(def.detection_type || "", def.params || {});
    } else {
      $("#alert-def-modal-title").textContent = "Nouvelle alerte";
      form.querySelector('[name="_id"]').value = "";
      form.querySelector('[name="slug"]').disabled = false;
      form.querySelector('[name="params"]').value = "{}";
      form.querySelector('[name="enabled"]').checked = true;
      populateGroupCheckboxes([]);
      if(window.WaAdmin) WaAdmin.setDefValues({});
      _syncParamsUI("", {});
    }
    modal.hidden = false;
  }

  function populateGroupCheckboxes(selectedIds){
    var container = $("#alert-def-groups-checkboxes");
    container.textContent = "";
    var SYSTEM_LABELS = {"__default__": "Defaut (sans groupe)", "__admin__": "Admin"};
    allGroups.forEach(function(g){
      var displayName = SYSTEM_LABELS[g.name] || g.name;
      var lbl = document.createElement("label");
      lbl.style.cssText = "display:flex; align-items:center; gap:6px; padding:4px 0; cursor:pointer;";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = g._id;
      cb.checked = selectedIds.indexOf(g._id) >= 0;
      var dot = document.createElement("span");
      dot.style.cssText = "width:10px; height:10px; border-radius:50%; background:" + escHtml(g.color || "#6366f1") + ";";
      lbl.appendChild(cb);
      lbl.appendChild(dot);
      lbl.appendChild(document.createTextNode(" " + displayName));
      container.appendChild(lbl);
    });
  }

  // ── Params visuels pcorg_urgency ──

  var _pcorgSelectedLevel = "UA";

  function _syncParamsUI(detectionType, params) {
    var rawRow = $("#params-raw-row");
    var pcorgRow = $("#params-pcorg-row");
    if (!rawRow || !pcorgRow) return;

    if (detectionType === "pcorg_urgency") {
      rawRow.style.display = "none";
      pcorgRow.style.display = "";
      var p = params || {};
      var catSelect = $("#pcorg-param-category");
      if (catSelect) catSelect.value = p.category || "PCO.";
      _pcorgSelectedLevel = p.min_level || "UA";
      _renderLevelBtns();
    } else {
      rawRow.style.display = "";
      pcorgRow.style.display = "none";
    }
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

  function _collectPcorgParams() {
    var catSelect = $("#pcorg-param-category");
    return {
      category: catSelect ? catSelect.value : "PCO.",
      min_level: _pcorgSelectedLevel || "UA"
    };
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
