// ==========================================================================
// MAP DEFAULTS — Admin: gestion des defauts carte globaux
// ==========================================================================
(function () {
  "use strict";

  var csrfToken = function () {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  };

  function toast(msg, type) {
    if (typeof window.showToast === "function") {
      window.showToast(msg, type);
    }
  }

  var container = document.getElementById("map-defaults-cats");
  var tileSelect = document.getElementById("map-default-tile");
  var btnSave = document.getElementById("btn-save-map-defaults");
  var btnAll = document.getElementById("map-defaults-all");
  var btnNone = document.getElementById("map-defaults-none");
  var statusEl = document.getElementById("map-defaults-status");

  if (!container) return;

  var categories = [];
  var hiddenSet = {};

  // --- Load categories + current defaults ---
  function load() {
    Promise.all([
      fetch("/get_gm_categories").then(function (r) { return r.json(); }),
      fetch("/api/map-defaults").then(function (r) { return r.json(); })
    ]).then(function (results) {
      categories = results[0] || [];
      var defaults = results[1] || {};
      var hidden = defaults.hidden_categories || [];
      hiddenSet = {};
      hidden.forEach(function (id) { hiddenSet[id] = true; });

      if (tileSelect && defaults.default_tile) {
        tileSelect.value = defaults.default_tile;
      }

      render();
    }).catch(function (err) {
      console.error("[MapDefaults] Erreur chargement:", err);
    });
  }

  // --- Render category checkboxes ---
  function render() {
    container.textContent = "";

    if (!categories.length) {
      var empty = document.createElement("span");
      empty.style.cssText = "color:var(--muted); font-size:0.84rem;";
      empty.textContent = "Aucune categorie disponible";
      container.appendChild(empty);
      return;
    }

    categories.forEach(function (cat) {
      var id = cat._id;
      var label = cat.label || cat.collection || id;
      var icon = cat.icon || "place";

      var chip = document.createElement("label");
      chip.className = "map-default-chip";
      chip.title = label;

      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !hiddenSet[id];
      cb.dataset.catId = id;
      cb.addEventListener("change", function () {
        if (cb.checked) {
          delete hiddenSet[id];
        } else {
          hiddenSet[id] = true;
        }
      });

      var iconSpan = document.createElement("span");
      iconSpan.className = "material-symbols-outlined";
      iconSpan.style.fontSize = "16px";
      iconSpan.textContent = icon;

      var labelSpan = document.createElement("span");
      labelSpan.textContent = label;

      chip.appendChild(cb);
      chip.appendChild(iconSpan);
      chip.appendChild(labelSpan);
      container.appendChild(chip);
    });
  }

  // --- Save ---
  if (btnSave) {
    btnSave.addEventListener("click", function () {
      var hidden = Object.keys(hiddenSet);
      var tile = tileSelect ? tileSelect.value : "osm";

      statusEl.textContent = "Sauvegarde...";

      fetch("/api/map-defaults", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify({ hidden_categories: hidden, default_tile: tile })
      }).then(function (r) {
        if (!r.ok) throw new Error("Erreur " + r.status);
        return r.json();
      }).then(function () {
        statusEl.textContent = "";
        toast("Defauts carte sauvegardes", "success");
      }).catch(function (err) {
        statusEl.textContent = "";
        toast("Erreur: " + err.message, "error");
      });
    });
  }

  // --- Select all / none ---
  if (btnAll) {
    btnAll.addEventListener("click", function () {
      hiddenSet = {};
      render();
    });
  }
  if (btnNone) {
    btnNone.addEventListener("click", function () {
      categories.forEach(function (cat) { hiddenSet[cat._id] = true; });
      render();
    });
  }

  // --- Init on DOM ready ---
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
