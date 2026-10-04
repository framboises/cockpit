/////////////////////////////////////////////////////////////////////////////////////////////////////
// CONSTANTES
/////////////////////////////////////////////////////////////////////////////////////////////////////

let categories = [];
let datasets = {};
let categorySuggestions = {};
let awesomplete;
let marker;
let menuOpen = false;
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content ?? "";

window.selectedEvent = null;
window.selectedYear = null;

// --- Block permissions ---
window.isBlockAllowed = function(id) {
    return !window.__allowedBlocks || window.__allowedBlocks.indexOf(id) >= 0;
};
(function() {
    if (!window.__allowedBlocks) return;
    var ids = [
        "widget-traffic","widget-counters","widget-parkings","widget-comms",
        "meteo-previsions","timeline-main","map-main","status-card",
        "widget-right-1","widget-right-2","widget-right-3","widget-right-4"
    ];
    ids.forEach(function(id) {
        if (!window.isBlockAllowed(id)) {
            var el = document.getElementById(id);
            if (el) el.style.display = "none";
        }
    });
    // Gerer timeline/carte selon les permissions
    var tlOk = window.isBlockAllowed("timeline-main");
    var mapOk = window.isBlockAllowed("map-main");
    if (!tlOk && mapOk) {
        // Forcer l'affichage carte immediatement (avant que map_view.js charge)
        var tlEl = document.getElementById("timeline-main");
        var mapEl = document.getElementById("map-main");
        if (tlEl) tlEl.style.display = "none";
        if (mapEl) mapEl.style.display = "block";
        // Signaler a map_view.js de demarrer en mode carte
        window.__forceMapView = true;
    }
    // Masquer le toggle si une seule vue autorisee
    if (!tlOk || !mapOk) {
        var toggle = document.querySelector(".view-toggle");
        if (toggle) toggle.style.display = "none";
    }
    // Masquer les elements lies a la timeline si elle n'est pas autorisee
    if (!tlOk) {
        var nowBtn = document.getElementById("nowline-toggle");
        var addBtn = document.getElementById("add-event-button");
        var upcoming = document.querySelector(".header-upcoming-wrap") || document.getElementById("header-upcoming");
        if (nowBtn) nowBtn.style.display = "none";
        if (addBtn) addBtn.style.display = "none";
        if (upcoming) upcoming.style.display = "none";
    }
})();

// --- Block layout relocation (gauche/droite par groupe) ---
(function() {
    var layout = window.__blockLayout;
    if (!layout) return;
    var leftPanel = document.getElementById("widgets-panel");
    var rightPanel = document.getElementById("widgets-panel-right");
    if (!leftPanel || !rightPanel) return;
    var leftIds = layout.left || [];
    var rightIds = layout.right || [];
    // Deplacer dans l'ordre configure
    leftIds.forEach(function(id) {
        var el = document.getElementById(id);
        if (el) leftPanel.appendChild(el);
    });
    rightIds.forEach(function(id) {
        var el = document.getElementById(id);
        if (el) rightPanel.appendChild(el);
    });
})();

// --- Widgets pliables ---
(function() {
    document.querySelectorAll(".collapsible-widget-header").forEach(function(header) {
        var body = header.nextElementSibling;
        var card = header.closest(".widget-card");
        if (!body) return;
        header.style.cursor = "pointer";
        header.addEventListener("click", function(e) {
            if (e.target.closest("button")) return;
            var collapsed = body.classList.toggle("widget-body-collapsed");
            header.classList.toggle("collapsed", collapsed);
            if (card) card.classList.toggle("widget-card-collapsed", collapsed);
        });
    });
})();

// --- Group pills in header ---
(function() {
    var groups = window.__userGroups;
    var container = document.getElementById("user-group-pills");
    if (!container || !groups || !groups.length) return;
    groups.forEach(function(g) {
        var pill = document.createElement("span");
        pill.className = "user-group-pill";
        pill.textContent = g.name;
        pill.style.background = g.color || "#6366f1";
        container.appendChild(pill);
    });
})();

/////////////////////////////////////////////////////////////////////////////////////////////////////
// UTILITAIRE
/////////////////////////////////////////////////////////////////////////////////////////////////////

function on(elOrId, event, handler) {
    const el = typeof elOrId === "string" ? document.getElementById(elOrId) : elOrId;
    if (el) el.addEventListener(event, handler, false);
}

// Fullscreen toggle
(function () {
    var btn = document.getElementById("app-fullscreen-btn");
    if (!btn) return;
    var ico = btn.querySelector(".material-symbols-outlined");
    btn.addEventListener("click", function () {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen().catch(function () {});
        } else {
            document.exitFullscreen();
        }
    });
    document.addEventListener("fullscreenchange", function () {
        ico.textContent = document.fullscreenElement ? "fullscreen_exit" : "fullscreen";
        btn.title = document.fullscreenElement ? "Quitter plein ecran" : "Plein ecran";
    });
})();

// ---------------------------------------------------------------------------
// Footer quick-action twins (synced with header buttons)
// ---------------------------------------------------------------------------
(function () {
    var pairs = [
        { sq: "sq-timeline",   hdr: "view-timeline-btn" },
        { sq: "sq-map",        hdr: "view-map-btn" },
        { sq: "sq-nowline",    hdr: "nowline-toggle" },
        { sq: "sq-fullscreen", hdr: "app-fullscreen-btn" },
        { sq: "sq-add",        hdr: "add-event-button" },
    ];

    pairs.forEach(function (p) {
        var sq  = document.getElementById(p.sq);
        var hdr = document.getElementById(p.hdr);
        if (!sq || !hdr) return;

        // Click on twin -> click header original
        sq.addEventListener("click", function () { hdr.click(); });

        // Sync visual state: observe class changes + icon text on header btn
        function sync() {
            var isActive = hdr.classList.contains("active");
            sq.classList.toggle("active", isActive);
            // Mirror icon text
            var hdrIco = hdr.querySelector(".material-symbols-outlined");
            var sqIco  = sq.querySelector(".material-symbols-outlined");
            if (hdrIco && sqIco && sqIco.textContent !== hdrIco.textContent) {
                sqIco.textContent = hdrIco.textContent;
            }
        }

        // Observe header button for class / child text changes
        var mo = new MutationObserver(sync);
        mo.observe(hdr, { attributes: true, attributeFilter: ["class"] });
        // Also observe the icon span for text changes (nowline, fullscreen)
        var hdrIco = hdr.querySelector(".material-symbols-outlined");
        if (hdrIco) mo.observe(hdrIco, { childList: true, characterData: true, subtree: true });

        // Initial sync
        sync();
    });

    // Respect same visibility rules as header (hidden if permission-blocked)
    var sqTimeline = document.getElementById("sq-timeline");
    var sqMap      = document.getElementById("sq-map");
    var sqNowline  = document.getElementById("sq-nowline");
    var sqAdd      = document.getElementById("sq-add");
    var toggle     = document.querySelector(".view-toggle");

    if (toggle && toggle.style.display === "none") {
        if (sqTimeline) sqTimeline.style.display = "none";
        if (sqMap) sqMap.style.display = "none";
    }
    var nowBtn = document.getElementById("nowline-toggle");
    if (nowBtn && nowBtn.style.display === "none" && sqNowline) sqNowline.style.display = "none";
    var addBtn = document.getElementById("add-event-button");
    if (addBtn && addBtn.style.display === "none" && sqAdd) sqAdd.style.display = "none";
})();

function apiPost(url, payload, _retried){
    return fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]')?.content) || ''
        },
        body: JSON.stringify(payload)
    }).then(r => r.json()).then(res => {
        // Jeton CSRF expire (onglet ouvert > 1 h) : renouvele puis requete rejouee une fois
        if (!_retried && res && res.code === 'csrf' && window.CockpitCsrf) {
            return window.CockpitCsrf.refresh().then(st => st.ok ? apiPost(url, payload, true) : res);
        }
        return res;
    });
}

function getCurrentEventYear() {
    return {
        event: window.selectedEvent || '',
        year: String(window.selectedYear || '')
    };
}

/////////////////////////////////////////////////////////////////////////////////////////////////////
// SIDEBAR (nouveau comportement collapse/expand)
/////////////////////////////////////////////////////////////////////////////////////////////////////

