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

  function ouvrirPage(chemin) {
    var c = contexte();
    if (!c.evenement || !c.annee) {
      message("error", "Veuillez selectionner un evenement et une annee");
      return;
    }
    window.open(chemin + "?event=" + encodeURIComponent(c.evenement) +
                "&year=" + encodeURIComponent(c.annee), "_blank");
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
      if (e.target.closest("#sidebar") && !e.target.closest(".sidebar-nav .nav-btn")) return;
      if (e.target.closest("#sidebarToggle, #header-burger-btn, #hamburger-button")) return;
      fermerMobile();
    });

    mobile.addEventListener("change", function (ev) {
      if (!ev.matches) fermerMobile();
    });
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
      if (el) el.addEventListener("click", function () { ouvrirPage(cibles[id]); });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    publierContexte();
    brancherRepli();
    brancherRaccourcis();
  });
})();
