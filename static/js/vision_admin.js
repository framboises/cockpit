// vision_admin.js - Logique JS pour la section "Tablettes Vision" de la page
// Field dispatch. Totalement autonome de field_admin.js : ses propres helpers
// HTTP, son propre state, ses propres modales et callbacks.

(function () {
  "use strict";

  // ------------------------------------------------------------------
  // Helpers HTTP + utilitaires
  // ------------------------------------------------------------------
  function $(sel, root) { return (root || document).querySelector(sel); }

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function apiGet(url) {
    return fetch(url).then(function (r) { return r.json(); });
  }
  function apiPost(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(data || {}),
    }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
    });
  }
  function apiDelete(url) {
    return fetch(url, {
      method: "DELETE",
      headers: { "X-CSRFToken": csrfToken() },
    }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
    });
  }

  function _toast(type, msg) {
    if (typeof showToast === "function") return showToast(msg, type);
    if (type === "error") console.error("[vision]", msg);
    else console.log("[vision]", msg);
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

  function formatExp(iso) {
    if (!iso) return "-";
    try {
      var d = new Date(iso);
      var diff = (d.getTime() - Date.now()) / 1000;
      if (diff < 0) return "expire";
      if (diff < 60) return "dans " + Math.round(diff) + "s";
      if (diff < 3600) return "dans " + Math.round(diff / 60) + " min";
      return d.toLocaleString();
    } catch (e) { return iso; }
  }

  // ------------------------------------------------------------------
  // Scope event/year (lit la meme source que field_admin.js : selects sidebar)
  // ------------------------------------------------------------------
  function currentScope() {
    var ev = $("#event-select");
    var yr = $("#year-select");
    return {
      event: ev && ev.value ? ev.value : "",
      year: yr && yr.value ? yr.value : "",
    };
  }

  // ------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------
  var state = {
    devices: [],
    pairings: [],
  };

  // ------------------------------------------------------------------
  // Devices Vision
  // ------------------------------------------------------------------
  function loadDevices() {
    var s = currentScope();
    var qs = new URLSearchParams();
    if (s.event) qs.set("event", s.event);
    if (s.year) qs.set("year", s.year);
    apiGet("/field/admin/vision/devices?" + qs.toString())
      .then(function (data) {
        state.devices = (data && data.devices) || [];
        renderDevices();
      })
      .catch(function () {});
  }

  function renderDevices() {
    var tb = $("#vision-devices-tbody");
    if (!tb) return;
    var countEl = $("#vision-admin-count");
    if (countEl) {
      countEl.textContent = state.devices.length
        ? (state.devices.length + " tablette" + (state.devices.length > 1 ? "s" : ""))
        : "";
    }
    while (tb.firstChild) tb.removeChild(tb.firstChild);
    if (state.devices.length === 0) {
      var emptyTr = document.createElement("tr");
      var emptyTd = document.createElement("td");
      emptyTd.colSpan = 7;
      emptyTd.style.cssText = "text-align:center; color:var(--muted); padding:16px;";
      emptyTd.textContent = "Aucune tablette Vision enrolee.";
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
      tr.setAttribute("data-vision-device-id", d.id);

      var tdName = document.createElement("td");
      var icon = document.createElement("span");
      icon.className = "material-symbols-outlined";
      icon.style.cssText = "font-size:14px; vertical-align:middle; color:#003b5c; margin-right:3px;";
      icon.textContent = "qr_code_scanner";
      tdName.appendChild(icon);
      var nameSpan = document.createElement("span");
      nameSpan.textContent = d.name || "-";
      nameSpan.style.fontWeight = "600";
      tdName.appendChild(nameSpan);
      tr.appendChild(tdName);

      var tdEvent = document.createElement("td");
      tdEvent.style.cssText = "white-space:nowrap; color:var(--muted); font-size:12px;";
      tdEvent.textContent = (d.event || "-") + (d.year ? " / " + d.year : "");
      tr.appendChild(tdEvent);

      var tdLieu = document.createElement("td");
      var lieuBadge = document.createElement("span");
      lieuBadge.textContent = d.lieu || "?";
      lieuBadge.style.cssText = "background:#003b5c; color:#fff; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;";
      tdLieu.appendChild(lieuBadge);
      tr.appendChild(tdLieu);

      var tdPos = document.createElement("td");
      tdPos.style.cssText = "font-size:11px; color:var(--muted);";
      if (d.last_lat != null && d.last_lng != null) {
        var posLink = document.createElement("a");
        posLink.href = "https://www.google.com/maps?q=" + d.last_lat.toFixed(6) + "," + d.last_lng.toFixed(6);
        posLink.target = "_blank";
        posLink.rel = "noopener";
        posLink.style.cssText = "color:inherit; text-decoration:none;";
        var acc = d.last_accuracy != null ? Math.round(d.last_accuracy) + "m" : "";
        posLink.textContent = "GPS" + (acc ? " ±" + acc : "");
        if (d.last_position_ts) {
          var ageSpan = document.createElement("span");
          ageSpan.style.cssText = "display:block; font-size:10px; opacity:0.7;";
          ageSpan.textContent = formatRelative(d.last_position_ts);
          tdPos.appendChild(posLink);
          tdPos.appendChild(ageSpan);
        } else {
          tdPos.appendChild(posLink);
        }
      } else {
        tdPos.textContent = "-";
      }
      tr.appendChild(tdPos);

      var tdSeen = document.createElement("td");
      tdSeen.textContent = d.last_seen ? formatRelative(d.last_seen) : "-";
      tr.appendChild(tdSeen);

      var tdBat = document.createElement("td");
      tdBat.style.cssText = "text-align:center;";
      if (d.last_battery != null) {
        var pct = Math.round(d.last_battery * 100);
        var batColor = pct >= 50 ? "#28a745" : (pct >= 20 ? "#fd7e14" : "#dc3545");
        var batSpan = document.createElement("span");
        batSpan.style.cssText = "font-weight:600; color:" + batColor + "; font-size:12px;";
        batSpan.textContent = pct + "%";
        tdBat.appendChild(batSpan);
        if (d.last_charging) {
          var chgIcon = document.createElement("span");
          chgIcon.className = "material-symbols-outlined";
          chgIcon.style.cssText = "font-size:12px; vertical-align:middle; color:#28a745; margin-left:2px;";
          chgIcon.textContent = "bolt";
          tdBat.appendChild(chgIcon);
        }
      } else {
        tdBat.textContent = "-";
        tdBat.style.color = "var(--muted)";
      }
      tr.appendChild(tdBat);

      var tdAct = document.createElement("td");
      tdAct.style.cssText = "white-space:nowrap; text-align:right;";
      var actWrap = document.createElement("div");
      actWrap.style.cssText = "display:inline-flex; gap:4px; align-items:center;";
      tdAct.appendChild(actWrap);

      function mkBtn(icon, title, extraClass, onClick) {
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
        actWrap.appendChild(mkBtn("place", "Changer le lieu", null, function () { changeLieu(d); }));
        actWrap.appendChild(mkBtn("block", "Revoquer (la session JWT reste valide jusqu'a expiration)", null, function () { revokeDevice(d); }));
      }
      actWrap.appendChild(mkBtn("delete", "Supprimer definitivement", null, function () { deleteDevice(d); }));
      tr.appendChild(tdAct);

      if (d.revoked) {
        tr.style.opacity = "0.55";
        tr.style.background = "rgba(127, 29, 29, 0.12)";
        nameSpan.style.textDecoration = "line-through";
        var badge = document.createElement("span");
        badge.textContent = "REVOQUEE";
        badge.style.cssText = "margin-left:6px; font-size:9px; font-weight:800; background:#7f1d1d; color:#fee2e2; padding:1px 5px; border-radius:4px; vertical-align:middle;";
        tdName.appendChild(badge);
      }

      tb.appendChild(tr);
    });
  }

  function changeLieu(d) {
    var input = window.prompt(
      "Lieu pour " + (d.name || "?") + " : Ouest, Panorama ou Houx",
      d.lieu || ""
    );
    if (input === null) return;
    input = (input || "").trim();
    if (["Ouest", "Panorama", "Houx"].indexOf(input) < 0) {
      _toast("error", "Lieu invalide. Choix : Ouest, Panorama, Houx.");
      return;
    }
    apiPost("/field/admin/vision/devices/" + d.id + "/lieu", { lieu: input })
      .then(function (res) {
        if (res.body && res.body.ok) {
          _toast("success", "Lieu mis a jour : " + input);
          loadDevices();
        } else {
          _toast("error", "Erreur : " + ((res.body && res.body.error) || "inconnue"));
        }
      })
      .catch(function () { _toast("error", "Erreur reseau"); });
  }

  function revokeDevice(d) {
    if (!window.confirm("Revoquer la tablette Vision " + (d.name || "?") + " ?\n\nNote : la session JWT en cours sur la tablette reste valide jusqu'a son expiration (limitation JWT stateless).")) {
      return;
    }
    apiPost("/field/admin/vision/devices/" + d.id + "/revoke")
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
    if (!window.confirm("Supprimer definitivement la tablette Vision " + (d.name || "?") + " ?")) {
      return;
    }
    apiDelete("/field/admin/vision/devices/" + d.id)
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
  // Pairings Vision
  // ------------------------------------------------------------------
  function loadPairings() {
    var s = currentScope();
    var qs = new URLSearchParams();
    if (s.event) qs.set("event", s.event);
    if (s.year) qs.set("year", s.year);
    apiGet("/field/admin/vision/pairings?" + qs.toString())
      .then(function (data) {
        state.pairings = (data && data.pairings) || [];
        var countEl = $("#vision-pair-count");
        if (countEl) countEl.textContent = String(state.pairings.length);
        renderCodesTable();
      })
      .catch(function () {});
  }

  function renderCodesTable() {
    var tb = $("#vision-codes-tbody");
    if (!tb) return;
    while (tb.firstChild) tb.removeChild(tb.firstChild);
    if (state.pairings.length === 0) {
      var emptyTr = document.createElement("tr");
      var emptyTd = document.createElement("td");
      emptyTd.colSpan = 5;
      emptyTd.style.cssText = "text-align:center; color:var(--muted); padding:16px;";
      emptyTd.textContent = "Aucun code Vision en cours.";
      emptyTr.appendChild(emptyTd);
      tb.appendChild(emptyTr);
      return;
    }
    state.pairings.forEach(function (p) {
      var tr = document.createElement("tr");

      var tdCode = document.createElement("td");
      tdCode.style.cssText = "padding:6px; font-family:monospace; font-weight:700; font-size:14px; letter-spacing:2px;";
      tdCode.textContent = p.code;
      tr.appendChild(tdCode);

      var tdName = document.createElement("td");
      tdName.style.padding = "6px";
      tdName.textContent = p.name || "-";
      tr.appendChild(tdName);

      var tdLieu = document.createElement("td");
      tdLieu.style.padding = "6px";
      tdLieu.textContent = p.lieu || "-";
      tr.appendChild(tdLieu);

      var tdExp = document.createElement("td");
      tdExp.style.padding = "6px";
      tdExp.textContent = formatExp(p.expiresAt);
      tr.appendChild(tdExp);

      var tdAct = document.createElement("td");
      tdAct.style.padding = "6px";
      var btnDel = document.createElement("button");
      btnDel.className = "btn btn-xs";
      btnDel.title = "Annuler ce code";
      var ic = document.createElement("span");
      ic.className = "material-symbols-outlined";
      ic.style.fontSize = "14px";
      ic.textContent = "close";
      btnDel.appendChild(ic);
      btnDel.addEventListener("click", function () { deletePairing(p); });
      tdAct.appendChild(btnDel);
      tr.appendChild(tdAct);

      tb.appendChild(tr);
    });
  }

  function deletePairing(p) {
    apiDelete("/field/admin/vision/pairings/" + encodeURIComponent(p.code))
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
  // Modal pairing creation
  // ------------------------------------------------------------------
  var pairPollHandle = null;

  function stopPairPoll() {
    if (pairPollHandle) {
      clearInterval(pairPollHandle);
      pairPollHandle = null;
    }
  }

  // Surveille la disparition d'un code dans la liste des pairings actifs.
  // Quand le code disparait avant son expiration : la tablette l'a consomme.
  function startPairPoll(code, expiresAtIso) {
    stopPairPoll();
    var expMs = expiresAtIso ? new Date(expiresAtIso).getTime() : null;
    pairPollHandle = setInterval(function () {
      var s = currentScope();
      var qs = new URLSearchParams();
      if (s.event) qs.set("event", s.event);
      if (s.year) qs.set("year", s.year);
      apiGet("/field/admin/vision/pairings?" + qs.toString())
        .then(function (data) {
          var pairings = (data && data.pairings) || [];
          var stillActive = pairings.some(function (p) { return p.code === code; });
          if (stillActive) return;
          stopPairPoll();
          if (expMs && Date.now() < expMs) {
            // Code consomme par la tablette
            closePairModal();
            _toast("success", "Tablette Vision appairee !");
            loadDevices();
            loadPairings();
          }
          // Sinon : code expire, on laisse la modale ouverte (l'utilisateur verra que rien ne se passe)
        })
        .catch(function () {});
    }, 2000);
  }

  function openPairModal() {
    var scope = currentScope();
    var lbl = $("#vision-pair-event-label");
    if (lbl) {
      lbl.textContent = (scope.event && scope.year)
        ? (scope.event + " " + scope.year)
        : "(selectionne un evenement dans la sidebar)";
    }
    var form = $("#vision-pair-form");
    if (form) form.reset();
    var result = $("#vision-pair-result");
    if (result) result.textContent = "";
    stopPairPoll();
    var modal = $("#vision-pair-modal");
    if (modal) modal.hidden = false;
  }
  function closePairModal() {
    stopPairPoll();
    var modal = $("#vision-pair-modal");
    if (modal) modal.hidden = true;
  }

  function submitPairing() {
    var form = $("#vision-pair-form");
    if (!form) return;
    var fd = new FormData(form);
    var scope = currentScope();
    if (!scope.event || !scope.year) {
      _toast("error", "Selectionne un evenement dans la sidebar");
      return;
    }
    var payload = {
      name: (fd.get("name") || "").toString().trim(),
      lieu: (fd.get("lieu") || "").toString(),
      notes: (fd.get("notes") || "").toString().trim(),
      event: scope.event,
      year: scope.year,
    };
    if (!payload.name) { _toast("error", "Nom requis"); return; }
    if (!payload.lieu) { _toast("error", "Lieu requis"); return; }

    apiPost("/field/admin/vision/pairings", payload)
      .then(function (res) {
        if (res.body && res.body.ok && res.body.pairing) {
          renderPairingResult(res.body.pairing);
          loadPairings();
        } else {
          var err = (res.body && res.body.error) || "unknown_error";
          var map = {
            missing_name: "Nom requis.",
            missing_event_year: "Selectionne un evenement et une annee.",
            invalid_lieu: "Lieu invalide (Ouest, Panorama, Houx).",
            name_conflict: "Ce nom est deja utilise par une autre tablette Vision.",
          };
          _toast("error", map[err] || ("Erreur : " + err));
        }
      })
      .catch(function () { _toast("error", "Erreur reseau"); });
  }

  function renderPairingResult(p) {
    var box = $("#vision-pair-result");
    if (!box) return;
    while (box.firstChild) box.removeChild(box.firstChild);
    var wrap = document.createElement("div");
    wrap.style.cssText = "background:#003b5c; color:white; padding:18px; border-radius:10px; text-align:center;";
    var label = document.createElement("div");
    label.style.cssText = "font-size:11px; opacity:0.8; text-transform:uppercase; letter-spacing:2px; margin-bottom:6px;";
    label.textContent = "Code de pairing Vision";
    wrap.appendChild(label);
    var code = document.createElement("div");
    code.style.cssText = "font-family:monospace; font-size:2.4em; font-weight:800; letter-spacing:0.4em; margin:6px 0;";
    code.textContent = p.code;
    wrap.appendChild(code);
    var hint = document.createElement("div");
    hint.style.cssText = "font-size:12px; opacity:0.85;";
    hint.textContent = "A saisir sur https://vision-a0f55.web.app dans les 15 minutes — Lieu : " + (p.lieu || "?");
    wrap.appendChild(hint);
    var waiting = document.createElement("div");
    waiting.style.cssText = "margin-top:10px; font-size:11px; opacity:0.75; font-style:italic;";
    waiting.textContent = "En attente de saisie sur la tablette...";
    wrap.appendChild(waiting);
    box.appendChild(wrap);

    // Lance le polling : la modale se fermera automatiquement quand la tablette saisit le code
    if (p.code) startPairPoll(p.code, p.expiresAt);
  }

  // ------------------------------------------------------------------
  // Codes modal
  // ------------------------------------------------------------------
  function openCodesModal() {
    loadPairings();
    var modal = $("#vision-codes-modal");
    if (modal) modal.hidden = false;
  }

  // ------------------------------------------------------------------
  // Wiring
  // ------------------------------------------------------------------
  function init() {
    var btnNew = $("#vision-pair-new");
    if (btnNew) btnNew.addEventListener("click", openPairModal);
    var btnSubmit = $("#vision-pair-submit");
    if (btnSubmit) btnSubmit.addEventListener("click", submitPairing);
    var btnCodes = $("#vision-pair-show-codes");
    if (btnCodes) btnCodes.addEventListener("click", openCodesModal);

    // Fermeture des modales (data-close attribute, comme field_admin.js)
    document.querySelectorAll("#vision-pair-modal [data-close], #vision-codes-modal [data-close]").forEach(function (el) {
      el.addEventListener("click", function () {
        var modal = el.closest(".crud-modal");
        if (modal && modal.id === "vision-pair-modal") {
          closePairModal();
        } else if (modal) {
          modal.hidden = true;
        }
      });
    });

    // Refresh global (bouton "Actualiser" deja existant)
    var refresh = $("#field-admin-refresh");
    if (refresh) refresh.addEventListener("click", function () {
      loadDevices();
      loadPairings();
    });

    // Re-load quand le scope change
    var ev = $("#event-select");
    var yr = $("#year-select");
    if (ev) ev.addEventListener("change", function () { loadDevices(); loadPairings(); });
    if (yr) yr.addEventListener("change", function () { loadDevices(); loadPairings(); });

    loadDevices();
    loadPairings();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
