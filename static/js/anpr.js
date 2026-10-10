/* =========================================================================
   LAPI -- poste de controle des plaques (page /anpr, API anpr.py)

   - Bandeau des voies : sante de chaque camera (active / calme / muette),
     activite de la periode, vehicules sur site.
   - Flux des passages : direct (nouveaux passages par ordre de reception),
     filtres, recherche tolerante sur tout l'historique, pagination par curseur.
   - Panneau de droite : synthese de la periode, ou fiche de la plaque
     (photo, presence, sejours, lectures proches, Vision, watchlist).
   Les listes affichent des vignettes (~25 Ko) ; la photo d'origine (~800 Ko)
   n'est chargee que dans la fiche.
   ========================================================================= */
(function () {
    "use strict";

    var API = {
        overview: "/api/anpr/overview",
        passages: "/api/anpr/passages",
        plate: "/api/anpr/plate/",
        onsiteReset: "/api/anpr/onsite/reset",
        watchlist: "/api/anpr-watchlist",
    };
    var LIVE_MS = 5000;
    var OVERVIEW_MS = 15000;
    var OVERVIEW_PAST_MS = 60000;
    var PAGE = 60;
    var TZ = "Europe/Paris";
    var PERIOD_LABEL = { "1h": "la derniere heure", "today": "aujourd'hui", "24h": "les dernieres 24 h", "7d": "les 7 derniers jours" };
    // Etats calcules par anpr.py (test reseau des voies silencieuses)
    var LANE_STATE = {
        ok: { label: "Active", icon: "sensors" },
        idle: { label: "Calme", icon: "bedtime" },
        noread: { label: "Sans lecture", icon: "visibility_off" },
        offline: { label: "Hors ligne", icon: "wifi_off" },
    };

    var S = {
        period: "today", date: "", q: "", dir: "", camera: "", watch: false, vision: false, unread: false,
        items: [], ids: {}, next: null, total: null, receivedMax: null,
        loading: false, seq: 0, pending: [],
        overview: null, overviewError: false,
        detail: null, detailItem: null, selectedNorm: null, selectedId: null, sideMode: "summary",
    };

    /* ------------------------------------------------------------------ */
    /* Utilitaires                                                         */
    /* ------------------------------------------------------------------ */
    function $(id) { return document.getElementById(id); }
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined && text !== null) e.textContent = text;
        return e;
    }
    function icon(name, cls) { return el("span", "material-symbols-outlined" + (cls ? " " + cls : ""), name); }
    function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ""; }

    function getJSON(url, signal) {
        return fetch(url, { credentials: "same-origin", signal: signal }).then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
        });
    }
    function send(method, url, body) {
        return fetch(url, {
            method: method, credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
            body: body ? JSON.stringify(body) : undefined,
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (j) {
                if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
                return j;
            });
        });
    }
    function toast(type, msg) { if (typeof showToast === "function") showToast(type, msg); }

    function normPlate(p) { return String(p || "").toUpperCase().replace(/[^A-Z0-9]/g, ""); }
    // Plaque SIV (AB123CD) affichee AB-123-CD ; le reste tel quel
    function fmtPlate(p) {
        var m = /^([A-Z]{2})(\d{3})([A-Z]{2})$/.exec(p || "");
        return m ? m[1] + "-" + m[2] + "-" + m[3] : (p || "");
    }
    function nf(n) { return (n || 0).toLocaleString("fr-FR"); }
    function fmtTime(iso) { return iso ? new Date(iso).toLocaleTimeString("fr-FR", { timeZone: TZ, hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "--"; }
    function fmtHM(iso) { return iso ? new Date(iso).toLocaleTimeString("fr-FR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" }) : "--"; }
    function fmtDay(iso) { return iso ? new Date(iso).toLocaleDateString("fr-FR", { timeZone: TZ, weekday: "short", day: "2-digit", month: "2-digit" }) : ""; }
    function fmtDate(iso) { return iso ? new Date(iso).toLocaleDateString("fr-FR", { timeZone: TZ, day: "2-digit", month: "2-digit", year: "numeric" }) : ""; }
    function fmtDT(iso) { return iso ? fmtDate(iso) + " " + fmtHM(iso) : "--"; }
    function dayKey(iso) { return iso ? new Date(iso).toLocaleDateString("fr-CA", { timeZone: TZ }) : ""; }
    function isToday(iso) { return dayKey(iso) === dayKey(new Date().toISOString()); }
    function ago(iso) {
        if (!iso) return "jamais";
        var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
        if (s < 60) return "il y a " + Math.round(s) + " s";
        if (s < 3600) return "il y a " + Math.round(s / 60) + " min";
        if (s < 86400) return "il y a " + Math.round(s / 3600) + " h";
        var d = Math.round(s / 86400);
        return "il y a " + d + " jour" + (d > 1 ? "s" : "");
    }
    function fmtDur(ms) {
        if (ms == null || ms < 0) return "";
        var m = Math.round(ms / 60000);
        if (m < 60) return m + " min";
        var h = Math.floor(m / 60), r = m % 60;
        if (h < 48) return h + " h " + (r < 10 ? "0" : "") + r;
        return Math.round(h / 24) + " j";
    }
    function plateBadge(p, xl) {
        var unread = !p || p === "UNKNOWN";
        var b = el("span", "lp-plate" + (unread ? " is-unread" : "") + (xl ? " lp-plate-xl" : ""));
        b.appendChild(el("span", "", unread ? "illisible" : fmtPlate(p)));
        return b;
    }
    function badge(cls, ic, text, title) {
        var b = el("span", "lp-badge " + cls);
        if (ic) b.appendChild(icon(ic));
        b.appendChild(document.createTextNode(text));
        if (title) b.title = title;
        return b;
    }
    function dirChip(dir) {
        var d = dir === "entry" ? ["login", "Entree"] : dir === "exit" ? ["logout", "Sortie"] : ["help", "Sens ?"];
        var c = el("span", "lp-dir lp-dir-" + (dir || "unknown"));
        c.appendChild(icon(d[0]));
        c.appendChild(document.createTextNode(d[1]));
        return c;
    }
    function thumb(item, cls) {
        var t = el("div", "lp-thumb" + (cls ? " " + cls : ""));
        if (item && item.thumb) {
            var im = document.createElement("img");
            im.loading = "lazy"; im.decoding = "async"; im.alt = "";
            im.src = item.thumb;
            im.addEventListener("error", function () { t.textContent = ""; t.appendChild(icon("no_photography")); });
            t.appendChild(im);
        } else {
            t.appendChild(icon("no_photography"));
        }
        return t;
    }
    function svgEl(tag, attrs) {
        var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
        Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
        return e;
    }
    function isLivePeriod() { return S.period !== "day"; }

    /* ------------------------------------------------------------------ */
    /* Vue d'ensemble : voies, en direct, synthese                         */
    /* ------------------------------------------------------------------ */
    function periodParams() {
        var p = new URLSearchParams();
        p.set("period", S.period);
        if (S.period === "day") p.set("date", S.date);
        return p;
    }

    function loadOverview() {
        return getJSON(API.overview + "?" + periodParams().toString()).then(function (ov) {
            S.overview = ov; S.overviewError = false;
            renderLanes(ov);
            fillCameraSelect(ov.lanes);
            renderLive();
            if (S.sideMode === "summary") renderSummary(ov);
        }).catch(function (e) {
            console.error("LAPI overview", e);
            S.overviewError = true;
            renderLive();
        });
    }

    function renderLive() {
        var box = $("lp-live"), txt = $("lp-live-text");
        box.className = "lp-live";
        if (S.overviewError) { box.classList.add("is-error"); txt.textContent = "Hors ligne"; return; }
        var ov = S.overview;
        if (!ov) return;
        if (S.period === "day") { box.classList.add("is-past"); txt.textContent = "Journee du " + fmtDate(ov.period.from); return; }
        var newest = null, anyOk = false;
        (ov.lanes || []).forEach(function (l) {
            if (l.status === "ok") anyOk = true;
            if (l.last_dt && (!newest || l.last_dt > newest)) newest = l.last_dt;
        });
        if (anyOk) { box.classList.add("is-ok"); txt.textContent = "En direct - derniere lecture " + ago(newest); }
        else { box.classList.add("is-idle"); txt.textContent = newest ? "Aucune lecture depuis " + ago(newest).replace("il y a ", "") : "Aucune lecture"; }
        box.title = "Lectures recues par le recepteur des cameras (service HikRecepteur, port 8000)";
    }

    function spark(values) {
        var n = values.length || 1, max = Math.max.apply(null, values.concat([1]));
        var svg = svgEl("svg", { "class": "lp-spark", viewBox: "0 0 " + n + " 26", preserveAspectRatio: "none", "aria-hidden": "true" });
        values.forEach(function (v, i) {
            if (!v) return;
            var h = Math.max(1.5, 26 * v / max);
            svg.appendChild(svgEl("rect", { x: i + 0.12, y: 26 - h, width: 0.76, height: h, rx: 0.2 }));
        });
        return svg;
    }

    function renderLanes(ov) {
        var box = $("lp-lanes");
        box.textContent = "";

        // Vehicules sur site
        var site = el("div", "lp-tile lp-tile-site");
        var sh = el("div", "lp-tile-head");
        sh.appendChild(icon("local_parking"));
        sh.appendChild(el("span", "lp-tile-name", "Sur site"));
        var rb = el("button", "lp-tile-reset");
        rb.type = "button"; rb.title = "Remettre le compteur a zero"; rb.setAttribute("aria-label", rb.title);
        rb.appendChild(icon("restart_alt"));
        rb.addEventListener("click", resetOnsite);
        sh.appendChild(rb);
        site.appendChild(sh);
        var sb = el("div", "lp-tile-big");
        sb.appendChild(el("span", "lp-tile-n", nf(ov.onsite.value)));
        sb.appendChild(el("span", "lp-tile-unit", "vehicules"));
        site.appendChild(sb);
        site.appendChild(el("div", "lp-tile-meta", "Entrees - sorties depuis le " + fmtDT(ov.onsite.reset_at)));
        var ses = el("div", "lp-tile-meta");
        ses.appendChild(el("span", "lp-in", nf(ov.onsite.entries) + " entrees"));
        ses.appendChild(document.createTextNode(" / "));
        ses.appendChild(el("span", "lp-out", nf(ov.onsite.exits) + " sorties"));
        site.appendChild(ses);
        box.appendChild(site);

        (ov.lanes || []).forEach(function (l) {
            var st = LANE_STATE[l.status] || LANE_STATE.idle;
            var t = el("div", "lp-tile is-" + l.status);
            t.tabIndex = 0;
            t.setAttribute("role", "button");
            t.title = laneTip(l) + "\nClic : filtrer les passages sur cette voie.";
            if (S.camera === l.path) t.style.boxShadow = "var(--focus-ring)";
            var hd = el("div", "lp-tile-head");
            hd.appendChild(el("span", "lp-tile-name", l.label));
            var stb = el("span", "lp-tile-state");
            stb.appendChild(icon(st.icon));
            stb.lastChild.style.fontSize = "13px";
            stb.appendChild(document.createTextNode(st.label));
            hd.appendChild(stb);
            t.appendChild(hd);
            var big = el("div", "lp-tile-big");
            big.appendChild(el("span", "lp-tile-n", nf(l.count)));
            big.appendChild(el("span", "lp-tile-unit", "passage" + (l.count > 1 ? "s" : "")));
            var es = el("span", "lp-tile-es");
            es.appendChild(el("span", "lp-in", "+" + nf(l.entries)));
            es.appendChild(el("span", "lp-out", "-" + nf(l.exits)));
            es.title = "Entrees / sorties sur la periode";
            big.appendChild(es);
            t.appendChild(big);
            var meta = el("div", "lp-tile-meta");
            meta.appendChild(document.createTextNode((l.last_dt ? "Lu " + ago(l.last_dt) : "Jamais lu") + (l.last_plate ? " - " : "")));
            if (l.last_plate) meta.appendChild(el("b", "", fmtPlate(l.last_plate)));
            t.appendChild(meta);
            if (l.read_warn) {
                var pct = Math.round(100 * l.unread / Math.max(1, l.count));
                t.appendChild(el("div", "lp-tile-warn", "Lecture degradee : " + pct + " % illisibles"));
            }
            t.appendChild(spark(l.spark || []));
            function pick() {
                S.camera = S.camera === l.path ? "" : l.path;
                $("lp-camera").value = S.camera;
                renderLanes(S.overview);
                loadList(true);
            }
            t.addEventListener("click", pick);
            t.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
            box.appendChild(t);
        });
    }

    function laneTip(l) {
        var silence = l.last_dt ? ago(l.last_dt).replace("il y a ", "") : "toujours";
        var test = l.reach ? " (test reseau de " + fmtHM(l.reach.at) + (l.ip ? ", " + l.ip : "") + ")" : "";
        if (l.status === "offline") return "Hors ligne : la camera ne repond pas sur le reseau" + test + ". Verifier l'alimentation, le cable ou le switch.";
        if (l.status === "noread") return "Sans lecture depuis " + silence + " alors que d'autres voies lisent. La camera repond sur le reseau" + test + " : verifier sa configuration (hote d'alarme HTTP vers le recepteur, lecture de plaques).";
        if (l.status === "idle") return "Calme : pas de lecture depuis " + silence + ", comme les autres voies (pas de trafic)" + (l.reach ? ". Camera joignable" + test : "") + ".";
        return "Active : derniere lecture " + ago(l.last_dt) + ".";
    }

    function fillCameraSelect(lanes) {
        var sel = $("lp-camera");
        var keep = S.camera;
        var sig = (lanes || []).map(function (l) { return l.path + "=" + l.label; }).join("|");
        if (sel.dataset.sig === sig) return;
        sel.dataset.sig = sig;
        while (sel.options.length > 1) sel.remove(1);
        (lanes || []).forEach(function (l) { sel.appendChild(new Option(l.label, l.path)); });
        sel.value = keep;
    }

    function resetOnsite() {
        showConfirmToast("Remettre le compteur de vehicules sur site a zero ? Les entrees et sorties seront recomptees a partir de maintenant.", { okLabel: "Remettre a zero", type: "warning" }).then(function (ok) {
            if (!ok) return;
            send("POST", API.onsiteReset).then(function () {
                toast("success", "Compteur sur site remis a zero");
                loadOverview();
            }).catch(function (e) { toast("error", "Remise a zero impossible : " + e.message); });
        });
    }

    /* ---- Synthese (panneau de droite) ---- */
    function section(title, ic, aside) {
        var s = el("section");
        var h = el("h4", "lp-sec-title");
        h.appendChild(icon(ic));
        h.appendChild(document.createTextNode(title));
        if (aside) h.appendChild(el("em", "", aside));
        s.appendChild(h);
        return s;
    }
    function kpi(n, label) {
        var k = el("div", "lp-kpi");
        k.appendChild(el("div", "lp-kpi-n", n));
        k.appendChild(el("div", "lp-kpi-l", label));
        return k;
    }
    function bars(list, colorOf) {
        var wrap = el("div", "lp-bars");
        var max = Math.max.apply(null, list.map(function (x) { return x.n; }).concat([1]));
        list.forEach(function (x) {
            var row = el("div", "lp-bar");
            var l = el("span", "lp-bar-l");
            if (x.hex) { var d = el("span", "lp-dot"); d.style.background = x.hex; l.appendChild(d); }
            l.appendChild(document.createTextNode(x.label));
            row.appendChild(l);
            var tr = el("div", "lp-bar-t"), i = el("i");
            i.style.width = Math.max(2, 100 * x.n / max) + "%";
            if (colorOf) i.style.setProperty("--c", colorOf(x));
            tr.appendChild(i);
            row.appendChild(tr);
            row.appendChild(el("span", "lp-bar-n", nf(x.n)));
            wrap.appendChild(row);
        });
        return wrap;
    }

    function flowChart(ov) {
        var fin = ov.flow.in || [], fout = ov.flow.out || [], n = fin.length || 1;
        var max = Math.max.apply(null, fin.concat(fout).concat([1]));
        var W = 400, H = 120, mid = 52, top = 4, bottom = 100, bw = W / n;
        var svg = svgEl("svg", { "class": "lp-flow", viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none", role: "img", "aria-label": "Entrees et sorties par intervalle" });
        svg.appendChild(svgEl("line", { x1: 0, x2: W, y1: mid, y2: mid }));
        var step = ov.period.step_min * 60000, t0 = new Date(ov.period.from).getTime();
        var labelEvery = Math.max(1, Math.ceil(n / 8));
        for (var i = 0; i < n; i++) {
            var hi = (mid - top) * (fin[i] || 0) / max, ho = (bottom - mid) * (fout[i] || 0) / max;
            var x = i * bw + bw * 0.12, w = bw * 0.76;
            var tip = fmtDay(new Date(t0 + i * step).toISOString()) + " " + fmtHM(new Date(t0 + i * step).toISOString()) + " : " + (fin[i] || 0) + " entrees, " + (fout[i] || 0) + " sorties";
            if (hi) { var r1 = svgEl("rect", { "class": "in", x: x, y: mid - hi, width: w, height: hi, rx: 1 }); r1.appendChild(svgEl("title")).textContent = tip; svg.appendChild(r1); }
            if (ho) { var r2 = svgEl("rect", { "class": "out", x: x, y: mid, width: w, height: ho, rx: 1 }); r2.appendChild(svgEl("title")).textContent = tip; svg.appendChild(r2); }
            if (i % labelEvery === 0) {
                var d = new Date(t0 + i * step).toISOString();
                var lbl = ov.period.step_min >= 360 ? fmtDay(d).split(" ")[0] + " " + fmtHM(d).slice(0, 2) + "h" : fmtHM(d);
                var tx = svgEl("text", { x: i * bw + 1, y: H - 4 });
                tx.textContent = lbl;
                svg.appendChild(tx);
            }
        }
        return svg;
    }

    function renderSummary(ov) {
        if (!ov) return;
        $("lp-side-title").textContent = S.period === "day" ? "Synthese du " + fmtDate(ov.period.from) : "Synthese - " + PERIOD_LABEL[S.period];
        var body = $("lp-side-body");
        var keepScroll = body.scrollTop;
        body.textContent = "";
        var t = ov.totals;

        var s1 = section("Periode", "summarize");
        var k = el("div", "lp-kpis");
        k.appendChild(kpi(nf(t.passages), "passages"));
        k.appendChild(kpi(nf(t.plates), "plaques differentes"));
        var ke = kpi(nf(t.entries), "entrees"); ke.firstChild.classList.add("lp-in"); k.appendChild(ke);
        var ks = kpi(nf(t.exits), "sorties"); ks.firstChild.classList.add("lp-out"); k.appendChild(ks);
        var pct = t.passages ? Math.round(100 * t.unread / t.passages) : 0;
        k.appendChild(kpi(nf(t.unread) + (t.passages ? " (" + pct + " %)" : ""), "plaques illisibles"));
        k.appendChild(kpi(nf(ov.watch.listed), "plaques en surveillance"));
        s1.appendChild(k);
        body.appendChild(s1);

        var s2 = section("Activite", "bar_chart", ov.period.step_min < 60 ? "par " + ov.period.step_min + " min" : "par " + (ov.period.step_min / 60) + " h");
        if (t.passages) {
            s2.appendChild(flowChart(ov));
            var lg = el("div", "lp-legend");
            var a = el("span"); var ia = el("i"); ia.style.background = "#22c55e"; a.appendChild(ia); a.appendChild(document.createTextNode("Entrees")); lg.appendChild(a);
            var b = el("span"); var ib = el("i"); ib.style.background = "#fb923c"; b.appendChild(ib); b.appendChild(document.createTextNode("Sorties")); lg.appendChild(b);
            s2.appendChild(lg);
        } else {
            s2.appendChild(el("p", "lp-note", "Aucun passage sur la periode."));
        }
        body.appendChild(s2);

        var s3 = section("Plaques surveillees vues", "local_police", ov.watch.hits.length ? String(ov.watch.hits.length) : "");
        if (ov.watch.hits.length) {
            var hl = el("div", "lp-hits");
            ov.watch.hits.forEach(function (h) {
                var bt = el("button", "lp-hit");
                bt.type = "button";
                bt.appendChild(plateBadge(h.plate));
                bt.appendChild(el("span", "lp-hit-l", h.label || "Sans libelle"));
                bt.appendChild(el("span", "lp-hit-t", (h.n > 1 ? h.n + " passages, " : "") + (isToday(h.last) ? fmtHM(h.last) : fmtDT(h.last))));
                bt.addEventListener("click", function () { openPlate(h.plate); });
                hl.appendChild(bt);
            });
            s3.appendChild(hl);
        } else {
            s3.appendChild(el("p", "lp-note", ov.watch.listed
                ? "Aucune des " + ov.watch.listed + " plaques surveillees n'est passee sur la periode."
                : "Aucune plaque en surveillance. Ouvrez une plaque et choisissez Surveiller : chaque passage declenche une alerte Cockpit."));
        }
        body.appendChild(s3);

        var v = ov.vision || {};
        var s4 = section(v.event ? "Vision - " + v.event.evenement + " " + v.event.annee : "Vision", "confirmation_number");
        if (v.event) {
            var kv = el("div", "lp-kpis");
            kv.appendChild(kpi(nf(v.fiches), "vehicules enregistres"));
            kv.appendChild(kpi(nf(v.seen_period), "vus par la LAPI sur la periode"));
            s4.appendChild(kv);
            s4.appendChild(el("p", "lp-note", nf(v.seen_all) + " vehicules enregistres dans Vision ont deja ete lus au moins une fois par la LAPI."));
        } else {
            s4.appendChild(el("p", "lp-note", "Aucun evenement Vision actif."));
        }
        body.appendChild(s4);

        var mix = ov.mix || {};
        if ((mix.types || []).length) {
            var s5 = section("Vehicules", "directions_car");
            s5.appendChild(bars(mix.types.slice(0, 6)));
            if ((mix.colors || []).length) { var sp = el("div"); sp.style.height = "12px"; s5.appendChild(sp); s5.appendChild(bars(mix.colors.slice(0, 6), function (x) { return x.hex; })); }
            if ((mix.brands || []).length) { var sp2 = el("div"); sp2.style.height = "12px"; s5.appendChild(sp2); s5.appendChild(bars(mix.brands, function () { return "#64748b"; })); }
            body.appendChild(s5);
        }
        body.scrollTop = keepScroll;
    }

    /* ------------------------------------------------------------------ */
    /* Flux des passages                                                   */
    /* ------------------------------------------------------------------ */
    function listParams() {
        var p = S.q ? new URLSearchParams() : periodParams();
        if (S.q) p.set("q", S.q);
        if (S.dir) p.set("dir", S.dir);
        if (S.camera) p.set("camera", S.camera);
        if (S.watch) p.set("watch", "1");
        if (S.vision) p.set("vision", "1");
        if (S.unread && !S.q) p.set("unread", "1");
        return p;
    }

    var listAbort = null;
    function loadList(reset) {
        var list = $("lp-list");
        if (reset) {
            S.seq++;
            if (listAbort) listAbort.abort();
            S.items = []; S.ids = {}; S.next = null; S.total = null; S.pending = [];
            $("lp-new-btn").classList.remove("show");
            list.textContent = "";
            for (var i = 0; i < 6; i++) list.appendChild(el("div", "lp-skel"));
            list.scrollTop = 0;
        } else if (!S.next || S.loading) {
            return;
        }
        var seq = S.seq;
        var p = listParams();
        p.set("limit", PAGE);
        if (!reset) p.set("before", S.next);
        S.loading = true;
        listAbort = new AbortController();
        getJSON(API.passages + "?" + p.toString(), listAbort.signal).then(function (d) {
            if (seq !== S.seq) return;
            if (reset) {
                list.textContent = "";
                S.total = d.total;
                S.receivedMax = d.received_max;
            }
            S.next = d.next;
            appendItems(d.items);
            renderCount();
            renderMore();
        }).catch(function (e) {
            if (e.name === "AbortError" || seq !== S.seq) return;
            console.error("LAPI passages", e);
            list.textContent = "";
            var em = el("div", "lp-empty");
            em.appendChild(icon("cloud_off"));
            em.appendChild(el("div", "", "Chargement impossible. Nouvel essai a la prochaine actualisation."));
            list.appendChild(em);
        }).then(function () { if (seq === S.seq) S.loading = false; });
    }

    function appendItems(items) {
        var list = $("lp-list");
        var sentinel = $("lp-more");
        var frag = document.createDocumentFragment();
        items.forEach(function (it) {
            if (S.ids[it.id]) return;
            S.ids[it.id] = it;
            S.items.push(it);
            frag.appendChild(row(it));
        });
        if (sentinel) list.insertBefore(frag, sentinel); else list.appendChild(frag);
        if (!S.items.length) {
            var em = el("div", "lp-empty");
            em.appendChild(icon(S.q ? "search_off" : "no_crash"));
            em.appendChild(el("div", "", S.q ? "Aucune plaque ne correspond a " + fmtPlate(S.q) + "." : "Aucun passage pour ces criteres."));
            if (!S.q && isLivePeriod()) em.appendChild(el("div", "lp-note", "Les nouveaux passages apparaitront ici automatiquement."));
            list.appendChild(em);
        }
    }

    function prependItems(items) {
        var list = $("lp-list");
        var empty = list.querySelector(".lp-empty");
        if (empty) empty.remove();
        var frag = document.createDocumentFragment();
        var fresh = [];
        items.forEach(function (it) {
            if (S.ids[it.id]) return;
            S.ids[it.id] = it;
            fresh.push(it);
            var r = row(it);
            r.classList.add("is-new");
            frag.appendChild(r);
        });
        S.items = fresh.concat(S.items);
        list.insertBefore(frag, list.firstChild);
        if (S.total != null) S.total += fresh.length;
        renderCount();
    }

    function row(it) {
        var r = el("button", "lp-row" + (it.watch && it.watch.enabled ? " is-watch" : "") + (it.id === S.selectedId ? " is-open" : ""));
        r.type = "button";
        r.dataset.id = it.id;
        r.appendChild(thumb(it));

        var who = el("div", "lp-who");
        var top = el("div", "lp-who-top");
        top.appendChild(plateBadge(it.plate));
        if (it.watch && it.watch.enabled) top.appendChild(badge("lp-badge-watch", "local_police", "Surveillee", it.watch.label || ""));
        if (it.vision) top.appendChild(badge("lp-badge-vision", "confirmation_number", it.vision.lieu || "Vision", "Enregistre dans Vision" + (it.vision.billets ? " - " + it.vision.billets + " billet(s)" : "")));
        if (it.allow) top.appendChild(badge("lp-badge-allow", "verified", "Autorisee", "Liste autorisee de la camera"));
        who.appendChild(top);
        var veh = el("div", "lp-veh");
        if (it.color_hex) { var dot = el("span", "lp-dot"); dot.style.background = it.color_hex; veh.appendChild(dot); }
        veh.appendChild(document.createTextNode([it.color_label, it.brand, it.type].filter(Boolean).join(" - ") || "Vehicule"));
        who.appendChild(veh);
        r.appendChild(who);

        var where = el("div", "lp-where");
        where.appendChild(el("span", "lp-lane", it.cam_label));
        where.appendChild(dirChip(it.dir));
        r.appendChild(where);

        var when = el("div", "lp-when");
        // Sens en icone sur telephone (colonne voie masquee)
        var wd = el("div", "lp-when-dir " + (it.dir === "entry" ? "lp-in" : it.dir === "exit" ? "lp-out" : ""));
        wd.appendChild(icon(it.dir === "entry" ? "login" : it.dir === "exit" ? "logout" : "help"));
        wd.title = it.cam_label;
        when.appendChild(wd);
        when.appendChild(el("div", "lp-when-t", fmtTime(it.dt)));
        when.appendChild(el("div", "lp-when-d", isToday(it.dt) ? ago(it.dt) : fmtDay(it.dt)));
        r.appendChild(when);

        var conf = el("div", "lp-conf");
        if (!it.unread) {
            conf.appendChild(document.createTextNode(it.conf + " %"));
            var m = el("div", "lp-meter"), mi = el("i");
            mi.style.width = Math.max(4, Math.min(100, it.conf)) + "%";
            mi.style.setProperty("--m", it.conf >= 90 ? "#16a34a" : it.conf >= 70 ? "#f59e0b" : "#dc2626");
            m.appendChild(mi);
            conf.appendChild(m);
            conf.title = "Confiance de lecture";
        }
        r.appendChild(conf);

        r.addEventListener("click", function () {
            if (it.unread) openPassage(it); else openPlate(it.plate, it);
        });
        return r;
    }

    function renderCount() {
        var c = $("lp-count");
        c.textContent = "";
        var n = S.total != null ? S.total : S.items.length;
        c.appendChild(el("b", "", nf(n)));
        var txt = " passage" + (n > 1 ? "s" : "");
        if (S.q) txt += " pour " + fmtPlate(S.q) + " (tout l'historique, lecture tolerante)";
        else if (S.period === "day") txt += " le " + S.date.split("-").reverse().join("/");
        else txt += " " + PERIOD_LABEL[S.period];
        if (S.unread && !S.q) txt += " - plaques illisibles";
        c.appendChild(document.createTextNode(txt));
    }

    function renderMore() {
        var list = $("lp-list");
        var m = $("lp-more");
        if (!S.next) { if (m) m.remove(); return; }
        if (!m) {
            m = el("div", "lp-more");
            m.id = "lp-more";
            m.appendChild(el("div", "lp-spinner"));
            list.appendChild(m);
            moreObserver.observe(m);
        } else {
            list.appendChild(m);
        }
    }
    var moreObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) loadList(false); });
    }, { root: document.getElementById("lp-list"), rootMargin: "400px" });

    /* Direct : passages recus depuis le dernier vu */
    function pollLive() {
        if (document.hidden || !isLivePeriod() || S.q || !S.receivedMax || S.loading) return;
        var seq = S.seq;
        var p = listParams();
        p.set("after", S.receivedMax);
        p.set("limit", 100);
        getJSON(API.passages + "?" + p.toString()).then(function (d) {
            if (seq !== S.seq) return;
            if (d.received_max && d.received_max > S.receivedMax) S.receivedMax = d.received_max;
            var items = (d.items || []).filter(function (it) { return !S.ids[it.id]; });
            if (!items.length) return;
            var list = $("lp-list");
            if (list.scrollTop < 60 && !S.pending.length) {
                prependItems(items);
            } else {
                S.pending = items.concat(S.pending);
                var b = $("lp-new-btn");
                b.textContent = S.pending.length + " nouveau" + (S.pending.length > 1 ? "x" : "") + " passage" + (S.pending.length > 1 ? "s" : "");
                b.classList.add("show");
            }
        }).catch(function () { /* l'indicateur d'etat passe par la vue d'ensemble */ });
    }
    function flushPending() {
        var items = S.pending; S.pending = [];
        $("lp-new-btn").classList.remove("show");
        $("lp-list").scrollTop = 0;
        if (items.length) prependItems(items);
    }

    /* ------------------------------------------------------------------ */
    /* Fiche plaque                                                        */
    /* ------------------------------------------------------------------ */
    function markOpen() {
        Array.prototype.forEach.call(document.querySelectorAll(".lp-row.is-open"), function (r) { r.classList.remove("is-open"); });
        if (!S.selectedId) return;
        var r = document.querySelector('.lp-row[data-id="' + S.selectedId + '"]');
        if (r) r.classList.add("is-open");
    }

    function openSide(mode) {
        S.sideMode = mode;
        $("lp-side-back").hidden = mode === "summary";
        $("lp-side").classList.add("is-open");
    }

    function showSummary() {
        S.sideMode = "summary"; S.detail = null; S.selectedNorm = null; S.selectedId = null;
        $("lp-side-back").hidden = true;
        markOpen();
        if (S.overview) renderSummary(S.overview);
        var u = new URL(location.href);
        if (u.searchParams.has("plate")) { u.searchParams.delete("plate"); history.replaceState(null, "", u.toString()); }
    }

    function closeSide() {
        $("lp-side").classList.remove("is-open");
        if (S.sideMode !== "summary") showSummary();
    }

    var plateAbort = null;
    function openPlate(plate, item) {
        var norm = normPlate(plate);
        if (!norm) return;
        S.selectedNorm = norm;
        S.selectedId = item ? item.id : null;
        S.detailItem = item || null;
        markOpen();
        openSide("plate");
        $("lp-side-title").textContent = "";
        $("lp-side-title").appendChild(plateBadge(norm));
        var body = $("lp-side-body");
        body.textContent = "";
        body.scrollTop = 0;
        body.appendChild(el("div", "lp-skel"));
        body.appendChild(el("div", "lp-skel"));
        var u = new URL(location.href);
        u.searchParams.set("plate", norm);
        history.replaceState(null, "", u.toString());
        if (plateAbort) plateAbort.abort();
        plateAbort = new AbortController();
        getJSON(API.plate + encodeURIComponent(norm), plateAbort.signal).then(function (d) {
            if (S.selectedNorm !== norm) return;
            S.detail = d;
            renderPlate(d);
        }).catch(function (e) {
            if (e.name === "AbortError") return;
            body.textContent = "";
            body.appendChild(el("p", "lp-note", "Fiche indisponible (" + e.message + ")."));
        });
    }

    function photoBlock(it) {
        var ph = el("div", "lp-d-photo");
        if (it && it.img) {
            var im = document.createElement("img");
            im.alt = "Photo du passage";
            im.src = it.thumb || it.img;
            // Vignette tout de suite, original des qu'il est charge
            if (it.thumb && it.img !== it.thumb) {
                var full = new Image();
                full.onload = function () { im.src = it.img; };
                full.src = it.img;
            }
            ph.appendChild(im);
            ph.addEventListener("click", function () { openLightbox(it.img); });
            ph.title = "Agrandir";
        } else {
            ph.appendChild(icon("no_photography"));
        }
        if (it) {
            ph.appendChild(el("div", "lp-d-photo-cap", it.cam_label + " - " + (it.dir === "entry" ? "Entree" : it.dir === "exit" ? "Sortie" : "Sens inconnu") + " - " + fmtTime(it.dt) + " " + fmtDate(it.dt)));
        }
        return ph;
    }

    function renderPlate(d) {
        var body = $("lp-side-body");
        body.textContent = "";
        var current = S.detailItem && S.detailItem.plate && normPlate(S.detailItem.plate) === d.norm ? S.detailItem : (d.items[0] || null);

        var top = el("div", "lp-d-top");
        var pr = el("div", "lp-d-plate-row");
        pr.appendChild(plateBadge(d.plate, true));
        if (d.watch && d.watch.enabled) pr.appendChild(badge("lp-badge-watch", "local_police", "Surveillee", d.watch.label || ""));
        if (current && current.allow) pr.appendChild(badge("lp-badge-allow", "verified", "Autorisee"));
        top.appendChild(pr);
        var photoWrap = el("div");
        photoWrap.appendChild(photoBlock(current));
        top.appendChild(photoWrap);
        if (current && current.plate_img) {
            var pi = document.createElement("img");
            pi.className = "lp-d-plateimg"; pi.src = current.plate_img; pi.alt = "Plaque lue";
            top.appendChild(pi);
        }
        if (current && !current.unread) {
            var vehTxt = [current.color_label, current.brand, current.type].filter(Boolean).join(" - ");
            if (vehTxt) {
                var vl = el("div", "lp-veh");
                if (current.color_hex) { var dt = el("span", "lp-dot"); dt.style.background = current.color_hex; vl.appendChild(dt); }
                vl.appendChild(document.createTextNode(vehTxt + " (lecture camera, confiance " + current.conf + " %)"));
                top.appendChild(vl);
            }
        }

        var acts = el("div", "lp-d-actions");
        var wb = el("button", "lp-btn lp-btn-watch" + (d.watch ? " is-on" : ""));
        wb.type = "button";
        wb.appendChild(icon(d.watch ? "notifications_off" : "local_police"));
        wb.appendChild(document.createTextNode(d.watch ? "Ne plus surveiller" : "Surveiller"));
        wb.title = d.watch ? "Retirer de la liste de surveillance" : "Chaque passage de cette plaque declenchera une alerte Cockpit";
        wb.addEventListener("click", function () { toggleWatch(d); });
        acts.appendChild(wb);
        var cb = el("button", "lp-btn");
        cb.type = "button";
        cb.appendChild(icon("content_copy"));
        cb.appendChild(document.createTextNode("Copier"));
        cb.addEventListener("click", function () {
            if (navigator.clipboard) navigator.clipboard.writeText(d.plate).then(function () { toast("success", "Plaque copiee"); });
        });
        acts.appendChild(cb);
        var fb = el("button", "lp-btn");
        fb.type = "button";
        fb.appendChild(icon("filter_alt"));
        fb.appendChild(document.createTextNode("Tous ses passages"));
        fb.title = "Afficher les passages de cette plaque dans la liste";
        fb.addEventListener("click", function () { setQuery(d.norm); });
        acts.appendChild(fb);
        top.appendChild(acts);
        body.appendChild(top);

        if (d.blacklist) {
            var al = el("div", "lp-alert");
            al.appendChild(icon("gpp_bad"));
            al.appendChild(document.createTextNode("Liste noire Vision" + (d.blacklist.raison ? " : " + d.blacklist.raison : "")));
            body.appendChild(al);
        }

        if (d.items.length) {
            var pres = el("div", "lp-presence " + (d.on_site ? "is-in" : "is-out"));
            pres.appendChild(icon(d.on_site ? "where_to_vote" : "history"));
            var last = d.items[0];
            pres.appendChild(document.createTextNode(d.on_site
                ? "Probablement sur site : entree " + (isToday(last.dt) ? "a " + fmtHM(last.dt) : "le " + fmtDT(last.dt)) + " (" + last.cam_label + "), " + ago(last.dt)
                : (last.dir === "exit" ? "Sortie " : "Dernier passage ") + (isToday(last.dt) ? "a " + fmtHM(last.dt) : "le " + fmtDT(last.dt)) + " (" + last.cam_label + ")"));
            body.appendChild(pres);

            var f = el("div", "lp-facts");
            f.appendChild(kpi(nf(d.count), "passage" + (d.count > 1 ? "s" : "")));
            f.appendChild(kpi(fmtDate(d.first), "premier passage"));
            f.appendChild(kpi(d.stays.length ? String(d.stays.length) : "0", "sejour" + (d.stays.length > 1 ? "s" : "")));
            body.appendChild(f);
        } else {
            body.appendChild(el("p", "lp-note", "Aucun passage LAPI pour cette plaque."));
        }

        if (d.similar.length) {
            var ss = section("Lectures proches", "spellcheck", "erreurs de lecture possibles");
            var sl = el("div", "lp-similar");
            d.similar.forEach(function (s) {
                var b = el("button");
                b.type = "button";
                b.title = s.n + " passage(s), dernier le " + fmtDT(s.last);
                b.appendChild(plateBadge(s.plate));
                b.appendChild(document.createTextNode("x" + s.n));
                b.addEventListener("click", function () { openPlate(s.plate); });
                sl.appendChild(b);
            });
            ss.appendChild(sl);
            body.appendChild(ss);
        }

        if (d.vision.length) {
            var sv = section("Vision", "confirmation_number");
            d.vision.forEach(function (v) {
                var card = el("div", "lp-vision");
                if (v.photo) {
                    var vi = document.createElement("img");
                    vi.loading = "lazy"; vi.alt = "Photo Vision"; vi.src = v.photo;
                    vi.addEventListener("click", function () { openLightbox(v.photo); });
                    card.appendChild(vi);
                }
                var vb = el("div", "lp-vision-b");
                vb.appendChild(el("b", "", v.evenement + " " + (v.annee || "") + (v.lieu ? " - parking " + v.lieu : "")));
                var vveh = [v.couleur, v.marque, v.modele].filter(Boolean).join(" ");
                if (vveh) vb.appendChild(el("span", "", vveh));
                vb.appendChild(el("span", "", (v.billets.length ? v.billets.length + " billet(s) : " + v.billets.join(", ") : "Aucun billet")));
                vb.appendChild(el("span", "lp-note", "Enregistre le " + fmtDT(v.date)));
                if (v.commentaire) vb.appendChild(el("span", "", v.commentaire));
                card.appendChild(vb);
                sv.appendChild(card);
            });
            body.appendChild(sv);
        }

        if (d.stays.length) {
            var st = section("Sejours", "timelapse", "entree puis sortie");
            var sl2 = el("div", "lp-stays");
            d.stays.slice(0, 12).forEach(function (s) {
                var r = el("div", "lp-stay" + (s.open ? " is-open" : ""));
                r.appendChild(el("span", "", s.in ? fmtDay(s.in) + " " + fmtHM(s.in) : "entree non vue"));
                r.appendChild(icon("arrow_forward"));
                r.appendChild(el("span", "", s.out ? fmtDay(s.out) + " " + fmtHM(s.out) : (s.open ? "en cours" : "sortie non vue")));
                var dur = s.in && s.out ? fmtDur(new Date(s.out) - new Date(s.in)) : (s.open && s.in ? fmtDur(Date.now() - new Date(s.in)) : "");
                r.appendChild(el("span", "lp-stay-d", dur));
                sl2.appendChild(r);
            });
            st.appendChild(sl2);
            body.appendChild(st);
        }

        if (d.items.length) {
            var tl = section("Passages", "history", d.count > d.items.length ? d.items.length + " derniers sur " + nf(d.count) : "");
            var list = el("div", "lp-timeline");
            d.items.forEach(function (it) {
                var b = el("button", "lp-tl" + (current && it.id === current.id ? " is-current" : ""));
                b.type = "button";
                b.appendChild(thumb(it));
                var m = el("div", "lp-tl-m");
                m.appendChild(el("span", "lp-lane", it.cam_label));
                m.appendChild(dirChip(it.dir));
                b.appendChild(m);
                var t = el("div", "lp-tl-t");
                t.appendChild(el("div", "", fmtTime(it.dt)));
                t.appendChild(el("div", "lp-when-d", fmtDay(it.dt) + " " + (it.dt ? new Date(it.dt).toLocaleDateString("fr-FR", { timeZone: TZ, year: "2-digit" }) : "")));
                b.appendChild(t);
                b.addEventListener("click", function () {
                    S.detailItem = it;
                    photoWrap.textContent = "";
                    photoWrap.appendChild(photoBlock(it));
                    Array.prototype.forEach.call(list.querySelectorAll(".lp-tl.is-current"), function (x) { x.classList.remove("is-current"); });
                    b.classList.add("is-current");
                    body.scrollTo({ top: 0, behavior: "smooth" });
                });
                list.appendChild(b);
            });
            tl.appendChild(list);
            body.appendChild(tl);
        }
    }

    function openPassage(it) {
        S.selectedNorm = null; S.selectedId = it.id; S.detail = null;
        markOpen();
        openSide("passage");
        $("lp-side-title").textContent = "Plaque illisible";
        var body = $("lp-side-body");
        body.textContent = "";
        body.appendChild(photoBlock(it));
        var vehTxt = [it.color_label, it.brand, it.type].filter(Boolean).join(" - ");
        if (vehTxt) body.appendChild(el("div", "lp-veh", vehTxt));
        body.appendChild(el("p", "lp-note", "La camera a detecte un vehicule sans parvenir a lire la plaque. Agrandissez la photo pour la lire, puis tapez-la dans la recherche."));
    }

    function toggleWatch(d) {
        if (d.watch) {
            showConfirmToast("Ne plus surveiller " + fmtPlate(d.plate) + " ?", { okLabel: "Retirer", type: "warning" }).then(function (ok) {
                if (!ok) return;
                send("DELETE", API.watchlist + "/" + encodeURIComponent(d.watch.id)).then(function () {
                    toast("success", fmtPlate(d.plate) + " n'est plus surveillee");
                    refreshAfterWatch(d);
                }).catch(function (e) { toast("error", e.message); });
            });
            return;
        }
        showPromptToast("Surveiller " + fmtPlate(d.plate) + " : chaque passage declenchera une alerte Cockpit. Motif (facultatif) :", { okLabel: "Surveiller", placeholder: "Ex. vehicule signale" }).then(function (label) {
            if (label === null) return;
            send("POST", API.watchlist, { plate: d.norm, label: (label || "").trim() }).then(function () {
                toast("success", fmtPlate(d.plate) + " est sous surveillance");
                refreshAfterWatch(d);
            }).catch(function (e) { toast("error", e.message); });
        });
    }
    function refreshAfterWatch(d) {
        openPlate(d.norm, S.detailItem);
        loadOverview();
        loadList(true);
    }

    /* ------------------------------------------------------------------ */
    /* Visionneuse                                                         */
    /* ------------------------------------------------------------------ */
    function openLightbox(src) {
        $("lp-lightbox-img").src = src;
        $("lp-lightbox").hidden = false;
    }
    function closeLightbox() {
        $("lp-lightbox").hidden = true;
        $("lp-lightbox-img").removeAttribute("src");
    }

    /* ------------------------------------------------------------------ */
    /* Filtres, recherche, periode                                         */
    /* ------------------------------------------------------------------ */
    function setQuery(q) {
        var input = $("lp-q");
        input.value = q;
        onQueryInput(true);
    }
    var qTimer = null;
    function onQueryInput(now) {
        var input = $("lp-q");
        var norm = normPlate(input.value);
        $("lp-search").classList.toggle("has-value", !!input.value);
        clearTimeout(qTimer);
        var run = function () {
            var q = norm.length >= 2 ? norm : "";
            if (q === S.q) return;
            S.q = q;
            document.body.classList.toggle("lp-searching", !!q);
            loadList(true);
        };
        if (now === true) run(); else qTimer = setTimeout(run, 250);
    }

    function setPeriod(code, date) {
        S.period = code;
        S.date = date || "";
        Array.prototype.forEach.call(document.querySelectorAll("#lp-period button"), function (b) {
            b.classList.toggle("active", b.dataset.period === code);
        });
        $("lp-date").classList.toggle("active", code === "day");
        if (code !== "day") $("lp-date").value = "";
        loadOverview();
        if (!S.q) loadList(true);
        restartTimers();
    }

    function toggleChip(id, key) {
        S[key] = !S[key];
        $(id).classList.toggle("active", S[key]);
        $(id).setAttribute("aria-pressed", S[key] ? "true" : "false");
        loadList(true);
    }

    function bind() {
        Array.prototype.forEach.call(document.querySelectorAll("#lp-period button"), function (b) {
            b.addEventListener("click", function () { setPeriod(b.dataset.period); });
        });
        var di = $("lp-date");
        di.max = new Date().toLocaleDateString("fr-CA", { timeZone: TZ });
        di.addEventListener("change", function () {
            if (di.value) setPeriod("day", di.value); else setPeriod("today");
        });
        Array.prototype.forEach.call(document.querySelectorAll("#lp-dir button"), function (b) {
            b.addEventListener("click", function () {
                S.dir = b.dataset.dir;
                Array.prototype.forEach.call(document.querySelectorAll("#lp-dir button"), function (x) { x.classList.toggle("active", x === b); });
                loadList(true);
            });
        });
        $("lp-camera").addEventListener("change", function () {
            S.camera = this.value;
            if (S.overview) renderLanes(S.overview);
            loadList(true);
        });
        $("lp-f-watch").addEventListener("click", function () { toggleChip("lp-f-watch", "watch"); });
        $("lp-f-vision").addEventListener("click", function () { toggleChip("lp-f-vision", "vision"); });
        $("lp-f-unread").addEventListener("click", function () { toggleChip("lp-f-unread", "unread"); });

        var q = $("lp-q");
        q.addEventListener("input", function () { onQueryInput(false); });
        q.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                e.preventDefault();
                onQueryInput(true);
                var norm = normPlate(q.value);
                if (norm.length >= 4) openPlate(norm);
            } else if (e.key === "Escape") {
                q.value = ""; onQueryInput(true); q.blur();
            }
        });
        $("lp-q-clear").addEventListener("click", function (e) { e.preventDefault(); q.value = ""; onQueryInput(true); q.focus(); });

        $("lp-new-btn").addEventListener("click", flushPending);
        $("lp-list").addEventListener("scroll", function () {
            if (this.scrollTop < 20 && S.pending.length) flushPending();
        }, { passive: true });

        $("lp-side-back").addEventListener("click", showSummary);
        $("lp-side-close").addEventListener("click", closeSide);
        $("lp-summary-btn").addEventListener("click", function () { showSummary(); openSide("summary"); });


        $("lp-lightbox").addEventListener("click", closeLightbox);

        document.addEventListener("keydown", function (e) {
            var tag = (e.target && e.target.tagName) || "";
            var typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (e.target && e.target.isContentEditable);
            if (e.key === "/" && !typing) { e.preventDefault(); q.focus(); q.select(); }
            if (e.key === "Escape" && !typing) {
                if (!$("lp-lightbox").hidden) closeLightbox();
                else if (S.sideMode !== "summary" || $("lp-side").classList.contains("is-open")) closeSide();
            }
        });
        document.addEventListener("visibilitychange", function () {
            if (!document.hidden) { loadOverview(); pollLive(); }
        });
    }

    /* ------------------------------------------------------------------ */
    /* Minuteries                                                          */
    /* ------------------------------------------------------------------ */
    var timers = [];
    function restartTimers() {
        timers.forEach(clearInterval);
        timers = [
            setInterval(function () { if (!document.hidden) loadOverview(); }, isLivePeriod() ? OVERVIEW_MS : OVERVIEW_PAST_MS),
            setInterval(pollLive, LIVE_MS),
            setInterval(renderLive, 5000),
        ];
    }

    function init() {
        bind();
        var plate = new URLSearchParams(location.search).get("plate");
        loadOverview();
        if (plate) {
            $("lp-q").value = normPlate(plate);
            $("lp-search").classList.add("has-value");
            S.q = normPlate(plate).length >= 2 ? normPlate(plate) : "";
            openPlate(plate);
        }
        loadList(true);
        restartTimers();
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
