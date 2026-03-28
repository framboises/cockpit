(function() {
    "use strict";

    // =========================================================================
    // STATE
    // =========================================================================
    var charts = {};
    var labMapCarroye = null;
    var labMapConv = null;
    var heatLayer = null;
    var computing = false;
    var currentEvent = null;
    var currentYear = null;

    // =========================================================================
    // HELPERS
    // =========================================================================
    function $(id) { return document.getElementById(id); }

    function apiGet(url) {
        return fetch(url, {
            credentials: "same-origin",
            headers: {"X-CSRFToken": document.querySelector('meta[name="csrf-token"]').getAttribute("content")}
        }).then(function(r) { return r.json(); });
    }

    function apiPost(url, body) {
        return fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": document.querySelector('meta[name="csrf-token"]').getAttribute("content")
            },
            body: JSON.stringify(body || {})
        }).then(function(r) { return r.json(); });
    }

    function destroyChart(key) {
        if (charts[key]) { charts[key].destroy(); delete charts[key]; }
    }

    function animateValue(el, end, duration) {
        var start = 0;
        var startTime = null;
        function step(ts) {
            if (!startTime) startTime = ts;
            var p = Math.min((ts - startTime) / duration, 1);
            var val = Math.round(start + (end - start) * p);
            el.textContent = val.toLocaleString("fr-FR");
            if (p < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
    }

    function escapeText(s) {
        var d = document.createElement("div");
        d.textContent = s;
        return d.textContent;
    }

    function buildEl(tag, cls, text) {
        var el = document.createElement(tag);
        if (cls) el.className = cls;
        if (text) el.textContent = text;
        return el;
    }

    // Chart.js defaults
    var BRAND_PALETTE = [
        "#2563eb", "#7c3aed", "#6366f1", "#0ea5e9", "#14b8a6",
        "#f59e0b", "#ef4444", "#ec4899", "#8b5cf6", "#10b981",
        "#f97316", "#06b6d4", "#84cc16", "#e11d48", "#0d9488"
    ];

    var HEATMAP_GRADIENT = ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"];

    // =========================================================================
    // INIT
    // =========================================================================
    document.addEventListener("DOMContentLoaded", function() {
        initCenterTabs();
        initPanelExpansion();
        initWidgetTabs();
        initCollapsibleWidgets();
        initComputeButton();
        initEventChangeListener();
        initChartFullscreen();
    });

    // =========================================================================
    // CHART FULLSCREEN MODAL
    // =========================================================================
    var _modalChart = null;

    function initChartFullscreen() {
        // Add expand buttons to all chart containers in center views
        document.querySelectorAll("#lab-center .lab-chart-container").forEach(function(container) {
            var canvas = container.querySelector("canvas");
            if (!canvas) return;
            var btn = buildEl("button", "lab-chart-expand");
            btn.appendChild(buildEl("span", "material-symbols-outlined", "open_in_full"));
            btn.title = "Plein ecran";
            container.appendChild(btn);
            btn.addEventListener("click", function(e) {
                e.stopPropagation();
                openChartModal(canvas.id);
            });
        });

        // Close handlers
        var overlay = $("lab-chart-modal-overlay");
        var closeBtn = $("lab-chart-modal-close");
        if (overlay) overlay.addEventListener("click", closeChartModal);
        if (closeBtn) closeBtn.addEventListener("click", closeChartModal);
        document.addEventListener("keydown", function(e) {
            if (e.key === "Escape") closeChartModal();
        });
    }

    function openChartModal(chartId) {
        var srcChart = charts[chartIdToKey(chartId)];
        if (!srcChart) return;

        var overlay = $("lab-chart-modal-overlay");
        var modal = $("lab-chart-modal");
        var title = $("lab-chart-modal-title");
        var canvas = $("lab-chart-modal-canvas");
        if (!overlay || !modal || !canvas) return;

        // Get title from chart config
        var chartTitle = "";
        if (srcChart.options && srcChart.options.plugins && srcChart.options.plugins.title && srcChart.options.plugins.title.text) {
            chartTitle = srcChart.options.plugins.title.text;
        }
        var labels = {"chart-timeline-hourly": "Chronologie des creations (par heure)", "chart-heatmap-matrix": "Carte de chaleur jour x heure", "chart-backlog": "Backlog au fil du temps"};
        if (title) title.textContent = labels[chartId] || chartTitle || chartId;

        overlay.classList.add("show");
        modal.classList.add("show");

        // Clone chart config into the modal canvas
        if (_modalChart) { _modalChart.destroy(); _modalChart = null; }
        var cfg = srcChart.config;

        // For matrix charts, functions are lost by JSON clone — rebuild them entirely
        if (cfg.type === "matrix") {
            var meta = srcChart._labMeta || {};
            var srcDs = cfg.data.datasets[0];
            var matrixData = srcDs.data.map(function(p) { return {x: p.x, y: p.y, v: p.v}; });
            var maxV = meta.maxVal || 0;
            if (!maxV) matrixData.forEach(function(p) { if (p.v > maxV) maxV = p.v; });
            var xLabels = meta.xLabels || [];
            var yLabels = meta.yLabels || [];
            var nRows = meta.nDays || yLabels.length || 7;
            var joursLong = meta.joursLong || {};
            var hmDays = meta.hmDays || [];
            _modalChart = new Chart(canvas, {
                type: "matrix",
                data: {
                    labels: {x: xLabels, y: yLabels},
                    datasets: [{
                        label: srcDs.label || "Activite",
                        data: matrixData,
                        width: function(ctx) { return (ctx.chart.chartArea || {}).width / 24 - 2; },
                        height: function(ctx) { return (ctx.chart.chartArea || {}).height / nRows - 2; },
                        backgroundColor: function(ctx) {
                            var v = ctx.dataset.data[ctx.dataIndex].v;
                            var ratio = maxV > 0 ? v / maxV : 0;
                            var idx = Math.min(Math.floor(ratio * HEATMAP_GRADIENT.length), HEATMAP_GRADIENT.length - 1);
                            return HEATMAP_GRADIENT[idx];
                        },
                        borderWidth: 1, borderColor: "rgba(255,255,255,0.3)",
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false, animation: false,
                    plugins: {legend: {display: false}, tooltip: {
                        enabled: true,
                        callbacks: {title: function() { return ""; }, label: function(ctx) {
                            var pt = ctx.dataset.data[ctx.dataIndex];
                            var dayIdx = yLabels.indexOf(pt.y);
                            var jourNom = joursLong[hmDays[dayIdx]] || pt.y;
                            return jourNom + " " + pt.x + " \u2192 " + pt.v + " fiche" + (pt.v > 1 ? "s" : "");
                        }}
                    }},
                    scales: {
                        x: {type: "category", position: "top", labels: xLabels, offset: true, grid: {display: false},
                            ticks: {color: "#1f2d3d", font: {size: 13, weight: "bold"}}},
                        y: {type: "category", labels: yLabels, offset: true, grid: {display: false},
                            ticks: {color: "#1f2d3d", font: {size: 14, weight: "bold"}}}
                    }
                },
            });
        } else {
            var newOpts = JSON.parse(JSON.stringify(cfg.options || {}));
            newOpts.responsive = true;
            newOpts.maintainAspectRatio = false;
            newOpts.animation = false;
            if (!newOpts.plugins) newOpts.plugins = {};
            if (!newOpts.plugins.tooltip) newOpts.plugins.tooltip = {};
            newOpts.plugins.tooltip.enabled = true;
            newOpts.plugins.tooltip.intersect = false;
            newOpts.plugins.tooltip.mode = "index";
            _modalChart = new Chart(canvas, {
                type: cfg.type,
                data: JSON.parse(JSON.stringify(cfg.data)),
                options: newOpts,
            });
        }
    }

    function closeChartModal() {
        var overlay = $("lab-chart-modal-overlay");
        var modal = $("lab-chart-modal");
        if (overlay) overlay.classList.remove("show");
        if (modal) modal.classList.remove("show");
        if (_modalChart) { _modalChart.destroy(); _modalChart = null; }
    }

    function chartIdToKey(canvasId) {
        var map = {
            "chart-timeline-hourly": "timeline-hourly",
            "chart-heatmap-matrix": "heatmap-matrix",
            "chart-backlog": "backlog",
            "chart-scatter-meteo": "scatter-meteo",
            "chart-affluence-ratio": "affluence-ratio",
            "chart-cluster-timeline": "cluster-timeline",
            "chart-comparative-hourly": "comparative-hourly",
            "chart-comparative-categories": "comparative-categories",
        };
        return map[canvasId] || canvasId.replace("chart-", "");
    }

    // =========================================================================
    // CENTER TABS
    // =========================================================================
    function initCenterTabs() {
        var tabs = document.querySelectorAll(".lab-tab");
        var views = document.querySelectorAll(".lab-view");
        tabs.forEach(function(btn) {
            btn.addEventListener("click", function() {
                var view = btn.getAttribute("data-view");
                tabs.forEach(function(b) { b.classList.toggle("active", b === btn); });
                views.forEach(function(v) { v.classList.toggle("active", v.getAttribute("data-view") === view); });
                if (view === "carte" && !labMapCarroye) setTimeout(initMapCarroye, 100);
                if (view === "convergence" && !labMapConv) setTimeout(initMapConvergence, 100);
                if (view === "reseau" && window._labNetworkData) {
                    setTimeout(function() { _doRenderNetwork(window._labNetworkData); }, 150);
                }
                if (view === "comparatif" && window._labComparativeData) {
                    setTimeout(function() { _doRenderComparative(window._labComparativeData); }, 150);
                }
                if (view === "chrono") {
                    if (!_chronoLoaded) {
                        setTimeout(loadChronoData, 100);
                    } else {
                        setTimeout(renderChrono, 100);
                    }
                }
            });
        });
    }

    // =========================================================================
    // WIDGET TABS (within widgets)
    // =========================================================================
    function initWidgetTabs() {
        document.querySelectorAll(".lab-widget").forEach(function(widget) {
            var tabs = widget.querySelectorAll(".widget-tab");
            var contents = widget.querySelectorAll(".widget-tab-content");
            tabs.forEach(function(btn) {
                btn.addEventListener("click", function() {
                    var tab = btn.getAttribute("data-tab");
                    tabs.forEach(function(b) { b.classList.toggle("active", b === btn); });
                    contents.forEach(function(c) { c.classList.toggle("active", c.getAttribute("data-tab") === tab); });
                });
            });
        });
    }

    // =========================================================================
    // COLLAPSIBLE WIDGETS (all start collapsed except KPIs)
    // =========================================================================
    function initCollapsibleWidgets() {
        document.querySelectorAll(".lab-widget").forEach(function(widget) {
            // KPIs start open, everything else collapsed
            if (widget.id !== "lab-kpis") {
                widget.classList.add("collapsed");
            }
            var header = widget.querySelector(".widget-header");
            if (!header) return;
            // Add chevron icon between h3 and expand button
            var chevron = buildEl("span", "material-symbols-outlined lab-collapse-icon", "expand_more");
            var expandBtn = header.querySelector(".lab-expand-btn");
            if (expandBtn) {
                header.insertBefore(chevron, expandBtn);
            } else {
                header.appendChild(chevron);
            }
            header.addEventListener("click", function(e) {
                if (e.target.closest(".lab-expand-btn")) return;
                widget.classList.toggle("collapsed");
            });
        });
    }

    // =========================================================================
    // PANEL EXPANSION — project widget into center as detail view
    // =========================================================================
    var _expandedWidgetId = null;
    var _detailCharts = {};

    function _destroyDetailChart(key) {
        if (_detailCharts[key]) { _detailCharts[key].destroy(); delete _detailCharts[key]; }
    }

    function initPanelExpansion() {
        document.querySelectorAll(".lab-expand-btn").forEach(function(btn) {
            btn.addEventListener("click", function(e) {
                e.stopPropagation();
                var widget = btn.closest(".lab-widget");
                if (!widget) return;
                if (_expandedWidgetId === widget.id) {
                    closeDetailView();
                } else {
                    openDetailView(widget);
                }
            });
        });
    }

    function openDetailView(widget) {
        _expandedWidgetId = widget.id;
        // Switch center to detail view
        var tabs = document.querySelectorAll(".lab-tab");
        var views = document.querySelectorAll(".lab-view");
        tabs.forEach(function(t) { t.classList.remove("active"); });
        views.forEach(function(v) { v.classList.remove("active"); });
        var detailView = $("lab-detail-view");
        if (detailView) {
            detailView.classList.add("active");
            detailView.textContent = "";
            // Build detail content
            buildDetailContent(widget.id, detailView);
        }
        // Update expand button icon
        var icon = widget.querySelector(".lab-expand-btn .material-symbols-outlined");
        if (icon) icon.textContent = "close_fullscreen";
    }

    function closeDetailView() {
        // Destroy detail charts
        Object.keys(_detailCharts).forEach(_destroyDetailChart);
        _expandedWidgetId = null;
        // Restore previous center tab
        var tabs = document.querySelectorAll(".lab-tab");
        var views = document.querySelectorAll(".lab-view");
        var detailView = $("lab-detail-view");
        if (detailView) { detailView.classList.remove("active"); detailView.textContent = ""; }
        // Reactivate first tab
        if (tabs.length) {
            tabs[0].classList.add("active");
            var firstViewName = tabs[0].getAttribute("data-view");
            views.forEach(function(v) { v.classList.toggle("active", v.getAttribute("data-view") === firstViewName); });
        }
        // Reset all expand icons
        document.querySelectorAll(".lab-expand-btn .material-symbols-outlined").forEach(function(ic) { ic.textContent = "open_in_full"; });
    }

    // =========================================================================
    // DETAIL BUILDERS — rich expanded content per widget
    // =========================================================================
    function _detailHeader(container, icon, title) {
        var hdr = buildEl("div", "lab-detail-header");
        var h2 = document.createElement("h2");
        h2.appendChild(buildEl("span", "material-symbols-outlined", icon));
        h2.appendChild(document.createTextNode(title));
        hdr.appendChild(h2);
        var closeBtn = buildEl("button", "lab-detail-close");
        closeBtn.appendChild(buildEl("span", "material-symbols-outlined", "close"));
        closeBtn.addEventListener("click", closeDetailView);
        hdr.appendChild(closeBtn);
        container.appendChild(hdr);
    }

    function _detailCard(parent, title, cls) {
        var card = buildEl("div", "lab-detail-card" + (cls ? " " + cls : ""));
        if (title) {
            card.appendChild(buildEl("h4", null, title));
        }
        parent.appendChild(card);
        return card;
    }

    function buildDetailContent(widgetId, container) {
        var builders = {
            "lab-kpis": detailKPIs,
            "lab-performance": detailPerformance,
            "lab-quality": detailQuality,
            "lab-meteo-cross": detailMeteo,
            "lab-effectifs-cross": detailEffectifs,
            "lab-appelants": detailAppelants,
            "lab-zones-vuln": detailZones,
            "lab-categories": detailCategories,
            "lab-services": detailServices,
            "lab-intervenants": detailIntervenants,
            "lab-escalation": detailEscalation,
            "lab-text": detailText,
            "lab-anpr": detailANPR,
        };
        var fn = builders[widgetId];
        if (fn) {
            fn(container);
        } else {
            _detailHeader(container, "info", "Detail");
            container.appendChild(buildEl("p", "lab-detail-desc", "Pas de vue detaillee pour ce widget."));
        }
    }

    // --- Detail: KPIs ---
    function detailKPIs(ct) {
        _detailHeader(ct, "monitoring", "KPIs globaux - Vue detaillee - " + currentEvent + " " + currentYear);
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/kpis" + base).then(function(res) {
            if (res.status !== "ok" || !res.data) return;
            var d = res.data;
            var grid = buildEl("div", "lab-detail-grid cols-1");

            // Big hero numbers
            var hero = _detailCard(grid, null);
            hero.style.cssText = "background:linear-gradient(135deg, #1e40af, #2563eb);border-radius:var(--radius-md);padding:20px;color:#fff;";
            var heroRow = buildEl("div");
            heroRow.style.cssText = "display:flex;align-items:center;gap:24px;flex-wrap:wrap;";
            var heroItems = [
                {v: d.total, l: "Fiches", icon: "description"},
                {v: d.total_closed, l: "Closes", icon: "check_circle"},
                {v: d.total - d.total_closed, l: "En cours", icon: "pending"},
            ];
            heroItems.forEach(function(h) {
                var item = buildEl("div");
                item.style.cssText = "text-align:center;flex:1;min-width:100px;";
                var iconEl = buildEl("span", "material-symbols-outlined", h.icon);
                iconEl.style.cssText = "font-size:28px;opacity:0.7;display:block;margin-bottom:4px;";
                item.appendChild(iconEl);
                var val = buildEl("div", null, String(h.v));
                val.style.cssText = "font-size:2rem;font-weight:700;line-height:1.1;";
                item.appendChild(val);
                var lbl = buildEl("div", null, h.l);
                lbl.style.cssText = "font-size:0.75rem;opacity:0.8;text-transform:uppercase;letter-spacing:0.5px;";
                item.appendChild(lbl);
                heroRow.appendChild(item);
            });
            hero.appendChild(heroRow);
            if (d.date_range && d.date_range.start) {
                var dtStart = new Date(d.date_range.start);
                var dtEnd = new Date(d.date_range.end);
                var nbJours = Math.round((dtEnd - dtStart) / 86400000) + 1;
                var moisFr = ["janvier","fevrier","mars","avril","mai","juin","juillet","aout","septembre","octobre","novembre","decembre"];
                var fmtDate = function(dt) { return dt.getDate() + " " + moisFr[dt.getMonth()] + " " + dt.getFullYear(); };
                var periodDiv = buildEl("div");
                periodDiv.style.cssText = "text-align:center;margin-top:14px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.15);";
                var iconCal = buildEl("span", "material-symbols-outlined", "date_range");
                iconCal.style.cssText = "font-size:16px;vertical-align:middle;margin-right:6px;opacity:0.8;";
                periodDiv.appendChild(iconCal);
                var periodText = buildEl("span", null, "Activation PC Org : du " + fmtDate(dtStart) + " au " + fmtDate(dtEnd));
                periodText.style.cssText = "font-size:0.82rem;";
                periodDiv.appendChild(periodText);
                var durationBadge = buildEl("span", null, nbJours + " jour" + (nbJours > 1 ? "s" : "") + " d'exploitation");
                durationBadge.style.cssText = "display:inline-block;margin-left:10px;padding:2px 10px;border-radius:12px;background:rgba(255,255,255,0.15);font-size:0.75rem;font-weight:600;";
                periodDiv.appendChild(durationBadge);
                hero.appendChild(periodDiv);
            }

            // Timing + reactivite
            var row2 = buildEl("div", "lab-detail-grid");
            row2.style.marginBottom = "0";

            var cTime = _detailCard(row2, "Reactivite");
            var timingItems = [
                {l: "Delai median", v: d.median_delay_min ? Math.round(d.median_delay_min) + " min" : "N/A", ok: d.median_delay_min && d.median_delay_min <= 30, obj: "< 30 min"},
                {l: "P90 delai", v: d.p90_delay_min ? Math.round(d.p90_delay_min) + " min" : "N/A", ok: d.p90_delay_min && d.p90_delay_min <= 60, obj: "< 60 min"},
                {l: "FCR", v: d.fcr_rate + "%", ok: d.fcr_rate >= 30, obj: "> 30%"},
            ];
            timingItems.forEach(function(t) {
                var row = buildEl("div");
                row.style.cssText = "display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--line);";
                var indicator = buildEl("span", "material-symbols-outlined", t.ok ? "check_circle" : "cancel");
                indicator.style.cssText = "font-size:20px;color:" + (t.ok ? "var(--success)" : "var(--danger)") + ";";
                row.appendChild(indicator);
                var info = buildEl("div");
                info.style.flex = "1";
                info.appendChild(buildEl("div", null, t.l));
                info.firstChild.style.cssText = "font-size:0.82rem;font-weight:500;";
                var objSpan = buildEl("div", null, "Objectif : " + t.obj);
                objSpan.style.cssText = "font-size:0.7rem;color:var(--muted);";
                info.appendChild(objSpan);
                row.appendChild(info);
                var val = buildEl("div", null, t.v);
                val.style.cssText = "font-size:1.1rem;font-weight:700;color:" + (t.ok ? "var(--success)" : "var(--danger)") + ";";
                row.appendChild(val);
                cTime.appendChild(row);
            });

            // SLA gauges
            var cSla = _detailCard(row2, "SLA (Service Level Agreement)");
            var slaItems = [
                {l: "SLA 10 min", v: d.sla10, target: 25, color: "#10b981"},
                {l: "SLA 30 min", v: d.sla30, target: 50, color: "#f59e0b"},
                {l: "SLA 60 min", v: d.sla60, target: 75, color: "#2563eb"},
            ];
            slaItems.forEach(function(s) {
                var slaDiv = buildEl("div");
                slaDiv.style.cssText = "margin-bottom:14px;";
                var labelRow = buildEl("div");
                labelRow.style.cssText = "display:flex;justify-content:space-between;margin-bottom:4px;";
                labelRow.appendChild(buildEl("span", null, s.l));
                labelRow.firstChild.style.cssText = "font-size:0.82rem;font-weight:500;";
                var valBadge = buildEl("span", null, s.v + "%");
                valBadge.style.cssText = "font-size:0.85rem;font-weight:700;color:" + (s.v >= s.target ? "var(--success)" : "var(--danger)") + ";";
                labelRow.appendChild(valBadge);
                slaDiv.appendChild(labelRow);
                // Bar
                var barOuter = buildEl("div");
                barOuter.style.cssText = "position:relative;height:10px;background:var(--line);border-radius:5px;overflow:visible;";
                var barFill = buildEl("div");
                barFill.style.cssText = "height:100%;border-radius:5px;background:" + s.color + ";width:" + Math.min(100, s.v) + "%;";
                barOuter.appendChild(barFill);
                var marker = buildEl("div");
                marker.style.cssText = "position:absolute;top:-2px;height:14px;width:2px;background:#1f2d3d;left:" + s.target + "%;";
                barOuter.appendChild(marker);
                slaDiv.appendChild(barOuter);
                var statusText = buildEl("div", null, s.v >= s.target ? "\u2713 Objectif atteint (" + s.target + "%)" : "\u2717 Sous l'objectif de " + s.target + "%");
                statusText.style.cssText = "font-size:0.7rem;margin-top:3px;font-weight:600;color:" + (s.v >= s.target ? "var(--success)" : "var(--danger)") + ";";
                slaDiv.appendChild(statusText);
                cSla.appendChild(slaDiv);
            });

            grid.appendChild(row2);

            // Canaux + qualite
            var row3 = buildEl("div", "lab-detail-grid cols-3");
            row3.style.marginBottom = "0";

            var cTel = _detailCard(row3, null);
            cTel.style.textAlign = "center";
            var telIcon = buildEl("span", "material-symbols-outlined", "phone_in_talk");
            telIcon.style.cssText = "font-size:32px;color:" + BRAND_PALETTE[0] + ";";
            cTel.appendChild(telIcon);
            var telVal = buildEl("div", "lab-kpi-value", d.tel_share + "%");
            telVal.style.fontSize = "1.4rem";
            cTel.appendChild(telVal);
            cTel.appendChild(buildEl("div", "lab-kpi-label", "Telephone"));

            var cRadio = _detailCard(row3, null);
            cRadio.style.textAlign = "center";
            var radioIcon = buildEl("span", "material-symbols-outlined", "radio");
            radioIcon.style.cssText = "font-size:32px;color:" + BRAND_PALETTE[4] + ";";
            cRadio.appendChild(radioIcon);
            var radioVal = buildEl("div", "lab-kpi-value", d.radio_share + "%");
            radioVal.style.fontSize = "1.4rem";
            cRadio.appendChild(radioVal);
            cRadio.appendChild(buildEl("div", "lab-kpi-label", "Radio"));

            var cQual = _detailCard(row3, null);
            cQual.style.textAlign = "center";
            var qualIcon = buildEl("span", "material-symbols-outlined", "verified");
            qualIcon.style.cssText = "font-size:32px;color:" + (d.pct_perfect > 50 ? "var(--success)" : "var(--danger)") + ";";
            cQual.appendChild(qualIcon);
            var qualVal = buildEl("div", "lab-kpi-value " + (d.pct_perfect > 50 ? "success" : "danger"), d.pct_perfect + "%");
            qualVal.style.fontSize = "1.4rem";
            cQual.appendChild(qualVal);
            cQual.appendChild(buildEl("div", "lab-kpi-label", "Fiches parfaites"));

            grid.appendChild(row3);

            // Explication
            ct.appendChild(grid);
            ct.appendChild(buildEl("p", "lab-detail-desc", "Tableau de bord synthetique de la main courante. La reactivite mesure la capacite a traiter les fiches rapidement (median < 30 min, P90 < 60 min). Le FCR mesure l'autonomie des operateurs. Les SLA fixent des objectifs progressifs de temps de traitement."));
        });
    }

    // --- Detail: Performance ---
    function detailPerformance(ct) {
        _detailHeader(ct, "speed", "Performance - Vue detaillee");
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/performance" + base).then(function(res) {
            if (res.status !== "ok" || !res.data) return;
            var d = res.data;
            var grid = buildEl("div", "lab-detail-grid cols-1");

            // KPIs timing en tete avec objectifs
            var c0 = _detailCard(grid, null);
            var kpiRow = buildEl("div", "lab-kpi-grid");
            kpiRow.style.gridTemplateColumns = "repeat(5, 1fr)";
            var timingItems = [
                {label: "Delai median", value: d.median_delay ? Math.round(d.median_delay) + " min" : "N/A", target: "Obj: < 30 min", ok: d.median_delay && d.median_delay <= 30},
                {label: "P90", value: d.p90_delay ? Math.round(d.p90_delay) + " min" : "N/A", target: "Obj: < 60 min", ok: d.p90_delay && d.p90_delay <= 60},
                {label: "SLA 10 min", value: d.sla.sla10 + "%", target: "Obj: > 25%", ok: d.sla.sla10 >= 25},
                {label: "SLA 30 min", value: d.sla.sla30 + "%", target: "Obj: > 50%", ok: d.sla.sla30 >= 50},
                {label: "SLA 60 min", value: d.sla.sla60 + "%", target: "Obj: > 75%", ok: d.sla.sla60 >= 75},
            ];
            timingItems.forEach(function(it) {
                var kpi = buildEl("div", "lab-kpi");
                kpi.style.cssText = "border-left:3px solid " + (it.ok ? "var(--success)" : "var(--danger)") + ";padding-left:10px;text-align:left;";
                var val = buildEl("div", "lab-kpi-value " + (it.ok ? "success" : "danger"), it.value);
                val.style.fontSize = "1.2rem";
                kpi.appendChild(val);
                kpi.appendChild(buildEl("div", "lab-kpi-label", it.label));
                var target = buildEl("div", null, (it.ok ? "\u2713 " : "\u2717 ") + it.target);
                target.style.cssText = "font-size:0.68rem;margin-top:4px;font-weight:600;color:" + (it.ok ? "var(--success)" : "var(--danger)");
                kpi.appendChild(target);
                kpiRow.appendChild(kpi);
            });
            c0.appendChild(kpiRow);
            grid.appendChild(c0);

            // FCR + SLA cote a cote
            var row2 = buildEl("div", "lab-detail-grid");
            row2.style.marginBottom = "0";

            // FCR
            var c1 = _detailCard(row2, "Bouclage premier contact (FCR)");
            var fcrTotal = d.fcr.fcr + d.fcr.non_fcr;
            var fcrPct = fcrTotal > 0 ? Math.round(100 * d.fcr.fcr / fcrTotal) : 0;
            var fcrRow = buildEl("div");
            fcrRow.style.cssText = "display:flex;align-items:center;gap:12px;margin-bottom:8px;";
            var fcrBig = buildEl("div", "lab-kpi-value", fcrPct + "%");
            fcrBig.style.fontSize = "1.6rem";
            fcrRow.appendChild(fcrBig);
            var fcrInfo = buildEl("div");
            fcrInfo.appendChild(buildEl("div", null, d.fcr.fcr + " fiches resolues au 1er contact"));
            fcrInfo.firstChild.style.cssText = "font-size:0.82rem;font-weight:500;";
            fcrInfo.appendChild(buildEl("div", null, d.fcr.non_fcr + " fiches necessitant une reprise"));
            fcrInfo.lastChild.style.cssText = "font-size:0.78rem;color:var(--muted);";
            fcrRow.appendChild(fcrInfo);
            c1.appendChild(fcrRow);
            var cv1 = document.createElement("canvas"); cv1.id = "detail-fcr";
            c1.appendChild(cv1);
            _detailCharts["detail-fcr"] = new Chart(cv1, {
                type: "doughnut",
                data: {labels: ["FCR (" + d.fcr.fcr + ")", "Non FCR (" + d.fcr.non_fcr + ")"], datasets: [{data: [d.fcr.fcr, d.fcr.non_fcr], backgroundColor: [BRAND_PALETTE[0], "#e2e8f0"]}]},
                options: {responsive: true, cutout: "55%", plugins: {legend: {position: "bottom", labels: {font: {size: 11}}}}}
            });
            c1.appendChild(buildEl("p", "lab-detail-desc", "FCR = fiche fermee par le meme operateur en moins de 30 min. Objectif : FCR > 35%. Un FCR bas indique un manque d'autonomie ou des incidents complexes necessitant transfert."));

            // SLA avec jauge visuelle
            var c2 = _detailCard(row2, "Respect des objectifs de temps (SLA)");
            var slaItems = [
                {label: "SLA 10 min", value: d.sla.sla10, target: 25, color: BRAND_PALETTE[9]},
                {label: "SLA 30 min", value: d.sla.sla30, target: 50, color: BRAND_PALETTE[4]},
                {label: "SLA 60 min", value: d.sla.sla60, target: 75, color: BRAND_PALETTE[0]},
            ];
            slaItems.forEach(function(s) {
                var slaRow = buildEl("div");
                slaRow.style.cssText = "margin-bottom:12px;";
                var labelRow = buildEl("div");
                labelRow.style.cssText = "display:flex;justify-content:space-between;font-size:0.82rem;margin-bottom:4px;";
                labelRow.appendChild(buildEl("span", null, s.label));
                var valSpan = buildEl("span", null, s.value + "%");
                valSpan.style.cssText = "font-weight:700;color:" + (s.value >= s.target ? "var(--success)" : "var(--danger)");
                labelRow.appendChild(valSpan);
                slaRow.appendChild(labelRow);
                // Bar with target marker
                var barWrap = buildEl("div");
                barWrap.style.cssText = "position:relative;height:12px;background:var(--line);border-radius:6px;overflow:visible;";
                var fill = buildEl("div");
                fill.style.cssText = "height:100%;border-radius:6px;background:" + s.color + ";width:" + Math.min(100, s.value) + "%;transition:width 0.6s ease;";
                barWrap.appendChild(fill);
                // Target marker
                var marker = buildEl("div");
                marker.style.cssText = "position:absolute;top:-3px;height:18px;width:2px;background:var(--danger);left:" + s.target + "%;";
                barWrap.appendChild(marker);
                var markerLabel = buildEl("div", null, "obj " + s.target + "%");
                markerLabel.style.cssText = "position:absolute;top:-16px;left:" + s.target + "%;transform:translateX(-50%);font-size:0.6rem;color:var(--danger);font-weight:600;white-space:nowrap;";
                barWrap.appendChild(markerLabel);
                slaRow.appendChild(barWrap);
                c2.appendChild(slaRow);
            });
            c2.appendChild(buildEl("p", "lab-detail-desc", "Les marqueurs rouges indiquent l'objectif cible. Une barre qui depasse le marqueur signifie que l'objectif est atteint. SLA 30 min est l'indicateur principal de performance operationnelle."));

            grid.appendChild(row2);

            // Distribution des delais — pleine largeur avec zones colorees
            if (d.delay_distribution && d.delay_distribution.length) {
                var c3 = _detailCard(grid, "Distribution des delais de traitement");
                var cv3 = document.createElement("canvas"); cv3.id = "detail-delay";
                c3.appendChild(cv3);
                var delayColors = d.delay_distribution.map(function(x) {
                    if (x.range === "0-10") return "#10b981";
                    if (x.range === "10-30") return "#14b8a6";
                    if (x.range === "30-60") return "#f59e0b";
                    if (x.range === "1-2h") return "#f97316";
                    return "#ef4444";
                });
                _detailCharts["detail-delay"] = new Chart(cv3, {
                    type: "bar",
                    data: {
                        labels: d.delay_distribution.map(function(x) { return x.range + " min"; }),
                        datasets: [{data: d.delay_distribution.map(function(x) { return x.count; }), backgroundColor: delayColors, borderRadius: 4}]
                    },
                    options: {responsive: true,
                        plugins: {legend: {display: false}, annotation: {annotations: {
                            zone30: {type: "box", xMin: -0.5, xMax: 1.5, backgroundColor: "rgba(16,185,129,0.06)", borderWidth: 0, label: {display: true, content: "< 30 min", position: "start", font: {size: 9, weight: "bold"}, color: "#1f2d3d"}},
                            zone60: {type: "box", xMin: 1.5, xMax: 2.5, backgroundColor: "rgba(245,158,11,0.06)", borderWidth: 0},
                            lineObj: {type: "line", xMin: 1.5, xMax: 1.5, borderColor: "#dc2626", borderWidth: 2, borderDash: [5, 3], label: {display: true, content: "Objectif 30 min", position: "start", font: {size: 10, weight: "bold"}, color: "#ffffff", backgroundColor: "#dc2626", padding: 4, borderRadius: 3}},
                        }}},
                        scales: {y: {beginAtZero: true, title: {display: true, text: "Nombre de fiches"}}}}
                });
                c3.appendChild(buildEl("p", "lab-detail-desc", "Vert = traite rapidement (< 30 min). Orange = acceptable. Rouge = traitement long necessitant une analyse des causes (complexite, manque de ressources, transferts multiples). La ligne rouge marque l'objectif de 30 minutes."));
            }

            ct.appendChild(grid);
        });
    }

    // --- Detail: Quality ---
    function detailQuality(ct) {
        _detailHeader(ct, "verified", "Qualite de saisie - Vue detaillee");
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/quality" + base).then(function(res) {
            if (res.status !== "ok" || !res.data) return;
            var d = res.data;
            var grid = buildEl("div", "lab-detail-grid");

            // Score global + jauge visuelle
            var c1 = _detailCard(grid, "Score global de qualite");
            var scoreRow = buildEl("div");
            scoreRow.style.cssText = "display:flex;align-items:center;gap:16px;margin-bottom:8px;";
            var bigScore = buildEl("div", "lab-kpi-value " + (d.pct_perfect > 60 ? "success" : d.pct_perfect > 30 ? "warning" : "danger"), d.pct_perfect + "%");
            bigScore.style.fontSize = "2rem";
            scoreRow.appendChild(bigScore);
            var scoreInfo = buildEl("div");
            scoreInfo.appendChild(buildEl("div", null, d.n_perfect + " fiches parfaites sur " + d.total));
            scoreInfo.firstChild.style.cssText = "font-size:0.85rem;font-weight:600;";
            scoreInfo.appendChild(buildEl("div", null, (d.total - d.n_perfect) + " fiches avec au moins un champ manquant"));
            scoreInfo.lastChild.style.cssText = "font-size:0.78rem;color:var(--muted);margin-top:2px;";
            scoreRow.appendChild(scoreInfo);
            c1.appendChild(scoreRow);
            // Progress bar
            var barOuter = buildEl("div");
            barOuter.style.cssText = "height:8px;background:var(--line);border-radius:4px;overflow:hidden;";
            var barInner = buildEl("div");
            var barColor = d.pct_perfect > 60 ? "var(--success)" : d.pct_perfect > 30 ? "var(--warning)" : "var(--danger)";
            barInner.style.cssText = "height:100%;border-radius:4px;background:" + barColor + ";width:" + d.pct_perfect + "%;transition:width 0.8s ease;";
            barOuter.appendChild(barInner);
            c1.appendChild(barOuter);
            c1.appendChild(buildEl("p", "lab-detail-desc", "Une fiche est parfaite quand les 4 champs cles sont remplis : sous-classification, appelant, carroye et service contacte."));

            // Taux par champ — barres horizontales
            var c2 = _detailCard(grid, "Taux de remplissage par champ");
            if (d.fields) {
                Object.entries(d.fields).forEach(function(entry) {
                    var fname = entry[0];
                    var fdata = entry[1];
                    var row = buildEl("div");
                    row.style.cssText = "margin-bottom:10px;";
                    var labelRow = buildEl("div");
                    labelRow.style.cssText = "display:flex;justify-content:space-between;font-size:0.82rem;margin-bottom:3px;";
                    labelRow.appendChild(buildEl("span", null, fname));
                    var pctSpan = buildEl("span", null, fdata.pct + "% (" + fdata.filled + "/" + d.total + ")");
                    pctSpan.style.cssText = "font-weight:600;color:" + (fdata.pct > 70 ? "var(--success)" : fdata.pct > 40 ? "var(--warning)" : "var(--danger)");
                    labelRow.appendChild(pctSpan);
                    row.appendChild(labelRow);
                    var bar = buildEl("div");
                    bar.style.cssText = "height:6px;background:var(--line);border-radius:3px;overflow:hidden;";
                    var fill = buildEl("div");
                    var fillColor = fdata.pct > 70 ? "var(--success)" : fdata.pct > 40 ? "var(--warning)" : "var(--danger)";
                    fill.style.cssText = "height:100%;border-radius:3px;background:" + fillColor + ";width:" + fdata.pct + "%;";
                    bar.appendChild(fill);
                    row.appendChild(bar);
                    c2.appendChild(row);
                });
            }
            c2.appendChild(buildEl("p", "lab-detail-desc", "Le carroye et le service contacte sont souvent les champs les moins remplis. Ameliorer leur saisie permet une meilleure cartographie et analyse des flux."));

            // Progression par jour
            if (d.by_day && d.by_day.length) {
                var c3 = _detailCard(grid, "Progression de la qualite jour par jour");
                c3.classList.add("lab-detail-full");
                var cv3 = document.createElement("canvas"); cv3.id = "detail-quality-daily";
                c3.appendChild(cv3);
                _detailCharts["detail-quality-daily"] = new Chart(cv3, {
                    type: "bar",
                    data: {
                        labels: d.by_day.map(function(x) { return x.date; }),
                        datasets: [
                            {label: "Fiches/jour", data: d.by_day.map(function(x) { return x.total; }),
                                backgroundColor: "rgba(37,99,235,0.3)", borderRadius: 3, yAxisID: "y"},
                            {label: "% parfaites", data: d.by_day.map(function(x) { return x.pct_perfect; }),
                                type: "line", borderColor: d.pct_perfect > 50 ? "#10b981" : "#ef4444",
                                tension: 0.3, pointRadius: 4, yAxisID: "y1"},
                        ]
                    },
                    options: {responsive: true, plugins: {legend: {position: "bottom", labels: {font: {size: 11}}}},
                        scales: {
                            y: {position: "left", beginAtZero: true, title: {display: true, text: "Nb fiches"}},
                            y1: {position: "right", min: 0, max: 100, grid: {display: false}, title: {display: true, text: "% parfaites"},
                                ticks: {callback: function(v) { return v + "%"; }}},
                        }}
                });
                c3.appendChild(buildEl("p", "lab-detail-desc", "Evolution du volume de fiches et du taux de qualite au fil de l'evenement. Une baisse de qualite en fin d'evenement peut indiquer de la fatigue des operateurs."));
            }

            // Classement operateurs
            if (d.by_operator && d.by_operator.length) {
                var c4 = _detailCard(grid, "Qualite par operateur (du moins bon au meilleur)");
                c4.classList.add("lab-detail-full");
                // Table header
                var hdr = buildEl("div");
                hdr.style.cssText = "display:grid;grid-template-columns:1fr 60px 70px 1fr;gap:8px;padding:6px 0;border-bottom:2px solid var(--line);font-size:0.72rem;font-weight:700;text-transform:uppercase;color:var(--muted);";
                hdr.appendChild(buildEl("span", null, "Operateur"));
                hdr.appendChild(buildEl("span", null, "Fiches"));
                hdr.appendChild(buildEl("span", null, "Parfaites"));
                hdr.appendChild(buildEl("span", null, "Detail par champ"));
                c4.appendChild(hdr);
                d.by_operator.forEach(function(op) {
                    var row = buildEl("div");
                    row.style.cssText = "display:grid;grid-template-columns:1fr 60px 70px 1fr;gap:8px;padding:6px 0;font-size:0.8rem;border-bottom:1px solid var(--line);align-items:center;";
                    var nameEl = buildEl("span", null, op.operator);
                    nameEl.style.fontWeight = "500";
                    row.appendChild(nameEl);
                    row.appendChild(buildEl("span", null, String(op.total)));
                    var pctEl = buildEl("span", null, op.pct_perfect + "%");
                    var pctColor = op.pct_perfect > 60 ? "var(--success)" : op.pct_perfect > 30 ? "var(--warning)" : "var(--danger)";
                    pctEl.style.cssText = "font-weight:700;color:" + pctColor;
                    row.appendChild(pctEl);
                    // Mini bars per field
                    var barsDiv = buildEl("div");
                    barsDiv.style.cssText = "display:flex;gap:4px;align-items:center;flex-wrap:wrap;";
                    if (op.fields) {
                        Object.entries(op.fields).forEach(function(entry) {
                            var pill = buildEl("span", null, entry[0].substring(0, 6) + " " + entry[1] + "%");
                            var pc = entry[1];
                            pill.style.cssText = "font-size:0.65rem;padding:1px 5px;border-radius:8px;background:" + (pc > 70 ? "var(--success-light)" : pc > 40 ? "var(--warning-light)" : "var(--danger-light)") + ";color:" + (pc > 70 ? "var(--success)" : pc > 40 ? "#b45309" : "var(--danger)") + ";font-weight:600;white-space:nowrap;";
                            barsDiv.appendChild(pill);
                        });
                    }
                    row.appendChild(barsDiv);
                    c4.appendChild(row);
                });
                c4.appendChild(buildEl("p", "lab-detail-desc", "Classement du moins bon au meilleur. Les pills colorees montrent le taux de remplissage par champ pour chaque operateur. Objectif : identifier les operateurs a former en priorite."));
            }

            ct.appendChild(grid);
        });
    }

    // --- Detail: Meteo ---
    function detailMeteo(ct) {
        _detailHeader(ct, "thermostat", "Correlation Meteo x Incidents - Vue detaillee");
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/meteo-cross" + base).then(function(res) {
            if (res.status !== "ok" || !res.data) return;
            var d = res.data;
            var grid = buildEl("div", "lab-detail-grid");
            // Scatter
            var c1 = _detailCard(grid, "Temperature vs Volume incidents");
            var cv1 = document.createElement("canvas"); cv1.id = "detail-scatter-temp";
            c1.appendChild(cv1);
            var pts = (d.days || []).filter(function(x) { return x.tmax !== null; });
            if (pts.length >= 2) {
                _detailCharts["detail-scatter-temp"] = new Chart(cv1, {
                    type: "scatter", data: {datasets: [{label: "Temp vs Incidents", data: pts.map(function(p) { return {x: p.tmax, y: p.incidents}; }), backgroundColor: "rgba(239,68,68,0.6)", pointRadius: 8}]},
                    options: {responsive: true, plugins: {legend: {display: false}}, scales: {x: {title: {display: true, text: "Temperature max (C)"}}, y: {title: {display: true, text: "Incidents"}}}}
                });
            }
            var c2 = _detailCard(grid, "Pluie vs Volume incidents");
            var cv2 = document.createElement("canvas"); cv2.id = "detail-scatter-rain";
            c2.appendChild(cv2);
            var rpts = (d.days || []).filter(function(x) { return x.rain !== null; });
            if (rpts.length >= 2) {
                _detailCharts["detail-scatter-rain"] = new Chart(cv2, {
                    type: "scatter", data: {datasets: [{label: "Pluie vs Incidents", data: rpts.map(function(p) { return {x: p.rain, y: p.incidents}; }), backgroundColor: "rgba(14,165,233,0.6)", pointRadius: 8}]},
                    options: {responsive: true, plugins: {legend: {display: false}}, scales: {x: {title: {display: true, text: "Precipitations (mm)"}}, y: {title: {display: true, text: "Incidents"}}}}
                });
            }
            // Chronologie
            var c3 = _detailCard(grid, "Chronologie journaliere");
            c3.classList.add("lab-detail-full");
            var cv3 = document.createElement("canvas"); cv3.id = "detail-meteo-chrono";
            c3.appendChild(cv3);
            if (d.days && d.days.length) {
                _detailCharts["detail-meteo-chrono"] = new Chart(cv3, {
                    data: {labels: d.days.map(function(x) { return x.date; }), datasets: [
                        {label: "Incidents", data: d.days.map(function(x) { return x.incidents; }), type: "bar", backgroundColor: "rgba(37,99,235,0.5)", yAxisID: "y", borderRadius: 3},
                        {label: "Tmax", data: d.days.map(function(x) { return x.tmax; }), type: "line", borderColor: "#ef4444", yAxisID: "y1", tension: 0.3, pointRadius: 4},
                        {label: "Pluie (mm)", data: d.days.map(function(x) { return x.rain; }), type: "line", borderColor: "#0ea5e9", yAxisID: "y1", tension: 0.3, pointRadius: 4},
                    ]},
                    options: {responsive: true, plugins: {legend: {position: "bottom"}}, scales: {y: {position: "left", beginAtZero: true}, y1: {position: "right", grid: {display: false}}}}
                });
            }
            // Pearson
            var desc = "Correlation de Pearson : ";
            if (d.pearson_temp !== null && d.pearson_temp !== undefined) desc += "temperature r=" + d.pearson_temp + " ";
            if (d.pearson_rain !== null && d.pearson_rain !== undefined) desc += "pluie r=" + d.pearson_rain;
            desc += ". Un coefficient > 0.5 indique une correlation significative entre la meteo et le volume d'incidents.";
            c3.appendChild(buildEl("p", "lab-detail-desc", desc));
            ct.appendChild(grid);
        });
    }

    // --- Detail: Text / Wordcloud ---
    function detailText(ct) {
        _detailHeader(ct, "text_fields", "Analyse textuelle - Vue detaillee");
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/text" + base).then(function(res) {
            if (res.status !== "ok" || !res.data) return;
            var d = res.data;
            var grid = buildEl("div", "lab-detail-grid cols-1");
            // Wordcloud
            var c1 = _detailCard(grid, "Nuage de mots (120 termes les plus frequents)");
            var wc = document.createElement("canvas"); wc.id = "detail-wordcloud";
            wc.width = 700; wc.height = 250;
            wc.style.cssText = "width:100%;max-height:250px;";
            c1.appendChild(wc);
            c1.appendChild(buildEl("p", "lab-detail-desc", "Les mots les plus utilises dans les champs texte, description et commentaires des fiches. Les noms d'operateurs et les stopwords francais sont exclus."));
            if (d.wordcloud && d.wordcloud.length && typeof WordCloud !== "undefined") {
                setTimeout(function() {
                    var maxN = d.wordcloud[0].n;
                    WordCloud(wc, {
                        list: d.wordcloud.map(function(w) { return [w.t, Math.max(10, Math.round(w.n / maxN * 55))]; }),
                        gridSize: 6, weightFactor: 1, fontFamily: "Outfit, sans-serif",
                        color: function() { return BRAND_PALETTE[Math.floor(Math.random() * BRAND_PALETTE.length)]; },
                        backgroundColor: "transparent", rotateRatio: 0.2,
                    });
                }, 100);
            }
            // Treemap
            var c2 = _detailCard(grid, "Repartition des sous-classifications (treemap)");
            var cv2 = document.createElement("canvas"); cv2.id = "detail-treemap";
            cv2.style.maxHeight = "260px";
            c2.appendChild(cv2);
            c2.appendChild(buildEl("p", "lab-detail-desc", "Chaque rectangle est proportionnel au nombre de fiches de cette sous-classification. Permet d'identifier visuellement les problematiques dominantes."));
            if (d.treemap && d.treemap.length) {
                var tmData = d.treemap.slice(0, 40).map(function(t) { return {label: t.label, value: t.n}; });
                _detailCharts["detail-treemap"] = new Chart(cv2, {
                    type: "treemap",
                    data: {datasets: [{tree: tmData, key: "value", labels: {display: true, formatter: function(ctx) { return ctx.raw._data ? ctx.raw._data.label : ""; }, font: {size: 10}}, backgroundColor: function(ctx) { return BRAND_PALETTE[ctx.dataIndex % BRAND_PALETTE.length]; }, borderWidth: 1, borderColor: "#fff"}]},
                    options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}}
                });
            }
            ct.appendChild(grid);
        });
    }

    // --- Detail: Categories ---
    function detailCategories(ct) {
        _detailHeader(ct, "category", "Categories - Vue detaillee");
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/categories" + base).then(function(res) {
            if (res.status !== "ok" || !res.data) return;
            var d = res.data;
            var grid = buildEl("div", "lab-detail-grid");

            // Sources PCO — full width, proper height
            var c1 = _detailCard(grid, "Sources PCO (top 15)");
            c1.classList.add("lab-detail-full");
            var wrapper1 = buildEl("div");
            var srcCount = d.sources ? d.sources.length : 0;
            wrapper1.style.cssText = "position:relative;height:" + Math.max(200, srcCount * 28) + "px;";
            var cv1 = document.createElement("canvas"); cv1.id = "detail-sources";
            wrapper1.appendChild(cv1);
            c1.appendChild(wrapper1);
            if (d.sources && d.sources.length) {
                _detailCharts["detail-sources"] = new Chart(cv1, {
                    type: "bar",
                    data: {
                        labels: d.sources.map(function(s) { return s.label.replace("PCO.", ""); }),
                        datasets: [{
                            data: d.sources.map(function(s) { return s.n; }),
                            backgroundColor: d.sources.map(function(_, i) { return BRAND_PALETTE[i % BRAND_PALETTE.length]; }),
                            borderRadius: 4
                        }]
                    },
                    options: {
                        indexAxis: "y", responsive: true, maintainAspectRatio: false,
                        plugins: {
                            legend: {display: false},
                            tooltip: {callbacks: {label: function(ctx) { return ctx.raw + " fiches"; }}}
                        },
                        scales: {
                            x: {beginAtZero: true, grid: {color: "rgba(0,0,0,0.05)"}},
                            y: {ticks: {font: {size: 12, weight: "bold"}}}
                        }
                    }
                });
            }
            c1.appendChild(buildEl("p", "lab-detail-desc", "Repartition des categories de la main courante PCO. Chaque source correspond a un type d'incident (Technique, Securite, Information, Secours, etc.)."));

            // Row 2: Channels + summary stats
            var c2 = _detailCard(grid, "Canaux de contact");
            var cv2 = document.createElement("canvas"); cv2.id = "detail-channels";
            c2.appendChild(cv2);
            if (d.channels) {
                var total = Object.values(d.channels).reduce(function(a, b) { return a + b; }, 0);
                _detailCharts["detail-channels"] = new Chart(cv2, {
                    type: "doughnut",
                    data: {
                        labels: Object.keys(d.channels).map(function(k) { return k + " (" + Math.round(100 * d.channels[k] / total) + "%)"; }),
                        datasets: [{data: Object.values(d.channels), backgroundColor: [BRAND_PALETTE[0], BRAND_PALETTE[4], "#e2e8f0"]}]
                    },
                    options: {responsive: true, cutout: "50%", plugins: {legend: {position: "bottom", labels: {font: {size: 11}}}}}
                });
            }
            c2.appendChild(buildEl("p", "lab-detail-desc", "Telephone vs Radio vs Autre. Un fort taux radio indique des equipes terrain, le telephone vient souvent des appelants externes."));

            // Sous-class summary (top 10 in a clean list)
            var c2b = _detailCard(grid, "Top sous-classifications");
            if (d.sous_classifications && d.sous_classifications.length) {
                var topSc = d.sous_classifications.slice(-15).reverse();
                topSc.forEach(function(sc, idx) {
                    var row = buildEl("div");
                    row.style.cssText = "display:flex;align-items:center;gap:8px;padding:4px 0;" + (idx < topSc.length - 1 ? "border-bottom:1px solid var(--line);" : "");
                    var rank = buildEl("span", null, String(idx + 1));
                    rank.style.cssText = "font-size:0.7rem;color:var(--muted);min-width:18px;text-align:right;";
                    row.appendChild(rank);
                    var label = buildEl("span", null, sc.label);
                    label.style.cssText = "flex:1;font-size:0.8rem;";
                    row.appendChild(label);
                    var val = buildEl("span", null, String(sc.n));
                    val.style.cssText = "font-weight:700;font-size:0.85rem;color:var(--brand);";
                    row.appendChild(val);
                    c2b.appendChild(row);
                });
            }

            // Sous-class full chart (scrollable)
            var c3 = _detailCard(grid, "Toutes les sous-classifications (detail complet)");
            c3.classList.add("lab-detail-full");
            var wrapper3 = buildEl("div");
            var scCount = d.sous_classifications ? d.sous_classifications.length : 0;
            wrapper3.style.cssText = "position:relative;height:" + Math.max(200, scCount * 22) + "px;max-height:450px;overflow-y:auto;";
            var cv3 = document.createElement("canvas"); cv3.id = "detail-sous-class";
            cv3.style.height = Math.max(200, scCount * 22) + "px";
            wrapper3.appendChild(cv3);
            c3.appendChild(wrapper3);
            if (d.sous_classifications && d.sous_classifications.length) {
                _detailCharts["detail-sous-class"] = new Chart(cv3, {
                    type: "bar",
                    data: {
                        labels: d.sous_classifications.map(function(s) { return s.label; }),
                        datasets: [{data: d.sous_classifications.map(function(s) { return s.n; }), backgroundColor: BRAND_PALETTE[3], borderRadius: 2}]
                    },
                    options: {indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}, scales: {x: {beginAtZero: true}}}
                });
            }

            ct.appendChild(grid);
        });
    }

    // --- Detail: Services ---
    function detailServices(ct) {
        _detailHeader(ct, "support_agent", "Services contactes - Vue detaillee");
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/services" + base).then(function(res) {
            if (res.status !== "ok" || !res.data) return;
            var d = res.data;
            var grid = buildEl("div", "lab-detail-grid cols-1");

            // Repartition doughnut
            var c1 = _detailCard(grid, "Repartition des services contactes");
            var cv1 = document.createElement("canvas"); cv1.id = "detail-svc-split";
            c1.appendChild(cv1);
            c1.appendChild(buildEl("p", "lab-detail-desc", "Services les plus sollicites. Permet d'identifier les services sous pression et d'ajuster les moyens."));
            if (d.split && d.split.length) {
                var total = d.split.reduce(function(a, s) { return a + s.n; }, 0);
                _detailCharts["detail-svc-split"] = new Chart(cv1, {
                    type: "doughnut",
                    data: {
                        labels: d.split.map(function(s) { return s.label + " (" + Math.round(100 * s.n / total) + "%)"; }),
                        datasets: [{data: d.split.map(function(s) { return s.n; }), backgroundColor: BRAND_PALETTE}]
                    },
                    options: {responsive: true, cutout: "45%", plugins: {legend: {position: "right", labels: {font: {size: 11}}}}}
                });
            }

            // P90 par service — hauteur adaptee
            var c2 = _detailCard(grid, "Delais P90 par service (temps au 90e percentile)");
            if (d.p90 && d.p90.length) {
                var wrapper = buildEl("div");
                wrapper.style.cssText = "position:relative;height:" + Math.max(200, d.p90.length * 30) + "px;";
                var cv2 = document.createElement("canvas"); cv2.id = "detail-svc-p90";
                wrapper.appendChild(cv2);
                c2.appendChild(wrapper);
                _detailCharts["detail-svc-p90"] = new Chart(cv2, {
                    type: "bar",
                    data: {
                        labels: d.p90.map(function(s) { return s.label; }),
                        datasets: [
                            {label: "P90 (min)", data: d.p90.map(function(s) { return s.p90; }), backgroundColor: BRAND_PALETTE[5], borderRadius: 3},
                            {label: "Mediane (min)", data: d.p90.map(function(s) { return s.median; }), backgroundColor: BRAND_PALETTE[0], borderRadius: 3},
                        ]
                    },
                    options: {
                        indexAxis: "y", responsive: true, maintainAspectRatio: false,
                        plugins: {legend: {position: "bottom", labels: {font: {size: 11}}}},
                        scales: {x: {beginAtZero: true, title: {display: true, text: "Minutes"}}, y: {ticks: {font: {size: 11}}}}
                    }
                });
            }
            c2.appendChild(buildEl("p", "lab-detail-desc", "Comparaison P90 vs mediane. Un ecart important P90/mediane indique des cas extremes a investiguer."));

            ct.appendChild(grid);
        });
    }

    // --- Detail: Intervenants ---
    function detailIntervenants(ct) {
        _detailHeader(ct, "engineering", "Intervenants - Vue detaillee");
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/intervenants" + base).then(function(res) {
            if (res.status !== "ok" || !res.data) return;
            var d = res.data;
            var grid = buildEl("div", "lab-detail-grid cols-1");
            // Stat line
            var statCard = _detailCard(grid, null);
            statCard.appendChild(buildEl("span", null, "Moyenne d'intervenants par fiche : "));
            var statVal = buildEl("span", null, String(d.avg_per_fiche || 0));
            statVal.style.cssText = "font-weight:700;color:var(--brand);font-size:1.1rem;";
            statCard.appendChild(statVal);
            // Top 20
            var c1 = _detailCard(grid, "Top 20 intervenants (tous niveaux confondus)");
            var cv1 = document.createElement("canvas"); cv1.id = "detail-interv-top";
            c1.appendChild(cv1);
            if (d.top && d.top.length) {
                _detailCharts["detail-interv-top"] = new Chart(cv1, {type: "bar", data: {labels: d.top.map(function(i) { return i.label; }), datasets: [{data: d.top.map(function(i) { return i.n; }), backgroundColor: BRAND_PALETTE[1], borderRadius: 3}]}, options: {indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}}});
            }
            // Levels stacked
            var c2 = _detailCard(grid, "Repartition par niveau d'escalade (stacked)");
            var cv2 = document.createElement("canvas"); cv2.id = "detail-interv-levels";
            c2.appendChild(cv2);
            c2.appendChild(buildEl("p", "lab-detail-desc", "Les niveaux 3+ indiquent une escalade significative necessitant l'intervention de ressources supplementaires."));
            if (d.levels && d.levels.length) {
                var niveaux = ["Niveau 1", "Niveau 2", "Niveau 3", "Niveau 4", "Niveau 5"];
                var names = []; d.levels.forEach(function(l) { if (names.indexOf(l.intervenant) < 0) names.push(l.intervenant); }); names = names.slice(0, 15);
                var ds = niveaux.map(function(niv, idx) { return {label: niv, data: names.map(function(n) { var m = d.levels.find(function(l) { return l.intervenant === n && l.niveau === niv; }); return m ? m.n : 0; }), backgroundColor: BRAND_PALETTE[idx]}; });
                _detailCharts["detail-interv-levels"] = new Chart(cv2, {type: "bar", data: {labels: names, datasets: ds}, options: {indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: {legend: {position: "bottom", labels: {font: {size: 10}}}}, scales: {x: {stacked: true}, y: {stacked: true}}}});
            }
            ct.appendChild(grid);
        });
    }

    // --- Detail: Escalation ---
    function detailEscalation(ct) {
        _detailHeader(ct, "account_tree", "Chaine de traitement - Vue detaillee");
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/escalation" + base).then(function(res) {
            if (res.status !== "ok" || !res.data) return;
            var d = res.data;
            var grid = buildEl("div", "lab-detail-grid cols-1");

            // Niveaux d'intervention — bar chart horizontal
            if (d.levels_count) {
                var c0 = _detailCard(grid, "Mobilisation par niveau d'intervention");
                var cv0 = document.createElement("canvas"); cv0.id = "detail-levels-bar";
                c0.appendChild(cv0);
                var lvlLabels = [];
                var lvlValues = [];
                var lvlColors = [];
                Object.entries(d.levels_count).forEach(function(entry, idx) {
                    if (entry[1] > 0) {
                        lvlLabels.push(entry[0]);
                        lvlValues.push(entry[1]);
                        lvlColors.push(BRAND_PALETTE[idx]);
                    }
                });
                _detailCharts["detail-levels-bar"] = new Chart(cv0, {
                    type: "bar",
                    data: {labels: lvlLabels, datasets: [{data: lvlValues, backgroundColor: lvlColors, borderRadius: 4}]},
                    options: {indexAxis: "y", responsive: true, plugins: {legend: {display: false}}, scales: {x: {beginAtZero: true}, y: {ticks: {font: {size: 12, weight: "bold"}}}}}
                });
                c0.appendChild(buildEl("p", "lab-detail-desc", "Nombre d'engagements par niveau. Le niveau 1 est le premier intervenant sur le terrain. Les niveaux 3 et plus indiquent une mobilisation importante necessitant des renforts ou une coordination inter-services."));
            }

            // Sankey — full width, generous height
            if (d.flows && d.flows.length) {
                var c1 = _detailCard(grid, "Flux de traitement : categorie \u2192 sous-classification \u2192 service");
                var wrapper = buildEl("div");
                wrapper.style.cssText = "position:relative;height:620px;";
                var cv1 = document.createElement("canvas"); cv1.id = "detail-sankey";
                wrapper.appendChild(cv1);
                c1.appendChild(wrapper);
                var topFlows = d.flows.sort(function(a, b) { return b.flow - a.flow; }).slice(0, 15);
                _detailCharts["detail-sankey"] = new Chart(cv1, {
                    type: "sankey",
                    data: {datasets: [{
                        data: topFlows.map(function(f) { return {from: f.from, to: f.to, flow: f.flow}; }),
                        colorFrom: function(c) { return BRAND_PALETTE[0]; },
                        colorTo: function(c) { return BRAND_PALETTE[2]; },
                        colorMode: "gradient",
                        size: "min",
                        labels: {font: {size: 11}},
                    }]},
                    options: {responsive: true, maintainAspectRatio: false}
                });
                c1.appendChild(buildEl("p", "lab-detail-desc", "Visualisation des 15 principaux flux de traitement. La largeur de chaque lien est proportionnelle au nombre de fiches. Lecture de gauche a droite : categorie d'origine \u2192 type de probleme \u2192 service mobilise."));
            }

            ct.appendChild(grid);
        });
    }

    // --- Stubs for remaining details (show a simple message) ---
    function detailEffectifs(ct) {
        _detailHeader(ct, "groups", "Effectifs x Incidents - Vue detaillee");
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/effectifs-cross" + base).then(function(res) {
            if (res.status !== "ok" || !res.data || !res.data.available) {
                ct.appendChild(buildEl("p", "lab-detail-desc", "Pas de donnees calendrier disponibles pour cet evenement."));
                return;
            }
            var d = res.data;
            var grid = buildEl("div", "lab-detail-grid");
            var c1 = _detailCard(grid, "Effectifs par zone et creneau");
            c1.classList.add("lab-detail-full");
            var total_secu = 0, total_accueil = 0;
            d.zones.forEach(function(z) { total_secu += z.agents_secu || 0; total_accueil += z.agents_accueil || 0; });
            c1.appendChild(buildEl("p", null, "Total creneaux securite : " + total_secu + " | Accueil : " + total_accueil));
            c1.lastChild.style.cssText = "font-size:0.85rem;font-weight:600;margin-bottom:8px;";
            c1.appendChild(buildEl("p", "lab-detail-desc", "Le croisement effectifs/incidents permet d'identifier les zones et creneaux sous-dimensionnes. A terme, le ratio incidents/agent par creneau de 30 minutes sera calcule pour cibler les renforts."));
            ct.appendChild(grid);
        });
    }
    function detailAppelants(ct) {
        _detailHeader(ct, "call", "Appelants - Vue detaillee");
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/appelants" + base).then(function(res) {
            if (res.status !== "ok" || !res.data) return;
            var grid = buildEl("div", "lab-detail-grid cols-1");
            var c1 = _detailCard(grid, "Top 20 appelants (normalises)");
            if (res.data.top && res.data.top.length) {
                var count = res.data.top.length;
                var wrapper = buildEl("div");
                wrapper.style.cssText = "position:relative;height:" + Math.max(300, count * 32) + "px;";
                var cv1 = document.createElement("canvas"); cv1.id = "detail-appelants";
                wrapper.appendChild(cv1);
                c1.appendChild(wrapper);
                _detailCharts["detail-appelants"] = new Chart(cv1, {
                    type: "bar",
                    data: {
                        labels: res.data.top.map(function(a) { return a.label; }),
                        datasets: [{data: res.data.top.map(function(a) { return a.n; }),
                            backgroundColor: res.data.top.map(function(_, i) { return BRAND_PALETTE[i % BRAND_PALETTE.length]; }),
                            borderRadius: 4}]
                    },
                    options: {indexAxis: "y", responsive: true, maintainAspectRatio: false,
                        plugins: {legend: {display: false}},
                        scales: {x: {beginAtZero: true}, y: {ticks: {font: {size: 12, weight: "bold"}}}}}
                });
            }
            c1.appendChild(buildEl("p", "lab-detail-desc", "Les appelants sont normalises par alias (ex: toutes les variantes de PCO, PC ORG, CGO sont regroupees). Permet d'identifier les interlocuteurs les plus actifs."));
            ct.appendChild(grid);
        });
    }
    function detailZones(ct) {
        _detailHeader(ct, "warning", "Zones vulnerables - Vue detaillee");
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        apiGet("/api/analyse-ops/zones-vulnerability" + base).then(function(res) {
            if (res.status !== "ok" || !res.data) return;
            var d = res.data;
            var grid = buildEl("div", "lab-detail-grid cols-1");

            // Score chart — full width, height proportionnelle
            var c1 = _detailCard(grid, "Classement par score de vulnerabilite");
            if (d.zones && d.zones.length) {
                var wrapper = buildEl("div");
                wrapper.style.cssText = "position:relative;height:" + Math.max(200, d.zones.length * 32) + "px;";
                var cv1 = document.createElement("canvas"); cv1.id = "detail-zones";
                wrapper.appendChild(cv1);
                c1.appendChild(wrapper);
                var colors = d.zones.map(function(z) { return z.score > 70 ? "#ef4444" : z.score > 40 ? "#f59e0b" : "#10b981"; });
                _detailCharts["detail-zones"] = new Chart(cv1, {
                    type: "bar",
                    data: {
                        labels: d.zones.map(function(z) { return z.zone.split("/").pop(); }),
                        datasets: [{
                            label: "Score",
                            data: d.zones.map(function(z) { return z.score; }),
                            backgroundColor: colors,
                            borderRadius: 4
                        }]
                    },
                    options: {
                        indexAxis: "y", responsive: true, maintainAspectRatio: false,
                        plugins: {legend: {display: false}, tooltip: {
                            callbacks: {label: function(ctx) {
                                var z = d.zones[ctx.dataIndex];
                                return "Score: " + z.score + " | Volume: " + z.volume + " | Severite: " + z.sev_mean + " | P90: " + z.p90_delay + " min";
                            }}
                        }},
                        scales: {x: {max: 100, title: {display: true, text: "Score de vulnerabilite (0-100)"}}, y: {ticks: {font: {size: 12, weight: "bold"}}}}
                    }
                });
            }

            // Detail tableau par zone
            var c2 = _detailCard(grid, "Detail des metriques par zone");
            if (d.zones && d.zones.length) {
                // En-tete
                var hdr = buildEl("div");
                hdr.style.cssText = "display:grid;grid-template-columns:1fr 70px 70px 80px 60px;gap:8px;padding:6px 0;border-bottom:2px solid var(--line);font-size:0.72rem;font-weight:700;text-transform:uppercase;color:var(--muted);letter-spacing:0.3px;";
                hdr.appendChild(buildEl("span", null, "Zone"));
                hdr.appendChild(buildEl("span", null, "Volume"));
                hdr.appendChild(buildEl("span", null, "Severite"));
                hdr.appendChild(buildEl("span", null, "P90 delai"));
                hdr.appendChild(buildEl("span", null, "Score"));
                c2.appendChild(hdr);
                d.zones.forEach(function(z) {
                    var row = buildEl("div");
                    row.style.cssText = "display:grid;grid-template-columns:1fr 70px 70px 80px 60px;gap:8px;padding:6px 0;font-size:0.82rem;border-bottom:1px solid var(--line);align-items:center;";
                    var name = buildEl("span", null, z.zone.split("/").pop());
                    name.style.fontWeight = "500";
                    row.appendChild(name);
                    row.appendChild(buildEl("span", null, String(z.volume)));
                    row.appendChild(buildEl("span", null, String(z.sev_mean)));
                    row.appendChild(buildEl("span", null, z.p90_delay + " min"));
                    var scoreBadge = buildEl("span", null, String(z.score));
                    var scoreColor = z.score > 70 ? "var(--danger)" : z.score > 40 ? "#b45309" : "var(--success)";
                    scoreBadge.style.cssText = "font-weight:700;color:" + scoreColor + ";";
                    row.appendChild(scoreBadge);
                    c2.appendChild(row);
                });
            }
            c2.appendChild(buildEl("p", "lab-detail-desc", "Score = volume(40%) + severite moyenne(30%) + P90 delai(30%). Les zones en rouge (>70) meritent un renforcement des effectifs ou une reorganisation du dispositif pour les prochaines editions."));

            ct.appendChild(grid);
        });
    }
    function detailANPR(ct) {
        _detailHeader(ct, "directions_car", "ANPR - Vue detaillee");
        ct.appendChild(buildEl("p", "lab-detail-desc", "Le croisement ANPR sera disponible quand les donnees Hikvision seront synchronisees pour la periode de l'evenement."));
    }

    // =========================================================================
    // EVENT CHANGE LISTENER
    // =========================================================================
    function initEventChangeListener() {
        setInterval(function() {
            if (window.selectedEvent && window.selectedYear) {
                if (window.selectedEvent !== currentEvent || window.selectedYear !== currentYear) {
                    currentEvent = window.selectedEvent;
                    currentYear = window.selectedYear;
                    var label = $("lab-event-label");
                    if (label) label.textContent = currentEvent + " " + currentYear;
                    loadCachedAnalysis();
                }
            }
        }, 500);
    }

    // =========================================================================
    // COMPUTE BUTTON
    // =========================================================================
    function initComputeButton() {
        var btn = $("lab-compute-btn");
        if (!btn) return;
        btn.addEventListener("click", function() {
            if (computing) return;
            if (!currentEvent || !currentYear) {
                if (window.selectedEvent) currentEvent = window.selectedEvent;
                if (window.selectedYear) currentYear = window.selectedYear;
            }
            if (!currentEvent || !currentYear) return;
            triggerCompute();
        });
    }

    function triggerCompute() {
        computing = true;
        var statusEl = $("lab-status");
        var btn = $("lab-compute-btn");
        if (statusEl) statusEl.style.display = "flex";
        if (btn) btn.disabled = true;

        apiPost("/api/analyse-ops/compute", {event: currentEvent, year: currentYear})
            .then(function() { pollStatus(); })
            .catch(function() {
                computing = false;
                if (statusEl) statusEl.style.display = "none";
                if (btn) btn.disabled = false;
            });
    }

    function pollStatus() {
        var statusText = $("lab-status-text");
        var statusEl = $("lab-status");
        var btn = $("lab-compute-btn");

        apiGet("/api/analyse-ops/status")
            .then(function(data) {
                if (data.status === "computing") {
                    if (statusText) statusText.textContent = "Calcul... " + (data.progress || 0) + "%";
                    setTimeout(pollStatus, 2000);
                } else if (data.status === "done") {
                    computing = false;
                    if (statusEl) statusEl.style.display = "none";
                    if (btn) btn.disabled = false;
                    loadAllModules();
                } else if (data.status === "error") {
                    computing = false;
                    if (statusText) statusText.textContent = "Erreur: " + (data.error || "");
                    if (btn) btn.disabled = false;
                    setTimeout(function() { if (statusEl) statusEl.style.display = "none"; }, 5000);
                } else {
                    computing = false;
                    if (statusEl) statusEl.style.display = "none";
                    if (btn) btn.disabled = false;
                }
            })
            .catch(function() { setTimeout(pollStatus, 3000); });
    }

    // =========================================================================
    // LOAD CACHED / ALL MODULES
    // =========================================================================
    function showEmptyState() {
        var center = $("lab-center");
        if (!center) return;
        var existing = center.querySelector(".lab-empty-state");
        if (existing) return;
        var empty = buildEl("div", "lab-empty-state");
        var icon = buildEl("span", "material-symbols-outlined", "biotech");
        empty.appendChild(icon);
        empty.appendChild(buildEl("p", null, "Selectionnez un evenement et cliquez Analyser pour generer le rapport."));
        // Insert after tabs
        var firstView = center.querySelector(".lab-view");
        if (firstView) center.insertBefore(empty, firstView);
        else center.appendChild(empty);
    }

    function removeEmptyState() {
        var center = $("lab-center");
        if (!center) return;
        var empty = center.querySelector(".lab-empty-state");
        if (empty) empty.remove();
    }

    function removeSkeletons() {
        document.querySelectorAll(".lab-skeleton").forEach(function(el) { el.remove(); });
    }

    function loadCachedAnalysis() {
        if (!currentEvent || !currentYear) return;
        showEmptyState();
        apiGet("/api/analyse-ops/kpis?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear))
            .then(function(res) {
                if (res.status === "ok" && res.data) {
                    removeEmptyState();
                    loadAllModules();
                }
            })
            .catch(function() {});
    }

    function loadAllModules() {
        removeEmptyState();
        removeSkeletons();
        var base = "?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear);
        var modules = [
            {name: "kpis", render: renderKPIs},
            {name: "temporal", render: renderTemporal},
            {name: "performance", render: renderPerformance},
            {name: "categories", render: renderCategories},
            {name: "services", render: renderServices},
            {name: "intervenants", render: renderIntervenants},
            {name: "text", render: renderText},
            {name: "appelants", render: renderAppelants},
            {name: "geographic", render: renderGeographic},
            {name: "operators", render: function() {}},
            {name: "waze-cross", render: renderWazeCross},
            {name: "meteo-cross", render: renderMeteoCross},
            {name: "zones-vulnerability", render: renderZonesVuln},
            {name: "escalation", render: renderEscalation},
            {name: "convergence", render: renderConvergence},
            {name: "comparative", render: renderComparative},
            {name: "affluence-cross", render: renderAffluenceChart},
            {name: "effectifs-cross", render: renderEffectifs},
            {name: "anpr-cross", render: renderANPR},
            {name: "network", render: renderNetwork},
        ];
        modules.forEach(function(m) {
            apiGet("/api/analyse-ops/" + m.name + base)
                .then(function(res) {
                    if (res.status === "ok" && res.data) m.render(res.data);
                })
                .catch(function() {});
        });
    }

    // =========================================================================
    // RENDER: KPIs
    // =========================================================================
    function renderKPIs(data) {
        // Also render quality widget from KPI data
        renderQuality(data);

        var grid = $("lab-kpis-grid");
        if (!grid) return;
        var items = [
            {label: "Total fiches", value: data.total},
            {label: "Closes", value: data.total_closed},
            {label: "Delai median", value: data.median_delay_min ? Math.round(data.median_delay_min) + " min" : "N/A", raw: true},
            {label: "P90", value: data.p90_delay_min ? Math.round(data.p90_delay_min) + " min" : "N/A", raw: true},
            {label: "SLA 10 min", value: data.sla10 + "%", cls: data.sla10 > 50 ? "success" : "warning", raw: true},
            {label: "SLA 30 min", value: data.sla30 + "%", cls: data.sla30 > 50 ? "success" : "warning", raw: true},
            {label: "SLA 60 min", value: data.sla60 + "%", cls: data.sla60 > 50 ? "success" : "", raw: true},
            {label: "FCR", value: data.fcr_rate + "%", cls: data.fcr_rate > 30 ? "success" : "warning", raw: true},
            {label: "Telephone", value: data.tel_share + "%", raw: true},
            {label: "Radio", value: data.radio_share + "%", raw: true},
            {label: "Parfaites", value: data.pct_perfect + "%", cls: data.pct_perfect > 50 ? "success" : "danger", raw: true},
        ];
        grid.textContent = "";
        items.forEach(function(it, idx) {
            var div = buildEl("div", "lab-kpi");
            div.style.opacity = "0";
            div.style.transform = "translateY(8px)";
            var valEl = buildEl("div", "lab-kpi-value" + (it.cls ? " " + it.cls : ""));
            if (it.raw) {
                valEl.textContent = it.value;
            } else {
                valEl.textContent = "0";
            }
            div.appendChild(valEl);
            div.appendChild(buildEl("div", "lab-kpi-label", it.label));
            grid.appendChild(div);
            // Staggered fade-in animation
            setTimeout(function() {
                div.style.transition = "opacity 0.4s ease, transform 0.4s ease";
                div.style.opacity = "1";
                div.style.transform = "translateY(0)";
            }, idx * 60);
            if (!it.raw && typeof it.value === "number") {
                setTimeout(function() { animateValue(valEl, it.value, 800); }, idx * 60 + 200);
            }
        });
        if (data.date_range && data.date_range.start) {
            var dtS = new Date(data.date_range.start);
            var dtE = new Date(data.date_range.end);
            var nbJ = Math.round((dtE - dtS) / 86400000) + 1;
            var mFr = ["jan","fev","mar","avr","mai","jun","jul","aou","sep","oct","nov","dec"];
            var fD = function(dt) { return dt.getDate() + " " + mFr[dt.getMonth()]; };
            var drDiv = buildEl("div", "lab-kpi");
            drDiv.style.opacity = "0";
            drDiv.style.transform = "translateY(8px)";
            var drVal = buildEl("div", "lab-kpi-value", nbJ + "j");
            drVal.style.fontSize = "1.3rem";
            drDiv.appendChild(drVal);
            drDiv.appendChild(buildEl("div", "lab-kpi-label", fD(dtS) + " \u2192 " + fD(dtE)));
            grid.appendChild(drDiv);
            setTimeout(function() {
                drDiv.style.transition = "opacity 0.4s ease, transform 0.4s ease";
                drDiv.style.opacity = "1";
                drDiv.style.transform = "translateY(0)";
            }, items.length * 60);
        }
    }

    // =========================================================================
    // RENDER: Temporal
    // =========================================================================
    function renderTemporal(data) {
        destroyChart("timeline-hourly");
        if (data.hourly && data.hourly.length) {
            var labels = data.hourly.map(function(h) { return h.dt.substring(5, 16); });
            var values = data.hourly.map(function(h) { return h.count; });
            var peaks = data.peaks || [];
            var peakDts = peaks.map(function(p) { return p.dt; });
            var colors = data.hourly.map(function(h) {
                return peakDts.indexOf(h.dt) >= 0 ? "#ef4444" : BRAND_PALETTE[0];
            });
            charts["timeline-hourly"] = new Chart($("chart-timeline-hourly"), {
                type: "bar",
                data: {labels: labels, datasets: [{label: "Interventions/h", data: values, backgroundColor: colors, borderRadius: 3}]},
                options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}},
                    scales: {x: {ticks: {maxRotation: 60, font: {size: 9}}}, y: {beginAtZero: true}}}
            });
        }

        destroyChart("heatmap-matrix");
        if (data.heatmap && data.heatmap.values && data.heatmap.values.length) {
            var matrixData = [];
            var maxVal = 0;
            data.heatmap.values.forEach(function(row, y) {
                row.forEach(function(val, x) {
                    matrixData.push({x: x, y: y, v: val});
                    if (val > maxVal) maxVal = val;
                });
            });
            var joursFr = {"lundi": "Lun", "mardi": "Mar", "mercredi": "Mer", "jeudi": "Jeu", "vendredi": "Ven", "samedi": "Sam", "dimanche": "Dim"};
            var joursLong = {"lundi": "Lundi", "mardi": "Mardi", "mercredi": "Mercredi", "jeudi": "Jeudi", "vendredi": "Vendredi", "samedi": "Samedi", "dimanche": "Dimanche"};
            var hmDays = data.heatmap.days;
            var nDays = hmDays.length;
            // Build labels for category axes
            var xLabels = [];
            for (var hi = 0; hi < 24; hi++) xLabels.push(String(hi).padStart(2, "0") + "h");
            var yLabels = hmDays.map(function(d) { return joursFr[d] || d; });
            // Remap data to use category labels
            var matrixCat = matrixData.map(function(p) {
                return {x: xLabels[p.x], y: yLabels[p.y], v: p.v};
            });
            charts["heatmap-matrix"] = new Chart($("chart-heatmap-matrix"), {
                type: "matrix",
                data: {
                    labels: {x: xLabels, y: yLabels},
                    datasets: [{
                        label: "Activite",
                        data: matrixCat,
                        width: function(ctx) { return (ctx.chart.chartArea || {}).width / 24 - 1; },
                        height: function(ctx) { return (ctx.chart.chartArea || {}).height / nDays - 1; },
                        backgroundColor: function(ctx) {
                            var v = ctx.dataset.data[ctx.dataIndex].v;
                            var ratio = maxVal > 0 ? v / maxVal : 0;
                            var idx = Math.min(Math.floor(ratio * HEATMAP_GRADIENT.length), HEATMAP_GRADIENT.length - 1);
                            return HEATMAP_GRADIENT[idx];
                        },
                        borderWidth: 1, borderColor: "rgba(255,255,255,0.3)",
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: {legend: {display: false}, tooltip: {
                        callbacks: {title: function() { return ""; }, label: function(ctx) {
                            var d = ctx.dataset.data[ctx.dataIndex];
                            var dayIdx = yLabels.indexOf(d.y);
                            var jourNom = joursLong[hmDays[dayIdx]] || d.y;
                            return jourNom + " " + d.x + " \u2192 " + d.v + " fiche" + (d.v > 1 ? "s" : "");
                        }}
                    }},
                    scales: {
                        x: {type: "category", position: "top", labels: xLabels, offset: true, grid: {display: false},
                            ticks: {color: "#1f2d3d", font: {size: 10, weight: "bold"}}},
                        y: {type: "category", labels: yLabels, offset: true, grid: {display: false},
                            ticks: {color: "#1f2d3d", font: {size: 11, weight: "bold"}}}
                    }
                }
            });
            // Store metadata for modal clone
            charts["heatmap-matrix"]._labMeta = {matrixData: matrixData, maxVal: maxVal, hmDays: hmDays, nDays: nDays, xLabels: xLabels, yLabels: yLabels, joursFr: joursFr, joursLong: joursLong};
        }

        destroyChart("backlog");
        if (data.backlog && data.backlog.length) {
            charts["backlog"] = new Chart($("chart-backlog"), {
                type: "line",
                data: {
                    labels: data.backlog.map(function(b) { return b.t.substring(5, 16); }),
                    datasets: [{label: "Backlog", data: data.backlog.map(function(b) { return b.backlog; }),
                        borderColor: BRAND_PALETTE[0], backgroundColor: "rgba(37,99,235,0.1)", fill: true, tension: 0.3, pointRadius: 0}]
                },
                options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}},
                    scales: {x: {ticks: {maxTicksLimit: 10, font: {size: 9}}}, y: {beginAtZero: true}}}
            });
        }
    }

    // =========================================================================
    // RENDER: Performance
    // =========================================================================
    function renderPerformance(data) {
        destroyChart("fcr");
        if (data.fcr) {
            charts["fcr"] = new Chart($("chart-fcr"), {
                type: "doughnut",
                data: {labels: ["FCR", "Non FCR"], datasets: [{data: [data.fcr.fcr, data.fcr.non_fcr], backgroundColor: [BRAND_PALETTE[0], "#e2e8f0"]}]},
                options: {responsive: true, maintainAspectRatio: true, cutout: "55%", plugins: {legend: {position: "bottom", labels: {font: {size: 11}}}}}
            });
        }
        destroyChart("sla");
        if (data.sla) {
            charts["sla"] = new Chart($("chart-sla"), {
                type: "bar",
                data: {labels: ["SLA 10 min", "SLA 30 min", "SLA 60 min"],
                    datasets: [{data: [data.sla.sla10, data.sla.sla30, data.sla.sla60],
                        backgroundColor: [BRAND_PALETTE[9], BRAND_PALETTE[4], BRAND_PALETTE[0]], borderRadius: 4}]},
                options: {indexAxis: "y", responsive: true, maintainAspectRatio: true,
                    plugins: {legend: {display: false}}, scales: {x: {max: 100, ticks: {callback: function(v) { return v + "%"; }}}}}
            });
        }
        destroyChart("delay-dist");
        if (data.delay_distribution && data.delay_distribution.length) {
            charts["delay-dist"] = new Chart($("chart-delay-dist"), {
                type: "bar",
                data: {labels: data.delay_distribution.map(function(d) { return d.range; }),
                    datasets: [{data: data.delay_distribution.map(function(d) { return d.count; }),
                        backgroundColor: BRAND_PALETTE[2], borderRadius: 3}]},
                options: {responsive: true, maintainAspectRatio: true, plugins: {legend: {display: false}}, scales: {y: {beginAtZero: true}}}
            });
        }
    }

    // =========================================================================
    // RENDER: Categories
    // =========================================================================
    function renderCategories(data) {
        destroyChart("sources");
        if (data.sources && data.sources.length) {
            charts["sources"] = new Chart($("chart-sources"), {
                type: "bar",
                data: {labels: data.sources.map(function(s) { return s.label; }),
                    datasets: [{data: data.sources.map(function(s) { return s.n; }), backgroundColor: BRAND_PALETTE[0], borderRadius: 3}]},
                options: {indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}, scales: {x: {beginAtZero: true}}}
            });
        }
        destroyChart("sous-class");
        if (data.sous_classifications && data.sous_classifications.length) {
            var canvas = $("chart-sous-class");
            if (canvas) {
                canvas.parentElement.style.height = Math.max(200, data.sous_classifications.length * 20) + "px";
                charts["sous-class"] = new Chart(canvas, {
                    type: "bar",
                    data: {labels: data.sous_classifications.map(function(s) { return s.label; }),
                        datasets: [{data: data.sous_classifications.map(function(s) { return s.n; }), backgroundColor: BRAND_PALETTE[3], borderRadius: 2}]},
                    options: {indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}, scales: {x: {beginAtZero: true}}}
                });
            }
        }
        destroyChart("channels");
        if (data.channels) {
            charts["channels"] = new Chart($("chart-channels"), {
                type: "doughnut",
                data: {labels: Object.keys(data.channels),
                    datasets: [{data: Object.values(data.channels), backgroundColor: [BRAND_PALETTE[0], BRAND_PALETTE[4], "#e2e8f0"]}]},
                options: {responsive: true, maintainAspectRatio: true, cutout: "50%", plugins: {legend: {position: "bottom", labels: {font: {size: 10}}}}}
            });
        }
    }

    // =========================================================================
    // RENDER: Services
    // =========================================================================
    function renderServices(data) {
        destroyChart("services-split");
        if (data.split && data.split.length) {
            charts["services-split"] = new Chart($("chart-services-split"), {
                type: "doughnut",
                data: {labels: data.split.map(function(s) { return s.label; }),
                    datasets: [{data: data.split.map(function(s) { return s.n; }), backgroundColor: BRAND_PALETTE}]},
                options: {responsive: true, maintainAspectRatio: true, cutout: "45%", plugins: {legend: {position: "bottom", labels: {font: {size: 9}}}}}
            });
        }
        destroyChart("services-p90");
        if (data.p90 && data.p90.length) {
            charts["services-p90"] = new Chart($("chart-services-p90"), {
                type: "bar",
                data: {labels: data.p90.map(function(s) { return s.label; }),
                    datasets: [{label: "P90 (min)", data: data.p90.map(function(s) { return s.p90; }), backgroundColor: BRAND_PALETTE[5], borderRadius: 3}]},
                options: {indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}}
            });
        }
    }

    // =========================================================================
    // RENDER: Intervenants
    // =========================================================================
    function renderIntervenants(data) {
        destroyChart("intervenants-top");
        if (data.top && data.top.length) {
            charts["intervenants-top"] = new Chart($("chart-intervenants-top"), {
                type: "bar",
                data: {labels: data.top.map(function(i) { return i.label; }),
                    datasets: [{data: data.top.map(function(i) { return i.n; }), backgroundColor: BRAND_PALETTE[1], borderRadius: 3}]},
                options: {indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}}
            });
        }
        destroyChart("intervenants-levels");
        if (data.levels && data.levels.length) {
            var niveaux = ["Niveau 1", "Niveau 2", "Niveau 3", "Niveau 4", "Niveau 5"];
            var intervenants = [];
            data.levels.forEach(function(l) { if (intervenants.indexOf(l.intervenant) < 0) intervenants.push(l.intervenant); });
            intervenants = intervenants.slice(0, 15);
            var datasets = niveaux.map(function(niv, idx) {
                return {label: niv, data: intervenants.map(function(intv) {
                    var match = data.levels.find(function(l) { return l.intervenant === intv && l.niveau === niv; });
                    return match ? match.n : 0;
                }), backgroundColor: BRAND_PALETTE[idx]};
            });
            charts["intervenants-levels"] = new Chart($("chart-intervenants-levels"), {
                type: "bar", data: {labels: intervenants, datasets: datasets},
                options: {indexAxis: "y", responsive: true, maintainAspectRatio: false,
                    plugins: {legend: {position: "bottom", labels: {font: {size: 9}}}},
                    scales: {x: {stacked: true}, y: {stacked: true}}}
            });
        }
    }

    // =========================================================================
    // RENDER: Text / Wordcloud
    // =========================================================================
    function renderText(data) {
        var canvas = $("lab-wordcloud");
        if (canvas && data.wordcloud && data.wordcloud.length && typeof WordCloud !== "undefined") {
            var maxN = data.wordcloud[0].n;
            var list = data.wordcloud.map(function(w) {
                return [w.t, Math.max(8, Math.round(w.n / maxN * 48))];
            });
            WordCloud(canvas, {
                list: list, gridSize: 6, weightFactor: 1,
                fontFamily: "Outfit, sans-serif",
                color: function() { return BRAND_PALETTE[Math.floor(Math.random() * BRAND_PALETTE.length)]; },
                backgroundColor: "transparent", rotateRatio: 0.3,
            });
        }
        destroyChart("treemap");
        if (data.treemap && data.treemap.length) {
            var treemapData = data.treemap.slice(0, 30).map(function(t) { return {label: t.label, value: t.n}; });
            charts["treemap"] = new Chart($("chart-treemap"), {
                type: "treemap",
                data: {datasets: [{tree: treemapData, key: "value",
                    labels: {display: true, formatter: function(ctx) { return ctx.raw._data ? ctx.raw._data.label : ""; }, font: {size: 9}},
                    backgroundColor: function(ctx) { return BRAND_PALETTE[ctx.dataIndex % BRAND_PALETTE.length]; },
                    borderWidth: 1, borderColor: "#fff"}]},
                options: {responsive: true, maintainAspectRatio: true, plugins: {legend: {display: false}}}
            });
        }
    }

    // =========================================================================
    // RENDER: Appelants
    // =========================================================================
    function renderAppelants(data) {
        destroyChart("appelants");
        if (data.top && data.top.length) {
            charts["appelants"] = new Chart($("chart-appelants"), {
                type: "bar",
                data: {labels: data.top.map(function(a) { return a.label; }),
                    datasets: [{data: data.top.map(function(a) { return a.n; }), backgroundColor: BRAND_PALETTE[4], borderRadius: 3}]},
                options: {indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}}
            });
        }
    }

    // =========================================================================
    // RENDER: Geographic
    // =========================================================================
    function renderGeographic(data) {
        window._labGeoData = data;
        if (labMapCarroye) updateMapCarroye(data);
    }

    // Map layer groups
    var _mapLayers = {carroyes: null, gpsPoints: null, hotspots: null, heatmap: null, waze: null};

    function initMapCarroye() {
        var container = $("lab-map-carroye");
        if (!container || labMapCarroye) return;
        labMapCarroye = L.map(container, {center: [47.938561, 0.224318], zoom: 14, maxZoom: 22, zoomControl: true, scrollWheelZoom: true});
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {attribution: "OSM", maxNativeZoom: 19, maxZoom: 22}).addTo(labMapCarroye);
        // Init layer groups
        _mapLayers.carroyes = L.layerGroup().addTo(labMapCarroye);
        _mapLayers.gpsPoints = L.layerGroup().addTo(labMapCarroye);
        _mapLayers.hotspots = L.layerGroup();
        _mapLayers.heatmap = L.layerGroup();
        _mapLayers.waze = L.layerGroup();
        // Bind toggle checkboxes
        initMapLayerToggles();

        // Custom fullscreen control (CSS expand, not browser fullscreen)
        var fsCtrl = L.control({position: "topright"});
        fsCtrl.onAdd = function() {
            var div = L.DomUtil.create("div", "leaflet-bar");
            var btn = L.DomUtil.create("a", "", div);
            btn.href = "#";
            btn.title = "Agrandir la carte";
            btn.style.cssText = "display:flex;align-items:center;justify-content:center;width:30px;height:30px;cursor:pointer;background:#fff;";
            var ico = L.DomUtil.create("span", "material-symbols-outlined", btn);
            ico.textContent = "fullscreen";
            ico.style.cssText = "font-size:18px;color:#1f2d3d;";
            L.DomEvent.on(btn, "click", function(e) {
                L.DomEvent.stop(e);
                var carteView = document.querySelector('.lab-view[data-view="carte"]');
                if (!carteView) return;
                var isExpanded = carteView.classList.contains("lab-map-expanded");
                carteView.classList.toggle("lab-map-expanded");
                ico.textContent = isExpanded ? "fullscreen" : "fullscreen_exit";
                btn.title = isExpanded ? "Agrandir la carte" : "Reduire la carte";
                setTimeout(function() { labMapCarroye.invalidateSize(); }, 300);
            });
            return div;
        };
        fsCtrl.addTo(labMapCarroye);

        // Hide any native fullscreen plugin button
        var nativeFs = container.querySelector(".leaflet-control-fullscreen-btn");
        if (nativeFs) nativeFs.style.display = "none";

        if (window._labGeoData) updateMapCarroye(window._labGeoData);
    }

    function initMapLayerToggles() {
        var toggles = {
            "layer-carroyes": "carroyes",
            "layer-gps-points": "gpsPoints",
            "layer-hotspots": "hotspots",
            "layer-heatmap": "heatmap",
            "layer-waze": "waze",
        };
        Object.keys(toggles).forEach(function(id) {
            var cb = $(id);
            if (!cb) return;
            cb.addEventListener("change", function() {
                var layerKey = toggles[id];
                var layer = _mapLayers[layerKey];
                if (!layer || !labMapCarroye) return;
                if (cb.checked) {
                    labMapCarroye.addLayer(layer);
                } else {
                    labMapCarroye.removeLayer(layer);
                }
            });
        });
    }

    function updateMapCarroye(data) {
        if (!labMapCarroye) return;
        // Clear all layer groups
        Object.values(_mapLayers).forEach(function(lg) { if (lg) lg.clearLayers(); });

        // Carroyes
        if (data.car_points) {
            data.car_points.forEach(function(p) {
                var radius = Math.max(8, Math.min(30, Math.sqrt(p.n) * 4));
                L.circleMarker([p.lat, p.lon], {radius: radius, fillColor: BRAND_PALETTE[0], fillOpacity: 0.6, color: "#fff", weight: 1})
                    .bindPopup(escapeText(p.ref) + " : " + p.n + " incidents").addTo(_mapLayers.carroyes);
            });
        }
        // GPS points
        if (data.gps_points) {
            data.gps_points.forEach(function(p) {
                L.circleMarker([p.lat, p.lon], {radius: 5, fillColor: "#ef4444", fillOpacity: 0.7, color: "#fff", weight: 1})
                    .bindPopup(escapeText(p.category) + " - " + escapeText(p.text)).addTo(_mapLayers.gpsPoints);
            });
        }
        // Hotspots
        if (data.hotspots) {
            data.hotspots.forEach(function(h) {
                var color = h.score > 70 ? "#ef4444" : h.score > 40 ? "#f59e0b" : "#10b981";
                L.circleMarker([h.lat, h.lon], {radius: Math.max(10, Math.sqrt(h.volume) * 3), fillColor: color, fillOpacity: 0.4, color: color, weight: 2})
                    .bindPopup(escapeText(h.ref) + " - Score: " + h.score + " - Vol: " + h.volume).addTo(_mapLayers.hotspots);
            });
        }
        // Heatmap
        var heatPoints = [];
        if (data.gps_points) { data.gps_points.forEach(function(p) { heatPoints.push([p.lat, p.lon, 1]); }); }
        if (data.car_points) { data.car_points.forEach(function(p) { heatPoints.push([p.lat, p.lon, p.n]); }); }
        if (heatPoints.length && typeof L.heatLayer !== "undefined") {
            heatLayer = L.heatLayer(heatPoints, {
                radius: 25, blur: 15, maxZoom: 17,
                gradient: {0.2: "#ffffb2", 0.4: "#fecc5c", 0.6: "#fd8d3c", 0.8: "#f03b20", 1.0: "#bd0026"}
            });
            _mapLayers.heatmap.addLayer(heatLayer);
        }
        // Sync toggle states with map
        var toggleMap = {"layer-carroyes": "carroyes", "layer-gps-points": "gpsPoints", "layer-hotspots": "hotspots", "layer-heatmap": "heatmap", "layer-waze": "waze"};
        Object.keys(toggleMap).forEach(function(id) {
            var cb = $(id);
            var layer = _mapLayers[toggleMap[id]];
            if (cb && layer) {
                if (cb.checked && !labMapCarroye.hasLayer(layer)) labMapCarroye.addLayer(layer);
                if (!cb.checked && labMapCarroye.hasLayer(layer)) labMapCarroye.removeLayer(layer);
            }
        });
    }

    // =========================================================================
    // RENDER: Meteo cross
    // =========================================================================
    function renderMeteoCross(data) {
        // Also feed scatter plot in croisements view
        renderScatterMeteo(data);
        destroyChart("meteo-cross");
        var pearsonEl = $("lab-meteo-pearson");
        if (data.days && data.days.length) {
            var labels = data.days.map(function(d) { return d.date; });
            charts["meteo-cross"] = new Chart($("chart-meteo-cross"), {
                data: {labels: labels, datasets: [
                    {label: "Incidents", data: data.days.map(function(d) { return d.incidents; }),
                        type: "bar", backgroundColor: "rgba(37,99,235,0.5)", yAxisID: "y", borderRadius: 3},
                    {label: "Tmax (C)", data: data.days.map(function(d) { return d.tmax; }),
                        type: "line", borderColor: "#ef4444", yAxisID: "y1", tension: 0.3, pointRadius: 3},
                    {label: "Pluie (mm)", data: data.days.map(function(d) { return d.rain; }),
                        type: "line", borderColor: "#0ea5e9", yAxisID: "y2", tension: 0.3, pointRadius: 3},
                ]},
                options: {responsive: true, maintainAspectRatio: false,
                    plugins: {legend: {position: "bottom", labels: {font: {size: 9}}}},
                    scales: {
                        y: {position: "left", beginAtZero: true, title: {display: true, text: "Incidents", font: {size: 9}}},
                        y1: {position: "right", grid: {display: false}, title: {display: true, text: "C", font: {size: 9}}},
                        y2: {position: "right", grid: {display: false}, display: false},
                    }}
            });
        }
        if (pearsonEl) {
            pearsonEl.textContent = "";
            if (data.pearson_temp !== null && data.pearson_temp !== undefined) {
                var cls = Math.abs(data.pearson_temp) > 0.6 ? "high" : Math.abs(data.pearson_temp) > 0.3 ? "mid" : "low";
                var span = buildEl("span", "lab-pearson " + cls, "Temp r=" + data.pearson_temp);
                pearsonEl.appendChild(span);
            }
            if (data.pearson_rain !== null && data.pearson_rain !== undefined) {
                var cls2 = Math.abs(data.pearson_rain) > 0.6 ? "high" : Math.abs(data.pearson_rain) > 0.3 ? "mid" : "low";
                var span2 = buildEl("span", "lab-pearson " + cls2, "Pluie r=" + data.pearson_rain);
                pearsonEl.appendChild(span2);
            }
        }
    }

    // =========================================================================
    // RENDER: Zones vulnerability
    // =========================================================================
    function renderZonesVuln(data) {
        destroyChart("zones-vuln");
        if (data.zones && data.zones.length) {
            var colors = data.zones.map(function(z) { return z.score > 70 ? "#ef4444" : z.score > 40 ? "#f59e0b" : "#10b981"; });
            charts["zones-vuln"] = new Chart($("chart-zones-vuln"), {
                type: "bar",
                data: {labels: data.zones.map(function(z) { return z.zone.split("/").pop(); }),
                    datasets: [{data: data.zones.map(function(z) { return z.score; }), backgroundColor: colors, borderRadius: 3}]},
                options: {indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}, scales: {x: {max: 100}}}
            });
        }
    }

    // =========================================================================
    // RENDER: Escalation (Sankey)
    // =========================================================================
    function renderEscalation(data) {
        // Sidebar: compact summary with levels + top flows as text
        var body = $("lab-escalation-body");
        if (!body) return;
        body.textContent = "";

        // Niveaux d'intervention
        if (data.levels_count) {
            var entries = Object.entries(data.levels_count).filter(function(e) { return e[1] > 0; });
            if (entries.length) {
                var total = entries.reduce(function(a, e) { return a + e[1]; }, 0);
                entries.forEach(function(entry) {
                    var row = buildEl("div");
                    row.style.cssText = "display:flex;align-items:center;gap:8px;padding:4px 0;font-size:0.78rem;";
                    var label = buildEl("span", null, entry[0]);
                    label.style.cssText = "min-width:65px;color:var(--text);font-weight:500;";
                    row.appendChild(label);
                    // Mini progress bar
                    var barWrap = buildEl("div");
                    barWrap.style.cssText = "flex:1;height:6px;background:var(--bg);border-radius:3px;overflow:hidden;";
                    var bar = buildEl("div");
                    var pct = Math.round(100 * entry[1] / total);
                    bar.style.cssText = "height:100%;border-radius:3px;background:" + BRAND_PALETTE[parseInt(entry[0].replace("Niveau ", "")) - 1] + ";width:" + pct + "%;";
                    barWrap.appendChild(bar);
                    row.appendChild(barWrap);
                    var val = buildEl("span", null, String(entry[1]));
                    val.style.cssText = "min-width:30px;text-align:right;font-weight:700;color:var(--brand);font-size:0.8rem;";
                    row.appendChild(val);
                    body.appendChild(row);
                });
            }
        }

        // Top 3 flux
        if (data.flows && data.flows.length) {
            var topFlows = data.flows.sort(function(a, b) { return b.flow - a.flow; }).slice(0, 3);
            var flowDiv = buildEl("div");
            flowDiv.style.cssText = "margin-top:8px;padding-top:6px;border-top:1px solid var(--line);";
            flowDiv.appendChild(buildEl("div", "lab-kpi-label", "Top flux"));
            topFlows.forEach(function(f) {
                var row = buildEl("div");
                row.style.cssText = "font-size:0.72rem;color:var(--muted);padding:2px 0;display:flex;gap:4px;";
                row.appendChild(buildEl("span", null, f.from.replace("PCO.", "").replace("PCS.", "")));
                var arrow = buildEl("span", null, "\u2192");
                arrow.style.color = "var(--brand)";
                row.appendChild(arrow);
                row.appendChild(buildEl("span", null, f.to));
                var cnt = buildEl("span", null, String(f.flow));
                cnt.style.cssText = "margin-left:auto;font-weight:600;color:var(--text);";
                row.appendChild(cnt);
                flowDiv.appendChild(row);
            });
            body.appendChild(flowDiv);
        }
    }

    // =========================================================================
    // RENDER: Convergence (with simple DBSCAN clustering)
    // =========================================================================
    function initMapConvergence() {
        var container = $("lab-map-convergence");
        if (!container || labMapConv) return;
        labMapConv = L.map(container, {center: [47.938561, 0.224318], zoom: 14, maxZoom: 22, zoomControl: true, scrollWheelZoom: true});
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {attribution: "OSM", maxNativeZoom: 19, maxZoom: 22}).addTo(labMapConv);
        if (window._labConvergenceData) updateMapConvergence(window._labConvergenceData);
    }

    function renderConvergence(data) {
        window._labConvergenceData = data;
        if (labMapConv) updateMapConvergence(data);
    }

    // Simple DBSCAN for spatial clustering (no external lib needed)
    function simpleDBSCAN(points, eps, minPts) {
        var n = points.length;
        var labels = new Array(n).fill(-1);
        var clusterId = 0;
        function haversine(a, b) {
            var R = 6371000;
            var dLat = (b.lat - a.lat) * Math.PI / 180;
            var dLon = (b.lon - a.lon) * Math.PI / 180;
            var sa = Math.sin(dLat / 2);
            var sb = Math.sin(dLon / 2);
            var h = sa * sa + Math.cos(a.lat * Math.PI / 180) * Math.cos(b.lat * Math.PI / 180) * sb * sb;
            return 2 * R * Math.asin(Math.sqrt(h));
        }
        function regionQuery(idx) {
            var neighbors = [];
            for (var j = 0; j < n; j++) {
                if (haversine(points[idx], points[j]) <= eps) neighbors.push(j);
            }
            return neighbors;
        }
        for (var i = 0; i < n; i++) {
            if (labels[i] !== -1) continue;
            var neighbors = regionQuery(i);
            if (neighbors.length < minPts) { labels[i] = 0; continue; }
            clusterId++;
            labels[i] = clusterId;
            var seeds = neighbors.slice();
            for (var si = 0; si < seeds.length; si++) {
                var q = seeds[si];
                if (labels[q] === 0) labels[q] = clusterId;
                if (labels[q] !== -1) continue;
                labels[q] = clusterId;
                var qNeighbors = regionQuery(q);
                if (qNeighbors.length >= minPts) {
                    for (var k = 0; k < qNeighbors.length; k++) {
                        if (seeds.indexOf(qNeighbors[k]) < 0) seeds.push(qNeighbors[k]);
                    }
                }
            }
        }
        return labels;
    }

    function updateMapConvergence(data) {
        if (!labMapConv || !data.gps_incidents) return;
        labMapConv.eachLayer(function(l) { if (!(l instanceof L.TileLayer)) labMapConv.removeLayer(l); });

        var pts = data.gps_incidents.filter(function(p) { return p.lat && p.lon; });
        if (pts.length < 3) {
            pts.forEach(function(p) {
                L.circleMarker([p.lat, p.lon], {radius: 4, fillColor: BRAND_PALETTE[0], fillOpacity: 0.5, color: "#fff", weight: 1}).addTo(labMapConv);
            });
            return;
        }

        // Run DBSCAN: eps=100m, minPts=3
        var labels = simpleDBSCAN(pts, 100, 3);
        var clusters = {};
        var clusterColors = ["#ef4444", "#f59e0b", "#10b981", "#2563eb", "#7c3aed", "#ec4899", "#06b6d4", "#84cc16"];

        for (var i = 0; i < pts.length; i++) {
            var lbl = labels[i];
            if (lbl <= 0) {
                // Noise point
                L.circleMarker([pts[i].lat, pts[i].lon], {radius: 3, fillColor: "#94a3b8", fillOpacity: 0.3, color: "transparent", weight: 0}).addTo(labMapConv);
            } else {
                if (!clusters[lbl]) clusters[lbl] = {points: [], categories: {}};
                clusters[lbl].points.push(pts[i]);
                var cat = pts[i].category || "Autre";
                clusters[lbl].categories[cat] = (clusters[lbl].categories[cat] || 0) + 1;
            }
        }

        // Draw cluster circles
        var clusterIds = Object.keys(clusters);
        clusterIds.forEach(function(cid, idx) {
            var c = clusters[cid];
            var sumLat = 0, sumLon = 0;
            c.points.forEach(function(p) { sumLat += p.lat; sumLon += p.lon; });
            var centLat = sumLat / c.points.length;
            var centLon = sumLon / c.points.length;
            var color = clusterColors[idx % clusterColors.length];
            var radius = Math.max(12, Math.sqrt(c.points.length) * 6);

            // Draw individual points
            c.points.forEach(function(p) {
                L.circleMarker([p.lat, p.lon], {radius: 4, fillColor: color, fillOpacity: 0.7, color: "#fff", weight: 1}).addTo(labMapConv);
            });

            // Draw cluster circle
            L.circle([centLat, centLon], {radius: radius * 10, fillColor: color, fillOpacity: 0.15, color: color, weight: 2, dashArray: "5,5"})
                .bindPopup("Cluster #" + cid + " : " + c.points.length + " incidents")
                .addTo(labMapConv);
        });

        // Cluster timeline chart
        destroyChart("cluster-timeline");
        if (clusterIds.length > 0) {
            var timeLabels = [];
            var timeData = [];
            clusterIds.forEach(function(cid, idx) {
                timeLabels.push("Cluster " + cid);
                timeData.push(clusters[cid].points.length);
            });
            charts["cluster-timeline"] = new Chart($("chart-cluster-timeline"), {
                type: "bar",
                data: {labels: timeLabels, datasets: [{data: timeData,
                    backgroundColor: clusterIds.map(function(_, i) { return clusterColors[i % clusterColors.length]; }),
                    borderRadius: 4}]},
                options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}, scales: {y: {beginAtZero: true}}}
            });
        }
    }

    // =========================================================================
    // RENDER: Comparative
    // =========================================================================
    function renderComparative(data) {
        // Store data for deferred rendering when tab becomes visible
        window._labComparativeData = data;
        _doRenderComparative(data);
    }

    function _doRenderComparative(data) {
        if (!data || !data.years || data.years.length < 2) return;
        var kpiEl = $("lab-comparative-kpis");
        var y1 = String(data.years[0]);
        var y2 = String(data.years[1]);
        var k1 = data.kpis_by_year[y1] || {};
        var k2 = data.kpis_by_year[y2] || {};
        if (kpiEl) {
            kpiEl.textContent = "";
            var metrics = [
                {label: "Total fiches", v1: k1.total, v2: k2.total},
                {label: "Delai median", v1: k1.median_delay, v2: k2.median_delay, unit: " min"},
                {label: "SLA 30 min", v1: k1.sla30, v2: k2.sla30, unit: "%"},
            ];
            metrics.forEach(function(m) {
                var div = buildEl("div", "lab-kpi");
                div.appendChild(buildEl("div", "lab-kpi-label", m.label));
                var row = buildEl("div");
                row.style.cssText = "display:flex;gap:8px;justify-content:center;align-items:baseline;";
                var v1Str = m.v1 !== null && m.v1 !== undefined ? m.v1 + (m.unit || "") : "N/A";
                var v2Str = m.v2 !== null && m.v2 !== undefined ? m.v2 + (m.unit || "") : "N/A";
                row.appendChild(buildEl("span", null, y1 + ": " + v1Str));
                row.lastChild.style.cssText = "font-size:0.75rem;color:var(--muted);";
                var mainVal = buildEl("span", "lab-kpi-value", v2Str);
                mainVal.style.fontSize = "1rem";
                row.appendChild(mainVal);
                if (m.v1 !== null && m.v2 !== null && m.v1 !== undefined && m.v2 !== undefined) {
                    var diff = m.v2 - m.v1;
                    if (diff > 0) row.appendChild(buildEl("span", "lab-trend-up", "+" + Math.round(diff)));
                    else if (diff < 0) row.appendChild(buildEl("span", "lab-trend-down", String(Math.round(diff))));
                }
                div.appendChild(row);
                kpiEl.appendChild(div);
            });
        }
        // Category evolution chart — only render if tab is visible
        var catCanvas = $("chart-comparative-categories");
        if (!catCanvas || catCanvas.clientWidth < 10) return;
        destroyChart("comparative-categories");
        if (data.category_evolution) {
            var cats = Object.keys(data.category_evolution);
            var years = data.years.map(String);
            var datasets = years.map(function(yr, idx) {
                return {label: yr, data: cats.map(function(c) { return data.category_evolution[c][yr] || 0; }), backgroundColor: BRAND_PALETTE[idx]};
            });
            charts["comparative-categories"] = new Chart(catCanvas, {
                type: "bar",
                data: {labels: cats.map(function(c) { return c.replace("PCO.", "").replace("PCS.", ""); }), datasets: datasets},
                options: {responsive: true, maintainAspectRatio: false,
                    plugins: {legend: {position: "top", labels: {font: {size: 10}}}}, scales: {y: {beginAtZero: true}}}
            });
        }
    }

    // =========================================================================
    // RENDER: Effectifs
    // =========================================================================
    function renderEffectifs(data) {
        var body = $("lab-effectifs-body");
        if (!body) return;
        body.textContent = "";
        if (!data.available) {
            body.appendChild(buildEl("div", null, "Pas de calendrier disponible pour cet evenement."));
            body.lastChild.style.cssText = "font-size:0.78rem;color:var(--muted);padding:8px;";
            return;
        }
        if (data.zones && data.zones.length) {
            var total_secu = 0, total_accueil = 0;
            data.zones.forEach(function(z) { total_secu += z.agents_secu || 0; total_accueil += z.agents_accueil || 0; });
            var grid = buildEl("div", "lab-kpi-grid");
            var k1 = buildEl("div", "lab-kpi");
            k1.appendChild(buildEl("div", "lab-kpi-value", String(total_secu)));
            k1.appendChild(buildEl("div", "lab-kpi-label", "Creneaux securite"));
            grid.appendChild(k1);
            var k2 = buildEl("div", "lab-kpi");
            k2.appendChild(buildEl("div", "lab-kpi-value", String(total_accueil)));
            k2.appendChild(buildEl("div", "lab-kpi-label", "Creneaux accueil"));
            grid.appendChild(k2);
            body.appendChild(grid);
        }
    }

    // =========================================================================
    // RENDER: ANPR
    // =========================================================================
    function renderANPR(data) {
        var widget = $("lab-anpr");
        var body = $("lab-anpr-body");
        if (!widget || !body) return;
        if (data.available && data.matches && data.matches.length) {
            widget.style.display = "";
            body.textContent = "";
            body.appendChild(buildEl("div", null, data.matches.length + " correspondance(s) ANPR trouvee(s)"));
            body.lastChild.style.cssText = "font-size:0.78rem;color:var(--text);";
        }
    }

    // =========================================================================
    // RENDER: Network (d3 force graph)
    // =========================================================================
    function renderNetwork(data) {
        window._labNetworkData = data;
        // Defer rendering until the tab is visible
        var container = $("lab-network");
        if (!container || !data.nodes || !data.nodes.length) return;
        if (container.clientWidth < 10) {
            // Container not visible yet, will render on tab switch
            return;
        }
        _doRenderNetwork(data);
    }

    function _doRenderNetwork(data) {
        var container = $("lab-network");
        if (!container || !data || !data.nodes || !data.nodes.length) return;
        container.textContent = "";

        var width = container.clientWidth || 700;
        var height = Math.max(container.clientHeight, 500);

        var svg = d3.select(container).append("svg")
            .attr("width", width).attr("height", height)
            .attr("viewBox", [0, 0, width, height]);

        var colorMap = {operator: "#2563eb", zone: "#10b981", service: "#f59e0b"};

        var nodeById = {};
        data.nodes.forEach(function(n) { nodeById[n.id] = n; });

        // Filter links to only those with valid source/target
        var validLinks = data.links.filter(function(l) { return nodeById[l.source] && nodeById[l.target]; });

        var simulation = d3.forceSimulation(data.nodes)
            .force("link", d3.forceLink(validLinks).id(function(d) { return d.id; }).distance(80))
            .force("charge", d3.forceManyBody().strength(-200))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("collision", d3.forceCollide(20));

        // Links
        var link = svg.append("g").attr("stroke", "#d8e1ec").attr("stroke-opacity", 0.6)
            .selectAll("line").data(validLinks).enter().append("line")
            .attr("stroke-width", function(d) { return Math.max(1, Math.min(6, Math.sqrt(d.value))); });

        // Nodes
        var node = svg.append("g").selectAll("g").data(data.nodes).enter().append("g")
            .call(d3.drag()
                .on("start", function(event, d) { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
                .on("drag", function(event, d) { d.fx = event.x; d.fy = event.y; })
                .on("end", function(event, d) { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; })
            );

        node.append("circle")
            .attr("r", function(d) { return Math.max(6, Math.min(18, Math.sqrt(d.weight || 1) * 2)); })
            .attr("fill", function(d) { return colorMap[d.type] || "#6366f1"; })
            .attr("stroke", "#fff").attr("stroke-width", 1.5);

        node.append("text")
            .text(function(d) { return d.id.length > 15 ? d.id.substring(0, 15) + "..." : d.id; })
            .attr("x", 12).attr("y", 4)
            .style("font-size", "9px").style("font-family", "Outfit, sans-serif")
            .style("fill", "var(--text)").style("pointer-events", "none");

        // Tooltip on hover
        node.append("title").text(function(d) { return d.id + " (" + d.type + ")"; });

        simulation.on("tick", function() {
            link.attr("x1", function(d) { return d.source.x; }).attr("y1", function(d) { return d.source.y; })
                .attr("x2", function(d) { return d.target.x; }).attr("y2", function(d) { return d.target.y; });
            node.attr("transform", function(d) { return "translate(" + d.x + "," + d.y + ")"; });
        });

        // Legend
        var legend = svg.append("g").attr("transform", "translate(12, 12)");
        [["Operateur", "#2563eb"], ["Zone", "#10b981"], ["Service", "#f59e0b"]].forEach(function(item, i) {
            legend.append("circle").attr("cx", 6).attr("cy", i * 18).attr("r", 5).attr("fill", item[1]);
            legend.append("text").attr("x", 16).attr("y", i * 18 + 4)
                .text(item[0]).style("font-size", "10px").style("fill", "var(--text)");
        });
    }

    // =========================================================================
    // RENDER: Quality (widget body)
    // =========================================================================
    function renderQuality(data) {
        var body = $("lab-quality-body");
        if (!body) return;
        body.textContent = "";

        // Quality score display
        var pct = data.pct_perfect || 0;
        var scoreDiv = buildEl("div", "lab-kpi-grid");

        var k1 = buildEl("div", "lab-kpi");
        var v1 = buildEl("div", "lab-kpi-value " + (pct > 60 ? "success" : pct > 30 ? "warning" : "danger"), pct + "%");
        k1.appendChild(v1);
        k1.appendChild(buildEl("div", "lab-kpi-label", "Fiches parfaites"));
        scoreDiv.appendChild(k1);

        var k2 = buildEl("div", "lab-kpi");
        k2.appendChild(buildEl("div", "lab-kpi-value", String(data.n_perfect || 0)));
        k2.appendChild(buildEl("div", "lab-kpi-label", "sur " + (data.total || 0)));
        scoreDiv.appendChild(k2);

        body.appendChild(scoreDiv);

        // Missing fields
        if (data.quality_fields) {
            var fields = Object.entries(data.quality_fields);
            if (fields.length) {
                var list = buildEl("div");
                list.style.cssText = "margin-top:8px;font-size:0.72rem;";
                fields.forEach(function(f) {
                    var row = buildEl("div");
                    row.style.cssText = "display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid var(--line);";
                    row.appendChild(buildEl("span", null, f[0].replace(/_/g, " ")));
                    var ratio = data.total ? Math.round(100 * f[1] / data.total) : 0;
                    var badge = buildEl("span", null, ratio + "% rempli");
                    badge.style.cssText = "color:" + (ratio > 70 ? "var(--success)" : ratio > 40 ? "var(--warning)" : "var(--danger)") + ";font-weight:600;";
                    row.appendChild(badge);
                    list.appendChild(row);
                });
                body.appendChild(list);
            }
        }
    }

    // =========================================================================
    // RENDER: Scatter plots (croisements view)
    // =========================================================================
    function renderScatterMeteo(meteoData) {
        destroyChart("scatter-meteo");
        if (!meteoData || !meteoData.days || !meteoData.days.length) return;
        var pts = meteoData.days.filter(function(d) { return d.tmax !== null; });
        if (pts.length < 2) return;
        charts["scatter-meteo"] = new Chart($("chart-scatter-meteo"), {
            type: "scatter",
            data: {datasets: [
                {label: "Temp vs Incidents", data: pts.map(function(d) { return {x: d.tmax, y: d.incidents}; }),
                    backgroundColor: "rgba(239,68,68,0.6)", pointRadius: 6},
                {label: "Pluie vs Incidents",
                    data: pts.filter(function(d) { return d.rain !== null; }).map(function(d) { return {x: d.rain, y: d.incidents}; }),
                    backgroundColor: "rgba(14,165,233,0.6)", pointRadius: 6},
            ]},
            options: {responsive: true, maintainAspectRatio: false,
                plugins: {legend: {position: "bottom", labels: {font: {size: 10}}}},
                scales: {x: {title: {display: true, text: "Meteo", font: {size: 10}}}, y: {title: {display: true, text: "Incidents", font: {size: 10}}}}}
        });
    }

    // =========================================================================
    // RENDER: Waze cross (overlay on carte map)
    // =========================================================================
    function renderWazeCross(data) {
        window._labWazeData = data;
        if (_mapLayers.waze && data.alerts && data.alerts.length) {
            _mapLayers.waze.clearLayers();
            data.alerts.forEach(function(a) {
                var color = a.type === "ACCIDENT" ? "#ef4444" : a.type === "JAM" ? "#f59e0b" : "#6366f1";
                L.circleMarker([a.lat, a.lon], {radius: 6, fillColor: color, fillOpacity: 0.5, color: color, weight: 1})
                    .bindPopup("Waze: " + escapeText(a.type) + " " + escapeText(a.subtype) + " - " + escapeText(a.street))
                    .addTo(_mapLayers.waze);
            });
        }
    }

    // =========================================================================
    // RENDER: Affluence ratio chart (in croisements view)
    // =========================================================================
    function renderAffluenceChart(data) {
        destroyChart("affluence-ratio");
        var canvas = $("chart-affluence-ratio");
        if (!canvas) return;
        // Clean up any previous empty message
        var prev = canvas.parentElement.querySelector(".lab-affluence-empty");
        if (prev) prev.remove();
        canvas.style.display = "";
        if (!data.hourly || !data.hourly.length) {
            var msg = buildEl("div", "lab-affluence-empty", "Pas de donnees affluence Skidata pour cet evenement.");
            msg.style.cssText = "font-size:0.78rem;color:var(--muted);text-align:center;padding:40px 0;";
            canvas.style.display = "none";
            canvas.parentElement.appendChild(msg);
            return;
        }
        var labels = data.hourly.map(function(h) { return h.dt.substring(11, 16); });
        var presents = data.hourly.map(function(h) { return h.presents; });
        charts["affluence-ratio"] = new Chart($("chart-affluence-ratio"), {
            type: "line",
            data: {labels: labels, datasets: [{
                label: "Presents", data: presents,
                borderColor: BRAND_PALETTE[4], backgroundColor: "rgba(20,184,166,0.1)",
                fill: true, tension: 0.3, pointRadius: 1,
            }]},
            options: {responsive: true, maintainAspectRatio: false,
                plugins: {legend: {position: "bottom", labels: {font: {size: 10}}}},
                scales: {y: {beginAtZero: true}, x: {ticks: {maxTicksLimit: 12, font: {size: 9}}}}}
        });
    }

    // =========================================================================
    // CHRONO TIMELINE — frise alternee haut/bas
    // =========================================================================
    var _chronoData = [];
    var _chronoZoom = 10;
    var _chronoMinTs = 0;
    var _chronoMaxTs = 0;
    var _chronoLoaded = false;
    var _chronoFullscreen = false;

    var CAT_COLORS = {
        "PCO.Technique": "#f59e0b", "PCO.Securite": "#ef4444", "PCO.Secours": "#dc2626",
        "PCO.Information": "#2563eb", "PCS.Surete": "#7c3aed", "PCS.Information": "#06b6d4",
        "PCO.Fourriere": "#6b7280", "PCO.Flux": "#0d9488", "PCO.MainCourante": "#8b5cf6"
    };

    function initChrono() {
        var zoomSlider = $("lab-chrono-zoom");
        var zinBtn = $("lab-chrono-zin");
        var zoutBtn = $("lab-chrono-zout");
        var fsBtn = $("lab-chrono-fs");
        if (zoomSlider) zoomSlider.addEventListener("input", function() { _chronoZoom = parseInt(zoomSlider.value); renderChrono(); });
        if (zinBtn) zinBtn.addEventListener("click", function() { _chronoZoom = Math.min(100, _chronoZoom + 5); if (zoomSlider) zoomSlider.value = _chronoZoom; renderChrono(); });
        if (zoutBtn) zoutBtn.addEventListener("click", function() { _chronoZoom = Math.max(1, _chronoZoom - 5); if (zoomSlider) zoomSlider.value = _chronoZoom; renderChrono(); });
        if (fsBtn) fsBtn.addEventListener("click", toggleChronoFullscreen);

        // Mouse wheel zoom on scroll area
        var scrollArea = $("lab-chrono-scroll");
        if (scrollArea) {
            scrollArea.addEventListener("wheel", function(e) {
                if (!e.ctrlKey && !e.metaKey) return; // require ctrl/cmd + scroll
                e.preventDefault();
                var oldZoom = _chronoZoom;
                var delta = e.deltaY > 0 ? -3 : 3;
                _chronoZoom = Math.max(1, Math.min(100, _chronoZoom + delta));
                if (_chronoZoom !== oldZoom) {
                    if (zoomSlider) zoomSlider.value = _chronoZoom;
                    // Keep scroll position centered on mouse
                    var rect = scrollArea.getBoundingClientRect();
                    var mouseRatio = (e.clientX - rect.left + scrollArea.scrollLeft) / (scrollArea.scrollWidth || 1);
                    renderChrono();
                    scrollArea.scrollLeft = mouseRatio * scrollArea.scrollWidth - (e.clientX - rect.left);
                }
            }, {passive: false});
        }
    }

    function loadChronoData() {
        if (!currentEvent || !currentYear) return;
        apiGet("/api/analyse-ops/timeline-data?event=" + encodeURIComponent(currentEvent) + "&year=" + encodeURIComponent(currentYear))
            .then(function(res) {
                if (res.items && res.items.length) {
                    _chronoData = res.items.map(function(it) {
                        var t = new Date(it.ts).getTime();
                        return {ts: t, cat: it.cat, text: it.text, sev: it.sev, area: it.area, op: it.op, sc: it.sc, id: it.id};
                    }).filter(function(it) { return !isNaN(it.ts); });
                    _chronoData.sort(function(a, b) { return a.ts - b.ts; });
                    _chronoMinTs = _chronoData[0].ts;
                    _chronoMaxTs = _chronoData[_chronoData.length - 1].ts;
                    _chronoLoaded = true;
                    buildChronoDayPills();
                    renderChrono();
                }
            });
    }

    function buildChronoDayPills() {
        var container = $("lab-chrono-days");
        if (!container || !_chronoData.length) return;
        container.textContent = "";
        // Collect unique days
        var days = {};
        _chronoData.forEach(function(it) {
            var dt = new Date(it.ts);
            var key = dt.toISOString().substring(0, 10);
            if (!days[key]) days[key] = {ts: it.ts, count: 0};
            days[key].count++;
        });
        var dayKeys = Object.keys(days).sort();
        // "All" pill
        var allPill = buildEl("button", "lab-chrono-day-pill active", "Tout (" + _chronoData.length + ")");
        allPill.addEventListener("click", function() {
            container.querySelectorAll(".lab-chrono-day-pill").forEach(function(p) { p.classList.remove("active"); });
            allPill.classList.add("active");
            var scrollArea = $("lab-chrono-scroll");
            if (scrollArea) scrollArea.scrollLeft = 0;
        });
        container.appendChild(allPill);
        dayKeys.forEach(function(key) {
            var d = days[key];
            var dt = new Date(key + "T00:00:00");
            var label = _joursSemaine[dt.getDay()].substring(0, 3) + " " + dt.getDate() + " " + _moisNoms[dt.getMonth()] + " (" + d.count + ")";
            var pill = buildEl("button", "lab-chrono-day-pill", label);
            pill.addEventListener("click", function() {
                container.querySelectorAll(".lab-chrono-day-pill").forEach(function(p) { p.classList.remove("active"); });
                pill.classList.add("active");
                // Scroll to this day
                var scrollArea = $("lab-chrono-scroll");
                if (scrollArea && _chronoMinTs && _chronoMaxTs) {
                    var totalDuration = _chronoMaxTs - _chronoMinTs;
                    var dayOffset = d.ts - _chronoMinTs;
                    var ratio = dayOffset / totalDuration;
                    scrollArea.scrollLeft = ratio * scrollArea.scrollWidth - 50;
                }
            });
            container.appendChild(pill);
        });
    }

    function renderChrono() {
        if (!_chronoData.length) return;
        var scroll = $("lab-chrono-scroll");
        var rangeEl = $("lab-chrono-range");
        if (!scroll) return;

        var totalDuration = _chronoMaxTs - _chronoMinTs;
        if (totalDuration <= 0) totalDuration = 3600000;
        var containerWidth = scroll.clientWidth;
        var totalWidth = Math.max(containerWidth, containerWidth * _chronoZoom / 3);
        var pxPerMs = totalWidth / totalDuration;

        // Range display
        if (rangeEl) {
            var d1 = new Date(_chronoMinTs);
            var d2 = new Date(_chronoMaxTs);
            rangeEl.textContent = _ficheFormatDate(d1.toISOString().substring(0, 10)) + " \u2192 " + _ficheFormatDate(d2.toISOString().substring(0, 10)) + " (" + _chronoData.length + " fiches)";
        }

        scroll.textContent = "";
        var container = buildEl("div", "lab-chrono-container");
        container.style.cssText = "position:relative;width:" + totalWidth + "px;min-height:100%;";

        // === Upper cards zone ===
        var upperZone = buildEl("div", "lab-chrono-upper");
        upperZone.style.cssText = "position:relative;min-height:50%;padding-bottom:20px;";

        // === Axis line ===
        var axis = buildEl("div", "lab-chrono-axis");
        axis.style.cssText = "position:relative;height:30px;border-top:2px solid var(--brand);border-bottom:2px solid var(--brand);background:var(--bg);";

        // Ticks on axis — use local time (new Date auto-converts to local)
        var tickInterval = _bestTickInterval(totalDuration, totalWidth);
        var t = Math.ceil(_chronoMinTs / tickInterval) * tickInterval;
        var lastDayStr = "";
        while (t <= _chronoMaxTs + tickInterval) {
            var tickLeft = (t - _chronoMinTs) * pxPerMs;
            var dt = new Date(t);
            var h = dt.getHours();
            var m = dt.getMinutes();
            var dayStr = dt.toLocaleDateString("fr-FR");
            var isNewDay = dayStr !== lastDayStr;
            lastDayStr = dayStr;

            var tick = buildEl("div");

            if (isNewDay) {
                // Day marker: tall, bold, with background
                tick.style.cssText = "position:absolute;left:" + tickLeft + "px;top:-4px;bottom:-4px;border-left:2px solid var(--brand);z-index:3;";
                var dayLabel = buildEl("div");
                dayLabel.style.cssText = "position:absolute;top:50%;transform:translateY(-50%);left:6px;font-size:0.78rem;font-weight:700;color:#fff;white-space:nowrap;background:var(--brand);padding:2px 8px;border-radius:10px;";
                dayLabel.textContent = _joursSemaine[dt.getDay()] + " " + dt.getDate() + " " + _moisNoms[dt.getMonth()];
                tick.appendChild(dayLabel);
            } else {
                tick.style.cssText = "position:absolute;left:" + tickLeft + "px;top:0;height:100%;border-left:1px solid var(--line);";
                var hourLabel = buildEl("div");
                hourLabel.style.cssText = "position:absolute;top:50%;transform:translateY(-50%);left:4px;font-size:0.65rem;color:var(--muted);white-space:nowrap;";
                hourLabel.textContent = String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
                tick.appendChild(hourLabel);
            }
            axis.appendChild(tick);
            t += tickInterval;
        }

        // === Lower cards zone ===
        var lowerZone = buildEl("div", "lab-chrono-lower");
        lowerZone.style.cssText = "position:relative;min-height:50%;padding-top:20px;";

        // Place cards on multiple levels above and below the axis
        var isDetailed = _chronoZoom > 15;
        var cardW = isDetailed ? Math.max(150, Math.min(240, 150 + _chronoZoom)) : Math.max(55, Math.min(120, 55 + _chronoZoom));
        var cardH = isDetailed ? 68 : 30;
        var gap = 6;
        // Track occupied slots per level: topLevels[level] = lastRightEdge, bottomLevels[level] = lastRightEdge
        var maxLevels = 5;
        var topSlots = [];
        var bottomSlots = [];
        for (var li = 0; li < maxLevels; li++) { topSlots.push(-Infinity); bottomSlots.push(-Infinity); }

        _chronoData.forEach(function(it, idx) {
            var left = (it.ts - _chronoMinTs) * pxPerMs;
            var color = CAT_COLORS[it.cat] || "#6366f1";
            var isTop = idx % 2 === 0;

            // Find the lowest available level
            function findLevel(slots) {
                for (var lv = 0; lv < maxLevels; lv++) {
                    if (left >= slots[lv]) { slots[lv] = left + cardW + gap; return lv; }
                }
                // All full: use lowest overlap
                var best = 0;
                for (var lv2 = 1; lv2 < maxLevels; lv2++) { if (slots[lv2] < slots[best]) best = lv2; }
                slots[best] = left + cardW + gap;
                return best;
            }

            var level;
            if (isTop) { level = findLevel(topSlots); } else { level = findLevel(bottomSlots); }

            // Position: distance from axis = level * (cardH + gap) + base offset
            var baseOffset = 8;
            var distFromAxis = baseOffset + level * (cardH + gap);

            var card = buildEl("div");
            card.style.cssText = "position:absolute;left:" + left + "px;width:" + cardW + "px;cursor:pointer;" +
                "background:var(--card);border:1px solid var(--line);border-left:3px solid " + color + ";" +
                "border-radius:var(--radius-sm);padding:4px 8px;font-size:0.72rem;" +
                "box-shadow:var(--shadow-sm);transition:box-shadow 0.15s,transform 0.15s;z-index:1;";

            // Connector: SVG line from axis to card
            var connectorH = distFromAxis;
            var connector = buildEl("div");
            connector.style.cssText = "position:absolute;left:" + (left + 1) + "px;width:1px;background:" + color + ";opacity:0.35;z-index:0;";

            // Dot on axis
            var dot = buildEl("div");
            dot.style.cssText = "position:absolute;left:" + (left - 3) + "px;width:8px;height:8px;border-radius:50%;background:" + color + ";z-index:2;";

            if (isTop) {
                card.style.bottom = distFromAxis + "px";
                connector.style.bottom = "0";
                connector.style.height = connectorH + "px";
                dot.style.bottom = "-4px";
                upperZone.appendChild(connector);
                upperZone.appendChild(card);
                upperZone.appendChild(dot);
            } else {
                card.style.top = distFromAxis + "px";
                connector.style.top = "0";
                connector.style.height = connectorH + "px";
                dot.style.top = "-4px";
                lowerZone.appendChild(connector);
                lowerZone.appendChild(card);
                lowerZone.appendChild(dot);
            }

            // Card content
            var timeDt = new Date(it.ts);
            var timeLabel = buildEl("div");
            timeLabel.style.cssText = "font-size:0.68rem;font-weight:700;color:" + color + ";margin-bottom:1px;";
            timeLabel.textContent = String(timeDt.getHours()).padStart(2, "0") + ":" + String(timeDt.getMinutes()).padStart(2, "0");
            card.appendChild(timeLabel);

            if (isDetailed) {
                var catLabel = buildEl("div");
                catLabel.style.cssText = "font-size:0.62rem;font-weight:600;color:var(--muted);text-transform:uppercase;";
                catLabel.textContent = (it.cat || "").replace("PCO.", "").replace("PCS.", "");
                card.appendChild(catLabel);
                var textEl = buildEl("div");
                textEl.style.cssText = "font-size:0.72rem;color:var(--text);line-height:1.3;margin-top:2px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;";
                textEl.textContent = it.text || "";
                card.appendChild(textEl);
                if (it.sc) {
                    var scEl = buildEl("div");
                    scEl.style.cssText = "font-size:0.62rem;color:var(--muted);margin-top:1px;";
                    scEl.textContent = it.sc;
                    card.appendChild(scEl);
                }
            } else {
                var shortText = buildEl("div");
                shortText.style.cssText = "font-size:0.65rem;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
                shortText.textContent = (it.cat || "").replace("PCO.", "").replace("PCS.", "") + " " + (it.text || "").substring(0, 30);
                card.appendChild(shortText);
            }

            card.addEventListener("mouseenter", function() { card.style.transform = "scale(1.05)"; card.style.zIndex = "10"; card.style.boxShadow = "var(--shadow-md)"; });
            card.addEventListener("mouseleave", function() { card.style.transform = ""; card.style.zIndex = "1"; card.style.boxShadow = "var(--shadow-sm)"; });
            card.title = _ficheFormatDate(timeDt.toISOString().substring(0, 10)) + " " + String(timeDt.getHours()).padStart(2, "0") + ":" + String(timeDt.getMinutes()).padStart(2, "0") + "\n" + (it.text || "") + "\n" + (it.sc || "") + " - " + (it.op || "");
        });

        // Set min-height for zones based on max levels used
        var usedTopLevels = topSlots.filter(function(s) { return s > -Infinity; }).length;
        var usedBottomLevels = bottomSlots.filter(function(s) { return s > -Infinity; }).length;
        var topH = Math.max(100, (usedTopLevels + 1) * (cardH + gap) + 20);
        var bottomH = Math.max(100, (usedBottomLevels + 1) * (cardH + gap) + 20);
        upperZone.style.minHeight = topH + "px";
        lowerZone.style.minHeight = bottomH + "px";

        container.appendChild(upperZone);
        container.appendChild(axis);
        container.appendChild(lowerZone);
        scroll.appendChild(container);
    }

    function _bestTickInterval(durationMs, widthPx) {
        var minPxPerTick = 80;
        var candidates = [300000, 600000, 1800000, 3600000, 7200000, 14400000, 43200000, 86400000];
        for (var i = 0; i < candidates.length; i++) {
            var n = durationMs / candidates[i];
            if (widthPx / n >= minPxPerTick) return candidates[i];
        }
        return 86400000;
    }

    function toggleChronoFullscreen() {
        var chronoView = document.querySelector('.lab-view[data-view="chrono"]');
        if (!chronoView) return;
        _chronoFullscreen = !_chronoFullscreen;
        if (_chronoFullscreen) {
            chronoView.classList.add("lab-chrono-fullscreen");
        } else {
            chronoView.classList.remove("lab-chrono-fullscreen");
        }
        var icon = $("lab-chrono-fs");
        if (icon) icon.querySelector(".material-symbols-outlined").textContent = _chronoFullscreen ? "fullscreen_exit" : "fullscreen";
        setTimeout(renderChrono, 100);
    }

    // =========================================================================
    // FICHES SEARCH
    // =========================================================================
    var _fichesPage = 0;
    var _fichesQuery = "";
    var _fichesDebounce = null;

    function initFichesSearch() {
        var input = $("lab-fiches-search");
        if (!input) return;
        input.addEventListener("input", function() {
            clearTimeout(_fichesDebounce);
            _fichesDebounce = setTimeout(function() {
                _fichesQuery = input.value;
                _fichesPage = 0;
                loadFiches();
            }, 350);
        });
    }

    function loadFiches() {
        if (!currentEvent || !currentYear) return;
        var url = "/api/analyse-ops/fiches?event=" + encodeURIComponent(currentEvent)
            + "&year=" + encodeURIComponent(currentYear)
            + "&page=" + _fichesPage;
        if (_fichesQuery) url += "&q=" + encodeURIComponent(_fichesQuery);
        apiGet(url).then(function(res) {
            renderFiches(res);
        }).catch(function() {});
    }

    function renderFiches(res) {
        var list = $("lab-fiches-list");
        var countEl = $("lab-fiches-count");
        var pager = $("lab-fiches-pager");
        if (!list) return;
        list.textContent = "";
        if (countEl) countEl.textContent = res.total + " fiche" + (res.total > 1 ? "s" : "");

        if (!res.fiches || !res.fiches.length) {
            list.appendChild(buildEl("div", "lab-empty-state", "Aucune fiche trouvee."));
            return;
        }

        res.fiches.forEach(function(f) {
            var row = buildEl("div", "lab-fiche-row");
            if (f.severity >= 3) row.classList.add("sev-high");
            else if (f.severity >= 1) row.classList.add("sev-mid");
            else row.classList.add("sev-low");

            // Date + time header
            var dateStr = _ficheFormatDate(f.date_local);
            var timeStr = f.time_local ? f.time_local.substring(0, 5) : "--:--";

            var dateRow = buildEl("div");
            dateRow.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap;";
            var dayTime = buildEl("span", null, dateStr + " " + timeStr);
            dayTime.style.cssText = "font-size:0.88rem;font-weight:700;color:var(--brand);";
            dateRow.appendChild(dayTime);
            dateRow.appendChild(buildEl("span", "lab-fiche-cat", (f.category || "").replace("PCO.", "").replace("PCS.", "")));
            if (f.delay_min !== null) {
                var delayBadge = buildEl("span", null, f.delay_min + " min");
                delayBadge.style.cssText = "font-size:0.68rem;padding:1px 6px;border-radius:8px;font-weight:600;" +
                    (f.delay_min <= 30 ? "background:var(--success-light);color:var(--success);" : f.delay_min <= 60 ? "background:var(--warning-light);color:#b45309;" : "background:var(--danger-light);color:var(--danger);");
                dateRow.appendChild(delayBadge);
            }
            dateRow.appendChild(buildEl("span", "lab-fiche-operator", f.operator ? clean_op_display(f.operator) : ""));
            row.appendChild(dateRow);

            // Sous-classification line
            if (f.sous_class) {
                var scLine = buildEl("div", null, f.sous_class);
                scLine.style.cssText = "font-size:0.75rem;color:var(--text);font-weight:500;margin-bottom:3px;";
                row.appendChild(scLine);
            }

            // Text
            var txt = buildEl("div", "lab-fiche-text", f.text || "");
            row.appendChild(txt);

            // Meta line
            var meta = buildEl("div", "lab-fiche-meta");
            if (f.area) meta.appendChild(buildEl("span", null, f.area.split("/").pop()));
            if (f.appelant) meta.appendChild(buildEl("span", null, "Appelant: " + f.appelant));
            if (f.carroye) meta.appendChild(buildEl("span", null, "Carroye: " + f.carroye));
            row.appendChild(meta);

            // Expanded detail (click to toggle)
            var detail = buildEl("div", "lab-fiche-expanded");

            // Grid layout for structured fields
            var grid = buildEl("div");
            grid.style.cssText = "display:grid;grid-template-columns:1fr 1fr;gap:8px 16px;margin-bottom:10px;";

            function _field(parent, label, value) {
                if (!value && value !== 0) return;
                var wrap = buildEl("div");
                var lbl = buildEl("div", null, label);
                lbl.style.cssText = "font-size:0.68rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.3px;";
                wrap.appendChild(lbl);
                var val = buildEl("div", null, String(value));
                val.style.cssText = "font-size:0.8rem;color:var(--text);";
                wrap.appendChild(val);
                parent.appendChild(wrap);
            }

            _field(grid, "Classification", f.classification);
            _field(grid, "Sous-classification", f.sous_class);
            _field(grid, "Motif", f.motif);
            _field(grid, "Appelant", f.appelant);
            var canal = f.telephone ? "Telephone" : f.radio ? "Radio" : "";
            _field(grid, "Canal", canal);
            _field(grid, "Zone / Secteur", f.area);
            _field(grid, "Carroye", f.carroye);
            _field(grid, "Groupe", f.group);
            _field(grid, "Service contacte", f.service_contacte);
            if (f.intervenants && f.intervenants.length) {
                _field(grid, "Intervenants", f.intervenants.join(" \u2192 "));
            }
            _field(grid, "Operateur creation", f.operator ? clean_op_display(f.operator) : "");
            _field(grid, "Operateur cloture", f.operator_close ? clean_op_display(f.operator_close) : "");
            _field(grid, "Severite", f.severity);
            var statusLabel = f.status_code === 10 ? "Termine" : f.status_code === 0 ? "Ouvert" : f.status_code !== null ? "Code " + f.status_code : "";
            _field(grid, "Statut", statusLabel);
            _field(grid, "Delai traitement", f.delay_min !== null ? f.delay_min + " min" : "");
            if (f.date_heure_xml) _field(grid, "Date/heure declaree", f.date_heure_xml);
            if (f.lat && f.lon) _field(grid, "GPS", f.lat.toFixed(5) + ", " + f.lon.toFixed(5));
            if (f.extracted_phones && f.extracted_phones.length) _field(grid, "Telephones extraits", f.extracted_phones.join(", "));
            if (f.extracted_plates && f.extracted_plates.length) _field(grid, "Plaques extraites", f.extracted_plates.join(", "));
            _field(grid, "ID fiche", f.sql_id || f.id);
            detail.appendChild(grid);

            // Full text
            if (f.text_full && f.text_full !== f.text) {
                var tfLabel = buildEl("div", null, "Description complete");
                tfLabel.style.cssText = "font-size:0.68rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.3px;margin-bottom:3px;";
                detail.appendChild(tfLabel);
                var tfBlock = buildEl("div", null, f.text_full);
                tfBlock.style.cssText = "font-size:0.8rem;line-height:1.5;background:var(--bg);padding:8px 10px;border-radius:var(--radius-sm);margin-bottom:8px;white-space:pre-wrap;";
                detail.appendChild(tfBlock);
            }

            // Comment (suivi)
            if (f.comment) {
                var cmtLabel = buildEl("div", null, "Historique de suivi");
                cmtLabel.style.cssText = "font-size:0.68rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.3px;margin-bottom:3px;";
                detail.appendChild(cmtLabel);
                var cmtBlock = buildEl("div", null, f.comment);
                cmtBlock.style.cssText = "font-size:0.78rem;line-height:1.6;background:var(--bg);padding:8px 10px;border-radius:var(--radius-sm);white-space:pre-wrap;max-height:300px;overflow-y:auto;";
                detail.appendChild(cmtBlock);
            }

            row.appendChild(detail);

            row.addEventListener("click", function() { row.classList.toggle("expanded"); });
            list.appendChild(row);
        });

        // Pager
        if (pager) {
            pager.textContent = "";
            var totalPages = Math.ceil(res.total / res.per_page);
            if (totalPages > 1) {
                if (_fichesPage > 0) {
                    var prev = buildEl("button", null, "Precedent");
                    prev.addEventListener("click", function() { _fichesPage--; loadFiches(); });
                    pager.appendChild(prev);
                }
                pager.appendChild(buildEl("span", null, "Page " + (_fichesPage + 1) + " / " + totalPages));
                pager.lastChild.style.cssText = "font-size:0.78rem;color:var(--muted);";
                if (_fichesPage < totalPages - 1) {
                    var next = buildEl("button", null, "Suivant");
                    next.addEventListener("click", function() { _fichesPage++; loadFiches(); });
                    pager.appendChild(next);
                }
            }
        }
    }

    function clean_op_display(s) {
        if (!s) return "";
        return s.replace(/\s*\[.*\]\s*$/, "");
    }

    var _joursSemaine = ["Dimanche", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"];
    var _moisNoms = ["jan.", "fev.", "mars", "avr.", "mai", "juin", "juil.", "aout", "sept.", "oct.", "nov.", "dec."];

    function _ficheFormatDate(dateLocal) {
        if (!dateLocal) return "";
        try {
            var parts = dateLocal.split("-");
            var dt = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
            return _joursSemaine[dt.getDay()] + " " + dt.getDate() + " " + _moisNoms[dt.getMonth()];
        } catch (e) {
            return dateLocal;
        }
    }

    // =========================================================================
    // REPLAY CHRONOLOGIQUE
    // =========================================================================
    var _replayTimer = null;
    var _replayLayer = null;
    var _replayPlaying = false;
    var _replayIndex = 0;
    var _replayData = [];

    function initReplay() {
        var btn = $("lab-replay-btn");
        var stopBtn = $("lab-replay-stop");
        if (!btn) return;
        btn.addEventListener("click", function() {
            if (_replayPlaying) return;
            startReplay();
        });
        if (stopBtn) {
            stopBtn.addEventListener("click", function() {
                stopReplay();
            });
        }
    }

    function startReplay() {
        if (!labMapCarroye || !window._labGeoData) return;
        var geo = window._labGeoData;
        // Build sorted list of all incidents with GPS or carroye coords
        _replayData = [];
        if (geo.gps_points) {
            geo.gps_points.forEach(function(p) {
                if (p.ts) _replayData.push({lat: p.lat, lon: p.lon, ts: p.ts, cat: p.category, text: p.text, severity: p.severity});
            });
        }
        if (geo.car_points) {
            geo.car_points.forEach(function(p) {
                _replayData.push({lat: p.lat, lon: p.lon, ts: "", cat: "Carroye " + p.ref, text: p.n + " incidents", severity: 0, isCarroye: true});
            });
        }
        // Sort by timestamp
        _replayData = _replayData.filter(function(p) { return p.ts; }).sort(function(a, b) {
            return a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0;
        });
        if (_replayData.length === 0) return;

        _replayPlaying = true;
        _replayIndex = 0;
        if (_replayLayer) labMapCarroye.removeLayer(_replayLayer);
        _replayLayer = L.layerGroup().addTo(labMapCarroye);

        var btn = $("lab-replay-btn");
        var stopBtn = $("lab-replay-stop");
        if (btn) btn.classList.add("playing");
        if (stopBtn) stopBtn.style.display = "";

        var speed = parseInt(($("lab-replay-speed") || {}).value || 5);
        var interval = Math.max(30, 300 - speed * 28);

        _replayTimer = setInterval(function() {
            if (_replayIndex >= _replayData.length) {
                stopReplay();
                return;
            }
            var p = _replayData[_replayIndex];
            var catColors = {
                "PCO.Technique": "#f59e0b", "PCO.Securite": "#ef4444", "PCO.Secours": "#dc2626",
                "PCO.Information": "#2563eb", "PCS.Surete": "#7c3aed", "PCS.Information": "#06b6d4",
                "PCO.Fourriere": "#6b7280", "PCO.Flux": "#0d9488", "PCO.MainCourante": "#8b5cf6"
            };
            var color = catColors[p.cat] || "#6366f1";
            var sizeBase = p.severity >= 3 ? 10 : p.severity >= 1 ? 7 : 5;

            // Pulse circle
            var pulse = L.circleMarker([p.lat, p.lon], {
                radius: sizeBase + 8, fillColor: color, fillOpacity: 0.3,
                color: color, weight: 1, opacity: 0.5, className: "lab-replay-marker"
            }).addTo(_replayLayer);

            // Core marker
            var marker = L.circleMarker([p.lat, p.lon], {
                radius: sizeBase, fillColor: color, fillOpacity: 0.9,
                color: "#fff", weight: 2
            }).bindPopup(escapeText(p.cat) + " - " + escapeText(p.text)).addTo(_replayLayer);

            // Fade out pulse after 2s
            setTimeout(function() {
                if (_replayLayer && _replayLayer.hasLayer(pulse)) {
                    pulse.setStyle({fillOpacity: 0, opacity: 0});
                }
            }, 2000);

            // Update time display
            var timeEl = $("lab-replay-time");
            if (timeEl && p.ts) {
                var d = p.ts.substring(0, 16).replace("T", " ");
                timeEl.textContent = d;
            }

            _replayIndex++;
        }, interval);
    }

    function stopReplay() {
        _replayPlaying = false;
        if (_replayTimer) { clearInterval(_replayTimer); _replayTimer = null; }
        var btn = $("lab-replay-btn");
        var stopBtn = $("lab-replay-stop");
        if (btn) btn.classList.remove("playing");
        if (stopBtn) stopBtn.style.display = "none";
    }

    // Init fiches and replay on DOMContentLoaded — extend the existing init
    var _origDCL = document.readyState === "loading" ? null : true;
    if (_origDCL) {
        initFichesSearch();
        initReplay();
        initChrono();
    } else {
        document.addEventListener("DOMContentLoaded", function() {
            initFichesSearch();
            initReplay();
            initChrono();
        });
    }

    // Load fiches when switching to the tab
    // (piggyback on the existing center tabs listener)
    var _fichesLoaded = false;
    var _origTabObserver = setInterval(function() {
        var fichesView = document.querySelector('.lab-view[data-view="fiches"]');
        if (fichesView && fichesView.classList.contains("active") && !_fichesLoaded) {
            _fichesLoaded = true;
            loadFiches();
        }
        if (fichesView && !fichesView.classList.contains("active")) {
            _fichesLoaded = false;
        }
    }, 500);

})();
