// === Trafic — widget "Temps d'acces" avec onglets, filtre, expand, cible carte ===

(function(){
  if (!window.isBlockAllowed("widget-parkings")) return;

  // --- Utils DOM ---
  function $(id){ return document.getElementById(id); }
  function on(el, evt, fn){ if(el) el.addEventListener(evt, fn, false); }

  // --- Helpers format ---
  function formatTime(seconds){
    if(seconds == null || isNaN(seconds)) return "--";
    seconds = Math.max(0, seconds|0);
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    if(m) return m + "m " + (s < 10 ? "0" : "") + s + "s";
    return s + "s";
  }

  function formatDelay(seconds){
    var s = Math.max(0, seconds|0);
    if(s === 0) return "+0s";
    var m = Math.floor(s / 60);
    var r = s % 60;
    if(m && r) return "+" + m + "m " + r + "s";
    if(m) return "+" + m + "m";
    return "+" + r + "s";
  }

  function levelClass(sev){
    switch(sev){
      case 0: return "level-better";
      case 1: return "level-normal";
      case 2: return "level-busy";
      case 3: return "level-heavy";
      case 4: return "level-gridlock";
      default: return "level-normal";
    }
  }

  function severityColor(sev){
    switch(sev){
      case 0: return "#22c55e";
      case 1: return "#22c55e";
      case 2: return "#eab308";
      case 3: return "#f97316";
      case 4: return "#ef4444";
      default: return "#22c55e";
    }
  }

  function dirLabel(d){
    if(d === "in") return "ENTREE";
    if(d === "out") return "SORTIE";
    return "";
  }

  function tagLabel(tag){
    if(tag === "I" || tag === "O" || tag === "neutral") return "";
    if(tag === "P") return "fork_right";  // icon name
    if(tag === "security") return "SECURITE";
    if(tag === "free") return "LIBRE";
    return "";
  }

  function isTagIcon(tag){
    return tag === "P";
  }

  // --- State ---
  var allRoutes = [];
  var currentTab = "trafic-complet";
  var filterText = "";

  // --- Tab logic (reuse pattern from affluence widget) ---
  var widget = $("widget-parkings");
  if(!widget) return;

  var tabButtons = widget.querySelectorAll(".widget-tab");
  var tabContents = widget.querySelectorAll(".widget-tab-content");

  tabButtons.forEach(function(btn){
    on(btn, "click", function(){
      var tab = btn.getAttribute("data-tab");
      currentTab = tab;
      tabButtons.forEach(function(b){ b.classList.toggle("active", b === btn); });
      tabContents.forEach(function(c){ c.classList.toggle("active", c.getAttribute("data-tab") === tab); });
      renderAll();
    });
  });

  // --- Filter ---
  var filterInput = $("trafic-filter-input");
  on(filterInput, "input", function(){
    filterText = (filterInput.value || "").toLowerCase();
    renderAll();
  });

  // (renderPill removed — replaced by inline elements in createRow)

  // --- Render detail panel for a single route ---
  function createDetailPanel(r){
    var panel = document.createElement("div");
    panel.className = "detail-panel";

    var delta = r.deltaSeconds;
    var ratio = r.ratio;

    var grid = document.createElement("div");
    grid.className = "detail-grid";

    var items = [
      {label: "Temps actuel", value: formatTime(r.currentTime)},
      {label: "Temps historique", value: formatTime(r.historicTime)},
      {label: "Retard", value: delta != null ? formatDelay(delta) : "--"},
      {label: "Ratio", value: ratio != null ? ratio.toFixed(2) + "x" : "--"},
      {label: "Statut", value: r.status || "--"},
      {label: "Direction", value: dirLabel(r.direction) || "--"},
    ];

    for(var j = 0; j < items.length; j++){
      var item = document.createElement("div");
      item.className = "detail-item";
      var lbl = document.createElement("span");
      lbl.className = "detail-label";
      lbl.textContent = items[j].label;
      var val = document.createElement("span");
      val.className = "detail-value";
      val.textContent = items[j].value;
      item.appendChild(lbl);
      item.appendChild(val);
      grid.appendChild(item);
    }
    panel.appendChild(grid);

    // Target button (only if we have line coords)
    if(r.line && r.line.length > 0){
      var btn = document.createElement("button");
      btn.className = "btn-target";
      btn.type = "button";
      var icon = document.createElement("span");
      icon.className = "material-symbols-outlined";
      icon.textContent = "map";
      btn.appendChild(icon);
      btn.appendChild(document.createTextNode(" Voir sur la carte"));
      on(btn, "click", function(e){
        e.stopPropagation();
        window._targetRoute = {
          line: r.line,
          name: r.terrain,
          severity: r.severity,
          status: r.status,
          currentTime: r.currentTime,
          historicTime: r.historicTime,
          delta: r.deltaSeconds,
          ratio: r.ratio
        };
        if(window.CockpitMapView && window.CockpitMapView.switchView){
          window.CockpitMapView.switchView("map");
          setTimeout(function(){
            document.dispatchEvent(new CustomEvent("drawTargetRoute"));
          }, 400);
        }
      });
      panel.appendChild(btn);
    }

    return panel;
  }

  // --- Render one row for a single route ---
  function createRow(r){
    var delay = Math.max(0, (r.currentTime|0) - (r.historicTime|0));
    var sev = r.severity || 0;

    var row = document.createElement("div");
    row.className = "parking-row sev-" + sev;

    var summary = document.createElement("div");
    summary.className = "row-summary";

    // Name + tag
    var nameDiv = document.createElement("div");
    nameDiv.className = "name";
    nameDiv.textContent = r.terrain;
    var tag = tagLabel(r.tag);
    if(tag){
      if(isTagIcon(r.tag)){
        var tagIcon = document.createElement("span");
        tagIcon.className = "material-symbols-outlined tag-icon";
        tagIcon.textContent = tag;
        nameDiv.appendChild(tagIcon);
      } else {
        var tagSpan = document.createElement("span");
        tagSpan.className = "tag-label";
        tagSpan.textContent = tag;
        nameDiv.appendChild(tagSpan);
      }
    }

    // Direction label
    var dir = dirLabel(r.direction);
    if(dir){
      var dirEl = document.createElement("span");
      dirEl.className = "dir-label";
      dirEl.textContent = dir;
      summary.appendChild(dirEl);
    }

    // Time — big readable
    var timeEl = document.createElement("span");
    timeEl.className = "route-time";
    timeEl.textContent = formatTime(r.currentTime);

    // Delay badge
    var delayEl = document.createElement("span");
    delayEl.className = "route-delay " + levelClass(sev);
    delayEl.textContent = formatDelay(delay);

    summary.insertBefore(nameDiv, summary.firstChild);
    summary.appendChild(timeEl);
    summary.appendChild(delayEl);
    row.appendChild(summary);

    row.appendChild(createDetailPanel(r));

    on(row, "click", function(e){
      if(e.target.closest(".btn-target")) return;
      var expanded = widget.querySelectorAll(".parking-row.expanded");
      for(var i = 0; i < expanded.length; i++){
        if(expanded[i] !== row) expanded[i].classList.remove("expanded");
      }
      row.classList.toggle("expanded");
    });

    return row;
  }

  // --- Criticite = ratio (temps actuel / historique) ---
  function routeRatio(r){
    return r.ratio || (r.historicTime > 0 ? (r.currentTime / r.historicTime) : 1);
  }

  // --- Sort state per tab: {key: "name"|"time"|"delay", asc: true|false} ---
  var sortState = {};

  function filterRoutes(routes, tab){
    var filtered = routes;
    if(tab === "trafic-pkg"){
      filtered = routes.filter(function(r){ return r.category === "pkg_aa" && r.tag !== "P"; });
    } else if(tab === "trafic-autoroute"){
      filtered = routes.filter(function(r){ return r.tag === "P"; });
    } else if(tab === "trafic-secu"){
      filtered = routes.filter(function(r){ return r.category === "security"; });
    }
    if(filterText){
      filtered = filtered.filter(function(r){
        return (r.terrain || "").toLowerCase().indexOf(filterText) >= 0;
      });
    }
    return filtered;
  }

  function sortRoutes(routes, state){
    var sorted = routes.slice();
    var key = state.key;
    var asc = state.asc;
    sorted.sort(function(a, b){
      var cmp = 0;
      if(key === "time"){
        cmp = (a.currentTime || 0) - (b.currentTime || 0);
      } else if(key === "delay"){
        cmp = routeRatio(a) - routeRatio(b);
      } else {
        cmp = (a.terrain || "").localeCompare(b.terrain || "", "fr");
      }
      return asc ? cmp : -cmp;
    });
    return sorted;
  }

  function createSortBar(containerId){
    var bar = document.createElement("div");
    bar.className = "trafic-sort-bar";

    var state = sortState[containerId] || {key: "name", asc: true};
    var buttons = [
      {key: "name", icon: "sort_by_alpha", title: "Trier par nom"},
      {key: "time", icon: "schedule", title: "Trier par duree de trajet"},
      {key: "delay", icon: "trending_up", title: "Trier par ratio retard / temps moyen"},
    ];

    for(var i = 0; i < buttons.length; i++){
      (function(b){
        var isActive = state.key === b.key;
        var btn = document.createElement("button");
        btn.className = "trafic-sort-btn" + (isActive ? " active" : "");
        btn.title = b.title;
        var ico = document.createElement("span");
        ico.className = "material-symbols-outlined";
        ico.textContent = b.icon;
        btn.appendChild(ico);
        // Arrow indicator
        if(isActive){
          var arrow = document.createElement("span");
          arrow.className = "material-symbols-outlined sort-arrow";
          arrow.textContent = state.asc ? "arrow_upward" : "arrow_downward";
          btn.appendChild(arrow);
        }
        on(btn, "click", function(e){
          e.stopPropagation();
          if(state.key === b.key){
            state.asc = !state.asc;
          } else {
            state.key = b.key;
            state.asc = true;
          }
          sortState[containerId] = state;
          renderAll();
        });
        bar.appendChild(btn);
      })(buttons[i]);
    }
    return bar;
  }

  // Bouton carte actif en cours (pour desactiver l'ancien au clic d'un autre)
  var _activeMapBtn = null;

  function createMapOverlayBtn(routes){
    var btn = document.createElement("button");
    btn.className = "trafic-sort-btn";
    btn.title = "Afficher ces routes sur la carte";
    var ico = document.createElement("span");
    ico.className = "material-symbols-outlined";
    ico.textContent = "map";
    btn.appendChild(ico);
    on(btn, "click", function(e){
      e.stopPropagation();

      // Toggle : si ce bouton est deja actif, on efface et on desactive
      if(btn.classList.contains("active")){
        btn.classList.remove("active");
        _activeMapBtn = null;
        if(window.CockpitMapView && window.CockpitMapView.clearAllRoutes){
          window.CockpitMapView.clearAllRoutes();
        } else {
          document.dispatchEvent(new CustomEvent("clearAllRoutes"));
        }
        return;
      }

      var withLines = routes.filter(function(r){ return r.line && r.line.length > 0; });
      if(!withLines.length) return;

      // Desactiver l'ancien bouton actif s'il existe
      if(_activeMapBtn && _activeMapBtn !== btn){
        _activeMapBtn.classList.remove("active");
      }

      btn.classList.add("active");
      _activeMapBtn = btn;

      window._allRoutesData = withLines;
      if(window.CockpitMapView && window.CockpitMapView.switchView){
        window.CockpitMapView.switchView("map");
        setTimeout(function(){
          document.dispatchEvent(new CustomEvent("drawAllRoutes"));
        }, 400);
      }
    });
    return btn;
  }

  function renderTab(containerId, routes){
    var container = $(containerId);
    if(!container) return;
    container.textContent = "";

    if(!routes.length){
      var empty = document.createElement("div");
      empty.className = "muted";
      empty.style.padding = "12px";
      empty.style.fontSize = "0.82rem";
      empty.textContent = "Aucune route";
      container.appendChild(empty);
      return;
    }

    // Top 3 by ratio (criticite), only those above normal (ratio > 1.2)
    var byRatio = routes.slice().sort(function(a, b){ return routeRatio(b) - routeRatio(a); });
    var top3 = byRatio.slice(0, 3).filter(function(r){ return routeRatio(r) > 1.2; });
    var top3Ids = {};
    for(var t = 0; t < top3.length; t++) top3Ids[top3[t].terrain + "|" + top3[t].direction] = true;

    if(top3.length){
      var topHeader = document.createElement("div");
      topHeader.className = "trafic-list-header";
      var topLabel = document.createElement("span");
      topLabel.className = "trafic-section-label";
      topLabel.textContent = "Plus critiques";
      topHeader.appendChild(topLabel);
      topHeader.appendChild(createMapOverlayBtn(top3));
      container.appendChild(topHeader);
      for(var t2 = 0; t2 < top3.length; t2++){
        var row = createRow(top3[t2]);
        row.classList.add("top-critical");
        container.appendChild(row);
      }
    }

    // Sort bar + rest (excluding top 3)
    var rest = routes.filter(function(r){
      return !top3Ids[r.terrain + "|" + r.direction];
    });

    var state = sortState[containerId] || {key: "name", asc: true};
    var sorted = sortRoutes(rest, state);

    var listHeader = document.createElement("div");
    listHeader.className = "trafic-list-header";
    var listLabel = document.createElement("span");
    listLabel.className = "trafic-section-label";
    listLabel.textContent = "Tous";
    var listRight = document.createElement("div");
    listRight.style.cssText = "display:flex;align-items:center;gap:4px;";
    listRight.appendChild(createMapOverlayBtn(routes));
    listRight.appendChild(createSortBar(containerId));
    listHeader.appendChild(listLabel);
    listHeader.appendChild(listRight);
    container.appendChild(listHeader);

    for(var i = 0; i < sorted.length; i++){
      container.appendChild(createRow(sorted[i]));
    }
  }

  function renderAll(){
    // Le re-render detruit les boutons DOM, on reset la ref
    _activeMapBtn = null;

    var tabs = [
      {id: "trafic-list-complet", tab: "trafic-complet"},
      {id: "trafic-list-pkg", tab: "trafic-pkg"},
      {id: "trafic-list-autoroute", tab: "trafic-autoroute"},
      {id: "trafic-list-secu", tab: "trafic-secu"},
    ];
    for(var i = 0; i < tabs.length; i++){
      var t = tabs[i];
      var filtered = filterRoutes(allRoutes, t.tab);
      renderTab(t.id, filtered);
    }
  }

  // --- Fetch data ---
  function updateAllRoutes(){
    fetch("/trafic/all_routes")
      .then(function(r){ return r.json(); })
      .then(function(data){
        allRoutes = Array.isArray(data.routes) ? data.routes : [];
        renderAll();
      })
      .catch(function(err){ console.error("Erreur trafic all_routes:", err); });
  }

  // --- Init ---
  document.addEventListener("DOMContentLoaded", function(){
    updateAllRoutes();
    setInterval(updateAllRoutes, 30000);
  });

})();