document.addEventListener("DOMContentLoaded", function () {
    // Le pilotage de la barre laterale (repli, mode mobile, boutons burger)
    // vit desormais dans static/js/sidebar.js, partage par les onze pages qui
    // incluent templates/_sidebar.html. NE PAS le rebrancher ici : deux
    // ecouteurs sur #sidebarToggle se declenchent tous les deux et s'annulent.
    var mobileMQ = window.matchMedia("(max-width: 820px)");

    // ======================== MOBILE BOTTOM NAV (<=820px) ========================
    (function () {
        var nav = document.getElementById("mobile-bottom-nav");
        if (!nav) return;
        var tabs = nav.querySelectorAll(".mb-tab");
        var activeWidget = null;
        var backgroundView = "timeline"; // "timeline" | "map" — vue de fond derriere les overlays

        function setActive(targetId) {
            tabs.forEach(function (t) {
                t.classList.toggle("is-active", t.dataset.target === targetId);
            });
        }

        function closeFullscreen() {
            if (!activeWidget) return;
            var btn = activeWidget.querySelector(":scope > .widget-header > .mb-close-btn, :scope > .status-indicator > .mb-close-btn");
            if (btn) btn.remove();
            activeWidget.classList.remove("mobile-fullscreen");
            activeWidget = null;
        }

        function injectCloseButton(widget) {
            var header = widget.querySelector(":scope > .widget-header") || widget.querySelector(":scope > .status-indicator");
            if (!header) return;
            if (header.querySelector(".mb-close-btn")) return;
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "mb-close-btn";
            btn.setAttribute("aria-label", "Fermer");
            var icon = document.createElement("span");
            icon.className = "material-symbols-outlined";
            icon.textContent = "close";
            btn.appendChild(icon);
            btn.addEventListener("click", function (e) {
                e.stopPropagation();
                closeFullscreen();
                setActive(backgroundView);
            });
            header.appendChild(btn);
        }

        function openFullscreen(widget) {
            if (activeWidget === widget) return;
            closeFullscreen();
            widget.classList.add("mobile-fullscreen");
            injectCloseButton(widget);
            activeWidget = widget;
            // Chart.js et autres modules s'accrochent au resize pour recalculer
            window.dispatchEvent(new Event("resize"));
        }

        function showTimeline() {
            closeFullscreen();
            // Toujours cliquer : la fonction switchView() interne de map_view.js
            // a son propre guard si on est deja dans la vue cible.
            var timelineBtn = document.getElementById("view-timeline-btn");
            if (timelineBtn) timelineBtn.click();
            setActive("timeline");
            document.body.classList.remove("map-view");
            backgroundView = "timeline";
        }

        function showMap() {
            closeFullscreen();
            var mapBtn = document.getElementById("view-map-btn");
            if (mapBtn) mapBtn.click();
            setActive("map");
            document.body.classList.add("map-view");
            backgroundView = "map";
        }

        tabs.forEach(function (tab) {
            tab.addEventListener("click", function () {
                var target = tab.dataset.target;
                if (target === "timeline") return showTimeline();
                if (target === "map") return showMap();
                var widget = document.getElementById(target);
                if (!widget) return;
                openFullscreen(widget);
                setActive(target);
            });
        });

        // FAB central "+" : declenche la creation de fiche d'intervention
        // en reutilisant le handler existant du widget Main courante (#pcorg-add-btn).
        var fab = document.getElementById("mb-fab-add");
        if (fab) {
            fab.addEventListener("click", function () {
                var addBtn = document.getElementById("pcorg-add-btn");
                if (addBtn) addBtn.click();
            });
        }

        // Quand on sort du mode mobile, on nettoie tout
        mobileMQ.addEventListener("change", function (ev) {
            if (!ev.matches) {
                closeFullscreen();
                setActive("timeline");
            }
        });
    })();

    // ======================== SIMULATION CLOCK (admin) ========================
    var simToggle = document.getElementById("sidebar-sim-toggle");
    var simBody = document.getElementById("sidebar-sim-body");
    var simDatetime = document.getElementById("sim-datetime");
    var simSpeed = document.getElementById("sim-speed");
    var simStart = document.getElementById("sim-start");
    var simPause = document.getElementById("sim-pause");
    var simReset = document.getElementById("sim-reset");
    var simClock = document.getElementById("sim-clock-display");
    var simDate = document.getElementById("sim-date-display");
    var simBadge = document.getElementById("sim-badge");
    var simTickTimer = null;

    var SIM_DAYS = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];

    function simUpdateDisplay() {
        if (!simClock || !window.TimelineClock) return;
        var now = window.TimelineClock.get();
        var hh = String(now.getHours()).padStart(2, "0");
        var mm = String(now.getMinutes()).padStart(2, "0");
        var ss = String(now.getSeconds()).padStart(2, "0");
        simClock.textContent = hh + ":" + mm + ":" + ss;

        var day = SIM_DAYS[now.getDay()];
        var dd = String(now.getDate()).padStart(2, "0");
        var mo = String(now.getMonth() + 1).padStart(2, "0");
        var yy = now.getFullYear();
        if (simDate) simDate.textContent = day + " " + dd + "/" + mo + "/" + yy;

        var isSimMode = window.TimelineClock._mode === "sim";
        if (simClock) simClock.className = "sim-clock" + (isSimMode ? " sim-active" : "");
        if (simDate) simDate.className = "sim-date" + (isSimMode ? " sim-active" : "");
        if (simBadge) simBadge.style.display = isSimMode ? "" : "none";
    }

    // Toggle panel
    if (simToggle && simBody) {
        simToggle.addEventListener("click", function () {
            var open = simBody.style.display === "none";
            simBody.style.display = open ? "" : "none";
        });
    }

    // Start simulation
    if (simStart) {
        simStart.addEventListener("click", function () {
            if (!window.TimelineClock || !simDatetime) return;
            var val = simDatetime.value;
            if (!val) {
                showToast("warning", "Choisissez une date et heure de simulation");
                return;
            }
            // datetime-local gives "YYYY-MM-DDTHH:MM"
            var dt = val.replace("T", " ");
            window.TimelineClock.setSim(dt);
            var speed = parseFloat(simSpeed ? simSpeed.value : "0.0167") || 0.0167;
            window.TimelineClock.setSpeed(speed);
            window.TimelineClock.play();

            simStart.disabled = true;
            if (simPause) simPause.disabled = false;
            var mult = Math.round(speed * 60);
            showToast("info", "Simulation demarree: " + dt + " (x" + mult + ")");
        });
    }

    // Pause
    if (simPause) {
        simPause.addEventListener("click", function () {
            if (!window.TimelineClock) return;
            window.TimelineClock.pause();
            simPause.disabled = true;
            if (simStart) simStart.disabled = false;
            showToast("info", "Simulation en pause");
        });
    }

    // Reset to real time
    if (simReset) {
        simReset.addEventListener("click", function () {
            if (!window.TimelineClock) return;
            window.TimelineClock.useReal();
            if (simStart) simStart.disabled = false;
            if (simPause) simPause.disabled = true;
            showToast("success", "Retour a l'heure reelle");
        });
    }

    // Speed change on-the-fly
    if (simSpeed) {
        simSpeed.addEventListener("change", function () {
            if (!window.TimelineClock || window.TimelineClock._mode !== "sim") return;
            var speed = parseFloat(this.value) || 0.0167;
            window.TimelineClock.setSpeed(speed);
        });
    }

    // Tick display every 500ms
    if (simClock) {
        simUpdateDisplay();
        simTickTimer = setInterval(simUpdateDisplay, 500);
    }
});

// ==========================================================================
// SELECTION EVENEMENT / ANNEE
//
// Defaut = evenement du jour (/api/event/current, source unique
// event_courant.py : epreuve active de montage.start a demontage.end, sinon
// SAISON). Un choix MANUEL est garde 12 h ; au-dela, ou si la selection etait
// automatique, la page suit l'evenement du jour. Un SAISON d'une annee passee
// saute a l'annee courante. Stockage : `cockpit_sel` = {event, year, manual,
// at} ; `cockpit_event` / `cockpit_year` restent ecrits (sidebar.js, pmv.js,
// dispatch_service.js les relisent).
// ==========================================================================
var COCKPIT_SEL_KEY = 'cockpit_sel';
var COCKPIT_MANUAL_TTL_MS = 12 * 3600 * 1000;
var COCKPIT_FOLLOW_MS = 5 * 60 * 1000;
// 2023 est la premiere edition dont on ait des donnees exploitables dans
// historique_controle. Changer cette borne impose de la changer aussi dans
// templates/scan_report.html, qui a sa propre copie du selecteur.
var COCKPIT_START_YEAR = 2023;
window.cockpitEventCurrent = null;   // dernier payload de /api/event/current

function cockpitIsSaison(ev) {
    return String(ev || '').trim().toUpperCase() === 'SAISON';
}

function _cockpitReadSel() {
    try {
        var raw = localStorage.getItem(COCKPIT_SEL_KEY);
        var s = raw ? JSON.parse(raw) : null;
        if (s && s.event && s.year) return s;
    } catch (e) {}
    return null;
}

function _cockpitWriteSel(ev, yr, manual, at) {
    try {
        localStorage.setItem(COCKPIT_SEL_KEY, JSON.stringify({
            event: ev, year: String(yr), manual: !!manual, at: at || Date.now()
        }));
        localStorage.setItem('cockpit_event', ev);
        localStorage.setItem('cockpit_year', String(yr));
    } catch (e) {}
}

function _cockpitManualRecent(sel) {
    return !!(sel && sel.manual && sel.at && (Date.now() - sel.at) < COCKPIT_MANUAL_TTL_MS);
}

function _cockpitSaisonYear(cur) {
    var acts = (cur && cur.active) || [];
    for (var i = 0; i < acts.length; i++) {
        if (acts[i].kind === 'saison') return parseInt(acts[i].year, 10);
    }
    return new Date().getFullYear();
}

function fetchEventCurrent() {
    return fetch('/api/event/current', { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
            if (d && d.current) window.cockpitEventCurrent = d;
            return (d && d.current) ? d : null;
        })
        .catch(function () { return null; });
}

// {event, year:int, manual, at} : choix manuel recent, sinon evenement du jour,
// sinon (API indisponible) dernier choix connu.
function _cockpitResolveSelection(cur) {
    var sel = _cockpitReadSel();
    var out;
    if (_cockpitManualRecent(sel)) {
        out = { event: sel.event, year: parseInt(sel.year, 10), manual: true, at: sel.at };
    } else if (cur && cur.current) {
        out = { event: cur.current.event, year: parseInt(cur.current.year, 10), manual: false, at: null };
    } else {
        var le = null, ly = null;
        try { le = localStorage.getItem('cockpit_event'); ly = localStorage.getItem('cockpit_year'); } catch (e) {}
        out = {
            event: (sel && sel.event) || le || '24H AUTOS',
            year: parseInt((sel && sel.year) || ly, 10) || new Date().getFullYear(),
            manual: !!(sel && sel.manual), at: sel ? sel.at : null
        };
    }
    var sy = _cockpitSaisonYear(cur);
    if (cockpitIsSaison(out.event) && (!out.year || out.year < sy)) out.year = sy;
    return out;
}

