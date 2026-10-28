// patrol_fiche_map.js - bloc "Patrouille" d'une fiche de main courante
// (content_category.patrol_run) : etat, tablette, avancement, trajet estime,
// carte agrandissable de tous les points (etat de chacun, trajet, position
// de la tablette) et liste des points avec leurs heures.
// Utilise par pcorg.js (fenetre de fiche de l'accueil, charge a la demande)
// et par la page Patrouilles. Donnees : GET /api/pcorg-patrol/<run_id>.
//
//   window.PatrolFicheMap.render(container, runId)  -> renvoie le bloc
(function () {
  "use strict";
  if (window.PatrolFicheMap) return;

  var STATE_LABELS = {
    proposed: "Proposee", running: "En cours", paused: "En pause", done: "Terminee",
    partial: "Terminee incomplete", refused: "Non prise", expired: "Expiree", cancelled: "Annulee",
  };
  var STATE_COLORS = {
    proposed: "#2563eb", running: "#16a34a", paused: "#d97706", done: "#059669",
    partial: "#ea580c", refused: "#dc2626", expired: "#dc2626", cancelled: "#64748b",
  };
  var STEP_COLORS = { pending: "#94a3b8", done: "#16a34a", skipped: "#f59e0b", missed: "#dc2626" };
  var STEP_LABELS = { pending: "a faire", done: "fait", skipped: "passe", missed: "non fait" };

  function injectCss() {
    if (document.getElementById("pfm-css")) return;
    var s = document.createElement("style");
    s.id = "pfm-css";
    s.textContent = [
      ".pfm{display:flex;flex-direction:column;gap:8px;margin:10px 0;padding:10px 12px;border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;color:#1e293b}",
      ".pfm-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}",
      ".pfm-head .material-symbols-outlined{font-size:20px;color:#475569}",
      ".pfm-title{font-weight:700;flex:1;min-width:0}",
      ".pfm-badge{font-size:12px;font-weight:700;padding:2px 9px;border-radius:999px;color:#fff;white-space:nowrap}",
      ".pfm-facts{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px;color:#475569}",
      ".pfm-facts b{color:#1e293b;font-variant-numeric:tabular-nums}",
      ".pfm-bar{display:flex;height:6px;border-radius:3px;background:#e2e8f0;overflow:hidden}",
      ".pfm-bar i{display:block;height:100%}",
      ".pfm-mapwrap{position:relative;height:240px;border-radius:9px;overflow:hidden;border:1px solid #cbd5e1;transition:height .2s}",
      ".pfm.big .pfm-mapwrap{height:min(70vh,640px)}",
      ".pfm-map{position:absolute;inset:0}",
      ".pfm-grow{position:absolute;right:8px;top:8px;z-index:500;display:inline-flex;align-items:center;gap:4px;border:0;border-radius:8px;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.25);padding:5px 9px;font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;color:#1e293b}",
      ".pfm-grow .material-symbols-outlined{font-size:17px}",
      ".pfm-steps{display:flex;flex-direction:column;gap:3px;font-size:13px}",
      ".pfm-step{display:grid;grid-template-columns:24px 1fr auto;gap:8px;align-items:center}",
      ".pfm-num{width:22px;height:22px;border-radius:50%;color:#fff;font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center}",
      ".pfm-when{color:#64748b;font-size:12px;white-space:nowrap}",
      ".pfm-sub{color:#64748b;font-size:12px}",
      ".pfm-ico span{display:flex;width:24px;height:24px;border-radius:50%;color:#fff;font-weight:700;font-size:11px;align-items:center;justify-content:center;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.35);box-sizing:border-box}",
      ".pfm-dev{display:flex;align-items:center;gap:4px;white-space:nowrap;font-size:12px;font-weight:700;color:#1e293b;text-shadow:0 0 3px #fff,0 0 3px #fff}",
      ".pfm-dev i{width:14px;height:14px;border-radius:50%;border:3px solid #fff;box-shadow:0 0 0 2px #2563eb;background:#2563eb;display:inline-block}",
      ".pfm-legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:#475569}",
      ".pfm-legend span{display:inline-flex;align-items:center;gap:5px}",
      ".pfm-legend i{display:inline-block;width:18px;height:0;border-top:4px solid #1d4ed8;border-radius:2px}",
      ".pfm-legend i.est{border-top:3px dashed #94a3b8}",
      ".pfm-legend i.pause{border-top:4px dotted #d97706}",
      ".pfm-legend i.dot{width:10px;height:10px;border:0;border-radius:50%}",
      ".pfm-thumbs{display:flex;gap:4px;flex-wrap:wrap;margin-top:3px}",
      ".pfm-thumbs img{width:54px;height:54px;object-fit:cover;border-radius:6px;border:1px solid #cbd5e1;cursor:zoom-in}",
      ".pfm-sec{font-weight:700;font-size:13px;margin-top:4px;display:flex;align-items:center;gap:6px}",
      ".pfm-sec .material-symbols-outlined{font-size:18px;color:#d97706}",
      ".pfm-constat{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;padding:6px 8px;border:1px solid #fde68a;background:#fffbeb;border-radius:8px;font-size:13px}",
      ".pfm-constat a{font-weight:700;color:#b45309;text-decoration:none}",
      ".pfm-constat a:hover{text-decoration:underline}",
      ".pfm-constat .pfm-thumbs{grid-column:1/-1}",
      ".pfm-cst-ico span{display:flex;width:26px;height:26px;border-radius:7px;background:#d97706;color:#fff;align-items:center;justify-content:center;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.35);box-sizing:border-box;font-size:17px}",
      ".pfm-pop img{display:block;max-width:220px;max-height:160px;border-radius:6px;margin-top:4px}",
    ].join("");
    document.head.appendChild(s);
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function decode6(str) {
    var idx = 0, lat = 0, lng = 0, out = [];
    while (str && idx < str.length) {
      var b, shift = 0, result = 0;
      do { b = str.charCodeAt(idx++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
      lat += (result & 1) ? ~(result >> 1) : (result >> 1);
      shift = 0; result = 0;
      do { b = str.charCodeAt(idx++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
      lng += (result & 1) ? ~(result >> 1) : (result >> 1);
      out.push([lat / 1e6, lng / 1e6]);
    }
    return out;
  }
  function hm(iso) { return iso ? new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }) : ""; }
  function km(m) { return m == null ? "-" : (m < 1000 ? m + " m" : (m / 1000).toFixed(1).replace(".", ",") + " km"); }
  function mins(s) {
    if (s == null) return "-";
    var m = Math.round(s / 60);
    return m < 60 ? m + " min" : Math.floor(m / 60) + " h " + String(m % 60).padStart(2, "0");
  }

  function thumbs(photos) {
    var w = el("div", "pfm-thumbs");
    photos.forEach(function (p) {
      if (!p || !(p.thumb || p.photo)) return;
      var a = el("a");
      a.href = p.photo || p.thumb; a.target = "_blank"; a.rel = "noopener";
      var img = el("img");
      img.src = p.thumb || p.photo; img.alt = "Photo"; img.loading = "lazy";
      a.appendChild(img);
      w.appendChild(a);
    });
    return w;
  }

  function render(container, runId) {
    injectCss();
    var box = el("div", "pfm");
    box.appendChild(el("div", "pfm-sub", "Chargement de la patrouille..."));
    container.appendChild(box);
    fetch("/api/pcorg-patrol/" + encodeURIComponent(runId), { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        box.textContent = "";
        if (!d || !d.ok) { box.appendChild(el("div", "pfm-sub", "Patrouille introuvable.")); return; }
        fill(box, d.run);
      })
      .catch(function () { box.textContent = ""; box.appendChild(el("div", "pfm-sub", "Patrouille indisponible.")); });
    return box;
  }

  function fill(box, r) {
    var sc = r.scenario || {};
    var est = sc.estimate || {};
    var c = r.counts || {};
    var total = (r.steps || []).length;

    var head = el("div", "pfm-head");
    var ic = el("span", "material-symbols-outlined", sc.travel === "pedestrian" ? "directions_walk" : "route");
    head.appendChild(ic);
    head.appendChild(el("span", "pfm-title", "Patrouille : " + (sc.name || "?")));
    var badge = el("span", "pfm-badge", STATE_LABELS[r.state] || r.state);
    badge.style.background = STATE_COLORS[r.state] || "#64748b";
    head.appendChild(badge);
    box.appendChild(head);

    var facts = el("div", "pfm-facts");
    function fact(label, value) {
      var s = el("span");
      s.appendChild(document.createTextNode(label + " "));
      s.appendChild(el("b", null, value));
      facts.appendChild(s);
    }
    fact("Tablette", r.device_name || (r.current ? r.current.device_name + " (proposee)" : "-"));
    fact("Points", (c.done || 0) + " / " + total + " faits" + (c.skipped ? ", " + c.skipped + " passe(s)" : "") + (c.missed ? ", " + c.missed + " non fait(s)" : ""));
    fact("Trajet estime", km(est.distance_m) + ", " + mins(est.duration_s) + (sc.travel === "pedestrian" ? " a pied" : " en vehicule"));
    if (r.started_at) fact("Depart", hm(r.started_at));
    if (r.ended_at) fact("Fin", hm(r.ended_at));
    if (r.started_at) {
      var end = r.ended_at ? new Date(r.ended_at) : new Date();
      fact("Duree", mins((end - new Date(r.started_at)) / 1000) + (r.paused_s >= 60 ? " dont " + mins(r.paused_s) + " de pause" : ""));
    }
    var trace = r.trace || { segments: [], n: 0 };
    if (r.started_at) fact("Parcouru (GPS)", trace.n > 1 ? km(trace.distance_m) : "aucune position recue");
    var constats = r.constats || [];
    if (constats.length) fact("Constats", String(constats.length));
    if ((r.speedings || []).length) fact("Depassements de vitesse", String(r.speedings.length));
    box.appendChild(facts);

    if (total) {
      var bar = el("div", "pfm-bar");
      [["done", c.done], ["skipped", c.skipped], ["missed", c.missed]].forEach(function (x) {
        if (!x[1]) return;
        var i = el("i"); i.style.width = (100 * x[1] / total) + "%"; i.style.background = STEP_COLORS[x[0]];
        bar.appendChild(i);
      });
      box.appendChild(bar);
    }

    // Carte agrandissable
    var wrap = el("div", "pfm-mapwrap");
    var mapEl = el("div", "pfm-map");
    wrap.appendChild(mapEl);
    var grow = el("button", "pfm-grow");
    grow.type = "button";
    var gi = el("span", "material-symbols-outlined", "open_in_full");
    var gt = el("span", null, "Agrandir");
    grow.appendChild(gi); grow.appendChild(gt);
    wrap.appendChild(grow);
    box.appendChild(wrap);

    var legend = el("div", "pfm-legend");
    function leg(cls, color, label) {
      var s = el("span"), i = el("i", cls);
      if (color) i.style.background = color;
      s.appendChild(i); s.appendChild(document.createTextNode(label));
      legend.appendChild(s);
    }
    if (trace.n > 1) leg(null, null, "parcours effectue");
    if (trace.segments.some(function (g) { return g.pause; })) leg("pause", null, "pendant une intervention (pause)");
    if (est.polyline) leg("est", null, "trajet prevu");
    if (constats.length) leg("dot", "#d97706", "constat terrain");
    if ((r.speedings || []).length) leg("dot", "#dc2626", "depassement de vitesse");
    if (legend.children.length) box.appendChild(legend);
    if (r.started_at && trace.n < 2) {
      box.appendChild(el("div", "pfm-sub", "Parcours effectue indisponible : la tablette n'a pas transmis de positions pendant la patrouille (application fermee, hors reseau ou mode test)."));
    }

    var steps = el("div", "pfm-steps");
    (r.steps || []).forEach(function (s, i) {
      var row = el("div", "pfm-step");
      var n = el("span", "pfm-num", String(i + 1));
      n.style.background = STEP_COLORS[s.state] || "#94a3b8";
      row.appendChild(n);
      var name = el("span", null, s.name);
      if (s.state === "skipped" && s.comment) name.appendChild(el("span", "pfm-sub", " - " + s.comment));
      else if (s.comment) name.appendChild(el("span", "pfm-sub", " - " + s.comment));
      if (s.photo && (s.photo.thumb || s.photo.photo)) name.appendChild(thumbs([s.photo]));
      row.appendChild(name);
      row.appendChild(el("span", "pfm-when", (STEP_LABELS[s.state] || s.state) + (s.at ? " " + hm(s.at) : "") + (s.method === "gps" ? " (GPS)" : "")));
      steps.appendChild(row);
    });
    box.appendChild(steps);

    if (constats.length) {
      var sec = el("div", "pfm-sec");
      sec.appendChild(el("span", "material-symbols-outlined", "report"));
      sec.appendChild(document.createTextNode("Constats terrain pendant la patrouille"));
      box.appendChild(sec);
      constats.forEach(function (c) {
        var row = el("div", "pfm-constat");
        var a = el("a", null, c.ref || "Constat");
        a.href = "/declarations?id=" + encodeURIComponent(c.id);
        a.target = "_blank"; a.rel = "noopener";
        row.appendChild(a);
        row.appendChild(el("span", "pfm-when", hm(c.at)));
        var t = el("div", null, c.text);
        t.style.gridColumn = "1/-1";
        row.appendChild(t);
        if (c.photos && c.photos.length) row.appendChild(thumbs(c.photos));
        box.appendChild(row);
      });
    }

    if (typeof L === "undefined") { wrap.remove(); return; }
    setTimeout(function () {
      var map = L.map(mapEl, { zoomControl: true, attributionControl: false });
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxNativeZoom: 19, maxZoom: 21 }).addTo(map);
      var pts = [];
      var hasTrace = trace.n > 1;
      if (est.polyline) {
        var line = decode6(est.polyline);
        if (line.length > 1) {
          // Avec un parcours effectue, le trajet prevu passe en pointilles
          L.polyline(line, hasTrace ? { color: "#94a3b8", weight: 3, opacity: 0.9, dashArray: "6 8" }
                                    : { color: sc.color && /^#[0-9a-fA-F]{6}$/.test(sc.color) ? sc.color : "#2563eb", weight: 4, opacity: 0.75 }).addTo(map);
          pts = pts.concat(line);
        }
      }
      trace.segments.forEach(function (g) {
        var ll = g.points.map(function (p) { return [p[0], p[1]]; });
        if (ll.length < 2) return;
        var t0 = g.points[0][2], t1 = g.points[g.points.length - 1][2];
        L.polyline(ll, g.pause ? { color: "#d97706", weight: 4, opacity: 0.9, dashArray: "2 7" }
                               : { color: "#1d4ed8", weight: 4, opacity: 0.85 })
          .bindTooltip((g.pause ? "Pendant une intervention : " : "Parcours : ") + hm(t0 * 1000) + " - " + hm(t1 * 1000), { sticky: true })
          .addTo(map);
        pts = pts.concat(ll);
      });
      (r.speedings || []).forEach(function (s) {
        if (s.lat == null) return;
        L.circleMarker([s.lat, s.lng], { radius: 7, color: "#fff", weight: 2, fillColor: "#dc2626", fillOpacity: 1 })
          .bindTooltip("Depassement : " + Math.round(s.max_kmh || 0) + " km/h a " + hm(s.at) + (s.duration_s ? " pendant " + s.duration_s + " s" : ""))
          .addTo(map);
      });
      constats.forEach(function (c) {
        if (c.lat == null) return;
        pts.push([c.lat, c.lng]);
        var pop = "<div class='pfm-pop'><b>" + esc(c.ref || "Constat") + "</b> - " + esc(hm(c.at)) + "<br>" + esc(c.text) +
          (c.photos && c.photos[0] ? "<img src='" + esc(c.photos[0].thumb || c.photos[0].photo) + "' alt='Photo'>" : "") +
          "<br><a href='/declarations?id=" + encodeURIComponent(c.id) + "' target='_blank' rel='noopener'>Ouvrir le constat</a></div>";
        L.marker([c.lat, c.lng], {
          icon: L.divIcon({ className: "pfm-cst-ico", html: "<span class='material-symbols-outlined'>report</span>", iconSize: [26, 26], iconAnchor: [13, 13] }),
          zIndexOffset: 1200,
        }).bindPopup(pop, { maxWidth: 260 }).addTo(map);
      });
      (r.steps || []).forEach(function (s, i) {
        if (s.lat == null) return;
        pts.push([s.lat, s.lng]);
        var mk = L.marker([s.lat, s.lng], {
          icon: L.divIcon({ className: "pfm-ico", html: "<span style='background:" + (STEP_COLORS[s.state] || "#94a3b8") + "'>" + (i + 1) + "</span>",
                            iconSize: [24, 24], iconAnchor: [12, 12] }),
        }).bindTooltip(esc(s.name) + " - " + esc(STEP_LABELS[s.state] || s.state) + (s.at ? " " + hm(s.at) : "") +
                       (s.photo ? " (photo : touchez le point)" : ""), { direction: "top", offset: [0, -10] }).addTo(map);
        if (s.photo && (s.photo.thumb || s.photo.photo)) {
          mk.bindPopup("<div class='pfm-pop'><b>" + (i + 1) + ". " + esc(s.name) + "</b> - " + esc(hm(s.at)) +
            "<a href='" + esc(s.photo.photo || s.photo.thumb) + "' target='_blank' rel='noopener'><img src='" + esc(s.photo.thumb || s.photo.photo) + "' alt='Photo'></a>" +
            (s.comment ? esc(s.comment) : "") + "</div>", { maxWidth: 260 });
        }
      });
      var pos = r.position;
      if (pos && pos.lat != null && ["running", "paused", "proposed"].indexOf(r.state) >= 0) {
        L.marker([pos.lat, pos.lng], {
          icon: L.divIcon({ className: "", html: "<div class='pfm-dev'><i></i>" + esc(r.device_name || "") + "</div>", iconSize: [140, 18], iconAnchor: [7, 9] }),
          zIndexOffset: 1000,
        }).addTo(map);
        pts.push([pos.lat, pos.lng]);
      }
      if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.15), { maxZoom: 17 });
      else map.setView([47.952, 0.212], 14);
      grow.addEventListener("click", function () {
        var big = !box.classList.contains("big");
        box.classList.toggle("big", big);
        gi.textContent = big ? "close_fullscreen" : "open_in_full";
        gt.textContent = big ? "Reduire" : "Agrandir";
        setTimeout(function () {
          map.invalidateSize();
          if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.15), { maxZoom: 17 });
          if (big) wrap.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }, 230);
      });
    }, 60);
  }

  window.PatrolFicheMap = { render: render, STATE_LABELS: STATE_LABELS };
})();
