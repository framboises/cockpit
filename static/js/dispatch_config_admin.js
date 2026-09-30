/**
 * dispatch_config_admin.js - Configuration du dispatch automatique des fiches
 * (page Field dispatch, section "Dispatch automatique").
 *
 * Backend : dispatch_auto.py
 *   GET  /api/dispatch/config      -> {ok, categories:{cat:{mode, levels, timeout_s,
 *                                      max_attempts, self_close, managers[]}},
 *                                      updated_at, updated_by, urgency_levels, modes}
 *   PUT  /api/dispatch/config      -> meme forme
 *   GET  /api/dispatch/users?q=    -> {ok, users:[{email, name, service}]}
 */
(function () {
  "use strict";

  var CATEGORIES = [
    { id: "PCO.Secours", label: "Secours" },
    { id: "PCO.Securite", label: "Securite" },
    { id: "PCO.Technique", label: "Technique" },
    { id: "PCO.Flux", label: "Flux" },
    { id: "PCO.Fourriere", label: "Fourriere" },
    { id: "PCO.Information", label: "Information" },
    { id: "PCO.MainCourante", label: "Main courante" },
  ];
  var MODE_LABELS = { never: "Jamais", always: "Toujours", urgency: "Selon l'urgence" };
  var DEFAULT_LEVELS = [
    { code: "IMP", level: 1 }, { code: "UR", level: 2 },
    { code: "UA", level: 3 }, { code: "EU", level: 4 },
  ];

  var state = {
    cfg: null,          // {cat: {...}} en cours d'edition
    levels: DEFAULT_LEVELS,
    modes: ["never", "always", "urgency"],
    names: {},          // email -> {name, service}
    saving: false,
  };

  function $(s, r) { return (r || document).querySelector(s); }

  function toast(type, msg) {
    if (typeof window.showToast === "function") window.showToast(type, msg);
    else console.log("[toast]", type, msg);
  }

  function headers() {
    var h = { "Content-Type": "application/json" };
    var m = $('meta[name="csrf-token"]');
    if (m) h["X-CSRFToken"] = m.getAttribute("content");
    return h;
  }

  function fetchJson(url, opts) {
    return fetch(url, opts || { credentials: "same-origin" }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        return { status: r.status, body: j || {} };
      });
    });
  }

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "className") e.className = attrs[k];
        else if (k === "style") e.style.cssText = attrs[k];
        else e.setAttribute(k, attrs[k]);
      });
    }
    if (text != null) e.textContent = text;
    return e;
  }

  function fmtDate(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso);
      return d.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
    } catch (e) { return String(iso); }
  }

  function cloneCfg(cats) {
    var out = {};
    CATEGORIES.forEach(function (c) {
      var src = (cats && cats[c.id]) || {};
      out[c.id] = {
        mode: src.mode || "never",
        levels: Array.isArray(src.levels) ? src.levels.slice() : [],
        timeout_s: src.timeout_s != null ? src.timeout_s : 60,
        max_attempts: src.max_attempts != null ? src.max_attempts : 3,
        self_close: !!src.self_close,
        managers: Array.isArray(src.managers) ? src.managers.slice() : [],
      };
    });
    return out;
  }

  // ------------------------------------------------------------------
  // Chargement
  // ------------------------------------------------------------------
  function load() {
    var list = $("#dispatch-cfg-list");
    if (!list) return;
    fetchJson("/api/dispatch/config?_=" + Date.now()).then(function (res) {
      var b = res.body;
      if (!b.ok) {
        list.textContent = "";
        list.appendChild(el("div", { style: "color:#dc2626; padding:12px;" },
          "Configuration indisponible (" + (b.error || res.status) + ")."));
        return;
      }
      applyServer(b);
      resolveManagerNames();
    }).catch(function () {
      list.textContent = "";
      list.appendChild(el("div", { style: "color:#dc2626; padding:12px;" }, "Erreur reseau."));
    });
  }

  function applyServer(b) {
    if (Array.isArray(b.urgency_levels) && b.urgency_levels.length) state.levels = b.urgency_levels;
    if (Array.isArray(b.modes) && b.modes.length) state.modes = b.modes;
    state.cfg = cloneCfg(b.categories);
    renderMeta(b);
    render();
  }

  function renderMeta(b) {
    var meta = $("#dispatch-cfg-meta");
    if (!meta) return;
    meta.textContent = b.updated_at
      ? "Modifie le " + fmtDate(b.updated_at) + (b.updated_by ? " par " + b.updated_by : "")
      : "Configuration par defaut (jamais enregistree)";
  }

  // Noms des responsables deja configures : une recherche par email inconnu.
  function resolveManagerNames() {
    if (!state.cfg) return;
    var todo = [];
    Object.keys(state.cfg).forEach(function (cat) {
      state.cfg[cat].managers.forEach(function (m) {
        if (!state.names[m] && todo.indexOf(m) < 0) todo.push(m);
      });
    });
    if (!todo.length) return;
    Promise.all(todo.map(function (email) {
      return fetchJson("/api/dispatch/users?q=" + encodeURIComponent(email))
        .then(function (res) { rememberUsers(res.body.users); })
        .catch(function () {});
    })).then(render);
  }

  function rememberUsers(users) {
    (users || []).forEach(function (u) {
      if (u && u.email) state.names[u.email.toLowerCase()] = { name: u.name || "", service: u.service || "" };
    });
  }

  // ------------------------------------------------------------------
  // Rendu
  // ------------------------------------------------------------------
  function render() {
    var list = $("#dispatch-cfg-list");
    if (!list || !state.cfg) return;
    list.textContent = "";
    CATEGORIES.forEach(function (c) { list.appendChild(renderCard(c)); });
  }

  function renderCard(c) {
    var cfg = state.cfg[c.id];
    var card = el("div", { className: "dcfg-card" });
    card.setAttribute("data-cat", c.id);

    // En-tete : nom + mode + niveaux
    var head = el("div", { className: "dcfg-card-head" });
    var title = el("div", { className: "dcfg-card-title" }, c.label);
    title.appendChild(el("div", { className: "dcfg-card-code" }, c.id));
    head.appendChild(title);

    var modeSel = el("select", { className: "form-input", style: "width:auto; font-size:12px; padding:4px 6px;" });
    modeSel.setAttribute("aria-label", "Mode de dispatch " + c.label);
    state.modes.forEach(function (m) {
      var o = el("option", null, MODE_LABELS[m] || m);
      o.value = m;
      modeSel.appendChild(o);
    });
    modeSel.value = cfg.mode;
    head.appendChild(modeSel);

    var levelsBox = el("div", { className: "dcfg-levels" });
    state.levels.forEach(function (lv) {
      var lab = el("label");
      var cb = el("input", { type: "checkbox" });
      cb.checked = cfg.levels.indexOf(lv.code) >= 0;
      cb.addEventListener("change", function () {
        var i = cfg.levels.indexOf(lv.code);
        if (cb.checked && i < 0) cfg.levels.push(lv.code);
        if (!cb.checked && i >= 0) cfg.levels.splice(i, 1);
      });
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(lv.level + " - " + lv.code));
      levelsBox.appendChild(lab);
    });
    levelsBox.style.display = cfg.mode === "urgency" ? "flex" : "none";
    head.appendChild(levelsBox);

    modeSel.addEventListener("change", function () {
      cfg.mode = modeSel.value;
      levelsBox.style.display = cfg.mode === "urgency" ? "flex" : "none";
    });
    card.appendChild(head);

    // Parametres numeriques + cloture
    var grid = el("div", { className: "dcfg-grid" });
    grid.appendChild(numberField("Delai de reponse (s)", cfg, "timeout_s", 10, 600));
    grid.appendChild(numberField("Nombre de propositions avant file", cfg, "max_attempts", 1, 10));
    var closeLab = el("label", { className: "dcfg-check", style: "align-self:end;" });
    var closeCb = el("input", { type: "checkbox" });
    closeCb.checked = cfg.self_close;
    closeCb.addEventListener("change", function () { cfg.self_close = closeCb.checked; });
    closeLab.appendChild(closeCb);
    closeLab.appendChild(document.createTextNode("L'unite peut clore la fiche (compte-rendu obligatoire)"));
    grid.appendChild(closeLab);
    card.appendChild(grid);

    card.appendChild(renderManagers(c, cfg));
    return card;
  }

  function numberField(label, cfg, key, min, max) {
    var lab = el("label", null, label);
    var inp = el("input", { type: "number", min: String(min), max: String(max), step: "1", className: "form-input" });
    inp.value = String(cfg[key]);
    inp.addEventListener("change", function () {
      var v = parseInt(inp.value, 10);
      if (isNaN(v)) v = cfg[key];
      v = Math.max(min, Math.min(max, v));
      cfg[key] = v;
      inp.value = String(v);
    });
    lab.appendChild(inp);
    return lab;
  }

  function renderManagers(c, cfg) {
    var wrap = el("div", { className: "dcfg-managers" });
    wrap.appendChild(el("div", { style: "color:var(--muted);" }, "Responsables de service"));

    var chips = el("div", { className: "dcfg-chips" });
    function drawChips() {
      chips.textContent = "";
      if (!cfg.managers.length) {
        chips.appendChild(el("span", { style: "color:var(--muted); font-style:italic;" }, "Aucun responsable"));
        return;
      }
      cfg.managers.forEach(function (email) {
        var info = state.names[email];
        var chip = el("span", { className: "dcfg-chip" });
        chip.title = email + (info && info.service ? " - " + info.service : "");
        if (info && info.name) {
          chip.appendChild(document.createTextNode(info.name + " "));
          chip.appendChild(el("small", null, email));
        } else {
          chip.appendChild(document.createTextNode(email));
        }
        var x = el("button", { type: "button", "aria-label": "Retirer " + email }, "×");
        x.addEventListener("click", function () {
          var i = cfg.managers.indexOf(email);
          if (i >= 0) cfg.managers.splice(i, 1);
          drawChips();
        });
        chip.appendChild(x);
        chips.appendChild(chip);
      });
    }
    drawChips();
    wrap.appendChild(chips);

    wrap.appendChild(buildAutocomplete(c, function (u) {
      var email = (u.email || "").toLowerCase();
      if (!email) return;
      state.names[email] = { name: u.name || "", service: u.service || "" };
      if (cfg.managers.indexOf(email) < 0) cfg.managers.push(email);
      drawChips();
    }));
    return wrap;
  }

  function buildAutocomplete(c, onPick) {
    var box = el("div", { className: "dcfg-ac" });
    var inp = el("input", {
      type: "text", className: "form-input", autocomplete: "off",
      placeholder: "Ajouter un responsable (nom ou email)...",
      style: "font-size:12px; padding:4px 6px;",
    });
    inp.setAttribute("aria-label", "Ajouter un responsable " + c.label);
    var menu = el("div", { className: "dcfg-ac-list" });
    menu.hidden = true;
    box.appendChild(inp);
    box.appendChild(menu);

    var timer = null;
    var seq = 0;
    var items = [];
    var active = -1;

    function close() { menu.hidden = true; active = -1; }

    function pick(u) {
      onPick(u);
      inp.value = "";
      close();
      inp.focus();
    }

    function highlight() {
      Array.prototype.forEach.call(menu.children, function (n, i) {
        n.classList.toggle("is-active", i === active);
      });
    }

    function draw(users) {
      items = users;
      active = -1;
      menu.textContent = "";
      if (!users.length) {
        menu.appendChild(el("div", { className: "dcfg-ac-item", style: "color:var(--muted); cursor:default;" }, "Aucun utilisateur"));
        menu.hidden = false;
        return;
      }
      users.forEach(function (u) {
        var it = el("div", { className: "dcfg-ac-item" });
        it.appendChild(document.createTextNode(u.name || u.email));
        it.appendChild(el("small", null, u.email + (u.service ? " - " + u.service : "")));
        it.addEventListener("mousedown", function (e) { e.preventDefault(); pick(u); });
        menu.appendChild(it);
      });
      menu.hidden = false;
    }

    function search() {
      var q = inp.value.trim();
      if (q.length < 2) { close(); return; }
      var my = ++seq;
      fetchJson("/api/dispatch/users?q=" + encodeURIComponent(q)).then(function (res) {
        if (my !== seq) return;
        var users = (res.body && res.body.users) || [];
        rememberUsers(users);
        draw(users);
      }).catch(function () { if (my === seq) close(); });
    }

    inp.addEventListener("input", function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(search, 250);
    });
    inp.addEventListener("keydown", function (e) {
      if (menu.hidden) {
        if (e.key === "Enter") e.preventDefault();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (items.length) { active = (active + 1) % items.length; highlight(); }
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (items.length) { active = (active - 1 + items.length) % items.length; highlight(); }
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (active >= 0 && items[active]) pick(items[active]);
        else if (items.length === 1) pick(items[0]);
      } else if (e.key === "Escape") {
        close();
      }
    });
    inp.addEventListener("blur", function () { setTimeout(close, 150); });
    return box;
  }

  // ------------------------------------------------------------------
  // Enregistrement
  // ------------------------------------------------------------------
  function save() {
    if (!state.cfg || state.saving) return;
    var missing = CATEGORIES.filter(function (c) {
      var cfg = state.cfg[c.id];
      return cfg.mode === "urgency" && !cfg.levels.length;
    });
    if (missing.length) {
      toast("warning", "Mode 'Selon l'urgence' sans niveau coche : " +
        missing.map(function (c) { return c.label; }).join(", ") + ". Aucune fiche ne sera proposee.");
    }
    var btn = $("#dispatch-cfg-save");
    state.saving = true;
    if (btn) btn.disabled = true;
    fetchJson("/api/dispatch/config", {
      method: "PUT",
      credentials: "same-origin",
      headers: headers(),
      body: JSON.stringify({ categories: state.cfg }),
    }).then(function (res) {
      if (res.body && res.body.ok) {
        applyServer(res.body);
        toast("success", "Configuration du dispatch enregistree");
      } else {
        var err = (res.body && (res.body.error || res.body.code)) || ("HTTP " + res.status);
        toast("error", "Enregistrement impossible : " + err);
      }
    }).catch(function () {
      toast("error", "Erreur reseau");
    }).then(function () {
      state.saving = false;
      if (btn) btn.disabled = false;
    });
  }

  function init() {
    if (!$("#dispatch-cfg-list")) return;
    var saveBtn = $("#dispatch-cfg-save");
    if (saveBtn) saveBtn.addEventListener("click", save);
    var reloadBtn = $("#dispatch-cfg-reload");
    if (reloadBtn) reloadBtn.addEventListener("click", function () {
      load();
      toast("info", "Modifications annulees");
    });
    load();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.DispatchConfigAdmin = { reload: load };
})();
