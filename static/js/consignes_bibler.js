/* Consignes Bibler (/consignes-bibler) - bibler_postes.py
 *
 * Liste par etat (en vigueur, programmees, levees, expirees) des consignes
 * donnees aux postes, lue via le relais GET /api/bibler/consignes ; detail et
 * accuses (suivi.lignes) via GET /api/bibler/consignes/<id>. Modifier et lever
 * seulement avec le droit "Donner des consignes" (window.__cbCanPublish).
 * "Voir sur la carte" ouvre l'accueil (/?bibler=consigne:<id>).
 * Bibler est proprietaire des consignes : Cockpit ne fait que relayer.
 * Aucun innerHTML avec des donnees, aucun confirm()/alert().
 */
(function () {
  "use strict";

  var REFRESH_MS = 30000;
  var TITRE_MAX = 120;
  var TEXTE_MAX = 600;
  var ETATS = ["en_vigueur", "programmee", "levee", "expiree"];
  var LIBELLES_ETAT = { en_vigueur: "En vigueur", programmee: "Programmee", levee: "Levee", expiree: "Expiree" };
  var PRIORITES = [
    { valeur: "information", libelle: "Information" },
    { valeur: "importante", libelle: "Important" },
    { valeur: "urgente", libelle: "Urgent" }
  ];
  var RE_ID = /^[0-9a-fA-F]{24}$/;

  var canPublish = window.__cbCanPublish === true;
  var state = { etat: "en_vigueur", items: [], erreur: "", openId: null, detail: null, mode: null,
                note: null, sel: null, gen: 0 };

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function icon(name) { return el("span", "material-symbols-outlined", name); }
  function toast(type, msg) { if (window.showToast) window.showToast(type, msg); }
  function vider(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function classeSure(s) { return String(s || "").replace(/[^a-z_]/g, ""); }

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") || "" : "";
  }

  function lireJson(r) {
    return r.json().catch(function () { return null; }).then(function (corps) {
      return { statut: r.status, corps: corps || {} };
    });
  }

  function lire(url) {
    return fetch(url, { credentials: "same-origin" }).then(lireJson)
      .catch(function () { return { statut: 0, corps: {} }; });
  }

  function ecrire(methode, url, corps, rejoue) {
    return fetch(url, {
      method: methode, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(corps || {})
    }).then(lireJson).then(function (rep) {
      if (!rejoue && rep.statut === 400 && rep.corps && rep.corps.code === "csrf" && window.CockpitCsrf) {
        return window.CockpitCsrf.refresh().then(function (st) {
          return st && st.ok ? ecrire(methode, url, corps, true) : rep;
        }, function () { return rep; });
      }
      return rep;
    }).catch(function () { return { statut: 0, corps: {} }; });
  }

  function messageErreur(rep, ecriture) {
    var c = rep.corps || {};
    var code = c.erreur || c.code || "";
    if (code === "bibler_non_configure") return "Bibler n'est pas configure dans Cockpit.";
    if (code === "bibler_refus_service") {
      return "Bibler refuse l'acces de Cockpit : configuration du pont a verifier (administrateur).";
    }
    if (rep.statut === 0 || rep.statut === 502 || rep.statut === 503 || code === "bibler_indisponible") {
      return "Bibler n'a pas repondu : reessayez.";
    }
    if (code === "evenement_inconnu") return "Evenement inconnu de Bibler.";
    if (code === "droit_insuffisant") return "Droit insuffisant.";
    if (code === "csrf") return "Session expiree : rechargez la page.";
    if (ecriture && (rep.statut === 409 || code === "version_perimee")) {
      return "Consigne modifiee entre-temps : elle a ete rechargee.";
    }
    if (code === "consigne_levee") return c.message || "Consigne deja levee.";
    return c.message || c.error || ("Erreur Bibler (" + rep.statut + ")");
  }

  // ------------------------------------------------------------- evenement
  // Meme source que le reste de Cockpit : cockpit_sel (choix de l'accueil),
  // puis cockpit_event / cockpit_year, puis /api/event/current.
  function selectionLocale() {
    try {
      var s = JSON.parse(localStorage.getItem("cockpit_sel") || "null");
      if (s && s.event && s.year) return { event: s.event, year: String(s.year) };
    } catch (e) { /* stockage indisponible */ }
    try {
      var ev = localStorage.getItem("cockpit_event");
      var yr = localStorage.getItem("cockpit_year");
      if (ev && yr) return { event: ev, year: String(yr) };
    } catch (e2) { /* stockage indisponible */ }
    return null;
  }

  function resoudreSelection() {
    var loc = selectionLocale();
    if (loc) return Promise.resolve(loc);
    return lire("/api/event/current").then(function (rep) {
      var cur = rep.corps && rep.corps.current;
      if (cur && cur.event && cur.year) return { event: cur.event, year: String(cur.year) };
      return null;
    });
  }

  function qs() {
    return "event=" + encodeURIComponent(state.sel.event) + "&year=" + encodeURIComponent(state.sel.year);
  }

  // --------------------------------------------------------------- libelles
  function fmtDate(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return String(iso || "");
    return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" }) + " " +
      d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }
  function libellePeriode(c) {
    if (!c.debut && !c.fin) return "";
    var t = c.debut ? "Du " + fmtDate(c.debut) : "Depuis la publication";
    return t + (c.fin ? " au " + fmtDate(c.fin) : ", jusqu'a la levee");
  }
  function prioClasse(c) {
    return "cb-prio cb-prio-" + classeSure(c.priorite);
  }

  // ----------------------------------------------------------------- liste
  function compte(etat) {
    return state.items.filter(function (c) { return c.etat === etat; }).length;
  }

  function rendreOnglets() {
    ETATS.forEach(function (e) {
      var b = $("cb-c-" + e);
      if (b) b.textContent = String(compte(e));
    });
    document.querySelectorAll("#cb-tabs .cb-seg-btn").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-etat") === state.etat);
    });
  }

  function rendreListe() {
    rendreOnglets();
    var zone = $("cb-list");
    vider(zone);
    if (state.erreur) {
      zone.appendChild(el("div", "cb-msg", state.erreur));
      return;
    }
    var lignes = state.items.filter(function (c) { return c.etat === state.etat; });
    if (!lignes.length) {
      zone.appendChild(el("div", "cb-vide", "Aucune consigne " + (LIBELLES_ETAT[state.etat] || "").toLowerCase() + "."));
      return;
    }
    lignes.forEach(function (c) {
      var b = el("button", "cb-item" + (c.id === state.openId ? " active" : ""));
      b.type = "button";
      var t = el("div", "cb-item-titre");
      t.appendChild(el("span", null, c.titre || ""));
      if (c.priorite_libelle) t.appendChild(el("span", prioClasse(c), c.priorite_libelle));
      b.appendChild(t);
      if (c.cible_libelle) b.appendChild(el("div", "cb-item-meta", c.cible_libelle));
      var s = c.suivi;
      if (s) {
        b.appendChild(el("div", "cb-item-taux", "Lu " + (s.part_lue || 0) + " % - transmis " +
          (s.part_transmise || 0) + " %" + (s.total ? " (" + s.total + " encadrant(s))" : "")));
      }
      b.addEventListener("click", function () { ouvrir(c.id); });
      zone.appendChild(b);
    });
  }

  function charger() {
    if (!state.sel) return Promise.resolve();
    return lire("/api/bibler/consignes?" + qs()).then(function (rep) {
      var liste = rep.statut === 200 && rep.corps && rep.corps.consignes;
      if (!Array.isArray(liste)) {
        state.erreur = messageErreur(rep, false);
        state.items = [];
      } else {
        state.erreur = "";
        state.items = liste.filter(function (c) { return c && c.id; });
      }
      rendreListe();
    });
  }

  // ---------------------------------------------------------------- detail
  function ouvrir(id, note) {
    if (!RE_ID.test(String(id))) return;
    state.openId = id;
    state.mode = null;
    state.note = note ? { texte: note, erreur: false } : null;
    var gen = ++state.gen;
    rendreListe();
    return lire("/api/bibler/consignes/" + encodeURIComponent(id)).then(function (rep) {
      if (gen !== state.gen) return;
      if (rep.statut === 200 && rep.corps && rep.corps.consigne) {
        state.detail = { c: rep.corps.consigne, suivi: rep.corps.suivi || null };
      } else {
        state.detail = { erreur: messageErreur(rep, false) };
      }
      rendreDetail();
    });
  }

  function section(titre) {
    var s = el("div", "cb-d-section");
    if (titre) s.appendChild(el("h4", null, titre));
    return s;
  }

  function sectionSuivi(suivi) {
    var s = section("Accuses de reception");
    if (!suivi) { s.appendChild(el("div", "cb-vide", "Suivi indisponible")); return s; }
    var total = suivi.total || 0;
    s.appendChild(el("div", "cb-d-meta", "Lu " + (suivi.lus || 0) + "/" + total + " (" + (suivi.part_lue || 0) +
      " %) - transmis " + (suivi.transmis || 0) + "/" + total + " (" + (suivi.part_transmise || 0) + " %)"));
    if (suivi.mauvaises_reponses) {
      s.appendChild(el("div", "cb-d-meta", suivi.mauvaises_reponses + " mauvaise(s) reponse(s) a la question de controle"));
    }
    var lignes = (suivi.lignes || []).map(function (l, i) { return { l: l, i: i }; });
    if (!lignes.length) { s.appendChild(el("div", "cb-vide", "Aucun encadrant concerne")); return s; }
    lignes.sort(function (a, b) { return (a.l.lu ? 1 : 0) - (b.l.lu ? 1 : 0) || a.i - b.i; });
    var ul = el("ul", "cb-suivi");
    lignes.forEach(function (x) {
      var l = x.l;
      var li = el("li", l.lu ? "" : "manque");
      var qui = el("span", "cb-qui");
      qui.appendChild(el("b", null, l.nom || "?"));
      qui.appendChild(el("small", null, l.cible === false ? "hors cible" : (l.perimetre || "")));
      li.appendChild(qui);
      var st = el("span", "cb-statuts");
      if (!l.lu) st.appendChild(el("span", "cb-statut cb-statut-manque", "Non lu"));
      else st.appendChild(el("span", "cb-statut", "Lu " + l.lu));
      if (l.valide) st.appendChild(el("span", "cb-statut cb-statut-ok", "Valide " + l.valide));
      if (l.transmis) {
        st.appendChild(el("span", "cb-statut cb-statut-ok", "Transmis " + l.transmis +
          (l.agents_informes ? " (" + l.agents_informes + " agent(s))" : "")));
      }
      if (l.mauvaises_reponses) {
        st.appendChild(el("span", "cb-statut cb-statut-manque", l.mauvaises_reponses + " mauvaise(s)"));
      }
      li.appendChild(st);
      ul.appendChild(li);
    });
    s.appendChild(ul);
    return s;
  }

  function rendreDetail() {
    var zone = $("cb-detail");
    vider(zone);
    var d = state.detail;
    if (!d) { zone.appendChild(el("div", "cb-vide", "Choisissez une consigne dans la liste.")); return; }
    if (d.erreur) { zone.appendChild(el("div", "cb-msg", d.erreur)); return; }
    var c = d.c;

    zone.appendChild(el("h3", "cb-d-titre", c.titre || ""));
    var ligne = el("div", "cb-d-ligne");
    ligne.appendChild(el("span", "cb-etat cb-etat-" + classeSure(c.etat), LIBELLES_ETAT[c.etat] || c.etat || ""));
    if (c.priorite_libelle) ligne.appendChild(el("span", prioClasse(c), c.priorite_libelle));
    if (c.modifiee) ligne.appendChild(el("span", "cb-etat", "version " + c.version));
    zone.appendChild(ligne);
    if (c.cible_libelle) zone.appendChild(el("div", "cb-d-meta", c.cible_libelle));
    if (c.texte) zone.appendChild(el("div", "cb-d-texte", c.texte));
    var meta = [c.auteur, c.heure].filter(Boolean).join(" - ");
    if (meta) zone.appendChild(el("div", "cb-d-meta", "Par " + meta));
    var periode = libellePeriode(c);
    if (periode) zone.appendChild(el("div", "cb-d-meta", periode));
    if (c.etat === "levee" && c.levee_heure) zone.appendChild(el("div", "cb-d-meta", "Levee a " + c.levee_heure));
    if (c.dans_briefing) zone.appendChild(el("div", "cb-d-meta", "Dans le briefing"));
    var qc = c.question_controle;
    if (qc && qc.question) zone.appendChild(el("div", "cb-d-meta", "Question de controle : " + qc.question));

    zone.appendChild(sectionSuivi(d.suivi));

    var actions = el("div", "cb-actions-zone");
    zone.appendChild(actions);
    rendreActions(actions, c);
  }

  function rendreActions(zone, c) {
    vider(zone);
    if (state.note) {
      zone.appendChild(el("div", state.note.erreur ? "cb-msg" : "cb-d-meta", state.note.texte));
    }
    var barre = el("div", "cb-actions");
    var carte = el("a", "cb-btn", "Voir sur la carte");
    carte.href = "/?bibler=consigne:" + encodeURIComponent(c.id);
    carte.insertBefore(icon("map"), carte.firstChild);
    barre.appendChild(carte);

    if (!canPublish || c.etat === "levee") { zone.appendChild(barre); return; }

    if (state.mode === "modifier") {
      zone.appendChild(formModifier(zone, c));
      return;
    }
    if (state.mode === "lever") {
      zone.appendChild(confirmLever(zone, c));
      return;
    }
    var bm = el("button", "cb-btn", "Modifier");
    bm.type = "button";
    bm.addEventListener("click", function () { state.mode = "modifier"; state.note = null; rendreActions(zone, c); });
    var bv = el("button", "cb-btn cb-btn-danger", "Lever");
    bv.type = "button";
    bv.addEventListener("click", function () { state.mode = "lever"; state.note = null; rendreActions(zone, c); });
    barre.appendChild(bm);
    barre.appendChild(bv);
    zone.appendChild(barre);
  }

  function champ(libelle, saisie, compteur) {
    var l = el("label", null, libelle);
    l.appendChild(saisie);
    if (compteur) l.appendChild(compteur);
    return l;
  }

  function formModifier(zone, c) {
    var form = el("form", "cb-form");
    form.noValidate = true;
    var titre = el("input");
    titre.type = "text";
    titre.maxLength = TITRE_MAX;
    titre.value = c.titre || "";
    form.appendChild(champ("Titre", titre));
    var texte = el("textarea");
    texte.maxLength = TEXTE_MAX;
    texte.rows = 4;
    texte.value = c.texte || "";
    var compteur = el("span", "cb-compteur");
    function maj() { compteur.textContent = texte.value.length + "/" + TEXTE_MAX; }
    texte.addEventListener("input", maj);
    maj();
    form.appendChild(champ("Texte", texte, compteur));
    var prio = el("select");
    PRIORITES.forEach(function (p) {
      var o = el("option", null, p.libelle);
      o.value = p.valeur;
      prio.appendChild(o);
    });
    prio.value = c.priorite || "information";
    form.appendChild(champ("Priorite", prio));
    form.appendChild(el("div", "cb-d-meta", "Pas de nom, de telephone ni d'e-mail dans la consigne."));
    var msg = el("div", "cb-msg");
    msg.setAttribute("role", "alert");
    msg.hidden = true;
    form.appendChild(msg);
    var barre = el("div", "cb-actions");
    var ok = el("button", "cb-btn cb-btn-principal", "Enregistrer");
    ok.type = "submit";
    var non = el("button", "cb-btn", "Annuler");
    non.type = "button";
    non.addEventListener("click", function () { state.mode = null; rendreActions(zone, c); });
    barre.appendChild(ok);
    barre.appendChild(non);
    form.appendChild(barre);
    var enCours = false;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (enCours) return;
      if (!titre.value.trim()) { msg.textContent = "Donnez un titre a la consigne."; msg.hidden = false; return; }
      enCours = true;
      ok.disabled = true;
      ok.textContent = "Enregistrement...";
      var corps = { titre: titre.value.trim(), texte: texte.value.trim(), priorite: prio.value, version: c.version };
      ecrire("PATCH", "/api/bibler/consignes/" + encodeURIComponent(c.id), corps).then(function (rep) {
        if (state.openId !== c.id) return;
        if (rep.statut === 200 && rep.corps && rep.corps.ok !== false) {
          toast("success", "Consigne modifiee");
          apresEcriture(c.id);
          return;
        }
        if (rep.statut === 409 || (rep.corps && rep.corps.erreur === "version_perimee")) {
          toast("error", messageErreur(rep, true));
          apresEcriture(c.id, messageErreur(rep, true));
          return;
        }
        enCours = false;
        ok.disabled = false;
        ok.textContent = "Enregistrer";
        msg.textContent = messageErreur(rep, true);
        msg.hidden = false;
      });
    });
    return form;
  }

  function confirmLever(zone, c) {
    var conf = el("div", "cb-confirm");
    conf.appendChild(el("div", null, "Lever cette consigne ? Elle ne sera plus diffusee aux encadrants."));
    var msg = el("div", "cb-msg");
    msg.setAttribute("role", "alert");
    msg.hidden = true;
    conf.appendChild(msg);
    var barre = el("div", "cb-actions");
    var oui = el("button", "cb-btn cb-btn-danger", "Confirmer la levee");
    oui.type = "button";
    var non = el("button", "cb-btn", "Annuler");
    non.type = "button";
    non.addEventListener("click", function () { state.mode = null; rendreActions(zone, c); });
    var enCours = false;
    oui.addEventListener("click", function () {
      if (enCours) return;
      enCours = true;
      oui.disabled = true;
      oui.textContent = "Levee...";
      ecrire("POST", "/api/bibler/consignes/" + encodeURIComponent(c.id) + "/lever", {}).then(function (rep) {
        if (state.openId !== c.id) return;
        if (rep.statut === 200 && rep.corps && rep.corps.ok !== false) {
          toast("success", "Consigne levee");
          apresEcriture(c.id);
          return;
        }
        var code = rep.corps && rep.corps.erreur;
        if (code === "consigne_levee" || rep.statut === 409) {
          apresEcriture(c.id, messageErreur(rep, true));
          return;
        }
        enCours = false;
        oui.disabled = false;
        oui.textContent = "Confirmer la levee";
        msg.textContent = messageErreur(rep, true);
        msg.hidden = false;
      });
    });
    barre.appendChild(oui);
    barre.appendChild(non);
    conf.appendChild(barre);
    return conf;
  }

  function apresEcriture(id, note) {
    // Le relais a vide son cache : la relecture voit l'ecriture tout de suite.
    charger();
    ouvrir(id, note);
  }

  // ----------------------------------------------------------------- debut
  function init() {
    document.querySelectorAll("#cb-tabs .cb-seg-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        state.etat = b.getAttribute("data-etat");
        rendreListe();
      });
    });
    $("cb-refresh").addEventListener("click", function () {
      charger();
      if (state.openId) ouvrir(state.openId);
    });
    resoudreSelection().then(function (sel) {
      if (!sel) {
        state.erreur = "Evenement courant introuvable.";
        rendreListe();
        return;
      }
      state.sel = sel;
      $("cb-event-label").textContent = sel.event + " " + sel.year;
      return charger();
    });
    setInterval(function () {
      if (!document.hidden && state.sel && !state.mode) charger();
    }, REFRESH_MS);
  }

  init();
})();
