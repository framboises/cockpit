(function () {
  "use strict";

  // ── Constants ──────────────────────────────────────────────────────────────
  var REFRESH_MS = 60000;
  var CATEGORY_STYLES = {
    "PCO.Secours":       { color: "#dc2626", icon: "local_hospital" },
    "PCO.Securite":      { color: "#ef4444", icon: "shield" },
    "PCO.Technique":     { color: "#f59e0b", icon: "build" },
    "PCO.Flux":          { color: "#0d9488", icon: "swap_calls" },
    "PCO.Fourriere":     { color: "#6b7280", icon: "directions_car" },
    "PCO.Information":   { color: "#2563eb", icon: "info" },
    "PCO.MainCourante":  { color: "#8b5cf6", icon: "edit_note" },
    // Fiches PC Securite (Prysm), affichees en SAISON : meme icone et meme
    // couleur que la categorie PCO equivalente (on ne les distingue que par le
    // libelle "PCS ...", cf. shortCat)
    "PCS.Surete":        { color: "#ef4444", icon: "shield" },
    "PCS.Information":   { color: "#2563eb", icon: "info" }
  };
  var FALLBACK_STYLE = { color: "#94a3b8", icon: "description" };
  // Categorie PCO equivalente d'une categorie PCS (droits des groupes)
  var PCS_EQUIVALENT = { "PCS.Surete": "PCO.Securite", "PCS.Information": "PCO.Information" };
  function isSaisonSelected() {
    return String(window.selectedEvent || "").toUpperCase() === "SAISON";
  }

  // Mode creation seule (File du service) : cf. window.PcorgCreate en fin de fichier
  var createOnlyMode = window.PCORG_CREATE_ONLY === true;
  var createHooks = {};   // {categories, onCreated} fournis par PcorgCreate.open

  // ── Urgency levels ────────────────────────────────────────────────────────
  var URGENCY_LEVELS = ["EU", "UA", "UR", "IMP"];
  var URGENCY_COLORS = {
    EU: "#dc2626", UA: "#f97316", UR: "#eab308", IMP: "#6b7280"
  };
  var URGENCY_LABELS = {
    SECOURS:  { EU: "D\u00e9tresse vitale", UA: "Urgence absolue", UR: "Urgence relative", IMP: "Impliqu\u00e9 m\u00e9dical" },
    SECURITE: { EU: "Danger imm\u00e9diat", UA: "Incident grave", UR: "Incident en cours", IMP: "T\u00e9moin / impliqu\u00e9" },
    MIXTE:    { EU: "Urgence extr\u00eame", UA: "Urgence prioritaire", UR: "Situation stable", IMP: "Impliqu\u00e9" }
  };

  function urgencyType(cat) {
    if (cat === "PCO.Secours") return "SECOURS";
    if (cat === "PCO.Securite" || cat === "PCS.Surete") return "SECURITE";
    return "MIXTE";
  }

  function urgencyLabel(cat, level) {
    return (URGENCY_LABELS[urgencyType(cat)] || URGENCY_LABELS.MIXTE)[level] || level;
  }

  function urgencyEnabledFor(cat, current) {
    var u = (pcorgConfig && pcorgConfig.urgence_categories) || {};
    return !!u[cat] || !!current;
  }

  // ── State ──────────────────────────────────────────────────────────────────
  var refreshTimer = null;
  var lastData = null;
  var pcorgMapLayer = null;
  var pcorgMarkers = {}; // {id: L.marker} pour ouvrir les popups programmatiquement
  var pickCallback = null;
  var activeFicheId = null; // fiche mise en evidence (popup ouverte / fiche affichee)

  // Lien carte <-> liste : met en evidence la ligne de la fiche dans le petit
  // bloc et le panneau elargi, et le pin sur la carte.
  function setActiveFiche(id) {
    activeFicheId = id || null;
    document.querySelectorAll(".pcorg-row.pcorg-row-active, .pcorg-exp-table tr.pcorg-row-active")
      .forEach(function (el) { el.classList.remove("pcorg-row-active"); });
    Object.keys(pcorgMarkers).forEach(function (mid) {
      var el = pcorgMarkers[mid].getElement && pcorgMarkers[mid].getElement();
      if (el) el.classList.toggle("pcorg-pin-active", mid === activeFicheId);
    });
    if (!activeFicheId) return;
    document.querySelectorAll('.pcorg-row[data-id="' + activeFicheId + '"], .pcorg-exp-table tr[data-id="' + activeFicheId + '"]')
      .forEach(function (el) {
        el.classList.add("pcorg-row-active");
        if (el.offsetParent) el.scrollIntoView({ block: "nearest" });
      });
  }
  // Bounce acknowledgement (localStorage per user)
  var ACK_STORAGE_KEY = "pcorg-pin-ack";
  function _loadAck() {
    try { return JSON.parse(localStorage.getItem(ACK_STORAGE_KEY)) || {}; } catch (e) { return {}; }
  }
  function _saveAck(ack) {
    try { localStorage.setItem(ACK_STORAGE_KEY, JSON.stringify(ack)); } catch (e) {}
  }
  function ackPin(id, rev) {
    var ack = _loadAck();
    ack[id] = rev;
    _saveAck(ack);
  }
  function ackAllPins(items) {
    var ack = _loadAck();
    items.forEach(function (item) { ack[item.id] = item.bounce_rev || 0; });
    _saveAck(ack);
  }
  function shouldBounce(item) {
    var rev = item.bounce_rev || 0;
    if (rev === 0) return false;
    var ack = _loadAck();
    var seen = ack[item.id];
    return seen === undefined || seen < rev;
  }

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var listOpen, listClosed, statsContainer, badge, placeholderOpen, placeholderClosed;

  // ── Device status resolution (anoloc cross-reference) ────────────────────
  var DEVICE_STATUS_META = {
    patrouille:        { label: "Disponible",         color: "#22c55e" },
    intervention:      { label: "Intervention",       color: "#f59e0b" },
    sur_place:         { label: "ASL",                color: "#3b82f6" },
    pause:             { label: "Pause",              color: "#94a3b8" },
    fin_intervention:  { label: "Fin d'inter",        color: "#8b5cf6" },
    running:           { label: "En mouvement",       color: "#22c55e" },
    stopped:           { label: "A l'arret",          color: "#f59e0b" },
    waiting:           { label: "En attente",         color: "#eab308" },
    offline:           { label: "Hors ligne",         color: "#ef4444" },
  };
  function _resolveDeviceStatus(dev) {
    if (!dev) return null;
    // Tablets: use patrol_status first
    if (dev.kind === "tablet" && dev.patrol_status) {
      return DEVICE_STATUS_META[dev.patrol_status] || DEVICE_STATUS_META.patrouille;
    }
    // Beacons / other
    if (!dev.online) return DEVICE_STATUS_META.offline;
    return DEVICE_STATUS_META[dev.status] || DEVICE_STATUS_META.running;
  }

  // Resout la dispo d'un vehicule (utilise par les pickers : sous-sous-menu
  // hover + overlay touch). Retourne {dev, dsMeta, isAvailable}.
  // Les balises GPS (kind != "tablet") sont toujours considerees disponibles.
  function resolveVehicleAvailability(v) {
    var anoRef = typeof window.getAnolocDeviceByLabel === "function"
      ? window.getAnolocDeviceByLabel(v.label) : null;
    var dev = anoRef ? anoRef.device : null;
    var dsMeta = dev ? _resolveDeviceStatus(dev) : null;
    var isAvailable = true;
    if (dev && dev.kind === "tablet" && dsMeta) {
      isAvailable = dsMeta === DEVICE_STATUS_META.patrouille
        || dsMeta === DEVICE_STATUS_META.running;
    }
    return { dev: dev, dsMeta: dsMeta, isAvailable: isAvailable };
  }

  // ── Helpers ────────────────────────────────────────────────────────────────
  function catStyle(cat) {
    return CATEGORY_STYLES[cat] || FALLBACK_STYLE;
  }

  function timeAgo(isoStr) {
    if (!isoStr) return "";
    var diff = Date.now() - new Date(isoStr).getTime();
    var mins = Math.floor(diff / 60000);
    if (mins < 1) return "a l'instant";
    if (mins < 60) return mins + " min";
    var hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + "h" + (mins % 60 ? String(mins % 60).padStart(2, "0") : "");
    var days = Math.floor(hrs / 24);
    return days + "j";
  }

  function shortTime(isoStr) {
    if (!isoStr) return "";
    var d = new Date(isoStr);
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  function shortCat(cat) {
    cat = cat || "";
    if (cat.indexOf("PCS.") === 0) return "PCS " + cat.slice(4);
    return cat.replace("PCO.", "");
  }

  function truncZone(desc) {
    if (!desc) return "";
    return desc.replace(/_MC PCO\/?/g, "").replace(/^\//, "");
  }

  var _STADE_TO_URGENCY = { "1": "IMP", "2": "UR", "3": "UA", "4": "EU" };

  function formatGroupDesc(groupDesc, category) {
    if (!groupDesc) return "";
    // Remplacer chaque "PCO/Stade N" ou "PCS/Stade N" par le label d'urgence
    return groupDesc.replace(/(?:PCO|PCS)\/Stade\s*(\d)/g, function (match, num) {
      var code = _STADE_TO_URGENCY[num];
      if (code) return urgencyLabel(category, code);
      return match;
    });
  }

  function mkEl(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  function matIcon(name, extraCls) {
    var s = document.createElement("span");
    s.className = "material-symbols-outlined" + (extraCls ? " " + extraCls : "");
    s.textContent = name;
    return s;
  }

  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c];
    });
  }

  function csrfToken() {
    return (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  }

  // Appel API JSON (POST/PUT/DELETE) : ne rejette jamais. Une erreur reseau
  // ou une reponse non JSON (redirection d'auth) donne {ok:false, error}.
  // Avant, plusieurs appels n'avaient pas de .catch : bouton bloque desactive,
  // aucun message.
  //
  // Jeton CSRF refuse (onglet ouvert depuis plus d'une heure) : le jeton est
  // renouvele (csrf_refresh.js) et la requete rejouee UNE fois, sans que
  // l'utilisateur perde sa saisie.
  function apiCall(method, url, payload, _retried) {
    var opts = { method: method, headers: { "X-CSRFToken": csrfToken() } };
    if (payload !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(payload);
    }
    return fetch(url, opts)
      .then(function (r) {
        return r.json().catch(function () {
          // Page HTML au lieu de JSON : redirection vers le portail
          return { ok: false, error: "Session expiree : rechargez la page", session_expired: true };
        });
      })
      .catch(function () { return { ok: false, error: "Erreur reseau" }; })
      .then(function (res) {
        if (!_retried && res && res.code === "csrf" && window.CockpitCsrf) {
          return window.CockpitCsrf.refresh().then(function (st) {
            if (st.ok) return apiCall(method, url, payload, true);
            if (!st.expired) return res;
            return { ok: false, error: "Session expiree : rechargez la page", session_expired: true };
          });
        }
        return res;
      });
  }

  // ── Photos prises sur le moment (07/10/2026) ──────────────────────────────
  // Telephone : l'input ouvre l'appareil photo (capture), poste : choix de
  // fichier. Reduite a 1920 px avant envoi (une photo de telephone pese
  // 3-6 Mo), puis POST multipart /api/pcorg/photo/<id>.
  var PHOTO_MAX_DIM = 1920, PHOTO_MAX_BATCH = 5;

  function shrinkPhoto(file) {
    return new Promise(function (resolve) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        var k = Math.min(1, PHOTO_MAX_DIM / Math.max(img.naturalWidth, img.naturalHeight));
        var c = document.createElement("canvas");
        c.width = Math.round(img.naturalWidth * k);
        c.height = Math.round(img.naturalHeight * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        c.toBlob(function (b) { resolve(b || file); }, "image/jpeg", 0.85);
      };
      img.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  // Ouvre le selecteur (appareil photo sur telephone) ; cb(blobs reduits)
  function pickPhotos(cb, max) {
    var inp = document.createElement("input");
    inp.type = "file";
    inp.accept = "image/*";
    inp.multiple = true;
    if (window.matchMedia && window.matchMedia("(pointer: coarse)").matches) inp.setAttribute("capture", "environment");
    inp.style.display = "none";
    inp.addEventListener("change", function () {
      var files = Array.prototype.slice.call(inp.files || []).slice(0, max || PHOTO_MAX_BATCH);
      inp.remove();
      if (!files.length) return;
      Promise.all(files.map(shrinkPhoto)).then(cb);
    });
    document.body.appendChild(inp);
    inp.click();
  }

  function uploadFichePhotos(ficheId, blobs, text, _retried) {
    var fd = new FormData();
    blobs.forEach(function (b, i) { fd.append("photos", b, "photo_" + (i + 1) + ".jpg"); });
    if (text) fd.append("text", text);
    return fetch("/api/pcorg/photo/" + encodeURIComponent(ficheId), {
      method: "POST", headers: { "X-CSRFToken": csrfToken() }, body: fd,
    })
      .then(function (r) { return r.json().catch(function () { return { ok: false, error: "Session expiree : rechargez la page" }; }); })
      .catch(function () { return { ok: false, error: "Erreur reseau" }; })
      .then(function (res) {
        if (!_retried && res && res.code === "csrf" && window.CockpitCsrf) {
          return window.CockpitCsrf.refresh().then(function () { return uploadFichePhotos(ficheId, blobs, text, true); });
        }
        return res;
      });
  }

  // Avant d'ouvrir un formulaire : jeton frais, ou alerte immediate si la
  // session a expire (plutot qu'apres avoir tout rempli)
  function checkSessionBeforeForm() {
    if (!window.CockpitCsrf) return;
    window.CockpitCsrf.refresh().then(function (st) {
      if (st.ok || !st.expired) return;
      showConfirmToast("Votre session a expire : la fiche ne pourra pas etre enregistree. Recharger la page maintenant ?",
        { okLabel: "Recharger", cancelLabel: "Plus tard", type: "warning" })
        .then(function (ok) { if (ok) window.location.reload(); });
    });
  }

  function randomToken() {
    try {
      var a = new Uint8Array(12);
      window.crypto.getRandomValues(a);
      return Array.prototype.map.call(a, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
    } catch (e) {
      return String(Date.now()) + Math.random().toString(16).slice(2);
    }
  }

  // "27/09 15:22" ; l'heure seule si le jour est aujourd'hui (withDate force)
  function fmtDayTime(isoStr, withDate) {
    if (!isoStr) return "";
    var d = new Date(isoStr);
    if (isNaN(d.getTime())) return "";
    var hm = _pad2(d.getHours()) + ":" + _pad2(d.getMinutes());
    if (!withDate && _isSameDay(d, new Date())) return hm;
    return _pad2(d.getDate()) + "/" + _pad2(d.getMonth() + 1) + " " + hm;
  }

  function operatorLabel(op) {
    op = op || "";
    if (op.indexOf("field:") === 0) return "Tablette " + op.slice(6);
    return op;
  }

  // ── Detection de changement (clotures tablette, commentaires, synchro) ─────
  // /api/pcorg/sig (empreinte legere, cache 2 s serveur) lue toutes les 5 s ;
  // la liste complete n'est rechargee que si elle change. Avant : jusqu'a 60 s
  // pour voir une fiche close depuis le terminal disparaitre.
  var SIG_POLL_MS = 5000;
  var lastSig = null;
  function checkChanges() {
    if (document.hidden) return;
    var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};
    if (!ey.event || !ey.year) return;
    var key = ey.event + "|" + ey.year;
    fetch("/api/pcorg/sig?event=" + encodeURIComponent(ey.event) + "&year=" + encodeURIComponent(ey.year),
          { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.sig) return;
        var sig = key + "#" + d.sig;
        if (lastSig !== null && sig !== lastSig) refresh();
        lastSig = sig;
      })
      .catch(function () {});
  }
  function startChangeWatch() {
    setInterval(checkChanges, SIG_POLL_MS);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) { refresh(); checkChanges(); }
    });
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    if (window.isBlockAllowed && !window.isBlockAllowed("widget-comms")) return;

    listOpen = document.getElementById("pcorg-list-open");
    listClosed = document.getElementById("pcorg-list-closed");
    statsContainer = document.getElementById("pcorg-stats");
    badge = document.getElementById("pcorg-badge");
    placeholderOpen = document.getElementById("pcorg-placeholder-open");
    placeholderClosed = document.getElementById("pcorg-placeholder-closed");

    initTabs();
    initCreateModal();
    initGpsModal();

    window.pcorgRefresh = refresh;
    window.pcorgUpdateTooltips = updateVehicleTooltips;
    loadPcorgConfig();
    loadCockpitUserNames();
    loadVehiclesByCategory();
    ensureSharedGrid();

    setTimeout(refresh, 800);
    refreshTimer = setInterval(refresh, REFRESH_MS);
    startChangeWatch();
    initWidgetFilter();

    // Echap ferme la fiche, l'assistant de creation et la modale GPS
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (_tsPopover || vehiclePicker || (_camPickerOverlay && _camPickerOverlay.classList.contains("show"))) return;
      if (document.querySelector(".pcorg-autocomplete")) return;
      if (document.querySelector("#toast-container .toast-confirm, #toast-container .toast-input")) return;
      var gps = document.getElementById("pcorgGpsModal");
      if (gps && gps.classList.contains("show")) { gps.classList.remove("show"); return; }
      if (createModal && createModal.classList.contains("show")) { hideCreate(); return; }
      if (detailModal && detailModal.classList.contains("show")) { hideFiche(); }
    });

    // Pins en attente de la carte (creee au premier passage en vue carte).
    // Avant, ce timer s'arretait si le premier /live n'avait pas encore
    // repondu : les pins n'apparaissaient qu'au refresh suivant (60 s).
    var pinRetry = setInterval(function () {
      if (!getMap()) return;
      clearInterval(pinRetry);
      if (pendingPins) updateMapPins(pendingPins);
    }, 1000);

    // Context menu on map
    buildContextMenu();
    var ctxRetry = setInterval(function () {
      var m = getMap();
      if (!m) return;
      clearInterval(ctxRetry);
      m.on("contextmenu", onMapContextMenu);

      // Long-press tactile (600ms) pour ecrans tactiles (Huawei IdeaHub, etc.)
      var lpTimer = null, lpStart = null;
      var mapContainer = m.getContainer();
      mapContainer.addEventListener("touchstart", function (e) {
        if (e.touches.length !== 1) { clearTimeout(lpTimer); return; }
        lpStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
        lpTimer = setTimeout(function () {
          // Convertir le point touch en latlng Leaflet
          var touch = lpStart;
          var rect = mapContainer.getBoundingClientRect();
          var pt = L.point(touch.x - rect.left, touch.y - rect.top);
          var latlng = m.containerPointToLatLng(pt);
          onMapContextMenu({
            latlng: latlng,
            originalEvent: { preventDefault: function () {} },
            _touch: true
          });
          L.DomEvent.preventDefault(e);
        }, 600);
      }, { passive: false });
      mapContainer.addEventListener("touchmove", function (e) {
        if (!lpStart || !lpTimer) return;
        var dx = e.touches[0].clientX - lpStart.x;
        var dy = e.touches[0].clientY - lpStart.y;
        if (dx * dx + dy * dy > 100) { clearTimeout(lpTimer); lpTimer = null; }
      });
      mapContainer.addEventListener("touchend", function () {
        clearTimeout(lpTimer); lpTimer = null;
      });
      mapContainer.addEventListener("touchcancel", function () {
        clearTimeout(lpTimer); lpTimer = null;
      });
    }, 2000);
  }

  // ── Context menu ──────────────────────────────────────────────────────────
  var ctxMenu = null;
  var ctxLat = null, ctxLon = null;
  var _ctxIsTouch = false;

  var CTX_DESCRIPTIONS = {
    "PCO.Secours":       "Victime, malaise, blessure",
    "PCO.Securite":      "Incident, intrusion, vol",
    "PCO.Technique":     "Panne, infrastructure, materiel",
    "PCO.Flux":          "Circulation, acces, jauge",
    "PCO.Fourriere":     "Vehicule, stationnement",
    "PCO.Information":   "Signalement, observation",
    "PCO.MainCourante":  "Note, consigne, suivi"
  };

  function buildContextMenu() {
    ctxMenu = mkEl("div", "pcorg-ctx-menu");
    ctxMenu.id = "pcorg-ctx-menu";

    // Header with coordinates
    var header = mkEl("div", "pcorg-ctx-header");
    var headerLeft = mkEl("div", "pcorg-ctx-header-left");
    var headerIco = matIcon("add_location_alt", "pcorg-ctx-header-icon");
    headerLeft.appendChild(headerIco);
    var headerTxt = mkEl("div", "pcorg-ctx-header-text");
    var headerTitle = mkEl("div", "pcorg-ctx-title");
    headerTitle.textContent = "Nouvelle intervention";
    headerTxt.appendChild(headerTitle);
    var headerCoords = mkEl("div", "pcorg-ctx-coords");
    headerCoords.id = "pcorg-ctx-coords";
    headerTxt.appendChild(headerCoords);
    headerLeft.appendChild(headerTxt);
    header.appendChild(headerLeft);
    ctxMenu.appendChild(header);

    // Category items
    var list = mkEl("div", "pcorg-ctx-list");
    CATEGORY_ORDER.forEach(function (cat, idx) {
      var st = catStyle(cat);
      var item = mkEl("div", "pcorg-ctx-item");
      item.setAttribute("data-cat", cat);
      item.style.setProperty("--cat-color", st.color);
      item.style.animationDelay = (idx * 30) + "ms";

      var iconWrap = mkEl("div", "pcorg-ctx-icon-wrap");
      iconWrap.style.background = st.color + "18";
      iconWrap.style.color = st.color;
      var ico = matIcon(st.icon);
      iconWrap.appendChild(ico);
      item.appendChild(iconWrap);

      var content = mkEl("div", "pcorg-ctx-content");
      var label = mkEl("div", "pcorg-ctx-label");
      label.textContent = shortCat(cat);
      content.appendChild(label);
      var desc = mkEl("div", "pcorg-ctx-desc");
      desc.textContent = CTX_DESCRIPTIONS[cat] || "";
      content.appendChild(desc);
      item.appendChild(content);

      var arrow = matIcon("chevron_right", "pcorg-ctx-arrow");
      item.appendChild(arrow);

      // Build urgency sub-menu (populated dynamically based on config)
      var submenu = mkEl("div", "pcorg-ctx-submenu");
      submenu.setAttribute("data-cat-sub", cat);
      var uType = urgencyType(cat);
      // Menu de la carte : du plus faible (IMP) en haut au plus fort (EU) en
      // bas. Copie inversee : URGENCY_LEVELS garde son ordre pour les autres usages.
      URGENCY_LEVELS.slice().reverse().forEach(function (level) {
        var subItem = mkEl("div", "pcorg-ctx-sub-item");
        subItem.style.setProperty("--sub-color", URGENCY_COLORS[level]);
        var dot = mkEl("span", "pcorg-ctx-sub-dot");
        dot.style.background = URGENCY_COLORS[level];
        subItem.appendChild(dot);
        var subContent = mkEl("div", "pcorg-ctx-sub-content");
        var subLabel = mkEl("div", "pcorg-ctx-sub-label");
        subLabel.textContent = URGENCY_LABELS[uType][level];
        subContent.appendChild(subLabel);
        var subCode = mkEl("div", "pcorg-ctx-sub-code");
        subCode.textContent = level;
        subContent.appendChild(subCode);
        subItem.appendChild(subContent);
        function onSubItemAction(e) {
          e.stopPropagation();
          e.preventDefault();
          // Save menu position before hiding (for vehicle picker placement)
          var menuPos = ctxMenu ? ctxMenu.getBoundingClientRect() : null;
          hideContextMenu();
          var userCanFS = !!window.__userFicheSimplifiee;
          var catFS = (pcorgConfig && pcorgConfig.fiche_simplifiee) || {};
          var vehicles = vehiclesByCategory[cat];
          var isQuick = userCanFS && catFS[cat];
          if (vehicles && vehicles.length > 0) {
            showVehiclePicker(ctxLat, ctxLon, cat, level, vehicles, isQuick, menuPos);
          } else if (isQuick) {
            quickCreate(ctxLat, ctxLon, cat, level);
          } else {
            openCreateFromContext(ctxLat, ctxLon, cat, level);
          }
        }
        // Vehicle sub-sub-menu (built once, shown/hidden dynamically via CSS hover)
        var vehSubmenu = mkEl("div", "pcorg-ctx-veh-submenu");
        vehSubmenu.setAttribute("data-cat-veh", cat);
        vehSubmenu.setAttribute("data-level-veh", level);
        subItem.appendChild(vehSubmenu);

        // Flip vehicle submenu up if it would overflow the viewport bottom.
        // Important : on neutralise top via "auto" (pas "") sinon la regle CSS
        // top:-6px reprend et le sous-menu se retrouve avec top ET bottom -> il
        // est etire sur la seule hauteur du subItem (~30px) au lieu de s'etendre
        // librement vers le haut.
        subItem.addEventListener("mouseenter", function () {
          if (vehSubmenu.style.display === "none") return;
          vehSubmenu.style.top = "-6px";
          vehSubmenu.style.bottom = "auto";
          requestAnimationFrame(function () {
            var r = vehSubmenu.getBoundingClientRect();
            if (r.bottom > window.innerHeight - 8) {
              vehSubmenu.style.top = "auto";
              vehSubmenu.style.bottom = "-6px";
            }
            placeSubmenuX(vehSubmenu, subItem);
          });
        });

        subItem.addEventListener("touchend", function (e) {
          // Touch: show vehicle picker as separate overlay
          onSubItemAction(e);
        });
        subItem.addEventListener("click", function (e) {
          if (_ctxIsTouch) return;
          // If vehicle submenu is visible, don't fire (user clicks vehicle inside)
          if (e.target.closest(".pcorg-ctx-veh-submenu")) return;
          onSubItemAction(e);
        });
        submenu.appendChild(subItem);
      });
      item.appendChild(submenu);

      // Flip urgency submenu up if it would overflow the viewport bottom.
      // Important : top "auto" et non "" pour vraiment neutraliser la regle CSS
      // (sinon top:-6px reprend, combine avec bottom -> sous-menu ecrase).
      item.addEventListener("mouseenter", function () {
        if (!item.classList.contains("has-submenu")) return;
        submenu.style.top = "-6px";
        submenu.style.bottom = "auto";
        requestAnimationFrame(function () {
          var r = submenu.getBoundingClientRect();
          if (r.bottom > window.innerHeight - 8) {
            submenu.style.top = "auto";
            submenu.style.bottom = "-6px";
          }
          placeSubmenuX(submenu, item);
        });
      });

      // Touch: tap toggles sub-menu, second tap opens wizard
      item.addEventListener("touchend", function (e) {
        if (e.target.closest(".pcorg-ctx-submenu")) return;
        e.preventDefault(); // empeche le click synthetise
        if (item.classList.contains("has-submenu")) {
          var wasOpen = submenu.classList.contains("touch-open");
          ctxMenu.querySelectorAll(".pcorg-ctx-submenu.touch-open").forEach(function (s) {
            s.classList.remove("touch-open");
          });
          if (!wasOpen) {
            submenu.classList.add("touch-open");
            return;
          }
        }
        hideContextMenu();
        openCreateFromContext(ctxLat, ctxLon, cat);
      });
      // Mouse: click opens wizard directly (hover handles sub-menu via CSS)
      item.addEventListener("click", function (e) {
        if (e.target.closest(".pcorg-ctx-submenu")) return;
        if (_ctxIsTouch) return; // deja gere par touchend
        hideContextMenu();
        openCreateFromContext(ctxLat, ctxLon, cat);
      });
      list.appendChild(item);
    });
    ctxMenu.appendChild(list);

    document.body.appendChild(ctxMenu);

    // Close on click/touch anywhere or Escape
    document.addEventListener("click", function () { hideContextMenu(); });
    document.addEventListener("touchstart", function (e) {
      if (ctxMenu.classList.contains("show") && !e.target.closest("#pcorg-ctx-menu")) {
        hideContextMenu();
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") hideContextMenu();
    });
    document.addEventListener("contextmenu", function (e) {
      if (ctxMenu.classList.contains("show") && !e.target.closest(".leaflet-container")) {
        hideContextMenu();
      }
    });
  }

  function onMapContextMenu(e) {
    if (e.originalEvent && e.originalEvent.preventDefault) e.originalEvent.preventDefault();
    // Droit de groupe "Creer des fiches" : sans lui, pas de menu de creation
    if (window.__userCanCreateFiche === false && !window.__userIsAdmin) return;
    _ctxIsTouch = !!(e._touch);
    ctxLat = e.latlng.lat;
    ctxLon = e.latlng.lng;

    // Resolve zone from POI polygons
    var ctxZone = "";
    if (window.CockpitMapView && window.CockpitMapView.findZoneAtPoint) {
      ctxZone = window.CockpitMapView.findZoneAtPoint(ctxLat, ctxLon) || "";
    }

    // Update coordinates display
    var coordsEl = document.getElementById("pcorg-ctx-coords");
    if (coordsEl) {
      coordsEl.textContent = (ctxZone ? ctxZone + " - " : "") + ctxLat.toFixed(5) + ", " + ctxLon.toFixed(5);
    }

    // Show urgency sub-menus only for categories enabled in config
    var urgCats = (pcorgConfig && pcorgConfig.urgence_categories) || {};
    ctxMenu.querySelectorAll(".pcorg-ctx-submenu").forEach(function (sub) {
      var cat = sub.getAttribute("data-cat-sub");
      if (urgCats[cat]) {
        sub.style.display = "";
        sub.parentElement.classList.add("has-submenu");
      } else {
        sub.style.display = "none";
        sub.parentElement.classList.remove("has-submenu");
      }
    });

    // Populate vehicle sub-sub-menus
    ctxMenu.querySelectorAll(".pcorg-ctx-veh-submenu").forEach(function (vSub) {
      vSub.textContent = "";
      var vCat = vSub.getAttribute("data-cat-veh");
      var vLevel = vSub.getAttribute("data-level-veh");
      var allVehicles = vehiclesByCategory[vCat];
      if (!allVehicles || !allVehicles.length) {
        vSub.style.display = "none";
        vSub.parentElement.classList.remove("has-veh-submenu");
        return;
      }
      // Filtre : on n'affiche que les vehicules disponibles. Si tout est
      // indisponible, on masque le sous-sous-menu (le subItem retombe sur le
      // comportement classique = ouvrir la fiche sans vehicule).
      var available = allVehicles.filter(function (v) {
        return resolveVehicleAvailability(v).isAvailable;
      });
      if (!available.length) {
        vSub.style.display = "none";
        vSub.parentElement.classList.remove("has-veh-submenu");
        return;
      }
      vSub.style.display = "";
      vSub.parentElement.classList.add("has-veh-submenu");
      var catSt = catStyle(vCat);
      vSub.style.setProperty("--cat-color", catSt.color);
      var userCanFS = !!window.__userFicheSimplifiee;
      var catFS = (pcorgConfig && pcorgConfig.fiche_simplifiee) || {};
      var isQuick = userCanFS && !!catFS[vCat];
      // Tri alphabetique (fr, insensible a la casse / accents).
      var vehicles = available.slice().sort(function (a, b) {
        return (a.label || "").localeCompare((b.label || ""), "fr", { sensitivity: "base" });
      });
      vehicles.forEach(function (v) {
        var vBtn = mkEl("div", "pcorg-ctx-veh-item");
        var avail = resolveVehicleAvailability(v);
        var dsMeta = avail.dsMeta;
        // Build label with status indicator
        var nameSpan = mkEl("span", "pcorg-ctx-veh-name");
        nameSpan.textContent = v.label;
        vBtn.appendChild(nameSpan);
        if (dsMeta) {
          var stSpan = mkEl("span", "pcorg-ctx-veh-status");
          var dot = mkEl("span", "pcorg-ctx-veh-dot");
          dot.style.background = dsMeta.color;
          stSpan.appendChild(dot);
          stSpan.appendChild(document.createTextNode(dsMeta.label));
          vBtn.appendChild(stSpan);
        }
        function onVehPick(ev) {
          ev.stopPropagation(); ev.preventDefault();
          hideContextMenu();
          if (isQuick) {
            quickCreate(ctxLat, ctxLon, vCat, vLevel, v.label);
          } else {
            openCreateFromContext(ctxLat, ctxLon, vCat, vLevel, v.label);
          }
        }
        vBtn.addEventListener("click", function (ev) { if (!_ctxIsTouch) onVehPick(ev); });
        vBtn.addEventListener("touchend", onVehPick);
        vSub.appendChild(vBtn);
      });
      // "Sans vehicule" option
      var noneBtn = mkEl("div", "pcorg-ctx-veh-none");
      noneBtn.textContent = "Sans v\u00e9hicule";
      function onNone(ev) {
        ev.stopPropagation(); ev.preventDefault();
        hideContextMenu();
        if (isQuick) {
          quickCreate(ctxLat, ctxLon, vCat, vLevel);
        } else {
          openCreateFromContext(ctxLat, ctxLon, vCat, vLevel);
        }
      }
      noneBtn.addEventListener("click", function (ev) { if (!_ctxIsTouch) onNone(ev); });
      noneBtn.addEventListener("touchend", onNone);
      // "Sans vehicule" en tete de liste
      vSub.insertBefore(noneBtn, vSub.firstChild);
    });

    // Reset item animations
    var items = ctxMenu.querySelectorAll(".pcorg-ctx-item");
    items.forEach(function (it) { it.classList.remove("pcorg-ctx-animate"); });

    var map = getMap();
    if (!map) return;
    var pt = map.latLngToContainerPoint(e.latlng);
    var mapEl = map.getContainer();
    var rect = mapEl.getBoundingClientRect();

    ctxMenu.style.left = (rect.left + pt.x) + "px";
    ctxMenu.style.top = (rect.top + pt.y) + "px";
    ctxMenu.classList.add("show");

    // Trigger stagger animation
    requestAnimationFrame(function () {
      items.forEach(function (it) { it.classList.add("pcorg-ctx-animate"); });
    });

    // Adjust if overflows viewport
    requestAnimationFrame(function () {
      var menuRect = ctxMenu.getBoundingClientRect();
      if (menuRect.right > window.innerWidth) {
        ctxMenu.style.left = (rect.left + pt.x - menuRect.width) + "px";
      }
      if (menuRect.bottom > window.innerHeight) {
        ctxMenu.style.top = (rect.top + pt.y - menuRect.height) + "px";
      }
      // Flip sub-menus if main menu is near right edge
      ctxMenu.classList.toggle("flip-sub", menuRect.right + 240 > window.innerWidth);
    });
  }

  // Place un sous-menu du clic droit a droite ou a gauche de son parent selon
  // la place REELLE a l'ecran. La bascule globale "flip-sub" ne regardait que
  // le 1er niveau (240 px) : le sous-menu vehicules, un cran plus a droite,
  // sortait de la fenetre. On repart du placement CSS, on mesure, et on
  // bascule seulement ce sous-menu (avec sa zone de passage de la souris).
  function placeSubmenuX(sub, owner) {
    sub.style.left = "";
    sub.style.right = "";
    owner.classList.remove("pcorg-sub-left", "pcorg-sub-right");
    var r = sub.getBoundingClientRect();
    if (!r.width) return;
    var margin = 8;
    if (r.right > window.innerWidth - margin) {
      sub.style.left = "auto";
      sub.style.right = "calc(100% + 6px)";
      owner.classList.add("pcorg-sub-left");
    } else if (r.left < margin) {
      sub.style.left = "calc(100% + 6px)";
      sub.style.right = "auto";
      owner.classList.add("pcorg-sub-right");
    }
  }

  function hideContextMenu() {
    if (ctxMenu) {
      ctxMenu.classList.remove("show");
      ctxMenu.querySelectorAll(".touch-open").forEach(function (s) { s.classList.remove("touch-open"); });
    }
  }

  function openCreateFromContext(lat, lon, cat, urgency, patrouille) {
    createHooks = {};
    checkSessionBeforeForm();
    resetCreateWizard();
    applyCreateCategoryFilter();
    if (urgency) createSelectedUrgency = urgency;
    // Pose APRES le reset : le vehicule choisi dans le menu clic droit etait
    // efface par resetCreateWizard avant l'ouverture de l'assistant
    if (patrouille) createPendingPatrouille = patrouille;
    showCreate();
    initCreateMap();

    // Wait for map init, then set position + category and stay on step 1
    // so the user can see/adjust the pin on the mini-map before proceeding
    setTimeout(function () {
      // Pre-select category (updates header color + pin icon)
      selectCategory(cat);
      // Place pin and center mini-map on the clicked location
      setCreatePosition(lat, lon);
      if (createMiniMap) {
        createMiniMap.setView([lat, lon], Math.max(createMiniMap.getZoom(), 16));
      }
      goToStep(1);
    }, 400);
  }

  // ── Vehicle picker (after urgency selection) ───────────────────────────────
  var vehiclePicker = null;

  function showVehiclePicker(lat, lon, cat, level, vehicles, isQuick, menuPos) {
    hideVehiclePicker();
    // Filtre : on n'affiche que les vehicules disponibles. Si tout est
    // indisponible, on saute le picker et on enchaine directement (quickCreate
    // ou wizard sans vehicule).
    var available = (vehicles || []).filter(function (v) {
      return resolveVehicleAvailability(v).isAvailable;
    });
    if (!available.length) {
      if (isQuick) quickCreate(lat, lon, cat, level);
      else openCreateFromContext(lat, lon, cat, level);
      return;
    }
    var st = catStyle(cat);
    vehiclePicker = mkEl("div", "pcorg-vehicle-picker");

    var header = mkEl("div", "pcorg-vp-header");
    header.style.borderColor = st.color;
    var ico = matIcon("directions_car");
    ico.style.color = st.color;
    header.appendChild(ico);
    var title = mkEl("span", "");
    title.textContent = "V\u00e9hicule engag\u00e9";
    header.appendChild(title);
    vehiclePicker.appendChild(header);

    var list = mkEl("div", "pcorg-vp-list");
    // Tri alphabetique (fr, insensible a la casse / accents).
    vehicles = available.slice().sort(function (a, b) {
      return (a.label || "").localeCompare((b.label || ""), "fr", { sensitivity: "base" });
    });
    vehicles.forEach(function (v) {
      var btn = mkEl("button", "pcorg-vp-btn");
      btn.textContent = v.label;
      function onPick(e) {
        e.stopPropagation();
        e.preventDefault();
        hideVehiclePicker();
        if (isQuick) {
          quickCreate(lat, lon, cat, level, v.label);
        } else {
          openCreateFromContext(lat, lon, cat, level, v.label);
        }
      }
      btn.addEventListener("click", function (e) { if (!_ctxIsTouch) onPick(e); });
      btn.addEventListener("touchend", onPick);
      list.appendChild(btn);
    });
    vehiclePicker.appendChild(list);

    var noneBtn = mkEl("button", "pcorg-vp-none");
    noneBtn.textContent = "Sans vehicule";
    function onNone(e) {
      e.stopPropagation();
      e.preventDefault();
      hideVehiclePicker();
      if (isQuick) {
        quickCreate(lat, lon, cat, level);
      } else {
        openCreateFromContext(lat, lon, cat, level);
      }
    }
    noneBtn.addEventListener("click", function (e) { if (!_ctxIsTouch) onNone(e); });
    noneBtn.addEventListener("touchend", onNone);
    // "Sans vehicule" en tete, juste sous le titre
    vehiclePicker.insertBefore(noneBtn, list);

    document.body.appendChild(vehiclePicker);

    // Position near last context menu
    if (menuPos) {
      vehiclePicker.style.left = menuPos.left + "px";
      vehiclePicker.style.top = menuPos.top + "px";
    } else {
      // Fallback: center of screen
      vehiclePicker.style.left = "50%";
      vehiclePicker.style.top = "50%";
      vehiclePicker.style.transform = "translate(-50%, -50%)";
    }

    requestAnimationFrame(function () {
      var rect = vehiclePicker.getBoundingClientRect();
      if (rect.right > window.innerWidth) vehiclePicker.style.left = (window.innerWidth - rect.width - 12) + "px";
      if (rect.bottom > window.innerHeight) vehiclePicker.style.top = (window.innerHeight - rect.height - 12) + "px";
    });

    setTimeout(function () {
      document.addEventListener("click", _vpOutside);
      document.addEventListener("touchstart", _vpOutside);
    }, 50);
    document.addEventListener("keydown", _vpEscape);
  }

  function _vpOutside(e) {
    if (vehiclePicker && !e.target.closest(".pcorg-vehicle-picker")) hideVehiclePicker();
  }
  function _vpEscape(e) {
    if (e.key === "Escape") hideVehiclePicker();
  }

  function hideVehiclePicker() {
    if (vehiclePicker) { vehiclePicker.remove(); vehiclePicker = null; }
    document.removeEventListener("click", _vpOutside);
    document.removeEventListener("touchstart", _vpOutside);
    document.removeEventListener("keydown", _vpEscape);
  }

  var quickCreatePending = false;
  function quickCreate(lat, lon, cat, level, patrouille) {
    if (quickCreatePending) return;
    var ev = window.selectedEvent, yr = window.selectedYear;
    if (!ev || !yr) {
      if (typeof showToast === "function") showToast("warning", "Evenement/annee non selectionnes");
      return;
    }
    quickCreatePending = true;
    var payload = {
      event: ev, year: yr,
      category: cat, niveau_urgence: level,
      lat: lat, lon: lon,
      // Carroyage resolu sur la grille chargee en memoire (et non plus sur
      // celle affichee sur la carte : vide tant que la grille est masquee)
      carroye: resolveCellLabel(lat, lon),
      area_desc: resolveZone(lat, lon),
      client_token: randomToken()
    };
    if (patrouille) payload.patrouille = patrouille;
    apiCall("POST", "/api/pcorg/quick-create", payload).then(function (r) {
      quickCreatePending = false;
      if (r.ok) {
        showToast("success", urgencyLabel(cat, level) + " - fiche creee");
        refresh();
      } else {
        showToast("error", r.error || "Erreur");
      }
    });
  }

  // ── Resolution zone / carroyage (partagee creation, rapide, deplacement) ──
  var sharedGridMeta = null;
  var sharedGridLoading = false;

  function buildGridMeta(data) {
    if (!data || !data.lines) return null;
    var lines = data.lines;
    var numCols = lines.num_cols || (lines.v_lines || []).length - 1;
    var numRows = lines.num_rows || (lines.h_lines || []).length - 1;
    var colOffset = lines.col_offset || 0;
    var rowOffset = lines.row_offset || 0;
    var cols = [];
    for (var ci = 0; ci < numCols; ci++) {
      var adj = ci - colOffset;
      cols.push(adj >= 0 ? colLabel(adj) : null);
    }
    var rows = [];
    for (var ri = 0; ri < numRows; ri++) {
      var rn = ri + 1 - rowOffset;
      rows.push(rn >= 1 ? rn : null);
    }
    return {
      cols: cols, rows: rows,
      hLines: lines.h_lines || [], vLines: lines.v_lines || [],
      numCols: numCols, numRows: numRows,
      colOffset: colOffset, rowOffset: rowOffset
    };
  }

  function ensureSharedGrid(cb) {
    if (sharedGridMeta) { if (cb) cb(); return; }
    var mv = window.CockpitMapView;
    if (mv && mv.getGridData && mv.getGridData()) {
      sharedGridMeta = buildGridMeta(mv.getGridData());
      if (cb) cb();
      return;
    }
    if (sharedGridLoading) return;
    sharedGridLoading = true;
    fetch("/api/grid-ref")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        sharedGridLoading = false;
        sharedGridMeta = buildGridMeta(d);
        if (cb) cb();
      })
      .catch(function () { sharedGridLoading = false; });
  }

  function gridCellFromMeta(m, lat, lon) {
    if (!m) return null;
    var col = null, row = null;
    for (var ci = 0; ci < m.numCols; ci++) {
      if (lon >= m.vLines[ci].lng && lon < m.vLines[ci + 1].lng) { col = ci; break; }
    }
    for (var ri = 0; ri < m.numRows; ri++) {
      if (lat <= m.hLines[ri].lat && lat > m.hLines[ri + 1].lat) { row = ri; break; }
    }
    if (col === null || row === null) return null;
    var colLbl = m.cols[col];
    var rowLbl = m.rows[row];
    if (!colLbl || !rowLbl) return null;
    return colLbl + "" + rowLbl;
  }

  function resolveCellLabel(lat, lon) {
    var label = gridCellFromMeta(sharedGridMeta, lat, lon);
    if (!label && window.CockpitMapView && window.CockpitMapView.getCellLabel) {
      label = window.CockpitMapView.getCellLabel(lat, lon);
    }
    return label || "";
  }

  function resolveZone(lat, lon) {
    if (window.CockpitMapView && window.CockpitMapView.findZoneAtPoint) {
      return window.CockpitMapView.findZoneAtPoint(lat, lon) || "";
    }
    return "";
  }

  // ── Tabs ───────────────────────────────────────────────────────────────────
  function initTabs() {
    var widget = document.getElementById("widget-comms");
    if (!widget) return;
    var tabs = widget.querySelectorAll(".widget-tab");
    var panes = widget.querySelectorAll(".widget-tab-content");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var target = tab.getAttribute("data-tab");
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        panes.forEach(function (p) {
          p.classList.toggle("active", p.getAttribute("data-tab") === target);
        });
      });
    });
  }

  // ── Refresh ────────────────────────────────────────────────────────────────
  function refresh() {
    var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};
    if (!ey.event || !ey.year) return;
    // Recharge la liste des vehicules engageables (inclut les tablettes Field) une fois sur 4
    if (!refresh._vbcCounter || refresh._vbcCounter % 4 === 0) {
      loadVehiclesByCategory();
    }
    refresh._vbcCounter = (refresh._vbcCounter || 0) + 1;
    // Changement d'evenement : on revient a la fenetre par defaut (SAISON :
    // ouvertes des 30 derniers jours) et a la periode de stats par defaut
    var eyKey = ey.event + "|" + ey.year;
    if (eyKey !== liveScopeKey) {
      liveScopeKey = eyKey;
      loadAllOpen = false;
      statsPeriod = "";
    }
    var liveUrl = "/api/pcorg/live?event=" + encodeURIComponent(ey.event) + "&year=" + encodeURIComponent(ey.year)
      + (loadAllOpen ? "&all_open=1" : "");
    var statsUrl = "/api/pcorg/stats?event=" + encodeURIComponent(ey.event) + "&year=" + encodeURIComponent(ey.year)
      + (statsPeriod ? "&period=" + encodeURIComponent(statsPeriod) : "");
    Promise.all([
      fetch(liveUrl, { cache: "no-store" }).then(function (r) { return r.json(); }),
      fetch(statsUrl, { cache: "no-store" }).then(function (r) { return r.json(); }).catch(function () { return null; }),
    ]).then(function (results) {
      var data = results[0];
      var stats = results[1];
      if (!data || data.error) return;
      // Filtrer par categories autorisees (le serveur filtre aussi desormais ;
      // lastData est filtre pour que le panneau elargi et la carte ne voient
      // jamais plus que le widget)
      var ac = window.__userAllowedCategories;
      var filterCat = ac ? function (it) {
        return ac.indexOf(it.category) !== -1 || ac.indexOf(PCS_EQUIVALENT[it.category]) !== -1;
      } : function () { return true; };
      var openFiltered = (data.open || []).filter(filterCat);
      var closedFiltered = (data.closed || []).filter(filterCat);
      data.open = openFiltered;
      data.closed = closedFiltered;
      lastData = data;
      renderWidgetLists();
      // Stats: compte sur la collection complete (fallback aux items charges si l'endpoint echoue)
      var statsCounts = (stats && stats.counts) ? stats.counts : null;
      lastStatsPeriod = (stats && stats.period) ? stats.period : null;
      renderStats(openFiltered, closedFiltered, statsCounts);
      syncTabHeights();
      updateBadge(openFiltered.length);
      updateMapPins(openFiltered);
      if (expPanel && expPanel.style.display !== "none") renderExpanded();
    }).catch(function (err) { console.error("[pcorg] refresh error", err); });
  }

  // ── Filtre texte du petit bloc (local, sur les fiches chargees) ──────────
  var widgetFilterText = "";

  function initWidgetFilter() {
    [listOpen, listClosed].forEach(function (list) {
      if (!list) return;
      var wrap = mkEl("div", "pcorg-widget-filter");
      wrap.appendChild(matIcon("search", "pcorg-widget-filter-ico"));
      var inp = mkEl("input", "pcorg-widget-filter-input");
      inp.type = "search";
      inp.placeholder = "Filtrer (texte, zone, vehicule, n°)...";
      inp.addEventListener("input", function () {
        widgetFilterText = (inp.value || "").trim().toLowerCase();
        // Un seul filtre pour les deux onglets
        document.querySelectorAll(".pcorg-widget-filter-input").forEach(function (o) {
          if (o !== inp) o.value = inp.value;
        });
        renderWidgetLists();
      });
      wrap.appendChild(inp);
      list.insertBefore(wrap, list.firstChild);
    });
  }

  function matchesWidgetFilter(it) {
    if (!widgetFilterText) return true;
    var hay = [it.text, it.category, it.sous_classification, it.area_desc, it.operator,
      it.patrouille, it.niveau_urgence ? urgencyLabel(it.category, it.niveau_urgence) : ""]
      .join(" ").toLowerCase();
    return hay.indexOf(widgetFilterText) !== -1;
  }

  // ── SAISON : fiches ouvertes anciennes (non chargees par defaut) ─────────
  // /live ne rend que les ouvertes des N derniers jours pour SAISON (main
  // courante permanente) et compte les autres dans `older_open`.
  var loadAllOpen = false;
  var liveScopeKey = "";
  var statsPeriod = "";          // "" = defaut serveur (SAISON : aujourd'hui)
  var lastStatsPeriod = null;
  var STATS_PERIOD_CYCLE = ["today", "24h", "7d", "all"];

  function renderOlderOpenHint() {
    if (!listOpen) return;
    var hint = listOpen.querySelector(".pcorg-older-open");
    var n = (lastData && lastData.older_open) || 0;
    if (!n || (lastData && lastData.all_open)) {
      if (hint) hint.remove();
      return;
    }
    if (!hint) {
      hint = mkEl("div", "pcorg-older-open");
      hint.addEventListener("click", function () {
        loadAllOpen = true;
        refresh();
      });
      var filter = listOpen.querySelector(".pcorg-widget-filter");
      if (filter && filter.nextSibling) listOpen.insertBefore(hint, filter.nextSibling);
      else listOpen.appendChild(hint);
    }
    hint.textContent = "";
    hint.appendChild(matIcon("history", ""));
    var days = (lastData && lastData.open_window_days) || 30;
    var txt = mkEl("span", "");
    txt.textContent = n + (n > 1 ? " fiches ouvertes" : " fiche ouverte") + " de plus de " + days
      + " jours - afficher";
    hint.appendChild(txt);
    hint.title = "Fiches SAISON encore ouvertes, non chargees par defaut";
  }

  function renderWidgetLists() {
    if (!lastData) return;
    renderList(listOpen, (lastData.open || []).filter(matchesWidgetFilter), false, placeholderOpen);
    renderList(listClosed, (lastData.closed || []).filter(matchesWidgetFilter), true, placeholderClosed);
    renderOlderOpenHint();
  }

  // ── Render list ────────────────────────────────────────────────────────────
  function renderList(container, items, isClosed, placeholder) {
    if (!container) return;
    var toRemove = container.querySelectorAll(".pcorg-row, .pcorg-detail");
    toRemove.forEach(function (node) { node.remove(); });

    if (placeholder) {
      placeholder.style.display = items.length ? "none" : "";
    }

    items.forEach(function (item) {
      var st = catStyle(item.category);

      // Row
      var row = mkEl("div", "pcorg-row" + (isClosed ? " closed" : ""));
      row.setAttribute("data-id", item.id);

      var bar = mkEl("div", "pcorg-row-bar");
      bar.style.backgroundColor = st.color;
      row.appendChild(bar);

      var ico = matIcon(st.icon, "pcorg-row-icon");
      ico.style.color = st.color;
      row.appendChild(ico);

      var content = mkEl("div", "pcorg-row-content");
      var title = mkEl("div", "pcorg-row-title");
      title.textContent = item.text || "(sans description)";
      content.appendChild(title);

      var meta = mkEl("div", "pcorg-row-meta");
      var catSpan = mkEl("span", "");
      var catB = document.createElement("b");
      catB.textContent = shortCat(item.category);
      catSpan.appendChild(catB);
      meta.appendChild(catSpan);
      if (item.sous_classification) {
        var scSpan = mkEl("span", "");
        scSpan.textContent = item.sous_classification;
        meta.appendChild(scSpan);
      }
      if (item.niveau_urgence) {
        var urgBadge = mkEl("span", "pcorg-urgency-badge pcorg-urgency-" + item.niveau_urgence);
        urgBadge.textContent = urgencyLabel(item.category, item.niveau_urgence);
        meta.appendChild(urgBadge);
      }
      if (item.source_type) {
        var srcMetaRow = SOURCE_BY_ID[item.source_type];
        if (srcMetaRow) {
          var srcIco = matIcon(srcMetaRow.icon, "pcorg-row-src-ico");
          srcIco.style.color = srcMetaRow.color;
          srcIco.title = "Source : " + srcMetaRow.label;
          meta.appendChild(srcIco);
        }
      }
      var zone = truncZone(item.area_desc);
      if (zone) {
        var zSpan = mkEl("span", "");
        zSpan.textContent = zone;
        meta.appendChild(zSpan);
      }
      content.appendChild(meta);
      row.appendChild(content);

      var right = mkEl("div", "pcorg-row-right");
      // Badge antidate / programme (delta ts vs created_at >= 1 min)
      if (!isClosed && item.created_at && item.ts) {
        var tsMs = new Date(item.ts).getTime();
        var caMs = new Date(item.created_at).getTime();
        if (Math.abs(tsMs - caMs) >= 60000) {
          var isFuture = tsMs > Date.now() + 60000;
          var rowBadge = mkEl("span", "pcorg-row-tsbadge pcorg-ts-" + (isFuture ? "future" : "past"));
          rowBadge.appendChild(matIcon(isFuture ? "event_upcoming" : "history"));
          rowBadge.title = isFuture ? "Fiche programmee" : "Fiche antidatee";
          right.appendChild(rowBadge);
        }
      }
      // Dispatch automatique : proposition en cours ou file du service
      var dispSt = !isClosed && item.dispatch ? item.dispatch.state : null;
      if (dispSt === "proposing" || dispSt === "queued") {
        var dIco = matIcon(dispSt === "proposing" ? "hourglass_top" : "pending_actions",
          "pcorg-row-disp pcorg-row-disp-" + dispSt);
        dIco.title = dispSt === "proposing"
          ? "Proposee a " + (item.dispatch.current_device || "une unite") + ", reponse attendue"
          : "En file du service" + (item.dispatch.queue_reason ? " : " + item.dispatch.queue_reason : "");
        right.appendChild(dIco);
      }
      var timeEl = mkEl("span", "pcorg-row-time");
      // Fiches closes : la date si ce n'est pas aujourd'hui (evenements sur
      // plusieurs jours : "14:05" seul etait ambigu)
      timeEl.textContent = isClosed ? fmtDayTime(item.close_ts) : timeAgo(item.ts);
      if (isClosed && item.operator_close) timeEl.title = "Close par " + operatorLabel(item.operator_close);
      right.appendChild(timeEl);

      var gpsIcon = matIcon(item.lat != null ? "location_on" : "location_off",
        "pcorg-row-gps " + (item.lat != null ? "has-gps" : "no-gps"));
      gpsIcon.style.fontSize = "14px";
      if (item.lat == null) {
        gpsIcon.title = "Ajouter une position";
        gpsIcon.addEventListener("click", (function (id) {
          return function (e) { e.stopPropagation(); openGpsModal(id); };
        })(item.id));
      } else {
        gpsIcon.title = "Voir sur la carte";
        gpsIcon.addEventListener("click", (function (lat, lon, id) {
          return function (e) { e.stopPropagation(); flyToPin(lat, lon, id); };
        })(item.lat, item.lon, item.id));
      }
      right.appendChild(gpsIcon);
      row.appendChild(right);
      if (activeFicheId === item.id) row.classList.add("pcorg-row-active");

      row.addEventListener("click", (function (id, closed) {
        return function () { openDetailModal(id, closed); };
      })(item.id, isClosed));

      container.appendChild(row);
    });
  }

  // ── Sync tab heights to stats panel ─────────────────────────────────────────
  function syncTabHeights() {
    if (!statsContainer) return;
    // Measure stats height (it's the reference)
    var h = statsContainer.scrollHeight;
    if (h > 0) {
      var px = h + "px";
      if (listOpen) listOpen.style.height = px;
      if (listClosed) listClosed.style.height = px;
      statsContainer.style.height = px;
    }
  }

  // ── Stats dashboard ─────────────────────────────────────────────────────────
  var ALL_CATEGORIES = [
    "PCO.Secours", "PCO.Securite", "PCO.Technique",
    "PCO.Flux", "PCO.Information", "PCO.MainCourante", "PCO.Fourriere"
  ];

  function getAllowedCategories() {
    var ac = window.__userAllowedCategories;
    if (!ac) return ALL_CATEGORIES; // null = pas de restriction
    return ALL_CATEGORIES.filter(function (c) { return ac.indexOf(c) !== -1; });
  }

  var CATEGORY_ORDER = getAllowedCategories();

  // Compteurs par icone : une fiche PCS compte sous l'icone de sa categorie PCO
  // equivalente (PCS Surete -> Securite, PCS Information -> Information). La
  // difference est gardee partout ailleurs (libelle de la fiche, filtre).
  function statCat(cat) {
    return PCS_EQUIVALENT[cat] || cat;
  }

  function renderStats(openItems, closedItems, serverCounts) {
    if (!statsContainer) return;
    statsContainer.textContent = "";
    var order = CATEGORY_ORDER;

    // Count per category
    var counts = {};
    order.forEach(function (cat) { counts[cat] = { open: 0, closed: 0 }; });

    if (serverCounts) {
      // Compte serveur sur la collection complete
      Object.keys(serverCounts).forEach(function (raw) {
        var c = serverCounts[raw] || {};
        var cat = statCat(raw);
        if (!counts[cat]) counts[cat] = { open: 0, closed: 0 };
        counts[cat].open += c.open || 0;
        counts[cat].closed += c.closed || 0;
      });
    } else {
      // Fallback: compte local sur les items charges
      openItems.forEach(function (item) {
        var cat = statCat(item.category || "");
        if (!counts[cat]) counts[cat] = { open: 0, closed: 0 };
        counts[cat].open++;
      });
      closedItems.forEach(function (item) {
        var cat = statCat(item.category || "");
        if (!counts[cat]) counts[cat] = { open: 0, closed: 0 };
        counts[cat].closed++;
      });
    }

    var grid = mkEl("div", "pcorg-stats-grid");

    order.forEach(function (cat) {
      var c = counts[cat];
      if (!c) c = { open: 0, closed: 0 };
      var st = catStyle(cat);

      var card = mkEl("div", "pcorg-stat-card");
      card.style.borderLeftColor = st.color;
      card.title = shortCat(cat);

      var ico = matIcon(st.icon, "pcorg-stat-icon");
      ico.style.color = st.color;
      card.appendChild(ico);

      var cnts = mkEl("div", "pcorg-stat-counts");
      var openEl = mkEl("span", "pcorg-stat-open");
      openEl.style.color = c.open > 0 ? st.color : "var(--muted)";
      openEl.textContent = c.open;
      cnts.appendChild(openEl);

      var closedEl = mkEl("span", "pcorg-stat-closed");
      closedEl.textContent = c.closed;
      cnts.appendChild(closedEl);

      card.appendChild(cnts);
      card.style.cursor = "pointer";
      card.addEventListener("click", (function (catName) {
        return function () { focusMostRecentByCat(catName); };
      })(cat));
      grid.appendChild(card);
    });

    statsContainer.appendChild(grid);

    // Periode appliquee par le serveur (SAISON : aujourd'hui par defaut).
    // Clic : periode suivante (aujourd'hui -> 24 h -> 7 j -> tout).
    if (serverCounts && lastStatsPeriod) {
      var per = mkEl("div", "pcorg-stats-period");
      per.textContent = "Ouvertes : toutes - Closes : " + (lastStatsPeriod.label || lastStatsPeriod.key);
      per.title = "Changer la periode des fiches closes";
      per.addEventListener("click", function () {
        var curKey = (lastStatsPeriod && lastStatsPeriod.key) || "all";
        var idx = STATS_PERIOD_CYCLE.indexOf(curKey);
        statsPeriod = STATS_PERIOD_CYCLE[(idx + 1) % STATS_PERIOD_CYCLE.length];
        refresh();
      });
      statsContainer.appendChild(per);
    }
  }

  // ── Focus most recent intervention by category ─────────────────────────────
  function focusMostRecentByCat(cat) {
    if (!lastData || !lastData.open) return;
    // Trouver l'intervention ouverte la plus recente de cette categorie avec GPS
    var match = null;
    for (var i = 0; i < lastData.open.length; i++) {
      var item = lastData.open[i];
      if (statCat(item.category) === cat && item.lat != null && item.lon != null) {
        match = item;
        break; // deja triees par ts desc
      }
    }
    if (!match) {
      // Pas de GPS, ouvrir la fiche de la plus recente sans GPS
      for (var j = 0; j < lastData.open.length; j++) {
        if (statCat(lastData.open[j].category) === cat) { match = lastData.open[j]; break; }
      }
      if (match) {
        openDetailModal(match.id, false);
      } else {
        showToast("info", "Aucune intervention en cours pour " + shortCat(cat));
      }
      return;
    }
    // Switch carte + fly + ouvrir popup
    ensureMapVisible();
    var map = getMap();
    setTimeout(function () {
      if (!map) return;
      // Ouvrir le popup d'abord pour mesurer sa hauteur
      var marker = pcorgMarkers[match.id];
      if (marker) marker.openPopup();
      // Decaler le centrage vers le bas pour que le popup ne soit pas coupe
      setTimeout(function () {
        var popupPx = 200; // estimation hauteur popup en pixels
        var popup = marker ? marker.getPopup() : null;
        if (popup && popup.getElement()) {
          popupPx = popup.getElement().offsetHeight || 200;
        }
        var targetPoint = map.project([match.lat, match.lon], 17);
        targetPoint.y -= popupPx / 2;
        var targetLatLng = map.unproject(targetPoint, 17);
        map.flyTo(targetLatLng, 17, { duration: 0.8 });
      }, 100);
    }, 300);
  }

  // ── Detail modal ────────────────────────────────────────────────────────────
  var detailModal = null;
  var detailOverlay = null;
  var detailMiniMap = null;

  function showFiche() {
    detailModal.classList.add("show");
    detailOverlay.classList.add("show");
  }
  function hideFiche() {
    if (!detailModal) return;
    detailModal.classList.remove("show");
    detailOverlay.classList.remove("show");
    detailModal.classList.remove("pca-over");
    detailOverlay.classList.remove("pca-over");
    destroyMiniMap();
    detailOpenId = null;
    setActiveFiche(null);
  }

  var detailOpenId = null;

  // opts.keepScroll : rechargement apres une action (commentaire, urgence...)
  // sans remonter en haut de la fiche ni faire clignoter un "Chargement".
  function openDetailModal(id, isClosed, opts) {
    opts = opts || {};
    detailModal = detailModal || document.getElementById("pcorgDetailModal");
    detailOverlay = detailOverlay || document.getElementById("pcorgDetailOverlay");
    if (!detailModal) return;
    var body = document.getElementById("pcorg-fiche-body");
    var scrollEl = detailModal.querySelector(".pcorg-fiche-modal") || body;
    var keep = opts.keepScroll && detailOpenId === id && detailModal.classList.contains("show");
    var prevScroll = keep ? body.scrollTop : 0;
    var prevScrollModal = keep ? scrollEl.scrollTop : 0;
    if (!keep) {
      body.textContent = "";
      var loading = mkEl("div", "widget-placeholder");
      loading.appendChild(matIcon("hourglass_top"));
      var lt = mkEl("span", ""); lt.textContent = "Chargement..."; loading.appendChild(lt);
      body.appendChild(loading);
    }
    detailOpenId = id;
    showFiche();
    setActiveFiche(id);

    // Wire close
    var closeBtn = document.getElementById("pcorgDetailClose");
    closeBtn.onclick = function () { hideFiche(); };
    detailOverlay.onclick = function () { hideFiche(); };

    fetch("/api/pcorg/detail/" + encodeURIComponent(id))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { body.textContent = d.error; return; }
        // Ack bounce for this user
        ackPin(id, d.bounce_rev || 0);
        var mk = pcorgMarkers[id];
        if (mk && mk.getElement && mk.getElement()) mk.getElement().classList.remove("pcorg-pin-bounce");
        // Statut lu sur la fiche elle-meme, pas sur la liste d'ou vient le clic
        renderFiche(d, d.status_code === 10);
        if (keep) {
          body.scrollTop = prevScroll;
          scrollEl.scrollTop = prevScrollModal;
        }
      })
      .catch(function () { body.textContent = "Erreur de chargement"; });
  }

  function reloadFiche(id) {
    if (detailOpenId === id && detailModal && detailModal.classList.contains("show")) {
      openDetailModal(id, false, { keepScroll: true });
    }
  }

  function destroyMiniMap() {
    if (detailMiniMap) { detailMiniMap.remove(); detailMiniMap = null; }
  }

  // ── Dispatch automatique dans la fiche ─────────────────────────────────────
  var DISPATCH_OUTCOMES = {
    resolu: "Resolu", partiel: "Resolu partiellement",
    materiel: "Besoin de materiel ou de renfort", impossible: "Intervention impossible"
  };
  var dispatchCountdownTimer = null;

  function _dispDelay(fromIso, toIso) {
    var a = fromIso ? new Date(fromIso).getTime() : NaN;
    var b = toIso ? new Date(toIso).getTime() : NaN;
    if (isNaN(a) || isNaN(b)) return "";
    var m = Math.max(0, Math.round((b - a) / 60000));
    return m < 60 ? m + " min" : Math.floor(m / 60) + " h " + _pad2(m % 60);
  }

  // Ligne d'etat du dispatch + bouton "Proposer automatiquement" + temps
  // d'intervention. Rend null si rien a montrer.
  function buildDispatchBlock(d, closed) {
    if (dispatchCountdownTimer) { clearInterval(dispatchCountdownTimer); dispatchCountdownTimer = null; }
    var disp = d.dispatch || null;
    var inter = d.intervention || {};
    var cc = d.content_category || {};
    var hasUnit = !!String(cc.patrouille || d.patrouille || "").trim();
    var state = disp ? disp.state : null;
    var canPropose = !closed && !hasUnit && state !== "proposing";
    var hasInter = !!(inter.engaged_at || inter.arrived_at || inter.done_at);
    var showLine = state === "proposing" || state === "queued";
    if (!showLine && !canPropose && !hasInter) return null;

    var wrap = mkEl("div", "pcorg-dispatch");

    if (state === "proposing") {
      var line = mkEl("div", "pcorg-dispatch-line is-proposing");
      line.appendChild(matIcon("hourglass_top"));
      var txt = mkEl("span", "");
      txt.textContent = disp.current_device ? "Proposee a " + disp.current_device : "Recherche automatique d'une unite...";
      line.appendChild(txt);
      if (disp.expires_at) {
        var cd = mkEl("strong", "pcorg-dispatch-countdown");
        line.appendChild(cd);
        var exp = new Date(disp.expires_at).getTime();
        var reloaded = false;
        var upd = function () {
          if (!cd.isConnected) { clearInterval(dispatchCountdownTimer); dispatchCountdownTimer = null; return; }
          var left = Math.round((exp - Date.now()) / 1000);
          cd.textContent = left > 0 ? left + " s" : "reponse attendue";
          // Echeance passee : relire la fiche (unite suivante ou file du service)
          if (left < -5 && !reloaded) {
            reloaded = true;
            clearInterval(dispatchCountdownTimer); dispatchCountdownTimer = null;
            reloadFiche(d.id);
          }
        };
        upd();
        dispatchCountdownTimer = setInterval(upd, 1000);
      }
      wrap.appendChild(line);
    } else if (state === "queued") {
      var q = mkEl("div", "pcorg-dispatch-line is-queued");
      q.appendChild(matIcon("pending_actions"));
      var qt = mkEl("span", "");
      qt.textContent = "En file du service" + (disp.queue_reason ? " : " + disp.queue_reason : "");
      q.appendChild(qt);
      wrap.appendChild(q);
    }

    if (canPropose) {
      var btn = mkEl("button", "pcorg-dispatch-btn");
      btn.type = "button";
      btn.appendChild(matIcon("smart_toy"));
      var bl = mkEl("span", "");
      bl.textContent = "Proposer automatiquement";
      btn.appendChild(bl);
      btn.title = "Proposer la fiche a l'unite disponible la plus proche de la categorie";
      btn.addEventListener("click", function () {
        btn.disabled = true;
        apiCall("POST", "/api/dispatch/" + encodeURIComponent(d.id) + "/auto", {}).then(function (res) {
          btn.disabled = false;
          if (!res || res.ok === false || res.error) {
            var msgs = {
              fiche_closee: "La fiche est close.", deja_engagee: "Une unite est deja engagee.",
              deja_en_cours: "Une proposition est deja en cours.", forbidden: "Categorie non autorisee."
            };
            showToast("error", msgs[res && res.error] || (res && res.error) || "Echec de la proposition");
          } else if (res.state === "queued") {
            showToast("warning", "Aucune unite disponible : fiche mise en file du service");
          } else {
            showToast("success", "Proposition automatique lancee");
          }
          reloadFiche(d.id);
        });
      });
      wrap.appendChild(btn);
    }

    if (hasInter) {
      var times = mkEl("div", "pcorg-dispatch-times");
      var add = function (label, iso, extra) {
        if (!iso) return;
        var it = mkEl("span", "");
        var b = document.createElement("b");
        b.textContent = label + " ";
        it.appendChild(b);
        it.appendChild(document.createTextNode(fmtDayTime(iso) + (extra ? " (" + extra + ")" : "")));
        times.appendChild(it);
      };
      add("Engagement", inter.engaged_at, inter.device_name || "");
      add("Arrivee", inter.arrived_at, _dispDelay(inter.engaged_at, inter.arrived_at));
      add("Fin", inter.done_at, DISPATCH_OUTCOMES[inter.outcome] || inter.outcome || "");
      if (inter.report) {
        var rep = mkEl("span", "pcorg-dispatch-report");
        rep.textContent = inter.report;
        times.appendChild(rep);
      }
      wrap.appendChild(times);
    }
    return wrap;
  }

  function renderFiche(d, isClosed) {
    var st = catStyle(d.category);
    var cc = d.content_category || {};

    // Header
    var header = document.getElementById("pcorg-fiche-header");
    header.style.background = st.color;
    header.querySelector(".pcorg-fiche-icon").textContent = st.icon;
    header.querySelector(".pcorg-fiche-cat").textContent = shortCat(d.category);
    header.querySelector(".pcorg-fiche-subcat").textContent = cc.sous_classification || cc.classification || cc.typedemande || "";
    header.querySelector(".pcorg-fiche-num").textContent = d.sql_id ? "N\u00b0 " + d.sql_id : "";
    var statusEl = header.querySelector(".pcorg-fiche-status");
    statusEl.textContent = d.status_code === 10 ? "TERMINE" : "EN COURS";
    statusEl.className = "pcorg-fiche-status " + (d.status_code === 10 ? "closed" : "open");

    // Urgency badge in header
    var urgEl = header.querySelector(".pcorg-fiche-urgency");
    if (urgEl) {
      if (d.niveau_urgence) {
        urgEl.textContent = urgencyLabel(d.category, d.niveau_urgence);
        urgEl.className = "pcorg-fiche-urgency pcorg-urgency-" + d.niveau_urgence;
        urgEl.style.display = "";
      } else {
        urgEl.style.display = "none";
      }
    }

    // Body
    var body = document.getElementById("pcorg-fiche-body");
    body.textContent = "";

    // Description
    var descText = d.text_full || d.text;
    if (descText) {
      var desc = mkEl("div", "pcorg-fiche-desc");
      desc.style.borderLeftColor = st.color;
      desc.textContent = descText;
      body.appendChild(desc);
    }

    // Urgency level selector (only if not closed). Meme regle que la creation
    // et l'edition : categories configurees, ou fiche portant deja un niveau.
    if (!isClosed && d.status_code !== 10 && urgencyEnabledFor(d.category, d.niveau_urgence)) {
      var urgSec = mkEl("div", "pcorg-fiche-section");
      urgSec.textContent = "Niveau d'urgence";
      body.appendChild(urgSec);
      body.appendChild(buildUrgencyButtons(d.niveau_urgence, d.category, d.id, false, d));
    }

    // Info row (fields + mini map)
    var infoRow = mkEl("div", "pcorg-fiche-info-row");

    // Fields
    var fields = mkEl("div", "pcorg-fiche-fields");
    var opDisplay = d.operator || "";
    if (d.operator_group) opDisplay += " (" + d.operator_group + ")";
    addField(fields, "Operateur", opDisplay);
    if (d.ts) addInterventionTsField(fields, d, isClosed);
    if (d.close_ts && d.status_code === 10) addField(fields, "Cloture", new Date(d.close_ts).toLocaleString("fr-FR"));
    if (d.status_code === 10 && d.operator_close) addField(fields, "Clos par", operatorLabel(d.operator_close));
    if (d.sql_id) {
      var owned = d.cockpit_owned || [];
      addField(fields, "Origine", owned.length
        ? "Prysm, modifiee dans Cockpit (modifications conservees a la synchro)"
        : "Prysm (synchro SQL)");
    }
    addField(fields, "Zone", truncZone(d.area_desc));
    addSourceField(fields, cc);
    addField(fields, "Carroye", cc.carroye);
    addField(fields, "Groupe", formatGroupDesc(d.group_desc, d.category));
    infoRow.appendChild(fields);

    // Vehicle engagement banner (prominent, before minimap)
    if (cc.patrouille) {
      var ficheDevInfo = window.getAnolocDeviceByLabel ? window.getAnolocDeviceByLabel(cc.patrouille) : null;
      var ficheDevSt = ficheDevInfo ? _resolveDeviceStatus(ficheDevInfo.device) : null;
      var vBanner = mkEl("div", "pcorg-fiche-vehicle");
      vBanner.appendChild(matIcon("directions_car", "pcorg-fiche-vehicle-ico"));
      var vInfo = mkEl("div", "pcorg-fiche-vehicle-info");
      var vLbl = mkEl("span", "pcorg-fiche-vehicle-lbl");
      vLbl.textContent = "Element engage";
      vInfo.appendChild(vLbl);
      var vName = mkEl("strong", "pcorg-fiche-vehicle-name");
      vName.textContent = cc.patrouille;
      vInfo.appendChild(vName);
      vBanner.appendChild(vInfo);
      if (ficheDevSt) {
        var fvStatusBadge = mkEl("span", "pcorg-fiche-vehicle-status");
        var fvDot = mkEl("span", "pcorg-fiche-vehicle-dot");
        fvDot.style.background = ficheDevSt.color;
        fvStatusBadge.appendChild(fvDot);
        fvStatusBadge.appendChild(document.createTextNode(ficheDevSt.label));
        fvStatusBadge.style.color = ficheDevSt.color;
        vBanner.appendChild(fvStatusBadge);
      }
      // Bouton "Calculer itineraire" : ouvre la modale Routing (vehicule -> fiche).
      // Sans position sur la fiche, il n'y a pas d'arrivee a calculer.
      if (d.lat != null && window.RoutingModal && typeof window.RoutingModal.openForFiche === "function") {
        var rtBtn = mkEl("button", "pcorg-fiche-vehicle-route");
        rtBtn.type = "button";
        rtBtn.appendChild(matIcon("route", "pcorg-fiche-vehicle-route-ico"));
        var rtTxt = document.createElement("span");
        rtTxt.textContent = "Calculer itineraire";
        rtBtn.appendChild(rtTxt);
        rtBtn.addEventListener("click", function () {
          window.RoutingModal.openForFiche(d.id, cc.patrouille);
        });
        vBanner.appendChild(rtBtn);
      }
      body.appendChild(vBanner);
    }

    // Dispatch automatique (dispatch_auto.py) : proposition en cours, file du
    // service, horodatages d'intervention
    var dispBlock = buildDispatchBlock(d, isClosed || d.status_code === 10);
    if (dispBlock) body.appendChild(dispBlock);

    // Mini map
    var mapDiv = mkEl("div", "pcorg-fiche-minimap");
    if (d.lat != null && d.lon != null) {
      mapDiv.id = "pcorg-minimap-container";
      infoRow.appendChild(mapDiv);
      body.appendChild(infoRow);
      setTimeout(function () { initMiniMap(d.lat, d.lon, st); }, 100);
    } else {
      mapDiv.classList.add("empty");
      mapDiv.textContent = "Pas de position";
      infoRow.appendChild(mapDiv);
      body.appendChild(infoRow);
    }

    // Category-specific details
    var specific = buildSpecificFields(d.category, cc);
    if (specific.length > 0) {
      var secTitle = mkEl("div", "pcorg-fiche-section");
      secTitle.textContent = "Details";
      body.appendChild(secTitle);
      var specFields = mkEl("div", "pcorg-fiche-fields");
      specific.forEach(function (f) { addField(specFields, f[0], f[1]); });
      body.appendChild(specFields);
    }

    // Extracted entities
    if ((d.phones && d.phones.length) || (d.plates && d.plates.length)) {
      var entSec = mkEl("div", "pcorg-fiche-section");
      entSec.textContent = "Extractions";
      body.appendChild(entSec);
      var entFields = mkEl("div", "pcorg-fiche-fields");
      if (d.phones && d.phones.length) addField(entFields, "Telephones", d.phones.join(", "));
      if (d.plates && d.plates.length) addField(entFields, "Plaques", d.plates.join(", "));
      body.appendChild(entFields);
    }

    // Chronology
    var history = d.comment_history || [];
    if (history.length > 0) {
      var chronoSec = mkEl("div", "pcorg-fiche-section");
      chronoSec.textContent = "Chronologie";
      body.appendChild(chronoSec);
      body.appendChild(renderChronology(history, st.color, { compact: false, refTs: d.ts }));
    }

    // Add comment form (only if not closed)
    if (!isClosed && d.status_code !== 10) {
      var commentSec = mkEl("div", "pcorg-fiche-section");
      commentSec.textContent = "Consigner une action";
      body.appendChild(commentSec);

      var commentForm = mkEl("div", "pcorg-comment-form");
      var commentInput = mkEl("textarea", "form-input pcorg-comment-input");
      commentInput.rows = 2;
      commentInput.placeholder = "Action realisee, observation, consigne...";
      commentForm.appendChild(commentInput);

      var commentBtns = mkEl("div", "pcorg-comment-btns");

      var camBtn = mkEl("button", "pcorg-comment-cam");
      camBtn.appendChild(matIcon("videocam"));
      camBtn.title = "Joindre une capture camera";
      camBtn.addEventListener("click", function () {
        openCameraPicker(function (camId, camName) {
          camBtn.disabled = true;
          showToast("info", "Capture " + camName + " en cours...");
          apiCall("POST", "/api/pcorg/camera-capture", { cam_id: camId, fiche_id: d.id })
            .then(function (r) {
              camBtn.disabled = false;
              if (r.ok) {
                showToast("success", "Photo " + camName + " ajoutee");
                reloadFiche(d.id);
              } else {
                showToast("error", r.error || "Camera injoignable ou erreur capture");
              }
            });
        });
      });
      commentBtns.appendChild(camBtn);

      // Photo prise sur le moment ; le texte saisi sert de legende
      var photoBtn = mkEl("button", "pcorg-comment-cam");
      photoBtn.appendChild(matIcon("add_a_photo"));
      photoBtn.title = "Joindre une photo (appareil photo ou fichier)";
      photoBtn.addEventListener("click", function () {
        pickPhotos(function (blobs) {
          photoBtn.disabled = true;
          showToast("info", "Envoi de " + (blobs.length > 1 ? blobs.length + " photos" : "la photo") + "...");
          uploadFichePhotos(d.id, blobs, commentInput.value.trim()).then(function (r) {
            photoBtn.disabled = false;
            if (r.ok) {
              commentInput.value = "";
              showToast("success", blobs.length > 1 ? "Photos ajoutees" : "Photo ajoutee");
              reloadFiche(d.id);
            } else {
              showToast("error", r.error || "Erreur");
            }
          });
        });
      });
      commentBtns.appendChild(photoBtn);

      var commentBtn = mkEl("button", "pcorg-comment-send");
      commentBtn.appendChild(matIcon("send"));
      commentBtn.title = "Envoyer";
      commentBtn.addEventListener("click", function () {
        var txt = commentInput.value.trim();
        if (!txt) return;
        commentBtn.disabled = true;
        apiCall("POST", "/api/pcorg/comment/" + encodeURIComponent(d.id), { text: txt })
          .then(function (r) {
            commentBtn.disabled = false;
            if (r.ok) {
              commentInput.value = "";
              showToast("success", "Commentaire ajoute");
              reloadFiche(d.id);
            } else {
              // Le brouillon reste dans le champ
              showToast("error", r.error || "Erreur");
            }
          });
      });
      commentInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); commentBtn.click(); }
      });
      commentBtns.appendChild(commentBtn);
      commentForm.appendChild(commentBtns);
      body.appendChild(commentForm);
    }

    // Precedents (autres editions), charges a la demande
    body.appendChild(pcaBuildPrecedentsBlock(d));

    // Actions
    var actions = mkEl("div", "pcorg-fiche-actions");
    var ficheOpen = !isClosed && d.status_code !== 10;
    if (d.lat != null) {
      var btnMap = mkEl("button", "");
      btnMap.appendChild(matIcon("map"));
      btnMap.appendChild(document.createTextNode(" Voir sur carte"));
      btnMap.addEventListener("click", function () {
        hideFiche(); flyToPin(d.lat, d.lon, d.id);
      });
      actions.appendChild(btnMap);
    }
    // Poser ou DEPLACER la position (avant : seulement si absente)
    if (ficheOpen && canEditFiche(d)) {
      var btnGps = mkEl("button", "");
      btnGps.appendChild(matIcon(d.lat != null ? "edit_location_alt" : "add_location"));
      btnGps.appendChild(document.createTextNode(d.lat != null ? " Deplacer" : " Ajouter position"));
      btnGps.addEventListener("click", function () {
        hideFiche(); openGpsModal(d.id, d.lat, d.lon);
      });
      actions.appendChild(btnGps);
    }
    // Edit button (not closed, groupe non "lecture seule")
    if (ficheOpen && canEditFiche(d)) {
      var btnEdit = mkEl("button", "");
      btnEdit.appendChild(matIcon("edit"));
      btnEdit.appendChild(document.createTextNode(" Editer"));
      btnEdit.addEventListener("click", function () {
        renderFicheEdit(d);
      });
      actions.appendChild(btnEdit);
    }
    if (!isClosed && d.status_code !== 10 && canCloseFicheCat(d.category)) {
      var btnClose = mkEl("button", "pcorg-btn-danger");
      btnClose.appendChild(matIcon("check_circle"));
      btnClose.appendChild(document.createTextNode(" Clore"));
      btnClose.addEventListener("click", function () {
        closeIntervention(d.id);
      });
      actions.appendChild(btnClose);
    }
    // Reouverture (fiche close par erreur, reprise d'intervention)
    if (d.status_code === 10 && canCloseFicheCat(d.category)) {
      var btnReopen = mkEl("button", "");
      btnReopen.appendChild(matIcon("restart_alt"));
      btnReopen.appendChild(document.createTextNode(" Rouvrir"));
      btnReopen.addEventListener("click", function () { reopenIntervention(d.id); });
      actions.appendChild(btnReopen);
    }
    // Delete button (admin only)
    if (window.__userIsAdmin) {
      var btnDel = mkEl("button", "pcorg-btn-delete");
      btnDel.appendChild(matIcon("delete"));
      btnDel.appendChild(document.createTextNode(" Supprimer"));
      btnDel.addEventListener("click", function () {
        showConfirmToast("Supprimer definitivement cette intervention ?", { type: "error", okLabel: "Supprimer" }).then(function (ok) {
          if (!ok) return;
          deleteIntervention(d.id);
        });
      });
      actions.appendChild(btnDel);
    }
    body.appendChild(actions);
  }

  // ── Chronologie (fiche et popup carte) ──────────────────────────────────
  // Entrees decorees cote serveur (pcorg_history.decorate_history) :
  //   kind        status | system | change | empty | comment
  //   status_from / status_to   ligne "Statut: A -> B" extraite du texte
  //   changes     [{field, old, new}] (modifications Prysm ou Cockpit)
  //   body        commentaire libre, SANS les lignes systeme
  //   synthetic   cloture reconstituee depuis close_ts / operator_close
  // Avant, toute entree commencant par "Statut:" etait en italique grise,
  // y compris le commentaire de cloture qui la suit : illisible.
  function chronoTime(dt, withDate) {
    var hm = _pad2(dt.getHours()) + ":" + _pad2(dt.getMinutes());
    return withDate ? _pad2(dt.getDate()) + "/" + _pad2(dt.getMonth() + 1) + " " + hm : hm;
  }

  function renderChronology(history, color, opts) {
    opts = opts || {};
    var wrap = mkEl("div", "pcorg-fiche-timeline" + (opts.compact ? " pcorg-chrono-compact" : ""));
    wrap.style.setProperty("--cat-color", color);
    var today = new Date();
    var prevDt = null;
    history.forEach(function (entry) {
      var kind = entry.kind || (entry.text && entry.text.indexOf("Statut:") === 0 ? "status" : "comment");
      var closed = kind === "status" && /^termin/i.test(entry.status_to || "");
      var reopened = kind === "status" && /^termin/i.test(entry.status_from || "");
      var ent = mkEl("div", "pcorg-chrono-entry kind-" + kind
        + (closed ? " is-closed" : "") + (reopened ? " is-reopened" : "")
        + (entry.synthetic ? " is-synthetic" : ""));
      ent.style.setProperty("--dot-color",
        kind === "comment" ? color : (closed ? "#16a34a" : (reopened ? "#f59e0b" : "#94a3b8")));

      // En-tete : heure (date au premier element et a chaque changement de
      // jour, ou si ce n'est pas aujourd'hui) + operateur
      var head = mkEl("div", "pcorg-chrono-head");
      var tsEl = mkEl("span", "pcorg-chrono-ts");
      var dt = entry.ts ? new Date(entry.ts) : null;
      if (dt && !isNaN(dt.getTime())) {
        var withDate = prevDt ? !_isSameDay(dt, prevDt) : !_isSameDay(dt, today);
        tsEl.textContent = chronoTime(dt, withDate);
        tsEl.title = dt.toLocaleString("fr-FR");
        prevDt = dt;
      } else {
        tsEl.textContent = "--:--";
        tsEl.title = "Heure inconnue";
      }
      head.appendChild(tsEl);
      var opEl = mkEl("span", "pcorg-chrono-op");
      opEl.textContent = operatorLabel(entry.operator) || (entry.synthetic ? "operateur non renseigne" : "");
      head.appendChild(opEl);
      ent.appendChild(head);

      if (kind === "status" && entry.status_to) {
        var pill = mkEl("span", "pcorg-chrono-status" + (closed ? " closed" : "") + (reopened ? " reopened" : ""));
        pill.appendChild(matIcon(closed ? "check_circle" : (reopened ? "restart_alt" : "sync_alt")));
        var pillTxt = mkEl("span", "");
        pillTxt.textContent = (entry.status_from ? entry.status_from + " → " : "") + entry.status_to;
        pill.appendChild(pillTxt);
        if (entry.synthetic) pill.title = "Cloture enregistree sur la fiche, sans ligne de statut dans la chronologie";
        ent.appendChild(pill);
      }

      var bodyText = entry.body != null ? entry.body : (entry.text || entry.comment || "");
      if (kind === "system" && bodyText) {
        var sys = mkEl("div", "pcorg-chrono-system");
        sys.appendChild(matIcon("tune"));
        var sysTxt = mkEl("span", ""); sysTxt.textContent = bodyText;
        sys.appendChild(sysTxt);
        ent.appendChild(sys);
        bodyText = "";
      }

      var changes = entry.changes || [];
      if (changes.length) {
        var ul = mkEl("ul", "pcorg-chrono-changes");
        changes.forEach(function (c) {
          var li = document.createElement("li");
          var f = document.createElement("b"); f.textContent = c.field;
          li.appendChild(f);
          li.appendChild(document.createTextNode(" : "));
          if (c.old) {
            var o = mkEl("span", "pcorg-chrono-old"); o.textContent = c.old;
            li.appendChild(o);
            li.appendChild(document.createTextNode(" → "));
          }
          var n = mkEl("span", "pcorg-chrono-new"); n.textContent = c["new"] || "(vide)";
          li.appendChild(n);
          ul.appendChild(li);
        });
        ent.appendChild(ul);
      }

      if (bodyText) {
        var txtEl = mkEl("div", "pcorg-chrono-text");
        txtEl.textContent = bodyText;
        ent.appendChild(txtEl);
      } else if (kind === "empty") {
        var em = mkEl("div", "pcorg-chrono-empty");
        em.textContent = "Mise a jour sans commentaire";
        ent.appendChild(em);
      }

      var photos = (entry.photos && entry.photos.length) ? entry.photos
        : (entry.photo ? [{ photo: entry.photo, thumb: entry.thumb }] : []);
      if (photos.length) {
        var photoWrap = mkEl("div", "pcorg-chrono-photo-wrap");
        photos.forEach(function (p) {
          var img = mkEl("img", "pcorg-chrono-photo");
          img.src = p.thumb || p.photo;
          img.alt = "Photo terrain";
          img.loading = "lazy";
          img.addEventListener("click", function (e) { e.stopPropagation(); openPhotoLightbox(p.photo); });
          photoWrap.appendChild(img);
        });
        ent.appendChild(photoWrap);
      }
      if (entry.codes && entry.codes.length) {
        var codesWrap = mkEl("div", "pcorg-chrono-codes");
        entry.codes.forEach(function (c) {
          var chip = mkEl("div", "pcorg-chrono-code");
          var fmtEl = mkEl("span", "pcorg-chrono-code-fmt");
          fmtEl.textContent = (c.format || "manual").toUpperCase();
          var valEl = mkEl("span", "pcorg-chrono-code-val");
          valEl.textContent = c.value || "";
          chip.appendChild(fmtEl);
          chip.appendChild(valEl);
          codesWrap.appendChild(chip);
        });
        ent.appendChild(codesWrap);
      }
      wrap.appendChild(ent);
    });
    return wrap;
  }

  // ── Champs propres a chaque categorie (creation ET edition) ─────────────
  // Un seul constructeur : la creation et l'edition dupliquaient la
  // structure, les listes Fourriere et les helpers de champ.
  var FOURRIERE_TYPES = ["Parking sauvage", "Pas de titre", "Mauvais titre (sticker ou badge)", "Stationnement genant", "Autre"];
  var FOURRIERE_DECISIONS = ["Remorquage demande", "Sabot pose", "Avertissement", "Annule"];

  // Cles de content_category portees par chaque categorie : au changement de
  // categorie en edition, celles de l'ancienne sont videes (elles restaient
  // attachees a la fiche et s'affichaient sous la nouvelle).
  function catFieldKeys(cat) {
    if (cat === "PCO.Secours" || cat === "PCO.Securite" || cat === "PCO.Technique") {
      return ["sous_classification", "intervenant1", "intervenant2", "service_contacte"];
    }
    if (cat === "PCO.Flux") return ["sous_classification", "moyens_engages_niveau_1", "moyens_engages_niveau_2"];
    if (cat === "PCO.Fourriere") return ["typedemande", "lieu", "detailsvl", "immat", "decision"];
    return ["sous_classification"];
  }

  function addFormField(container, id, label, value, options, required) {
    var grp = mkEl("div", "form-group");
    var lbl = mkEl("label", ""); lbl.textContent = label; lbl.setAttribute("for", id);
    if (required) {
      var star = mkEl("span", ""); star.style.color = "var(--danger,#ef4444)"; star.textContent = " *";
      lbl.appendChild(star);
    }
    grp.appendChild(lbl);
    var el;
    if (options && options.length) {
      el = mkEl("select", "form-input");
      var opt0 = mkEl("option", ""); opt0.value = ""; opt0.textContent = "-- Choisir --";
      el.appendChild(opt0);
      options.forEach(function (o) {
        var opt = mkEl("option", ""); opt.value = o; opt.textContent = o;
        el.appendChild(opt);
      });
      // Valeur courante absente de la liste (ancienne fiche, liste modifiee)
      if (value && options.indexOf(value) === -1) {
        var optC = mkEl("option", ""); optC.value = value; optC.textContent = value;
        el.appendChild(optC);
      }
    } else {
      el = mkEl("input", "form-input"); el.type = "text"; el.placeholder = label;
    }
    el.id = id;
    el.value = value || "";
    grp.appendChild(el);
    container.appendChild(grp);
    return el;
  }

  // hooks.urgency / hooks.comment sont inseres a leur place dans l'ordre
  // propre a la categorie. Retourne { collect(): {cle: valeur}, requiresSous }.
  function buildCategoryFields(container, cat, values, prefix, hooks) {
    values = values || {};
    hooks = hooks || {};
    var subs = extractLabels((pcorgConfig.sous_classifications || {})[cat]);
    var intervList = extractLabels(pcorgConfig.intervenants);
    var serviceList = extractLabels(pcorgConfig.services);
    var vehicleNames = (vehiclesByCategory[cat] || []).map(function (v) { return v.label; });
    var ids = {};

    function field(key, label, options, required) {
      ids[key] = prefix + key;
      addFormField(container, ids[key], label, values[key] || "", options && options.length ? options : null, required);
    }
    function sous() {
      if (subs.length || values.sous_classification) field("sous_classification", "Sous-classification", subs, subs.length > 0);
    }
    function vehicle() {
      if (vehicleNames.length || values.patrouille) field("patrouille", "Vehicule engage", vehicleNames);
    }
    function hook(name) { if (hooks[name]) hooks[name](container); }

    if (cat === "PCO.Secours" || cat === "PCO.Securite" || cat === "PCO.Technique") {
      sous(); hook("urgency"); hook("comment"); vehicle();
      field("intervenant1", "Intervenant 1", intervList);
      field("intervenant2", "Intervenant 2", intervList);
      field("service_contacte", "Service contacte", serviceList);
    } else if (cat === "PCO.Flux") {
      sous(); hook("urgency"); hook("comment"); vehicle();
      field("moyens_engages_niveau_1", "Moyens engages Niv.1", intervList);
      field("moyens_engages_niveau_2", "Moyens engages Niv.2", intervList);
    } else if (cat === "PCO.Fourriere") {
      field("typedemande", "Type de demande", FOURRIERE_TYPES);
      field("lieu", "Lieu");
      field("detailsvl", "Vehicule (marque, couleur, modele)");
      field("immat", "Immatriculation");
      field("decision", "Decision", FOURRIERE_DECISIONS);
      hook("comment"); vehicle();
    } else {
      sous(); hook("urgency"); hook("comment"); vehicle();
    }

    return {
      requiresSous: subs.length > 0,
      collect: function () {
        var out = {};
        Object.keys(ids).forEach(function (k) {
          var el = document.getElementById(ids[k]);
          if (el) out[k] = (el.value || "").trim();
        });
        return out;
      }
    };
  }

  // ── Source (qui a declenche la fiche) : editeur autonome pour l'edition ──
  // Meme modele que la creation (source_type + qui + canal). L'edition
  // n'offrait que l'ancien couple Appelant + Telephone/Radio : modifier la
  // source d'une fiche recente ne changeait rien a l'affichage.
  function buildSourceEditor(parent, cc) {
    cc = cc || {};
    var st = { src: cc.source_type || "", canal: cc.canal || "" };
    if (!st.src && (cc.appelant || cc.telephone || cc.radio)) st.src = "externe";
    if (!st.canal) {
      if (cc.telephone) st.canal = "telephone";
      else if (cc.radio) st.canal = "radio";
      else if (cc.presentiel) st.canal = "presentiel";
      else if (cc.mail) st.canal = "mail";
    }
    var sec = mkEl("div", "pcorg-fiche-section"); sec.textContent = "Source";
    parent.appendChild(sec);
    var tabs = mkEl("div", "pcorg-source-tabs");
    var fields = mkEl("div", "pcorg-source-fields");
    parent.appendChild(tabs);
    parent.appendChild(fields);
    _refreshSourceDatalists();

    function whoValue(src) {
      if (src === "externe") return cc.appelant || "";
      if (src === "operateur") return cc.emetteur_interne || cc.appelant || "";
      if (src === "hierarchie") return cc.donneur_ordre || cc.appelant || "";
      return "";
    }

    function renderTabs() {
      tabs.textContent = "";
      SOURCE_TYPES.forEach(function (s) {
        var b = mkEl("button", "pcorg-source-tab" + (s.id === st.src ? " selected" : ""));
        b.type = "button";
        b.style.setProperty("--src-color", s.color);
        b.appendChild(matIcon(s.icon, "pcorg-source-tab-ico"));
        var l = mkEl("span", "pcorg-source-tab-label"); l.textContent = s.label;
        b.appendChild(l);
        b.title = s.desc;
        b.addEventListener("click", function () { st.src = s.id; renderTabs(); renderFields(); });
        tabs.appendChild(b);
      });
    }

    function renderFields() {
      fields.textContent = "";
      if (!st.src) return;
      if (st.src === "initiative") {
        addFormField(fields, "pcorg-edit-src-origine", "A la suite de (optionnel)", cc.source_origine || "");
        return;
      }
      var labels = { externe: "Appelant", operateur: "Operateur emetteur", hierarchie: "Donneur d'ordre" };
      var who = addFormField(fields, "pcorg-edit-src-who", labels[st.src], whoValue(st.src), null, true);
      who.autocomplete = "off";
      if (st.src === "operateur" || st.src === "hierarchie") {
        attachAutocomplete(who, function () { return _sourceLists[st.src] || []; });
      }
      var grpC = mkEl("div", "form-group pcorg-source-subgrp");
      var lblC = mkEl("label", ""); lblC.textContent = "Canal *";
      grpC.appendChild(lblC);
      var rowC = mkEl("div", "pcorg-canal-row");
      var det = mkEl("div", "pcorg-canal-detail");
      var detInp = mkEl("input", "form-input");
      detInp.id = "pcorg-edit-src-canal-detail";
      detInp.placeholder = "Canal radio (ex: 3, Secu, Maintenance...)";
      detInp.value = cc.radio_canal || (typeof cc.radio === "string" ? cc.radio : "");
      det.appendChild(detInp);
      CANAUX.forEach(function (c) {
        var b = mkEl("button", "pcorg-canal-btn" + (c.id === st.canal ? " selected" : ""));
        b.type = "button";
        b.appendChild(matIcon(c.icon, "pcorg-canal-btn-ico"));
        var lab = mkEl("span", ""); lab.textContent = c.label;
        b.appendChild(lab);
        b.addEventListener("click", function () {
          st.canal = c.id;
          rowC.querySelectorAll(".pcorg-canal-btn").forEach(function (x) { x.classList.toggle("selected", x === b); });
          det.style.display = c.id === "radio" ? "" : "none";
        });
        rowC.appendChild(b);
      });
      det.style.display = st.canal === "radio" ? "" : "none";
      grpC.appendChild(rowC);
      grpC.appendChild(det);
      fields.appendChild(grpC);
    }

    renderTabs();
    renderFields();

    return {
      // {ok, msg, cc} ; une fiche ancienne sans source peut rester sans source
      collect: function () {
        var out = {};
        if (!st.src) return { ok: true, cc: out };
        out.source_type = st.src;
        if (st.src === "initiative") {
          out.source_origine = getVal("pcorg-edit-src-origine");
          out.canal = ""; out.telephone = false; out.radio = false; out.presentiel = false; out.mail = false;
          return { ok: true, cc: out };
        }
        var who = getVal("pcorg-edit-src-who");
        if (!who) return { ok: false, msg: "Indiquez qui est a l'origine de la fiche" };
        if (!st.canal) return { ok: false, msg: "Selectionnez le canal" };
        out.appelant = who;
        if (st.src === "operateur") out.emetteur_interne = who;
        if (st.src === "hierarchie") out.donneur_ordre = who;
        out.canal = st.canal;
        out.telephone = st.canal === "telephone";
        out.presentiel = st.canal === "presentiel";
        out.mail = st.canal === "mail";
        if (st.canal === "radio") {
          var detail = getVal("pcorg-edit-src-canal-detail");
          out.radio = detail || true;
          out.radio_canal = detail;
        } else {
          out.radio = false;
          out.radio_canal = "";
        }
        return { ok: true, cc: out };
      }
    };
  }

  // ── Edit mode on fiche ────────────────────────────────────────────────────
  function renderFicheEdit(d) {
    checkSessionBeforeForm();
    var cc = d.content_category || {};
    var body = document.getElementById("pcorg-fiche-body");
    body.textContent = "";
    // Etat partage par les boutons : l'urgence etait passee PAR VALEUR au
    // constructeur des champs, et le changement n'etait jamais envoye.
    var state = { cat: d.category, urgency: d.niveau_urgence || "", spec: null };

    // Description
    var descSec = mkEl("div", "pcorg-fiche-section"); descSec.textContent = "Description"; body.appendChild(descSec);
    var descInput = mkEl("textarea", "form-input");
    descInput.id = "pcorg-edit-text"; descInput.rows = 2;
    descInput.value = d.text || "";
    body.appendChild(descInput);

    // Categorie
    var catSec = mkEl("div", "pcorg-fiche-section"); catSec.textContent = "Categorie"; body.appendChild(catSec);
    var catContainer = mkEl("div", "pcorg-create-cats");
    var cats = CATEGORY_ORDER.slice();
    if (cats.indexOf(d.category) === -1) cats.push(d.category);
    function paintCats() {
      catContainer.querySelectorAll(".pcorg-create-cat-btn").forEach(function (b) {
        var c = b.getAttribute("data-cat");
        var sel = c === state.cat;
        var s = catStyle(c);
        b.classList.toggle("selected", sel);
        b.style.borderColor = sel ? s.color : "";
        b.style.background = sel ? s.color : "";
        var bico = b.querySelector(".material-symbols-outlined");
        if (bico) bico.style.color = sel ? "#fff" : s.color;
      });
    }
    cats.forEach(function (cat) {
      var s = catStyle(cat);
      var btn = mkEl("button", "pcorg-create-cat-btn");
      btn.type = "button";
      btn.setAttribute("data-cat", cat);
      btn.appendChild(matIcon(s.icon));
      var label = mkEl("span", ""); label.textContent = shortCat(cat); btn.appendChild(label);
      btn.addEventListener("click", function () {
        if (state.cat === cat) return;
        state.cat = cat;
        paintCats();
        var hdr = document.getElementById("pcorg-fiche-header");
        hdr.style.background = s.color;
        hdr.querySelector(".pcorg-fiche-icon").textContent = s.icon;
        hdr.querySelector(".pcorg-fiche-cat").textContent = shortCat(cat);
        rebuildSpecific();
      });
      catContainer.appendChild(btn);
    });
    body.appendChild(catContainer);
    paintCats();

    var srcEditor = buildSourceEditor(body, cc);

    var detSec = mkEl("div", "pcorg-fiche-section"); detSec.textContent = "Details"; body.appendChild(detSec);
    var specContainer = mkEl("div", "");
    body.appendChild(specContainer);

    function appendEditUrgency(container) {
      if (!urgencyEnabledFor(state.cat, state.urgency)) return;
      var grp = mkEl("div", "form-group");
      var lbl = mkEl("label", ""); lbl.textContent = "Niveau d'urgence";
      grp.appendChild(lbl);
      var row = mkEl("div", "pcorg-create-cats");
      function paint() {
        row.textContent = "";
        [""].concat(URGENCY_LEVELS).forEach(function (lvl) {
          var sel = state.urgency === lvl;
          var c = lvl ? URGENCY_COLORS[lvl] : "var(--muted)";
          var b = mkEl("button", "pcorg-create-cat-btn pcorg-urg-choice" + (sel ? " selected" : ""));
          b.type = "button";
          if (sel) { b.style.borderColor = c; b.style.background = c; }
          if (lvl) {
            var dot = mkEl("span", "pcorg-urg-dot"); dot.style.background = c;
            b.appendChild(dot);
          }
          var t = mkEl("span", ""); t.textContent = lvl ? urgencyLabel(state.cat, lvl) : "Aucun";
          b.appendChild(t);
          b.addEventListener("click", function () { state.urgency = lvl; paint(); });
          row.appendChild(b);
        });
      }
      paint();
      grp.appendChild(row);
      container.appendChild(grp);
    }

    var commentDraft = "";
    function appendEditComment(container) {
      var grp = mkEl("div", "form-group");
      var lbl = mkEl("label", ""); lbl.setAttribute("for", "pcorg-edit-comment");
      lbl.textContent = "Commentaire (optionnel)";
      grp.appendChild(lbl);
      var ta = mkEl("textarea", "form-input");
      ta.id = "pcorg-edit-comment"; ta.rows = 2;
      ta.placeholder = "Action realisee, motif de la modification... (les champs modifies sont traces automatiquement)";
      ta.value = commentDraft;
      ta.addEventListener("input", function () { commentDraft = ta.value; });
      grp.appendChild(ta);
      container.appendChild(grp);
    }

    function rebuildSpecific() {
      specContainer.textContent = "";
      state.spec = buildCategoryFields(specContainer, state.cat, cc, "pcorg-edit-",
        { urgency: appendEditUrgency, comment: appendEditComment });
    }
    rebuildSpecific();

    // Save / Cancel
    var editActions = mkEl("div", "pcorg-fiche-actions");
    var btnCancel = mkEl("button", "");
    btnCancel.appendChild(matIcon("close"));
    btnCancel.appendChild(document.createTextNode(" Annuler"));
    btnCancel.addEventListener("click", function () { openDetailModal(d.id, false); });
    editActions.appendChild(btnCancel);

    var btnSave = mkEl("button", "pcorg-btn-primary");
    btnSave.style.cssText = "background:var(--brand);color:#fff;border-color:var(--brand)";
    btnSave.appendChild(matIcon("save"));
    btnSave.appendChild(document.createTextNode(" Enregistrer"));
    btnSave.addEventListener("click", function () {
      submitFicheEdit(d, state, srcEditor, btnSave);
    });
    editActions.appendChild(btnSave);
    body.appendChild(editActions);
    setTimeout(function () { descInput.focus(); }, 50);
  }

  function submitFicheEdit(d, state, srcEditor, btnSave) {
    var text = getVal("pcorg-edit-text");
    if (!text) { showToast("warning", "La description est obligatoire"); return; }
    var src = srcEditor.collect();
    if (!src.ok) { showToast("warning", src.msg); return; }
    var spec = state.spec.collect();
    if (state.spec.requiresSous && !spec.sous_classification) {
      showToast("warning", "La sous-classification est obligatoire");
      return;
    }
    var cc = {};
    Object.keys(src.cc).forEach(function (k) { cc[k] = src.cc[k]; });
    Object.keys(spec).forEach(function (k) { cc[k] = spec[k]; });

    var payload = {
      text: text,
      category: state.cat,
      niveau_urgence: urgencyEnabledFor(state.cat, state.urgency) ? (state.urgency || null) : (d.niveau_urgence || null),
      content_category: cc,
      comment: getVal("pcorg-edit-comment")
    };
    if (state.cat !== d.category) {
      var keep = catFieldKeys(state.cat);
      payload.content_category_remove = catFieldKeys(d.category).filter(function (k) {
        return keep.indexOf(k) === -1;
      });
    }

    btnSave.disabled = true;
    apiCall("PUT", "/api/pcorg/update/" + encodeURIComponent(d.id), payload).then(function (r) {
      btnSave.disabled = false;
      if (!r.ok) { showToast("error", r.error || "Erreur"); return; }
      showToast("success", r.unchanged ? "Aucune modification" : "Fiche mise a jour");
      refresh();
      openDetailModal(d.id, false);
    });
  }

  function addField(parent, label, value) {
    if (!value) return;
    var row = mkEl("div", "pcorg-fiche-field");
    var lbl = mkEl("span", "pcorg-fiche-label");
    lbl.textContent = label;
    row.appendChild(lbl);
    var val = mkEl("span", "pcorg-fiche-value");
    val.textContent = value;
    row.appendChild(val);
    parent.appendChild(row);
  }

  // Champ "Ouverture" enrichi : affiche un badge si antidate/programme + crayon edit
  function addInterventionTsField(parent, d, isClosed) {
    var ts = new Date(d.ts);
    var createdAt = d.created_at ? new Date(d.created_at) : null;
    var antidated = createdAt && Math.abs(ts.getTime() - createdAt.getTime()) >= 60000;
    var row = mkEl("div", "pcorg-fiche-field pcorg-fiche-field-ts");
    var lbl = mkEl("span", "pcorg-fiche-label");
    lbl.textContent = "Heure intervention";
    row.appendChild(lbl);
    var valWrap = mkEl("span", "pcorg-fiche-value pcorg-fiche-ts-value");
    var val = mkEl("span", "");
    val.textContent = ts.toLocaleString("fr-FR");
    valWrap.appendChild(val);
    if (antidated) {
      var info = formatTsChipLabel(ts);
      var badge = mkEl("span", "pcorg-fiche-ts-badge pcorg-ts-" + info.state);
      var bIco = matIcon(info.state === "future" ? "event_upcoming" : "history");
      badge.appendChild(bIco);
      var bTxt = mkEl("span", "");
      bTxt.textContent = info.state === "future" ? "Programmee" : "Antidatee";
      badge.appendChild(bTxt);
      badge.title = "Saisie le " + createdAt.toLocaleString("fr-FR");
      valWrap.appendChild(badge);
    }
    // Bouton edit (crayon) — uniquement si fiche ouverte
    if (!isClosed && d.status_code !== 10) {
      var editBtn = mkEl("button", "pcorg-fiche-ts-edit");
      editBtn.type = "button";
      editBtn.title = "Modifier l'heure d'intervention";
      editBtn.appendChild(matIcon("edit"));
      editBtn.addEventListener("click", function (e) {
        e.preventDefault(); e.stopPropagation();
        if (_tsPopover) { closeTsPopover(); return; }
        openTsPopover(editBtn, ts, function (newDate) {
          var dt = newDate || new Date();
          // datetime-local sans tz : le backend assume Europe/Paris si tzinfo absent
          var iso = _toLocalInputValue(dt) + ":00";
          apiCall("PUT", "/api/pcorg/update/" + encodeURIComponent(d.id), { intervention_ts: iso })
            .then(function (r) {
              if (r && r.ok) {
                showToast("success", "Heure d'intervention mise a jour");
                reloadFiche(d.id);
                refresh();
              } else {
                showToast("error", (r && r.error) || "Erreur");
              }
            });
        });
      });
      valWrap.appendChild(editBtn);
    }
    row.appendChild(valWrap);
    parent.appendChild(row);
  }

  // Affichage source (modale detail) — gere fiches nouvelles ET legacy
  function addSourceField(parent, cc) {
    cc = cc || {};
    var srcType = cc.source_type || "";
    var who = "", channel = "";

    if (srcType === "initiative") {
      who = "Initiative personnelle";
      if (cc.source_origine) who += " (" + cc.source_origine + ")";
    } else if (srcType === "externe") {
      who = cc.appelant || "—";
    } else if (srcType === "operateur") {
      who = cc.emetteur_interne || cc.appelant || "—";
    } else if (srcType === "hierarchie") {
      who = cc.donneur_ordre || cc.appelant || "—";
    } else {
      // Fallback pour fiches legacy : appelant + telephone/radio
      if (!cc.appelant && !cc.telephone && !cc.radio) return;
      srcType = "externe";
      who = cc.appelant || "—";
    }

    if (srcType !== "initiative") {
      var c = cc.canal || "";
      if (!c) {
        // Fallback canal depuis flags legacy
        if (cc.telephone) c = "telephone";
        else if (cc.radio) c = "radio";
        else if (cc.presentiel) c = "presentiel";
        else if (cc.mail) c = "mail";
      }
      if (c) {
        var meta = CANAL_BY_ID[c];
        channel = meta ? meta.label : c;
        if (c === "radio") {
          var canalDet = cc.radio_canal || (typeof cc.radio === "string" ? cc.radio : "");
          if (canalDet) channel += " · canal " + canalDet;
        }
      }
    }

    var srcMeta = SOURCE_BY_ID[srcType] || SOURCE_BY_ID.externe;
    var row = mkEl("div", "pcorg-fiche-field pcorg-fiche-field-source");
    var lbl = mkEl("span", "pcorg-fiche-label");
    lbl.textContent = "Source";
    row.appendChild(lbl);
    var val = mkEl("span", "pcorg-fiche-value pcorg-fiche-source-value");
    var chip = mkEl("span", "pcorg-fiche-source-chip");
    chip.style.setProperty("--src-color", srcMeta.color);
    chip.title = srcMeta.desc;
    var ico = matIcon(srcMeta.icon, "pcorg-fiche-source-ico");
    chip.appendChild(ico);
    var lblTxt = mkEl("span", "pcorg-fiche-source-type");
    lblTxt.textContent = srcMeta.label;
    chip.appendChild(lblTxt);
    val.appendChild(chip);
    var whoEl = mkEl("span", "pcorg-fiche-source-who");
    whoEl.textContent = who;
    val.appendChild(whoEl);
    if (channel) {
      var sep = mkEl("span", "pcorg-fiche-source-sep");
      sep.textContent = "·";
      val.appendChild(sep);
      var cIco = matIcon(srcType === "initiative" ? "visibility"
        : (CANAL_BY_ID[(cc.canal || "")] || {}).icon || "alt_route",
        "pcorg-fiche-source-canal-ico");
      val.appendChild(cIco);
      var chEl = mkEl("span", "pcorg-fiche-source-channel");
      chEl.textContent = channel;
      val.appendChild(chEl);
    }
    row.appendChild(val);
    parent.appendChild(row);
  }

  function buildSpecificFields(category, cc) {
    var fields = [];
    var cat = category || "";
    if (cat === "PCO.Fourriere") {
      if (cc.detailsvl) fields.push(["Vehicule", cc.detailsvl]);
      if (cc.immat) fields.push(["Immatriculation", cc.immat]);
      if (cc.lieu) fields.push(["Lieu", cc.lieu]);
      if (cc.typedemande) fields.push(["Type demande", cc.typedemande]);
      if (cc.decision) fields.push(["Decision", cc.decision]);
      if (cc.dhenlevement) fields.push(["Enlevement", cc.dhenlevement]);
      if (cc.paiement) fields.push(["Paiement", cc.paiement]);
      if (cc.precurseur) fields.push(["Precurseur", cc.precurseur]);
    } else if (cat === "PCO.Flux") {
      if (cc.moyens_engages_niveau_1) fields.push(["Moyens Niv.1", cc.moyens_engages_niveau_1]);
      if (cc.moyens_engages_niveau_2) fields.push(["Moyens Niv.2", cc.moyens_engages_niveau_2]);
    } else if (cat === "PCO.Secours" || cat === "PCO.Securite" || cat === "PCO.Technique") {
      var intervs = [];
      for (var i = 1; i <= 5; i++) { if (cc["intervenant" + i]) intervs.push(cc["intervenant" + i]); }
      if (intervs.length) fields.push(["Intervenants", intervs.join(", ")]);
      if (cc.service_contacte) fields.push(["Service contacte", cc.service_contacte]);
    } else if (cat === "PCO.Information" || cat === "PCO.MainCourante") {
      if (cc.texte) fields.push(["Texte", cc.texte]);
      if (cc.alerte) fields.push(["Alerte", "Oui"]);
    }
    return fields;
  }

  function initMiniMap(lat, lon, st) {
    destroyMiniMap();
    var container = document.getElementById("pcorg-minimap-container");
    if (!container) return;
    detailMiniMap = L.map(container, {
      center: [lat, lon], zoom: 16, zoomControl: false,
      attributionControl: false, dragging: false, scrollWheelZoom: false,
      doubleClickZoom: false, touchZoom: false
    });
    // Vignette d'apercu volontairement figee : pas de selecteur de couches ici.
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxNativeZoom: 19, maxZoom: 22
    }).addTo(detailMiniMap);
    var pinHtml ="<div class='pcorg-pin' style='background:" + st.color + "'>" +
      "<span class='material-symbols-outlined'>" + st.icon + "</span></div>";
    L.marker([lat, lon], {
      icon: L.divIcon({ className: "", html: pinHtml, iconSize: [36, 36], iconAnchor: [18, 36] })
    }).addTo(detailMiniMap);
    setTimeout(function () { detailMiniMap.invalidateSize(); }, 150);
  }

  // ── Change urgency level ────────────────────────────────────────────────────
  function setUrgencyLevel(id, level, category) {
    apiCall("POST", "/api/pcorg/set-urgency/" + encodeURIComponent(id), { niveau_urgence: level || null })
      .then(function (r) {
        if (r.ok) {
          var label = level ? urgencyLabel(category, level) : "Aucun";
          showToast("success", "Urgence \u2192 " + label);
          refresh();
          // Rafraichit la fiche seulement si elle est affichee : depuis la
          // popup de la carte, on n'ouvre plus la grande fiche en prime
          reloadFiche(id);
        } else {
          showToast("error", r.error || "Erreur");
        }
      });
  }

  // Droits de groupe (config > groupes) : "Fiches en lecture seule" retire la
  // modification ; "Responsable de service" donne la cloture de ses categories.
  // Le serveur applique les memes regles (403 sinon) : ceci n'est que l'affichage.
  // `fiche` (facultatif) : une fiche creee par l'utilisateur reste modifiable
  // meme en lecture seule (meme regle que _user_can_edit_fiche cote serveur).
  function canEditFiche(fiche) {
    if (window.__userCanEditFiche !== false || window.__userIsAdmin) return true;
    var me = String(window.__userEmail || "").trim().toLowerCase();
    var creator = String((fiche && fiche.operator_id_create) || "").trim().toLowerCase();
    return !!me && creator === me;
  }
  function canCloseFicheCat(category) {
    return !!(window.__userCanCloseFiche || window.__userIsAdmin
      || (window.__userDispatchCategories || []).indexOf(category) >= 0);
  }

  function buildUrgencyButtons(currentLevel, category, ficheId, compact, fiche) {
    var container = mkEl("div", "pcorg-urgency-selector" + (compact ? " compact" : ""));
    var editable = canEditFiche(fiche);
    if (!editable) container.title = "Lecture seule : l'urgence ne peut pas etre modifiee";
    var levels = [
      { code: "EU", color: URGENCY_COLORS.EU },
      { code: "UA", color: URGENCY_COLORS.UA },
      { code: "UR", color: URGENCY_COLORS.UR },
      { code: "IMP", color: URGENCY_COLORS.IMP },
      { code: null, color: "#94a3b8" }
    ];
    levels.forEach(function (lvl) {
      var isActive = (currentLevel || null) === lvl.code;
      var btn = mkEl("button", "pcorg-urg-btn" + (isActive ? " active" : ""));
      btn.type = "button";
      btn.style.setProperty("--urg-color", lvl.color);
      if (isActive) { btn.style.background = lvl.color; btn.style.color = "#fff"; btn.style.borderColor = lvl.color; }
      var dot = mkEl("span", "pcorg-urg-dot");
      dot.style.background = lvl.color;
      btn.appendChild(dot);
      var text = mkEl("span", "");
      text.textContent = lvl.code ? (compact ? lvl.code : urgencyLabel(category, lvl.code)) : (compact ? "\u2013" : "Aucun");
      btn.appendChild(text);
      if (!editable) btn.disabled = true;
      btn.addEventListener("click", function () {
        if (isActive || !editable) return;
        setUrgencyLevel(ficheId, lvl.code, category);
      });
      container.appendChild(btn);
    });
    return container;
  }

  // ── Close intervention ─────────────────────────────────────────────────────
  function removePin(id) {
    var map = getMap();
    if (map) map.closePopup();
    if (pcorgMarkers[id] && pcorgMapLayer) {
      pcorgMapLayer.removeLayer(pcorgMarkers[id]);
      delete pcorgMarkers[id];
    }
  }

  // Cloture avec motif (facultatif), consigne dans la meme entree que le
  // changement de statut et affiche en texte normal sous la pastille
  function closeIntervention(id) {
    showPromptToast("Clore cette intervention ? Motif / bilan (facultatif)",
      { okLabel: "Clore", cancelLabel: "Annuler", type: "warning" }).then(function (motif) {
      if (motif === null) return;
      apiCall("POST", "/api/pcorg/close/" + encodeURIComponent(id), { comment: (motif || "").trim() })
        .then(function (r) {
          if (r.ok) {
            hideFiche();
            removePin(id);
            showToast("success", "Intervention cloturee");
            refresh();
          } else {
            showToast("error", r.error || "Erreur");
          }
        });
    });
  }

  function reopenIntervention(id) {
    showPromptToast("Rouvrir cette intervention : motif (obligatoire)",
      { okLabel: "Rouvrir", cancelLabel: "Annuler", type: "warning" }).then(function (motif) {
      if (motif === null) return;
      motif = (motif || "").trim();
      if (!motif) { showToast("warning", "Le motif de reouverture est obligatoire"); return; }
      apiCall("POST", "/api/pcorg/reopen/" + encodeURIComponent(id), { comment: motif })
        .then(function (r) {
          if (r.ok) {
            showToast("success", "Intervention rouverte");
            refresh();
            openDetailModal(id, false);
          } else {
            showToast("error", r.error || "Erreur");
          }
        });
    });
  }

  function deleteIntervention(id) {
    apiCall("DELETE", "/api/pcorg/delete/" + encodeURIComponent(id))
      .then(function (r) {
        if (r.ok) {
          hideFiche();
          removePin(id);
          showToast("success", "Intervention supprimee");
          refresh();
        } else {
          showToast("error", r.error || "Erreur");
        }
      });
  }

  // ── Badge ──────────────────────────────────────────────────────────────────
  function updateBadge(count) {
    if (!badge) return;
    if (count > 0) {
      badge.textContent = count;
      badge.style.display = "";
    } else {
      badge.style.display = "none";
    }
  }

  // ── Map pins ───────────────────────────────────────────────────────────────
  var pendingPins = null;
  var ignoreAllControl = null;

  function addIgnoreAllControl(map) {
    if (ignoreAllControl) return;
    var IgnoreAll = L.Control.extend({
      options: { position: "topleft" },
      onAdd: function () {
        var container = L.DomUtil.create("div", "pcorg-ignore-all-ctrl");
        var btn = L.DomUtil.create("a", "pcorg-ignore-all-btn", container);
        btn.href = "#";
        btn.title = "Ignorer toutes les alertes interventions";
        btn.setAttribute("role", "button");
        var ico = document.createElement("span");
        ico.className = "material-symbols-outlined";
        ico.textContent = "notifications_off";
        ico.style.fontSize = "18px";
        ico.style.lineHeight = "30px";
        btn.appendChild(ico);
        L.DomEvent.disableClickPropagation(container);
        L.DomEvent.on(btn, "click", function (e) {
          L.DomEvent.preventDefault(e);
          if (lastData && lastData.open) {
            ackAllPins(lastData.open);
          }
          refresh();
          showToast("info", "Toutes les alertes ignorees");
        });
        return container;
      }
    });
    ignoreAllControl = new IgnoreAll();
    map.addControl(ignoreAllControl);
  }

  function getMap() {
    if (window.CockpitMapView && window.CockpitMapView.getMap) {
      return window.CockpitMapView.getMap();
    }
    return null;
  }

  function vehicleTipHtml(patrouille) {
    var devInfo = window.getAnolocDeviceByLabel ? window.getAnolocDeviceByLabel(patrouille) : null;
    var devSt = devInfo ? _resolveDeviceStatus(devInfo.device) : null;
    var color = devSt ? devSt.color : "#94a3b8";
    var label = devSt ? devSt.label : "";
    // Nom echappe : il vient de SQL ou d'une saisie libre
    return "<span class='veh-tip-name'>" + escHtml(patrouille) + "</span>"
      + (label ? "<span class='veh-tip-status' style='color:" + color + "'><span class='veh-tip-dot' style='background:" + color + "'></span>" + escHtml(label) + "</span>" : "");
  }

  function updateVehicleTooltips() {
    // Met a jour les tooltips des pins pcorg avec les statuts anoloc frais
    if (!pcorgMarkers) return;
    Object.keys(pcorgMarkers).forEach(function (id) {
      var marker = pcorgMarkers[id];
      if (!marker || !marker._pcorgPatrouille) return;
      if (marker.getTooltip()) marker.getTooltip().setContent(vehicleTipHtml(marker._pcorgPatrouille));
    });
  }

  // ── Filtre de la couche carte (par poste, localStorage) ─────────────────
  // Avant : la couche ne pouvait ni etre masquee ni filtree.
  var MAP_FILTER_KEY = "pcorg-map-filter";
  var mapFilter = loadMapFilter();
  var mapFilterControl = null;
  var lastPinItems = [];

  function loadMapFilter() {
    try {
      var f = JSON.parse(localStorage.getItem(MAP_FILTER_KEY));
      if (f && typeof f === "object") {
        return { hidden: !!f.hidden, cats: f.cats || {}, urgentOnly: !!f.urgentOnly };
      }
    } catch (e) {}
    return { hidden: false, cats: {}, urgentOnly: false };
  }

  function saveMapFilter() {
    try { localStorage.setItem(MAP_FILTER_KEY, JSON.stringify(mapFilter)); } catch (e) {}
  }

  function mapFilterActive() {
    if (mapFilter.hidden || mapFilter.urgentOnly) return true;
    return Object.keys(mapFilter.cats).some(function (c) { return mapFilter.cats[c] === false; });
  }

  function passesMapFilter(item) {
    if (mapFilter.cats[item.category] === false) return false;
    if (mapFilter.urgentOnly && item.niveau_urgence !== "EU" && item.niveau_urgence !== "UA") return false;
    return true;
  }

  function applyLayerVisibility(map) {
    if (!pcorgMapLayer || !map) return;
    if (mapFilter.hidden) {
      if (map.hasLayer(pcorgMapLayer)) map.removeLayer(pcorgMapLayer);
    } else if (!map.hasLayer(pcorgMapLayer)) {
      pcorgMapLayer.addTo(map);
    }
  }

  function addMapFilterControl(map) {
    if (mapFilterControl) return;
    var Ctl = L.Control.extend({
      options: { position: "topleft" },
      onAdd: function () {
        var container = L.DomUtil.create("div", "pcorg-map-filter-ctrl");
        var btn = L.DomUtil.create("a", "pcorg-map-filter-btn", container);
        btn.href = "#";
        btn.title = "Fiches main courante sur la carte";
        btn.setAttribute("role", "button");
        btn.appendChild(matIcon("filter_alt"));
        var panel = L.DomUtil.create("div", "pcorg-map-filter-panel", container);
        panel.style.display = "none";

        function paintBtn() { container.classList.toggle("active", mapFilterActive()); }

        function row(label, checked, onChange, color) {
          var r = mkEl("label", "pcorg-map-filter-row");
          var cb = mkEl("input", ""); cb.type = "checkbox"; cb.checked = checked;
          cb.addEventListener("change", function () { onChange(cb.checked); });
          r.appendChild(cb);
          if (color) {
            var dot = mkEl("span", "pcorg-map-filter-dot"); dot.style.background = color;
            r.appendChild(dot);
          }
          var t = mkEl("span", ""); t.textContent = label;
          r.appendChild(t);
          panel.appendChild(r);
        }

        function renderPanel() {
          panel.textContent = "";
          var title = mkEl("div", "pcorg-map-filter-title"); title.textContent = "Main courante";
          panel.appendChild(title);
          function changed() { saveMapFilter(); paintBtn(); applyLayerVisibility(map); updateMapPins(lastPinItems); }
          row("Afficher les fiches", !mapFilter.hidden, function (v) { mapFilter.hidden = !v; changed(); });
          row("Urgences EU / UA seulement", mapFilter.urgentOnly, function (v) { mapFilter.urgentOnly = v; changed(); });
          var sep = mkEl("div", "pcorg-map-filter-sep"); panel.appendChild(sep);
          CATEGORY_ORDER.forEach(function (cat) {
            row(shortCat(cat), mapFilter.cats[cat] !== false, function (v) {
              if (v) delete mapFilter.cats[cat]; else mapFilter.cats[cat] = false;
              changed();
            }, catStyle(cat).color);
          });
        }

        L.DomEvent.disableClickPropagation(container);
        L.DomEvent.disableScrollPropagation(container);
        L.DomEvent.on(btn, "click", function (e) {
          L.DomEvent.preventDefault(e);
          var open = panel.style.display === "none";
          if (open) renderPanel();
          panel.style.display = open ? "" : "none";
        });
        paintBtn();
        return container;
      }
    });
    mapFilterControl = new Ctl();
    map.addControl(mapFilterControl);
  }

  // Signature d'affichage : un pin n'est recree que si ce qu'il montre a
  // change. Avant, clearLayers() toutes les 60 s fermait la popup ouverte et
  // perdait la chronologie chargee.
  function pinSignature(item) {
    return [item.lat, item.lon, item.category, item.niveau_urgence || "", item.patrouille || "",
      item.text || "", item.sous_classification || "", item.area_desc || "", item.operator || "",
      item.bounce_rev || 0, item.ts || "", (item.dispatch && item.dispatch.state) || ""].join("|");
  }

  function updateMapPins(openItems) {
    var map = getMap();
    if (!map) {
      pendingPins = openItems;
      return;
    }
    pendingPins = null;
    lastPinItems = openItems || [];

    if (!pcorgMapLayer) pcorgMapLayer = L.layerGroup();
    applyLayerVisibility(map);
    addIgnoreAllControl(map);
    addMapFilterControl(map);

    var seen = {};
    lastPinItems.forEach(function (item) {
      if (item.lat == null || item.lon == null || !passesMapFilter(item)) return;
      seen[item.id] = true;
      var sig = pinSignature(item);
      var existing = pcorgMarkers[item.id];
      if (existing && existing._pcorgSig === sig) {
        var el = existing.getElement && existing.getElement();
        if (el) el.classList.toggle("pcorg-pin-bounce", shouldBounce(item));
        return;
      }
      var wasOpen = existing && existing.isPopupOpen && existing.isPopupOpen();
      if (existing) pcorgMapLayer.removeLayer(existing);
      var marker = buildPinMarker(item);
      marker._pcorgSig = sig;
      marker.addTo(pcorgMapLayer);
      pcorgMarkers[item.id] = marker;
      if (wasOpen && !mapFilter.hidden) marker.openPopup();
    });
    Object.keys(pcorgMarkers).forEach(function (id) {
      if (!seen[id]) {
        pcorgMapLayer.removeLayer(pcorgMarkers[id]);
        delete pcorgMarkers[id];
      }
    });
  }

  function buildPinMarker(item) {
    var st = catStyle(item.category);

    var pinOuter = document.createElement("div");
    pinOuter.style.position = "relative";

    var pulse = mkEl("div", "pcorg-pin-pulse");
    pulse.style.borderColor = st.color;
    pinOuter.appendChild(pulse);

    var pin = mkEl("div", "pcorg-pin");
    pin.style.background = st.color;
    pin.appendChild(matIcon(st.icon));
    pinOuter.appendChild(pin);

    // Urgence visible sur le pin (avant : seulement dans la popup)
    if (item.niveau_urgence && URGENCY_COLORS[item.niveau_urgence]) {
      var urg = mkEl("span", "pcorg-pin-urg pcorg-pin-urg-" + item.niveau_urgence);
      urg.style.background = URGENCY_COLORS[item.niveau_urgence];
      urg.textContent = item.niveau_urgence;
      pinOuter.appendChild(urg);
    }

    // Dispatch automatique en cours (proposition) ou en file du service
    var pinDisp = item.status_code !== 10 && item.dispatch ? item.dispatch.state : null;
    if (pinDisp === "proposing" || pinDisp === "queued") {
      pinOuter.appendChild(matIcon(pinDisp === "proposing" ? "hourglass_top" : "pending_actions",
        "pcorg-pin-disp pcorg-pin-disp-" + pinDisp));
    }

    var iconCls = "pcorg-pin-icon" + (shouldBounce(item) ? " pcorg-pin-bounce" : "")
      + (item.id === activeFicheId ? " pcorg-pin-active" : "");
    var icon = L.divIcon({
      className: iconCls,
      html: pinOuter.outerHTML,
      iconSize: [36, 36],
      iconAnchor: [18, 36],
      popupAnchor: [0, -38]
    });

    // Build popup
    var popupDiv = mkEl("div", "pcorg-popup");

    // Header bar
    var popHeader = mkEl("div", "pcorg-popup-header");
    popHeader.style.background = st.color;
    popHeader.appendChild(matIcon(st.icon, "pcorg-popup-icon"));
    var popCat = mkEl("span", "pcorg-popup-cat");
    popCat.textContent = shortCat(item.category);
    popHeader.appendChild(popCat);
    if (item.status_code !== 10) {
      var popStatus = mkEl("span", "pcorg-popup-status");
      popStatus.textContent = "EN COURS";
      popHeader.appendChild(popStatus);
    }
    if (item.niveau_urgence) {
      var popUrg = mkEl("span", "pcorg-popup-urgency");
      popUrg.textContent = urgencyLabel(item.category, item.niveau_urgence);
      popHeader.appendChild(popUrg);
    }
    popupDiv.appendChild(popHeader);

    var popBody = mkEl("div", "pcorg-popup-body");

    if (item.sous_classification) {
      var scLine = mkEl("div", "pcorg-popup-subcat");
      scLine.textContent = item.sous_classification;
      popBody.appendChild(scLine);
    }

    if (item.text) {
      var descLine = mkEl("div", "pcorg-popup-desc");
      descLine.textContent = item.text;
      popBody.appendChild(descLine);
    }

    // Vehicule engage (badge prominent with status)
    if (item.patrouille) {
      var popDevInfo = window.getAnolocDeviceByLabel ? window.getAnolocDeviceByLabel(item.patrouille) : null;
      var popDevSt = popDevInfo ? _resolveDeviceStatus(popDevInfo.device) : null;
      var vehBanner = mkEl("div", "pcorg-popup-vehicle");
      var vehLeft = mkEl("div", "pcorg-popup-vehicle-left");
      vehLeft.appendChild(matIcon("directions_car", "pcorg-popup-vehicle-icon"));
      var vehNameWrap = mkEl("div", "");
      var vehLabel = mkEl("span", "pcorg-popup-vehicle-label");
      vehLabel.textContent = "Element engage";
      vehNameWrap.appendChild(vehLabel);
      var vehName = mkEl("strong", "pcorg-popup-vehicle-name");
      vehName.textContent = item.patrouille;
      vehNameWrap.appendChild(vehName);
      vehLeft.appendChild(vehNameWrap);
      vehBanner.appendChild(vehLeft);
      if (popDevSt) {
        var vehStatusBadge = mkEl("span", "pcorg-popup-vehicle-status");
        var vehStDot = mkEl("span", "pcorg-popup-vehicle-dot");
        vehStDot.style.background = popDevSt.color;
        vehStatusBadge.appendChild(vehStDot);
        vehStatusBadge.appendChild(document.createTextNode(popDevSt.label));
        vehBanner.appendChild(vehStatusBadge);
      }
      popBody.appendChild(vehBanner);
    }

    // Info fields
    var elapsedMs = item.ts ? Date.now() - new Date(item.ts).getTime() : 0;
    var isOld = elapsedMs > 3600000; // > 1h

    var popFields = mkEl("div", "pcorg-popup-fields");
    if (truncZone(item.area_desc)) {
      var zField = mkEl("div", "pcorg-popup-field");
      zField.appendChild(matIcon("location_on", "pcorg-popup-fi"));
      var zVal = mkEl("span", ""); zVal.textContent = truncZone(item.area_desc);
      zField.appendChild(zVal);
      popFields.appendChild(zField);
    }
    var opField = mkEl("div", "pcorg-popup-field");
    opField.appendChild(matIcon("person", "pcorg-popup-fi"));
    var opVal = mkEl("span", "pcorg-popup-op-val"); opVal.textContent = operatorLabel(item.operator) || "?";
    opField.appendChild(opVal);
    popFields.appendChild(opField);

    var tsField = mkEl("div", "pcorg-popup-field");
    tsField.appendChild(matIcon("event", "pcorg-popup-fi"));
    var tsVal = mkEl("span", "");
    tsVal.textContent = item.ts ? fmtDayTime(item.ts, true) : "?";
    tsField.appendChild(tsVal);
    popFields.appendChild(tsField);

    var durField = mkEl("div", "pcorg-popup-field");
    durField.appendChild(matIcon("timer", "pcorg-popup-fi"));
    var durVal = mkEl("span", isOld ? "pcorg-popup-old" : "");
    durVal.textContent = timeAgo(item.ts);
    durField.appendChild(durVal);
    popFields.appendChild(durField);

    popBody.appendChild(popFields);

    // Details et chronologie charges a l'ouverture
    var specDiv = mkEl("div", "pcorg-popup-spec");
    popBody.appendChild(specDiv);
    var chronoDiv = mkEl("div", "pcorg-popup-chrono");
    chronoDiv.style.setProperty("--cat-color", st.color);
    var chronoLoading = mkEl("div", "pcorg-popup-chrono-loading");
    chronoLoading.textContent = "...";
    chronoDiv.appendChild(chronoLoading);
    popBody.appendChild(chronoDiv);

    // Urgence (compact) : memes categories que la fiche
    if (item.status_code !== 10 && urgencyEnabledFor(item.category, item.niveau_urgence)) {
      popBody.appendChild(buildUrgencyButtons(item.niveau_urgence, item.category, item.id, true, item));
    }

    var popBtns = mkEl("div", "pcorg-popup-btns");
    var popBtn = mkEl("button", "pcorg-popup-btn");
    popBtn.appendChild(matIcon("open_in_new"));
    popBtn.appendChild(document.createTextNode(" Ouvrir la fiche"));
    popBtn.addEventListener("click", function () { openDetailModal(item.id, false); });
    popBtns.appendChild(popBtn);

    if (item.status_code !== 10 && canCloseFicheCat(item.category)) {
      var closePopBtn = mkEl("button", "pcorg-popup-btn pcorg-popup-btn-danger");
      closePopBtn.appendChild(matIcon("check_circle"));
      closePopBtn.appendChild(document.createTextNode(" Clore"));
      closePopBtn.addEventListener("click", function () { closeIntervention(item.id); });
      popBtns.appendChild(closePopBtn);
    }
    popBody.appendChild(popBtns);
    popupDiv.appendChild(popBody);

    var marker = L.marker([item.lat, item.lon], { icon: icon, bounceOnAdd: false })
      .bindPopup(popupDiv, { className: "pcorg-popup-wrap", maxWidth: 440, minWidth: 380 });

    // Tooltip vehicule engage (permanent, a droite du pin)
    marker._pcorgPatrouille = item.patrouille || null;
    if (item.patrouille) {
      marker.bindTooltip(vehicleTipHtml(item.patrouille), {
        permanent: true,
        direction: "right",
        offset: [12, -18],
        className: "pcorg-vehicle-tooltip",
      });
    }

    // Pan map to center pin slightly below middle (room for popup above)
    marker.on("click", function () {
      var map = getMap();
      if (!map) return;
      var px = map.latLngToContainerPoint(marker.getLatLng());
      var offsetY = map.getSize().y * 0.25; // shift pin 25% below center
      map.panTo(map.containerPointToLatLng([px.x, px.y - offsetY]), { animate: true, duration: 0.3 });
    });

    marker.on("popupclose", function () {
      if (activeFicheId === item.id && detailOpenId !== item.id) setActiveFiche(null);
    });

    // Lazy load details + chronology on popup open + ack bounce
    marker.on("popupopen", function () {
      ackPin(item.id, item.bounce_rev || 0);
      var el = marker.getElement();
      if (el) el.classList.remove("pcorg-pin-bounce");
      setActiveFiche(item.id);
      if (chronoDiv._loaded) return;
      chronoDiv._loaded = true;
      fetch("/api/pcorg/detail/" + encodeURIComponent(item.id))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.error) { chronoDiv.textContent = ""; return; }
          if (d.operator_group) {
            opVal.textContent = (operatorLabel(d.operator) || "?") + " (" + d.operator_group + ")";
          }
          var specs = buildSpecificFields(d.category, d.content_category || {});
          if (specs.length) {
            var specGrid = mkEl("div", "pcorg-popup-spec-grid");
            specs.forEach(function (s) {
              var lbl = mkEl("span", "pcorg-popup-spec-lbl"); lbl.textContent = s[0];
              specGrid.appendChild(lbl);
              var val = mkEl("span", "pcorg-popup-spec-val"); val.textContent = s[1];
              specGrid.appendChild(val);
            });
            specDiv.appendChild(specGrid);
          }
          chronoDiv.textContent = "";
          var history = d.comment_history || [];
          if (!history.length) { chronoDiv.style.display = "none"; return; }
          chronoDiv.appendChild(renderChronology(history, st.color, { compact: true, refTs: d.ts }));
        })
        .catch(function () { chronoDiv.textContent = ""; chronoDiv._loaded = false; });
    });

    return marker;
  }

  // ── Photo lightbox ─────────────────────────────────────────────────────
  function openPhotoLightbox(src) {
    var existing = document.getElementById("pcorg-photo-lightbox");
    if (existing) existing.remove();
    var overlay = document.createElement("div");
    overlay.id = "pcorg-photo-lightbox";
    overlay.className = "pcorg-lightbox";
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay || e.target.classList.contains("pcorg-lightbox-close")) {
        overlay.remove();
      }
    });
    var img = document.createElement("img");
    img.src = src;
    img.alt = "Photo terrain";
    overlay.appendChild(img);
    var closeBtn = document.createElement("button");
    closeBtn.className = "pcorg-lightbox-close";
    closeBtn.innerHTML = "<span class='material-symbols-outlined'>close</span>";
    closeBtn.addEventListener("click", function () { overlay.remove(); });
    overlay.appendChild(closeBtn);
    document.body.appendChild(overlay);
  }

  // Affiche la carte, y compris depuis le panneau elargi : celui-ci masquait
  // #map-main sans changer la vue courante, et switchView("map") n'etait
  // alors pas appele -> centrage sur une carte invisible, zone vide.
  function ensureMapVisible() {
    var mv = window.CockpitMapView;
    if (expPanel && expPanel.style.display !== "none") {
      _expPreviousView = "map";
      closeExpanded();
    }
    if (mv && mv.currentView && mv.currentView() !== "map") mv.switchView("map");
    var mapMain = document.getElementById("map-main");
    if (mapMain && mapMain.style.display === "none") mapMain.style.display = "block";
    var map = getMap();
    if (map) setTimeout(function () { map.invalidateSize(); }, 50);
  }

  // id (optionnel) : ouvre la popup du pin et met la fiche en evidence
  function flyToPin(lat, lon, id) {
    ensureMapVisible();
    setTimeout(function () {
      var map = getMap();
      if (!map) return;
      var targetPoint = map.project([lat, lon], 17);
      targetPoint.y -= 100;
      var targetLatLng = map.unproject(targetPoint, 17);
      map.flyTo(targetLatLng, 17, { duration: 0.8 });
      if (id) {
        setActiveFiche(id);
        map.once("moveend", function () {
          var mk = pcorgMarkers[id];
          if (mk && pcorgMapLayer && pcorgMapLayer.hasLayer(mk)) mk.openPopup();
        });
      }
    }, 300);
  }

  // ── Create modal (wizard 3 etapes) ─────────────────────────────────────────
  var createModal, createOverlay, createMiniMap;
  var createLat = null, createLon = null;
  var createSelectedCat = "";
  var createStep = 1;
  var createMarker = null;
  var createGridLayer = null;
  var createGrid25Layer = null;
  var createGridData = null;
  var createGridMeta = null;
  var createAreaDesc = "";
  var createGrid100On = false;
  var createGrid25On = false;
  var createCarroye = "";
  var _createCameraPhoto = null; // {url, cam_name} si une capture camera est jointe
  var _createPhotos = [];        // photos prises sur le moment (blobs reduits), envoyees apres creation
  var createInterventionTs = null; // Date|null : null = "maintenant" (defaut)
  var createSource = "";           // "initiative" | "externe" | "operateur" | "hierarchie"
  var createCanal = "";            // "telephone" | "radio" | "presentiel" | "mail"

  // ── Source de la fiche (qui/pourquoi a declenche la creation) ───────────
  var SOURCE_TYPES = [
    { id: "initiative", label: "Moi",         icon: "person",         color: "#10b981",
      desc: "Observation, ronde, ASL" },
    { id: "externe",    label: "Externe",     icon: "call_received",  color: "#0ea5e9",
      desc: "Spectateur, riverain, secours, organisateur" },
    { id: "operateur",  label: "Opérateur",   icon: "groups",         color: "#8b5cf6",
      desc: "Collègue PCO, dispatcheur, terrain" },
    { id: "hierarchie", label: "Hiérarchie",  icon: "verified_user",  color: "#f59e0b",
      desc: "Chef de poste, COS, direction" }
  ];
  var SOURCE_BY_ID = SOURCE_TYPES.reduce(function (acc, s) { acc[s.id] = s; return acc; }, {});

  var CANAUX = [
    { id: "telephone",  label: "Téléphone", icon: "call" },
    { id: "radio",      label: "Radio",       icon: "radio" },
    { id: "presentiel", label: "Présentiel",  icon: "co_present" },
    { id: "mail",       label: "Mail",        icon: "mail" },
    // Application : fiche issue d'un outil (constat terrain d'une tablette
    // declarante transforme sur /declarations, etc.)
    { id: "application", label: "Application", icon: "smartphone" }
  ];
  var CANAL_BY_ID = CANAUX.reduce(function (acc, c) { acc[c.id] = c; return acc; }, {});

  // Listes de reference chargees depuis la config
  var pcorgConfig = { sous_classifications: {}, intervenants: [], services: [], fiche_simplifiee: {}, urgence_categories: {} };
  var vehiclesByCategory = {};
  var createPendingPatrouille = "";
  var cockpitUserNames = []; // [{name: "Prenom Nom"}, ...] pour autocomplete sources

  function extractLabels(items) {
    if (!items || !items.length) return [];
    return items.map(function (it) { return (typeof it === "object") ? it.label : it; });
  }

  function loadPcorgConfig() {
    fetch("/api/pcorg-config")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d) pcorgConfig = d;
        try { _refreshSourceDatalists(); } catch (e) {}
      })
      .catch(function () {});
  }

  function loadCockpitUserNames() {
    fetch("/api/cockpit-users/names", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (d) {
        if (Array.isArray(d)) cockpitUserNames = d;
        try { _refreshSourceDatalists(); } catch (e) {}
      })
      .catch(function () {});
  }

  function loadVehiclesByCategory() {
    var ev = window.selectedEvent, yr = window.selectedYear;
    var url = "/anoloc/vehicles-by-category";
    if (ev && yr) {
      url += "?event=" + encodeURIComponent(ev) + "&year=" + encodeURIComponent(yr);
    }
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d) vehiclesByCategory = d; })
      .catch(function () {});
  }

  // ── Horodatage personnalise (chip + popover) ─────────────────────────────
  // Bornes alignees avec le backend (cf. PCORG_TS_PAST_MAX_DAYS / FUTURE_MAX_DAYS)
  var TS_PAST_MAX_DAYS = 60;
  var TS_FUTURE_MAX_DAYS = 30;
  var TS_QUICK_OFFSETS_MIN = [-5, -15, -30, -60]; // raccourcis (negatifs = passe)
  var _tsPopover = null;
  var _tsPopoverDocHandlers = null;

  function _pad2(n) { return String(n).padStart(2, "0"); }

  function _fmtTimeHM(d) { return _pad2(d.getHours()) + ":" + _pad2(d.getMinutes()); }
  function _fmtDateDMY(d) { return _pad2(d.getDate()) + "/" + _pad2(d.getMonth() + 1); }

  function _isSameDay(a, b) {
    return a.getFullYear() === b.getFullYear()
      && a.getMonth() === b.getMonth()
      && a.getDate() === b.getDate();
  }

  // Convertit Date -> string format input[type=datetime-local] (YYYY-MM-DDTHH:MM)
  function _toLocalInputValue(d) {
    return d.getFullYear() + "-" + _pad2(d.getMonth() + 1) + "-" + _pad2(d.getDate())
      + "T" + _pad2(d.getHours()) + ":" + _pad2(d.getMinutes());
  }

  // Convertit input[type=datetime-local] (sans tz) -> Date locale
  function _fromLocalInputValue(s) {
    if (!s) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(s);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], 0, 0);
  }

  // Genere le label affiche dans le chip pour une Date donnee (ou null = "Maintenant")
  function formatTsChipLabel(date) {
    if (!date) return { label: "Maintenant", state: "now" };
    var now = new Date();
    var diffMs = date.getTime() - now.getTime();
    var diffMin = Math.round(diffMs / 60000);
    var absMin = Math.abs(diffMin);
    // < 1 min d'ecart -> considere comme maintenant
    if (absMin < 1) return { label: "Maintenant", state: "now" };
    if (diffMin < 0) {
      // Antidate (passe)
      var lbl;
      if (absMin < 60) {
        lbl = "Il y a " + absMin + " min";
      } else if (absMin < 24 * 60 && _isSameDay(date, now)) {
        var h = Math.floor(absMin / 60);
        var m = absMin % 60;
        lbl = "Il y a " + h + "h" + (m ? _pad2(m) : "");
      } else {
        lbl = _fmtDateDMY(date) + " a " + _fmtTimeHM(date);
      }
      return { label: lbl, state: "past", icon: "history" };
    }
    // Programme (futur)
    var lblF;
    if (absMin < 60) {
      lblF = "Dans " + absMin + " min";
    } else if (absMin < 24 * 60 && _isSameDay(date, now)) {
      lblF = "Aujourd'hui " + _fmtTimeHM(date);
    } else {
      var tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
      if (_isSameDay(date, tomorrow)) lblF = "Demain " + _fmtTimeHM(date);
      else lblF = _fmtDateDMY(date) + " a " + _fmtTimeHM(date);
    }
    return { label: lblF, state: "future", icon: "event_upcoming" };
  }

  // Met a jour visuel du chip
  function updateTsChip(chipEl, date) {
    if (!chipEl) return;
    var info = formatTsChipLabel(date);
    var labelEl = chipEl.querySelector(".pcorg-ts-chip-label");
    var icoEl = chipEl.querySelector(".pcorg-ts-chip-ico");
    if (labelEl) labelEl.textContent = info.label;
    if (icoEl) icoEl.textContent = info.icon || "schedule";
    chipEl.classList.remove("pcorg-ts-now", "pcorg-ts-past", "pcorg-ts-future");
    chipEl.classList.add("pcorg-ts-" + info.state);
  }

  // Affiche le popover ancre sur chipEl. onChange(Date|null) appele a la selection.
  function openTsPopover(chipEl, currentDate, onChange) {
    closeTsPopover();
    var pop = mkEl("div", "pcorg-ts-popover");
    pop.setAttribute("role", "dialog");

    var head = mkEl("div", "pcorg-ts-pop-head");
    var headIco = matIcon("schedule");
    head.appendChild(headIco);
    var headTxt = mkEl("span", ""); headTxt.textContent = "Heure d'intervention";
    head.appendChild(headTxt);
    pop.appendChild(head);

    var nowOpt = mkEl("button", "pcorg-ts-opt");
    nowOpt.type = "button";
    nowOpt.setAttribute("data-value", "now");
    var nowDot = mkEl("span", "pcorg-ts-opt-dot");
    nowOpt.appendChild(nowDot);
    var nowLbl = mkEl("span", "pcorg-ts-opt-label");
    nowLbl.textContent = "Maintenant";
    nowOpt.appendChild(nowLbl);
    var nowTime = mkEl("span", "pcorg-ts-opt-time");
    nowTime.textContent = _fmtTimeHM(new Date());
    nowOpt.appendChild(nowTime);
    pop.appendChild(nowOpt);

    TS_QUICK_OFFSETS_MIN.forEach(function (off) {
      var d = new Date(Date.now() + off * 60000);
      var btn = mkEl("button", "pcorg-ts-opt");
      btn.type = "button";
      var dot = mkEl("span", "pcorg-ts-opt-dot");
      btn.appendChild(dot);
      var lbl = mkEl("span", "pcorg-ts-opt-label");
      lbl.textContent = "Il y a " + Math.abs(off) + " min";
      btn.appendChild(lbl);
      var t = mkEl("span", "pcorg-ts-opt-time");
      t.textContent = _fmtTimeHM(d);
      btn.appendChild(t);
      btn.addEventListener("click", function (e) {
        e.preventDefault(); e.stopPropagation();
        onChange(d);
        closeTsPopover();
      });
      pop.appendChild(btn);
    });

    nowOpt.addEventListener("click", function (e) {
      e.preventDefault(); e.stopPropagation();
      onChange(null);
      closeTsPopover();
    });

    var sep = mkEl("div", "pcorg-ts-pop-sep");
    sep.textContent = "Personnaliser";
    pop.appendChild(sep);

    var custom = mkEl("div", "pcorg-ts-custom");
    var input = mkEl("input", "form-input pcorg-ts-custom-input");
    input.type = "datetime-local";
    var seedDate = currentDate || new Date();
    input.value = _toLocalInputValue(seedDate);
    var minD = new Date(); minD.setDate(minD.getDate() - TS_PAST_MAX_DAYS);
    var maxD = new Date(); maxD.setDate(maxD.getDate() + TS_FUTURE_MAX_DAYS);
    input.min = _toLocalInputValue(minD);
    input.max = _toLocalInputValue(maxD);
    custom.appendChild(input);
    var applyBtn = mkEl("button", "pcorg-ts-custom-apply");
    applyBtn.type = "button";
    applyBtn.appendChild(matIcon("check"));
    applyBtn.addEventListener("click", function (e) {
      e.preventDefault(); e.stopPropagation();
      var d = _fromLocalInputValue(input.value);
      if (!d) { showToast("warning", "Date/heure invalide"); return; }
      var diffDays = (d.getTime() - Date.now()) / 86400000;
      if (diffDays < -TS_PAST_MAX_DAYS) {
        showToast("warning", "Date trop ancienne (max " + TS_PAST_MAX_DAYS + " jours)");
        return;
      }
      if (diffDays > TS_FUTURE_MAX_DAYS) {
        showToast("warning", "Date trop lointaine (max " + TS_FUTURE_MAX_DAYS + " jours)");
        return;
      }
      onChange(d);
      closeTsPopover();
    });
    custom.appendChild(applyBtn);
    pop.appendChild(custom);

    document.body.appendChild(pop);
    chipEl.setAttribute("aria-expanded", "true");

    // Position : sous le chip, aligne a gauche; flip si overflow
    var rect = chipEl.getBoundingClientRect();
    pop.style.left = rect.left + "px";
    pop.style.top = (rect.bottom + 6) + "px";
    requestAnimationFrame(function () {
      var pr = pop.getBoundingClientRect();
      if (pr.right > window.innerWidth - 8) {
        pop.style.left = Math.max(8, window.innerWidth - pr.width - 8) + "px";
      }
      if (pr.bottom > window.innerHeight - 8) {
        pop.style.top = Math.max(8, rect.top - pr.height - 6) + "px";
      }
    });

    _tsPopover = { el: pop, chip: chipEl };
    function onDocClick(e) {
      if (!_tsPopover) return;
      if (e.target.closest(".pcorg-ts-popover")) return;
      if (e.target === _tsPopover.chip || _tsPopover.chip.contains(e.target)) return;
      closeTsPopover();
    }
    function onEsc(e) { if (e.key === "Escape") closeTsPopover(); }
    setTimeout(function () {
      document.addEventListener("click", onDocClick, true);
      document.addEventListener("touchstart", onDocClick, true);
    }, 50);
    document.addEventListener("keydown", onEsc);
    _tsPopoverDocHandlers = { onDocClick: onDocClick, onEsc: onEsc };
  }

  function closeTsPopover() {
    if (_tsPopover) {
      _tsPopover.chip.setAttribute("aria-expanded", "false");
      _tsPopover.el.remove();
      _tsPopover = null;
    }
    if (_tsPopoverDocHandlers) {
      document.removeEventListener("click", _tsPopoverDocHandlers.onDocClick, true);
      document.removeEventListener("touchstart", _tsPopoverDocHandlers.onDocClick, true);
      document.removeEventListener("keydown", _tsPopoverDocHandlers.onEsc);
      _tsPopoverDocHandlers = null;
    }
  }

  function showCreate() { createModal.classList.add("show"); createOverlay.classList.add("show"); }
  function hideCreate() {
    createModal.classList.remove("show"); createOverlay.classList.remove("show");
    destroyCreateMap();
    createStep = 1;
    createLat = null;
    createLon = null;
    createSelectedCat = "";
    createCarroye = "";
    createGrid100On = false;
    createGrid25On = false;
    _createCameraPhoto = null;
    _createPhotos = [];
  }

  function destroyCreateMap() {
    if (createMiniMap) { createMiniMap.remove(); createMiniMap = null; }
    createMarker = null;
    createGridLayer = null;
    createGrid25Layer = null;
    createGridMeta = null;
  }

  function initCreateModal() {
    createModal = document.getElementById("pcorgCreateModal");
    createOverlay = document.getElementById("pcorgCreateOverlay");
    var btn = document.getElementById("pcorg-add-btn");
    var closeBtn = document.getElementById("pcorgCreateClose");
    var cancelBtn = document.getElementById("pcorgCreateCancel");
    var nextBtn = document.getElementById("pcorgCreateNext");
    var prevBtn = document.getElementById("pcorgCreatePrev");
    var form = document.getElementById("pcorgCreateForm");
    // Le bouton "+" n'existe que sur l'accueil : en mode creation seule
    // (File du service), l'assistant s'ouvre par window.PcorgCreate.open().
    if (!createModal || (!btn && !createOnlyMode)) return;
    // Droit de groupe "Creer des fiches" (le serveur refuse aussi : 403)
    if (btn && window.__userCanCreateFiche === false && !window.__userIsAdmin) btn.style.display = "none";

    closeBtn.addEventListener("click", hideCreate);
    cancelBtn.addEventListener("click", hideCreate);
    createOverlay.addEventListener("click", hideCreate);

    buildSourceTabs();

    // Click "+" -> ouvrir la modale directement a l'etape 1
    if (btn) btn.addEventListener("click", function () {
      createHooks = {};
      openCreateWizard();
    });

    // Chip horodatage (etape 2)
    var tsChip = document.getElementById("pcorg-c-ts-chip");
    if (tsChip) {
      tsChip.addEventListener("click", function (e) {
        e.preventDefault(); e.stopPropagation();
        if (_tsPopover) { closeTsPopover(); return; }
        openTsPopover(tsChip, createInterventionTs, function (date) {
          createInterventionTs = date;
          updateTsChip(tsChip, date);
        });
      });
    }

    // Navigation
    nextBtn.addEventListener("click", function () {
      if (createStep === 1) {
        if (createLat === null) {
          // Position facultative (appel sans localisation precise) : elle
          // pourra etre posee plus tard depuis la fiche
          showConfirmToast("Aucune position. Continuer sans localiser la fiche ?",
            { okLabel: "Continuer", cancelLabel: "Positionner", type: "warning" })
            .then(function (ok) { if (ok) goToStep(2); });
          return;
        }
        goToStep(2);
      } else if (createStep === 2) {
        if (!createSelectedCat) {
          showToast("warning", "Selectionnez une categorie");
          return;
        }
        var text = document.getElementById("pcorg-c-text").value.trim();
        if (!text) {
          showToast("warning", "La description est obligatoire");
          return;
        }
        var srcCheck = validateSourceFields();
        if (!srcCheck.ok) { showToast("warning", srcCheck.msg); return; }
        goToStep(3);
      }
    });

    prevBtn.addEventListener("click", function () {
      if (createStep > 1) goToStep(createStep - 1);
    });

    // Tile switcher
    document.getElementById("pcorg-create-tile").addEventListener("click", function () {
      if (!createMiniMap) return;
      if (createTileCurrent === "osm") {
        createMiniMap.removeLayer(createTileOSM);
        createTileSatEGIS.addTo(createMiniMap);
        createTileCurrent = "sat-egis";
      } else if (createTileCurrent === "sat-egis") {
        createMiniMap.removeLayer(createTileSatEGIS);
        createTileSatACO.addTo(createMiniMap);
        createTileCurrent = "sat-aco";
      } else if (createTileCurrent === "sat-aco") {
        createMiniMap.removeLayer(createTileSatACO);
        createTileSatIGN.addTo(createMiniMap);
        createTileCurrent = "sat-ign";
      } else {
        createMiniMap.removeLayer(createTileSatIGN);
        createTileOSM.addTo(createMiniMap);
        createTileCurrent = "osm";
      }
    });

    // Grid toggles
    document.getElementById("pcorg-create-grid100").addEventListener("click", function () {
      toggleCreateGrid100();
    });
    var locBtn = document.getElementById("pcorg-create-locate");
    if (locBtn && navigator.geolocation && window.isSecureContext !== false) {
      locBtn.style.display = "";
      locBtn.addEventListener("click", function () { createLocate(false); });
    }
    document.getElementById("pcorg-create-grid25").addEventListener("click", function () {
      toggleCreateGrid25();
    });

    buildCatButtons();
    pcaInitAssist();

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      submitCreate();
    });
  }

  function openCreateWizard() {
    checkSessionBeforeForm();
    resetCreateWizard();
    applyCreateCategoryFilter();
    showCreate();
    goToStep(1);
    initCreateMap();
    // Telephone : position GPS posee d'office (modifiable d'un clic sur la carte),
    // sauf si l'appelant fournit deja la position (constat transforme en fiche)
    var pfPos = createHooks.prefill && createHooks.prefill.lat != null;
    if (isPhoneDevice() && !pfPos) createLocate(true);
  }

  // Telephone = ecran tactile (pointeur grossier) et etroit. Une tablette du
  // PC ou un poste fixe ne se geolocalise pas d'office.
  function isPhoneDevice() {
    try {
      return window.matchMedia("(pointer: coarse)").matches &&
             window.matchMedia("(max-width: 820px)").matches;
    } catch (e) { return false; }
  }

  // Geolocalisation de la fiche en cours de creation.
  // auto=true (ouverture sur telephone) : ne remplace jamais une position
  // deja choisie a la main, et ne place l'epingle QUE si la position tombe
  // dans le carroyage du site (/api/grid-ref, aucun point ni rayon en dur) :
  // hors carroyage ou carroyage indisponible = operateur pas sur place, rien
  // n'est pose. Le bouton "Ma position" (auto=false) place toujours.
  var CREATE_GEO_MAX_ACC_M = 150;  // precision GPS au-dela de laquelle on n'auto-place pas
  var CREATE_GEO_GRID_WAIT_MS = 5000;  // attente max du carroyage apres la reponse GPS
  var createGeoSeq = 0;
  function createLocate(auto) {
    if (!navigator.geolocation || window.isSecureContext === false) {
      if (!auto) showToast("warning", "Geolocalisation indisponible sur cet appareil");
      return;
    }
    var seq = ++createGeoSeq;
    var btn = document.getElementById("pcorg-create-locate");
    if (btn) btn.classList.add("active");
    var info = document.getElementById("pcorg-create-map-info");
    if (auto && info && createLat == null) {
      info.innerHTML = "<span class='material-symbols-outlined'>my_location</span> Localisation en cours...";
    }
    function restoreInfo() {
      if (info) info.innerHTML = "<span class='material-symbols-outlined'>touch_app</span> Cliquez sur la carte pour positionner l'intervention";
    }
    navigator.geolocation.getCurrentPosition(function (pos) {
      if (btn) btn.classList.remove("active");
      restoreInfo();
      // fiche fermee, autre demande lancee, ou etape 1 quittee entre-temps
      if (seq !== createGeoSeq || !createModal.classList.contains("show") || createStep !== 1) return;
      if (auto && createLat != null) return;     // position deja choisie a la main
      var lat = pos.coords.latitude, lon = pos.coords.longitude;
      var acc = Math.round(pos.coords.accuracy || 0);
      var t0 = Date.now();
      var apply = function () {
        if (seq !== createGeoSeq || !createModal.classList.contains("show") || createStep !== 1) return;
        if (auto && createLat != null) return;
        // carte et carroyage pas encore prets : on attend un peu
        if (!createMiniMap || (auto && !createGridMeta && Date.now() - t0 < CREATE_GEO_GRID_WAIT_MS)) {
          setTimeout(apply, 150);
          return;
        }
        if (auto) {
          if (!createGridMeta) return;     // carroyage indisponible : on ne devine pas
          if (!resolveCreateGridCell(lat, lon)) {
            showToast("info", "Position GPS hors du carroyage du site : placez l'intervention sur la carte");
            return;
          }
          if (acc > CREATE_GEO_MAX_ACC_M) {
            showToast("info", "GPS imprecis (+/- " + acc + " m) : placez l'intervention sur la carte");
            return;
          }
        }
        setCreatePosition(lat, lon);
        createMiniMap.setView([lat, lon], Math.max(createMiniMap.getZoom(), 18));
        showToast("success", "Position GPS (+/- " + acc + " m) - touchez la carte pour corriger");
      };
      apply();
    }, function (err) {
      if (btn) btn.classList.remove("active");
      restoreInfo();
      if (seq !== createGeoSeq) return;
      if (err && err.code === 1) {
        showToast("warning", "Localisation refusee : autorisez-la dans les reglages du telephone");
      } else if (!auto) {
        showToast("warning", "Position GPS introuvable, placez l'intervention sur la carte");
      }
    }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 });
  }

  // Categories proposees a l'etape 2 : toutes sur l'accueil, celles du
  // service sur la File du service (createHooks.categories). Une seule :
  // preselectionnee (l'etape 2 ne demande plus que la description).
  function applyCreateCategoryFilter() {
    var only = Array.isArray(createHooks.categories) && createHooks.categories.length
      ? createHooks.categories : null;
    document.querySelectorAll(".pcorg-create-cat-btn").forEach(function (b) {
      b.style.display = (!only || only.indexOf(b.getAttribute("data-cat")) >= 0) ? "" : "none";
    });
    if (only && only.length === 1) selectCategory(only[0]);
  }

  function resetCreateWizard() {
    createLat = null;
    createLon = null;
    createSelectedCat = "";
    createSelectedUrgency = "";
    createPendingPatrouille = "";
    createFieldsCtl = null;
    createClientToken = randomToken();
    createSubmitting = false;
    createCarroye = "";
    createAreaDesc = "";
    createGrid100On = false;
    createGrid25On = false;
    createStep = 1;
    createInterventionTs = null;
    closeTsPopover();
    var tsChipReset = document.getElementById("pcorg-c-ts-chip");
    if (tsChipReset) updateTsChip(tsChipReset, null);
    createSource = "";
    createCanal = "";
    pcaReset();
    document.querySelectorAll(".pcorg-source-tab").forEach(function (b) { b.classList.remove("selected"); });
    var srcFields = document.getElementById("pcorg-c-source-fields");
    if (srcFields) srcFields.textContent = "";
    // Reset header
    var header = document.getElementById("pcorg-create-header");
    header.style.background = "var(--brand)";
    header.querySelector(".pcorg-fiche-icon").textContent = "add_circle";
    header.querySelector(".pcorg-fiche-cat").textContent = "Nouvelle intervention";
    document.getElementById("pcorg-create-pos-label").textContent = "";
    // Reset form
    document.getElementById("pcorgCreateForm").reset();
    document.querySelectorAll(".pcorg-create-cat-btn").forEach(function (b) {
      b.classList.remove("selected"); b.style.borderColor = ""; b.style.background = "";
    });
    document.getElementById("pcorg-create-specific").textContent = "";
    document.getElementById("pcorg-create-pos-row").style.display = "none";
    document.getElementById("pcorg-create-map-info").style.display = "";
    document.getElementById("pcorg-create-grid25").style.display = "none";
    document.getElementById("pcorg-create-grid100").classList.remove("active");
    document.getElementById("pcorg-create-grid25").classList.remove("active");
  }

  function goToStep(step) {
    createStep = step;
    // Update step visibility
    document.querySelectorAll(".pcorg-create-step").forEach(function (el) {
      el.classList.toggle("active", el.getAttribute("data-step") === String(step));
    });
    // Update stepper
    document.querySelectorAll(".pcorg-step").forEach(function (el) {
      var s = parseInt(el.getAttribute("data-step"), 10);
      el.classList.toggle("active", s === step);
      el.classList.toggle("done", s < step);
    });
    // Update step lines
    var lines = document.querySelectorAll(".pcorg-step-line");
    if (lines[0]) lines[0].classList.toggle("done", step > 1);
    if (lines[1]) lines[1].classList.toggle("done", step > 2);
    // Update nav buttons
    document.getElementById("pcorgCreatePrev").style.display = step > 1 ? "" : "none";
    document.getElementById("pcorgCreateNext").style.display = step < 3 ? "" : "none";
    document.getElementById("pcorgCreateSubmit").style.display = step === 3 ? "" : "none";
    // Resize map if going back to step 1
    if (step === 1 && createMiniMap) {
      setTimeout(function () { createMiniMap.invalidateSize(); }, 100);
    }
  }

  var createTileOSM = null, createTileSatEGIS = null, createTileSatACO = null, createTileSatIGN = null;
  var createTileCurrent = "osm";

  function initCreateMap() {
    var mapDiv = document.getElementById("pcorg-create-minimap");
    destroyCreateMap();
    createTileCurrent = "osm";
    setTimeout(function () {
      var mainMap = getMap();
      var center = mainMap ? mainMap.getCenter() : L.latLng(47.95, 0.22);
      var zoom = mainMap ? Math.max(mainMap.getZoom(), 15) : 16;

      createMiniMap = L.map(mapDiv, {
        center: center, zoom: zoom,
        zoomControl: true, attributionControl: false,
        dragging: true, scrollWheelZoom: true,
        doubleClickZoom: false, touchZoom: true
      });
      createTileOSM = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxNativeZoom: 19, maxZoom: 22
      }).addTo(createMiniMap);
      createTileSatEGIS = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
        maxNativeZoom: 19, maxZoom: 22
      });
      createTileSatACO = L.tileLayer("/tiles/{z}/{x}/{y}.png", {
        tms: true, maxZoom: 22
      });
      // Orthophoto IGN (Géoplateforme, sans clé API) : tuiles natives jusqu'au zoom 19,
      // puis agrandissement pixelisé jusqu'au zoom 22.
      createTileSatIGN = L.tileLayer("https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}", {
        maxNativeZoom: 19, maxZoom: 22
      });
      createMiniMap.invalidateSize();

      // Click to position
      createMiniMap.on("click", function (e) {
        setCreatePosition(e.latlng.lat, e.latlng.lng);
      });

      // Zoom change: show/hide 25m button
      createMiniMap.on("zoomend", function () {
        var btn25 = document.getElementById("pcorg-create-grid25");
        if (createGrid100On && createMiniMap.getZoom() >= 18) {
          btn25.style.display = "";
        } else {
          btn25.style.display = "none";
          if (createGrid25On) { clearCreateGrid25(); createGrid25On = false; btn25.classList.remove("active"); }
        }
      });

      // Auto-load grid silently for carroyage resolution
      loadCreateGridSilent();
    }, 150);
  }

  function loadCreateGridSilent() {
    var doLoad = function (data) {
      createGridData = data;
      createGridMeta = buildGridMeta(data);
      if (createGridMeta && !sharedGridMeta) sharedGridMeta = createGridMeta;
      // position posee avant l'arrivee du carroyage (GPS du telephone) : on
      // complete le carre maintenant
      if (createGridMeta && createLat != null && !createCarroye && createMiniMap) {
        setCreatePosition(createLat, createLon);
      }
    };
    if (window.CockpitMapView && window.CockpitMapView.getGridData && window.CockpitMapView.getGridData()) {
      doLoad(window.CockpitMapView.getGridData());
    } else {
      fetch("/api/grid-ref")
        .then(function (r) { return r.json(); })
        .then(doLoad)
        .catch(function () {});
    }
  }

  function setCreatePosition(lat, lon) {
    createLat = lat;
    createLon = lon;

    // Update or create marker
    if (createMarker) {
      createMarker.setLatLng([lat, lon]);
    } else {
      createMarker = L.marker([lat, lon], {
        icon: L.divIcon({
          className: "",
          html: "<div class='pcorg-pin' style='background:var(--brand)'><span class='material-symbols-outlined'>add_location</span></div>",
          iconSize: [36, 36], iconAnchor: [18, 36]
        })
      }).addTo(createMiniMap);
    }

    // Display coordinates
    document.getElementById("pcorg-create-lat-display").textContent = lat.toFixed(6);
    document.getElementById("pcorg-create-lon-display").textContent = lon.toFixed(6);
    document.getElementById("pcorg-create-pos-row").style.display = "";
    document.getElementById("pcorg-create-map-info").style.display = "none";

    // Resolve carroyage
    createCarroye = "";
    var carrEl = document.getElementById("pcorg-create-carroye-display");
    if (createGridMeta) {
      var label = resolveCreateGridCell(lat, lon);
      if (label) {
        createCarroye = label;
        carrEl.textContent = label;
      } else {
        carrEl.textContent = "Hors zone";
      }
    } else {
      var mainLabel = resolveCellLabel(lat, lon);
      if (mainLabel) {
        createCarroye = mainLabel;
        carrEl.textContent = mainLabel;
      } else {
        carrEl.textContent = "--";
      }
    }

    // Resolve zone from POI polygons
    createAreaDesc = "";
    var zoneItem = document.getElementById("pcorg-create-zone-item");
    var zoneDisp = document.getElementById("pcorg-create-zone-display");
    if (window.CockpitMapView && window.CockpitMapView.findZoneAtPoint) {
      var zoneName = window.CockpitMapView.findZoneAtPoint(lat, lon);
      if (zoneName) createAreaDesc = zoneName;
    }
    if (zoneItem && zoneDisp) {
      if (createAreaDesc) {
        zoneDisp.textContent = createAreaDesc;
        zoneItem.style.display = "";
      } else {
        zoneItem.style.display = "none";
      }
    }

    // Update header
    var posParts = [];
    if (createAreaDesc) posParts.push(createAreaDesc);
    if (createCarroye) posParts.push(createCarroye);
    posParts.push(lat.toFixed(5) + ", " + lon.toFixed(5));
    document.getElementById("pcorg-create-pos-label").textContent = posParts.join(" - ");
  }

  function resolveCreateGridCell(lat, lon) {
    return gridCellFromMeta(createGridMeta, lat, lon);
  }

  // ── Grid on create map ──────────────────────────────────────────────────────
  function colLabel(idx) {
    if (idx < 26) return String.fromCharCode(65 + idx);
    return String.fromCharCode(65 + Math.floor(idx / 26) - 1) + String.fromCharCode(65 + (idx % 26));
  }

  function toggleCreateGrid100() {
    createGrid100On = !createGrid100On;
    document.getElementById("pcorg-create-grid100").classList.toggle("active", createGrid100On);
    if (createGrid100On) {
      renderCreateGrid100();
    } else {
      clearCreateGrid100();
      clearCreateGrid25();
      createGrid25On = false;
      document.getElementById("pcorg-create-grid25").classList.remove("active");
      document.getElementById("pcorg-create-grid25").style.display = "none";
    }
  }

  function renderCreateGrid100() {
    if (!createMiniMap || !createGridData || !createGridData.lines) return;
    var lines = createGridData.lines;
    var hLines = lines.h_lines || [];
    var vLines = lines.v_lines || [];

    createGridLayer = L.layerGroup().addTo(createMiniMap);
    hLines.forEach(function (l) {
      L.polyline([[l.lat, l.lng_start], [l.lat, l.lng_end]],
        { color: "#f59e0b", weight: 1, opacity: 0.6, interactive: false }
      ).addTo(createGridLayer);
    });
    vLines.forEach(function (l) {
      L.polyline([[l.lat_start, l.lng], [l.lat_end, l.lng]],
        { color: "#f59e0b", weight: 1, opacity: 0.6, interactive: false }
      ).addTo(createGridLayer);
    });

    var numCols = createGridMeta ? createGridMeta.numCols : (lines.num_cols || vLines.length - 1);
    var numRows = createGridMeta ? createGridMeta.numRows : (lines.num_rows || hLines.length - 1);
    var colOff = lines.col_offset || 0;
    var rowOff = lines.row_offset || 0;
    var labeledBounds = L.latLngBounds(
      [hLines[numRows].lat, vLines[colOff > 0 ? colOff : 0].lng],
      [hLines[rowOff > 0 ? rowOff : 0].lat, vLines[numCols].lng]
    );
    createMiniMap.fitBounds(labeledBounds, { padding: [20, 20] });

    if (createMiniMap.getZoom() >= 18) {
      document.getElementById("pcorg-create-grid25").style.display = "";
    }
  }

  function clearCreateGrid100() {
    if (createGridLayer && createMiniMap) {
      createMiniMap.removeLayer(createGridLayer);
      createGridLayer = null;
    }
    // createGridMeta conserve : masquer la grille cassait la resolution du
    // carroyage au clic suivant
  }

  function toggleCreateGrid25() {
    createGrid25On = !createGrid25On;
    document.getElementById("pcorg-create-grid25").classList.toggle("active", createGrid25On);
    if (createGrid25On) {
      renderCreateGrid25();
    } else {
      clearCreateGrid25();
    }
  }

  function renderCreateGrid25() {
    if (!createMiniMap || !createGridData || !createGridData.lines) return;
    var lines25 = createGridData.lines_25;
    if (!lines25) return;
    clearCreateGrid25();
    createGrid25Layer = L.layerGroup().addTo(createMiniMap);
    (lines25.h_lines || []).forEach(function (l) {
      L.polyline([[l.lat, l.lng_start], [l.lat, l.lng_end]],
        { color: "#fb923c", weight: 1, opacity: 0.7, dashArray: "6 4", interactive: false }
      ).addTo(createGrid25Layer);
    });
    (lines25.v_lines || []).forEach(function (l) {
      L.polyline([[l.lat_start, l.lng], [l.lat_end, l.lng]],
        { color: "#fb923c", weight: 1, opacity: 0.7, dashArray: "6 4", interactive: false }
      ).addTo(createGrid25Layer);
    });
  }

  function clearCreateGrid25() {
    if (createGrid25Layer && createMiniMap) {
      createMiniMap.removeLayer(createGrid25Layer);
      createGrid25Layer = null;
    }
  }

  function buildCatButtons() {
    var container = document.getElementById("pcorg-create-cats");
    if (!container) return;
    CATEGORY_ORDER.forEach(function (cat) {
      var st = catStyle(cat);
      var btn = mkEl("button", "pcorg-create-cat-btn");
      btn.type = "button";
      btn.setAttribute("data-cat", cat);
      var ico = matIcon(st.icon);
      ico.style.color = st.color;
      btn.appendChild(ico);
      var label = mkEl("span", "");
      label.textContent = shortCat(cat);
      btn.appendChild(label);
      btn.addEventListener("click", function () {
        selectCategory(cat);
      });
      container.appendChild(btn);
    });
  }

  function selectCategory(cat) {
    createSelectedCat = cat;
    var st = catStyle(cat);
    document.querySelectorAll(".pcorg-create-cat-btn").forEach(function (b) {
      var isSel = b.getAttribute("data-cat") === cat;
      b.classList.toggle("selected", isSel);
      b.style.borderColor = isSel ? st.color : "";
      b.style.background = isSel ? st.color : "";
    });
    // Update header color
    var header = document.getElementById("pcorg-create-header");
    header.style.background = st.color;
    header.querySelector(".pcorg-fiche-icon").textContent = st.icon;
    header.querySelector(".pcorg-fiche-cat").textContent = "Nouvelle " + shortCat(cat);
    // Update pin color on map
    if (createMarker && createMiniMap) {
      createMarker.setIcon(L.divIcon({
        className: "",
        html: "<div class='pcorg-pin' style='background:" + st.color + "'><span class='material-symbols-outlined'>" + st.icon + "</span></div>",
        iconSize: [36, 36], iconAnchor: [18, 36]
      }));
    }
    // Default source : Information/MainCourante -> "Moi" ; sinon "Externe"
    if (!createSource) {
      var defaultSrc = (cat === "PCO.Information" || cat === "PCO.MainCourante") ? "initiative" : "externe";
      selectSource(defaultSrc);
    }
    // Build specific fields for step 3
    buildSpecificCreateFields(cat);
  }

  // ── Source selector (etape 2) ──────────────────────────────────────────
  function buildSourceTabs() {
    var container = document.getElementById("pcorg-c-source-tabs");
    if (!container) return;
    container.textContent = "";
    SOURCE_TYPES.forEach(function (src) {
      var btn = mkEl("button", "pcorg-source-tab");
      btn.type = "button";
      btn.setAttribute("data-src", src.id);
      btn.style.setProperty("--src-color", src.color);
      var ico = matIcon(src.icon, "pcorg-source-tab-ico");
      btn.appendChild(ico);
      var lbl = mkEl("span", "pcorg-source-tab-label");
      lbl.textContent = src.label;
      btn.appendChild(lbl);
      btn.title = src.desc;
      btn.addEventListener("click", function () { selectSource(src.id); });
      container.appendChild(btn);
    });
  }

  function selectSource(srcId) {
    createSource = srcId;
    // Mise a jour visuel des onglets
    document.querySelectorAll(".pcorg-source-tab").forEach(function (b) {
      var isSel = b.getAttribute("data-src") === srcId;
      b.classList.toggle("selected", isSel);
    });
    buildSourceSubFields(srcId);
  }

  // Cache memoire fusionne admin + cockpit users (pour autocomplete custom)
  var _sourceLists = { operateur: [], hierarchie: [] };

  function _refreshSourceDatalists() {
    var userNames = (cockpitUserNames || []).map(function (u) { return u.name; }).filter(Boolean);
    var opAdmin = extractLabels((pcorgConfig && pcorgConfig.operateurs_internes) || []);
    var doAdmin = extractLabels((pcorgConfig && pcorgConfig.donneurs_ordre) || []);
    function dedupMerge(primary, secondary) {
      var seen = {};
      var out = [];
      primary.concat(secondary).forEach(function (v) {
        var k = (v || "").trim().toLowerCase();
        if (!k || seen[k]) return;
        seen[k] = 1; out.push(v);
      });
      return out;
    }
    _sourceLists.operateur = dedupMerge(opAdmin, userNames);
    _sourceLists.hierarchie = dedupMerge(doAdmin, userNames);
  }

  // Autocomplete custom : seuil 3 chars, style app, navigation clavier
  function attachAutocomplete(input, getItems, opts) {
    opts = opts || {};
    var minChars = opts.minChars != null ? opts.minChars : 3;
    var maxItems = opts.maxItems || 10;
    var dropdown = null;
    var activeIndex = -1;
    var currentItems = [];

    function position() {
      if (!dropdown) return;
      var r = input.getBoundingClientRect();
      dropdown.style.left = r.left + "px";
      dropdown.style.top = (r.bottom + 4) + "px";
      dropdown.style.minWidth = r.width + "px";
    }

    function close() {
      if (dropdown) { dropdown.remove(); dropdown = null; }
      activeIndex = -1;
    }

    function setActive(idx) {
      activeIndex = idx;
      if (!dropdown) return;
      var nodes = dropdown.querySelectorAll(".pcorg-autocomplete-opt");
      nodes.forEach(function (o, i) {
        o.classList.toggle("active", i === idx);
        if (i === idx) o.scrollIntoView({ block: "nearest" });
      });
    }

    function choose(idx) {
      if (idx < 0 || idx >= currentItems.length) return;
      input.value = currentItems[idx];
      close();
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function render() {
      var q = (input.value || "").trim().toLowerCase();
      if (q.length < minChars) { close(); return; }
      var items = (getItems() || []).filter(function (it) {
        return it.toLowerCase().indexOf(q) !== -1;
      });
      items.sort(function (a, b) {
        var as = a.toLowerCase().indexOf(q) === 0 ? 0 : 1;
        var bs = b.toLowerCase().indexOf(q) === 0 ? 0 : 1;
        if (as !== bs) return as - bs;
        return a.localeCompare(b);
      });
      currentItems = items.slice(0, maxItems);
      if (!currentItems.length) { close(); return; }
      if (!dropdown) {
        dropdown = mkEl("div", "pcorg-autocomplete");
        document.body.appendChild(dropdown);
      }
      dropdown.textContent = "";
      activeIndex = -1;
      currentItems.forEach(function (item, idx) {
        var opt = mkEl("div", "pcorg-autocomplete-opt");
        var pos = item.toLowerCase().indexOf(q);
        if (pos === -1) {
          opt.textContent = item;
        } else {
          if (pos > 0) opt.appendChild(document.createTextNode(item.slice(0, pos)));
          var hi = document.createElement("strong");
          hi.textContent = item.slice(pos, pos + q.length);
          opt.appendChild(hi);
          if (pos + q.length < item.length) {
            opt.appendChild(document.createTextNode(item.slice(pos + q.length)));
          }
        }
        opt.addEventListener("mousedown", function (e) {
          e.preventDefault(); // empeche le blur de fermer avant le click
          choose(idx);
        });
        dropdown.appendChild(opt);
      });
      position();
    }

    input.addEventListener("input", render);
    input.addEventListener("focus", render);
    input.addEventListener("blur", function () { setTimeout(close, 150); });
    input.addEventListener("keydown", function (e) {
      if (!dropdown || !currentItems.length) {
        if (e.key === "Escape") close();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((activeIndex + 1) % currentItems.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((activeIndex - 1 + currentItems.length) % currentItems.length);
      } else if (e.key === "Enter") {
        if (activeIndex >= 0) { e.preventDefault(); choose(activeIndex); }
      } else if (e.key === "Escape") {
        close();
      }
    });
    var repos = function () { if (dropdown) position(); };
    window.addEventListener("scroll", repos, true);
    window.addEventListener("resize", repos);
  }

  function buildSourceSubFields(srcId) {
    var container = document.getElementById("pcorg-c-source-fields");
    if (!container) return;
    container.textContent = "";
    _refreshSourceDatalists();

    if (srcId === "initiative") {
      // Champ optionnel : "A la suite de"
      var grp = mkEl("div", "form-group pcorg-source-subgrp");
      var lbl = mkEl("label", ""); lbl.setAttribute("for", "pcorg-c-source-origine");
      lbl.textContent = "À la suite de ";
      var optTag = mkEl("span", "pcorg-source-opt"); optTag.textContent = "(optionnel)";
      lbl.appendChild(optTag);
      grp.appendChild(lbl);
      var inp = mkEl("input", "form-input"); inp.type = "text"; inp.id = "pcorg-c-source-origine";
      inp.placeholder = "Ronde, observation directe, alerte caméra, ASL...";
      grp.appendChild(inp);
      container.appendChild(grp);
      return;
    }

    var qLabel, qId, qPlaceholder, qListKey;
    if (srcId === "externe") {
      qLabel = "Appelant"; qId = "pcorg-c-appelant";
      qPlaceholder = "Nom, fonction, organisme...";
      qListKey = "";
    } else if (srcId === "operateur") {
      qLabel = "Opérateur émetteur"; qId = "pcorg-c-emetteur";
      qPlaceholder = "Collègue PCO, dispatcheur, agent terrain";
      qListKey = "operateur";
    } else if (srcId === "hierarchie") {
      qLabel = "Donneur d'ordre"; qId = "pcorg-c-donneur";
      qPlaceholder = "Chef de poste, COS, direction sécurité...";
      qListKey = "hierarchie";
    }

    // Champ "qui"
    var grpQ = mkEl("div", "form-group pcorg-source-subgrp");
    var lblQ = mkEl("label", "");
    lblQ.setAttribute("for", qId);
    lblQ.textContent = qLabel + " ";
    var star = mkEl("span", "pcorg-required-star");
    star.style.color = "var(--danger,#ef4444)";
    star.textContent = "*";
    lblQ.appendChild(star);
    grpQ.appendChild(lblQ);
    var inpQ = mkEl("input", "form-input");
    inpQ.type = "text"; inpQ.id = qId; inpQ.placeholder = qPlaceholder;
    inpQ.autocomplete = "off";
    grpQ.appendChild(inpQ);
    container.appendChild(grpQ);

    if (qListKey) {
      attachAutocomplete(inpQ, function () { return _sourceLists[qListKey] || []; });
    }

    // Canal (obligatoire) — segmented
    var grpC = mkEl("div", "form-group pcorg-source-subgrp");
    var lblC = mkEl("label", "");
    lblC.textContent = "Canal ";
    var starC = mkEl("span", "pcorg-required-star");
    starC.style.color = "var(--danger,#ef4444)";
    starC.textContent = "*";
    lblC.appendChild(starC);
    grpC.appendChild(lblC);
    var rowC = mkEl("div", "pcorg-canal-row");
    CANAUX.forEach(function (c) {
      var b = mkEl("button", "pcorg-canal-btn");
      b.type = "button";
      b.setAttribute("data-canal", c.id);
      b.appendChild(matIcon(c.icon, "pcorg-canal-btn-ico"));
      var lab = mkEl("span", ""); lab.textContent = c.label;
      b.appendChild(lab);
      b.addEventListener("click", function () { selectCanal(c.id); });
      rowC.appendChild(b);
    });
    grpC.appendChild(rowC);
    var canalDet = mkEl("div", "pcorg-canal-detail");
    canalDet.id = "pcorg-c-canal-detail";
    canalDet.style.display = "none";
    var canalInp = mkEl("input", "form-input");
    canalInp.id = "pcorg-c-canal-detail-input";
    canalInp.placeholder = "Canal radio (ex: 3, Sécu, Maintenance...)";
    canalDet.appendChild(canalInp);
    grpC.appendChild(canalDet);
    container.appendChild(grpC);

    // Re-applique canal en cours si compatible
    if (createCanal) selectCanal(createCanal, true);
  }

  function selectCanal(canalId, keepValue) {
    createCanal = canalId;
    document.querySelectorAll(".pcorg-canal-btn").forEach(function (b) {
      b.classList.toggle("selected", b.getAttribute("data-canal") === canalId);
    });
    var det = document.getElementById("pcorg-c-canal-detail");
    var inp = document.getElementById("pcorg-c-canal-detail-input");
    if (det && inp) {
      if (canalId === "radio") {
        det.style.display = "";
        if (!keepValue) inp.value = "";
      } else {
        det.style.display = "none";
        if (!keepValue) inp.value = "";
      }
    }
  }

  function validateSourceFields() {
    if (!createSource) return { ok: false, msg: "Selectionnez la source de la fiche" };
    if (createSource === "initiative") return { ok: true };
    var qId = createSource === "externe" ? "pcorg-c-appelant"
            : createSource === "operateur" ? "pcorg-c-emetteur"
            : "pcorg-c-donneur";
    var v = (document.getElementById(qId) || {}).value;
    if (!v || !v.trim()) {
      var label = createSource === "externe" ? "L'appelant"
                : createSource === "operateur" ? "L'opérateur émetteur"
                : "Le donneur d'ordre";
      return { ok: false, msg: label + " est obligatoire" };
    }
    if (!createCanal) return { ok: false, msg: "Selectionnez le canal" };
    return { ok: true };
  }

  var createSelectedUrgency = "";
  var createFieldsCtl = null;   // retour de buildCategoryFields (collect)
  var createClientToken = "";   // un par ouverture de l'assistant (anti double clic)
  var createSubmitting = false;

  function buildSpecificCreateFields(cat) {
    var container = document.getElementById("pcorg-create-specific");
    var prevComment = getVal("pcorg-c-comment");
    container.textContent = "";

    var urgCats = (pcorgConfig && pcorgConfig.urgence_categories) || {};
    var urgRow; // shared by appendUrgency / updateUrgencyCreateBtns

    function appendUrgency() {
      if (!urgCats[cat]) { createSelectedUrgency = ""; return; }
      var presetUrgency = createSelectedUrgency || "";
      var urgGrp = mkEl("div", "form-group");
      var urgLbl = mkEl("label", ""); urgLbl.textContent = "Niveau d'urgence";
      urgGrp.appendChild(urgLbl);
      urgRow = mkEl("div", "pcorg-create-cats");
      urgRow.id = "pcorg-create-urgency-btns";
      var uType = urgencyType(cat);
      var noneBtnC = mkEl("button", "pcorg-create-cat-btn" + (!presetUrgency ? " selected" : ""));
      noneBtnC.type = "button";
      noneBtnC.style.cssText = !presetUrgency ? "border-color:var(--muted);background:var(--muted);font-size:0.75rem" : "font-size:0.75rem";
      noneBtnC.setAttribute("data-urgency", "");
      noneBtnC.textContent = "Aucun";
      noneBtnC.addEventListener("click", function () {
        createSelectedUrgency = "";
        updateUrgBtns();
      });
      urgRow.appendChild(noneBtnC);
      URGENCY_LEVELS.forEach(function (lvl) {
        var c = URGENCY_COLORS[lvl];
        var isSel = presetUrgency === lvl;
        var btnU = mkEl("button", "pcorg-create-cat-btn" + (isSel ? " selected" : ""));
        btnU.type = "button";
        btnU.setAttribute("data-urgency", lvl);
        if (isSel) { btnU.style.borderColor = c; btnU.style.background = c; }
        var dotU = mkEl("span", ""); dotU.style.cssText = "width:8px;height:8px;border-radius:50%;background:" + c;
        btnU.appendChild(dotU);
        var lblU = mkEl("span", ""); lblU.style.fontSize = "0.72rem";
        lblU.textContent = URGENCY_LABELS[uType][lvl];
        btnU.appendChild(lblU);
        btnU.addEventListener("click", function () {
          createSelectedUrgency = lvl;
          updateUrgBtns();
        });
        urgRow.appendChild(btnU);
      });
      urgGrp.appendChild(urgRow);
      container.appendChild(urgGrp);
      function updateUrgBtns() {
        urgRow.querySelectorAll(".pcorg-create-cat-btn").forEach(function (b) {
          var val = b.getAttribute("data-urgency");
          var sel = val === createSelectedUrgency;
          b.classList.toggle("selected", sel);
          if (val) {
            b.style.borderColor = sel ? URGENCY_COLORS[val] : "";
            b.style.background = sel ? URGENCY_COLORS[val] : "";
          } else {
            b.style.borderColor = sel ? "var(--muted)" : "";
            b.style.background = sel ? "var(--muted)" : "";
          }
        });
      }
    }

    function appendComment() {
      var sec = mkEl("div", "pcorg-fiche-section"); sec.textContent = "Action prise";
      container.appendChild(sec);
      var grp = mkEl("div", "form-group");
      var lbl = mkEl("label", ""); lbl.setAttribute("for", "pcorg-c-comment");
      lbl.textContent = "Consignez l'action ou l'observation ";
      var star = mkEl("span", ""); star.style.color = "var(--danger,#ef4444)"; star.textContent = "*";
      lbl.appendChild(star);
      grp.appendChild(lbl);
      var ta = mkEl("textarea", "form-input"); ta.id = "pcorg-c-comment"; ta.rows = 3;
      ta.required = true; ta.placeholder = "Action realisee, observation, consigne...";
      grp.appendChild(ta);

      // Bouton capture camera
      var camRow = mkEl("div", "pcorg-create-cam-row");
      var camBtn = mkEl("button", "pcorg-create-cam-btn");
      camBtn.type = "button";
      camBtn.appendChild(matIcon("videocam"));
      camBtn.appendChild(document.createTextNode(" Joindre une capture camera"));
      camBtn.addEventListener("click", function () {
        openCameraPicker(function (camId, camName) {
          camBtn.disabled = true;
          camBtn.textContent = "";
          camBtn.appendChild(matIcon("hourglass_top"));
          camBtn.appendChild(document.createTextNode(" Capture " + camName + "..."));
          // Capture sans fiche (pas encore creee)
          apiCall("POST", "/api/pcorg/camera-capture", { cam_id: camId })
            .then(function (r) {
              camBtn.disabled = false;
              if (r.ok) {
                _createCameraPhoto = { url: r.photo, cam_name: r.cam_name };
                camBtn.textContent = "";
                camBtn.appendChild(matIcon("check_circle"));
                camBtn.appendChild(document.createTextNode(" " + r.cam_name));
                // Afficher preview
                var existingPreview = container.querySelector(".pcorg-create-cam-preview");
                if (existingPreview) existingPreview.remove();
                var preview = mkEl("div", "pcorg-create-cam-preview");
                var img = mkEl("img", "");
                img.src = r.photo;
                img.alt = "Capture " + r.cam_name;
                preview.appendChild(img);
                var removeBtn = mkEl("button", "pcorg-create-cam-remove");
                removeBtn.type = "button";
                removeBtn.appendChild(matIcon("close"));
                removeBtn.addEventListener("click", function () {
                  _createCameraPhoto = null;
                  preview.remove();
                  camBtn.textContent = "";
                  camBtn.appendChild(matIcon("videocam"));
                  camBtn.appendChild(document.createTextNode(" Joindre une capture camera"));
                });
                preview.appendChild(removeBtn);
                grp.appendChild(preview);
                showToast("success", "Capture " + r.cam_name + " jointe");
              } else {
                camBtn.textContent = "";
                camBtn.appendChild(matIcon("videocam"));
                camBtn.appendChild(document.createTextNode(" Joindre une capture camera"));
                showToast("error", r.error || "Erreur capture");
              }
            })
            .catch(function () {
              camBtn.disabled = false;
              camBtn.textContent = "";
              camBtn.appendChild(matIcon("videocam"));
              camBtn.appendChild(document.createTextNode(" Joindre une capture camera"));
              showToast("error", "Camera injoignable ou erreur reseau");
            });
        });
      });
      camRow.appendChild(camBtn);

      // Photos prises sur le moment : jointes a la fiche juste apres sa creation
      var phBtn = mkEl("button", "pcorg-create-cam-btn");
      phBtn.type = "button";
      phBtn.appendChild(matIcon("add_a_photo"));
      phBtn.appendChild(document.createTextNode(" Joindre une photo"));
      var phWrap = mkEl("div", "pcorg-create-photos");
      function renderCreatePhotos() {
        phWrap.textContent = "";
        _createPhotos.forEach(function (p, i) {
          var cell = mkEl("div", "pcorg-create-cam-preview");
          var img = mkEl("img", "");
          img.src = p.url;
          img.alt = "Photo " + (i + 1);
          cell.appendChild(img);
          var rm = mkEl("button", "pcorg-create-cam-remove");
          rm.type = "button";
          rm.appendChild(matIcon("close"));
          rm.addEventListener("click", function () {
            try { URL.revokeObjectURL(p.url); } catch (e) { /* deja libere */ }
            _createPhotos.splice(i, 1);
            renderCreatePhotos();
          });
          cell.appendChild(rm);
          phWrap.appendChild(cell);
        });
        phBtn.style.display = _createPhotos.length >= PHOTO_MAX_BATCH ? "none" : "";
      }
      phBtn.addEventListener("click", function () {
        pickPhotos(function (blobs) {
          blobs.forEach(function (b) { _createPhotos.push({ blob: b, url: URL.createObjectURL(b) }); });
          renderCreatePhotos();
        }, PHOTO_MAX_BATCH - _createPhotos.length);
      });
      camRow.appendChild(phBtn);
      grp.appendChild(camRow);
      grp.appendChild(phWrap);
      renderCreatePhotos();
      container.appendChild(grp);
    }

    // Champs de la categorie : constructeur commun avec l'edition. Les
    // valeurs deja saisies survivent a un changement de categorie.
    var prevValues = createFieldsCtl ? createFieldsCtl.collect() : {};
    if (createPendingPatrouille) {
      prevValues.patrouille = createPendingPatrouille;
      createPendingPatrouille = "";
    }
    createFieldsCtl = buildCategoryFields(container, cat, prevValues, "pcorg-c-",
      { urgency: appendUrgency, comment: appendComment });
    if (prevComment) {
      var ta = document.getElementById("pcorg-c-comment");
      if (ta) ta.value = prevComment;
    }
  }

  function submitCreate() {
    // Validate action prise
    var comment = getVal("pcorg-c-comment");
    if (!comment) {
      showToast("warning", "L'action prise est obligatoire");
      return;
    }
    if (createSubmitting) return;
    var specValues = createFieldsCtl ? createFieldsCtl.collect() : {};
    // Validate sous-classification
    if (createFieldsCtl && createFieldsCtl.requiresSous && !specValues.sous_classification) {
      showToast("warning", "La sous-classification est obligatoire");
      return;
    }
    var text = document.getElementById("pcorg-c-text").value.trim();
    var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};

    // Build content_category
    var cc = {};
    // Source de la fiche (4 types). Backwards compat : on remplit aussi
    // appelant/telephone/radio quand pertinent pour la lecture par les anciens consumers.
    if (createSource) cc.source_type = createSource;
    if (createSource === "initiative") {
      var origine = getVal("pcorg-c-source-origine");
      if (origine) cc.source_origine = origine;
    } else if (createSource === "externe") {
      var appelant = getVal("pcorg-c-appelant");
      if (appelant) cc.appelant = appelant;
    } else if (createSource === "operateur") {
      var emetteur = getVal("pcorg-c-emetteur");
      if (emetteur) {
        cc.emetteur_interne = emetteur;
        cc.appelant = emetteur; // compat
      }
    } else if (createSource === "hierarchie") {
      var donneur = getVal("pcorg-c-donneur");
      if (donneur) {
        cc.donneur_ordre = donneur;
        cc.appelant = donneur; // compat
      }
    }
    if (createSource && createSource !== "initiative") {
      cc.canal = createCanal;
      if (createCanal === "telephone") {
        cc.telephone = true;
      } else if (createCanal === "radio") {
        var canalDet = getVal("pcorg-c-canal-detail-input");
        cc.radio = canalDet || true;
        if (canalDet) cc.radio_canal = canalDet;
      } else if (createCanal === "presentiel") {
        cc.presentiel = true;
      } else if (createCanal === "mail") {
        cc.mail = true;
      }
    }

    // Carroyage from step 1
    if (createCarroye) cc.carroye = createCarroye;

    // Champs propres a la categorie (dont sous-classification et vehicule)
    Object.keys(specValues).forEach(function (k) {
      if (specValues[k]) cc[k] = specValues[k];
    });
    var cat = createSelectedCat;

    var payload = {
      event: ey.event,
      year: ey.year,
      category: cat,
      text: text,
      area_desc: createAreaDesc,
      content_category: cc,
      comment: getVal("pcorg-c-comment"),
      niveau_urgence: createSelectedUrgency || null,
      lat: createLat,
      lon: createLon,
      client_token: createClientToken || randomToken()
    };

    if (createInterventionTs) {
      payload.intervention_ts = _toLocalInputValue(createInterventionTs) + ":00";
    }

    var pendingPhoto = _createCameraPhoto;
    var pendingPhotos = _createPhotos.slice();
    var submitBtn = document.getElementById("pcorgCreateSubmit");

    function _doSubmit() {
      // Garde double clic : bouton desactive + jeton client (le serveur
      // retombe sur la meme fiche si la requete est rejouee)
      createSubmitting = true;
      if (submitBtn) submitBtn.disabled = true;
      apiCall("POST", "/api/pcorg/create", payload)
        .then(function (r) {
          if (!r.ok) {
            createSubmitting = false;
            if (submitBtn) submitBtn.disabled = false;
            showToast("error", r.error || "Erreur");
            return null;
          }
          // Photo camera jointe : attachee AVANT le rafraichissement
          if (pendingPhoto && r.id && !r.duplicate) {
            return apiCall("POST", "/api/pcorg/comment/" + encodeURIComponent(r.id), {
              text: "Capture camera " + pendingPhoto.cam_name,
              photo: pendingPhoto.url
            }).then(function (rc) {
              if (!rc.ok) showToast("warning", "Fiche creee, mais la photo n'a pas pu etre jointe");
              return r;
            });
          }
          return r;
        })
        .then(function (r) {
          // Photos prises sur le moment
          if (r && r.id && !r.duplicate && pendingPhotos.length) {
            return uploadFichePhotos(r.id, pendingPhotos.map(function (p) { return p.blob; }), "")
              .then(function (rp) {
                if (!rp.ok) showToast("warning", "Fiche creee, mais les photos n'ont pas pu etre jointes : " + (rp.error || "erreur"));
                return r;
              });
          }
          return r;
        })
        .then(function (r) {
          if (!r) return;
          createSubmitting = false;
          if (submitBtn) submitBtn.disabled = false;
          hideCreate();
          showToast("success", "Intervention creee");
          if (!createOnlyMode) refresh();
          if (typeof createHooks.onCreated === "function") {
            try { createHooks.onCreated(r.id); } catch (e) { /* page appelante */ }
          }
        });
    }

    // Heure d'intervention personnalisee : confirm si antidatage > 24h
    if (createInterventionTs && (Date.now() - createInterventionTs.getTime()) / 3600000 > 24) {
      var fmt = _pad2(createInterventionTs.getDate()) + "/" + _pad2(createInterventionTs.getMonth() + 1)
        + " a " + _fmtTimeHM(createInterventionTs);
      showConfirmToast("Creer une fiche datee du " + fmt + " ?", { okLabel: "Creer", type: "warning" })
        .then(function (ok) { if (ok) _doSubmit(); });
      return;
    }

    _doSubmit();
  }

  function getVal(id) {
    var el = document.getElementById(id);
    return el ? el.value.trim() : "";
  }

  // ── GPS modal ──────────────────────────────────────────────────────────────
  function initGpsModal() {
    var modal = document.getElementById("pcorgGpsModal");
    var closeBtn = document.getElementById("pcorgGpsClose");
    var cancelBtn = document.getElementById("pcorgGpsCancel");
    var saveBtn = document.getElementById("pcorgGpsSave");
    var pickBtn = document.getElementById("pcorg-gps-pick");

    if (!modal) return;

    closeBtn.addEventListener("click", function () { modal.classList.remove("show"); });
    cancelBtn.addEventListener("click", function () { modal.classList.remove("show"); });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.classList.remove("show");
    });

    pickBtn.addEventListener("click", function () {
      modal.classList.remove("show");
      startGpsPick(function (lat, lon) {
        document.getElementById("pcorg-gps-lat").value = lat.toFixed(6);
        document.getElementById("pcorg-gps-lon").value = lon.toFixed(6);
        modal.classList.add("show");
      });
    });

    saveBtn.addEventListener("click", function () {
      var id = document.getElementById("pcorg-gps-id").value;
      var lat = document.getElementById("pcorg-gps-lat").value;
      var lon = document.getElementById("pcorg-gps-lon").value;
      if (!lat || !lon) {
        showToast("warning", "Latitude et longitude requises");
        return;
      }
      var fLat = parseFloat(String(lat).replace(",", "."));
      var fLon = parseFloat(String(lon).replace(",", "."));
      if (isNaN(fLat) || isNaN(fLon) || Math.abs(fLat) > 90 || Math.abs(fLon) > 180) {
        showToast("warning", "Coordonnees invalides");
        return;
      }
      saveBtn.disabled = true;
      // Zone et carroyage recalcules au nouveau point (avant : conserves,
      // donc faux apres un deplacement)
      apiCall("POST", "/api/pcorg/update-gps/" + encodeURIComponent(id), {
        lat: fLat,
        lon: fLon,
        area_desc: resolveZone(fLat, fLon),
        carroye: resolveCellLabel(fLat, fLon)
      }).then(function (r) {
        saveBtn.disabled = false;
        if (r.ok) {
          modal.classList.remove("show");
          showToast("success", "Position enregistree");
          refresh();
        } else {
          showToast("error", r.error || "Erreur");
        }
      });
    });
  }

  function openGpsModal(id, lat, lon) {
    var modal = document.getElementById("pcorgGpsModal");
    if (!modal) return;
    document.getElementById("pcorg-gps-id").value = id;
    document.getElementById("pcorg-gps-lat").value = lat != null ? Number(lat).toFixed(6) : "";
    document.getElementById("pcorg-gps-lon").value = lon != null ? Number(lon).toFixed(6) : "";
    modal.classList.add("show");
  }

  // ── GPS pick on map ────────────────────────────────────────────────────────
  var _gpsPickCleanup = null;

  function startGpsPick(callback) {
    ensureMapVisible();
    var map = getMap();
    if (!map) {
      showToast("warning", "Carte non disponible");
      return;
    }
    // Un seul mode "pointage" a la fois : relancer ne laisse plus d'ecouteur orphelin
    if (_gpsPickCleanup) _gpsPickCleanup();
    pickCallback = callback;
    document.body.classList.add("pcorg-pick-active");
    showToast("info", "Cliquez sur la carte pour placer le point (Echap pour annuler)");

    function cleanup() {
      document.body.classList.remove("pcorg-pick-active");
      map.off("click", onMapClick);
      document.removeEventListener("keydown", onEsc, true);
      _gpsPickCleanup = null;
    }
    function onMapClick(e) {
      var cb = pickCallback;
      pickCallback = null;
      cleanup();
      if (cb) cb(e.latlng.lat, e.latlng.lng);
    }
    // Sur le document (et non la carte) : Echap marche meme si la carte n'a pas le focus
    function onEsc(e) {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      pickCallback = null;
      cleanup();
      var modal = document.getElementById("pcorgGpsModal");
      if (modal) modal.classList.add("show");
    }
    map.on("click", onMapClick);
    document.addEventListener("keydown", onEsc, true);
    _gpsPickCleanup = cleanup;
  }

  // ── Expanded list panel (zone centrale, meme pattern que meteo) ──────────

  var expPanel, expBody, expSearch, expCount;
  var expFilter = "all";
  var _expPreviousView = null;

  // Pagination + recherche serveur
  var expClosedExtra = [];          // fiches closed chargees en plus via /api/pcorg/closed
  var expClosedTotal = 0;           // total cote serveur
  var expClosedPageSize = 100;
  var expClosedLoading = false;
  var expClosedHasMore = false;

  var expSearchActive = false;      // true si on est en mode recherche serveur
  var expSearchResults = null;      // { open: [...], closed: [...] } ou null
  var expSearchQuery = "";
  var expSearchSeq = 0;             // anti-race (chaque appel incremente)
  var expSearchTimer = null;
  var expSearchLoading = false;

  // ── Colonnes du panneau elargi : largeurs redimensionnables ──────────────
  // w = largeur par defaut (px) ; null = colonne elastique (prend le reste).
  // Le tableau a une largeur minimale : en dessous, defilement horizontal
  // (ecran de portable 15" : le tableau etait coupe sans pouvoir defiler).
  var EXP_COLUMNS = [
    { key: "status", label: "", title: "Statut", w: 34, fixed: true },
    { key: "cat", label: "Categorie", w: 140 },
    { key: "desc", label: "Description", w: null, min: 220 },
    { key: "zone", label: "Zone", w: 120 },
    { key: "op", label: "Operateur", w: 120 },
    { key: "open", label: "Ouverture", w: 88 },
    { key: "close", label: "Cloture", w: 88 },
    { key: "loc", label: "", title: "Localiser", w: 36, fixed: true }
  ];
  var EXP_COLW_KEY = "pcorg-exp-colw";

  function loadExpColWidths() {
    try { return JSON.parse(localStorage.getItem(EXP_COLW_KEY)) || {}; } catch (e) { return {}; }
  }
  function saveExpColWidths(w) {
    try { localStorage.setItem(EXP_COLW_KEY, JSON.stringify(w)); } catch (e) {}
  }

  function expColWidth(col, saved) {
    return saved[col.key] || col.w;
  }

  function applyExpMinWidth(table, saved) {
    var total = 0;
    EXP_COLUMNS.forEach(function (c) {
      var w = expColWidth(c, saved);
      total += w || c.min || 200;
    });
    table.style.minWidth = total + "px";
  }

  function buildExpHeader(table) {
    var saved = loadExpColWidths();
    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    EXP_COLUMNS.forEach(function (col) {
      var th = document.createElement("th");
      if (col.key === "status") th.className = "pcorg-exp-th-status";
      th.textContent = col.label;
      if (col.title) th.title = col.title;
      var w = expColWidth(col, saved);
      if (w) th.style.width = w + "px";
      if (!col.fixed) {
        // Poignee de redimensionnement (double-clic : largeur par defaut)
        var handle = mkEl("span", "pcorg-exp-resize");
        handle.title = "Glisser pour redimensionner, double-clic pour reinitialiser";
        handle.addEventListener("pointerdown", function (e) {
          e.preventDefault(); e.stopPropagation();
          var startX = e.clientX;
          var startW = th.getBoundingClientRect().width;
          document.body.classList.add("pcorg-exp-resizing");
          function onMove(ev) {
            var nw = Math.max(50, Math.round(startW + ev.clientX - startX));
            th.style.width = nw + "px";
            var cur = loadExpColWidths();
            cur[col.key] = nw;
            applyExpMinWidth(table, cur);
            th._pendingWidth = nw;
          }
          function onUp() {
            document.removeEventListener("pointermove", onMove);
            document.removeEventListener("pointerup", onUp);
            document.body.classList.remove("pcorg-exp-resizing");
            if (th._pendingWidth) {
              var cur = loadExpColWidths();
              cur[col.key] = th._pendingWidth;
              saveExpColWidths(cur);
              th._pendingWidth = null;
            }
          }
          document.addEventListener("pointermove", onMove);
          document.addEventListener("pointerup", onUp);
        });
        handle.addEventListener("dblclick", function (e) {
          e.stopPropagation();
          var cur = loadExpColWidths();
          delete cur[col.key];
          saveExpColWidths(cur);
          th.style.width = col.w ? col.w + "px" : "";
          applyExpMinWidth(table, cur);
        });
        handle.addEventListener("click", function (e) { e.stopPropagation(); });
        th.appendChild(handle);
      }
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    applyExpMinWidth(table, saved);
  }

  // Filtre par categorie du panneau elargi (cree en JS, a cote de la recherche)
  var expCatFilter = "";
  var expCatSelect = null;

  function ensureExpCatFilter() {
    if (expCatSelect || !expSearch || !expSearch.parentNode) return;
    expCatSelect = mkEl("select", "pcorg-exp-catfilter");
    expCatSelect.title = "Filtrer par categorie";
    var o0 = mkEl("option", ""); o0.value = ""; o0.textContent = "Toutes categories";
    expCatSelect.appendChild(o0);
    CATEGORY_ORDER.concat(Object.keys(PCS_EQUIVALENT).filter(function (pcs) {
      return CATEGORY_ORDER.indexOf(PCS_EQUIVALENT[pcs]) !== -1;
    })).forEach(function (cat) {
      var o = mkEl("option", ""); o.value = cat; o.textContent = shortCat(cat);
      expCatSelect.appendChild(o);
    });
    expCatSelect.addEventListener("change", function () {
      expCatFilter = expCatSelect.value;
      if (expBody) expBody.scrollTop = 0;
      renderExpanded();
    });
    expSearch.parentNode.insertBefore(expCatSelect, expSearch);
  }

  function initExpandedPanel() {
    expPanel = document.getElementById("pcorg-expanded-panel");
    expBody = document.getElementById("pcorg-expanded-body");
    expSearch = document.getElementById("pcorg-expanded-search");
    expCount = document.getElementById("pcorg-expanded-count");
    if (!expPanel) return;

    var expandBtn = document.getElementById("pcorg-expand-btn");
    var closeBtn = document.getElementById("pcorg-expanded-close");

    if (expandBtn) expandBtn.addEventListener("click", toggleExpanded);
    if (closeBtn) closeBtn.addEventListener("click", closeExpanded);

    // Filter tabs
    var tabs = expPanel.querySelectorAll(".pcorg-exp-tab");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        expFilter = tab.getAttribute("data-filter");
        if (expBody) expBody.scrollTop = 0;
        if (expSearchActive) {
          // relance la recherche serveur avec le nouveau filtre status
          triggerExpandedSearch(expSearchQuery, true);
        } else {
          renderExpanded();
        }
      });
    });

    // Search (debounce 300ms, recherche serveur si q.length >= 2)
    if (expSearch) {
      expSearch.addEventListener("input", function () {
        var raw = (expSearch.value || "").trim();
        if (expSearchTimer) clearTimeout(expSearchTimer);
        if (raw.length < 2) {
          // fin de recherche serveur, retour aux donnees locales
          expSearchActive = false;
          expSearchResults = null;
          expSearchQuery = "";
          expSearchLoading = false;
          renderExpanded();
          return;
        }
        expSearchTimer = setTimeout(function () {
          triggerExpandedSearch(raw, false);
        }, 300);
      });
    }

    // Infinite scroll sur les fiches cloturees
    if (expBody) {
      expBody.addEventListener("scroll", onExpScroll);
    }
  }

  function toggleExpanded() {
    if (expPanel && expPanel.style.display !== "none") {
      closeExpanded();
      return;
    }
    openExpanded();
  }

  function openExpanded() {
    if (!expPanel) return;
    if (!lastData) {
      showToast("info", "Main courante en cours de chargement...");
      refresh();
      return;
    }
    // Tableau de bord controle d'acces : le fermer par son propre bouton
    // (liberation des graphiques) pour ne pas empiler deux panneaux
    var counters = document.getElementById("counters-panel");
    if (counters && counters.style.display !== "none" && counters.style.display !== "") {
      var cClose = document.getElementById("counters-panel-close");
      if (cClose) cClose.click();
    }
    var timeline = document.getElementById("timeline-main");
    var mapMain = document.getElementById("map-main");
    var meteoPanel = document.getElementById("meteo-panel");

    // Sauvegarder la vue precedente
    if (window.CockpitMapView) {
      _expPreviousView = window.CockpitMapView.currentView();
    } else {
      _expPreviousView = (mapMain && mapMain.style.display !== "none") ? "map" : "timeline";
    }

    // Fermer le panel meteo s'il est ouvert
    if (meteoPanel && meteoPanel.style.display !== "none" && window.MeteoPanel) {
      window.MeteoPanel.collapse();
    }

    // Fermer le panel affluence s'il est ouvert
    var affPanel = document.getElementById("affluence-panel");
    if (affPanel && affPanel.style.display !== "none") {
      affPanel.style.display = "none";
      var affBtn = document.getElementById("affluence-expand-btn");
      if (affBtn) affBtn.querySelector(".material-symbols-outlined").textContent = "open_in_full";
    }

    if (timeline) timeline.style.display = "none";
    if (mapMain) mapMain.style.display = "none";
    expPanel.style.display = "flex";

    var expandBtn = document.getElementById("pcorg-expand-btn");
    if (expandBtn) expandBtn.querySelector(".material-symbols-outlined").textContent = "close_fullscreen";

    if (expSearch) expSearch.value = "";
    expFilter = "all";
    var tabs = expPanel.querySelectorAll(".pcorg-exp-tab");
    tabs.forEach(function (t) { t.classList.toggle("active", t.getAttribute("data-filter") === "all"); });

    // Reset pagination + recherche
    expClosedExtra = [];
    expClosedTotal = (lastData && lastData.counts && typeof lastData.counts.closed_total === "number")
      ? lastData.counts.closed_total
      : (lastData && lastData.closed ? lastData.closed.length : 0);
    expClosedPageSize = (lastData && lastData.closed_page_size) || 100;
    expClosedHasMore = (lastData && lastData.closed ? lastData.closed.length : 0) < expClosedTotal;
    expClosedLoading = false;
    expSearchActive = false;
    expSearchResults = null;
    expSearchQuery = "";
    expSearchLoading = false;
    if (expSearchTimer) { clearTimeout(expSearchTimer); expSearchTimer = null; }

    renderExpanded();
    setTimeout(function () { if (expSearch) expSearch.focus(); }, 100);
  }

  function onExpScroll() {
    if (!expBody) return;
    if (expSearchActive) return;          // pagination desactivee en mode recherche serveur
    if (expFilter === "open") return;     // rien a paginer pour les fiches ouvertes
    if (!expClosedHasMore || expClosedLoading) return;
    var threshold = 200;
    if (expBody.scrollTop + expBody.clientHeight >= expBody.scrollHeight - threshold) {
      loadMoreClosed();
    }
  }

  function loadMoreClosed() {
    var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};
    if (!ey.event || !ey.year) return;
    var loaded = allLoadedClosed();
    if (loaded.length >= expClosedTotal) {
      expClosedHasMore = false;
      renderExpanded();
      return;
    }
    // Pagination par curseur (derniere cloture chargee) : l'offset glissait
    // a chaque nouvelle cloture et produisait doublons ou trous
    var last = loaded[loaded.length - 1];
    expClosedLoading = true;
    renderExpanded();
    var url = "/api/pcorg/closed?event=" + encodeURIComponent(ey.event)
      + "&year=" + encodeURIComponent(ey.year)
      + "&limit=" + expClosedPageSize
      + (last && last.close_ts
        ? "&before_ts=" + encodeURIComponent(last.close_ts) + "&before_id=" + encodeURIComponent(last.id)
        : "&offset=" + loaded.length);
    fetch(url, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        expClosedLoading = false;
        if (!data || !Array.isArray(data.items)) return;
        var known = {};
        allLoadedClosed().forEach(function (it) { known[it.id] = true; });
        expClosedExtra = expClosedExtra.concat(data.items.filter(function (it) { return !known[it.id]; }));
        if (typeof data.total === "number") expClosedTotal = data.total;
        expClosedHasMore = !!data.has_more;
        renderExpanded();
      })
      .catch(function () {
        expClosedLoading = false;
        renderExpanded();
      });
  }

  // Fiches closes connues : page initiale (/live) + pages chargees, sans doublon
  function allLoadedClosed() {
    var seen = {};
    var out = [];
    ((lastData && lastData.closed) || []).concat(expClosedExtra || []).forEach(function (it) {
      if (seen[it.id]) return;
      seen[it.id] = true;
      out.push(it);
    });
    return out;
  }

  function triggerExpandedSearch(q, force) {
    var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};
    if (!ey.event || !ey.year) return;
    if (!force && q === expSearchQuery && expSearchResults) return;
    expSearchActive = true;
    expSearchQuery = q;
    expSearchLoading = true;
    var seq = ++expSearchSeq;
    renderExpanded();
    var url = "/api/pcorg/search?event=" + encodeURIComponent(ey.event)
      + "&year=" + encodeURIComponent(ey.year)
      + "&status=" + encodeURIComponent(expFilter)
      + "&q=" + encodeURIComponent(q);
    fetch(url, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (seq !== expSearchSeq) return;   // resultat obsolete
        expSearchLoading = false;
        if (!data || data.error) {
          expSearchResults = { open: [], closed: [] };
        } else {
          expSearchResults = { open: data.open || [], closed: data.closed || [] };
        }
        renderExpanded();
      })
      .catch(function () {
        if (seq !== expSearchSeq) return;
        expSearchLoading = false;
        expSearchResults = { open: [], closed: [] };
        renderExpanded();
      });
  }

  function closeExpanded() {
    if (expPanel) expPanel.style.display = "none";
    var expandBtn = document.getElementById("pcorg-expand-btn");
    if (expandBtn) expandBtn.querySelector(".material-symbols-outlined").textContent = "open_in_full";
    var timeline = document.getElementById("timeline-main");
    var mapMain = document.getElementById("map-main");

    if (_expPreviousView === "map") {
      if (timeline) timeline.style.display = "none";
      if (mapMain) mapMain.style.display = "block";
    } else {
      if (timeline) timeline.style.display = "";
      if (mapMain) mapMain.style.display = "none";
    }
  }

  function renderExpanded() {
    if (!expBody || !lastData) return;

    var openItems, closedItems;
    if (expSearchActive && expSearchResults) {
      openItems = (expSearchResults.open || []).map(function (it) { it._open = true; return it; });
      closedItems = (expSearchResults.closed || []).map(function (it) { it._open = false; return it; });
    } else {
      openItems = (lastData.open || []).map(function (it) { it._open = true; return it; });
      var openIds = {};
      openItems.forEach(function (it) { openIds[it.id] = true; });
      // Une fiche rouverte depuis ne figure plus parmi les closes
      closedItems = allLoadedClosed().filter(function (it) { return !openIds[it.id]; })
        .map(function (it) { it._open = false; return it; });
    }

    var items;
    if (expFilter === "open") items = openItems;
    else if (expFilter === "closed") items = closedItems;
    else items = openItems.concat(closedItems);
    ensureExpCatFilter();
    if (expCatFilter) {
      items = items.filter(function (it) { return it.category === expCatFilter; });
      openItems = openItems.filter(function (it) { return it.category === expCatFilter; });
      closedItems = closedItems.filter(function (it) { return it.category === expCatFilter; });
    }
    // Tri : en cours d'abord, puis terminees ; dans chaque groupe, heure
    // d'ouverture de la plus recente a la plus ancienne (les closes etaient
    // triees par date de cloture)
    function tsMs(it) { var t = it.ts ? new Date(it.ts).getTime() : NaN; return isNaN(t) ? -Infinity : t; }
    items = items.slice().sort(function (a, b) {
      if (a._open !== b._open) return a._open ? -1 : 1;
      return tsMs(b) - tsMs(a);
    });

    // Le rafraichissement (60 s) ne ramene plus en haut du tableau
    var prevScrollTop = expBody.scrollTop;
    expBody.textContent = "";

    // Bandeau loading recherche
    if (expSearchActive && expSearchLoading) {
      var loadingBanner = mkEl("div", "pcorg-exp-loading");
      loadingBanner.textContent = "Recherche en cours...";
      expBody.appendChild(loadingBanner);
    }

    if (items.length === 0) {
      if (expSearchActive && expSearchLoading) {
        // on a deja affiche le bandeau, on s'arrete la
        if (expCount) expCount.textContent = "";
        return;
      }
      var empty = mkEl("div", "widget-placeholder");
      empty.style.padding = "60px 0";
      var emptyIco = matIcon("search_off", "");
      emptyIco.style.fontSize = "40px";
      empty.appendChild(emptyIco);
      var emptyTxt = mkEl("span", "");
      emptyTxt.textContent = expSearchActive
        ? "Aucun resultat pour \"" + expSearchQuery + "\""
        : "Aucune intervention";
      empty.appendChild(emptyTxt);
      expBody.appendChild(empty);
      if (expCount) expCount.textContent = "";
      return;
    }

    var table = mkEl("table", "pcorg-exp-table");
    buildExpHeader(table);

    var tbody = document.createElement("tbody");
    items.forEach(function (item) {
      var st = catStyle(item.category);
      var tr = document.createElement("tr");
      tr.setAttribute("data-id", item.id);
      if (activeFicheId === item.id) tr.classList.add("pcorg-row-active");

      // Statut
      // Statut en icone (le badge texte prenait une colonne de 90 px)
      var tdStatus = mkEl("td", "pcorg-exp-td-status");
      var statusIco = matIcon(item._open ? "radio_button_checked" : "check_circle",
        "pcorg-exp-status-ico " + (item._open ? "open" : "closed"));
      statusIco.title = item._open ? "En cours" : "Termin\u00e9e";
      tdStatus.appendChild(statusIco);
      tr.appendChild(tdStatus);

      // Categorie
      var tdCat = document.createElement("td");
      var catEl = mkEl("span", "pcorg-exp-cat");
      catEl.style.color = st.color;
      var catIco = matIcon(st.icon, "");
      catIco.style.color = st.color;
      catEl.appendChild(catIco);
      var catTxt = document.createTextNode(shortCat(item.category));
      catEl.appendChild(catTxt);
      tdCat.appendChild(catEl);
      if (item.sous_classification) {
        var scEl = mkEl("div", "");
        scEl.style.fontSize = "0.65rem";
        scEl.style.color = "var(--muted)";
        scEl.textContent = item.sous_classification;
        tdCat.appendChild(scEl);
      }
      if (item.niveau_urgence) {
        var urgWrapExp = mkEl("div", "");
        urgWrapExp.style.marginTop = "2px";
        var urgBadgeExp = mkEl("span", "pcorg-urgency-badge pcorg-urgency-" + item.niveau_urgence);
        urgBadgeExp.style.marginLeft = "0";
        urgBadgeExp.textContent = urgencyLabel(item.category, item.niveau_urgence);
        urgWrapExp.appendChild(urgBadgeExp);
        tdCat.appendChild(urgWrapExp);
      }
      tr.appendChild(tdCat);

      // Description
      var tdDesc = document.createElement("td");
      var descEl = mkEl("span", "pcorg-exp-desc");
      descEl.textContent = item.text || "(sans description)";
      descEl.title = item.text || "";
      tdDesc.appendChild(descEl);
      if (item.patrouille) {
        var vehEl = mkEl("div", "pcorg-exp-veh");
        vehEl.appendChild(matIcon("directions_car"));
        vehEl.appendChild(document.createTextNode(" " + item.patrouille));
        tdDesc.appendChild(vehEl);
      }
      tr.appendChild(tdDesc);

      // Zone
      var tdZone = document.createElement("td");
      var zoneEl = mkEl("span", "pcorg-exp-zone");
      zoneEl.textContent = truncZone(item.area_desc);
      tdZone.appendChild(zoneEl);
      tr.appendChild(tdZone);

      // Operateur
      var tdOp = document.createElement("td");
      var opEl = mkEl("span", "pcorg-exp-operator");
      opEl.textContent = operatorLabel(item.operator);
      tdOp.appendChild(opEl);
      tr.appendChild(tdOp);

      // Ouverture
      var tdOpen = document.createElement("td");
      var openEl = mkEl("span", "pcorg-exp-time");
      if (item.ts) {
        var dOpen = new Date(item.ts);
        openEl.textContent = String(dOpen.getDate()).padStart(2, "0") + "/" +
          String(dOpen.getMonth() + 1).padStart(2, "0") + " " +
          String(dOpen.getHours()).padStart(2, "0") + ":" +
          String(dOpen.getMinutes()).padStart(2, "0");
      }
      tdOpen.appendChild(openEl);
      tr.appendChild(tdOpen);

      // Cloture
      var tdClose = document.createElement("td");
      var closeEl = mkEl("span", "pcorg-exp-time");
      // Fiche en cours : rien. Prysm renseigne une date de cloture par
      // defaut sur les fiches ouvertes (affichee "01/01")
      if (!item._open && item.close_ts) {
        var dClose = new Date(item.close_ts);
        closeEl.textContent = String(dClose.getDate()).padStart(2, "0") + "/" +
          String(dClose.getMonth() + 1).padStart(2, "0") + " " +
          String(dClose.getHours()).padStart(2, "0") + ":" +
          String(dClose.getMinutes()).padStart(2, "0");
      } else {
        closeEl.textContent = "-";
        closeEl.style.color = "var(--muted)";
      }
      if (item.operator_close) closeEl.title = "Close par " + operatorLabel(item.operator_close);
      tdClose.appendChild(closeEl);
      tr.appendChild(tdClose);

      // Localiser sur la carte (fiches ouvertes positionnees : seules elles ont un pin)
      var tdLoc = document.createElement("td");
      if (item._open && item.lat != null && item.lon != null) {
        var locBtn = mkEl("button", "pcorg-exp-locate");
        locBtn.type = "button";
        locBtn.title = "Voir sur la carte";
        locBtn.appendChild(matIcon("location_on"));
        locBtn.addEventListener("click", (function (it) {
          return function (e) { e.stopPropagation(); flyToPin(it.lat, it.lon, it.id); };
        })(item));
        tdLoc.appendChild(locBtn);
      }
      tr.appendChild(tdLoc);

      // Click -> open detail
      tr.addEventListener("click", (function (id, closed) {
        return function () { openDetailModal(id, closed); };
      })(item.id, !item._open));

      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    expBody.appendChild(table);
    expBody.scrollTop = prevScrollTop;

    // Footer: pagination cloturees (sauf en mode recherche serveur)
    if (!expSearchActive && expFilter !== "open") {
      var footer = mkEl("div", "pcorg-exp-footer");
      if (expClosedLoading) {
        footer.textContent = "Chargement...";
      } else if (expClosedHasMore) {
        var btn = mkEl("button", "pcorg-exp-loadmore");
        var loadedClosed = (lastData.closed || []).length + (expClosedExtra || []).length;
        btn.textContent = "Charger plus (" + loadedClosed + " / " + expClosedTotal + ")";
        btn.addEventListener("click", function () { loadMoreClosed(); });
        footer.appendChild(btn);
      } else if (expClosedTotal > 0) {
        var loadedClosedAll = (lastData.closed || []).length + (expClosedExtra || []).length;
        footer.textContent = "Toutes les fiches cloturees affichees (" + loadedClosedAll + ")";
      }
      if (footer.childNodes.length || footer.textContent) {
        expBody.appendChild(footer);
      }
    }

    // Count
    var openCount = items.filter(function (it) { return it._open; }).length;
    var closedCount = items.filter(function (it) { return !it._open; }).length;
    if (expCount) {
      var label = items.length + " intervention" + (items.length > 1 ? "s" : "") +
        " - " + openCount + " en cours, " + closedCount + " terminee" + (closedCount > 1 ? "s" : "");
      if (expSearchActive) {
        label = "Resultats: " + label;
      } else if (expClosedTotal > closedItems.length && expFilter !== "open") {
        label += " (sur " + expClosedTotal + " cloturees au total)";
      }
      expCount.textContent = label;
    }
  }

  // ── Camera picker ──────────────────────────────────────────────────────────
  var _camPickerOverlay = null;
  var _camPickerCallback = null;
  var _camPickerCache = null;

  function openCameraPicker(callback) {
    _camPickerCallback = callback;

    if (!_camPickerOverlay) {
      var overlay = mkEl("div", "pcorg-cam-picker-overlay");
      overlay.addEventListener("click", function (e) {
        if (e.target === overlay) closeCameraPicker();
      });

      var panel = mkEl("div", "pcorg-cam-picker");
      var header = mkEl("div", "pcorg-cam-picker-header");
      var title = mkEl("span", ""); title.textContent = "Choisir une camera";
      var closeBtn = mkEl("button", "pcorg-cam-picker-close");
      closeBtn.appendChild(matIcon("close"));
      closeBtn.addEventListener("click", closeCameraPicker);
      header.appendChild(title);
      header.appendChild(closeBtn);
      panel.appendChild(header);

      var list = mkEl("div", "pcorg-cam-picker-list");
      list.id = "pcorg-cam-picker-list";
      panel.appendChild(list);

      overlay.appendChild(panel);
      document.body.appendChild(overlay);
      _camPickerOverlay = overlay;
    }

    _camPickerOverlay.classList.add("show");
    loadCameraList();
  }

  function closeCameraPicker() {
    if (_camPickerOverlay) _camPickerOverlay.classList.remove("show");
    _camPickerCallback = null;
  }

  function loadCameraList() {
    var list = document.getElementById("pcorg-cam-picker-list");
    list.textContent = "";
    var loading = mkEl("div", "pcorg-cam-picker-loading");
    loading.textContent = "Chargement...";
    list.appendChild(loading);

    var doRender = function (cameras) {
      list.textContent = "";
      if (!cameras || !cameras.length) {
        var empty = mkEl("div", "pcorg-cam-picker-empty");
        empty.textContent = "Aucune camera disponible";
        list.appendChild(empty);
        return;
      }
      cameras.forEach(function (cam) {
        if (!cam.enabled) return;
        var item = mkEl("div", "pcorg-cam-picker-item");
        item.dataset.id = cam._id;

        var thumb = mkEl("div", "pcorg-cam-picker-thumb");
        var img = mkEl("img", "");
        img.src = "/api/cameras/" + encodeURIComponent(cam._id) + "/snapshot";
        img.alt = "";
        img.addEventListener("error", function () { img.style.display = "none"; placeholder.style.display = ""; });
        img.addEventListener("load", function () { img.style.display = "block"; placeholder.style.display = "none"; });
        img.style.display = "none";
        var placeholder = mkEl("span", "material-symbols-outlined");
        placeholder.textContent = "videocam";
        placeholder.style.cssText = "font-size:28px;color:rgba(148,163,184,.4);";
        thumb.appendChild(img);
        thumb.appendChild(placeholder);

        var info = mkEl("div", "pcorg-cam-picker-info");
        var name = mkEl("div", "pcorg-cam-picker-name");
        name.textContent = cam.name;
        var meta = mkEl("div", "pcorg-cam-picker-meta");
        meta.textContent = cam.ip + (cam.location ? " - " + cam.location : "");
        info.appendChild(name);
        info.appendChild(meta);

        var arrow = matIcon("chevron_right");
        arrow.style.cssText = "color:var(--muted);font-size:20px;";

        item.appendChild(thumb);
        item.appendChild(info);
        item.appendChild(arrow);

        item.addEventListener("click", function () {
          var cb = _camPickerCallback;
          closeCameraPicker();
          if (cb) cb(cam._id, cam.name);
        });

        list.appendChild(item);
      });
    };

    if (_camPickerCache) {
      doRender(_camPickerCache);
    } else {
      fetch("/api/cameras").then(function (r) { return r.json(); })
        .then(function (cameras) {
          _camPickerCache = cameras;
          doRender(cameras);
        })
        .catch(function () {
          list.textContent = "";
          var err = mkEl("div", "pcorg-cam-picker-empty");
          err.textContent = "Erreur de chargement";
          list.appendChild(err);
        });
    }
  }

  // ── Aide a la saisie (pcorg_assist.py) ─────────────────────────────────────
  // Sous le champ Description de l'assistant de creation : suggestions de
  // classement (IA, jamais appliquees d'office) et fiches similaires ouvertes
  // ou recentes. Ne bloque ni la saisie ni l'enregistrement : toute erreur
  // est silencieuse, une requete en cours est annulee des que le texte change.
  var PCA_MIN_CHARS = 25;
  var PCA_DEBOUNCE_MS = 1200;
  var pca = { timer: null, ctrl: null, key: "", bar: null, res: null };

  function pcaInitAssist() {
    var ta = document.getElementById("pcorg-c-text");
    if (!ta || pca.bar) return;
    var bar = mkEl("div", "pca-bar");
    bar.setAttribute("aria-live", "polite");
    bar.hidden = true;
    ta.parentNode.appendChild(bar);
    pca.bar = bar;
    ta.addEventListener("input", function () { pcaSchedule(PCA_DEBOUNCE_MS); });
    ta.addEventListener("blur", function () { pcaSchedule(0); });
  }

  function pcaAbort() {
    if (pca.timer) { clearTimeout(pca.timer); pca.timer = null; }
    if (pca.ctrl) { try { pca.ctrl.abort(); } catch (e) {} pca.ctrl = null; }
  }

  function pcaReset() {
    pcaAbort();
    pca.key = "";
    pca.res = null;
    if (pca.bar) { pca.bar.textContent = ""; pca.bar.hidden = true; }
  }

  function pcaKey(text) {
    var ey = (typeof getCurrentEventYear === "function") ? getCurrentEventYear() : {};
    return { ey: ey, key: [text, ey.event || "", ey.year || ""].join("|") };
  }

  function pcaSchedule(delay) {
    var ta = document.getElementById("pcorg-c-text");
    if (!ta) return;
    var text = ta.value.trim();
    if (pca.timer) { clearTimeout(pca.timer); pca.timer = null; }
    if (text.length < PCA_MIN_CHARS) { pcaReset(); return; }
    var k = pcaKey(text);
    if (k.key === pca.key) return;                       // deja demande ou affiche
    if (pca.ctrl) { try { pca.ctrl.abort(); } catch (e) {} pca.ctrl = null; }
    pca.timer = setTimeout(function () { pca.timer = null; pcaFetch(text); }, delay);
  }

  // POST JSON annulable (AbortController) ; rend null en cas d'erreur ou
  // d'annulation. Jeton CSRF refuse : renouvele puis requete rejouee une fois.
  function pcaPost(url, payload, ctrl, retried) {
    var opts = {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(payload)
    };
    if (ctrl) opts.signal = ctrl.signal;
    return fetch(url, opts)
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .catch(function () { return null; })
      .then(function (res) {
        if (!retried && res && res.code === "csrf" && window.CockpitCsrf && !(ctrl && ctrl.signal.aborted)) {
          return window.CockpitCsrf.refresh().then(function (st) {
            return st.ok ? pcaPost(url, payload, ctrl, true) : null;
          }, function () { return null; });
        }
        return res;
      });
  }

  function pcaFetch(text) {
    var k = pcaKey(text);
    if (!k.ey.event || !k.ey.year || k.key === pca.key) return;
    var ctrl = window.AbortController ? new AbortController() : null;
    pca.ctrl = ctrl;
    pca.key = k.key;
    var payload = {
      text: text, event: k.ey.event, year: k.ey.year,
      category: createSelectedCat || null,
      area_desc: createAreaDesc || null
    };
    if (createLat != null && createLon != null) { payload.lat = createLat; payload.lng = createLon; }
    pcaPost("/api/pcorg/assist/suggest", payload, ctrl).then(function (res) {
      if (ctrl && pca.ctrl !== ctrl) return;              // requete depassee
      pca.ctrl = null;
      if (!res || res.ok !== true) { pca.key = ""; return; }
      pca.res = res;
      pcaRender();
    });
  }

  function pcaChip(label, title, onApply) {
    var chip = mkEl("span", "pca-chip");
    var lbl = mkEl("span", "pca-chip-label");
    lbl.textContent = label;
    chip.appendChild(lbl);
    if (title) chip.title = title;
    if (onApply) {
      var btn = mkEl("button", "pca-chip-apply");
      btn.type = "button";
      btn.textContent = "Appliquer";
      btn.addEventListener("click", function (e) {
        e.preventDefault(); e.stopPropagation();
        try { onApply(); } catch (err) {}
        pcaRender();
      });
      chip.appendChild(btn);
    }
    return chip;
  }

  function pcaSousSelect() { return document.getElementById("pcorg-c-sous_classification"); }

  function pcaEnsureCategory(cat) {
    if (!cat || CATEGORY_ORDER.indexOf(cat) === -1) return false;
    if (createSelectedCat !== cat) selectCategory(cat);
    return true;
  }

  function pcaRender() {
    var bar = pca.bar, res = pca.res;
    if (!bar) return;
    bar.textContent = "";
    if (!res) { bar.hidden = true; return; }
    var s = res.suggestions;
    var chips = [];
    if (s) {
      var pct = Math.round((s.confidence || 0) * 100);
      if (s.category && CATEGORY_ORDER.indexOf(s.category) !== -1 && s.category !== createSelectedCat) {
        chips.push(pcaChip("Categorie : " + shortCat(s.category) + " (" + pct + "%)", s.reason,
          function () { pcaEnsureCategory(s.category); }));
      }
      var sousCat = s.sous_classification_category;
      var sel = pcaSousSelect();
      var sousDone = sel && createSelectedCat === sousCat && sel.value === s.sous_classification;
      if (s.sous_classification && !sousDone && CATEGORY_ORDER.indexOf(sousCat) !== -1) {
        chips.push(pcaChip("Sous-classification : " + s.sous_classification, s.reason, function () {
          if (!pcaEnsureCategory(sousCat)) return;
          var el = pcaSousSelect();
          if (el) el.value = s.sous_classification;
        }));
      }
      var urgCat = s.category || createSelectedCat;
      var urgCats = (pcorgConfig && pcorgConfig.urgence_categories) || {};
      if (s.niveau_urgence && s.niveau_urgence !== createSelectedUrgency && urgCat && urgCats[urgCat]) {
        chips.push(pcaChip("Urgence : " + s.niveau_urgence + " - " + urgencyLabel(urgCat, s.niveau_urgence),
          s.reason, function () {
            if (!pcaEnsureCategory(urgCat)) return;
            createSelectedUrgency = s.niveau_urgence;
            buildSpecificCreateFields(createSelectedCat);  // valeurs deja saisies conservees
          }));
      }
      if (s.zone) chips.push(pcaChip("Lieu cite : " + s.zone, "Lieu repere dans le texte (information)", null));
    }
    if (chips.length) {
      var row = mkEl("div", "pca-row");
      var tag = mkEl("span", "pca-tag");
      tag.appendChild(matIcon("auto_awesome"));
      tag.appendChild(document.createTextNode("Suggestion"));
      if (s && s.reason) tag.title = s.reason;
      row.appendChild(tag);
      chips.forEach(function (c) { row.appendChild(c); });
      bar.appendChild(row);
    }
    var dups = res.possible_duplicates || [];
    if (dups.length) {
      var warn = mkEl("div", "pca-dups");
      var head = mkEl("div", "pca-dups-head");
      head.appendChild(matIcon("content_copy"));
      var ht = mkEl("span", "");
      ht.textContent = dups.length + (dups.length > 1 ? " fiches similaires" : " fiche similaire")
        + " (ouvertes ou saisies depuis moins de " + (res.duplicates_window_h || 2) + " h)";
      head.appendChild(ht);
      warn.appendChild(head);
      dups.forEach(function (it) {
        var b = mkEl("button", "pca-dup");
        b.type = "button";
        b.title = "Ouvrir la fiche";
        var t = mkEl("span", "pca-dup-time");
        t.textContent = fmtDayTime(it.ts) + (it.status_closed ? " (close)" : "");
        b.appendChild(t);
        var c = mkEl("span", "pca-dup-cat");
        c.textContent = shortCat(it.category);
        c.style.color = catStyle(it.category).color;
        b.appendChild(c);
        var x = mkEl("span", "pca-dup-text");
        x.textContent = it.excerpt || "";
        b.appendChild(x);
        b.addEventListener("click", function (e) { e.preventDefault(); pcaOpenFiche(it.id); });
        warn.appendChild(b);
      });
      bar.appendChild(warn);
    }
    bar.hidden = !bar.childNodes.length;
  }

  // La fiche s'ouvre AU-DESSUS de l'assistant de creation, sans le fermer
  function pcaOpenFiche(id) {
    var m = document.getElementById("pcorgDetailModal");
    var o = document.getElementById("pcorgDetailOverlay");
    if (m) m.classList.add("pca-over");
    if (o) o.classList.add("pca-over");
    openDetailModal(id, false);
  }

  // ── Precedents d'une fiche (autres editions) ──────────────────────────────
  function pcaBuildPrecedentsBlock(d) {
    var wrap = mkEl("details", "pca-prec");
    var sum = document.createElement("summary");
    sum.className = "pcorg-fiche-section pca-prec-summary";
    sum.textContent = "Precedents";
    wrap.appendChild(sum);

    var tools = mkEl("div", "pca-prec-tools");
    var scopeSel = mkEl("select", "form-input pca-prec-scope");
    [["editions", "Autres editions de l'evenement"], ["all", "Tous les evenements"]].forEach(function (o) {
      var opt = document.createElement("option");
      opt.value = o[0]; opt.textContent = o[1];
      scopeSel.appendChild(opt);
    });
    var go = mkEl("button", "pca-btn");
    go.type = "button";
    go.appendChild(matIcon("history"));
    go.appendChild(document.createTextNode(" Rechercher"));
    tools.appendChild(scopeSel);
    tools.appendChild(go);
    wrap.appendChild(tools);

    var list = mkEl("div", "pca-prec-list");
    wrap.appendChild(list);
    var synth = mkEl("div", "pca-synth");
    wrap.appendChild(synth);
    var current = [];

    function msg(el, text, cls) {
      el.textContent = "";
      var p = mkEl("div", "pca-msg" + (cls ? " " + cls : ""));
      p.textContent = text;
      el.appendChild(p);
    }

    go.addEventListener("click", function () {
      go.disabled = true;
      synth.textContent = "";
      msg(list, "Recherche...");
      var scope = scopeSel.value;
      fetch("/api/pcorg/assist/similar/" + encodeURIComponent(d.id) + "?scope=" + encodeURIComponent(scope))
        .then(function (r) { return r.json(); })
        .catch(function () { return null; })
        .then(function (res) {
          go.disabled = false;
          if (!res || res.ok !== true) { msg(list, "Recherche impossible", "pca-msg-err"); return; }
          current = res.precedents || [];
          renderList(scope);
        });
    });

    function renderList(scope) {
      list.textContent = "";
      if (!current.length) {
        msg(list, scope === "all" ? "Aucun precedent trouve." : "Aucun precedent dans les autres editions.");
        return;
      }
      current.forEach(function (p) {
        var card = mkEl("div", "pca-prec-item");
        card.style.borderLeftColor = catStyle(p.category).color;
        var top = mkEl("div", "pca-prec-top");
        var ed = mkEl("strong", ""); ed.textContent = p.edition || "";
        top.appendChild(ed);
        var dt = mkEl("span", "pca-prec-date"); dt.textContent = fmtDayTime(p.ts, true);
        top.appendChild(dt);
        if (p.niveau_urgence && URGENCY_COLORS[p.niveau_urgence]) {
          var u = mkEl("span", "pca-prec-urg");
          u.textContent = p.niveau_urgence;
          u.style.background = URGENCY_COLORS[p.niveau_urgence];
          u.title = urgencyLabel(p.category, p.niveau_urgence);
          top.appendChild(u);
        }
        var cat = mkEl("span", "pca-prec-cat");
        cat.textContent = shortCat(p.category) + (p.sous_classification ? " / " + p.sous_classification : "");
        top.appendChild(cat);
        if (p.duration_min != null) {
          var du = mkEl("span", "pca-prec-dur");
          du.textContent = p.duration_min < 60 ? p.duration_min + " min"
            : Math.floor(p.duration_min / 60) + " h " + _pad2(p.duration_min % 60);
          du.title = "Duree d'ouverture de la fiche";
          top.appendChild(du);
        }
        var open = mkEl("button", "pca-prec-open");
        open.type = "button";
        open.title = "Ouvrir cette fiche";
        open.appendChild(matIcon("open_in_new"));
        open.addEventListener("click", function () { openDetailModal(p.id, false); });
        top.appendChild(open);
        card.appendChild(top);
        var tx = mkEl("div", "pca-prec-text"); tx.textContent = p.excerpt || "";
        card.appendChild(tx);
        if (p.closing_comment) {
          var cl = mkEl("div", "pca-prec-close");
          var clb = document.createElement("b"); clb.textContent = "Cloture : ";
          cl.appendChild(clb);
          cl.appendChild(document.createTextNode(p.closing_comment));
          card.appendChild(cl);
        } else if (p.last_entries && p.last_entries.length) {
          var last = p.last_entries[p.last_entries.length - 1];
          var le = mkEl("div", "pca-prec-close");
          var leb = document.createElement("b"); leb.textContent = "Derniere action : ";
          le.appendChild(leb);
          le.appendChild(document.createTextNode(last.text || ""));
          card.appendChild(le);
        }
        list.appendChild(card);
      });
      var sb = mkEl("button", "pca-btn");
      sb.type = "button";
      sb.appendChild(matIcon("auto_awesome"));
      sb.appendChild(document.createTextNode(" Synthese IA"));
      sb.title = "Resume ce qui a ete fait dans ces precedents (fonde uniquement sur ces fiches)";
      sb.addEventListener("click", function () { runSynthesis(scope, sb); });
      synth.textContent = "";
      synth.appendChild(sb);
    }

    function runSynthesis(scope, btn) {
      btn.disabled = true;
      var wait = mkEl("div", "pca-msg"); wait.textContent = "Synthese en cours...";
      synth.appendChild(wait);
      apiCall("POST", "/api/pcorg/assist/similar/" + encodeURIComponent(d.id) + "/synthesis", {
        scope: scope, precedent_ids: current.map(function (p) { return p.id; })
      }).then(function (res) {
        btn.disabled = false;
        wait.remove();
        if (!res || res.ok !== true) {
          var e = mkEl("div", "pca-msg pca-msg-err");
          e.textContent = res && res.error === "cle_api_absente" ? "Assistant IA non configure"
            : (res && res.error === "budget_ia_depasse" ? "Budget IA mensuel depasse" : "Synthese indisponible");
          synth.appendChild(e);
          return;
        }
        btn.remove();
        var box = mkEl("div", "pca-synth-box");
        var h = mkEl("div", "pca-synth-head");
        h.appendChild(matIcon("auto_awesome"));
        h.appendChild(document.createTextNode(" Ce qui a ete fait"
          + (res.fiabilite ? " (fiabilite " + res.fiabilite + ")" : "")));
        box.appendChild(h);
        var ul = document.createElement("ul");
        (res.points || []).forEach(function (pt) {
          var li = document.createElement("li"); li.textContent = pt; ul.appendChild(li);
        });
        box.appendChild(ul);
        synth.appendChild(box);
      });
    }

    return wrap;
  }

  // ── Public API (pour alert_poller) ──────────────────────────────────────────
  // openFiche rafraichit aussi la liste et la carte : une fiche ouverte depuis
  // une alerte (SOS terrain) n'avait pas encore de pin
  window.PcorgUI = { openFiche: function (id) { openDetailModal(id, false); refresh(); } };

  // ── Mode creation seule (File du service) ───────────────────────────────────
  // La page pose window.PCORG_CREATE_ONLY = true avant de charger ce fichier et
  // inclut _pcorg_create_modal.html : MEME assistant 3 etapes que l'accueil
  // (position + carroyage, categorie, source, champs de categorie, vehicule,
  // horodatage, suggestions), sans liste, pins, menu clic droit ni rafraichissement.
  // PcorgCreate.open({categories: [...], onCreated: function (id) {}}).
  function initCreateOnly() {
    initCreateModal();
    loadPcorgConfig();
    loadCockpitUserNames();
    loadVehiclesByCategory();
    ensureSharedGrid();
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || !createModal || !createModal.classList.contains("show")) return;
      if (_tsPopover || vehiclePicker || (_camPickerOverlay && _camPickerOverlay.classList.contains("show"))) return;
      if (document.querySelector(".pcorg-autocomplete")) return;
      if (document.querySelector("#toast-container .toast-confirm, #toast-container .toast-input")) return;
      hideCreate();
    });
  }

  window.PcorgCreate = {
    open: function (opts) {
      if (!createModal) return false;
      createHooks = opts || {};
      loadVehiclesByCategory();   // l'evenement a pu changer sur la page
      openCreateWizard();
      // Pre-remplissage (constat terrain transforme en fiche, /declarations) :
      // {lat, lon, text, urgency}. L'operateur garde la main sur tout.
      var pf = createHooks.prefill;
      if (pf) {
        if (pf.urgency) createSelectedUrgency = pf.urgency;
        var ta = document.getElementById("pcorg-c-text");
        if (ta && pf.text) ta.value = pf.text;
        // Source : {source: "externe", appelant: "...", canal: "application"}
        if (pf.source && SOURCE_BY_ID[pf.source]) {
          selectSource(pf.source);
          var who = document.getElementById(pf.source === "externe" ? "pcorg-c-appelant"
            : pf.source === "operateur" ? "pcorg-c-emetteur"
            : pf.source === "hierarchie" ? "pcorg-c-donneur" : "pcorg-c-source-origine");
          if (who && pf.appelant) who.value = pf.appelant;
          if (pf.canal && CANAL_BY_ID[pf.canal] && pf.source !== "initiative") selectCanal(pf.canal);
        }
        if (pf.lat != null && pf.lon != null) {
          var t0 = Date.now();
          (function place() {
            if (!createMiniMap) {
              if (Date.now() - t0 < 5000) setTimeout(place, 100);
              return;
            }
            setCreatePosition(pf.lat, pf.lon);
            createMiniMap.setView([pf.lat, pf.lon], Math.max(createMiniMap.getZoom(), 17));
          })();
        }
      }
      return true;
    }
  };

  // ── Bootstrap ──────────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    if (createOnlyMode) { initCreateOnly(); return; }
    init();
    initExpandedPanel();
  });
})();
