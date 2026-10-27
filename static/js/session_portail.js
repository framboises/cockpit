// session_portail.js - Session du portail TITAN expiree : l'onglet arrete ses appels.
//
// Un onglet oublie une nuit continuait de rafraichir ses widgets (alertes,
// main courante, affluence...) toutes les quelques secondes : des milliers de
// requetes, chacune renvoyee vers le portail. Ce script, charge en tete de
// page avant les autres, enveloppe window.fetch :
//  - chaque appel vers Cockpit porte X-Requested-With : role_required y
//    repond par un 401 JSON (et non une redirection), y compris hors /api/ ;
//  - au premier 401 de role_required (codes ci-dessous), plus aucun appel ne
//    part : fetch rend aussitot un 401 sans toucher le reseau, et un bandeau
//    propose de se reconnecter (retour sur cette page par ?next=).
// Les 401 propres aux modules (tablettes Field, crise, montre...) ne coupent
// rien. Ecran TV (murs, ?tv=1, ou page encadree par le portail) : personne
// pour cliquer, la page se recharge apres 60 s ; le portail rend un jeton
// frais, ou l'agent du Pi ramene l'ecran sur son tableau (guide 05 §10.1).
(function () {
  "use strict";
  if (window.CockpitSession || !window.fetch) return;

  var CODES = { auth_requise: 1, session_expiree: 1, session_revoquee: 1, auth_invalide: 1 };
  // Murs TV de PAGES_AFFICHEUR (app.py)
  var PAGES_TV = ["/meteo-mur", "/portes-mur", "/intrusions-mur", "/balises-mur", "/circulation"];
  var RECHARGEMENT_TV_MS = 60 * 1000;
  var PORTAIL_DEFAUT = "https://safe.lemans.org/login";

  var fetchNatif = window.fetch.bind(window);
  var coupee = false;
  var urlPortail = PORTAIL_DEFAUT;

  function urlDe(input) {
    if (typeof input === "string") return input;
    if (input && typeof input.url === "string") return input.url;
    return String(input);
  }

  function versCockpit(url) {
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  function estEcranTv() {
    if (PAGES_TV.indexOf(window.location.pathname) !== -1) return true;
    if (/[?&]tv=1(&|$)/.test(window.location.search)) return true;
    try {
      return window.self !== window.top;
    } catch (e) {
      return true;
    }
  }

  function lienConnexion() {
    // Retour vers la PAGE affichee, pas vers l'appel d'API qui a echoue
    return urlPortail + "?next=" + encodeURIComponent(window.location.href);
  }

  function reponseCoupee() {
    var corps = { ok: false, erreur: "session_expiree", message: "Session expiree", login: lienConnexion() };
    return new Response(JSON.stringify(corps), {
      status: 401,
      headers: { "Content-Type": "application/json" }
    });
  }

  function afficherBandeau() {
    if (document.getElementById("session-portail-bandeau")) return;
    var bandeau = document.createElement("div");
    bandeau.id = "session-portail-bandeau";
    bandeau.setAttribute("role", "alert");
    var s = bandeau.style;
    s.position = "fixed";
    s.top = "0";
    s.left = "0";
    s.right = "0";
    s.zIndex = "2147483647";
    s.padding = "12px 16px";
    s.background = "#b91c1c";
    s.color = "#ffffff";
    s.font = "600 16px/1.4 system-ui, sans-serif";
    s.textAlign = "center";
    s.boxShadow = "0 2px 8px rgba(0, 0, 0, 0.4)";

    var lien = document.createElement("a");
    lien.href = lienConnexion();
    lien.textContent = "Session expirée — se reconnecter";
    lien.style.color = "#ffffff";
    lien.style.textDecoration = "underline";
    bandeau.appendChild(lien);
    document.body.appendChild(bandeau);
  }

  function couper(corps) {
    if (coupee) return;
    coupee = true;
    if (corps && typeof corps.login === "string") {
      var base = corps.login.split("?")[0];
      if (/^https?:\/\//.test(base)) urlPortail = base;
    }
    if (document.body) {
      afficherBandeau();
    } else {
      document.addEventListener("DOMContentLoaded", afficherBandeau);
    }
    if (estEcranTv()) {
      setTimeout(function () { window.location.reload(); }, RECHARGEMENT_TV_MS);
    }
  }

  window.fetch = function (input, init) {
    if (!versCockpit(urlDe(input))) return fetchNatif(input, init);
    if (coupee) return Promise.resolve(reponseCoupee());

    var options = {};
    var cle;
    for (cle in (init || {})) {
      if (Object.prototype.hasOwnProperty.call(init, cle)) options[cle] = init[cle];
    }
    var entetes = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
    if (!entetes.has("X-Requested-With")) entetes.set("X-Requested-With", "XMLHttpRequest");
    options.headers = entetes;

    return fetchNatif(input, options).then(function (rep) {
      if (rep.status === 401) {
        rep.clone().json().then(function (corps) {
          if (corps && CODES[corps.erreur]) couper(corps);
        }, function () {});
      }
      return rep;
    });
  };

  window.CockpitSession = {
    coupee: function () { return coupee; },
    lienConnexion: lienConnexion
  };
})();
