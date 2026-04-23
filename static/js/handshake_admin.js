(function () {
  "use strict";

  if (!document.getElementById("hsh-tree")) return;

  var csrfToken = function () {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  };
  var jsonHeaders = function () {
    return { "Content-Type": "application/json", "X-CSRFToken": csrfToken() };
  };

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  // --- DOM refs ---
  var toggleEl = document.getElementById("hsh-actif-toggle");
  var eventSelect = document.getElementById("hsh-event-select");
  var forceBtn = document.getElementById("hsh-force-inventory");
  var statusText = document.getElementById("hsh-status-text");
  var healthDot = document.getElementById("hsh-admin-health");
  var treeContainer = document.getElementById("hsh-tree");
  var saveLocBtn = document.getElementById("hsh-save-locations");
  var errorsTbody = document.getElementById("hsh-errors-tbody");
  var liveCounters = document.getElementById("hsh-live-counters");
  var filterInput = document.getElementById("hsh-tree-filter");
  var forceTxBtn = document.getElementById("hsh-force-tx");
  var forceTxDays = document.getElementById("hsh-force-tx-days");
  var errorsFilterEvent = document.getElementById("hsh-errors-filter-event");

  var activeContainer = document.getElementById("hsh-active-checkpoints");
  var activeCountEl = document.getElementById("hsh-active-count");
  var titresTbody = document.getElementById("hsh-titres-tbody");
  var debitTbody = document.getElementById("hsh-debit-tbody");

  var config = {};
  var structure = [];
  var selectedLocations = [];
  var collapsedNodes = {};  // _id -> true si replie

  // =========================================================
  //  LOAD
  // =========================================================

  function loadEvents() {
    fetch("/get_events")
      .then(function (r) { return r.json(); })
      .then(function (events) {
        eventSelect.textContent = "";
        var def = document.createElement("option");
        def.value = "";
        def.textContent = "-- Evenement --";
        eventSelect.appendChild(def);
        (events || []).forEach(function (ev) {
          var name = ev.nom || ev;
          var opt = document.createElement("option");
          opt.value = name;
          opt.textContent = name;
          eventSelect.appendChild(opt);
        });
        if (config.evenement) eventSelect.value = config.evenement;
      });
  }

  function loadConfig() {
    fetch("/api/live-controle/config")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        config = d || {};
        toggleEl.checked = !!config.live_controle_actif;
        if (config.evenement) eventSelect.value = config.evenement;
        selectedLocations = (config.locations_selectionnees || []).slice();
        loadStatus();
        loadStructure();
        loadErrors();
        loadLiveCounters();
        loadActiveCheckpoints();
        loadTitresLive();
        loadDebitGates();
      });
  }

  function loadStatus() {
    fetch("/api/live-controle/status")
      .then(function (r) { return r.json(); })
      .then(function (s) {
        var parts = [];
        if (s.dernier_cycle) {
          var age = s.age_seconds;
          if (age !== null && age !== undefined) {
            parts.push("Dernier cycle : il y a " + Math.floor(age / 60) + " min");
          }
        } else if (s.live_controle_actif) {
          parts.push("En attente du premier cycle...");
        }
        if (s.dernier_inventaire) parts.push("Inventaire OK");
        if (s.nb_locations > 0) parts.push(s.nb_locations + " location(s)");
        statusText.textContent = parts.join("  \u00b7  ");

        var color = "#999"; var title = "Inactif";
        if (s.health === "ok") { color = "#4caf50"; title = "OK"; }
        else if (s.health === "warning") { color = "#ff9800"; title = "Cycle en retard"; }
        else if (s.health === "waiting") { color = "#2196f3"; title = "En attente"; }
        healthDot.style.background = color;
        healthDot.title = title;
      });
  }

  function loadStructure() {
    var url = "/api/live-controle/structure";
    if (config.evenement) url += "?evenement=" + encodeURIComponent(config.evenement);
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (docs) {
        structure = docs || [];
        renderTree();
      });
  }

  function loadErrors() {
    var url = "/api/live-controle/errors";
    var filterByEvent = errorsFilterEvent && errorsFilterEvent.checked;
    if (filterByEvent && config.evenement) url += "?evenement=" + encodeURIComponent(config.evenement);
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (errors) {
        errorsTbody.textContent = "";
        var shown = (errors || []).slice(0, 20);
        if (shown.length === 0) {
          var tr = el("tr");
          var td = el("td");
          td.setAttribute("colspan", "5");
          td.style.cssText = "color:var(--muted);text-align:center;padding:16px;";
          td.textContent = "Aucune erreur";
          tr.appendChild(td);
          errorsTbody.appendChild(tr);
          return;
        }
        shown.forEach(function (e) {
          var tr = el("tr");
          var cpName = (e.checkpoint && (e.checkpoint.Name || e.checkpoint.name)) || "?";
          var typeLabel = e.type_scan === "vehicule" ? "Veh." : e.type_scan === "enfant" ? "Enf." : e.type_scan === "accredite" ? "Accred." : "Pers.";
          [e.date_paris || "", cpName, e.status_label || e.status || "", e.direction || "", typeLabel].forEach(function (txt) {
            var td = el("td");
            td.textContent = txt;
            tr.appendChild(td);
          });
          errorsTbody.appendChild(tr);
        });
      });
  }

  function loadActiveCheckpoints() {
    if (!activeContainer) return;
    fetch("/api/live-controle/active-checkpoints")
      .then(function (r) { return r.json(); })
      .then(function (cps) {
        activeContainer.textContent = "";
        if (!cps || cps.length === 0) {
          var nd = el("div", "hsh-no-data");
          nd.textContent = "Aucun checkpoint actif (0 scan dans les 10 dernieres min)";
          activeContainer.appendChild(nd);
          if (activeCountEl) activeCountEl.textContent = "";
          return;
        }
        if (activeCountEl) activeCountEl.textContent = cps.length + " checkpoint" + (cps.length > 1 ? "s" : "");
        var wrap = el("div", "hsh-active-chips");
        cps.forEach(function (cp) {
          var chip = el("span", "hsh-active-chip");
          var dot = el("span", "hsh-active-chip-dot");
          chip.appendChild(dot);
          var name = document.createTextNode(cp.location_name || ("ID " + cp.location_id));
          chip.appendChild(name);
          if (cp.parent_gate) {
            var gate = el("span", "hsh-active-chip-count");
            gate.textContent = cp.parent_gate;
            chip.appendChild(gate);
          }
          if (cp.entrees) {
            var counts = el("span", "hsh-active-chip-count");
            var parts = [];
            if (cp.entrees_pers) parts.push(cp.entrees_pers + " pers.");
            if (cp.entrees_veh) parts.push(cp.entrees_veh + " veh.");
            if (cp.entrees_enf) parts.push(cp.entrees_enf + " enf.");
            if (cp.entrees_acc) parts.push(cp.entrees_acc + " accred.");
            counts.textContent = parts.length ? parts.join(" / ") : cp.entrees + " E";
            chip.appendChild(counts);
          }
          wrap.appendChild(chip);
        });
        activeContainer.appendChild(wrap);
      })
      .catch(function () {});
  }

  function loadDebitGates() {
    if (!debitTbody) return;
    fetch("/api/live-controle/debit-gates")
      .then(function (r) { return r.json(); })
      .then(function (gates) {
        debitTbody.textContent = "";
        if (!gates || gates.length === 0) {
          var tr = el("tr");
          var td = el("td");
          td.colSpan = 4;
          td.textContent = "Aucune donnee";
          td.style.textAlign = "center";
          td.style.color = "var(--muted)";
          tr.appendChild(td);
          debitTbody.appendChild(tr);
          return;
        }
        gates.forEach(function (g) {
          var tr = el("tr");
          var tdGate = el("td");
          tdGate.textContent = g.gate;
          tr.appendChild(tdGate);
          [g.entrees_h, g.sorties_h, g.total_h].forEach(function (val) {
            var td = el("td");
            td.textContent = val;
            td.style.textAlign = "right";
            tr.appendChild(td);
          });
          debitTbody.appendChild(tr);
        });
      })
      .catch(function () {});
  }

  function loadTitresLive() {
    if (!titresTbody) return;
    fetch("/api/live-controle/titres-live")
      .then(function (r) { return r.json(); })
      .then(function (titres) {
        titresTbody.textContent = "";
        if (!titres || titres.length === 0) {
          var tr = el("tr");
          var td = el("td");
          td.colSpan = 4;
          td.textContent = "Aucune donnee";
          td.style.textAlign = "center";
          td.style.color = "var(--muted)";
          tr.appendChild(td);
          titresTbody.appendChild(tr);
          return;
        }
        titres.forEach(function (t) {
          var tr = el("tr");
          var tdTitre = el("td");
          tdTitre.textContent = t.titre;
          tr.appendChild(tdTitre);
          [t.entrees, t.sorties, t.presents].forEach(function (val) {
            var td = el("td");
            td.textContent = val;
            td.style.textAlign = "right";
            tr.appendChild(td);
          });
          titresTbody.appendChild(tr);
        });
      })
      .catch(function () {});
  }

  function loadLiveCounters() {
    if (!liveCounters) return;
    fetch("/api/live-controle/counters")
      .then(function (r) { return r.json(); })
      .then(function (counters) {
        liveCounters.textContent = "";
        if (!counters || counters.length === 0) {
          var nd = el("div", "hsh-no-data");
          nd.textContent = "Aucun compteur";
          liveCounters.appendChild(nd);
          return;
        }
        counters.forEach(function (c) {
          var current = parseInt(c.current, 10) || 0;
          var correction = parseInt(c.correction, 10) || 0;
          var corrected = current - correction;
          var upper = parseInt(c.upper_limit, 10) || 0;
          var pct = upper > 0 ? Math.round((corrected / upper) * 100) : 0;
          var locked = c.locked && c.locked !== "0";

          var card = el("div", "hsh-counter-card");
          var header = el("div", "hsh-counter-header");
          var nameSpan = el("span", "hsh-counter-name");
          nameSpan.textContent = c.location_name || c.counter_name || ("Location " + c.location_id);
          header.appendChild(nameSpan);
          if (locked) {
            var lockIcon = el("span", "material-symbols-outlined hsh-counter-locked");
            lockIcon.textContent = "lock";
            header.appendChild(lockIcon);
          }
          card.appendChild(header);

          var stats = el("div", "hsh-counter-stats");
          if (correction) {
            // Afficher la correction clairement : brut - correction = corrige
            var sp = el("span", "hsh-counter-corrected");
            sp.innerHTML = "P: <strong>" + corrected + "</strong> <span style=\"font-size:0.7rem;opacity:0.7;\">(" + current + " - " + correction + ")</span>";
            stats.appendChild(sp);
            [["E", c.entries], ["S", c.exits]].forEach(function (pair) {
              var s = el("span");
              s.textContent = pair[0] + ": ";
              var b = el("strong");
              b.textContent = pair[1] || "--";
              s.appendChild(b);
              stats.appendChild(s);
            });
          } else {
            [["E", c.entries], ["S", c.exits], ["P", c.current]].forEach(function (pair) {
              var s = el("span");
              s.textContent = pair[0] + ": ";
              var b = el("strong");
              b.textContent = pair[1] || "--";
              s.appendChild(b);
              stats.appendChild(s);
            });
          }
          card.appendChild(stats);

          // Detail par categorie (personnes / vehicules / enfants / accredites)
          var eVeh = c.entrees_veh || 0;
          var sVeh = c.sorties_veh || 0;
          var eEnf = c.entrees_enf || 0;
          var sEnf = c.sorties_enf || 0;
          var eAcc = c.entrees_acc || 0;
          var sAcc = c.sorties_acc || 0;
          if (eVeh || sVeh || eEnf || sEnf || eAcc || sAcc) {
            var eTotal = parseInt(c.entries, 10) || 0;
            var sTotal = parseInt(c.exits, 10) || 0;
            var ePers = eTotal - eVeh - eEnf - eAcc;
            var sPers = sTotal - sVeh - sEnf - sAcc;
            var detail = el("div", "hsh-counter-detail");
            detail.style.fontSize = "0.75rem";
            detail.style.opacity = "0.8";
            detail.style.marginTop = "4px";
            var lines = [];
            lines.push("Pers: E " + ePers + " / S " + sPers + " / P " + (ePers - sPers));
            if (eVeh || sVeh) lines.push("Veh: E " + eVeh + " / S " + sVeh + " / P " + (eVeh - sVeh));
            if (eEnf || sEnf) lines.push("Enf: E " + eEnf + " / S " + sEnf + " / P " + (eEnf - sEnf));
            if (eAcc || sAcc) lines.push("Accred: E " + eAcc + " / S " + sAcc + " / P " + (eAcc - sAcc));
            lines.forEach(function (line) {
              var lineEl = el("div");
              lineEl.textContent = line;
              detail.appendChild(lineEl);
            });
            card.appendChild(detail);
          }

          // Inputs corrections (global + enfants + vehicules + accredites) + radio principal
          var corrRow = el("div", "hsh-correction-row");
          corrRow.style.flexWrap = "wrap";
          corrRow.style.gap = "8px";

          var makeCorrInput = function (label, value, configKey) {
            var lbl = el("label", "hsh-corr-cell");
            lbl.style.fontSize = "0.7rem";
            lbl.style.display = "inline-flex";
            lbl.style.alignItems = "center";
            lbl.style.gap = "3px";
            lbl.appendChild(document.createTextNode(label + " "));
            var inp = el("input", "hsh-correction-input");
            inp.type = "number";
            inp.value = value || "";
            inp.placeholder = "0";
            inp.style.width = "56px";
            inp.dataset.locationId = c.location_id;
            inp.addEventListener("change", function () {
              var val = parseInt(this.value, 10) || 0;
              var corrs = config[configKey] || {};
              if (val === 0) {
                delete corrs[String(c.location_id)];
              } else {
                corrs[String(c.location_id)] = val;
              }
              var payload = {};
              payload[configKey] = corrs;
              saveConfig(payload).then(function () {
                config[configKey] = corrs;
                loadLiveCounters();
              });
            });
            lbl.appendChild(inp);
            return lbl;
          };

          corrRow.appendChild(makeCorrInput("Corr. global", correction, "corrections_compteurs"));
          corrRow.appendChild(makeCorrInput("Corr. enf", c.correction_enf, "corrections_enfants"));
          corrRow.appendChild(makeCorrInput("Corr. veh", c.correction_veh, "corrections_vehicules"));
          corrRow.appendChild(makeCorrInput("Corr. acc", c.correction_acc, "corrections_accredites"));

          var principalLabel = el("label", "hsh-principal-label");
          principalLabel.style.fontSize = "0.72rem";
          principalLabel.style.marginLeft = "8px";
          var principalRadio = el("input");
          principalRadio.type = "radio";
          principalRadio.name = "hsh-principal-counter";
          principalRadio.checked = !!c.is_principal;
          principalRadio.addEventListener("change", function () {
            if (this.checked) {
              saveConfig({ compteur_principal_id: String(c.location_id) }).then(function () {
                config.compteur_principal_id = String(c.location_id);
                loadLiveCounters();
              });
            }
          });
          principalLabel.appendChild(principalRadio);
          principalLabel.appendChild(document.createTextNode(" Principal"));
          corrRow.appendChild(principalLabel);

          card.appendChild(corrRow);

          if (upper > 0) {
            var gauge = el("div", "hsh-gauge");
            var fill = el("div", "hsh-gauge-fill");
            fill.style.width = Math.min(pct, 100) + "%";
            fill.style.background = pct >= 90 ? "#ef4444" : pct >= 70 ? "#ff9800" : "#4caf50";
            gauge.appendChild(fill);
            card.appendChild(gauge);
            var info = el("div", "hsh-gauge-info");
            var pctSpan = el("span"); pctSpan.textContent = pct + "%"; info.appendChild(pctSpan);
            var capSpan = el("span"); capSpan.textContent = upper; info.appendChild(capSpan);
            card.appendChild(info);
          }
          liveCounters.appendChild(card);
        });
      })
      .catch(function () {});
  }

  // =========================================================
  //  TREE — fold/unfold + reassignment
  // =========================================================

  var TYPE_META = {
    "Venue":      { icon: "stadium",     color: "#6366f1" },
    "Area":       { icon: "layers",      color: "#0ea5e9" },
    "Gate":       { icon: "door_front",  color: "#f59e0b" },
    "Checkpoint": { icon: "sensors",     color: "#10b981" },
  };

  // Quel type de parent accepte chaque type
  var PARENT_TYPES = {
    "Checkpoint": "Gate",
    "Gate": "Area",
    "Area": "Venue",
  };

  function buildHierarchy(docs) {
    var venues = [], areas = [], gates = [], checkpoints = [];
    docs.forEach(function (d) {
      var t = (d.location_type || "").toLowerCase();
      if (t === "venue") venues.push(d);
      else if (t === "area") areas.push(d);
      else if (t === "gate") gates.push(d);
      else if (t === "checkpoint") checkpoints.push(d);
    });

    gates.forEach(function (g) {
      g._children = checkpoints.filter(function (c) {
        return c.parent_gate && String(c.parent_gate.id) === String(g.location_id);
      });
    });
    areas.forEach(function (a) {
      a._children = gates.filter(function (g) {
        return g.parent_area && String(g.parent_area.id) === String(a.location_id);
      });
    });
    venues.forEach(function (v) {
      v._children = areas.filter(function (a) {
        return a.parent_venue && String(a.parent_venue.id) === String(v.location_id);
      });
    });

    // Remonter les compteurs du jour des checkpoints vers les parents
    function sumCounts(node) {
      var c = node.counts_jour || {entrees: 0, sorties: 0, entrees_veh: 0, sorties_veh: 0, entrees_enf: 0, sorties_enf: 0, entrees_acc: 0, sorties_acc: 0};
      var kids = node._children || [];
      kids.forEach(function (child) {
        var cc = sumCounts(child);
        c.entrees = (c.entrees || 0) + (cc.entrees || 0);
        c.sorties = (c.sorties || 0) + (cc.sorties || 0);
        c.entrees_veh = (c.entrees_veh || 0) + (cc.entrees_veh || 0);
        c.sorties_veh = (c.sorties_veh || 0) + (cc.sorties_veh || 0);
        c.entrees_enf = (c.entrees_enf || 0) + (cc.entrees_enf || 0);
        c.sorties_enf = (c.sorties_enf || 0) + (cc.sorties_enf || 0);
        c.entrees_acc = (c.entrees_acc || 0) + (cc.entrees_acc || 0);
        c.sorties_acc = (c.sorties_acc || 0) + (cc.sorties_acc || 0);
      });
      node.counts_jour = c;
      return c;
    }

    var attachedIds = new Set();
    venues.forEach(function (v) { (v._children || []).forEach(function (a) { attachedIds.add(a._id); }); });
    areas.forEach(function (a) { (a._children || []).forEach(function (g) { attachedIds.add(g._id); }); });
    gates.forEach(function (g) { (g._children || []).forEach(function (c) { attachedIds.add(c._id); }); });

    // Calculer les totaux en remontant depuis les feuilles
    venues.forEach(sumCounts);

    var orphans = docs.filter(function (d) {
      return !attachedIds.has(d._id) && (d.location_type || "").toLowerCase() !== "venue";
    });
    orphans.forEach(sumCounts);

    return { roots: venues, orphans: orphans };
  }

  function isSelected(locId, locType) {
    return selectedLocations.some(function (s) {
      return String(s.id) === String(locId) && s.type === locType;
    });
  }

  // Trouver tous les parents possibles pour un type donne
  function getPossibleParents(childType) {
    var parentType = PARENT_TYPES[childType];
    if (!parentType) return [];
    return structure.filter(function (d) {
      return d.location_type === parentType;
    });
  }

  function renderTree() {
    treeContainer.textContent = "";

    if (structure.length === 0) {
      var nd = el("div", "hsh-no-data");
      nd.textContent = "Aucune structure disponible. Activez le controle et attendez le premier cycle.";
      treeContainer.appendChild(nd);
      return;
    }

    var hier = buildHierarchy(structure);

    function makeNode(doc, depth) {
      var meta = TYPE_META[doc.location_type] || { icon: "location_on", color: "#999" };
      var children = doc._children || [];
      var hasChildren = children.length > 0;
      var nodeKey = doc._id;
      var isCollapsed = !!collapsedNodes[nodeKey];

      // --- Row container ---
      var row = el("div", "hsh-tree-row");
      row.style.paddingLeft = (12 + depth * 24) + "px";
      row.dataset.name = doc.location_name || "";
      row.dataset.type = doc.location_type || "";
      row.dataset.nodeId = nodeKey;

      // Chevron fold/unfold
      var chevron = el("span", "material-symbols-outlined hsh-tree-chevron");
      if (hasChildren) {
        chevron.textContent = isCollapsed ? "chevron_right" : "expand_more";
        chevron.addEventListener("click", function (e) {
          e.stopPropagation();
          collapsedNodes[nodeKey] = !collapsedNodes[nodeKey];
          renderTree();
          applyFilter();
        });
      } else {
        chevron.textContent = "";
        chevron.style.width = "20px";
      }
      row.appendChild(chevron);

      // Checkbox
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "hsh-tree-cb";
      cb.checked = isSelected(doc.location_id, doc.location_type);
      cb.dataset.locId = doc.location_id;
      cb.dataset.locType = doc.location_type;
      cb.dataset.locName = doc.location_name || "";
      cb.addEventListener("change", onTreeCheckChange);
      cb.addEventListener("click", function (e) { e.stopPropagation(); });
      row.appendChild(cb);

      // Pastille type
      var dot = el("span", "hsh-tree-type-dot");
      dot.style.background = meta.color;
      dot.title = doc.location_type;
      row.appendChild(dot);

      // Nom
      var nameSpan = el("span", "hsh-tree-name");
      nameSpan.textContent = doc.location_name || ("ID " + doc.location_id);
      row.appendChild(nameSpan);

      // Badge type
      var badge = el("span", "hsh-tree-badge");
      badge.textContent = doc.location_type;
      badge.style.color = meta.color;
      badge.style.borderColor = meta.color;
      row.appendChild(badge);

      // Compteurs inline (jour, par categorie)
      var cj = doc.counts_jour;
      if (cj && cj.entrees) {
        var ctr = el("span", "hsh-tree-counter");
        var ePers = (cj.entrees || 0) - (cj.entrees_veh || 0) - (cj.entrees_enf || 0) - (cj.entrees_acc || 0);
        var parts = [];
        if (ePers > 0) parts.push(ePers + " pers.");
        if (cj.entrees_veh > 0) parts.push(cj.entrees_veh + " veh.");
        if (cj.entrees_enf > 0) parts.push(cj.entrees_enf + " enf.");
        if (cj.entrees_acc > 0) parts.push(cj.entrees_acc + " accred.");
        ctr.textContent = parts.length ? parts.join(" / ") : cj.entrees + " E";
        row.appendChild(ctr);
      }

      // Bouton reassigner (pour Gate, Checkpoint, Area)
      if (PARENT_TYPES[doc.location_type]) {
        var assignBtn = el("button", "hsh-tree-assign-btn");
        assignBtn.title = "Rattacher a un parent";
        var assignIcon = el("span", "material-symbols-outlined");
        assignIcon.textContent = "more_vert";
        assignBtn.appendChild(assignIcon);
        assignBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          openAssignMenu(doc, assignBtn);
        });
        row.appendChild(assignBtn);
      }

      treeContainer.appendChild(row);

      // Enfants (si non replie)
      if (!isCollapsed) {
        children.forEach(function (child) {
          makeNode(child, depth + 1);
        });
      }
    }

    // Noeuds racines (Venues)
    hier.roots.forEach(function (v) { makeNode(v, 0); });

    // Section orphelins
    if (hier.orphans.length > 0) {
      var orphanKey = "__orphans__";
      var orphanCollapsed = !!collapsedNodes[orphanKey];

      var divider = el("div", "hsh-tree-section-header");

      var orphChevron = el("span", "material-symbols-outlined hsh-tree-chevron");
      orphChevron.textContent = orphanCollapsed ? "chevron_right" : "expand_more";
      orphChevron.addEventListener("click", function () {
        collapsedNodes[orphanKey] = !collapsedNodes[orphanKey];
        renderTree();
        applyFilter();
      });
      divider.appendChild(orphChevron);

      var orphLabel = el("span", "hsh-tree-section-label");
      orphLabel.textContent = "Non attribues (" + hier.orphans.length + ")";
      divider.appendChild(orphLabel);

      treeContainer.appendChild(divider);

      if (!orphanCollapsed) {
        hier.orphans.forEach(function (o) { makeNode(o, 1); });
      }
    }
  }

  // =========================================================
  //  MENU DE REASSIGNATION
  // =========================================================

  var activeMenu = null;

  function closeAssignMenu() {
    if (activeMenu) {
      if (activeMenu.parentNode) activeMenu.parentNode.removeChild(activeMenu);
      activeMenu = null;
    }
  }

  document.addEventListener("click", function () { closeAssignMenu(); });

  function openAssignMenu(doc, anchorBtn) {
    closeAssignMenu();

    var possibleParents = getPossibleParents(doc.location_type);
    var parentType = PARENT_TYPES[doc.location_type];

    var menu = el("div", "hsh-assign-menu");
    activeMenu = menu;

    var title = el("div", "hsh-assign-menu-title");
    title.textContent = "Rattacher a un " + parentType;
    menu.appendChild(title);

    // Option "Detacher"
    var detachItem = el("div", "hsh-assign-menu-item hsh-assign-detach");
    detachItem.textContent = "Detacher (non attribue)";
    detachItem.addEventListener("click", function (e) {
      e.stopPropagation();
      assignParent(doc, null, null, "");
    });
    menu.appendChild(detachItem);

    if (possibleParents.length === 0) {
      var empty = el("div", "hsh-assign-menu-empty");
      empty.textContent = "Aucun " + parentType + " disponible";
      menu.appendChild(empty);
    } else {
      possibleParents.forEach(function (p) {
        var item = el("div", "hsh-assign-menu-item");
        var pMeta = TYPE_META[p.location_type] || { color: "#999" };

        var pDot = el("span", "hsh-tree-type-dot");
        pDot.style.background = pMeta.color;
        item.appendChild(pDot);

        var pName = el("span");
        pName.textContent = p.location_name || ("ID " + p.location_id);
        item.appendChild(pName);

        // Indiquer le parent actuel
        var currentParentField = { "Gate": "parent_area", "Checkpoint": "parent_gate", "Area": "parent_venue" }[doc.location_type];
        var currentParent = doc[currentParentField];
        if (currentParent && String(currentParent.id) === String(p.location_id)) {
          item.classList.add("hsh-assign-current");
          var tag = el("span", "hsh-assign-current-tag");
          tag.textContent = "actuel";
          item.appendChild(tag);
        }

        item.addEventListener("click", function (e) {
          e.stopPropagation();
          assignParent(doc, p.location_id, p.location_type, p.location_name || "");
        });
        menu.appendChild(item);
      });
    }

    // Positionner le menu
    var rect = anchorBtn.getBoundingClientRect();
    menu.style.top = (rect.bottom + 4) + "px";
    menu.style.left = Math.min(rect.left, window.innerWidth - 260) + "px";
    document.body.appendChild(menu);

    // Empecher la fermeture immediate
    setTimeout(function () {
      menu.addEventListener("click", function (e) { e.stopPropagation(); });
    }, 0);
  }

  function assignParent(doc, parentId, parentType, parentName) {
    closeAssignMenu();
    fetch("/api/live-controle/structure/assign", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({
        node_id: doc.location_id,
        node_type: doc.location_type,
        node_name: doc.location_name || "",
        parent_id: parentId,
        parent_type: parentType,
        parent_name: parentName,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        if (r.ok) {
          if (typeof showToast === "function") showToast("success", "Parent mis a jour");
          loadStructure();
        } else {
          if (typeof showToast === "function") showToast("error", r.error || "Erreur");
        }
      });
  }

  // =========================================================
  //  SELECTIONS
  // =========================================================

  function onTreeCheckChange() {
    var cbs = treeContainer.querySelectorAll(".hsh-tree-cb");
    selectedLocations = [];
    cbs.forEach(function (cb) {
      if (cb.checked) {
        selectedLocations.push({ id: cb.dataset.locId, type: cb.dataset.locType, name: cb.dataset.locName });
      }
    });
    saveLocBtn.disabled = false;
  }

  // =========================================================
  //  FILTRE ARBRE
  // =========================================================

  function applyFilter() {
    if (!filterInput) return;
    var q = filterInput.value.trim().toLowerCase();
    var rows = treeContainer.querySelectorAll(".hsh-tree-row");
    rows.forEach(function (row) {
      if (!q) {
        row.classList.remove("hsh-hidden");
        return;
      }
      var name = (row.dataset.name || "").toLowerCase();
      var type = (row.dataset.type || "").toLowerCase();
      row.classList.toggle("hsh-hidden", name.indexOf(q) === -1 && type.indexOf(q) === -1);
    });
    // Aussi masquer/montrer les section headers orphelins
    var sectionHeaders = treeContainer.querySelectorAll(".hsh-tree-section-header");
    sectionHeaders.forEach(function (sh) {
      if (!q) { sh.classList.remove("hsh-hidden"); return; }
      // Garder visible si au moins un orphelin visible apres
      var next = sh.nextElementSibling;
      var anyVisible = false;
      while (next && !next.classList.contains("hsh-tree-section-header")) {
        if (next.classList.contains("hsh-tree-row") && !next.classList.contains("hsh-hidden")) {
          anyVisible = true;
          break;
        }
        next = next.nextElementSibling;
      }
      sh.classList.toggle("hsh-hidden", !anyVisible);
    });
  }

  if (filterInput) {
    filterInput.addEventListener("input", applyFilter);
  }

  // =========================================================
  //  SAVE
  // =========================================================

  function saveConfig(fields) {
    return fetch("/api/live-controle/config", {
      method: "PUT",
      headers: jsonHeaders(),
      body: JSON.stringify(fields),
    })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        if (r.ok) {
          if (typeof showToast === "function") showToast("success", "Configuration sauvegardee");
        } else {
          if (typeof showToast === "function") showToast("error", r.error || "Erreur");
        }
        return r;
      });
  }

  // =========================================================
  //  EVENTS
  // =========================================================

  // --- Modale archivage ---
  var archiveOverlay = document.getElementById("hsh-archive-overlay");
  var archiveEventInput = document.getElementById("hsh-archive-event");
  var archiveCancelBtn = document.getElementById("hsh-archive-cancel");
  var archiveSkipBtn = document.getElementById("hsh-archive-skip");
  var archiveConfirmBtn = document.getElementById("hsh-archive-confirm");

  function doDeactivate() {
    saveConfig({ live_controle_actif: false }).then(function () { loadConfig(); });
  }

  function showArchiveModal() {
    archiveEventInput.value = config.evenement || "";
    archiveOverlay.style.display = "";
  }

  function hideArchiveModal() {
    archiveOverlay.style.display = "none";
  }

  if (archiveCancelBtn) {
    archiveCancelBtn.addEventListener("click", function () {
      hideArchiveModal();
      toggleEl.checked = true;  // annuler : remettre le toggle ON
    });
  }

  if (archiveSkipBtn) {
    archiveSkipBtn.addEventListener("click", function () {
      hideArchiveModal();
      doDeactivate();
    });
  }

  if (archiveConfirmBtn) {
    archiveConfirmBtn.addEventListener("click", function () {
      var evt = archiveEventInput.value.trim();
      if (!evt) {
        if (typeof showToast === "function") showToast("error", "Evenement requis");
        return;
      }
      archiveConfirmBtn.disabled = true;
      archiveConfirmBtn.textContent = "Archivage...";
      fetch("/api/live-controle/archive", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ evenement: evt }),
      })
        .then(function (r) { return r.json(); })
        .then(function (r) {
          archiveConfirmBtn.disabled = false;
          archiveConfirmBtn.textContent = "Archiver et desactiver";
          if (r.ok) {
            var c = r.counts || {};
            var parts = [];
            if (c.transactions_agg) parts.push(c.transactions_agg + " tranches tx");
            if (c.erreurs) parts.push(c.erreurs + " erreurs");
            if (c.structure) parts.push(c.structure + " locations");
            if (c.compteurs) parts.push(c.compteurs + " compteurs");
            var msg = "Archive " + r.archive + " : " + (parts.length ? parts.join(", ") : "aucune donnee");
            if (typeof showToast === "function") showToast("success", msg);
            hideArchiveModal();
            doDeactivate();
          } else {
            if (typeof showToast === "function") showToast("error", r.error || "Erreur archivage");
          }
        })
        .catch(function () {
          archiveConfirmBtn.disabled = false;
          archiveConfirmBtn.textContent = "Archiver et desactiver";
          if (typeof showToast === "function") showToast("error", "Erreur reseau");
        });
    });
  }

  toggleEl.addEventListener("change", function () {
    var actif = toggleEl.checked;
    if (!actif && config.live_controle_actif) {
      // Desactivation → proposer l'archivage
      showArchiveModal();
      return;
    }
    var fields = { live_controle_actif: actif };
    if (actif && eventSelect.value) {
      fields.evenement = eventSelect.value;
      fields.evenement_clean = eventSelect.value.replace(/[^a-zA-Z0-9_-]/g, "_");
    }
    saveConfig(fields).then(function () { loadConfig(); });
  });

  eventSelect.addEventListener("change", function () {
    var val = eventSelect.value;
    saveConfig({
      evenement: val,
      evenement_clean: val.replace(/[^a-zA-Z0-9_-]/g, "_"),
    }).then(function () { loadConfig(); });
  });

  forceBtn.addEventListener("click", function () {
    fetch("/api/live-controle/force-inventory", { method: "POST", headers: jsonHeaders() })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        if (r.ok) {
          if (typeof showToast === "function") showToast("success", "Inventaire force au prochain cycle");
          loadStatus();
        }
      });
  });

  if (forceTxBtn) {
    forceTxBtn.addEventListener("click", function () {
      var jours = forceTxDays ? parseInt(forceTxDays.value, 10) : 1;
      showConfirmToast("Forcer la collecte des transactions sur " + jours + " jour(s) ? Cela sera execute au prochain cycle (max 2 min).").then(function(ok){
        if(!ok) return;
        fetch("/api/live-controle/force-transactions", {
          method: "POST",
          headers: jsonHeaders(),
          body: JSON.stringify({ jours: jours }),
        })
          .then(function (r) { return r.json(); })
          .then(function (r) {
            if (r.ok) {
              showToast("success", "Collecte forcee sur " + jours + " jour(s) au prochain cycle");
              loadStatus();
            } else {
              showToast("error", r.error || "Erreur");
            }
          });
      });
    });
  }

  if (errorsFilterEvent) {
    errorsFilterEvent.addEventListener("change", function () { loadErrors(); });
  }

  saveLocBtn.addEventListener("click", function () {
    saveConfig({ locations_selectionnees: selectedLocations }).then(function () {
      saveLocBtn.disabled = true;
    });
  });

  // =========================================================
  //  INIT
  // =========================================================

  loadEvents();
  loadConfig();
  setInterval(function () { loadStatus(); loadErrors(); loadLiveCounters(); loadActiveCheckpoints(); loadTitresLive(); loadDebitGates(); }, 30000);

})();
