/* Assistant IA : modale de resume de periode des fiches PC Organisation.
 * Active le bouton sidebar .sidebar-ai et orchestre les appels a /api/pcorg/summary/*.
 */
(function () {
  "use strict";

  var TAB_ORDER = ["overview", "secours", "securite", "technique", "recommandations"];
  var TAB_TITLES = {
    overview:        "Vue d'ensemble",
    secours:         "Secours",
    securite:        "Sécurité",
    technique:       "Technique",
    recommandations: "Recommandations"
  };
  var TAB_ICONS = {
    overview:        "dashboard",
    secours:         "local_hospital",
    securite:        "shield",
    technique:       "build",
    recommandations: "lightbulb"
  };
  var URGENCY_LABELS = { EU: "Detresse vitale", UA: "Urgence absolue", UR: "Urgence relative", IMP: "Implique" };

  var state = { busy: false, current: null, history: null, activeTab: "overview" };

  function csrfHeader() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") || "" : "";
  }

  function apiGetJson(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "Reponse invalide" }; });
    });
  }

  function apiPostJson(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfHeader()
      },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "Reponse invalide (HTTP " + r.status + ")" }; });
    });
  }

  function toast(msg, type) {
    if (window.showToast) { window.showToast(msg, type || "info"); return; }
    try { console.log("[ai-assistant]", type || "info", msg); } catch (e) {}
  }

  function isManager() {
    if (window.__userIsManager === true) return true;
    if (window.__userIsAdmin === true) return true;
    if (Array.isArray(window.__userRoles)) {
      return window.__userRoles.indexOf("manager") !== -1 || window.__userRoles.indexOf("admin") !== -1;
    }
    return false;
  }

  function selectedEventYear() {
    return {
      event: window.selectedEvent || "",
      year: window.selectedYear ? String(window.selectedYear) : ""
    };
  }

  function isAllEvents() {
    var cb = rootEl && rootEl.querySelector("#ai-all-events");
    return !!(cb && cb.checked);
  }

  function refreshContextLabel() {
    var ctx = rootEl && rootEl.querySelector("#ai-modal-context");
    if (!ctx) return;
    if (isAllEvents()) {
      ctx.textContent = "Tous les événements";
    } else {
      var ey = selectedEventYear();
      ctx.textContent = (ey.event || "?") + " · " + (ey.year || "?");
    }
  }

  // ---------- DOM helpers ----------

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") node.className = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else if (k.indexOf("on") === 0 && typeof attrs[k] === "function") node.addEventListener(k.slice(2), attrs[k]);
        else node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c == null) return;
      if (typeof c === "string") node.appendChild(document.createTextNode(c));
      else node.appendChild(c);
    });
    return node;
  }

  function clearChildren(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  // ---------- Date helpers ----------

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  function toLocalInputValue(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate())
         + "T" + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  function fromLocalInputValue(s) {
    if (!s) return null;
    var m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], 0, 0);
  }

  function formatPeriodHuman(startIso, endIso) {
    if (!startIso || !endIso) return "";
    try {
      var a = new Date(startIso);
      var b = new Date(endIso);
      var fmt = function (d) {
        return pad2(d.getDate()) + "/" + pad2(d.getMonth() + 1) + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
      };
      return fmt(a) + " → " + fmt(b);
    } catch (e) { return startIso + " -> " + endIso; }
  }

  // ---------- Modal markup ----------

  var rootEl = null;

  function buildModal() {
    if (rootEl) return rootEl;

    var overlay = el("div", { id: "ai-assistant-modal", class: "ai-modal-overlay", "aria-hidden": "true" });
    overlay.addEventListener("click", function (e) { if (e.target === overlay) closeModal(); });

    var modal = el("div", { class: "ai-modal", role: "dialog", "aria-modal": "true", "aria-label": "Assistant IA" });

    // Header
    var header = el("div", { class: "ai-modal-header" }, [
      el("span", { class: "material-symbols-outlined ai-modal-icon" }, ["smart_toy"]),
      el("div", { class: "ai-modal-titles" }, [
        el("h2", { class: "ai-modal-title", text: "Assistant IA — Résumé fiches PC Organisation" }),
        el("div", { class: "ai-modal-subtitle", id: "ai-modal-context" })
      ]),
      el("button", { type: "button", class: "ai-modal-close", title: "Fermer", "aria-label": "Fermer", onclick: closeModal }, [
        el("span", { class: "material-symbols-outlined" }, ["close"])
      ])
    ]);

    // Period controls
    var presets = el("div", { class: "ai-presets" }, [
      el("button", { type: "button", class: "ai-preset-btn", "data-preset": "1h",     text: "Dernière heure" }),
      el("button", { type: "button", class: "ai-preset-btn", "data-preset": "24h",    text: "Dernières 24h" }),
      el("button", { type: "button", class: "ai-preset-btn", "data-preset": "today",  text: "Aujourd'hui" }),
      el("button", { type: "button", class: "ai-preset-btn", "data-preset": "7d",     text: "7 derniers jours" })
    ]);
    presets.addEventListener("click", function (e) {
      var b = e.target.closest(".ai-preset-btn");
      if (!b) return;
      applyPreset(b.getAttribute("data-preset"));
    });

    var dateRow = el("div", { class: "ai-date-row" }, [
      el("label", { class: "ai-date-field" }, [
        el("span", { class: "ai-date-label", text: "Début" }),
        el("input", { type: "datetime-local", id: "ai-date-start", class: "ai-date-input" })
      ]),
      el("label", { class: "ai-date-field" }, [
        el("span", { class: "ai-date-label", text: "Fin" }),
        el("input", { type: "datetime-local", id: "ai-date-end", class: "ai-date-input" })
      ]),
      el("button", { type: "button", class: "ai-btn ai-btn-primary", id: "ai-btn-generate" }, [
        el("span", { class: "material-symbols-outlined" }, ["auto_awesome"]),
        el("span", { text: "Générer" })
      ])
    ]);

    var allCheckbox = el("input", { type: "checkbox", id: "ai-all-events" });
    var allLabel = el("label", { class: "ai-all-toggle", for: "ai-all-events" }, [
      allCheckbox,
      el("span", { text: "Tous les événements" })
    ]);
    allCheckbox.addEventListener("change", refreshContextLabel);

    var actionRow = el("div", { class: "ai-action-row" }, [
      allLabel,
      el("button", { type: "button", class: "ai-btn ai-btn-ghost", id: "ai-btn-history" }, [
        el("span", { class: "material-symbols-outlined" }, ["history"]),
        el("span", { text: "Historique" })
      ]),
      el("span", { class: "ai-status", id: "ai-status" })
    ]);

    var body = el("div", { class: "ai-modal-body", id: "ai-modal-body" }, [
      el("div", { class: "ai-empty", id: "ai-empty", text: "Choisissez une période et cliquez sur Générer." })
    ]);

    modal.appendChild(header);
    modal.appendChild(presets);
    modal.appendChild(dateRow);
    modal.appendChild(actionRow);
    modal.appendChild(body);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    overlay.querySelector("#ai-btn-generate").addEventListener("click", onGenerate);
    overlay.querySelector("#ai-btn-history").addEventListener("click", onShowHistory);

    rootEl = overlay;
    return overlay;
  }

  function setStatus(msg, kind) {
    var s = rootEl && rootEl.querySelector("#ai-status");
    if (!s) return;
    s.textContent = msg || "";
    s.className = "ai-status" + (kind ? " ai-status-" + kind : "");
  }

  function setBusy(b) {
    state.busy = !!b;
    if (!rootEl) return;
    var btn = rootEl.querySelector("#ai-btn-generate");
    if (btn) btn.disabled = state.busy;
    var btn2 = rootEl.querySelector("#ai-btn-history");
    if (btn2) btn2.disabled = state.busy;
  }

  function applyPreset(preset) {
    var now = new Date();
    var start = new Date(now);
    if (preset === "1h") {
      start = new Date(now.getTime() - 60 * 60 * 1000);
    } else if (preset === "24h") {
      start = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    } else if (preset === "today") {
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
    } else if (preset === "7d") {
      start = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    }
    rootEl.querySelector("#ai-date-start").value = toLocalInputValue(start);
    rootEl.querySelector("#ai-date-end").value = toLocalInputValue(now);
  }

  // ---------- Render summary ----------

  function renderEmpty(msg) {
    var body = rootEl.querySelector("#ai-modal-body");
    clearChildren(body);
    body.appendChild(el("div", { class: "ai-empty", text: msg || "Aucun résumé." }));
  }

  function renderSummary(summary) {
    state.current = summary;
    state.activeTab = "overview";
    var body = rootEl.querySelector("#ai-modal-body");
    clearChildren(body);

    var scopeLabel;
    if (summary.event && summary.year) {
      scopeLabel = summary.event + " " + summary.year;
    } else if (summary.event) {
      scopeLabel = summary.event + " (toutes années)";
    } else if (summary.year) {
      scopeLabel = "Année " + summary.year + " (tous événements)";
    } else {
      scopeLabel = "Tous événements";
    }
    var meta = el("div", { class: "ai-summary-meta" }, [
      el("span", { class: "ai-meta-chip", text: scopeLabel }),
      el("span", { class: "ai-meta-chip", text: formatPeriodHuman(summary.period_start, summary.period_end) }),
      el("span", { class: "ai-meta-chip", text: (summary.fiches_count || 0) + " fiche(s) analysée(s)" + (summary.truncated ? " (échantillon)" : "") }),
      el("span", { class: "ai-meta-chip ai-meta-soft", text: "Modèle : " + (summary.model || "?") })
    ]);
    body.appendChild(meta);

    var tabsRow = el("div", { class: "ai-tabs", role: "tablist" });
    var panelHost = el("div", { class: "ai-tab-panel-host" });
    TAB_ORDER.forEach(function (key) {
      var btn = el("button", {
        type: "button",
        class: "ai-tab" + (state.activeTab === key ? " ai-tab-active" : ""),
        role: "tab",
        "data-tab": key,
        "aria-selected": state.activeTab === key ? "true" : "false"
      }, [
        el("span", { class: "material-symbols-outlined" }, [TAB_ICONS[key]]),
        el("span", { text: TAB_TITLES[key] })
      ]);
      btn.addEventListener("click", function () { setActiveTab(key); });
      tabsRow.appendChild(btn);
    });
    body.appendChild(tabsRow);
    body.appendChild(panelHost);
    renderActivePanel();
  }

  function setActiveTab(key) {
    state.activeTab = key;
    if (!rootEl) return;
    var btns = rootEl.querySelectorAll(".ai-tab");
    btns.forEach(function (b) {
      var on = b.getAttribute("data-tab") === key;
      b.classList.toggle("ai-tab-active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    renderActivePanel();
  }

  function renderActivePanel() {
    var host = rootEl && rootEl.querySelector(".ai-tab-panel-host");
    if (!host || !state.current) return;
    clearChildren(host);
    var summary = state.current;
    var panel = el("div", { class: "ai-tab-panel ai-tab-" + state.activeTab, role: "tabpanel" });

    if (state.activeTab === "overview") {
      panel.appendChild(buildKpiCard(summary.kpis || {}));
      var faits = (summary.sections || {}).faits_marquants || "";
      panel.appendChild(el("div", { class: "ai-section-card ai-section-faits_marquants" }, [
        el("div", { class: "ai-section-header" }, [
          el("span", { class: "material-symbols-outlined" }, ["campaign"]),
          el("span", { class: "ai-section-title", text: "Faits marquants" })
        ]),
        el("div", { class: "ai-section-body" }, [
          el("p", { text: faits || "RAS" })
        ])
      ]));
    } else {
      var content = (summary.sections || {})[state.activeTab] || "";
      panel.appendChild(el("div", { class: "ai-section-card ai-section-" + state.activeTab }, [
        el("div", { class: "ai-section-header" }, [
          el("span", { class: "material-symbols-outlined" }, [TAB_ICONS[state.activeTab]]),
          el("span", { class: "ai-section-title", text: TAB_TITLES[state.activeTab] })
        ]),
        el("div", { class: "ai-section-body" }, [
          el("p", { text: content || "RAS" })
        ])
      ]));
    }
    host.appendChild(panel);
  }

  function buildKpiCard(kpis) {
    var box = el("div", { class: "ai-kpi-card" });
    var top = el("div", { class: "ai-kpi-top" }, [
      kpiTile("Total", kpis.total || 0, "description"),
      kpiTile("Ouvertes", kpis.open || 0, "pending"),
      kpiTile("Clôturées", kpis.closed || 0, "task_alt"),
      kpiTile(
        "Durée moy.",
        (kpis.avg_duration_min != null ? kpis.avg_duration_min + " min" : "—"),
        "schedule"
      )
    ]);
    box.appendChild(top);

    var cats = kpis.by_category || {};
    if (Object.keys(cats).length) {
      var catRow = el("div", { class: "ai-kpi-bars" });
      catRow.appendChild(el("div", { class: "ai-kpi-bars-title", text: "Par catégorie" }));
      var max = 0;
      Object.keys(cats).forEach(function (k) { if (cats[k] > max) max = cats[k]; });
      Object.keys(cats).sort(function (a, b) { return cats[b] - cats[a]; }).forEach(function (k) {
        var pct = max ? Math.round(100 * cats[k] / max) : 0;
        catRow.appendChild(el("div", { class: "ai-kpi-bar-row" }, [
          el("span", { class: "ai-kpi-bar-label", text: k }),
          el("span", { class: "ai-kpi-bar-track" }, [
            el("span", { class: "ai-kpi-bar-fill", style: "width:" + pct + "%" })
          ]),
          el("span", { class: "ai-kpi-bar-value", text: String(cats[k]) })
        ]));
      });
      box.appendChild(catRow);
    }

    var urg = kpis.by_urgency || {};
    var urgKeys = Object.keys(urg).filter(function (k) { return k !== "_none"; });
    if (urgKeys.length) {
      var urgRow = el("div", { class: "ai-kpi-urg" });
      urgRow.appendChild(el("div", { class: "ai-kpi-bars-title", text: "Par urgence" }));
      var pills = el("div", { class: "ai-kpi-urg-pills" });
      ["EU", "UA", "UR", "IMP"].forEach(function (lvl) {
        if (urg[lvl] == null) return;
        pills.appendChild(el("span", { class: "ai-urg-pill ai-urg-" + lvl, text: lvl + " " + urg[lvl] + " — " + URGENCY_LABELS[lvl] }));
      });
      urgRow.appendChild(pills);
      box.appendChild(urgRow);
    }

    var zones = kpis.top_zones || [];
    if (zones.length) {
      var zoneList = el("div", { class: "ai-kpi-list" }, [
        el("div", { class: "ai-kpi-bars-title", text: "Top zones" })
      ]);
      zones.slice(0, 5).forEach(function (z) {
        zoneList.appendChild(el("div", { class: "ai-kpi-list-row" }, [
          el("span", { text: z.desc }),
          el("span", { class: "ai-kpi-list-count", text: String(z.count) })
        ]));
      });
      box.appendChild(zoneList);
    }

    var events = kpis.by_event || [];
    if (events.length > 1) {
      var evList = el("div", { class: "ai-kpi-list" }, [
        el("div", { class: "ai-kpi-bars-title", text: "Par événement" })
      ]);
      events.forEach(function (ev) {
        var label = (ev.event || "?") + (ev.year ? " " + ev.year : "");
        evList.appendChild(el("div", { class: "ai-kpi-list-row" }, [
          el("span", { text: label }),
          el("span", { class: "ai-kpi-list-count", text: String(ev.count) })
        ]));
      });
      box.appendChild(evList);
    }

    return box;
  }

  function kpiTile(label, value, icon) {
    return el("div", { class: "ai-kpi-tile" }, [
      el("span", { class: "material-symbols-outlined" }, [icon]),
      el("div", { class: "ai-kpi-tile-text" }, [
        el("div", { class: "ai-kpi-tile-value", text: String(value) }),
        el("div", { class: "ai-kpi-tile-label", text: label })
      ])
    ]);
  }

  // ---------- Actions ----------

  function onGenerate() {
    if (state.busy) return;
    var allEvents = isAllEvents();
    var ey = selectedEventYear();
    if (!allEvents && (!ey.event || !ey.year)) {
      toast("Selectionnez un evenement et une annee, ou cochez Tous les evenements.", "error");
      return;
    }
    var startStr = rootEl.querySelector("#ai-date-start").value;
    var endStr = rootEl.querySelector("#ai-date-end").value;
    var s = fromLocalInputValue(startStr);
    var e = fromLocalInputValue(endStr);
    if (!s || !e) {
      toast("Periode invalide.", "error");
      return;
    }
    if (e <= s) {
      toast("La fin doit etre apres le debut.", "error");
      return;
    }

    setBusy(true);
    setStatus("Génération en cours… (10 à 60 s)", "info");
    var body = rootEl.querySelector("#ai-modal-body");
    clearChildren(body);
    body.appendChild(el("div", { class: "ai-loading" }, [
      el("span", { class: "ai-spinner" }),
      el("span", { text: "Calcul des KPIs et appel au modèle…" })
    ]));

    var payload = {
      period_start: startStr,
      period_end: endStr,
      all_events: allEvents
    };
    if (!allEvents) {
      payload.event = ey.event;
      payload.year = ey.year;
    }
    apiPostJson("/api/pcorg/summary/generate", payload).then(function (res) {
      setBusy(false);
      if (!res || !res.ok) {
        var err = (res && res.error) || "Erreur inconnue";
        setStatus("Échec : " + err, "error");
        renderEmpty("Échec : " + err);
        return;
      }
      setStatus("Résumé généré.", "ok");
      renderSummary(res.summary);
      state.history = null;
    });
  }

  function onShowHistory() {
    if (state.busy) return;
    setBusy(true);
    setStatus("Chargement de l'historique…", "info");
    var url = "/api/pcorg/summary/list";
    if (!isAllEvents()) {
      var ey = selectedEventYear();
      if (ey.event && ey.year) {
        url += "?event=" + encodeURIComponent(ey.event) + "&year=" + encodeURIComponent(ey.year);
      }
    }
    apiGetJson(url).then(function (res) {
      setBusy(false);
      if (!res || !res.ok) {
        setStatus("Échec : " + ((res && res.error) || "erreur"), "error");
        return;
      }
      setStatus("");
      renderHistory(res.items || []);
    });
  }

  function renderHistory(items) {
    var body = rootEl.querySelector("#ai-modal-body");
    clearChildren(body);
    if (!items.length) {
      body.appendChild(el("div", { class: "ai-empty", text: "Aucun résumé enregistré pour cet événement." }));
      return;
    }
    var list = el("div", { class: "ai-history-list" });
    items.forEach(function (it) {
      var row = el("button", { type: "button", class: "ai-history-row" }, [
        el("div", { class: "ai-history-period", text: formatPeriodHuman(it.period_start, it.period_end) }),
        el("div", { class: "ai-history-meta" }, [
          el("span", { class: "ai-history-author", text: (it.created_by_name || it.created_by || "?") }),
          el("span", { class: "material-symbols-outlined ai-history-dot" }, ["fiber_manual_record"]),
          el("span", { text: (it.fiches_count || 0) + " fiche(s)" }),
          el("span", { class: "material-symbols-outlined ai-history-dot" }, ["fiber_manual_record"]),
          el("span", { text: it.created_at ? new Date(it.created_at).toLocaleString() : "" })
        ])
      ]);
      row.addEventListener("click", function () { loadSummary(it.id); });
      list.appendChild(row);
    });
    body.appendChild(list);
  }

  function loadSummary(id) {
    setBusy(true);
    setStatus("Chargement…", "info");
    apiGetJson("/api/pcorg/summary/" + encodeURIComponent(id)).then(function (res) {
      setBusy(false);
      if (!res || !res.ok) {
        setStatus("Échec : " + ((res && res.error) || "erreur"), "error");
        return;
      }
      setStatus("");
      renderSummary(res.summary);
    });
  }

  // ---------- Open / close ----------

  function openModal() {
    if (!isManager()) {
      toast("Assistant IA réservé aux managers.", "error");
      return;
    }
    buildModal();
    refreshContextLabel();
    if (!rootEl.querySelector("#ai-date-start").value) applyPreset("24h");
    rootEl.classList.add("is-open");
    rootEl.setAttribute("aria-hidden", "false");
    setStatus("");
  }

  function closeModal() {
    if (!rootEl) return;
    rootEl.classList.remove("is-open");
    rootEl.setAttribute("aria-hidden", "true");
  }

  function attachSidebarBindings() {
    var btns = document.querySelectorAll(".sidebar-ai");
    if (!btns.length) return;
    btns.forEach(function (b) {
      b.setAttribute("role", "button");
      b.setAttribute("tabindex", "0");
      b.setAttribute("title", "Assistant IA — Résumé fiches PC");
      b.classList.add("sidebar-ai-active");
      b.addEventListener("click", function (e) { e.preventDefault(); openModal(); });
      b.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openModal(); }
      });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && rootEl && rootEl.classList.contains("is-open")) closeModal();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attachSidebarBindings);
  } else {
    attachSidebarBindings();
  }
})();
