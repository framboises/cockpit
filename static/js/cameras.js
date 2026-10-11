/* =========================================================================
   Cameras -- configuration de toutes les cameras (page /cameras, cameras.py)

   - Tableau des appareils (toutes marques) : recherche, filtres, tri,
     selection multiple et edition de masse (identifiants, site, activation,
     suppression).
   - Fiche en panneau lateral : identite, profil d'identifiants (mot de passe
     jamais affiche), chemin d'envoi des evenements, canaux et usages (LAPI,
     detection d'intrusion), apercu par canal.
   - Pilotage (capture, presets, essuie-glace) : marques "controllable" (Hikvision, Bosch,
     Dahua, Hanwha, Axis), jamais les cameras de piste (c.piste, verrou serveur).
     Changement de vue uniquement par preset : pas de deplacement libre (fleches).
   - Etat reseau : test TCP cote serveur, garde 1 min (GET reachability).
   ========================================================================= */
(function () {
    "use strict";

    var API = {
        list: "/api/cameras",
        meta: "/api/cameras/meta",
        bulk: "/api/cameras/bulk",
        reach: "/api/cameras/reachability",
        profiles: "/api/camera-profiles",
    };
    var REACH_MS = 60000;

    var S = {
        cams: [], meta: null, reach: {}, selected: {}, sort: { key: "name", desc: false },
        f: { q: "", site: "", brand: "", usage: "", state: "" },
        openId: null, draft: null,
    };

    /* ---------------- utilitaires ---------------- */
    function $(id) { return document.getElementById(id); }
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined && text !== null) e.textContent = text;
        return e;
    }
    function icon(name) { return el("span", "material-symbols-outlined", name); }
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
            var ct = r.headers.get("Content-Type") || "";
            if (ct.indexOf("image/") === 0) { if (!r.ok) throw new Error("HTTP " + r.status); return r.blob(); }
            return r.json().catch(function () { return {}; }).then(function (j) {
                if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
                return j;
            });
        });
    }
    function toast(type, msg) { if (typeof showToast === "function") showToast(type, msg); }
    function confirmToast(msg, ok) { return showConfirmToast(msg, { okLabel: ok || "Confirmer", type: "warning" }); }
    function roleSpec(t) { return (S.meta.roles || []).filter(function (r) { return r.type === t; })[0]; }
    function camRoles(c) {
        var out = [];
        (c.channels || []).forEach(function (ch) {
            (ch.roles || []).forEach(function (r) { if (out.indexOf(r.type) < 0) out.push(r.type); });
        });
        return out;
    }
    function reachOf(c) {
        if (!c.enabled) return "off";
        var r = S.reach[c._id];
        return r ? (r.ok ? "ok" : "ko") : "";
    }
    function ago(iso) {
        if (!iso) return "";
        var s = (Date.now() - new Date(iso).getTime()) / 1000;
        return s < 90 ? "a l'instant" : "il y a " + Math.round(s / 60) + " min";
    }

    /* ---------------- chargement ---------------- */
    function load() {
        return Promise.all([getJSON(API.list), getJSON(API.meta)]).then(function (res) {
            S.cams = res[0];
            S.meta = res[1];
            $("cm-key-banner").hidden = !!S.meta.key_ok;
            fillFilters();
            render();
        }).catch(function (e) {
            $("cm-tbody").textContent = "";
            var tr = el("tr"), td = el("td", "cm-empty", "Chargement impossible (" + e.message + ").");
            td.colSpan = 9; tr.appendChild(td); $("cm-tbody").appendChild(tr);
        });
    }
    function loadReach() {
        if (document.hidden) return;
        getJSON(API.reach).then(function (r) { S.reach = r; render(); }).catch(function () {});
    }

    function fillSelect(sel, items, keepFirst) {
        var cur = sel.value;
        while (sel.options.length > keepFirst) sel.remove(keepFirst);
        items.forEach(function (it) { sel.appendChild(new Option(it[1], it[0])); });
        sel.value = cur;
    }
    function fillFilters() {
        var sites = S.meta.sites || [];
        fillSelect($("cm-f-site"), sites.map(function (s) { return [s, s]; }), 1);
        var brands = Object.keys(S.meta.brands).map(function (k) { return [k, S.meta.brands[k]]; });
        fillSelect($("cm-f-brand"), brands, 1);
        fillSelect($("cm-f-usage"), S.meta.roles.map(function (r) { return [r.type, r.label]; }), 2);
        var dl = $("cm-sites");
        dl.textContent = "";
        sites.forEach(function (s) { dl.appendChild(new Option(s, s)); });
        var bp = $("cm-bulk-profile");
        bp.textContent = "";
        bp.appendChild(new Option("-- aucun --", ""));
        S.meta.profiles.forEach(function (p) { bp.appendChild(new Option(p.name, p._id)); });
    }

    /* ---------------- tableau ---------------- */
    function filtered() {
        var f = S.f, q = f.q.toLowerCase();
        return S.cams.filter(function (c) {
            if (f.site && c.site !== f.site) return false;
            if (f.brand && c.brand !== f.brand) return false;
            if (f.usage) {
                var roles = camRoles(c);
                if (f.usage === "none" ? roles.length : roles.indexOf(f.usage) < 0) return false;
            }
            if (f.state) {
                var st = reachOf(c);
                if (f.state === "online" && st !== "ok") return false;
                if (f.state === "offline" && st !== "ko") return false;
                if (f.state === "disabled" && c.enabled) return false;
                if (f.state === "noprofile" && c.credential_profile_id) return false;
            }
            if (q) {
                var hay = [c.name, c.ip, c.model, c.camera_path, c.site, c.location, c.profile_name]
                    .concat((c.channels || []).map(function (ch) { return ch.name; })).join(" ").toLowerCase();
                if (hay.indexOf(q) < 0) return false;
            }
            return true;
        });
    }
    function sortKey(c, k) {
        if (k === "ip") return c.ip.split(".").map(function (x) { return ("00" + x).slice(-3); }).join(".");
        if (k === "brand") return (c.brand + " " + c.model).toLowerCase();
        if (k === "profile") return (c.profile_name || "~").toLowerCase();
        if (k === "state") return { ok: 0, ko: 1, off: 2 }[reachOf(c)] != null ? { ok: 0, ko: 1, off: 2 }[reachOf(c)] : 3;
        return String(c[k] || "~").toLowerCase();
    }
    function sorted(list) {
        var k = S.sort.key, d = S.sort.desc ? -1 : 1;
        return list.slice().sort(function (a, b) {
            var x = sortKey(a, k), y = sortKey(b, k);
            // Ordre alphabetique francais (accents, "015" avant "120")
            if (typeof x === "string" && x !== y) return d * x.localeCompare(y, "fr", { numeric: true });
            if (x < y) return -d;
            if (x > y) return d;
            return a.name.localeCompare(b.name, "fr");
        });
    }

    function render() {
        var list = sorted(filtered());
        var tb = $("cm-tbody");
        tb.textContent = "";
        var frag = document.createDocumentFragment();
        list.forEach(function (c) { frag.appendChild(row(c)); });
        if (!list.length) {
            var tr = el("tr"), td = el("td", "cm-empty", "Aucune camera ne correspond.");
            td.colSpan = 9; tr.appendChild(td); frag.appendChild(tr);
        }
        tb.appendChild(frag);
        var online = S.cams.filter(function (c) { return reachOf(c) === "ok"; }).length;
        var offline = S.cams.filter(function (c) { return reachOf(c) === "ko"; }).length;
        $("cm-count").textContent = list.length + " / " + S.cams.length + " cameras";
        $("cm-header-sub").textContent = Object.keys(S.reach).length
            ? online + " en ligne, " + offline + " hors ligne" : S.cams.length + " appareils";
        Array.prototype.forEach.call(document.querySelectorAll("#cm-table th[data-sort]"), function (th) {
            th.classList.toggle("is-sorted", th.dataset.sort === S.sort.key);
            th.classList.toggle("is-desc", th.dataset.sort === S.sort.key && S.sort.desc);
        });
        renderBulk(list);
        if (S.view === "map") drawMapPins(list);
    }

    /* ---------------- carte des cameras ---------------- */
    // Positions : manuelle (page Detection) ou couche GPS titan.cameras (une IP
    // = un lieu). La carte suit les filtres du tableau ; le survol d'un pin
    // montre la fiche courte et la derniere capture (image chargee au survol).
    var MAP_CENTER = [47.938561, 0.224318];
    var M = { map: null, layer: null, pos: {}, unplaced: 0, poll: null, loaded: false };

    function setView(v) {
        S.view = v;
        var isMap = v === "map";
        $("cm-map-area").hidden = !isMap;
        $("cm-table-wrap").hidden = isMap;
        $("cm-map-btn").classList.toggle("is-on", isMap);
        $("cm-map-btn").setAttribute("aria-pressed", isMap ? "true" : "false");
        document.querySelector(".cm-app").classList.toggle("is-map", isMap);
        Array.prototype.forEach.call(document.querySelectorAll("#cm-mnav [data-mnav]"), function (b) {
            b.classList.toggle("is-active", b.dataset.mnav === v);
        });
        try { localStorage.setItem("cockpit-cameras-view", v); } catch (e) {}
        if (!isMap) return;
        initCamMap();
        M.map.invalidateSize();
        M.lastKey = null;  // recadrer a chaque ouverture de la carte
        loadMapData().then(render);
        pollCaptureAll();
    }
    function initCamMap() {
        if (M.map) { setTimeout(function () { M.map.invalidateSize(); }, 50); return; }
        M.map = L.map("cm-map", { center: MAP_CENTER, zoom: 14, maxZoom: 22, zoomControl: false, zoomSnap: 0.25 });
        L.control.zoom({ position: "bottomright" }).addTo(M.map);
        var ign = L.tileLayer("https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}", { attribution: "IGN-F/Geoplateforme", maxNativeZoom: 19, maxZoom: 22 });
        var osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "OSM", maxNativeZoom: 19, maxZoom: 22 });
        var aco = L.tileLayer("/tiles/{z}/{x}/{y}.png", { attribution: "ACO", tms: true, maxZoom: 22 });
        ign.addTo(M.map);
        L.control.layers({ "Vue aerienne": ign, "Plan": osm, "Plan ACO": aco }, {}, { position: "topright" }).addTo(M.map);
        M.layer = L.layerGroup().addTo(M.map);
        M.map.on("click", onMapClick);
        if (window.ResizeObserver) new ResizeObserver(function () { M.map.invalidateSize(); }).observe($("cm-map"));
    }
    function loadMapData() {
        return getJSON("/api/cameras/map").then(function (r) {
            M.pos = {};
            r.cameras.forEach(function (c) { M.pos[c._id] = c; });
            M.unplaced = r.unplaced;
            M.loaded = true;
        }).catch(function (e) { toast("error", "Carte indisponible : " + e.message); });
    }
    function placed(list) { return list.filter(function (c) { return M.pos[c._id]; }); }
    function capturable(c) { return c.enabled && c.controllable && c.credential_profile_id; }
    // Canaux actifs captures par "Capturer" (meme regle que cameras._capture_channels)
    function activeChannels(c) {
        return (c.channels || []).filter(function (ch) { return ch.enabled !== false; }).length || 1;
    }
    function fitPins() {
        var pts = placed(filtered()).map(function (c) { return [M.pos[c._id].lat, M.pos[c._id].lng]; });
        // Cadrage sur le site : les cameras lointaines (boutique de Paris)
        // n'ecrasent pas la vue, sauf si elles sont seules dans le filtre
        var near = pts.filter(function (p) { return M.map.distance(p, MAP_CENTER) < 30000; });
        if (near.length) pts = near;
        if (pts.length) M.map.fitBounds(pts, { padding: [40, 40], maxZoom: 18 });
    }
    function camTip(c) {
        var p = M.pos[c._id], st = reachOf(c);
        var box = el("div", "cm-tip");
        var shot = el("div", "cm-tip-shot");
        shot.appendChild(el("span", "cm-tip-noshot", p.snap_at ? "Chargement..." : "Aucune capture"));
        box.appendChild(shot);
        box.appendChild(el("b", "", c.name));
        box.appendChild(el("div", "cm-tip-line", c.ip + (c.port !== 80 ? ":" + c.port : "") + " - " + (S.meta.brands[c.brand] || c.brand) + (c.model ? " " + c.model : "")));
        var where = [c.site, c.location !== c.site ? c.location : ""].filter(Boolean).join(" - ");
        if (where) box.appendChild(el("div", "cm-tip-line", where));
        var chs = c.channels || [];
        var roles = camRoles(c).map(function (t) { var r = roleSpec(t); return r ? r.label : t; });
        box.appendChild(el("div", "cm-tip-line", chs.length + (chs.length > 1 ? " canaux" : " canal") + (roles.length ? " - " + roles.join(", ") : "")));
        var state = st === "off" ? "Desactivee" : st === "ok" ? "En ligne" : st === "ko" ? (c.cellular ? "4G : en veille ou hors couverture" : "Ne repond pas") : "Test reseau en cours";
        var stl = el("div", "cm-tip-state is-" + (st === "ko" && c.cellular ? "sleep" : st || "na"), state);
        box.appendChild(stl);
        box.appendChild(el("div", "cm-tip-line cm-tip-muted", (p.snap_at ? "Capture " + ago(p.snap_at) : "Jamais capturee") +
            " - position " + (p.map_source === "manual" ? "placee a la main" : "couche GPS")));
        // Plusieurs canaux (enregistreur) : une vignette par canal capture
        var snaps = p.snaps || {};
        var multi = chs.filter(function (ch) { return snaps[ch.no]; });
        if (chs.length > 1 && multi.length > 1) {
            shot.className = "cm-tip-shot cm-tip-grid";
            shot.textContent = "";
        }
        box._loadShot = function () {
            if (box._shotLoaded) return;
            if (chs.length > 1 && multi.length > 1) {
                box._shotLoaded = true;
                multi.forEach(function (ch) {
                    var cell = el("div", "cm-tip-cell");
                    var im = document.createElement("img");
                    im.alt = ch.name || "Canal " + ch.no;
                    im.src = API.list + "/" + c._id + "/snapshot?channel=" + ch.no + "&t=" + encodeURIComponent(snaps[ch.no]);
                    cell.appendChild(im);
                    cell.appendChild(el("span", "", ch.name || "Canal " + ch.no));
                    shot.appendChild(cell);
                });
                return;
            }
            if (!p.snap_at) return;
            box._shotLoaded = true;
            var im = document.createElement("img");
            im.alt = "Derniere capture";
            im.onload = function () { shot.textContent = ""; shot.appendChild(im); };
            im.onerror = function () { shot.textContent = ""; shot.appendChild(el("span", "cm-tip-noshot", "Aucune capture")); };
            im.src = API.list + "/" + c._id + "/snapshot?t=" + encodeURIComponent(p.snap_at);
        };
        return box;
    }
    function drawMapPins(list) {
        if (!M.map || !M.loaded) return;
        M.layer.clearLayers();
        var pins = placed(list);
        pins.forEach(function (c) {
            var p = M.pos[c._id], st = reachOf(c);
            var dot = el("div", "cm-pin is-" + (st === "ko" && c.cellular ? "sleep" : st || "na"));
            dot.appendChild(icon(c.piste ? "lock" : "videocam"));
            if (M.edit) dot.classList.add("is-draggable");
            var mk = L.marker([p.lat, p.lng], { icon: L.divIcon({ html: dot, className: "", iconSize: [28, 28], iconAnchor: [14, 14] }),
                draggable: !!M.edit, autoPan: true });
            var tip = camTip(c);
            mk.bindTooltip(tip, { direction: "top", offset: [0, -14], className: "cm-tip-wrap", opacity: 1 });
            mk.on("tooltipopen", function () { tip._loadShot(); });
            mk.on("click", function () { if (!M.edit) openDrawer(c); });
            mk.on("dragstart", function () { mk.closeTooltip(); });
            mk.on("dragend", function () { moveCam(c, mk); });
            mk.addTo(M.layer);
        });
        fillPlaceSelect(list);
        // Recadrage seulement a l'ouverture de la carte et quand les FILTRES
        // changent : placer, deplacer une camera ou un test reseau ne bougent
        // pas la vue (on reste ou l'on vient de poser)
        var key = JSON.stringify(S.f);
        if (key !== M.lastKey) { M.lastKey = key; fitPins(); }
        var n = pins.filter(capturable).length;
        var nch = pins.filter(capturable).reduce(function (a, c) { return a + activeChannels(c); }, 0);
        $("cm-map-count").textContent = pins.length + " camera(s) sur la carte" +
            (list.length > pins.length ? ", " + (list.length - pins.length) + " sans position" : "");
        if (!M.job || !M.job.running) {
            $("cm-capture-all-label").textContent = "Capturer les " + n + " camera(s)" + (nch > n ? " (" + nch + " canaux)" : "");
            $("cm-capture-all").disabled = !n || !S.meta.key_ok;
        }
        $("cm-capture-all").title = "Capture de chaque canal actif des cameras pilotables de la carte, canal apres canal sur un meme enregistreur (jamais les cameras de piste)";
    }

    // Correction de position : ecrit dans la couche GPS titan.cameras (tous les
    // points de l'IP), relue par la page Detection (PUT /api/cameras/<id>/geo)
    var HINT_EDIT = "Faites glisser un pin : la position est enregistree dans la couche GPS des cameras.";
    function setMapEdit(on) {
        M.edit = on;
        M.placing = null;
        $("cm-map-edit").classList.toggle("is-on", on);
        $("cm-map-edit").setAttribute("aria-pressed", on ? "true" : "false");
        $("cm-map-hint").hidden = !on;
        $("cm-map-hint").textContent = HINT_EDIT;
        $("cm-map-place").hidden = !on;
        $("cm-map").classList.remove("is-placing");
        render();
    }
    // Cameras sans position (filtres du tableau appliques) : a placer d'un clic
    function fillPlaceSelect(list) {
        var sel = $("cm-map-place");
        if (sel.hidden) return;
        var todo = list.filter(function (c) { return !M.pos[c._id]; });
        sel.textContent = "";
        sel.appendChild(new Option(todo.length ? "Placer une camera sans position (" + todo.length + ")..." : "Toutes les cameras filtrees sont placees", ""));
        todo.forEach(function (c) { sel.appendChild(new Option(c.name + " - " + c.ip, c._id)); });
        sel.value = M.placing || "";
    }
    function onPlaceSelect() {
        M.placing = this.value || null;
        var c = M.placing && S.cams.filter(function (x) { return x._id === M.placing; })[0];
        $("cm-map-hint").textContent = c ? "Cliquez sur la carte a l'emplacement de " + c.name + " (point cree dans la couche GPS)." : HINT_EDIT;
        $("cm-map").classList.toggle("is-placing", !!c);
    }
    function onMapClick(e) {
        if (!M.edit || !M.placing) return;
        var c = S.cams.filter(function (x) { return x._id === M.placing; })[0];
        if (!c) return;
        var n = (c.channels || []).length || 1;
        confirmToast("Placer " + c.name + " ici ? " + n + " point(s) seront crees dans la couche GPS des cameras.", "Placer").then(function (ok) {
            if (!ok) return;
            send("PUT", API.list + "/" + c._id + "/geo", { lat: e.latlng.lat, lng: e.latlng.lng }).then(function (r) {
                toast("success", c.name + " placee" + (r.created ? " : " + r.created + " point(s) cree(s) dans la couche GPS" : ""));
                M.placing = null;
                $("cm-map").classList.remove("is-placing");
                $("cm-map-hint").textContent = HINT_EDIT;
                return loadMapData().then(render);
            }).catch(function (err) { toast("error", err.message); });
        });
    }
    function moveCam(c, mk) {
        var p = M.pos[c._id], to = mk.getLatLng();
        var dist = Math.round(M.map.distance([p.lat, p.lng], to));
        confirmToast("Deplacer " + c.name + " de " + dist + " m ? La position est enregistree dans la couche GPS des cameras" +
            ((c.channels || []).length > 1 ? " (tous les points de cette IP)." : "."), "Deplacer").then(function (ok) {
            if (!ok) { mk.setLatLng([p.lat, p.lng]); return; }
            send("PUT", API.list + "/" + c._id + "/geo", { lat: to.lat, lng: to.lng }).then(function (r) {
                p.lat = r.lat; p.lng = r.lng; p.map_source = r.map_source;
                toast("success", c.name + " deplacee" + (r.points ? " (" + r.points + " point(s) de la couche GPS)" : " (position manuelle : absente de la couche GPS)"));
                render();
            }).catch(function (e) {
                mk.setLatLng([p.lat, p.lng]);
                toast("error", e.message);
            });
        });
    }

    function startCaptureAll() {
        var ids = placed(filtered()).filter(capturable).map(function (c) { return c._id; });
        if (!ids.length) return;
        var cell = placed(filtered()).filter(function (c) { return capturable(c) && c.cellular; }).length;
        var nch = placed(filtered()).filter(capturable).reduce(function (a, c) { return a + activeChannels(c); }, 0);
        confirmToast("Capturer " + ids.length + " camera(s)" + (nch > ids.length ? ", soit " + nch + " canaux" : "") + " ?" +
            (cell ? " Dont " + cell + " en 4G, qui peuvent mettre jusqu'a une minute a se reveiller." : ""), "Capturer").then(function (ok) {
            if (!ok) return;
            send("POST", "/api/cameras/capture-all", { ids: ids }).then(function (r) {
                toast("info", "Capture lancee : " + r.devices + " camera(s), " + r.total + " canal(aux)" + (r.skipped ? ", " + r.skipped + " ignoree(s)" : ""));
                pollCaptureAll();
            }).catch(function (e) { toast("error", e.message); });
        });
    }
    function pollCaptureAll() {
        clearTimeout(M.poll);
        getJSON("/api/cameras/capture-all").then(function (j) {
            var was = M.job && M.job.running;
            M.job = j;
            var bar = $("cm-map-progress");
            if (!j.total) { bar.hidden = true; return; }
            bar.hidden = false;
            $("cm-map-progress-fill").style.width = Math.round(100 * j.done / j.total) + "%";
            var fails = (j.failed || []).length;
            $("cm-map-progress-text").textContent = (j.running ? "Capture " : "Derniere capture : ") + j.done + " / " + j.total + " canaux" +
                (fails ? " - " + fails + " echec(s)" : "") + (j.running ? "" : " (" + ago(j.finished_at) + ")");
            $("cm-map-progress-text").title = (j.failed || []).map(function (f) { return f.name + " : " + f.error; }).join("\n");
            $("cm-capture-all").disabled = !!j.running;
            if (j.running) {
                $("cm-capture-all-label").textContent = "Capture en cours...";
                M.poll = setTimeout(pollCaptureAll, 2000);
                // Vignettes a jour au fil de l'eau (toutes les 8 captures)
                if (j.done - (M.lastDone || 0) >= 8) { M.lastDone = j.done; loadMapData().then(render); }
            } else if (was) {
                toast(fails ? "warning" : "success", "Capture terminee : " + j.ok + " reussie(s)" + (fails ? ", " + fails + " echec(s)" : ""));
                loadMapData().then(render);
            }
        }).catch(function () {});
    }

    function row(c) {
        var tr = el("tr", (S.selected[c._id] ? "is-selected " : "") + (c._id === S.openId ? "is-open " : "") + (c.enabled ? "" : "is-disabled"));
        tr.dataset.id = c._id;

        var tdc = el("td");
        var cb = document.createElement("input");
        cb.type = "checkbox"; cb.checked = !!S.selected[c._id];
        cb.setAttribute("aria-label", "Selectionner " + c.name);
        cb.addEventListener("click", function (e) { e.stopPropagation(); });
        cb.addEventListener("change", function () {
            if (cb.checked) S.selected[c._id] = true; else delete S.selected[c._id];
            render();
        });
        tdc.appendChild(cb);
        tr.appendChild(tdc);

        var st = reachOf(c), tds = el("td");
        var dot = el("span", "cm-dot" + (st ? " is-" + st : ""));
        var r = S.reach[c._id];
        if (st === "ko" && c.cellular) dot.className = "cm-dot is-sleep";
        dot.title = st === "off" ? "Desactivee" : r ? (r.ok ? "En ligne" : c.cellular ? "Camera 4G : en veille ou hors couverture" : "Ne repond pas") + " (test " + ago(r.at) + ")" : "Test reseau en cours...";
        tds.appendChild(dot);
        tr.appendChild(tds);

        var tdn = el("td");
        tdn.appendChild(el("div", "cm-name", c.name));
        if (c.camera_path) { var p = el("span", "cm-path", c.camera_path); p.title = "Chemin d'envoi des evenements"; tdn.appendChild(p); }
        if (c.location && c.location !== c.site) tdn.appendChild(el("div", "cm-sub", c.location));
        tr.appendChild(tdn);

        var tdi = el("td");
        tdi.appendChild(el("div", "cm-mono", c.ip + (c.port !== 80 ? ":" + c.port : "")));
        if (c.protocol === "https") tdi.appendChild(el("div", "cm-sub", "https"));
        tr.appendChild(tdi);

        var tdb = el("td", "cm-col-hide-sm");
        tdb.appendChild(el("div", "", S.meta.brands[c.brand] || c.brand));
        tdb.appendChild(el("div", "cm-sub", [c.model, c.firmware].filter(Boolean).join(" - ")));
        tr.appendChild(tdb);

        tr.appendChild(el("td", "cm-col-hide-md", c.site || ""));

        var tdp = el("td", "cm-col-hide-md");
        if (c.profile_name) tdp.textContent = c.profile_name;
        else tdp.appendChild(el("span", "cm-warn", "Aucun"));
        tr.appendChild(tdp);

        var tdr = el("td"), chips = el("div", "cm-chips");
        chips.appendChild(el("span", "cm-chan-n", (c.channels || []).length + " canal" + ((c.channels || []).length > 1 ? "aux" : "")));
        camRoles(c).forEach(function (t) {
            var spec = roleSpec(t);
            if (!spec) return;
            var ch = el("span", "cm-chip");
            ch.style.setProperty("--c", spec.color);
            ch.appendChild(icon(spec.icon));
            ch.appendChild(document.createTextNode(spec.label));
            chips.appendChild(ch);
        });
        tdr.appendChild(chips);
        tr.appendChild(tdr);

        var tda = el("td"), acts = el("div", "cm-actions");
        var canControl = c.controllable && S.meta.key_ok && c.credential_profile_id;
        var why = c.piste ? "Camera de piste : aucun pilotage depuis Cockpit (securite piste)" :
            !c.controllable ? "Pilotage non disponible pour cette marque" : !c.credential_profile_id ? "Aucun profil d'identifiants" : !S.meta.key_ok ? "Cle de chiffrement absente" : "";
        acts.appendChild(actBtn("photo_camera", canControl ? "Capture" : why, !canControl, function () { capture(c, 0, null); }));
        var canWipe = canControl && c.wiper;
        acts.appendChild(actBtn("water_drop", canWipe ? "Essuie-glace" : (why || "Essuie-glace non pilotable pour cette marque"), !canWipe, function () { wiper(c); }));
        acts.appendChild(actBtn("edit", "Modifier", false, function () { openDrawer(c); }));
        acts.appendChild(actBtn("delete", "Supprimer", false, function () { removeCams([c]); }));
        tda.appendChild(acts);
        tr.appendChild(tda);

        tr.addEventListener("click", function () { openDrawer(c); });
        return tr;
    }
    function actBtn(ic, title, disabled, fn) {
        var b = el("button", "cm-icon-btn");
        b.type = "button"; b.title = title; b.disabled = disabled;
        b.setAttribute("aria-label", title);
        b.appendChild(icon(ic));
        b.addEventListener("click", function (e) { e.stopPropagation(); if (!disabled) fn(); });
        return b;
    }

    /* ---------------- selection et edition de masse ---------------- */
    function selectedIds() { return Object.keys(S.selected); }
    function renderBulk(visible) {
        var ids = selectedIds();
        $("cm-bulk").hidden = !ids.length;
        $("cm-bulk-n").textContent = ids.length + " selectionnee" + (ids.length > 1 ? "s" : "");
        var all = $("cm-check-all");
        var visIds = visible.map(function (c) { return c._id; });
        var nSel = visIds.filter(function (id) { return S.selected[id]; }).length;
        all.checked = visIds.length > 0 && nSel === visIds.length;
        all.indeterminate = nSel > 0 && nSel < visIds.length;
    }
    function bulk(kind) {
        var ids = selectedIds();
        if (kind === "clear") { S.selected = {}; render(); return; }
        if (!ids.length) return;
        var body = { ids: ids };
        if (kind === "profile") body.set = { credential_profile_id: $("cm-bulk-profile").value };
        else if (kind === "site") {
            var site = $("cm-bulk-site").value.trim();
            if (!site) { toast("warning", "Saisissez un site"); return; }
            body.set = { site: site };
        } else if (kind === "enable" || kind === "disable") body.set = { enabled: kind === "enable" };
        else if (kind === "delete") { removeCams(S.cams.filter(function (c) { return S.selected[c._id]; })); return; }
        send("POST", API.bulk, body).then(function (r) {
            toast("success", r.modified + " camera(s) modifiee(s)");
            return load();
        }).catch(function (e) { toast("error", e.message); });
    }
    function removeCams(list) {
        if (!list.length) return;
        var msg = list.length === 1 ? "Supprimer la camera " + list[0].name + " ?" : "Supprimer les " + list.length + " cameras selectionnees ?";
        confirmToast(msg + " Ses usages (LAPI, detection) seront retires.", "Supprimer").then(function (ok) {
            if (!ok) return;
            var p = list.length === 1
                ? send("DELETE", API.list + "/" + list[0]._id)
                : send("POST", API.bulk, { ids: list.map(function (c) { return c._id; }), delete: true });
            p.then(function () {
                list.forEach(function (c) { delete S.selected[c._id]; if (S.openId === c._id) closeDrawer(); });
                toast("success", list.length === 1 ? "Camera supprimee" : list.length + " cameras supprimees");
                load();
            }).catch(function (e) { toast("error", e.message); });
        });
    }

    /* ---------------- pilotage ---------------- */
    function capture(c, channel, target) {
        toast("info", c.cellular ? "Camera 4G : reveil et capture, jusqu'a une minute..." : "Capture en cours...");
        send("POST", API.list + "/" + c._id + "/capture" + (channel ? "?channel=" + channel : "")).then(function (blob) {
            var url = URL.createObjectURL(blob);
            if (target) { target.textContent = ""; target.appendChild(snapImg(url)); }
            else openLightbox(url);
            toast("success", "Capture reussie");
        }).catch(function (e) {
            var msg = e.message || "Camera injoignable";
            if (/timed out|timeout/i.test(msg)) msg = "Camera injoignable (delai depasse)";
            toast("error", msg);
        });
    }
    function wiper(c) {
        send("POST", API.list + "/" + c._id + "/action", { action: "wiper" }).then(function (r) {
            toast(r.ok ? "success" : "warning", r.ok ? "Essuie-glace active" : "La camera n'a pas accepte la commande");
        }).catch(function (e) { toast("error", e.message); });
    }
    function snapImg(url) {
        var im = document.createElement("img");
        im.src = url; im.alt = "Capture";
        im.addEventListener("click", function () { openLightbox(url); });
        im.addEventListener("error", function () { im.replaceWith(icon("no_photography")); });
        return im;
    }
    function openLightbox(src) { $("cm-lightbox-img").src = src; $("cm-lightbox").hidden = false; }
    function closeLightbox() { $("cm-lightbox").hidden = true; $("cm-lightbox-img").removeAttribute("src"); }

    /* ---------------- fiche ---------------- */
    function field(label, input, cls) {
        var l = el("label", "cm-field" + (cls ? " " + cls : ""));
        l.appendChild(document.createTextNode(label));
        l.appendChild(input);
        return l;
    }
    function inp(value, attrs) {
        var i = document.createElement("input");
        i.type = "text"; i.value = value == null ? "" : value;
        Object.keys(attrs || {}).forEach(function (k) { i.setAttribute(k, attrs[k]); });
        return i;
    }
    function sel(options, value) {
        var s = document.createElement("select");
        options.forEach(function (o) { s.appendChild(new Option(o[1], o[0])); });
        s.value = value == null ? "" : value;
        return s;
    }
    function section(title, ic) {
        var s = el("section");
        var h = el("h4", "cm-sec-title");
        h.appendChild(icon(ic));
        h.appendChild(document.createTextNode(title));
        s.appendChild(h);
        return s;
    }

    function openDrawer(c) {
        S.openId = c ? c._id : null;
        S.draft = JSON.parse(JSON.stringify(c || {
            name: "", ip: "", port: 80, protocol: "http", brand: "hikvision", model: "", firmware: "",
            site: "", location: "", notes: "", camera_path: "", enabled: true, credential_profile_id: "",
            channels: [{ no: 1, name: "", enabled: true, roles: [] }],
        }));
        var d = S.draft;
        $("cm-drawer").hidden = false;
        $("cm-drawer-title").textContent = c ? c.name : "Nouvelle camera";
        $("cm-drawer-delete").hidden = !c;
        var body = $("cm-drawer-body");
        body.textContent = "";
        body.scrollTop = 0;

        // Identite
        var s1 = section("Identite", "badge"), g1 = el("div", "cm-grid");
        var iName = inp(d.name, { maxlength: 120 }); iName.addEventListener("input", function () { d.name = iName.value; });
        g1.appendChild(field("Nom", iName, "cm-span2"));
        var iIp = inp(d.ip, { inputmode: "decimal", placeholder: "10.34.1.10" }); iIp.addEventListener("input", function () { d.ip = iIp.value.trim(); });
        g1.appendChild(field("Adresse IP", iIp));
        var iPort = inp(d.port, { inputmode: "numeric" }); iPort.addEventListener("input", function () { d.port = parseInt(iPort.value, 10) || 80; });
        g1.appendChild(field("Port", iPort));
        var sBrand = sel(Object.keys(S.meta.brands).map(function (k) { return [k, S.meta.brands[k]]; }), d.brand);
        sBrand.addEventListener("change", function () { d.brand = sBrand.value; });
        g1.appendChild(field("Marque", sBrand));
        var sProto = sel([["http", "http"], ["https", "https"]], d.protocol); sProto.addEventListener("change", function () { d.protocol = sProto.value; });
        g1.appendChild(field("Protocole", sProto));
        var iModel = inp(d.model); iModel.addEventListener("input", function () { d.model = iModel.value; });
        g1.appendChild(field("Modele", iModel));
        var iFw = inp(d.firmware); iFw.addEventListener("input", function () { d.firmware = iFw.value; });
        g1.appendChild(field("Firmware", iFw));
        var iSite = inp(d.site, { list: "cm-sites" }); iSite.addEventListener("input", function () { d.site = iSite.value; });
        g1.appendChild(field("Site", iSite));
        var iLoc = inp(d.location); iLoc.addEventListener("input", function () { d.location = iLoc.value; });
        g1.appendChild(field("Emplacement", iLoc));
        var ta = document.createElement("textarea"); ta.value = d.notes || ""; ta.addEventListener("input", function () { d.notes = ta.value; });
        g1.appendChild(field("Notes", ta, "cm-span2"));
        var en = el("label", "cm-field cm-check cm-span2"), cbEn = document.createElement("input");
        cbEn.type = "checkbox"; cbEn.checked = d.enabled !== false; cbEn.addEventListener("change", function () { d.enabled = cbEn.checked; });
        en.appendChild(cbEn); en.appendChild(document.createTextNode("Camera active"));
        g1.appendChild(en);
        s1.appendChild(g1);
        if (c && (c.group_path || c.vms_server)) s1.appendChild(el("p", "cm-help", "SeeTec : " + [c.group_path, c.vms_server].filter(Boolean).join(" - ")));
        body.appendChild(s1);

        // Identifiants
        var s2 = section("Identifiants", "key");
        var prOpts = [["", "-- aucun --"]].concat(S.meta.profiles.map(function (p) { return [p._id, p.name + (p.user ? " (" + p.user + ")" : "")]; }));
        var sProf = sel(prOpts, d.credential_profile_id);
        sProf.addEventListener("change", function () { d.credential_profile_id = sProf.value; });
        var row2 = el("div", "cm-inline");
        var fp = field("Profil d'identifiants", sProf); fp.style.flex = "1";
        row2.appendChild(fp);
        if (c && c.controllable) {
            var tb = el("button", "cm-btn"); tb.type = "button";
            tb.appendChild(icon("network_check")); tb.appendChild(document.createTextNode("Tester la connexion"));
            var res = el("span", "cm-result");
            tb.addEventListener("click", function () {
                res.className = "cm-result"; res.textContent = "Test en cours...";
                send("POST", API.list + "/" + c._id + "/test").then(function (r) {
                    if (r.ok) {
                        // Le serveur a deja enregistre modele et firmware : on les reporte dans
                        // le formulaire, sinon un "Enregistrer" ulterieur les ecrase par du vide
                        if (r.info.model) { d.model = r.info.model; iModel.value = r.info.model; }
                        if (r.info.firmwareVersion) { d.firmware = r.info.firmwareVersion; iFw.value = r.info.firmwareVersion; }
                        res.className = "cm-result is-ok";
                        res.textContent = "Connexion reussie : " + [r.info.model, r.info.firmwareVersion].filter(Boolean).join(", ");
                        if (r.channels_added && r.channels_added.length) {
                            toast("success", r.channels_added.length + " canal(aux) detecte(s) et ajoute(s) : " + r.channels_added.join(", "));
                            load().then(function () {
                                var nc = S.cams.filter(function (x) { return x._id === c._id; })[0];
                                if (nc) openDrawer(nc);
                            });
                        }
                    } else { res.className = "cm-result is-ko"; res.textContent = "Echec : " + (r.error || "inconnu"); }
                }).catch(function (e) { res.className = "cm-result is-ko"; res.textContent = e.message; });
            });
            row2.appendChild(tb);
            s2.appendChild(row2);
            s2.appendChild(res);
            s2.appendChild(el("p", "cm-help", "Enregistrez d'abord un changement de profil avant de tester. Sur une Hikvision, le test ajoute aussi les canaux manquants (cameras multi-capteurs)."));
        } else {
            s2.appendChild(row2);
            if (c) s2.appendChild(el("p", "cm-help", c.piste
                ? "Camera de piste : aucun pilotage depuis Cockpit (securite piste)."
                : "Pilotage non disponible pour cette marque : les identifiants sont conserves pour plus tard."));
        }
        body.appendChild(s2);

        // Evenements
        var s3 = section("Evenements envoyes a Cockpit", "sensors");
        var iPath = inp(d.camera_path, { placeholder: "/portehoux", maxlength: 61 });
        iPath.classList.add("cm-mono");
        iPath.addEventListener("input", function () { d.camera_path = iPath.value.trim(); renderPathHelp(); });
        s3.appendChild(field("Chemin d'envoi", iPath));
        var help = el("p", "cm-help");
        function renderPathHelp() {
            help.textContent = "";
            help.appendChild(document.createTextNode("Sur la camera, \"hote d'alarme HTTP\" : "));
            help.appendChild(el("code", "", S.meta.receiver + (d.camera_path || "/<chemin>")));
            help.appendChild(document.createTextNode(". Le chemin identifie l'appareil ; chaque evenement indique son canal. Requis pour les usages LAPI et detection."));
        }
        renderPathHelp();
        s3.appendChild(help);
        body.appendChild(s3);

        // Canaux et usages
        var s4 = section("Canaux et usages", "view_module");
        var chWrap = el("div");
        chWrap.style.cssText = "display:flex;flex-direction:column;gap:8px;";
        function renderChannels() {
            chWrap.textContent = "";
            d.channels.forEach(function (ch, idx) { chWrap.appendChild(channelBox(ch, idx)); });
        }
        function channelBox(ch, idx) {
            var box = el("div", "cm-channel" + (ch.enabled === false ? " is-off" : ""));
            var head = el("div", "cm-channel-head");
            head.appendChild(el("span", "cm-channel-no", String(ch.no)));
            var n = inp(ch.name, { maxlength: 80, placeholder: "Nom du canal", "aria-label": "Nom du canal " + ch.no });
            n.addEventListener("input", function () { ch.name = n.value; });
            head.appendChild(n);
            var lab = el("label", "cm-field cm-check"), cbc = document.createElement("input");
            cbc.type = "checkbox"; cbc.checked = ch.enabled !== false;
            cbc.addEventListener("change", function () { ch.enabled = cbc.checked; box.classList.toggle("is-off", !cbc.checked); });
            lab.appendChild(cbc); lab.appendChild(document.createTextNode("Actif"));
            head.appendChild(lab);
            if (d.channels.length > 1) {
                var rm = el("button", "cm-icon-btn"); rm.type = "button"; rm.title = "Retirer ce canal"; rm.appendChild(icon("remove_circle"));
                rm.addEventListener("click", function () { d.channels.splice(idx, 1); renderChannels(); });
                head.appendChild(rm);
            }
            box.appendChild(head);
            var roles = el("div", "cm-roles");
            S.meta.roles.forEach(function (spec) {
                var on = (ch.roles || []).some(function (r) { return r.type === spec.type; });
                var t = el("button", "cm-role-toggle" + (on ? " is-on" : ""));
                t.type = "button"; t.style.setProperty("--c", spec.color); t.title = spec.desc;
                t.appendChild(icon(on ? "check_circle" : spec.icon));
                t.appendChild(document.createTextNode(spec.label));
                t.addEventListener("click", function () {
                    if (on) ch.roles = ch.roles.filter(function (r) { return r.type !== spec.type; });
                    else {
                        var r = { type: spec.type };
                        spec.params.forEach(function (p) { r[p.key] = p.default; });
                        ch.roles = (ch.roles || []).concat([r]);
                    }
                    renderChannels();
                });
                roles.appendChild(t);
            });
            box.appendChild(roles);
            (ch.roles || []).forEach(function (r) {
                var spec = roleSpec(r.type);
                if (!spec || !spec.params.length) return;
                var pg = el("div", "cm-role-params");
                pg.style.setProperty("--c", spec.color);
                var tt = el("div", "cm-role-title"); tt.appendChild(icon(spec.icon)); tt.appendChild(document.createTextNode(spec.label));
                pg.appendChild(tt);
                spec.params.forEach(function (p) {
                    var input;
                    if (p.kind === "bool") {
                        var l = el("label", "cm-field cm-check"); input = document.createElement("input");
                        input.type = "checkbox"; input.checked = r[p.key] !== false;
                        input.addEventListener("change", function () { r[p.key] = input.checked; });
                        l.appendChild(input); l.appendChild(document.createTextNode(p.label));
                        pg.appendChild(l);
                        return;
                    }
                    if (p.kind === "select") input = sel(p.options, r[p.key]);
                    else if (p.kind === "lieu") input = sel([["", "--"]].concat((S.meta.lieux || []).map(function (x) { return [x, x]; })), r[p.key]);
                    else input = inp(r[p.key]);
                    input.addEventListener("change", function () { r[p.key] = input.value; });
                    pg.appendChild(field(p.label, input));
                });
                box.appendChild(pg);
            });
            return box;
        }
        renderChannels();
        s4.appendChild(chWrap);
        var add = el("button", "cm-btn"); add.type = "button"; add.style.marginTop = "8px";
        add.appendChild(icon("add")); add.appendChild(document.createTextNode("Ajouter un canal"));
        add.addEventListener("click", function () {
            var next = d.channels.reduce(function (m, ch) { return Math.max(m, ch.no); }, 0) + 1;
            d.channels.push({ no: next, name: "", enabled: true, roles: [] });
            renderChannels();
        });
        s4.appendChild(add);
        body.appendChild(s4);

        // Apercu
        if (c && c.controllable) {
            var s5 = section("Apercu", "photo_camera");
            var bar = el("div", "cm-inline");
            var sCh = sel(d.channels.map(function (ch) { return [ch.no, ch.no + " - " + (ch.name || "Canal " + ch.no)]; }), d.channels[0].no);
            sCh.style.cssText = "height:34px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:.82rem;padding:0 8px;flex:1;min-width:0;";
            bar.appendChild(sCh);
            var cap = el("button", "cm-btn"); cap.type = "button"; cap.disabled = !S.meta.key_ok || !c.credential_profile_id;
            cap.appendChild(icon("photo_camera")); cap.appendChild(document.createTextNode("Capturer"));
            bar.appendChild(cap);
            if (c.wiper) {
                var wb = el("button", "cm-btn"); wb.type = "button"; wb.disabled = cap.disabled;
                wb.appendChild(icon("water_drop")); wb.appendChild(document.createTextNode("Essuie-glace"));
                wb.addEventListener("click", function () { wiper(c); });
                bar.appendChild(wb);
            }
            s5.appendChild(bar);
            // Presets : seul moyen de changer la vue (pas de deplacement libre)
            var pbar = el("div", "cm-inline");
            pbar.style.marginTop = "8px";
            var sPre = sel([["", "Chargement des presets..."]], "");
            sPre.style.cssText = sCh.style.cssText;
            sPre.disabled = true;
            var gb = el("button", "cm-btn"); gb.type = "button"; gb.disabled = true;
            gb.appendChild(icon("switch_video")); gb.appendChild(document.createTextNode("Changer de vue"));
            pbar.appendChild(sPre); pbar.appendChild(gb);
            s5.appendChild(pbar);
            function loadPresets() {
                sPre.textContent = ""; sPre.appendChild(new Option("Chargement des presets...", ""));
                sPre.disabled = true; gb.disabled = true;
                if (cap.disabled) { sPre.options[0].text = "Presets indisponibles (identifiants)"; return; }
                getJSON(API.list + "/" + c._id + "/presets" + (+sCh.value !== 1 ? "?channel=" + sCh.value : "")).then(function (r) {
                    sPre.textContent = "";
                    if (!r.presets.length) { sPre.appendChild(new Option("Aucun preset sur cette camera", "")); return; }
                    sPre.appendChild(new Option("-- choisir une vue --", ""));
                    r.presets.forEach(function (p) { sPre.appendChild(new Option(p.id + " - " + p.name, p.id)); });
                    sPre.disabled = false;
                }).catch(function () {
                    sPre.textContent = ""; sPre.appendChild(new Option("Pas de presets (camera fixe ou injoignable)", ""));
                });
            }
            sPre.addEventListener("change", function () { gb.disabled = !sPre.value; });
            gb.addEventListener("click", function () {
                var label = sPre.options[sPre.selectedIndex].text;
                confirmToast("La camera va pivoter vers \"" + label + "\". La vue change aussi pour les operateurs SeeTec.", "Changer de vue").then(function (ok) {
                    if (!ok) return;
                    gb.disabled = true;
                    send("POST", API.list + "/" + c._id + "/preset", { preset: +sPre.value, channel: +sCh.value }).then(function () {
                        toast("success", "Vue demandee : " + label + ". Capture dans quelques secondes...");
                        setTimeout(function () { capture(c, +sCh.value, snap); gb.disabled = false; }, 4000);
                    }).catch(function (e) { toast("error", e.message); gb.disabled = false; });
                });
            });
            var snap = el("div", "cm-snap");
            snap.style.marginTop = "8px";
            function showLatest() {
                snap.textContent = "";
                var url = API.list + "/" + c._id + "/snapshot" + (+sCh.value !== 1 ? "?channel=" + sCh.value : "");
                var im = document.createElement("img");
                im.alt = "Derniere capture";
                im.addEventListener("load", function () { if (!im.naturalWidth) im.replaceWith(icon("no_photography")); });
                im.addEventListener("error", function () { im.replaceWith(icon("no_photography")); });
                im.addEventListener("click", function () { openLightbox(im.src); });
                im.src = url + (url.indexOf("?") < 0 ? "?" : "&") + "t=" + Date.now();
                snap.appendChild(im);
            }
            sCh.addEventListener("change", function () { showLatest(); loadPresets(); });
            cap.addEventListener("click", function () { capture(c, +sCh.value, snap); });
            showLatest();
            loadPresets();
            s5.appendChild(snap);
            // Apercu en tete de fiche : visible sans faire defiler
            body.insertBefore(s5, body.firstChild);
        }
        render();
        // Pas de defilement vers le champ : l'apercu reste visible en haut
        if (!c) iName.focus({ preventScroll: true });
    }

    function closeDrawer() {
        $("cm-drawer").hidden = true;
        S.openId = null; S.draft = null;
        render();
    }

    function saveDrawer() {
        var d = S.draft;
        if (!d) return;
        // Appareil a canal unique renomme : le canal portait l'ancien nom, il suit
        // (c'est ce nom qu'affichent l'apercu et le selecteur de capture)
        var orig = S.openId ? S.cams.filter(function (x) { return x._id === S.openId; })[0] : null;
        if (orig && d.channels.length === 1 && d.name !== orig.name
            && (d.channels[0].name || "") === (orig.channels && orig.channels[0] ? orig.channels[0].name || "" : "")
            && (!d.channels[0].name || d.channels[0].name === orig.name)) {
            d.channels[0].name = d.name;
        }
        var payload = {
            name: d.name, ip: d.ip, port: d.port, protocol: d.protocol, brand: d.brand, model: d.model,
            firmware: d.firmware, site: d.site, location: d.location, notes: d.notes, enabled: d.enabled !== false,
            camera_path: d.camera_path || "", credential_profile_id: d.credential_profile_id || "",
            channels: d.channels,
        };
        // Source "consultation" (Axis LPV) : Cockpit va chercher les lectures, pas de chemin d'envoi
        var needsPath = d.channels.some(function (ch) {
            return (ch.roles || []).some(function (r) {
                var s = roleSpec(r.type);
                return s && s.needs_path && (s.no_path_sources || []).indexOf(r.source) < 0;
            });
        });
        if (needsPath && !payload.camera_path) { toast("warning", "Un usage LAPI ou detection demande un chemin d'envoi"); return; }
        var btn = $("cm-drawer-save");
        btn.disabled = true;
        var p = S.openId ? send("PUT", API.list + "/" + S.openId, payload) : send("POST", API.list, payload);
        p.then(function (c) {
            var gs = c.geo_sync;
            toast("success", "Camera enregistree" + (gs && (gs.updated || gs.created)
                ? " - couche GPS : " + gs.updated + " point(s) mis a jour" + (gs.created ? ", " + gs.created + " cree(s)" : "") : ""));
            if (S.view === "map") loadMapData().then(render);
            return load().then(closeDrawer);
        }).catch(function (e) { toast("error", e.message); }).then(function () { btn.disabled = false; });
    }

    /* ---------------- profils d'identifiants ---------------- */
    function openProfiles() {
        renderProfiles();
        var dlg = $("cm-profiles-dialog");
        if (!dlg.open) dlg.showModal();
    }
    function renderProfiles(extraNew) {
        var list = $("cm-profiles-list");
        list.textContent = "";
        var items = S.meta.profiles.slice();
        if (extraNew) items.push({ _id: "", name: "", user: "admin", has_password: false, devices: 0, note: "" });
        if (!items.length) list.appendChild(el("p", "cm-help", "Aucun profil."));
        items.forEach(function (p) {
            var box = el("div", "cm-profile");
            var iN = inp(p.name, { maxlength: 60, placeholder: "Ex. Hikvision standard" });
            var iU = inp(p.user, { maxlength: 60, autocomplete: "off" });
            var iP = inp("", { type: "password", autocomplete: "new-password", placeholder: p.has_password ? "inchange (defini)" : "a definir" });
            iP.type = "password";
            box.appendChild(field("Nom", iN));
            box.appendChild(field("Utilisateur", iU));
            box.appendChild(field("Mot de passe", iP));
            var acts = el("div", "cm-profile-actions");
            var sv = el("button", "cm-btn cm-btn-primary"); sv.type = "button"; sv.textContent = p._id ? "Enregistrer" : "Creer";
            sv.addEventListener("click", function () {
                var body = { name: iN.value.trim(), user: iU.value.trim() };
                if (iP.value) body.password = iP.value;
                var req = p._id ? send("PUT", API.profiles + "/" + p._id, body) : send("POST", API.profiles, body);
                req.then(function () {
                    iP.value = "";
                    toast("success", "Profil enregistre");
                    return load().then(function () { renderProfiles(); });
                }).catch(function (e) { toast("error", e.message); });
            });
            acts.appendChild(sv);
            if (p._id) {
                var del = el("button", "cm-icon-btn"); del.type = "button"; del.title = p.devices ? "Utilise par " + p.devices + " camera(s)" : "Supprimer";
                del.disabled = p.devices > 0; del.appendChild(icon("delete"));
                del.addEventListener("click", function () {
                    confirmToast("Supprimer le profil " + p.name + " ?", "Supprimer").then(function (ok) {
                        if (!ok) return;
                        send("DELETE", API.profiles + "/" + p._id).then(function () {
                            toast("success", "Profil supprime");
                            return load().then(function () { renderProfiles(); });
                        }).catch(function (e) { toast("error", e.message); });
                    });
                });
                acts.appendChild(del);
            }
            box.appendChild(acts);
            var meta = el("div", "cm-profile-meta");
            meta.appendChild(el("span", "", p._id ? p.devices + " camera(s)" : "Nouveau profil"));
            meta.appendChild(el("span", "", p.has_password ? "Mot de passe defini (chiffre)" : "Pas de mot de passe"));
            if (p.note) meta.appendChild(el("span", "", p.note));
            box.appendChild(meta);
            list.appendChild(box);
        });
    }

    /* ---------------- evenements ---------------- */
    // Nombre de filtres actifs sur l'onglet "Filtres" de la barre du bas
    function mnavBadge() {
        var n = ["site", "brand", "usage", "state"].filter(function (k) { return S.f[k]; }).length;
        var b = $("cm-mnav-badge");
        if (!b) return;
        b.hidden = !n;
        b.textContent = n || "";
    }
    function bind() {
        var qt = null;
        $("cm-q").addEventListener("input", function () {
            clearTimeout(qt);
            var v = this.value;
            qt = setTimeout(function () { S.f.q = v.trim(); render(); }, 150);
        });
        [["cm-f-site", "site"], ["cm-f-brand", "brand"], ["cm-f-usage", "usage"], ["cm-f-state", "state"]].forEach(function (x) {
            $(x[0]).addEventListener("change", function () { S.f[x[1]] = this.value; render(); mnavBadge(); });
        });
        // Telephone : barre du bas (les boutons du haut y passent) et volet des filtres
        var app = document.querySelector(".cm-app");
        function setFilters(open) {
            app.classList.toggle("is-filters-open", open);
            document.querySelector('#cm-mnav [data-mnav="filters"]').classList.toggle("is-on", open);
        }
        Array.prototype.forEach.call(document.querySelectorAll("#cm-mnav [data-mnav]"), function (b) {
            b.addEventListener("click", function () {
                var a = b.dataset.mnav;
                if (a === "list" || a === "map") { setFilters(false); if (S.view !== a) setView(a); }
                else if (a === "filters") setFilters(!app.classList.contains("is-filters-open"));
                else if (a === "add") { setFilters(false); openDrawer(null); }
                else if (a === "profiles") openProfiles();
            });
        });
        $("cm-filters-done").addEventListener("click", function () { setFilters(false); });
        $("cm-filters-reset").addEventListener("click", function () {
            [["cm-f-site", "site"], ["cm-f-brand", "brand"], ["cm-f-usage", "usage"], ["cm-f-state", "state"]].forEach(function (x) {
                $(x[0]).value = ""; S.f[x[1]] = "";
            });
            render(); mnavBadge();
        });
        Array.prototype.forEach.call(document.querySelectorAll("#cm-table th[data-sort]"), function (th) {
            th.addEventListener("click", function () {
                if (S.sort.key === th.dataset.sort) S.sort.desc = !S.sort.desc;
                else { S.sort.key = th.dataset.sort; S.sort.desc = false; }
                render();
            });
        });
        $("cm-check-all").addEventListener("change", function () {
            var on = this.checked;
            filtered().forEach(function (c) { if (on) S.selected[c._id] = true; else delete S.selected[c._id]; });
            render();
        });
        Array.prototype.forEach.call(document.querySelectorAll("[data-bulk]"), function (b) {
            b.addEventListener("click", function () { bulk(b.dataset.bulk); });
        });
        $("cm-add-btn").addEventListener("click", function () { openDrawer(null); });
        $("cm-drawer-close").addEventListener("click", closeDrawer);
        $("cm-drawer-cancel").addEventListener("click", closeDrawer);
        $("cm-drawer-save").addEventListener("click", saveDrawer);
        $("cm-drawer-delete").addEventListener("click", function () {
            var c = S.cams.filter(function (x) { return x._id === S.openId; })[0];
            if (c) removeCams([c]);
        });
        $("cm-profiles-btn").addEventListener("click", openProfiles);
        $("cm-map-btn").addEventListener("click", function () { setView(S.view === "map" ? "list" : "map"); });
        $("cm-capture-all").addEventListener("click", startCaptureAll);
        $("cm-map-edit").addEventListener("click", function () { setMapEdit(!M.edit); });
        $("cm-map-place").addEventListener("change", onPlaceSelect);
        $("cm-profile-add").addEventListener("click", function () { renderProfiles(true); });
        Array.prototype.forEach.call(document.querySelectorAll("#cm-profiles-dialog [data-close]"), function (b) {
            b.addEventListener("click", function () { $("cm-profiles-dialog").close(); });
        });
        $("cm-lightbox").addEventListener("click", closeLightbox);
        document.addEventListener("keydown", function (e) {
            if (e.key !== "Escape") return;
            if (!$("cm-lightbox").hidden) closeLightbox();
            else if (!$("cm-drawer").hidden && !document.querySelector("dialog[open]")) closeDrawer();
        });
        document.addEventListener("visibilitychange", function () { if (!document.hidden) loadReach(); });
    }

    function init() {
        bind();
        var usage = new URLSearchParams(location.search).get("usage");
        load().then(function () {
            if (usage) { S.f.usage = usage; $("cm-f-usage").value = usage; render(); mnavBadge(); }
            loadReach();
            var v = null;
            try { v = localStorage.getItem("cockpit-cameras-view"); } catch (e) {}
            if (v === "map" || new URLSearchParams(location.search).get("view") === "map") setView("map");
        });
        setInterval(loadReach, REACH_MS);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
