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
    live: [], liveTimer: null, previewTimer: null, previewSeq: 0,
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
    if (S.tab === "points") openPointEditor({ lat: e.latlng.lat, lng: e.latlng.lng });
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
    var add = $("pt-sc-add");
    clear(add);
    add.appendChild(new Option("+ Ajouter un point...", ""));
    S.data.points.filter(function (p) { return p.active; }).forEach(function (p) { add.appendChild(new Option(p.name, p.id)); });
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
  }

  // ---------- Onglets ----------
  function setTab(tab) {
    S.tab = tab;
    document.querySelectorAll("#pt-tabs button").forEach(function (b) { b.classList.toggle("active", b.dataset.tab === tab); });
    document.querySelectorAll(".pt-tab").forEach(function (t) { t.hidden = t.dataset.tab !== tab; });
    try { localStorage.setItem("pt_tab", tab); } catch (e) { /* noop */ }
    if (S.liveTimer) { clearInterval(S.liveTimer); S.liveTimer = null; }
    if (tab === "suivi") { loadPlans(); loadLive(); S.liveTimer = setInterval(loadLive, 10000); }
    if (tab === "plans") { loadPlans(); S.liveTimer = setInterval(loadPlans, 30000); }
    if (tab === "historique") loadHistory();
    render();
    setTimeout(function () { S.map.invalidateSize(); }, 50);
  }

  function render() {
    if (!S.data) return;
    S.layers.points.clearLayers();
    S.layers.scenario.clearLayers();
    S.layers.live.clearLayers();
    if (S.tab === "points") { renderPoints(); drawAllPoints(true); }
    else if (S.tab === "scenarios") { renderScenarios(); if (S.sc) drawScenarioEditor(); else drawAllPoints(false); }
    else if (S.tab === "tablettes") renderDevices();
    else if (S.tab === "suivi") renderLive();
    else if (S.tab === "plans") renderPlans();
    drawZones();
  }

  // ---------- Points ----------
  function drawAllPoints(editable) {
    S.data.points.forEach(function (p) {
      if (!p.active && !$("pt-pt-inactive").checked) return;
      var editing = S.pt && S.pt.id === p.id;
      if (editing) return;
      var m = L.marker([p.lat, p.lng], { icon: dotIcon(p.active ? "#2563eb" : "#94a3b8", p.name) })
        .bindTooltip(esc(p.name), { direction: "top", offset: [0, -6] });
      L.circle([p.lat, p.lng], { radius: p.radius_m, color: "#2563eb", weight: 1, fillOpacity: 0.04, interactive: false }).addTo(S.layers.points);
      m.on("click", function (e) {
        L.DomEvent.stopPropagation(e);
        if (editable) openPointEditor(p);
        else if (S.sc) addPointToScenario(p.id);
      });
      m.addTo(S.layers.points);
    });
    if (S.pt) {
      var em = L.marker([S.pt.lat, S.pt.lng], { draggable: true, icon: dotIcon("#dc2626", "en edition") }).addTo(S.layers.points);
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
             active: p.active !== false, zones: p.zones || [] };
    $("pt-pt-editor").hidden = false;
    $("pt-pt-name").value = S.pt.name;
    $("pt-pt-radius").value = S.pt.radius_m;
    $("pt-pt-dwell").value = S.pt.dwell_s;
    $("pt-pt-instruction").value = S.pt.instruction;
    $("pt-pt-photo").checked = S.pt.photo_required;
    $("pt-pt-pos").textContent = "Position : " + S.pt.lat.toFixed(5) + ", " + S.pt.lng.toFixed(5) + " (deplacez le repere rouge pour ajuster)";
    $("pt-pt-zones").textContent = S.pt.id ? (S.pt.zones.length ? "Zones : " + S.pt.zones.join(", ") : "Dans aucune zone de competence") : "";
    $("pt-pt-toggle").hidden = !S.pt.id;
    $("pt-pt-toggle").textContent = S.pt.active ? "Desactiver" : "Reactiver";
    render();
    $("pt-pt-name").focus();
  }

  function closePointEditor() { S.pt = null; $("pt-pt-editor").hidden = true; render(); }

  function savePoint() {
    var body = {
      name: $("pt-pt-name").value, lat: S.pt.lat, lng: S.pt.lng, radius_m: Number($("pt-pt-radius").value),
      dwell_s: Number($("pt-pt-dwell").value), instruction: $("pt-pt-instruction").value, photo_required: $("pt-pt-photo").checked,
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

  // ---------- Scenarios ----------
  function renderScenarios() {
    var list = $("pt-sc-list"), showArch = $("pt-sc-archived").checked;
    clear(list);
    list.hidden = !!S.sc;
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
    $("pt-sc-name").value = S.sc.name;
    $("pt-sc-dominante").value = S.sc.dominante;
    $("pt-sc-color").value = S.sc.color;
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

  function closeScenario() { S.sc = null; $("pt-sc-editor").hidden = true; render(); }

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

  var dragFrom = null;
  function renderScenarioPoints() {
    var ol = $("pt-sc-points");
    clear(ol);
    var legs = (S.sc.estimate && S.sc.estimate.legs) || [];
    S.sc.points.forEach(function (id, i) {
      var p = pointById(id) || { name: "(point supprime)" };
      var li = el("li", "pt-step");
      li.draggable = true;
      li.appendChild(el("span", "pt-step-num", String(i + 1)));
      var name = el("span", "pt-step-name", p.name);
      if (p.active === false) name.appendChild(el("em", "pt-muted", " (desactive)"));
      li.appendChild(name);
      if (i > 0 && legs[i - 1]) li.appendChild(el("span", "pt-step-leg", fmtKm(legs[i - 1].distance_m) + " - " + fmtMin(legs[i - 1].duration_s)));
      var rm = el("button", "pt-step-rm"); rm.type = "button"; rm.title = "Retirer"; rm.appendChild(icon("close"));
      rm.addEventListener("click", function () { S.sc.points.splice(i, 1); renderScenarioPoints(); render(); preview(); });
      li.appendChild(rm);
      li.addEventListener("dragstart", function () { dragFrom = i; li.classList.add("dragging"); });
      li.addEventListener("dragend", function () { li.classList.remove("dragging"); });
      li.addEventListener("dragover", function (e) { e.preventDefault(); });
      li.addEventListener("drop", function (e) {
        e.preventDefault();
        if (dragFrom === null || dragFrom === i) return;
        var moved = S.sc.points.splice(dragFrom, 1)[0];
        S.sc.points.splice(i, 0, moved);
        dragFrom = null;
        renderScenarioPoints(); render(); preview();
      });
      ol.appendChild(li);
    });
    if (!S.sc.points.length) ol.appendChild(el("li", "pt-empty", "Aucun point : cliquez-les sur la carte."));
    renderZoneHint();
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

  function drawScenarioEditor() {
    drawAllPoints(false);
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
      ds.appendChild(new Option(d.name + " - " + (STATUS_LABELS[d.status] || d.status) + (d.recent ? "" : ", pas vue recemment") +
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
      res.className = "pt-send-result ok";
      res.appendChild(el("div", null, "Proposee a " + (r.run.current ? r.run.current.device_name : "?") +
        (r.run.current && r.run.current.distance_m != null ? " (" + fmtKm(r.run.current.distance_m) + " du depart)" : "") +
        ", reponse attendue sous " + S.data.settings.accept_timeout_s + " s. Suivi dans l'onglet Suivi."));
      showSkipped(res, r.skipped);
      toast("success", "Patrouille proposee");
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
  function loadLive() {
    apiGet("/api/patrols/runs?scope=live").then(function (r) {
      S.live = r.runs;
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
    if (withActions && ["proposed", "running", "paused"].indexOf(r.state) >= 0) {
      var act = el("div", "pt-row pt-actions");
      act.appendChild(el("span", "pt-spacer"));
      var b = el("button", "btn pt-danger", "Annuler la patrouille"); b.type = "button";
      b.addEventListener("click", function (e) { e.stopPropagation(); cancelRun(r); });
      act.appendChild(b);
      card.appendChild(act);
    }
    card.addEventListener("click", function () { focusRun(r); });
    return card;
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
      L.marker([r.position.lat, r.position.lng], { icon: L.divIcon({ className: "pt-dev-icon", html: '<span class="material-symbols-outlined" style="color:' + safeColor(color) + '">navigation</span>', iconSize: [28, 28], iconAnchor: [14, 14] }), zIndexOffset: 2000 })
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
      if (!r.runs.length) list.appendChild(el("div", "pt-empty", "Aucune patrouille sur la periode."));
      r.runs.forEach(function (x) { list.appendChild(runCard(x, false)); });
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
  function planDirty() { $("pt-plan-dirty").textContent = "Modifications non enregistrees"; }

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
  function renderDevices() {
    var list = $("pt-dev-list");
    clear(list);
    var groups = {};
    S.data.groups.forEach(function (g) { groups[g.id] = g; });
    S.data.devices.forEach(function (d) {
      var row = el("div", "pt-item pt-dev");
      var main = el("div", "pt-item-main");
      main.appendChild(el("div", "pt-item-title", d.name));
      main.appendChild(el("div", "pt-item-sub", [d.group_label || "sans groupe", STATUS_LABELS[d.status] || d.status,
        d.recent ? "vue recemment" : "pas vue recemment", d.declarant ? "declarante (jamais de patrouille)" : ""].filter(Boolean).join(" - ")));
      var zbox = el("div", "pt-zone-chips");
      function paint() {
        clear(zbox);
        if (!d.competence_zones.length) zbox.appendChild(el("span", "pt-chip", "Partout"));
        d.competence_zones.forEach(function (z) { zbox.appendChild(el("span", "pt-chip zone", zoneLabel(z))); });
      }
      paint();
      main.appendChild(zbox);
      var det = el("details", "pt-zone-edit");
      det.appendChild(el("summary", null, "Modifier les zones"));
      var boxes = el("div", "pt-zone-grid");
      S.data.zones.forEach(function (z) {
        var lab = el("label", "pt-check");
        var cb = document.createElement("input"); cb.type = "checkbox"; cb.value = z.id; cb.checked = d.competence_zones.indexOf(z.id) >= 0;
        lab.appendChild(cb); lab.appendChild(document.createTextNode(" " + z.label));
        boxes.appendChild(lab);
      });
      det.appendChild(boxes);
      var save = el("button", "btn btn-primary", "Enregistrer les zones"); save.type = "button";
      save.addEventListener("click", function () {
        var zones = Array.prototype.slice.call(boxes.querySelectorAll("input:checked")).map(function (c) { return c.value; });
        apiPost("/api/patrols/devices/" + d.id + "/zones", { zones: zones }).then(function (r) {
          d.competence_zones = r.zones; paint(); det.open = false;
          toast("success", "Zones de " + d.name + " enregistrees");
        }).catch(function (e) { toast("error", errLabel(e)); });
      });
      det.appendChild(save);
      main.appendChild(det);
      row.appendChild(main);
      list.appendChild(row);
    });
    if (!S.data.devices.length) list.appendChild(el("div", "pt-empty", "Aucune tablette appairee."));
  }

  // ---------- Reglages ----------
  function saveSettings() {
    apiPut("/api/patrols/settings", {
      radius_m: Number($("pt-set-radius").value), accept_timeout_s: Number($("pt-set-timeout").value),
      seen_window_min: Number($("pt-set-seen").value),
    }).then(function (r) { S.data.settings = r.settings; fillSelects(); toast("success", "Reglages enregistres"); })
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
    $("pt-sc-add").addEventListener("change", function (e) { if (e.target.value) addPointToScenario(e.target.value); e.target.value = ""; });
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
    $("pt-sc-color").addEventListener("change", function () { readScenarioForm(); render(); });
    document.querySelectorAll('input[name="pt-sc-travel"]').forEach(function (r) { r.addEventListener("change", preview); });
    $("pt-send-close").addEventListener("click", function () { $("pt-send-modal").hidden = true; if (S.tab === "suivi") loadLive(); });
    $("pt-send-go").addEventListener("click", doSend);
    $("pt-hist-days").addEventListener("change", loadHistory);
    $("pt-hist-sc").addEventListener("change", loadHistory);
    $("pt-set-save").addEventListener("click", saveSettings);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initMap();
    bind();
    var tab = "suivi";
    try { tab = localStorage.getItem("pt_tab") || "suivi"; } catch (e) { /* noop */ }
    load().then(function () { setTab(tab); });
  });
})();
