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

    // Restaurer les IDs vus depuis sessionStorage (survit aux changements de page)
    try {
        var stored = sessionStorage.getItem("cockpit-seen-alerts");
        if (stored) {
            _seenAlertIds = JSON.parse(stored);
            _seenAlertCount = Object.keys(_seenAlertIds).length;
        }
    } catch(e) {}
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
        "meteo-pluie-imminente": "rainy",
        "checkpoint-reassign": "swap_horiz",
        "checkpoint-error-burst": "error",
        "pcorg-securite-ua": "shield",
        "pcorg-secours-ua": "local_hospital",
        "field_sos": "sos",
        "field-sos": "sos"
    };
    var TITLE_MAP = {
        opening: "OUVERTURE IMMINENTE", opened: "SITE OUVERT",
        closing: "FERMETURE IMMINENTE", closed: "SITE FERME",
        "traffic-cluster": "ALERTE TRAFIC",
        "anpr-watchlist": "PLAQUE SURVEILLEE DETECTEE",
        "meteo-vent": "ALERTE VENT",
        "meteo-pluie": "ALERTE PLUIE",
        "meteo-pluie-imminente": "PLUIE IMMINENTE",
        "checkpoint-error-burst": "RAFALE ERREURS CHECKPOINT",
        "checkpoint-reassign": "CHANGEMENT AFFECTATION CHECKPOINT",
        "pcorg-securite-ua": "ALERTE S\u00c9CURIT\u00c9",
        "pcorg-secours-ua": "ALERTE SECOURS",
        "field_sos": "SOS TABLETTE",
        "field-sos": "SOS TABLETTE"
    };

    // Labels urgence par type de categorie
    var URGENCY_LABELS_ALERT = {
        SECOURS:  { EU: "D\u00e9tresse vitale", UA: "Urgence absolue", UR: "Urgence relative", IMP: "Impliqu\u00e9 m\u00e9dical" },
        SECURITE: { EU: "Danger imm\u00e9diat", UA: "Incident grave", UR: "Incident en cours", IMP: "T\u00e9moin / impliqu\u00e9" },
        MIXTE:    { EU: "Urgence extr\u00eame", UA: "Urgence prioritaire", UR: "Situation stable", IMP: "Impliqu\u00e9" }
    };
    var URGENCY_ENGAGE = {
        EU: "Engagement imm\u00e9diat toutes ressources",
        UA: "Engagement prioritaire",
        UR: "Engagement planifi\u00e9 selon ressources disponibles",
        IMP: "Suivi en main courante, aucun engagement d'urgence"
    };
    function _urgencyType(cat) {
        if (cat === "PCO.Secours") return "SECOURS";
        if (cat === "PCO.Securite") return "SECURITE";
        return "MIXTE";
    }
    function _urgencyLabel(cat, level) {
        var t = _urgencyType(cat);
        return (URGENCY_LABELS_ALERT[t] || URGENCY_LABELS_ALERT.MIXTE)[level] || level;
    }

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
            var opts = { timeZone: "Europe/Paris", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false };
            var parts = new Intl.DateTimeFormat("fr-FR", opts).formatToParts(d);
            var p = {};
            parts.forEach(function(x) { p[x.type] = x.value; });
            var nowParts = new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", day: "2-digit", month: "2-digit" }).formatToParts(new Date());
            var np = {};
            nowParts.forEach(function(x) { np[x.type] = x.value; });
            var isToday = p.day === np.day && p.month === np.month;
            return isToday
                ? (p.hour || "00") + ":" + (p.minute || "00")
                : (p.day || "00") + "/" + (p.month || "00") + " a " + (p.hour || "00") + ":" + (p.minute || "00");
        } catch(e) { return ""; }
    }

    // --- File d'attente : empiler et afficher une par une ---
    function enqueueAlert(type, triggeredAt, message, onView, actionData) {
        // Historiser si la fonction existe (page index avec widget alertes)
        var timeStr = fmtAlertDateTime(triggeredAt);
        if (typeof window._pushAlertHistory === "function") {
            window._pushAlertHistory(type, ICON_MAP[type] || "info", TITLE_MAP[type] || type, timeStr, message, onView);
        }

        if (isAlertMuted(type)) return;

        _alertQueue.push({ type: type, triggeredAt: triggeredAt, message: message, onView: onView, actionData: actionData || {}, _mongoId: (actionData && actionData._mongoId) || null });

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

    // --- Alarm sound via Web Audio API ---
    var _sosAlarmTimer = null;
    function _playSosAlarm() {
        _stopSosAlarm();
        var playOnce = function() {
            try {
                var ctx = new (window.AudioContext || window.webkitAudioContext)();
                var t = ctx.currentTime;
                // Two-tone siren pattern
                var osc = ctx.createOscillator();
                var gain = ctx.createGain();
                osc.type = "square";
                osc.frequency.setValueAtTime(880, t);
                osc.frequency.setValueAtTime(660, t + 0.25);
                osc.frequency.setValueAtTime(880, t + 0.5);
                osc.frequency.setValueAtTime(660, t + 0.75);
                osc.frequency.setValueAtTime(880, t + 1.0);
                osc.frequency.setValueAtTime(660, t + 1.25);
                gain.gain.setValueAtTime(0.6, t);
                gain.gain.linearRampToValueAtTime(0, t + 1.5);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start(t);
                osc.stop(t + 1.5);
            } catch(e) {}
        };
        playOnce();
        // Repeat every 2s for 10s
        var count = 0;
        _sosAlarmTimer = setInterval(function() {
            count++;
            if (count >= 5) { _stopSosAlarm(); return; }
            playOnce();
        }, 2000);
    }
    function _stopSosAlarm() {
        if (_sosAlarmTimer) { clearInterval(_sosAlarmTimer); _sosAlarmTimer = null; }
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
        var actionDataPre = item.actionData || {};
        if ((type === "field_sos" || type === "field-sos") && actionDataPre.device_name) {
            title.textContent = "SOS - " + actionDataPre.device_name;
        } else {
            title.textContent = TITLE_MAP[type] || type.toUpperCase();
        }

        header.appendChild(icon);
        header.appendChild(title);

        var body = document.createElement("div");
        body.className = "critical-alert-body";

        // Contenu specifique PCO : enrichi avec urgence + engagement + operateur
        var isPco = type.indexOf("pcorg-") === 0;
        var isFieldSos = (type === "field_sos" || type === "field-sos");
        var actionData = item.actionData || {};

        if (isFieldSos) {
            // Play alarm sound for SOS
            _playSosAlarm();

            // Gros message
            var bigMsg = document.createElement("div");
            bigMsg.className = "critical-alert-sos-big";
            bigMsg.textContent = "Demande d assistance immediate";
            body.appendChild(bigMsg);

            // Bloc info : position / batterie / heure
            var info = document.createElement("div");
            info.className = "critical-alert-sos-info";
            var hasPos = (typeof actionData.lat === "number" && typeof actionData.lng === "number");
            var posTxt = hasPos
                ? actionData.lat.toFixed(5) + ", " + actionData.lng.toFixed(5)
                : "Position inconnue";
            var batTxt = (typeof actionData.battery === "number") ? (Math.round(actionData.battery) + "%") : "?";
            info.innerHTML =
                "<div><span class='material-symbols-outlined'>place</span> " + posTxt + "</div>" +
                "<div><span class='material-symbols-outlined'>battery_5_bar</span> " + batTxt + "</div>" +
                "<div><span class='material-symbols-outlined'>schedule</span> " + timeStr + "</div>";
            body.appendChild(info);
        } else if (isPco && actionData.niveau_urgence && actionData.category) {
            var urgLabel = _urgencyLabel(actionData.category, actionData.niveau_urgence);
            var engageDesc = URGENCY_ENGAGE[actionData.niveau_urgence] || "";

            // Badge urgence
            var urgBadge = document.createElement("div");
            urgBadge.className = "critical-alert-urgency-badge critical-alert-urgency-" + actionData.niveau_urgence;
            urgBadge.textContent = actionData.niveau_urgence + " \u2014 " + urgLabel;
            body.appendChild(urgBadge);

            // Description intervention
            var pcoSub = document.createElement("div");
            pcoSub.className = "critical-alert-message";
            var msgText = (item.message || "").split(" \u2014 ")[0];
            pcoSub.textContent = msgText;
            body.appendChild(pcoSub);

            // Engagement
            if (engageDesc) {
                var engEl = document.createElement("div");
                engEl.className = "critical-alert-engage";
                engEl.textContent = engageDesc;
                body.appendChild(engEl);
            }

            // Operateur + heure en petite ligne
            var metaLine = document.createElement("div");
            metaLine.className = "critical-alert-meta";
            var opParts = (item.message || "").split(" \u2014 ");
            var opName = "";
            for (var pi = 0; pi < opParts.length; pi++) {
                if (opParts[pi].indexOf("Operateur") === 0) {
                    opName = opParts[pi].replace("Operateur : ", "");
                }
            }
            var metaText = timeStr;
            if (opName) metaText += " \u2014 " + opName;
            var userGroups = window.__userGroups;
            if (userGroups && userGroups.length) {
                var groupNames = userGroups.map(function(g) { return g.name; }).join(", ");
                if (groupNames && opName) metaText += " (" + groupNames + ")";
            }
            metaLine.textContent = metaText;
            body.appendChild(metaLine);
        } else {
            // Message standard (non PCO)
            var stdSub = document.createElement("div");
            stdSub.className = "critical-alert-message";
            stdSub.textContent = item.message;
            body.appendChild(stdSub);

            var stdTime = document.createElement("div");
            stdTime.className = "critical-alert-time";
            stdTime.textContent = timeStr;
            body.appendChild(stdTime);
        }

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

        // Store alert _id for acknowledge
        var alertMongoId = item._mongoId || null;

        if (item.onView) {
            if (isFieldSos) {
                // SOS : "Acquitter" + "Ouvrir la fiche"
                var btnAck = document.createElement("button");
                btnAck.className = "critical-alert-btn critical-alert-btn-secondary";
                btnAck.textContent = "Acquitter";
                btnAck.addEventListener("click", function() {
                    _stopSosAlarm();
                    if (alertMongoId) _acknowledgeAlert(alertMongoId);
                    _dismissCurrent(null);
                });
                btnRow.appendChild(btnAck);

                var btnView = document.createElement("button");
                btnView.className = "critical-alert-btn";
                btnView.textContent = "Ouvrir la fiche";
                btnView.addEventListener("click", function() {
                    _stopSosAlarm();
                    if (alertMongoId) _acknowledgeAlert(alertMongoId);
                    var cb = item.onView;
                    _dismissCurrent(cb);
                });
                btnRow.appendChild(btnView);
            } else {
                var btnIgnore = document.createElement("button");
                btnIgnore.className = "critical-alert-btn critical-alert-btn-secondary";
                btnIgnore.textContent = "Ignorer";
                btnIgnore.addEventListener("click", function() { _dismissCurrent(null); });
                btnRow.appendChild(btnIgnore);

                var btnView2 = document.createElement("button");
                btnView2.className = "critical-alert-btn";
                var viewLabel = "Voir sur la carte";
                if (type === "anpr-watchlist") viewLabel = "Voir sur LAPI";
                else if (type === "checkpoint-reassign") viewLabel = "Voir Controle acces";
                else if (isPco) viewLabel = "Ouvrir la fiche";
                btnView2.textContent = viewLabel;
                btnView2.addEventListener("click", function() {
                    var cb = item.onView;
                    _dismissCurrent(cb);
                });
                btnRow.appendChild(btnView2);
            }
        } else {
            var btn = document.createElement("button");
            btn.className = "critical-alert-btn";
            btn.textContent = "Compris";
            btn.addEventListener("click", function() {
                if (isFieldSos) _stopSosAlarm();
                _dismissCurrent(null);
            });
            btnRow.appendChild(btn);
        }

        body.appendChild(counter);
        body.appendChild(btnRow);
        box.appendChild(header);
        box.appendChild(body);
        overlay.appendChild(box);
        document.body.appendChild(overlay);

        var focusBtn = overlay.querySelector(".critical-alert-btn:last-child");
        if (focusBtn) setTimeout(function() { focusBtn.focus(); }, 100);
    }

    function _acknowledgeAlert(alertId) {
        fetch("/api/active-alerts/" + encodeURIComponent(alertId) + "/acknowledge", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
        }).catch(function(e) {
            console.warn("[alert_poller] acknowledge failed:", e);
        });
    }

    // Exposer globalement
    window.showCriticalAlert = enqueueAlert; // (type, triggeredAt, message, onView, actionData)

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
        if (slug.indexOf("pcorg-") === 0 && a.actionData && a.actionData.pcorg_id) {
            fn = function() {
                if (window.PcorgUI && window.PcorgUI.openFiche) {
                    window.PcorgUI.openFiche(a.actionData.pcorg_id);
                }
            };
            fn._actionData = a.actionData;
        }
        if ((slug === "field_sos" || slug === "field-sos") && a.actionData) {
            var ad = a.actionData;
            fn = function() {
                // Priorite : ouvrir la fiche PCO auto-creee si dispo, sinon centrer la carte
                if (ad.pcorg_id && window.PcorgUI && window.PcorgUI.openFiche) {
                    window.PcorgUI.openFiche(ad.pcorg_id);
                    return;
                }
                if (typeof ad.lat === "number" && typeof ad.lng === "number") {
                    if (window.CockpitMapView && window.CockpitMapView.switchView) {
                        window.CockpitMapView.switchView("map");
                    }
                    setTimeout(function() {
                        if (window.CockpitMapView && window.CockpitMapView.flyTo) {
                            window.CockpitMapView.flyTo(ad.lat, ad.lng, 19);
                        } else if (window.CockpitMapView && window.CockpitMapView.getMap) {
                            var m = window.CockpitMapView.getMap();
                            if (m) m.setView([ad.lat, ad.lng], 19);
                        }
                    }, 400);
                }
            };
            fn._actionData = a.actionData;
        }
        return fn;
    }

    // --- Purge memoire des IDs vus ---
    function _persistSeen() {
        try { sessionStorage.setItem("cockpit-seen-alerts", JSON.stringify(_seenAlertIds)); } catch(e) {}
    }

    function _markSeen(id) {
        if (_seenAlertIds[id]) return;
        _seenAlertIds[id] = true;
        _seenAlertCount++;
        _persistSeen();
        if (_seenAlertCount > MAX_SEEN_IDS) {
            // Purger la moitie la plus ancienne
            var keys = Object.keys(_seenAlertIds);
            var toRemove = Math.floor(keys.length / 2);
            for (var i = 0; i < toRemove; i++) {
                delete _seenAlertIds[keys[i]];
            }
            _seenAlertCount = Object.keys(_seenAlertIds).length;
            _persistSeen();
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
                        var ad = a.actionData || {};
                        ad._mongoId = a._id;
                        enqueueAlert(slug, a.triggeredAt || "", a.message || "", onView, ad);
                    });
                    return;
                }

                // Polls suivants : afficher en fullscreen uniquement les nouvelles
                alerts.forEach(function(a) {
                    if (_seenAlertIds[a._id]) return;
                    _markSeen(a._id);
                    var slug = a.definition_slug || "";
                    var onView = _buildOnView(slug, a);
                    var ad = a.actionData || {};
                    ad._mongoId = a._id;
                    enqueueAlert(slug, a.triggeredAt || "", a.message || "", onView, ad);
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
