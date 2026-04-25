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

    // Poll leger des compteurs non-lus : plus frequent pour reactivite
    setInterval(loadUnreadByDevice, 8000);

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
    var qs = new URLSearchParams();
    // Si window.FIELD_ADMIN_ALL_SCOPES est vrai (mode console dispatch), on
    // charge TOUTES les tablettes sans filtre event/year pour que l'operateur
    // PCO voie le parc complet d'un coup d'oeil.
    if (!window.FIELD_ADMIN_ALL_SCOPES) {
      var s = currentScope();
      if (s.event) qs.set("event", s.event);
      if (s.year) qs.set("year", s.year);
    }
    apiGet("/field/admin/devices?" + qs.toString())
      .then(function (data) {
        state.devices = (data && data.devices) || [];
        renderDevices();
        loadUnreadByDevice();
      })
      .catch(function () {
        state.devices = [];
        renderDevices();
      });
  }

  function renderDevices() {
    var tb = $("#field-devices-tbody");
    if (!tb) return;
    var firstHeader = document.querySelector("#field-devices-table thead th:nth-child(2)");
    var hasEventCol = !!(firstHeader && /evenement/i.test(firstHeader.textContent));
    var colspan = hasEventCol ? 7 : 6;
    var countEl = $("#field-admin-count");
    if (countEl) {
      countEl.textContent = state.devices.length
        ? (state.devices.length + " tablette" + (state.devices.length > 1 ? "s" : ""))
        : "";
    }
    while (tb.firstChild) tb.removeChild(tb.firstChild);
    if (state.devices.length === 0) {
      var emptyTr = document.createElement("tr");
      var emptyTd = document.createElement("td");
      emptyTd.colSpan = colspan;
      emptyTd.style.cssText = "text-align:center; color:var(--muted); padding:16px;";
      emptyTd.textContent = window.FIELD_ADMIN_ALL_SCOPES
        ? "Aucune tablette enrolee."
        : "Aucune tablette enrolee dans cet evenement.";
      emptyTr.appendChild(emptyTd);
      tb.appendChild(emptyTr);
      return;
    }
    var sorted = state.devices.slice().sort(function (a, b) {
      var ea = (a.event || "") + "/" + (a.year || "");
      var eb = (b.event || "") + "/" + (b.year || "");
      if (ea !== eb) return ea.localeCompare(eb);
      return (a.name || "").localeCompare(b.name || "");
    });
    sorted.forEach(function (d) {
      var tr = document.createElement("tr");
      tr.setAttribute("data-device-id", d.id);

      var tdName = document.createElement("td");
      var tabletIcon = document.createElement("span");
      tabletIcon.className = "material-symbols-outlined";
      tabletIcon.style.cssText = "font-size:14px; vertical-align:middle; color:var(--muted); margin-right:3px;";
      tabletIcon.textContent = "tablet_android";
      tdName.appendChild(tabletIcon);
      var nameSpan = document.createElement("span");
      nameSpan.textContent = d.name || "-";
      nameSpan.style.fontWeight = "600";
      tdName.appendChild(nameSpan);
      tr.appendChild(tdName);

      if (hasEventCol) {
        var tdEvent = document.createElement("td");
        tdEvent.style.whiteSpace = "nowrap";
        tdEvent.style.color = "var(--muted)";
        tdEvent.style.fontSize = "12px";
        tdEvent.textContent = (d.event || "-") + (d.year ? " / " + d.year : "");
        tr.appendChild(tdEvent);
      }

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
      tdAct.style.whiteSpace = "nowrap";
      tdAct.style.textAlign = "right";
      var actWrap = document.createElement("div");
      actWrap.style.cssText = "display:inline-flex; gap:4px; align-items:center;";
      tdAct.appendChild(actWrap);

      function mkActionBtn(icon, title, extraClass, onClick) {
        var b = document.createElement("button");
        b.className = "btn btn-xs" + (extraClass ? " " + extraClass : "");
        b.title = title;
        b.style.cssText = "width:32px; height:28px; padding:0; display:inline-flex; align-items:center; justify-content:center; flex:0 0 auto;";
        var ic = document.createElement("span");
        ic.className = "material-symbols-outlined";
        ic.style.fontSize = "16px";
        ic.textContent = icon;
        b.appendChild(ic);
        b.addEventListener("click", onClick);
        return b;
      }

      if (!d.revoked) {
        actWrap.appendChild(mkActionBtn(
          "chat",
          "Ouvrir la conversation avec cette tablette",
          "btn-primary",
          function () { openConversationModal(d); }
        ));
      }

      if (d.revoked) {
        actWrap.appendChild(mkActionBtn(
          "lock_open",
          "Re-autoriser cette tablette (sans nouveau code)",
          "btn-success",
          function () { restoreDevice(d); }
        ));
      } else {
        actWrap.appendChild(mkActionBtn(
          "block",
          "Revoquer (la tablette sera deconnectee)",
          null,
          function () { revokeDevice(d); }
        ));
      }

      actWrap.appendChild(mkActionBtn(
        "delete",
        "Supprimer definitivement",
        null,
        function () { deleteDevice(d); }
      ));
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
  var pairPollHandle = null;

  function stopPairPoll() {
    if (pairPollHandle) {
      clearInterval(pairPollHandle);
      pairPollHandle = null;
    }
  }

  // Surveille la disparition d'un code dans les pairings actifs : disparu avant
  // expiration = consomme par la tablette -> ferme la modale + refresh.
  function startPairPoll(code, expiresAtIso) {
    stopPairPoll();
    var expMs = expiresAtIso ? new Date(expiresAtIso).getTime() : null;
    pairPollHandle = setInterval(function () {
      var s = currentScope();
      var qs = new URLSearchParams();
      if (s.event) qs.set("event", s.event);
      if (s.year) qs.set("year", s.year);
      apiGet("/field/admin/pairings?" + qs.toString())
        .then(function (data) {
          var pairings = (data && data.pairings) || [];
          var stillActive = pairings.some(function (p) { return p.code === code; });
          if (stillActive) return;
          stopPairPoll();
          if (expMs && Date.now() < expMs) {
            closePairModal();
            _toast("success", "Tablette appairee !");
            loadDevices();
            loadPairings();
          }
        })
        .catch(function () {});
    }, 2000);
  }

  function openPairModal() {
    refreshScopeUi();
    loadBeaconGroups();
    var form = $("#field-pair-form");
    if (form) form.reset();
    var result = $("#field-pair-result");
    if (result) result.textContent = "";
    stopPairPoll();
    var modal = $("#field-pair-modal");
    if (modal) modal.hidden = false;
  }

  function closePairModal() {
    stopPairPoll();
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
    var waiting = document.createElement("div");
    waiting.style.cssText = "margin-top:8px; font-size:11px; color:var(--muted); font-style:italic;";
    waiting.textContent = "En attente de saisie sur la tablette...";
    box.appendChild(waiting);
    el.appendChild(box);

    // Fermeture auto + refresh quand la tablette consomme le code
    if (p.code) startPairPoll(p.code, p.expiresAt);
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
  function openPhotoLightbox(url) {
    var existing = document.getElementById("field-admin-lightbox");
    if (existing) { try { existing.remove(); } catch (e) {} }
    var lb = document.createElement("div");
    lb.id = "field-admin-lightbox";
    lb.style.cssText = "position:fixed; inset:0; background:rgba(0,0,0,0.88); z-index:6000; display:flex; align-items:center; justify-content:center; cursor:zoom-out; padding:20px;";
    var img = document.createElement("img");
    img.src = url;
    img.style.cssText = "max-width:100%; max-height:100%; border-radius:4px; box-shadow:0 8px 30px rgba(0,0,0,0.5);";
    lb.appendChild(img);
    lb.addEventListener("click", function () { lb.remove(); });
    document.addEventListener("keydown", function esc(e) {
      if (e.key === "Escape") { lb.remove(); document.removeEventListener("keydown", esc); }
    });
    document.body.appendChild(lb);
  }

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
      var isInbound = m.direction === "field_to_cockpit";
      if (isInbound) {
        var dirIcon = document.createElement("span");
        dirIcon.className = "material-symbols-outlined";
        dirIcon.style.cssText = "font-size:14px; vertical-align:middle; color:#0369a1; margin-right:4px;";
        dirIcon.textContent = "call_received";
        dirIcon.title = "Message recu de la tablette";
        tdType.appendChild(dirIcon);
      }
      var typeLabel = {
        info: "Info", instruction: "Instruction", alert: "Alerte", route: "Itineraire",
        photo_report: "Photo", sos_broadcast: "SOS"
      }[m.type] || m.type;
      var badge = document.createElement("span");
      badge.textContent = typeLabel;
      badge.style.fontSize = "10px";
      badge.style.padding = "1px 6px";
      badge.style.borderRadius = "4px";
      if (m.type === "alert" || m.type === "sos_broadcast") {
        badge.style.background = "#fee2e2";
        badge.style.color = "#991b1b";
      } else if (m.type === "instruction") {
        badge.style.background = "#fef3c7";
        badge.style.color = "#92400e";
      } else if (m.type === "route") {
        badge.style.background = "#dbeafe";
        badge.style.color = "#1e40af";
      } else if (m.type === "photo_report") {
        badge.style.background = "#dcfce7";
        badge.style.color = "#166534";
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
      tdTitle.style.maxWidth = "260px";
      // Miniature photo si le message en contient une
      var photoUrl = m.payload && m.payload.photo;
      var thumbUrl = (m.payload && m.payload.thumb) || photoUrl;
      if (photoUrl) {
        var thumb = document.createElement("img");
        thumb.src = thumbUrl;
        thumb.alt = "Photo";
        thumb.loading = "lazy";
        thumb.style.cssText = "width:40px; height:40px; object-fit:cover; border-radius:4px; margin-right:8px; vertical-align:middle; cursor:zoom-in; border:1px solid var(--line);";
        thumb.addEventListener("click", function (e) {
          e.stopPropagation();
          openPhotoLightbox(photoUrl);
        });
        tdTitle.appendChild(thumb);
      }
      var titleText = document.createElement("span");
      titleText.textContent = m.title || (isInbound ? (m.body || "(photo)") : "(sans titre)");
      titleText.style.cssText = "overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:middle;";
      tdTitle.appendChild(titleText);
      tdTitle.title = m.body || "";
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

  // ------------------------------------------------------------------
  // Conversations : une tablette a plusieurs fils (threads). L'UI a 3 vues :
  //   - liste : tous les fils de la tablette avec preview + unread
  //   - thread : les messages d'un fil + zone de reply
  //   - new : formulaire pour creer un nouveau fil
  // ------------------------------------------------------------------
  var convState = { device: null, view: "list", activeThreadId: null, pollTimer: null };

  function openConversationModal(device) {
    convState.device = device;
    convState.view = "list";
    convState.activeThreadId = null;
    var modal = $("#field-conv-modal");
    if (!modal) return;
    wireConversationModalOnce(modal);
    updateConvTitle();
    switchConvView("list");
    loadThreads();
    modal.hidden = false;
    if (convState.pollTimer) clearInterval(convState.pollTimer);
    convState.pollTimer = setInterval(function () {
      if (modal.hidden) return;
      if (convState.view === "list") loadThreads(true);
      else if (convState.view === "thread") loadThreadMessages(true);
    }, 4000);
  }

  function closeConversationModal() {
    var modal = $("#field-conv-modal");
    if (modal) modal.hidden = true;
    if (convState.pollTimer) { clearInterval(convState.pollTimer); convState.pollTimer = null; }
    convState.device = null;
    convState.activeThreadId = null;
    loadUnreadByDevice();
  }

  function wireConversationModalOnce(modal) {
    if (modal._wired) return;
    modal._wired = true;
    Array.prototype.forEach.call(modal.querySelectorAll("[data-close]"), function (b) {
      b.addEventListener("click", closeConversationModal);
    });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) closeConversationModal();
    });
    $("#field-conv-back").addEventListener("click", function () {
      switchConvView("list");
      loadThreads();
    });
    $("#field-conv-new-thread").addEventListener("click", function () {
      switchConvView("new");
      var t = $("#field-conv-new-title"); if (t) { t.value = ""; setTimeout(function () { t.focus(); }, 80); }
      var b = $("#field-conv-new-body"); if (b) b.value = "";
      var ty = $("#field-conv-new-type"); if (ty) ty.value = "info";
      var pr = $("#field-conv-new-priority"); if (pr) pr.checked = false;
    });
    $("#field-conv-new-cancel").addEventListener("click", function () { switchConvView("list"); });
    $("#field-conv-new-submit").addEventListener("click", submitNewThread);
    $("#field-conv-send").addEventListener("click", sendReplyInThread);
    var input = $("#field-conv-input");
    if (input) input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendReplyInThread(); }
    });
  }

  function switchConvView(view) {
    convState.view = view;
    var listView = $("#field-conv-list-view");
    var threadView = $("#field-conv-thread-view");
    var newView = $("#field-conv-new-view");
    var backBtn = $("#field-conv-back");
    if (listView) listView.style.display = (view === "list") ? "flex" : "none";
    if (threadView) threadView.style.display = (view === "thread") ? "flex" : "none";
    if (newView) newView.style.display = (view === "new") ? "flex" : "none";
    if (backBtn) backBtn.hidden = (view === "list");
    updateConvTitle();
  }

  function updateConvTitle() {
    var title = $("#field-conv-title");
    if (!title || !convState.device) return;
    var d = convState.device;
    var prefix = d.name + (d.event ? " (" + d.event + (d.year ? " / " + d.year : "") + ")" : "");
    if (convState.view === "thread" && state.currentThread && state.currentThread.title) {
      title.textContent = state.currentThread.title;
      title.title = prefix;
    } else if (convState.view === "new") {
      title.textContent = "Nouveau fil - " + prefix;
    } else {
      title.textContent = "Conversations - " + prefix;
    }
  }

  // --- Liste des fils -------------------------------------------------
  function loadThreads(silent) {
    if (!convState.device) return;
    var box = $("#field-conv-threads");
    if (!box) return;
    if (!silent) {
      while (box.firstChild) box.removeChild(box.firstChild);
      var ph = document.createElement("div");
      ph.style.cssText = "text-align:center; color:var(--muted); padding:20px;";
      ph.textContent = "Chargement...";
      box.appendChild(ph);
    }
    apiGet("/field/admin/threads/" + encodeURIComponent(convState.device.id))
      .then(function (data) {
        if (!data || !data.ok) return;
        renderThreadsList(data.threads || []);
      })
      .catch(function () { /* silent */ });
  }

  function renderThreadsList(threads) {
    var box = $("#field-conv-threads");
    if (!box) return;
    while (box.firstChild) box.removeChild(box.firstChild);
    if (threads.length === 0) {
      var empty = document.createElement("div");
      empty.style.cssText = "text-align:center; color:var(--muted); padding:30px 20px;";
      empty.textContent = "Aucun fil. Cree-en un avec 'Nouveau fil' ou attends qu'une photo arrive de la tablette.";
      box.appendChild(empty);
      return;
    }
    threads.forEach(function (t) {
      var row = document.createElement("div");
      row.style.cssText = "display:flex; gap:10px; padding:10px 14px; border-bottom:1px solid var(--line); cursor:pointer; align-items:flex-start;";
      row.addEventListener("mouseenter", function () { row.style.background = "var(--card)"; });
      row.addEventListener("mouseleave", function () { row.style.background = ""; });

      // Thumbnail photo ou icone type
      var thumb = document.createElement("div");
      thumb.style.cssText = "flex:0 0 auto; width:46px; height:46px; border-radius:6px; background:var(--card); display:flex; align-items:center; justify-content:center; overflow:hidden;";
      if (t.photo) {
        var img = document.createElement("img");
        img.src = t.photo;
        img.alt = "";
        img.style.cssText = "width:100%; height:100%; object-fit:cover;";
        thumb.appendChild(img);
      } else {
        var icon = document.createElement("span");
        icon.className = "material-symbols-outlined";
        icon.style.cssText = "font-size:22px; color:var(--muted);";
        icon.textContent = t.type === "alert" ? "warning"
          : t.type === "instruction" ? "rule"
          : t.type === "route" ? "navigation"
          : t.type === "sos_broadcast" ? "emergency"
          : "chat";
        thumb.appendChild(icon);
      }
      row.appendChild(thumb);

      var main = document.createElement("div");
      main.style.cssText = "flex:1; min-width:0;";
      var topRow = document.createElement("div");
      topRow.style.cssText = "display:flex; gap:8px; align-items:baseline;";
      var ttl = document.createElement("div");
      ttl.style.cssText = "flex:1; font-size:14px; font-weight:700; color:#0f172a; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;";
      ttl.textContent = t.title;
      topRow.appendChild(ttl);
      var when = document.createElement("div");
      when.style.cssText = "flex:0 0 auto; font-size:11px; color:var(--muted);";
      when.textContent = t.last_at ? formatRelative(t.last_at) : "";
      topRow.appendChild(when);
      main.appendChild(topRow);

      var sub = document.createElement("div");
      sub.style.cssText = "display:flex; gap:6px; align-items:center; margin-top:2px;";
      var previewEl = document.createElement("div");
      previewEl.style.cssText = "flex:1; font-size:12px; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;";
      var arrow = (t.last_direction === "field_to_cockpit" ? "⬅  " : "➡  ");
      previewEl.textContent = (arrow + (t.last_preview || "")).trim();
      sub.appendChild(previewEl);
      if (t.reply_count > 0) {
        var rc = document.createElement("span");
        rc.style.cssText = "font-size:10px; color:var(--muted);";
        rc.textContent = t.reply_count + " rep.";
        sub.appendChild(rc);
      }
      if (t.unread > 0) {
        var badge = document.createElement("span");
        badge.style.cssText = "min-width:18px; height:18px; padding:0 5px; background:#dc2626; color:#fff; font-size:10px; font-weight:800; border-radius:9px; line-height:18px; text-align:center;";
        badge.textContent = t.unread > 9 ? "9+" : String(t.unread);
        sub.appendChild(badge);
      }
      main.appendChild(sub);

      row.appendChild(main);
      row.addEventListener("click", function () { openThread(t); });
      box.appendChild(row);
    });
  }

  // --- Vue fil detail -------------------------------------------------
  function openThread(thread) {
    convState.activeThreadId = thread.root_id;
    state.currentThread = thread;
    switchConvView("thread");
    loadThreadMessages();
    markThreadRead(thread.root_id);
    var input = $("#field-conv-input");
    if (input) input.value = "";
  }

  function loadThreadMessages(silent) {
    if (!convState.activeThreadId) return;
    var list = $("#field-conv-thread-list");
    if (!list) return;
    if (!silent) {
      while (list.firstChild) list.removeChild(list.firstChild);
      var ph = document.createElement("div");
      ph.style.cssText = "text-align:center; color:var(--muted); padding:20px;";
      ph.textContent = "Chargement...";
      list.appendChild(ph);
    }
    apiGet("/field/admin/thread/" + encodeURIComponent(convState.activeThreadId))
      .then(function (data) {
        if (!data || !data.ok) return;
        renderThreadMessages(data.messages || []);
      })
      .catch(function () { /* silent */ });
  }

  function renderThreadMessages(messages) {
    var list = $("#field-conv-thread-list");
    if (!list) return;
    while (list.firstChild) list.removeChild(list.firstChild);
    if (messages.length === 0) {
      var empty = document.createElement("div");
      empty.style.cssText = "text-align:center; color:var(--muted); padding:30px 20px;";
      empty.textContent = "Fil vide.";
      list.appendChild(empty);
      return;
    }
    messages.forEach(function (m) {
      var isInbound = m.direction === "field_to_cockpit";
      var bubble = document.createElement("div");
      bubble.style.cssText = "max-width:78%; padding:8px 12px; border-radius:12px; "
        + "font-size:13px; line-height:1.4; word-wrap:break-word; box-shadow:0 1px 2px rgba(0,0,0,0.08);"
        + (isInbound
           ? "align-self:flex-start; background:#fff; border:1px solid var(--line); color:#0f172a;"
           : "align-self:flex-end; background:#2563eb; color:#fff;");
      if (m.type === "alert" || m.type === "sos_broadcast") {
        bubble.style.borderLeft = "3px solid #dc2626";
      }
      if (m.title) {
        var t = document.createElement("div");
        t.style.cssText = "font-weight:700; font-size:12px; margin-bottom:3px; opacity:0.9;";
        t.textContent = m.title;
        bubble.appendChild(t);
      }
      var photoUrl = m.payload && m.payload.photo;
      var thumbUrl = (m.payload && m.payload.thumb) || photoUrl;
      if (photoUrl) {
        var img = document.createElement("img");
        img.src = thumbUrl;
        img.alt = "Photo";
        img.loading = "lazy";
        img.style.cssText = "display:block; max-width:100%; max-height:220px; border-radius:6px; margin:4px 0; cursor:zoom-in; background:#000;";
        img.addEventListener("click", function () { openPhotoLightbox(photoUrl); });
        bubble.appendChild(img);
      }
      if (m.body) {
        var b = document.createElement("div");
        b.textContent = m.body;
        bubble.appendChild(b);
      }
      var meta = document.createElement("div");
      meta.style.cssText = "font-size:10px; opacity:0.7; margin-top:4px; text-align:right;";
      meta.textContent = m.created_at ? formatRelative(m.created_at) : "";
      bubble.appendChild(meta);
      list.appendChild(bubble);
    });
    var body = $("#field-conv-thread-body");
    if (body) body.scrollTop = body.scrollHeight;
  }

  function sendReplyInThread() {
    if (!convState.activeThreadId) return;
    var input = $("#field-conv-input");
    var sendBtn = $("#field-conv-send");
    if (!input || !sendBtn) return;
    var body = (input.value || "").trim();
    if (!body) { input.focus(); return; }
    sendBtn.disabled = true;
    var fd = new FormData();
    fd.append("body", body);
    fetch("/field/admin/reply/" + encodeURIComponent(convState.activeThreadId), {
      method: "POST",
      headers: (function () { var h = {}; var m = document.querySelector('meta[name="csrf-token"]'); if (m) h["X-CSRFToken"] = m.getAttribute("content"); return h; })(),
      body: fd,
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        sendBtn.disabled = false;
        if (data && data.ok) {
          input.value = "";
          loadThreadMessages();
        } else {
          _toast("error", "Echec envoi : " + ((data && data.error) || "?"));
        }
      })
      .catch(function () {
        sendBtn.disabled = false;
        _toast("error", "Erreur reseau");
      });
  }

  function markThreadRead(rootId) {
    apiPost("/field/admin/thread/" + encodeURIComponent(rootId) + "/mark-read", {})
      .catch(function () { /* silent */ });
  }

  // --- Nouveau fil ----------------------------------------------------
  function submitNewThread() {
    if (!convState.device) return;
    var titleEl = $("#field-conv-new-title");
    var bodyEl = $("#field-conv-new-body");
    var typeEl = $("#field-conv-new-type");
    var prioEl = $("#field-conv-new-priority");
    var submitBtn = $("#field-conv-new-submit");
    var title = (titleEl ? titleEl.value : "").trim();
    var body = (bodyEl ? bodyEl.value : "").trim();
    if (!title) { _toast("error", "Donne un sujet au fil"); if (titleEl) titleEl.focus(); return; }
    if (!body) { _toast("error", "Ecris un message"); if (bodyEl) bodyEl.focus(); return; }
    submitBtn.disabled = true;
    var dev = convState.device;
    var payload = {
      event: dev.event,
      year: String(dev.year || ""),
      target: { device_ids: [dev.id] },
      type: typeEl ? typeEl.value : "info",
      title: title,
      body: body,
      priority: prioEl && prioEl.checked ? "high" : "normal",
    };
    apiPost("/field/admin/send", payload)
      .then(function (res) {
        submitBtn.disabled = false;
        if (res.body && res.body.ok) {
          _toast("success", "Fil cree");
          switchConvView("list");
          loadThreads();
        } else {
          _toast("error", "Echec : " + ((res.body && res.body.error) || "?"));
        }
      })
      .catch(function () {
        submitBtn.disabled = false;
        _toast("error", "Erreur reseau");
      });
  }

  // Charge le nombre de messages non lus par tablette (pour badge dans la table)
  function loadUnreadByDevice() {
    apiGet("/field/admin/unread-by-device")
      .then(function (data) {
        if (!data || !data.ok) return;
        state.unreadByDevice = data.unread || {};
        // Rafraichir juste les badges sans recreer toute la table
        updateUnreadBadges();
      })
      .catch(function () { /* silent */ });
  }

  function updateUnreadBadges() {
    var rows = document.querySelectorAll("#field-devices-tbody tr[data-device-id]");
    Array.prototype.forEach.call(rows, function (tr) {
      var did = tr.getAttribute("data-device-id");
      var n = (state.unreadByDevice || {})[did] || 0;
      var badge = tr.querySelector(".field-unread-badge");
      if (n > 0) {
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "field-unread-badge";
          badge.style.cssText = "display:inline-block; margin-left:6px; min-width:18px; height:18px; "
            + "padding:0 5px; background:#dc2626; color:#fff; font-size:10px; font-weight:800; "
            + "border-radius:9px; line-height:18px; text-align:center; vertical-align:middle;";
          var nameCell = tr.querySelector("td:first-child");
          if (nameCell) nameCell.appendChild(badge);
        }
        badge.textContent = n > 9 ? "9+" : String(n);
      } else if (badge) {
        badge.remove();
      }
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

    // Preselection eventuelle (appel depuis la carte operateur / clic droit
    // / bouton "envoyer message" dans la table devices). Si un scope event/year
    // est passe, on l'utilise comme override au submit pour que ca marche
    // meme en mode "parc complet" ou sans scope sidebar selectionne.
    state.msgScopeOverride = null;
    if (prefill && prefill.device_id) {
      var modeSel = $("#field-msg-target-mode");
      if (modeSel) modeSel.value = "devices";
      if (sel) {
        Array.from(sel.options).forEach(function (o) {
          o.selected = (o.value === prefill.device_id);
        });
      }
      if (prefill.event && prefill.year) {
        state.msgScopeOverride = { event: prefill.event, year: String(prefill.year) };
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
    // Priorite a l'override passe au moment de l'ouverture du modal (envoi
    // cible depuis la table devices) ; fallback sur le scope sidebar.
    var scope = state.msgScopeOverride || currentScope();
    if (!scope.event || !scope.year) {
      _toast("error", "Selectionne un evenement dans le header cockpit ou utilise le bouton 'Envoyer message' directement sur une tablette");
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
