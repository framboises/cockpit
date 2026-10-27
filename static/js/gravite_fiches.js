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
  // Faits que l'admin peut signaler faux (meme liste que gravite_fiches.CHAMPS, sauf resume)
  var CHAMPS = [["nature", "Nature"], ["lieu", "Lieu"], ["position", "Position"], ["heures", "Heures"],
                ["equipement", "Equipement"], ["moyens", "Moyens"], ["services", "Services ext."], ["causes", "Causes"],
                ["issue", "Issue"], ["sous_classification", "Sous-classif."], ["ronde", "Ronde"], ["alarme", "Alarme"],
                ["dysfonctionnement", "Dysfonct."], ["jalon", "Jalon"], ["secours", "Secours"],
                ["prise_service", "Prise de service"]];
  // Puces des blocs v3 : affichees seulement si le bloc est present sur la fiche
  var CHAMPS_BLOCS = ["ronde", "alarme", "dysfonctionnement", "jalon", "secours", "prise_service"];
  var NATURE_LABELS = { incident: "Incident", routine: "Routine", information: "Information" };
  var POS_LABELS = { gps: "GPS d'origine", carroyage: "Carroyage", lieu: "Lieu cite", titre: "Titre", aucune: "Aucune" };
  var ISSUE_LABELS = { resolu: "Resolu", transmis: "Transmis", sans_suite: "Sans suite", en_attente: "En attente", non_precise: "Non precise" };

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
    renderQuality(s.quality || {});
    renderBatches(s.batches || []);
    schedule();
  }

  function qbox(title, rows) {
    var b = el("div", "gf-qbox");
    b.appendChild(el("h5", null, title));
    if (!rows.length) b.appendChild(el("div", "gf-muted", "Pas encore de donnees"));
    rows.forEach(function (r) {
      var row = el("div", "gf-qrow");
      row.appendChild(el("span", null, r[0]));
      row.appendChild(el("strong", null, r[1]));
      b.appendChild(row);
    });
    return b;
  }

  function renderQuality(q) {
    var box = document.getElementById("gf-quality");
    clear(box);
    if (!q.total) {
      box.appendChild(el("div", "gf-muted", "Aucune fiche structuree avec les consignes actuelles : lancez un lot."));
      return;
    }
    var grid = el("div", "gf-quality-grid");
    var pos = q.positions || {};
    var located = q.total - (pos.aucune || 0);
    grid.appendChild(qbox("Position (" + fmtPct(located, q.total) + " des fiches localisees)",
      Object.keys(POS_LABELS).filter(function (k) { return pos[k]; }).map(function (k) {
        return [POS_LABELS[k], fmtInt(pos[k]) + " (" + fmtPct(pos[k], q.total) + ")"];
      })));
    var d = q.delais || {};
    grid.appendChild(qbox("Delais depuis le signalement (mediane)", [
      ["Engagement des moyens", d.engagement && d.engagement.n ? d.engagement.mediane + " min (" + d.engagement.n + " fiches)" : "-"],
      ["Arrivee sur place", d.sur_place && d.sur_place.n ? d.sur_place.mediane + " min (" + d.sur_place.n + " fiches)" : "-"],
      ["Fin d'intervention", d.fin && d.fin.n ? d.fin.mediane + " min (" + d.fin.n + " fiches)" : "-"],
      ["Completude moyenne", q.completude_moy !== null && q.completude_moy !== undefined ? q.completude_moy + " / 4" : "-"],
    ]));
    grid.appendChild(qbox("Equipements en cause (recurrences)", (q.equipements || []).map(function (r) { return [r.v, r.n]; })));
    grid.appendChild(qbox("Services exterieurs", (q.services || []).map(function (r) { return [r.v, r.n]; })));
    grid.appendChild(qbox("Causes", (q.causes || []).map(function (r) { return [r.v, r.n]; })));
    grid.appendChild(qbox("Issue", (q.issues || []).map(function (r) { return [ISSUE_LABELS[r.v] || r.v, r.n]; })));
    var perso = (q.donnees_perso || []).map(function (r) { return [r.v, r.n]; });
    perso.unshift(["Doublons probables", fmtInt(q.doublons)]);
    var dd = q.doublons_declares || {};
    if (dd.n) perso.unshift(["Doublons declares retrouves", dd.retrouves + " / " + dd.n + " (" + fmtPct(dd.retrouves, dd.n) + ")"]);
    grid.appendChild(qbox("Doublons et donnees personnelles", perso));

    function lab(v) { return String(v || "-").replace(/_/g, " "); }
    function pairs(list) { return (list || []).map(function (r) { return [lab(r.v), r.n]; }); }
    grid.appendChild(qbox("Nature des fiches", pairs(q.natures).map(function (r) {
      return [NATURE_LABELS[r[0]] || r[0], r[1] + " (" + fmtPct(r[1], q.total) + ")"];
    }).concat(pairs(q.activites))));
    var ro = q.rondes || {};
    grid.appendChild(qbox("Rondes (" + fmtInt(ro.n) + ")", [
      ["RAS / anomalie", fmtInt(ro.ras) + " / " + fmtInt(ro.anomalies)],
      ["Duree mediane", ro.duree_mediane !== null && ro.duree_mediane !== undefined ? ro.duree_mediane + " min" : "-"],
    ].concat(pairs(ro.points).slice(0, 8).map(function (r) { return ["Point : " + r[0], r[1]]; }))
     .concat(pairs(ro.anomalies_top).slice(0, 5).map(function (r) { return ["Anomalie : " + r[0], r[1]]; }))));
    var a = q.alarmes || {};
    grid.appendChild(qbox("Alarmes intrusion", [
      ["Mises hors service", fmtInt(a.hors_service)], ["Remises en service", fmtInt(a.remises)],
      ["JAMAIS remises (24 h)", fmtInt(a.non_remises)],
    ].concat((a.derniers_non_remis || []).map(function (x) { return [x.quand, x.batiment || "-"]; }))));
    var rx = q.retex || {};
    grid.appendChild(qbox("Retour d'experience (dysfonctionnements)", pairs(rx.par_type)
      .concat((rx.derniers || []).slice(0, 8).map(function (x) { return [x.quand + " - " + lab(x.type), x.description || "-"]; }))));
    var jl = q.jalons || {};
    grid.appendChild(qbox("Jalons d'exploitation", pairs(jl.par_type)
      .concat((jl.derniers || []).slice(0, 8).map(function (x) { return [x.quand, lab(x.type) + " " + (x.heure || "")]; }))));
    var sc = q.secours || {};
    grid.appendChild(qbox("Bilan secours (" + fmtInt(sc.n) + ")", pairs(sc.nature)
      .concat(pairs(sc.tranche_age).map(function (r) { return ["Age " + r[0], r[1]]; }))
      .concat(pairs(sc.prise_en_charge).map(function (r) { return ["Prise en charge : " + r[0], r[1]]; }))));
    var pr = q.presences || {};
    grid.appendChild(qbox("Prises de service (" + fmtInt(pr.n) + ")", pairs(pr.postes)));
    grid.appendChild(qbox("Reactivite des services", (q.reactivite || []).map(function (x) {
      return [x.service + " (" + x.n + ")", (x.fin_mediane !== null ? x.fin_mediane + " min" : "-") +
        (x.relances_moy !== null ? " | " + x.relances_moy + " rel." : "")];
    })));
    var tx = q.taxonomie || {};
    grid.appendChild(qbox("Taxonomie", [["Hors liste officielle", fmtInt(tx.hors_liste)],
      ["Sous-classif. proposees (absentes)", fmtInt(tx.proposees)]]
      .concat(pairs(tx.sous_classifications).slice(0, 8))
      .concat(pairs(tx.motifs).slice(0, 5).map(function (r) { return ["Motif : " + r[0], r[1]]; }))));
    box.appendChild(grid);
  }

  function renderReviews(r) {
    var v = r.verdicts || {};
    var k = document.getElementById("gf-review-kpis");
    clear(k);
    var count = r.count || 0;
    // Marge d'erreur a 95 % sur la justesse (approximation normale)
    var p = count ? (v.ok || 0) / count : 0;
    var marge = count ? Math.round(196 * Math.sqrt((p * (1 - p)) / count)) : null;
    k.appendChild(kpi("Fiches controlees", fmtInt(count), r.ignorees ? r.ignorees + " ignoree(s) en plus (tests, essais)" : ""));
    k.appendChild(kpi("Justes", fmtPct(v.ok || 0, count), marge !== null ? "marge +/- " + marge + " points" : ""));
    var al = r.aleatoire || {};
    var pa = al.n ? al.ok / al.n : 0;
    k.appendChild(kpi("Justes (tirage au hasard)", al.n ? fmtPct(al.ok, al.n) : "-",
      al.n ? al.n + " fiches, marge +/- " + Math.round(196 * Math.sqrt((pa * (1 - pa)) / al.n)) + " points"
        : "mesure non biaisee"));
    k.appendChild(kpi("Trop haut", fmtInt(v.trop_haut || 0), "modele trop severe"));
    k.appendChild(kpi("Trop bas", fmtInt(v.trop_bas || 0), "modele trop indulgent"));
    k.appendChild(kpi("Resumes inexacts", fmtInt(r.resume_inexact || 0), fmtPct(r.resume_inexact || 0, count)));
    document.getElementById("gf-mobile-stats").textContent = count
      ? fmtInt(count) + " fiches controlees, " + fmtPct(v.ok || 0, count) + " justes (marge +/- " + marge + " points)"
      : "Aucune fiche controlee pour l'instant";

    var vb = document.getElementById("gf-versions");
    clear(vb);
    var bv = r.by_version || {};
    Object.keys(bv).sort().forEach(function (ver) {
      var x = bv[ver];
      var tr = el("tr");
      tr.appendChild(el("td", null, ver));
      tr.appendChild(el("td", "gf-num", x.n));
      tr.appendChild(el("td", "gf-num", fmtPct(x.ok, x.n)));
      tr.appendChild(el("td", "gf-num", x.n ? (x.ecart / x.n).toFixed(2) : "-"));
      vb.appendChild(tr);
    });
    var cf = document.getElementById("gf-champs-faux");
    clear(cf);
    var cfx = r.champs_faux || {};
    var anyFaux = false;
    CHAMPS.concat([["resume", "Resume"]]).forEach(function (c) {
      if (!cfx[c[0]]) return;
      anyFaux = true;
      cf.appendChild(el("span", "gf-tag gf-warn", c[1] + " : " + cfx[c[0]]));
    });
    if (r.doublons && (r.doublons.oui || r.doublons.non)) {
      anyFaux = true;
      cf.appendChild(el("span", "gf-tag", "Doublons confirmes " + r.doublons.oui + " / rejetes " + r.doublons.non));
    }
    if (!anyFaux) cf.appendChild(el("span", "gf-muted", "Aucun champ signale"));

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
      tr.appendChild(el("td", "gf-num", b.marked ? b.marked_done + " / " + b.marked
        : b.reviewed + " / " + (b.n - (b.errors || 0))));
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
  function openBatch(batch, all) {
    state.batch = batch;
    apiGet("/api/gravite-fiches/batch/" + encodeURIComponent(batch) + (all ? "?tout=1" : "")).then(function (r) {
      var title = r.only_marked
        ? "Lot " + batch + " : " + r.items.length + " fiches a controler sur " + r.total
        : "Lot " + batch + " : " + r.items.length + " fiches";
      showItems(title, r.items, r.only_marked ? { label: "Voir tout le lot (" + r.total + ")", fn: function () { openBatch(batch, true); } }
        : (r.marked ? { label: "Seulement les " + r.marked + " a controler", fn: function () { openBatch(batch, false); } } : null));
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

  function showItems(title, items, toggle) {
    state.items = items;
    state.focus = 0;
    document.getElementById("gf-items-card").hidden = false;
    document.getElementById("gf-items-title").textContent = title;
    var box = document.getElementById("gf-items");
    clear(box);
    if (toggle) {
      var tb = el("button", "btn gf-batch-toggle", toggle.label);
      tb.type = "button";
      tb.addEventListener("click", toggle.fn);
      box.appendChild(tb);
    }
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
    if (it.a_controler) {
      var why = el("div", "gf-why");
      why.appendChild(el("span", "gf-muted", "A controler : "));
      (it.a_controler.raisons || []).forEach(function (r) {
        why.appendChild(el("span", "gf-tag" + (r === "aleatoire" ? "" : " gf-warn"), r === "aleatoire" ? "tirage au hasard" : r));
      });
      right.appendChild(why);
    }
    var head = el("div", "gf-head");
    head.appendChild(el("span", "gf-muted gf-ia-label", "Gravite IA"));
    head.appendChild(el("span", "gf-badge gf-g" + it.gravite, it.gravite));
    head.appendChild(el("strong", null, GRAV_LABELS[it.gravite] || ""));
    right.appendChild(head);
    right.appendChild(el("div", "gf-muted", it.justification || ""));
    var dl = el("dl");
    [["Type", it.type], ["Victimes", it.victimes], ["Issue", it.issue], ["Resume", it.resume]].forEach(function (p) {
      dl.appendChild(el("dt", null, p[0]));
      dl.appendChild(el("dd", null, p[1] === undefined || p[1] === null ? "-" : p[1]));
    });
    right.appendChild(dl);
    right.appendChild(renderFacts(it));
    right.appendChild(renderFieldChips(it));
    if (it.doublon_probable) right.appendChild(renderDuplicate(it));

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
    var ign = el("button", "gf-ignore", "Ignorer");
    ign.type = "button";
    ign.title = "Fiche de test ou d'essai : ni jugee, ni retraitee, hors statistiques";
    ign.addEventListener("click", function (ev) { ev.stopPropagation(); review(idx, IGNORE); });
    act.appendChild(ign);
    var verdict = el("span", "gf-verdict");
    act.appendChild(verdict);
    right.appendChild(act);
    card.appendChild(right);
    paintReview(card, it);
    return card;
  }

  function tags(list, cls) {
    var span = el("span");
    (list || []).forEach(function (x) { span.appendChild(el("span", "gf-tag" + (cls ? " " + cls : ""), x)); });
    if (!(list || []).length) span.appendChild(el("span", "gf-muted", "-"));
    return span;
  }

  function renderFacts(it) {
    var dl = el("dl", "gf-facts");
    function row(label, node) {
      dl.appendChild(el("dt", null, label));
      var dd = el("dd");
      if (node instanceof Node) dd.appendChild(node); else dd.textContent = node === undefined || node === null || node === "" ? "-" : node;
      dl.appendChild(dd);
    }
    var geo = it.geo || null;
    var lieu = el("span", null, it.lieu || "-");
    if (geo) {
      var src = POS_LABELS[geo.source] || geo.source;
      var txt = " -> " + src + (geo.name ? " : " + geo.name : "") + (geo.layer_label ? " (" + geo.layer_label + ")" : "");
      lieu.appendChild(el("span", "gf-tag " + (geo.source === "gps" ? "gf-good" : (geo.confidence === "haute" ? "gf-good" : "gf-warn")), txt));
    } else {
      lieu.appendChild(el("span", "gf-tag gf-warn", "non localisee"));
    }
    row("Lieu", lieu);
    var cp = it.competence;
    if (cp) {
      var comp = el("span");
      comp.appendChild(el("span", "gf-comp-path", [cp.departement, cp.zone, cp.secteur].filter(Boolean).join(" > ") || "-"));
      if (cp.lot) comp.appendChild(el("span", "gf-tag", cp.lot));
      if ((cp.postes || []).length) comp.appendChild(el("div", "gf-muted", "postes du secteur : " + cp.postes.join(", ")));
      row("Competence", comp);
    } else if (it.zone) {
      row("Zone", it.zone.name);
    }
    if ((it.dans || []).length) {
      row("Dans", tags(it.dans.map(function (x) { return x.label + " : " + x.name; })));
    }
    if ((it.pres || []).length) {
      row("A proximite", tags(it.pres.map(function (x) { return x.label + " " + x.name + " (" + x.dist_m + " m)"; })));
    }
    var et = it.etapes || {}, de = it.delais || {};
    var heures = ["signalement", "engagement", "sur_place", "fin"].filter(function (k) { return et[k]; }).map(function (k) {
      var lab = { signalement: "signal.", engagement: "engag.", sur_place: "sur place", fin: "fin" }[k];
      return lab + " " + et[k] + (k !== "signalement" && de[k] !== null && de[k] !== undefined ? " (+" + de[k] + " min)" : "");
    });
    row("Heures", heures.length ? heures.join("  |  ") : "-");
    row("Equipement", it.equipement && it.equipement !== "aucun" ? it.equipement : "-");
    row("Moyens", tags(it.moyens_engages));
    row("Services ext.", tags(it.services_exterieurs));
    row("Causes", tags(it.causes));
    row("Issue codee", ISSUE_LABELS[it.issue_code] || it.issue_code || "-");
    if (it.nature) {
      row("Nature", (NATURE_LABELS[it.nature] || it.nature) + (it.activite ? " - " + it.activite.replace(/_/g, " ") : ""));
    }
    var tx = it.taxo || {};
    if (tx.sous_classification || it.sous_classification_proposee) {
      var scTxt = tx.sous_classification ? tx.sous_classification + (tx.sous_classification_officielle ? "" : " (hors liste)") : "-";
      if (!tx.sous_classification && it.sous_classification_proposee) scTxt = "proposee : " + it.sous_classification_proposee;
      row("Sous-classif.", scTxt);
    }
    if (tx.motif) row("Motif", tx.motif);
    if (tx.service) row("Service contacte", tx.service + (it.relances ? " (" + it.relances + " relance(s))" : ""));
    var b = it.ronde;
    if (b) {
      row("Ronde", [b.debut && b.fin ? b.debut + " - " + b.fin : null, b.resultat ? b.resultat.toUpperCase() : null,
        (b.points || []).length ? (b.points || []).length + " points : " + b.points.join(", ") : null,
        (b.anomalies || []).length ? "anomalies : " + b.anomalies.join(", ") : null].filter(Boolean).join("  |  "));
    }
    if (it.alarme) {
      var al = el("span", null, [it.alarme.action ? it.alarme.action.replace(/_/g, " ") : null, it.alarme.batiment,
        it.alarme.prestataire].filter(Boolean).join(" - "));
      if (it.alarme_non_remise) al.appendChild(el("span", "gf-tag gf-warn", " jamais remise en service"));
      row("Alarme", al);
    }
    if (it.dysfonctionnement) row("Dysfonct.", (it.dysfonctionnement.type || "") + " : " + (it.dysfonctionnement.description || ""));
    if (it.jalon) row("Jalon", [it.jalon.type ? it.jalon.type.replace(/_/g, " ") : null, it.jalon.heure, it.jalon.libelle].filter(Boolean).join(" - "));
    if (it.secours) {
      var s2 = it.secours;
      row("Secours", [s2.nature, s2.sexe !== "non_precise" ? s2.sexe : null, s2.tranche_age !== "non_precise" ? s2.tranche_age : null,
        s2.prise_en_charge ? "prise en charge : " + s2.prise_en_charge.replace(/_/g, " ") : null].filter(Boolean).join(" - "));
    }
    if (it.prise_service) {
      var p2 = it.prise_service;
      row("Prise de service", [p2.type, p2.poste, p2.debut && p2.fin ? p2.debut + " - " + p2.fin : (p2.debut || p2.fin),
        (p2.materiel || []).length ? "materiel : " + p2.materiel.join(", ") : null].filter(Boolean).join(" - "));
    }
    if ((it.plaques || []).length) row("Plaques", tags(it.plaques));
    if (it.doublon_declare) {
      var dd = el("span", null, "de la fiche n " + it.doublon_declare.sql_id + " ");
      if (it.doublon_declare.fiche) dd.appendChild(renderTwin(it.doublon_declare.fiche, false));
      row("Doublon declare", dd);
    }
    if ((it.donnees_personnelles || []).length) row("Donnees perso", tags(it.donnees_personnelles, "gf-warn"));
    if (it.completude) {
      row("Completude", it.completude.score + " / " + it.completude.max +
        ((it.completude.manquants || []).length ? " (manque : " + it.completude.manquants.join(", ") + ")" : ""));
    }
    return dl;
  }

  function renderFieldChips(it) {
    var box = el("div", "gf-fields");
    box.appendChild(el("span", "gf-muted", "Faux :"));
    CHAMPS.forEach(function (c) {
      if (CHAMPS_BLOCS.indexOf(c[0]) >= 0 && !it[c[0]]) return;          // bloc absent : rien a juger
      if (c[0] === "sous_classification" && !it.sous_classification_proposee) return;
      var b = el("button", "gf-chip", c[1]);
      b.type = "button";
      b.dataset.champ = c[0];
      b.addEventListener("click", function (ev) { ev.stopPropagation(); b.classList.toggle("gf-on"); });
      box.appendChild(b);
    });
    return box;
  }

  function renderDuplicate(it) {
    var box = el("div", "gf-dup");
    var dp = it.doublon_probable;
    var tf = dp.fiche || null;
    box.appendChild(el("span", null, "Doublon probable de la fiche " +
      (tf && tf.sql_id ? "n " + tf.sql_id : String(dp.de).slice(0, 8)) +
      " (score " + Math.round((dp.score || 0) * 100) + " %) : "));
    [["oui", "Oui"], ["non", "Non"]].forEach(function (o) {
      var b = el("button", "gf-chip" + (o[0] === "oui" ? " gf-yes" : ""), o[1]);
      b.type = "button";
      b.dataset.doublon = o[0];
      b.addEventListener("click", function (ev) {
        ev.stopPropagation();
        var on = !b.classList.contains("gf-on");
        box.querySelectorAll("[data-doublon]").forEach(function (x) { x.classList.remove("gf-on"); });
        if (on) b.classList.add("gf-on");
      });
      box.appendChild(b);
    });
    if (tf) box.appendChild(renderTwin(tf, false));
    return box;
  }

  // Fiche jumelle (doublon) lisible sur place : bouton qui deplie son contenu
  function renderTwin(f, open) {
    var wrap = el("div", "gf-twin");
    var btn = el("button", "gf-link", open ? "Masquer la fiche" : "Lire la fiche");
    btn.type = "button";
    var body = el("div", "gf-twin-body");
    body.hidden = !open;
    body.appendChild(el("div", "gf-meta", [f.date_local, f.time_local].filter(Boolean).join(" ") +
      "  |  " + (f.category || "sans categorie") + (f.sql_id ? "  |  n " + f.sql_id : "")));
    body.appendChild(el("div", "gf-title", f.text || "(sans titre)"));
    body.appendChild(el("div", "gf-deroule gf-open", f.comment || "(pas de deroule)"));
    btn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      body.hidden = !body.hidden;
      btn.textContent = body.hidden ? "Lire la fiche" : "Masquer la fiche";
    });
    wrap.appendChild(btn);
    wrap.appendChild(body);
    return wrap;
  }

  function paintReview(card, it) {
    var rv = it.review;
    var btns = card.querySelectorAll(".gf-gbtn");
    for (var i = 0; i < btns.length; i++) {
      var g = Number(btns[i].dataset.g);
      btns[i].classList.remove("gf-chosen", "gf-g1", "gf-g2", "gf-g3", "gf-g4", "gf-g5");
      if (rv && rv.gravite_validee === g) btns[i].classList.add("gf-chosen", "gf-g" + g);
    }
    var ignb = card.querySelector(".gf-ignore");
    if (ignb) ignb.classList.toggle("gf-on", !!(rv && rv.verdict === IGNORE));
    var cb = card.querySelector(".gf-inexact");
    var note = card.querySelector(".gf-note");
    var verdict = card.querySelector(".gf-verdict");
    if (cb) cb.checked = !!(rv && rv.resume_inexact);
    if (note && rv) note.value = rv.note || "";
    var faux = (rv && rv.champs_faux) || [];
    card.querySelectorAll("[data-champ]").forEach(function (b) {
      b.classList.toggle("gf-on", faux.indexOf(b.dataset.champ) >= 0);
    });
    card.querySelectorAll("[data-doublon]").forEach(function (b) {
      b.classList.toggle("gf-on", !!(rv && rv.doublon === b.dataset.doublon));
    });
    if (verdict) {
      if (!rv) { verdict.textContent = ""; verdict.className = "gf-verdict"; }
      else {
        var txt = { ok: "Juste", trop_haut: "Trop haut", trop_bas: "Trop bas", ignoree: "Ignoree (test / essai)" }[rv.verdict] || rv.verdict;
        verdict.textContent = txt + " - " + (rv.by || "");
        verdict.className = "gf-verdict " + (rv.verdict === "ok" ? "ok" : (rv.verdict === "ignoree" ? "" : "ko"));
      }
    }
    card.classList.toggle("gf-done", !!rv);
  }

  var IGNORE = "ignoree";     // fiche de test / essai (gravite_fiches.IGNORE)

  function review(idx, g) {
    var it = state.items[idx];
    if (!it || it.error) return;
    var card = document.querySelector('.gf-item[data-idx="' + idx + '"]');
    var verdict = g === IGNORE ? IGNORE : (g === it.gravite ? "ok" : (g < it.gravite ? "trop_haut" : "trop_bas"));
    var champs = [];
    var doublon = null;
    if (card) {
      card.querySelectorAll("[data-champ].gf-on").forEach(function (b) { champs.push(b.dataset.champ); });
      var d = card.querySelector("[data-doublon].gf-on");
      doublon = d ? d.dataset.doublon : null;
    }
    var payload = {
      verdict: verdict, gravite_validee: g, champs_faux: champs, doublon: doublon,
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
        if (isPhone()) setTimeout(exitReview, 1200);
      }
      loadSummary();
    }).catch(function (e) { toast("error", "Controle non enregistre : " + e.message); });
  }

  // Meme requete que gravite_fiches.css : ecran etroit OU tactile de faible
  // hauteur (iPhone en paysage, 844-932 px de large).
  var PHONE_QUERY = "(max-width: 760px), (pointer: coarse) and (max-height: 540px)";
  function isPhone() { return window.matchMedia(PHONE_QUERY).matches; }

  // Sur telephone, c'est .app-main qui defile (pas la page : barres fixes stables sur iOS)
  function scroller() {
    var m = document.querySelector(".app-main");
    return isPhone() && m ? m : null;
  }

  function scrollToTop() {
    var s = scroller();
    if (s) s.scrollTo({ top: 0, behavior: "smooth" });
    else window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function exitReview() {
    document.body.classList.remove("gf-reviewing");
    document.getElementById("gf-mobile-bar").hidden = true;
    scrollToTop();
    loadSummary();
  }

  function setFocus(idx, scroll) {
    var cards = document.querySelectorAll(".gf-item");
    if (!cards.length) return;
    idx = Math.max(0, Math.min(cards.length - 1, idx));
    for (var i = 0; i < cards.length; i++) cards[i].classList.toggle("gf-focus", i === idx);
    state.focus = idx;
    updateMobileBar();
    if (isPhone()) {
      // Une fiche a la fois : on remonte en haut de la fiche affichee (dans .app-main)
      var s = scroller();
      if (s) {
        var top = cards[idx].getBoundingClientRect().top - s.getBoundingClientRect().top + s.scrollTop - 10;
        s.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
      }
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

  // ---------- Telephone : barre d'onglets a icones ----------
  function setTab(target) {
    var tabs = document.querySelectorAll("#gf-tabbar [data-target]");
    for (var i = 0; i < tabs.length; i++) tabs[i].classList.toggle("gf-tab-on", tabs[i].dataset.target === target);
  }

  function initTabbar() {
    var bar = document.getElementById("gf-tabbar");
    if (!bar) return;
    bar.addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      if (b.dataset.action === "sample") {
        document.getElementById("gf-sample-grav").value = "";
        openSample();
        return;
      }
      var sec = document.getElementById(b.dataset.target);
      if (sec) {
        setTab(b.dataset.target);
        sec.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
    // L'onglet actif suit le bloc visible pendant le defilement
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) setTab(e.target.id); });
      }, { rootMargin: "-40% 0px -55% 0px" });
      ["gf-sec-suivi", "gf-sec-controle", "gf-sec-qualite", "gf-sec-lots"].forEach(function (id) {
        var s = document.getElementById(id);
        if (s) io.observe(s);
      });
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
    document.getElementById("gf-mb-done").addEventListener("click", exitReview);
    document.getElementById("gf-mb-ignore").addEventListener("click", function () { review(state.focus, IGNORE); });
    initTabbar();
    // Rotation du telephone : on recale la fiche courante (la barre change de cote)
    window.addEventListener("orientationchange", function () {
      setTimeout(function () { if (state.items.length) setFocus(state.focus, true); }, 300);
    });
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
