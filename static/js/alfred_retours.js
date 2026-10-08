/* Page admin Retours Alfred (/alfred-retours).
 *
 * Lit les instantanes figes a chaque pouce du chat (alfred_retours.py) :
 * conversation, outils appeles par le modele, appels recus par Cockpit avec
 * leur resolution et leur resume. L'admin qualifie le retour (reponse
 * attendue, note, statut). Tout le contenu (questions, reponses du modele)
 * passe par textContent, jamais par innerHTML.
 */
(function () {
  "use strict";

  var API = "/api/alfred-chat/retours";
  var list = document.getElementById("ar-list");
  var kpis = document.getElementById("ar-kpis");
  var fRating = document.getElementById("ar-f-rating");
  var fStatut = document.getElementById("ar-f-statut");
  var fMotif = document.getElementById("ar-f-motif");
  var exportLink = document.getElementById("ar-export");
  var STATUTS = { nouveau: "À traiter", traite: "Traité", ignore: "Ignoré" };
  var motifs = {};
  var ouverts = {};

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(name) { return el("span", "material-symbols-outlined", name); }
  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") || "" : "";
  }
  function toast(type, msg) { if (window.showToast) window.showToast(type, msg); }

  function api(method, path, body) {
    var opts = { method: method, credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    if (method !== "GET") opts.headers["X-CSRFToken"] = csrf();
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "http_" + r.status }; });
    }).catch(function () { return { ok: false, error: "reseau" }; });
  }

  function fmt(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric" }) +
      " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }

  function query() {
    var p = [];
    if (fRating.value) p.push("rating=" + encodeURIComponent(fRating.value));
    if (fStatut.value) p.push("statut=" + encodeURIComponent(fStatut.value));
    if (fMotif.value) p.push("motif=" + encodeURIComponent(fMotif.value));
    return p.length ? "?" + p.join("&") : "";
  }

  function renderKpis(c) {
    kpis.innerHTML = "";
    [["Pouces bas à traiter", c.nouveaux_negatifs, "is-warn"],
     ["Pouces bas", c.negatifs, ""], ["Pouces hauts", c.positifs, "is-ok"],
     ["Total", c.total, ""]].forEach(function (k) {
      var b = el("div", "ar-kpi " + k[2]);
      b.appendChild(el("div", "ar-kpi-v", String(k[1] || 0)));
      b.appendChild(el("div", "ar-kpi-l", k[0]));
      kpis.appendChild(b);
    });
    var m = c.motifs || {};
    var keys = Object.keys(m).sort(function (a, b) { return m[b] - m[a]; });
    if (keys.length) {
      var box = el("div", "ar-kpi ar-kpi-motifs");
      keys.forEach(function (k) {
        box.appendChild(el("span", "ar-tag", (motifs[k] || "Sans motif") + " · " + m[k]));
      });
      kpis.appendChild(box);
    }
  }

  function bloc(titre, contenu) {
    var s = el("div", "ar-sec");
    s.appendChild(el("div", "ar-sec-t", titre));
    s.appendChild(contenu);
    return s;
  }

  function pre(text) { return el("pre", "ar-pre", text || ""); }

  function json(v) {
    if (v == null || v === "") return "";
    if (typeof v === "string") {
      try { return JSON.stringify(JSON.parse(v), null, 1); } catch (e) { return v; }
    }
    try { return JSON.stringify(v, null, 1); } catch (e) { return String(v); }
  }

  function detail(r, card) {
    var d = el("div", "ar-detail");

    var conv = el("div", "ar-conv");
    (r.conversation || []).forEach(function (m) {
      var b = el("div", "ar-msg " + (m.role === "user" ? "is-user" : "is-bot"));
      b.appendChild(el("div", "ar-msg-r", m.role === "user" ? "Opérateur" : "Alfred"));
      b.appendChild(el("div", "ar-msg-c", m.content));
      conv.appendChild(b);
    });
    d.appendChild(bloc("Conversation jusqu'à la réponse notée", conv));

    var outils = el("div", "ar-tools");
    var appels = r.appels_cockpit || [];
    if (!appels.length) {
      var mod = r.outils_modele || [];
      outils.appendChild(el("p", "ar-muted", mod.length
        ? "Aucun appel reçu par Cockpit pour cette réponse (outils déclarés par la VM ci-dessous)."
        : "Aucun outil appelé : la réponse vient du modèle seul."));
      mod.forEach(function (t) { outils.appendChild(pre(t.name + " " + json(t.args))); });
    }
    appels.forEach(function (a) {
      var t = el("div", "ar-tool" + (a.ok === false ? " is-err" : ""));
      var h = el("div", "ar-tool-h");
      h.appendChild(el("strong", null, a.outil || "?"));
      if (a.duree_ms != null) h.appendChild(el("span", "ar-muted", a.duree_ms + " ms"));
      if (a.ok === false) h.appendChild(el("span", "ar-tag is-err", "échec"));
      t.appendChild(h);
      t.appendChild(el("div", "ar-k", "Arguments reçus"));
      t.appendChild(pre(json(a.args)));
      if (a.resolu && Object.keys(a.resolu).length) {
        t.appendChild(el("div", "ar-k", "Compris par l'outil"));
        t.appendChild(pre(json(a.resolu)));
      }
      if (a.resume) {
        t.appendChild(el("div", "ar-k", "Ce que l'outil a répondu (résumé)"));
        t.appendChild(pre(a.resume));
      }
      outils.appendChild(t);
    });
    d.appendChild(bloc("Outils", outils));

    var tech = [r.model, r.hops != null ? r.hops + " tours" : null,
                r.duration_ms ? Math.round(r.duration_ms / 100) / 10 + " s" : null,
                r.request_id].filter(Boolean).join(" · ");
    d.appendChild(el("div", "ar-muted ar-tech", tech));

    // Qualification admin
    var form = el("div", "ar-form");
    form.appendChild(el("label", "ar-k", "Réponse attendue (sert de référence au corpus de la VM)"));
    var attendue = el("textarea", "ar-input");
    attendue.rows = 3; attendue.maxLength = 2000;
    attendue.value = r.reponse_attendue || "";
    form.appendChild(attendue);
    form.appendChild(el("label", "ar-k", "Note de traitement (cause, correction faite, où)"));
    var note = el("textarea", "ar-input");
    note.rows = 2; note.maxLength = 1000;
    note.placeholder = "Ex. : outil cockpit_lieux, tribune mal résolue, corrigé dans alfred_lieux.py";
    note.value = r.note_admin || "";
    form.appendChild(note);

    var acts = el("div", "ar-acts");
    function enregistrer(statut) {
      var body = { reponse_attendue: attendue.value, note_admin: note.value };
      if (statut) body.statut = statut;
      api("POST", API + "/" + encodeURIComponent(r.id), body).then(function (j) {
        if (!j || !j.ok) { toast("error", "Enregistrement impossible (" + ((j && j.error) || "erreur") + ")."); return; }
        toast("success", statut ? "Retour marqué « " + STATUTS[statut] + " »." : "Enregistré.");
        charger();
      });
    }
    var save = el("button", "ar-btn", "Enregistrer");
    save.type = "button";
    save.addEventListener("click", function () { enregistrer(null); });
    acts.appendChild(save);
    [["traite", "Marquer traité", "is-primary"], ["ignore", "Ignorer", ""],
     ["nouveau", "Remettre à traiter", ""]].forEach(function (x) {
      if (r.statut === x[0]) return;
      var b = el("button", "ar-btn " + x[2], x[1]);
      b.type = "button";
      b.addEventListener("click", function () { enregistrer(x[0]); });
      acts.appendChild(b);
    });
    var del = el("button", "ar-btn is-danger");
    del.type = "button";
    del.appendChild(icon("delete"));
    del.title = "Supprimer définitivement ce retour";
    del.addEventListener("click", function () {
      var ask = window.showConfirmToast
        ? window.showConfirmToast("Supprimer définitivement ce retour ?", { type: "error", okLabel: "Supprimer", cancelLabel: "Annuler" })
        : Promise.resolve(false);
      ask.then(function (ok) {
        if (!ok) return;
        api("POST", API + "/" + encodeURIComponent(r.id) + "/supprimer", {}).then(function (j) {
          if (j && j.ok) { toast("success", "Retour supprimé."); charger(); }
          else toast("error", "Suppression impossible.");
        });
      });
    });
    acts.appendChild(del);
    form.appendChild(acts);
    if (r.traite_par) {
      form.appendChild(el("div", "ar-muted", "Dernière mise à jour : " + r.traite_par + ", " + fmt(r.traite_at)));
    }
    d.appendChild(bloc("Traitement", form));
    return d;
  }

  function carte(r) {
    var card = el("div", "ar-card" + (r.rating === -1 ? " is-neg" : " is-pos"));
    var head = el("button", "ar-head");
    head.type = "button";
    var ic = icon(r.rating === -1 ? "thumb_down" : "thumb_up");
    ic.className += " ar-thumb";
    head.appendChild(ic);
    var main = el("div", "ar-head-main");
    main.appendChild(el("div", "ar-q", r.question || "(question vide)"));
    var meta = el("div", "ar-meta");
    meta.appendChild(el("span", null, fmt(r.asked_at)));
    if (r.user_name) meta.appendChild(el("span", null, r.user_name));
    if (r.event) meta.appendChild(el("span", null, r.event + (r.year ? " " + r.year : "")));
    if (r.page) meta.appendChild(el("span", null, r.page));
    main.appendChild(meta);
    if (r.motif_label || r.commentaire) {
      var fb = el("div", "ar-fb");
      if (r.motif_label) fb.appendChild(el("span", "ar-tag is-motif", r.motif_label));
      if (r.commentaire) fb.appendChild(el("span", "ar-com", "« " + r.commentaire + " »"));
      main.appendChild(fb);
    }
    var rep = el("div", "ar-rep", r.reponse || "");
    main.appendChild(rep);
    head.appendChild(main);
    head.appendChild(el("span", "ar-tag ar-statut is-" + (r.statut || "nouveau"), STATUTS[r.statut] || r.statut || ""));
    card.appendChild(head);

    var body = null;
    function toggle() {
      if (body) { body.remove(); body = null; delete ouverts[r.id]; card.classList.remove("is-open"); return; }
      body = detail(r, card);
      card.appendChild(body);
      ouverts[r.id] = true;
      card.classList.add("is-open");
    }
    head.addEventListener("click", toggle);
    if (ouverts[r.id]) toggle();
    return card;
  }

  function charger() {
    var q = query();
    exportLink.href = API + "/export" + q;
    api("GET", API + q).then(function (j) {
      list.innerHTML = "";
      if (!j || !j.ok) { list.appendChild(el("p", "ar-empty", "Chargement impossible.")); return; }
      motifs = j.motifs || {};
      renderKpis(j.compteurs || {});
      if (!j.retours.length) {
        list.appendChild(el("p", "ar-empty", "Aucun retour pour ces filtres."));
        return;
      }
      j.retours.forEach(function (r) { list.appendChild(carte(r)); });
    });
  }

  [fRating, fStatut, fMotif].forEach(function (s) { s.addEventListener("change", charger); });
  charger();
})();
