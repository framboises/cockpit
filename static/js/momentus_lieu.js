/* momentus_lieu.js - "Ce que Momentus prevoit dans ce lieu" (information seule).
 *
 * ⚠️ COPIE IDENTIQUE dans cockpit/static/js/momentus_lieu.js,
 * groundmaster/static/js/momentus-lieu.js et looker/static/js/momentus_lieu.js :
 * meme API cote serveur dans les trois apps (GET /api/momentus/mapped,
 * /api/momentus/lieu/<feature_id>, /api/momentus/carte).
 *
 * API :
 *   MomentusLieu.ready            Promise, resolue quand la liste des lieux rattaches est chargee
 *   MomentusLieu.isMapped(id)     le lieu carto a-t-il au moins un espace Momentus valide ?
 *   MomentusLieu.open({featureId, name, event, year, from, to})
 *   MomentusLieu.buttonHtml(id, name, compactClass, {from, to})  bouton pret a inserer (vide si
 *                                 non rattache) ; compactClass = bouton icone seule avec cette classe
 *   MomentusLieu.addLayerControl(map, {position, defaultPeriod, onEmpty})
 *                                 bouton + calque Leaflet avec choix de periode (Looker)
 *   MomentusLieu.currentEvent     fonction fournie par l'app, rend {event, year}
 * Tout element portant data-momentus-feature ouvre la fenetre au clic.
 */
