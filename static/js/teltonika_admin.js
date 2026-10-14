/**
 * teltonika_admin.js - Configuration > Balises GPS > Source Teltonika.
 * Parc (IMEI, nom, modele, periodes), sources d'arrivee (recepteur TCP maison,
 * flespi transitoire), IMEI inconnus, derniers messages d'une balise.
 * Le rattachement aux groupes se fait dans la fenetre du groupe (anoloc_admin.js).
 * Regles : docs/claude/balises.md.
 */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.from((r || document).querySelectorAll(s)); };
  var state = { devices: [], settings: {}, flespi: {}, receiver: {}, unknown: [] };
  var editingImei = null;

  function headers() {
    var h = { "Content-Type": "application/json" };
    var m = $('meta[name="csrf-token"]');
    if (m) h["X-CSRFToken"] = m.getAttribute("content");
    return h;
  }
  function getJson(url) { return fetch(url).then(function (r) { return r.json(); }); }
  function send(method, url, data) {
    return fetch(url, { method: method, headers: headers(), body: data ? JSON.stringify(data) : undefined })
      .then(function (r) { return r.json(); });
  }
  function toast(type, msg) { if (window.showToast) window.showToast(type, msg); }

  function h(tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "style") e.style.cssText = attrs[k];
      else if (k === "text") e.textContent = attrs[k];
      else if (k.indexOf("on") === 0) e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      if (c == null) return;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return e;
  }
  function icon(name, style) {
    return h("span", { "class": "material-symbols-outlined", style: "font-size:16px;vertical-align:middle;" + (style || ""), text: name });
  }

  function fmtDate(iso) {
    if (!iso) return "-";
    var d = new Date(iso);
    return isNaN(d) ? "-" : d.toLocaleString("fr-FR");
  }
  function fmtAgo(iso) {
    if (!iso) return "jamais";
    var s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 90) return "il y a " + Math.max(s, 0) + " s";
    if (s < 5400) return "il y a " + Math.round(s / 60) + " min";
    if (s < 172800) return "il y a " + Math.round(s / 3600) + " h";
    return fmtDate(iso);
  }
  function fmtStats(st) {
    if (!st) return "";
    return st.accepted + " accepte(s), " + st.duplicates + " doublon(s)" +
      (st.unknown ? ", " + st.unknown + " IMEI inconnu(s)" : "") +
      (st.bad_ts ? ", " + st.bad_ts + " date(s) aberrante(s)" : "");
  }

  // ------------------------------------------------------------------
  // Chargement et rendu
  // ------------------------------------------------------------------

  function load() {
    return getJson("/anoloc/teltonika").then(function (data) {
      if (!data || data.error) return;
      state = data;
      renderSources();
      renderPeriods();
      renderUnknown();
      renderDevices();
    }).catch(function () {});
  }

  function sourceCard(title, iconName, okState, lines, controls) {
    var color = okState === true ? "#22c55e" : okState === false ? "#ef4444" : "var(--muted)";
    return h("div", { "class": "tlt-source-card" }, [
      h("div", { style: "display:flex;align-items:center;gap:8px;font-weight:600;font-size:13px;" }, [
        icon(iconName, "font-size:20px;color:" + color), title,
      ]),
      h("div", { style: "font-size:11px;color:var(--muted);margin-top:4px;line-height:1.5;" },
        lines.map(function (l) { return h("div", { text: l }); })),
      controls || null,
    ]);
  }

  function renderSources() {
    var box = $("#tlt-sources");
    if (!box) return;
    box.textContent = "";
    var rx = state.receiver || {};
    var fl = state.flespi || {};
    var rxRecent = rx.last_post_at && (Date.now() - new Date(rx.last_post_at).getTime()) < 3600 * 1000;
    box.appendChild(sourceCard("Recepteur maison (TCP 5027)", "router", rx.last_post_at ? rxRecent : null, [
      "Secret d'ingestion (BALISES_INGEST_SECRET) : " + (rx.secret_present ? "present" : "ABSENT, ingestion refusee"),
      "Dernier envoi du recepteur : " + fmtAgo(rx.last_post_at),
      rx.last_stats ? "Dernier lot : " + fmtStats(rx.last_stats) : "",
    ]));

    var chInput = h("input", { type: "text", "class": "form-input", value: fl.channel_id || "",
                               placeholder: "1460243", style: "width:120px;" });
    var toggle = h("input", { type: "checkbox" });
    toggle.checked = !!fl.enabled;
    var saveBtn = h("button", { "class": "btn btn-xs btn-primary", text: "Enregistrer", onclick: function () {
      send("POST", "/anoloc/teltonika/settings", { flespi_enabled: toggle.checked, flespi_channel_id: chInput.value.trim() })
        .then(function (r) {
          if (r.ok) { toast("success", "Source flespi enregistree"); load(); }
          else toast("error", "Erreur : " + (r.error || ""));
        });
    } });
    var controls = h("div", { style: "display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px;" }, [
      h("label", { style: "display:flex;align-items:center;gap:6px;font-size:12px;" }, [toggle, "Relever flespi"]),
      h("label", { style: "font-size:12px;" }, ["Canal ", chInput]),
      saveBtn,
    ]);
    var flOk = fl.enabled ? !fl.last_error && !!fl.last_run : null;
    box.appendChild(sourceCard("flespi (transitoire, en attendant l'ouverture du port)", "cloud_sync", flOk, [
      "Jeton (FLESPI_TOKEN) : " + (fl.token_present ? "present" : "absent de l'environnement de Cockpit"),
      "Dernier releve : " + fmtAgo(fl.last_run) + (fl.last_error ? " - ERREUR : " + fl.last_error : ""),
      fl.last_stats ? "Dernier releve : " + fmtStats(fl.last_stats) : "",
      "Une balise vue par les deux chemins pendant la bascule n'est jamais dupliquee.",
    ], controls));
  }

  function renderPeriods() {
    var s = state.settings || {};
    var stop = $("#tlt-period-stop"), move = $("#tlt-period-move");
    if (stop && document.activeElement !== stop) stop.value = s.period_stop_s || "";
    if (move && document.activeElement !== move) move.value = s.period_move_s || "";
  }

  function renderUnknown() {
    var box = $("#tlt-unknown");
    if (!box) return;
    box.textContent = "";
    (state.unknown || []).forEach(function (u) {
      box.appendChild(h("div", { "class": "tlt-unknown-row" }, [
        icon("help", "color:#f59e0b;"),
        " IMEI inconnu " + u.imei + " (" + (u.source || "?") + ", " + u.count + " fois, " + fmtAgo(u.last_seen) + ") ",
        h("button", { "class": "btn btn-xs", text: "Ajouter au parc", onclick: function () { openDevice(null, u.imei); } }),
      ]));
    });
  }

  function renderDevices() {
    var tbody = $("#tlt-devices-table tbody");
    if (!tbody) return;
    tbody.textContent = "";
    var devs = state.devices || [];
    if (!devs.length) {
      tbody.appendChild(h("tr", {}, [h("td", { colspan: "8", style: "text-align:center;color:var(--muted);", text: "Aucune balise Teltonika" })]));
      return;
    }
    devs.forEach(function (d) {
      var etat;
      if (!d.enabled) etat = h("span", { "class": "tlt-badge tlt-off", text: "desactivee" });
      else if (!d.last_msg_at) etat = h("span", { "class": "tlt-badge tlt-off", text: "jamais recue" });
      else etat = h("span", { "class": "tlt-badge " + (d.online ? "tlt-on" : "tlt-ko"),
                              text: (d.online ? "en ligne" : "muette") + (d.online && !d.gps_fix ? " (sans GPS)" : "") +
                                    (d.movement ? " - en mouvement" : "") });
      var bat = d.battery_pct != null ? d.battery_pct + " %" + (d.charging ? " (charge)" : "") : "-";
      tbody.appendChild(h("tr", {}, [
        h("td", { text: d.label }),
        h("td", { style: "font-family:monospace;", text: d.imei }),
        h("td", { text: d.model || "" }),
        h("td", { text: (d.groups || []).join(", ") || "aucun" }),
        h("td", {}, [etat]),
        h("td", { text: bat }),
        h("td", { title: fmtDate(d.last_msg_at) + (d.ingest_source ? " via " + d.ingest_source : ""), text: fmtAgo(d.last_msg_at) }),
        h("td", { style: "white-space:nowrap;" }, [
          h("button", { "class": "btn-icon", title: "Derniers messages", onclick: function () { openMessages(d); } }, [icon("list_alt")]),
          h("button", { "class": "btn-icon", title: "Modifier", onclick: function () { openDevice(d); } }, [icon("edit")]),
          h("button", { "class": "btn-icon", title: "Retirer du parc", onclick: function () { removeDevice(d); } }, [icon("delete")]),
        ]),
      ]));
    });
  }

  // ------------------------------------------------------------------
  // Parc
  // ------------------------------------------------------------------

  function openDevice(d, imei) {
    var modal = $("#tlt-device-modal"), form = $("#tlt-device-form");
    if (!modal || !form) return;
    form.reset();
    editingImei = d ? d.imei : null;
    $("#tlt-device-modal-title").textContent = d ? "Modifier " + d.label : "Nouvelle balise Teltonika";
    var f = function (n) { return $('[name="' + n + '"]', form); };
    f("imei").value = d ? d.imei : (imei || "");
    f("imei").readOnly = !!d;
    f("label").value = d ? d.label : "";
    f("model").value = d ? (d.model || "") : "ATC700";
    f("commissioned_at").value = d && d.commissioned_at ? d.commissioned_at.slice(0, 10) : "";
    f("period_stop_s").value = d && d.period_stop_s ? d.period_stop_s : "";
    f("period_move_s").value = d && d.period_move_s ? d.period_move_s : "";
    f("notes").value = d ? (d.notes || "") : "";
    f("enabled").checked = d ? !!d.enabled : true;
    modal.hidden = false;
  }

  function saveDevice() {
    var form = $("#tlt-device-form");
    var f = function (n) { return $('[name="' + n + '"]', form); };
    var body = {
      imei: f("imei").value.trim(),
      label: f("label").value.trim(),
      model: f("model").value.trim(),
      commissioned_at: f("commissioned_at").value ? f("commissioned_at").value + "T00:00:00+00:00" : null,
      period_stop_s: f("period_stop_s").value,
      period_move_s: f("period_move_s").value,
      notes: f("notes").value,
      enabled: f("enabled").checked,
    };
    var errors = { imei_invalide: "IMEI invalide (chiffres uniquement)", nom_requis: "Nom requis", date_invalide: "Date invalide" };
    send("POST", "/anoloc/teltonika/device", body).then(function (r) {
      if (!r.ok) { toast("error", errors[r.error] || ("Erreur : " + (r.error || ""))); return; }
      toast("success", "Balise enregistree");
      $("#tlt-device-modal").hidden = true;
      load();
      document.dispatchEvent(new CustomEvent("teltonika:changed"));
    });
  }

  function removeDevice(d) {
    var ask = window.showConfirmToast ? window.showConfirmToast(
      "Retirer " + d.label + " (" + d.imei + ") du parc ? Elle sera refusee par le recepteur et retiree de ses groupes. Son historique est conserve.")
      : Promise.resolve(true);
    ask.then(function (ok) {
      if (!ok) return;
      send("DELETE", "/anoloc/teltonika/device/" + encodeURIComponent(d.imei)).then(function (r) {
        if (r.ok) {
          toast("success", "Balise retiree");
          load();
          document.dispatchEvent(new CustomEvent("teltonika:changed"));
        }
      });
    });
  }

  function savePeriods() {
    send("POST", "/anoloc/teltonika/settings", {
      period_stop_s: ($("#tlt-period-stop") || {}).value,
      period_move_s: ($("#tlt-period-move") || {}).value,
    }).then(function (r) {
      if (r.ok) { toast("success", "Periodes enregistrees"); load(); }
      else toast("error", "Periode invalide");
    });
  }

  // ------------------------------------------------------------------
  // Derniers messages (cadence reelle, fix, batterie)
  // ------------------------------------------------------------------

  function openMessages(d) {
    var modal = $("#tlt-messages-modal"), body = $("#tlt-messages-body");
    if (!modal || !body) return;
    $("#tlt-messages-title").textContent = "Derniers messages - " + d.label + " (" + d.imei + ")";
    body.textContent = "";
    body.appendChild(h("tr", {}, [h("td", { colspan: "9", text: "Chargement..." })]));
    modal.hidden = false;
    getJson("/anoloc/teltonika/messages?limit=200&imei=" + encodeURIComponent(d.imei)).then(function (r) {
      body.textContent = "";
      var msgs = (r && r.messages) || [];
      if (!msgs.length) { body.appendChild(h("tr", {}, [h("td", { colspan: "9", text: "Aucun message" })])); return; }
      msgs.forEach(function (m) {
        var gap = m.interval_s == null ? "" : (m.interval_s < 120 ? Math.round(m.interval_s) + " s" : Math.round(m.interval_s / 60) + " min");
        body.appendChild(h("tr", { style: m.ts_valid ? "" : "opacity:0.5;", title: m.ts_valid ? "" : "Horodatage aberrant (ignore)" }, [
          h("td", { text: fmtDate(m.ts) }),
          h("td", { text: gap }),
          h("td", { text: fmtDate(m.received_at) }),
          h("td", { text: m.source || "" }),
          h("td", { text: m.position_valid ? "oui" : "non" }),
          h("td", { text: m.speed != null ? Math.round(m.speed) + " km/h" : "-" }),
          h("td", { text: m.movement == null ? "-" : (m.movement ? "oui" : "non") }),
          h("td", { text: m.battery_pct != null ? m.battery_pct + " %" : "-" }),
          h("td", { style: "font-family:monospace;", text: m.position_valid && m.lat != null ? m.lat.toFixed(5) + ", " + m.lng.toFixed(5) : "-" }),
        ]));
      });
    });
  }

  // ------------------------------------------------------------------

  function init() {
    if (!$("#tlt-devices-table")) return;
    var add = $("#tlt-add-device");
    if (add) add.addEventListener("click", function () { openDevice(null); });
    var sp = $("#tlt-save-periods");
    if (sp) sp.addEventListener("click", savePeriods);
    var save = $("#tlt-device-modal-save");
    if (save) save.addEventListener("click", saveDevice);
    ["#tlt-device-modal", "#tlt-messages-modal"].forEach(function (sel) {
      var m = $(sel);
      if (!m) return;
      $$("[data-close]", m).forEach(function (b) { b.addEventListener("click", function () { m.hidden = true; }); });
      m.addEventListener("click", function (e) { if (e.target === m) m.hidden = true; });
    });
    load();
    setInterval(function () { if (!document.hidden) load(); }, 30000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