function _cockpitActiveEpreuves(cur) {
    var names = [];
    ((cur && cur.active) || []).forEach(function (a) {
        if (a.kind === 'epreuve' && names.indexOf(a.event) === -1) names.push(a.event);
    });
    return names;
}

function _cockpitPopulateEvents(eventSelect, names, cur, selected) {
    eventSelect.textContent = '';
    var actives = _cockpitActiveEpreuves(cur);
    if (selected && names.indexOf(selected) === -1) names = names.concat([selected]);
    actives.forEach(function (n) { if (names.indexOf(n) === -1) names = names.concat([n]); });
    function addOpt(parent, name, label) {
        var o = document.createElement('option');
        o.value = name;
        o.textContent = label || name;
        parent.appendChild(o);
    }
    if (actives.length) {
        // Plusieurs epreuves actives : elles passent en tete (prioritaire d'abord)
        var g1 = document.createElement('optgroup');
        g1.label = 'En cours';
        actives.forEach(function (n) { addOpt(g1, n); });
        eventSelect.appendChild(g1);
        var g2 = document.createElement('optgroup');
        g2.label = 'Tous les evenements';
        names.forEach(function (n) { if (actives.indexOf(n) === -1) addOpt(g2, n); });
        eventSelect.appendChild(g2);
    } else {
        names.forEach(function (n) { addOpt(eventSelect, n); });
    }
}

function _cockpitPopulateYears(yearSelect, selectedYear) {
    yearSelect.textContent = '';
    var currentYear = new Date().getFullYear();
    var years = [];
    for (var y = COCKPIT_START_YEAR; y <= currentYear + 1; y++) years.push(y);
    if (selectedYear && years.indexOf(selectedYear) === -1) {
        years.push(selectedYear);
        years.sort();
    }
    years.forEach(function (y) {
        var o = document.createElement('option');
        o.value = y;
        o.textContent = y;
        yearSelect.appendChild(o);
    });
}

// Applique une selection (selects + globals + stockage) et recharge la page.
function cockpitApplySelection(ev, yr, manual, at, noReload) {
    yr = parseInt(yr, 10);
    var eventSelect = document.getElementById('event-select');
    var yearSelect = document.getElementById('year-select');
    if (eventSelect) {
        var has = Array.prototype.some.call(eventSelect.options, function (o) { return o.value === ev; });
        if (!has) {
            var o = document.createElement('option');
            o.value = ev;
            o.textContent = ev;
            eventSelect.appendChild(o);
        }
        eventSelect.value = ev;
    }
    if (yearSelect) {
        var hasY = Array.prototype.some.call(yearSelect.options, function (o) { return parseInt(o.value, 10) === yr; });
        if (!hasY) _cockpitPopulateYears(yearSelect, yr);
        yearSelect.value = String(yr);
    }
    window.selectedEvent = ev;
    window.selectedYear = yr;
    _cockpitWriteSel(ev, yr, manual, at);
    renderEventSuggest();
    renderPriorityChip();
    applySaisonOnlyBlocks();
    if (!noReload) loadCockpitData();
}

// Bandeau discret "Evenement du jour : X" quand la selection n'est pas
// l'evenement en cours : epreuve non active, ou SAISON alors qu'une epreuve
// a pris la main (bascule). Une epreuve secondaire active ne declenche rien.
function renderEventSuggest() {
    var host = document.querySelector('.header-event-info');
    if (!host) return;
    var chip = document.getElementById('header-event-suggest');
    var cur = window.cockpitEventCurrent;
    var show = false;
    if (cur && cur.current && window.selectedEvent) {
        var selYr = parseInt(window.selectedYear, 10);
        var c = cur.current;
        var isCurrent = c.event === window.selectedEvent && parseInt(c.year, 10) === selYr;
        var isActive = (cur.active || []).some(function (a) {
            return a.event === window.selectedEvent && parseInt(a.year, 10) === selYr;
        });
        show = !isCurrent && (!isActive || (cockpitIsSaison(window.selectedEvent) && c.kind === 'epreuve'));
    }
    if (!show) {
        if (chip) chip.remove();
        return;
    }
    if (!chip) {
        chip = document.createElement('button');
        chip.type = 'button';
        chip.id = 'header-event-suggest';
        chip.className = 'header-event-suggest';
        chip.addEventListener('click', function () {
            var cc = window.cockpitEventCurrent && window.cockpitEventCurrent.current;
            if (!cc) return;
            cockpitApplySelection(cc.event, cc.year, false);
            if (typeof showToast === 'function') showToast('info', 'Evenement du jour : ' + cc.event + ' ' + cc.year);
        });
        host.appendChild(chip);
    }
    chip.textContent = '';
    var ico = document.createElement('span');
    ico.className = 'material-symbols-outlined';
    ico.textContent = 'swap_horiz';
    chip.appendChild(ico);
    var lbl = document.createElement('span');
    lbl.textContent = 'Evenement du jour : ' + cur.current.event + ' ' + cur.current.year;
    chip.appendChild(lbl);
    chip.title = 'Basculer sur l\'evenement en cours';
}

// Blocs "seulement en SAISON" (fiche groupe, bascule SAISON de chaque bloc) :
// masques avec leur onglet mobile quand la selection n'est pas SAISON. Classe
// avec !important : les scripts des blocs peuvent poser style.display sans
// annuler ce masquage.
function applySaisonOnlyBlocks() {
    var ids = window.__saisonOnlyBlocks;
    if (!Array.isArray(ids) || !ids.length) return;
    var hide = !cockpitIsSaison(window.selectedEvent);
    ids.forEach(function (id) {
        var w = document.getElementById(id);
        if (w) w.classList.toggle('block-hidden-saison', hide);
        document.querySelectorAll('.mb-tab[data-target="' + id + '"]').forEach(function (t) {
            t.classList.toggle('block-hidden-saison', hide);
        });
    });
}

// Epreuves simultanees : pastille "N epreuves en cours". La priorite est un
// choix GLOBAL d'un admin (PUT /api/event/priority) ; sans choix, l'epreuve
// deja en cours garde la main (pastille ambre "priorite a choisir"). Changer
// la priorite ne deplace aucune fiche existante.
function renderPriorityChip() {
    var host = document.querySelector('.header-event-info');
    if (!host) return;
    var cur = window.cockpitEventCurrent;
    var chip = document.getElementById('header-event-priority');
    var epreuves = ((cur && cur.active) || []).filter(function (a) { return a.kind === 'epreuve'; });
    if (!cur || !cur.conflict || epreuves.length < 2) {
        if (chip) chip.remove();
        var m0 = document.getElementById('header-event-priority-menu');
        if (m0) m0.remove();
        return;
    }
    var isAdmin = window.__userIsAdmin === true || window.__userIsAdmin === 'true';
    if (!chip) {
        chip = document.createElement('button');
        chip.type = 'button';
        chip.id = 'header-event-priority';
        chip.className = 'header-event-priority';
        chip.addEventListener('click', function (e) {
            e.stopPropagation();
            if (window.__userIsAdmin === true || window.__userIsAdmin === 'true') togglePriorityMenu(chip);
        });
        host.appendChild(chip);
    }
    chip.classList.toggle('is-unchosen', !cur.priority_chosen);
    chip.textContent = '';
    var ico = document.createElement('span');
    ico.className = 'material-symbols-outlined';
    ico.textContent = 'call_split';
    chip.appendChild(ico);
    var lbl = document.createElement('span');
    lbl.textContent = epreuves.length + ' epreuves en cours - prioritaire : ' + epreuves[0].event
        + (cur.priority_chosen ? '' : ' (a choisir)');
    chip.appendChild(lbl);
    chip.title = (isAdmin ? 'Choisir l\'epreuve prioritaire (choix global, aucune fiche deplacee)'
                          : 'Epreuve prioritaire choisie par un administrateur')
        + '\n' + epreuves.map(function (a) { return a.event + ' ' + a.year + ' (' + (a.phase || '') + ')'; }).join('\n');
}

function togglePriorityMenu(anchor) {
    var menu = document.getElementById('header-event-priority-menu');
    if (menu) { menu.remove(); return; }
    var cur = window.cockpitEventCurrent;
    var epreuves = ((cur && cur.active) || []).filter(function (a) { return a.kind === 'epreuve'; });
    menu = document.createElement('div');
    menu.id = 'header-event-priority-menu';
    menu.className = 'header-event-priority-menu';
    var title = document.createElement('div');
    title.className = 'hep-title';
    title.textContent = 'Epreuve prioritaire (pour tous les postes)';
    menu.appendChild(title);
    epreuves.forEach(function (a, i) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'hep-item' + (i === 0 ? ' is-current' : '');
        b.textContent = a.event + ' ' + a.year + (a.phase ? ' - ' + a.phase : '') + (i === 0 ? '  (prioritaire)' : '');
        b.addEventListener('click', function () {
            menu.remove();
            if (i === 0 && cur.priority_chosen) return;
            setEventPriority(a.event, a.year);
        });
        menu.appendChild(b);
    });
    var note = document.createElement('div');
    note.className = 'hep-note';
    note.textContent = 'Les nouvelles fiches, la selection par defaut et les rapports suivent ce choix. Aucune fiche existante n\'est deplacee.';
    menu.appendChild(note);
    var r = anchor.getBoundingClientRect();
    menu.style.top = (r.bottom + window.scrollY + 4) + 'px';
    menu.style.left = Math.max(8, r.left + window.scrollX) + 'px';
    document.body.appendChild(menu);
    setTimeout(function () {
        document.addEventListener('click', function close(ev) {
            if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('click', close); }
        });
    }, 0);
}

