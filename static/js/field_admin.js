/**
 * field_admin.js - Administration des tablettes terrain (Field)
 *
 * Gere : generation de codes de pairing, liste et revocation des tablettes
 * enrolees. Scope = event/year selectionnes dans le header cockpit.
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

  function apiGet(url) {
    return fetch(url).then(function (r) { return r.json(); });
  }
  function apiPost(url, data) {
    return fetch(url, { method: "POST", headers: jsonHeaders(), body: JSON.stringify(data || {}) })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); });
  }
  function apiDelete(url) {
    return fetch(url, { method: "DELETE", headers: jsonHeaders() })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); });
  }

  function _toast(type, msg) {
    if (typeof window.showToast === "function") window.showToast(type, msg);
    else console.log("[toast]", type, msg);
  }

  // State
  var state = {
    beaconGroups: [],  // [{id, label, color, icon, pco_category}, ...]
    pairings: [],
    devices: [],
    messages: [],
    pollTimer: null,
  };

  // ------------------------------------------------------------------
  // Scope (event/year) : reprend la selection cockpit globale
  // ------------------------------------------------------------------
  function currentScope() {
    return {
      event: window.selectedEvent || "",
      year: String(window.selectedYear || ""),
    };
  }

  function scopeLabel() {
    var s = currentScope();
    if (!s.event || !s.year) return "(aucun evenement selectionne)";
    return s.event + " / " + s.year;
  }

  function refreshScopeUi() {
    var el = $("#field-admin-scope");
    if (el) el.textContent = scopeLabel();
    var lbl = $("#field-pair-event-label");
    if (lbl) lbl.textContent = scopeLabel();
    var lbl2 = $("#field-msg-event-label");
    if (lbl2) lbl2.textContent = scopeLabel();
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------
  function init() {
    refreshScopeUi();

    var newBtn = $("#field-pair-new");
    if (newBtn) newBtn.addEventListener("click", openPairModal);

    var codesBtn = $("#field-pair-show-codes");
    if (codesBtn) codesBtn.addEventListener("click", openCodesModal);

    var refreshBtn = $("#field-admin-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", function () {
      refreshScopeUi();
      loadBeaconGroups();
      loadDevices();
      loadPairings();
      loadMessages();
    });

    // Modal pairing
    var pairModal = $("#field-pair-modal");
    if (pairModal) {
      $$("[data-close]", pairModal).forEach(function (b) {
        b.addEventListener("click", closePairModal);
      });
      pairModal.addEventListener("click", function (e) {
        if (e.target === pairModal) closePairModal();
      });
    }
    var pairSubmit = $("#field-pair-submit");
    if (pairSubmit) pairSubmit.addEventListener("click", submitPairing);

    // Modal codes
    var codesModal = $("#field-codes-modal");
    if (codesModal) {
      $$("[data-close]", codesModal).forEach(function (b) {
        b.addEventListener("click", closeCodesModal);
      });
      codesModal.addEventListener("click", function (e) {
        if (e.target === codesModal) closeCodesModal();
      });
    }

    // Modal message compose
    var msgBtn = $("#field-msg-new");
    if (msgBtn) msgBtn.addEventListener("click", openMsgModal);
    var msgHistoryBtn = $("#field-msg-show-history");
    if (msgHistoryBtn) msgHistoryBtn.addEventListener("click", openMsgHistoryModal);
    var msgModal = $("#field-msg-modal");
    if (msgModal) {
      $$("[data-close]", msgModal).forEach(function (b) {
        b.addEventListener("click", closeMsgModal);
      });
      msgModal.addEventListener("click", function (e) {
        if (e.target === msgModal) closeMsgModal();
      });
    }
    var msgSubmit = $("#field-msg-submit");
    if (msgSubmit) msgSubmit.addEventListener("click", submitMessage);
    var msgTargetMode = $("#field-msg-target-mode");
    if (msgTargetMode) msgTargetMode.addEventListener("change", updateMsgTargetRows);
    var msgType = $("#field-msg-type");
    if (msgType) msgType.addEventListener("change", updateMsgTypeRows);

    // Modal history
    var histModal = $("#field-msg-history-modal");
    if (histModal) {
      $$("[data-close]", histModal).forEach(function (b) {
        b.addEventListener("click", closeMsgHistoryModal);
      });
      histModal.addEventListener("click", function (e) {
        if (e.target === histModal) closeMsgHistoryModal();
      });
    }

    // Initial load : attendre que window.selectedEvent/Year soient dispos
    setTimeout(function () {
      refreshScopeUi();
      loadBeaconGroups();
      loadDevices();
      loadPairings();
      loadMessages();
    }, 800);

    // Poll periodique (toutes les 30s) pour maj la liste des tablettes
    state.pollTimer = setInterval(function () {
      loadDevices();
      loadPairings();
      loadMessages();
    }, 30000);

    // Reagir aux changements globaux event/year
    document.addEventListener("cockpit:scope-changed", function () {
      refreshScopeUi();
      loadDevices();
      loadPairings();
      loadMessages();
    });
  }

  // ------------------------------------------------------------------
  // Beacon groups (dropdown)
  // ------------------------------------------------------------------
  function loadBeaconGroups() {
    // Cache-busting via timestamp pour eviter qu'un proxy/navigateur ne nous
    // serve une reponse perimee.
    apiGet("/field/admin/beacon-groups?_=" + Date.now())
      .then(function (data) {
        state.beaconGroups = (data && data.groups) || [];
        renderBeaconGroupSelect();
      })
      .catch(function () { state.beaconGroups = []; });
  }

  function renderBeaconGroupSelect() {
    function fill(sel, placeholder) {
      if (!sel) return;
      sel.innerHTML = "";
      var opt0 = document.createElement("option");
      opt0.value = "";
      opt0.textContent = placeholder;
      sel.appendChild(opt0);
      if (!state.beaconGroups.length) {
        var optEmpty = document.createElement("option");
        optEmpty.value = "";
        optEmpty.disabled = true;
        optEmpty.textContent = "(aucun groupe configure)";
        sel.appendChild(optEmpty);
        return;
      }
      state.beaconGroups.forEach(function (g) {
        var opt = document.createElement("option");
        opt.value = g.id;
        var label = g.label + (g.pco_category ? " (" + g.pco_category + ")" : "");
        if (g.disabled) label += " [inactif]";
        opt.textContent = label;
        sel.appendChild(opt);
      });
    }
    fill($('select[name="beacon_group_id"]', $("#field-pair-form")), "-- Choisir un groupe --");
    fill($("#field-msg-group-select"), "-- Choisir un groupe --");
  }

  function beaconGroupLabel(id) {
    var g = state.beaconGroups.find(function (x) { return x.id === id; });
    return g ? g.label : (id || "-");
  }

  function beaconGroupColor(id) {
    var g = state.beaconGroups.find(function (x) { return x.id === id; });
    return g ? g.color : "#6366f1";
  }

  // ------------------------------------------------------------------
  // Tablettes enrolees
  // ------------------------------------------------------------------
  function loadDevices() {
    var s = currentScope();
    var qs = new URLSearchParams();
    if (s.event) qs.set("event", s.event);
    if (s.year) qs.set("year", s.year);
    apiGet("/field/admin/devices?" + qs.toString())
      .then(function (data) {
        state.devices = (data && data.devices) || [];
        renderDevices();
      })
      .catch(function () {
        state.devices = [];
        renderDevices();
      });
  }

  function renderDevices() {
    var tb = $("#field-devices-tbody");
    if (!tb) return;
    if (state.devices.length === 0) {
      tb.innerHTML = "<tr><td colspan='6' style='text-align:center; color:var(--muted); padding:16px;'>Aucune tablette enrolee dans cet evenement.</td></tr>";
      return;
    }
    tb.innerHTML = "";
    state.devices.forEach(function (d) {
      var tr = document.createElement("tr");

      var tdName = document.createElement("td");
      tdName.innerHTML = "<span class='material-symbols-outlined' style='font-size:14px; vertical-align:middle; color:var(--muted); margin-right:3px;'>tablet_android</span>";
      var nameSpan = document.createElement("span");
      nameSpan.textContent = d.name || "-";
      nameSpan.style.fontWeight = "600";
      tdName.appendChild(nameSpan);
      tr.appendChild(tdName);

      var tdGroup = document.createElement("td");
      var dot = document.createElement("span");
      dot.style.display = "inline-block";
      dot.style.width = "10px";
      dot.style.height = "10px";
      dot.style.borderRadius = "50%";
      dot.style.background = beaconGroupColor(d.beacon_group_id);
      dot.style.marginRight = "5px";
      tdGroup.appendChild(dot);
      tdGroup.appendChild(document.createTextNode(beaconGroupLabel(d.beacon_group_id)));
      tr.appendChild(tdGroup);

      var tdPos = document.createElement("td");
      if (d.last_position && d.last_position.lat != null) {
        tdPos.textContent = d.last_position.lat.toFixed(5) + ", " + d.last_position.lng.toFixed(5);
      } else {
        tdPos.innerHTML = "<span style='color:var(--muted);'>jamais</span>";
      }
      tr.appendChild(tdPos);

      var tdSeen = document.createElement("td");
      tdSeen.textContent = d.last_seen ? formatRelative(d.last_seen) : "-";
      tr.appendChild(tdSeen);

      var tdBat = document.createElement("td");
      if (d.last_position && d.last_position.battery != null) {
        tdBat.textContent = d.last_position.battery + "%";
      } else {
        tdBat.innerHTML = "<span style='color:var(--muted);'>-</span>";
      }
      tr.appendChild(tdBat);

      var tdAct = document.createElement("td");
      if (d.revoked) {
        var btnRestore = document.createElement("button");
        btnRestore.className = "btn btn-xs btn-success";
        btnRestore.title = "Re-autoriser cette tablette (sans nouveau code)";
        btnRestore.innerHTML = "<span class='material-symbols-outlined' style='font-size:14px;'>lock_open</span>";
        btnRestore.addEventListener("click", function () { restoreDevice(d); });
        tdAct.appendChild(btnRestore);
      } else {
        var btnRevoke = document.createElement("button");
        btnRevoke.className = "btn btn-xs";
        btnRevoke.title = "Revoquer (la tablette sera deconnectee)";
        btnRevoke.innerHTML = "<span class='material-symbols-outlined' style='font-size:14px;'>block</span>";
        btnRevoke.addEventListener("click", function () { revokeDevice(d); });
        tdAct.appendChild(btnRevoke);
      }

      var btnDel = document.createElement("button");
      btnDel.className = "btn btn-xs";
      btnDel.title = "Supprimer definitivement";
      btnDel.style.marginLeft = "3px";
      btnDel.innerHTML = "<span class='material-symbols-outlined' style='font-size:14px;'>delete</span>";
      btnDel.addEventListener("click", function () { deleteDevice(d); });
      tdAct.appendChild(btnDel);
      tr.appendChild(tdAct);

      if (d.revoked) {
        tr.style.opacity = "0.55";
        tr.style.background = "rgba(127, 29, 29, 0.12)";
        nameSpan.style.textDecoration = "line-through";
        var badge = document.createElement("span");
        var isEventEnded = d.revoke_reason === "event_ended";
        badge.textContent = isEventEnded ? "EVT TERMINE" : "REVOQUEE";
        var badgeColor = isEventEnded ? "#78350f" : "#7f1d1d";
        var badgeBg = isEventEnded ? "#fde68a" : "#fee2e2";
        badge.style.cssText = "margin-left:6px; font-size:9px; font-weight:800; background:" + badgeColor + "; color:" + badgeBg + "; padding:1px 5px; border-radius:4px; vertical-align:middle;";
        tdName.appendChild(badge);
      }

      tb.appendChild(tr);
    });
  }

  function formatRelative(iso) {
    try {
      var d = new Date(iso);
      var diff = (Date.now() - d.getTime()) / 1000;
      if (diff < 60) return "il y a " + Math.round(diff) + "s";
      if (diff < 3600) return "il y a " + Math.round(diff / 60) + " min";
      if (diff < 86400) return "il y a " + Math.round(diff / 3600) + " h";
      return d.toLocaleString();
    } catch (e) { return iso; }
  }

  function revokeDevice(d) {
    showConfirmToast(
      "Revoquer la tablette " + (d.name || "?") + " ? La tablette sera deconnectee mais conservee dans la liste : tu pourras la re-autoriser.",
      { okLabel: "Revoquer", cancelLabel: "Annuler", type: "warning" }
    ).then(function (ok) {
      if (!ok) return;
      apiPost("/field/admin/devices/" + d.id + "/revoke")
        .then(function (res) {
          if (res.body && res.body.ok) {
            _toast("success", "Tablette revoquee");
            loadDevices();
          } else {
            _toast("error", "Erreur : " + ((res.body && res.body.error) || "inconnue"));
          }
        })
        .catch(function () { _toast("error", "Erreur reseau"); });
    });
  }

  function restoreDevice(d) {
    showConfirmToast(
      "Re-autoriser la tablette " + (d.name || "?") + " ? Elle retrouvera automatiquement son acces sans nouveau code de pairing.",
      { okLabel: "Re-autoriser", cancelLabel: "Annuler", type: "info" }
    ).then(function (ok) {
      if (!ok) return;
      apiPost("/field/admin/devices/" + d.id + "/restore")
        .then(function (res) {
          if (res.body && res.body.ok) {
            _toast("success", "Tablette re-autorisee");
            loadDevices();
          } else {
            _toast("error", "Erreur : " + ((res.body && res.body.error) || "inconnue"));
          }
        })
        .catch(function () { _toast("error", "Erreur reseau"); });
    });
  }

  function deleteDevice(d) {
    showConfirmToast(
      "Supprimer definitivement la tablette " + (d.name || "?") + " ? Les messages associes seront egalement purges.",
      { okLabel: "Supprimer", cancelLabel: "Annuler", type: "error" }
    ).then(function (ok) {
      if (!ok) return;
      apiDelete("/field/admin/devices/" + d.id)
        .then(function (res) {
          if (res.body && res.body.ok) {
            _toast("success", "Tablette supprimee");
            loadDevices();
          } else {
            _toast("error", "Erreur : " + ((res.body && res.body.error) || "inconnue"));
          }
        })
        .catch(function () { _toast("error", "Erreur reseau"); });
    });
  }

  // ------------------------------------------------------------------
  // Pairings (codes actifs)
  // ------------------------------------------------------------------
  function loadPairings() {
    var s = currentScope();
    var qs = new URLSearchParams();
    if (s.event) qs.set("event", s.event);
    if (s.year) qs.set("year", s.year);
    apiGet("/field/admin/pairings?" + qs.toString())
      .then(function (data) {
        state.pairings = (data && data.pairings) || [];
        var countEl = $("#field-pair-count");
        if (countEl) countEl.textContent = String(state.pairings.length);
        renderPairingsTable();
      })
      .catch(function () {});
  }

  function renderPairingsTable() {
    var tb = $("#field-codes-tbody");
    if (!tb) return;
    if (state.pairings.length === 0) {
      tb.innerHTML = "<tr><td colspan='5' style='text-align:center; color:var(--muted); padding:16px;'>Aucun code en cours.</td></tr>";
      return;
    }
    tb.innerHTML = "";
    state.pairings.forEach(function (p) {
      var tr = document.createElement("tr");

      var tdCode = document.createElement("td");
      tdCode.style.fontFamily = "monospace";
      tdCode.style.fontWeight = "700";
      tdCode.style.fontSize = "14px";
      tdCode.style.letterSpacing = "2px";
      tdCode.textContent = p.code;
      tr.appendChild(tdCode);

      var tdName = document.createElement("td");
      tdName.textContent = p.name || "-";
      tr.appendChild(tdName);

      var tdGroup = document.createElement("td");
      tdGroup.textContent = beaconGroupLabel(p.beacon_group_id);
      tr.appendChild(tdGroup);

      var tdExp = document.createElement("td");
      tdExp.textContent = p.expiresAt ? formatRelative(p.expiresAt) : "-";
      tr.appendChild(tdExp);

      var tdAct = document.createElement("td");
      var btnDel = document.createElement("button");
      btnDel.className = "btn btn-xs";
      btnDel.title = "Annuler ce code";
      btnDel.innerHTML = "<span class='material-symbols-outlined' style='font-size:14px;'>close</span>";
      btnDel.addEventListener("click", function () { deletePairing(p); });
      tdAct.appendChild(btnDel);
      tr.appendChild(tdAct);

      tb.appendChild(tr);
    });
  }

  function deletePairing(p) {
    apiDelete("/field/admin/pairings/" + encodeURIComponent(p.code))
      .then(function (res) {
        if (res.body && res.body.ok) {
          _toast("success", "Code supprime");
          loadPairings();
        } else {
          _toast("error", "Erreur");
        }
      });
  }

  // ------------------------------------------------------------------
  // Modal pairing (creation)
  // ------------------------------------------------------------------
  function openPairModal() {
    refreshScopeUi();
    loadBeaconGroups();
    var form = $("#field-pair-form");
    if (form) form.reset();
    var result = $("#field-pair-result");
    if (result) result.innerHTML = "";
    var modal = $("#field-pair-modal");
    if (modal) modal.hidden = false;
  }

  function closePairModal() {
    var modal = $("#field-pair-modal");
    if (modal) modal.hidden = true;
  }

  function submitPairing() {
    var form = $("#field-pair-form");
    if (!form) return;
    var fd = new FormData(form);
    var scope = currentScope();
    if (!scope.event || !scope.year) {
      _toast("error", "Selectionne un evenement dans le header cockpit");
      return;
    }
    var payload = {
      name: (fd.get("name") || "").toString().trim(),
      beacon_group_id: (fd.get("beacon_group_id") || "").toString(),
      notes: (fd.get("notes") || "").toString().trim(),
      event: scope.event,
      year: scope.year,
    };
    if (!payload.name) { _toast("error", "Nom requis"); return; }
    if (!payload.beacon_group_id) { _toast("error", "Groupe requis"); return; }

    apiPost("/field/admin/pairings", payload)
      .then(function (res) {
        if (res.body && res.body.ok && res.body.pairing) {
          renderPairingResult(res.body.pairing);
          loadPairings();
        } else {
          var err = (res.body && res.body.error) || "unknown_error";
          var map = {
            missing_name: "Nom requis.",
            missing_event_year: "Evenement/annee manquants.",
            missing_beacon_group: "Groupe requis.",
            unknown_beacon_group: "Groupe introuvable.",
            beacon_group_disabled: "Groupe desactive.",
            name_conflict: "Ce nom est deja utilise par une balise Anoloc ou une tablette.",
          };
          _toast("error", map[err] || ("Erreur : " + err));
        }
      })
      .catch(function () { _toast("error", "Erreur reseau"); });
  }

  function renderPairingResult(p) {
    var el = $("#field-pair-result");
    if (!el) return;
    el.innerHTML = "";
    var box = document.createElement("div");
    box.style.padding = "14px";
    box.style.border = "2px solid var(--brand)";
    box.style.borderRadius = "var(--radius-sm)";
    box.style.background = "var(--bg)";
    box.style.textAlign = "center";
    var title = document.createElement("div");
    title.style.fontSize = "12px";
    title.style.color = "var(--muted)";
    title.style.marginBottom = "6px";
    title.textContent = "Code de pairing pour " + (p.name || "?");
    box.appendChild(title);
    var code = document.createElement("div");
    code.style.fontFamily = "monospace";
    code.style.fontSize = "38px";
    code.style.fontWeight = "800";
    code.style.letterSpacing = "8px";
    code.style.color = "var(--brand)";
    code.textContent = p.code;
    box.appendChild(code);
    var exp = document.createElement("div");
    exp.style.fontSize = "11px";
    exp.style.color = "var(--muted)";
    exp.style.marginTop = "4px";
    exp.textContent = "Expire dans 15 minutes. Saisis ce code sur la tablette apres avoir ouvert /field/pair.";
    box.appendChild(exp);
    el.appendChild(box);
  }

  // ------------------------------------------------------------------
  // Modal codes actifs
  // ------------------------------------------------------------------
  function openCodesModal() {
    loadPairings();
    var modal = $("#field-codes-modal");
    if (modal) modal.hidden = false;
  }

  function closeCodesModal() {
    var modal = $("#field-codes-modal");
    if (modal) modal.hidden = true;
  }

  // ------------------------------------------------------------------
  // Messages : envoi et historique
  // ------------------------------------------------------------------
  function loadMessages() {
    var s = currentScope();
    if (!s.event || !s.year) {
      state.messages = [];
      renderMessagesCount();
      renderMessagesTable();
      return;
    }
    var qs = new URLSearchParams();
    qs.set("event", s.event);
    qs.set("year", s.year);
    qs.set("limit", "100");
    apiGet("/field/admin/messages?" + qs.toString())
      .then(function (data) {
        state.messages = (data && data.messages) || [];
        renderMessagesCount();
        renderMessagesTable();
      })
      .catch(function () {
        state.messages = [];
        renderMessagesCount();
        renderMessagesTable();
      });
  }

  function renderMessagesCount() {
    var el = $("#field-msg-count");
    if (el) el.textContent = String(state.messages.length);
  }

  function renderMessagesTable() {
    var tb = $("#field-msg-history-tbody");
    if (!tb) return;
    if (state.messages.length === 0) {
      tb.innerHTML = "<tr><td colspan='6' style='text-align:center; color:var(--muted); padding:16px;'>Aucun message envoye.</td></tr>";
      return;
    }
    tb.innerHTML = "";
    state.messages.forEach(function (m) {
      var tr = document.createElement("tr");

      var tdTs = document.createElement("td");
      tdTs.textContent = m.created_at ? formatRelative(m.created_at) : "-";
      tdTs.title = m.created_at || "";
      tr.appendChild(tdTs);

      var tdDev = document.createElement("td");
      tdDev.innerHTML = "<span class='material-symbols-outlined' style='font-size:12px; vertical-align:middle; color:var(--muted); margin-right:2px;'>tablet_android</span>";
      tdDev.appendChild(document.createTextNode(m.device_name || "-"));
      tr.appendChild(tdDev);

      var tdType = document.createElement("td");
      var typeLabel = { info: "Info", instruction: "Instruction", alert: "Alerte", route: "Itineraire" }[m.type] || m.type;
      var badge = document.createElement("span");
      badge.textContent = typeLabel;
      badge.style.fontSize = "10px";
      badge.style.padding = "1px 6px";
      badge.style.borderRadius = "4px";
      if (m.type === "alert") {
        badge.style.background = "#fee2e2";
        badge.style.color = "#991b1b";
      } else if (m.type === "instruction") {
        badge.style.background = "#fef3c7";
        badge.style.color = "#92400e";
      } else if (m.type === "route") {
        badge.style.background = "#dbeafe";
        badge.style.color = "#1e40af";
      } else {
        badge.style.background = "#e5e7eb";
        badge.style.color = "#374151";
      }
      tdType.appendChild(badge);
      if (m.priority === "high") {
        var prio = document.createElement("span");
        prio.textContent = " !";
        prio.style.color = "#dc2626";
        prio.style.fontWeight = "700";
        tdType.appendChild(prio);
      }
      tr.appendChild(tdType);

      var tdTitle = document.createElement("td");
      tdTitle.textContent = m.title || "(sans titre)";
      tdTitle.title = m.body || "";
      tdTitle.style.maxWidth = "240px";
      tdTitle.style.overflow = "hidden";
      tdTitle.style.textOverflow = "ellipsis";
      tdTitle.style.whiteSpace = "nowrap";
      tr.appendChild(tdTitle);

      var tdStatus = document.createElement("td");
      if (m.status === "read") {
        tdStatus.innerHTML = "<span style='color:#059669;'><span class='material-symbols-outlined' style='font-size:14px; vertical-align:middle;'>done_all</span> Lu " + (m.ack_at ? formatRelative(m.ack_at) : "") + "</span>";
      } else {
        tdStatus.innerHTML = "<span style='color:var(--muted);'><span class='material-symbols-outlined' style='font-size:14px; vertical-align:middle;'>schedule</span> Non lu</span>";
      }
      tr.appendChild(tdStatus);

      var tdAct = document.createElement("td");
      var btnDel = document.createElement("button");
      btnDel.className = "btn btn-xs";
      btnDel.title = "Supprimer ce message";
      btnDel.innerHTML = "<span class='material-symbols-outlined' style='font-size:14px;'>delete</span>";
      btnDel.addEventListener("click", function () { deleteMessage(m); });
      tdAct.appendChild(btnDel);
      tr.appendChild(tdAct);

      tb.appendChild(tr);
    });
  }

  function deleteMessage(m) {
    showConfirmToast(
      "Supprimer ce message ? (la tablette l'a peut-etre deja recu)",
      { okLabel: "Supprimer", cancelLabel: "Annuler", type: "warning" }
    ).then(function (ok) {
      if (!ok) return;
      apiDelete("/field/admin/messages/" + encodeURIComponent(m.id))
        .then(function (res) {
          if (res.body && res.body.ok) {
            _toast("success", "Message supprime");
            loadMessages();
          } else {
            _toast("error", "Erreur");
          }
        });
    });
  }

  function openMsgModal(prefill) {
    refreshScopeUi();
    loadBeaconGroups();
    var form = $("#field-msg-form");
    if (form) form.reset();
    var result = $("#field-msg-result");
    if (result) result.innerHTML = "";

    // Remplir la liste des tablettes disponibles (scope courant)
    var sel = $("#field-msg-devices-select");
    if (sel) {
      sel.innerHTML = "";
      state.devices.forEach(function (d) {
        var opt = document.createElement("option");
        opt.value = d.id;
        opt.textContent = d.name + " (" + beaconGroupLabel(d.beacon_group_id) + ")";
        sel.appendChild(opt);
      });
    }

    // Preselection eventuelle (appel depuis la carte operateur / clic droit)
    if (prefill && prefill.device_id) {
      var modeSel = $("#field-msg-target-mode");
      if (modeSel) modeSel.value = "devices";
      if (sel) {
        Array.from(sel.options).forEach(function (o) {
          o.selected = (o.value === prefill.device_id);
        });
      }
    } else {
      var modeSel2 = $("#field-msg-target-mode");
      if (modeSel2) modeSel2.value = "all";
    }
    updateMsgTargetRows();
    updateMsgTypeRows();

    var modal = $("#field-msg-modal");
    if (modal) modal.hidden = false;
  }

  function closeMsgModal() {
    var modal = $("#field-msg-modal");
    if (modal) modal.hidden = true;
  }

  function updateMsgTargetRows() {
    var mode = ($("#field-msg-target-mode") || {}).value || "all";
    var groupRow = $("#field-msg-group-row");
    var devRow = $("#field-msg-devices-row");
    if (groupRow) groupRow.hidden = (mode !== "beacon_group");
    if (devRow) devRow.hidden = (mode !== "devices");
  }

  function updateMsgTypeRows() {
    var type = ($("#field-msg-type") || {}).value || "info";
    var routeRow = $("#field-msg-route-row");
    if (routeRow) routeRow.hidden = (type !== "route");
  }

  function parseLatLng(raw) {
    if (!raw) return null;
    // Accepte "lat, lng", "lat lng", "lat;lng"
    var parts = String(raw).trim().split(/[\s,;]+/);
    if (parts.length < 2) return null;
    var lat = parseFloat(parts[0]);
    var lng = parseFloat(parts[1]);
    if (isNaN(lat) || isNaN(lng)) return null;
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
    return [lat, lng];
  }

  function submitMessage() {
    var scope = currentScope();
    if (!scope.event || !scope.year) {
      _toast("error", "Selectionne un evenement dans le header cockpit");
      return;
    }
    var mode = ($("#field-msg-target-mode") || {}).value || "all";
    var title = ($("#field-msg-title") || {}).value || "";
    var body = ($("#field-msg-body") || {}).value || "";
    var type = ($("#field-msg-type") || {}).value || "info";
    var priority = ($("#field-msg-priority") || {}).value || "normal";

    if (!title.trim() && !body.trim()) {
      _toast("error", "Saisis un titre ou un message");
      return;
    }

    var target = {};
    if (mode === "all") {
      target.all = true;
    } else if (mode === "beacon_group") {
      var gid = ($("#field-msg-group-select") || {}).value || "";
      if (!gid) { _toast("error", "Choisis un groupe"); return; }
      target.beacon_group_id = gid;
    } else if (mode === "devices") {
      var sel = $("#field-msg-devices-select");
      var ids = sel ? Array.from(sel.selectedOptions).map(function (o) { return o.value; }) : [];
      if (ids.length === 0) { _toast("error", "Selectionne au moins une tablette"); return; }
      target.device_ids = ids;
    }

    var payload = {
      event: scope.event,
      year: scope.year,
      target: target,
      type: type,
      title: title.trim(),
      body: body.trim(),
      priority: priority,
    };

    if (type === "route") {
      var dest = ($("#field-msg-destination") || {}).value || "";
      var parsed = parseLatLng(dest);
      if (!parsed) {
        _toast("error", "Destination invalide. Format attendu : lat, lng");
        return;
      }
      payload.payload = { waypoints: [parsed] };
      if (!payload.title) payload.title = "Itineraire";
      if (!payload.body) payload.body = "Destination : " + parsed[0].toFixed(5) + ", " + parsed[1].toFixed(5);
    }

    apiPost("/field/admin/send", payload)
      .then(function (res) {
        if (res.body && res.body.ok) {
          _toast("success", "Message envoye a " + res.body.sent_count + " tablette(s)");
          var result = $("#field-msg-result");
          if (result) {
            result.innerHTML = "";
            var box = document.createElement("div");
            box.style.padding = "10px";
            box.style.border = "1px solid var(--line)";
            box.style.borderRadius = "var(--radius-sm)";
            box.style.background = "var(--bg)";
            box.style.fontSize = "12px";
            box.innerHTML = "<b>" + res.body.sent_count + " tablette(s) destinataire(s) :</b> "
              + (res.body.targets || []).map(function (t) { return t.name; }).join(", ");
            result.appendChild(box);
          }
          loadMessages();
          // Fermer le modal apres 1.5s si succes
          setTimeout(closeMsgModal, 1500);
        } else {
          var err = (res.body && res.body.error) || "unknown_error";
          var map = {
            missing_event_year: "Evenement/annee manquants.",
            invalid_type: "Type de message invalide.",
            empty_message: "Le titre ou le contenu est requis.",
            title_too_long: "Titre trop long (max 120).",
            body_too_long: "Message trop long (max 4000).",
            invalid_target: "Cible invalide.",
            missing_target: "Cible manquante.",
            invalid_device_id: "Identifiant de tablette invalide.",
            empty_device_ids: "Aucune tablette selectionnee.",
            no_target_matched: "Aucune tablette ne correspond a cette cible.",
          };
          _toast("error", map[err] || ("Erreur : " + err));
        }
      })
      .catch(function () { _toast("error", "Erreur reseau"); });
  }

  function openMsgHistoryModal() {
    loadMessages();
    var modal = $("#field-msg-history-modal");
    if (modal) modal.hidden = false;
  }

  function closeMsgHistoryModal() {
    var modal = $("#field-msg-history-modal");
    if (modal) modal.hidden = true;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.FieldAdmin = {
    reload: function () {
      loadBeaconGroups();
      loadDevices();
      loadPairings();
      loadMessages();
    },
    openCompose: function (prefill) { openMsgModal(prefill); },
  };
})();
