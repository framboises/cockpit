/* Epreuve 4 : les patrouilles (maquette test_pcorg.html, RENDER.ops), nouveau plan.
 *
 * Moteur pur (section 1) : reproduit a l'identique formation/patrouilles.py, au
 * temps ecoule et jamais par accumulation de setInterval. Temps de jeu
 * tj = (performance.now() - t0) / coef ; incident ouvert a T = t * 1000, echeance
 * D = T + dl * 1000 ; engagement valide -> arrivee = tj + (cases du chemin) * PAS_MS,
 * traite si arrivee <= D, moyen libre a arrivee + SUR_PLACE_MS. Position d'un pion =
 * fonction pure (heure d'engagement, chemin, maintenant). Sous node, le moteur
 * s'exporte (module.exports) : tests/test_formation_patrouilles_js.py le compare au rejeu.
 *
 * Interface (section 2) : selection incident -> moyen ou moyen -> incident (reclic =
 * deselection), glisser-deposer (Pointer Events), touches 1 a 5, trajet en pointilles.
 * Envois : {type: "engager", moyen, incident, t_ms} pour un engagement valide ; pour un
 * moyen d'une autre specialite, le meme "engager" (le rejeu le compte en erreur, sans
 * autre effet) suivi d'un {type: "refus", moyen, incident, t_ms} (trace, non note).
 * Rien n'est envoye pour un moyen occupe ou un incident clos. */
