/* Test en ligne du PC Organisation : coeur des pages candidat.
 *
 * - Accueil (#formation-accueil) : formulaire e-mail + 4 chiffres.
 * - Test (#formation-test, attribut data-etat = etat client du serveur) :
 *   avertissement, prise de poste, epreuves dans l'ordre, ecran de fin.
 *
 * API exposee aux modules d'epreuve (window.Formation) :
 *   api(path, body, opts)    fetch JSON + X-CSRFToken ; 401 session -> ecran de session perdue
 *   action(epreuve, donnees) envoi immediat, file de reemission, t_ms = performance.now() - t0
 *   epreuves                 registre {id: {intro(el, enonce, coef), jouer(el, enonce, coef, fini)}}
 *                            (intro est appele avant /debut : enonce vaut null)
 *   son                      {actif, bip(), chirp()}, coupe par defaut (bouton « Son »)
 *   groupe()                 minuteurs et ecouteurs arretes ensemble (toutArreter a la fin d'une epreuve)
 *   entete(lead, chrono)     en-tete de l'ecran courant ; chrono(totalMs, depart, groupe, fin) compte a rebours
 * Aucun score ni aucune bonne reponse n'est affiche par le coeur.
 */
(function () {
  "use strict";

  var F = window.Formation = window.Formation || {};
  F.epreuves = F.epreuves || {};

  var BASE = (document.body && document.body.getAttribute("data-base")) || "/test-pc-organisation";
  var RETOURS_MS = [500, 1000, 2000, 4000, 8000];

  F.reduit = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  // ------------------------------------------------------------ utilitaires

  F.esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[c];
    });
  };

  // 2 -> "2", 2.66 -> "2,7"
  F.secondes = function (x) {
    var r = Math.round(x * 10) / 10;
    return (r % 1 === 0 ? String(r) : r.toFixed(1)).replace(".", ",");
  };

  // 240000 -> "4 minutes", 319200 -> "5 min 19 s"
  F.duree = function (ms) {
    var s = Math.round(ms / 1000), m = Math.floor(s / 60), r = s % 60;
    if (r === 0) return m + (m > 1 ? " minutes" : " minute");
    return m + " min " + r + " s";
  };

  F.mmss = function (ms) {
    var s = Math.max(0, Math.ceil(ms / 1000));
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  };

  function csrf() {
    var m = document.querySelector("meta[name=\"csrf-token\"]");
    return m ? m.getAttribute("content") : "";
  }

  F.toast = function (t) {
    var d = document.createElement("div");
    d.className = "toast";
    d.setAttribute("role", "status");
    d.textContent = t;
    document.body.appendChild(d);
    setTimeout(function () { d.remove(); }, 2400);
  };

  // ------------------------------------------------------------ son

  var actx = null;
  F.son = {
    actif: false,
    bip: function (freq, dur, type, vol) {
      if (!F.son.actif) return;
      try {
        actx = actx || new (window.AudioContext || window.webkitAudioContext)();
        var o = actx.createOscillator(), g = actx.createGain();
        o.type = type || "square";
        o.frequency.value = freq || 880;
        g.gain.value = vol || 0.04;
        o.connect(g);
        g.connect(actx.destination);
        o.start();
        o.stop(actx.currentTime + (dur || 0.08));
      } catch (e) { /* son indisponible : sans importance */ }
    },
    chirp: function () {
      F.son.bip(1250, 0.05);
      setTimeout(function () { F.son.bip(900, 0.05); }, 70);
    }
  };

  // ------------------------------------------------------------ minuteurs

  var groupes = [];
  F.groupe = function () {
    var ids = [], ecouteurs = [], arrete = false;
    var g = {
      later: function (fn, ms) {
        if (arrete) return null;
        var id = setTimeout(fn, Math.max(0, ms));
        ids.push(["t", id]);
        return id;
      },
      every: function (fn, ms) {
        if (arrete) return null;
        var id = setInterval(fn, ms);
        ids.push(["i", id]);
        return id;
      },
      on: function (cible, evt, fn) {
        if (arrete) return;
        cible.addEventListener(evt, fn);
        ecouteurs.push([cible, evt, fn]);
      },
      stop: function () {
        arrete = true;
        ids.forEach(function (x) { if (x[0] === "t") clearTimeout(x[1]); else clearInterval(x[1]); });
        ecouteurs.forEach(function (x) { x[0].removeEventListener(x[1], x[2]); });
        ids = [];
        ecouteurs = [];
      },
      get arrete() { return arrete; }
    };
    groupes.push(g);
    return g;
  };
  F.toutArreter = function () {
    groupes.forEach(function (g) { g.stop(); });
    groupes = [];
  };

  // ------------------------------------------------------------ API

  var perdue = false;

  F.api = function (path, body, opts) {
    opts = opts || {};
    var init = {method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store",
                headers: {"Accept": "application/json"}};
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.headers["X-CSRFToken"] = csrf();
      init.body = JSON.stringify(body);
    }
    var minuterie = null;
    if (window.AbortController) {
      var ctrl = new AbortController();
      init.signal = ctrl.signal;
      minuterie = setTimeout(function () { ctrl.abort(); }, opts.timeout || 15000);
    }
    return fetch(BASE + path, init).then(function (res) {
      return res.text().then(function (txt) {
        var data = null;
        try { data = JSON.parse(txt); } catch (e) { data = null; }
        return {status: res.status, data: data};
      });
    }, function () {
      return {status: 0, data: null};
    }).then(function (r) {
      if (minuterie) clearTimeout(minuterie);
      if (r.status === 401 && r.data && r.data.error === "session") {
        sessionPerdue();
      } else if (r.status === 400 && (!r.data || r.data.code === "csrf" || r.data.error === "csrf")) {
        // Jeton CSRF expire : nouveau jeton puis un seul rejeu ; rechargement seulement en dernier recours
        // (un rechargement pendant une epreuve la clot).
        if (opts.rejeu) { jetonExpire(); return r; }
        return renouvelerJeton().then(function (ok) {
          if (!ok) { jetonExpire(); return r; }
          var o = {};
          for (var k in opts) if (Object.prototype.hasOwnProperty.call(opts, k)) o[k] = opts[k];
          o.rejeu = true;
          return F.api(path, body, o);
        });
      }
      return r;
    });
  };

  function renouvelerJeton() {
    return fetch(BASE + "/api/csrf", {credentials: "same-origin", cache: "no-store",
                                     headers: {"Accept": "application/json"}}).then(function (res) {
      return res.ok ? res.json() : null;
    }).then(function (d) {
      var m = document.querySelector("meta[name=\"csrf-token\"]");
      if (!d || !d.csrf_token || !m) return false;
      m.setAttribute("content", d.csrf_token);
      return true;
    }, function () { return false; });
  }

  function aReessayer(status) {
    return status === 0 || status === 429 || status === 502 || status === 503 || status === 504;
  }

  function attente(essai) {
    return RETOURS_MS[Math.min(essai, RETOURS_MS.length - 1)];
  }

  // ------------------------------------------------------------ actions au fil de l'eau

  // Une action reemise apres un timeout a pu etre enregistree deux fois : la premiere fait foi cote
  // serveur (drapeau `doublon` pour une prise radio ou une decision d'acces repetee, sans effet sur le score).
  var MAX_ESSAIS = 5;
  var file = [], envoi = false, attenteVide = [];
  F.t0 = 0;

  F.maintenant = function () {
    return Math.round(performance.now() - F.t0);
  };

  F.action = function (epreuve, donnees) {
    if (perdue) return;
    var corps = {};
    for (var k in donnees) {
      if (Object.prototype.hasOwnProperty.call(donnees, k)) corps[k] = donnees[k];
    }
    if (typeof corps.t_ms !== "number") corps.t_ms = F.maintenant();
    file.push({epreuve: epreuve, corps: corps, essais: 0});
    pomper();
  };

  function signalerVide() {
    var w = attenteVide;
    attenteVide = [];
    w.forEach(function (fn) { fn(); });
  }

  function pomper() {
    if (envoi || perdue) return;
    if (!file.length) { signalerVide(); return; }
    envoi = true;
    var it = file[0];
    F.api("/api/epreuve/" + encodeURIComponent(it.epreuve) + "/action", it.corps).then(function (r) {
      envoi = false;
      if (perdue) return;
      if (aReessayer(r.status)) {
        it.essais++;
        // Apres MAX_ESSAIS, l'action reste en tete de file sans nouvel essai automatique : la prochaine
        // action relance l'envoi, et /fin part quand meme a l'expiration de vider().
        if (it.essais < MAX_ESSAIS) setTimeout(pomper, attente(it.essais - 1));
        else it.essais = 0;
        return;
      }
      file.shift();  // accepte, ou refuse pour de bon (epreuve close, corps invalide) : on passe
      pomper();
    });
  }

  // Promesse resolue quand la file est vide, ou apres `ms` au plus.
  function vider(ms) {
    return new Promise(function (resolve) {
      if (!file.length && !envoi) { resolve(); return; }
      var fait = false;
      var fin = function () { if (!fait) { fait = true; resolve(); } };
      attenteVide.push(fin);
      setTimeout(fin, ms);
    });
  }

  // ------------------------------------------------------------ garde de la page

  var garde = false;
  window.addEventListener("beforeunload", function (e) {
    if (!garde) return undefined;
    e.preventDefault();
    e.returnValue = "";
    return "";
  });

  function jetonExpire() {
    garde = false;
    window.location.reload();
  }

  // ------------------------------------------------------------ accueil

  function initAccueil() {
    var form = document.getElementById("formation-connexion");
    if (!form) return;
    var msg = document.getElementById("formation-message");
    var bouton = form.querySelector("button[type=submit]");
    var tel = form.elements.tel4;
    tel.addEventListener("input", function () {
      tel.value = tel.value.replace(/\D/g, "").slice(0, 4);
    });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var email = form.elements.email.value.trim();
      var tel4 = tel.value.trim();
      if (!email || !/^\d{4}$/.test(tel4)) {
        msg.textContent = "Saisissez votre adresse e-mail et les 4 derniers chiffres de votre téléphone mobile.";
        return;
      }
      bouton.disabled = true;
      msg.textContent = "";
      F.api("/api/connexion", {email: email, tel4: tel4}).then(function (r) {
        bouton.disabled = false;
        var d = r.data || {};
        if (r.status === 200 && d.ok) {
          window.location.href = BASE + "/test";
        } else if (r.status === 401 || r.status === 429) {
          msg.textContent = d.message || "Ces informations ne correspondent à aucune candidature.";
        } else if (r.status === 403) {
          msg.textContent = {
            avant: "Le test n'est pas encore ouvert.",
            apres: "Le test est fermé.",
            non_configure: "Le test n'est pas encore disponible."
          }[d.fenetre] || "Le test n'est pas ouvert en ce moment.";
        } else {
          msg.textContent = "Service momentanément indisponible, réessayez dans un instant.";
        }
      });
    });
  }

  // ------------------------------------------------------------ test : ecrans

  var DEROULE = [
    {id: "brief", h: "20:55", t: "Prise de poste"},
    {id: "radio", h: "21:15", t: "Le canal radio"},
    {id: "appel", h: "21:45", t: "Le 38 17"},
    {id: "mc", h: "22:30", t: "La main courante"},
    {id: "patrouilles", h: "23:30", t: "Les patrouilles"},
    {id: "acces", h: "01:30", t: "Contrôle d'accès"},
    {id: "logique", h: "03:00", t: "La relève"},
    {id: "situations", h: "05:00", t: "Le pic du matin"},
    {id: "fin", h: "06:00", t: "Fin de vacation"}
  ];
  var EPREUVES = ["radio", "appel", "mc", "patrouilles", "acces", "logique", "situations"];

  var stage = null, etat = null, courant = null, avisMontre = false;

  function etape(id) {
    for (var i = 0; i < DEROULE.length; i++) if (DEROULE[i].id === id) return DEROULE[i];
    return {id: id, h: "", t: ""};
  }

  function etatEpreuve(id) {
    var l = (etat && etat.epreuves) || [];
    for (var i = 0; i < l.length; i++) if (l[i].id === id) return l[i].etat;
    return "a_faire";
  }

  function coef() {
    var c = etat && Number(etat.coef);
    return c && isFinite(c) && c > 0 ? c : 1;
  }

  function dessinerNav() {
    var nav = document.getElementById("f-nav");
    if (!nav) return;
    var html = "<div class=\"navlabel\">Déroulé de la nuit</div><ol>";
    DEROULE.forEach(function (g) {
      var s = "";
      if (EPREUVES.indexOf(g.id) >= 0) {
        var e = etatEpreuve(g.id);
        if (e === "terminee") s = "<span class=\"s done\" aria-label=\"faite\">✓</span>";
        else if (e === "abandonnee") s = "<span class=\"s\" aria-label=\"interrompue\">–</span>";
        else s = "<span class=\"s\" aria-hidden=\"true\">·</span>";
      }
      html += "<li" + (g.id === courant ? " aria-current=\"step\"" : "") + "><span class=\"h\">" + g.h +
        "</span><span class=\"t\">" + F.esc(g.t) + "</span>" + s + "</li>";
    });
    nav.innerHTML = html + "</ol>";
  }

  // Prepare l'ecran `id` : nav, horloge, en-tete et corps vides. Renvoie le corps.
  function ecran(id) {
    F.toutArreter();
    courant = id;
    var g = etape(id);
    var horloge = document.getElementById("f-horloge");
    if (horloge && g.h) horloge.textContent = g.h;
    dessinerNav();
    stage.innerHTML = "<div id=\"f-tete\"></div><div id=\"f-corps\"></div>";
    window.scrollTo(0, 0);
    return document.getElementById("f-corps");
  }

  F.entete = function (lead, chrono, eyebrow) {
    var tete = document.getElementById("f-tete");
    if (!tete) return;
    var ancien = document.getElementById("f-timer");
    var texteTimer = ancien ? ancien.textContent : "--:--";
    var bas = ancien ? ancien.classList.contains("low") : false;
    var g = etape(courant);
    var k = EPREUVES.indexOf(courant);
    var eb = eyebrow != null ? eyebrow : g.h + (k >= 0 ? " · épreuve " + (k + 1) + " / " + EPREUVES.length : "");
    var html = "<div class=\"stage-head\"><div><div class=\"eyebrow\">" + F.esc(eb) + "</div>" +
      "<h2 tabindex=\"-1\">" + F.esc(g.t) + "</h2>" +
      (lead ? "<p class=\"lead\">" + F.esc(lead) + "</p>" : "") + "</div>" +
      (chrono ? "<div class=\"timer\" id=\"f-timer\" role=\"timer\" aria-label=\"Temps restant\">" + F.esc(texteTimer) + "</div>" : "") +
      "</div>";
    if (k >= 0) {
      html += "<p class=\"garde\">Ne rechargez pas la page et n'utilisez pas le bouton retour : " +
        "l'épreuve en cours serait perdue et comptée en l'état.</p>";
    }
    tete.innerHTML = html;
    var t = document.getElementById("f-timer");
    if (t && bas) t.classList.add("low");
  };

  // Focus sur le premier element utile apres un nouveau rendu (clavier, lecteur d'ecran).
  F.focus = function (el) {
    if (el && el.focus) el.focus({preventScroll: true});
  };

  function focaliserTitre() {
    var h = stage.querySelector("h2");
    if (h) h.focus({preventScroll: true});
  }

  // Compte a rebours visible, calcule sur le temps ecoule depuis `depart` (performance.now()).
  F.chrono = function (totalMs, depart, groupe, fin) {
    var finie = false;
    var dessiner = function () {
      var reste = totalMs - (performance.now() - depart);
      var el = document.getElementById("f-timer");
      if (el) {
        el.textContent = F.mmss(reste);
        el.classList.toggle("low", reste <= 10000);
      }
      if (reste <= 0 && !finie) {
        finie = true;
        if (fin) fin();
      }
    };
    dessiner();
    groupe.every(dessiner, 250);
  };

  // ------------------------------------------------------------ test : parcours

  function aller(e) {
    if (perdue) return;
    etat = e || etat;
    if (!etat || etat.statut === "non_commence") return avertissement();
    if (etat.statut === "terminee" || etat.statut === "interrompue") return fin();
    if (etat.statut !== "en_cours") return fin();
    var id = etat.epreuve_courante;
    if (!id) return fin();
    if (etatEpreuve(id) === "en_cours") {
      // Epreuve ouverte sans page pour la jouer : on la clot, jamais de second essai.
      return cloreOrpheline(id);
    }
    if (id === "radio" && etatEpreuve("radio") === "a_faire") return priseDePoste();
    return introEpreuve(id);
  }

  function rafraichir() {
    return F.api("/api/etat").then(function (r) {
      if (r.status === 200 && r.data && r.data.ok) { aller(r.data.etat); return; }
      if (perdue) return;
      var corps = ecran(courant || "brief");
      F.entete("");
      corps.innerHTML = "<p>Impossible de joindre le serveur.</p><div class=\"row actions\">" +
        "<button class=\"btn\" type=\"button\" id=\"f-reessai\">Réessayer</button></div>";
      document.getElementById("f-reessai").onclick = rafraichir;
    });
  }

  function cloreOrpheline(id) {
    F.api("/api/epreuve/" + id + "/fin", {}).then(function (r) {
      if (r.status === 200 && r.data && r.data.ok) aller(r.data.etat);
      else rafraichir();
    });
  }

  function avertissement() {
    var corps = ecran("brief");
    F.entete("Lisez attentivement : ces règles s'appliquent dès que vous cliquez sur « Commencer ».",
             false, "AVANT DE COMMENCER");
    var h2 = stage.querySelector("h2");
    if (h2) h2.textContent = "Avertissement";
    corps.innerHTML =
      "<div class=\"rules\"><h3>Une seule fois</h3><ul>" +
      "<li>Vous ne passerez ce test <b>qu'une seule fois</b> : il n'y a aucune possibilité de recommencer.</li>" +
      "<li>Toute épreuve interrompue (fermeture de la page, rechargement, coupure de connexion) est " +
      "<b>perdue et comptée en l'état</b>. Elle ne sera pas rejouée.</li>" +
      "<li>7 épreuves, dans l'ordre, sans retour en arrière. Ne rechargez pas la page et n'utilisez pas " +
      "le bouton retour du navigateur.</li></ul></div>" +
      "<div class=\"rules\"><h3>Votre installation</h3><ul>" +
      "<li>Au calme, seul, sur un ordinateur ou une tablette en mode paysage. Pas de téléphone.</li>" +
      "<li>Une connexion stable : de préférence filaire ou un bon Wi-Fi. Évitez le partage de connexion 4G.</li>" +
      "<li>Batterie branchée, notifications coupées.</li>" +
      "<li>Environ 25 minutes devant vous, sans interruption.</li>" +
      "<li>Le son est conseillé mais facultatif : bouton « Son » en haut de l'écran.</li></ul></div>" +
      "<label class=\"accord\"><input type=\"checkbox\" id=\"f-accord\"><span>J'ai compris que je ne peux " +
      "passer ce test qu'une seule fois. Je le fais seul, sur un ordinateur, dans un endroit calme, et j'ai " +
      "devant moi environ 25 minutes sans interruption.</span></label>" +
      "<div class=\"row\"><button class=\"btn big\" type=\"button\" id=\"f-commencer\" disabled>Commencer</button></div>" +
      "<div id=\"f-confirmer\"></div><p class=\"erreur\" id=\"f-erreur\" role=\"alert\"></p>";
    var accord = document.getElementById("f-accord");
    var bouton = document.getElementById("f-commencer");
    var zone = document.getElementById("f-confirmer");
    var erreur = document.getElementById("f-erreur");
    accord.onchange = function () {
      bouton.disabled = !accord.checked;
      if (!accord.checked) zone.innerHTML = "";
    };
    bouton.onclick = function () {
      if (!accord.checked) return;
      zone.innerHTML = "<div class=\"confirmer\" role=\"alertdialog\" aria-labelledby=\"f-conf-t\">" +
        "<p id=\"f-conf-t\"><b>Le test démarre maintenant et ne pourra pas être recommencé.</b> " +
        "Vous êtes prêt ?</p><div class=\"row\">" +
        "<button class=\"btn\" type=\"button\" id=\"f-oui\">Oui, je commence</button>" +
        "<button class=\"btn ghost\" type=\"button\" id=\"f-non\">Pas encore</button></div></div>";
      var oui = document.getElementById("f-oui");
      oui.focus();
      document.getElementById("f-non").onclick = function () { zone.innerHTML = ""; bouton.focus(); };
      oui.onclick = function () {
        oui.disabled = true;
        erreur.textContent = "";
        F.api("/api/commencer", {accepte: true}).then(function (r) {
          if (perdue) return;
          if (r.status === 200 && r.data && r.data.ok) { aller(r.data.etat); return; }
          oui.disabled = false;
          if (r.status === 403) {
            erreur.textContent = "Le test n'est pas ouvert en ce moment : il ne peut pas être commencé.";
          } else {
            erreur.textContent = "Service momentanément indisponible, réessayez dans un instant.";
          }
        });
      };
    };
    focaliserTitre();
  }

  function priseDePoste() {
    var corps = ecran("brief");
    F.entete("Samedi soir, grande épreuve de 24 heures. Le public est au maximum, la nuit commence. " +
             "Vous prenez votre poste au PC Organisation pour une vacation de nuit : sept épreuves courtes, " +
             "environ 25 minutes.");
    corps.innerHTML =
      "<div class=\"rules\"><h3>Votre poste cette nuit</h3><ul>" +
      "<li>Vous êtes l'opérateur Sud-Est du PC Organisation, sur le canal Sud-Est. Votre indicatif radio : " +
      "<b>PC Org</b>. Vous parlez par radio aux équipes de terrain.</li>" +
      "<li>Un message radio commence toujours par le destinataire, puis l'émetteur : « PC Org de Patrouille 2 » " +
      "veut dire que Patrouille 2 appelle le PC Org.</li>" +
      "<li>Les moyens : <b>Patrouilles</b> (sécurité), <b>Tango</b> (technique), <b>Secours</b> (blessés, " +
      "malaises), <b>Moto</b> (circulation, parkings).</li>" +
      "<li>Une personne qui ne répond plus est toujours une urgence vitale.</li></ul></div>" +
      "<div class=\"rules\"><h3>Avant de commencer</h3><ul>" +
      "<li>Toutes les règles sont rappelées à chaque épreuve : aucune connaissance du site ni expérience " +
      "n'est nécessaire.</li>" +
      "<li>Certaines épreuves sont chronométrées. Faites de votre mieux, la perfection n'est pas attendue.</li>" +
      "<li>Chaque épreuve démarre quand vous cliquez sur « Commencer » : prenez le temps de lire ses règles " +
      "avant.</li>" +
      "<li>Activez le son si vous le souhaitez (bouton « Son » en haut de l'écran).</li></ul></div>" +
      "<div class=\"row\"><button class=\"btn big\" type=\"button\" id=\"f-poste\">Prendre mon poste</button></div>";
    document.getElementById("f-poste").onclick = function () {
      F.son.chirp();
      introEpreuve("radio");
    };
    focaliserTitre();
  }

  function aEteInterrompue() {
    return EPREUVES.some(function (id) { return etatEpreuve(id) === "abandonnee"; });
  }

  function introEpreuve(id) {
    var corps = ecran(id);
    var mod = F.epreuves[id];
    F.entete("");
    var avis = "";
    if (!avisMontre && aEteInterrompue()) {
      avisMontre = true;
      avis = "<div class=\"avis\" role=\"status\">Une épreuve a été interrompue : elle est comptée en l'état " +
        "et ne sera pas rejouée. Le test continue à l'épreuve suivante.</div>";
    }
    if (!mod) {
      // TODO Task 7 : module des patrouilles. En attendant, l'epreuve est ouverte puis close sans action.
      corps.innerHTML = avis + "<div class=\"rules\"><h3>Épreuve indisponible</h3><p>Cette épreuve n'est pas " +
        "disponible pour le moment. Cliquez sur « Continuer » pour passer à la suivante.</p></div>" +
        "<div class=\"row actions\"><button class=\"btn\" type=\"button\" id=\"f-go\">Continuer</button></div>" +
        "<p class=\"erreur\" id=\"f-erreur\" role=\"alert\"></p>";
      document.getElementById("f-go").onclick = function () { demarrer(id, null); };
      focaliserTitre();
      return;
    }
    var zone = document.createElement("div");
    corps.innerHTML = avis;
    corps.appendChild(zone);
    mod.intro(zone, null, coef());
    var row = document.createElement("div");
    row.className = "row actions";
    row.innerHTML = "<button class=\"btn big\" type=\"button\" id=\"f-go\">Commencer l'épreuve</button>";
    corps.appendChild(row);
    var err = document.createElement("p");
    err.className = "erreur";
    err.id = "f-erreur";
    err.setAttribute("role", "alert");
    corps.appendChild(err);
    document.getElementById("f-go").onclick = function () { demarrer(id, mod); };
    focaliserTitre();
  }

  function demarrer(id, mod) {
    var bouton = document.getElementById("f-go");
    var err = document.getElementById("f-erreur");
    if (bouton) { bouton.disabled = true; bouton.textContent = "Démarrage…"; }
    F.api("/api/epreuve/" + id + "/debut", {}).then(function (r) {
      if (perdue) return;
      if (r.status === 200 && r.data && r.data.ok) {
        F.toutArreter();  // entrainement eventuel
        F.t0 = performance.now();
        garde = true;
        var corps = ecran(id);
        var une = false;
        var fini = function () {
          if (une) return;
          une = true;
          terminer(id);
        };
        if (!mod) { fini(); return; }
        try {
          mod.jouer(corps, r.data.enonce, coef(), fini);
        } catch (e) {
          if (window.console) console.error(e);
          fini();
        }
        return;
      }
      if (r.status === 409) { rafraichir(); return; }
      if (bouton) { bouton.disabled = false; bouton.textContent = mod ? "Commencer l'épreuve" : "Continuer"; }
      if (err) err.textContent = "Service momentanément indisponible, réessayez dans un instant.";
    });
  }

  function terminer(id) {
    F.toutArreter();
    var corps = ecran(id);
    F.entete("Enregistrement de l'épreuve…");
    corps.innerHTML = "<div class=\"result\"><p role=\"status\">Enregistrement en cours, ne fermez pas la page.</p></div>";
    vider(20000).then(function () { envoyerFin(id, 0); });
  }

  function envoyerFin(id, essai) {
    F.api("/api/epreuve/" + id + "/fin", {}).then(function (r) {
      if (perdue) return;
      if (r.status === 200 && r.data && r.data.ok) {
        garde = false;
        etat = r.data.etat;
        return transition(id);
      }
      if (r.status === 409) {
        garde = false;
        return rafraichir();
      }
      if (aReessayer(r.status) && essai < 8) {
        setTimeout(function () { envoyerFin(id, essai + 1); }, attente(essai));
        return;
      }
      var corps = document.getElementById("f-corps");
      if (!corps) return;
      corps.innerHTML = "<div class=\"result\"><p>La fin de l'épreuve n'a pas pu être enregistrée. Vérifiez " +
        "votre connexion puis réessayez.</p><div class=\"row\" style=\"justify-content:center\">" +
        "<button class=\"btn\" type=\"button\" id=\"f-reessai\">Réessayer</button></div></div>";
      document.getElementById("f-reessai").onclick = function () { envoyerFin(id, 0); };
    });
  }

  function transition(id) {
    var corps = ecran(id);
    F.entete("Épreuve terminée.");
    var suivante = etat.statut === "en_cours" && etat.epreuve_courante ? etat.epreuve_courante : "fin";
    var g = etape(suivante);
    corps.innerHTML = "<div class=\"result\"><p class=\"fort\">Vos réponses ont été enregistrées.</p>" +
      "<div class=\"row\" style=\"justify-content:center\"><button class=\"btn\" type=\"button\" id=\"f-suite\">" +
      F.esc(g.h + " · " + g.t) + "</button></div></div>";
    var b = document.getElementById("f-suite");
    b.onclick = function () { aller(etat); };
    b.focus();
  }

  function fin() {
    garde = false;
    var corps = ecran("fin");
    F.entete("6 h, la relève arrive. Merci pour cette nuit au PC Organisation.");
    var premier = etat && etat.statut === "interrompue"
      ? "Votre test a été interrompu. Les épreuves faites ont été enregistrées et comptent en l'état."
      : "Votre test est terminé et vos réponses ont été enregistrées.";
    corps.innerHTML = "<div class=\"result\"><p class=\"fort\">" + F.esc(premier) + "</p>" +
      "<p>L'équipe de sélection vous contactera par e-mail ; les convocations partent au plus tard le 4 décembre.</p>" +
      "<p>Vous pouvez fermer cette page.</p></div>";
    focaliserTitre();
  }

  function sessionPerdue() {
    if (perdue || !stage) return;
    perdue = true;
    garde = false;
    file = [];
    F.toutArreter();
    courant = null;
    dessinerNav();
    stage.innerHTML = "<div class=\"stage-head\"><div><div class=\"eyebrow\">SESSION</div>" +
      "<h2 tabindex=\"-1\">Session interrompue</h2></div></div>" +
      "<div class=\"result\"><p class=\"fort\">Votre session a été ouverte ailleurs ou a expiré.</p>" +
      "<p>Si le test est ouvert dans un autre onglet ou un autre navigateur, c'est là qu'il continue.</p>" +
      "<div class=\"row\" style=\"justify-content:center\"><a class=\"btn\" href=\"" + F.esc(BASE) + "\">" +
      "Revenir à l'accueil</a></div></div>";
    focaliserTitre();
  }

  function initTest() {
    stage = document.getElementById("formation-test");
    if (!stage) return;
    var son = document.getElementById("f-son");
    if (son) {
      son.onclick = function () {
        F.son.actif = !F.son.actif;
        son.setAttribute("aria-pressed", String(F.son.actif));
        if (F.son.actif) F.son.chirp();
      };
    }
    var e = null;
    try { e = JSON.parse(stage.getAttribute("data-etat") || "null"); } catch (x) { e = null; }
    if (!e) { rafraichir(); return; }
    aller(e);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initAccueil();
    initTest();
  });
})();
