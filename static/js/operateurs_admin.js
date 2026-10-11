// ==========================================================================
// ZONES ET METIERS - Admin (Configuration > Zones et metiers (operateurs))
// Choix de la couche Looker des zones de prise de poste et de la propriete
// servant de nom ; apercu carte + liste ; metiers Bibler en lecture.
// Plan : docs/claude/operateurs.md. Texte injecte via textContent.
// ==========================================================================
(function () {
  "use strict";

  var layerSel = document.getElementById("op-admin-layer");
  if (!layerSel) return;
  var labelSel = document.getElementById("op-admin-label");
  var infoEl = document.getElementById("op-admin-layer-info");
  var statusEl = document.getElementById("op-admin-status");
  var zonesEl = document.getElementById("op-admin-zones");
  var metiersEl = document.getElementById("op-admin-metiers");
  var mapEl = document.getElementById("op-admin-map");

  var state = { layers: [], settings: {}, dirty: false, map: null, group: null, polys: {} };

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
  function option(value, text, selected) {
    var o = document.createElement("option");
    o.value = value;
    o.textContent = text;
    if (selected) o.selected = true;
    return o;
  }
  function getJSON(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function setDirty(v) {
    state.dirty = v;
    statusEl.textContent = v ? "Modifications non enregistrees" : "";
    statusEl.style.color = v ? "var(--warning)" : "var(--muted)";
  }
  function currentLayer() {
    var c = layerSel.value;
    return state.layers.filter(function (l) { return l.collection === c; })[0] || null;
  }

  // --- Carte -------------------------------------------------------------
  function ensureMap() {
    if (state.map || typeof L === "undefined") return;
    state.map = L.map(mapEl, { zoomControl: true }).setView([47.95, 0.21], 13);
    var ign = L.tileLayer("https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}", { attribution: "IGN-F/Geoplateforme", maxNativeZoom: 19, maxZoom: 22 });
    var osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "OSM", maxNativeZoom: 19, maxZoom: 22 });
    osm.addTo(state.map);
    L.control.layers({ "Plan": osm, "Vue aerienne": ign }, {}, { position: "topright" }).addTo(state.map);
    state.group = L.featureGroup().addTo(state.map);
  }
  function fit() {
    if (!state.map) return;
    state.map.invalidateSize();
    if (state.group && state.group.getLayers().length) state.map.fitBounds(state.group.getBounds(), { padding: [12, 12] });
  }

  // --- Rendu -------------------------------------------------------------
  function renderLayers() {
    layerSel.textContent = "";
    layerSel.appendChild(option("", "(aucune : pas de choix de zone)", !state.settings.zones_collection));
    state.layers.forEach(function (l) {
      layerSel.appendChild(option(l.collection, l.label + " (" + l.n_zones + " zones)",
        l.collection === state.settings.zones_collection));
    });
  }
  function renderLabelField(keep) {
    var l = currentLayer();
    var want = keep || (l && l.search_field) || "";
    labelSel.textContent = "";
    labelSel.disabled = !l;
    if (!l) { infoEl.textContent = ""; return; }
    l.properties.forEach(function (p) { labelSel.appendChild(option(p, p, p === want)); });
    if (l.n_sans_id) {
      infoEl.className = "op-admin-info is-warn";
      infoEl.textContent = l.n_sans_id + " zone(s) sans identifiant Looker (_id_feature) : ignorees tant qu'elles n'en ont pas.";
    } else {
      infoEl.className = "op-admin-info";
      infoEl.textContent = "";
    }
  }
  function renderZones(zones) {
    zonesEl.textContent = "";
    ensureMap();
    if (state.group) state.group.clearLayers();
    state.polys = {};
    if (!zones.length) {
      zonesEl.appendChild(el("span", "si-admin-empty",
        layerSel.value ? "Aucune zone exploitable dans cette couche." : "Aucune couche choisie."));
      return;
    }
    zones.forEach(function (z) {
      var chip = el("span", "op-admin-chip", z.label);
      zonesEl.appendChild(chip);
      if (!state.group) return;
      var poly = L.geoJSON(z.geometry, { style: { color: "#2563eb", weight: 2, fillOpacity: 0.15 } });
      poly.bindTooltip(z.label, { sticky: true, className: "op-admin-tip" });
      poly.addTo(state.group);
      state.polys[z.id] = poly;
      function on() { chip.classList.add("is-hover"); poly.setStyle({ fillOpacity: 0.45 }); }
      function off() { chip.classList.remove("is-hover"); poly.setStyle({ fillOpacity: 0.15 }); }
      chip.addEventListener("mouseenter", on);
      chip.addEventListener("mouseleave", off);
      poly.on("mouseover", on);
      poly.on("mouseout", off);
    });
    fit();
  }
  function renderMetiers(list) {
    metiersEl.textContent = "";
    if (!list.length) { metiersEl.appendChild(el("span", "si-admin-empty", "Aucun metier dans Bibler.")); return; }
    list.forEach(function (m) {
      var c = el("span", "op-admin-chip", m.label);
      c.title = m.id;
      metiersEl.appendChild(c);
    });
  }

  // Zones de la couche selectionnee (apercu admin, enregistree ou non)
  function loadZones() {
    if (!layerSel.value) { renderZones([]); return Promise.resolve(); }
    var url = "/api/operator/zones?fresh=1&collection=" + encodeURIComponent(layerSel.value)
      + "&label=" + encodeURIComponent(labelSel.value || "");
    return getJSON(url)
      .then(function (d) { renderZones(d.zones || []); })
      .catch(function (e) { toast("error", "Zones : chargement impossible (" + e.message + ")"); });
  }

  // --- Chargement --------------------------------------------------------
  function load() {
    getJSON("/api/operator/settings")
      .then(function (d) {
        state.layers = d.layers || [];
        state.settings = d.settings || {};
        renderLayers();
        renderLabelField(state.settings.zones_label_field);
        setDirty(false);
        return loadZones();
      })
      .catch(function (e) { toast("error", "Zones et metiers : chargement impossible (" + e.message + ")"); });
    getJSON("/api/operator/metiers")
      .then(function (d) { renderMetiers(d.metiers || []); })
      .catch(function (e) { toast("warning", "Metiers Bibler indisponibles (" + e.message + ")"); });
  }

  layerSel.addEventListener("change", function () {
    renderLabelField(null);
    setDirty(true);
    loadZones();
  });
  labelSel.addEventListener("change", function () { setDirty(true); loadZones(); });

  document.getElementById("op-admin-save").addEventListener("click", function () {
    statusEl.textContent = "Enregistrement...";
    var payload = { zones_collection: layerSel.value || null,
                    zones_label_field: layerSel.value ? (labelSel.value || null) : null };
    fetch("/api/operator/settings", {
      method: "PUT", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d, status: r.status }; });
    }).then(function (res) {
      if (!res.ok || !res.d.ok) {
        setDirty(true);
        toast("error", "Enregistrement refuse (" + ((res.d && res.d.error) || res.status) + ")");
        return;
      }
      state.settings = res.d.settings;
      setDirty(false);
      toast("success", "Couche de zones enregistree");
      loadZones();
    }).catch(function (e) {
      setDirty(true);
      toast("error", "Erreur : " + e.message);
    });
  });

  // Section repliee au chargement : la carte n'a pas de taille tant qu'elle
  // n'est pas ouverte.
  var header = document.querySelector('[data-section="operateurs"]');
  if (header) header.addEventListener("click", function () { setTimeout(fit, 250); });

  window.addEventListener("beforeunload", function (e) {
    if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
  else load();
})();
