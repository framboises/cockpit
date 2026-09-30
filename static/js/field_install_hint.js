/* =====================================================================
   COCKPIT Field - Aide a l'installation sur iPhone / iPad
   ---------------------------------------------------------------------
   Sur iOS (16.4+), les notifications Web Push ne sont disponibles que
   si l'app est ajoutee a l'ecran d'accueil et ouverte depuis l'icone.
   Ce script affiche un petit bandeau, masquable, uniquement :
     - sur iOS / iPadOS,
     - quand l'app n'est PAS deja lancee en mode installe (standalone).
   Le choix "ne plus afficher" est memorise en localStorage (try/catch :
   navigation privee ou stockage bloque ne doivent rien casser).
   Volontairement independant de field.js.
   ===================================================================== */
(function () {
  "use strict";

  var STORAGE_KEY = "field-ios-install-hint-dismissed";

  function isStandalone() {
    try {
      if (window.navigator.standalone === true) return true;
      if (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) return true;
      if (window.matchMedia && window.matchMedia("(display-mode: fullscreen)").matches) return true;
    } catch (e) { /* ignore */ }
    return false;
  }

  function isIOS() {
    var ua = window.navigator.userAgent || "";
    if (/iPad|iPhone|iPod/.test(ua)) return true;
    // iPadOS 13+ se presente comme un Mac : on le reconnait au tactile.
    return window.navigator.platform === "MacIntel" && (window.navigator.maxTouchPoints || 0) > 1;
  }

  function isOtherIOSBrowser() {
    // Chrome, Firefox, Edge... sur iOS : l'ajout a l'ecran d'accueil passe
    // aussi par le menu Partager depuis iOS 16.4, mais Safari reste le plus sur.
    return /CriOS|FxiOS|EdgiOS|OPiOS/.test(window.navigator.userAgent || "");
  }

  function wasDismissed() {
    try { return window.localStorage.getItem(STORAGE_KEY) === "1"; } catch (e) { return false; }
  }

  function remember() {
    try { window.localStorage.setItem(STORAGE_KEY, "1"); } catch (e) { /* ignore */ }
  }

  function mkIcon(name) {
    var s = document.createElement("span");
    s.className = "material-symbols-outlined";
    s.setAttribute("aria-hidden", "true");
    s.textContent = name;
    return s;
  }

  function show() {
    if (document.getElementById("field-install-hint")) return;

    var bar = document.createElement("div");
    bar.id = "field-install-hint";
    bar.className = "field-install-hint";
    bar.setAttribute("role", "note");

    bar.appendChild(mkIcon("install_mobile"));

    var txt = document.createElement("div");
    txt.className = "field-install-hint-text";
    var strong = document.createElement("strong");
    strong.textContent = "Pour recevoir les alertes :";
    txt.appendChild(strong);
    var line = document.createElement("span");
    line.appendChild(document.createTextNode(" Partager "));
    line.appendChild(mkIcon("ios_share"));
    line.appendChild(document.createTextNode(" > Sur l'ecran d'accueil"
      + (isOtherIOSBrowser() ? " (de preference dans Safari)" : "")
      + ", puis ouvrir Field depuis l'icone."));
    txt.appendChild(line);
    bar.appendChild(txt);

    var close = document.createElement("button");
    close.type = "button";
    close.className = "field-install-hint-close";
    close.setAttribute("aria-label", "Ne plus afficher ce conseil");
    close.title = "Ne plus afficher";
    close.appendChild(mkIcon("close"));
    close.addEventListener("click", function () {
      remember();
      if (bar.parentNode) bar.parentNode.removeChild(bar);
    });
    bar.appendChild(close);

    document.body.appendChild(bar);
  }

  // ---------------------------------------------------------------------
  // App installee sur iOS : la demande d'autorisation des notifications
  // DOIT venir d'un geste utilisateur (tap). field.js s'abonne au push au
  // chargement, sans geste : sur iPhone la demande est ignoree. On propose
  // donc un bouton ; une fois l'autorisation donnee, on recharge et
  // l'abonnement de field.js (initPushSubscription) passe sans geste.
  // ---------------------------------------------------------------------
  var PUSH_KEY = "field-ios-push-hint-dismissed";

  function pushWasDismissed() {
    try { return window.localStorage.getItem(PUSH_KEY) === "1"; } catch (e) { return false; }
  }

  function showPushPrompt() {
    if (document.getElementById("field-install-hint")) return;

    var bar = document.createElement("div");
    bar.id = "field-install-hint";
    bar.className = "field-install-hint";
    bar.setAttribute("role", "note");
    bar.appendChild(mkIcon("notifications_active"));

    var txt = document.createElement("div");
    txt.className = "field-install-hint-text";
    txt.textContent = "Autoriser les notifications pour recevoir les alertes et les SOS.";
    bar.appendChild(txt);

    var ok = document.createElement("button");
    ok.type = "button";
    ok.className = "field-install-hint-action";
    ok.textContent = "Activer";
    ok.addEventListener("click", function () {
      ok.disabled = true;
      var p;
      try { p = window.Notification.requestPermission(); } catch (e) { p = null; }
      Promise.resolve(p).then(function (res) {
        var perm = res || window.Notification.permission;
        if (perm === "granted") {
          txt.textContent = "Notifications activees, rechargement...";
          setTimeout(function () { window.location.reload(); }, 600);
        } else {
          txt.textContent = "Notifications refusees : Reglages > Notifications > Field pour les activer.";
          ok.parentNode && ok.parentNode.removeChild(ok);
        }
      }).catch(function () { ok.disabled = false; });
    });
    bar.appendChild(ok);

    var close = document.createElement("button");
    close.type = "button";
    close.className = "field-install-hint-close";
    close.setAttribute("aria-label", "Ne plus afficher ce conseil");
    close.title = "Ne plus afficher";
    close.appendChild(mkIcon("close"));
    close.addEventListener("click", function () {
      try { window.localStorage.setItem(PUSH_KEY, "1"); } catch (e) { /* ignore */ }
      if (bar.parentNode) bar.parentNode.removeChild(bar);
    });
    bar.appendChild(close);

    document.body.appendChild(bar);
  }

  function init() {
    var standalone = isStandalone();
    try {
      document.documentElement.classList.toggle("field-standalone", standalone);
    } catch (e) { /* ignore */ }
    if (!isIOS()) return;
    if (standalone) {
      if ("Notification" in window && "PushManager" in window
          && window.Notification.permission === "default" && !pushWasDismissed()) {
        showPushPrompt();
      }
      return;
    }
    if (wasDismissed()) return;
    show();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
