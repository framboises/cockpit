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

function apiPost(url, payload){
    return fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]')?.content) || ''
        },
        body: JSON.stringify(payload)
    }).then(r => r.json());
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
    const sidebar = document.getElementById("sidebar");
    const toggleBtn = document.getElementById("sidebarToggle");
    const hamburgerButton = document.getElementById("hamburger-button");

    // Restaurer l'etat sidebar depuis localStorage (defaut: collapsed)
    if (sidebar) {
        var stored = localStorage.getItem("sidebar-collapsed");
        if (stored === null || stored === "true") {
            sidebar.classList.add("collapsed");
        }
    }

    function toggleSidebar() {
        if (!sidebar) return;
        sidebar.classList.toggle("collapsed");
        localStorage.setItem("sidebar-collapsed", sidebar.classList.contains("collapsed"));
    }

    if (toggleBtn) toggleBtn.addEventListener("click", toggleSidebar);
    if (hamburgerButton) hamburgerButton.addEventListener("click", toggleSidebar);

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

document.addEventListener('DOMContentLoaded', function () {
    const eventSelect = document.getElementById('event-select');
    const yearSelect  = document.getElementById('year-select');

    // Restore saved selections from localStorage
    const savedEvent = localStorage.getItem('cockpit_event');
    const savedYear  = localStorage.getItem('cockpit_year');

    // Populate event select
    fetch('/get_events')
    .then(response => response.json())
    .then(eventsData => {
        if (!eventSelect) return;

        let matched = false;
        eventsData.forEach(item => {
            const option = document.createElement('option');
            option.value = item.nom;
            option.textContent = item.nom;
            eventSelect.appendChild(option);

            // Priority: localStorage > "24H AUTOS" > first item
            if (savedEvent && item.nom === savedEvent) {
                option.selected = true;
                window.selectedEvent = item.nom;
                matched = true;
            }
        });

        if (!matched) {
            // Fallback to "24H AUTOS" if no saved preference
            const fallback = Array.from(eventSelect.options).find(o => o.value === "24H AUTOS");
            if (fallback) {
                fallback.selected = true;
                window.selectedEvent = fallback.value;
            } else if (eventSelect.options.length > 0) {
                eventSelect.selectedIndex = 0;
                window.selectedEvent = eventSelect.options[0].value;
            }
        }

        localStorage.setItem('cockpit_event', window.selectedEvent);
    })
    .catch(error => console.error('Erreur lors de la recuperation des evenements :', error));

    // Populate year select
    const currentYear = new Date().getFullYear();
    const startYear   = 2024;
    if (yearSelect) {
        const preferredYear = savedYear ? parseInt(savedYear, 10) : currentYear;

        for (let year = startYear; year <= currentYear + 1; year++) {
            const option = document.createElement('option');
            option.value = year;
            option.textContent = year;
            if (year === preferredYear) {
                option.selected = true;
                window.selectedYear = year;
            }
            yearSelect.appendChild(option);
        }

        // If saved year was out of range, default to current
        if (!window.selectedYear) {
            yearSelect.value = currentYear;
            window.selectedYear = currentYear;
        }

        localStorage.setItem('cockpit_year', window.selectedYear);
    }

    // Change listeners — persist + auto-reload
    if (eventSelect) {
        eventSelect.addEventListener('change', function () {
            window.selectedEvent = this.value;
            localStorage.setItem('cockpit_event', this.value);
            loadCockpitData();
        });
    }
    if (yearSelect) {
        yearSelect.addEventListener('change', function () {
            window.selectedYear = parseInt(this.value, 10);
            localStorage.setItem('cockpit_year', this.value);
            loadCockpitData();
        });
    }

    // Auto-load on startup after selects are populated
    // Small delay to let event select fetch complete
    setTimeout(loadCockpitData, 600);
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

function computeAndRenderStatus(paramData) {
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
                var remaining;
                if (isOvernight) {
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
                renderStatus(state, "lock_open", "OUVERT AU PUBLIC", null, {
                    hours: todayPublic.openTime + " \u2013 " + todayPublic.closeTime,
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
        renderStatus("demontage", "demolition", "PERIODE DE DEMONTAGE",
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
var _alertIconMap = { opening: "door_open", opened: "lock_open", closing: "door_front", closed: "lock", "traffic-cluster": "emergency", "anpr-watchlist": "local_police" };
var _alertColorMap = {
    "opening": "#f59e0b", "closing": "#f59e0b",
    "opened": "#22c55e", "closed": "#ef4444",
    "traffic-cluster": "#f97316",
    "anpr-watchlist": "#dc2626",
    "meteo": "#42a5f5"
};
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

function _renderAlertEntry(container, type, iconName, title, timeStr, message, onAction, dateStr, actionData) {
    var color = _alertColorMap[type] || "#6366f1";

    var entry = document.createElement("div");
    entry.className = "alert-history-entry";
    entry.setAttribute("data-type", type);
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

    // Bouton "Voir sur la carte"
    if (onAction) {
        var btn = document.createElement("button");
        btn.className = "alert-history-action";
        btn.style.color = color;
        var btnIco = document.createElement("span");
        btnIco.className = "material-symbols-outlined";
        btnIco.textContent = "map";
        btn.appendChild(btnIco);
        btn.appendChild(document.createTextNode(" Voir sur la carte"));
        btn.addEventListener("click", function(e) { e.stopPropagation(); onAction(); });
        detail.appendChild(btn);
    }

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

window._pushAlertHistory = function _pushAlertHistory(type, iconName, title, timeStr, message, onAction) {
    var container = document.getElementById("widget-right-3-body");
    if (!container) return;

    // Retirer le placeholder
    var placeholder = container.querySelector(".widget-placeholder");
    if (placeholder) placeholder.remove();

    var now = new Date();
    var dateStr = String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");

    // Extraire actionData depuis le callback (attache par alerte.js)
    var actionData = onAction && onAction._actionData ? onAction._actionData : null;

    var entry = _renderAlertEntry(container, type, iconName, title, timeStr, message, onAction, dateStr, actionData);
    container.insertBefore(entry, container.firstChild);

    while (container.children.length > 50) {
        container.removeChild(container.lastChild);
    }

    // Persister en base (avec actionData pour reconstruire le bouton carte au reload)
    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    var headers = {"Content-Type": "application/json"};
    if (csrfMeta) headers["X-CSRFToken"] = csrfMeta.getAttribute("content");
    fetch("/api/alert-history", {
        method: "POST",
        headers: headers,
        body: JSON.stringify({
            type: type,
            title: title,
            timeStr: timeStr,
            message: message,
            hasAction: !!onAction,
            actionData: actionData
        })
    }).catch(function() {});

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
                var iconName = _alertIconMap[a.type] || "info";
                var d = new Date(a.createdAt);
                var dateStr = isNaN(d.getTime()) ? "" :
                    String(d.getDate()).padStart(2, "0") + "/" +
                    String(d.getMonth() + 1).padStart(2, "0") + " " +
                    String(d.getHours()).padStart(2, "0") + ":" +
                    String(d.getMinutes()).padStart(2, "0");

                // Reconstruire le callback "Voir sur la carte" depuis actionData
                var actionData = a.actionData || null;
                var onAction = null;
                if (a.hasAction && actionData && actionData.pins && actionData.pins.length) {
                    onAction = _buildMapAction(actionData.pins);
                }

                var entry = _renderAlertEntry(container, a.type, iconName, a.title, a.timeStr, a.message, onAction, dateStr, actionData);
                container.insertBefore(entry, container.firstChild);
            }
        })
        .catch(function() {});
}

document.addEventListener("DOMContentLoaded", _loadAlertHistory);

// ---------- Preferences alertes personnelles ----------
var ALERT_PREF_TYPES = [
    {id: "opening", label: "Ouverture imminente"},
    {id: "opened", label: "Site ouvert"},
    {id: "closing", label: "Fermeture imminente"},
    {id: "closed", label: "Site ferme"},
    {id: "traffic-cluster", label: "Alerte trafic (cluster)"},
    {id: "anpr-watchlist", label: "Plaque surveillee (LAPI)"}
];

function _getAlertPrefs() {
    try {
        var stored = localStorage.getItem("cockpit-alert-prefs");
        if (stored) return JSON.parse(stored);
    } catch(e) {}
    return null; // null = tout actif
}

function _setAlertPrefs(prefs) {
    localStorage.setItem("cockpit-alert-prefs", JSON.stringify(prefs));
}

function isAlertMuted(type) {
    var prefs = _getAlertPrefs();
    if (!prefs) return false; // pas de prefs = tout actif
    return prefs.indexOf(type) < 0; // pas dans la liste = mute
}

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
    var prefs = _getAlertPrefs();
    // Si null, tout est actif -> initialiser avec tout
    var activeIds = prefs || ALERT_PREF_TYPES.map(function(t) { return t.id; });

    ALERT_PREF_TYPES.forEach(function(t) {
        var label = document.createElement("label");
        label.className = "alert-pref-item";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = activeIds.indexOf(t.id) >= 0;
        cb.addEventListener("change", function() {
            var current = _getAlertPrefs() || ALERT_PREF_TYPES.map(function(x) { return x.id; });
            if (cb.checked) {
                if (current.indexOf(t.id) < 0) current.push(t.id);
            } else {
                current = current.filter(function(x) { return x !== t.id; });
            }
            _setAlertPrefs(current);
        });
        label.appendChild(cb);
        label.appendChild(document.createTextNode(" " + t.label));
        dropdown.appendChild(label);
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

// ==================== DRAWER EVENEMENT ====================
(function(){
  const drawer    = document.getElementById('event-drawer');
  const overlay   = document.getElementById('event-drawer-overlay');
  const closeBtn  = document.getElementById('drawer-close');
  const bodyEl    = document.getElementById('event-drawer-body');
  const titleEl   = document.getElementById('drawer-title');

  window.openEventDrawer = function(eventItem) {
    if (!drawer || !bodyEl) return;

    titleEl.textContent = eventItem?.activity || 'Evenement';

    const fields = [
      { label: 'Date', value: eventItem?.date },
      { label: 'Heure', value: formatTimeRange(eventItem?.start, eventItem?.end) },
      { label: 'Categorie', value: eventItem?.category },
      { label: 'Lieu', value: eventItem?.place },
      { label: 'Departement', value: eventItem?.department },
      { label: 'Duree', value: eventItem?.duration },
      { label: 'Remarque', value: eventItem?.remark }
    ].filter(f => f.value && String(f.value).trim().length);

    // Safe DOM construction — no innerHTML
    bodyEl.textContent = '';
    if (fields.length === 0) {
      const empty = document.createElement('div');
      empty.style.color = 'var(--muted)';
      empty.textContent = 'Aucune information.';
      bodyEl.appendChild(empty);
    } else {
      fields.forEach(f => {
        const fieldDiv = document.createElement('div');
        fieldDiv.className = 'field';

        const labelDiv = document.createElement('div');
        labelDiv.className = 'label';
        labelDiv.textContent = f.label;

        const valueDiv = document.createElement('div');
        valueDiv.className = 'value';
        valueDiv.textContent = String(f.value);

        fieldDiv.appendChild(labelDiv);
        fieldDiv.appendChild(valueDiv);
        bodyEl.appendChild(fieldDiv);
      });
    }

    drawer.dataset.itemId = eventItem?._id || '';
    drawer.dataset.itemRaw = JSON.stringify(eventItem || {});

    drawer.classList.add('open');
    overlay?.classList.add('show');
    drawer.setAttribute('aria-hidden', 'false');
  };

  window.closeEventDrawer = function() {
    drawer?.classList.remove('open');
    overlay?.classList.remove('show');
    drawer?.setAttribute('aria-hidden', 'true');
  };

  function formatTimeRange(start, end){
    if (!start && !end) return '';
    const s = (start && start !== 'TBC') ? start : '\u2014';
    const e = (end && end !== 'TBC') ? end   : '\u2014';
    return `${s} - ${e}`;
  }
  function escapeHtml(s){
    return s.replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  overlay?.addEventListener('click', window.closeEventDrawer);
  closeBtn?.addEventListener('click', window.closeEventDrawer);

  // Edit button
  document.getElementById('drawer-edit')?.addEventListener('click', () => {
    try {
        const drawer = document.getElementById('event-drawer');
        const item = JSON.parse(drawer.dataset.itemRaw || '{}');

        const addEventModal = document.getElementById('addEventModal');
        const addEventForm  = document.getElementById('addEventForm');

        const fDate   = document.getElementById('event-date');
        const fStart  = document.getElementById('start-time');
        const fEnd    = document.getElementById('end-time');
        const fDur    = document.getElementById('duration');
        const fCat    = document.getElementById('category');
        const fAct    = document.getElementById('activity');
        const fPlace  = document.getElementById('place');
        const fDept   = document.getElementById('department');
        const fRemark = document.getElementById('remark');

        if (!addEventModal || !addEventForm) {
            showToast("error", "Modale d'edition introuvable.");
            return;
        }

        window.formMode = 'edit';
        window.editingItemId = item?._id || null;

        let hiddenId = addEventForm.querySelector('input[name="_id"]');
        if (!hiddenId) {
            hiddenId = document.createElement('input');
            hiddenId.type = 'hidden';
            hiddenId.name = '_id';
            addEventForm.appendChild(hiddenId);
        }
        hiddenId.value = window.editingItemId || '';

        if (fDate)   fDate.value   = (item.date || '').slice(0,10);
        if (fStart)  fStart.value  = item.start   || '';
        if (fEnd)    fEnd.value    = item.end     || '';
        if (fDur)    fDur.value    = item.duration|| '';
        if (fCat)    fCat.value    = item.category|| '';
        if (fAct)    fAct.value    = item.activity|| '';
        if (fPlace)  fPlace.value  = item.place   || '';
        if (fDept)   fDept.value   = item.department || '';
        if (fRemark) fRemark.value = item.remark  || '';

        const title = addEventModal.querySelector('h3');
        if (title) title.textContent = "Modifier un evenement";

        addEventModal.style.display = 'block';
        setTimeout(() => addEventModal.classList.add('show'), 10);

        if (window.closeEventDrawer) window.closeEventDrawer();

    } catch(e){
        console.error(e);
        showToast("error", "Erreur a l'ouverture de l'edition");
    }
  });

  // Duplicate button
  document.getElementById('drawer-duplicate')?.addEventListener('click', async () => {
    try {
        const drawer = document.getElementById('event-drawer');
        const item = JSON.parse(drawer.dataset.itemRaw || '{}');

        if (!item?._id || !item?.date) {
            showToast("error", "Evenement incomplet (id/date manquant).");
            return;
        }

        const defaultDate = item.date;
        const target = await showPromptToast("Dupliquer a la date (YYYY-MM-DD) :", { defaultValue: defaultDate, inputType: "date" });
        if (target === null) return;

        const re = /^\d{4}-\d{2}-\d{2}$/;
        if (!re.test(target)) {
            showToast("error", "Format de date invalide (YYYY-MM-DD).");
            return;
        }

        const { event, year } = getCurrentEventYear();
        if (!event || !year) {
            showToast("error", "Selectionnez un evenement et une annee.");
            return;
        }

        const payload = { event, year, date: item.date, _id: item._id, target_date: target };
        const res = await apiPost('/duplicate_timetable_event', payload);
        if (res?.success) {
            showToast("success", "Evenement duplique.");
            const eventList = document.getElementById("event-list");
            if (eventList) eventList.textContent = "";
            if (window.fetchTimetable) window.fetchTimetable();
        } else {
            showToast("error", res?.message || "Erreur lors de la duplication.");
        }
    } catch (e) {
        console.error(e);
        showToast("error", "Erreur inattendue lors de la duplication.");
    }
  });

  // Delete button
  document.getElementById('drawer-delete')?.addEventListener('click', async () => {
    try {
        const drawer = document.getElementById('event-drawer');
        const item = JSON.parse(drawer.dataset.itemRaw || '{}');

        if (!item?._id || !item?.date) {
            showToast("error", "Evenement incomplet (id/date manquant).");
            return;
        }

        const ok = await showConfirmToast("Confirmer la suppression de cet evenement ?", { type: "error", okLabel: "Supprimer", cancelLabel: "Annuler" });
        if (!ok) return;

        const { event, year } = getCurrentEventYear();
        if (!event || !year) {
            showToast("error", "Selectionnez un evenement et une annee.");
            return;
        }

        const payload = { event, year, date: item.date, _id: item._id };
        const res = await apiPost('/delete_timetable_event', payload);
        if (res?.success) {
            showToast("success", "Evenement supprime.");
            if (window.closeEventDrawer) window.closeEventDrawer();
            const eventList = document.getElementById("event-list");
            if (eventList) eventList.textContent = "";
            if (window.fetchTimetable) window.fetchTimetable();
        } else {
            showToast("error", res?.message || "Erreur lors de la suppression.");
        }
    } catch (e) {
        console.error(e);
        showToast("error", "Erreur inattendue lors de la suppression.");
    }
  });

})();

// showToast is now provided by toast.js
// Legacy alias for any code still calling showDynamicFlashMessage
function showDynamicFlashMessage(message, category, duration) {
    showToast(category || "success", message, duration || 3500);
}

/////////////////////////////////////////////////////////////////////////////////////////////////////
// NAVBAR (safe listeners)
/////////////////////////////////////////////////////////////////////////////////////////////////////

on("stats-page-button", "click", function(){
    if (!window.selectedEvent || !window.selectedYear) {
        showToast("error", "Veuillez selectionner un evenement et une annee");
        return;
    }
    const url = "/general_stat?event=" + encodeURIComponent(window.selectedEvent) + "&year=" + encodeURIComponent(window.selectedYear);
    window.open(url, "_blank");
});

on("parkings-page-button", "click", function(){
    if (!window.selectedEvent || !window.selectedYear) {
        showToast("error", "Veuillez selectionner un evenement et une annee");
        return;
    }
    const url = "/terrains?event=" + encodeURIComponent(window.selectedEvent) + "&year=" + encodeURIComponent(window.selectedYear);
    window.open(url, "_blank");
});

on("doors-page-button", "click", function(){
    if (!window.selectedEvent || !window.selectedYear) {
        showToast("error", "Veuillez selectionner un evenement et une annee");
        return;
    }
    const url = "/doors?event=" + encodeURIComponent(window.selectedEvent) + "&year=" + encodeURIComponent(window.selectedYear);
    window.open(url, "_blank");
});
