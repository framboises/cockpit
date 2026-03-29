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

  var data = { sous_classifications: {}, intervenants: [], services: [] };
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
})();
