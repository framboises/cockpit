/**
 * alert_poller.js - Polling et affichage des alertes de la centrale.
 * Script autonome, sans dependance a main.js.
 *
 * Logique : au premier poll (chargement de page), les alertes de moins
 * de 5 min sont affichees, les autres vont dans l'historique.
 * Les polls suivants affichent toute nouvelle alerte.
 * Les alertes plein ecran simultanees sont en file d'attente (une a la fois).
 *
 * Le rendu (couleur, icone, titre, mise en forme, mode d'affichage) est
 * pilote par la DEFINITION de l'alerte (champ `meta` de /api/active-alerts),
 * plus par des tables codees en dur : une alerte creee depuis l'admin avec
 * un slug libre (ex. main-courante-flux) s'affichait sans en-tete ni bouton
 * visible. Les tables ci-dessous ne servent plus que de repli.
 *
 * Modes d'affichage (definition.display_mode) :
 *   banner     : notification discrete + historique, jamais de plein ecran
 *   fullscreen : plein ecran a acquitter sur chaque poste
 *   critical   : plein ecran + son + prise en compte partagee ; jamais muet
 *
 * API exposee : window.CockpitAlerts (meta, definitions, isMuted, setMuted,
 * preview, explainWidget) et window.showCriticalAlert (compat).
 * Sur la page d'admin, window.__alertPollerPreviewOnly = true desactive le
 * polling : le script ne sert qu'a l'apercu.
 */