function setEventPriority(ev, yr) {
    var meta = document.querySelector('meta[name="csrf-token"]');
    fetch('/api/event/priority', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': meta ? meta.getAttribute('content') : '' },
        body: JSON.stringify({ event: ev, year: yr })
    }).then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (d) {
        if (!d || !d.ok) {
            if (typeof showToast === 'function') showToast('error', 'Priorite non enregistree' + (d && d.error ? ' (' + d.error + ')' : ''));
            return;
        }
        if (typeof showToast === 'function') showToast('success', 'Epreuve prioritaire : ' + ev + ' ' + yr);
        window.cockpitEventCurrent = d;
        renderPriorityChip();
        // La page suit la nouvelle priorite si sa selection etait automatique
        _cockpitFollowCurrent();
    }).catch(function () {
        if (typeof showToast === 'function') showToast('error', 'Priorite non enregistree (reseau)');
    });
}

// Suivi de l'evenement du jour (accueil seulement : sur l'edition d'un
// parametrage, une bascule automatique serait destructrice).
function _cockpitFollowCurrent() {
    fetchEventCurrent().then(function (cur) {
        if (!cur) return;
        var eventSelect = document.getElementById('event-select');
        // Liste "En cours" a jour si les epreuves actives ont change
        if (eventSelect && window._cockpitEventNames) {
            var before = eventSelect.value;
            _cockpitPopulateEvents(eventSelect, window._cockpitEventNames.slice(), cur, before);
            eventSelect.value = before;
        }
        var sel = _cockpitReadSel();
        if (!_cockpitManualRecent(sel)) {
            var c = cur.current;
            if (c.event !== window.selectedEvent || parseInt(c.year, 10) !== parseInt(window.selectedYear, 10)) {
                cockpitApplySelection(c.event, c.year, false);
                if (typeof showToast === 'function') showToast('info', 'Bascule sur l\'evenement du jour : ' + c.event + ' ' + c.year);
                return;
            }
        }
        renderEventSuggest();
        renderPriorityChip();
    });
}

document.addEventListener('DOMContentLoaded', function () {
    var eventSelect = document.getElementById('event-select');
    var yearSelect  = document.getElementById('year-select');

    // Valeurs provisoires synchrones (scripts qui lisent window.selectedEvent
    // au chargement) : remplacees des que l'evenement du jour est connu.
    var prov = _cockpitReadSel();
    try {
        window.selectedEvent = (prov && prov.event) || localStorage.getItem('cockpit_event') || '';
        window.selectedYear = parseInt((prov && prov.year) || localStorage.getItem('cockpit_year'), 10) || new Date().getFullYear();
    } catch (e) {}
    if (yearSelect) {
        _cockpitPopulateYears(yearSelect, window.selectedYear);
        yearSelect.value = String(window.selectedYear);
    }

    var eventsP = fetch('/get_events')
        .then(function (r) { return r.json(); })
        .catch(function (error) {
            console.error('Erreur lors de la recuperation des evenements :', error);
            return [];
        });

    Promise.all([eventsP, fetchEventCurrent()]).then(function (res) {
        var names = (res[0] || []).map(function (it) { return it.nom; }).filter(Boolean);
        var cur = res[1];
        window._cockpitEventNames = names.slice();
        var sel = _cockpitResolveSelection(cur);
        if (eventSelect) _cockpitPopulateEvents(eventSelect, names, cur, sel.event);
        cockpitApplySelection(sel.event, sel.year, sel.manual, sel.at);
        if (document.getElementById('status-card')) {
            setInterval(_cockpitFollowCurrent, COCKPIT_FOLLOW_MS);
        }
    });

    // Choix de l'utilisateur : manuel, garde 12 h
    if (eventSelect) {
        eventSelect.addEventListener('change', function () {
            var yr = parseInt(window.selectedYear, 10);
            // SAISON d'une annee passee : on saute a l'annee courante
            var sy = _cockpitSaisonYear(window.cockpitEventCurrent);
            if (cockpitIsSaison(this.value) && yr < sy) yr = sy;
            cockpitApplySelection(this.value, yr, true);
        });
    }
    if (yearSelect) {
        yearSelect.addEventListener('change', function () {
            cockpitApplySelection(window.selectedEvent, this.value, true);
        });
    }
});

// ==========================================================================
// AUTO-LOAD: timeline + carte + header
// ==========================================================================

function loadCockpitData() {
    if (!window.selectedEvent || !window.selectedYear) return;

    // Update header
    updateHeaderInfo();

    // Clear existing timeline
    var eventList = document.getElementById("event-list");
    if (eventList) eventList.textContent = "";

    // Load timeline (parametrage then timetable)
    if (typeof fetchParametrage === "function" && typeof fetchTimetable === "function") {
        fetchParametrage().then(function () {
            var ttPromise = fetchTimetable();
            if (typeof updateGlobalCounter === "function") updateGlobalCounter();
            if (typeof loadAffluence === "function") loadAffluence();
            setTimeout(updateUpcomingEvents, 800);

            // Si la timeline est vide, basculer sur la carte
            if (ttPromise && ttPromise.then) {
                ttPromise.then(function () {
                    setTimeout(function () {
                        var eventList = document.getElementById("event-list");
                        var hasDays = eventList && eventList.querySelector(".timetable-date-section");
                        if (!hasDays && window.CockpitMapView && window.CockpitMapView.currentView() !== "map") {
                            window.CockpitMapView.switchView("map");
                        }
                    }, 300);
                });
            }
        }).catch(function () {});
    }

    // Pre-charger les donnees carte (meme si on est en timeline)
    if (window.CockpitMapView && window.CockpitMapView.preload) {
        window.CockpitMapView.preload();
    }

    // Load map markers if map view active
    if (window.CockpitMapView && window.CockpitMapView.currentView() === "map") {
        window.CockpitMapView.reload();
    }

    // Refresh main courante PCO
    if (typeof window.pcorgRefresh === "function") {
        window.pcorgRefresh();
    }

    // Update upcoming events + status after timeline loads
    setTimeout(updateUpcomingEvents, 1500);

    // Refresh bandeau + statut toutes les 30s
    if (window._upcomingTimer) clearInterval(window._upcomingTimer);
    window._upcomingTimer = setInterval(function () {
        updateUpcomingEvents();
        if (window._statusParamData) {
            computeAndRenderStatus(window._statusParamData);
        }
    }, 30000);

    // Update event status card
    updateEventStatus();
}

function updateHeaderInfo() {
    var nameEl = document.getElementById("header-event-name");
    var yearEl = document.getElementById("header-event-year");
    if (nameEl) nameEl.textContent = window.selectedEvent || "---";
    if (yearEl) yearEl.textContent = window.selectedYear || "";
}

// ==========================================================================
// EVENT STATUS CARD
// ==========================================================================

var _statusTimer = null;

function updateEventStatus() {
    if (!window.selectedEvent || !window.selectedYear) {
        renderStatus("no-event", "hourglass_empty", "Aucun evenement", "Selectionnez un evenement");
        return;
    }

    // SAISON : main courante permanente, ni montage ni demontage ni course ->
    // pas de cycle de vie a calculer. Seuls les jours de VISITES LIBRES
    // (globalHoraires.dates du parametrage SAISON, 02/10/2026) sont affiches.
    if (cockpitIsSaison(window.selectedEvent)) {
        window._statusParamData = null;
        window._saisonPublicDates = [];
        if (_statusTimer) { clearInterval(_statusTimer); _statusTimer = null; }
        renderSaisonStatus();
        var saisonKey = window.selectedEvent + "|" + window.selectedYear;
        fetch("/get_parametrage?event=" + encodeURIComponent(window.selectedEvent) + "&year=" + encodeURIComponent(window.selectedYear))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (saisonKey !== window.selectedEvent + "|" + window.selectedYear) return;
                var gh = (data && data.globalHoraires) || {};
                window._saisonPublicDates = Array.isArray(gh.dates) ? gh.dates : [];
                renderSaisonStatus();
            })
            .catch(function () {});
        _statusTimer = setInterval(renderSaisonStatus, 60000);
        return;
    }

    // Fetch parametrage for status data
    fetch("/get_parametrage?event=" + encodeURIComponent(window.selectedEvent) + "&year=" + encodeURIComponent(window.selectedYear))
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data || typeof data !== "object") {
                renderStatus("no-event", "error", "Pas de parametrage", "Aucune donnee pour cet evenement");
                return;
            }
            // Store for live updates
            window._statusParamData = data;
            computeAndRenderStatus(data);

            // Live update every 5s
            if (_statusTimer) clearInterval(_statusTimer);
            _statusTimer = setInterval(function () {
                if (window._statusParamData) {
                    computeAndRenderStatus(window._statusParamData);
                }
            }, 5000);
        })
        .catch(function () {
            renderStatus("no-event", "error", "Erreur", "Impossible de charger le parametrage");
        });
}

function shiftDateISO(iso, days) {
    var parts = iso.split("-");
    var d = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
    d.setDate(d.getDate() + days);
    return d.getFullYear() + "-" +
        String(d.getMonth() + 1).padStart(2, "0") + "-" +
        String(d.getDate()).padStart(2, "0");
}

