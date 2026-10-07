// ==========================================================================
// INDICATEURS SAISON - Admin (Configuration > Indicateurs SAISON (timeline))
// Configuration globale des pastilles / points de la barre des jours de la
// timeline SAISON (saison_indicateurs.py). Texte injecte via textContent.
// ==========================================================================
(function () {
  "use strict";

  var listEl = document.getElementById("si-admin-list");
  if (!listEl) return;
  var previewEl = document.getElementById("si-admin-preview-cells");
  var statusEl = document.getElementById("si-admin-status");

  var semEl = document.getElementById("si-admin-sem");
  var state = { indicators: [], defaults: [], venues: null, roomNames: {}, dirty: false, openPicker: null,
                sem: null, semDefaults: null, semTypes: null, semMax: 20 };
  var COLOR_RE = /^#[0-9A-Fa-f]{6}$/;

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  function toast(type, msg) {
    if (typeof window.showToast === "function") window.showToast(type, msg);
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function icon(name, size) {
    var s = el("span", "material-symbols-outlined", name);
    if (size) s.style.fontSize = size + "px";
    return s;
  }
  function textColor(hex) {
    if (!COLOR_RE.test(hex || "")) return "#fff";
    var r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
    return (r * 299 + g * 587 + b * 114) / 1000 > 150 ? "#111827" : "#ffffff";
  }
  function marker(ind) {
    var c = COLOR_RE.test(ind.color || "") ? ind.color : "#64748b";
    if (ind.rank === "pill") {
      var t = el("span", "si-tag", ind.short || "?");
      t.style.background = c;
      t.style.color = textColor(c);
      return t;
    }
    var d = el("span", "si-dot");
    d.style.background = c;
    return d;
  }
  function setDirty(v) {
    state.dirty = v;
    statusEl.textContent = v ? "Modifications non enregistrees" : "";
    statusEl.style.color = v ? "var(--warning)" : "var(--muted)";
  }
  function slug(s) {
    return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
      .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 32) || "indicateur";
  }
  function uniqueId(base) {
    var ids = state.indicators.map(function (i) { return i.id; });
    var id = base, n = 2;
    while (ids.indexOf(id) !== -1) { id = (base.slice(0, 28) + "_" + n); n++; }
    return id;
  }

  // --- Chargement --------------------------------------------------------
  function load() {
    fetch("/api/saison/indicateurs/config", { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (data) {
        state.indicators = JSON.parse(JSON.stringify(data.indicators || []));
        state.defaults = data.defaults || [];
        state.sem = JSON.parse(JSON.stringify(data.seminaires || { enabled: false, types: [], max_list: 5 }));
        state.semDefaults = data.seminaires_defaults || null;
        state.semMax = (data.limits && data.limits.sem_list) || 20;
        setDirty(false);
        render();
        loadEventTypes();
        return loadRooms();
      })
      .catch(function (e) { toast("error", "Indicateurs SAISON : chargement impossible (" + e.message + ")"); });
  }
  function loadEventTypes() {
    if (state.semTypes) return;
    fetch("/api/saison/indicateurs/event-types", { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (data) { state.semTypes = data.types || []; renderSem(); })
      .catch(function (e) { toast("warning", "Types d'evenement Momentus indisponibles (" + e.message + ")"); });
  }

  // --- Seminaires (bloc a part : metriques par jour) ----------------------
  function normType(s) {
    return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();
  }
  function renderSem() {
    if (!semEl || !state.sem) return;
    var sem = state.sem;
    semEl.textContent = "";
    var head = el("div", "si-admin-sem-head");
    head.appendChild(icon("groups", 18));
    head.appendChild(el("strong", null, "Seminaires du site"));
    var tog = el("label", "toggle-switch");
    tog.title = sem.enabled ? "Actif" : "Desactive";
    var cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = !!sem.enabled;
    cb.addEventListener("change", function () { sem.enabled = cb.checked; setDirty(true); renderSem(); });
    tog.appendChild(cb); tog.appendChild(el("span", "toggle-slider"));
    head.appendChild(tog);
    var ml = input("number", sem.max_list, { min: "1", max: String(state.semMax), step: "1" });
    ml.className = "form-input si-admin-sem-max";
    ml.addEventListener("input", function () { sem.max_list = parseInt(ml.value, 10) || 0; setDirty(true); });
    head.appendChild(field("Evenements detailles par jour", ml, "si-admin-f-semmax"));
    semEl.appendChild(head);
    semEl.appendChild(el("p", "si-admin-intro si-admin-sem-intro",
      "Calendrier et infobulle du jour : nombre d'evenements Momentus de ces types et personnes attendues "
      + "(effectif des fonctions du jour, sinon estimation de l'evenement), puis les plus gros. "
      + "Annules, perdus et prospects exclus ; ni contacts ni montants."));
    var box = el("div", "si-admin-sem-types" + (sem.enabled ? "" : " is-disabled"));
    var chosen = {};
    (sem.types || []).forEach(function (t) { chosen[normType(t)] = t; });
    var list = (state.semTypes || []).slice();
    // Types configures absents de Momentus : affiches quand meme (decochables)
    Object.keys(chosen).forEach(function (k) {
      if (!list.some(function (t) { return normType(t.name) === k; })) list.push({ name: chosen[k], count: 0 });
    });
    if (!state.semTypes) box.appendChild(el("span", "si-admin-empty", "Chargement des types Momentus..."));
    list.forEach(function (t) {
      var k = normType(t.name);
      var l = el("label", "si-admin-room" + (chosen[k] ? " is-on" : ""));
      var c = document.createElement("input");
      c.type = "checkbox"; c.checked = !!chosen[k];
      c.addEventListener("change", function () {
        if (c.checked) chosen[k] = t.name; else delete chosen[k];
        sem.types = Object.keys(chosen).map(function (x) { return chosen[x]; });
        l.classList.toggle("is-on", c.checked);
        setDirty(true);
      });
      l.appendChild(c);
      l.appendChild(el("span", null, t.name + (t.count ? " (" + t.count + ")" : " (absent de Momentus)")));
      box.appendChild(l);
    });
    semEl.appendChild(box);
  }
  function loadRooms() {
    if (state.venues) return Promise.resolve();
    return fetch("/api/saison/indicateurs/rooms", { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (data) {
        state.venues = data.venues || [];
        state.venues.forEach(function (v) {
          v.rooms.forEach(function (r) { state.roomNames[r.id] = r.name; });
        });
        render();
      })
      .catch(function (e) { toast("warning", "Espaces Momentus indisponibles (" + e.message + ")"); });
  }

  // --- Apercu ------------------------------------------------------------
  function previewCell(dayName, dayNum, activeFn) {
    var cell = el("div", "day-nav-pill si-day si-admin-cell");
    var enabled = state.indicators.filter(function (i) { return i.enabled; });
    var pills = enabled.filter(function (i) { return i.rank === "pill"; });
    var dots = enabled.filter(function (i) { return i.rank === "dot"; });
    if (pills.length) {
      var top = el("span", "si-tags");
      pills.forEach(function (ind, k) {
        var m = marker(ind);
        if (!activeFn(ind, k)) m.classList.add("si-off");
        top.appendChild(m);
      });
      cell.appendChild(top);
    }
    cell.appendChild(el("span", "day-name", dayName));
    cell.appendChild(el("span", "day-num", dayNum));
    if (dots.length) {
      var bottom = el("span", "si-dots");
      dots.forEach(function (ind, k) {
        var slot = el("span", "si-slot");
        if (activeFn(ind, k)) { slot.classList.add("si-on"); slot.appendChild(marker(ind)); }
        bottom.appendChild(slot);
      });
      cell.appendChild(bottom);
    }
    return cell;
  }
  function renderPreview() {
    previewEl.textContent = "";
    previewEl.appendChild(previewCell("Sam", "3", function () { return true; }));
    previewEl.appendChild(previewCell("Dim", "4", function (ind, k) { return k % 2 === 0; }));
    previewEl.appendChild(previewCell("Lun", "5", function () { return false; }));
    var legend = el("div", "si-admin-preview-legend");
    state.indicators.filter(function (i) { return i.enabled; }).forEach(function (ind) {
      var chip = el("span", "si-admin-legend-chip");
      chip.appendChild(marker(ind));
      chip.appendChild(el("span", null, ind.label || "(sans libelle)"));
      legend.appendChild(chip);
    });
    previewEl.appendChild(legend);
  }

  // --- Liste -------------------------------------------------------------
  function field(labelText, input, cls) {
    var w = el("label", "si-admin-field" + (cls ? " " + cls : ""));
    w.appendChild(el("span", "si-admin-field-label", labelText));
    w.appendChild(input);
    return w;
  }
  function input(type, value, attrs) {
    var i = document.createElement("input");
    i.type = type;
    i.className = "form-input";
    i.value = value == null ? "" : value;
    Object.keys(attrs || {}).forEach(function (k) { i.setAttribute(k, attrs[k]); });
    return i;
  }
  function select(options, value) {
    var s = document.createElement("select");
    s.className = "form-input";
    options.forEach(function (o) {
      var op = document.createElement("option");
      op.value = o[0];
      op.textContent = o[1];
      if (o[0] === value) op.selected = true;
      s.appendChild(op);
    });
    return s;
  }
  function move(idx, delta) {
    var j = idx + delta;
    if (j < 0 || j >= state.indicators.length) return;
    var a = state.indicators;
    var t = a[idx]; a[idx] = a[j]; a[j] = t;
    setDirty(true);
    render();
  }

  function render() {
    renderPreview();
    listEl.textContent = "";
    if (!state.indicators.length) {
      listEl.appendChild(el("p", "si-admin-empty", "Aucun indicateur : la barre des jours SAISON reste sans pastille ni point."));
    }
    state.indicators.forEach(function (ind, idx) { listEl.appendChild(renderRow(ind, idx)); });
    renderSem();
  }

  function renderRow(ind, idx) {
    var row = el("div", "si-admin-row" + (ind.enabled ? "" : " is-disabled"));
    var main = el("div", "si-admin-row-main");

    var order = el("div", "si-admin-order");
    var up = el("button", "btn-icon");
    up.type = "button"; up.title = "Monter"; up.appendChild(icon("arrow_upward", 16));
    up.disabled = idx === 0;
    up.addEventListener("click", function () { move(idx, -1); });
    var down = el("button", "btn-icon");
    down.type = "button"; down.title = "Descendre"; down.appendChild(icon("arrow_downward", 16));
    down.disabled = idx === state.indicators.length - 1;
    down.addEventListener("click", function () { move(idx, 1); });
    order.appendChild(up); order.appendChild(down);
    main.appendChild(order);

    var tog = el("label", "toggle-switch");
    tog.title = ind.enabled ? "Actif" : "Desactive";
    var cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = !!ind.enabled;
    cb.addEventListener("change", function () { ind.enabled = cb.checked; setDirty(true); render(); });
    tog.appendChild(cb); tog.appendChild(el("span", "toggle-slider"));
    main.appendChild(tog);

    var mk = el("span", "si-admin-marker");
    mk.appendChild(marker(ind));
    main.appendChild(mk);

    var lab = input("text", ind.label, { maxlength: "40", placeholder: "Libelle" });
    lab.addEventListener("input", function () { ind.label = lab.value; setDirty(true); renderPreview(); });
    main.appendChild(field("Libelle", lab, "si-admin-f-label"));

    var sh = input("text", ind.short, { maxlength: "4", placeholder: "VL" });
    sh.addEventListener("input", function () {
      ind.short = sh.value; setDirty(true); renderPreview();
      mk.textContent = ""; mk.appendChild(marker(ind));
    });
    main.appendChild(field("Court", sh, "si-admin-f-short"));

    var col = input("color", COLOR_RE.test(ind.color || "") ? ind.color : "#64748b");
    col.className = "si-admin-color";
    col.addEventListener("input", function () {
      ind.color = col.value.toUpperCase(); setDirty(true); renderPreview();
      mk.textContent = ""; mk.appendChild(marker(ind));
    });
    main.appendChild(field("Couleur", col, "si-admin-f-color"));

    var icw = el("span", "si-admin-iconwrap");
    var icPrev = icon(ind.icon || "circle", 18);
    var ic = input("text", ind.icon, { maxlength: "40", placeholder: "toys" });
    ic.title = "Nom d'icone Material Symbols (ex. toys, route, tour, directions_walk)";
    ic.addEventListener("input", function () { ind.icon = ic.value.trim(); icPrev.textContent = ind.icon || "circle"; setDirty(true); });
    icw.appendChild(icPrev); icw.appendChild(ic);
    main.appendChild(field("Icone", icw, "si-admin-f-icon"));

    var rk = select([["pill", "Pastille (au-dessus)"], ["dot", "Point (dessous)"]], ind.rank);
    rk.addEventListener("change", function () { ind.rank = rk.value; setDirty(true); render(); });
    main.appendChild(field("Affichage", rk, "si-admin-f-rank"));

    var srcType = (ind.source && ind.source.type) || "momentus_rooms";
    var st = select([["momentus_rooms", "Espaces Momentus"], ["visites", "Jours de visites"],
                     ["voisins", "Evenements voisins"]], srcType);
    st.addEventListener("change", function () {
      ind.source = st.value === "visites" ? { type: "visites", kind: "libre" }
        : st.value === "voisins" ? { type: "voisins", venue: "antares" }
        : { type: "momentus_rooms", room_ids: [] };
      setDirty(true); render();
    });
    main.appendChild(field("Source", st, "si-admin-f-src"));

    if (srcType === "voisins") {
      var vn = select([["antares", "Antares (spectacles, MSB)"], ["stade", "Stade MMArena (Le Mans FC)"]], ind.source.venue);
      vn.addEventListener("change", function () { ind.source.venue = vn.value; setDirty(true); });
      main.appendChild(field("Lieu", vn, "si-admin-f-kind"));
    } else if (srcType === "visites") {
      var kd = select([["libre", "Visites libres"], ["guidee", "Visites guidees"]], ind.source.kind);
      kd.addEventListener("change", function () { ind.source.kind = kd.value; setDirty(true); });
      main.appendChild(field("Visites", kd, "si-admin-f-kind"));
    } else {
      var n = (ind.source.room_ids || []).length;
      var pk = el("button", "btn btn-sm si-admin-pick" + (n ? "" : " is-empty"));
      pk.type = "button";
      pk.appendChild(icon("meeting_room", 16));
      pk.appendChild(el("span", null, n ? n + " espace" + (n > 1 ? "s" : "") : "Choisir les espaces"));
      pk.addEventListener("click", function () {
        state.openPicker = state.openPicker === idx ? null : idx;
        render();
      });
      main.appendChild(field("Espaces", pk, "si-admin-f-rooms"));
    }

    var del = el("button", "btn-icon btn-icon-danger si-admin-del");
    del.type = "button"; del.title = "Supprimer l'indicateur"; del.appendChild(icon("delete", 18));
    del.addEventListener("click", function () {
      var go = typeof window.showConfirmToast === "function"
        ? window.showConfirmToast("Supprimer l'indicateur \"" + (ind.label || ind.id) + "\" ? (effectif a l'enregistrement)", { type: "warning", okLabel: "Supprimer", cancelLabel: "Annuler" })
        : Promise.resolve(true);
      go.then(function (ok) {
        if (!ok) return;
        state.indicators.splice(idx, 1);
        state.openPicker = null;
        setDirty(true); render();
      });
    });
    main.appendChild(del);
    row.appendChild(main);

    if (srcType === "momentus_rooms") {
      var chosen = el("div", "si-admin-chosen");
      (ind.source.room_ids || []).forEach(function (rid) {
        var c = el("span", "si-admin-room-chip", state.roomNames[rid] || rid);
        c.title = rid;
        chosen.appendChild(c);
      });
      if ((ind.source.room_ids || []).length) row.appendChild(chosen);
      if (state.openPicker === idx) row.appendChild(renderPicker(ind));
    }
    return row;
  }

  function renderPicker(ind) {
    var box = el("div", "si-admin-picker");
    if (!state.venues) {
      box.appendChild(el("p", "si-admin-empty", "Chargement des espaces Momentus..."));
      loadRooms();
      return box;
    }
    var search = input("search", "", { placeholder: "Rechercher un espace ou un site (ex. piste, karting, circuit)" });
    search.className = "form-input si-admin-search";
    box.appendChild(search);
    var groups = el("div", "si-admin-venues");
    box.appendChild(groups);
    var selected = {};
    (ind.source.room_ids || []).forEach(function (r) { selected[r] = true; });

    function draw() {
      var q = search.value.trim().toLowerCase();
      groups.textContent = "";
      var shown = 0;
      state.venues.forEach(function (v) {
        // Recherche sur le nom d'espace d'abord ; le nom du site ne sert que si
        // aucun de ses espaces ne correspond ("karting" -> tout le site KARTING,
        // "piste" -> les seules pistes, pas les 60 box du site PISTES).
        var rooms = v.rooms.filter(function (r) {
          return !q || r.name.toLowerCase().indexOf(q) !== -1 || r.id.indexOf(q) !== -1;
        });
        if (q && !rooms.length && v.venue.toLowerCase().indexOf(q) !== -1) rooms = v.rooms.slice();
        if (!rooms.length) return;
        var g = el("div", "si-admin-venue");
        var nSel = rooms.filter(function (r) { return selected[r.id]; }).length;
        g.appendChild(el("div", "si-admin-venue-name", v.venue + (nSel ? "  (" + nSel + ")" : "")));
        var wrap = el("div", "si-admin-venue-rooms");
        rooms.forEach(function (r) {
          var l = el("label", "si-admin-room" + (selected[r.id] ? " is-on" : "") + (r.active ? "" : " is-inactive"));
          var c = document.createElement("input");
          c.type = "checkbox"; c.checked = !!selected[r.id];
          c.addEventListener("change", function () {
            if (c.checked) selected[r.id] = true; else delete selected[r.id];
            ind.source.room_ids = Object.keys(selected);
            setDirty(true);
            l.classList.toggle("is-on", c.checked);
          });
          l.appendChild(c);
          l.appendChild(el("span", null, r.name));
          l.title = r.id + (r.active ? "" : " (inactif dans Momentus)");
          wrap.appendChild(l);
          shown++;
        });
        g.appendChild(wrap);
        groups.appendChild(g);
      });
      if (!shown) groups.appendChild(el("p", "si-admin-empty", "Aucun espace ne correspond."));
    }
    search.addEventListener("input", draw);
    draw();
    var foot = el("div", "si-admin-picker-foot");
    var done = el("button", "btn btn-sm", "Fermer");
    done.type = "button";
    done.addEventListener("click", function () { state.openPicker = null; render(); });
    foot.appendChild(done);
    box.appendChild(foot);
    setTimeout(function () { search.focus(); }, 0);
    return box;
  }

  // --- Actions -----------------------------------------------------------
  function clientCheck() {
    var errs = [];
    state.indicators.forEach(function (ind, i) {
      var w = (ind.label || "indicateur " + (i + 1)) + " : ";
      if (!String(ind.label || "").trim()) errs.push(w + "libelle requis");
      var s = String(ind.short || "").trim();
      if (!s || s.length > 4) errs.push(w + "libelle court de 1 a 4 caracteres");
      if (!COLOR_RE.test(ind.color || "")) errs.push(w + "couleur invalide");
      if (!/^[a-z0-9_]{1,40}$/.test(ind.icon || "")) errs.push(w + "icone invalide (minuscules, chiffres, _)");
      if (ind.source.type === "momentus_rooms" && !(ind.source.room_ids || []).length) errs.push(w + "choisir au moins un espace");
    });
    if (state.sem) {
      if (state.sem.enabled && !(state.sem.types || []).length) errs.push("Seminaires : choisir au moins un type d'evenement");
      var n = state.sem.max_list;
      if (!(n >= 1 && n <= state.semMax)) errs.push("Seminaires : evenements detailles entre 1 et " + state.semMax);
    }
    return errs;
  }

  document.getElementById("si-admin-save").addEventListener("click", function () {
    var errs = clientCheck();
    if (errs.length) { toast("error", errs.slice(0, 3).join(" / ")); return; }
    statusEl.textContent = "Enregistrement...";
    var body = state.indicators.map(function (i) {
      return { id: i.id, label: String(i.label).trim(), short: String(i.short).trim(), color: i.color,
               icon: i.icon, rank: i.rank, enabled: !!i.enabled, source: i.source };
    });
    var payload = { indicators: body };
    if (state.sem) payload.seminaires = { enabled: !!state.sem.enabled, types: state.sem.types || [],
                                          max_list: state.sem.max_list };
    fetch("/api/saison/indicateurs/config", {
      method: "PUT", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d, status: r.status }; });
    }).then(function (res) {
      if (!res.ok || !res.d.ok) {
        var det = (res.d && res.d.details) ? " : " + res.d.details.slice(0, 3).join(" / ") : "";
        setDirty(true);
        toast("error", "Enregistrement refuse (" + ((res.d && res.d.error) || res.status) + ")" + det);
        return;
      }
      state.indicators = res.d.indicators;
      if (res.d.seminaires) state.sem = res.d.seminaires;
      setDirty(false);
      render();
      toast("success", "Indicateurs SAISON enregistres");
    }).catch(function (e) {
      setDirty(true);
      toast("error", "Erreur : " + e.message);
    });
  });

  document.getElementById("si-admin-add").addEventListener("click", function () {
    state.indicators.push({ id: uniqueId("indicateur"), label: "Nouvel indicateur", short: "NEW",
                            color: "#0072B2", icon: "flag", rank: "dot", enabled: true,
                            source: { type: "momentus_rooms", room_ids: [] } });
    state.openPicker = state.indicators.length - 1;
    setDirty(true);
    render();
  });

  document.getElementById("si-admin-reset").addEventListener("click", function () {
    var go = typeof window.showConfirmToast === "function"
      ? window.showConfirmToast("Remplacer la liste par la configuration par defaut ? (rien n'est enregistre tant que vous ne cliquez pas sur Enregistrer)", { type: "warning", okLabel: "Remplacer", cancelLabel: "Annuler" })
      : Promise.resolve(true);
    go.then(function (ok) {
      if (!ok) return;
      state.indicators = JSON.parse(JSON.stringify(state.defaults));
      if (state.semDefaults) state.sem = JSON.parse(JSON.stringify(state.semDefaults));
      state.openPicker = null;
      setDirty(true);
      render();
    });
  });

  // Libelle -> identifiant pour les nouveaux indicateurs jamais enregistres
  listEl.addEventListener("change", function (ev) {
    if (!ev.target.closest(".si-admin-f-label")) return;
    state.indicators.forEach(function (ind) {
      if (/^indicateur(_\d+)?$/.test(ind.id) && ind.label) ind.id = uniqueId(slug(ind.label));
    });
  });

  window.addEventListener("beforeunload", function (e) {
    if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
  else load();
})();
