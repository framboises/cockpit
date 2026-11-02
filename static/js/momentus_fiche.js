/* momentus_fiche.js - Fiche d'exploitation d'un evenement Momentus (modale Cockpit).
 *
 * Pour les organisateurs (PC, regie, production), jamais pour le client :
 * equipe ACO, alertes (pas de plan, personnel sans heures, stock depasse,
 * regisseur necessaire), tuiles securite / regie / accueil / materiel / plans,
 * espaces jour par jour avec phase et pilote, personnel commande a la demi-heure
 * (grille du planning Bibler), programme, bon de preparation du materiel, plans.
 *
 * Donnees : GET /api/momentus/evenement/<event_id> (momentus_fiche.py, meme
 * calcul que Looker), plans : GET /api/momentus/document/<doc_id>.
 * Ouverture : MomentusFiche.open(eventId, declencheur) ou tout element portant
 * data-mf-event="<event_id>" (vignettes de la timeline, fenetre d'un lieu).
 * Tout le texte vient de Momentus : textContent uniquement, jamais innerHTML.
 */
(function () {
  "use strict";

  var FAMILLES = {
    securite: { label: "Sécurité", icon: "shield_person" },
    ssiap: { label: "SSIAP", icon: "local_fire_department" },
    regie: { label: "Régie", icon: "tune" },
    accueil: { label: "Accueil / placiers", icon: "badge" },
    autre: { label: "Autre personnel", icon: "groups" }
  };
  var PHASES = {
    montage: "Montage", reserve: "Réservé", exploitation: "Exploitation",
    demontage: "Démontage", bloque: "Bloqué", travaux: "Travaux"
  };
  var STATUTS = { confirme: "Confirmé", option: "Option", prospect: "Prospect", autre: "Statut inconnu" };
  var JOURS = ["dim.", "lun.", "mar.", "mer.", "jeu.", "ven.", "sam."];

  var state = null;

  // ------------------------------------------------------------- utilitaires
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null && text !== "") e.textContent = String(text);
    return e;
  }
  function add(parent) {
    for (var i = 1; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null || c === false) continue;
      parent.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return parent;
  }
  function icon(name) {
    var s = el("span", "material-symbols-outlined", name);
    s.setAttribute("aria-hidden", "true");
    return s;
  }
  function parseDay(iso) {
    var p = String(iso || "").slice(0, 10).split("-");
    return new Date(+p[0], +p[1] - 1, +p[2]);
  }
  function fmtDay(iso, long) {
    if (!iso) return "";
    var d = parseDay(iso);
    var s = ("0" + d.getDate()).slice(-2) + "/" + ("0" + (d.getMonth() + 1)).slice(-2);
    return long === false ? s : JOURS[d.getDay()] + " " + s;
  }
  function fmtRange(a, b) {
    if (!a) return "";
    return !b || a === b ? fmtDay(a) : fmtDay(a) + " → " + fmtDay(b);
  }
  function hours(a, b) {
    if (!a && !b) return "";
    return (a || "?") + (b ? " → " + b : "");
  }
  function relDays(iso) {
    var t = new Date(); t.setHours(0, 0, 0, 0);
    var n = Math.round((parseDay(iso) - t) / 86400000);
    if (n === 0) return "aujourd'hui";
    if (n === 1) return "demain";
    if (n > 1) return "dans " + n + " j";
    if (n === -1) return "hier";
    return "il y a " + (-n) + " j";
  }
  function qty(q) {
    if (q == null) return "";
    return String(q).replace(".", ",");
  }
  function slotLabel(i) {
    var m = i * 30;
    return ("0" + Math.floor(m / 60)).slice(-2) + ":" + ("0" + (m % 60)).slice(-2);
  }
  function isoToday() {
    var d = new Date();
    return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);
  }

  // ------------------------------------------------------------- structure
  function close() {
    if (!state) return;
    var opener = state.opener;
    state.root.remove();
    document.removeEventListener("keydown", onKey, true);
    state = null;
    if (opener && document.body.contains(opener)) { try { opener.focus(); } catch (e) { /* ignore */ } }
  }
  function onKey(ev) {
    if (ev.key === "Escape") { ev.stopPropagation(); close(); }
  }

  function shell() {
    var root = el("div", "mf-overlay");
    var modal = el("div", "mf-modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "mf-title");
    var head = el("div", "mf-head");
    var title = el("h2", "mf-title", "Fiche d'exploitation");
    title.id = "mf-title";
    var closeBtn = el("button", "mf-close");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Fermer");
    closeBtn.title = "Fermer (Échap)";
    add(closeBtn, icon("close"));
    var headMain = add(el("div", "mf-head-main"), add(el("div", "mf-eyebrow"), icon("assignment"), "Fiche d'exploitation Momentus"), title,
      el("div", "mf-meta"));
    add(head, headMain, el("div", "mf-countdown"), closeBtn);
    var body = el("div", "mf-body");
    body.setAttribute("aria-live", "polite");
    add(modal, head, body);
    add(root, modal);
    root.addEventListener("mousedown", function (ev) { if (ev.target === root) close(); });
    closeBtn.addEventListener("click", close);
    return { root: root, modal: modal, head: head, title: title, body: body };
  }

  function section(titleText, iconName, extra) {
    var s = el("section", "mf-section");
    var h = add(el("h3", "mf-h3"), icon(iconName), titleText);
    if (extra) add(h, el("span", "mf-h3-extra", extra));
    add(s, h);
    return s;
  }

  // ------------------------------------------------------------- en-tete
  function renderHead(d) {
    var ev = d.evenement;
    state.title.textContent = ev.nom;
    var meta = state.head.querySelector(".mf-meta");
    meta.textContent = "";
    var st = el("span", "mf-pill mf-st-" + (ev.annule ? "annule" : ev.statut), ev.annule ? "Annulé / perdu" : (STATUTS[ev.statut] || ev.statut));
    add(meta, st);
    if (ev.type) add(meta, el("span", "mf-pill", ev.type));
    if (ev.interne) add(meta, el("span", "mf-pill mf-pill-aco", "ACO interne"));
    var days = d.premierJour ? fmtRange(d.premierJour, d.dernierJour) : fmtRange(ev.debut, ev.fin);
    add(meta, el("span", "mf-meta-item mf-mono", days));
    if (ev.personnes) add(meta, el("span", "mf-meta-item", ev.personnes + " pers."));
    if (ev.client) add(meta, el("span", "mf-meta-item", ev.client));
    var team = (ev.equipe || []).map(function (s) { return s.nom.replace(/^ACO (SE|PROD) /, ""); });
    if (team.length) add(meta, el("span", "mf-meta-item", "Suivi : " + team.join(", ")));
    if (d.resume.pilotes && d.resume.pilotes.length) add(meta, el("span", "mf-meta-item", "Pilote : " + d.resume.pilotes.join(", ")));
    var cd = state.head.querySelector(".mf-countdown");
    cd.textContent = "";
    var first = d.premierJour || ev.debut;
    if (first) {
      var today = isoToday();
      var last = d.dernierJour || ev.fin || first;
      var label = today >= first && today <= last ? "en cours" : relDays(first);
      add(cd, el("small", null, today > last ? "terminé" : (today >= first ? "" : "début")), el("b", null, label));
    }
  }

  // ------------------------------------------------------------- alertes + tuiles
  function renderAlertes(d, body) {
    if (!d.alertes || !d.alertes.length) return;
    var box = el("div", "mf-alerts");
    d.alertes.forEach(function (a) {
      var row = el("div", "mf-alert mf-al-" + a.niveau);
      if (a.type === "stock") {
        add(row, icon("inventory_2"), add(el("p"),
          el("b", null, fmtDay(a.date) + " · " + a.article),
          " : " + qty(a.demande) + " demandés pour " + a.stock + " en stock" +
          (a.evenements && a.evenements.length ? " (" + a.evenements.join(", ") + ")" : "") + "."));
      } else {
        add(row, icon(a.type === "plan" ? "map" : a.type === "horaires" ? "schedule" : "engineering"), el("p", null, a.texte));
      }
      add(box, row);
    });
    add(body, box);
  }

  function tile(cls, k, v, sub, iconName) {
    var t = el("div", "mf-tile " + (cls || ""));
    add(t, add(el("span", "mf-tile-k"), icon(iconName), k), el("span", "mf-tile-v", v), sub ? el("span", "mf-tile-d", sub) : null);
    return t;
  }

  function peopleText(fam, d) {
    var list = d.personnel.filter(function (p) { return p.famille === fam; });
    if (!list.length) return null;
    var n = 0, units = false;
    list.forEach(function (p) { if (p.unite === "Jour" || p.unite === "Heure") units = true; else n += p.nombre || 0; });
    var v = n ? String(n) : (units ? qty(list.reduce(function (s, p) { return s + (p.nombre || 0); }, 0)) + " j" : String(list.length));
    var withH = list.filter(function (p) { return Object.keys(p.presences || {}).length; });
    var sub;
    if (withH.length) {
      var p0 = withH[0];
      sub = fmtDay(p0.debut) + ", " + p0.debut.slice(11, 16) + " → " + p0.fin.slice(11, 16) + (p0.lieu ? " · " + p0.lieu : "");
      if (withH.length > 1) sub += " (+" + (withH.length - 1) + ")";
    } else {
      sub = "heures à confirmer";
    }
    return { v: v, sub: sub, warn: withH.length < list.length };
  }

  function renderTiles(d, body) {
    var grid = el("div", "mf-tiles");
    var secu = peopleText("securite", d), ssiap = peopleText("ssiap", d);
    if (secu || ssiap) {
      var v = (secu ? secu.v + " agent" + (secu.v === "1" ? "" : "s") : "") + (secu && ssiap ? " · " : "") + (ssiap ? ssiap.v + " SSIAP" : "");
      add(grid, tile("mf-t-secu" + ((secu && secu.warn) || (ssiap && ssiap.warn) ? " mf-t-warn" : ""), "Sécurité", v,
        (secu || ssiap).sub, "shield_person"));
    } else {
      add(grid, tile("mf-t-empty", "Sécurité", "aucune", "aucun agent ni SSIAP commandé", "shield_person"));
    }
    var reg = peopleText("regie", d);
    var pil = (d.resume.pilotes || []).join(", ");
    add(grid, reg ? tile("mf-t-regie" + (reg.warn ? " mf-t-warn" : ""), "Régie", reg.v, (pil ? pil + " · " : "") + reg.sub, "tune")
      : tile(pil ? "mf-t-regie" : "mf-t-empty", "Régie", pil || "aucune", pil ? "pilote saisi sur les espaces" : "aucun régisseur commandé", "tune"));
    var acc = peopleText("accueil", d);
    if (acc) add(grid, tile("mf-t-accueil", "Accueil", acc.v, acc.sub, "badge"));
    var nbArt = d.resume.materiel.lignes;
    var top = [];
    d.materiel.forEach(function (g) { g.lignes.forEach(function (l) { top.push(l); }); });
    top.sort(function (a, b) { return (b.quantite || 0) - (a.quantite || 0); });
    add(grid, tile(nbArt ? "" : "mf-t-empty", "Matériel", nbArt + " ligne" + (nbArt > 1 ? "s" : ""),
      top.slice(0, 2).map(function (l) { return qty(l.quantite) + " " + l.nom; }).join(", ") || "rien de commandé", "inventory_2"));
    var plans = d.resume.documents.plans + d.resume.documents.etatsDesLieux;
    add(grid, tile(plans ? "" : "mf-t-warn", "Plans", String(plans), plans ? d.documents.length + " document(s)" : "aucun plan dans Momentus", "map"));
    add(body, grid);
  }

  // ------------------------------------------------------------- espaces
  function renderEspaces(d, body) {
    if (!d.espaces.length) return;
    var days = d.jours.length ? d.jours : [];
    if (!days.length) return;
    var s = section("Espaces, phases et pilote", "meeting_room", d.espaces.length + " espace" + (d.espaces.length > 1 ? "s" : ""));
    var wrap = el("div", "mf-scroll");
    var grid = el("div", "mf-gantt");
    grid.style.setProperty("--mf-days", String(days.length));
    add(grid, el("span", "mf-gh"));
    days.forEach(function (j) { add(grid, el("span", "mf-gh mf-mono" + (j === isoToday() ? " mf-today" : ""), fmtDay(j))); });
    d.espaces.forEach(function (e) {
      var lab = el("span", "mf-glab", e.nom);
      lab.title = e.nom;
      add(grid, lab);
      days.forEach(function (j) {
        var cell = el("div", "mf-gcell");
        e.segments.forEach(function (sg) {
          if (j < sg.debut || j > sg.fin) return;
          var chip = el("div", "mf-seg mf-ph-" + sg.phase);
          var line = sg.pilote || PHASES[sg.phase] || sg.phase;
          add(chip, el("b", null, line));
          var h = sg.journee ? "" : hours(sg.heureDebut, sg.heureFin);
          if (h || sg.statut === "tentative") add(chip, el("span", null, [h, sg.statut === "tentative" ? "option" : ""].filter(Boolean).join(" · ")));
          chip.title = (PHASES[sg.phase] || sg.phase) + (sg.pilote ? " · " + sg.pilote : "") + (h ? " · " + h : "");
          add(cell, chip);
        });
        add(grid, cell);
      });
    });
    add(wrap, grid);
    var leg = el("div", "mf-legend");
    ["montage", "exploitation", "demontage", "reserve"].forEach(function (p) {
      add(leg, add(el("span"), el("i", "mf-ph-" + p), PHASES[p]));
    });
    add(leg, el("span", "mf-muted", "Le texte du bloc est le pilote saisi dans Momentus."));
    add(s, wrap, leg);
    add(body, s);
  }

  // ------------------------------------------------------------- personnel
  function renderPersonnel(d, body) {
    if (!d.personnel.length) return;
    var s = section("Personnel commandé", "groups", d.personnel.length + " commande" + (d.personnel.length > 1 ? "s" : ""));
    var days = [];
    d.personnel.forEach(function (p) { (p.jours || []).forEach(function (j) { if (days.indexOf(j) < 0) days.push(j); }); });
    days.sort();
    var chips = el("div", "mf-days");
    chips.setAttribute("role", "group");
    chips.setAttribute("aria-label", "Jour affiché");
    var host = el("div");
    var today = isoToday();
    var current = days.indexOf(today) >= 0 ? today : (days.filter(function (j) { return j >= today; })[0] || days[0]);
    function draw(day) {
      chips.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.day === day)); });
      host.textContent = "";
      add(host, personnelGrid(d, day));
    }
    days.forEach(function (j) {
      var b = el("button", "mf-day", fmtDay(j));
      b.type = "button";
      b.dataset.day = j;
      b.addEventListener("click", function () { draw(j); });
      add(chips, b);
    });
    add(s, chips, host);
    add(body, s);
    if (current) draw(current);
  }

  function personnelGrid(d, day) {
    var rows = d.personnel.filter(function (p) { return (p.jours || []).indexOf(day) >= 0; });
    var timed = rows.filter(function (p) { return p.presences && p.presences[day]; });
    var untimed = rows.filter(function (p) { return !(p.presences && p.presences[day]); });
    var frag = document.createDocumentFragment();
    if (timed.length) {
      var lo = 48, hi = 0;
      timed.forEach(function (p) {
        p.presences[day].forEach(function (n, i) { if (n) { lo = Math.min(lo, i); hi = Math.max(hi, i); } });
      });
      lo = Math.max(0, Math.min(lo - 2, 14));          // au plus tard 07:00, deux plages de marge
      hi = Math.min(47, Math.max(hi + 2, 43));         // au moins jusqu'a 22:00
      lo -= lo % 2; if (hi % 2 === 0) hi += 1;
      var n = hi - lo + 1;
      var wrap = el("div", "mf-scroll");
      var grid = el("div", "mf-pres");
      grid.style.setProperty("--mf-slots", String(n));
      add(grid, el("span"));
      for (var i = lo; i <= hi; i += 2) add(grid, el("span", "mf-ph mf-mono", slotLabel(i).slice(0, 2) + "h"));
      var total = new Array(48).fill(0);
      timed.forEach(function (p) {
        var lab = el("div", "mf-plab");
        add(lab, el("b", null, p.metier), el("span", null, [p.lieu, p.debut ? p.debut.slice(11, 16) + " → " + p.fin.slice(11, 16) : null].filter(Boolean).join(" · ")));
        lab.title = [p.metier, p.categorie, p.fonction, p.note].filter(Boolean).join("\n");
        add(grid, lab);
        var arr = p.presences[day];
        for (var k = lo; k <= hi; k++) {
          var c = el("i", "mf-c" + (arr[k] ? " mf-f-" + p.famille : ""), arr[k] ? String(arr[k]) : "");
          c.title = slotLabel(k) + " : " + (arr[k] || 0) + " " + p.metier;
          total[k] += arr[k] || 0;
          add(grid, c);
        }
      });
      add(grid, el("span", "mf-psum", "Sur site"));
      for (var t = lo; t <= hi; t++) add(grid, el("i", "mf-c mf-c-sum", total[t] ? String(total[t]) : ""));
      add(wrap, grid);
      add(frag, wrap);
    }
    if (untimed.length) {
      var list = el("ul", "mf-untimed");
      untimed.forEach(function (p) {
        var li = el("li", "mf-f-border-" + p.famille);
        var head = add(el("div", "mf-ut-head"), el("b", null, p.metier),
          el("span", "mf-mono", qty(p.nombre) + " " + (p.unite || "")),
          el("span", "mf-tag-warn", p.horaires === "fonction" ? "heures de la fonction" : "heures à confirmer"));
        add(li, head);
        var meta = [p.lieu, p.fonction, p.package].filter(Boolean).join(" · ");
        if (meta) add(li, el("span", "mf-muted", meta));
        if (p.note) add(li, add(el("p", "mf-note"), icon("sticky_note_2"), p.note));
        add(list, li);
      });
      add(frag, add(el("p", "mf-sub"), "Sans heures dans Momentus : la note dit souvent quand et combien."), list);
    }
    if (timed.some(function (p) { return p.note; })) {
      var notes = el("ul", "mf-notes");
      timed.filter(function (p) { return p.note; }).forEach(function (p) {
        add(notes, add(el("li"), el("b", null, p.metier + " : "), p.note));
      });
      add(frag, notes);
    }
    return frag;
  }

  // ------------------------------------------------------------- programme
  function renderProgramme(d, body) {
    if (!d.programme.length) return;
    var s = section("Programme", "event_note", d.programme.length + " créneau" + (d.programme.length > 1 ? "x" : ""));
    var byDay = {};
    d.programme.forEach(function (f) { (byDay[f.date] = byDay[f.date] || []).push(f); });
    var cols = el("div", "mf-prog");
    Object.keys(byDay).sort().forEach(function (j) {
      var col = add(el("div", "mf-prog-day"), el("h4", null, fmtDay(j)));
      var ul = el("ul");
      byDay[j].forEach(function (f) {
        add(ul, add(el("li"), el("span", "mf-mono mf-prog-h", f.heureDebut ? hours(f.heureDebut, f.heureFin) : "journée"),
          add(el("span"), el("b", null, f.nom), (f.lieu || f.personnes) ? el("small", null, [f.lieu, f.personnes ? f.personnes + " pers." : null].filter(Boolean).join(" · ")) : null)));
      });
      add(col, ul);
      add(cols, col);
    });
    add(s, cols);
    add(body, s);
  }

  // ------------------------------------------------------------- materiel
  function renderMateriel(d, body) {
    if (!d.materiel.length) return;
    var s = section("Bon de préparation du matériel", "inventory_2", d.resume.materiel.lignes + " ligne" + (d.resume.materiel.lignes > 1 ? "s" : ""));
    var stockKeys = {};
    (d.alertes || []).forEach(function (a) { if (a.type === "stock") stockKeys[a.article + "|" + a.date] = a; });
    var grid = el("div", "mf-pick");
    d.materiel.forEach(function (g) {
      var box = el("div", "mf-grp" + (g.type === "foodAndBeverage" ? " mf-grp-fb" : ""));
      add(box, add(el("h4"), el("span", null, g.categorie), el("span", "mf-mono", String(g.lignes.length))));
      var ul = el("ul");
      g.lignes.forEach(function (l) {
        var over = (l.jours || []).some(function (j) { return stockKeys[l.nom + "|" + j]; });
        var li = el("li", over ? "mf-over" : "");
        var txt = add(el("span"), l.nom);
        var meta = [l.lieu || l.fonction, l.jours && l.jours.length ? (l.jours.length > 1 ? fmtRange(l.jours[0], l.jours[l.jours.length - 1]) : fmtDay(l.jours[0])) : null,
          l.heureDebut ? hours(l.heureDebut, l.heureFin) : null].filter(Boolean).join(" · ");
        if (meta) add(txt, el("small", null, meta));
        if (l.note) add(txt, el("small", "mf-note-inline", l.note));
        if (over) add(txt, el("small", "mf-over-txt", "stock dépassé ce jour-là"));
        add(li, el("span", "mf-q mf-mono", qty(l.quantite)), txt);
        add(ul, li);
      });
      add(box, ul);
      add(grid, box);
    });
    add(s, grid);
    add(body, s);
  }

  // ------------------------------------------------------------- documents
  function renderDocuments(d, body) {
    var s = section("Plans et états des lieux", "map", d.documents.length ? String(d.documents.length) : "");
    if (!d.documents.length) {
      add(s, el("p", "mf-muted", "Aucun plan ni état des lieux déposé dans Momentus pour cet événement."));
      add(body, s);
      return;
    }
    var list = el("div", "mf-docs");
    d.documents.forEach(function (doc) {
      var a = el(doc.disponible || doc.lien ? "a" : "div", "mf-doc");
      if (doc.disponible) { a.href = "/api/momentus/document/" + encodeURIComponent(doc.id); a.target = "_blank"; a.rel = "noopener"; }
      else if (doc.lien && /^https:\/\//i.test(doc.lien)) { a.href = doc.lien; a.target = "_blank"; a.rel = "noopener noreferrer"; }
      add(a, icon(/\.pdf$/i.test(doc.nom) ? "picture_as_pdf" : /\.(png|jpe?g|gif|webp)$/i.test(doc.nom) ? "image" : "description"),
        add(el("span"), el("b", null, doc.nom), el("small", null, [doc.dossier, doc.date ? fmtDay(doc.date.slice(0, 10)) : null,
          doc.taille ? Math.max(1, Math.round(doc.taille / 1024)) + " Ko" : null, doc.disponible || doc.lien ? null : "fichier indisponible"].filter(Boolean).join(" · "))));
      add(list, a);
    });
    add(s, list);
    add(body, s);
  }

  // ------------------------------------------------------------- rendu
  function render(d) {
    var body = state.body;
    body.textContent = "";
    renderHead(d);
    renderAlertes(d, body);
    renderTiles(d, body);
    renderEspaces(d, body);
    renderPersonnel(d, body);
    renderProgramme(d, body);
    renderMateriel(d, body);
    renderDocuments(d, body);
    var foot = el("p", "mf-foot", "Information Momentus, en lecture : Momentus fait foi. Ni montants ni contacts." +
      (d.synced_at ? " Données du " + new Date(d.synced_at).toLocaleString("fr-FR") + "." : ""));
    add(body, foot);
  }

  function open(eventId, opener) {
    if (!/^event-[0-9]{1,9}-[A-Z]{1,3}$/.test(String(eventId || ""))) return;
    close();
    var sh = shell();
    state = { root: sh.root, title: sh.title, head: sh.head, body: sh.body, opener: opener || null, id: eventId };
    document.body.appendChild(sh.root);
    document.addEventListener("keydown", onKey, true);
    add(sh.body, el("p", "mf-loading", "Chargement de la fiche…"));
    sh.modal.querySelector(".mf-close").focus();
    var mine = state;
    fetch("/api/momentus/evenement/" + encodeURIComponent(eventId), { credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (state !== mine) return;
        if (!res.ok || !res.j || !res.j.ok) {
          var code = res.j && res.j.error;
          throw new Error(code === "evenement_inconnu" ? "événement introuvable dans Momentus" : (code || "erreur"));
        }
        render(res.j);
      })
      .catch(function (e) {
        if (state !== mine) return;
        state.body.textContent = "";
        add(state.body, el("p", "mf-loading", "Fiche indisponible (" + e.message + ")."));
      });
  }

  // Tout element data-mf-event ouvre la fiche (vignettes de la timeline,
  // fenetre d'un lieu). Capture : ni le tiroir ni le depliage de la vignette.
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest("[data-mf-event]");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    open(btn.getAttribute("data-mf-event"), btn);
  }, true);
  document.addEventListener("keydown", function (ev) {
    if ((ev.key === "Enter" || ev.key === " ") && ev.target.closest && ev.target.closest("[data-mf-event]")) ev.stopPropagation();
  }, true);

  window.MomentusFiche = { open: open, close: close };
})();