// Calcule le segment public continu autour de todayISO :
// des jours adjacents sont considérés continus si l'un ferme à 23:59/24:00
// (ou is24h) et l'autre ouvre à 00:00 (ou is24h).
function getContinuousSegmentFor(todayISO, publicDates) {
    var byDate = {};
    for (var i = 0; i < publicDates.length; i++) {
        if (publicDates[i] && publicDates[i].date) {
            byDate[publicDates[i].date] = publicDates[i];
        }
    }
    if (!byDate[todayISO]) return null;
    var closesAtEOD = function (d) {
        return !!d.is24h || d.closeTime === "23:59" || d.closeTime === "24:00" || d.closeTime === "00:00";
    };
    var opensAtSOD = function (d) {
        return !!d.is24h || d.openTime === "00:00";
    };
    var startDate = todayISO;
    while (byDate[startDate] && opensAtSOD(byDate[startDate])) {
        var prevISO = shiftDateISO(startDate, -1);
        var prev = byDate[prevISO];
        if (!prev || !closesAtEOD(prev)) break;
        startDate = prevISO;
    }
    var endDate = todayISO;
    while (byDate[endDate] && closesAtEOD(byDate[endDate])) {
        var nextISO = shiftDateISO(endDate, 1);
        var next = byDate[nextISO];
        if (!next || !opensAtSOD(next)) break;
        endDate = nextISO;
    }
    var startDay = byDate[startDate];
    var endDay = byDate[endDate];
    return {
        startDate: startDate,
        startTime: startDay.is24h ? "00:00" : (startDay.openTime || "00:00"),
        endDate: endDate,
        endTime: endDay.is24h ? "23:59" : (endDay.closeTime || "23:59"),
        isSingleDay: startDate === endDate
    };
}

function _saisonHour(t) {
    // "10:00" -> "10h00"
    return String(t || "").replace(":", "h");
}

function renderSaisonStatus() {
    if (!cockpitIsSaison(window.selectedEvent)) return;
    var detail = "Saison " + (window.selectedYear || "");
    var cur = window.cockpitEventCurrent && window.cockpitEventCurrent.current;
    var epreuve = !!(cur && cur.kind === "epreuve");
    if (epreuve) detail = "Epreuve en cours : " + cur.event;
    // Jour de visites libres (jours publics du parametrage SAISON). Pendant
    // une epreuve active, c'est elle qui pilote le site : pas d'affichage.
    var now = (window.TimelineClock && typeof window.TimelineClock.get === "function")
        ? window.TimelineClock.get() : new Date();
    var todayISO = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0")
        + "-" + String(now.getDate()).padStart(2, "0");
    var dates = window._saisonPublicDates || [];
    var today = null;
    for (var i = 0; i < dates.length; i++) {
        if (dates[i] && String(dates[i].date || "").slice(0, 10) === todayISO) { today = dates[i]; break; }
    }
    if (today && !epreuve) {
        var hours = today.is24h ? "24h/24"
            : _saisonHour(today.openTime || "00:00") + "-" + _saisonHour(today.closeTime || "23:59");
        // visite_libre / visite_guidee : import GroundMaster, absent = autorise
        var libre = today.visite_libre !== false;
        var guidee = today.visite_guidee !== false;
        var lab = libre && guidee ? "Visites libres et guidees" : (libre ? "Visites libres" : "Visites guidees");
        renderStatus("saison", "museum", lab + " aujourd'hui " + hours, detail);
        return;
    }
    renderStatus("saison", "event_note", "Exploitation courante", detail);
}

