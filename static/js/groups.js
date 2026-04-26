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
  var catRegistry = [];

  var UserAPI = {
    list: function(){ return fetch("/api/cockpit-users").then(function(r){ return r.json(); }); },
    setGroups: function(uid, groups){ return fetch("/api/cockpit-users/"+uid+"/groups", {method:"PUT", headers:jsonHeaders(), body:JSON.stringify({groups:groups})}).then(function(r){ return r.json(); }); }
  };
  var MorningReportAPI = {
    get: function(){ return fetch("/api/pcorg/morning-report/prefs").then(function(r){ return r.json(); }); },
    set: function(uid, enabled){ return fetch("/api/pcorg/morning-report/prefs", {method:"PUT", headers:jsonHeaders(), body:JSON.stringify({user_id: uid, enabled: !!enabled})}).then(function(r){ return r.json(); }); },
    setGlobal: function(enabled){ return fetch("/api/pcorg/morning-report/prefs", {method:"PUT", headers:jsonHeaders(), body:JSON.stringify({global_enabled: !!enabled})}).then(function(r){ return r.json(); }); }
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

  // --- Block layout UI (deux colonnes gauche/droite + visibilite) ---

  function _buildBlockItem(b, selectedBlocks, side){
    var item = el("div", {"class":"block-layout-item", "data-block-id": b.id});
    if(selectedBlocks && selectedBlocks.indexOf(b.id) < 0) item.classList.add("unchecked");
    var cb = document.createElement("input");
    cb.type = "checkbox"; cb.value = b.id; cb.name = "allowed_blocks";
    cb.style.accentColor = "var(--accent)";
    if(!selectedBlocks || selectedBlocks.indexOf(b.id) >= 0) cb.checked = true;
    cb.addEventListener("change", function(){
      item.classList.toggle("unchecked", !cb.checked);
    });
    var lbl = el("span", {"class":"block-label"}, b.label);
    item.appendChild(cb);
    item.appendChild(lbl);
    // Bouton deplacement vers l'autre colonne
    var arrow = el("button", {"class":"block-move-btn", "data-action":"switch", type:"button", title: side === "left" ? "Deplacer a droite" : "Deplacer a gauche"});
    arrow.textContent = side === "left" ? "\u2192" : "\u2190";
    item.appendChild(arrow);
    // Boutons haut/bas
    var up = el("button", {"class":"block-move-btn", "data-action":"up", type:"button", title:"Monter"});
    up.textContent = "\u2191";
    var down = el("button", {"class":"block-move-btn", "data-action":"down", type:"button", title:"Descendre"});
    down.textContent = "\u2193";
    item.appendChild(up);
    item.appendChild(down);
    return item;
  }

  function populateBlockCheckboxes(selectedBlocks, blockLayout){
    var container = $("#group-blocks-checkboxes");
    var fixedContainer = $("#group-blocks-fixed");
    if(!container) return;
    container.textContent = "";
    if(fixedContainer) fixedContainer.textContent = "";

    // Separer blocs deplacables et fixes
    var movable = blockRegistry.filter(function(b){ return b.default_column !== null && b.default_column !== undefined; });
    var fixed = blockRegistry.filter(function(b){ return b.default_column === null || b.default_column === undefined; });

    // Determiner layout courant
    var leftIds, rightIds;
    if(blockLayout && (blockLayout.left || blockLayout.right)){
      leftIds = blockLayout.left || [];
      rightIds = blockLayout.right || [];
      // Ajouter les blocs deplacables manquants a leur colonne par defaut
      movable.forEach(function(b){
        if(leftIds.indexOf(b.id) < 0 && rightIds.indexOf(b.id) < 0){
          if(b.default_column === "left") leftIds.push(b.id);
          else rightIds.push(b.id);
        }
      });
    } else {
      leftIds = movable.filter(function(b){ return b.default_column === "left"; }).map(function(b){ return b.id; });
      rightIds = movable.filter(function(b){ return b.default_column === "right"; }).map(function(b){ return b.id; });
    }

    // Construire les deux colonnes
    var colLeft = el("div", {"class":"block-layout-col layout-col-left"});
    var titleLeft = el("div", {"class":"block-layout-col-title"}, "Gauche");
    colLeft.appendChild(titleLeft);

    var colRight = el("div", {"class":"block-layout-col layout-col-right"});
    var titleRight = el("div", {"class":"block-layout-col-title"}, "Droite");
    colRight.appendChild(titleRight);

    function findBlock(id){ return movable.find(function(b){ return b.id === id; }); }

    leftIds.forEach(function(id){
      var b = findBlock(id);
      if(b) colLeft.appendChild(_buildBlockItem(b, selectedBlocks, "left"));
    });
    rightIds.forEach(function(id){
      var b = findBlock(id);
      if(b) colRight.appendChild(_buildBlockItem(b, selectedBlocks, "right"));
    });

    container.appendChild(colLeft);
    container.appendChild(colRight);

    // Delegation evenements — remplacer le handler precedent
    if(container._blockHandler) container.removeEventListener("click", container._blockHandler);
    container._blockHandler = function(e){
      var btn = e.target.closest(".block-move-btn");
      if(!btn) return;
      var item = btn.closest(".block-layout-item");
      if(!item) return;
      var action = btn.getAttribute("data-action");
      var col = item.closest(".block-layout-col");
      if(!col) return;

      if(action === "up"){
        var prev = item.previousElementSibling;
        while(prev && !prev.classList.contains("block-layout-item")) prev = prev.previousElementSibling;
        if(prev) col.insertBefore(item, prev);
      } else if(action === "down"){
        var next = item.nextElementSibling;
        if(next) col.insertBefore(next, item);
      } else if(action === "switch"){
        var isLeft = col.classList.contains("layout-col-left");
        var target = isLeft ? $(".layout-col-right", container) : $(".layout-col-left", container);
        if(!target) return;
        var switchBtn = item.querySelector('[data-action="switch"]');
        if(switchBtn){
          switchBtn.textContent = isLeft ? "\u2190" : "\u2192";
          switchBtn.title = isLeft ? "Deplacer a gauche" : "Deplacer a droite";
        }
        target.appendChild(item);
      }
    };
    container.addEventListener("click", container._blockHandler);

    // Blocs fixes (non deplacables)
    if(fixedContainer && fixed.length){
      fixed.forEach(function(b){
        var lbl = document.createElement("label");
        lbl.className = "block-fixed-item";
        var cb = document.createElement("input");
        cb.type = "checkbox"; cb.value = b.id; cb.name = "allowed_blocks";
        cb.style.accentColor = "var(--accent)";
        if(!selectedBlocks || selectedBlocks.indexOf(b.id) >= 0) cb.checked = true;
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(" " + b.label));
        fixedContainer.appendChild(lbl);
      });
    }
  }

  function getSelectedBlocks(){
    var checks = $$('input[name="allowed_blocks"]:checked', $("#group-form"));
    if(!checks.length) return null;
    return checks.map(function(cb){ return cb.value; });
  }

  function getBlockLayout(){
    var container = $("#group-blocks-checkboxes");
    if(!container) return null;
    var left = [], right = [];
    $$('.layout-col-left .block-layout-item', container).forEach(function(el){
      left.push(el.getAttribute('data-block-id'));
    });
    $$('.layout-col-right .block-layout-item', container).forEach(function(el){
      right.push(el.getAttribute('data-block-id'));
    });
    return (left.length || right.length) ? {left: left, right: right} : null;
  }

  // --- Category checkboxes ---
  function populateCatCheckboxes(selectedCats){
    var container = $("#group-cats-checkboxes");
    if(!container) return;
    container.textContent = "";
    catRegistry.forEach(function(c){
      var label = document.createElement("label");
      label.className = "block-checkbox-label";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = c.id;
      cb.name = "allowed_categories";
      cb.style.accentColor = "var(--accent)";
      if(!selectedCats || selectedCats.indexOf(c.id) >= 0) cb.checked = true;
      label.appendChild(cb);
      label.appendChild(document.createTextNode(" " + c.label));
      container.appendChild(label);
    });
  }

  function getSelectedCats(){
    var checks = $$('input[name="allowed_categories"]:checked', $("#group-form"));
    if(!checks.length) return null;
    var all = checks.length === catRegistry.length;
    return all ? null : checks.map(function(cb){ return cb.value; });
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

  var morningReportEnabled = {};

  function renderUsers(users){
    currentUsers = users;
    var usersTbody = $("tbody", $("#users-table"));
    usersTbody.textContent = "";

    if(!users.length){
      var tr = el("tr"); var td = el("td", {colspan:"6", "class":"muted"}, "Aucun utilisateur");
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

      // Morning report opt-in checkbox
      var tdMorning = el("td", {"class": "morning-report-cell", style: "text-align:center;"});
      var morningCb = document.createElement("input");
      morningCb.type = "checkbox";
      morningCb.title = "Recevoir le rapport matinal automatique a 07h00";
      morningCb.checked = !!morningReportEnabled[u._id];
      morningCb.addEventListener("change", function(){
        var on = morningCb.checked;
        morningCb.disabled = true;
        MorningReportAPI.set(u._id, on).then(function(res){
          morningCb.disabled = false;
          if(res && res.ok){
            morningReportEnabled[u._id] = on;
            showToast("success", on ? "Inscrit au rapport matinal" : "Desinscrit du rapport matinal");
          } else {
            morningCb.checked = !on;
            showToast("error", (res && res.error) || "Erreur");
          }
        });
      });
      tdMorning.appendChild(morningCb);

      tr.appendChild(tdName);
      tr.appendChild(tdEmail);
      tr.appendChild(tdService);
      tr.appendChild(tdRole);
      tr.appendChild(tdGroups);
      tr.appendChild(tdMorning);
      usersTbody.appendChild(tr);
    });
  }

  // Fermer les dropdowns quand on clique en dehors
  document.addEventListener("click", function(e){
    if(!e.target.closest(".group-dropdown-wrap")){
      $$(".group-dropdown-panel").forEach(function(p){ p.style.display = "none"; });
    }
  });

  function applyMorningReportGlobalUi(globalEnabled){
    var cb = document.getElementById("morning-report-global");
    var stateLbl = document.getElementById("morning-report-global-state");
    var warning = document.getElementById("morning-report-warning");
    var master = document.getElementById("morning-report-master");
    if (cb) cb.checked = !!globalEnabled;
    if (stateLbl) stateLbl.textContent = globalEnabled
      ? "Active : la tache planifiee de 7h envoie le rapport aux utilisateurs cochés ci-dessous."
      : "Desactive : la tache de 7h ne fait rien.";
    if (warning) warning.style.display = globalEnabled ? "none" : "block";
    if (master) master.classList.toggle("is-disabled", !globalEnabled);
  }

  // Bind du toggle global (delegation pour eviter de rebinder a chaque refresh)
  document.addEventListener("change", function(e){
    if (e.target && e.target.id === "morning-report-global"){
      var on = e.target.checked;
      e.target.disabled = true;
      MorningReportAPI.setGlobal(on).then(function(res){
        e.target.disabled = false;
        if (res && res.ok){
          applyMorningReportGlobalUi(on);
          showToast("success", on ? "Rapport matinal active" : "Rapport matinal desactive");
        } else {
          e.target.checked = !on;
          showToast("error", (res && res.error) || "Erreur");
        }
      });
    }
  });

  function refreshAll(){
    Promise.all([GroupAPI.list(), UserAPI.list(), MorningReportAPI.get()]).then(function(results){
      renderGroups(results[0]);
      var prefs = results[2] || {};
      morningReportEnabled = {};
      (prefs.enabled_user_ids || []).forEach(function(uid){ morningReportEnabled[uid] = true; });
      applyMorningReportGlobalUi(!!prefs.enabled);
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
    var fsRow = document.getElementById("group-fs-row");
    if(fsRow) fsRow.style.display = "";
    var fsCheck = groupForm.elements.fiche_simplifiee;
    if(fsCheck) fsCheck.checked = false;
    populateBlockCheckboxes(null, null);
    var catRow = document.getElementById("group-cat-row");
    if(catRow) catRow.style.display = "";
    populateCatCheckboxes(null);
    var closeRow = document.getElementById("group-close-row");
    if(closeRow) closeRow.style.display = "";
    var closeCheck = groupForm.elements.can_close_fiche;
    if(closeCheck) closeCheck.checked = false;
    openGroupModal();
  });

  groupModalSave.addEventListener("click", function(){
    var name = (groupForm.elements.name.value || "").trim();
    var description = (groupForm.elements.description.value || "").trim();
    var color = groupForm.elements.color.value;
    var id = groupForm.elements._id.value;

    if(!name){ showToast("warning", "Le nom est requis."); return; }

    var allowed = getSelectedBlocks();
    var layout = getBlockLayout();
    var fsCheck = groupForm.elements.fiche_simplifiee;
    var allowedCats = getSelectedCats();
    var closeCheck = groupForm.elements.can_close_fiche;
    var payload = {name: name, description: description, color: color, allowed_blocks: allowed,
                   block_layout: layout, fiche_simplifiee: !!(fsCheck && fsCheck.checked),
                   can_close_fiche: !!(closeCheck && closeCheck.checked),
                   allowed_categories: allowedCats};
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
      // Fiche simplifiee (visible pour admin et groupes normaux, cache pour defaut)
      var fsRow = document.getElementById("group-fs-row");
      if(fsRow) fsRow.style.display = isDef ? "none" : "";
      var fsCheck = groupForm.elements.fiche_simplifiee;
      if(fsCheck) fsCheck.checked = !!(g.fiche_simplifiee);
      populateBlockCheckboxes(g.allowed_blocks || null, g.block_layout || null);
      // Categories
      var catRow = document.getElementById("group-cat-row");
      if(catRow) catRow.style.display = isAdm ? "none" : "";
      populateCatCheckboxes(g.allowed_categories || null);
      // Cloture fiches
      var closeRow = document.getElementById("group-close-row");
      if(closeRow) closeRow.style.display = isDef ? "none" : "";
      var closeCheck = groupForm.elements.can_close_fiche;
      if(closeCheck) closeCheck.checked = !!(g.can_close_fiche);
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
    $$('input[name="allowed_blocks"]', $("#group-form")).forEach(function(cb){
      cb.checked = true;
      var item = cb.closest(".block-layout-item");
      if(item) item.classList.remove("unchecked");
    });
  });
  if(btnUncheckAll) btnUncheckAll.addEventListener("click", function(){
    $$('input[name="allowed_blocks"]', $("#group-form")).forEach(function(cb){
      cb.checked = false;
      var item = cb.closest(".block-layout-item");
      if(item) item.classList.add("unchecked");
    });
  });

  // SQL default group select
  var sqlGroupSelect = $("#sql-default-group");
  function loadSqlDefaultGroup(){
    if(!sqlGroupSelect) return;
    fetch("/api/groups/sql-default").then(function(r){ return r.json(); }).then(function(d){
      sqlGroupSelect.value = d.group_id || "";
    }).catch(function(){});
  }
  function populateSqlGroupSelect(){
    if(!sqlGroupSelect) return;
    var current = sqlGroupSelect.value;
    sqlGroupSelect.textContent = "";
    var none = document.createElement("option");
    none.value = ""; none.textContent = "-- Aucun --";
    sqlGroupSelect.appendChild(none);
    currentGroups.filter(function(g){ return !isSystemGroup(g); }).forEach(function(g){
      var opt = document.createElement("option");
      opt.value = g._id; opt.textContent = g.name;
      sqlGroupSelect.appendChild(opt);
    });
    sqlGroupSelect.value = current;
  }
  if(sqlGroupSelect){
    sqlGroupSelect.addEventListener("change", function(){
      fetch("/api/groups/sql-default", {method:"PUT", headers:jsonHeaders(), body:JSON.stringify({group_id:sqlGroupSelect.value})})
        .then(function(r){ return r.json(); })
        .then(function(r){
          if(r.ok) showToast("success", "Groupe SQL mis a jour");
          else showToast("error", r.error || "Erreur");
        });
    });
  }

  // Override refreshAll to also populate SQL group select
  var _origRefreshAll = refreshAll;
  refreshAll = function(){
    _origRefreshAll();
    setTimeout(function(){ populateSqlGroupSelect(); loadSqlDefaultGroup(); }, 500);
  };

  // Initial load: fetch block registry + category registry then groups/users
  Promise.all([
    GroupAPI.registry().catch(function(){ return []; }),
    fetch("/api/pco-category-registry").then(function(r){ return r.json(); }).catch(function(){ return []; })
  ]).then(function(results){
    blockRegistry = results[0];
    catRegistry = results[1];
    refreshAll();
  });

})();
