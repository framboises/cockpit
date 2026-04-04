/**
 * whatsapp_admin.js - UI admin WhatsApp pour COCKPIT
 * Gestion config, groupes, contacts, historique et integration modal definition.
 */
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

  var WaAPI = {
    getConfig: function(){ return fetch("/api/whatsapp/config").then(function(r){ return r.json(); }); },
    saveConfig: function(d){ return fetch("/api/whatsapp/config", {method:"PUT", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    getStatus: function(){ return fetch("/api/whatsapp/status").then(function(r){ return r.json(); }); },
    sendTest: function(chatId){ return fetch("/api/whatsapp/test", {method:"POST", headers:jsonHeaders(), body:JSON.stringify({chat_id: chatId})}).then(function(r){ return r.json(); }); },
    getGroups: function(){ return fetch("/api/whatsapp/groups").then(function(r){ return r.json(); }); },
    syncGroups: function(){ return fetch("/api/whatsapp/groups/sync", {method:"POST", headers:jsonHeaders()}).then(function(r){ return r.json(); }); },
    updateGroup: function(id, d){ return fetch("/api/whatsapp/groups/"+id, {method:"PUT", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    deleteGroup: function(id){ return fetch("/api/whatsapp/groups/"+id, {method:"DELETE", headers:jsonHeaders()}).then(function(r){ return r.json(); }); },
    clearGroups: function(){ return fetch("/api/whatsapp/groups/clear", {method:"DELETE", headers:jsonHeaders()}).then(function(r){ return r.json(); }); },
    getContacts: function(){ return fetch("/api/whatsapp/contacts").then(function(r){ return r.json(); }); },
    createContact: function(d){ return fetch("/api/whatsapp/contacts", {method:"POST", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    updateContact: function(id, d){ return fetch("/api/whatsapp/contacts/"+id, {method:"PUT", headers:jsonHeaders(), body:JSON.stringify(d)}).then(function(r){ return r.json(); }); },
    deleteContact: function(id){ return fetch("/api/whatsapp/contacts/"+id, {method:"DELETE", headers:jsonHeaders()}).then(function(r){ return r.json(); }); },
    getHistory: function(page, limit){ return fetch("/api/whatsapp/history?page="+(page||1)+"&limit="+(limit||20)).then(function(r){ return r.json(); }); }
  };

  var waGroups = [];
  var waContacts = [];

  // ============================================================
  // Config globale
  // ============================================================

  function loadWaConfig(){
    WaAPI.getConfig().then(function(cfg){
      $("#wa-global-enabled").checked = !!cfg.enabled;
      $("#wa-waha-url").value = cfg.waha_url || "http://localhost:3000";
      var apiKeyEl = $("#wa-api-key");
      if(apiKeyEl) apiKeyEl.value = cfg.api_key || "";
      $("#wa-rate-hour").value = cfg.rate_limit_per_hour || 20;
      $("#wa-rate-day").value = cfg.rate_limit_per_day || 100;
      $("#wa-cooldown").value = cfg.global_cooldown_minutes || 10;
      $("#wa-type-cooldown").value = cfg.type_cooldown_minutes || 30;
      var qh = cfg.quiet_hours || {};
      $("#wa-quiet-enabled").checked = !!qh.enabled;
      $("#wa-quiet-start").value = qh.start || "23:00";
      $("#wa-quiet-end").value = qh.end || "06:00";
    });
  }

  function saveWaConfig(){
    var data = {
      enabled: $("#wa-global-enabled").checked,
      waha_url: $("#wa-waha-url").value.trim(),
      api_key: ($("#wa-api-key") && $("#wa-api-key").value || "").trim(),
      rate_limit_per_hour: parseInt($("#wa-rate-hour").value) || 20,
      rate_limit_per_day: parseInt($("#wa-rate-day").value) || 100,
      global_cooldown_minutes: parseInt($("#wa-cooldown").value) || 10,
      type_cooldown_minutes: parseInt($("#wa-type-cooldown").value) || 30,
      quiet_hours: {
        enabled: $("#wa-quiet-enabled").checked,
        start: $("#wa-quiet-start").value || "23:00",
        end: $("#wa-quiet-end").value || "06:00"
      }
    };
    WaAPI.saveConfig(data).then(function(res){
      if(res.ok){
        showToast("success", "Configuration WhatsApp enregistree");
        loadWaStatus();
      } else {
        showToast("error", res.error || "Erreur");
      }
    });
  }

  var btnSave = $("#btn-wa-save-config");
  if(btnSave) btnSave.addEventListener("click", saveWaConfig);

  // ============================================================
  // Statut session + stats
  // ============================================================

  function loadWaStatus(){
    WaAPI.getStatus().then(function(data){
      var session = data.session || {};
      var stats = data.stats || {};
      var icon = $("#wa-session-icon");
      var text = $("#wa-session-text");
      var badge = $("#wa-status-badge");
      var status = (session.status || "UNKNOWN").toUpperCase();

      if(status === "CONNECTED" || status === "WORKING"){
        icon.style.color = "#22c55e";
        text.textContent = "Session connectee";
        badge.textContent = "Connecte";
        badge.style.background = "#22c55e22";
        badge.style.color = "#22c55e";
      } else if(status === "UNREACHABLE"){
        icon.style.color = "#ef4444";
        text.textContent = "WAHA injoignable";
        badge.textContent = "Hors ligne";
        badge.style.background = "#ef444422";
        badge.style.color = "#ef4444";
      } else {
        icon.style.color = "#f59e0b";
        text.textContent = "Session: " + status;
        badge.textContent = status;
        badge.style.background = "#f59e0b22";
        badge.style.color = "#f59e0b";
      }

      // Stats bar - DOM safe
      var bar = $("#wa-stats-bar");
      if(bar){
        bar.textContent = "";
        var items = [
          {label: "Heure", val: (stats.sent_this_hour || 0) + "/" + (stats.limit_hour || 20)},
          {label: "Jour", val: (stats.sent_today || 0) + "/" + (stats.limit_day || 100)},
          {label: "Erreurs", val: stats.errors_today || 0}
        ];
        if(stats.circuit_breaker === "open"){
          items.push({label: "Circuit breaker", val: "OUVERT"});
        }
        items.forEach(function(it){
          var span = document.createElement("span");
          span.textContent = it.label + ": " + it.val;
          if(it.label === "Circuit breaker") span.style.color = "#ef4444";
          bar.appendChild(span);
        });
      }

      // Pause button
      var btnPause = $("#btn-wa-pause");
      if(btnPause){
        btnPause.hidden = !$("#wa-global-enabled").checked;
      }
    }).catch(function(){
      var badge = $("#wa-status-badge");
      badge.textContent = "Erreur";
      badge.style.background = "#ef444422";
      badge.style.color = "#ef4444";
    });
  }

  // ============================================================
  // Test message
  // ============================================================

  var btnTest = $("#btn-wa-test");
  if(btnTest) btnTest.addEventListener("click", function(){
    showPromptToast("Entrez le chat ID (ex: 33612345678@c.us ou groupId@g.us) :").then(function(chatId){
      if(!chatId) return;
      WaAPI.sendTest(chatId.trim()).then(function(res){
        if(res.ok){
          showToast("success", "Test envoye : " + (res.detail || ""));
        } else {
          showToast("error", "Echec : " + (res.detail || "Erreur"));
        }
      });
    });
  });

  // Pause manuelle
  var btnPause = $("#btn-wa-pause");
  if(btnPause) btnPause.addEventListener("click", function(){
    WaAPI.saveConfig({enabled: false}).then(function(){
      showToast("success", "WhatsApp mis en pause");
      loadWaConfig();
      loadWaStatus();
    });
  });

  // ============================================================
  // Groupes WA
  // ============================================================

  function loadWaGroups(){
    return WaAPI.getGroups().then(function(groups){
      waGroups = groups || [];
      renderWaGroups(waGroups);
      return waGroups;
    });
  }

  function renderWaGroups(groups){
    var tbody = $("#wa-groups-table tbody");
    tbody.textContent = "";
    if(!groups.length){
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 5;
      td.style.cssText = "text-align:center; color:var(--muted); padding:16px;";
      td.textContent = "Aucun groupe. Cliquez sur Synchroniser pour charger les groupes depuis WhatsApp.";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    groups.forEach(function(g){
      var tr = document.createElement("tr");
      // Nom
      var tdName = document.createElement("td");
      tdName.style.fontWeight = "600";
      tdName.textContent = g.name || "";
      tr.appendChild(tdName);
      // ID
      var tdId = document.createElement("td");
      tdId.style.cssText = "font-family:monospace; font-size:0.78rem; color:var(--muted);";
      tdId.textContent = g.group_id || "";
      tr.appendChild(tdId);
      // Participants
      var tdPart = document.createElement("td");
      tdPart.textContent = g.participants_count || "-";
      tr.appendChild(tdPart);
      // Toggle
      var tdEnabled = document.createElement("td");
      tdEnabled.className = "col-shrink";
      var toggle = document.createElement("button");
      toggle.className = "btn-icon";
      var tIcon = document.createElement("span");
      tIcon.className = "material-symbols-outlined";
      tIcon.style.color = g.enabled ? "#22c55e" : "#94a3b8";
      tIcon.textContent = g.enabled ? "toggle_on" : "toggle_off";
      toggle.appendChild(tIcon);
      toggle.addEventListener("click", function(){
        WaAPI.updateGroup(g._id, {enabled: !g.enabled}).then(loadWaGroups);
      });
      tdEnabled.appendChild(toggle);
      tr.appendChild(tdEnabled);
      // Actions
      var tdAct = document.createElement("td");
      tdAct.className = "col-shrink";
      var btnDel = document.createElement("button");
      btnDel.className = "btn-icon";
      btnDel.title = "Supprimer";
      var delIcon = document.createElement("span");
      delIcon.className = "material-symbols-outlined";
      delIcon.style.cssText = "font-size:18px; color:#ef4444;";
      delIcon.textContent = "delete";
      btnDel.appendChild(delIcon);
      btnDel.addEventListener("click", function(){
        showConfirmToast("Retirer le groupe '" + g.name + "' ?").then(function(ok){
          if(ok) WaAPI.deleteGroup(g._id).then(loadWaGroups);
        });
      });
      tdAct.appendChild(btnDel);
      tr.appendChild(tdAct);
      tbody.appendChild(tr);
    });
  }

  var btnSync = $("#btn-wa-sync-groups");
  if(btnSync) btnSync.addEventListener("click", function(){
    showToast("info", "Synchronisation en cours...");
    WaAPI.syncGroups().then(function(res){
      showToast("success", (res.synced || 0) + " groupe(s) synchronise(s)");
      loadWaGroups();
    });
  });

  var btnClearGroups = $("#btn-wa-clear-groups");
  if(btnClearGroups) btnClearGroups.addEventListener("click", function(){
    showConfirmToast("Supprimer tous les groupes ?").then(function(ok){
      if(!ok) return;
      WaAPI.clearGroups().then(function(res){
        showToast("success", (res.deleted || 0) + " groupe(s) supprime(s)");
        loadWaGroups();
      });
    });
  });

  // ============================================================
  // Contacts DM
  // ============================================================

  function loadWaContacts(){
    return WaAPI.getContacts().then(function(contacts){
      waContacts = contacts || [];
      renderWaContacts(waContacts);
      return waContacts;
    });
  }

  function renderWaContacts(contacts){
    var tbody = $("#wa-contacts-table tbody");
    tbody.textContent = "";
    if(!contacts.length){
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 5;
      td.style.cssText = "text-align:center; color:var(--muted); padding:16px;";
      td.textContent = "Aucun contact DM configure.";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    contacts.forEach(function(c){
      var tr = document.createElement("tr");
      // Nom
      var tdName = document.createElement("td");
      tdName.style.fontWeight = "600";
      tdName.textContent = c.name || "";
      tr.appendChild(tdName);
      // Phone
      var tdPhone = document.createElement("td");
      tdPhone.style.fontFamily = "monospace";
      tdPhone.textContent = c.phone || "";
      tr.appendChild(tdPhone);
      // Role
      var tdRole = document.createElement("td");
      tdRole.style.color = "var(--muted)";
      tdRole.textContent = c.role || "";
      tr.appendChild(tdRole);
      // Toggle
      var tdEnabled = document.createElement("td");
      tdEnabled.className = "col-shrink";
      var toggle = document.createElement("button");
      toggle.className = "btn-icon";
      var tIcon = document.createElement("span");
      tIcon.className = "material-symbols-outlined";
      tIcon.style.color = c.enabled ? "#22c55e" : "#94a3b8";
      tIcon.textContent = c.enabled ? "toggle_on" : "toggle_off";
      toggle.appendChild(tIcon);
      toggle.addEventListener("click", function(){
        WaAPI.updateContact(c._id, {enabled: !c.enabled}).then(loadWaContacts);
      });
      tdEnabled.appendChild(toggle);
      tr.appendChild(tdEnabled);
      // Actions
      var tdAct = document.createElement("td");
      tdAct.className = "col-shrink";
      var btnDel = document.createElement("button");
      btnDel.className = "btn-icon";
      btnDel.title = "Supprimer";
      var delIcon = document.createElement("span");
      delIcon.className = "material-symbols-outlined";
      delIcon.style.cssText = "font-size:18px; color:#ef4444;";
      delIcon.textContent = "delete";
      btnDel.appendChild(delIcon);
      btnDel.addEventListener("click", function(){
        showConfirmToast("Supprimer le contact '" + c.name + "' ?").then(function(ok){
          if(ok) WaAPI.deleteContact(c._id).then(loadWaContacts);
        });
      });
      tdAct.appendChild(btnDel);
      tr.appendChild(tdAct);
      tbody.appendChild(tr);
    });
  }

  var btnAddContact = $("#btn-wa-add-contact");
  if(btnAddContact) btnAddContact.addEventListener("click", function(){
    showPromptToast("Nom du contact :").then(function(name){
      if(!name) return;
      showPromptToast("Numero de telephone (format international sans +, ex: 33612345678) :").then(function(phone){
        if(!phone) return;
        showPromptToast("Role (optionnel) :").then(function(role){
          WaAPI.createContact({name: name.trim(), phone: phone.trim(), role: (role || "").trim()}).then(function(res){
            if(res.error){
              showToast("error", res.error);
            } else {
              showToast("success", "Contact ajoute");
              loadWaContacts();
            }
          });
        });
      });
    });
  });

  // ============================================================
  // Historique
  // ============================================================

  var historyPage = 1;

  function loadWaHistory(page){
    historyPage = page || 1;
    WaAPI.getHistory(historyPage, 10).then(function(data){
      renderWaHistory(data.items || [], data.total || 0, data.page || 1, data.limit || 10);
    });
  }

  function renderWaHistory(items, total, page, limit){
    var tbody = $("#wa-history-table tbody");
    tbody.textContent = "";
    if(!items.length){
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 4;
      td.style.cssText = "text-align:center; color:var(--muted); padding:16px;";
      td.textContent = "Aucun message envoye.";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    items.forEach(function(h){
      var tr = document.createElement("tr");
      // Date
      var tdDate = document.createElement("td");
      tdDate.style.cssText = "font-size:0.8rem; white-space:nowrap;";
      if(h.sentAt){
        var d = new Date(h.sentAt);
        tdDate.textContent = d.toLocaleDateString("fr-FR") + " " + d.toLocaleTimeString("fr-FR", {hour:"2-digit", minute:"2-digit"});
      }
      tr.appendChild(tdDate);
      // Alerte
      var tdAlert = document.createElement("td");
      tdAlert.textContent = h.alert_name || h.alert_slug || "";
      tdAlert.style.fontSize = "0.85rem";
      tr.appendChild(tdAlert);
      // Destinataire
      var tdDest = document.createElement("td");
      var destType = h.recipient_type === "group" ? "Groupe" : "DM";
      tdDest.textContent = destType + ": " + (h.recipient_name || h.recipient_id || "");
      tdDest.style.cssText = "font-size:0.82rem; color:var(--muted);";
      tr.appendChild(tdDest);
      // Statut
      var tdStatus = document.createElement("td");
      var statusBadge = document.createElement("span");
      statusBadge.className = "badge";
      if(h.status === "sent"){
        statusBadge.style.cssText = "font-size:0.72rem; padding:2px 8px; border-radius:10px; background:#22c55e22; color:#22c55e;";
        statusBadge.textContent = "Envoye";
      } else {
        statusBadge.style.cssText = "font-size:0.72rem; padding:2px 8px; border-radius:10px; background:#ef444422; color:#ef4444;";
        statusBadge.textContent = "Erreur";
        statusBadge.title = h.error || "";
      }
      tdStatus.appendChild(statusBadge);
      tr.appendChild(tdStatus);
      tbody.appendChild(tr);
    });

    // Pagination - DOM safe
    var pag = $("#wa-history-pagination");
    pag.textContent = "";
    var totalPages = Math.ceil(total / limit);
    if(totalPages > 1){
      for(var i = 1; i <= totalPages && i <= 10; i++){
        var btn = document.createElement("button");
        btn.className = "btn btn-secondary";
        btn.style.cssText = "font-size:0.75rem; padding:2px 8px; min-width:28px;";
        btn.textContent = i;
        if(i === page) btn.style.fontWeight = "700";
        btn.addEventListener("click", (function(p){ return function(){ loadWaHistory(p); }; })(i));
        pag.appendChild(btn);
      }
    }
  }

  // ============================================================
  // Fonctions exportees pour le modal definition (alertes_admin.js)
  // ============================================================

  function populateWaDefCheckboxes(selectedGroups, selectedContacts){
    // Groupes
    var gContainer = $("#wa-def-groups-checkboxes");
    if(gContainer){
      gContainer.textContent = "";
      gContainer.style.color = "";
      gContainer.style.fontSize = "";
      waGroups.forEach(function(g){
        if(!g.enabled) return;
        var lbl = document.createElement("label");
        lbl.style.cssText = "display:flex; align-items:center; gap:6px; padding:4px 0; cursor:pointer;";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = g.group_id;
        cb.checked = (selectedGroups || []).indexOf(g.group_id) >= 0;
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(" " + (g.name || g.group_id)));
        gContainer.appendChild(lbl);
      });
      if(!waGroups.length){
        gContainer.textContent = "Aucun groupe synchronise.";
        gContainer.style.color = "var(--muted)";
        gContainer.style.fontSize = "0.82rem";
      }
    }

    // Contacts
    var cContainer = $("#wa-def-contacts-checkboxes");
    if(cContainer){
      cContainer.textContent = "";
      cContainer.style.color = "";
      cContainer.style.fontSize = "";
      waContacts.forEach(function(c){
        if(!c.enabled) return;
        var lbl = document.createElement("label");
        lbl.style.cssText = "display:flex; align-items:center; gap:6px; padding:4px 0; cursor:pointer;";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = c.phone;
        cb.checked = (selectedContacts || []).indexOf(c.phone) >= 0;
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(" " + c.name + " (" + c.phone + ")"));
        cContainer.appendChild(lbl);
      });
      if(!waContacts.length){
        cContainer.textContent = "Aucun contact configure.";
        cContainer.style.color = "var(--muted)";
        cContainer.style.fontSize = "0.82rem";
      }
    }
  }

  function setupWaToggle(){
    var cb = $('[name="wa_enabled"]');
    var opts = $("#wa-def-options");
    if(!cb || !opts) return;
    function update(){
      opts.style.display = cb.checked ? "block" : "none";
    }
    cb.addEventListener("change", update);
    update();
  }

  // ============================================================
  // Init
  // ============================================================

  function initWa(){
    loadWaConfig();
    loadWaStatus();
    loadWaGroups();
    loadWaContacts();
    loadWaHistory(1);
    setupWaToggle();
  }

  // Expose pour alertes_admin.js
  window.WaAdmin = {
    init: initWa,
    loadGroups: loadWaGroups,
    loadContacts: loadWaContacts,
    populateDefCheckboxes: populateWaDefCheckboxes,
    getDefValues: function(){
      var waEnabled = $('[name="wa_enabled"]');
      if(!waEnabled) return null;
      var groups = [];
      $$("#wa-def-groups-checkboxes input[type=checkbox]:checked").forEach(function(cb){
        groups.push(cb.value);
      });
      var contacts = [];
      $$("#wa-def-contacts-checkboxes input[type=checkbox]:checked").forEach(function(cb){
        contacts.push(cb.value);
      });
      var cooldownInput = $('[name="wa_cooldown"]');
      return {
        enabled: waEnabled.checked,
        groups: groups,
        dm_on_critical: !!($('[name="wa_dm_on_critical"]') && $('[name="wa_dm_on_critical"]').checked),
        dm_recipients: contacts,
        cooldown_minutes: parseInt((cooldownInput && cooldownInput.value) || "15") || 15
      };
    },
    setDefValues: function(wa){
      wa = wa || {};
      var waEnabled = $('[name="wa_enabled"]');
      if(waEnabled) waEnabled.checked = !!wa.enabled;
      var dmCritical = $('[name="wa_dm_on_critical"]');
      if(dmCritical) dmCritical.checked = !!wa.dm_on_critical;
      var cooldown = $('[name="wa_cooldown"]');
      if(cooldown) cooldown.value = wa.cooldown_minutes || 15;
      populateWaDefCheckboxes(wa.groups || [], wa.dm_recipients || []);
      var opts = $("#wa-def-options");
      if(opts) opts.style.display = (wa.enabled) ? "block" : "none";
    },
    groups: function(){ return waGroups; },
    contacts: function(){ return waContacts; }
  };

  if(document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", initWa);
  } else {
    initWa();
  }
})();
