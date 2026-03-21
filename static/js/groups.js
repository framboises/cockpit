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
    remove: function(id){ return fetch("/api/groups/"+id, {method:"DELETE", headers:jsonHeaders()}).then(function(r){ return r.json(); }); }
  };

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

  function renderGroups(groups){
    currentGroups = groups;
    groupsTbody.textContent = "";
    if(!groups.length){
      var tr = el("tr"); var td = el("td", {colspan:"5", "class":"muted"}, "Aucun groupe");
      tr.appendChild(td); groupsTbody.appendChild(tr);
      return;
    }
    groups.forEach(function(g){
      var tr = el("tr", {"data-id": g._id});

      // Color dot
      var tdColor = el("td");
      var dot = el("span", {"class":"group-color-dot", style:"background:"+g.color});
      tdColor.appendChild(dot);

      // Name
      var tdName = el("td");
      var strong = el("strong", null, g.name);
      tdName.appendChild(strong);

      // Description
      var tdDesc = el("td", null, g.description || "");

      // Members
      var tdMembers = el("td", null, String(g.member_count || 0));

      // Actions
      var tdActions = el("td");
      var btnEdit = el("button", {"class":"btn btn-xs", "data-action":"edit-group"}, "Editer");
      var btnDel = el("button", {"class":"btn btn-xs btn-danger", "data-action":"delete-group"}, "Supprimer");
      tdActions.appendChild(btnEdit);
      tdActions.appendChild(document.createTextNode(" "));
      tdActions.appendChild(btnDel);

      tr.appendChild(tdColor);
      tr.appendChild(tdName);
      tr.appendChild(tdDesc);
      tr.appendChild(tdMembers);
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

      // Groups cell
      var tdGroups = el("td");
      var cell = el("div", {"class":"user-groups-cell"});
      var badges = el("div", {"class":"user-groups-badges"});

      var userGroupIds = u.groups || [];
      if(userGroupIds.length === 0){
        badges.appendChild(el("span", {"class":"muted"}, "Aucun"));
      } else {
        userGroupIds.forEach(function(gid){
          var g = currentGroups.find(function(gr){ return gr._id === gid; });
          if(!g) return;
          var badge = el("span", {"class":"group-badge", style:"background:" + g.color}, g.name);
          badges.appendChild(badge);
          badges.appendChild(document.createTextNode(" "));
        });
      }
      cell.appendChild(badges);

      // Multi-select dropdown
      if(currentGroups.length > 0){
        var sel = document.createElement("select");
        sel.className = "group-select";
        sel.multiple = true;
        sel.setAttribute("data-uid", u._id);
        currentGroups.forEach(function(g){
          var opt = document.createElement("option");
          opt.value = g._id;
          opt.textContent = g.name;
          if(userGroupIds.indexOf(g._id) >= 0) opt.selected = true;
          sel.appendChild(opt);
        });
        sel.addEventListener("change", function(){
          var uid = sel.getAttribute("data-uid");
          var selected = Array.from(sel.selectedOptions).map(function(o){ return o.value; });
          UserAPI.setGroups(uid, selected).then(function(res){
            if(res.error){
              showToast("error", res.error);
            } else {
              showToast("success", "Groupes mis a jour");
              refreshAll();
            }
          });
        });
        cell.appendChild(sel);
      }

      tdGroups.appendChild(cell);
      tr.appendChild(tdName);
      tr.appendChild(tdEmail);
      tr.appendChild(tdService);
      tr.appendChild(tdRole);
      tr.appendChild(tdGroups);
      usersTbody.appendChild(tr);
    });
  }

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
    openGroupModal();
  });

  groupModalSave.addEventListener("click", function(){
    var name = (groupForm.elements.name.value || "").trim();
    var description = (groupForm.elements.description.value || "").trim();
    var color = groupForm.elements.color.value;
    var id = groupForm.elements._id.value;

    if(!name){ showToast("warning", "Le nom est requis."); return; }

    var payload = {name: name, description: description, color: color};
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
      groupModalTitle.textContent = "Editer le groupe";
      groupForm.elements._id.value = g._id;
      groupForm.elements.name.value = g.name;
      groupForm.elements.description.value = g.description || "";
      groupForm.elements.color.value = g.color || "#6366f1";
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

  // Initial load
  refreshAll();

})();
