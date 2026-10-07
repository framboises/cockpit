/* Constats terrain (/declarations) - declarations.py
 *
 * Liste + carte des constats deposes par les tablettes Field en mode
 * declarant. Un operateur du droit "Traiter les constats terrain" les suit,
 * annote, classe, ou les transforme en fiche d'intervention avec l'assistant
 * de creation habituel (window.PcorgCreate, pre-rempli) ; la fiche creee est
 * ensuite rattachee au constat (POST /api/declarations/<id>/link).
 */
// pcorg.js (mode creation seule) lit l'evenement de la fiche via
// getCurrentEventYear(), defini par main.js sur l'accueil (absent ici).
if (typeof window.getCurrentEventYear !== "function") {
  window.getCurrentEventYear = function () {
    return { event: window.selectedEvent || "", year: String(window.selectedYear || "") };
  };
}

(function () {
  "use strict";

  var REFRESH_MS = 15000;
  var STATUS_META = {
    nouvelle:    { label: "Nouveau",   color: "#dc2626", icon: "fiber_new" },
    en_suivi:    { label: "En suivi",  color: "#d97706", icon: "visibility" },
    transformee: { label: "Transforme en fiche", color: "#2563eb", icon: "assignment_turned_in" },
    classee:     { label: "Classe",    color: "#64748b", icon: "archive" },
  };
  var PRIORITY_META = {
    basse:   { label: "Peut attendre", color: "#64748b" },
    normale: { label: "A traiter rapidement", color: "#eab308" },
    haute:   { label: "Urgent", color: "#f97316" },
  };

  var state = { status: "a_traiter", q: "", days: 30, items: [], map: null, layer: null,
                markers: {}, timer: null, openId: null, loading: false };
  var canTreat = window.__dcCanTreat === true;

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function icon(name) { return el("span", "material-symbols-outlined", name); }
  function toast(type, msg) { if (window.showToast) window.showToast(type, msg); }

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  function post(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        d._status = r.status;
        return d;
      });
    }).catch(function () { return { ok: false, error: "reseau" }; });
  }

  var ERRORS = {
    forbidden: "Votre groupe ne permet pas de traiter les constats.",
    motif_requis: "Un motif est obligatoire pour classer.",
    etat_incompatible: "Le constat a change d'etat entre-temps, la liste est rechargee.",
    fiche_non_creee_par_vous: "La fiche doit etre creee par vous.",
    fiche_deja_rattachee: "Cette fiche est deja rattachee a un autre constat.",
    a_finaliser: "L'envoi des constats par mail n'est pas encore active.",
  };
  function errMsg(d) { return ERRORS[d && d.error] || ("Erreur : " + ((d && d.error) || "?")); }

  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    var now = new Date();
    var hm = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === now.toDateString()) return "Aujourd'hui " + hm;
    var y = new Date(now); y.setDate(now.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return "Hier " + hm;
    return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" }) + " " + hm;
  }

  // ---------------------------------------------------------------- donnees
  function load() {
    if (state.loading) return;
    state.loading = true;
    var qs = "?status=" + encodeURIComponent(state.status) + "&days=" + state.days +
             (state.q ? "&q=" + encodeURIComponent(state.q) : "");
    fetch("/api/declarations" + qs, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.loading = false;
        if (!d || !d.ok) { setLive(false); return; }
        canTreat = !!d.can_treat;
        state.items = d.declarations || [];
        var c = d.counts || {};
        $("dc-c-a_traiter").textContent = (c.nouvelle || 0) + (c.en_suivi || 0);
        $("dc-c-transformee").textContent = c.transformee || 0;
        $("dc-c-classee").textContent = c.classee || 0;
        renderList();
        renderMap();
        setLive(true);
      })
      .catch(function () { state.loading = false; setLive(false); });
  }

  function setLive(ok) {
    var t = $("dc-live-text");
    if (t) t.textContent = ok ? new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "hors ligne";
    var w = $("dc-live");
    if (w) w.classList.toggle("is-off", !ok);
  }

  // ----------------------------------------------------------------- liste
  function statusChip(d) {
    var m = STATUS_META[d.status] || STATUS_META.nouvelle;
    var chip = el("span", "dc-chip");
    chip.style.setProperty("--c", m.color);
    var label = m.label;
    if (d.status === "transformee" && d.fiche) label = d.fiche.closed ? "Fiche close" : "Fiche en cours";
    chip.textContent = label;
    return chip;
  }

  function renderList() {
    var list = $("dc-list");
    while (list.firstChild) list.removeChild(list.firstChild);
    if (!state.items.length) {
      var empty = el("div", "dc-empty");
      empty.appendChild(icon("inbox"));
      empty.appendChild(el("div", "", state.q ? "Aucun constat ne correspond a la recherche." : "Aucun constat."));
      list.appendChild(empty);
      return;
    }
    state.items.forEach(function (d) {
      var card = el("button", "dc-card");
      card.type = "button";
      card.dataset.id = d.id;
      if (d.id === state.openId) card.classList.add("is-open");

      var thumb = el("div", "dc-thumb");
      if (d.photos && d.photos.length) {
        var img = el("img");
        img.src = d.photos[0].thumb || d.photos[0].photo;
        img.alt = "";
        img.loading = "lazy";
        thumb.appendChild(img);
        if (d.photos.length > 1) thumb.appendChild(el("span", "dc-thumb-n", "+" + (d.photos.length - 1)));
      } else {
        thumb.appendChild(icon("no_photography"));
      }
      card.appendChild(thumb);

      var body = el("div", "dc-card-body");
      var top = el("div", "dc-card-top");
      top.appendChild(el("span", "dc-ref", d.ref || ""));
      var pm = PRIORITY_META[d.priority] || PRIORITY_META.normale;
      var pr = el("span", "dc-prio", pm.label);
      pr.style.setProperty("--c", pm.color);
      top.appendChild(pr);
      top.appendChild(statusChip(d));
      body.appendChild(top);
      body.appendChild(el("div", "dc-text", d.text));
      var meta = el("div", "dc-meta");
      meta.appendChild(el("span", "", fmtDate(d.created_at)));
      meta.appendChild(el("span", "", d.device_name || ""));
      if (d.group_label) meta.appendChild(el("span", "", d.group_label));
      if (d.carroye) meta.appendChild(el("span", "dc-carroye", d.carroye));
      body.appendChild(meta);
      card.appendChild(body);
      card.addEventListener("click", function () { openDetail(d.id); });
      list.appendChild(card);
    });
  }

  // ------------------------------------------------------------------ carte
  function initMap() {
    if (!window.L || !$("dc-map")) return;
    state.map = L.map("dc-map", { zoomControl: true, attributionControl: false })
      .setView([47.95, 0.22], 14);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 20, maxNativeZoom: 19 })
      .addTo(state.map);
    state.layer = L.layerGroup().addTo(state.map);
  }

  function renderMap() {
    if (!state.map) return;
    state.layer.clearLayers();
    state.markers = {};
    var pts = [];
    state.items.forEach(function (d) {
      if (d.lat == null || d.lng == null) return;
      var m = STATUS_META[d.status] || STATUS_META.nouvelle;
      var mk = L.circleMarker([d.lat, d.lng], {
        radius: d.id === state.openId ? 11 : 8, color: "#fff", weight: 2,
        fillColor: m.color, fillOpacity: 0.95,
      }).addTo(state.layer);
      // Un noeud et non une chaine : Leaflet injecte une chaine en innerHTML,
      // et le texte vient d'une tablette declarant (XSS stockee sinon).
      var tip = document.createElement("span");
      tip.textContent = (d.ref || "") + " - " + (d.text || "").slice(0, 60);
      mk.bindTooltip(tip);
      mk.on("click", function () { openDetail(d.id); });
      state.markers[d.id] = mk;
      pts.push([d.lat, d.lng]);
    });
    if (pts.length && !state.fitted) {
      state.fitted = true;
      state.map.fitBounds(pts, { padding: [30, 30], maxZoom: 17 });
    }
  }

  // ----------------------------------------------------------------- detail
  function closeDetail() {
    $("dc-detail").hidden = true;
    state.openId = null;
    renderList();
    renderMap();
  }

  function openDetail(id) {
    state.openId = id;
    var ov = $("dc-detail");
    var body = $("dc-d-body");
    while (body.firstChild) body.removeChild(body.firstChild);
    body.appendChild(el("div", "dc-loading", "Chargement..."));
    $("dc-d-foot").textContent = "";
    ov.hidden = false;
    renderList();
    renderMap();
    var mk = state.markers[id];
    if (mk && state.map) state.map.panTo(mk.getLatLng());
    fetch("/api/declarations/" + encodeURIComponent(id), { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) { body.textContent = "Constat introuvable."; return; }
        if (state.openId !== id) return;
        renderDetail(d);
      })
      .catch(function () { body.textContent = "Erreur de chargement."; });
  }

  function section(title) { return el("div", "dc-sec", title); }

  function renderDetail(d) {
    canTreat = !!d.can_treat;
    $("dc-d-title").textContent = "Constat " + (d.ref || "");
    $("dc-d-sub").textContent = fmtDate(d.created_at) + " - " + (d.device_name || "") +
      (d.group_label ? " (" + d.group_label + ")" : "");
    var body = $("dc-d-body");
    while (body.firstChild) body.removeChild(body.firstChild);

    var head = el("div", "dc-d-head");
    head.appendChild(statusChip(d));
    var pm = PRIORITY_META[d.priority] || PRIORITY_META.normale;
    var pr = el("span", "dc-prio", pm.label);
    pr.style.setProperty("--c", pm.color);
    head.appendChild(pr);
    if (d.carroye) head.appendChild(el("span", "dc-carroye", d.carroye));
    body.appendChild(head);

    body.appendChild(el("div", "dc-d-text", d.text));

    if (d.photos && d.photos.length) {
      body.appendChild(section("Photos (" + d.photos.length + ")"));
      var gal = el("div", "dc-gallery");
      d.photos.forEach(function (p) {
        var b = el("button", "dc-gal-item");
        b.type = "button";
        var img = el("img");
        img.src = p.thumb || p.photo;
        img.alt = "Photo du constat";
        b.appendChild(img);
        b.addEventListener("click", function () { openLightbox(p.photo); });
        gal.appendChild(b);
      });
      body.appendChild(gal);
    }

    if (d.fiche) {
      body.appendChild(section("Fiche d'intervention"));
      var fc = el("div", "dc-fiche");
      fc.appendChild(icon(d.fiche.closed ? "task_alt" : "assignment"));
      var ft = el("div");
      ft.appendChild(el("strong", "", (d.fiche.category || "").replace("PCO.", "") +
        (d.fiche.niveau_urgence ? " - " + d.fiche.niveau_urgence : "")));
      ft.appendChild(el("div", "dc-muted", d.fiche.closed
        ? "Close " + fmtDate(d.fiche.close_ts)
        : "En cours" + (d.fiche.unit ? " - unite " + d.fiche.unit : "")));
      fc.appendChild(ft);
      body.appendChild(fc);
    }
    if (d.status === "classee" && d.classed_reason) {
      body.appendChild(section("Classe sans suite"));
      body.appendChild(el("div", "dc-d-text", d.classed_reason));
    }

    body.appendChild(section("Chronologie"));
    var tl = el("div", "dc-timeline");
    (d.history || []).forEach(function (h) {
      var row = el("div", "dc-tl-row dc-tl-" + (h.origin || "cockpit"));
      var meta = el("div", "dc-tl-meta", fmtDate(h.ts) + " - " + (h.by || "").replace(/^field:/, "Tablette "));
      row.appendChild(meta);
      if (h.text) row.appendChild(el("div", "dc-tl-text", h.text));
      if (h.photos && h.photos.length) {
        var g = el("div", "dc-tl-photos");
        h.photos.forEach(function (p) {
          var img = el("img");
          img.src = p.thumb || p.photo;
          img.alt = "";
          img.addEventListener("click", function () { openLightbox(p.photo); });
          g.appendChild(img);
        });
        row.appendChild(g);
      }
      tl.appendChild(row);
    });
    body.appendChild(tl);

    if (d.lat != null) {
      var pos = el("div", "dc-muted dc-pos", "Position : " + d.lat.toFixed(5) + ", " + d.lng.toFixed(5));
      body.appendChild(pos);
    }

    renderActions(d);
  }

  function btn(label, iconName, cls, onClick) {
    var b = el("button", "btn " + (cls || "btn-secondary") + " dc-btn");
    b.type = "button";
    if (iconName) b.appendChild(icon(iconName));
    b.appendChild(document.createTextNode(" " + label));
    b.addEventListener("click", onClick);
    return b;
  }

  function renderActions(d) {
    var foot = $("dc-d-foot");
    foot.textContent = "";
    var left = el("div", "dc-foot-left");
    left.appendChild(btn("Apercu du constat", "mail", "btn-secondary", function () {
      window.open("/api/declarations/" + encodeURIComponent(d.id) + "/report/preview", "_blank", "noopener");
    }));
    foot.appendChild(left);
    if (!canTreat) return;
    var right = el("div", "dc-foot-right");
    var open = d.status === "nouvelle" || d.status === "en_suivi";
    if (open || d.status === "transformee") {
      right.appendChild(btn("Note", "edit_note", "btn-secondary", function () { addNote(d); }));
    }
    if (d.status === "nouvelle") {
      right.appendChild(btn("Prendre en suivi", "visibility", "btn-secondary", function () { setStatus(d, "en_suivi"); }));
    }
    if (open) {
      right.appendChild(btn("Classer", "archive", "btn-secondary", function () { classify(d); }));
      if (window.__userCanCreateFiche !== false) {
        right.appendChild(btn("Transformer en fiche", "assignment_add", "btn-primary", function () { convert(d); }));
      }
    }
    if (d.status === "classee") {
      right.appendChild(btn("Rouvrir", "unarchive", "btn-secondary", function () { setStatus(d, "en_suivi"); }));
    }
    foot.appendChild(right);
  }

  function after(d, res, okMsg) {
    if (res && res.ok) {
      if (okMsg) toast("success", okMsg);
    } else {
      toast("error", errMsg(res));
    }
    load();
    if (state.openId === d.id) openDetail(d.id);
  }

  function setStatus(d, status) {
    post("/api/declarations/" + encodeURIComponent(d.id) + "/status", { status: status })
      .then(function (r) { after(d, r, status === "en_suivi" ? "Constat pris en suivi" : null); });
  }

  function classify(d) {
    var ask = window.showPromptToast
      ? window.showPromptToast("Motif du classement sans suite", { okLabel: "Classer", type: "warning" })
      : Promise.resolve(null);
    ask.then(function (reason) {
      if (reason == null) return;
      reason = String(reason).trim();
      if (reason.length < 3) { toast("warning", ERRORS.motif_requis); return; }
      post("/api/declarations/" + encodeURIComponent(d.id) + "/status", { status: "classee", reason: reason })
        .then(function (r) { after(d, r, "Constat classe"); });
    });
  }

  function addNote(d) {
    var ask = window.showPromptToast
      ? window.showPromptToast("Note interne (non visible du declarant)", { okLabel: "Ajouter" })
      : Promise.resolve(null);
    ask.then(function (text) {
      if (text == null || !String(text).trim()) return;
      post("/api/declarations/" + encodeURIComponent(d.id) + "/note", { text: String(text).trim() })
        .then(function (r) { after(d, r, "Note ajoutee"); });
    });
  }

  function convert(d) {
    if (!window.PcorgCreate) { toast("error", "Assistant de creation indisponible."); return; }
    var pf = d.prefill || {};
    // Evenement de la fiche = celui du constat (epreuve active ou SAISON)
    window.selectedEvent = pf.event || window.selectedEvent;
    window.selectedYear = pf.year || window.selectedYear;
    $("dc-detail").hidden = true;
    var opened = window.PcorgCreate.open({
      prefill: { lat: pf.lat, lon: pf.lon, text: pf.text, urgency: pf.urgency },
      onCreated: function (ficheId) {
        if (!ficheId) return;
        post("/api/declarations/" + encodeURIComponent(d.id) + "/link", { fiche_id: ficheId })
          .then(function (r) {
            after(d, r, "Fiche creee et rattachee au constat " + (d.ref || ""));
          });
      },
    });
    if (!opened) { toast("error", "Assistant de creation indisponible."); $("dc-detail").hidden = false; }
  }

  // --------------------------------------------------------------- lightbox
  function openLightbox(src) {
    $("dc-lightbox-img").src = src;
    $("dc-lightbox").hidden = false;
  }
  function closeLightbox() {
    $("dc-lightbox").hidden = true;
    $("dc-lightbox-img").removeAttribute("src");
  }

  // ------------------------------------------------------------------ init
  function wire() {
    document.querySelectorAll("#dc-tabs .dc-tab").forEach(function (t) {
      t.addEventListener("click", function () {
        document.querySelectorAll("#dc-tabs .dc-tab").forEach(function (x) { x.classList.toggle("active", x === t); });
        state.status = t.dataset.status;
        state.fitted = false;
        load();
      });
    });
    var qTimer = null;
    $("dc-q").addEventListener("input", function (e) {
      clearTimeout(qTimer);
      qTimer = setTimeout(function () { state.q = e.target.value.trim(); state.fitted = false; load(); }, 350);
    });
    $("dc-days").addEventListener("change", function (e) {
      state.days = parseInt(e.target.value, 10) || 0;
      state.fitted = false;
      load();
    });
    $("dc-refresh").addEventListener("click", load);
    document.querySelectorAll("#dc-detail [data-close]").forEach(function (b) { b.addEventListener("click", closeDetail); });
    $("dc-detail").addEventListener("click", function (e) { if (e.target === $("dc-detail")) closeDetail(); });
    $("dc-lightbox").addEventListener("click", closeLightbox);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (!$("dc-lightbox").hidden) { closeLightbox(); return; }
      if (!$("dc-detail").hidden) closeDetail();
    });
    document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    initMap();
    load();
    state.timer = setInterval(function () { if (!document.hidden) load(); }, REFRESH_MS);
  });
})();
