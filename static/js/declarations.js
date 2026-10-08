/* Constats terrain (/declarations) - declarations.py
 *
 * Liste (a gauche) + carte (tout le reste) des constats deposes par les
 * tablettes Field en mode declarant ; detail dans un panneau lateral
 * par-dessus la carte. Un operateur du droit "Traiter les constats terrain"
 * accuse reception (le declarant voit "Vu par le PC"), ajoute des notes
 * internes, classe sans suite, ou transforme le constat en fiche
 * d'intervention avec l'assistant habituel (window.PcorgCreate) : il choisit
 * d'abord l'evenement (epreuve en cours ou SAISON selon quand le sujet sera
 * traite) ; source pre-remplie Externe / groupe declarant / canal
 * Application. La fiche creee est rattachee au constat
 * (POST /api/declarations/<id>/link) et recoit ses photos.
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
    nouvelle:    { label: "Nouveau",  color: "#dc2626" },
    en_suivi:    { label: "Recu",     color: "#d97706" },
    transformee: { label: "En fiche", color: "#2563eb" },
    classee:     { label: "Classe",   color: "#64748b" },
  };
  var PRIORITY_META = {
    basse:   { label: "Peut attendre", color: "#64748b" },
    normale: { label: "Rapide", color: "#ca8a04" },
    haute:   { label: "Urgent", color: "#ea580c" },
  };

  var state = { status: "a_traiter", q: "", days: 30, items: [], map: null, layer: null,
                markers: {}, openId: null, detail: null, mode: null, loading: false,
                lightbox: { list: [], i: 0 } };
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
      return r.json().catch(function () { return {}; }).then(function (d) { d._status = r.status; return d; });
    }).catch(function () { return { ok: false, error: "reseau" }; });
  }

  var ERRORS = {
    forbidden: "Votre groupe ne permet pas de traiter les constats.",
    motif_requis: "Indiquez le motif du classement.",
    empty_text: "Le texte est vide.",
    classee: "Le constat est classe : rouvrez-le pour ecrire au declarant.",
    etat_incompatible: "Le constat a change d'etat entre-temps.",
    fiche_non_creee_par_vous: "La fiche doit etre creee par vous.",
    fiche_deja_rattachee: "Cette fiche est deja rattachee a un autre constat.",
    reseau: "Erreur reseau.",
  };
  function errMsg(d) { return ERRORS[d && d.error] || ("Erreur : " + ((d && d.error) || "?")); }

  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso), now = new Date();
    var hm = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === now.toDateString()) return "Aujourd'hui " + hm;
    var y = new Date(now); y.setDate(now.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return "Hier " + hm;
    return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" }) + " " + hm;
  }
  function eventLabel(e) { return (e.event || "") + " " + (e.year || ""); }

  // ---------------------------------------------------------------- donnees
  function load() {
    if (state.loading) return;
    state.loading = true;
    var qs = "?status=" + encodeURIComponent(state.status) + "&days=" + state.days +
             (state.q ? "&q=" + encodeURIComponent(state.q) : "") +
             (state.event ? "&event=" + encodeURIComponent(state.event.event) + "&year=" + state.event.year : "");
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
        var sub = [];
        if (c.nouvelle) sub.push(c.nouvelle + " nouveau" + (c.nouvelle > 1 ? "x" : ""));
        if (c.reponses) sub.push(c.reponses + " reponse" + (c.reponses > 1 ? "s" : ""));
        $("dc-header-sub").textContent = sub.join(" - ");
        renderEventFilter(d.events || []);
        renderList();
        renderMap();
        setLive(true);
      })
      .catch(function () { state.loading = false; setLive(false); });
  }

  // Filtre evenement : evenements presents dans les constats (appairage des
  // tablettes), la selection est conservee d'un rafraichissement a l'autre.
  function renderEventFilter(events) {
    var sel = $("dc-event");
    var cur = state.event ? state.event.event + "|" + state.event.year : "";
    var sig = events.map(function (e) { return e.event + "|" + e.year + "|" + e.count; }).join(",");
    if (sel._sig === sig) return;
    sel._sig = sig;
    while (sel.options.length > 1) sel.remove(1);
    events.forEach(function (e) {
      var o = el("option", "", eventLabel(e) + " (" + e.count + ")");
      o.value = e.event + "|" + e.year;
      sel.appendChild(o);
    });
    sel.value = cur;
  }

  function setLive(ok) {
    $("dc-live-text").textContent = ok
      ? new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }) : "hors ligne";
    $("dc-live").classList.toggle("is-off", !ok);
  }

  // ----------------------------------------------------------------- liste
  function chip(text, color, cls) {
    var c = el("span", "dc-chip" + (cls ? " " + cls : ""), text);
    c.style.setProperty("--c", color);
    return c;
  }
  function statusChip(d) {
    var m = STATUS_META[d.status] || STATUS_META.nouvelle;
    var label = m.label;
    if (d.status === "transformee" && d.fiche) label = d.fiche.closed ? "Fiche close" : "Fiche en cours";
    return chip(label, d.status === "transformee" && d.fiche && d.fiche.closed ? "#16a34a" : m.color);
  }

  function renderList() {
    var list = $("dc-list");
    list.textContent = "";
    if (!state.items.length) {
      var empty = el("div", "dc-empty");
      empty.appendChild(icon("task_alt"));
      empty.appendChild(el("div", "", state.q ? "Aucun constat ne correspond." : "Aucun constat dans cette vue."));
      list.appendChild(empty);
      return;
    }
    state.items.forEach(function (d) {
      var card = el("button", "dc-card");
      card.type = "button";
      if (d.id === state.openId) card.classList.add("is-open");
      if (d.status === "nouvelle") card.classList.add("is-new");

      var thumb = el("div", "dc-thumb");
      if (d.photos && d.photos.length) {
        var img = el("img");
        img.src = d.photos[0].thumb || d.photos[0].photo;
        img.alt = "";
        img.loading = "lazy";
        thumb.appendChild(img);
        if (d.photos.length > 1) thumb.appendChild(el("span", "dc-thumb-n", String(d.photos.length)));
      } else {
        thumb.appendChild(icon("no_photography"));
      }
      card.appendChild(thumb);

      var body = el("div", "dc-card-body");
      var top = el("div", "dc-card-top");
      top.appendChild(el("span", "dc-ref", d.ref || ""));
      top.appendChild(statusChip(d));
      if (d.priority === "haute") top.appendChild(chip("Urgent", PRIORITY_META.haute.color));
      if (d.cockpit_unread) {
        top.appendChild(chip(d.cockpit_unread > 1 ? d.cockpit_unread + " reponses" : "Reponse", "#7c3aed"));
        card.classList.add("has-reply");
      } else if (d.field_unread) {
        top.appendChild(chip("Message non lu", "#64748b", "is-pending"));
      }
      top.appendChild(el("span", "dc-when", fmtDate(d.created_at)));
      body.appendChild(top);
      body.appendChild(el("div", "dc-text", d.text));
      var meta = el("div", "dc-meta");
      if (d.event) meta.appendChild(el("span", "dc-event-tag", eventLabel(d)));
      meta.appendChild(el("span", "", d.group_label || d.device_name || ""));
      if (d.carroye) meta.appendChild(el("span", "dc-carroye", d.carroye));
      if (d.lat == null) meta.appendChild(el("span", "dc-nopos", "sans position"));
      body.appendChild(meta);
      card.appendChild(body);
      card.addEventListener("click", function () { openDetail(d.id); });
      list.appendChild(card);
    });
  }

  // ------------------------------------------------------------------ carte
  function initMap() {
    if (!window.L || !$("dc-map")) return;
    state.map = L.map("dc-map", { zoomControl: false, attributionControl: false }).setView([47.95, 0.22], 14);
    L.control.zoom({ position: "bottomleft" }).addTo(state.map);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 20, maxNativeZoom: 19 })
      .addTo(state.map);
    state.layer = L.layerGroup().addTo(state.map);
    // La grille n'a pas sa taille definitive a l'init (police, barre laterale)
    setTimeout(function () { state.map.invalidateSize(); }, 300);
    if (window.ResizeObserver) {
      new ResizeObserver(function () { state.map.invalidateSize(); }).observe($("dc-map"));
    }
  }

  function renderMap() {
    if (!state.map) return;
    state.layer.clearLayers();
    state.markers = {};
    var pts = [];
    state.items.forEach(function (d) {
      if (d.lat == null || d.lng == null) return;
      var m = STATUS_META[d.status] || STATUS_META.nouvelle;
      var open = d.id === state.openId;
      var mk = L.circleMarker([d.lat, d.lng], {
        radius: open ? 12 : 8, color: open ? "#111827" : "#fff", weight: open ? 3 : 2,
        fillColor: m.color, fillOpacity: 0.95,
      }).addTo(state.layer);
      mk.bindTooltip((d.ref || "") + " - " + (d.text || "").slice(0, 60), { direction: "top" });
      mk.on("click", function () { openDetail(d.id); });
      state.markers[d.id] = mk;
      pts.push([d.lat, d.lng]);
    });
    if (pts.length && !state.fitted) {
      state.fitted = true;
      state.map.fitBounds(pts, { padding: [40, 40], maxZoom: 17 });
    }
  }

  // ----------------------------------------------------------------- detail
  function closeDetail() {
    $("dc-panel").hidden = true;
    state.openId = null;
    state.detail = null;
    state.mode = null;
    document.body.classList.remove("dc-panel-open");
    renderList();
    renderMap();
  }

  function openDetail(id, keepMode) {
    if (state.openId !== id) state.mode = null;
    if (!keepMode) state.mode = null;
    state.openId = id;
    var panel = $("dc-panel");
    panel.hidden = false;
    document.body.classList.add("dc-panel-open");
    if (!state.detail || state.detail.id !== id) {
      $("dc-p-body").textContent = "";
      $("dc-p-body").appendChild(el("div", "dc-loading", "Chargement..."));
      $("dc-p-foot").textContent = "";
    }
    renderList();
    renderMap();
    var mk = state.markers[id];
    if (mk && state.map) state.map.panTo(mk.getLatLng());
    fetch("/api/declarations/" + encodeURIComponent(id), { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (state.openId !== id) return;
        if (!d || !d.ok) { $("dc-p-body").textContent = "Constat introuvable."; return; }
        state.detail = d;
        renderDetail();
      })
      .catch(function () { $("dc-p-body").textContent = "Erreur de chargement."; });
  }

  // Rafraichissement du constat ouvert (reponse du declarant, changement
  // d'etat par un autre operateur) sans perdre le brouillon en cours.
  function detailSig(x) {
    return [x.status, (x.history || []).length, x.fiche && x.fiche.closed, x.field_unread].join("|");
  }
  function refreshDetail() {
    var id = state.openId;
    if (!id || state.mode || document.hidden || !state.detail) return;
    fetch("/api/declarations/" + encodeURIComponent(id), { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (state.openId !== id || state.mode || !d || !d.ok) return;
        if (state.detail && detailSig(state.detail) === detailSig(d)) return;
        var ta = document.querySelector("#dc-p-foot .dc-note-input");
        var draft = ta ? ta.value : "";
        var focused = ta && document.activeElement === ta;
        var body = $("dc-p-body");
        var atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 60;
        state.detail = d;
        renderDetail();
        var nta = document.querySelector("#dc-p-foot .dc-note-input");
        if (nta && draft) nta.value = draft;
        if (nta && focused) nta.focus();
        if (atBottom) body.scrollTop = body.scrollHeight;
      })
      .catch(function () {});
  }

  function section(title) { return el("div", "dc-sec", title); }

  function photoGrid(photos, cls) {
    var gal = el("div", cls || "dc-gallery");
    photos.forEach(function (p, i) {
      var b = el("button", "dc-gal-item");
      b.type = "button";
      var img = el("img");
      img.src = p.thumb || p.photo;
      img.alt = "Photo " + (i + 1);
      b.appendChild(img);
      b.addEventListener("click", function () { openLightbox(photos.map(function (x) { return x.photo; }), i); });
      gal.appendChild(b);
    });
    return gal;
  }

  function renderDetail() {
    var d = state.detail;
    canTreat = !!d.can_treat;
    $("dc-p-title").textContent = d.ref || "Constat";
    $("dc-p-sub").textContent = fmtDate(d.created_at) + " - " + (d.device_name || "") +
      (d.group_label ? " (" + d.group_label + ")" : "");
    var body = $("dc-p-body");
    body.textContent = "";

    var head = el("div", "dc-d-head");
    if (d.event) head.appendChild(chip(eventLabel(d), "#0f766e"));
    head.appendChild(statusChip(d));
    var pm = PRIORITY_META[d.priority] || PRIORITY_META.normale;
    head.appendChild(chip(pm.label, pm.color));
    if (d.carroye) head.appendChild(chip(d.carroye, "#334155"));
    if (d.lat == null) head.appendChild(chip("Sans position", "#94a3b8"));
    body.appendChild(head);

    body.appendChild(el("div", "dc-d-text", d.text));
    if (d.photos && d.photos.length) body.appendChild(photoGrid(d.photos));

    if (d.fiche) {
      var fc = el("div", "dc-fiche" + (d.fiche.closed ? " is-closed" : ""));
      fc.appendChild(icon(d.fiche.closed ? "task_alt" : "assignment"));
      var ft = el("div");
      ft.appendChild(el("strong", "", "Fiche " + (d.fiche.category || "").replace("PCO.", "") +
        (d.fiche.niveau_urgence ? " " + d.fiche.niveau_urgence : "") + " - " + eventLabel(d.fiche)));
      ft.appendChild(el("div", "dc-muted", d.fiche.closed
        ? "Close " + fmtDate(d.fiche.close_ts)
        : "En cours" + (d.fiche.unit ? " - " + d.fiche.unit : "")));
      fc.appendChild(ft);
      body.appendChild(fc);
    }
    if (d.status === "classee" && d.classed_reason) {
      var cl = el("div", "dc-fiche is-classed");
      cl.appendChild(icon("archive"));
      cl.appendChild(el("div", "", "Classe sans suite : " + d.classed_reason));
      body.appendChild(cl);
    }

    body.appendChild(section("Chronologie"));
    var tl = el("div", "dc-timeline");
    (d.history || []).forEach(function (h) {
      var row = el("div", "dc-tl-row dc-tl-" + (h.origin || "cockpit") +
        (h.kind === "note" ? " is-note" : "") + (h.kind === "message" ? " is-message" : "") +
        (h.origin === "field" && h.kind === "complement" ? " is-reply" : ""));
      var meta = el("div", "dc-tl-meta");
      meta.appendChild(el("span", "", fmtDate(h.ts)));
      meta.appendChild(el("span", "dc-tl-by", (h.by || "").replace(/^field:/, "Tablette ")));
      if (h.kind === "note") meta.appendChild(el("span", "dc-tl-tag", "note interne"));
      if (h.kind === "message") meta.appendChild(el("span", "dc-tl-tag is-msg", "message au declarant"));
      if (h.origin === "field" && h.kind === "complement") meta.appendChild(el("span", "dc-tl-tag is-reply", "reponse du declarant"));
      row.appendChild(meta);
      if (h.text) row.appendChild(el("div", "dc-tl-text", h.text));
      if (h.photos && h.photos.length && h.kind !== "declaration") row.appendChild(photoGrid(h.photos, "dc-tl-photos"));
      tl.appendChild(row);
    });
    body.appendChild(tl);

    renderFoot();
  }

  // Pied du panneau : note interne toujours visible (si droit), puis les
  // actions ; "Classer" et "Transformer" ouvrent un formulaire en place.
  function renderFoot() {
    var d = state.detail;
    var foot = $("dc-p-foot");
    foot.textContent = "";
    if (!canTreat) return;
    var open = d.status === "nouvelle" || d.status === "en_suivi";

    if (state.mode === "classer") { foot.appendChild(classForm(d)); return; }
    if (state.mode === "event") { foot.appendChild(eventForm(d)); return; }

    if (d.status !== "classee") {
      // Un seul champ : message au declarant (notification sur sa tablette,
      // il repond depuis son constat) ou note interne du PC.
      var kind = state.composeKind || "message";
      var compose = el("div", "dc-compose");
      var sw = el("div", "dc-compose-kind");
      [["message", "Au declarant", "chat"], ["note", "Note interne", "edit_note"]].forEach(function (k) {
        var b = el("button", k[0] === kind ? "active" : "");
        b.type = "button";
        b.appendChild(icon(k[2]));
        b.appendChild(el("span", "", k[1]));
        b.addEventListener("click", function () {
          state.composeKind = k[0];
          var keep = ta.value;
          renderFoot();
          var nta = document.querySelector("#dc-p-foot .dc-note-input");
          if (nta) { nta.value = keep; nta.focus(); }
        });
        sw.appendChild(b);
      });
      compose.appendChild(sw);
      var note = el("div", "dc-note" + (kind === "message" ? " is-message" : ""));
      var ta = el("textarea", "dc-note-input");
      ta.rows = 1;
      ta.placeholder = kind === "message"
        ? "Message au declarant (notifie sur sa tablette)..."
        : "Note interne (non visible du declarant)...";
      ta.addEventListener("input", function () {
        ta.style.height = "auto";
        ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
      });
      var send = el("button", "dc-note-send");
      send.type = "button";
      send.title = kind === "message" ? "Envoyer au declarant" : "Ajouter la note";
      send.appendChild(icon("send"));
      function submitText() {
        var t = ta.value.trim();
        if (!t) return;
        send.disabled = true;
        post("/api/declarations/" + encodeURIComponent(d.id) + "/" + kind, { text: t }).then(function (r) {
          send.disabled = false;
          if (!r.ok) { toast("error", errMsg(r)); return; }
          if (kind === "message") {
            toast("success", r.pushed ? "Message envoye, le declarant est notifie"
              : "Message envoye (pas de notification possible : il le verra a l'ouverture de l'application)");
          }
          openDetail(d.id);
        });
      }
      send.addEventListener("click", submitText);
      ta.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submitText(); }
      });
      note.appendChild(ta);
      note.appendChild(send);
      compose.appendChild(note);
      foot.appendChild(compose);
    }

    var bar = el("div", "dc-actions");
    if (d.status === "nouvelle") {
      bar.appendChild(btn("Accuser reception", "done", "", function () { setStatus(d, "en_suivi"); },
        "Le declarant voit sur sa tablette que le PC a pris connaissance du constat"));
    }
    if (open) {
      bar.appendChild(btn("Classer", "archive", "", function () { state.mode = "classer"; renderFoot(); },
        "Classer sans suite (motif demande)"));
      if (window.__userCanCreateFiche !== false) {
        bar.appendChild(btn("Transformer en fiche", "assignment_add", "is-primary", function () { startConvert(d); },
          "Creer la fiche d'intervention a partir du constat"));
      }
    }
    if (d.status === "classee") {
      bar.appendChild(btn("Rouvrir", "unarchive", "", function () { setStatus(d, "en_suivi"); }));
    }
    if (bar.children.length) foot.appendChild(bar);
  }

  function btn(label, iconName, cls, onClick, title) {
    var b = el("button", "dc-btn " + (cls || ""));
    b.type = "button";
    if (title) b.title = title;
    if (iconName) b.appendChild(icon(iconName));
    b.appendChild(el("span", "", label));
    b.addEventListener("click", onClick);
    return b;
  }

  function classForm(d) {
    var box = el("div", "dc-inline-form");
    box.appendChild(el("div", "dc-inline-title", "Classer sans suite"));
    var ta = el("textarea", "dc-note-input");
    ta.rows = 2;
    ta.placeholder = "Motif (doublon, deja traite, hors perimetre...)";
    box.appendChild(ta);
    var bar = el("div", "dc-actions");
    bar.appendChild(btn("Annuler", null, "", function () { state.mode = null; renderFoot(); }));
    bar.appendChild(btn("Classer", "archive", "is-danger", function () {
      var reason = ta.value.trim();
      if (reason.length < 3) { ta.focus(); toast("warning", ERRORS.motif_requis); return; }
      post("/api/declarations/" + encodeURIComponent(d.id) + "/status", { status: "classee", reason: reason })
        .then(function (r) { state.mode = null; after(d, r, "Constat classe"); });
    }));
    box.appendChild(bar);
    setTimeout(function () { ta.focus(); }, 30);
    return box;
  }

  // Choix de l'evenement de la fiche avant l'assistant : epreuve en cours
  // (traite pendant l'epreuve) ou SAISON (traite en exploitation courante).
  function startConvert(d) {
    var choices = d.event_choices || [];
    if (choices.length <= 1) { openWizard(d, choices[0] || d.event_default); return; }
    state.mode = "event";
    renderFoot();
  }

  function eventForm(d) {
    var box = el("div", "dc-inline-form");
    box.appendChild(el("div", "dc-inline-title", "Sur quel evenement ouvrir la fiche ?"));
    box.appendChild(el("div", "dc-muted", "Selon le moment ou le sujet sera traite."));
    var def = d.event_default || {};
    var chosen = null;
    var list = el("div", "dc-choices");
    (d.event_choices || []).forEach(function (c) {
      var saison = c.kind === "saison";
      var b = el("button", "dc-choice");
      b.type = "button";
      b.appendChild(icon(saison ? "calendar_month" : "flag"));
      var t = el("div");
      t.appendChild(el("strong", "", eventLabel(c)));
      t.appendChild(el("div", "dc-muted", saison
        ? "Traite en exploitation courante, apres l'epreuve"
        : "Traite pendant l'epreuve" + (c.phase ? " (" + c.phase + ")" : "")));
      b.appendChild(t);
      b.addEventListener("click", function () {
        chosen = c;
        list.querySelectorAll(".dc-choice").forEach(function (x) { x.classList.toggle("selected", x === b); });
        go.disabled = false;
      });
      if (c.event === def.event && String(c.year) === String(def.year)) { chosen = c; b.classList.add("selected"); }
      list.appendChild(b);
    });
    box.appendChild(list);
    var bar = el("div", "dc-actions");
    bar.appendChild(btn("Annuler", null, "", function () { state.mode = null; renderFoot(); }));
    var go = btn("Continuer", "arrow_forward", "is-primary", function () {
      if (!chosen) return;
      state.mode = null;
      renderFoot();
      openWizard(d, chosen);
    });
    go.disabled = !chosen;
    bar.appendChild(go);
    box.appendChild(bar);
    return box;
  }

  function openWizard(d, ev) {
    if (!window.PcorgCreate) { toast("error", "Assistant de creation indisponible."); return; }
    var pf = d.prefill || {};
    window.selectedEvent = (ev && ev.event) || window.selectedEvent;
    window.selectedYear = (ev && ev.year) || window.selectedYear;
    var opened = window.PcorgCreate.open({
      prefill: { lat: pf.lat, lon: pf.lon, text: pf.text, urgency: pf.urgency,
                 source: pf.source, appelant: pf.appelant, canal: pf.canal },
      onCreated: function (ficheId) {
        if (!ficheId) return;
        post("/api/declarations/" + encodeURIComponent(d.id) + "/link", { fiche_id: ficheId })
          .then(function (r) { after(d, r, "Fiche creee sur " + eventLabel(ev || {}) + ", photos du constat jointes"); });
      },
    });
    if (!opened) toast("error", "Assistant de creation indisponible.");
  }

  function setStatus(d, status) {
    post("/api/declarations/" + encodeURIComponent(d.id) + "/status", { status: status })
      .then(function (r) { after(d, r, status === "en_suivi" ? "Reception accusee" : null); });
  }

  function after(d, res, okMsg) {
    if (res && res.ok) { if (okMsg) toast("success", okMsg); }
    else toast("error", errMsg(res));
    load();
    if (state.openId === d.id) openDetail(d.id);
  }

  // --------------------------------------------------------------- lightbox
  function openLightbox(list, i) {
    state.lightbox = { list: list, i: i };
    showLb();
    $("dc-lightbox").hidden = false;
  }
  function showLb() {
    var lb = state.lightbox;
    $("dc-lightbox-img").src = lb.list[lb.i];
    $("dc-lb-prev").hidden = lb.list.length < 2;
    $("dc-lb-next").hidden = lb.list.length < 2;
  }
  function stepLb(k) {
    var lb = state.lightbox;
    if (lb.list.length < 2) return;
    lb.i = (lb.i + k + lb.list.length) % lb.list.length;
    showLb();
  }
  function closeLightbox() {
    $("dc-lightbox").hidden = true;
    $("dc-lightbox-img").removeAttribute("src");
  }

  // ------------------------------------------------------------------ init
  function wire() {
    document.querySelectorAll("#dc-tabs .dc-seg-btn").forEach(function (t) {
      t.addEventListener("click", function () {
        document.querySelectorAll("#dc-tabs .dc-seg-btn").forEach(function (x) { x.classList.toggle("active", x === t); });
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
    $("dc-event").addEventListener("change", function (e) {
      var v = e.target.value;
      state.event = v ? { event: v.split("|")[0], year: v.split("|")[1] } : null;
      state.fitted = false;
      load();
    });
    $("dc-days").addEventListener("change", function (e) {
      state.days = parseInt(e.target.value, 10) || 0;
      state.fitted = false;
      load();
    });
    $("dc-refresh").addEventListener("click", load);
    $("dc-p-close").addEventListener("click", closeDetail);
    $("dc-p-preview").addEventListener("click", function () {
      if (state.openId) window.open("/api/declarations/" + encodeURIComponent(state.openId) + "/report/preview", "_blank", "noopener");
    });
    $("dc-lb-close").addEventListener("click", closeLightbox);
    $("dc-lb-prev").addEventListener("click", function (e) { e.stopPropagation(); stepLb(-1); });
    $("dc-lb-next").addEventListener("click", function (e) { e.stopPropagation(); stepLb(1); });
    $("dc-lightbox").addEventListener("click", function (e) { if (e.target === $("dc-lightbox")) closeLightbox(); });
    document.addEventListener("keydown", function (e) {
      if (!$("dc-lightbox").hidden) {
        if (e.key === "Escape") closeLightbox();
        else if (e.key === "ArrowLeft") stepLb(-1);
        else if (e.key === "ArrowRight") stepLb(1);
        return;
      }
      var wiz = document.getElementById("pcorgCreateModal");
      if (e.key === "Escape" && !$("dc-panel").hidden && !(wiz && wiz.classList.contains("show"))) closeDetail();
    });
    document.querySelectorAll(".dc-mobile-switch button").forEach(function (b) {
      b.addEventListener("click", function () {
        document.querySelectorAll(".dc-mobile-switch button").forEach(function (x) { x.classList.toggle("active", x === b); });
        $("dc-main").classList.toggle("show-map", b.dataset.view === "map");
        if (state.map) setTimeout(function () { state.map.invalidateSize(); }, 50);
      });
    });
    document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
    window.addEventListener("resize", function () { if (state.map) state.map.invalidateSize(); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    initMap();
    load();
    setInterval(function () { if (!document.hidden) load(); }, REFRESH_MS);
    // Conversation ouverte : relue plus souvent que la liste
    setInterval(refreshDetail, 6000);
  });
})();
