/**
 * anoloc_admin.js - Administration des balises GPS Anoloc
 * Gere: credentials, groupes de balises (CRUD), visibilite par groupe cockpit.
 */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.from((r || document).querySelectorAll(s)); };

  function jsonHeaders() {
    var h = { "Content-Type": "application/json" };
    var m = $('meta[name="csrf-token"]');
    if (m) h["X-CSRFToken"] = m.getAttribute("content");
    return h;
  }

  // State
  var config = {};            // current anoloc_config
  var remoteDevices = [];     // devices from Anoloc API
  var cockpitGroups = [];     // groups from /api/groups

  // ============================================================
  // API helpers
  // ============================================================

  function apiGet(url) { return fetch(url).then(function (r) { return r.json(); }); }
  function apiPost(url, data) {
    return fetch(url, { method: "POST", headers: jsonHeaders(), body: JSON.stringify(data) })
      .then(function (r) { return r.json(); });
  }

  // ============================================================
  // Init
  // ============================================================

  function init() {
    loadConfig();
    loadCockpitGroups();

    // Credentials events
    var testBtn = $("#anoloc-test-btn");
    if (testBtn) testBtn.addEventListener("click", testLogin);
    var saveCredsBtn = $("#anoloc-save-creds");
    if (saveCredsBtn) saveCredsBtn.addEventListener("click", saveConfig);

    // Beacon groups
    var addBtn = $("#anoloc-add-group");
    if (addBtn) addBtn.addEventListener("click", function () { openGroupModal(null); });
    var fetchBtn = $("#anoloc-fetch-devices");
    if (fetchBtn) fetchBtn.addEventListener("click", fetchRemoteDevices);

    // Modal
    var modal = $("#anoloc-group-modal");
    if (modal) {
      $$("[data-close]", modal).forEach(function (btn) {
        btn.addEventListener("click", closeGroupModal);
      });
      modal.addEventListener("click", function (e) {
        if (e.target === modal) closeGroupModal();
      });
    }
    var saveModalBtn = $("#anoloc-group-modal-save");
    if (saveModalBtn) saveModalBtn.addEventListener("click", saveBeaconGroup);

    // Icon preview
    var iconInput = $('input[name="icon"]', $("#anoloc-group-form"));
    if (iconInput) {
      iconInput.addEventListener("input", function () {
        var preview = $("#anoloc-icon-preview");
        if (preview) preview.textContent = iconInput.value || "location_on";
      });
    }

    // Visibility save
    var saveVisBtn = $("#anoloc-save-vis");
    if (saveVisBtn) saveVisBtn.addEventListener("click", saveVisibility);

    // Live control toggle
    var collectToggle = $("#anoloc-collect-toggle");
    if (collectToggle) {
      collectToggle.addEventListener("change", function () {
        toggleCollecting(collectToggle.checked);
      });
    }
    loadLiveControl();
  }

  // ============================================================
  // Load config
  // ============================================================

  function loadConfig() {
    apiGet("/anoloc/config").then(function (data) {
      config = data || {};
      populateCredentials();
      populateGroupsTable();
      populateVisibilityTable();
      // Charger les devices automatiquement si credentials configures
      if (config.login && config.password && remoteDevices.length === 0) {
        fetchRemoteDevices(true);
      }
    });
  }

  function populateCredentials() {
    var apiBaseEl = $("#anoloc-api-base");
    var loginEl = $("#anoloc-login");
    var pwdEl = $("#anoloc-password");
    var enabledEl = $("#anoloc-enabled");
    if (apiBaseEl) apiBaseEl.value = config.api_base || "";
    if (loginEl) loginEl.value = config.login || "";
    if (pwdEl) pwdEl.value = config.password || "";
    if (enabledEl) enabledEl.checked = !!config.enabled;
  }

  // ============================================================
  // Test login
  // ============================================================

  function testLogin() {
    var login = ($("#anoloc-login") || {}).value || "";
    var password = ($("#anoloc-password") || {}).value || "";
    var resultDiv = $("#anoloc-test-result");
    if (resultDiv) resultDiv.textContent = "Test en cours...";

    apiPost("/anoloc/test-login", { login: login, password: password })
      .then(function (data) {
        if (resultDiv) {
          if (data.ok) {
            resultDiv.style.color = "#22c55e";
            var user = data.user || {};
            resultDiv.textContent = "Connexion OK" + (user.username ? " (" + user.username + ")" : "");
          } else {
            resultDiv.style.color = "#ef4444";
            resultDiv.textContent = "Echec: " + (data.error || "erreur inconnue");
          }
        }
      })
      .catch(function () {
        if (resultDiv) {
          resultDiv.style.color = "#ef4444";
          resultDiv.textContent = "Erreur reseau";
        }
      });
  }

  // ============================================================
  // Save config (credentials + beacon_groups)
  // ============================================================

  function saveConfig() {
    var apiBase = ($("#anoloc-api-base") || {}).value || "";
    var login = ($("#anoloc-login") || {}).value || "";
    var password = ($("#anoloc-password") || {}).value || "";
    var enabled = ($("#anoloc-enabled") || {}).checked || false;

    var payload = {
      api_base: apiBase,
      login: login,
      password: password,
      enabled: enabled,
      beacon_groups: config.beacon_groups || [],
      group_visibility: config.group_visibility || {},
    };

    apiPost("/anoloc/config", payload).then(function (data) {
      if (data.ok) {
        showToast("Configuration sauvegardee", "success");
        loadConfig();
      } else {
        showToast("Erreur de sauvegarde", "error");
      }
    });
  }

  // ============================================================
  // Fetch remote devices from Anoloc
  // ============================================================

  function fetchRemoteDevices(silent) {
    var btn = $("#anoloc-fetch-devices");
    if (btn) btn.disabled = true;

    apiGet("/anoloc/anoloc-devices").then(function (data) {
      if (btn) btn.disabled = false;
      if (data.ok) {
        remoteDevices = data.devices || [];
        if (!silent) showToast(remoteDevices.length + " devices charges depuis Anoloc", "success");
      } else {
        if (!silent) showToast("Erreur: " + (data.error || ""), "error");
      }
    }).catch(function () {
      if (btn) btn.disabled = false;
      if (!silent) showToast("Erreur reseau", "error");
    });
  }

  // ============================================================
  // Beacon groups table
  // ============================================================

  function populateGroupsTable() {
    var tbody = $("#anoloc-groups-table tbody");
    if (!tbody) return;
    tbody.textContent = "";

    var groups = config.beacon_groups || [];
    if (groups.length === 0) {
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 6;
      td.style.cssText = "text-align:center; color:var(--muted); font-size:12px;";
      td.textContent = "Aucun groupe configure";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    groups.forEach(function (grp, idx) {
      var tr = document.createElement("tr");

      // Color
      var tdColor = document.createElement("td");
      var swatch = document.createElement("span");
      swatch.style.cssText = "display:inline-block;width:18px;height:18px;border-radius:4px;vertical-align:middle;";
      swatch.style.background = grp.color || "#6366f1";
      tdColor.appendChild(swatch);
      tr.appendChild(tdColor);

      // Icon
      var tdIcon = document.createElement("td");
      var iconEl = document.createElement("span");
      iconEl.className = "material-symbols-outlined";
      iconEl.style.cssText = "font-size:18px;vertical-align:middle;";
      iconEl.style.color = grp.color || "#6366f1";
      iconEl.textContent = grp.icon || "location_on";
      tdIcon.appendChild(iconEl);
      tr.appendChild(tdIcon);

      // Label
      var tdLabel = document.createElement("td");
      tdLabel.textContent = grp.label || grp.id;
      tr.appendChild(tdLabel);

      // Devices count
      var tdDevices = document.createElement("td");
      var devCount = (grp.anoloc_device_ids || []).length;
      tdDevices.textContent = devCount + " device" + (devCount !== 1 ? "s" : "");
      tr.appendChild(tdDevices);

      // Enabled
      var tdActive = document.createElement("td");
      var badge = document.createElement("span");
      badge.style.cssText = "font-size:11px; padding:2px 6px; border-radius:4px; color:#fff; background:" + (grp.enabled !== false ? "#22c55e" : "#9ca3af");
      badge.textContent = grp.enabled !== false ? "Oui" : "Non";
      tdActive.appendChild(badge);
      tr.appendChild(tdActive);

      // Actions
      var tdActions = document.createElement("td");
      tdActions.style.cssText = "display:flex; gap:4px; align-items:center;";
      var editBtn = document.createElement("button");
      editBtn.className = "btn-icon";
      editBtn.title = "Editer";
      var editIcon = document.createElement("span");
      editIcon.className = "material-symbols-outlined";
      editIcon.style.fontSize = "16px";
      editIcon.textContent = "edit";
      editBtn.appendChild(editIcon);
      editBtn.addEventListener("click", function () { openGroupModal(idx); });
      var delBtn = document.createElement("button");
      delBtn.className = "btn-icon";
      delBtn.title = "Supprimer";
      var delIcon = document.createElement("span");
      delIcon.className = "material-symbols-outlined";
      delIcon.style.fontSize = "16px";
      delIcon.textContent = "delete";
      delBtn.appendChild(delIcon);
      delBtn.addEventListener("click", function () { deleteBeaconGroup(idx); });
      tdActions.appendChild(editBtn);
      tdActions.appendChild(delBtn);
      tr.appendChild(tdActions);

      tbody.appendChild(tr);
    });
  }

  // ============================================================
  // Beacon group modal (create/edit)
  // ============================================================

  var editingGroupIdx = null;

  function openGroupModal(idx) {
    editingGroupIdx = idx;
    var modal = $("#anoloc-group-modal");
    var form = $("#anoloc-group-form");
    var title = $("#anoloc-group-modal-title");
    if (!modal || !form) return;

    form.reset();
    var grp = null;
    if (idx !== null && config.beacon_groups && config.beacon_groups[idx]) {
      grp = config.beacon_groups[idx];
      title.textContent = "Editer groupe";
      $('input[name="id"]', form).value = grp.id || "";
      $('input[name="label"]', form).value = grp.label || "";
      $('input[name="icon"]', form).value = grp.icon || "location_on";
      $('input[name="color"]', form).value = grp.color || "#6366f1";
      $('input[name="enabled"]', form).checked = grp.enabled !== false;
    } else {
      title.textContent = "Nouveau groupe de balises";
    }

    // Update icon preview
    var preview = $("#anoloc-icon-preview");
    if (preview) preview.textContent = (grp && grp.icon) || "location_on";

    // Populate device checkboxes
    populateDeviceCheckboxes(grp);

    modal.hidden = false;
  }

  function closeGroupModal() {
    var modal = $("#anoloc-group-modal");
    if (modal) modal.hidden = true;
    editingGroupIdx = null;
  }

  function populateDeviceCheckboxes(grp) {
    var container = $("#anoloc-devices-checkboxes");
    if (!container) return;
    container.textContent = "";

    var selectedIds = (grp && grp.anoloc_device_ids) || [];

    if (remoteDevices.length === 0) {
      var msg = document.createElement("p");
      msg.style.cssText = "font-size:11px; color:var(--muted); margin:4px;";
      msg.textContent = "Aucun device charge. Cliquez 'Charger devices' d'abord.";
      container.appendChild(msg);
      return;
    }

    remoteDevices.forEach(function (dev) {
      var row = document.createElement("div");
      row.className = "anoloc-device-checkbox-row";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.id = "anoloc-dev-" + dev.id;
      cb.value = dev.id;
      cb.checked = selectedIds.indexOf(dev.id) !== -1;
      row.appendChild(cb);
      var lbl = document.createElement("label");
      lbl.setAttribute("for", "anoloc-dev-" + dev.id);
      lbl.textContent = dev.label || dev.id;
      row.appendChild(lbl);
      if (dev.imei) {
        var imeiSpan = document.createElement("span");
        imeiSpan.className = "anoloc-device-imei";
        imeiSpan.textContent = dev.imei;
        row.appendChild(imeiSpan);
      }
      container.appendChild(row);
    });
  }

  function saveBeaconGroup() {
    var form = $("#anoloc-group-form");
    if (!form) return;

    var label = $('input[name="label"]', form).value.trim();
    if (!label) { showToast("Le label est requis", "error"); return; }

    var icon = $('input[name="icon"]', form).value.trim() || "location_on";
    var color = $('input[name="color"]', form).value || "#6366f1";
    var enabled = $('input[name="enabled"]', form).checked;

    // Collect selected device IDs + build label map
    var deviceIds = [];
    var deviceLabels = {};
    $$('#anoloc-devices-checkboxes input[type="checkbox"]:checked').forEach(function (cb) {
      deviceIds.push(cb.value);
      // Trouver le label depuis remoteDevices
      for (var i = 0; i < remoteDevices.length; i++) {
        if (remoteDevices[i].id === cb.value) {
          deviceLabels[cb.value] = remoteDevices[i].label || cb.value;
          break;
        }
      }
    });

    // Generate slug id
    var existingId = $('input[name="id"]', form).value;
    var id = existingId || "grp-" + label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-+$/, "");

    var grpData = {
      id: id,
      label: label,
      icon: icon,
      color: color,
      anoloc_device_ids: deviceIds,
      device_labels: deviceLabels,
      enabled: enabled,
    };

    if (!config.beacon_groups) config.beacon_groups = [];

    if (editingGroupIdx !== null) {
      config.beacon_groups[editingGroupIdx] = grpData;
    } else {
      config.beacon_groups.push(grpData);
    }

    // Save full config
    var payload = {
      login: config.login || ($("#anoloc-login") || {}).value || "",
      password: config.password || "********",
      enabled: config.enabled !== undefined ? config.enabled : (($("#anoloc-enabled") || {}).checked || false),
      beacon_groups: config.beacon_groups,
      group_visibility: config.group_visibility || {},
    };

    apiPost("/anoloc/config", payload).then(function (data) {
      if (data.ok) {
        showToast("Groupe sauvegarde", "success");
        closeGroupModal();
        loadConfig();
      } else {
        showToast("Erreur de sauvegarde", "error");
      }
    });
  }

  function deleteBeaconGroup(idx) {
    if (!confirm("Supprimer ce groupe de balises ?")) return;
    if (!config.beacon_groups) return;
    config.beacon_groups.splice(idx, 1);

    var payload = {
      login: config.login || "",
      password: "********",
      enabled: config.enabled || false,
      beacon_groups: config.beacon_groups,
      group_visibility: config.group_visibility || {},
    };

    apiPost("/anoloc/config", payload).then(function (data) {
      if (data.ok) {
        showToast("Groupe supprime", "success");
        loadConfig();
      }
    });
  }

  // ============================================================
  // Visibility table
  // ============================================================

  function loadCockpitGroups() {
    apiGet("/api/groups").then(function (data) {
      cockpitGroups = (data.groups || []).filter(function (g) {
        return g.name !== "__default__" && g.name !== "__admin__";
      });
      populateVisibilityTable();
    });
  }

  function populateVisibilityTable() {
    var headerRow = $("#anoloc-vis-header");
    var tbody = $("#anoloc-vis-body");
    if (!headerRow || !tbody) return;

    var beaconGroups = config.beacon_groups || [];
    var visibility = config.group_visibility || {};

    // Rebuild header
    headerRow.textContent = "";
    var th0 = document.createElement("th");
    th0.textContent = "Groupe cockpit";
    headerRow.appendChild(th0);
    beaconGroups.forEach(function (bg) {
      var th = document.createElement("th");
      th.style.textAlign = "center";
      th.textContent = bg.label || bg.id;
      headerRow.appendChild(th);
    });

    // Rebuild body
    tbody.textContent = "";
    if (cockpitGroups.length === 0 || beaconGroups.length === 0) {
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = beaconGroups.length + 1;
      td.style.cssText = "text-align:center; color:var(--muted); font-size:12px;";
      td.textContent = cockpitGroups.length === 0
        ? "Aucun groupe cockpit configure"
        : "Aucun groupe de balises configure";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    cockpitGroups.forEach(function (cg) {
      var tr = document.createElement("tr");
      var tdName = document.createElement("td");
      tdName.textContent = cg.name;
      tr.appendChild(tdName);

      var cgId = String(cg._id);
      var visibleForGroup = visibility[cgId] || null;

      beaconGroups.forEach(function (bg) {
        var td = document.createElement("td");
        td.style.textAlign = "center";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.dataset.cockpitGroup = cgId;
        cb.dataset.beaconGroup = bg.id;
        cb.checked = visibleForGroup === null || (Array.isArray(visibleForGroup) && visibleForGroup.indexOf(bg.id) !== -1);
        td.appendChild(cb);
        tr.appendChild(td);
      });

      tbody.appendChild(tr);
    });
  }

  function saveVisibility() {
    var beaconGroups = config.beacon_groups || [];
    var allBgIds = beaconGroups.map(function (bg) { return bg.id; });
    var result = {};

    cockpitGroups.forEach(function (cg) {
      var cgId = String(cg._id);
      var checked = [];
      $$('input[data-cockpit-group="' + cgId + '"]:checked').forEach(function (cb) {
        checked.push(cb.dataset.beaconGroup);
      });
      // If all checked -> null (no restriction)
      if (checked.length === allBgIds.length) {
        result[cgId] = null;
      } else {
        result[cgId] = checked;
      }
    });

    apiPost("/anoloc/visibility", result).then(function (data) {
      if (data.ok) {
        showToast("Visibilite sauvegardee", "success");
        config.group_visibility = result;
      } else {
        showToast("Erreur de sauvegarde", "error");
      }
    });
  }

  // ============================================================
  // Toast helper (reuse existing if available)
  // ============================================================

  function showToast(msg, type) {
    if (window.showToast) {
      window.showToast(msg, type);
      return;
    }
    var container = document.getElementById("toast-container");
    if (!container) return;
    var toast = document.createElement("div");
    toast.className = "toast-popup " + (type || "info");
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(function () { toast.remove(); }, 3000);
  }

  // ============================================================
  // Live control
  // ============================================================

  function loadLiveControl() {
    apiGet("/anoloc/live-control").then(function (data) {
      var toggle = $("#anoloc-collect-toggle");
      var status = $("#anoloc-collect-status");
      var icon = $("#anoloc-collect-icon");

      var collecting = !!data.collecting;
      if (toggle) toggle.checked = collecting;
      if (icon) icon.style.color = collecting ? "#22c55e" : "var(--muted)";

      if (status) {
        var parts = [];
        if (collecting) {
          parts.push("Collecte active");
        } else {
          parts.push("Collecte arretee");
        }
        if (data.last_run) {
          try {
            var d = new Date(data.last_run);
            parts.push("- derniere exec: " + d.toLocaleString("fr-FR"));
          } catch (e) {}
        }
        if (data.last_error) {
          parts.push("- " + data.last_error);
        }
        if (data.running) {
          parts.push("(en cours)");
        }
        status.textContent = parts.join(" ");
      }
    });
  }

  function toggleCollecting(enabled) {
    apiPost("/anoloc/live-control", { collecting: enabled }).then(function (data) {
      if (data.ok) {
        showToast(enabled ? "Collecte activee" : "Collecte desactivee", "success");
        loadLiveControl();
      }
    });
  }

  // ============================================================
  // Boot
  // ============================================================

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