(function () {
  "use strict";

  // "reserve" = moveIn Momentus, mode de reservation par defaut (PAS un montage,
  // cf. momentus_lieux.PHASES) ; les trois autres sont des choix volontaires.
  var PHASE_LABELS = { reserve: "Reserve", exploitation: "Exploitation", demontage: "Demontage", bloque: "Bloque" };
  var PHASE_COLORS = { reserve: "#0ea5e9", exploitation: "#2563eb", demontage: "#a855f7", bloque: "#64748b" };
  var STATUS = {
    confirme: { label: "Confirme", color: "#059669" },
    option: { label: "Option", color: "#d97706" },
    prospect: { label: "Prospect", color: "#6b7280" },
    autre: { label: "?", color: "#6b7280" }
  };
  var mapped = {};
  var current = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  var DAYS = ["dim.", "lun.", "mar.", "mer.", "jeu.", "ven.", "sam."];
  function parseDay(iso) {
    var p = String(iso || "").slice(0, 10).split("-");
    return new Date(+p[0], +p[1] - 1, +p[2]);
  }
  function fmtDay(iso, withDay) {
    if (!iso) return "";
    var d = parseDay(iso);
    var s = ("0" + d.getDate()).slice(-2) + "/" + ("0" + (d.getMonth() + 1)).slice(-2);
    return withDay ? DAYS[d.getDay()] + " " + s : s;
  }
  function fmtRange(a, b) {
    return a === b ? fmtDay(a, true) : fmtDay(a, true) + " → " + fmtDay(b, true);
  }
  function isoToday(offset) {
    var d = new Date();
    d.setDate(d.getDate() + (offset || 0));
    return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);
  }
  function dayDiff(a, b) { return Math.round((parseDay(b) - parseDay(a)) / 86400000); }

  // ---------------------------------------------------------------- styles
  function injectCss() {
    if (document.getElementById("ml-css")) return;
    var css = [
      ".mlz-overlay{position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:10050;display:flex;align-items:flex-start;justify-content:center;padding:5vh 16px;overflow:auto}",
      ".mlz-modal{background:#fff;color:#1f2937;border-radius:12px;box-shadow:0 20px 50px rgba(0,0,0,.3);width:min(880px,100%);font-family:inherit;display:flex;flex-direction:column;max-height:90vh}",
      ".mlz-head{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid #e5e7eb}",
      ".mlz-head h3{margin:0;font-size:17px;flex:1;display:flex;align-items:center;gap:8px}",
      ".mlz-head .material-symbols-outlined{color:#2563eb}",
      ".mlz-close{border:0;background:none;cursor:pointer;font-size:22px;color:#6b7280}",
      ".mlz-sub{padding:8px 18px;font-size:12px;color:#6b7280;border-bottom:1px solid #f3f4f6}",
      ".mlz-period{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 18px;border-bottom:1px solid #f3f4f6;font-size:13px}",
      ".mlz-chip{border:1px solid #d1d5db;background:#fff;border-radius:999px;padding:4px 12px;cursor:pointer;font:inherit;font-size:12px}",
      ".mlz-chip.on{background:#2563eb;border-color:#2563eb;color:#fff}",
      ".mlz-period input[type=date]{border:1px solid #d1d5db;border-radius:6px;padding:3px 6px;font:inherit;font-size:12px}",
      ".mlz-body{overflow:auto;padding:12px 18px 18px}",
      ".mlz-empty{padding:30px 10px;text-align:center;color:#6b7280}",
      ".mlz-card{border:1px solid #e5e7eb;border-left:4px solid #9ca3af;border-radius:8px;padding:10px 12px;margin-bottom:10px}",
      ".mlz-card-top{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline}",
      ".mlz-name{font-weight:700;font-size:14px}",
      ".mlz-badge{font-size:11px;font-weight:600;border-radius:4px;padding:1px 6px;color:#fff}",
      ".mlz-meta{font-size:12px;color:#6b7280}",
      ".mlz-bar{position:relative;height:10px;background:#f3f4f6;border-radius:5px;margin:8px 0 6px;overflow:hidden}",
      ".mlz-bar span{position:absolute;top:0;bottom:0;border-radius:3px;opacity:.9}",
      ".mlz-phases{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px}",
      ".mlz-phase i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:0}",
      ".mlz-fn{margin-top:6px;font-size:12px}",
      ".mlz-fn summary{cursor:pointer;color:#2563eb}",
      ".mlz-fn table{border-collapse:collapse;margin-top:4px;width:100%}",
      ".mlz-fn td{padding:2px 8px 2px 0;border-top:1px solid #f3f4f6;vertical-align:top}",
      ".mlz-legend{display:flex;gap:12px;font-size:11px;color:#6b7280;padding:0 18px 10px}",
      ".mlz-btn{display:inline-flex;align-items:center;gap:4px;border:1px solid #c7d2fe;background:#eef2ff;color:#3730a3;border-radius:6px;padding:4px 9px;font:inherit;font-size:12px;cursor:pointer;margin-top:8px}",
      ".mlz-btn .material-symbols-outlined{font-size:16px}"
    ].join("\n");
    var st = document.createElement("style");
    st.id = "ml-css";
    st.textContent = css;
    document.head.appendChild(st);
  }

  // ---------------------------------------------------------------- data
  var ready = fetch("/api/momentus/mapped", { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : { features: {} }; })
    .then(function (res) { mapped = (res && res.features) || {}; return mapped; })
    .catch(function () { mapped = {}; return mapped; });

  function isMapped(id) { return !!(id && mapped[id]); }

  function buttonHtml(featureId, name, compactClass, period) {
    if (!isMapped(featureId)) return "";
    // Periode imposee (calque de carte) : la fenetre s'ouvre sur ces dates
    var per = period && period.from && period.to
      ? ' data-momentus-from="' + esc(period.from) + '" data-momentus-to="' + esc(period.to) + '"' : "";
    // Le style du bouton (.mlz-btn) vit dans injectCss, qui n'etait appele qu'a
    // l'ouverture de la fenetre : avant le premier clic, le bouton des popups
    // de carte s'affichait sans style.
    injectCss();
    if (compactClass) {
      // Bouton icone seule (cartes Groundmaster, a cote de l'oeil "Voir sur la carte")
      return '<button type="button" class="' + esc(compactClass) + '" data-momentus-feature="' + esc(featureId) +
        '" data-momentus-name="' + esc(name || "") + '" data-tooltip="Reservations Momentus" ' +
        'title="Reservations Momentus">storefront</button>';
    }
    return '<button type="button" class="mlz-btn" data-momentus-feature="' + esc(featureId) +
      '" data-momentus-name="' + esc(name || "") + '"' + per + ' title="Ce que Momentus prevoit dans ce lieu">' +
      '<span class="material-symbols-outlined">storefront</span>Reservations Momentus</button>';
  }

  // ---------------------------------------------------------------- rendu
  function renderCard(b, from, to) {
    var st = STATUS[b.status] || STATUS.autre;
    var span = Math.max(1, dayDiff(from, to) + 1);
    var bar = b.phases.map(function (p) {
      var left = Math.max(0, dayDiff(from, p.start)) / span * 100;
      var width = Math.max(1, dayDiff(p.start, p.end) + 1) / span * 100;
      return '<span style="left:' + left.toFixed(2) + "%;width:" + width.toFixed(2) + "%;background:" +
        (PHASE_COLORS[p.phase] || "#9ca3af") + '" title="' + esc((PHASE_LABELS[p.phase] || p.phase) + " " +
        fmtRange(p.start, p.end)) + '"></span>';
    }).join("");
    var multiRoom = b.rooms.length > 1;
    var phases = b.phases.map(function (p) {
      var hours = p.start_time ? " " + esc(p.start_time) + "-" + esc(p.end_time || "") : "";
      return '<span class="mlz-phase"><i style="background:' + (PHASE_COLORS[p.phase] || "#9ca3af") + '"></i>' +
        esc(PHASE_LABELS[p.phase] || p.phase) + " " + fmtRange(p.start, p.end) + hours +
        (multiRoom ? " · " + esc(p.room) : "") + "</span>";
    }).join("");
    var meta = [];
    if (b.account && b.account.toUpperCase() !== b.name.toUpperCase()) meta.push(esc(b.account));
    if (b.type) meta.push(esc(b.type));
    if (b.attendance) meta.push("~" + esc(b.attendance) + " pers.");
    if (!multiRoom && b.rooms.length) meta.push(esc(b.rooms[0]));
    var fns = "";
    if (b.functions && b.functions.length) {
      fns = '<details class="mlz-fn"><summary>' + b.functions.length + " creneau(x) horaire(s)</summary><table>" +
        b.functions.map(function (f) {
          var h = f.start_time ? esc(f.start_time) + "-" + esc(f.end_time || "") : "journee";
          return "<tr><td>" + fmtDay(f.date, true) + "</td><td>" + h + "</td><td>" + esc(f.name) +
            (f.type ? ' <span class="mlz-meta">(' + esc(f.type) + ")</span>" : "") + "</td><td>" +
            (f.attendance ? "~" + esc(f.attendance) : "") + "</td>" + (multiRoom ? "<td>" + esc(f.room) + "</td>" : "") + "</tr>";
        }).join("") + "</table></details>";
    }
    return '<div class="mlz-card" style="border-left-color:' + st.color + '">' +
      '<div class="mlz-card-top"><span class="mlz-name">' + esc(b.name) + "</span>" +
      '<span class="mlz-badge" style="background:' + st.color + '">' + st.label + "</span>" +
      '<span class="mlz-meta">' + meta.join(" · ") + "</span></div>" +
      '<div class="mlz-bar">' + bar + "</div>" +
      '<div class="mlz-phases">' + phases + "</div>" + fns + "</div>";
  }

  function render(res) {
    var body = current.el.querySelector(".mlz-body");
    var sub = current.el.querySelector(".mlz-sub");
    var rooms = (res.rooms || []).map(function (r) { return esc(r.name); }).join(", ");
    sub.innerHTML = "Espaces Momentus : " + (rooms || "aucun") +
      (res.synced_at ? " · donnees du " + esc(new Date(res.synced_at).toLocaleString("fr-FR")) : "");
    current.el.querySelector(".mlz-from").value = res.from;
    current.el.querySelector(".mlz-to").value = res.to;
    if (!res.bookings.length) {
      body.innerHTML = '<div class="mlz-empty">Aucune reservation Momentus dans ce lieu du ' +
        fmtDay(res.from, true) + " au " + fmtDay(res.to, true) + ".</div>";
      return;
    }
    body.innerHTML = '<div class="mlz-meta" style="margin-bottom:8px">' + res.bookings.length +
      " reservation(s) du " + fmtDay(res.from, true) + " au " + fmtDay(res.to, true) + "</div>" +
      res.bookings.map(function (b) { return renderCard(b, res.from, res.to); }).join("");
  }

  function load(params) {
    var body = current.el.querySelector(".mlz-body");
    body.innerHTML = '<div class="mlz-empty">Chargement...</div>';
    var q = [];
    Object.keys(params).forEach(function (k) {
      if (params[k]) q.push(encodeURIComponent(k) + "=" + encodeURIComponent(params[k]));
    });
    var token = current.token = {};
    fetch("/api/momentus/lieu/" + encodeURIComponent(current.featureId) + (q.length ? "?" + q.join("&") : ""),
      { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!current || current.token !== token) return;
        if (!res.ok) throw new Error(res.error || "erreur");
        render(res);
      })
      .catch(function (e) {
        if (current && current.token === token) {
          body.innerHTML = '<div class="mlz-empty">Chargement impossible (' + esc(e.message) + ").</div>";
        }
      });
  }

  function setChip(which) {
    current.el.querySelectorAll(".mlz-chip").forEach(function (c) {
      c.classList.toggle("on", c.dataset.p === which);
    });
  }

  function close() {
    if (!current) return;
    current.el.remove();
    document.removeEventListener("keydown", onKey, true);
    current = null;
  }
  function onKey(ev) {
    if (ev.key === "Escape") { ev.stopPropagation(); close(); }
  }

  function open(opts) {
    injectCss();
    close();
    var hasEvent = !!(opts.event && opts.year);
    var el = document.createElement("div");
    el.className = "mlz-overlay";
    el.innerHTML =
      '<div class="mlz-modal" role="dialog" aria-modal="true">' +
      '<div class="mlz-head"><h3><span class="material-symbols-outlined">storefront</span>' + esc(opts.name || "Lieu") +
      '</h3><button class="mlz-close" title="Fermer">&times;</button></div>' +
      '<div class="mlz-sub"></div>' +
      '<div class="mlz-period">' +
      (hasEvent ? '<button class="mlz-chip" data-p="event">Montage → demontage ' + esc(opts.event) + " " + esc(opts.year) + "</button>" : "") +
      '<button class="mlz-chip" data-p="next">Aujourd\'hui + 60 j</button>' +
      '<span style="margin-left:auto">du <input type="date" class="mlz-from"> au <input type="date" class="mlz-to"></span>' +
      "</div>" +
      '<div class="mlz-legend">' + Object.keys(PHASE_LABELS).map(function (k) {
        return '<span><i style="display:inline-block;width:9px;height:9px;border-radius:2px;background:' + PHASE_COLORS[k] +
          ';margin-right:4px"></i>' + PHASE_LABELS[k] + "</span>";
      }).join("") + "</div>" +
      '<div class="mlz-body"></div></div>';
    document.body.appendChild(el);
    current = { el: el, featureId: opts.featureId, token: null };

    el.addEventListener("click", function (ev) {
      if (ev.target === el || ev.target.closest(".mlz-close")) { close(); return; }
      var chip = ev.target.closest(".mlz-chip");
      if (!chip) return;
      setChip(chip.dataset.p);
      if (chip.dataset.p === "event") load({ event: opts.event, year: opts.year });
      else load({ from: isoToday(0), to: isoToday(60) });
    });
    el.querySelectorAll(".mlz-from,.mlz-to").forEach(function (inp) {
      inp.addEventListener("change", function () {
        var a = el.querySelector(".mlz-from").value, b = el.querySelector(".mlz-to").value;
        if (a && b && a <= b) { setChip(""); load({ from: a, to: b }); }
      });
    });
    document.addEventListener("keydown", onKey, true);

    if (opts.from && opts.to) { setChip(""); load({ from: opts.from, to: opts.to }); }
    else if (hasEvent) { setChip("event"); load({ event: opts.event, year: opts.year }); }
    else { setChip("next"); load({ from: isoToday(0), to: isoToday(60) }); }
  }

  // ---------------------------------------------------------------- calque de carte
  // Lieux rattaches ayant au moins une reservation sur la periode choisie
  // (GET /api/momentus/carte), qu'ils soient actives ou non pour un evenement.
  var LAYER_PERIODS = [
    { id: "today", label: "Aujourd'hui", from: 0, to: 0 },
    { id: "tomorrow", label: "Demain", from: 1, to: 1 },
    { id: "7d", label: "7 prochains jours", from: 0, to: 6 },
    { id: "30d", label: "30 prochains jours", from: 0, to: 29 },
    { id: "month", label: "Mois en cours", month: true }
  ];
  var LAYER_COLOR = "#7c3aed";

  function periodDates(p) {
    if (p.month) {
      var d = new Date();
      var last = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
      return { from: isoToday(1 - d.getDate()), to: isoToday(last - d.getDate()) };
    }
    return { from: isoToday(p.from), to: isoToday(p.to) };
  }

  function injectLayerCss() {
    if (document.getElementById("ml-layer-css")) return;
    var css = [
      ".mlz-ctrl{position:relative}",
      ".mlz-ctrl-btn{display:flex;align-items:center;gap:6px;background:#fff;border:0;border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.4);padding:5px 9px;cursor:pointer;font:inherit;font-size:13px;color:#1f2937;white-space:nowrap}",
      ".mlz-ctrl-btn .material-symbols-outlined{font-size:20px;color:" + LAYER_COLOR + "}",
      ".mlz-ctrl-btn.on{background:" + LAYER_COLOR + ";color:#fff}",
      ".mlz-ctrl-btn.on .material-symbols-outlined{color:#fff}",
      ".mlz-ctrl-menu{position:absolute;top:0;right:calc(100% + 6px);background:#fff;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.25);padding:6px;min-width:210px;display:none;z-index:1000}",
      ".mlz-ctrl.left .mlz-ctrl-menu{right:auto;left:calc(100% + 6px)}",
      ".mlz-ctrl-menu.open{display:block}",
      ".mlz-ctrl-menu .mlz-ctrl-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#6b7280;padding:4px 8px}",
      ".mlz-ctrl-opt{display:flex;align-items:center;gap:8px;width:100%;text-align:left;border:0;background:none;border-radius:6px;padding:6px 8px;cursor:pointer;font:inherit;font-size:13px;color:#1f2937}",
      ".mlz-ctrl-opt:hover{background:#f3f4f6}",
      ".mlz-ctrl-opt.on{background:#ede9fe;color:#5b21b6;font-weight:600}",
      ".mlz-ctrl-opt small{margin-left:auto;color:#9ca3af;font-weight:400}",
      ".mlz-ctrl-sep{border-top:1px solid #e5e7eb;margin:4px 0}",
      ".mlz-ctrl-dates{display:flex;gap:4px;align-items:center;padding:4px 8px;font-size:12px;color:#6b7280}",
      ".mlz-ctrl-dates input{border:1px solid #d1d5db;border-radius:4px;padding:2px 4px;font:inherit;font-size:12px;width:118px}",
      ".mlz-pin{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4);position:relative}",
      ".mlz-pin .material-symbols-outlined{font-size:16px}",
      ".mlz-pin b{position:absolute;top:-7px;right:-9px;background:#fff;color:" + LAYER_COLOR + ";border:1px solid " + LAYER_COLOR + ";border-radius:9px;font-size:10px;min-width:16px;height:16px;line-height:14px;text-align:center;padding:0 3px}",
      ".mlz-pop{min-width:230px;font-size:13px}",
      ".mlz-pop strong.t{color:" + LAYER_COLOR + ";font-size:14px}",
      ".mlz-pop .m{color:#6b7280;font-size:12px}",
      ".mlz-pop ul{margin:6px 0 0;padding-left:16px}",
      ".mlz-pop li{margin:2px 0}"
    ].join("\n");
    var st = document.createElement("style");
    st.id = "ml-layer-css";
    st.textContent = css;
    document.head.appendChild(st);
  }

  /* addLayerControl(map, {position, defaultPeriod}) : bouton "Momentus" sur une
   * carte Leaflet. Clic = menu des periodes ; choisir une periode affiche le
   * calque, "Masquer" le retire. Rend {refresh, hide}. */
  function addLayerControl(map, opts) {
    opts = opts || {};
    if (!window.L || !map) return null;
    injectCss();
    injectLayerCss();
    var state = { layer: null, period: null, from: null, to: null, timer: null };
    var ctrl = L.control({ position: opts.position || "topright" });
    var btn, menu, labelEl;

    function setActive() {
      btn.classList.toggle("on", !!state.layer);
      labelEl.textContent = state.layer ? "Momentus · " + (state.period ? state.period.label : fmtDay(state.from) + "-" + fmtDay(state.to)) : "Momentus";
      menu.querySelectorAll(".mlz-ctrl-opt[data-p]").forEach(function (o) {
        o.classList.toggle("on", !!state.layer && state.period && o.dataset.p === state.period.id);
      });
    }
    function hide() {
      if (state.layer) { map.removeLayer(state.layer); state.layer = null; }
      clearInterval(state.timer);
      state.timer = null;
      setActive();
    }
    function show(period, from, to) {
      state.period = period;
      if (period) { var d = periodDates(period); from = d.from; to = d.to; }
      state.from = from; state.to = to;
      if (!state.layer) state.layer = L.layerGroup().addTo(map);
      clearInterval(state.timer);
      state.timer = setInterval(refresh, 600000);  // synchro horaire : 10 min suffit
      setActive();
      refresh();
    }
    function refresh() {
      if (!state.layer) return;
      var layer = state.layer, from = state.from, to = state.to;
      fetch("/api/momentus/carte?from=" + from + "&to=" + to, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (layer !== state.layer || !data || !data.ok) return;
          layer.clearLayers();
          var periode = from === to ? "le " + fmtDay(from, true) : "du " + fmtDay(from, true) + " au " + fmtDay(to, true);
          if (!data.lieux.length && typeof opts.onEmpty === "function") opts.onEmpty(periode);
          data.lieux.forEach(function (l) { drawLieu(layer, l, periode, from, to); });
          labelEl.title = data.lieux.length + " lieu(x) avec reservations Momentus " + periode;
        })
        .catch(function (e) { if (window.console) console.error("[Momentus] calque", e); });
    }
    function drawLieu(layer, l, periode, from, to) {
      var color = l.today ? LAYER_COLOR : "#a78bfa";
      if (l.geometry) {
        var ring = l.geometry.type === "Polygon" ? l.geometry.coordinates[0] : l.geometry.coordinates[0][0];
        L.polygon(ring.map(function (c) { return [c[1], c[0]]; }), {
          color: color, weight: 2, dashArray: "6 4", fillColor: color, fillOpacity: 0.12, interactive: false
        }).addTo(layer);
      }
      var marker = L.marker([l.lat, l.lng], {
        zIndexOffset: 500,
        icon: L.divIcon({
          className: "",
          html: '<div class="mlz-pin" style="background:' + color + '"><span class="material-symbols-outlined">storefront</span><b>' +
            l.count + "</b></div>",
          iconSize: [28, 28], iconAnchor: [14, 14]
        })
      });
      var list = l.bookings.map(function (b) {
        var phases = b.phases.map(function (p) { return (PHASE_LABELS[p] || p).toLowerCase(); }).join(", ");
        return "<li><strong>" + esc(b.name) + '</strong> <span class="m">' + esc((STATUS[b.status] || STATUS.autre).label) +
          " · " + fmtDay(b.start) + (b.end !== b.start ? "-" + fmtDay(b.end) : "") + (phases ? " · " + esc(phases) : "") + "</span></li>";
      }).join("");
      var more = l.count > l.bookings.length ? '<div class="m">+ ' + (l.count - l.bookings.length) + " autre(s)</div>" : "";
      marker.bindPopup('<div class="mlz-pop"><strong class="t">' + esc(l.name) + "</strong>" +
        '<div class="m">' + l.count + " reservation(s) Momentus " + periode +
        (l.today ? " · <strong>" + l.today + " aujourd'hui</strong>" : "") + "</div><ul>" + list + "</ul>" + more +
        buttonHtml(l.feature_id, l.name, null, { from: from, to: to }) + "</div>", { maxWidth: 320 });
      marker.bindTooltip(l.name + " · " + l.count + " resa Momentus", { direction: "top", offset: [0, -14] });
      layer.addLayer(marker);
    }

    ctrl.onAdd = function () {
      var div = L.DomUtil.create("div", "leaflet-control mlz-ctrl" + (/left$/.test(opts.position || "") ? " left" : ""));
      div.innerHTML = '<button type="button" class="mlz-ctrl-btn" title="Reservations Momentus (seminaires, receptifs, hospitalites vendues)">' +
        '<span class="material-symbols-outlined">storefront</span><span class="mlz-ctrl-label">Momentus</span></button>' +
        '<div class="mlz-ctrl-menu"><div class="mlz-ctrl-title">Afficher les reservations</div>' +
        LAYER_PERIODS.map(function (p) {
          var d = periodDates(p);
          return '<button type="button" class="mlz-ctrl-opt" data-p="' + p.id + '">' + esc(p.label) +
            "<small>" + (d.from === d.to ? fmtDay(d.from) : fmtDay(d.from) + "-" + fmtDay(d.to)) + "</small></button>";
        }).join("") +
        '<div class="mlz-ctrl-sep"></div><div class="mlz-ctrl-dates">du <input type="date" class="mlz-cf"> au <input type="date" class="mlz-ct"></div>' +
        '<div class="mlz-ctrl-sep"></div><button type="button" class="mlz-ctrl-opt" data-hide="1">' +
        '<span class="material-symbols-outlined" style="font-size:18px">visibility_off</span>Masquer</button></div>';
      btn = div.querySelector(".mlz-ctrl-btn");
      menu = div.querySelector(".mlz-ctrl-menu");
      labelEl = div.querySelector(".mlz-ctrl-label");
      L.DomEvent.disableClickPropagation(div);
      L.DomEvent.disableScrollPropagation(div);
      btn.addEventListener("click", function () { menu.classList.toggle("open"); });
      menu.addEventListener("click", function (ev) {
        var o = ev.target.closest(".mlz-ctrl-opt");
        if (!o) return;
        menu.classList.remove("open");
        if (o.dataset.hide) { hide(); return; }
        var p = LAYER_PERIODS.filter(function (x) { return x.id === o.dataset.p; })[0];
        if (p) show(p);
      });
      menu.querySelectorAll(".mlz-cf,.mlz-ct").forEach(function (inp) {
        inp.addEventListener("change", function () {
          var a = menu.querySelector(".mlz-cf").value, b = menu.querySelector(".mlz-ct").value;
          if (a && b && a <= b) { menu.classList.remove("open"); show(null, a, b); }
        });
      });
      document.addEventListener("click", function (ev) {
        if (!div.contains(ev.target)) menu.classList.remove("open");
      });
      return div;
    };
    ctrl.addTo(map);
    if (opts.defaultPeriod) {
      var p0 = LAYER_PERIODS.filter(function (x) { return x.id === opts.defaultPeriod; })[0];
      if (p0) ready.then(function () { show(p0); });
    }
    return { refresh: refresh, hide: hide };
  }

  // Tout element data-momentus-feature ouvre la fenetre (popups Leaflet,
  // cartes Groundmaster...). L'app fournit l'evenement courant via
  // MomentusLieu.currentEvent (fonction rendant {event, year}).
  // Phase de CAPTURE : selon la version de Leaflet, une popup peut arreter la
  // propagation du clic avant qu'il n'atteigne document.
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest("[data-momentus-feature]");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    var ctx = (window.MomentusLieu.currentEvent && window.MomentusLieu.currentEvent()) || {};
    open({ featureId: btn.dataset.momentusFeature, name: btn.dataset.momentusName, event: ctx.event, year: ctx.year,
           from: btn.dataset.momentusFrom, to: btn.dataset.momentusTo });
  }, true);

  window.MomentusLieu = {
    ready: ready,
    isMapped: isMapped,
    open: open,
    buttonHtml: buttonHtml,
    addLayerControl: addLayerControl,
    currentEvent: null
  };
})();
