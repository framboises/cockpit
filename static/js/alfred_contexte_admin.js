// Configuration > Contexte des evenements (Alfred).
// Description, surnoms et note par edition, lus par les outils Alfred
// (alfred_evenements.py). Rendu DOM uniquement (textContent), jamais de
// innerHTML sur une saisie.
(function () {
  "use strict";
  var list = document.getElementById("aec-list");
  if (!list) return;
  var statusEl = document.getElementById("aec-status");
  var limites = { description: 800, note: 400, surnoms: 12 };
  var NOTES_MAX = 3;

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function toast(type, msg) {
    if (typeof showToast === "function") showToast(type, msg);
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function icon(name) {
    return el("span", "material-symbols-outlined", name);
  }

  function counter(input, max, out) {
    function upd() {
      out.textContent = input.value.length + " / " + max;
      out.classList.toggle("is-over", input.value.length > max);
    }
    input.addEventListener("input", upd);
    upd();
  }

  function renderRow(ev) {
    var row = el("div", "aec-row");
    var head = el("button", "aec-head");
    head.type = "button";
    var name = el("span", "aec-name", ev.event);
    head.appendChild(name);
    if (ev.short) head.appendChild(el("span", "aec-short", ev.short));
    var filled = !!(ev.description || (ev.surnoms && ev.surnoms.length));
    var badge = el("span", "aec-badge" + (filled ? " is-on" : ""), filled ? "Renseigné" : "Vide");
    head.appendChild(badge);
    var chev = icon("expand_more");
    chev.classList.add("aec-chev");
    head.appendChild(chev);
    row.appendChild(head);

    var body = el("div", "aec-body");
    body.hidden = true;

    var l1 = el("label", "aec-field");
    l1.appendChild(el("span", "aec-label", "Description"));
    var desc = el("textarea", "form-input aec-desc");
    desc.rows = 4;
    desc.maxLength = limites.description;
    desc.placeholder = "Ex. : course d'endurance de camions sur 24 h, public familial et routier, "
      + "grosse affluence le samedi, camping important, parade des camions le vendredi soir...";
    desc.value = ev.description || "";
    l1.appendChild(desc);
    var c1 = el("span", "aec-count");
    l1.appendChild(c1);
    counter(desc, limites.description, c1);
    body.appendChild(l1);

    var l2 = el("label", "aec-field");
    l2.appendChild(el("span", "aec-label", "Surnoms (séparés par des virgules)"));
    var sur = el("input", "form-input");
    sur.type = "text";
    sur.placeholder = "Ex. : les camions, 24 camions, TC";
    sur.value = (ev.surnoms || []).join(", ");
    l2.appendChild(sur);
    body.appendChild(l2);

    var notes = {};
    var annees = (ev.annees || []).slice(0, NOTES_MAX);
    if (annees.length) {
      body.appendChild(el("span", "aec-label", "Note par édition (facultatif)"));
      annees.forEach(function (y) {
        var l = el("label", "aec-field aec-note");
        l.appendChild(el("span", "aec-year", y));
        var inp = el("input", "form-input");
        inp.type = "text";
        inp.maxLength = limites.note;
        inp.placeholder = "Ex. : nouvelle tribune, village déplacé côté Houx";
        inp.value = (ev.editions || {})[y] || "";
        l.appendChild(inp);
        notes[y] = inp;
        body.appendChild(l);
      });
    }

    var foot = el("div", "aec-foot");
    var meta = el("span", "aec-meta",
      ev.updated_at ? "Modifié le " + new Date(ev.updated_at).toLocaleString("fr-FR")
        + (ev.updated_by ? " par " + ev.updated_by : "") : "");
    foot.appendChild(meta);
    var save = el("button", "btn-primary aec-save");
    save.type = "button";
    save.appendChild(icon("save"));
    save.appendChild(document.createTextNode(" Enregistrer"));
    foot.appendChild(save);
    body.appendChild(foot);
    row.appendChild(body);

    head.addEventListener("click", function () {
      body.hidden = !body.hidden;
      row.classList.toggle("is-open", !body.hidden);
    });

    save.addEventListener("click", function () {
      var editions = {};
      Object.keys(notes).forEach(function (y) {
        if (notes[y].value.trim()) editions[y] = notes[y].value.trim();
      });
      // Les notes des editions plus anciennes (non affichees) sont conservees
      Object.keys(ev.editions || {}).forEach(function (y) {
        if (!(y in notes)) editions[y] = ev.editions[y];
      });
      var payload = { event: ev.event, description: desc.value, surnoms: sur.value, editions: editions };
      save.disabled = true;
      fetch("/api/alfred-chat/contexte", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify(payload)
      }).then(function (r) { return r.json(); })
        .then(function (r) {
          if (r.ok) {
            var c = r.contexte || {};
            ev.description = c.description;
            ev.surnoms = c.surnoms;
            ev.editions = c.editions;
            sur.value = (c.surnoms || []).join(", ");
            var on = !!(c.description || (c.surnoms && c.surnoms.length));
            badge.textContent = on ? "Renseigné" : "Vide";
            badge.classList.toggle("is-on", on);
            meta.textContent = "Modifié à l'instant";
            toast("success", "Contexte de " + ev.event + " enregistré");
          } else if (r.error === "surnom_deja_pris") {
            toast("error", "Surnom déjà utilisé : " + (r.conflits || []).join(", "));
          } else {
            toast("error", r.error || "Erreur");
          }
        })
        .catch(function () { toast("error", "Erreur réseau"); })
        .finally(function () { save.disabled = false; });
    });
    return row;
  }

  function load() {
    if (statusEl) statusEl.textContent = "Chargement...";
    fetch("/api/alfred-chat/contextes")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) throw new Error(d.error || "erreur");
        if (d.limites) limites = d.limites;
        while (list.firstChild) list.removeChild(list.firstChild);
        var n = 0;
        (d.evenements || []).forEach(function (ev) {
          if (ev.description || (ev.surnoms && ev.surnoms.length)) n += 1;
          list.appendChild(renderRow(ev));
        });
        if (statusEl) statusEl.textContent = n + " / " + (d.evenements || []).length + " renseignés";
      })
      .catch(function () {
        if (statusEl) statusEl.textContent = "Indisponible";
      });
  }

  load();
})();