function computeAndRenderStatus(paramData) {
    if (cockpitIsSaison(window.selectedEvent)) {
        renderSaisonStatus();
        return;
    }
    var gh = paramData.globalHoraires;
    if (!gh) {
        renderStatus("no-event", "info", "Pas de configuration", "Horaires non definis");
        return;
    }

    var now = (window.TimelineClock && typeof window.TimelineClock.get === "function")
        ? window.TimelineClock.get()
        : new Date();

    var todayISO = now.getFullYear() + "-" +
        String(now.getMonth() + 1).padStart(2, "0") + "-" +
        String(now.getDate()).padStart(2, "0");
    var nowMinutes = now.getHours() * 60 + now.getMinutes();

    // 1) Check public opening dates
    var publicDates = gh.dates || [];
    var todayPublic = null;
    for (var i = 0; i < publicDates.length; i++) {
        if (publicDates[i].date === todayISO) {
            todayPublic = publicDates[i];
            break;
        }
    }

    // Also check if we are in the overnight tail of yesterday's opening
    var yesterdayDate = new Date(now.getTime() - 86400000);
    var yesterdayISO = yesterdayDate.getFullYear() + "-" +
        String(yesterdayDate.getMonth() + 1).padStart(2, "0") + "-" +
        String(yesterdayDate.getDate()).padStart(2, "0");
    var yesterdayPublic = null;
    for (var j = 0; j < publicDates.length; j++) {
        if (publicDates[j].date === yesterdayISO) {
            yesterdayPublic = publicDates[j];
            break;
        }
    }

    // --- Helper: find next opening (date + openTime) from now ---
    function findNextOpening() {
        var future = publicDates
            .filter(function (d) { return d.date >= todayISO; })
            .sort(function (a, b) { return a.date.localeCompare(b.date); });
        for (var k = 0; k < future.length; k++) {
            var fd = future[k];
            var fOpen = parseTimeToMin(fd.openTime);
            if (fd.date === todayISO && fOpen !== null && fOpen <= nowMinutes) continue;
            return fd;
        }
        return null;
    }

    function minutesUntilOpening(targetDate) {
        if (!targetDate) return null;
        var tOpen = parseTimeToMin(targetDate.openTime);
        if (tOpen === null) return null;
        if (targetDate.date === todayISO) return tOpen - nowMinutes;
        // Future day: compute full delta
        var nowTs = now.getTime();
        var parts = targetDate.date.split("-");
        var targetTs = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10), Math.floor(tOpen / 60), tOpen % 60).getTime();
        return Math.round((targetTs - nowTs) / 60000);
    }

    // --- Check yesterday overnight tail ---
    if (yesterdayPublic && !yesterdayPublic.is24h) {
        var ydOpenMin = parseTimeToMin(yesterdayPublic.openTime);
        var ydCloseMin = parseTimeToMin(yesterdayPublic.closeTime);
        if (ydOpenMin !== null && ydCloseMin !== null && ydCloseMin < ydOpenMin) {
            if (nowMinutes < ydCloseMin) {
                var remaining = ydCloseMin - nowMinutes;
                var state = remaining <= 60 ? "closing-soon" : "open";
                renderStatus(state, "lock_open", "OUVERT AU PUBLIC", null, {
                    hours: yesterdayPublic.openTime + " \u2013 " + yesterdayPublic.closeTime,
                    countdown: "Fermeture dans " + formatMinutesDelta(remaining)
                });
                return;
            }
        }
    }

    // --- Today is a public day ---
    if (todayPublic) {
        if (todayPublic.is24h) {
            renderStatus("open", "lock_open", "OUVERT AU PUBLIC", "24h/24 aujourd'hui");
            return;
        }
        var openMin = parseTimeToMin(todayPublic.openTime);
        var closeMin = parseTimeToMin(todayPublic.closeTime);

        if (openMin !== null && closeMin !== null) {
            var isOvernight = closeMin < openMin;

            // Before opening today
            if (nowMinutes < openMin) {
                var untilOpen = openMin - nowMinutes;
                if (untilOpen <= 60) {
                    renderStatus("opening-soon", "schedule", "OUVERTURE IMMINENTE", null, {
                        hours: todayPublic.openTime,
                        countdown: "Dans " + formatMinutesDelta(untilOpen)
                    });
                } else {
                    renderStatus("ferme", "lock", "FERME AU PUBLIC", null, {
                        hours: "Ouverture " + todayPublic.openTime,
                        countdown: "Dans " + formatMinutesDelta(untilOpen)
                    });
                }
                return;
            }

            // Currently open
            if (nowMinutes >= openMin) {
                // Détection d'un segment public continu inter-jours (ex. sam 08:00 -> dim 18:00)
                var segment = getContinuousSegmentFor(todayISO, publicDates);
                var multiDaySegment = segment && !segment.isSingleDay;

                var remaining;
                if (multiDaySegment && segment.endDate !== todayISO) {
                    var eParts = segment.endDate.split("-");
                    var eCloseMin = parseTimeToMin(segment.endTime);
                    var closeTs = new Date(
                        parseInt(eParts[0], 10),
                        parseInt(eParts[1], 10) - 1,
                        parseInt(eParts[2], 10),
                        Math.floor(eCloseMin / 60),
                        eCloseMin % 60
                    ).getTime();
                    remaining = Math.round((closeTs - now.getTime()) / 60000);
                } else if (isOvernight) {
                    remaining = (1440 - nowMinutes) + closeMin;
                } else if (nowMinutes < closeMin) {
                    remaining = closeMin - nowMinutes;
                } else {
                    // After close same day — find next opening
                    var nextOp = findNextOpening();
                    if (nextOp) {
                        var untilNext = minutesUntilOpening(nextOp);
                        if (untilNext !== null && untilNext <= 60) {
                            renderStatus("opening-soon", "schedule", "OUVERTURE IMMINENTE", null, {
                                hours: nextOp.openTime + " (" + formatDateShort(nextOp.date) + ")",
                                countdown: "Dans " + formatMinutesDelta(untilNext)
                            });
                        } else {
                            renderStatus("ferme", "lock", "FERME AU PUBLIC", null, {
                                hours: "Prochaine ouverture",
                                countdown: nextOp.openTime + " \u2013 " + formatDateShort(nextOp.date)
                            });
                        }
                    } else {
                        renderStatus("ferme", "lock", "FERME AU PUBLIC",
                            "Fermeture depuis " + todayPublic.closeTime);
                    }
                    return;
                }

                // Open with remaining time
                var state = remaining <= 60 ? "closing-soon" : "open";
                var hOpenDate = multiDaySegment ? segment.startDate : todayISO;
                var hCloseDate = multiDaySegment ? segment.endDate : todayISO;
                var hOpenTime = multiDaySegment ? segment.startTime : todayPublic.openTime;
                var hCloseTime = multiDaySegment ? segment.endTime : todayPublic.closeTime;
                var hoursStr;
                if (hOpenDate === hCloseDate) {
                    hoursStr = hOpenTime + " \u2013 " + hCloseTime;
                } else {
                    var openLbl = (hOpenDate === todayISO) ? hOpenTime : formatDateShort(hOpenDate).split(" ")[0] + " " + hOpenTime;
                    var closeLbl = (hCloseDate === todayISO) ? hCloseTime : formatDateShort(hCloseDate).split(" ")[0] + " " + hCloseTime;
                    hoursStr = openLbl + " \u2013 " + closeLbl;
                }
                renderStatus(state, "lock_open", "OUVERT AU PUBLIC", null, {
                    hours: hoursStr,
                    countdown: "Fermeture dans " + formatMinutesDelta(remaining)
                });
                return;
            }
        }
    }

    // --- Not a public day: full lifecycle logic ---
    var montageStart = (gh.montage && gh.montage.start) ? gh.montage.start.slice(0, 10) : null;
    var montageEnd = (gh.montage && gh.montage.end) ? gh.montage.end.slice(0, 10) : null;
    var demontageStart = (gh.demontage && gh.demontage.start) ? gh.demontage.start.slice(0, 10) : null;
    var demontageEnd = (gh.demontage && gh.demontage.end) ? gh.demontage.end.slice(0, 10) : null;

    var sortedDates = publicDates.map(function (d) { return d.date; }).sort();
    var firstPublicDate = sortedDates.length ? sortedDates[0] : null;
    var lastPublicDate = sortedDates.length ? sortedDates[sortedDates.length - 1] : null;

    // Find the last public day's close time to know when demontage truly begins
    var lastPublicDay = null;
    if (lastPublicDate) {
        for (var lp = 0; lp < publicDates.length; lp++) {
            if (publicDates[lp].date === lastPublicDate) {
                lastPublicDay = publicDates[lp];
                break;
            }
        }
    }
    var lastPublicCloseMin = lastPublicDay ? parseTimeToMin(lastPublicDay.closeTime) : null;
    var isLastDayOvernight = false;
    if (lastPublicDay && lastPublicCloseMin !== null) {
        var lastPublicOpenMin = parseTimeToMin(lastPublicDay.openTime);
        if (lastPublicOpenMin !== null && lastPublicCloseMin < lastPublicOpenMin) {
            isLastDayOvernight = true;
        }
    }

    // Determine if we are past the last public day's closing
    var pastLastPublicClose = false;
    if (lastPublicDate) {
        if (todayISO > lastPublicDate) {
            if (isLastDayOvernight) {
                // Overnight: closing is on the day after lastPublicDate
                var dayAfterLast = new Date(new Date(lastPublicDate + "T12:00:00").getTime() + 86400000);
                var dayAfterLastISO = dayAfterLast.getFullYear() + "-" +
                    String(dayAfterLast.getMonth() + 1).padStart(2, "0") + "-" +
                    String(dayAfterLast.getDate()).padStart(2, "0");
                if (todayISO > dayAfterLastISO) {
                    pastLastPublicClose = true;
                } else if (todayISO === dayAfterLastISO && nowMinutes >= lastPublicCloseMin) {
                    pastLastPublicClose = true;
                }
            } else {
                pastLastPublicClose = true;
            }
        } else if (todayISO === lastPublicDate && lastPublicCloseMin !== null && !isLastDayOvernight && nowMinutes >= lastPublicCloseMin) {
            pastLastPublicClose = true;
        }
    }

    // 1) Before montage starts -> EVENEMENT A VENIR
    if (montageStart && todayISO < montageStart) {
        renderStatus("closed", "event_upcoming", "EVENEMENT A VENIR",
            "Debut montage le " + formatDateShort(montageStart));
        return;
    }

    // 2) During montage period (montage started, before first public date)
    if (montageStart && todayISO >= montageStart && firstPublicDate && todayISO < firstPublicDate) {
        var nextOp = findNextOpening();
        if (nextOp) {
            renderStatus("montage", "construction", "PERIODE DE MONTAGE", null, {
                hours: "Prochaine ouverture",
                countdown: (nextOp.openTime || "") + " \u2013 " + formatDateShort(nextOp.date)
            });
        } else {
            renderStatus("montage", "construction", "PERIODE DE MONTAGE",
                "Du " + formatDateShort(montageStart) + " au " + formatDateShort(montageEnd || firstPublicDate));
        }
        return;
    }

    // 3) Between public dates but not a public day
    if (firstPublicDate && lastPublicDate && todayISO >= firstPublicDate && !pastLastPublicClose) {
        var nextOp = findNextOpening();
        if (nextOp) {
            var untilNext = minutesUntilOpening(nextOp);
            if (untilNext !== null && untilNext <= 60) {
                renderStatus("opening-soon", "schedule", "OUVERTURE IMMINENTE", null, {
                    hours: nextOp.openTime + " (" + formatDateShort(nextOp.date) + ")",
                    countdown: "Dans " + formatMinutesDelta(untilNext)
                });
            } else {
                renderStatus("ferme", "lock", "FERME AU PUBLIC", null, {
                    hours: "Prochaine ouverture",
                    countdown: (nextOp.openTime || "") + " \u2013 " + formatDateShort(nextOp.date)
                });
            }
        } else {
            renderStatus("ferme", "lock", "FERME AU PUBLIC", "Aucune ouverture a venir");
        }
        return;
    }

    // 4) After last public close, during demontage period
    if (pastLastPublicClose && demontageStart && demontageEnd && todayISO <= demontageEnd) {
        renderStatus("demontage", "pallet", "PERIODE DE DEMONTAGE",
            "Du " + formatDateShort(demontageStart) + " au " + formatDateShort(demontageEnd));
        return;
    }

    // 5) After demontage end -> EVENEMENT TERMINE
    if (demontageEnd && todayISO > demontageEnd) {
        renderStatus("closed", "event_available", "EVENEMENT TERMINE",
            "Cloture le " + formatDateShort(demontageEnd));
        return;
    }

    // 6) After last public date but no demontage configured
    if (pastLastPublicClose) {
        renderStatus("closed", "event_available", "EVENEMENT TERMINE",
            "Cloture le " + formatDateShort(lastPublicDate));
        return;
    }

    // 7) No public dates but montage exists and we are in montage
    if (montageStart && montageEnd && !firstPublicDate && todayISO >= montageStart && todayISO <= montageEnd) {
        renderStatus("montage", "construction", "PERIODE DE MONTAGE",
            "Du " + formatDateShort(montageStart) + " au " + formatDateShort(montageEnd));
        return;
    }

    // 8) No public dates, before everything
    if (!firstPublicDate && montageStart && todayISO < montageStart) {
        renderStatus("closed", "event_upcoming", "EVENEMENT A VENIR",
            "Debut montage le " + formatDateShort(montageStart));
        return;
    }

    renderStatus("no-event", "info", "Pas de configuration", "Horaires non definis");
}

function renderStatus(state, icon, label, detail, extra) {
    var indicator = document.getElementById("status-indicator");
    var iconEl = document.getElementById("status-icon");
    var labelEl = document.getElementById("status-label");
    var detailEl = document.getElementById("status-detail");
    if (!indicator) return;

    indicator.className = "status-indicator status-" + state;
    if (iconEl) iconEl.textContent = icon;
    if (labelEl) labelEl.textContent = label;

    if (!detailEl) return;
    detailEl.textContent = "";

    if (extra && extra.hours && extra.countdown) {
        // Structured: big hours + countdown below
        var hoursSpan = document.createElement("span");
        hoursSpan.className = "status-hours";
        hoursSpan.textContent = extra.hours;

        var countSpan = document.createElement("span");
        countSpan.className = "status-countdown";
        countSpan.textContent = extra.countdown;

        detailEl.appendChild(hoursSpan);
        detailEl.appendChild(countSpan);
    } else {
        detailEl.textContent = detail || "";
    }
}

function parseTimeToMin(timeStr) {
    if (!timeStr) return null;
    var parts = timeStr.split(":");
    if (parts.length < 2) return null;
    return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
}

function formatMinutesDelta(min) {
    if (min < 60) return min + " min";
    var h = Math.floor(min / 60);
    var m = min % 60;
    if (m === 0) return h + " h";
    return h + " h " + m + " min";
}

function formatDateShort(iso) {
    if (!iso || iso.length < 10) return iso || "";
    var parts = iso.split("-");
    var dt = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
    var days = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];
    return days[dt.getDay()] + " " + parts[2] + "/" + parts[1];
}

function findNextPublicDate(dates, afterISO) {
    var future = dates.filter(function (d) { return d.date > afterISO; }).sort(function (a, b) { return a.date.localeCompare(b.date); });
    if (future.length) return formatDateShort(future[0].date);
    return "---";
}

// Le polling et l'affichage fullscreen sont dans alert_poller.js (inclus sur toutes les pages)
// _pushAlertHistory est expose sur window pour que alert_poller.js puisse l'appeler

// ---------- Historique d'alertes (widget droite) ----------
// Icone et couleur d'une alerte : une seule source, la definition (via
// window.CockpitAlerts, alert_poller.js). Deux tables divergentes vivaient
// ici et dans alert_poller.js ; celle-ci ignorait secours, securite, SOS et
// cameras, affiches en violet par defaut dans l'historique.
function _alertMeta(type, serverMeta) {
    if (window.CockpitAlerts && window.CockpitAlerts.meta) return window.CockpitAlerts.meta(type, serverMeta);
    var m = serverMeta || {};
    return { icon: m.icon || "info", color: m.color || "#6366f1", name: m.name || type };
}
var _alertTypeColors = {ACCIDENT: "#e53935", JAM: "#f59e0b", HAZARD: "#f97316", ROAD_CLOSED: "#8b5cf6"};
var _alertTypeLabels = {ACCIDENT: "accident", JAM: "ralentissement", HAZARD: "danger", ROAD_CLOSED: "route fermee"};