(function() {
    "use strict";

    var PREVIEW_ONLY = !!window.__alertPollerPreviewOnly;
    var POLL_INTERVAL = 5000;
    var GRACE_PERIOD_MS = 5 * 60 * 1000;
    var MAX_SEEN_IDS = 500;
    var SEEN_KEY = "cockpit-seen-alerts";
    var _seenAlertIds = {};
    var _seenAlertCount = 0;
    var _firstPollDone = false;
    var _pollInFlight = false;

    // Deux memoires distinctes :
    // - "vu" (sessionStorage, par onglet) : l'alerte a deja ete mise en file
    //   dans CET onglet ;
    // - "traite" (localStorage, tout le poste) : un operateur a clique sur un
    //   bouton de l'alerte (Compris / Ignorer / Ouvrir). Au chargement d'une
    //   page, une alerte recente deja traitee sur ce poste n'est plus remise
    //   en plein ecran. Avant, chaque changement de page ou nouvel onglet
    //   re-affichait les alertes de moins de 5 min : un SOS deja pris en
    //   compte "revenait" plusieurs fois.
    var DISMISSED_KEY = "cockpit-dismissed-alerts";
    var DISMISSED_TTL_MS = 24 * 3600 * 1000;
    try {
        var stored = sessionStorage.getItem(SEEN_KEY);
        if (stored) {
            _seenAlertIds = JSON.parse(stored) || {};
            _seenAlertCount = Object.keys(_seenAlertIds).length;
        }
    } catch(e) {}

    function _loadDismissed() {
        try {
            var raw = localStorage.getItem(DISMISSED_KEY);
            return raw ? (JSON.parse(raw) || {}) : {};
        } catch(e) { return {}; }
    }
    function _isDismissed(id) {
        return !!(id && _loadDismissed()[id]);
    }
    function _markDismissed(id) {
        if (!id) return;
        var d = _loadDismissed();
        var now = Date.now();
        Object.keys(d).forEach(function(k) { if (now - d[k] > DISMISSED_TTL_MS) delete d[k]; });
        d[id] = now;
        try { localStorage.setItem(DISMISSED_KEY, JSON.stringify(d)); } catch(e) {}
    }
    var _consecutiveErrors = 0;

    // --- File d'attente d'alertes ---
    var _alertQueue = [];
    var _alertOverlay = null;
    var _currentItem = null;

    // --- Replis (alertes sans definition, anciennes alertes) ---
    var ICON_MAP = {
        opening: "door_open", opened: "lock_open",
        closing: "door_front", closed: "lock",
        "traffic-cluster": "emergency",
        "anpr-watchlist": "local_police",
        "meteo": "cloud",
        "meteo-vent": "air",
        "meteo-pluie": "umbrella",
        "meteo-pluie-imminente": "rainy",
        "checkpoint-reassign": "swap_horiz",
        "checkpoint-error-burst": "error",
        "pcorg-securite-ua": "shield",
        "pcorg-secours-ua": "local_hospital",
        "field_sos": "sos",
        "field-sos": "sos",
        "camera-event": "videocam"
    };
    var TITLE_MAP = {
        opening: "OUVERTURE IMMINENTE", opened: "SITE OUVERT",
        closing: "FERMETURE IMMINENTE", closed: "SITE FERME",
        "traffic-cluster": "ALERTE TRAFIC",
        "anpr-watchlist": "PLAQUE SURVEILLEE DETECTEE",
        "meteo": "Meteo",
        "meteo-vent": "ALERTE VENT",
        "meteo-pluie": "ALERTE PLUIE",
        "meteo-pluie-imminente": "PLUIE IMMINENTE",
        "checkpoint-error-burst": "RAFALE ERREURS CHECKPOINT",
        "checkpoint-reassign": "CHANGEMENT AFFECTATION CHECKPOINT",
        "pcorg-securite-ua": "ALERTE SÉCURITÉ",
        "pcorg-secours-ua": "ALERTE SECOURS",
        "field_sos": "SOS TABLETTE",
        "field-sos": "SOS TABLETTE",
        "camera-event": "ALERTE CAMERA"
    };
    var COLOR_MAP = {
        opening: "#f59e0b", closing: "#f59e0b",
        opened: "#22c55e", closed: "#ef4444",
        "traffic-cluster": "#f97316",
        "anpr-watchlist": "#dc2626",
        "meteo": "#42a5f5",
        "meteo-vent": "#f97316",
        "meteo-pluie": "#42a5f5",
        "meteo-pluie-imminente": "#42a5f5",
        "checkpoint-reassign": "#8b5cf6",
        "checkpoint-error-burst": "#dc2626",
        "pcorg-secours-ua": "#dc2626",
        "pcorg-securite-ua": "#ef4444",
        "field_sos": "#dc2626",
        "field-sos": "#dc2626"
    };
    var DISPLAY_MODES = { banner: 1, fullscreen: 1, critical: 1 };

    function _isSosType(type) { return type === "field_sos" || type === "field-sos"; }

    // --- Definitions (metadonnees par slug) ---
    var _defs = {};           // slug -> meta serveur
    var _defsList = null;     // definitions recues par l'utilisateur
    var _defsWaiters = [];

    function _rememberMeta(m) {
        if (m && m.slug) _defs[m.slug] = m;
    }

    function meta(type, m) {
        var d = m || _defs[type] || {};
        var mode = DISPLAY_MODES[d.display_mode] ? d.display_mode : "fullscreen";
        if (_isSosType(type)) mode = "critical";
        return {
            slug: type,
            icon: d.icon || ICON_MAP[type] || "info",
            color: d.color || COLOR_MAP[type] || "#6366f1",
            name: d.name || TITLE_MAP[type] || type,
            detection_type: d.detection_type || "",
            display_mode: mode,
            category: d.category || ""
        };
    }

    function _isPcorg(type, m) {
        return (m && m.detection_type === "pcorg_urgency") || type.indexOf("pcorg-") === 0;
    }

    function loadDefinitions(cb) {
        if (_defsList) { if (cb) cb(_defsList); return; }
        if (cb) _defsWaiters.push(cb);
        if (_defsWaiters.length > 1) return;
        fetch("/api/alert-definitions/mine", { credentials: "same-origin", cache: "no-store" })
            .then(function(r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
            .then(function(list) {
                _defsList = Array.isArray(list) ? list : [];
                _defsList.forEach(_rememberMeta);
            })
            .catch(function() { _defsList = null; })
            .then(function() {
                var w = _defsWaiters; _defsWaiters = [];
                w.forEach(function(fn) { try { fn(_defsList || []); } catch(e) {} });
            });
    }

    // --- Preferences locales : alertes coupees sur ce poste ---
    // On memorise les alertes COUPEES, plus une liste blanche : l'ancienne
    // cle "cockpit-alert-prefs" listait 9 types actifs, et tout ce qui n'y
    // figurait pas (secours, securite, flux, cameras...) devenait muet des
    // qu'un operateur avait touche a une case. Une alerte critique ne peut
    // jamais etre coupee.
    var MUTED_KEY = "cockpit-alert-muted";
    var LEGACY_PREFS_KEY = "cockpit-alert-prefs";
    var LEGACY_PREF_IDS = ["opening", "opened", "closing", "closed", "traffic-cluster",
        "anpr-watchlist", "meteo-vent", "meteo-pluie", "checkpoint-reassign"];

    function _loadMuted() {
        try {
            var raw = localStorage.getItem(MUTED_KEY);
            if (raw) return JSON.parse(raw) || [];
            var legacy = localStorage.getItem(LEGACY_PREFS_KEY);
            if (legacy) {
                var active = JSON.parse(legacy) || [];
                var muted = LEGACY_PREF_IDS.filter(function(id) { return active.indexOf(id) < 0; });
                localStorage.setItem(MUTED_KEY, JSON.stringify(muted));
                localStorage.removeItem(LEGACY_PREFS_KEY);
                return muted;
            }
        } catch(e) {}
        return [];
    }
    function isMuted(type, m) {
        if (meta(type, m).display_mode === "critical") return false;
        return _loadMuted().indexOf(type) >= 0;
    }
    function setMuted(type, muted) {
        var list = _loadMuted().filter(function(x) { return x !== type; });
        if (muted) list.push(type);
        try { localStorage.setItem(MUTED_KEY, JSON.stringify(list)); } catch(e) {}
    }

    // Labels urgence par type de categorie
    var URGENCY_LABELS_ALERT = {
        SECOURS:  { EU: "Détresse vitale", UA: "Urgence absolue", UR: "Urgence relative", IMP: "Impliqué médical" },
        SECURITE: { EU: "Danger immédiat", UA: "Incident grave", UR: "Incident en cours", IMP: "Témoin / impliqué" },
        MIXTE:    { EU: "Urgence extrême", UA: "Urgence prioritaire", UR: "Situation stable", IMP: "Impliqué" }
    };
    var URGENCY_ENGAGE = {
        EU: "Engagement immédiat toutes ressources",
        UA: "Engagement prioritaire",
        UR: "Engagement planifié selon ressources disponibles",
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

    // Titre affiche : SOS et cameras ont un titre dynamique ; sinon le titre
    // produit par la source, sinon le nom de la definition.
    function _displayTitle(item) {
        var ad = item.actionData || {};
        if (_isSosType(item.type) && ad.device_name) return "SOS - " + ad.device_name;
        if (ad.event_type && ad.event_label) {
            return ad.event_label.toUpperCase() + (ad.camera_label ? " - " + ad.camera_label : "");
        }
        return ad.title || item.title || item.meta.name;
    }

    function _pushHistory(item) {
        if (item.preview || typeof window._pushAlertHistory !== "function") return;
        window._pushAlertHistory(item.type, item.meta.icon, _displayTitle(item),
            fmtAlertDateTime(item.triggeredAt), item.message, item.onView,
            item.alertId, item.meta.color);
    }

    // --- File d'attente : empiler et afficher une par une ---
    // extra : {title, meta, preview}
    function enqueueAlert(type, triggeredAt, message, onView, actionData, alertId, extra) {
        extra = extra || {};
        if (extra.meta) _rememberMeta(extra.meta);
        var m = meta(type, extra.meta);
        var item = {
            type: type, triggeredAt: triggeredAt, message: message || "", onView: onView,
            actionData: actionData || {}, alertId: alertId || null,
            title: extra.title || "", meta: m, preview: !!extra.preview
        };
        _pushHistory(item);

        if (!item.preview && isMuted(type, m)) return;

        if (m.display_mode === "banner") {
            _showBanner(item);
            return;
        }

        var isCritical = m.display_mode === "critical";
        if (isCritical) {
            // Une alerte critique passe devant tout : un SOS ne doit pas
            // attendre derriere une file d'alertes meteo ou trafic, ce qui le
            // faisait apparaitre avec un decalage variable selon les postes.
            // Entre critiques, l'ordre d'arrivee est conserve.
            var pos = 0;
            while (pos < _alertQueue.length && _alertQueue[pos].meta.display_mode === "critical") pos++;
            _alertQueue.splice(pos, 0, item);
            if (_alertOverlay && _currentItem && _currentItem.meta.display_mode !== "critical") {
                _alertQueue.splice(pos + 1, 0, _currentItem);
                _dismissCurrent(null);
                return;
            }
        } else {
            _alertQueue.push(item);
        }

        if (!_alertOverlay) {
            _showNextAlert();
        } else {
            _updateCounter();
        }
    }

    // --- Mode bandeau : notification discrete, sans plein ecran ---
    function _showBanner(item) {
        var text = _displayTitle(item) + (item.message ? " : " + item.message : "");
        if (typeof window.showToast === "function") {
            window.showToast("info", text, 9000);
        }
    }

    // --- Prise en compte partagee (SOS et alertes critiques) ---
    // Le premier operateur qui clique est enregistre cote serveur ; les autres
    // postes voient au poll suivant (5 s max) qui a pris l'alerte, leur alarme
    // s'arrete et l'alerte se ferme seule.
    var _takenById = {};   // alertId -> {name, at}
    var TAKEN_AUTOCLOSE_MS = 10000;

    function _csrfToken() {
        var m = document.querySelector("meta[name='csrf-token']");
        return m ? m.getAttribute("content") : "";
    }

    function _takeLabel(item) {
        return _isSosType(item.type) ? "Je prends en charge" : "Je prends en compte";
    }

    function _renderTaken(overlay, name, atIso, mine) {
        if (!overlay || overlay._takenRendered) return;
        overlay._takenRendered = true;
        _stopAlarm();
        var item = overlay._item || {};
        var isSos = _isSosType(item.type);
        var el = overlay._takenEl;
        if (el) {
            var at = fmtAlertDateTime(atIso);
            el.textContent = mine
                ? (isSos ? "Vous avez pris en charge ce SOS" : "Vous avez pris en compte cette alerte")
                : (isSos ? "Pris en charge par " : "Prise en compte par ") + (name || "un operateur") + (at ? " a " + at : "");
            el.style.display = "";
        }
        var box = overlay.querySelector(".critical-alert-box");
        if (box) box.classList.add("alert-taken");
        var row = overlay._btnRow;
        if (row) {
            row.textContent = "";
            if (item.onView) {
                var bv = document.createElement("button");
                bv.className = "critical-alert-btn critical-alert-btn-secondary";
                bv.textContent = _viewLabel(item);
                bv.addEventListener("click", function() {
                    _markDismissed(item.alertId);
                    if (_alertOverlay === overlay) _dismissCurrent(item.onView);
                });
                row.appendChild(bv);
            }
            var bc = document.createElement("button");
            bc.className = "critical-alert-btn";
            bc.textContent = "Fermer";
            bc.addEventListener("click", function() {
                _markDismissed(item.alertId);
                if (_alertOverlay === overlay) _dismissCurrent(null);
            });
            row.appendChild(bc);
        }
        if (!mine) {
            setTimeout(function() {
                if (_alertOverlay === overlay) {
                    _markDismissed(item.alertId);
                    _dismissCurrent(null);
                }
            }, TAKEN_AUTOCLOSE_MS);
        }
    }

    function _toast(type, msg) {
        if (typeof window.showToast === "function") window.showToast(type, msg);
    }

    function _takeAlert(overlay, item, btn) {
        if (item.preview) {
            _renderTaken(overlay, "", new Date().toISOString(), true);
            return;
        }
        if (!item.alertId) return;
        var label = _takeLabel(item);
        btn.disabled = true;
        btn.textContent = "Envoi...";
        fetch("/api/active-alerts/" + encodeURIComponent(item.alertId) + "/take", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": _csrfToken() },
            body: "{}"
        })
            .then(function(r) {
                return r.json().catch(function() { return {}; }).then(function(j) { return { status: r.status, json: j || {} }; });
            })
            .then(function(res) {
                if (res.status === 200 && res.json.ok) {
                    _takenById[item.alertId] = { name: res.json.taken_by_name, at: res.json.taken_at };
                    _stopAlarm();
                    _markDismissed(item.alertId);
                    if (_alertOverlay === overlay) _dismissCurrent(item.onView || null);
                    _toast("success", _isSosType(item.type)
                        ? "SOS pris en charge : les autres postes sont prevenus"
                        : "Alerte prise en compte : les autres postes sont prevenus");
                    return;
                }
                if (res.status === 409 && res.json.error === "already_taken") {
                    _takenById[item.alertId] = { name: res.json.taken_by_name, at: res.json.taken_at };
                    _renderTaken(overlay, res.json.taken_by_name, res.json.taken_at, false);
                    return;
                }
                btn.disabled = false;
                btn.textContent = label;
                _toast("error", "Prise en compte impossible (" + (res.json.error || res.status) + ")");
            })
            .catch(function() {
                btn.disabled = false;
                btn.textContent = label;
                _toast("error", "Erreur reseau");
            });
    }

    // Applique les prises en compte remontees par le poll : retire de la file
    // les alertes deja prises, bascule l'alerte affichee.
    function _applyTaken(alerts) {
        alerts.forEach(function(a) {
            if (a.taken_at) _takenById[a._id] = { name: a.taken_by_name, at: a.taken_at };
        });
        for (var i = _alertQueue.length - 1; i >= 0; i--) {
            var q = _alertQueue[i];
            if (q.alertId && _takenById[q.alertId]) _alertQueue.splice(i, 1);
        }
        _updateCounter();
        if (_alertOverlay && _currentItem && _currentItem.alertId && _takenById[_currentItem.alertId]) {
            var tk = _takenById[_currentItem.alertId];
            _renderTaken(_alertOverlay, tk.name, tk.at, false);
        }
    }

    function _dismissCurrent(callback) {
        if (_alertOverlay) {
            _stopAlarm();
            _alertOverlay.style.opacity = "0";
            var ov = _alertOverlay;
            _alertOverlay = null;
            _currentItem = null;
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
            badge.textContent = _alertQueue.length + " autre" + (_alertQueue.length > 1 ? "s" : "") + " en attente";
            badge.style.display = "";
        } else if (badge) {
            badge.style.display = "none";
        }
    }

    // --- Signal sonore via Web Audio API ---
    // SOS : sirene deux tons, 5 repetitions. Critique : double bip, 2 fois.
    var _alarmTimer = null;
    function _playAlarm(kind) {
        _stopAlarm();
        var isSos = kind === "sos";
        var playOnce = function() {
            try {
                var ctx = new (window.AudioContext || window.webkitAudioContext)();
                var t = ctx.currentTime;
                var osc = ctx.createOscillator();
                var gain = ctx.createGain();
                if (isSos) {
                    osc.type = "square";
                    for (var k = 0; k < 6; k++) osc.frequency.setValueAtTime(k % 2 ? 660 : 880, t + k * 0.25);
                    gain.gain.setValueAtTime(0.6, t);
                    gain.gain.linearRampToValueAtTime(0, t + 1.5);
                } else {
                    osc.type = "triangle";
                    osc.frequency.setValueAtTime(1046, t);
                    osc.frequency.setValueAtTime(784, t + 0.2);
                    gain.gain.setValueAtTime(0.5, t);
                    gain.gain.setValueAtTime(0, t + 0.18);
                    gain.gain.setValueAtTime(0.5, t + 0.2);
                    gain.gain.linearRampToValueAtTime(0, t + 0.5);
                }
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start(t);
                osc.stop(t + (isSos ? 1.5 : 0.5));
                setTimeout(function() { try { ctx.close(); } catch(e) {} }, 2000);
            } catch(e) {}
        };
        playOnce();
        var count = 0;
        var max = isSos ? 5 : 2;
        _alarmTimer = setInterval(function() {
            count++;
            if (count >= max) { _stopAlarm(); return; }
            playOnce();
        }, 2000);
    }
    function _stopAlarm() {
        if (_alarmTimer) { clearInterval(_alarmTimer); _alarmTimer = null; }
    }

    function _viewLabel(item) {
        var ad = item.actionData || {};
        if (item.type === "anpr-watchlist") return "Voir sur LAPI";
        if (item.type === "checkpoint-reassign") return "Voir Controle acces";
        if (_isPcorg(item.type, item.meta) || _isSosType(item.type)) return "Ouvrir la fiche";
        if (ad.event_type && ad.camera_path) return "Voir la camera";
        return "Voir sur la carte";
    }

    function _el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }

    function _addMessage(body, text) {
        var el = _el("div", "critical-alert-message", text);
        // Un texte de fiche peut faire 200 caracteres : en 1,4 rem gras il
        // remplissait l'ecran.
        if ((text || "").length > 90) el.classList.add("is-long");
        body.appendChild(el);
    }

    function _showNextAlert() {
        if (_alertQueue.length === 0) return;

        var item = _alertQueue.shift();
        _currentItem = item;
        var type = item.type;
        var m = item.meta;
        var timeStr = fmtAlertDateTime(item.triggeredAt);
        var actionData = item.actionData || {};
        var isFieldSos = _isSosType(type);
        var isCritical = m.display_mode === "critical";
        var isPco = _isPcorg(type, m);
        var isCameraEvent = !!actionData.event_type && !!actionData.camera_path;

        var overlay = _el("div", "critical-alert-overlay");
        _alertOverlay = overlay;
        overlay._item = item;

        var box = _el("div", "critical-alert-box alert-" + type);
        box.style.setProperty("--alert-color", m.color);
        if (isCritical) box.classList.add("alert-critical");
        box.setAttribute("role", "alertdialog");
        box.setAttribute("aria-modal", "true");

        var header = _el("div", "critical-alert-header");
        var icon = _el("span", "material-symbols-outlined critical-alert-icon", actionData.icon || m.icon);
        var title = _el("div", "critical-alert-title", _displayTitle(item));
        title.id = "critical-alert-title-" + Date.now();
        box.setAttribute("aria-labelledby", title.id);
        header.appendChild(icon);
        header.appendChild(title);
        if (item.preview) header.appendChild(_el("div", "critical-alert-preview-tag", "Apercu - aucune alerte envoyee"));

        var body = _el("div", "critical-alert-body");

        if (isFieldSos) {
            body.appendChild(_el("div", "critical-alert-sos-big", "Demande d assistance immediate"));
            var info = _el("div", "critical-alert-sos-info");
            var hasPos = (typeof actionData.lat === "number" && typeof actionData.lng === "number");
            var rows = [
                ["place", hasPos ? actionData.lat.toFixed(5) + ", " + actionData.lng.toFixed(5) : "Position inconnue"],
                ["battery_5_bar", (typeof actionData.battery === "number") ? (Math.round(actionData.battery) + "%") : "?"],
                ["schedule", timeStr]
            ];
            rows.forEach(function(r) {
                var d = _el("div");
                d.appendChild(_el("span", "material-symbols-outlined", r[0]));
                d.appendChild(document.createTextNode(" " + r[1]));
                info.appendChild(d);
            });
            body.appendChild(info);
        } else if (isPco && actionData.niveau_urgence) {
            var parts = (item.message || "").split(" — ");
            var zone = actionData.zone || "";
            var opName = actionData.operator || "";
            parts.slice(1).forEach(function(p) {
                if (!zone && p.indexOf("Zone : ") === 0) zone = p.slice(7);
                if (!opName && p.indexOf("Operateur : ") === 0) opName = p.slice(12);
            });

            body.appendChild(_el("div",
                "critical-alert-urgency-badge critical-alert-urgency-" + actionData.niveau_urgence,
                actionData.niveau_urgence + " — " + _urgencyLabel(actionData.category, actionData.niveau_urgence)));
            _addMessage(body, actionData.text || parts[0] || "");
            if (zone) {
                var zl = _el("div", "critical-alert-zone");
                zl.appendChild(_el("span", "material-symbols-outlined", "place"));
                zl.appendChild(document.createTextNode(" " + zone));
                body.appendChild(zl);
            }
            var engageDesc = URGENCY_ENGAGE[actionData.niveau_urgence] || "";
            if (engageDesc) body.appendChild(_el("div", "critical-alert-engage", engageDesc));
            body.appendChild(_el("div", "critical-alert-meta", timeStr + (opName ? " — saisie par " + opName : "")));
        } else if (isCameraEvent) {
            _addMessage(body, item.message);
            if (item.alertId && actionData.has_snapshot) {
                var img = document.createElement("img");
                img.className = "critical-alert-snapshot";
                img.src = "/api/hik-snapshot/" + encodeURIComponent(item.alertId);
                img.alt = "Snapshot " + (actionData.camera_label || actionData.camera_path || "");
                img.onerror = function(){ this.style.display = "none"; };
                body.appendChild(img);
            }
            body.appendChild(_el("div", "critical-alert-meta",
                timeStr + (actionData.camera_location ? " — " + actionData.camera_location : "")));
        } else {
            _addMessage(body, item.message);
            body.appendChild(_el("div", "critical-alert-time", timeStr));
        }

        // Bouton "Expliquer" (IA) retire des alertes : chaque clic d'operateur
        // consommait un appel au modele. explainWidget reste disponible.

        // Zone "pris en compte par ..." (remplie par _renderTaken)
        var takenEl = _el("div", "critical-alert-taken");
        takenEl.style.display = "none";
        body.appendChild(takenEl);
        overlay._takenEl = takenEl;

        var counter = _el("div", "critical-alert-counter");
        counter.style.display = "none";

        var btnRow = _el("div", "critical-alert-btns");
        overlay._btnRow = btnRow;

        var close = function(cb) {
            _markDismissed(item.alertId);
            _dismissCurrent(cb);
        };

        if (isCritical && (item.alertId || item.preview)) {
            var btnIgnore = _el("button", "critical-alert-btn critical-alert-btn-secondary", "Ignorer");
            btnIgnore.addEventListener("click", function() { close(null); });
            btnRow.appendChild(btnIgnore);
            if (item.onView) {
                var btnOpen = _el("button", "critical-alert-btn critical-alert-btn-secondary", _viewLabel(item));
                btnOpen.addEventListener("click", function() { close(item.onView); });
                btnRow.appendChild(btnOpen);
            }
            var btnTake = _el("button", "critical-alert-btn", _takeLabel(item));
            btnTake.addEventListener("click", function() { _takeAlert(overlay, item, btnTake); });
            btnRow.appendChild(btnTake);
        } else if (item.onView) {
            var btnIgn = _el("button", "critical-alert-btn critical-alert-btn-secondary", "Ignorer");
            btnIgn.addEventListener("click", function() { close(null); });
            btnRow.appendChild(btnIgn);
            var btnView = _el("button", "critical-alert-btn", _viewLabel(item));
            btnView.addEventListener("click", function() { close(item.onView); });
            btnRow.appendChild(btnView);
        } else {
            var btn = _el("button", "critical-alert-btn", "Compris");
            btn.addEventListener("click", function() { close(null); });
            btnRow.appendChild(btn);
        }

        body.appendChild(counter);
        body.appendChild(btnRow);
        box.appendChild(header);
        box.appendChild(body);
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        _updateCounter();

        if (isCritical) _playAlarm(isFieldSos ? "sos" : "critical");

        // Alerte deja prise ailleurs au moment de l'affichage
        if (item.alertId && _takenById[item.alertId]) {
            var tk = _takenById[item.alertId];
            _renderTaken(overlay, tk.name, tk.at, false);
        }

        var focusBtn = overlay.querySelector(".critical-alert-btn:last-child");
        if (focusBtn) setTimeout(function() { focusBtn.focus(); }, 100);
    }

    // --- Construction du callback "Voir" ---
    function _buildOnView(slug, a) {
        var fn = null;
        var ad = a.actionData || null;
        if (slug === "traffic-cluster" && ad && ad.pins) {
            fn = function() {
                window._allAlertPinsData = ad.pins;
                if (window.CockpitMapView && window.CockpitMapView.switchView) {
                    window.CockpitMapView.switchView("map");
                    setTimeout(function() {
                        document.dispatchEvent(new CustomEvent("showAllAlertPins"));
                    }, 400);
                }
            };
        }
        if (slug === "anpr-watchlist" && ad && ad.plate) {
            fn = function() {
                window.open("/anpr?plate=" + encodeURIComponent(ad.plate), "_blank");
            };
        }
        if (slug === "checkpoint-reassign") {
            fn = function() {
                window.open("/live-controle", "_blank");
            };
        }
        if (_isPcorg(slug, a.meta) && ad && ad.pcorg_id) {
            fn = function() {
                if (window.PcorgUI && window.PcorgUI.openFiche) {
                    window.PcorgUI.openFiche(ad.pcorg_id);
                }
            };
        }
        // Alerte camera : detection via actionData.event_type (slug est user-defined)
        if (ad && ad.event_type && ad.camera_path) {
            fn = function() {
                window.open("/cameras?focus=" + encodeURIComponent(ad.camera_path), "_blank");
            };
        }
        if (_isSosType(slug) && ad) {
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
                            var mp = window.CockpitMapView.getMap();
                            if (mp) mp.setView([ad.lat, ad.lng], 19);
                        }
                    }, 400);
                }
            };
        }
        if (fn) fn._actionData = ad || {};
        return fn;
    }

    // --- Explication par l'assistant IA (POST /api/alerts/<id>/explain) ---
    // Bloc autonome (bouton + zone de resultat) reutilise par le plein ecran
    // et par l'historique (main.js). Il ne bloque jamais l'acquittement : le
    // bouton est hors de la rangee d'actions, que _renderTaken remplace.
    // En apercu (page d'admin), aucun appel n'est fait.
    var AAI_LABELS = [
        ["contexte", "Contexte"],
        ["cause_probable", "Cause probable"],
        ["evolution", "Evolution"],
        ["action_suggeree", "Action suggeree"],
        ["confiance", "Confiance"]
    ];
    var AAI_MAX_RETRY = 20;

    function _aaiRender(result, data, refreshFn) {
        result.textContent = "";
        var secs = data.sections || {};
        AAI_LABELS.forEach(function(kv) {
            var txt = secs[kv[0]];
            if (!txt) return;
            var row = _el("div", "aai-section aai-" + kv[0]);
            row.appendChild(_el("div", "aai-label", kv[1]));
            row.appendChild(_el("div", "aai-text", txt));
            result.appendChild(row);
        });
        var foot = _el("div", "aai-foot");
        var when = fmtAlertDateTime(data.created_at);
        foot.appendChild(document.createTextNode(
            "Assistant IA" + (when ? " - " + when : "") + (data.cached ? " (deja genere)" : "") +
            " - a verifier sur le terrain"));
        if (data.can_refresh && refreshFn) {
            var rb = _el("button", "aai-refresh", "Actualiser");
            rb.type = "button";
            rb.addEventListener("click", function(e) { e.stopPropagation(); refreshFn(); });
            foot.appendChild(rb);
        }
        result.appendChild(foot);
    }

    function _aaiRequest(alertId, refresh, btn, result, attempt) {
        attempt = attempt || 0;
        btn.disabled = true;
        result.style.display = "";
        if (attempt === 0) {
            result.textContent = "";
            var sp = _el("div", "aai-loading");
            sp.appendChild(_el("span", "aai-spinner"));
            sp.appendChild(document.createTextNode(" Analyse en cours..."));
            result.appendChild(sp);
        }
        fetch("/api/alerts/" + encodeURIComponent(alertId) + "/explain" + (refresh ? "?refresh=1" : ""), {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": _csrfToken() },
            body: "{}"
        })
            .then(function(r) {
                return r.json().catch(function() { return {}; }).then(function(j) { return { status: r.status, json: j || {} }; });
            })
            .then(function(res) {
                if (res.status === 202 && attempt < AAI_MAX_RETRY) {
                    // Un autre poste fait deja generer cette explication
                    setTimeout(function() { _aaiRequest(alertId, false, btn, result, attempt + 1); },
                               (res.json.retry_after_s || 3) * 1000);
                    return;
                }
                btn.disabled = false;
                if (res.status === 200 && res.json.ok) {
                    btn.style.display = "none";
                    _aaiRender(result, res.json, function() { _aaiRequest(alertId, true, btn, result, 0); });
                    if (res.json.refresh_refused) {
                        _toast("info", "Explication generee il y a moins de 2 min : pas de nouvelle analyse");
                    }
                    return;
                }
                var code = res.json.error || res.status;
                var msg = code === "cle_api_absente" ? "Assistant IA non configure sur ce serveur"
                    : code === "alerte_introuvable" ? "Alerte introuvable"
                    : code === "en_cours" ? "Analyse toujours en cours, reessayez"
                    : code === "budget_exceeded" ? "Budget IA atteint : explication indisponible"
                    : "Explication indisponible (" + code + ")";
                result.textContent = "";
                result.appendChild(_el("div", "aai-error", msg));
            })
            .catch(function() {
                btn.disabled = false;
                result.textContent = "";
                result.appendChild(_el("div", "aai-error", "Erreur reseau"));
            });
    }

    // opts : {preview: bool, compact: bool}
    function explainWidget(alertId, opts) {
        opts = opts || {};
        var wrap = _el("div", "aai-wrap" + (opts.compact ? " aai-compact" : ""));
        var btn = _el("button", "aai-btn");
        btn.type = "button";
        btn.appendChild(_el("span", "material-symbols-outlined", "auto_awesome"));
        btn.appendChild(document.createTextNode(" Expliquer"));
        var result = _el("div", "aai-result");
        result.style.display = "none";
        btn.addEventListener("click", function(e) {
            e.stopPropagation();
            if (opts.preview || !alertId) {
                _toast("info", "Apercu : l'explication n'est pas generee (aucun appel)");
                return;
            }
            _aaiRequest(alertId, false, btn, result, 0);
        });
        result.addEventListener("click", function(e) { e.stopPropagation(); });
        wrap.appendChild(btn);
        wrap.appendChild(result);
        return wrap;
    }

    // --- Apercu (page d'admin) ---
    // Affiche une alerte factice avec le rendu reel d'une definition, sans
    // rien ecrire nulle part : ni alerte active, ni historique, ni WhatsApp.
    function preview(def) {
        def = def || {};
        var slug = def.slug || "apercu";
        var m = {
            slug: slug, name: def.name || slug, icon: def.icon, color: def.color,
            detection_type: def.detection_type || "", display_mode: def.display_mode,
            category: (def.params || {}).category || ""
        };
        var params = def.params || {};
        var ad = {};
        var message = def.description || "Exemple de message d'alerte";
        var title = "";
        if (def.detection_type === "pcorg_urgency") {
            var lvl = params.min_level || "UA";
            ad = { niveau_urgence: lvl, category: params.category || "PCO.", pcorg_id: null,
                   text: "Exemple : texte de la fiche main courante", zone: "Zone d'exemple",
                   operator: "Operateur PC" };
            var catLabel = (params.category || "").split(".").pop();
            title = ("MAIN COURANTE " + (catLabel || "")).toUpperCase().trim();
            message = ad.text;
        } else if (_isSosType(slug)) {
            ad = { device_name: "Tablette exemple", lat: 47.9496, lng: 0.2076, battery: 64 };
        } else if (def.detection_type === "door_saturation_forecast") {
            title = "SATURATION PORTE PREVUE";
            message = "PORTE EXEMPLE : ~3200/h prevu vers 10:05 (capacite 3250/h)";
        }
        var onView = (def.detection_type === "pcorg_urgency" || def.detection_type === "traffic_cluster")
            ? function() {} : null;
        enqueueAlert(slug, new Date().toISOString(), message, onView, ad, null,
                     { title: title, meta: m, preview: true });
    }

    window.showCriticalAlert = enqueueAlert; // (type, triggeredAt, message, onView, actionData, alertId, extra)
    window.CockpitAlerts = {
        meta: meta,
        definitions: loadDefinitions,
        isMuted: isMuted,
        setMuted: setMuted,
        preview: preview,
        explainWidget: explainWidget
    };

    // --- Purge memoire des IDs vus ---
    function _persistSeen() {
        try { sessionStorage.setItem(SEEN_KEY, JSON.stringify(_seenAlertIds)); } catch(e) {}
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

    function _enqueueFromApi(a) {
        var slug = a.definition_slug || "";
        enqueueAlert(slug, a.triggeredAt || "", a.message || "", _buildOnView(slug, a),
                     a.actionData, a._id, { title: a.title || "", meta: a.meta || null });
    }

    // --- Polling ---
    function pollActiveAlerts() {
        if (_pollInFlight) return;
        _pollInFlight = true;
        fetch("/api/active-alerts", { cache: "no-store" })
            .then(function(r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function(alerts) {
                _consecutiveErrors = 0;
                if (!Array.isArray(alerts)) return;
                alerts.forEach(function(a) { _rememberMeta(a.meta); });
                _applyTaken(alerts);

                if (!_firstPollDone) {
                    // Premier poll : alertes < 5 min -> affichees, les autres -> historique seulement.
                    // Une alerte deja vue dans cet onglet ou deja traitee sur ce poste
                    // ne repasse pas en plein ecran.
                    _firstPollDone = true;
                    var now = Date.now();
                    alerts.slice().reverse().forEach(function(a) {
                        var alreadyHandled = !!_seenAlertIds[a._id] || _isDismissed(a._id) || !!a.taken_at;
                        _markSeen(a._id);
                        var age = a.triggeredAt ? (now - new Date(a.triggeredAt).getTime()) : Infinity;
                        if (age <= GRACE_PERIOD_MS && !alreadyHandled) {
                            _enqueueFromApi(a);
                        } else {
                            var slug = a.definition_slug || "";
                            _pushHistory({
                                type: slug, triggeredAt: a.triggeredAt, message: a.message || "",
                                onView: null, actionData: a.actionData || {}, alertId: a._id,
                                title: a.title || "", meta: meta(slug, a.meta)
                            });
                        }
                    });
                    return;
                }

                // Polls suivants : uniquement les nouvelles, dans l'ordre d'arrivee
                alerts.slice().reverse().forEach(function(a) {
                    if (_seenAlertIds[a._id]) return;
                    _markSeen(a._id);
                    if (_isDismissed(a._id)) return;   // deja traitee dans un autre onglet du poste
                    if (a.taken_at) return;            // deja prise en compte avant d'etre vue ici
                    _enqueueFromApi(a);
                });
            })
            .catch(function(err) {
                _consecutiveErrors++;
                if (_consecutiveErrors >= 3) {
                    console.warn("[alert_poller] Polling alertes en echec (" + _consecutiveErrors + " erreurs consecutives)", err);
                }
            })
            .then(function() { _pollInFlight = false; });
    }

    if (PREVIEW_ONLY) return;

    document.addEventListener("DOMContentLoaded", function() {
        loadDefinitions();
        pollActiveAlerts();
        setInterval(pollActiveAlerts, POLL_INTERVAL);
        // Chrome ralentit les minuteries d'un onglet en arriere-plan jusqu'a
        // une execution par minute : un poste dont l'onglet cockpit n'etait
        // pas au premier plan voyait le SOS avec jusqu'a une minute de retard.
        // Rattrapage immediat au retour sur l'onglet ou sur la fenetre.
        document.addEventListener("visibilitychange", function() {
            if (document.visibilityState === "visible") pollActiveAlerts();
        });
        window.addEventListener("focus", pollActiveAlerts);
    });
})();
