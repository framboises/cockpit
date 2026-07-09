/* Wiki des procédures — back-office admin.
   CRUD des fiches + catégories, éditeur de logigramme avec aperçu temps réel.
   Réutilise window.Wiki.flowHtml pour le rendu de l'aperçu.  */
(function () {
  "use strict";

  var KINDS = [
    { k: "start", label: "Départ" },
    { k: "act", label: "Action" },
    { k: "watch", label: "Levée de doute" },
    { k: "engage", label: "Engager un acteur" },
    { k: "ask", label: "Décision (Oui / Non)" },
    { k: "end", label: "Clôture" }
  ];

  // ---------- utils DOM ----------
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "class") e.className = attrs[k];
      else if (k === "html") e.innerHTML = attrs[k];
      else if (k === "text") e.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) {
      if (c == null) return;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return e;
  }
  function icon(name, size) { return el("span", { class: "material-symbols-outlined", html: name, style: "font-size:" + (size || 18) + "px;" }); }
  function iconBtn(name, onClick, danger) {
    var b = el("button", { type: "button", class: "wk-iconbtn" + (danger ? " wk-danger" : ""), onclick: onClick });
    b.appendChild(icon(name));
    return b;
  }

  function fmtDate(v) {
    if (!v) return "—";
    var d = new Date(v);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric" }) +
      " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }

  // ---------- API ----------
  function j(r) { return r.json(); }
  function jsonHeaders() {
    var h = { "Content-Type": "application/json" };
    var m = document.querySelector('meta[name="csrf-token"]');
    if (m) h["X-CSRFToken"] = m.getAttribute("content");
    return h;
  }
  function req(url, method, body) {
    var opt = { method: method || "GET", credentials: "same-origin" };
    if (body) { opt.headers = jsonHeaders(); opt.body = JSON.stringify(body); }
    return fetch(url, opt).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; }); });
  }
  var A = {
    listProc: function () { return fetch("/api/wiki/admin/procedures", { credentials: "same-origin" }).then(j); },
    createProc: function (d) { return req("/api/wiki/admin/procedures", "POST", d); },
    updateProc: function (id, d) { return req("/api/wiki/admin/procedures/" + id, "PUT", d); },
    deleteProc: function (id) { return req("/api/wiki/admin/procedures/" + id, "DELETE"); },
    listCat: function () { return fetch("/api/wiki/admin/categories", { credentials: "same-origin" }).then(j); },
    createCat: function (d) { return req("/api/wiki/admin/categories", "POST", d); },
    updateCat: function (id, d) { return req("/api/wiki/admin/categories/" + id, "PUT", d); },
    deleteCat: function (id) { return req("/api/wiki/admin/categories/" + id, "DELETE"); }
  };
  function toast(type, msg) { if (window.showToast) showToast(type, msg, 3500); else console.log(type, msg); }

  // ---------- état ----------
  var procs = [], cats = [], catByKey = {}, current = null, dirty = false;
  var listEl, editorEl, searchEl;

  // ---------- listes répétables ----------
  function listRow(container, value, placeholder, multiline) {
    var input = el(multiline ? "textarea" : "input", { class: "wk-input" + (multiline ? " wk-textarea" : ""), placeholder: placeholder || "" });
    input.value = value || "";
    input.addEventListener("input", function () { dirty = true; });
    var row = el("div", { class: "wk-le-row" });
    var up = iconBtn("arrow_upward", function () { var p = row.previousElementSibling; if (p) container.insertBefore(row, p); dirty = true; });
    var down = iconBtn("arrow_downward", function () { var n = row.nextElementSibling; if (n) container.insertBefore(n, row); dirty = true; });
    var del = iconBtn("close", function () { row.remove(); dirty = true; }, true);
    row.appendChild(input); row.appendChild(up); row.appendChild(down); row.appendChild(del);
    row._input = input;
    return row;
  }
  function listEditor(items, placeholder, multiline) {
    var box = el("div", { class: "wk-list-editor" });
    (items || []).forEach(function (it) { box.appendChild(listRow(box, it, placeholder, multiline)); });
    var add = el("button", { type: "button", class: "wk-addbtn", onclick: function () { box.insertBefore(listRow(box, "", placeholder, multiline), add); dirty = true; } });
    add.appendChild(icon("add", 16)); add.appendChild(document.createTextNode("Ajouter"));
    box.appendChild(add);
    box._collect = function () { return Array.prototype.map.call(box.querySelectorAll(".wk-le-row"), function (r) { return r._input.value.trim(); }).filter(Boolean); };
    return box;
  }

  // ---------- éditeur de logigramme ----------
  var flowStepsBox = null, flowPreview = null;
  function previewFlow() {
    if (!flowPreview) return;
    var flow = collectFlow();
    flowPreview.innerHTML = '<div class="wk-fp-lbl">Aperçu</div>' + window.Wiki.flowHtml(flow);
  }
  function collectFlow() {
    if (!flowStepsBox) return [];
    return Array.prototype.map.call(flowStepsBox.querySelectorAll(".wk-fe-step"), function (r) { return r._get(); })
      .filter(function (n) { return n.t || n.k; });
  }
  function flowRow(node) {
    node = node || { k: "act", t: "" };
    var row = el("div", { class: "wk-fe-step" });
    var sel = el("select", { class: "wk-select" });
    KINDS.forEach(function (kd) { var o = el("option", { value: kd.k, text: kd.label }); if (kd.k === node.k) o.selected = true; sel.appendChild(o); });
    var txt = el("input", { class: "wk-input", placeholder: "Texte du nœud" }); txt.value = node.t || "";
    var up = iconBtn("arrow_upward", function () { var p = row.previousElementSibling; if (p) flowStepsBox.insertBefore(row, p); dirty = true; previewFlow(); });
    var down = iconBtn("arrow_downward", function () { var n = row.nextElementSibling; if (n) flowStepsBox.insertBefore(n, row); dirty = true; previewFlow(); });
    var del = iconBtn("close", function () { row.remove(); dirty = true; previewFlow(); }, true);
    var top = el("div", { class: "wk-fe-top" }, [sel, txt, up, down, del]);
    var branches = el("div", { class: "wk-fe-branches" });
    var yIn = el("input", { class: "wk-input", placeholder: "Branche OUI → …" }); yIn.value = node.y || "";
    var nIn = el("input", { class: "wk-input", placeholder: "Branche NON → … (vide = poursuivre)" }); nIn.value = (node.n && node.n !== "—") ? node.n : "";
    branches.appendChild(yIn); branches.appendChild(nIn);
    row.appendChild(top); row.appendChild(branches);
    function sync() { branches.style.display = sel.value === "ask" ? "grid" : "none"; }
    sync();
    function changed() { dirty = true; previewFlow(); }
    sel.addEventListener("change", function () { sync(); changed(); });
    txt.addEventListener("input", changed);
    yIn.addEventListener("input", changed);
    nIn.addEventListener("input", changed);
    row._get = function () {
      var o = { k: sel.value, t: txt.value.trim() };
      if (sel.value === "ask") { o.y = yIn.value.trim(); o.n = nIn.value.trim() || "—"; }
      return o;
    };
    return row;
  }
  function flowEditor(flow) {
    flowStepsBox = el("div", { class: "wk-fe-steps" });
    (flow || []).forEach(function (n) { flowStepsBox.appendChild(flowRow(n)); });
    var add = el("button", { type: "button", class: "wk-addbtn", onclick: function () { flowStepsBox.appendChild(flowRow({ k: "act", t: "" })); dirty = true; previewFlow(); } });
    add.appendChild(icon("add", 16)); add.appendChild(document.createTextNode("Ajouter une étape"));
    flowStepsBox.appendChild(add);
    flowPreview = el("div", { class: "wk-fe-preview" });
    var wrap = el("div", { class: "wk-flow-editor" }, [flowStepsBox, flowPreview]);
    setTimeout(previewFlow, 0);
    return wrap;
  }

  // ---------- éditeur de fiche ----------
  var F = {};
  function field(label, control) {
    return el("div", { class: "wk-field" }, [el("label", { text: label }), control]);
  }
  function renderEditor(p) {
    current = p;
    dirty = false;
    editorEl.innerHTML = "";
    F = {};
    var isNew = !p._id;

    F.code = el("input", { class: "wk-input" }); F.code.value = p.code || "";
    F.titre = el("input", { class: "wk-input" }); F.titre.value = p.titre || "";
    F.status = el("select", { class: "wk-select" });
    [["draft", "Brouillon"], ["published", "Publié"]].forEach(function (s) {
      var o = el("option", { value: s[0], text: s[1] }); if ((p.status || "draft") === s[0]) o.selected = true; F.status.appendChild(o);
    });
    var head = el("div", { class: "wk-row2" }, [
      field("Code", F.code), field("Titre", F.titre), field("Statut", F.status)
    ]);

    F.dom = el("select", { class: "wk-select" });
    cats.forEach(function (c) { var o = el("option", { value: c.key, text: c.label }); if (p.dom === c.key) o.selected = true; F.dom.appendChild(o); });

    F.situation = el("textarea", { class: "wk-input wk-textarea" }); F.situation.value = p.situation || "";
    F.acteurs = el("textarea", { class: "wk-input wk-textarea" }); F.acteurs.value = p.acteurs || "";
    F.consigner = el("textarea", { class: "wk-input wk-textarea" }); F.consigner.value = p.consigner || "";
    F.pieges = el("textarea", { class: "wk-input wk-textarea" }); F.pieges.value = p.pieges || "";
    [F.situation, F.acteurs, F.consigner, F.pieges].forEach(function (t) { t.addEventListener("input", function () { dirty = true; }); });

    F.questions = listEditor(p.questions, "Question à poser…");
    F.conduite = listEditor(p.conduite, "Étape de la conduite à tenir…");
    F.souscas = listEditor(p.souscas, "Sous-cas…");
    F.details = listEditor(p.details, "Réflexe terrain…", true);
    var flowUi = flowEditor(p.flow);

    var save = el("button", { class: "wk-btn wk-primary", onclick: doSave });
    save.appendChild(icon("save", 18)); save.appendChild(document.createTextNode(" Enregistrer"));
    var bar = el("div", { class: "wk-editor-bar" }, [save]);
    if (!isNew) {
      var delBtn = el("button", { class: "wk-btn wk-danger", onclick: doDelete });
      delBtn.appendChild(icon("delete", 18)); delBtn.appendChild(document.createTextNode(" Supprimer"));
      bar.appendChild(delBtn);
    }
    bar.appendChild(el("div", { class: "wk-spacer" }));
    if (!isNew) {
      bar.appendChild(el("div", { class: "wk-meta", text: "v" + (p.version || 1) + " · " + (p.updated_by || "—") + " · " + fmtDate(p.updated_at) }));
    }

    editorEl.appendChild(el("div", { class: "wk-editor" }, [
      head,
      field("Catégorie", F.dom),
      field("Situation", F.situation),
      field("Les bonnes questions à poser", F.questions),
      field("Le cheminement (logigramme)", flowUi),
      field("Qui engager", F.acteurs),
      field("Conduite à tenir, en détail", F.conduite),
      field("À consigner", F.consigner),
      field("Pièges", F.pieges),
      field("Sous-cas", F.souscas),
      field("Réflexes terrain — ce que font les opérateurs", F.details),
      bar
    ]));
  }

  function collectForm() {
    return {
      code: F.code.value.trim().toUpperCase(),
      titre: F.titre.value.trim(),
      dom: F.dom.value,
      status: F.status.value,
      situation: F.situation.value.trim(),
      acteurs: F.acteurs.value.trim(),
      consigner: F.consigner.value.trim(),
      pieges: F.pieges.value.trim(),
      questions: F.questions._collect(),
      conduite: F.conduite._collect(),
      souscas: F.souscas._collect(),
      details: F.details._collect(),
      flow: collectFlow()
    };
  }

  function doSave() {
    var d = collectForm();
    if (!d.code || !d.titre || !d.dom) { toast("error", "Code, titre et catégorie sont obligatoires."); return; }
    var p = current._id ? A.updateProc(current._id, d) : A.createProc(d);
    p.then(function (res) {
      if (!res.ok) { toast("error", (res.data && res.data.error) || "Erreur"); return; }
      toast("success", current._id ? "Fiche mise à jour" : "Fiche créée");
      var savedId = res.data._id;
      reload(savedId);
    });
  }

  function doDelete() {
    if (!current._id) return;
    var msg = "Supprimer définitivement la fiche " + current.code + " ?";
    var ask = window.showConfirmToast
      ? showConfirmToast(msg, { type: "warning", okLabel: "Supprimer", cancelLabel: "Annuler" })
      : Promise.resolve(window.confirm(msg));
    ask.then(function (ok) {
      if (!ok) return;
      A.deleteProc(current._id).then(function (res) {
        if (!res.ok) { toast("error", (res.data && res.data.error) || "Erreur"); return; }
        toast("success", "Fiche supprimée");
        current = null; reload();
      });
    });
  }

  // ---------- liste ----------
  function statusDot(s) { return el("span", { class: "wk-status-dot " + (s === "published" ? "pub" : "draft"), title: s === "published" ? "Publié" : "Brouillon" }); }
  function renderList() {
    var term = (searchEl.value || "").trim().toLowerCase();
    listEl.innerHTML = "";
    procs.filter(function (p) {
      return !term || (p.code + " " + p.titre).toLowerCase().indexOf(term) !== -1;
    }).forEach(function (p) {
      var row = el("div", { class: "wk-adm-row" + (current && current._id === p._id ? " wk-sel" : ""), onclick: function () { openProc(p); } }, [
        el("span", { class: "wk-rcode", text: p.code, style: "color:" + ((catByKey[p.dom] || {}).color || "#64748b") }),
        el("span", { class: "wk-rtitle", text: p.titre }),
        statusDot(p.status)
      ]);
      listEl.appendChild(row);
    });
  }
  function openProc(p) {
    if (dirty && !window.confirm("Modifications non enregistrées. Continuer et les perdre ?")) return;
    renderEditor(p); renderList();
  }
  function newProc() {
    if (dirty && !window.confirm("Modifications non enregistrées. Continuer et les perdre ?")) return;
    var maxN = procs.reduce(function (m, p) { var n = parseInt((p.code || "").replace(/\D/g, ""), 10); return isNaN(n) ? m : Math.max(m, n); }, 0);
    var code = "P" + String(maxN + 1).padStart(2, "0");
    renderEditor({ code: code, titre: "", dom: (cats[0] || {}).key || "", status: "draft", questions: [], conduite: [], souscas: [], details: [], flow: [{ k: "start", t: "" }, { k: "end", t: "Clôture" }] });
    renderList();
  }

  function reload(selectId) {
    return Promise.all([A.listProc(), A.listCat()]).then(function (r) {
      procs = r[0] || []; cats = r[1] || [];
      catByKey = {}; cats.forEach(function (c) { catByKey[c.key] = c; });
      if (selectId) { var f = procs.filter(function (p) { return p._id === selectId; })[0]; if (f) current = f; }
      renderList();
      if (current && current._id) { var cur = procs.filter(function (p) { return p._id === current._id; })[0]; if (cur) renderEditor(cur); }
      else if (!current) editorEl.innerHTML = '<div class="wk-editor wk-empty">Sélectionnez une fiche à gauche, ou créez-en une nouvelle.</div>';
    });
  }

  // ---------- catégories ----------
  function openCats() {
    var back = el("div", { class: "wk-modal-back", onclick: function (e) { if (e.target === back) back.remove(); } });
    var body = el("div", { class: "wk-cats-body" });
    var modal = el("div", { class: "wk-modal" }, [
      el("div", { class: "wk-modal-head" }, [el("h3", { text: "Catégories" }), iconBtn("close", function () { back.remove(); })]),
      body
    ]);
    back.appendChild(modal); document.body.appendChild(back);
    function draw() {
      body.innerHTML = "";
      cats.forEach(function (c) {
        var lab = el("input", { class: "wk-input" }); lab.value = c.label;
        var col = el("input", { class: "wk-input", type: "color" }); col.value = c.color || "#2563eb"; col.style.width = "48px"; col.style.padding = "2px";
        var save = iconBtn("save", function () { A.updateCat(c._id, { label: lab.value.trim(), color: col.value }).then(function (r) { if (r.ok) { toast("success", "Catégorie mise à jour"); reload().then(draw); } else toast("error", r.data.error || "Erreur"); }); });
        var del = iconBtn("delete", function () { A.deleteCat(c._id).then(function (r) { if (r.ok) { toast("success", "Catégorie supprimée"); reload().then(draw); } else toast("error", r.data.error || "Erreur"); }); }, true);
        body.appendChild(el("div", { class: "wk-le-row" }, [el("span", { class: "wk-rcode", text: c.key }), lab, col, save, del]));
      });
      var nk = el("input", { class: "wk-input", placeholder: "clé (ex: sanitaire)" });
      var nl = el("input", { class: "wk-input", placeholder: "libellé" });
      var nc = el("input", { class: "wk-input", type: "color" }); nc.value = "#2563eb"; nc.style.width = "48px"; nc.style.padding = "2px";
      var addb = iconBtn("add", function () {
        if (!nk.value.trim() || !nl.value.trim()) { toast("error", "clé et libellé requis"); return; }
        A.createCat({ key: nk.value.trim(), label: nl.value.trim(), color: nc.value }).then(function (r) { if (r.ok) { toast("success", "Catégorie créée"); reload().then(draw); } else toast("error", r.data.error || "Erreur"); });
      });
      body.appendChild(el("div", { class: "wk-le-row", style: "margin-top:12px;border-top:1px solid var(--line);padding-top:12px;" }, [nk, nl, nc, addb]));
    }
    draw();
  }

  // ---------- init ----------
  document.addEventListener("DOMContentLoaded", function () {
    listEl = document.getElementById("wk-adm-list");
    editorEl = document.getElementById("wk-editor");
    searchEl = document.getElementById("wk-adm-search");
    if (!listEl) return;
    searchEl.addEventListener("input", renderList);
    document.getElementById("wk-new-btn").addEventListener("click", newProc);
    document.getElementById("wk-cats-btn").addEventListener("click", openCats);
    reload();
  });
})();
