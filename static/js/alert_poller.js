/**
 * alert_poller.js - Polling et affichage des alertes fullscreen.
 * Script autonome, sans dependance a main.js.
 * Inclus sur TOUTES les pages de l'application.
 *
 * Logique : au premier poll (chargement de page), les alertes de moins
 * de 5 min sont affichees en fullscreen, les autres vont dans l'historique.
 * Les polls suivants affichent en fullscreen toute nouvelle alerte.
 * Les alertes simultanees sont mises en file d'attente (une a la fois).
 */
(function() {
    "use strict";

    var POLL_INTERVAL = 10000;
    var GRACE_PERIOD_MS = 5 * 60 * 1000;
    var MAX_SEEN_IDS = 500;
    var _seenAlertIds = {};
    var _seenAlertCount = 0;
    var _firstPollDone = false;
    var _consecutiveErrors = 0;

    // --- File d'attente d'alertes ---
    var _alertQueue = [];
    var _alertOverlay = null;

    var ICON_MAP = {
        opening: "door_open", opened: "lock_open",
        closing: "door_front", closed: "lock",
        "traffic-cluster": "emergency",
        "anpr-watchlist": "local_police",
        "meteo-vent": "air",
        "meteo-pluie": "umbrella",
        "checkpoint-reassign": "swap_horiz"
    };
    var TITLE_MAP = {
        opening: "OUVERTURE IMMINENTE", opened: "SITE OUVERT",
        closing: "FERMETURE IMMINENTE", closed: "SITE FERME",
        "traffic-cluster": "ALERTE TRAFIC",
        "anpr-watchlist": "PLAQUE SURVEILLEE DETECTEE",
        "meteo-vent": "ALERTE VENT",
        "meteo-pluie": "ALERTE PLUIE",
        "checkpoint-reassign": "CHANGEMENT AFFECTATION CHECKPOINT"
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

    // --- Formatage date/heure ---
    function fmtAlertDateTime(isoStr) {
        if (!isoStr) return "";
        try {
            var d = new Date(isoStr);
            if (isNaN(d.getTime())) return "";
            var day = String(d.getDate()).padStart(2, "0");
            var month = String(d.getMonth() + 1).padStart(2, "0");
            var hours = String(d.getHours()).padStart(2, "0");
            var mins = String(d.getMinutes()).padStart(2, "0");
            return day + "/" + month + " a " + hours + ":" + mins;
        } catch(e) { return ""; }
    }

    // --- File d'attente : empiler et afficher une par une ---
    function enqueueAlert(type, triggeredAt, message, onView) {
        // Historiser si la fonction existe (page index avec widget alertes)
        var timeStr = fmtAlertDateTime(triggeredAt);
        if (typeof window._pushAlertHistory === "function") {
            window._pushAlertHistory(type, ICON_MAP[type] || "info", TITLE_MAP[type] || type, timeStr, message, onView);
        }

        if (isAlertMuted(type)) return;

        _alertQueue.push({ type: type, triggeredAt: triggeredAt, message: message, onView: onView });

        // Si pas d'overlay active, afficher la premiere
        if (!_alertOverlay) {
            _showNextAlert();
        } else {
            // Mettre a jour le compteur sur l'overlay existante
            _updateCounter();
        }
    }

    function _dismissCurrent(callback) {
        if (_alertOverlay) {
            _alertOverlay.style.opacity = "0";
            var ov = _alertOverlay;
            _alertOverlay = null;
            setTimeout(function() {
                if (ov.parentNode) ov.parentNode.removeChild(ov);
                if (callback) callback();
                // Afficher la suivante apres un court delai
                setTimeout(function() { _showNextAlert(); }, 150);
            }, 300);
        }
    }

    function _updateCounter() {
        if (!_alertOverlay) return;
        var badge = _alertOverlay.querySelector(".critical-alert-counter");
        if (_alertQueue.length > 0 && badge) {
            badge.textContent = _alertQueue.length + " autre" + (_alertQueue.length > 1 ? "s" : "");
            badge.style.display = "";
        } else if (badge) {
            badge.style.display = "none";
        }
    }

    function _showNextAlert() {
        if (_alertQueue.length === 0) return;

        var item = _alertQueue.shift();
        var type = item.type;
        var timeStr = fmtAlertDateTime(item.triggeredAt);

        var overlay = document.createElement("div");
        overlay.className = "critical-alert-overlay";
        _alertOverlay = overlay;

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

        // Date et heure
        var timeEl = document.createElement("div");
        timeEl.className = "critical-alert-time";
        timeEl.textContent = timeStr;

        var sub = document.createElement("div");
        sub.className = "critical-alert-sub";
        sub.textContent = item.message;

        // Compteur d'alertes restantes
        var counter = document.createElement("div");
        counter.className = "critical-alert-counter";
        if (_alertQueue.length > 0) {
            counter.textContent = _alertQueue.length + " autre" + (_alertQueue.length > 1 ? "s" : "");
        } else {
            counter.style.display = "none";
        }

        var btnRow = document.createElement("div");
        btnRow.className = "critical-alert-btns";

        if (item.onView) {
            var btnIgnore = document.createElement("button");
            btnIgnore.className = "critical-alert-btn critical-alert-btn-secondary";
            btnIgnore.textContent = "Ignorer";
            btnIgnore.addEventListener("click", function() { _dismissCurrent(null); });
            btnRow.appendChild(btnIgnore);

            var btnView = document.createElement("button");
            btnView.className = "critical-alert-btn";
            var viewLabel = "Voir sur la carte";
            if (type === "anpr-watchlist") viewLabel = "Voir sur LAPI";
            else if (type === "checkpoint-reassign") viewLabel = "Voir Controle acces";
            btnView.textContent = viewLabel;
            btnView.addEventListener("click", function() {
                var cb = item.onView;
                _dismissCurrent(cb);
            });
            btnRow.appendChild(btnView);
        } else {
            var btn = document.createElement("button");
            btn.className = "critical-alert-btn";
            btn.textContent = "Compris";
            btn.addEventListener("click", function() { _dismissCurrent(null); });
            btnRow.appendChild(btn);
        }

        body.appendChild(timeEl);
        body.appendChild(sub);
        body.appendChild(counter);
        body.appendChild(btnRow);
        box.appendChild(header);
        box.appendChild(body);
        overlay.appendChild(box);
        document.body.appendChild(overlay);

        var focusBtn = overlay.querySelector(".critical-alert-btn:last-child");
        if (focusBtn) setTimeout(function() { focusBtn.focus(); }, 100);
    }

    // Exposer globalement
    window.showCriticalAlert = enqueueAlert;

    // --- Construction du callback "Voir" ---
    function _buildOnView(slug, a) {
        var fn = null;
        if (slug === "traffic-cluster" && a.actionData && a.actionData.pins) {
            fn = function() {
                window._allAlertPinsData = a.actionData.pins;
                if (window.CockpitMapView && window.CockpitMapView.switchView) {
                    window.CockpitMapView.switchView("map");
                    setTimeout(function() {
                        document.dispatchEvent(new CustomEvent("showAllAlertPins"));
                    }, 400);
                }
            };
            fn._actionData = a.actionData;
        }
        if (slug === "anpr-watchlist" && a.actionData && a.actionData.plate) {
            fn = function() {
                window.open("/anpr?plate=" + encodeURIComponent(a.actionData.plate), "_blank");
            };
            fn._actionData = a.actionData;
        }
        if (slug === "checkpoint-reassign") {
            fn = function() {
                window.open("/live-controle", "_blank");
            };
            fn._actionData = a.actionData || {};
        }
        return fn;
    }

    // --- Purge memoire des IDs vus ---
    function _markSeen(id) {
        if (_seenAlertIds[id]) return;
        _seenAlertIds[id] = true;
        _seenAlertCount++;
        if (_seenAlertCount > MAX_SEEN_IDS) {
            // Purger la moitie la plus ancienne
            var keys = Object.keys(_seenAlertIds);
            var toRemove = Math.floor(keys.length / 2);
            for (var i = 0; i < toRemove; i++) {
                delete _seenAlertIds[keys[i]];
            }
            _seenAlertCount = Object.keys(_seenAlertIds).length;
        }
    }

    // --- Polling ---
    function pollActiveAlerts() {
        fetch("/api/active-alerts")
            .then(function(r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function(alerts) {
                _consecutiveErrors = 0;
                if (!Array.isArray(alerts)) return;

                if (!_firstPollDone) {
                    // Premier poll : alertes < 5 min -> fullscreen, les autres -> historique seulement
                    _firstPollDone = true;
                    var now = Date.now();
                    var recentAlerts = [];
                    alerts.forEach(function(a) {
                        _markSeen(a._id);
                        var age = a.triggeredAt ? (now - new Date(a.triggeredAt).getTime()) : Infinity;
                        if (age <= GRACE_PERIOD_MS) {
                            recentAlerts.push(a);
                        } else if (typeof window._pushAlertHistory === "function") {
                            var slug = a.definition_slug || "";
                            window._pushAlertHistory(slug, ICON_MAP[slug] || "info", TITLE_MAP[slug] || slug, fmtAlertDateTime(a.triggeredAt), a.message || "", null);
                        }
                    });
                    // Afficher en fullscreen les alertes recentes (< 5 min)
                    recentAlerts.forEach(function(a) {
                        var slug = a.definition_slug || "";
                        var onView = _buildOnView(slug, a);
                        enqueueAlert(slug, a.triggeredAt || "", a.message || "", onView);
                    });
                    return;
                }

                // Polls suivants : afficher en fullscreen uniquement les nouvelles
                alerts.forEach(function(a) {
                    if (_seenAlertIds[a._id]) return;
                    _markSeen(a._id);
                    var slug = a.definition_slug || "";
                    var onView = _buildOnView(slug, a);
                    enqueueAlert(slug, a.triggeredAt || "", a.message || "", onView);
                });
            })
            .catch(function(err) {
                _consecutiveErrors++;
                if (_consecutiveErrors >= 3) {
                    console.warn("[alert_poller] Polling alertes en echec (" + _consecutiveErrors + " erreurs consecutives)", err);
                }
            });
    }

    document.addEventListener("DOMContentLoaded", function() {
        pollActiveAlerts();
        setInterval(pollActiveAlerts, POLL_INTERVAL);
    });
})();
