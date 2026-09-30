// csrf_refresh.js - Garde le jeton CSRF de la page a jour.
//
// Flask-WTF signe le jeton avec une duree de vie (WTF_CSRF_TIME_LIMIT,
// 1 h par defaut). La page ne le lisait qu'au chargement : un onglet laisse
// ouvert plus d'une heure echouait a la premiere ecriture, souvent apres
// avoir rempli tout un formulaire.
//
// Tous les modules lisent <meta name="csrf-token"> au moment de l'appel :
// il suffit de mettre cette balise a jour. Renouvellement periodique, au
// retour sur l'onglet (veille, onglet en arriere-plan) et a la demande :
//   window.CockpitCsrf.ensureFresh()  -> Promise<{ok, expired}>
//   window.CockpitCsrf.refresh()      -> force le renouvellement
// expired = true : la session d'authentification elle-meme a expire (la
// route repond par une redirection vers le portail), il faut recharger.
(function () {
  "use strict";

  var ENDPOINT = "/api/csrf-token";
  var PERIOD_MS = 15 * 60 * 1000;     // renouvellement de fond
  var FRESH_MS = 10 * 60 * 1000;      // en dessous : jeton considere frais
  var lastRefresh = Date.now();       // le jeton de la page vient d'etre emis
  var pending = null;

  function meta() {
    var m = document.querySelector('meta[name="csrf-token"]');
    if (!m) {
      m = document.createElement("meta");
      m.setAttribute("name", "csrf-token");
      document.head.appendChild(m);
    }
    return m;
  }

  function refresh() {
    if (pending) return pending;
    pending = fetch(ENDPOINT, { cache: "no-store", credentials: "same-origin", redirect: "manual" })
      .then(function (r) {
        // Redirection vers le portail (session expiree) : opaqueredirect
        if (r.type === "opaqueredirect" || r.status === 401 || r.status === 403 || (r.status >= 300 && r.status < 400)) {
          return { ok: false, expired: true };
        }
        return r.json().then(function (d) {
          if (d && d.csrf_token) {
            meta().setAttribute("content", d.csrf_token);
            lastRefresh = Date.now();
            return { ok: true, expired: false };
          }
          return { ok: false, expired: true };
        }, function () {
          // Reponse non JSON (page de connexion) : session expiree
          return { ok: false, expired: true };
        });
      })
      .catch(function () { return { ok: false, expired: false }; })
      .then(function (res) { pending = null; return res; });
    return pending;
  }

  function ensureFresh() {
    if (Date.now() - lastRefresh < FRESH_MS) return Promise.resolve({ ok: true, expired: false });
    return refresh();
  }

  setInterval(function () {
    if (document.visibilityState === "visible") refresh();
  }, PERIOD_MS);

  // Retour sur l'onglet apres une absence (veille du poste, autre onglet)
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") ensureFresh();
  });
  window.addEventListener("focus", function () { ensureFresh(); });

  window.CockpitCsrf = { refresh: refresh, ensureFresh: ensureFresh };
})();
