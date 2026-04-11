/////////////////////////////////////////////////////////////////////////////////////////////////////
// CONTROLE D'ACCES — Widget compteurs enrichi
/////////////////////////////////////////////////////////////////////////////////////////////////////

(function () {
  "use strict";

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  var countersBody = document.getElementById("hsh-counters-body");
  var totalCurrentEl = document.getElementById("hsh-total-current");
  var totalCapacityEl = document.getElementById("hsh-total-capacity");
  var capacityLabel = document.getElementById("hsh-capacity-label");
  var healthDot = document.getElementById("hsh-health-dot");
  var lastUpdateEl = document.getElementById("hsh-last-update");

  // Projection elements
  var projBlock = document.getElementById("hsh-projection-block");
  var progressFill = document.getElementById("hsh-progress-fill");
  var progressMark = document.getElementById("hsh-progress-mark");
  var progressPct = document.getElementById("hsh-progress-pct");
  var progressPic = document.getElementById("hsh-progress-pic");
  var n1Text = document.getElementById("hsh-n1-text");

  if (!countersBody) return;

  var latestTotalCurrent = 0;

  function gaugeColor(pct) {
    if (pct >= 90) return "#ef4444";
    if (pct >= 70) return "#ff9800";
    return "#4caf50";
  }

  function formatNum(v) {
    var n = parseInt(v, 10);
    if (isNaN(n)) return "--";
    return n.toLocaleString("fr-FR");
  }

  function renderCounters(counters) {
    while (countersBody.firstChild) {
      countersBody.removeChild(countersBody.firstChild);
    }

    if (!counters || counters.length === 0) {
      var nd = el("div", "hsh-no-data");
      nd.textContent = "Aucun compteur configure";
      countersBody.appendChild(nd);
      totalCurrentEl.textContent = "--";
      latestTotalCurrent = 0;
      return;
    }

    var totalCurrent = 0;

    counters.forEach(function (c) {
      var current = parseInt(c.current, 10) || 0;
      var entries = parseInt(c.entries, 10) || 0;
      var exits = parseInt(c.exits, 10) || 0;
      var locked = c.locked && c.locked !== "0";

      totalCurrent += current;

      var card = el("div", "hsh-counter-card");

      // Header : nom + verrou
      var header = el("div", "hsh-counter-header");
      var nameSpan = el("span", "hsh-counter-name");
      nameSpan.textContent = c.location_name || c.counter_name || ("Location " + c.location_id);
      header.appendChild(nameSpan);

      if (locked) {
        var lockIcon = el("span", "material-symbols-outlined hsh-counter-locked");
        lockIcon.textContent = "lock";
        lockIcon.title = "Verrouille";
        header.appendChild(lockIcon);
      }
      card.appendChild(header);

      // Stats : E / S / P
      var stats = el("div", "hsh-counter-stats");
      [["E", entries], ["S", exits], ["P", current]].forEach(function (pair) {
        var sp = el("span");
        sp.textContent = pair[0] + ": ";
        var b = el("strong");
        b.textContent = formatNum(pair[1]);
        sp.appendChild(b);
        stats.appendChild(sp);
      });
      card.appendChild(stats);

      countersBody.appendChild(card);
    });

    totalCurrentEl.textContent = formatNum(totalCurrent);
    latestTotalCurrent = totalCurrent;
  }

  function loadCounters() {
    if (typeof window.isBlockAllowed === "function" && !window.isBlockAllowed("widget-counters")) return;

    fetch("/api/live-controle/counters")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderCounters(data);
        loadCountersContext();
      })
      .catch(function () {});
  }

  function loadCountersContext() {
    if (!projBlock) return;
    var ev = window.selectedEvent;
    var yr = window.selectedYear;
    if (!ev || !yr) {
      projBlock.style.display = "none";
      // Fallback : afficher capacite totale si pas de contexte
      if (capacityLabel) capacityLabel.textContent = "Capacite totale";
      totalCapacityEl.textContent = "--";
      return;
    }

    fetch("/api/live-controle/counters-context?event=" + encodeURIComponent(ev) + "&year=" + encodeURIComponent(yr))
      .then(function (r) { return r.json(); })
      .then(function (ctx) {
        if (!ctx || (!ctx.pic_projection && !ctx.no_data)) {
          projBlock.style.display = "none";
          if (capacityLabel) capacityLabel.textContent = "Capacite totale";
          totalCapacityEl.textContent = "--";
          return;
        }

        // Pas de donnees historiques pour cette date
        if (ctx.no_data) {
          projBlock.style.display = "";
          progressFill.style.width = "0%";
          if (progressMark) progressMark.style.display = "none";
          progressPct.textContent = ctx.message || "Pas de donnees N-1";
          progressPic.textContent = "";
          n1Text.textContent = ctx.hint || "";
          if (capacityLabel) capacityLabel.textContent = "Pic projete";
          totalCapacityEl.textContent = "--";
          return;
        }

        // Mettre a jour le chiffre de droite : pic projete
        totalCapacityEl.textContent = formatNum(ctx.pic_projection);
        if (capacityLabel) {
          capacityLabel.textContent = ctx.mode === "projected" ? "Pic projete" : "Pic N-1";
        }

        // Barre de progression
        projBlock.style.display = "";
        var pct = ctx.pic_projection > 0 ? Math.round(latestTotalCurrent / ctx.pic_projection * 100) : 0;
        var color = gaugeColor(pct);

        if (pct > 100 && progressMark) {
          // Depassement : barre pleine, marqueur a la position 100%
          progressFill.style.width = "100%";
          progressFill.style.background = color;
          var markPos = Math.round(100 / pct * 100);
          progressMark.style.display = "";
          progressMark.style.left = markPos + "%";
        } else {
          progressFill.style.width = Math.max(pct, 0) + "%";
          progressFill.style.background = color;
          if (progressMark) progressMark.style.display = "none";
        }

        var picLabel = ctx.mode === "projected" ? "Pic proj. " : "Pic N-1 : ";
        progressPct.textContent = pct + "% du pic " + (ctx.mode === "projected" ? "projete" : "N-1");
        progressPic.textContent = picLabel + formatNum(ctx.pic_projection);

        // N-1 meme heure
        if (ctx.present_n1 != null) {
          var yearLabel = ctx.prev_year || "N-1";
          var suffix = ctx.mode === "projected" ? " (proj.)" : "";
          n1Text.textContent = yearLabel + " meme heure" + suffix + " : " + formatNum(ctx.present_n1);
        } else {
          n1Text.textContent = "";
        }
      })
      .catch(function () {
        projBlock.style.display = "none";
      });
  }

  var widget = document.getElementById("widget-counters");

  function loadStatus() {
    fetch("/api/live-controle/status")
      .then(function (r) { return r.json(); })
      .then(function (s) {
        // Masquer le widget si le live controle n'est pas active
        if (widget) widget.style.display = s.live_controle_actif ? "" : "none";

        var color = "#999";
        var title = "Inactif";
        if (s.health === "ok") { color = "#4caf50"; title = "OK"; }
        else if (s.health === "warning") { color = "#ff9800"; title = "Cycle en retard"; }
        else if (s.health === "waiting") { color = "#2196f3"; title = "En attente"; }
        healthDot.style.background = color;
        healthDot.title = title;

        if (s.dernier_cycle && s.age_seconds !== null && s.age_seconds !== undefined) {
          var min = Math.floor(s.age_seconds / 60);
          lastUpdateEl.textContent = "maj il y a " + min + " min";
        } else if (s.live_controle_actif) {
          lastUpdateEl.textContent = "en attente...";
        } else {
          lastUpdateEl.textContent = "";
        }
      })
      .catch(function () {});
  }

  window.updateGlobalCounter = function () {
    loadCounters();
    loadStatus();
  };

  loadCounters();
  loadStatus();

  setInterval(function () {
    loadCounters();
    loadStatus();
  }, 120000);

})();
