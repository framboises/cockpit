// ==========================================================================
// POSTE OPERATEUR (accueil)
// - Membre d'un groupe Operateur sans poste ouvert : fenetre bloquante
//   (evenement en cours, zone(s), metier) -> POST /api/operator/post, qui cree
//   aussi la fiche "Prise de poste" (cloturee) dans la main courante.
// - Poste ouvert : repris tel quel (rattache au compte, pas au navigateur).
// - Pastille "Poste" de l'en-tete : changer de zone, pause / reprise (pause
//   DECLAREE, jamais deduite), fin de poste.
// Le poste ne restreint aucun droit : il fixe l'evenement affiche et sert au
// ciblage des alertes. Plan : docs/claude/operateurs.md.
// ==========================================================================
(function () {
  "use strict";

  if (!window.__operatorRequired) return;

  var REFRESH_MS = 60 * 1000;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function icon(name) { return el("span", "material-symbols-outlined", name); }
  function toast(type, msg) { if (typeof window.showToast === "function") window.showToast(type, msg); }
  function confirmToast(msg, okLabel) {
    return typeof window.showConfirmToast === "function" && document.getElementById("toast-container")
      ? window.showConfirmToast(msg, { type: "warning", okLabel: okLabel, cancelLabel: "Annuler" })
      : Promise.resolve(true);
  }
  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  function norm(s) {
    return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }
  function post(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok && d.ok, d: d, status: r.status }; });
    });
  }

  var ERRORS = {
    evenement_non_en_cours: "Cet evenement n'est plus en cours.",
    zone_requise: "Choisissez au moins une zone.",
    zone_inconnue: "Une zone choisie n'existe plus, rechargez la liste.",
    metier_requis: "Choisissez votre metier.",
    pas_operateur: "Votre groupe ne demande pas de prise de poste.",
    utilisateur_inconnu: "Compte introuvable dans la base des utilisateurs.",
    aucun_poste: "Aucun poste ouvert : il a peut-etre ete cloture."
  };
  function errText(res, what) {
    return ERRORS[res.d && res.d.error] || (what + " refuse (" + ((res.d && res.d.error) || res.status) + ")");
  }

  // --- Pastille "Poste" de l'en-tete et son menu --------------------------
  function renderChip(p) {
    var host = document.querySelector(".header-actions");
    var chip = document.getElementById("operator-post-chip");
    if (!p) { if (chip) chip.remove(); closeMenu(); return; }
    if (!host) return;
    if (!chip) {
      chip = el("button", "operator-post-chip");
      chip.type = "button";
      chip.id = "operator-post-chip";
      chip.addEventListener("click", function (e) { e.stopPropagation(); toggleMenu(chip); });
      host.insertBefore(chip, host.firstChild);
    }
    chip.textContent = "";
    var paused = p.status === "pause";
    chip.classList.toggle("is-pause", paused);
    chip.appendChild(icon(paused ? "coffee" : "badge"));
    var zones = p.zones_labels || [];
    var txt = paused ? "En pause" : (zones.length ? (zones.length > 2 ? zones.slice(0, 2).join(", ") + " +" + (zones.length - 2) : zones.join(", ")) : "Poste");
    if (!paused && p.metier_label) txt += " - " + p.metier_label;
    chip.appendChild(el("span", "operator-post-chip-text", txt));
    chip.appendChild(icon("expand_more"));
    var started = p.started_at ? new Date(p.started_at) : null;
    chip.title = "Poste : " + p.event + " " + p.year
      + (zones.length ? "\nZones : " + zones.join(", ") : "")
      + (p.metier_label ? "\nMetier : " + p.metier_label : "")
      + (started ? "\nDepuis " + started.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "")
      + (paused ? "\nEn pause : vos alertes de zone partent vers les autres operateurs" : "");
  }

  function closeMenu() {
    var m = document.getElementById("operator-post-menu");
    if (m) m.remove();
  }

  function toggleMenu(anchor) {
    if (document.getElementById("operator-post-menu")) { closeMenu(); return; }
    var p = window.__operatorPost;
    if (!p) return;
    var menu = el("div", "operator-post-menu");
    menu.id = "operator-post-menu";
    var head = el("div", "operator-post-menu-head");
    head.appendChild(el("strong", null, p.event + " " + p.year));
    if ((p.zones_labels || []).length) head.appendChild(el("div", null, "Zones : " + p.zones_labels.join(", ")));
    if (p.metier_label) head.appendChild(el("div", null, "Metier : " + p.metier_label));
    menu.appendChild(head);
    function item(ic, label, fn, cls) {
      var b = el("button", "operator-post-menu-item" + (cls ? " " + cls : ""));
      b.type = "button";
      b.appendChild(icon(ic));
      b.appendChild(el("span", null, label));
      b.addEventListener("click", function () { closeMenu(); fn(); });
      menu.appendChild(b);
    }
    item("edit_location_alt", "Changer de zone / metier", function () { openModal("zones"); });
    if (p.status === "pause") item("play_arrow", "Je reprends", function () { doPause(false); });
    else item("coffee", "Je pars en pause", function () { doPause(true); });
    item("logout", "Fin de poste", doEnd, "is-danger");
    var r = anchor.getBoundingClientRect();
    menu.style.top = (r.bottom + window.scrollY + 4) + "px";
    menu.style.left = Math.max(8, Math.min(r.left + window.scrollX, window.innerWidth - 280)) + "px";
    document.body.appendChild(menu);
    setTimeout(function () {
      document.addEventListener("click", function close(ev) {
        if (!menu.contains(ev.target)) { closeMenu(); document.removeEventListener("click", close); }
      });
    }, 0);
  }

  function applyPost(p) {
    window.__operatorPost = p;
    if (typeof window.cockpitLockToPost === "function") window.cockpitLockToPost(p);
    renderChip(p);
  }

  // --- Actions ------------------------------------------------------------
  function doPause(pause) {
    post(pause ? "/api/operator/post/pause" : "/api/operator/post/resume").then(function (res) {
      if (!res.ok) { toast("error", errText(res, pause ? "Pause" : "Reprise")); return; }
      applyPost(res.d.post);
      toast(pause ? "info" : "success", pause
        ? "En pause : vos alertes de zone partent vers les autres operateurs"
        : "Retour de pause : vos zones sont de nouveau couvertes");
    }).catch(function (e) { toast("error", "Erreur reseau (" + e.message + ")"); });
  }

  function doEnd() {
    confirmToast("Terminer votre poste ? Une fiche \"Fin de poste\" est ajoutee a la main courante.", "Terminer")
      .then(function (ok) {
        if (!ok) return;
        post("/api/operator/post/end").then(function (res) {
          if (!res.ok) { toast("error", errText(res, "Fin de poste")); return; }
          ended("Poste termine");
        }).catch(function (e) { toast("error", "Erreur reseau (" + e.message + ")"); });
      });
  }

  // Poste ferme (par l'operateur ou un admin) : selecteurs liberes, nouvelle
  // prise de poste obligatoire
  function ended(msg) {
    applyPost(null);
    try { sessionStorage.removeItem("operator_post_seen"); } catch (e) {}
    if (msg) toast("info", msg);
    openModal("prise");
  }

  // --- Fenetre : prise de poste ("prise") ou changement de zone ("zones") --
  var state = null;

  function openModal(mode) {
    if (document.getElementById("operator-post-overlay")) return;
    var current = window.__operatorPost;
    var isZones = mode === "zones";
    state = { opts: null, event: null, zones: {}, metier: null, map: null, polys: {}, group: null, busy: false };
    if (isZones && current) {
      (current.zones || []).forEach(function (z) { state.zones[z] = true; });
      state.metier = current.metier_id || null;
    }
    var overlay = el("div", "operator-post-overlay");
    overlay.id = "operator-post-overlay";
    var box = el("div", "operator-post-modal");
    overlay.appendChild(box);
    var head = el("div", "operator-post-head");
    head.appendChild(icon(isZones ? "edit_location_alt" : "badge"));
    var ht = el("div");
    ht.appendChild(el("div", "operator-post-title", isZones ? "Changer de zone / metier" : "Prise de poste"));
    ht.appendChild(el("div", "operator-post-sub", isZones
      ? "Le poste continue. Une fiche \"Changement de zone\" est ajoutee a la main courante."
      : "Indiquez l'evenement, vos zones et votre metier. Une fiche \"Prise de poste\" est ajoutee a la main courante."));
    head.appendChild(ht);
    box.appendChild(head);
    var body = el("div", "operator-post-body");
    body.appendChild(el("div", "operator-post-loading", "Chargement..."));
    box.appendChild(body);
    var foot = el("div", "operator-post-foot");
    var err = el("span", "operator-post-error");
    foot.appendChild(err);
    if (isZones) {
      var cancel = el("button", "btn btn-secondary", "Annuler");
      cancel.type = "button";
      cancel.addEventListener("click", close);
      foot.appendChild(cancel);
    }
    var go = el("button", "btn btn-primary", isZones ? "Enregistrer" : "Prendre mon poste");
    go.type = "button";
    go.disabled = true;
    foot.appendChild(go);
    box.appendChild(foot);
    document.body.appendChild(overlay);
    document.body.classList.add("operator-post-open");

    function close() {
      overlay.remove();
      document.body.classList.remove("operator-post-open");
    }
    function check() {
      var o = state.opts;
      var msg = "";
      if (!isZones && !state.event) msg = "Choisissez l'evenement.";
      else if (o.zones.length && !Object.keys(state.zones).length) msg = "Choisissez au moins une zone.";
      else if (o.metiers.length && !state.metier) msg = "Choisissez votre metier.";
      err.textContent = msg;
      go.disabled = !!msg || state.busy;
    }

    fetch("/api/operator/options", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) {
        state.opts = d;
        // Zones disparues de la couche depuis la prise de poste : decochees
        var known = {};
        d.zones.forEach(function (z) { known[z.id] = true; });
        Object.keys(state.zones).forEach(function (id) { if (!known[id]) delete state.zones[id]; });
        body.textContent = "";
        if (!isZones) {
          if (d.events.length) state.event = d.events[0];
          body.appendChild(sectionEvents(d.events, check));
        }
        if (d.zones.length) body.appendChild(sectionZones(d.zones, check));
        else if (d.zones_configured) body.appendChild(el("p", "operator-post-note", "La couche de zones ne contient aucune zone exploitable : poste sans zone."));
        if (d.metiers.length) body.appendChild(sectionMetiers(d.metiers, check));
        check();
      })
      .catch(function (e) {
        body.textContent = "";
        body.appendChild(el("p", "operator-post-error", "Chargement impossible (" + e.message + "). Rechargez la page."));
      });

    go.addEventListener("click", function () {
      if (go.disabled) return;
      var label = go.textContent;
      state.busy = true;
      check();
      go.textContent = "Envoi...";
      var url = isZones ? "/api/operator/post/zones" : "/api/operator/post";
      var payload = { zones: Object.keys(state.zones), metier_id: state.metier };
      if (!isZones) { payload.event = state.event.event; payload.year = state.event.year; }
      post(url, payload).then(function (res) {
        if (!res.ok) {
          state.busy = false;
          go.textContent = label;
          check();
          err.textContent = errText(res, isZones ? "Changement" : "Prise de poste");
          if (isZones && res.d && res.d.error === "aucun_poste") { close(); ended(null); }
          return;
        }
        close();
        applyPost(res.d.post);
        try { sessionStorage.setItem("operator_post_seen", res.d.post.id); } catch (e) {}
        if (isZones) toast("success", "Zones mises a jour");
        else toast("success", res.d.created ? "Poste pris : " + res.d.post.event + " " + res.d.post.year
                                           : "Poste deja ouvert : repris");
      }).catch(function (e) {
        state.busy = false;
        go.textContent = label;
        check();
        err.textContent = "Erreur reseau (" + e.message + ")";
      });
    });
  }

  function section(title, iconName) {
    var s = el("div", "operator-post-section");
    var h = el("div", "operator-post-section-title");
    h.appendChild(icon(iconName));
    h.appendChild(el("span", null, title));
    s.appendChild(h);
    return s;
  }

  function radios(list, name, isOn, onPick, labelOf, subOf) {
    var box = el("div", "operator-post-options");
    list.forEach(function (it) {
      var on = isOn(it);
      var l = el("label", "operator-post-option" + (on ? " is-on" : ""));
      var r = document.createElement("input");
      r.type = "radio"; r.name = name; r.checked = on;
      r.addEventListener("change", function () {
        onPick(it);
        box.querySelectorAll(".operator-post-option").forEach(function (x) { x.classList.remove("is-on"); });
        l.classList.add("is-on");
      });
      l.appendChild(r);
      l.appendChild(el("span", "operator-post-option-main", labelOf(it)));
      var sub = subOf && subOf(it);
      if (sub) l.appendChild(el("span", "operator-post-option-sub", sub));
      box.appendChild(l);
    });
    return box;
  }

  function sectionEvents(events, check) {
    var s = section("Evenement", "event");
    s.appendChild(radios(events, "op-event",
      function (ev) { return ev === state.event; },
      function (ev) { state.event = ev; check(); },
      function (ev) { return ev.event + " " + ev.year; },
      function (ev) { return ev.kind === "epreuve" ? ev.phase : null; }));
    return s;
  }

  function sectionMetiers(metiers, check) {
    var s = section("Metier", "work");
    s.appendChild(radios(metiers, "op-metier",
      function (m) { return m.id === state.metier; },
      function (m) { state.metier = m.id; check(); },
      function (m) { return m.label; }));
    return s;
  }

  function sectionZones(zones, check) {
    var s = section("Zone(s)", "map");
    var grid = el("div", "operator-post-zones");
    var mapEl = el("div", "operator-post-map");
    var side = el("div", "operator-post-zone-side");
    var search = document.createElement("input");
    search.type = "search";
    search.className = "form-input";
    search.placeholder = "Rechercher une zone";
    var tools = el("div", "operator-post-zone-tools");
    var count = el("span", "operator-post-zone-count");
    var none = el("button", "btn btn-sm", "Aucune");
    none.type = "button";
    tools.appendChild(count);
    tools.appendChild(none);
    var list = el("div", "operator-post-zone-list");
    side.appendChild(search);
    side.appendChild(tools);
    side.appendChild(list);
    grid.appendChild(mapEl);
    grid.appendChild(side);
    s.appendChild(grid);

    var boxes = {};
    function styleFor(on) {
      return on ? { color: "#16a34a", weight: 3, fillOpacity: 0.35 } : { color: "#2563eb", weight: 1.5, fillOpacity: 0.08 };
    }
    function refreshCount() {
      var n = Object.keys(state.zones).length;
      count.textContent = n ? n + " zone" + (n > 1 ? "s" : "") + " choisie" + (n > 1 ? "s" : "") : "Aucune zone choisie";
    }
    function set(id, on) {
      if (on) state.zones[id] = true; else delete state.zones[id];
      if (boxes[id]) { boxes[id].input.checked = on; boxes[id].row.classList.toggle("is-on", on); }
      if (state.polys[id]) state.polys[id].setStyle(styleFor(on));
      refreshCount();
      check();
    }
    zones.forEach(function (z) {
      var on = !!state.zones[z.id];
      var row = el("label", "operator-post-zone" + (on ? " is-on" : ""));
      var c = document.createElement("input");
      c.type = "checkbox";
      c.checked = on;
      c.addEventListener("change", function () { set(z.id, c.checked); });
      row.appendChild(c);
      row.appendChild(el("span", null, z.label));
      list.appendChild(row);
      boxes[z.id] = { input: c, row: row, key: norm(z.label) };
    });
    refreshCount();
    search.addEventListener("input", function () {
      var q = norm(search.value.trim());
      Object.keys(boxes).forEach(function (id) {
        boxes[id].row.style.display = !q || boxes[id].key.indexOf(q) !== -1 ? "" : "none";
      });
    });
    none.addEventListener("click", function () { Object.keys(state.zones).forEach(function (id) { set(id, false); }); });

    if (typeof L !== "undefined") {
      setTimeout(function () {
        state.map = L.map(mapEl, { zoomControl: true }).setView([47.95, 0.21], 13);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "OSM", maxNativeZoom: 19, maxZoom: 22 }).addTo(state.map);
        var group = L.featureGroup().addTo(state.map);
        // Les plus grandes zones dessous : une petite zone incluse reste cliquable
        zones.slice().sort(function (a, b) { return area(b.geometry) - area(a.geometry); }).forEach(function (z) {
          var p = L.geoJSON(z.geometry, { style: styleFor(!!state.zones[z.id]) });
          p.bindTooltip(z.label, { sticky: true });
          p.on("click", function () { set(z.id, !state.zones[z.id]); });
          p.addTo(group);
          state.polys[z.id] = p;
        });
        state.group = group;
        state.map.invalidateSize();
        if (group.getLayers().length) state.map.fitBounds(group.getBounds(), { padding: [10, 10] });
      }, 0);
    }
    return s;
  }

  // Aire approximative (degres carres) pour l'ordre d'empilement seulement
  function area(geom) {
    var polys = geom.type === "MultiPolygon" ? geom.coordinates : [geom.coordinates];
    var a = 0;
    polys.forEach(function (poly) {
      var ring = (poly && poly[0]) || [];
      for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        a += (ring[j][0] + ring[i][0]) * (ring[j][1] - ring[i][1]);
      }
    });
    return Math.abs(a / 2);
  }

  // --- Etat du poste modifie ailleurs (autre PC, cloture admin) ------------
  function refresh() {
    if (document.getElementById("operator-post-overlay")) return;
    fetch("/api/operator/post", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.ok) return;
        var cur = window.__operatorPost;
        if (!d.post) {
          if (cur) ended("Votre poste a ete termine (autre poste de travail ou administrateur)");
          return;
        }
        if (!cur || JSON.stringify(cur) !== JSON.stringify(d.post)) applyPost(d.post);
      })
      .catch(function () {});
  }

  // --- Demarrage -----------------------------------------------------------
  function start() {
    var p = window.__operatorPost;
    setInterval(refresh, REFRESH_MS);
    if (!p) { openModal("prise"); return; }
    applyPost(p);
    var seen = null;
    try { seen = sessionStorage.getItem("operator_post_seen"); } catch (e) {}
    if (seen === p.id) return;
    try { sessionStorage.setItem("operator_post_seen", p.id); } catch (e) {}
    if (p.status === "pause") {
      // Retour sur Cockpit pendant une pause declaree : proposer de reprendre
      confirmToast("Vous etes en pause. Reprendre votre poste ?", "Je reprends")
        .then(function (ok) { if (ok) doPause(false); });
    } else {
      toast("info", "Poste repris : " + p.event + " " + p.year
        + ((p.zones_labels || []).length ? " - " + p.zones_labels.join(", ") : ""));
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
