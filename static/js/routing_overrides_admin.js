// routing_overrides_admin.js - Editeur de carte pour les corrections de
// routing Cockpit (portails fermes, routes barrees, forcer passable...).
//
// IIFE autonome chargee dans templates/field_dispatch.html. Backend :
// /api/admin/routing-overrides (CRUD admin).

(function () {
  "use strict";

  // -------------------------------------------------------------------------
  // Constantes
  // -------------------------------------------------------------------------

  var CIRCUIT_CENTER = [47.952, 0.225];
  var CIRCUIT_BBOX = [[47.898, 0.144], [48.006, 0.306]];

  var COLORS = {
    block_point: "#dc2626",     // rouge
    block_polygon: "#dc2626",
    force_open: "#16a34a",      // vert
    draft: "#7c3aed",           // violet : en cours de saisie
  };

  var TYPE_LABELS = {
    block_point: "Point bloque",
    block_polygon: "Zone bloquee",
    force_open: "Forcer passable",
  };

  var SCOPE_LABELS = {
    all: "Tous",
    normal_only: "Normal seul",
    god_only: "Intervention seule",
  };

  // -------------------------------------------------------------------------
  // State
  // -------------------------------------------------------------------------

  var state = {
    map: null,
    layerExisting: null,   // L.layerGroup avec les overrides existants
    layerDraft: null,      // L.layerGroup avec la saisie en cours
    items: [],             // overrides existants charges
    filter: "all",
    mode: null,            // "block_point" | "block_polygon" | "force_open" | null
    draftCoords: [],       // pour polygone en cours
    draftMarker: null,
    highlightLayer: null,
    initialised: false,
  };

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  function $(sel) { return document.querySelector(sel); }

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function toast(type, msg) {
    if (typeof showToast === "function") return showToast(msg, type);
    if (type === "error") console.error("[rov]", msg);
    else console.log("[rov]", msg);
  }

  function apiGet(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; });
    });
  }

  function apiSend(url, method, data) {
    var opts = {
      method: method,
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrfToken() },
    };
    if (data !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(data);
    }
    return fetch(url, opts).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; });
    });
  }

  function formatRelative(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      var diff = (Date.now() - d.getTime()) / 1000;
      if (diff < 60) return "il y a " + Math.round(diff) + "s";
      if (diff < 3600) return "il y a " + Math.round(diff / 60) + " min";
      if (diff < 86400) return "il y a " + Math.round(diff / 3600) + " h";
      return d.toLocaleDateString();
    } catch (e) { return ""; }
  }

  function isExpired(iso) {
    if (!iso) return false;
    try { return new Date(iso).getTime() < Date.now(); } catch (e) { return false; }
  }

  function circlePolygon(lat, lon, radiusM, n) {
    n = n || 24;
    var R = 6371000.0;
    var coords = [];
    var cosLat = Math.cos(lat * Math.PI / 180) || 1e-9;
    for (var i = 0; i < n; i++) {
      var angle = 2 * Math.PI * i / n;
      var dlat = (radiusM * Math.cos(angle)) / R * (180.0 / Math.PI);
      var dlon = (radiusM * Math.sin(angle)) / (R * cosLat) * (180.0 / Math.PI);
      coords.push([lat + dlat, lon + dlon]);
    }
    coords.push(coords[0]);
    return coords;
  }

  // -------------------------------------------------------------------------
  // Carte
  // -------------------------------------------------------------------------

  function initMap() {
    if (state.map) return;
    var mapDiv = $("#rov-map");
    if (!mapDiv) return;

    state.map = L.map(mapDiv, { zoomControl: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap",
    }).addTo(state.map);
    state.map.fitBounds(CIRCUIT_BBOX);

    state.layerExisting = L.layerGroup().addTo(state.map);
    state.layerDraft = L.layerGroup().addTo(state.map);

    state.map.on("click", onMapClick);
    state.map.on("dblclick", onMapDoubleClick);

    setTimeout(function () { if (state.map) state.map.invalidateSize(); }, 100);
  }

  function renderExisting() {
    if (!state.layerExisting) return;
    state.layerExisting.clearLayers();

    var filtered = state.items.filter(function (it) {
      if (state.filter === "all") return true;
      return it.type === state.filter;
    });

    filtered.forEach(function (it) {
      var color = COLORS[it.type] || "#666";
      var faded = !it.active || isExpired(it.expires_at);
      var opacity = faded ? 0.35 : 0.85;

      if (it.type === "block_point" || it.type === "force_open") {
        if (it.lat == null || it.lon == null) return;
        // Pin ponctuel cliquable (block_point = avoid_locations Valhalla,
        // pas de zone d'effet a representer).
        var iconName = it.type === "force_open" ? "door_open" : "block";
        var marker = L.marker([it.lat, it.lon], {
          icon: L.divIcon({
            className: "",
            html: "<div class='rov-pin' style='background:" + color + "; opacity:" + opacity + "'>"
              + "<span class='material-symbols-outlined'>" + iconName + "</span></div>",
            iconSize: [28, 28],
            iconAnchor: [14, 14],
          }),
        });
        marker.bindTooltip(it.label || "(sans libelle)");
        marker.on("click", function () { openEditModal(it); });
        marker.addTo(state.layerExisting);
      } else if (it.type === "block_polygon") {
        var ring = (it.coords || []).map(function (c) { return [c[1], c[0]]; }); // [lon,lat] -> [lat,lon]
        if (ring.length < 3) return;
        var poly = L.polygon(ring, {
          color: color,
          fillColor: color,
          fillOpacity: faded ? 0.05 : 0.2,
          weight: 2,
          opacity: opacity,
        });
        poly.bindTooltip(it.label || "(sans libelle)");
        poly.on("click", function () { openEditModal(it); });
        poly.addTo(state.layerExisting);
      }
    });

    renderList(filtered);
    var n = state.items.length;
    var nActive = state.items.filter(function (it) { return it.active && !isExpired(it.expires_at); }).length;
    var countEl = $("#rov-count");
    if (countEl) {
      countEl.textContent = nActive + " active" + (nActive > 1 ? "s" : "")
        + (n > nActive ? " (" + (n - nActive) + " inactive/expiree)" : "");
    }
  }

  function renderList(items) {
    var ul = $("#rov-items");
    if (!ul) return;
    while (ul.firstChild) ul.removeChild(ul.firstChild);

    if (!items.length) {
      var empty = document.createElement("li");
      empty.className = "rov-empty";
      empty.textContent = "Aucune correction.";
      ul.appendChild(empty);
      return;
    }

    items.forEach(function (it) {
      var li = document.createElement("li");
      li.className = "rov-item";
      if (!it.active || isExpired(it.expires_at)) li.classList.add("rov-inactive");
      li.style.borderLeftColor = COLORS[it.type] || "#666";

      var head = document.createElement("div");
      head.className = "rov-item-head";
      var lbl = document.createElement("strong");
      lbl.textContent = it.label || "(sans libelle)";
      head.appendChild(lbl);
      var badge = document.createElement("span");
      badge.className = "rov-badge";
      badge.textContent = TYPE_LABELS[it.type] || it.type;
      head.appendChild(badge);
      li.appendChild(head);

      var meta = document.createElement("div");
      meta.className = "rov-item-meta";
      var bits = [];
      bits.push(SCOPE_LABELS[it.scope] || it.scope);
      if (it.expires_at) {
        bits.push(isExpired(it.expires_at)
          ? "expire " + new Date(it.expires_at).toLocaleDateString()
          : "expire " + new Date(it.expires_at).toLocaleString());
      }
      if (!it.active) bits.push("desactive");
      if (it.created_at) bits.push(formatRelative(it.created_at));
      meta.textContent = bits.join(" · ");
      li.appendChild(meta);

      li.addEventListener("click", function () {
        focusItem(it);
      });
      li.addEventListener("dblclick", function (e) {
        e.stopPropagation();
        openEditModal(it);
      });

      ul.appendChild(li);
    });
  }

  function focusItem(it) {
    if (state.highlightLayer) {
      state.map.removeLayer(state.highlightLayer);
      state.highlightLayer = null;
    }
    if (it.type === "block_point" || it.type === "force_open") {
      state.map.setView([it.lat, it.lon], 18);
      state.highlightLayer = L.circle([it.lat, it.lon], {
        radius: 15,
        color: "#7c3aed", fill: false, weight: 3, dashArray: "6 4",
      }).addTo(state.map);
    } else if (it.type === "block_polygon") {
      var ring = (it.coords || []).map(function (c) { return [c[1], c[0]]; });
      var poly = L.polygon(ring);
      state.map.fitBounds(poly.getBounds(), { padding: [40, 40], maxZoom: 18 });
      state.highlightLayer = L.polygon(ring, {
        color: "#7c3aed", fill: false, weight: 3, dashArray: "6 4",
      }).addTo(state.map);
    }
    setTimeout(function () {
      if (state.highlightLayer) {
        state.map.removeLayer(state.highlightLayer);
        state.highlightLayer = null;
      }
    }, 3000);
  }

  // -------------------------------------------------------------------------
  // Modes de saisie
  // -------------------------------------------------------------------------

  function setMode(mode) {
    state.mode = mode;
    state.draftCoords = [];
    if (state.layerDraft) state.layerDraft.clearLayers();
    if (state.draftMarker) {
      state.map.removeLayer(state.draftMarker);
      state.draftMarker = null;
    }
    var hint = $("#rov-hint");
    var cancel = $("#rov-btn-cancel");
    document.querySelectorAll(".rov-btn").forEach(function (b) {
      b.classList.toggle("active", b.dataset.mode === mode);
    });
    if (!mode) {
      if (hint) hint.hidden = true;
      if (cancel) cancel.hidden = true;
      state.map && state.map.getContainer().classList.remove("rov-cursor-cross");
      return;
    }
    if (hint) {
      hint.hidden = false;
      if (mode === "block_polygon") {
        hint.textContent = "Clique pour ajouter un sommet, double-clic pour fermer la zone.";
      } else if (mode === "block_point") {
        hint.textContent = "Clique sur la carte pour poser un blocage ponctuel.";
      } else if (mode === "force_open") {
        hint.textContent = "Clique sur la carte pour marquer un passage a forcer ouvert.";
      }
    }
    if (cancel) cancel.hidden = false;
    state.map && state.map.getContainer().classList.add("rov-cursor-cross");
  }

  function onMapClick(e) {
    if (!state.mode) return;
    var lat = e.latlng.lat;
    var lng = e.latlng.lng;

    if (state.mode === "block_point" || state.mode === "force_open") {
      openEditModal({
        type: state.mode,
        lat: lat,
        lon: lng,
        scope: "all",
        active: true,
        label: "",
      }, /* isDraft */ true);
      setMode(null);
      return;
    }
    if (state.mode === "block_polygon") {
      state.draftCoords.push([lat, lng]);
      redrawDraftPolygon();
    }
  }

  function onMapDoubleClick(e) {
    if (state.mode !== "block_polygon") return;
    if (state.draftCoords.length < 3) {
      toast("warn", "Au moins 3 sommets requis pour fermer la zone.");
      return;
    }
    // Le double-clic ajoute un sommet juste avant (single-click qui precede),
    // on l'enleve pour ne pas dupliquer.
    if (state.draftCoords.length >= 2) {
      var last = state.draftCoords[state.draftCoords.length - 1];
      var prev = state.draftCoords[state.draftCoords.length - 2];
      if (Math.abs(last[0] - prev[0]) < 1e-6 && Math.abs(last[1] - prev[1]) < 1e-6) {
        state.draftCoords.pop();
      }
    }
    // Convertit en [lon, lat] pour le backend
    var coords = state.draftCoords.map(function (c) { return [c[1], c[0]]; });
    openEditModal({
      type: "block_polygon",
      coords: coords,
      scope: "all",
      active: true,
      label: "",
    }, /* isDraft */ true);
    setMode(null);
  }

  function redrawDraftPolygon() {
    if (!state.layerDraft) return;
    state.layerDraft.clearLayers();
    if (!state.draftCoords.length) return;
    state.draftCoords.forEach(function (c, i) {
      L.circleMarker(c, {
        radius: 5, fillColor: COLORS.draft, color: "#fff", weight: 2, fillOpacity: 1,
      }).bindTooltip("Sommet " + (i + 1))
        .addTo(state.layerDraft);
    });
    if (state.draftCoords.length >= 2) {
      L.polyline(state.draftCoords, {
        color: COLORS.draft, weight: 3, opacity: 0.8, dashArray: "4 4",
      }).addTo(state.layerDraft);
    }
    if (state.draftCoords.length >= 3) {
      L.polygon(state.draftCoords, {
        color: COLORS.draft, fillColor: COLORS.draft, fillOpacity: 0.1,
        weight: 1, opacity: 0.6,
      }).addTo(state.layerDraft);
    }
  }

  // -------------------------------------------------------------------------
  // Modale d'edition
  // -------------------------------------------------------------------------

  function openEditModal(item, isDraft) {
    var modal = $("#rov-edit-modal");
    if (!modal) return;
    var form = $("#rov-edit-form");
    if (!form) return;

    var isNew = isDraft || !item.id;
    $("#rov-edit-title").textContent = isNew
      ? "Nouvelle correction (" + (TYPE_LABELS[item.type] || item.type) + ")"
      : "Modifier : " + (item.label || "(sans libelle)");

    form.id.value = item.id || "";
    form.type.value = item.type || "";
    form.label.value = item.label || "";
    form.scope.value = item.scope || "all";
    form.notes.value = item.notes || "";
    form.osm_ref.value = item.osm_ref || "";

    var expVal = "";
    if (item.expires_at) {
      try {
        var d = new Date(item.expires_at);
        // Format datetime-local : YYYY-MM-DDTHH:MM
        var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
        expVal = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate())
          + "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
      } catch (e) { /* ignore */ }
    }
    form.expires_at.value = expVal;

    // Le draft des coords est stocke en module pour le submit (pas besoin de
    // l'embarquer dans le form puisqu'il vient de la carte)
    state.draftItem = item;

    var activeRow = $("#rov-active-row");
    if (activeRow) {
      activeRow.hidden = isNew;
      form.active.checked = item.active !== false;
    }

    var del = $("#rov-edit-delete");
    if (del) del.hidden = isNew;

    modal.hidden = false;
  }

  function closeEditModal() {
    var modal = $("#rov-edit-modal");
    if (modal) modal.hidden = true;
    state.draftItem = null;
    // Si on annule la creation, on efface le draft de la carte
    if (state.layerDraft) state.layerDraft.clearLayers();
    state.draftCoords = [];
  }

  function submitEdit() {
    var form = $("#rov-edit-form");
    if (!form) return;
    var id = form.id.value;
    var isNew = !id;

    var payload = {
      label: form.label.value.trim(),
      scope: form.scope.value,
      notes: form.notes.value.trim(),
      osm_ref: form.osm_ref.value.trim(),
    };
    if (!isNew) payload.active = !!form.active.checked;
    var expV = form.expires_at.value;
    payload.expires_at = expV ? new Date(expV).toISOString() : null;

    if (isNew) {
      payload.type = form.type.value;
      if (payload.type === "block_point" || payload.type === "force_open") {
        if (!state.draftItem || state.draftItem.lat == null) {
          toast("error", "Coordonnees manquantes");
          return;
        }
        payload.lat = state.draftItem.lat;
        payload.lon = state.draftItem.lon;
      } else if (payload.type === "block_polygon") {
        if (!state.draftItem || !state.draftItem.coords || state.draftItem.coords.length < 3) {
          toast("error", "Polygone invalide");
          return;
        }
        payload.coords = state.draftItem.coords;
      }
    }

    if (!payload.label) {
      toast("error", "Libelle requis");
      return;
    }

    var btn = $("#rov-edit-submit");
    if (btn) btn.disabled = true;

    var p = isNew
      ? apiSend("/api/admin/routing-overrides", "POST", payload)
      : apiSend("/api/admin/routing-overrides/" + encodeURIComponent(id), "PATCH", payload);

    p.then(function (resp) {
      if (btn) btn.disabled = false;
      if (!resp.ok || !resp.body || resp.body.ok === false) {
        toast("error", (resp.body && resp.body.error) || ("Erreur " + resp.status));
        return;
      }
      toast("success", isNew ? "Correction creee" : "Correction mise a jour");
      closeEditModal();
      reload();
    }).catch(function (e) {
      if (btn) btn.disabled = false;
      toast("error", (e && e.message) || "Reseau indisponible");
    });
  }

  function deleteCurrent() {
    var form = $("#rov-edit-form");
    if (!form) return;
    var id = form.id.value;
    if (!id) return;
    if (!confirm("Supprimer cette correction ?")) return;
    apiSend("/api/admin/routing-overrides/" + encodeURIComponent(id), "DELETE")
      .then(function (resp) {
        if (!resp.ok || !resp.body || resp.body.ok === false) {
          toast("error", (resp.body && resp.body.error) || ("Erreur " + resp.status));
          return;
        }
        toast("success", "Correction supprimee");
        closeEditModal();
        reload();
      }).catch(function (e) {
        toast("error", (e && e.message) || "Reseau indisponible");
      });
  }

  // -------------------------------------------------------------------------
  // Chargement
  // -------------------------------------------------------------------------

  function reload() {
    return apiGet("/api/admin/routing-overrides").then(function (resp) {
      if (!resp.ok || !resp.body || resp.body.ok === false) {
        toast("error", (resp.body && resp.body.error) || ("Erreur " + resp.status));
        return;
      }
      state.items = resp.body.items || [];
      renderExisting();
    }).catch(function (e) {
      toast("error", (e && e.message) || "Reseau indisponible");
    });
  }

  // -------------------------------------------------------------------------
  // Wiring
  // -------------------------------------------------------------------------

  function wire() {
    document.querySelectorAll(".rov-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        var m = b.dataset.mode;
        setMode(state.mode === m ? null : m);
      });
    });
    var cancel = $("#rov-btn-cancel");
    if (cancel) cancel.addEventListener("click", function () { setMode(null); });

    var filter = $("#rov-filter");
    if (filter) {
      filter.addEventListener("change", function () {
        state.filter = filter.value;
        renderExisting();
      });
    }

    var modal = $("#rov-edit-modal");
    if (modal) {
      modal.querySelectorAll("[data-close]").forEach(function (b) {
        b.addEventListener("click", closeEditModal);
      });
      modal.addEventListener("click", function (e) {
        if (e.target === modal) closeEditModal();
      });
    }
    var submitBtn = $("#rov-edit-submit");
    if (submitBtn) submitBtn.addEventListener("click", submitEdit);
    var delBtn = $("#rov-edit-delete");
    if (delBtn) delBtn.addEventListener("click", deleteCurrent);
  }

  function init() {
    if (state.initialised) return;
    state.initialised = true;
    initMap();
    wire();
    reload();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.RoutingOverridesAdmin = { reload: reload };
})();
