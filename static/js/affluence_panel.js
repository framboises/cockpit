/////////////////////////////////////////////////////////////////////////////////////////////////////
// PANEL ANALYSE AFFLUENCE - grand bloc central (equivalent du grand bloc Meteo)
/////////////////////////////////////////////////////////////////////////////////////////////////////

(function () {
  "use strict";

  var _prevView = "timeline";
  var _hourlyChart = null;
  var _fillChart = null;
  var _selectedDate = null;
  var _loading = false;

  // ── Helpers ──────────────────────────────────────────────────────────────

  function fmt(n) {
    if (n == null || isNaN(n)) return "--";
    return parseInt(n, 10).toLocaleString("fr-FR");
  }

  function fmtShort(n) {
    if (n == null || isNaN(n)) return "--";
    var v = parseInt(n, 10);
    if (v >= 10000) return (v / 1000).toFixed(1).replace(".", ",") + "k";
    if (v >= 1000) return (v / 1000).toFixed(2).replace(".", ",") + "k";
    return v.toString();
  }

  function fmtRange(low, high) {
    if (low == null && high == null) return "--";
    if (low == null || low === high) return fmt(high);
    if (high == null) return fmt(low);
    return fmtShort(low) + " - " + fmtShort(high);
  }

  function pct(a, b) {
    if (a == null || !b || b === 0) return null;
    return Math.round(((a - b) / b) * 100);
  }

  function hourLabel(ts) {
    if (!ts) return "";
    try {
      var d = new Date(ts);
      return d.getHours().toString().padStart(2, "0") + ":" +
             d.getMinutes().toString().padStart(2, "0");
    } catch (e) { return ts; }
  }

  function tsToMinutes(ts) {
    if (!ts) return null;
    try {
      var d = new Date(ts);
      return d.getHours() * 60 + d.getMinutes();
    } catch (e) { return null; }
  }

  function hourStringToMinutes(h) {
    if (!h || h.length < 4) return null;
    var hh = parseInt(h.slice(0, 2), 10);
    var mm = parseInt(h.slice(3, 5), 10) || 0;
    if (isNaN(hh)) return null;
    return hh * 60 + mm;
  }

  // Interpole la serie N-1 (points espaces, "HH:MM") sur les timestamps de la serie N.
  function alignN1(nSeries, n1Series) {
    if (!n1Series || !n1Series.length) return nSeries.map(function () { return null; });
    var pts = [];
    for (var i = 0; i < n1Series.length; i++) {
      var m = hourStringToMinutes(n1Series[i].hour);
      if (m != null) pts.push({ m: m, p: n1Series[i].present });
    }
    pts.sort(function (a, b) { return a.m - b.m; });
    if (!pts.length) return nSeries.map(function () { return null; });
    return nSeries.map(function (p) {
      var tm = tsToMinutes(p.ts);
      if (tm == null) return null;
      if (tm < pts[0].m || tm > pts[pts.length - 1].m) return null;
      for (var j = 0; j < pts.length - 1; j++) {
        var a = pts[j], b = pts[j + 1];
        if (a.m <= tm && tm <= b.m) {
          var r = (b.m === a.m) ? 0 : (tm - a.m) / (b.m - a.m);
          return Math.round(a.p + r * (b.p - a.p));
        }
      }
      return pts[pts.length - 1].p;
    });
  }

  function formatDate(d) {
    var y = d.getFullYear();
    var m = (d.getMonth() + 1).toString().padStart(2, "0");
    var dd = d.getDate().toString().padStart(2, "0");
    return y + "-" + m + "-" + dd;
  }

  function todayDate() {
    // Respecte TimelineClock.simulatedNow si dispo (mode simulation cockpit)
    if (window.TimelineClock && typeof window.TimelineClock.now === "function") {
      try { return new Date(window.TimelineClock.now()); } catch (e) {}
    }
    return new Date();
  }

  function destroyCharts() {
    if (_hourlyChart) { try { _hourlyChart.destroy(); } catch (e) {} _hourlyChart = null; }
    if (_fillChart)   { try { _fillChart.destroy();   } catch (e) {} _fillChart = null; }
  }

  // ── Ouverture / fermeture du panel ───────────────────────────────────────

  function openPanel(targetDate) {
    if (_loading) return;
    var panel = document.getElementById("affluence-panel");
    if (!panel) return;

    if (window.CockpitMapView) _prevView = window.CockpitMapView.currentView();

    // Fermer les autres grands panels actifs
    var others = [
      { panel: "meteo-panel",          btn: "meteo-expand-btn" },
      { panel: "counters-panel",       btn: "counters-expand-btn" },
      { panel: "pcorg-expanded-panel", btn: "pcorg-expand-btn" }
    ];
    others.forEach(function (o) {
      var p = document.getElementById(o.panel);
      if (p && p.style.display !== "none") {
        p.style.display = "none";
        var b = document.getElementById(o.btn);
        if (b) {
          var icon = b.querySelector(".material-symbols-outlined");
          if (icon) icon.textContent = "open_in_full";
        }
      }
    });

    var timeline = document.getElementById("timeline-main");
    var mapMain = document.getElementById("map-main");
    if (timeline) timeline.style.display = "none";
    if (mapMain) mapMain.style.display = "none";
    panel.style.display = "flex";

    var btn = document.getElementById("affluence-expand-btn");
    if (btn) {
      var icon = btn.querySelector(".material-symbols-outlined");
      if (icon) icon.textContent = "close_fullscreen";
    }

    var date = targetDate || _selectedDate || formatDate(todayDate());
    _selectedDate = date;
    // Les tabs sont construits dans loadPanelData() une fois qu'on a les jours
    // publics retournes par /get_affluence (auto-correction si la date demandee
    // n'est pas un jour public).
    loadPanelData(date);
  }

  function closePanel() {
    var panel = document.getElementById("affluence-panel");
    if (panel) panel.style.display = "none";
    destroyCharts();
    var btn = document.getElementById("affluence-expand-btn");
    if (btn) {
      var icon = btn.querySelector(".material-symbols-outlined");
      if (icon) icon.textContent = "open_in_full";
    }
    var timeline = document.getElementById("timeline-main");
    var mapMain = document.getElementById("map-main");
    if (_prevView === "map") {
      if (timeline) timeline.style.display = "none";
      if (mapMain) mapMain.style.display = "block";
    } else {
      if (timeline) timeline.style.display = "";
      if (mapMain) mapMain.style.display = "none";
    }
  }

  // ── Onglets : jours publics de l'event ──────────────────────────────────

  function buildTabs(days, activeDate) {
    var tabsDiv = document.getElementById("affluence-panel-tabs");
    if (!tabsDiv) return;
    tabsDiv.innerHTML = "";
    if (!days || !days.length) {
      var ph = document.createElement("span");
      ph.className = "affluence-empty-tabs";
      ph.textContent = "Aucun jour public configure";
      tabsDiv.appendChild(ph);
      return;
    }
    days.forEach(function (d) {
      var btn = document.createElement("button");
      btn.className = "meteo-panel-tab" + (d.date === activeDate ? " active" : "");
      btn.textContent = d.label || d.date;
      btn.setAttribute("data-date", d.date);
      btn.addEventListener("click", function () {
        var dd = this.getAttribute("data-date");
        tabsDiv.querySelectorAll(".meteo-panel-tab").forEach(function (t) { t.classList.remove("active"); });
        this.classList.add("active");
        _selectedDate = dd;
        loadPanelData(dd);
      });
      tabsDiv.appendChild(btn);
    });
  }

  // Retourne la "meilleure" date a selectionner par defaut :
  // 1) celle demandee si elle est dans les jours publics
  // 2) sinon aujourd'hui si public
  // 3) sinon le 1er jour public
  function pickDefaultDate(days, requested) {
    if (!days || !days.length) return requested;
    var has = function (d) { return days.some(function (x) { return x.date === d; }); };
    if (requested && has(requested)) return requested;
    var today = formatDate(todayDate());
    if (has(today)) return today;
    return days[0].date;
  }

  // ── Chargement des donnees ───────────────────────────────────────────────

  function loadPanelData(date) {
    var ev = window.selectedEvent;
    var yr = window.selectedYear;
    if (!ev || !yr) return;
    _loading = true;
    var qs = "event=" + encodeURIComponent(ev) + "&year=" + encodeURIComponent(yr);
    var urlGlobal = "/get_affluence?" + qs;
    var urlHourly = "/get_affluence_hourly?" + qs + "&date=" + encodeURIComponent(date);
    var urlCurves = "/get_affluence_curves?" + qs;

    Promise.all([
      fetch(urlGlobal, { cache: "no-store" }).then(function (r) { return r.json(); }),
      fetch(urlHourly, { cache: "no-store" }).then(function (r) { return r.json(); }),
      fetch(urlCurves, { cache: "no-store" }).then(function (r) { return r.json(); })
    ]).then(function (results) {
      var global = results[0] || {};
      var hourly = results[1] || {};
      var curves = results[2] || {};
      var days = global.days || [];

      // Auto-correction si la date demandee n'est pas un jour public
      var effectiveDate = pickDefaultDate(days, date);
      if (effectiveDate !== date && days.length) {
        _selectedDate = effectiveDate;
        _loading = false;
        // Refetch des donnees horaires (qui depend de la date)
        return loadPanelData(effectiveDate);
      }

      _loading = false;
      buildTabs(days, effectiveDate);
      renderUpdate(global);
      renderKpis(global);
      renderDayDetail(global, hourly, effectiveDate);
      renderDaysTable(global, effectiveDate);
      renderFillChart(curves);
      renderSites(global);
      renderAlerts(global);
    }).catch(function (err) {
      console.error("affluence_panel load error", err);
      _loading = false;
    });
  }

  // ── Renderers ────────────────────────────────────────────────────────────

  function renderUpdate(data) {
    var el = document.getElementById("affluence-panel-update");
    if (!el) return;
    var bits = [];
    if (data.last_update) bits.push("Maj billetterie : " + data.last_update.split("-").reverse().join("/"));
    if (data.prev_year) bits.push("N-1 : " + data.prev_year);
    el.textContent = bits.join("  -  ");
  }

  function makeKpiCard(label, value, sub, subClass) {
    var card = document.createElement("div");
    card.className = "affluence-kpi-card";
    var v = document.createElement("div");
    v.className = "affluence-kpi-val";
    v.textContent = value;
    var l = document.createElement("div");
    l.className = "affluence-kpi-lbl";
    l.textContent = label;
    card.appendChild(v);
    card.appendChild(l);
    if (sub) {
      var s = document.createElement("div");
      s.className = "affluence-kpi-sub " + (subClass || "");
      s.textContent = sub;
      card.appendChild(s);
    }
    return card;
  }

  function renderKpis(data) {
    var strip = document.getElementById("affluence-kpi-strip");
    if (!strip) return;
    strip.innerHTML = "";

    var totalN = data.total_ventes;
    var totalPrev = data.total_ventes_prev;
    var totalProj = data.total_projection;
    var totalProjLow = data.total_projection_low;
    var totalDelta = data.total_delta;

    // KPI 1 : Vendu N
    var subDelta = null, subDeltaClass = "";
    if (totalDelta != null && totalDelta !== 0) {
      subDelta = (totalDelta > 0 ? "+" : "") + fmt(totalDelta) + " depuis hier";
      subDeltaClass = totalDelta >= 0 ? "pos" : "neg";
    }
    strip.appendChild(makeKpiCard("Billets vendus (N)", fmt(totalN), subDelta, subDeltaClass));

    // KPI 2 : Vendu N-1 + delta %
    var deltaPct = pct(totalN, totalPrev);
    var sub2 = null, sub2Class = "";
    if (deltaPct != null) {
      sub2 = (deltaPct >= 0 ? "+" : "") + deltaPct + " % vs N-1";
      sub2Class = deltaPct >= 0 ? "pos" : "neg";
    }
    strip.appendChild(makeKpiCard("Vendus N-1 (meme avancement)", fmt(totalPrev), sub2, sub2Class));

    // KPI 3 : Projection finale (fourchette)
    var projVal = fmtRange(totalProjLow, totalProj);
    strip.appendChild(makeKpiCard("Projection finale", projVal, "fourchette basse - haute", ""));

    // KPI 4 : Pic maximal projete sur l'event (max sur tous les jours)
    var maxPic = null;
    (data.days || []).forEach(function (d) {
      var p = d.pic_projection || d.pic_prev;
      if (p && (maxPic == null || p > maxPic)) maxPic = p;
    });
    strip.appendChild(makeKpiCard("Pic max prevu", fmt(maxPic), "sur les jours publics", ""));
  }

  function frenchDayLabel(dateStr) {
    var JOURS = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];
    try {
      var d = new Date(dateStr + "T00:00:00");
      return JOURS[d.getDay()] + " " + d.toLocaleDateString("fr-FR", { day: "numeric", month: "long" });
    } catch (e) { return dateStr; }
  }

  function renderDayDetail(global, hourly, dateStr) {
    var title = document.getElementById("affluence-day-title");
    if (title) title.textContent = "Detail du jour : " + frenchDayLabel(dateStr);

    var cards = document.getElementById("affluence-day-cards");
    if (cards) {
      cards.innerHTML = "";
      var dayInfo = ((global.days) || []).find(function (d) { return d.date === dateStr; });
      if (!dayInfo) {
        var ph = document.createElement("div");
        ph.className = "affluence-empty";
        ph.textContent = "Jour non public ou pas de donnees";
        cards.appendChild(ph);
      } else {
        cards.appendChild(makeKpiCard("Billets vendus (J)", fmt(dayInfo.ventes), null, ""));
        cards.appendChild(makeKpiCard("Pic N-1 (jour eq.)", fmt(dayInfo.pic_prev),
                                      hourly.pic_prev_hour ? "vers " + hourly.pic_prev_hour : null, ""));
        cards.appendChild(makeKpiCard("Pic projete",
                                      fmtRange(dayInfo.pic_projection_low, dayInfo.pic_projection),
                                      "fourchette", ""));
        // Delta projection vs N-1
        var deltaPic = pct(dayInfo.pic_projection, dayInfo.pic_prev);
        var subDp = null, subDpClass = "";
        if (deltaPic != null) {
          subDp = (deltaPic >= 0 ? "+" : "") + deltaPic + " % vs N-1";
          subDpClass = deltaPic >= 0 ? "pos" : "neg";
        }
        cards.appendChild(makeKpiCard("Heure pic attendue",
                                      hourly.pic_prev_hour || "--",
                                      subDp, subDpClass));
      }
    }

    // Chart horaire : N + N-1 superposees
    var canvas = document.getElementById("affluence-hourly-chart");
    if (!canvas) return;
    if (_hourlyChart) { try { _hourlyChart.destroy(); } catch (e) {} _hourlyChart = null; }

    var nSeries = hourly.n || [];
    var n1Series = hourly.n1 || [];

    var labels, dataN, dataN1;
    if (nSeries.length > 0) {
      // On a une serie N : labels horaires N, on interpole N-1 dessus.
      labels = nSeries.map(function (p) { return hourLabel(p.ts); });
      dataN = nSeries.map(function (p) { return p.present; });
      dataN1 = alignN1(nSeries, n1Series);
    } else if (n1Series.length > 0) {
      // Pas de N : on affiche N-1 sur son propre axe.
      labels = n1Series.map(function (p) { return p.hour; });
      dataN = labels.map(function () { return null; });
      dataN1 = n1Series.map(function (p) { return p.present; });
    } else {
      labels = []; dataN = []; dataN1 = [];
    }

    _hourlyChart = new Chart(canvas, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "N (jour en cours)",
            data: dataN,
            borderColor: "#22c55e",
            backgroundColor: "rgba(34,197,94,0.18)",
            tension: 0.25,
            fill: true,
            pointRadius: 0,
            spanGaps: false
          },
          {
            label: "N-1 (jour equivalent)",
            data: dataN1,
            borderColor: "#94a3b8",
            backgroundColor: "rgba(148,163,184,0.10)",
            borderDash: [4, 4],
            tension: 0.25,
            fill: false,
            pointRadius: 0
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              label: function (c) { return c.dataset.label + " : " + fmt(c.parsed.y); }
            }
          }
        },
        scales: {
          y: { beginAtZero: true, ticks: { callback: function (v) { return fmt(v); } } },
          x: { ticks: { maxTicksLimit: 12 } }
        }
      }
    });
  }

  function renderDaysTable(global, activeDate) {
    var container = document.getElementById("affluence-days-table");
    if (!container) return;
    container.innerHTML = "";
    var days = global.days || [];
    if (!days.length) {
      var ph = document.createElement("div");
      ph.className = "affluence-empty";
      ph.textContent = "Pas de jours publics configures";
      container.appendChild(ph);
      return;
    }

    var table = document.createElement("table");
    table.className = "affluence-table";
    var thead = document.createElement("thead");
    var thr = document.createElement("tr");
    ["Jour", "Vendus N", "Δ jour", "Vendus N-1", "Δ % N-1", "Projection vente", "Pic N-1", "Pic projete"]
      .forEach(function (h) {
        var th = document.createElement("th");
        th.textContent = h;
        thr.appendChild(th);
      });
    thead.appendChild(thr);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    days.forEach(function (d) {
      var tr = document.createElement("tr");
      if (d.date === activeDate) tr.className = "active";

      function td(txt, cls) {
        var c = document.createElement("td");
        c.textContent = txt;
        if (cls) c.className = cls;
        return c;
      }

      tr.appendChild(td(d.label || d.date));
      tr.appendChild(td(fmt(d.ventes)));

      // Delta jour (vs hier)
      var deltaTxt = "--", deltaCls = "";
      if (d.delta != null && d.delta !== 0) {
        deltaTxt = (d.delta > 0 ? "+" : "") + fmt(d.delta);
        deltaCls = d.delta >= 0 ? "affluence-delta-pos" : "affluence-delta-neg";
      } else if (d.delta === 0) {
        deltaTxt = "0";
      }
      tr.appendChild(td(deltaTxt, deltaCls));

      tr.appendChild(td(fmt(d.ventes_prev)));

      // Δ % N-1
      var dPct = pct(d.ventes, d.ventes_prev);
      var dPctTxt = "--", dPctCls = "";
      if (dPct != null) {
        dPctTxt = (dPct >= 0 ? "+" : "") + dPct + " %";
        dPctCls = dPct >= 0 ? "affluence-delta-pos" : "affluence-delta-neg";
      }
      tr.appendChild(td(dPctTxt, dPctCls));

      tr.appendChild(td(fmtRange(d.projection_low, d.projection)));
      tr.appendChild(td(fmt(d.pic_prev)));
      tr.appendChild(td(fmtRange(d.pic_projection_low, d.pic_projection)));

      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.appendChild(table);
  }

  function renderFillChart(curves) {
    var canvas = document.getElementById("affluence-fill-chart");
    if (!canvas) return;
    if (_fillChart) { try { _fillChart.destroy(); } catch (e) {} _fillChart = null; }

    var n = curves.n || [];
    var n1 = curves.n_minus_1 || [];
    var n2 = curves.n_minus_2 || [];

    // Axe X : ensemble des d_before vus, tries decroissant (du plus eloigne vers J-0)
    var labelSet = {};
    [n, n1, n2].forEach(function (serie) {
      serie.forEach(function (p) { labelSet[p.d_before] = true; });
    });
    var labels = Object.keys(labelSet).map(function (k) { return parseInt(k, 10); }).sort(function (a, b) { return b - a; });

    function buildData(serie) {
      var map = {};
      serie.forEach(function (p) { map[p.d_before] = p.ventes; });
      return labels.map(function (d) { return map[d] != null ? map[d] : null; });
    }

    var datasets = [];
    if (n.length) datasets.push({
      label: "N (" + (curves.year_n || "courante") + ")",
      data: buildData(n),
      borderColor: "#22c55e",
      backgroundColor: "rgba(34,197,94,0.12)",
      borderWidth: 2.5,
      tension: 0.18,
      fill: false,
      pointRadius: 2,
      spanGaps: true
    });
    if (n1.length) datasets.push({
      label: "N-1 (" + (curves.year_n1 || "?") + ")",
      data: buildData(n1),
      borderColor: "#3b82f6",
      borderDash: [6, 4],
      borderWidth: 2,
      tension: 0.18,
      fill: false,
      pointRadius: 0,
      spanGaps: true
    });
    if (n2.length) datasets.push({
      label: "N-2 (" + (curves.year_n2 || "?") + ")",
      data: buildData(n2),
      borderColor: "#cbd5e1",
      borderDash: [2, 3],
      borderWidth: 1.5,
      tension: 0.18,
      fill: false,
      pointRadius: 0,
      spanGaps: true
    });

    if (!datasets.length) {
      var ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#94a3b8";
      ctx.font = "13px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Pas d'historique de courbes disponible", canvas.width / 2, canvas.height / 2);
      return;
    }

    _fillChart = new Chart(canvas, {
      type: "line",
      data: { labels: labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              title: function (items) {
                if (!items.length) return "";
                var d = items[0].label;
                return "J - " + d + " jours";
              },
              label: function (c) { return c.dataset.label + " : " + fmt(c.parsed.y) + " billets"; }
            }
          }
        },
        scales: {
          y: { beginAtZero: true, ticks: { callback: function (v) { return fmt(v); } },
               title: { display: true, text: "Billets vendus cumules" } },
          x: { reverse: false,
               ticks: { maxTicksLimit: 14,
                        callback: function (v, i, ticks) {
                          var lbl = labels[i];
                          return lbl === 0 ? "J-0" : "J-" + lbl;
                        } },
               title: { display: true, text: "Jours avant la course" } }
        }
      }
    });
  }

  function renderSites(data) {
    var container = document.getElementById("affluence-sites-list");
    if (!container) return;
    container.innerHTML = "";
    var sites = data.sites || [];
    if (!sites.length) {
      var ph = document.createElement("div");
      ph.className = "affluence-empty";
      ph.textContent = "Aucun site (parking/camping) avec billetterie";
      container.appendChild(ph);
      return;
    }
    sites.forEach(function (s) {
      var row = document.createElement("div");
      row.className = "affluence-site-row";

      var name = document.createElement("div");
      name.className = "affluence-site-name";
      name.textContent = s.name;
      row.appendChild(name);

      var stats = document.createElement("div");
      stats.className = "affluence-site-stats";

      var capacity = s.capacite || 0;
      var ventes = s.ventes || 0;
      var ratio = capacity > 0 ? Math.min(100, Math.round(100 * ventes / capacity)) : 0;
      var ratioProj = (capacity > 0 && s.projection) ? Math.min(100, Math.round(100 * s.projection / capacity)) : null;

      function statSpan(label, val) {
        var sp = document.createElement("span");
        sp.className = "affluence-site-stat";
        sp.innerHTML = "<em>" + label + "</em> " + val;
        return sp;
      }

      stats.appendChild(statSpan("Capa.", fmt(capacity)));
      stats.appendChild(statSpan("Vendus", fmt(ventes)));
      if (s.ventes_prev != null) {
        var dPct = pct(ventes, s.ventes_prev);
        var pctTxt = dPct == null ? "" : " (" + (dPct >= 0 ? "+" : "") + dPct + " %)";
        stats.appendChild(statSpan("N-1", fmt(s.ventes_prev) + pctTxt));
      }
      if (s.projection != null) {
        stats.appendChild(statSpan("Proj.", fmt(s.projection)));
      }
      row.appendChild(stats);

      // Jauge horizontale
      var gaugeWrap = document.createElement("div");
      gaugeWrap.className = "affluence-site-gauge";
      var fill = document.createElement("div");
      fill.className = "affluence-site-gauge-fill";
      fill.style.width = ratio + "%";
      // Couleur selon taux
      if (ratio >= 90) fill.classList.add("hot");
      else if (ratio >= 70) fill.classList.add("warm");
      gaugeWrap.appendChild(fill);
      if (ratioProj != null && ratioProj > ratio) {
        var mark = document.createElement("div");
        mark.className = "affluence-site-gauge-mark";
        mark.style.left = ratioProj + "%";
        mark.title = "Projection : " + ratioProj + " % de la capacite";
        gaugeWrap.appendChild(mark);
      }
      var pctLabel = document.createElement("span");
      pctLabel.className = "affluence-site-gauge-pct";
      pctLabel.textContent = ratio + " %";
      gaugeWrap.appendChild(pctLabel);
      row.appendChild(gaugeWrap);

      container.appendChild(row);
    });
  }

  function renderAlerts(data) {
    var container = document.getElementById("affluence-alerts");
    if (!container) return;
    container.innerHTML = "";

    var alerts = [];

    // 1. Capacite site potentiellement depassee par la projection
    (data.sites || []).forEach(function (s) {
      if (s.capacite && s.projection && s.projection > s.capacite) {
        var over = s.projection - s.capacite;
        alerts.push({
          level: "critical",
          icon: "warning",
          text: "Site " + s.name + " : projection " + fmt(s.projection) +
                " > capacite " + fmt(s.capacite) + " (depassement " + fmt(over) + ")"
        });
      } else if (s.capacite && s.projection && s.projection / s.capacite >= 0.9) {
        alerts.push({
          level: "warning",
          icon: "warning",
          text: "Site " + s.name + " : projection a " +
                Math.round(100 * s.projection / s.capacite) + " % de capacite"
        });
      }
    });

    // 2. Croissance forte vs N-1
    if (data.total_ventes != null && data.total_ventes_prev != null && data.total_ventes_prev > 0) {
      var pct = Math.round(100 * (data.total_ventes - data.total_ventes_prev) / data.total_ventes_prev);
      if (pct >= 20) {
        alerts.push({
          level: "info",
          icon: "trending_up",
          text: "Croissance billetterie globale : +" + pct + " % vs N-1 (vigilance flux/parkings)"
        });
      } else if (pct <= -15) {
        alerts.push({
          level: "info",
          icon: "trending_down",
          text: "Repli billetterie : " + pct + " % vs N-1"
        });
      }
    }

    // 3. Donnees obsoletes
    if (data.last_update) {
      try {
        var last = new Date(data.last_update + "T00:00:00");
        var now = todayDate();
        var diff = Math.floor((now - last) / (1000 * 60 * 60 * 24));
        if (diff >= 2) {
          alerts.push({
            level: "warning",
            icon: "schedule",
            text: "Donnees billetterie datees du " + data.last_update +
                  " (" + diff + " jours, verifier la synchro Skidata)"
          });
        }
      } catch (e) {}
    }

    if (!alerts.length) {
      var ok = document.createElement("div");
      ok.className = "affluence-alert affluence-alert-ok";
      ok.innerHTML = '<span class="material-symbols-outlined">check_circle</span>' +
                     '<span>Aucune alerte : projections sous capacite, donnees a jour</span>';
      container.appendChild(ok);
      return;
    }

    alerts.forEach(function (a) {
      var row = document.createElement("div");
      row.className = "affluence-alert affluence-alert-" + a.level;
      row.innerHTML = '<span class="material-symbols-outlined">' + a.icon + '</span>' +
                      '<span>' + a.text + '</span>';
      container.appendChild(row);
    });
  }

  // ── Wiring DOM ───────────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("affluence-expand-btn");
    if (btn) {
      btn.addEventListener("click", function (e) {
        // Empeche la propagation pour ne pas declencher un changement d'onglet du widget
        e.stopPropagation();
        var panel = document.getElementById("affluence-panel");
        if (panel && panel.style.display !== "none") closePanel();
        else openPanel();
      });
    }
    var closeBtn = document.getElementById("affluence-panel-close");
    if (closeBtn) closeBtn.addEventListener("click", closePanel);
  });

  // Expose pour debug console
  window.AffluencePanel = { open: openPanel, close: closePanel };

})();
