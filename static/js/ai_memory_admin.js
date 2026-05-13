/* Memoire Assistant IA -- page /edit
 * Gestion des directives constitutionnelles + stats apprentissage.
 */
(function () {
  "use strict";

  var ROOT = document.getElementById("ai-memory-body");
  if (!ROOT) return;

  var TBODY = document.querySelector("#ai-memory-table tbody");
  var FILTER_EVENT = document.getElementById("ai-memory-filter-event");
  var FILTER_SECTION = document.getElementById("ai-memory-filter-section");
  var FILTER_TYPE = document.getElementById("ai-memory-filter-type");
  var FILTER_ACTIVE = document.getElementById("ai-memory-filter-active");

  var SECTION_LABELS = {
    synthese: "Synthèse", faits_marquants: "Faits marquants",
    secours: "Secours", securite: "Sécurité", technique: "Technique",
    flux: "Flux", fourriere: "Fourrière", recommandations: "Recommandations",
    prochaines_24h: "Prochaines 24h"
  };

  function csrfHeader() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") || "" : "";
  }
  function apiGet(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) { return r.json(); });
  }
  function apiSend(method, url, body) {
    var headers = { "Content-Type": "application/json", "X-CSRFToken": csrfHeader() };
    var opts = { method: method, credentials: "same-origin", headers: headers };
    if (body) opts.body = JSON.stringify(body);
    return fetch(url, opts).then(function (r) { return r.json(); });
  }
  function toast(msg, kind) {
    if (window.showToast) window.showToast(kind || "info", msg);
    else try { console.log("[ai-memory]", kind, msg); } catch (e) {}
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "class") node.className = attrs[k];
      else if (k === "text") node.textContent = attrs[k];
      else if (k.indexOf("on") === 0 && typeof attrs[k] === "function") node.addEventListener(k.slice(2), attrs[k]);
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      if (c == null) return;
      if (typeof c === "string") node.appendChild(document.createTextNode(c));
      else node.appendChild(c);
    });
    return node;
  }
  function clearChildren(node) { if (!node) return; while (node.firstChild) node.removeChild(node.firstChild); }

  // ----- Stats -----

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = (value == null ? "—" : value);
  }

  function refreshStats() {
    Promise.all([
      apiGet("/api/pcorg/ai-memory/stats"),
      apiGet("/api/pcorg/summary/export-stats")
    ]).then(function (results) {
      var mem = results[0] || {};
      var exp = results[1] || {};
      setText("ai-memory-stat-active", mem.active);
      setText("ai-memory-stat-inactive", mem.inactive);
      setText("ai-memory-stat-sft", exp.estimated_sft_samples);
      setText("ai-memory-stat-dpo", exp.estimated_dpo_samples);
      setText("ai-memory-stat-corrections", exp.with_corrections);
      // Validations par section (le clic 👍 sur une carte).
      setText("ai-memory-stat-validations-good", exp.validations_good);
      setText("ai-memory-stat-validations-bad", exp.validations_bad);
      setText("ai-memory-stat-rules-promoted", exp.rules_promoted);
      setText("ai-memory-stat-feedback", exp.feedback_entries_total);
      // Sous-libellé : nb de rapports distincts qui ont au moins une validation
      var subEl = document.getElementById("ai-memory-stat-validations-good-sub");
      if (subEl) {
        if (exp.summaries_with_section_validation != null) {
          subEl.textContent = "sur " + exp.summaries_with_section_validation + " rapport(s)";
        } else {
          subEl.textContent = "";
        }
      }
    });
  }

  // ----- Filter options : peuple le dropdown d'événements depuis les directives existantes -----

  function refreshEventFilter(items) {
    var seen = {};
    var events = [];
    items.forEach(function (it) {
      var ev = (it.scope || {}).event;
      if (ev && !seen[ev]) { seen[ev] = true; events.push(ev); }
    });
    events.sort();
    var current = FILTER_EVENT.value;
    clearChildren(FILTER_EVENT);
    FILTER_EVENT.appendChild(el("option", { value: "" }, ["— Tous —"]));
    events.forEach(function (ev) {
      FILTER_EVENT.appendChild(el("option", { value: ev }, [ev]));
    });
    if (current && seen[current]) FILTER_EVENT.value = current;
  }

  // ----- Liste -----

  function fetchList() {
    var params = [];
    if (FILTER_EVENT.value) params.push("event=" + encodeURIComponent(FILTER_EVENT.value));
    if (FILTER_SECTION.value) params.push("section=" + encodeURIComponent(FILTER_SECTION.value));
    if (FILTER_TYPE.value) params.push("type=" + encodeURIComponent(FILTER_TYPE.value));
    if (FILTER_ACTIVE.checked) params.push("active_only=1");
    var url = "/api/pcorg/ai-memory" + (params.length ? "?" + params.join("&") : "");
    return apiGet(url);
  }

  function render(items) {
    clearChildren(TBODY);
    if (!items.length) {
      var tr = el("tr", null, [
        el("td", { colspan: "7", class: "ai-memory-empty", text: "Aucune directive. Ajoute des règles depuis la modale d'un rapport ou clique sur 'Nouvelle directive'." })
      ]);
      TBODY.appendChild(tr);
      return;
    }
    items.forEach(function (it) {
      var scope = it.scope || {};
      var scopeParts = [];
      if (scope.event) scopeParts.push(scope.event);
      if (scope.section) scopeParts.push(SECTION_LABELS[scope.section] || scope.section);
      if (scope.phase) scopeParts.push(scope.phase);
      if (scope.year) scopeParts.push(scope.year);
      var scopeStr = scopeParts.length ? scopeParts.join(" / ") : "Global";

      var typeBadge = el("span", { class: "ai-memory-type-badge ai-memory-type-" + (it.type || "principe"), text: it.type || "principe" });

      var contentCell = el("td", { class: "ai-memory-content-cell" });
      var contentSpan = el("span", { class: "ai-memory-content-text", text: it.content });
      contentCell.appendChild(contentSpan);
      if ((it.source || {}).summary_id) {
        contentCell.appendChild(el("div", { class: "ai-memory-source", text: "↳ promue depuis un rapport" }));
      }

      var activeCb = el("input", { type: "checkbox" });
      activeCb.checked = !!it.active;
      activeCb.addEventListener("change", function () {
        var was = it.active;
        activeCb.disabled = true;
        apiSend("PUT", "/api/pcorg/ai-memory/" + encodeURIComponent(it.id), { active: activeCb.checked }).then(function (res) {
          activeCb.disabled = false;
          if (!res || !res.ok) { activeCb.checked = was; toast((res && res.error) || "Erreur", "error"); return; }
          it.active = !!res.item.active;
          toast(it.active ? "Directive activée" : "Directive archivée", "info");
          refreshStats();
        });
      });

      var btnEdit = el("button", { type: "button", class: "btn-ghost btn-icon", title: "Modifier" }, [
        el("span", { class: "material-symbols-outlined" }, ["edit"])
      ]);
      btnEdit.addEventListener("click", function () { openEditModal(it); });

      var btnDel = el("button", { type: "button", class: "btn-ghost btn-icon ai-memory-btn-delete", title: "Supprimer (définitif, admin)" }, [
        el("span", { class: "material-symbols-outlined" }, ["delete"])
      ]);
      btnDel.addEventListener("click", function () {
        if (!confirm("Supprimer définitivement cette directive ? (préférer la désactivation)")) return;
        apiSend("DELETE", "/api/pcorg/ai-memory/" + encodeURIComponent(it.id)).then(function (res) {
          if (!res || !res.ok) { toast((res && res.error) || "Erreur", "error"); return; }
          toast("Directive supprimée", "info");
          load();
        });
      });

      var actions = el("td", { class: "ai-memory-actions-cell" }, [btnEdit, btnDel]);
      var tr = el("tr", null, [
        el("td", null, [typeBadge]),
        el("td", { class: "ai-memory-scope-cell", text: scopeStr }),
        contentCell,
        el("td", { class: "ai-memory-used-cell", text: String(it.used_count || 0) }),
        el("td", { class: "ai-memory-author-cell", text: (it.created_by_name || it.created_by || "—") }),
        el("td", { class: "ai-memory-active-cell" }, [activeCb]),
        actions
      ]);
      TBODY.appendChild(tr);
    });
  }

  function load() {
    fetchList().then(function (res) {
      if (!res || !res.ok) { toast((res && res.error) || "Erreur de chargement", "error"); return; }
      refreshEventFilter(res.items || []);
      render(res.items || []);
      refreshStats();
    });
  }

  [FILTER_EVENT, FILTER_SECTION, FILTER_TYPE, FILTER_ACTIVE].forEach(function (e) {
    e && e.addEventListener("change", load);
  });

  // ----- Création / modification : modale -----

  var modalEl = null;

  function buildModal() {
    if (modalEl) return modalEl;
    var overlay = el("div", { class: "ai-modal-overlay ai-memory-modal-overlay", "aria-hidden": "true" });
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    var modal = el("div", { class: "ai-modal ai-memory-modal", role: "dialog", "aria-modal": "true" });
    var header = el("div", { class: "ai-modal-header" }, [
      el("span", { class: "material-symbols-outlined ai-modal-icon" }, ["psychology"]),
      el("div", { class: "ai-modal-titles" }, [
        el("h2", { class: "ai-modal-title ai-memory-modal-title", text: "Nouvelle directive" }),
        el("div", { class: "ai-modal-subtitle", text: "Une directive est une consigne durable injectée dans le system prompt de l'IA pour les futurs rapports." })
      ]),
      el("button", { type: "button", class: "ai-modal-close", title: "Fermer", onclick: close }, [
        el("span", { class: "material-symbols-outlined" }, ["close"])
      ])
    ]);
    var formGrid = el("div", { class: "ai-memory-form-grid" }, [
      el("label", { class: "ai-memory-form-row" }, [
        el("span", { class: "ai-memory-form-label", text: "Type" }),
        (function () {
          var s = el("select", { id: "ai-memory-modal-type" });
          ["principe", "correction", "vocabulaire", "contexte"].forEach(function (t) {
            s.appendChild(el("option", { value: t }, [t]));
          });
          return s;
        })()
      ]),
      el("label", { class: "ai-memory-form-row" }, [
        el("span", { class: "ai-memory-form-label", text: "Événement (laisse vide pour global)" }),
        el("input", { type: "text", id: "ai-memory-modal-event", placeholder: "Ex: 24H AUTOS" })
      ]),
      el("label", { class: "ai-memory-form-row" }, [
        el("span", { class: "ai-memory-form-label", text: "Section ciblée" }),
        (function () {
          var s = el("select", { id: "ai-memory-modal-section" });
          s.appendChild(el("option", { value: "" }, ["— Toutes —"]));
          Object.keys(SECTION_LABELS).forEach(function (k) {
            s.appendChild(el("option", { value: k }, [SECTION_LABELS[k]]));
          });
          return s;
        })()
      ]),
      el("label", { class: "ai-memory-form-row" }, [
        el("span", { class: "ai-memory-form-label", text: "Phase" }),
        (function () {
          var s = el("select", { id: "ai-memory-modal-phase" });
          s.appendChild(el("option", { value: "" }, ["— Toutes —"]));
          ["montage", "course", "demontage"].forEach(function (p) {
            s.appendChild(el("option", { value: p }, [p]));
          });
          return s;
        })()
      ]),
      el("label", { class: "ai-memory-form-row ai-memory-form-row-wide" }, [
        el("span", { class: "ai-memory-form-label", text: "Contenu (concis, formulation directive)" }),
        el("textarea", { id: "ai-memory-modal-content", rows: "5",
                         placeholder: "Ex: Tribune Mulsanne — toujours préciser le secteur (T1 à T5) car les renforts sont mobilisables par secteur." })
      ]),
      el("label", { class: "ai-memory-form-row ai-memory-form-row-check" }, [
        el("input", { type: "checkbox", id: "ai-memory-modal-active", checked: "checked" }),
        el("span", { text: "Active (réinjectée dans les rapports)" })
      ])
    ]);
    var footer = el("div", { class: "ai-modal-footer" }, [
      el("button", { type: "button", class: "ai-btn ai-btn-ghost", onclick: close }, [el("span", { text: "Annuler" })]),
      el("button", { type: "button", class: "ai-btn ai-btn-primary", id: "ai-memory-modal-save" }, [
        el("span", { class: "material-symbols-outlined" }, ["save"]),
        el("span", { text: "Enregistrer" })
      ])
    ]);
    modal.appendChild(header);
    modal.appendChild(formGrid);
    modal.appendChild(footer);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    overlay.querySelector("#ai-memory-modal-save").addEventListener("click", submit);
    modalEl = overlay;
    return overlay;
  }

  function open(directive) {
    buildModal();
    modalEl.dataset.editId = directive ? directive.id : "";
    modalEl.querySelector(".ai-memory-modal-title").textContent = directive ? "Modifier la directive" : "Nouvelle directive";
    modalEl.querySelector("#ai-memory-modal-type").value = (directive && directive.type) || "principe";
    var scope = (directive && directive.scope) || {};
    modalEl.querySelector("#ai-memory-modal-event").value = scope.event || "";
    modalEl.querySelector("#ai-memory-modal-section").value = scope.section || "";
    modalEl.querySelector("#ai-memory-modal-phase").value = scope.phase || "";
    modalEl.querySelector("#ai-memory-modal-content").value = (directive && directive.content) || "";
    modalEl.querySelector("#ai-memory-modal-active").checked = directive ? !!directive.active : true;
    modalEl.classList.add("is-open");
    modalEl.setAttribute("aria-hidden", "false");
    setTimeout(function () { modalEl.querySelector("#ai-memory-modal-content").focus(); }, 50);
  }
  function close() {
    if (!modalEl) return;
    modalEl.classList.remove("is-open");
    modalEl.setAttribute("aria-hidden", "true");
  }
  function openEditModal(directive) { open(directive); }

  function submit() {
    var editId = modalEl.dataset.editId;
    var payload = {
      type: modalEl.querySelector("#ai-memory-modal-type").value,
      scope: {
        event: modalEl.querySelector("#ai-memory-modal-event").value || null,
        section: modalEl.querySelector("#ai-memory-modal-section").value || null,
        phase: modalEl.querySelector("#ai-memory-modal-phase").value || null
      },
      content: modalEl.querySelector("#ai-memory-modal-content").value,
      active: modalEl.querySelector("#ai-memory-modal-active").checked
    };
    if (!payload.content || !payload.content.trim()) { toast("Contenu requis", "error"); return; }
    var btn = modalEl.querySelector("#ai-memory-modal-save");
    btn.disabled = true;
    var p = editId
      ? apiSend("PUT", "/api/pcorg/ai-memory/" + encodeURIComponent(editId), payload)
      : apiSend("POST", "/api/pcorg/ai-memory", payload);
    p.then(function (res) {
      btn.disabled = false;
      if (!res || !res.ok) { toast((res && res.error) || "Erreur", "error"); return; }
      toast(editId ? "Directive modifiée" : "Directive créée", "success");
      close();
      load();
    });
  }

  document.getElementById("ai-memory-btn-new").addEventListener("click", function () { open(null); });

  var refreshBtn = document.getElementById("ai-memory-refresh");
  if (refreshBtn) refreshBtn.addEventListener("click", function () {
    refreshBtn.classList.add("is-loading");
    var done = function () { setTimeout(function () { refreshBtn.classList.remove("is-loading"); }, 400); };
    Promise.all([fetchList().then(function (res) {
      if (res && res.ok) { refreshEventFilter(res.items || []); render(res.items || []); }
    }), refreshStats()]).then(done, done);
  });

  document.getElementById("ai-memory-btn-export-sft").addEventListener("click", function () {
    var ok = confirm("Télécharger le dataset SFT (JSONL) ?\n\nInclut tous les rapports avec prompts persistés. Conseillé : ne lancer le fine-tuning qu'à partir de 500+ samples.");
    if (!ok) return;
    window.location.href = "/api/pcorg/summary/export-dataset?format=sft";
  });
  document.getElementById("ai-memory-btn-export-dpo").addEventListener("click", function () {
    var ok = confirm("Télécharger le dataset DPO (JSONL) ?\n\nN'inclut que les rapports avec au moins une correction (paires chosen/rejected).");
    if (!ok) return;
    window.location.href = "/api/pcorg/summary/export-dataset?format=dpo";
  });

  load();
})();
