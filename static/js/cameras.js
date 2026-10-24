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
        planned: "/api/cameras/planned",
        dossier: "/api/cameras/planned/dossier.pdf",
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
    function sinceText(iso) {
        var d = new Date(iso), m = (Date.now() - d.getTime()) / 60000;
        var hm = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
        if (m < 60) return hm + " (" + Math.max(1, Math.round(m)) + " min)";
        if (m < 1440) return hm + " (" + Math.floor(m / 60) + " h " + String(Math.round(m % 60)).padStart(2, "0") + ")";
        return d.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) + " (" + Math.floor(m / 1440) + " j)";
    }
    function pct(v) { return v == null ? "-" : (Math.floor(v * 1000) / 10).toString().replace(".", ",") + " %"; }
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
            $("cm-privacy-all-btn").hidden = !S.meta.super_admin;
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
        // Mode "En projet" : aucune camera reelle, seules les cameras en projet (carte)
        if (f.state === "planned") return [];
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
            var planned = S.f.state === "planned";
            var tr = el("tr"), td = el("td", "cm-empty", planned ? "Les cameras en projet s'affichent sur la carte. " : "Aucune camera ne correspond.");
            if (planned) {
                var go = el("button", "cm-btn", "Voir la carte"); go.type = "button";
                go.addEventListener("click", function () { setView("map"); });
                td.appendChild(go);
            }
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
        M.editLayer = L.layerGroup().addTo(M.map);
        M.map.on("click", onMapClick);
        if (window.ResizeObserver) new ResizeObserver(function () { M.map.invalidateSize(); }).observe($("cm-map"));
    }
    function loadMapData() {
        return Promise.all([getJSON("/api/cameras/map"), getJSON(API.planned).catch(function () { return []; })]).then(function (res) {
            var r = res[0];
            M.pos = {};
            r.cameras.forEach(function (c) { M.pos[c._id] = c; });
            M.unplaced = r.unplaced;
            M.planned = res[1];
            rebuildPlanned();
            M.loaded = true;
        }).catch(function (e) { toast("error", "Carte indisponible : " + e.message); });
    }

    /* ---------------- cameras en projet ---------------- */
    // Emplacements souhaites (travaux a venir) : collection cameras_planned,
    // jamais melangee aux vraies cameras. Representees comme une camera a un
    // canal ("p:<id>") pour reutiliser le volet du configurateur (optique, cone).
    var PLANNED_COLOR = "#8b5cf6";
    var PLANNED_STATUS = [["souhaitee", "Souhaitee"], ["validee", "Validee"], ["commandee", "Commandee"]];
    // Support de pose (cameras.PLANNED_MOUNTS) : aide a la constitution du dossier travaux
    var PLANNED_MOUNTS = [["", "A definir"], ["mat", "Mat"], ["angle_mur", "Angle de mur"], ["mur_plat", "Mur plat"]];
    var REPLACE_RADIUS_M = 500;
    function plannedCam(p) {
        return { _id: "p:" + p._id, pid: p._id, planned: true, rec: p, name: p.name, enabled: true, channels: plannedChannels(p) };
    }
    function plannedChannels(p) {
        return (p.channels || []).map(function (k) {
            return { no: k.no, name: "", enabled: true, optics: k.optics, optics_src: k.optics ? "manuel" : "", model: k.model, view: k.view };
        });
    }
    // "1 mobile + 4 fixes"
    function plannedMix(chs) {
        var m = chs.filter(function (k) { return k.optics === "mobile"; }).length;
        var f = chs.filter(function (k) { return k.optics === "fixe"; }).length;
        var u = chs.length - m - f;
        return [m ? m + " mobile" + (m > 1 ? "s" : "") : "", f ? f + " fixe" + (f > 1 ? "s" : "") : "",
            u ? u + " a definir" : ""].filter(Boolean).join(" + ");
    }
    function rebuildPlanned() {
        M.plannedCams = (M.planned || []).map(plannedCam);
        M.plannedCams.forEach(function (pc) { M.pos[pc._id] = { lat: pc.rec.lat, lng: pc.rec.lng }; });
    }
    function plannedById(id) { return (M.plannedCams || []).filter(function (x) { return x._id === id; })[0]; }
    // Remplace l'enregistrement local par la reponse du serveur
    function storePlanned(rec) {
        M.planned = (M.planned || []).filter(function (p) { return p._id !== rec._id; }).concat([rec]);
        rebuildPlanned();
        return plannedById("p:" + rec._id);
    }
    function statusLabel(s) { return (PLANNED_STATUS.filter(function (x) { return x[0] === s; })[0] || ["", s])[1]; }
    function plannedTip(pc) {
        var r = pc.rec, box = el("div", "cm-tip cm-tip-planned");
        box.appendChild(el("div", "cm-tip-badge", "Camera en projet - " + statusLabel(r.status)));
        box.appendChild(el("b", "", (r.priority ? "P" + r.priority + " - " : "") + r.name));
        if (r.moves) box.appendChild(el("div", "cm-tip-line", "Deplacement de " + r.moves.name));
        if ((r.replaces || []).length) box.appendChild(el("div", "cm-tip-line", "Remplace " + r.replaces.map(function (x) { return x.name; }).join(", ")));
        var mnt = (PLANNED_MOUNTS.filter(function (m) { return m[0] === r.mount; })[0] || [])[1];
        if (r.mount) box.appendChild(el("div", "cm-tip-line", "Pose : " + mnt + (r.mount_height ? ", " + String(r.mount_height).replace(".", ",") + " m" : "")));
        if (r.note) box.appendChild(el("div", "cm-tip-line", r.note));
        var drawn = pc.channels.filter(function (k) { return k.view; }).length;
        box.appendChild(el("div", "cm-tip-line", plannedMix(pc.channels) + " - " +
            (drawn === pc.channels.length ? "angle(s) de vue dessine(s)" : drawn + " / " + pc.channels.length + " angle(s) de vue dessine(s)")));
        box.appendChild(el("div", "cm-tip-line cm-tip-muted", "Ajoutee" + (r.by ? " par " + r.by : "") + (r.at ? " le " + new Date(r.at).toLocaleDateString("fr-FR") : "")));
        return box;
    }
    function drawPlannedLinks(pc) {
        var p = M.pos[pc._id], r = pc.rec;
        (r.replaces || []).forEach(function (x) {
            var q = M.pos[x._id];
            if (!q) return;
            L.polyline([[q.lat, q.lng], [p.lat, p.lng]], { color: "#dc2626", weight: 1.5, dashArray: "3 4", opacity: .8, interactive: false }).addTo(M.layer);
            var cross = el("div", "cm-pin-removed");
            cross.appendChild(icon("close"));
            L.marker([q.lat, q.lng], { icon: L.divIcon({ html: cross, className: "", iconSize: [18, 18], iconAnchor: [9, 9] }),
                interactive: false, zIndexOffset: 400 }).addTo(M.layer);
        });
        if (r.moves && M.pos[r.moves._id]) {
            var o = M.pos[r.moves._id];
            L.polyline([[o.lat, o.lng], [p.lat, p.lng]], { color: "#ea580c", weight: 2.5, dashArray: "6 5", interactive: false }).addTo(M.layer);
            L.circleMarker([o.lat, o.lng], { radius: 6, color: "#ea580c", weight: 2, fillColor: "#fff", fillOpacity: 1, interactive: false }).addTo(M.layer);
        }
    }
    function drawPlanned() {
        (M.plannedCams || []).forEach(function (pc) {
            var p = M.pos[pc._id];
            drawPlannedLinks(pc);
            var editing = M.coneEdit && M.coneEdit.c._id === pc._id;
            pc.channels.forEach(function (ch) {
                if (!ch.view || editing && +M.coneEdit.no === +ch.no) return;
                coneLayer(p.lat, p.lng, ch.view, "planned", { dashArray: "5 4" }).addTo(M.layer);
            });
            var dot = el("div", "cm-pin is-planned" + (editing ? " is-cone-edit is-draggable" : ""));
            dot.appendChild(icon("add_a_photo"));
            // Deplacable seulement pendant son edition (volet ouvert)
            var mk = L.marker([p.lat, p.lng], { icon: L.divIcon({ html: dot, className: "", iconSize: [28, 28], iconAnchor: [14, 14] }),
                draggable: editing, autoPan: true, zIndexOffset: 500 });
            mk.bindTooltip(plannedTip(pc), { direction: "top", offset: [0, -14], className: "cm-tip-wrap", opacity: 1 });
            mk.on("click", function () { if (!M.edit) openPlanned(pc); });
            mk.on("dragstart", function () { mk.closeTooltip(); });
            mk.on("dragend", function () { movePlanned(pc, mk.getLatLng()); });
            mk.addTo(M.layer);
        });
    }
    function openPlanned(pc) {
        if (!M.cones && !setConeMode(true)) return;
        M.tour = null;
        openCone(pc);
    }
    function movePlanned(pc, to) {
        send("PUT", API.planned + "/" + pc.pid, { lat: to.lat, lng: to.lng }).then(function (r) {
            var p = M.pos[pc._id];
            p.lat = r.lat; p.lng = r.lng;  // meme objet : le cone en edition suit
            pc.rec.lat = r.lat; pc.rec.lng = r.lng;
            if (M.coneEdit && M.coneEdit.c === pc) drawConeEdit();
            render();
        }).catch(function (e) { toast("error", e.message); render(); });
    }
    function setAddPlanned(on) {
        if (on && M.edit) setMapEdit(false);
        M.addPlanned = on;
        $("cm-map-plan").classList.toggle("is-on", on);
        $("cm-map-plan").setAttribute("aria-pressed", on ? "true" : "false");
        $("cm-map").classList.toggle("is-placing", on);
        if (on) toast("info", "Cliquez sur la carte a l'emplacement de la camera souhaitee");
    }
    function addPlannedAt(latlng) {
        setAddPlanned(false);
        send("POST", API.planned, { lat: latlng.lat, lng: latlng.lng }).then(function (r) {
            openPlanned(storePlanned(r));
            toast("success", r.name + " ajoutee : nommez-la et dessinez son angle de vue");
        }).catch(function (e) { toast("error", e.message); });
    }
    function placed(list) { return list.filter(function (c) { return M.pos[c._id]; }); }
    function capturable(c) { return c.enabled && c.controllable && c.credential_profile_id; }
    // Canaux actifs captures par "Capturer" (meme regle que cameras._capture_channels)
    function activeChannels(c) {
        return (c.channels || []).filter(function (ch) { return ch.enabled !== false; }).length || 1;
    }
    function fitPins() {
        var pts = (S.f.state === "planned" ? (M.plannedCams || []) : placed(filtered())).map(function (c) { return [M.pos[c._id].lat, M.pos[c._id].lng]; });
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
        // Angles de vue enregistres, sous les pins (sauf celui en cours d'edition)
        pins.forEach(function (c) {
            var p = M.pos[c._id];
            (c.channels || []).forEach(function (ch) {
                if (ch.enabled === false || !ch.view) return;
                if (M.coneEdit && M.coneEdit.c._id === c._id && +M.coneEdit.no === +ch.no) return;
                coneLayer(p.lat, p.lng, ch.view, ch.optics).addTo(M.layer);
            });
        });
        pins.forEach(function (c) {
            var p = M.pos[c._id], st = reachOf(c);
            var dot = el("div", "cm-pin is-" + (st === "ko" && c.cellular ? "sleep" : st || "na"));
            dot.appendChild(icon(c.piste ? "lock" : "videocam"));
            if (M.edit) dot.classList.add("is-draggable");
            if (M.cones && coneTodo(c).length) dot.classList.add("is-todo");
            if (M.coneEdit && M.coneEdit.c._id === c._id) dot.classList.add("is-cone-edit");
            var mk = L.marker([p.lat, p.lng], { icon: L.divIcon({ html: dot, className: "", iconSize: [28, 28], iconAnchor: [14, 14] }),
                draggable: !!M.edit, autoPan: true });
            var tip = camTip(c);
            mk.bindTooltip(tip, { direction: "top", offset: [0, -14], className: "cm-tip-wrap", opacity: 1 });
            mk.on("tooltipopen", function () { tip._loadShot(); });
            mk.on("click", function () {
                if (M.cones) openCone(c);
                else if (!M.edit) openDrawer(c);
            });
            mk.on("dragstart", function () { mk.closeTooltip(); });
            mk.on("dragend", function () { moveCam(c, mk); });
            mk.addTo(M.layer);
        });
        drawPlanned();
        fillPlaceSelect(list);
        // Recadrage seulement a l'ouverture de la carte et quand les FILTRES
        // changent : placer, deplacer une camera ou un test reseau ne bougent
        // pas la vue (on reste ou l'on vient de poser)
        var key = JSON.stringify(S.f);
        if (key !== M.lastKey) { M.lastKey = key; fitPins(); }
        if (M.cones) {
            var mob = 0, mobDone = 0, all = 0;
            pins.forEach(function (c) {
                (c.channels || []).forEach(function (ch) {
                    if (ch.enabled === false) return;
                    if (ch.view) all++;
                    if (ch.optics === "mobile") { mob++; if (ch.view) mobDone++; }
                });
            });
            $("cm-map-hint").textContent = "Angles de vue : " + mobDone + " / " + mob + " canaux mobiles dessines (" + all +
                " au total). Cliquez une camera ; pointilles orange = canal mobile sans angle de vue.";
        }
        var n = pins.filter(capturable).length;
        var nch = pins.filter(capturable).reduce(function (a, c) { return a + activeChannels(c); }, 0);
        $("cm-map-count").textContent = S.f.state === "planned" ? (M.planned || []).length + " camera(s) en projet" : pins.length + " camera(s) sur la carte" +
            (list.length > pins.length ? ", " + (list.length - pins.length) + " sans position" : "") +
            ((M.planned || []).length ? ", " + M.planned.length + " en projet" : "");
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
        if (on && M.cones) { if (!setConeMode(false)) return; }
        if (on && M.addPlanned) setAddPlanned(false);
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
        if (M.addPlanned) { addPlannedAt(e.latlng); return; }
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

    /* ---------------- angles de vue ---------------- */
    // Un cone par canal, dessine a la main d'apres la capture : az = degres
    // depuis le nord (sens horaire), fov = ouverture, range = portee en m.
    // Enregistre dans cockpit_cameras (PUT /channels/<no>/view) : rien n'est
    // envoye aux cameras ni a la couche GPS.
    var CONE_COLORS = { mobile: "#f97316", fixe: "#3b82f6", planned: "#8b5cf6" };
    function destPoint(lat, lng, az, dist) {
        var r = az * Math.PI / 180;
        return [lat + dist * Math.cos(r) / 111320, lng + dist * Math.sin(r) / (111320 * Math.cos(lat * Math.PI / 180))];
    }
    function bearingDist(lat, lng, to) {
        var dy = (to.lat - lat) * 111320;
        var dx = (to.lng - lng) * 111320 * Math.cos(lat * Math.PI / 180);
        return { az: (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360, d: Math.sqrt(dx * dx + dy * dy) };
    }
    function conePoints(lat, lng, v) {
        var pts = v.fov >= 360 ? [] : [[lat, lng]];
        var n = Math.max(8, Math.round(v.fov / 4));
        for (var i = 0; i <= n; i++) pts.push(destPoint(lat, lng, v.az - v.fov / 2 + v.fov * i / n, v.range));
        return pts;
    }
    function coneLayer(lat, lng, v, optics, extra) {
        var col = CONE_COLORS[optics] || "#64748b";
        var opts = { color: col, weight: 1.5, fillColor: col, fillOpacity: .18, interactive: false };
        Object.keys(extra || {}).forEach(function (k) { opts[k] = extra[k]; });
        return L.polygon(conePoints(lat, lng, v), opts);
    }
    function liveChannels(c) {
        var chs = (c.channels || []).filter(function (ch) { return ch.enabled !== false; });
        return chs.length ? chs : (c.channels || []);
    }
    // Canaux mobiles sans angle de vue (pin entoure en mode Angles de vue)
    function coneTodo(c) {
        return liveChannels(c).filter(function (ch) { return ch.optics === "mobile" && !ch.view; });
    }
    function optLabel(ch) {
        return ch.optics === "mobile" ? "mobile" : ch.optics === "fixe" ? "fixe" : "optique ?";
    }

    // Renvoie false si l'utilisateur garde ses modifications en cours
    function setConeMode(on) {
        if (!on && M.coneEdit && M.coneEdit.dirty) {
            confirmToast("Abandonner l'angle de vue en cours de dessin ?", "Abandonner").then(function (ok) {
                if (ok) { M.coneEdit.dirty = false; setConeMode(false); }
            });
            return false;
        }
        if (on && M.edit) setMapEdit(false);
        M.cones = on;
        $("cm-map-cones").classList.toggle("is-on", on);
        $("cm-map-cones").setAttribute("aria-pressed", on ? "true" : "false");
        $("cm-map-hint").hidden = !on;
        if (!on) { M.tour = null; closeCone(true); }
        render();
        return true;
    }

    /* Configurateur : parcours camera par camera, canal par canal. Pour chaque
       canal : capture fraiche automatique (la suivante est preparee pendant
       qu'on dessine), optique Fixe / Mobile enregistree d'un clic, cone a
       regler, "Enregistrer et suivant". Raccourcis : Entree, F, M, fleches, C. */
    function camById(id) { return S.cams.filter(function (x) { return x._id === id; })[0]; }
    function chOf(c, no) { return (c.channels || []).filter(function (x) { return +x.no === +no; })[0]; }
    function startTour() {
        var cams = placed(filtered()).filter(function (c) { return c.enabled; }).sort(function (a, b) {
            return a.name.localeCompare(b.name, "fr", { numeric: true });
        });
        function build(all) {
            var items = [];
            cams.forEach(function (c) {
                liveChannels(c).forEach(function (ch) { if (all || !ch.view) items.push({ id: c._id, no: ch.no }); });
            });
            return items;
        }
        var items = build(false);
        var go = function (list, label) {
            if (!list.length) { toast("info", "Aucun canal a parcourir (filtres de la liste appliques)"); return; }
            if (!M.cones && !setConeMode(true)) return;
            M.tour = { items: list, i: 0, label: label };
            openCone(camById(list[0].id), list[0].no);
        };
        if (items.length) { go(items, "a dessiner"); return; }
        confirmToast("Tous les canaux affiches ont deja un angle de vue. Les revoir tous ?", "Revoir").then(function (ok) {
            if (ok) go(build(true), "revue");
        });
    }
    function tourStep(delta) {
        var T = M.tour;
        if (!T) return;
        var i = T.i + delta;
        if (i >= T.items.length) {
            M.tour = null;
            closeCone(true);
            toast("success", "Parcours termine");
            return;
        }
        if (i < 0) return;
        T.i = i;
        if (M.coneEdit) M.coneEdit.dirty = false;  // "Passer" abandonne le dessin en cours
        openCone(camById(T.items[i].id), T.items[i].no);
    }

    function openCone(c, no) {
        if (!c) return;
        if (M.coneEdit && M.coneEdit.dirty && !(M.coneEdit.c._id === c._id && +M.coneEdit.no === +no)) {
            confirmToast("Abandonner l'angle de vue en cours de dessin ?", "Abandonner").then(function (ok) {
                if (ok) { M.coneEdit.dirty = false; openCone(c, no); }
            });
            return;
        }
        var chs = liveChannels(c);
        var ch = (no && chs.filter(function (x) { return +x.no === +no; })[0]) || coneTodo(c)[0]
            || chs.filter(function (x) { return !x.view; })[0] || chs[0];
        if (!ch) return;
        if (M.tour) {
            var k = -1;
            M.tour.items.forEach(function (it, j) { if (it.id === c._id && +it.no === +ch.no) k = j; });
            if (k >= 0) M.tour.i = k;
        }
        M.coneEdit = { c: c, no: ch.no, ch: ch, v: defaultView(c, ch), dirty: false };
        $("cm-map-area").classList.add("is-cone-open");
        buildConePanel();
        drawConeEdit();
        render();
        frameCone();
        showShot();
    }
    function defaultView(c, ch) {
        if (ch.view) return { az: ch.view.az, fov: ch.view.fov, range: ch.view.range };
        // Autre canal deja dessine sur le meme appareil : meme direction de depart
        var sib = (c.channels || []).filter(function (x) { return x.view; })[0];
        // Capteurs fixes d'un multi-capteurs : le suivant part 90 degres plus loin
        var fixes = (c.channels || []).filter(function (x) { return x.view && x.optics === "fixe"; });
        var az = ch.optics === "fixe" && fixes.length ? (fixes[fixes.length - 1].view.az + 90) % 360 : sib ? sib.view.az : 0;
        var L0 = lensFor(c, ch);
        return L0 ? { az: az, fov: L0.fov, range: L0.range }
            : { az: az, fov: ch.optics === "fixe" ? 80 : 50, range: ch.optics === "fixe" ? 40 : 100 };
    }

    // Objectifs connus (fiches techniques Hikvision) : ouverture horizontale et
    // portee = distance DORI "Observer" (63 px/m). PTZ : grand angle, portee
    // "Observer" ramenee a la focale mini. Cles stockees sur les canaux en
    // projet (cameras.PLANNED_MODELS).
    var LENSES = [
        { k: "2dp-ptz", label: "DS-2DP7D836 (1 + 4) - PTZ", optics: "mobile", fov: 59, range: 48 },
        // Portee harmonisee a 30 m sur tous les objectifs fixes des 1 + 4 (demande du 21/10/2026, fiche : 22 m)
        { k: "2dp-pano", label: "DS-2DP7D836 (1 + 4) - objectif fixe", optics: "fixe", fov: 106, range: 30 },
        { k: "2sf-ptz", label: "DS-2SF8C442 (1 + 1) - PTZ", optics: "mobile", fov: 59, range: 28 },
        { k: "2sf-pano", label: "DS-2SF8C442 (1 + 1) - panoramique", optics: "fixe", fov: 190, range: 31 },
        { k: "bullet-wide", label: "iDS-2CD7A87G0 - fixe 2,8 mm", optics: "fixe", fov: 100, range: 37 },
        { k: "bullet-tele", label: "iDS-2CD7A87G0 - fixe 12 mm", optics: "fixe", fov: 44, range: 81 },
        { k: "fisheye-360", label: "DS-2CD63C5G1 - 360 au plafond", optics: "fixe", fov: 360, range: 11 },
        { k: "fisheye-180", label: "DS-2CD63C5G1 - 180 au mur", optics: "fixe", fov: 180, range: 11 },
        { k: "ptz", label: "PTZ generique", optics: "mobile", fov: 59, range: 50 },
    ];
    function lensByKey(k) { return LENSES.filter(function (x) { return x.k === k; })[0]; }
    function lensLabel(l) { return l.label + " (" + l.fov + " deg, " + l.range + " m)"; }
    // Objectif d'un canal : choisi (canal en projet ou selecteur du volet),
    // sinon deduit du modele de l'appareil, sinon profil par defaut de l'optique
    // (fixe = iDS-2CD7A87G0, le modele de base des fixes)
    function lensKeyFor(c, ch) {
        if (ch.model && lensByKey(ch.model)) return ch.model;
        var m = (c.model || "").toUpperCase(), first = +ch.no === +(liveChannels(c)[0] || {}).no;
        if (m.indexOf("2DP7D836") >= 0) return first ? "2dp-ptz" : "2dp-pano";
        if (m.indexOf("2SF8C442") >= 0) return first ? "2sf-ptz" : "2sf-pano";
        if (m.indexOf("2CD7A87G0") >= 0) return "bullet-wide";
        if (m.indexOf("2CD63C5G1") >= 0) return "fisheye-360";
        return ch.optics === "mobile" ? "ptz" : ch.optics === "fixe" ? "bullet-wide" : "";
    }
    function lensFor(c, ch) { return lensByKey(lensKeyFor(c, ch)); }
    function closeCone(force) {
        if (!force && M.coneEdit && M.coneEdit.dirty) {
            confirmToast("Abandonner l'angle de vue en cours de dessin ?", "Abandonner").then(function (ok) {
                if (ok) closeCone(true);
            });
            return;
        }
        M.coneEdit = null;
        M.tour = null;
        if (M.editLayer) M.editLayer.clearLayers();
        $("cm-cone-panel").hidden = true;
        $("cm-cone-panel").textContent = "";
        $("cm-map-area").classList.remove("is-cone-open");
        if (M.map) { M.map.invalidateSize(); render(); }
    }
    // Cadre la carte sur la camera et son cone, dans la partie non couverte par le volet
    function frameCone() {
        var E = M.coneEdit, p = M.pos[E.c._id], box = $("cm-cone-panel");
        var pts = conePoints(p.lat, p.lng, E.v).concat([[p.lat, p.lng]]);
        var phone = window.innerWidth <= 820;
        M.map.invalidateSize();
        M.map.fitBounds(L.latLngBounds(pts), {
            paddingTopLeft: phone ? [20, 70] : [box.offsetWidth + 50, 70],
            paddingBottomRight: phone ? [20, box.offsetHeight + 30] : [50, 40],
            maxZoom: 19, animate: false,
        });
    }
    function coneHandle(cls, title) {
        return L.divIcon({ html: '<div class="cm-cone-handle ' + cls + '" title="' + title + '"></div>', className: "", iconSize: [22, 22], iconAnchor: [11, 11] });
    }
    function drawConeEdit() {
        var E = M.coneEdit, p = M.pos[E.c._id];
        M.editLayer.clearLayers();
        E.poly = coneLayer(p.lat, p.lng, E.v, E.c.planned ? "planned" : E.ch.optics, { weight: 2.5, dashArray: "6 4", fillOpacity: .3 }).addTo(M.editLayer);
        // Verrou (M.lockFov) : ouverture et portee figees, la poignee ne fait que tourner
        E.tip = L.marker(destPoint(p.lat, p.lng, E.v.az, E.v.range), { icon: coneHandle("is-tip", M.lockFov ? "Orienter" : "Orienter et allonger"), draggable: true, zIndexOffset: 1000 }).addTo(M.editLayer);
        E.edge = M.lockFov ? null : L.marker(destPoint(p.lat, p.lng, E.v.az + E.v.fov / 2, E.v.range), { icon: coneHandle("is-edge", "Ouvrir ou fermer l'angle"), draggable: true, zIndexOffset: 1000 }).addTo(M.editLayer);
        E.tip.on("drag", function () {
            var b = bearingDist(p.lat, p.lng, E.tip.getLatLng());
            E.v.az = Math.round(b.az);
            if (!M.lockFov) E.v.range = Math.max(1, Math.round(b.d));
            refreshCone("tip");
        });
        E.tip.on("dragend", function () { refreshCone(null); });
        if (!E.edge) return;
        E.edge.on("drag", function () {
            var b = bearingDist(p.lat, p.lng, E.edge.getLatLng());
            var diff = Math.abs(((b.az - E.v.az + 540) % 360) - 180);
            E.v.fov = Math.min(359, Math.max(1, Math.round(2 * diff)));
            E.v.range = Math.max(1, Math.round(b.d));
            refreshCone("edge");
        });
        E.edge.on("dragend", function () { refreshCone(null); });
    }
    function refreshCone(dragging) {
        var E = M.coneEdit;
        if (!E) return;
        var p = M.pos[E.c._id];
        E.poly.setLatLngs(conePoints(p.lat, p.lng, E.v));
        if (dragging !== "tip") E.tip.setLatLng(destPoint(p.lat, p.lng, E.v.az, E.v.range));
        if (E.edge && dragging !== "edge") E.edge.setLatLng(destPoint(p.lat, p.lng, E.v.az + E.v.fov / 2, E.v.range));
        if (dragging) E.dirty = true;
        if (dragging === "input") return;  // ne pas reecrire le champ en cours de saisie
        E.inAz.value = Math.round(E.v.az);
        E.inFov.value = Math.round(E.v.fov);
        E.inRange.value = Math.round(E.v.range);
    }

    // Captures du configurateur : cache par canal (2 min), une seule capture
    // a la fois cote navigateur (le serveur refuse deux captures sur une meme IP)
    var SHOT_FRESH_MS = 120000;
    M.shots = {};
    function shotKey(c, no) { return c._id + ":" + no; }
    function grabShot(c, no) {
        var key = shotKey(c, no), hit = M.shots[key];
        if (hit && hit.pending) return hit.pending;
        if (hit && hit.url && Date.now() - hit.at < SHOT_FRESH_MS) return Promise.resolve(hit);
        var tries = 0;
        function once() {
            return send("POST", API.list + "/" + c._id + "/capture" + (+no !== 1 ? "?channel=" + no : "")).catch(function (e) {
                // Capture deja en cours sur l'appareil / delai entre deux captures : on reessaie
                if (++tries < 4 && /en cours|Patienter/i.test(e.message || "")) {
                    return new Promise(function (r) { setTimeout(r, 1800); }).then(once);
                }
                throw e;
            });
        }
        var pr = once().then(function (blob) {
            M.shots[key] = { url: URL.createObjectURL(blob), at: Date.now() };
            return M.shots[key];
        }, function (e) {
            delete M.shots[key];
            throw e;
        });
        M.shots[key] = { pending: pr };
        return pr;
    }
    function canShoot(c) { return capturable(c) && S.meta.key_ok; }
    function showShot(force) {
        var E = M.coneEdit;
        if (!E || E.c.planned) return;
        var c = E.c, no = E.no, token = E;
        var im = E.shotImg, st = E.shotState;
        if (force) delete M.shots[shotKey(c, no)];
        var hit = M.shots[shotKey(c, no)];
        if (hit && hit.url && Date.now() - hit.at < SHOT_FRESH_MS) {
            im.src = hit.url; im.classList.remove("is-stale");
            st.textContent = "Capture de " + new Date(hit.at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
            prefetchNext();
            return;
        }
        // Derniere capture connue en attendant (grisee)
        im.src = API.list + "/" + c._id + "/snapshot" + (+no !== 1 ? "?channel=" + no + "&" : "?") + "t=" + Date.now();
        im.classList.add("is-stale");
        if (!canShoot(c) || !M.autoShot && !force) {
            im.classList.remove("is-stale");
            st.textContent = !canShoot(c) ? (c.piste ? "Camera de piste : pas de capture" : "Capture impossible (marque ou identifiants)") + ", derniere image connue"
                : "Derniere image connue (capture auto desactivee)";
            return;
        }
        st.textContent = c.cellular ? "Camera 4G : reveil et capture..." : "Capture en cours...";
        st.classList.add("is-busy");
        grabShot(c, no).then(function (r) {
            if (M.coneEdit !== token) return;
            im.src = r.url; im.classList.remove("is-stale");
            st.classList.remove("is-busy");
            st.textContent = "Capture de " + new Date(r.at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
            prefetchNext();
        }).catch(function (e) {
            if (M.coneEdit !== token) return;
            st.classList.remove("is-busy");
            im.classList.remove("is-stale");
            var msg = e.message || "Camera injoignable";
            if (/timed out|timeout/i.test(msg)) msg = "delai depasse";
            st.textContent = "Capture impossible (" + msg + "), derniere image connue";
        });
    }
    // Prepare la capture du canal suivant du parcours pendant qu'on dessine
    function prefetchNext() {
        var T = M.tour;
        if (!T || !M.autoShot) return;
        var it = T.items[T.i + 1], c = it && camById(it.id);
        if (!c || !canShoot(c) || M.shots[shotKey(c, it.no)]) return;
        grabShot(c, it.no).catch(function () {});
    }

    function setOptics(optics) {
        var E = M.coneEdit;
        if (!E || E.ch.optics === optics && E.ch.optics_src === "manuel") return;
        var c = E.c, ch = E.ch;
        var req = c.planned
            ? send("PUT", API.planned + "/" + c.pid, { ch: ch.no, optics: optics }).then(function (r) { patchRec(c, r); })
            : send("PUT", API.list + "/" + c._id + "/channels/" + ch.no + "/optics", { optics: optics }).then(function (r) {
                ch.optics = r.optics; ch.optics_src = r.optics_src;
            });
        req.then(function () {
            if (M.coneEdit !== E) return;
            // Cone par defaut (jamais touche) : reprend les valeurs du nouveau type
            if (!E.ch.view && !E.dirty) E.v = defaultView(c, E.ch);
            if (!c.planned) E.poly.setStyle({ color: CONE_COLORS[optics], fillColor: CONE_COLORS[optics] });
            refreshCone(null);
            renderOptics();
            renderChips();
        }).catch(function (e) { toast("error", e.message); });
    }
    function renderOptics() {
        var E = M.coneEdit;
        if (!E) return;
        Array.prototype.forEach.call(E.optBtns, function (b) {
            b.classList.toggle("is-on", b.dataset.optics === E.ch.optics);
        });
        E.optHint.textContent = !E.ch.optics ? "Optique a choisir"
            : E.ch.optics_src === "modele" ? "Deduite du modele : a confirmer"
            : E.ch.optics_src === "presets" ? "Deduite des presets de la camera"
            : "Confirmee";
        E.optHint.className = "cm-cfg-opt-hint" + (!E.ch.optics || E.ch.optics_src === "modele" ? " is-warn" : "");
    }
    function saveCone() {
        var E = M.coneEdit;
        if (!E || E.saving) return;
        var c = E.c, ch = E.ch;
        if (c.planned) { savePlanned(E); return; }
        E.saving = true;
        E.saveBtn.disabled = true;
        send("PUT", API.list + "/" + c._id + "/channels/" + ch.no + "/view", { az: E.v.az, fov: E.v.fov, range: E.v.range }).then(function (r) {
            ch.view = r.view;
            E.dirty = false;
            toast("success", "Enregistre : " + c.name + (liveChannels(c).length > 1 ? ", canal " + ch.no : ""));
            if (M.tour) { tourStep(1); return; }
            // Canal suivant de l'appareil sans angle de vue (toute optique),
            // d'abord apres celui-ci, sinon en reprenant au debut ; sinon on ferme
            var rest = liveChannels(c).filter(function (k) { return !k.view && +k.no !== +ch.no; });
            var next = rest.filter(function (k) { return +k.no > +ch.no; })[0] || rest[0];
            if (next) openCone(c, next.no);
            else closeCone(true);
        }).catch(function (e) {
            toast("error", e.message);
            E.saving = false;
            E.saveBtn.disabled = false;
        });
    }
    function clearCone() {
        var E = M.coneEdit, c = E.c, ch = E.ch;
        confirmToast("Effacer l'angle de vue de " + c.name + (liveChannels(c).length > 1 ? ", canal " + ch.no : "") + " ?", "Effacer").then(function (ok) {
            if (!ok) return;
            var req = c.planned
                ? send("PUT", API.planned + "/" + c.pid, { ch: ch.no, clear_view: true }).then(function (r) { patchRec(c, r); })
                : send("PUT", API.list + "/" + c._id + "/channels/" + ch.no + "/view", { clear: true });
            req.then(function () {
                ch.view = null;
                toast("success", "Angle de vue efface");
                openCone(c, ch.no);
            }).catch(function (e) { toast("error", e.message); });
        });
    }
    // Camera en projet : reponse du serveur reportee sur l'objet local (meme
    // enregistrement que M.planned, l'objet du volet reste valable)
    function patchRec(c, r) {
        Object.keys(r).forEach(function (k) { c.rec[k] = r[k]; });
        c.name = r.name;
        c.channels = plannedChannels(r);
        // Le volet pointe sur le canal reconstruit
        if (M.coneEdit && M.coneEdit.c === c) M.coneEdit.ch = chOf(c, M.coneEdit.no) || c.channels[0];
    }
    // Canaux simules d'un emplacement en projet (ex. 1 PTZ + 4 fixes)
    function addPlannedChannels(c, optics, count, model) {
        return send("POST", API.planned + "/" + c.pid + "/channels", { optics: optics, count: count || 1, model: model || "" }).then(function (r) {
            var before = c.channels.map(function (k) { return +k.no; });
            patchRec(c, r);
            var added = c.channels.filter(function (k) { return before.indexOf(+k.no) < 0; })[0];
            if (M.coneEdit) M.coneEdit.dirty = false;
            openCone(c, added ? added.no : undefined);
        }).catch(function (e) { toast("error", e.message); });
    }
    // Modeles multi-capteurs : canal 1 = PTZ du modele, puis ses objectifs fixes
    // (DS-2DP7D836 : 1 + 4 ; DS-2SF8C442 : 1 + 1 panoramique)
    var PLANNED_KITS = {
        "2dp": { ptz: "2dp-ptz", fixe: "2dp-pano", count: 4 },
        "2sf": { ptz: "2sf-ptz", fixe: "2sf-pano", count: 1 },
    };
    function plannedPreset(c, kit) {
        var K = PLANNED_KITS[kit], first = c.channels[0];
        send("PUT", API.planned + "/" + c.pid, { ch: first.no, optics: "mobile", model: K.ptz }).then(function (r) {
            patchRec(c, r);
            return addPlannedChannels(c, "fixe", K.count, K.fixe);
        }).catch(function (e) { toast("error", e.message); });
    }
    function removePlannedChannel(c, no) {
        confirmToast("Retirer le canal " + no + " de " + c.name + " ?", "Retirer").then(function (ok) {
            if (!ok) return;
            send("DELETE", API.planned + "/" + c.pid + "/channels/" + no).then(function (r) {
                patchRec(c, r);
                if (M.coneEdit) M.coneEdit.dirty = false;
                openCone(c, c.channels[0].no);
            }).catch(function (e) { toast("error", e.message); });
        });
    }
    function savePlanned(E) {
        var c = E.c, ch = E.ch, f = E.form;
        var name = f.name.value.trim();
        if (!name) { toast("warning", "Donnez un nom a la camera en projet"); f.name.focus(); return; }
        var body = { name: name, note: f.note.value, status: f.status.value, priority: f.priority.value,
            mount: f.mount.value, mount_height: f.height.value, replaces: f.replaces };
        // Le cone affiche est toujours enregistre, meme laisse aux valeurs par defaut
        body.ch = ch.no;
        body.view = { az: E.v.az, fov: E.v.fov, range: E.v.range };
        E.saving = true;
        E.saveBtn.disabled = true;
        send("PUT", API.planned + "/" + c.pid, body).then(function (r) {
            patchRec(c, r);
            E.dirty = false;
            toast("success", "Camera en projet enregistree : " + r.name + (c.channels.length > 1 ? ", canal " + ch.no : ""));
            // Canal suivant sans angle de vue, sinon on ferme
            var rest = c.channels.filter(function (k) { return !k.view && +k.no !== +ch.no; });
            var next = rest.filter(function (k) { return +k.no > +ch.no; })[0] || rest[0];
            if (next) openCone(c, next.no);
            else closeCone(true);
        }).catch(function (e) {
            toast("error", e.message);
            E.saving = false;
            E.saveBtn.disabled = false;
        });
    }
    // Cameras existantes placees sur la carte (hors projets), avec leur distance au point
    function existingNear(lat, lng, radius) {
        return Object.keys(M.pos).filter(function (id) { return id.indexOf("p:") !== 0 && M.pos[id].lat != null; }).map(function (id) {
            var p = M.pos[id];
            return { _id: id, name: p.name || id, ip: p.ip || "", d: bearingDist(lat, lng, { lat: p.lat, lng: p.lng }).d };
        }).filter(function (x) { return x.d <= radius; }).sort(function (a, b) { return a.d - b.d; });
    }
    function camName(id, rec) {
        var p = M.pos[id];
        if (p && p.name) return p.name;
        var r = (rec.replaces || []).filter(function (x) { return x._id === id; })[0];
        return r ? r.name : id;
    }
    // Liens d'un projet avec l'existant : camera deplacee (lien pose a la
    // creation depuis la camera) et cameras remplacees (a deposer)
    function plannedLinks(c) {
        var E = M.coneEdit, rec = c.rec, box = el("div", "cm-plan-links");
        if (rec.moves) {
            var mv = el("div", "cm-plan-move");
            mv.appendChild(icon("move_location"));
            var old = M.pos[rec.moves._id];
            var dist = old ? Math.round(bearingDist(old.lat, old.lng, { lat: rec.lat, lng: rec.lng }).d) : null;
            mv.appendChild(el("span", "", "Deplacement de " + rec.moves.name + (rec.moves.exists === false ? " (camera supprimee)" : "") +
                (dist != null ? " : " + dist + " m depuis sa position actuelle" : "")));
            mv.appendChild(cfgBtn("link_off", "", "", function () {
                confirmToast("Ce projet ne sera plus le deplacement de " + rec.moves.name + " ?", "Retirer le lien").then(function (ok) {
                    if (!ok) return;
                    send("PUT", API.planned + "/" + c.pid, { moves_camera_id: "" }).then(function (r) {
                        patchRec(c, r);
                        box.replaceWith(plannedLinks(c));
                        render();
                    }).catch(function (e) { toast("error", e.message); });
                });
            }, "Retirer le lien avec la camera deplacee"));
            box.appendChild(mv);
        }
        var head = el("div", "cm-plan-rep-head");
        head.appendChild(el("b", "", "Remplace"));
        var chips = el("div", "cm-plan-chips");
        var list = el("div", "cm-plan-cands");
        list.hidden = true;
        function drawChips() {
            chips.textContent = "";
            if (!E.form.replaces.length) chips.appendChild(el("span", "cm-plan-none", "aucune camera existante"));
            E.form.replaces.forEach(function (id) {
                var chip = el("span", "cm-plan-chip");
                chip.appendChild(el("span", "", camName(id, rec)));
                var x = el("button", "cm-plan-chip-x"); x.type = "button"; x.title = "Ne remplace plus cette camera";
                x.appendChild(icon("close"));
                x.addEventListener("click", function () {
                    E.form.replaces = E.form.replaces.filter(function (k) { return k !== id; });
                    E.dirty = true;
                    drawChips(); drawList();
                });
                chip.appendChild(x);
                chips.appendChild(chip);
            });
        }
        function drawList() {
            list.textContent = "";
            var cands = existingNear(rec.lat, rec.lng, REPLACE_RADIUS_M).filter(function (x) { return !rec.moves || x._id !== rec.moves._id; });
            if (!cands.length) { list.appendChild(el("span", "cm-plan-none", "Aucune camera existante a moins de " + REPLACE_RADIUS_M + " m")); return; }
            cands.slice(0, 25).forEach(function (x) {
                var l = el("label", "cm-plan-cand"), cb = document.createElement("input");
                cb.type = "checkbox"; cb.checked = E.form.replaces.indexOf(x._id) >= 0;
                cb.addEventListener("change", function () {
                    E.form.replaces = cb.checked ? E.form.replaces.concat([x._id]) : E.form.replaces.filter(function (k) { return k !== x._id; });
                    E.dirty = true;
                    drawChips();
                });
                l.appendChild(cb);
                l.appendChild(el("span", "", x.name));
                l.appendChild(el("small", "", Math.round(x.d) + " m"));
                list.appendChild(l);
            });
        }
        head.appendChild(cfgBtn("checklist", "Choisir", "", function () {
            list.hidden = !list.hidden;
            if (!list.hidden) drawList();
        }, "Cameras existantes remplacees par ce projet (a deposer), les plus proches d'abord"));
        box.appendChild(head);
        box.appendChild(chips);
        box.appendChild(list);
        drawChips();
        return box;
    }
    // Camera existante : projet de deplacement cree a sa position, avec ses
    // canaux et angles de vue ; il reste a glisser le pin a la nouvelle place
    function planMove(c) {
        var already = (M.planned || []).filter(function (p) { return p.moves && p.moves._id === c._id; })[0];
        if (already) { closeCone(true); openPlanned(plannedById("p:" + already._id)); return; }
        send("POST", API.planned, { from_camera_id: c._id }).then(function (r) {
            closeCone(true);
            openPlanned(storePlanned(r));
            toast("success", "Deplacement planifie : glissez le pin violet a la nouvelle position, puis enregistrez");
        }).catch(function (e) { toast("error", e.message); });
    }
    // Dossier de planification des travaux (GET /api/cameras/planned/dossier.pdf).
    // Options memorisees par poste (titre, indice, auteur, fonction...).
    var DOSSIER_FIELDS = ["title", "subtitle", "indice", "phase", "author", "role"];
    function dossierStatuses() {
        return Array.prototype.filter.call(document.querySelectorAll("#cm-dos-status input"), function (cb) { return cb.checked; })
            .map(function (cb) { return cb.value; });
    }
    function dossierCount() {
        var st = dossierStatuses();
        var n = (M.planned || []).filter(function (p) { return st.indexOf(p.status) >= 0; }).length;
        var noView = (M.planned || []).filter(function (p) {
            return st.indexOf(p.status) >= 0 && (p.channels || []).some(function (k) { return !k.view; });
        }).length;
        var noPrio = (M.planned || []).filter(function (p) { return st.indexOf(p.status) >= 0 && p.priority == null; }).length;
        $("cm-dos-count").textContent = n + " camera(s) en projet dans le dossier" +
            (noPrio ? " ; " + noPrio + " sans priorite (placees a la fin)" : "") +
            (noView ? " ; " + noView + " avec un canal sans angle de vue" : "") + ".";
        $("cm-dos-go").disabled = !n;
    }
    function openDossier() {
        var saved = {};
        try { saved = JSON.parse(localStorage.getItem("cockpit-cameras-dossier") || "{}"); } catch (e) {}
        DOSSIER_FIELDS.forEach(function (k) { if (saved[k] != null) $("cm-dos-" + k).value = saved[k]; });
        var go = function () { dossierCount(); $("cm-dossier-dialog").showModal(); };
        if (M.loaded) go(); else loadMapData().then(go);
    }
    function makeDossier() {
        var q = { status: dossierStatuses().join(",") }, keep = {};
        DOSSIER_FIELDS.forEach(function (k) {
            var v = $("cm-dos-" + k).value.trim();
            keep[k] = v;
            if (v) q[k] = v;
        });
        try { localStorage.setItem("cockpit-cameras-dossier", JSON.stringify(keep)); } catch (e) {}
        var url = API.dossier + "?" + Object.keys(q).map(function (k) { return k + "=" + encodeURIComponent(q[k]); }).join("&");
        // Nouvel onglet : le PDF s'y affiche (tuiles IGN telechargees au premier passage : quelques secondes)
        window.open(url, "_blank");
        toast("info", "Dossier en preparation dans un nouvel onglet");
        $("cm-dossier-dialog").close();
    }
    function deletePlanned(c) {
        confirmToast("Supprimer la camera en projet " + c.name + " ?", "Supprimer").then(function (ok) {
            if (!ok) return;
            send("DELETE", API.planned + "/" + c.pid).then(function () {
                M.planned = (M.planned || []).filter(function (p) { return p._id !== c.pid; });
                delete M.pos[c._id];
                rebuildPlanned();
                if (M.coneEdit) M.coneEdit.dirty = false;
                closeCone(true);
                toast("success", "Camera en projet supprimee");
            }).catch(function (e) { toast("error", e.message); });
        });
    }
    // Prereglage d'objectif : ouverture et portee de la fiche technique (la
    // direction est gardee). Canal en projet : modele (et optique) enregistres.
    function applyLens(k) {
        var E = M.coneEdit, l = lensByKey(k);
        if (!E) return;
        var c = E.c, ch = E.ch;
        if (l) {
            E.v.fov = l.fov;
            E.v.range = l.range;
            E.dirty = true;
            refreshCone(null);
        }
        if (c.planned) {
            var body = { ch: ch.no, model: k };
            if (l) body.optics = l.optics;
            send("PUT", API.planned + "/" + c.pid, body).then(function (r) {
                patchRec(c, r);
                renderOptics();
                renderChips();
            }).catch(function (e) { toast("error", e.message); });
        } else {
            ch.model = k;  // session seulement : l'objectif d'une camera reelle vient de son modele
            if (l && l.optics !== ch.optics) setOptics(l.optics);
        }
    }
    function setLock(on) {
        M.lockFov = on;
        try { localStorage.setItem("cockpit-cameras-lockfov", on ? "1" : "0"); } catch (e) {}
        var E = M.coneEdit;
        if (!E) return;
        E.lockBtn.classList.toggle("is-on", on);
        E.lockBtn.querySelector(".material-symbols-outlined").textContent = on ? "lock" : "lock_open";
        E.inFov.disabled = E.inRange.disabled = on;
        drawConeEdit();
    }
    // Pastilles des canaux (fait / optique) ; camera en projet : ajout de
    // canaux simules (+ Mobile, + Fixe, modeles 1 PTZ + 4 fixes / + 1 fixe)
    function renderChips() {
        var E = M.coneEdit;
        if (!E || !E.chipsBox) return;
        var c = E.c, box = E.chipsBox, chs = liveChannels(c);
        box.textContent = "";
        chs.forEach(function (k) {
            var b = el("button", "cm-cfg-chip" + (+k.no === +E.no ? " is-current" : "") + (k.view ? " is-done" : "") + (k.optics ? " is-" + k.optics : ""));
            b.type = "button";
            b.title = (k.name || "Canal " + k.no) + " : " + optLabel(k) + (k.view ? ", angle dessine" : ", angle a dessiner");
            b.appendChild(el("span", "", String(k.no)));
            if (k.view) b.appendChild(icon("check"));
            b.addEventListener("click", function () { openCone(c, k.no); });
            box.appendChild(b);
        });
        if (!c.planned) return;
        box.appendChild(cfgBtn("add", "Mobile", "cm-cfg-chip-add", function () { addPlannedChannels(c, "mobile", 1, "ptz"); }, "Ajouter un canal mobile (PTZ generique)"));
        box.appendChild(cfgBtn("add", "Fixe", "cm-cfg-chip-add", function () { addPlannedChannels(c, "fixe", 1, "bullet-wide"); }, "Ajouter un canal fixe (iDS-2CD7A87G0)"));
        box.appendChild(cfgBtn("add", "360", "cm-cfg-chip-add", function () { addPlannedChannels(c, "fixe", 1, "fisheye-360"); }, "Ajouter une camera 360 (DS-2CD63C5G1 au plafond)"));
        if (chs.length === 1) {
            box.appendChild(cfgBtn("view_module", "1 PTZ + 4 fixes", "cm-cfg-chip-add", function () { plannedPreset(c, "2dp"); }, "DS-2DP7D836 : canal 1 PTZ + 4 objectifs fixes"));
            box.appendChild(cfgBtn("view_agenda", "1 PTZ + 1 fixe", "cm-cfg-chip-add", function () { plannedPreset(c, "2sf"); }, "DS-2SF8C442 : canal 1 PTZ + 1 panoramique"));
        } else {
            box.appendChild(cfgBtn("remove", "", "cm-cfg-chip-add cm-btn-danger", function () { removePlannedChannel(c, E.no); }, "Retirer le canal " + E.no));
        }
    }
    function cfgBtn(ic, label, cls, fn, title) {
        var b = el("button", "cm-btn " + (cls || "")); b.type = "button";
        if (ic) b.appendChild(icon(ic));
        if (label) b.appendChild(el("span", "", label));
        if (title) b.title = title;
        b.addEventListener("click", fn);
        return b;
    }
    function buildConePanel() {
        var E = M.coneEdit, c = E.c, ch = E.ch, T = M.tour;
        var box = $("cm-cone-panel");
        box.textContent = "";
        box.hidden = false;
        box.classList.toggle("is-tour", !!T);

        // En-tete : progression du parcours, camera, fermeture
        var head = el("div", "cm-cfg-head");
        var ttl = el("div", "cm-cfg-title");
        if (T) {
            ttl.appendChild(el("span", "cm-cfg-step", (T.i + 1) + " / " + T.items.length + " " + T.label));
        }
        ttl.appendChild(el("b", "", c.name));
        head.appendChild(ttl);
        head.appendChild(cfgBtn("close", "", "cm-cfg-close", function () { closeCone(false); }, "Fermer (Echap)"));
        box.appendChild(head);
        if (T) {
            var bar = el("div", "cm-cfg-progress"), fill = el("i");
            fill.style.width = Math.round(100 * T.i / T.items.length) + "%";
            bar.appendChild(fill);
            box.appendChild(bar);
        }

        // Canaux de l'appareil : pastilles (fait / optique)
        var chs = liveChannels(c);
        E.chipsBox = el("div", "cm-cfg-chips");
        if (chs.length > 1 || c.planned) box.appendChild(E.chipsBox);
        renderChips();
        if (c.planned) {
            // Camera en projet : pas de photo, fiche du projet a la place
            box.appendChild(el("div", "cm-cfg-planned-badge", "Camera en projet (travaux a venir) - glissez son pin pour la deplacer"));
            var pf = el("div", "cm-cfg-planned");
            E.form = {
                name: inp(c.rec.name, { maxlength: 120, placeholder: "Ex. Parking P4 - entree nord" }),
                status: sel(PLANNED_STATUS, c.rec.status || "souhaitee"),
                note: document.createElement("textarea"),
            };
            E.form.note.value = c.rec.note || "";
            E.form.note.maxLength = 1000;
            E.form.note.placeholder = "Pourquoi ici, ce qu'elle doit voir, contraintes (reseau, alimentation)...";
            E.form.priority = inp(c.rec.priority == null ? "" : c.rec.priority, { placeholder: "1 = urgent" });
            E.form.priority.type = "number"; E.form.priority.min = "1"; E.form.priority.max = "999";
            E.form.mount = sel(PLANNED_MOUNTS, c.rec.mount || "");
            E.form.height = inp(c.rec.mount_height == null ? "" : String(c.rec.mount_height).replace(".", ","), { placeholder: "ex. 6", inputmode: "decimal" });
            E.form.replaces = (c.rec.replaces || []).map(function (r) { return r._id; });
            pf.appendChild(field("Nom", E.form.name));
            pf.appendChild(field("Statut", E.form.status));
            pf.appendChild(field("Priorite", E.form.priority));
            pf.appendChild(field("Support de pose", E.form.mount));
            pf.appendChild(field("Hauteur de pose (m)", E.form.height));
            pf.appendChild(field("Note", E.form.note, "cm-span2"));
            box.appendChild(pf);
            box.appendChild(plannedLinks(c));
            box.appendChild(el("div", "cm-cfg-chname", "Ajoutee" + (c.rec.by ? " par " + c.rec.by : "") +
                (c.rec.at ? " le " + new Date(c.rec.at).toLocaleDateString("fr-FR") : "")));
        } else {
            box.appendChild(el("div", "cm-cfg-chname", (chs.length > 1 ? "Canal " + ch.no + " : " : "") + (ch.name || c.name)));

            // Photo du canal
            var shot = el("div", "cm-cfg-shot");
            E.shotImg = document.createElement("img");
            E.shotImg.alt = "Capture du canal";
            E.shotImg.addEventListener("error", function () { E.shotImg.removeAttribute("src"); E.shotImg.classList.remove("is-stale"); });
            E.shotImg.addEventListener("click", function () { if (E.shotImg.src) openLightbox(E.shotImg.src); });
            shot.appendChild(E.shotImg);
            E.shotState = el("span", "cm-cfg-shot-state", "");
            shot.appendChild(E.shotState);
            if (canShoot(c)) {
                var rb = cfgBtn("refresh", "", "cm-cfg-shot-btn", function () { showShot(true); }, "Nouvelle capture (C)");
                shot.appendChild(rb);
            }
            box.appendChild(shot);
        }

        // Optique : un clic = enregistre
        var opt = el("div", "cm-cfg-opt");
        var seg = el("div", "cm-cfg-seg");
        E.optBtns = [
            cfgBtn("crop_free", "Fixe", "", function () { setOptics("fixe"); }, "Optique fixe (F)"),
            cfgBtn("360", "Mobile (PTZ)", "", function () { setOptics("mobile"); }, "Optique mobile, PTZ (M)"),
        ];
        E.optBtns[0].dataset.optics = "fixe";
        E.optBtns[1].dataset.optics = "mobile";
        E.optBtns.forEach(function (b) { seg.appendChild(b); });
        opt.appendChild(seg);
        E.optHint = el("span", "cm-cfg-opt-hint");
        opt.appendChild(E.optHint);
        box.appendChild(opt);
        renderOptics();

        // Modele / objectif : prereglage de l'ouverture et de la portee
        var lensRow = el("div", "cm-cfg-lens");
        var curKey = lensKeyFor(c, ch);
        var sLens = sel([["", "Modele / objectif : valeurs libres"]].concat(LENSES.map(function (l) { return [l.k, lensLabel(l)]; })), curKey);
        sLens.className = "cm-select";
        sLens.title = "Prereglage d'apres la fiche technique : ouverture horizontale et portee DORI 'Observer'";
        sLens.addEventListener("change", function () { applyLens(sLens.value); });
        lensRow.appendChild(field(c.planned || ch.model ? "Modele / objectif" : "Modele / objectif (deduit du modele de l'appareil)", sLens));
        E.lockBtn = cfgBtn(M.lockFov ? "lock" : "lock_open", "", "cm-cfg-lock" + (M.lockFov ? " is-on" : ""), function () { setLock(!M.lockFov); },
            "Verrouiller l'ouverture et la portee : la poignee ne fait plus que tourner le cone");
        lensRow.appendChild(E.lockBtn);
        box.appendChild(lensRow);

        // Cone
        var g = el("div", "cm-cone-fields");
        function num(label, key, min, max) {
            var i = inp(Math.round(E.v[key]), { inputmode: "numeric" });
            i.type = "number"; i.min = min; i.max = max;
            i.addEventListener("input", function () {
                var n = parseFloat(i.value);
                if (isNaN(n)) return;
                E.v[key] = key === "az" ? ((n % 360) + 360) % 360 : Math.min(max, Math.max(min, n));
                refreshCone("input");
            });
            g.appendChild(field(label, i));
            return i;
        }
        E.inAz = num("Direction (0 = nord)", "az", 0, 359);
        E.inFov = num("Ouverture (degres)", "fov", 1, 360);
        E.inRange = num("Portee (m)", "range", 1, 5000);
        E.inFov.disabled = E.inRange.disabled = !!M.lockFov;
        box.appendChild(g);
        box.appendChild(el("p", "cm-help", "Sur la carte : point plein = direction et portee, point creux = ouverture. " +
            (c.planned ? "Dessinez ce que la future camera devra voir."
                : "Reglez d'apres la photo (vue ACTUELLE : verifiez que c'est la vue principale).") +
            (ch.view && ch.view.at ? " Dessine" + (ch.view.by ? " par " + ch.view.by : "") + " le " +
                new Date(ch.view.at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) + "." : "")));

        // Actions
        var acts = el("div", "cm-cfg-actions");
        if (T) acts.appendChild(cfgBtn("arrow_back", "", "", function () { tourStep(-1); }, "Precedent (fleche gauche)"));
        if (T) acts.appendChild(cfgBtn("skip_next", "Passer", "", function () { tourStep(1); }, "Passer sans enregistrer l'angle (fleche droite)"));
        if (c.planned) acts.appendChild(cfgBtn("delete", "", "cm-btn-danger", function () { deletePlanned(c); }, "Supprimer la camera en projet"));
        else if (ch.view) acts.appendChild(cfgBtn("delete", "", "cm-btn-danger", clearCone, "Effacer l'angle de vue"));
        if (!c.planned) acts.appendChild(cfgBtn("move_location", "", "", function () { planMove(c); },
            "Planifier le deplacement de cette camera (camera en projet a sa position actuelle, a glisser a la nouvelle place)"));
        E.saveBtn = cfgBtn("check", T ? "Enregistrer et suivant" : "Enregistrer", "cm-btn-primary cm-cfg-save", saveCone, "Entree");
        acts.appendChild(E.saveBtn);
        box.appendChild(acts);

        var foot = el("div", "cm-cfg-foot");
        var auto = el("label", "cm-cfg-auto"), cb = document.createElement("input");
        cb.type = "checkbox"; cb.checked = !!M.autoShot;
        cb.addEventListener("change", function () {
            M.autoShot = cb.checked;
            try { localStorage.setItem("cockpit-cameras-autoshot", cb.checked ? "1" : "0"); } catch (e) {}
            if (cb.checked) showShot();
        });
        auto.appendChild(cb);
        auto.appendChild(document.createTextNode("Capture auto"));
        foot.appendChild(auto);
        foot.appendChild(el("span", "cm-cfg-keys", "Entree enregistrer - F / M optique - C capture - fleches naviguer - Echap fermer"));
        box.appendChild(foot);
    }
    // Raccourcis du configurateur (volet ouvert, pas de fiche ni de dialogue)
    function coneKeys(e) {
        if ($("cm-cone-panel").hidden || !M.coneEdit || !$("cm-drawer").hidden || document.querySelector("dialog[open]")) return false;
        if (!$("cm-lightbox").hidden) return false;
        var tag = (e.target && e.target.tagName) || "";
        if (e.ctrlKey || e.metaKey || e.altKey) return false;
        if (e.key === "Enter" && tag !== "BUTTON" && tag !== "SELECT" && tag !== "TEXTAREA") { e.preventDefault(); saveCone(); return true; }
        if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return false;
        var k = e.key.toLowerCase();
        if (k === "f") { setOptics("fixe"); return true; }
        if (k === "m") { setOptics("mobile"); return true; }
        if (k === "c") { showShot(true); return true; }
        if (e.key === "ArrowRight" && M.tour) { e.preventDefault(); tourStep(1); return true; }
        if (e.key === "ArrowLeft" && M.tour) { e.preventDefault(); tourStep(-1); return true; }
        return false;
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
        // Releve planifiee (cameras_health.py) : coupure confirmee et sa duree
        if (r && r.health_ok === false && r.since) dot.title += " - hors ligne depuis " + sinceText(r.since);
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
    function capture(c, channel, target, onDone) {
        toast("info", c.cellular ? "Camera 4G : reveil et capture, jusqu'a une minute..." : "Capture en cours...");
        send("POST", API.list + "/" + c._id + "/capture" + (channel ? "?channel=" + channel : "")).then(function (blob) {
            var url = URL.createObjectURL(blob);
            if (onDone) onDone(url);
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
            // Optique : mobile (PTZ) ou fixe. Pre-remplie par
            // scripts/cameras_optics_prefill.py, un choix ici devient "manuel"
            var so = sel([["", "Optique ?"], ["mobile", "Mobile (PTZ)"], ["fixe", "Fixe"]], ch.optics || "");
            so.className = "cm-optics" + (ch.optics_src === "modele" ? " is-guess" : "");
            so.title = ch.optics_src === "presets" ? "Deduit des presets lus sur la camera"
                : ch.optics_src === "modele" ? "Deduit du modele : a verifier"
                : ch.optics_src === "manuel" ? "Choisi a la main" : "Non renseigne";
            so.setAttribute("aria-label", "Optique du canal " + ch.no);
            so.addEventListener("change", function () { ch.optics = so.value; so.classList.remove("is-guess"); });
            head.appendChild(so);
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
                    else if (p.kind === "preset") {
                        // Preset de VUE (1-300) : les presets de fonction Hik (94 = redemarrage...) sont refuses par le serveur
                        input = inp(r[p.key]); input.type = "number"; input.min = "1"; input.max = "300"; input.placeholder = "aucun";
                    }
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

        // Historique (GET /api/cameras/<id>/journal), charge a la demande
        if (c) {
            var s6 = section("Historique", "history");
            var hrow = el("div", "cm-health", "");
            s6.appendChild(hrow);
            getJSON(API.list + "/" + c._id + "/health").then(function (h) {
                if (!h.monitored) {
                    hrow.textContent = h.piste ? "Camera de piste : jamais sondee." : h.enabled === false
                        ? "Camera desactivee : pas de releve." : "Pas encore relevee (tache Cockpit - Etat cameras).";
                    return;
                }
                hrow.classList.add(h.ok ? "is-ok" : "is-ko");
                hrow.textContent = (h.ok ? "En ligne" : "Hors ligne") + " depuis " + sinceText(h.since) +
                    " - disponibilite 24 h : " + pct(h.availability_24h) + ", 7 j : " + pct(h.availability_7d) +
                    " - " + h.outages_7d + " coupure" + (h.outages_7d > 1 ? "s" : "") + " en 7 j" +
                    (h.lapi_last_at ? " - derniere plaque lue " + sinceText(h.lapi_last_at) : "");
            }).catch(function () { hrow.hidden = true; });
            var hb = el("button", "cm-btn"); hb.type = "button";
            hb.appendChild(icon("history")); hb.appendChild(document.createTextNode("Afficher l'historique"));
            var hl = el("div", "cm-journal");
            hb.addEventListener("click", function () {
                hb.disabled = true;
                getJSON(API.list + "/" + c._id + "/journal").then(function (rows) {
                    hb.hidden = true;
                    renderJournal(hl, rows);
                }).catch(function (e) { hb.disabled = false; toast("error", "Historique illisible : " + e.message); });
            });
            s6.appendChild(hb);
            s6.appendChild(hl);
            body.appendChild(s6);
        }

        // Apercu
        if (c && c.controllable) {
            var s5 = section("Apercu", "photo_camera");
            var bar = el("div", "cm-inline");
            var sCh = sel(d.channels.map(function (ch) { return [ch.no, ch.no + " - " + (ch.name || "Canal " + ch.no)]; }), d.channels[0].no);
            sCh.style.cssText = "height:34px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:.82rem;padding:0 8px;flex:1;min-width:0;";
            bar.appendChild(sCh);
            // Canaux actifs : "Capturer" les prend tous d'un coup (un clic par canal sinon)
            var capChs = d.channels.filter(function (ch) { return ch.enabled !== false; }).map(function (ch) { return +ch.no; });
            if (!capChs.length) capChs = [+d.channels[0].no];
            var cap = el("button", "cm-btn"); cap.type = "button"; cap.disabled = !S.meta.key_ok || !c.credential_profile_id;
            cap.appendChild(icon("photo_camera")); cap.appendChild(document.createTextNode(capChs.length > 1 ? "Capturer les " + capChs.length + " canaux" : "Capturer"));
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
                        setTimeout(function () { var no = +sCh.value; capture(c, no, snap, function (url) { setTile(no, url); }); gb.disabled = false; }, 4000);
                    }).catch(function (e) { toast("error", e.message); gb.disabled = false; });
                });
            });
            // Apres une bascule des masques : captures de controle affichees
            s5.appendChild(privacyRow(c, cap.disabled, function (nos) {
                nos.forEach(function (no) { setTile(+no, snapUrl(+no) + (+no !== 1 ? "&" : "?") + "t=" + Date.now()); });
                if (nos.map(Number).indexOf(+sCh.value) >= 0) showLatest();
                if (S.view === "map") loadMapData().then(render);
            }));
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
            // Vignettes des autres canaux : derniere capture de chacun, clic = canal choisi
            var tiles = el("div", "cm-snap-tiles"), tileOf = {};
            function snapUrl(no) {
                return API.list + "/" + c._id + "/snapshot" + (no !== 1 ? "?channel=" + no : "");
            }
            function setTile(no, url) {
                var t = tileOf[no];
                if (!t) return;
                var old = t.querySelector("img, .material-symbols-outlined");
                var im = document.createElement("img");
                im.alt = "Canal " + no;
                im.addEventListener("error", function () { im.replaceWith(icon("no_photography")); });
                im.src = url;
                if (old) old.replaceWith(im); else t.insertBefore(im, t.firstChild);
            }
            function markTiles() {
                Object.keys(tileOf).forEach(function (no) { tileOf[no].classList.toggle("is-current", +no === +sCh.value); });
            }
            if (capChs.length > 1) {
                capChs.forEach(function (no) {
                    var ch = d.channels.filter(function (x) { return +x.no === no; })[0] || {};
                    var t = el("button", "cm-snap-tile"); t.type = "button";
                    t.title = no + " - " + (ch.name || "Canal " + no);
                    t.appendChild(el("span", "cm-snap-tile-no", String(no)));
                    t.addEventListener("click", function () {
                        sCh.value = no;
                        sCh.dispatchEvent(new Event("change"));
                    });
                    tileOf[no] = t;
                    tiles.appendChild(t);
                    setTile(no, snapUrl(no) + (no !== 1 ? "&" : "?") + "t=" + Date.now());
                });
                markTiles();
            }
            function captureAll() {
                cap.disabled = true;
                var done = 0, fails = [];
                toast("info", (c.cellular ? "Camera 4G : reveil, puis " : "") + "capture des " + capChs.length + " canaux...");
                // Un canal apres l'autre : la camera ne recoit jamais plusieurs demandes a la fois
                capChs.reduce(function (p, no) {
                    return p.then(function () {
                        return send("POST", API.list + "/" + c._id + "/capture" + (no !== 1 ? "?channel=" + no : "")).then(function (blob) {
                            var url = URL.createObjectURL(blob);
                            done++;
                            setTile(no, url);
                            if (+sCh.value === no) { snap.textContent = ""; snap.appendChild(snapImg(url)); }
                        }).catch(function (e) {
                            var msg = e.message || "Camera injoignable";
                            if (/timed out|timeout/i.test(msg)) msg = "delai depasse";
                            fails.push(no + " (" + msg + ")");
                        });
                    });
                }, Promise.resolve()).then(function () {
                    cap.disabled = false;
                    if (!fails.length) toast("success", "Capture reussie sur les " + done + " canaux");
                    else if (done) toast("warning", done + " canal(aux) capture(s), echec : " + fails.join(", "));
                    else toast("error", "Aucune capture : " + fails.join(", "));
                });
            }
            sCh.addEventListener("change", function () { showLatest(); loadPresets(); markTiles(); });
            cap.addEventListener("click", function () {
                if (capChs.length > 1) captureAll();
                else capture(c, +sCh.value, snap);
            });
            showLatest();
            loadPresets();
            s5.appendChild(snap);
            if (capChs.length > 1) s5.appendChild(tiles);
            // Apercu en tete de fiche : visible sans faire defiler
            body.insertBefore(s5, body.firstChild);
        }
        render();
        // Pas de defilement vers le champ : l'apercu reste visible en haut
        if (!c) iName.focus({ preventScroll: true });
    }

    var JOURNAL_LABELS = {
        creation: "Creation", modification: "Modification", suppression: "Suppression",
        modification_masse: "Modification de masse", preset: "Rappel de preset",
        essuie_glace: "Essuie-glace", position: "Position sur la carte", masques: "Masques de vie privee",
    };
    var FIELD_LABELS = {
        name: "nom", ip: "IP", port: "port", protocol: "protocole", brand: "marque", model: "modele",
        firmware: "firmware", site: "site", location: "emplacement", notes: "notes", camera_path: "chemin d'envoi",
        enabled: "active", tags: "etiquettes", credential_profile_id: "profil", channels: "canaux / usages",
    };
    function journalText(r) {
        var d = r.detail || {};
        if (r.action === "modification") {
            return Object.keys(d).map(function (k) {
                if (k === "channels" || k === "notes" || k === "credential_profile_id") return FIELD_LABELS[k] || k;
                return (FIELD_LABELS[k] || k) + " : " + (d[k][0] == null || d[k][0] === "" ? "-" : d[k][0]) + " -> " + (d[k][1] == null || d[k][1] === "" ? "-" : d[k][1]);
            }).join(", ");
        }
        if (r.action === "modification_masse") return Object.keys(d).map(function (k) { return FIELD_LABELS[k] || k; }).join(", ");
        if (r.action === "preset") return "canal " + d.channel + ", preset " + d.preset + (d.ok ? "" : " (refuse)");
        if (r.action === "essuie_glace") return "canal " + d.channel + (d.ok ? "" : " (refuse)");
        if (r.action === "masques") return (d.enabled ? "actives" : "desactives") + (d.bulk ? " (toutes les cameras)" : "") + (d.ok ? "" : " - echec");
        return "";
    }
    function renderJournal(box, rows) {
        box.textContent = "";
        if (!rows.length) { box.appendChild(el("p", "cm-journal-empty", "Aucune action enregistree.")); return; }
        rows.forEach(function (r) {
            var row = el("div", "cm-journal-row");
            var head = el("div", "cm-journal-head");
            head.appendChild(el("strong", "", JOURNAL_LABELS[r.action] || r.action));
            head.appendChild(el("span", "", (r.at ? new Date(r.at).toLocaleString("fr-FR") : "") + (r.by ? " - " + r.by : "")));
            row.appendChild(head);
            var t = journalText(r);
            if (t) row.appendChild(el("div", "cm-journal-detail", t));
            box.appendChild(row);
        });
    }

    // Masques de vie privee (POST/GET /api/cameras/<id>/privacy) : seuls les
    // masques deja dessines sur la camera sont actives ou desactives.
    // Hikvision, Hanwha, Dahua, Bosch (domes, RCP+) ; Axis : a regler sur la camera.
    var PRIVACY_BRANDS = ["hikvision", "hanwha", "dahua", "bosch"];
    function privacyRow(c, noCreds, onCaptured) {
        var rowEl = el("div", "cm-privacy");
        rowEl.appendChild(icon("privacy_tip"));
        var txt = el("span", "cm-privacy-text", "");
        rowEl.appendChild(txt);
        var btn = el("button", "cm-btn"); btn.type = "button"; btn.hidden = true;
        rowEl.appendChild(btn);
        if (PRIVACY_BRANDS.indexOf(c.brand) < 0) {
            txt.textContent = "Masques de vie privee : non pilotables pour cette marque (a regler sur la camera)";
            rowEl.classList.add("is-na");
            return rowEl;
        }
        if (noCreds) { txt.textContent = "Masques de vie privee : identifiants requis"; rowEl.classList.add("is-na"); return rowEl; }
        var state = null;
        function show(st) {
            state = st;
            rowEl.className = "cm-privacy";
            btn.hidden = true;
            btn.disabled = false;
            if (!st.supported) { txt.textContent = "Masques de vie privee : " + (st.reason || "non pilotables"); rowEl.classList.add("is-na"); return; }
            if (!st.configured) { txt.textContent = "Masques de vie privee : aucun masque dessine sur la camera"; rowEl.classList.add("is-na"); return; }
            var n = st.channels.reduce(function (a, k) { return a + (k.masks || 0); }, 0);
            var partial = st.enabled && !st.all_enabled;
            txt.textContent = "Masques de vie privee : " + (st.all_enabled ? "ACTIFS" : partial ? "actifs sur une partie des canaux" : "inactifs") +
                (n ? " (" + n + " zone(s)" + (st.channels.length > 1 ? " sur " + st.channels.length + " canaux" : "") + ")" : "") +
                (st.last ? " - derniere bascule Cockpit : " + (st.last.enabled ? "activation" : "desactivation") + (st.last.by ? " par " + st.last.by : "") + ", " + ago(st.last.at) : "");
            rowEl.classList.add(st.all_enabled ? "is-on" : partial ? "is-partial" : "is-off");
            btn.textContent = "";
            btn.appendChild(icon(st.all_enabled ? "visibility" : "visibility_off"));
            btn.appendChild(document.createTextNode(st.all_enabled ? "Desactiver" : "Activer"));
            btn.hidden = false;
        }
        function load() {
            txt.textContent = c.cellular ? "Masques de vie privee : lecture (camera 4G, jusqu'a une minute)..." : "Masques de vie privee : lecture...";
            getJSON(API.list + "/" + c._id + "/privacy").then(show).catch(function (e) {
                txt.textContent = "Masques de vie privee : lecture impossible (" + e.message + ")";
                rowEl.className = "cm-privacy is-na";
                btn.hidden = false; btn.textContent = ""; btn.appendChild(icon("refresh")); btn.appendChild(document.createTextNode("Relire"));
                state = null;
            });
        }
        btn.addEventListener("click", function () {
            if (!state) { load(); return; }
            var on = !state.all_enabled;
            confirmToast((on ? "Activer" : "Desactiver") + " les masques de vie privee de " + c.name + " ? " +
                (on ? "Les zones masquees ne seront plus visibles ni enregistrees." : "Les zones masquees redeviennent visibles et enregistrees, pour tous les operateurs."),
                on ? "Activer" : "Desactiver").then(function (ok) {
                if (!ok) return;
                btn.disabled = true;
                txt.textContent = "Masques de vie privee : " + (on ? "activation" : "desactivation") + " en cours...";
                txt.textContent += " (puis capture de controle)";
                send("POST", API.list + "/" + c._id + "/privacy", { enabled: on }).then(function (st) {
                    show(st);
                    toast(st.ok ? "success" : "warning", st.ok ? "Masques " + (on ? "actives" : "desactives") + " : " + c.name
                        : "La camera a repondu mais l'etat relu ne correspond pas : verifiez sur la camera");
                    if (onCaptured && (st.captured || []).length) onCaptured(st.captured);
                }).catch(function (e) { toast("error", e.message); load(); });
            });
        });
        load();
        return rowEl;
    }

    // Bascule de TOUS les masques (super admin, /api/cameras/privacy-all) :
    // jauge par camera, captures de controle, vignettes rechargees a la fin
    var PALL = { poll: null, running: false };
    function openPrivacyAll() {
        var dlg = $("cm-privacy-dialog");
        if (!dlg.open) dlg.showModal();
        pollPrivacyAll();
    }
    function startPrivacyAll(on) {
        confirmToast((on ? "ACTIVER" : "DESACTIVER") + " les masques de vie privee de TOUTES les cameras pilotables ? " +
            (on ? "Les zones masquees ne seront plus visibles ni enregistrees."
                : "Les zones masquees redeviennent visibles et enregistrees, pour tous les operateurs."),
            on ? "Activer tout" : "Desactiver tout").then(function (ok) {
            if (!ok) return;
            send("POST", "/api/cameras/privacy-all", { enabled: on }).then(function (r) {
                toast("info", "Bascule lancee sur " + r.total + " camera(s)");
                pollPrivacyAll();
            }).catch(function (e) { toast("error", e.message); });
        });
    }
    function pollPrivacyAll() {
        clearTimeout(PALL.poll);
        getJSON("/api/cameras/privacy-all").then(function (j) {
            var was = PALL.running;
            PALL.running = !!j.running;
            $("cm-pall-on").disabled = $("cm-pall-off").disabled = PALL.running;
            var box = $("cm-pall-progress");
            if (!j.total) { box.hidden = true; return; }
            box.hidden = false;
            $("cm-pall-title").textContent = (j.running ? (j.enabled ? "Activation" : "Desactivation") + " en cours..."
                : "Derniere bascule : " + (j.enabled ? "activation" : "desactivation") + " (" + ago(j.finished_at) + ")") +
                (j.by ? " - " + j.by : "");
            $("cm-pall-fill").style.width = Math.round(100 * j.done / j.total) + "%";
            $("cm-pall-stats").textContent = j.done + " / " + j.total + " camera(s) traitee(s) : " +
                (j.changed || 0) + " basculee(s) et capturee(s), " + (j.unchanged || 0) + " deja dans cet etat, " +
                (j.nomask || 0) + " sans masque, " + (j.failed || 0) + " echec(s)";
            var ul = $("cm-pall-fails");
            ul.textContent = "";
            (j.failures || []).forEach(function (f) { ul.appendChild(el("li", "", f.name + " : " + f.error)); });
            if (j.running) PALL.poll = setTimeout(pollPrivacyAll, 1500);
            else if (was) {
                toast(j.failed ? "warning" : "success", "Masques " + (j.enabled ? "actives" : "desactives") + " : " +
                    (j.changed || 0) + " camera(s) basculee(s)" + (j.failed ? ", " + j.failed + " echec(s)" : ""));
                if (S.view === "map") loadMapData().then(render);
            }
        }).catch(function (e) { toast("error", "Progression illisible : " + e.message); });
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
        $("cm-map-cones").addEventListener("click", function () { setConeMode(!M.cones); });
        $("cm-map-tour").addEventListener("click", startTour);
        $("cm-map-plan").addEventListener("click", function () { setAddPlanned(!M.addPlanned); });
        $("cm-map-dossier").addEventListener("click", openDossier);
        $("cm-dos-go").addEventListener("click", makeDossier);
        Array.prototype.forEach.call(document.querySelectorAll("#cm-dos-status input"), function (cb) { cb.addEventListener("change", dossierCount); });
        Array.prototype.forEach.call(document.querySelectorAll("#cm-dossier-dialog [data-close]"), function (b) {
            b.addEventListener("click", function () { $("cm-dossier-dialog").close(); });
        });
        $("cm-privacy-all-btn").addEventListener("click", openPrivacyAll);
        $("cm-pall-on").addEventListener("click", function () { startPrivacyAll(true); });
        $("cm-pall-off").addEventListener("click", function () { startPrivacyAll(false); });
        Array.prototype.forEach.call(document.querySelectorAll("#cm-privacy-dialog [data-close]"), function (b) {
            b.addEventListener("click", function () { $("cm-privacy-dialog").close(); });
        });
        try { M.autoShot = localStorage.getItem("cockpit-cameras-autoshot") !== "0"; } catch (e) { M.autoShot = true; }
        try { M.lockFov = localStorage.getItem("cockpit-cameras-lockfov") !== "0"; } catch (e) { M.lockFov = true; }
        $("cm-map-place").addEventListener("change", onPlaceSelect);
        $("cm-profile-add").addEventListener("click", function () { renderProfiles(true); });
        Array.prototype.forEach.call(document.querySelectorAll("#cm-profiles-dialog [data-close]"), function (b) {
            b.addEventListener("click", function () { $("cm-profiles-dialog").close(); });
        });
        $("cm-lightbox").addEventListener("click", closeLightbox);
        document.addEventListener("keydown", function (e) {
            if (coneKeys(e)) return;
            if (e.key !== "Escape") return;
            if (!$("cm-lightbox").hidden) closeLightbox();
            else if (!$("cm-cone-panel").hidden) closeCone(false);
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
