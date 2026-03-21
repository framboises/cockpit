(function () {
  "use strict";

  const csrfToken = () =>
    document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";

  const tbody = document.querySelector("#merge-config-table tbody");
  const modal = document.getElementById("merge-modal");
  const form = document.getElementById("merge-config-form");
  const saveBtn = document.getElementById("merge-modal-save");
  const runBtn = document.getElementById("btn-run-merge");
  const statusEl = document.getElementById("merge-status");
  const unconfiguredSection = document.getElementById("unconfigured-section");
  const unconfiguredList = document.getElementById("unconfigured-list");

  if (!tbody) return;

  // ----- helpers -----
  function openModal() { modal.hidden = false; }
  function closeModal() { modal.hidden = true; form.reset(); }

  modal?.querySelectorAll("[data-close]").forEach(b =>
    b.addEventListener("click", closeModal)
  );

  function toast(msg, type) {
    if (typeof window.showToast === "function") {
      window.showToast(msg, type);
    }
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) Object.entries(attrs).forEach(([k, v]) => {
      if (k === "style" && typeof v === "object") {
        Object.assign(node.style, v);
      } else if (k.startsWith("on")) {
        node.addEventListener(k.slice(2), v);
      } else {
        node.setAttribute(k, v);
      }
    });
    if (children != null) {
      if (Array.isArray(children)) children.forEach(c => {
        if (typeof c === "string") node.appendChild(document.createTextNode(c));
        else if (c) node.appendChild(c);
      });
      else if (typeof children === "string") node.textContent = children;
      else node.appendChild(children);
    }
    return node;
  }

  function badge(text, variant) {
    const span = el("span", {"class": "badge badge-" + variant}, text);
    return span;
  }

  // ----- load -----
  async function load() {
    try {
      const res = await fetch("/api/merge-config");
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      renderTable(data.configs || []);
      renderUnconfigured(data.unconfigured || []);
    } catch (e) {
      console.error("merge-config load:", e);
    }
  }

  function renderTable(configs) {
    tbody.textContent = "";
    configs.forEach(cfg => {
      const tr = el("tr", null, [
        el("td", null, [
          el("strong", null, cfg.label || cfg.data_key),
          document.createElement("br"),
          el("span", {style: {fontSize: "0.78rem", color: "var(--muted)"}}, cfg.data_key),
        ]),
        el("td", null, badge(cfg.mode || "-", modeBadge(cfg.mode))),
        el("td", null, cfg.activity_label || ""),
        el("td", null, cfg.timeline_category || ""),
        el("td", null, (cfg.access_types || []).join(", ") || "-"),
        el("td", null, cfg.todos_type || "-"),
        el("td", null, (cfg.vignette_fields || []).join(", ") || "-"),
        el("td", null,
          cfg.enabled
            ? el("span", {style: {color: "var(--success)"}}, "Oui")
            : el("span", {style: {color: "var(--muted)"}}, "Non")
        ),
        el("td", null, [
          el("button", {"class": "btn btn-sm btn-secondary", onclick: () => editConfig(cfg)}, "Editer"),
          document.createTextNode(" "),
          el("button", {"class": "btn btn-sm btn-danger", onclick: () => deleteConfig(cfg.data_key)}, "X"),
        ]),
      ]);
      tbody.appendChild(tr);
    });
  }

  function renderUnconfigured(list) {
    if (!list.length) {
      unconfiguredSection.hidden = true;
      return;
    }
    unconfiguredSection.hidden = false;
    unconfiguredList.textContent = "";
    list.forEach(u => {
      const btn = el("button", {
        "class": "btn btn-sm btn-warning",
        onclick: () => {
          form.reset();
          form.data_key.value = u.data_key;
          form.label.value = u.label;
          form.mode.value = u.mode || "schedule";
          form.activity_label.value = u.label + " {name}";
          form.timeline_category.value = "Controle";
          form.timeline_type.value = "Organization";
          form.department.value = "SAFE";
          form.enabled.checked = true;
          document.getElementById("merge-modal-title").textContent = "Configurer: " + u.label;
          openModal();
        },
      }, "+ " + u.label + " (" + u.data_key + ")");
      unconfiguredList.appendChild(btn);
    });
  }

  function modeBadge(mode) {
    if (mode === "schedule") return "info";
    if (mode === "addable_schedule") return "success";
    if (mode === "activation") return "muted";
    return "secondary";
  }

  // ----- edit -----
  function editConfig(cfg) {
    form.data_key.value = cfg.data_key;
    form.label.value = cfg.label || "";
    form.mode.value = cfg.mode || "schedule";
    form.activity_label.value = cfg.activity_label || "";
    form.timeline_category.value = cfg.timeline_category || "";
    form.timeline_type.value = cfg.timeline_type || "";
    form.department.value = cfg.department || "SAFE";
    form.todos_type.value = cfg.todos_type || "";
    form.access_types.value = (cfg.access_types || []).join(", ");
    form.vignette_fields.value = (cfg.vignette_fields || []).join(", ");
    form.enabled.checked = !!cfg.enabled;
    document.getElementById("merge-modal-title").textContent = "Editer: " + (cfg.label || cfg.data_key);
    openModal();
  }

  // ----- save -----
  saveBtn?.addEventListener("click", async () => {
    const dataKey = form.data_key.value;
    if (!dataKey) return;

    const payload = {
      label: form.label.value.trim(),
      mode: form.mode.value,
      activity_label: form.activity_label.value.trim(),
      timeline_category: form.timeline_category.value.trim(),
      timeline_type: form.timeline_type.value.trim(),
      department: form.department.value.trim() || "SAFE",
      todos_type: form.todos_type.value.trim() || null,
      access_types: form.access_types.value.split(",").map(s => s.trim()).filter(Boolean),
      vignette_fields: form.vignette_fields.value.split(",").map(s => s.trim()).filter(Boolean),
      enabled: form.enabled.checked,
    };

    try {
      const res = await fetch("/api/merge-config/" + encodeURIComponent(dataKey), {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await res.text());
      toast("Configuration sauvegardee", "success");
      closeModal();
      load();
    } catch (e) {
      toast("Erreur: " + e.message, "error");
    }
  });

  // ----- delete -----
  async function deleteConfig(dataKey) {
    if (!confirm("Supprimer la config pour \"" + dataKey + "\" ?")) return;
    try {
      const res = await fetch("/api/merge-config/" + encodeURIComponent(dataKey), {
        method: "DELETE",
        headers: { "X-CSRFToken": csrfToken() },
      });
      if (!res.ok) throw new Error(await res.text());
      toast("Configuration supprimee", "success");
      load();
    } catch (e) {
      toast("Erreur: " + e.message, "error");
    }
  }

  // ----- run merge -----
  runBtn?.addEventListener("click", async () => {
    const event = window.selectedEvent;
    const year = window.selectedYear;
    if (!event || !year) {
      toast("Selectionner un evenement et une annee d'abord", "warning");
      return;
    }
    statusEl.textContent = "Merge en cours...";
    runBtn.disabled = true;

    try {
      const res = await fetch("/api/run-merge", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify({ event: event, year: year }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      statusEl.textContent = "Termine: " + data.vignettes_count + " vignettes generees";
      toast("Merge termine: " + data.vignettes_count + " vignettes", "success");
    } catch (e) {
      statusEl.textContent = "Erreur: " + e.message;
      toast("Erreur merge: " + e.message, "error");
    } finally {
      runBtn.disabled = false;
    }
  });

  // ----- init -----
  load();
})();
