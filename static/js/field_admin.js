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

    // Initial load : attendre que window.selectedEvent/Year soient dispos
    setTimeout(function () {
      refreshScopeUi();
      loadBeaconGroups();
      loadDevices();
      loadPairings();
    }, 800);

    // Poll periodique (toutes les 30s) pour maj la liste des tablettes
    state.pollTimer = setInterval(function () {
      loadDevices();
      loadPairings();
    }, 30000);

    // Reagir aux changements globaux event/year
    document.addEventListener("cockpit:scope-changed", function () {
      refreshScopeUi();
      loadDevices();
      loadPairings();
    });
  }

  // ------------------------------------------------------------------
  // Beacon groups (dropdown)
  // ------------------------------------------------------------------
  function loadBeaconGroups() {
    apiGet("/field/admin/beacon-groups")
      .then(function (data) {
        state.beaconGroups = (data && data.groups) || [];
        renderBeaconGroupSelect();
      })
      .catch(function () { state.beaconGroups = []; });
  }

  function renderBeaconGroupSelect() {
    var sel = $('select[name="beacon_group_id"]', $("#field-pair-form"));
    if (!sel) return;
    sel.innerHTML = "";
    var opt0 = document.createElement("option");
    opt0.value = "";
    opt0.textContent = "-- Choisir un groupe --";
    sel.appendChild(opt0);
    state.beaconGroups.forEach(function (g) {
      var opt = document.createElement("option");
      opt.value = g.id;
      opt.textContent = g.label + (g.pco_category ? " (" + g.pco_category + ")" : "");
      sel.appendChild(opt);
    });
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
      var btnRevoke = document.createElement("button");
      btnRevoke.className = "btn btn-xs";
      btnRevoke.title = "Revoquer (la tablette sera deconnectee)";
      btnRevoke.innerHTML = "<span class='material-symbols-outlined' style='font-size:14px;'>block</span>";
      btnRevoke.addEventListener("click", function () { revokeDevice(d); });
      tdAct.appendChild(btnRevoke);

      var btnDel = document.createElement("button");
      btnDel.className = "btn btn-xs";
      btnDel.title = "Supprimer definitivement";
      btnDel.style.marginLeft = "3px";
      btnDel.innerHTML = "<span class='material-symbols-outlined' style='font-size:14px;'>delete</span>";
      btnDel.addEventListener("click", function () { deleteDevice(d); });
      tdAct.appendChild(btnDel);
      tr.appendChild(tdAct);

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
    if (!confirm("Revoquer la tablette " + (d.name || "?") + " ?\nLa tablette sera deconnectee et devra etre re-appairee.")) return;
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
  }

  function deleteDevice(d) {
    if (!confirm("Supprimer definitivement la tablette " + (d.name || "?") + " ?\nLes messages associes seront egalement purges.")) return;
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
    },
  };
})();