(function () {
  "use strict";

  // ------------------------------------------------------------ 1. moteur pur

  var PAS_MS = 430, SUR_PLACE_MS = 4000;
  var VOISINS = [[1, 0], [-1, 0], [0, 1], [0, -1]];

  function cle(r, c) { return r + "," + c; }
  function propre(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }

  function lirePlan(p) {
    var trav = {};
    (p.TRAV || []).forEach(function (x) { trav[cle(x[0], x[1])] = true; });
    return {rw: p.RW, cl: p.CL, pass: (p.PASS || []).slice(), trav: trav};
  }

  // Piste en colonne 6 hors passerelles, et travaux (comme patrouilles.bloquee).
  function bloquee(pl, r, c) {
    if (r < 0 || c < 0 || r >= pl.rw || c >= pl.cl) return true;
    return (c === 6 && pl.pass.indexOf(r) < 0) || propre(pl.trav, cle(r, c));
  }

  // BFS, voisins dans l'ordre de VOISINS : meme chemin unique que le serveur.
  function chemin(pl, a, b) {
    var prev = {}, file = [[a[0], a[1]]], tete = 0;
    prev[cle(a[0], a[1])] = null;
    while (tete < file.length) {
      var c = file[tete++];
      if (c[0] === b[0] && c[1] === b[1]) {
        var out = [], x = c;
        while (x) { out.unshift(x); x = prev[cle(x[0], x[1])]; }
        return out;
      }
      for (var k = 0; k < VOISINS.length; k++) {
        var n = [c[0] + VOISINS[k][0], c[1] + VOISINS[k][1]], kn = cle(n[0], n[1]);
        if (propre(prev, kn) || bloquee(pl, n[0], n[1])) continue;
        prev[kn] = c;
        file.push(n);
      }
    }
    return null;
  }

  function creer(p, incidents) {
    var e = {plan: lirePlan(p), units: [], incs: [], u: {}, i: {}, erreurs: 0};
    (p.UNITS || []).forEach(function (x) {
      var u = {id: x.id, name: x.name, cls: x.cls, type: x.type, pos: [x.pos[0], x.pos[1]],
               libre_a: 0, trajet: null, depart: 0, arrivee: 0};
      e.units.push(u);
      e.u[u.id] = u;
    });
    (incidents || []).forEach(function (x) {
      var t = x.t * 1000;
      var inc = {id: x.id, type: x.type, txt: x.txt, zone: x.zone, pos: [x.pos[0], x.pos[1]],
                 T: t, D: t + x.dl * 1000, moyen: null, arrivee: null};
      e.incs.push(inc);
      e.i[inc.id] = inc;
    });
    return e;
  }

  // Un engagement a tMs (ms reelles depuis le debut) : memes controles, dans le meme
  // ordre, que patrouilles.rejouer. Seuls "ok" et "mauvais_type" changent l'etat.
  function engager(e, tMs, coef, uid, iid) {
    var inc = typeof iid === "string" && propre(e.i, iid) ? e.i[iid] : null;
    var u = typeof uid === "string" && propre(e.u, uid) ? e.u[uid] : null;
    if (!inc || !u) return "inconnu";
    var t = tMs / coef;
    if (t < inc.T) return "pas_ouvert";
    if (t >= inc.D) return "echu";
    if (inc.moyen !== null) return "attribue";
    if (t < u.libre_a) return "occupe";
    if (u.type !== inc.type) { e.erreurs++; return "mauvais_type"; }
    var trajet = chemin(e.plan, u.pos, inc.pos);
    if (!trajet) return "sans_chemin";
    var arrivee = t + (trajet.length - 1) * PAS_MS;
    inc.moyen = u.id;
    inc.arrivee = arrivee;
    u.trajet = trajet;
    u.depart = t;
    u.arrivee = arrivee;
    u.pos = [inc.pos[0], inc.pos[1]];
    u.libre_a = arrivee + SUR_PLACE_MS;
    return "ok";
  }

  // attente | ouvert | route | traite | manque, au temps de jeu tj.
  function etatIncident(inc, tj) {
    if (tj < inc.T) return "attente";
    if (inc.moyen !== null) {
      if (inc.arrivee <= inc.D) return tj < inc.arrivee ? "route" : "traite";
      return tj < inc.D ? "route" : "manque";
    }
    return tj < inc.D ? "ouvert" : "manque";
  }

  // route | site | dispo (dispo exactement quand engager ne repond plus "occupe").
  function phase(u, tj) {
    if (u.trajet && tj < u.arrivee) return "route";
    return tj < u.libre_a ? "site" : "dispo";
  }

  // Index de la derniere case atteinte sur le trajet en cours.
  function etape(u, tj) {
    return Math.max(0, Math.floor((tj - u.depart) / PAS_MS));
  }

  // Position [ligne, colonne] (fractionnaire en mouvement ; case par case si pasAPas).
  function position(u, tj, pasAPas) {
    if (!u.trajet || tj >= u.arrivee) return [u.pos[0], u.pos[1]];
    var k = Math.max(0, (tj - u.depart) / PAS_MS), i = Math.floor(k);
    var a = u.trajet[i], b = u.trajet[i + 1];
    if (!b || pasAPas) return [a[0], a[1]];
    var f = k - i;
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
  }

  function bilan(e) {
    var detail = e.incs.map(function (inc) {
      var ok = inc.moyen !== null && inc.arrivee <= inc.D;
      return {id: inc.id, statut: ok ? "traite" : "manque", moyen: inc.moyen, arrivee: inc.arrivee};
    });
    var traites = detail.filter(function (d) { return d.statut === "traite"; }).length;
    return {traites: traites, manques: detail.length - traites, erreurs: e.erreurs, detail: detail};
  }

  var Moteur = {PAS_MS: PAS_MS, SUR_PLACE_MS: SUR_PLACE_MS, lirePlan: lirePlan, chemin: chemin, creer: creer,
                engager: engager, etatIncident: etatIncident, phase: phase, position: position, bilan: bilan};

  if (typeof module === "object" && module && module.exports) {
    module.exports = Moteur;
    return;
  }

  // ------------------------------------------------------------ 2. interface

  var F = window.Formation;
  var SVGNS = "http://www.w3.org/2000/svg";
  var URGENT_MS = 8000;  // ms reelles restantes : signal visuel et sonore
  var COULEURS = {pat: "#2E8BF0", tan: "#8A6BE0", sec: "#E5534B", mot: "#1FA38A"};
  var LEGENDE = [["pat", "P", "Patrouille", "sécurité"], ["tan", "T", "Tango", "technique"],
                 ["sec", "S", "Secours", "blessés, malaises"], ["mot", "M", "Moto", "circulation, parkings"]];
  var DUREE_MAX = 110000;  // ms de jeu (correction.duree_max_ms, hors marge reseau)

  function regles() {
    return "<div class=\"rules\"><h3>Règles</h3><ul>" +
      "<li>Vous êtes PC Org : vous coordonnez les moyens sur le terrain. Un incident apparaît sur le plan avec " +
      "un compte à rebours.</li>" +
      "<li>Pour envoyer un moyen : cliquez sur l'incident puis sur le moyen, ou sur le moyen puis sur " +
      "l'incident. Vous pouvez aussi faire glisser le moyen sur l'incident. Un second clic annule la " +
      "sélection. Touches <b>1</b> à <b>5</b> : choisir un moyen, dans l'ordre de la liste.</li>" +
      "<li>Chaque moyen a sa spécialité : <b>Patrouille</b> sécurité, <b>Tango</b> technique, " +
      "<b>Secours</b> blessés et malaises, <b>Moto</b> circulation et parkings. Un moyen d'une autre " +
      "spécialité est refusé et compte comme une erreur.</li>" +
      "<li>Les moyens avancent case par case. La <b>piste</b> ne se traverse qu'aux deux passerelles. Les " +
      "cases hachurées sont en travaux.</li>" +
      "<li>Un moyen engagé reste quelques secondes sur place avant d'être de nouveau disponible.</li>" +
      "<li>Objectif : traiter un maximum d'incidents avant la fin de leur compte à rebours.</li></ul></div>";
  }

  function noeud(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  }

  // Mise a jour en place : on ne touche au DOM que si la valeur change.
  function texte(n, t) { if (n.textContent !== t) n.textContent = t; }
  function classe(n, c, oui) { if (n.classList.contains(c) !== !!oui) n.classList.toggle(c, !!oui); }
  function cacher(n, oui) { if (n.hidden !== !!oui) n.hidden = !!oui; }

  function legende() {
    var h = "<ul class=\"legende\" aria-label=\"Légende du plan\">";
    LEGENDE.forEach(function (l) {
      h += "<li><i class=\"tok-mini " + l[0] + "\" aria-hidden=\"true\">" + l[1] + "</i><b>" + l[2] +
        "</b>&nbsp;· " + l[3] + "</li>";
    });
    return h + "<li><i class=\"inc-mini\" aria-hidden=\"true\">I</i>Incident (rouge : moins de 8 s)</li>" +
      "<li><i class=\"case-mini piste\" aria-hidden=\"true\"></i>Piste</li>" +
      "<li><i class=\"case-mini pass\" aria-hidden=\"true\"></i>Passerelle</li>" +
      "<li><i class=\"case-mini trav\" aria-hidden=\"true\"></i>Travaux</li></ul>";
  }

  // Une partie dans `zone`. opts : {t0, groupe, envoyer(action)|null, fin()}
  function partie(zone, enonce, coef, opts) {
    var g = opts.groupe, t0 = opts.t0;
    var e = creer(enonce, enonce.INCIDENTS);
    var pl = e.plan;
    var sel = {inc: null, unit: null}, termine = false, apresGlisse = false;
    var vu = {};  // dernier etat affiche de chaque incident (sons, annonces)

    zone.innerHTML = "<div class=\"ops\"><div class=\"ops-plan\"><div class=\"map\"></div>" + legende() +
      "</div><div class=\"side\"><div><h3>Incidents</h3><div class=\"list ilist\"></div></div>" +
      "<div><h3>Moyens</h3><div class=\"list ulist\"></div></div></div></div>" +
      "<p class=\"sr\" role=\"status\" aria-live=\"polite\"></p>";
    var map = zone.querySelector(".map"), ilist = zone.querySelector(".ilist");
    var ulist = zone.querySelector(".ulist"), annonce = zone.querySelector(".sr");
    var annoncer = function (t) { annonce.textContent = t; };
    var pct = function (p) { return {left: (p[1] + 0.5) / pl.cl * 100 + "%", top: (p[0] + 0.5) / pl.rw * 100 + "%"}; };
    var tJeu = function () { return (performance.now() - t0) / coef; };

    // Plan : zones, cases, libelle de piste, calque SVG des trajets (cree une fois).
    var fond = "";
    (enonce.MAPZONES || []).forEach(function (z) {
      fond += "<div class=\"zone\" style=\"left:" + z[2] / pl.cl * 100 + "%;top:" + z[1] / pl.rw * 100 + "%;width:" +
        z[4] / pl.cl * 100 + "%;height:" + z[3] / pl.rw * 100 + "%\">" + F.esc(z[0]) + "</div>";
    });
    for (var r = 0; r < pl.rw; r++) {
      for (var c = 0; c < pl.cl; c++) {
        var cls = c === 6 ? (pl.pass.indexOf(r) >= 0 ? "pass" : "piste") : (propre(pl.trav, cle(r, c)) ? "trav" : "");
        fond += "<div class=\"cell " + cls + "\" style=\"left:" + c / pl.cl * 100 + "%;top:" + r / pl.rw * 100 +
          "%;width:" + 100 / pl.cl + "%;height:" + 100 / pl.rw + "%\"></div>";
      }
    }
    map.innerHTML = fond + "<div class=\"pistelabel\" style=\"left:" + 6 / pl.cl * 100 + "%;width:" + 100 / pl.cl +
      "%\" aria-hidden=\"true\">PISTE</div>";
    var svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("class", "trajets");
    svg.setAttribute("viewBox", "0 0 " + pl.cl + " " + pl.rw);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    map.appendChild(svg);

    // Incidents : pastille sur le plan + entree de liste, creees une fois, montrees a l'ouverture.
    var vide = noeud("div", "it vide", "Aucun incident pour l'instant.");
    ilist.appendChild(vide);
    e.incs.forEach(function (inc) {
      var b = noeud("button", "inc", inc.id);
      b.type = "button";
      b.hidden = true;
      b.setAttribute("data-i", inc.id);
      var p = pct(inc.pos);
      b.style.left = p.left;
      b.style.top = p.top;
      map.appendChild(b);
      var it = noeud("button", "it");
      it.type = "button";
      it.hidden = true;
      it.setAttribute("data-i", inc.id);
      var top = noeud("span", "top");
      top.appendChild(noeud("span", "", inc.id + " · " + inc.type));
      var cd = noeud("span", "cd");
      top.appendChild(cd);
      it.appendChild(top);
      it.appendChild(noeud("span", "txt", inc.txt + " · " + inc.zone));
      var ro = noeud("span", "top ro");
      ro.hidden = true;
      it.appendChild(ro);
      ilist.appendChild(it);
      inc.vue = {pion: b, it: it, cd: cd, ro: ro};
      vu[inc.id] = "attente";
    });

    // Moyens : pion sur le plan + bouton de liste, trajet en pointilles.
    e.units.forEach(function (u, k) {
      var t = noeud("button", "tok " + u.cls, u.id);
      t.type = "button";
      t.setAttribute("data-u", u.id);
      t.setAttribute("aria-label", u.name + ", " + u.type);
      map.appendChild(t);
      var it = noeud("button", "it mv");
      it.type = "button";
      it.setAttribute("data-u", u.id);
      it.appendChild(noeud("i", "tok-mini " + u.cls, u.id.charAt(0))).setAttribute("aria-hidden", "true");
      var nom = noeud("span", "nom", u.name);
      nom.appendChild(noeud("small", "", u.type));
      it.appendChild(nom);
      var st = noeud("span", "st", "Disponible");
      it.appendChild(st);
      if (k < 9) it.appendChild(noeud("kbd", "", String(k + 1))).setAttribute("aria-hidden", "true");
      ulist.appendChild(it);
      var ligne = document.createElementNS(SVGNS, "polyline");
      ligne.setAttribute("stroke", COULEURS[u.cls] || "#fff");
      ligne.setAttribute("vector-effect", "non-scaling-stroke");
      svg.appendChild(ligne);
      u.vue = {pion: t, it: it, st: st, ligne: ligne, points: ""};
    });

    // ---------------------------------------------------------- selection et engagement

    var majSelection = function () {
      e.incs.forEach(function (inc) {
        var s = sel.inc === inc;
        classe(inc.vue.pion, "sel", s);
        classe(inc.vue.it, "sel", s);
        inc.vue.it.setAttribute("aria-pressed", String(s));
      });
      e.units.forEach(function (u) {
        var s = sel.unit === u;
        classe(u.vue.pion, "sel", s);
        classe(u.vue.it, "sel", s);
        u.vue.it.setAttribute("aria-pressed", String(s));
      });
    };

    var refusVisuel = function (u) {
      classe(u.vue.pion, "refuse", true);
      classe(u.vue.it, "refuse", true);
      g.later(function () { classe(u.vue.pion, "refuse", false); classe(u.vue.it, "refuse", false); }, 700);
    };

    var tenter = function (u, inc) {
      var tMs = performance.now() - t0;  // un seul instant : decision locale et t_ms envoye
      var code = engager(e, tMs, coef, u.id, inc.id);
      if (code === "ok") {
        if (opts.envoyer) opts.envoyer({type: "engager", moyen: u.id, incident: inc.id, t_ms: tMs});
        F.son.bip(1100, 0.05);
        annoncer(u.name + " engagé sur " + inc.id + ".");
        sel.inc = null;
        sel.unit = null;
      } else if (code === "mauvais_type") {
        if (opts.envoyer) {
          opts.envoyer({type: "engager", moyen: u.id, incident: inc.id, t_ms: tMs});
          opts.envoyer({type: "refus", moyen: u.id, incident: inc.id, t_ms: tMs});
        }
        F.son.bip(200, 0.18, "sawtooth");
        F.toast(u.name + " ne traite pas ce type d'incident (" + inc.type + ").");
        refusVisuel(u);
        sel.unit = null;
        sel.inc = inc;  // l'incident reste choisi : il reste a envoyer le bon moyen
      } else if (code === "occupe") {
        F.toast(u.name + " n'est pas disponible.");
        sel.unit = null;
      } else {
        F.toast("Cet incident n'est plus ouvert.");
        sel.inc = null;
        sel.unit = null;
      }
      majSelection();
      maj();
    };

    var choisirInc = function (inc) {
      if (termine) return;
      var st = etatIncident(inc, tJeu());
      if (st !== "ouvert") {
        if (st === "route") F.toast(e.u[inc.moyen].name + " est déjà engagé sur " + inc.id + ".");
        return;
      }
      if (sel.unit) { tenter(sel.unit, inc); return; }
      sel.inc = sel.inc === inc ? null : inc;
      majSelection();
    };

    var choisirUnit = function (u) {
      if (termine) return;
      if (sel.inc) { tenter(u, sel.inc); return; }
      if (sel.unit === u) { sel.unit = null; majSelection(); return; }
      if (phase(u, tJeu()) !== "dispo") { F.toast(u.name + " n'est pas disponible."); return; }
      sel.unit = u;
      majSelection();
    };

    var incDe = function (n) {
      var b = n && n.closest ? n.closest("[data-i]") : null;
      var id = b && b.getAttribute("data-i");
      return id && propre(e.i, id) ? e.i[id] : null;
    };
    var unitDe = function (n) {
      var b = n && n.closest ? n.closest("[data-u]") : null;
      var id = b && b.getAttribute("data-u");
      return id && propre(e.u, id) ? e.u[id] : null;
    };

    g.on(zone, "click", function (ev) {
      if (apresGlisse) { apresGlisse = false; return; }
      var u = unitDe(ev.target);
      if (u) { choisirUnit(u); return; }
      var inc = incDe(ev.target);
      if (inc) choisirInc(inc);
    });

    // Glisser-deposer d'un moyen (pion ou liste) sur un incident (pion ou liste).
    g.on(zone, "pointerdown", function (ev) {
      var u = unitDe(ev.target);
      apresGlisse = false;
      if (!u || termine || (ev.pointerType === "mouse" && ev.button !== 0)) return;
      var src = ev.target.closest("[data-u]"), id = ev.pointerId, x0 = ev.clientX, y0 = ev.clientY;
      var fantome = null, cible = null;
      // Capture sur le pion ; sinon, ecoute sur le document (le relacher hors du pion arrive quand meme).
      var ecoute = src;
      try { src.setPointerCapture(id); } catch (x) { ecoute = document; }
      if (ecoute === src && src.hasPointerCapture && !src.hasPointerCapture(id)) ecoute = document;
      var sous = function (m) {
        var n = document.elementFromPoint(m.clientX, m.clientY);
        var inc = incDe(n);
        return inc && etatIncident(inc, tJeu()) === "ouvert" ? inc : null;
      };
      var marquer = function (inc) {
        if (cible === inc) return;
        if (cible) { classe(cible.vue.pion, "cible", false); classe(cible.vue.it, "cible", false); }
        cible = inc;
        if (cible) { classe(cible.vue.pion, "cible", true); classe(cible.vue.it, "cible", true); }
      };
      var bouge = function (m) {
        if (m.pointerId !== id) return;
        if (g.arrete) { fin(m); return; }
        if (!fantome) {
          if (Math.abs(m.clientX - x0) + Math.abs(m.clientY - y0) < 8) return;
          fantome = noeud("div", "tok fantome " + u.cls, u.id);
          fantome.setAttribute("aria-hidden", "true");
          document.body.appendChild(fantome);
        }
        fantome.style.left = m.clientX + "px";
        fantome.style.top = m.clientY + "px";
        marquer(sous(m));
      };
      var fin = function (m) {
        if (m.pointerId !== id) return;
        ecoute.removeEventListener("pointermove", bouge);
        ecoute.removeEventListener("pointerup", fin);
        ecoute.removeEventListener("pointercancel", fin);
        ecoute.removeEventListener("lostpointercapture", fin);  // pion retire en plein glisser (fin d'epreuve)
        if (!fantome) return;  // simple clic : la selection passe par l'evenement click
        fantome.remove();  // toujours retire, quelle que soit l'issue
        apresGlisse = true;  // le click qui suit le relacher ne selectionne rien
        g.later(function () { apresGlisse = false; }, 0);
        var inc = m.type === "pointerup" && !termine && !g.arrete ? sous(m) : null;
        marquer(null);
        if (inc) { sel.inc = null; sel.unit = null; tenter(u, inc); }
      };
      ecoute.addEventListener("pointermove", bouge);
      ecoute.addEventListener("pointerup", fin);
      ecoute.addEventListener("pointercancel", fin);
      ecoute.addEventListener("lostpointercapture", fin);
    });

    g.on(document, "keydown", function (ev) {
      if (termine || ev.ctrlKey || ev.metaKey || ev.altKey) return;
      var tag = (ev.target && ev.target.tagName) || "";
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(tag)) return;
      if (ev.key === "Escape") {
        sel.inc = null;
        sel.unit = null;
        majSelection();
        return;
      }
      var k = /^[1-9]$/.test(ev.key) ? Number(ev.key) - 1 : -1;
      if (k < 0 || k >= e.units.length) return;
      ev.preventDefault();
      if (!ev.repeat) choisirUnit(e.units[k]);
    });

    // ---------------------------------------------------------- rendu au temps ecoule

    var maj = function () {
      if (g.arrete) return;
      var tj = tJeu(), visibles = [], resolus = 0;
      e.incs.forEach(function (inc, k) {
        var st = etatIncident(inc, tj), v = inc.vue, avant = vu[inc.id];
        var restant = (inc.D - tj) * coef;  // ms reelles
        var actif = st === "ouvert" || st === "route";
        var urgent = st === "ouvert" && restant <= URGENT_MS;
        if (st !== avant) {
          vu[inc.id] = st;
          if (st === "ouvert" && avant === "attente") {
            F.son.chirp();
            annoncer("Nouvel incident " + inc.id + ", " + inc.type + " : " + inc.txt + ", " + inc.zone + ".");
          } else if (st === "traite") {
            F.son.bip(1500, 0.06);
          } else if (st === "manque") {
            F.son.bip(180, 0.25, "sawtooth");
            annoncer("Incident " + inc.id + " manqué.");
          }
          if (!actif && sel.inc === inc) { sel.inc = null; majSelection(); }
        }
        if (urgent && !inc.alerte) {
          inc.alerte = true;
          F.son.bip(660, 0.12);
          annoncer("Incident " + inc.id + " : moins de 8 secondes.");
        }
        cacher(v.pion, !actif);
        classe(v.pion, "urg", urgent);
        cacher(v.it, st === "attente");
        classe(v.it, "done", st === "traite" || st === "manque");
        classe(v.it, "urg", urgent);
        classe(v.cd, "u", urgent);
        if (actif) {
          var s = Math.max(0, Math.ceil(restant / 1000)) + " s";
          texte(v.cd, s);
          v.pion.setAttribute("aria-label", inc.id + ", " + inc.type + ", " + inc.txt + ", " + s);
        } else {
          texte(v.cd, st === "traite" ? "Traité" : st === "manque" ? "Manqué" : "");
        }
        var ro = "";
        if (inc.moyen !== null && actif) ro = e.u[inc.moyen].name + " en route";
        texte(v.ro, ro);
        cacher(v.ro, !ro);
        if (st !== "attente") visibles.push([actif ? 0 : 1, actif ? inc.D : -inc.D, k, inc]);
        if (st === "traite" || st === "manque") resolus++;
      });
      // Liste triee par temps restant (actifs d'abord).
      visibles.sort(function (a, b) { return a[0] - b[0] || a[1] - b[1] || a[2] - b[2]; });
      // Seuls les noeuds hors de place bougent : l'element qui a le focus le garde s'il est deja bien place.
      visibles.forEach(function (x, k) {
        var n = x[3].vue.it, ici = ilist.children[k + 1];  // children[0] : « Aucun incident »
        if (ici !== n) ilist.insertBefore(n, ici || null);
      });
      cacher(vide, visibles.length > 0);
      e.units.forEach(function (u) {
        var ph = phase(u, tj);
        texte(u.vue.st, ph === "dispo" ? "Disponible" : ph === "route" ? "En route" : "Sur place");
        classe(u.vue.pion, "busy", ph !== "dispo");
        classe(u.vue.it, "busy", ph !== "dispo");
        if (ph !== "dispo" && sel.unit === u) { sel.unit = null; majSelection(); }
      });
      if (!termine && e.incs.length && resolus === e.incs.length) {
        termine = true;
        sel.inc = null;
        sel.unit = null;
        majSelection();
        g.later(opts.fin, 800);
      }
    };

    // Pions et trajets : chaque image, fonction pure du temps ecoule.
    var dessiner = function () {
      var tj = tJeu();
      e.units.forEach(function (u) {
        var p = position(u, tj, F.reduit), v = u.vue;
        var css = pct(p);
        if (v.pion.style.left !== css.left) v.pion.style.left = css.left;
        if (v.pion.style.top !== css.top) v.pion.style.top = css.top;
        var pts = "";
        if (phase(u, tj) === "route") {
          pts = (p[1] + 0.5) + "," + (p[0] + 0.5);
          u.trajet.slice(etape(u, tj) + 1).forEach(function (q) { pts += " " + (q[1] + 0.5) + "," + (q[0] + 0.5); });
        }
        if (pts !== v.points) {
          v.points = pts;
          v.ligne.setAttribute("points", pts);
        }
      });
    };
    var image = function () {
      if (g.arrete) return;
      dessiner();
      window.requestAnimationFrame(image);
    };

    maj();
    dessiner();
    g.every(maj, 200);
    window.requestAnimationFrame(image);
    // Onglet en arriere-plan (pas d'image) : les pions se recalent quand meme.
    g.every(dessiner, 1000);
    return {fin: function () { termine = true; }};
  }

  function entrainement(el, coef) {
    var contenu = null, groupe = null;
    el.innerHTML = "<div class=\"entrainement\"><h3>Entraînement</h3><p>Un incident pour vous exercer. " +
      "Rien n'est enregistré : vous pouvez recommencer autant de fois que vous le souhaitez.</p>" +
      "<div class=\"row\"><button class=\"btn ghost\" type=\"button\">Lancer l'entraînement</button></div>" +
      "<div class=\"zone-jeu\"></div></div>";
    var bouton = el.querySelector("button"), zone = el.querySelector(".zone-jeu");
    var lancer = function () {
      if (groupe) groupe.stop();
      var g = groupe = F.groupe();
      bouton.textContent = "Recommencer l'entraînement";
      partie(zone, contenu, coef, {t0: performance.now(), groupe: g, envoyer: null, fin: function () {
        g.stop();  // fin de l'entrainement : plus de minuteurs ni d'ecouteurs (clavier compris)
        var p = noeud("p", "enreg", "Entraînement terminé. Vous pouvez le recommencer ou commencer l'épreuve.");
        p.setAttribute("role", "status");
        zone.appendChild(p);
      }});
    };
    bouton.onclick = function () {
      if (contenu) { lancer(); return; }
      bouton.disabled = true;
      F.api("/api/entrainement/patrouilles").then(function (r) {
        bouton.disabled = false;
        var d = r.data;
        if (r.status === 200 && d && d.ok && d.contenu && d.contenu.UNITS && d.contenu.INCIDENTS) {
          contenu = d.contenu;
          lancer();
        } else if (r.status !== 401) {
          zone.innerHTML = "<p class=\"erreur\">Entraînement indisponible pour le moment.</p>";
        }
      });
    };
  }

  F.epreuves.patrouilles = {
    intro: function (el, enonce, coef) {
      F.entete("La nuit s'emballe. Vous coordonnez les moyens sur le terrain.");
      el.innerHTML = regles() + "<div></div>";
      entrainement(el.lastChild, coef);
    },
    jouer: function (el, enonce, coef, fini) {
      F.entete("Choisissez un incident, puis le bon moyen.", true);
      var g = F.groupe();
      var zone = noeud("div");
      el.appendChild(zone);
      var revoir = noeud("details", "rules");
      revoir.innerHTML = "<summary>Revoir les règles</summary>" + regles().replace("<div class=\"rules\">", "<div>");
      el.appendChild(revoir);
      var jeu = partie(zone, enonce, coef, {
        t0: F.t0, groupe: g, fin: fini,
        envoyer: function (a) { F.action("patrouilles", a); }
      });
      // Duree maximale (x coef) : l'epreuve se clot meme si un incident reste ouvert.
      F.chrono(DUREE_MAX * coef, F.t0, g, function () { jeu.fin(); fini(); });
    }
  };
})();
