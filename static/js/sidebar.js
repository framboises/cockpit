/* sidebar.js -- comportement de la barre laterale, partage par toutes les pages.
 *
 * POURQUOI CE FICHIER
 *
 * Le bouton de repli avait sept implementations : main.js, alertes_admin.js,
 * anpr.js, et du script en ligne dans cameras.html, live_controle.html,
 * scan_report.html, wiki.html, wiki_admin.html. Elles ne faisaient pas la meme
 * chose -- certaines oubliaient la persistance, d'autres le mode mobile. Le
 * markup vit maintenant dans templates/_sidebar.html, le comportement ici.
 *
 * A charger sur toute page qui inclut _sidebar.html. Ne rien rebrancher
 * ailleurs : deux ecouteurs sur #sidebarToggle s'annulent l'un l'autre.
 */
(function () {
  "use strict";

  var SEUIL_MOBILE = "(max-width: 820px)";
  var CLE_REPLI = "sidebar-collapsed";
  var CLE_EVENEMENT = "cockpit_event";
  var CLE_ANNEE = "cockpit_year";

  function message(type, texte) {
    if (typeof window.showToast === "function") { window.showToast(type, texte); return; }
    // Une page sans toast.js ne doit pas avaler l'information en silence :
    // l'utilisateur a clique, il attend une reponse.
    try { console.warn("[sidebar]", texte); } catch (e) {}
  }

  // ------------------------------------------------------------------
  // Evenement et annee courants
  //
  // main.js les pose sur window et les persiste dans localStorage, mais il
  // n'est charge que sur cinq des onze pages a sidebar. Ailleurs on relit
  // simplement le dernier choix -- c'est ce qui rend Portes / Parkings /
  // Statistiques et l'Assistant IA utilisables partout.
  // ------------------------------------------------------------------

  function contexte() {
    var evenement = window.selectedEvent;
    var annee = window.selectedYear;
    try {
      if (!evenement) evenement = localStorage.getItem(CLE_EVENEMENT) || "";
      if (!annee) annee = localStorage.getItem(CLE_ANNEE) || "";
    } catch (e) {}
    return { evenement: evenement || "", annee: annee ? String(annee) : "" };
  }

  function publierContexte() {
    var c = contexte();
    // On ne remplace jamais une valeur deja posee : sur les pages qui ont le
    // selecteur, main.js fait autorite.
    if (!window.selectedEvent && c.evenement) window.selectedEvent = c.evenement;
    if (!window.selectedYear && c.annee) window.selectedYear = c.annee;
  }

  // memeOnglet : page operateur (Portes) ouverte a la place de la page
  // courante ; les autres restent dans un nouvel onglet
  function ouvrirPage(chemin, memeOnglet) {
    var c = contexte();
    if (!c.evenement || !c.annee) {
      message("error", "Veuillez selectionner un evenement et une annee");
      return;
    }
    var url = chemin + "?event=" + encodeURIComponent(c.evenement) +
              "&year=" + encodeURIComponent(c.annee);
    if (memeOnglet) window.location.href = url;
    else window.open(url, "_blank");
  }

  // ------------------------------------------------------------------
  // Repli / depli
  // ------------------------------------------------------------------

  function brancherRepli() {
    var barre = document.getElementById("sidebar");
    if (!barre) return;

    // Etat par defaut : repliee. Un menu deplie mange la largeur utile sur
    // les postes du PC, ou l'ecran sert d'abord a la timeline et a la carte.
    var memorise = null;
    try { memorise = localStorage.getItem(CLE_REPLI); } catch (e) {}
    if (memorise === null || memorise === "true") barre.classList.add("collapsed");

    var mobile = window.matchMedia(SEUIL_MOBILE);

    function basculer() {
      if (mobile.matches) {
        var ouvert = barre.classList.toggle("mobile-open");
        document.body.classList.toggle("sidebar-open", ouvert);
        return;
      }
      barre.classList.toggle("collapsed");
      try {
        localStorage.setItem(CLE_REPLI, barre.classList.contains("collapsed"));
      } catch (e) {}
    }

    function fermerMobile() {
      barre.classList.remove("mobile-open");
      document.body.classList.remove("sidebar-open");
    }

    ["sidebarToggle", "hamburger-button", "header-burger-btn"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("click", basculer);
    });

    document.addEventListener("click", function (e) {
      if (!mobile.matches) return;
      if (!barre.classList.contains("mobile-open")) return;
      // Un clic dans la barre la laisse ouverte, sauf sur un lien de
      // navigation : la page va changer, la garder ouverte n'a pas de sens.
      if (e.target.closest("#sidebar") && !e.target.closest(".sidebar-nav .nav-btn:not(.nav-group-toggle)")) return;
      if (e.target.closest("#sidebarToggle, #header-burger-btn, #hamburger-button")) return;
      fermerMobile();
    });

    mobile.addEventListener("change", function (ev) {
      if (!ev.matches) fermerMobile();
    });
  }

  // ------------------------------------------------------------------
  // Groupes de menus (_sidebar.html nav_group)
  //
  // Menu deplie (PC) et telephone : le groupe s'ouvre / se replie sur place,
  // etat memorise par groupe. Menu replie (PC, icones seules) : le sous-menu
  // s'ouvre en volet a droite des icones (position fixe : la nav defile et
  // couperait un volet absolu), ferme par un clic ailleurs ou Echap. Le
  // groupe de la page courante est toujours ouvert.
  // ------------------------------------------------------------------

  var CLE_GROUPE = "sidebar-group-";

  function brancherGroupes() {
    var barre = document.getElementById("sidebar");
    if (!barre) return;
    var groupes = Array.prototype.slice.call(barre.querySelectorAll(".nav-group"));
    if (!groupes.length) return;
    var mobile = window.matchMedia(SEUIL_MOBILE);

    function enVolet() {
      return !mobile.matches && barre.classList.contains("collapsed");
    }
    function memoire(g, v) {
      try {
        if (v === undefined) return localStorage.getItem(CLE_GROUPE + g.getAttribute("data-group"));
        localStorage.setItem(CLE_GROUPE + g.getAttribute("data-group"), v ? "1" : "0");
      } catch (e) {}
      return null;
    }
    function bouton(g) { return g.querySelector(".nav-group-toggle"); }

    function poser(g, ouvert) {
      g.classList.toggle("is-open", ouvert);
      bouton(g).setAttribute("aria-expanded", ouvert ? "true" : "false");
    }
    function fermerVolets() {
      groupes.forEach(function (g) {
        if (!g.classList.contains("is-flyout")) return;
        g.classList.remove("is-flyout");
        bouton(g).setAttribute("aria-expanded", g.classList.contains("is-open") ? "true" : "false");
        var liste = g.querySelector(".nav-group-items");
        liste.style.top = "";
        liste.style.left = "";
      });
    }
    function ouvrirVolet(g) {
      var dejaOuvert = g.classList.contains("is-flyout");
      fermerVolets();
      if (dejaOuvert) return;
      g.classList.add("is-flyout");
      bouton(g).setAttribute("aria-expanded", "true");
      var liste = g.querySelector(".nav-group-items");
      var r = bouton(g).getBoundingClientRect();
      liste.style.left = (barre.getBoundingClientRect().right + 6) + "px";
      liste.style.top = r.top + "px";
      // Reste dans l'ecran si le groupe est bas dans la barre
      var h = liste.getBoundingClientRect().height;
      var max = window.innerHeight - h - 8;
      if (r.top > max) liste.style.top = Math.max(8, max) + "px";
    }

    groupes.forEach(function (g) {
      var m = memoire(g);
      poser(g, g.classList.contains("has-active") || m === "1");
      bouton(g).addEventListener("click", function (e) {
        e.stopPropagation();
        if (enVolet()) { ouvrirVolet(g); return; }
        var ouvert = !g.classList.contains("is-open");
        poser(g, ouvert);
        memoire(g, ouvert);
      });
    });

    document.addEventListener("click", function (e) {
      if (!e.target.closest(".nav-group.is-flyout .nav-group-items")) fermerVolets();
      else if (e.target.closest(".nav-btn")) fermerVolets();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") fermerVolets();
    });
    window.addEventListener("resize", fermerVolets);
    // Repli / depli de la barre ou passage telephone : plus de volet
    new MutationObserver(fermerVolets).observe(barre, { attributes: true, attributeFilter: ["class"] });
    var nav = barre.querySelector(".sidebar-nav");
    if (nav) nav.addEventListener("scroll", fermerVolets, { passive: true });
  }

  // ------------------------------------------------------------------

  function brancherRaccourcis() {
    var cibles = {
      "doors-page-button": "/doors",
      "parkings-page-button": "/terrains",
      "stats-page-button": "/general_stat"
    };
    Object.keys(cibles).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("click", function () { ouvrirPage(cibles[id], id === "doors-page-button"); });
    });
  }

  // Deconnexion volontaire = fin du poste operateur s'il y en a un ouvert
  // (docs/claude/operateurs.md). POST protege CSRF : la route GET
  // /logout_redirect reste sans effet de bord.
  function brancherDeconnexion() {
    var lien = document.querySelector('a[href="/logout_redirect"]');
    if (!lien) return;
    lien.addEventListener("click", function (e) {
      e.preventDefault();
      var partir = function () { window.location.href = "/logout_redirect"; };
      var meta = document.querySelector('meta[name="csrf-token"]');
      fetch("/api/operator/post", { credentials: "same-origin", cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d || !d.post) { partir(); return; }
          // Sans conteneur de toasts, showConfirmToast repond "non" : on ne
          // doit pas bloquer la deconnexion pour autant
          var ok = typeof window.showConfirmToast === "function" && document.getElementById("toast-container")
            ? window.showConfirmToast("Vous deconnecter termine votre poste (fiche \"Fin de poste\" dans la main courante). Continuer ?",
                                      { type: "warning", okLabel: "Me deconnecter", cancelLabel: "Annuler" })
            : Promise.resolve(true);
          return ok.then(function (go) {
            if (!go) return;
            return fetch("/api/operator/post/end", {
              method: "POST", credentials: "same-origin",
              headers: { "Content-Type": "application/json", "X-CSRFToken": meta ? meta.getAttribute("content") : "" },
              body: JSON.stringify({ reason: "logout" })
            }).catch(function () {}).then(partir);
          });
        })
        .catch(partir);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    publierContexte();
    brancherRepli();
    brancherGroupes();
    brancherRaccourcis();
    brancherDeconnexion();
  });
})();
