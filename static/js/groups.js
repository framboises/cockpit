(function(){
  var $ = function(s, r){ return (r||document).querySelector(s); };
  var $$ = function(s, r){ return Array.from((r||document).querySelectorAll(s)); };

  function jsonHeaders(){
    var h = {"Content-Type": "application/json"};
    var m = $('meta[name="csrf-token"]');
    if(m) h["X-CSRFToken"] = m.getAttribute("content");
    return h;
  }

  // ============================================================
  // API
  // ============================================================

  var GroupAPI = {
    list: function(){ return fetch("/api/groups").then(function(r){ return r.json(); }); },
    create: function(d){ return fetch("/api/groups", {method:"POST", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    update: function(id, d){ return fetch("/api/groups/"+id, {method:"PUT", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    remove: function(id){ return fetch("/api/groups/"+id, {method:"DELETE", headers:jsonHeaders()}).then(function(r){ return r.json(); }); },
    registry: function(){ return fetch("/api/block-registry").then(function(r){ return r.json(); }); }
  };

  var blockRegistry = [];

  var UserAPI = {
    list: function(){ return fetch("/api/cockpit-users").then(function(r){ return r.json(); }); },
    setGroups: function(uid, groups){ return fetch("/api/cockpit-users/"+uid+"/groups", {method:"PUT", headers:jsonHeaders(), body:JSON.stringify({groups:groups})}).then(function(r){ return r.json(); }); }
  };

  // ============================================================
  // Collapsible sections
  // ============================================================

  $$("[data-collapsible]").forEach(function(header){
    var section = header.getAttribute("data-section");
    var body = header.nextElementSibling;
    var key = "cockpit-collapse-" + section;

    // Toujours replier a l'ouverture de la page
    header.classList.add("collapsed");
    body.classList.add("collapsed");

    header.addEventListener("click", function(){
      var isCollapsed = header.classList.toggle("collapsed");
      body.classList.toggle("collapsed", isCollapsed);
      localStorage.setItem(key, isCollapsed ? "1" : "0");
    });
  });

  // ============================================================
  // Groups CRUD
  // ============================================================

  var groupsTable = $("#groups-table");
  if(!groupsTable) return;

  var groupsTbody = $("tbody", groupsTable);
  var btnAddGroup = $("#btn-add-group");
  var groupModal = $("#group-modal");
  var groupModalTitle = $("#group-modal-title");
  var groupForm = $("#group-form");
  var groupModalSave = $("#group-modal-save");

  var currentGroups = [];
  var currentUsers = [];

  // --- Block permissions UI ---
  function populateBlockCheckboxes(selectedBlocks){
    var container = $("#group-blocks-checkboxes");
    if(!container) return;
    container.textContent = "";
    blockRegistry.forEach(function(b){
      var label = document.createElement("label");
      label.className = "block-checkbox-label";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = b.id;
      cb.name = "allowed_blocks";
      if(selectedBlocks && selectedBlocks.indexOf(b.id) >= 0) cb.checked = true;
      label.appendChild(cb);
      label.appendChild(document.createTextNode(" " + b.label));
      container.appendChild(label);
    });
  }

  function getSelectedBlocks(){
    var checks = $$('input[name="allowed_blocks"]:checked', $("#group-form"));
    if(!checks.length) return null;
    return checks.map(function(cb){ return cb.value; });
  }

  // --- Traffic alerts UI ---
  var TRAFFIC_ALERT_TYPES = [
    {id: "traffic-cluster", label: "Alerte zone critique (cluster d'incidents)"},
    {id: "opening", label: "Ouverture imminente (30 min)"},
    {id: "opened", label: "Site ouvert au public"},
    {id: "closing", label: "Fermeture imminente (30 min)"},
    {id: "closed", label: "Site ferme au public"}
  ];

  function populateAlertCheckboxes(selectedAlerts){
    var container = $("#group-alert-checkboxes");
    if(!container) return;
    container.textContent = "";
    TRAFFIC_ALERT_TYPES.forEach(function(t){
      var label = document.createElement("label");
      label.className = "block-checkbox-label";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = t.id;
      cb.name = "traffic_alerts";
      if(selectedAlerts && selectedAlerts.indexOf(t.id) >= 0) cb.checked = true;
      label.appendChild(cb);
      label.appendChild(document.createTextNode(" " + t.label));
      container.appendChild(label);
    });
  }

  function getSelectedAlerts(){
    var checks = $$('input[name="traffic_alerts"]:checked', $("#group-form"));
    if(!checks.length) return null;
    return checks.map(function(cb){ return cb.value; });
  }

  function openGroupModal(){ groupModal.removeAttribute("hidden"); }
  function closeGroupModal(){ groupModal.setAttribute("hidden", ""); }
  $$("[data-close]", groupModal).forEach(function(b){ b.addEventListener("click", closeGroupModal); });
  groupModal.addEventListener("click", function(e){ if(e.target === groupModal) closeGroupModal(); });

  function escapeHtml(s){
    return (s||"").replace(/[&<>"']/g, function(c){
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
    });
  }

  // Helper: build a DOM element from tag, attrs, children
  function el(tag, attrs, children){
    var node = document.createElement(tag);
    if(attrs) Object.keys(attrs).forEach(function(k){ node.setAttribute(k, attrs[k]); });
    if(children){
      if(typeof children === "string") node.textContent = children;
      else if(Array.isArray(children)) children.forEach(function(c){ if(c) node.appendChild(c); });
    }
    return node;
  }

  function isDefaultGroup(g){ return g.name === "__default__"; }
  function isAdminGroup(g){ return g.name === "__admin__"; }
  function isSystemGroup(g){ return isDefaultGroup(g) || isAdminGroup(g); }

  function renderGroups(groups){
    currentGroups = groups;
    groupsTbody.textContent = "";
    if(!groups.length){
      var tr = el("tr"); var td = el("td", {colspan:"6", "class":"muted"}, "Aucun groupe");
      tr.appendChild(td); groupsTbody.appendChild(tr);
      return;
    }
    // Trier: groupes systeme en premier (default, admin), puis alphabetique
    var sorted = groups.slice().sort(function(a, b){
      if(isDefaultGroup(a)) return -1;
      if(isDefaultGroup(b)) return 1;
      if(isAdminGroup(a)) return -1;
      if(isAdminGroup(b)) return 1;
      return (a.name || "").localeCompare(b.name || "");
    });
    sorted.forEach(function(g){
      var isDef = isDefaultGroup(g);
      var isAdm = isAdminGroup(g);
      var isSys = isDef || isAdm;
      var tr = el("tr", {"data-id": g._id});
      if(isSys) tr.className = "default-group-row";

      // Color dot
      var tdColor = el("td");
      var dot = el("span", {"class":"group-color-dot", style:"background:"+g.color});
      tdColor.appendChild(dot);

      // Name
      var tdName = el("td");
      var displayName = isDef ? "Par defaut" : isAdm ? "Admin" : g.name;
      var strong = el("strong", null, displayName);
      tdName.appendChild(strong);
      if(isDef){
        tdName.appendChild(document.createTextNode(" "));
        tdName.appendChild(el("span", {"class":"muted", style:"font-size:0.75rem"}, "(utilisateurs sans groupe)"));
      }
      if(isAdm){
        tdName.appendChild(document.createTextNode(" "));
        tdName.appendChild(el("span", {"class":"muted", style:"font-size:0.75rem"}, "(couleur pillule admin)"));
      }

      // Description
      var tdDesc = el("td", null, isSys ? "" : (g.description || ""));

      // Members
      var tdMembers = el("td", null, isSys ? "--" : String(g.member_count || 0));

      // Blocks
      var tdBlocks = el("td");
      if(isAdm){
        tdBlocks.appendChild(el("span", {"class":"muted"}, "--"));
      } else if(!g.allowed_blocks || !g.allowed_blocks.length){
        tdBlocks.appendChild(el("span", {"class":"muted"}, "Tous"));
      } else {
        tdBlocks.textContent = g.allowed_blocks.length + "/" + blockRegistry.length;
      }

      // Actions (icones inline)
      var tdActions = el("td");
      tdActions.className = "group-actions";
      var btnEdit = el("button", {"class":"btn-icon", "data-action":"edit-group", title: isSys ? "Configurer" : "Editer"});
      btnEdit.appendChild(el("span", {"class":"material-symbols-outlined"}, isSys ? "tune" : "edit"));
      tdActions.appendChild(btnEdit);
      if(!isSys){
        var btnDel = el("button", {"class":"btn-icon btn-icon-danger", "data-action":"delete-group", title:"Supprimer"});
        btnDel.appendChild(el("span", {"class":"material-symbols-outlined"}, "delete"));
        tdActions.appendChild(btnDel);
      }

      tr.appendChild(tdColor);
      tr.appendChild(tdName);
      tr.appendChild(tdDesc);
      tr.appendChild(tdMembers);
      tr.appendChild(tdBlocks);
      tr.appendChild(tdActions);
      groupsTbody.appendChild(tr);
    });
  }

  function renderUsers(users){
    currentUsers = users;
    var usersTbody = $("tbody", $("#users-table"));
    usersTbody.textContent = "";

    if(!users.length){
      var tr = el("tr"); var td = el("td", {colspan:"5", "class":"muted"}, "Aucun utilisateur");
      tr.appendChild(td); usersTbody.appendChild(tr);
      return;
    }

    users.forEach(function(u){
      var tr = el("tr", {"data-uid": u._id});

      // Name + titre
      var tdName = el("td");
      var nameStrong = el("strong", null, u.prenom + " " + u.nom);
      tdName.appendChild(nameStrong);
      if(u.titre){
        tdName.appendChild(el("br"));
        var titreSpan = el("span", {"class":"muted", style:"font-size:0.78rem"}, u.titre);
        tdName.appendChild(titreSpan);
      }

      // Email
      var tdEmail = el("td", null, u.email);

      // Service
      var tdService = el("td", null, u.service || "");

      // Role badge
      var tdRole = el("td");
      var roleBadge = el("span", {"class":"role-badge role-" + u.cockpit_role}, u.cockpit_role);
      tdRole.appendChild(roleBadge);

      // Groups cell — dropdown compact avec checkboxes
      var tdGroups = el("td");
      var userGroupIds = u.groups || [];
      var assignableGroups = currentGroups.filter(function(g){ return !isSystemGroup(g); });

      var wrapper = el("div", {"class":"group-dropdown-wrap"});

      // Bouton toggle
      var toggle = el("button", {"class":"btn btn-xs group-dropdown-toggle", type:"button"});
      function updateToggleLabel(){
        var names = [];
        userGroupIds.forEach(function(gid){
          var g = currentGroups.find(function(gr){ return gr._id === gid; });
          if(g && !isSystemGroup(g)) names.push(g.name);
        });
        toggle.textContent = names.length ? names.join(", ") : "Aucun";
        if(!names.length) toggle.classList.add("muted");
        else toggle.classList.remove("muted");
      }
      updateToggleLabel();

      // Panel dropdown
      var panel = el("div", {"class":"group-dropdown-panel"});
      panel.style.display = "none";

      assignableGroups.forEach(function(g){
        var lbl = document.createElement("label");
        lbl.className = "group-dropdown-item";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = g._id;
        if(userGroupIds.indexOf(g._id) >= 0) cb.checked = true;
        var dot = el("span", {"class":"group-color-dot-sm", style:"background:" + g.color});
        lbl.appendChild(cb);
        lbl.appendChild(dot);
        lbl.appendChild(document.createTextNode(" " + g.name));
        panel.appendChild(lbl);

        cb.addEventListener("change", function(){
          var checked = Array.from(panel.querySelectorAll("input:checked")).map(function(c){ return c.value; });
          UserAPI.setGroups(u._id, checked).then(function(res){
            if(res.error){
              showToast("error", res.error);
            } else {
              userGroupIds = checked;
              updateToggleLabel();
              showToast("success", "Groupes mis a jour");
            }
          });
        });
      });

      toggle.addEventListener("click", function(e){
        e.stopPropagation();
        var open = panel.style.display !== "none";
        // Fermer tous les autres panels
        $$(".group-dropdown-panel").forEach(function(p){ p.style.display = "none"; });
        panel.style.display = open ? "none" : "";
      });

      wrapper.appendChild(toggle);
      wrapper.appendChild(panel);
      tdGroups.appendChild(wrapper);
      tr.appendChild(tdName);
      tr.appendChild(tdEmail);
      tr.appendChild(tdService);
      tr.appendChild(tdRole);
      tr.appendChild(tdGroups);
      usersTbody.appendChild(tr);
    });
  }

  // Fermer les dropdowns quand on clique en dehors
  document.addEventListener("click", function(e){
    if(!e.target.closest(".group-dropdown-wrap")){
      $$(".group-dropdown-panel").forEach(function(p){ p.style.display = "none"; });
    }
  });

  function refreshAll(){
    Promise.all([GroupAPI.list(), UserAPI.list()]).then(function(results){
      renderGroups(results[0]);
      renderUsers(results[1]);
    });
  }

  // Group CRUD actions
  btnAddGroup.addEventListener("click", function(){
    groupModalTitle.textContent = "Nouveau groupe";
    groupForm.reset();
    groupForm.elements._id.value = "";
    groupForm.elements.color.value = "#6366f1";
    // Rendre les champs nom/description/couleur visibles
    var formRows = $$(".form-row", groupForm);
    for(var i = 0; i < 3 && i < formRows.length; i++){
      formRows[i].style.display = "";
    }
    populateBlockCheckboxes(null);
    populateAlertCheckboxes(null);
    openGroupModal();
  });

  groupModalSave.addEventListener("click", function(){
    var name = (groupForm.elements.name.value || "").trim();
    var description = (groupForm.elements.description.value || "").trim();
    var color = groupForm.elements.color.value;
    var id = groupForm.elements._id.value;

    if(!name){ showToast("warning", "Le nom est requis."); return; }

    var allowed = getSelectedBlocks();
    var trafficAlerts = getSelectedAlerts();
    var payload = {name: name, description: description, color: color, allowed_blocks: allowed, traffic_alerts: trafficAlerts};
    var promise = id ? GroupAPI.update(id, payload) : GroupAPI.create(payload);
    promise.then(function(res){
      if(res.error){
        showToast("error", res.error);
      } else {
        closeGroupModal();
        refreshAll();
      }
    });
  });

  groupsTbody.addEventListener("click", function(e){
    var btn = e.target.closest("button");
    if(!btn) return;
    var tr = e.target.closest("tr");
    var id = tr ? tr.getAttribute("data-id") : null;
    var action = btn.getAttribute("data-action");

    if(action === "edit-group"){
      var g = currentGroups.find(function(gr){ return gr._id === id; });
      if(!g) return;
      var isDef = isDefaultGroup(g);
      var isAdm = isAdminGroup(g);
      var isSys = isDef || isAdm;
      groupModalTitle.textContent = isDef ? "Blocs par defaut" : isAdm ? "Couleur Admin" : "Editer le groupe";
      groupForm.elements._id.value = g._id;
      groupForm.elements.name.value = g.name;
      groupForm.elements.description.value = g.description || "";
      groupForm.elements.color.value = g.color || "#6366f1";
      // Visibilite des champs selon le type de groupe
      // formRows: [0] Nom, [1] Description, [2] Couleur, [3] Blocs
      var formRows = $$(".form-row", groupForm);
      if(formRows[0]) formRows[0].style.display = isSys ? "none" : "";       // Nom
      if(formRows[1]) formRows[1].style.display = isSys ? "none" : "";       // Description
      if(formRows[2]) formRows[2].style.display = isAdm ? "" : (isDef ? "none" : ""); // Couleur: visible pour admin et normal
      if(formRows[3]) formRows[3].style.display = isAdm ? "none" : "";       // Blocs: cache pour admin
      populateBlockCheckboxes(g.allowed_blocks || null);
      populateAlertCheckboxes(g.traffic_alerts || null);
      openGroupModal();
    }

    if(action === "delete-group"){
      showConfirmToast("Supprimer ce groupe ?", {type:"error", okLabel:"Supprimer"}).then(function(ok){
        if(!ok) return;
        GroupAPI.remove(id).then(function(res){
          if(res.error) showToast("error", res.error);
          else refreshAll();
        });
      });
    }
  });

  // Block check all / uncheck all
  var btnCheckAll = $("#blocks-check-all");
  var btnUncheckAll = $("#blocks-uncheck-all");
  if(btnCheckAll) btnCheckAll.addEventListener("click", function(){
    $$('input[name="allowed_blocks"]', $("#group-form")).forEach(function(cb){ cb.checked = true; });
  });
  if(btnUncheckAll) btnUncheckAll.addEventListener("click", function(){
    $$('input[name="allowed_blocks"]', $("#group-form")).forEach(function(cb){ cb.checked = false; });
  });

  // Initial load: fetch block registry then groups/users
  GroupAPI.registry().then(function(blocks){
    blockRegistry = blocks;
  }).catch(function(){}).then(function(){
    refreshAll();
  });

})();
