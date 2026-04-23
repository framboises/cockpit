// ============================================================================
// AFFLUENCE PREVISIONNELLE
// ============================================================================

function formatNumber(n) {
    if (n == null) return "--";
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

function formatShort(n) {
    if (n == null) return "--";
    if (n >= 10000) return (n / 1000).toFixed(1).replace(".", ",") + "k";
    if (n >= 1000) return (n / 1000).toFixed(2).replace(".", ",") + "k";
    return String(n);
}

function formatRange(low, high) {
    if (low == null && high == null) return "--";
    if (low == null || low === high) return formatNumber(high);
    if (high == null) return formatNumber(low);
    return formatShort(low) + "-" + formatShort(high);
}

// ── Tab 1 : Affluence ──────────────────────────────────────────────────

function renderAffluenceTab(data) {
    var body = document.querySelector('.widget-tab-content[data-tab="affluence-jours"]');
    if (!body) return;
    body.textContent = "";

    if (!data.days || data.days.length === 0) {
        var ph = document.createElement("div");
        ph.className = "widget-placeholder";
        var icon = document.createElement("span");
        icon.className = "material-symbols-outlined";
        icon.textContent = "groups";
        var txt = document.createElement("span");
        txt.textContent = "Pas de donnees";
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
    headerRow.appendChild(document.createElement("div"));

    var labels = ["Proj. Ventes", "Pic" + prevLabel, "Pic Proj."];
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

        // Projection ventes (fourchette low - high compact)
        var projCell = document.createElement("div");
        projCell.className = "affluence-metric";
        var projVal = document.createElement("span");
        projVal.className = "affluence-value";
        projVal.textContent = formatRange(d.projection_low, d.projection);
        projCell.appendChild(projVal);
        row.appendChild(projCell);

        // Pic N-1
        var picCell = document.createElement("div");
        picCell.className = "affluence-metric";
        var picVal = document.createElement("span");
        picVal.className = "affluence-value";
        picVal.textContent = formatNumber(d.pic_prev);
        picCell.appendChild(picVal);
        row.appendChild(picCell);

        // Pic projete : fourchette low - high compact
        var picProjCell = document.createElement("div");
        picProjCell.className = "affluence-metric";
        var picProjVal = document.createElement("span");
        picProjVal.className = "affluence-value";
        picProjVal.textContent = formatRange(d.pic_projection_low, d.pic_projection);
        picProjCell.appendChild(picProjVal);
        if (d.pic_projection != null && d.pic_prev != null && d.pic_prev > 0) {
            var midPic = d.pic_projection_low != null ? Math.round((d.pic_projection_low + d.pic_projection) / 2) : d.pic_projection;
            var pct = ((midPic - d.pic_prev) / d.pic_prev * 100).toFixed(0);
            var pill = document.createElement("span");
            pill.className = "affluence-pill " + (pct >= 0 ? "positive" : "negative");
            pill.textContent = (pct >= 0 ? "+" : "") + pct + "%";
            picProjCell.appendChild(pill);
        }
        row.appendChild(picProjCell);

        grid.appendChild(row);
    }

    body.appendChild(grid);

    // Footer
    var footer = document.createElement("div");
    footer.className = "affluence-footer";

    if (data.total_projection) {
        var projSpan = document.createElement("span");
        projSpan.textContent = "Proj. " + formatRange(data.total_projection_low, data.total_projection);
        footer.appendChild(projSpan);
    }

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

// ── Tab 2 : Ventes ─────────────────────────────────────────────────────

function renderVentesTab(data) {
    var body = document.querySelector('.widget-tab-content[data-tab="affluence-tab2"]');
    if (!body) return;
    body.textContent = "";

    if (!data.days || data.days.length === 0) return;

    var prevLabel = data.prev_year ? " (" + data.prev_year + ")" : "";

    var grid = document.createElement("div");
    grid.className = "affluence-grid";

    // En-tete
    var headerRow = document.createElement("div");
    headerRow.className = "affluence-header-row";
    headerRow.appendChild(document.createElement("div"));

    var labels = ["Ventes", "Ventes" + prevLabel, "Projection"];
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

        // Projection + pill delta vs N-1 (fourchette low - high compact)
        var projCell = document.createElement("div");
        projCell.className = "affluence-metric";
        var projVal = document.createElement("span");
        projVal.className = "affluence-value";
        projVal.textContent = formatRange(d.projection_low, d.projection);
        projCell.appendChild(projVal);
        if (d.projection != null && d.ventes_prev != null && d.ventes_prev > 0) {
            var midProj = d.projection_low != null ? Math.round((d.projection_low + d.projection) / 2) : d.projection;
            var projDiff = ((midProj - d.ventes_prev) / d.ventes_prev * 100).toFixed(0);
            var projPill = document.createElement("span");
            projPill.className = "affluence-pill " + (projDiff >= 0 ? "positive" : "negative");
            projPill.textContent = (projDiff >= 0 ? "+" : "") + projDiff + "%";
            projCell.appendChild(projPill);
        }
        row.appendChild(projCell);

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

    if (data.total_projection) {
        var projFooter = document.createElement("span");
        projFooter.className = "affluence-update";
        projFooter.textContent = "Proj. " + formatRange(data.total_projection_low, data.total_projection);
        footer.appendChild(projFooter);
    }

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

// ── Tab 3 : Sites ──────────────────────────────────────────────────────

function renderSitesTab(data) {
    var body = document.querySelector('.widget-tab-content[data-tab="affluence-tab3"]');
    if (!body) return;
    body.textContent = "";

    if (!data.sites || data.sites.length === 0) {
        var ph = document.createElement("div");
        ph.className = "widget-placeholder";
        var icon = document.createElement("span");
        icon.className = "material-symbols-outlined";
        icon.textContent = "local_parking";
        var txt = document.createElement("span");
        txt.textContent = "Pas de donnees sites";
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
    headerRow.className = "affluence-header-row site-header";
    var labels = ["Site", "Jauge", "Ventes", "Projection"];
    for (var h = 0; h < labels.length; h++) {
        var col = document.createElement("div");
        col.className = "affluence-col-label";
        col.textContent = labels[h];
        headerRow.appendChild(col);
    }
    grid.appendChild(headerRow);

    // Lignes
    for (var i = 0; i < data.sites.length; i++) {
        var s = data.sites[i];
        var row = document.createElement("div");
        row.className = "site-row";

        // Nom
        var nameCell = document.createElement("div");
        nameCell.textContent = s.name;
        row.appendChild(nameCell);

        // Mini camembert jauge
        var gaugeCell = document.createElement("div");
        gaugeCell.className = "gauge-cell";
        var cap = parseInt(s.capacite) || 0;
        if (cap > 0) {
            var pct = Math.round(s.ventes / cap * 100);
            var piePct = Math.min(pct, 100);
            var color = pct < 60 ? "var(--success)" : pct < 85 ? "var(--warning, #f59e0b)" : "var(--danger)";
            var pie = document.createElement("div");
            pie.className = "mini-pie";
            pie.style.background = "conic-gradient(" + color + " 0% " + piePct + "%, var(--line) " + piePct + "% 100%)";
            var pctLabel = document.createElement("span");
            pctLabel.className = "mini-pie-label";
            pctLabel.textContent = pct + "%";
            var tooltip = document.createElement("div");
            tooltip.className = "gauge-tooltip";
            tooltip.textContent = "Capacite : " + formatNumber(cap);
            gaugeCell.appendChild(pie);
            gaugeCell.appendChild(pctLabel);
            gaugeCell.appendChild(tooltip);
        } else {
            gaugeCell.textContent = "--";
        }
        row.appendChild(gaugeCell);

        // Ventes
        var ventesCell = document.createElement("div");
        ventesCell.textContent = formatNumber(s.ventes);
        row.appendChild(ventesCell);

        // Projection + pill delta vs N-1
        var projCell = document.createElement("div");
        var projVal = document.createElement("span");
        projVal.textContent = formatNumber(s.projection);
        projCell.appendChild(projVal);
        if (s.projection != null && s.ventes_prev != null && s.ventes_prev > 0) {
            var diff = ((s.projection - s.ventes_prev) / s.ventes_prev * 100).toFixed(0);
            var pill = document.createElement("span");
            pill.className = "affluence-pill " + (diff >= 0 ? "positive" : "negative");
            pill.textContent = (diff >= 0 ? "+" : "") + diff + "%";
            projCell.appendChild(pill);
        }
        row.appendChild(projCell);

        grid.appendChild(row);
    }

    body.appendChild(grid);
}

// ── Chargement ─────────────────────────────────────────────────────────

function loadAffluence() {
    if (!window.isBlockAllowed("widget-right-2")) return;
    var ev = window.selectedEvent;
    var yr = window.selectedYear;
    if (!ev || !yr) return;

    var widget = document.getElementById("widget-right-2");
    fetch("/get_affluence?event=" + encodeURIComponent(ev) + "&year=" + encodeURIComponent(yr))
        .then(function (r) { return r.json(); })
        .then(function (data) {
            var hasData = data.days && data.days.length > 0;
            if (widget) widget.style.display = hasData ? "" : "none";
            renderAffluenceTab(data);
            renderVentesTab(data);
            renderSitesTab(data);
        })
        .catch(function (err) {
            console.error("Affluence error:", err);
            if (widget) widget.style.display = "none";
        });
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

        var tabs = card.querySelectorAll(".widget-tab");
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].classList.remove("active");
        }
        tab.classList.add("active");

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
