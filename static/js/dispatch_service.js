/* dispatch_service.js -- page /dispatch-service : file du service.
 *
 * Pour les responsables d'une categorie (Technique, Securite...) : les fiches
 * ouvertes a dispatcher, les propositions automatiques en cours, les fiches
 * engagees, et les unites Field de la categorie. Backend : dispatch_auto.py
 * (GET /api/dispatch/board, POST /api/dispatch/<id>/assign et /auto).
 *
 * Utilise sur telephone : une seule colonne, bascule Fiches / Unites,
 * selecteur d'unite en feuille du bas. Polling 5 s, suspendu quand l'onglet
 * est cache, relance immediate au retour. Toute donnee de fiche (texte saisi
 * par un operateur ou une tablette) est inseree en textContent, jamais en HTML.
 */
(function () {
  "use strict";

  var POLL_MS = 5000;
  var LS_EVENT = "cockpit_event";
  var LS_YEAR = "cockpit_year";
  var LS_CAT = "ds_category";
  var LS_SOUND = "ds_sound";

  var CATEGORY_STYLES = {
    "PCO.Secours": { color: "#dc2626", icon: "local_hospital" },
    "PCO.Securite": { color: "#ef4444", icon: "shield" },
    "PCO.Technique": { color: "#f59e0b", icon: "build" },
    "PCO.Flux": { color: "#0d9488", icon: "swap_calls" },
    "PCO.Fourriere": { color: "#6b7280", icon: "directions_car" },
    "PCO.Information": { color: "#2563eb", icon: "info" },
    "PCO.MainCourante": { color: "#8b5cf6", icon: "edit_note" }
  };
  var URGENCY_COLORS = { EU: "#dc2626", UA: "#f97316", UR: "#eab308", IMP: "#6b7280" };
  var URGENCY_RANK = { EU: 4, UA: 3, UR: 2, IMP: 1 };
  var UNIT_STATUS = {
    patrouille: { label: "Disponible", color: "#16a34a" },
    intervention: { label: "Intervention", color: "#f59e0b" },
    sur_place: { label: "ASL", color: "#3b82f6" },
    pause: { label: "Pause", color: "#94a3b8" },
    fin_intervention: { label: "Fin d'inter", color: "#8b5cf6" }
  };
  var ANSWERS = {
    accepted: { label: "Acceptee", icon: "check_circle", cls: "ok" },
    refused: { label: "Refusee", icon: "cancel", cls: "ko" },
    timeout: { label: "Sans reponse", icon: "timer_off", cls: "warn" }
  };
  var OUTCOMES = {
    resolu: "Resolu",
    partiel: "Resolu partiellement",
    materiel: "Besoin de materiel ou de renfort",
    impossible: "Intervention impossible"
  };
  var ERRORS = {
    reseau: "Serveur Cockpit injoignable.",
    not_manager: "Vous n'etes pas responsable de cette categorie.",
    forbidden: "Action non autorisee.",
    not_found: "Fiche introuvable.",
    fiche_closee: "La fiche est close.",
    unite_invalide: "Unite invalide pour cet evenement.",
    invalid_device: "Unite introuvable.",
    categorie_differente: "L'unite n'appartient pas a la categorie de la fiche.",
    deja_engagee: "Une unite est deja engagee sur la fiche.",
    deja_en_cours: "Une proposition automatique est deja en cours.",
    missing_event_year: "Choisir un evenement et une annee.",
    session: "Session expiree : rechargez la page."
  };

  var managed = Array.isArray(window.__dsManagedCategories) ? window.__dsManagedCategories : [];

  var state = {
    data: null,
    skewMs: 0,            // heure serveur - heure locale
    lastOkAt: 0,
    cat: "",              // filtre categorie ("" = toutes)
    pane: "fiches",       // vue telephone
    timer: null,
    loading: false,
    seenQueue: null,      // ids deja vus dans "A traiter" (null = premier chargement)
    openHist: {},         // id fiche -> historique deplie
    pickerFiche: null,
    busy: {},             // actions en cours (anti double clic)
    audioCtx: null,
    soundOn: true,
    unread: 0
  };

  // ------------------------------------------------------------------ outils
  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }

  function icon(name, cls) {
    var s = el("span", "material-symbols-outlined" + (cls ? " " + cls : ""));
    s.textContent = name;
    return s;
  }

  function toast(type, msg, duration) {
    if (typeof window.showToast === "function") window.showToast(type, msg, duration);
  }

  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? (m.getAttribute("content") || "") : "";
  }

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* stockage bloque */ } }

  /** Requete JSON ; ne rejette jamais : {ok, status, data}. Rejoue une fois sur jeton CSRF perime. */
  function api(method, url, body, retried) {
    var opts = { method: method, credentials: "same-origin", headers: { "Accept": "application/json" } };
    if (method !== "GET") {
      opts.headers["X-CSRFToken"] = csrfToken();
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body || {});
    }
    return fetch(url, opts).then(function (r) {
      return r.json().then(function (j) {
        return { ok: r.ok && j && j.ok !== false, status: r.status, data: j || {} };
      }, function () {
        return { ok: false, status: r.status, data: { error: "session" } };
      });
    }, function () {
      return { ok: false, status: 0, data: { error: "reseau" } };
    }).then(function (res) {
      if (!retried && method !== "GET" && res.data && res.data.code === "csrf" && window.CockpitCsrf) {
        return window.CockpitCsrf.refresh().then(function (st) {
          if (st.ok) return api(method, url, body, true);
          return { ok: false, status: 0, data: { error: "session" } };
        });
      }
      return res;
    });
  }

  function errorText(data) {
    if (!data) return "Erreur inconnue.";
    return ERRORS[data.error] || ("Erreur : " + (data.error || data.message || "inconnue"));
  }

  function nowMs() { return Date.now() + state.skewMs; }

  function parseMs(iso) {
    if (!iso) return null;
    var t = Date.parse(iso);
    return isNaN(t) ? null : t;
  }

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  function hhmm(iso) {
    var t = parseMs(iso);
    if (t == null) return "";
    var d = new Date(t);
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  /** Duree lisible : "45 s", "12 min", "1 h 05". */
  function fmtDuration(ms) {
    if (ms == null || isNaN(ms)) return "";
    var s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return s + " s";
    var m = Math.floor(s / 60);
    if (m < 60) return m + " min";
    var h = Math.floor(m / 60);
    return h + " h " + pad2(m % 60);
  }

  function sinceText(iso) {
    var t = parseMs(iso);
    return t == null ? "" : fmtDuration(nowMs() - t);
  }

  function fmtDistance(m) {
    if (m == null || isNaN(m)) return "";
    if (m < 1000) return Math.round(m) + " m";
    return (m / 1000).toFixed(1).replace(".", ",") + " km";
  }

  function haversine(lat1, lng1, lat2, lng2) {
    var R = 6371000, rad = Math.PI / 180;
    var dp = (lat2 - lat1) * rad, dl = (lng2 - lng1) * rad;
    var a = Math.sin(dp / 2) * Math.sin(dp / 2) +
      Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  function norm(s) {
    s = String(s || "");
    try { s = s.normalize("NFKD").replace(/[̀-ͯ]/g, ""); } catch (e) { /* vieux navigateur */ }
    return s.trim().toLowerCase();
  }

  /** Meme regle que dispatch_auto._device_metiers_ok : sans metier declare, l'unite les couvre tous. */
  function metierOk(unit, metier) {
    var list = unit.metiers || [];
    if (!metier || !list.length) return true;
    var t = norm(metier);
    return list.some(function (m) { return norm(m) === t; });
  }

  function shortCat(c) { return String(c || "").replace("PCO.", ""); }
  function catStyle(c) { return CATEGORY_STYLES[c] || { color: "#94a3b8", icon: "description" }; }

  // ------------------------------------------------------------------ evenement / annee
  function scope() {
    var ev = $("ds-event"), yr = $("ds-year");
    return { event: ev ? ev.value : "", year: yr ? yr.value : "" };
  }

  function updateScopeLabel() {
    var s = scope(), lbl = $("ds-scope-label");
    if (lbl) lbl.textContent = s.event && s.year ? s.event + " " + s.year : "";
  }

  /** Meme mecanisme que main.js : localStorage partage avec le cockpit, repli 24H AUTOS. */
  function initScope() {
    var evSel = $("ds-event"), yrSel = $("ds-year");
    if (!evSel || !yrSel) return Promise.resolve();
    var savedEv = lsGet(LS_EVENT), savedYr = parseInt(lsGet(LS_YEAR) || "", 10);
    var cur = new Date().getFullYear();
    var prefYr = savedYr || cur;
    for (var y = 2023; y <= cur + 1; y++) {
      var o = el("option", "", y);
      o.value = String(y);
      if (y === prefYr) o.selected = true;
      yrSel.appendChild(o);
    }
    if (!yrSel.value) yrSel.value = String(cur);

    evSel.addEventListener("change", function () { lsSet(LS_EVENT, evSel.value); onScopeChange(); });
    yrSel.addEventListener("change", function () { lsSet(LS_YEAR, yrSel.value); onScopeChange(); });

    return fetch("/get_events", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (list) {
        var names = (Array.isArray(list) ? list : []).map(function (x) { return x && x.nom; })
          .filter(function (n) { return !!n; });
        names.forEach(function (n) {
          var o = el("option", "", n);
          o.value = n;
          evSel.appendChild(o);
        });
        if (savedEv && names.indexOf(savedEv) >= 0) evSel.value = savedEv;
        else if (names.indexOf("24H AUTOS") >= 0) evSel.value = "24H AUTOS";
        else if (names.length) evSel.value = names[0];
      })
      .catch(function () { toast("error", "Liste des evenements indisponible."); })
      .then(function () { updateScopeLabel(); });
  }

  function onScopeChange() {
    updateScopeLabel();
    state.data = null;
    state.seenQueue = null;
    refresh();
  }

  // ------------------------------------------------------------------ son, titre, notifications
  function unlockAudio() {
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!state.audioCtx) state.audioCtx = new AC();
      if (state.audioCtx.state === "suspended") state.audioCtx.resume();
    } catch (e) { /* audio indisponible */ }
  }

  function beep() {
    if (!state.soundOn) return;
    try {
      unlockAudio();
      var ctx = state.audioCtx;
      if (!ctx) return;
      var t0 = ctx.currentTime;
      [0, 0.22].forEach(function (off, i) {
        var osc = ctx.createOscillator(), gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = i ? 1175 : 880;
        gain.gain.setValueAtTime(0.0001, t0 + off);
        gain.gain.exponentialRampToValueAtTime(0.35, t0 + off + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + off + 0.18);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(t0 + off);
        osc.stop(t0 + off + 0.2);
      });
    } catch (e) { /* audio indisponible */ }
  }

  function askNotificationPermission() {
    try {
      if ("Notification" in window && Notification.permission === "default") {
        var p = Notification.requestPermission();
        if (p && typeof p.then === "function") p.then(updateNotifButton, function () {});
      }
    } catch (e) { /* API absente (iOS hors PWA) */ }
  }

  function notify(fiches) {
    try {
      if (!("Notification" in window) || Notification.permission !== "granted") return;
      var f = fiches[0];
      var title = fiches.length > 1 ? fiches.length + " nouvelles fiches a traiter"
        : "A traiter : " + shortCat(f.category) + (f.niveau_urgence ? " " + f.niveau_urgence : "");
      var n = new Notification(title, {
        body: (f.text || "").slice(0, 140),
        tag: "ds-queue-" + f.id,
        renotify: true
      });
      n.onclick = function () { try { window.focus(); n.close(); } catch (e) { /* rien */ } };
    } catch (e) { /* Notification refusee par le navigateur */ }
  }

  function updateTitle(queueCount) {
    var base = "File du service -- COCKPIT";
    var prefix = "";
    if (state.unread > 0) prefix = "(" + state.unread + " nouv.) ";
    else if (queueCount > 0) prefix = "(" + queueCount + ") ";
    document.title = prefix + base;
  }

  function updateNotifButton() {
    var b = $("ds-notif-btn");
    if (!b) return;
    var ic = b.querySelector(".material-symbols-outlined");
    var perm = "";
    try { perm = ("Notification" in window) ? Notification.permission : "absente"; } catch (e) { perm = "absente"; }
    if (ic) ic.textContent = state.soundOn ? "notifications_active" : "notifications_off";
    b.classList.toggle("is-off", !state.soundOn);
    b.title = (state.soundOn ? "Son active" : "Son coupe") +
      (perm === "granted" ? " - notifications autorisees" :
        perm === "denied" ? " - notifications bloquees par le navigateur" :
          perm === "absente" ? " - notifications non disponibles" : " - notifications a autoriser") +
      " (cliquer pour " + (state.soundOn ? "couper" : "activer") + " le son)";
  }

  // ------------------------------------------------------------------ chargement
  function schedule(delay) {
    clearTimeout(state.timer);
    if (document.hidden) return;
    state.timer = setTimeout(refresh, delay == null ? POLL_MS : delay);
  }

  function setMessage(text, kind) {
    var m = $("ds-message");
    if (!m) return;
    if (!text) { m.hidden = true; m.textContent = ""; return; }
    m.hidden = false;
    m.className = "ds-message" + (kind ? " is-" + kind : "");
    m.textContent = text;
  }

  function refresh() {
    if (!managed.length) return;
    var s = scope();
    if (!s.event || !s.year) {
      setMessage("Choisir un evenement et une annee.", "warn");
      schedule();
      return;
    }
    if (state.loading) return;
    state.loading = true;
    var url = "/api/dispatch/board?event=" + encodeURIComponent(s.event) + "&year=" + encodeURIComponent(s.year);
    api("GET", url).then(function (res) {
      state.loading = false;
      if (!res.ok) {
        if (res.status === 403 && res.data.error === "not_manager") {
          setMessage("Vous n'etes plus responsable d'aucune categorie. Un administrateur doit vous ajouter dans Field dispatch > Dispatch automatique.", "warn");
          return;   // pas de nouvel essai : rien ne changera sans rechargement
        }
        setLive(false);
        setMessage(errorText(res.data), res.data.error === "reseau" ? "warn" : "error");
        schedule();
        return;
      }
      var d = res.data;
      var srv = parseMs(d.now);
      if (srv != null) state.skewMs = srv - Date.now();
      state.data = d;
      state.lastOkAt = Date.now();
      setLive(true);
      setMessage("");
      render();
      schedule();
    });
  }

  function setLive(ok) {
    var live = $("ds-live");
    if (live) live.classList.toggle("is-ko", !ok);
    var t = $("ds-live-text");
    if (t) t.textContent = ok ? hhmm(new Date().toISOString()) : "hors ligne";
  }

  // ------------------------------------------------------------------ classement
  function isOpenNoUnit(f) { return !(f.patrouille || "").trim(); }

  function bucket(f) {
    var st = (f.dispatch || {}).state;
    if (!isOpenNoUnit(f)) return "engaged";
    if (st === "proposing") return "proposing";
    return "queue";
  }

  function visibleFiches() {
    var list = (state.data && state.data.fiches) || [];
    if (!state.cat) return list;
    return list.filter(function (f) { return f.category === state.cat; });
  }

  function visibleUnits() {
    var list = (state.data && state.data.units) || [];
    if (!state.cat) return list;
    return list.filter(function (u) { return u.category === state.cat; });
  }

  function queueSortKey(f) {
    var d = f.dispatch || {};
    // En file du service d'abord (anciennete de mise en file), puis les autres fiches sans unite
    var queued = d.state === "queued";
    return [queued ? 0 : 1, parseMs(queued ? d.queued_at : f.ts) || 0];
  }

  function cmpKeys(a, b) {
    for (var i = 0; i < a.length; i++) {
      if (a[i] < b[i]) return -1;
      if (a[i] > b[i]) return 1;
    }
    return 0;
  }

  // ------------------------------------------------------------------ rendu
  function render() {
    if (!state.data) return;
    renderCats();
    var fiches = visibleFiches();
    var groups = { queue: [], proposing: [], engaged: [] };
    fiches.forEach(function (f) { groups[bucket(f)].push(f); });
    groups.queue.sort(function (a, b) { return cmpKeys(queueSortKey(a), queueSortKey(b)); });
    groups.proposing.sort(function (a, b) {
      return (parseMs((a.dispatch.current || {}).expires_at) || 0) - (parseMs((b.dispatch.current || {}).expires_at) || 0);
    });
    groups.engaged.sort(function (a, b) {
      return (parseMs((a.intervention || {}).engaged_at || a.ts) || 0) - (parseMs((b.intervention || {}).engaged_at || b.ts) || 0);
    });

    detectNewQueue();

    fillList("ds-list-queue", groups.queue, "Aucune fiche en attente.");
    fillList("ds-list-proposing", groups.proposing, "Aucune proposition en cours.");
    fillList("ds-list-engaged", groups.engaged, "Aucune fiche engagee.");
    setCount("ds-count-queue", groups.queue.length);
    setCount("ds-count-proposing", groups.proposing.length);
    setCount("ds-count-engaged", groups.engaged.length);
    setCount("ds-switch-fiches", groups.queue.length);

    renderUnits();
    updateTitle(groups.queue.length);
    tick();
    if (state.pickerFiche) refreshPicker();
  }

  /** Nouvelle fiche dans "A traiter" (toutes categories gerees, filtre ignore) : son + notification. */
  function detectNewQueue() {
    var all = (state.data && state.data.fiches) || [];
    var ids = {}, fresh = [];
    all.forEach(function (f) {
      if (bucket(f) !== "queue") return;
      ids[f.id] = true;
      if (state.seenQueue && !state.seenQueue[f.id]) fresh.push(f);
    });
    var first = state.seenQueue === null;
    state.seenQueue = ids;
    if (first || !fresh.length) return;
    fresh.sort(function (a, b) { return (URGENCY_RANK[b.niveau_urgence] || 0) - (URGENCY_RANK[a.niveau_urgence] || 0); });
    beep();
    notify(fresh);
    if (document.hidden || !document.hasFocus()) state.unread += fresh.length;
    toast("warning", fresh.length > 1 ? fresh.length + " nouvelles fiches a traiter"
      : "Nouvelle fiche a traiter : " + (fresh[0].text || shortCat(fresh[0].category)).slice(0, 80), 6000);
  }

  function setCount(id, n) {
    var c = $(id);
    if (c) {
      c.textContent = String(n);
      c.classList.toggle("is-zero", !n);
    }
  }

  function renderCats() {
    var nav = $("ds-cats");
    if (!nav) return;
    var cats = (state.data && state.data.categories) || managed;
    if (cats.length < 2) { nav.hidden = true; state.cat = cats[0] || ""; return; }
    nav.hidden = false;
    if (state.cat && cats.indexOf(state.cat) < 0) state.cat = "";
    var counts = {};
    ((state.data && state.data.fiches) || []).forEach(function (f) {
      if (bucket(f) === "queue") counts[f.category] = (counts[f.category] || 0) + 1;
    });
    var total = 0;
    Object.keys(counts).forEach(function (k) { total += counts[k]; });
    nav.textContent = "";
    var entries = [{ key: "", label: "Toutes", n: total }].concat(cats.map(function (c) {
      return { key: c, label: shortCat(c), n: counts[c] || 0 };
    }));
    entries.forEach(function (e) {
      var b = el("button", "ds-cat" + (state.cat === e.key ? " active" : ""));
      b.type = "button";
      b.setAttribute("role", "tab");
      if (e.key) {
        var ic = icon(catStyle(e.key).icon);
        ic.style.color = catStyle(e.key).color;
        b.appendChild(ic);
      }
      b.appendChild(el("span", "", e.label));
      if (e.n) b.appendChild(el("span", "ds-cat-n", e.n));
      b.addEventListener("click", function () {
        state.cat = e.key;
        lsSet(LS_CAT, e.key);
        render();
      });
      nav.appendChild(b);
    });
  }

  function fillList(id, fiches, emptyText) {
    var box = $(id);
    if (!box) return;
    box.textContent = "";
    if (!fiches.length) {
      box.appendChild(el("div", "ds-empty", emptyText));
      return;
    }
    fiches.forEach(function (f) { box.appendChild(ficheCard(f)); });
  }

  function unitByName(name) {
    var units = (state.data && state.data.units) || [];
    for (var i = 0; i < units.length; i++) if (units[i].name === name) return units[i];
    return null;
  }

  function statusBadge(status) {
    var meta = UNIT_STATUS[status] || { label: status || "?", color: "#94a3b8" };
    var b = el("span", "ds-status");
    b.style.setProperty("--st", meta.color);
    b.appendChild(el("span", "ds-status-dot"));
    b.appendChild(document.createTextNode(meta.label));
    return b;
  }

  function ficheCard(f) {
    var d = f.dispatch || {};
    var inter = f.intervention || {};
    var b = bucket(f);
    var st = catStyle(f.category);
    var card = el("article", "ds-card ds-card-" + b);
    card.style.setProperty("--cat", st.color);
    card.setAttribute("data-id", f.id);

    // En-tete : urgence, categorie, metier, age
    var head = el("div", "ds-card-head");
    if (f.niveau_urgence) {
      var u = el("span", "ds-urg", f.niveau_urgence);
      u.style.background = URGENCY_COLORS[f.niveau_urgence] || "#6b7280";
      head.appendChild(u);
    }
    var cat = el("span", "ds-card-cat");
    var ci = icon(st.icon);
    ci.style.color = st.color;
    cat.appendChild(ci);
    cat.appendChild(document.createTextNode(shortCat(f.category)));
    head.appendChild(cat);
    if (f.metier) head.appendChild(el("span", "ds-chip", f.metier));
    var age = el("span", "ds-card-age");
    age.appendChild(icon("schedule"));
    var ageTxt = el("span", "", sinceText(f.ts));
    ageTxt.setAttribute("data-since", f.ts || "");
    age.appendChild(ageTxt);
    age.title = "Creee a " + hhmm(f.ts);
    head.appendChild(age);
    card.appendChild(head);

    card.appendChild(el("div", "ds-card-text", f.text || "(sans description)"));

    var where = [f.area, f.carroye ? "Carroye " + f.carroye : ""].filter(function (x) { return !!x; }).join(" - ");
    if (where || f.lat == null) {
      var loc = el("div", "ds-card-loc");
      loc.appendChild(icon(f.lat != null ? "location_on" : "location_off"));
      loc.appendChild(document.createTextNode(where || "Sans position"));
      card.appendChild(loc);
    }

    // Etat de dispatch
    if (b === "queue") {
      var q = el("div", "ds-card-state is-queue");
      q.appendChild(icon(d.state === "queued" ? "pending_actions" : "inbox"));
      var qt = el("span", "");
      if (d.state === "queued") {
        qt.appendChild(document.createTextNode("En file depuis "));
        var qs = el("strong", "", sinceText(d.queued_at));
        qs.setAttribute("data-since", d.queued_at || "");
        qt.appendChild(qs);
        if (d.queue_reason) qt.appendChild(document.createTextNode(" - " + d.queue_reason));
      } else {
        qt.textContent = "Aucune unite engagee, pas de proposition automatique";
      }
      q.appendChild(qt);
      card.appendChild(q);
    } else if (b === "proposing") {
      var cur = d.current || {};
      var p = el("div", "ds-card-state is-proposing");
      p.appendChild(icon("hourglass_top"));
      var pt = el("span", "");
      if (cur.device_name) {
        pt.appendChild(document.createTextNode("Proposee a "));
        pt.appendChild(el("strong", "", cur.device_name));
        if (cur.distance_m != null) pt.appendChild(document.createTextNode(" (" + fmtDistance(cur.distance_m) + ")"));
      } else {
        pt.appendChild(document.createTextNode("Recherche d'une unite..."));
      }
      p.appendChild(pt);
      if (cur.expires_at) {
        var cd = el("span", "ds-countdown");
        cd.setAttribute("data-expires", cur.expires_at);
        p.appendChild(cd);
      }
      card.appendChild(p);
      var tried = (d.attempts || []).filter(function (a) { return a.round === d.round; }).length;
      if (d.round || tried) {
        card.appendChild(el("div", "ds-card-sub",
          "Tournee " + (d.round || 1) + " - " + tried + " proposition(s) deja sans suite"));
      }
    } else {
      var unit = unitByName(f.patrouille);
      var e = el("div", "ds-card-state is-engaged");
      e.appendChild(icon("directions_car"));
      e.appendChild(el("strong", "", f.patrouille));
      e.appendChild(statusBadge(f.unit_status || (unit && unit.status)));
      if (unit && !unit.online) e.appendChild(el("span", "ds-flag is-warn", "hors ligne"));
      card.appendChild(e);
      var times = el("div", "ds-times");
      if (inter.engaged_at) times.appendChild(timeItem("Engagee", inter.engaged_at, null));
      if (inter.arrived_at) {
        var dly = parseMs(inter.engaged_at) != null
          ? " (" + fmtDuration(parseMs(inter.arrived_at) - parseMs(inter.engaged_at)) + " apres engagement)" : "";
        times.appendChild(timeItem("Sur place", inter.arrived_at, dly));
      }
      if (inter.done_at) times.appendChild(timeItem("Terminee", inter.done_at, null));
      if (!inter.engaged_at && d.assigned_by && d.assigned_by !== "proposition") {
        times.appendChild(el("span", "ds-time", "Engagee par " + d.assigned_by + ", attente de confirmation"));
      }
      if (times.childNodes.length) card.appendChild(times);
    }

    if (inter.outcome && b !== "engaged") {
      var oc = el("div", "ds-card-sub is-outcome");
      oc.appendChild(icon("assignment_return"));
      oc.appendChild(document.createTextNode("Dernier retour : " + (OUTCOMES[inter.outcome] || inter.outcome) +
        (inter.device_name ? " (" + inter.device_name + ")" : "") + (inter.report ? " - " + inter.report : "")));
      card.appendChild(oc);
    }

    // Historique des propositions
    var attempts = d.attempts || [];
    if (attempts.length) {
      var det = el("details", "ds-hist");
      if (state.openHist[f.id]) det.open = true;
      det.addEventListener("toggle", function () { state.openHist[f.id] = det.open; });
      var refused = attempts.filter(function (a) { return a.answer !== "accepted"; }).length;
      det.appendChild(el("summary", "", "Historique des propositions (" + attempts.length +
        (refused ? ", " + refused + " sans suite" : "") + ")"));
      var ul = el("ul", "");
      attempts.slice().reverse().forEach(function (a) {
        var meta = ANSWERS[a.answer] || { label: a.answer || "?", icon: "help", cls: "" };
        var li = el("li", "is-" + meta.cls);
        li.appendChild(icon(meta.icon));
        li.appendChild(el("strong", "", a.device_name || "?"));
        li.appendChild(document.createTextNode(" " + meta.label.toLowerCase() +
          (a.answered_at ? " a " + hhmm(a.answered_at) : "") + (a.round ? " - tournee " + a.round : "")));
        ul.appendChild(li);
      });
      det.appendChild(ul);
      card.appendChild(det);
    }

    // Actions
    if (b !== "engaged") {
      var act = el("div", "ds-card-actions");
      var eng = el("button", "btn btn-primary ds-btn");
      eng.type = "button";
      eng.appendChild(icon("person_add"));
      eng.appendChild(document.createTextNode(" Engager"));
      eng.addEventListener("click", function () { openPicker(f.id); });
      act.appendChild(eng);
      if (b === "queue") {
        var auto = el("button", "btn btn-secondary ds-btn");
        auto.type = "button";
        auto.appendChild(icon("smart_toy"));
        auto.appendChild(document.createTextNode(" Proposer auto"));
        auto.disabled = !!state.busy["auto:" + f.id];
        auto.addEventListener("click", function () { relaunchAuto(f, auto); });
        act.appendChild(auto);
      }
      card.appendChild(act);
    }
    return card;
  }

  function timeItem(label, iso, extra) {
    var s = el("span", "ds-time");
    s.appendChild(el("b", "", label + " "));
    s.appendChild(document.createTextNode(hhmm(iso) + " - il y a "));
    var since = el("span", "", sinceText(iso));
    since.setAttribute("data-since", iso);
    s.appendChild(since);
    if (extra) s.appendChild(document.createTextNode(extra));
    return s;
  }

  function renderUnits() {
    var box = $("ds-list-units");
    if (!box) return;
    var units = visibleUnits().slice();
    setCount("ds-count-units", units.length);
    setCount("ds-switch-units", units.filter(function (u) { return u.status === "patrouille" && u.online; }).length);
    box.textContent = "";
    if (!units.length) {
      box.appendChild(el("div", "ds-empty", "Aucune unite Field enrolee pour cette categorie sur cet evenement."));
      return;
    }
    var order = { patrouille: 0, sur_place: 1, intervention: 1, fin_intervention: 2, pause: 3 };
    units.sort(function (a, b) {
      return cmpKeys([a.online ? 0 : 1, order[a.status] != null ? order[a.status] : 4, String(a.name || "")],
        [b.online ? 0 : 1, order[b.status] != null ? order[b.status] : 4, String(b.name || "")]);
    });
    var fichesById = {};
    ((state.data && state.data.fiches) || []).forEach(function (f) { fichesById[f.id] = f; });
    var multi = ((state.data && state.data.categories) || []).length > 1 && !state.cat;

    units.forEach(function (u) {
      var row = el("div", "ds-unit" + (u.online ? "" : " is-offline"));
      var top = el("div", "ds-unit-top");
      var dot = el("span", "ds-online" + (u.online ? " on" : ""));
      dot.title = u.online ? "En ligne" : ("Hors ligne" + (u.last_seen ? " - vue il y a " + sinceText(u.last_seen) : ""));
      top.appendChild(dot);
      top.appendChild(el("strong", "ds-unit-name", u.name || "?"));
      top.appendChild(statusBadge(u.status));
      if (u.battery != null) {
        var bat = el("span", "ds-battery" + (u.battery <= 20 ? " is-low" : ""));
        bat.appendChild(icon(u.battery <= 20 ? "battery_alert" : "battery_full"));
        bat.appendChild(document.createTextNode(Math.round(u.battery) + " %"));
        top.appendChild(bat);
      }
      row.appendChild(top);

      var meta = el("div", "ds-unit-meta");
      if (multi) meta.appendChild(el("span", "", shortCat(u.category)));
      if (u.status_since) meta.appendChild(el("span", "", "depuis " + sinceText(u.status_since)));
      if (u.pos_age_s == null) meta.appendChild(el("span", "", "sans position"));
      else if (u.pos_age_s > 300) meta.appendChild(el("span", "is-warn", "position il y a " + fmtDuration(u.pos_age_s * 1000)));
      if (!u.online && u.last_seen) meta.appendChild(el("span", "is-warn", "vue il y a " + sinceText(u.last_seen)));
      if (meta.childNodes.length) row.appendChild(meta);

      if ((u.metiers || []).length) {
        var ms = el("div", "ds-chips");
        u.metiers.forEach(function (m) { ms.appendChild(el("span", "ds-chip", m)); });
        row.appendChild(ms);
      }
      if (u.active_fiche_id) {
        var af = fichesById[u.active_fiche_id];
        var line = el("div", "ds-unit-fiche");
        line.appendChild(icon("assignment"));
        line.appendChild(document.createTextNode("Fiche active : " + (af ? (af.text || "(sans description)") : "hors de cette liste")));
        row.appendChild(line);
      }
      if (u.pending_fiche_id) {
        var pf = fichesById[u.pending_fiche_id];
        var pl = el("div", "ds-unit-fiche is-pending");
        pl.appendChild(icon("hourglass_top"));
        pl.appendChild(document.createTextNode("Proposition en attente : " + (pf ? (pf.text || "(sans description)") : "autre fiche")));
        row.appendChild(pl);
      }
      box.appendChild(row);
    });
  }

  // ------------------------------------------------------------------ minuterie 1 s
  function tick() {
    var now = nowMs();
    document.querySelectorAll("#ds-main [data-expires]").forEach(function (n) {
      var t = parseMs(n.getAttribute("data-expires"));
      if (t == null) return;
      var left = Math.round((t - now) / 1000);
      n.textContent = left > 0 ? left + " s" : "expiree";
      n.classList.toggle("is-low", left <= 10);
    });
    document.querySelectorAll("#ds-main [data-since]").forEach(function (n) {
      var iso = n.getAttribute("data-since");
      if (iso) n.textContent = sinceText(iso);
    });
    // Lien serveur perdu depuis plus de 20 s : le signaler plutot que d'afficher un etat fige
    if (state.lastOkAt && Date.now() - state.lastOkAt > 20000 && !document.hidden) setLive(false);
  }

  // ------------------------------------------------------------------ actions
  function findFiche(id) {
    var list = (state.data && state.data.fiches) || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }

  function relaunchAuto(f, btn) {
    var key = "auto:" + f.id;
    if (state.busy[key]) return;
    state.busy[key] = true;
    if (btn) btn.disabled = true;
    api("POST", "/api/dispatch/" + encodeURIComponent(f.id) + "/auto", {}).then(function (res) {
      delete state.busy[key];
      if (btn) btn.disabled = false;
      if (!res.ok) { toast("error", errorText(res.data)); refresh(); return; }
      var st = res.data.state;
      toast(st === "queued" ? "warning" : "success",
        st === "queued" ? "Aucune unite disponible : la fiche reste en file." : "Proposition automatique lancee.");
      refresh();
    });
  }

  function candidateList(f) {
    var units = ((state.data && state.data.units) || []).filter(function (u) { return u.category === f.category; });
    return units.map(function (u) {
      var dist = null;
      if (f.lat != null && f.lng != null && u.lat != null && u.lng != null) {
        dist = haversine(+f.lat, +f.lng, +u.lat, +u.lng);
      }
      var available = u.status === "patrouille" && u.online && !u.active_fiche_id;
      return { u: u, dist: dist, available: available, metierOk: metierOk(u, f.metier) };
    }).sort(function (a, b) {
      return cmpKeys(
        [a.available ? 0 : 1, a.u.pending_fiche_id ? 1 : 0, a.dist == null ? 1 : 0, a.dist == null ? 0 : a.dist, String(a.u.name || "")],
        [b.available ? 0 : 1, b.u.pending_fiche_id ? 1 : 0, b.dist == null ? 1 : 0, b.dist == null ? 0 : b.dist, String(b.u.name || "")]);
    });
  }

  function openPicker(ficheId) {
    unlockAudio();
    state.pickerFiche = ficheId;
    var ov = $("ds-picker");
    if (!ov) return;
    ov.hidden = false;
    document.body.classList.add("ds-sheet-open");
    refreshPicker();
    var close = ov.querySelector("[data-close]");
    if (close) close.focus();
  }

  function closePicker() {
    state.pickerFiche = null;
    var ov = $("ds-picker");
    if (ov) ov.hidden = true;
    document.body.classList.remove("ds-sheet-open");
  }

  function refreshPicker() {
    var f = findFiche(state.pickerFiche);
    var list = $("ds-picker-list"), sub = $("ds-picker-sub");
    if (!list) return;
    if (!f) {
      closePicker();
      toast("info", "La fiche n'est plus dans la file (close ou hors perimetre).");
      return;
    }
    if (sub) {
      sub.textContent = "";
      if (f.niveau_urgence) {
        var u = el("span", "ds-urg", f.niveau_urgence);
        u.style.background = URGENCY_COLORS[f.niveau_urgence] || "#6b7280";
        sub.appendChild(u);
      }
      sub.appendChild(document.createTextNode(" " + (f.text || "(sans description)").slice(0, 160)));
    }
    var scroll = list.scrollTop;
    list.textContent = "";
    var cands = candidateList(f);
    if (!cands.length) {
      list.appendChild(el("div", "ds-empty", "Aucune unite " + shortCat(f.category) + " enrolee sur cet evenement."));
      return;
    }
    if (f.lat == null) list.appendChild(el("div", "ds-picker-note", "La fiche n'a pas de position : les unites ne sont pas classees par distance."));
    cands.forEach(function (c) {
      var u = c.u;
      var row = el("button", "ds-pick" + (c.available ? " is-available" : " is-busy"));
      row.type = "button";
      var top = el("div", "ds-pick-top");
      var dot = el("span", "ds-online" + (u.online ? " on" : ""));
      top.appendChild(dot);
      top.appendChild(el("strong", "", u.name || "?"));
      top.appendChild(statusBadge(u.status));
      if (c.dist != null) top.appendChild(el("span", "ds-pick-dist", fmtDistance(c.dist)));
      row.appendChild(top);

      var meta = el("div", "ds-unit-meta");
      if (u.pos_age_s == null) meta.appendChild(el("span", "", "sans position"));
      else if (u.pos_age_s > 300) meta.appendChild(el("span", "is-warn", "position il y a " + fmtDuration(u.pos_age_s * 1000)));
      if (!u.online) meta.appendChild(el("span", "is-warn", "hors ligne" + (u.last_seen ? " (vue il y a " + sinceText(u.last_seen) + ")" : "")));
      if (u.battery != null) meta.appendChild(el("span", u.battery <= 20 ? "is-warn" : "", "batterie " + Math.round(u.battery) + " %"));
      if (meta.childNodes.length) row.appendChild(meta);

      var chips = el("div", "ds-chips");
      if ((u.metiers || []).length) u.metiers.forEach(function (m) { chips.appendChild(el("span", "ds-chip", m)); });
      else chips.appendChild(el("span", "ds-chip is-muted", "tous metiers"));
      row.appendChild(chips);

      if (!c.metierOk) row.appendChild(flag("warning", "Metier \"" + f.metier + "\" non declare pour cette unite", "warn"));
      if (u.status !== "patrouille" || u.active_fiche_id) {
        row.appendChild(flag("info", "Occupee : la fiche s'ajoutera a ses missions", "info"));
      }
      if (u.pending_fiche_id && u.pending_fiche_id !== f.id) row.appendChild(flag("hourglass_top", "Une autre proposition attend sa reponse", "info"));
      if (u.pending_fiche_id === f.id) row.appendChild(flag("hourglass_top", "Proposition en cours sur cette fiche", "info"));

      row.addEventListener("click", function () { confirmAssign(f, c, row); });
      list.appendChild(row);
    });
    list.scrollTop = scroll;
  }

  function flag(ic, text, kind) {
    var f = el("div", "ds-flag is-" + kind);
    f.appendChild(icon(ic));
    f.appendChild(document.createTextNode(text));
    return f;
  }

  function confirmAssign(f, c, row) {
    var warn = [];
    if (!c.u.online) warn.push("l'unite est hors ligne");
    if (!c.metierOk) warn.push("son metier ne correspond pas (" + f.metier + ")");
    if (c.u.status !== "patrouille" || c.u.active_fiche_id) warn.push("elle est occupee : la fiche s'ajoutera a ses missions");
    if (!warn.length) { doAssign(f, c.u, row); return; }
    var ask = typeof window.showConfirmToast === "function"
      ? window.showConfirmToast("Engager " + (c.u.name || "cette unite") + " ? Attention : " + warn.join(", ") + ".",
        { okLabel: "Engager", cancelLabel: "Annuler", type: "warning" })
      : Promise.resolve(false);
    ask.then(function (ok) { if (ok) doAssign(f, c.u, row); });
  }

  function doAssign(f, u, row) {
    var key = "assign:" + f.id;
    if (state.busy[key]) return;
    state.busy[key] = true;
    if (row) row.classList.add("is-sending");
    api("POST", "/api/dispatch/" + encodeURIComponent(f.id) + "/assign", { device_id: u.id }).then(function (res) {
      delete state.busy[key];
      if (row) row.classList.remove("is-sending");
      if (!res.ok) { toast("error", errorText(res.data)); refresh(); return; }
      toast("success", res.data.result === "ajoutee_aux_missions"
        ? "Fiche ajoutee aux missions de " + u.name + "."
        : "Fiche envoyee a " + u.name + " : en attente de sa prise en charge.");
      closePicker();
      refresh();
    });
  }

  // ------------------------------------------------------------------ vue telephone
  function setPane(pane) {
    state.pane = pane;
    document.querySelectorAll("#ds-switch .ds-switch-btn").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-pane") === pane);
    });
    document.querySelectorAll(".ds-pane").forEach(function (p) {
      p.classList.toggle("active", p.getAttribute("data-pane") === pane);
    });
  }

  // ------------------------------------------------------------------ init
  function init() {
    var soundPref = lsGet(LS_SOUND);
    state.soundOn = soundPref !== "0";
    updateNotifButton();
    if (!managed.length) return;   // page d'explication rendue par le gabarit

    state.cat = lsGet(LS_CAT) || "";
    if (state.cat && managed.indexOf(state.cat) < 0) state.cat = "";

    // Premier geste utilisateur : debloque l'audio et demande l'autorisation de notifier
    var firstGesture = function () {
      unlockAudio();
      askNotificationPermission();
      document.removeEventListener("click", firstGesture, true);
      document.removeEventListener("touchend", firstGesture, true);
    };
    document.addEventListener("click", firstGesture, true);
    document.addEventListener("touchend", firstGesture, true);

    var nb = $("ds-notif-btn");
    if (nb) nb.addEventListener("click", function () {
      state.soundOn = !state.soundOn;
      lsSet(LS_SOUND, state.soundOn ? "1" : "0");
      updateNotifButton();
      if (state.soundOn) { unlockAudio(); beep(); }
      toast("info", state.soundOn ? "Son des nouvelles fiches active." : "Son des nouvelles fiches coupe.");
    });
    var rb = $("ds-refresh");
    if (rb) rb.addEventListener("click", function () { refresh(); });

    document.querySelectorAll("#ds-switch .ds-switch-btn").forEach(function (b) {
      b.addEventListener("click", function () { setPane(b.getAttribute("data-pane")); });
    });

    var ov = $("ds-picker");
    if (ov) {
      ov.addEventListener("click", function (e) {
        if (e.target === ov || e.target.closest("[data-close]")) closePicker();
      });
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && state.pickerFiche) closePicker();
    });

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { clearTimeout(state.timer); return; }
      state.unread = 0;
      refresh();
    });
    window.addEventListener("focus", function () {
      if (state.unread) { state.unread = 0; render(); }
    });

    setInterval(tick, 1000);
    initScope().then(refresh);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