function _buildMapAction(pins) {
    return function() {
        window._allAlertPinsData = pins;
        if (window.CockpitMapView && window.CockpitMapView.switchView) {
            window.CockpitMapView.switchView('map');
            setTimeout(function() {
                document.dispatchEvent(new CustomEvent('showAllAlertPins'));
            }, 400);
        }
    };
}

function _renderAlertEntry(container, type, iconName, title, timeStr, message, onAction, dateStr, actionData, color, alertId, explainId) {
    color = color || _alertMeta(type).color;
    // Identifiant pour l'explication IA : l'id de l'alerte, sinon (anciennes
    // entrees d'historique sans alert_id) l'id de l'entree elle-meme.
    explainId = explainId || alertId;

    var entry = document.createElement("div");
    entry.className = "alert-history-entry";
    entry.setAttribute("data-type", type);
    if (alertId) entry.setAttribute("data-alert-id", alertId);
    entry.style.borderLeftColor = color;

    // --- Header row (toujours visible) ---
    var headerRow = document.createElement("div");
    headerRow.className = "alert-history-header";

    var ico = document.createElement("span");
    ico.className = "material-symbols-outlined";
    ico.style.cssText = "font-size:14px;color:" + color + ";";
    ico.textContent = iconName;

    var titleEl = document.createElement("span");
    titleEl.className = "alert-history-title";
    titleEl.textContent = title;

    // Preview: extract street/location from message
    var preview = "";
    if (message) {
        var dashIdx = message.indexOf(" \u2014 ");
        if (dashIdx < 0) dashIdx = message.indexOf(" - ");
        if (dashIdx >= 0) {
            preview = message.substring(dashIdx + 3).trim();
        } else {
            preview = message.length > 35 ? message.substring(0, 35) + "\u2026" : message;
        }
    } else if (timeStr) {
        preview = timeStr;
    }

    headerRow.appendChild(ico);
    headerRow.appendChild(titleEl);
    if (preview) {
        var previewEl = document.createElement("span");
        previewEl.className = "alert-history-preview";
        previewEl.textContent = preview;
        headerRow.appendChild(previewEl);
    }
    var timeEl = document.createElement("span");
    timeEl.className = "alert-history-time";
    timeEl.textContent = dateStr || "";
    headerRow.appendChild(timeEl);
    entry.appendChild(headerRow);

    // --- Detail section (visible au depliage) ---
    var detail = document.createElement("div");
    detail.className = "alert-history-detail";

    if (type === "traffic-cluster" && actionData && actionData.pins && actionData.pins.length) {
        // Ventilation par type avec pastilles colorees
        var typeCounts = {};
        var streetSet = {};
        actionData.pins.forEach(function(p) {
            var t = p.type || "UNKNOWN";
            typeCounts[t] = (typeCounts[t] || 0) + 1;
            if (p.street) streetSet[p.street] = true;
        });

        var grid = document.createElement("div");
        grid.className = "alert-detail-grid";
        Object.keys(typeCounts).forEach(function(t) {
            var item = document.createElement("div");
            item.className = "alert-detail-item";
            var dot = document.createElement("span");
            dot.className = "alert-detail-dot";
            dot.style.background = _alertTypeColors[t] || "#6366f1";
            item.appendChild(dot);
            var n = typeCounts[t];
            var lbl = _alertTypeLabels[t] || t;
            if (n > 1) lbl += "s";
            item.appendChild(document.createTextNode(n + " " + lbl));
            grid.appendChild(item);
        });
        detail.appendChild(grid);

        // Rues concernees
        var streets = Object.keys(streetSet);
        if (streets.length) {
            var streetsDiv = document.createElement("div");
            streetsDiv.className = "alert-detail-meta";
            var locIco = document.createElement("span");
            locIco.className = "material-symbols-outlined";
            locIco.textContent = "location_on";
            streetsDiv.appendChild(locIco);
            streetsDiv.appendChild(document.createTextNode(streets.slice(0, 4).join(", ")));
            if (streets.length > 4) {
                var more = document.createElement("span");
                more.className = "alert-detail-more";
                more.textContent = " +" + (streets.length - 4);
                streetsDiv.appendChild(more);
            }
            detail.appendChild(streetsDiv);
        }

        // Rayon / nb alertes
        if (timeStr) {
            var infoDiv = document.createElement("div");
            infoDiv.className = "alert-detail-meta";
            var radarIco = document.createElement("span");
            radarIco.className = "material-symbols-outlined";
            radarIco.textContent = "radar";
            infoDiv.appendChild(radarIco);
            infoDiv.appendChild(document.createTextNode(timeStr));
            detail.appendChild(infoDiv);
        }

    } else if (type === "opening" || type === "closing" || type === "opened" || type === "closed") {
        // Alertes site : heure + message
        if (timeStr) {
            var timeBig = document.createElement("div");
            timeBig.className = "alert-detail-time-big";
            var clockIco = document.createElement("span");
            clockIco.className = "material-symbols-outlined";
            clockIco.textContent = "schedule";
            timeBig.appendChild(clockIco);
            timeBig.appendChild(document.createTextNode(" " + timeStr));
            detail.appendChild(timeBig);
        }
        if (message) {
            var msgDiv = document.createElement("div");
            msgDiv.className = "alert-detail-msg";
            msgDiv.textContent = message;
            detail.appendChild(msgDiv);
        }

    } else {
        // Fallback generique
        if (message) {
            var fallbackMsg = document.createElement("div");
            fallbackMsg.className = "alert-detail-msg";
            fallbackMsg.textContent = message;
            detail.appendChild(fallbackMsg);
        }
    }

    // Bouton d'action ("Voir sur la carte", ou "Ouvrir la fiche" pour la main courante)
    if (onAction) {
        var isFiche = !!(actionData && actionData.pcorg_id);
        var btn = document.createElement("button");
        btn.className = "alert-history-action";
        btn.style.color = color;
        var btnIco = document.createElement("span");
        btnIco.className = "material-symbols-outlined";
        btnIco.textContent = isFiche ? "description" : "map";
        btn.appendChild(btnIco);
        btn.appendChild(document.createTextNode(isFiche ? " Ouvrir la fiche" : " Voir sur la carte"));
        btn.addEventListener("click", function(e) { e.stopPropagation(); onAction(); });
        detail.appendChild(btn);
    }

    // Bouton "Expliquer" (IA) retire de l'historique : cout par clic operateur.

    entry.appendChild(detail);

    // Toggle expand
    entry.addEventListener("click", function() {
        var wasExpanded = entry.classList.contains("expanded");
        var all = container.querySelectorAll(".alert-history-entry.expanded");
        for (var k = 0; k < all.length; k++) all[k].classList.remove("expanded");
        if (!wasExpanded) entry.classList.add("expanded");
    });

    return entry;
}

