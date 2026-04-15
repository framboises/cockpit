/* =========================================================================
   ANPR / LAPI -- Frontend (2-column layout, tabbed right panel)
   ========================================================================= */
(function () {
    "use strict";

    var CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";
    var API = {
        search:  "/api/anpr/search",
        stats:   "/api/anpr/stats",
        live:    "/api/anpr/live",
        plate:   "/api/anpr/plate/",
        image:   "/api/anpr/image/",
        cameras: "/api/anpr/cameras",
        camCfg:  "/api/anpr/cameras/config",
        onsite:  "/api/anpr/onsite",
        onsiteReset: "/api/anpr/onsite/reset",
        visionLookup: "/api/anpr/vision/lookup/",
        visionBatch:  "/api/anpr/vision/batch",
        visionSearch: "/api/anpr/vision/search",
        visionStats:  "/api/anpr/vision/stats",
    };

    function imgUrl(r, type) {
        var path = r[type + "_image_path"];
        var id   = r[type + "_image_id"];
        if (path) return API.image + encodeURIComponent(path);
        if (id)   return API.image + encodeURIComponent(id);
        return null;
    }

    function qs(s, r) { return (r || document).querySelector(s); }
    function qsa(s, r) { return (r || document).querySelectorAll(s); }
    function mk(tag, cls, txt) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (txt !== undefined) e.textContent = txt;
        return e;
    }
    async function get(url) { var r = await fetch(url); if (!r.ok) throw r.status; return r.json(); }
    async function post(url, b) {
        var r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF }, body: JSON.stringify(b) });
        if (!r.ok) throw r.status; return r.json();
    }

    var COLOR_FR = { white:"Blanc", black:"Noir", gray:"Gris", blue:"Bleu", red:"Rouge", green:"Vert", yellow:"Jaune", brown:"Marron", pink:"Rose", cyan:"Cyan" };
    function hex(c) { return { white:"#ddd", black:"#1a1a2e", gray:"#6b7280", blue:"#3b82f6", red:"#ef4444", green:"#22c55e", yellow:"#eab308", brown:"#92400e", pink:"#ec4899", cyan:"#06b6d4" }[c] || "#888"; }

    function fmtDt(iso) { if (!iso) return "--"; var d = new Date(iso); return d.toLocaleDateString("fr-FR",{day:"2-digit",month:"2-digit",year:"numeric"}) + " " + d.toLocaleTimeString("fr-FR",{hour:"2-digit",minute:"2-digit",second:"2-digit"}); }
    function fmtTm(iso) { if (!iso) return "--"; return new Date(iso).toLocaleTimeString("fr-FR",{hour:"2-digit",minute:"2-digit",second:"2-digit"}); }

    function animVal(el, end) {
        var s = parseInt(el.textContent) || 0; if (s === end) { el.textContent = end; return; }
        var d = end - s, dur = Math.min(600, Math.abs(d) * 8), t0 = null;
        function step(ts) { if (!t0) t0 = ts; var p = Math.min(1, (ts - t0) / dur); el.textContent = Math.round(s + d * (1 - Math.pow(1 - p, 3))); if (p < 1) requestAnimationFrame(step); }
        requestAnimationFrame(step);
    }

    Chart.defaults.font.family = "'Outfit',sans-serif";
    Chart.defaults.font.size = 11;
    Chart.defaults.color = "#607286";
    Chart.defaults.plugins.legend.display = false;
    Chart.defaults.animation.duration = 500;

    var LIEU_COLORS = { "Ouest": "#3b82f6", "Panorama": "#22c55e", "Houx": "#f59e0b" };
    function mkLieuPill(lieu) {
        if (!lieu) return null;
        var pill = mk("span", "anpr-lieu-pill");
        pill.textContent = lieu;
        pill.style.cssText = "font-size:0.65rem; padding:1px 6px; border-radius:9px; font-weight:600; margin-left:5px; color:#fff; background:" + (LIEU_COLORS[lieu] || "#6b7280") + ";";
        return pill;
    }

    var charts = {}, page = 1, statsLoaded = false, searchLoaded = false, lastLiveTop = null;
    var visionStatsLoaded = false;
    var _watchlistPlates = {};  // plate -> {_id, label, enabled}

    function loadWatchlist() {
        fetch("/api/anpr-watchlist").then(function(r){ return r.json(); }).then(function(list){
            _watchlistPlates = {};
            (list || []).forEach(function(w){
                _watchlistPlates[w.plate] = w;
            });
        }).catch(function(){});
    }

    function isWatched(plate) {
        return !!_watchlistPlates[(plate || "").toUpperCase().replace(/\s+/g, "-")];
    }

    function addToWatchlist(plate, label) {
        var normalPlate = (plate || "").toUpperCase().replace(/\s+/g, "-");
        return fetch("/api/anpr-watchlist", {
            method: "POST",
            headers: {"Content-Type": "application/json", "X-CSRFToken": CSRF},
            body: JSON.stringify({plate: normalPlate, label: label || ""})
        }).then(function(r){ return r.json(); }).then(function(res){
            if(!res.error){
                _watchlistPlates[normalPlate] = res;
                if(typeof showToast === "function") showToast("Plaque " + normalPlate + " ajoutee a la watchlist", "success");
            } else {
                if(typeof showToast === "function") showToast(res.error, "warning");
            }
            return res;
        });
    }

    function removeFromWatchlist(plate) {
        var normalPlate = (plate || "").toUpperCase().replace(/\s+/g, "-");
        var w = _watchlistPlates[normalPlate];
        if(!w) return Promise.resolve();
        return fetch("/api/anpr-watchlist/" + w._id, {
            method: "DELETE",
            headers: {"X-CSRFToken": CSRF}
        }).then(function(r){ return r.json(); }).then(function(){
            delete _watchlistPlates[normalPlate];
            if(typeof showToast === "function") showToast("Plaque " + normalPlate + " retiree de la watchlist", "success");
        });
    }

    /* ---- init ---- */
    function init() {
        loadWatchlist();
        buildChips();
        loadKPIs();
        loadOnsite();
        loadLive();
        loadSearch();
        searchLoaded = true;
        bind();
        setInterval(loadLive, 5000);
        setInterval(loadOnsite, 15000);
    }

    /* ---- color chips (circles) ---- */
    function buildChips() {
        var c = qs("#anpr-color-chips");
        ["white","black","gray","blue","red","green","yellow","brown","pink","cyan"].forEach(function (col) {
            var btn = mk("button", "anpr-color-chip");
            btn.dataset.color = col;
            btn.title = COLOR_FR[col];
            btn.style.setProperty("--chip-color", hex(col));
            btn.style.background = hex(col);
            btn.addEventListener("click", function () {
                var was = btn.classList.contains("active");
                qsa(".anpr-color-chip.active").forEach(function (b) { b.classList.remove("active"); });
                if (!was) btn.classList.add("active");
                page = 1; loadSearch();
            });
            c.appendChild(btn);
        });
    }

    /* ---- KPIs ---- */
    async function loadKPIs() {
        try {
            var d = await get(API.stats);
            animVal(qs("#kpi-total"), d.total);
            animVal(qs("#kpi-unique"), d.unique_plates);
            animVal(qs("#kpi-allowlist"), d.allowlist_count);
            qs("#kpi-confidence").textContent = d.avg_confidence + "%";
            window._anprStats = d;
            fillFilters(d);
        } catch (e) { console.error("KPI", e); }
        // Vision KPI
        try {
            var vs = await get(API.visionStats);
            animVal(qs("#kpi-vision-matched"), vs.matched);
            window._visionStats = vs;
        } catch (e) { /* Vision not configured, hide KPI */ qs("#kpi-vision-matched").textContent = "--"; }
    }

    function fillFilters(d) {
        var ts = qs("#anpr-filter-type"), bs = qs("#anpr-filter-brand"), cs = qs("#anpr-filter-camera");
        if (ts.options.length <= 1) (d.by_type || []).forEach(function (t) { ts.appendChild(new Option(t.label, t.type)); });
        if (bs.options.length <= 1) (d.by_brand || []).forEach(function (b) { bs.appendChild(new Option(b.brand, b.brand)); });
        if (cs.options.length <= 1) Object.keys(d.by_camera || {}).sort().forEach(function (c) { cs.appendChild(new Option(c, c)); });
    }

    /* ---- on-site counter ---- */
    async function loadOnsite() {
        try {
            var d = await get(API.onsite);
            animVal(qs("#kpi-onsite"), d.on_site);
        } catch (e) { console.error("Onsite", e); }
    }

    /* ---- tabs ---- */
    function switchTab(tab) {
        qsa("[data-anpr-tab]").forEach(function (b) { b.classList.toggle("active", b.dataset.anprTab === tab); });
        qsa(".anpr-tab-pane").forEach(function (p) { p.classList.toggle("active", p.id === "pane-" + tab); });
        if (tab === "stats" && !statsLoaded) { statsLoaded = true; loadStats(); }
        if (tab === "search" && !searchLoaded) { searchLoaded = true; loadSearch(); }
        if (tab === "stats") setTimeout(function () { Object.values(charts).forEach(function (c) { if (c) c.resize(); }); }, 60);
    }

    function switchStatsSub(sub) {
        qsa("[data-stats-sub]").forEach(function (b) { b.classList.toggle("active", b.dataset.statsSub === sub); });
        qsa(".anpr-stats-subpane").forEach(function (p) { p.classList.toggle("active", p.id === "stats-sub-" + sub); });
        if (sub === "vision" && !visionStatsLoaded) { visionStatsLoaded = true; loadVisionStats(); }
        if (sub === "anpr") setTimeout(function () { Object.values(charts).forEach(function (c) { if (c) c.resize(); }); }, 60);
        if (sub === "vision") setTimeout(function () { ["visionLieux", "visionCross"].forEach(function (k) { if (charts[k]) charts[k].resize(); }); }, 60);
    }

    /* ---- stats ---- */
    async function loadStats() {
        try {
            var d = window._anprStats || (await get(API.stats));
            mkChart("colors", "#chart-colors", "doughnut", colorData(d.by_color), { cutout: "60%", plugins: { legend: { display: true, position: "right", labels: { boxWidth: 10, padding: 6, font: { size: 10 } } } } });
            mkChart("brands", "#chart-brands", "bar", brandData(d.by_brand), { indexAxis: "y", scales: { x: { grid: { display: false } }, y: { grid: { display: false }, ticks: { font: { size: 10 } } } } });
            mkChart("hourly", "#chart-hourly", "line", hourData(d.by_hour), { scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: "rgba(0,0,0,0.04)" } } }, plugins: { legend: { display: false } } });
            mkChart("types", "#chart-types", "doughnut", typeData(d.by_type), { cutout: "55%", plugins: { legend: { display: true, position: "right", labels: { boxWidth: 10, padding: 6, font: { size: 10 } } } } });
            mkChart("cameras", "#chart-cameras", "bar", camData(d.by_camera), { scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: "rgba(0,0,0,0.04)" } } } });
        } catch (e) { console.error("Stats", e); }
    }

    function mkChart(key, sel, type, data, opts) {
        if (charts[key]) charts[key].destroy();
        charts[key] = new Chart(qs(sel), { type: type, data: data, options: Object.assign({ responsive: true, maintainAspectRatio: false }, opts) });
    }
    function colorData(m) { var l = [], v = [], b = []; Object.keys(m || {}).forEach(function (c) { l.push(COLOR_FR[c] || c); v.push(m[c]); b.push(hex(c)); }); return { labels: l, datasets: [{ data: v, backgroundColor: b, borderWidth: 1.5, borderColor: "#fff" }] }; }
    function brandData(m) { var l = [], v = []; (m || []).slice(0, 10).forEach(function (b) { l.push(b.brand); v.push(b.count); }); return { labels: l, datasets: [{ data: v, backgroundColor: "rgba(37,99,235,0.7)", borderRadius: 3, borderSkipped: false }] }; }
    function hourData(m) { var l = [], v = []; for (var h = 0; h < 24; h++) { l.push(h + "h"); v.push((m || {})[h] || 0); } return { labels: l, datasets: [{ data: v, borderColor: "#2563eb", backgroundColor: "rgba(37,99,235,0.06)", fill: true, tension: 0.4, pointRadius: 2, pointHoverRadius: 5, borderWidth: 2 }] }; }
    function typeData(m) { var l = [], v = [], bg = ["#2563eb","#7c3aed","#ec4899","#f59e0b","#10b981","#6366f1","#06b6d4"]; (m || []).forEach(function (t) { l.push(t.label); v.push(t.count); }); return { labels: l, datasets: [{ data: v, backgroundColor: bg.slice(0, l.length), borderWidth: 1.5, borderColor: "#fff" }] }; }
    function camData(m) { var l = [], v = [], bg = ["#2563eb","#7c3aed","#ec4899","#f59e0b","#10b981"]; Object.keys(m || {}).sort().forEach(function (c) { l.push(c); v.push(m[c]); }); return { labels: l, datasets: [{ data: v, backgroundColor: bg.slice(0, l.length), borderRadius: 4, borderSkipped: false }] }; }

    /* ---- search / table ---- */
    var sourceFilter = "";  // "", "vision", "anpr"

    async function loadSearch() {
        var p = new URLSearchParams(); p.set("page", page); p.set("per_page", 50);
        var plate = qs("#anpr-filter-plate")?.value?.trim(); if (plate) p.set("plate", plate);
        var ac = qs(".anpr-color-chip.active"); if (ac) p.set("color", ac.dataset.color);
        var v;
        v = qs("#anpr-filter-type")?.value; if (v) p.set("type", v);
        v = qs("#anpr-filter-brand")?.value; if (v) p.set("brand", v);
        v = qs("#anpr-filter-camera")?.value; if (v) p.set("camera", v);
        v = qs("#anpr-filter-from")?.value; if (v) p.set("from", v);
        v = qs("#anpr-filter-to")?.value; if (v) p.set("to", v);
        if (sourceFilter) p.set("source", sourceFilter);
        try {
            var d = await get(API.search + "?" + p.toString());
            renderTable(d.results);
            qs("#anpr-result-count").textContent = d.total + " resultat" + (d.total !== 1 ? "s" : "");
            renderPag(d.page, d.pages);
        } catch (e) { console.error("Search", e); }
    }

    function renderTable(rows) {
        var tb = qs("#anpr-table-body"); tb.textContent = "";
        if (!rows || !rows.length) { var tr = document.createElement("tr"); var td = mk("td", "anpr-empty", "Aucun resultat"); td.setAttribute("colspan", "11"); tr.appendChild(td); tb.appendChild(tr); return; }
        rows.forEach(function (r) { tb.appendChild(mkRow(r)); });
    }

    function mkRow(r) {
        var isVision = r.source === "vision";
        var tr = document.createElement("tr"); tr.className = "anpr-row" + (isVision ? " anpr-row-vision" : "");
        // img
        var tdi = document.createElement("td"); tdi.className = "anpr-td-img";
        if (isVision) {
            if (r.photo_vehicule) { var im = document.createElement("img"); im.className = "anpr-thumb"; im.src = r.photo_vehicule; im.loading = "lazy"; tdi.appendChild(im); }
            else { var ne = mk("div", "anpr-thumb-empty"); ne.appendChild(mk("span", "material-symbols-outlined", "confirmation_number")); ne.style.color = "#f59e0b"; tdi.appendChild(ne); }
        } else {
            var vehicleUrl = imgUrl(r, "vehicle");
            if (vehicleUrl) { var im2 = document.createElement("img"); im2.className = "anpr-thumb"; im2.src = vehicleUrl; im2.loading = "lazy"; tdi.appendChild(im2); }
            else { var ne2 = mk("div", "anpr-thumb-empty"); ne2.appendChild(mk("span", "material-symbols-outlined", "no_photography")); tdi.appendChild(ne2); }
        }
        tr.appendChild(tdi);
        // plate
        var tdp = document.createElement("td"); tdp.appendChild(mk("span", "anpr-plate-badge" + (r.list_name === "allowList" ? " anpr-plate-allow" : ""), r.plate));
        if (isVision && r.lieu) { var pill = mkLieuPill(r.lieu); if (pill) tdp.appendChild(pill); }
        if(isWatched(r.plate)){ var wi = mk("span","material-symbols-outlined"); wi.style.cssText="font-size:13px;color:#dc2626;margin-left:4px;vertical-align:middle;"; wi.textContent="visibility"; wi.title="Plaque surveillee"; tdp.appendChild(wi); }
        tr.appendChild(tdp);
        // source
        var tds = document.createElement("td");
        var srcBadge = mk("span", "anpr-source-badge anpr-source-" + (isVision ? "vision" : "anpr"), isVision ? "Vision" : "ANPR");
        tds.appendChild(srcBadge);
        tr.appendChild(tds);
        // conf
        tr.appendChild(mk("td", "anpr-conf anpr-conf-" + (r.confidence >= 90 ? "high" : r.confidence >= 60 ? "med" : "low"), isVision ? "" : r.confidence + "%"));
        // color (ANPR english or Vision french)
        var tdc = document.createElement("td");
        if (r.color_hex) { var dot = mk("span", "anpr-color-dot"); dot.style.background = r.color_hex; tdc.appendChild(dot); tdc.appendChild(document.createTextNode(" " + (COLOR_FR[r.color] || r.color || ""))); }
        tr.appendChild(tdc);
        tr.appendChild(mk("td", "", r.brand || ""));
        tr.appendChild(mk("td", "", r.type_label || ""));
        tr.appendChild(mk("td", "", isVision ? "" : r.camera));
        // dir
        var tdd = document.createElement("td");
        if (isVision) { tdd.textContent = "--"; tdd.style.opacity = "0.3"; }
        else {
            var rd = r.resolved_dir || "unknown";
            var ds = mk("span", "anpr-dir anpr-dir-" + rd);
            var di = mk("span", "material-symbols-outlined"); di.style.fontSize = "14px";
            di.textContent = rd === "entry" ? "login" : rd === "exit" ? "logout" : "help";
            ds.appendChild(di);
            ds.appendChild(document.createTextNode(rd === "entry" ? " Entree" : rd === "exit" ? " Sortie" : " ?"));
            tdd.appendChild(ds);
        }
        tr.appendChild(tdd);
        tr.appendChild(mk("td", "", fmtDt(r.event_dt)));
        // divers
        var tdDiv = document.createElement("td");
        if (isVision) {
            var parts = [];
            if (r.lieu) parts.push(r.lieu);
            if (r.billets_count) parts.push(r.billets_count + " billet(s)");
            if (r.commentaire) parts.push(r.commentaire);
            tdDiv.textContent = parts.join(" · ") || "--";
            tdDiv.style.fontSize = "0.8rem";
        }
        tr.appendChild(tdDiv);
        tr.addEventListener("click", function () { openDetail(r); });
        return tr;
    }

    function renderPag(pg, pages) {
        var c = qs("#anpr-pagination"); c.textContent = ""; if (pages <= 1) return;
        function btn(l, p, act, dis) { var b = mk("button", "anpr-page-btn" + (act ? " active" : ""), l); if (dis) b.disabled = true; else b.addEventListener("click", function () { page = p; loadSearch(); }); return b; }
        function ib(icon, p, dis) { var b = document.createElement("button"); b.className = "anpr-page-btn"; if (dis) b.disabled = true; else b.addEventListener("click", function () { page = p; loadSearch(); }); var i = mk("span", "material-symbols-outlined", icon); i.style.fontSize = "14px"; b.appendChild(i); return b; }
        c.appendChild(ib("chevron_left", pg - 1, pg <= 1));
        var s = Math.max(1, pg - 2), e = Math.min(pages, pg + 2);
        if (s > 1) { c.appendChild(btn("1", 1)); if (s > 2) c.appendChild(mk("span", "anpr-page-dots", "...")); }
        for (var i = s; i <= e; i++) c.appendChild(btn(String(i), i, i === pg));
        if (e < pages) { if (e < pages - 1) c.appendChild(mk("span", "anpr-page-dots", "...")); c.appendChild(btn(String(pages), pages)); }
        c.appendChild(ib("chevron_right", pg + 1, pg >= pages));
    }

    /* ---- live feed ---- */
    async function loadLive() {
        try {
            var rows = await get(API.live + "?n=20");
            renderFeed(rows);
            // Enrich ANPR items with Vision lieu pills only if feed was rebuilt
            if (_feedRebuilt) enrichFeedWithVision(rows);
        } catch (e) { console.error("Live", e); }
    }

    var _feedRebuilt = false;
    function renderFeed(rows) {
        _feedRebuilt = false;
        if (!rows || !rows.length) return;
        // Skip rebuild if nothing changed
        var topId = rows[0].id;
        if (topId === lastLiveTop) return;
        var prevTop = lastLiveTop;
        lastLiveTop = topId;
        _feedRebuilt = true;
        var c = qs("#anpr-live-feed"); c.textContent = "";
        (rows || []).forEach(function (r, i) {
            var isVision = r.source === "vision";
            var isNew = prevTop !== null && r.id === topId;
            var itemCls = "anpr-feed-item" + (isNew ? " anpr-feed-new" : "") + (isVision ? " anpr-feed-item-vision" : "");
            var item = mk("div", itemCls);

            // Thumbnail with source badge overlay
            var th = mk("div", "anpr-feed-thumb");
            var srcBadge = mk("span", "anpr-feed-source " + (isVision ? "anpr-feed-source-vision" : "anpr-feed-source-anpr"), isVision ? "VISION" : "LAPI");
            th.appendChild(srcBadge);
            if (isVision && r.photo_vehicule) {
                var im = document.createElement("img"); im.src = r.photo_vehicule; im.loading = "lazy"; th.appendChild(im);
            } else {
                var feedVehicleUrl = imgUrl(r, "vehicle");
                if (feedVehicleUrl) { var im2 = document.createElement("img"); im2.src = feedVehicleUrl; im2.loading = "lazy"; th.appendChild(im2); }
                else th.appendChild(mk("span", "material-symbols-outlined", isVision ? "confirmation_number" : "directions_car"));
            }
            item.appendChild(th);

            // Info: plate + meta
            var info = mk("div", "anpr-feed-info");
            var plateBadge = mk("span", "anpr-plate-badge anpr-plate-sm" + (r.list_name === "allowList" ? " anpr-plate-allow" : ""), r.plate);
            info.appendChild(plateBadge);
            if (isWatched(r.plate)) {
                var wIcon = mk("span", "material-symbols-outlined");
                wIcon.style.cssText = "font-size:12px; color:#dc2626; margin-top:2px;";
                wIcon.textContent = "visibility";
                wIcon.title = "Plaque surveillee";
                info.appendChild(wIcon);
            }
            var meta = mk("span", "anpr-feed-meta");
            if (r.color_hex) {
                var dot = mk("span", "anpr-color-dot"); dot.style.background = r.color_hex; dot.style.width = "6px"; dot.style.height = "6px"; meta.appendChild(dot);
            }
            if (isVision) {
                var desc = [r.brand, r.type_label].filter(Boolean).join(" ");
                if (r.lieu) desc += (desc ? " \u00b7 " : "") + r.lieu;
                meta.appendChild(document.createTextNode(" " + desc));
            } else {
                meta.appendChild(document.createTextNode(" " + r.brand));
            }
            info.appendChild(meta);
            item.appendChild(info);

            // Footer: camera/lieu + time
            var right = mk("div", "anpr-feed-right");
            if (isVision && r.lieu) {
                var lieuPill = mkLieuPill(r.lieu);
                if (lieuPill) right.appendChild(lieuPill);
            } else if (r.camera) {
                right.appendChild(mk("span", "anpr-feed-cam", r.camera));
            }
            right.appendChild(mk("span", "anpr-feed-time", fmtTm(r.event_dt)));
            item.appendChild(right);

            item.addEventListener("click", function () { openDetail(r); });
            c.appendChild(item);
        });
    }

    /* ---- Vision batch enrichment ---- */
    function enrichWithVision(rows, rowSelector) {
        var plates = (rows || []).map(function (r) { return r.plate; }).filter(function (p) { return p && p !== "UNKNOWN"; });
        if (!plates.length) return;
        var unique = plates.filter(function (v, i, a) { return a.indexOf(v) === i; });
        get(API.visionBatch + "?plates=" + unique.join(",")).then(function (visionData) {
            if (!visionData || !Object.keys(visionData).length) return;
            var els = qsa(rowSelector);
            els.forEach(function (el, i) {
                var r = rows[i];
                if (!r || !visionData[r.plate]) return;
                var badge = el.querySelector(".anpr-plate-badge");
                if (!badge) return;
                var pill = mkLieuPill(visionData[r.plate].lieu);
                if (pill) badge.parentNode.insertBefore(pill, badge.nextSibling);
            });
        }).catch(function () {});
    }

    function enrichFeedWithVision(rows) {
        var plates = (rows || []).map(function (r) { return r.plate; }).filter(function (p) { return p && p !== "UNKNOWN"; });
        if (!plates.length) return;
        var unique = plates.filter(function (v, i, a) { return a.indexOf(v) === i; });
        get(API.visionBatch + "?plates=" + unique.join(",")).then(function (visionData) {
            if (!visionData || !Object.keys(visionData).length) return;
            qsa(".anpr-feed-item", qs("#anpr-live-feed")).forEach(function (item, i) {
                var r = rows[i];
                if (!r || !visionData[r.plate]) return;
                var badge = item.querySelector(".anpr-plate-badge");
                if (!badge) return;
                var pill = mkLieuPill(visionData[r.plate].lieu);
                if (pill) badge.parentNode.insertBefore(pill, badge.nextSibling);
            });
        }).catch(function () {});
    }

    /* ---- detail modal ---- */
    function openDetail(r) {
        var isVision = r.source === "vision";
        var imgDiv = qs("#anpr-modal-image"); imgDiv.textContent = "";

        // Main image: ANPR vehicle image or Vision photo
        var mainImgUrl = isVision ? r.photo_vehicule : imgUrl(r, "vehicle");
        if (mainImgUrl) {
            var im = document.createElement("img"); im.src = mainImgUrl;
            im.style.cssText = "max-width:100%;max-height:300px;object-fit:contain;border-radius:8px;";
            imgDiv.appendChild(im);
        } else {
            var nd = mk("div", "anpr-modal-no-image");
            nd.appendChild(mk("span", "material-symbols-outlined", isVision ? "confirmation_number" : "no_photography"));
            var p = document.createElement("p"); p.textContent = "Image non disponible"; nd.appendChild(p); imgDiv.appendChild(nd);
        }
        if (!isVision) {
            var modalPlateUrl = imgUrl(r, "plate");
            if (modalPlateUrl) { var pd = mk("div", "anpr-modal-plate-img"); var pi = document.createElement("img"); pi.src = modalPlateUrl; pd.appendChild(pi); imgDiv.appendChild(pd); }
        }

        var info = qs("#anpr-modal-info"); info.textContent = "";
        info.appendChild(mk("div", "anpr-modal-plate" + (r.list_name === "allowList" ? " anpr-plate-allow" : ""), r.plate));
        var det = mk("div", "anpr-modal-details");

        if (isVision) {
            // Vision-specific details
            var billetsTxt = (r.billets && r.billets.length) ? r.billets.length + " billet(s): " + r.billets.join(", ") : "Aucun billet";
            var vehTxt = [r.color, r.brand, r.type_label].filter(function(v){ return v; }).join(" ");
            [
                { i: "confirmation_number", t: "Source: Vision" },
                vehTxt ? { i: "directions_car", t: vehTxt, c: r.color_hex } : null,
                r.lieu ? { i: "location_on", t: "Parking: " + r.lieu } : null,
                { i: "receipt_long", t: billetsTxt },
                r.commentaire ? { i: "chat", t: r.commentaire } : null,
                { i: "schedule", t: fmtDt(r.event_dt) },
            ].forEach(function (row) {
                if (!row) return;
                var d = mk("div", "anpr-detail-row");
                d.appendChild(mk("span", "material-symbols-outlined", row.i));
                if (row.c) { var dt = mk("span", "anpr-color-dot"); dt.style.background = row.c; d.appendChild(dt); }
                d.appendChild(document.createTextNode(row.t)); det.appendChild(d);
            });
        } else {
            // ANPR-specific details
            [{ i: "palette", c: r.color_hex, t: COLOR_FR[r.color] || r.color }, { i: "directions_car", t: r.brand }, { i: "category", t: r.type_label }, { i: "videocam", t: r.camera }, { i: "schedule", t: fmtDt(r.event_dt) }, { i: "speed", t: "Confiance: " + r.confidence + "%" },
             r.list_name === "allowList" ? { i: "verified", t: "Liste autorisee", cls: "anpr-detail-allow" } : null
            ].forEach(function (row) {
                if (!row) return;
                var d = mk("div", "anpr-detail-row" + (row.cls ? " " + row.cls : ""));
                d.appendChild(mk("span", "material-symbols-outlined", row.i));
                if (row.c) { var dt = mk("span", "anpr-color-dot"); dt.style.background = row.c; d.appendChild(dt); }
                d.appendChild(document.createTextNode(row.t)); det.appendChild(d);
            });
        }
        info.appendChild(det);

        // Bouton watchlist
        if(r.plate && r.plate !== "UNKNOWN"){
            var watchBtn = mk("button", "anpr-watchlist-btn");
            var watched = isWatched(r.plate);
            var wbi = mk("span", "material-symbols-outlined");
            wbi.textContent = watched ? "visibility_off" : "visibility";
            wbi.style.fontSize = "16px";
            watchBtn.appendChild(wbi);
            watchBtn.appendChild(document.createTextNode(watched ? " Retirer de la watchlist" : " Surveiller cette plaque"));
            watchBtn.style.cssText = "margin-top:12px; display:flex; align-items:center; gap:6px; padding:8px 14px; border-radius:8px; border:1px solid " + (watched ? "#ef444444" : "#3b82f644") + "; background:" + (watched ? "#ef444411" : "#3b82f611") + "; color:" + (watched ? "#ef4444" : "#3b82f6") + "; cursor:pointer; font-size:0.85rem; font-weight:500;";
            watchBtn.addEventListener("click", function(){
                if(isWatched(r.plate)){
                    removeFromWatchlist(r.plate).then(function(){ openDetail(r); });
                } else {
                    addToWatchlist(r.plate).then(function(){ openDetail(r); });
                }
            });
            info.appendChild(watchBtn);
        }

        // Vision cross-reference section (always, for any plate)
        var visionDiv = qs("#anpr-modal-vision");
        visionDiv.style.display = "none";
        visionDiv.textContent = "";

        var hist = qs("#anpr-modal-history"); hist.textContent = "";
        var ld = mk("div", "anpr-history-loading"); ld.appendChild(mk("div", "anpr-spinner")); ld.appendChild(document.createTextNode(" Chargement...")); hist.appendChild(ld);
        qs("#anpr-modal").style.display = "flex"; document.body.style.overflow = "hidden";

        if (r.plate && r.plate !== "UNKNOWN") {
            // Always load Vision data
            get(API.visionLookup + encodeURIComponent(r.plate)).then(function (v) {
                visionDiv.textContent = "";
                if (v.found) {
                    visionDiv.style.display = "";
                    var title = mk("div", "anpr-vision-title");
                    title.appendChild(mk("span", "material-symbols-outlined", "confirmation_number"));
                    title.appendChild(document.createTextNode(" Vision \u2014 " + v.evenement + " " + v.annee));
                    visionDiv.appendChild(title);
                    var vVehTxt = [v.couleur, v.marque, v.modele].filter(function(x){ return x; }).join(" ");
                    var vrows = [
                        vVehTxt ? { i: "directions_car", t: vVehTxt } : null,
                        { i: "location_on", t: "Parking: " + v.lieu },
                        { i: "receipt_long", t: v.billets.length + " billet(s)" + (v.billets.length ? ": " + v.billets.join(", ") : "") },
                        { i: "event", t: "Enregistre le " + fmtDt(v.date) },
                    ];
                    if (v.commentaire) vrows.push({ i: "chat", t: v.commentaire });
                    vrows.forEach(function (row) {
                        var d = mk("div", "anpr-detail-row");
                        d.appendChild(mk("span", "material-symbols-outlined", row.i));
                        d.appendChild(document.createTextNode(row.t));
                        visionDiv.appendChild(d);
                    });
                    if (!isVision && v.photo_vehicule) {
                        var pDiv = mk("div", ""); var pImg = document.createElement("img");
                        pImg.src = v.photo_vehicule; pImg.style.cssText = "max-width:100%;max-height:150px;object-fit:contain;border-radius:6px;margin-top:8px;";
                        pDiv.appendChild(pImg); visionDiv.appendChild(pDiv);
                    }
                } else if (v.blacklisted) {
                    visionDiv.style.display = "";
                    var warn = mk("div", "anpr-vision-blacklist");
                    warn.appendChild(mk("span", "material-symbols-outlined", "gpp_bad"));
                    warn.appendChild(document.createTextNode(" BLACKLIST Vision: " + v.blacklisted.raison));
                    visionDiv.appendChild(warn);
                }
            }).catch(function () {});

            // Load unified history (ANPR + Vision)
            get(API.plate + encodeURIComponent(r.plate)).then(function (data) {
                hist.textContent = "";
                if (!data.count || data.count === 0) { hist.appendChild(mk("div", "anpr-history-empty", "Aucun passage")); return; }
                var nAnpr = data.records.filter(function(h){ return h.source === "anpr"; }).length;
                var nVision = data.records.filter(function(h){ return h.source === "vision"; }).length;
                var label = data.count + " evenement(s)";
                if (nAnpr && nVision) label += " (" + nAnpr + " ANPR, " + nVision + " Vision)";
                var t = mk("div", "anpr-history-title"); t.appendChild(mk("span", "material-symbols-outlined", "history")); t.appendChild(document.createTextNode(" " + label)); hist.appendChild(t);
                var list = mk("div", "anpr-history-list");
                data.records.forEach(function (h) {
                    var it = mk("div", "anpr-history-item");
                    if (h.source === "vision") {
                        // Vision entry
                        var vBadge = mk("span", "anpr-source-badge anpr-source-vision", "Vision");
                        it.appendChild(vBadge);
                        if (h.color_hex) { var vdt = mk("span", "anpr-color-dot"); vdt.style.background = h.color_hex; it.appendChild(vdt); }
                        it.appendChild(mk("span", "", fmtDt(h.event_dt)));
                        if (h.lieu) it.appendChild(mk("span", "anpr-feed-cam", h.lieu));
                        var vInfo = [h.marque, h.modele].filter(function(x){ return x; }).join(" ");
                        if (vInfo) it.appendChild(mk("span", "", vInfo));
                        if (h.billets_count) it.appendChild(mk("span", "", h.billets_count + " billet(s)"));
                    } else {
                        // ANPR detection
                        var dt = mk("span", "anpr-color-dot"); dt.style.background = h.color_hex; it.appendChild(dt);
                        it.appendChild(mk("span", "", fmtDt(h.event_dt))); it.appendChild(mk("span", "anpr-feed-cam", h.camera));
                        var hrd = h.resolved_dir || "unknown";
                        var ds = mk("span", "anpr-dir anpr-dir-" + hrd); var di = mk("span", "material-symbols-outlined"); di.style.fontSize = "14px";
                        di.textContent = hrd === "entry" ? "login" : hrd === "exit" ? "logout" : "help";
                        ds.appendChild(di); ds.appendChild(document.createTextNode(hrd === "entry" ? " E" : hrd === "exit" ? " S" : "")); it.appendChild(ds);
                    }
                    list.appendChild(it);
                }); hist.appendChild(list);
            }).catch(function () { hist.textContent = ""; hist.appendChild(mk("div", "anpr-history-empty", "Erreur")); });
        } else { hist.textContent = ""; }
    }

    function closeModal(id) { qs("#" + id).style.display = "none"; document.body.style.overflow = ""; }

    /* ---- camera config ---- */
    async function openCamCfg() {
        var modal = qs("#anpr-config-modal"), body = qs("#anpr-config-body");
        body.textContent = ""; body.appendChild(mk("div", "anpr-spinner")); modal.style.display = "flex";
        try {
            var cams = await get(API.cameras); body.textContent = "";
            cams.forEach(function (cam) {
                var row = mk("div", "anpr-cam-config-row");
                // Left: icon + label + path
                var nd = mk("div", "anpr-cam-config-name");
                nd.appendChild(mk("span", "material-symbols-outlined", "videocam"));
                var li = document.createElement("input"); li.type = "text"; li.className = "anpr-cam-label"; li.value = cam.label; li.dataset.path = cam.camera_path;
                nd.appendChild(li);
                nd.appendChild(mk("span", "anpr-cam-path", cam.camera_path));
                row.appendChild(nd);
                // Right: direction + lieu
                var rd = mk("div", "anpr-cam-config-role");
                rd.appendChild(mk("label", "", "Direction"));
                var sel = document.createElement("select"); sel.className = "anpr-cam-role"; sel.dataset.path = cam.camera_path;
                var o1 = new Option("Entree", "entry"), o2 = new Option("Sortie", "exit");
                if (cam.forward_role === "exit") o2.selected = true; else o1.selected = true;
                sel.appendChild(o1); sel.appendChild(o2); rd.appendChild(sel);
                rd.appendChild(mk("label", "", "Lieu Vision"));
                var lieuSel = document.createElement("select"); lieuSel.className = "anpr-cam-lieu"; lieuSel.dataset.path = cam.camera_path;
                ["", "Ouest", "Panorama", "Houx"].forEach(function (l) { var opt = new Option(l || "---", l); if (cam.lieu === l) opt.selected = true; lieuSel.appendChild(opt); });
                rd.appendChild(lieuSel);
                row.appendChild(rd); body.appendChild(row);
            });
            var sb = mk("button", "btn btn-primary", "Enregistrer"); sb.style.marginTop = "12px";
            sb.addEventListener("click", async function () {
                var rows = qsa(".anpr-cam-config-row");
                for (var i = 0; i < rows.length; i++) { var pa = rows[i].querySelector(".anpr-cam-role").dataset.path; await post(API.camCfg, { camera_path: pa, label: rows[i].querySelector(".anpr-cam-label").value, forward_role: rows[i].querySelector(".anpr-cam-role").value, lieu: rows[i].querySelector(".anpr-cam-lieu").value, enabled: true }); }
                closeModal("anpr-config-modal"); loadKPIs();
            }); body.appendChild(sb);
        } catch (e) { body.textContent = ""; body.appendChild(mk("div", "anpr-history-empty", "Erreur")); }
    }

    /* ---- Vision stats sub-tab ---- */
    var visionEventsPopulated = false;

    async function loadVisionStats(eventFilter) {
        try {
            // Build query string
            var qs_str = "";
            if (eventFilter) {
                var parts = eventFilter.split("|");
                qs_str = "?evenement=" + encodeURIComponent(parts[0]) + "&annee=" + encodeURIComponent(parts[1]);
            }

            var vs = await get(API.visionStats + qs_str);

            // Populate event select (once)
            if (!visionEventsPopulated && vs.events && vs.events.length) {
                visionEventsPopulated = true;
                var sel = qs("#vision-event-filter");
                vs.events.forEach(function (e) {
                    sel.appendChild(new Option(e.evenement + " " + e.annee + " (" + e.count + ")", e.evenement + "|" + e.annee));
                });
            }

            // KPIs
            animVal(qs("#kpi-v-total"), vs.vision_total);
            animVal(qs("#kpi-v-matched"), vs.matched);
            animVal(qs("#kpi-v-unmatched"), vs.unmatched);

            // Repartition par lieu (all) - doughnut
            var lieuxAll = vs.by_lieu_all || {};
            var laLabels = Object.keys(lieuxAll), laValues = laLabels.map(function (k) { return lieuxAll[k]; });
            var laBg = laLabels.map(function (k) { return LIEU_COLORS[k] || "#6b7280"; });
            mkChart("visionLieuxAll", "#chart-vision-lieux-all", "doughnut",
                { labels: laLabels, datasets: [{ data: laValues, backgroundColor: laBg, borderWidth: 1.5, borderColor: "#fff" }] },
                { cutout: "60%", plugins: { legend: { display: true, position: "right", labels: { boxWidth: 10, padding: 6, font: { size: 10 } } } } }
            );

            // Croisement par lieu - bar
            var lieux = vs.by_lieu || {};
            var ll = Object.keys(lieux), lv = ll.map(function (k) { return lieux[k]; });
            var lbg = ll.map(function (k) { return LIEU_COLORS[k] || "#6b7280"; });
            mkChart("visionCross", "#chart-vision-cross", "bar",
                { labels: ll, datasets: [{ label: "Croisees ANPR", data: lv, backgroundColor: lbg, borderRadius: 4, borderSkipped: false }] },
                { scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: "rgba(0,0,0,0.04)" } } } }
            );

            // Vision search table
            var sd = await get(API.visionSearch + qs_str);
            qs("#vision-result-count").textContent = sd.total + " fiche(s) Vision";
            var tb = qs("#vision-table-body"); tb.textContent = "";
            (sd.results || []).forEach(function (r) {
                var tr = document.createElement("tr"); tr.className = "anpr-row";
                var tdp = document.createElement("td"); tdp.appendChild(mk("span", "anpr-plate-badge", r.plaque)); tr.appendChild(tdp);
                var tdl = document.createElement("td"); var pill = mkLieuPill(r.lieu); if (pill) tdl.appendChild(pill); else tdl.textContent = r.lieu || "--"; tr.appendChild(tdl);
                tr.appendChild(mk("td", "", r.billets ? r.billets.length + " billet(s)" : "0"));
                tr.appendChild(mk("td", "", fmtDt(r.date)));
                var tda = document.createElement("td");
                if (r.anpr_detections > 0) {
                    var badge = mk("span", "", r.anpr_detections + " detection(s)");
                    badge.style.cssText = "color:#22c55e;font-weight:600;";
                    tda.appendChild(badge);
                } else { tda.textContent = "Aucune"; tda.style.opacity = "0.4"; }
                tr.appendChild(tda);
                tr.addEventListener("click", function () {
                    qs("#anpr-filter-plate").value = r.plaque;
                    switchTab("search"); page = 1; loadSearch();
                });
                tb.appendChild(tr);
            });
        } catch (e) { console.error("VisionStats", e); }
    }

    /* ---- events ---- */
    function bind() {
        // Tabs
        qsa("[data-anpr-tab]").forEach(function (b) { b.addEventListener("click", function () { switchTab(b.dataset.anprTab); }); });
        // Stats sub-tabs
        qsa("[data-stats-sub]").forEach(function (b) { b.addEventListener("click", function () { switchStatsSub(b.dataset.statsSub); }); });
        // Vision event filter
        qs("#vision-event-filter")?.addEventListener("change", function () {
            visionStatsLoaded = false;
            loadVisionStats(this.value);
        });
        // Default: activate search tab on first click (lazy)
        // Refresh
        qs("#anpr-refresh-btn")?.addEventListener("click", function () { window._anprStats = null; statsLoaded = false; searchLoaded = false; loadKPIs(); loadOnsite(); loadLive(); switchTab(qs("[data-anpr-tab].active")?.dataset.anprTab || "search"); });
        qs("#anpr-config-btn")?.addEventListener("click", openCamCfg);
        // Expand toggle (right panel full width)
        qs("#anpr-expand-toggle")?.addEventListener("click", function () {
            var body = qs(".anpr-body");
            var btn = this;
            body.classList.toggle("anpr-expand-right");
            var expanded = body.classList.contains("anpr-expand-right");
            btn.classList.toggle("active", expanded);
            btn.querySelector(".material-symbols-outlined").textContent = expanded ? "fullscreen_exit" : "fullscreen";
            btn.title = expanded ? "Reduire" : "Plein ecran";
        });
        // On-site reset
        qs("#anpr-reset-onsite")?.addEventListener("click", function () {
            showConfirmToast("Remettre le compteur de vehicules sur site a zero ?", { okLabel: "Reinitialiser", type: "warning" }).then(function (ok) {
                if (!ok) return;
                post(API.onsiteReset, {}).then(function () { loadOnsite(); });
            });
        });
        // Search
        var pt; qs("#anpr-filter-plate")?.addEventListener("input", function () { clearTimeout(pt); pt = setTimeout(function () { page = 1; loadSearch(); }, 350); });
        ["#anpr-filter-type","#anpr-filter-brand","#anpr-filter-camera","#anpr-filter-from","#anpr-filter-to"].forEach(function (s) { qs(s)?.addEventListener("change", function () { page = 1; loadSearch(); }); });
        qs("#anpr-filter-vision")?.addEventListener("click", function () {
            var lbl = qs("#anpr-filter-vision-label");
            if (sourceFilter === "") { sourceFilter = "vision"; this.classList.add("active"); lbl.textContent = "Vision"; }
            else if (sourceFilter === "vision") { sourceFilter = "anpr"; lbl.textContent = "ANPR"; }
            else if (sourceFilter === "anpr") { sourceFilter = "cross"; lbl.textContent = "Croisees"; }
            else { sourceFilter = ""; this.classList.remove("active"); lbl.textContent = "Source"; }
            page = 1; loadSearch();
        });
        qs("#anpr-filter-reset")?.addEventListener("click", function () {
            qs("#anpr-filter-plate").value = ""; qs("#anpr-filter-type").value = ""; qs("#anpr-filter-brand").value = ""; qs("#anpr-filter-camera").value = ""; qs("#anpr-filter-from").value = ""; qs("#anpr-filter-to").value = "";
            sourceFilter = ""; qs("#anpr-filter-vision")?.classList.remove("active"); var vl = qs("#anpr-filter-vision-label"); if (vl) vl.textContent = "Source";
            qsa(".anpr-color-chip.active").forEach(function (b) { b.classList.remove("active"); }); page = 1; loadSearch();
        });
        // Modals
        qs("#anpr-modal-close")?.addEventListener("click", function () { closeModal("anpr-modal"); });
        qs("#anpr-config-modal-close")?.addEventListener("click", function () { closeModal("anpr-config-modal"); });
        qs("#anpr-modal")?.addEventListener("click", function (e) { if (e.target === this) closeModal("anpr-modal"); });
        qs("#anpr-config-modal")?.addEventListener("click", function (e) { if (e.target === this) closeModal("anpr-config-modal"); });
        document.addEventListener("keydown", function (e) { if (e.key === "Escape") { closeModal("anpr-modal"); closeModal("anpr-config-modal"); } });
        // Sidebar (restore + toggle with memory)
        var sidebar = qs("#sidebar");
        if (sidebar) {
            var stored = localStorage.getItem("sidebar-collapsed");
            if (stored === null || stored === "true") sidebar.classList.add("collapsed");
        }
        qs("#sidebarToggle")?.addEventListener("click", function () {
            if (!sidebar) return;
            sidebar.classList.toggle("collapsed");
            localStorage.setItem("sidebar-collapsed", sidebar.classList.contains("collapsed"));
        });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
