/**
 * alert_poller.js - Polling et affichage des alertes fullscreen.
 * Script autonome, sans dependance a main.js.
 * Inclus sur TOUTES les pages de l'application.
 */
(function() {
    "use strict";

    var POLL_INTERVAL = 10000;
    var _seenAlertIds = {};

    var ICON_MAP = {
        opening: "door_open", opened: "lock_open",
        closing: "door_front", closed: "lock",
        "traffic-cluster": "emergency",
        "anpr-watchlist": "local_police"
    };
    var TITLE_MAP = {
        opening: "OUVERTURE IMMINENTE", opened: "SITE OUVERT",
        closing: "FERMETURE IMMINENTE", closed: "SITE FERME",
        "traffic-cluster": "ALERTE TRAFIC",
        "anpr-watchlist": "PLAQUE SURVEILLEE DETECTEE"
    };

    // --- Preferences alertes (localStorage) ---
    function _getAlertPrefs() {
        try {
            var stored = localStorage.getItem("cockpit-alert-prefs");
            if (stored) return JSON.parse(stored);
        } catch(e) {}
        return null;
    }

    function isAlertMuted(type) {
        var prefs = _getAlertPrefs();
        if (!prefs) return false;
        return prefs.indexOf(type) < 0;
    }

    // --- Affichage fullscreen ---
    function showFullscreenAlert(type, timeStr, message, onView) {
        // Historiser si la fonction existe (page index avec widget alertes)
        if (typeof window._pushAlertHistory === "function") {
            window._pushAlertHistory(type, ICON_MAP[type] || "info", TITLE_MAP[type] || type, timeStr, message, onView);
        }

        if (isAlertMuted(type)) return;

        var overlay = document.createElement("div");
        overlay.className = "critical-alert-overlay";

        var box = document.createElement("div");
        box.className = "critical-alert-box alert-" + type;

        var header = document.createElement("div");
        header.className = "critical-alert-header";

        var icon = document.createElement("span");
        icon.className = "material-symbols-outlined critical-alert-icon";
        icon.textContent = ICON_MAP[type] || "info";

        var title = document.createElement("div");
        title.className = "critical-alert-title";
        title.textContent = TITLE_MAP[type] || type.toUpperCase();

        header.appendChild(icon);
        header.appendChild(title);

        var body = document.createElement("div");
        body.className = "critical-alert-body";

        var timeEl = document.createElement("div");
        timeEl.className = "critical-alert-time";
        timeEl.textContent = timeStr;

        var sub = document.createElement("div");
        sub.className = "critical-alert-sub";
        sub.textContent = message;

        var btnRow = document.createElement("div");
        btnRow.className = "critical-alert-btns";

        if (onView) {
            var btnIgnore = document.createElement("button");
            btnIgnore.className = "critical-alert-btn critical-alert-btn-secondary";
            btnIgnore.textContent = "Ignorer";
            btnIgnore.addEventListener("click", function() {
                overlay.style.opacity = "0";
                setTimeout(function() { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }, 300);
            });
            btnRow.appendChild(btnIgnore);

            var btnView = document.createElement("button");
            btnView.className = "critical-alert-btn";
            btnView.textContent = type === "anpr-watchlist" ? "Voir sur LAPI" : "Voir sur la carte";
            btnView.addEventListener("click", function() {
                overlay.style.opacity = "0";
                setTimeout(function() { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }, 300);
                onView();
            });
            btnRow.appendChild(btnView);
        } else {
            var btn = document.createElement("button");
            btn.className = "critical-alert-btn";
            btn.textContent = "Compris";
            btn.addEventListener("click", function() {
                overlay.style.opacity = "0";
                setTimeout(function() { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }, 300);
            });
            btnRow.appendChild(btn);
        }

        body.appendChild(timeEl);
        body.appendChild(sub);
        body.appendChild(btnRow);
        box.appendChild(header);
        box.appendChild(body);
        overlay.appendChild(box);
        document.body.appendChild(overlay);

        var focusBtn = overlay.querySelector(".critical-alert-btn:last-child");
        if (focusBtn) setTimeout(function() { focusBtn.focus(); }, 100);
    }

    // Exposer globalement pour que main.js (index) puisse aussi l'utiliser
    window.showCriticalAlert = showFullscreenAlert;

    // --- Polling ---
    function pollActiveAlerts() {
        fetch("/api/active-alerts")
            .then(function(r) { return r.json(); })
            .then(function(alerts) {
                if (!Array.isArray(alerts)) return;
                alerts.forEach(function(a) {
                    if (_seenAlertIds[a._id]) return;
                    _seenAlertIds[a._id] = true;
                    var slug = a.definition_slug || "";
                    var onView = null;
                    // Cluster trafic : bouton "Voir sur la carte"
                    if (slug === "traffic-cluster" && a.actionData && a.actionData.pins) {
                        onView = function() {
                            window._allAlertPinsData = a.actionData.pins;
                            if (window.CockpitMapView && window.CockpitMapView.switchView) {
                                window.CockpitMapView.switchView("map");
                                setTimeout(function() {
                                    document.dispatchEvent(new CustomEvent("showAllAlertPins"));
                                }, 400);
                            }
                        };
                    }
                    // ANPR watchlist : bouton "Voir sur LAPI"
                    if (slug === "anpr-watchlist" && a.actionData && a.actionData.plate) {
                        onView = function() {
                            window.open("/anpr?plate=" + encodeURIComponent(a.actionData.plate), "_blank");
                        };
                    }
                    showFullscreenAlert(slug, a.timeStr || "", a.message || "", onView);
                });
            })
            .catch(function() {});
    }

    document.addEventListener("DOMContentLoaded", function() {
        pollActiveAlerts();
        setInterval(pollActiveAlerts, POLL_INTERVAL);
    });
})();
