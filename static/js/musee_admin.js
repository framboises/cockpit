/////////////////////////////////////////////////////////////////////////////////////////////////////
// MUSEE - onglet de configuration de la page /live-controle
// API : GET/PUT /api/musee/config, GET /api/musee/structure, POST /api/musee/test (musee_api.py).
// Independant du live controle (data_access, live_controle_actif) : la collecte musee
// (scripts/musee_collect.py) ne lit que cockpit_settings.musee.
/////////////////////////////////////////////////////////////////////////////////////////////////////

(function () {
  "use strict";

  var root = document.getElementById("musee-admin");
  if (!root) return;

  var JOURS = [["lun", "Lundi"], ["mar", "Mardi"], ["mer", "Mercredi"], ["jeu", "Jeudi"],
               ["ven", "Vendredi"], ["sam", "Samedi"], ["dim", "Dimanche"]];
  var TAB_KEY = "lc_tab";

  // --- DOM ---
  var evtGrid = document.getElementById("main-content");
  var tabs = document.querySelectorAll("[data-lc-tab]");
  var elEnabled = document.getElementById("ma-enabled");
  var elTx = document.getElementById("ma-transactions");
  var elEtat = document.getElementById("ma-etat");
  var elBadge = document.getElementById("ma-badge");
  var elToday = document.getElementById("ma-today");
  var elOuv = document.getElementById("ma-ouv");
  var elFerm = document.getElementById("ma-ferm");
  var elSemaine = document.querySelector("#ma-semaine tbody");
  var elExc = document.querySelector("#ma-exceptions tbody");
  var elExcAdd = document.getElementById("ma-exc-add");
  var elArea = document.getElementById("ma-area");
  var elGates = document.getElementById("ma-gates");
  var elCps = document.querySelector("#ma-cps tbody");
  var elCpAdd = document.getElementById("ma-cp-add");
  var elSource = document.getElementById("ma-struct-source");
  var elRefresh = document.getElementById("ma-struct-refresh");
  var elPerime = document.getElementById("ma-perime");
  var elStatuts = document.getElementById("ma-statuts");
  var elStatutsHint = document.getElementById("ma-statuts-hint");
  var elSave = document.getElementById("ma-save");
  var elReload = document.getElementById("ma-reload");
  var elTest = document.getElementById("ma-test");
  var elTestRes = document.getElementById("ma-test-result");
  var elSiteEnabled = document.getElementById("ma-site-enabled");
  var elSiteMarge = document.getElementById("ma-site-marge");
  var elSiteCps = document.querySelector("#ma-site-cps tbody");
  var elSiteAdd = document.getElementById("ma-site-cp-add");
  var elSiteJours = document.getElementById("ma-site-jours");
  var elSiteEtat = document.getElementById("ma-site-etat");
  var elSiteToday = document.getElementById("ma-site-today");
  var SENS = [["mixte", "Entree et sortie"], ["entree", "Entree"], ["sortie", "Sortie"]];
  var siteCps = [];           // [{id, nom, libelle, inclus, sens}] (ordre affiche)

  // --- Etat ---
  var loaded = false;
  var server = null;          // reponse GET /api/musee/config
  var structure = null;       // reponse GET /api/musee/structure
  var cpState = {};           // id -> {id, nom, libelle, inclus, mobile, info}
  var gateState = {};         // id -> {id, nom, coche}
  var dirty = false;

  // =========================================================
  //  Utilitaires
  // =========================================================

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function icon(name) { return el("span", "material-symbols-outlined", name); }
  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  function api(method, url, body) {
    var opts = { method: method, credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    if (method !== "GET") opts.headers["X-CSRFToken"] = csrf();
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "HTTP " + r.status }; })
        .then(function (d) { d = d || {}; d._status = r.status; return d; });
    });
  }
  function hh(v) { return v ? v.replace(":", "h") : "--"; }
  function ago(iso) {
    if (!iso) return "";
    var s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 90) return "il y a 1 min";
    if (s < 3600) return "il y a " + Math.round(s / 60) + " min";
    if (s < 172800) return "il y a " + Math.round(s / 3600) + " h";
    return "il y a " + Math.round(s / 86400) + " j";
  }
  function todayIso() {
    var d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function setDirty(v) {
    dirty = v;
    elSave.disabled = !v;
  }
  function timeInput(value) {
    var i = el("input", "hsh-select");
    i.type = "time"; i.step = 60;
    i.value = value || "";
    return i;
  }
  function select(options, value) {
    var s = el("select", "hsh-select");
    options.forEach(function (o) {
      var opt = el("option", null, o[1]);
      opt.value = o[0];
      s.appendChild(opt);
    });
    s.value = value;
    return s;
  }

  // =========================================================
  //  Onglets
  // =========================================================

  function showTab(name) {
    var musee = name === "musee";
    tabs.forEach(function (b) { b.classList.toggle("active", b.getAttribute("data-lc-tab") === name); });
    if (evtGrid) evtGrid.classList.toggle("lc-hidden", musee);
    root.hidden = !musee;
    try { localStorage.setItem(TAB_KEY, name); } catch (e) { /* stockage indisponible */ }
    if (musee && !loaded) {
      loaded = true;
      loadAll();
    }
  }
  tabs.forEach(function (b) {
    b.addEventListener("click", function () { showTab(b.getAttribute("data-lc-tab")); });
  });

  // =========================================================
  //  Chargement
  // =========================================================

  function loadAll() {
    loadConfig().then(function () { loadStructure(false); });
  }

  function loadConfig() {
    return api("GET", "/api/musee/config").then(function (d) {
      if (!d.ok) { showToast("error", "Configuration du musee indisponible"); return; }
      server = d;
      fillForm(d.config);
      renderEtat();
      renderSiteEtat();
      setDirty(false);
    }).catch(function () { showToast("error", "Configuration du musee indisponible"); });
  }

  function loadStructure(refresh) {
    elSource.textContent = "Lecture de la structure Handshake...";
    elRefresh.disabled = true;
    return api("GET", "/api/musee/structure" + (refresh ? "?refresh=1" : "")).then(function (d) {
      elRefresh.disabled = false;
      if (!d.ok) {
        elSource.textContent = "Structure indisponible : seul le perimetre enregistre est affiche.";
        structure = null;
      } else {
        structure = d;
        elSource.textContent = "Source : " + d.source_detail +
          (d.editions && d.editions.length ? " - relations : archives " + d.editions.join(", ") : "");
      }
      fillAreas();
      renderPerimeter();
      renderSite();
    }).catch(function () {
      elRefresh.disabled = false;
      elSource.textContent = "Structure indisponible.";
    });
  }

  // =========================================================
  //  Formulaire
  // =========================================================

  function fillForm(c) {
    c = c || { enabled: true, transactions: true, horaires: { semaine: {}, exceptions: [] },
               area: null, gates: [], checkpoints: [],
               releve_perime_min: server.defauts.releve_perime_min,
               statuts_passage: server.defauts.statuts_passage };
    elEnabled.checked = !!c.enabled;
    elTx.checked = !!c.transactions;
    var h = c.horaires || {};
    elOuv.value = h.ouverture || "";
    elFerm.value = h.fermeture || "";
    renderSemaine(h.semaine || {});
    clear(elExc);
    (h.exceptions || []).forEach(addException);
    elPerime.value = c.releve_perime_min;
    elStatuts.value = (c.statuts_passage || []).join(", ");
    elStatutsHint.textContent = "Par defaut : " + server.defauts.statuts_passage.join(", ") +
      " (OK, hors ligne, entree sans sortie : ce que comptent les compteurs Area Skidata). Les refus ne comptent pas.";

    cpState = {};
    (c.checkpoints || []).forEach(function (cp) {
      cpState[cp.id] = { id: cp.id, nom: cp.nom, libelle: cp.libelle || "", inclus: cp.inclus !== false,
                         mobile: !!cp.mobile, saved: true };
    });
    gateState = {};
    (c.gates || []).forEach(function (g) { gateState[g.id] = { id: g.id, nom: g.nom, coche: true }; });
    elArea.dataset.current = c.area ? c.area.id : "";
    elArea.dataset.currentNom = c.area ? c.area.nom : "";
    clear(elArea);              // l'Area enregistree prime sur une selection en cours
    fillAreas();
    renderPerimeter();

    var s = c.site || { enabled: false, marge_min: server.defauts.site_marge_min, checkpoints: [] };
    elSiteEnabled.checked = !!s.enabled;
    elSiteMarge.value = s.marge_min;
    siteCps = (s.checkpoints || []).map(function (cp) {
      return { id: cp.id, nom: cp.nom, libelle: cp.libelle || "", inclus: cp.inclus !== false, sens: cp.sens || "mixte" };
    });
    renderSite();
  }

  // --- Site - visites libres ---------------------------------------------------

  function cpAreas() {
    // checkpoint id -> noms des Areas sous lesquelles la structure le connait
    var map = {};
    ((structure && structure.areas) || []).forEach(function (a) {
      a.checkpoints.forEach(function (c) {
        (map[c.id] = map[c.id] || []).push(a.nom);
      });
    });
    return map;
  }

  function renderSite() {
    clear(elSiteCps);
    var areas = cpAreas();
    if (!siteCps.length) {
      var tr0 = el("tr"); var td0 = el("td", "ma-hint", "Aucun checkpoint : en ajouter un ci-dessous.");
      td0.colSpan = 6; tr0.appendChild(td0); elSiteCps.appendChild(tr0);
    }
    siteCps.forEach(function (st, idx) {
      var tr = el("tr");
      tr.classList.toggle("ma-off", !st.inclus);
      var inc = el("input"); inc.type = "checkbox"; inc.checked = st.inclus;
      inc.addEventListener("change", function () { st.inclus = inc.checked; tr.classList.toggle("ma-off", !st.inclus); });
      var td1 = el("td"); td1.appendChild(inc); tr.appendChild(td1);
      var td2 = el("td", "ma-cp-nom");
      td2.appendChild(el("div", null, st.nom));
      td2.appendChild(el("div", "ma-small", "id " + st.id));
      tr.appendChild(td2);
      var lib = el("input", "hsh-select"); lib.type = "text"; lib.maxLength = 60; lib.value = st.libelle;
      lib.placeholder = st.nom;
      lib.addEventListener("input", function () { st.libelle = lib.value; });
      var td3 = el("td"); td3.appendChild(lib); tr.appendChild(td3);
      var sens = select(SENS, st.sens);
      sens.addEventListener("change", function () { st.sens = sens.value; });
      var td4 = el("td"); td4.appendChild(sens); tr.appendChild(td4);
      var td5 = el("td", "ma-small", (areas[st.id] || []).join(", ") || (structure ? "hors structure" : ""));
      tr.appendChild(td5);
      var del = el("button", "ma-icon-btn"); del.type = "button"; del.title = "Retirer";
      del.appendChild(icon("delete"));
      del.addEventListener("click", function () { siteCps.splice(idx, 1); renderSite(); setDirty(true); });
      var td6 = el("td"); td6.appendChild(del); tr.appendChild(td6);
      elSiteCps.appendChild(tr);
    });

    while (elSiteAdd.options.length > 1) elSiteAdd.remove(1);
    elSiteAdd.disabled = !structure;
    if (structure) {
      var dans = {};
      siteCps.forEach(function (c) { dans[c.id] = true; });
      (structure.checkpoints || []).forEach(function (c) {
        if (dans[c.id]) return;
        var a = areas[c.id];
        var o = el("option", null, c.nom + " (" + c.id + ")" + (a ? " - " + a.join(", ") : "") +
          (c.borne ? "" : " - absent de la borne"));
        o.value = c.id;
        elSiteAdd.appendChild(o);
      });
    }
  }

  elSiteAdd.addEventListener("change", function () {
    var id = elSiteAdd.value;
    if (!id) return;
    var c = ((structure && structure.checkpoints) || []).filter(function (x) { return x.id === id; })[0];
    siteCps.push({ id: id, nom: c ? c.nom : id, libelle: "", inclus: true, sens: "mixte" });
    renderSite();
    setDirty(true);
  });

  function jourLabel(iso) {
    var p = iso.split("-");
    var d = new Date(+p[0], +p[1] - 1, +p[2]);
    return d.toLocaleDateString("fr-FR", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" });
  }

  function renderSiteEtat() {
    var s = (server && server.site) || {};
    clear(elSiteJours);
    var jours = s.jours || [];
    if (s.jours_erreur) {
      elSiteJours.appendChild(el("div", "ma-err", "Jours publics SAISON illisibles : " + s.jours_erreur));
    } else if (!jours.length) {
      elSiteJours.appendChild(el("div", "ma-hint", "Aucun jour de visites libres a venir sur SAISON."));
    } else {
      var dl = el("dl", "ma-etat");
      jours.forEach(function (j) {
        dl.appendChild(el("dt", null, jourLabel(j.date)));
        var txt = j.is24h ? "24h/24" : hh(j.ouverture) + " - " + hh(j.fermeture);
        var dd = el("dd", j.epreuve ? "ma-err" : null,
          txt + (j.epreuve ? " - epreuve " + j.epreuve + " en cours : non compte" : ""));
        dl.appendChild(dd);
      });
      elSiteJours.appendChild(dl);
    }
    clear(elSiteEtat);
    var dl2 = el("dl", "ma-etat");
    function row(k, v, cls) { dl2.appendChild(el("dt", null, k)); dl2.appendChild(el("dd", cls || null, v)); }
    row("Derniere collecte site", s.derniere_collecte_heure || "aucune");
    row("Dernier releve site", s.dernier_releve_heure || "aucun");
    if (s.derniere_erreur) row("Derniere erreur", s.derniere_erreur, "ma-err");
    elSiteEtat.appendChild(dl2);
    var t = s.aujourdhui;
    elSiteToday.textContent = t ? "Aujourd'hui : " + t.libelle +
      (t.fenetre && t.collecte ? " (comptage " + hh(t.fenetre.debut) + " - " + hh(t.fenetre.fin) + ")" : "") : "";
  }

  function renderSemaine(sem) {
    clear(elSemaine);
    JOURS.forEach(function (j) {
      var v = sem[j[0]];
      var mode = !v ? "defaut" : (v.ferme ? "ferme" : "horaires");
      var tr = el("tr");
      tr.dataset.jour = j[0];
      tr.appendChild(el("td", "ma-day", j[1]));
      var s = select([["defaut", "Horaires par defaut"], ["horaires", "Horaires specifiques"], ["ferme", "Ferme"]], mode);
      s.className += " ma-mode";
      var tdS = el("td"); tdS.appendChild(s); tr.appendChild(tdS);
      var o = timeInput(v && !v.ferme ? v.ouverture : ""); o.className += " ma-o";
      var f = timeInput(v && !v.ferme ? v.fermeture : ""); f.className += " ma-f";
      var tdO = el("td"); tdO.appendChild(o); tr.appendChild(tdO);
      var tdF = el("td"); tdF.appendChild(f); tr.appendChild(tdF);
      function sync() {
        var on = s.value === "horaires";
        o.disabled = f.disabled = !on;
        if (on && !o.value) o.value = elOuv.value;
        if (on && !f.value) f.value = elFerm.value;
      }
      s.addEventListener("change", sync);
      sync();
      elSemaine.appendChild(tr);
    });
  }

  function addException(e) {
    e = e || { date: "", ferme: true, libelle: "" };
    var tr = el("tr");
    var d = el("input", "hsh-select"); d.type = "date"; d.value = e.date || ""; d.className += " ma-date";
    var tdD = el("td"); tdD.appendChild(d); tr.appendChild(tdD);
    var s = select([["ferme", "Ferme"], ["horaires", "Horaires"]], e.ferme ? "ferme" : "horaires");
    s.className += " ma-mode";
    var tdS = el("td"); tdS.appendChild(s); tr.appendChild(tdS);
    var o = timeInput(e.ouverture); o.className += " ma-o";
    var f = timeInput(e.fermeture); f.className += " ma-f";
    var tdO = el("td"); tdO.appendChild(o); tr.appendChild(tdO);
    var tdF = el("td"); tdF.appendChild(f); tr.appendChild(tdF);
    var l = el("input", "hsh-select"); l.type = "text"; l.maxLength = 80; l.value = e.libelle || "";
    l.placeholder = "ex. Noel, nocturne"; l.className += " ma-lib";
    var tdL = el("td"); tdL.appendChild(l); tr.appendChild(tdL);
    var del = el("button", "ma-icon-btn"); del.type = "button"; del.title = "Supprimer";
    del.appendChild(icon("delete"));
    del.addEventListener("click", function () { tr.remove(); setDirty(true); });
    var tdX = el("td"); tdX.appendChild(del); tr.appendChild(tdX);
    function sync() {
      var on = s.value === "horaires";
      o.disabled = f.disabled = !on;
      if (on && !o.value) o.value = elOuv.value;
      if (on && !f.value) f.value = elFerm.value;
      tr.classList.toggle("ma-past", !!d.value && d.value < todayIso());
    }
    s.addEventListener("change", sync);
    d.addEventListener("change", sync);
    sync();
    elExc.appendChild(tr);
    return tr;
  }

  // --- Perimetre -------------------------------------------------------------

  function areaById(id) {
    if (!structure) return null;
    for (var i = 0; i < structure.areas.length; i++) {
      if (structure.areas[i].id === id) return structure.areas[i];
    }
    return null;
  }

  function fillAreas() {
    var cur = elArea.value || elArea.dataset.current || "";
    clear(elArea);
    var def = el("option", null, "-- Choisir l'Area du musee --");
    def.value = "";
    elArea.appendChild(def);
    var seen = false;
    var areas = structure ? structure.areas.slice() : [];
    // Areas qui ont des checkpoints d'abord : ce sont celles qu'on peut choisir utilement.
    areas.sort(function (a, b) {
      var x = a.checkpoints.length ? 0 : 1, y = b.checkpoints.length ? 0 : 1;
      return x - y || a.nom.localeCompare(b.nom);
    });
    areas.forEach(function (a) {
      var o = el("option", null, a.nom + " (" + a.id + ") - " + a.checkpoints.length + " checkpoint(s)" +
        (a.borne === false ? " - absente de la borne" : ""));
      o.value = a.id;
      if (a.id === cur) seen = true;
      elArea.appendChild(o);
    });
    if (cur && !seen) {
      var o2 = el("option", null, (elArea.dataset.currentNom || cur) + " (" + cur + ") - hors structure");
      o2.value = cur;
      elArea.appendChild(o2);
    }
    elArea.value = cur;
  }

  function renderPerimeter() {
    var aid = elArea.value;
    var area = areaById(aid);
    var sameAsSaved = aid && aid === (elArea.dataset.current || "");

    // Portes : celles de la structure + celles enregistrees (si meme Area).
    clear(elGates);
    var gates = {};
    if (area) area.gates.forEach(function (g) { gates[g.id] = { id: g.id, nom: g.nom, borne: g.borne }; });
    if (sameAsSaved) Object.keys(gateState).forEach(function (id) {
      if (!gates[id]) gates[id] = { id: id, nom: gateState[id].nom, borne: null };
    });
    var gids = Object.keys(gates);
    if (!gids.length) elGates.appendChild(el("span", "ma-hint", aid ? "Aucune porte connue pour cette Area." : "Choisir une Area."));
    gids.forEach(function (id) {
      var g = gates[id];
      if (!gateState[id]) gateState[id] = { id: id, nom: g.nom, coche: true };
      var lab = el("label");
      var cb = el("input"); cb.type = "checkbox"; cb.checked = gateState[id].coche;
      cb.addEventListener("change", function () { gateState[id].coche = cb.checked; setDirty(true); });
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(g.nom + " (" + id + ")"));
      if (g.borne === false) lab.appendChild(el("span", "ma-tag warn", "absente de la borne"));
      elGates.appendChild(lab);
    });
    elGates.dataset.ids = gids.join(",");

    // Checkpoints : ceux de la structure + ceux enregistres (si meme Area).
    clear(elCps);
    var rows = [];
    if (area) area.checkpoints.forEach(function (c) {
      var st = cpState[c.id];
      if (!st) {
        st = cpState[c.id] = { id: c.id, nom: c.nom, libelle: "", inclus: c.borne !== false,
                               mobile: !!c.mobile_suggere, saved: false };
      }
      st.nom = c.nom;
      rows.push({ st: st, info: c });
    });
    // Checkpoints enregistres que la structure ne rattache pas a cette Area :
    // gardes et signales, jamais perdus en silence.
    Object.keys(cpState).forEach(function (id) {
      var st = cpState[id];
      var garde = (sameAsSaved && st.saved) || (st.manuel === aid);
      if (aid && garde && !rows.some(function (r) { return r.st.id === id; })) {
        rows.push({ st: st, info: null });
      }
    });
    if (!rows.length) {
      var tr0 = el("tr"); var td0 = el("td", "ma-hint", aid ? "Aucun checkpoint connu pour cette Area." : "Choisir une Area.");
      td0.colSpan = 5; tr0.appendChild(td0); elCps.appendChild(tr0);
    }
    rows.forEach(function (row) {
      var st = row.st;
      var tr = el("tr");
      tr.dataset.id = st.id;
      tr.classList.toggle("ma-off", !st.inclus);
      var inc = el("input"); inc.type = "checkbox"; inc.checked = st.inclus;
      inc.addEventListener("change", function () { st.inclus = inc.checked; tr.classList.toggle("ma-off", !st.inclus); setDirty(true); });
      var td1 = el("td"); td1.appendChild(inc); tr.appendChild(td1);
      var td2 = el("td", "ma-cp-nom");
      td2.appendChild(el("div", null, st.nom));
      td2.appendChild(el("div", "ma-small", "id " + st.id));
      tr.appendChild(td2);
      var lib = el("input", "hsh-select"); lib.type = "text"; lib.maxLength = 60; lib.value = st.libelle;
      lib.placeholder = st.nom;
      lib.addEventListener("input", function () { st.libelle = lib.value; setDirty(true); });
      var td3 = el("td"); td3.appendChild(lib); tr.appendChild(td3);
      var mob = el("input"); mob.type = "checkbox"; mob.checked = st.mobile;
      mob.addEventListener("change", function () { st.mobile = mob.checked; setDirty(true); });
      var td4 = el("td"); td4.appendChild(mob); tr.appendChild(td4);
      var td5 = el("td");
      var info = row.info;
      if (!info) {
        td5.appendChild(el("span", st.manuel ? "ma-tag info" : "ma-tag warn", st.manuel ? "ajout manuel" : "hors structure"));
        if (st.borneInfo === false) td5.appendChild(el("span", "ma-tag warn", "absent de la borne"));
      } else {
        if (info.borne === false) td5.appendChild(el("span", "ma-tag warn", "absent de la borne"));
        if (info.mobile_suggere) {
          var t = el("span", "ma-tag info", "vu ailleurs");
          t.title = info.autres_areas.map(function (a) { return a.nom + " (" + a.editions.join(", ") + ")"; }).join(" ; ");
          td5.appendChild(t);
        }
        var eds = el("div", "ma-small", (info.editions || []).join(", "));
        td5.appendChild(eds);
      }
      tr.appendChild(td5);
      elCps.appendChild(tr);
    });
    elCps.dataset.ids = rows.map(function (r) { return r.st.id; }).join(",");

    // Ajout manuel : checkpoints de l'inventaire absents de la liste.
    while (elCpAdd.options.length > 1) elCpAdd.remove(1);
    elCpAdd.disabled = !aid || !structure;
    if (structure && aid) {
      var dans = {};
      rows.forEach(function (r) { dans[r.st.id] = true; });
      (structure.checkpoints || []).forEach(function (c) {
        if (dans[c.id]) return;
        var o = el("option", null, c.nom + " (" + c.id + ")" + (c.borne ? "" : " - absent de la borne"));
        o.value = c.id;
        elCpAdd.appendChild(o);
      });
    }
  }

  elCpAdd.addEventListener("change", function () {
    var id = elCpAdd.value;
    if (!id) return;
    var c = (structure.checkpoints || []).filter(function (x) { return x.id === id; })[0];
    var st = cpState[id];
    if (!st) st = cpState[id] = { id: id, nom: c ? c.nom : id, libelle: "", inclus: true, mobile: false, saved: false };
    st.manuel = elArea.value;
    st.inclus = true;
    st.borneInfo = c ? c.borne : null;
    renderPerimeter();
    setDirty(true);
  });

  elArea.addEventListener("change", function () {
    // Les reglages par checkpoint (cpState) sont gardes : revenir a l'Area
    // enregistree les retrouve. Seuls ceux de l'Area choisie sont envoyes.
    renderPerimeter();
    setDirty(true);
  });

  // =========================================================
  //  Etat de la collecte
  // =========================================================

  function renderEtat() {
    var d = server || {};
    var e = d.etat || {};
    clear(elEtat);
    var dl = el("dl", "ma-etat");
    function row(k, v, cls) {
      dl.appendChild(el("dt", null, k));
      dl.appendChild(el("dd", cls || null, v));
    }
    row("Derniere collecte", e.derniere_collecte_heure ? e.derniere_collecte_heure + " (" + ago(e.derniere_collecte) + ")" : "aucune");
    row("Dernier releve compteurs", e.dernier_releve_heure ? e.dernier_releve_heure + " (" + ago(e.dernier_releve) + ")" : "aucun");
    row("Derniere erreur", e.derniere_erreur || "aucune", e.derniere_erreur ? "ma-err" : null);
    var errs = e.erreurs_releve || {};
    var keys = Object.keys(errs);
    if (keys.length) row("Locations en echec", keys.map(function (k) { return k + " : " + errs[k]; }).join(" ; "), "ma-err");
    if (d.maj && d.maj.ts) row("Configuration", "modifiee le " + new Date(d.maj.ts).toLocaleString("fr-FR") + (d.maj.par ? " par " + d.maj.par : ""));
    elEtat.appendChild(dl);

    var cfg = d.config;
    var perimeMin = cfg ? cfg.releve_perime_min : 15;
    var age = e.dernier_releve ? (Date.now() - new Date(e.dernier_releve).getTime()) / 60000 : null;
    elBadge.className = "ma-badge";
    if (!d.existe || !d.configure) { elBadge.textContent = "A configurer"; elBadge.classList.add("warn"); }
    else if (!cfg.enabled) { elBadge.textContent = "Collecte desactivee"; }
    else if (age === null || age > perimeMin) { elBadge.textContent = "Collecte en retard"; elBadge.classList.add("warn"); }
    else { elBadge.textContent = "Collecte OK"; elBadge.classList.add("ok"); }

    var t = d.aujourdhui;
    if (t && t.horaires) {
      var h = t.horaires;
      elToday.textContent = "Aujourd'hui : " + (h.ferme ? "ferme" : hh(h.ouverture) + " - " + hh(h.fermeture)) +
        (h.source !== "defaut" ? " (" + (h.source === "exception" ? "exception" : "jour de semaine") + ")" : "");
    } else {
      elToday.textContent = "";
    }
  }

  // =========================================================
  //  Payload, enregistrement, test
  // =========================================================

  function buildPayload() {
    var semaine = {};
    elSemaine.querySelectorAll("tr").forEach(function (tr) {
      var mode = tr.querySelector(".ma-mode").value;
      if (mode === "ferme") semaine[tr.dataset.jour] = { ferme: true };
      else if (mode === "horaires") semaine[tr.dataset.jour] = {
        ouverture: tr.querySelector(".ma-o").value, fermeture: tr.querySelector(".ma-f").value };
    });
    var exceptions = [];
    elExc.querySelectorAll("tr").forEach(function (tr) {
      var mode = tr.querySelector(".ma-mode").value;
      var e = { date: tr.querySelector(".ma-date").value, libelle: tr.querySelector(".ma-lib").value.trim() };
      if (mode === "ferme") e.ferme = true;
      else { e.ferme = false; e.ouverture = tr.querySelector(".ma-o").value; e.fermeture = tr.querySelector(".ma-f").value; }
      exceptions.push(e);
    });
    var aid = elArea.value;
    var opt = elArea.options[elArea.selectedIndex];
    var area = areaById(aid);
    var gates = (elGates.dataset.ids ? elGates.dataset.ids.split(",") : []).filter(function (id) {
      return gateState[id] && gateState[id].coche;
    }).map(function (id) { return { id: id, nom: gateState[id].nom }; });
    var cps = (elCps.dataset.ids ? elCps.dataset.ids.split(",") : []).map(function (id) {
      var s = cpState[id];
      return { id: id, nom: s.nom, libelle: (s.libelle || "").trim(), inclus: !!s.inclus, mobile: !!s.mobile };
    });
    var statuts = elStatuts.value.split(/[\s,;]+/).filter(function (x) { return x !== ""; });
    var per = parseInt(elPerime.value, 10);
    var marge = parseInt(elSiteMarge.value, 10);
    return {
      site: {
        enabled: elSiteEnabled.checked,
        marge_min: isNaN(marge) ? null : marge,
        checkpoints: siteCps.map(function (s) {
          return { id: s.id, nom: s.nom, libelle: (s.libelle || "").trim(), inclus: !!s.inclus, sens: s.sens };
        })
      },
      enabled: elEnabled.checked,
      transactions: elTx.checked,
      horaires: { ouverture: elOuv.value, fermeture: elFerm.value, semaine: semaine, exceptions: exceptions },
      area: aid ? { id: aid, nom: area ? area.nom : (elArea.dataset.currentNom || (opt ? opt.textContent : aid)) } : null,
      gates: gates,
      checkpoints: cps,
      releve_perime_min: isNaN(per) ? null : per,
      statuts_passage: statuts
    };
  }

  function showErrors(d, prefix) {
    var list = d.erreurs || [];
    showToast("error", prefix + (list.length ? " : " + list.join(" ; ") : (d.error ? " (" + d.error + ")" : "")), 9000);
  }

  function save() {
    var payload = buildPayload();
    var wasEnabled = server && server.config ? !!server.config.enabled : null;
    var go = Promise.resolve(true);
    if (wasEnabled === true && !payload.enabled) {
      go = showConfirmToast("Desactiver la collecte du musee ? Le bloc de l'accueil sera masque.",
                            { okLabel: "Desactiver", cancelLabel: "Annuler" });
    }
    go.then(function (ok) {
      if (!ok) return;
      elSave.disabled = true;
      api("PUT", "/api/musee/config", payload).then(function (d) {
        if (!d.ok) { elSave.disabled = false; showErrors(d, "Configuration refusee"); return; }
        showToast("success", "Configuration du musee enregistree (prise en compte a la prochaine collecte, 5 min au plus)");
        loadConfig();
      }).catch(function () { elSave.disabled = false; showToast("error", "Enregistrement impossible"); });
    });
  }

  function renderTest(d) {
    clear(elTestRes);
    elTestRes.hidden = false;
    elTestRes.classList.toggle("ko", !d.ok);
    elTestRes.appendChild(el("div", null, (d.ok ? "Borne " + d.borne + " : reponse en " + d.duree_s + " s" : d.erreur || "Echec du test") +
      " (rien n'a ete enregistre)"));
    var comp = d.compteurs || {};
    var errs = d.erreurs || {};
    var ids = Object.keys(comp).concat(Object.keys(errs).filter(function (k) { return !comp[k]; }));
    if (ids.length) {
      var t = el("table");
      ids.forEach(function (id) {
        var tr = el("tr");
        var c = comp[id];
        tr.appendChild(el("td", null, id));
        tr.appendChild(el("td", null, c ? c.type + " " + c.nom : "echec"));
        tr.appendChild(el("td", "n", c ? "E " + (c.entries === null ? "--" : Number(c.entries).toLocaleString("fr-FR")) : errs[id]));
        t.appendChild(tr);
      });
      elTestRes.appendChild(t);
    }
    var sc = d.site_compteurs || {};
    var se = d.site_erreurs || {};
    var sids = Object.keys(sc).concat(Object.keys(se).filter(function (k) { return !sc[k]; }));
    if (sids.length) {
      elTestRes.appendChild(el("div", null, "Site - visites libres" +
        (d.site_gate ? " : " + d.site_gate.libelle : "")));
      var t2 = el("table");
      sids.forEach(function (id) {
        var tr = el("tr");
        var c = sc[id];
        function n(v) { return v === null || v === undefined ? "--" : Number(v).toLocaleString("fr-FR"); }
        tr.appendChild(el("td", null, id));
        tr.appendChild(el("td", null, c ? c.nom : "echec"));
        tr.appendChild(el("td", "n", c ? "E " + n(c.entries) + " / S " + n(c.exits) : se[id]));
        t2.appendChild(tr);
      });
      elTestRes.appendChild(t2);
    }
    var tx = d.transactions;
    if (tx) {
      var parts = Object.keys(tx.par_checkpoint || {}).map(function (k) {
        return (tx.par_checkpoint[k].nom || k) + " " + tx.par_checkpoint[k].n;
      });
      elTestRes.appendChild(el("div", null, "Passages " + tx.depuis + " - " + tx.jusqu_a + " : " + tx.lues +
        " transactions lues, " + tx.au_musee + " dans l'Area, " + tx.comptees + " comptees" +
        (tx.refus ? ", " + tx.refus + " refus" : "") + (tx.plafonne ? " (lecture partielle)" : "") +
        (parts.length ? " - " + parts.join(", ") : "")));
    } else if (d.ok) {
      elTestRes.appendChild(el("div", "ma-hint", "Passages non testes (collecte des transactions desactivee)."));
    }
    var ap = d.apercu_jour;
    if (ap && !ap.erreur) {
      var h = ap.horaires || {};
      elTestRes.appendChild(el("div", null, "Avec ces horaires, aujourd'hui : " +
        (h.ferme ? "ferme" : hh(h.ouverture) + " - " + hh(h.fermeture)) + " - " + ap.statut +
        " - visiteurs " + (ap.visiteurs === null ? "--" : ap.visiteurs) + (ap.source ? " (" + ap.source + ")" : "")));
    }
  }

  function test() {
    elTest.disabled = true;
    elTestRes.hidden = false;
    clear(elTestRes);
    elTestRes.appendChild(el("div", "ma-hint", "Interrogation de la borne..."));
    api("POST", "/api/musee/test", buildPayload()).then(function (d) {
      elTest.disabled = false;
      if (d._status === 400) { elTestRes.hidden = true; showErrors(d, "Formulaire invalide"); return; }
      renderTest(d);
    }).catch(function () {
      elTest.disabled = false;
      elTestRes.hidden = true;
      showToast("error", "Test impossible");
    });
  }

  // =========================================================
  //  Evenements
  // =========================================================

  root.addEventListener("input", function (e) {
    if (e.target.closest("#ma-cps")) return;     // gere par ligne
    setDirty(true);
  });
  root.addEventListener("change", function (e) {
    if (e.target === elArea || e.target.closest("#ma-cps") || e.target.closest("#ma-gates")) return;
    setDirty(true);
  });
  elExcAdd.addEventListener("click", function () {
    var tr = addException(null);
    tr.querySelector(".ma-date").focus();
    setDirty(true);
  });
  elRefresh.addEventListener("click", function () { loadStructure(true); });
  elSave.addEventListener("click", save);
  elTest.addEventListener("click", test);
  elReload.addEventListener("click", function () {
    if (!dirty) { loadConfig(); return; }
    showConfirmToast("Abandonner les modifications non enregistrees ?", { okLabel: "Abandonner", cancelLabel: "Garder" })
      .then(function (ok) { if (ok) loadConfig().then(function () { renderPerimeter(); }); });
  });

  // Onglet initial : #musee dans l'URL, sinon le dernier utilise.
  var initial = "evenement";
  if (location.hash === "#musee") initial = "musee";
  else {
    try { initial = localStorage.getItem(TAB_KEY) || "evenement"; } catch (e) { /* stockage indisponible */ }
  }
  showTab(initial === "musee" ? "musee" : "evenement");
})();
