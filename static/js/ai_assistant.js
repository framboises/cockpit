/* Assistant IA : modale de resume de periode des fiches PC Organisation.
 * Active le bouton sidebar .sidebar-ai et orchestre les appels a /api/pcorg/summary/*.
 */
(function () {
  "use strict";

  var TAB_ORDER = ["overview", "secours", "securite", "technique", "flux", "fourriere", "recommandations"];
  var TAB_TITLES = {
    overview:        "Vue d'ensemble",
    secours:         "Secours",
    securite:        "Sécurité",
    technique:       "Technique",
    flux:            "Flux",
    fourriere:       "Fourrière",
    recommandations: "Recommandations"
  };
  var TAB_ICONS = {
    overview:        "dashboard",
    secours:         "local_hospital",
    securite:        "shield",
    technique:       "build",
    flux:            "swap_calls",
    fourriere:       "directions_car",
    recommandations: "lightbulb"
  };
  // Mapping pour calculer le badge count par onglet a partir de kpis.by_category
  var TAB_CATEGORIES = {
    secours:   ["PCO.Secours"],
    securite:  ["PCO.Securite", "PCS.Surete", "PCS.Information"],
    technique: ["PCO.Technique", "PCO.MainCourante"],
    flux:      ["PCO.Flux"],
    fourriere: ["PCO.Fourriere"]
  };
  var URGENCY_LABELS = { EU: "Detresse vitale", UA: "Urgence absolue", UR: "Urgence relative", IMP: "Implique" };

  var state = { busy: false, current: null, history: null, activeTab: "overview", controlsCollapsed: false, recipients: null };

  function tabCount(kpis, key) {
    if (key === "overview" || key === "recommandations") return null;
    var cats = TAB_CATEGORIES[key] || [];
    var by = (kpis && kpis.by_category) || {};
    var n = 0;
    cats.forEach(function (c) { if (by[c]) n += by[c]; });
    return n;
  }

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

  // ---------- Mini markdown renderer (DOM-safe, no innerHTML) ----------
  // Supporte : - puces, paragraphes, **gras**.

  function _mdInlineToNodes(line) {
    // Découpe sur **xxx** et retourne une liste de noeuds (text + <strong>)
    var nodes = [];
    var rx = /\*\*([^*]+?)\*\*/g;
    var lastIdx = 0;
    var matches = String(line).matchAll(rx);
    for (var m of matches) {
      if (m.index > lastIdx) {
        nodes.push(document.createTextNode(line.substring(lastIdx, m.index)));
      }
      nodes.push(el("strong", null, [m[1]]));
      lastIdx = m.index + m[0].length;
    }
    if (lastIdx < line.length) {
      nodes.push(document.createTextNode(line.substring(lastIdx)));
    }
    return nodes;
  }

  function renderMd(text, container) {
    // Vide le container puis rend le markdown leger.
    clearChildren(container);
    if (!text) return;
    var t = String(text).replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
    if (!t) return;
    var lines = t.split("\n");
    var i = 0;
    while (i < lines.length) {
      var stripped = lines[i].trim();
      if (!stripped) { i++; continue; }
      if (/^[-*]\s/.test(stripped)) {
        var ul = el("ul", { class: "ai-md-ul" });
        while (i < lines.length) {
          var s = lines[i].trim();
          if (/^[-*]\s/.test(s)) {
            var liText = s.replace(/^[-*]\s+/, "");
            var li = el("li", { class: "ai-md-li" });
            _mdInlineToNodes(liText).forEach(function (n) { li.appendChild(n); });
            ul.appendChild(li);
            i++;
          } else {
            break;
          }
        }
        container.appendChild(ul);
      } else {
        var p = el("p", { class: "ai-md-p" });
        var first = true;
        while (i < lines.length) {
          var s2 = lines[i].trim();
          if (!s2) break;
          if (/^[-*]\s/.test(s2)) break;
          if (!first) p.appendChild(el("br"));
          _mdInlineToNodes(s2).forEach(function (n) { p.appendChild(n); });
          first = false;
          i++;
        }
        container.appendChild(p);
      }
    }
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

    var dateRowChildren = [
      el("label", { class: "ai-date-field" }, [
        el("span", { class: "ai-date-label", text: "Début" }),
        el("input", { type: "datetime-local", id: "ai-date-start", class: "ai-date-input" })
      ]),
      el("label", { class: "ai-date-field" }, [
        el("span", { class: "ai-date-label", text: "Fin" }),
        el("input", { type: "datetime-local", id: "ai-date-end", class: "ai-date-input" })
      ])
    ];
    if (window.__userIsAdmin === true) {
      dateRowChildren.push(el("label", { class: "ai-date-field ai-date-field-test" }, [
        el("span", { class: "ai-date-label", text: "Simuler le « maintenant » (admin)" }),
        el("input", { type: "datetime-local", id: "ai-date-asof", class: "ai-date-input",
                      title: "Reserve admin : fixe le 'now' virtuel pour tester upcoming / billetterie / portes hors periode d'evenement" })
      ]));
    }
    dateRowChildren.push(el("button", { type: "button", class: "ai-btn ai-btn-primary", id: "ai-btn-generate" }, [
      el("span", { class: "material-symbols-outlined" }, ["auto_awesome"]),
      el("span", { text: "Générer" })
    ]));
    var dateRow = el("div", { class: "ai-date-row" }, dateRowChildren);

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
      el("button", { type: "button", class: "ai-btn ai-btn-ghost", id: "ai-btn-send-mail", disabled: "true" }, [
        el("span", { class: "material-symbols-outlined" }, ["mail"]),
        el("span", { text: "Envoyer par mail" })
      ]),
      el("span", { class: "ai-status", id: "ai-status" })
    ]);

    var body = el("div", { class: "ai-modal-body", id: "ai-modal-body" }, [
      el("div", { class: "ai-empty", id: "ai-empty", text: "Choisissez une période et cliquez sur Générer." })
    ]);

    var controls = el("div", { class: "ai-controls", id: "ai-controls" }, [
      presets, dateRow, actionRow
    ]);

    var collapsedBar = el("button", {
      type: "button",
      class: "ai-controls-collapsed",
      id: "ai-controls-collapsed",
      title: "Modifier la période",
      "aria-label": "Modifier la période"
    }, [
      el("span", { class: "material-symbols-outlined" }, ["tune"]),
      el("span", { class: "ai-controls-collapsed-label", id: "ai-controls-collapsed-label", text: "Modifier la période" }),
      el("span", { class: "material-symbols-outlined ai-controls-collapsed-chevron" }, ["expand_more"])
    ]);
    collapsedBar.addEventListener("click", function () { setControlsCollapsed(false); });

    modal.appendChild(header);
    modal.appendChild(controls);
    modal.appendChild(collapsedBar);
    modal.appendChild(body);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    overlay.querySelector("#ai-btn-generate").addEventListener("click", onGenerate);
    overlay.querySelector("#ai-btn-history").addEventListener("click", onShowHistory);
    overlay.querySelector("#ai-btn-send-mail").addEventListener("click", openSendMailModal);

    rootEl = overlay;
    return overlay;
  }

  function refreshSendMailButton() {
    if (!rootEl) return;
    var btn = rootEl.querySelector("#ai-btn-send-mail");
    if (!btn) return;
    btn.disabled = !state.current || !state.current.id;
  }

  function setControlsCollapsed(c) {
    state.controlsCollapsed = !!c;
    if (!rootEl) return;
    rootEl.classList.toggle("ai-controls-hidden", state.controlsCollapsed);
    var lbl = rootEl.querySelector("#ai-controls-collapsed-label");
    if (lbl && state.current) {
      var startStr = rootEl.querySelector("#ai-date-start").value;
      var endStr = rootEl.querySelector("#ai-date-end").value;
      var s = fromLocalInputValue(startStr);
      var e = fromLocalInputValue(endStr);
      if (s && e) {
        var fmt = function (d) {
          return pad2(d.getDate()) + "/" + pad2(d.getMonth() + 1) + " " + pad2(d.getHours()) + "h" + pad2(d.getMinutes());
        };
        lbl.textContent = "Période : " + fmt(s) + " → " + fmt(e) + " · Modifier";
      }
    }
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
    refreshSendMailButton();
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
      var children = [
        el("span", { class: "material-symbols-outlined" }, [TAB_ICONS[key]]),
        el("span", { text: TAB_TITLES[key] })
      ];
      var n = tabCount(summary.kpis, key);
      if (n != null && n > 0) {
        children.push(el("span", { class: "ai-tab-badge", text: String(n) }));
      }
      var btn = el("button", {
        type: "button",
        class: "ai-tab" + (state.activeTab === key ? " ai-tab-active" : ""),
        role: "tab",
        "data-tab": key,
        "aria-selected": state.activeTab === key ? "true" : "false"
      }, children);
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
      // 1. Tuiles KPI (4 chiffres clés + chips comparatives)
      panel.appendChild(buildKpiTilesCard(summary.kpis || {}, summary.comparisons || null));
      // 2. Faits marquants (la synthèse a été retirée car redondante)
      panel.appendChild(buildFaitsMarquantsCard(summary));
      // 3. Détails KPI
      var detailsCard = buildKpiDetailsCard(summary.kpis || {});
      if (detailsCard) panel.appendChild(detailsCard);
      // 4. Cartes contextuelles
      var upcomingCard = buildUpcomingCard(summary);
      if (upcomingCard) panel.appendChild(upcomingCard);
      var attendanceCard = buildAttendanceCard(summary);
      if (attendanceCard) panel.appendChild(attendanceCard);
      var doorsCard = buildDoorsCard(summary);
      if (doorsCard) panel.appendChild(doorsCard);
    } else {
      var content = (summary.sections || {})[state.activeTab] || "";
      var card = el("div", { class: "ai-section-card ai-section-" + state.activeTab }, [
        el("div", { class: "ai-section-header" }, [
          el("span", { class: "material-symbols-outlined" }, [TAB_ICONS[state.activeTab]]),
          el("span", { class: "ai-section-title", text: TAB_TITLES[state.activeTab] })
        ])
      ]);
      var body = el("div", { class: "ai-section-body" });
      if (content) {
        renderMd(content, body);
      } else {
        body.appendChild(el("p", { class: "ai-md-ras", text: "RAS" }));
      }
      card.appendChild(body);
      panel.appendChild(card);
    }
    host.appendChild(panel);
  }

  var DOORS_CAT_COLORS = {
    "PCO.Flux":         "#0d9488",
    "PCO.Securite":     "#ef4444",
    "PCO.Information":  "#2563eb",
    "PCO.MainCourante": "#8b5cf6"
  };
  var DOORS_CAT_LABELS = {
    "PCO.Flux":         "Flux",
    "PCO.Securite":     "Sécurité",
    "PCO.Information":  "Info",
    "PCO.MainCourante": "Main courante"
  };

  function buildDoorsCard(summary) {
    var dr = summary.door_reinforcement;
    if (!dr || !dr.recommendations || !dr.recommendations.length) return null;

    var card = el("div", { class: "ai-doors-card" });
    card.appendChild(el("div", { class: "ai-doors-header" }, [
      el("span", { class: "material-symbols-outlined" }, ["meeting_room"]),
      el("span", { class: "ai-doors-title", text: "Renforts conseillés sur les portes (24h à venir)" }),
      el("span", { class: "ai-doors-count", text: dr.recommendations.length + " reco(s)" })
    ]));
    card.appendChild(el("div", { class: "ai-doors-sub", text: "Aligné sur le jour-équivalent course de l'édition précédente (" + (dr.year_prev || "?") + ")" }));

    var table = el("table", { class: "ai-doors-table" });
    var thead = el("thead", null, [
      el("tr", null, [
        el("th", { text: "Porte" }),
        el("th", { text: "Créneau" }),
        el("th", { text: "Pic N-1" }),
        el("th", { text: "Incidents N-1" }),
        el("th", { text: "Criticité" })
      ])
    ]);
    var tbody = el("tbody");
    dr.recommendations.forEach(function (r) {
      var tr = el("tr", { class: "ai-doors-row ai-doors-criticite-" + r.criticite });
      tr.appendChild(el("td", { class: "ai-doors-porte" }, [
        el("strong", { text: r.family_label || "?" }),
        (r.doors && r.doors.length > 1)
          ? el("div", { class: "ai-doors-sub-doors", text: r.doors.length + " portes" })
          : null
      ]));
      tr.appendChild(el("td", { class: "ai-doors-creneau", text: r.slot_label_n }));
      var picCell = el("td", { class: "ai-doors-pic" });
      if (r.n1_scan_count) picCell.appendChild(el("span", { text: formatNumberFr(r.n1_scan_count) }));
      else picCell.appendChild(el("span", { class: "ai-doors-dim", text: "—" }));
      if (r.is_top3_pic) picCell.appendChild(el("span", { class: "ai-doors-pic-badge", text: "top 3" }));
      tr.appendChild(picCell);
      var incCell = el("td", { class: "ai-doors-inc" });
      var byCat = r.n1_fiches_by_category || {};
      Object.keys(byCat).forEach(function (cat) {
        var color = DOORS_CAT_COLORS[cat] || "#64748b";
        var label = DOORS_CAT_LABELS[cat] || cat;
        incCell.appendChild(el("span", {
          class: "ai-doors-cat-chip",
          style: "background:" + color + ";",
          text: label + " " + byCat[cat]
        }));
      });
      if (!Object.keys(byCat).length) {
        incCell.appendChild(el("span", { class: "ai-doors-dim", text: "—" }));
      }
      tr.appendChild(incCell);
      tr.appendChild(el("td", { class: "ai-doors-criticite-cell" }, [
        el("span", { class: "ai-doors-criticite-badge ai-doors-criticite-" + r.criticite + "-badge",
                     text: r.criticite === "forte" ? "Forte" : "Modérée" })
      ]));
      tbody.appendChild(tr);
    });
    table.appendChild(thead);
    table.appendChild(tbody);
    card.appendChild(table);
    return card;
  }

  function buildAttendanceCard(summary) {
    var att = summary.attendance;
    if (!att || !att.slots || !att.slots.length) return null;

    var card = el("div", { class: "ai-attendance-card" });
    card.appendChild(el("div", { class: "ai-attendance-header" }, [
      el("span", { class: "material-symbols-outlined" }, ["confirmation_number"]),
      el("span", { class: "ai-attendance-title", text: "Billetterie & fréquentation" }),
      att.prev_year
        ? el("span", { class: "ai-attendance-sub", text: "Comparé à " + att.prev_year })
        : null
    ]));

    var grid = el("div", { class: "ai-attendance-grid" });
    att.slots.forEach(function (s) {
      grid.appendChild(buildAttendanceSlot(s));
    });
    card.appendChild(grid);
    return card;
  }

  function buildAttendanceSlot(slot) {
    var dateLabel = "";
    var d = slot.date ? new Date(slot.date + "T12:00:00") : null;
    if (d && !isNaN(d.getTime())) {
      dateLabel = pad2(d.getDate()) + "/" + pad2(d.getMonth() + 1);
    }
    var col = el("div", { class: "ai-attendance-slot ai-attendance-slot-" + slot.slot });
    col.appendChild(el("div", { class: "ai-attendance-slot-header" }, [
      el("span", { class: "ai-attendance-slot-label", text: slot.label }),
      dateLabel ? el("span", { class: "ai-attendance-slot-date", text: dateLabel }) : null
    ]));

    if (!slot.is_public) {
      col.appendChild(el("div", { class: "ai-attendance-empty", text: "Pas d'ouverture publique" }));
      return col;
    }

    // Pic principal :
    // - "yesterday" : UNIQUEMENT le pic constaté (pas de fallback projection)
    // - "today"     : pic projeté en valeur principale + pic en cours en complément
    // - "tomorrow"  : projection
    var mainPic, mainLabel;
    if (slot.slot === "yesterday") {
      mainPic = slot.pic_observed;
      mainLabel = "Pic constaté";
    } else if (slot.slot === "today") {
      mainPic = slot.pic_projection != null ? slot.pic_projection : slot.pic_observed;
      mainLabel = slot.pic_projection != null ? "Pic projeté" : "Pic en cours";
    } else {
      mainPic = slot.pic_projection;
      mainLabel = "Pic projeté";
    }

    if (mainPic != null) {
      col.appendChild(el("div", { class: "ai-attendance-main" }, [
        el("div", { class: "ai-attendance-main-label", text: mainLabel }),
        el("div", { class: "ai-attendance-main-value", text: formatNumberFr(mainPic) })
      ]));
    } else {
      col.appendChild(el("div", { class: "ai-attendance-empty",
                                  text: slot.slot === "yesterday" ? "Pic non disponible" : "Pas de donnée" }));
    }

    // Comparaison N-1 : couleurs sémantiques metier (hausse de fréquentation = vert,
    // baisse = rouge). Inverse de la sémantique fiches d'incident.
    if (slot.pic_prev != null && mainPic != null) {
      var deltaTxt = "";
      var kind = "flat";
      var arrow = "→";
      if (slot.delta_pct_vs_prev != null) {
        var v = slot.delta_pct_vs_prev;
        deltaTxt = (v >= 0 ? "+" : "") + v + "%";
        if (Math.abs(v) >= 5) {
          kind = v > 0 ? "up" : "down";
          arrow = v > 0 ? "↑" : "↓";
        }
      }
      col.appendChild(el("div", { class: "ai-attendance-prev ai-attendance-delta-" + kind }, [
        el("span", { class: "ai-attendance-prev-arrow", text: arrow }),
        el("span", { text: " " + deltaTxt + " " }),
        el("span", { class: "ai-attendance-prev-value", text: "(" + formatNumberFr(slot.pic_prev) + ")" })
      ]));
    }

    // Aujourd'hui : si projection ET observé dispo, affiche le "Pic en cours" en complément
    if (slot.slot === "today" && slot.pic_observed != null && slot.pic_projection != null) {
      col.appendChild(el("div", { class: "ai-attendance-extra ai-attendance-live" }, [
        el("span", { text: "Pic en cours : " }),
        el("strong", { text: formatNumberFr(slot.pic_observed) })
      ]));
    }

    // Billets vendus
    if (slot.billets_vendus != null) {
      col.appendChild(el("div", { class: "ai-attendance-tickets" }, [
        el("span", { class: "material-symbols-outlined" }, ["confirmation_number"]),
        el("span", { class: "ai-attendance-tickets-value", text: formatNumberFr(slot.billets_vendus) }),
        el("span", { class: "ai-attendance-tickets-label", text: "billet(s) vendu(s)" })
      ]));
    }

    return col;
  }

  function formatNumberFr(n) {
    if (n == null) return "—";
    try { return Number(n).toLocaleString("fr-FR"); } catch (e) { return String(n); }
  }

  function buildUpcomingCard(summary) {
    var sections = summary.sections || {};
    var briefing = sections.prochaines_24h || "";
    var items = summary.upcoming || [];
    if (!briefing && !items.length) return null;
    var card = el("div", { class: "ai-upcoming-card" });
    card.appendChild(el("div", { class: "ai-upcoming-header" }, [
      el("span", { class: "material-symbols-outlined" }, ["schedule"]),
      el("span", { class: "ai-upcoming-title", text: "Prochaines 24 heures" }),
      el("span", { class: "ai-upcoming-count", text: items.length ? String(items.length) + " jalon(s)" : "aucun jalon" })
    ]));
    if (briefing) {
      var briefingEl = el("div", { class: "ai-upcoming-briefing" });
      renderMd(briefing, briefingEl);
      card.appendChild(briefingEl);
    }
    if (items.length) {
      var list = el("div", { class: "ai-upcoming-list" });
      items.forEach(function (it) {
        var labelChildren = [];
        // Pour les items factorisés on met l'activité en gras et le place en regular
        if (it.is_factorized) {
          labelChildren.push(el("strong", { text: it.activity || "" }));
          if (it.place) {
            labelChildren.push(document.createTextNode(" — "));
            labelChildren.push(el("span", { class: "ai-upcoming-places", text: it.place }));
          }
        } else {
          var lbl = it.activity || "";
          if (it.place) lbl += " — " + it.place;
          labelChildren.push(document.createTextNode(lbl));
        }
        var meta = [];
        if (it.event) meta.push(it.event + (it.year ? " " + it.year : ""));
        if (it.category) meta.push(it.category);
        if (it.department) meta.push(it.department);
        list.appendChild(el("div", { class: "ai-upcoming-row" + (it.is_factorized ? " ai-upcoming-row-factorized" : "") }, [
          el("span", { class: "ai-upcoming-time", text: formatUpcomingWhen(it) }),
          el("div", { class: "ai-upcoming-text" }, [
            el("div", { class: "ai-upcoming-label" }, labelChildren),
            meta.length ? el("div", { class: "ai-upcoming-meta", text: meta.join(" · ") }) : null
          ])
        ]));
      });
      card.appendChild(list);
    }
    return card;
  }

  function formatUpcomingWhen(it) {
    var dt = it.datetime ? new Date(it.datetime) : null;
    if (dt && !isNaN(dt.getTime())) {
      var sameDay = isToday(dt);
      var t = pad2(dt.getHours()) + "h" + pad2(dt.getMinutes());
      if (sameDay) return "Aujourd'hui " + t;
      var d = pad2(dt.getDate()) + "/" + pad2(dt.getMonth() + 1);
      return d + " " + t;
    }
    return (it.date || "") + " " + (it.time || "");
  }

  function isToday(d) {
    var n = new Date();
    return d.getFullYear() === n.getFullYear()
        && d.getMonth() === n.getMonth()
        && d.getDate() === n.getDate();
  }

  function buildFaitsMarquantsCard(summary) {
    var faits = (summary.sections || {}).faits_marquants || "";
    var card = el("div", { class: "ai-section-card ai-section-faits_marquants" }, [
      el("div", { class: "ai-section-header" }, [
        el("span", { class: "material-symbols-outlined" }, ["campaign"]),
        el("span", { class: "ai-section-title", text: "Faits marquants" })
      ])
    ]);
    var body = el("div", { class: "ai-section-body" });
    if (faits) renderMd(faits, body);
    else body.appendChild(el("p", { class: "ai-md-ras", text: "RAS" }));
    card.appendChild(body);
    return card;
  }

  function buildKpiTilesCard(kpis, comparisons) {
    var box = el("div", { class: "ai-kpi-card" });
    var top = el("div", { class: "ai-kpi-top" }, [
      kpiTile("Total", kpis.total || 0, "description", deltasFor(kpis.total, comparisons)),
      kpiTile("Ouvertes", kpis.open || 0, "pending"),
      kpiTile("Clôturées", kpis.closed || 0, "task_alt"),
      kpiTile(
        "Durée moy.",
        (kpis.avg_duration_min != null ? kpis.avg_duration_min + " min" : "—"),
        "schedule"
      )
    ]);
    box.appendChild(top);
    if (comparisons) {
      var compRow = buildComparisonsRow(kpis, comparisons);
      if (compRow) box.appendChild(compRow);
    }
    return box;
  }

  function buildKpiDetailsCard(kpis) {
    if (!kpis) return null;
    var hasContent = (kpis.by_category && Object.keys(kpis.by_category).length)
      || (kpis.by_urgency && Object.keys(kpis.by_urgency).length)
      || (kpis.top_zones && kpis.top_zones.length)
      || (kpis.by_event && kpis.by_event.length > 1);
    if (!hasContent) return null;
    var box = el("div", { class: "ai-kpi-card" });

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
    var events = kpis.by_event || [];
    var leftRight = el("div", { class: "ai-kpi-cols" });
    if (zones.length) {
      var zoneList = el("div", { class: "ai-kpi-list" }, [
        el("div", { class: "ai-kpi-bars-title", text: "Top zones" })
      ]);
      zones.slice(0, 8).forEach(function (z) {
        zoneList.appendChild(el("div", { class: "ai-kpi-list-row" }, [
          el("span", { text: z.desc }),
          el("span", { class: "ai-kpi-list-count", text: String(z.count) })
        ]));
      });
      leftRight.appendChild(zoneList);
    }
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
      leftRight.appendChild(evList);
    }
    if (leftRight.childNodes.length) box.appendChild(leftRight);

    return box;
  }

  function kpiTile(label, value, icon, deltas) {
    var children = [
      el("span", { class: "material-symbols-outlined" }, [icon]),
      el("div", { class: "ai-kpi-tile-text" }, [
        el("div", { class: "ai-kpi-tile-value", text: String(value) }),
        el("div", { class: "ai-kpi-tile-label", text: label })
      ])
    ];
    var tile = el("div", { class: "ai-kpi-tile" }, children);
    if (deltas && deltas.length) {
      var deltaRow = el("div", { class: "ai-kpi-tile-deltas" });
      deltas.forEach(function (d) {
        deltaRow.appendChild(el("span", {
          class: "ai-kpi-delta ai-kpi-delta-" + d.kind,
          title: d.title || ""
        }, [
          el("span", { text: d.label + " " }),
          el("span", { class: "ai-kpi-delta-arrow", text: d.arrow }),
          el("span", { text: " " + d.value })
        ]));
      });
      tile.appendChild(deltaRow);
    }
    return tile;
  }

  function deltasFor(currentTotal, comparisons) {
    if (!comparisons) return null;
    var out = [];
    var prev = comparisons.prev_period;
    if (prev && prev.kpis && (prev.kpis.total || 0) > 0) {
      out.push(formatDelta("Période précédente", currentTotal, prev.kpis.total, prev.label));
    }
    var py = comparisons.prev_year_aligned;
    if (py && py.kpis && (py.kpis.total || 0) > 0) {
      var label = "N-1" + (py.year_prev ? " (" + py.year_prev + ")" : "");
      out.push(formatDelta(label, currentTotal, py.kpis.total, py.label));
    }
    return out;
  }

  function formatDelta(label, current, ref, title) {
    var cur = Number(current) || 0;
    var rf = Number(ref) || 0;
    var diff = cur - rf;
    var pct = rf > 0 ? Math.round(100 * diff / rf) : null;
    var kind = "flat";
    var arrow = "→";
    if (pct !== null && Math.abs(pct) >= 5) {
      if (diff > 0) { kind = "up"; arrow = "↑"; }
      else if (diff < 0) { kind = "down"; arrow = "↓"; }
    }
    var value = (pct !== null ? (pct >= 0 ? "+" : "") + pct + "%" : (diff >= 0 ? "+" : "") + diff);
    return { label: label, value: value, arrow: arrow, kind: kind, title: title || "" };
  }

  function buildComparisonsRow(kpis, comparisons) {
    var prev = comparisons.prev_period;
    var py = comparisons.prev_year_aligned;
    if ((!prev || !(prev.kpis && prev.kpis.total)) && (!py || !(py.kpis && py.kpis.total))) return null;
    var row = el("div", { class: "ai-comparisons" });
    if (prev && prev.kpis) {
      row.appendChild(comparisonChip(
        "Période précédente",
        prev.kpis.total || 0,
        kpis.total || 0,
        prev.period_start, prev.period_end
      ));
    }
    if (py && py.kpis) {
      row.appendChild(comparisonChip(
        "Édition précédente" + (py.year_prev ? " (" + py.year_prev + ")" : ""),
        py.kpis.total || 0,
        kpis.total || 0,
        py.period_start, py.period_end
      ));
    }
    return row;
  }

  function comparisonChip(label, refValue, currentValue, startIso, endIso) {
    var d = formatDelta(label, currentValue, refValue, "");
    return el("div", { class: "ai-comparison-chip ai-kpi-delta-" + d.kind, title: formatPeriodHuman(startIso, endIso) }, [
      el("span", { class: "ai-comparison-chip-label", text: label }),
      el("span", { class: "ai-comparison-chip-value", text: String(refValue) + " fiche(s)" }),
      el("span", { class: "ai-comparison-chip-delta", text: d.arrow + " " + d.value })
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
    var asOfInput = rootEl.querySelector("#ai-date-asof");
    if (asOfInput && asOfInput.value) {
      payload.as_of = asOfInput.value;
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
      setControlsCollapsed(true);
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
      // Pre-remplit les date pickers avec la periode du resume charge.
      if (res.summary && res.summary.period_start && res.summary.period_end) {
        try {
          var s = new Date(res.summary.period_start);
          var e = new Date(res.summary.period_end);
          if (!isNaN(s.getTime()) && !isNaN(e.getTime())) {
            rootEl.querySelector("#ai-date-start").value = toLocalInputValue(s);
            rootEl.querySelector("#ai-date-end").value = toLocalInputValue(e);
          }
        } catch (e) {}
      }
      renderSummary(res.summary);
      setControlsCollapsed(true);
    });
  }

  // ---------- Envoyer par mail ----------

  var sendMailEl = null;
  var sendMailState = { selectedUsers: {}, selectedGroups: {}, query: "", busy: false };

  function openSendMailModal() {
    if (!state.current || !state.current.id) {
      toast("Aucun résumé chargé.", "error");
      return;
    }
    sendMailState = { selectedUsers: {}, selectedGroups: {}, query: "", busy: false };
    buildSendMailModal();
    sendMailEl.classList.add("is-open");
    sendMailEl.setAttribute("aria-hidden", "false");
    loadRecipients();
  }

  function closeSendMailModal() {
    if (!sendMailEl) return;
    sendMailEl.classList.remove("is-open");
    sendMailEl.setAttribute("aria-hidden", "true");
  }

  function buildSendMailModal() {
    if (sendMailEl) return sendMailEl;
    var overlay = el("div", { id: "ai-send-mail-modal", class: "ai-modal-overlay ai-send-modal-overlay", "aria-hidden": "true" });
    overlay.addEventListener("click", function (e) { if (e.target === overlay) closeSendMailModal(); });

    var modal = el("div", { class: "ai-modal ai-send-modal", role: "dialog", "aria-modal": "true", "aria-label": "Envoyer le rapport" });

    var header = el("div", { class: "ai-modal-header" }, [
      el("span", { class: "material-symbols-outlined ai-modal-icon" }, ["mail"]),
      el("div", { class: "ai-modal-titles" }, [
        el("h2", { class: "ai-modal-title", text: "Envoyer le rapport par mail" }),
        el("div", { class: "ai-modal-subtitle", text: "Choisissez les destinataires (utilisateurs et/ou groupes)." })
      ]),
      el("button", { type: "button", class: "ai-modal-close", title: "Fermer", onclick: closeSendMailModal }, [
        el("span", { class: "material-symbols-outlined" }, ["close"])
      ])
    ]);

    var search = el("div", { class: "ai-send-search" }, [
      el("span", { class: "material-symbols-outlined" }, ["search"]),
      el("input", { type: "text", id: "ai-send-search-input", placeholder: "Rechercher un utilisateur ou un groupe..." })
    ]);

    var lists = el("div", { class: "ai-send-lists" }, [
      el("div", { class: "ai-send-pane" }, [
        el("div", { class: "ai-send-pane-header", text: "Groupes" }),
        el("div", { class: "ai-send-pane-body", id: "ai-send-groups-list" }, [
          el("div", { class: "ai-send-loading", text: "Chargement..." })
        ])
      ]),
      el("div", { class: "ai-send-pane" }, [
        el("div", { class: "ai-send-pane-header", text: "Utilisateurs" }),
        el("div", { class: "ai-send-pane-body", id: "ai-send-users-list" }, [
          el("div", { class: "ai-send-loading", text: "Chargement..." })
        ])
      ])
    ]);

    var footer = el("div", { class: "ai-send-footer" }, [
      el("div", { class: "ai-send-summary", id: "ai-send-summary", text: "Aucun destinataire sélectionné." }),
      el("button", { type: "button", class: "ai-btn ai-btn-ghost", onclick: closeSendMailModal }, [
        el("span", { text: "Annuler" })
      ]),
      el("button", { type: "button", class: "ai-btn ai-btn-primary", id: "ai-send-confirm" }, [
        el("span", { class: "material-symbols-outlined" }, ["send"]),
        el("span", { text: "Envoyer" })
      ])
    ]);

    modal.appendChild(header);
    modal.appendChild(search);
    modal.appendChild(lists);
    modal.appendChild(footer);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    overlay.querySelector("#ai-send-search-input").addEventListener("input", function (e) {
      sendMailState.query = (e.target.value || "").toLowerCase();
      renderRecipients();
    });
    overlay.querySelector("#ai-send-confirm").addEventListener("click", confirmSend);

    sendMailEl = overlay;
    return overlay;
  }

  function loadRecipients() {
    if (state.recipients) {
      renderRecipients();
      return;
    }
    apiGetJson("/api/pcorg/summary/recipients").then(function (res) {
      if (!res || !res.ok) {
        var list = sendMailEl.querySelector("#ai-send-users-list");
        clearChildren(list);
        list.appendChild(el("div", { class: "ai-send-error", text: "Impossible de charger les destinataires : " + ((res && res.error) || "erreur") }));
        return;
      }
      state.recipients = { users: res.users || [], groups: res.groups || [] };
      renderRecipients();
    });
  }

  function renderRecipients() {
    if (!sendMailEl || !state.recipients) return;
    var groupsHost = sendMailEl.querySelector("#ai-send-groups-list");
    var usersHost = sendMailEl.querySelector("#ai-send-users-list");
    clearChildren(groupsHost);
    clearChildren(usersHost);
    var q = sendMailState.query;

    var groups = state.recipients.groups.filter(function (g) {
      return !q || g.name.toLowerCase().indexOf(q) !== -1;
    });
    if (!groups.length) {
      groupsHost.appendChild(el("div", { class: "ai-send-empty", text: "Aucun groupe." }));
    } else {
      groups.forEach(function (g) {
        var checked = !!sendMailState.selectedGroups[g.id];
        var row = el("label", { class: "ai-send-row" + (checked ? " is-checked" : "") }, [
          el("input", { type: "checkbox", "data-id": g.id, "data-kind": "group" }),
          el("div", { class: "ai-send-row-text" }, [
            el("div", { class: "ai-send-row-name" }, [
              el("span", { class: "material-symbols-outlined ai-send-row-icon" }, ["groups"]),
              el("span", { text: g.name })
            ]),
            el("div", { class: "ai-send-row-meta", text: g.member_count + " membre(s)" })
          ])
        ]);
        var cb = row.querySelector("input");
        cb.checked = checked;
        cb.addEventListener("change", function () {
          if (cb.checked) sendMailState.selectedGroups[g.id] = g;
          else delete sendMailState.selectedGroups[g.id];
          row.classList.toggle("is-checked", cb.checked);
          updateSendSummary();
        });
        groupsHost.appendChild(row);
      });
    }

    var users = state.recipients.users.filter(function (u) {
      if (!q) return true;
      return (u.name + " " + u.email + " " + (u.service || "")).toLowerCase().indexOf(q) !== -1;
    });
    if (!users.length) {
      usersHost.appendChild(el("div", { class: "ai-send-empty", text: "Aucun utilisateur." }));
    } else {
      users.forEach(function (u) {
        var checked = !!sendMailState.selectedUsers[u.id];
        var row = el("label", { class: "ai-send-row" + (checked ? " is-checked" : "") }, [
          el("input", { type: "checkbox", "data-id": u.id, "data-kind": "user" }),
          el("div", { class: "ai-send-row-text" }, [
            el("div", { class: "ai-send-row-name" }, [
              el("span", { class: "material-symbols-outlined ai-send-row-icon" }, ["person"]),
              el("span", { text: u.name })
            ]),
            el("div", { class: "ai-send-row-meta", text: u.email + (u.service ? " · " + u.service : "") + " · " + u.role })
          ])
        ]);
        var cb = row.querySelector("input");
        cb.checked = checked;
        cb.addEventListener("change", function () {
          if (cb.checked) sendMailState.selectedUsers[u.id] = u;
          else delete sendMailState.selectedUsers[u.id];
          row.classList.toggle("is-checked", cb.checked);
          updateSendSummary();
        });
        usersHost.appendChild(row);
      });
    }
    updateSendSummary();
  }

  function updateSendSummary() {
    if (!sendMailEl) return;
    var nU = Object.keys(sendMailState.selectedUsers).length;
    var nG = Object.keys(sendMailState.selectedGroups).length;
    var sum = sendMailEl.querySelector("#ai-send-summary");
    var btn = sendMailEl.querySelector("#ai-send-confirm");
    if (!nU && !nG) {
      sum.textContent = "Aucun destinataire sélectionné.";
      btn.disabled = true;
    } else {
      sum.textContent = nG + " groupe(s), " + nU + " utilisateur(s) sélectionné(s).";
      btn.disabled = sendMailState.busy;
    }
  }

  function confirmSend() {
    if (sendMailState.busy) return;
    if (!state.current || !state.current.id) return;
    var userIds = Object.keys(sendMailState.selectedUsers);
    var groupIds = Object.keys(sendMailState.selectedGroups);
    if (!userIds.length && !groupIds.length) return;

    sendMailState.busy = true;
    var btn = sendMailEl.querySelector("#ai-send-confirm");
    btn.disabled = true;
    var sum = sendMailEl.querySelector("#ai-send-summary");
    sum.textContent = "Envoi en cours...";

    apiPostJson("/api/pcorg/summary/" + encodeURIComponent(state.current.id) + "/send", {
      user_ids: userIds,
      group_ids: groupIds
    }).then(function (res) {
      sendMailState.busy = false;
      if (!res || !res.ok) {
        sum.textContent = "Échec : " + ((res && res.error) || "erreur inconnue");
        btn.disabled = false;
        toast("Envoi mail échoué : " + ((res && res.error) || "erreur"), "error");
        return;
      }
      toast("Rapport envoyé à " + (res.sent_count || 0) + " destinataire(s).", "success");
      closeSendMailModal();
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
    setControlsCollapsed(false);
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
