(function () {
  "use strict";

  var container = document.getElementById("pcorg-config-container");
  if (!container) return;

  var csrfToken = function () {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  };

  var CATEGORIES = [
    "PCO.Secours", "PCO.Securite", "PCO.Technique",
    "PCO.Flux", "PCO.Fourriere", "PCO.Information", "PCO.MainCourante"
  ];

  var data = { sous_classifications: {}, intervenants: [], services: [], fiche_simplifiee: {} };
  var dirty = false;

  function load() {
    fetch("/api/pcorg-config")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        data = d || { sous_classifications: {}, intervenants: [], services: [] };
        render();
      });
  }

  function save() {
    fetch("/api/pcorg-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(data)
    }).then(function (r) { return r.json(); })
      .then(function (r) {
        if (r.ok) {
          dirty = false;
          if (typeof showToast === "function") showToast("success", "Configuration sauvegardee");
          updateSaveBtn();
        } else {
          if (typeof showToast === "function") showToast("error", r.error || "Erreur");
        }
      });
  }

  function genId() {
    return Math.random().toString(36).substr(2, 8);
  }

  function render() {
    container.textContent = "";

    // Save button
    var saveBar = el("div", "pcorg-cfg-save-bar");
    var saveBtn = el("button", "btn btn-primary pcorg-cfg-save-btn");
    saveBtn.id = "pcorg-cfg-save";
    saveBtn.textContent = "Sauvegarder";
    saveBtn.disabled = true;
    saveBtn.addEventListener("click", save);
    saveBar.appendChild(saveBtn);
    container.appendChild(saveBar);

    // Section 1: Sous-classifications par categorie
    var h1 = el("div", "pcorg-cfg-title");
    h1.textContent = "Sous-classifications par categorie";
    container.appendChild(h1);

    CATEGORIES.forEach(function (cat) {
      var items = (data.sous_classifications || {})[cat] || [];
      var section = buildTagSection(cat.replace("PCO.", ""), items, function (newItems) {
        if (!data.sous_classifications) data.sous_classifications = {};
        data.sous_classifications[cat] = newItems;
        markDirty();
      });
      container.appendChild(section);
    });

    // Section: Fiche simplifiee (clic droit rapide)
    var hFS = el("div", "pcorg-cfg-title");
    hFS.textContent = "Fiche simplifiee (creation rapide par clic droit)";
    container.appendChild(hFS);

    var fsDesc = el("div", "");
    fsDesc.style.cssText = "font-size:0.78rem; color:var(--muted); margin:-4px 0 4px;";
    fsDesc.textContent = "Les categories cochees proposent un sous-menu avec les niveaux d'urgence au clic droit sur la carte.";
    container.appendChild(fsDesc);

    var fsSection = el("div", "pcorg-cfg-fs-section");
    CATEGORIES.forEach(function (cat) {
      var enabled = (data.fiche_simplifiee || {})[cat] || false;
      var lbl = el("label", "pcorg-cfg-fs-label");
      var cb = el("input", "");
      cb.type = "checkbox";
      cb.checked = enabled;
      cb.addEventListener("change", function () {
        if (!data.fiche_simplifiee) data.fiche_simplifiee = {};
        data.fiche_simplifiee[cat] = cb.checked;
        markDirty();
      });
      lbl.appendChild(cb);
      lbl.appendChild(document.createTextNode(" " + cat.replace("PCO.", "")));
      fsSection.appendChild(lbl);
    });
    container.appendChild(fsSection);

    // Section 2: Intervenants / Moyens engages
    var h2 = el("div", "pcorg-cfg-title");
    h2.textContent = "Intervenants / Moyens engages";
    container.appendChild(h2);
    container.appendChild(buildTagSection("Liste commune", data.intervenants || [], function (newItems) {
      data.intervenants = newItems;
      markDirty();
    }));

    // Section 3: Services
    var h3 = el("div", "pcorg-cfg-title");
    h3.textContent = "Services contactes";
    container.appendChild(h3);
    container.appendChild(buildTagSection("Liste commune", data.services || [], function (newItems) {
      data.services = newItems;
      markDirty();
    }));
  }

  function buildTagSection(label, items, onChange) {
    var section = el("div", "pcorg-cfg-section");

    var header = el("div", "pcorg-cfg-section-header");
    var lbl = el("span", "pcorg-cfg-section-label");
    lbl.textContent = label;
    header.appendChild(lbl);
    var count = el("span", "pcorg-cfg-count");
    count.textContent = items.length;
    header.appendChild(count);
    section.appendChild(header);

    var tagsWrap = el("div", "pcorg-cfg-tags");

    function renderTags() {
      tagsWrap.textContent = "";
      items.forEach(function (item, idx) {
        var itemLabel = (typeof item === "object") ? item.label : item;
        var tag = el("span", "pcorg-cfg-tag");

        // Editable text
        var text = el("span", "pcorg-cfg-tag-text");
        text.textContent = itemLabel;
        text.title = "Double-clic pour modifier";
        text.addEventListener("dblclick", function () {
          startEdit(tag, text, item, idx, items, onChange, count);
        });
        tag.appendChild(text);

        // Edit button
        var editBtn = el("button", "pcorg-cfg-tag-edit");
        editBtn.textContent = "\u270e";
        editBtn.title = "Modifier";
        editBtn.addEventListener("click", function () {
          startEdit(tag, text, item, idx, items, onChange, count);
        });
        tag.appendChild(editBtn);

        // Delete button
        var del = el("button", "pcorg-cfg-tag-del");
        del.textContent = "\u00d7";
        del.title = "Supprimer";
        del.addEventListener("click", function () {
          items.splice(idx, 1);
          count.textContent = items.length;
          onChange(items);
          renderTags();
        });
        tag.appendChild(del);

        tagsWrap.appendChild(tag);
      });

      // Input pour ajouter
      var addWrap = el("span", "pcorg-cfg-tag-add");
      var inp = el("input", "pcorg-cfg-tag-input");
      inp.placeholder = "+ Ajouter";
      function addItem() {
        var val = inp.value.trim();
        if (!val) return;
        // Check doublon par label
        var exists = items.some(function (it) {
          return ((typeof it === "object") ? it.label : it) === val;
        });
        if (exists) {
          if (typeof showToast === "function") showToast("warning", "Element deja present");
          return;
        }
        items.push({ id: genId(), label: val });
        items.sort(function (a, b) {
          var la = (typeof a === "object") ? a.label : a;
          var lb = (typeof b === "object") ? b.label : b;
          return la.localeCompare(lb);
        });
        count.textContent = items.length;
        onChange(items);
        inp.value = "";
        renderTags();
      }
      inp.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); addItem(); }
      });
      inp.addEventListener("blur", addItem);
      addWrap.appendChild(inp);
      tagsWrap.appendChild(addWrap);
    }
    renderTags();
    section.appendChild(tagsWrap);
    return section;
  }

  function startEdit(tag, textEl, item, idx, items, onChange, countEl) {
    var currentLabel = (typeof item === "object") ? item.label : item;
    tag.classList.add("editing");
    var inp = el("input", "pcorg-cfg-tag-input-edit");
    inp.value = currentLabel;
    textEl.style.display = "none";
    tag.insertBefore(inp, textEl.nextSibling);
    inp.focus();
    inp.select();

    function commit() {
      var newVal = inp.value.trim();
      if (newVal && newVal !== currentLabel) {
        if (typeof item === "object") {
          item.label = newVal;
        } else {
          items[idx] = { id: genId(), label: newVal };
        }
        onChange(items);
      }
      textEl.textContent = (typeof items[idx] === "object") ? items[idx].label : items[idx];
      textEl.style.display = "";
      if (inp.parentNode) inp.parentNode.removeChild(inp);
      tag.classList.remove("editing");
    }

    inp.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); commit(); }
      if (e.key === "Escape") {
        textEl.style.display = "";
        if (inp.parentNode) inp.parentNode.removeChild(inp);
        tag.classList.remove("editing");
      }
    });
    inp.addEventListener("blur", commit);
  }

  function markDirty() {
    dirty = true;
    updateSaveBtn();
  }

  function updateSaveBtn() {
    var btn = document.getElementById("pcorg-cfg-save");
    if (btn) btn.disabled = !dirty;
  }

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  load();

  // ---------------------------------------------------------------------------
  // Sync SQL control (toggle + force sync)
  // ---------------------------------------------------------------------------

  var syncToggle = document.getElementById("pcorg-sync-toggle");
  var syncStatus = document.getElementById("pcorg-sync-status");
  var syncIcon = document.getElementById("pcorg-sync-icon");
  var syncDot = document.getElementById("pcorg-sync-dot");
  var syncHeaderText = document.getElementById("pcorg-sync-header-text");
  var forceSyncBtn = document.getElementById("pcorg-force-sync-btn");
  var forceFullBtn = document.getElementById("pcorg-force-full-btn");

  function formatAge(isoStr) {
    if (!isoStr) return "";
    var d = new Date(isoStr);
    if (isNaN(d.getTime())) return "";
    var sec = Math.floor((Date.now() - d.getTime()) / 1000);
    if (sec < 60) return "il y a " + sec + "s";
    var min = Math.floor(sec / 60);
    if (min < 60) return "il y a " + min + " min";
    var h = Math.floor(min / 60);
    if (h < 24) return "il y a " + h + "h" + (min % 60 ? String(min % 60).padStart(2, "0") : "");
    return d.toLocaleDateString("fr-FR") + " " + d.toLocaleTimeString("fr-FR", {hour: "2-digit", minute: "2-digit"});
  }

  function loadSyncControl() {
    fetch("/api/pcorg/sync-control")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (syncToggle) syncToggle.checked = !!d.actif;
        updateSyncDisplay(d);
      })
      .catch(function () {
        if (syncStatus) syncStatus.textContent = "Erreur de chargement";
      });
  }

  function updateSyncDisplay(d) {
    var dotColor = "var(--muted)";
    var dotTitle = "";
    var headerText = "";

    if (d.running) {
      if (syncStatus) syncStatus.textContent = "Sync en cours...";
      if (syncIcon) syncIcon.style.color = "var(--accent, #6366f1)";
      dotColor = "var(--accent, #6366f1)";
      dotTitle = "Sync en cours";
      headerText = "sync en cours...";
    } else if (d.last_error) {
      if (syncStatus) syncStatus.textContent = "Erreur : " + d.last_error;
      if (syncIcon) syncIcon.style.color = "var(--danger, #ef4444)";
      dotColor = "var(--danger, #ef4444)";
      dotTitle = "Erreur : " + d.last_error;
      headerText = "erreur" + (d.last_run ? " - " + formatAge(d.last_run) : "");
    } else if (d.last_success) {
      if (syncStatus) syncStatus.textContent = "OK" + (d.last_summary ? " - " + d.last_summary : "");
      if (syncIcon) syncIcon.style.color = "var(--success, #22c55e)";
      dotColor = "var(--success, #22c55e)";
      dotTitle = "Derniere sync reussie";
      headerText = "ok - " + formatAge(d.last_success);
    } else if (d.actif) {
      if (syncStatus) syncStatus.textContent = "Active, en attente du prochain cycle";
      if (syncIcon) syncIcon.style.color = "var(--muted)";
      dotTitle = "En attente";
      headerText = "en attente...";
    } else {
      if (syncStatus) syncStatus.textContent = "Desactivee";
      if (syncIcon) syncIcon.style.color = "var(--muted)";
      headerText = "";
    }

    if (syncDot) {
      syncDot.style.background = dotColor;
      syncDot.title = dotTitle;
    }
    if (syncHeaderText) syncHeaderText.textContent = headerText;
  }

  if (syncToggle) {
    syncToggle.addEventListener("change", function () {
      fetch("/api/pcorg/sync-control", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify({ actif: syncToggle.checked })
      })
        .then(function (r) { return r.json(); })
        .then(function (r) {
          if (r.ok) {
            var msg = syncToggle.checked ? "Sync SQL activee" : "Sync SQL desactivee";
            if (typeof showToast === "function") showToast("success", msg);
            loadSyncControl();
          }
        })
        .catch(function () {
          syncToggle.checked = !syncToggle.checked;
          if (typeof showToast === "function") showToast("error", "Erreur reseau");
        });
    });
  }

  function triggerSync(full) {
    var btn = full ? forceFullBtn : forceSyncBtn;
    if (btn) btn.disabled = true;
    fetch("/api/pcorg/force-sync", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify({ full: !!full })
    })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        if (r.ok) {
          if (typeof showToast === "function") showToast("success", r.message || "Sync lancee");
          if (syncStatus) syncStatus.textContent = "Sync en cours...";
          if (syncIcon) syncIcon.style.color = "var(--accent, #6366f1)";
          // Rafraichir le statut apres quelques secondes
          setTimeout(loadSyncControl, 5000);
          setTimeout(loadSyncControl, 15000);
          setTimeout(loadSyncControl, 30000);
        } else {
          if (typeof showToast === "function") showToast("error", r.error || "Erreur");
        }
      })
      .catch(function () {
        if (typeof showToast === "function") showToast("error", "Erreur reseau");
      })
      .finally(function () {
        if (btn) btn.disabled = false;
      });
  }

  if (forceSyncBtn) {
    forceSyncBtn.addEventListener("click", function () { triggerSync(false); });
  }
  if (forceFullBtn) {
    forceFullBtn.addEventListener("click", function () {
      if (confirm("Resynchronisation complete depuis SQL Server ?\nCela peut prendre plusieurs minutes.")) {
        triggerSync(true);
      }
    });
  }

  loadSyncControl();
})();
