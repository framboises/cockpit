/* =========================================================================
   Detection -- poste de controle des intrusions (page /detection, detection.py)

   - Carte : canaux "detection d'intrusion" places, zones dessinees ; une
     camera ou une zone en cours d'intrusion clignote en rouge.
   - Flux : intrusions (episodes du recepteur) en direct, filtres cible /
     zone / camera / statut, regroupement par zone, camera ou type.
   - Fiche : photos avec la cible encadree (cadre fourni par la camera),
     chronologie, traitement (vue, cloturee, note).
   - Configurer : placer les canaux sur la carte, dessiner les zones et les
     lier aux canaux (et eventuellement aux zones internes de la camera).
   ========================================================================= */
(function () {
    "use strict";

    var API = {
        config: "/api/detection/config",
        overview: "/api/detection/overview",
        episodes: "/api/detection/episodes",
        status: "/api/detection/episodes/status",
        zones: "/api/detection/zones",
        groups: "/api/detection/groups",
        channels: "/api/detection/channels/",
        cameras: "/api/detection/cameras/",
    };
    var DAYS = [[1, "L"], [2, "M"], [3, "M"], [4, "J"], [5, "V"], [6, "S"], [7, "D"]];
    var POLL_MS = 10000;
    var TZ = "Europe/Paris";
    var CENTER = [47.938561, 0.224318];
    var TYPE = {
        human: { label: "Pieton", icon: "directions_walk", color: "#f97316", cls: "dt-b-human" },
        vehicle: { label: "Vehicule", icon: "directions_car", color: "#3b82f6", cls: "dt-b-vehicle" },
        other: { label: "Autre", icon: "help", color: "#94a3b8", cls: "dt-b-other" },
    };
    var STATUS = { "new": "Nouvelle", seen: "Vue", closed: "Cloturee" };
    // Etiquette du cadre de la cible sur la photo
    var BOX_LABEL = { human: "personne", vehicle: "vehicule" };
    var PERIOD_LABEL = { "1h": "la derniere heure", today: "aujourd'hui", "24h": "les dernieres 24 h", "7d": "les 7 derniers jours" };

    var S = {
        period: "today", date: "", type: "", zone: "", camera: "", status: "open", ai: "", planning: "", groupBy: "", grp: "",
        config: { channels: [], zones: [], groups: [] }, overview: null, items: [], next: null, seq: 0,
        openId: null, edit: false, placing: null, drawing: null, editZone: null, editGroup: null, pollN: 0,
    };
    var map, layers = { cams: null, zones: null, draw: null }, camMarkers = {}, zonePolys = {};

    /* ---------------- utilitaires ---------------- */
    function $(id) { return document.getElementById(id); }
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined && text !== null) e.textContent = text;
        return e;
    }
    function icon(n) { return el("span", "material-symbols-outlined", n); }
    function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ""; }
    function getJSON(url) {
        return fetch(url, { credentials: "same-origin" }).then(function (r) {
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
    function toast(t, m) { if (typeof showToast === "function") showToast(t, m); }
    function fmtTime(iso) { return iso ? new Date(iso).toLocaleTimeString("fr-FR", { timeZone: TZ, hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "--"; }
    function fmtHM(iso) { return iso ? new Date(iso).toLocaleTimeString("fr-FR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" }) : "--"; }
    function fmtDay(iso) { return iso ? new Date(iso).toLocaleDateString("fr-FR", { timeZone: TZ, weekday: "short", day: "2-digit", month: "2-digit" }) : ""; }
    function dayKey(iso) { return iso ? new Date(iso).toLocaleDateString("fr-CA", { timeZone: TZ }) : ""; }
    function isToday(iso) { return dayKey(iso) === dayKey(new Date().toISOString()); }
    function fmtDur(s) {
        if (s < 60) return s + " s";
        if (s < 3600) return Math.floor(s / 60) + " min " + (s % 60 < 10 ? "0" : "") + (s % 60);
        return Math.floor(s / 3600) + " h " + Math.round((s % 3600) / 60) + " min";
    }
    function ago(iso) {
        var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
        if (s < 60) return "il y a " + Math.round(s) + " s";
        if (s < 3600) return "il y a " + Math.round(s / 60) + " min";
        if (s < 86400) return "il y a " + Math.round(s / 3600) + " h";
        return "le " + fmtDay(iso);
    }
    function kind(types) { return types.indexOf("human") >= 0 ? "human" : types.indexOf("vehicle") >= 0 ? "vehicle" : "other"; }
    function badge(cls, ic, text) {
        var b = el("span", "dt-badge " + cls);
        if (ic) b.appendChild(icon(ic));
        b.appendChild(document.createTextNode(text));
        return b;
    }
    function chanKey(ep) { return ep.camera_id + ":" + ep.channel; }
    function chanByKey(k) { return S.config.channels.filter(function (c) { return c.key === k; })[0]; }
    // Une IP = un lieu : les canaux d'un meme appareil (multi-capteurs)
    // partagent la position et un seul pin sur la carte.
    function devices() {
        var out = [], idx = {};
        S.config.channels.forEach(function (ch) {
            var d = idx[ch.camera_id];
            if (!d) {
                d = idx[ch.camera_id] = { id: ch.camera_id, name: ch.camera_name, path: ch.camera_path, channels: [],
                    map: ch.map, map_source: ch.map_source, geo_name: ch.geo_name };
                out.push(d);
            }
            d.channels.push(ch);
        });
        out.forEach(function (d) { d.key = d.channels.length > 1 ? d.id + ":*" : d.channels[0].key; });
        return out;
    }
    function devById(id) { return devices().filter(function (d) { return d.id === id; })[0]; }
    function zonesOfDevice(dv) {
        return (S.config.zones || []).filter(function (z) {
            return (z.links || []).some(function (l) { return l.camera_id === dv.id; });
        });
    }
    function svgEl(tag, attrs) {
        var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
        Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
        return e;
    }

    /* ---------------- carte ---------------- */
    function initMap() {
        // Zoom en bas a droite : le volet de configuration (a gauche) et la
        // fiche (a droite, en haut) ne le recouvrent jamais
        map = L.map("dt-map", { center: CENTER, zoom: 15, maxZoom: 22, zoomControl: false });
        L.control.zoom({ position: "bottomright" }).addTo(map);
        // La carte est creee avant la mise en page definitive (bandeau des
        // groupes, compteurs) : sans re-mesure, une bande grise restait a
        // droite jusqu'au premier deplacement. On re-mesure a chaque
        // changement de taille du conteneur.
        if (window.ResizeObserver) {
            new ResizeObserver(function () { map.invalidateSize(); }).observe($("dt-map"));
        }
        setTimeout(function () { map.invalidateSize(); }, 300);
        var ign = L.tileLayer("https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}", { attribution: "IGN-F/Geoplateforme", maxNativeZoom: 19, maxZoom: 22 });
        var osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "OSM", maxNativeZoom: 19, maxZoom: 22 });
        var aco = L.tileLayer("/tiles/{z}/{x}/{y}.png", { attribution: "ACO", tms: true, maxZoom: 22 });
        ign.addTo(map);
        L.control.layers({ "Vue aerienne": ign, "Plan": osm, "Plan ACO": aco }, {}, { position: "topright" }).addTo(map);
        layers.zones = L.layerGroup().addTo(map);
        layers.cams = L.layerGroup().addTo(map);
        layers.draw = L.layerGroup().addTo(map);
        map.on("click", onMapClick);
    }

    function drawZones() {
        layers.zones.clearLayers();
        zonePolys = {};
        var stats = (S.overview && S.overview.by_zone) || {};
        S.config.zones.forEach(function (z) {
            if (!z.polygon || z.polygon.length < 3) return;
            var st = stats[z._id] || { n: 0, active: false };
            var poly = L.polygon(z.polygon, {
                color: st.active ? "#dc2626" : z.color, weight: st.active ? 4 : 2,
                fillColor: z.color, fillOpacity: st.active ? 0.35 : (S.zone === z._id ? 0.3 : 0.15),
                dashArray: S.editZone && S.editZone._id === z._id ? "6 4" : null,
            });
            // Infobulle en noeud texte : Leaflet interprete une chaine comme du HTML
            poly.bindTooltip(el("span", "", z.name + " - " + st.n + " intrusion" + (st.n > 1 ? "s" : "") + (st.active ? " (en cours)" : "")), { className: "dt-tip", sticky: true });
            poly.on("click", function (e) {
                L.DomEvent.stopPropagation(e);
                if (S.edit) { openZoneForm(z); return; }
                S.zone = S.zone === z._id ? "" : z._id;
                $("dt-zone").value = S.zone;
                updateFilterCount();
                refreshAll();
                if (isPhone()) setView("list");
            });
            poly.addTo(layers.zones);
            zonePolys[z._id] = poly;
        });
    }

    function camIcon(ch, st) {
        var wrap = el("div", "dt-cam-wrap");
        var m = el("div", "dt-cam-marker" + (st && st.active ? " is-active" : st && st.new ? " has-new" : "") + (ch.armed === false ? " is-off" : ""));
        m.appendChild(icon("videocam"));
        wrap.appendChild(m);
        if (st && st.new) wrap.appendChild(el("span", "dt-cam-badge", String(st.new)));
        return L.divIcon({ html: wrap, className: "", iconSize: [34, 34], iconAnchor: [17, 17] });
    }

    function drawCams() {
        layers.cams.clearLayers();
        camMarkers = {};
        var stats = (S.overview && S.overview.by_channel) || {};
        devices().forEach(function (dv) {
            if (!dv.map) return;
            // Statistiques et etat cumules sur les canaux de l'appareil
            var st = null, armedAll = true, armedAny = false, dropped = 0;
            dv.channels.forEach(function (ch) {
                var s = stats[ch.key];
                if (s) {
                    st = st || { n: 0, new: 0, active: false };
                    st.n += s.n; st.new += s.new; st.active = st.active || s.active;
                }
                if (ch.armed === false) { armedAll = false; dropped += ch.dropped_today || 0; } else armedAny = true;
            });
            var mk = L.marker([dv.map.lat, dv.map.lng], { icon: camIcon({ armed: armedAny }, st), draggable: S.edit, zIndexOffset: 500 });
            var label = dv.name + (dv.channels.length > 1 ? " (" + dv.channels.length + " canaux)" : " - " + dv.channels[0].channel_name);
            mk.bindTooltip(el("span", "", label + (st ? " : " + st.n + " intrusion(s)" : "") +
                (!armedAny ? " - hors planning (" + dropped + " detection(s) gardee(s) sans alerte aujourd'hui)" : !armedAll ? " - certains canaux desactives" : "")), { className: "dt-tip", direction: "top", offset: [0, -16] });
            mk.on("click", function () {
                if (S.edit) return;
                S.camera = S.camera === dv.key ? "" : dv.key;
                $("dt-camera").value = S.camera;
                updateFilterCount();
                refreshAll();
                if (isPhone()) setView("list");
            });
            mk.on("dragend", function () {
                var p = mk.getLatLng();
                savePosition(dv, p.lat, p.lng);
            });
            mk.addTo(layers.cams);
            dv.channels.forEach(function (ch) { camMarkers[ch.key] = mk; });
        });
    }

    // Carte cachee (page ouverte en mode Trafic / Galerie / Carte globale) :
    // taille nulle, un cadrage donnerait la Terre entiere. On le refait des
    // que la carte redevient visible (IADetection.refresh).
    var needFit = false;
    function fitAll() {
        if (!$("dt-map").offsetWidth) { needFit = true; return; }
        needFit = false;
        var pts = [];
        S.config.channels.forEach(function (c) { if (c.map) pts.push([c.map.lat, c.map.lng]); });
        S.config.zones.forEach(function (z) { (z.polygon || []).forEach(function (p) { pts.push(p); }); });
        if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.25), { maxZoom: 19 });
    }

    /* ---------------- donnees ---------------- */
    function filterParams() {
        var p = new URLSearchParams();
        p.set("period", S.period);
        if (S.period === "day") p.set("date", S.date);
        if (S.type) p.set("type", S.type);
        if (S.zone) p.set("zone", S.zone);
        if (S.grp) p.set("group", S.grp);
        if (S.camera) p.set("camera", S.camera);
        if (S.status) p.set("status", S.status);
        if (S.ai) p.set("ai", S.ai);
        if (S.planning) p.set("planning", S.planning);
        return p;
    }

    function loadConfig() {
        return getJSON(API.config).then(function (c) {
            S.config = c;
            S.config.groups = c.groups || [];
            fillSelects();
            renderArmPanel();
            drawZones();
            drawCams();
            if (S.edit && !S.editGroup && !S.editZone && !S.drawing) renderConfig();
        });
    }

    function fillSelects() {
        var z = $("dt-zone"), cur = S.zone;
        while (z.options.length > 1) z.remove(1);
        S.config.zones.forEach(function (x) { z.appendChild(new Option(x.name, x._id)); });
        z.value = cur;
        var g = $("dt-group"), gc = S.grp;
        while (g.options.length > 1) g.remove(1);
        S.config.groups.forEach(function (x) { g.appendChild(new Option(x.name, x._id)); });
        g.value = gc;
        var c = $("dt-camera"), cc = S.camera;
        while (c.options.length > 1) c.remove(1);
        devices().forEach(function (dv) {
            if (dv.channels.length > 1) c.appendChild(new Option(dv.name + " (tous les canaux)", dv.key));
            dv.channels.forEach(function (x) { c.appendChild(new Option(x.camera_name + " - " + x.channel_name, x.key)); });
        });
        c.value = cc;
    }

    function loadOverview() {
        return getJSON(API.overview + "?" + filterParams().toString()).then(function (ov) {
            S.overview = ov;
            renderKpis(ov);
            updateBadges(ov);
            renderLive(ov, false);
            drawZones();
            drawCams();
        }).catch(function () { renderLive(null, true); });
    }

    function loadList(reset) {
        var seq = ++S.seq;
        var p = filterParams();
        p.set("limit", 60);
        if (!reset && S.next) p.set("before", S.next);
        if (reset && !S.items.length) {
            var l = $("dt-list"); l.textContent = "";
            for (var i = 0; i < 4; i++) l.appendChild(el("div", "dt-skel"));
        }
        return getJSON(API.episodes + "?" + p.toString()).then(function (d) {
            if (seq !== S.seq) return;
            if (reset && S.period !== "day") notifyNew(d.items);
            S.items = reset ? d.items : S.items.concat(d.items);
            S.next = d.next;
            renderList();
        }).catch(function (e) {
            if (seq !== S.seq) return;
            var l = $("dt-list"); l.textContent = "";
            var em = el("div", "dt-empty"); em.appendChild(icon("cloud_off")); em.appendChild(el("div", "", "Chargement impossible (" + e.message + ")."));
            l.appendChild(em);
        });
    }

    function refreshAll() {
        loadOverview();
        loadList(true);
    }

    /* ---------------- synthese ---------------- */
    function renderLive(ov, error) {
        var box = $("dt-live"), txt = $("dt-live-text");
        box.className = "dt-live";
        if (error) { box.classList.add("is-error"); txt.textContent = "Hors ligne"; return; }
        if (S.period === "day") { box.classList.add("is-past"); txt.textContent = "Journee du " + S.date.split("-").reverse().join("/"); return; }
        if (ov.totals.active) { box.classList.add("is-alert"); txt.textContent = ov.totals.active + " intrusion" + (ov.totals.active > 1 ? "s" : "") + " en cours"; }
        else { box.classList.add("is-ok"); txt.textContent = "En direct - aucune intrusion en cours"; }
    }

    function kpi(ic, color, n, label, alert) {
        var k = el("div", "dt-kpi" + (alert ? " is-alert" : ""));
        var i = icon(ic); i.style.setProperty("--c", color); k.style.setProperty("--c", color);
        k.appendChild(i);
        var t = el("div");
        t.appendChild(el("div", "dt-kpi-n", String(n)));
        t.appendChild(el("div", "dt-kpi-l", label));
        k.appendChild(t);
        return k;
    }

    function renderKpis(ov) {
        var box = $("dt-kpis");
        box.textContent = "";
        var t = ov.totals;
        box.appendChild(kpi("emergency", "#dc2626", t.active, "en cours", t.active > 0));
        box.appendChild(kpi("mark_email_unread", "#b45309", t["new"], "a traiter", t["new"] > 0));
        box.appendChild(kpi("directions_walk", "#f97316", t.human, "pietons"));
        box.appendChild(kpi("directions_car", "#3b82f6", t.vehicle, "vehicules"));
        box.appendChild(kpi("shield", "#64748b", t.episodes, "intrusions"));
        var sp = el("div", "dt-kpi dt-kpi-spark");
        sp.appendChild(el("div", "dt-kpi-l", "Activite " + (S.period === "day" ? "de la journee" : PERIOD_LABEL[S.period]) + (ov.period.step_min < 60 ? " (par " + ov.period.step_min + " min)" : " (par " + ov.period.step_min / 60 + " h)")));
        var n = ov.period.bars, max = 1;
        for (var i = 0; i < n; i++) max = Math.max(max, ov.flow.human[i] + ov.flow.vehicle[i] + ov.flow.other[i]);
        var svg = svgEl("svg", { "class": "dt-spark", viewBox: "0 0 " + n + " 34", preserveAspectRatio: "none" });
        for (var j = 0; j < n; j++) {
            var y = 34;
            [["human", "h"], ["vehicle", "v"], ["other", "o"]].forEach(function (k) {
                var v = ov.flow[k[0]][j];
                if (!v) return;
                var h = 32 * v / max;
                y -= h;
                svg.appendChild(svgEl("rect", { "class": k[1], x: j + 0.12, y: y, width: 0.76, height: h }));
            });
        }
        sp.appendChild(svg);
        box.appendChild(sp);
    }

    /* ---------------- telephone ---------------- */
    var PHONE_MQ = window.matchMedia("(max-width: 820px)");
    function isPhone() { return PHONE_MQ.matches; }

    // Telephone : la periode quitte l'en-tete (trop etroit) pour les filtres
    var periodHome = null;
    function placePeriod() {
        var p = $("dt-period");
        if (!periodHome) periodHome = { parent: p.parentNode, next: p.nextSibling };
        if (isPhone()) $("dt-filters").insertBefore(p, $("dt-filters").firstChild);
        else if (p.parentNode !== periodHome.parent) periodHome.parent.insertBefore(p, periodHome.next);
    }

    function setView(v) {
        document.body.classList.remove("dt-view-list", "dt-view-map", "dt-view-groups");
        document.body.classList.add("dt-view-" + v);
        Array.prototype.forEach.call(document.querySelectorAll("#dt-tabs button"), function (b) { b.classList.toggle("active", b.dataset.view === v); });
        if (v === "map" && map) setTimeout(function () { map.invalidateSize(); }, 50);
        if (v === "groups") renderArmPanel();
    }

    // Fiche, visionneuse ou configuration ouverte : masque la bulle de l'assistant
    function syncSheet() {
        var open = !$("dt-detail").hidden || !$("dt-lightbox").hidden || !$("dt-config").hidden;
        document.body.classList.toggle("dt-sheet-open", open);
    }

    function updateFilterCount() {
        var n = [S.type, S.grp, S.zone, S.camera, S.ai, S.planning].filter(Boolean).length + (S.status !== "open" ? 1 : 0) + (S.period !== "today" ? 1 : 0);
        $("dt-filters-n").textContent = n ? String(n) : "";
    }

    // Badges des onglets et du titre de l'onglet du navigateur
    var baseTitle = document.title;
    function updateBadges(ov) {
        var t = ov.totals, ln = $("dt-tab-list-n"), mn = $("dt-tab-map-n");
        ln.hidden = !t["new"]; ln.textContent = t["new"] > 99 ? "99+" : String(t["new"]);
        mn.hidden = !t.active; mn.textContent = String(t.active);
        var n = t.active || t["new"];
        // "COCKPIT" reste en tete du titre (reperage de l'onglet / de l'appli)
        document.title = baseTitle + (t.active ? " (" + t.active + " en cours)" : n ? " (" + n + ")" : "");
    }

    // Nouvelle intrusion apparue depuis le dernier passage : signalement
    var lastSeenStart = null;
    function notifyNew(items) {
        var newest = items.length ? items[0].started_at : null;
        if (lastSeenStart && newest && newest > lastSeenStart) {
            var fresh = items.filter(function (x) { return x.started_at > lastSeenStart; });
            var ep = fresh[0];
            toast("warning", (fresh.length > 1 ? fresh.length + " nouvelles intrusions - " : "Nouvelle intrusion : ") +
                (TYPE[kind(ep.types)] || TYPE.other).label + " - " + ep.camera_name);
            if (navigator.vibrate) navigator.vibrate([120, 60, 120]);
        }
        if (newest && (!lastSeenStart || newest > lastSeenStart)) lastSeenStart = newest;
    }

    // Glisser a gauche / a droite (photos)
    function onSwipe(node, fn) {
        var x0 = null, y0 = null;
        node.addEventListener("touchstart", function (e) { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
        node.addEventListener("touchend", function (e) {
            if (x0 === null) return;
            var dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
            x0 = null;
            if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy) * 1.4) fn(dx < 0 ? 1 : -1);
        }, { passive: true });
    }

    function force(g, state, hours) {
        return send("POST", API.groups + "/" + g._id + "/override", { state: state, hours: hours }).then(function (r) {
            toast("success", g.name + " : " + r.state_label);
            return loadConfig();
        }).catch(function (e) { toast("error", e.message); });
    }

    // Onglet Groupes (telephone) : etat, activer / desactiver d'un geste
    var armDur = 4;
    function renderArmPanel() {
        var box = $("dt-armpanel");
        if (!box) return;
        box.textContent = "";
        if (!S.config.groups.length) {
            var em = el("div", "dt-empty");
            em.appendChild(icon("shield_lock"));
            em.appendChild(el("div", "", "Aucun groupe. Toutes les cameras en detection sont actives en permanence."));
            em.appendChild(el("div", "dt-help", "Les groupes et leurs programmes se creent depuis un ordinateur (bouton Configurer)."));
            box.appendChild(em);
            return;
        }
        var dur = el("div", "dt-arm-dur");
        dur.appendChild(el("span", "", "Duree du forcage"));
        var seg = el("div", "dt-seg");
        [[1, "1 h"], [4, "4 h"], [12, "12 h"], [24, "24 h"]].forEach(function (d) {
            var b = el("button", armDur === d[0] ? "active" : "", d[1]); b.type = "button";
            b.addEventListener("click", function () { armDur = d[0]; renderArmPanel(); });
            seg.appendChild(b);
        });
        dur.appendChild(seg);
        box.appendChild(dur);
        S.config.groups.forEach(function (g) {
            var c = el("div", "dt-arm-card " + (g.armed ? "is-on" : "is-off"));
            var h = el("div", "dt-arm-head");
            var sw = el("span", "sw"); sw.style.background = g.color; h.appendChild(sw);
            h.appendChild(el("b", "", g.name));
            h.appendChild(stateChip(g));
            c.appendChild(h);
            c.appendChild(el("div", "dt-arm-label", g.state_label + (!g.armed && g.dropped_today ? " - " + g.dropped_today + " detection(s) hors planning aujourd'hui (gardees sans alerte)" : "")));
            var acts = el("div", "dt-arm-actions");
            var on = el("button", "dt-btn is-arm"); on.type = "button";
            on.appendChild(icon("notifications_active")); on.appendChild(document.createTextNode("Activer " + armDur + " h"));
            on.addEventListener("click", function () { force(g, "on", armDur); });
            var off = el("button", "dt-btn is-disarm"); off.type = "button";
            off.appendChild(icon("notifications_off")); off.appendChild(document.createTextNode("Desactiver " + armDur + " h"));
            off.addEventListener("click", function () {
                showConfirmToast("Desactiver " + g.name + " pendant " + armDur + " h ? Les detections seront ignorees (ni photo, ni alerte).", { okLabel: "Desactiver", type: "warning" })
                    .then(function (ok) { if (ok) force(g, "off", armDur); });
            });
            acts.appendChild(on); acts.appendChild(off);
            c.appendChild(acts);
            var cams = el("div", "dt-arm-cams");
            g.members.forEach(function (m) {
                var ch = chanByKey(m.key);
                if (!ch) return;
                var r = el("div"); var dot = el("i"); dot.style.background = ch.armed ? "#16a34a" : "#94a3b8";
                r.appendChild(dot); r.appendChild(el("span", "", ch.camera_name + " - " + ch.channel_name));
                cams.appendChild(r);
            });
            c.appendChild(cams);
            var links = el("div", "dt-arm-links");
            if (g.source === "override") {
                var cancel = el("button", "dt-link-btn", "Revenir au programme"); cancel.type = "button";
                cancel.addEventListener("click", function () { force(g, null, 0); });
                links.appendChild(cancel);
            }
            var see = el("button", "dt-link-btn", "Voir ses intrusions"); see.type = "button";
            see.addEventListener("click", function () {
                S.grp = g._id; $("dt-group").value = g._id; S.status = ""; $("dt-status").value = "";
                updateFilterCount(); refreshAll(); setView("list");
            });
            links.appendChild(see);
            c.appendChild(links);
            box.appendChild(c);
        });
        box.appendChild(el("p", "dt-help", "Un forcage prend le pas sur le programme horaire jusqu'a son echeance. Le programme se modifie depuis un ordinateur (bouton Configurer)."));
    }

    /* ---------------- activation des groupes ---------------- */
    function stateChip(g) {
        var forced = g.source === "override";
        var s = el("span", "dt-gstate " + (g.armed ? "is-on" : "is-off") + (forced ? " is-forced" : ""));
        s.appendChild(icon(g.armed ? "notifications_active" : "notifications_off"));
        s.appendChild(document.createTextNode(g.armed ? "Active" : "Desactive"));
        return s;
    }



    /* ---------------- flux ---------------- */
    /* ---------------- verification IA locale ---------------- */
    var AI_LABEL = { personne: "une personne", vehicule: "un vehicule", velo: "un velo", oiseau: "un oiseau",
                     animal: "un animal", objet: "un objet", rien: "rien", panneau: "un panneau",
                     branche: "des branches", reflet: "un reflet", sol: "un sol vide", grillage: "un grillage" };
    var AI_NAME = { personne: "Personne", vehicule: "Vehicule", velo: "Velo", oiseau: "Oiseau", animal: "Animal",
                    panneau: "Panneau", branche: "Branches", reflet: "Reflet", sol: "Sol vide", grillage: "Grillage", objet: "Objet" };
    var AI_COLOR = { personne: "#16a34a", vehicule: "#2563eb", velo: "#16a34a" };
    // Cadres trouves par le detecteur de l'IA sur cette photo (hors "objet",
    // trop vague : un frisbee pour une corneille)
    function aiBoxes(p) {
        return ((p.ai && p.ai.detections) || []).filter(function (d) { return d.box && d.category !== "objet" && d.conf >= 0.2; });
    }
    // r : portion de la photo affichee (zoom), en fractions
    function aiBoxEl(d, r) {
        var bx = el("div", "dt-box dt-box-ai");
        bx.style.left = ((d.box.x - r.x) / r.w * 100) + "%"; bx.style.top = ((d.box.y - r.y) / r.h * 100) + "%";
        bx.style.width = (d.box.w / r.w * 100) + "%"; bx.style.height = (d.box.h / r.h * 100) + "%";
        bx.style.setProperty("--bc", AI_COLOR[d.category] || "#7c3aed");
        bx.appendChild(el("span", "dt-box-tag", "IA voit : " + (AI_NAME[d.category] || d.category).toLowerCase() + " " + Math.round(d.conf * 100) + " %"));
        return bx;
    }
    var LABEL_CAT = {
        personne: { label: "Personne", icon: "directions_walk", color: "#16a34a" },
        vehicule: { label: "Vehicule", icon: "directions_car", color: "#2563eb" },
    };
    // Cadres poses par l'operateur (verite terrain pour l'entrainement)
    function labelBoxEl(b, r, by) {
        var c = LABEL_CAT[b.category] || LABEL_CAT.personne;
        var bx = el("div", "dt-box dt-box-label");
        bx.style.left = ((b.x - r.x) / r.w * 100) + "%"; bx.style.top = ((b.y - r.y) / r.h * 100) + "%";
        bx.style.width = (b.w / r.w * 100) + "%"; bx.style.height = (b.h / r.h * 100) + "%";
        bx.style.setProperty("--bc", c.color);
        var t = el("span", "dt-box-tag", "verifie : " + c.label.toLowerCase());
        if (by) t.title = "Indique par " + by;
        bx.appendChild(t);
        return bx;
    }

    // Editeur : l'operateur dessine ou est la personne / le vehicule sur une
    // photo (souris ou doigt), plusieurs cadres possibles, loupe pour les
    // petites cibles. "Rien sur cette photo" enregistre une liste vide.
    function openAnnotator(ep, idx, onSaved) {
        var p = ep.photos[idx];
        var boxes = ((p.label && p.label.boxes) || []).map(function (b) { return { x: b.x, y: b.y, w: b.w, h: b.h, category: b.category }; });
        var cat = "personne", zoomed = false, nat = null;
        var ov = el("div", "dt-annot");
        var panel = el("div", "dt-annot-panel");
        ov.appendChild(panel);
        var head = el("div", "dt-annot-head");
        head.appendChild(el("b", "", "Ou est la cible ?"));
        head.appendChild(el("span", "", "Choisis Personne ou Vehicule, puis trace un cadre autour avec la souris ou le doigt. Plusieurs cadres possibles ; touche un cadre pour le supprimer."));
        panel.appendChild(head);

        var tools = el("div", "dt-annot-tools");
        var seg = el("div", "dt-annot-cats");
        Object.keys(LABEL_CAT).forEach(function (k) {
            var b = el("button", "dt-annot-cat"); b.type = "button"; b.dataset.cat = k;
            b.style.setProperty("--cc", LABEL_CAT[k].color);
            b.appendChild(icon(LABEL_CAT[k].icon)); b.appendChild(el("span", "", LABEL_CAT[k].label));
            b.addEventListener("click", function () { cat = k; syncTools(); });
            seg.appendChild(b);
        });
        tools.appendChild(seg);
        var zb = el("button", "dt-btn"); zb.type = "button";
        zb.addEventListener("click", function () { zoomed = !zoomed; layout(); syncTools(); });
        if (p.rect) tools.appendChild(zb);
        panel.appendChild(tools);

        var stage = el("div", "dt-annot-stage");
        var im = document.createElement("img"); im.alt = ""; im.draggable = false;
        var layer = el("div", "dt-annot-layer");
        stage.appendChild(im); stage.appendChild(layer);
        panel.appendChild(stage);

        var foot = el("div", "dt-annot-foot");
        var none = el("button", "dt-btn"); none.type = "button";
        none.appendChild(icon("block")); none.appendChild(document.createTextNode("Rien sur cette photo"));
        var cancel = el("button", "dt-btn"); cancel.type = "button"; cancel.textContent = "Annuler";
        var save = el("button", "dt-btn dt-btn-primary"); save.type = "button";
        foot.appendChild(none); foot.appendChild(el("span", "dt-annot-sp")); foot.appendChild(cancel); foot.appendChild(save);
        panel.appendChild(foot);

        function region() {
            if (!zoomed || !p.rect || !nat) return { x: 0, y: 0, w: 1, h: 1 };
            return zoomRegion(p.rect, nat.w / nat.h, nat.w / nat.h);
        }
        function layout() {
            if (!nat) return;
            var r = region();
            var ratio = (r.w * nat.w) / (r.h * nat.h);
            var maxW = Math.min(window.innerWidth - 48, 1200), maxH = window.innerHeight - 260;
            var w = Math.min(maxW, maxH * ratio);
            stage.style.width = w + "px"; stage.style.height = (w / ratio) + "px";
            im.style.width = (100 / r.w) + "%";
            im.style.left = (-r.x / r.w * 100) + "%";
            im.style.top = (-r.y / r.h * 100) + "%";
            draw();
        }
        function draw(tmp) {
            var r = region();
            layer.textContent = "";
            boxes.concat(tmp ? [tmp] : []).forEach(function (b, i) {
                var bx = labelBoxEl(b, r);
                if (b !== tmp) {
                    bx.classList.add("is-editable");
                    bx.title = "Supprimer ce cadre";
                    bx.addEventListener("pointerdown", function (e) { e.stopPropagation(); });
                    bx.addEventListener("click", function (e) { e.stopPropagation(); boxes.splice(i, 1); draw(); syncTools(); });
                }
                layer.appendChild(bx);
            });
        }
        function syncTools() {
            Array.prototype.forEach.call(seg.children, function (b) { b.classList.toggle("is-on", b.dataset.cat === cat); });
            zb.textContent = "";
            zb.appendChild(icon(zoomed ? "zoom_out" : "zoom_in"));
            zb.appendChild(document.createTextNode(zoomed ? "Vue entiere" : "Loupe sur la cible"));
            save.textContent = boxes.length ? "Enregistrer (" + boxes.length + " cadre" + (boxes.length > 1 ? "s" : "") + ")" : "Enregistrer";
            save.disabled = !boxes.length;
        }
        // Dessin : coordonnees ramenees a la photo entiere (0-1)
        var start = null;
        function pt(e) {
            var b = stage.getBoundingClientRect(), r = region();
            var fx = Math.min(1, Math.max(0, (e.clientX - b.left) / b.width));
            var fy = Math.min(1, Math.max(0, (e.clientY - b.top) / b.height));
            return { x: r.x + fx * r.w, y: r.y + fy * r.h };
        }
        function rectOf(a, b) {
            return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y), category: cat };
        }
        stage.addEventListener("pointerdown", function (e) {
            if (e.button !== undefined && e.button !== 0) return;
            start = pt(e);
            stage.setPointerCapture(e.pointerId);
            e.preventDefault();
        });
        stage.addEventListener("pointermove", function (e) { if (start) draw(rectOf(start, pt(e))); });
        stage.addEventListener("pointerup", function (e) {
            if (!start) return;
            var b = rectOf(start, pt(e));
            start = null;
            if (b.w > 0.004 && b.h > 0.004) boxes.push(b);
            draw(); syncTools();
        });
        stage.addEventListener("pointercancel", function () { start = null; draw(); });

        function close() { window.removeEventListener("resize", layout); document.removeEventListener("keydown", onKey, true); ov.remove(); }
        function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); close(); } }
        function submit(list) {
            save.disabled = none.disabled = true;
            send("POST", API.episodes + "/" + ep.id + "/photos/" + idx + "/label", { photo: p.photo, boxes: list }).then(function (r) {
                p.label = r.label;
                if (r.ai) ep.ai = r.ai;
                toast("success", list.length ? "Cible enregistree, merci : elle servira a entrainer l'IA" : "Enregistre : rien sur cette photo");
                close();
                if (onSaved) onSaved(r);
            }).catch(function (e) { save.disabled = none.disabled = false; toast("error", e.message); });
        }
        save.addEventListener("click", function () { submit(boxes); });
        none.addEventListener("click", function () { submit([]); });
        cancel.addEventListener("click", close);
        ov.addEventListener("click", function (e) { if (e.target === ov) close(); });
        window.addEventListener("resize", layout);
        document.addEventListener("keydown", onKey, true);
        im.addEventListener("load", function () { nat = { w: im.naturalWidth, h: im.naturalHeight }; layout(); });
        im.src = p.photo;
        syncTools();
        document.body.appendChild(ov);
    }
    // Re-rend le bloc "Verification IA" de la fiche ouverte et la carte de la liste
    function refreshAi(ep) {
        var old = document.querySelector("#dt-detail-body .dt-ai");
        if (old && S.openId === ep.id) old.replaceWith(aiPanel(ep));
        S.items.forEach(function (x) { if (x.id === ep.id) x.ai = ep.ai; });
        renderList();
    }
    function pct(x) { return x != null ? " (" + Math.round(x * 100) + " %)" : ""; }
    // Ce que l'IA voit le plus probablement : le pourcentage le plus haut,
    // detecteur (personne / vehicule) ou avis (oiseau, panneau...), quelle que
    // soit la decision. ai : verdict d'episode {label, conf, guess} ou resultat
    // d'une photo {detections, guess}
    function aiTop(ai) {
        if (!ai) return null;
        var c = [];
        if (ai.label && ai.label !== "rien" && ai.conf) c.push({ label: ai.label, p: ai.conf });
        (ai.detections || []).forEach(function (d) { if (d.category !== "objet") c.push({ label: d.category, p: d.conf }); });
        if (ai.guess && ai.guess[0]) c.push({ label: ai.guess[0].label, p: ai.guess[0].p });
        c.sort(function (a, b) { return b.p - a.p; });
        return c[0] || null;
    }
    function aiBadge(ai) {
        if (!ai || !ai.status) return null;
        var t = aiTop(ai);
        var lab = t ? (AI_NAME[t.label] || t.label).toLowerCase() + " " + Math.round(t.p * 100) + " %" : "rien";
        if (ai.status === "filtered") return badge("dt-b-ai-filtered", "filter_alt", "Filtree IA : " + lab);
        if (ai.status === "confirmed") return badge("dt-b-ai-confirmed", "verified", "IA : " + lab);
        return badge("dt-b-ai-unverified", "help", "Non verifiee");
    }
    // Verdict + avis de l'operateur (pouce). Les avis servent a regler le
    // seuil du filtre et a constituer un jeu d'entrainement.
    function aiPanel(ep) {
        var ai = ep.ai || {};
        var box = el("div", "dt-ai" + (ai.status ? " is-" + ai.status : ""));
        var v = el("div", "dt-ai-verdict");
        var txt, top = aiTop(ai);
        var seen = top ? (AI_LABEL[top.label] || top.label) + pct(top.p) : "rien";
        if (ai.status === "filtered") {
            v.appendChild(icon("filter_alt"));
            txt = "Filtree : l'IA voit surtout " + seen + ", pas de personne ni de vehicule. Aucune alerte envoyee.";
        } else if (ai.status === "confirmed") {
            v.appendChild(icon("verified"));
            txt = "Confirmee : l'IA voit surtout " + seen +
                (top && top.label !== ai.label ? ", et detecte " + (AI_LABEL[ai.label] || ai.label) + pct(ai.real_conf || ai.conf) : "") +
                (ep.armed === false ? ". Hors planning : pas d'alerte." : ". Alerte envoyee.");
        } else {
            v.appendChild(icon("help"));
            txt = "Non verifiee" + (ai.reason ? " (" + ai.reason + ")" : "") +
                (ep.armed === false ? ". Hors planning : pas d'alerte." : " : alerte envoyee sans verification.");
        }
        if (ai.status === "filtered" && ep.armed === false) txt = txt.replace("Aucune alerte envoyee.", "Hors planning : pas d'alerte de toute facon.");
        v.appendChild(el("span", "", txt));
        box.appendChild(v);
        if (ai.guess && ai.guess.length) {
            var gl = el("div", "dt-ai-guess");
            gl.appendChild(el("span", "", "Selon l'IA :"));
            ai.guess.slice(0, 3).forEach(function (gs, i) {
                if (i && gs.p < 0.03) return;
                gl.appendChild(el("span", "chip" + (i ? "" : " is-top"), (AI_NAME[gs.label] || gs.label) + " " + Math.round(gs.p * 100) + " %"));
            });
            box.appendChild(gl);
        }
        if (!ai.status) return box;
        var vote = el("div", "dt-ai-vote");
        vote.appendChild(el("span", "dt-ai-vote-q", "L'IA a-t-elle raison ? Ton avis sert a l'entrainer."));
        var row = el("div", "dt-thumbs");
        vote.appendChild(row);
        [["up", "thumb_up", "Bien vu"], ["down", "thumb_down", "Erreur"]].forEach(function (o) {
            var b = el("button", "dt-thumb-btn is-" + o[0] + (ai.vote === o[0] ? " is-on" : "")); b.type = "button";
            b.setAttribute("aria-pressed", ai.vote === o[0] ? "true" : "false");
            b.title = o[0] === "up" ? "L'IA a raison" : "L'IA s'est trompee";
            b.appendChild(icon(o[1])); b.appendChild(el("span", "", o[2]));
            b.addEventListener("click", function () {
                var nv = ai.vote === o[0] ? null : o[0];  // second clic : retire l'avis
                send("POST", API.episodes + "/" + ep.id + "/ai-vote", { vote: nv }).then(function (r) {
                    ep.ai = r.ai;
                    refreshAi(ep);
                    toast("success", nv ? "Avis enregistre, merci" : "Avis retire");
                }).catch(function (e) { toast("error", e.message); });
            });
            row.appendChild(b);
        });
        if (ai.vote && ai.vote_by) vote.appendChild(el("span", "dt-ai-vote-by", "Avis de " + ai.vote_by + " " + ago(ai.vote_at)));
        // Erreur sur une intrusion filtree : montrer ou etait la cible
        if (ai.vote === "down" && ep.photos && ep.photos.length) {
            var lb = el("button", "dt-btn dt-btn-primary dt-annot-open"); lb.type = "button";
            lb.appendChild(icon("ads_click"));
            lb.appendChild(document.createTextNode("Indiquer ou est la personne ou le vehicule"));
            lb.addEventListener("click", function () {
                var i = gallery && gallery.ep === ep ? gallery.idx : ep.photos.length - 1;
                openAnnotator(ep, i, function () { if (gallery && gallery.ep === ep) gallery.render(); refreshAi(ep); });
            });
            vote.appendChild(lb);
        }
        box.appendChild(vote);
        return box;
    }

    function card(ep) {
        var k = kind(ep.types);
        var c = el("button", "dt-card" + (ep.status === "new" ? " is-new" : "") + (ep.active ? " is-active" : "") +
            (ep.status === "closed" ? " is-closed" : "") + (ep.id === S.openId ? " is-open" : ""));
        c.type = "button";
        var th = el("div", "dt-thumb");
        if (ep.thumb) {
            var im = document.createElement("img");
            im.loading = "lazy"; im.alt = ""; im.src = ep.thumb;
            im.addEventListener("error", function () { th.textContent = ""; th.appendChild(icon("no_photography")); });
            th.appendChild(im);
        } else th.appendChild(icon("no_photography"));
        if (ep.photos_n > 1) th.appendChild(el("span", "dt-thumb-n", ep.photos_n + " photos"));
        c.appendChild(th);
        var b = el("div", "dt-card-body");
        var top = el("div", "dt-card-top");
        ep.types.forEach(function (t) { var s = TYPE[t] || TYPE.other; top.appendChild(badge(s.cls, s.icon, s.label)); });
        if (ep.active) top.appendChild(badge("dt-b-active", "emergency", "En cours"));
        else if (ep.status !== "seen") top.appendChild(badge("dt-b-" + ep.status, null, STATUS[ep.status]));
        if (ep.armed === false) top.appendChild(badge("dt-b-offplan", "schedule", "Hors planning"));
        var ab = aiBadge(ep.ai);
        if (ab) top.appendChild(ab);
        top.appendChild(el("span", "dt-card-time", fmtTime(ep.started_at)));
        b.appendChild(top);
        b.appendChild(el("div", "dt-card-where", ep.camera_name + (ep.channel_name ? " - " + ep.channel_name : "")));
        var meta = el("div", "dt-card-meta");
        ep.zones.forEach(function (z) { var zc = el("span", "dt-zone-chip"); var i = el("i"); i.style.background = z.color; zc.appendChild(i); zc.appendChild(document.createTextNode(z.name)); meta.appendChild(zc); });
        meta.appendChild(el("span", "", "Duree " + fmtDur(ep.duration_s)));
        if (ep.targets > 1) meta.appendChild(el("span", "", ep.targets + " cibles"));
        meta.appendChild(el("span", "", isToday(ep.started_at) ? ago(ep.started_at) : fmtDay(ep.started_at)));
        b.appendChild(meta);
        c.appendChild(b);
        c.addEventListener("click", function () { openDetail(ep.id); });
        return c;
    }

    function groupOf(ep) {
        if (S.groupBy === "group") {
            var ch = chanByKey(chanKey(ep));
            var gs = S.config.groups.filter(function (g) { return ch && (ch.groups || []).indexOf(g._id) >= 0; });
            return gs.length ? gs.map(function (g) { return { key: g._id, label: g.name, color: g.color }; }) : [{ key: "_", label: "Hors groupe", color: "#94a3b8" }];
        }
        if (S.groupBy === "zone") return ep.zones.length ? ep.zones.map(function (z) { return { key: z.id, label: z.name, color: z.color }; }) : [{ key: "_", label: "Hors zone", color: "#94a3b8" }];
        if (S.groupBy === "camera") return [{ key: chanKey(ep), label: ep.camera_name + (ep.channel_name ? " - " + ep.channel_name : ""), color: "#475569" }];
        if (S.groupBy === "type") { var t = TYPE[kind(ep.types)]; return [{ key: kind(ep.types), label: t.label + "s", color: t.color }]; }
        return [{ key: "", label: "" }];
    }

    function renderList() {
        var l = $("dt-list");
        var keep = l.scrollTop;
        l.textContent = "";
        $("dt-count").textContent = S.items.length + (S.next ? "+" : "") + " intrusion" + (S.items.length > 1 ? "s" : "");
        if (!S.items.length) {
            var em = el("div", "dt-empty");
            em.appendChild(icon("verified_user"));
            em.appendChild(el("div", "", S.status === "open" ? "Aucune intrusion a traiter." : "Aucune intrusion pour ces criteres."));
            if (!S.config.channels.length) em.appendChild(el("div", "dt-help", "Aucune camera n'a l'usage Detection d'intrusion : activez-le sur un canal dans la page Cameras."));
            l.appendChild(em);
            return;
        }
        if (!S.groupBy) {
            S.items.forEach(function (ep) { l.appendChild(card(ep)); });
        } else {
            var groups = {}, order = [];
            S.items.forEach(function (ep) {
                groupOf(ep).forEach(function (g) {
                    if (!groups[g.key]) { groups[g.key] = { g: g, items: [] }; order.push(g.key); }
                    groups[g.key].items.push(ep);
                });
            });
            order.sort(function (a, b) { return groups[b].items.length - groups[a].items.length; });
            order.forEach(function (k) {
                var g = groups[k];
                var h = el("div", "dt-group-title");
                var i = el("i"); i.style.background = g.g.color; h.appendChild(i);
                h.appendChild(document.createTextNode(g.g.label));
                h.appendChild(el("em", "", String(g.items.length)));
                l.appendChild(h);
                g.items.forEach(function (ep) { l.appendChild(card(ep)); });
            });
        }
        if (S.next) {
            var more = el("div", "dt-more");
            var mb = el("button", "dt-btn", "Afficher plus"); mb.type = "button";
            mb.addEventListener("click", function () { loadList(false); });
            more.appendChild(mb);
            l.appendChild(more);
        }
        l.scrollTop = keep;
    }

    /* ---------------- fiche ---------------- */
    // Etiquette du cadre de la camera : ce que dit la camera ET ce que voit
    // l'IA (son pourcentage le plus haut), cote a cote
    function boxTag(p) {
        var tag = el("span", "dt-box-tag", "camera : " + (BOX_LABEL[p.type] || "objet"));
        var top = aiTop(p.ai);
        var tip = "Ce que la camera a cru voir" + (p.target_id ? " (cible " + p.target_id + ")" : "");
        if (p.ai) {
            var lab = top ? (AI_NAME[top.label] || top.label).toLowerCase() + " " + Math.round(top.p * 100) + " %" : "rien";
            tag.appendChild(el("b", "dt-box-tag-ai", "IA : " + lab));
            tip += ", et ce que voit l'IA (son pourcentage le plus haut)";
        }
        tag.title = tip + ". Le cadre en pointilles = ce que detecte l'IA.";
        return tag;
    }
    function photoView(p, ep, big) {
        var wrap = el("div", "dt-photo");
        var im = document.createElement("img");
        im.alt = "Photo de l'intrusion";
        im.src = p.photo;
        wrap.appendChild(im);
        if (p.rect) {
            // Cadre fin + etiquette, a la maniere des modeles de detection
            var bx = el("div", "dt-box" + (p.rect.y < 0.06 ? " tag-inside" : ""));
            bx.style.left = (p.rect.x * 100) + "%"; bx.style.top = (p.rect.y * 100) + "%";
            bx.style.width = (p.rect.w * 100) + "%"; bx.style.height = (p.rect.h * 100) + "%";
            bx.style.setProperty("--bc", (TYPE[p.type] || TYPE.other).color);
            bx.appendChild(boxTag(p));
            wrap.appendChild(bx);
        }
        aiBoxes(p).forEach(function (d) {
            wrap.appendChild(aiBoxEl(d, { x: 0, y: 0, w: 1, h: 1 }));
        });
        ((p.label && p.label.boxes) || []).forEach(function (b) {
            wrap.appendChild(labelBoxEl(b, { x: 0, y: 0, w: 1, h: 1 }, p.label.by));
        });
        wrap.appendChild(el("div", "dt-photo-cap", (TYPE[p.type] || TYPE.other).label + " - " + fmtTime(p.at)));
        return wrap;
    }

    // Zone de la photo a afficher pour zoomer sur la cible (fractions de
    // l'image), au format du cadre (ratio largeur / hauteur = frameRatio).
    function zoomRegion(rect, imgRatio, frameRatio) {
        var rw = Math.max(rect.w * 2.6, rect.h * 2.6 * frameRatio / imgRatio, 0.06);
        var rh = rw * imgRatio / frameRatio;
        if (rh > 1) { rh = 1; rw = frameRatio / imgRatio; }
        rw = Math.min(rw, 1);
        var cx = rect.x + rect.w / 2, cy = rect.y + rect.h / 2;
        return {
            x: Math.min(Math.max(0, cx - rw / 2), 1 - rw), y: Math.min(Math.max(0, cy - rh / 2), 1 - rh),
            w: rw, h: rh,
        };
    }

    // Decoupage zoome sur la cible, a partir de la photo de scene (meme
    // fichier, recadre en CSS : pas de requete supplementaire)
    function zoomView(p) {
        var z = el("div", "dt-zoom");
        if (!p.rect) { z.appendChild(icon("crop_free")); return z; }
        var frame = 4 / 3;
        var im = document.createElement("img");
        im.alt = "Zoom sur la cible";
        im.addEventListener("load", function () {
            var r = zoomRegion(p.rect, (im.naturalWidth / im.naturalHeight) || 16 / 9, frame);
            im.style.width = (100 / r.w) + "%";
            im.style.left = (-r.x / r.w * 100) + "%";
            im.style.top = (-r.y / r.h * 100) + "%";
            var bx = el("div", "dt-box");
            bx.style.left = ((p.rect.x - r.x) / r.w * 100) + "%"; bx.style.top = ((p.rect.y - r.y) / r.h * 100) + "%";
            bx.style.width = (p.rect.w / r.w * 100) + "%"; bx.style.height = (p.rect.h / r.h * 100) + "%";
            bx.style.setProperty("--bc", (TYPE[p.type] || TYPE.other).color);
            bx.appendChild(boxTag(p));
            if ((p.rect.y - r.y) / r.h < 0.08) bx.classList.add("tag-inside");
            z.appendChild(bx);
            aiBoxes(p).forEach(function (d) { z.appendChild(aiBoxEl(d, r)); });
            ((p.label && p.label.boxes) || []).forEach(function (b) { z.appendChild(labelBoxEl(b, r, p.label.by)); });
            z.appendChild(el("span", "dt-zoom-x", "x" + Math.max(1, Math.round(1 / r.w * 10) / 10)));
        });
        im.src = p.photo;
        z.appendChild(im);
        return z;
    }

    // Galerie d'une intrusion : photo de scene + zoom cible, fleches,
    // compteur, bandeau de vignettes, touches fleches gauche / droite.
    var gallery = null;
    function buildGallery(photos, start, ep) {
        var g = { photos: photos, idx: start, root: el("div", "dt-gallery"), ep: ep || null };
        var stage = el("div", "dt-stage");
        var side = el("div", "dt-zoom-wrap");
        var strip = photos.length > 1 ? el("div", "dt-strip") : null;
        function nav(cls, ic, delta, label) {
            var b = el("button", "dt-nav " + cls); b.type = "button"; b.title = label; b.setAttribute("aria-label", label);
            b.appendChild(icon(ic));
            b.addEventListener("click", function (e) { e.stopPropagation(); g.go(g.idx + delta); });
            return b;
        }
        g.render = function () {
            var p = photos[g.idx];
            stage.textContent = "";
            var view = photoView(p);
            view.title = "Agrandir";
            view.addEventListener("click", function () { openLightbox(photos, g.idx, "scene"); });
            stage.appendChild(view);
            if (photos.length > 1) {
                stage.appendChild(nav("prev", "chevron_left", -1, "Photo precedente"));
                stage.appendChild(nav("next", "chevron_right", 1, "Photo suivante"));
                stage.appendChild(el("span", "dt-counter", (g.idx + 1) + " / " + photos.length));
            }
            side.textContent = "";
            var zv = zoomView(p);
            zv.title = "Agrandir le zoom";
            zv.addEventListener("click", function () { openLightbox(photos, g.idx, "zoom"); });
            side.appendChild(zv);
            var info = el("div", "dt-zoom-info");
            info.appendChild(el("b", "", "Zoom sur la cible signalee"));
            info.appendChild(el("span", "", fmtTime(p.at) + (p.target_id ? " - cible " + p.target_id : "")));
            if (!p.rect) info.appendChild(el("span", "", "Pas de cadre fourni par la camera"));
            var pa = p.ai;
            // Qui dit quoi : la camera (cadre orange) contre l'IA (pointilles)
            info.appendChild(el("span", "", "La camera a signale : " + (BOX_LABEL[p.type] || "une cible")));
            if (pa) {
                var det = (pa.detections || []).filter(function (d) { return d.category !== "objet"; })[0];
                var real = pa.decision === "confirmed" || (pa.real_conf || 0) >= 0.3;
                var top = aiTop(pa);  // pourcentage le plus haut de l'IA, quel qu'il soit
                var concl = el("span", "dt-ai-concl " + (real ? "is-real" : "is-false"));
                concl.textContent = "L'IA voit surtout : " + (top ? (AI_NAME[top.label] || top.label).toLowerCase() + " (" + Math.round(top.p * 100) + " %)" : "rien") +
                    (real ? " - intrusion confirmee" : " - fausse alerte probable");
                info.appendChild(concl);
                info.appendChild(el("span", "", "Detecteur IA : " + (det ? (AI_NAME[det.category] || det.category) + " " + Math.round(det.conf * 100) + " %" : "ni personne ni vehicule")));
                if (pa.guess && pa.guess.length) {
                    var gl = el("div", "dt-ai-guess");
                    gl.appendChild(el("span", "", "Selon l'IA :"));
                    pa.guess.slice(0, 3).forEach(function (gs, i) {
                        if (i && gs.p < 0.03) return;
                        gl.appendChild(el("span", "chip" + (i ? "" : " is-top"), (AI_NAME[gs.label] || gs.label) + " " + Math.round(gs.p * 100) + " %"));
                    });
                    info.appendChild(gl);
                }
            }
            if (p.label) {
                var nb = { personne: 0, vehicule: 0 };
                p.label.boxes.forEach(function (b) { nb[b.category] = (nb[b.category] || 0) + 1; });
                var parts = [];
                if (nb.personne) parts.push(nb.personne + " personne" + (nb.personne > 1 ? "s" : ""));
                if (nb.vehicule) parts.push(nb.vehicule + " vehicule" + (nb.vehicule > 1 ? "s" : ""));
                info.appendChild(el("span", "dt-label-sum", "Verifie" + (p.label.by ? " par " + p.label.by : "") + " : " +
                    (parts.length ? parts.join(", ") : "rien sur cette photo")));
            }
            if (g.ep) {
                var ab = el("button", "dt-btn dt-annot-open"); ab.type = "button";
                ab.appendChild(icon("ads_click"));
                ab.appendChild(document.createTextNode(p.label ? "Corriger les cadres" : "Indiquer personne / vehicule"));
                ab.addEventListener("click", function () {
                    var i = g.idx;
                    openAnnotator(g.ep, i, function () { g.render(); refreshAi(g.ep); });
                });
                info.appendChild(ab);
            }
            side.appendChild(info);
            if (strip) Array.prototype.forEach.call(strip.children, function (b, i) { b.classList.toggle("is-current", i === g.idx); });
        };
        g.go = function (i) {
            g.idx = (i + photos.length) % photos.length;
            g.render();
            if (strip && strip.children[g.idx]) strip.children[g.idx].scrollIntoView({ block: "nearest", inline: "nearest" });
        };
        if (photos.length > 1) onSwipe(stage, function (d) { g.go(g.idx + d); });
        g.root.appendChild(stage);
        g.root.appendChild(side);
        if (strip) {
            photos.forEach(function (p, i) {
                var b = el("button"); b.type = "button"; b.title = fmtTime(p.at);
                var im = document.createElement("img"); im.src = p.thumb; im.alt = ""; im.loading = "lazy";
                b.appendChild(im);
                b.addEventListener("click", function () { g.go(i); });
                strip.appendChild(b);
            });
            g.root.appendChild(strip);
        }
        g.render();
        return g;
    }

    function openDetail(id) {
        S.openId = id;
        renderList();
        var box = $("dt-detail"), body = $("dt-detail-body"), foot = $("dt-detail-foot");
        box.hidden = false;
        syncSheet();
        body.textContent = "";
        foot.textContent = "";
        body.appendChild(el("div", "dt-skel"));
        getJSON(API.episodes + "/" + id).then(function (ep) {
            if (S.openId !== id) return;
            body.textContent = "";
            var title = $("dt-detail-title");
            title.textContent = "";
            ep.types.forEach(function (t) { var s = TYPE[t] || TYPE.other; title.appendChild(badge(s.cls, s.icon, s.label)); });
            title.appendChild(document.createTextNode(" " + ep.camera_name));

            gallery = null;
            if (ep.photos.length) {
                // Galerie : derniere photo affichee d'abord (la plus recente)
                gallery = buildGallery(ep.photos, ep.photos.length - 1, ep);
                body.appendChild(gallery.root);
            } else {
                body.appendChild(el("p", "dt-help", "Pas de photo : activez l'envoi de l'image dans les reglages de la detection sur la camera."));
            }

            var facts = el("div", "dt-facts");
            [[fmtTime(ep.started_at), (isToday(ep.started_at) ? "aujourd'hui" : fmtDay(ep.started_at))],
             [fmtDur(ep.duration_s), ep.active ? "en cours" : "duree"],
             [String(ep.targets || 1), "cible" + (ep.targets > 1 ? "s" : "") + " detectee" + (ep.targets > 1 ? "s" : "")]].forEach(function (f) {
                var d = el("div", "dt-fact"); d.appendChild(el("b", "", f[0])); d.appendChild(el("span", "", f[1])); facts.appendChild(d);
            });
            body.appendChild(facts);
            if (ep.ai) {
                var aiSec = el("div");
                aiSec.appendChild(el("h4", "dt-sec", "Verification IA"));
                aiSec.appendChild(aiPanel(ep));
                body.appendChild(aiSec);
            }

            var where = el("div");
            where.appendChild(el("h4", "dt-sec", "Ou"));
            where.appendChild(el("div", "", ep.camera_name + (ep.channel_name ? " - " + ep.channel_name : "") + " (canal " + ep.channel + ")"));
            var zr = el("div", "dt-card-meta");
            ep.zones.forEach(function (z) { var zc = el("span", "dt-zone-chip"); var i = el("i"); i.style.background = z.color; zc.appendChild(i); zc.appendChild(document.createTextNode(z.name)); zr.appendChild(zc); });
            if (ep.regions.length) zr.appendChild(el("span", "", "Zone camera " + ep.regions.join(", ")));
            where.appendChild(zr);
            body.appendChild(where);

            if (ep.messages && ep.messages.length) {
                var tl = el("div");
                tl.appendChild(el("h4", "dt-sec", "Detections"));
                var list = el("div", "dt-timeline");
                ep.messages.forEach(function (m) {
                    var r = el("div");
                    r.appendChild(el("time", "", fmtTime(m.at)));
                    r.appendChild(el("span", "", (TYPE[m.target] || TYPE.other).label + (m.region != null ? " - zone " + m.region : "") + (m.target_id ? " - cible " + m.target_id : "")));
                    list.appendChild(r);
                });
                tl.appendChild(list);
                body.appendChild(tl);
            }

            // Traitement
            var st = el("div", "dt-help");
            st.textContent = "Statut : " + STATUS[ep.status] + (ep.status_by ? " par " + ep.status_by + " " + ago(ep.status_at) : "");
            foot.appendChild(st);
            var note = document.createElement("textarea");
            note.className = "dt-note"; note.placeholder = "Note (facultative)"; note.value = ep.note || "";
            foot.appendChild(note);
            var acts = el("div", "dt-actions");
            [["seen", "visibility", "Marquer vue"], ["closed", "task_alt", "Cloturer"], ["new", "undo", "Remettre a traiter"]].forEach(function (a) {
                if (a[0] === ep.status) return;
                var b = el("button", "dt-btn" + (a[0] === "closed" ? " dt-btn-primary" : "")); b.type = "button";
                b.appendChild(icon(a[1])); b.appendChild(document.createTextNode(a[2]));
                b.addEventListener("click", function () {
                    send("POST", API.status, { ids: [ep.id], status: a[0], note: note.value }).then(function () {
                        toast("success", "Intrusion " + STATUS[a[0]].toLowerCase());
                        refreshAll();
                        // Cloturee : le traitement est fini, on ferme le volet
                        if (a[0] === "closed") closeDetail(); else openDetail(ep.id);
                    }).catch(function (e) { toast("error", e.message); });
                });
                acts.appendChild(b);
            });
            foot.appendChild(acts);
            // Centrer la carte sur la camera
            var ch = chanByKey(chanKey(ep));
            if (ch && ch.map) map.panTo([ch.map.lat, ch.map.lng]);
        }).catch(function (e) {
            body.textContent = "";
            body.appendChild(el("p", "dt-help", "Fiche indisponible (" + e.message + ")."));
        });
    }
    function closeDetail() { $("dt-detail").hidden = true; S.openId = null; gallery = null; syncSheet(); renderList(); }
    function closeLightbox() { $("dt-lightbox").hidden = true; syncSheet(); }

    // Visionneuse plein ecran : scene ou zoom, navigation entre les photos
    var lb = null;
    function openLightbox(photos, idx, mode) {
        lb = { photos: photos, idx: idx, mode: mode || "scene" };
        renderLightbox();
        $("dt-lightbox").hidden = false;
        syncSheet();
    }
    function renderLightbox() {
        var f = $("dt-lightbox-frame");
        f.textContent = "";
        var p = lb.photos[lb.idx];
        var v = lb.mode === "zoom" ? zoomView(p) : photoView(p);
        v.classList.add(lb.mode === "zoom" ? "dt-zoom-big" : "dt-photo-big");
        v.addEventListener("click", function (e) { e.stopPropagation(); });
        f.appendChild(v);
        var bar = el("div", "dt-lb-bar");
        bar.addEventListener("click", function (e) { e.stopPropagation(); });
        if (lb.photos.length > 1) {
            var pv = el("button", "", ""); pv.type = "button"; pv.appendChild(icon("chevron_left")); pv.title = "Photo precedente (fleche gauche)";
            pv.addEventListener("click", function () { lbGo(-1); });
            bar.appendChild(pv);
            bar.appendChild(el("span", "", (lb.idx + 1) + " / " + lb.photos.length));
            var nx = el("button", "", ""); nx.type = "button"; nx.appendChild(icon("chevron_right")); nx.title = "Photo suivante (fleche droite)";
            nx.addEventListener("click", function () { lbGo(1); });
            bar.appendChild(nx);
        }
        [["scene", "image", "Scene"], ["zoom", "zoom_in", "Zoom cible"]].forEach(function (m) {
            var b = el("button", lb.mode === m[0] ? "is-on" : ""); b.type = "button";
            b.appendChild(icon(m[1])); b.appendChild(document.createTextNode(m[2]));
            b.title = "Touche Z : basculer scene / zoom";
            b.addEventListener("click", function () { lb.mode = m[0]; renderLightbox(); });
            bar.appendChild(b);
        });
        f.appendChild(bar);
    }
    function lbGo(delta) {
        lb.idx = (lb.idx + delta + lb.photos.length) % lb.photos.length;
        renderLightbox();
        if (gallery) gallery.go(lb.idx);
    }

    /* ---------------- configuration de la carte ---------------- */
    function setEdit(on) {
        S.edit = on;
        $("dt-edit-btn").classList.toggle("active", on);
        $("dt-config").hidden = !on;
        if (on && isPhone()) setView("map");
        syncSheet();
        if (map) setTimeout(function () { map.invalidateSize(); }, 80);
        if (on) { closeDetail(); renderConfig(); } else { stopPlacing(); cancelDrawing(); S.editZone = null; }
        drawCams();
        drawZones();
    }

    function hint(text) {
        var h = document.querySelector(".dt-map-hint");
        if (!h) { h = el("div", "dt-map-hint"); document.querySelector(".dt-map-area").appendChild(h); }
        h.textContent = text || "";
        h.hidden = !text;
    }

    function renderConfig() {
        var body = $("dt-config-body");
        body.textContent = "";
        var s1 = el("div");
        s1.appendChild(el("h4", "dt-sec", "Cameras en detection"));
        if (!S.config.channels.length) s1.appendChild(el("p", "dt-help", "Aucun canal n'a l'usage Detection d'intrusion. Activez-le dans la page Cameras."));
        devices().forEach(function (ch) {
            var it = el("div", "dt-cfg-item" + (S.placing === ch.id ? " is-placing" : ""));
            it.appendChild(icon(ch.map ? "location_on" : "location_off"));
            var g = el("div", "grow");
            g.appendChild(el("b", "", ch.name));
            g.appendChild(el("span", "sub", (ch.channels.length > 1 ? ch.channels.length + " canaux : " : "") +
                ch.channels.map(function (x) { return x.channel_name; }).join(", ") + " - " + (ch.path || "sans chemin")));
            g.appendChild(el("span", "sub", ch.map_source === "geo" ? "Position : couche GPS des cameras (" + (ch.geo_name || "par IP") + ")"
                : ch.map_source === "manual" ? "Position : placee a la main" : "Aucune position (absente de la couche GPS)"));
            it.appendChild(g);
            var b = el("button", "dt-btn"); b.type = "button";
            b.textContent = S.placing === ch.id ? "Cliquez la carte" : ch.map ? "Deplacer" : "Placer";
            b.addEventListener("click", function () { startPlacing(ch); });
            it.appendChild(b);
            // Zones de detection de cette IP : un ou plusieurs polygones
            var mine = zonesOfDevice(ch);
            if (mine.length) g.appendChild(el("span", "sub", mine.length + " zone(s) : " + mine.map(function (z) { return z.name; }).join(", ")));
            var dz = el("button", "dt-icon-btn"); dz.type = "button"; dz.title = "Dessiner une zone de detection pour cette camera";
            dz.appendChild(icon("draw"));
            dz.addEventListener("click", function () {
                startDrawing({ _id: "", name: ch.name + " - zone " + (mine.length + 1), color: "#d97706", notes: "",
                    links: ch.channels.map(function (x) { return { camera_id: x.camera_id, channel: x.channel, regions: [] }; }) });
            });
            it.appendChild(dz);
            // Sur place, devant la camera : position GPS du telephone
            if (navigator.geolocation && window.isSecureContext) {
                var gps = el("button", "dt-icon-btn"); gps.type = "button"; gps.title = "Placer a ma position (GPS)";
                gps.appendChild(icon("my_location"));
                gps.addEventListener("click", function () {
                    toast("info", "Localisation en cours...");
                    navigator.geolocation.getCurrentPosition(function (pos) {
                        var acc = Math.round(pos.coords.accuracy);
                        if (acc > 50) toast("warning", "Precision faible (" + acc + " m) : ajustez ensuite en faisant glisser la camera");
                        savePosition(ch, pos.coords.latitude, pos.coords.longitude);
                        map.setView([pos.coords.latitude, pos.coords.longitude], 19);
                    }, function (err) {
                        toast("error", "Position indisponible : " + (err.code === 1 ? "autorisation refusee" : err.message));
                    }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
                });
                it.appendChild(gps);
            }
            if (ch.map_source === "manual") {
                var rm = el("button", "dt-icon-btn"); rm.type = "button"; rm.title = "Oublier la position manuelle (retour a la couche GPS si la camera y figure)"; rm.appendChild(icon("undo"));
                rm.addEventListener("click", function () { savePosition(ch, null, null); });
                it.appendChild(rm);
            }
            s1.appendChild(it);
        });
        body.appendChild(s1);
        if (S.config.channels.some(function (c) { return c.map; })) s1.appendChild(el("p", "dt-help", "Astuce : en mode configuration, les cameras placees se deplacent aussi par glisser-deposer."));

        // Ordre du volet : 1 groupes et horaires, 2 cameras sur la carte, 3 zones
        s1.querySelector(".dt-sec").textContent = "2. Placer les cameras sur la carte";
        var sg = el("div");
        sg.appendChild(el("h4", "dt-sec", "1. Groupes et horaires de detection"));
        sg.appendChild(el("p", "dt-help", "Un groupe rassemble des cameras et dit QUAND la detection est active (ex. la nuit). Hors de ces horaires, tout est ignore : ni photo, ni intrusion, ni alerte. Une camera dans aucun groupe est active en permanence."));
        S.config.groups.forEach(function (g) {
            var it = el("div", "dt-cfg-item");
            var sw = el("span"); sw.style.cssText = "width:14px;height:14px;border-radius:3px;flex:none;background:" + g.color;
            it.appendChild(sw);
            var gr = el("div", "grow");
            gr.appendChild(el("b", "", g.name));
            gr.appendChild(el("span", "sub", g.members.length + " camera(s) - " + g.state_label));
            it.appendChild(gr);
            it.appendChild(stateChip(g));
            var b = el("button", "dt-btn"); b.type = "button"; b.textContent = "Modifier";
            b.addEventListener("click", function () { openGroupForm(g); });
            it.appendChild(b);
            sg.appendChild(it);
        });
        {
            var addg = el("button", "dt-btn"); addg.type = "button"; addg.style.marginTop = "6px";
            addg.appendChild(icon("group_add")); addg.appendChild(document.createTextNode("Nouveau groupe"));
            addg.addEventListener("click", function () { openGroupForm(newGroupDraft()); });
            sg.appendChild(addg);
        }
        body.insertBefore(sg, body.firstChild);

        var s2 = el("div");
        s2.appendChild(el("h4", "dt-sec", "3. Zones (facultatif)"));
        s2.appendChild(el("p", "dt-help", "Dessinez un perimetre sur la carte et liez-le aux cameras qui le surveillent, pour filtrer les intrusions et les alertes par zone."));
        S.config.zones.forEach(function (z) {
            var it = el("div", "dt-cfg-item");
            var sw = el("span"); sw.style.cssText = "width:14px;height:14px;border-radius:3px;background:" + z.color;
            it.appendChild(sw);
            var g = el("div", "grow");
            g.appendChild(el("b", "", z.name));
            g.appendChild(el("span", "sub", z.links.length + " canal(aux) lie(s)"));
            it.appendChild(g);
            var b = el("button", "dt-btn"); b.type = "button"; b.textContent = "Modifier";
            b.addEventListener("click", function () { openZoneForm(z); });
            it.appendChild(b);
            s2.appendChild(it);
        });
        if (S.editZone) s2.appendChild(zoneForm(S.editZone));
        else {
            var add = el("button", "dt-btn"); add.type = "button"; add.style.marginTop = "6px";
            add.appendChild(icon("draw")); add.appendChild(document.createTextNode("Dessiner une zone"));
            add.addEventListener("click", function () { startDrawing(null); });
            s2.appendChild(add);
        }
        body.appendChild(s2);
    }

    function startPlacing(dv) {
        cancelDrawing();
        S.placing = dv.id;
        hint("Cliquez sur la carte a l'emplacement de " + dv.name + (dv.channels.length > 1 ? " (ses " + dv.channels.length + " canaux)" : ""));
        renderConfig();
    }
    function stopPlacing() { S.placing = null; hint(""); }

    function savePosition(dv, lat, lng) {
        send("PUT", API.cameras + dv.id + "/position", lat == null ? {} : { lat: lat, lng: lng }).then(function () {
            toast("success", lat == null ? "Position manuelle oubliee" : "Position enregistree");
            return loadConfig();
        }).catch(function (e) { toast("error", e.message); });
    }

    function startDrawing(zone) {
        stopPlacing();
        S.drawing = { zone: zone, points: [] };
        hint("Cliquez les sommets de la zone" + (zone && zone.name ? " (" + zone.name + ")" : "") + ", puis Terminer (au moins 3 points)");
        renderDrawing();
        var body = $("dt-config-body");
        var bar = el("div", "dt-actions");
        var fin = el("button", "dt-btn dt-btn-primary", "Terminer"); fin.type = "button";
        fin.addEventListener("click", finishDrawing);
        var undo = el("button", "dt-btn", "Annuler le dernier point"); undo.type = "button";
        undo.addEventListener("click", function () { S.drawing.points.pop(); renderDrawing(); });
        var cancel = el("button", "dt-btn", "Abandonner"); cancel.type = "button";
        cancel.addEventListener("click", function () { cancelDrawing(); renderConfig(); });
        bar.appendChild(fin); bar.appendChild(undo); bar.appendChild(cancel);
        body.insertBefore(bar, body.firstChild);
    }
    function renderDrawing() {
        layers.draw.clearLayers();
        if (!S.drawing) return;
        var pts = S.drawing.points;
        pts.forEach(function (p) { L.circleMarker(p, { radius: 5, color: "#f59e0b", fillOpacity: 1 }).addTo(layers.draw); });
        if (pts.length > 1) L.polyline(pts.concat(pts.length > 2 ? [pts[0]] : []), { color: "#f59e0b", dashArray: "6 4" }).addTo(layers.draw);
    }
    function cancelDrawing() { S.drawing = null; layers.draw && layers.draw.clearLayers(); hint(""); }
    function finishDrawing() {
        if (!S.drawing || S.drawing.points.length < 3) { toast("warning", "Au moins 3 points"); return; }
        var z = S.drawing.zone || { _id: "", name: "", color: "#d97706", links: [], notes: "" };
        z = JSON.parse(JSON.stringify(z));
        z.polygon = S.drawing.points.map(function (p) { return [p.lat, p.lng]; });
        cancelDrawing();
        S.editZone = z;
        renderConfig();
    }

    /* ---------------- groupe et planning (fenetre dediee) ---------------- */
    // Planning = semaines types (7 jours x 96 quarts d'heure), exceptions
    // datees ou liees aux epreuves, regle des jours feries ; priorite :
    // forcage > exception > ferie > semaine type (detection_arming.py).
    var WEEK_COLORS = ["#2563eb", "#7c3aed", "#0d9488", "#db2777", "#ca8a04", "#4b5563"];
    var EMPTY_DAY = new Array(97).join("0");
    var FULL_DAY = new Array(97).join("1");
    var DAY_FULL = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
    var plan = null;  // {g, a, week, tab, month}

    function dayString(fromH, toH) {
        var s = "";
        for (var q = 0; q < 96; q++) s += (q >= fromH * 4 && q < toH * 4) ? "1" : "0";
        return s;
    }
    function orStr(a, b) { var s = ""; for (var i = 0; i < 96; i++) s += (a[i] === "1" || b[i] === "1") ? "1" : "0"; return s; }
    var WEEK_PRESETS = [
        ["Toutes les nuits 22h - 6h", function () { var d = orStr(dayString(0, 6), dayString(22, 24)); return [d, d, d, d, d, d, d]; }],
        ["Hors heures de bureau", function () { var d = orStr(dayString(0, 7), dayString(19, 24)); return [d, d, d, d, d, FULL_DAY, FULL_DAY]; }],
        ["Week-end entier", function () { return [EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, FULL_DAY, FULL_DAY]; }],
        ["Tout actif", function () { return [FULL_DAY, FULL_DAY, FULL_DAY, FULL_DAY, FULL_DAY, FULL_DAY, FULL_DAY]; }],
        ["Tout effacer", function () { return [EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY]; }],
    ];

    function newGroupDraft() {
        var d = orStr(dayString(0, 6), dayString(22, 24));
        return { _id: "", name: "", color: "#2563eb", members: [],
            arming: { mode: "schedule", weeks: [{ id: "w1", name: "Semaine normale", grid: [d, d, d, d, d, d, d] }],
                default_week: "w1", exceptions: [], holidays: "normal", override: null } };
    }
    function weekColor(id) {
        var i = plan.a.weeks.map(function (w) { return w.id; }).indexOf(id);
        return WEEK_COLORS[(i < 0 ? 0 : i) % WEEK_COLORS.length];
    }
    function currentWeek() { return plan.a.weeks.filter(function (w) { return w.id === plan.week; })[0] || plan.a.weeks[0]; }
    function planDirty() { $("dt-plan-msg").textContent = "Modifications non enregistrees"; }

    function openGroupForm(g) { openPlan(g); }
    function openPlan(g) {
        var copy = JSON.parse(JSON.stringify(g));
        var a = copy.arming || {};
        if (!a.weeks || !a.weeks.length) a = newGroupDraft().arming;
        a.exceptions = a.exceptions || [];
        a.holidays = a.holidays || "normal";
        copy.arming = a;
        var today = new Date().toLocaleDateString("fr-CA", { timeZone: TZ }).slice(0, 7);
        plan = { g: copy, a: a, week: a.default_week || a.weeks[0].id, tab: "weeks", month: today };
        $("dt-plan-title").textContent = copy._id ? copy.name : "Nouveau groupe";
        $("dt-plan-state").textContent = "";
        if (copy._id) $("dt-plan-state").appendChild(stateChip(g));
        $("dt-plan-delete").hidden = !copy._id;
        $("dt-plan-msg").textContent = "";
        renderPlanSide();
        setPlanTab("weeks");
        var dlg = $("dt-plan");
        if (!dlg.open) dlg.showModal();
    }

    function setPlanTab(t) {
        plan.tab = t;
        Array.prototype.forEach.call(document.querySelectorAll("#dt-plan-tabs button"), function (b) { b.classList.toggle("active", b.dataset.tab === t); });
        var pane = $("dt-plan-pane");
        pane.textContent = "";
        if (t === "weeks") renderWeeks(pane);
        else if (t === "exceptions") renderExceptions(pane);
        else renderCalendar(pane);
    }

    /* Colonne de gauche : identite, cameras, mode, forcage */
    function renderPlanSide() {
        var side = $("dt-plan-side"), g = plan.g, a = plan.a;
        side.textContent = "";
        var l1 = el("label", "dt-field", "Nom du groupe");
        var n = document.createElement("input"); n.type = "text"; n.value = g.name; n.maxLength = 60; n.placeholder = "Ex. Portes enceinte";
        n.addEventListener("input", function () { g.name = n.value; planDirty(); });
        l1.appendChild(n); side.appendChild(l1);
        var l2 = el("label", "dt-field dt-field-inline", "Couleur");
        var c = document.createElement("input"); c.type = "color"; c.value = g.color || "#2563eb";
        c.addEventListener("input", function () { g.color = c.value; planDirty(); });
        l2.appendChild(c); side.appendChild(l2);

        side.appendChild(el("div", "dt-sec", "Cameras du groupe"));
        var keys = (g.members || []).map(function (m) { return m.key || (m.camera_id + ":" + m.channel); });
        if (!S.config.channels.length) side.appendChild(el("p", "dt-help", "Aucune camera en detection : activez l'usage Detection d'intrusion sur un canal (page Cameras)."));
        S.config.channels.forEach(function (ch) {
            var r = el("label", "dt-link-row");
            var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = keys.indexOf(ch.key) >= 0;
            cb.addEventListener("change", function () {
                g.members = (g.members || []).filter(function (m) { return (m.key || (m.camera_id + ":" + m.channel)) !== ch.key; });
                if (cb.checked) g.members.push({ camera_id: ch.camera_id, channel: ch.channel, key: ch.key });
                planDirty();
            });
            r.appendChild(cb); r.appendChild(el("span", "", ch.camera_name + " - " + ch.channel_name));
            side.appendChild(r);
        });

        side.appendChild(el("div", "dt-sec", "Mode"));
        var modes = el("div", "dt-mode dt-mode-col");
        [["schedule", "Selon le planning", "semaines types et exceptions ci-contre"], ["always", "Tout le temps", "detection permanente"], ["off", "En pause", "tout est ignore"]].forEach(function (m) {
            var l = el("label"); var r = document.createElement("input");
            r.type = "radio"; r.name = "dt-plan-mode"; r.value = m[0]; r.checked = a.mode === m[0];
            r.addEventListener("change", function () { a.mode = m[0]; planDirty(); if (plan.tab === "calendar") setPlanTab("calendar"); });
            l.appendChild(r); l.appendChild(document.createTextNode(m[1])); l.appendChild(el("em", "", m[2]));
            modes.appendChild(l);
        });
        side.appendChild(modes);

        side.appendChild(el("div", "dt-sec", "Priorites"));
        side.appendChild(el("p", "dt-help", "1. Forcage temporaire  2. Exception datee (la premiere de la liste)  3. Jour ferie  4. Semaine type par defaut."));

        if (g._id) {
            side.appendChild(el("div", "dt-sec", "Exception immediate (forcage)"));
            var ov = el("div", "dt-override");
            var dur = document.createElement("select");
            [["1", "1 h"], ["4", "4 h"], ["12", "12 h"], ["24", "24 h"], ["72", "3 jours"]].forEach(function (o) { dur.appendChild(new Option(o[1], o[0])); });
            function doForce(state) {
                send("POST", API.groups + "/" + g._id + "/override", { state: state, hours: +dur.value }).then(function (r) {
                    toast("success", r.state_label);
                    $("dt-plan-state").textContent = ""; $("dt-plan-state").appendChild(stateChip(r));
                    loadConfig();
                }).catch(function (e) { toast("error", e.message); });
            }
            var on = el("button", "dt-btn", "Activer"); on.type = "button"; on.addEventListener("click", function () { doForce("on"); });
            var off = el("button", "dt-btn", "Desactiver"); off.type = "button"; off.addEventListener("click", function () { doForce("off"); });
            ov.appendChild(on); ov.appendChild(off); ov.appendChild(el("span", "dt-help", "pendant")); ov.appendChild(dur);
            var cancel = el("button", "dt-link-btn", "Revenir au planning"); cancel.type = "button";
            cancel.addEventListener("click", function () { doForce(null); });
            ov.appendChild(cancel);
            side.appendChild(ov);
        }
    }

    /* Onglet Semaines types : grille a peindre */
    function renderWeeks(pane) {
        var a = plan.a;
        var bar = el("div", "dt-weeks-bar");
        a.weeks.forEach(function (w) {
            var b = el("button", "dt-week-chip" + (w.id === plan.week ? " is-on" : "")); b.type = "button";
            var sw = el("i"); sw.style.background = weekColor(w.id); b.appendChild(sw);
            b.appendChild(document.createTextNode(w.name));
            if (w.id === a.default_week) { var st = icon("star"); st.title = "Semaine par defaut"; b.appendChild(st); }
            b.addEventListener("click", function () { plan.week = w.id; setPlanTab("weeks"); });
            bar.appendChild(b);
        });
        var add = el("button", "dt-week-chip dt-week-add"); add.type = "button";
        add.appendChild(icon("add")); add.appendChild(document.createTextNode("Nouvelle semaine type"));
        add.title = "Copie de la semaine affichee (ex. semaine d'epreuve, fermeture estivale)";
        add.addEventListener("click", function () {
            if (a.weeks.length >= 10) { toast("warning", "10 semaines types au maximum"); return; }
            var cur = currentWeek(), id = "w" + Date.now().toString(36);
            a.weeks.push({ id: id, name: "Semaine " + (a.weeks.length + 1), grid: cur.grid.slice() });
            plan.week = id; planDirty(); setPlanTab("weeks");
        });
        bar.appendChild(add);
        pane.appendChild(bar);

        var w = currentWeek();
        var tools = el("div", "dt-week-tools");
        var nm = document.createElement("input"); nm.type = "text"; nm.value = w.name; nm.maxLength = 40; nm.title = "Nom de la semaine type";
        nm.addEventListener("input", function () { w.name = nm.value || "Semaine"; planDirty(); });
        nm.addEventListener("change", function () { setPlanTab("weeks"); });
        tools.appendChild(nm);
        if (w.id !== a.default_week) {
            var def = el("button", "dt-link-btn", "Definir par defaut"); def.type = "button";
            def.addEventListener("click", function () { a.default_week = w.id; planDirty(); setPlanTab("weeks"); });
            tools.appendChild(def);
            var del = el("button", "dt-link-btn dt-danger-link", "Supprimer"); del.type = "button";
            del.addEventListener("click", function () {
                if (a.exceptions.some(function (x) { return x.action === "week" && x.week_id === w.id; })) {
                    toast("warning", "Semaine utilisee par une exception : modifiez l'exception d'abord"); return;
                }
                a.weeks = a.weeks.filter(function (x) { return x.id !== w.id; });
                plan.week = a.default_week; planDirty(); setPlanTab("weeks");
            });
            tools.appendChild(del);
        } else {
            tools.appendChild(el("span", "dt-help", "Semaine appliquee par defaut, hors exceptions"));
        }
        pane.appendChild(tools);

        var pre = el("div", "dt-presets");
        pre.appendChild(el("span", "", "Remplir :"));
        WEEK_PRESETS.forEach(function (p) {
            var b = el("button", "dt-preset", p[0]); b.type = "button";
            b.addEventListener("click", function () { w.grid = p[1](); planDirty(); setPlanTab("weeks"); });
            pre.appendChild(b);
        });
        var cp = document.createElement("select"); cp.className = "dt-copy-day";
        cp.appendChild(new Option("Copier un jour...", ""));
        DAY_FULL.forEach(function (d, i) {
            cp.appendChild(new Option("Copier " + d.toLowerCase() + " sur les jours ouvres", i + ":wk"));
            cp.appendChild(new Option("Copier " + d.toLowerCase() + " sur toute la semaine", i + ":all"));
        });
        cp.addEventListener("change", function () {
            if (!cp.value) return;
            var p = cp.value.split(":"), src = w.grid[+p[0]];
            for (var i = 0; i < 7; i++) if (p[1] === "all" || i < 5) w.grid[i] = src;
            planDirty(); setPlanTab("weeks");
        });
        pre.appendChild(cp);
        pane.appendChild(pre);
        pane.appendChild(hoursForm(w));
        pane.appendChild(gridEditor(w));
        var lg = el("div", "dt-legend dt-grid-legend");
        var on = el("span"), oi = el("i"); oi.style.background = weekColor(w.id); on.appendChild(oi);
        on.appendChild(document.createTextNode("Actif : detection en marche (photos, intrusions, alertes)"));
        var off = el("span"), fi = el("i"); fi.style.background = "#e2e8f0"; fi.style.border = "1px solid #cbd5e1"; off.appendChild(fi);
        off.appendChild(document.createTextNode("Inactif : tout est ignore"));
        lg.appendChild(on); lg.appendChild(off);
        pane.appendChild(lg);
        pane.appendChild(el("p", "dt-help", "Cliquez-glissez pour peindre les plages actives (au quart d'heure), sur une ou plusieurs lignes. Commencer sur une case active efface. Minuit est entre la fin d'une ligne et le debut de la suivante."));
        pane.appendChild(weekSummary(w));
    }

    // Saisie d'horaires : "de 22:00 a 06:00 tous les jours" (passage de minuit
    // = fin de la plage sur le jour suivant, comme dans la grille).
    function hoursForm(w) {
        var f = el("div", "dt-hours");
        f.appendChild(el("span", "dt-hours-lbl", "Actif de"));
        var t1 = document.createElement("input"); t1.type = "time"; t1.step = 900; t1.value = "22:00";
        var t2 = document.createElement("input"); t2.type = "time"; t2.step = 900; t2.value = "06:00";
        f.appendChild(t1); f.appendChild(el("span", "", "a")); f.appendChild(t2);
        var days = document.createElement("select");
        [["all", "tous les jours"], ["wk", "du lundi au vendredi"], ["we", "samedi et dimanche"]].forEach(function (o) { days.appendChild(new Option(o[1], o[0])); });
        DAY_FULL.forEach(function (d, i) { days.appendChild(new Option("le " + d.toLowerCase(), String(i))); });
        f.appendChild(days);
        function q(v) { var p = (v || "").split(":"); return p.length < 2 ? null : Math.floor((+p[0] * 60 + +p[1]) / 15); }
        function apply(replace) {
            var a = q(t1.value), b = q(t2.value);
            if (a === null || b === null) { toast("warning", "Saisissez les deux heures"); return; }
            if (b === 0) b = 96;  // "a 00:00" = jusqu'a minuit
            if (a === b) { toast("warning", "Heure de debut et de fin identiques : utilisez le modele Tout actif"); return; }
            var sel = days.value === "all" ? [0, 1, 2, 3, 4, 5, 6] : days.value === "wk" ? [0, 1, 2, 3, 4] : days.value === "we" ? [5, 6] : [+days.value];
            var g = w.grid.map(function (s) { return s.split(""); });
            if (replace) sel.forEach(function (d) { for (var i = 0; i < 96; i++) g[d][i] = "0"; });
            sel.forEach(function (d) {
                if (a < b) { for (var i = a; i < b; i++) g[d][i] = "1"; }
                else {  // passe minuit : fin sur le jour suivant
                    for (var j = a; j < 96; j++) g[d][j] = "1";
                    for (var k = 0; k < b; k++) g[(d + 1) % 7][k] = "1";
                }
            });
            w.grid = g.map(function (r) { return r.join(""); });
            planDirty(); setPlanTab("weeks");
        }
        var rep = el("button", "dt-btn dt-btn-primary", "Remplacer"); rep.type = "button";
        rep.title = "Efface les jours choisis puis applique ces horaires";
        rep.addEventListener("click", function () { apply(true); });
        var add = el("button", "dt-btn", "Ajouter"); add.type = "button";
        add.title = "Ajoute cette plage aux horaires deja actifs";
        add.addEventListener("click", function () { apply(false); });
        f.appendChild(rep); f.appendChild(add);
        return f;
    }

    function gridEditor(w) {
        var color = weekColor(w.id);
        var wrap = el("div", "dt-grid");
        wrap.style.setProperty("--wc", color);
        var head = el("div", "dt-grid-row dt-grid-head");
        head.appendChild(el("span", "dt-grid-day", ""));
        var hours = el("div", "dt-grid-hours");
        for (var h = 0; h < 24; h++) hours.appendChild(el("span", "", (h < 10 ? "0" : "") + h));
        head.appendChild(hours);
        wrap.appendChild(head);
        var painting = null;  // {value, rows:{}}
        var cellsByRow = [];
        DAY_FULL.forEach(function (dn, d) {
            var row = el("div", "dt-grid-row");
            row.appendChild(el("span", "dt-grid-day", dn.slice(0, 3)));
            var cells = el("div", "dt-grid-cells");
            cellsByRow[d] = [];
            for (var q = 0; q < 96; q++) {
                var c = el("i", w.grid[d][q] === "1" ? "on" : "");
                if (q % 4 === 0) c.classList.add("hh");
                c.dataset.d = d; c.dataset.q = q;
                cells.appendChild(c);
                cellsByRow[d].push(c);
            }
            row.appendChild(cells);
            wrap.appendChild(row);
        });
        var start = null;
        function apply(target) {
            // Rectangle entre la case de depart et la case courante
            var d0 = Math.min(start.d, target.d), d1 = Math.max(start.d, target.d);
            var q0 = Math.min(start.q, target.q), q1 = Math.max(start.q, target.q);
            for (var d = 0; d < 7; d++) {
                var row = w.grid[d].split("");
                for (var q = 0; q < 96; q++) {
                    var inRect = d >= d0 && d <= d1 && q >= q0 && q <= q1;
                    var v = inRect ? painting.value : painting.base[d][q];
                    row[q] = v;
                    cellsByRow[d][q].classList.toggle("on", v === "1");
                }
                w.grid[d] = row.join("");
            }
        }
        function cellAt(e) {
            var t = document.elementFromPoint(e.clientX, e.clientY);
            return t && t.dataset && t.dataset.q !== undefined ? { d: +t.dataset.d, q: +t.dataset.q } : null;
        }
        wrap.addEventListener("pointerdown", function (e) {
            var c = cellAt(e);
            if (!c) return;
            e.preventDefault();
            start = c;
            painting = { value: w.grid[c.d][c.q] === "1" ? "0" : "1", base: w.grid.slice() };
            apply(c);
            wrap.setPointerCapture(e.pointerId);
        });
        wrap.addEventListener("pointermove", function (e) {
            if (!painting) return;
            var c = cellAt(e);
            if (c) apply(c);
        });
        function end() {
            if (!painting) return;
            painting = null; start = null; planDirty();
            var s = wrap.parentNode && wrap.parentNode.querySelector(".dt-summary");
            if (s) s.replaceWith(weekSummary(w));
        }
        wrap.addEventListener("pointerup", end);
        wrap.addEventListener("pointercancel", end);
        return wrap;
    }

    function spansOf(dayStr) {
        var out = [], s = null;
        for (var q = 0; q <= 96; q++) {
            var on = q < 96 && dayStr[q] === "1";
            if (on && s === null) s = q;
            if (!on && s !== null) { out.push(fmtQ(s) + "-" + fmtQ(q)); s = null; }
        }
        return out;
    }
    function fmtQ(q) { var m = q * 15; return (m === 1440 ? "24" : ("0" + Math.floor(m / 60)).slice(-2)) + ":" + ("0" + (m % 60)).slice(-2); }
    function weekSummary(w) {
        var box = el("div", "dt-summary");
        var total = w.grid.join("").split("1").length - 1;
        box.appendChild(el("b", "", w.name + " : " + Math.round(total / 4) + " h actives par semaine"));
        w.grid.forEach(function (d, i) {
            var sp = spansOf(d);
            box.appendChild(el("div", "", DAY_FULL[i] + " : " + (sp.length ? sp.join(", ") : "inactif")));
        });
        return box;
    }

    /* Onglet Exceptions */
    function renderExceptions(pane) {
        var a = plan.a;
        pane.appendChild(el("p", "dt-help", "Une exception remplace la semaine type sur une periode. La premiere de la liste qui couvre un jour l'emporte (fleches pour changer l'ordre)."));
        var list = el("div", "dt-exc-list");
        if (!a.exceptions.length) list.appendChild(el("div", "dt-empty-small", "Aucune exception."));
        a.exceptions.forEach(function (x, i) { list.appendChild(exceptionRow(x, i)); });
        pane.appendChild(list);
        var adds = el("div", "dt-actions");
        var ad = el("button", "dt-btn"); ad.type = "button"; ad.appendChild(icon("date_range")); ad.appendChild(document.createTextNode("Exception par dates"));
        ad.addEventListener("click", function () {
            var t = new Date().toLocaleDateString("fr-CA", { timeZone: TZ });
            a.exceptions.push({ id: "x" + Date.now().toString(36), label: "Nouvelle exception", kind: "dates", start: t, end: t, yearly: false, action: "on" });
            planDirty(); setPlanTab("exceptions");
        });
        var ae = el("button", "dt-btn"); ae.type = "button"; ae.appendChild(icon("sports_motorsports")); ae.appendChild(document.createTextNode("Exception liee a une epreuve"));
        ae.disabled = !(S.config.events || []).length;
        ae.title = ae.disabled ? "Aucune epreuve dans le parametrage" : "Suit automatiquement les dates de l'epreuve, chaque annee";
        ae.addEventListener("click", function () {
            a.exceptions.push({ id: "x" + Date.now().toString(36), label: "Pendant l'epreuve", kind: "event", event: (S.config.events || [])[0], span: "window", action: a.weeks.length > 1 ? "week" : "on", week_id: a.weeks[a.weeks.length - 1].id });
            planDirty(); setPlanTab("exceptions");
        });
        adds.appendChild(ad); adds.appendChild(ae);
        pane.appendChild(adds);

        pane.appendChild(el("div", "dt-sec", "Jours feries"));
        var hs = document.createElement("select"); hs.className = "dt-select";
        [["normal", "Rien de special (semaine type)"], ["sunday", "Comme un dimanche"], ["on", "Toujours actif"], ["off", "En pause"]].forEach(function (o) { hs.appendChild(new Option(o[1], o[0])); });
        hs.value = a.holidays;
        hs.addEventListener("change", function () { a.holidays = hs.value; planDirty(); });
        pane.appendChild(hs);
        pane.appendChild(el("p", "dt-help", "Jours feries francais calcules automatiquement (Paques, Ascension, Pentecote compris)."));
    }

    function exceptionRow(x, i) {
        var a = plan.a;
        var r = el("div", "dt-exc");
        var top = el("div", "dt-exc-top");
        var lb = document.createElement("input"); lb.type = "text"; lb.value = x.label; lb.maxLength = 60; lb.placeholder = "Libelle";
        lb.addEventListener("input", function () { x.label = lb.value; planDirty(); });
        top.appendChild(lb);
        var up = el("button", "dt-icon-btn"); up.type = "button"; up.title = "Plus prioritaire"; up.appendChild(icon("arrow_upward")); up.disabled = i === 0;
        up.addEventListener("click", function () { a.exceptions.splice(i - 1, 0, a.exceptions.splice(i, 1)[0]); planDirty(); setPlanTab("exceptions"); });
        var dn = el("button", "dt-icon-btn"); dn.type = "button"; dn.title = "Moins prioritaire"; dn.appendChild(icon("arrow_downward")); dn.disabled = i === a.exceptions.length - 1;
        dn.addEventListener("click", function () { a.exceptions.splice(i + 1, 0, a.exceptions.splice(i, 1)[0]); planDirty(); setPlanTab("exceptions"); });
        var rm = el("button", "dt-icon-btn"); rm.type = "button"; rm.title = "Supprimer"; rm.appendChild(icon("delete"));
        rm.addEventListener("click", function () { a.exceptions.splice(i, 1); planDirty(); setPlanTab("exceptions"); });
        top.appendChild(up); top.appendChild(dn); top.appendChild(rm);
        r.appendChild(top);

        var when = el("div", "dt-exc-when");
        if (x.kind === "event") {
            when.appendChild(el("span", "", "Pendant"));
            var es = document.createElement("select");
            (S.config.events || []).forEach(function (e) { es.appendChild(new Option(e, e)); });
            if (x.event && (S.config.events || []).indexOf(x.event) < 0) es.appendChild(new Option(x.event + " (introuvable)", x.event));
            es.value = x.event;
            es.addEventListener("change", function () { x.event = es.value; planDirty(); });
            when.appendChild(es);
            var sp = document.createElement("select");
            sp.appendChild(new Option("du montage au demontage", "window"));
            sp.appendChild(new Option("les jours publics", "public"));
            sp.value = x.span || "window";
            sp.addEventListener("change", function () { x.span = sp.value; planDirty(); });
            when.appendChild(sp);
            when.appendChild(el("span", "dt-help", "chaque annee, selon le parametrage"));
        } else {
            when.appendChild(el("span", "", "Du"));
            var d1 = document.createElement("input"); d1.type = "date"; d1.value = x.start;
            d1.addEventListener("change", function () { x.start = d1.value; if (!x.end || x.end < x.start) { x.end = x.start; d2.value = x.end; } planDirty(); });
            var d2 = document.createElement("input"); d2.type = "date"; d2.value = x.end;
            d2.addEventListener("change", function () { x.end = d2.value; planDirty(); });
            when.appendChild(d1); when.appendChild(el("span", "", "au")); when.appendChild(d2);
            var yl = el("label", "dt-link-row"); var yc = document.createElement("input"); yc.type = "checkbox"; yc.checked = !!x.yearly;
            yc.addEventListener("change", function () { x.yearly = yc.checked; planDirty(); });
            yl.appendChild(yc); yl.appendChild(el("span", "", "chaque annee"));
            when.appendChild(yl);
        }
        r.appendChild(when);

        var act = el("div", "dt-exc-when");
        act.appendChild(el("span", "", "Appliquer"));
        var as = document.createElement("select");
        a.weeks.forEach(function (w) { as.appendChild(new Option("la semaine type " + w.name, "week:" + w.id)); });
        as.appendChild(new Option("toujours actif", "on"));
        as.appendChild(new Option("en pause (tout ignore)", "off"));
        as.value = x.action === "week" ? "week:" + x.week_id : x.action;
        as.addEventListener("change", function () {
            if (as.value.indexOf("week:") === 0) { x.action = "week"; x.week_id = as.value.slice(5); }
            else { x.action = as.value; delete x.week_id; }
            planDirty();
        });
        act.appendChild(as);
        r.appendChild(act);
        return r;
    }

    /* Onglet Calendrier : apercu du planning en cours d'edition */
    function planPayload() {
        var g = plan.g;
        return {
            name: (g.name || "").trim(), color: g.color,
            members: (g.members || []).map(function (m) { return { camera_id: m.camera_id, channel: m.channel }; }),
            arming: { mode: plan.a.mode, weeks: plan.a.weeks, default_week: plan.a.default_week,
                exceptions: plan.a.exceptions, holidays: plan.a.holidays },
        };
    }
    function renderCalendar(pane) {
        var nav = el("div", "dt-cal-nav");
        var prev = el("button", "dt-icon-btn"); prev.type = "button"; prev.appendChild(icon("chevron_left"));
        var next = el("button", "dt-icon-btn"); next.type = "button"; next.appendChild(icon("chevron_right"));
        var y = +plan.month.slice(0, 4), m = +plan.month.slice(5, 7);
        var title = el("b", "", new Date(y, m - 1, 15).toLocaleDateString("fr-FR", { month: "long", year: "numeric" }));
        function shift(n) { var d = new Date(y, m - 1 + n, 15); plan.month = d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2); setPlanTab("calendar"); }
        prev.addEventListener("click", function () { shift(-1); });
        next.addEventListener("click", function () { shift(1); });
        nav.appendChild(prev); nav.appendChild(title); nav.appendChild(next);
        pane.appendChild(nav);
        var box = el("div", "dt-cal");
        box.appendChild(el("div", "dt-skel"));
        pane.appendChild(box);
        var side = el("div", "dt-cal-changes");
        pane.appendChild(side);
        send("POST", API.groups + "/preview?month=" + plan.month, planPayload()).then(function (r) {
            box.textContent = "";
            ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."].forEach(function (d) { box.appendChild(el("span", "dt-cal-dow", d)); });
            var first = new Date(r.days[0].date + "T12:00:00");
            for (var k = 0; k < (first.getDay() + 6) % 7; k++) box.appendChild(el("span", "dt-cal-pad"));
            var todayKey = new Date().toLocaleDateString("fr-CA", { timeZone: TZ });
            r.days.forEach(function (d) {
                var c = el("div", "dt-cal-day src-" + d.source + (d.date === todayKey ? " is-today" : ""));
                var col = d.source === "exception" ? "#f97316" : d.source === "holiday" ? "#dc2626" : d.kind === "off" ? "#94a3b8" : d.week_id ? weekColor(d.week_id) : "#16a34a";
                c.style.setProperty("--dc", col);
                c.appendChild(el("b", "", String(+d.date.slice(8))));
                c.appendChild(el("span", "dt-cal-lbl", d.holiday && d.source !== "holiday" ? d.label + " (" + d.holiday + ")" : d.label));
                var bar = el("div", "dt-cal-bar");
                d.spans.forEach(function (s) {
                    var a0 = (+s[0].slice(0, 2) * 60 + +s[0].slice(3)) / 1440, a1 = (+s[1].slice(0, 2) * 60 + +s[1].slice(3)) / 1440;
                    var seg = el("i"); seg.style.left = (a0 * 100) + "%"; seg.style.width = ((a1 - a0) * 100) + "%";
                    bar.appendChild(seg);
                });
                c.appendChild(bar);
                c.title = d.date.split("-").reverse().join("/") + " - " + d.label + "\n" + (d.spans.length ? d.spans.map(function (s) { return s[0] + "-" + s[1]; }).join(", ") : "inactif toute la journee");
                box.appendChild(c);
            });
            side.textContent = "";
            side.appendChild(el("div", "dt-sec", "Prochains changements (14 jours)"));
            if (!r.changes.length) side.appendChild(el("p", "dt-help", "Aucun changement : " + (plan.a.mode === "always" ? "toujours actif." : plan.a.mode === "off" ? "en pause." : "etat constant.")));
            r.changes.forEach(function (ch) {
                var it = el("div", "dt-cal-change");
                it.appendChild(el("span", "dt-gstate " + (ch.active ? "is-on" : "is-off"), ch.active ? "Actif" : "Inactif"));
                it.appendChild(el("span", "", "a partir du " + fmtDay(ch.at) + " " + fmtHM(ch.at)));
                side.appendChild(it);
            });
            var lg = el("div", "dt-legend");
            plan.a.weeks.forEach(function (w) { var s = el("span"); var i = el("i"); i.style.background = weekColor(w.id); s.appendChild(i); s.appendChild(document.createTextNode(w.name)); lg.appendChild(s); });
            [["#f97316", "Exception"], ["#dc2626", "Jour ferie"], ["#94a3b8", "En pause"]].forEach(function (x) { var s = el("span"); var i = el("i"); i.style.background = x[0]; s.appendChild(i); s.appendChild(document.createTextNode(x[1])); lg.appendChild(s); });
            side.appendChild(lg);
        }).catch(function (e) {
            box.textContent = "";
            box.appendChild(el("p", "dt-help", "Apercu impossible : " + e.message));
        });
    }

    function savePlan() {
        var g = plan.g, body = planPayload();
        if (!body.name) { toast("warning", "Donnez un nom au groupe"); return; }
        var btn = $("dt-plan-save"); btn.disabled = true;
        (g._id ? send("PUT", API.groups + "/" + g._id, body) : send("POST", API.groups, body)).then(function () {
            toast("success", "Groupe enregistre");
            $("dt-plan").close();
            return loadConfig().then(function () {
                if (S.edit) renderConfig(); refreshAll();
                document.dispatchEvent(new CustomEvent("ia:groups-changed"));
            });
        }).catch(function (e) { toast("error", e.message); }).then(function () { btn.disabled = false; });
    }

    function bindPlan() {
        Array.prototype.forEach.call(document.querySelectorAll("#dt-plan-tabs button"), function (b) {
            b.addEventListener("click", function () { setPlanTab(b.dataset.tab); });
        });
        $("dt-plan-save").addEventListener("click", savePlan);
        function closePlan() {
            if ($("dt-plan-msg").textContent) {
                showConfirmToast("Fermer sans enregistrer les modifications ?", { okLabel: "Fermer", type: "warning" }).then(function (ok) { if (ok) $("dt-plan").close(); });
            } else $("dt-plan").close();
        }
        $("dt-plan-cancel").addEventListener("click", closePlan);
        $("dt-plan-close").addEventListener("click", closePlan);
        $("dt-plan").addEventListener("cancel", function (e) { e.preventDefault(); closePlan(); });
        $("dt-plan-delete").addEventListener("click", function () {
            var g = plan.g;
            showConfirmToast("Supprimer le groupe " + g.name + " ? Ses cameras redeviennent actives en permanence si elles ne sont dans aucun autre groupe.", { okLabel: "Supprimer", type: "warning" }).then(function (ok) {
                if (!ok) return;
                send("DELETE", API.groups + "/" + g._id).then(function () {
                    toast("success", "Groupe supprime");
                    $("dt-plan").close();
                    return loadConfig().then(function () { if (S.edit) renderConfig(); });
                }).catch(function (e) { toast("error", e.message); });
            });
        });
    }

    function openZoneForm(z) {
        if (!S.edit) setEdit(true);
        S.editGroup = null;
        S.editZone = JSON.parse(JSON.stringify(z));
        renderConfig();
        drawZones();
    }

    function zoneForm(z) {
        var f = el("div", "dt-zone-form");
        var ln = el("label", "", "Nom de la zone");
        var n = document.createElement("input"); n.type = "text"; n.value = z.name; n.maxLength = 60; n.placeholder = "Ex. Portail Houx";
        ln.appendChild(n); f.appendChild(ln);
        var lc = el("label", "", "Couleur");
        var c = document.createElement("input"); c.type = "color"; c.value = z.color || "#d97706";
        lc.appendChild(c); f.appendChild(lc);
        f.appendChild(el("div", "dt-sec", "Cameras qui surveillent cette zone"));
        var rows = [];
        S.config.channels.forEach(function (ch) {
            var link = (z.links || []).filter(function (l) { return l.camera_id === ch.camera_id && l.channel === ch.channel; })[0];
            var r = el("label", "dt-link-row");
            var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !!link;
            r.appendChild(cb);
            r.appendChild(el("span", "", ch.camera_name + " - " + ch.channel_name));
            var rg = document.createElement("input"); rg.type = "text"; rg.placeholder = "zones cam."; rg.title = "Numeros des zones de la camera (ex. 1,2), vide = toutes";
            rg.value = link && link.regions.length ? link.regions.join(",") : "";
            r.appendChild(rg);
            rows.push({ ch: ch, cb: cb, rg: rg });
            f.appendChild(r);
        });
        f.appendChild(el("p", "dt-help", "Une intrusion detectee par un canal coche est rattachee a cette zone. Camera multi-capteurs : decochez les canaux qui ne filment pas cette zone. Pour un canal qui a plusieurs zones de detection sur la camera, indiquez leurs numeros."));
        var acts = el("div", "dt-actions");
        var save = el("button", "dt-btn dt-btn-primary", z._id ? "Enregistrer" : "Creer la zone"); save.type = "button";
        save.addEventListener("click", function () {
            var body = {
                name: n.value.trim(), color: c.value, polygon: z.polygon,
                links: rows.filter(function (x) { return x.cb.checked; }).map(function (x) {
                    return { camera_id: x.ch.camera_id, channel: x.ch.channel, regions: x.rg.value.split(/[ ,;]+/).filter(Boolean) };
                }),
            };
            (z._id ? send("PUT", API.zones + "/" + z._id, body) : send("POST", API.zones, body)).then(function () {
                toast("success", "Zone enregistree");
                S.editZone = null;
                return loadConfig().then(refreshAll);
            }).catch(function (e) { toast("error", e.message); });
        });
        acts.appendChild(save);
        var redraw = el("button", "dt-btn", "Redessiner"); redraw.type = "button";
        redraw.addEventListener("click", function () { var keep = S.editZone; S.editZone = null; startDrawing(keep); });
        acts.appendChild(redraw);
        if (z._id) {
            var del = el("button", "dt-btn dt-btn-danger", "Supprimer"); del.type = "button";
            del.addEventListener("click", function () {
                showConfirmToast("Supprimer la zone " + z.name + " ? Les intrusions sont conservees.", { okLabel: "Supprimer", type: "warning" }).then(function (ok) {
                    if (!ok) return;
                    send("DELETE", API.zones + "/" + z._id).then(function () {
                        S.editZone = null; toast("success", "Zone supprimee");
                        return loadConfig().then(refreshAll);
                    }).catch(function (e) { toast("error", e.message); });
                });
            });
            acts.appendChild(del);
        }
        var cancel = el("button", "dt-btn", "Annuler"); cancel.type = "button";
        cancel.addEventListener("click", function () { S.editZone = null; renderConfig(); drawZones(); });
        acts.appendChild(cancel);
        f.appendChild(acts);
        return f;
    }

    function onMapClick(e) {
        if (!S.edit) return;
        if (S.placing) {
            var dv = devById(S.placing);
            stopPlacing();
            if (dv) savePosition(dv, e.latlng.lat, e.latlng.lng);
            return;
        }
        if (S.drawing) {
            S.drawing.points.push(e.latlng);
            renderDrawing();
        }
    }

    /* ---------------- evenements ---------------- */
    function bind() {
        Array.prototype.forEach.call(document.querySelectorAll("#dt-period button"), function (b) {
            b.addEventListener("click", function () {
                S.period = b.dataset.period; S.date = "";
                $("dt-date").value = ""; $("dt-date").classList.remove("active");
                Array.prototype.forEach.call(document.querySelectorAll("#dt-period button"), function (x) { x.classList.toggle("active", x === b); });
                updateFilterCount();
                refreshAll();
            });
        });
        $("dt-date").max = new Date().toLocaleDateString("fr-CA", { timeZone: TZ });
        $("dt-date").addEventListener("change", function () {
            if (!this.value) return;
            S.period = "day"; S.date = this.value; this.classList.add("active");
            Array.prototype.forEach.call(document.querySelectorAll("#dt-period button"), function (x) { x.classList.remove("active"); });
            refreshAll();
        });
        Array.prototype.forEach.call(document.querySelectorAll("#dt-type button"), function (b) {
            b.addEventListener("click", function () {
                S.type = b.dataset.type;
                Array.prototype.forEach.call(document.querySelectorAll("#dt-type button"), function (x) { x.classList.toggle("active", x === b); });
                updateFilterCount();
                refreshAll();
            });
        });
        $("dt-zone").addEventListener("change", function () { S.zone = this.value; updateFilterCount(); refreshAll(); });
        $("dt-camera").addEventListener("change", function () { S.camera = this.value; updateFilterCount(); refreshAll(); });
        $("dt-status").addEventListener("change", function () { S.status = this.value; updateFilterCount(); refreshAll(); });
        $("dt-ai").addEventListener("change", function () { S.ai = this.value; updateFilterCount(); refreshAll(); });
        $("dt-planning").addEventListener("change", function () { S.planning = this.value; updateFilterCount(); refreshAll(); });
        $("dt-groupby").addEventListener("change", function () { S.groupBy = this.value; renderList(); });
        $("dt-group").addEventListener("change", function () { S.grp = this.value; updateFilterCount(); refreshAll(); });
        // Telephone : onglets, filtres replies, actualisation, glisser dans la visionneuse
        Array.prototype.forEach.call(document.querySelectorAll("#dt-tabs button"), function (b) {
            b.addEventListener("click", function () { setView(b.dataset.view); });
        });
        $("dt-filters-toggle").addEventListener("click", function () {
            var open = $("dt-filters").classList.toggle("is-open");
            this.setAttribute("aria-expanded", open ? "true" : "false");
        });
        $("dt-refresh-btn").addEventListener("click", function () { loadConfig(); refreshAll(); toast("info", "Actualise"); });
        onSwipe($("dt-lightbox"), function (d) { if (lb && lb.photos.length > 1) lbGo(d); });
        $("dt-seen-all").addEventListener("click", function () {
            var ids = S.items.filter(function (x) { return x.status === "new"; }).map(function (x) { return x.id; });
            if (!ids.length) { toast("info", "Aucune nouvelle intrusion affichee"); return; }
            send("POST", API.status, { ids: ids, status: "seen" }).then(function (r) {
                toast("success", r.modified + " intrusion(s) marquee(s) vue(s)");
                refreshAll();
            }).catch(function (e) { toast("error", e.message); });
        });
        $("dt-detail-close").addEventListener("click", closeDetail);
        $("dt-edit-btn").addEventListener("click", function () { setEdit(!S.edit); });
        $("dt-config-close").addEventListener("click", function () { setEdit(false); });
        $("dt-lightbox").addEventListener("click", closeLightbox);
        document.addEventListener("keydown", function (e) {
            var tag = (e.target && e.target.tagName) || "";
            if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
            // Galerie : fleches gauche / droite, Z = scene / zoom (visionneuse)
            if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                var d = e.key === "ArrowLeft" ? -1 : 1;
                if (!$("dt-lightbox").hidden && lb && lb.photos.length > 1) { e.preventDefault(); lbGo(d); }
                else if (!$("dt-detail").hidden && gallery && gallery.photos.length > 1) { e.preventDefault(); gallery.go(gallery.idx + d); }
                return;
            }
            if ((e.key === "z" || e.key === "Z") && !$("dt-lightbox").hidden && lb) {
                lb.mode = lb.mode === "zoom" ? "scene" : "zoom"; renderLightbox(); return;
            }
            if (e.key !== "Escape") return;
            if (!$("dt-lightbox").hidden) closeLightbox();
            else if (S.drawing) { cancelDrawing(); renderConfig(); }
            else if (S.placing) { stopPlacing(); renderConfig(); }
            else if (!$("dt-detail").hidden) closeDetail();
        });
    }

    function poll() {
        if (document.hidden) return;
        // Etat d'activation des groupes (programme horaire) toutes les 30 s
        if (++S.pollN % 3 === 0) loadConfig();
        if (S.period === "day") return;
        loadOverview();
        // Rafraichit la premiere page (episodes en cours : duree, photos)
        if (!S.next || S.items.length <= 60) loadList(true);
    }

    function init() {
        initMap();
        bind();
        bindPlan();
        placePeriod();
        PHONE_MQ.addEventListener("change", function () {
            placePeriod();
            if (map) setTimeout(function () { map.invalidateSize(); }, 80);
        });
        loadConfig().then(function () {
            fitAll(); refreshAll();
            // Lien d'une alerte (centrale, WhatsApp) : /detection?episode=<id>
            var ep = new URLSearchParams(location.search).get("episode");
            if (ep && /^[0-9a-f]{24}$/.test(ep)) {
                openDetail(ep);
                history.replaceState(null, "", location.pathname);
            }
        });
        setInterval(poll, POLL_MS);
        document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
    }

    // Pour la page IA (ia_capture.js : trafic, galerie) : plannings (groupes)
    // et fiche d'une intrusion
    window.IADetection = {
        config: function () { return S.config; },
        reloadConfig: function () { return loadConfig(); },
        openGroup: function (gid, members) {
            var g = (S.config.groups || []).filter(function (x) { return x._id === gid; })[0];
            if (!g) { g = newGroupDraft(); g.members = members || []; }
            openPlan(g);
        },
        // Filtre "camera" du mode Intrusions (cle "<id>:*" = tous les canaux)
        filterCamera: function (key) {
            var sel = $("dt-camera"), id = String(key || "").split(":")[0];
            // Appareil a un seul canal : son option est "<id>:<canal>"
            var opt = sel && Array.prototype.filter.call(sel.options, function (o) { return o.value.indexOf(id + ":") === 0; })[0];
            S.camera = opt ? opt.value : (key || "");
            if (sel) sel.value = S.camera;
            updateFilterCount();
        },
        openEpisode: function (id) { setView("list"); this.refresh(); openDetail(id); },
        refresh: function () {
            refreshAll();
            if (!map) return;
            map.invalidateSize();
            if (needFit) fitAll();
            setTimeout(function () { map.invalidateSize(); }, 50);
        },
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
