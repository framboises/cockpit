// ==========================================================================
// PRISE DE POSTE OPERATEUR (accueil)
// Membre d'un groupe Operateur sans poste ouvert : fenetre bloquante
// (evenement en cours, zone(s), metier) -> POST /api/operator/post, qui cree
// aussi la fiche "Prise de poste" (cloturee) dans la main courante.
// Poste ouvert : repris tel quel (rattache au compte, pas au navigateur).
// Le poste ne restreint aucun droit : il fixe l'evenement affiche et sert au
// ciblage des alertes. Plan : docs/claude/operateurs.md.
// ==========================================================================
(function () {
  "use strict";

  if (!window.__operatorRequired) return;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function icon(name) { return el("span", "material-symbols-outlined", name); }
  function toast(type, msg) { if (typeof window.showToast === "function") window.showToast(type, msg); }
  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  function norm(s) {
    return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  var ERRORS = {
    evenement_non_en_cours: "Cet evenement n'est plus en cours.",
    zone_requise: "Choisissez au moins une zone.",
    zone_inconnue: "Une zone choisie n'existe plus, rechargez la liste.",
    metier_requis: "Choisissez votre metier.",
    pas_operateur: "Votre groupe ne demande pas de prise de poste.",
    utilisateur_inconnu: "Compte introuvable dans la base des utilisateurs."
  };

  // --- Pastille "Poste" de l'en-tete ---------------------------------------
  function renderChip(post) {
    var host = document.querySelector(".header-actions");
    var chip = document.getElementById("operator-post-chip");
    if (!post) { if (chip) chip.remove(); return; }
    if (!host) return;
    if (!chip) {
      chip = el("div", "operator-post-chip");
      chip.id = "operator-post-chip";
      host.insertBefore(chip, host.firstChild);
    }
    chip.textContent = "";
    chip.classList.toggle("is-pause", post.status === "pause");
    chip.appendChild(icon(post.status === "pause" ? "coffee" : "badge"));
    var zones = (post.zones_labels || []);
    var txt = zones.length ? (zones.length > 2 ? zones.slice(0, 2).join(", ") + " +" + (zones.length - 2) : zones.join(", ")) : "Poste";
    if (post.metier_label) txt += " - " + post.metier_label;
    chip.appendChild(el("span", "operator-post-chip-text", txt));
    var started = post.started_at ? new Date(post.started_at) : null;
    chip.title = "Poste : " + post.event + " " + post.year
      + (zones.length ? "\nZones : " + zones.join(", ") : "")
      + (post.metier_label ? "\nMetier : " + post.metier_label : "")
      + (started ? "\nDepuis " + started.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "");
  }

  function applyPost(post) {
    window.__operatorPost = post;
    if (typeof window.cockpitLockToPost === "function") window.cockpitLockToPost(post);
    renderChip(post);
  }

  // --- Fenetre de prise de poste ------------------------------------------
  var state = { opts: null, event: null, zones: {}, metier: null, map: null, polys: {}, busy: false };

  function openModal() {
    var overlay = el("div", "operator-post-overlay");
    overlay.id = "operator-post-overlay";
    var box = el("div", "operator-post-modal");
    overlay.appendChild(box);
    var head = el("div", "operator-post-head");
    head.appendChild(icon("badge"));
    var ht = el("div");
    ht.appendChild(el("div", "operator-post-title", "Prise de poste"));
    ht.appendChild(el("div", "operator-post-sub",
      "Indiquez l'evenement, vos zones et votre metier. Une fiche \"Prise de poste\" est ajoutee a la main courante."));
    head.appendChild(ht);
    box.appendChild(head);
    var body = el("div", "operator-post-body");
    body.appendChild(el("div", "operator-post-loading", "Chargement..."));
    box.appendChild(body);
    var foot = el("div", "operator-post-foot");
    var err = el("span", "operator-post-error");
    var go = el("button", "btn btn-primary", "Prendre mon poste");
    go.type = "button";
    go.disabled = true;
    foot.appendChild(err);
    foot.appendChild(go);
    box.appendChild(foot);
    document.body.appendChild(overlay);
    document.body.classList.add("operator-post-open");

    function check() {
      var o = state.opts;
      var msg = "";
      if (!state.event) msg = "Choisissez l'evenement.";
      else if (o.zones.length && !Object.keys(state.zones).length) msg = "Choisissez au moins une zone.";
      else if (o.metiers.length && !state.metier) msg = "Choisissez votre metier.";
      err.textContent = msg;
      go.disabled = !!msg || state.busy;
    }

    fetch("/api/operator/options", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) {
        state.opts = d;
        body.textContent = "";
        // Evenement : celui du jour preselectionne
        if (d.events.length) state.event = d.events[0];
        body.appendChild(sectionEvents(d.events, check));
        if (d.zones.length) body.appendChild(sectionZones(d.zones, check));
        else if (d.zones_configured) body.appendChild(el("p", "operator-post-note", "La couche de zones ne contient aucune zone exploitable : prise de poste sans zone."));
        if (d.metiers.length) body.appendChild(sectionMetiers(d.metiers, check));
        check();
        if (state.map) setTimeout(function () { fitMap(); }, 50);
      })
      .catch(function (e) {
        body.textContent = "";
        body.appendChild(el("p", "operator-post-error", "Chargement impossible (" + e.message + "). Rechargez la page."));
      });

    go.addEventListener("click", function () {
      if (go.disabled) return;
      state.busy = true;
      check();
      go.textContent = "Prise de poste...";
      fetch("/api/operator/post", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify({ event: state.event.event, year: state.event.year,
                               zones: Object.keys(state.zones), metier_id: state.metier })
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d, status: r.status }; });
      }).then(function (res) {
        if (!res.ok || !res.d.ok) {
          state.busy = false;
          go.textContent = "Prendre mon poste";
          check();
          err.textContent = ERRORS[res.d && res.d.error] || ("Prise de poste refusee (" + ((res.d && res.d.error) || res.status) + ")");
          return;
        }
        overlay.remove();
        document.body.classList.remove("operator-post-open");
        applyPost(res.d.post);
        try { sessionStorage.setItem("operator_post_seen", res.d.post.id); } catch (e) {}
        toast("success", res.d.created ? "Poste pris : " + res.d.post.event + " " + res.d.post.year
                                       : "Poste deja ouvert : repris");
      }).catch(function (e) {
        state.busy = false;
        go.textContent = "Prendre mon poste";
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

  function sectionEvents(events, check) {
    var s = section("Evenement", "event");
    var list = el("div", "operator-post-options");
    events.forEach(function (ev, i) {
      var l = el("label", "operator-post-option" + (i === 0 ? " is-on" : ""));
      var r = document.createElement("input");
      r.type = "radio"; r.name = "op-event"; r.checked = i === 0;
      r.addEventListener("change", function () {
        state.event = ev;
        list.querySelectorAll(".operator-post-option").forEach(function (x) { x.classList.remove("is-on"); });
        l.classList.add("is-on");
        check();
      });
      l.appendChild(r);
      l.appendChild(el("span", "operator-post-option-main", ev.event + " " + ev.year));
      if (ev.kind === "epreuve" && ev.phase) l.appendChild(el("span", "operator-post-option-sub", ev.phase));
      list.appendChild(l);
    });
    s.appendChild(list);
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
    function set(id, on) {
      if (on) state.zones[id] = true; else delete state.zones[id];
      if (boxes[id]) { boxes[id].input.checked = on; boxes[id].row.classList.toggle("is-on", on); }
      if (state.polys[id]) state.polys[id].setStyle(styleFor(on));
      var n = Object.keys(state.zones).length;
      count.textContent = n ? n + " zone" + (n > 1 ? "s" : "") + " choisie" + (n > 1 ? "s" : "") : "Aucune zone choisie";
      check();
    }
    zones.forEach(function (z) {
      var row = el("label", "operator-post-zone");
      var c = document.createElement("input");
      c.type = "checkbox";
      c.addEventListener("change", function () { set(z.id, c.checked); });
      row.appendChild(c);
      row.appendChild(el("span", null, z.label));
      list.appendChild(row);
      boxes[z.id] = { input: c, row: row, key: norm(z.label) };
    });
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
          var p = L.geoJSON(z.geometry, { style: styleFor(false) });
          p.bindTooltip(z.label, { sticky: true });
          p.on("click", function () { set(z.id, !state.zones[z.id]); });
          p.addTo(group);
          state.polys[z.id] = p;
        });
        state.group = group;
        fitMap();
      }, 0);
    }
    set("__none__", false);
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

  function fitMap() {
    if (!state.map) return;
    state.map.invalidateSize();
    if (state.group && state.group.getLayers().length) state.map.fitBounds(state.group.getBounds(), { padding: [10, 10] });
  }

  function sectionMetiers(metiers, check) {
    var s = section("Metier", "work");
    var list = el("div", "operator-post-options");
    metiers.forEach(function (m) {
      var l = el("label", "operator-post-option");
      var r = document.createElement("input");
      r.type = "radio"; r.name = "op-metier";
      r.addEventListener("change", function () {
        state.metier = m.id;
        list.querySelectorAll(".operator-post-option").forEach(function (x) { x.classList.remove("is-on"); });
        l.classList.add("is-on");
        check();
      });
      l.appendChild(r);
      l.appendChild(el("span", "operator-post-option-main", m.label));
      list.appendChild(l);
    });
    s.appendChild(list);
    return s;
  }

  // --- Demarrage -----------------------------------------------------------
  function start() {
    var post = window.__operatorPost;
    if (post) {
      applyPost(post);
      // "Poste repris" une fois par session de navigateur
      var seen = null;
      try { seen = sessionStorage.getItem("operator_post_seen"); } catch (e) {}
      if (seen !== post.id) {
        toast("info", "Poste repris : " + post.event + " " + post.year
          + ((post.zones_labels || []).length ? " - " + post.zones_labels.join(", ") : ""));
        try { sessionStorage.setItem("operator_post_seen", post.id); } catch (e) {}
      }
      return;
    }
    openModal();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
