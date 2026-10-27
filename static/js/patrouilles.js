// patrouilles.js - page /patrouilles : points d'interet, scenarios (ordre,
// optimisation, apercu du trajet), envoi a une tablette ou a un groupe,
// suivi en direct, historique, zones de competence des tablettes.
// Serveur : patrols_api.py / patrols.py. Voir docs/claude/patrouilles.md.
(function () {
  "use strict";

  var STATE_LABELS = {
    proposed: "Proposee", running: "En cours", paused: "En pause", done: "Terminee",
    partial: "Incomplete", refused: "Non prise", expired: "Expiree", cancelled: "Annulee",
  };
  var STEP_COLORS = { pending: "#94a3b8", done: "#16a34a", skipped: "#f59e0b", missed: "#dc2626" };
  var TRAVEL_ICONS = { vehicle: "directions_car", pedestrian: "directions_walk" };

  var S = {
    data: null, tab: "suivi", map: null, layers: {}, zonesLayer: null,
    sc: null,          // scenario en edition {id, name, travel, loop, color, note, points:[ids]}
    pt: null,          // point en edition {id, name, lat, lng, ...}
    live: [], liveTimer: null, previewTimer: null, previewSeq: 0, liveSeen: {}, liveSeenInit: false,
  };

  // ---------- HTTP ----------
  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  function apiGet(url) {
    return fetch(url, { cache: "no-store", credentials: "same-origin" }).then(function (r) {
      return r.json().then(function (b) { if (!r.ok) throw Object.assign(new Error(b.error || r.status), { body: b }); return b; });
    });
  }
  function apiSend(method, url, data) {
    return fetch(url, {
      method: method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(data || {}),
    }).then(function (r) {
      return r.json().then(function (b) { if (!r.ok) throw Object.assign(new Error(b.error || r.status), { body: b }); return b; });
    });
  }
  function apiPost(url, data) { return apiSend("POST", url, data); }
  function apiPut(url, data) { return apiSend("PUT", url, data); }
  function toast(type, msg) { if (typeof window.showToast === "function") window.showToast(type, msg); }

  var ERRORS = {
    nom_obligatoire: "Nom obligatoire", position_invalide: "Position invalide", point_inconnu: "Point inconnu",
    trop_de_points: "Trop de points (40 au plus)", no_route: "Aucun chemin entre ces points",
    valhalla_unreachable: "Moteur d'itineraire injoignable", scenario_vide: "Scenario sans point",
    cible_invalide: "Choisissez une tablette ou un groupe", not_found: "Introuvable",
  };
  function errLabel(e) { var c = (e && e.message) || String(e); return ERRORS[c] || c; }

  // ---------- DOM ----------
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function icon(name) { return el("span", "material-symbols-outlined", name); }
  // Leaflet interprete en HTML le texte des infobulles et des divIcon : tout
  // nom saisi (point, tablette) ou couleur passe par esc().
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function safeColor(c) { return /^#[0-9a-fA-F]{6}$/.test(c || "") ? c : "#2563eb"; }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function fmtKm(m) { return m == null ? "-" : (m < 1000 ? m + " m" : (m / 1000).toFixed(1).replace(".", ",") + " km"); }
  function fmtMin(s) {
    if (s == null) return "-";
    var m = Math.round(s / 60);
    return m < 60 ? m + " min" : Math.floor(m / 60) + " h " + String(m % 60).padStart(2, "0");
  }
  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }
  function fmtDateTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" }) + " " + fmtTime(iso);
  }
  function pointById(id) {
    var pts = (S.data && S.data.points) || [];
    for (var i = 0; i < pts.length; i++) if (pts[i].id === id) return pts[i];
    return null;
  }
  function zoneLabel(id) {
    var zs = (S.data && S.data.zones) || [];
    for (var i = 0; i < zs.length; i++) if (zs[i].id === id) return zs[i].label;
    return id;
  }

  // Polyline encodee precision 6 (Valhalla)
  function decode6(str) {
    var idx = 0, lat = 0, lng = 0, out = [];
    while (str && idx < str.length) {
      var b, shift = 0, result = 0;
      do { b = str.charCodeAt(idx++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
      lat += (result & 1) ? ~(result >> 1) : (result >> 1);
      shift = 0; result = 0;
      do { b = str.charCodeAt(idx++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
      lng += (result & 1) ? ~(result >> 1) : (result >> 1);
      out.push([lat / 1e6, lng / 1e6]);
    }
    return out;
  }

  // ---------- Carte ----------
  function initMap() {
    var map = L.map("pt-map", { zoomControl: true }).setView([47.952, 0.212], 14);
    var osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "OSM", maxNativeZoom: 19, maxZoom: 22 });
    var aco = L.tileLayer("/tiles/{z}/{x}/{y}.png", { attribution: "ACO", tms: true, maxZoom: 22 });
    var ign = L.tileLayer("https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}", { attribution: "IGN-F/Geoplateforme", maxNativeZoom: 19, maxZoom: 22 });
    osm.addTo(map);
    L.control.layers({ "Plan": osm, "Plan ACO": aco, "Photo IGN": ign }, null, { position: "topright" }).addTo(map);
    S.map = map;
    ["points", "scenario", "live"].forEach(function (k) { S.layers[k] = L.layerGroup().addTo(map); });
    map.on("click", onMapClick);
    $("pt-show-zones").addEventListener("change", drawZones);
  }

  function drawZones() {
    if (S.zonesLayer) { S.map.removeLayer(S.zonesLayer); S.zonesLayer = null; }
    if (!$("pt-show-zones").checked || !S.data) return;
    S.zonesLayer = L.geoJSON({
      type: "FeatureCollection",
      features: S.data.zones.map(function (z) { return { type: "Feature", properties: { label: z.label }, geometry: z.geometry }; }),
    }, { style: { color: "#7c3aed", weight: 1, fillOpacity: 0.05 }, interactive: false }).addTo(S.map);
    S.zonesLayer.bringToBack();
  }

  function numberIcon(n, color, extra) {
    return L.divIcon({
      className: "pt-num-icon" + (extra ? " " + extra : ""),
      html: '<span style="background:' + safeColor(color) + '">' + esc(n) + "</span>",
      iconSize: [26, 26], iconAnchor: [13, 13],
    });
  }
  function dotIcon(color, title) {
    return L.divIcon({ className: "pt-dot-icon", html: '<span style="background:' + safeColor(color) + '" title="' + esc(title) + '"></span>', iconSize: [14, 14], iconAnchor: [7, 7] });
  }

  function onMapClick(e) {
    if (S.tab === "proposer" && PR.placing) { placeAt(e.latlng); return; }
    if (S.tab !== "points") return;
    if (IMP.on) {       // import depuis une couche : la carte sert au lasso
      if (IMP.lasso) { IMP.lasso.push([e.latlng.lng, e.latlng.lat]); drawImport(); }
      return;
    }
    // Lieu cite a placer (bouton Placer de l'onglet Proposer) : le nom est pre-rempli
    var pending = S.pendingPlace;
    S.pendingPlace = null;
    openPointEditor({ lat: e.latlng.lat, lng: e.latlng.lng, name: pending || "" });
  }

  // ---------- Chargement ----------
  function load() {
    return apiGet("/api/patrols/bootstrap").then(function (d) {
      S.data = d;
      fillSelects();
      render();
    }).catch(function (e) { toast("error", "Chargement impossible : " + errLabel(e)); });
  }

  function fillSelects() {
    var dom = $("pt-sc-dominante");
    clear(dom);
    (S.data.dominantes || []).forEach(function (d) { dom.appendChild(new Option(d.label, d.id)); });
    var hs = $("pt-hist-sc"), cur = hs.value;
    clear(hs);
    hs.appendChild(new Option("Tous les scenarios", ""));
    S.data.scenarios.forEach(function (s) { hs.appendChild(new Option(s.name, s.id)); });
    hs.value = cur;
    $("pt-set-radius").value = S.data.settings.radius_m;
    $("pt-set-timeout").value = S.data.settings.accept_timeout_s;
    $("pt-set-seen").value = S.data.settings.seen_window_min;
    var ll = $("pt-set-layers");
    clear(ll);
    (S.data.layers || []).forEach(function (l) {
      var lab = el("label", "pt-check");
      var cb = document.createElement("input"); cb.type = "checkbox"; cb.value = l.collection; cb.checked = l.selected;
      lab.appendChild(cb); lab.appendChild(document.createTextNode(" " + l.label));
      ll.appendChild(lab);
    });
  }

  // ---------- Onglets ----------
  var MAP_TABS = { suivi: true, scenarios: true, points: true, proposer: true };

  function setTab(tab) {
    if (!document.querySelector('.pt-tab[data-tab="' + tab + '"]')) tab = "suivi";
    S.tab = tab;
    document.querySelectorAll("#pt-tabs button").forEach(function (b) {
      b.classList.toggle("active", b.dataset.tab === tab);
      b.setAttribute("aria-selected", b.dataset.tab === tab ? "true" : "false");
    });
    document.querySelectorAll(".pt-tab").forEach(function (t) { t.hidden = t.dataset.tab !== tab; });
    // Carte seulement pour Suivi, Scenarios, Points ; le reste en pleine largeur
    $("pt-work").classList.toggle("nomap", !MAP_TABS[tab]);
    try { localStorage.setItem("pt_tab", tab); } catch (e) { /* noop */ }
    if (S.liveTimer) { clearInterval(S.liveTimer); S.liveTimer = null; }
    if (tab === "suivi") { loadPlans(); loadLive(); }
    if (tab === "plans") { loadPlans(); S.liveTimer = setInterval(loadPlans, 30000); }
    if (tab === "historique") loadHistory();
    if (tab === "proposer") initProposer();
    render();
    setTimeout(function () { S.map.invalidateSize(); }, 50);
  }

  // Compteurs des onglets : en cours, planifications actives, scenarios et
  // points actifs, tablettes
  function updateCounts() {
    function set(id, n) { var e = $(id); if (e) e.textContent = n ? String(n) : ""; }
    set("pt-n-suivi", (S.live || []).filter(function (r) { return ["proposed", "running", "paused"].indexOf(r.state) >= 0; }).length);
    set("pt-n-plans", (S.plans || []).filter(function (p) { return p.enabled; }).length);
    if (S.data) {
      set("pt-n-scenarios", S.data.scenarios.filter(function (s) { return !s.archived; }).length);
      set("pt-n-points", S.data.points.filter(function (p) { return p.active; }).length);
      set("pt-n-tablettes", S.data.devices.length);
    }
  }

  function render() {
    if (!S.data) return;
    updateCounts();
    S.layers.points.clearLayers();
    S.layers.scenario.clearLayers();
    S.layers.live.clearLayers();
    if (S.tab === "points") { renderPoints(); if (IMP.on) drawImport(); else drawAllPoints(true); }
    else if (S.tab === "scenarios") { renderScenarios(); if (S.sc) drawScenarioEditor(); else drawScenarioRoutes(); }
    else if (S.tab === "tablettes") renderDevices();
    else if (S.tab === "suivi") renderLive();
    else if (S.tab === "plans") renderPlans();
    else if (S.tab === "proposer") drawProposal();
    drawZones();
  }

  // ---------- Points ----------
  // faint : points d'appoint de l'editeur de scenario (petits, pales, sans
  // cercle de rayon), hors ceux deja dans le scenario
  function drawAllPoints(editable, faint) {
    var inSc = {};
    if (faint && S.sc) S.sc.points.forEach(function (id) { inSc[id] = true; });
    S.data.points.forEach(function (p) {
      if (!p.active && (faint || !$("pt-pt-inactive").checked)) return;
      if (inSc[p.id]) return;
      var editing = S.pt && S.pt.id === p.id;
      if (editing) return;
      var m = faint
        ? L.circleMarker([p.lat, p.lng], { radius: 5, color: "#64748b", weight: 1, fillColor: "#cbd5e1", fillOpacity: 0.9 })
        : L.marker([p.lat, p.lng], { icon: dotIcon(p.active ? "#2563eb" : "#94a3b8", p.name) });
      m.bindTooltip(esc(p.name) + (faint ? " - cliquer pour ajouter" : ""), { direction: "top", offset: [0, -6] });
      if (!faint) L.circle([p.lat, p.lng], { radius: p.radius_m, color: "#2563eb", weight: 1, fillOpacity: 0.04, interactive: false }).addTo(S.layers.points);
      m.on("click", function (e) {
        L.DomEvent.stopPropagation(e);
        if (editable) openPointEditor(p);
        else if (S.sc) addPointToScenario(p.id);
      });
      m.addTo(S.layers.points);
    });
    if (S.pt) {
      // Point issu de Looker : position fixee par Looker, pas de deplacement ici
      var em = L.marker([S.pt.lat, S.pt.lng], { draggable: !S.pt.layer_ref, icon: dotIcon("#dc2626", "en edition") }).addTo(S.layers.points);
      var circle = L.circle([S.pt.lat, S.pt.lng], { radius: Number($("pt-pt-radius").value) || S.data.settings.radius_m, color: "#dc2626", weight: 2, fillOpacity: 0.08, interactive: false }).addTo(S.layers.points);
      em.on("drag", function (e) { circle.setLatLng(e.latlng); });
      em.on("dragend", function (e) {
        var ll = e.target.getLatLng();
        S.pt.lat = ll.lat; S.pt.lng = ll.lng;
        $("pt-pt-pos").textContent = "Position : " + ll.lat.toFixed(5) + ", " + ll.lng.toFixed(5) + " (deplacez le repere rouge pour ajuster)";
      });
    }
  }

  function renderPoints() {
    var list = $("pt-pt-list"), q = ($("pt-pt-search").value || "").toLowerCase(), showOff = $("pt-pt-inactive").checked;
    clear(list);
    var uses = {};
    S.data.scenarios.forEach(function (s) { if (!s.archived) s.points.forEach(function (id) { uses[id] = (uses[id] || 0) + 1; }); });
    var pts = S.data.points.filter(function (p) { return (showOff || p.active) && (!q || p.name.toLowerCase().indexOf(q) >= 0); });
    if (!pts.length) list.appendChild(el("div", "pt-empty", "Aucun point. Cliquez sur la carte pour en creer un."));
    pts.forEach(function (p) {
      var row = el("div", "pt-item" + (p.active ? "" : " off") + (S.pt && S.pt.id === p.id ? " sel" : ""));
      var main = el("div", "pt-item-main");
      main.appendChild(el("div", "pt-item-title", p.name));
      var sub = [];
      sub.push(p.radius_m + " m");
      if (p.photo_required) sub.push("photo exigee");
      if (p.instruction) sub.push("consigne");
      sub.push((uses[p.id] || 0) + " scenario(s)");
      if (p.layer_ref) sub.push(p.layer_missing ? "ABSENT de Looker" : "suit Looker");
      if (p.zones && p.zones.length) sub.push(p.zones.join(", "));
      main.appendChild(el("div", "pt-item-sub", sub.join(" - ")));
      row.appendChild(main);
      row.addEventListener("click", function () { openPointEditor(p); S.map.setView([p.lat, p.lng], Math.max(S.map.getZoom(), 17)); });
      list.appendChild(row);
    });
  }

  function openPointEditor(p) {
    S.pt = { id: p.id || null, name: p.name || "", lat: p.lat, lng: p.lng, radius_m: p.radius_m || S.data.settings.radius_m,
             dwell_s: p.dwell_s || 0, instruction: p.instruction || "", photo_required: !!p.photo_required,
             active: p.active !== false, zones: p.zones || [], info: p.info || "", layer_ref: p.layer_ref || null };
    $("pt-pt-editor").hidden = false;
    $("pt-pt-listpane").hidden = true;
    $("pt-pt-name").value = S.pt.name;
    $("pt-pt-radius").value = S.pt.radius_m;
    $("pt-pt-dwell").value = S.pt.dwell_s;
    $("pt-pt-instruction").value = S.pt.instruction;
    $("pt-pt-aliases").value = (p.aliases || []).join(", ");
    $("pt-pt-photo").checked = S.pt.photo_required;
    $("pt-pt-pos").textContent = "Position : " + S.pt.lat.toFixed(5) + ", " + S.pt.lng.toFixed(5) + " (deplacez le repere rouge pour ajuster)";
    $("pt-pt-zones").textContent = (S.pt.id ? (S.pt.zones.length ? "Zones : " + S.pt.zones.join(", ") : "Dans aucune zone de competence") : "") +
      (S.pt.info ? "\nInformations transmises a l'agent : " + S.pt.info : "");
    $("pt-pt-name").disabled = !!S.pt.layer_ref;
    if (S.pt.layer_ref) $("pt-pt-pos").textContent = "Nom, position et informations suivent Looker" + (p.layer_missing ? " (objet ABSENT de la couche : derniere position gardee)" : "") + ".";
    $("pt-pt-toggle").hidden = !S.pt.id;
    $("pt-pt-toggle").textContent = S.pt.active ? "Desactiver" : "Reactiver";
    render();
    $("pt-pt-name").focus();
  }

  function closePointEditor() {
    S.pt = null;
    $("pt-pt-editor").hidden = true;
    $("pt-pt-listpane").hidden = false;
    render();
  }

  function savePoint() {
    var body = {
      name: $("pt-pt-name").value, lat: S.pt.lat, lng: S.pt.lng, radius_m: Number($("pt-pt-radius").value),
      dwell_s: Number($("pt-pt-dwell").value), instruction: $("pt-pt-instruction").value, photo_required: $("pt-pt-photo").checked,
      aliases: $("pt-pt-aliases").value,
    };
    var req = S.pt.id ? apiPut("/api/patrols/points/" + S.pt.id, body) : apiPost("/api/patrols/points", body);
    req.then(function (r) {
      toast("success", "Point enregistre");
      var i = S.data.points.findIndex(function (p) { return p.id === r.point.id; });
      if (i >= 0) S.data.points[i] = r.point; else S.data.points.push(r.point);
      S.data.points.sort(function (a, b) { return a.name.localeCompare(b.name); });
      fillSelects();
      closePointEditor();
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  function togglePoint() {
    var active = !S.pt.active;
    apiPost("/api/patrols/points/" + S.pt.id + "/active", { active: active }).then(function () {
      var p = pointById(S.pt.id); if (p) p.active = active;
      toast("success", active ? "Point reactive" : "Point desactive (les scenarios qui l'utilisent le gardent)");
      fillSelects();
      closePointEditor();
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  // ---------- Proposer une ronde d'apres l'historique (patrols_propose.py) ----------
  var PR = { init: false, result: null, ai: false };

  function initProposer() {
    if (PR.init) return;
    PR.init = true;
    var dom = $("pr-dom");
    clear(dom);
    (S.data.dominantes || []).forEach(function (d) { dom.appendChild(new Option(d.label, d.id)); });
    dom.value = "surete";
    var now = new Date();
    $("pr-hour").value = String(now.getHours()).padStart(2, "0") + ":" + String(Math.floor(now.getMinutes() / 15) * 15).padStart(2, "0");
    loadProposalEvents();
    document.querySelectorAll('input[name="pr-src"]').forEach(function (r) { r.addEventListener("change", loadProposalEvents); });
    $("pr-go").addEventListener("click", function () { runProposal(false); });
    $("pr-ai").addEventListener("click", function () {
      // Un avis deja paye pour ces criteres : on le reprend, sauf demande expresse
      var rv = PR.result && PR.result.ai_review;
      if (rv && sameCriteria() && typeof window.showConfirmToast === "function") {
        window.showConfirmToast("Un avis de l'IA existe deja pour ces criteres (" + fmtWhen(rv.at) + "). Le reprendre gratuitement ?",
          { okLabel: "Reprendre (gratuit)", cancelLabel: "Nouvel avis (payant)", type: "info" }).then(function (reuse) {
          if (reuse) { PR.useAi = true; runProposal(false); } else runProposal(true);
        });
        return;
      }
      runProposal(true);
    });
    $("pr-create").addEventListener("click", createFromProposal);
    renderProposal();
  }

  // Evenements de la source choisie (PCS : SAISON par defaut, PCO : le plus fourni)
  function loadProposalEvents() {
    var src = document.querySelector('input[name="pr-src"]:checked').value;
    var ev = $("pr-event");
    clear(ev);
    ev.appendChild(new Option("Chargement...", ""));
    apiGet("/api/patrols/propose/events?source=" + src).then(function (r) {
      clear(ev);
      r.events.forEach(function (e) {
        var label = e.event === "__all__" ? "Tout l'historique" : e.event;
        ev.appendChild(new Option(label + " (" + e.n + " fiches, " + e.years.join(", ") + ")", e.event));
      });
      if (src === "pcs" && r.events.some(function (e) { return e.event === "SAISON"; })) ev.value = "SAISON";
      else if (r.events.length > 1) ev.value = r.events[1].event;
      PR.ai = !!r.ai;
      $("pr-ai").hidden = !PR.ai;
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  function proposalBody(ai) {
    return {
      source: document.querySelector('input[name="pr-src"]:checked').value,
      dominante: $("pr-dom").value, duration_min: Number($("pr-dur").value),
      travel: document.querySelector('input[name="pr-travel"]:checked').value, event: $("pr-event").value,
      loop: $("pr-loop").checked, hour: $("pr-same-hour").checked ? $("pr-hour").value : null, ai: !!ai,
      use_ai: !ai && PR.useAi !== false,
    };
  }

  // Memes criteres que l'avis IA affiche (source, evenement, dominante, deplacement)
  function sameCriteria() {
    var a = PR.body || {}, b = proposalBody(false);
    return a.source === b.source && a.event === b.event && a.dominante === b.dominante && a.travel === b.travel;
  }

  function fmtWhen(iso) {
    var d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleDateString("fr-FR") + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }

  function runProposal(ai) {
    var body = proposalBody(ai);
    if (!body.event) { toast("warning", "Choisissez l'evenement"); return; }
    var box = $("pr-result");
    clear(box);
    box.appendChild(el("div", "pt-hint", ai ? "Relecture des foyers par l'IA (30 s a 1 min)..." : "Calcul de la ronde..."));
    $("pr-go").disabled = $("pr-ai").disabled = true;
    apiPost("/api/patrols/propose", body).then(function (r) {
      if (ai) PR.useAi = true;
      PR.result = r;
      PR.body = body;
      PR.fitted = false;
      var dl = (S.data.dominantes || []).find(function (d) { return d.id === body.dominante; });
      $("pr-name").value = "Ronde " + (dl ? dl.label.toLowerCase() : "") + " " + body.duration_min + " min" + (r.ai_review && r.ai_review.used ? " (IA)" : "");
      renderProposal();
      render();
    }).catch(function (e) {
      clear(box);
      box.appendChild(el("div", "pt-empty", e.message === "ia_indisponible" ? "IA indisponible (cle, budget ou reseau) : la proposition sans IA reste utilisable." : errLabel(e)));
    }).then(function () { $("pr-go").disabled = $("pr-ai").disabled = false; });
  }

  function renderProposal() {
    var box = $("pr-result");
    clear(box);
    var r = PR.result;
    $("pr-foot").hidden = !(r && r.stops && r.stops.length >= 2);
    if (!r) {
      box.appendChild(el("div", "pt-empty", "Choisissez une dominante, une duree et l'evenement, puis Proposer."));
      return;
    }
    var est = r.estimate || {};
    var sum = el("div", "pt-estimate");
    sum.textContent = r.stops.length + " arret(s) - " + fmtKm(est.distance_m) + " - " + fmtMin(est.total_s) +
      " (arrets compris) pour " + fmtMin(r.budget_s) + " disponibles";
    box.appendChild(sum);
    box.appendChild(el("div", "pt-hint", (r.foyers || []).length + " foyer(s) sur la carte (cercles gris, taille = importance)."));
    (r.notes || []).forEach(function (n) { box.appendChild(el("div", "pt-hint", n)); });
    if (r.ai_review) {
      // Avis IA garde en base : on le garde ou non sans repayer
      var lab = el("label", "pr-ai-keep");
      var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !!r.ai_review.used;
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(" Utiliser l'avis de l'IA du " + fmtWhen(r.ai_review.at) +
        (r.ai_review.by ? " (" + r.ai_review.by + ")" : "") + ", deja paye : " + r.ai_review.n + " foyers relus"));
      cb.addEventListener("change", function () { PR.useAi = cb.checked; runProposal(false); });
      box.appendChild(lab);
    }
    renderMissing(box, r);
    if (r.ai_comment) {
      var c = el("div", "pt-callout");
      c.appendChild(icon("psychology"));
      c.appendChild(el("span", null, r.ai_comment));
      box.appendChild(c);
    }
    var ol = el("ol", "pt-steps");
    r.stops.forEach(function (s, i) {
      var li = el("li", "pt-step pr-stop");
      li.style.cursor = "pointer";
      li.appendChild(el("span", "pt-step-num", String(i + 1)));
      var main = el("div", "pt-step-name");
      main.appendChild(el("div", null, s.name));
      var sub = s.kind === "foyer"
        ? s.n + " incident(s) - " + (s.editions || []).join(", ") + (s.gravite_moy ? " - gravite " + s.gravite_moy : "") +
          (s.top_sous && s.top_sous.length ? " - " + s.top_sous.join(", ") : "") + (s.sources && s.sources.indexOf("carroyage") >= 0 ? " - position approx. (carroyage)" : "")
        : "Complement : couche " + (s.layer || "") + (s.info ? " - " + s.info : "");
      main.appendChild(el("div", "pt-muted", sub));
      if (s.ai_reason) main.appendChild(el("div", "pr-ai-reason", "IA : " + s.ai_reason));
      li.appendChild(main);
      if (s.kind === "foyer") {
        // Lieu mal place (nom rapproche du mauvais objet du referentiel) : on le corrige ici
        var fix = el("button", "btn btn-sm", "Corriger le lieu"); fix.type = "button";
        fix.title = "Placer ce lieu au bon endroit : toutes les fiches qui le citent suivront";
        fix.addEventListener("click", function (ev) {
          ev.stopPropagation();
          startPlacing(s.lieu || s.name, li);
          S.map.setView([s.lat, s.lng], Math.max(S.map.getZoom(), 16));
        });
        li.appendChild(fix);
      }
      li.addEventListener("click", function () { S.map.setView([s.lat, s.lng], Math.max(S.map.getZoom(), 17)); });
      ol.appendChild(li);
    });
    box.appendChild(ol);
  }

  // Lieux cites dans les fiches mais pas encore places. « Placer » se fait ici
  // meme : clic sur la carte (nouveau point) ou rattachement comme autre nom
  // d'un point existant, puis la proposition est recalculee.
  function renderMissing(box, r) {
    var miss = (r.stats && r.stats.non_placees) || [];
    if (!miss.length) return;
    var mb = el("details", "pr-missing");
    mb.open = true;
    mb.appendChild(el("summary", null, "Lieux cites non places (" + miss.length + ") - " +
      Math.round(100 * (r.stats.placees || 0) / Math.max(1, r.stats.fiches || 0)) + " % des fiches placees"));
    miss.forEach(function (m) {
      var row = el("div", "pr-miss-row");
      row.appendChild(el("span", "pr-miss-name", m.lieu));
      row.appendChild(el("span", "pt-muted", m.n + " fiche" + (m.n > 1 ? "s" : "")));
      var b = el("button", "btn", "Placer"); b.type = "button";
      b.addEventListener("click", function () { startPlacing(m.lieu, row); });
      row.appendChild(b);
      mb.appendChild(row);
    });
    box.appendChild(mb);
  }

  function startPlacing(lieu, row) {
    stopPlacing(true);
    // Un point de ce nom existe deja : on le DEPLACE (pas de doublon)
    var same = (S.data.points || []).find(function (p) { return p.active && p.name.toLowerCase() === String(lieu).toLowerCase(); });
    PR.placing = { lieu: lieu, latlng: null, marker: null, move: same && !same.layer_ref ? same : null };
    var f = el("div", "pr-place");
    f.appendChild(el("div", "pt-hint", PR.placing.move
      ? "Le point « " + lieu + " » existe : cliquez sur la carte a son BON emplacement (repere deplacable) puis enregistrez."
      : "Cliquez sur la carte a l'emplacement de « " + lieu + " » (repere deplacable), ou rattachez ce nom a un point existant. Votre point passe avant le referentiel."));
    var nm = el("input", "pt-input"); nm.type = "text"; nm.maxLength = 80; nm.value = lieu; nm.placeholder = "Nom du point";
    var al = el("input", "pt-input"); al.type = "text"; al.maxLength = 400; al.placeholder = "Autres noms (virgules), ex. pole europeen";
    f.appendChild(labelled("Nom du nouveau point", nm));
    f.appendChild(labelled("Autres noms", al));
    var sel = el("select", "pt-input");
    sel.appendChild(new Option("- ou rattacher a un point existant -", ""));
    (S.data.points || []).filter(function (p) { return p.active; }).forEach(function (p) { sel.appendChild(new Option(p.name, p.id)); });
    f.appendChild(sel);
    var pos = el("div", "pt-muted", "Position : pas encore placee");
    f.appendChild(pos);
    var bar = el("div", "pr-place-bar");
    var ok = el("button", "btn btn-primary", "Enregistrer et recalculer"); ok.type = "button";
    var ko = el("button", "btn", "Annuler"); ko.type = "button";
    bar.appendChild(ok); bar.appendChild(ko);
    f.appendChild(bar);
    PR.placing.posEl = pos;
    ko.addEventListener("click", function () { stopPlacing(); f.remove(); });
    ok.addEventListener("click", function () {
      var req;
      if (PR.placing.move && !sel.value) {
        if (!PR.placing.latlng) { toast("warning", "Cliquez d'abord sur la carte au bon emplacement"); return; }
        var mv = PR.placing.move;
        var extra = al.value.split(",").map(function (x) { return x.trim(); }).filter(Boolean);
        req = apiPut("/api/patrols/points/" + mv.id, Object.assign({}, mv, { lat: PR.placing.latlng.lat, lng: PR.placing.latlng.lng,
                                                                           aliases: (mv.aliases || []).concat(extra).join(", ") }));
      } else if (sel.value) {
        var p = pointById(sel.value);
        var aliases = (p.aliases || []).concat([lieu]).concat(al.value.split(",").map(function (x) { return x.trim(); }).filter(Boolean));
        req = apiPut("/api/patrols/points/" + p.id, Object.assign({}, p, { aliases: aliases.join(", ") }));
      } else {
        if (!PR.placing.latlng) { toast("warning", "Cliquez d'abord sur la carte a l'emplacement du lieu"); return; }
        req = apiPost("/api/patrols/points", { name: nm.value, lat: PR.placing.latlng.lat, lng: PR.placing.latlng.lng, aliases: al.value });
      }
      ok.disabled = true;
      req.then(function (res) {
        var i = S.data.points.findIndex(function (x) { return x.id === res.point.id; });
        if (i >= 0) S.data.points[i] = res.point; else S.data.points.push(res.point);
        S.data.points.sort(function (a, b) { return a.name.localeCompare(b.name); });
        toast("success", sel.value ? "« " + lieu + " » rattache a " + res.point.name : "Point " + res.point.name + " cree");
        stopPlacing();
        runProposal(false);
      }).catch(function (e) { ok.disabled = false; toast("error", errLabel(e)); });
    });
    row.after(f);
    S.map.getContainer().style.cursor = "crosshair";
    nm.focus();
  }

  function labelled(text, input) {
    var l = el("label", "pt-field", text);
    l.appendChild(input);
    return l;
  }

  function placeAt(latlng) {
    var pl = PR.placing;
    pl.latlng = latlng;
    if (!pl.marker) {
      pl.marker = L.marker(latlng, { draggable: true, zIndexOffset: 2000 }).addTo(S.map);
      pl.marker.on("dragend", function () { pl.latlng = pl.marker.getLatLng(); showPos(); });
    } else pl.marker.setLatLng(latlng);
    showPos();
    function showPos() { pl.posEl.textContent = "Position : " + pl.latlng.lat.toFixed(5) + ", " + pl.latlng.lng.toFixed(5) + " (deplacez le repere pour ajuster)"; }
  }

  function stopPlacing(keepForm) {
    if (PR.placing && PR.placing.marker) S.map.removeLayer(PR.placing.marker);
    PR.placing = null;
    S.map.getContainer().style.cursor = "";
    if (keepForm !== true) return;
    document.querySelectorAll(".pr-place").forEach(function (x) { x.remove(); });
  }

  function drawProposal() {
    var r = PR.result;
    if (!r) return;
    (r.foyers || []).forEach(function (f) {
      L.circleMarker([f.lat, f.lng], { radius: Math.max(4, Math.min(18, 3 + Math.sqrt(f.score) * 2)), color: "#64748b", weight: 1,
                                        fillColor: "#94a3b8", fillOpacity: 0.35 })
        .bindTooltip(esc(f.name || "Foyer") + " - " + f.n + " incident(s), score " + f.score, { direction: "top" })
        .addTo(S.layers.scenario);
    });
    var col = { surete: "#ef4444", secours: "#dc2626", incendie: "#f97316", prevention: "#16a34a" }[(PR.body || {}).dominante] || "#2563eb";
    var est = r.estimate || {};
    if (est.polyline) L.polyline(decode6(est.polyline), { color: col, weight: 5, opacity: 0.85 }).addTo(S.layers.scenario);
    var ll = [];
    r.stops.forEach(function (s, i) {
      ll.push([s.lat, s.lng]);
      L.marker([s.lat, s.lng], { icon: numberIcon(i + 1, s.kind === "foyer" ? col : "#64748b"), zIndexOffset: 1000 })
        .bindTooltip(esc(s.name), { direction: "top", offset: [0, -12] }).addTo(S.layers.scenario);
    });
    if (ll.length && !PR.fitted) { S.map.fitBounds(L.latLngBounds(ll).pad(0.2), { maxZoom: 17 }); }
    PR.fitted = true;
  }

  function createFromProposal() {
    var r = PR.result;
    if (!r) return;
    var body = Object.assign({}, PR.body, { stops: r.stops, name: $("pr-name").value });
    $("pr-create").disabled = true;
    apiPost("/api/patrols/propose/create", body).then(function (res) {
      toast("success", "Scenario cree : " + res.scenario.name);
      return load().then(function () {
        setTab("scenarios");
        openScenario(res.scenario);
      });
    }).catch(function (e) { toast("error", errLabel(e)); })
      .then(function () { $("pr-create").disabled = false; });
  }

  // ---------- Import depuis une couche Looker ----------
  // Les points crees suivent Looker (nom, position, informations) : voir
  // patrols.sync_layer_points. Selection par nom (filtre), par zone de
  // competence, au lasso (polygone trace sur la carte) ou au clic sur la carte.
  var IMP = { on: false, layer: null, items: [], sel: {}, lasso: null, visible: [] };

  function pipRing(lat, lng, ring) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if (((yi > lat) !== (yj > lat)) && (lng < (xj - xi) * (lat - yi) / ((yj - yi) || 1e-12) + xi)) inside = !inside;
    }
    return inside;
  }
  function inGeom(lat, lng, g) {
    if (!g) return false;
    var polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
    return polys.some(function (p) {
      return p.length && pipRing(lat, lng, p[0]) && !p.slice(1).some(function (h) { return pipRing(lat, lng, h); });
    });
  }

  function openImport() {
    var layers = (S.data.layers || []).filter(function (l) { return l.selected; });
    if (!layers.length) { toast("info", "Choisissez d'abord les couches dans Reglages"); return; }
    if (S.pt) closePointEditor();
    IMP.on = true;
    IMP.sel = {};
    IMP.lasso = null;
    var ls = $("pt-imp-layer");
    clear(ls);
    layers.forEach(function (l) { ls.appendChild(new Option(l.label, l.collection)); });
    if (IMP.layer && layers.some(function (l) { return l.collection === IMP.layer; })) ls.value = IMP.layer;
    var zs = $("pt-imp-zone");
    clear(zs);
    zs.appendChild(new Option("Toutes", ""));
    S.data.zones.forEach(function (z) { zs.appendChild(new Option(z.label, z.id)); });
    $("pt-imp-search").value = "";
    $("pt-pt-listpane").hidden = true;
    $("pt-imp-pane").hidden = false;
    loadImportItems();
  }

  function closeImport() {
    IMP.on = false;
    IMP.lasso = null;
    $("pt-imp-lasso-help").hidden = true;
    $("pt-imp-pane").hidden = true;
    $("pt-pt-listpane").hidden = false;
    render();
  }

  function loadImportItems() {
    IMP.layer = $("pt-imp-layer").value;
    IMP.items = [];
    IMP.sel = {};
    paintImport();
    $("pt-imp-count").textContent = "Chargement...";
    apiGet("/api/patrols/layers/" + encodeURIComponent(IMP.layer) + "/items").then(function (r) {
      IMP.items = r.items;
      paintImport();
      var ll = r.items.map(function (i) { return [i.lat, i.lng]; });
      if (ll.length) S.map.fitBounds(L.latLngBounds(ll).pad(0.05), { maxZoom: 17 });
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  function importFilter() {
    var q = ($("pt-imp-search").value || "").toLowerCase();
    var zid = $("pt-imp-zone").value;
    var zone = zid ? S.data.zones.find(function (z) { return z.id === zid; }) : null;
    return IMP.items.filter(function (it) {
      if (q && (it.name + " " + it.info).toLowerCase().indexOf(q) < 0) return false;
      if (zone && !inGeom(it.lat, it.lng, zone.geometry)) return false;
      return true;
    });
  }

  function paintImport() {
    var vis = importFilter();
    IMP.visible = vis.map(function (i) { return i.fid; });
    var box = $("pt-imp-list");
    clear(box);
    if (!vis.length) box.appendChild(el("div", "pt-empty", IMP.items.length ? "Aucun objet ne correspond." : "Aucun objet."));
    vis.slice(0, 400).forEach(function (it) {
      var lab = el("label", "pt-pick-item" + (IMP.sel[it.fid] ? " on" : ""));
      var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !!IMP.sel[it.fid];
      cb.addEventListener("change", function () {
        if (cb.checked) IMP.sel[it.fid] = true; else delete IMP.sel[it.fid];
        lab.classList.toggle("on", cb.checked);
        countImport(); drawImport();
      });
      lab.appendChild(cb);
      var txt = el("span", "pt-imp-txt");
      txt.appendChild(el("span", "pt-pick-name", it.name));
      if (it.info) txt.appendChild(el("span", "pt-muted", it.info));
      lab.appendChild(txt);
      if (it.point) lab.appendChild(el("span", "pt-chip zone", it.point.active ? "deja un point" : "point desactive"));
      box.appendChild(lab);
    });
    if (vis.length > 400) box.appendChild(el("div", "pt-hint", (vis.length - 400) + " autres : affinez le filtre (tout cocher les prend quand meme)."));
    countImport();
    drawImport();
  }

  function countImport() {
    var n = Object.keys(IMP.sel).length;
    $("pt-imp-count").textContent = IMP.visible.length + " objet(s) affiche(s) sur " + IMP.items.length + " - " + n + " coche(s)";
    $("pt-imp-go").disabled = !n;
    $("pt-imp-sc").disabled = n < 2;
    $("pt-imp-go").lastChild.textContent = " Creer " + (n || "") + " point" + (n > 1 ? "s" : "");
  }

  function drawImport() {
    if (S.tab !== "points" || !IMP.on) return;
    S.layers.points.clearLayers();
    var shown = {};
    IMP.visible.forEach(function (f) { shown[f] = true; });
    IMP.items.forEach(function (it) {
      if (!shown[it.fid] && !IMP.sel[it.fid]) return;
      var on = !!IMP.sel[it.fid];
      var m = L.circleMarker([it.lat, it.lng], {
        radius: on ? 7 : 5, weight: it.point ? 3 : 1, color: it.point ? "#16a34a" : "#1e293b",
        fillColor: on ? "#2563eb" : "#cbd5e1", fillOpacity: 0.95,
      }).bindTooltip(esc(it.name), { direction: "top", offset: [0, -6] });
      m.on("click", function (e) {
        L.DomEvent.stopPropagation(e);
        if (IMP.lasso) return;
        if (IMP.sel[it.fid]) delete IMP.sel[it.fid]; else IMP.sel[it.fid] = true;
        paintImport();
      });
      m.addTo(S.layers.points);
    });
    if (IMP.lasso && IMP.lasso.length) {
      var ll = IMP.lasso.map(function (c) { return [c[1], c[0]]; });
      L.polyline(ll.concat(ll.length > 2 ? [ll[0]] : []), { color: "#f59e0b", weight: 2, dashArray: "6 4" }).addTo(S.layers.points);
      ll.forEach(function (p) { L.circleMarker(p, { radius: 4, color: "#f59e0b", fillOpacity: 1 }).addTo(S.layers.points); });
    }
  }

  function startLasso() {
    IMP.lasso = [];
    $("pt-imp-lasso-help").hidden = false;
    S.map.getContainer().style.cursor = "crosshair";
    drawImport();
  }
  function endLasso(apply) {
    var poly = IMP.lasso;
    IMP.lasso = null;
    $("pt-imp-lasso-help").hidden = true;
    S.map.getContainer().style.cursor = "";
    if (apply) {
      if (!poly || poly.length < 3) { toast("warning", "Au moins 3 points pour tracer une zone"); drawImport(); return; }
      var ring = poly.concat([poly[0]]);
      var n = 0;
      IMP.items.forEach(function (it) { if (pipRing(it.lat, it.lng, ring) && !IMP.sel[it.fid]) { IMP.sel[it.fid] = true; n++; } });
      toast("success", n + " objet(s) coche(s) dans la zone");
    }
    paintImport();
  }

  function doImport(asScenario) {
    // Ordre : celui de la liste (noms) ; Optimiser le remet dans l'ordre de passage
    var fids = IMP.items.filter(function (it) { return IMP.sel[it.fid]; }).map(function (it) { return it.fid; });
    if (!fids.length) return;
    if (asScenario && fids.length > 40) { toast("warning", "40 points au plus par scenario (" + fids.length + " coches)"); return; }
    var layerLabel = ($("pt-imp-layer").selectedOptions[0] || {}).textContent || "";
    apiPost("/api/patrols/points/import", { collection: IMP.layer, fids: fids }).then(function (r) {
      toast("success", r.ids.length + " point(s) crees ou mis a jour");
      return load().then(function () {
        if (!asScenario) { paintImport(); loadImportItems(); return; }
        closeImport();
        setTab("scenarios");
        openScenario(null);
        $("pt-sc-name").value = "Ronde " + layerLabel.toLowerCase();
        S.sc.points = r.ids.slice();
        renderScenarioPoints(); render(); fitScenario(); preview();
        if (r.ids.length >= 3) setTimeout(optimize, 400);
      });
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  // ---------- Scenarios ----------
  function renderScenarios() {
    var list = $("pt-sc-list"), showArch = $("pt-sc-archived").checked;
    clear(list);
    var scs = S.data.scenarios.filter(function (s) { return showArch || !s.archived; });
    if (!scs.length) list.appendChild(el("div", "pt-empty", "Aucun scenario."));
    scs.forEach(function (s) {
      var row = el("div", "pt-item" + (s.archived ? " off" : ""));
      var sw = el("span", "pt-swatch"); sw.style.background = s.color; row.appendChild(sw);
      var main = el("div", "pt-item-main");
      var t = el("div", "pt-item-title");
      t.appendChild(icon(TRAVEL_ICONS[s.travel] || "route"));
      t.appendChild(document.createTextNode(" " + s.name));
      main.appendChild(t);
      var est = s.estimate || {};
      var dl = (S.data.dominantes || []).find(function (d) { return d.id === s.dominante; });
      main.appendChild(el("div", "pt-item-sub", (dl && s.dominante !== "generale" ? dl.label + " - " : "") +
        s.points.length + " points" + (s.loop ? ", boucle" : "") +
        (est.distance_m ? " - " + fmtKm(est.distance_m) + ", " + fmtMin(est.duration_s) : "") +
        (est.error ? " - trajet non calcule" : "") + " - v" + s.version));
      row.appendChild(main);
      row.addEventListener("click", function () { openScenario(s); });
      list.appendChild(row);
    });
  }

  function openScenario(s) {
    S.sc = s ? { id: s.id, name: s.name, travel: s.travel, loop: s.loop, color: s.color, note: s.note,
                 dominante: s.dominante || "generale", points: s.points.slice(), archived: s.archived, estimate: s.estimate }
             : { id: null, name: "", travel: "vehicle", loop: false, color: "#2563eb", note: "", dominante: "generale",
                 points: [], estimate: null };
    $("pt-sc-editor").hidden = false;
    $("pt-sc-listpane").hidden = true;
    // Nouveau scenario : les points a ajouter sont montres ; scenario existant :
    // seulement les siens (les autres sur demande)
    $("pt-sc-others").checked = !s;
    $("pt-sc-name").value = S.sc.name;
    $("pt-sc-dominante").value = S.sc.dominante;
    $("pt-sc-color").value = S.sc.color;
    renderSwatches();
    $("pt-sc-loop").checked = S.sc.loop;
    $("pt-sc-note").value = S.sc.note || "";
    document.querySelectorAll('input[name="pt-sc-travel"]').forEach(function (r) { r.checked = r.value === S.sc.travel; });
    $("pt-sc-more").hidden = !S.sc.id;
    $("pt-sc-archive").textContent = S.sc.archived ? "Restaurer" : "Archiver";
    renderScenarioPoints();
    render();
    fitScenario();
    preview();
  }

  function closeScenario() {
    S.sc = null;
    $("pt-sc-editor").hidden = true;
    $("pt-sc-listpane").hidden = false;
    render();
  }

  function readScenarioForm() {
    S.sc.name = $("pt-sc-name").value;
    S.sc.color = $("pt-sc-color").value;
    S.sc.loop = $("pt-sc-loop").checked;
    S.sc.note = $("pt-sc-note").value;
    S.sc.dominante = $("pt-sc-dominante").value || "generale";
    var r = document.querySelector('input[name="pt-sc-travel"]:checked');
    S.sc.travel = r ? r.value : "vehicle";
  }

  function addPointToScenario(id) {
    if (!S.sc) return;
    if (S.sc.points.indexOf(id) >= 0 && !confirmDuplicate()) return;
    S.sc.points.push(id);
    renderScenarioPoints();
    render();
    preview();
  }
  function confirmDuplicate() { toast("warning", "Ce point est deja dans le scenario"); return false; }

  // Glisser-deposer : un trait bleu montre ou la ligne va tomber (avant ou
  // apres la ligne survolee, selon la moitie), y compris apres la derniere.
  // Fleches monter / descendre en plus (precis, utilisable sans souris).
  var dragFrom = null, dropAt = null;
  function movePoint(from, to) {
    if (from === to || to < 0 || to > S.sc.points.length) return;
    var moved = S.sc.points.splice(from, 1)[0];
    if (from < to) to -= 1;
    S.sc.points.splice(to, 0, moved);
    renderScenarioPoints(); render(); preview();
  }
  function clearDropMarks() {
    Array.prototype.forEach.call(document.querySelectorAll("#pt-sc-points .pt-step"), function (x) {
      x.classList.remove("drop-before", "drop-after");
    });
  }
  function renderScenarioPoints() {
    var ol = $("pt-sc-points");
    clear(ol);
    var legs = (S.sc.estimate && S.sc.estimate.legs) || [];
    var n = S.sc.points.length;
    S.sc.points.forEach(function (id, i) {
      var p = pointById(id) || { name: "(point supprime)" };
      var li = el("li", "pt-step");
      li.draggable = true;
      li.appendChild(icon("drag_indicator"));
      li.firstChild.classList.add("pt-step-grip");
      li.appendChild(el("span", "pt-step-num", String(i + 1)));
      var name = el("span", "pt-step-name", p.name);
      if (p.active === false) name.appendChild(el("em", "pt-muted", " (desactive)"));
      li.appendChild(name);
      if (i > 0 && legs[i - 1]) li.appendChild(el("span", "pt-step-leg", fmtKm(legs[i - 1].distance_m) + " - " + fmtMin(legs[i - 1].duration_s)));
      [["arrow_upward", "Monter", i === 0, function () { movePoint(i, i - 1); }],
       ["arrow_downward", "Descendre", i === n - 1, function () { movePoint(i, i + 2); }]].forEach(function (b) {
        var bt = el("button", "pt-step-mv"); bt.type = "button"; bt.title = b[1]; bt.disabled = b[2];
        bt.appendChild(icon(b[0]));
        bt.addEventListener("click", b[3]);
        li.appendChild(bt);
      });
      var rm = el("button", "pt-step-rm"); rm.type = "button"; rm.title = "Retirer"; rm.appendChild(icon("close"));
      rm.addEventListener("click", function () { S.sc.points.splice(i, 1); renderScenarioPoints(); render(); preview(); });
      li.appendChild(rm);
      li.addEventListener("dragstart", function (e) {
        dragFrom = i; li.classList.add("dragging");
        try { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(i)); } catch (x) { /* navigateur ancien */ }
      });
      li.addEventListener("dragend", function () { li.classList.remove("dragging"); clearDropMarks(); dragFrom = null; });
      li.addEventListener("dragover", function (e) {
        e.preventDefault();
        var r = li.getBoundingClientRect();
        var after = e.clientY > r.top + r.height / 2;
        clearDropMarks();
        li.classList.add(after ? "drop-after" : "drop-before");
        dropAt = after ? i + 1 : i;
      });
      li.addEventListener("drop", function (e) {
        e.preventDefault();
        clearDropMarks();
        if (dragFrom === null || dropAt === null) return;
        var from = dragFrom;
        dragFrom = null;
        movePoint(from, dropAt);
      });
      ol.appendChild(li);
    });
    if (!n) ol.appendChild(el("li", "pt-empty", "Aucun point : Ajouter des points, ou cliquez-les sur la carte (Montrer les autres points)."));
    renderZoneHint();
  }

  // ---------- Selecteur de points (recherche, plusieurs a la fois) ----------
  var PK = { sel: {}, order: [], visible: [] };
  function openPicker() {
    PK = { sel: {}, order: [], visible: [] };
    $("pt-pick-search").value = "";
    paintPicker();
    $("pt-pick-modal").hidden = false;
    $("pt-pick-search").focus();
  }
  function paintPicker() {
    var q = ($("pt-pick-search").value || "").toLowerCase();
    var inSc = {};
    S.sc.points.forEach(function (id) { inSc[id] = true; });
    var pts = S.data.points.filter(function (p) {
      if (!p.active || inSc[p.id]) return false;
      return !q || (p.name + " " + (p.zones || []).join(" ")).toLowerCase().indexOf(q) >= 0;
    });
    PK.visible = pts.map(function (p) { return p.id; });
    var box = $("pt-pick-list");
    clear(box);
    if (!pts.length) box.appendChild(el("div", "pt-empty", q ? "Aucun point ne correspond." : "Tous les points actifs sont deja dans le scenario."));
    pts.forEach(function (p) {
      var lab = el("label", "pt-pick-item" + (PK.sel[p.id] ? " on" : ""));
      var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !!PK.sel[p.id];
      cb.addEventListener("change", function () {
        if (cb.checked) { PK.sel[p.id] = true; PK.order.push(p.id); } else delete PK.sel[p.id];
        lab.classList.toggle("on", cb.checked);
        countPicker();
      });
      lab.appendChild(cb);
      var t = el("span", "pt-pick-name", p.name);
      lab.appendChild(t);
      var sub = [];
      if (p.zones && p.zones.length) sub.push(p.zones.slice(0, 2).join(", "));
      if (p.photo_required) sub.push("photo");
      if (p.source && p.source !== "manual") sub.push(p.source.replace("layer:", ""));
      if (sub.length) lab.appendChild(el("span", "pt-muted", sub.join(" - ")));
      box.appendChild(lab);
    });
    countPicker();
  }
  function countPicker() {
    var n = Object.keys(PK.sel).length;
    $("pt-pick-count").textContent = n ? n + " point" + (n > 1 ? "s" : "") + " coche" + (n > 1 ? "s" : "") : "";
    $("pt-pick-go").disabled = !n;
  }
  function applyPicker() {
    // Ordre : celui de la liste (alphabetique), plus lisible qu'un ordre de clic
    var ids = S.data.points.filter(function (p) { return PK.sel[p.id]; }).map(function (p) { return p.id; });
    if (S.sc.points.length + ids.length > 40) { toast("warning", "40 points au plus par scenario"); return; }
    ids.forEach(function (id) { if (S.sc.points.indexOf(id) < 0) S.sc.points.push(id); });
    $("pt-pick-modal").hidden = true;
    toast("success", ids.length + " point(s) ajoute(s)");
    renderScenarioPoints(); render(); fitScenario(); preview();
  }

  // ---------- Couleurs habituelles (categories de la main courante) ----------
  var SWATCHES = [["#ef4444", "Securite"], ["#dc2626", "Secours"], ["#f59e0b", "Technique"], ["#0d9488", "Flux"],
                  ["#2563eb", "Information"], ["#8b5cf6", "Main courante"], ["#16a34a", "Prevention"], ["#6b7280", "Fourriere"]];
  var DOMINANTE_COLORS = { surete: "#ef4444", secours: "#dc2626", incendie: "#f97316", prevention: "#16a34a", generale: "#2563eb" };
  function renderSwatches() {
    var box = $("pt-sc-swatches");
    clear(box);
    var cur = ($("pt-sc-color").value || "").toLowerCase();
    SWATCHES.forEach(function (s) {
      var b = el("button", "pt-swatch-btn" + (cur === s[0] ? " on" : ""));
      b.type = "button";
      b.title = s[1];
      b.style.background = s[0];
      b.setAttribute("aria-label", "Couleur " + s[1]);
      b.addEventListener("click", function () { $("pt-sc-color").value = s[0]; readScenarioForm(); renderSwatches(); render(); });
      box.appendChild(b);
    });
  }

  function renderZoneHint() {
    var hint = $("pt-sc-zone");
    if (!S.sc.points.length) { hint.textContent = ""; return; }
    var common = null;
    S.sc.points.forEach(function (id) {
      var z = (pointById(id) || {}).zone_ids || [];
      common = common === null ? z.slice() : common.filter(function (x) { return z.indexOf(x) >= 0; });
    });
    var devs = S.data.devices.filter(function (d) {
      if (!d.competence_zones.length) return false;
      return S.sc.points.every(function (id) {
        var z = (pointById(id) || {}).zone_ids || [];
        return d.competence_zones.some(function (x) { return z.indexOf(x) >= 0; });
      });
    });
    hint.textContent = (common && common.length ? "Zones communes a tous les points : " + common.map(zoneLabel).join(", ") + ". " : "Aucune zone commune a tous les points. ") +
      (devs.length ? "Tablettes avec zone qui couvrent le parcours : " + devs.map(function (d) { return d.name; }).join(", ") + "." : "") +
      " Les tablettes sans zone couvrent tout.";
  }

  // Liste des scenarios : le trajet de chacun dans sa couleur (clic = ouvrir),
  // jamais tous les points de la bibliotheque
  function drawScenarioRoutes() {
    var showArch = $("pt-sc-archived").checked;
    S.data.scenarios.forEach(function (s) {
      if (s.archived && !showArch) return;
      var est = s.estimate || {};
      var ll = est.polyline ? decode6(est.polyline)
        : s.points.map(pointById).filter(Boolean).map(function (p) { return [p.lat, p.lng]; });
      if (ll.length < 2) return;
      var line = L.polyline(ll, { color: safeColor(s.color), weight: 4, opacity: s.archived ? 0.3 : 0.75 })
        .bindTooltip(esc(s.name), { sticky: true });
      line.on("click", function () { openScenario(s); });
      line.addTo(S.layers.scenario);
    });
  }

  function drawScenarioEditor() {
    if ($("pt-sc-others") && $("pt-sc-others").checked) drawAllPoints(false, true);
    var color = $("pt-sc-color").value || S.sc.color;
    S.sc.points.forEach(function (id, i) {
      var p = pointById(id);
      if (!p) return;
      L.marker([p.lat, p.lng], { icon: numberIcon(i + 1, color), zIndexOffset: 1000 })
        .bindTooltip(esc(p.name), { direction: "top", offset: [0, -12] }).addTo(S.layers.scenario);
    });
    var est = S.sc.estimate;
    if (est && est.polyline) {
      L.polyline(decode6(est.polyline), { color: color, weight: 5, opacity: 0.85 }).addTo(S.layers.scenario);
    } else if (S.sc.points.length > 1) {
      var ll = S.sc.points.map(pointById).filter(Boolean).map(function (p) { return [p.lat, p.lng]; });
      if (S.sc.loop) ll.push(ll[0]);
      L.polyline(ll, { color: color, weight: 2, dashArray: "6 6" }).addTo(S.layers.scenario);
    }
  }

  function fitScenario() {
    var ll = (S.sc ? S.sc.points : []).map(pointById).filter(Boolean).map(function (p) { return [p.lat, p.lng]; });
    if (ll.length) S.map.fitBounds(L.latLngBounds(ll).pad(0.2), { maxZoom: 17 });
  }

  function preview() {
    if (!S.sc) return;
    readScenarioForm();
    var est = $("pt-sc-estimate");
    if (S.sc.points.length < 2) { S.sc.estimate = null; est.textContent = "Au moins deux points pour calculer le trajet."; return; }
    est.textContent = "Calcul du trajet...";
    clearTimeout(S.previewTimer);
    var seq = ++S.previewSeq;
    S.previewTimer = setTimeout(function () {
      apiPost("/api/patrols/preview", { name: S.sc.name || "apercu", travel: S.sc.travel, loop: S.sc.loop, points: S.sc.points })
        .then(function (r) {
          if (seq !== S.previewSeq || !S.sc) return;
          S.sc.estimate = r.estimate;
          var e = r.estimate || {};
          est.textContent = "Trajet : " + fmtKm(e.distance_m) + " - " + fmtMin(e.duration_s) +
            (S.sc.travel === "pedestrian" ? " a pied" : " en vehicule") + (S.sc.loop ? ", retour au depart compris" : "");
          renderScenarioPoints();
          render();
        }).catch(function (e) {
          if (seq !== S.previewSeq || !S.sc) return;
          S.sc.estimate = null;
          est.textContent = "Trajet non calcule : " + errLabel(e);
          render();
        });
    }, 350);
  }

  function optimize() {
    readScenarioForm();
    if (S.sc.points.length < 3) { toast("info", "Au moins trois points pour optimiser"); return; }
    apiPost("/api/patrols/optimize", { travel: S.sc.travel, loop: S.sc.loop, points: S.sc.points }).then(function (r) {
      if (!r.changed) { toast("info", "L'ordre actuel est deja le plus court"); return; }
      var before = S.sc.points.slice();
      S.sc.points = r.points;
      renderScenarioPoints(); render(); preview();
      if (typeof window.showConfirmToast === "function") {
        window.showConfirmToast("Ordre optimise : " + fmtMin(r.before_s) + " -> " + fmtMin(r.after_s) + ". Garder ce nouvel ordre ?",
          { okLabel: "Garder", cancelLabel: "Revenir" }).then(function (ok) {
          if (!ok && S.sc) { S.sc.points = before; renderScenarioPoints(); render(); preview(); }
        });
      }
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  function saveScenario() {
    readScenarioForm();
    var body = { name: S.sc.name, travel: S.sc.travel, loop: S.sc.loop, color: S.sc.color, note: S.sc.note,
                 dominante: S.sc.dominante, points: S.sc.points };
    var req = S.sc.id ? apiPut("/api/patrols/scenarios/" + S.sc.id, body) : apiPost("/api/patrols/scenarios", body);
    req.then(function (r) {
      toast("success", "Scenario enregistre (v" + r.scenario.version + ")");
      var i = S.data.scenarios.findIndex(function (s) { return s.id === r.scenario.id; });
      if (i >= 0) S.data.scenarios[i] = r.scenario; else S.data.scenarios.push(r.scenario);
      S.data.scenarios.sort(function (a, b) { return a.name.localeCompare(b.name); });
      fillSelects();
      openScenario(r.scenario);
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  function archiveScenario() {
    var archived = !S.sc.archived;
    apiPost("/api/patrols/scenarios/" + S.sc.id + "/archive", { archived: archived }).then(function () {
      var s = S.data.scenarios.find(function (x) { return x.id === S.sc.id; });
      if (s) s.archived = archived;
      toast("success", archived ? "Scenario archive" : "Scenario restaure");
      closeScenario();
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  // ---------- Envoyer maintenant ----------
  var STATUS_LABELS = { patrouille: "disponible", intervention: "en intervention", sur_place: "sur place",
                        pause: "en pause", fin_intervention: "fin d'intervention" };

  // scenarioId absent : choix du scenario dans la fenetre (depuis le Suivi)
  function openSend(scenarioId) {
    var sel = $("pt-send-scsel");
    var live = S.data.scenarios.filter(function (s) { return !s.archived && s.points.length; });
    if (!scenarioId && !live.length) { toast("info", "Creez d'abord un scenario (onglet Scenarios)"); return; }
    sel.hidden = !!scenarioId;
    clear(sel);
    live.forEach(function (s) { sel.appendChild(new Option(s.name + " - " + s.points.length + " points", s.id)); });
    S.sendSc = scenarioId || sel.value;
    sel.onchange = function () { S.sendSc = sel.value; };
    var sc = S.data.scenarios.find(function (s) { return s.id === S.sendSc; });
    $("pt-send-sc").textContent = scenarioId && sc ? sc.name + " - " + sc.points.length + " points" : "";
    var gs = $("pt-send-group");
    clear(gs);
    S.data.groups.filter(function (g) { return !g.declarant && !g.disabled; }).forEach(function (g) {
      var n = S.data.devices.filter(function (d) { return d.beacon_group_id === g.id; }).length;
      gs.appendChild(new Option(g.label + " (" + n + " tablette" + (n > 1 ? "s" : "") + ")", g.id));
    });
    var ds = $("pt-send-device");
    clear(ds);
    S.data.devices.filter(function (d) { return !d.declarant; }).forEach(function (d) {
      ds.appendChild(new Option((d.test_mode ? "[TEST] " : "") + d.name + " - " + (STATUS_LABELS[d.status] || d.status) + (d.recent ? "" : ", pas vue recemment") +
        (d.active_patrol_run ? ", deja en patrouille" : ""), d.id));
    });
    $("pt-send-result").hidden = true;
    $("pt-send-modal").hidden = false;
  }

  function doSend() {
    var kind = document.querySelector('input[name="pt-send-kind"]:checked').value;
    var target = kind === "group" ? { kind: "group", beacon_group_id: $("pt-send-group").value }
                                  : { kind: "device", device_id: $("pt-send-device").value };
    var res = $("pt-send-result");
    $("pt-send-go").disabled = true;
    apiPost("/api/patrols/scenarios/" + S.sendSc + "/send", { target: target }).then(function (r) {
      res.hidden = false; clear(res);
      // Envoi reussi : la fenetre se ferme, la suite (acceptation, refus) arrive
      // en message sur cette page et dans le Suivi
      res.hidden = true;
      $("pt-send-modal").hidden = true;
      toast("success", "Patrouille proposee a " + (r.run.current ? r.run.current.device_name : "?") +
        (r.run.current && r.run.current.distance_m != null ? " (" + fmtKm(r.run.current.distance_m) + " du depart)" : "") +
        " : reponse attendue sous " + S.data.settings.accept_timeout_s + " s");
      S.liveSeen[r.run.id] = r.run.state;
      setTab("suivi");
    }).catch(function (e) {
      var b = e.body || {};
      res.hidden = false; clear(res);
      res.className = "pt-send-result err";
      res.appendChild(el("div", null, b.code === "no_device" ? "Aucune tablette disponible : la patrouille n'est pas partie." : errLabel(e)));
      showSkipped(res, b.skipped || []);
    }).then(function () { $("pt-send-go").disabled = false; });
  }

  function showSkipped(box, skipped) {
    if (!skipped || !skipped.length) return;
    var ul = el("ul", "pt-skipped");
    skipped.forEach(function (s) { ul.appendChild(el("li", null, s.name + " : " + s.label)); });
    box.appendChild(el("div", "pt-muted", "Tablettes ecartees :"));
    box.appendChild(ul);
  }

  // ---------- Suivi ----------
  // Messages sur cette page quand une patrouille change d'etat (acceptee,
  // non prise, terminee...) : plus besoin de l'accueil pour voir un refus.
  // Le premier chargement ne fait que memoriser les etats.
  var LIVE_MSG = {
    running: ["success", function (r) { return r.device_name + " a accepte la patrouille " + r.scenario.name; }],
    paused: ["info", function (r) { return "Patrouille " + r.scenario.name + " en pause (" + r.device_name + " engage ailleurs)"; }],
    refused: ["error", function (r) { return "Patrouille non prise : " + r.scenario.name + (r.end_reason ? " - " + r.end_reason : ""); }],
    done: ["success", function (r) { return "Patrouille " + r.scenario.name + " terminee (" + r.counts.done + "/" + r.steps.length + " points)"; }],
    partial: ["warning", function (r) { return "Patrouille " + r.scenario.name + " terminee incomplete (" + r.counts.done + "/" + r.steps.length + " points)"; }],
    cancelled: ["info", function (r) { return "Patrouille " + r.scenario.name + " annulee"; }],
  };
  function notifyLive(runs) {
    var first = !S.liveSeenInit;
    S.liveSeenInit = true;
    runs.forEach(function (r) {
      var before = S.liveSeen[r.id];
      S.liveSeen[r.id] = r.state;
      if (first || before === r.state) return;
      // Reprise apres pause : message seulement pour une vraie acceptation
      if (r.state === "running" && before === "paused") { toast("info", "Patrouille " + r.scenario.name + " reprise"); return; }
      var m = LIVE_MSG[r.state];
      if (m) toast(m[0], m[1](r));
    });
  }

  function loadLive() {
    apiGet("/api/patrols/runs?scope=live").then(function (r) {
      S.live = r.runs;
      notifyLive(r.runs);
      updateCounts();
      if (S.tab === "suivi") render();
    }).catch(function () { /* silencieux : prochain passage */ });
  }

  function runCard(r, withActions) {
    var card = el("div", "pt-run st-" + r.state);
    var head = el("div", "pt-run-head");
    var sw = el("span", "pt-swatch"); sw.style.background = r.scenario.color || "#2563eb"; head.appendChild(sw);
    var title = el("div", "pt-run-title");
    title.appendChild(icon(TRAVEL_ICONS[r.scenario.travel] || "route"));
    title.appendChild(document.createTextNode(" " + (r.scenario.name || "?")));
    head.appendChild(title);
    head.appendChild(el("span", "pt-badge st-" + r.state, STATE_LABELS[r.state] || r.state));
    card.appendChild(head);
    var who = r.device_name || (r.current ? r.current.device_name + " (en attente de reponse)" : "-");
    var meta = [who];
    if (r.started_at) meta.push("depart " + fmtTime(r.started_at));
    else meta.push("envoyee " + fmtDateTime(r.created_at) + (r.created_by ? " par " + r.created_by : ""));
    if (r.ended_at) meta.push("fin " + fmtTime(r.ended_at));
    if (r.paused_s >= 60) meta.push(fmtMin(r.paused_s) + " de pause");
    card.appendChild(el("div", "pt-item-sub", meta.join(" - ")));
    if (r.started_at) {
      var total = r.steps.length, c = r.counts;
      var bar = el("div", "pt-progress");
      [["done", c.done], ["skipped", c.skipped], ["missed", c.missed]].forEach(function (x) {
        if (!x[1]) return;
        var seg = el("span"); seg.style.width = (100 * x[1] / total) + "%"; seg.style.background = STEP_COLORS[x[0]];
        bar.appendChild(seg);
      });
      card.appendChild(bar);
      var chips = el("div", "pt-chips");
      r.steps.forEach(function (s, i) {
        var ch = el("span", "pt-chip", (i + 1) + ". " + s.name + (s.at ? " " + fmtTime(s.at) : ""));
        ch.style.borderColor = STEP_COLORS[s.state]; ch.style.color = STEP_COLORS[s.state] === "#94a3b8" ? "" : STEP_COLORS[s.state];
        ch.title = s.state === "skipped" ? "Passe : " + (s.comment || "") : (s.method === "gps" ? "Valide au passage GPS" : "");
        chips.appendChild(ch);
      });
      card.appendChild(chips);
    }
    if (r.end_reason) card.appendChild(el("div", "pt-muted", r.end_reason));
    if (r.attempts.length && !r.started_at) {
      card.appendChild(el("div", "pt-muted", r.attempts.map(function (a) {
        return a.device_name + " : " + ({ refused: "refus", timeout: "sans reponse", accepted: "acceptee" }[a.answer] || a.answer) + (a.reason ? " (" + a.reason + ")" : "");
      }).join(" ; ")));
    }
    var ficheId = r.fiche_id || r.not_taken_fiche;
    var open = withActions && ["proposed", "running", "paused"].indexOf(r.state) >= 0;
    if (ficheId || open) {
      var act = el("div", "pt-row pt-actions");
      if (ficheId) act.appendChild(ficheLink(ficheId, r.fiche_id ? "Voir la fiche de main courante" : "Voir la fiche Non prise"));
      act.appendChild(el("span", "pt-spacer"));
      if (open) {
        var b = el("button", "btn pt-danger", "Annuler la patrouille"); b.type = "button";
        b.addEventListener("click", function (e) { e.stopPropagation(); cancelRun(r); });
        act.appendChild(b);
      }
      card.appendChild(act);
    }
    card.addEventListener("click", function () { focusRun(r); });
    return card;
  }

  // Bouton "Voir la fiche" : fenetre de lecture sur cette page (en-tete,
  // bloc patrouille avec sa carte, chronologie) ; lien vers l'accueil dedans.
  function ficheLink(id, label) {
    var b = el("button", "btn pt-fiche-link");
    b.type = "button";
    b.appendChild(icon("description"));
    b.appendChild(document.createTextNode(" " + label));
    b.addEventListener("click", function (e) { e.stopPropagation(); openFiche(id); });
    return b;
  }

  function openFiche(id) {
    var body = $("pt-fiche-body");
    clear(body);
    body.appendChild(el("div", "pt-hint", "Chargement..."));
    $("pt-fiche-title").textContent = "Fiche";
    $("pt-fiche-home").href = "/?fiche=" + encodeURIComponent(id);
    $("pt-fiche-modal").hidden = false;
    apiGet("/api/pcorg/detail/" + encodeURIComponent(id)).then(function (d) {
      clear(body);
      var cc = d.content_category || {};
      $("pt-fiche-title").textContent = (d.text || "Fiche").slice(0, 90);
      var meta = el("div", "pt-fiche-meta");
      var st = el("span", "pt-badge " + (d.status_code === 10 ? "st-done" : "st-running"), d.status_code === 10 ? "Terminee" : "En cours");
      meta.appendChild(st);
      meta.appendChild(el("span", "pt-muted", (d.category || "").replace("PCO.", "") + (d.ts ? " - ouverte le " + fmtDateTime(d.ts) : "") +
        (d.close_ts && d.status_code === 10 ? " - close le " + fmtDateTime(d.close_ts) : "")));
      body.appendChild(meta);
      if (cc.patrol_run && window.PatrolFicheMap) window.PatrolFicheMap.render(body, cc.patrol_run);
      var hist = d.comment_history || [];
      body.appendChild(el("div", "pt-step-title", "Chronologie"));
      var list = el("div", "pt-chrono");
      if (!hist.length) list.appendChild(el("div", "pt-empty", "Aucune entree."));
      hist.forEach(function (e) {
        var row = el("div", "pt-chrono-row");
        row.appendChild(el("span", "pt-chrono-ts", e.ts ? fmtDateTime(e.ts) : ""));
        var main = el("div", "pt-chrono-main");
        main.appendChild(el("span", "pt-chrono-op", String(e.operator || "").replace(/^field:/, "")));
        main.appendChild(el("div", "pt-chrono-text", e.body || e.text || ""));
        (e.photos || (e.thumb ? [{ thumb: e.thumb, photo: e.photo }] : [])).forEach(function (p) {
          if (!p || !p.thumb) return;
          var a = el("a"); a.href = p.photo || p.thumb; a.target = "_blank"; a.rel = "noopener";
          var img = el("img", "pt-chrono-img"); img.src = p.thumb; img.alt = "Photo";
          a.appendChild(img);
          main.appendChild(a);
        });
        row.appendChild(main);
        list.appendChild(row);
      });
      body.appendChild(list);
    }).catch(function (e) { clear(body); body.appendChild(el("div", "pt-empty", "Fiche indisponible : " + errLabel(e))); });
  }

  function renderLive() {
    renderLiveSummary();
    var box = $("pt-live");
    clear(box);
    if (!S.live.length) box.appendChild(el("div", "pt-empty", "Aucune patrouille en cours ni recente. Envoyez-en une depuis l'onglet Scenarios."));
    S.live.forEach(function (r) { box.appendChild(runCard(r, true)); });
    S.live.filter(function (r) { return ["proposed", "running", "paused"].indexOf(r.state) >= 0; }).forEach(drawRun);
  }

  function drawRun(r) {
    var color = r.scenario.color || "#2563eb";
    var est = r.scenario.estimate || {};
    if (est.polyline) L.polyline(decode6(est.polyline), { color: color, weight: 4, opacity: r.state === "paused" ? 0.35 : 0.7 }).addTo(S.layers.live);
    r.steps.forEach(function (s, i) {
      if (s.lat == null) return;
      L.marker([s.lat, s.lng], { icon: numberIcon(i + 1, STEP_COLORS[s.state]) })
        .bindTooltip(esc(s.name) + (s.at ? " - " + fmtTime(s.at) : ""), { direction: "top", offset: [0, -12] }).addTo(S.layers.live);
    });
    if (r.position && r.position.lat != null) {
      // Rond + nom de la tablette (une fleche laissait croire a une direction)
      L.marker([r.position.lat, r.position.lng], { icon: L.divIcon({ className: "pt-dev-icon", html: '<div class="pt-dev-dot"><i style="background:' + safeColor(color) + ';box-shadow:0 0 0 2px ' + safeColor(color) + '"></i>' + esc(r.device_name || "?") + '</div>', iconSize: [160, 20], iconAnchor: [8, 10] }), zIndexOffset: 2000 })
        .bindTooltip(esc(r.device_name || "?") + " - position " + fmtTime(r.position.ts), { permanent: false }).addTo(S.layers.live);
    }
  }

  function focusRun(r) {
    var ll = r.steps.filter(function (s) { return s.lat != null; }).map(function (s) { return [s.lat, s.lng]; });
    if (r.position && r.position.lat != null) ll.push([r.position.lat, r.position.lng]);
    if (ll.length) S.map.fitBounds(L.latLngBounds(ll).pad(0.2), { maxZoom: 17 });
  }

  function cancelRun(r) {
    var ask = typeof window.showPromptToast === "function"
      ? window.showPromptToast("Annuler la patrouille " + r.scenario.name + " ? Motif (facultatif) :", { okLabel: "Annuler la patrouille", cancelLabel: "Retour" })
      : Promise.resolve("");
    ask.then(function (reason) {
      if (reason === null) return;
      apiPost("/api/patrols/runs/" + r.id + "/cancel", { reason: reason }).then(function () {
        toast("success", "Patrouille annulee");
        loadLive();
      }).catch(function (e) { toast("error", errLabel(e)); });
    });
  }

  // ---------- Historique ----------
  function loadHistory() {
    var q = "/api/patrols/runs?scope=history&days=" + $("pt-hist-days").value + ($("pt-hist-sc").value ? "&scenario=" + $("pt-hist-sc").value : "");
    apiGet(q).then(function (r) {
      var st = $("pt-hist-stats");
      clear(st);
      if (r.stats.length) {
        var t = el("table", "pt-table");
        var h = el("tr");
        ["Scenario", "Envoyees", "Prises", "Non prises", "Points faits"].forEach(function (x) { h.appendChild(el("th", null, x)); });
        t.appendChild(h);
        r.stats.forEach(function (s) {
          var tr = el("tr");
          tr.appendChild(el("td", null, s.name));
          tr.appendChild(el("td", null, s.runs));
          tr.appendChild(el("td", null, s.taken));
          tr.appendChild(el("td", null, s.not_taken));
          tr.appendChild(el("td", null, s.points ? Math.round(100 * s.done / s.points) + " %" : "-"));
          t.appendChild(tr);
        });
        st.appendChild(t);
      }
      var list = $("pt-hist-list");
      clear(list);
      if (!r.runs.length) {
        var tr0 = el("tr"), td0 = el("td", "pt-empty", "Aucune patrouille sur la periode.");
        td0.colSpan = 7; tr0.appendChild(td0); list.appendChild(tr0);
      }
      r.runs.forEach(function (x) {
        var tr = el("tr");
        tr.appendChild(el("td", "num", fmtDateTime(x.created_at)));
        tr.appendChild(el("td", null, x.scenario.name || "?"));
        tr.appendChild(el("td", null, x.device_name || (x.attempts.length ? x.attempts.map(function (a) { return a.device_name; }).join(", ") : "-")));
        var tds = el("td"); tds.appendChild(el("span", "pt-badge st-" + x.state, STATE_LABELS[x.state] || x.state)); tr.appendChild(tds);
        tr.appendChild(el("td", "num", x.started_at ? x.counts.done + " / " + x.steps.length + (x.counts.skipped ? " (" + x.counts.skipped + " passe)" : "") : "-"));
        var dur = x.started_at && x.ended_at ? Math.round((new Date(x.ended_at) - new Date(x.started_at)) / 1000) : null;
        tr.appendChild(el("td", "num", dur != null ? fmtMin(dur) : "-"));
        var tdd = el("td", "pt-muted", x.end_reason || (x.created_by ? "par " + x.created_by : ""));
        var fid = x.fiche_id || x.not_taken_fiche;
        if (fid) { tdd.appendChild(document.createTextNode(" ")); tdd.appendChild(ficheLink(fid, "Fiche")); }
        tr.appendChild(tdd);
        list.appendChild(tr);
      });
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  // ---------- Planifications ----------
  var RULE_LABELS = { span_start: "une fois au debut de chaque plage", every: "toutes les " };
  function fmtEvery(m) { return m % 60 ? (m >= 60 ? Math.floor(m / 60) + " h " + (m % 60) : m + " min") : (m / 60) + " h"; }
  function fmtWhen(iso) {
    var d = new Date(iso), today = new Date();
    var same = d.toDateString() === today.toDateString();
    var tmr = new Date(today.getTime() + 86400000).toDateString() === d.toDateString();
    return (same ? "aujourd'hui" : tmr ? "demain" : d.toLocaleDateString("fr-FR", { weekday: "short", day: "2-digit", month: "2-digit" })) + " " + fmtTime(iso);
  }
  function targetLabel(t) {
    if (!t) return "-";
    if (t.kind === "device") {
      var d = S.data.devices.find(function (x) { return x.id === t.device_id; });
      return "tablette " + (d ? d.name : "(supprimee)");
    }
    var g = S.data.groups.find(function (x) { return x.id === t.beacon_group_id; });
    return "groupe " + (g ? g.label : t.beacon_group_id);
  }
  function explainLabel(p) {
    var e = p.explain || "";
    if (e === "desactivee") return "Desactivee";
    if (e === "override") return p.arming.override ? (p.arming.override.state === "on" ? "Forcee active" : "Suspendue") + " jusqu'a " + fmtWhen(p.arming.override.until) : "";
    if (e === "always") return "Tout le temps";
    if (e === "off") return "En pause";
    var parts = e.split(":");
    return (p.active_now ? "En plage" : "Hors plage") + (parts[1] ? " - " + parts.slice(1).join(":") : "");
  }

  function loadPlans() {
    return apiGet("/api/patrols/plans").then(function (r) {
      S.plans = r.plans;
      updateCounts();
      if (S.tab === "plans") renderPlans();
      if (S.tab === "suivi") renderLiveSummary();
    }).catch(function () { /* silencieux */ });
  }

  function renderPlans() {
    var list = $("pt-plan-list");
    clear(list);
    if (!S.plans || !S.plans.length) {
      list.appendChild(el("div", "pt-empty", "Aucune planification. Exemple : la ronde des parkings toutes les 2 h de 8 h a 20 h, envoyee au groupe Securite."));
      return;
    }
    S.plans.forEach(function (p) {
      var card = el("div", "pt-plan" + (p.enabled ? "" : " off"));
      var head = el("div", "pt-run-head");
      var sw = el("span", "pt-swatch"); sw.style.background = p.scenario_color || "#2563eb"; head.appendChild(sw);
      var t = el("div", "pt-run-title", p.name);
      head.appendChild(t);
      var tog = el("label", "pt-switch");
      var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = p.enabled;
      cb.addEventListener("change", function () {
        apiPost("/api/patrols/plans/" + p.id + "/enabled", { enabled: cb.checked }).then(function () {
          toast("success", cb.checked ? "Planification activee" : "Planification desactivee"); loadPlans();
        }).catch(function (e) { cb.checked = !cb.checked; toast("error", errLabel(e)); });
      });
      tog.appendChild(cb); tog.appendChild(el("span"));
      tog.title = p.enabled ? "Active (cliquer pour desactiver)" : "Desactivee (cliquer pour activer)";
      head.appendChild(tog);
      card.appendChild(head);
      card.appendChild(el("div", "pt-item-sub", (p.scenario_name || "(scenario supprime)") + " - " + targetLabel(p.target) + " - " +
        (p.launch.rule === "every" ? RULE_LABELS.every + fmtEvery(p.launch.every_min) + " pendant les plages" : RULE_LABELS.span_start) +
        (p.valid_from || p.valid_until ? " - du " + (p.valid_from || "...") + " au " + (p.valid_until || "...") : "")));
      var state = el("div", "pt-plan-state");
      state.appendChild(el("span", "pt-badge " + (p.active_now ? "st-running" : (p.enabled ? "" : "st-cancelled")), explainLabel(p)));
      state.appendChild(el("span", "pt-muted", p.next.length ? "Prochain : " + p.next.slice(0, 3).map(fmtWhen).join(", ") : "Aucun lancement prevu sous 14 jours"));
      card.appendChild(state);
      if (p.last_launch) card.appendChild(el("div", "pt-muted", "Dernier lancement " + fmtWhen(p.last_launch.at) + " : " + p.last_launch.result));
      var acts = el("div", "pt-row pt-actions");
      var ed = el("button", "btn", ""); ed.type = "button"; ed.appendChild(icon("edit")); ed.appendChild(document.createTextNode(" Modifier"));
      ed.addEventListener("click", function () { openPlan(p); });
      acts.appendChild(ed);
      if (p.enabled) {
        if (p.arming.override) {
          var back = el("button", "btn", "Revenir au planning"); back.type = "button";
          back.addEventListener("click", function () { overridePlan(p, null); });
          acts.appendChild(back);
        } else {
          var off = el("button", "btn", "Suspendre 4 h"); off.type = "button"; off.title = "Aucun lancement pendant 4 h";
          off.addEventListener("click", function () { overridePlan(p, "off"); });
          var on = el("button", "btn", "Lancer pendant 4 h"); on.type = "button"; on.title = "Comme si la plage etait active pendant 4 h (lancement immediat)";
          on.addEventListener("click", function () { overridePlan(p, "on"); });
          acts.appendChild(off); acts.appendChild(on);
        }
      }
      card.appendChild(acts);
      list.appendChild(card);
    });
  }

  function overridePlan(p, state) {
    apiPost("/api/patrols/plans/" + p.id + "/override", { state: state, hours: 4 }).then(function () {
      toast("success", state === "off" ? "Suspendue 4 h" : state === "on" ? "Lancements actives pendant 4 h" : "Retour au planning");
      loadPlans();
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  var PLAN = null;   // {id, grid}
  function planDirty() { $("pt-plan-dirty").textContent = "Modifications non enregistrees"; scheduleWhen(); }

  // Etape 5 expliquee en clair (selon la regle de l'etape 3) et prochains
  // departs recalcules a chaque modification
  var whenTimer = null;
  function scheduleWhen() { clearTimeout(whenTimer); whenTimer = setTimeout(updateWhen, 500); }
  function updateWhen() {
    var box = $("pt-plan-when");
    if (!box || !PLAN || !PLAN.grid) return;
    var rule = document.querySelector('input[name="pt-plan-rule"]:checked').value;
    var every = Number($("pt-plan-every").value);
    var mode = PLAN.grid.arming().mode;
    clear(box);
    var txt = mode === "off" ? "En pause : aucune patrouille ne part."
      : mode === "always" ? (rule === "every" ? "Tout le temps : une patrouille part toutes les " + fmtEvery(every) + ", jour et nuit (a partir de minuit)."
                                              : "Tout le temps : une seule patrouille au debut (choisissez plutot Toutes les ... a l'etape 3).")
      : rule === "every" ? "Peignez ci-dessous les plages horaires (en couleur). Pendant chaque plage, une patrouille part toutes les " + fmtEvery(every) +
          " a partir du debut de la plage. Ex. plage 08:00-12:00, toutes les 2 h : departs a 08:00 et 10:00."
      : "Peignez ci-dessous les plages horaires (en couleur). Une patrouille part au DEBUT de chaque plage. Ex. plage 08:00-12:00 : un depart a 08:00.";
    box.appendChild(el("div", "pt-hint", txt));
    var nx = el("div", "pt-when-next", "Prochains departs : calcul...");
    box.appendChild(nx);
    apiPost("/api/patrols/plans/preview", planBody()).then(function (r) {
      nx.textContent = (r.next || []).length
        ? "Prochains departs : " + r.next.slice(0, 6).map(fmtWhen).join(", ")
        : "Aucun depart dans les 14 prochains jours avec ce planning.";
    }).catch(function () { nx.textContent = ""; });
  }

  function fillPlanSelects() {
    var sc = $("pt-plan-sc");
    clear(sc);
    S.data.scenarios.filter(function (s) { return !s.archived; }).forEach(function (s) {
      sc.appendChild(new Option(s.name + " (" + s.points.length + " points" + (s.estimate && s.estimate.duration_s ? ", " + fmtMin(s.estimate.duration_s) : "") + ")", s.id));
    });
    var gs = $("pt-plan-group");
    clear(gs);
    S.data.groups.filter(function (g) { return !g.declarant && !g.disabled; }).forEach(function (g) { gs.appendChild(new Option(g.label, g.id)); });
    var ds = $("pt-plan-device");
    clear(ds);
    S.data.devices.filter(function (d) { return !d.declarant; }).forEach(function (d) { ds.appendChild(new Option(d.name + (d.group_label ? " (" + d.group_label + ")" : ""), d.id)); });
  }

  function planBody() {
    var kind = document.querySelector('input[name="pt-plan-kind"]:checked').value;
    return {
      name: $("pt-plan-name").value, scenario_id: $("pt-plan-sc").value,
      target: kind === "group" ? { kind: "group", beacon_group_id: $("pt-plan-group").value } : { kind: "device", device_id: $("pt-plan-device").value },
      launch: { rule: document.querySelector('input[name="pt-plan-rule"]:checked').value, every_min: Number($("pt-plan-every").value) },
      valid_from: $("pt-plan-from").value || null, valid_until: $("pt-plan-until").value || null,
      enabled: $("pt-plan-enabled").checked, note: $("pt-plan-note").value,
      arming: PLAN.grid.arming(),
    };
  }

  function openPlan(p) {
    if (!S.data.scenarios.some(function (s) { return !s.archived; })) { toast("info", "Creez d'abord un scenario (onglet Scenarios)"); return; }
    fillPlanSelects();
    PLAN = { id: p ? p.id : null };
    $("pt-plan-title").textContent = p ? p.name : "Nouvelle planification";
    $("pt-plan-name").value = p ? p.name : "";
    if (p && p.scenario_id) $("pt-plan-sc").value = p.scenario_id;
    var t = (p && p.target) || { kind: "group" };
    document.querySelectorAll('input[name="pt-plan-kind"]').forEach(function (r) { r.checked = r.value === t.kind; });
    if (t.beacon_group_id) $("pt-plan-group").value = t.beacon_group_id;
    if (t.device_id) $("pt-plan-device").value = t.device_id;
    var l = (p && p.launch) || { rule: "span_start", every_min: 120 };
    document.querySelectorAll('input[name="pt-plan-rule"]').forEach(function (r) { r.checked = r.value === l.rule; });
    $("pt-plan-every").value = String(l.every_min || 120);
    if (!$("pt-plan-every").value) $("pt-plan-every").value = "120";
    $("pt-plan-from").value = (p && p.valid_from) || "";
    $("pt-plan-until").value = (p && p.valid_until) || "";
    $("pt-plan-enabled").checked = p ? p.enabled : true;
    $("pt-plan-note").value = (p && p.note) || "";
    $("pt-plan-delete").hidden = !p;
    PLAN.grid = window.ScheduleGrid.create($("pt-plan-grid"), p ? p.arming : null, {
      events: S.data.events || [], onLabel: "Plage",
      onChange: planDirty,
      preview: function (month) {
        return apiPost("/api/patrols/plans/preview?month=" + month, planBody());
      },
    });
    $("pt-plan-dirty").textContent = "";
    $("pt-plan-modal").hidden = false;
    updateWhen();
  }

  function closePlan() {
    if ($("pt-plan-dirty").textContent && typeof window.showConfirmToast === "function") {
      window.showConfirmToast("Fermer sans enregistrer les modifications ?", { okLabel: "Fermer", type: "warning" }).then(function (ok) {
        if (ok) $("pt-plan-modal").hidden = true;
      });
    } else $("pt-plan-modal").hidden = true;
  }

  function savePlan() {
    var body = planBody();
    if (!body.name.trim()) { toast("warning", "Donnez un nom a la planification"); $("pt-plan-name").focus(); return; }
    var req = PLAN.id ? apiPut("/api/patrols/plans/" + PLAN.id, body) : apiPost("/api/patrols/plans", body);
    req.then(function (r) {
      var p = r.plan;
      toast("success", "Planification enregistree" + (p.next.length ? " - prochain lancement " + fmtWhen(p.next[0]) : ""));
      $("pt-plan-dirty").textContent = "";
      $("pt-plan-modal").hidden = true;
      loadPlans();
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  function deletePlan() {
    var go = typeof window.showConfirmToast === "function"
      ? window.showConfirmToast("Supprimer cette planification ? Les patrouilles deja envoyees restent dans l'historique.", { okLabel: "Supprimer", type: "warning" })
      : Promise.resolve(true);
    go.then(function (ok) {
      if (!ok) return;
      apiSend("DELETE", "/api/patrols/plans/" + PLAN.id).then(function () {
        toast("success", "Planification supprimee");
        $("pt-plan-dirty").textContent = "";
        $("pt-plan-modal").hidden = true;
        loadPlans();
      }).catch(function (e) { toast("error", errLabel(e)); });
    });
  }

  function renderLiveSummary() {
    var box = $("pt-live-summary");
    if (!box) return;
    clear(box);
    var c = { running: 0, paused: 0, proposed: 0, done: 0, notTaken: 0 };
    (S.live || []).forEach(function (r) {
      if (r.state === "running") c.running++;
      else if (r.state === "paused") c.paused++;
      else if (r.state === "proposed") c.proposed++;
      else if (r.state === "done" || r.state === "partial") c.done++;
      else if (r.state === "refused" || r.state === "expired") c.notTaken++;
    });
    [["En cours", c.running, "#16a34a"], ["En pause", c.paused, "#d97706"], ["En attente de reponse", c.proposed, "#2563eb"],
     ["Terminees", c.done, "#475569"], ["Non prises", c.notTaken, "#dc2626"]].forEach(function (x) {
      var k = el("div", "pt-kpi");
      var v = el("b", null, x[1]); v.style.color = x[1] ? x[2] : "#94a3b8";
      k.appendChild(v); k.appendChild(el("span", null, x[0]));
      box.appendChild(k);
    });
    var nexts = [];
    (S.plans || []).forEach(function (p) { if (p.enabled && p.next.length) nexts.push({ at: p.next[0], name: p.name }); });
    nexts.sort(function (a, b) { return a.at < b.at ? -1 : 1; });
    if (nexts.length) {
      var n = el("div", "pt-next-launches");
      n.appendChild(el("b", null, "Prochains lancements : "));
      n.appendChild(document.createTextNode(nexts.slice(0, 4).map(function (x) { return x.name + " " + fmtWhen(x.at); }).join(" - ")));
      box.appendChild(n);
    }
  }

  // ---------- Tablettes ----------
  function fmtAgo(iso) {
    if (!iso) return "jamais";
    var s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return "a l'instant";
    if (s < 3600) return "il y a " + Math.round(s / 60) + " min";
    if (s < 86400) return "il y a " + Math.round(s / 3600) + " h";
    return "il y a " + Math.round(s / 86400) + " j";
  }

  function renderDevices() {
    var body = $("pt-dev-list");
    clear(body);
    var q = ($("pt-dev-search").value || "").toLowerCase();
    var devs = S.data.devices.filter(function (d) {
      return !q || (d.name + " " + (d.group_label || "")).toLowerCase().indexOf(q) >= 0;
    });
    if (!devs.length) {
      var tr0 = el("tr"), td0 = el("td", "pt-empty", q ? "Aucune tablette ne correspond." : "Aucune tablette appairee.");
      td0.colSpan = 7; tr0.appendChild(td0); body.appendChild(tr0);
      return;
    }
    devs.forEach(function (d) {
      var tr = el("tr");
      var tdn = el("td");
      tdn.appendChild(el("b", null, d.name));
      if (d.test_mode) { tdn.appendChild(document.createTextNode(" ")); tdn.appendChild(el("span", "pt-chip test", "TEST")); }
      if (d.declarant) { tdn.appendChild(document.createTextNode(" ")); tdn.appendChild(el("span", "pt-chip", "declarante")); }
      tr.appendChild(tdn);
      tr.appendChild(el("td", null, d.group_label || "-"));
      var tds = el("td");
      var st = el("span", "pt-status s-" + d.status);
      st.appendChild(el("i"));
      st.appendChild(document.createTextNode(STATUS_LABELS[d.status] || d.status));
      tds.appendChild(st);
      tr.appendChild(tds);
      var tdv = el("td", "num", fmtAgo(d.last_seen));
      if (!d.recent) tdv.style.color = "#b45309";
      tr.appendChild(tdv);
      tr.appendChild(el("td", null, d.active_patrol_run ? "en cours" : (d.proposal ? "proposition" : "-")));
      var tdz = el("td");
      var chips = el("div", "pt-zone-chips");
      if (!d.competence_zones.length) chips.appendChild(el("span", "pt-chip", "Partout"));
      d.competence_zones.slice(0, 4).forEach(function (z) { chips.appendChild(el("span", "pt-chip zone", zoneLabel(z))); });
      if (d.competence_zones.length > 4) chips.appendChild(el("span", "pt-chip", "+" + (d.competence_zones.length - 4)));
      tdz.appendChild(chips);
      tr.appendChild(tdz);
      var tda = el("td");
      var b = el("button", "btn", ""); b.type = "button";
      b.appendChild(icon("fence")); b.appendChild(document.createTextNode(" Zones"));
      b.addEventListener("click", function () { openZones(d); });
      tda.appendChild(b);
      tr.appendChild(tda);
      body.appendChild(tr);
    });
  }

  // Fenetre des zones de competence : recherche, regroupement par famille
  // (Departements, Lots, Secteurs, autres) d'apres le debut du nom.
  var ZD = null;
  function zoneFamily(label) {
    var l = (label || "").toLowerCase();
    if (l.indexOf("departement") === 0 || l.indexOf("département") === 0) return "Departements";
    if (l.indexOf("lot") === 0) return "Lots";
    if (l.indexOf("secteur") === 0) return "Secteurs";
    return "Autres zones";
  }
  function openZones(d) {
    ZD = { dev: d, sel: d.competence_zones.slice() };
    $("pt-zone-title").textContent = "Zones de competence de " + d.name;
    $("pt-zone-search").value = "";
    paintZones();
    $("pt-zone-modal").hidden = false;
    $("pt-zone-search").focus();
  }
  function paintZones() {
    var picked = $("pt-zone-picked");
    clear(picked);
    if (!ZD.sel.length) picked.appendChild(el("span", "pt-chip", "Partout (aucune zone)"));
    ZD.sel.forEach(function (z) { picked.appendChild(el("span", "pt-chip zone", zoneLabel(z))); });
    var q = ($("pt-zone-search").value || "").toLowerCase();
    var box = $("pt-zone-groups");
    clear(box);
    var fams = {};
    S.data.zones.forEach(function (z) {
      if (q && z.label.toLowerCase().indexOf(q) < 0) return;
      var f = zoneFamily(z.label);
      (fams[f] = fams[f] || []).push(z);
    });
    ["Departements", "Lots", "Secteurs", "Autres zones"].forEach(function (f) {
      if (!fams[f]) return;
      var g = el("div", "pt-zone-group");
      g.appendChild(el("b", null, f + " (" + fams[f].length + ")"));
      var grid = el("div", "pt-zone-grid");
      fams[f].forEach(function (z) {
        var lab = el("label");
        var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = ZD.sel.indexOf(z.id) >= 0;
        cb.addEventListener("change", function () {
          ZD.sel = ZD.sel.filter(function (x) { return x !== z.id; });
          if (cb.checked) ZD.sel.push(z.id);
          paintZones();
        });
        lab.appendChild(cb); lab.appendChild(document.createTextNode(z.label));
        grid.appendChild(lab);
      });
      g.appendChild(grid);
      box.appendChild(g);
    });
    if (!box.firstChild) box.appendChild(el("div", "pt-empty", "Aucune zone ne correspond."));
  }
  function saveZones() {
    var d = ZD.dev;
    apiPost("/api/patrols/devices/" + d.id + "/zones", { zones: ZD.sel }).then(function (r) {
      d.competence_zones = r.zones;
      $("pt-zone-modal").hidden = true;
      toast("success", "Zones de " + d.name + " enregistrees");
      renderDevices();
    }).catch(function (e) { toast("error", errLabel(e)); });
  }

  // ---------- Reglages ----------
  function saveSettings() {
    apiPut("/api/patrols/settings", {
      radius_m: Number($("pt-set-radius").value), accept_timeout_s: Number($("pt-set-timeout").value),
      seen_window_min: Number($("pt-set-seen").value),
      layers: Array.prototype.slice.call(document.querySelectorAll("#pt-set-layers input:checked")).map(function (c) { return c.value; }),
    }).then(function (r) {
      S.data.settings = r.settings;
      (S.data.layers || []).forEach(function (l) { l.selected = r.settings.layers.indexOf(l.collection) >= 0; });
      fillSelects();
      toast("success", "Reglages enregistres");
    })
      .catch(function (e) { toast("error", errLabel(e)); });
  }

  // ---------- Init ----------
  function bind() {
    document.querySelectorAll("#pt-tabs button").forEach(function (b) { b.addEventListener("click", function () { setTab(b.dataset.tab); }); });
    $("pt-refresh").addEventListener("click", function () {
      load().then(function () { loadPlans(); if (S.tab === "suivi") loadLive(); if (S.tab === "historique") loadHistory(); });
    });
    $("pt-pt-search").addEventListener("input", renderPoints);
    $("pt-pt-inactive").addEventListener("change", render);
    $("pt-pt-save").addEventListener("click", savePoint);
    $("pt-pt-cancel").addEventListener("click", closePointEditor);
    $("pt-pt-toggle").addEventListener("click", togglePoint);
    $("pt-pt-radius").addEventListener("change", render);
    $("pt-sc-new").addEventListener("click", function () { openScenario(null); });
    $("pt-sc-archived").addEventListener("change", render);
    $("pt-sc-add").addEventListener("click", openPicker);
    $("pt-sc-others").addEventListener("change", render);
    $("pt-pick-search").addEventListener("input", paintPicker);
    $("pt-pick-all").addEventListener("click", function () { PK.visible.forEach(function (id) { PK.sel[id] = true; }); paintPicker(); });
    $("pt-pick-none").addEventListener("click", function () { PK.sel = {}; paintPicker(); });
    $("pt-pick-close").addEventListener("click", function () { $("pt-pick-modal").hidden = true; });
    $("pt-pick-go").addEventListener("click", applyPicker);
    $("pt-sc-cancel").addEventListener("click", closeScenario);
    $("pt-sc-save").addEventListener("click", saveScenario);
    $("pt-sc-optimize").addEventListener("click", optimize);
    $("pt-sc-archive").addEventListener("click", archiveScenario);
    $("pt-sc-send").addEventListener("click", function () { openSend(S.sc.id); });
    $("pt-live-send").addEventListener("click", function () { openSend(null); });
    $("pt-plan-new").addEventListener("click", function () { openPlan(null); });
    $("pt-plan-save").addEventListener("click", savePlan);
    $("pt-plan-cancel").addEventListener("click", closePlan);
    $("pt-plan-delete").addEventListener("click", deletePlan);
    ["pt-plan-name", "pt-plan-sc", "pt-plan-group", "pt-plan-device", "pt-plan-every", "pt-plan-from", "pt-plan-until",
     "pt-plan-enabled", "pt-plan-note"].forEach(function (id) { $(id).addEventListener("change", planDirty); });
    document.querySelectorAll('input[name="pt-plan-kind"], input[name="pt-plan-rule"]').forEach(function (r) { r.addEventListener("change", planDirty); });
    $("pt-sc-loop").addEventListener("change", preview);
    $("pt-sc-color").addEventListener("change", function () { readScenarioForm(); renderSwatches(); render(); });
    // Choisir une dominante propose sa couleur si la couleur n'a pas ete
    // choisie a la main (encore une des couleurs proposees)
    $("pt-sc-dominante").addEventListener("change", function () {
      var cur = ($("pt-sc-color").value || "").toLowerCase();
      var isPreset = SWATCHES.some(function (s) { return s[0] === cur; }) || cur === "#f97316";
      var c = DOMINANTE_COLORS[$("pt-sc-dominante").value];
      if (c && isPreset) { $("pt-sc-color").value = c; renderSwatches(); }
      readScenarioForm(); render();
    });
    document.querySelectorAll('input[name="pt-sc-travel"]').forEach(function (r) { r.addEventListener("change", preview); });
    $("pt-send-close").addEventListener("click", function () { $("pt-send-modal").hidden = true; if (S.tab === "suivi") loadLive(); });
    $("pt-send-go").addEventListener("click", doSend);
    $("pt-imp-open").addEventListener("click", openImport);
    $("pt-imp-close").addEventListener("click", closeImport);
    $("pt-imp-layer").addEventListener("change", loadImportItems);
    $("pt-imp-zone").addEventListener("change", paintImport);
    $("pt-imp-search").addEventListener("input", paintImport);
    $("pt-imp-all").addEventListener("click", function () { IMP.visible.forEach(function (f) { IMP.sel[f] = true; }); paintImport(); });
    $("pt-imp-none").addEventListener("click", function () { IMP.sel = {}; paintImport(); });
    $("pt-imp-lasso").addEventListener("click", startLasso);
    $("pt-imp-lasso-done").addEventListener("click", function () { endLasso(true); });
    $("pt-imp-lasso-cancel").addEventListener("click", function () { endLasso(false); });
    $("pt-imp-go").addEventListener("click", function () { doImport(false); });
    $("pt-imp-sc").addEventListener("click", function () { doImport(true); });
    $("pt-fiche-close").addEventListener("click", function () { $("pt-fiche-modal").hidden = true; });
    $("pt-dev-search").addEventListener("input", renderDevices);
    $("pt-zone-search").addEventListener("input", paintZones);
    $("pt-zone-clear").addEventListener("click", function () { ZD.sel = []; paintZones(); });
    $("pt-zone-close").addEventListener("click", function () { $("pt-zone-modal").hidden = true; });
    $("pt-zone-save").addEventListener("click", saveZones);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      ["pt-zone-modal", "pt-send-modal", "pt-pick-modal", "pt-fiche-modal"].forEach(function (id) { $(id).hidden = true; });
    });
    $("pt-hist-days").addEventListener("change", loadHistory);
    $("pt-hist-sc").addEventListener("change", loadHistory);
    $("pt-set-save").addEventListener("click", saveSettings);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initMap();
    bind();
    var tab = "suivi";
    try { tab = localStorage.getItem("pt_tab") || "suivi"; } catch (e) { /* noop */ }
    load().then(function () { setTab(tab); loadLive(); });
    // Patrouilles relues toutes les 10 s quel que soit l'onglet : compteurs
    // et messages (acceptee, non prise, terminee) partout sur la page
    setInterval(loadLive, 10000);
  });
})();
