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

    // Apercu de l'icone et de la couleur dans l'en-tete
    var groupForm = $("#anoloc-group-form");
    if (groupForm) {
      ["icon", "color", "label"].forEach(function (n) {
        var el = groupForm.elements[n];
        if (el) el.addEventListener("input", refreshGroupHeader);
      });
      groupForm.addEventListener("change", refreshGroupCounts);
    }
    $$(".bg-tab").forEach(function (t) {
      t.addEventListener("click", function () { showGroupTab(t.dataset.src); });
    });
    var bgSearch = $("#bg-search");
    if (bgSearch) bgSearch.addEventListener("input", filterGroupMembers);
    var bgOnly = $("#bg-only-selected");
    if (bgOnly) bgOnly.addEventListener("change", filterGroupMembers);

    // Interrupteur general des balises GPS (toutes sources) : enregistre tout de suite
    var enabledToggle = $("#anoloc-enabled");
    if (enabledToggle) enabledToggle.addEventListener("change", saveConfig);
    // Parc Teltonika modifie (teltonika_admin.js) : libelles et compteurs des groupes
    document.addEventListener("teltonika:changed", loadConfig);

    // Visibility save
    var saveVisBtn = $("#anoloc-save-vis");
    if (saveVisBtn) saveVisBtn.addEventListener("click", saveVisibility);

    // Live control toggle
    var collectToggle = $("#anoloc-collect-toggle");
    if (collectToggle) {
      collectToggle.addEventListener("change", function () {
        apiPost("/anoloc/live-control", { collecting: collectToggle.checked }).then(function (data) {
          if (data.ok) showToast(collectToggle.checked ? "Collecte activee" : "Collecte desactivee", "success");
          loadLiveControl();
        });
      });
    }

    // Logging toggle
    var logToggle = $("#anoloc-log-toggle");
    if (logToggle) {
      logToggle.addEventListener("change", function () {
        apiPost("/anoloc/live-control", { logging: logToggle.checked }).then(function (data) {
          if (data.ok) showToast(logToggle.checked ? "Logging active" : "Logging desactive", "success");
          loadLiveControl();
        });
      });
    }

    // Log buttons
    var refreshLogsBtn = $("#anoloc-refresh-logs");
    if (refreshLogsBtn) refreshLogsBtn.addEventListener("click", loadLogs);
    var clearLogsBtn = $("#anoloc-clear-logs");
    if (clearLogsBtn) clearLogsBtn.addEventListener("click", function () {
      showConfirmToast("Vider tous les logs ?").then(function(ok){
        if(!ok) return;
        fetch("/anoloc/logs", { method: "DELETE", headers: jsonHeaders() })
          .then(function (r) { return r.json(); })
          .then(function () { loadLogs(); showToast("success", "Logs vides"); });
      });
    });

    loadLiveControl();
    loadLogs();
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
      if (grp.declarant) {
        var decl = document.createElement("span");
        decl.style.cssText = "margin-left:6px; font-size:10px; font-weight:700; padding:1px 6px; border-radius:4px; background:#fef3c7; color:#92400e;";
        decl.textContent = "Declarant";
        decl.title = "Mode declarant : tablettes Field qui declarent sans etre engagees";
        tdLabel.appendChild(decl);
      }
      if (grp.route_piste) {
        var pst = document.createElement("span");
        pst.style.cssText = "margin-left:6px; font-size:10px; font-weight:700; padding:1px 6px; border-radius:4px; background:#dbeafe; color:#1e40af;";
        pst.textContent = "Piste";
        pst.title = "Itineraires par la piste autorises aux tablettes Field du groupe";
        tdLabel.appendChild(pst);
      }
      if (grp.controle) {
        var ctl = document.createElement("span");
        ctl.style.cssText = "margin-left:6px; font-size:10px; font-weight:700; padding:1px 6px; border-radius:4px; background:#ede9fe; color:#5b21b6;";
        ctl.textContent = "Controle";
        ctl.title = "Controle de documents autorise aux tablettes Field du groupe";
        tdLabel.appendChild(ctl);
      }
      tr.appendChild(tdLabel);

      // Devices count
      var tdDevices = document.createElement("td");
      var ids = grp.anoloc_device_ids || [];
      var nTlt = ids.filter(isTeltonikaId).length;
      var nAno = ids.length - nTlt;
      var parts = [];
      if (nAno) parts.push(nAno + " Anoloc");
      if (nTlt) parts.push(nTlt + " Teltonika");
      tdDevices.textContent = parts.length ? parts.join(" + ") : "aucune";
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

  // Categories PCO du groupe : plusieurs cases, ou "Toutes" ("*", exclusive).
  // Ancien groupe : `pco_category` seule.
  function groupCategories(grp) {
    if (grp && Array.isArray(grp.pco_categories) && grp.pco_categories.length) return grp.pco_categories;
    return grp && grp.pco_category ? [grp.pco_category] : [];
  }

  function setPcoCategories(form, cats) {
    var boxes = form.querySelectorAll('input[name="pco_categories"]');
    Array.prototype.forEach.call(boxes, function (cb) { cb.checked = (cats || []).indexOf(cb.value) !== -1; });
    var wrap = $("#bg-pco-categories");
    if (wrap && !wrap._wired) {
      wrap._wired = true;
      wrap.addEventListener("change", function (e) {
        var t = e.target;
        if (!t || t.name !== "pco_categories" || !t.checked) return;
        Array.prototype.forEach.call(form.querySelectorAll('input[name="pco_categories"]'), function (cb) {
          if (cb !== t && (t.value === "*" || cb.value === "*")) cb.checked = false;
        });
      });
    }
  }

  function getPcoCategories(form) {
    var out = [];
    Array.prototype.forEach.call(form.querySelectorAll('input[name="pco_categories"]'), function (cb) {
      if (cb.checked) out.push(cb.value);
    });
    return out.indexOf("*") !== -1 ? ["*"] : out;
  }

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
      form.elements.enabled.checked = grp.enabled !== false;
      setPcoCategories(form, groupCategories(grp));
      $('input[name="declarant"]', form).checked = !!grp.declarant;
      $('input[name="route_piste"]', form).checked = !!grp.route_piste;
      $('input[name="controle"]', form).checked = !!grp.controle;
    } else {
      title.textContent = "Nouveau groupe de balises";
      $('input[name="id"]', form).value = "";  // forcer id vide pour nouveau groupe
      setPcoCategories(form, []);
    }

    var search = $("#bg-search");
    if (search) search.value = "";
    var only = $("#bg-only-selected");
    if (only) only.checked = false;
    refreshGroupHeader();
    showGroupTab("tlt");

    // Populate device checkboxes
    populateDeviceCheckboxes(grp);

    modal.hidden = false;
  }

  function refreshGroupHeader() {
    var form = $("#anoloc-group-form");
    if (!form) return;
    var icon = form.elements.icon.value.trim() || "location_on";
    var color = form.elements.color.value || "#6366f1";
    var preview = $("#anoloc-icon-preview");
    if (preview) preview.textContent = icon;
    var avatar = $("#bg-avatar");
    if (avatar) avatar.style.setProperty("--grp", color);
    var hex = $("#bg-color-hex");
    if (hex) hex.textContent = color;
    var sub = $("#bg-header-sub");
    if (sub) sub.textContent = form.elements.label.value.trim() || "Balises GPS et tablettes Field suivies ensemble";
  }

  function showGroupTab(src) {
    $$(".bg-tab").forEach(function (t) {
      var on = t.dataset.src === src;
      t.classList.toggle("is-active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
    });
    $$(".bg-panel").forEach(function (p) { p.hidden = p.dataset.panel !== src; });
    var only = $("#bg-only-selected");
    if (only) only.closest(".bg-only").hidden = src === "field";
    filterGroupMembers();
  }

  function filterGroupMembers() {
    var q = (($("#bg-search") || {}).value || "").trim().toLowerCase();
    var only = !!(($("#bg-only-selected") || {}).checked);
    $$(".bg-list .bg-member").forEach(function (row) {
      var cb = row.querySelector("input[type=checkbox]");
      var match = !q || (row.dataset.search || "").indexOf(q) !== -1;
      row.hidden = !match || (only && cb && !cb.checked);
    });
  }

  function refreshGroupCounts() {
    var tlt = $$("#tlt-devices-checkboxes input:checked").length;
    var ano = $$("#anoloc-devices-checkboxes input:checked").length;
    var fld = $$("#field-devices-list .bg-member").length;
    var set = function (id, n) { var e = $(id); if (e) { e.textContent = n; e.classList.toggle("is-zero", !n); } };
    set("#bg-count-tlt", tlt);
    set("#bg-count-anoloc", ano);
    set("#bg-count-field", fld);
    var parts = [];
    if (tlt) parts.push(tlt + " Teltonika");
    if (ano) parts.push(ano + " Anoloc");
    if (fld) parts.push(fld + " tablette" + (fld > 1 ? "s" : ""));
    var sum = $("#bg-summary");
    if (sum) sum.textContent = parts.length ? parts.join(" · ") : "Aucun membre";
    filterGroupMembers();
  }

  function closeGroupModal() {
    var modal = $("#anoloc-group-modal");
    if (modal) modal.hidden = true;
    editingGroupIdx = null;
  }

  function isTeltonikaId(id) { return String(id || "").indexOf("tlt:") === 0; }
  function isFieldId(id) { return String(id || "").indexOf("field:") === 0; }

  // Une ligne de membre : case, nom, identifiant, etiquettes (deja dans un
  // autre groupe, non lue chez Anoloc...). Toute la ligne est cliquable.
  function checkboxRow(prefix, value, label, sub, checked, tags) {
    var row = document.createElement("label");
    row.className = "bg-member";
    row.dataset.search = (label + " " + (sub || "") + " " + value).toLowerCase();
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.id = prefix + value;
    cb.value = value;
    cb.checked = checked;
    row.appendChild(cb);
    var box = document.createElement("span");
    box.className = "bg-check";
    row.appendChild(box);
    var txt = document.createElement("span");
    txt.className = "bg-member-text";
    var n = document.createElement("span");
    n.className = "bg-member-name";
    n.textContent = label;
    txt.appendChild(n);
    if (sub) {
      var s = document.createElement("span");
      s.className = "bg-member-sub";
      s.textContent = sub;
      txt.appendChild(s);
    }
    row.appendChild(txt);
    (tags || []).forEach(function (t) {
      var tag = document.createElement("span");
      tag.className = "bg-tag" + (t.kind ? " bg-tag-" + t.kind : "");
      tag.textContent = t.text;
      if (t.title) tag.title = t.title;
      row.appendChild(tag);
    });
    return row;
  }

  function emptyNote(container, text) {
    var msg = document.createElement("p");
    msg.className = "bg-empty";
    msg.textContent = text;
    container.appendChild(msg);
  }

  // Tablettes Field du groupe (lecture seule : le groupe se choisit a l'appairage)
  function populateFieldDevices(grp) {
    var box = $("#field-devices-list");
    if (!box) return;
    box.textContent = "";
    if (!grp || !grp.id) { emptyNote(box, "Enregistrez le groupe, puis appairez les tablettes en le choisissant."); refreshGroupCounts(); return; }
    emptyNote(box, "Chargement...");
    apiGet("/field/admin/devices?include_revoked=false&beacon_group_id=" + encodeURIComponent(grp.id)).then(function (data) {
      box.textContent = "";
      var devs = (data && data.devices) || [];
      if (!devs.length) emptyNote(box, "Aucune tablette appairee dans ce groupe.");
      devs.forEach(function (d) {
        var row = document.createElement("div");
        row.className = "bg-member bg-member-ro";
        row.dataset.search = ((d.name || "") + " " + (d.event || "")).toLowerCase();
        var ic = document.createElement("span");
        ic.className = "material-symbols-outlined bg-member-icon";
        ic.textContent = "tablet_android";
        row.appendChild(ic);
        var txt = document.createElement("span");
        txt.className = "bg-member-text";
        var n = document.createElement("span");
        n.className = "bg-member-name";
        n.textContent = d.name || d.id;
        txt.appendChild(n);
        var s = document.createElement("span");
        s.className = "bg-member-sub";
        s.textContent = [d.event, d.year].filter(Boolean).join(" ") + (d.native_app_version ? " · application " + d.native_app_version : " · PWA");
        txt.appendChild(s);
        row.appendChild(txt);
        if (d.test_mode) {
          var t = document.createElement("span");
          t.className = "bg-tag bg-tag-warn";
          t.textContent = "Mode test";
          row.appendChild(t);
        }
        box.appendChild(row);
      });
      refreshGroupCounts();
    }).catch(function () {
      box.textContent = "";
      emptyNote(box, "Liste des tablettes indisponible.");
      refreshGroupCounts();
    });
  }

  function populateDeviceCheckboxes(grp) {
    var selectedIds = (grp && grp.anoloc_device_ids) || [];
    var savedLabels = (grp && grp.device_labels) || {};

    // Teltonika : parc local
    var tltBox = $("#tlt-devices-checkboxes");
    if (tltBox) {
      tltBox.textContent = "";
      emptyNote(tltBox, "Chargement...");
      apiGet("/anoloc/teltonika").then(function (data) {
        tltBox.textContent = "";
        var devs = (data && data.devices) || [];
        if (!devs.length) { emptyNote(tltBox, "Aucune balise Teltonika dans le parc."); return; }
        devs.forEach(function (d) {
          var others = (d.groups || []).filter(function (g) { return !grp || g !== grp.label; });
          var tags = others.length ? [{ text: "Aussi dans " + others.join(", "), kind: "info" }] : [];
          tltBox.appendChild(checkboxRow("tlt-dev-", d.id, d.label || d.imei, "IMEI " + d.imei,
                                         selectedIds.indexOf(d.id) !== -1, tags));
        });
        refreshGroupCounts();
      }).catch(function () {
        tltBox.textContent = "";
        emptyNote(tltBox, "Parc Teltonika indisponible.");
        refreshGroupCounts();
      });
    }

    // Anoloc : liste distante + balises deja dans le groupe (toujours
    // affichees : avec l'API Anoloc en panne, enregistrer le groupe les
    // effacait toutes)
    var container = $("#anoloc-devices-checkboxes");
    if (!container) return;
    container.textContent = "";
    var shown = {};
    remoteDevices.forEach(function (dev) {
      shown[dev.id] = true;
      container.appendChild(checkboxRow("anoloc-dev-", dev.id, dev.label || dev.id, dev.imei ? "IMEI " + dev.imei : "",
                                        selectedIds.indexOf(dev.id) !== -1));
    });
    selectedIds.forEach(function (id) {
      if (shown[id] || isTeltonikaId(id) || isFieldId(id)) return;
      container.appendChild(checkboxRow("anoloc-dev-", id, savedLabels[id] || id, id, true,
                                        [{ text: "Non lue chez Anoloc", kind: "warn", title: "Gardee dans le groupe tant que vous ne la decochez pas" }]));
    });
    if (!container.children.length) {
      emptyNote(container, "Aucune balise Anoloc chargee. Cliquez 'Charger devices' dans Source Anoloc.");
    }
    populateFieldDevices(grp);
    refreshGroupCounts();
  }

  function saveBeaconGroup() {
    var form = $("#anoloc-group-form");
    if (!form) return;

    var label = $('input[name="label"]', form).value.trim();
    if (!label) { showToast("Le label est requis", "error"); return; }

    var icon = $('input[name="icon"]', form).value.trim() || "location_on";
    var color = $('input[name="color"]', form).value || "#6366f1";
    var enabled = form.elements.enabled.checked;

    // Collect selected device IDs + build label map (Anoloc et Teltonika ;
    // les libelles Teltonika sont recalcules par le serveur depuis le parc)
    var prevGrp = editingGroupIdx !== null && config.beacon_groups ? config.beacon_groups[editingGroupIdx] : null;
    var prevLabels = (prevGrp && prevGrp.device_labels) || {};
    var deviceIds = [];
    var deviceLabels = {};
    $$('#anoloc-devices-checkboxes input[type="checkbox"]:checked, #tlt-devices-checkboxes input[type="checkbox"]:checked').forEach(function (cb) {
      deviceIds.push(cb.value);
      deviceLabels[cb.value] = prevLabels[cb.value] || cb.value;
      for (var i = 0; i < remoteDevices.length; i++) {
        if (remoteDevices[i].id === cb.value) {
          deviceLabels[cb.value] = remoteDevices[i].label || cb.value;
          break;
        }
      }
    });
    // Les tablettes Field ne passent jamais par ces cases : les conserver
    ((prevGrp && prevGrp.anoloc_device_ids) || []).forEach(function (id) {
      if (isFieldId(id) && deviceIds.indexOf(id) === -1) {
        deviceIds.push(id);
        if (prevLabels[id]) deviceLabels[id] = prevLabels[id];
      }
    });

    // Generate slug id (normalise les accents pour eviter les collisions)
    var existingId = $('input[name="id"]', form).value;
    var slug = label.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-+$/, "");
    var id = existingId || "grp-" + slug;
    // Verifier l'unicite
    if (!existingId && config.beacon_groups) {
      var usedIds = config.beacon_groups.map(function(g) { return g.id; });
      while (usedIds.indexOf(id) !== -1) { id += "-" + Math.random().toString(36).substr(2, 4); }
    }

    var pcoCategories = getPcoCategories(form);

    var grpData = {
      id: id,
      label: label,
      icon: icon,
      color: color,
      anoloc_device_ids: deviceIds,
      device_labels: deviceLabels,
      enabled: enabled,
      // Liste ("*" = toutes) ; le serveur tient a jour l'ancien pco_category
      pco_categories: pcoCategories,
      pco_category: pcoCategories[0] && pcoCategories[0] !== "*" ? pcoCategories[0] : "",
      declarant: $('input[name="declarant"]', form).checked,
      route_piste: $('input[name="route_piste"]', form).checked,
      controle: $('input[name="controle"]', form).checked,
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
    showConfirmToast("Supprimer ce groupe de balises ?").then(function(ok){
      if(!ok) return;
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
          showToast("success", "Groupe supprime");
          loadConfig();
        }
      });
    });
  }

  // ============================================================
  // Visibility table
  // ============================================================

  function loadCockpitGroups() {
    apiGet("/api/groups").then(function (data) {
      var groups = Array.isArray(data) ? data : (data.groups || []);
      cockpitGroups = groups.filter(function (g) {
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
      var logToggle = $("#anoloc-log-toggle");
      var logIcon = $("#anoloc-log-icon");

      var collecting = !!data.collecting;
      if (toggle) toggle.checked = collecting;
      if (icon) icon.style.color = collecting ? "#22c55e" : "var(--muted)";
      if (logToggle) logToggle.checked = !!data.logging;
      if (logIcon) logIcon.style.color = data.logging ? "#3b82f6" : "var(--muted)";

      if (status) {
        var parts = [];
        parts.push(collecting ? "Collecte active" : "Collecte arretee");
        if (data.last_run_display) {
          parts.push("- derniere exec: " + data.last_run_display);
        } else if (data.last_run) {
          try {
            var d = new Date(data.last_run);
            parts.push("- derniere exec: " + d.toLocaleString("fr-FR"));
          } catch (e) {}
        }
        if (data.last_error) parts.push("- " + data.last_error);
        if (data.running) parts.push("(en cours)");
        status.textContent = parts.join(" ");
      }
    });
  }

  function loadLogs() {
    var container = $("#anoloc-logs-container");
    if (!container) return;

    apiGet("/anoloc/logs?limit=50").then(function (data) {
      container.textContent = "";
      var logs = data.logs || [];
      if (logs.length === 0) {
        container.textContent = "Aucun log disponible.";
        return;
      }
      logs.forEach(function (entry) {
        var line = document.createElement("div");
        line.style.cssText = "padding:3px 0; border-bottom:1px solid var(--line-light, rgba(255,255,255,0.04));";

        // Timestamp
        var tsStr = "";
        if (entry.ts_display) {
          tsStr = entry.ts_display + " ";
        } else if (entry.ts) {
          try {
            var d = new Date(entry.ts);
            tsStr = d.toLocaleString("fr-FR") + " ";
          } catch (e) {}
        }

        var levelColor = entry.level === "error" ? "#ef4444" : entry.level === "warning" ? "#f59e0b" : "#22c55e";
        var header = document.createElement("span");
        header.style.color = levelColor;
        header.textContent = tsStr + "[" + (entry.level || "info").toUpperCase() + "] ";
        line.appendChild(header);

        var msg = document.createElement("span");
        msg.textContent = entry.message || "";
        line.appendChild(msg);

        // Details (devices)
        if (entry.details && entry.details.devices) {
          var details = document.createElement("div");
          details.style.cssText = "margin-left:16px; color:var(--muted);";
          entry.details.devices.forEach(function (dev) {
            var devLine = document.createElement("div");
            var color = dev.online ? "#22c55e" : dev.collected ? "#f59e0b" : "#9ca3af";
            var text = dev.label || dev.id;
            if (dev.reason) {
              text += " - " + dev.reason;
            } else {
              text += " [" + dev.status + "]";
              text += " GPS:" + (dev.gps || "?");
              if (dev.speed !== "-" && dev.speed != null) text += " " + dev.speed + "km/h";
              if (dev.battery != null) text += " bat:" + dev.battery + "%";
              if (dev.last_real && dev.last_real !== "-") text += " last:" + dev.last_real;
            }
            devLine.textContent = "  " + text;
            devLine.style.color = color;
            details.appendChild(devLine);
          });
          line.appendChild(details);
        }

        container.appendChild(line);
      });
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
