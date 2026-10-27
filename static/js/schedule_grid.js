// schedule_grid.js - editeur de planning recurrent (format detection_arming.py)
//
// Semaines types (7 jours x 96 quarts d'heure, grille a peindre), modeles,
// saisie "de 22:00 a 06:00", exceptions datees (une fois ou chaque annee) ou
// calees sur une epreuve (chaque annee), regle des jours feries, et calendrier
// d'apercu fourni par l'appelant. Meme ergonomie que le planning de la page
// Detection (detection.js), qui garde pour l'instant sa propre copie.
//
//   var sg = ScheduleGrid.create(container, arming, {
//     events: ["24H AUTOS", ...], onChange: fn, onLabel: "Lancements",
//     preview: function (month) { return Promise({days, next}) } });
//   sg.arming()  -> {mode, weeks, default_week, exceptions, holidays}
(function () {
  "use strict";

  var WEEK_COLORS = ["#2563eb", "#7c3aed", "#0d9488", "#db2777", "#ca8a04", "#4b5563"];
  var EMPTY_DAY = new Array(97).join("0");
  var FULL_DAY = new Array(97).join("1");
  var DAY_FULL = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
  var TZ = "Europe/Paris";

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function icon(n) { return el("span", "material-symbols-outlined", n); }
  function btn(cls, label, iconName) {
    var b = el("button", cls);
    b.type = "button";
    if (iconName) b.appendChild(icon(iconName));
    if (label) b.appendChild(document.createTextNode((iconName ? " " : "") + label));
    return b;
  }
  function toast(type, msg) { if (typeof window.showToast === "function") window.showToast(type, msg); }
  function dayString(fromH, toH) {
    var s = "";
    for (var q = 0; q < 96; q++) s += (q >= fromH * 4 && q < toH * 4) ? "1" : "0";
    return s;
  }
  function orStr(a, b) { var s = ""; for (var i = 0; i < 96; i++) s += (a[i] === "1" || b[i] === "1") ? "1" : "0"; return s; }
  function fmtQ(q) { var m = q * 15; return (m === 1440 ? "24" : ("0" + Math.floor(m / 60)).slice(-2)) + ":" + ("0" + (m % 60)).slice(-2); }
  function spansOf(dayStr) {
    var out = [], s = null;
    for (var q = 0; q <= 96; q++) {
      var on = q < 96 && dayStr[q] === "1";
      if (on && s === null) s = q;
      if (!on && s !== null) { out.push(fmtQ(s) + "-" + fmtQ(q)); s = null; }
    }
    return out;
  }

  var PRESETS = [
    ["Jours ouvres 8h - 18h", function () { var d = dayString(8, 18); return [d, d, d, d, d, EMPTY_DAY, EMPTY_DAY]; }],
    ["Tous les jours 8h - 20h", function () { var d = dayString(8, 20); return [d, d, d, d, d, d, d]; }],
    ["Toutes les nuits 22h - 6h", function () { var d = orStr(dayString(0, 6), dayString(22, 24)); return [d, d, d, d, d, d, d]; }],
    ["Week-end entier", function () { return [EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, FULL_DAY, FULL_DAY]; }],
    ["Tout le temps", function () { return [FULL_DAY, FULL_DAY, FULL_DAY, FULL_DAY, FULL_DAY, FULL_DAY, FULL_DAY]; }],
    ["Tout effacer", function () { return [EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY, EMPTY_DAY]; }],
  ];

  function normalize(a) {
    a = JSON.parse(JSON.stringify(a || {}));
    a.mode = ["schedule", "always", "off"].indexOf(a.mode) >= 0 ? a.mode : "schedule";
    if (!a.weeks || !a.weeks.length) a.weeks = [{ id: "w1", name: "Semaine normale", grid: PRESETS[0][1]() }];
    a.weeks.forEach(function (w) {
      var g = w.grid || [];
      w.grid = [0, 1, 2, 3, 4, 5, 6].map(function (i) { return ((g[i] || "") + EMPTY_DAY).slice(0, 96); });
    });
    if (!a.weeks.some(function (w) { return w.id === a.default_week; })) a.default_week = a.weeks[0].id;
    a.exceptions = a.exceptions || [];
    a.holidays = a.holidays || "normal";
    delete a.override;
    return a;
  }

  function create(container, arming, opts) {
    opts = opts || {};
    var a = normalize(arming);
    var st = { tab: "weeks", week: a.default_week, month: new Date().toLocaleDateString("fr-CA", { timeZone: TZ }).slice(0, 7) };
    var onLabel = opts.onLabel || "Actif";

    function changed() { if (opts.onChange) opts.onChange(); }
    function weekColor(id) {
      var i = a.weeks.map(function (w) { return w.id; }).indexOf(id);
      return WEEK_COLORS[(i < 0 ? 0 : i) % WEEK_COLORS.length];
    }
    function currentWeek() { return a.weeks.filter(function (w) { return w.id === st.week; })[0] || a.weeks[0]; }

    var root = el("div", "sg");
    var modes = el("div", "sg-modes");
    [["schedule", "Selon le planning", "event_repeat"], ["always", "Tout le temps", "all_inclusive"], ["off", "En pause", "pause_circle"]].forEach(function (m) {
      var l = el("label", "sg-mode");
      var r = document.createElement("input"); r.type = "radio"; r.name = "sg-mode-" + Math.random().toString(36).slice(2); r.value = m[0];
      r.checked = a.mode === m[0];
      r.addEventListener("change", function () { a.mode = m[0]; changed(); paint(); });
      l.appendChild(r); l.appendChild(icon(m[2])); l.appendChild(document.createTextNode(" " + m[1]));
      modes.appendChild(l);
    });
    root.appendChild(modes);
    var tabs = el("div", "sg-tabs");
    [["weeks", "Semaine type", "calendar_view_week"], ["exceptions", "Exceptions et feries", "event_busy"], ["calendar", "Calendrier", "calendar_month"]].forEach(function (t) {
      var b = btn("", t[1], t[2]);
      b.dataset.tab = t[0];
      b.addEventListener("click", function () { st.tab = t[0]; paint(); });
      tabs.appendChild(b);
    });
    root.appendChild(tabs);
    var pane = el("div", "sg-pane");
    root.appendChild(pane);
    container.textContent = "";
    container.appendChild(root);

    function paint() {
      Array.prototype.forEach.call(tabs.children, function (b) { b.classList.toggle("active", b.dataset.tab === st.tab); });
      tabs.hidden = a.mode !== "schedule" && st.tab !== "calendar";
      pane.textContent = "";
      if (a.mode !== "schedule" && st.tab !== "calendar") {
        pane.appendChild(el("p", "sg-help", a.mode === "always"
          ? "Actif tout le temps : le planning n'est pas utilise (voir l'onglet Calendrier pour les lancements)."
          : "En pause : aucun lancement tant que ce mode est choisi."));
        var cal = btn("sg-link", "Voir le calendrier", "calendar_month");
        cal.addEventListener("click", function () { st.tab = "calendar"; tabs.hidden = false; paint(); });
        pane.appendChild(cal);
        return;
      }
      if (st.tab === "weeks") renderWeeks();
      else if (st.tab === "exceptions") renderExceptions();
      else renderCalendar();
    }

    // ---------- Semaines types ----------
    function renderWeeks() {
      var bar = el("div", "sg-weeks");
      a.weeks.forEach(function (w) {
        var b = btn("sg-chip" + (w.id === st.week ? " on" : ""), w.name);
        var sw = el("i"); sw.style.background = weekColor(w.id); b.insertBefore(sw, b.firstChild);
        if (w.id === a.default_week) { var s = icon("star"); s.title = "Semaine par defaut"; b.appendChild(s); }
        b.addEventListener("click", function () { st.week = w.id; paint(); });
        bar.appendChild(b);
      });
      var add = btn("sg-chip sg-add", "Autre semaine type", "add");
      add.title = "Copie de la semaine affichee (ex. semaine d'epreuve, vacances)";
      add.addEventListener("click", function () {
        if (a.weeks.length >= 10) { toast("warning", "10 semaines types au maximum"); return; }
        var cur = currentWeek(), id = "w" + Date.now().toString(36);
        a.weeks.push({ id: id, name: "Semaine " + (a.weeks.length + 1), grid: cur.grid.slice() });
        st.week = id; changed(); paint();
      });
      bar.appendChild(add);
      pane.appendChild(bar);

      var w = currentWeek();
      if (a.weeks.length > 1) {
        var tools = el("div", "sg-row");
        var nm = document.createElement("input"); nm.type = "text"; nm.value = w.name; nm.maxLength = 40; nm.className = "sg-input";
        nm.addEventListener("input", function () { w.name = nm.value || "Semaine"; changed(); });
        nm.addEventListener("change", paint);
        tools.appendChild(nm);
        if (w.id !== a.default_week) {
          var def = btn("sg-link", "Par defaut");
          def.addEventListener("click", function () { a.default_week = w.id; changed(); paint(); });
          var del = btn("sg-link sg-danger", "Supprimer");
          del.addEventListener("click", function () {
            if (a.exceptions.some(function (x) { return x.action === "week" && x.week_id === w.id; })) {
              toast("warning", "Semaine utilisee par une exception : modifiez l'exception d'abord"); return;
            }
            a.weeks = a.weeks.filter(function (x) { return x.id !== w.id; });
            st.week = a.default_week; changed(); paint();
          });
          tools.appendChild(def); tools.appendChild(del);
        } else tools.appendChild(el("span", "sg-help", "appliquee par defaut, hors exceptions"));
        pane.appendChild(tools);
      }

      var pre = el("div", "sg-presets");
      pre.appendChild(el("span", "sg-help", "Modeles :"));
      PRESETS.forEach(function (p) {
        var b = btn("sg-preset", p[0]);
        b.addEventListener("click", function () { w.grid = p[1](); changed(); paint(); });
        pre.appendChild(b);
      });
      pane.appendChild(pre);
      pane.appendChild(hoursForm(w));
      pane.appendChild(gridEditor(w));
      pane.appendChild(el("p", "sg-help", "Cliquez-glissez sur la grille pour peindre ou effacer des plages (au quart d'heure). Minuit est entre la fin d'une ligne et le debut de la suivante."));
      pane.appendChild(summary(w));
    }

    function hoursForm(w) {
      var f = el("div", "sg-hours");
      f.appendChild(el("span", "", onLabel + " de"));
      var t1 = document.createElement("input"); t1.type = "time"; t1.step = 900; t1.value = "08:00"; t1.className = "sg-input";
      var t2 = document.createElement("input"); t2.type = "time"; t2.step = 900; t2.value = "18:00"; t2.className = "sg-input";
      f.appendChild(t1); f.appendChild(el("span", "", "a")); f.appendChild(t2);
      var days = document.createElement("select"); days.className = "sg-input";
      [["all", "tous les jours"], ["wk", "du lundi au vendredi"], ["we", "samedi et dimanche"]].forEach(function (o) { days.appendChild(new Option(o[1], o[0])); });
      DAY_FULL.forEach(function (d, i) { days.appendChild(new Option("le " + d.toLowerCase(), String(i))); });
      f.appendChild(days);
      function q(v) { var p = (v || "").split(":"); return p.length < 2 ? null : Math.floor((+p[0] * 60 + +p[1]) / 15); }
      function apply(replace) {
        var s = q(t1.value), e = q(t2.value);
        if (s === null || e === null) { toast("warning", "Saisissez les deux heures"); return; }
        if (e === 0) e = 96;
        if (s === e) { toast("warning", "Debut et fin identiques : utilisez le modele Tout le temps"); return; }
        var sel = days.value === "all" ? [0, 1, 2, 3, 4, 5, 6] : days.value === "wk" ? [0, 1, 2, 3, 4] : days.value === "we" ? [5, 6] : [+days.value];
        var g = w.grid.map(function (x) { return x.split(""); });
        if (replace) sel.forEach(function (d) { for (var i = 0; i < 96; i++) g[d][i] = "0"; });
        sel.forEach(function (d) {
          if (s < e) { for (var i = s; i < e; i++) g[d][i] = "1"; }
          else { for (var j = s; j < 96; j++) g[d][j] = "1"; for (var k = 0; k < e; k++) g[(d + 1) % 7][k] = "1"; }
        });
        w.grid = g.map(function (r) { return r.join(""); });
        changed(); paint();
      }
      var rep = btn("sg-btn sg-primary", "Remplacer"); rep.title = "Efface les jours choisis puis applique ces horaires";
      rep.addEventListener("click", function () { apply(true); });
      var add = btn("sg-btn", "Ajouter"); add.title = "Ajoute cette plage aux horaires deja actifs";
      add.addEventListener("click", function () { apply(false); });
      f.appendChild(rep); f.appendChild(add);
      return f;
    }

    function gridEditor(w) {
      var wrap = el("div", "sg-grid");
      wrap.style.setProperty("--wc", weekColor(w.id));
      var head = el("div", "sg-grid-row sg-grid-head");
      head.appendChild(el("span", "sg-grid-day", ""));
      var hours = el("div", "sg-grid-hours");
      for (var h = 0; h < 24; h++) hours.appendChild(el("span", "", (h < 10 ? "0" : "") + h));
      head.appendChild(hours);
      wrap.appendChild(head);
      var cellsByRow = [];
      DAY_FULL.forEach(function (dn, d) {
        var row = el("div", "sg-grid-row");
        row.appendChild(el("span", "sg-grid-day", dn.slice(0, 3)));
        var cells = el("div", "sg-grid-cells");
        cellsByRow[d] = [];
        for (var q = 0; q < 96; q++) {
          var c = el("i", w.grid[d][q] === "1" ? "on" : "");
          if (q % 4 === 0) c.classList.add("hh");
          c.dataset.d = d; c.dataset.q = q;
          cells.appendChild(c);
          cellsByRow[d].push(c);
        }
        row.appendChild(cells);
        wrap.appendChild(row);
      });
      var painting = null, start = null;
      function apply(target) {
        var d0 = Math.min(start.d, target.d), d1 = Math.max(start.d, target.d);
        var q0 = Math.min(start.q, target.q), q1 = Math.max(start.q, target.q);
        for (var d = 0; d < 7; d++) {
          var row = w.grid[d].split("");
          for (var q = 0; q < 96; q++) {
            var v = (d >= d0 && d <= d1 && q >= q0 && q <= q1) ? painting.value : painting.base[d][q];
            row[q] = v;
            cellsByRow[d][q].classList.toggle("on", v === "1");
          }
          w.grid[d] = row.join("");
        }
      }
      function cellAt(e) {
        var t = document.elementFromPoint(e.clientX, e.clientY);
        return t && t.dataset && t.dataset.q !== undefined ? { d: +t.dataset.d, q: +t.dataset.q } : null;
      }
      wrap.addEventListener("pointerdown", function (e) {
        var c = cellAt(e);
        if (!c) return;
        e.preventDefault();
        start = c;
        painting = { value: w.grid[c.d][c.q] === "1" ? "0" : "1", base: w.grid.slice() };
        apply(c);
        wrap.setPointerCapture(e.pointerId);
      });
      wrap.addEventListener("pointermove", function (e) { if (painting) { var c = cellAt(e); if (c) apply(c); } });
      function end() {
        if (!painting) return;
        painting = null; start = null; changed();
        var s = pane.querySelector(".sg-summary");
        if (s) s.replaceWith(summary(w));
      }
      wrap.addEventListener("pointerup", end);
      wrap.addEventListener("pointercancel", end);
      return wrap;
    }

    function summary(w) {
      var box = el("div", "sg-summary");
      var total = w.grid.join("").split("1").length - 1;
      box.appendChild(el("b", "", w.name + " : " + Math.round(total / 4) + " h par semaine"));
      w.grid.forEach(function (d, i) {
        var sp = spansOf(d);
        box.appendChild(el("div", "", DAY_FULL[i] + " : " + (sp.length ? sp.join(", ") : "-")));
      });
      return box;
    }

    // ---------- Exceptions ----------
    function renderExceptions() {
      pane.appendChild(el("p", "sg-help", "Une exception remplace la semaine type sur une periode (vacances, jours d'epreuve, fermeture). La premiere de la liste qui couvre un jour l'emporte."));
      var list = el("div", "sg-exc-list");
      if (!a.exceptions.length) list.appendChild(el("div", "sg-empty", "Aucune exception."));
      a.exceptions.forEach(function (x, i) { list.appendChild(exceptionRow(x, i)); });
      pane.appendChild(list);
      var adds = el("div", "sg-row");
      var ad = btn("sg-btn", "Par dates", "date_range");
      ad.addEventListener("click", function () {
        var t = new Date().toLocaleDateString("fr-CA", { timeZone: TZ });
        a.exceptions.push({ id: "x" + Date.now().toString(36), label: "Nouvelle exception", kind: "dates", start: t, end: t, yearly: false, action: "off" });
        changed(); paint();
      });
      var ae = btn("sg-btn", "Pendant une epreuve", "sports_motorsports");
      var evs = opts.events || [];
      ae.disabled = !evs.length;
      ae.title = ae.disabled ? "Aucune epreuve dans le parametrage" : "Suit automatiquement les dates de l'epreuve, chaque annee";
      ae.addEventListener("click", function () {
        a.exceptions.push({ id: "x" + Date.now().toString(36), label: "Pendant l'epreuve", kind: "event", event: evs[0], span: "window",
                            action: a.weeks.length > 1 ? "week" : "on", week_id: a.weeks[a.weeks.length - 1].id });
        changed(); paint();
      });
      adds.appendChild(ad); adds.appendChild(ae);
      pane.appendChild(adds);

      pane.appendChild(el("div", "sg-sec", "Jours feries"));
      var hs = document.createElement("select"); hs.className = "sg-input";
      [["normal", "Comme un jour normal (semaine type)"], ["sunday", "Comme un dimanche"], ["on", "Toute la journee"], ["off", "Rien ce jour-la"]].forEach(function (o) { hs.appendChild(new Option(o[1], o[0])); });
      hs.value = a.holidays;
      hs.addEventListener("change", function () { a.holidays = hs.value; changed(); });
      pane.appendChild(hs);
      pane.appendChild(el("p", "sg-help", "Jours feries francais calcules automatiquement (Paques, Ascension, Pentecote compris)."));
    }

    function exceptionRow(x, i) {
      var r = el("div", "sg-exc");
      var top = el("div", "sg-row");
      var lb = document.createElement("input"); lb.type = "text"; lb.value = x.label; lb.maxLength = 60; lb.className = "sg-input sg-grow"; lb.placeholder = "Libelle";
      lb.addEventListener("input", function () { x.label = lb.value; changed(); });
      top.appendChild(lb);
      [["arrow_upward", "Plus prioritaire", i === 0, -1], ["arrow_downward", "Moins prioritaire", i === a.exceptions.length - 1, 1]].forEach(function (m) {
        var b = btn("sg-icon", null, m[0]); b.title = m[1]; b.disabled = m[2];
        b.addEventListener("click", function () { a.exceptions.splice(i + m[3], 0, a.exceptions.splice(i, 1)[0]); changed(); paint(); });
        top.appendChild(b);
      });
      var rm = btn("sg-icon", null, "delete"); rm.title = "Supprimer";
      rm.addEventListener("click", function () { a.exceptions.splice(i, 1); changed(); paint(); });
      top.appendChild(rm);
      r.appendChild(top);

      var when = el("div", "sg-row");
      if (x.kind === "event") {
        when.appendChild(el("span", "", "Pendant"));
        var es = document.createElement("select"); es.className = "sg-input";
        (opts.events || []).forEach(function (e) { es.appendChild(new Option(e, e)); });
        if (x.event && (opts.events || []).indexOf(x.event) < 0) es.appendChild(new Option(x.event + " (introuvable)", x.event));
        es.value = x.event;
        es.addEventListener("change", function () { x.event = es.value; changed(); });
        when.appendChild(es);
        var sp = document.createElement("select"); sp.className = "sg-input";
        sp.appendChild(new Option("du montage au demontage", "window"));
        sp.appendChild(new Option("les jours publics", "public"));
        sp.value = x.span || "window";
        sp.addEventListener("change", function () { x.span = sp.value; changed(); });
        when.appendChild(sp);
        when.appendChild(el("span", "sg-help", "chaque annee"));
      } else {
        when.appendChild(el("span", "", "Du"));
        var d1 = document.createElement("input"); d1.type = "date"; d1.value = x.start; d1.className = "sg-input";
        var d2 = document.createElement("input"); d2.type = "date"; d2.value = x.end; d2.className = "sg-input";
        d1.addEventListener("change", function () { x.start = d1.value; if (!x.end || x.end < x.start) { x.end = x.start; d2.value = x.end; } changed(); });
        d2.addEventListener("change", function () { x.end = d2.value; changed(); });
        when.appendChild(d1); when.appendChild(el("span", "", "au")); when.appendChild(d2);
        var yl = el("label", "sg-check"); var yc = document.createElement("input"); yc.type = "checkbox"; yc.checked = !!x.yearly;
        yc.addEventListener("change", function () { x.yearly = yc.checked; changed(); });
        yl.appendChild(yc); yl.appendChild(document.createTextNode(" chaque annee"));
        when.appendChild(yl);
      }
      r.appendChild(when);
      var act = el("div", "sg-row");
      act.appendChild(el("span", "", "Appliquer"));
      var as = document.createElement("select"); as.className = "sg-input";
      a.weeks.forEach(function (w) { as.appendChild(new Option("la semaine type " + w.name, "week:" + w.id)); });
      as.appendChild(new Option("toute la journee", "on"));
      as.appendChild(new Option("rien (aucun lancement)", "off"));
      as.value = x.action === "week" ? "week:" + x.week_id : x.action;
      as.addEventListener("change", function () {
        if (as.value.indexOf("week:") === 0) { x.action = "week"; x.week_id = as.value.slice(5); }
        else { x.action = as.value; delete x.week_id; }
        changed();
      });
      act.appendChild(as);
      r.appendChild(act);
      return r;
    }

    // ---------- Calendrier ----------
    function renderCalendar() {
      var nav = el("div", "sg-cal-nav");
      var prev = btn("sg-icon", null, "chevron_left");
      var next = btn("sg-icon", null, "chevron_right");
      var y = +st.month.slice(0, 4), m = +st.month.slice(5, 7);
      var title = el("b", "", new Date(y, m - 1, 15).toLocaleDateString("fr-FR", { month: "long", year: "numeric" }));
      function shift(n) { var d = new Date(y, m - 1 + n, 15); st.month = d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2); paint(); }
      prev.addEventListener("click", function () { shift(-1); });
      next.addEventListener("click", function () { shift(1); });
      nav.appendChild(prev); nav.appendChild(title); nav.appendChild(next);
      pane.appendChild(nav);
      var box = el("div", "sg-cal");
      box.appendChild(el("div", "sg-help", "Calcul..."));
      pane.appendChild(box);
      var side = el("div", "sg-cal-next");
      pane.appendChild(side);
      if (!opts.preview) { box.textContent = ""; return; }
      opts.preview(st.month).then(function (r) {
        box.textContent = "";
        ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."].forEach(function (d) { box.appendChild(el("span", "sg-cal-dow", d)); });
        var first = new Date(r.days[0].date + "T12:00:00");
        for (var k = 0; k < (first.getDay() + 6) % 7; k++) box.appendChild(el("span", "sg-cal-pad"));
        var todayKey = new Date().toLocaleDateString("fr-CA", { timeZone: TZ });
        r.days.forEach(function (d) {
          var c = el("div", "sg-cal-day" + (d.date === todayKey ? " today" : ""));
          var col = d.source === "exception" ? "#f97316" : d.source === "holiday" ? "#dc2626" : d.source === "validity" || d.kind === "off" ? "#94a3b8" : d.week_id ? weekColor(d.week_id) : "#16a34a";
          c.style.setProperty("--dc", col);
          c.appendChild(el("b", "", String(+d.date.slice(8))));
          c.appendChild(el("span", "sg-cal-lbl", d.holiday && d.source !== "holiday" ? d.label + " (" + d.holiday + ")" : d.label));
          var bar = el("div", "sg-cal-bar");
          d.spans.forEach(function (s) {
            var a0 = (+s[0].slice(0, 2) * 60 + +s[0].slice(3)) / 1440, a1 = (+s[1].slice(0, 2) * 60 + +s[1].slice(3)) / 1440;
            var seg = el("i"); seg.style.left = (a0 * 100) + "%"; seg.style.width = ((a1 - a0) * 100) + "%";
            bar.appendChild(seg);
          });
          c.appendChild(bar);
          c.title = d.date.split("-").reverse().join("/") + " - " + d.label + "\n" + (d.spans.length ? d.spans.map(function (s) { return s[0] + "-" + s[1]; }).join(", ") : "rien");
          box.appendChild(c);
        });
        side.textContent = "";
        side.appendChild(el("div", "sg-sec", "Prochains lancements"));
        if (!(r.next || []).length) side.appendChild(el("p", "sg-help", "Aucun lancement dans les 14 prochains jours."));
        (r.next || []).forEach(function (t) {
          var d = new Date(t);
          side.appendChild(el("div", "sg-next", d.toLocaleDateString("fr-FR", { weekday: "long", day: "2-digit", month: "2-digit", timeZone: TZ }) + " a " +
            d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: TZ })));
        });
      }).catch(function (e) { box.textContent = ""; box.appendChild(el("p", "sg-help", "Apercu impossible : " + (e.message || e))); });
    }

    paint();
    return {
      arming: function () { return { mode: a.mode, weeks: a.weeks, default_week: a.default_week, exceptions: a.exceptions, holidays: a.holidays }; },
      showCalendar: function () { st.tab = "calendar"; paint(); },
      refresh: paint,
    };
  }

  window.ScheduleGrid = { create: create, presets: PRESETS };
})();