function _alertHistoryHas(container, alertId) {
    return !!(alertId && container.querySelector('.alert-history-entry[data-alert-id="' + String(alertId).replace(/"/g, "") + '"]'));
}

// Affichage seulement : l'historique en base est ecrit par le moteur
// (alert_engine.sync_alert_history), une entree par alerte, qu'un poste soit
// ouvert ou non. Avant, chaque poste postait sa propre copie.
window._pushAlertHistory = function _pushAlertHistory(type, iconName, title, timeStr, message, onAction, alertId, color) {
    var container = document.getElementById("widget-right-3-body");
    if (!container) return;
    if (_alertHistoryHas(container, alertId)) return;

    // Retirer le placeholder
    var placeholder = container.querySelector(".widget-placeholder");
    if (placeholder) placeholder.remove();

    var now = new Date();
    var dateStr = String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");

    // Extraire actionData depuis le callback (attache par alert_poller.js)
    var actionData = onAction && onAction._actionData ? onAction._actionData : null;

    var entry = _renderAlertEntry(container, type, iconName, title, timeStr, message, onAction, dateStr, actionData, color, alertId);
    container.insertBefore(entry, container.firstChild);

    while (container.children.length > 50) {
        container.removeChild(container.lastChild);
    }
    return entry;
};

// Charger l'historique depuis MongoDB au demarrage
function _loadAlertHistory() {
    var container = document.getElementById("widget-right-3-body");
    if (!container) return;

    fetch("/api/alert-history?limit=30")
        .then(function(r) { return r.json(); })
        .then(function(alerts) {
            if (!alerts || !alerts.length) return;
            var placeholder = container.querySelector(".widget-placeholder");
            if (placeholder) placeholder.remove();

            // alerts sont du plus recent au plus ancien, on les ajoute dans l'ordre inverse
            // pour que insertBefore mette le plus recent en haut
            for (var i = alerts.length - 1; i >= 0; i--) {
                var a = alerts[i];
                // Deja affichee par alert_poller (course entre les deux requetes)
                if (_alertHistoryHas(container, a.alert_id)) continue;
                var m = _alertMeta(a.type, a.meta);
                var d = new Date(a.createdAt);
                var dateStr = "";
                if (!isNaN(d.getTime())) {
                    var parts = new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(d);
                    var p = {};
                    parts.forEach(function(x) { p[x.type] = x.value; });
                    var nowParts = new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", day: "2-digit", month: "2-digit" }).formatToParts(new Date());
                    var np = {};
                    nowParts.forEach(function(x) { np[x.type] = x.value; });
                    var isToday = p.day === np.day && p.month === np.month;
                    dateStr = isToday
                        ? (p.hour || "") + ":" + (p.minute || "")
                        : (p.day || "") + "/" + (p.month || "") + " " + (p.hour || "") + ":" + (p.minute || "");
                }

                // Reconstruire le callback d'action depuis actionData
                var actionData = a.actionData || null;
                var onAction = null;
                if (actionData && actionData.pins && actionData.pins.length) {
                    onAction = _buildMapAction(actionData.pins);
                } else if (actionData && actionData.pcorg_id) {
                    onAction = (function(fid) {
                        return function() {
                            if (window.PcorgUI && window.PcorgUI.openFiche) window.PcorgUI.openFiche(fid);
                        };
                    })(actionData.pcorg_id);
                }

                var entry = _renderAlertEntry(container, a.type, m.icon, a.title || m.name, a.timeStr, a.message, onAction, dateStr, actionData, m.color, a.alert_id, a.alert_id || a._id);
                container.insertBefore(entry, container.firstChild);
            }
        })
        .catch(function() {});
}

document.addEventListener("DOMContentLoaded", _loadAlertHistory);

// ---------- Preferences alertes personnelles ----------
// Liste construite depuis les definitions que l'utilisateur recoit
// (/api/alert-definitions/mine, via window.CockpitAlerts). L'ancienne liste
// figee de 9 types servait de liste blanche : toucher une case coupait en
// silence toutes les autres alertes (secours, securite, flux, cameras...).
(function initAlertPrefsUI() {
    document.addEventListener("DOMContentLoaded", function() {
        var btn = document.getElementById("alert-prefs-btn");
        var dropdown = document.getElementById("alert-prefs-dropdown");
        if (!btn || !dropdown) return;

        btn.addEventListener("click", function(e) {
            e.stopPropagation();
            var isHidden = dropdown.hasAttribute("hidden");
            if (isHidden) {
                _renderAlertPrefs(dropdown);
                dropdown.removeAttribute("hidden");
                // Position fixed sous le bouton
                var rect = btn.getBoundingClientRect();
                dropdown.style.top = (rect.bottom + 4) + "px";
                dropdown.style.right = (window.innerWidth - rect.right) + "px";
            } else {
                dropdown.setAttribute("hidden", "");
            }
        });

        document.addEventListener("click", function() {
            dropdown.setAttribute("hidden", "");
        });
        dropdown.addEventListener("click", function(e) { e.stopPropagation(); });
    });
})();

function _renderAlertPrefs(dropdown) {
    dropdown.textContent = "";
    var CA = window.CockpitAlerts;
    if (!CA) return;
    var loading = document.createElement("div");
    loading.className = "alert-pref-note";
    loading.textContent = "Chargement...";
    dropdown.appendChild(loading);

    CA.definitions(function(defs) {
        dropdown.textContent = "";
        if (!defs.length) {
            var empty = document.createElement("div");
            empty.className = "alert-pref-note";
            empty.textContent = "Aucune alerte ne vous est destinee.";
            dropdown.appendChild(empty);
            return;
        }
        defs.forEach(function(d) {
            var critical = d.display_mode === "critical";
            var label = document.createElement("label");
            label.className = "alert-pref-item" + (critical ? " is-locked" : "");
            var cb = document.createElement("input");
            cb.type = "checkbox";
            cb.checked = critical || !CA.isMuted(d.slug, d);
            cb.disabled = critical;
            cb.addEventListener("change", function() { CA.setMuted(d.slug, !cb.checked); });
            var ico = document.createElement("span");
            ico.className = "material-symbols-outlined alert-pref-icon";
            ico.style.color = d.color || "#6366f1";
            ico.textContent = d.icon || "notifications";
            label.appendChild(cb);
            label.appendChild(ico);
            label.appendChild(document.createTextNode(" " + (d.name || d.slug)));
            if (critical) label.title = "Alerte critique : toujours affichee";
            dropdown.appendChild(label);
        });
        var note = document.createElement("div");
        note.className = "alert-pref-note";
        note.textContent = "Reglage propre a ce poste. Les alertes critiques ne peuvent pas etre coupees.";
        dropdown.appendChild(note);
    });
}

// showCriticalAlert est defini dans alert_poller.js (window.showCriticalAlert)

var DAY_NAMES_SHORT = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];

function formatShortDate(isoDate) {
    if (!isoDate || isoDate.length < 10) return "";
    var parts = isoDate.split("-");
    var dt = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
    return DAY_NAMES_SHORT[dt.getDay()] + " " + parts[2] + "/" + parts[1];
}

function updateUpcomingEvents() {
    var container = document.getElementById("header-upcoming");
    if (!container) return;

    var now = (window.TimelineClock && typeof window.TimelineClock.get === "function")
        ? window.TimelineClock.get()
        : new Date();
    var nowMin = now.getHours() * 60 + now.getMinutes();
    var todayISO = now.getFullYear() + "-" +
        String(now.getMonth() + 1).padStart(2, "0") + "-" +
        String(now.getDate()).padStart(2, "0");

    var upcoming = [];

    var sections = document.querySelectorAll(".timetable-date-section");
    sections.forEach(function (sec) {
        var secDate = sec.dataset.date;
        if (!secDate) return;

        var cards = sec.querySelectorAll(".event-item");
        cards.forEach(function (card) {
            var minute = parseInt(card.getAttribute("data-minute") || "99999", 10);
            if (!isFinite(minute)) return;

            var isFuture = false;
            if (secDate === todayISO && minute > nowMin) isFuture = true;
            if (secDate > todayISO) isFuture = true;
            if (!isFuture) return;

            var titleEl = card.querySelector(".event-title h5");
            var timeEl = card.querySelector(".time-info");
            if (!titleEl) return;

            upcoming.push({
                name: titleEl.textContent.trim(),
                time: timeEl ? timeEl.textContent.trim() : "",
                date: secDate,
                sortKey: secDate + "-" + String(minute).padStart(5, "0"),
                cardEl: card
            });
        });
    });

    upcoming.sort(function (a, b) { return a.sortKey.localeCompare(b.sortKey); });

    // Take first 8
    var next = upcoming.slice(0, 8);

    container.textContent = "";

    if (!next.length) {
        container.classList.add("no-scroll");
        var empty = document.createElement("span");
        empty.className = "header-upcoming-empty";
        var ico = document.createElement("span");
        ico.className = "material-symbols-outlined";
        ico.style.fontSize = "16px";
        ico.textContent = "event_busy";
        empty.appendChild(ico);
        empty.appendChild(document.createTextNode(" Aucun evenement a venir"));
        container.appendChild(empty);
        return;
    }

    container.classList.remove("no-scroll");

    // Build cards
    function buildCards(items) {
        var frag = document.createDocumentFragment();
        items.forEach(function (ev, i) {
            if (i > 0) {
                var dot = document.createElement("span");
                dot.className = "header-upcoming-dot";
                frag.appendChild(dot);
            }

            var card = document.createElement("div");
            card.className = "header-upcoming-card";

            var timeSpan = document.createElement("span");
            timeSpan.className = "header-upcoming-time";
            timeSpan.textContent = ev.time || "\u2014";

            // Show date if not today
            if (ev.date !== todayISO) {
                var dateSpan = document.createElement("span");
                dateSpan.className = "header-upcoming-date";
                dateSpan.textContent = formatShortDate(ev.date);
                card.appendChild(dateSpan);
            }

            var nameSpan = document.createElement("span");
            nameSpan.className = "header-upcoming-name";
            nameSpan.textContent = ev.name;

            card.appendChild(timeSpan);
            card.appendChild(nameSpan);
            card.style.cursor = "pointer";
            card.addEventListener("click", function () {
                var srcCard = ev.cardEl;
                if (!srcCard) return;
                // Scroller vers la carte dans la timeline
                srcCard.scrollIntoView({ behavior: "smooth", block: "center" });
                // Flash visuel
                srcCard.classList.add("highlight-flash");
                setTimeout(function () { srcCard.classList.remove("highlight-flash"); }, 1500);
                // Ouvrir le drawer si c'est un item (pas un cluster)
                if (srcCard.__itemData && typeof window.openEventDrawer === "function") {
                    window.openEventDrawer(ev.date, srcCard.__itemData);
                }
            });
            frag.appendChild(card);
        });
        return frag;
    }

    // First set of cards
    container.appendChild(buildCards(next));

    // If enough items, duplicate for seamless infinite scroll
    if (next.length >= 4) {
        var spacer = document.createElement("span");
        spacer.className = "header-upcoming-dot";
        container.appendChild(spacer);
        container.appendChild(buildCards(next));
    } else {
        // Not enough to scroll — center them
        container.classList.add("no-scroll");
    }
}

// Drawer evenement et modale d'ajout/edition : voir static/js/timeline.js
// (openEventDrawer, openTimetableModal). Une ancienne copie vivait ici et
// doublait les ecouteurs des boutons Modifier / Dupliquer / Supprimer.

// showToast is now provided by toast.js
// Legacy alias for any code still calling showDynamicFlashMessage
function showDynamicFlashMessage(message, category, duration) {
    showToast(category || "success", message, duration || 3500);
}

/////////////////////////////////////////////////////////////////////////////////////////////////////
// NAVBAR (safe listeners)
/////////////////////////////////////////////////////////////////////////////////////////////////////

// Portes / Parkings / Statistiques : voir static/js/sidebar.js. Ils y sont
// branches pour toutes les pages, en relisant l'evenement et l'annee dans
// localStorage a defaut de selecteur. Les rebrancher ici ouvrirait deux
// onglets par clic sur les pages qui chargent les deux fichiers.
