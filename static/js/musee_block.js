/////////////////////////////////////////////////////////////////////////////////////////////////////
// MUSEE - bloc autonome de la page d'accueil (visiteurs du jour)
// Source : GET /api/musee/state (musee_api.py), alimente par scripts/musee_collect.py.
// Independant du live controle et de Waze : visible meme si live_controle_actif = false.
/////////////////////////////////////////////////////////////////////////////////////////////////////

(function () {
  "use strict";

  var BLOCK_ID = "widget-musee";
  var POLL_MS = 60000;

  var widget = document.getElementById(BLOCK_ID);
  var body = document.getElementById("musee-body");
  var statutEl = document.getElementById("musee-statut");
  var majEl = document.getElementById("musee-maj");
  var mbTab = document.getElementById("mb-tab-musee");
  if (!widget || !body) return;

  if (typeof window.isBlockAllowed === "function" && !window.isBlockAllowed(BLOCK_ID)) {
    widget.remove();
    if (mbTab) mbTab.remove();
    return;
  }

  // Une disposition de groupe enregistree avant l'existence du bloc ne le
  // cite pas : on le range sous le Controle d'acces plutot qu'en tete.
  (function placeBlock() {
    var layout = window.__blockLayout;
    if (!layout) return;
    var ids = (layout.left || []).concat(layout.right || []);
    if (ids.indexOf(BLOCK_ID) >= 0) return;
    var counters = document.getElementById("widget-counters");
    if (counters && counters.parentNode) {
      counters.parentNode.insertBefore(widget, counters.nextSibling);
    }
  })();

  var chart = null;
  var timer = null;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  function fmt(n) {
    if (n === null || n === undefined || isNaN(n)) return "--";
    return Number(n).toLocaleString("fr-FR");
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }

  function ageLabel(s) {
    if (s === null || s === undefined) return "aucun releve";
    if (s < 90) return "maj il y a " + Math.max(1, Math.round(s / 60)) + " min";
    if (s < 3600) return "maj il y a " + Math.round(s / 60) + " min";
    return "maj il y a " + Math.round(s / 3600) + " h";
  }

  function compareLine(label, c) {
    if (!c) return null;
    var line = el("div", "musee-comp");
    line.appendChild(el("span", "musee-comp-label", label));
    var v = el("span", "musee-comp-val");
    v.textContent = fmt(c.a_meme_heure) + " a la meme heure";
    if (c.total !== null && c.total !== undefined && c.total !== c.a_meme_heure) {
      v.textContent += " (" + fmt(c.total) + " sur la journee)";
    }
    line.appendChild(v);
    return line;
  }

  function sourceLabel(d) {
    if (d.source === "compteur") return "compteur depuis l'ouverture";
    if (d.source === "transactions") return "passages valides de la journee";
    if (d.source === "compteur_partiel") return "compteur depuis " + (d.partiel_depuis || "?") + " seulement";
    if (d.source === "transactions_partiel") return "passages depuis " + (d.partiel_depuis || "?") + " seulement";
    return "";
  }

  function renderChart(canvas, rows) {
    if (typeof Chart === "undefined") return;
    var labels = rows.map(function (r) { return r.heure + "h"; });
    var data = rows.map(function (r) { return r.n; });
    var color = cssVar("--brand", "#6366f1");
    var muted = cssVar("--muted", "#888");
    if (chart) { chart.destroy(); chart = null; }
    chart = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: { labels: labels, datasets: [{ label: "Visiteurs", data: data,
        backgroundColor: color, borderRadius: 3, maxBarThickness: 22 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: function (ctx) { return fmt(ctx.parsed.y) + " visiteurs"; } } } },
        scales: {
          x: { grid: { display: false }, ticks: { color: muted, font: { size: 10 } } },
          y: { beginAtZero: true, grid: { color: "rgba(127,127,127,0.15)" },
               ticks: { color: muted, font: { size: 10 }, precision: 0 } }
        }
      }
    });
  }

  function render(d) {
    clear(body);

    if (statutEl) {
      statutEl.textContent = d.statut_libelle || "";
      statutEl.className = "musee-statut musee-statut-" + (d.statut || "ferme");
    }
    if (majEl) {
      majEl.textContent = d.releve_perime ? "releve perime" : ageLabel(d.age_releve_s);
      majEl.classList.toggle("musee-perime", !!d.releve_perime);
      majEl.title = d.dernier_releve_heure ? "Dernier releve : " + d.dernier_releve_heure : "Aucun releve";
    }

    // Chiffre principal : jamais 0 par defaut, "--" sans donnee.
    var top = el("div", "hsh-summary musee-summary");
    var left = el("div");
    var val = el("div", "hsh-summary-value", d.visiteurs === null || d.visiteurs === undefined ? "--" : fmt(d.visiteurs));
    if (d.releve_perime) val.classList.add("musee-val-perime");
    left.appendChild(val);
    left.appendChild(el("div", "hsh-summary-label", "Visiteurs aujourd'hui"));
    var src = sourceLabel(d);
    if (src) left.appendChild(el("div", "hsh-summary-sub", src));
    top.appendChild(left);

    var right = el("div");
    right.appendChild(el("div", "hsh-summary-value", d.pic_heure ? d.pic_heure.heure + "h" : "--"));
    right.appendChild(el("div", "hsh-summary-label",
      d.pic_heure ? "Heure de pointe (" + fmt(d.pic_heure.n) + ")" : "Heure de pointe"));
    top.appendChild(right);
    body.appendChild(top);

    if (d.releve_perime) {
      var warn = el("div", "musee-warn");
      warn.textContent = "Releve perime" + (d.dernier_releve_heure ? " (dernier : " + d.dernier_releve_heure + ")" : "") +
        (d.derniere_erreur ? " - " + d.derniere_erreur : "");
      body.appendChild(warn);
    }

    var comps = el("div", "musee-comps");
    var c1 = compareLine("S-1", d.comparaisons && d.comparaisons.semaine_derniere);
    var c2 = compareLine("N-1", d.comparaisons && d.comparaisons.n_1);
    if (c1) comps.appendChild(c1);
    if (c2) comps.appendChild(c2);
    if (comps.firstChild) body.appendChild(comps);

    var rows = d.par_heure || [];
    var hasCurve = rows.some(function (r) { return r.n > 0; });
    if (hasCurve) {
      var wrap = el("div", "musee-chart");
      var canvas = document.createElement("canvas");
      wrap.appendChild(canvas);
      body.appendChild(wrap);
      renderChart(canvas, rows);
    } else if (chart) {
      chart.destroy(); chart = null;
    }

    var cps = (d.par_checkpoint || []).filter(function (c) { return c.n > 0 || !c.mobile; });
    if (cps.length) {
      var list = el("div", "musee-cps");
      var total = cps.reduce(function (a, c) { return a + (c.n || 0); }, 0);
      cps.forEach(function (c) {
        var row = el("div", "musee-cp");
        row.appendChild(el("span", "musee-cp-nom", c.nom + (c.mobile ? " (mobile)" : "")));
        var bar = el("span", "musee-cp-bar");
        var fill = el("span", "musee-cp-fill");
        fill.style.width = (total > 0 ? Math.round(c.n / total * 100) : 0) + "%";
        bar.appendChild(fill);
        row.appendChild(bar);
        row.appendChild(el("span", "musee-cp-n", fmt(c.n)));
        list.appendChild(row);
      });
      body.appendChild(list);
    }

    var hj = d.horaires_jour || {};
    var horaires = hj.ferme
      ? "Ferme aujourd'hui" + (hj.libelle ? " (" + hj.libelle + ")" : "")
      : "Ouvert " + (d.ouverture || "").replace(":", "h") + " - " + (d.fermeture || "").replace(":", "h") +
        (hj.source === "exception" && hj.libelle ? " (" + hj.libelle + ")" : "");
    body.appendChild(el("div", "musee-foot",
      horaires + " - compteurs Handshake " + (d.area_nom || "")));
  }

  function renderAConfigurer(d) {
    clear(body);
    if (statutEl) {
      statutEl.textContent = d.statut_libelle || "A configurer";
      statutEl.className = "musee-statut musee-statut-a_configurer";
    }
    if (majEl) { majEl.textContent = ""; majEl.classList.remove("musee-perime"); }
    var msg = el("div", "hsh-no-data", "Compteur du musee a configurer");
    body.appendChild(msg);
    if (window.__userIsAdmin === true || window.__userIsAdmin === "true") {
      var a = el("a", null, "Controle d'acces > onglet Musee");
      a.href = "/live-controle#musee";
      var p = el("div", "musee-foot");
      p.appendChild(a);
      body.appendChild(p);
    }
  }

  function load() {
    fetch("/api/musee/state", { credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 403) { widget.style.display = "none"; if (mbTab) mbTab.style.display = "none"; return null; }
        return r.json();
      })
      .then(function (d) {
        if (!d) return;
        if (d.enabled === false) {
          widget.style.display = "none";
          if (mbTab) mbTab.style.display = "none";
          return;
        }
        widget.style.display = "";
        if (mbTab) mbTab.style.display = "";
        if (!d.ok) {
          clear(body);
          body.appendChild(el("div", "hsh-no-data", "Etat du musee indisponible"));
          return;
        }
        if (d.configure === false) { renderAConfigurer(d); return; }
        render(d);
      })
      .catch(function () {
        if (majEl) { majEl.textContent = "releve indisponible"; majEl.classList.add("musee-perime"); }
      });
  }

  function start() {
    load();
    if (timer) clearInterval(timer);
    timer = setInterval(function () {
      if (document.hidden) return;
      load();
    }, POLL_MS);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) load();
    });  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
