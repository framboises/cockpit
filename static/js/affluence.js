// ============================================================================
// AFFLUENCE PREVISIONNELLE
// ============================================================================

function formatNumber(n) {
    if (n == null) return "--";
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

function renderAffluence(data) {
    var body = document.getElementById("widget-right-2-body");
    if (!body) return;
    body.textContent = "";

    if (!data.days || data.days.length === 0) {
        var ph = document.createElement("div");
        ph.className = "widget-placeholder";
        var icon = document.createElement("span");
        icon.className = "material-symbols-outlined";
        icon.textContent = "groups";
        var txt = document.createElement("span");
        txt.textContent = "Pas de donnees billetterie";
        ph.appendChild(icon);
        ph.appendChild(txt);
        body.appendChild(ph);
        return;
    }

    var prevLabel = data.prev_year ? " (" + data.prev_year + ")" : "";

    var grid = document.createElement("div");
    grid.className = "affluence-grid";

    // En-tete
    var headerRow = document.createElement("div");
    headerRow.className = "affluence-header-row";
    var emptyCell = document.createElement("div");
    headerRow.appendChild(emptyCell);

    var labels = ["Ventes", "Ventes" + prevLabel, "Pic" + prevLabel];
    for (var h = 0; h < labels.length; h++) {
        var col = document.createElement("div");
        col.className = "affluence-col-label";
        col.textContent = labels[h];
        headerRow.appendChild(col);
    }
    grid.appendChild(headerRow);

    // Lignes par jour
    for (var i = 0; i < data.days.length; i++) {
        var d = data.days[i];
        var row = document.createElement("div");
        row.className = "affluence-day";

        var dayLabel = document.createElement("div");
        dayLabel.className = "affluence-day-label";
        dayLabel.textContent = d.label;
        row.appendChild(dayLabel);

        // Ventes + delta
        var ventesCell = document.createElement("div");
        ventesCell.className = "affluence-metric";
        var ventesVal = document.createElement("span");
        ventesVal.className = "affluence-value";
        ventesVal.textContent = formatNumber(d.ventes);
        ventesCell.appendChild(ventesVal);
        if (d.delta && d.delta !== 0) {
            var deltaSpan = document.createElement("span");
            deltaSpan.className = "affluence-delta " + (d.delta > 0 ? "positive" : "negative");
            deltaSpan.textContent = (d.delta > 0 ? "+" : "") + formatNumber(d.delta);
            ventesCell.appendChild(deltaSpan);
        }
        row.appendChild(ventesCell);

        // Ventes prev + pill % diff
        var vprevCell = document.createElement("div");
        vprevCell.className = "affluence-metric";
        var vprevVal = document.createElement("span");
        vprevVal.className = "affluence-value";
        vprevVal.textContent = formatNumber(d.ventes_prev);
        vprevCell.appendChild(vprevVal);
        if (d.ventes_prev != null && d.ventes_prev > 0 && d.ventes != null) {
            var pct = ((d.ventes - d.ventes_prev) / d.ventes_prev * 100).toFixed(0);
            var pill = document.createElement("span");
            pill.className = "affluence-pill " + (pct >= 0 ? "positive" : "negative");
            pill.textContent = (pct >= 0 ? "+" : "") + pct + "%";
            vprevCell.appendChild(pill);
        }
        row.appendChild(vprevCell);

        // Pic prev
        var picCell = document.createElement("div");
        picCell.className = "affluence-metric";
        var picVal = document.createElement("span");
        picVal.className = "affluence-value";
        picVal.textContent = formatNumber(d.pic_prev);
        picCell.appendChild(picVal);
        row.appendChild(picCell);

        grid.appendChild(row);
    }

    body.appendChild(grid);

    // Footer
    var footer = document.createElement("div");
    footer.className = "affluence-footer";

    var totalSpan = document.createElement("span");
    totalSpan.textContent = "Total : " + formatNumber(data.total_ventes);
    if (data.total_delta && data.total_delta !== 0) {
        totalSpan.textContent += " ";
        var totalDelta = document.createElement("span");
        totalDelta.className = "affluence-delta " + (data.total_delta > 0 ? "positive" : "negative");
        totalDelta.textContent = (data.total_delta > 0 ? "+" : "") + formatNumber(data.total_delta);
        totalSpan.appendChild(totalDelta);
    }
    footer.appendChild(totalSpan);

    if (data.last_update) {
        var parts = data.last_update.split("-");
        var maj = parts.length === 3 ? parts[2] + "/" + parts[1] + "/" + parts[0] : data.last_update;
        var updateSpan = document.createElement("span");
        updateSpan.className = "affluence-update";
        updateSpan.textContent = "MAJ " + maj;
        footer.appendChild(updateSpan);
    }

    body.appendChild(footer);
}

function loadAffluence() {
    var ev = window.selectedEvent;
    var yr = window.selectedYear;
    if (!ev || !yr) return;

    fetch("/get_affluence?event=" + encodeURIComponent(ev) + "&year=" + encodeURIComponent(yr))
        .then(function (r) { return r.json(); })
        .then(function (data) { renderAffluence(data); })
        .catch(function (err) { console.error("Affluence error:", err); });
}

setInterval(loadAffluence, 120000);

// ============================================================================
// WIDGET TABS
// ============================================================================

(function () {
    document.addEventListener("click", function (e) {
        var tab = e.target.closest(".widget-tab");
        if (!tab) return;

        var card = tab.closest(".widget-card");
        if (!card) return;

        var tabName = tab.getAttribute("data-tab");

        // Desactiver tous les onglets du widget
        var tabs = card.querySelectorAll(".widget-tab");
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].classList.remove("active");
        }
        tab.classList.add("active");

        // Afficher le contenu correspondant
        var contents = card.querySelectorAll(".widget-tab-content");
        for (var j = 0; j < contents.length; j++) {
            if (contents[j].getAttribute("data-tab") === tabName) {
                contents[j].classList.add("active");
            } else {
                contents[j].classList.remove("active");
            }
        }
    });
})();
