/* Couleurs et icones des categories de la timeline (page Configuration).
 *
 * Reglage global (cockpit_settings "timeline_categories") lu par timeline.js
 * via /api/timeline-category-styles. Une ligne par slug de categorie (meme
 * normalisation que _categorySlug de timeline.js). Sans reglage : couleur
 * integree (timeline_v2.css) sinon couleur automatique tiree du nom.
 */
(function () {
  "use strict";

  const table = document.getElementById("tlcat-table");
  if (!table) return;
  const tbody = table.querySelector("tbody");
  const saveBtn = document.getElementById("tlcat-save");
  const status = document.getElementById("tlcat-status");
  const addForm = document.getElementById("tlcat-add");

  // Palette de repli de timeline_v2.css (.tlc-fb-1 .. .tlc-fb-10)
  const FALLBACK = ["#db2777", "#d97706", "#059669", "#0284c7", "#7c3aed",
                    "#e11d48", "#65a30d", "#9333ea", "#ca8a04", "#0891b2"];
  const ICON_SUGGESTIONS = ["local_parking", "shield_lock", "badge", "event", "event_note",
    "sports_motorsports", "two_wheeler", "flag", "local_hospital", "security", "campaign",
    "storefront", "stadium", "door_front", "camping", "groups", "mic", "restaurant",
    "directions_car", "local_police", "medical_services", "info"];

  let rows = [];      // [{slug, labels, label, count, builtin, color, icon}]
  let dirty = false;

  function slugOf(name) {
    return String(name || "").normalize("NFD").replace(/[̀-ͯ]/g, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  }
  function fallbackColor(slug) {
    let h = 0;
    for (let i = 0; i < slug.length; i++) { h = ((h << 5) - h) + slug.charCodeAt(i); h |= 0; }
    return FALLBACK[Math.abs(h) % 10];
  }
  function effective(r) { return r.color || r.builtin || fallbackColor(r.slug); }
  function origin(r) { return r.color ? "personnalisee" : (r.builtin ? "integree" : "automatique"); }
  function displayName(r) { return r.label || r.labels[0] || r.slug; }
  function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") || "" : "";
  }
  function toast(type, msg) { if (window.showToast) window.showToast(type, msg); }
  function setDirty(v) {
    dirty = v;
    saveBtn.disabled = !v;
    status.textContent = v ? "Modifications non enregistrees" : "";
  }

  function el(tag, attrs, children) {
    const n = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (v == null) return;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else n.setAttribute(k, v);
    });
    (children || []).forEach(c => { if (c != null) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }

  function render() {
    tbody.innerHTML = "";
    if (!rows.length) {
      tbody.appendChild(el("tr", null, [el("td", { colspan: "6", class: "muted", text: "Aucune categorie." })]));
      return;
    }
    rows.forEach((r, idx) => {
      const color = effective(r);
      const icon = r.icon || "event_note";
      const preview = el("div", { class: "tlcat-preview", style: "--cat:" + color }, [
        el("span", { class: "material-symbols-outlined", text: icon }),
        el("span", { text: displayName(r) })
      ]);
      const names = el("div", null, [el("strong", { text: displayName(r) })]);
      if (r.labels.length > 1) names.appendChild(el("div", { class: "tlcat-muted", text: "Aussi : " + r.labels.slice(1).join(", ") }));

      const colorInput = el("input", { type: "color", value: color, "aria-label": "Couleur de " + displayName(r) });
      colorInput.addEventListener("input", () => {
        r.color = colorInput.value;
        preview.style.setProperty("--cat", r.color);
        orig.textContent = origin(r);
        setDirty(true);
      });
      const orig = el("span", { class: "tlcat-origin", text: origin(r) });

      const iconInput = el("input", { type: "text", class: "form-input tlcat-icon", value: r.icon || "",
                                      placeholder: "par defaut", maxlength: "40", list: "tlcat-icons",
                                      "aria-label": "Icone de " + displayName(r) });
      iconInput.addEventListener("input", () => {
        const v = iconInput.value.trim();
        const ok = !v || /^[a-z0-9_]{1,40}$/.test(v);
        iconInput.classList.toggle("is-invalid", !ok);
        if (!ok) return;
        r.icon = v || null;
        preview.firstChild.textContent = r.icon || "event_note";
        setDirty(true);
      });

      const reset = el("button", { type: "button", class: "btn-icon", title: "Revenir aux valeurs par defaut" },
                       [el("span", { class: "material-symbols-outlined", text: "restart_alt" })]);
      reset.disabled = !r.color && !r.icon;
      reset.addEventListener("click", () => { r.color = null; r.icon = null; setDirty(true); render(); });

      tbody.appendChild(el("tr", { "data-idx": String(idx) }, [
        el("td", null, [preview]),
        el("td", null, [names]),
        el("td", { text: r.count ? r.count.toLocaleString("fr-FR") : "-" }),
        el("td", null, [el("div", { class: "tlcat-color" }, [colorInput, orig])]),
        el("td", null, [iconInput]),
        el("td", null, [reset])
      ]));
    });
  }

  function load() {
    status.textContent = "Chargement...";
    fetch("/api/timeline-category-styles/catalog", { credentials: "same-origin" })
      .then(r => r.json())
      .then(j => {
        rows = (j.items || []).map(e => ({
          slug: e.slug, labels: e.labels || [], label: e.label || "", count: e.count || 0,
          builtin: e.builtin || null, color: e.color || null, icon: e.icon || null
        }));
        render();
        setDirty(false);
      })
      .catch(() => { status.textContent = "Chargement impossible."; });
  }

  saveBtn.addEventListener("click", () => {
    const items = rows.filter(r => r.color || r.icon).map(r => ({
      slug: r.slug, label: displayName(r), color: r.color || "", icon: r.icon || ""
    }));
    saveBtn.disabled = true;
    status.textContent = "Enregistrement...";
    fetch("/api/timeline-category-styles", {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ items })
    })
      .then(r => r.json().then(j => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        if (!ok || !j.ok) throw new Error((j && j.error) || "Erreur");
        setDirty(false);
        status.textContent = "Enregistre. Visible sur la timeline au prochain chargement de la page.";
        toast("success", "Couleurs des categories enregistrees.");
      })
      .catch(e => { saveBtn.disabled = false; status.textContent = ""; toast("error", "Enregistrement impossible : " + e.message); });
  });

  addForm?.addEventListener("submit", (e) => {
    e.preventDefault();
    const input = addForm.querySelector("input");
    const name = (input.value || "").trim();
    const slug = slugOf(name);
    if (!slug) return;
    if (rows.some(r => r.slug === slug)) { toast("warning", "Cette categorie existe deja dans la liste."); return; }
    rows.unshift({ slug, labels: [name], label: name, count: 0, builtin: null, color: fallbackColor(slug), icon: null });
    input.value = "";
    setDirty(true);
    render();
  });

  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  const dl = el("datalist", { id: "tlcat-icons" }, ICON_SUGGESTIONS.map(i => el("option", { value: i })));
  document.body.appendChild(dl);
  load();
})();
