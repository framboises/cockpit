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
  var noData = document.getElementById("hsh-no-data");
  var totalCurrentEl = document.getElementById("hsh-total-current");
  var totalCapacityEl = document.getElementById("hsh-total-capacity");
  var healthDot = document.getElementById("hsh-health-dot");
  var lastUpdateEl = document.getElementById("hsh-last-update");

  if (!countersBody) return;

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
    // Vider le container
    while (countersBody.firstChild) {
      countersBody.removeChild(countersBody.firstChild);
    }

    if (!counters || counters.length === 0) {
      var nd = el("div", "hsh-no-data");
      nd.textContent = "Aucun compteur configure";
      countersBody.appendChild(nd);
      totalCurrentEl.textContent = "--";
      totalCapacityEl.textContent = "--";
      return;
    }

    var totalCurrent = 0;
    var totalCapacity = 0;

    counters.forEach(function (c) {
      var current = parseInt(c.current, 10) || 0;
      var upper = parseInt(c.upper_limit, 10) || 0;
      var entries = parseInt(c.entries, 10) || 0;
      var exits = parseInt(c.exits, 10) || 0;
      var locked = c.locked && c.locked !== "0";
      var pct = upper > 0 ? Math.round((current / upper) * 100) : 0;

      totalCurrent += current;
      totalCapacity += upper;

      // Card
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
      var se = el("span");
      se.textContent = "E: ";
      var seB = el("strong");
      seB.textContent = formatNum(entries);
      se.appendChild(seB);
      stats.appendChild(se);

      var ss = el("span");
      ss.textContent = "S: ";
      var ssB = el("strong");
      ssB.textContent = formatNum(exits);
      ss.appendChild(ssB);
      stats.appendChild(ss);

      var sp = el("span");
      sp.textContent = "P: ";
      var spB = el("strong");
      spB.textContent = formatNum(current);
      sp.appendChild(spB);
      stats.appendChild(sp);

      card.appendChild(stats);

      // Gauge
      if (upper > 0) {
        var gauge = el("div", "hsh-gauge");
        var fill = el("div", "hsh-gauge-fill");
        fill.style.width = Math.min(pct, 100) + "%";
        fill.style.background = gaugeColor(pct);
        gauge.appendChild(fill);
        card.appendChild(gauge);

        var info = el("div", "hsh-gauge-info");
        var pctSpan = el("span");
        pctSpan.textContent = pct + "%";
        info.appendChild(pctSpan);
        var capSpan = el("span");
        capSpan.textContent = formatNum(upper);
        info.appendChild(capSpan);
        card.appendChild(info);
      }

      countersBody.appendChild(card);
    });

    totalCurrentEl.textContent = formatNum(totalCurrent);
    totalCapacityEl.textContent = formatNum(totalCapacity);
  }

  function loadCounters() {
    if (typeof window.isBlockAllowed === "function" && !window.isBlockAllowed("widget-counters")) return;

    fetch("/api/live-controle/counters")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderCounters(data);
      })
      .catch(function () {
        // Fallback silencieux
      });
  }

  function loadStatus() {
    fetch("/api/live-controle/status")
      .then(function (r) { return r.json(); })
      .then(function (s) {
        // Health dot
        var color = "#999";
        var title = "Inactif";
        if (s.health === "ok") { color = "#4caf50"; title = "OK"; }
        else if (s.health === "warning") { color = "#ff9800"; title = "Cycle en retard"; }
        else if (s.health === "waiting") { color = "#2196f3"; title = "En attente"; }
        healthDot.style.background = color;
        healthDot.title = title;

        // Last update
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

  // Fonction globale pour compatibilite avec main.js
  window.updateGlobalCounter = function () {
    loadCounters();
    loadStatus();
  };

  // Init
  loadCounters();
  loadStatus();

  // Auto-refresh toutes les 2 minutes
  setInterval(function () {
    loadCounters();
    loadStatus();
  }, 120000);

})();
