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
    // no-store : les listes (messages, non lus, tablettes) sont relues en
    // polling, une reponse servie depuis un cache figerait l'affichage.
    return fetch(url, { cache: "no-store", credentials: "same-origin" })
      .then(function (r) { return r.json(); });
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

  // Categories de fiche d'une tablette (miroir de FIELD_CATEGORIES, field.py).
  var FIELD_CATEGORIES = [
    { id: "PCO.Secours", label: "Secours" },
    { id: "PCO.Securite", label: "Securite" },
    { id: "PCO.Technique", label: "Technique" },
    { id: "PCO.Flux", label: "Flux" },
    { id: "PCO.Fourriere", label: "Fourriere" },
    { id: "PCO.Information", label: "Information" },
    { id: "PCO.MainCourante", label: "Main courante" },
  ];

  function categoryLabel(id) {
    var c = FIELD_CATEGORIES.find(function (x) { return x.id === id; });
    return c ? c.label : (id || "aucune");
  }

  function fillCategorySelect(sel, emptyLabel, value) {
    while (sel.firstChild) sel.removeChild(sel.firstChild);
    var opt0 = document.createElement("option");
    opt0.value = "";
    opt0.textContent = emptyLabel;
    sel.appendChild(opt0);
    FIELD_CATEGORIES.forEach(function (c) {
      var o = document.createElement("option");
      o.value = c.id;
      o.textContent = c.label;
      sel.appendChild(o);
    });
    sel.value = value || "";
  }

  // ------------------------------------------------------------------
  // Metiers (sous-classifications d'une categorie, dispatch automatique)
  // ------------------------------------------------------------------
  var metiersCache = {};  // category -> Promise<[labels]>

  function fetchMetiers(category) {
    if (!category) return Promise.resolve([]);
    if (!metiersCache[category]) {
      metiersCache[category] = apiGet("/api/dispatch/metiers?category=" + encodeURIComponent(category))
        .then(function (data) { return (data && data.ok && data.metiers) || []; })
        .catch(function () { delete metiersCache[category]; return []; });
    }
    return metiersCache[category];
  }

  function normMetier(s) {
    return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").trim().toLowerCase();
  }

  // Remplit `box` de cases a cocher pour les metiers de `category`.
  // `selected` : labels deja coches (compares sans accents ni casse). Les
  // metiers selectionnes absents du referentiel restent affiches (coches).
  function renderMetiersCheckboxes(box, category, selected) {
    if (!box) return Promise.resolve();
    var token = (box._metiersSeq = (box._metiersSeq || 0) + 1);
    while (box.firstChild) box.removeChild(box.firstChild);
    var info = document.createElement("span");
    info.style.cssText = "color:var(--muted); font-size:12px;";
    if (!category) {
      info.textContent = "Choisir un groupe ou une categorie.";
      box.appendChild(info);
      return Promise.resolve();
    }
    info.textContent = "Chargement...";
    box.appendChild(info);
    return fetchMetiers(category).then(function (list) {
      if (box._metiersSeq !== token) return;
      while (box.firstChild) box.removeChild(box.firstChild);
      var sel = (selected || []).slice();
      var selNorm = sel.map(normMetier);
      var all = list.slice();
      sel.forEach(function (m) {
        if (!all.some(function (x) { return normMetier(x) === normMetier(m); })) all.push(m);
      });
      if (!all.length) {
        var empty = document.createElement("span");
        empty.style.cssText = "color:var(--muted); font-size:12px;";
        empty.textContent = "Aucun metier defini pour " + categoryLabel(category) + " (tous les metiers).";
        box.appendChild(empty);
        return;
      }
      all.forEach(function (m) {
        var lab = document.createElement("label");
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = m;
        cb.setAttribute("data-metier", "1");
        cb.checked = selNorm.indexOf(normMetier(m)) >= 0;
        lab.appendChild(cb);
        lab.appendChild(document.createTextNode(m));
        box.appendChild(lab);
      });
    });
  }

  function checkedMetiers(box) {
    if (!box) return [];
    return $$('input[data-metier]', box)
      .filter(function (cb) { return cb.checked; })
      .map(function (cb) { return cb.value; });
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
      loadStreamSlots();
    });

    // Polling de l'etat des slots streaming (3 slots fixes du pool video)
    loadStreamSlots();
    setInterval(loadStreamSlots, 5000);

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

    // Poll leger des compteurs non-lus (10 s), suspendu quand l'onglet est
    // cache. init() ne tourne qu'une fois : pas de timer en double.
    setInterval(function () {
      if (!document.hidden) loadUnreadByDevice();
    }, UNREAD_POLL_MS);
    // Retour sur l'onglet : Chrome a ralenti les minuteries, on relit tout
    // de suite (compteurs + conversation ouverte).
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) return;
      loadUnreadByDevice();
      pollConversation();
    });

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
  // Pool streaming live (3 slots fixes)
  // ------------------------------------------------------------------
  function loadStreamSlots() {
    var container = document.getElementById("field-stream-slots");
    if (!container) return;
    apiGet("/field/admin/streams/active")
      .then(function (data) {
        if (!data || !data.ok || !data.slots) {
          container.textContent = "";
          return;
        }
        renderStreamSlots(container, data.slots);
      })
      .catch(function () { /* silent */ });
  }

  function renderStreamSlots(container, slots) {
    container.textContent = "";
    slots.forEach(function (slot) {
      var card = document.createElement("div");
      card.className = "field-stream-slot" + (slot.free ? " is-free" : " is-busy");

      var head = document.createElement("div");
      head.className = "field-stream-slot-head";
      var ic = document.createElement("span");
      ic.className = "material-symbols-outlined";
      ic.textContent = slot.free ? "videocam_off" : "videocam";
      ic.style.cssText = "vertical-align:middle; font-size:18px; color:" + (slot.free ? "#94a3b8" : "#dc2626") + ";";
      head.appendChild(ic);
      var title = document.createElement("strong");
      title.textContent = " " + (slot.slot_label || ("Slot " + slot.slot_index));
      head.appendChild(title);
      card.appendChild(head);

      var info = document.createElement("div");
      info.className = "field-stream-slot-info";
      if (slot.free) {
        info.textContent = "Libre";
        info.style.color = "var(--muted)";
      } else {
        var s = slot.stream || {};
        var line1 = document.createElement("div");
        line1.style.cssText = "font-weight:600;";
        line1.textContent = s.device_name || "?";
        info.appendChild(line1);
        var line2 = document.createElement("div");
        line2.style.cssText = "font-size:11px;color:var(--muted);";
        var status = s.status === "requested" ? "En attente acceptation" : "Diffusion en cours";
        var dur = "";
        if (s.accepted_at) {
          try {
            var elapsed = Math.max(0, Math.floor((Date.now() - new Date(s.accepted_at).getTime()) / 1000));
            var mm = Math.floor(elapsed / 60);
            var ss = elapsed % 60;
            dur = " - " + mm + ":" + (ss < 10 ? "0" + ss : ss);
          } catch (e) {}
        }
        line2.textContent = status + dur;
        info.appendChild(line2);
      }
      card.appendChild(info);

      var actions = document.createElement("div");
      actions.className = "field-stream-slot-actions";
      if (!slot.free && slot.stream) {
        var viewBtn = document.createElement("button");
        viewBtn.className = "btn btn-xs btn-primary";
        viewBtn.style.cssText = "padding:4px 8px;font-size:11px;";
        viewBtn.textContent = "Voir";
        viewBtn.addEventListener("click", function () {
          if (typeof window.openFieldStreamViewer === "function") {
            window.openFieldStreamViewer(slot.stream.stream_id, slot.stream.device_name);
          }
        });
        actions.appendChild(viewBtn);

        var killBtn = document.createElement("button");
        killBtn.className = "btn btn-xs";
        killBtn.style.cssText = "padding:4px 8px;font-size:11px;background:#dc2626;color:#fff;border:none;";
        killBtn.textContent = "Couper";
        killBtn.addEventListener("click", function () {
          if (!window.confirm("Couper ce flux ?")) return;
          fetch("/field/admin/stream/" + encodeURIComponent(slot.stream.stream_id) + "/end", {
            method: "POST",
            headers: jsonHeaders(),
          }).then(loadStreamSlots);
        });
        actions.appendChild(killBtn);
      }
      card.appendChild(actions);

      container.appendChild(card);
    });
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
    var colspan = hasEventCol ? 8 : 7;
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

      // Categorie : modifiable sur place. "" = celle du groupe (affichee).
      var tdCat = document.createElement("td");
      var catSel = document.createElement("select");
      catSel.className = "form-input";
      catSel.style.cssText = "font-size:12px; padding:2px 4px; height:26px; width:100%;";
      var inherited = d.category ? null : d.category_effective;
      fillCategorySelect(catSel, "Groupe (" + categoryLabel(inherited) + ")", d.category);
      catSel.title = "Categorie effective : " + categoryLabel(d.category_effective);
      catSel.addEventListener("change", function () {
        catSel.disabled = true;
        apiPost("/field/admin/devices/" + encodeURIComponent(d.id) + "/category",
                { category: catSel.value })
          .then(function (res) {
            if (res.body && res.body.ok) {
              _toast("success", d.name + " : categorie " + categoryLabel(res.body.category_effective));
              loadDevices();
            } else {
              _toast("error", "Erreur : " + ((res.body && res.body.error) || "?"));
              catSel.value = d.category || "";
              catSel.disabled = false;
            }
          })
          .catch(function () {
            _toast("error", "Erreur reseau");
            catSel.value = d.category || "";
            catSel.disabled = false;
          });
      });
      tdCat.appendChild(catSel);

      // Metiers couverts (dispatch automatique) : vide = tous.
      var metLine = document.createElement("div");
      metLine.className = "field-dev-metiers";
      var metTxt = document.createElement("span");
      metTxt.className = "txt";
      var devMetiers = Array.isArray(d.metiers) ? d.metiers : [];
      metTxt.textContent = devMetiers.length ? devMetiers.join(", ") : "tous metiers";
      metTxt.title = "Metiers : " + (devMetiers.length ? devMetiers.join(", ") : "tous");
      metLine.appendChild(metTxt);
      var metBtn = document.createElement("button");
      metBtn.type = "button";
      metBtn.title = "Modifier les metiers";
      var metIc = document.createElement("span");
      metIc.className = "material-symbols-outlined";
      metIc.style.fontSize = "14px";
      metIc.textContent = "edit";
      metBtn.appendChild(metIc);
      metBtn.addEventListener("click", function () { openMetiersModal(d); });
      metLine.appendChild(metBtn);
      tdCat.appendChild(metLine);
      tr.appendChild(tdCat);

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
        actWrap.appendChild(mkActionBtn(
          "videocam",
          "Demander un flux video live",
          null,
          function () {
            if (typeof window.requestFieldStreamForDevice === "function") {
              window.requestFieldStreamForDevice(d._id, d.name);
            } else {
              _toast("error", "Module flux video non charge");
            }
          }
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
  // Modal metiers d'une tablette
  // ------------------------------------------------------------------
  var metiersModalDevice = null;

  function wireMetiersModalOnce(modal) {
    if (modal._wired) return;
    modal._wired = true;
    $$("[data-close]", modal).forEach(function (b) {
      b.addEventListener("click", closeMetiersModal);
    });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) closeMetiersModal();
    });
    var submit = $("#field-metiers-submit");
    if (submit) submit.addEventListener("click", submitMetiers);
  }

  function openMetiersModal(d) {
    var modal = $("#field-metiers-modal");
    if (!modal) return;
    wireMetiersModalOnce(modal);
    metiersModalDevice = d;
    var title = $("#field-metiers-title");
    if (title) title.textContent = "Metiers - " + (d.name || "?");
    var catEl = $("#field-metiers-cat");
    if (catEl) {
      catEl.textContent = d.category_effective
        ? "Categorie : " + categoryLabel(d.category_effective)
        : "Aucune categorie : choisir d'abord la categorie de la tablette.";
    }
    var submit = $("#field-metiers-submit");
    if (submit) submit.disabled = !d.category_effective;
    renderMetiersCheckboxes($("#field-metiers-list"), d.category_effective || "",
                            Array.isArray(d.metiers) ? d.metiers : []);
    modal.hidden = false;
  }

  function closeMetiersModal() {
    var modal = $("#field-metiers-modal");
    if (modal) modal.hidden = true;
    metiersModalDevice = null;
  }

  function submitMetiers() {
    var d = metiersModalDevice;
    if (!d) return;
    var submit = $("#field-metiers-submit");
    if (submit) submit.disabled = true;
    var metiers = checkedMetiers($("#field-metiers-list"));
    apiPost("/field/admin/devices/" + encodeURIComponent(d.id) + "/metiers", { metiers: metiers })
      .then(function (res) {
        if (res.body && res.body.ok) {
          var m = res.body.metiers || [];
          _toast("success", (d.name || "Tablette") + " : " + (m.length ? m.join(", ") : "tous metiers"));
          closeMetiersModal();
          loadDevices();
        } else {
          _toast("error", "Erreur : " + ((res.body && res.body.error) || "?"));
        }
      })
      .catch(function () { _toast("error", "Erreur reseau"); })
      .then(function () { if (submit) submit.disabled = false; });
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

  // ------------------------------------------------------------------
  // Evenement d'appairage : par defaut l'evenement courant
  // (/api/event/current : epreuve active, sinon SAISON), modifiable (autres
  // evenements actifs, selection du header). Une tablette SAISON voit aussi
  // les fiches des epreuves actives (field.device_pairs).
  // ------------------------------------------------------------------
  function loadCurrentEvent() {
    return fetch("/api/event/current", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { state.eventCurrent = d || null; return d; })
      .catch(function () { state.eventCurrent = null; return null; });
  }

  function renderPairEventChoice() {
    var row = $("#field-pair-event-row");
    if (!row) return;
    var opts = [];
    function add(ev, yr, suffix) {
      if (!ev || !yr) return;
      var key = ev + "|" + yr;
      if (opts.some(function (o) { return o.key === key; })) return;
      opts.push({ key: key, event: ev, year: String(yr), label: ev + " / " + yr + suffix });
    }
    var cur = state.eventCurrent || {};
    if (cur.current) add(cur.current.event, cur.current.year, " (evenement courant)");
    (cur.active || []).forEach(function (a) { add(a.event, a.year, " (actif)"); });
    var s = currentScope();
    add(s.event, s.year, " (selection du header)");

    var prev = $("#field-pair-event");
    var keepKey = (prev && state.pairEventTouched) ? prev.value : "";
    row.textContent = "";
    var lab = document.createElement("label");
    lab.setAttribute("for", "field-pair-event");
    lab.textContent = "Evenement";
    row.appendChild(lab);
    if (!opts.length) {
      var none = document.createElement("p");
      none.style.cssText = "font-size:12px; color:var(--muted); margin:0;";
      none.textContent = "(aucun evenement)";
      row.appendChild(none);
      return;
    }
    var sel = document.createElement("select");
    sel.id = "field-pair-event";
    sel.className = "form-input";
    opts.forEach(function (o) {
      var op = document.createElement("option");
      op.value = o.key;
      op.textContent = o.label;
      op.setAttribute("data-event", o.event);
      op.setAttribute("data-year", o.year);
      sel.appendChild(op);
    });
    if (keepKey && opts.some(function (o) { return o.key === keepKey; })) sel.value = keepKey;
    sel.addEventListener("change", function () { state.pairEventTouched = true; });
    row.appendChild(sel);
  }

  function pairScope() {
    var sel = $("#field-pair-event");
    var op = sel && sel.options[sel.selectedIndex];
    if (op) return { event: op.getAttribute("data-event") || "", year: op.getAttribute("data-year") || "" };
    return currentScope();
  }

  function openPairModal() {
    refreshScopeUi();
    loadBeaconGroups();
    state.pairEventTouched = false;
    renderPairEventChoice();
    loadCurrentEvent().then(renderPairEventChoice);
    var form = $("#field-pair-form");
    if (form) form.reset();
    var catSel = form && $('select[name="category"]', form);
    var grpSel = form && $('select[name="beacon_group_id"]', form);
    if (catSel) {
      fillCategorySelect(catSel, "Celle du groupe", "");
      if (grpSel && !grpSel._catWired) {
        // Le choix d'un groupe propose sa categorie ; l'admin peut la changer.
        grpSel.addEventListener("change", function () {
          var g = state.beaconGroups.find(function (x) { return x.id === grpSel.value; });
          catSel.value = (g && g.pco_category) || "";
          refreshPairMetiers();
        });
        catSel.addEventListener("change", refreshPairMetiers);
        grpSel._catWired = true;
      }
    }
    refreshPairMetiers();
    var result = $("#field-pair-result");
    if (result) result.textContent = "";
    stopPairPoll();
    var modal = $("#field-pair-modal");
    if (modal) modal.hidden = false;
  }

  // Categorie effective du formulaire de pairing : celle choisie, sinon celle
  // du groupe. Les metiers coches sont conserves si elle ne change pas.
  function refreshPairMetiers() {
    var form = $("#field-pair-form");
    var box = $("#field-pair-metiers");
    if (!form || !box) return;
    var catSel = $('select[name="category"]', form);
    var grpSel = $('select[name="beacon_group_id"]', form);
    var cat = (catSel && catSel.value) || "";
    if (!cat && grpSel && grpSel.value) {
      var g = state.beaconGroups.find(function (x) { return x.id === grpSel.value; });
      cat = (g && g.pco_category) || "";
    }
    var keep = box._metiersCat === cat ? checkedMetiers(box) : [];
    box._metiersCat = cat;
    renderMetiersCheckboxes(box, cat, keep);
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
    var scope = pairScope();
    if (!scope.event || !scope.year) {
      _toast("error", "Choisir l'evenement de la tablette");
      return;
    }
    var payload = {
      name: (fd.get("name") || "").toString().trim(),
      beacon_group_id: (fd.get("beacon_group_id") || "").toString(),
      category: (fd.get("category") || "").toString(),
      notes: (fd.get("notes") || "").toString().trim(),
      metiers: checkedMetiers($("#field-pair-metiers")),
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
            invalid_category: "Categorie inconnue.",
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
        photo_report: "Photo", scan_report: "Scan", sos_broadcast: "SOS"
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
      } else if (m.type === "scan_report") {
        badge.style.background = "#e0e7ff";
        badge.style.color = "#3730a3";
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
      // Miniature scan_report : icone QR + nb codes
      var msgCodes = (m.payload && Array.isArray(m.payload.codes)) ? m.payload.codes : null;
      if (msgCodes && msgCodes.length) {
        var scanBadge = document.createElement("span");
        scanBadge.style.cssText = "display:inline-flex; align-items:center; gap:4px; background:#e0e7ff; color:#3730a3; padding:2px 6px; border-radius:4px; margin-right:8px; font-size:11px; font-weight:700;";
        var scanIcon = document.createElement("span");
        scanIcon.className = "material-symbols-outlined";
        scanIcon.textContent = "qr_code_scanner";
        scanIcon.style.cssText = "font-size:14px;";
        scanBadge.appendChild(scanIcon);
        scanBadge.appendChild(document.createTextNode("x" + msgCodes.length));
        tdTitle.appendChild(scanBadge);
      }
      var titleText = document.createElement("span");
      var fallbackTxt = msgCodes && msgCodes.length
        ? msgCodes.map(function (c) { return c.value || ""; }).join(", ")
        : (m.body || "(photo)");
      titleText.textContent = m.title || (isInbound ? fallbackTxt : "(sans titre)");
      titleText.style.cssText = "overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:middle;";
      tdTitle.appendChild(titleText);
      tdTitle.title = msgCodes && msgCodes.length
        ? msgCodes.map(function (c) { return (c.format || "manual").toUpperCase() + " " + (c.value || ""); }).join("\n")
        : (m.body || "");
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
  var CONV_POLL_MS = 4000;
  var UNREAD_POLL_MS = 10000;
  var convState = {
    device: null, view: "list", activeThreadId: null, pollTimer: null,
    renderedThreadId: null, seenMsgIds: {}, threadsSig: null,
  };

  function openConversationModal(device) {
    convState.device = device;
    convState.view = "list";
    convState.activeThreadId = null;
    convState.renderedThreadId = null;
    convState.threadsSig = null;
    var modal = $("#field-conv-modal");
    if (!modal) return;
    wireConversationModalOnce(modal);
    updateConvTitle();
    switchConvView("list");
    loadThreads();
    modal.hidden = false;
    // Un seul timer, meme si la modale est rouverte sans avoir ete fermee.
    if (convState.pollTimer) clearInterval(convState.pollTimer);
    convState.pollTimer = setInterval(pollConversation, CONV_POLL_MS);
  }

  // Relit la vue affichee (liste des fils ou fil ouvert). Rien si la modale
  // est fermee ou l'onglet cache.
  function pollConversation() {
    if (document.hidden) return;
    var modal = $("#field-conv-modal");
    if (!modal || modal.hidden || !convState.device) return;
    if (convState.view === "list") loadThreads(true);
    else if (convState.view === "thread") loadThreadMessages(true);
  }

  function closeConversationModal() {
    var modal = $("#field-conv-modal");
    if (modal) modal.hidden = true;
    if (convState.pollTimer) { clearInterval(convState.pollTimer); convState.pollTimer = null; }
    convState.device = null;
    convState.activeThreadId = null;
    convState.renderedThreadId = null;
    convState.threadsSig = null;
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
      convState.threadsSig = null;
      while (box.firstChild) box.removeChild(box.firstChild);
      var ph = document.createElement("div");
      ph.style.cssText = "text-align:center; color:var(--muted); padding:20px;";
      ph.textContent = "Chargement...";
      box.appendChild(ph);
    }
    var deviceId = convState.device.id;
    apiGet("/field/admin/threads/" + encodeURIComponent(deviceId))
      .then(function (data) {
        if (!data || !data.ok) return;
        // Reponse arrivee apres un changement de tablette ou de vue : ignoree.
        if (!convState.device || convState.device.id !== deviceId) return;
        if (convState.view !== "list") return;
        var threads = data.threads || [];
        // Rien de neuf : on ne redessine pas (pas de clignotement des
        // vignettes ni de saut de defilement toutes les 4 s).
        var sig = JSON.stringify(threads);
        if (silent && sig === convState.threadsSig) return;
        convState.threadsSig = sig;
        var scroll = box.scrollTop;
        renderThreadsList(threads);
        if (silent) box.scrollTop = scroll;
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

  // silent : polling, on n'ajoute que les messages nouveaux (pas de
  // "Chargement...", defilement conserve sauf si l'operateur etait en bas).
  // forceScroll : apres un envoi, on descend toujours au dernier message.
  function loadThreadMessages(silent, forceScroll) {
    var threadId = convState.activeThreadId;
    if (!threadId) return;
    var list = $("#field-conv-thread-list");
    if (!list) return;
    if (!silent) {
      convState.renderedThreadId = null;
      while (list.firstChild) list.removeChild(list.firstChild);
      var ph = document.createElement("div");
      ph.style.cssText = "text-align:center; color:var(--muted); padding:20px;";
      ph.textContent = "Chargement...";
      list.appendChild(ph);
    }
    apiGet("/field/admin/thread/" + encodeURIComponent(threadId))
      .then(function (data) {
        if (!data || !data.ok) return;
        // L'operateur a change de fil pendant la requete : reponse perimee.
        if (convState.activeThreadId !== threadId || convState.view !== "thread") return;
        if (convState.renderedThreadId === threadId) {
          appendNewThreadMessages(data.messages || [], !!forceScroll);
        } else {
          renderThreadMessages(data.messages || []);
        }
      })
      .catch(function () { /* silent */ });
  }

  function appendNewThreadMessages(messages, forceScroll) {
    var list = $("#field-conv-thread-list");
    if (!list) return;
    var body = $("#field-conv-thread-body");
    var atBottom = !body || (body.scrollHeight - body.scrollTop - body.clientHeight < 60);
    var added = 0;
    var inbound = false;
    messages.forEach(function (m) {
      if (!m || !m.id || convState.seenMsgIds[m.id]) return;
      convState.seenMsgIds[m.id] = true;
      if (!added) {
        var empty = list.querySelector("[data-conv-empty]");
        if (empty) empty.remove();
      }
      list.appendChild(buildThreadBubble(m));
      added++;
      if (m.direction === "field_to_cockpit") inbound = true;
    });
    if (!added) return;
    if (body && (atBottom || forceScroll)) body.scrollTop = body.scrollHeight;
    // Le fil est sous les yeux de l'operateur : le message est lu.
    if (inbound) markThreadRead(convState.activeThreadId);
  }

  function renderThreadMessages(messages) {
    var list = $("#field-conv-thread-list");
    if (!list) return;
    while (list.firstChild) list.removeChild(list.firstChild);
    convState.renderedThreadId = convState.activeThreadId;
    convState.seenMsgIds = {};
    messages.forEach(function (m) { if (m && m.id) convState.seenMsgIds[m.id] = true; });
    if (messages.length === 0) {
      var empty = document.createElement("div");
      empty.setAttribute("data-conv-empty", "1");
      empty.style.cssText = "text-align:center; color:var(--muted); padding:30px 20px;";
      empty.textContent = "Fil vide.";
      list.appendChild(empty);
      return;
    }
    messages.forEach(function (m) {
      list.appendChild(buildThreadBubble(m));
    });
    var body = $("#field-conv-thread-body");
    if (body) body.scrollTop = body.scrollHeight;
  }

  function buildThreadBubble(m) {
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
      var bubbleCodes = (m.payload && Array.isArray(m.payload.codes)) ? m.payload.codes : null;
      if (bubbleCodes && bubbleCodes.length) {
        var codesWrap = document.createElement("div");
        codesWrap.style.cssText = "display:flex; flex-direction:column; gap:3px; margin:4px 0;";
        bubbleCodes.forEach(function (c) {
          var chipEl = document.createElement("div");
          chipEl.style.cssText = "display:flex; gap:6px; align-items:center; background:rgba(0,0,0,0.06); border-radius:4px; padding:3px 6px; font-size:11px; font-family:ui-monospace, Menlo, monospace;";
          var fmtSpan = document.createElement("span");
          fmtSpan.textContent = (c.format || "manual").toUpperCase();
          fmtSpan.style.cssText = "background:#3730a3; color:#fff; padding:1px 4px; border-radius:3px; font-size:10px; font-weight:800; flex-shrink:0; font-family:inherit;";
          var valSpan = document.createElement("span");
          valSpan.textContent = c.value || "";
          valSpan.style.cssText = "overflow-wrap:anywhere;";
          chipEl.appendChild(fmtSpan);
          chipEl.appendChild(valSpan);
          codesWrap.appendChild(chipEl);
        });
        bubble.appendChild(codesWrap);
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
      return bubble;
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
          loadThreadMessages(true, true);
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
      .then(function () { loadUnreadByDevice(); })
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
  var unreadPrimed = false;       // 1re lecture : reference, pas de toast
  var lastUnreadToastAt = 0;
  function loadUnreadByDevice() {
    apiGet("/field/admin/unread-by-device")
      .then(function (data) {
        if (!data || !data.ok) return;
        var next = data.unread || {};
        if (unreadPrimed) notifyNewInbound(state.unreadByDevice || {}, next);
        unreadPrimed = true;
        state.unreadByDevice = next;
        // Rafraichir juste les badges sans recreer toute la table
        updateUnreadBadges();
      })
      .catch(function () { /* silent */ });
  }

  // Toast discret quand un compteur augmente. Pas pour la tablette dont la
  // conversation est ouverte (le message s'y affiche deja), et au plus un
  // toast toutes les 8 s (regroupe les tablettes).
  function notifyNewInbound(prev, next) {
    var modal = $("#field-conv-modal");
    var openId = (modal && !modal.hidden && convState.device) ? convState.device.id : null;
    var names = [];
    Object.keys(next).forEach(function (did) {
      if ((next[did] || 0) <= (prev[did] || 0)) return;
      if (did === openId) return;
      var d = (state.devices || []).find(function (x) { return x.id === did; });
      names.push(d ? d.name : "une tablette");
    });
    if (!names.length) return;
    var now = Date.now();
    if (now - lastUnreadToastAt < 8000) return;
    lastUnreadToastAt = now;
    _toast("info", names.length === 1
      ? "Nouveau message de " + names[0]
      : "Nouveaux messages : " + names.join(", "));
  }

  function updateUnreadBadges() {
    // Total global (en-tete "Tablettes enrolees")
    var totalEl = $("#field-unread-total");
    if (totalEl) {
      var total = 0;
      Object.keys(state.unreadByDevice || {}).forEach(function (k) {
        total += (state.unreadByDevice[k] || 0);
      });
      totalEl.hidden = total === 0;
      totalEl.textContent = total > 99 ? "99+" : String(total);
      totalEl.title = total + " message(s) non lu(s)";
    }
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
