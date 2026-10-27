// gravite_fiches.js - page /admin/gravite-fiches : avancement du traitement
// (pcorg_digest.py, hors Cockpit), lots de fiches et controle par un admin.
// Controle en un geste : cliquer (ou taper) la bonne gravite ; celle du
// modele = juste, en dessous = trop haut, au-dessus = trop bas.
(function () {
  "use strict";

  var GRAV_LABELS = {
    1: "1 - Information",
    2: "2 - Gene, anomalie",
    3: "3 - Intervention, risque modere",
    4: "4 - Atteinte personnes / biens",
    5: "5 - Danger vital, crise",
  };
  var state = { batch: null, items: [], focus: 0, timer: null, running: false };

  // ---------- HTTP ----------
  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  function apiGet(url) {
    return fetch(url, { cache: "no-store", credentials: "same-origin" }).then(function (r) {
      return r.json().then(function (b) { if (!r.ok) throw new Error(b.error || r.status); return b; });
    });
  }
  function apiPost(url, data) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(data || {}),
    }).then(function (r) {
      return r.json().then(function (b) { if (!r.ok) throw new Error(b.error || r.status); return b; });
    });
  }
  function toast(type, msg) {
    if (typeof window.showToast === "function") window.showToast(type, msg);
  }

  // ---------- DOM ----------
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function fmtInt(n) { return (n || 0).toLocaleString("fr-FR"); }
  function fmtPct(a, b) { return b ? Math.round((a * 100) / b) + " %" : "-"; }
  function fmtTime(iso) {
    if (!iso) return "-";
    var d = new Date(iso);
    return d.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }
  function fmtDuration(s) {
    if (s === null || s === undefined) return "-";
    var h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
    return h ? h + " h " + (m < 10 ? "0" : "") + m : m + " min";
  }
  function kpi(label, value, sub) {
    var k = el("div", "gf-kpi");
    k.appendChild(el("div", "gf-kpi-label", label));
    k.appendChild(el("div", "gf-kpi-value", value));
    if (sub) k.appendChild(el("div", "gf-kpi-sub", sub));
    return k;
  }

  // ---------- Resume ----------
  function loadSummary() {
    return apiGet("/api/gravite-fiches/summary").then(renderSummary).catch(function (e) {
      toast("error", "Lecture de l'avancement impossible : " + e.message);
    });
  }

  function renderRun(s) {
    var run = s.run || {};
    var req = s.request || {};
    var pending = req.state === "pending";
    var box = document.getElementById("gf-run-box");
    var bar = document.getElementById("gf-run-bar");
    var title = document.getElementById("gf-run-title");
    var detail = document.getElementById("gf-run-detail");
    var btn = document.getElementById("gf-launch");
    btn.disabled = pending || !!run.alive;
    btn.title = btn.disabled ? "Un traitement est deja demande ou en cours" : "";
    document.getElementById("gf-night").checked = !!s.night_enabled;

    if (run.alive) {
      var n = (run.done || 0) + (run.errors || 0);
      var target = run.target || run.todo_at_start || 0;
      var pct = target ? Math.min(100, (100 * n) / target) : 0;
      box.hidden = false;
      box.classList.remove("gf-waiting");
      bar.style.width = pct.toFixed(1) + "%";
      title.textContent = "Traitement en cours : " + fmtInt(n) + " / " + fmtInt(target) + " fiches (" + Math.round(pct) + " %)";
      detail.textContent = [
        run.max_batches ? run.max_batches + " lot(s) de " + run.batch_size : (run.end_at ? "jusqu'a " + run.end_at : "tout le stock"),
        "lot " + (run.batch || "-"),
        run.rate_per_min ? run.rate_per_min + " fiches / min" : null,
        run.eta_s !== null && run.eta_s !== undefined ? "fin dans " + fmtDuration(run.eta_s) : null,
        run.errors ? run.errors + " echec(s)" : null,
        run.requested_by ? "par " + run.requested_by : null,
        run.stop_requested ? "ARRET DEMANDE" : null,
      ].filter(Boolean).join("  |  ");
    } else if (pending) {
      box.hidden = false;
      box.classList.add("gf-waiting");
      title.textContent = "Demande en attente : " + req.batches + " lot(s) de " + req.batch_size;
      detail.textContent = "prise en charge par la tache planifiee sous une minute (demande a " + fmtTime(req.requested_at) + ")";
    } else {
      box.hidden = true;
    }
    return run.alive || pending;
  }

  function renderSummary(s) {
    var run = s.run || {};
    state.running = renderRun(s);
    document.getElementById("gf-stop").hidden = !state.running || run.stop_requested;

    var runState = document.getElementById("gf-run-state");
    if (run.alive) {
      runState.textContent = "En cours : lot " + (run.batch || "-") + (run.stop_requested ? " (arret demande)" : "");
    } else if (run.ended_at) {
      runState.textContent = "Dernier traitement termine le " + fmtTime(run.ended_at) + " (" + (run.stop_reason || "-") + ")";
    } else {
      runState.textContent = "Aucun traitement lance";
    }

    var k = document.getElementById("gf-kpis");
    clear(k);
    k.appendChild(kpi("Fiches structurees", fmtInt(s.structured), "sur " + fmtInt(s.total) + " (" + fmtPct(s.structured, s.total) + ")"));
    k.appendChild(kpi("Avec position", fmtInt(s.structured_gps), "sur " + fmtInt(s.total_gps) + " (" + fmtPct(s.structured_gps, s.total_gps) + ")"));
    if (run.alive) {
      k.appendChild(kpi("Ce traitement", fmtInt(run.done), "sur " + fmtInt(run.todo_at_start) + " a faire"));
      k.appendChild(kpi("Rythme", run.rate_per_min ? run.rate_per_min + " / min" : "-", "fin estimee dans " + fmtDuration(run.eta_s)));
    }
    k.appendChild(kpi("Echecs", fmtInt(s.errors), fmtInt(s.abandoned) + " abandonnees (3 essais)"));

    var pct = s.total ? (100 * s.structured) / s.total : 0;
    document.getElementById("gf-progress-bar").style.width = pct.toFixed(1) + "%";
    document.getElementById("gf-progress-label").textContent =
      fmtInt(s.structured) + " / " + fmtInt(s.total) + " fiches (consignes " + s.prompt_version + ", modele " + s.model + ")";

    var g = document.getElementById("gf-gravites");
    clear(g);
    var max = 0, n;
    for (n = 1; n <= 5; n++) max = Math.max(max, s.by_gravite[String(n)] || 0);
    for (n = 1; n <= 5; n++) {
      var c = s.by_gravite[String(n)] || 0;
      var row = el("div", "gf-grav-row");
      row.appendChild(el("span", null, GRAV_LABELS[n]));
      var track = el("div", "gf-grav-track");
      var fill = el("div", "gf-grav-fill gf-g" + n);
      fill.style.width = (max ? (100 * c) / max : 0) + "%";
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("span", "gf-num", fmtInt(c)));
      g.appendChild(row);
    }

    renderReviews(s.reviews || {});
    renderBatches(s.batches || []);
    schedule();
  }

  function renderReviews(r) {
    var v = r.verdicts || {};
    var k = document.getElementById("gf-review-kpis");
    clear(k);
    var count = r.count || 0;
    // Marge d'erreur a 95 % sur la justesse (approximation normale)
    var p = count ? (v.ok || 0) / count : 0;
    var marge = count ? Math.round(196 * Math.sqrt((p * (1 - p)) / count)) : null;
    k.appendChild(kpi("Fiches controlees", fmtInt(count)));
    k.appendChild(kpi("Justes", fmtPct(v.ok || 0, count), marge !== null ? "marge +/- " + marge + " points" : ""));
    k.appendChild(kpi("Trop haut", fmtInt(v.trop_haut || 0), "modele trop severe"));
    k.appendChild(kpi("Trop bas", fmtInt(v.trop_bas || 0), "modele trop indulgent"));
    k.appendChild(kpi("Resumes inexacts", fmtInt(r.resume_inexact || 0), fmtPct(r.resume_inexact || 0, count)));
    document.getElementById("gf-mobile-stats").textContent = count
      ? fmtInt(count) + " fiches controlees, " + fmtPct(v.ok || 0, count) + " justes (marge +/- " + marge + " points)"
      : "Aucune fiche controlee pour l'instant";

    var t = document.getElementById("gf-matrix");
    clear(t);
    var head = el("tr");
    head.appendChild(el("th", null, ""));
    for (var c = 1; c <= 5; c++) head.appendChild(el("th", null, c));
    t.appendChild(head);
    var m = r.matrix || {};
    for (var a = 1; a <= 5; a++) {
      var tr = el("tr");
      tr.appendChild(el("th", null, a));
      for (var b = 1; b <= 5; b++) {
        var val = m[a + ">" + b] || 0;
        var td = el("td", val ? (a === b ? "gf-diag" : "gf-off") : null, val || "");
        tr.appendChild(td);
      }
      t.appendChild(tr);
    }
  }

  function renderBatches(batches) {
    var body = document.getElementById("gf-batches-body");
    clear(body);
    if (!batches.length) {
      var tr0 = el("tr");
      var td0 = el("td", "gf-muted", "Aucun lot pour l'instant.");
      td0.colSpan = 7;
      tr0.appendChild(td0);
      body.appendChild(tr0);
      return;
    }
    batches.forEach(function (b) {
      var tr = el("tr", b.batch === state.batch ? "gf-active" : null);
      tr.appendChild(el("td", null, b.batch));
      tr.appendChild(el("td", null, fmtTime(b.last)));
      tr.appendChild(el("td", null, b.prompt_version || "-"));
      tr.appendChild(el("td", "gf-num", b.n));
      tr.appendChild(el("td", "gf-num", b.errors || ""));
      tr.appendChild(el("td", "gf-num", b.reviewed + " / " + (b.n - (b.errors || 0))));
      tr.appendChild(el("td", "gf-num", b.reviewed ? fmtPct(b.ok, b.reviewed) : "-"));
      tr.addEventListener("click", function () { openBatch(b.batch); });
      body.appendChild(tr);
    });
  }

  function schedule() {
    clearTimeout(state.timer);
    state.timer = setTimeout(loadSummary, state.running ? 5000 : 30000);
  }

  // ---------- Fiches d'un lot / d'un sondage ----------
  function openBatch(batch) {
    state.batch = batch;
    apiGet("/api/gravite-fiches/batch/" + encodeURIComponent(batch)).then(function (r) {
      showItems("Lot " + batch, r.items);
      loadSummary();
    }).catch(function (e) { toast("error", "Lot illisible : " + e.message); });
  }

  function openSample() {
    var g = document.getElementById("gf-sample-grav").value;
    state.batch = null;
    apiGet("/api/gravite-fiches/sample?n=20" + (g ? "&gravite=" + g : "")).then(function (r) {
      showItems("Sondage : " + r.items.length + " fiches non controlees" + (g ? " de gravite " + g : ""), r.items);
      loadSummary();
    }).catch(function (e) { toast("error", "Sondage impossible : " + e.message); });
  }

  function showItems(title, items) {
    state.items = items;
    state.focus = 0;
    document.getElementById("gf-items-card").hidden = false;
    document.getElementById("gf-items-title").textContent = title;
    var box = document.getElementById("gf-items");
    clear(box);
    if (!items.length) {
      box.appendChild(el("div", "gf-muted", "Rien a afficher."));
      return;
    }
    items.forEach(function (it, i) { box.appendChild(renderItem(it, i)); });
    document.body.classList.add("gf-reviewing");
    var first = 0;
    for (var i = 0; i < items.length; i++) { if (!items[i].review && !items[i].error) { first = i; break; } }
    setFocus(first, false);
    document.getElementById("gf-items-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderItem(it, idx) {
    var f = it.fiche || {};
    var card = el("div", "gf-item" + (it.review ? " gf-done" : ""));
    card.dataset.idx = idx;
    card.addEventListener("click", function () { setFocus(idx, false); });

    // Gauche : fiche d'origine
    var left = el("div");
    left.appendChild(el("div", "gf-meta",
      [f.event, f.year, f.date_local, f.time_local].filter(Boolean).join(" ") +
      "  |  " + (f.category || "sans categorie") + "  |  urgence saisie : " + (f.niveau_urgence || "aucune")));
    left.appendChild(el("div", "gf-title", f.text || "(sans titre)"));
    var der = el("div", "gf-deroule", f.comment || "(pas de deroule)");
    left.appendChild(der);
    if ((f.comment || "").length > 700) {
      var more = el("button", "gf-link", "Tout afficher");
      more.type = "button";
      more.addEventListener("click", function (ev) {
        ev.stopPropagation();
        der.classList.toggle("gf-open");
        more.textContent = der.classList.contains("gf-open") ? "Replier" : "Tout afficher";
      });
      left.appendChild(more);
    }
    card.appendChild(left);

    // Droite : sortie du modele + controle
    var right = el("div", "gf-model");
    if (it.error) {
      right.appendChild(el("div", "gf-error", "Echec du modele (" + (it.attempts || 1) + " essai(s)) : " + it.error));
      card.appendChild(right);
      return card;
    }
    var head = el("div", "gf-head");
    head.appendChild(el("span", "gf-badge gf-g" + it.gravite, it.gravite));
    head.appendChild(el("strong", null, GRAV_LABELS[it.gravite] || ""));
    right.appendChild(head);
    right.appendChild(el("div", "gf-muted", it.justification || ""));
    var dl = el("dl");
    [["Type", it.type], ["Victimes", it.victimes], ["Moyens engages", it.moyens_engages],
     ["Issue", it.issue], ["Resume", it.resume]].forEach(function (p) {
      dl.appendChild(el("dt", null, p[0]));
      dl.appendChild(el("dd", null, p[1] === undefined || p[1] === null ? "-" : p[1]));
    });
    right.appendChild(dl);

    var act = el("div", "gf-actions");
    act.appendChild(el("span", "gf-muted", "Bonne gravite :"));
    for (var g = 1; g <= 5; g++) {
      var b = el("button", "gf-gbtn" + (g === it.gravite ? " gf-model-val" : ""), g);
      b.type = "button";
      b.title = g === it.gravite ? "Juste (gravite du modele)" : GRAV_LABELS[g];
      b.dataset.g = g;
      b.addEventListener("click", (function (gv) {
        return function (ev) { ev.stopPropagation(); review(idx, gv); };
      })(g));
      act.appendChild(b);
    }
    var lab = el("label", "gf-check");
    var cb = el("input");
    cb.type = "checkbox";
    cb.className = "gf-inexact";
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode("Resume inexact"));
    act.appendChild(lab);
    var note = el("input", "gf-note");
    note.placeholder = "Note (facultatif)";
    note.addEventListener("click", function (ev) { ev.stopPropagation(); });
    act.appendChild(note);
    var verdict = el("span", "gf-verdict");
    act.appendChild(verdict);
    right.appendChild(act);
    card.appendChild(right);
    paintReview(card, it);
    return card;
  }

  function paintReview(card, it) {
    var rv = it.review;
    var btns = card.querySelectorAll(".gf-gbtn");
    for (var i = 0; i < btns.length; i++) {
      var g = Number(btns[i].dataset.g);
      btns[i].classList.remove("gf-chosen", "gf-g1", "gf-g2", "gf-g3", "gf-g4", "gf-g5");
      if (rv && rv.gravite_validee === g) btns[i].classList.add("gf-chosen", "gf-g" + g);
    }
    var cb = card.querySelector(".gf-inexact");
    var note = card.querySelector(".gf-note");
    var verdict = card.querySelector(".gf-verdict");
    if (cb) cb.checked = !!(rv && rv.resume_inexact);
    if (note && rv) note.value = rv.note || "";
    if (verdict) {
      if (!rv) { verdict.textContent = ""; verdict.className = "gf-verdict"; }
      else {
        var txt = { ok: "Juste", trop_haut: "Trop haut", trop_bas: "Trop bas" }[rv.verdict] || rv.verdict;
        verdict.textContent = txt + " - " + (rv.by || "");
        verdict.className = "gf-verdict " + (rv.verdict === "ok" ? "ok" : "ko");
      }
    }
    card.classList.toggle("gf-done", !!rv);
  }

  function review(idx, g) {
    var it = state.items[idx];
    if (!it || it.error) return;
    var card = document.querySelector('.gf-item[data-idx="' + idx + '"]');
    var verdict = g === it.gravite ? "ok" : (g < it.gravite ? "trop_haut" : "trop_bas");
    var payload = {
      verdict: verdict, gravite_validee: g,
      resume_inexact: !!(card && card.querySelector(".gf-inexact").checked),
      note: card ? card.querySelector(".gf-note").value : "",
    };
    apiPost("/api/gravite-fiches/" + encodeURIComponent(it.id) + "/review", payload).then(function (r) {
      it.review = r.review;
      paintReview(card, it);
      // Fiche suivante non controlee (sinon la suivante tout court)
      var next = -1;
      for (var j = idx + 1; j < state.items.length; j++) {
        if (!state.items[j].review && !state.items[j].error) { next = j; break; }
      }
      if (next >= 0) setFocus(next, true);
      else if (idx + 1 < state.items.length) setFocus(idx + 1, true);
      else {
        updateMobileBar();
        toast("success", "Toutes les fiches de la liste sont controlees");
      }
      loadSummary();
    }).catch(function (e) { toast("error", "Controle non enregistre : " + e.message); });
  }

  function isPhone() { return window.matchMedia("(max-width: 760px)").matches; }

  function setFocus(idx, scroll) {
    var cards = document.querySelectorAll(".gf-item");
    if (!cards.length) return;
    idx = Math.max(0, Math.min(cards.length - 1, idx));
    for (var i = 0; i < cards.length; i++) cards[i].classList.toggle("gf-focus", i === idx);
    state.focus = idx;
    updateMobileBar();
    if (isPhone()) {
      // Une fiche a la fois : on remonte en haut de la fiche affichee
      var top = cards[idx].getBoundingClientRect().top + window.pageYOffset - 10;
      window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    } else if (scroll) {
      cards[idx].scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  // ---------- Telephone : barre de reponse fixe ----------
  function currentCard() {
    return document.querySelector('.gf-item[data-idx="' + state.focus + '"]');
  }

  function updateMobileBar() {
    var bar = document.getElementById("gf-mobile-bar");
    var it = state.items[state.focus];
    bar.hidden = !it;
    if (!it) return;
    var done = state.items.filter(function (x) { return x.review; }).length;
    document.getElementById("gf-mb-count").textContent =
      (state.focus + 1) + " / " + state.items.length + "  (" + done + " controlee" + (done > 1 ? "s" : "") + ")";
    var card = currentCard();
    var cb = card && card.querySelector(".gf-inexact");
    document.getElementById("gf-mb-inexact").checked = !!(cb && cb.checked);
    var btns = document.querySelectorAll("#gf-mb-grav button");
    for (var i = 0; i < btns.length; i++) {
      var g = Number(btns[i].dataset.g);
      btns[i].className = "";
      btns[i].disabled = !!it.error;
      if (g === it.gravite) btns[i].classList.add("gf-model-val");
      if (it.review && it.review.gravite_validee === g) btns[i].classList.add("gf-chosen", "gf-g" + g);
    }
  }

  function initMobileBar() {
    var btns = document.querySelectorAll("#gf-mb-grav button");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function (ev) {
        review(state.focus, Number(ev.currentTarget.dataset.g));
      });
    }
    document.getElementById("gf-mb-inexact").addEventListener("change", function (ev) {
      var card = currentCard();
      var cb = card && card.querySelector(".gf-inexact");
      if (cb) cb.checked = ev.target.checked;
    });
    document.getElementById("gf-mb-prev").addEventListener("click", function () { setFocus(state.focus - 1, true); });
    document.getElementById("gf-mb-next").addEventListener("click", function () { setFocus(state.focus + 1, true); });
    document.getElementById("gf-mobile-sample").addEventListener("click", function () {
      document.getElementById("gf-sample-grav").value = "";
      openSample();
    });
  }

  function onKey(ev) {
    var tag = (ev.target && ev.target.tagName) || "";
    if (tag === "INPUT" && ev.target.type !== "checkbox") return;
    if (tag === "SELECT" || tag === "TEXTAREA") return;
    if (!state.items.length || document.getElementById("gf-items-card").hidden) return;
    if (ev.key >= "1" && ev.key <= "5") { ev.preventDefault(); review(state.focus, Number(ev.key)); }
    else if (ev.key === "r" || ev.key === "R") {
      var card = document.querySelector('.gf-item[data-idx="' + state.focus + '"]');
      var cb = card && card.querySelector(".gf-inexact");
      if (cb) cb.checked = !cb.checked;
    }
    else if (ev.key === "ArrowDown" || ev.key === "ArrowRight") { ev.preventDefault(); setFocus(state.focus + 1, true); }
    else if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") { ev.preventDefault(); setFocus(state.focus - 1, true); }
  }

  function stopRun() {
    apiPost("/api/gravite-fiches/stop", {}).then(function (r) {
      var msg = r.running ? "Arret demande : pris en compte apres la fiche en cours"
        : (r.cancelled ? "Demande annulee" : "Aucun traitement en cours");
      toast(r.running || r.cancelled ? "success" : "info", msg);
      loadSummary();
    }).catch(function (e) { toast("error", "Arret impossible : " + e.message); });
  }

  var LAUNCH_ERRORS = {
    traitement_en_cours: "Un traitement est deja en cours",
    demande_deja_en_attente: "Une demande attend deja la tache planifiee",
    parametres_invalides: "Nombre de lots ou taille invalide",
  };

  function launch() {
    var batches = parseInt(document.getElementById("gf-launch-batches").value, 10) || 1;
    var size = parseInt(document.getElementById("gf-launch-size").value, 10);
    apiPost("/api/gravite-fiches/launch", { batches: batches, batch_size: size }).then(function () {
      toast("success", batches + " lot(s) de " + size + " demande(s) : depart sous une minute");
      loadSummary();
    }).catch(function (e) { toast("error", LAUNCH_ERRORS[e.message] || ("Lancement impossible : " + e.message)); });
  }

  function toggleNight(ev) {
    var on = ev.target.checked;
    apiPost("/api/gravite-fiches/night", { enabled: on }).then(function () {
      toast("success", on ? "Traitement de nuit active (01:00 - 06:00)" : "Traitement de nuit coupe");
    }).catch(function (e) {
      ev.target.checked = !on;
      toast("error", "Reglage non enregistre : " + e.message);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.getElementById("gf-refresh").addEventListener("click", loadSummary);
    document.getElementById("gf-sample").addEventListener("click", openSample);
    document.getElementById("gf-stop").addEventListener("click", stopRun);
    document.getElementById("gf-launch").addEventListener("click", launch);
    document.getElementById("gf-night").addEventListener("change", toggleNight);
    initMobileBar();
    document.addEventListener("keydown", onKey);
    loadSummary();
  });
})();
