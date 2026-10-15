/* ia_capture.js -- Page IA : modes Trafic et Galerie (le mode Intrusions est
   detection.js). Les captures sont prises par la tache planifiee "Cockpit -
   Capture IA" (capture_ia.py), jamais par la page : ici on lit, on regle et
   on donne son avis (entrainement de l'IA). Serveur : ia_capture.py. */
(function () {
    "use strict";

    var API = {
        cams: "/api/detection/capture/cameras",
        samples: "/api/detection/capture/samples",
        zones: "/api/detection/capture/zones",
        gallery: "/api/detection/gallery",
        captureSwitch: "/api/detection/capture/switch",        groups: "/api/detection/groups",
        episodes: "/api/detection/episodes",
    };
    var TZ = "Europe/Paris";
    // Etats : trafic (fluide / dense / ralenti / bouchon) et foule (normal / charge / trop)
    var STATE_LABEL = { fluide: "Fluide", dense: "Dense", ralenti: "Ralenti", bouchon: "Bouchon", inconnu: "--",
                        normal: "Normal", charge: "Charge", trop: "Trop de monde",
                        rien: "Personne au sol : non", sol: "Personne au sol", immobile: "Au sol, immobile" };
    var CROWD_STATES = ["normal", "charge", "trop"];
    var TRAFFIC_STATES = ["fluide", "dense", "ralenti", "bouchon"];
    var RESCUE_STATES = ["rien", "sol", "immobile"];
    // Fonction d'une camera en Capture IA : nom, icone, mode de la page
    var PURPOSE_UI = { traffic: { name: "Trafic", icon: "traffic", mode: "trafic" },
                       crowd: { name: "Foule", icon: "groups", mode: "foule" },
                       secours: { name: "Secours", icon: "personal_injury", mode: "secours" } };
    function purposeOf(x) { return x ? (x.purpose || (x.role && x.role.purpose) || "traffic") : null; }
    function isCrowd(x) { return purposeOf(x) === "crowd"; }
    // Secours : une photo, squelette et posture de chaque personne
    function isRescue(x) { return purposeOf(x) === "secours"; }
    // Une seule photo par mesure (foule, secours) : ni paires, ni vehicules
    function isSingle(x) { return isCrowd(x) || isRescue(x); }
    var POSTURE = { allonge: { label: "au sol", color: "#dc2626" }, incline: { label: "penche", color: "#f59e0b" },
                    vertical: { label: "debout ou assis", color: "#16a34a" }, inconnu: { label: "posture incertaine", color: "#94a3b8" } };
    // Statut d'un vehicule (couleur du cadre). Deux paires (method "flux") :
    // arrete / lent / bref / roule ; anciennes paires A/B : arrete / deplace /
    // parti sur A, arrive / suivi sur B
    var VSTAT = {
        arrete: { label: "arrete", color: "#dc2626" },
        lent: { label: "roule lentement", color: "#f59e0b" },
        bref: { label: "arret bref (cede le passage)", color: "#a855f7" },
        roule: { label: "roule", color: "#16a34a" },
        autre: { label: "pas relie a la 1re image de la paire", color: "#94a3b8" },
        deplace: { label: "avance lentement", color: "#f59e0b" },
        parti: { label: "roule (parti)", color: "#16a34a" },
        arrive: { label: "arrive entre les 2 photos", color: "#2563eb" },
        suivi: { label: "deja vu sur A", color: "#94a3b8" },
    };
    var WAZE_ALERT = { JAM: "bouchon", ACCIDENT: "accident", ROAD_CLOSED: "route fermee", HAZARD: "danger", WEATHERHAZARD: "danger" };
    var AI_NAME = { personne: "personne", vehicule: "vehicule", velo: "velo", oiseau: "oiseau", animal: "animal",
                    panneau: "panneau", branche: "branches", reflet: "reflet", sol: "sol vide", grillage: "grillage",
                    objet: "objet", rien: "rien" };

    var M = { mode: "carte", hours: 24, data: null, timer: null, selected: {}, focusKey: null,
              gmap: null, gmapLayer: null, gmapData: null, gmapFilter: "all", gmapFitted: false,
              gal: { source: "all", mode: "revoir", items: [], next: null, seq: 0 } };

    /* ---------------- utilitaires ---------------- */
    function $(id) { return document.getElementById(id); }
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function icon(n) { return el("span", "material-symbols-outlined", n); }
    function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ""; }
    function getJSON(url) {
        return fetch(url, { credentials: "same-origin" }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (j) {
                if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
                return j;
            });
        });
    }
    function send(method, url, body) {
        return fetch(url, {
            method: method, credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
            body: body === undefined ? undefined : JSON.stringify(body),
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
    function dayKey(iso) { return new Date(iso).toLocaleDateString("fr-FR", { timeZone: TZ }); }
    function isToday(iso) { return !!iso && dayKey(iso) === dayKey(new Date().toISOString()); }
    function ago(iso) {
        if (!iso) return "";
        var s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
        if (s < 60) return "a l'instant";
        if (s < 3600) return "il y a " + Math.round(s / 60) + " min";
        if (s < 86400) return "il y a " + Math.round(s / 3600) + " h";
        return fmtDay(iso) + " " + fmtHM(iso);
    }
    function chip(cls, ic, text, title) {
        var c = el("span", "ia-chip " + (cls || ""));
        if (ic) c.appendChild(icon(ic));
        c.appendChild(document.createTextNode(text));
        if (title) c.title = title;
        return c;
    }
    function stateChip(s) {
        var ic = { bouchon: "traffic_jam", ralenti: "speed", dense: "directions_car", trop: "groups", charge: "group", normal: "check_circle",
                   sol: "personal_injury", immobile: "emergency" }[s] || "check_circle";
        return chip("ia-state s-" + (s || "inconnu"), ic, STATE_LABEL[s] || "--");
    }
    function svgEl(tag, attrs) {
        var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
        Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
        return e;
    }
    function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

    /* ---------------- modes ---------------- */
    var MODES = ["carte", "intrusions", "trafic", "foule", "secours", "galerie"];
    function setMode(m) {
        M.mode = m;
        MODES.forEach(function (x) { document.body.classList.toggle("ia-mode-" + x, x === m); });
        Array.prototype.forEach.call(document.querySelectorAll("#ia-modes button"), function (b) {
            b.classList.toggle("active", b.dataset.mode === m);
        });
        $("ia-carte").hidden = m !== "carte";
        $("ia-trafic").hidden = m !== "trafic";
        $("ia-foule").hidden = m !== "foule";
        $("ia-secours").hidden = m !== "secours";
        $("ia-galerie").hidden = m !== "galerie";
        clearInterval(M.timer);
        if (m === "carte") { loadGmap(); M.timer = setInterval(loadGmap, 60000); }
        else if (m === "trafic" || m === "foule" || m === "secours") { loadTrafic(); M.timer = setInterval(loadTrafic, 60000); }
        else if (m === "galerie") loadGallery(true);
        else if (window.IADetection) window.IADetection.refresh();
    }

    /* ---------------- Carte globale (ouverture) ---------------- */
    var CIRCUIT = [47.9497, 0.2076];
    var STATE_COLOR = { fluide: "#16a34a", dense: "#ca8a04", ralenti: "#ea580c", bouchon: "#dc2626", inconnu: "#7c3aed",
                        normal: "#16a34a", charge: "#ea580c", trop: "#dc2626",
                        rien: "#16a34a", sol: "#ea580c", immobile: "#dc2626" };
    function initGmap() {
        if (M.gmap || !window.L) return;
        M.gmap = L.map("ia-gmap", { center: CIRCUIT, zoom: 14, maxZoom: 22, zoomControl: false });
        L.control.zoom({ position: "bottomright" }).addTo(M.gmap);
        var ign = L.tileLayer("https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}", { attribution: "IGN-F/Geoplateforme", maxNativeZoom: 19, maxZoom: 22 });
        var osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "OSM", maxNativeZoom: 19, maxZoom: 22 });
        var aco = L.tileLayer("/tiles/{z}/{x}/{y}.png", { attribution: "ACO", tms: true, maxZoom: 22 });
        ign.addTo(M.gmap);
        L.control.layers({ "Vue aerienne": ign, "Plan": osm, "Plan ACO": aco }, {}, { position: "topright" }).addTo(M.gmap);
        M.gmapLayer = L.layerGroup().addTo(M.gmap);
        if (window.ResizeObserver) new ResizeObserver(function () { M.gmap.invalidateSize(); }).observe($("ia-gmap"));
    }
    function loadGmap() {
        initGmap();
        if (!M.gmap) return;
        setTimeout(function () { M.gmap.invalidateSize(); }, 50);
        return getJSON("/api/detection/global-map").then(function (d) {
            M.gmapData = d;
            renderGmap();
        }).catch(function (e) { toast("error", "Carte indisponible : " + e.message); });
    }
    function gmapShown(c) {
        var f = M.gmapFilter;
        if (f === "ia") return c.intrusion || c.traffic || c.crowd || c.secours;
        if (f === "intrusion") return c.intrusion;
        if (f === "traffic") return c.traffic;
        if (f === "crowd") return c.crowd;
        if (f === "secours") return c.secours;
        return true;
    }
    function gmapIcon(c) {
        // Toutes les cameras ont un logo : camera (gris), intrusion (orange),
        // trafic / foule (couleur de l'etat), piste (sombre, cadenas)
        var cls = "ia-pin", ic = "videocam", size = 24, color = "#475569";
        // Foule : carre arrondi (le trafic est rond), couleur du niveau
        // Secours d'abord : une personne au sol se voit avant le reste
        if (c.secours && (c.secours_state === "sol" || c.secours_state === "immobile" || !(c.crowd || c.traffic))) {
            cls += " is-traffic is-crowd"; ic = "personal_injury"; size = 32; color = STATE_COLOR[c.secours_state || "inconnu"];
        }
        else if (c.crowd) { cls += " is-traffic is-crowd"; ic = "groups"; size = 32; color = STATE_COLOR[c.crowd_state || "inconnu"]; }
        else if (c.traffic) { cls += " is-traffic"; ic = "traffic"; size = 32; color = STATE_COLOR[c.traffic_state || "inconnu"]; }
        else if (c.intrusion) { cls += " is-intr"; ic = "directions_walk"; size = 28; color = "#d97706"; }
        else if (c.piste) { cls += " is-piste"; ic = "lock"; color = "#1e293b"; }
        else if (c.anpr) { ic = "directions_car"; color = "#2563eb"; }
        if (c.id === M.gSel) cls += " is-sel";
        if (c.traffic && c.intrusion) cls += " is-both";
        if (!c.enabled) cls += " is-off";
        var html = '<span class="ia-pin-dot" style="background:' + color + '">' +
            (ic ? '<span class="material-symbols-outlined">' + ic + "</span>" : "") + "</span>" +
            (c.intrusions_open ? '<b class="ia-pin-n">' + c.intrusions_open + "</b>" : "");
        return L.divIcon({ className: cls, html: html, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
    }
    /* Volet de detail d'une camera (clic sur la carte) */
    var ROLE_LABEL = { fielddetection: ["directions_walk", "Detection d'intrusion"], capture: ["traffic", "Analyse du trafic"],
                       anpr: ["directions_car", "LAPI"] };
    function section(title, ic) {
        var s = el("div", "ia-gd-sec");
        var h = el("h4", "dt-sec");
        if (ic) h.appendChild(icon(ic));
        h.appendChild(document.createTextNode(title));
        s.appendChild(h);
        return s;
    }
    function btn(ic, text, fn, cls) {
        var b = el("button", "dt-btn" + (cls ? " " + cls : "")); b.type = "button";
        b.appendChild(icon(ic)); b.appendChild(document.createTextNode(text));
        b.addEventListener("click", fn);
        return b;
    }
    function closeGDetail() { $("ia-gdetail").hidden = true; M.gSel = null; renderGmap(); }
    function openGDetail(c) {
        M.gSel = c.id;
        renderGmap();
        var box = $("ia-gdetail"), body = $("ia-gdetail-body");
        box.hidden = false;
        $("ia-gdetail-title").textContent = c.name;
        body.textContent = "";
        body.appendChild(el("div", "dt-skel"));
        getJSON("/api/detection/global-map/" + encodeURIComponent(c.id)).then(function (d) {
            if (M.gSel !== c.id) return;
            body.textContent = "";
            renderGDetail(body, c, d);
        }).catch(function (e) {
            body.textContent = "";
            body.appendChild(el("p", "dt-help", "Fiche indisponible (" + e.message + ")."));
        });
    }
    function renderGDetail(body, c, d) {
        // Usages et etat
        var chips = el("div", "ia-pop-chips");
        if (c.intrusion) chips.appendChild(chip("", "directions_walk", "Detection d'intrusion"));
        if (c.traffic) chips.appendChild(stateChip(c.traffic_state));
        if (c.crowd) chips.appendChild(stateChip(c.crowd_state));
        if (c.secours) chips.appendChild(stateChip(c.secours_state));
        if (c.anpr) chips.appendChild(chip("", "directions_car", "LAPI"));
        if (d.cellular) chips.appendChild(chip("", "signal_cellular_alt", "4G"));
        if (d.piste) chips.appendChild(chip("is-obs", "lock", "Piste : jamais pilotee"));
        if (!d.enabled) chips.appendChild(chip("is-err", "block", "Desactivee"));
        if (!c.intrusion && !c.traffic && !c.crowd && !c.secours && !c.anpr && !d.piste) chips.appendChild(chip("is-obs", "videocam", "Non surveillee par l'IA"));
        body.appendChild(chips);
        var info = [d.site, d.location, [d.brand, d.model].filter(Boolean).join(" "), d.ip ? "IP " + d.ip : "",
                    d.channels.length > 1 ? d.channels.length + " canaux" : ""].filter(Boolean);
        if (info.length) body.appendChild(el("div", "ia-meta", info.join(" - ")));

        // Image : un onglet par canal qui en a une (capture de la page Cameras,
        // sinon derniere photo d'intrusion ou de Capture IA)
        var withImg = d.channels.filter(function (ch) { return ch.image; });
        var img = el("div", "ia-gd-img");
        var tabs = withImg.length > 1 ? el("div", "ia-gd-tabs") : null;
        var SRC = { capture: "capture", intrusion: "photo d'intrusion", ia: "photo Capture IA" };
        function showImg(ch) {
            img.textContent = "";
            var a = document.createElement("a"); a.href = ch.image.url; a.target = "_blank"; a.rel = "noopener"; a.title = "Ouvrir en grand";
            var im = document.createElement("img"); im.alt = "Image du canal " + ch.no;
            im.src = ch.image.url + (ch.image.source === "capture" ? (ch.image.url.indexOf("?") < 0 ? "?" : "&") + "t=" + Date.now() : "");
            a.appendChild(im);
            img.appendChild(a);
            img.appendChild(el("span", "ia-shot-cap", (d.channels.length > 1 ? (ch.name || "Canal " + ch.no) + " - " : "") +
                SRC[ch.image.source] + " " + ago(ch.image.at)));
            if (tabs) Array.prototype.forEach.call(tabs.children, function (b) { b.classList.toggle("is-on", b.dataset.no === String(ch.no)); });
        }
        if (withImg.length) {
            if (tabs) {
                withImg.forEach(function (ch) {
                    var b = el("button", "ia-gd-tab"); b.type = "button"; b.dataset.no = String(ch.no);
                    var n = ch.intrusions.armed;
                    b.appendChild(document.createTextNode(ch.name && ch.name !== d.name ? ch.name : "Canal " + ch.no));
                    if (n) b.appendChild(el("b", "ia-gd-tab-n", String(n)));
                    b.title = n ? n + " intrusion(s) dans le planning aujourd'hui" : "";
                    b.addEventListener("click", function () { showImg(ch); });
                    tabs.appendChild(b);
                });
                body.appendChild(tabs);
            }
            // Canal le plus actif en premier (intrusions du jour), sinon le premier
            showImg(withImg.slice().sort(function (a, b) { return b.intrusions.armed - a.intrusions.armed; })[0]);
        } else img.appendChild(el("div", "ia-meta", "Aucune image de cette camera : lancez une capture depuis la page Cameras."));
        body.appendChild(img);

        // Trafic (canaux en analyse)
        d.channels.filter(function (ch) { return ch.traffic; }).forEach(function (ch) {
            var t = ch.traffic;
            var pk = PURPOSE_UI[t.purpose] || PURPOSE_UI.traffic;
            var s = section(pk.name + (d.channels.length > 1 ? " - " + ch.name : ""), pk.icon);
            var row = el("div", "ia-pop-chips");
            row.appendChild(stateChip(t.state));
            row.appendChild(el("span", "ia-meta", t.last ? "mesure " + ago(t.last.at) + " - " + (t.purpose === "traffic" || !t.purpose ? "une paire" : "une photo") +
                " toutes les " + t.role.interval_min + " min" : "pas encore de mesure"));
            s.appendChild(row);
            if (t.last_error) s.appendChild(chip("is-err", "error", "Derniere erreur : " + t.last_error));
            if (t.last) {
                s.appendChild(sampleView(t.last, []));
                if (isRescue(t.last)) s.appendChild(el("div", "ia-meta", rescueText(t.last)));
                (t.last.zones || []).forEach(function (z) { s.appendChild(el("div", "ia-meta", z.name + " : " + zoneText(z))); });
            }
            s.appendChild(btn("open_in_new", "Ouvrir dans le mode " + pk.name, function () {
                M.focusKey = t.key; setMode(pk.mode);
            }));
            body.appendChild(s);
        });

        // Intrusions : compteurs du jour (cliquables) puis dernieres photos.
        // Une intrusion au sens propre n'existe que DANS le planning ; le hors
        // planning est a part (entrainement), jamais compte comme alerte
        if (c.intrusion || d.episodes.length || d.episodes_off.length) {
            var si = section("Intrusions aujourd'hui", "directions_walk");
            var tot = { armed: 0, open: 0, offplan: 0 };
            d.channels.forEach(function (ch) { ["armed", "open", "offplan"].forEach(function (k) { tot[k] += ch.intrusions[k]; }); });
            function openList(planning) {
                if (window.IADetection) window.IADetection.filterCamera(c.id + ":*", planning);
                setMode("intrusions");
                if (window.IADetection) window.IADetection.refresh();
            }
            var kp = el("div", "ia-gd-kpis");
            [[tot.armed, "dans le planning", "", "is-armed"], [tot.open, "a traiter", "", "is-open"],
             [tot.offplan, "hors planning", "off", "is-off"]].forEach(function (k) {
                var b = el("button", "ia-gd-kpi " + k[3] + (k[0] ? "" : " is-zero")); b.type = "button";
                b.appendChild(el("b", "", String(k[0])));
                b.appendChild(el("span", "", k[1]));
                b.title = "Voir la liste";
                b.addEventListener("click", function () { openList(k[2]); });
                kp.appendChild(b);
            });
            si.appendChild(kp);
            var seg = el("div", "dt-seg ia-gd-seg");
            var grid = el("div", "ia-gd-grid");
            function showEps(off) {
                Array.prototype.forEach.call(seg.children, function (b, i) { b.classList.toggle("active", (i === 1) === off); });
                grid.textContent = "";
                var list = off ? d.episodes_off : d.episodes;
                list.forEach(function (ep) {
                    var r = el("button", "ia-gd-ep"); r.type = "button";
                    var th = el("span", "ia-gd-ep-img");
                    if (ep.thumb) { var ti = document.createElement("img"); ti.src = ep.thumb; ti.alt = ""; ti.loading = "lazy"; th.appendChild(ti); }
                    else th.appendChild(icon("no_photography"));
                    if (ep.status === "new" && !off) th.appendChild(el("i", "ia-gd-ep-new", ""));
                    r.appendChild(th);
                    var ai = ep.ai || {};
                    var lab = ai.truth ? "Avis : " + (AI_NAME[ai.truth] || ai.truth)
                        : ai.status === "filtered" ? "Filtree : " + (AI_NAME[ai.label] || ai.label || "rien")
                        : ai.status === "confirmed" ? "IA : " + (AI_NAME[ai.label] || ai.label) : "Non verifiee";
                    r.appendChild(el("b", "", (isToday(ep.started_at) ? "" : fmtDay(ep.started_at) + " ") + fmtTime(ep.started_at)));
                    r.appendChild(el("span", "ia-meta", (d.channels.length > 1 && ep.channel_name ? ep.channel_name + " - " : "") + lab));
                    r.title = "Ouvrir la fiche de l'intrusion";
                    r.addEventListener("click", function () {
                        if (window.IADetection) window.IADetection.filterCamera(c.id + ":*", off ? "off" : "");
                        setMode("intrusions");
                        if (window.IADetection) window.IADetection.openEpisode(ep.id);
                    });
                    grid.appendChild(r);
                });
                if (!list.length) grid.appendChild(el("div", "ia-meta", off ? "Aucune detection hors planning." : "Aucune intrusion dans le planning."));
            }
            [["Dernieres dans le planning", false], ["Dernieres hors planning", true]].forEach(function (o) {
                var b = el("button", "", o[0]); b.type = "button";
                b.addEventListener("click", function () { showEps(o[1]); });
                seg.appendChild(b);
            });
            si.appendChild(seg);
            si.appendChild(grid);
            showEps(!d.episodes.length && d.episodes_off.length > 0);
            body.appendChild(si);
        }

        // Canaux et usages : replie (reglages, rarement utile en exploitation)
        var sc = el("details", "ia-gd-sec ia-gd-chans");
        var sum = el("summary", "dt-sec");
        sum.appendChild(icon("videocam"));
        sum.appendChild(document.createTextNode("Canaux et usages (" + d.channels.length + ")"));
        sc.appendChild(sum);
        d.channels.forEach(function (ch) {
            var r = el("div", "ia-group");
            r.appendChild(el("span", "", ch.no + " - " + (ch.name || "Canal " + ch.no)));
            r.appendChild(el("span", "header-spacer"));
            if (!ch.roles.length) r.appendChild(el("span", "ia-meta", "aucun usage"));
            ch.roles.forEach(function (ro) { var l = ROLE_LABEL[ro]; if (l) r.appendChild(chip("", l[0], l[1])); });
            if (d.pilotable && ch.roles.indexOf("capture") < 0) {
                [["traffic", "traffic", "Analyser le trafic", "Analyse du trafic"], ["crowd", "groups", "Analyser la foule", "Analyse de foule"],
                 ["secours", "personal_injury", "Surveiller (secours)", "Surveillance des personnes au sol"]].forEach(function (o) {
                    r.appendChild(btn(o[1], o[2], function () {
                        send("POST", API.cams, { camera_id: d.id, channel: ch.no, purpose: o[0] }).then(function () {
                            toast("success", o[3] + " ajoutee : premiere mesure dans les minutes qui viennent");
                            loadGmap(); openGDetail(c);
                        }).catch(function (e) { toast("error", e.message); });
                    }, "ia-btn-sm"));
                });
            }
            sc.appendChild(r);
        });
        body.appendChild(sc);

        var acts = el("div", "ia-cam-actions");
        if (c.anpr) acts.appendChild(btn("directions_car", "Page LAPI", function () { location.href = "/anpr"; }));
        if (d.is_admin) acts.appendChild(btn("settings", "Page Cameras", function () { location.href = "/cameras"; }));
        if (acts.children.length) body.appendChild(acts);
    }
    function renderGmap() {
        if (!M.gmap || !M.gmapData) return;
        M.gmapLayer.clearLayers();
        var shown = (M.gmapData.cameras || []).filter(gmapShown);
        // Les cameras surveillees par-dessus les autres
        shown.sort(function (a, b) { return (a.traffic || a.crowd || a.secours || a.intrusion ? 1 : 0) - (b.traffic || b.crowd || b.secours || b.intrusion ? 1 : 0); });
        var pts = [];
        shown.forEach(function (c) {
            var mk = L.marker([c.lat, c.lng], { icon: gmapIcon(c), zIndexOffset: c.traffic || c.crowd || c.secours ? 20000 : c.intrusion ? 10000 : 0 });
            // Texte (pas de HTML) : le nom vient de la base, Leaflet interprete une chaine comme du HTML
            mk.bindTooltip(el("span", "", c.name + (c.traffic ? " - trafic " + (STATE_LABEL[c.traffic_state] || "--").toLowerCase() : "") +
                                              (c.crowd ? " - foule " + (STATE_LABEL[c.crowd_state] || "--").toLowerCase() : "") +
                                              (c.secours ? " - secours : " + (STATE_LABEL[c.secours_state] || "--").toLowerCase() : "")),
                           { className: "dt-tip", direction: "top", offset: [0, -10] });
            mk.on("click", function () { openGDetail(c); });
            mk.addTo(M.gmapLayer);
            // Cadrage : cameras a moins de ~30 km du circuit (boutique de Paris exclue)
            if (Math.abs(c.lat - CIRCUIT[0]) < 0.27 && Math.abs(c.lng - CIRCUIT[1]) < 0.4) pts.push([c.lat, c.lng]);
        });
        var nIa = (M.gmapData.cameras || []).filter(function (c) { return c.traffic || c.crowd || c.secours || c.intrusion; }).length;
        $("ia-gmap-count").textContent = shown.length + " camera(s) affichee(s), dont " + nIa + " surveillee(s) par l'IA" +
            (M.gmapData.unplaced ? " - " + M.gmapData.unplaced + " sans position" : "");
        if (!M.gmapFitted && pts.length && $("ia-gmap").offsetWidth) {
            M.gmap.fitBounds(L.latLngBounds(pts).pad(0.05), { maxZoom: 17 });
            M.gmapFitted = true;
        }
    }

    /* ---------------- photo + cadres (SVG 0-1) ---------------- */
    // Itineraire trace : ligne du depart a l'arrivee, rond au depart, fleche a
    // l'arrivee (calculee en 16:9 pour ne pas etre deformee par le SVG 0-1)
    var AR = 16 / 9;
    function traceSvg(svg, line, color, label) {
        if (!line || line.length < 2) return;
        var pts = line.map(function (p) { return p[0] + "," + p[1]; }).join(" ");
        svg.appendChild(svgEl("polyline", { points: pts, fill: "none", stroke: "rgba(15,23,42,.55)", "stroke-width": 6,
            "stroke-linecap": "round", "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" }));
        svg.appendChild(svgEl("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 3,
            "stroke-linecap": "round", "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" }));
        var a = line[line.length - 2], b = line[line.length - 1];
        var dx = (b[0] - a[0]) * AR, dy = b[1] - a[1], n = Math.sqrt(dx * dx + dy * dy) || 1;
        var ux = dx / n, uy = dy / n, s = 0.035;
        var tip = [b[0], b[1]];
        var l = [b[0] + (-ux * s - uy * s * 0.6) / AR, b[1] + (-uy * s + ux * s * 0.6)];
        var r = [b[0] + (-ux * s + uy * s * 0.6) / AR, b[1] + (-uy * s - ux * s * 0.6)];
        svg.appendChild(svgEl("polygon", { points: [tip, l, r].map(function (p) { return p.join(","); }).join(" "),
            fill: color, stroke: "rgba(15,23,42,.55)", "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
        svg.appendChild(svgEl("ellipse", { cx: line[0][0], cy: line[0][1], rx: 0.008 / AR, ry: 0.008, fill: color,
            stroke: "#fff", "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" }));
        if (label) {
            var t = svgEl("title", {}); t.textContent = label;
            svg.lastChild.appendChild(t);
        }
    }
    function shotView(photo, vehicles, zones, caption, which) {
        var w = el("div", "ia-shot");
        var im = document.createElement("img");
        im.alt = caption; im.loading = "lazy"; im.src = photo;
        w.appendChild(im);
        var svg = svgEl("svg", { viewBox: "0 0 1 1", preserveAspectRatio: "none" });
        var traceCol = {}, traceName = {};
        (zones || []).forEach(function (z) {
            if (z.kind === "itineraire") {
                traceCol[z.id] = z.color || "#0891b2";
                traceName[z.id] = z.name;
                traceSvg(svg, z.line, traceCol[z.id], z.name);
                // Nom du trace pres de son depart : on sait quel sens est lequel
                if (z.line && z.line.length) {
                    var tl = el("span", "ia-trace-lbl", z.name);
                    tl.style.left = (z.line[0][0] * 100).toFixed(2) + "%";
                    tl.style.top = (z.line[0][1] * 100).toFixed(2) + "%";
                    tl.style.background = traceCol[z.id];
                    w.appendChild(tl);
                }
                return;
            }
            if (!z.polygon || z.polygon.length < 3) return;
            svg.appendChild(svgEl("polygon", {
                points: z.polygon.map(function (p) { return p[0] + "," + p[1]; }).join(" "),
                fill: "rgba(124,58,237,.10)", stroke: z.color || "#7c3aed", "stroke-width": 2,
                "vector-effect": "non-scaling-stroke", "stroke-dasharray": "6 4",
            }));
        });
        (vehicles || []).forEach(function (v) {
            var st = VSTAT[v.status] || VSTAT.suivi;
            var r = svgEl("rect", {
                x: v.box.x, y: v.box.y, width: v.box.w, height: v.box.h, fill: "none",
                stroke: st.color, "stroke-width": 2.5, "vector-effect": "non-scaling-stroke",
            });
            var t = svgEl("title", {}); t.textContent = v["class"] + " " + Math.round(v.conf * 100) + " % - " + st.label +
                (v.speed != null ? " (" + v.speed.toFixed(2) + " longueur/s)" : "") +
                (v.trace && traceName[v.trace] ? " - " + traceName[v.trace] : "");
            r.appendChild(t);
            svg.appendChild(r);
            // Pastille a la couleur du trace auquel l'IA rattache le vehicule
            if (v.trace && traceCol[v.trace]) {
                svg.appendChild(svgEl("ellipse", { cx: v.box.x + v.box.w / 2, cy: v.box.y + v.box.h / 2, rx: 0.007 / AR, ry: 0.007,
                    fill: traceCol[v.trace], stroke: "#fff", "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" }));
            }
        });
        w.insertBefore(svg, w.querySelector(".ia-trace-lbl"));
        if (caption) w.appendChild(el("span", "ia-shot-cap", caption));
        return w;
    }
    // Legende des cadres : photo A (ce que devient chaque vehicule) puis
    // photo B (nouveaux venus / vehicules deja vus sur A)
    function legend(sample) {
        var l = el("div", "ia-legend");
        var groups = sample && sample.method !== "flux" ? [["Photo A :", ["arrete", "deplace", "parti"]], ["Photo B :", ["arrive", "suivi"]]]
            : [["Vitesse sur chaque paire :", ["arrete", "lent", "bref", "roule", "autre"]]];
        groups.forEach(function (grp) {
            l.appendChild(el("b", "ia-legend-grp", grp[0]));
            grp[1].forEach(function (k) {
                var s = el("span");
                var i = el("i"); i.style.setProperty("--c", VSTAT[k].color);
                s.appendChild(i); s.appendChild(document.createTextNode(VSTAT[k].label));
                l.appendChild(s);
            });
        });
        return l;
    }
    function pairView(sample, zones) {
        var p = el("div", "ia-pair");
        if (!sample || !sample.photos || sample.photos.length < 2) {
            p.appendChild(el("div", "ia-empty", "Pas encore de mesure"));
            return p;
        }
        var flux = sample.method === "flux" && sample.photos.length >= 4;
        var a = flux ? shotView(sample.photos[0].photo, sample.vehicles_a, zones, "1re paire " + fmtTime(sample.photos[0].at), "a")
            : shotView(sample.photos[0].photo, sample.vehicles_a, zones, "A " + fmtTime(sample.photos[0].at), "a");
        var b = flux ? shotView(sample.photos[2].photo, sample.vehicles_b, zones, "2e paire +" + Math.round(sample.gap_s || 0) + " s", "b")
            : shotView(sample.photos[1].photo, sample.vehicles_b, zones, "B +" + Math.round(sample.gap_s || 0) + " s", "b");
        a.addEventListener("click", function () { openViewer(sample, zones); });
        b.addEventListener("click", function () { openViewer(sample, zones); });
        p.appendChild(a); p.appendChild(b);
        return p;
    }
    // Foule : une photo, carte de densite (rouge = dense) et zones colorees
    // selon leur niveau
    function crowdView(sample, zones) {
        var w = el("div", "ia-crowd-shot");
        if (!sample || !sample.photos || !sample.photos.length) {
            w.appendChild(el("div", "ia-empty", "Pas encore de mesure"));
            return w;
        }
        var shot = shotView(sample.photos[0].photo, [], [], fmtTime(sample.photos[0].at) + " - ~" + Math.round(sample.total || 0) + " pers.");
        var svg = shot.querySelector("svg");
        var g = sample.grid || [];
        if (g.length) {
            var gh = g.length, gw = g[0].length;
            g.forEach(function (row, i) {
                row.forEach(function (v, j) {
                    if (v < 0.08) return;
                    svg.appendChild(svgEl("rect", { x: j / gw, y: i / gh, width: 1 / gw, height: 1 / gh,
                        fill: "rgba(239,68,68," + Math.min(0.65, v * 0.7).toFixed(2) + ")" }));
                });
            });
        }
        var byId = {};
        (sample.zones || []).forEach(function (z) { byId[z.id] = z; });
        (zones || []).forEach(function (z) {
            if (!z.polygon || z.polygon.length < 3) return;
            var m = byId[z.id] || {};
            var col = STATE_COLOR[m.state] || z.color || "#7c3aed";
            svg.appendChild(svgEl("polygon", { points: z.polygon.map(function (p) { return p[0] + "," + p[1]; }).join(" "),
                fill: "none", stroke: col, "stroke-width": 3, "vector-effect": "non-scaling-stroke" }));
        });
        shot.addEventListener("click", function () { openViewer(sample, zones); });
        w.appendChild(shot);
        return w;
    }
    // Secours : une photo, un cadre par personne a la couleur de sa posture
    // (rouge = au sol), son squelette, et les zones secours
    var LIMBS = [[5, 7], [7, 9], [6, 8], [8, 10], [5, 6], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]];
    function rescueView(sample, zones) {
        var w = el("div", "ia-crowd-shot");
        if (!sample || !sample.photos || !sample.photos.length) {
            w.appendChild(el("div", "ia-empty", "Pas encore de mesure"));
            return w;
        }
        var persons = sample.persons_pose || [];
        var shot = shotView(sample.photos[0].photo, [], (zones || []).filter(function (z) { return z.kind === "secours"; }),
                            fmtTime(sample.photos[0].at) + " - " + (sample.total || 0) + " pers." + (sample.lying ? ", " + sample.lying + " au sol" : ""));
        var svg = shot.querySelector("svg");
        persons.forEach(function (q) {
            var ps = POSTURE[q.posture] || POSTURE.inconnu;
            var col = q.posture === "allonge" && !q.counted ? "#f59e0b" : ps.color;
            var k = q.kpts || [];
            LIMBS.forEach(function (l) {
                var a = k[l[0]], b = k[l[1]];
                if (!a || !b || a[2] < 0.35 || b[2] < 0.35) return;
                svg.appendChild(svgEl("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: col, "stroke-width": 2,
                    "stroke-opacity": 0.85, "vector-effect": "non-scaling-stroke" }));
            });
            var r = svgEl("rect", { x: q.box.x, y: q.box.y, width: q.box.w, height: q.box.h, fill: q.counted ? "rgba(220,38,38,.18)" : "none",
                stroke: col, "stroke-width": q.counted ? 3.5 : 1.5, "stroke-dasharray": q.counted ? "" : "4 3", "vector-effect": "non-scaling-stroke" });
            var t = svgEl("title", {});
            t.textContent = "personne " + Math.round((q.conf || 0) * 100) + " % - " + ps.label + (q.angle != null ? " (corps a " + q.angle + " degres de la verticale)" : "") +
                (q.counted && q.since ? " - au sol depuis " + fmtHM(q.since) : "") + (q.posture === "allonge" && !q.counted ? " - non compte (peu sur ou hors zone)" : "");
            r.appendChild(t);
            svg.appendChild(r);
        });
        shot.addEventListener("click", function () { openViewer(sample, zones); });
        w.appendChild(shot);
        return w;
    }
    function rescueLegend() {
        var l = el("div", "ia-legend");
        l.appendChild(el("b", "ia-legend-grp", "Posture :"));
        [["#dc2626", "au sol (compte)"], ["#f59e0b", "penche, ou au sol peu sur / hors zone"], ["#16a34a", "debout ou assis"], ["#94a3b8", "incertaine"]].forEach(function (x) {
            var s = el("span"); var i = el("i"); i.style.setProperty("--c", x[0]);
            s.appendChild(i); s.appendChild(document.createTextNode(x[1])); l.appendChild(s);
        });
        return l;
    }
    // Resume d'une mesure secours
    function rescueText(s) {
        var n = s.total || 0;
        if (!s.lying) return n + " personne(s) vue(s), aucune au sol";
        return n + " personne(s) vue(s), " + s.lying + " au sol" +
            (s.state === "immobile" ? " : toujours au meme endroit depuis " + (s.lying_min || 0) + " min" : " (1re photo : a confirmer sur la suivante)");
    }
    function sampleView(sample, zones) { return isRescue(sample) ? rescueView(sample, zones) : isCrowd(sample) ? crowdView(sample, zones) : pairView(sample, zones); }
    // Texte d'une zone, quel que soit son type
    function zoneText(z) {
        if (z.kind === "stationnement")
            return (z.parked || []).length ? (z.parked || []).map(function (p) { return "arrete depuis " + p.minutes + " min"; }).join(", ") : "aucun vehicule a l'arret";
        if (z.kind === "foule")
            return "~" + Math.round(z.count || 0) + " personne(s)" + (z.surface_m2 ? " sur " + Math.round(z.surface_m2) + " m2 (" +
                (z.density || 0).toFixed(1) + " p/m2) - " + (STATE_LABEL[z.state] || "--").toLowerCase() +
                (z.cap_trop ? ", trop de monde a partir de " + z.cap_trop : "") : " (surface inconnue : niveau non calcule)");
        return (STATE_LABEL[z.state] || "--").toLowerCase() + ", " + z.n + " vehicule(s), " + z.arretes + " arrete(s)";
    }
    // Agrandissement d'une mesure (plein ecran)
    function openViewer(sample, zones, onChange) {
        var ov = el("div", "dt-annot");
        var panel = el("div", "dt-annot-panel ia-viewer");
        var head = el("div", "dt-annot-head");
        head.appendChild(el("b", "", sample.camera_name + " - " + fmtDay(sample.at) + " " + fmtTime(sample.at)));
        if (isRescue(sample)) {
            head.appendChild(el("span", "", rescueText(sample) + ". Cadre rouge = au sol, orange = penche, vert = debout ou assis ; survoler un cadre pour le detail."));
        } else if (isCrowd(sample)) {
            head.appendChild(el("span", "", "~" + Math.round(sample.total || 0) + " personne(s) estimee(s) sur l'image (carte de densite en rouge)" +
                (sample.persons_yolo != null ? ", " + sample.persons_yolo + " vue(s) individuellement par le detecteur" : "") + "."));
        } else if (sample.method === "flux") {
            var ps = sample.pair_s || [];
            head.appendChild(el("span", "", sample.n + " vehicule(s) sur la 1re paire : " + sample.arretes + " arrete(s), " +
                sample.deplaces + " lent(s), " + (sample.brefs || 0) + " arret(s) bref(s), " + (sample.partis - (sample.brefs || 0)) +
                " roulant(s). Paires de " + (ps[0] || 1) + " s et " + (ps[1] || 1) + " s, a " + Math.round(sample.gap_s || 0) + " s d'intervalle" +
                (sample.source === "photos" ? " (photos enchainees : pas de flux video)." : " (flux video).")));
        } else {
            head.appendChild(el("span", "", sample.n + " vehicule(s) sur A : " + sample.arretes + " arrete(s), " +
                sample.deplaces + " avancant lentement, " + sample.partis + " parti(s) en " + Math.round(sample.gap_s || 0) + " s."));
        }
        panel.appendChild(head);
        // Contenu qui defile, pied (Fermer) toujours visible
        var body = el("div", "ia-viewer-body");
        panel.appendChild(body);
        body.appendChild(sampleView(sample, zones));
        if (!isCrowd(sample) && sample.method === "flux" && sample.photos.length >= 4) {
            // 2e image de chaque paire : chaque vehicule a la couleur de son
            // jumeau de la 1re image (gris : relie a aucun)
            var p2 = el("div", "ia-pair");
            p2.appendChild(shotView(sample.photos[1].photo, sample.vehicles_a2, zones, "1re paire +" + ((sample.pair_s || [1])[0]) + " s"));
            p2.appendChild(shotView(sample.photos[3].photo, sample.vehicles_b2, zones, "2e paire +" + ((sample.pair_s || [1, 1])[1]) + " s"));
            body.appendChild(p2);
        }
        if (!isSingle(sample)) body.appendChild(legend(sample));
        if ((zones || []).some(function (z) { return z.kind === "itineraire"; }))
            body.appendChild(el("div", "ia-meta", "Pastille au centre d'un vehicule : le trace (meme couleur) auquel l'IA le rattache, selon sa position et son sens. Sans pastille : sur aucun trace."));
        // Trafic : le vrai niveau de chaque trace / zone, la photo en grand
        // sous les yeux (pastille sur chaque voiture = trace auquel l'IA la
        // rattache)
        var votes = el("div", "ia-viewer-votes");
        (sample.zones || []).forEach(function (z) {
            if (!isCrowd(sample) && z.id !== "image" && (z.kind === "itineraire" || z.kind === "trafic"))
                votes.appendChild(zoneVote(sample, z, zones, { named: true, onDone: onChange }));
            else body.appendChild(el("div", "ia-meta", z.name + " : " + zoneText(z)));
        });
        if (votes.children.length) body.appendChild(votes);
        var foot = el("div", "dt-annot-foot");
        foot.appendChild(el("span", "dt-annot-sp"));
        var close = el("button", "dt-btn", "Fermer"); close.type = "button";
        foot.appendChild(close);
        panel.appendChild(foot);
        ov.appendChild(panel);
        function shut() { document.removeEventListener("keydown", onKey, true); ov.remove(); }
        function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); shut(); } }
        close.addEventListener("click", shut);
        ov.addEventListener("click", function (e) { if (e.target === ov) shut(); });
        document.addEventListener("keydown", onKey, true);
        // Dans le viewer, un clic sur une photo ne rouvre pas le viewer
        Array.prototype.forEach.call(panel.querySelectorAll(".ia-shot"), function (s) { s.style.cursor = "default"; s.replaceWith(s.cloneNode(true)); });
        document.body.appendChild(ov);
    }

    /* ---------------- avis sur l'etat (trafic) ---------------- */
    function stateVote(sample, onDone) {
        var box = el("div", "ia-sample-vote");
        var v = sample.vote || {};
        box.appendChild(el("span", "", isRescue(sample) ? (sample.lying ? (STATE_LABEL[sample.state] || "--") + " : juste ?" : "Personne au sol : aucune, juste ?")
            : (isCrowd(sample) ? "Niveau " : "Etat ") + (STATE_LABEL[sample.state] || "--").toLowerCase() + " : juste ?"));
        var row = el("div", "ia-gal-thumbs");
        [["up", "thumb_up", "Oui"], ["down", "thumb_down", "Non"]].forEach(function (o) {
            var b = el("button", "dt-thumb-btn is-" + o[0] + (v.vote === o[0] ? " is-on" : "")); b.type = "button";
            b.appendChild(icon(o[1])); b.appendChild(el("span", "", o[2]));
            b.addEventListener("click", function () {
                if (o[0] === "up") return submit(v.vote === "up" ? null : "up", null);
                states.hidden = false;  // "Non" : donner le vrai etat
            });
            row.appendChild(b);
        });
        box.appendChild(row);
        // "Non" : le vrai etat, en boutons courts de meme largeur
        var states = el("div", "ia-truth");
        states.hidden = v.vote !== "down";
        states.appendChild(el("span", "ia-truth-lbl", isCrowd(sample) ? "Le vrai niveau :" : isRescue(sample) ? "En vrai :" : "Le vrai etat :"));
        var srow = el("div", "ia-gal-states");
        (isCrowd(sample) ? CROWD_STATES : isRescue(sample) ? RESCUE_STATES : TRAFFIC_STATES).forEach(function (s) {
            if (s === sample.state) return;
            var b = el("button", "s-" + s + (v.vote === "down" && v.state === s ? " is-on" : ""), STATE_LABEL[s]);
            b.type = "button";
            b.addEventListener("click", function () { submit("down", s); });
            srow.appendChild(b);
        });
        states.appendChild(srow);
        box.appendChild(states);
        function submit(vote, state) {
            send("POST", API.samples + "/" + sample.id + "/feedback", { vote: vote, state: state }).then(function (r) {
                sample.vote = r.vote;
                toast("success", vote ? "Avis enregistre, merci" : "Avis retire");
                var nb = stateVote(sample, onDone);
                box.replaceWith(nb);
                if (onDone) onDone(sample);
            }).catch(function (e) { toast("error", e.message); });
        }
        return box;
    }

    /* ---------------- Trafic ---------------- */
    function loadTrafic() {
        return getJSON(API.cams).then(function (d) {
            M.data = d;
            renderTrafic();
        }).catch(function (e) {
            ["ia-trafic-list", "ia-foule-list", "ia-secours-list"].forEach(function (id) {
                var l = $(id); l.textContent = "";
                l.appendChild(el("div", "ia-empty", "Mesures indisponibles (" + e.message + ")."));
            });
        });
    }
    // Trois listes separees : Trafic (paires), Foule et Secours (une photo), memes donnees
    function renderTrafic() {
        var all = (M.data && M.data.cameras) || [];
        renderList($("ia-trafic-list"), all.filter(function (c) { return !isSingle(c); }),
                   "Aucune camera en analyse du trafic. \"Ajouter une camera\" pour commencer (giratoire, route...).");
        renderList($("ia-foule-list"), all.filter(isCrowd),
                   "Aucune camera en analyse de foule. \"Ajouter une camera\" pour commencer (passerelle, place, hall...).");
        renderList($("ia-secours-list"), all.filter(isRescue),
                   "Aucune camera en surveillance secours. \"Ajouter une camera\" pour commencer (parking, passerelle, file d'entree...).");
        renderSwitch("traffic", $("ia-trafic-switch"), $("ia-trafic-list"));
        renderSwitch("crowd", $("ia-foule-switch"), $("ia-foule-list"));
        renderSwitch("secours", $("ia-secours-switch"), $("ia-secours-list"));
    }
    // Interrupteur general de la fonction : a l'arret, la tache planifiee ne
    // prend plus aucune photo (mesures, alertes) ; en marche, elle reprend
    function renderSwitch(purpose, btn, list) {
        var sw = M.data && M.data.switch && M.data.switch[purpose];
        if (!btn || !sw) return;
        var name = (PURPOSE_UI[purpose] || PURPOSE_UI.traffic).name;
        btn.hidden = false;
        btn.className = "ia-switch" + (sw.on ? " is-on" : "");
        btn.textContent = "";
        btn.appendChild(icon(sw.on ? "toggle_on" : "toggle_off"));
        btn.appendChild(document.createTextNode(sw.on ? "Captures en marche" : "Captures a l'arret"));
        btn.title = (sw.on ? "Cliquer pour arreter toutes les captures " : "Cliquer pour relancer les captures ") + name.toLowerCase() +
            (sw.by ? " (derniere bascule : " + sw.by + ", " + fmtDay(sw.at) + " " + fmtTime(sw.at) + ")" : "");
        // Cameras 4G : incluses ou exclues (data preservee), en plus du on / off
        var cb = $(btn.id + "-cell");
        if (!cb) {
            cb = el("button", "ia-switch ia-switch-cell"); cb.type = "button"; cb.id = btn.id + "-cell";
            btn.parentNode.insertBefore(cb, btn.nextSibling);
        }
        var nCell = ((M.data && M.data.cameras) || []).filter(function (c) {
            return c.cellular && (purposeOf(c) === purpose || (purpose === "traffic" && !isSingle(c)));
        }).length;
        cb.hidden = !nCell;
        cb.className = "ia-switch ia-switch-cell" + (sw.cell ? " is-on" : "");
        cb.textContent = "";
        cb.appendChild(icon(sw.cell ? "signal_cellular_alt" : "signal_cellular_off"));
        cb.appendChild(document.createTextNode(sw.cell ? "4G incluses" : "4G exclues"));
        cb.title = sw.cell ? "Les " + nCell + " camera(s) 4G sont mesurees. Cliquer pour ne mesurer que les cameras filaires (data 4G preservee)"
                           : "Seules les cameras filaires sont mesurees. Cliquer pour reprendre les " + nCell + " camera(s) 4G";
        cb.onclick = function () {
            cb.disabled = true;
            send("PUT", API.captureSwitch, { purpose: purpose, cell: !sw.cell }).then(function (r) {
                M.data.switch = r.switch;
                renderTrafic();
                toast("success", name + " : " + (r.switch[purpose].cell ? "cameras 4G reprises (prochaine photo dans la minute)" : "cameras 4G exclues, seules les filaires sont mesurees"));
            }).catch(function (e) { toast("error", e.message); }).then(function () { cb.disabled = false; });
        };
        if (sw.on && !sw.cell && nCell) {
            var b4 = el("div", "ia-switch-off is-cell");
            b4.appendChild(icon("signal_cellular_off"));
            b4.appendChild(el("span", "", nCell + " camera(s) 4G en pause (data preservee) : seules les cameras filaires sont mesurees."));
            list.insertBefore(b4, list.firstChild);
        }
        if (!sw.on) {
            var ban = el("div", "ia-switch-off");
            ban.appendChild(icon("pause_circle"));
            ban.appendChild(el("span", "", "Captures " + name.toLowerCase() + " a l'arret" + (sw.by ? " depuis le " + fmtDay(sw.at) + " " + fmtTime(sw.at) + " (" + sw.by + ")" : "") +
                " : aucune photo prise, ni mesure, ni alerte. Les mesures deja prises restent consultables."));
            list.insertBefore(ban, list.firstChild);
        }
        btn.onclick = function () {
            var go = function () {
                btn.disabled = true;
                send("PUT", API.captureSwitch,{ purpose: purpose, on: !sw.on }).then(function (r) {
                    M.data.switch = r.switch;
                    renderTrafic();
                    toast("success", name + " : captures " + (r.switch[purpose].on ? "relancees (prochaine photo dans la minute)" : "arretees"));
                }).catch(function (e) { toast("error", e.message); }).then(function () { btn.disabled = false; });
            };
            if (sw.on && typeof showConfirmToast === "function")
                showConfirmToast("Arreter toutes les captures " + name.toLowerCase() + " ? Plus aucune photo, mesure ni alerte jusqu'a la remise en marche.",
                                 { okLabel: "Arreter", type: "warning" }).then(function (y) { if (y) go(); });
            else go();
        };
    }
    function renderList(l, cams, empty) {
        l.textContent = "";
        if (!cams.length) {
            l.appendChild(el("div", "ia-empty", empty));
            return;
        }
        cams.forEach(function (c) { l.appendChild(camCard(c)); });
        // Arrivee depuis la carte globale : on amene la camera a l'ecran
        if (M.focusKey) {
            var target = l.querySelector('[data-key="' + M.focusKey + '"]');
            if (target) { M.focusKey = null; target.scrollIntoView({ block: "start", behavior: "smooth" }); }
        }
    }
    function camCard(c) {
        var card = el("article", "ia-cam");
        card.dataset.key = c.key;
        var head = el("div", "ia-cam-head");
        var crowd = isCrowd(c), rescue = isRescue(c);
        head.appendChild(icon((PURPOSE_UI[purposeOf(c)] || PURPOSE_UI.traffic).icon));
        head.appendChild(el("b", "", c.camera_name + (c.channel_name && c.channel_name !== c.camera_name ? " - " + c.channel_name : "")));
        head.appendChild(stateChip(c.state));
        var csw = M.data && M.data.switch && M.data.switch[isSingle(c) ? purposeOf(c) : "traffic"];
        if (c.cellular) head.appendChild(csw && !csw.cell ? chip("is-off", "signal_cellular_off", "4G en pause", "Cameras 4G exclues de cette fonction (data preservee)")
                                                          : chip("", "signal_cellular_alt", "4G"));
        head.appendChild(c.planning_on ? chip("", "schedule", c.groups.length ? "Dans le planning" : "24 h/24")
                                       : chip("is-off", "schedule", "Hors planning", "Pas de mesure en dehors du planning de ses groupes"));
        if (c.alert_rules && c.alert_rules.length)
            head.appendChild(chip("is-alerts", "notifications_active", "Alertes : " + c.alert_rules.join(", "), "Regles de la centrale d'alertes qui surveillent cette camera"));
        else head.appendChild(chip("is-obs", "visibility", "Observation (aucune alerte)", "Aucune regle active de la centrale d'alertes (" +
            (rescue ? "\"Personne au sol\"" : crowd ? "\"Foule dense\"" : "\"Trafic\" ou \"Stationnement interdit\"") + ") ne surveille cette camera"));
        var noZone = !rescue && c.last && (c.last.zones || []).some(function (z) { return z.id === "image"; });
        if (crowd && c.last && !noZone && (c.last.zones || []).some(function (z) { return z.kind === "foule" && !z.surface_m2; }))
            head.appendChild(chip("is-err", "square_foot", "Surface a renseigner", "Sans surface, le niveau (personnes / m2) ne peut pas etre calcule"));
        if (noZone) head.appendChild(crowd
            ? chip("is-err", "pentagon", "Zones foule a dessiner", "Sans zone (avec sa surface), seul le nombre total de personnes est estime : pas de niveau ni d'alerte")
            : chip("is-err", "pentagon", "Zones trafic a dessiner",
                   "Sans zone trafic, toute l'image compte : les vehicules gares passent pour un bouchon, et aucune alerte trafic n'est envoyee"));
        var errNewer = c.last_error && (!c.last || (c.error_at && c.error_at > c.last.at));
        if (errNewer) head.appendChild(chip("is-err", "error", "Echec " + ago(c.error_at), c.last_error));
        head.appendChild(el("span", "header-spacer"));
        head.appendChild(el("span", "ia-meta", (crowd ? "Foule : une photo toutes les " + c.role.interval_min + " min"
            : rescue ? "Secours : une photo toutes les " + c.role.interval_min + " min"
            : "Trafic : deux paires (1 s) toutes les " + c.role.interval_min + " min, a " + c.role.gap_s + " s d'intervalle") +
            (c.role.preset ? ", preset " + c.role.preset : "") + (c.last ? " - derniere " + ago(c.last.at) : "")));
        card.appendChild(head);

        var body = el("div", "ia-cam-body");
        var left = el("div");
        var shown = c.last;
        var zones = [];
        var pairBox = el("div");
        left.appendChild(pairBox);
        if (rescue) left.appendChild(rescueLegend());
        else if (!crowd) left.appendChild(legend(c.last));
        else left.appendChild(el("div", "ia-legend", "Rouge = densite de personnes estimee ; contour des zones a la couleur de leur niveau."));
        body.appendChild(left);
        var side = el("div", "ia-side");
        body.appendChild(side);
        card.appendChild(body);

        function showSample(s) {
            shown = s;
            pairBox.textContent = "";
            pairBox.appendChild(s ? sampleView(s, zones) : (rescue ? rescueView(null) : crowd ? crowdView(null) : pairView(null)));
            zoneBox.textContent = "";
            if (s && rescue) {
                // Secours : personnes vues, au sol, depuis quand
                var rb = el("div", "ia-zone");
                var rh = el("div", "ia-zone-head");
                rh.appendChild(el("span", "", "Personnes au sol"));
                rh.appendChild(el("span", "header-spacer"));
                rh.appendChild(stateChip(s.state));
                rb.appendChild(rh);
                var rn = el("div", "ia-zone-nums");
                [[s.total || 0, "personne(s) vue(s)"], [s.lying || 0, "au sol"]].concat(s.state === "immobile" ? [[s.lying_min || 0, "min au meme endroit"]] : [])
                    .forEach(function (x) {
                        var s4 = el("span"); s4.appendChild(el("b", "", String(x[0]))); s4.appendChild(document.createTextNode(" " + x[1])); rn.appendChild(s4);
                    });
                rb.appendChild(rn);
                rb.appendChild(el("div", "ia-meta", rescueText(s)));
                if (zones.some(function (z) { return z.kind === "secours"; }))
                    rb.appendChild(el("div", "ia-meta", "Seules les personnes dans les zones secours comptent."));
                zoneBox.appendChild(rb);
            }
            ((s && s.zones) || []).forEach(function (z) {
                var zb = el("div", "ia-zone");
                var zh = el("div", "ia-zone-head");
                zh.appendChild(el("span", "", z.name));
                zh.appendChild(el("span", "header-spacer"));
                if (z.kind === "foule") {
                    // Foule : personnes estimees, surface, densite, capacites
                    zh.appendChild(stateChip(z.state));
                    zb.appendChild(zh);
                    var fn = el("div", "ia-zone-nums");
                    [["~" + Math.round(z.count || 0), "personne(s)"],
                     [z.surface_m2 ? Math.round(z.surface_m2) : "?", "m2"],
                     [z.density != null ? z.density.toFixed(1) : "?", "p/m2"]].forEach(function (x) {
                        var s3 = el("span"); s3.appendChild(el("b", "", String(x[0]))); s3.appendChild(document.createTextNode(" " + x[1])); fn.appendChild(s3);
                    });
                    zb.appendChild(fn);
                    if (z.cap_trop) zb.appendChild(el("div", "ia-meta", "Charge a partir de " + z.cap_charge + " personnes, trop de monde a partir de " + z.cap_trop));
                    zoneBox.appendChild(zb);
                    return;
                }
                if (z.kind === "stationnement") {
                    // Stationnement interdit : vehicules arretes et depuis quand
                    zh.appendChild(z.over ? chip("is-err", "local_parking", z.over + " au-dela de " + z.max_min + " min")
                                          : chip("", "local_parking", "Stationnement interdit"));
                    zb.appendChild(zh);
                    var pk = (z.parked || []);
                    zb.appendChild(el("div", "ia-zone-nums", pk.length
                        ? pk.map(function (p) { return "vehicule arrete depuis " + p.minutes + " min" + (p.over ? " (au-dela)" : ""); }).join(" - ")
                        : (z.n ? z.n + " vehicule(s), aucun a l'arret" : "Aucun vehicule")));
                    zoneBox.appendChild(zb);
                    return;
                }
                var zdef = zones.filter(function (x) { return x.id === z.id; })[0];
                if (z.kind === "itineraire" && zdef) {
                    var dot = el("i", "ia-trace-dot"); dot.style.background = zdef.color || "#0891b2";
                    zh.insertBefore(dot, zh.firstChild);
                }
                zh.appendChild(stateChip(z.state));
                zb.appendChild(zh);
                var nums = el("div", "ia-zone-nums");
                (s.method === "flux"
                    ? [[z.n, "vehicule(s)"], [z.arretes, "arrete(s)"], [z.deplaces, "lent(s)"], [z.brefs, "arret(s) bref(s)"], [(z.partis || 0) - (z.brefs || 0), "roulant(s)"]]
                    : [[z.n, "vehicule(s)"], [z.arretes, "arrete(s)"], [z.deplaces, "lent(s)"], [z.partis, "parti(s)"], [z.arrives, "arrive(s)"]]).forEach(function (x) {
                    var s2 = el("span"); s2.appendChild(el("b", "", String(x[0] || 0))); s2.appendChild(document.createTextNode(" " + x[1])); nums.appendChild(s2);
                });
                zb.appendChild(nums);
                if (z.id !== "image") zb.appendChild(zoneVote(s, z, zones));
                zoneBox.appendChild(zb);
            });
            // Waze au moment de la mesure (itineraires lies, signalements proches)
            var wz = s && s.waze;
            if (wz && (Object.keys(wz.routes || {}).length || Object.keys(wz.alerts || {}).length)) {
                var wb = el("div", "ia-zone");
                var wh = el("div", "ia-zone-head");
                wh.appendChild(icon("alt_route")); wh.appendChild(el("span", "", "Waze au meme moment"));
                wb.appendChild(wh);
                var zname = {};
                (s.zones || []).forEach(function (z) { zname[z.id] = z.name; });
                Object.keys(wz.routes || {}).forEach(function (id) {
                    var r = wz.routes[id];
                    wb.appendChild(el("div", "ia-meta", r.name + (r.zone && zname[r.zone] ? " (zone " + zname[r.zone] + ")" : "") + " : " + (r.ratio != null ? "x" + r.ratio.toFixed(2) + " du temps habituel (" +
                        r.time + " s au lieu de " + r.historic + " s)" : "temps inconnu") + (r.jam ? ", bouchon niveau " + r.jam + "/4" : "")));
                    if (r.sub && r.sub.ratio != null) wb.appendChild(el("div", "ia-meta", "  troncon devant la camera (" + (r.sub.from || "?") + " -> " +
                        (r.sub.to || "?") + ") : x" + r.sub.ratio.toFixed(2) + " (" + r.sub.time + " s au lieu de " + r.sub.historic + " s)"));
                });
                var al = Object.keys(wz.alerts || {});
                if (al.length) wb.appendChild(el("div", "ia-meta", "Signalements a moins de 400 m : " +
                    al.map(function (k) { return (WAZE_ALERT[k] || k.toLowerCase()) + " x" + wz.alerts[k]; }).join(", ")));
                zoneBox.appendChild(wb);
            }
            voteBox.textContent = "";
            if (s) {
                voteBox.appendChild(el("div", "ia-meta", "Mesure du " + fmtDay(s.at) + " " + fmtTime(s.at)));
                voteBox.appendChild(stateVote(s));
            }
        }
        var zoneBox = el("div", "ia-timeline");
        var tl = el("div", "ia-timeline");
        var voteBox = el("div", "ia-timeline");
        side.appendChild(zoneBox);
        side.appendChild(tl);
        side.appendChild(voteBox);

        var acts = el("div", "ia-cam-actions");
        [["photo_camera", "Mesurer maintenant", function () {
            send("POST", API.cams + "/" + c.camera_id + "/" + c.channel + "/now").then(function () {
                toast("success", "Mesure demandee : elle sera prise par la tache planifiee dans la minute");
            }).catch(function (e) { toast("error", e.message); });
        }], ["tune", "Reglages et planning", function () { openSettings(c); }],
           ["pentagon", "Zones de mesure", function () { openZones(c, shown); }]]
            .concat(crowd || rescue ? [] : [["alt_route", "Itineraires Waze", function () { openWaze(c); }]]).forEach(function (a) {
            var b = el("button", "dt-btn"); b.type = "button";
            b.appendChild(icon(a[0])); b.appendChild(document.createTextNode(a[1]));
            b.addEventListener("click", a[2]);
            acts.appendChild(b);
        });
        side.appendChild(acts);

        // Zones, puis historique
        getJSON(API.zones + "?key=" + encodeURIComponent(c.key)).then(function (z) {
            zones = z.zones || [];
            showSample(shown);
        }).catch(function () { showSample(shown); });
        getJSON(API.samples + "?key=" + encodeURIComponent(c.key) + "&hours=" + M.hours).then(function (d) {
            renderTimeline(tl, d.items || [], function (it) {
                getJSON(API.samples + "/" + it.id).then(showSample).catch(function (e) { toast("error", e.message); });
            });
        }).catch(function () {});
        return card;
    }
    // Avis de l'operateur sur UNE zone / UN itineraire trace d'une mesure :
    // le vrai niveau. Contour = niveau calcule par l'IA, plein = avis donne.
    // Chaque avis apprend a l'IA le nombre de vehicules de chaque niveau
    // sur ce trace (seuils affiches dessous).
    function levelsText(lv) {
        if (!lv || !lv.votes) return "Pas encore d'avis sur ce trace : donnez le vrai niveau, l'IA apprend le nombre de vehicules de chaque niveau.";
        var parts = [];
        ["dense", "ralenti", "bouchon"].forEach(function (k) { if (lv[k] != null) parts.push(STATE_LABEL[k].toLowerCase() + " des " + lv[k] + " vehicules"); });
        return "Appris sur " + lv.votes + " avis" + (parts.length ? " : " + parts.join(", ") : " (il en faut au moins 2 de chaque cote d'un niveau)") +
            (lv.votes ? " - " + (lv.agree || 0) + "/" + lv.votes + " retrouves" : "");
    }
    // opts.named : nom du trace en tete (galerie, agrandissement) ;
    // opts.onDone(s) apres chaque avis
    function zoneVote(s, z, zones, opts) {
        opts = opts || {};
        var box = el("div", "ia-truth");
        var my = ((s.zone_votes || {})[z.id] || {}).state || null;
        var zd = (zones || []).filter(function (x) { return x.id === z.id; })[0];
        var lbl = el("span", "ia-truth-lbl");
        if (opts.named) {
            if (z.kind === "itineraire") {
                var dot = el("i", "ia-trace-dot"); dot.style.background = (zd && zd.color) || "#0891b2";
                lbl.appendChild(dot);
            }
            lbl.appendChild(el("b", "", z.name + " "));
        }
        lbl.appendChild(document.createTextNode("Vrai niveau" + (z.veh != null ? " (" + z.veh + " vehicule(s) par image)" : "") + " :"));
        box.appendChild(lbl);
        var row = el("div", "ia-gal-states");
        TRAFFIC_STATES.forEach(function (st) {
            var b = el("button", "s-" + st + (my === st ? " is-on" : "") + (z.state === st ? " is-ai" : ""), STATE_LABEL[st]);
            b.type = "button";
            b.title = (z.state === st ? "Niveau calcule par l'IA. " : "") + (my === st ? "Votre avis (cliquer pour l'annuler)" : "C'est " + STATE_LABEL[st].toLowerCase());
            b.addEventListener("click", function () {
                Array.prototype.forEach.call(row.children, function (x) { x.disabled = true; });
                send("POST", API.samples + "/" + s.id + "/zone-vote", { zone: z.id, state: my === st ? null : st }).then(function (r) {
                    s.zone_votes = s.zone_votes || {};
                    if (my === st) delete s.zone_votes[z.id]; else s.zone_votes[z.id] = { state: st };
                    zones.forEach(function (zz) { if (zz.id === z.id) zz.levels = r.levels; });
                    var nb = zoneVote(s, z, zones, opts);
                    box.replaceWith(nb);
                    toast("success", "Merci : niveau appris pour " + z.name);
                    if (opts.onDone) opts.onDone(s);
                }).catch(function (e) {
                    toast("error", e.message);
                    Array.prototype.forEach.call(row.children, function (x) { x.disabled = false; });
                });
            });
            row.appendChild(b);
        });
        box.appendChild(row);
        box.appendChild(el("div", "ia-levels", levelsText(zd && zd.levels)));
        return box;
    }
    function renderTimeline(box, items, onPick) {
        box.textContent = "";
        var lbl = el("div", "ia-timeline-lbl");
        lbl.appendChild(el("span", "", items.length ? fmtDay(items[0].at) + " " + fmtHM(items[0].at) : ""));
        lbl.appendChild(el("span", "", items.length + " mesure(s) sur " + (M.hours >= 48 ? Math.round(M.hours / 24) + " j" : M.hours + " h")));
        lbl.appendChild(el("span", "", items.length ? fmtHM(items[items.length - 1].at) : ""));
        box.appendChild(lbl);
        var strip = el("div", "ia-strip");
        var crowd = items.length && isCrowd(items[0]);
        var rescue = items.length && isRescue(items[0]);
        function val(x) { return crowd || rescue ? Math.round(x.total || 0) : x.n; }
        items.forEach(function (it) {
            var s = el("span", "s-" + (it.state || "inconnu"));
            s.title = fmtDay(it.at) + " " + fmtHM(it.at) + " - " + (STATE_LABEL[it.state] || "--") + " - " +
                (rescue ? val(it) + " personne(s), " + (it.lying || 0) + " au sol" : crowd ? "~" + val(it) + " personne(s)" : it.n + " vehicule(s), " + it.arretes + " arrete(s)");
            s.addEventListener("click", function () { onPick(it); });
            strip.appendChild(s);
        });
        box.appendChild(strip);
        // Nombre de vehicules / de personnes (courbe)
        if (items.length > 1) {
            var max = Math.max.apply(null, items.map(val).concat([1]));
            var w = 300, h = 46;
            var pts = items.map(function (x, i) { return [i / (items.length - 1) * w, h - 3 - (val(x) / max) * (h - 8)]; });
            var svg = svgEl("svg", { viewBox: "0 0 " + w + " " + h, preserveAspectRatio: "none", "class": "ia-spark" });
            var d = "M" + pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" L");
            svg.appendChild(svgEl("path", { d: d + " L" + w + "," + h + " L0," + h + " Z", "class": "area" }));
            svg.appendChild(svgEl("path", { d: d, "vector-effect": "non-scaling-stroke" }));
            box.appendChild(svg);
            box.appendChild(el("div", "ia-timeline-lbl", (crowd ? "Personnes estimees" : rescue ? "Personnes vues" : "Vehicules vus") + " (max " + max + ")"));
        }
    }

    /* ---------------- reglages d'une camera ---------------- */
    function dialog(title) {
        var d = el("dialog", "ia-dialog");
        var head = el("div", "ia-dialog-head");
        head.appendChild(el("h3", "", title));
        var x = el("button", "dt-icon-btn"); x.type = "button"; x.appendChild(icon("close"));
        x.addEventListener("click", function () { d.close(); });
        head.appendChild(x);
        d.appendChild(head);
        d.body = el("div", "ia-dialog-body");
        d.appendChild(d.body);
        d.foot = el("div", "ia-dialog-foot");
        d.appendChild(d.foot);
        d.addEventListener("close", function () { d.remove(); });
        document.body.appendChild(d);
        return d;
    }
    function selectField(label, options, value, help) {
        var f = el("label", "ia-field", label);
        var s = document.createElement("select");
        options.forEach(function (o) { s.appendChild(new Option(o[1], o[0])); });
        s.value = value;
        f.appendChild(s);
        if (help) f.appendChild(el("small", "", help));
        f.input = s;
        return f;
    }
    function openSettings(c) {
        var params = {};
        (M.data.params || []).forEach(function (p) { params[p.key] = p; });
        var d = dialog("Reglages - " + c.camera_name);
        var role = JSON.parse(JSON.stringify(c.role));
        var fpu = selectField("Analyse", params.purpose.options, role.purpose || "traffic",
            "Trafic : une paire de photos (vehicules arretes / qui roulent). Foule : une photo, personnes estimees par zone. Secours : une photo, personnes au sol.");
        d.body.appendChild(fpu);
        var row = el("div", "ia-row");
        var fi = selectField("Une mesure toutes les", params.interval_min.options, role.interval_min,
            c.cellular ? "Camera 4G : chaque photo pleine taille pese ~1 Mo" : "");
        var fg = selectField("Intervalle entre les 2 paires (trafic)", params.gap_s.options, role.gap_s,
            "Assez long pour qu'un vehicule qui roule au pas ait bouge");
        row.appendChild(fi); row.appendChild(fg);
        d.body.appendChild(row);
        function syncPurpose() { fg.hidden = fpu.input.value === "crowd" || fpu.input.value === "secours"; }
        fpu.input.addEventListener("change", syncPurpose);
        syncPurpose();

        // Preset a rappeler avant la mesure (facultatif)
        var fp = el("div", "ia-field", "Preset a rappeler avant chaque mesure");
        var ps = document.createElement("select");
        ps.appendChild(new Option("Aucun : ne pas bouger la camera", ""));
        if (role.preset) ps.appendChild(new Option("Preset " + role.preset, role.preset));
        ps.value = role.preset || "";
        var prow = el("div", "ia-row");
        prow.appendChild(ps);
        var load = el("button", "dt-btn"); load.type = "button"; load.style.flex = "0 0 auto";
        load.appendChild(icon("sync")); load.appendChild(document.createTextNode("Lire les presets"));
        load.addEventListener("click", function () {
            load.disabled = true;
            getJSON(API.cams + "/" + c.camera_id + "/" + c.channel + "/presets").then(function (r) {
                var cur = ps.value;
                while (ps.options.length > 1) ps.remove(1);
                (r.presets || []).forEach(function (p) { ps.appendChild(new Option(p.id + " - " + (p.name || "Preset " + p.id), String(p.id))); });
                ps.value = cur;
                if (!r.presets || !r.presets.length) toast("info", "Aucun preset enregistre sur cette camera");
            }).catch(function (e) { toast("error", e.message); }).then(function () { load.disabled = false; });
        });
        prow.appendChild(load);
        fp.appendChild(prow);
        fp.appendChild(el("small", "", "Si un preset est choisi, la camera y est ramenee a CHAQUE mesure (les operateurs la voient bouger). Sinon on mesure la vue du moment."));
        d.body.appendChild(fp);

        d.body.appendChild(el("small", "ia-meta", "Alertes : regles \"Trafic - ralenti / bouchon\", \"Stationnement interdit\" ou \"Foule dense\" de la centrale d'alertes (comme les intrusions)."));

        // Planning : groupes de la page (memes semaines types que les intrusions)
        var gsec = el("div", "ia-field", "Planning (groupes)");
        gsec.appendChild(el("small", "", "Sans groupe : mesure 24 h/24. Dans un ou plusieurs groupes : mesure seulement quand un de ses groupes est actif."));
        var glist = el("div", "ia-groups");
        gsec.appendChild(glist);
        function renderGroups() {
            glist.textContent = "";
            var cfg = window.IADetection ? window.IADetection.config() : { groups: [] };
            (cfg.groups || []).forEach(function (g) {
                var row2 = el("label", "ia-group");
                var gcb = document.createElement("input"); gcb.type = "checkbox";
                gcb.checked = (g.members || []).some(function (m) { return m.camera_id === c.camera_id && m.channel === c.channel; });
                gcb.addEventListener("change", function () {
                    send("POST", API.groups + "/" + g._id + "/member", { camera_id: c.camera_id, channel: c.channel, member: gcb.checked })
                        .then(function () { return window.IADetection.reloadConfig(); })
                        .then(function () { renderGroups(); loadTrafic(); })
                        .catch(function (e) { toast("error", e.message); gcb.checked = !gcb.checked; });
                });
                row2.appendChild(gcb);
                var dot = el("i"); dot.style.background = g.color; row2.appendChild(dot);
                row2.appendChild(el("span", "", g.name));
                row2.appendChild(el("span", "header-spacer"));
                row2.appendChild(el("span", "ia-meta", g.state_label || ""));
                var ed = el("button", "dt-link-btn", "Planning"); ed.type = "button";
                ed.addEventListener("click", function (e) { e.preventDefault(); window.IADetection.openGroup(g._id); });
                row2.appendChild(ed);
                glist.appendChild(row2);
            });
            var add = el("button", "dt-btn"); add.type = "button";
            add.appendChild(icon("add")); add.appendChild(document.createTextNode("Nouveau planning pour cette camera"));
            add.addEventListener("click", function () {
                window.IADetection.openGroup(null, [{ camera_id: c.camera_id, channel: c.channel }]);
            });
            glist.appendChild(add);
        }
        renderGroups();
        document.addEventListener("ia:groups-changed", function onG() {
            if (!d.isConnected) { document.removeEventListener("ia:groups-changed", onG); return; }
            renderGroups(); loadTrafic();
        });
        d.body.appendChild(gsec);

        var del = el("button", "dt-btn dt-btn-danger"); del.type = "button";
        del.appendChild(icon("delete")); del.appendChild(document.createTextNode("Retirer"));
        del.addEventListener("click", function () {
            var ask = typeof showConfirmToast === "function"
                ? showConfirmToast((isCrowd(c) ? "Ne plus mesurer la foule sur " : isRescue(c) ? "Ne plus surveiller les personnes au sol sur " : "Ne plus mesurer le trafic sur ") + c.camera_name + " ? L'historique est garde.", { okLabel: "Retirer", type: "warning" })
                : Promise.resolve(true);
            ask.then(function (ok) {
                if (!ok) return;
                send("DELETE", API.cams + "/" + c.camera_id + "/" + c.channel).then(function () {
                    toast("success", "Camera retiree de la capture"); d.close(); loadTrafic();
                }).catch(function (e) { toast("error", e.message); });
            });
        });
        d.foot.appendChild(del);
        d.foot.appendChild(el("span", "header-spacer"));
        var cancel = el("button", "dt-btn", "Annuler"); cancel.type = "button";
        cancel.addEventListener("click", function () { d.close(); });
        var save = el("button", "dt-btn dt-btn-primary", "Enregistrer"); save.type = "button";
        save.addEventListener("click", function () {
            send("PUT", API.cams + "/" + c.camera_id + "/" + c.channel, {
                purpose: fpu.input.value, interval_min: fi.input.value, gap_s: fg.input.value, preset: ps.value,
            }).then(function () { toast("success", "Reglages enregistres"); d.close(); loadTrafic(); })
              .catch(function (e) { toast("error", e.message); });
        });
        d.foot.appendChild(cancel); d.foot.appendChild(save);
        d.showModal();
    }

    /* ---------------- itineraires Waze lies a une camera ---------------- */
    function openWaze(c) {
        var d = dialog("Itineraires Waze - " + c.camera_name);
        d.body.appendChild(el("div", "ia-empty", "Chargement..."));
        d.showModal();
        var url = API.cams + "/" + c.camera_id + "/" + c.channel + "/waze";
        getJSON(url).then(function (r) {
            d.body.textContent = "";
            d.body.appendChild(el("small", "ia-meta", "Cochez les itineraires Waze que cette camera voit (plusieurs possibles). " +
                "Leur temps de parcours et les signalements proches sont enregistres avec chaque mesure, pour comparer Waze et la camera." +
                (r.tag ? " Vous pouvez aussi ajouter " + r.tag + " (ou " + r.tag_channel + " pour ce canal seulement) dans le nom d'un itineraire sur Waze : il est alors lie tout seul." : "")));
            if (!r.positioned) d.body.appendChild(el("small", "ia-meta", "Camera sans position sur la carte : pas de distance ni de signalements proches."));
            var q = document.createElement("input"); q.type = "text"; q.placeholder = "Rechercher un itineraire";
            var fq = el("label", "ia-field", ""); fq.appendChild(q);
            d.body.appendChild(fq);
            var list = el("div", "ia-groups");
            d.body.appendChild(list);
            var boxes = [];
            d.body.insertBefore(el("small", "ia-meta", "Pour comparer voie par voie, tracez plutot chaque itineraire sur la photo (\"Zones de mesure\", type \"Itineraire trace\") : " +
                "chaque vehicule est rattache au trace qu'il suit, dans le bon sens."), fq);
            (r.routes || []).forEach(function (x) {
                var row = el("label", "ia-group");
                var cb = document.createElement("input"); cb.type = "checkbox";
                cb.checked = x.chosen || x.tagged; cb.disabled = x.tagged; cb.value = x.id;
                row.appendChild(cb);
                var txt = el("span", "", x.name);
                row.appendChild(txt);
                row.appendChild(el("span", "header-spacer"));
                row.appendChild(el("span", "ia-meta", [
                    x.tagged ? "lie par son nom" : "",
                    x.distance_m != null ? (x.distance_m < 1000 ? x.distance_m + " m" : (x.distance_m / 1000).toFixed(1) + " km") : "",
                    x.ratio != null ? "x" + x.ratio.toFixed(2) : "",
                ].filter(Boolean).join(" - ")));
                row.title = (x.from || "?") + " -> " + (x.to || "?") + (x.length ? ", " + x.length + " m" : "");
                row.dataset.q = (x.name + " " + x.from + " " + x.to).toLowerCase();
                list.appendChild(row);
                boxes.push(cb);
            });
            if (!(r.routes || []).length) list.appendChild(el("div", "ia-empty", "Aucun itineraire dans le dernier releve Waze"));
            q.addEventListener("input", function () {
                var t = q.value.trim().toLowerCase();
                Array.prototype.forEach.call(list.children, function (row) { row.hidden = !!t && (row.dataset.q || "").indexOf(t) < 0; });
            });
            var cancel = el("button", "dt-btn", "Annuler"); cancel.type = "button";
            cancel.addEventListener("click", function () { d.close(); });
            var save = el("button", "dt-btn dt-btn-primary", "Enregistrer"); save.type = "button";
            save.addEventListener("click", function () {
                var ids = boxes.filter(function (b) { return b.checked && !b.disabled; }).map(function (b) { return b.value; });
                send("PUT", url, { routes: ids }).then(function () {
                    toast("success", "Itineraires enregistres : les statistiques sont refaites dans la minute");
                    d.close();
                }).catch(function (e) { toast("error", e.message); });
            });
            d.foot.appendChild(el("span", "header-spacer"));
            d.foot.appendChild(cancel); d.foot.appendChild(save);
        }).catch(function (e) { d.close(); toast("error", e.message); });
    }

    function openAdd(purpose) {
        if (!M.data) return;
        var d = dialog(purpose === "crowd" ? "Ajouter une camera en analyse de foule" : purpose === "secours" ? "Ajouter une camera en surveillance secours" : "Ajouter une camera en analyse du trafic");
        var cands = M.data.candidates || [];
        var q = document.createElement("input"); q.type = "text"; q.placeholder = "Rechercher (nom, numero)";
        var fq = el("label", "ia-field", "Camera"); fq.appendChild(q);
        var sel = document.createElement("select"); sel.size = 8; sel.style.height = "auto";
        fq.appendChild(sel);
        d.body.appendChild(fq);
        var fch = el("label", "ia-field", "Canal");
        var sch = document.createElement("select"); fch.appendChild(sch);
        d.body.appendChild(fch);
        var fpu = selectField("Analyse", [["traffic", "Trafic (bouchons, stationnement)"], ["crowd", "Foule (trop de monde)"], ["secours", "Secours (personne au sol)"]], purpose || "traffic");
        d.body.appendChild(fpu);
        d.body.appendChild(el("small", "ia-meta", "Reglages par defaut : trafic = une paire toutes les 3 min, 10 s d'ecart ; foule et secours = une photo toutes les 5 min. Sans preset, sans alerte (regles dans la centrale). Cameras de piste exclues."));
        function fill() {
            var t = q.value.trim().toLowerCase();
            sel.textContent = "";
            cands.filter(function (x) { return x.channels.length && (!t || x.name.toLowerCase().indexOf(t) >= 0); }).slice(0, 200).forEach(function (x) {
                sel.appendChild(new Option(x.name + (x.cellular ? " (4G)" : ""), x.camera_id));
            });
            fillCh();
        }
        function fillCh() {
            var x = cands.filter(function (y) { return y.camera_id === sel.value; })[0];
            sch.textContent = "";
            ((x && x.channels) || []).forEach(function (ch) { sch.appendChild(new Option(ch.no + " - " + (ch.name || "Canal " + ch.no), ch.no)); });
        }
        q.addEventListener("input", fill);
        sel.addEventListener("change", fillCh);
        fill();
        var cancel = el("button", "dt-btn", "Annuler"); cancel.type = "button";
        cancel.addEventListener("click", function () { d.close(); });
        var ok = el("button", "dt-btn dt-btn-primary", "Ajouter"); ok.type = "button";
        ok.addEventListener("click", function () {
            if (!sel.value || !sch.value) { toast("warning", "Choisissez une camera et un canal"); return; }
            send("POST", API.cams, { camera_id: sel.value, channel: Number(sch.value), purpose: fpu.input.value }).then(function () {
                toast("success", "Camera ajoutee : premiere mesure dans les minutes qui viennent");
                d.close(); loadTrafic();
            }).catch(function (e) { toast("error", e.message); });
        });
        d.foot.appendChild(cancel); d.foot.appendChild(ok);
        d.showModal();
        q.focus();
    }

    /* ---------------- zones de mesure (polygones sur la photo) ---------------- */
    function openZones(c, sample) {
        if (!sample || !sample.photos) { toast("info", "Il faut au moins une mesure (photo) pour dessiner les zones"); return; }
        var crowd = isCrowd(c), rescue = isRescue(c);
        var d = dialog("Zones de mesure - " + c.camera_name);
        d.style.width = "min(1100px, calc(100vw - 24px))";
        d.body.appendChild(el("small", "ia-meta", rescue
            ? "Zones \"Secours\" (facultatives) : les endroits ou une personne au sol n'est pas normale (voie, parking, passerelle, file d'entree). Avec au moins une zone, seules les personnes dedans comptent ; sans zone, toute l'image compte (attention aux pelouses et campings ou l'on s'allonge)."
            : crowd
            ? "Zones \"Foule\" : la passerelle, la place, le hall... Cliquez les sommets sur la photo, puis donnez la SURFACE reelle de la zone (\"Mesurer sur la carte\" la calcule depuis la vue aerienne, ou tapez-la d'apres un plan). Capacite = surface x seuil : charge a 2 personnes/m2, trop de monde a 4 personnes/m2 (seuils modifiables)."
            : "\"Itineraire trace\" : cliquez sur la photo le DEPART puis des points le long de la voie jusqu'a l'ARRIVEE, dans le sens de circulation, et choisissez l'itineraire Waze correspondant. Chaque vehicule est rattache au trace qu'il suit, dans ce sens (un vehicule en sens inverse n'y compte pas). " +
              "Zones \"Trafic\" : une par entree du giratoire ou par voie, jamais sur un parking (un vehicule gare passerait pour un bouchon). Zones \"Stationnement interdit\" : alerte quand un vehicule y reste plus que la duree choisie (precision : le rythme des mesures)."));
        var stage = el("div", "ia-zedit-stage");
        var im = document.createElement("img"); im.src = sample.photos[0].photo; im.alt = "";
        stage.appendChild(im);
        var svg = svgEl("svg", { viewBox: "0 0 1 1", preserveAspectRatio: "none" });
        stage.appendChild(svg);
        d.body.appendChild(stage);
        var tools = el("div", "ia-row");
        var name = document.createElement("input"); name.type = "text"; name.placeholder = "Nom de la zone (ex. Entree Antares)";
        var fn = el("label", "ia-field", "Nouvelle zone"); fn.appendChild(name);
        tools.appendChild(fn);
        // Type : trafic (etat fluide / bouchon) ou stationnement interdit (duree max)
        var kind = document.createElement("select");
        if (crowd) kind.appendChild(new Option("Foule", "foule"));
        else {
            kind.appendChild(new Option("Itineraire trace (du depart a l'arrivee)", "itineraire"));
            kind.appendChild(new Option("Trafic (fluide / ralenti / bouchon)", "trafic"));
            kind.appendChild(new Option("Stationnement interdit", "stationnement"));
        }
        var fk = el("label", "ia-field", "Type"); fk.appendChild(kind);
        if (!crowd && !rescue) tools.appendChild(fk);
        // Itineraire trace : itineraire Waze correspondant (facultatif)
        var wsel = document.createElement("select");
        wsel.appendChild(new Option("Aucun itineraire Waze", ""));
        var fw = el("label", "ia-field", "Itineraire Waze"); fw.appendChild(wsel);
        tools.appendChild(fw);
        var wazeLoaded = false;
        function syncKind() {
            fm.hidden = kind.value !== "stationnement";
            fw.hidden = kind.value !== "itineraire";
            name.placeholder = kind.value === "itineraire" ? "Nom (ex. Vers Antares)" : "Nom de la zone (ex. Entree Antares)";
            done.textContent = kind.value === "itineraire" ? "Terminer le trace" : "Terminer la zone";
            if (kind.value === "itineraire" && !wazeLoaded) {
                wazeLoaded = true;
                getJSON(API.cams + "/" + c.camera_id + "/" + c.channel + "/waze").then(function (r) {
                    (r.routes || []).forEach(function (x) {
                        wsel.appendChild(new Option(x.name + (x.distance_m != null ? " (" + (x.distance_m < 1000 ? x.distance_m + " m" : (x.distance_m / 1000).toFixed(1) + " km") + ")" : ""), x.id));
                    });
                }).catch(function () { wazeLoaded = false; });
            }
            draw();
        }
        var maxm = document.createElement("input"); maxm.type = "text"; maxm.inputMode = "numeric"; maxm.value = "10";
        var fm = el("label", "ia-field", "Alerte au-dela de (min)"); fm.appendChild(maxm);
        fm.hidden = true;
        tools.appendChild(fm);
        kind.addEventListener("change", function () { syncKind(); });
        // Foule : surface (saisie ou mesuree sur la carte) et seuils
        var surf = document.createElement("input"); surf.type = "text"; surf.inputMode = "decimal"; surf.placeholder = "m2";
        var geo = [];
        var fsu = el("label", "ia-field", "Surface (m2)"); fsu.appendChild(surf);
        var meas = el("button", "dt-btn"); meas.type = "button";
        meas.appendChild(icon("straighten")); meas.appendChild(document.createTextNode("Mesurer sur la carte"));
        meas.addEventListener("click", function (e) {
            e.preventDefault();
            measureOnMap(c, geo, function (area, g) { surf.value = String(Math.round(area)); geo = g; });
        });
        fsu.appendChild(meas);
        var sch = document.createElement("input"); sch.type = "text"; sch.inputMode = "decimal"; sch.value = "2";
        var stp = document.createElement("input"); stp.type = "text"; stp.inputMode = "decimal"; stp.value = "4";
        var fsc = el("label", "ia-field", "Charge a (pers./m2)"); fsc.appendChild(sch);
        var fst = el("label", "ia-field", "Trop de monde a (pers./m2)"); fst.appendChild(stp);
        if (crowd) { tools.appendChild(fsu); tools.appendChild(fsc); tools.appendChild(fst); }
        var undo = el("button", "dt-btn", "Annuler le dernier point"); undo.type = "button";
        var done = el("button", "dt-btn dt-btn-primary", "Terminer la zone"); done.type = "button";
        var tb = el("div", "ia-cam-actions"); tb.style.alignItems = "flex-end";
        tb.appendChild(undo); tb.appendChild(done);
        tools.appendChild(tb);
        d.body.appendChild(tools);
        var list = el("div", "ia-zedit-list");
        d.body.appendChild(list);
        var zones = [], pts = [];
        var COLORS = ["#7c3aed", "#0891b2", "#db2777", "#65a30d", "#ea580c", "#2563eb"];
        function draw() {
            svg.textContent = "";
            zones.forEach(function (z) {
                if (z.kind === "itineraire") { traceSvg(svg, z.line, z.color, z.name); return; }
                svg.appendChild(svgEl("polygon", { points: z.polygon.map(function (p) { return p.join(","); }).join(" "),
                    fill: "rgba(124,58,237,.15)", stroke: z.color, "stroke-width": 2, "vector-effect": "non-scaling-stroke" }));
            });
            if (pts.length && kind.value === "itineraire") {
                if (pts.length >= 2) traceSvg(svg, pts, "#f59e0b");
                else svg.appendChild(svgEl("ellipse", { cx: pts[0][0], cy: pts[0][1], rx: 0.008 / AR, ry: 0.008, fill: "#f59e0b" }));
            } else if (pts.length) {
                svg.appendChild(svgEl("polyline", { points: pts.map(function (p) { return p.join(","); }).join(" "),
                    fill: "rgba(245,158,11,.15)", stroke: "#f59e0b", "stroke-width": 2, "vector-effect": "non-scaling-stroke" }));
            }
            list.textContent = "";
            zones.forEach(function (z) {
                var r = el("div", "ia-group");
                var dot = el("i"); dot.style.background = z.color; r.appendChild(dot);
                r.appendChild(el("span", "", z.name));
                r.appendChild(el("span", "ia-meta", z.kind === "itineraire"
                    ? "Itineraire trace" + (z.waze_name ? ", Waze : " + z.waze_name : ", sans Waze")
                    : z.kind === "stationnement"
                    ? "Stationnement interdit, alerte au-dela de " + z.max_min + " min"
                    : z.kind === "foule"
                        ? (z.surface_m2 ? Math.round(z.surface_m2) + " m2 : charge a " + Math.round(z.surface_m2 * z.seuil_charge) +
                           " pers., trop de monde a " + Math.round(z.surface_m2 * z.seuil_trop) + " pers." : "SURFACE A RENSEIGNER")
                        : z.kind === "secours" ? "Secours : personnes au sol comptees ici" : "Trafic"));
                r.appendChild(el("span", "header-spacer"));
                if (z.kind === "foule") {
                    // Modifier la surface (saisie ou carte) sans redessiner la zone
                    var ed = el("button", "dt-link-btn", "Surface"); ed.type = "button";
                    ed.addEventListener("click", function () {
                        measureOnMap(c, z.geo || [], function (area, g) {
                            send("PUT", API.zones + "/" + z.id, { name: z.name, polygon: z.polygon, color: z.color, kind: "foule",
                                surface_m2: Math.round(area), geo: g, seuil_charge: z.seuil_charge, seuil_trop: z.seuil_trop })
                                .then(function () { toast("success", "Surface enregistree, historique recalcule"); return load(); })
                                .catch(function (e) { toast("error", e.message); });
                        }, z.surface_m2);
                    });
                    r.appendChild(ed);
                }
                var rm = el("button", "dt-link-btn", "Supprimer"); rm.type = "button";
                rm.addEventListener("click", function () {
                    send("DELETE", API.zones + "/" + z.id).then(load).catch(function (e) { toast("error", e.message); });
                });
                r.appendChild(rm);
                list.appendChild(r);
            });
        }
        function load() {
            return getJSON(API.zones + "?key=" + encodeURIComponent(c.key)).then(function (r) { zones = r.zones || []; draw(); });
        }
        stage.addEventListener("click", function (e) {
            var b = stage.getBoundingClientRect();
            pts.push([Math.round((e.clientX - b.left) / b.width * 10000) / 10000, Math.round((e.clientY - b.top) / b.height * 10000) / 10000]);
            draw();
        });
        undo.addEventListener("click", function () { pts.pop(); draw(); });
        done.addEventListener("click", function () {
            if (kind.value === "itineraire") {
                if (pts.length < 2) { toast("warning", "Cliquez au moins le depart puis l'arrivee (et des points intermediaires pour suivre la courbe)"); return; }
                if (!name.value.trim()) { toast("warning", "Donnez un nom a l'itineraire"); name.focus(); return; }
                var wopt = wsel.options[wsel.selectedIndex];
                send("POST", API.zones, { camera_id: c.camera_id, channel: c.channel, name: name.value.trim(), kind: "itineraire",
                    line: pts, waze_route: wsel.value || null, waze_name: wsel.value ? wopt.text.replace(/ \([^)]*\)$/, "") : null,
                    color: COLORS[(zones.length + 1) % COLORS.length] }).then(function () {
                    pts = []; name.value = ""; wsel.value = "";
                    toast("success", "Itineraire enregistre, historique recalcule");
                    return load();
                }).catch(function (e) { toast("error", e.message); });
                return;
            }
            if (pts.length < 3) { toast("warning", "Au moins 3 points"); return; }
            if (!name.value.trim()) { toast("warning", "Donnez un nom a la zone"); name.focus(); return; }
            var park = kind.value === "stationnement";
            var mm = parseInt(maxm.value, 10);
            if (park && !(mm >= 1 && mm <= 1440)) { toast("warning", "Duree maximale entre 1 et 1440 min"); maxm.focus(); return; }
            var body = { camera_id: c.camera_id, channel: c.channel, name: name.value.trim(), polygon: pts,
                         kind: kind.value, max_min: park ? mm : null,
                         color: park ? "#dc2626" : COLORS[zones.length % COLORS.length] };
            if (kind.value === "foule") {
                body.surface_m2 = surf.value.trim() ? Number(surf.value.replace(",", ".")) : null;
                body.seuil_charge = Number(sch.value.replace(",", ".")) || 2;
                body.seuil_trop = Number(stp.value.replace(",", ".")) || 4;
                body.geo = geo;
                if (!body.surface_m2) toast("info", "Zone sans surface : le nombre de personnes sera estime, mais pas le niveau");
            }
            send("POST", API.zones, body).then(function () {
                pts = []; name.value = ""; surf.value = ""; geo = [];
                toast("success", "Zone enregistree, historique recalcule");
                return load();
            }).catch(function (e) { toast("error", e.message); });
        });
        var close = el("button", "dt-btn", "Fermer"); close.type = "button";
        close.addEventListener("click", function () { d.close(); loadTrafic(); });
        d.foot.appendChild(close);
        if (!crowd) syncKind(); else fw.hidden = true;
        load();
        d.showModal();
    }

    /* ---------------- mesure d'une surface sur la vue aerienne ---------------- */
    // Aire geodesique d'un polygone [[lat, lng], ...] en m2 (exces spherique)
    function geoArea(pts) {
        var R = 6378137, rad = Math.PI / 180, s = 0, n = pts.length;
        for (var i = 0; i < n; i++) {
            var p1 = pts[i], p2 = pts[(i + 1) % n];
            s += (p2[1] - p1[1]) * rad * (2 + Math.sin(p1[0] * rad) + Math.sin(p2[0] * rad));
        }
        return Math.abs(s * R * R / 2);
    }
    // Dessin de la zone sur la vue aerienne IGN, centre sur la camera ;
    // cb(surface_m2, [[lat, lng], ...]). Ou saisie directe de la surface.
    function measureOnMap(c, initial, cb, current) {
        var d = dialog("Surface de la zone");
        d.style.width = "min(900px, calc(100vw - 24px))";
        d.body.appendChild(el("small", "ia-meta", "Cliquez les sommets de la zone sur la vue aerienne (la camera est au centre), ou tapez la surface d'apres un plan (interieur)."));
        var box = el("div"); box.style.height = "420px"; box.style.borderRadius = "8px";
        d.body.appendChild(box);
        var out = el("div", "ia-row");
        var manual = document.createElement("input"); manual.type = "text"; manual.inputMode = "decimal";
        manual.value = current ? String(Math.round(current)) : "";
        var fman = el("label", "ia-field", "Surface (m2)"); fman.appendChild(manual);
        out.appendChild(fman);
        d.body.appendChild(out);
        var pts = (initial || []).slice();
        var cam = ((M.gmapData && M.gmapData.cameras) || []).filter(function (x) { return x.id === c.camera_id; })[0];
        var center = cam ? [cam.lat, cam.lng] : (pts[0] || CIRCUIT);
        var mp, layer;
        function redraw() {
            layer.clearLayers();
            if (cam) L.circleMarker([cam.lat, cam.lng], { radius: 6, color: "#7c3aed", fillOpacity: 1 }).addTo(layer);
            if (pts.length) L.polygon(pts, { color: "#dc2626", weight: 2, fillOpacity: .2 }).addTo(layer);
            pts.forEach(function (p) { L.circleMarker(p, { radius: 4, color: "#dc2626", fillOpacity: 1 }).addTo(layer); });
            if (pts.length >= 3) manual.value = String(Math.round(geoArea(pts)));
        }
        var undo = el("button", "dt-btn", "Annuler le dernier point"); undo.type = "button";
        undo.addEventListener("click", function () { pts.pop(); redraw(); });
        var cancel = el("button", "dt-btn", "Annuler"); cancel.type = "button";
        cancel.addEventListener("click", function () { d.close(); });
        var ok = el("button", "dt-btn dt-btn-primary", "Utiliser cette surface"); ok.type = "button";
        ok.addEventListener("click", function () {
            var a = Number(String(manual.value).replace(",", "."));
            if (!(a > 0)) { toast("warning", "Dessinez la zone ou tapez une surface"); return; }
            cb(a, pts.length >= 3 ? pts : []);
            d.close();
        });
        d.foot.appendChild(undo); d.foot.appendChild(el("span", "header-spacer")); d.foot.appendChild(cancel); d.foot.appendChild(ok);
        d.showModal();
        mp = L.map(box, { center: center, zoom: 19, maxZoom: 22 });
        L.tileLayer("https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}", { attribution: "IGN-F/Geoplateforme", maxNativeZoom: 19, maxZoom: 22 }).addTo(mp);
        layer = L.layerGroup().addTo(mp);
        mp.on("click", function (e) { pts.push([+e.latlng.lat.toFixed(7), +e.latlng.lng.toFixed(7)]); redraw(); });
        if (pts.length >= 3) mp.fitBounds(L.latLngBounds(pts).pad(0.3));
        setTimeout(function () { mp.invalidateSize(); }, 80);
        redraw();
        if (!cam && !pts.length) {  // carte globale pas encore chargee : position de la camera
            getJSON("/api/detection/global-map").then(function (gm) {
                M.gmapData = M.gmapData || gm;
                cam = (gm.cameras || []).filter(function (x) { return x.id === c.camera_id; })[0];
                if (cam) { mp.setView([cam.lat, cam.lng], 19); redraw(); }
            }).catch(function () {});
        }
    }

    /* ---------------- Galerie (entrainement) ---------------- */
    // Compteur du jeu d'entrainement : avis par source, photos annotees
    // (objectif avant de re-entrainer le detecteur), avis par trace
    function loadTrainStats() {
        getJSON(API.gallery + "/stats").then(function (d) {
            var box = $("ia-gal-train");
            box.textContent = "";
            function votes(v) { return (v.up + v.down) + " avis (" + v.up + " oui, " + v.down + " non) sur " + v.total; }
            function block(ic, title, lines) {
                var b = el("div", "ia-train-block");
                var h = el("div", "ia-train-head"); h.appendChild(icon(ic)); h.appendChild(el("b", "", title));
                b.appendChild(h);
                lines.forEach(function (l) { b.appendChild(typeof l === "string" ? el("div", "ia-meta", l) : l); });
                box.appendChild(b);
            }
            var lb = d.labels, pct = Math.min(100, Math.round(lb.photos / d.target_photos * 100));
            var prog = el("div", "ia-train-prog");
            var bar = el("div", "ia-train-bar"); var fill = el("i"); fill.style.width = pct + "%"; bar.appendChild(fill);
            prog.appendChild(el("span", "", "Photos annotees : " + lb.photos + " / " + d.target_photos));
            prog.appendChild(bar);
            prog.title = "Objectif avant de re-entrainer le detecteur sur nos cameras : ouvrir une intrusion et poser les cadres (ou \"rien\" sur la photo)";
            var cats = Object.keys(lb.boxes).map(function (k) { return lb.boxes[k] + " " + k; }).join(", ");
            block("directions_walk", "Intrusions", [votes(d.intrusions), prog,
                lb.with_boxes + " avec cadres" + (cats ? " (" + cats + ")" : "") + ", " + lb.empty + " \"rien sur la photo\""]);
            var tr = Object.keys(d.traces || {});
            block("traffic", "Trafic", [votes(d.traffic), tr.length
                ? "Vrai niveau par trace : " + tr.map(function (k) { return k + " " + d.traces[k]; }).join(", ")
                : "Vrai niveau par trace : aucun (ouvrir une photo de camera avec traces)"]);
            block("groups", "Foule", [votes(d.crowd)]);
            if (d.secours) block("personal_injury", "Secours", [votes(d.secours)]);
            box.hidden = false;
        }).catch(function () {});
    }
    function hasVote(it) {
        return it.kind === "capture" ? !!(it.vote || Object.keys(it.zone_votes || {}).length) : !!(it.ai && it.ai.vote);
    }
    function loadGallery(reset) {
        var g = M.gal;
        if (reset) { g.items = []; g.next = null; $("ia-gal-grid").textContent = ""; loadTrainStats(); }
        var seq = ++g.seq;
        var p = new URLSearchParams({ source: g.source, limit: "60", mode: g.mode });
        if (!reset && g.next != null) {
            // A revoir : classement par interet, page suivante par rang. Les
            // photos qui ont recu un avis sortent de la liste : on ne compte
            // que celles encore sans avis (sinon on en sauterait)
            if (g.mode === "revoir") p.set("offset", String(g.items.filter(function (x) { return !hasVote(x); }).length));
            else p.set("before", g.next);
        }
        return getJSON(API.gallery + "?" + p.toString()).then(function (d) {
            if (seq !== g.seq) return;
            g.next = d.next;
            (d.items || []).forEach(function (it) { g.items.push(it); $("ia-gal-grid").appendChild(galItem(it)); });
            $("ia-gal-more").hidden = d.next == null;
            var c = d.counts || {};
            $("ia-gal-count").textContent = g.mode === "revoir"
                ? (c.revoir || 0) + " photo(s) a revoir sur " + (c.todo || 0) + " sans avis : les autres n'apprendraient rien a l'IA (identiques a la precedente, loin des seuils)"
                : g.items.length + " photo(s) affichee(s)" + (g.mode === "todo" ? " sur " + (c.todo || 0) + " sans avis" : "");
            if (!g.items.length) $("ia-gal-grid").appendChild(el("div", "ia-empty", g.mode === "revoir" ? "Rien a revoir : merci ! (\"Sans avis\" montre le reste)"
                : g.mode === "todo" ? "Tout a un avis : merci !" : "Aucune photo."));
        }).catch(function (e) { toast("error", e.message); });
    }
    // "Oui a tout le reste" : l'operateur marque d'abord les erreurs (Non),
    // puis valide d'un clic les photos affichees encore sans avis. Sont
    // exclues les intrusions que l'IA n'a pas jugees ; les mesures foule au
    // niveau "inconnu" (zone sans surface) comptent : "oui" = estimation juste.
    // Zones / traces d'une mesure trafic (vrai niveau dans l'agrandissement)
    function galZones(it) {
        if (it.kind !== "capture" || isCrowd(it)) return [];
        return (it.zones || []).filter(function (z) { return z.kind === "itineraire" || z.kind === "trafic"; });
    }
    function galPending() {
        return M.gal.items.filter(function (it) {
            if (it.kind === "capture") return !it.vote && !Object.keys(it.zone_votes || {}).length && (it.state || isCrowd(it));
            return it.ai && it.ai.status && it.ai.status !== "unverified" && !it.ai.vote;
        });
    }
    function galAllYes() {
        var todo = galPending();
        if (!todo.length) { toast("info", "Aucune photo affichee sans avis"); return; }
        var go = function () {
            var b = $("ia-gal-allyes"); b.disabled = true;
            var ok = 0, ko = 0, i = 0;
            function next() {
                if (i >= todo.length) {
                    b.disabled = false;
                    toast(ko ? "warning" : "success", ok + " avis \"Oui\" enregistre(s)" + (ko ? ", " + ko + " en echec" : "") + ", merci");
                    loadGallery(true);
                    return;
                }
                var it = todo[i++];
                var p = it.kind === "capture"
                    ? send("POST", API.samples + "/" + it.id + "/feedback", { vote: "up", state: null })
                    : send("POST", API.episodes + "/" + it.id + "/ai-vote", { vote: "up" });
                p.then(function () { ok++; }, function () { ko++; }).then(next);
            }
            next();
        };
        if (typeof showConfirmToast === "function")
            showConfirmToast("Dire \"Oui, l'IA a raison\" pour les " + todo.length + " photo(s) affichee(s) encore sans avis ?", { okLabel: "Oui a tout", type: "info" }).then(function (y) { if (y) go(); });
        else go();
    }
    function galItem(it) {
        var card = el("div", "ia-gal-item");
        var kc = it.kind === "capture" && isCrowd(it);
        var kr = it.kind === "capture" && isRescue(it);
        var imgBox = el("div", "ia-gal-img");
        var im = document.createElement("img"); im.loading = "lazy"; im.alt = ""; im.src = it.thumb || it.photo;
        imgBox.appendChild(im);
        var k = el("span", "ia-gal-kind");
        var pu = it.kind === "capture" ? (PURPOSE_UI[purposeOf(it)] || PURPOSE_UI.traffic) : { icon: "directions_walk", name: "Intrusion" };
        k.appendChild(icon(pu.icon));
        k.appendChild(document.createTextNode(pu.name));
        imgBox.appendChild(k);
        card.appendChild(imgBox);
        var b = el("div", "ia-gal-body");
        b.appendChild(el("div", "ia-gal-title", it.camera_name + (it.channel_name && it.channel_name !== it.camera_name ? " - " + it.channel_name : "")));
        b.appendChild(el("div", "ia-meta", fmtDay(it.at) + " " + fmtTime(it.at)));
        // Pourquoi cette photo est proposee (mode "A revoir")
        var rs = (it.review && it.review.reasons) || [];
        if (rs.length && M.gal.mode === "revoir") {
            var why = el("div", "ia-gal-why");
            why.appendChild(icon("school"));
            why.appendChild(el("span", "", rs.join(" - ")));
            b.appendChild(why);
        }
        var verdict = el("div", "ia-gal-verdict");
        b.appendChild(verdict);
        card.appendChild(b);
        function markDone() { if (M.gal.mode !== "all") card.classList.add("is-done"); }
        if (it.kind === "capture") {
            verdict.appendChild(stateChip(it.state));
            verdict.appendChild(el("span", "ia-meta", kc ? "~" + Math.round(it.total || 0) + " personne(s)"
                                                          : kr ? (it.total || 0) + " personne(s), " + (it.lying || 0) + " au sol"
                                                          : it.n + " vehicule(s), " + it.arretes + " arrete(s)"));
            // Carte compacte : l'avis global. Les traces (sens de circulation,
            // vrai niveau par trace) sont dans l'agrandissement
            var nz = galZones(it).length;
            b.appendChild(stateVote(it, markDone));
            if (nz) {
                var vz = galZones(it).filter(function (z) { return (it.zone_votes || {})[z.id]; }).length;
                b.appendChild(el("div", "ia-meta", "Niveau par trace : " + (vz ? vz + "/" + nz + " donne(s)" : "cliquer la photo")));
            }
            imgBox.title = nz ? "Agrandir : les 4 photos, les traces et le vrai niveau de chacun" : "Agrandir";
            imgBox.addEventListener("click", function () {
                getJSON(API.samples + "/" + it.id).then(function (s) {
                    return getJSON(API.zones + "?key=" + encodeURIComponent(s.camera_id + ":" + s.channel)).then(function (z) {
                        openViewer(s, z.zones || [], function (sv) {  // avis donne dans l'agrandissement : la carte suit
                            it.zone_votes = sv.zone_votes;
                            card.replaceWith(galItem(it));
                        });
                    });
                }).catch(function (e) { toast("error", e.message); });
            });
        } else {
            var ai = it.ai || {};
            if (ai.status) {
                // Pourcentage le plus haut de l'IA (detecteur ou avis), comme la page Intrusions
                var top = ai.label && ai.label !== "rien" ? { label: ai.label, p: ai.conf || 0 } : null;
                if (ai.guess && ai.guess[0] && (!top || ai.guess[0].p > top.p)) top = ai.guess[0];
                var lab = top ? (AI_NAME[top.label] || top.label) + " " + Math.round(top.p * 100) + " %" : "rien";
                verdict.appendChild(chip(ai.status === "filtered" ? "is-alerts" : ai.status === "confirmed" ? "" : "is-obs",
                    ai.status === "filtered" ? "filter_alt" : "verified", (ai.status === "filtered" ? "Filtree : " : "IA : ") + lab));
            }
            if (!it.armed) verdict.appendChild(chip("is-off", "schedule", "Hors planning"));
            if (it.labeled) verdict.appendChild(chip("", "ads_click", "Cadres poses"));
            // Avis = clic sur ce qu'est vraiment la cible : les 3 propositions de
            // l'IA (+ personne / vehicule si absents) ; "Autre" ouvre la fiche
            var row = el("div", "ia-gal-truths");
            var props = (ai.guess || []).filter(function (g) { return g.p >= 0.03; }).slice(0, 3).map(function (g) { return g.label; });
            ["personne", "vehicule"].forEach(function (l) { if (props.indexOf(l) < 0) props.push(l); });
            if (ai.truth && props.indexOf(ai.truth) < 0) props.unshift(ai.truth);
            var pc = {};
            (ai.guess || []).forEach(function (g) { pc[g.label] = g.p; });
            props.forEach(function (l) {
                var on = ai.truth === l;
                var bt = el("button", "ia-truth" + (on ? " is-on" : "")); bt.type = "button";
                bt.textContent = (AI_NAME[l] || l) + (pc[l] >= 0.03 ? " " + Math.round(pc[l] * 100) + " %" : "");
                bt.title = on ? "Retirer mon avis" : "C'est ca";
                bt.addEventListener("click", function () {
                    var nv = on ? null : l;
                    send("POST", API.episodes + "/" + it.id + "/ai-vote", { truth: nv }).then(function (r) {
                        it.ai = r.ai;
                        card.replaceWith(galItem(it));
                        toast("success", nv ? "Avis enregistre, merci" : "Avis retire");
                    }).catch(function (e) { toast("error", e.message); });
                });
                row.appendChild(bt);
            });
            var other = el("button", "ia-truth is-other", "Autre..."); other.type = "button";
            other.title = "Ouvrir la fiche : toutes les reponses, commentaire, cadres";
            other.addEventListener("click", function () {
                setMode("intrusions");
                if (window.IADetection) window.IADetection.openEpisode(it.id);
            });
            row.appendChild(other);
            b.appendChild(row);
            if (ai.vote && !ai.truth) b.appendChild(el("div", "ia-meta", "Ancien avis : " + (ai.vote === "up" ? "bien vu" : "erreur")));
            if (it.photos_n > 1) {
                // Une photo = une cible : la reponse photo par photo se donne dans la fiche
                var pp = el("button", "ia-truth is-other", it.photos_n + " cibles : " + (it.answered_n || 0) + "/" + it.photos_n + " repondues, repondre photo par photo");
                pp.type = "button";
                pp.addEventListener("click", function () {
                    setMode("intrusions");
                    if (window.IADetection) window.IADetection.openEpisode(it.id);
                });
                b.appendChild(pp);
            }
            if (ai.vote) markDone();
            imgBox.title = "Ouvrir la fiche (cadres, toutes les photos)";
            imgBox.addEventListener("click", function () {
                setMode("intrusions");
                if (window.IADetection) window.IADetection.openEpisode(it.id);
            });
        }
        if (it.kind === "capture" && (it.vote || Object.keys(it.zone_votes || {}).length)) markDone();
        return card;
    }

    /* ---------------- init ---------------- */
    function init() {
        if (!$("ia-modes")) return;
        Array.prototype.forEach.call(document.querySelectorAll("#ia-modes button"), function (b) {
            b.addEventListener("click", function () { setMode(b.dataset.mode); });
        });
        // Historique : un seul reglage pour les modes Trafic, Foule et Secours
        var hourBtns = "#ia-trafic-hours button, #ia-foule-hours button, #ia-secours-hours button";
        Array.prototype.forEach.call(document.querySelectorAll(hourBtns), function (b) {
            b.addEventListener("click", function () {
                M.hours = Number(b.dataset.hours);
                Array.prototype.forEach.call(document.querySelectorAll(hourBtns), function (x) { x.classList.toggle("active", x.dataset.hours === b.dataset.hours); });
                renderTrafic();
            });
        });
        $("ia-trafic-add").addEventListener("click", function () { openAdd("traffic"); });
        $("ia-foule-add").addEventListener("click", function () { openAdd("crowd"); });
        $("ia-secours-add").addEventListener("click", function () { openAdd("secours"); });
        Array.prototype.forEach.call(document.querySelectorAll("#ia-gal-source button"), function (b) {
            b.addEventListener("click", function () {
                M.gal.source = b.dataset.source;
                Array.prototype.forEach.call(document.querySelectorAll("#ia-gal-source button"), function (x) { x.classList.toggle("active", x === b); });
                loadGallery(true);
            });
        });
        Array.prototype.forEach.call(document.querySelectorAll("#ia-gal-mode button"), function (b) {
            b.addEventListener("click", function () {
                M.gal.mode = b.dataset.gmode;
                Array.prototype.forEach.call(document.querySelectorAll("#ia-gal-mode button"), function (x) { x.classList.toggle("active", x === b); });
                loadGallery(true);
            });
        });
        $("ia-gal-more").addEventListener("click", function () { loadGallery(false); });
        $("ia-gal-allyes").addEventListener("click", galAllYes);
        $("ia-gdetail-close").addEventListener("click", closeGDetail);
        Array.prototype.forEach.call(document.querySelectorAll("#ia-gmap-filter button"), function (b) {
            b.addEventListener("click", function () {
                M.gmapFilter = b.dataset.f;
                Array.prototype.forEach.call(document.querySelectorAll("#ia-gmap-filter button"), function (x) { x.classList.toggle("active", x === b); });
                renderGmap();
            });
        });
        // Ouverture sur la carte globale ; ?mode= pour un lien direct, et un
        // lien vers une intrusion (?episode=, alertes, WhatsApp) reste en mode Intrusions
        var qs = new URLSearchParams(location.search);
        var m = qs.get("mode") || (qs.get("episode") ? "intrusions" : "carte");
        if (MODES.indexOf(m) < 0) m = "carte";
        setMode(m);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
