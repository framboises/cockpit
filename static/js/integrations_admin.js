/* Applications liees (/integrations, admin) - integrations.py
 *
 * Liste des applications externes (Friday...), etat de la liaison (secret
 * present, file d'envoi, derniere erreur), test de connexion, relance des
 * envois en echec, et edition : adresse de reception, variable du secret,
 * regles d'envoi par categorie / sous-classification.
 */
(function () {
  "use strict";

  var CATEGORIES = [
    ["PCO.Secours", "Secours"], ["PCO.Securite", "Securite"], ["PCO.Technique", "Technique"],
    ["PCO.Flux", "Flux"], ["PCO.Fourriere", "Fourriere"], ["PCO.Information", "Information"],
    ["PCO.MainCourante", "Main courante"],
  ];
  var state = { items: [], sousClass: {}, editing: null };

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function icon(n) { return el("span", "material-symbols-outlined", n); }
  function toast(t, m) { if (window.showToast) window.showToast(t, m); }
  function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ""; }
  function api(method, url, body) {
    var o = { method: method, credentials: "same-origin", headers: { "X-CSRFToken": csrf() } };
    if (body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(body); }
    return fetch(url, o).then(function (r) { return r.json().catch(function () { return { ok: false, error: "HTTP " + r.status }; }); })
      .catch(function () { return { ok: false, error: "reseau" }; });
  }
  function fmt(iso) { return iso ? new Date(iso).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : ""; }

  function load() {
    api("GET", "/api/integrations-admin").then(function (d) {
      state.items = (d && d.integrations) || [];
      render();
    });
  }

  function ruleLabel(r) {
    var c = (CATEGORIES.filter(function (x) { return x[0] === r.category; })[0] || [r.category, r.category])[1];
    return r.sous_classification ? c + " > " + r.sous_classification : c + " (toute la categorie)";
  }

  function render() {
    var list = $("ig-list");
    list.textContent = "";
    if (!state.items.length) {
      list.appendChild(el("div", "ig-empty", "Aucune application. Cliquez sur Nouvelle application."));
      return;
    }
    state.items.forEach(function (it) {
      var card = el("section", "ig-card");
      var head = el("div", "ig-card-head");
      head.appendChild(el("h3", "", it.label));
      head.appendChild(el("span", "ig-badge " + (it.enabled ? "is-on" : "is-off"), it.enabled ? "Active" : "Inactive"));
      if (!it.secret_ok) head.appendChild(el("span", "ig-badge is-warn", "Secret absent (" + it.secret_env + ")"));
      var sp = el("div", "ig-spacer");
      head.appendChild(sp);
      var bTest = el("button", "btn btn-secondary ig-btn");
      bTest.appendChild(icon("network_check")); bTest.appendChild(document.createTextNode(" Tester"));
      bTest.addEventListener("click", function () {
        bTest.disabled = true;
        api("POST", "/api/integrations-admin/" + encodeURIComponent(it.id) + "/test").then(function (r) {
          bTest.disabled = false;
          toast(r.ok ? "success" : "error", r.ok ? "Connexion OK : l'application a accepte l'evenement signe" : "Echec : " + (r.message || r.error));
        });
      });
      head.appendChild(bTest);
      var bEdit = el("button", "btn btn-primary ig-btn");
      bEdit.appendChild(icon("edit")); bEdit.appendChild(document.createTextNode(" Modifier"));
      bEdit.addEventListener("click", function () { openEdit(it); });
      head.appendChild(bEdit);
      card.appendChild(head);

      var grid = el("div", "ig-facts");
      function fact(k, v, mono) {
        var f = el("div", "ig-fact");
        f.appendChild(el("span", "ig-k", k));
        f.appendChild(el("span", "ig-v" + (mono ? " is-mono" : ""), v || "-"));
        grid.appendChild(f);
      }
      fact("Envoi vers", it.outbound_url, true);
      fact("Hotes autorises (serveur)", (it.allowed_hosts || []).join(", ") || "aucun : COCKPIT_INTEG_ALLOWED_HOSTS vide", true);
      fact("Reception (a donner a l'application)", (window.__cockpitPublicUrl || "<adresse de Cockpit>") + it.inbound_path, true);
      fact("Fiches envoyees", (it.rules || []).map(ruleLabel).join(" ; ") || "aucune regle");
      fact("Fiches partagees / liees", it.shared + " / " + it.linked);
      fact("File d'envoi", it.outbox.pending + " en attente, " + it.outbox.failed + " en echec");
      if (it.last_error) fact("Derniere erreur", fmt(it.last_error_at) + " - " + it.last_error);
      card.appendChild(grid);

      if (it.outbox.failed) {
        var bRetry = el("button", "btn btn-secondary ig-btn");
        bRetry.appendChild(icon("replay")); bRetry.appendChild(document.createTextNode(" Relancer les envois en echec"));
        bRetry.addEventListener("click", function () {
          api("POST", "/api/integrations-admin/" + encodeURIComponent(it.id) + "/retry").then(function (r) {
            toast(r.ok ? "success" : "error", r.ok ? r.requeued + " envoi(s) relance(s)" : "Erreur");
            load();
          });
        });
        card.appendChild(bRetry);
      }
      list.appendChild(card);
    });
  }

  // ------------------------------------------------------------ edition
  function renderRules(rules) {
    var box = $("ig-rules");
    box.textContent = "";
    CATEGORIES.forEach(function (c) {
      var cat = c[0];
      var whole = rules.some(function (r) { return r.category === cat && !r.sous_classification; });
      var subs = rules.filter(function (r) { return r.category === cat && r.sous_classification; })
        .map(function (r) { return r.sous_classification; });
      var row = el("div", "ig-rule");
      var lab = el("label", "ig-rule-cat");
      var cb = el("input");
      cb.type = "checkbox";
      cb.dataset.cat = cat;
      cb.className = "ig-cat-cb";
      cb.checked = whole;
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(" " + c[1] + " (toute la categorie)"));
      row.appendChild(lab);
      var list = state.sousClass[cat] || [];
      var subBox = el("div", "ig-rule-subs");
      list.forEach(function (s) {
        var name = typeof s === "string" ? s : (s && (s.label || s.name)) || "";
        if (!name) return;
        var sl = el("label", "ig-sub");
        var scb = el("input");
        scb.type = "checkbox";
        scb.className = "ig-sub-cb";
        scb.dataset.cat = cat;
        scb.dataset.sub = name;
        scb.checked = subs.indexOf(name) >= 0;
        scb.disabled = whole;
        sl.appendChild(scb);
        sl.appendChild(document.createTextNode(" " + name));
        subBox.appendChild(sl);
      });
      cb.addEventListener("change", function () {
        subBox.querySelectorAll("input").forEach(function (x) { x.disabled = cb.checked; if (cb.checked) x.checked = false; });
      });
      if (list.length) row.appendChild(subBox);
      box.appendChild(row);
    });
  }

  function collectRules() {
    var out = [];
    document.querySelectorAll("#ig-rules .ig-cat-cb").forEach(function (cb) {
      if (cb.checked) out.push({ category: cb.dataset.cat, sous_classification: null });
    });
    document.querySelectorAll("#ig-rules .ig-sub-cb").forEach(function (cb) {
      if (cb.checked && !cb.disabled) out.push({ category: cb.dataset.cat, sous_classification: cb.dataset.sub });
    });
    return out;
  }

  function openEdit(it) {
    state.editing = it || null;
    $("ig-edit-title").textContent = it ? "Modifier " + it.label : "Nouvelle application";
    $("ig-f-id").value = it ? it.id : "";
    $("ig-f-id").disabled = !!it;
    $("ig-f-label").value = it ? it.label : "";
    $("ig-f-url").value = it ? it.outbound_url : "";
    $("ig-f-secret").value = it ? it.secret_env : "";
    $("ig-f-enabled").checked = it ? it.enabled : false;
    renderRules(it ? it.rules || [] : []);
    $("ig-edit").hidden = false;
  }

  function save() {
    var id = $("ig-f-id").value.trim().toLowerCase();
    var body = {
      id: id, label: $("ig-f-label").value.trim(), outbound_url: $("ig-f-url").value.trim(),
      secret_env: $("ig-f-secret").value.trim(), enabled: $("ig-f-enabled").checked, rules: collectRules(),
    };
    api("POST", "/api/integrations-admin", body).then(function (r) {
      if (!r.ok) {
        var msg = {
          https_obligatoire: "L'adresse doit commencer par https://",
          hote_non_autorise: "Hote non autorise. Hotes admis sur ce serveur : " +
            ((r.allowed_hosts || []).join(", ") || "aucun (variable COCKPIT_INTEG_ALLOWED_HOSTS a renseigner)"),
          hote_introuvable: "Nom d'hote introuvable (DNS)",
          adresse_interdite: "Adresse interdite (le serveur lui-meme ou une adresse speciale)",
          url_invalide: "Adresse invalide",
        }[r.error];
        toast("error", msg || ("Erreur : " + (r.error || "?")));
        return;
      }
      toast("success", "Application enregistree");
      $("ig-edit").hidden = true;
      load();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    $("ig-new").addEventListener("click", function () { openEdit(null); });
    $("ig-save").addEventListener("click", save);
    document.querySelectorAll("#ig-edit [data-close]").forEach(function (b) {
      b.addEventListener("click", function () { $("ig-edit").hidden = true; });
    });
    api("GET", "/api/pcorg-config").then(function (cfg) {
      state.sousClass = (cfg && cfg.sous_classifications) || {};
    });
    load();
  });
})();
