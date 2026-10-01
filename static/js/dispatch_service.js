/* dispatch_service.js -- page /dispatch-service : file du service.
 *
 * Pour les responsables d'une categorie (Technique, Securite...) : les fiches
 * ouvertes a dispatcher, les propositions automatiques en cours, les fiches
 * engagees, et les unites Field de la categorie. Backend : dispatch_auto.py
 * (GET /api/dispatch/board, POST /api/dispatch/<id>/assign et /auto).
 *
 * Fenetre fiche (bouton "Ouvrir" ou clic sur le texte) : lecture seule de
 * GET /api/pcorg/detail/<id>, ajout d'action (POST /api/pcorg/comment/<id>)
 * et cloture (POST /api/pcorg/close/<id>) selon board.rights. Aucune
 * modification des elements initiaux de la fiche n'est proposee ici.
 *
 * Bouton "Nouvelle fiche" (si board.rights.can_create) : formulaire de
 * creation limite aux categories gerees, POST /api/pcorg/create.
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
  var LS_SORT = "ds_sort";   // {section: "desc"|"asc"} : ordre de chaque section
  var SECTIONS = ["queue", "proposing", "engaged", "done"];

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
    expanded: {},         // id fiche -> carte depliee (sinon ligne compacte)
    sort: {},             // section -> "desc" (plus recentes en haut) | "asc"
    pickerFiche: null,
    fiche: null,          // fenetre fiche ouverte : {id, data, seq, loading, textarea, ...}
    nf: null,             // formulaire "Nouvelle fiche" ouvert : {token, urgency, lat, lon, ...}
    metiersCache: {},     // categorie -> [metiers]
    selfCreated: {},      // fiches creees depuis ce poste : pas de son "nouvelle fiche"
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
  /** Filtre evenement de la page. Vide = tous les evenements actifs
   *  (epreuves en cours + SAISON), le defaut : la file montre alors les
   *  fiches de chaque evenement actif. */
  function scope() {
    var ev = $("ds-event"), yr = $("ds-year");
    var e = ev ? ev.value : "";
    return { event: e, year: e && yr ? yr.value : "" };
  }

  /** Evenement cible d'une creation : le filtre s'il y en a un, sinon
   *  l'evenement courant rendu par le serveur (epreuve active sinon SAISON). */
  function effectiveScope() {
    var s = scope();
    if (s.event && s.year) return s;
    var cur = (state.data && state.data.current) || state.eventCurrent || null;
    return cur ? { event: cur.event, year: String(cur.year) } : { event: "", year: "" };
  }
  // pcorg.js (assistant de creation) lit l'evenement courant par cette
  // fonction globale, definie par main.js sur l'accueil.
  if (typeof window.getCurrentEventYear !== "function") {
    window.getCurrentEventYear = function () { return effectiveScope(); };
  }

  function eventLabel(ev, yr) { return str(ev) + (yr != null && yr !== "" ? " " + yr : ""); }

  function updateScopeLabel() {
    var s = scope(), lbl = $("ds-scope-label");
    var yrSel = $("ds-year");
    if (yrSel) yrSel.disabled = !s.event;
    if (!lbl) return;
    if (s.event && s.year) { lbl.textContent = s.event + " " + s.year; return; }
    var evs = (state.data && state.data.active) || [];
    lbl.textContent = evs.length ? evs.map(function (a) { return eventLabel(a.event, a.year); }).join(" + ") : "Evenements actifs";
  }

  /** Filtre propre a la page (ne modifie plus la selection du cockpit). */
  var LS_DS_EVENT = "ds_event_filter";
  var LS_DS_YEAR = "ds_year_filter";
  function initScope() {
    var evSel = $("ds-event"), yrSel = $("ds-year");
    fetch("/api/event/current", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.current) state.eventCurrent = d.current; })
      .catch(function () { /* repli : current du board */ });
    if (!evSel || !yrSel) return Promise.resolve();
    var savedEv = lsGet(LS_DS_EVENT) || "", savedYr = parseInt(lsGet(LS_DS_YEAR) || lsGet(LS_YEAR) || "", 10);
    var cur = new Date().getFullYear();
    var prefYr = savedYr || cur;
    for (var y = 2023; y <= cur + 1; y++) {
      var o = el("option", "", y);
      o.value = String(y);
      if (y === prefYr) o.selected = true;
      yrSel.appendChild(o);
    }
    if (!yrSel.value) yrSel.value = String(cur);
    var all = el("option", "", "Tous les evenements actifs");
    all.value = "";
    evSel.appendChild(all);

    evSel.addEventListener("change", function () { lsSet(LS_DS_EVENT, evSel.value); onScopeChange(); });
    yrSel.addEventListener("change", function () { lsSet(LS_DS_YEAR, yrSel.value); onScopeChange(); });

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
        evSel.value = (savedEv && names.indexOf(savedEv) >= 0) ? savedEv : "";
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
    if (state.loading) return;
    state.loading = true;
    // Sans filtre : fiches de tous les evenements actifs (defaut serveur).
    var url = "/api/dispatch/board" + (s.event && s.year
      ? "?event=" + encodeURIComponent(s.event) + "&year=" + encodeURIComponent(s.year) : "");
    api("GET", url).then(function (res) {
      state.loading = false;
      if (!res.ok) {
        if (res.status === 403 && res.data.error === "not_manager") {
          setMessage("Vous n'etes plus responsable d'aucune categorie. Un administrateur doit vous ajouter a un groupe 'Responsable de service' dans Configuration > Groupes.", "warn");
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
      // SAISON accumule toute l'annee : seules les fiches ouvertes des
      // <older_days> derniers jours sont listees, les autres sont comptees.
      setMessage(d.older_open ? d.older_open + " fiche(s) SAISON ouverte(s) de plus de " +
        (d.older_days || 30) + " jours non affichee(s) (main courante)." : "", d.older_open ? "warn" : "");
      updateScopeLabel();
      render();
      if (state.fiche) loadFiche(true);
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
    if (Number(f.status_code) === 10) return "done";
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

  function visibleClosed() {
    var list = (state.data && state.data.closed) || [];
    if (!state.cat) return list;
    return list.filter(function (f) { return f.category === state.cat; });
  }

  // Date de reference du tri de chaque section : l'evenement qui l'a fait
  // entrer dans la section (mise en file, creation, engagement, cloture).
  var SORT_REF = {
    queue: function (f) {
      var d = f.dispatch || {};
      return parseMs(d.state === "queued" && d.queued_at ? d.queued_at : f.ts);
    },
    proposing: function (f) { return parseMs(f.ts); },
    engaged: function (f) { return parseMs((f.intervention || {}).engaged_at || f.ts); },
    done: function (f) { return parseMs(f.close_ts || f.ts); }
  };

  function sortDir(key) { return state.sort[key] === "asc" ? "asc" : "desc"; }

  function sortGroup(key, list) {
    var ref = SORT_REF[key], asc = sortDir(key) === "asc";
    list.sort(function (a, b) {
      var x = ref(a) || 0, y = ref(b) || 0;
      return asc ? x - y : y - x;
    });
  }

  /** Libelles des boutons de section (tri, tout deplier). */
  function updateSectionTools(groups) {
    SECTIONS.forEach(function (key) {
      var sb = document.querySelector('[data-sort="' + key + '"]');
      if (sb) {
        var asc = sortDir(key) === "asc";
        sb.textContent = "";
        sb.appendChild(icon(asc ? "arrow_upward" : "arrow_downward"));
        sb.appendChild(el("span", "ds-sec-btn-label", asc ? "Plus anciennes" : "Plus recentes"));
        sb.title = asc ? "Plus anciennes en haut (cliquer pour inverser)"
          : "Plus recentes en haut (cliquer pour inverser)";
      }
      var fb = document.querySelector('[data-fold="' + key + '"]');
      if (fb) {
        var list = groups[key] || [];
        var allOpen = list.length > 0 && list.every(function (f) { return !!state.expanded[f.id]; });
        fb.textContent = "";
        fb.appendChild(icon(allOpen ? "unfold_less" : "unfold_more"));
        fb.appendChild(el("span", "ds-sec-btn-label", allOpen ? "Tout plier" : "Tout deplier"));
        fb.disabled = !list.length;
        fb.setAttribute("data-open", allOpen ? "1" : "0");
      }
    });
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
    var groups = { queue: [], proposing: [], engaged: [], done: visibleClosed().slice() };
    fiches.forEach(function (f) { groups[bucket(f)].push(f); });
    SECTIONS.forEach(function (key) { sortGroup(key, groups[key]); });

    detectNewQueue();

    fillList("ds-list-queue", groups.queue, "Aucune fiche en attente.");
    fillList("ds-list-proposing", groups.proposing, "Aucune proposition en cours.");
    fillList("ds-list-engaged", groups.engaged, "Aucune fiche engagee.");
    fillList("ds-list-done", groups.done, "Aucune fiche terminee.");
    setCount("ds-count-queue", groups.queue.length);
    setCount("ds-count-proposing", groups.proposing.length);
    setCount("ds-count-engaged", groups.engaged.length);
    setCount("ds-count-done", groups.done.length);
    state.groups = groups;
    updateSectionTools(groups);
    setCount("ds-switch-fiches", groups.queue.length);

    renderUnits();
    updateCreateButton();
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
      if (state.seenQueue && !state.seenQueue[f.id] && !state.selfCreated[f.id]) fresh.push(f);
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

  /** Etat de la fiche en quelques mots, pour la ligne compacte. */
  function compactState(f, b) {
    var d = f.dispatch || {};
    var s = el("span", "ds-card-mini is-" + b);
    if (b === "queue") {
      if (d.state === "queued" && d.queued_at) {
        s.appendChild(icon("pending_actions"));
        s.appendChild(document.createTextNode("En file "));
        var qs = el("strong", "", sinceText(d.queued_at));
        qs.setAttribute("data-since", d.queued_at);
        s.appendChild(qs);
      } else {
        s.appendChild(icon("inbox"));
        s.appendChild(document.createTextNode("Sans unite"));
      }
    } else if (b === "proposing") {
      var cur = d.current || {};
      s.appendChild(icon("hourglass_top"));
      s.appendChild(document.createTextNode(cur.device_name || "Recherche..."));
      if (cur.expires_at) {
        var cd = el("span", "ds-countdown is-mini");
        cd.setAttribute("data-expires", cur.expires_at);
        s.appendChild(cd);
      }
    } else if (b === "engaged") {
      s.appendChild(icon("directions_car"));
      s.appendChild(el("strong", "", f.patrouille || "?"));
      var unit = unitByName(f.patrouille);
      var stat = f.unit_status || (unit && unit.status);
      if (stat) {
        var dot = el("span", "ds-status-dot");
        dot.style.background = (UNIT_STATUS[stat] || {}).color || "#94a3b8";
        dot.title = (UNIT_STATUS[stat] || {}).label || stat;
        s.appendChild(dot);
      }
    } else {
      s.appendChild(icon("task_alt"));
      s.appendChild(document.createTextNode("Terminee " + (f.close_ts ? hhmm(f.close_ts) : "")));
    }
    return s;
  }

  function fmtOperator(op) {
    op = str(op);
    return op.indexOf("field:") === 0 ? "Tablette " + op.slice(6) : op;
  }

  /** Fiche depliee : source, creation et derniere action de la chronologie. */
  function appendCardInfos(card, f) {
    var box = el("div", "ds-card-infos");
    function row(ic, label, content) {
      var r = el("div", "ds-info-row");
      r.appendChild(icon(ic));
      r.appendChild(el("span", "ds-info-label", label));
      var v = el("span", "ds-info-val");
      if (typeof content === "string") v.textContent = content;
      else v.appendChild(content);
      r.appendChild(v);
      box.appendChild(r);
      return v;
    }
    var src = f.source || {};
    var srcTxt = [src.label, src.who].filter(function (x) { return !!x; }).join(" - ");
    if (src.canal) srcTxt += (srcTxt ? " " : "") + "(" + src.canal + (src.radio_canal ? " " + src.radio_canal : "") + ")";
    if (srcTxt) row("call", "Source", srcTxt);
    var created = fmtOperator(f.operator);
    if (created || f.ts) {
      row("person_edit", "Creee", (created ? "par " + created + " " : "") + (f.ts ? "a " + hhmm(f.ts) : "") +
        (f.sql_id ? " - N. " + f.sql_id : ""));
    }
    var la = f.last_action;
    if (la && la.text) {
      var frag = document.createDocumentFragment();
      frag.appendChild(el("span", "ds-info-quote", la.text));
      var meta = el("span", "ds-info-meta");
      meta.appendChild(document.createTextNode(fmtOperator(la.operator) || "?"));
      if (la.ts) {
        meta.appendChild(document.createTextNode(", il y a "));
        var s = el("span", "", sinceText(la.ts));
        s.setAttribute("data-since", la.ts);
        meta.appendChild(s);
      }
      if (la.count > 1) meta.appendChild(document.createTextNode(" - " + la.count + " entrees"));
      frag.appendChild(meta);
      row("history", "Derniere action", frag);
    }
    if (box.childNodes.length) card.appendChild(box);
  }

  function setExpanded(card, id, open) {
    state.expanded[id] = !!open;
    card.classList.toggle("is-collapsed", !open);
    var chev = card.querySelector(".ds-card-chevron");
    if (chev) chev.textContent = open ? "expand_less" : "expand_more";
    var sum = card.querySelector(".ds-card-summary");
    if (sum) sum.setAttribute("aria-expanded", open ? "true" : "false");
    if (state.groups) updateSectionTools(state.groups);
  }

  function ficheCard(f) {
    var b = bucket(f);
    var st = catStyle(f.category);
    var open = !!state.expanded[f.id];
    var card = el("article", "ds-card ds-card-" + b + (open ? "" : " is-collapsed"));
    card.style.setProperty("--cat", st.color);
    card.setAttribute("data-id", f.id);

    // Ligne compacte, toujours visible : un clic plie / deplie la fiche
    var head = el("div", "ds-card-head ds-card-summary");
    head.setAttribute("role", "button");
    head.setAttribute("tabindex", "0");
    head.setAttribute("aria-expanded", open ? "true" : "false");
    head.title = "Deplier ou plier la fiche";
    head.appendChild(icon(open ? "expand_less" : "expand_more", "ds-card-chevron"));
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
    if (state.data && state.data.multi_event && f.event) {
      // Plusieurs evenements affiches (epreuve active + SAISON) : on nomme celui de la fiche.
      var evc = el("span", "ds-chip ds-chip-event");
      evc.appendChild(icon("event"));
      evc.appendChild(document.createTextNode(eventLabel(f.event, f.year)));
      evc.title = "Evenement : " + eventLabel(f.event, f.year);
      head.appendChild(evc);
    }
    if (f.metier) {
      // Metier (sous-classification) : "Cloture" est un METIER (barrieres),
      // pas l'etat de la fiche.
      var chip = el("span", "ds-chip ds-chip-metier");
      chip.appendChild(icon("construction"));
      chip.appendChild(document.createTextNode(f.metier));
      chip.title = "Metier : " + f.metier;
      head.appendChild(chip);
    }
    head.appendChild(el("span", "ds-card-oneline", f.text || "(sans description)"));
    head.appendChild(compactState(f, b));
    // Fiche close : delai de resolution (creation -> cloture), fige.
    // Fiche ouverte : temps ecoule depuis la creation, qui continue de tourner.
    var age = el("span", "ds-card-age");
    var tsMs = parseMs(f.ts), closeMs = parseMs(f.close_ts);
    if (b === "done" && tsMs != null && closeMs != null && closeMs >= tsMs) {
      age.classList.add("is-resolution");
      age.appendChild(icon("timer"));
      age.appendChild(el("span", "", fmtDuration(closeMs - tsMs)));
      age.title = "Delai de resolution : creee a " + hhmm(f.ts) + ", close a " + hhmm(f.close_ts);
    } else {
      age.appendChild(icon("schedule"));
      var ageTxt = el("span", "", sinceText(f.ts));
      ageTxt.setAttribute("data-since", f.ts || "");
      age.appendChild(ageTxt);
      age.title = "Ouverte depuis " + sinceText(f.ts) + " (creee a " + hhmm(f.ts) + ")";
    }
    head.appendChild(age);
    var cardRoot = card;   // `card` designe ensuite le corps depliable
    function toggle(e) {
      if (e && e.target && e.target.closest && e.target.closest("button, a")) return;
      setExpanded(cardRoot, f.id, cardRoot.classList.contains("is-collapsed"));
    }
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
    card.appendChild(head);

    var body = el("div", "ds-card-body");
    card.appendChild(body);
    card = body;   // la suite remplit le corps depliable

    // Le texte sert aussi de titre cliquable : ouvre la fiche
    var txt = el("div", "ds-card-text ds-card-open", f.text || "(sans description)");
    txt.setAttribute("role", "button");
    txt.setAttribute("tabindex", "0");
    txt.title = "Ouvrir la fiche";
    txt.addEventListener("click", function () { openFiche(f.id); });
    txt.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFiche(f.id); }
    });
    card.appendChild(txt);

    var where = [f.area, f.carroye ? "Carroye " + f.carroye : ""].filter(function (x) { return !!x; }).join(" - ");
    if (where || f.lat == null) {
      var loc = el("div", "ds-card-loc");
      loc.appendChild(icon(f.lat != null ? "location_on" : "location_off"));
      loc.appendChild(document.createTextNode(where || "Sans position"));
      card.appendChild(loc);
    }

    appendCardInfos(card, f);

    if (b === "done") {
      var dn = el("div", "ds-card-state is-done");
      dn.appendChild(icon("task_alt"));
      dn.appendChild(document.createTextNode("Terminee" + (f.close_ts ? " a " + hhmm(f.close_ts) : "") +
        (f.operator_close ? " par " + f.operator_close : "")));
      card.appendChild(dn);
      var inter = f.intervention || {};
      if (inter.outcome) {
        var oc = el("div", "ds-card-sub is-outcome");
        oc.appendChild(icon("assignment_turned_in"));
        oc.appendChild(document.createTextNode((OUTCOMES[inter.outcome] || inter.outcome) +
          (inter.device_name ? " (" + inter.device_name + ")" : "") + (inter.report ? " - " + inter.report : "")));
        card.appendChild(oc);
      }
    } else {
      appendDispatchInfo(card, f);
    }

    // Actions
    var act = el("div", "ds-card-actions");
    if (b !== "done") appendFicheActions(act, f);
    var openBtn = el("button", "btn btn-secondary ds-btn");
    openBtn.type = "button";
    openBtn.appendChild(icon("open_in_new"));
    openBtn.appendChild(document.createTextNode(" Ouvrir"));
    openBtn.addEventListener("click", function () { openFiche(f.id); });
    act.appendChild(openBtn);
    card.appendChild(act);
    return cardRoot;
  }

  /** Engager / Proposer auto, selon la section de la fiche (carte et fenetre fiche). */
  function appendFicheActions(act, f) {
    var b = bucket(f);
    if (b === "engaged") return;
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
  }

  /** Etat de dispatch, horaires d'intervention, dernier retour et historique des propositions. */
  function appendDispatchInfo(card, f) {
    var d = f.dispatch || {};
    var inter = f.intervention || {};
    var b = bucket(f);

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
    document.querySelectorAll("#ds-main [data-expires], #ds-fiche [data-expires]").forEach(function (n) {
      var t = parseMs(n.getAttribute("data-expires"));
      if (t == null) return;
      var left = Math.round((t - now) / 1000);
      n.textContent = left > 0 ? left + " s" : "expiree";
      n.classList.toggle("is-low", left <= 10);
    });
    document.querySelectorAll("#ds-main [data-since], #ds-fiche [data-since]").forEach(function (n) {
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
    syncBodyLock();
    refreshPicker();
    var close = ov.querySelector("[data-close]");
    if (close) close.focus();
  }

  function closePicker() {
    state.pickerFiche = null;
    var ov = $("ds-picker");
    if (ov) ov.hidden = true;
    syncBodyLock();
  }

  /** Bloque le defilement de la page tant qu'une des deux fenetres est ouverte. */
  function syncBodyLock() {
    document.body.classList.toggle("ds-sheet-open", !!(state.pickerFiche || state.fiche || state.nf));
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

  // ------------------------------------------------------------------ fenetre fiche
  // Lecture seule des elements initiaux (description, categorie, urgence,
  // position, zone...) : AUCUNE modification depuis cette page. Seules
  // actions : ajouter une action a la chronologie, cloturer, et engager /
  // proposer comme depuis la carte. GET /api/pcorg/detail/<id>.

  var COMMENT_MAX = 2000;
  var PARIS_FMT = null;

  /** Date et heure a Paris : {ymd, day "dd/mm", hm "HH:MM"}. */
  function parisParts(t) {
    try {
      if (!PARIS_FMT) {
        PARIS_FMT = new Intl.DateTimeFormat("fr-FR", {
          timeZone: "Europe/Paris", year: "numeric", month: "2-digit", day: "2-digit",
          hour: "2-digit", minute: "2-digit", hour12: false
        });
      }
      var o = {};
      PARIS_FMT.formatToParts(new Date(t)).forEach(function (p) { o[p.type] = p.value; });
      return { ymd: o.year + o.month + o.day, day: o.day + "/" + o.month, hm: (o.hour === "24" ? "00" : o.hour) + ":" + o.minute };
    } catch (e) {
      var d = new Date(t);
      return {
        ymd: "" + d.getFullYear() + pad2(d.getMonth() + 1) + pad2(d.getDate()),
        day: pad2(d.getDate()) + "/" + pad2(d.getMonth() + 1),
        hm: pad2(d.getHours()) + ":" + pad2(d.getMinutes())
      };
    }
  }

  /** "HH:MM" si aujourd'hui (heure de Paris), "dd/mm HH:MM" sinon. */
  function fmtParis(iso) {
    var t = parseMs(iso);
    if (t == null) return "--:--";
    var p = parisParts(t);
    return p.ymd === parisParts(nowMs()).ymd ? p.hm : p.day + " " + p.hm;
  }

  function operatorLabel(op) {
    op = String(op || "");
    if (op.indexOf("field:") === 0) return "Tablette " + op.slice(6);
    return op;
  }

  /** URL de photo acceptee : chemin du meme site ou http(s), jamais javascript: ni data:. */
  function safeUrl(u) {
    u = String(u || "").trim();
    if (/^\/(?!\/)/.test(u) || /^https?:\/\//i.test(u)) return u;
    return "";
  }

  function str(v) {
    if (v == null || typeof v === "object") return "";
    return String(v).trim();
  }

  /** Message d'erreur du serveur, tel quel (les routes pcorg renvoient un texte lisible). */
  function serverMsg(data) {
    if (!data) return "Erreur inconnue.";
    return ERRORS[data.error] || data.error || data.message || "Erreur inconnue.";
  }

  function rights() {
    return (state.data && state.data.rights) || {};
  }

  function isTyping() {
    var f = state.fiche;
    return !!(f && f.textarea && document.activeElement === f.textarea);
  }

  function hasDraft() {
    var f = state.fiche;
    return !!(f && f.textarea && f.textarea.value.trim());
  }

  function openFiche(id) {
    if (!id) return;
    unlockAudio();
    var ov = $("ds-fiche");
    if (!ov) return;
    if (!state.fiche || state.fiche.id !== id) {
      state.fiche = { id: id, data: null, seq: 0, loading: false, textarea: null, composer: null, footKey: "" };
      var body = $("ds-fiche-body"), foot = $("ds-fiche-foot"), title = $("ds-fiche-title"), head = $("ds-fiche-headline");
      if (body) { body.textContent = ""; body.appendChild(el("div", "ds-empty", "Chargement de la fiche...")); }
      if (foot) { foot.textContent = ""; foot.hidden = true; }
      if (title) title.textContent = "Fiche";
      if (head) head.textContent = "";
      var sub = $("ds-fiche-subcat");
      if (sub) sub.textContent = "";
      var banner = $("ds-fiche-head");
      if (banner) { banner.classList.remove("is-banner"); banner.style.background = ""; }
      destroyFicheMap();
    }
    ov.hidden = false;
    syncBodyLock();
    loadFiche(false);
    var close = ov.querySelector("[data-close]");
    if (close) close.focus();
  }

  function closeFicheModal(force) {
    if (!state.fiche) return;
    if (!force && hasDraft()) {
      var ask = typeof window.showConfirmToast === "function"
        ? window.showConfirmToast("Abandonner l'action en cours de saisie ?",
          { okLabel: "Abandonner", cancelLabel: "Continuer", type: "warning" })
        : Promise.resolve(true);
      ask.then(function (ok) { if (ok) closeFicheModal(true); });
      return;
    }
    destroyFicheMap();
    state.fiche = null;
    var ov = $("ds-fiche");
    if (ov) ov.hidden = true;
    syncBodyLock();
  }

  // ---- Mini-carte de la fiche (meme vignette figee que l'accueil) ----------
  // Le corps est reconstruit a chaque rafraichissement (5 s) : la carte et son
  // conteneur sont gardes et re-attaches tant que la position ne change pas,
  // sinon elle clignoterait et rechargerait ses tuiles a chaque cycle.
  var ficheMap = { el: null, map: null, key: "" };

  function destroyFicheMap() {
    if (ficheMap.map) { try { ficheMap.map.remove(); } catch (e) { /* deja detruite */ } }
    ficheMap = { el: null, map: null, key: "" };
  }

  function ficheMapNode(lat, lon, st) {
    var key = (+lat).toFixed(6) + "," + (+lon).toFixed(6) + "," + st.color;
    if (ficheMap.el && ficheMap.key === key) {
      setTimeout(function () { if (ficheMap.map) ficheMap.map.invalidateSize(); }, 0);
      return ficheMap.el;
    }
    destroyFicheMap();
    var node = el("div", "pcorg-fiche-minimap ds-fiche-minimap");
    if (typeof window.L === "undefined") {
      node.classList.add("empty");
      node.textContent = "Carte indisponible";
      return node;
    }
    ficheMap.el = node;
    ficheMap.key = key;
    setTimeout(function () {
      if (ficheMap.el !== node) return;
      var map = window.L.map(node, {
        center: [+lat, +lon], zoom: 16, zoomControl: false,
        attributionControl: false, dragging: false, scrollWheelZoom: false,
        doubleClickZoom: false, touchZoom: false, boxZoom: false, keyboard: false
      });
      window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxNativeZoom: 19, maxZoom: 22
      }).addTo(map);
      var pin = document.createElement("div");
      pin.className = "pcorg-pin";
      pin.style.background = st.color;
      pin.appendChild(icon(st.icon));
      window.L.marker([+lat, +lon], {
        icon: window.L.divIcon({ className: "", html: pin.outerHTML, iconSize: [36, 36], iconAnchor: [18, 36] })
      }).addTo(map);
      ficheMap.map = map;
      setTimeout(function () { map.invalidateSize(); }, 150);
    }, 60);
    return node;
  }

  /** (Re)charge le detail. silent : sans message de chargement, et jamais pendant la saisie. */
  function loadFiche(silent) {
    var fs = state.fiche;
    if (!fs || fs.loading) return;
    if (silent && isTyping()) return;
    fs.loading = true;
    var seq = ++fs.seq;
    api("GET", "/api/pcorg/detail/" + encodeURIComponent(fs.id)).then(function (res) {
      if (state.fiche !== fs || seq !== fs.seq) return;
      fs.loading = false;
      if (!res.ok) {
        if (silent && fs.data && (res.status === 0 || res.data.error === "reseau")) return;   // coupure : garder l'affichage
        var body = $("ds-fiche-body");
        if (body) {
          body.textContent = "";
          body.appendChild(el("div", "ds-message is-error",
            res.status === 404 ? "Fiche introuvable (supprimee ?)." : "Fiche indisponible : " + serverMsg(res.data)));
        }
        var foot = $("ds-fiche-foot");
        if (foot) { foot.textContent = ""; foot.hidden = true; }
        fs.footKey = "";
        return;
      }
      if (silent && isTyping()) return;   // saisie commencee pendant la requete
      fs.data = res.data;
      renderFiche(false);
    });
  }

  function renderFiche(scrollBottom) {
    var fs = state.fiche;
    if (!fs || !fs.data) return;
    renderFicheHead(fs.data);
    renderFicheBody(fs.data, scrollBottom);
    renderFicheFoot(fs.data);
  }

  function isClosed(d) { return d && Number(d.status_code) === 10; }

  function renderFicheHead(d) {
    var title = $("ds-fiche-title"), head = $("ds-fiche-headline");
    var st = catStyle(d.category);
    // Bandeau a la couleur de la categorie, comme la fiche de l'accueil
    var banner = $("ds-fiche-head");
    if (banner) {
      banner.classList.add("is-banner");
      banner.style.background = st.color;
    }
    var sub = $("ds-fiche-subcat");
    if (sub) {
      var cc = d.content_category || {};
      sub.textContent = str(cc.sous_classification || cc.classification || cc.typedemande);
    }
    if (title) {
      title.textContent = "";
      var ci = icon(st.icon, "ds-fiche-cat-icon");
      title.appendChild(ci);
      title.appendChild(document.createTextNode(" " + (shortCat(d.category) || "Fiche")));
    }
    if (!head) return;
    head.textContent = "";
    if (d.niveau_urgence) {
      var u = el("span", "ds-urg", d.niveau_urgence);
      u.style.background = URGENCY_COLORS[d.niveau_urgence] || "#6b7280";
      head.appendChild(u);
    }
    var closed = isClosed(d);
    var pill = el("span", "ds-fiche-state" + (closed ? " is-closed" : " is-open"));
    pill.appendChild(icon(closed ? "check_circle" : "radio_button_checked"));
    pill.appendChild(document.createTextNode(closed ? "Close" : "Ouverte"));
    head.appendChild(pill);
    var created = d.ts || d.created_at;
    if (created) head.appendChild(el("span", "ds-fiche-created", "Creee " + fmtParis(created)));
    if (d.sql_id != null && d.sql_id !== "") head.appendChild(el("span", "ds-fiche-created", "N. " + d.sql_id));
  }

  function renderFicheBody(d, scrollBottom) {
    var body = $("ds-fiche-body");
    if (!body) return;
    var prevScroll = body.scrollTop;
    body.textContent = "";
    var cc = d.content_category || {};
    var closed = isClosed(d);

    // Description
    var text = str(d.text);
    var full = str(d.text_full);
    if (full.length > text.length) text = full;
    var sd = section("Description", "description");
    sd.appendChild(el("div", "ds-fiche-text", text || "(sans description)"));
    body.appendChild(sd);

    // Elements cles
    var fields = [
      ["Zone", str(d.area_desc)],
      ["Carroyage", str(cc.carroye)],
      ["Metier", str(cc.sous_classification)],
      ["Appelant", [str(cc.appelant), str(cc.telephone)].filter(function (x) { return !!x; }).join(" - ")],
      ["Unite engagee", str(cc.patrouille)],
      ["Creee par", str(d.operator) + (str(d.operator_group) ? " (" + str(d.operator_group) + ")" : "")]
    ];
    if (closed) {
      fields.push(["Cloturee", (d.close_ts ? fmtParis(d.close_ts) : "") +
        (str(d.operator_close) ? " par " + str(d.operator_close) : "")]);
    }
    var dl = el("dl", "ds-fields");
    fields.forEach(function (p) {
      if (!p[1]) return;
      dl.appendChild(el("dt", "", p[0]));
      dl.appendChild(el("dd", "", p[1]));
    });
    var lat = d.lat, lon = d.lon;
    if (lat != null && lon != null && isFinite(+lat) && isFinite(+lon)) {
      dl.appendChild(el("dt", "", "Position"));
      var dd = el("dd", "");
      var a = el("a", "ds-map-link");
      a.href = "https://www.google.com/maps?q=" + (+lat) + "," + (+lon);
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.appendChild(icon("map"));
      a.appendChild(document.createTextNode(" Voir sur la carte"));
      dd.appendChild(a);
      dl.appendChild(dd);
    } else {
      dl.appendChild(el("dt", "", "Position"));
      dl.appendChild(el("dd", "is-muted", "Sans position"));
    }
    var sk = section("Elements", "info");
    // Champs a gauche, mini-carte a droite (empilees sur telephone)
    var row = el("div", "ds-fiche-inforow");
    row.appendChild(dl);
    if (lat != null && lon != null && isFinite(+lat) && isFinite(+lon)) {
      row.appendChild(ficheMapNode(lat, lon, catStyle(d.category)));
    } else {
      destroyFicheMap();
    }
    sk.appendChild(row);
    body.appendChild(sk);

    // Dispatch / intervention : la fiche du tableau porte l'etat le plus riche
    var bf = closed ? null : findFiche(d.id);
    var sdisp = section("Dispatch et intervention", "directions_car");
    var box = el("div", "ds-fiche-dispatch");
    if (bf) appendDispatchInfo(box, bf);
    else appendInterventionFromDetail(box, d);
    if (box.childNodes.length) {
      sdisp.appendChild(box);
      body.appendChild(sdisp);
    }

    // Chronologie (la plus recente en dernier)
    var hist = Array.isArray(d.comment_history) ? d.comment_history : [];
    var sc = section("Chronologie (" + hist.length + ")", "history");
    if (!hist.length) sc.appendChild(el("div", "ds-empty", "Aucune entree."));
    else sc.appendChild(renderChronology(hist, catStyle(d.category).color));
    body.appendChild(sc);

    body.scrollTop = scrollBottom ? body.scrollHeight : prevScroll;
  }

  function section(title, ic) {
    var s = el("section", "ds-fsec");
    var h = el("div", "ds-fsec-title");
    h.appendChild(icon(ic));
    h.appendChild(document.createTextNode(title));
    s.appendChild(h);
    return s;
  }

  /** Repli pour une fiche absente du tableau (close, ou hors des 300 dernieres). */
  function appendInterventionFromDetail(box, d) {
    var inter = d.intervention || {};
    var disp = d.dispatch || {};
    if (!isClosed(d) && disp.state === "proposing" && disp.current_device) {
      var p = el("div", "ds-card-state is-proposing");
      p.appendChild(icon("hourglass_top"));
      p.appendChild(document.createTextNode("Proposee a " + disp.current_device));
      if (disp.expires_at) {
        var cd = el("span", "ds-countdown");
        cd.setAttribute("data-expires", disp.expires_at);
        p.appendChild(cd);
      }
      box.appendChild(p);
    } else if (!isClosed(d) && disp.state === "queued") {
      var q = el("div", "ds-card-state is-queue");
      q.appendChild(icon("pending_actions"));
      q.appendChild(document.createTextNode("En file du service" + (disp.queue_reason ? " - " + disp.queue_reason : "")));
      box.appendChild(q);
    }
    var times = el("div", "ds-times");
    if (inter.device_name) times.appendChild(el("span", "ds-time", "Unite : " + inter.device_name));
    [["Engagee", inter.engaged_at], ["Sur place", inter.arrived_at], ["Terminee", inter.done_at]].forEach(function (p) {
      if (!p[1]) return;
      var s = el("span", "ds-time");
      s.appendChild(el("b", "", p[0] + " "));
      s.appendChild(document.createTextNode(fmtParis(p[1])));
      times.appendChild(s);
    });
    if (times.childNodes.length) box.appendChild(times);
    if (inter.outcome) {
      var oc = el("div", "ds-card-sub is-outcome");
      oc.appendChild(icon("assignment_return"));
      oc.appendChild(document.createTextNode("Retour : " + (OUTCOMES[inter.outcome] || inter.outcome) +
        (inter.report ? " - " + inter.report : "")));
      box.appendChild(oc);
    }
  }

  /** Chronologie simplifiee, en lecture seule (meme decoupage que pcorg.js renderChronology). */
  function renderChronology(history, color) {
    var wrap = el("ol", "ds-chrono");
    wrap.style.setProperty("--cat", color);
    history.forEach(function (entry) {
      entry = entry || {};
      var kind = entry.kind || "comment";
      var closedSt = kind === "status" && /^termin/i.test(entry.status_to || "");
      var reopened = kind === "status" && /^termin/i.test(entry.status_from || "");
      var li = el("li", "ds-chrono-entry kind-" + kind + (closedSt ? " is-closed" : "") +
        (reopened ? " is-reopened" : "") + (entry.synthetic ? " is-synthetic" : ""));

      var head = el("div", "ds-chrono-head");
      var ts = el("span", "ds-chrono-ts", fmtParis(entry.ts));
      var t = parseMs(entry.ts);
      if (t != null) ts.title = new Date(t).toLocaleString("fr-FR", { timeZone: "Europe/Paris" });
      head.appendChild(ts);
      head.appendChild(el("span", "ds-chrono-op",
        operatorLabel(entry.operator) || (entry.synthetic ? "operateur non renseigne" : "")));
      li.appendChild(head);

      if (kind === "status" && entry.status_to) {
        var pill = el("span", "ds-chrono-status" + (closedSt ? " is-closed" : "") + (reopened ? " is-reopened" : ""));
        pill.appendChild(icon(closedSt ? "check_circle" : (reopened ? "restart_alt" : "sync_alt")));
        pill.appendChild(document.createTextNode((entry.status_from ? entry.status_from + " -> " : "") + entry.status_to));
        li.appendChild(pill);
      }

      var bodyText = entry.body != null ? String(entry.body) : String(entry.text || entry.comment || "");
      if (kind === "system" && bodyText) {
        var sys = el("div", "ds-chrono-system");
        sys.appendChild(icon("tune"));
        sys.appendChild(el("span", "", bodyText));
        li.appendChild(sys);
        bodyText = "";
      }

      var changes = Array.isArray(entry.changes) ? entry.changes : [];
      if (changes.length) {
        var ul = el("ul", "ds-chrono-changes");
        changes.forEach(function (c) {
          if (!c) return;
          var ci = el("li", "");
          ci.appendChild(el("b", "", c.field || "?"));
          ci.appendChild(document.createTextNode(" : "));
          if (c.old) {
            ci.appendChild(el("span", "ds-chrono-old", c.old));
            ci.appendChild(document.createTextNode(" -> "));
          }
          ci.appendChild(el("span", "", c["new"] || "(vide)"));
          ul.appendChild(ci);
        });
        li.appendChild(ul);
      }

      if (bodyText) li.appendChild(el("div", "ds-chrono-text", bodyText));
      else if (kind === "empty") li.appendChild(el("div", "ds-chrono-empty", "Mise a jour sans commentaire"));

      var photos = (Array.isArray(entry.photos) && entry.photos.length) ? entry.photos
        : (entry.photo ? [{ photo: entry.photo, thumb: entry.thumb }] : []);
      var pw = el("div", "ds-chrono-photos");
      photos.forEach(function (p) {
        if (!p) return;
        var full = safeUrl(typeof p === "string" ? p : p.photo);
        var thumb = safeUrl(typeof p === "string" ? p : (p.thumb || p.photo));
        if (!full && !thumb) return;
        var a = el("a", "ds-chrono-photo");
        a.href = full || thumb;
        a.target = "_blank";
        a.rel = "noopener";
        a.title = "Ouvrir la photo";
        var img = el("img", "");
        img.src = thumb || full;
        img.alt = "Photo";
        img.loading = "lazy";
        a.appendChild(img);
        pw.appendChild(a);
      });
      if (pw.childNodes.length) li.appendChild(pw);

      if (Array.isArray(entry.codes) && entry.codes.length) {
        var cw = el("div", "ds-chips");
        entry.codes.forEach(function (c) {
          if (!c) return;
          cw.appendChild(el("span", "ds-chip", String(c.format || "manuel").toUpperCase() + " " + (c.value || "")));
        });
        li.appendChild(cw);
      }
      wrap.appendChild(li);
    });
    return wrap;
  }

  /** Pied : saisie d'action + Engager / Proposer auto + Cloturer. Reconstruit
   *  seulement si son contenu change ; la zone de saisie est conservee. */
  function renderFicheFoot(d) {
    var fs = state.fiche, foot = $("ds-fiche-foot");
    if (!fs || !foot) return;
    var r = rights();
    var closed = isClosed(d);
    var bf = closed ? null : findFiche(d.id);
    var key = [closed ? "c" : "o", bf ? bucket(bf) : "-", r.can_comment ? 1 : 0, r.can_close ? 1 : 0,
      bf && state.busy["auto:" + bf.id] ? 1 : 0].join("|");
    if (key === fs.footKey) return;
    fs.footKey = key;
    foot.textContent = "";
    if (closed) {
      foot.hidden = false;
      foot.appendChild(el("div", "ds-fiche-closed-note", "Fiche close : consultation seule."));
      return;
    }

    if (r.can_comment) {
      if (!fs.composer) fs.composer = buildComposer();
      foot.appendChild(fs.composer);
    }
    var act = el("div", "ds-fiche-actions");
    if (bf) appendFicheActions(act, bf);
    if (r.can_close) {
      var cl = el("button", "btn ds-btn ds-btn-close");
      cl.type = "button";
      cl.appendChild(icon("task_alt"));
      cl.appendChild(document.createTextNode(" Cloturer"));
      cl.addEventListener("click", function () { closeFiche(cl); });
      act.appendChild(cl);
    }
    if (act.childNodes.length) foot.appendChild(act);
    foot.hidden = !foot.childNodes.length;
  }

  function buildComposer() {
    var fs = state.fiche;
    var wrap = el("div", "ds-composer");
    var ta = el("textarea", "form-input ds-composer-input");
    ta.rows = 2;
    ta.maxLength = COMMENT_MAX;
    ta.placeholder = "Ajouter une action (ce qui a ete fait, decide, constate)...";
    ta.setAttribute("aria-label", "Nouvelle action");
    var row = el("div", "ds-composer-row");
    var count = el("span", "ds-composer-count", "0 / " + COMMENT_MAX);
    var btn = el("button", "btn btn-primary ds-btn");
    btn.type = "button";
    btn.appendChild(icon("add_comment"));
    btn.appendChild(document.createTextNode(" Ajouter l'action"));
    ta.addEventListener("input", function () {
      count.textContent = ta.value.length + " / " + COMMENT_MAX;
      count.classList.toggle("is-warn", ta.value.length > COMMENT_MAX - 100);
    });
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submitComment(ta, btn, count); }
    });
    btn.addEventListener("click", function () { submitComment(ta, btn, count); });
    row.appendChild(count);
    row.appendChild(btn);
    wrap.appendChild(ta);
    wrap.appendChild(row);
    fs.textarea = ta;
    return wrap;
  }

  function submitComment(ta, btn, count) {
    var fs = state.fiche;
    if (!fs) return;
    var text = ta.value.trim();
    if (!text) { toast("warning", "Saisir le texte de l'action."); ta.focus(); return; }
    if (text.length > COMMENT_MAX) { toast("warning", "Action trop longue (" + COMMENT_MAX + " caracteres max)."); return; }
    var key = "comment:" + fs.id;
    if (state.busy[key]) return;
    state.busy[key] = true;
    btn.disabled = true;
    var id = fs.id;
    api("POST", "/api/pcorg/comment/" + encodeURIComponent(id), { text: text }).then(function (res) {
      delete state.busy[key];
      btn.disabled = false;
      if (!res.ok) {
        toast("error", serverMsg(res.data));
        if (res.status === 403 || res.status === 409 || res.status === 404) loadFiche(true);
        return;
      }
      toast("success", "Action ajoutee a la fiche.");
      if (state.fiche && state.fiche.id === id) {
        ta.value = "";
        count.textContent = "0 / " + COMMENT_MAX;
        count.classList.remove("is-warn");
        ta.blur();
        var e = res.data.entry || {};
        var d = state.fiche.data;
        if (d) {
          if (!Array.isArray(d.comment_history)) d.comment_history = [];
          d.comment_history.push({
            ts: e.ts || new Date().toISOString(), operator: e.operator || "",
            kind: "comment", text: e.text || text, body: e.text || text, changes: []
          });
          renderFicheBody(d, true);
        }
      }
      refresh();   // recharge aussi la fiche (entree decoree par le serveur)
    });
  }

  function closeFiche(btn) {
    var fs = state.fiche;
    if (!fs) return;
    if (typeof window.showPromptToast !== "function") { toast("error", "Confirmation indisponible : rechargez la page."); return; }
    var id = fs.id;
    var draft = fs.textarea ? fs.textarea.value.trim() : "";
    window.showPromptToast("Cloturer cette fiche ? Motif de cloture (facultatif) :", {
      okLabel: "Cloturer", cancelLabel: "Annuler", type: "warning", defaultValue: draft
    }).then(function (motif) {
      if (motif === null || motif === undefined) return;
      motif = String(motif).trim().slice(0, COMMENT_MAX);
      var key = "close:" + id;
      if (state.busy[key]) return;
      state.busy[key] = true;
      if (btn) btn.disabled = true;
      api("POST", "/api/pcorg/close/" + encodeURIComponent(id), { comment: motif }).then(function (res) {
        delete state.busy[key];
        if (btn) btn.disabled = false;
        if (!res.ok) {
          if (res.status === 404) {
            toast("warning", "Fiche deja close.");
            if (state.fiche && state.fiche.id === id) loadFiche(false);
            refresh();
          } else {
            toast("error", serverMsg(res.data));
          }
          return;
        }
        toast("success", "Fiche cloturee.");
        if (state.fiche && state.fiche.id === id) closeFicheModal(true);
        refresh();
      });
    });
  }

  // ------------------------------------------------------------------ nouvelle fiche
  // POST /api/pcorg/create (droit de groupe "Creer des fiches" : rights.can_create).
  // Un client_token par ouverture du formulaire : un double clic ou un renvoi
  // retombe sur la meme fiche cote serveur ({ok, id, duplicate}).

  var NF_TEXT_MAX = 1000;
  var NF_URGENCIES = ["IMP", "UR", "UA", "EU"];   // niveaux 1 a 4
  // Memes libelles que pcorg.js URGENCY_LABELS (sans accents)
  var NF_URGENCY_LABELS = {
    "PCO.Secours": { EU: "Detresse vitale", UA: "Urgence absolue", UR: "Urgence relative", IMP: "Implique medical" },
    "PCO.Securite": { EU: "Danger immediat", UA: "Incident grave", UR: "Incident en cours", IMP: "Temoin / implique" },
    "": { EU: "Urgence extreme", UA: "Urgence prioritaire", UR: "Situation stable", IMP: "Implique" }
  };

  function nfUrgencyLabel(cat, code) {
    return (NF_URGENCY_LABELS[cat] || NF_URGENCY_LABELS[""])[code] || code;
  }

  function newToken() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    } catch (e) { /* contexte non securise */ }
    return "nf-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
  }

  function boardCategories() {
    return (state.data && state.data.categories) || managed;
  }

  function updateCreateButton() {
    var b = $("ds-new-btn");
    if (b) b.hidden = !rights().can_create;
  }

  function nfCategory() {
    var cats = boardCategories();
    if (cats.length === 1) return cats[0];
    var sel = $("ds-nf-cat");
    return sel ? sel.value : "";
  }

  function nfHasContent() {
    var nf = state.nf;
    if (!nf) return false;
    return !!(($("ds-nf-text") || {}).value || "").trim() || !!(($("ds-nf-area") || {}).value || "").trim() ||
      !!(($("ds-nf-caller") || {}).value || "").trim() || !!(($("ds-nf-metier") || {}).value) || nf.lat != null;
  }

  function openNewFiche() {
    if (!rights().can_create) { toast("warning", "Votre groupe ne permet pas de creer des fiches."); return; }
    // Filtre de la page s'il y en a un, sinon l'evenement courant (epreuve
    // active, sinon SAISON).
    var s = effectiveScope();
    if (!s.event || !s.year) { toast("warning", "Evenement courant indisponible, choisir un evenement."); return; }
    // Meme assistant 3 etapes que la carte de l'accueil (pcorg.js en mode
    // creation seule) : position + carroyage, categorie + source, details de
    // categorie. Le formulaire simplifie ci-dessous ne sert plus que de repli.
    if (window.PcorgCreate) {
      window.selectedEvent = s.event;
      window.selectedYear = s.year;
      var cats = state.cat ? [state.cat] : boardCategories();
      var opened = window.PcorgCreate.open({
        categories: cats,
        onCreated: function (id) {
          if (id) state.selfCreated[id] = true;
          refresh();
          if (id) setTimeout(function () { openFiche(id); }, 400);
        }
      });
      if (opened) return;
    }
    var ov = $("ds-new");
    if (!ov) return;
    unlockAudio();
    state.nf = { token: newToken(), urgency: "UR", lat: null, lon: null, acc: null, sending: false, geoSeq: 0, metierSeq: 0 };

    var sub = $("ds-new-sub");
    if (sub) sub.textContent = s.event + " " + s.year;

    // Categorie : limitee aux categories gerees ; figee s'il n'y en a qu'une
    var cats = boardCategories();
    var sel = $("ds-nf-cat"), fixed = $("ds-nf-cat-fixed");
    if (sel) {
      sel.textContent = "";
      if (cats.length > 1) {
        var ph = el("option", "", "Choisir une categorie...");
        ph.value = "";
        sel.appendChild(ph);
      }
      cats.forEach(function (c) {
        var o = el("option", "", shortCat(c));
        o.value = c;
        sel.appendChild(o);
      });
      sel.value = cats.length === 1 ? cats[0] : (state.cat && cats.indexOf(state.cat) >= 0 ? state.cat : "");
      sel.hidden = cats.length === 1;
    }
    if (fixed) {
      fixed.hidden = cats.length !== 1;
      fixed.textContent = "";
      if (cats.length === 1) {
        var ci = icon(catStyle(cats[0]).icon);
        ci.style.color = catStyle(cats[0]).color;
        fixed.appendChild(ci);
        fixed.appendChild(document.createTextNode(" " + shortCat(cats[0])));
      }
    }

    ["ds-nf-text", "ds-nf-area", "ds-nf-caller"].forEach(function (id) { var n = $(id); if (n) n.value = ""; });
    renderUrgencyButtons();
    updatePosition("");
    loadMetiers();
    nfUpdate();

    ov.hidden = false;
    syncBodyLock();
    var first = cats.length > 1 && sel && !sel.value ? sel : $("ds-nf-text");
    if (first) { try { first.focus(); } catch (e) { /* rien */ } }
  }

  function closeNewFiche(force) {
    if (!state.nf) return;
    if (!force && state.nf.sending) return;
    if (!force && nfHasContent()) {
      var ask = typeof window.showConfirmToast === "function"
        ? window.showConfirmToast("Abandonner la fiche en cours de saisie ?",
          { okLabel: "Abandonner", cancelLabel: "Continuer", type: "warning" })
        : Promise.resolve(true);
      ask.then(function (ok) { if (ok) closeNewFiche(true); });
      return;
    }
    state.nf = null;
    var ov = $("ds-new");
    if (ov) ov.hidden = true;
    syncBodyLock();
  }

  function renderUrgencyButtons() {
    var box = $("ds-nf-urg");
    if (!box || !state.nf) return;
    box.textContent = "";
    var cat = nfCategory();
    NF_URGENCIES.forEach(function (code) {
      var label = nfUrgencyLabel(cat, code);
      var b = el("button", "ds-nf-urg-btn");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("data-code", code);
      b.title = code + " - " + label;
      b.style.setProperty("--urg", URGENCY_COLORS[code] || "#6b7280");
      b.appendChild(el("strong", "", code));
      b.appendChild(el("span", "", label));
      b.addEventListener("click", function () {
        if (!state.nf) return;
        state.nf.urgency = code;
        syncUrgencyButtons();
        nfUpdate();
      });
      box.appendChild(b);
    });
    syncUrgencyButtons();
  }

  function syncUrgencyButtons() {
    var cur = state.nf ? state.nf.urgency : "";
    document.querySelectorAll("#ds-nf-urg .ds-nf-urg-btn").forEach(function (b) {
      var on = b.getAttribute("data-code") === cur;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
  }

  /** Meme regle que dispatch_auto.wants_auto (fiche creee sans unite). */
  function willAutoPropose(cat, urgency) {
    var cfg = ((state.data && state.data.config) || {})[cat] || {};
    if (cfg.mode === "always") return true;
    if (cfg.mode === "urgency") return (cfg.levels || []).indexOf(urgency) >= 0;
    return false;
  }

  /** Metiers de la categorie choisie (GET /api/dispatch/metiers), avec cache. */
  function loadMetiers() {
    var nf = state.nf, sel = $("ds-nf-metier");
    if (!nf || !sel) return;
    var cat = nfCategory();
    var seq = ++nf.metierSeq;
    var fill = function (list, note) {
      if (state.nf !== nf || seq !== nf.metierSeq) return;
      var prev = sel.value;
      sel.textContent = "";
      var none = el("option", "", note || "Non precise");
      none.value = "";
      sel.appendChild(none);
      (list || []).forEach(function (m) {
        var o = el("option", "", m);
        o.value = m;
        sel.appendChild(o);
      });
      sel.value = prev && (list || []).indexOf(prev) >= 0 ? prev : "";
      sel.disabled = !(list || []).length;
    };
    if (!cat) { fill([], "Choisir d'abord une categorie"); return; }
    if (state.metiersCache[cat]) { fill(state.metiersCache[cat]); return; }
    fill([], "Chargement...");
    api("GET", "/api/dispatch/metiers?category=" + encodeURIComponent(cat)).then(function (res) {
      if (!res.ok) { fill([], "Metiers indisponibles"); return; }
      var list = (Array.isArray(res.data.metiers) ? res.data.metiers : [])
        .map(function (m) { return String(m || "").trim(); })
        .filter(function (m) { return !!m; });
      state.metiersCache[cat] = list;
      fill(list, list.length ? "" : "Aucun metier pour cette categorie");
    });
  }

  function updatePosition(message) {
    var nf = state.nf;
    var geo = $("ds-nf-geo"), clr = $("ds-nf-geo-clear"), st = $("ds-nf-pos-state");
    var has = !!(nf && nf.lat != null && nf.lon != null);
    if (geo) geo.hidden = has;
    if (clr) clr.hidden = !has;
    if (st) {
      st.classList.remove("is-ok", "is-warn");
      if (has) {
        st.textContent = "Position relevee" + (nf.acc != null ? " (precision " + Math.round(nf.acc) + " m)" : "");
        st.classList.add(nf.acc != null && nf.acc > 100 ? "is-warn" : "is-ok");
      } else {
        st.textContent = message || "";
      }
    }
  }

  function locate() {
    var nf = state.nf, geo = $("ds-nf-geo");
    if (!nf) return;
    var seq = ++nf.geoSeq;
    var done = function () { if (geo) geo.disabled = false; };
    var fail = function (msg) {
      if (state.nf !== nf || seq !== nf.geoSeq) return;
      done();
      updatePosition(msg);
      toast("warning", msg);
    };
    try {
      if (!navigator.geolocation || typeof navigator.geolocation.getCurrentPosition !== "function") {
        fail("Localisation non disponible sur cet appareil.");
        return;
      }
      if (geo) geo.disabled = true;
      updatePosition("Localisation en cours...");
      navigator.geolocation.getCurrentPosition(function (pos) {
        if (state.nf !== nf || seq !== nf.geoSeq) return;
        done();
        var c = (pos && pos.coords) || {};
        var lat = +c.latitude, lon = +c.longitude;
        if (!isFinite(lat) || !isFinite(lon)) { fail("Position invalide."); return; }
        nf.lat = lat;
        nf.lon = lon;
        nf.acc = isFinite(+c.accuracy) ? +c.accuracy : null;
        updatePosition("");
      }, function (err) {
        var code = err && err.code;
        fail(code === 1 ? "Localisation refusee : autoriser l'acces a la position dans le navigateur."
          : code === 3 ? "Localisation trop longue : reessayer a l'exterieur."
            : "Position indisponible.");
      }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
    } catch (e) {
      fail("Localisation non disponible sur cet appareil.");
    }
  }

  function clearPosition() {
    var nf = state.nf;
    if (!nf) return;
    nf.geoSeq++;   // ignore une reponse encore en route
    nf.lat = nf.lon = nf.acc = null;
    var geo = $("ds-nf-geo");
    if (geo) geo.disabled = false;
    updatePosition("Position retiree");
  }

  /** Compteur, indication de proposition automatique, bouton de creation. */
  function nfUpdate() {
    var nf = state.nf;
    if (!nf) return;
    var ta = $("ds-nf-text");
    var len = ta ? ta.value.length : 0;
    var count = $("ds-nf-count");
    if (count) {
      count.textContent = len + " / " + NF_TEXT_MAX;
      count.classList.toggle("is-warn", len > NF_TEXT_MAX - 100);
    }
    var cat = nfCategory();
    var hint = $("ds-nf-auto-hint");
    if (hint) hint.hidden = !(cat && willAutoPropose(cat, nf.urgency));
    var btn = $("ds-nf-submit");
    if (btn) btn.disabled = nf.sending || !cat || !(ta && ta.value.trim());
  }

  function submitNewFiche() {
    var nf = state.nf;
    if (!nf || nf.sending) return;
    var ta = $("ds-nf-text");
    var text = ta ? ta.value.trim() : "";
    var cat = nfCategory();
    if (!cat) { toast("warning", "Choisir une categorie."); return; }
    if (!text) { toast("warning", "Saisir la description."); if (ta) ta.focus(); return; }
    if (text.length > NF_TEXT_MAX) { toast("warning", "Description trop longue (" + NF_TEXT_MAX + " caracteres max)."); return; }
    var s = effectiveScope();
    if (!s.event || !s.year) { toast("warning", "Evenement courant indisponible, choisir un evenement."); return; }

    var body = { event: s.event, year: s.year, category: cat, text: text, client_token: nf.token };
    if (nf.urgency) body.niveau_urgence = nf.urgency;
    var area = (($("ds-nf-area") || {}).value || "").trim();
    if (area) body.area_desc = area;
    if (nf.lat != null && nf.lon != null) { body.lat = nf.lat; body.lon = nf.lon; }
    var cc = {};
    var metier = (($("ds-nf-metier") || {}).value || "").trim();
    if (metier) cc.sous_classification = metier;
    var caller = (($("ds-nf-caller") || {}).value || "").trim();
    if (caller) cc.appelant = caller;
    if (Object.keys(cc).length) body.content_category = cc;

    nf.sending = true;
    nfUpdate();
    api("POST", "/api/pcorg/create", body).then(function (res) {
      if (state.nf !== nf) return;
      nf.sending = false;
      if (!res.ok || !res.data.id) {
        nfUpdate();
        toast("error", serverMsg(res.data));
        return;
      }
      var id = res.data.id;
      // La fiche creee ici ne doit pas sonner comme une "nouvelle fiche a traiter"
      state.selfCreated[id] = true;
      toast("success", res.data.duplicate ? "Fiche deja creee." : "Fiche creee.");
      closeNewFiche(true);
      refresh();
      openFiche(id);
    });
  }

  function initNewFiche() {
    var nb = $("ds-new-btn");
    if (nb) nb.addEventListener("click", openNewFiche);
    var ov = $("ds-new");
    if (!ov) return;
    ov.addEventListener("click", function (e) {
      if (e.target === ov || e.target.closest("[data-close]")) closeNewFiche(false);
    });
    var form = $("ds-new-form");
    if (form) form.addEventListener("submit", function (e) { e.preventDefault(); submitNewFiche(); });
    var sel = $("ds-nf-cat");
    if (sel) sel.addEventListener("change", function () { loadMetiers(); renderUrgencyButtons(); nfUpdate(); });
    var ta = $("ds-nf-text");
    if (ta) {
      ta.addEventListener("input", nfUpdate);
      ta.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submitNewFiche(); }
      });
    }
    var geo = $("ds-nf-geo");
    if (geo) geo.addEventListener("click", locate);
    var clr = $("ds-nf-geo-clear");
    if (clr) clr.addEventListener("click", clearPosition);
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
    try { state.sort = JSON.parse(lsGet(LS_SORT) || "{}") || {}; } catch (e) { state.sort = {}; }

    // Outils de section : ordre (plus recentes / plus anciennes) et tout deplier
    document.addEventListener("click", function (e) {
      var sb = e.target.closest && e.target.closest("[data-sort]");
      if (sb) {
        var key = sb.getAttribute("data-sort");
        state.sort[key] = sortDir(key) === "asc" ? "desc" : "asc";
        lsSet(LS_SORT, JSON.stringify(state.sort));
        render();
        return;
      }
      var fb = e.target.closest && e.target.closest("[data-fold]");
      if (fb && state.groups) {
        var list = state.groups[fb.getAttribute("data-fold")] || [];
        var openAll = fb.getAttribute("data-open") !== "1";
        list.forEach(function (f) { state.expanded[f.id] = openAll; });
        render();
      }
    });

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
    var fov = $("ds-fiche");
    if (fov) {
      fov.addEventListener("click", function (e) {
        if (e.target === fov || e.target.closest("[data-close]")) closeFicheModal(false);
      });
    }
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      // Echap dans un toast de confirmation ou de saisie : c'est lui qui le traite
      if (e.target && e.target.closest && e.target.closest("#toast-container")) return;
      if (state.pickerFiche) { closePicker(); return; }
      if (state.nf) { closeNewFiche(false); return; }
      if (state.fiche) closeFicheModal(false);
    });
    initNewFiche();

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
