/////////////////////////////////////////////////////////////////////////////////////////////////////
// DASHBOARD CONTROLE D'ACCES - panel plein ecran
/////////////////////////////////////////////////////////////////////////////////////////////////////

(function () {
  "use strict";

  var _prevView = "timeline";
  var _mainChart = null;
  var _zoneCharts = [];
  var _loading = false;
  var _selectedDate = null;  // YYYY-MM-DD ; null = aujourd'hui

  function fmt(n) {
    if (n == null || isNaN(n)) return "--";
    return parseInt(n, 10).toLocaleString("fr-FR");
  }

  function pct(a, b) {
    if (!b || b === 0) return null;
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
    try { var d = new Date(ts); return d.getHours() * 60 + d.getMinutes(); }
    catch (e) { return null; }
  }

  function hourStringToMinutes(h) {
    if (!h || h.length < 4) return null;
    var hh = parseInt(h.slice(0, 2), 10);
    var mm = parseInt(h.slice(3, 5), 10) || 0;
    if (isNaN(hh)) return null;
    return hh * 60 + mm;
  }

  // Interpole la serie N-1 horaire (points espaces) sur une echelle fine.
  // Retourne un tableau de meme longueur que nSeries, chaque valeur = present N-1
  // interpole a la minute du point N correspondant.
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
      // Hors plage horaire connue : null (Chart.js saute)
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

  function dayHourLabel(ts) {
    if (!ts) return "";
    try {
      var d = new Date(ts);
      var J = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];
      return J[d.getDay()] + " " + d.getHours().toString().padStart(2, "0") + "h";
    } catch (e) { return ts; }
  }

  function destroyCharts() {
    if (_mainChart) { _mainChart.destroy(); _mainChart = null; }
    _zoneCharts.forEach(function (c) { try { c.destroy(); } catch(e){} });
    _zoneCharts = [];
  }

  function openPanel() {
    if (_loading) return;
    var panel = document.getElementById("counters-panel");
    if (!panel) return;

    if (window.CockpitMapView) _prevView = window.CockpitMapView.currentView();

    var pcorg = document.getElementById("pcorg-expanded-panel");
    if (pcorg && pcorg.style.display !== "none") {
      pcorg.style.display = "none";
      var pb = document.getElementById("pcorg-expand-btn");
      if (pb) pb.querySelector(".material-symbols-outlined").textContent = "open_in_full";
    }
    var meteo = document.getElementById("meteo-panel");
    if (meteo && meteo.style.display !== "none") {
      meteo.style.display = "none";
      var mb = document.getElementById("meteo-expand-btn");
      if (mb) mb.querySelector(".material-symbols-outlined").textContent = "open_in_full";
    }

    var timeline = document.getElementById("timeline-main");
    var mapMain = document.getElementById("map-main");
    if (timeline) timeline.style.display = "none";
    if (mapMain) mapMain.style.display = "none";
    panel.style.display = "flex";

    var btn = document.getElementById("counters-expand-btn");
    if (btn) btn.querySelector(".material-symbols-outlined").textContent = "close_fullscreen";

    loadDashboard();
  }

  function closePanel() {
    var panel = document.getElementById("counters-panel");
    if (panel) panel.style.display = "none";
    destroyCharts();
    _selectedDate = null;
    var btn = document.getElementById("counters-expand-btn");
    if (btn) btn.querySelector(".material-symbols-outlined").textContent = "open_in_full";

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

  function loadDashboard() {
    var ev = window.selectedEvent;
    var yr = window.selectedYear;
    if (!ev || !yr) return;
    _loading = true;
    var url = "/api/live-controle/dashboard?event=" + encodeURIComponent(ev) +
              "&year=" + encodeURIComponent(yr);
    if (_selectedDate) url += "&date=" + encodeURIComponent(_selectedDate);
    url += "&_=" + Date.now();  // cache-buster
    console.log("[dashboard] fetch", url);
    fetch(url, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        console.log("[dashboard] target_date=" + data.target_date + " principal pts=" +
                    (data.zones && data.zones[0] ? data.zones[0].series.length : "?"));
        renderDashboard(data);
        _loading = false;
      })
      .catch(function (err) {
        console.error("dashboard error", err);
        _loading = false;
      });
  }

  function renderDashboard(data) {
    destroyCharts();
    var prevYearEl = document.getElementById("counters-panel-prev-year");
    if (prevYearEl) {
      var bits = [];
      if (data.prev_year) bits.push("N-1 : " + data.prev_year);
      if (data.target_date) bits.push("Jour : " + data.target_date.split("-").reverse().join("/"));
      prevYearEl.textContent = bits.join("  -  ");
    }

    var zones = data.zones || [];
    var principal = zones.find(function (z) { return z.is_principal; }) || zones[0];
    var others = zones.filter(function (z) { return z !== principal; });

    renderPrincipal(principal, data.n1_series_today || []);
    renderDaysGrid(data.days_summary || [], data.target_date);
    renderOtherZones(others);
  }

  function renderPrincipal(z, n1Series) {
    var nameEl = document.getElementById("counters-dash-principal-name");
    var kpisEl = document.getElementById("counters-dash-principal-kpis");
    if (!z) {
      if (nameEl) nameEl.textContent = "Aucune zone configuree";
      if (kpisEl) kpisEl.innerHTML = "";
      return;
    }
    if (nameEl) nameEl.textContent = z.name + (z.is_principal ? "" : " (zone 1)");

    if (kpisEl) {
      kpisEl.innerHTML = "";
      var kpi = function (label, val, sub) {
        var b = document.createElement("div");
        b.className = "counters-kpi";
        var v = document.createElement("div");
        v.className = "counters-kpi-val";
        v.textContent = fmt(val);
        b.appendChild(v);
        var l = document.createElement("div");
        l.className = "counters-kpi-lbl";
        l.textContent = label;
        b.appendChild(l);
        if (sub != null) {
          var s = document.createElement("div");
          s.className = "counters-kpi-sub " + (sub >= 0 ? "pos" : "neg");
          s.textContent = (sub >= 0 ? "+" : "") + sub + "% vs N-1";
          b.appendChild(s);
        }
        return b;
      };
      kpisEl.appendChild(kpi("Actuellement", z.current));
      kpisEl.appendChild(kpi("Pic du jour", z.pic_today, pct(z.pic_today, z.pic_n1_same_day)));
      if (z.pic_n1_same_day != null) kpisEl.appendChild(kpi("Pic N-1 jour", z.pic_n1_same_day));
      if (z.max_n1_season != null) kpisEl.appendChild(kpi("Max N-1 saison", z.max_n1_season));
    }

    // Chart principal : courbe du jour (cible) + courbe N-1 alignee par interpolation
    var ctx = document.getElementById("counters-main-chart");
    if (!ctx) return;
    var labels = (z.series || []).map(function (p) { return hourLabel(p.ts); });
    var data = (z.series || []).map(function (p) { return p.present; });
    var n1Data = alignN1(z.series || [], n1Series);

    _mainChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Jour selectionne",
            data: data,
            borderColor: "#22c55e",
            backgroundColor: "rgba(34,197,94,0.15)",
            tension: 0.25,
            fill: true,
            pointRadius: 0,
          },
          {
            label: "N-1 (jour equivalent)",
            data: n1Data,
            borderColor: "#94a3b8",
            borderDash: [4, 4],
            tension: 0.25,
            fill: false,
            pointRadius: 0,
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        plugins: {
          legend: { position: "bottom" },
          tooltip: { callbacks: { label: function (c) { return c.dataset.label + " : " + fmt(c.parsed.y); } } }
        },
        scales: {
          y: { beginAtZero: true, ticks: { callback: function (v) { return fmt(v); } } },
          x: { ticks: { maxTicksLimit: 12 } }
        }
      }
    });
  }

  function renderDaysGrid(days, targetDate) {
    var grid = document.getElementById("counters-days-grid");
    if (!grid) return;
    grid.innerHTML = "";
    if (!days.length) {
      var e = document.createElement("div");
      e.className = "counters-empty";
      e.textContent = "Pas de jours publics configures";
      grid.appendChild(e);
      return;
    }
    days.forEach(function (d) {
      var card = document.createElement("div");
      var isSelected = d.date === targetDate;
      card.className = "counters-day-card clickable" +
                       (d.is_today ? " today" : "") +
                       (isSelected ? " selected" : "");
      card.title = "Cliquer pour afficher la courbe du " + d.label;
      card.addEventListener("click", function () {
        _selectedDate = d.date;
        loadDashboard();
      });
      var lbl = document.createElement("div");
      lbl.className = "counters-day-lbl";
      lbl.textContent = d.label;
      card.appendChild(lbl);

      var row = document.createElement("div");
      row.className = "counters-day-row";
      var c1 = document.createElement("div");
      c1.className = "counters-day-val";
      c1.textContent = fmt(d.pic_n);
      var c1l = document.createElement("div");
      c1l.className = "counters-day-sub";
      c1l.textContent = "Pic " + (d.is_today ? "jour" : "N");
      var col1 = document.createElement("div");
      col1.appendChild(c1); col1.appendChild(c1l);

      var c2 = document.createElement("div");
      c2.className = "counters-day-val n1";
      c2.textContent = fmt(d.pic_n1);
      var c2l = document.createElement("div");
      c2l.className = "counters-day-sub";
      c2l.textContent = "Pic N-1";
      var col2 = document.createElement("div");
      col2.appendChild(c2); col2.appendChild(c2l);

      row.appendChild(col1);
      row.appendChild(col2);
      card.appendChild(row);

      if (d.pic_n != null && d.pic_n1 != null && d.pic_n1 > 0) {
        var delta = pct(d.pic_n, d.pic_n1);
        var pill = document.createElement("span");
        pill.className = "counters-day-pill " + (delta >= 0 ? "pos" : "neg");
        pill.textContent = (delta >= 0 ? "+" : "") + delta + "%";
        card.appendChild(pill);
      }
      grid.appendChild(card);
    });
  }

  function renderOtherZones(zones) {
    var cont = document.getElementById("counters-zones-scroll");
    if (!cont) return;
    cont.innerHTML = "";
    if (!zones.length) {
      var e = document.createElement("div");
      e.className = "counters-empty";
      e.textContent = "Aucune autre zone";
      cont.appendChild(e);
      return;
    }
    zones.forEach(function (z, idx) {
      var card = document.createElement("div");
      card.className = "counters-zone-card";

      var h = document.createElement("div");
      h.className = "counters-zone-head";
      var n = document.createElement("div");
      n.className = "counters-zone-name";
      n.textContent = z.name;
      h.appendChild(n);
      var cur = document.createElement("div");
      cur.className = "counters-zone-cur";
      cur.textContent = fmt(z.current);
      h.appendChild(cur);
      card.appendChild(h);

      var stats = document.createElement("div");
      stats.className = "counters-zone-stats";
      var stat = function (lbl, val) {
        var s = document.createElement("span");
        s.innerHTML = "<em>" + lbl + "</em> " + fmt(val);
        return s;
      };
      stats.appendChild(stat("Pic jour", z.pic_today));
      if (z.pic_n1_same_day != null) stats.appendChild(stat("N-1 jour", z.pic_n1_same_day));
      if (z.max_n1_season != null) stats.appendChild(stat("Max N-1", z.max_n1_season));
      card.appendChild(stats);

      var cvWrap = document.createElement("div");
      cvWrap.className = "counters-zone-chart-wrap";
      var cv = document.createElement("canvas");
      cvWrap.appendChild(cv);
      card.appendChild(cvWrap);
      cont.appendChild(card);

      var labels = (z.series || []).map(function (p) { return dayHourLabel(p.ts); });
      var data = (z.series || []).map(function (p) { return p.present; });
      var ch = new Chart(cv, {
        type: "line",
        data: {
          labels: labels,
          datasets: [{
            data: data,
            borderColor: "#3b82f6",
            backgroundColor: "rgba(59,130,246,0.18)",
            tension: 0.2,
            fill: true,
            pointRadius: 0,
            borderWidth: 1.5,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: function (c) { return fmt(c.parsed.y); } } }
          },
          scales: {
            y: { display: false, beginAtZero: true },
            x: { ticks: { maxTicksLimit: 5, font: { size: 9 } }, grid: { display: false } }
          }
        }
      });
      _zoneCharts.push(ch);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("counters-expand-btn");
    if (btn) {
      btn.addEventListener("click", function () {
        var panel = document.getElementById("counters-panel");
        if (panel && panel.style.display !== "none") closePanel();
        else openPanel();
      });
    }
    var closeBtn = document.getElementById("counters-panel-close");
    if (closeBtn) closeBtn.addEventListener("click", closePanel);
  });

})();
